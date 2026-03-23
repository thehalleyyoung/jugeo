"""Purpose-conditioned regime search for JuGeo ideation.

This module implements the "semantic futures under explicit purpose" framing
from ``theory2.tex``. A regime is a mathematical or semantic mode of
exploration with its own admissible moves, novelty criteria, bridge patterns,
and trust discipline. The code below treats regimes as typed search objects
instead of loose labels so JuGeo can rank, switch, bootstrap, serialize, and
diagnose exploration strategies in a replayable way.

The design is intentionally rich and slightly redundant: the same facts can be
observed through catalog lookup, selector advice, policy evaluation, and
diagnostics. That duplication is valuable because regime search is part control
logic and part copilot-facing explanation surface.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

try:
    from jugeo.geometry.site import Site, Coordinate, CoordinateKind, SiteDiagnostics
except ImportError:  # pragma: no cover
    Site = None  # type: ignore[assignment,misc]
    Coordinate = None  # type: ignore[assignment,misc]
    CoordinateKind = None  # type: ignore[assignment,misc]
    SiteDiagnostics = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.budgets import BudgetLedger, BudgetDimension
except ImportError:  # pragma: no cover
    BudgetLedger = None  # type: ignore[assignment,misc]
    BudgetDimension = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import CertificateBuilder, Certificate, emit_certificate
except ImportError:  # pragma: no cover
    CertificateBuilder = None  # type: ignore[assignment,misc]
    Certificate = None  # type: ignore[assignment,misc]
    emit_certificate = None  # type: ignore[assignment,misc]


def _normalize_text(value: str) -> str:
    """Return a normalized, whitespace-stable textual form."""

    return " ".join(value.strip().split())


def _tokenize(value: str) -> tuple[str, ...]:
    """Split text into normalized search tokens while preserving order."""

    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", value.lower())
    return tuple(dict.fromkeys(tokens))


def _normalize_sequence(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize a string sequence into a deterministic, duplicate-free tuple."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(str(value))
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return tuple(normalized)


def _slugify(value: str) -> str:
    """Create a lowercase identifier suitable for regime ids."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "regime"


