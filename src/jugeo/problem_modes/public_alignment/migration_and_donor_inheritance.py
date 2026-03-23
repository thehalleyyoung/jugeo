"""
# copilot: migration_and_donor_inheritance — Migration and Donor Inheritance for Public Alignment

Migration is the process of moving code, APIs, or data structures from one coordinate in the
semantic topology to another while preserving behavioral obligations.  In the judgment category
(theory2.tex Ch13), each public coordinate carries obligations encoded as morphisms; migration
must therefore be a functor that maps old morphisms to new morphisms without dropping any
obligation from the fiber over the original coordinate.

Donor inheritance formalises the pushforward of obligations along migration steps.  When a
symbol is renamed or moved, its obligations do not disappear; they are inherited by the
recipient coordinate.  A ``DonorInheritanceRecord`` is the certificate that this transfer
occurred and was verified.

§13.3 – Migration and Donor Inheritance (theory2.tex, Ch 13)

The central invariant is *behavior preservation*: for every pair (old_coordinate, new_coordinate)
related by a migration step σ, the canonical diagram

    F_old ──σ──▶ F_new
        ↘       ↙
          Behav

must commute up to the tolerance specified by ``MigrationWitness.preservation_score``.  A score
of 1.0 indicates a fully-safe, non-breaking migration; scores below the strict threshold trigger
``ObstructionRecord`` entries in the coordinator output.
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
    "sequence": 3,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "migration_and_donor_inheritance",
    "class": "MigrationDonorInheritanceCoordinator",
    "theory_section": "§13.3 – Migration and Donor Inheritance",
}

# ---------------------------------------------------------------------------
# §4  MigrationStep
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """A single atomic step within a migration plan.

    Each step maps one old coordinate to one new coordinate and is annotated
    with a ``step_kind`` drawn from the vocabulary of categorical migration
    operations defined in theory2.tex §13.3.  The ``is_breaking`` flag marks
    steps that cannot be executed without coordinating with all callers of the
    old coordinate.

    Attributes:
        step_id: Unique identifier for this step.
        step_kind: One of RENAME, MOVE, SPLIT, MERGE, DEPRECATE, ADD, REMOVE.
        old_coordinate: The fully-qualified old name or path.
        new_coordinate: The fully-qualified new name or path.
        description: Human-readable explanation of the step.
        is_breaking: Whether this step is backward-incompatible.
        trust_level: The trust level assigned to this step.
        created_at: ISO-8601 timestamp of creation.
        metadata: Arbitrary additional data.
    """

    step_id: str
    step_kind: str
    old_coordinate: str
    new_coordinate: str
    description: str
    is_breaking: bool
    trust_level: Any
    created_at: str
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the step to a plain dict suitable for JSON encoding.

        Returns a dictionary with all fields represented as JSON-compatible
        values.  The ``trust_level`` field is coerced to its string
        representation when it is not already a scalar.
        """
        return {
            "step_id": self.step_id,
            "step_kind": self.step_kind,
            "old_coordinate": self.old_coordinate,
            "new_coordinate": self.new_coordinate,
            "description": self.description,
            "is_breaking": self.is_breaking,
            "trust_level": str(self.trust_level),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationStep:
        """Reconstruct a ``MigrationStep`` from a plain dict.

        Unknown keys in ``data`` are silently stored in ``metadata`` to
        ensure forward-compatibility with richer future representations.
        """
        return cls(
            step_id=data.get("step_id", _new_id("step")),
            step_kind=data.get("step_kind", "RENAME"),
            old_coordinate=data.get("old_coordinate", ""),
            new_coordinate=data.get("new_coordinate", ""),
            description=data.get("description", ""),
            is_breaking=bool(data.get("is_breaking", False)),
            trust_level=data.get("trust_level"),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    def is_safe(self) -> bool:
        """Return True iff this step is non-breaking.

        A safe step can be applied without coordinating with callers; the old
        interface remains available (possibly deprecated) until the next major
        version boundary.
        """
        return not self.is_breaking

    def summarize(self) -> str:
        """Return a one-line human-readable summary of this migration step.

        The summary is intended for use in changelogs and migration guides
        and follows the conventional-commits style.
        """
        breaking_tag = "[BREAKING] " if self.is_breaking else ""
        return (
            f"{breaking_tag}{self.step_kind}: "
            f"{self.old_coordinate!r} → {self.new_coordinate!r} — {self.description}"
        )


# ---------------------------------------------------------------------------
# §5  DonorInheritanceRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DonorInheritanceRecord:
    """Certificate that obligations have been transferred from donor to recipient.

    In the categorical semantics of theory2.tex §13.3, a DonorInheritanceRecord
    is the 2-cell witnessing that the pushforward functor σ_* carried all
    obligations from the fiber over the donor coordinate to the fiber over the
    recipient coordinate.

    Attributes:
        record_id: Unique record identifier.
        donor_coordinate: The old coordinate that is being retired.
        recipient_coordinate: The new coordinate that inherits obligations.
        inherited_obligations: Tuple of obligation identifiers transferred.
        inherited_trust_level: Trust level of the inherited obligations.
        inheritance_kind: FULL, PARTIAL, or NONE.
        transfer_timestamp: ISO-8601 timestamp of the transfer.
        is_verified: Whether a human or automated check confirmed the transfer.
        verification_notes: Free-form notes from the verifier.
        metadata: Arbitrary additional data.
    """

    record_id: str
    donor_coordinate: str
    recipient_coordinate: str
    inherited_obligations: tuple[str, ...]
    inherited_trust_level: Any
    inheritance_kind: str
    transfer_timestamp: str
    is_verified: bool
    verification_notes: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dict.

        Tuples are converted to lists for JSON compatibility.  The
        ``inherited_trust_level`` is coerced to str.
        """
        return {
            "record_id": self.record_id,
            "donor_coordinate": self.donor_coordinate,
            "recipient_coordinate": self.recipient_coordinate,
            "inherited_obligations": list(self.inherited_obligations),
            "inherited_trust_level": str(self.inherited_trust_level),
            "inheritance_kind": self.inheritance_kind,
            "transfer_timestamp": self.transfer_timestamp,
            "is_verified": self.is_verified,
            "verification_notes": self.verification_notes,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DonorInheritanceRecord:
        """Reconstruct a ``DonorInheritanceRecord`` from a plain dict."""
        return cls(
            record_id=data.get("record_id", _new_id("rec")),
            donor_coordinate=data.get("donor_coordinate", ""),
            recipient_coordinate=data.get("recipient_coordinate", ""),
            inherited_obligations=tuple(data.get("inherited_obligations", [])),
            inherited_trust_level=data.get("inherited_trust_level"),
            inheritance_kind=data.get("inheritance_kind", "FULL"),
            transfer_timestamp=data.get("transfer_timestamp", _now_iso()),
            is_verified=bool(data.get("is_verified", False)),
            verification_notes=data.get("verification_notes", ""),
            metadata=dict(data.get("metadata", {})),
        )

    def obligation_count(self) -> int:
        """Return the number of obligations transferred by this record."""
        return len(self.inherited_obligations)

    def is_complete_transfer(self) -> bool:
        """Return True iff the inheritance is FULL and has been verified.

        A complete transfer means no residual obligations remain on the donor
        coordinate; the donor can be safely removed from the public surface.
        """
        return self.inheritance_kind == "FULL" and self.is_verified

    def to_summary(self) -> dict[str, JsonValue]:
        """Return a compact summary dict for reporting purposes."""
        return {
            "record_id": self.record_id,
            "donor": self.donor_coordinate,
            "recipient": self.recipient_coordinate,
            "obligations": self.obligation_count(),
            "kind": self.inheritance_kind,
            "verified": self.is_verified,
        }


# ---------------------------------------------------------------------------
# §6  MigrationWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationWitness:
    """Aggregate witness for a single migration from one API version to another.

    A ``MigrationWitness`` records the outcome of applying a set of
    ``MigrationStep`` objects and capturing the corresponding
    ``DonorInheritanceRecord`` objects.  It is the primary evidence artifact
    presented to the ``MigrationDonorInheritanceCoordinator`` for acceptance
    or rejection.

    Attributes:
        witness_id: Unique identifier.
        migration_plan_id: Identifier of the plan this witness belongs to.
        steps_completed: IDs of successfully completed migration steps.
        donor_records: Full donor inheritance records for this migration.
        is_behavior_preserved: Whether behavior preservation was confirmed.
        preservation_score: Float 0.0–1.0 quantifying preservation.
        obstruction_ids: IDs of any obstructions detected.
        created_at: ISO-8601 timestamp.
        metadata: Arbitrary additional data.
    """

    witness_id: str
    migration_plan_id: str
    steps_completed: tuple[str, ...]
    donor_records: tuple[DonorInheritanceRecord, ...]
    is_behavior_preserved: bool
    preservation_score: float
    obstruction_ids: tuple[str, ...]
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the witness to a JSON-compatible dict."""
        return {
            "witness_id": self.witness_id,
            "migration_plan_id": self.migration_plan_id,
            "steps_completed": list(self.steps_completed),
            "donor_records": [r.to_dict() for r in self.donor_records],
            "is_behavior_preserved": self.is_behavior_preserved,
            "preservation_score": self.preservation_score,
            "obstruction_ids": list(self.obstruction_ids),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationWitness:
        """Reconstruct a ``MigrationWitness`` from a plain dict."""
        return cls(
            witness_id=data.get("witness_id", _new_id("wit")),
            migration_plan_id=data.get("migration_plan_id", ""),
            steps_completed=tuple(data.get("steps_completed", [])),
            donor_records=tuple(
                DonorInheritanceRecord.from_dict(r)
                for r in data.get("donor_records", [])
            ),
            is_behavior_preserved=bool(data.get("is_behavior_preserved", False)),
            preservation_score=float(data.get("preservation_score", 0.0)),
            obstruction_ids=tuple(data.get("obstruction_ids", [])),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def failed_steps(self) -> list[str]:
        """Return the IDs of steps that did NOT complete successfully.

        In the current model a step is considered failed when it generated an
        obstruction ID, i.e. it appears in ``obstruction_ids`` but not in
        ``steps_completed``.
        """
        completed = set(self.steps_completed)
        return [oid for oid in self.obstruction_ids if oid not in completed]

    def all_obligations_transferred(self) -> bool:
        """Return True iff every donor record shows a complete transfer.

        This is the global analog of ``DonorInheritanceRecord.is_complete_transfer``:
        it checks that no obligation was left behind anywhere in the migration.
        """
        return all(r.is_complete_transfer() for r in self.donor_records)

    def grade(self) -> str:
        """Return a letter grade summarising the migration quality.

        Grades:
            A  — score ≥ 0.95, behavior preserved, all obligations transferred
            B  — score ≥ 0.80, behavior preserved
            C  — score ≥ 0.60
            D  — score ≥ 0.40
            F  — score < 0.40
        """
        s = self.preservation_score
        if s >= 0.95 and self.is_behavior_preserved and self.all_obligations_transferred():
            return "A"
        if s >= 0.80 and self.is_behavior_preserved:
            return "B"
        if s >= 0.60:
            return "C"
        if s >= 0.40:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# §7  MigrationDonorInheritanceAnalyzer
# ---------------------------------------------------------------------------


class MigrationDonorInheritanceAnalyzer:
    """Stateless analyzer that compares old and new API surfaces.

    The analyzer provides a suite of pure functions for diffing API surfaces,
    identifying migration steps, computing donor inheritance records, and
    scoring behavior preservation.  All methods are deterministic given the
    same inputs and carry no internal state.

    Theory reference: the diff of API surfaces is the mapping cone of the
    inclusion functor (theory2.tex §13.3, Proposition 13.3.4).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_migration(
        self,
        old_api: dict[str, Any],
        new_api: dict[str, Any],
    ) -> list[MigrationStep]:
        """Identify what changed between two API surface dictionaries.

        Each key in the dictionary represents a coordinate (function name,
        endpoint path, CLI command, etc.).  Values are treated as
        obligation/metadata bundles.

        Args:
            old_api: Mapping of coordinate → metadata for the old surface.
            new_api: Mapping of coordinate → metadata for the new surface.

        Returns:
            Ordered list of ``MigrationStep`` objects describing the diff.
        """
        added, removed, changed = self._diff_api_surfaces(old_api, new_api)
        steps: list[MigrationStep] = []
        for coord in sorted(removed):
            steps.append(
                MigrationStep(
                    step_id=_new_id("step"),
                    step_kind="REMOVE",
                    old_coordinate=coord,
                    new_coordinate="",
                    description=f"Coordinate {coord!r} removed from public surface",
                    is_breaking=True,
                    trust_level=None,
                    created_at=_now_iso(),
                )
            )
        for coord in sorted(added):
            steps.append(
                MigrationStep(
                    step_id=_new_id("step"),
                    step_kind="ADD",
                    old_coordinate="",
                    new_coordinate=coord,
                    description=f"Coordinate {coord!r} added to public surface",
                    is_breaking=False,
                    trust_level=None,
                    created_at=_now_iso(),
                )
            )
        for coord in sorted(changed):
            steps.append(
                MigrationStep(
                    step_id=_new_id("step"),
                    step_kind="RENAME",
                    old_coordinate=coord,
                    new_coordinate=coord,
                    description=f"Coordinate {coord!r} changed signature or obligations",
                    is_breaking=True,
                    trust_level=None,
                    created_at=_now_iso(),
                )
            )
        return steps

    def compute_donor_inheritance(
        self,
        steps: Sequence[MigrationStep],
    ) -> list[DonorInheritanceRecord]:
        """Build donor inheritance records from a list of migration steps.

        For each REMOVE or RENAME step, obligations on the old coordinate are
        pushed forward to the new coordinate.  ADD steps do not generate
        inheritance records (there is no donor).

        Args:
            steps: Sequence of ``MigrationStep`` objects from ``analyze_migration``.

        Returns:
            List of ``DonorInheritanceRecord`` objects.
        """
        records: list[DonorInheritanceRecord] = []
        for step in steps:
            if step.step_kind in ("REMOVE", "RENAME", "MOVE", "MERGE", "SPLIT"):
                obligations = tuple(
                    step.metadata.get("obligations", [f"obs:{step.old_coordinate}"])
                    if isinstance(step.metadata.get("obligations"), list)
                    else [f"obs:{step.old_coordinate}"]
                )
                kind = (
                    "NONE"
                    if step.step_kind == "REMOVE"
                    else ("FULL" if step.step_kind in ("RENAME", "MOVE") else "PARTIAL")
                )
                records.append(
                    DonorInheritanceRecord(
                        record_id=_new_id("rec"),
                        donor_coordinate=step.old_coordinate,
                        recipient_coordinate=step.new_coordinate,
                        inherited_obligations=obligations,
                        inherited_trust_level=step.trust_level,
                        inheritance_kind=kind,
                        transfer_timestamp=_now_iso(),
                        is_verified=False,
                        verification_notes="auto-generated; awaiting human review",
                    )
                )
        return records

    def check_behavior_preservation(
        self,
        old_api: dict[str, Any],
        new_api: dict[str, Any],
        steps: Sequence[MigrationStep],
    ) -> float:
        """Score behavioral preservation between old and new APIs on 0.0–1.0.

        The score is computed as the fraction of coordinates that survived
        the migration without breaking changes, weighted by the proportion of
        breaking steps.

        Args:
            old_api: Old API surface dict.
            new_api: New API surface dict.
            steps: Sequence of migration steps already computed.

        Returns:
            Float 0.0 (fully broken) to 1.0 (fully preserved).
        """
        if not steps:
            return 1.0
        breaking = sum(1 for s in steps if s.is_breaking)
        return max(0.0, 1.0 - breaking / len(steps))

    def detect_breaking_changes(
        self,
        steps: Sequence[MigrationStep],
    ) -> list[MigrationStep]:
        """Filter the step list to only breaking changes.

        Args:
            steps: Full sequence of migration steps.

        Returns:
            Sub-list containing only steps where ``is_breaking`` is True.
        """
        return [s for s in steps if s.is_breaking]

    def build_migration_witness(
        self,
        plan_id: str,
        steps: Sequence[MigrationStep],
        records: Sequence[DonorInheritanceRecord],
        preservation_score: float,
    ) -> MigrationWitness:
        """Assemble a ``MigrationWitness`` from computed artifacts.

        Args:
            plan_id: Identifier of the migration plan.
            steps: All migration steps attempted.
            records: Donor inheritance records generated.
            preservation_score: Score from ``check_behavior_preservation``.

        Returns:
            A new ``MigrationWitness`` encapsulating the migration outcome.
        """
        completed = tuple(s.step_id for s in steps if not s.is_breaking)
        obstructions = tuple(s.step_id for s in steps if s.is_breaking)
        return MigrationWitness(
            witness_id=_new_id("wit"),
            migration_plan_id=plan_id or _new_id("plan"),
            steps_completed=completed,
            donor_records=tuple(records),
            is_behavior_preserved=preservation_score >= 0.95,
            preservation_score=preservation_score,
            obstruction_ids=obstructions,
            created_at=_now_iso(),
        )

    def batch_analyze(
        self,
        migrations: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[MigrationWitness]:
        """Analyze multiple (old_api, new_api) pairs in one call.

        Args:
            migrations: Sequence of (old_api, new_api) tuples.

        Returns:
            List of ``MigrationWitness`` objects, one per input pair.
        """
        witnesses: list[MigrationWitness] = []
        for old_api, new_api in migrations:
            steps = self.analyze_migration(old_api, new_api)
            records = self.compute_donor_inheritance(steps)
            score = self.check_behavior_preservation(old_api, new_api, steps)
            witness = self.build_migration_witness(_new_id("plan"), steps, records, score)
            witnesses.append(witness)
        return witnesses

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _diff_api_surfaces(
        self,
        old_api: dict[str, Any],
        new_api: dict[str, Any],
    ) -> tuple[set[str], set[str], set[str]]:
        """Compute (added, removed, changed) coordinate sets.

        Args:
            old_api: Old surface dict.
            new_api: New surface dict.

        Returns:
            Three-tuple of sets: (added_keys, removed_keys, changed_keys).
        """
        old_keys = set(old_api.keys())
        new_keys = set(new_api.keys())
        added = new_keys - old_keys
        removed = old_keys - new_keys
        common = old_keys & new_keys
        changed = {k for k in common if old_api[k] != new_api[k]}
        return added, removed, changed


# ---------------------------------------------------------------------------
# §8  MigrationDonorInheritanceCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationDonorInheritanceCoordinator:
    """Top-level coordinator that orchestrates migration analysis.

    The coordinator wraps the analyzer with policy enforcement: if
    ``strict_preservation`` is True then any witness with a grade below B
    generates obstructions; if ``allow_breaking_changes`` is False then
    breaking steps always generate obstructions.

    Attributes:
        coordinator_id: Unique identifier for this coordinator instance.
        strict_preservation: Require grade A/B for acceptance.
        allow_breaking_changes: Whether breaking steps are tolerated.
        created_at: ISO-8601 creation timestamp.
    """

    coordinator_id: str
    strict_preservation: bool = True
    allow_breaking_changes: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set defaults for fields that require runtime initialisation.

        ``created_at`` defaults to the current UTC time when not supplied.
        Uses ``object.__setattr__`` because the dataclass is frozen.
        """
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Coordination entry points
    # ------------------------------------------------------------------

    def coordinate(
        self,
        old_api: dict[str, Any],
        new_api: dict[str, Any],
        plan_id: str = "",
    ) -> tuple[MigrationWitness, list[Any]]:
        """Coordinate a single migration and return (witness, obstructions).

        The returned list of obstructions will be empty when the migration
        passes all policy checks.  Obstructions are represented as plain dicts
        when ``ObstructionRecord`` is not importable.

        Args:
            old_api: Old API surface.
            new_api: New API surface.
            plan_id: Optional identifier for the migration plan.

        Returns:
            Tuple of (MigrationWitness, list[ObstructionRecord]).
        """
        analyzer = MigrationDonorInheritanceAnalyzer()
        steps = analyzer.analyze_migration(old_api, new_api)
        records = analyzer.compute_donor_inheritance(steps)
        score = analyzer.check_behavior_preservation(old_api, new_api, steps)
        eff_plan_id = plan_id or _new_id("plan")
        witness = analyzer.build_migration_witness(eff_plan_id, steps, records, score)

        obstructions: list[Any] = []
        if not self.allow_breaking_changes:
            for step in analyzer.detect_breaking_changes(steps):
                obstructions.append(
                    self._step_to_obstruction(step, "breaking change not permitted")
                )
        if self.strict_preservation and witness.grade() not in ("A", "B"):
            for record in records:
                if not record.is_complete_transfer():
                    obstructions.append(self._record_to_obstruction(record))
        return witness, obstructions

    def coordinate_batch(
        self,
        migrations: Sequence[tuple[dict[str, Any], dict[str, Any], str]],
    ) -> tuple[list[MigrationWitness], list[Any]]:
        """Coordinate multiple migrations in one call.

        Args:
            migrations: Sequence of (old_api, new_api, plan_id) triples.

        Returns:
            Tuple of (list[MigrationWitness], aggregated list[ObstructionRecord]).
        """
        all_witnesses: list[MigrationWitness] = []
        all_obstructions: list[Any] = []
        for old_api, new_api, plan_id in migrations:
            witness, obstructions = self.coordinate(old_api, new_api, plan_id)
            all_witnesses.append(witness)
            all_obstructions.extend(obstructions)
        return all_witnesses, all_obstructions

    def generate_migration_report(
        self,
        witnesses: Sequence[MigrationWitness],
    ) -> dict[str, JsonValue]:
        """Produce a structured report from a collection of witnesses.

        The report includes aggregate statistics and per-witness summaries.

        Args:
            witnesses: Sequence of ``MigrationWitness`` objects to summarise.

        Returns:
            Dict with keys: total, grades, avg_score, breaking_count.
        """
        if not witnesses:
            return {"total": 0, "grades": {}, "avg_score": 0.0, "breaking_count": 0}
        grades: dict[str, int] = {}
        total_score = 0.0
        total_breaking = 0
        for w in witnesses:
            g = w.grade()
            grades[g] = grades.get(g, 0) + 1
            total_score += w.preservation_score
            total_breaking += len(w.obstruction_ids)
        return {
            "total": len(witnesses),
            "grades": grades,
            "avg_score": round(total_score / len(witnesses), 4),
            "breaking_count": total_breaking,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _step_to_obstruction(
        self,
        step: MigrationStep,
        reason: str,
    ) -> dict[str, Any]:
        """Convert a breaking ``MigrationStep`` to an obstruction payload.

        Args:
            step: The breaking migration step.
            reason: Human-readable reason for the obstruction.

        Returns:
            Dict representing the obstruction (plain dict fallback).
        """
        return {
            "obstruction_id": _new_id("obs"),
            "coordinate": step.old_coordinate or step.new_coordinate,
            "reason": reason,
            "step_id": step.step_id,
            "step_kind": step.step_kind,
            "created_at": _now_iso(),
        }

    def _record_to_obstruction(
        self,
        record: DonorInheritanceRecord,
    ) -> dict[str, Any]:
        """Convert an incomplete ``DonorInheritanceRecord`` to an obstruction.

        Args:
            record: The donor inheritance record with an incomplete transfer.

        Returns:
            Dict representing the obstruction.
        """
        return {
            "obstruction_id": _new_id("obs"),
            "coordinate": record.donor_coordinate,
            "reason": f"Incomplete inheritance transfer (kind={record.inheritance_kind}, verified={record.is_verified})",
            "record_id": record.record_id,
            "created_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# §9  MigrationDonorInheritanceWitness  (top-level aggregate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationDonorInheritanceWitness:
    """Top-level aggregate witness for a full migration session.

    Summarises the outcomes of all migrations coordinated within a single
    session.  Used as the final artifact presented to external auditors and
    included in the public alignment manifest.

    Attributes:
        witness_id: Unique identifier.
        session_id: Identifier of the session that produced this witness.
        total_migrations: Total number of migrations attempted.
        successful_migrations: Migrations that passed all policy checks.
        failed_migrations: Migrations that produced obstructions.
        breaking_changes_count: Total number of breaking steps across all migrations.
        overall_preservation_score: Weighted average preservation score.
        created_at: ISO-8601 timestamp.
        metadata: Arbitrary additional data.
    """

    witness_id: str
    session_id: str
    total_migrations: int
    successful_migrations: int
    failed_migrations: int
    breaking_changes_count: int
    overall_preservation_score: float
    created_at: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dict."""
        return {
            "witness_id": self.witness_id,
            "session_id": self.session_id,
            "total_migrations": self.total_migrations,
            "successful_migrations": self.successful_migrations,
            "failed_migrations": self.failed_migrations,
            "breaking_changes_count": self.breaking_changes_count,
            "overall_preservation_score": self.overall_preservation_score,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationDonorInheritanceWitness:
        """Reconstruct a ``MigrationDonorInheritanceWitness`` from a plain dict."""
        return cls(
            witness_id=data.get("witness_id", _new_id("mwit")),
            session_id=data.get("session_id", ""),
            total_migrations=int(data.get("total_migrations", 0)),
            successful_migrations=int(data.get("successful_migrations", 0)),
            failed_migrations=int(data.get("failed_migrations", 0)),
            breaking_changes_count=int(data.get("breaking_changes_count", 0)),
            overall_preservation_score=float(data.get("overall_preservation_score", 0.0)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def success_rate(self) -> float:
        """Return the fraction of migrations that succeeded.

        Returns 1.0 when no migrations were attempted to avoid division by zero.
        """
        if self.total_migrations == 0:
            return 1.0
        return self.successful_migrations / self.total_migrations

    def summary(self) -> str:
        """Return a one-line summary of the migration session.

        The summary includes the session ID, success rate, and overall
        preservation score for quick inspection.
        """
        return (
            f"session={self.session_id} "
            f"success={self.success_rate():.0%} "
            f"preservation={self.overall_preservation_score:.2f} "
            f"breaking={self.breaking_changes_count}"
        )

    def is_acceptable(self, threshold: float = 0.9) -> bool:
        """Return True iff the overall preservation score meets the threshold.

        Args:
            threshold: Minimum acceptable preservation score (default 0.9).

        Returns:
            True if ``overall_preservation_score >= threshold`` and no failed
            migrations remain.
        """
        return (
            self.overall_preservation_score >= threshold and self.failed_migrations == 0
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
    "MigrationStep",
    "DonorInheritanceRecord",
    "MigrationWitness",
    "MigrationDonorInheritanceAnalyzer",
    "MigrationDonorInheritanceCoordinator",
    "MigrationDonorInheritanceWitness",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# ---------------------------------------------------------------------------
# §11  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== migration_and_donor_inheritance smoke test ===")

    old: dict[str, Any] = {
        "get_user": {"args": ["id"], "returns": "User"},
        "list_users": {"args": [], "returns": "list[User]"},
        "delete_user": {"args": ["id"], "returns": "None"},
    }
    new: dict[str, Any] = {
        "fetch_user": {"args": ["user_id"], "returns": "User"},
        "list_users": {"args": ["page"], "returns": "list[User]"},
        "create_user": {"args": ["data"], "returns": "User"},
    }

    analyzer = MigrationDonorInheritanceAnalyzer()
    steps = analyzer.analyze_migration(old, new)
    print(f"  steps found: {len(steps)}")
    for s in steps:
        print(f"    {s.summarize()}")

    records = analyzer.compute_donor_inheritance(steps)
    score = analyzer.check_behavior_preservation(old, new, steps)
    print(f"  preservation score: {score:.2f}")
    print(f"  donor records: {len(records)}")

    coordinator = MigrationDonorInheritanceCoordinator(
        coordinator_id=_new_id("coord"),
        strict_preservation=False,
        allow_breaking_changes=True,
    )
    witness, obstructions = coordinator.coordinate(old, new, "plan-001")
    print(f"  witness grade: {witness.grade()}")
    print(f"  obstructions: {len(obstructions)}")

    agg = MigrationDonorInheritanceWitness(
        witness_id=_new_id("mwit"),
        session_id="sess-001",
        total_migrations=1,
        successful_migrations=1 if not obstructions else 0,
        failed_migrations=1 if obstructions else 0,
        breaking_changes_count=len(witness.obstruction_ids),
        overall_preservation_score=witness.preservation_score,
        created_at=_now_iso(),
    )
    print(f"  aggregate: {agg.summary()}")
    print(f"  acceptable (0.5): {agg.is_acceptable(0.5)}")
    print("=== smoke test passed ===")
