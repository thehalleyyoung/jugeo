from __future__ import annotations
"""Oracle Federation package manifest — Theory2.tex Chapter 7.

Covers: Controlled oracles, solver federation, and runtime witnesses.

This module provides the PackageManifest dataclass and associated helpers that
describe the oracle_federation sub-package's contents, provenance, and
relationship to the theoretical framework defined in Theory2.tex Chapter 7.

Chapter 7 of Theory2.tex establishes three pillars of the oracle federation
architecture:

  §7.1  Controlled Oracle Model — defines how external knowledge sources are
        constrained to produce only ORACLE_PROPOSED-level trust assertions and
        never elevate claims to mechanically-verified status.

  §7.2  Solver Federation — formalises the routing of LogicalFragments among
        heterogeneous solver backends (Z3, CVC5, Lean, Copilot) while
        maintaining end-to-end trust accounting via TrustTier propagation.

  §7.3  Runtime Witnesses — specifies how heap snapshots, identity proofs, and
        stack traces are collected, validated, and surfaced as RUNTIME_WITNESSED
        evidence items inside the JuGeo EvidenceChannel pipeline.

Usage::

    from jugeo.foundations.oracle_federation.manifest import get_manifest, MANIFEST

    m = get_manifest()
    print(m.get_chapter_summary())
    print(describe_package())
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_chapter_coverage(coverage: dict) -> bool:
    """Return True iff *coverage* contains all required keys.

    The minimum required keys mirror the keys produced by the default
    PackageManifest.chapter_coverage factory:  ``chapter``, ``title``,
    ``theory_file``, and ``sections``.

    Parameters
    ----------
    coverage:
        A dictionary describing chapter coverage metadata.

    Returns
    -------
    bool
        ``True`` when all required keys are present and ``sections`` is a
        non-empty list; ``False`` otherwise.

    Examples
    --------
    >>> _validate_chapter_coverage({"chapter": 7, "title": "x", "theory_file": "T.tex", "sections": ["7.1"]})
    True
    >>> _validate_chapter_coverage({})
    False
    """
    required_keys = {"chapter", "title", "theory_file", "sections"}
    if not required_keys.issubset(coverage.keys()):
        logger.debug(
            "_validate_chapter_coverage: missing keys %s",
            required_keys - set(coverage.keys()),
        )
        return False
    if not isinstance(coverage["sections"], list) or len(coverage["sections"]) == 0:
        logger.debug("_validate_chapter_coverage: 'sections' must be a non-empty list")
        return False
    if not isinstance(coverage["chapter"], int):
        logger.debug("_validate_chapter_coverage: 'chapter' must be an int")
        return False
    return True


def _normalize_symbol_list(symbols: list) -> list:
    """Return a deduplicated, sorted list of non-empty string symbols.

    Filters out any non-string entries and empty strings, strips whitespace,
    deduplicates, and returns a sorted list.

    Parameters
    ----------
    symbols:
        Raw list that may contain duplicates, ``None`` values, or strings with
        leading/trailing whitespace.

    Returns
    -------
    list[str]
        Cleaned, sorted, deduplicated symbol list.

    Examples
    --------
    >>> _normalize_symbol_list(["Z3Routing", "  OracleChannel  ", "Z3Routing", None])
    ['OracleChannel', 'Z3Routing']
    """
    cleaned: list[str] = []
    for item in symbols:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            cleaned.append(stripped)
    return sorted(set(cleaned))


def _format_section_list(sections: list[str]) -> str:
    """Format a list of section strings into a human-readable numbered block.

    Parameters
    ----------
    sections:
        Strings of the form ``"7.1 Controlled Oracle Model"``.

    Returns
    -------
    str
        Multi-line formatted string, one section per line, prefixed with an
        ordinal counter.

    Examples
    --------
    >>> print(_format_section_list(["7.1 Foo", "7.2 Bar"]))
      1. 7.1 Foo
      2. 7.2 Bar
    """
    if not sections:
        return "  (no sections)"
    lines = [f"  {idx}. {sec}" for idx, sec in enumerate(sections, start=1)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PackageManifest dataclass
# ---------------------------------------------------------------------------

_DEFAULT_CHAPTER_COVERAGE: dict[str, Any] = {
    "chapter": 7,
    "title": "Controlled oracles, solver federation, and runtime witnesses",
    "theory_file": "Theory2.tex",
    "sections": [
        "7.1 Controlled Oracle Model",
        "7.2 Solver Federation",
        "7.3 Runtime Witnesses",
    ],
}

_DEFAULT_EXPORTED_SYMBOLS: list[str] = [
    "OracleChannel",
    "OracleJurisdiction",
    "TrustCeilingEnforcer",
    "OracleProposalRecord",
    "CopilotOracleChannel",
    "SolverFederation",
    "Z3Routing",
    "FragmentClassification",
    "FederationRouter",
    "RuntimeWitnessCollector",
    "HeapWitness",
    "IdentityWitness",
    "StackWitness",
    "WitnessValidator",
    "OracleFederationIntegration",
    "SiteOracleBridge",
    "PackageManifest",
    "MANIFEST",
    "get_manifest",
    "describe_package",
    "OracleModel",
    "SolverFederationModel",
    "RuntimeWitnessModel",
    "JurisdictionModel",
    "WitnessKind",
    "MergeStrategy",
    "ModelRegistry",
    "OracleChannelConfig",
    "FederationConfig",
    "WitnessCollectionConfig",
]

# Mapping of section numbers to known theorem/definition references in Theory2.tex
_SECTION_THEOREM_REFS: dict[str, list[str]] = {
    "7.1": ["Def 7.1 (ControlledOracle)", "Thm 7.2 (CeilingMonotonicity)"],
    "7.2": ["Def 7.4 (SolverFederation)", "Thm 7.5 (FragmentRouting)", "Lem 7.6 (MergeConsistency)"],
    "7.3": ["Def 7.8 (RuntimeWitness)", "Thm 7.9 (WitnessAdequacy)"],
}


@dataclass
class PackageManifest:
    """Manifest describing the oracle_federation sub-package.

    Tracks which chapter of Theory2.tex the package implements, lists all
    exported public symbols, and provides utilities for introspection, merging,
    and diffing manifests across package versions.

    Attributes
    ----------
    chapter_coverage:
        Dictionary with keys ``chapter`` (int), ``title`` (str),
        ``theory_file`` (str), and ``sections`` (list[str]).
    exported_symbols:
        List of public symbol names exported by this package.
    version:
        Semantic version string (e.g. ``"0.1.0"``).
    description:
        Human-readable one-line description of the package.
    author:
        Package author or team name.
    created_at:
        Unix timestamp of manifest creation.
    theory_file:
        Name of the LaTeX theory file this package implements.
    chapter_number:
        Integer chapter number within *theory_file*.
    """

    chapter_coverage: dict = field(
        default_factory=lambda: dict(_DEFAULT_CHAPTER_COVERAGE)
    )
    exported_symbols: list[str] = field(
        default_factory=lambda: list(_DEFAULT_EXPORTED_SYMBOLS)
    )
    version: str = "0.1.0"
    description: str = (
        "Oracle federation: controlled oracles, solver federation, "
        "and runtime witnesses (Theory2.tex Ch. 7)"
    )
    author: str = "JuGeo team"
    created_at: float = field(default_factory=time.time)
    theory_file: str = "Theory2.tex"
    chapter_number: int = 7

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_chapter_summary(self) -> dict:
        """Return a rich dictionary summarising this manifest's chapter coverage.

        The returned dictionary includes:
        - ``chapter_number`` and ``theory_file`` identifiers.
        - ``title`` of the chapter.
        - ``section_count``: number of sections listed.
        - ``sections``: ordered list of section strings.
        - ``theorem_references``: mapping of section numbers to known theorem
          and definition labels from Theory2.tex.
        - ``version``: manifest version.
        - ``symbol_count``: total number of exported symbols.

        Returns
        -------
        dict
            Rich summary dictionary.
        """
        sections = self.chapter_coverage.get("sections", [])
        theorem_refs: dict[str, list[str]] = {}
        for sec in sections:
            # Extract leading section number like "7.1"
            parts = sec.split()
            if parts:
                sec_num = parts[0]
                if sec_num in _SECTION_THEOREM_REFS:
                    theorem_refs[sec_num] = _SECTION_THEOREM_REFS[sec_num]

        summary = {
            "chapter_number": self.chapter_number,
            "theory_file": self.theory_file,
            "title": self.chapter_coverage.get("title", ""),
            "section_count": len(sections),
            "sections": list(sections),
            "theorem_references": theorem_refs,
            "version": self.version,
            "symbol_count": self.symbol_count(),
            "author": self.author,
        }
        logger.debug("get_chapter_summary: returning summary for ch.%d", self.chapter_number)
        return summary

    def get_exported_symbols(self) -> list[str]:
        """Return a sorted copy of the exported symbols list.

        Returns
        -------
        list[str]
            Sorted list of public symbol names.
        """
        return sorted(self.exported_symbols)

    def validate(self) -> bool:
        """Validate that all required manifest fields are properly populated.

        Checks:
        - ``version`` is non-empty.
        - ``description`` is non-empty.
        - ``chapter_coverage`` passes :func:`_validate_chapter_coverage`.
        - ``exported_symbols`` is a non-empty list.
        - ``chapter_number`` matches ``chapter_coverage["chapter"]``.

        Returns
        -------
        bool
            ``True`` when the manifest is valid.

        Raises
        ------
        ValueError
            If any required field is missing or inconsistent.
        """
        if not self.version or not isinstance(self.version, str):
            raise ValueError("PackageManifest.version must be a non-empty string")
        if not self.description or not isinstance(self.description, str):
            raise ValueError("PackageManifest.description must be a non-empty string")
        if not _validate_chapter_coverage(self.chapter_coverage):
            raise ValueError(
                "PackageManifest.chapter_coverage failed validation; "
                "required keys: chapter, title, theory_file, sections"
            )
        if not self.exported_symbols:
            raise ValueError("PackageManifest.exported_symbols must not be empty")
        if self.chapter_coverage.get("chapter") != self.chapter_number:
            raise ValueError(
                f"chapter_number {self.chapter_number!r} does not match "
                f"chapter_coverage['chapter'] {self.chapter_coverage.get('chapter')!r}"
            )
        logger.debug("validate: manifest v%s is valid", self.version)
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised to JSON-compatible types.
        """
        return {
            "chapter_coverage": dict(self.chapter_coverage),
            "exported_symbols": list(self.exported_symbols),
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "created_at": self.created_at,
            "theory_file": self.theory_file,
            "chapter_number": self.chapter_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PackageManifest:
        """Deserialise a manifest from a dictionary produced by :meth:`to_dict`.

        Parameters
        ----------
        data:
            Dictionary with the same keys as produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest
            Reconstructed manifest instance.

        Raises
        ------
        KeyError
            If a required key is missing from *data*.
        """
        required = {"version", "description", "chapter_coverage", "exported_symbols"}
        missing = required - set(data.keys())
        if missing:
            raise KeyError(f"from_dict: missing required keys: {missing}")

        instance = cls(
            chapter_coverage=dict(data["chapter_coverage"]),
            exported_symbols=list(data["exported_symbols"]),
            version=str(data["version"]),
            description=str(data["description"]),
            author=str(data.get("author", "JuGeo team")),
            created_at=float(data.get("created_at", time.time())),
            theory_file=str(data.get("theory_file", "Theory2.tex")),
            chapter_number=int(data.get("chapter_number", 7)),
        )
        logger.debug("from_dict: reconstructed manifest v%s", instance.version)
        return instance

    def get_section_info(self, section_number: str) -> dict:
        """Return information for a specific section identified by its number.

        Parameters
        ----------
        section_number:
            Section number string such as ``"7.1"`` or ``"7.2"``.

        Returns
        -------
        dict
            Dictionary with keys ``section_number``, ``title`` (str),
            ``theorem_references`` (list), and ``found`` (bool).
        """
        sections = self.chapter_coverage.get("sections", [])
        matched_title = ""
        for sec in sections:
            parts = sec.split(" ", 1)
            if parts[0] == section_number:
                matched_title = parts[1] if len(parts) > 1 else sec
                break

        refs = _SECTION_THEOREM_REFS.get(section_number, [])
        result = {
            "section_number": section_number,
            "title": matched_title,
            "theorem_references": refs,
            "found": bool(matched_title),
            "chapter": self.chapter_number,
            "theory_file": self.theory_file,
        }
        if not matched_title:
            logger.warning("get_section_info: section %r not found in manifest", section_number)
        return result

    def symbol_count(self) -> int:
        """Return the number of exported symbols.

        Returns
        -------
        int
            Length of :attr:`exported_symbols`.
        """
        return len(self.exported_symbols)

    def add_symbol(self, symbol: str) -> None:
        """Append *symbol* to :attr:`exported_symbols` if not already present.

        Parameters
        ----------
        symbol:
            Public symbol name to register.
        """
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("add_symbol: symbol must be a non-empty string")
        if symbol not in self.exported_symbols:
            self.exported_symbols.append(symbol)
            logger.debug("add_symbol: added %r (total: %d)", symbol, len(self.exported_symbols))
        else:
            logger.debug("add_symbol: %r already present, skipping", symbol)

    def remove_symbol(self, symbol: str) -> None:
        """Remove *symbol* from :attr:`exported_symbols` if present.

        Parameters
        ----------
        symbol:
            Public symbol name to remove.
        """
        symbol = symbol.strip()
        if symbol in self.exported_symbols:
            self.exported_symbols.remove(symbol)
            logger.debug("remove_symbol: removed %r (total: %d)", symbol, len(self.exported_symbols))
        else:
            logger.warning("remove_symbol: %r not found in exported_symbols", symbol)

    def merge(self, other: PackageManifest) -> PackageManifest:
        """Merge *other* into *self* and return a new :class:`PackageManifest`.

        The merge policy:
        - ``exported_symbols``: union of both lists, normalised.
        - ``version``: whichever is lexicographically greater.
        - ``description``: self's description is retained.
        - ``chapter_coverage``: self's coverage is retained; sections are unioned.
        - ``created_at``: minimum (earliest) creation timestamp.

        Parameters
        ----------
        other:
            Another :class:`PackageManifest` to merge with.

        Returns
        -------
        PackageManifest
            New merged manifest.
        """
        merged_symbols = _normalize_symbol_list(
            self.exported_symbols + other.exported_symbols
        )
        merged_version = (
            self.version if self.version >= other.version else other.version
        )
        # Union the sections lists, preserving order
        self_sections = self.chapter_coverage.get("sections", [])
        other_sections = other.chapter_coverage.get("sections", [])
        seen: set[str] = set()
        merged_sections: list[str] = []
        for sec in self_sections + other_sections:
            if sec not in seen:
                merged_sections.append(sec)
                seen.add(sec)

        merged_coverage = dict(self.chapter_coverage)
        merged_coverage["sections"] = merged_sections

        merged = PackageManifest(
            chapter_coverage=merged_coverage,
            exported_symbols=merged_symbols,
            version=merged_version,
            description=self.description,
            author=self.author,
            created_at=min(self.created_at, other.created_at),
            theory_file=self.theory_file,
            chapter_number=self.chapter_number,
        )
        logger.debug(
            "merge: produced manifest with %d symbols from %d + %d",
            len(merged_symbols),
            len(self.exported_symbols),
            len(other.exported_symbols),
        )
        return merged

    def diff(self, other: PackageManifest) -> dict:
        """Compute the difference between *self* and *other*.

        Returns a structured dict with keys:
        - ``symbols_added``: symbols in *other* but not *self*.
        - ``symbols_removed``: symbols in *self* but not *other*.
        - ``version_changed``: bool.
        - ``description_changed``: bool.
        - ``sections_added``: sections in *other* but not *self*.
        - ``sections_removed``: sections in *self* but not *other*.
        - ``is_identical``: bool, True iff all differences are empty.

        Parameters
        ----------
        other:
            Manifest to compare against.

        Returns
        -------
        dict
            Structured diff dictionary.
        """
        self_syms = set(self.exported_symbols)
        other_syms = set(other.exported_symbols)
        self_secs = set(self.chapter_coverage.get("sections", []))
        other_secs = set(other.chapter_coverage.get("sections", []))

        added_symbols = sorted(other_syms - self_syms)
        removed_symbols = sorted(self_syms - other_syms)
        added_sections = sorted(other_secs - self_secs)
        removed_sections = sorted(self_secs - other_secs)
        version_changed = self.version != other.version
        description_changed = self.description != other.description

        is_identical = (
            not added_symbols
            and not removed_symbols
            and not added_sections
            and not removed_sections
            and not version_changed
            and not description_changed
        )

        result = {
            "symbols_added": added_symbols,
            "symbols_removed": removed_symbols,
            "sections_added": added_sections,
            "sections_removed": removed_sections,
            "version_changed": version_changed,
            "version_self": self.version,
            "version_other": other.version,
            "description_changed": description_changed,
            "is_identical": is_identical,
        }
        logger.debug(
            "diff: +%d/-%d symbols, +%d/-%d sections",
            len(added_symbols),
            len(removed_symbols),
            len(added_sections),
            len(removed_sections),
        )
        return result


# ---------------------------------------------------------------------------
# Module-level helpers and singleton
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Return the module-level singleton :data:`MANIFEST` instance.

    The manifest is lazily validated on first call and cached for the
    lifetime of the module.

    Returns
    -------
    PackageManifest
        The validated singleton manifest.
    """
    global MANIFEST  # noqa: PLW0603 – intentional module-level singleton
    try:
        MANIFEST.validate()
    except ValueError as exc:
        logger.error("get_manifest: manifest validation failed: %s", exc)
        raise
    return MANIFEST


def describe_package() -> str:
    """Return a multi-line human-readable description of the oracle_federation package.

    The description draws from the module-level :data:`MANIFEST` and formats
    chapter coverage, exported symbol count, and section listing.

    Returns
    -------
    str
        Formatted multi-line description string.
    """
    m = MANIFEST
    sections = m.chapter_coverage.get("sections", [])
    section_block = _format_section_list(sections)
    sym_count = m.symbol_count()

    lines = [
        "=" * 60,
        f"Package : jugeo.foundations.oracle_federation",
        f"Version : {m.version}",
        f"Author  : {m.author}",
        f"Theory  : {m.theory_file}  (Chapter {m.chapter_number})",
        f"Title   : {m.chapter_coverage.get('title', '')}",
        "-" * 60,
        "Sections covered:",
        section_block,
        "-" * 60,
        f"Exported symbols : {sym_count}",
        "Selected exports :",
    ]
    # List up to 10 symbols for brevity
    for sym in sorted(m.exported_symbols)[:10]:
        lines.append(f"  • {sym}")
    if sym_count > 10:
        lines.append(f"  … and {sym_count - 10} more")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton — constructed after helpers are defined
# ---------------------------------------------------------------------------

MANIFEST: PackageManifest = PackageManifest()
"""Module-level singleton :class:`PackageManifest` for oracle_federation.

Access via :func:`get_manifest` for validated retrieval.
"""

logger.debug(
    "oracle_federation.manifest loaded: v%s, %d symbols",
    MANIFEST.version,
    MANIFEST.symbol_count(),
)
