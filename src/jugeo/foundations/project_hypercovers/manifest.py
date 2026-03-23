"""Theory2.tex Ch8 §8.0 — package manifest for project_hypercovers.

This module is the canonical source of metadata for the
``jugeo.foundations.project_hypercovers`` package, which implements the full
machinery of Theory2.tex Chapter 8: *Projects, modules, hypercovers, and
fleets*.

Chapter overview
----------------
Chapter 8 of Theory2.tex develops the theory of structured decomposition of
software projects for formal verification purposes.  The chapter is divided
into four sections, each with its own Python module in this package:

§8.1  Project Sites
    Defines a *project site* as a Grothendieck site (C, J) whose objects are
    the semantic coordinates of a project's modules, functions, interfaces,
    and tests.  The covering sieves are the admissible families of modules
    that collectively observe every semantic point in the site.
    Key definitions: Def 8.1 (ProjectSite), Def 8.2 (CoordinateRegistry),
    Def 8.3 (SemanticTopology), Def 8.4 (SiteMorphism).
    Key theorems: Thm 8.1 (ProjectSiteExistence), Lem 8.1
    (CoordinateUniqueness), Cor 8.1 (MorphismComposition).

§8.2  Module Covers
    Studies admissible covers of a project site — families of ModuleCover
    objects whose union spans the entire site.  Introduces the Čech nerve
    construction and proves the existence of admissible covers (Thm 8.2).
    Key definitions: Def 8.5 (ModuleCover), Def 8.6 (CoverAdmissibility),
    Def 8.7 (CechNerve), Def 8.8 (OverlapData).
    Key theorems: Thm 8.2 (AdmissibleCoverExistence), Thm 8.3
    (CechNerveContractible), Lem 8.2 (OverlapTransitivity).

§8.3  Fleet Structure
    Formalises a *fleet* of LLM agents assigned to cover elements, with
    trust aggregation (Thm 8.5) and load-balance optimality (Lem 8.3).
    Key definitions: Def 8.9 (FleetMember), Def 8.10 (FleetAssignment),
    Def 8.11 (TrustAggregation), Def 8.12 (LoadBalance).
    Key theorems: Thm 8.4 (FleetCoverage), Thm 8.5 (TrustMonotonicity),
    Lem 8.3 (LoadBalanceOptimality).

§8.4  Hypercover Refinement and Descent
    Develops iterated cover refinement resolving descent obstructions.
    Proves the hypercover descent theorem (Thm 8.6) and the obstruction
    vanishing criterion (Thm 8.7).
    Key definitions: Def 8.13 (HypercoverDecomposition), Def 8.14
    (DescentObstruction), Def 8.15 (RefinementEngine), Def 8.16
    (SimplicialStructure).
    Key theorems: Thm 8.6 (HypercoverDescent), Thm 8.7
    (ObstructionVanishing), Thm 8.8 (ModuleDecomposition), Lem 8.4
    (DescentSpectralSequence).

Usage
-----
::

    from jugeo.foundations.project_hypercovers.manifest import (
        get_manifest, list_exports, validate_package_integrity,
        format_manifest_report, PACKAGE_NAME, VERSION,
    )

    m = get_manifest()
    print(m.summary())
    print(format_manifest_report())

copilot: shared-core manifest — exposes package metadata for LLM orchestration.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: str = "jugeo.foundations.project_hypercovers"
VERSION: str = "0.1.0"
AUTHOR: str = "JuGeo Theory Team"
CHAPTER: int = 8
SECTION_START: int = 1
SECTION_END: int = 4
THEORY_FILE: str = "theory2.tex"
CHAPTER_TITLE: str = "Projects, modules, hypercovers, and fleets"
MIN_EXPORT_COUNT: int = 50
PACKAGE_ROOT: pathlib.Path = pathlib.Path(__file__).parent


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ModuleStatus(str, Enum):
    """Operational status of a module in the package.

    Notes
    -----
    Used in ``ModuleDescription.status`` to indicate whether a module is
    production-ready, experimental, deprecated, or merely a scaffold
    awaiting implementation.

    Examples
    --------
    >>> ModuleStatus.STABLE.value
    'stable'
    >>> ModuleStatus("experimental") == ModuleStatus.EXPERIMENTAL
    True
    """

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    SCAFFOLD = "scaffold"


class ExportKind(str, Enum):
    """Kind of a Python symbol exported from this package.

    Notes
    -----
    Used in manifest export records to categorise each exported name so
    that LLM orchestrators can select appropriate handling strategies
    (e.g. only instantiate DATACLASSes, only call FUNCTIONs).

    Examples
    --------
    >>> ExportKind.CLASS.value
    'class'
    >>> ExportKind.DATACLASS in list(ExportKind)
    True
    """

    CLASS = "class"
    FUNCTION = "function"
    CONSTANT = "constant"
    ENUM = "enum"
    DATACLASS = "dataclass"
    PROTOCOL = "protocol"


class DependencyKind(str, Enum):
    """Nature of a dependency between two modules or packages.

    Notes
    -----
    INTERNAL  — both source and target are within this package.
    EXTERNAL  — the target lives in another jugeo package.
    OPTIONAL  — the dependency may not be importable in all environments
                (e.g. optional C extensions or heavyweight ML libraries).

    Examples
    --------
    >>> DependencyKind.EXTERNAL.value
    'external'
    >>> DependencyKind.OPTIONAL != DependencyKind.INTERNAL
    True
    """

    INTERNAL = "internal"
    EXTERNAL = "external"
    OPTIONAL = "optional"


class SectionStatus(str, Enum):
    """Theory coverage status for a single §8.x section.

    Notes
    -----
    COMPLETE — all definitions and theorems in the section are implemented.
    PARTIAL  — some constructions are missing or only stubbed.
    STUB     — only the module structure exists; no real logic is present.

    Examples
    --------
    >>> SectionStatus.COMPLETE.value
    'complete'
    >>> SectionStatus.STUB != SectionStatus.COMPLETE
    True
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    STUB = "stub"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ModuleDescription:
    """Description of a single Python module in the project_hypercovers package.

    Parameters
    ----------
    name : str
        Bare module name (e.g. ``"manifest"``).
    path : str
        Relative path from the package root (e.g. ``"manifest.py"``).
    description : str
        One-sentence human-readable description of the module's purpose.
    status : ModuleStatus
        Operational stability of this module.
    theory_section : str
        Theory2.tex section reference (e.g. ``"§8.0"``).
    export_count : int
        Number of public symbols exported by this module.
    line_count : int
        Approximate number of lines in the module.
    exports : list[str]
        Names of all public symbols exported.
    dependencies : list[str]
        Module or package names this module imports from.

    Notes
    -----
    Theory2.tex §8.0 — package manifest entry for a single module.

    Examples
    --------
    >>> md = ModuleDescription(
    ...     "manifest", "manifest.py", "Package manifest.",
    ...     ModuleStatus.STABLE, "§8.0", 14, 600,
    ... )
    >>> md.is_stable()
    True
    >>> "manifest" in md.summary()
    True
    """

    name: str
    path: str
    description: str
    status: ModuleStatus
    theory_section: str
    export_count: int
    line_count: int
    exports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    def is_stable(self) -> bool:
        """Return True if this module has STABLE status.

        Returns
        -------
        bool
            ``True`` when ``self.status == ModuleStatus.STABLE``.

        Examples
        --------
        >>> md = ModuleDescription("x", "x.py", "d", ModuleStatus.STABLE, "§8.0", 1, 10)
        >>> md.is_stable()
        True
        """
        return self.status == ModuleStatus.STABLE

    def is_scaffold(self) -> bool:
        """Return True if this module is only a scaffold.

        Returns
        -------
        bool
            ``True`` when ``self.status == ModuleStatus.SCAFFOLD``.

        Examples
        --------
        >>> md = ModuleDescription("x", "x.py", "d", ModuleStatus.SCAFFOLD, "§8.0", 0, 5)
        >>> md.is_scaffold()
        True
        """
        return self.status == ModuleStatus.SCAFFOLD

    def summary(self) -> str:
        """Return a one-line summary string for this module description.

        Returns
        -------
        str
            Formatted string: ``"<name> (<status>) [<section>] — <description>"``.

        Examples
        --------
        >>> md = ModuleDescription("manifest", "manifest.py", "Package manifest.",
        ...     ModuleStatus.STABLE, "§8.0", 14, 600)
        >>> "manifest" in md.summary()
        True
        >>> "stable" in md.summary()
        True
        """
        return (
            f"{self.name} ({self.status.value}) [{self.theory_section}]"
            f" — {self.description}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ``ModuleDescription`` to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable dictionary with all fields.

        Examples
        --------
        >>> md = ModuleDescription("manifest", "manifest.py", "d",
        ...     ModuleStatus.STABLE, "§8.0", 1, 10)
        >>> isinstance(md.to_dict(), dict)
        True
        >>> md.to_dict()["name"]
        'manifest'
        """
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "status": self.status.value,
            "theory_section": self.theory_section,
            "export_count": self.export_count,
            "line_count": self.line_count,
            "exports": self.exports,
            "dependencies": self.dependencies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleDescription:
        """Construct a ``ModuleDescription`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ModuleDescription
            Reconstructed instance.

        Raises
        ------
        KeyError
            If a required field is missing from *data*.
        ValueError
            If ``data["status"]`` is not a valid :class:`ModuleStatus` value.

        Examples
        --------
        >>> d = {"name": "x", "path": "x.py", "description": "d",
        ...      "status": "stable", "theory_section": "§8.0",
        ...      "export_count": 1, "line_count": 10,
        ...      "exports": [], "dependencies": []}
        >>> ModuleDescription.from_dict(d).name
        'x'
        """
        return cls(
            name=data["name"],
            path=data["path"],
            description=data["description"],
            status=ModuleStatus(data["status"]),
            theory_section=data["theory_section"],
            export_count=data["export_count"],
            line_count=data["line_count"],
            exports=data.get("exports", []),
            dependencies=data.get("dependencies", []),
        )


@dataclass
class TheorySection:
    """Metadata describing a single §8.x section of Theory2.tex.

    Parameters
    ----------
    section_id : str
        Short identifier, e.g. ``"8.1"``.
    title : str
        Section title as it appears in Theory2.tex.
    theory_file : str
        Name of the theory file (``"theory2.tex"``).
    chapter : int
        Chapter number (8).
    section_number : int
        Section number within the chapter (1–4).
    subsection : int
        Subsection index; 0 if this record describes the section itself,
        positive for a specific subsection.
    page_estimate : int
        Estimated page span of the section in the compiled PDF.
    status : SectionStatus
        Implementation coverage status for this section.
    key_definitions : list[str]
        Names of key definitions introduced in this section.
    key_theorems : list[str]
        Names of key theorems proved in this section.

    Notes
    -----
    Theory2.tex §8.0 — section metadata record.

    Examples
    --------
    >>> ts = TheorySection("8.1", "Project Sites", "theory2.tex",
    ...     8, 1, 0, 14, SectionStatus.COMPLETE)
    >>> ts.section_ref()
    'theory2.tex §8.1'
    >>> ts.is_complete()
    True
    """

    section_id: str
    title: str
    theory_file: str
    chapter: int
    section_number: int
    subsection: int
    page_estimate: int
    status: SectionStatus
    key_definitions: list[str] = field(default_factory=list)
    key_theorems: list[str] = field(default_factory=list)

    def section_ref(self) -> str:
        """Return the canonical section reference string.

        Returns
        -------
        str
            String of the form ``"theory2.tex §8.1"``.

        Examples
        --------
        >>> ts = TheorySection("8.2", "Module Covers", "theory2.tex",
        ...     8, 2, 0, 16, SectionStatus.COMPLETE)
        >>> ts.section_ref()
        'theory2.tex §8.2'
        """
        return f"{self.theory_file} §{self.section_id}"

    def is_complete(self) -> bool:
        """Return True if this section's implementation status is COMPLETE.

        Returns
        -------
        bool
            ``True`` when ``self.status == SectionStatus.COMPLETE``.

        Examples
        --------
        >>> ts = TheorySection("8.1", "Project Sites", "theory2.tex",
        ...     8, 1, 0, 14, SectionStatus.COMPLETE)
        >>> ts.is_complete()
        True
        >>> ts2 = TheorySection("8.1", "X", "theory2.tex",
        ...     8, 1, 0, 5, SectionStatus.STUB)
        >>> ts2.is_complete()
        False
        """
        return self.status == SectionStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ``TheorySection`` to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable dictionary with all fields.

        Examples
        --------
        >>> ts = TheorySection("8.1", "Project Sites", "theory2.tex",
        ...     8, 1, 0, 14, SectionStatus.COMPLETE)
        >>> isinstance(ts.to_dict(), dict)
        True
        >>> ts.to_dict()["section_id"]
        '8.1'
        """
        return {
            "section_id": self.section_id,
            "title": self.title,
            "theory_file": self.theory_file,
            "chapter": self.chapter,
            "section_number": self.section_number,
            "subsection": self.subsection,
            "page_estimate": self.page_estimate,
            "status": self.status.value,
            "key_definitions": self.key_definitions,
            "key_theorems": self.key_theorems,
        }


@dataclass
class DependencyRecord:
    """A single directed dependency between two modules or packages.

    Parameters
    ----------
    source : str
        Fully-qualified name of the importing module
        (e.g. ``"models"`` or ``"jugeo.foundations.project_hypercovers.models"``).
    target : str
        Fully-qualified name of the dependency
        (e.g. ``"jugeo.geometry.hypercovers"``).
    kind : DependencyKind
        Whether the dependency is INTERNAL, EXTERNAL, or OPTIONAL.
    description : str
        Human-readable explanation of why this dependency exists.
    required : bool
        Whether the package is non-functional without this dependency.

    Notes
    -----
    Theory2.tex §8.0 — dependency tracking record for the manifest.

    Examples
    --------
    >>> dr = DependencyRecord(
    ...     "models", "jugeo.geometry.hypercovers",
    ...     DependencyKind.EXTERNAL, "Uses HypercoverLevel.", True,
    ... )
    >>> dr.is_optional()
    False
    >>> dr.to_dict()["kind"]
    'external'
    """

    source: str
    target: str
    kind: DependencyKind
    description: str
    required: bool

    def is_optional(self) -> bool:
        """Return True if this dependency is OPTIONAL or non-required.

        A dependency is considered optional if its ``kind`` is
        ``DependencyKind.OPTIONAL`` *or* its ``required`` field is ``False``.

        Returns
        -------
        bool
            ``True`` when the dependency need not be satisfied for the
            package to be functional.

        Examples
        --------
        >>> dr = DependencyRecord("a", "b", DependencyKind.OPTIONAL, "d", False)
        >>> dr.is_optional()
        True
        >>> dr2 = DependencyRecord("a", "b", DependencyKind.EXTERNAL, "d", True)
        >>> dr2.is_optional()
        False
        """
        return self.kind == DependencyKind.OPTIONAL or not self.required

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ``DependencyRecord`` to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable dictionary with all fields.

        Examples
        --------
        >>> dr = DependencyRecord("a", "b", DependencyKind.EXTERNAL, "desc", True)
        >>> isinstance(dr.to_dict(), dict)
        True
        >>> dr.to_dict()["required"]
        True
        """
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "description": self.description,
            "required": self.required,
        }


@dataclass
class PackageManifest:
    """Complete manifest for the ``jugeo.foundations.project_hypercovers`` package.

    Parameters
    ----------
    name : str
        Fully-qualified Python package name.
    version : str
        Semantic version string (``"MAJOR.MINOR.PATCH"``).
    chapter : int
        Theory2.tex chapter number implemented by this package (8).
    section_range : tuple[int, int]
        ``(first_section, last_section)`` within the chapter.
    theory_file : str
        Name of the LaTeX theory source file (``"theory2.tex"``).
    exports : list[str]
        All public symbol names exported by the package.
    dependencies : list[str]
        All external package/module dependencies (deduplicated, sorted).
    modules : list[ModuleDescription]
        Descriptions of each Python module in the package.
    theory_sections : list[TheorySection]
        Metadata for each theory section covered by this package.
    dependency_records : list[DependencyRecord]
        Detailed directed-dependency relationship records.
    created_at : float
        Unix timestamp of manifest creation; defaults to ``time.time()``.

    Notes
    -----
    Theory2.tex §8.0 — top-level manifest for the project_hypercovers package.

    The manifest is the single source of truth for:

    * Which symbols are exported and from which sub-module they originate.
    * Which theory sections are covered and to what completeness degree.
    * What external dependencies are required for the package to function.
    * Integrity verification via a SHA-256 hash of key manifest contents.

    Examples
    --------
    >>> m = get_manifest()
    >>> m.count_exports() >= 50
    True
    >>> m.validate()["name_nonempty"]
    True
    >>> "8.1" in m.check_section_coverage()
    True
    """

    name: str
    version: str
    chapter: int
    section_range: tuple[int, int]
    theory_file: str
    exports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    modules: list[ModuleDescription] = field(default_factory=list)
    theory_sections: list[TheorySection] = field(default_factory=list)
    dependency_records: list[DependencyRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def get_module(self, name: str) -> ModuleDescription | None:
        """Return the ``ModuleDescription`` for module *name*, or None.

        Parameters
        ----------
        name : str
            The bare module name to look up (e.g. ``"manifest"``).

        Returns
        -------
        ModuleDescription or None
            The first matching description, or ``None`` if not found.

        Examples
        --------
        >>> m = get_manifest()
        >>> m.get_module("manifest") is not None
        True
        >>> m.get_module("nonexistent") is None
        True
        """
        for mod in self.modules:
            if mod.name == name:
                return mod
        return None

    def get_section(self, section_id: str) -> TheorySection | None:
        """Return the ``TheorySection`` for *section_id*, or None.

        Parameters
        ----------
        section_id : str
            Section identifier, e.g. ``"8.1"``.

        Returns
        -------
        TheorySection or None
            The matching section record, or ``None`` if not found.

        Examples
        --------
        >>> m = get_manifest()
        >>> m.get_section("8.1") is not None
        True
        >>> m.get_section("99.99") is None
        True
        """
        for sec in self.theory_sections:
            if sec.section_id == section_id:
                return sec
        return None

    def list_stable_modules(self) -> list[str]:
        """Return names of all STABLE modules in the package.

        Returns
        -------
        list[str]
            Sorted list of module names with ``status == ModuleStatus.STABLE``.

        Examples
        --------
        >>> m = get_manifest()
        >>> isinstance(m.list_stable_modules(), list)
        True
        >>> "manifest" in m.list_stable_modules()
        True
        """
        return sorted(mod.name for mod in self.modules if mod.is_stable())

    def list_experimental_modules(self) -> list[str]:
        """Return names of all EXPERIMENTAL modules in the package.

        Returns
        -------
        list[str]
            Sorted list of module names with
            ``status == ModuleStatus.EXPERIMENTAL``.

        Examples
        --------
        >>> m = get_manifest()
        >>> isinstance(m.list_experimental_modules(), list)
        True
        """
        return sorted(
            mod.name
            for mod in self.modules
            if mod.status == ModuleStatus.EXPERIMENTAL
        )

    def count_exports(self) -> int:
        """Return the total number of exported symbols.

        Returns
        -------
        int
            Length of ``self.exports``.

        Examples
        --------
        >>> m = get_manifest()
        >>> m.count_exports() == len(m.exports)
        True
        """
        return len(self.exports)

    def count_modules(self) -> int:
        """Return the total number of described modules.

        Returns
        -------
        int
            Length of ``self.modules``.

        Examples
        --------
        >>> m = get_manifest()
        >>> m.count_modules() >= 9
        True
        """
        return len(self.modules)

    def validate(self) -> dict[str, bool]:
        """Run a suite of basic integrity checks on this manifest.

        Checks performed
        ----------------
        name_nonempty
            ``self.name`` is a non-empty string.
        version_valid
            ``self.version`` matches the semver pattern ``N.N.N``.
        chapter_positive
            ``self.chapter > 0``.
        has_exports
            ``len(self.exports) >= MIN_EXPORT_COUNT``.
        has_modules
            ``len(self.modules) >= 1``.
        has_theory_sections
            ``len(self.theory_sections) >= 1``.
        section_range_valid
            ``self.section_range`` is a 2-tuple with first ≤ last.

        Returns
        -------
        dict[str, bool]
            Mapping from check name to pass/fail boolean.

        Examples
        --------
        >>> m = get_manifest()
        >>> results = m.validate()
        >>> all(results.values())
        True
        """
        import re

        semver_pattern = re.compile(r"^\d+\.\d+\.\d+$")
        return {
            "name_nonempty": bool(self.name),
            "version_valid": bool(semver_pattern.match(self.version)),
            "chapter_positive": self.chapter > 0,
            "has_exports": len(self.exports) >= MIN_EXPORT_COUNT,
            "has_modules": len(self.modules) >= 1,
            "has_theory_sections": len(self.theory_sections) >= 1,
            "section_range_valid": (
                len(self.section_range) == 2
                and self.section_range[0] <= self.section_range[1]
            ),
        }

    def compute_integrity_hash(self) -> str:
        """Compute a SHA-256 integrity hash of the manifest's key contents.

        The hash is computed over the JSON serialisation of the manifest's
        name, version, sorted exports list, and sorted module names.  This
        provides a lightweight fingerprint for change detection without
        hashing timestamps or other volatile fields.

        Returns
        -------
        str
            64-character lower-case hexadecimal SHA-256 digest.

        Examples
        --------
        >>> m = get_manifest()
        >>> h = m.compute_integrity_hash()
        >>> len(h) == 64
        True
        >>> h == m.compute_integrity_hash()  # deterministic
        True
        """
        payload = json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "exports": sorted(self.exports),
                "module_names": sorted(mod.name for mod in self.modules),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ``PackageManifest`` to a JSON-serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with all manifest fields suitable for ``json.dumps``.

        Examples
        --------
        >>> m = get_manifest()
        >>> d = m.to_dict()
        >>> isinstance(d, dict)
        True
        >>> d["name"] == PACKAGE_NAME
        True
        """
        return {
            "name": self.name,
            "version": self.version,
            "chapter": self.chapter,
            "section_range": list(self.section_range),
            "theory_file": self.theory_file,
            "exports": self.exports,
            "dependencies": self.dependencies,
            "modules": [mod.to_dict() for mod in self.modules],
            "theory_sections": [sec.to_dict() for sec in self.theory_sections],
            "dependency_records": [dr.to_dict() for dr in self.dependency_records],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Construct a ``PackageManifest`` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest
            Reconstructed manifest instance.

        Raises
        ------
        KeyError
            If any required key is missing from *data*.
        ValueError
            If an enum field contains an unrecognised value.

        Examples
        --------
        >>> m = get_manifest()
        >>> m2 = PackageManifest.from_dict(m.to_dict())
        >>> m2.name == m.name
        True
        >>> m2.version == m.version
        True
        """
        return cls(
            name=data["name"],
            version=data["version"],
            chapter=data["chapter"],
            section_range=tuple(data["section_range"]),  # type: ignore[arg-type]
            theory_file=data["theory_file"],
            exports=data.get("exports", []),
            dependencies=data.get("dependencies", []),
            modules=[
                ModuleDescription.from_dict(md) for md in data.get("modules", [])
            ],
            theory_sections=[
                TheorySection(
                    section_id=s["section_id"],
                    title=s["title"],
                    theory_file=s["theory_file"],
                    chapter=s["chapter"],
                    section_number=s["section_number"],
                    subsection=s["subsection"],
                    page_estimate=s["page_estimate"],
                    status=SectionStatus(s["status"]),
                    key_definitions=s.get("key_definitions", []),
                    key_theorems=s.get("key_theorems", []),
                )
                for s in data.get("theory_sections", [])
            ],
            dependency_records=[
                DependencyRecord(
                    source=dr["source"],
                    target=dr["target"],
                    kind=DependencyKind(dr["kind"]),
                    description=dr["description"],
                    required=dr["required"],
                )
                for dr in data.get("dependency_records", [])
            ],
            created_at=data.get("created_at", 0.0),
        )

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary of this manifest.

        Returns
        -------
        str
            Multi-line string describing the package name, version, chapter,
            export count, module count, and integrity hash prefix.

        Examples
        --------
        >>> m = get_manifest()
        >>> "project_hypercovers" in m.summary()
        True
        >>> "Ch8" in m.summary()
        True
        """
        lines = [
            f"Package : {self.name} v{self.version}",
            f"Chapter : {self.theory_file} Ch{self.chapter} ({CHAPTER_TITLE})",
            f"Sections: §{self.section_range[0]}–§{self.section_range[1]}",
            f"Modules : {self.count_modules()}",
            f"Exports : {self.count_exports()}",
            f"Deps    : {len(self.dependencies)}",
            f"Hash    : {self.compute_integrity_hash()[:16]}...",
        ]
        return "\n".join(lines)

    def check_section_coverage(self) -> dict[str, SectionStatus]:
        """Return a mapping from section_id to its implementation status.

        Returns
        -------
        dict[str, SectionStatus]
            Keys are section identifiers (e.g. ``"8.1"``); values are
            :class:`SectionStatus` members indicating how completely the
            corresponding theory section is implemented.

        Examples
        --------
        >>> m = get_manifest()
        >>> cov = m.check_section_coverage()
        >>> "8.1" in cov
        True
        >>> cov["8.4"] == SectionStatus.COMPLETE
        True
        """
        return {sec.section_id: sec.status for sec in self.theory_sections}

    def find_dependencies(self, module_name: str) -> list[DependencyRecord]:
        """Return all dependency records where *module_name* is the source.

        Parameters
        ----------
        module_name : str
            Bare module name to search for (e.g. ``"models"``).

        Returns
        -------
        list[DependencyRecord]
            All :class:`DependencyRecord` entries whose ``source`` field
            matches *module_name*.

        Examples
        --------
        >>> m = get_manifest()
        >>> deps = m.find_dependencies("models")
        >>> isinstance(deps, list)
        True
        >>> all(d.source == "models" for d in deps)
        True
        """
        return [dr for dr in self.dependency_records if dr.source == module_name]


# ---------------------------------------------------------------------------
# MODULE_REGISTRY
# ---------------------------------------------------------------------------

MODULE_REGISTRY: dict[str, str] = {
    "__init__": (
        "Package initialiser — re-exports all public symbols from the 9 "
        "sub-modules and provides package_summary(), get_chapter_overview(), "
        "and verify_imports() convenience helpers."
    ),
    "manifest": (
        "§8.0 — Package manifest: metadata, integrity checking, theory section "
        "coverage, and dependency tracking for the project_hypercovers package."
    ),
    "models": (
        "§8.1–§8.4 — Core domain models: ProjectSite, ModuleCover, FleetMember, "
        "HypercoverDecomposition, and supporting enumerations ProjectKind, "
        "CoverStrategy, FleetStatus, DecompositionStatus."
    ),
    "project_sites": (
        "§8.1 — Project site construction: SemanticSiteBuilder, "
        "CoordinateRegistry, TopologyGenerator, ProjectSiteInspector, and "
        "module-level helpers build_project_site, compute_site_morphisms, "
        "site_from_modules."
    ),
    "module_covers": (
        "§8.2 — Module cover machinery: CoverBuilder, OverlapComputer, "
        "AdmissibilityChecker, CoverRefiner, CechNerveComputer, and helpers "
        "build_module_cover, refine_cover_until_admissible, score_cover_quality."
    ),
    "fleet_structure": (
        "§8.3 — Fleet coordination: FleetCoordinator, LoadBalancer, "
        "TrustAggregator, FleetMonitor, FleetPlanner, and helpers "
        "assemble_fleet, assign_fleet_to_cover, compute_fleet_trust."
    ),
    "hypercover_refinement": (
        "§8.4 — Hypercover refinement: HypercoverBuilder, "
        "SimplicialStructureValidator, RefinementEngine, ObstructionAnalyzer, "
        "DescentCoordinator, and helpers build_hypercover, refine_hypercover, "
        "compute_descent_obstruction."
    ),
    "algorithms": (
        "Cross-cutting algorithms: greedy_cover_algorithm, "
        "optimal_fleet_assignment, hypercover_descent_algorithm, "
        "cech_complex_computation, obstruction_repair_algorithm, "
        "iterative_refinement_loop, trust_propagation_algorithm."
    ),
    "integration": (
        "Integration glue connecting project hypercovers to the judgment and "
        "evidence sub-systems: ProjectHypercoverIntegration, "
        "ProjectHypercoverExporter, ProjectHypercoverImporter, "
        "register_project_site, connect_fleet_to_judgment_system."
    ),
    "theorems": (
        "Formal theorem statements, proof steps, and verification status for "
        "all Ch8 results: TheoremRecord, TheoremRegistry, ProofVerifier, "
        "theorem_hypercover_descent, theorem_fleet_coverage, "
        "theorem_module_decomposition, theorem_cech_nerve_contractible, "
        "VerificationStatus, ProofStep."
    ),
}


# ---------------------------------------------------------------------------
# THEORY_SECTIONS
# ---------------------------------------------------------------------------

THEORY_SECTIONS: list[TheorySection] = [
    TheorySection(
        section_id="8.1",
        title="Project Sites",
        theory_file=THEORY_FILE,
        chapter=CHAPTER,
        section_number=1,
        subsection=0,
        page_estimate=14,
        status=SectionStatus.COMPLETE,
        key_definitions=[
            "Def 8.1 (ProjectSite)",
            "Def 8.2 (CoordinateRegistry)",
            "Def 8.3 (SemanticTopology)",
            "Def 8.4 (SiteMorphism)",
        ],
        key_theorems=[
            "Thm 8.1 (ProjectSiteExistence)",
            "Lem 8.1 (CoordinateUniqueness)",
            "Cor 8.1 (MorphismComposition)",
        ],
    ),
    TheorySection(
        section_id="8.2",
        title="Module Covers",
        theory_file=THEORY_FILE,
        chapter=CHAPTER,
        section_number=2,
        subsection=0,
        page_estimate=16,
        status=SectionStatus.COMPLETE,
        key_definitions=[
            "Def 8.5 (ModuleCover)",
            "Def 8.6 (CoverAdmissibility)",
            "Def 8.7 (CechNerve)",
            "Def 8.8 (OverlapData)",
        ],
        key_theorems=[
            "Thm 8.2 (AdmissibleCoverExistence)",
            "Thm 8.3 (CechNerveContractible)",
            "Lem 8.2 (OverlapTransitivity)",
        ],
    ),
    TheorySection(
        section_id="8.3",
        title="Fleet Structure",
        theory_file=THEORY_FILE,
        chapter=CHAPTER,
        section_number=3,
        subsection=0,
        page_estimate=18,
        status=SectionStatus.COMPLETE,
        key_definitions=[
            "Def 8.9 (FleetMember)",
            "Def 8.10 (FleetAssignment)",
            "Def 8.11 (TrustAggregation)",
            "Def 8.12 (LoadBalance)",
        ],
        key_theorems=[
            "Thm 8.4 (FleetCoverage)",
            "Thm 8.5 (TrustMonotonicity)",
            "Lem 8.3 (LoadBalanceOptimality)",
        ],
    ),
    TheorySection(
        section_id="8.4",
        title="Hypercover Refinement and Descent",
        theory_file=THEORY_FILE,
        chapter=CHAPTER,
        section_number=4,
        subsection=0,
        page_estimate=20,
        status=SectionStatus.COMPLETE,
        key_definitions=[
            "Def 8.13 (HypercoverDecomposition)",
            "Def 8.14 (DescentObstruction)",
            "Def 8.15 (RefinementEngine)",
            "Def 8.16 (SimplicialStructure)",
        ],
        key_theorems=[
            "Thm 8.6 (HypercoverDescent)",
            "Thm 8.7 (ObstructionVanishing)",
            "Thm 8.8 (ModuleDecomposition)",
            "Lem 8.4 (DescentSpectralSequence)",
        ],
    ),
]


# ---------------------------------------------------------------------------
# DEPENDENCY_RECORDS
# ---------------------------------------------------------------------------

DEPENDENCY_RECORDS: list[DependencyRecord] = [
    DependencyRecord(
        source="models",
        target="jugeo.geometry.hypercovers",
        kind=DependencyKind.EXTERNAL,
        description=(
            "HypercoverLevel, CechNerve, and HypercoverKind are used to type "
            "the hypercover_levels field of HypercoverDecomposition and the "
            "nerve field of ModuleCover (theory2.tex §8.4 Def 8.13)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="models",
        target="jugeo.geometry.descent",
        kind=DependencyKind.EXTERNAL,
        description=(
            "DescentEngine, DescentResult, LocalSection, and GluingData are "
            "used in FleetMember descent tracking and in the iterative descent "
            "loop of HypercoverDecomposition (theory2.tex §8.4 Def 8.15)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="models",
        target="jugeo.geometry.site",
        kind=DependencyKind.EXTERNAL,
        description=(
            "CoordinateObject and SemanticSite underpin the ProjectSite "
            "domain model; CoordinateKind classifies nodes in ModuleCover "
            "(theory2.tex §8.1 Def 8.1–Def 8.4)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="models",
        target="jugeo.geometry.covers",
        kind=DependencyKind.EXTERNAL,
        description=(
            "Cover and CoverMetric are the base types extended by ModuleCover "
            "to add semantic annotations and trust provenance "
            "(theory2.tex §8.2 Def 8.5)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="models",
        target="jugeo.judgments.judgment_terms",
        kind=DependencyKind.EXTERNAL,
        description=(
            "JudgmentTerm and JudgmentKind are embedded in FleetMember "
            "assignment records to link fleet tasks to first-class judgment "
            "obligations (theory2.tex §8.3 Def 8.10)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="models",
        target="jugeo.evidence.certificates",
        kind=DependencyKind.EXTERNAL,
        description=(
            "Certificate and CertificateStatus track the verification outcome "
            "attached to each completed HypercoverDecomposition "
            "(theory2.tex §8.4)."
        ),
        required=True,
    ),
    DependencyRecord(
        source="integration",
        target="jugeo.judgments.judgment_terms",
        kind=DependencyKind.EXTERNAL,
        description=(
            "connect_fleet_to_judgment_system uses JudgmentTerm to register "
            "fleet task assignments as first-class judgment obligations in the "
            "global judgment context."
        ),
        required=True,
    ),
    DependencyRecord(
        source="integration",
        target="jugeo.evidence.certificates",
        kind=DependencyKind.EXTERNAL,
        description=(
            "ProjectHypercoverExporter emits Certificate objects as part of "
            "the structured verification report produced after descent "
            "completion."
        ),
        required=False,
    ),
    DependencyRecord(
        source="hypercover_refinement",
        target="jugeo.geometry.descent",
        kind=DependencyKind.EXTERNAL,
        description=(
            "DescentCoordinator wraps DescentEngine to drive the iterative "
            "refinement loop defined in theory2.tex §8.4 Def 8.15."
        ),
        required=True,
    ),
    DependencyRecord(
        source="module_covers",
        target="jugeo.geometry.hypercovers",
        kind=DependencyKind.EXTERNAL,
        description=(
            "CechNerveComputer delegates to CechNerve from "
            "jugeo.geometry.hypercovers to build the simplicial complex of "
            "pairwise overlaps (theory2.tex §8.2 Def 8.7)."
        ),
        required=True,
    ),
]


# ---------------------------------------------------------------------------
# MODULE_DESCRIPTIONS
# ---------------------------------------------------------------------------

MODULE_DESCRIPTIONS: list[ModuleDescription] = [
    ModuleDescription(
        name="__init__",
        path="__init__.py",
        description=(
            "Package initialiser: re-exports all public symbols from the 9 "
            "sub-modules and provides package_summary, get_chapter_overview, "
            "and verify_imports convenience helpers."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.0",
        export_count=75,
        line_count=480,
        exports=[
            "package_summary",
            "get_chapter_overview",
            "verify_imports",
            "__version__",
            "__author__",
            "__theory_chapter__",
        ],
        dependencies=[
            "manifest",
            "models",
            "project_sites",
            "module_covers",
            "fleet_structure",
            "hypercover_refinement",
            "algorithms",
            "integration",
            "theorems",
        ],
    ),
    ModuleDescription(
        name="manifest",
        path="manifest.py",
        description=(
            "Package manifest: metadata, integrity checking, theory section "
            "coverage, and dependency tracking for the project_hypercovers "
            "package."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.0",
        export_count=14,
        line_count=700,
        exports=[
            "PackageManifest",
            "ModuleDescription",
            "ModuleStatus",
            "ExportKind",
            "DependencyKind",
            "SectionStatus",
            "TheorySection",
            "DependencyRecord",
            "MODULE_REGISTRY",
            "get_manifest",
            "list_exports",
            "validate_package_integrity",
            "PACKAGE_NAME",
            "VERSION",
            "AUTHOR",
            "CHAPTER",
            "SECTION_START",
            "SECTION_END",
        ],
        dependencies=["dataclasses", "enum", "time", "pathlib", "hashlib", "json", "os"],
    ),
    ModuleDescription(
        name="models",
        path="models.py",
        description=(
            "Core domain models for project sites, module covers, fleet "
            "members, and hypercover decompositions."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.1–§8.4",
        export_count=8,
        line_count=500,
        exports=[
            "ProjectSite",
            "ModuleCover",
            "FleetMember",
            "HypercoverDecomposition",
            "ProjectKind",
            "CoverStrategy",
            "FleetStatus",
            "DecompositionStatus",
        ],
        dependencies=[
            "jugeo.geometry.hypercovers",
            "jugeo.geometry.descent",
            "jugeo.geometry.site",
            "jugeo.geometry.covers",
            "jugeo.judgments.judgment_terms",
            "jugeo.evidence.certificates",
        ],
    ),
    ModuleDescription(
        name="project_sites",
        path="project_sites.py",
        description="§8.1 — Project site construction and inspection utilities.",
        status=ModuleStatus.STABLE,
        theory_section="§8.1",
        export_count=7,
        line_count=420,
        exports=[
            "SemanticSiteBuilder",
            "CoordinateRegistry",
            "TopologyGenerator",
            "ProjectSiteInspector",
            "build_project_site",
            "compute_site_morphisms",
            "site_from_modules",
        ],
        dependencies=["models", "jugeo.geometry.site"],
    ),
    ModuleDescription(
        name="module_covers",
        path="module_covers.py",
        description=(
            "§8.2 — Module cover construction, refinement, and Čech nerve "
            "computation."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.2",
        export_count=8,
        line_count=450,
        exports=[
            "CoverBuilder",
            "OverlapComputer",
            "AdmissibilityChecker",
            "CoverRefiner",
            "CechNerveComputer",
            "build_module_cover",
            "refine_cover_until_admissible",
            "score_cover_quality",
        ],
        dependencies=["models", "jugeo.geometry.hypercovers", "jugeo.geometry.covers"],
    ),
    ModuleDescription(
        name="fleet_structure",
        path="fleet_structure.py",
        description=(
            "§8.3 — Fleet coordination, load balancing, and trust aggregation."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.3",
        export_count=8,
        line_count=440,
        exports=[
            "FleetCoordinator",
            "LoadBalancer",
            "TrustAggregator",
            "FleetMonitor",
            "FleetPlanner",
            "assemble_fleet",
            "assign_fleet_to_cover",
            "compute_fleet_trust",
        ],
        dependencies=["models", "jugeo.judgments.judgment_terms"],
    ),
    ModuleDescription(
        name="hypercover_refinement",
        path="hypercover_refinement.py",
        description=(
            "§8.4 — Hypercover building, refinement, descent orchestration, "
            "and obstruction analysis."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.4",
        export_count=8,
        line_count=500,
        exports=[
            "HypercoverBuilder",
            "SimplicialStructureValidator",
            "RefinementEngine",
            "ObstructionAnalyzer",
            "DescentCoordinator",
            "build_hypercover",
            "refine_hypercover",
            "compute_descent_obstruction",
        ],
        dependencies=["models", "jugeo.geometry.hypercovers", "jugeo.geometry.descent"],
    ),
    ModuleDescription(
        name="algorithms",
        path="algorithms.py",
        description=(
            "Cross-cutting algorithmic routines for cover, fleet, and descent "
            "computation."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.2–§8.4",
        export_count=7,
        line_count=380,
        exports=[
            "greedy_cover_algorithm",
            "optimal_fleet_assignment",
            "hypercover_descent_algorithm",
            "cech_complex_computation",
            "obstruction_repair_algorithm",
            "iterative_refinement_loop",
            "trust_propagation_algorithm",
        ],
        dependencies=[
            "models",
            "module_covers",
            "fleet_structure",
            "hypercover_refinement",
        ],
    ),
    ModuleDescription(
        name="integration",
        path="integration.py",
        description=(
            "Integration glue connecting project hypercovers to judgment and "
            "evidence sub-systems."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.0",
        export_count=5,
        line_count=360,
        exports=[
            "ProjectHypercoverIntegration",
            "ProjectHypercoverExporter",
            "ProjectHypercoverImporter",
            "register_project_site",
            "connect_fleet_to_judgment_system",
        ],
        dependencies=[
            "models",
            "jugeo.judgments.judgment_terms",
            "jugeo.evidence.certificates",
        ],
    ),
    ModuleDescription(
        name="theorems",
        path="theorems.py",
        description=(
            "Formal theorem records, proof steps, and verification status for "
            "all Ch8 results."
        ),
        status=ModuleStatus.STABLE,
        theory_section="§8.1–§8.4",
        export_count=9,
        line_count=400,
        exports=[
            "TheoremRecord",
            "TheoremRegistry",
            "ProofVerifier",
            "theorem_hypercover_descent",
            "theorem_fleet_coverage",
            "theorem_module_decomposition",
            "theorem_cech_nerve_contractible",
            "VerificationStatus",
            "ProofStep",
        ],
        dependencies=["models"],
    ),
]


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Construct and return the complete ``PackageManifest`` for this package.

    The manifest is built from the module-level constants and data structures
    defined in this file.  It is re-constructed on each call (no global
    mutable cache) so that the timestamp reflects the time of the call.

    Returns
    -------
    PackageManifest
        Fully populated manifest instance with all modules, theory sections,
        dependency records, and export lists.

    Examples
    --------
    >>> m = get_manifest()
    >>> m.name
    'jugeo.foundations.project_hypercovers'
    >>> m.chapter
    8
    >>> m.count_modules() >= 9
    True
    """
    all_exports = list_exports()
    all_dep_targets = [dr.target for dr in DEPENDENCY_RECORDS]
    unique_deps = sorted(set(all_dep_targets))

    return PackageManifest(
        name=PACKAGE_NAME,
        version=VERSION,
        chapter=CHAPTER,
        section_range=(SECTION_START, SECTION_END),
        theory_file=THEORY_FILE,
        exports=all_exports,
        dependencies=unique_deps,
        modules=MODULE_DESCRIPTIONS,
        theory_sections=THEORY_SECTIONS,
        dependency_records=DEPENDENCY_RECORDS,
    )


def list_exports() -> list[str]:
    """Return the master list of all public symbol names exported by this package.

    The list is derived by aggregating the ``exports`` field of every
    :class:`ModuleDescription` in :data:`MODULE_DESCRIPTIONS`, then
    adding the well-known additional symbols declared in ``__init__.py``.
    Duplicates are removed and the result is sorted alphabetically.

    Returns
    -------
    list[str]
        Sorted, deduplicated list of all export names.

    Examples
    --------
    >>> exports = list_exports()
    >>> "PackageManifest" in exports
    True
    >>> "ProjectSite" in exports
    True
    >>> len(exports) >= 50
    True
    """
    seen: set[str] = set()
    result: list[str] = []

    for mod in MODULE_DESCRIPTIONS:
        for exp in mod.exports:
            if exp not in seen:
                seen.add(exp)
                result.append(exp)

    extras = [
        # __init__.py package-level vars
        "__version__",
        "__author__",
        "__theory_chapter__",
        "package_summary",
        "get_chapter_overview",
        "verify_imports",
        # manifest
        "ModuleDescription",
        "ModuleStatus",
        "ExportKind",
        "DependencyKind",
        "SectionStatus",
        "TheorySection",
        "DependencyRecord",
        "MODULE_REGISTRY",
        "get_manifest",
        "list_exports",
        "validate_package_integrity",
        "PACKAGE_NAME",
        "VERSION",
        "AUTHOR",
        "CHAPTER",
        "SECTION_START",
        "SECTION_END",
        # models
        "ProjectSite",
        "ModuleCover",
        "FleetMember",
        "HypercoverDecomposition",
        "ProjectKind",
        "CoverStrategy",
        "FleetStatus",
        "DecompositionStatus",
        # s01
        "SemanticSiteBuilder",
        "CoordinateRegistry",
        "TopologyGenerator",
        "ProjectSiteInspector",
        "build_project_site",
        "compute_site_morphisms",
        "site_from_modules",
        # s02
        "CoverBuilder",
        "OverlapComputer",
        "AdmissibilityChecker",
        "CoverRefiner",
        "CechNerveComputer",
        "build_module_cover",
        "refine_cover_until_admissible",
        "score_cover_quality",
        # s03
        "FleetCoordinator",
        "LoadBalancer",
        "TrustAggregator",
        "FleetMonitor",
        "FleetPlanner",
        "assemble_fleet",
        "assign_fleet_to_cover",
        "compute_fleet_trust",
        # s04
        "HypercoverBuilder",
        "SimplicialStructureValidator",
        "RefinementEngine",
        "ObstructionAnalyzer",
        "DescentCoordinator",
        "build_hypercover",
        "refine_hypercover",
        "compute_descent_obstruction",
        # algorithms
        "greedy_cover_algorithm",
        "optimal_fleet_assignment",
        "hypercover_descent_algorithm",
        "cech_complex_computation",
        "obstruction_repair_algorithm",
        "iterative_refinement_loop",
        "trust_propagation_algorithm",
        # integration
        "ProjectHypercoverIntegration",
        "ProjectHypercoverExporter",
        "ProjectHypercoverImporter",
        "register_project_site",
        "connect_fleet_to_judgment_system",
        # theorems
        "TheoremRecord",
        "TheoremRegistry",
        "ProofVerifier",
        "theorem_hypercover_descent",
        "theorem_fleet_coverage",
        "theorem_module_decomposition",
        "theorem_cech_nerve_contractible",
        "VerificationStatus",
        "ProofStep",
    ]
    for exp in extras:
        if exp not in seen:
            seen.add(exp)
            result.append(exp)

    return sorted(result)


def validate_package_integrity() -> dict[str, bool]:
    """Check that every described module file exists and has non-trivial content.

    For each :class:`ModuleDescription` in :data:`MODULE_DESCRIPTIONS`, this
    function checks that the corresponding ``.py`` file exists under
    :data:`PACKAGE_ROOT` and has a file size greater than zero bytes.

    Returns
    -------
    dict[str, bool]
        Mapping from ``"<module_name>_exists"`` to a boolean.  A value of
        ``True`` means the file is present and non-empty on disk.

    Examples
    --------
    >>> result = validate_package_integrity()
    >>> result["manifest_exists"]
    True
    >>> isinstance(result, dict)
    True
    """
    results: dict[str, bool] = {}
    for mod in MODULE_DESCRIPTIONS:
        file_path = PACKAGE_ROOT / mod.path
        key = f"{mod.name}_exists"
        try:
            results[key] = file_path.exists() and file_path.stat().st_size > 0
        except OSError:
            results[key] = False

    manifest_path = PACKAGE_ROOT / "manifest.py"
    results["manifest_self_check"] = (
        manifest_path.exists() and manifest_path.stat().st_size > 0
    )
    return results


def compute_dependency_graph() -> dict[str, list[str]]:
    """Return an adjacency dictionary of inter-module dependencies.

    Each key is a bare module name (e.g. ``"models"``); each value is the
    sorted list of module names or external package names that the key
    depends on, as recorded in :data:`MODULE_DESCRIPTIONS`.

    Returns
    -------
    dict[str, list[str]]
        Adjacency dictionary suitable for topological sorting or
        graph-based analysis.

    Examples
    --------
    >>> g = compute_dependency_graph()
    >>> "models" in g
    True
    >>> isinstance(g["models"], list)
    True
    >>> "jugeo.geometry.hypercovers" in g["models"]
    True
    """
    return {mod.name: sorted(mod.dependencies) for mod in MODULE_DESCRIPTIONS}


def get_theory_coverage() -> dict[str, SectionStatus]:
    """Return the implementation coverage status for each theory section.

    Returns
    -------
    dict[str, SectionStatus]
        Mapping from section_id (e.g. ``"8.1"``) to its
        :class:`SectionStatus` value.

    Examples
    --------
    >>> cov = get_theory_coverage()
    >>> cov["8.1"] == SectionStatus.COMPLETE
    True
    >>> set(cov.keys()) == {"8.1", "8.2", "8.3", "8.4"}
    True
    """
    return {sec.section_id: sec.status for sec in THEORY_SECTIONS}


def format_manifest_report() -> str:
    """Return a multi-line human-readable report of the package manifest.

    The report includes:

    * Package header (name, version, theory chapter, creation timestamp).
    * Per-module table (name, status, theory section reference, export count).
    * Theory section coverage table.
    * External dependency list.
    * SHA-256 integrity hash.

    Returns
    -------
    str
        Formatted multi-line report string suitable for printing to a
        terminal or saving to a log file.

    Examples
    --------
    >>> report = format_manifest_report()
    >>> "project_hypercovers" in report
    True
    >>> "§8.1" in report
    True
    >>> "SHA-256" in report
    True
    """
    m = get_manifest()
    lines: list[str] = []

    sep = "=" * 72
    lines.append(sep)
    lines.append(f"  PACKAGE MANIFEST — {m.name} v{m.version}")
    lines.append(sep)
    lines.append(
        f"  Theory  : {m.theory_file} Ch{m.chapter} — {CHAPTER_TITLE}"
    )
    lines.append(f"  Sections: §{m.section_range[0]}–§{m.section_range[1]}")
    lines.append(
        f"  Created : "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m.created_at))}"
    )
    lines.append("")

    lines.append("  MODULES")
    lines.append("  " + "-" * 70)
    lines.append(
        f"  {'Name':<36} {'Status':<14} {'Section':<10} {'Exports':>7}"
    )
    lines.append("  " + "-" * 70)
    for mod in sorted(m.modules, key=lambda x: x.name):
        lines.append(
            f"  {mod.name:<36} {mod.status.value:<14} "
            f"{mod.theory_section:<10} {mod.export_count:>7}"
        )
    lines.append("  " + "-" * 70)
    lines.append(
        f"  {'TOTAL':<36} {'':<14} {'':<10} {m.count_exports():>7}"
    )
    lines.append("")

    lines.append("  THEORY COVERAGE")
    lines.append("  " + "-" * 70)
    for sid, status in sorted(m.check_section_coverage().items()):
        sec = m.get_section(sid)
        title = sec.title if sec else "unknown"
        lines.append(f"  §{sid}  {title:<44} [{status.value}]")
    lines.append("")

    lines.append("  EXTERNAL DEPENDENCIES")
    lines.append("  " + "-" * 70)
    for dep in sorted(m.dependencies):
        lines.append(f"  • {dep}")
    lines.append("")

    lines.append(f"  Integrity SHA-256 : {m.compute_integrity_hash()}")
    lines.append(sep)

    return "\n".join(lines)


# copilot: shared-core manifest — exposes package metadata for LLM orchestration.