def _clamp(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp a floating-point score to a closed interval."""

    return max(lower, min(upper, float(value)))


def _mean(values: Iterable[float]) -> float:
    """Compute a safe arithmetic mean that returns ``0.0`` on empty input."""

    items = [float(value) for value in values]
    if not items:
        return 0.0
    return sum(items) / len(items)


def _overlap_score(left: str, right: str) -> float:
    """Measure token overlap between two textual descriptions."""

    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    """Return a dictionary view of a counter with deterministic key ordering."""

    return {key: counter[key] for key in sorted(counter)}


class RegimeKind(str, Enum):
    """Legacy regime labels retained for existing JuGeo callers."""

    COVER_REFINEMENT = "cover-refinement"
    THEORY_EXTENSION = "theory-extension"
    PACK_CHANGE = "pack-change"
    INVARIANT_STRENGTHENING = "invariant-strengthening"


@dataclass(frozen=True, slots=True)
class RegimeProposal:
    """A compact legacy proposal object used by small ideation tests.

    The richer :class:`IdeationRegime` surface is the preferred representation
    for new code, but JuGeo still exposes this small compatibility wrapper so
    existing tests and federation logic remain stable while the regime system
    grows around them.
    """

    kind: RegimeKind
    rationale: str
    expected_obstruction_drop: int

    def to_transition(self, target_regime: str) -> "RegimeTransition":
        """Translate the proposal into a transition-shaped compatibility value."""

        return RegimeTransition(
            from_regime=self.kind.value,
            to_regime=target_regime,
            rationale=_normalize_text(self.rationale),
            cost=max(0.05, 1.0 - min(self.expected_obstruction_drop / 10.0, 0.9)),
            trust_effect=0.0,
            expected_novelty=_clamp(self.expected_obstruction_drop / 10.0),
        )

    def score(self) -> float:
        """Expose a normalized proposal score for compatibility layers."""

        return max(0.0, float(self.expected_obstruction_drop))

    def to_dict(self) -> dict[str, Any]:
        """Lower the proposal into a JSON-safe dictionary."""

        return {
            "kind": self.kind.value,
            "rationale": _normalize_text(self.rationale),
            "expected_obstruction_drop": int(self.expected_obstruction_drop),
        }


def choose_regime(proposals: tuple[RegimeProposal, ...]) -> RegimeProposal | None:
    """Choose the strongest legacy regime proposal by obstruction drop."""

    if not proposals:
        return None
    return max(proposals, key=lambda proposal: proposal.expected_obstruction_drop)


@dataclass(frozen=True, init=False)
class IdeationRegime:
    """A mathematical or semantic mode of exploration in JuGeo ideation.

    The fields mirror the theory text closely: a regime has a purpose, a set of
    admissible moves, a novelty metric, bridge patterns into neighboring
    semantics, and an explicit trust policy. The object is immutable so regime
    evolution happens through transition objects and bootstrap procedures rather
    than silent mutation.
    """

    regime_id: str
    name: str
    purpose: str
    admissible_moves: tuple[str, ...]
    novelty_metric: str
    trust_policy: str
    bridge_patterns: tuple[str, ...]
    active_constraints: tuple[str, ...]

    def __init__(
        self,
        regime_id: str = "",
        name: str = "",
        purpose: str = "",
        admissible_moves: Iterable[str] = (),
        novelty_metric: str = "semantic-distance",
        trust_policy: str = "default",
        bridge_patterns: Iterable[str] = (),
        active_constraints: Iterable[str] = (),
        *,
        description: str = "",
    ) -> None:
        seed_text = description or purpose or name or regime_id or "regime"
        object.__setattr__(self, "regime_id", _slugify(regime_id or seed_text))
        object.__setattr__(self, "name", _normalize_text(name or description or seed_text))
        object.__setattr__(self, "purpose", _normalize_text(purpose or description or seed_text))
        object.__setattr__(self, "admissible_moves", _normalize_sequence(admissible_moves))
        object.__setattr__(self, "novelty_metric", _normalize_text(novelty_metric).lower())
        object.__setattr__(self, "trust_policy", _normalize_text(trust_policy).lower())
        object.__setattr__(self, "bridge_patterns", _normalize_sequence(bridge_patterns))
        object.__setattr__(self, "active_constraints", _normalize_sequence(active_constraints))

    def admits_move(self, move: str) -> bool:
        """Return whether a move is admissible under this regime.

        Exact move names are accepted, but JuGeo also allows a move that is a
        semantic refinement of a listed move. A refinement is recognized when a
        listed move appears as a tokenized substring and no active constraint is
        explicitly contradicted inside the candidate move text.
        """

        candidate = _normalize_text(move).lower()
        if not candidate:
            return False
        if candidate in {item.lower() for item in self.admissible_moves}:
            return True
        for listed_move in self.admissible_moves:
            listed = listed_move.lower()
            if listed in candidate or candidate in listed:
                if any(
                    constraint.lower().startswith("forbid:")
                    and constraint.split(":", 1)[1].strip().lower() in candidate
                    for constraint in self.active_constraints
                ):
                    return False
                return True
        return False

    def novelty_score(self, candidate: str, *, prior_art: Iterable[str] = ()) -> float:
        """Score the novelty of a candidate move or theorem fragment.

        The novelty metric name influences how strongly bridge patterns and prior
        art collisions are weighted. This keeps the logic real while remaining
        lightweight enough for unit-scale orchestration code.
        """

        normalized = _normalize_text(candidate)
        if not normalized:
            return 0.0
        candidate_tokens = set(_tokenize(normalized))
        prior_tokens = [set(_tokenize(item)) for item in prior_art if _normalize_text(item)]
        prior_overlap = max((len(candidate_tokens & tokens) / max(1, len(candidate_tokens | tokens)) for tokens in prior_tokens), default=0.0)
        bridge_bonus = max((_overlap_score(pattern, normalized) for pattern in self.bridge_patterns), default=0.0)
        move_bonus = max((_overlap_score(move, normalized) for move in self.admissible_moves), default=0.0)
        metric_weight = 1.15 if "semantic" in self.novelty_metric else 1.0
        metric_weight += 0.15 if "bridge" in self.novelty_metric else 0.0
        metric_weight += 0.10 if "proof" in self.novelty_metric else 0.0
        raw_score = (0.55 + 0.45 * bridge_bonus + 0.25 * move_bonus - 0.60 * prior_overlap) * metric_weight
        return _clamp(raw_score)

    def trust_score(self, evidence: Mapping[str, float] | None = None) -> float:
        """Estimate trust readiness using the declared trust discipline."""

        evidence_map = dict(evidence or {})
        replay = float(evidence_map.get("replay", 0.0))
        proof = float(evidence_map.get("proof", 0.0))
        transport = float(evidence_map.get("transport", 0.0))
        sandbox = float(evidence_map.get("sandbox", 0.0))
        base = 0.25 * replay + 0.35 * proof + 0.25 * transport + 0.15 * sandbox
        if "strict" in self.trust_policy:
            base *= 0.95
        if "provisional" in self.trust_policy:
            base *= 0.80
        if "sandbox" in self.trust_policy:
            base = min(base, 0.70)
        if "certificate" in self.trust_policy:
            base += 0.10 * proof
        if any(constraint.lower() == "sandbox-only" for constraint in self.active_constraints):
            base = min(base, 0.65)
        return _clamp(base)

    def compatible_bridges(self, other: "IdeationRegime") -> tuple[str, ...]:
        """Return bridge patterns that plausibly connect this regime to another."""

        other_patterns = {pattern.lower(): pattern for pattern in other.bridge_patterns}
        shared = [
            pattern
            for pattern in self.bridge_patterns
            if pattern.lower() in other_patterns or _overlap_score(pattern, other.purpose) >= 0.25
        ]
        if shared:
            return _normalize_sequence(shared)
        purpose_tokens = set(_tokenize(self.purpose)) & set(_tokenize(other.purpose))
        derived = tuple(f"transport::{token}" for token in sorted(purpose_tokens))
        return _normalize_sequence(derived)

    def activate_constraint(self, constraint: str) -> "IdeationRegime":
        """Return a new regime with an additional active constraint."""

        return IdeationRegime(
            regime_id=self.regime_id,
            name=self.name,
            purpose=self.purpose,
            admissible_moves=self.admissible_moves,
            novelty_metric=self.novelty_metric,
            trust_policy=self.trust_policy,
            bridge_patterns=self.bridge_patterns,
            active_constraints=self.active_constraints + (constraint,),
        )

    def deactivate_constraint(self, constraint: str) -> "IdeationRegime":
        """Return a new regime without the given active constraint."""

        target = _normalize_text(constraint).casefold()
        return IdeationRegime(
            regime_id=self.regime_id,
            name=self.name,
            purpose=self.purpose,
            admissible_moves=self.admissible_moves,
            novelty_metric=self.novelty_metric,
            trust_policy=self.trust_policy,
            bridge_patterns=self.bridge_patterns,
            active_constraints=tuple(
                item for item in self.active_constraints if item.casefold() != target
            ),
        )

    def aligns_with_goal(self, goal: str) -> float:
        """Measure how strongly the regime purpose and moves align with a goal."""

        move_overlap = max((_overlap_score(goal, move) for move in self.admissible_moves), default=0.0)
        bridge_overlap = max((_overlap_score(goal, pattern) for pattern in self.bridge_patterns), default=0.0)
        purpose_overlap = _overlap_score(self.purpose, goal)
        constraint_penalty = 0.08 * len(self.active_constraints)
        return _clamp(0.55 * purpose_overlap + 0.25 * move_overlap + 0.20 * bridge_overlap - constraint_penalty)

    def to_dict(self) -> dict[str, Any]:
        """Convert the regime into a JSON-safe dictionary."""

        return {
            "regime_id": self.regime_id,
            "name": self.name,
            "purpose": self.purpose,
            "admissible_moves": list(self.admissible_moves),
            "novelty_metric": self.novelty_metric,
            "trust_policy": self.trust_policy,
            "bridge_patterns": list(self.bridge_patterns),
            "active_constraints": list(self.active_constraints),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IdeationRegime":
        """Build a regime from serialized data."""

        return cls(
            regime_id=str(payload["regime_id"]),
            name=str(payload["name"]),
            purpose=str(payload["purpose"]),
            admissible_moves=tuple(str(item) for item in payload.get("admissible_moves", ())),
            novelty_metric=str(payload["novelty_metric"]),
            trust_policy=str(payload["trust_policy"]),
            bridge_patterns=tuple(str(item) for item in payload.get("bridge_patterns", ())),
            active_constraints=tuple(str(item) for item in payload.get("active_constraints", ())),
        )

    def copilot_hint(self, goal: str) -> str:
        """Return a concise copilot-facing explanation for using the regime."""

        alignment = self.aligns_with_goal(goal)
        moves = ", ".join(self.admissible_moves[:3]) or "no moves registered"
        bridges = ", ".join(self.bridge_patterns[:2]) or "no bridges yet"
        return (
            f"copilot regime hint: '{self.name}' aligns {alignment:.2f} with goal "
            f"'{_normalize_text(goal)}'; try moves [{moves}] and bridge via [{bridges}]."
        )


@dataclass(frozen=True, slots=True)
class RegimeTransition:
    """A directed shift from one regime to another with explicit trade-offs."""

    from_regime: str
    to_regime: str
    rationale: str
    cost: float
    trust_effect: float
    expected_novelty: float

    def __post_init__(self) -> None:
        """Normalize identifiers and clamp scalar values into stable ranges."""

        object.__setattr__(self, "from_regime", _slugify(self.from_regime))
        object.__setattr__(self, "to_regime", _slugify(self.to_regime))
        object.__setattr__(self, "rationale", _normalize_text(self.rationale))
        object.__setattr__(self, "cost", max(0.0, float(self.cost)))
        object.__setattr__(self, "trust_effect", _clamp(self.trust_effect, lower=-1.0, upper=1.0))
        object.__setattr__(self, "expected_novelty", _clamp(self.expected_novelty))

    def net_value(self) -> float:
        """Combine novelty gain, trust shift, and cost into one transition score."""

        return self.expected_novelty + 0.35 * self.trust_effect - 0.50 * self.cost

    def risk_level(self) -> str:
        """Classify the transition as low, medium, or high risk."""

        risk_score = self.cost - 0.20 * self.trust_effect + (0.15 if self.expected_novelty > 0.8 else 0.0)
        if risk_score < 0.25:
            return "low"
        if risk_score < 0.60:
            return "medium"
        return "high"

    def describe_shift(self) -> str:
        """Render a human-readable explanation of the shift."""

        return (
            f"Transition {self.from_regime} -> {self.to_regime}: {self.rationale} "
            f"(cost={self.cost:.2f}, trust_effect={self.trust_effect:.2f}, "
            f"expected_novelty={self.expected_novelty:.2f}, risk={self.risk_level()})."
        )

    def apply_to_score(self, base_score: float) -> float:
        """Apply the transition's net effect to a base orchestration score."""

        return _clamp(float(base_score) + self.net_value(), lower=0.0, upper=2.0)

    def inverted(self) -> "RegimeTransition":
        """Return the opposite transition with mirrored qualitative effects."""

        return RegimeTransition(
            from_regime=self.to_regime,
            to_regime=self.from_regime,
            rationale=f"Reverse of: {self.rationale}",
            cost=self.cost,
            trust_effect=-self.trust_effect,
            expected_novelty=max(0.0, self.expected_novelty * 0.75),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the transition into a JSON-safe payload."""

        return {
            "from_regime": self.from_regime,
            "to_regime": self.to_regime,
            "rationale": self.rationale,
            "cost": self.cost,
            "trust_effect": self.trust_effect,
            "expected_novelty": self.expected_novelty,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegimeTransition":
        """Deserialize a transition payload."""

        return cls(
            from_regime=str(payload["from_regime"]),
            to_regime=str(payload["to_regime"]),
            rationale=str(payload["rationale"]),
            cost=float(payload["cost"]),
            trust_effect=float(payload["trust_effect"]),
            expected_novelty=float(payload["expected_novelty"]),
        )


@dataclass(frozen=True, slots=True)
class RegimeEvaluation:
    """A structured scorecard returned by :class:`RegimeEvaluator`."""

    regime_id: str
    progress: float
    novelty: float
    stability: float
    trust: float
    stalled: bool
    recommendation: str

    def composite(self) -> float:
        """Compute a composite score for ranking candidate regimes."""

        stall_penalty = 0.20 if self.stalled else 0.0
        return _clamp(
            0.35 * self.progress + 0.30 * self.novelty + 0.20 * self.stability + 0.15 * self.trust - stall_penalty,
        )

    def verdict(self) -> str:
        """Summarize the evaluation in a compact operational label."""

        if self.stalled:
            return "stalled"
        score = self.composite()
        if score >= 0.75:
            return "strong"
        if score >= 0.50:
            return "viable"
        return "weak"

    def to_dict(self) -> dict[str, Any]:
        """Convert the evaluation into a JSON-safe dictionary."""

        return {
            "regime_id": self.regime_id,
            "progress": self.progress,
            "novelty": self.novelty,
            "stability": self.stability,
            "trust": self.trust,
            "stalled": self.stalled,
            "recommendation": self.recommendation,
            "composite": self.composite(),
            "verdict": self.verdict(),
        }


@dataclass(frozen=True, slots=True)
class RegimeHistoryEntry:
    """A timestamped record of regime use and transition outcome."""

    transition: RegimeTransition
    success: bool
    novelty_realized: float
    started_at: datetime
    ended_at: datetime
    notes: str
    failure_mode: str | None = None

    def duration_seconds(self) -> float:
        """Return the active duration of the transition in seconds."""

        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    def touches(self, regime_id: str) -> bool:
        """Return whether the entry involves the given regime id."""

        slug = _slugify(regime_id)
        return self.transition.from_regime == slug or self.transition.to_regime == slug

    def outcome_label(self) -> str:
        """Provide a stable label for analytics and diagnostics."""

        if self.success:
            return "success"
        if self.failure_mode:
            return f"failure:{self.failure_mode}"
        return "failure:unknown"

    def to_dict(self) -> dict[str, Any]:
        """Lower the entry into a JSON-safe payload."""

        return {
            "transition": self.transition.to_dict(),
            "success": self.success,
            "novelty_realized": _clamp(self.novelty_realized),
            "started_at": self.started_at.astimezone(UTC).isoformat(),
            "ended_at": self.ended_at.astimezone(UTC).isoformat(),
            "notes": _normalize_text(self.notes),
            "failure_mode": self.failure_mode,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegimeHistoryEntry":
        """Deserialize a history entry from a dictionary."""

        return cls(
            transition=RegimeTransition.from_dict(payload["transition"]),
            success=bool(payload["success"]),
            novelty_realized=float(payload["novelty_realized"]),
            started_at=datetime.fromisoformat(str(payload["started_at"])),
            ended_at=datetime.fromisoformat(str(payload["ended_at"])),
            notes=str(payload.get("notes", "")),
            failure_mode=payload.get("failure_mode"),
        )


@dataclass(frozen=True, slots=True)
class RegimePolicy:
    """Operational guardrails for admissibility, novelty, and escalation.

    The policy object is where abstract theory-space discipline becomes
    executable. It checks whether a move may be attempted, how much novelty is
    required, and when human or higher-trust review should be triggered.
    """

    guardrails: tuple[str, ...]
    admissibility_checks: Mapping[str, tuple[str, ...]]
    novelty_thresholds: Mapping[str, float]
    escalation_rules: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        """Normalize all policy structures into deterministic forms."""

        normalized_checks = {
            _normalize_text(key).lower(): _normalize_sequence(values)
            for key, values in self.admissibility_checks.items()
        }
        normalized_thresholds = {
            _normalize_text(key).lower(): _clamp(float(value))
            for key, value in self.novelty_thresholds.items()
        }
        normalized_rules = {
            _normalize_text(key).lower(): _normalize_sequence(values)
            for key, values in self.escalation_rules.items()
        }
        object.__setattr__(self, "guardrails", _normalize_sequence(self.guardrails))
        object.__setattr__(self, "admissibility_checks", normalized_checks)
        object.__setattr__(self, "novelty_thresholds", normalized_thresholds)
        object.__setattr__(self, "escalation_rules", normalized_rules)

    def guardrail_violations(
        self,
        regime: IdeationRegime,
        move: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """Return guardrail violations for a candidate move."""

        move_text = _normalize_text(move).lower()
        context_map = dict(context or {})
        violations: list[str] = []
        for guardrail in self.guardrails:
            label = guardrail.lower()
            if label == "require-bridge" and not any(pattern.lower() in move_text for pattern in regime.bridge_patterns):
                violations.append("missing-bridge")
            elif label == "no-ornamental-math" and _overlap_score(move_text, regime.purpose) < 0.10:
                violations.append("ornamental-move")
            elif label == "respect-constraints" and any(
                constraint.lower().replace("forbid:", "") in move_text
                for constraint in regime.active_constraints
                if constraint.lower().startswith("forbid:")
            ):
                violations.append("constraint-breach")
            elif label == "sandbox-only" and context_map.get("scope", "sandbox") != "sandbox":
                violations.append("outside-sandbox")
        return _normalize_sequence(violations)

    def check_admissibility(
        self,
        regime: IdeationRegime,
        move: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """Return whether a move passes regime and policy admissibility checks."""

        move_text = _normalize_text(move).lower()
        if not regime.admits_move(move_text):
            return False
        checks = self.admissibility_checks
        blocked_tokens = set(checks.get("blocked_tokens", ()))
        required_tokens = set(checks.get("required_tokens", ()))
        if blocked_tokens & set(_tokenize(move_text)):
            return False
        if required_tokens and not required_tokens & set(_tokenize(move_text)):
            return False
        if self.guardrail_violations(regime, move_text, context=context):
            return False
        return True

    def required_novelty(self, regime: IdeationRegime) -> float:
        """Look up the novelty floor for a regime or novelty metric."""

        thresholds = self.novelty_thresholds
        if regime.regime_id in thresholds:
            return thresholds[regime.regime_id]
        if regime.novelty_metric in thresholds:
            return thresholds[regime.novelty_metric]
        return thresholds.get("default", 0.25)

    def needs_escalation(
        self,
        regime: IdeationRegime,
        *,
        trust_score: float,
        novelty_score: float,
        stability_score: float,
    ) -> tuple[bool, str | None]:
        """Determine whether regime execution should be escalated."""

        required = self.escalation_rules
        reasons: list[str] = []
        if "low-trust" in required and trust_score < 0.40:
            reasons.append("low-trust")
        if "high-novelty" in required and novelty_score > 0.85 and trust_score < 0.70:
            reasons.append("high-novelty")
        if "low-stability" in required and stability_score < 0.35:
            reasons.append("low-stability")
        if "provisional-policy" in required and "provisional" in regime.trust_policy:
            reasons.append("provisional-policy")
        if not reasons:
            return False, None
        return True, ", ".join(sorted(reasons))

    def enforce(
        self,
        regime: IdeationRegime,
        move: str,
        *,
        novelty_score: float | None = None,
        trust_score: float | None = None,
        stability_score: float | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        """Apply admissibility, novelty, and escalation checks to a move."""

        reasons: list[str] = []
        admissible = self.check_admissibility(regime, move, context=context)
        if not admissible:
            reasons.append("inadmissible")
        if novelty_score is not None and novelty_score < self.required_novelty(regime):
            reasons.append("novelty-below-threshold")
        if trust_score is not None and stability_score is not None:
            escalate, detail = self.needs_escalation(
                regime,
                trust_score=trust_score,
                novelty_score=novelty_score or 0.0,
                stability_score=stability_score,
            )
            if escalate and detail:
                reasons.append(f"escalate:{detail}")
        return not reasons, _normalize_sequence(reasons)

    def explain_policy(self, regime: IdeationRegime) -> str:
        """Render a concise explanation of the current policy application."""

        threshold = self.required_novelty(regime)
        checks = ", ".join(sorted(self.admissibility_checks)) or "none"
        guardrails = ", ".join(self.guardrails) or "none"
        escalations = ", ".join(sorted(self.escalation_rules)) or "none"
        return (
            f"Policy for {regime.regime_id}: novelty>={threshold:.2f}; "
            f"checks=[{checks}]; guardrails=[{guardrails}]; escalation=[{escalations}]."
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the policy into a JSON-safe payload."""

        return {
            "guardrails": list(self.guardrails),
            "admissibility_checks": {
                key: list(values) for key, values in sorted(self.admissibility_checks.items())
            },
            "novelty_thresholds": dict(sorted(self.novelty_thresholds.items())),
            "escalation_rules": {
                key: list(values) for key, values in sorted(self.escalation_rules.items())
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegimePolicy":
        """Deserialize a policy from a dictionary."""

        return cls(
            guardrails=tuple(str(item) for item in payload.get("guardrails", ())),
            admissibility_checks={
                str(key): tuple(str(item) for item in values)
                for key, values in dict(payload.get("admissibility_checks", {})).items()
            },
            novelty_thresholds={
                str(key): float(value)
                for key, value in dict(payload.get("novelty_thresholds", {})).items()
            },
            escalation_rules={
                str(key): tuple(str(item) for item in values)
                for key, values in dict(payload.get("escalation_rules", {})).items()
            },
        )

    def merge(self, other: "RegimePolicy") -> "RegimePolicy":
        """Combine two policies while preserving deterministic ordering."""

        merged_checks: dict[str, tuple[str, ...]] = dict(self.admissibility_checks)
        for key, values in other.admissibility_checks.items():
            merged_checks[key] = _normalize_sequence(merged_checks.get(key, ()) + tuple(values))
        merged_thresholds = dict(self.novelty_thresholds)
        merged_thresholds.update(other.novelty_thresholds)
        merged_escalation: dict[str, tuple[str, ...]] = dict(self.escalation_rules)
        for key, values in other.escalation_rules.items():
            merged_escalation[key] = _normalize_sequence(merged_escalation.get(key, ()) + tuple(values))
        return RegimePolicy(
            guardrails=_normalize_sequence(self.guardrails + other.guardrails),
            admissibility_checks=merged_checks,
            novelty_thresholds=merged_thresholds,
            escalation_rules=merged_escalation,
        )


DEFAULT_POLICY = RegimePolicy(
    guardrails=("require-bridge", "no-ornamental-math", "respect-constraints"),
    admissibility_checks={
        "blocked_tokens": ("ignore", "bypass", "unsafe"),
        "required_tokens": (),
    },
    novelty_thresholds={"default": 0.25, "semantic-distance": 0.35, "bridge-seeking": 0.30},
    escalation_rules={
        "low-trust": ("review",),
        "high-novelty": ("review", "archive-note"),
        "low-stability": ("replay",),
        "provisional-policy": ("sandbox",),
    },
)


class RegimeCatalog:
    """A registry of known regimes and their activation status."""

    def __init__(self, regimes: Iterable[IdeationRegime] = ()) -> None:
        """Create an empty catalog and register any provided regimes."""

        self._regimes: dict[str, IdeationRegime] = {}
        self._active: set[str] = set()
        for regime in regimes:
            self.register(regime)

    def register(
        self,
        regime: IdeationRegime,
        *,
        activate: bool = False,
        replace: bool = False,
    ) -> IdeationRegime:
        """Register a regime in the catalog."""

        if not replace and regime.regime_id in self._regimes:
            raise ValueError(f"regime already registered: {regime.regime_id}")
        self._regimes[regime.regime_id] = regime
        if activate:
            self._active.add(regime.regime_id)
        return regime

    def get(self, regime_id: str) -> IdeationRegime | None:
        """Look up a regime by id."""

        return self._regimes.get(_slugify(regime_id))

    def list_regimes(self, *, active_only: bool = False) -> tuple[IdeationRegime, ...]:
        """List registered regimes in deterministic identifier order."""

        regime_ids = sorted(self._active if active_only else self._regimes)
        return tuple(self._regimes[regime_id] for regime_id in regime_ids)

    def find_by_purpose(self, purpose: str, *, limit: int | None = None) -> tuple[IdeationRegime, ...]:
        """Find regimes ranked by their alignment to a purpose statement."""

        ranked = sorted(
            self._regimes.values(),
            key=lambda regime: (regime.aligns_with_goal(purpose), regime.name),
            reverse=True,
        )
        if limit is not None:
            ranked = ranked[: max(0, int(limit))]
        return tuple(ranked)

    def compatible_with(self, regime: IdeationRegime | str) -> tuple[IdeationRegime, ...]:
        """List regimes with meaningful move or bridge compatibility."""

        anchor = regime if isinstance(regime, IdeationRegime) else self.get(regime)
        if anchor is None:
            return ()
        compatibles: list[tuple[float, IdeationRegime]] = []
        for candidate in self._regimes.values():
            if candidate.regime_id == anchor.regime_id:
                continue
            bridge_score = len(anchor.compatible_bridges(candidate))
            purpose_score = _overlap_score(anchor.purpose, candidate.purpose)
            move_score = max(
                (_overlap_score(left, right) for left in anchor.admissible_moves for right in candidate.admissible_moves),
                default=0.0,
            )
            composite = 0.45 * purpose_score + 0.30 * move_score + 0.25 * min(1.0, bridge_score / 3.0)
            if composite >= 0.20:
                compatibles.append((composite, candidate))
        compatibles.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        return tuple(candidate for _, candidate in compatibles)

    def activate(self, regime_id: str) -> IdeationRegime:
        """Mark a registered regime as active."""

        regime = self.get(regime_id)
        if regime is None:
            raise KeyError(f"unknown regime: {regime_id}")
        self._active.add(regime.regime_id)
        return regime

    def deactivate(self, regime_id: str) -> IdeationRegime:
        """Mark a registered regime as inactive."""

        regime = self.get(regime_id)
        if regime is None:
            raise KeyError(f"unknown regime: {regime_id}")
        self._active.discard(regime.regime_id)
        return regime

    def is_active(self, regime_id: str) -> bool:
        """Return whether a regime is currently active."""

        return _slugify(regime_id) in self._active

    def active_regimes(self) -> tuple[IdeationRegime, ...]:
        """Return all active regimes."""

        return self.list_regimes(active_only=True)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the catalog into a JSON-safe dictionary."""

        return {
            "regimes": [regime.to_dict() for regime in self.list_regimes()],
            "active": sorted(self._active),
        }


class RegimeEvaluator:
    """Score regime progress, novelty, stability, and stall behavior."""

    def __init__(self, policy: RegimePolicy | None = None) -> None:
        """Create a regime evaluator with a policy surface."""

        self.policy = policy or DEFAULT_POLICY

    def evaluate(
        self,
        regime: IdeationRegime,
        *,
        goal: str,
        frontier: Sequence[str] = (),
        history: "RegimeHistory | None" = None,
        evidence: Mapping[str, float] | None = None,
        prior_art: Iterable[str] = (),
    ) -> RegimeEvaluation:
        """Evaluate a regime against an explicit goal and current frontier."""

        progress = self.score_progress(goal, frontier)
        novelty = self.score_novelty(regime, frontier, prior_art=prior_art)
        stability = self.score_stability(regime, frontier, history=history)
        trust = self.trust_signal(regime, evidence)
        stalled = self.detect_stall(frontier, history=history)
        ok, reasons = self.policy.enforce(
            regime,
            frontier[0] if frontier else (regime.admissible_moves[0] if regime.admissible_moves else regime.name),
            novelty_score=novelty,
            trust_score=trust,
            stability_score=stability,
            context={"scope": "sandbox" if "sandbox" in regime.trust_policy else "catalog"},
        )
        recommendation = "proceed" if ok and not stalled else "; ".join(reasons) or "stalled"
        return RegimeEvaluation(
            regime_id=regime.regime_id,
            progress=progress,
            novelty=novelty,
            stability=stability,
            trust=trust,
            stalled=stalled,
            recommendation=recommendation,
        )

    def score_progress(self, goal: str, frontier: Sequence[str]) -> float:
        """Measure how much the frontier already speaks to the goal."""

        if not frontier:
            return 0.0
        frontier_scores = [_overlap_score(goal, item) for item in frontier]
        diversity_bonus = min(0.20, len({token for item in frontier for token in _tokenize(item)}) / 40.0)
        return _clamp(_mean(frontier_scores) + diversity_bonus)

    def score_novelty(
        self,
        regime: IdeationRegime,
        frontier: Sequence[str],
        *,
        prior_art: Iterable[str] = (),
    ) -> float:
        """Measure novelty generated by the frontier under a given regime."""

        if not frontier:
            return self.policy.required_novelty(regime)
        scores = [regime.novelty_score(item, prior_art=prior_art) for item in frontier]
        bridge_bonus = min(0.20, len(regime.bridge_patterns) / 20.0)
        return _clamp(_mean(scores) + bridge_bonus)

    def score_stability(
        self,
        regime: IdeationRegime,
        frontier: Sequence[str],
        *,
        history: "RegimeHistory | None" = None,
    ) -> float:
        """Measure stability from move coherence and historical success."""

        move_fit = _mean(1.0 if regime.admits_move(item) else 0.0 for item in frontier) if frontier else 0.50
        history_bonus = history.success_rate(regime.regime_id) if history else 0.50
        constraint_penalty = min(0.25, 0.05 * len(regime.active_constraints))
        return _clamp(0.55 * move_fit + 0.45 * history_bonus - constraint_penalty)

    def detect_stall(
        self,
        frontier: Sequence[str],
        *,
        history: "RegimeHistory | None" = None,
    ) -> bool:
        """Detect frontier stall by repetition and recent failures."""

        if not frontier:
            return True
        normalized = [_normalize_text(item).lower() for item in frontier if _normalize_text(item)]
        repeated = len(normalized) != len(set(normalized))
        if history is None:
            return repeated
        recent = history.entries[-3:]
        repeated_failures = sum(1 for entry in recent if not entry.success) >= 2
        return repeated or repeated_failures

    def trust_signal(
        self,
        regime: IdeationRegime,
        evidence: Mapping[str, float] | None = None,
    ) -> float:
        """Compute the trust signal for a regime and evidence bundle."""

        return regime.trust_score(evidence)


class RegimeSelector:
    """Choose and compare regimes for explicit ideation goals."""

    def __init__(
        self,
        catalog: RegimeCatalog,
        *,
        evaluator: RegimeEvaluator | None = None,
        policy: RegimePolicy | None = None,
        history: "RegimeHistory | None" = None,
    ) -> None:
        """Create a selector over a catalog of candidate regimes."""

        self.catalog = catalog
        self.policy = policy or DEFAULT_POLICY
        self.evaluator = evaluator or RegimeEvaluator(self.policy)
        self.history = history

    def select_for_goal(
        self,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
        active_only: bool = False,
    ) -> IdeationRegime | None:
        """Select the highest-ranked regime for a goal."""

        ranked = self.rank_candidates(goal, context=context, active_only=active_only)
        return ranked[0][0] if ranked else None

    def rank_candidates(
        self,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
        active_only: bool = False,
    ) -> list[tuple[IdeationRegime, float]]:
        """Rank candidate regimes for a goal using evaluation and alignment."""

        context_map = dict(context or {})
        frontier = tuple(str(item) for item in context_map.get("frontier", ()))
        evidence = context_map.get("evidence")
        prior_art = tuple(str(item) for item in context_map.get("prior_art", ()))
        candidates = self.catalog.list_regimes(active_only=active_only)
        scored: list[tuple[IdeationRegime, float]] = []
        for regime in candidates:
            evaluation = self.evaluator.evaluate(
                regime,
                goal=goal,
                frontier=frontier,
                history=self.history,
                evidence=evidence,
                prior_art=prior_art,
            )
            active_bonus = 0.05 if self.catalog.is_active(regime.regime_id) else 0.0
            alignment = regime.aligns_with_goal(goal)
            score = _clamp(0.60 * evaluation.composite() + 0.35 * alignment + active_bonus)
            scored.append((regime, score))
        scored.sort(key=lambda item: (item[1], item[0].name), reverse=True)
        return scored

    def compare_regimes(
        self,
        left: IdeationRegime | str,
        right: IdeationRegime | str,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> RegimeTransition:
        """Compare two regimes and describe the shift from left to right."""

        left_regime = left if isinstance(left, IdeationRegime) else self.catalog.get(left)
        right_regime = right if isinstance(right, IdeationRegime) else self.catalog.get(right)
        if left_regime is None or right_regime is None:
            raise KeyError("both regimes must exist for comparison")
        frontier = tuple(str(item) for item in dict(context or {}).get("frontier", ()))
        evidence = dict(context or {}).get("evidence")
        prior_art = tuple(str(item) for item in dict(context or {}).get("prior_art", ()))
        left_eval = self.evaluator.evaluate(
            left_regime,
            goal=goal,
            frontier=frontier,
            history=self.history,
            evidence=evidence,
            prior_art=prior_art,
        )
        right_eval = self.evaluator.evaluate(
            right_regime,
            goal=goal,
            frontier=frontier,
            history=self.history,
            evidence=evidence,
            prior_art=prior_art,
        )
        rationale = (
            f"switch to improve composite score from {left_eval.composite():.2f} "
            f"to {right_eval.composite():.2f}; "
            f"bridge overlap={len(left_regime.compatible_bridges(right_regime))}"
        )
        cost = max(0.0, 0.40 - 0.20 * len(left_regime.compatible_bridges(right_regime)))
        trust_effect = right_eval.trust - left_eval.trust
        expected_novelty = max(0.0, right_eval.novelty - left_eval.novelty + right_regime.aligns_with_goal(goal) / 4.0)
        return RegimeTransition(
            from_regime=left_regime.regime_id,
            to_regime=right_regime.regime_id,
            rationale=rationale,
            cost=cost,
            trust_effect=trust_effect,
            expected_novelty=expected_novelty,
        )

    def switch_regime(
        self,
        current: IdeationRegime | str | None,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> RegimeTransition | None:
        """Recommend a regime switch when another regime dominates the current one."""

        target = self.select_for_goal(goal, context=context)
        if target is None:
            return None
        if current is None:
            return RegimeTransition(
                from_regime="unassigned",
                to_regime=target.regime_id,
                rationale=f"initial regime choice for goal '{_normalize_text(goal)}'",
                cost=0.10,
                trust_effect=0.0,
                expected_novelty=target.aligns_with_goal(goal),
            )
        current_regime = current if isinstance(current, IdeationRegime) else self.catalog.get(current)
        if current_regime is None:
            raise KeyError(f"unknown current regime: {current}")
        candidate = self.compare_regimes(current_regime, target, goal, context=context)
        if candidate.to_regime == current_regime.regime_id or candidate.net_value() <= 0.05:
            return None
        return candidate

    def explain_ranking(
        self,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
        limit: int = 3,
    ) -> str:
        """Explain the top-ranked regimes for a goal."""

        ranked = self.rank_candidates(goal, context=context)
        lines = [f"Ranking for '{_normalize_text(goal)}':"]
        for regime, score in ranked[: max(0, limit)]:
            lines.append(f"- {regime.name} ({regime.regime_id}) score={score:.2f}")
        return "\n".join(lines)

    def copilot_regime_advice(
        self,
        goal: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """Return compact copilot-facing advice about regime choice."""

        best = self.select_for_goal(goal, context=context)
        if best is None:
            return f"copilot regime advice: no regime available for '{_normalize_text(goal)}'."
        alternates = [regime.name for regime, _ in self.rank_candidates(goal, context=context)[1:3]]
        alternate_text = ", ".join(alternates) if alternates else "no clear alternate"
        return (
            f"copilot regime advice: start with '{best.name}' ({best.regime_id}) for "
            f"goal '{_normalize_text(goal)}'; alternates: {alternate_text}."
        )

    # ------------------------------------------------------------------
    # Judgment-geometric integration
    # ------------------------------------------------------------------

    def regime_from_site(
        self,
        site: Any | None = None,
        *,
        goal: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> "IdeationRegime | None":
        """Select a regime based on site topology.

        Uses :mod:`jugeo.geometry.site` to inspect the Grothendieck
        topology of the judgment site and choose a regime whose purpose
        and admissible moves align with the site's covering families and
        coordinate structure.

        Parameters
        ----------
        site:
            A :class:`~jugeo.geometry.site.Site` instance.  When ``None``
            the method falls back to :meth:`select_for_goal`.
        goal:
            Research goal for alignment scoring.
        context:
            Optional context mapping forwarded to ranking.

        Returns
        -------
        IdeationRegime or None
            The best-matching regime, or ``None`` if no candidate exists.
        """
        if Site is None or site is None:
            return self.select_for_goal(goal, context=context)

        # Extract topological features from the site.
        coord_count = len(site.coordinates) if hasattr(site, "coordinates") else 0
        covering_count = len(site.covering_families) if hasattr(site, "covering_families") else 0
        # Augment context with site topology data.
        augmented_context = dict(context or {})
        augmented_context["site_coord_count"] = coord_count
        augmented_context["site_covering_count"] = covering_count

        # Prefer regimes whose bridge patterns intersect site coordinate kinds.
        ranked = self.rank_candidates(goal, context=augmented_context)
        if not ranked:
            return None

        # Boost regimes with bridge patterns matching site structure.
        best_regime, best_score = ranked[0]
        for regime, score in ranked:
            topo_bonus = 0.0
            if covering_count > 0 and any("cover" in p.lower() for p in regime.bridge_patterns):
                topo_bonus += 0.10
            if coord_count > 5 and any("transport" in p.lower() for p in regime.bridge_patterns):
                topo_bonus += 0.05
            if score + topo_bonus > best_score:
                best_score = score + topo_bonus
                best_regime = regime
        return best_regime

    def budget_constrained_regime(
        self,
        goal: str,
        ledger: Any | None = None,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> tuple["IdeationRegime | None", dict[str, Any]]:
        """Select a regime factoring in orchestration budget constraints.

        Uses :mod:`jugeo.orchestration.budgets` to inspect the current
        budget ledger and exclude regimes whose estimated cost exceeds the
        remaining allocation.

        Parameters
        ----------
        goal:
            Research goal for alignment scoring.
        ledger:
            A :class:`~jugeo.orchestration.budgets.BudgetLedger` instance.
            When ``None`` budget constraints are ignored and the method
            falls back to :meth:`select_for_goal`.
        context:
            Optional context mapping forwarded to ranking.

        Returns
        -------
        tuple
            ``(selected_regime, budget_info)`` where *budget_info* reports
            remaining budget and whether a constraint was applied.
        """
        if BudgetLedger is None or ledger is None:
            regime = self.select_for_goal(goal, context=context)
            return regime, {"budget_constrained": False, "reason": "no ledger"}

        remaining = ledger.remaining() if hasattr(ledger, "remaining") else float("inf")
        ranked = self.rank_candidates(goal, context=context)
        for regime, score in ranked:
            # Estimate regime cost from move count and constraint count.
            estimated_cost = float(len(regime.admissible_moves)) + 0.5 * float(len(regime.active_constraints))
            if estimated_cost <= remaining:
                return regime, {
                    "budget_constrained": True,
                    "remaining_budget": remaining,
                    "estimated_cost": estimated_cost,
                    "regime_id": regime.regime_id,
                    "score": score,
                }
        return None, {
            "budget_constrained": True,
            "remaining_budget": remaining,
            "reason": "all regimes exceed budget",
        }

    def certificate_for_regime(
        self,
        regime: "IdeationRegime",
        *,
        goal: str = "",
        evidence: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        """Produce a regime certificate using the evidence subsystem.

        Uses :mod:`jugeo.evidence.certificates` to build a
        :class:`~jugeo.evidence.certificates.Certificate` attesting to
        the regime's suitability for a given goal, incorporating the
        regime's trust score and evaluation composite.

        Parameters
        ----------
        regime:
            The regime to certify.
        goal:
            The research goal the regime is being certified for.
        evidence:
            Optional evidence mapping forwarded to
            :meth:`IdeationRegime.trust_score`.

        Returns
        -------
        dict
            Certificate data including trust score, evaluation composite,
            and certificate id (when the certificates subsystem is available).
        """
        trust = regime.trust_score(evidence)
        evaluation = self.evaluator.evaluate(regime, goal=goal)
        base_info = {
            "regime_id": regime.regime_id,
            "regime_name": regime.name,
            "goal": goal,
            "trust_score": trust,
            "evaluation_composite": evaluation.composite(),
            "evaluation_verdict": evaluation.verdict(),
        }

        if CertificateBuilder is None:
            base_info["certificate_status"] = "unavailable"
            base_info["reason"] = "jugeo.evidence.certificates not installed"
            return base_info

        builder = CertificateBuilder()
        builder = builder.for_coordinate(regime.regime_id)
        builder = builder.add_verified(f"regime-suitable-for:{goal}")
        builder = builder.set_issuer("jugeo.ideation.regimes")
        builder = builder.set_evidence_summary(
            f"trust={trust:.2f}, composite={evaluation.composite():.2f}, "
            f"verdict={evaluation.verdict()}"
        )
        builder = builder.sign()
        cert = builder.build()
        base_info["certificate_id"] = cert.certificate_id if hasattr(cert, "certificate_id") else str(cert)
        base_info["certificate_status"] = "issued"
        return base_info


class RegimeHistory:
    """Replayable history of regime transitions and outcomes."""

    def __init__(self, entries: Iterable[RegimeHistoryEntry] = ()) -> None:
        """Create a history from zero or more existing entries."""

        self.entries: list[RegimeHistoryEntry] = list(entries)

    def record_transition(
        self,
        transition: RegimeTransition,
        *,
        success: bool,
        novelty_realized: float,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        notes: str = "",
        failure_mode: str | None = None,
    ) -> RegimeHistoryEntry:
        """Append a transition outcome to history."""

        start = started_at or datetime.now(UTC)
        end = ended_at or start
        if end < start:
            end = start
        entry = RegimeHistoryEntry(
            transition=transition,
            success=success,
            novelty_realized=_clamp(novelty_realized),
            started_at=start,
            ended_at=end,
            notes=_normalize_text(notes),
            failure_mode=_normalize_text(failure_mode) if failure_mode else None,
        )
        self.entries.append(entry)
        return entry

    def active_time(self, regime_id: str | None = None) -> float:
        """Return active time in seconds for a regime or for all regimes."""

        return sum(
            entry.duration_seconds()
            for entry in self.entries
            if regime_id is None or entry.touches(regime_id)
        )

    def success_rate(self, regime_id: str | None = None) -> float:
        """Return the share of successful entries."""

        relevant = [entry for entry in self.entries if regime_id is None or entry.touches(regime_id)]
        if not relevant:
            return 0.0
        return sum(1.0 for entry in relevant if entry.success) / len(relevant)

    def novelty_yield(self, regime_id: str | None = None) -> float:
        """Return average realized novelty for matching entries."""

        relevant = [entry.novelty_realized for entry in self.entries if regime_id is None or entry.touches(regime_id)]
        return _mean(relevant)

    def failure_modes(self, regime_id: str | None = None) -> dict[str, int]:
        """Aggregate failure modes for matching entries."""

        counter: Counter[str] = Counter()
        for entry in self.entries:
            if regime_id is not None and not entry.touches(regime_id):
                continue
            if entry.success:
                continue
            counter[entry.failure_mode or "unknown"] += 1
        return _sorted_counter(counter)

    def transition_count(self, regime_id: str | None = None) -> int:
        """Count transitions that involve a given regime."""

        return sum(1 for entry in self.entries if regime_id is None or entry.touches(regime_id))

    def last_transition(self, regime_id: str | None = None) -> RegimeHistoryEntry | None:
        """Return the most recent transition involving a regime."""

        for entry in reversed(self.entries):
            if regime_id is None or entry.touches(regime_id):
                return entry
        return None

    def entries_for(self, regime_id: str) -> tuple[RegimeHistoryEntry, ...]:
        """Return all entries that involve the requested regime."""

        return tuple(entry for entry in self.entries if entry.touches(regime_id))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the history into a dictionary."""

        return {"entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegimeHistory":
        """Deserialize a history object from a dictionary."""

        return cls(RegimeHistoryEntry.from_dict(item) for item in payload.get("entries", ()))


class RegimeBootstrapper:
    """Bootstrap provisional regimes from purpose statements and seed terms.

    Bootstrapping is how new mathematical kinds enter JuGeo without receiving
    immediate trusted-core authority. The bootstrapper synthesizes provisional
    moves, bridge stubs, and a trust discipline that naturally pushes copilot
    experiments toward sandbox replay first.
    """

    def __init__(self, policy: RegimePolicy | None = None) -> None:
        """Create a bootstrapper with optional policy guidance."""

        self.policy = policy or DEFAULT_POLICY

    def bootstrap(
        self,
        *,
        name: str,
        purpose: str,
        seed_terms: Sequence[str],
        evidence: Mapping[str, float] | None = None,
        prior_regimes: Sequence[IdeationRegime] = (),
    ) -> IdeationRegime:
        """Create a provisional regime from sparse semantic hints."""

        constraints = self.infer_initial_constraints(purpose, seed_terms, evidence=evidence)
        moves = self.synthesize_move_set(purpose, seed_terms, prior_regimes=prior_regimes)
        bridges = self.derive_bridge_patterns(seed_terms, prior_regimes)
        trust_policy = self.propose_trust_policy(purpose, evidence)
        metric = "semantic-distance"
        if any("bridge" in term.lower() for term in seed_terms):
            metric = "bridge-seeking"
        elif "proof" in purpose.lower():
            metric = "proof-surface"
        regime = IdeationRegime(
            regime_id=_slugify(name),
            name=name,
            purpose=purpose,
            admissible_moves=moves,
            novelty_metric=metric,
            trust_policy=trust_policy,
            bridge_patterns=bridges,
            active_constraints=constraints,
        )
        valid, errors = self.validate_bootstrap(regime)
        if not valid:
            raise ValueError(f"invalid regime bootstrap: {', '.join(errors)}")
        return regime

    def infer_initial_constraints(
        self,
        purpose: str,
        seed_terms: Sequence[str],
        *,
        evidence: Mapping[str, float] | None = None,
    ) -> tuple[str, ...]:
        """Infer provisional constraints from purpose and evidence."""

        constraints: list[str] = ["sandbox-only", "protect-existing-certificates"]
        purpose_tokens = set(_tokenize(purpose))
        seed_token_text = " ".join(seed_terms).lower()
        if {"proof", "certificate", "witness"} & purpose_tokens:
            constraints.append("require-explicit-evidence")
        if {"bridge", "transport", "federation"} & purpose_tokens:
            constraints.append("require-bridge-trace")
        if "retire" in seed_token_text:
            constraints.append("forbid:destructive-retire")
        evidence_map = dict(evidence or {})
        if evidence_map.get("replay", 0.0) >= 0.75:
            constraints = [item for item in constraints if item != "sandbox-only"]
        if evidence_map.get("transport", 0.0) < 0.30:
            constraints.append("forbid:unprobed-transport")
        return _normalize_sequence(constraints)

    def synthesize_move_set(
        self,
        purpose: str,
        seed_terms: Sequence[str],
        *,
        prior_regimes: Sequence[IdeationRegime] = (),
    ) -> tuple[str, ...]:
        """Synthesize admissible moves for a provisional regime."""

        purpose_tokens = set(_tokenize(purpose))
        moves: list[str] = [
            "refine-cover",
            "introduce-bridge",
            "add-constructor",
            "federate-pack",
        ]
        if {"coefficient", "coefficients", "scalar"} & purpose_tokens:
            moves.append("change-coefficients")
        if {"proof", "lemma", "witness"} & purpose_tokens:
            moves.append("prove-template")
        if {"retire", "simplify"} & purpose_tokens:
            moves.append("retire-regime")
        for term in seed_terms:
            token = _slugify(term)
            if token:
                moves.append(f"probe-{token}")
        inherited = [
            move
            for regime in prior_regimes
            for move in regime.admissible_moves
            if _overlap_score(regime.purpose, purpose) >= 0.20
        ]
        moves.extend(inherited[:4])
        return _normalize_sequence(moves)

    def derive_bridge_patterns(
        self,
        seed_terms: Sequence[str],
        prior_regimes: Sequence[IdeationRegime],
    ) -> tuple[str, ...]:
        """Derive provisional bridge stubs from seeds and nearby regimes."""

        patterns: list[str] = []
        for term in seed_terms:
            token = _slugify(term)
            if token:
                patterns.extend(
                    (
                        f"transport::{token}",
                        f"bridge::{token}",
                    )
                )
        for regime in prior_regimes:
            patterns.extend(regime.bridge_patterns[:2])
        return _normalize_sequence(patterns or ("transport::sandbox", "bridge::experimental-law"))

    def propose_trust_policy(
        self,
        purpose: str,
        evidence: Mapping[str, float] | None = None,
    ) -> str:
        """Propose a trust policy for a newly bootstrapped regime."""

        evidence_map = dict(evidence or {})
        if evidence_map.get("replay", 0.0) >= 0.80 and evidence_map.get("proof", 0.0) >= 0.70:
            return "strict-certificate replay-first"
        if "certificate" in purpose.lower():
            return "provisional-certificate sandbox replay-first"
        return "provisional-sandbox replay-first"

    def validate_bootstrap(self, regime: IdeationRegime) -> tuple[bool, tuple[str, ...]]:
        """Validate a provisional regime before it enters the catalog."""

        errors: list[str] = []
        if len(regime.admissible_moves) < 3:
            errors.append("too-few-moves")
        if len(regime.bridge_patterns) < 2:
            errors.append("too-few-bridges")
        if not regime.purpose:
            errors.append("missing-purpose")
        if "sandbox-only" in regime.active_constraints and "sandbox" not in regime.trust_policy:
            errors.append("sandbox-policy-mismatch")
        if regime.novelty_metric not in {"semantic-distance", "bridge-seeking", "proof-surface"}:
            errors.append("unknown-novelty-metric")
        return not errors, _normalize_sequence(errors)

    def bootstrap_report(self, regime: IdeationRegime) -> str:
        """Describe a bootstrapped regime for diagnostics and copilot output."""

        valid, errors = self.validate_bootstrap(regime)
        status = "valid" if valid else f"invalid ({', '.join(errors)})"
        return (
            f"Bootstrapped regime '{regime.name}' [{regime.regime_id}] is {status}; "
            f"moves={len(regime.admissible_moves)}, bridges={len(regime.bridge_patterns)}, "
            f"constraints={len(regime.active_constraints)}."
        )


class RegimeSerializer:
    """Deterministic JSON serialization helpers for regime objects."""

    def normalize(self, value: Any) -> Any:
        """Recursively lower supported regime objects into JSON-safe data."""

        if isinstance(value, IdeationRegime):
            return value.to_dict()
        if isinstance(value, RegimeTransition):
            return value.to_dict()
        if isinstance(value, RegimeHistory):
            return value.to_dict()
        if isinstance(value, RegimeHistoryEntry):
            return value.to_dict()
        if isinstance(value, RegimePolicy):
            return value.to_dict()
        if isinstance(value, RegimeEvaluation):
            return value.to_dict()
        if isinstance(value, RegimeCatalog):
            return value.to_dict()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Mapping):
            return {str(key): self.normalize(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, (list, tuple, set)):
            return [self.normalize(item) for item in value]
        return value

    def to_json(self, value: Any, *, indent: int = 2) -> str:
        """Serialize a supported value to deterministic JSON."""

        return json.dumps(self.normalize(value), indent=indent, sort_keys=True)

    def from_json(self, payload: str, *, kind: str) -> Any:
        """Deserialize JSON into a requested regime-related kind."""

        data = json.loads(payload)
        if kind == "regime":
            return IdeationRegime.from_dict(data)
        if kind == "transition":
            return RegimeTransition.from_dict(data)
        if kind == "history":
            return RegimeHistory.from_dict(data)
        if kind == "policy":
            return RegimePolicy.from_dict(data)
        if kind == "catalog":
            return self.deserialize_catalog(payload)
        raise ValueError(f"unsupported kind: {kind}")

    def serialize_catalog(self, catalog: RegimeCatalog, *, indent: int = 2) -> str:
        """Serialize a catalog to JSON."""

        return self.to_json(catalog, indent=indent)

    def deserialize_catalog(self, payload: str) -> RegimeCatalog:
        """Deserialize a catalog from JSON."""

        data = json.loads(payload)
        catalog = RegimeCatalog(IdeationRegime.from_dict(item) for item in data.get("regimes", ()))
        for regime_id in data.get("active", ()):
            if catalog.get(regime_id) is not None:
                catalog.activate(str(regime_id))
        return catalog

    def serialize_history(self, history: RegimeHistory, *, indent: int = 2) -> str:
        """Serialize a history to JSON."""

        return self.to_json(history, indent=indent)

    def deserialize_history(self, payload: str) -> RegimeHistory:
        """Deserialize a history from JSON."""

        return RegimeHistory.from_dict(json.loads(payload))

    def serialize_transition(self, transition: RegimeTransition, *, indent: int = 2) -> str:
        """Serialize a transition to JSON."""

        return self.to_json(transition, indent=indent)

    def deserialize_transition(self, payload: str) -> RegimeTransition:
        """Deserialize a transition from JSON."""

        return RegimeTransition.from_dict(json.loads(payload))


class RegimeDiagnostics:
    """Human-readable diagnostics for regime catalogs, policy, and history."""

    def __init__(
        self,
        *,
        evaluator: RegimeEvaluator | None = None,
        history: RegimeHistory | None = None,
        policy: RegimePolicy | None = None,
    ) -> None:
        """Create a diagnostic surface over regime orchestration state."""

        self.policy = policy or DEFAULT_POLICY
        self.evaluator = evaluator or RegimeEvaluator(self.policy)
        self.history = history or RegimeHistory()

    def summary(self, catalog: RegimeCatalog) -> str:
        """Summarize catalog size, activity, and common purposes."""

        active = len(catalog.active_regimes())
        total = len(catalog.list_regimes())
        purpose_tokens = Counter(
            token
            for regime in catalog.list_regimes()
            for token in _tokenize(regime.purpose)
            if len(token) > 4
        )
        common = ", ".join(token for token, _ in purpose_tokens.most_common(5)) or "none"
        return f"Catalog summary: {total} regimes, {active} active, common purposes: {common}."

    def transition_report(self, history: RegimeHistory | None = None) -> str:
        """Summarize transition outcomes and failure modes."""

        source = history or self.history
        if not source.entries:
            return "Transition report: no regime transitions recorded."
        failures = source.failure_modes()
        failure_text = ", ".join(f"{mode}={count}" for mode, count in failures.items()) or "none"
        return (
            f"Transition report: {source.transition_count()} transitions, success_rate={source.success_rate():.2f}, "
            f"novelty_yield={source.novelty_yield():.2f}, failures={failure_text}."
        )

    def purpose_alignment_report(self, catalog: RegimeCatalog, goal: str) -> str:
        """Describe which regimes best align with a goal."""

        ranked = catalog.find_by_purpose(goal, limit=5)
        if not ranked:
            return f"Purpose alignment for '{_normalize_text(goal)}': no regimes available."
        lines = [f"Purpose alignment for '{_normalize_text(goal)}':"]
        for regime in ranked:
            lines.append(f"- {regime.name}: alignment={regime.aligns_with_goal(goal):.2f}")
        return "\n".join(lines)

    def policy_report(self, regime: IdeationRegime) -> str:
        """Explain policy and failure hot spots for a regime."""

        failures = self.history.failure_modes(regime.regime_id)
        failure_text = ", ".join(f"{mode}={count}" for mode, count in failures.items()) or "none"
        return f"{self.policy.explain_policy(regime)} Failures for {regime.regime_id}: {failure_text}."

    def evaluation_report(
        self,
        regime: IdeationRegime,
        *,
        goal: str,
        frontier: Sequence[str] = (),
        evidence: Mapping[str, float] | None = None,
        prior_art: Iterable[str] = (),
    ) -> str:
        """Render a detailed evaluation report for a regime."""

        evaluation = self.evaluator.evaluate(
            regime,
            goal=goal,
            frontier=frontier,
            history=self.history,
            evidence=evidence,
            prior_art=prior_art,
        )
        return (
            f"Evaluation for {regime.regime_id}: verdict={evaluation.verdict()}, composite={evaluation.composite():.2f}, "
            f"progress={evaluation.progress:.2f}, novelty={evaluation.novelty:.2f}, "
            f"stability={evaluation.stability:.2f}, trust={evaluation.trust:.2f}, "
            f"recommendation={evaluation.recommendation}."
        )

    def copilot_regime_summary(self, catalog: RegimeCatalog, goal: str) -> str:
        """Produce a concise copilot-facing regime overview."""

        selector = RegimeSelector(catalog, evaluator=self.evaluator, policy=self.policy, history=self.history)
        best = selector.select_for_goal(goal)
        if best is None:
            return f"copilot regime summary: no candidate regime for '{_normalize_text(goal)}'."
        compatible = catalog.compatible_with(best)
        alternate = compatible[0].name if compatible else "no nearby bridge partner"
        return (
            f"copilot regime summary: '{best.name}' best serves '{_normalize_text(goal)}'; "
            f"trust={best.trust_score({'replay': 0.6, 'proof': 0.5, 'transport': 0.5, 'sandbox': 0.8}):.2f}; "
            f"nearest compatible regime: {alternate}."
        )


__all__ = [
    "DEFAULT_POLICY",
    "IdeationRegime",
    "RegimeBootstrapper",
    "RegimeCatalog",
    "RegimeDiagnostics",
    "RegimeEvaluation",
    "RegimeEvaluator",
    "RegimeHistory",
    "RegimeHistoryEntry",
    "RegimeKind",
    "RegimePolicy",
    "RegimeProposal",
    "RegimeSelector",
    "RegimeSerializer",
    "RegimeTransition",
    "choose_regime",
]
