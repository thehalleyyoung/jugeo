"""Authoritative manifest for the live_mutation package, aligned with Ch23.

This module is the authoritative manifest for the ``live_mutation`` package,
aligning with Ch23 of theory2.tex.  Dynamic mutations — exec injection, eval
queries, monkey patches, hot reloads — are modelled as section operations in
the sheaf-theoretic framework.  This manifest catalogues every exported
symbol, assigns it a mutation kind and risk level, and provides validation
and theory cross-referencing utilities.

The module exposes:

* ``MutationRiskLevel`` / ``MutationCategory`` — taxonomies for classifying
  symbols.
* ``SymbolRecord`` — an immutable record describing one exported symbol.
* ``LiveMutationManifest`` — a versioned, theory-aligned catalogue of all
  symbols in the package.
* ``ManifestValidator`` — consistency and completeness checks.
* ``ManifestRegistry`` — a lightweight registry for multiple manifests.
* ``TheoryAlignment`` — cross-references between symbols and Ch23 sections.
* ``LIVE_MUTATION_MANIFEST`` — the canonical instance for this package.
* ``DEFAULT_REGISTRY`` — a registry pre-populated with the canonical manifest.

Theory reference: Ch23, §23.1–§23.7 of theory2.tex.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION: str = "0.1.0"
"""Package version string following semantic versioning."""

THEORY_CHAPTER: int = 23
"""Chapter of theory2.tex that governs this package."""

MUTATION_RISK_LEVELS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
"""Ordered tuple of risk level names, from least to most severe."""

PACKAGE_NAME: str = "jugeo.python_runtime.live_mutation"
"""Fully-qualified Python package name."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MutationRiskLevel(Enum):
    """Risk classification for a dynamically-mutating symbol.

    Risk is assessed by the breadth of invalidation a symbol can trigger
    and the difficulty of reverting its effects (Ch23 §23.3).
    """

    LOW = "LOW"
    """Mutation is purely local and trivially reversible."""

    MEDIUM = "MEDIUM"
    """Mutation may affect sibling namespaces but is recoverable."""

    HIGH = "HIGH"
    """Mutation has broad invalidation scope and may be hard to revert."""

    CRITICAL = "CRITICAL"
    """Mutation can cascade globally or render the runtime inconsistent."""


class MutationCategory(Enum):
    """Functional category of an exported symbol in the live_mutation package.

    Categories correspond to the major subsystems described in Ch23.
    """

    INJECTION = "INJECTION"
    """Symbols implementing or supporting exec-based section injection."""

    QUERY = "QUERY"
    """Symbols implementing or supporting eval-based section queries."""

    REPLACEMENT = "REPLACEMENT"
    """Symbols implementing monkey-patch / attribute-override replacement."""

    RELOAD = "RELOAD"
    """Symbols implementing hot-reload / incremental descent."""

    VALIDATION = "VALIDATION"
    """Symbols used for consistency and safety validation."""

    INTEGRATION = "INTEGRATION"
    """Symbols that integrate multiple subsystems or provide top-level API."""

    THEOREM = "THEOREM"
    """Symbols encoding theorem statements or formal proof artefacts."""


# ---------------------------------------------------------------------------
# SymbolRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolRecord:
    """Immutable record describing one exported symbol in the live_mutation package.

    A ``SymbolRecord`` links a symbol name to its origin module, a human-
    readable description, the mutation kind it participates in, a risk
    classification, and a precise theory cross-reference.

    Attributes:
        name: The Python identifier name of the symbol.
        origin_module: Fully-qualified module that defines this symbol.
        description: One-sentence description of the symbol's purpose.
        mutation_kind: String tag matching a ``MutationCategory`` value.
        risk_level: String tag matching a ``MutationRiskLevel`` value.
        theory_section: Section reference in theory2.tex (e.g. ``"§23.2"``).
        is_public: Whether this symbol appears in the module's ``__all__``.
    """

    name: str
    origin_module: str
    description: str
    mutation_kind: str
    risk_level: str
    theory_section: str
    is_public: bool = True

    def to_dict(self) -> dict:
        """Serialise the record to a JSON-safe dictionary.

        Returns:
            A plain ``dict`` with all fields represented as primitive types.
        """
        return {
            "name": self.name,
            "origin_module": self.origin_module,
            "description": self.description,
            "mutation_kind": self.mutation_kind,
            "risk_level": self.risk_level,
            "theory_section": self.theory_section,
            "is_public": self.is_public,
        }

    def is_risky(self) -> bool:
        """Return True if the risk level is HIGH or CRITICAL.

        Returns:
            ``True`` when ``risk_level`` is ``"HIGH"`` or ``"CRITICAL"``.
        """
        return self.risk_level in (MutationRiskLevel.HIGH.value, MutationRiskLevel.CRITICAL.value)

    def summary(self) -> str:
        """Return a one-line human-readable summary of this record.

        The summary format is::

            <name> [<risk_level>/<mutation_kind>] (<theory_section>) — <description>

        Returns:
            A single-line string.
        """
        return (
            f"{self.name} [{self.risk_level}/{self.mutation_kind}]"
            f" ({self.theory_section}) — {self.description}"
        )


