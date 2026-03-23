from __future__ import annotations

"""
manifest.py — Chapter 26 Scalar-Encoding Manifest
===================================================

This module implements the coverage manifest for Chapter 26 of theory2.tex:
"Exact Z3 Encodings I: base refinements, guards, arithmetic, path conditions".

Chapter 26 specifies the low-level translation layer that converts JuGeo's
internal type-theoretic objects into SMT-LIB2 formulae consumable by Z3.
The chapter is divided into eight sections, each addressing a distinct encoding
concern: base-sort refinements (§26.1), refinement-type formulae (§26.2),
guard formula elimination (§26.3), arithmetic side-obligations (§26.4), path-
condition accumulation (§26.5), failure-artifact packaging (§26.6), encoding-
context composition (§26.7), and the integration pipeline (§26.8).

What this manifest tracks
-------------------------
- Per-section coverage status (MISSING → COMPLETE) mirroring chapter progress.
- Open TODO items blocking advancement to the next coverage tier.
- Symbols exported by each logical group so copilot can detect drift between
  the Python surface and the theoretical presentation.
- Formal claims (soundness, completeness, decidability) with mechanisation
  flags so copilot will use this to report which theorems still need Lean/Coq
  proofs or Z3-backed test harnesses.

How to use
----------
Import ``MANIFEST`` for a pre-populated ``PackageManifest`` and call
``MANIFEST.copilot_report()`` to get a human-readable coverage summary.
Use ``MANIFEST.coverage_gate(CoverageStatus.SUBSTANTIAL)`` in CI to block
merges when coverage drops below a threshold.

Key invariants
--------------
1. Every section_id in CHAPTER_COVERAGE must correspond to a §26.x heading.
2. A record reaches COMPLETE only when theorem_count > 0 and open_todos == [].
3. Symbol groups must remain disjoint; merge() is the only approved union op.
4. ClaimSummary.verified == True implies an executable test harness exists.
5. MANIFEST is constructed at import time; it is safe to query from any thread
   (all mutation methods acquire no locks — callers must serialise writes).
"""

# stdlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ===========================================================================
# Enumerations
# ===========================================================================


class CoverageStatus(Enum):
    """Ordered coverage tiers for a single §26.x section.

    Tiers increase monotonically; a section can only move forward.
    copilot will use this to colour-code the coverage dashboard.
    """

    MISSING = 0
    """No Python implementation exists; the section is entirely unaddressed."""

    STUB = 1
    """A skeleton module with placeholder ``raise NotImplementedError`` bodies."""

    PARTIAL = 2
    """Core data structures exist but at least one key algorithm is missing."""

    SUBSTANTIAL = 3
    """All major algorithms are present; minor edge cases may be absent."""

    COMPLETE = 4
    """Full implementation matching every definition in the chapter section."""

    # ------------------------------------------------------------------
    def is_sufficient(self) -> bool:
        """Return True when the coverage tier is good enough for integration.

        SUBSTANTIAL and COMPLETE are considered sufficient; lower tiers are
        not. copilot will use this to decide whether to enable dependent
        modules during pipeline construction.

        Returns
        -------
        bool
            ``True`` for SUBSTANTIAL or COMPLETE, ``False`` otherwise.
        """
        return self.value >= CoverageStatus.SUBSTANTIAL.value

    def numeric_level(self) -> int:
        """Return the integer rank of this coverage tier (0–4).

        The rank corresponds directly to the Enum value and is used in
        arithmetic comparisons when computing coverage scores.

        Returns
        -------
        int
            An integer in the closed range [0, 4].
        """
        return self.value


# ===========================================================================
# Value-object dataclasses
# ===========================================================================


