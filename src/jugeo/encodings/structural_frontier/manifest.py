from __future__ import annotations

"""Chapter 25 manifest: Z3 and the Structural Frontier.

This is the chapter 25 manifest for the structural frontier package.  It
records coverage, symbols, claims, and frontier boundaries for the chapter
on Z3 decidability and type lifting.

Background
----------
Chapter 25 of *theory2.tex* — "Z3 and the Structural Frontier" — develops
the theoretical backbone that connects JuGeo's type system to Z3's decision
procedures.  The chapter covers:

* The decidability landscape of first-order theories (QF_LIA, QF_LRA,
  QF_BV, QF_UF, …).
* How JuGeo types "lift" Z3 invariants from ground-level constraints to
  type-theoretic invariants that persist across solver calls.
* The structural frontier: the precise boundary of what Z3 can decide
  without human intervention or approximation.
* Countermodel-guided repair: how countermodels from Z3 drive the
  localisation and repair of broken invariants.
* Frontier crossings: the estimated cost of moving a formula from the
  decidable interior to the undecidable exterior.

This manifest tracks which parts of the chapter are implemented, which are
stubs, and which are complete.  It is consumed by the copilot orchestration
layer to understand the state of the package and to generate a prioritised
work-queue for contributors.

Usage
-----
    >>> from jugeo.encodings.structural_frontier.manifest import DEFAULT_MANIFEST
    >>> report = DEFAULT_MANIFEST.copilot_report()

See Also
--------
theory2.tex ch25 — Z3 and the Structural Frontier
jugeo.encodings.structural_frontier.models — core data models
jugeo.solver.z3_session — Z3 session management
"""

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from jugeo.solver.z3_session import (  # noqa: F401 — type references
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
)
from jugeo.solver.fragments import (  # noqa: F401
    Fragment,
    LogicalFragment,
    SolverFragment,
    classify_fragment,
)
from jugeo.solver.countermodels import (  # noqa: F401
    Countermodel,
    CountermodelExtractor,
    ObstructionConverter,
    FailureClass,
    RepairType,
)
from jugeo.geometry.supports import SupportRegion, SupportSet  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Coverage status enumeration
# ---------------------------------------------------------------------------


class CoverageStatus(str, Enum):
    """Tracks implementation coverage of a chapter 25 section.

    Each value corresponds to a level of completeness — from ``MISSING``
    (no code at all) to ``COMPLETE`` (fully validated against the
    corresponding theory2.tex passage).  The copilot orchestration layer
    uses these values to prioritise which sections need attention.

    Members
    -------
    MISSING
        No implementation exists; the section is entirely absent.
    STUB
        A placeholder module or class exists but contains only ``pass``
        or ``raise NotImplementedError``.
    PARTIAL
        Core logic is present but significant edge cases, proofs, or
        frontier-boundary handling is missing.
    SUBSTANTIAL
        Implementation covers all major cases; only polish, docs, and
        minor edge cases remain.
    COMPLETE
        Implementation is full, tested, and validated against theory2.tex.
    """

    MISSING = "missing"
    """No code exists — not even a placeholder module."""

    STUB = "stub"
    """A stub exists but contains only pass or NotImplementedError."""

    PARTIAL = "partial"
    """Core logic exists; edge cases and proofs are incomplete."""

    SUBSTANTIAL = "substantial"
    """All major cases covered; only polish and minor gaps remain."""

    COMPLETE = "complete"
    """Implementation is complete, tested, and theory-validated."""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def progress_score(self) -> float:
        """Return a numeric progress score in [0.0, 1.0].

        Copilot uses this score when computing package-wide coverage
        statistics and generating progress reports.  Values are tuned to
        reflect the typical fraction of work completed at each stage.
        """
        _scores: dict[str, float] = {
            "missing": 0.0,
            "stub": 0.1,
            "partial": 0.4,
            "substantial": 0.8,
            "complete": 1.0,
        }
        return _scores.get(self.value, 0.0)

    @property
    def is_actionable(self) -> bool:
        """Return True when copilot should queue this section for work.

        Sections at MISSING, STUB, or PARTIAL coverage are actionable;
        SUBSTANTIAL and COMPLETE sections do not need immediate attention
        but can still be refined.
        """
        return self in (
            CoverageStatus.MISSING,
            CoverageStatus.STUB,
            CoverageStatus.PARTIAL,
        )

    def next_status(self) -> CoverageStatus:
        """Return the next coverage milestone in the progression.

        Returns *self* when already at COMPLETE so callers can safely
        call ``status.next_status()`` without range-checking.

        Examples
        --------
        >>> CoverageStatus.STUB.next_status()
        <CoverageStatus.PARTIAL: 'partial'>
        >>> CoverageStatus.COMPLETE.next_status()
        <CoverageStatus.COMPLETE: 'complete'>
        """
        _progression: list[CoverageStatus] = [
            CoverageStatus.MISSING,
            CoverageStatus.STUB,
            CoverageStatus.PARTIAL,
            CoverageStatus.SUBSTANTIAL,
            CoverageStatus.COMPLETE,
        ]
        idx = _progression.index(self)
        return _progression[min(idx + 1, len(_progression) - 1)]

    def gap_to_complete(self) -> float:
        """Return the fractional progress remaining to reach COMPLETE.

        A value of 0.0 means already complete; 1.0 means entirely missing.
        """
        return 1.0 - self.progress_score


