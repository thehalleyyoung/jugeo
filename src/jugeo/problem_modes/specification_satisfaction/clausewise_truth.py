"""Clausewise truth evaluation for the specification-satisfaction problem mode.

Section 10.2: Clausewise Truth.  Each clause in a specification is checked
independently against the current judgment section.  The result of checking a
single clause is NOT a boolean — it is either a *witness record* (the clause is
satisfied by the current section) or a *gap record* (the clause is not satisfied,
and we record what is missing and how severe the gap is).  The collection of
per-clause truth values constitutes the *clausewise truth table* for the
specification against the current section.

Theory2 invariants
------------------
* Judgments are 8-tuples (c, φ, A, E, O, B, T, Π) where:
  - c   = coordinate (an object of the site)
  - φ   = formula/type being judged
  - A   = author (entity who issued the judgment)
  - E   = evidence (the proof/data attached)
  - O   = obligations (open duties still owed)
  - B   = blocking conditions (conditions that prevent progress)
  - T   = trust tier (a discrete tier/level, NOT a float)
  - Π   = provenance (origin chain of the judgment)
* Trust is a discrete tier — strings such as VERIFIED, TRUSTED, PROPOSED,
  UNVERIFIED are the four canonical tiers; comparisons are ordinal, never
  floating-point.
* Specifications are target sections of the judgment sheaf — they prescribe
  *what* should be at each coordinate; they are not boolean predicates.
* Per-clause truth values are witness records or gap records, never raw bools.
* Generation proposals enter at PROPOSAL tier, which is treated as PROPOSED.

References
----------
theory2.tex §10.1  "Specifications as Presheaves of Judgments"
theory2.tex §10.2  "Witnesses and Partial Sections"
theory2.tex §10.3  "Certificates via Descent"
theory2.tex §10.4  "Residual Gaps and Obstruction Classes"

# copilot: generated scaffold for jugeo specification-satisfaction s02 —
# per-clause truth evaluation for theory2.tex §10.2 clausewise truth table.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo imports – fall back to Any so the module loads stand-alone.
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        SatisfactionStatus,
        SatisfactionWitness,
        ResidualGap,
        GapSeverity,
        WitnessStatus,
    )
except ImportError:
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.specification_satisfaction.specifications import (
        Specification,
        SpecificationClause,
        SpecificationKind,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SpecificationClause = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

EVALUATION_STATUS_SATISFIED: str = "SATISFIED"
EVALUATION_STATUS_PARTIAL: str = "PARTIAL"
EVALUATION_STATUS_UNSATISFIED: str = "UNSATISFIED"

GAP_SEVERITY_CRITICAL: str = "CRITICAL"
GAP_SEVERITY_HIGH: str = "HIGH"
GAP_SEVERITY_MEDIUM: str = "MEDIUM"
GAP_SEVERITY_LOW: str = "LOW"

TRUST_TIER_VERIFIED: str = "VERIFIED"
TRUST_TIER_TRUSTED: str = "TRUSTED"
TRUST_TIER_PROPOSED: str = "PROPOSED"
TRUST_TIER_UNVERIFIED: str = "UNVERIFIED"

JUDGMENT_COMPONENTS: tuple[str, ...] = ("c", "phi", "A", "E", "O", "B", "T", "Pi")

# Ordinal rank for trust tiers (higher = stronger trust).
_TRUST_TIER_RANK: dict[str, int] = {
    TRUST_TIER_UNVERIFIED: 0,
    TRUST_TIER_PROPOSED: 1,
    TRUST_TIER_TRUSTED: 2,
    TRUST_TIER_VERIFIED: 3,
}

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Enum
    "TruthEvaluationKind",
    # Frozen dataclasses
    "ClauseTruthRecord",
    "ClausewiseTruthEntry",
    "ClausewiseTruthWitness",
    # Mutable dataclasses
    "ClauseGapRecord",
    "ClausewiseTruthTable",
    # Main classes
    "ClausewiseTruthCoordinator",
    "ClausewiseTruthAnalyzer",
    # Module-level functions
    "evaluate_clause_against_judgment",
    "build_clausewise_truth_table",
    "merge_truth_tables",
    "compute_overall_satisfaction_status",
    "gap_records_from_table",
    "witness_records_from_table",
    "clausewise_truth_witness_from_table",
    "rank_gaps",
    # Constants
    "EVALUATION_STATUS_SATISFIED",
    "EVALUATION_STATUS_PARTIAL",
    "EVALUATION_STATUS_UNSATISFIED",
    "GAP_SEVERITY_CRITICAL",
    "GAP_SEVERITY_HIGH",
    "GAP_SEVERITY_MEDIUM",
    "GAP_SEVERITY_LOW",
    "TRUST_TIER_VERIFIED",
    "TRUST_TIER_TRUSTED",
    "TRUST_TIER_PROPOSED",
    "TRUST_TIER_UNVERIFIED",
    "JUDGMENT_COMPONENTS",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]

# copilot: clausewise_truth — per-clause truth evaluation for theory2.tex §10.2

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Parameters
    ----------
    None

    Returns
    -------
    str
        UTC timestamp in the form ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _stable_hash(payload: str) -> str:
    """Compute a stable, deterministic SHA-256 hex digest of *payload*.

    Parameters
    ----------
    payload : str
        Arbitrary string to hash.

    Returns
    -------
    str
        64-character lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_record_id(clause_id: str, coordinate: str) -> str:
    """Construct a deterministic record identifier for a clause truth record.

    The identifier encodes both the clause and the coordinate so that two
    records for the same (clause, coordinate) pair always produce the same id.

    Parameters
    ----------
    clause_id : str
        Identifier of the specification clause being evaluated.
    coordinate : str
        String representation of the site coordinate.

    Returns
    -------
    str
        Prefixed hex string of the form ``ctr-<12-char-hash>``.
    """
    raw = f"record::{clause_id}::{coordinate}"
    return "ctr-" + _stable_hash(raw)[:12]


def _make_gap_id(clause_id: str, gap_kind: str) -> str:
    """Construct a deterministic identifier for a clause gap record.

    Parameters
    ----------
    clause_id : str
        Identifier of the specification clause that has a gap.
    gap_kind : str
        Category of the gap (e.g. ``"MISSING_EVIDENCE"``, ``"TRUST_BELOW_THRESHOLD"``).

    Returns
    -------
    str
        Prefixed hex string of the form ``cgr-<12-char-hash>``.
    """
    raw = f"gap::{clause_id}::{gap_kind}"
    return "cgr-" + _stable_hash(raw)[:12]


def _make_table_id(spec_id: str) -> str:
    """Construct a deterministic identifier for a clausewise truth table.

    Parameters
    ----------
    spec_id : str
        Identifier of the specification whose clauses are being evaluated.

    Returns
    -------
    str
        Prefixed hex string of the form ``ctt-<12-char-hash>``.
    """
    salt = str(uuid.uuid4())
    raw = f"table::{spec_id}::{salt}"
    return "ctt-" + _stable_hash(raw)[:12]


def _evidence_kinds_from_judgment(judgment_fields: dict) -> list[str]:
    """Extract the evidence kinds present in a judgment field dictionary.

    The judgment field ``E`` may hold a plain string, a list of evidence items,
    or a mapping.  This helper normalises the value into a list of string kind
    labels that describe what sorts of evidence are attached.

    Parameters
    ----------
    judgment_fields : dict
        A judgment 8-tuple expressed as a dict with keys drawn from
        ``JUDGMENT_COMPONENTS``.

    Returns
    -------
    list[str]
        A deduplicated list of evidence-kind labels.  Returns an empty list
        when no evidence field is present.
    """
    evidence_raw = judgment_fields.get("E", None)
    if evidence_raw is None:
        return []
    if isinstance(evidence_raw, str):
        return [evidence_raw] if evidence_raw else []
    if isinstance(evidence_raw, list):
        kinds: list[str] = []
        for item in evidence_raw:
            if isinstance(item, str):
                kinds.append(item)
            elif isinstance(item, dict):
                kinds.append(item.get("kind", item.get("type", "UNKNOWN")))
            else:
                kinds.append(str(type(item).__name__))
        return list(dict.fromkeys(kinds))  # preserve order, deduplicate
    if isinstance(evidence_raw, dict):
        return [evidence_raw.get("kind", evidence_raw.get("type", "DICT_EVIDENCE"))]
    return [str(type(evidence_raw).__name__)]


def _check_formula_match(formula: str, judgment_fields: dict) -> bool:
    """Decide whether a clause formula is matched by the judgment section entry.

    The matching is deliberately lenient: a wildcard formula ``"*"`` matches
    anything; otherwise we check substring containment after lowercasing, which
    allows partial symbolic matching without requiring a full unifier.

    Parameters
    ----------
    formula : str
        The formula or type string from the specification clause.
    judgment_fields : dict
        A judgment 8-tuple expressed as a dict.

    Returns
    -------
    bool
        ``True`` if the judgment's ``phi`` field matches *formula*.
    """
    if not formula or formula == "*":
        return True
    judgment_phi = str(judgment_fields.get("phi", ""))
    if not judgment_phi:
        return False
    formula_lower = formula.strip().lower()
    phi_lower = judgment_phi.strip().lower()
    # Exact match first.
    if formula_lower == phi_lower:
        return True
    # Substring containment — covers cases where the judgment elaborates the clause.
    if formula_lower in phi_lower or phi_lower in formula_lower:
        return True
    return False


def _check_trust_threshold(judgment_trust_tier: str, threshold: str) -> bool:
    """Determine whether a judgment's trust tier meets a required threshold.

    Trust is compared ordinally using ``_TRUST_TIER_RANK``.  An unknown tier
    is treated as rank −1 (fails every threshold check).

    Parameters
    ----------
    judgment_trust_tier : str
        The ``T`` component of the judgment — one of the four canonical tiers
        (VERIFIED, TRUSTED, PROPOSED, UNVERIFIED) or a custom label.
    threshold : str
        The minimum acceptable tier.

    Returns
    -------
    bool
        ``True`` if ``judgment_trust_tier`` is at least as strong as
        *threshold*.
    """
    tier_rank = _TRUST_TIER_RANK.get(judgment_trust_tier, -1)
    threshold_rank = _TRUST_TIER_RANK.get(threshold, 0)
    return tier_rank >= threshold_rank


