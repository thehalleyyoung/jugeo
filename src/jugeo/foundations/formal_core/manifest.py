"""
manifest.py — Package manifest for jugeo.foundations.formal_core.

Theory2.tex §9: Mathematical interlude — a more explicit formal core.

This module describes the coverage, exported symbols, and structure of the
formal_core package, which formalises the site-theoretic, trust-algebraic,
and obstruction-theoretic machinery developed in Chapter 9 of Theory2.tex.

Sections covered
----------------
§9.1  Site Definition        — Grothendieck sites built from JudgmentSites
§9.2  Trust Algebra          — Partial-order algebra on TrustLevel / TrustTier
§9.3  Obstruction Theory     — Cohomological obstructions to global sections

See Also
--------
jugeo.evidence.trust  — TrustProfile, TrustTier, TrustLevel
jugeo.evidence.channels — EvidenceChannel, ChannelJurisdiction
jugeo.geometry.site   — JudgmentSite (optional)
jugeo.solver.router   — SolverRouter, BackendKind, RoutingDecision (optional)
jugeo.solver.fragments — LogicalFragment, SolverFragment (optional)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from jugeo.evidence.trust import (
    TrustLevel,
    TrustProfile,
    TrustTier,
)
from jugeo.evidence.channels import (
    ChannelJurisdiction,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)

try:
    from jugeo.geometry.site import JudgmentSite
    _HAS_JUDGMENT_SITE = True
except ImportError:
    JudgmentSite = None  # type: ignore[assignment,misc]
    _HAS_JUDGMENT_SITE = False

try:
    from jugeo.solver.router import BackendKind, RoutingDecision, SolverRouter
    _HAS_SOLVER_ROUTER = True
except ImportError:
    SolverRouter = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_ROUTER = False

try:
    from jugeo.solver.fragments import LogicalFragment, SolverFragment
    _HAS_SOLVER_FRAGMENTS = True
except ImportError:
    LogicalFragment = None  # type: ignore[assignment,misc]
    SolverFragment = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_FRAGMENTS = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (Theory2.tex §9 metadata)
# ---------------------------------------------------------------------------

_CHAPTER_COVERAGE: dict[str, Any] = {
    "chapter": 9,
    "title": "Mathematical interlude — a more explicit formal core",
    "theory_file": "Theory2.tex",
    "sections": [
        "9.1 Site Definition",
        "9.2 Trust Algebra",
        "9.3 Obstruction Theory",
    ],
}

_EXPORTED_SYMBOLS: list[str] = [
    # manifest
    "PackageManifest",
    "SectionManifest",
    "SymbolRegistry",
    # models
    "ObjectData",
    "MorphismData",
    "CategoryStructure",
    "FormalSite",
    "TrustAlgebraAxioms",
    "ObstructionTheory",
    "DescentData",
]

_VERSION = "0.1.0"

_DESCRIPTION = (
    "jugeo.foundations.formal_core formalises the mathematical core of the "
    "JuGeo judgment-geometry system.  Following Theory2.tex Chapter 9 it "
    "provides: (1) Grothendieck-site structures built on top of JudgmentSite "
    "objects (§9.1); (2) a trust-algebra whose carrier set is the TrustLevel "
    "enumeration ordered by epistemic strength, together with monotonicity, "
    "no-silent-promotion, and challenge-conservativity axioms (§9.2); and "
    "(3) cohomological obstruction theory that decides when local trust "
    "certificates can be glued into a global section (§9.3).  The package "
    "is designed to be imported by higher-level JuGeo modules that need a "
    "rigorous, type-checked account of these structures."
)


# ---------------------------------------------------------------------------
# SectionManifest
# ---------------------------------------------------------------------------


@dataclass
class SectionManifest:
    """Metadata for a single Theory2.tex §9.x section.

    Theory2.tex §9 — each section has a well-defined set of key definitions,
    theorems, and algorithms that the formal_core package implements.

    Parameters
    ----------
    section_number:
        Dotted section number, e.g. ``"9.1"``.
    title:
        Human-readable section title.
    key_definitions:
        Names of the principal definitions introduced in the section.
    key_theorems:
        Names / labels of the main theorems stated in the section.
    key_algorithms:
        Names of computational procedures associated with the section.
    """

    section_number: str
    title: str
    key_definitions: list[str] = field(default_factory=list)
    key_theorems: list[str] = field(default_factory=list)
    key_algorithms: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a multi-line human-readable summary of this section.

        Returns
        -------
        str
            Formatted description including all definitions, theorems, and
            algorithms catalogued for this section.
        """
        lines: list[str] = [
            f"Section {self.section_number}: {self.title}",
            "-" * 60,
        ]
        if self.key_definitions:
            lines.append("  Key definitions:")
            for defn in self.key_definitions:
                lines.append(f"    • {defn}")
        else:
            lines.append("  Key definitions: (none recorded)")

        if self.key_theorems:
            lines.append("  Key theorems:")
            for thm in self.key_theorems:
                lines.append(f"    • {thm}")
        else:
            lines.append("  Key theorems: (none recorded)")

        if self.key_algorithms:
            lines.append("  Key algorithms:")
            for alg in self.key_algorithms:
                lines.append(f"    • {alg}")
        else:
            lines.append("  Key algorithms: (none recorded)")

        description = "\n".join(lines)
        logger.debug("SectionManifest.describe() for section %s", self.section_number)
        return description

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of this section manifest.
        """
        return {
            "section_number": self.section_number,
            "title": self.title,
            "key_definitions": list(self.key_definitions),
            "key_theorems": list(self.key_theorems),
            "key_algorithms": list(self.key_algorithms),
        }

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SectionManifest:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        SectionManifest
            Reconstructed instance.

        Raises
        ------
        ValueError
            If ``section_number`` or ``title`` are missing.
        """
        required = ("section_number", "title")
        for key in required:
            if key not in data:
                raise ValueError(
                    f"SectionManifest.from_dict: missing required key '{key}'"
                )
        return cls(
            section_number=data["section_number"],
            title=data["title"],
            key_definitions=list(data.get("key_definitions", [])),
            key_theorems=list(data.get("key_theorems", [])),
            key_algorithms=list(data.get("key_algorithms", [])),
        )