@dataclass(frozen=True)
class ManifestRecord:
    """Immutable record describing the implementation status of one §26.x section.

    Each record is keyed by *section_id* (e.g. ``"26.3"``) and stores the
    human-readable title, the dotted Python module path, the coverage tier,
    a list of blocking TODOs, and the number of theorems the section depends
    on. copilot will use this to surface which sections are blocking a
    ``MANIFEST.coverage_gate()`` call.
    """

    section_id: str
    """Dotted section identifier matching a §26.x heading, e.g. ``"26.4"``."""

    title: str
    """Human-readable section title copied verbatim from theory2.tex."""

    python_module: str
    """Dotted module path relative to the ``jugeo.encodings`` package root."""

    coverage: CoverageStatus
    """Current implementation coverage tier for this section."""

    open_todos: list[str] = field(default_factory=list)
    """Ordered list of open TODO strings blocking the next coverage tier."""

    theorem_count: int = 0
    """Number of theorems/lemmas in this section that require mechanisation."""

    # ------------------------------------------------------------------
    def is_complete(self) -> bool:
        """Return True only when the record has full coverage and theorems.

        A record is considered *complete* iff its coverage tier is COMPLETE
        **and** at least one theorem has been counted for the section.

        Returns
        -------
        bool
            ``True`` when ``coverage == COMPLETE`` and ``theorem_count > 0``.
        """
        return self.coverage == CoverageStatus.COMPLETE and self.theorem_count > 0

    def coverage_score(self) -> float:
        """Return a normalised coverage score between 0.0 and 1.0.

        The base score is derived from the numeric coverage level divided by 4
        (the maximum level). Each open TODO penalises the score by 0.05,
        clamped to a minimum of 0.0.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]`` where 1.0 means perfectly complete.
        """
        base = self.coverage.numeric_level() / 4.0
        penalty = len(self.open_todos) * 0.05
        return max(0.0, base - penalty)

    def needs_work(self) -> bool:
        """Return True when additional implementation effort is required.

        A section *needs work* when its coverage level is below SUBSTANTIAL,
        i.e. when the section is MISSING, STUB, or PARTIAL.

        Returns
        -------
        bool
            ``True`` when ``coverage < SUBSTANTIAL``.
        """
        return self.coverage.value < CoverageStatus.SUBSTANTIAL.value

    def summary_line(self) -> str:
        """Return a single-line human-readable summary of this record.

        The summary includes the section_id, title (truncated to 40 chars),
        coverage tier name, coverage score, and the count of open TODOs.

        Returns
        -------
        str
            A compact summary string suitable for CLI output.
        """
        title_trunc = self.title[:40].ljust(40)
        score = f"{self.coverage_score():.2f}"
        todos = len(self.open_todos)
        return (
            f"[§{self.section_id}] {title_trunc} | "
            f"{self.coverage.name:<12} score={score} todos={todos}"
        )


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolGroup:
    """Immutable named collection of exported Python symbols for one logical area.

    Symbol groups partition the public API of the scalar-encodings sub-package
    into coherent clusters that mirror the theoretical groupings in Chapter 26.
    copilot will use this to verify that every symbol mentioned in theory2.tex
    §26.x has a corresponding Python binding.
    """

    group_name: str
    """Short snake_case identifier for the group, e.g. ``"refinement_types"``."""

    symbols: tuple[str, ...]
    """Sorted tuple of fully-qualified symbol names belonging to this group."""

    description: str
    """One-sentence description of what role these symbols play."""

    # ------------------------------------------------------------------
    def contains(self, symbol: str) -> bool:
        """Return True when *symbol* is a member of this group.

        The comparison is case-sensitive and exact; no prefix matching is
        performed.

        Parameters
        ----------
        symbol:
            The symbol name to look up.

        Returns
        -------
        bool
            ``True`` iff *symbol* is in ``self.symbols``.
        """
        return symbol in self.symbols

    def symbol_count(self) -> int:
        """Return the number of symbols in this group.

        Returns
        -------
        int
            ``len(self.symbols)``
        """
        return len(self.symbols)

    def merge(self, other: SymbolGroup) -> SymbolGroup:
        """Return a new SymbolGroup combining symbols from *self* and *other*.

        Both groups must share the same ``group_name``; if they differ a
        ``ValueError`` is raised. The merged group's description is taken from
        *self* and the symbols are deduplicated and sorted.

        Parameters
        ----------
        other:
            Another SymbolGroup with the same ``group_name``.

        Returns
        -------
        SymbolGroup
            A new frozen SymbolGroup whose symbols are the union of both inputs.

        Raises
        ------
        ValueError
            When ``self.group_name != other.group_name``.
        """
        if self.group_name != other.group_name:
            raise ValueError(
                f"Cannot merge groups with different names: "
                f"{self.group_name!r} vs {other.group_name!r}"
            )
        merged = tuple(sorted(set(self.symbols) | set(other.symbols)))
        return SymbolGroup(
            group_name=self.group_name,
            symbols=merged,
            description=self.description,
        )


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimSummary:
    """Immutable record of a single formal claim from Chapter 26.

    Claims cover soundness, completeness, decidability, and minimality
    properties stated in theory2.tex. copilot will use this to track which
    claims are mechanised (``verified=True``) versus still informal.
    """

    claim_id: str
    """Short kebab-case identifier, e.g. ``"refinement-soundness"``."""

    statement: str
    """The claim statement as it appears (paraphrased) in theory2.tex."""

    verified: bool
    """True iff an executable test harness or formal proof exists."""

    fragment: str
    """SMT-LIB2 logic fragment used to discharge this claim, e.g. ``"QF_LIA"``."""

    # ------------------------------------------------------------------
    def is_mechanized(self) -> bool:
        """Return True when a formal mechanisation of this claim exists.

        This is a semantic alias for ``self.verified`` that uses the
        mechanisation terminology common in the formal-methods literature.

        Returns
        -------
        bool
            ``True`` iff ``self.verified`` is ``True``.
        """
        return self.verified

    def fragment_tag(self) -> str:
        """Return the SMT fragment enclosed in square brackets.

        The tag is used in copilot reports and CI output to make it easy to
        identify the decidability fragment at a glance.

        Returns
        -------
        str
            The fragment string wrapped in ``[…]``, e.g. ``"[QF_LIA]"``.
        """
        return f"[{self.fragment}]"