# ---------------------------------------------------------------------------
# LiveMutationManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveMutationManifest:
    """Versioned, theory-aligned catalogue of all symbols in the live_mutation package.

    A ``LiveMutationManifest`` is the single source of truth for what is
    exported, where it lives, and how it relates to the theoretical framework
    in Ch23 of theory2.tex.

    Attributes:
        package_name: Fully-qualified Python package name.
        version: Semantic version string.
        theory_chapter: Chapter number in theory2.tex.
        exported_symbols: Tuple of ``SymbolRecord`` objects, one per export.
        description: Short description of the package's purpose.
        author: Author or generator identifier.
        created_at: POSIX timestamp of manifest creation.
    """

    package_name: str
    version: str
    theory_chapter: int
    exported_symbols: tuple[SymbolRecord, ...]
    description: str
    author: str = "copilot"
    created_at: float = field(default_factory=time.time)

    def symbol_count(self) -> int:
        """Return the total number of symbol records in this manifest.

        Returns:
            Integer count of ``exported_symbols``.
        """
        return len(self.exported_symbols)

    def get_symbol(self, name: str) -> Optional[SymbolRecord]:
        """Return the ``SymbolRecord`` with the given *name*, or ``None``.

        Args:
            name: The Python identifier to look up.

        Returns:
            The matching ``SymbolRecord``, or ``None`` if not found.
        """
        for sym in self.exported_symbols:
            if sym.name == name:
                return sym
        return None

    def symbols_by_module(self, module: str) -> list[SymbolRecord]:
        """Return all symbol records whose origin_module matches *module*.

        Args:
            module: Fully-qualified module name to filter by.

        Returns:
            List of matching ``SymbolRecord`` objects (may be empty).
        """
        return [s for s in self.exported_symbols if s.origin_module == module]

    def symbols_by_risk(self, level: str) -> list[SymbolRecord]:
        """Return all symbol records at the given *level* of risk.

        Args:
            level: A ``MutationRiskLevel`` value name (e.g. ``"HIGH"``).

        Returns:
            List of ``SymbolRecord`` objects at that risk level.
        """
        return [s for s in self.exported_symbols if s.risk_level == level]

    def high_risk_symbols(self) -> list[SymbolRecord]:
        """Return all symbols whose risk level is HIGH or CRITICAL.

        Convenience wrapper around ``symbols_by_risk`` for the two highest
        tiers.

        Returns:
            Combined list of HIGH and CRITICAL symbols.
        """
        return [s for s in self.exported_symbols if s.is_risky()]

    def to_dict(self) -> dict:
        """Serialise the manifest to a JSON-safe dictionary.

        Returns:
            A plain ``dict`` with all fields represented as primitive types.
        """
        return {
            "package_name": self.package_name,
            "version": self.version,
            "theory_chapter": self.theory_chapter,
            "exported_symbols": [s.to_dict() for s in self.exported_symbols],
            "description": self.description,
            "author": self.author,
            "created_at": self.created_at,
            "symbol_count": self.symbol_count(),
        }

    def summary_report(self) -> str:
        """Generate a multi-line human-readable summary of the manifest.

        The report includes package metadata, risk statistics, and a table
        of all exported symbols grouped by module.

        Returns:
            A multi-line string suitable for printing to a terminal.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append(f"  Live Mutation Manifest  v{self.version}")
        lines.append(f"  Package : {self.package_name}")
        lines.append(f"  Chapter : Ch{self.theory_chapter} of theory2.tex")
        lines.append(f"  Author  : {self.author}")
        lines.append(f"  Symbols : {self.symbol_count()}")
        lines.append("=" * 72)

        # Risk breakdown
        risk_counts: dict[str, int] = {lvl: 0 for lvl in MUTATION_RISK_LEVELS}
        for sym in self.exported_symbols:
            if sym.risk_level in risk_counts:
                risk_counts[sym.risk_level] += 1
        lines.append("  Risk breakdown:")
        for lvl, cnt in risk_counts.items():
            bar = "#" * cnt
            lines.append(f"    {lvl:<10} {cnt:>3}  {bar}")

        lines.append("")
        lines.append("  Symbols by module:")
        modules: dict[str, list[SymbolRecord]] = {}
        for sym in self.exported_symbols:
            modules.setdefault(sym.origin_module, []).append(sym)
        for mod in sorted(modules):
            lines.append(f"  [{mod}]")
            for sym in modules[mod]:
                tag = "*" if sym.is_risky() else " "
                lines.append(f"    {tag} {sym.summary()}")
        lines.append("=" * 72)
        return "\n".join(lines)

    def validate(self) -> bool:
        """Validate structural integrity of the manifest.

        Checks that every symbol has a non-empty name, a valid risk level,
        and a non-empty theory section reference.

        Returns:
            ``True`` if all checks pass, ``False`` otherwise.
        """
        if not self.package_name or not self.version:
            return False
        valid_levels = set(MUTATION_RISK_LEVELS)
        for sym in self.exported_symbols:
            if not sym.name:
                return False
            if sym.risk_level not in valid_levels:
                return False
            if not sym.theory_section:
                return False
        return True


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


@dataclass
class ManifestValidator:
    """Consistency and completeness checker for a ``LiveMutationManifest``.

    Performs structural, completeness, and theory-alignment checks.  Every
    method returns a structured result that can be incorporated into a
    report.

    Attributes:
        manifest: The ``LiveMutationManifest`` to validate.
    """

    manifest: LiveMutationManifest

    def validate_exports(self) -> dict[str, bool]:
        """Check each exported symbol for structural validity.

        A symbol passes if it has a non-empty name, a valid risk level, and
        a non-empty origin module.

        Returns:
            Mapping from symbol name to ``True`` (valid) / ``False`` (invalid).
        """
        valid_levels = set(MUTATION_RISK_LEVELS)
        results: dict[str, bool] = {}
        for sym in self.manifest.exported_symbols:
            ok = (
                bool(sym.name)
                and bool(sym.origin_module)
                and sym.risk_level in valid_levels
                and bool(sym.theory_section)
            )
            results[sym.name] = ok
        return results

    def check_completeness(self) -> list[str]:
        """Return a list of missing or incomplete items in the manifest.

        Checks for: missing description, missing version, symbols without
        descriptions, symbols without theory sections, and duplicate names.

        Returns:
            List of human-readable strings describing each issue.  Empty list
            means the manifest is complete.
        """
        issues: list[str] = []
        if not self.manifest.description:
            issues.append("Manifest is missing a description.")
        if not self.manifest.version:
            issues.append("Manifest is missing a version string.")
        seen_names: set[str] = set()
        for sym in self.manifest.exported_symbols:
            if not sym.description:
                issues.append(f"Symbol '{sym.name}' has no description.")
            if not sym.theory_section:
                issues.append(f"Symbol '{sym.name}' has no theory section reference.")
            if sym.name in seen_names:
                issues.append(f"Duplicate symbol name: '{sym.name}'.")
            seen_names.add(sym.name)
        return issues

    def cross_reference_theory(self) -> dict[str, str]:
        """Map each symbol name to its theory section reference.

        Returns:
            Mapping from symbol name to ``theory_section`` string.
        """
        return {sym.name: sym.theory_section for sym in self.manifest.exported_symbols}

    def generate_report(self) -> str:
        """Generate a full multi-line validation report.

        The report includes export validation results, completeness issues,
        theory cross-references, and a mutation-safety assessment.

        Returns:
            A multi-line string suitable for printing to a terminal.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("  ManifestValidator Report")
        lines.append(f"  Package : {self.manifest.package_name}  v{self.manifest.version}")
        lines.append("=" * 72)

        # Export validation
        exports = self.validate_exports()
        pass_count = sum(1 for v in exports.values() if v)
        fail_count = len(exports) - pass_count
        lines.append(f"\n  Export Validation: {pass_count} pass / {fail_count} fail")
        for name, ok in sorted(exports.items()):
            status = "PASS" if ok else "FAIL"
            lines.append(f"    [{status}] {name}")

        # Completeness
        issues = self.check_completeness()
        lines.append(f"\n  Completeness Issues ({len(issues)}):")
        if issues:
            for issue in issues:
                lines.append(f"    - {issue}")
        else:
            lines.append("    None — manifest is complete.")

        # Theory cross-references
        xref = self.cross_reference_theory()
        lines.append("\n  Theory Cross-References:")
        for name in sorted(xref):
            lines.append(f"    {name:<45} {xref[name]}")

        # Safety
        safety = self.check_mutation_safety()
        lines.append("\n  Mutation Safety Notes:")
        for name in sorted(safety):
            lines.append(f"    {name:<45} {safety[name]}")

        lines.append("=" * 72)
        return "\n".join(lines)

    def check_mutation_safety(self) -> dict[str, str]:
        """Return a safety note for each symbol based on its risk level.

        Args: (none)

        Returns:
            Mapping from symbol name to a short safety advisory string.
        """
        notes: dict[str, str] = {}
        for sym in self.manifest.exported_symbols:
            if sym.risk_level == MutationRiskLevel.CRITICAL.value:
                notes[sym.name] = "CRITICAL — use only in controlled environments."
            elif sym.risk_level == MutationRiskLevel.HIGH.value:
                notes[sym.name] = "HIGH — verify invalidation scope before use."
            elif sym.risk_level == MutationRiskLevel.MEDIUM.value:
                notes[sym.name] = "MEDIUM — review side effects in concurrent code."
            else:
                notes[sym.name] = "LOW — safe for general use."
        return notes

    def get_risk_summary(self) -> dict[str, int]:
        """Return a count of symbols at each risk level.

        Returns:
            Mapping from risk level name to integer count.
        """
        summary: dict[str, int] = {lvl: 0 for lvl in MUTATION_RISK_LEVELS}
        for sym in self.manifest.exported_symbols:
            if sym.risk_level in summary:
                summary[sym.risk_level] += 1
        return summary

    def validate_all(self) -> bool:
        """Run all validation checks and return True only if everything passes.

        Combines ``validate_exports``, ``check_completeness``, and the
        manifest's own ``validate()`` method.

        Returns:
            ``True`` iff all checks pass with no issues.
        """
        if not self.manifest.validate():
            return False
        if self.check_completeness():
            return False
        exports = self.validate_exports()
        return all(exports.values())