# ---------------------------------------------------------------------------
# 2. Manifest record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRecord:
    """Immutable record describing a single section of chapter 25.

    Each section of theory2.tex ch25 is represented by exactly one
    ``ManifestRecord``.  The record tracks the corresponding Python
    module, current coverage status, outstanding work items, the number
    of formal theorems stated in the section, and the name of the
    structural-frontier boundary that the section characterises.

    Copilot reads these records to generate section-level work summaries
    and to determine which frontier boundaries still need implementation.

    Attributes
    ----------
    section_id:
        Canonical identifier, e.g. ``"25.3"``.
    title:
        Human-readable section title from theory2.tex.
    python_module:
        Dotted import path of the primary implementation module.
    coverage:
        Current implementation coverage.
    open_todos:
        Outstanding work items for this section (may be empty).
    theorem_count:
        Number of formal theorems stated in the section.
    frontier_boundary:
        Name of the structural-frontier boundary this section analyses.
    """

    section_id: str
    title: str
    python_module: str
    coverage: CoverageStatus
    open_todos: list[str] = field(default_factory=list)
    theorem_count: int = 0
    frontier_boundary: str = ""

    def is_complete(self) -> bool:
        """Return True when the section is fully implemented."""
        return self.coverage == CoverageStatus.COMPLETE

    def work_remaining(self) -> float:
        """Return the estimated fraction of work remaining for this section.

        Combines the coverage gap with an open-todo penalty so that
        sections with many open items are ranked more urgent than their
        raw coverage score suggests.  The todo penalty is capped at 0.25
        to avoid overriding a COMPLETE status.
        """
        base_gap = self.coverage.gap_to_complete()
        todo_penalty = min(len(self.open_todos) * 0.04, 0.25)
        return min(base_gap + todo_penalty, 1.0)

    def priority_label(self) -> str:
        """Return a human-readable priority label for backlog ordering.

        Copilot uses this label when generating sorted work-queues.
        """
        w = self.work_remaining()
        if w >= 0.9:
            return "critical"
        if w >= 0.6:
            return "high"
        if w >= 0.3:
            return "medium"
        return "low"


# ---------------------------------------------------------------------------
# 3. Symbol group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolGroup:
    """A named cluster of related exported symbols from chapter 25.

    Symbol groups organise the public API surface into thematic clusters
    (frontier types, solver types, repair types, algorithm types,
    theorem types) so that copilot can reason about which symbols serve
    which architectural role.

    Attributes
    ----------
    group_name:
        Short identifier for the group, e.g. ``"Frontier types"``.
    symbols:
        Ordered list of qualified symbol names in this group.
    description:
        Human-readable description of what the group represents.
    is_decidable_region:
        True when every symbol in the group belongs to the decidable
        interior of the structural frontier.
    """

    group_name: str
    symbols: list[str] = field(default_factory=list)
    description: str = ""
    is_decidable_region: bool = True

    def symbol_count(self) -> int:
        """Return the number of symbols in this group."""
        return len(self.symbols)

    def contains(self, symbol: str) -> bool:
        """Return True when *symbol* is a member of this group."""
        return symbol in self.symbols

    def decidability_label(self) -> str:
        """Return ``"decidable"`` or ``"undecidable"`` for reporting."""
        return "decidable" if self.is_decidable_region else "undecidable"


# ---------------------------------------------------------------------------
# 4. Claim summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimSummary:
    """A single formal claim from chapter 25 of theory2.tex.

    Each claim corresponds to a theorem, lemma, or corollary stated in
    the chapter.  The ``verified`` flag tracks whether the claim has been
    validated by the implementation; ``decidability_class`` records the
    fragment to which the claim applies.

    Copilot references these records when explaining why a particular
    solver strategy was chosen or why a repair action is recommended.

    Attributes
    ----------
    claim_id:
        Unique identifier, e.g. ``"ch25-thm-3"``.
    statement:
        Full natural-language statement of the claim.
    verified:
        True when the claim has been validated by tests or proofs.
    decidability_class:
        The SMT-LIB fragment or decidability class this claim concerns.
    """

    claim_id: str
    statement: str
    verified: bool = False
    decidability_class: str = "unknown"

    def status_label(self) -> str:
        """Return ``"verified"`` or ``"unverified"`` for display."""
        return "verified" if self.verified else "unverified"

    def is_in_decidable_fragment(self) -> bool:
        """Return True when the claim's fragment is known to be decidable.

        Uses a conservative whitelist of decidable SMT-LIB logics.
        """
        _decidable = {
            "QF_LIA", "QF_LRA", "QF_BV", "QF_UF", "QF_AUFLIA",
            "QF_ABV", "QF_IDL", "QF_RDL", "QF_UFBV", "PROP",
        }
        return self.decidability_class in _decidable


