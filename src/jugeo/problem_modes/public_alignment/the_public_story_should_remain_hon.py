"""
# copilot: the_public_story_should_remain_hon — Public Narrative Honesty Invariant

The public story of a system is the collection of claims it makes about itself: what it
does, what it does not do, the trust level of its guarantees, and the scope of its
obligations.  In the judgment category (theory2.tex Ch13), a claim is a morphism from a
coordinate to a trust level.  The honesty invariant states that no claim may assert a
higher trust level than has been verified by evidence.

Violations of honesty are cohomological obstructions in Ȟ¹(X, 𝒯) where 𝒯 is the trust
sheaf over the public surface X.  An OVERCLAIM at coordinate p is a 1-cocycle that fails
to be a coboundary because the claimed trust exceeds the verified trust by more than the
allowed tolerance.  The repair functor maps each violated narrative to a weakened version
in which every claim is downgraded to its verified trust level.

§13.5 – Public Narrative Honesty Invariant (theory2.tex, Ch 13)

This module implements the honesty checker, narrative model, repair functor, and the
top-level coordinator that applies the honesty invariant across a set of narratives.  The
``ThePublicStoryRemainCoordinator`` is the primary entry point; it returns repaired
narratives together with a list of ``HonestyViolation`` records and plain-dict obstruction
payloads for each violation that could not be automatically repaired.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# §1  Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# §2  Jugeo imports (try/except pattern)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        TrustLevel,
        Proposition,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
        JudgmentAlgebra,
        JudgmentStatus,
    )
except ImportError:
    Judgment = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    Proposition = Any  # type: ignore[assignment,misc]
    Carrier = Any  # type: ignore[assignment,misc]
    EvidenceBundle = Any  # type: ignore[assignment,misc]
    EvidenceItem = Any  # type: ignore[assignment,misc]
    EvidenceItemKind = Any  # type: ignore[assignment,misc]
    ResidualObligation = Any  # type: ignore[assignment,misc]
    Obstruction = Any  # type: ignore[assignment,misc]
    TrustAnnotation = Any  # type: ignore[assignment,misc]
    Provenance = Any  # type: ignore[assignment,misc]
    ProvenanceSource = Any  # type: ignore[assignment,misc]
    JudgmentAlgebra = Any  # type: ignore[assignment,misc]
    JudgmentStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import (
        StructuredFailure,
        JuGeoError,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        FailureChain,
        as_failure_payload,
        raise_with_scope,
    )
except ImportError:
    StructuredFailure = Any  # type: ignore[assignment,misc]
    JuGeoError = Any  # type: ignore[assignment,misc]
    FailureScope = Any  # type: ignore[assignment,misc]
    FailureClassification = Any  # type: ignore[assignment,misc]
    EvidenceFamily = Any  # type: ignore[assignment,misc]
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    RepairHint = Any  # type: ignore[assignment,misc]
    RepairPriority = Any  # type: ignore[assignment,misc]
    FailureChain = Any  # type: ignore[assignment,misc]
    as_failure_payload = Any  # type: ignore[assignment,misc]
    raise_with_scope = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.public_alignment.models import (
        PublicClaim,
        HonestProjection,
        DocumentationSection,
        MigrationPlan,
        _now_iso,
        _new_id,
    )
except ImportError:
    PublicClaim = Any  # type: ignore[assignment,misc]
    HonestProjection = Any  # type: ignore[assignment,misc]
    DocumentationSection = Any  # type: ignore[assignment,misc]
    MigrationPlan = Any  # type: ignore[assignment,misc]

    def _now_iso() -> str:
        """Return the current UTC time as an ISO-8601 string."""
        import time

        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _new_id(prefix: str = "id") -> str:
        """Generate a short unique identifier with the given prefix."""
        import uuid

        return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# §3  Module-level constants
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch13-public-alignment",
    "sequence": 5,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "the_public_story_should_remain_hon",
    "class": "ThePublicStoryRemainCoordinator",
    "theory_section": "§13.5 – Public Narrative Honesty Invariant",
}

# Internal severity ordering used by HonestyChecker._severity_weight.
_SEVERITY_WEIGHTS: dict[str, float] = {
    "LOW": 0.1,
    "MEDIUM": 0.3,
    "HIGH": 0.6,
    "CRITICAL": 1.0,
}

# ---------------------------------------------------------------------------
# §4  HonestyViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HonestyViolation:
    """A single honesty violation detected in a public claim or narrative.

    In the cohomological picture (theory2.tex §13.5), a ``HonestyViolation``
    is a 1-cocycle in Ȟ¹(X, 𝒯) that fails to be exact.  The ``violation_kind``
    specifies which type of honesty failure occurred; ``severity`` encodes the
    magnitude of the trust inflation.

    Attributes:
        violation_id: Unique identifier.
        coordinate: The public coordinate where the violation was detected.
        claim_text: The text of the claim that violated honesty.
        verified_trust_level: The trust level supported by evidence.
        claimed_trust_level: The trust level asserted in the claim.
        violation_kind: One of OVERCLAIM, FALSE_CERTAINTY, HIDDEN_ASSUMPTION,
            SCOPE_CREEP, TRUST_INFLATION.
        severity: One of LOW, MEDIUM, HIGH, CRITICAL.
        detected_at: ISO-8601 timestamp when the violation was detected.
        repair_suggestion: Human-readable suggestion for repairing the violation.
        metadata: Arbitrary additional data.
    """

    violation_id: str
    coordinate: str
    claim_text: str
    verified_trust_level: Any
    claimed_trust_level: Any
    violation_kind: str
    severity: str
    detected_at: str
    repair_suggestion: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the violation to a JSON-compatible dict.

        Converts all fields to JSON-safe types, coercing trust levels to
        strings.
        """
        return {
            "violation_id": self.violation_id,
            "coordinate": self.coordinate,
            "claim_text": self.claim_text,
            "verified_trust_level": str(self.verified_trust_level),
            "claimed_trust_level": str(self.claimed_trust_level),
            "violation_kind": self.violation_kind,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "repair_suggestion": self.repair_suggestion,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HonestyViolation:
        """Reconstruct a ``HonestyViolation`` from a plain dict."""
        return cls(
            violation_id=data.get("violation_id", _new_id("viol")),
            coordinate=data.get("coordinate", ""),
            claim_text=data.get("claim_text", ""),
            verified_trust_level=data.get("verified_trust_level"),
            claimed_trust_level=data.get("claimed_trust_level"),
            violation_kind=data.get("violation_kind", "OVERCLAIM"),
            severity=data.get("severity", "MEDIUM"),
            detected_at=data.get("detected_at", _now_iso()),
            repair_suggestion=data.get("repair_suggestion", ""),
            metadata=dict(data.get("metadata", {})),
        )

    def is_critical(self) -> bool:
        """Return True iff this violation has CRITICAL severity.

        Critical violations must be resolved before a narrative may be
        published.  They correspond to claims that assert capabilities or
        trust levels that are entirely unsupported by evidence.
        """
        return self.severity == "CRITICAL"

    def to_obstruction(self) -> dict[str, Any]:
        """Convert this violation to an obstruction payload.

        Returns a plain dict obstruction record suitable for embedding in
        coordinator output or surfacing to callers that cannot import
        ``ObstructionRecord`` directly.
        """
        return {
            "obstruction_id": _new_id("obs"),
            "coordinate": self.coordinate,
            "reason": (
                f"{self.violation_kind} — claimed {self.claimed_trust_level!r} "
                f"but verified {self.verified_trust_level!r}: {self.claim_text[:80]}"
            ),
            "violation_id": self.violation_id,
            "severity": self.severity,
            "repair_suggestion": self.repair_suggestion,
            "created_at": _now_iso(),
        }

    def delta_trust_levels(self) -> int:
        """Return the numeric delta between claimed and verified trust levels.

        When trust levels are integers (or can be coerced to int), this
        returns ``claimed - verified``.  If coercion fails, returns 0 (no
        measurable delta).
        """
        try:
            return int(self.claimed_trust_level) - int(self.verified_trust_level)
        except (TypeError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# §5  PublicNarrative
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicNarrative:
    """A versioned public narrative containing claims about system behaviour.

    A ``PublicNarrative`` is a snapshot of what the system claims about
    itself at a particular version.  The ``verified_claims`` subset lists
    claims that have been confirmed by the judgment engine; ``claims`` may
    contain additional, unverified claims.  The honesty invariant requires
    that ``set(claims) ⊆ set(verified_claims)`` after repair.

    Attributes:
        narrative_id: Unique identifier.
        title: Human-readable title.
        sections: Tuple of section names or identifiers in this narrative.
        claims: All claims currently in the narrative (verified or not).
        verified_claims: Claims confirmed by the judgment engine.
        trust_level: Overall trust level assigned to this narrative.
        audience: Target audience (e.g. "end-user", "developer", "auditor").
        version: Semantic version of this narrative.
        published_at: ISO-8601 timestamp of publication.
        is_honest: Whether the narrative currently satisfies the honesty invariant.
        metadata: Arbitrary additional data.
    """

    narrative_id: str
    title: str
    sections: tuple[str, ...]
    claims: tuple[str, ...]
    verified_claims: tuple[str, ...]
    trust_level: Any
    audience: str
    version: str
    published_at: str
    is_honest: bool
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the narrative to a JSON-compatible dict."""
        return {
            "narrative_id": self.narrative_id,
            "title": self.title,
            "sections": list(self.sections),
            "claims": list(self.claims),
            "verified_claims": list(self.verified_claims),
            "trust_level": str(self.trust_level),
            "audience": self.audience,
            "version": self.version,
            "published_at": self.published_at,
            "is_honest": self.is_honest,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicNarrative:
        """Reconstruct a ``PublicNarrative`` from a plain dict."""
        return cls(
            narrative_id=data.get("narrative_id", _new_id("narr")),
            title=data.get("title", ""),
            sections=tuple(data.get("sections", [])),
            claims=tuple(data.get("claims", [])),
            verified_claims=tuple(data.get("verified_claims", [])),
            trust_level=data.get("trust_level"),
            audience=data.get("audience", "developer"),
            version=data.get("version", "1.0.0"),
            published_at=data.get("published_at", _now_iso()),
            is_honest=bool(data.get("is_honest", False)),
            metadata=dict(data.get("metadata", {})),
        )

    def claim_count(self) -> int:
        """Return the total number of claims in this narrative."""
        return len(self.claims)

    def verification_ratio(self) -> float:
        """Return the fraction of claims that have been verified.

        Returns 1.0 when the claim set is empty (vacuous truth).
        """
        if not self.claims:
            return 1.0
        verified_set = set(self.verified_claims)
        return sum(1 for c in self.claims if c in verified_set) / len(self.claims)

    def summary(self) -> str:
        """Return a one-line summary of the narrative.

        Includes the narrative ID, title, version, honesty status, and
        verification ratio.
        """
        return (
            f"[{self.narrative_id}] {self.title!r} v{self.version} "
            f"honest={self.is_honest} verified={self.verification_ratio():.0%}"
        )

    def weaken_to_honest(self) -> PublicNarrative:
        """Return a new narrative containing only verified claims.

        This is the repair functor R applied to the narrative: it projects
        the claim set onto its verified sub-bundle, ensuring that the result
        satisfies the honesty invariant.  The returned narrative has
        ``is_honest=True`` and ``claims == verified_claims``.
        """
        return replace(
            self,
            claims=self.verified_claims,
            is_honest=True,
            metadata={
                **self.metadata,
                "weakened_at": _now_iso(),
                "original_claim_count": len(self.claims),
                "removed_claims": len(self.claims) - len(self.verified_claims),
            },
        )


# ---------------------------------------------------------------------------
# §6  HonestyChecker
# ---------------------------------------------------------------------------


class HonestyChecker:
    """Stateless checker that detects honesty violations in claims and narratives.

    All methods are pure functions; the class carries no internal state.  It
    is designed for use as a delegate inside coordinators and analyzers.

    Theory reference: the check_claim method implements the local
    section comparison that detects Ȟ¹ obstructions (theory2.tex §13.5,
    Algorithm 13.5.1).
    """

    def check_claim(
        self,
        claim: str,
        verified_level: Any,
        declared_level: Any,
    ) -> HonestyViolation | None:
        """Check a single claim for honesty violations.

        A violation is detected when the declared trust level exceeds the
        verified trust level, or when the claim is empty (which would be a
        FALSE_CERTAINTY violation — asserting something without content).

        Args:
            claim: The text of the claim to check.
            verified_level: Trust level supported by evidence.
            declared_level: Trust level asserted in the claim.

        Returns:
            A ``HonestyViolation`` if a violation was detected, else None.
        """
        if not claim.strip():
            return HonestyViolation(
                violation_id=_new_id("viol"),
                coordinate="unknown",
                claim_text=claim,
                verified_trust_level=verified_level,
                claimed_trust_level=declared_level,
                violation_kind="FALSE_CERTAINTY",
                severity="HIGH",
                detected_at=_now_iso(),
                repair_suggestion="Remove empty claims from the narrative.",
            )
        try:
            if int(declared_level) > int(verified_level):
                delta = int(declared_level) - int(verified_level)
                severity = "LOW" if delta <= 1 else ("MEDIUM" if delta <= 2 else "HIGH")
                return HonestyViolation(
                    violation_id=_new_id("viol"),
                    coordinate="claim",
                    claim_text=claim,
                    verified_trust_level=verified_level,
                    claimed_trust_level=declared_level,
                    violation_kind="TRUST_INFLATION",
                    severity=severity,
                    detected_at=_now_iso(),
                    repair_suggestion=(
                        f"Lower declared trust from {declared_level} to {verified_level}."
                    ),
                )
        except (TypeError, ValueError):
            pass
        return None

    def check_narrative(
        self,
        narrative: PublicNarrative,
    ) -> list[HonestyViolation]:
        """Check all claims in a narrative for honesty violations.

        Compares the full ``claims`` set against ``verified_claims``.  Any
        claim that appears in ``claims`` but not in ``verified_claims`` is
        flagged as an OVERCLAIM.

        Args:
            narrative: The ``PublicNarrative`` to check.

        Returns:
            List of ``HonestyViolation`` objects, one per unverified claim.
        """
        verified_set = set(narrative.verified_claims)
        violations: list[HonestyViolation] = []
        for claim in narrative.claims:
            if claim not in verified_set:
                violations.append(
                    HonestyViolation(
                        violation_id=_new_id("viol"),
                        coordinate=narrative.narrative_id,
                        claim_text=claim,
                        verified_trust_level=None,
                        claimed_trust_level=narrative.trust_level,
                        violation_kind="OVERCLAIM",
                        severity="HIGH",
                        detected_at=_now_iso(),
                        repair_suggestion=(
                            f"Remove or verify claim: {claim[:60]!r}"
                        ),
                    )
                )
        return violations

    def check_section(
        self,
        section_text: str,
        trust_level: Any,
    ) -> list[HonestyViolation]:
        """Check a free-text section for honesty red flags.

        Scans for over-confident language patterns (absolute guarantees,
        "always", "never", "guaranteed") that may indicate SCOPE_CREEP or
        FALSE_CERTAINTY.

        Args:
            section_text: The text of a narrative section.
            trust_level: The overall trust level of the narrative.

        Returns:
            List of ``HonestyViolation`` objects for detected red flags.
        """
        red_flags = ["always", "never", "guaranteed", "100%", "perfect", "infallible"]
        violations: list[HonestyViolation] = []
        lower = section_text.lower()
        for flag in red_flags:
            if flag in lower:
                violations.append(
                    HonestyViolation(
                        violation_id=_new_id("viol"),
                        coordinate="section",
                        claim_text=section_text[:120],
                        verified_trust_level=None,
                        claimed_trust_level=trust_level,
                        violation_kind="FALSE_CERTAINTY",
                        severity="MEDIUM",
                        detected_at=_now_iso(),
                        repair_suggestion=(
                            f"Replace absolute language {flag!r} with qualified phrasing."
                        ),
                    )
                )
        return violations

    def detect_overclaims(
        self,
        claims: Sequence[str],
        verified_claims: Sequence[str],
    ) -> list[HonestyViolation]:
        """Identify claims in ``claims`` that are absent from ``verified_claims``.

        Args:
            claims: Full set of claims to check.
            verified_claims: Set of claims confirmed by evidence.

        Returns:
            List of ``HonestyViolation`` objects for unverified claims.
        """
        verified_set = set(verified_claims)
        return [
            HonestyViolation(
                violation_id=_new_id("viol"),
                coordinate="claim-set",
                claim_text=claim,
                verified_trust_level=None,
                claimed_trust_level="asserted",
                violation_kind="OVERCLAIM",
                severity="HIGH",
                detected_at=_now_iso(),
                repair_suggestion=f"Verify or retract: {claim[:60]!r}",
            )
            for claim in claims
            if claim not in verified_set
        ]

    def detect_trust_inflation(
        self,
        declared: Any,
        internal: Any,
        coordinate: str,
    ) -> HonestyViolation | None:
        """Detect trust inflation: declared trust exceeds internal trust.

        Args:
            declared: Trust level declared in the public narrative.
            internal: Trust level supported by internal evidence.
            coordinate: The coordinate where inflation was detected.

        Returns:
            A ``HonestyViolation`` if inflation is detected, else None.
        """
        try:
            if int(declared) > int(internal):
                delta = int(declared) - int(internal)
                severity = (
                    "CRITICAL"
                    if delta >= 3
                    else ("HIGH" if delta >= 2 else "MEDIUM")
                )
                return HonestyViolation(
                    violation_id=_new_id("viol"),
                    coordinate=coordinate,
                    claim_text=f"Trust declared at {declared}, verified at {internal}",
                    verified_trust_level=internal,
                    claimed_trust_level=declared,
                    violation_kind="TRUST_INFLATION",
                    severity=severity,
                    detected_at=_now_iso(),
                    repair_suggestion=(
                        f"Lower public trust claim from {declared} to {internal}."
                    ),
                )
        except (TypeError, ValueError):
            pass
        return None

    def score_honesty(
        self,
        violations: Sequence[HonestyViolation],
    ) -> float:
        """Compute an honesty score 0.0–1.0 from a list of violations.

        The score is 1.0 − weighted_penalty, where penalty is the sum of
        ``_severity_weight(v.severity)`` for each violation, normalised to
        stay in [0, 1].  Zero violations returns 1.0.

        Args:
            violations: Sequence of detected ``HonestyViolation`` objects.

        Returns:
            Float honesty score.
        """
        if not violations:
            return 1.0
        total_penalty = sum(self._severity_weight(v.severity) for v in violations)
        return max(0.0, 1.0 - total_penalty / max(1, len(violations)))

    def repair_narrative(
        self,
        narrative: PublicNarrative,
        violations: Sequence[HonestyViolation],
    ) -> PublicNarrative:
        """Apply the repair functor to produce an honest narrative.

        Removes all claims that generated violations, resulting in a narrative
        that satisfies the honesty invariant.  This is the canonical
        implementation of the repair functor R described in theory2.tex §13.5.

        Args:
            narrative: The narrative to repair.
            violations: Violations detected in the narrative.

        Returns:
            A new ``PublicNarrative`` with violated claims removed.
        """
        violated_texts = {v.claim_text for v in violations}
        safe_claims = tuple(c for c in narrative.claims if c not in violated_texts)
        return replace(
            narrative,
            claims=safe_claims,
            is_honest=len(safe_claims) == len(narrative.verified_claims)
            or set(safe_claims) <= set(narrative.verified_claims),
            metadata={
                **narrative.metadata,
                "repaired_at": _now_iso(),
                "violations_resolved": len(violations),
            },
        )

    def batch_check(
        self,
        narratives: Sequence[PublicNarrative],
    ) -> dict[str, list[HonestyViolation]]:
        """Check a collection of narratives and return violations keyed by ID.

        Args:
            narratives: Sequence of ``PublicNarrative`` objects to check.

        Returns:
            Dict mapping ``narrative_id`` to the list of violations found.
        """
        return {n.narrative_id: self.check_narrative(n) for n in narratives}

    def _severity_weight(self, severity: str) -> float:
        """Return the numeric weight for a severity string.

        Args:
            severity: One of LOW, MEDIUM, HIGH, CRITICAL.

        Returns:
            Float weight from ``_SEVERITY_WEIGHTS`` (default 0.3 for unknown).
        """
        return _SEVERITY_WEIGHTS.get(severity, 0.3)


# ---------------------------------------------------------------------------
# §7  ThePublicStoryRemainAnalyzer
# ---------------------------------------------------------------------------


class ThePublicStoryRemainAnalyzer:
    """Analyzer for the public narrative honesty invariant.

    Provides higher-level analysis methods that combine the ``HonestyChecker``
    primitives into multi-narrative workflows.

    Theory reference: the analyzer implements the global section computation
    that determines whether the local honesty conditions (checked by
    ``HonestyChecker``) extend to a global honest narrative
    (theory2.tex §13.5, Theorem 13.5.3).
    """

    def __init__(self) -> None:
        """Initialise the analyzer with a shared ``HonestyChecker``."""
        self._checker = HonestyChecker()

    def analyze_narrative(
        self,
        narrative: PublicNarrative,
    ) -> tuple[list[HonestyViolation], float]:
        """Analyse a single narrative and return (violations, honesty_score).

        Args:
            narrative: The ``PublicNarrative`` to analyse.

        Returns:
            Tuple of (violations list, float honesty score).
        """
        violations = self._checker.check_narrative(narrative)
        score = self._checker.score_honesty(violations)
        return violations, score

    def analyze_claim_set(
        self,
        claims: Sequence[str],
        verified: Sequence[str],
    ) -> list[HonestyViolation]:
        """Analyse a raw set of claims against verified claims.

        Args:
            claims: All claims to check.
            verified: Claims supported by evidence.

        Returns:
            List of ``HonestyViolation`` objects.
        """
        return self._checker.detect_overclaims(claims, verified)

    def compute_honesty_score(
        self,
        narrative: PublicNarrative,
    ) -> float:
        """Compute the honesty score for a narrative.

        Args:
            narrative: The narrative to score.

        Returns:
            Float 0.0–1.0 honesty score.
        """
        violations = self._checker.check_narrative(narrative)
        return self._checker.score_honesty(violations)

    def compare_narratives(
        self,
        v1: PublicNarrative,
        v2: PublicNarrative,
    ) -> dict[str, JsonValue]:
        """Compare two narrative versions and report differences in honesty.

        Args:
            v1: First narrative (typically the older version).
            v2: Second narrative (typically the newer version).

        Returns:
            Dict with keys: improved, regressed, score_delta, new_violations.
        """
        viol1 = self._checker.check_narrative(v1)
        viol2 = self._checker.check_narrative(v2)
        score1 = self._checker.score_honesty(viol1)
        score2 = self._checker.score_honesty(viol2)
        claims1 = set(c.claim_text for c in viol1)
        claims2 = set(c.claim_text for c in viol2)
        return {
            "v1_id": v1.narrative_id,
            "v2_id": v2.narrative_id,
            "score_delta": round(score2 - score1, 4),
            "improved": score2 > score1,
            "regressed": score2 < score1,
            "new_violations": list(claims2 - claims1),
            "resolved_violations": list(claims1 - claims2),
        }

    def build_honesty_audit(
        self,
        narratives: Sequence[PublicNarrative],
    ) -> dict[str, JsonValue]:
        """Build a full honesty audit for a collection of narratives.

        Args:
            narratives: Sequence of ``PublicNarrative`` objects to audit.

        Returns:
            Dict with keys: total, honest, dishonest, avg_score,
            critical_violations, per_narrative.
        """
        if not narratives:
            return {
                "total": 0,
                "honest": 0,
                "dishonest": 0,
                "avg_score": 1.0,
                "critical_violations": 0,
                "per_narrative": {},
            }
        per: dict[str, Any] = {}
        total_score = 0.0
        honest_count = 0
        critical_count = 0
        for narr in narratives:
            violations, score = self.analyze_narrative(narr)
            per[narr.narrative_id] = {
                "score": round(score, 4),
                "violations": len(violations),
                "critical": sum(1 for v in violations if v.is_critical()),
            }
            total_score += score
            if score >= 1.0 and not violations:
                honest_count += 1
            critical_count += sum(1 for v in violations if v.is_critical())
        return {
            "total": len(narratives),
            "honest": honest_count,
            "dishonest": len(narratives) - honest_count,
            "avg_score": round(total_score / len(narratives), 4),
            "critical_violations": critical_count,
            "per_narrative": per,
        }


# ---------------------------------------------------------------------------
# §8  ThePublicStoryRemainCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThePublicStoryRemainCoordinator:
    """Top-level coordinator enforcing the public narrative honesty invariant.

    When ``zero_tolerance`` is True any violation — regardless of severity —
    generates an obstruction and blocks publication.  When ``auto_repair`` is
    True the coordinator applies the repair functor to all narratives that
    violate the invariant.

    Attributes:
        coordinator_id: Unique identifier.
        zero_tolerance: Require zero violations for acceptance.
        min_honesty_score: Minimum acceptable honesty score.
        auto_repair: Automatically apply the repair functor when violations
            are found.
        created_at: ISO-8601 creation timestamp.
    """

    coordinator_id: str
    zero_tolerance: bool = False
    min_honesty_score: float = 0.9
    auto_repair: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set ``created_at`` to the current time if not provided.

        Uses ``object.__setattr__`` because the dataclass is frozen.
        """
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def coordinate(
        self,
        narrative: PublicNarrative,
    ) -> tuple[PublicNarrative, list[HonestyViolation], list[dict[str, Any]]]:
        """Coordinate honesty validation for a single narrative.

        Checks the narrative, optionally repairs it, and returns obstructions
        for any remaining violations that exceed the policy threshold.

        Args:
            narrative: The ``PublicNarrative`` to process.

        Returns:
            Tuple of (possibly-repaired narrative, violations, obstructions).
        """
        checker = HonestyChecker()
        violations = checker.check_narrative(narrative)
        score = checker.score_honesty(violations)

        working = narrative
        if self.auto_repair and violations:
            working = checker.repair_narrative(narrative, violations)
            violations = checker.check_narrative(working)
            score = checker.score_honesty(violations)

        obstructions: list[dict[str, Any]] = []
        if self.zero_tolerance and violations:
            for v in violations:
                obstructions.append(self._violation_to_obstruction(v))
        elif score < self.min_honesty_score:
            for v in violations:
                obstructions.append(self._violation_to_obstruction(v))

        return working, violations, obstructions

    def coordinate_batch(
        self,
        narratives: Sequence[PublicNarrative],
    ) -> tuple[list[PublicNarrative], list[HonestyViolation], list[dict[str, Any]]]:
        """Coordinate honesty validation for multiple narratives.

        Args:
            narratives: Sequence of ``PublicNarrative`` objects to process.

        Returns:
            Tuple of (repaired narratives, all violations, all obstructions).
        """
        all_narratives: list[PublicNarrative] = []
        all_violations: list[HonestyViolation] = []
        all_obstructions: list[dict[str, Any]] = []
        for narr in narratives:
            repaired, violations, obstructions = self.coordinate(narr)
            all_narratives.append(repaired)
            all_violations.extend(violations)
            all_obstructions.extend(obstructions)
        return all_narratives, all_violations, all_obstructions

    def generate_honesty_report(
        self,
        violations: Sequence[HonestyViolation],
    ) -> dict[str, JsonValue]:
        """Produce a structured honesty report from a collection of violations.

        Args:
            violations: Sequence of ``HonestyViolation`` objects.

        Returns:
            Dict with keys: total, by_kind, by_severity, critical_count,
            score, repair_suggestions.
        """
        if not violations:
            return {
                "total": 0,
                "by_kind": {},
                "by_severity": {},
                "critical_count": 0,
                "score": 1.0,
                "repair_suggestions": [],
            }
        by_kind: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        for v in violations:
            by_kind[v.violation_kind] = by_kind.get(v.violation_kind, 0) + 1
            by_sev[v.severity] = by_sev.get(v.severity, 0) + 1

        checker = HonestyChecker()
        score = checker.score_honesty(violations)
        suggestions = list(
            {v.repair_suggestion for v in violations if v.repair_suggestion}
        )[:10]
        return {
            "total": len(violations),
            "by_kind": by_kind,
            "by_severity": by_sev,
            "critical_count": sum(1 for v in violations if v.is_critical()),
            "score": round(score, 4),
            "repair_suggestions": suggestions,
        }

    def _violation_to_obstruction(
        self,
        violation: HonestyViolation,
    ) -> dict[str, Any]:
        """Convert a ``HonestyViolation`` to an obstruction dict.

        Args:
            violation: The honesty violation to convert.

        Returns:
            Plain dict obstruction payload.
        """
        return violation.to_obstruction()


# ---------------------------------------------------------------------------
# §9  ThePublicStoryRemainWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThePublicStoryRemainWitness:
    """Top-level aggregate witness for narrative honesty across a session.

    Summarises the honesty state of all narratives processed by the
    coordinator.  Included in the public alignment manifest as the final
    honesty certificate.

    Attributes:
        witness_id: Unique identifier.
        narrative_ids: Tuple of IDs of all narratives processed.
        total_claims: Total number of claims across all narratives.
        honest_claims: Number of claims that passed honesty checks.
        violations_count: Total violations detected.
        critical_violations: Count of CRITICAL-severity violations.
        overall_honesty_score: Weighted average honesty score.
        is_acceptable: Whether the overall score meets the coordinator threshold.
        created_at: ISO-8601 timestamp.
        metadata: Arbitrary additional data.
    """

    witness_id: str
    narrative_ids: tuple[str, ...]
    total_claims: int
    honest_claims: int
    violations_count: int
    critical_violations: int
    overall_honesty_score: float
    is_acceptable: bool
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the witness to a JSON-compatible dict."""
        return {
            "witness_id": self.witness_id,
            "narrative_ids": list(self.narrative_ids),
            "total_claims": self.total_claims,
            "honest_claims": self.honest_claims,
            "violations_count": self.violations_count,
            "critical_violations": self.critical_violations,
            "overall_honesty_score": self.overall_honesty_score,
            "is_acceptable": self.is_acceptable,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThePublicStoryRemainWitness:
        """Reconstruct a ``ThePublicStoryRemainWitness`` from a plain dict."""
        return cls(
            witness_id=data.get("witness_id", _new_id("pswit")),
            narrative_ids=tuple(data.get("narrative_ids", [])),
            total_claims=int(data.get("total_claims", 0)),
            honest_claims=int(data.get("honest_claims", 0)),
            violations_count=int(data.get("violations_count", 0)),
            critical_violations=int(data.get("critical_violations", 0)),
            overall_honesty_score=float(data.get("overall_honesty_score", 0.0)),
            is_acceptable=bool(data.get("is_acceptable", False)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def grade(self) -> str:
        """Return a letter grade for the overall narrative honesty.

        Grades:
            A  — score ≥ 0.95, zero critical violations
            B  — score ≥ 0.80, zero critical violations
            C  — score ≥ 0.60
            D  — score ≥ 0.40
            F  — score < 0.40 or any critical violations
        """
        s = self.overall_honesty_score
        if self.critical_violations > 0 or s < 0.40:
            return "F"
        if s >= 0.95:
            return "A"
        if s >= 0.80:
            return "B"
        if s >= 0.60:
            return "C"
        return "D"

    def summary(self) -> str:
        """Return a one-line summary of the honesty witness.

        Includes witness ID, grade, honesty score, total violations, and
        critical violations for quick inspection.
        """
        return (
            f"witness={self.witness_id} "
            f"grade={self.grade()} "
            f"score={self.overall_honesty_score:.2f} "
            f"violations={self.violations_count} "
            f"critical={self.critical_violations} "
            f"acceptable={self.is_acceptable}"
        )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.evidence, jugeo.judgments)
# ---------------------------------------------------------------------------


def alignment_trust_check(claim: Any) -> dict[str, Any]:
    """Check trust alignment between a public claim and internal evidence.

    Trust checking verifies that the declared trust level of a public
    claim does not exceed the trust actually supported by evidence.

    Parameters
    ----------
    claim : Any
        A PublicClaim object or dict with claim data.

    Returns
    -------
    dict[str, Any]
        Trust check result with ``honest``, ``declared_trust``,
        ``actual_trust``, ``gap``, and ``trust_obj`` keys.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, compare_trust, compute_trust_gap
    except ImportError:
        TrustLevel = None
        compare_trust = None
        compute_trust_gap = None

    declared = getattr(claim, "declared_trust_level", None) or (
        claim.get("declared_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )
    actual = getattr(claim, "internal_trust_level", None) or (
        claim.get("internal_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )

    result: dict[str, Any] = {
        "declared_trust": str(declared),
        "actual_trust": str(actual),
        "honest": str(declared) <= str(actual),
        "gap": None,
        "trust_obj": None,
    }

    if compare_trust is not None:
        try:
            cmp = compare_trust(declared, actual)
            result["honest"] = getattr(cmp, "honest", result["honest"])
        except Exception:
            pass

    if compute_trust_gap is not None:
        try:
            result["gap"] = compute_trust_gap(declared, actual)
        except Exception:
            pass

    return result


def alignment_judgment(claim: Any, reality: Any) -> dict[str, Any]:
    """Construct a judgment comparing a public claim against internal reality.

    The alignment judgment captures the relationship between what is
    publicly stated and what the internal evidence actually supports.

    Parameters
    ----------
    claim : Any
        The public claim or documentation assertion.
    reality : Any
        The internal judgment, evidence, or code state.

    Returns
    -------
    dict[str, Any]
        Judgment record with ``aligned``, ``claim_summary``,
        ``reality_summary``, ``discrepancies``, and ``judgment_obj`` keys.
    """
    try:
        from jugeo.judgments import Judgment, build_comparison_judgment
    except ImportError:
        Judgment = None
        build_comparison_judgment = None

    claim_str = getattr(claim, "summary", None) or (
        claim.get("summary") if isinstance(claim, dict) else str(claim)[:120]
    )
    reality_str = getattr(reality, "summary", None) or (
        reality.get("summary") if isinstance(reality, dict) else str(reality)[:120]
    )

    judgment: dict[str, Any] = {
        "aligned": claim_str == reality_str,
        "claim_summary": claim_str,
        "reality_summary": reality_str,
        "discrepancies": [],
        "judgment_obj": None,
    }

    if build_comparison_judgment is not None:
        try:
            j = build_comparison_judgment(claim=claim, reality=reality)
            judgment["aligned"] = getattr(j, "aligned", judgment["aligned"])
            judgment["discrepancies"] = getattr(j, "discrepancies", [])
            judgment["judgment_obj"] = j
        except Exception:
            pass

    return judgment


def alignment_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for an alignment check result.

    The alignment certificate records whether a public claim was found
    to be honest, the evidence used, and the trust level.

    Parameters
    ----------
    result : Any
        An alignment check result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``aligned``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    aligned = getattr(result, "aligned", None) or getattr(result, "honest", None)
    if aligned is None and isinstance(result, dict):
        aligned = result.get("aligned", result.get("honest", False))

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "aligned": bool(aligned) if aligned is not None else False,
        "trust_level": "ALIGNED" if aligned else "MISALIGNED",
        "certificate_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim="public_alignment", satisfied=aligned, source="public_alignment"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# §10  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "JsonScalar",
    "JsonValue",
    "HonestyViolation",
    "PublicNarrative",
    "HonestyChecker",
    "ThePublicStoryRemainAnalyzer",
    "ThePublicStoryRemainCoordinator",
    "ThePublicStoryRemainWitness",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# ---------------------------------------------------------------------------
# §11  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== the_public_story_should_remain_hon smoke test ===")

    n1 = PublicNarrative(
        narrative_id=_new_id("narr"),
        title="System Capabilities v1",
        sections=("Overview", "Guarantees", "Limitations"),
        claims=(
            "The system processes requests in under 100ms.",
            "The system guarantees 99.99% uptime.",
            "The system never loses data.",
            "The system supports multi-region replication.",
        ),
        verified_claims=(
            "The system processes requests in under 100ms.",
            "The system supports multi-region replication.",
        ),
        trust_level=3,
        audience="developer",
        version="1.0.0",
        published_at=_now_iso(),
        is_honest=False,
    )
    print(f"  narrative: {n1.summary()}")
    print(f"  claim_count: {n1.claim_count()}")
    print(f"  verification_ratio: {n1.verification_ratio():.0%}")

    checker = HonestyChecker()
    violations = checker.check_narrative(n1)
    print(f"\n  violations detected: {len(violations)}")
    for v in violations:
        print(f"    [{v.severity}] {v.violation_kind}: {v.claim_text[:60]!r}")

    score = checker.score_honesty(violations)
    print(f"\n  honesty score: {score:.2f}")

    repaired = checker.repair_narrative(n1, violations)
    print(f"  repaired claims: {repaired.claims}")
    print(f"  repaired is_honest: {repaired.is_honest}")

    weakened = n1.weaken_to_honest()
    print(f"  weakened claims: {len(weakened.claims)} (was {len(n1.claims)})")

    coordinator = ThePublicStoryRemainCoordinator(
        coordinator_id=_new_id("coord"),
        zero_tolerance=False,
        min_honesty_score=0.8,
        auto_repair=True,
    )
    final, final_violations, obstructions = coordinator.coordinate(n1)
    print(f"\n  coordinated: violations={len(final_violations)} obstructions={len(obstructions)}")
    report = coordinator.generate_honesty_report(violations)
    print(f"  report: {report}")

    analyzer = ThePublicStoryRemainAnalyzer()
    audit = analyzer.build_honesty_audit([n1, repaired])
    print(f"\n  audit: {audit}")

    witness = ThePublicStoryRemainWitness(
        witness_id=_new_id("pswit"),
        narrative_ids=(n1.narrative_id,),
        total_claims=n1.claim_count(),
        honest_claims=len(n1.verified_claims),
        violations_count=len(violations),
        critical_violations=sum(1 for v in violations if v.is_critical()),
        overall_honesty_score=score,
        is_acceptable=score >= 0.8,
        created_at=_now_iso(),
    )
    print(f"\n  witness: {witness.summary()}")
    print(f"  grade: {witness.grade()}")
    print("=== smoke test passed ===")
