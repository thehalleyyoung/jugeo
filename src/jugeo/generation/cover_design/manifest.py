r"""Package manifest for the cover_design sub-package.

Theory (theory2.tex §cover_design — Cover design):
    The *cover design* chapter of theory2.tex introduces the machinery for
    partitioning a judgment site :math:`S` into a finite family of open
    patches :math:`\mathcal{U} = \{U_i\}_{i \in I}` that collectively cover
    :math:`S`.  The chapter is organised as follows:

    §cover_design.1 — *Cover completeness*: defines the covering condition
        :math:`S \subseteq \bigcup_i U_i` and the associated completeness
        metric :math:`\kappa`.

    §cover_design.2 — *Čech condition*: requires that sections assigned to
        overlapping patches agree on their intersection.  This is the
        sheaf-theoretic gluing condition that makes local sections cohere into
        a global one.

    §cover_design.3 — *Budget allocation*: treats the budget :math:`B` as a
        first-class object.  Introduces the overhead fraction :math:`\beta`,
        the net budget :math:`B_\text{net} = B(1-\beta)`, and the
        priority-weighted allocation formula.

    §cover_design.4 — *Patch selection*: greedy set-cover algorithm, cost
        model for individual patches, and approximation guarantees.

    §cover_design.5 — *Dependency ordering*: Kahn topological sort, critical
        path via longest-path DP, and transitive reduction of the DAG.

    §cover_design.6 — *Parallelism*: antichain decomposition (Dilworth's
        theorem), Coffman–Graham scheduling, and the parallelism-safety
        invariant.

    §cover_design.7 — *Quality metrics*: coverage completeness, monotonicity
        under patch addition, and integration with the outer generation loop.

    §cover_design.8 — *Integration and trust*: generated cover sections enter
        at the **PROPOSAL** trust tier and must pass theorem verification
        (§cover_design theorems T_CD_1–T_CD_8) before promotion.

    §cover_design.1 (well-formedness of the manifest):
        A package manifest is *well-formed* iff
        (i)   every exported symbol resolves to a defined Python object,
        (ii)  every theory reference cites an extant section of theory2.tex,
        (iii) the version string obeys MAJOR.MINOR.PATCH.

    §cover_design.2 (file manifest):
        Per-module records binding each source file to the theory sections it
        realises.  The union of all file manifests must cover all
        §cover_design theorems without gaps.

    §cover_design.3 (manifest registry):
        Aggregates file manifests and exposes queries such as "which module
        implements theorem T_CD_4?"

    copilot: manifest-marker

Usage::

    from jugeo.generation.cover_design.manifest import (
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
    internal consistency checks (§cover_design well-formedness conditions).
    """


class MissingSymbolError(ManifestError):
    """Raised when an exported symbol is not resolvable."""


class InvalidVersionError(ManifestError):
    """Raised when the version string does not match MAJOR.MINOR.PATCH."""


class MissingFileError(ManifestError):
    """Raised when :func:`validate_package_structure` detects a missing file."""