# ---------------------------------------------------------------------------
# SymbolRegistry
# ---------------------------------------------------------------------------


class SymbolRegistry:
    """A lightweight registry mapping symbol names to live Python objects.

    Theory2.tex §9 introduces a number of named constructs (definitions,
    classes, functions).  The :class:`SymbolRegistry` keeps track of which
    Python symbols implement which Theory2.tex constructs so that tooling can
    introspect the package at runtime.

    Usage
    -----
    >>> reg = SymbolRegistry()
    >>> reg.register("FormalSite", FormalSite, "Theory2 §9.1 Def 9.3")
    >>> entry = reg.get("FormalSite")
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        logger.debug("SymbolRegistry created")

    # ------------------------------------------------------------------ #
    def register(self, name: str, obj: Any, description: str = "") -> None:
        """Register a symbol.

        Parameters
        ----------
        name:
            Canonical symbol name (should match the Python identifier).
        obj:
            The Python object (class, function, constant, …) being registered.
        description:
            Human-readable description, ideally referencing Theory2.tex.

        Raises
        ------
        ValueError
            If *name* is empty or already registered with a *different* object.
        """
        if not name:
            raise ValueError("SymbolRegistry.register: name must be non-empty")

        if name in self._entries:
            existing = self._entries[name]["obj"]
            if existing is not obj:
                raise ValueError(
                    f"SymbolRegistry.register: '{name}' already registered "
                    f"with a different object ({existing!r} vs {obj!r})"
                )
            logger.debug("SymbolRegistry: re-registering '%s' (same object)", name)
            return

        self._entries[name] = {
            "name": name,
            "obj": obj,
            "description": description,
            "type": type(obj).__name__,
        }
        logger.debug("SymbolRegistry: registered '%s' (%s)", name, type(obj).__name__)

    # ------------------------------------------------------------------ #
    def get(self, name: str) -> Any:
        """Retrieve a registered symbol by name.

        Parameters
        ----------
        name:
            Symbol name to look up.

        Returns
        -------
        Any
            The registered Python object.

        Raises
        ------
        KeyError
            If *name* has not been registered.
        """
        if name not in self._entries:
            raise KeyError(
                f"SymbolRegistry.get: '{name}' is not registered. "
                f"Available symbols: {sorted(self._entries)}"
            )
        return self._entries[name]["obj"]

    # ------------------------------------------------------------------ #
    def list_all(self) -> list[dict[str, Any]]:
        """Return a list of all registry entries as plain dictionaries.

        Each entry contains ``name``, ``description``, and ``type`` fields
        (the live ``obj`` reference is excluded for safe serialisation).

        Returns
        -------
        list[dict]
            Sorted by symbol name.
        """
        result = []
        for entry in sorted(self._entries.values(), key=lambda e: e["name"]):
            result.append(
                {
                    "name": entry["name"],
                    "description": entry["description"],
                    "type": entry["type"],
                }
            )
        return result

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        """Return a formatted table of all registered symbols.

        Returns
        -------
        str
            Multi-line string table suitable for ``print()``.
        """
        if not self._entries:
            return "SymbolRegistry: (empty)"

        lines = [f"SymbolRegistry — {len(self._entries)} symbol(s):"]
        lines.append("-" * 70)
        for entry in self.list_all():
            lines.append(
                f"  {entry['name']:<35} [{entry['type']}]  {entry['description']}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Manifest for the jugeo.foundations.formal_core package.

    Theory2.tex §9 — the manifest records which sections of the theory are
    implemented, which Python symbols are exported, and provides validation
    helpers so that CI tooling can confirm that the package stays in sync with
    the theoretical source.

    Parameters
    ----------
    chapter_coverage:
        Dictionary describing the chapter covered (see ``_CHAPTER_COVERAGE``).
    exported_symbols:
        List of all public symbols exported from the package.
    version:
        Semantic version string for the formal_core package.
    description:
        Long-form description of the package's purpose and scope.
    sections:
        Optional list of :class:`SectionManifest` objects, one per §9.x.
    registry:
        Optional :class:`SymbolRegistry` pre-populated for this package.
    """

    chapter_coverage: dict[str, Any] = field(
        default_factory=lambda: dict(_CHAPTER_COVERAGE)
    )
    exported_symbols: list[str] = field(
        default_factory=lambda: list(_EXPORTED_SYMBOLS)
    )
    version: str = _VERSION
    description: str = _DESCRIPTION
    sections: list[SectionManifest] = field(default_factory=list)
    registry: SymbolRegistry = field(default_factory=SymbolRegistry)

    # Class-level constant so we know which fields are truly required
    _REQUIRED_COVERAGE_KEYS: ClassVar[tuple[str, ...]] = (
        "chapter",
        "title",
        "theory_file",
        "sections",
    )

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        logger.debug(
            "PackageManifest created: version=%s, symbols=%d",
            self.version,
            len(self.exported_symbols),
        )
        if not self.sections:
            self._populate_default_sections()

    # ------------------------------------------------------------------ #
    def _populate_default_sections(self) -> None:
        """Populate :attr:`sections` from the canonical Theory2.tex §9 content."""
        self.sections = [
            SectionManifest(
                section_number="9.1",
                title="Site Definition",
                key_definitions=[
                    "Def 9.1: Judgment category C_J",
                    "Def 9.2: Covering sieve",
                    "Def 9.3: Grothendieck topology J on C_J",
                    "Def 9.4: Formal site (C_J, J)",
                ],
                key_theorems=[
                    "Thm 9.1: Sheaf condition on (C_J, J)",
                    "Prop 9.2: Stability under base-change",
                ],
                key_algorithms=[
                    "Alg 9.1: Sieve saturation",
                    "Alg 9.2: Cover verification",
                ],
            ),
            SectionManifest(
                section_number="9.2",
                title="Trust Algebra",
                key_definitions=[
                    "Def 9.5: Trust carrier set T",
                    "Def 9.6: Partial order ≤ on T",
                    "Def 9.7: Trust composition ⊕",
                    "Def 9.8: Attenuation operator α",
                ],
                key_theorems=[
                    "Thm 9.3: Monotonicity of composition",
                    "Thm 9.4: No-silent-promotion",
                    "Thm 9.5: Challenge-conservativity",
                ],
                key_algorithms=[
                    "Alg 9.3: Trust meet (greatest lower bound)",
                    "Alg 9.4: Axiom checker",
                ],
            ),
            SectionManifest(
                section_number="9.3",
                title="Obstruction Theory",
                key_definitions=[
                    "Def 9.9: Čech cochain complex C•(U, F)",
                    "Def 9.10: Coboundary operator δ",
                    "Def 9.11: Obstruction class [ω] ∈ Ȟ¹(U, F)",
                    "Def 9.12: Descent datum",
                ],
                key_theorems=[
                    "Thm 9.6: Vanishing obstruction ⟹ global section exists",
                    "Thm 9.7: Effective descent criterion",
                ],
                key_algorithms=[
                    "Alg 9.5: Obstruction computation",
                    "Alg 9.6: Gluing algorithm for unobstructed data",
                ],
            ),
        ]
        logger.debug("PackageManifest: populated %d default sections", len(self.sections))

    # ------------------------------------------------------------------ #
    def get_chapter_summary(self) -> str:
        """Return a one-paragraph summary of the chapter coverage.

        Theory2.tex §9 — the summary is constructed from the coverage dict
        and section manifests, providing a human-readable overview.

        Returns
        -------
        str
            Multi-line summary string.
        """
        chapter = self.chapter_coverage.get("chapter", "?")
        title = self.chapter_coverage.get("title", "")
        theory_file = self.chapter_coverage.get("theory_file", "")
        raw_sections = self.chapter_coverage.get("sections", [])

        lines: list[str] = [
            f"Chapter {chapter}: {title}",
            f"Theory file: {theory_file}",
            f"Version: {self.version}",
            "",
            self.description,
            "",
            f"Sections ({len(raw_sections)}):",
        ]
        for s in raw_sections:
            lines.append(f"  {s}")

        if self.sections:
            lines.append("")
            lines.append(f"Section manifests ({len(self.sections)}):")
            for sm in self.sections:
                n_defs = len(sm.key_definitions)
                n_thms = len(sm.key_theorems)
                n_algs = len(sm.key_algorithms)
                lines.append(
                    f"  §{sm.section_number} {sm.title}  "
                    f"({n_defs} defs, {n_thms} thms, {n_algs} algs)"
                )

        summary = "\n".join(lines)
        logger.debug("PackageManifest.get_chapter_summary() called")
        return summary

    # ------------------------------------------------------------------ #
    def get_exported_symbols(self) -> list[str]:
        """Return the list of exported symbol names.

        Theory2.tex §9 — every named construct that is both defined in the
        chapter and implemented in formal_core should appear here.

        Returns
        -------
        list[str]
            Sorted, deduplicated list of exported symbol names.
        """
        unique = sorted(set(self.exported_symbols))
        logger.debug(
            "PackageManifest.get_exported_symbols(): %d unique symbols", len(unique)
        )
        return unique

    # ------------------------------------------------------------------ #
    def validate(self) -> bool:
        """Check that all required fields are present and consistent.

        Validation rules
        ----------------
        1. ``chapter_coverage`` must contain all keys in
           :attr:`_REQUIRED_COVERAGE_KEYS`.
        2. ``exported_symbols`` must be non-empty.
        3. ``version`` must be a non-empty string in ``X.Y.Z`` format.
        4. ``description`` must be a non-empty string.
        5. Every :class:`SectionManifest` must have a non-empty
           ``section_number`` and ``title``.

        Returns
        -------
        bool
            ``True`` if all validation rules pass, ``False`` otherwise.
            Violations are logged at WARNING level.
        """
        valid = True

        # Rule 1: chapter_coverage keys
        for key in self._REQUIRED_COVERAGE_KEYS:
            if key not in self.chapter_coverage:
                logger.warning(
                    "PackageManifest.validate: chapter_coverage missing key '%s'", key
                )
                valid = False

        # Rule 2: exported_symbols non-empty
        if not self.exported_symbols:
            logger.warning(
                "PackageManifest.validate: exported_symbols is empty"
            )
            valid = False

        # Rule 3: version format
        if not self.version:
            logger.warning("PackageManifest.validate: version is empty")
            valid = False
        else:
            parts = self.version.split(".")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                logger.warning(
                    "PackageManifest.validate: version '%s' is not in X.Y.Z format",
                    self.version,
                )
                valid = False

        # Rule 4: description
        if not self.description or not self.description.strip():
            logger.warning("PackageManifest.validate: description is empty")
            valid = False

        # Rule 5: section manifests
        for sm in self.sections:
            if not sm.section_number:
                logger.warning(
                    "PackageManifest.validate: a SectionManifest has empty section_number"
                )
                valid = False
            if not sm.title:
                logger.warning(
                    "PackageManifest.validate: section %s has empty title",
                    sm.section_number,
                )
                valid = False

        logger.info(
            "PackageManifest.validate(): %s", "PASS" if valid else "FAIL"
        )
        return valid

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation including all fields.
        """
        return {
            "chapter_coverage": dict(self.chapter_coverage),
            "exported_symbols": list(self.exported_symbols),
            "version": self.version,
            "description": self.description,
            "sections": [sm.to_dict() for sm in self.sections],
        }

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Deserialise a manifest from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest
            Reconstructed manifest.  Any missing optional fields are filled
            with their defaults.

        Raises
        ------
        ValueError
            If ``data`` is not a mapping or is missing critical fields.
        TypeError
            If ``exported_symbols`` is not a list.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"PackageManifest.from_dict: expected dict, got {type(data).__name__}"
            )

        exported = data.get("exported_symbols", list(_EXPORTED_SYMBOLS))
        if not isinstance(exported, list):
            raise TypeError(
                "PackageManifest.from_dict: 'exported_symbols' must be a list"
            )

        sections_raw = data.get("sections", [])
        sections = [SectionManifest.from_dict(s) for s in sections_raw]

        manifest = cls(
            chapter_coverage=dict(data.get("chapter_coverage", _CHAPTER_COVERAGE)),
            exported_symbols=exported,
            version=data.get("version", _VERSION),
            description=data.get("description", _DESCRIPTION),
            sections=sections,
        )
        logger.debug(
            "PackageManifest.from_dict(): created version=%s", manifest.version
        )
        return manifest

    # ------------------------------------------------------------------ #
    def get_section(self, section_number: str) -> SectionManifest | None:
        """Retrieve a :class:`SectionManifest` by its section number.

        Parameters
        ----------
        section_number:
            Dotted section number, e.g. ``"9.2"``.

        Returns
        -------
        SectionManifest or None
            The matching manifest, or ``None`` if not found.
        """
        for sm in self.sections:
            if sm.section_number == section_number:
                return sm
        logger.debug(
            "PackageManifest.get_section(%r): not found", section_number
        )
        return None

    # ------------------------------------------------------------------ #
    def register_symbols(self, registry: SymbolRegistry | None = None) -> SymbolRegistry:
        """Populate a :class:`SymbolRegistry` from :attr:`exported_symbols`.

        If *registry* is ``None`` the manifest's own :attr:`registry` is used
        and returned.  Otherwise *registry* is populated in place.

        Parameters
        ----------
        registry:
            Registry to populate.  Defaults to ``self.registry``.

        Returns
        -------
        SymbolRegistry
            The populated registry.
        """
        import importlib

        target_registry = registry if registry is not None else self.registry
        module_name = "jugeo.foundations.formal_core"

        for symbol_name in self.exported_symbols:
            try:
                mod = importlib.import_module(module_name)
                obj = getattr(mod, symbol_name, None)
                if obj is not None:
                    target_registry.register(
                        symbol_name,
                        obj,
                        f"Exported from {module_name} (Theory2.tex §9)",
                    )
                else:
                    logger.debug(
                        "PackageManifest.register_symbols: '%s' not found in %s",
                        symbol_name,
                        module_name,
                    )
            except ImportError as exc:
                logger.warning(
                    "PackageManifest.register_symbols: could not import %s: %s",
                    module_name,
                    exc,
                )
                break

        logger.info(
            "PackageManifest.register_symbols(): registered %d/%d symbols",
            len(target_registry),
            len(self.exported_symbols),
        )
        return target_registry

    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:
        chapter = self.chapter_coverage.get("chapter", "?")
        return (
            f"PackageManifest(chapter={chapter!r}, "
            f"version={self.version!r}, "
            f"symbols={len(self.exported_symbols)})"
        )


# ---------------------------------------------------------------------------
# Module-level default manifest instance
# ---------------------------------------------------------------------------

#: The canonical manifest for this package.  Import and inspect it directly:
#:
#:   >>> from jugeo.foundations.formal_core.manifest import DEFAULT_MANIFEST
#:   >>> print(DEFAULT_MANIFEST.get_chapter_summary())
DEFAULT_MANIFEST: PackageManifest = PackageManifest()

logger.debug(
    "formal_core manifest loaded: version=%s, has_judgment_site=%s, "
    "has_solver_router=%s, has_solver_fragments=%s",
    _VERSION,
    _HAS_JUDGMENT_SITE,
    _HAS_SOLVER_ROUTER,
    _HAS_SOLVER_FRAGMENTS,
)

__all__ = [
    "PackageManifest",
    "SectionManifest",
    "SymbolRegistry",
    "DEFAULT_MANIFEST",
]