# ---------------------------------------------------------------------------
# 5. Package manifest
# ---------------------------------------------------------------------------


class PackageManifest:
    """Chapter 25 package manifest: coverage, symbols, claims, frontiers.

    ``PackageManifest`` is the top-level registry for everything the
    structural_frontier package exports.  It aggregates:

    * :class:`ManifestRecord` objects (one per theory2.tex section)
    * :class:`SymbolGroup` objects (thematic clusters of symbols)
    * :class:`ClaimSummary` objects (formal claims from the chapter)
    * A ``frontier_boundaries`` dictionary mapping boundary names to
      human-readable descriptions

    The copilot orchestration layer calls :meth:`copilot_report` to
    obtain a structured string suitable for feeding into an LLM prompt.
    The ``validate_coverage`` method surfaces sections that need work.

    Parameters
    ----------
    records:
        Ordered list of :class:`ManifestRecord` objects.
    symbol_groups:
        Ordered list of :class:`SymbolGroup` objects.
    claims:
        Ordered list of :class:`ClaimSummary` objects.
    frontier_boundaries_dict:
        Mapping of boundary name → description.
    """

    # Class-level metadata consumed by introspection tooling
    CHAPTER: ClassVar[int] = 25
    CHAPTER_TITLE: ClassVar[str] = "Z3 and the Structural Frontier"

    def __init__(
        self,
        records: list[ManifestRecord],
        symbol_groups: list[SymbolGroup],
        claims: list[ClaimSummary],
        frontier_boundaries_dict: dict[str, str],
    ) -> None:
        self._records = list(records)
        self._symbol_groups = list(symbol_groups)
        self._claims = list(claims)
        self._frontier_boundaries_dict = dict(frontier_boundaries_dict)
        self._created_at: float = time.monotonic()
        self._manifest_id: str = uuid.uuid4().hex[:16]
        logger.debug(
            "PackageManifest created: id=%s sections=%d symbols=%d claims=%d",
            self._manifest_id,
            len(self._records),
            sum(g.symbol_count() for g in self._symbol_groups),
            len(self._claims),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def records(self) -> list[ManifestRecord]:
        """Return a copy of the manifest record list."""
        return list(self._records)

    @property
    def symbol_groups(self) -> list[SymbolGroup]:
        """Return a copy of the symbol group list."""
        return list(self._symbol_groups)

    @property
    def claims(self) -> list[ClaimSummary]:
        """Return a copy of the claims list."""
        return list(self._claims)

    # ------------------------------------------------------------------
    # Coverage analysis
    # ------------------------------------------------------------------

    def validate_coverage(self) -> dict[str, bool]:
        """Check each section's coverage and return a per-section validity map.

        A section is considered *valid* when its ``theorem_count`` is
        positive — at least one formal claim must be registered before a
        section can be considered substantive.  Sections with zero theorems
        are flagged as invalid regardless of their stated coverage level,
        because a coverage status without theorem backing cannot be trusted.

        Returns
        -------
        dict[str, bool]
            Maps each ``section_id`` to ``True`` (valid) or ``False``
            (invalid / missing theorem count).
        """
        result: dict[str, bool] = {}
        for record in self._records:
            valid = record.theorem_count > 0
            if not valid:
                logger.warning(
                    "Section %s (%r) has zero theorems — coverage %s may be overstated",
                    record.section_id,
                    record.title,
                    record.coverage.value,
                )
            result[record.section_id] = valid
        return result

    def overall_progress(self) -> float:
        """Return a weighted average progress score across all sections.

        Sections with more theorems count more heavily in the average, so
        that large, theory-heavy sections dominate the overall progress
        metric.  Returns 0.0 if there are no records.
        """
        if not self._records:
            return 0.0
        total_weight = sum(max(r.theorem_count, 1) for r in self._records)
        weighted_sum = sum(
            r.coverage.progress_score * max(r.theorem_count, 1)
            for r in self._records
        )
        return weighted_sum / total_weight

    def actionable_sections(self) -> list[ManifestRecord]:
        """Return records that copilot should act on (MISSING/STUB/PARTIAL).

        Sorted by descending ``work_remaining()`` so that the most urgent
        sections appear first in the list.
        """
        return sorted(
            [r for r in self._records if r.coverage.is_actionable],
            key=lambda r: r.work_remaining(),
            reverse=True,
        )

    # ------------------------------------------------------------------
    # Section access
    # ------------------------------------------------------------------

    def section_summary(self, section_id: str) -> str:
        """Return a multiline human-readable summary of a single section.

        Copilot calls this to obtain context for a specific section before
        proposing changes or explaining the code structure.

        Parameters
        ----------
        section_id:
            The section identifier to look up, e.g. ``"25.3"``.

        Returns
        -------
        str
            Multiline summary including title, coverage, module path,
            theorem count, frontier boundary, and open TODOs.
        """
        record = next(
            (r for r in self._records if r.section_id == section_id), None
        )
        if record is None:
            return (
                f"[manifest] Section {section_id!r} not found in "
                f"chapter {self.CHAPTER} manifest."
            )
        lines: list[str] = [
            f"Section {record.section_id}: {record.title}",
            f"  Coverage      : {record.coverage.value} "
            f"(progress {record.coverage.progress_score:.0%})",
            f"  Priority      : {record.priority_label()}",
            f"  Module        : {record.python_module}",
            f"  Theorems      : {record.theorem_count}",
            f"  Frontier      : {record.frontier_boundary or '(none)'}",
        ]
        if record.open_todos:
            lines.append(f"  Open TODOs ({len(record.open_todos)}):")
            for todo in record.open_todos:
                lines.append(f"    - {todo}")
        else:
            lines.append("  Open TODOs    : (none)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Symbol access
    # ------------------------------------------------------------------

    def all_symbols(self) -> list[str]:
        """Return a flat list of every exported symbol across all groups.

        Preserves the ordering of symbols within each group and the
        ordering of groups in the manifest.  Duplicate symbols (if any)
        are preserved as-is.

        Returns
        -------
        list[str]
            All symbol names in declaration order.
        """
        result: list[str] = []
        for group in self._symbol_groups:
            result.extend(group.symbols)
        return result

    def symbols_in_decidable_region(self) -> list[str]:
        """Return only symbols belonging to decidable-region groups.

        Copilot uses this to quickly scope suggestions to the parts of the
        API that are solver-dischargeable.
        """
        result: list[str] = []
        for group in self._symbol_groups:
            if group.is_decidable_region:
                result.extend(group.symbols)
        return result

    def find_symbol_group(self, symbol: str) -> SymbolGroup | None:
        """Return the first group that contains *symbol*, or None."""
        for group in self._symbol_groups:
            if group.contains(symbol):
                return group
        return None

    # ------------------------------------------------------------------
    # Claims access
    # ------------------------------------------------------------------

    def verified_claims(self) -> list[ClaimSummary]:
        """Return all claims whose ``verified`` flag is True."""
        return [c for c in self._claims if c.verified]

    def unverified_claims(self) -> list[ClaimSummary]:
        """Return all claims that have not yet been verified."""
        return [c for c in self._claims if not c.verified]

    def verification_rate(self) -> float:
        """Return the fraction of claims that are verified.

        Returns 0.0 if there are no claims.
        """
        if not self._claims:
            return 0.0
        return len(self.verified_claims()) / len(self._claims)

    # ------------------------------------------------------------------
    # Frontier boundaries
    # ------------------------------------------------------------------

    def frontier_boundaries(self) -> dict[str, str]:
        """Return the mapping of boundary name → description.

        This is the raw frontier_boundaries dictionary supplied at
        construction time — a pure read-only accessor.

        Returns
        -------
        dict[str, str]
            Boundary name → human-readable description.
        """
        return dict(self._frontier_boundaries_dict)

    def decidability_map(self) -> dict[str, str]:
        """Return a map from each section_id to its frontier_boundary string.

        Copilot uses this to quickly determine which frontier boundary each
        section of the chapter analyses, enabling targeted context loading.

        Returns
        -------
        dict[str, str]
            Section id → frontier boundary name (empty string if none).
        """
        return {r.section_id: r.frontier_boundary for r in self._records}

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def copilot_report(self) -> str:
        """Return a structured string report suitable for copilot consumption.

        The report is formatted so that an LLM can parse it to understand
        the current state of the package without additional tool calls.  It
        includes:

        * Chapter metadata
        * Overall progress percentage
        * Per-section coverage table
        * List of actionable sections with priorities
        * Claim verification rate
        * Frontier boundary registry

        Returns
        -------
        str
            Multi-section plain-text report.
        """
        overall = self.overall_progress()
        verification = self.verification_rate()
        actionable = self.actionable_sections()

        lines: list[str] = [
            f"# Copilot Package Report — Chapter {self.CHAPTER}: {self.CHAPTER_TITLE}",
            f"# manifest_id : {self._manifest_id}",
            f"# generated   : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            "",
            "## Overview",
            f"  Overall progress  : {overall:.1%}",
            f"  Sections          : {len(self._records)}",
            f"  Actionable        : {len(actionable)}",
            f"  Total symbols     : {len(self.all_symbols())}",
            f"  Claims verified   : {len(self.verified_claims())}/{len(self._claims)} "
            f"({verification:.1%})",
            "",
            "## Section Coverage",
        ]

        for record in self._records:
            score = record.coverage.progress_score
            bar_len = 20
            filled = round(score * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(
                f"  {record.section_id:>6}  [{bar}] {score:>4.0%}  "
                f"{record.coverage.value:<12}  {record.title}"
            )

        if actionable:
            lines += ["", "## Actionable Sections (priority order)"]
            for rec in actionable:
                lines.append(
                    f"  [{rec.priority_label():>8}] {rec.section_id} — "
                    f"{rec.title}  ({len(rec.open_todos)} TODOs)"
                )

        lines += ["", "## Frontier Boundaries"]
        for name, desc in self._frontier_boundaries_dict.items():
            lines.append(f"  {name:<30} {desc}")

        lines += ["", "## Claim Verification"]
        for claim in self._claims:
            mark = "✓" if claim.verified else "✗"
            lines.append(
                f"  [{mark}] {claim.claim_id:<20} [{claim.decidability_class}] "
                f"{claim.statement[:72]}"
            )

        lines += ["", "# end of report"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _checksum(self) -> str:
        """Return a short SHA-256 checksum of the serialised manifest.

        Used by copilot to detect stale cached reports.
        """
        payload = json.dumps(self._to_raw_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _to_raw_dict(self) -> dict[str, Any]:
        """Return the manifest as a plain nested dict (no JSON encoding)."""
        return {
            "chapter": self.CHAPTER,
            "chapter_title": self.CHAPTER_TITLE,
            "manifest_id": self._manifest_id,
            "records": [
                {
                    "section_id": r.section_id,
                    "title": r.title,
                    "python_module": r.python_module,
                    "coverage": r.coverage.value,
                    "open_todos": r.open_todos,
                    "theorem_count": r.theorem_count,
                    "frontier_boundary": r.frontier_boundary,
                }
                for r in self._records
            ],
            "symbol_groups": [
                {
                    "group_name": g.group_name,
                    "symbols": g.symbols,
                    "description": g.description,
                    "is_decidable_region": g.is_decidable_region,
                }
                for g in self._symbol_groups
            ],
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "statement": c.statement,
                    "verified": c.verified,
                    "decidability_class": c.decidability_class,
                }
                for c in self._claims
            ],
            "frontier_boundaries": self._frontier_boundaries_dict,
        }

    def to_json(self) -> str:
        """Serialise the manifest to a compact JSON string.

        The JSON representation is canonical (sorted keys) so that
        checksums are reproducible across Python process restarts.

        Returns
        -------
        str
            JSON-encoded manifest.
        """
        return json.dumps(self._to_raw_dict(), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, s: str) -> PackageManifest:
        """Deserialise a manifest from the JSON produced by :meth:`to_json`.

        Parameters
        ----------
        s:
            JSON string previously produced by ``to_json()``.

        Returns
        -------
        PackageManifest
            Reconstructed manifest object.

        Raises
        ------
        ValueError
            If the JSON is malformed or missing required keys.
        """
        try:
            raw = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed manifest JSON: {exc}") from exc

        records: list[ManifestRecord] = [
            ManifestRecord(
                section_id=r["section_id"],
                title=r["title"],
                python_module=r["python_module"],
                coverage=CoverageStatus(r["coverage"]),
                open_todos=r.get("open_todos", []),
                theorem_count=r.get("theorem_count", 0),
                frontier_boundary=r.get("frontier_boundary", ""),
            )
            for r in raw.get("records", [])
        ]
        symbol_groups: list[SymbolGroup] = [
            SymbolGroup(
                group_name=g["group_name"],
                symbols=g.get("symbols", []),
                description=g.get("description", ""),
                is_decidable_region=g.get("is_decidable_region", True),
            )
            for g in raw.get("symbol_groups", [])
        ]
        claims: list[ClaimSummary] = [
            ClaimSummary(
                claim_id=c["claim_id"],
                statement=c["statement"],
                verified=c.get("verified", False),
                decidability_class=c.get("decidability_class", "unknown"),
            )
            for c in raw.get("claims", [])
        ]
        return cls(
            records=records,
            symbol_groups=symbol_groups,
            claims=claims,
            frontier_boundaries_dict=raw.get("frontier_boundaries", {}),
        )


# ---------------------------------------------------------------------------
# 6. Helper functions
# ---------------------------------------------------------------------------


def coverage_progress(records: list[ManifestRecord]) -> float:
    """Compute the simple mean progress score across a list of records.

    Unlike :meth:`PackageManifest.overall_progress`, this function gives
    equal weight to all sections regardless of theorem count.  Useful for
    quick sanity checks outside of the full manifest context.

    Parameters
    ----------
    records:
        List of :class:`ManifestRecord` objects to average.

    Returns
    -------
    float
        Mean progress in [0.0, 1.0]; returns 0.0 for an empty list.
    """
    if not records:
        return 0.0
    return sum(r.coverage.progress_score for r in records) / len(records)


def sections_by_priority(records: list[ManifestRecord]) -> list[ManifestRecord]:
    """Return *records* sorted by descending work_remaining (highest urgency first).

    Copilot uses this ordering when presenting a prioritised backlog to a
    contributor.

    Parameters
    ----------
    records:
        Unsorted list of :class:`ManifestRecord` objects.

    Returns
    -------
    list[ManifestRecord]
        Sorted copy of *records*.
    """
    return sorted(records, key=lambda r: r.work_remaining(), reverse=True)


def make_stub_record(section_id: str, title: str, module: str) -> ManifestRecord:
    """Create a minimal stub :class:`ManifestRecord` with STUB coverage.

    Convenience factory used when scaffolding a new chapter section before
    any implementation exists.

    Parameters
    ----------
    section_id:
        Section identifier, e.g. ``"25.9"``.
    title:
        Section title.
    module:
        Dotted import path of the implementation module.

    Returns
    -------
    ManifestRecord
        A record with STUB coverage, one default open TODO, and zero theorems.
    """
    return ManifestRecord(
        section_id=section_id,
        title=title,
        python_module=module,
        coverage=CoverageStatus.STUB,
        open_todos=[f"Implement {title} per theory2.tex ch25"],
        theorem_count=0,
        frontier_boundary="",
    )


def compute_frontier_hash(boundaries: dict[str, str]) -> str:
    """Return a short hash of the frontier boundary dictionary.

    The hash changes whenever any boundary name or description changes,
    allowing downstream caches to detect stale data.

    Parameters
    ----------
    boundaries:
        Mapping of boundary name → description as stored in the manifest.

    Returns
    -------
    str
        16-character hex digest.
    """
    canonical = json.dumps(boundaries, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def format_coverage_table(records: list[ManifestRecord]) -> str:
    """Render a plain-text coverage table for *records*.

    Each row contains the section id, coverage value, theorem count, and
    title.  Column widths adapt to the longest value in each column.

    Parameters
    ----------
    records:
        Records to include in the table.

    Returns
    -------
    str
        Formatted table string with a header and separator row.
    """
    if not records:
        return "(no sections)"

    col_id = max(len(r.section_id) for r in records)
    col_cov = max(len(r.coverage.value) for r in records)
    header = (
        f"{'Section':<{col_id}}  {'Coverage':<{col_cov}}  "
        f"{'Thms':>4}  Title"
    )
    sep = "-" * len(header)
    rows = [header, sep]
    for r in records:
        rows.append(
            f"{r.section_id:<{col_id}}  {r.coverage.value:<{col_cov}}  "
            f"{r.theorem_count:>4}  {r.title}"
        )
    return "\n".join(rows)


def estimate_completion_effort(records: list[ManifestRecord]) -> dict[str, float]:
    """Estimate the relative effort to complete each section.

    The effort estimate is a dimensionless positive number computed from
    the work remaining and the section's theorem count (as a proxy for
    theoretical complexity).  Higher values indicate more work.

    Parameters
    ----------
    records:
        Sections to estimate.

    Returns
    -------
    dict[str, float]
        Maps section_id → effort estimate.
    """
    result: dict[str, float] = {}
    for record in records:
        work = record.work_remaining()
        complexity = math.log1p(max(record.theorem_count, 1))
        result[record.section_id] = round(work * complexity, 3)
    return result


# ---------------------------------------------------------------------------
# 7. Chapter coverage constants  (theory2.tex ch25 sections 25.1 – 25.8)
# ---------------------------------------------------------------------------


CHAPTER_COVERAGE: dict[str, ManifestRecord] = {
    "25.1": ManifestRecord(
        section_id="25.1",
        title="Decidability Landscape of First-Order Theories",
        python_module="jugeo.encodings.structural_frontier.models",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Add frontier boundary for QF_NIA vs QF_LIA",
            "Validate DecidabilityClass against Presburger completeness proof",
        ],
        theorem_count=5,
        frontier_boundary="QF_LIA_BOUNDARY",
    ),
    "25.2": ManifestRecord(
        section_id="25.2",
        title="Type Lifting: From Z3 Invariants to JuGeo Types",
        python_module="jugeo.encodings.structural_frontier.models",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Implement SolverLiftedType.complexity() weighting for quantifiers",
            "Add support for recursive type lifting",
        ],
        theorem_count=7,
        frontier_boundary="TYPE_LIFT_BOUNDARY",
    ),
    "25.3": ManifestRecord(
        section_id="25.3",
        title="Structural Frontier: Definition and Characterisation",
        python_module="jugeo.encodings.structural_frontier.models",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Formalise boundary_description output format",
            "Cross-reference with theory2.tex §25.3 Prop 4",
        ],
        theorem_count=6,
        frontier_boundary="STRUCTURAL_FRONTIER",
    ),
    "25.4": ManifestRecord(
        section_id="25.4",
        title="Frontier Crossings and Crossing Cost",
        python_module="jugeo.encodings.structural_frontier.models",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Implement crossing_path BFS in DecidabilityMap",
            "Add cost model for ESCALATE_TO_HUMAN vs COPILOT_SUGGEST",
            "Validate FrontierBoundary.crossing_cost against empirical data",
        ],
        theorem_count=4,
        frontier_boundary="CROSSING_BOUNDARY",
    ),
    "25.5": ManifestRecord(
        section_id="25.5",
        title="Countermodel Obstructions and Repair Pipeline",
        python_module="jugeo.encodings.structural_frontier.models",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Implement CountermodelObstruction.to_repair_ticket full schema",
            "Integrate ObstructionConverter from countermodels module",
            "Add confidence calibration for repair suggestions",
        ],
        theorem_count=8,
        frontier_boundary="OBSTRUCTION_FRONTIER",
    ),
    "25.6": ManifestRecord(
        section_id="25.6",
        title="Decision Procedures and Fragment Classification",
        python_module="jugeo.solver.fragments",
        coverage=CoverageStatus.COMPLETE,
        open_todos=[],
        theorem_count=6,
        frontier_boundary="FRAGMENT_BOUNDARY",
    ),
    "25.7": ManifestRecord(
        section_id="25.7",
        title="Z3 Session Management and Query Building",
        python_module="jugeo.solver.z3_session",
        coverage=CoverageStatus.COMPLETE,
        open_todos=[],
        theorem_count=3,
        frontier_boundary="SESSION_BOUNDARY",
    ),
    "25.8": ManifestRecord(
        section_id="25.8",
        title="Copilot Integration and Human Escalation",
        python_module="jugeo.encodings.structural_frontier.manifest",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Add copilot_report streaming mode",
            "Implement human-escalation ticket format",
            "Connect copilot_report to LLM orchestration pipeline",
        ],
        theorem_count=2,
        frontier_boundary="COPILOT_BOUNDARY",
    ),
}


