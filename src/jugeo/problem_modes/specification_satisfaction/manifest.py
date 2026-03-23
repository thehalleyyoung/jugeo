"""Package manifest for the specification_satisfaction problem-mode sub-package.

This manifest module provides machine-readable metadata about the
``specification_satisfaction`` sub-package of jugeo's problem-modes layer.
It lists the modules contained in the package, the public symbols they
export, the theoretical chapter they implement, and integrity-checking
utilities that verify all modules can be imported at runtime.

The ``specification_satisfaction`` sub-package implements theory2.tex Ch10
("Specification Satisfaction via Sheaf Descent").  The key insight is that
a global specification -- a presheaf of judgments over a site -- is
*satisfied* when there exists a global section of that presheaf that agrees
with every locally prescribed judgment.  This global section is assembled
from partial witnesses via descent / gluing and is formally attested by a
:class:`~jugeo.problem_modes.specification_satisfaction.models.CertificateOfSatisfaction`.

copilot: shared-core module -- every public surface is designed for LLM
orchestration and Copilot-assisted verification workflows.

References
----------
theory2.tex §10.1   "Specifications as Presheaves of Judgments"
theory2.tex §10.2   "Witnesses and Partial Sections"
theory2.tex §10.3   "Certificates via Descent"
theory2.tex §10.4   "Residual Gaps and Obstruction Classes"
theory2.tex §10.5   "Package Structure and Module Responsibilities"
"""

from __future__ import annotations

import importlib
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Package-level constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: str = "specification_satisfaction"
"""Canonical name of this sub-package."""

VERSION: str = "0.1.0"
"""Semantic version of the specification_satisfaction sub-package."""

AUTHOR: str = "jugeo-core"
"""Author / maintainer identifier for the package."""

CHAPTER: str = "theory2.tex Ch10"
"""Chapter of the theoretical reference that this package implements."""

THEORY_REFERENCE: str = "theory2.tex §10"
"""Section-level reference in the theoretical document."""

PACKAGE_BASE: str = "jugeo.problem_modes.specification_satisfaction"
"""Fully-qualified Python package path."""

# ---------------------------------------------------------------------------
# Specification kinds registry
# ---------------------------------------------------------------------------

SPECIFICATION_KINDS: list[tuple[str, str]] = [
    (
        "structural",
        "Constraints on the topological or graph-theoretic structure of the site "
        "(e.g., connectivity, acyclicity, containment hierarchies).",
    ),
    (
        "behavioral",
        "Constraints on the dynamic behaviour of entities over time, such as "
        "state-machine invariants or temporal trace properties.",
    ),
    (
        "relational",
        "Constraints expressed as relations between pairs or tuples of coordinates, "
        "e.g., ordering, similarity, or mutual-exclusion requirements.",
    ),
    (
        "semantic",
        "Constraints on the meaning or interpretation of coordinate content, "
        "typically evaluated by language models or ontology reasoners.",
    ),
    (
        "composite",
        "A specification assembled from two or more sub-specifications of potentially "
        "different kinds, combined by conjunction or disjunction.",
    ),
    (
        "resource",
        "Constraints on resource usage (time, memory, API calls, tokens) at or "
        "between coordinates.",
    ),
]
"""Registry of known specification kinds.

Each entry is a ``(kind_name, description)`` tuple.  The ``kind_name`` values
correspond to the ``SpecificationKind`` enum variants defined in ``models.py``.
"""

# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

MODULE_REGISTRY: dict[str, str] = {
    "models": (
        "Core data-model classes: Specification, SatisfactionWitness, "
        "CertificateOfSatisfaction, ResidualGap, and their supporting enums."
    ),
    "manifest": (
        "Package metadata, module registry, integrity checks, and the "
        "PackageManifest / ModuleDescriptor dataclasses."
    ),
}
"""Mapping from module names (relative to this package) to their descriptions.

Keys are bare module names (without the package prefix); values are
human-readable description strings suitable for documentation or LLM prompts.
"""

# ---------------------------------------------------------------------------
# Exports registry
# ---------------------------------------------------------------------------

_MODELS_EXPORTS: tuple[str, ...] = (
    "SpecificationKind",
    "WitnessStatus",
    "GapSeverity",
    "SatisfactionStatus",
    "DescentCondition",
    "Specification",
    "SatisfactionWitness",
    "CertificateOfSatisfaction",
    "ResidualGap",
)