def _classify_gap_severity(missing_evidence: list, obstruction: str | None) -> str:
    """Classify the severity of a gap based on what is missing and any obstruction.

    Severity rules (applied in order, first match wins):
    1. Any obstruction present → CRITICAL.
    2. Three or more items of missing evidence → HIGH.
    3. One or two items of missing evidence → MEDIUM.
    4. No missing evidence and no obstruction → LOW (vacuous gap).

    Parameters
    ----------
    missing_evidence : list
        Items that are absent from the current judgment section for the clause.
    obstruction : str | None
        A description of a hard blocking condition, or ``None`` if none exists.

    Returns
    -------
    str
        One of ``GAP_SEVERITY_CRITICAL``, ``GAP_SEVERITY_HIGH``,
        ``GAP_SEVERITY_MEDIUM``, ``GAP_SEVERITY_LOW``.
    """
    if obstruction:
        return GAP_SEVERITY_CRITICAL
    n = len(missing_evidence)
    if n >= 3:
        return GAP_SEVERITY_HIGH
    if n >= 1:
        return GAP_SEVERITY_MEDIUM
    return GAP_SEVERITY_LOW


def _compute_blocking_clauses(truth_records: dict) -> list[str]:
    """Identify clause ids whose truth records are in a blocking state.

    A clause is *blocking* when its evaluation status is UNSATISFIED and it
    carries a non-empty ``blocking_reason``.  Such clauses prevent the overall
    specification from being satisfied regardless of other clauses.

    Parameters
    ----------
    truth_records : dict
        Mapping of ``clause_id → ClauseTruthRecord | ClauseGapRecord``.

    Returns
    -------
    list[str]
        Sorted list of clause ids that are currently blocking.
    """
    blocking: list[str] = []
    for clause_id, record in truth_records.items():
        if isinstance(record, ClauseTruthRecord):
            if (
                record.evaluation_status == EVALUATION_STATUS_UNSATISFIED
                and record.blocking_reason
            ):
                blocking.append(clause_id)
        elif isinstance(record, ClauseGapRecord):
            if record.obstruction:
                blocking.append(clause_id)
    return sorted(set(blocking))


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


class TruthEvaluationKind(Enum):
    """Enumeration of the ways in which a clause truth value may be obtained.

    Attributes
    ----------
    DIRECT :
        The clause was evaluated directly against evidence present at the same
        coordinate.
    INFERRED :
        The clause truth was inferred via a logical derivation from other
        clauses or known facts.
    INHERITED :
        The clause truth was inherited from a parent or covering coordinate via
        the restriction maps of the sheaf.
    DELEGATED :
        Evaluation was delegated to a sub-agent or external oracle that
        returned a verdict.
    BLOCKED :
        Evaluation was attempted but halted by a blocking condition (``B``
        component of the judgment).
    VACUOUS :
        The clause is vacuously satisfied because there are no constraints to
        discharge (e.g. an empty formula or an always-true guard).
    """

    DIRECT = "DIRECT"
    INFERRED = "INFERRED"
    INHERITED = "INHERITED"
    DELEGATED = "DELEGATED"
    BLOCKED = "BLOCKED"
    VACUOUS = "VACUOUS"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClauseTruthRecord:
    """Immutable record capturing a satisfied (or partially satisfied) clause.

    A ``ClauseTruthRecord`` is produced when the evaluator determines that a
    specification clause is met — possibly only partially — by the current
    judgment section.  The record encodes which evidence was found, the trust
    tier of the matching judgment, and any blocking reason that prevented full
    satisfaction.

    Parameters
    ----------
    record_id : str
        Unique, deterministic identifier for this record (see
        ``_make_record_id``).
    clause_id : str
        Identifier of the specification clause this record describes.
    coordinate : str
        String representation of the site coordinate at which the clause was
        evaluated.
    evaluation_status : str
        One of ``EVALUATION_STATUS_SATISFIED``, ``EVALUATION_STATUS_PARTIAL``,
        or ``EVALUATION_STATUS_UNSATISFIED``.
    evidence_present : list
        Raw evidence items found in the judgment's ``E`` component.
    evidence_kinds : list
        Normalised kind labels extracted from ``evidence_present``
        (see ``_evidence_kinds_from_judgment``).
    trust_tier : str
        Trust tier of the matching judgment (the ``T`` component).  Always one
        of the four canonical string tiers, never a float.
    blocking_reason : str | None
        Human-readable description of any blocking condition that prevented
        full satisfaction, or ``None`` if the clause is fully satisfied.
    record_timestamp : str
        ISO-8601 UTC timestamp of when this record was created.

    Methods
    -------
    is_satisfied() -> bool
        Return ``True`` iff ``evaluation_status == EVALUATION_STATUS_SATISFIED``.
    """

    record_id: str
    clause_id: str
    coordinate: str
    evaluation_status: str
    evidence_present: list
    evidence_kinds: list
    trust_tier: str
    blocking_reason: str | None
    record_timestamp: str

    def is_satisfied(self) -> bool:
        """Return whether this clause is fully satisfied.

        Returns
        -------
        bool
            ``True`` iff ``evaluation_status`` is ``EVALUATION_STATUS_SATISFIED``.
        """
        return self.evaluation_status == EVALUATION_STATUS_SATISFIED

    def to_dict(self) -> dict:
        """Serialise this record to a plain dictionary.

        Returns
        -------
        dict
            All fields expressed as JSON-serialisable Python primitives.
        """
        return {
            "record_id": self.record_id,
            "clause_id": self.clause_id,
            "coordinate": self.coordinate,
            "evaluation_status": self.evaluation_status,
            "evidence_present": list(self.evidence_present),
            "evidence_kinds": list(self.evidence_kinds),
            "trust_tier": self.trust_tier,
            "blocking_reason": self.blocking_reason,
            "record_timestamp": self.record_timestamp,
        }


@dataclass(frozen=True, slots=True)
class ClausewiseTruthEntry:
    """Immutable entry in a clausewise truth table.

    Each entry wraps either a ``ClauseTruthRecord`` or a ``ClauseGapRecord``
    (serialised as a plain dict) together with indexing and metadata fields
    that allow the truth table to reconstruct a complete picture of the
    evaluation.

    Parameters
    ----------
    entry_id : str
        UUID-based unique identifier for this entry.
    clause_id : str
        Identifier of the specification clause this entry covers.
    is_gap : bool
        ``True`` if this entry represents an unsatisfied clause (gap);
        ``False`` if it represents a satisfied/partial clause (truth record).
    truth_record : dict
        Serialised form of the underlying ``ClauseTruthRecord`` or
        ``ClauseGapRecord``.
    evaluation_metadata : dict
        Arbitrary metadata about how the evaluation was performed — e.g.
        which ``TruthEvaluationKind`` was used, elapsed time, engine version.
    evaluation_order_index : int
        Zero-based position of this clause in the evaluation order sequence.
    entry_timestamp : str
        ISO-8601 UTC timestamp of when this entry was added to the table.
    """

    entry_id: str
    clause_id: str
    is_gap: bool
    truth_record: dict
    evaluation_metadata: dict
    evaluation_order_index: int
    entry_timestamp: str