# ---------------------------------------------------------------------------
# 8. Exported symbol groups
# ---------------------------------------------------------------------------


EXPORTED_SYMBOLS: list[SymbolGroup] = [
    SymbolGroup(
        group_name="Frontier types",
        symbols=[
            "StructuralFrontier",
            "FrontierBoundary",
            "FrontierSide",
            "DecidabilityMap",
            "DecidabilityClass",
        ],
        description=(
            "Core data types representing the structural frontier, its "
            "boundaries, and the decidability map used by the repair pipeline."
        ),
        is_decidable_region=True,
    ),
    SymbolGroup(
        group_name="Solver types",
        symbols=[
            "SolverLiftedType",
            "Z3Session",
            "Z3Formula",
            "Z3Result",
            "Z3QueryBuilder",
            "SolveOutcome",
            "SolverResult",
        ],
        description=(
            "Solver-integration types: lifted types that carry Z3 invariants "
            "and session/query/result abstractions."
        ),
        is_decidable_region=True,
    ),
    SymbolGroup(
        group_name="Repair types",
        symbols=[
            "RepairAction",
            "CountermodelObstruction",
            "Countermodel",
            "CountermodelExtractor",
            "ObstructionConverter",
            "RepairType",
            "FailureClass",
        ],
        description=(
            "Repair-pipeline types: countermodel extraction, obstruction "
            "classification, and concrete repair actions."
        ),
        is_decidable_region=False,
    ),
    SymbolGroup(
        group_name="Algorithm types",
        symbols=[
            "Fragment",
            "LogicalFragment",
            "SolverFragment",
            "classify_fragment",
            "SupportRegion",
            "SupportSet",
        ],
        description=(
            "Algorithm support types: fragment classification, support "
            "regions, and solver fragment helpers."
        ),
        is_decidable_region=True,
    ),
    SymbolGroup(
        group_name="Theorem types",
        symbols=[
            "PackageManifest",
            "ManifestRecord",
            "SymbolGroup",
            "ClaimSummary",
            "CoverageStatus",
            "DEFAULT_MANIFEST",
            "CHAPTER_COVERAGE",
            "THEORY_CLAIMS",
            "FRONTIER_BOUNDARIES",
        ],
        description=(
            "Manifest and theorem-tracking types: coverage records, claim "
            "summaries, and the package-level manifest instance."
        ),
        is_decidable_region=True,
    ),
]


