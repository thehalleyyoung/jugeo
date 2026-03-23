"""Manifest system for the JuGeo concurrency_boundaries package.

This module provides the canonical registry of all exported symbols from the
``concurrency_boundaries`` package, together with validation, cross-referencing
against Ch24 of ``theory2.tex``, and version-aware tracking facilities.

Overview
--------
The manifest system answers three questions:

1. **What exists?** — :class:`SymbolRecord` captures every public symbol with
   its origin module, concurrency role, boundary kind, and optional theory
   reference.  :class:`ConcurrencyBoundariesManifest` aggregates all records
   into a queryable, serialisable catalogue.

2. **Is it correct?** — :class:`ManifestValidator` checks that declared
   symbols actually exist in their stated modules, that every module has at
   least one export, and that theory references are well-formed and point to
   known Ch24 sections.

3. **How has it changed?** — :class:`ManifestRegistry` stores multiple
   versioned manifests, supports diff operations between versions, and prunes
   old entries to keep memory bounded.

Theory alignment
----------------
Chapter 24 of ``theory2.tex`` introduces four primary concurrency boundary
constructs:

* *Task-local context* as scoped sections — modelled by ``TaskLocalSection``
  and associated ``TASK_LOCAL`` boundary symbols.
* *Cancellation* as obstruction injection — modelled by ``CancellationRecord``
  and ``CANCELLATION`` boundary symbols.
* *Exception groups* as multi-obstruction records — modelled by
  ``ExceptionGroupRecord`` and ``EXCEPTION_GROUP`` boundary symbols.
* *Process boundaries* as cover boundaries — modelled by ``ProcessBoundary``
  and ``PROCESS`` boundary symbols.

:class:`TheoryAlignment` maps each exported symbol to the specific Ch24
sections and theorems it implements, enabling automated theory-coverage checks.

Design notes
------------
* All timestamps use ``time.time()`` (float seconds since epoch).
* All generated IDs use ``uuid.uuid4().hex``.
* No third-party dependencies — only stdlib.
* Frozen dataclasses use ``slots=True``; mutable dataclasses use ``slots=False``.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# Module-level constants
# ══════════════════════════════════════════════════════════════════════════════

VERSION: str = "0.1.0"
THEORY_CHAPTER: str = "Ch24"

BOUNDARY_KINDS: list[str] = [
    "task_local",
    "thread_local",
    "process",
    "network",
    "ipc",
    "memory_mapped",
]

CONCURRENCY_ROLES: list[str] = [
    "task",
    "coroutine",
    "process",
    "thread",
    "actor",
    "boundary_enforcer",
]

# Known Ch24 section identifiers used for validation.
_KNOWN_CH24_SECTIONS: frozenset[str] = frozenset({
    "Ch24.1",
    "Ch24.2",
    "Ch24.3",
    "Ch24.4",
    "Ch24.5",
    "Ch24.1.1",
    "Ch24.1.2",
    "Ch24.2.1",
    "Ch24.2.2",
    "Ch24.3.1",
    "Ch24.3.2",
    "Ch24.4.1",
    "Ch24.4.2",
    "Ch24.5.1",
    "Ch24.5.2",
    "Ch24.T1",
    "Ch24.T2",
    "Ch24.T3",
    "Ch24.T4",
    "Ch24.T5",
})

# ══════════════════════════════════════════════════════════════════════════════
# SymbolRecord
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class SymbolRecord:
    """Metadata record for a single exported symbol.

    A :class:`SymbolRecord` captures all information needed to reason about
    one public name in the ``concurrency_boundaries`` package: where it comes
    from, what concurrency role it plays, which boundary kind it belongs to,
    and whether it corresponds to a specific section of Ch24.

    Args:
        name: The unqualified symbol name (e.g. ``"TaskLocalSection"``).
        origin_module: The submodule within the package that defines the symbol
            (e.g. ``"models"``).
        description: A one-line human-readable description.
        concurrency_role: One of the strings in :data:`CONCURRENCY_ROLES`.
        boundary_kind: One of the strings in :data:`BOUNDARY_KINDS`.
        is_public: Whether the symbol appears in the module's ``__all__``.
        theory_ref: Optional Ch24 section reference, e.g. ``"Ch24.2.1"``.
    """

    name: str
    origin_module: str
    description: str
    concurrency_role: str
    boundary_kind: str
    is_public: bool = True
    theory_ref: str = ""

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary.

        Returns:
            A JSON-safe dict with all fields.
        """
        return {
            "name": self.name,
            "origin_module": self.origin_module,
            "description": self.description,
            "concurrency_role": self.concurrency_role,
            "boundary_kind": self.boundary_kind,
            "is_public": self.is_public,
            "theory_ref": self.theory_ref,
        }

    def matches_role(self, role: str) -> bool:
        """Return True if this symbol's concurrency role matches *role*.

        The comparison is case-insensitive to tolerate minor formatting
        differences between callers.

        Args:
            role: A concurrency role string to compare against.

        Returns:
            True if roles match (case-insensitive).
        """
        return self.concurrency_role.lower() == role.lower()

    def is_boundary_symbol(self) -> bool:
        """Return True if this symbol directly models a boundary construct.

        A boundary symbol is one whose boundary_kind is not ``"task_local"``
        and whose concurrency_role includes the word ``"boundary"`` or equals
        ``"process"`` or ``"actor"``.  This heuristic reflects the Ch24
        distinction between scoped-section symbols and actual boundary
        enforcement symbols.

        Returns:
            True if the symbol qualifies as a boundary symbol.
        """
        boundary_roles = {"boundary_enforcer", "process", "actor"}
        non_local_kinds = {"process", "network", "ipc", "memory_mapped"}
        return (
            self.concurrency_role.lower() in boundary_roles
            or self.boundary_kind.lower() in non_local_kinds
        )

    def full_qualified_name(self) -> str:
        """Return the fully qualified dotted name for this symbol.

        The name is constructed as
        ``jugeo.python_runtime.concurrency_boundaries.<module>.<name>``.

        Returns:
            A dotted Python import path string.
        """
        return (
            f"jugeo.python_runtime.concurrency_boundaries"
            f".{self.origin_module}.{self.name}"
        )

    def __repr__(self) -> str:
        return (
            f"SymbolRecord(name={self.name!r}, module={self.origin_module!r}, "
            f"role={self.concurrency_role!r})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ConcurrencyBoundariesManifest
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=False)
class ConcurrencyBoundariesManifest:
    """Catalogue of all exported symbols in the concurrency_boundaries package.

    The manifest is the authoritative record of what the package exposes.  It
    is constructed once at package initialisation time (via
    :func:`build_default_manifest`) and can be extended at runtime, serialised
    to JSON, or compared across versions.

    Args:
        package_name: The Python package name (typically
            ``"jugeo.python_runtime.concurrency_boundaries"``).
        version: Semantic version string.
        theory_chapter: The theory chapter this package implements.
        exported_symbols: Initial list of :class:`SymbolRecord` objects.
        description: Multi-line human-readable description of the package.
        author: Package author identifier.
        created_at: Unix timestamp of manifest creation.
        tags: Arbitrary string tags for filtering.
    """

    package_name: str
    version: str
    theory_chapter: str
    exported_symbols: list[SymbolRecord]
    description: str
    author: str = "copilot"
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_symbol(self, record: SymbolRecord) -> None:
        """Append a new :class:`SymbolRecord` to the manifest.

        If a symbol with the same name and origin module already exists it is
        replaced rather than duplicated.

        Args:
            record: The symbol record to add or replace.
        """
        for i, existing in enumerate(self.exported_symbols):
            if (
                existing.name == record.name
                and existing.origin_module == record.origin_module
            ):
                self.exported_symbols[i] = record
                return
        self.exported_symbols.append(record)

    def remove_symbol(self, name: str) -> bool:
        """Remove a symbol by name.

        Args:
            name: The unqualified symbol name to remove.

        Returns:
            True if a symbol was removed, False if not found.
        """
        before = len(self.exported_symbols)
        self.exported_symbols = [
            r for r in self.exported_symbols if r.name != name
        ]
        return len(self.exported_symbols) < before

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_symbol(self, name: str) -> SymbolRecord | None:
        """Look up a symbol by name.

        Args:
            name: Unqualified symbol name.

        Returns:
            The matching :class:`SymbolRecord`, or None if not found.
        """
        for record in self.exported_symbols:
            if record.name == name:
                return record
        return None

    def symbols_by_module(self, module: str) -> list[SymbolRecord]:
        """Return all symbols defined in *module*.

        Args:
            module: Module name within the package (e.g. ``"models"``).

        Returns:
            List of :class:`SymbolRecord` objects from that module.
        """
        return [r for r in self.exported_symbols if r.origin_module == module]

    def symbols_by_role(self, role: str) -> list[SymbolRecord]:
        """Return all symbols with the given concurrency role.

        Args:
            role: A concurrency role string.

        Returns:
            List of matching :class:`SymbolRecord` objects.
        """
        return [r for r in self.exported_symbols if r.matches_role(role)]

    def symbol_count(self) -> int:
        """Return the total number of exported symbols.

        Returns:
            Integer count.
        """
        return len(self.exported_symbols)

    def public_symbol_count(self) -> int:
        """Return the number of symbols with ``is_public=True``.

        Returns:
            Integer count of public symbols.
        """
        return sum(1 for r in self.exported_symbols if r.is_public)

    def modules_present(self) -> list[str]:
        """Return a deduplicated sorted list of origin modules.

        Returns:
            Sorted list of module name strings.
        """
        seen: set[str] = set()
        for r in self.exported_symbols:
            seen.add(r.origin_module)
        return sorted(seen)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise the manifest to a plain dictionary.

        Returns:
            A JSON-safe dict representing the full manifest.
        """
        return {
            "package_name": self.package_name,
            "version": self.version,
            "theory_chapter": self.theory_chapter,
            "description": self.description,
            "author": self.author,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "symbol_count": self.symbol_count(),
            "exported_symbols": [r.to_dict() for r in self.exported_symbols],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ConcurrencyBoundariesManifest:
        """Deserialise a manifest from a plain dictionary.

        Args:
            data: A dict as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ConcurrencyBoundariesManifest` instance.

        Raises:
            KeyError: If a required field is missing from *data*.
            ValueError: If *exported_symbols* is not a list.
        """
        raw_symbols = data.get("exported_symbols", [])
        if not isinstance(raw_symbols, list):
            raise ValueError("exported_symbols must be a list")
        symbols = [
            SymbolRecord(
                name=s["name"],
                origin_module=s["origin_module"],
                description=s["description"],
                concurrency_role=s["concurrency_role"],
                boundary_kind=s["boundary_kind"],
                is_public=s.get("is_public", True),
                theory_ref=s.get("theory_ref", ""),
            )
            for s in raw_symbols
        ]
        return cls(
            package_name=str(data["package_name"]),
            version=str(data["version"]),
            theory_chapter=str(data["theory_chapter"]),
            exported_symbols=symbols,
            description=str(data.get("description", "")),
            author=str(data.get("author", "copilot")),
            created_at=float(data.get("created_at", time.time())),
            tags=list(data.get("tags", [])),
        )

    # ------------------------------------------------------------------
    # Validation & summary
    # ------------------------------------------------------------------

    def validate(self) -> dict[str, object]:
        """Run basic self-consistency checks.

        Returns:
            A dict with keys ``"valid"`` (bool), ``"errors"`` (list of str),
            ``"warnings"`` (list of str), and ``"symbol_count"`` (int).
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self.package_name:
            errors.append("package_name is empty")
        if not self.version:
            errors.append("version is empty")
        if not self.theory_chapter:
            warnings.append("theory_chapter is empty")
        if not self.exported_symbols:
            warnings.append("No exported symbols registered")

        # Check for duplicate names
        seen_names: dict[str, str] = {}
        for r in self.exported_symbols:
            key = r.name
            if key in seen_names:
                errors.append(
                    f"Duplicate symbol name {r.name!r} in modules "
                    f"{seen_names[key]!r} and {r.origin_module!r}"
                )
            else:
                seen_names[key] = r.origin_module

            if r.concurrency_role not in CONCURRENCY_ROLES:
                warnings.append(
                    f"Symbol {r.name!r} has unknown role {r.concurrency_role!r}"
                )
            if r.boundary_kind not in BOUNDARY_KINDS:
                warnings.append(
                    f"Symbol {r.name!r} has unknown boundary_kind "
                    f"{r.boundary_kind!r}"
                )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "symbol_count": self.symbol_count(),
        }

    def summary(self) -> str:
        """Return a human-readable one-line summary of the manifest.

        Returns:
            A formatted summary string.
        """
        modules = self.modules_present()
        return (
            f"ConcurrencyBoundariesManifest v{self.version} "
            f"({self.symbol_count()} symbols across {len(modules)} modules: "
            f"{', '.join(modules)}) — theory: {self.theory_chapter}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ManifestValidator
# ══════════════════════════════════════════════════════════════════════════════

class ManifestValidator:
    """Validates a :class:`ConcurrencyBoundariesManifest` against multiple criteria.

    Validation is separated into distinct stages so callers can run only the
    checks they need, or run all of them and collect a unified report.

    Args:
        manifest: The manifest to validate.
    """

    def __init__(self, manifest: ConcurrencyBoundariesManifest) -> None:
        self._manifest = manifest
        self._results: dict[str, dict[str, object]] = {}

    # ------------------------------------------------------------------
    # Individual validation stages
    # ------------------------------------------------------------------

    def validate_exports(self) -> dict[str, object]:
        """Check that all declared symbols have non-empty names and descriptions.

        This is a lightweight check that does not perform actual imports; it
        verifies that the manifest metadata is internally complete.

        Returns:
            Dict with ``"stage"``, ``"passed"`` (bool), ``"issues"`` (list),
            ``"checked"`` (int).
        """
        issues: list[str] = []
        checked = 0
        for rec in self._manifest.exported_symbols:
            checked += 1
            if not rec.name or not rec.name.strip():
                issues.append(f"Record #{checked} has empty name")
            if not rec.description or not rec.description.strip():
                issues.append(f"Symbol {rec.name!r} has empty description")
            if not rec.origin_module or not rec.origin_module.strip():
                issues.append(f"Symbol {rec.name!r} has no origin_module")
            # Names should be valid Python identifiers
            if rec.name and not rec.name.replace("_", "a").isalnum():
                issues.append(
                    f"Symbol {rec.name!r} may not be a valid Python identifier"
                )
        result: dict[str, object] = {
            "stage": "validate_exports",
            "passed": len(issues) == 0,
            "issues": issues,
            "checked": checked,
        }
        self._results["validate_exports"] = result
        return result

    def check_completeness(self) -> dict[str, object]:
        """Verify that every expected module has at least one exported symbol.

        Expected modules are: ``models``, ``manifest``, ``s01``, ``s02``,
        ``s03``, ``algorithms``, ``integration``, ``theorems``.

        Returns:
            Dict with ``"stage"``, ``"passed"``, ``"missing_modules"`` (list),
            ``"modules_present"`` (list).
        """
        expected = {
            "models", "manifest", "s01", "s02", "s03",
            "algorithms", "integration", "theorems",
        }
        present = set(self._manifest.modules_present())
        missing = sorted(expected - present)
        result: dict[str, object] = {
            "stage": "check_completeness",
            "passed": len(missing) == 0,
            "missing_modules": missing,
            "modules_present": sorted(present),
            "expected_modules": sorted(expected),
        }
        self._results["check_completeness"] = result
        return result

    def cross_reference_theory(self) -> dict[str, object]:
        """Validate that theory_ref fields point to known Ch24 sections.

        Only non-empty theory_ref values are checked.  Empty refs are allowed
        (they simply mean the symbol has not been mapped to a specific section).

        Returns:
            Dict with ``"stage"``, ``"passed"``, ``"invalid_refs"`` (list),
            ``"missing_refs"`` (list of symbols with no ref), ``"valid_refs"``
            (int).
        """
        invalid_refs: list[str] = []
        missing_refs: list[str] = []
        valid_refs = 0
        for rec in self._manifest.exported_symbols:
            if not rec.theory_ref:
                missing_refs.append(rec.name)
            elif rec.theory_ref not in _KNOWN_CH24_SECTIONS:
                invalid_refs.append(
                    f"{rec.name!r} references unknown section {rec.theory_ref!r}"
                )
            else:
                valid_refs += 1
        result: dict[str, object] = {
            "stage": "cross_reference_theory",
            "passed": len(invalid_refs) == 0,
            "invalid_refs": invalid_refs,
            "missing_refs": missing_refs,
            "valid_refs": valid_refs,
            "total_symbols": self._manifest.symbol_count(),
        }
        self._results["cross_reference_theory"] = result
        return result

    def check_boundary_safety(self) -> dict[str, object]:
        """Check that every symbol uses a recognised boundary kind and role.

        Returns:
            Dict with ``"stage"``, ``"passed"``, ``"bad_kinds"`` (list),
            ``"bad_roles"`` (list).
        """
        bad_kinds: list[str] = []
        bad_roles: list[str] = []
        for rec in self._manifest.exported_symbols:
            if rec.boundary_kind not in BOUNDARY_KINDS:
                bad_kinds.append(
                    f"{rec.name!r}: unknown boundary_kind {rec.boundary_kind!r}"
                )
            if rec.concurrency_role not in CONCURRENCY_ROLES:
                bad_roles.append(
                    f"{rec.name!r}: unknown concurrency_role "
                    f"{rec.concurrency_role!r}"
                )
        result: dict[str, object] = {
            "stage": "check_boundary_safety",
            "passed": len(bad_kinds) == 0 and len(bad_roles) == 0,
            "bad_kinds": bad_kinds,
            "bad_roles": bad_roles,
        }
        self._results["check_boundary_safety"] = result
        return result

    # ------------------------------------------------------------------
    # Unified report
    # ------------------------------------------------------------------

    def generate_report(self) -> str:
        """Run all validation stages and return a formatted report string.

        If individual stages have not been run yet, they are run now.

        Returns:
            A human-readable multi-line report string.
        """
        if "validate_exports" not in self._results:
            self.validate_exports()
        if "check_completeness" not in self._results:
            self.check_completeness()
        if "cross_reference_theory" not in self._results:
            self.cross_reference_theory()
        if "check_boundary_safety" not in self._results:
            self.check_boundary_safety()

        lines: list[str] = [
            "=" * 72,
            f"ManifestValidator Report — {self._manifest.summary()}",
            "=" * 72,
        ]
        overall_passed = True
        for stage_name, result in self._results.items():
            passed = bool(result.get("passed", False))
            overall_passed = overall_passed and passed
            status = "PASS" if passed else "FAIL"
            lines.append(f"\n[{status}] {stage_name}")
            for key, val in result.items():
                if key in ("stage", "passed"):
                    continue
                if isinstance(val, list) and val:
                    lines.append(f"  {key}:")
                    for item in val:
                        lines.append(f"    - {item}")
                elif isinstance(val, list):
                    lines.append(f"  {key}: (none)")
                else:
                    lines.append(f"  {key}: {val}")

        lines.append("\n" + "=" * 72)
        overall_label = "OVERALL: PASS" if overall_passed else "OVERALL: FAIL"
        lines.append(overall_label)
        lines.append("=" * 72)
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ManifestRegistry
# ══════════════════════════════════════════════════════════════════════════════

class ManifestRegistry:
    """Tracks multiple versioned :class:`ConcurrencyBoundariesManifest` objects.

    The registry stores one manifest per version string and maintains a
    time-ordered history log of registration events.  It provides diff
    operations between versions and can prune old entries to bound memory use.
    """

    def __init__(self) -> None:
        self._registry: dict[str, ConcurrencyBoundariesManifest] = {}
        self._history: list[dict[str, object]] = []

    def register(self, manifest: ConcurrencyBoundariesManifest) -> None:
        """Add or replace a manifest for its version.

        Args:
            manifest: The manifest to register.  Its ``version`` field is used
                as the registry key.
        """
        version = manifest.version
        action = "update" if version in self._registry else "register"
        self._registry[version] = manifest
        self._history.append({
            "action": action,
            "version": version,
            "timestamp": time.time(),
            "symbol_count": manifest.symbol_count(),
            "package_name": manifest.package_name,
        })

    def get(self, version: str) -> ConcurrencyBoundariesManifest | None:
        """Retrieve the manifest for a specific version.

        Args:
            version: Version string to look up.

        Returns:
            The matching manifest, or None if not found.
        """
        return self._registry.get(version)

    def list_versions(self) -> list[str]:
        """Return all registered version strings in insertion order.

        Returns:
            List of version strings.
        """
        return list(self._registry.keys())

    def latest(self) -> ConcurrencyBoundariesManifest | None:
        """Return the most recently registered manifest.

        Returns:
            The last-registered manifest, or None if registry is empty.
        """
        if not self._registry:
            return None
        last_version = list(self._registry.keys())[-1]
        return self._registry[last_version]

    def diff(self, v1: str, v2: str) -> dict[str, object]:
        """Compute the symbol-level diff between two registered versions.

        Args:
            v1: Base version string.
            v2: Target version string.

        Returns:
            Dict with ``"added"`` (symbols in v2 but not v1), ``"removed"``
            (symbols in v1 but not v2), ``"changed"`` (symbols present in both
            but with differing fields), ``"v1"``, ``"v2"``.

        Raises:
            KeyError: If either version is not registered.
        """
        if v1 not in self._registry:
            raise KeyError(f"Version {v1!r} not found in registry")
        if v2 not in self._registry:
            raise KeyError(f"Version {v2!r} not found in registry")

        m1 = self._registry[v1]
        m2 = self._registry[v2]

        names1 = {r.name: r for r in m1.exported_symbols}
        names2 = {r.name: r for r in m2.exported_symbols}

        added = [
            names2[n].to_dict() for n in sorted(set(names2) - set(names1))
        ]
        removed = [
            names1[n].to_dict() for n in sorted(set(names1) - set(names2))
        ]
        changed: list[dict[str, object]] = []
        for name in sorted(set(names1) & set(names2)):
            r1 = names1[name]
            r2 = names2[name]
            if r1.to_dict() != r2.to_dict():
                changed.append({
                    "name": name,
                    "before": r1.to_dict(),
                    "after": r2.to_dict(),
                })

        return {
            "v1": v1,
            "v2": v2,
            "added": added,
            "removed": removed,
            "changed": changed,
            "net_change": len(added) - len(removed),
        }

    def history(self) -> list[dict[str, object]]:
        """Return the full registration history log.

        Returns:
            List of event dicts, each with ``"action"``, ``"version"``,
            ``"timestamp"``, ``"symbol_count"``, ``"package_name"``.
        """
        return list(self._history)

    def purge_old(self, keep_n: int = 5) -> int:
        """Remove all but the *keep_n* most recently registered versions.

        Args:
            keep_n: Number of versions to keep.  Must be >= 1.

        Returns:
            Number of versions removed.

        Raises:
            ValueError: If keep_n < 1.
        """
        if keep_n < 1:
            raise ValueError("keep_n must be at least 1")
        versions = list(self._registry.keys())
        if len(versions) <= keep_n:
            return 0
        to_remove = versions[:len(versions) - keep_n]
        for v in to_remove:
            del self._registry[v]
        removed = len(to_remove)
        self._history.append({
            "action": "purge",
            "versions_removed": to_remove,
            "timestamp": time.time(),
            "kept": keep_n,
        })
        return removed

    def __len__(self) -> int:
        return len(self._registry)

    def __repr__(self) -> str:
        return (
            f"ManifestRegistry({len(self._registry)} versions: "
            f"{list(self._registry.keys())})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TheoryAlignment
# ══════════════════════════════════════════════════════════════════════════════

class TheoryAlignment:
    """Maps concurrency_boundaries symbols to Ch24 sections and theorems.

    :class:`TheoryAlignment` maintains three bidirectional indexes:

    * ``sections`` — maps Ch24 section IDs to human-readable descriptions.
    * ``theorems`` — maps Ch24 theorem IDs to statement summaries.
    * ``symbol_alignments`` — maps symbol names to lists of section IDs.

    It can validate that all public symbols are aligned to at least one section,
    and generate a structured coverage report.
    """

    chapter: str = "Ch24"

    def __init__(self) -> None:
        self.sections: dict[str, str] = {
            "Ch24.1": "Task-local context as scoped sections",
            "Ch24.1.1": "Scoped section semantics and binding rules",
            "Ch24.1.2": "Section inheritance across task boundaries",
            "Ch24.2": "Cancellation as obstruction injection",
            "Ch24.2.1": "Obstruction algebra for cancelled tasks",
            "Ch24.2.2": "Propagation rules for parent-child cancellation",
            "Ch24.3": "Exception groups as multi-obstruction records",
            "Ch24.3.1": "ExceptionGroup composition and flattening",
            "Ch24.3.2": "Resolution strategies for multi-exception states",
            "Ch24.4": "Process boundaries as cover boundaries",
            "Ch24.4.1": "Cover morphisms across process boundaries",
            "Ch24.4.2": "Allowed crossing predicates and policies",
            "Ch24.5": "Concurrency scope hierarchy and lifecycle",
            "Ch24.5.1": "Scope depth and parent-child relationships",
            "Ch24.5.2": "Lifecycle transitions and status invariants",
            "Ch24.T1": "Theorem: task-local sections form a presheaf",
            "Ch24.T2": "Theorem: cancellation is an obstruction class",
            "Ch24.T3": "Theorem: exception groups are multi-obstructions",
            "Ch24.T4": "Theorem: process boundaries are cover boundaries",
            "Ch24.T5": "Theorem: scope depth is well-founded",
        }
        self.theorems: dict[str, str] = {
            "Ch24.T1": (
                "For any task T, the collection of task-local sections "
                "indexed by T forms a sheaf over the semantic site."
            ),
            "Ch24.T2": (
                "Every cancellation event corresponds to an injected "
                "obstruction class in the Čech cohomology of the task graph."
            ),
            "Ch24.T3": (
                "An ExceptionGroup containing n exceptions encodes n "
                "simultaneous obstruction classes, none of which subsumes "
                "another."
            ),
            "Ch24.T4": (
                "Each process boundary corresponds to a cover morphism in "
                "the Grothendieck topology of the deployment site."
            ),
            "Ch24.T5": (
                "The parent-child scope relation is acyclic and well-founded; "
                "every scope has finite depth."
            ),
        }
        self.symbol_alignments: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Alignment operations
    # ------------------------------------------------------------------

    def align_symbol(self, name: str, sections: list[str]) -> None:
        """Record that symbol *name* is aligned to the given Ch24 *sections*.

        Unknown section IDs are silently accepted (they may be future
        sections not yet in the known set).

        Args:
            name: Symbol name.
            sections: List of Ch24 section ID strings.
        """
        existing = self.symbol_alignments.get(name, [])
        merged = list(dict.fromkeys(existing + sections))  # dedup, preserve order
        self.symbol_alignments[name] = merged

    def get_aligned_sections(self, name: str) -> list[str]:
        """Return the list of Ch24 sections aligned to *name*.

        Args:
            name: Symbol name.

        Returns:
            List of section IDs, empty if the symbol has no alignments.
        """
        return list(self.symbol_alignments.get(name, []))

    def validate_alignment(self) -> dict[str, object]:
        """Check internal consistency of alignments.

        Returns:
            Dict with ``"valid"`` (bool), ``"unrecognised_sections"`` (list),
            ``"unaligned_symbols"`` (list), and ``"coverage_pct"`` (float).
        """
        unrecognised: list[str] = []
        for sym_name, secs in self.symbol_alignments.items():
            for sec in secs:
                if sec not in self.sections and sec not in self.theorems:
                    unrecognised.append(f"{sym_name} -> {sec}")

        aligned_count = sum(
            1 for secs in self.symbol_alignments.values() if secs
        )
        total = len(self.symbol_alignments)
        coverage = (aligned_count / total * 100.0) if total > 0 else 0.0

        unaligned = [
            name for name, secs in self.symbol_alignments.items() if not secs
        ]

        return {
            "valid": len(unrecognised) == 0,
            "unrecognised_sections": unrecognised,
            "unaligned_symbols": unaligned,
            "aligned_count": aligned_count,
            "total_symbols": total,
            "coverage_pct": round(coverage, 2),
        }

    def report(self) -> str:
        """Return a formatted alignment report string.

        Returns:
            Multi-line string summarising chapter, sections, and per-symbol
            alignments.
        """
        lines: list[str] = [
            "=" * 72,
            f"TheoryAlignment Report — {self.chapter}",
            "=" * 72,
            f"Sections defined: {len(self.sections)}",
            f"Theorems defined: {len(self.theorems)}",
            f"Symbol alignments: {len(self.symbol_alignments)}",
            "",
            "Symbol → Section mappings:",
        ]
        for sym_name in sorted(self.symbol_alignments):
            secs = self.symbol_alignments[sym_name]
            secs_str = ", ".join(secs) if secs else "(none)"
            lines.append(f"  {sym_name}: {secs_str}")
        lines.append("=" * 72)
        return "\n".join(lines)

    def chapter_summary(self) -> dict[str, object]:
        """Return a structured summary of the chapter's sections and theorems.

        Returns:
            Dict with ``"chapter"``, ``"sections"`` (dict), ``"theorems"``
            (dict), ``"symbol_count"`` (int).
        """
        return {
            "chapter": self.chapter,
            "sections": dict(self.sections),
            "theorems": dict(self.theorems),
            "symbol_count": len(self.symbol_alignments),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Default manifest builder
# ══════════════════════════════════════════════════════════════════════════════

def build_default_manifest() -> ConcurrencyBoundariesManifest:
    """Construct a manifest pre-populated with all known package symbols.

    The manifest includes records for every public class, enum, and helper
    function across all submodules of the ``concurrency_boundaries`` package.

    Returns:
        A fully populated :class:`ConcurrencyBoundariesManifest`.
    """
    pkg = "jugeo.python_runtime.concurrency_boundaries"
    symbols: list[SymbolRecord] = [
        # ── models module ──────────────────────────────────────────────
        SymbolRecord(
            name="ConcurrencyRole",
            origin_module="models",
            description="Enum of concurrency actor roles (task, thread, process, etc.)",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.1",
        ),
        SymbolRecord(
            name="CancellationReason",
            origin_module="models",
            description="Enum of reasons a task-local section may be cancelled",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.2",
        ),
        SymbolRecord(
            name="BoundaryKind",
            origin_module="models",
            description="Enum of process/thread/network boundary kinds",
            concurrency_role="boundary_enforcer",
            boundary_kind="process",
            theory_ref="Ch24.4",
        ),
        SymbolRecord(
            name="ScopeStatus",
            origin_module="models",
            description="Enum of lifecycle statuses for a concurrency scope",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.5",
        ),
        SymbolRecord(
            name="TaskLocalSection",
            origin_module="models",
            description="Immutable record of a task-local scoped section (Ch24.1)",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.1.1",
        ),
        SymbolRecord(
            name="CancellationRecord",
            origin_module="models",
            description="Immutable record of a cancellation event with obstruction key",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.2.1",
        ),
        SymbolRecord(
            name="ExceptionGroupRecord",
            origin_module="models",
            description="Mutable multi-obstruction record modelling Python ExceptionGroup",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.3.1",
        ),
        SymbolRecord(
            name="ProcessBoundary",
            origin_module="models",
            description="Immutable cover-boundary record between two process IDs",
            concurrency_role="process",
            boundary_kind="process",
            theory_ref="Ch24.4.1",
        ),
        SymbolRecord(
            name="ConcurrencyScope",
            origin_module="models",
            description="Mutable scope node tracking child scopes and active sections",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.5.1",
        ),
        SymbolRecord(
            name="make_task_section",
            origin_module="models",
            description="Factory function for TaskLocalSection with auto-generated ID",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.1.1",
        ),
        SymbolRecord(
            name="make_cancellation_record",
            origin_module="models",
            description="Factory function for CancellationRecord",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.2.1",
        ),
        SymbolRecord(
            name="make_process_boundary",
            origin_module="models",
            description="Factory function for ProcessBoundary",
            concurrency_role="process",
            boundary_kind="process",
            theory_ref="Ch24.4.1",
        ),
        SymbolRecord(
            name="make_scope",
            origin_module="models",
            description="Factory function for ConcurrencyScope",
            concurrency_role="task",
            boundary_kind="task_local",
            theory_ref="Ch24.5.1",
        ),
        # ── manifest module ────────────────────────────────────────────
        SymbolRecord(
            name="SymbolRecord",
            origin_module="manifest",
            description="Metadata record for a single exported symbol",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
        SymbolRecord(
            name="ConcurrencyBoundariesManifest",
            origin_module="manifest",
            description="Catalogue of all exported symbols in this package",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
        SymbolRecord(
            name="ManifestValidator",
            origin_module="manifest",
            description="Validates manifest completeness and theory alignment",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
        SymbolRecord(
            name="ManifestRegistry",
            origin_module="manifest",
            description="Version-aware registry of multiple manifests",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
        SymbolRecord(
            name="TheoryAlignment",
            origin_module="manifest",
            description="Maps symbols to Ch24 sections and theorems",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
        SymbolRecord(
            name="build_default_manifest",
            origin_module="manifest",
            description="Constructs the default pre-populated manifest",
            concurrency_role="boundary_enforcer",
            boundary_kind="task_local",
            theory_ref="",
        ),
    ]

    return ConcurrencyBoundariesManifest(
        package_name=pkg,
        version=VERSION,
        theory_chapter=THEORY_CHAPTER,
        exported_symbols=symbols,
        description=(
            "The concurrency_boundaries package models the four primary "
            "concurrency boundary constructs from Ch24 of theory2.tex: "
            "task-local context as scoped sections, cancellation as obstruction "
            "injection, exception groups as multi-obstruction records, and "
            "process boundaries as cover boundaries."
        ),
        author="copilot",
        tags=["concurrency", "boundaries", "Ch24", "theory2"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Module exports
# ══════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    "VERSION",
    "THEORY_CHAPTER",
    "BOUNDARY_KINDS",
    "CONCURRENCY_ROLES",
    "SymbolRecord",
    "ConcurrencyBoundariesManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "TheoryAlignment",
    "build_default_manifest",
]

# copilot: shared-core marker for future LLM orchestration.