# ---------------------------------------------------------------------------
# ManifestRegistry
# ---------------------------------------------------------------------------


@dataclass
class ManifestRegistry:
    """Lightweight registry for multiple ``LiveMutationManifest`` instances.

    Allows registration and lookup of manifests by package name.  Useful
    when multiple packages in the JuGeo ecosystem each publish their own
    manifest and a centralised view is needed.

    Attributes:
        _entries: Internal list of registered manifests.
    """

    _entries: list = field(default_factory=list)

    def register(self, manifest: LiveMutationManifest) -> None:
        """Register a manifest in the registry.

        Duplicate registrations (same package_name and version) are silently
        ignored; differing versions of the same package are both stored.

        Args:
            manifest: The ``LiveMutationManifest`` to register.
        """
        for existing in self._entries:
            if (
                existing.package_name == manifest.package_name
                and existing.version == manifest.version
            ):
                return
        self._entries.append(manifest)

    def lookup(self, package_name: str) -> Optional[LiveMutationManifest]:
        """Return the most recently registered manifest for *package_name*.

        If multiple versions are registered, the one with the highest
        ``created_at`` timestamp is returned.

        Args:
            package_name: Fully-qualified package name to look up.

        Returns:
            The latest ``LiveMutationManifest`` for the package, or ``None``.
        """
        matches = [m for m in self._entries if m.package_name == package_name]
        if not matches:
            return None
        return max(matches, key=lambda m: m.created_at)

    def list_packages(self) -> list[str]:
        """Return a sorted list of all registered package names (deduplicated).

        Returns:
            Sorted list of unique package name strings.
        """
        return sorted({m.package_name for m in self._entries})

    def latest_version(self, package_name: str) -> Optional[str]:
        """Return the version string of the most recently registered manifest.

        Args:
            package_name: Fully-qualified package name.

        Returns:
            Version string, or ``None`` if the package is not registered.
        """
        manifest = self.lookup(package_name)
        return manifest.version if manifest is not None else None

    def total_symbols(self) -> int:
        """Return the total number of symbol records across all registered manifests.

        De-duplication is *not* performed; a symbol registered in two
        packages is counted twice.

        Returns:
            Integer total symbol count.
        """
        return sum(m.symbol_count() for m in self._entries)

    def export_registry(self) -> dict:
        """Serialise the entire registry to a JSON-safe dictionary.

        Returns:
            A dict with a ``"packages"`` key containing a list of manifest
            dicts, plus ``"total_symbols"`` and ``"package_count"`` metadata.
        """
        return {
            "package_count": len(self.list_packages()),
            "total_symbols": self.total_symbols(),
            "packages": [m.to_dict() for m in self._entries],
        }