# ---------------------------------------------------------------------------
# 9. Theory claims (formal claims from theory2.tex ch25)
# ---------------------------------------------------------------------------


THEORY_CLAIMS: list[ClaimSummary] = [
    ClaimSummary(
        claim_id="ch25-thm-1",
        statement=(
            "QF_LIA is decidable: every closed quantifier-free linear integer "
            "arithmetic formula can be decided by Presburger arithmetic."
        ),
        verified=True,
        decidability_class="QF_LIA",
    ),
    ClaimSummary(
        claim_id="ch25-thm-2",
        statement=(
            "QF_LRA is decidable: the theory of linear real arithmetic admits "
            "quantifier elimination via Fourier-Motzkin or simplex."
        ),
        verified=True,
        decidability_class="QF_LRA",
    ),
    ClaimSummary(
        claim_id="ch25-thm-3",
        statement=(
            "QF_BV is decidable: quantifier-free bit-vector arithmetic is "
            "decidable by reduction to propositional SAT via bit-blasting."
        ),
        verified=True,
        decidability_class="QF_BV",
    ),
    ClaimSummary(
        claim_id="ch25-lem-1",
        statement=(
            "Type lifting preserves satisfiability: if T is a solver-lifted "
            "type with invariant φ, then T is inhabited iff φ is satisfiable "
            "in the underlying fragment."
        ),
        verified=True,
        decidability_class="QF_LIA",
    ),
    ClaimSummary(
        claim_id="ch25-lem-2",
        statement=(
            "Frontier crossing is monotone: if a formula f crosses from the "
            "decidable interior to the undecidable exterior, no syntactic "
            "simplification can return it to the interior without removing "
            "at least one crossing operator."
        ),
        verified=False,
        decidability_class="NONLINEAR",
    ),
    ClaimSummary(
        claim_id="ch25-cor-1",
        statement=(
            "Countermodel extraction is complete for QF_LIA: whenever Z3 "
            "returns SAT on the negation of a QF_LIA claim, the solver model "
            "constitutes a total counterexample."
        ),
        verified=True,
        decidability_class="QF_LIA",
    ),
    ClaimSummary(
        claim_id="ch25-thm-4",
        statement=(
            "The structural frontier is sharp: there exists a QF_NIA formula "
            "that is undecidable (Hilbert's tenth problem reduction) and lies "
            "immediately outside the QF_LIA boundary."
        ),
        verified=False,
        decidability_class="QF_NIA",
    ),
    ClaimSummary(
        claim_id="ch25-lem-3",
        statement=(
            "Repair by weakening terminates: the sequence of types produced "
            "by repeated application of SolverLiftedType.weaken() eventually "
            "reaches a trivially inhabited type (the top type)."
        ),
        verified=False,
        decidability_class="QF_LIA",
    ),
]