_MANIFEST_EXPORTS: tuple[str, ...] = (
    "PACKAGE_NAME",
    "VERSION",
    "AUTHOR",
    "CHAPTER",
    "THEORY_REFERENCE",
    "SPECIFICATION_KINDS",
    "MODULE_REGISTRY",
    "PackageManifest",
    "ModuleDescriptor",
    "get_manifest",
    "list_exports",
    "validate_package_integrity",
    "get_module_descriptor",
    "register_module",
)

_ALL_EXPORTS: tuple[str, ...] = _MODELS_EXPORTS + _MANIFEST_EXPORTS

# Internal mutable registry used by register_module()
_descriptor_registry: dict[str, ModuleDescriptor] = {}  # populated after class definition


def _now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Returns
    -------
    str
        Timestamp string like ``"2025-01-15T12:00:00Z"``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    """Metadata descriptor for a single module within this package.

    Parameters
    ----------
    module_name : str
        Bare module name (without the package prefix), e.g., ``"models"``.
    file_path : str
        Filesystem path relative to the package root, e.g.,
        ``"specification_satisfaction/models.py"``.
    description : str
        Human-readable description of the module's responsibilities.
    exports : tuple[str, ...]
        Tuple of public symbol names declared in ``__all__`` for this module.
    theory_sections : tuple[str, ...]
        Tuple of theory2.tex section references covered by this module,
        e.g., ``("§10.1", "§10.2")``.
    """

    module_name: str
    file_path: str
    description: str
    exports: tuple[str, ...] = field(default_factory=tuple)
    theory_sections: tuple[str, ...] = field(default_factory=tuple)

    # -- derived properties -----------------------------------------------

    @property
    def fully_qualified_name(self) -> str:
        """Return the fully-qualified Python module path.

        Returns
        -------
        str
            e.g., ``"jugeo.problem_modes.specification_satisfaction.models"``.
        """
        return f"{PACKAGE_BASE}.{self.module_name}"

    @property
    def export_count(self) -> int:
        """Return the number of exported symbols.

        Returns
        -------
        int
            Length of :attr:`exports`.
        """
        return len(self.exports)

    # -- validation & checks ----------------------------------------------

    def is_importable(self) -> bool:
        """Check whether the module can currently be imported.

        Returns
        -------
        bool
            ``True`` if ``importlib.import_module`` succeeds for this module.
        """
        try:
            importlib.import_module(self.fully_qualified_name)
            return True
        except ImportError:
            return False

    def get_import_error(self) -> str | None:
        """Return the import error message if the module cannot be imported.

        Returns
        -------
        str or None
            The error message string, or ``None`` if the module imports cleanly.
        """
        try:
            importlib.import_module(self.fully_qualified_name)
            return None
        except ImportError as exc:
            return str(exc)

    def check_exports_present(self) -> list[str]:
        """Return a list of declared exports that are missing from the module.

        Attempts to import the module and checks that each name in
        :attr:`exports` is a valid attribute.

        Returns
        -------
        list[str]
            Names declared in :attr:`exports` that are absent from the module,
            or all names if the module cannot be imported.
        """
        try:
            mod = importlib.import_module(self.fully_qualified_name)
        except ImportError:
            return list(self.exports)
        missing: list[str] = []
        for name in self.exports:
            if not hasattr(mod, name):
                missing.append(name)
        return missing

    # -- serialization / export -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary suitable for ``json.dumps``.
        """
        return {
            "module_name": self.module_name,
            "file_path": self.file_path,
            "description": self.description,
            "exports": list(self.exports),
            "theory_sections": list(self.theory_sections),
            "fully_qualified_name": self.fully_qualified_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleDescriptor:
        """Construct a :class:`ModuleDescriptor` from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ModuleDescriptor
            Reconstructed instance.
        """
        return cls(
            module_name=data["module_name"],
            file_path=data.get("file_path", ""),
            description=data.get("description", ""),
            exports=tuple(data.get("exports", [])),
            theory_sections=tuple(data.get("theory_sections", [])),
        )


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Immutable manifest describing the ``specification_satisfaction`` package.

    The manifest aggregates all machine-readable metadata about the package
    in a single, serialisable object.  It is the authoritative source of
    truth for package structure queries by LLM orchestration and CI tooling.

    Parameters
    ----------
    package_name : str
        Canonical name of the package.
    version : str
        Semantic version string.
    author : str
        Author / maintainer identifier.
    chapter : str
        Theory reference chapter (e.g., ``"theory2.tex Ch10"``).
    theory_reference : str
        Fine-grained theory reference (e.g., ``"theory2.tex §10"``).
    modules : tuple[str, ...]
        Names of all modules included in the package.
    exports : tuple[str, ...]
        All public symbols exported by the package.
    specification_kinds : tuple[str, ...]
        Names of all known specification kinds.
    description : str
        Human-readable description of the package's purpose.
    created_at : str
        ISO-8601 timestamp when this manifest was generated.
    is_complete : bool
        Whether all declared modules have been implemented.
    """

    package_name: str
    version: str
    author: str
    chapter: str
    theory_reference: str
    modules: tuple[str, ...]
    exports: tuple[str, ...]
    specification_kinds: tuple[str, ...]
    description: str
    created_at: str
    is_complete: bool

    # -- derived properties -----------------------------------------------

    @property
    def fully_qualified_package(self) -> str:
        """Return the fully-qualified Python package path.

        Returns
        -------
        str
            e.g., ``"jugeo.problem_modes.specification_satisfaction"``.
        """
        return PACKAGE_BASE

    @property
    def kind_count(self) -> int:
        """Return the number of registered specification kinds.

        Returns
        -------
        int
            Length of :attr:`specification_kinds`.
        """
        return len(self.specification_kinds)

    # -- query methods ----------------------------------------------------

    def module_count(self) -> int:
        """Return the number of modules in this package.

        Returns
        -------
        int
            Length of :attr:`modules`.
        """
        return len(self.modules)

    def export_count(self) -> int:
        """Return the total number of exported symbols.

        Returns
        -------
        int
            Length of :attr:`exports`.
        """
        return len(self.exports)

    def summary(self) -> str:
        """Return a one-line human-readable summary of the package.

        Returns
        -------
        str
            A concise summary string.
        """
        status = "complete" if self.is_complete else "in-progress"
        return (
            f"{self.package_name} v{self.version} ({status}) -- "
            f"{self.module_count()} modules, {self.export_count()} exports, "
            f"ref: {self.theory_reference}"
        )

    def get_module_description(self, module_name: str) -> str:
        """Return the description for *module_name* from :data:`MODULE_REGISTRY`.

        Parameters
        ----------
        module_name : str
            Bare module name (without package prefix).

        Returns
        -------
        str
            The description string, or a ``"(no description available)"``
            placeholder if the module is not in the registry.
        """
        return MODULE_REGISTRY.get(module_name, "(no description available)")

    # -- validation -------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the completeness and consistency of the manifest.

        Checks include:

        - ``package_name`` must be non-empty.
        - ``version`` must be non-empty.
        - ``modules`` must be non-empty.
        - ``exports`` must be non-empty.
        - ``specification_kinds`` must be non-empty.
        - Each module in :attr:`modules` must appear in :data:`MODULE_REGISTRY`.

        Returns
        -------
        list[str]
            List of human-readable error strings; empty if the manifest is valid.
        """
        errors: list[str] = []
        if not self.package_name:
            errors.append("package_name must be non-empty.")
        if not self.version:
            errors.append("version must be non-empty.")
        if not self.modules:
            errors.append("modules must be non-empty.")
        if not self.exports:
            errors.append("exports must be non-empty.")
        if not self.specification_kinds:
            errors.append("specification_kinds must be non-empty.")
        for mod in self.modules:
            if mod not in MODULE_REGISTRY:
                errors.append(
                    f"Module {mod!r} is declared in the manifest but not in "
                    f"MODULE_REGISTRY."
                )
        return errors

    # -- serialization / export -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary suitable for ``json.dumps``.
        """
        return {
            "package_name": self.package_name,
            "version": self.version,
            "author": self.author,
            "chapter": self.chapter,
            "theory_reference": self.theory_reference,
            "modules": list(self.modules),
            "exports": list(self.exports),
            "specification_kinds": list(self.specification_kinds),
            "description": self.description,
            "created_at": self.created_at,
            "is_complete": self.is_complete,
            "fully_qualified_package": self.fully_qualified_package,
            "module_count": self.module_count(),
            "export_count": self.export_count(),
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Construct a :class:`PackageManifest` from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PackageManifest
            Reconstructed instance.
        """
        return cls(
            package_name=data.get("package_name", PACKAGE_NAME),
            version=data.get("version", VERSION),
            author=data.get("author", AUTHOR),
            chapter=data.get("chapter", CHAPTER),
            theory_reference=data.get("theory_reference", THEORY_REFERENCE),
            modules=tuple(data.get("modules", [])),
            exports=tuple(data.get("exports", [])),
            specification_kinds=tuple(data.get("specification_kinds", [])),
            description=data.get("description", ""),
            created_at=data.get("created_at", _now_iso()),
            is_complete=bool(data.get("is_complete", False)),
        )


# ---------------------------------------------------------------------------
# Populate descriptor registry after class is defined
# ---------------------------------------------------------------------------

_descriptor_registry["models"] = ModuleDescriptor(
    module_name="models",
    file_path="specification_satisfaction/models.py",
    description=MODULE_REGISTRY["models"],
    exports=_MODELS_EXPORTS,
    theory_sections=("§10.1", "§10.2", "§10.3", "§10.4"),
)

_descriptor_registry["manifest"] = ModuleDescriptor(
    module_name="manifest",
    file_path="specification_satisfaction/manifest.py",
    description=MODULE_REGISTRY["manifest"],
    exports=_MANIFEST_EXPORTS,
    theory_sections=("§10.5",),
)


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Return the canonical :class:`PackageManifest` for this package.

    The manifest is freshly constructed on each call to ensure the
    ``created_at`` timestamp reflects the current time.

    Returns
    -------
    PackageManifest
        The package manifest with all metadata populated.

    Examples
    --------
    >>> m = get_manifest()
    >>> m.package_name
    'specification_satisfaction'
    >>> m.version
    '0.1.0'
    """
    kind_names = tuple(k for k, _ in SPECIFICATION_KINDS)
    return PackageManifest(
        package_name=PACKAGE_NAME,
        version=VERSION,
        author=AUTHOR,
        chapter=CHAPTER,
        theory_reference=THEORY_REFERENCE,
        modules=tuple(sorted(MODULE_REGISTRY.keys())),
        exports=_ALL_EXPORTS,
        specification_kinds=kind_names,
        description=(
            "Implements specification satisfaction via sheaf descent: "
            "specifications are presheaves of judgments, witnesses assemble "
            "local evidence into global sections, and certificates formally "
            "attest satisfaction (theory2.tex Ch10)."
        ),
        created_at=_now_iso(),
        is_complete=len(MODULE_REGISTRY) >= 2,
    )


def list_exports() -> list[str]:
    """Return a sorted list of all public symbols exported by this package.

    Returns
    -------
    list[str]
        Alphabetically sorted list of export names.

    Examples
    --------
    >>> exports = list_exports()
    >>> "Specification" in exports
    True
    """
    return sorted(_ALL_EXPORTS)


def validate_package_integrity() -> dict[str, Any]:
    """Check that all declared modules can be imported and their exports resolved.

    For each module in :data:`MODULE_REGISTRY`, this function:

    1. Attempts to import the module.
    2. Verifies that all declared exports are present as attributes.
    3. Collects any import errors or missing exports.

    Returns
    -------
    dict[str, Any]
        A report dictionary with the following keys:

        ``"valid"``
            ``True`` if all modules imported cleanly with all exports present.
        ``"module_results"``
            A mapping from module name to a result dict containing
            ``"importable"`` (bool), ``"import_error"`` (str or None), and
            ``"missing_exports"`` (list[str]).
        ``"total_modules"``
            Total number of modules checked.
        ``"importable_count"``
            Number of modules that imported without error.
        ``"missing_export_count"``
            Total number of missing exports across all modules.
        ``"checked_at"``
            ISO-8601 timestamp of the check.

    Examples
    --------
    >>> report = validate_package_integrity()
    >>> isinstance(report["valid"], bool)
    True
    """
    module_results: dict[str, dict[str, Any]] = {}
    total_importable = 0
    total_missing_exports = 0

    for mod_name, descriptor in _descriptor_registry.items():
        importable = descriptor.is_importable()
        import_error = descriptor.get_import_error()
        missing_exports = descriptor.check_exports_present()

        module_results[mod_name] = {
            "importable": importable,
            "import_error": import_error,
            "missing_exports": missing_exports,
            "export_count": descriptor.export_count,
        }

        if importable:
            total_importable += 1
        total_missing_exports += len(missing_exports)

    all_valid = (
        total_importable == len(_descriptor_registry)
        and total_missing_exports == 0
    )

    return {
        "valid": all_valid,
        "module_results": module_results,
        "total_modules": len(_descriptor_registry),
        "importable_count": total_importable,
        "missing_export_count": total_missing_exports,
        "checked_at": _now_iso(),
    }


def get_module_descriptor(name: str) -> ModuleDescriptor | None:
    """Return the :class:`ModuleDescriptor` for the module named *name*.

    Parameters
    ----------
    name : str
        Bare module name (without package prefix), e.g., ``"models"``.

    Returns
    -------
    ModuleDescriptor or None
        The descriptor, or ``None`` if *name* is not registered.

    Examples
    --------
    >>> d = get_module_descriptor("models")
    >>> d is not None
    True
    >>> d.module_name
    'models'
    """
    return _descriptor_registry.get(name)


def register_module(descriptor: ModuleDescriptor) -> None:
    """Register a :class:`ModuleDescriptor` in the runtime descriptor registry.

    If a descriptor for :attr:`~ModuleDescriptor.module_name` already exists,
    it is replaced.  The function also updates :data:`MODULE_REGISTRY` with
    the descriptor's description so that :meth:`PackageManifest.get_module_description`
    returns up-to-date information.

    Parameters
    ----------
    descriptor : ModuleDescriptor
        The descriptor to register.

    Raises
    ------
    ValueError
        If ``descriptor.module_name`` is empty.

    Examples
    --------
    >>> d = ModuleDescriptor(
    ...     module_name="extra",
    ...     file_path="specification_satisfaction/extra.py",
    ...     description="Extra utilities.",
    ...     exports=("ExtraHelper",),
    ...     theory_sections=("§10.6",),
    ... )
    >>> register_module(d)
    >>> get_module_descriptor("extra") is not None
    True
    """
    if not descriptor.module_name:
        raise ValueError("descriptor.module_name must be non-empty.")
    _descriptor_registry[descriptor.module_name] = descriptor
    MODULE_REGISTRY[descriptor.module_name] = descriptor.description


def describe_specification_kinds() -> list[dict[str, str]]:
    """Return a list of all specification kinds with their descriptions.

    Returns
    -------
    list[dict[str, str]]
        Each entry is a dict with ``"kind"`` and ``"description"`` keys,
        matching the entries in :data:`SPECIFICATION_KINDS`.

    Examples
    --------
    >>> kinds = describe_specification_kinds()
    >>> kinds[0]["kind"]
    'structural'
    """
    return [{"kind": k, "description": d} for k, d in SPECIFICATION_KINDS]


def module_theory_coverage() -> dict[str, tuple[str, ...]]:
    """Return a mapping from module name to covered theory sections.

    Returns
    -------
    dict[str, tuple[str, ...]]
        Keys are bare module names; values are tuples of theory section strings.

    Examples
    --------
    >>> coverage = module_theory_coverage()
    >>> "§10.1" in coverage.get("models", ())
    True
    """
    return {
        name: descriptor.theory_sections
        for name, descriptor in _descriptor_registry.items()
    }


def package_info() -> dict[str, Any]:
    """Return a concise info dictionary for quick introspection.

    This is a lightweight alternative to constructing a full
    :class:`PackageManifest`.  It is suitable for logging, health-check
    endpoints, and LLM context summaries.

    Returns
    -------
    dict[str, Any]
        Dictionary with keys ``"package"``, ``"version"``, ``"author"``,
        ``"chapter"``, ``"module_count"``, ``"export_count"``, and
        ``"theory_reference"``.

    Examples
    --------
    >>> info = package_info()
    >>> info["package"]
    'specification_satisfaction'
    """
    return {
        "package": PACKAGE_NAME,
        "version": VERSION,
        "author": AUTHOR,
        "chapter": CHAPTER,
        "theory_reference": THEORY_REFERENCE,
        "module_count": len(MODULE_REGISTRY),
        "export_count": len(_ALL_EXPORTS),
        "specification_kind_count": len(SPECIFICATION_KINDS),
        "modules": list(sorted(MODULE_REGISTRY.keys())),
    }


__all__ = [
    "PACKAGE_NAME",
    "VERSION",
    "AUTHOR",
    "CHAPTER",
    "THEORY_REFERENCE",
    "SPECIFICATION_KINDS",
    "MODULE_REGISTRY",
    "PackageManifest",
    "ModuleDescriptor",
    "get_manifest",
    "list_exports",
    "validate_package_integrity",
    "get_module_descriptor",
    "register_module",
    "describe_specification_kinds",
    "module_theory_coverage",
    "package_info",
]
# copilot: shared-core marker -- indicates LLM orchestration readiness.