# ===========================================================================
# Package Manifest
# ===========================================================================


class PackageManifest:
    """Mutable container aggregating all manifest data for Chapter 26.

    A ``PackageManifest`` holds :class:`ManifestRecord` objects (one per
    §26.x section), :class:`SymbolGroup` collections, and
    :class:`ClaimSummary` items. It exposes query helpers used by copilot
    and by the CI coverage gate.

    Thread-safety note: mutation methods are **not** thread-safe. Callers
    that add records from multiple threads must supply their own locking.
    """

    # Class-level constant for JSON schema version
    _JSON_VERSION: ClassVar[str] = "1.0"

    # ------------------------------------------------------------------
    def __init__(self, package_name: str) -> None:
        """Initialise an empty manifest for *package_name*.

        Parameters
        ----------
        package_name:
            A short human-readable name for the package this manifest
            describes, e.g. ``"jugeo.encodings.scalar_encodings"``.
        """
        self._package_name: str = package_name
        self._records: dict[str, ManifestRecord] = {}
        self._symbol_groups: list[SymbolGroup] = []
        self._claims: list[ClaimSummary] = []
        self._created_at: float = time.time()
        logger.debug("PackageManifest(%r) initialised at %.3f", package_name, self._created_at)

    # ------------------------------------------------------------------
    def add_record(self, record: ManifestRecord) -> None:
        """Add or replace a :class:`ManifestRecord` in this manifest.

        If a record with the same ``section_id`` already exists it is
        silently replaced and a debug log entry is written.

        Parameters
        ----------
        record:
            The :class:`ManifestRecord` to register.
        """
        if record.section_id in self._records:
            logger.debug(
                "Replacing existing record for section %r", record.section_id
            )
        self._records[record.section_id] = record
        logger.debug(
            "Added record §%s (%s) coverage=%s",
            record.section_id,
            record.title,
            record.coverage.name,
        )

    def add_symbol_group(self, group: SymbolGroup) -> None:
        """Append a :class:`SymbolGroup` to the manifest's symbol registry.

        Duplicate group names are allowed; callers should use
        :meth:`SymbolGroup.merge` before adding if deduplication is desired.

        Parameters
        ----------
        group:
            The :class:`SymbolGroup` to append.
        """
        self._symbol_groups.append(group)
        logger.debug(
            "Added symbol group %r with %d symbols",
            group.group_name,
            group.symbol_count(),
        )

    def add_claim(self, claim: ClaimSummary) -> None:
        """Append a :class:`ClaimSummary` to the manifest's claim list.

        Claims are stored in insertion order. Duplicate claim_ids are
        permitted but will generate a warning.

        Parameters
        ----------
        claim:
            The :class:`ClaimSummary` to register.
        """
        existing_ids = {c.claim_id for c in self._claims}
        if claim.claim_id in existing_ids:
            logger.warning("Duplicate claim_id %r — appending anyway", claim.claim_id)
        self._claims.append(claim)
        logger.debug(
            "Added claim %r verified=%s fragment=%s",
            claim.claim_id,
            claim.verified,
            claim.fragment,
        )

    # ------------------------------------------------------------------
    def validate_coverage(self) -> dict[str, CoverageStatus]:
        """Return a mapping of section_id → CoverageStatus for every record.

        Logs a WARNING for each section whose coverage is MISSING so that
        CI pipelines catch unaddressed sections early. copilot will use this
        to fail fast when required sections are not yet implemented.

        Returns
        -------
        dict[str, CoverageStatus]
            All section IDs mapped to their current coverage tier.
        """
        result: dict[str, CoverageStatus] = {}
        for sid, rec in self._records.items():
            result[sid] = rec.coverage
            if rec.coverage == CoverageStatus.MISSING:
                logger.warning(
                    "Section §%s (%r) has MISSING coverage!", sid, rec.title
                )
        return result

    def section_summary(self, section_id: str) -> ManifestRecord:
        """Return the :class:`ManifestRecord` for *section_id*.

        Parameters
        ----------
        section_id:
            The dotted section ID to look up, e.g. ``"26.3"``.

        Returns
        -------
        ManifestRecord
            The matching record.

        Raises
        ------
        KeyError
            When no record with *section_id* is registered.
        """
        if section_id not in self._records:
            raise KeyError(
                f"No manifest record found for section_id={section_id!r}. "
                f"Available: {sorted(self._records)}"
            )
        return self._records[section_id]

    def all_symbols(self) -> list[str]:
        """Return a deduplicated, sorted list of every symbol across all groups.

        Symbols that appear in multiple :class:`SymbolGroup` objects are
        included only once in the returned list. The sort order is
        lexicographic. copilot will use this to validate the ``__all__``
        export list of the scalar_encodings package.

        Returns
        -------
        list[str]
            Sorted, deduplicated symbol names.
        """
        seen: set[str] = set()
        for group in self._symbol_groups:
            seen.update(group.symbols)
        return sorted(seen)

    def copilot_report(self) -> str:
        """Return a detailed multi-line coverage report suitable for copilot.

        The report includes: package name, creation timestamp, overall coverage
        tier, per-section summary lines, open-TODO inventory, symbol-group
        statistics, and claim verification status. copilot will use this as
        the primary human-readable artefact for Chapter 26 progress tracking.

        Returns
        -------
        str
            A multi-line string (10+ lines) ready for ``print()`` or logging.
        """
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"  CHAPTER 26 MANIFEST REPORT  —  {self._package_name}")
        lines.append(f"  Generated at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._created_at))}")
        lines.append("=" * 70)

        overall = self.overall_coverage()
        total_th = self.total_theorems()
        lines.append(f"  Overall coverage : {overall.name}  (numeric={overall.numeric_level()})")
        lines.append(f"  Total theorems   : {total_th}")
        lines.append(f"  Sections tracked : {len(self._records)}")
        lines.append(f"  Symbol groups    : {len(self._symbol_groups)}")
        lines.append(f"  Total symbols    : {len(self.all_symbols())}")
        lines.append(f"  Claims tracked   : {len(self._claims)}")
        verified_ct = sum(1 for c in self._claims if c.verified)
        lines.append(f"  Claims verified  : {verified_ct}/{len(self._claims)}")
        lines.append("")

        lines.append("  Per-section coverage:")
        lines.append("  " + "-" * 66)
        for sid in sorted(self._records):
            rec = self._records[sid]
            lines.append("    " + rec.summary_line())
        lines.append("")

        incomplete = self.incomplete_sections()
        if incomplete:
            lines.append(f"  Sections needing work ({len(incomplete)}):")
            for rec in incomplete:
                for todo in rec.open_todos:
                    lines.append(f"    [§{rec.section_id}] TODO: {todo}")
            lines.append("")

        lines.append("  Symbol groups:")
        for grp in self._symbol_groups:
            lines.append(
                f"    {grp.group_name:<22} {grp.symbol_count():>3} symbols — {grp.description}"
            )
        lines.append("")

        lines.append("  Formal claims:")
        for claim in self._claims:
            tick = "✓" if claim.verified else "✗"
            lines.append(
                f"    [{tick}] {claim.claim_id:<35} {claim.fragment_tag()}"
            )
        lines.append("")

        gate_substantial = self.coverage_gate(CoverageStatus.SUBSTANTIAL)
        gate_complete = self.coverage_gate(CoverageStatus.COMPLETE)
        lines.append(f"  CI gate SUBSTANTIAL : {'PASS' if gate_substantial else 'FAIL'}")
        lines.append(f"  CI gate COMPLETE    : {'PASS' if gate_complete else 'FAIL'}")
        lines.append("=" * 70)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def to_json(self) -> str:
        """Serialise the entire manifest to a JSON string.

        All :class:`ManifestRecord`, :class:`SymbolGroup`, and
        :class:`ClaimSummary` objects are encoded. ``open_todos`` is stored
        as a JSON array. The schema version is embedded so that
        :meth:`from_json` can detect format mismatches.

        Returns
        -------
        str
            A UTF-8 JSON string representation of this manifest.
        """
        records_payload = []
        for sid, rec in self._records.items():
            records_payload.append({
                "section_id": rec.section_id,
                "title": rec.title,
                "python_module": rec.python_module,
                "coverage": rec.coverage.name,
                "open_todos": list(rec.open_todos),
                "theorem_count": rec.theorem_count,
            })

        groups_payload = []
        for grp in self._symbol_groups:
            groups_payload.append({
                "group_name": grp.group_name,
                "symbols": list(grp.symbols),
                "description": grp.description,
            })

        claims_payload = []
        for claim in self._claims:
            claims_payload.append({
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "verified": claim.verified,
                "fragment": claim.fragment,
            })

        payload = {
            "_schema_version": self._JSON_VERSION,
            "package_name": self._package_name,
            "created_at": self._created_at,
            "records": records_payload,
            "symbol_groups": groups_payload,
            "claims": claims_payload,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a JSON string.

        Reconstructs all records, symbol groups, and claims from *s*.
        Raises ``ValueError`` if the schema version field is missing or
        mismatched.

        Parameters
        ----------
        s:
            A JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        PackageManifest
            A fully populated manifest instance.

        Raises
        ------
        ValueError
            When the JSON is malformed or the schema version is unsupported.
        """
        try:
            data = json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        version = data.get("_schema_version")
        if version != cls._JSON_VERSION:
            raise ValueError(
                f"Unsupported manifest schema version: {version!r} "
                f"(expected {cls._JSON_VERSION!r})"
            )

        manifest = cls(package_name=data["package_name"])
        manifest._created_at = float(data.get("created_at", time.time()))

        for rec_data in data.get("records", []):
            coverage = CoverageStatus[rec_data["coverage"]]
            record = ManifestRecord(
                section_id=rec_data["section_id"],
                title=rec_data["title"],
                python_module=rec_data["python_module"],
                coverage=coverage,
                open_todos=list(rec_data.get("open_todos", [])),
                theorem_count=int(rec_data.get("theorem_count", 0)),
            )
            manifest.add_record(record)

        for grp_data in data.get("symbol_groups", []):
            group = SymbolGroup(
                group_name=grp_data["group_name"],
                symbols=tuple(grp_data.get("symbols", [])),
                description=grp_data.get("description", ""),
            )
            manifest.add_symbol_group(group)

        for claim_data in data.get("claims", []):
            claim = ClaimSummary(
                claim_id=claim_data["claim_id"],
                statement=claim_data["statement"],
                verified=bool(claim_data.get("verified", False)),
                fragment=claim_data.get("fragment", "UNKNOWN"),
            )
            manifest.add_claim(claim)

        logger.info(
            "PackageManifest.from_json loaded %d records, %d groups, %d claims",
            len(manifest._records),
            len(manifest._symbol_groups),
            len(manifest._claims),
        )
        return manifest

    # ------------------------------------------------------------------
    def coverage_gate(self, min_level: CoverageStatus) -> bool:
        """Return True iff every registered record meets *min_level*.

        This method is intended to be called from CI scripts. copilot will
        use this as the canonical gate condition for merging feature branches
        that touch Chapter 26 encodings.

        Parameters
        ----------
        min_level:
            The minimum required :class:`CoverageStatus` for all sections.

        Returns
        -------
        bool
            ``True`` when every record's coverage >= *min_level*.
        """
        for rec in self._records.values():
            if rec.coverage.value < min_level.value:
                logger.debug(
                    "coverage_gate FAIL: §%s has %s < %s",
                    rec.section_id,
                    rec.coverage.name,
                    min_level.name,
                )
                return False
        return True

    def theory_claims(self) -> list[ClaimSummary]:
        """Return all registered :class:`ClaimSummary` objects.

        Returns a new list so that callers cannot mutate the internal store.
        copilot will use this to enumerate unverified claims that need
        test-harness coverage.

        Returns
        -------
        list[ClaimSummary]
            A shallow copy of the internal claims list in insertion order.
        """
        return list(self._claims)

    def overall_coverage(self) -> CoverageStatus:
        """Return the minimum coverage tier across all registered records.

        The overall coverage of a package is limited by its weakest section;
        this mirrors the standard definition of coverage in formal verification
        (a chain is only as strong as its weakest link).

        Returns
        -------
        CoverageStatus
            The minimum :class:`CoverageStatus` found; MISSING if no records.
        """
        if not self._records:
            return CoverageStatus.MISSING
        min_val = min(rec.coverage.value for rec in self._records.values())
        return CoverageStatus(min_val)

    def incomplete_sections(self) -> list[ManifestRecord]:
        """Return all records where :meth:`ManifestRecord.needs_work` is True.

        Sections are returned in section_id order. copilot will use this list
        to prioritise implementation work during sprint planning.

        Returns
        -------
        list[ManifestRecord]
            Records whose coverage is MISSING, STUB, or PARTIAL, sorted by
            section_id.
        """
        return [
            rec
            for sid, rec in sorted(self._records.items())
            if rec.needs_work()
        ]

    def total_theorems(self) -> int:
        """Return the total number of theorems tracked across all records.

        The count is the sum of :attr:`ManifestRecord.theorem_count` for
        every record in this manifest.

        Returns
        -------
        int
            Non-negative integer sum of all theorem counts.
        """
        return sum(rec.theorem_count for rec in self._records.values())

    def __repr__(self) -> str:
        return (
            f"PackageManifest("
            f"package={self._package_name!r}, "
            f"sections={len(self._records)}, "
            f"symbols={len(self.all_symbols())}, "
            f"claims={len(self._claims)})"
        )


# ===========================================================================
# Module-level data: Chapter 26 section records
# ===========================================================================

CHAPTER_COVERAGE: dict[str, ManifestRecord] = {
    "26.1": ManifestRecord(
        section_id="26.1",
        title="Base Sort Encodings",
        python_module="scalar_encodings.base_sort_encoder",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Add support for dependent-sort parameters in Z3 uninterpreted sorts",
            "Verify round-trip decode for all base sorts",
        ],
        theorem_count=3,
    ),
    "26.2": ManifestRecord(
        section_id="26.2",
        title="Refinement Type Encoding",
        python_module="scalar_encodings.refinement_type_encoder",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Handle mutual refinements (cycles in the refinement graph)",
            "Add caching layer for repeated refinement formula translations",
            "Write property-based tests for encode/decode round-trip",
        ],
        theorem_count=4,
    ),
    "26.3": ManifestRecord(
        section_id="26.3",
        title="Guard Formula Encoding",
        python_module="scalar_encodings.guard_formula_encoder",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Implement guard-elimination for higher-arity conjunctive guards",
            "Port negation-normal-form normaliser from the Haskell prototype",
        ],
        theorem_count=2,
    ),
    "26.4": ManifestRecord(
        section_id="26.4",
        title="Arithmetic Obligations",
        python_module="scalar_encodings.arithmetic_obligations",
        coverage=CoverageStatus.SUBSTANTIAL,
        open_todos=[
            "Extend to non-linear integer arithmetic (NIA) fragments",
        ],
        theorem_count=3,
    ),
    "26.5": ManifestRecord(
        section_id="26.5",
        title="Path Condition Encoding",
        python_module="scalar_encodings.path_condition_encoder",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Support branching path-join with more than two branches",
            "Add interpolant extraction post path-condition discharge",
            "Integrate with the failure-artifact pipeline in §26.6",
        ],
        theorem_count=3,
    ),
    "26.6": ManifestRecord(
        section_id="26.6",
        title="Failure Artifact Encoding",
        python_module="scalar_encodings.failure_artifact_encoder",
        coverage=CoverageStatus.PARTIAL,
        open_todos=[
            "Minimise unsat-core output using Z3 unsatcore minimisation API",
            "Define canonical serialisation for failure-artifact JSON blobs",
        ],
        theorem_count=2,
    ),
    "26.7": ManifestRecord(
        section_id="26.7",
        title="Encoding Context and Composition",
        python_module="scalar_encodings.encoding_context",
        coverage=CoverageStatus.STUB,
        open_todos=[
            "Implement EncodingContext.push/pop for scoped assumption sets",
            "Wire monotonicity invariant check into context merge operation",
            "Add snapshot-and-restore for backtracking-style usage",
        ],
        theorem_count=1,
    ),
    "26.8": ManifestRecord(
        section_id="26.8",
        title="Integration and Pipeline",
        python_module="scalar_encodings.pipeline",
        coverage=CoverageStatus.STUB,
        open_todos=[
            "Assemble end-to-end pipeline: sort → refinement → guard → arith → path",
            "Add telemetry hooks for per-section encoding latency measurement",
        ],
        theorem_count=1,
    ),
}

# ===========================================================================
# Module-level data: exported symbol groups
# ===========================================================================

EXPORTED_SYMBOLS: list[SymbolGroup] = [
    SymbolGroup(
        group_name="refinement_types",
        symbols=(
            "RefinementEncoding",
            "RefinementTypeEncoder",
            "RefinementFormula",
            "RefinementPredicate",
            "encode_refinement_type",
            "decode_refinement_type",
        ),
        description="Classes and helpers for translating refinement types into Z3 formulae (§26.2).",
    ),
    SymbolGroup(
        group_name="path_conditions",
        symbols=(
            "PathCondition",
            "PathConditionEncoder",
            "PathBranch",
            "PathJoin",
            "encode_path_condition",
            "path_condition_sat",
        ),
        description="Path-condition accumulation and discharge helpers (§26.5).",
    ),
    SymbolGroup(
        group_name="guards",
        symbols=(
            "GuardFormula",
            "GuardEncoder",
            "GuardElimination",
            "NNFNormaliser",
            "encode_guard",
        ),
        description="Guard formula encoding and elimination procedures (§26.3).",
    ),
    SymbolGroup(
        group_name="arithmetic",
        symbols=(
            "ArithmeticObligation",
            "ArithmeticEncoder",
            "LinearArithFragment",
            "NonLinearArithFragment",
            "encode_arithmetic_obligation",
            "check_arithmetic_fragment",
        ),
        description="Arithmetic side-obligation encoding and fragment detection (§26.4).",
    ),
    SymbolGroup(
        group_name="context_results",
        symbols=(
            "EncodingContext",
            "EncodingResult",
            "EncodingError",
            "ContextSnapshot",
            "merge_contexts",
            "EncodingPipeline",
        ),
        description="Encoding context, result containers, and the integration pipeline (§26.7–26.8).",
    ),
]

# ===========================================================================
# Module-level data: formal claims from Chapter 26
# ===========================================================================

THEORY_CLAIMS: list[ClaimSummary] = [
    ClaimSummary(
        claim_id="refinement-soundness",
        statement=(
            "If encode_refinement_type(τ) ⊨ φ then every value v satisfying τ "
            "also satisfies φ in the Z3 model."
        ),
        verified=True,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="path-condition-completeness",
        statement=(
            "For every feasible execution path π, the encoding encode_path_condition(π) "
            "is satisfiable in Z3."
        ),
        verified=True,
        fragment="QF_LRA",
    ),
    ClaimSummary(
        claim_id="guard-elimination-correctness",
        statement=(
            "GuardElimination preserves the satisfying-assignment set: "
            "⟦G⟧ = ⟦eliminate(G)⟧ for all guards G."
        ),
        verified=False,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="arithmetic-fragment-decidability",
        statement=(
            "Every ArithmeticObligation whose check_arithmetic_fragment returns "
            "QF_LIA or QF_LRA is decidable and Z3 will terminate."
        ),
        verified=True,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="failure-artifact-minimality",
        statement=(
            "The unsat core produced by the failure-artifact encoder is subset-minimal: "
            "removing any clause restores satisfiability."
        ),
        verified=False,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="subtype-entailment-correctness",
        statement=(
            "τ₁ <: τ₂ holds in the type system iff the Z3 query "
            "encode(τ₁) ∧ ¬encode(τ₂) is unsatisfiable."
        ),
        verified=True,
        fragment="QF_LRA",
    ),
    ClaimSummary(
        claim_id="qf-lia-termination",
        statement=(
            "All Z3 queries generated by the arithmetic-obligation encoder for "
            "linear integer arithmetic terminate in finite time."
        ),
        verified=True,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="path-join-soundness",
        statement=(
            "PathJoin(π₁, π₂) encodes a formula implied by the conjunction of the "
            "individual path conditions: encode(π₁) ∧ encode(π₂) ⊨ encode(join(π₁, π₂))."
        ),
        verified=False,
        fragment="QF_LRA",
    ),
    ClaimSummary(
        claim_id="encoding-context-monotonicity",
        statement=(
            "EncodingContext.push(φ) is monotone: the assumption set grows and is "
            "never retracted except via a matching pop()."
        ),
        verified=False,
        fragment="HORN",
    ),
    ClaimSummary(
        claim_id="unsat-core-minimality",
        statement=(
            "The minimised unsat core returned by the failure-artifact pipeline "
            "contains no redundant clauses."
        ),
        verified=False,
        fragment="QF_LIA",
    ),
    ClaimSummary(
        claim_id="base-sort-injectivity",
        statement=(
            "The base-sort encoder is injective: distinct JuGeo base sorts map to "
            "distinct Z3 sorts."
        ),
        verified=True,
        fragment="ALL",
    ),
    ClaimSummary(
        claim_id="refinement-completeness",
        statement=(
            "For every Z3 model M satisfying encode_refinement_type(τ), there exists "
            "a value v in the denotation of τ consistent with M."
        ),
        verified=False,
        fragment="QF_LRA",
    ),
]

# ===========================================================================
# Pre-populated module-level MANIFEST singleton
# ===========================================================================

MANIFEST: PackageManifest = PackageManifest(
    package_name="jugeo.encodings.scalar_encodings"
)

for _record in CHAPTER_COVERAGE.values():
    MANIFEST.add_record(_record)

for _group in EXPORTED_SYMBOLS:
    MANIFEST.add_symbol_group(_group)

for _claim in THEORY_CLAIMS:
    MANIFEST.add_claim(_claim)

logger.debug("Module-level MANIFEST singleton constructed: %r", MANIFEST)

# ===========================================================================
# CLI entry-point
# ===========================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print(MANIFEST.copilot_report())