# ---------------------------------------------------------------------------
# 10. Frontier boundary constants
# ---------------------------------------------------------------------------


FRONTIER_BOUNDARIES: dict[str, str] = {
    "QF_LIA_BOUNDARY": (
        "The boundary between quantifier-free linear integer arithmetic "
        "(decidable) and nonlinear integer arithmetic (undecidable via "
        "Hilbert's 10th problem).  Crossing cost: high."
    ),
    "TYPE_LIFT_BOUNDARY": (
        "The boundary between ground Z3 invariants and type-theoretic "
        "lifted invariants.  Crossing cost: medium; requires structural "
        "induction proof."
    ),
    "STRUCTURAL_FRONTIER": (
        "The top-level structural frontier of the package — the outermost "
        "boundary separating all decidable fragments from undecidable "
        "territory.  Crossing cost: very high."
    ),
    "CROSSING_BOUNDARY": (
        "The meta-boundary characterising frontier crossings themselves: "
        "formulas that describe crossing events are in general undecidable "
        "because they quantify over arbitrary formulas.  Crossing cost: "
        "escalate to human."
    ),
    "OBSTRUCTION_FRONTIER": (
        "The boundary between countermodel-resolvable obstructions and "
        "obstructions that require human judgement.  Crossing cost: "
        "depends on repair confidence; copilot suggests when confidence "
        "exceeds 0.6."
    ),
    "FRAGMENT_BOUNDARY": (
        "The boundary between any two adjacent SMT-LIB fragments in the "
        "fragment lattice.  Exact crossing cost depends on the specific "
        "fragment pair; see FrontierBoundary for per-pair costs."
    ),
    "SESSION_BOUNDARY": (
        "The boundary between in-session Z3 queries (decidable within "
        "session scope) and cross-session reasoning (semi-decidable "
        "because it requires persistent state)."
    ),
    "COPILOT_BOUNDARY": (
        "The boundary at which automated repair hands off to copilot "
        "suggestion or human escalation.  Defined by confidence threshold "
        "0.3 (below: escalate) and solver timeout (above: copilot suggest)."
    ),
}


