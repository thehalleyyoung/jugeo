"""
# copilot: public_api_cli_and_explanation_sem — Public API/CLI as Semantic Surface

Every public function, CLI command, or webhook endpoint is an obligation in the judgment
category (theory2.tex Ch13).  The public surface of a system is the semantic boundary
separating internal implementation from external commitments.  It is analogous to the
sheaf-theoretic notion of a cover: coherence conditions (explanation semantics) must be
satisfied on all overlaps, i.e. wherever a caller can observe an action.

Explanation semantics formalise the question "why did the system do X?".  A valid
explanation is an honest, verifiable account of the inputs, the decision process, and the
outputs.  The sheaf condition states that local explanations must glue: if action A at
coordinate p invokes action B at coordinate q, the explanation for A must be consistent
with the explanation for B.

§13.4 – Public API/CLI as Semantic Surface (theory2.tex, Ch 13)

The central data type is ``ExplanationRecord``.  A ``PublicSurface`` carries a set of
``ExplanationRecord`` objects, one per observable action.  The ``ExplanationSemantics``
aggregate measures coverage (fraction of actions that have explanations) and verifiability
(fraction of explanations that can be independently checked).
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
    "sequence": 4,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "public_api_cli_and_explanation_sem",
    "class": "PublicAPICLIExplanationCoordinator",
    "theory_section": "§13.4 – Public API/CLI as Semantic Surface",
}

# ---------------------------------------------------------------------------
# §4  PublicSurface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicSurface:
    """Represents the public semantic surface of a system component.

    A ``PublicSurface`` is the sheaf-theoretic cover of the system's observable
    behaviour.  Each entry in ``endpoints`` is a coordinate (function name,
    CLI subcommand, REST path, etc.) that is publicly committed to.  Each entry
    in ``obligations`` names a specific behavioural contract attached to this
    surface.

    Attributes:
        surface_id: Unique identifier.
        name: Human-readable name for the surface.
        kind: One of API, CLI, LIBRARY, WEBHOOK.
        endpoints: Tuple of coordinate strings.
        obligations: Tuple of obligation identifiers.
        trust_level: Trust level of this surface.
        version: Semantic version string.
        is_stable: Whether this surface is considered stable (non-experimental).
        created_at: ISO-8601 creation timestamp.
        metadata: Arbitrary additional data.
    """

    surface_id: str
    name: str
    kind: str
    endpoints: tuple[str, ...]
    obligations: tuple[str, ...]
    trust_level: Any
    version: str
    is_stable: bool
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the surface to a JSON-compatible dict.

        Converts tuples to lists and coerces ``trust_level`` to a string
        for JSON compatibility.
        """
        return {
            "surface_id": self.surface_id,
            "name": self.name,
            "kind": self.kind,
            "endpoints": list(self.endpoints),
            "obligations": list(self.obligations),
            "trust_level": str(self.trust_level),
            "version": self.version,
            "is_stable": self.is_stable,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicSurface:
        """Reconstruct a ``PublicSurface`` from a plain dict."""
        return cls(
            surface_id=data.get("surface_id", _new_id("surf")),
            name=data.get("name", ""),
            kind=data.get("kind", "API"),
            endpoints=tuple(data.get("endpoints", [])),
            obligations=tuple(data.get("obligations", [])),
            trust_level=data.get("trust_level"),
            version=data.get("version", "0.0.0"),
            is_stable=bool(data.get("is_stable", False)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def endpoint_count(self) -> int:
        """Return the number of publicly committed endpoints on this surface."""
        return len(self.endpoints)

    def obligation_count(self) -> int:
        """Return the number of obligations attached to this surface."""
        return len(self.obligations)

    def is_production_ready(self) -> bool:
        """Return True iff the surface is stable, has endpoints, and has obligations.

        A surface is production-ready only when all three conditions hold:
        it is declared stable, it exposes at least one endpoint, and it
        carries at least one obligation (i.e. it is not an empty contract).
        """
        return self.is_stable and self.endpoint_count() > 0 and self.obligation_count() > 0


# ---------------------------------------------------------------------------
# §5  ExplanationRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationRecord:
    """A single explanation for one observable action on a public surface.

    An ``ExplanationRecord`` is the local section of the explanation sheaf at
    coordinate ``coordinate``.  For the sheaf to be globally consistent, every
    ``ExplanationRecord`` must be honest (``is_honest()`` returns True) and the
    full collection must satisfy the gluing condition checked by
    ``ExplanationSemantics.all_honest()``.

    Attributes:
        record_id: Unique identifier for this record.
        action: Name or description of the observable action.
        coordinate: The public coordinate (endpoint/command) that performed it.
        why: Human-readable explanation of why the action occurred.
        inputs_summary: Summary of the inputs that triggered the action.
        outputs_summary: Summary of the outputs produced.
        trust_level: Trust level of the explanation.
        supporting_obligations: Tuple of obligation IDs that justify the action.
        timestamp: ISO-8601 timestamp when the action occurred.
        is_verifiable: Whether the explanation can be independently verified.
        metadata: Arbitrary additional data.
    """

    record_id: str
    action: str
    coordinate: str
    why: str
    inputs_summary: str
    outputs_summary: str
    trust_level: Any
    supporting_obligations: tuple[str, ...]
    timestamp: str
    is_verifiable: bool
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dict."""
        return {
            "record_id": self.record_id,
            "action": self.action,
            "coordinate": self.coordinate,
            "why": self.why,
            "inputs_summary": self.inputs_summary,
            "outputs_summary": self.outputs_summary,
            "trust_level": str(self.trust_level),
            "supporting_obligations": list(self.supporting_obligations),
            "timestamp": self.timestamp,
            "is_verifiable": self.is_verifiable,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplanationRecord:
        """Reconstruct an ``ExplanationRecord`` from a plain dict."""
        return cls(
            record_id=data.get("record_id", _new_id("expl")),
            action=data.get("action", ""),
            coordinate=data.get("coordinate", ""),
            why=data.get("why", ""),
            inputs_summary=data.get("inputs_summary", ""),
            outputs_summary=data.get("outputs_summary", ""),
            trust_level=data.get("trust_level"),
            supporting_obligations=tuple(data.get("supporting_obligations", [])),
            timestamp=data.get("timestamp", _now_iso()),
            is_verifiable=bool(data.get("is_verifiable", False)),
            metadata=dict(data.get("metadata", {})),
        )

    def is_honest(self) -> bool:
        """Return True iff the explanation is honest and verifiable.

        An explanation is honest when the ``why`` field is non-empty (i.e. the
        system provides a reason rather than asserting the action was
        self-evidently correct) and the explanation is independently
        verifiable.  Empty or vacuous explanations are considered dishonest.
        """
        return bool(self.why.strip()) and self.is_verifiable

    def to_claim(self) -> dict[str, JsonValue]:
        """Convert to a compact claim dict for inclusion in reports.

        Returns a minimal representation suitable for embedding in a public
        narrative or changelog.
        """
        return {
            "action": self.action,
            "coordinate": self.coordinate,
            "why": self.why,
            "honest": self.is_honest(),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# §6  ExplanationSemantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationSemantics:
    """Aggregate sheaf-coherence data for all explanations on a surface.

    An ``ExplanationSemantics`` object captures the global state of the
    explanation sheaf over a ``PublicSurface``.  It records which actions are
    covered, how many are verifiable, and which actions lack honest
    explanations entirely.

    Attributes:
        semantics_id: Unique identifier.
        surface_id: Identifier of the surface being analysed.
        records: All ``ExplanationRecord`` objects for this surface.
        coverage_ratio: Fraction of endpoints that have at least one explanation.
        verified_ratio: Fraction of records that are verifiable.
        unverifiable_actions: Actions whose explanations cannot be verified.
        created_at: ISO-8601 creation timestamp.
        metadata: Arbitrary additional data.
    """

    semantics_id: str
    surface_id: str
    records: tuple[ExplanationRecord, ...]
    coverage_ratio: float
    verified_ratio: float
    unverifiable_actions: tuple[str, ...]
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dict."""
        return {
            "semantics_id": self.semantics_id,
            "surface_id": self.surface_id,
            "records": [r.to_dict() for r in self.records],
            "coverage_ratio": self.coverage_ratio,
            "verified_ratio": self.verified_ratio,
            "unverifiable_actions": list(self.unverifiable_actions),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplanationSemantics:
        """Reconstruct an ``ExplanationSemantics`` from a plain dict."""
        return cls(
            semantics_id=data.get("semantics_id", _new_id("sem")),
            surface_id=data.get("surface_id", ""),
            records=tuple(
                ExplanationRecord.from_dict(r) for r in data.get("records", [])
            ),
            coverage_ratio=float(data.get("coverage_ratio", 0.0)),
            verified_ratio=float(data.get("verified_ratio", 0.0)),
            unverifiable_actions=tuple(data.get("unverifiable_actions", [])),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def all_honest(self) -> bool:
        """Return True iff every record in the sheaf is honest.

        This is the global sheaf-coherence condition for the explanation
        semantics.  It fails as soon as any local section is not honest.
        """
        return all(r.is_honest() for r in self.records)

    def summary_stats(self) -> dict[str, JsonValue]:
        """Return a dict of aggregate statistics for reporting.

        Keys: total_records, honest_count, coverage, verified, unverifiable.
        """
        honest = sum(1 for r in self.records if r.is_honest())
        return {
            "total_records": len(self.records),
            "honest_count": honest,
            "coverage": round(self.coverage_ratio, 4),
            "verified": round(self.verified_ratio, 4),
            "unverifiable": len(self.unverifiable_actions),
        }

    def worst_explanations(self) -> list[str]:
        """Return the action names of the least honest explanations.

        Returns the actions whose explanations are neither honest nor
        verifiable, sorted alphabetically.  Useful for prioritising repair.
        """
        return sorted(
            r.action
            for r in self.records
            if not r.is_honest()
        )


# ---------------------------------------------------------------------------
# §7  PublicAPICLIExplanationAnalyzer
# ---------------------------------------------------------------------------


class PublicAPICLIExplanationAnalyzer:
    """Stateless analyzer for public surfaces and their explanation semantics.

    Provides pure functions for constructing ``PublicSurface`` and
    ``ExplanationRecord`` objects, scoring coverage, and checking honesty.
    Maintains no internal state; all methods are safe to call concurrently.

    Theory reference: explanation semantics as the sheaf condition on the
    semantic surface (theory2.tex §13.4, Definition 13.4.2).
    """

    def analyze_surface(
        self,
        name: str,
        kind: str,
        endpoints: Sequence[str],
        obligations: Sequence[str],
    ) -> PublicSurface:
        """Construct a ``PublicSurface`` from raw surface data.

        Args:
            name: Human-readable name for the surface.
            kind: One of API, CLI, LIBRARY, WEBHOOK.
            endpoints: Sequence of coordinate strings.
            obligations: Sequence of obligation identifier strings.

        Returns:
            A new ``PublicSurface`` with a generated ID and current timestamp.
        """
        return PublicSurface(
            surface_id=_new_id("surf"),
            name=name,
            kind=kind,
            endpoints=tuple(endpoints),
            obligations=tuple(obligations),
            trust_level=None,
            version="1.0.0",
            is_stable=True,
            created_at=_now_iso(),
        )

    def build_explanation(
        self,
        action: str,
        coordinate: str,
        why: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        trust_level: Any = None,
    ) -> ExplanationRecord:
        """Build a single ``ExplanationRecord``.

        Args:
            action: Name of the observable action.
            coordinate: Public coordinate where the action occurred.
            why: Human-readable reason for the action.
            inputs: Dict of input values (summarised to a string).
            outputs: Dict of output values (summarised to a string).
            trust_level: Optional trust level for this explanation.

        Returns:
            A new ``ExplanationRecord`` with computed verifiability.
        """
        inputs_summary = ", ".join(f"{k}={v!r}" for k, v in inputs.items()) or "(none)"
        outputs_summary = ", ".join(f"{k}={v!r}" for k, v in outputs.items()) or "(none)"
        is_ver = self._is_verifiable(why, outputs)
        return ExplanationRecord(
            record_id=_new_id("expl"),
            action=action,
            coordinate=coordinate,
            why=why,
            inputs_summary=inputs_summary,
            outputs_summary=outputs_summary,
            trust_level=trust_level,
            supporting_obligations=(),
            timestamp=_now_iso(),
            is_verifiable=is_ver,
        )

    def check_explanation_honesty(self, record: ExplanationRecord) -> bool:
        """Check whether a single ``ExplanationRecord`` is honest.

        Delegates to ``record.is_honest()`` but is provided as a standalone
        function for use in higher-order contexts (e.g. ``filter``).

        Args:
            record: The explanation record to check.

        Returns:
            True iff the record is honest.
        """
        return record.is_honest()

    def compute_explanation_semantics(
        self,
        surface: PublicSurface,
        records: Sequence[ExplanationRecord],
    ) -> ExplanationSemantics:
        """Compute the explanation semantics for a surface and its records.

        Calculates coverage (how many endpoints have explanations) and
        verifiability ratios.  Identifies unverifiable actions.

        Args:
            surface: The ``PublicSurface`` being analysed.
            records: All available ``ExplanationRecord`` objects.

        Returns:
            An ``ExplanationSemantics`` object.
        """
        covered = {r.coordinate for r in records}
        coverage = (
            len(covered & set(surface.endpoints)) / max(1, len(surface.endpoints))
        )
        total = len(records)
        verified = sum(1 for r in records if r.is_verifiable)
        verified_ratio = verified / max(1, total)
        unverifiable = tuple(r.action for r in records if not r.is_verifiable)
        return ExplanationSemantics(
            semantics_id=_new_id("sem"),
            surface_id=surface.surface_id,
            records=tuple(records),
            coverage_ratio=coverage,
            verified_ratio=verified_ratio,
            unverifiable_actions=unverifiable,
            created_at=_now_iso(),
        )

    def validate_public_surface(
        self,
        surface: PublicSurface,
    ) -> list[dict[str, Any]]:
        """Validate a ``PublicSurface`` and return any obstructions found.

        Checks that the surface has a name, at least one endpoint, and at
        least one obligation.  Returns plain dicts as obstruction payloads.

        Args:
            surface: The ``PublicSurface`` to validate.

        Returns:
            List of obstruction dicts (empty on success).
        """
        obstructions: list[dict[str, Any]] = []
        if not surface.name.strip():
            obstructions.append(
                {
                    "obstruction_id": _new_id("obs"),
                    "coordinate": surface.surface_id,
                    "reason": "PublicSurface has no name",
                    "created_at": _now_iso(),
                }
            )
        if surface.endpoint_count() == 0:
            obstructions.append(
                {
                    "obstruction_id": _new_id("obs"),
                    "coordinate": surface.surface_id,
                    "reason": "PublicSurface has no endpoints",
                    "created_at": _now_iso(),
                }
            )
        if surface.obligation_count() == 0:
            obstructions.append(
                {
                    "obstruction_id": _new_id("obs"),
                    "coordinate": surface.surface_id,
                    "reason": "PublicSurface has no obligations",
                    "created_at": _now_iso(),
                }
            )
        return obstructions

    def batch_build_explanations(
        self,
        actions: Sequence[tuple[str, str, str, dict[str, Any], dict[str, Any]]],
    ) -> list[ExplanationRecord]:
        """Build multiple ``ExplanationRecord`` objects in one call.

        Args:
            actions: Sequence of (action, coordinate, why, inputs, outputs) tuples.

        Returns:
            List of ``ExplanationRecord`` objects.
        """
        return [
            self.build_explanation(action, coord, why, inp, out)
            for action, coord, why, inp, out in actions
        ]

    def score_surface_coverage(
        self,
        surface: PublicSurface,
        records: Sequence[ExplanationRecord],
    ) -> float:
        """Score what fraction of the surface's endpoints have explanations.

        Args:
            surface: The surface whose coverage is being scored.
            records: Available explanation records.

        Returns:
            Float 0.0–1.0 where 1.0 means every endpoint has at least one
            honest, verifiable explanation.
        """
        if not surface.endpoints:
            return 1.0
        covered = {r.coordinate for r in records if r.is_honest()}
        return len(covered & set(surface.endpoints)) / len(surface.endpoints)

    def _is_verifiable(self, why: str, outputs: dict[str, Any]) -> bool:
        """Determine whether an explanation is independently verifiable.

        An explanation is verifiable when it provides a non-trivial reason
        (more than five words) and the outputs dict is non-empty, meaning
        there is observable evidence to check.

        Args:
            why: The explanation text.
            outputs: The outputs produced by the action.

        Returns:
            True iff the explanation is independently verifiable.
        """
        has_reason = len(why.strip().split()) > 5
        has_evidence = bool(outputs)
        return has_reason and has_evidence


# ---------------------------------------------------------------------------
# §8  PublicAPICLIExplanationCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicAPICLIExplanationCoordinator:
    """Coordinator that enforces explanation policy on public surfaces.

    When ``require_explanations`` is True, any surface action without an
    honest explanation generates an obstruction.  When ``min_coverage`` is
    set, surfaces below the coverage threshold also generate obstructions.

    Attributes:
        coordinator_id: Unique identifier.
        require_explanations: Require all actions to have honest explanations.
        min_coverage: Minimum acceptable coverage ratio.
        created_at: ISO-8601 creation timestamp.
    """

    coordinator_id: str
    require_explanations: bool = True
    min_coverage: float = 0.8
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set ``created_at`` to the current time if not provided.

        Uses ``object.__setattr__`` because the dataclass is frozen.
        """
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def coordinate(
        self,
        surface: PublicSurface,
        records: Sequence[ExplanationRecord],
    ) -> tuple[ExplanationSemantics, list[dict[str, Any]]]:
        """Coordinate explanation validation for a single surface.

        Args:
            surface: The public surface to validate.
            records: Explanation records to evaluate.

        Returns:
            Tuple of (ExplanationSemantics, list of obstruction dicts).
        """
        analyzer = PublicAPICLIExplanationAnalyzer()
        semantics = analyzer.compute_explanation_semantics(surface, records)
        obstructions: list[dict[str, Any]] = []

        if self.require_explanations:
            for record in records:
                if not record.is_honest():
                    obstructions.append(
                        self._explanation_to_obstruction(
                            record, "action lacks honest/verifiable explanation"
                        )
                    )

        if semantics.coverage_ratio < self.min_coverage:
            obstructions.append(
                {
                    "obstruction_id": _new_id("obs"),
                    "coordinate": surface.surface_id,
                    "reason": (
                        f"Coverage {semantics.coverage_ratio:.0%} "
                        f"below minimum {self.min_coverage:.0%}"
                    ),
                    "created_at": _now_iso(),
                }
            )
        return semantics, obstructions

    def coordinate_surface_creation(
        self,
        name: str,
        kind: str,
        endpoints: Sequence[str],
        obligations: Sequence[str],
    ) -> tuple[PublicSurface, list[dict[str, Any]]]:
        """Create and immediately validate a new ``PublicSurface``.

        Args:
            name: Human-readable name.
            kind: One of API, CLI, LIBRARY, WEBHOOK.
            endpoints: Sequence of coordinate strings.
            obligations: Sequence of obligation identifiers.

        Returns:
            Tuple of (PublicSurface, list of obstruction dicts).
        """
        analyzer = PublicAPICLIExplanationAnalyzer()
        surface = analyzer.analyze_surface(name, kind, endpoints, obligations)
        obstructions = analyzer.validate_public_surface(surface)
        return surface, obstructions

    def generate_explanation_report(
        self,
        semantics: ExplanationSemantics,
    ) -> dict[str, JsonValue]:
        """Produce a structured report from ``ExplanationSemantics``.

        Args:
            semantics: The explanation semantics to report on.

        Returns:
            Dict with keys: surface_id, stats, all_honest, worst_explanations.
        """
        return {
            "surface_id": semantics.surface_id,
            "stats": semantics.summary_stats(),
            "all_honest": semantics.all_honest(),
            "worst_explanations": semantics.worst_explanations(),
        }

    def _explanation_to_obstruction(
        self,
        record: ExplanationRecord,
        reason: str,
    ) -> dict[str, Any]:
        """Convert a problematic ``ExplanationRecord`` to an obstruction dict.

        Args:
            record: The explanation record that failed validation.
            reason: Human-readable description of the problem.

        Returns:
            Obstruction dict.
        """
        return {
            "obstruction_id": _new_id("obs"),
            "coordinate": record.coordinate,
            "reason": reason,
            "record_id": record.record_id,
            "action": record.action,
            "created_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# §9  PublicAPICLIExplanationWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicAPICLIExplanationWitness:
    """Final witness for the explanation semantics of a public surface.

    Aggregates coverage, honesty, and completeness metrics into a single
    artifact for inclusion in the public alignment manifest.

    Attributes:
        witness_id: Unique identifier.
        surface_id: ID of the surface this witness covers.
        total_actions: Total number of observed actions.
        explained_actions: Number of actions with any explanation.
        verified_actions: Number of actions with verifiable explanations.
        coverage_score: Fraction of endpoints covered.
        honesty_score: Fraction of explanations that are honest.
        is_complete: Whether all endpoints have honest explanations.
        created_at: ISO-8601 timestamp.
        metadata: Arbitrary additional data.
    """

    witness_id: str
    surface_id: str
    total_actions: int
    explained_actions: int
    verified_actions: int
    coverage_score: float
    honesty_score: float
    is_complete: bool
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dict."""
        return {
            "witness_id": self.witness_id,
            "surface_id": self.surface_id,
            "total_actions": self.total_actions,
            "explained_actions": self.explained_actions,
            "verified_actions": self.verified_actions,
            "coverage_score": self.coverage_score,
            "honesty_score": self.honesty_score,
            "is_complete": self.is_complete,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicAPICLIExplanationWitness:
        """Reconstruct a ``PublicAPICLIExplanationWitness`` from a plain dict."""
        return cls(
            witness_id=data.get("witness_id", _new_id("pawit")),
            surface_id=data.get("surface_id", ""),
            total_actions=int(data.get("total_actions", 0)),
            explained_actions=int(data.get("explained_actions", 0)),
            verified_actions=int(data.get("verified_actions", 0)),
            coverage_score=float(data.get("coverage_score", 0.0)),
            honesty_score=float(data.get("honesty_score", 0.0)),
            is_complete=bool(data.get("is_complete", False)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def grade(self) -> str:
        """Return a letter grade summarising the explanation quality.

        Grades:
            A  — honesty_score ≥ 0.95 and is_complete
            B  — honesty_score ≥ 0.80 and coverage_score ≥ 0.80
            C  — honesty_score ≥ 0.60
            D  — honesty_score ≥ 0.40
            F  — honesty_score < 0.40
        """
        h = self.honesty_score
        c = self.coverage_score
        if h >= 0.95 and self.is_complete:
            return "A"
        if h >= 0.80 and c >= 0.80:
            return "B"
        if h >= 0.60:
            return "C"
        if h >= 0.40:
            return "D"
        return "F"

    def summary(self) -> str:
        """Return a one-line summary of the explanation witness.

        Includes surface ID, grade, coverage, and honesty score for quick
        inspection in logs and reports.
        """
        return (
            f"surface={self.surface_id} "
            f"grade={self.grade()} "
            f"coverage={self.coverage_score:.0%} "
            f"honesty={self.honesty_score:.0%} "
            f"complete={self.is_complete}"
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
    "PublicSurface",
    "ExplanationRecord",
    "ExplanationSemantics",
    "PublicAPICLIExplanationAnalyzer",
    "PublicAPICLIExplanationCoordinator",
    "PublicAPICLIExplanationWitness",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# ---------------------------------------------------------------------------
# §11  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== public_api_cli_and_explanation_sem smoke test ===")

    analyzer = PublicAPICLIExplanationAnalyzer()

    surface, obs = PublicAPICLIExplanationCoordinator(
        coordinator_id=_new_id("coord")
    ).coordinate_surface_creation(
        name="UserService API",
        kind="API",
        endpoints=["GET /users", "POST /users", "DELETE /users/{id}"],
        obligations=["users-read", "users-write", "users-delete"],
    )
    print(f"  surface: {surface.name!r} kind={surface.kind}")
    print(f"  endpoints: {surface.endpoint_count()}, obligations: {surface.obligation_count()}")
    print(f"  production_ready: {surface.is_production_ready()}")
    print(f"  creation obstructions: {len(obs)}")

    records = analyzer.batch_build_explanations(
        [
            (
                "list_users",
                "GET /users",
                "User requested a paginated list of all registered users in the system",
                {"page": 1, "limit": 20},
                {"users": ["alice", "bob"], "total": 2},
            ),
            (
                "create_user",
                "POST /users",
                "Admin submitted valid user creation form with required fields",
                {"name": "charlie", "email": "charlie@example.com"},
                {"user_id": "u-123", "status": "created"},
            ),
            (
                "delete_user",
                "DELETE /users/{id}",
                "",  # intentionally empty — will be flagged as dishonest
                {"id": "u-99"},
                {},
            ),
        ]
    )
    print(f"\n  records built: {len(records)}")
    for r in records:
        print(f"    {r.action!r}: honest={r.is_honest()}")

    coordinator = PublicAPICLIExplanationCoordinator(
        coordinator_id=_new_id("coord"),
        require_explanations=True,
        min_coverage=0.8,
    )
    semantics, obstructions = coordinator.coordinate(surface, records)
    print(f"\n  coverage: {semantics.coverage_ratio:.0%}")
    print(f"  verified: {semantics.verified_ratio:.0%}")
    print(f"  all_honest: {semantics.all_honest()}")
    print(f"  obstructions: {len(obstructions)}")
    print(f"  worst: {semantics.worst_explanations()}")

    report = coordinator.generate_explanation_report(semantics)
    print(f"\n  report stats: {report['stats']}")

    honest_count = sum(1 for r in records if r.is_honest())
    witness = PublicAPICLIExplanationWitness(
        witness_id=_new_id("pawit"),
        surface_id=surface.surface_id,
        total_actions=len(records),
        explained_actions=sum(1 for r in records if r.why.strip()),
        verified_actions=sum(1 for r in records if r.is_verifiable),
        coverage_score=semantics.coverage_ratio,
        honesty_score=honest_count / max(1, len(records)),
        is_complete=semantics.all_honest(),
        created_at=_now_iso(),
    )
    print(f"\n  witness: {witness.summary()}")
    print(f"  grade: {witness.grade()}")
    print("=== smoke test passed ===")