# ---------------------------------------------------------------------------
# PackageManifest
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Package-level manifest for the cover_design sub-package.

    Implements the formal manifest record defined in theory2.tex
    §cover_design.1.  Every field directly corresponds to a field in the
    cover-design manifest definition.

    Attributes
    ----------
    package_name:
        Fully-qualified Python package name.
    version:
        Semantic version string (MAJOR.MINOR.PATCH).
    description:
        Human-readable one-line description of the package.
    theory_chapter:
        Chapter of theory2.tex this package implements.
    theory_section:
        Primary section within that chapter.
    exported_classes:
        Tuple of class names exported from ``__init__.py``.
    exported_functions:
        Tuple of function names exported from ``__init__.py``.
    dependencies:
        Tuple of required sibling/external package names.
    author:
        Author identifier string.
    created_at:
        Unix timestamp of initial creation.
    is_stable:
        Whether the public API is considered stable.
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

        Returns
        -------
        dict[str, Any]
            Maps field names to their values (tuples converted to lists for
            JSON compatibility).
        """
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialise the manifest to a JSON string.

        Parameters
        ----------
        indent:
            Number of spaces for indentation.

        Returns
        -------
        str
            Pretty-printed JSON string.
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackageManifest:
        """Deserialise a :class:`PackageManifest` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict` or parsed from JSON.

        Returns
        -------
        PackageManifest

        Raises
        ------
        ManifestError
            If required keys are absent.
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

        Parameters
        ----------
        s:
            JSON string as produced by :meth:`to_json`.

        Returns
        -------
        PackageManifest
        """
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check well-formedness conditions from theory2.tex §cover_design.1.

        Conditions checked:

        * Version string matches MAJOR.MINOR.PATCH.
        * ``exported_classes`` and ``exported_functions`` are non-empty.
        * ``theory_chapter`` and ``theory_section`` are non-empty strings.
        * ``created_at`` is a positive float.
        * ``package_name`` is non-empty.

        Returns
        -------
        list[str]
            List of human-readable error strings; empty list means valid.
        """
        errors: list[str] = []

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

        Returns
        -------
        dict[str, tuple[str, ...]]
            Dict with keys ``"classes"`` and ``"functions"``.
        """
        return {
            "classes": self.exported_classes,
            "functions": self.exported_functions,
        }

    def check_dependency(self, dep_name: str) -> bool:
        """Check whether *dep_name* is listed as a dependency.

        Parameters
        ----------
        dep_name:
            The dependency name to look up.

        Returns
        -------
        bool
            True if *dep_name* appears in :attr:`dependencies`.
        """
        return dep_name in self.dependencies

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary.

        Returns
        -------
        str
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
        """Return the combined tuple of all exported symbols.

        Returns
        -------
        tuple[str, ...]
            Classes then functions in declaration order.
        """
        return self.exported_classes + self.exported_functions


# ---------------------------------------------------------------------------
# FileManifest
# ---------------------------------------------------------------------------


@dataclass
class FileManifest:
    """Per-file manifest record for the cover_design package.

    Implements the file manifest concept introduced in theory2.tex
    §cover_design.2.  Each :class:`FileManifest` binds a source file to the
    theory sections it realises, the Python objects it defines, and basic
    metrics.

    Attributes
    ----------
    file_name:
        Bare file name (e.g. ``"models.py"``).
    module_path:
        Fully-qualified Python module path.
    purpose:
        One-sentence description of the file's role.
    classes:
        Tuple of class names defined in this file.
    functions:
        Tuple of top-level function names defined in this file.
    theory_refs:
        Tuple of theory2.tex section identifiers
        (e.g. ``"§cover_design.1"``).
    lines_of_code:
        Approximate number of non-blank, non-comment lines.
    last_modified:
        Unix timestamp of the last known modification.
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

        Returns
        -------
        dict[str, Any]
            All fields; tuples serialised as lists.
        """
        return asdict(self)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a compact summary line for the file.

        Returns
        -------
        str
            Single-line string: ``file_name — purpose (N lines, K theory refs)``.
        """
        return (
            f"{self.file_name} — {self.purpose} "
            f"({self.lines_of_code} lines, {len(self.theory_refs)} theory refs)"
        )

    def get_theory_refs(self) -> tuple[str, ...]:
        """Return the tuple of theory section identifiers.

        Returns
        -------
        tuple[str, ...]
            Strings such as ``("§cover_design.1", "§cover_design.2")``.
        """
        return self.theory_refs

    def is_theory_aligned(self) -> bool:
        """Return True when this file cites at least one theory reference.

        A file is *theory-aligned* (§cover_design.2 definition) iff it
        cites at least one section of theory2.tex.

        Returns
        -------
        bool
        """
        return len(self.theory_refs) > 0


# ---------------------------------------------------------------------------
# ManifestRegistry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """Registry of :class:`FileManifest` objects for the cover_design package.

    Implements the manifest registry of theory2.tex §cover_design.3.  The
    registry supports O(1) lookup by file name and O(k) lookup by theory
    reference where k is the number of files citing the given section.

    Attributes
    ----------
    _store:
        Internal dict mapping ``file_name -> FileManifest``.
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

        Parameters
        ----------
        fm:
            The :class:`FileManifest` to register.
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

        Parameters
        ----------
        file_name:
            Bare file name, e.g. ``"models.py"``.

        Returns
        -------
        FileManifest | None
            The matching :class:`FileManifest` or ``None`` if absent.
        """
        return self._store.get(file_name)

    def list_all(self) -> list[FileManifest]:
        """Return all registered file manifests in registration order.

        Returns
        -------
        list[FileManifest]
        """
        return list(self._store.values())

    def get_by_theory_ref(self, ref: str) -> list[FileManifest]:
        """Return all manifests that cite *ref*.

        Parameters
        ----------
        ref:
            Theory section identifier, e.g. ``"§cover_design.4"``.

        Returns
        -------
        list[FileManifest]
            Possibly empty list of matching manifests.
        """
        return [fm for fm in self._store.values() if ref in fm.theory_refs]

    def validate_all(self) -> dict[str, list[str]]:
        """Validate every registered manifest for basic consistency.

        Checks performed per file:

        * ``file_name`` is non-empty.
        * ``module_path`` is non-empty.
        * ``lines_of_code`` is non-negative.
        * At least one theory ref is present.

        Returns
        -------
        dict[str, list[str]]
            Maps ``file_name -> list[error_string]``; files without errors
            are omitted.
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

        Returns
        -------
        str
            One line per file plus a totals line.
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

        Parameters
        ----------
        indent:
            Indentation for the JSON output.

        Returns
        -------
        str
            JSON string encoding a list of file manifest dicts.
        """
        payload = [fm.to_dict() for fm in self._store.values()]
        return json.dumps(payload, indent=indent)


# ---------------------------------------------------------------------------
# ManifestDiagnostics
# ---------------------------------------------------------------------------


class ManifestDiagnostics:
    """Diagnostic checks for the cover_design package manifest.

    Aggregates well-formedness conditions described in theory2.tex
    §cover_design.1–§cover_design.3 and exposes them as a single diagnostic
    runner.

    Attributes
    ----------
    package_manifest:
        The :class:`PackageManifest` under examination.
    registry:
        The :class:`ManifestRegistry` under examination.
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

        Expected files are the 8 files constituting the cover_design package.

        Returns
        -------
        dict[str, Any]
            Dict with keys ``"present"``, ``"missing"``, ``"extra"``.
        """
        expected = {
            "manifest.py",
            "models.py",
            "cover_completeness.py",
            "cech_condition.py",
            "budget_allocation.py",
            "patch_selection.py",
            "dependency_ordering.py",
            "parallelism.py",
            "quality_metrics.py",
            "integration.py",
            "algorithms.py",
            "theorems.py",
            "integration.py",
        }
        registered = {fm.file_name for fm in self.registry.list_all()}
        present = expected & registered
        missing = expected - registered
        extra = registered - expected
        logger.debug(
            "check_completeness: present=%d, missing=%d, extra=%d",
            len(present), len(missing), len(extra),
        )
        return {
            "present": sorted(present),
            "missing": sorted(missing),
            "extra": sorted(extra),
        }

    def check_theory_alignment(self) -> dict[str, bool]:
        """Check that every registered file is theory-aligned.

        Returns
        -------
        dict[str, bool]
            Maps ``file_name -> is_theory_aligned``.
        """
        return {
            fm.file_name: fm.is_theory_aligned()
            for fm in self.registry.list_all()
        }

    def check_theorem_coverage(self) -> dict[str, list[str]]:
        """Check which theorems are covered by at least one file.

        The eight T_CD theorems are derived from §cover_design.1–8.  A
        theorem is *covered* if at least one registered file cites the
        corresponding section.

        Returns
        -------
        dict[str, list[str]]
            Maps each theorem tag to the list of files that cover it.
        """
        theorem_sections = {
            "T_CD_1": "§cover_design.1",
            "T_CD_2": "§cover_design.2",
            "T_CD_3": "§cover_design.3",
            "T_CD_4": "§cover_design.5",
            "T_CD_5": "§cover_design.5",
            "T_CD_6": "§cover_design.6",
            "T_CD_7": "§cover_design.7",
            "T_CD_8": "§cover_design.3",
        }
        coverage: dict[str, list[str]] = {}
        for theorem, section in theorem_sections.items():
            files = [fm.file_name for fm in self.registry.get_by_theory_ref(section)]
            coverage[theorem] = files
        return coverage

    def report(self) -> str:
        """Produce a combined diagnostic report.

        Runs :meth:`check_completeness`, :meth:`check_theory_alignment`,
        :meth:`check_theorem_coverage`, and
        :meth:`ManifestRegistry.validate_all`, then formats the results.

        Returns
        -------
        str
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
        lines.append("\n[2] Package completeness")
        lines.append(f"    Present : {', '.join(completeness['present']) or '—'}")
        lines.append(f"    Missing : {', '.join(completeness['missing']) or '—'}")
        lines.append(f"    Extra   : {', '.join(completeness['extra']) or '—'}")

        # Theory alignment
        alignment = self.check_theory_alignment()
        lines.append("\n[3] Theory alignment (§cover_design.2)")
        for fname, aligned in sorted(alignment.items()):
            status = "aligned" if aligned else "UNALIGNED"
            lines.append(f"    {fname:<55} {status}")

        # Theorem coverage
        theorem_cov = self.check_theorem_coverage()
        lines.append("\n[4] Theorem coverage")
        for theorem, files in sorted(theorem_cov.items()):
            covered_str = ", ".join(files) if files else "NOT COVERED"
            lines.append(f"    {theorem}: {covered_str}")

        # File-level validation
        file_errors = self.registry.validate_all()
        lines.append("\n[5] File-level validation")
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
    """Walk *directory* and verify every expected cover_design file exists.

    Implements the structural check described in theory2.tex §cover_design.1:
    a package is *structurally complete* iff all expected files exist on disk
    and are non-empty.

    Parameters
    ----------
    directory:
        Path to the cover_design package directory.

    Returns
    -------
    dict[str, Any]
        Dict with keys:

        ``"status"``
            ``"ok"`` or ``"incomplete"``
        ``"directory"``
            Resolved absolute path as a string.
        ``"files"``
            Dict mapping each expected file name to a sub-dict with keys
            ``"exists"`` (bool), ``"size_bytes"`` (int or None), and
            ``"non_empty"`` (bool).
        ``"missing_files"``
            List of file names that are absent.
        ``"empty_files"``
            List of file names that exist but are empty (0 bytes).

    Raises
    ------
    ManifestError
        If *directory* does not exist or is not a directory.
    """
    base = Path(directory).resolve()
    if not base.exists():
        raise MissingFileError(f"directory does not exist: {base}")
    if not base.is_dir():
        raise ManifestError(f"path is not a directory: {base}")

    expected_files = [
        "__init__.py",
        "manifest.py",
        "models.py",
        "cover_completeness.py",
        "cech_condition.py",
        "budget_allocation.py",
        "patch_selection.py",
        "dependency_ordering.py",
        "parallelism.py",
        "quality_metrics.py",
        "integration.py",
        "algorithms.py",
        "theorems.py",
        "integration.py",
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
    package_name="jugeo.generation.cover_design",
    version="0.1.0",
    description=(
        "Cover design algorithms and theorem suite for the JuGeo generation "
        "pipeline — patch selection, Čech condition, budget allocation, "
        "dependency ordering, and parallelism (theory2.tex §cover_design)."
    ),
    theory_chapter="§cover_design",
    theory_section="§cover_design.1",
    exported_classes=(
        "DependencyGraph",
        "OverlapGraph",
        "ScheduleResult",
        "TheoremResult",
        "TheoremSuite",
    ),
    exported_functions=(
        "greedy_cover_algorithm",
        "topological_sort_patches",
        "compute_critical_path",
        "compute_antichain_decomposition",
        "check_cech_condition",
        "compute_overlap_graph",
        "priority_weighted_allocation",
        "compute_coverage_completeness",
        "compute_coffman_graham_order",
        "estimate_patch_cost",
        "run_all_theorems",
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
"""Module-level :class:`PackageManifest` for the cover_design package."""


def _build_registry() -> ManifestRegistry:
    """Construct and return the canonical :class:`ManifestRegistry`.

    Internal factory function used to initialise :data:`FILE_MANIFESTS`.
    Not part of the public API.

    Returns
    -------
    ManifestRegistry
        Pre-populated with all cover_design file manifests.
    """
    reg = ManifestRegistry()
    _ts = 1_700_000_000.0  # shared placeholder last_modified timestamp

    reg.register(FileManifest(
        file_name="manifest.py",
        module_path="jugeo.generation.cover_design.manifest",
        purpose="Package manifest, registry, and structural validation",
        classes=(
            "PackageManifest",
            "FileManifest",
            "ManifestRegistry",
            "ManifestDiagnostics",
        ),
        functions=(
            "get_manifest",
            "get_file_manifest",
            "validate_package_structure",
        ),
        theory_refs=("§cover_design.1", "§cover_design.2", "§cover_design.3"),
        lines_of_code=520,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="models.py",
        module_path="jugeo.generation.cover_design.models",
        purpose="Core dataclass models: PatchDescriptor, Budget, CoverSection, CoverDesignState",
        classes=(
            "PatchDescriptor",
            "Budget",
            "CoverSection",
            "CoverDesignState",
            "CoverDesignError",
            "BudgetOverflowError",
            "CoverIncompleteError",
            "CechViolationError",
            "TrustTier",
            "PatchStatus",
        ),
        functions=(),
        theory_refs=(
            "§cover_design.1",
            "§cover_design.2",
            "§cover_design.3",
            "§cover_design.8",
        ),
        lines_of_code=480,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="cover_completeness.py",
        module_path="jugeo.generation.cover_design.cover_completeness",
        purpose="Section cover_design.1 — definition and verification of cover completeness",
        classes=("CoverCompletenessChecker",),
        functions=("check_cover_completeness", "compute_uncovered_regions"),
        theory_refs=("§cover_design.1",),
        lines_of_code=240,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="cech_condition.py",
        module_path="jugeo.generation.cover_design.cech_condition",
        purpose="Section cover_design.2 — Čech condition and sheaf-theoretic gluing",
        classes=("CechConditionChecker", "GluingResult"),
        functions=(
            "verify_cech_condition",
            "compute_section_restriction",
            "glue_sections",
        ),
        theory_refs=("§cover_design.2",),
        lines_of_code=280,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="budget_allocation.py",
        module_path="jugeo.generation.cover_design.budget_allocation",
        purpose="Section cover_design.3 — budget as first-class object, allocation strategies",
        classes=("BudgetAllocator", "BudgetRecord"),
        functions=(
            "allocate_budget",
            "compute_overhead",
            "check_budget_admissibility",
        ),
        theory_refs=("§cover_design.3",),
        lines_of_code=260,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="patch_selection.py",
        module_path="jugeo.generation.cover_design.patch_selection",
        purpose="Section cover_design.4 — greedy patch selection and cost estimation",
        classes=("PatchSelector", "CostModel"),
        functions=(
            "select_patches",
            "score_patch",
            "estimate_marginal_gain",
        ),
        theory_refs=("§cover_design.4",),
        lines_of_code=240,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="dependency_ordering.py",
        module_path="jugeo.generation.cover_design.dependency_ordering",
        purpose="Section cover_design.5 — DAG construction, topological sort, critical path",
        classes=("DependencyBuilder", "CriticalPathAnalyser"),
        functions=(
            "build_dependency_dag",
            "sort_patches_topologically",
            "find_critical_path",
        ),
        theory_refs=("§cover_design.5",),
        lines_of_code=260,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="parallelism.py",
        module_path="jugeo.generation.cover_design.parallelism",
        purpose="Section cover_design.6 — antichain decomposition and Coffman–Graham scheduling",
        classes=("ParallelScheduler", "WaveAssignment"),
        functions=(
            "decompose_into_waves",
            "schedule_coffman_graham",
            "compute_parallelism_ratio",
        ),
        theory_refs=("§cover_design.6",),
        lines_of_code=240,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="quality_metrics.py",
        module_path="jugeo.generation.cover_design.quality_metrics",
        purpose="Section cover_design.7 — quality and completion criteria, monitoring",
        classes=("QualityMonitor", "CompletionCriteria"),
        functions=(
            "compute_quality_score",
            "check_completion_criteria",
            "monitor_coverage_progress",
        ),
        theory_refs=("§cover_design.7",),
        lines_of_code=220,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="integration.py",
        module_path="jugeo.generation.cover_design.integration",
        purpose="Section cover_design.8 — trust tiers, PROPOSAL entry, integration with outer loop",
        classes=("TrustPromoter", "CoverDesignIntegrator"),
        functions=(
            "promote_section_trust",
            "integrate_cover_design",
            "validate_trust_tier",
        ),
        theory_refs=("§cover_design.8",),
        lines_of_code=220,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="algorithms.py",
        module_path="jugeo.generation.cover_design.algorithms",
        purpose=(
            "Core algorithms: greedy cover, topological sort, critical path, "
            "antichain decomposition, Čech check, overlap graph, budget "
            "allocation, coverage completeness, Coffman–Graham, cost model"
        ),
        classes=("DependencyGraph", "OverlapGraph", "ScheduleResult"),
        functions=(
            "greedy_cover_algorithm",
            "topological_sort_patches",
            "compute_critical_path",
            "compute_antichain_decomposition",
            "check_cech_condition",
            "compute_overlap_graph",
            "priority_weighted_allocation",
            "compute_coverage_completeness",
            "compute_coffman_graham_order",
            "estimate_patch_cost",
        ),
        theory_refs=(
            "§cover_design.1",
            "§cover_design.2",
            "§cover_design.3",
            "§cover_design.4",
            "§cover_design.5",
            "§cover_design.6",
            "§cover_design.7",
        ),
        lines_of_code=600,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="theorems.py",
        module_path="jugeo.generation.cover_design.theorems",
        purpose=(
            "Theorem verification suite for T_CD_1–T_CD_8: cover completeness, "
            "Čech condition soundness, budget admissibility, acyclicity, "
            "topological ordering, parallelism safety, monotonicity, priority consistency"
        ),
        classes=("TheoremResult", "TheoremSuite"),
        functions=(
            "run_all_theorems",
            "verify_cover_completeness",
            "verify_cech_condition_soundness",
            "verify_budget_admissibility",
            "verify_dependency_acyclicity",
            "verify_topological_ordering_correctness",
            "verify_parallelism_safety",
            "verify_quality_threshold_monotonicity",
            "verify_priority_allocation_consistency",
        ),
        theory_refs=(
            "§cover_design.1",
            "§cover_design.2",
            "§cover_design.3",
            "§cover_design.5",
            "§cover_design.6",
            "§cover_design.7",
            "§cover_design.8",
        ),
        lines_of_code=580,
        last_modified=_ts,
    ))

    reg.register(FileManifest(
        file_name="integration.py",
        module_path="jugeo.generation.cover_design.integration",
        purpose=(
            "Integration adapters connecting cover design to the outer "
            "ConstructionLoop and the local_construction pipeline"
        ),
        classes=("CoverDesignAdapter", "CoverResultLifter"),
        functions=(
            "lift_cover_result",
            "lower_cover_goal",
            "merge_cover_sections",
        ),
        theory_refs=("§cover_design.8",),
        lines_of_code=200,
        last_modified=_ts,
    ))

    return reg


FILE_MANIFESTS: ManifestRegistry = _build_registry()
"""Module-level :class:`ManifestRegistry` pre-populated with all cover_design file manifests."""


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Return the canonical :data:`PACKAGE_MANIFEST` for this package.

    This is the primary entry-point described in theory2.tex §cover_design.1:
    every package exposes a ``get_manifest()`` function so that the outer
    generation loop can introspect it at runtime.

    Returns
    -------
    PackageManifest
        The module-level :class:`PackageManifest` singleton.

    Example
    -------
    ::

        from jugeo.generation.cover_design.manifest import get_manifest
        pm = get_manifest()
        print(pm.summary())
    """
    return PACKAGE_MANIFEST


def get_file_manifest(file_name: str) -> FileManifest:
    """Return the :class:`FileManifest` for a named file in this package.

    Parameters
    ----------
    file_name:
        Bare file name (e.g. ``"algorithms.py"``).

    Returns
    -------
    FileManifest
        The corresponding manifest record.

    Raises
    ------
    ManifestError
        If *file_name* is not registered in :data:`FILE_MANIFESTS`.

    Example
    -------
    ::

        fm = get_file_manifest("algorithms.py")
        print(fm.summary())
    """
    fm = FILE_MANIFESTS.get(file_name)
    if fm is None:
        registered = sorted(f.file_name for f in FILE_MANIFESTS.list_all())
        raise ManifestError(
            f"No manifest registered for '{file_name}'. "
            f"Known files: {registered}"
        )
    return fm


# ---------------------------------------------------------------------------
# Module self-check (runs when invoked directly)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

    diag = ManifestDiagnostics(PACKAGE_MANIFEST, FILE_MANIFESTS)
    print(diag.report())
    print()
    print(PACKAGE_MANIFEST.summary())
    print()

    # Round-trip serialisation check
    pm_json = PACKAGE_MANIFEST.to_json()
    pm_restored = PackageManifest.from_json(pm_json)
    assert pm_restored.package_name == PACKAGE_MANIFEST.package_name
    assert pm_restored.version == PACKAGE_MANIFEST.version
    print("Round-trip serialisation: OK")
    print()

    # Registry JSON export
    reg_json = FILE_MANIFESTS.export_as_json()
    import json as _json
    reg_data = _json.loads(reg_json)
    print(f"Registry JSON export: {len(reg_data)} file manifests")
    print()

    # Validate package structure at this file's parent directory
    this_dir = Path(__file__).parent
    result = validate_package_structure(this_dir)
    print(f"validate_package_structure: status={result['status']}")
    if result["missing_files"]:
        print(f"  Missing: {result['missing_files']}")
    if result["empty_files"]:
        print(f"  Empty  : {result['empty_files']}")
    print()

    # get_file_manifest smoke test
    for fname in ["algorithms.py", "theorems.py", "manifest.py"]:
        fm = get_file_manifest(fname)
        print(f"  {fm.summary()}")

    print()
    print("Smoke test PASSED.")