# ---------------------------------------------------------------------------
# 11. Default manifest instance
# ---------------------------------------------------------------------------


DEFAULT_MANIFEST: PackageManifest = PackageManifest(
    records=list(CHAPTER_COVERAGE.values()),
    symbol_groups=EXPORTED_SYMBOLS,
    claims=THEORY_CLAIMS,
    frontier_boundaries_dict=FRONTIER_BOUNDARIES,
)

logger.debug(
    "structural_frontier manifest loaded: %d sections, %.1f%% overall progress",
    len(DEFAULT_MANIFEST.records),
    DEFAULT_MANIFEST.overall_progress() * 100,
)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


__all__ = [
    # Enumerations
    "CoverageStatus",
    # Data classes
    "ManifestRecord",
    "SymbolGroup",
    "ClaimSummary",
    # Manifest class
    "PackageManifest",
    # Helper functions
    "coverage_progress",
    "sections_by_priority",
    "make_stub_record",
    "compute_frontier_hash",
    "format_coverage_table",
    "estimate_completion_effort",
    # Constants
    "CHAPTER_COVERAGE",
    "EXPORTED_SYMBOLS",
    "THEORY_CLAIMS",
    "FRONTIER_BOUNDARIES",
    # Singleton
    "DEFAULT_MANIFEST",
]

# copilot: shared-core marker for LLM orchestration.
