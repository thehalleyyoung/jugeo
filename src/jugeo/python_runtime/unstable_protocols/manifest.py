"""Package manifest for JuGeo unstable_protocols (Ch22).

This module provides version metadata, symbol catalogues, and theory-alignment
records for the unstable_protocols package.  Every exported symbol is registered
as a :class:`SymbolRecord`; the aggregate :class:`UnstableProtocolsManifest`
exposes query helpers so tooling can inspect the package without importing it.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1  Protocol sections – behavioral sections over semantic coordinates
* §2  Proxy delegation – transport restrictions as cover conditions
* §3  Unstable surfaces – boundaries where support is actively retracting
* §4  Stability monitors – drift detection between declared/observed behavior
* §5  Delegation morphisms – morphisms between protocol sections

The manifest deliberately mirrors the sheaf-theoretic metaphor: just as a sheaf
assigns data to every open set in a cover, the manifest assigns a :class:`SymbolRecord`
to every name exported from the package.  Validation checks that every symbol has a
non-empty theory reference, ensuring nothing floats free of the formal framework.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PACKAGE_VERSION: str = "0.1.0"
THEORY_CHAPTER: str = "Ch22"
AUTHOR: str = "copilot"
STABILITY_LEVELS: list[str] = [
    "stable",
    "degrading",
    "unstable",
    "retracting",
    "collapsed",
]


# ---------------------------------------------------------------------------
# SymbolRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolRecord:
    """Metadata for a single exported symbol in the unstable_protocols package.

    Each record captures the symbol's origin module, a human-readable
    description, its current stability level (one of :data:`STABILITY_LEVELS`),
    a reference to the theory section that motivates it, and the package
    version in which it was introduced.

    Parameters
    ----------
    name:
        The Python identifier as it appears in ``__all__``.
    origin_module:
        Dotted module path relative to the package root (e.g. ``"models"``).
    description:
        One-sentence description of the symbol's purpose.
    stability_level:
        One of the values in :data:`STABILITY_LEVELS`.
    theory_ref:
        Section reference such as ``"Ch22§1"`` or ``"Ch22§2.3"``.
    since_version:
        Semver string of the package version where the symbol was added.
    """

    name: str
    origin_module: str
    description: str
    stability_level: str
    theory_ref: str
    since_version: str

    def __post_init__(self) -> None:
        """Validate field values at construction time."""
        if self.stability_level not in STABILITY_LEVELS:
            raise ValueError(
                f"stability_level {self.stability_level!r} not in {STABILITY_LEVELS}"
            )
        if not self.name:
            raise ValueError("name must not be empty")
        if not self.theory_ref:
            raise ValueError("theory_ref must not be empty")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_stable(self) -> bool:
        """Return True when the symbol's stability level is ``'stable'``."""
        return self.stability_level == "stable"

    def to_dict(self) -> dict[str, Any]:
        """Serialise the record to a plain JSON-compatible dictionary."""
        return {
            "name": self.name,
            "origin_module": self.origin_module,
            "description": self.description,
            "stability_level": self.stability_level,
            "theory_ref": self.theory_ref,
            "since_version": self.since_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SymbolRecord:
        """Reconstruct a :class:`SymbolRecord` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary with keys matching the dataclass fields.
        """
        return cls(
            name=data["name"],
            origin_module=data["origin_module"],
            description=data["description"],
            stability_level=data["stability_level"],
            theory_ref=data["theory_ref"],
            since_version=data["since_version"],
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary string."""
        stable_tag = "✓" if self.is_stable() else "⚠"
        return (
            f"{stable_tag} [{self.origin_module}] {self.name} "
            f"(level={self.stability_level}, ref={self.theory_ref}, since={self.since_version})"
        )


# ---------------------------------------------------------------------------
# UnstableProtocolsManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnstableProtocolsManifest:
    """Immutable manifest describing the entire unstable_protocols package.

    The manifest collects every exported symbol as a :class:`SymbolRecord` and
    exposes query helpers for tooling.  It is frozen so it can be hashed,
    cached, and passed between subsystems without defensive copying.

    Parameters
    ----------
    package_name:
        Fully qualified package name, e.g. ``"jugeo.python_runtime.unstable_protocols"``.
    version:
        Semver string.
    theory_chapter:
        Chapter identifier, e.g. ``"Ch22"``.
    exported_symbols:
        Ordered tuple of every :class:`SymbolRecord` exported from the package.
    description:
        Multi-sentence package description.
    author:
        Author tag; defaults to ``"copilot"``.
    created_at:
        Unix timestamp of manifest creation.
    tags:
        Frozen set of free-form string tags for search/filtering.
    """

    package_name: str
    version: str
    theory_chapter: str
    exported_symbols: tuple[SymbolRecord, ...]
    description: str
    author: str = "copilot"
    created_at: float = field(default_factory=time.time)
    tags: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def symbol_count(self) -> int:
        """Return the number of exported symbols recorded in this manifest."""
        return len(self.exported_symbols)

    def find_symbol(self, name: str) -> SymbolRecord | None:
        """Look up a symbol by its Python identifier name.

        Parameters
        ----------
        name:
            Exact identifier string, e.g. ``"ProtocolSection"``.

        Returns
        -------
        SymbolRecord | None
            The matching record, or ``None`` if not found.
        """
        for sym in self.exported_symbols:
            if sym.name == name:
                return sym
        return None

    def symbols_by_module(self) -> dict[str, list[SymbolRecord]]:
        """Group all exported symbols by their origin module.

        Returns
        -------
        dict[str, list[SymbolRecord]]
            Mapping from module name to list of symbol records.
        """
        groups: dict[str, list[SymbolRecord]] = defaultdict(list)
        for sym in self.exported_symbols:
            groups[sym.origin_module].append(sym)
        return dict(groups)

    def stable_symbols(self) -> list[SymbolRecord]:
        """Return only those symbols whose stability level is ``'stable'``."""
        return [s for s in self.exported_symbols if s.is_stable()]

    def unstable_symbols(self) -> list[SymbolRecord]:
        """Return all symbols whose stability level is not ``'stable'``."""
        return [s for s in self.exported_symbols if not s.is_stable()]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the manifest to a plain JSON-compatible dictionary."""
        return {
            "package_name": self.package_name,
            "version": self.version,
            "theory_chapter": self.theory_chapter,
            "exported_symbols": [s.to_dict() for s in self.exported_symbols],
            "description": self.description,
            "author": self.author,
            "created_at": self.created_at,
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnstableProtocolsManifest:
        """Reconstruct a manifest from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`to_dict`.
        """
        return cls(
            package_name=data["package_name"],
            version=data["version"],
            theory_chapter=data["theory_chapter"],
            exported_symbols=tuple(
                SymbolRecord.from_dict(s) for s in data["exported_symbols"]
            ),
            description=data["description"],
            author=data.get("author", "copilot"),
            created_at=data.get("created_at", time.time()),
            tags=frozenset(data.get("tags", [])),
        )

    def summary_report(self) -> str:
        """Return a multi-line human-readable summary of the manifest."""
        lines: list[str] = [
            f"Package  : {self.package_name} v{self.version}",
            f"Chapter  : {self.theory_chapter}",
            f"Author   : {self.author}",
            f"Tags     : {', '.join(sorted(self.tags)) or '—'}",
            f"Symbols  : {self.symbol_count()} "
            f"({len(self.stable_symbols())} stable, "
            f"{len(self.unstable_symbols())} unstable)",
            "",
        ]
        for module, syms in sorted(self.symbols_by_module().items()):
            lines.append(f"  {module}:")
            for sym in syms:
                lines.append(f"    {sym.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


@dataclass
class ManifestValidator:
    """Validates a :class:`UnstableProtocolsManifest` against consistency rules.

    Validation checks that:

    * Every exported symbol has a non-empty ``theory_ref``.
    * The version string follows semver (``MAJOR.MINOR.PATCH``).
    * Every ``theory_ref`` begins with the manifest's ``theory_chapter``.
    * No two symbols share the same name.

    In strict mode every check that fails is treated as an error;
    in lenient mode only structural issues are errors and alignment issues
    become warnings.

    Parameters
    ----------
    manifest:
        The manifest to validate.
    strict:
        When ``True`` (default), theory alignment failures are errors.
    errors:
        Accumulated error messages (populated by :meth:`validate_exports`).
    warnings:
        Accumulated warning messages.
    """

    manifest: UnstableProtocolsManifest
    strict: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def validate_exports(self) -> bool:
        """Validate every symbol in the manifest's exported_symbols list.

        Returns
        -------
        bool
            ``True`` when no errors were found.
        """
        seen_names: set[str] = set()
        for sym in self.manifest.exported_symbols:
            if sym.name in seen_names:
                self.errors.append(f"Duplicate symbol name: {sym.name!r}")
            seen_names.add(sym.name)
            self._check_symbol(sym)
        return len(self.errors) == 0

    def check_completeness(self) -> bool:
        """Check that the manifest has a non-empty description and author.

        Returns
        -------
        bool
            ``True`` when completeness requirements are satisfied.
        """
        ok = True
        if not self.manifest.description.strip():
            self.errors.append("Manifest description is empty")
            ok = False
        if not self.manifest.author.strip():
            self.errors.append("Manifest author is empty")
            ok = False
        if self.manifest.symbol_count() == 0:
            self.warnings.append("Manifest has no exported symbols")
        return ok

    def cross_reference_theory(self) -> bool:
        """Check that every symbol's theory_ref aligns with the manifest chapter.

        Returns
        -------
        bool
            ``True`` when all refs are consistent.
        """
        ok = True
        for sym in self.manifest.exported_symbols:
            if not self._validate_theory_ref(sym.theory_ref):
                msg = (
                    f"Symbol {sym.name!r} theory_ref {sym.theory_ref!r} "
                    f"does not begin with chapter {self.manifest.theory_chapter!r}"
                )
                if self.strict:
                    self.errors.append(msg)
                    ok = False
                else:
                    self.warnings.append(msg)
        return ok

    def generate_report(self) -> dict[str, Any]:
        """Run all checks and return a structured validation report.

        Returns
        -------
        dict[str, Any]
            Keys: ``passed``, ``error_count``, ``warning_count``,
            ``errors``, ``warnings``.
        """
        self.reset()
        self.validate_exports()
        self.check_completeness()
        self.cross_reference_theory()
        return {
            "passed": len(self.errors) == 0,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def reset(self) -> None:
        """Clear all accumulated errors and warnings."""
        self.errors.clear()
        self.warnings.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_symbol(self, symbol: SymbolRecord) -> None:
        """Validate a single symbol record, appending to errors/warnings."""
        if not symbol.description.strip():
            self.warnings.append(f"Symbol {symbol.name!r} has no description")
        if not self._check_version_format(symbol.since_version):
            self.errors.append(
                f"Symbol {symbol.name!r} has invalid since_version {symbol.since_version!r}"
            )

    def _check_version_format(self, version: str) -> bool:
        """Return True if version matches MAJOR.MINOR.PATCH."""
        parts = version.split(".")
        if len(parts) != 3:
            return False
        return all(p.isdigit() for p in parts)

    def _validate_theory_ref(self, ref: str) -> bool:
        """Return True if ref starts with the manifest's theory_chapter."""
        return ref.startswith(self.manifest.theory_chapter)


# ---------------------------------------------------------------------------
# ManifestRegistry
# ---------------------------------------------------------------------------


@dataclass
class ManifestRegistry:
    """Registry of multiple manifest versions, supporting lookup and diffing.

    The registry acts as a version history for the package manifest.
    Each version is stored under its semver string and can be retrieved,
    compared, or pruned.

    Parameters
    ----------
    manifests:
        Mapping from version string to :class:`UnstableProtocolsManifest`.
    current_version:
        Semver string of the currently active manifest.
    """

    manifests: dict[str, UnstableProtocolsManifest] = field(default_factory=dict)
    current_version: str = PACKAGE_VERSION

    def register(self, manifest: UnstableProtocolsManifest) -> None:
        """Add a manifest to the registry, keyed by its version string.

        Parameters
        ----------
        manifest:
            The manifest to register.
        """
        self.manifests[manifest.version] = manifest
        self.current_version = manifest.version

    def lookup(self, version: str) -> UnstableProtocolsManifest | None:
        """Retrieve a manifest by version string.

        Parameters
        ----------
        version:
            Semver string, e.g. ``"0.1.0"``.
        """
        return self.manifests.get(version)

    def list_versions(self) -> list[str]:
        """Return a sorted list of all registered version strings."""
        return sorted(self.manifests.keys())

    def latest(self) -> UnstableProtocolsManifest | None:
        """Return the most recently registered manifest, or ``None``."""
        return self.manifests.get(self.current_version)

    def diff(self, v1: str, v2: str) -> dict[str, Any]:
        """Compute the symbol-level diff between two manifest versions.

        Parameters
        ----------
        v1, v2:
            Version strings to compare.

        Returns
        -------
        dict[str, Any]
            Keys: ``added``, ``removed``, ``changed`` (list of symbol names).
        """
        m1 = self.lookup(v1)
        m2 = self.lookup(v2)
        if m1 is None or m2 is None:
            return {"error": f"Version(s) not found: {v1!r}, {v2!r}"}
        names1 = {s.name: s for s in m1.exported_symbols}
        names2 = {s.name: s for s in m2.exported_symbols}
        added = [n for n in names2 if n not in names1]
        removed = [n for n in names1 if n not in names2]
        changed = [
            n
            for n in names1
            if n in names2 and names1[n].to_dict() != names2[n].to_dict()
        ]
        return {"added": added, "removed": removed, "changed": changed}

    def prune_old(self, keep_n: int = 5) -> int:
        """Remove old manifest versions, keeping only the ``keep_n`` most recent.

        Parameters
        ----------
        keep_n:
            Number of versions to retain.

        Returns
        -------
        int
            Number of manifests removed.
        """
        versions = sorted(self.manifests.keys())
        to_remove = versions[: max(0, len(versions) - keep_n)]
        for v in to_remove:
            del self.manifests[v]
        return len(to_remove)

    def export_registry(self) -> dict[str, Any]:
        """Serialise the entire registry to a plain dictionary."""
        return {
            "current_version": self.current_version,
            "manifests": {v: m.to_dict() for v, m in self.manifests.items()},
        }


# ---------------------------------------------------------------------------
# TheoryAlignment
# ---------------------------------------------------------------------------


@dataclass
class TheoryAlignment:
    """Tracks the alignment between package symbols and theory sections.

    Each entry in :attr:`section_refs` maps a short key such as ``"§1"`` to a
    human-readable reference like ``"Ch22§1 – Protocol sections"``.  The
    :attr:`theorem_ids` list enumerates every theorem the package depends on.

    Parameters
    ----------
    chapter:
        Theory chapter identifier, e.g. ``"Ch22"``.
    section_refs:
        Mapping from short key to full reference string.
    theorem_ids:
        List of theorem IDs (e.g. ``["T22.1", "T22.2"]``).
    alignment_notes:
        Free-form notes describing the alignment.
    """

    chapter: str
    section_refs: dict[str, str] = field(default_factory=dict)
    theorem_ids: list[str] = field(default_factory=list)
    alignment_notes: str = ""

    def add_section(self, key: str, ref: str) -> None:
        """Register a section reference under ``key``.

        Parameters
        ----------
        key:
            Short key, e.g. ``"§1"``.
        ref:
            Full reference, e.g. ``"Ch22§1 – Protocol sections"``.
        """
        self.section_refs[key] = ref

    def get_ref(self, key: str) -> str | None:
        """Return the full reference for ``key``, or ``None`` if not found."""
        return self.section_refs.get(key)

    def aligned_theorems(self) -> list[str]:
        """Return a copy of the theorem IDs list."""
        return list(self.theorem_ids)

    def validate_alignment(self) -> bool:
        """Return True when every section_ref value starts with the chapter."""
        return all(v.startswith(self.chapter) for v in self.section_refs.values())

    def report(self) -> str:
        """Return a multi-line text report of the alignment."""
        lines = [
            f"Theory alignment for {self.chapter}",
            "=" * 40,
        ]
        for key, ref in sorted(self.section_refs.items()):
            lines.append(f"  {key}: {ref}")
        lines.append(f"Theorems ({len(self.theorem_ids)}): {', '.join(self.theorem_ids)}")
        if self.alignment_notes:
            lines.append(f"Notes: {self.alignment_notes}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "chapter": self.chapter,
            "section_refs": dict(self.section_refs),
            "theorem_ids": list(self.theorem_ids),
            "alignment_notes": self.alignment_notes,
        }


# ---------------------------------------------------------------------------
# DEFAULT_MANIFEST – module-level constant with 15 SymbolRecord entries
# ---------------------------------------------------------------------------

_SYM = SymbolRecord  # brevity alias

DEFAULT_MANIFEST: UnstableProtocolsManifest = UnstableProtocolsManifest(
    package_name="jugeo.python_runtime.unstable_protocols",
    version=PACKAGE_VERSION,
    theory_chapter=THEORY_CHAPTER,
    description=(
        "JuGeo unstable_protocols package: sheaf-theoretic semantic verification "
        "for protocol sections, proxy delegation, unstable surfaces, and delegation "
        "morphisms as described in Ch22 of theory2.tex."
    ),
    author=AUTHOR,
    created_at=0.0,
    tags=frozenset(["unstable", "protocols", "sheaf", "Ch22"]),
    exported_symbols=(
        _SYM("StabilityLevel", "models", "Enum of protocol stability levels.", "stable", "Ch22§4", "0.1.0"),
        _SYM("ProxyRestriction", "models", "Enum of proxy transport restrictions.", "stable", "Ch22§2", "0.1.0"),
        _SYM("DelegationKind", "models", "Enum of delegation morphism kinds.", "stable", "Ch22§2", "0.1.0"),
        _SYM("ProtocolSection", "models", "Behavioral section over a semantic coordinate.", "stable", "Ch22§1", "0.1.0"),
        _SYM("ProxyRecord", "models", "Transport-restricted section proxy record.", "stable", "Ch22§2", "0.1.0"),
        _SYM("DelegationChain", "models", "Chain of delegation morphisms between sections.", "stable", "Ch22§2", "0.1.0"),
        _SYM("UnstableInterface", "models", "Boundary where support is actively retracting.", "unstable", "Ch22§3", "0.1.0"),
        _SYM("StabilityMonitor", "models", "Drift detector between declared and observed behavior.", "stable", "Ch22§4", "0.1.0"),
        _SYM("ProtocolSectionManager", "protocol_sections", "CRUD registry for protocol sections.", "stable", "Ch22§1", "0.1.0"),
        _SYM("ProtocolDescentEngine", "protocol_sections", "Restricts protocol sections to sub-coordinates.", "stable", "Ch22§1", "0.1.0"),
        _SYM("ProtocolGluer", "protocol_sections", "Glues local sections into a global section.", "stable", "Ch22§1", "0.1.0"),
        _SYM("StalenessDetector", "protocol_sections", "Detects stale protocol sections.", "stable", "Ch22§1", "0.1.0"),
        _SYM("ProxyManager", "proxy_delegation", "Manages proxy records and access control.", "stable", "Ch22§2", "0.1.0"),
        _SYM("DelegationMorphism", "proxy_delegation", "Morphism between two protocol sections.", "stable", "Ch22§2", "0.1.0"),
        _SYM("SurfaceTracker", "unstable_surfaces", "Tracks unstable object surfaces.", "unstable", "Ch22§3", "0.1.0"),
    ),
)

# ---------------------------------------------------------------------------

__all__ = [
    "PACKAGE_VERSION",
    "THEORY_CHAPTER",
    "AUTHOR",
    "STABILITY_LEVELS",
    "SymbolRecord",
    "UnstableProtocolsManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "TheoryAlignment",
    "DEFAULT_MANIFEST",
]

# copilot: manifest.py – package metadata, symbol catalogue, and theory-alignment for unstable_protocols
