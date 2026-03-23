r"""Package manifest for the local_construction sub-package.

Theory (theory2.tex §39 — Local construction loops):
    Chapter 39 of theory2.tex introduces *local construction loops* — the
    innermost layer of the three-tier JuGeo generation architecture.  Each
    loop is scoped to a single coordinate u ∈ Coord and attempts to
    construct a local section s_u : Spec_u → Type_u that satisfies the
    obligations decreed by the global treaty τ and the interface discipline
    ∂u imposed by neighbouring coordinates.

    §39.1 defines the *manifest* of a local-construction package as the
    formal record of which objects, functions, and theory identifiers the
    package exports.  A well-formed manifest must satisfy:
        (i)   every exported symbol resolves to a defined Python object,
        (ii)  every theory reference cites an extant section of theory2.tex,
        (iii) the version string obeys the semantic-versioning convention
              MAJOR.MINOR.PATCH.

    §39.2 extends this to *file manifests* — per-module records that bind a
    module path to the theory sections it implements.  The union of all file
    manifests must partition the set of §39 theorems without gaps.

    §39.3 introduces the *manifest registry* — the data structure that
    aggregates file manifests and exposes queries such as "which module
    implements Theorem 39.4.2?"

    copilot: manifest-marker

Usage::

    from jugeo.generation.local_construction.manifest import (
        PACKAGE_MANIFEST,
        FILE_MANIFESTS,
        get_manifest,
        get_file_manifest,
        validate_package_structure,
    )
    pm = get_manifest()
    print(pm.summary())
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PackageManifest",
    "FileManifest",
    "ManifestRegistry",
    "ManifestDiagnostics",
    "ManifestError",
    "PACKAGE_MANIFEST",
    "FILE_MANIFESTS",
    "get_manifest",
    "get_file_manifest",
    "validate_package_structure",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ManifestError(Exception):
    """Base exception for manifest validation failures.

    Raised when a :class:`PackageManifest` or :class:`FileManifest` fails its
    internal consistency checks (§39.1 well-formedness conditions).
    """


class MissingSymbolError(ManifestError):
    """Raised when an exported symbol is not resolvable."""


class InvalidVersionError(ManifestError):
    """Raised when the version string does not match MAJOR.MINOR.PATCH."""


class MissingFileError(ManifestError):
    """Raised when validate_package_structure detects a missing file."""


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Package-level manifest for the local_construction sub-package.

    Implements the formal manifest record defined in theory2.tex §39.1.
    Every field directly corresponds to a field in Definition 39.1.1.

    Attributes:
        package_name: Fully-qualified Python package name.
        version: Semantic version string (MAJOR.MINOR.PATCH).
        description: Human-readable one-line description of the package.
        theory_chapter: Chapter of theory2.tex this package implements.
        theory_section: Primary section within that chapter.
        exported_classes: Tuple of class names exported from __init__.py.
        exported_functions: Tuple of function names exported from __init__.py.
        dependencies: Tuple of required sibling/external package names.
        author: Author identifier string.
        created_at: Unix timestamp of initial creation.
        is_stable: Whether the public API is considered stable.
    """

    package_name: str
    version: str
    description: str
    theory_chapter: str
    theory_section: str
    exported_classes: tuple[str, ...]
    exported_functions: tuple[str, ...]
    dependencies: tuple[str, ...]
    author: str
    created_at: float
    is_stable: bool

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        The resulting dict satisfies the schema expected by :meth:`from_dict`.

        Returns:
            dict mapping field names to their values (tuples converted to
            lists for JSON compatibility).
        """
        d = asdict(self)
        # asdict already converts tuples to lists; keep that convention.
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialise the manifest to a JSON string.

        Args:
            indent: Number of spaces used for indentation (default 2).

        Returns:
            Pretty-printed JSON string.
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a plain dictionary.

        Args:
            d: Dictionary as produced by :meth:`to_dict` or parsed from JSON.

        Returns:
            A fully initialised :class:`PackageManifest`.

        Raises:
            ManifestError: If required keys are absent.
        """
        required = {
            "package_name", "version", "description", "theory_chapter",
            "theory_section", "exported_classes", "exported_functions",
            "dependencies", "author", "created_at", "is_stable",
        }
        missing = required - d.keys()
        if missing:
            raise ManifestError(f"from_dict: missing keys {sorted(missing)}")
        return cls(
            package_name=str(d["package_name"]),
            version=str(d["version"]),
            description=str(d["description"]),
            theory_chapter=str(d["theory_chapter"]),
            theory_section=str(d["theory_section"]),
            exported_classes=tuple(d["exported_classes"]),
            exported_functions=tuple(d["exported_functions"]),
            dependencies=tuple(d["dependencies"]),
            author=str(d["author"]),
            created_at=float(d["created_at"]),
            is_stable=bool(d["is_stable"]),
        )

    @classmethod
    def from_json(cls, s: str) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a JSON string.

        Args:
            s: JSON string as produced by :meth:`to_json`.

        Returns:
            A fully initialised :class:`PackageManifest`.
        """
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check well-formedness conditions from theory2.tex §39.1.

        Conditions checked:
            * Version string matches MAJOR.MINOR.PATCH.
            * exported_classes and exported_functions are non-empty tuples.
            * theory_chapter and theory_section are non-empty strings.
            * created_at is a positive float.

        Returns:
            List of human-readable error strings; empty list means valid.
        """
        errors: list[str] = []

        # Version format
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"version '{self.version}' does not match MAJOR.MINOR.PATCH"
            )

        if not self.exported_classes:
            errors.append("exported_classes must be non-empty")

        if not self.exported_functions:
            errors.append("exported_functions must be non-empty")

        if not self.theory_chapter.strip():
            errors.append("theory_chapter must be a non-empty string")

        if not self.theory_section.strip():
            errors.append("theory_section must be a non-empty string")

        if self.created_at <= 0.0:
            errors.append("created_at must be a positive Unix timestamp")

        if not self.package_name.strip():
            errors.append("package_name must be non-empty")

        if errors:
            logger.warning(
                "PackageManifest.validate: found %d error(s) for '%s'",
                len(errors),
                self.package_name,
            )
        else:
            logger.debug(
                "PackageManifest.validate: '%s' passed all checks",
                self.package_name,
            )
        return errors

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_public_api(self) -> dict[str, tuple[str, ...]]:
        """Return the public API grouped by kind.

        Returns:
            Dict with keys ``"classes"`` and ``"functions"``, each mapping to
            a tuple of symbol names.
        """
        return {
            "classes": self.exported_classes,
            "functions": self.exported_functions,
        }

    def check_dependency(self, dep_name: str) -> bool:
        """Check whether *dep_name* is listed as a dependency.

        Args:
            dep_name: The dependency name to look up.

        Returns:
            True if *dep_name* appears in :attr:`dependencies`.
        """
        return dep_name in self.dependencies

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary.

        Returns:
            Multi-line string suitable for printing to a terminal.
        """
        lines = [
            f"Package : {self.package_name}",
            f"Version : {self.version}  (stable={self.is_stable})",
            f"Theory  : {self.theory_chapter} / {self.theory_section}",
            f"Desc    : {self.description}",
            f"Classes : {', '.join(self.exported_classes) or '—'}",
            f"Funcs   : {', '.join(self.exported_functions) or '—'}",
            f"Deps    : {', '.join(self.dependencies) or '—'}",
            f"Author  : {self.author}",
        ]
        return "\n".join(lines)

    def all_exports(self) -> tuple[str, ...]:
        """Return the combined tuple of all exported symbols (classes + functions).

        Returns:
            Tuple of symbol name strings in the order classes then functions.
        """
        return self.exported_classes + self.exported_functions


# ---------------------------------------------------------------------------
# FileManifest
# ---------------------------------------------------------------------------


@dataclass
class FileManifest:
    """Per-file manifest record.

    Implements the file manifest concept introduced in theory2.tex §39.2.
    Each :class:`FileManifest` binds a source file to the theory sections it
    realises, the Python objects it defines, and basic metrics.

    Attributes:
        file_name: Bare file name (e.g. ``"models.py"``).
        module_path: Fully-qualified Python module path.
        purpose: One-sentence description of the file's role.
        classes: Tuple of class names defined in this file.
        functions: Tuple of top-level function names defined in this file.
        theory_refs: Tuple of theory2.tex section identifiers (e.g. ``"§39.2"``).
        lines_of_code: Approximate number of non-blank, non-comment lines.
        last_modified: Unix timestamp of the last known modification.
    """

    file_name: str
    module_path: str
    purpose: str
    classes: tuple[str, ...]
    functions: tuple[str, ...]
    theory_refs: tuple[str, ...]
    lines_of_code: int
    last_modified: float

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            dict with all fields; tuples serialised as lists.
        """
        return asdict(self)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a compact summary line for the file.

        Returns:
            Single-line string: ``file_name — purpose (N lines, K theory refs)``.
        """
        return (
            f"{self.file_name} — {self.purpose} "
            f"({self.lines_of_code} lines, {len(self.theory_refs)} theory refs)"
        )

    def get_theory_refs(self) -> tuple[str, ...]:
        """Return the tuple of theory section identifiers.

        Returns:
            Tuple of strings such as ``("§39.2", "§39.3")``.
        """
        return self.theory_refs

    def is_theory_aligned(self) -> bool:
        """Return True when this file cites at least one theory reference.

        A file is considered *theory-aligned* (§39.2 Definition 39.2.3) iff
        it cites at least one section of theory2.tex.

        Returns:
            bool.
        """
        return len(self.theory_refs) > 0


# ---------------------------------------------------------------------------
# ManifestRegistry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """Registry of :class:`FileManifest` objects for the package.

    Implements the manifest registry of theory2.tex §39.3.  The registry
    supports O(1) lookup by file name and O(k) lookup by theory reference
    where k is the number of files citing the given section.

    Attributes:
        _store: Internal dict mapping ``file_name -> FileManifest``.
    """

    def __init__(self) -> None:
        self._store: dict[str, FileManifest] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, fm: FileManifest) -> None:
        """Add *fm* to the registry.

        If a manifest for the same file already exists it is silently
        replaced.

        Args:
            fm: The :class:`FileManifest` to register.
        """
        if fm.file_name in self._store:
            logger.debug(
                "ManifestRegistry.register: replacing existing entry for '%s'",
                fm.file_name,
            )
        self._store[fm.file_name] = fm
        logger.debug(
            "ManifestRegistry.register: registered '%s'", fm.file_name
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, file_name: str) -> FileManifest | None:
        """Look up a :class:`FileManifest` by file name.

        Args:
            file_name: Bare file name, e.g. ``"models.py"``.

        Returns:
            The matching :class:`FileManifest` or ``None`` if absent.
        """
        return self._store.get(file_name)

    def list_all(self) -> list[FileManifest]:
        """Return all registered file manifests in registration order.

        Returns:
            List of :class:`FileManifest` objects.
        """
        return list(self._store.values())

    def get_by_theory_ref(self, ref: str) -> list[FileManifest]:
        """Return all manifests that cite *ref*.

        Args:
            ref: Theory section identifier, e.g. ``"§39.4"``.

        Returns:
            List (possibly empty) of matching :class:`FileManifest` objects.
        """
        return [
            fm for fm in self._store.values() if ref in fm.theory_refs
        ]

    def validate_all(self) -> dict[str, list[str]]:
        """Validate every registered manifest for basic consistency.

        Checks performed per file:
            * ``file_name`` is non-empty.
            * ``module_path`` is non-empty.
            * ``lines_of_code`` is non-negative.
            * At least one theory ref is present.

        Returns:
            Dict mapping ``file_name -> list[error_string]``; files without
            errors are omitted.
        """
        results: dict[str, list[str]] = {}
        for fm in self._store.values():
            errors: list[str] = []
            if not fm.file_name.strip():
                errors.append("file_name is empty")
            if not fm.module_path.strip():
                errors.append("module_path is empty")
            if fm.lines_of_code < 0:
                errors.append("lines_of_code is negative")
            if not fm.theory_refs:
                errors.append("no theory references recorded")
            if errors:
                results[fm.file_name] = errors
        return results

    def summary_report(self) -> str:
        """Return a multi-line human-readable summary of all registered files.

        Returns:
            String with one line per file plus a totals line.
        """
        lines: list[str] = [
            f"ManifestRegistry — {len(self._store)} file(s) registered",
            "-" * 60,
        ]
        total_loc = 0
        for fm in sorted(self._store.values(), key=lambda f: f.file_name):
            lines.append(f"  {fm.summary()}")
            total_loc += fm.lines_of_code
        lines.append("-" * 60)
        lines.append(f"  Total lines of code: {total_loc}")
        return "\n".join(lines)

    def export_as_json(self, indent: int = 2) -> str:
        """Serialise the entire registry to a JSON string.

        Args:
            indent: Indentation for the JSON output.

        Returns:
            JSON string encoding a list of file manifest dicts.
        """
        payload = [fm.to_dict() for fm in self._store.values()]
        return json.dumps(payload, indent=indent)


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


class ManifestDiagnostics:
    """Diagnostic checks for the local_construction package manifest.

    Aggregates several well-formedness conditions described in theory2.tex
    §39.1–§39.3 and exposes them as a single diagnostic runner.

    Attributes:
        package_manifest: The :class:`PackageManifest` under examination.
        registry: The :class:`ManifestRegistry` under examination.
    """

    def __init__(
        self,
        package_manifest: PackageManifest,
        registry: ManifestRegistry,
    ) -> None:
        self.package_manifest = package_manifest
        self.registry = registry

    def check_completeness(self) -> dict[str, Any]:
        """Check that all expected files are represented in the registry.

        Expected files are the 9 files constituting the local_construction
        package (§39 Table 39.1).

        Returns:
            Dict with keys ``"present"``, ``"missing"``, ``"extra"``.
        """
        expected = {
            "manifest.py",
            "models.py",
            "local_construction_loop.py",
            "interface_discipline.py",
            "coordinated_elaboration.py",
            "copilot_in_construction.py",
            "algorithms.py",
            "integration.py",
            "theorems.py",
        }
        registered = {fm.file_name for fm in self.registry.list_all()}
        present = expected & registered
        missing = expected - registered
        extra = registered - expected
        logger.debug(
            "check_completeness: present=%d, missing=%d, extra=%d",
            len(present), len(missing), len(extra),
        )
        return {"present": sorted(present), "missing": sorted(missing), "extra": sorted(extra)}

    def check_theory_alignment(self) -> dict[str, bool]:
        """Check that every registered file is theory-aligned (§39.2 Def 39.2.3).

        Returns:
            Dict mapping ``file_name -> is_theory_aligned``.
        """
        return {
            fm.file_name: fm.is_theory_aligned()
            for fm in self.registry.list_all()
        }

    def report(self) -> str:
        """Produce a combined diagnostic report.

        Runs :meth:`check_completeness`, :meth:`check_theory_alignment`, and
        :meth:`ManifestRegistry.validate_all`, then formats the results.

        Returns:
            Multi-section human-readable report string.
        """
        lines: list[str] = ["=" * 60, "ManifestDiagnostics Report", "=" * 60]

        # Package-level validation
        pkg_errors = self.package_manifest.validate()
        lines.append("\n[1] Package manifest validation")
        if pkg_errors:
            for e in pkg_errors:
                lines.append(f"    ERROR: {e}")
        else:
            lines.append("    OK — no errors")

        # Completeness
        completeness = self.check_completeness()
        lines.append("\n[2] Package completeness (§39 Table 39.1)")
        lines.append(f"    Present : {', '.join(completeness['present']) or '—'}")
        lines.append(f"    Missing : {', '.join(completeness['missing']) or '—'}")
        lines.append(f"    Extra   : {', '.join(completeness['extra']) or '—'}")

        # Theory alignment
        alignment = self.check_theory_alignment()
        lines.append("\n[3] Theory alignment (§39.2 Def 39.2.3)")
        for fname, aligned in sorted(alignment.items()):
            status = "aligned" if aligned else "UNALIGNED"
            lines.append(f"    {fname:<50} {status}")

        # File-level validation
        file_errors = self.registry.validate_all()
        lines.append("\n[4] File-level validation")
        if file_errors:
            for fname, errs in sorted(file_errors.items()):
                for e in errs:
                    lines.append(f"    {fname}: {e}")
        else:
            lines.append("    OK — all files valid")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# validate_package_structure
# ---------------------------------------------------------------------------


def validate_package_structure(directory: str | Path) -> dict[str, Any]:
    """Walk *directory* and verify every expected local_construction file exists.

    Implements the structural check described in theory2.tex §39.1 Remark
    39.1.4: a package is *structurally complete* iff all files listed in
    Table 39.1 exist on disk and are non-empty.

    Args:
        directory: Path to the local_construction package directory.

    Returns:
        Dict with keys:

        ``"status"``
            ``"ok"`` or ``"incomplete"``
        ``"directory"``
            The resolved absolute path as a string.
        ``"files"``
            Dict mapping each expected file name to a sub-dict with keys
            ``"exists"`` (bool), ``"size_bytes"`` (int or None), and
            ``"non_empty"`` (bool).
        ``"missing_files"``
            List of file names that are absent.
        ``"empty_files"``
            List of file names that exist but are empty (0 bytes).

    Raises:
        ManifestError: If *directory* does not exist on the filesystem.
    """
    base = Path(directory).resolve()
    if not base.exists():
        raise MissingFileError(f"directory does not exist: {base}")
    if not base.is_dir():
        raise ManifestError(f"path is not a directory: {base}")

    expected_files = [
        "manifest.py",
        "models.py",
        "local_construction_loop.py",
        "interface_discipline.py",
        "coordinated_elaboration.py",
        "copilot_in_construction.py",
        "algorithms.py",
        "integration.py",
        "theorems.py",
    ]

    file_results: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    empty: list[str] = []

    for fname in expected_files:
        fpath = base / fname
        exists = fpath.exists()
        size: int | None = None
        non_empty = False
        if exists:
            size = fpath.stat().st_size
            non_empty = size > 0
            if not non_empty:
                empty.append(fname)
        else:
            missing.append(fname)
        file_results[fname] = {
            "exists": exists,
            "size_bytes": size,
            "non_empty": non_empty,
        }

    status = "ok" if not missing and not empty else "incomplete"
    logger.info(
        "validate_package_structure: %s — missing=%d, empty=%d",
        status, len(missing), len(empty),
    )
    return {
        "status": status,
        "directory": str(base),
        "files": file_results,
        "missing_files": missing,
        "empty_files": empty,
    }


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PACKAGE_MANIFEST: PackageManifest = PackageManifest(
    package_name="jugeo.generation.local_construction",
    version="0.1.0",
    description=(
        "Inner construction loops for local goal resolution in the JuGeo "
        "generation pipeline (theory2.tex Chapter 39)."
    ),
    theory_chapter="Chapter 39",
    theory_section="§39.1",
    exported_classes=(
        "LocalConstructionLoop",
        "InterfaceDiscipline",
        "CoordinatedElaboration",
        "CandidateSet",
        "LoopStatus",
        "StrictnessLevel",
        "GenerationMethod",
        "LocalConstructionError",
        "InterfaceBreachError",
        "BudgetExhaustedError",
        "ConvergenceFailureError",
    ),
    exported_functions=(
        "run_local_loop",
        "build_interface_discipline",
        "coordinate_elaboration",
        "select_candidate",
        "verify_candidate",
        "propagate_obligations",
        "get_manifest",
        "get_file_manifest",
        "validate_package_structure",
    ),
    dependencies=(
        "jugeo.generation.goals",
        "jugeo.generation.construction",
    ),
    author="JuGeo project",
    created_at=1_700_000_000.0,
    is_stable=False,
)
"""Module-level :class:`PackageManifest` for the local_construction package."""


def _build_registry() -> ManifestRegistry:
    """Construct and return the canonical :class:`ManifestRegistry`.

    Internal factory function used to initialise :data:`FILE_MANIFESTS`.
    Not part of the public API.
    """
    reg = ManifestRegistry()
    _ts = 1_700_000_000.0  # shared placeholder last_modified timestamp

    reg.register(FileManifest(
        file_name="manifest.py",
        module_path="jugeo.generation.local_construction.manifest",
        purpose="Package manifest, registry, and structural validation",
        classes=("PackageManifest", "FileManifest", "ManifestRegistry", "ManifestDiagnostics"),
        functions=("get_manifest", "get_file_manifest", "validate_package_structure"),
        theory_refs=("§39.1", "§39.2", "§39.3"),
        lines_of_code=460,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="models.py",
        module_path="jugeo.generation.local_construction.models",
        purpose="Core dataclass models for local construction loops",
        classes=(
            "LocalConstructionLoop",
            "InterfaceDiscipline",
            "CoordinatedElaboration",
            "CandidateSet",
            "LoopStatus",
            "StrictnessLevel",
            "GenerationMethod",
            "LocalConstructionError",
            "InterfaceBreachError",
            "BudgetExhaustedError",
            "ConvergenceFailureError",
        ),
        functions=(),
        theory_refs=("§39.4", "§39.5", "§39.6", "§39.7"),
        lines_of_code=500,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="local_construction_loop.py",
        module_path="jugeo.generation.local_construction.local_construction_loop",
        purpose="Section 39.1 — definition and driver of the local construction loop",
        classes=("LocalConstructionDriver",),
        functions=("run_local_loop",),
        theory_refs=("§39.1", "§39.1.1", "§39.1.2", "§39.1.3"),
        lines_of_code=300,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="interface_discipline.py",
        module_path="jugeo.generation.local_construction.interface_discipline",
        purpose="Section 39.2 — interface discipline at boundary ∂u",
        classes=("InterfaceChecker", "BoundaryResolver"),
        functions=("build_interface_discipline", "check_boundary_compliance"),
        theory_refs=("§39.2", "§39.2.1", "§39.2.2", "§39.2.3"),
        lines_of_code=280,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="coordinated_elaboration.py",
        module_path="jugeo.generation.local_construction.coordinated_elaboration",
        purpose="Section 39.3 — coordinated parallel elaboration of multiple sections",
        classes=("ElaborationCoordinator",),
        functions=("coordinate_elaboration", "synchronize_loop_states"),
        theory_refs=("§39.3", "§39.3.1", "§39.3.2"),
        lines_of_code=260,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="copilot_in_construction.py",
        module_path="jugeo.generation.local_construction.copilot_in_construction",
        purpose="Section 39.4 — copilot-assisted candidate generation and verification",
        classes=("CopilotAdapter", "CopilotCandidateSource"),
        functions=("query_copilot", "score_copilot_candidates"),
        theory_refs=("§39.4", "§39.4.1", "§39.4.2"),
        lines_of_code=240,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="algorithms.py",
        module_path="jugeo.generation.local_construction.algorithms",
        purpose="Core algorithms: selection, Pareto dominance, budget allocation",
        classes=("ParetoSelector", "BudgetAllocator"),
        functions=(
            "select_candidate",
            "verify_candidate",
            "propagate_obligations",
            "pareto_front",
            "compute_trust_score",
        ),
        theory_refs=("§39.5", "§39.5.1", "§39.5.2", "§39.6"),
        lines_of_code=320,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="integration.py",
        module_path="jugeo.generation.local_construction.integration",
        purpose="Integration adapters connecting local loops to the outer ConstructionLoop",
        classes=("LocalToGlobalAdapter", "ObligationPropagator"),
        functions=("lift_local_result", "lower_global_goal", "merge_results"),
        theory_refs=("§39.7", "§39.7.1", "§39.7.2"),
        lines_of_code=200,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="theorems.py",
        module_path="jugeo.generation.local_construction.theorems",
        purpose="Theorem statements and proof sketches for Chapter 39",
        classes=("TheoremRecord", "ProofSketch"),
        functions=("list_theorems", "get_theorem", "verify_theorem_preconditions"),
        theory_refs=(
            "§39.1", "§39.2", "§39.3", "§39.4",
            "§39.5", "§39.6", "§39.7",
        ),
        lines_of_code=180,
        last_modified=_ts,
    ))

    return reg


FILE_MANIFESTS: ManifestRegistry = _build_registry()
"""Module-level :class:`ManifestRegistry` pre-populated with all 9 file manifests."""


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Return the canonical :data:`PACKAGE_MANIFEST` for this package.

    This is the primary entry-point described in theory2.tex §39.1 Remark
    39.1.2: every package exposes a ``get_manifest()`` function so that the
    outer generation loop can introspect it at runtime.

    Returns:
        The module-level :class:`PackageManifest` singleton.

    Example::

        from jugeo.generation.local_construction.manifest import get_manifest
        pm = get_manifest()
        print(pm.summary())
    """
    return PACKAGE_MANIFEST


def get_file_manifest(file_name: str) -> FileManifest:
    """Return the :class:`FileManifest` for a named file in this package.

    Args:
        file_name: Bare file name (e.g. ``"models.py"``).

    Returns:
        The corresponding :class:`FileManifest`.

    Raises:
        ManifestError: If *file_name* is not registered in :data:`FILE_MANIFESTS`.

    Example::

        fm = get_file_manifest("models.py")
        print(fm.summary())
    """
    fm = FILE_MANIFESTS.get(file_name)
    if fm is None:
        registered = sorted(
            f.file_name for f in FILE_MANIFESTS.list_all()
        )
        raise ManifestError(
            f"No manifest registered for '{file_name}'. "
            f"Known files: {registered}"
        )
    return fm


# ---------------------------------------------------------------------------
# Module self-check (runs when invoked directly)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    diag = ManifestDiagnostics(PACKAGE_MANIFEST, FILE_MANIFESTS)
    print(diag.report())
    print()
    print(PACKAGE_MANIFEST.summary())