@dataclass(frozen=True, slots=True)
class ClausewiseTruthWitness:
    """Immutable record certifying the clausewise truth of a full specification.

    A ``ClausewiseTruthWitness`` is produced after all clauses have been
    evaluated.  It summarises the per-clause truth table and records whether
    the global section is achievable (i.e. all clauses are satisfied and no
    blocking conditions remain).

    Parameters
    ----------
    witness_id : str
        UUID-based unique identifier for this witness record.
    spec_id : str
        Identifier of the specification being witnessed.
    clause_count : int
        Total number of clauses in the specification.
    satisfied_count : int
        Number of clauses with status SATISFIED.
    unsatisfied_count : int
        Number of clauses with status UNSATISFIED or that produced gap records.
    clause_truth_records : dict
        Mapping of ``clause_id → serialised record dict`` for all clauses.
    per_clause_trust_tiers : dict
        Mapping of ``clause_id → trust_tier_string`` for quick lookup.
    overall_status : str
        One of the three ``EVALUATION_STATUS_*`` constants describing the
        aggregate satisfaction status.
    global_section_achievable : bool
        ``True`` iff all clauses are satisfied and there are no blocking
        clauses — i.e. the spec's target section is fully achieved.
    blocking_clauses : list
        Clause ids that are blocking (have hard obstructions).
    witness_timestamp : str
        ISO-8601 UTC timestamp of when this witness was created.
    provenance : str
        Free-text provenance chain describing how this witness was generated.
    """

    witness_id: str
    spec_id: str
    clause_count: int
    satisfied_count: int
    unsatisfied_count: int
    clause_truth_records: dict
    per_clause_trust_tiers: dict
    overall_status: str
    global_section_achievable: bool
    blocking_clauses: list
    witness_timestamp: str
    provenance: str

    def summary_dict(self) -> dict:
        """Return a compact summary dictionary (omits large nested records).

        Returns
        -------
        dict
            Keys: witness_id, spec_id, clause_count, satisfied_count,
            unsatisfied_count, overall_status, global_section_achievable,
            blocking_clauses, witness_timestamp, provenance.
        """
        return {
            "witness_id": self.witness_id,
            "spec_id": self.spec_id,
            "clause_count": self.clause_count,
            "satisfied_count": self.satisfied_count,
            "unsatisfied_count": self.unsatisfied_count,
            "overall_status": self.overall_status,
            "global_section_achievable": self.global_section_achievable,
            "blocking_clauses": list(self.blocking_clauses),
            "witness_timestamp": self.witness_timestamp,
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClauseGapRecord:
    """Mutable record describing a gap — a clause that is not satisfied.

    A ``ClauseGapRecord`` is produced when the evaluator finds that the current
    judgment section does not provide the evidence required by a specification
    clause.  The record captures *what* is missing, *why* it cannot currently
    be supplied (obstruction), *how severe* the shortfall is, and *hints* for
    how to repair the gap.

    Parameters
    ----------
    gap_id : str
        Deterministic identifier for this gap record (see ``_make_gap_id``).
    clause_id : str
        Identifier of the specification clause that is unsatisfied.
    coordinate : str
        String representation of the site coordinate where the gap was found.
    gap_kind : str
        A label categorising the gap, such as ``"MISSING_EVIDENCE"``,
        ``"TRUST_BELOW_THRESHOLD"``, ``"FORMULA_MISMATCH"``, or
        ``"BLOCKED_BY_CONDITION"``.
    missing_evidence : list
        Description of the evidence items that are absent from the judgment.
    obstruction : str | None
        A blocking condition preventing progress, or ``None``.
    gap_severity : str
        One of the four ``GAP_SEVERITY_*`` constants.
    repair_hints : list
        Ordered list of human-readable suggestions for repairing the gap.
    created_at : str
        ISO-8601 UTC timestamp of when this gap was first recorded.
    updated_at : str
        ISO-8601 UTC timestamp of the most recent mutation.

    Methods
    -------
    add_repair_hint(hint: str) -> None
        Append a repair hint to ``repair_hints`` and refresh ``updated_at``.
    escalate_severity() -> None
        Raise the severity one level (LOW→MEDIUM→HIGH→CRITICAL).
    to_dict() -> dict
        Serialise this record to a plain dictionary.
    """

    gap_id: str
    clause_id: str
    coordinate: str
    gap_kind: str
    missing_evidence: list
    obstruction: str | None
    gap_severity: str
    repair_hints: list
    created_at: str
    updated_at: str

    def add_repair_hint(self, hint: str) -> None:
        """Append a repair suggestion to this gap record.

        Parameters
        ----------
        hint : str
            A human-readable description of a potential repair action.
        """
        if hint and hint not in self.repair_hints:
            self.repair_hints.append(hint)
        self.updated_at = _now_iso()

    def escalate_severity(self) -> None:
        """Raise the severity of this gap by one tier.

        The ladder is LOW → MEDIUM → HIGH → CRITICAL.  Calling this method
        when the severity is already CRITICAL has no effect.
        """
        ladder = [
            GAP_SEVERITY_LOW,
            GAP_SEVERITY_MEDIUM,
            GAP_SEVERITY_HIGH,
            GAP_SEVERITY_CRITICAL,
        ]
        current = self.gap_severity
        try:
            idx = ladder.index(current)
        except ValueError:
            # Unknown severity — treat as LOW.
            idx = 0
        if idx < len(ladder) - 1:
            self.gap_severity = ladder[idx + 1]
        self.updated_at = _now_iso()

    def to_dict(self) -> dict:
        """Serialise this gap record to a plain dictionary.

        Returns
        -------
        dict
            All fields expressed as JSON-serialisable Python primitives.
        """
        return {
            "gap_id": self.gap_id,
            "clause_id": self.clause_id,
            "coordinate": self.coordinate,
            "gap_kind": self.gap_kind,
            "missing_evidence": list(self.missing_evidence),
            "obstruction": self.obstruction,
            "gap_severity": self.gap_severity,
            "repair_hints": list(self.repair_hints),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class ClausewiseTruthTable:
    """Mutable container for the full collection of per-clause truth entries.

    The truth table is the primary output of the clausewise evaluation process.
    It accumulates ``ClausewiseTruthEntry`` objects as each clause is evaluated
    and exposes methods for querying aggregate satisfaction metrics.

    Parameters
    ----------
    table_id : str
        Unique identifier for this truth table instance.
    spec_id : str
        Identifier of the specification whose clauses populate this table.
    entries : list[ClausewiseTruthEntry]
        Ordered list of truth entries, one per evaluated clause.
    clause_id_index : dict
        Mapping of ``clause_id → index`` into ``entries`` for O(1) lookup.
    gap_count : int
        Running count of gap entries.
    witness_count : int
        Running count of non-gap (truth record) entries.
    created_at : str
        ISO-8601 UTC timestamp of when this table was created.

    Methods
    -------
    add_entry(entry: ClausewiseTruthEntry) -> None
        Append an entry and update internal index and counters.
    get_entry_for_clause(clause_id: str) -> ClausewiseTruthEntry | None
        Return the entry for *clause_id*, or ``None`` if not present.
    compute_satisfaction_fraction() -> float
        Return the fraction of clauses that have been satisfied (0.0–1.0).
    to_summary_dict() -> dict
        Return a compact summary dictionary describing this table.
    """

    table_id: str
    spec_id: str
    entries: list = field(default_factory=list)
    clause_id_index: dict = field(default_factory=dict)
    gap_count: int = 0
    witness_count: int = 0
    created_at: str = field(default_factory=_now_iso)

    def add_entry(self, entry: ClausewiseTruthEntry) -> None:
        """Append a truth entry to this table.

        If an entry for the same ``clause_id`` already exists it is replaced
        rather than duplicated (the last evaluation wins).

        Parameters
        ----------
        entry : ClausewiseTruthEntry
            The entry to add or replace.
        """
        existing_idx = self.clause_id_index.get(entry.clause_id)
        if existing_idx is not None:
            # Replace in-place; update counters.
            old_entry = self.entries[existing_idx]
            if old_entry.is_gap:
                self.gap_count -= 1
            else:
                self.witness_count -= 1
            self.entries[existing_idx] = entry
        else:
            self.clause_id_index[entry.clause_id] = len(self.entries)
            self.entries.append(entry)

        if entry.is_gap:
            self.gap_count += 1
        else:
            self.witness_count += 1

    def get_entry_for_clause(self, clause_id: str) -> ClausewiseTruthEntry | None:
        """Return the truth entry for a given clause identifier.

        Parameters
        ----------
        clause_id : str
            The clause identifier to look up.

        Returns
        -------
        ClausewiseTruthEntry | None
            The entry if found, or ``None`` if no entry exists for this clause.
        """
        idx = self.clause_id_index.get(clause_id)
        if idx is None:
            return None
        return self.entries[idx]

    def compute_satisfaction_fraction(self) -> float:
        """Return the proportion of evaluated clauses that are satisfied.

        A clause is counted as satisfied when its truth entry is a non-gap
        entry (``is_gap is False``).  Partial entries are counted as
        satisfied for the purposes of this metric.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.  Returns ``0.0`` for an empty table.
        """
        total = len(self.entries)
        if total == 0:
            return 0.0
        return self.witness_count / total

    def to_summary_dict(self) -> dict:
        """Return a compact summary of this truth table.

        Returns
        -------
        dict
            Keys: table_id, spec_id, total_clauses, gap_count, witness_count,
            satisfaction_fraction, created_at.
        """
        return {
            "table_id": self.table_id,
            "spec_id": self.spec_id,
            "total_clauses": len(self.entries),
            "gap_count": self.gap_count,
            "witness_count": self.witness_count,
            "satisfaction_fraction": self.compute_satisfaction_fraction(),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Main classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClausewiseTruthCoordinator:
    """Orchestrates the per-clause evaluation of a specification against a judgment section.

    The coordinator holds the current judgment section (a mapping from
    coordinate to judgment 8-tuple fields) and drives the evaluation of every
    clause in a specification, producing a ``ClausewiseTruthTable``.  It also
    exposes query methods for inspecting partial results and computing overall
    satisfaction status.

    Parameters
    ----------
    coordinator_id : str
        UUID-based unique identifier for this coordinator instance.
    judgment_section : dict
        The current section being evaluated against — a mapping of
        ``coordinate_str → judgment_fields_dict``.  Each value is a dict
        with keys drawn from ``JUDGMENT_COMPONENTS``.
    clause_truth_table : dict
        Accumulator mapping ``clause_id → ClauseTruthRecord | ClauseGapRecord``
        as clauses are evaluated.
    evaluation_order : list
        Ordered list of clause ids defining the sequence in which clauses are
        (or were) evaluated.
    evaluation_log : list
        Append-only list of log entries (plain dicts) describing each
        evaluation step.
    strict_mode : bool
        When ``True``, a clause is only SATISFIED when evidence is present *and*
        the trust tier meets ``trust_threshold``.  When ``False``, clauses with
        matching formulas are accepted at PARTIAL status even if trust is low.
    trust_threshold : str
        The minimum trust tier required for a clause to be fully satisfied in
        ``strict_mode``.  Defaults to ``TRUST_TIER_TRUSTED``.

    Methods
    -------
    load_judgment_section(section: dict) -> None
        Replace the judgment section and clear the truth table.
    evaluate_clause(clause_dict: dict) -> ClauseTruthRecord | ClauseGapRecord
        Evaluate a single clause and store the result.
    evaluate_all_clauses(clauses: list[dict]) -> ClausewiseTruthTable
        Evaluate every clause in sequence and return the complete truth table.
    get_truth_table() -> ClausewiseTruthTable
        Build and return a ``ClausewiseTruthTable`` from accumulated results.
    get_satisfied_clauses() -> list[str]
        Return clause ids with status SATISFIED.
    get_unsatisfied_clauses() -> list[str]
        Return clause ids with status UNSATISFIED or gap records.
    compute_overall_status() -> str
        Compute the aggregate satisfaction status across all clauses.
    reset() -> None
        Clear all accumulated state except configuration fields.
    """

    coordinator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    judgment_section: dict = field(default_factory=dict)
    clause_truth_table: dict = field(default_factory=dict)
    evaluation_order: list = field(default_factory=list)
    evaluation_log: list = field(default_factory=list)
    strict_mode: bool = True
    trust_threshold: str = TRUST_TIER_TRUSTED

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def load_judgment_section(self, section: dict) -> None:
        """Replace the current judgment section with a new one.

        Loading a new section automatically clears the accumulated truth table
        and evaluation order so that stale results are not mixed with fresh
        evaluations.

        Parameters
        ----------
        section : dict
            A mapping of ``coordinate_str → judgment_fields_dict``.  Each
            value should be a dict with keys from ``JUDGMENT_COMPONENTS``.
        """
        self.judgment_section = dict(section)
        self.clause_truth_table = {}
        self.evaluation_order = []
        self.evaluation_log.append({
            "event": "load_judgment_section",
            "coordinate_count": len(section),
            "timestamp": _now_iso(),
        })

    def evaluate_clause(
        self, clause_dict: dict
    ) -> ClauseTruthRecord | ClauseGapRecord:
        """Evaluate a single specification clause against the judgment section.

        The evaluation proceeds as follows:

        1. Locate the judgment entry for the clause's coordinate.
        2. If no entry exists, produce a gap with kind ``"MISSING_COORDINATE"``.
        3. Check formula match via ``_check_formula_match``.
        4. Check trust tier via ``_check_trust_threshold`` (in strict mode).
        5. Inspect evidence via ``_evidence_kinds_from_judgment``.
        6. Decide SATISFIED / PARTIAL / UNSATISFIED.
        7. Store the result in ``clause_truth_table`` and ``evaluation_order``.

        Parameters
        ----------
        clause_dict : dict
            A dict describing the specification clause.  Expected keys:
            ``clause_id`` (str), ``coordinate`` (str), ``formula`` (str),
            ``required_trust`` (str, optional).

        Returns
        -------
        ClauseTruthRecord | ClauseGapRecord
            The evaluation result for this clause.
        """
        clause_id = clause_dict.get("clause_id", str(uuid.uuid4()))
        coordinate = str(clause_dict.get("coordinate", ""))
        formula = str(clause_dict.get("formula", "*"))
        required_trust = clause_dict.get("required_trust", self.trust_threshold)

        # Locate judgment entry for this coordinate.
        judgment_fields = self.judgment_section.get(coordinate)

        record: ClauseTruthRecord | ClauseGapRecord

        if judgment_fields is None:
            # No judgment at this coordinate — hard gap.
            gap = ClauseGapRecord(
                gap_id=_make_gap_id(clause_id, "MISSING_COORDINATE"),
                clause_id=clause_id,
                coordinate=coordinate,
                gap_kind="MISSING_COORDINATE",
                missing_evidence=["No judgment found at coordinate"],
                obstruction=f"Coordinate {coordinate!r} absent from judgment section",
                gap_severity=GAP_SEVERITY_CRITICAL,
                repair_hints=[
                    f"Add judgment for coordinate {coordinate!r} to the section.",
                    "Ensure the specification coordinate labels match the site objects.",
                ],
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
            record = gap
        else:
            evidence_present = judgment_fields.get("E", [])
            if not isinstance(evidence_present, list):
                evidence_present = [evidence_present] if evidence_present else []
            evidence_kinds = _evidence_kinds_from_judgment(judgment_fields)
            judgment_trust = str(judgment_fields.get("T", TRUST_TIER_UNVERIFIED))
            blocking_conditions = judgment_fields.get("B", None)
            blocking_reason: str | None = None

            # Check for hard blocking condition.
            if blocking_conditions:
                blocking_reason = str(blocking_conditions)

            formula_ok = _check_formula_match(formula, judgment_fields)
            trust_ok = _check_trust_threshold(judgment_trust, required_trust)

            if not formula_ok:
                # Formula mismatch → gap.
                gap_kind = "FORMULA_MISMATCH"
                missing = [f"Expected formula matching {formula!r}"]
                obstruction = blocking_reason
                severity = _classify_gap_severity(missing, obstruction)
                hints = [
                    f"Update judgment phi to match or subsume {formula!r}.",
                    "Check that the specification formula uses normalised notation.",
                ]
                gap = ClauseGapRecord(
                    gap_id=_make_gap_id(clause_id, gap_kind),
                    clause_id=clause_id,
                    coordinate=coordinate,
                    gap_kind=gap_kind,
                    missing_evidence=missing,
                    obstruction=obstruction,
                    gap_severity=severity,
                    repair_hints=hints,
                    created_at=_now_iso(),
                    updated_at=_now_iso(),
                )
                record = gap
            elif blocking_reason:
                # Blocked — record as UNSATISFIED with reason.
                rec = ClauseTruthRecord(
                    record_id=_make_record_id(clause_id, coordinate),
                    clause_id=clause_id,
                    coordinate=coordinate,
                    evaluation_status=EVALUATION_STATUS_UNSATISFIED,
                    evidence_present=list(evidence_present),
                    evidence_kinds=evidence_kinds,
                    trust_tier=judgment_trust,
                    blocking_reason=blocking_reason,
                    record_timestamp=_now_iso(),
                )
                record = rec
            elif not evidence_present:
                # No evidence — gap unless formula is vacuous.
                if formula == "*" or not formula:
                    # Vacuously satisfied.
                    rec = ClauseTruthRecord(
                        record_id=_make_record_id(clause_id, coordinate),
                        clause_id=clause_id,
                        coordinate=coordinate,
                        evaluation_status=EVALUATION_STATUS_SATISFIED,
                        evidence_present=[],
                        evidence_kinds=[TruthEvaluationKind.VACUOUS.value],
                        trust_tier=judgment_trust,
                        blocking_reason=None,
                        record_timestamp=_now_iso(),
                    )
                    record = rec
                else:
                    gap_kind = "MISSING_EVIDENCE"
                    missing = [f"No evidence for formula {formula!r}"]
                    severity = _classify_gap_severity(missing, None)
                    hints = [
                        "Attach evidence to the judgment's E component.",
                        "Run evidence collection workflow for this coordinate.",
                    ]
                    gap = ClauseGapRecord(
                        gap_id=_make_gap_id(clause_id, gap_kind),
                        clause_id=clause_id,
                        coordinate=coordinate,
                        gap_kind=gap_kind,
                        missing_evidence=missing,
                        obstruction=None,
                        gap_severity=severity,
                        repair_hints=hints,
                        created_at=_now_iso(),
                        updated_at=_now_iso(),
                    )
                    record = gap
            elif self.strict_mode and not trust_ok:
                # Evidence present but trust tier insufficient.
                gap_kind = "TRUST_BELOW_THRESHOLD"
                missing = [
                    f"Trust tier {judgment_trust!r} below required {required_trust!r}"
                ]
                severity = _classify_gap_severity(missing, None)
                hints = [
                    f"Elevate judgment trust tier from {judgment_trust!r} to at least {required_trust!r}.",
                    "Request re-verification by an authorised agent.",
                    "Consider lowering the required trust threshold in the specification.",
                ]
                gap = ClauseGapRecord(
                    gap_id=_make_gap_id(clause_id, gap_kind),
                    clause_id=clause_id,
                    coordinate=coordinate,
                    gap_kind=gap_kind,
                    missing_evidence=missing,
                    obstruction=None,
                    gap_severity=severity,
                    repair_hints=hints,
                    created_at=_now_iso(),
                    updated_at=_now_iso(),
                )
                record = gap
            else:
                # All checks pass.
                if self.strict_mode:
                    status = EVALUATION_STATUS_SATISFIED
                else:
                    # Lenient mode: PARTIAL if trust is below threshold.
                    status = (
                        EVALUATION_STATUS_SATISFIED
                        if trust_ok
                        else EVALUATION_STATUS_PARTIAL
                    )
                rec = ClauseTruthRecord(
                    record_id=_make_record_id(clause_id, coordinate),
                    clause_id=clause_id,
                    coordinate=coordinate,
                    evaluation_status=status,
                    evidence_present=list(evidence_present),
                    evidence_kinds=evidence_kinds,
                    trust_tier=judgment_trust,
                    blocking_reason=None,
                    record_timestamp=_now_iso(),
                )
                record = rec

        # Store result.
        self.clause_truth_table[clause_id] = record
        if clause_id not in self.evaluation_order:
            self.evaluation_order.append(clause_id)
        self.evaluation_log.append({
            "event": "evaluate_clause",
            "clause_id": clause_id,
            "coordinate": coordinate,
            "result_type": "gap" if isinstance(record, ClauseGapRecord) else "record",
            "timestamp": _now_iso(),
        })
        return record

    def evaluate_all_clauses(
        self, clauses: list[dict]
    ) -> ClausewiseTruthTable:
        """Evaluate every clause in *clauses* and return the truth table.

        Parameters
        ----------
        clauses : list[dict]
            Ordered list of clause dicts.  Each must have at minimum a
            ``clause_id`` and ``coordinate`` key.

        Returns
        -------
        ClausewiseTruthTable
            The fully populated truth table for this evaluation run.
        """
        spec_id = clauses[0].get("spec_id", "unknown") if clauses else "unknown"
        table = ClausewiseTruthTable(
            table_id=_make_table_id(spec_id),
            spec_id=spec_id,
        )
        for idx, clause_dict in enumerate(clauses):
            result = self.evaluate_clause(clause_dict)
            is_gap = isinstance(result, ClauseGapRecord)
            truth_rec_dict = (
                result.to_dict() if isinstance(result, (ClauseGapRecord, ClauseTruthRecord)) else {}
            )
            entry = ClausewiseTruthEntry(
                entry_id=str(uuid.uuid4()),
                clause_id=clause_dict.get("clause_id", ""),
                is_gap=is_gap,
                truth_record=truth_rec_dict,
                evaluation_metadata={
                    "kind": TruthEvaluationKind.DIRECT.value,
                    "strict_mode": self.strict_mode,
                    "trust_threshold": self.trust_threshold,
                },
                evaluation_order_index=idx,
                entry_timestamp=_now_iso(),
            )
            table.add_entry(entry)
        self.evaluation_log.append({
            "event": "evaluate_all_clauses",
            "clause_count": len(clauses),
            "gap_count": table.gap_count,
            "witness_count": table.witness_count,
            "timestamp": _now_iso(),
        })
        return table

    def get_truth_table(self) -> ClausewiseTruthTable:
        """Build a ``ClausewiseTruthTable`` from the accumulated clause results.

        Returns
        -------
        ClausewiseTruthTable
            A snapshot of the current accumulated results.
        """
        spec_id = "coordinator-snapshot"
        table = ClausewiseTruthTable(
            table_id=_make_table_id(spec_id),
            spec_id=spec_id,
        )
        for idx, clause_id in enumerate(self.evaluation_order):
            result = self.clause_truth_table.get(clause_id)
            if result is None:
                continue
            is_gap = isinstance(result, ClauseGapRecord)
            truth_rec_dict = result.to_dict() if hasattr(result, "to_dict") else {}
            entry = ClausewiseTruthEntry(
                entry_id=str(uuid.uuid4()),
                clause_id=clause_id,
                is_gap=is_gap,
                truth_record=truth_rec_dict,
                evaluation_metadata={"kind": TruthEvaluationKind.DIRECT.value},
                evaluation_order_index=idx,
                entry_timestamp=_now_iso(),
            )
            table.add_entry(entry)
        return table

    def get_satisfied_clauses(self) -> list[str]:
        """Return the clause ids for all currently satisfied clauses.

        Returns
        -------
        list[str]
            Clause ids where the stored result is a ``ClauseTruthRecord`` with
            status SATISFIED or PARTIAL.
        """
        satisfied = []
        for clause_id, result in self.clause_truth_table.items():
            if isinstance(result, ClauseTruthRecord):
                if result.evaluation_status in (
                    EVALUATION_STATUS_SATISFIED,
                    EVALUATION_STATUS_PARTIAL,
                ):
                    satisfied.append(clause_id)
        return satisfied

    def get_unsatisfied_clauses(self) -> list[str]:
        """Return the clause ids for all currently unsatisfied clauses.

        Returns
        -------
        list[str]
            Clause ids where the stored result is a ``ClauseGapRecord`` or a
            ``ClauseTruthRecord`` with status UNSATISFIED.
        """
        unsatisfied = []
        for clause_id, result in self.clause_truth_table.items():
            if isinstance(result, ClauseGapRecord):
                unsatisfied.append(clause_id)
            elif isinstance(result, ClauseTruthRecord):
                if result.evaluation_status == EVALUATION_STATUS_UNSATISFIED:
                    unsatisfied.append(clause_id)
        return unsatisfied

    def compute_overall_status(self) -> str:
        """Compute the aggregate satisfaction status across all evaluated clauses.

        Returns
        -------
        str
            One of the three ``EVALUATION_STATUS_*`` constants:
            * ``SATISFIED`` — every clause is satisfied.
            * ``PARTIAL`` — some clauses are satisfied; some are not.
            * ``UNSATISFIED`` — no clauses are satisfied or none evaluated.
        """
        if not self.clause_truth_table:
            return EVALUATION_STATUS_UNSATISFIED
        satisfied = self.get_satisfied_clauses()
        unsatisfied = self.get_unsatisfied_clauses()
        total = len(self.clause_truth_table)
        if len(satisfied) == total:
            return EVALUATION_STATUS_SATISFIED
        if satisfied:
            return EVALUATION_STATUS_PARTIAL
        return EVALUATION_STATUS_UNSATISFIED

    def reset(self) -> None:
        """Clear all accumulated evaluation state.

        Configuration fields ``strict_mode`` and ``trust_threshold`` are
        preserved; ``coordinator_id`` is also preserved.  The judgment section
        is cleared because it is considered evaluation-time state.
        """
        self.judgment_section = {}
        self.clause_truth_table = {}
        self.evaluation_order = []
        self.evaluation_log = []


@dataclass(slots=True)
class ClausewiseTruthAnalyzer:
    """Analyzes a clausewise truth table to identify patterns and improvements.

    The analyzer takes a snapshot of a ``ClausewiseTruthTable`` and performs
    higher-order analysis: it identifies structural patterns among gap records
    (e.g. all gaps at the same coordinate, or all gaps of the same kind),
    detects logical dependencies between clauses, and suggests prioritised
    repair actions.

    Parameters
    ----------
    analyzer_id : str
        UUID-based unique identifier for this analyzer instance.
    analysis_log : list
        Append-only log of analysis steps performed.
    truth_table_snapshot : dict
        A serialised snapshot of the truth table under analysis
        (``clause_id → entry_dict``).
    dependency_graph : dict
        Mapping of ``clause_id → list[clause_id]`` representing inferred
        ordering dependencies between clauses.
    gap_patterns : list
        Identified structural patterns among gap records.
    witness_patterns : list
        Identified structural patterns among satisfied records.
    coverage_metrics : dict
        Computed coverage statistics derived from the truth table.

    Methods
    -------
    analyze_truth_table(table: ClausewiseTruthTable) -> dict
        Run the full analysis suite and return a report dict.
    identify_gap_patterns(gaps: list[ClauseGapRecord]) -> list[dict]
        Find structural patterns among gap records.
    identify_witness_patterns(records: list[ClauseTruthRecord]) -> list[dict]
        Find structural patterns among satisfied truth records.
    compute_dependency_graph(table: ClausewiseTruthTable) -> dict
        Infer clause dependency ordering from coordinates and formulas.
    suggest_improvements(table: ClausewiseTruthTable) -> list[str]
        Produce a ranked list of improvement suggestions.
    produce_analysis_report() -> dict
        Return the full accumulated analysis as a structured dict.
    rank_gaps_by_severity(gaps: list[ClauseGapRecord]) -> list[ClauseGapRecord]
        Return gaps sorted by descending severity.
    """

    analyzer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    analysis_log: list = field(default_factory=list)
    truth_table_snapshot: dict = field(default_factory=dict)
    dependency_graph: dict = field(default_factory=dict)
    gap_patterns: list = field(default_factory=list)
    witness_patterns: list = field(default_factory=list)
    coverage_metrics: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze_truth_table(self, table: ClausewiseTruthTable) -> dict:
        """Run the full analysis suite on *table* and return a report.

        This method orchestrates the other analysis methods in order:
        1. Snapshot the table.
        2. Extract gap and witness records.
        3. Identify patterns.
        4. Compute the dependency graph.
        5. Compute coverage metrics.
        6. Produce suggestions.
        7. Compile and return the full report.

        Parameters
        ----------
        table : ClausewiseTruthTable
            The truth table to analyse.

        Returns
        -------
        dict
            A structured analysis report.  See ``produce_analysis_report``.
        """
        # Snapshot.
        self.truth_table_snapshot = {
            entry.clause_id: {
                "is_gap": entry.is_gap,
                "truth_record": entry.truth_record,
                "order_index": entry.evaluation_order_index,
            }
            for entry in table.entries
        }

        gaps = gap_records_from_table(table)
        witnesses = witness_records_from_table(table)

        self.gap_patterns = self.identify_gap_patterns(gaps)
        self.witness_patterns = self.identify_witness_patterns(witnesses)
        self.dependency_graph = self.compute_dependency_graph(table)

        # Coverage metrics.
        total = len(table.entries)
        self.coverage_metrics = {
            "total_clauses": total,
            "satisfied": table.witness_count,
            "unsatisfied": table.gap_count,
            "satisfaction_fraction": table.compute_satisfaction_fraction(),
            "gap_patterns_found": len(self.gap_patterns),
            "witness_patterns_found": len(self.witness_patterns),
        }

        self.analysis_log.append({
            "event": "analyze_truth_table",
            "table_id": table.table_id,
            "total": total,
            "timestamp": _now_iso(),
        })
        return self.produce_analysis_report()

    def identify_gap_patterns(self, gaps: list[ClauseGapRecord]) -> list[dict]:
        """Identify structural patterns among a collection of gap records.

        Patterns detected:
        * ``COORDINATE_CLUSTER`` — multiple gaps at the same coordinate.
        * ``KIND_CLUSTER`` — multiple gaps of the same gap_kind.
        * ``SEVERITY_CLUSTER`` — multiple gaps at the same severity level.
        * ``OBSTRUCTION_CLUSTER`` — multiple gaps sharing an obstruction.

        Parameters
        ----------
        gaps : list[ClauseGapRecord]
            The gap records to analyse.

        Returns
        -------
        list[dict]
            A list of pattern dicts, each with keys: ``pattern_type``,
            ``key``, ``clause_ids``, ``count``.
        """
        if not gaps:
            return []

        patterns: list[dict] = []

        # Group by coordinate.
        coord_map: dict[str, list[str]] = {}
        for gap in gaps:
            coord_map.setdefault(gap.coordinate, []).append(gap.clause_id)
        for coord, ids in coord_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "COORDINATE_CLUSTER",
                    "key": coord,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        # Group by gap_kind.
        kind_map: dict[str, list[str]] = {}
        for gap in gaps:
            kind_map.setdefault(gap.gap_kind, []).append(gap.clause_id)
        for kind, ids in kind_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "KIND_CLUSTER",
                    "key": kind,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        # Group by severity.
        sev_map: dict[str, list[str]] = {}
        for gap in gaps:
            sev_map.setdefault(gap.gap_severity, []).append(gap.clause_id)
        for sev, ids in sev_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "SEVERITY_CLUSTER",
                    "key": sev,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        # Group by obstruction.
        obs_map: dict[str, list[str]] = {}
        for gap in gaps:
            if gap.obstruction:
                obs_map.setdefault(gap.obstruction, []).append(gap.clause_id)
        for obs, ids in obs_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "OBSTRUCTION_CLUSTER",
                    "key": obs,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        return patterns

    def identify_witness_patterns(
        self, records: list[ClauseTruthRecord]
    ) -> list[dict]:
        """Identify structural patterns among satisfied truth records.

        Patterns detected:
        * ``SHARED_TRUST_TIER`` — multiple clauses satisfied at the same trust tier.
        * ``SHARED_EVIDENCE_KIND`` — multiple clauses sharing an evidence kind.
        * ``COORDINATE_CLUSTER`` — multiple clauses satisfied at the same coordinate.

        Parameters
        ----------
        records : list[ClauseTruthRecord]
            The truth records to analyse.

        Returns
        -------
        list[dict]
            A list of pattern dicts with keys: ``pattern_type``, ``key``,
            ``clause_ids``, ``count``.
        """
        if not records:
            return []

        patterns: list[dict] = []

        # Group by trust tier.
        tier_map: dict[str, list[str]] = {}
        for rec in records:
            tier_map.setdefault(rec.trust_tier, []).append(rec.clause_id)
        for tier, ids in tier_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "SHARED_TRUST_TIER",
                    "key": tier,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        # Group by evidence kind (flatten all kinds across records).
        kind_to_ids: dict[str, list[str]] = {}
        for rec in records:
            for kind in rec.evidence_kinds:
                kind_to_ids.setdefault(kind, []).append(rec.clause_id)
        for kind, ids in kind_to_ids.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "SHARED_EVIDENCE_KIND",
                    "key": kind,
                    "clause_ids": sorted(set(ids)),
                    "count": len(set(ids)),
                })

        # Group by coordinate.
        coord_map: dict[str, list[str]] = {}
        for rec in records:
            coord_map.setdefault(rec.coordinate, []).append(rec.clause_id)
        for coord, ids in coord_map.items():
            if len(ids) >= 2:
                patterns.append({
                    "pattern_type": "COORDINATE_CLUSTER",
                    "key": coord,
                    "clause_ids": sorted(ids),
                    "count": len(ids),
                })

        return patterns

    def compute_dependency_graph(
        self, table: ClausewiseTruthTable
    ) -> dict:
        """Infer a dependency ordering between clauses from the truth table.

        The dependency graph is computed heuristically: clauses at the same
        coordinate as a gap are considered upstream dependencies of clauses at
        later coordinates in the evaluation order.  The graph maps each
        clause id to a (possibly empty) list of clause ids that should be
        repaired first.

        Parameters
        ----------
        table : ClausewiseTruthTable
            The truth table to analyse.

        Returns
        -------
        dict
            Mapping of ``clause_id → list[clause_id]``.
        """
        graph: dict[str, list[str]] = {}
        # Build a coordinate-to-clause index from the snapshot.
        coord_to_clauses: dict[str, list[str]] = {}
        for entry in table.entries:
            coord = entry.truth_record.get("coordinate", "")
            coord_to_clauses.setdefault(coord, []).append(entry.clause_id)
            graph[entry.clause_id] = []

        # For each gap entry, mark all later (by evaluation_order_index) same-
        # coordinate non-gap entries as depending on it.
        gap_entries = [e for e in table.entries if e.is_gap]
        witness_entries = [e for e in table.entries if not e.is_gap]

        for gap_entry in gap_entries:
            gap_coord = gap_entry.truth_record.get("coordinate", "")
            for witness_entry in witness_entries:
                w_coord = witness_entry.truth_record.get("coordinate", "")
                if (
                    w_coord == gap_coord
                    and witness_entry.evaluation_order_index
                    > gap_entry.evaluation_order_index
                ):
                    deps = graph.setdefault(witness_entry.clause_id, [])
                    if gap_entry.clause_id not in deps:
                        deps.append(gap_entry.clause_id)

        return graph

    def suggest_improvements(self, table: ClausewiseTruthTable) -> list[str]:
        """Produce a ranked list of improvement suggestions for the truth table.

        Suggestions are derived from gap records, gap patterns, and coverage
        metrics.  They are returned in priority order (most impactful first).

        Parameters
        ----------
        table : ClausewiseTruthTable
            The truth table under analysis.

        Returns
        -------
        list[str]
            Human-readable improvement suggestions.
        """
        suggestions: list[str] = []
        gaps = gap_records_from_table(table)
        ranked = self.rank_gaps_by_severity(gaps)

        for gap in ranked[:5]:
            for hint in gap.repair_hints[:2]:
                if hint not in suggestions:
                    suggestions.append(hint)

        # Pattern-level suggestions.
        for pattern in self.gap_patterns:
            p_type = pattern.get("pattern_type", "")
            key = pattern.get("key", "")
            count = pattern.get("count", 0)
            if p_type == "COORDINATE_CLUSTER":
                suggestions.append(
                    f"Address {count} gaps at coordinate {key!r} together "
                    f"to amortise repair cost."
                )
            elif p_type == "KIND_CLUSTER":
                suggestions.append(
                    f"All {count} gaps of kind {key!r} may share a single "
                    f"systemic fix."
                )
            elif p_type == "OBSTRUCTION_CLUSTER":
                suggestions.append(
                    f"Remove shared obstruction {key!r} to unblock {count} clauses."
                )

        frac = table.compute_satisfaction_fraction()
        if frac < 0.5:
            suggestions.append(
                "Fewer than half of clauses are satisfied; consider restructuring "
                "the specification to target achievable sub-goals first."
            )
        elif frac < 1.0:
            suggestions.append(
                f"{table.gap_count} remaining gap(s) — run targeted evidence "
                f"collection to reach full satisfaction."
            )

        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    def produce_analysis_report(self) -> dict:
        """Return the full accumulated analysis as a structured dict.

        Returns
        -------
        dict
            Keys: analyzer_id, truth_table_snapshot (summary), dependency_graph,
            gap_patterns, witness_patterns, coverage_metrics, analysis_log_length.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "snapshot_clause_count": len(self.truth_table_snapshot),
            "dependency_graph": dict(self.dependency_graph),
            "gap_patterns": list(self.gap_patterns),
            "witness_patterns": list(self.witness_patterns),
            "coverage_metrics": dict(self.coverage_metrics),
            "analysis_log_length": len(self.analysis_log),
        }

    def rank_gaps_by_severity(
        self, gaps: list[ClauseGapRecord]
    ) -> list[ClauseGapRecord]:
        """Return *gaps* sorted by descending severity.

        The severity ladder is CRITICAL > HIGH > MEDIUM > LOW.  Gaps with
        unknown severity are ranked last.

        Parameters
        ----------
        gaps : list[ClauseGapRecord]
            The gap records to rank.

        Returns
        -------
        list[ClauseGapRecord]
            A new list sorted with the most severe gaps first.
        """
        severity_rank = {
            GAP_SEVERITY_CRITICAL: 3,
            GAP_SEVERITY_HIGH: 2,
            GAP_SEVERITY_MEDIUM: 1,
            GAP_SEVERITY_LOW: 0,
        }
        return sorted(
            gaps,
            key=lambda g: severity_rank.get(g.gap_severity, -1),
            reverse=True,
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def evaluate_clause_against_judgment(
    clause_dict: dict, judgment_fields: dict
) -> ClauseTruthRecord | ClauseGapRecord:
    """Evaluate a specification clause against a single judgment field dict.

    This is the low-level, stateless version of the evaluation.  It does not
    require a ``ClausewiseTruthCoordinator`` and is suitable for direct use in
    functional pipelines.

    The evaluation logic:
    1. Extract and normalise evidence from ``judgment_fields``.
    2. Check formula match via ``_check_formula_match``.
    3. Inspect blocking conditions (``B`` component).
    4. Classify as SATISFIED, PARTIAL, or UNSATISFIED and produce the
       appropriate record type.

    Parameters
    ----------
    clause_dict : dict
        A clause description with keys: ``clause_id``, ``coordinate``,
        ``formula``, optionally ``required_trust``.
    judgment_fields : dict
        The judgment 8-tuple as a dict with keys from ``JUDGMENT_COMPONENTS``.

    Returns
    -------
    ClauseTruthRecord | ClauseGapRecord
        A witness record if the clause is satisfied (or partially so), or a
        gap record if it is not.
    """
    coordinator = ClausewiseTruthCoordinator(
        strict_mode=True,
        trust_threshold=clause_dict.get("required_trust", TRUST_TIER_TRUSTED),
    )
    coordinate = str(clause_dict.get("coordinate", ""))
    coordinator.load_judgment_section({coordinate: judgment_fields})
    return coordinator.evaluate_clause(clause_dict)


def build_clausewise_truth_table(
    clauses: list[dict], judgment_section: dict
) -> ClausewiseTruthTable:
    """Evaluate *clauses* against *judgment_section* and return the truth table.

    This is the primary entry-point for batch clausewise evaluation.  It
    creates a fresh ``ClausewiseTruthCoordinator``, loads the section, and
    evaluates every clause in order.

    Parameters
    ----------
    clauses : list[dict]
        Ordered list of clause dicts.  Each must contain at minimum
        ``clause_id``, ``coordinate``, and ``formula``.
    judgment_section : dict
        Mapping of ``coordinate_str → judgment_fields_dict``.

    Returns
    -------
    ClausewiseTruthTable
        A fully populated clausewise truth table.
    """
    coordinator = ClausewiseTruthCoordinator()
    coordinator.load_judgment_section(judgment_section)
    return coordinator.evaluate_all_clauses(clauses)


def merge_truth_tables(
    left: ClausewiseTruthTable, right: ClausewiseTruthTable
) -> ClausewiseTruthTable:
    """Merge two ``ClausewiseTruthTable`` instances into a single table.

    Entries from *right* override entries from *left* when the same
    ``clause_id`` appears in both.  The merged table carries a fresh
    ``table_id`` and records the spec_id from *left* (assumed to be the
    primary table).

    Parameters
    ----------
    left : ClausewiseTruthTable
        The base table.  Its spec_id is used for the merged result.
    right : ClausewiseTruthTable
        The table to merge in.  Entries here take precedence.

    Returns
    -------
    ClausewiseTruthTable
        A new merged truth table containing all entries from both inputs.
    """
    merged = ClausewiseTruthTable(
        table_id=_make_table_id(left.spec_id + "-merged"),
        spec_id=left.spec_id,
    )
    # Add left entries first.
    for entry in left.entries:
        merged.add_entry(entry)
    # Right entries override.
    for entry in right.entries:
        merged.add_entry(entry)
    return merged


def compute_overall_satisfaction_status(table: ClausewiseTruthTable) -> str:
    """Compute a single aggregate satisfaction status string for *table*.

    Status rules (applied in order):
    * ``SATISFIED`` — zero gap entries and at least one witness entry.
    * ``UNSATISFIED`` — zero witness entries.
    * ``PARTIAL`` — otherwise (mix of gaps and witnesses).

    Parameters
    ----------
    table : ClausewiseTruthTable
        The truth table to assess.

    Returns
    -------
    str
        One of the three ``EVALUATION_STATUS_*`` constants.
    """
    total = len(table.entries)
    if total == 0:
        return EVALUATION_STATUS_UNSATISFIED
    if table.gap_count == 0 and table.witness_count > 0:
        return EVALUATION_STATUS_SATISFIED
    if table.witness_count == 0:
        return EVALUATION_STATUS_UNSATISFIED
    return EVALUATION_STATUS_PARTIAL


def gap_records_from_table(table: ClausewiseTruthTable) -> list[ClauseGapRecord]:
    """Extract and reconstruct all gap records from *table*.

    Each entry with ``is_gap=True`` has its ``truth_record`` dict used to
    reconstruct a ``ClauseGapRecord``.  If a required field is absent the
    default from ``ClauseGapRecord``'s schema is substituted.

    Parameters
    ----------
    table : ClausewiseTruthTable
        The truth table to extract gap records from.

    Returns
    -------
    list[ClauseGapRecord]
        Reconstructed gap records in evaluation order.
    """
    records: list[ClauseGapRecord] = []
    for entry in table.entries:
        if not entry.is_gap:
            continue
        d = entry.truth_record
        now = _now_iso()
        gap = ClauseGapRecord(
            gap_id=d.get("gap_id", _make_gap_id(entry.clause_id, "UNKNOWN")),
            clause_id=d.get("clause_id", entry.clause_id),
            coordinate=d.get("coordinate", ""),
            gap_kind=d.get("gap_kind", "UNKNOWN"),
            missing_evidence=list(d.get("missing_evidence", [])),
            obstruction=d.get("obstruction"),
            gap_severity=d.get("gap_severity", GAP_SEVERITY_MEDIUM),
            repair_hints=list(d.get("repair_hints", [])),
            created_at=d.get("created_at", now),
            updated_at=d.get("updated_at", now),
        )
        records.append(gap)
    return records


def witness_records_from_table(
    table: ClausewiseTruthTable,
) -> list[ClauseTruthRecord]:
    """Extract and reconstruct all truth (witness) records from *table*.

    Each entry with ``is_gap=False`` has its ``truth_record`` dict used to
    reconstruct a ``ClauseTruthRecord``.

    Parameters
    ----------
    table : ClausewiseTruthTable
        The truth table to extract truth records from.

    Returns
    -------
    list[ClauseTruthRecord]
        Reconstructed truth records in evaluation order.
    """
    records: list[ClauseTruthRecord] = []
    for entry in table.entries:
        if entry.is_gap:
            continue
        d = entry.truth_record
        now = _now_iso()
        rec = ClauseTruthRecord(
            record_id=d.get(
                "record_id",
                _make_record_id(entry.clause_id, d.get("coordinate", "")),
            ),
            clause_id=d.get("clause_id", entry.clause_id),
            coordinate=d.get("coordinate", ""),
            evaluation_status=d.get("evaluation_status", EVALUATION_STATUS_SATISFIED),
            evidence_present=list(d.get("evidence_present", [])),
            evidence_kinds=list(d.get("evidence_kinds", [])),
            trust_tier=d.get("trust_tier", TRUST_TIER_UNVERIFIED),
            blocking_reason=d.get("blocking_reason"),
            record_timestamp=d.get("record_timestamp", now),
        )
        records.append(rec)
    return records


def clausewise_truth_witness_from_table(
    table: ClausewiseTruthTable, spec_id: str
) -> ClausewiseTruthWitness:
    """Construct a ``ClausewiseTruthWitness`` from a completed truth table.

    The witness record is the authoritative summary of the clausewise
    evaluation for a specification.  It captures all per-clause records,
    computes the overall status, and identifies blocking clauses.

    Parameters
    ----------
    table : ClausewiseTruthTable
        The fully populated truth table.
    spec_id : str
        The specification identifier to record in the witness.

    Returns
    -------
    ClausewiseTruthWitness
        An immutable witness record for the evaluation.
    """
    gaps = gap_records_from_table(table)
    witnesses = witness_records_from_table(table)

    # Build per-clause records dict (clause_id → serialised record).
    clause_truth_records: dict = {}
    for entry in table.entries:
        clause_truth_records[entry.clause_id] = {
            "is_gap": entry.is_gap,
            "record": entry.truth_record,
        }

    # Build per-clause trust tiers.
    per_clause_trust_tiers: dict = {}
    for rec in witnesses:
        per_clause_trust_tiers[rec.clause_id] = rec.trust_tier
    for gap in gaps:
        per_clause_trust_tiers[gap.clause_id] = TRUST_TIER_UNVERIFIED

    overall_status = compute_overall_satisfaction_status(table)

    # Compute blocking clauses from reconstructed mapping.
    mixed_records: dict = {}
    for rec in witnesses:
        mixed_records[rec.clause_id] = rec
    for gap in gaps:
        mixed_records[gap.clause_id] = gap
    blocking = _compute_blocking_clauses(mixed_records)

    global_achievable = (
        overall_status == EVALUATION_STATUS_SATISFIED and len(blocking) == 0
    )

    return ClausewiseTruthWitness(
        witness_id=str(uuid.uuid4()),
        spec_id=spec_id,
        clause_count=len(table.entries),
        satisfied_count=table.witness_count,
        unsatisfied_count=table.gap_count,
        clause_truth_records=clause_truth_records,
        per_clause_trust_tiers=per_clause_trust_tiers,
        overall_status=overall_status,
        global_section_achievable=global_achievable,
        blocking_clauses=blocking,
        witness_timestamp=_now_iso(),
        provenance=(
            f"clausewise_truth_witness_from_table(table_id={table.table_id!r}, "
            f"spec_id={spec_id!r})"
        ),
    )


def rank_gaps(gaps: list[ClauseGapRecord]) -> list[ClauseGapRecord]:
    """Return *gaps* ranked by descending severity.

    This is a module-level convenience wrapper around
    ``ClausewiseTruthAnalyzer.rank_gaps_by_severity``.

    Parameters
    ----------
    gaps : list[ClauseGapRecord]
        The gap records to rank.

    Returns
    -------
    list[ClauseGapRecord]
        A new list with the most severe gaps first.
    """
    severity_rank = {
        GAP_SEVERITY_CRITICAL: 3,
        GAP_SEVERITY_HIGH: 2,
        GAP_SEVERITY_MEDIUM: 1,
        GAP_SEVERITY_LOW: 0,
    }
    return sorted(
        gaps,
        key=lambda g: severity_rank.get(g.gap_severity, -1),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== clausewise_truth smoke test ===")

    # ------------------------------------------------------------------
    # 1. Internal helpers
    # ------------------------------------------------------------------
    now = _now_iso()
    assert isinstance(now, str) and "T" in now, "ISO timestamp malformed"

    h = _stable_hash("hello")
    assert len(h) == 64, "SHA-256 digest should be 64 hex chars"
    assert h == _stable_hash("hello"), "_stable_hash must be deterministic"

    rid = _make_record_id("clause-1", "coord-A")
    assert rid.startswith("ctr-"), f"record id prefix wrong: {rid}"

    gid = _make_gap_id("clause-2", "MISSING_EVIDENCE")
    assert gid.startswith("cgr-"), f"gap id prefix wrong: {gid}"

    tid = _make_table_id("spec-42")
    assert tid.startswith("ctt-"), f"table id prefix wrong: {tid}"

    kinds = _evidence_kinds_from_judgment({"E": ["proof", "measurement"]})
    assert kinds == ["proof", "measurement"], f"evidence kinds wrong: {kinds}"

    kinds_empty = _evidence_kinds_from_judgment({})
    assert kinds_empty == [], f"empty evidence kinds wrong: {kinds_empty}"

    assert _check_formula_match("*", {"phi": "anything"}) is True
    assert _check_formula_match("type-A", {"phi": "type-A is provable"}) is True
    assert _check_formula_match("type-Z", {"phi": "type-A"}) is False

    assert _check_trust_threshold(TRUST_TIER_VERIFIED, TRUST_TIER_TRUSTED) is True
    assert _check_trust_threshold(TRUST_TIER_UNVERIFIED, TRUST_TIER_TRUSTED) is False
    assert _check_trust_threshold(TRUST_TIER_PROPOSED, TRUST_TIER_PROPOSED) is True

    sev = _classify_gap_severity([], None)
    assert sev == GAP_SEVERITY_LOW, f"empty missing → LOW, got {sev}"
    sev_crit = _classify_gap_severity([], "hard block")
    assert sev_crit == GAP_SEVERITY_CRITICAL, f"obstruction → CRITICAL, got {sev_crit}"
    sev_high = _classify_gap_severity(["a", "b", "c"], None)
    assert sev_high == GAP_SEVERITY_HIGH, f"3 missing → HIGH, got {sev_high}"

    print("  helpers: OK")

    # ------------------------------------------------------------------
    # 2. TruthEvaluationKind enum
    # ------------------------------------------------------------------
    assert TruthEvaluationKind.DIRECT.value == "DIRECT"
    assert TruthEvaluationKind.VACUOUS.value == "VACUOUS"
    assert len(TruthEvaluationKind) == 6
    print("  TruthEvaluationKind: OK")

    # ------------------------------------------------------------------
    # 3. ClauseTruthRecord
    # ------------------------------------------------------------------
    ctr = ClauseTruthRecord(
        record_id="ctr-test",
        clause_id="c1",
        coordinate="x",
        evaluation_status=EVALUATION_STATUS_SATISFIED,
        evidence_present=["proof-1"],
        evidence_kinds=["proof"],
        trust_tier=TRUST_TIER_VERIFIED,
        blocking_reason=None,
        record_timestamp=_now_iso(),
    )
    assert ctr.is_satisfied() is True
    ctr_unsatisfied = ClauseTruthRecord(
        record_id="ctr-test2",
        clause_id="c2",
        coordinate="x",
        evaluation_status=EVALUATION_STATUS_UNSATISFIED,
        evidence_present=[],
        evidence_kinds=[],
        trust_tier=TRUST_TIER_UNVERIFIED,
        blocking_reason="hard block",
        record_timestamp=_now_iso(),
    )
    assert ctr_unsatisfied.is_satisfied() is False
    d = ctr.to_dict()
    assert d["clause_id"] == "c1"
    print("  ClauseTruthRecord: OK")

    # ------------------------------------------------------------------
    # 4. ClauseGapRecord
    # ------------------------------------------------------------------
    gap = ClauseGapRecord(
        gap_id=_make_gap_id("c3", "MISSING_EVIDENCE"),
        clause_id="c3",
        coordinate="y",
        gap_kind="MISSING_EVIDENCE",
        missing_evidence=["proof-2"],
        obstruction=None,
        gap_severity=GAP_SEVERITY_MEDIUM,
        repair_hints=[],
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    gap.add_repair_hint("attach proof-2")
    assert "attach proof-2" in gap.repair_hints
    gap.escalate_severity()
    assert gap.gap_severity == GAP_SEVERITY_HIGH
    gap.escalate_severity()
    assert gap.gap_severity == GAP_SEVERITY_CRITICAL
    gap.escalate_severity()  # no-op at CRITICAL
    assert gap.gap_severity == GAP_SEVERITY_CRITICAL
    gap_d = gap.to_dict()
    assert gap_d["gap_severity"] == GAP_SEVERITY_CRITICAL
    print("  ClauseGapRecord: OK")

    # ------------------------------------------------------------------
    # 5. ClausewiseTruthTable
    # ------------------------------------------------------------------
    table = ClausewiseTruthTable(
        table_id="ctt-test",
        spec_id="spec-1",
    )
    assert table.compute_satisfaction_fraction() == 0.0

    entry_ok = ClausewiseTruthEntry(
        entry_id=str(uuid.uuid4()),
        clause_id="c1",
        is_gap=False,
        truth_record=ctr.to_dict(),
        evaluation_metadata={"kind": TruthEvaluationKind.DIRECT.value},
        evaluation_order_index=0,
        entry_timestamp=_now_iso(),
    )
    entry_gap = ClausewiseTruthEntry(
        entry_id=str(uuid.uuid4()),
        clause_id="c3",
        is_gap=True,
        truth_record=gap.to_dict(),
        evaluation_metadata={"kind": TruthEvaluationKind.DIRECT.value},
        evaluation_order_index=1,
        entry_timestamp=_now_iso(),
    )
    table.add_entry(entry_ok)
    table.add_entry(entry_gap)
    assert table.witness_count == 1
    assert table.gap_count == 1
    frac = table.compute_satisfaction_fraction()
    assert abs(frac - 0.5) < 1e-9, f"fraction wrong: {frac}"
    assert table.get_entry_for_clause("c1") is entry_ok
    assert table.get_entry_for_clause("c99") is None
    summary = table.to_summary_dict()
    assert summary["total_clauses"] == 2

    # Replace an entry.
    entry_ok_v2 = ClausewiseTruthEntry(
        entry_id=str(uuid.uuid4()),
        clause_id="c1",
        is_gap=False,
        truth_record=ctr.to_dict(),
        evaluation_metadata={"kind": TruthEvaluationKind.INFERRED.value},
        evaluation_order_index=0,
        entry_timestamp=_now_iso(),
    )
    table.add_entry(entry_ok_v2)
    assert len(table.entries) == 2, "replacement should not grow table"
    assert table.witness_count == 1
    print("  ClausewiseTruthTable: OK")

    # ------------------------------------------------------------------
    # 6. evaluate_clause_against_judgment
    # ------------------------------------------------------------------
    clause_ok = {
        "clause_id": "clause-alpha",
        "coordinate": "coord-1",
        "formula": "P(x)",
        "required_trust": TRUST_TIER_TRUSTED,
    }
    judgment_ok = {
        "c": "coord-1",
        "phi": "P(x) holds for all x in domain",
        "A": "agent-A",
        "E": ["proof-of-P"],
        "O": [],
        "B": None,
        "T": TRUST_TIER_VERIFIED,
        "Pi": "root",
    }
    result_ok = evaluate_clause_against_judgment(clause_ok, judgment_ok)
    assert isinstance(result_ok, ClauseTruthRecord), (
        f"Expected ClauseTruthRecord, got {type(result_ok)}"
    )
    assert result_ok.is_satisfied()

    clause_no_ev = {
        "clause_id": "clause-beta",
        "coordinate": "coord-2",
        "formula": "Q(y)",
    }
    judgment_no_ev = {
        "c": "coord-2",
        "phi": "Q(y)",
        "A": "agent-B",
        "E": [],
        "O": [],
        "B": None,
        "T": TRUST_TIER_TRUSTED,
        "Pi": "root",
    }
    result_no_ev = evaluate_clause_against_judgment(clause_no_ev, judgment_no_ev)
    assert isinstance(result_no_ev, ClauseGapRecord), (
        f"Expected ClauseGapRecord, got {type(result_no_ev)}"
    )
    assert result_no_ev.gap_kind == "MISSING_EVIDENCE"

    clause_mismatch = {
        "clause_id": "clause-gamma",
        "coordinate": "coord-3",
        "formula": "R(z)",
    }
    judgment_mismatch = {
        "c": "coord-3",
        "phi": "S(w)",
        "A": "agent-C",
        "E": ["some-proof"],
        "O": [],
        "B": None,
        "T": TRUST_TIER_VERIFIED,
        "Pi": "root",
    }
    result_mismatch = evaluate_clause_against_judgment(clause_mismatch, judgment_mismatch)
    assert isinstance(result_mismatch, ClauseGapRecord)
    assert result_mismatch.gap_kind == "FORMULA_MISMATCH"

    clause_blocked = {
        "clause_id": "clause-delta",
        "coordinate": "coord-4",
        "formula": "T(v)",
    }
    judgment_blocked = {
        "c": "coord-4",
        "phi": "T(v)",
        "A": "agent-D",
        "E": ["ev-T"],
        "O": [],
        "B": "awaiting external verification",
        "T": TRUST_TIER_TRUSTED,
        "Pi": "root",
    }
    result_blocked = evaluate_clause_against_judgment(clause_blocked, judgment_blocked)
    assert isinstance(result_blocked, ClauseTruthRecord)
    assert result_blocked.evaluation_status == EVALUATION_STATUS_UNSATISFIED
    assert result_blocked.blocking_reason is not None

    print("  evaluate_clause_against_judgment: OK")

    # ------------------------------------------------------------------
    # 7. build_clausewise_truth_table
    # ------------------------------------------------------------------
    clauses_all = [
        {
            "spec_id": "spec-smoke",
            "clause_id": "clause-1",
            "coordinate": "coord-A",
            "formula": "F1",
        },
        {
            "spec_id": "spec-smoke",
            "clause_id": "clause-2",
            "coordinate": "coord-B",
            "formula": "*",
        },
        {
            "spec_id": "spec-smoke",
            "clause_id": "clause-3",
            "coordinate": "coord-C",
            "formula": "F3",
        },
    ]
    section_all = {
        "coord-A": {
            "c": "coord-A",
            "phi": "F1 is established",
            "A": "agent",
            "E": ["proof-F1"],
            "O": [],
            "B": None,
            "T": TRUST_TIER_VERIFIED,
            "Pi": "root",
        },
        "coord-B": {
            "c": "coord-B",
            "phi": "anything",
            "A": "agent",
            "E": [],
            "O": [],
            "B": None,
            "T": TRUST_TIER_TRUSTED,
            "Pi": "root",
        },
        # coord-C intentionally missing → gap
    }
    built_table = build_clausewise_truth_table(clauses_all, section_all)
    assert len(built_table.entries) == 3
    # clause-1: formula match + evidence → SATISFIED
    e1 = built_table.get_entry_for_clause("clause-1")
    assert e1 is not None and not e1.is_gap, "clause-1 should be witness"
    # clause-2: wildcard formula + no evidence but vacuously satisfied
    e2 = built_table.get_entry_for_clause("clause-2")
    assert e2 is not None and not e2.is_gap, "clause-2 should be vacuous witness"
    # clause-3: missing coordinate → gap
    e3 = built_table.get_entry_for_clause("clause-3")
    assert e3 is not None and e3.is_gap, "clause-3 should be gap"
    print("  build_clausewise_truth_table: OK")

    # ------------------------------------------------------------------
    # 8. merge_truth_tables
    # ------------------------------------------------------------------
    table_left = build_clausewise_truth_table(clauses_all[:2], section_all)
    table_right = build_clausewise_truth_table(clauses_all[1:], section_all)
    merged = merge_truth_tables(table_left, table_right)
    # Should contain clause-1 (from left), clause-2 (both, right wins), clause-3 (right only).
    assert len(merged.entries) == 3
    assert merged.get_entry_for_clause("clause-1") is not None
    assert merged.get_entry_for_clause("clause-3") is not None
    print("  merge_truth_tables: OK")

    # ------------------------------------------------------------------
    # 9. compute_overall_satisfaction_status
    # ------------------------------------------------------------------
    status_full = compute_overall_satisfaction_status(built_table)
    assert status_full == EVALUATION_STATUS_PARTIAL, (
        f"Expected PARTIAL (2 witnesses, 1 gap), got {status_full}"
    )

    table_all_ok = build_clausewise_truth_table(clauses_all[:2], section_all)
    status_all_ok = compute_overall_satisfaction_status(table_all_ok)
    assert status_all_ok == EVALUATION_STATUS_SATISFIED, (
        f"Expected SATISFIED, got {status_all_ok}"
    )

    table_empty = ClausewiseTruthTable(table_id="empty", spec_id="none")
    status_empty = compute_overall_satisfaction_status(table_empty)
    assert status_empty == EVALUATION_STATUS_UNSATISFIED
    print("  compute_overall_satisfaction_status: OK")

    # ------------------------------------------------------------------
    # 10. gap_records_from_table / witness_records_from_table
    # ------------------------------------------------------------------
    extracted_gaps = gap_records_from_table(built_table)
    assert len(extracted_gaps) == 1
    assert extracted_gaps[0].clause_id == "clause-3"

    extracted_witnesses = witness_records_from_table(built_table)
    assert len(extracted_witnesses) == 2
    print("  gap_records_from_table / witness_records_from_table: OK")

    # ------------------------------------------------------------------
    # 11. clausewise_truth_witness_from_table
    # ------------------------------------------------------------------
    witness = clausewise_truth_witness_from_table(built_table, "spec-smoke")
    assert witness.spec_id == "spec-smoke"
    assert witness.clause_count == 3
    assert witness.satisfied_count == 2
    assert witness.unsatisfied_count == 1
    assert witness.overall_status == EVALUATION_STATUS_PARTIAL
    assert witness.global_section_achievable is False
    assert isinstance(witness.per_clause_trust_tiers, dict)
    summary = witness.summary_dict()
    assert summary["spec_id"] == "spec-smoke"
    print("  clausewise_truth_witness_from_table: OK")

    # ------------------------------------------------------------------
    # 12. rank_gaps
    # ------------------------------------------------------------------
    gap_low = ClauseGapRecord(
        gap_id="g1", clause_id="c-low", coordinate="x",
        gap_kind="K", missing_evidence=[], obstruction=None,
        gap_severity=GAP_SEVERITY_LOW, repair_hints=[],
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    gap_high = ClauseGapRecord(
        gap_id="g2", clause_id="c-high", coordinate="x",
        gap_kind="K", missing_evidence=["a", "b", "c"], obstruction=None,
        gap_severity=GAP_SEVERITY_HIGH, repair_hints=[],
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    gap_crit = ClauseGapRecord(
        gap_id="g3", clause_id="c-crit", coordinate="x",
        gap_kind="K", missing_evidence=[], obstruction="blocker",
        gap_severity=GAP_SEVERITY_CRITICAL, repair_hints=[],
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    ranked = rank_gaps([gap_low, gap_high, gap_crit])
    assert ranked[0].gap_severity == GAP_SEVERITY_CRITICAL
    assert ranked[1].gap_severity == GAP_SEVERITY_HIGH
    assert ranked[2].gap_severity == GAP_SEVERITY_LOW
    print("  rank_gaps: OK")

    # ------------------------------------------------------------------
    # 13. ClausewiseTruthCoordinator
    # ------------------------------------------------------------------
    coord = ClausewiseTruthCoordinator(strict_mode=True, trust_threshold=TRUST_TIER_TRUSTED)
    coord.load_judgment_section(section_all)
    assert coord.judgment_section == section_all
    assert coord.clause_truth_table == {}

    for c in clauses_all:
        coord.evaluate_clause(c)

    sat_ids = coord.get_satisfied_clauses()
    unsat_ids = coord.get_unsatisfied_clauses()
    assert "clause-1" in sat_ids
    assert "clause-3" in unsat_ids

    overall = coord.compute_overall_status()
    assert overall == EVALUATION_STATUS_PARTIAL

    snapshot_table = coord.get_truth_table()
    assert len(snapshot_table.entries) == 3

    coord.reset()
    assert coord.judgment_section == {}
    assert coord.clause_truth_table == {}
    print("  ClausewiseTruthCoordinator: OK")

    # ------------------------------------------------------------------
    # 14. ClausewiseTruthAnalyzer
    # ------------------------------------------------------------------
    analyzer = ClausewiseTruthAnalyzer()

    # Build a richer table for analysis.
    clauses_rich = [
        {"spec_id": "spec-rich", "clause_id": f"cl-{i}", "coordinate": f"coord-{i % 2}", "formula": f"F{i}"}
        for i in range(6)
    ]
    section_rich = {
        "coord-0": {
            "c": "coord-0", "phi": "F0 and F2 and F4",
            "A": "agent", "E": ["ev-0"], "O": [], "B": None,
            "T": TRUST_TIER_VERIFIED, "Pi": "root",
        },
        # coord-1 missing → gaps for cl-1, cl-3, cl-5
    }
    rich_table = build_clausewise_truth_table(clauses_rich, section_rich)

    report = analyzer.analyze_truth_table(rich_table)
    assert "coverage_metrics" in report
    assert report["coverage_metrics"]["total_clauses"] == 6

    gap_patterns = analyzer.identify_gap_patterns(gap_records_from_table(rich_table))
    assert any(p["pattern_type"] == "COORDINATE_CLUSTER" for p in gap_patterns)

    witness_patterns = analyzer.identify_witness_patterns(
        witness_records_from_table(rich_table)
    )
    assert isinstance(witness_patterns, list)

    dep_graph = analyzer.compute_dependency_graph(rich_table)
    assert isinstance(dep_graph, dict)

    suggestions = analyzer.suggest_improvements(rich_table)
    assert isinstance(suggestions, list)
    assert len(suggestions) > 0

    ranked_gaps = analyzer.rank_gaps_by_severity(gap_records_from_table(rich_table))
    assert isinstance(ranked_gaps, list)

    full_report = analyzer.produce_analysis_report()
    assert full_report["analyzer_id"] == analyzer.analyzer_id
    print("  ClausewiseTruthAnalyzer: OK")

    # ------------------------------------------------------------------
    # 15. _compute_blocking_clauses
    # ------------------------------------------------------------------
    mixed = {
        "c-blocked-rec": ClauseTruthRecord(
            record_id="r1", clause_id="c-blocked-rec", coordinate="x",
            evaluation_status=EVALUATION_STATUS_UNSATISFIED,
            evidence_present=[], evidence_kinds=[],
            trust_tier=TRUST_TIER_UNVERIFIED,
            blocking_reason="hard stop",
            record_timestamp=_now_iso(),
        ),
        "c-ok": ClauseTruthRecord(
            record_id="r2", clause_id="c-ok", coordinate="y",
            evaluation_status=EVALUATION_STATUS_SATISFIED,
            evidence_present=["ev"], evidence_kinds=["ev"],
            trust_tier=TRUST_TIER_VERIFIED,
            blocking_reason=None,
            record_timestamp=_now_iso(),
        ),
        "c-gap-obs": gap_crit,
    }
    blocking = _compute_blocking_clauses(mixed)
    assert "c-blocked-rec" in blocking
    assert "c-gap-obs" in blocking   # dict key, not record.clause_id
    assert "c-ok" not in blocking
    print("  _compute_blocking_clauses: OK")

    print()
    print("All smoke tests passed.")
    sys.exit(0)