# ---------------------------------------------------------------------------
# TheoryAlignment
# ---------------------------------------------------------------------------


@dataclass
class TheoryAlignment:
    """Cross-reference utility between exported symbols and Ch23 of theory2.tex.

    ``TheoryAlignment`` provides a structured view of how each symbol in the
    live_mutation package corresponds to a theorem, lemma, or section in
    Ch23.  It can validate that every symbol is grounded in the theory and
    generate alignment reports for documentation.

    Attributes:
        chapter: The chapter number of theory2.tex being aligned against.
    """

    chapter: int

    def get_section_refs(self) -> dict[str, str]:
        """Return a mapping from theorem / lemma names to section references.

        The returned dict covers the key results from Ch23 that underpin the
        live_mutation package.

        Returns:
            Mapping from theorem/lemma identifier to section string.
        """
        return {
            "ExecInjectionTheorem": f"§{self.chapter}.2",
            "EvalQueryLemma": f"§{self.chapter}.4",
            "MonkeyPatchReplacementThm": f"§{self.chapter}.5",
            "HotReloadDescentLemma": f"§{self.chapter}.6",
            "InvalidationScopeCorollary": f"§{self.chapter}.5.1",
            "TrustTierLatticeThm": f"§{self.chapter}.3",
            "SheafConsistencyAxiom": f"§{self.chapter}.1",
            "DynamicSectionGluingLemma": f"§{self.chapter}.2.3",
            "NamespaceStalkDefinition": f"§{self.chapter}.1.2",
            "SectionFingerprinting": f"§{self.chapter}.2.4",
        }

    def get_chapter_summary(self) -> str:
        """Return a multi-sentence summary of Ch23's content and scope.

        Returns:
            A plain-text summary string.
        """
        return (
            f"Chapter {self.chapter} of theory2.tex develops the sheaf-theoretic "
            "semantics of Python's dynamic mutation mechanisms.  "
            "§23.1 introduces the presheaf of module namespaces and defines "
            "the topology of open sets used throughout.  "
            "§23.2–§23.4 cover exec injection, dynamic section gluing, and "
            "eval queries as stalk operations.  "
            "§23.5 formalises monkey patching as section replacement with "
            "cascading invalidation.  "
            "§23.6 models hot reload as an incremental descent functor.  "
            "§23.7 establishes the trust-tier lattice and proves that "
            "consistency is preserved under certified mutations."
        )

    def get_mutation_theory_map(self) -> dict[str, str]:
        """Map each MutationKind value name to its theoretical description.

        Returns:
            Mapping from mutation kind string to a short theory description.
        """
        return {
            "EXEC_INJECTION": (
                f"Section injection into the namespace presheaf (Ch{self.chapter} §{self.chapter}.2)."
            ),
            "EVAL_QUERY": (
                f"Read-only stalk query via the evaluation functor (Ch{self.chapter} §{self.chapter}.4)."
            ),
            "MONKEY_PATCH": (
                f"Section replacement with invalidation propagation (Ch{self.chapter} §{self.chapter}.5)."
            ),
            "HOT_RELOAD": (
                f"Incremental descent across a module reload boundary (Ch{self.chapter} §{self.chapter}.6)."
            ),
            "DYNAMIC_SECTION": (
                f"Generic runtime section not bound to a single mechanism (Ch{self.chapter} §{self.chapter}.2)."
            ),
            "ATTRIBUTE_OVERRIDE": (
                f"Lightweight attribute override without full patch bookkeeping (Ch{self.chapter} §{self.chapter}.5.2)."
            ),
        }

    def validate_alignment(self, symbol_record: SymbolRecord) -> bool:
        """Return True if *symbol_record* has a valid theory section reference.

        A reference is valid if it matches the pattern ``§<chapter>.<rest>``
        where ``<chapter>`` equals ``self.chapter``.

        Args:
            symbol_record: The ``SymbolRecord`` to validate.

        Returns:
            ``True`` when the section reference is well-formed and chapter-correct.
        """
        ref = symbol_record.theory_section.strip()
        prefix = f"§{self.chapter}."
        return ref.startswith(prefix) and len(ref) > len(prefix)

    def alignment_report(self) -> str:
        """Return a multi-line alignment report for Ch23.

        Includes the chapter summary, section references, and the mutation
        theory map.

        Returns:
            A multi-line string.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append(f"  Theory Alignment Report — Ch{self.chapter} of theory2.tex")
        lines.append("=" * 72)
        lines.append("")
        lines.append("  Chapter Summary:")
        for sentence in self.get_chapter_summary().split("  "):
            if sentence.strip():
                lines.append(f"    {sentence.strip()}")
        lines.append("")
        lines.append("  Section References:")
        for thm, ref in sorted(self.get_section_refs().items()):
            lines.append(f"    {thm:<45} {ref}")
        lines.append("")
        lines.append("  Mutation Kind → Theory:")
        for kind, desc in self.get_mutation_theory_map().items():
            lines.append(f"    {kind:<25} {desc}")
        lines.append("=" * 72)
        return "\n".join(lines)

    def cross_check_all(self, symbols: list[SymbolRecord]) -> dict[str, bool]:
        """Check every symbol in *symbols* for chapter-correct section references.

        Args:
            symbols: List of ``SymbolRecord`` objects to validate.

        Returns:
            Mapping from symbol name to ``True`` (aligned) / ``False`` (not aligned).
        """
        return {sym.name: self.validate_alignment(sym) for sym in symbols}


# ---------------------------------------------------------------------------
# Canonical symbol records for the live_mutation package
# ---------------------------------------------------------------------------

_BASE = PACKAGE_NAME


def _sym(
    name: str,
    module_suffix: str,
    description: str,
    mutation_kind: str,
    risk_level: str,
    theory_section: str,
) -> SymbolRecord:
    """Internal factory for building a ``SymbolRecord`` concisely."""
    return SymbolRecord(
        name=name,
        origin_module=f"{_BASE}.{module_suffix}",
        description=description,
        mutation_kind=mutation_kind,
        risk_level=risk_level,
        theory_section=theory_section,
    )


_SYMBOL_RECORDS: tuple[SymbolRecord, ...] = (
    # --- models.py ---
    _sym(
        "MutationKind",
        "models",
        "Taxonomy of dynamic mutation operations modelled as section operations.",
        MutationCategory.INJECTION.value,
        MutationRiskLevel.LOW.value,
        "§23.2",
    ),
    _sym(
        "InvalidationScope",
        "models",
        "Scope over which a mutation invalidates existing sections.",
        MutationCategory.REPLACEMENT.value,
        MutationRiskLevel.LOW.value,
        "§23.5",
    ),
    _sym(
        "ReloadStatus",
        "models",
        "Status of a hot-reload event, tracking the descent lifecycle.",
        MutationCategory.RELOAD.value,
        MutationRiskLevel.LOW.value,
        "§23.6",
    ),
    _sym(
        "TrustTier",
        "models",
        "Sheaf-theoretic trust classification for dynamically-created sections.",
        MutationCategory.VALIDATION.value,
        MutationRiskLevel.LOW.value,
        "§23.3",
    ),
    _sym(
        "ExecContext",
        "models",
        "Metadata describing the open-set context for an exec call.",
        MutationCategory.INJECTION.value,
        MutationRiskLevel.MEDIUM.value,
        "§23.2",
    ),
    _sym(
        "DynamicSection",
        "models",
        "A section of the sheaf of Python namespaces injected at runtime.",
        MutationCategory.INJECTION.value,
        MutationRiskLevel.HIGH.value,
        "§23.2",
    ),
    _sym(
        "EvalResult",
        "models",
        "The result of a read-only eval query on a section stalk.",
        MutationCategory.QUERY.value,
        MutationRiskLevel.LOW.value,
        "§23.4",
    ),
    _sym(
        "MonkeyPatchRecord",
        "models",
        "Record of a monkey-patch applied to a module attribute.",
        MutationCategory.REPLACEMENT.value,
        MutationRiskLevel.HIGH.value,
        "§23.5",
    ),
    _sym(
        "HotReloadEvent",
        "models",
        "A hot-reload event representing incremental descent across a module boundary.",
        MutationCategory.RELOAD.value,
        MutationRiskLevel.HIGH.value,
        "§23.6",
    ),
    _sym(
        "new_section_id",
        "models",
        "Generate a UUID4-based section identifier with a sec- prefix.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.2",
    ),
    _sym(
        "new_context_id",
        "models",
        "Generate a UUID4-based context identifier with a ctx- prefix.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.2",
    ),
    _sym(
        "new_patch_id",
        "models",
        "Generate a UUID4-based patch identifier with a patch- prefix.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.5",
    ),
    _sym(
        "new_event_id",
        "models",
        "Generate a UUID4-based event identifier with an evt- prefix.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.6",
    ),
    _sym(
        "new_result_id",
        "models",
        "Generate a UUID4-based result identifier with a res- prefix.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.4",
    ),
    # --- manifest.py ---
    _sym(
        "MutationRiskLevel",
        "manifest",
        "Risk classification enum for dynamically-mutating symbols.",
        MutationCategory.VALIDATION.value,
        MutationRiskLevel.LOW.value,
        "§23.3",
    ),
    _sym(
        "MutationCategory",
        "manifest",
        "Functional category of an exported symbol in the live_mutation package.",
        MutationCategory.VALIDATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "SymbolRecord",
        "manifest",
        "Immutable record describing one exported symbol.",
        MutationCategory.VALIDATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "LiveMutationManifest",
        "manifest",
        "Versioned, theory-aligned catalogue of all symbols in the package.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "ManifestValidator",
        "manifest",
        "Consistency and completeness checker for a LiveMutationManifest.",
        MutationCategory.VALIDATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "ManifestRegistry",
        "manifest",
        "Lightweight registry for multiple LiveMutationManifest instances.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "TheoryAlignment",
        "manifest",
        "Cross-reference utility between symbols and Ch23 of theory2.tex.",
        MutationCategory.THEOREM.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "LIVE_MUTATION_MANIFEST",
        "manifest",
        "Canonical LiveMutationManifest instance for this package.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
    _sym(
        "DEFAULT_REGISTRY",
        "manifest",
        "ManifestRegistry pre-populated with the canonical manifest.",
        MutationCategory.INTEGRATION.value,
        MutationRiskLevel.LOW.value,
        "§23.1",
    ),
)

# ---------------------------------------------------------------------------
# Module-level instances
# ---------------------------------------------------------------------------

LIVE_MUTATION_MANIFEST: LiveMutationManifest = LiveMutationManifest(
    package_name=PACKAGE_NAME,
    version=VERSION,
    theory_chapter=THEORY_CHAPTER,
    exported_symbols=_SYMBOL_RECORDS,
    description=(
        "Sheaf-theoretic live mutation framework: exec injection, eval queries, "
        "monkey patching, and hot reload modelled as section operations (Ch23)."
    ),
    author="copilot",
)
"""The canonical ``LiveMutationManifest`` for this package.

Pre-populated with records for all major symbols across ``models.py`` and
``manifest.py``.  Consumers should treat this as read-only.
"""

DEFAULT_REGISTRY: ManifestRegistry = ManifestRegistry()
DEFAULT_REGISTRY.register(LIVE_MUTATION_MANIFEST)
"""A ``ManifestRegistry`` pre-populated with ``LIVE_MUTATION_MANIFEST``.

Import and call ``DEFAULT_REGISTRY.lookup(PACKAGE_NAME)`` to retrieve the
manifest programmatically.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "VERSION",
    "THEORY_CHAPTER",
    "MUTATION_RISK_LEVELS",
    "PACKAGE_NAME",
    # Enumerations
    "MutationRiskLevel",
    "MutationCategory",
    # Data classes
    "SymbolRecord",
    "LiveMutationManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "TheoryAlignment",
    # Module-level instances
    "LIVE_MUTATION_MANIFEST",
    "DEFAULT_REGISTRY",
]

# copilot: manifest and theory alignment for live_mutation Ch23
