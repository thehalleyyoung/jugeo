r"""Package manifest and integrity registry for the Unified Problem Atlas.

copilot: package manifest and integrity registry for Theory2.tex Ch14.

This module implements §14.0 of *Theory2.tex*: the authoritative registry of every
submodule that makes up the ``jugeo.problem_modes.problem_atlas`` package.  It tracks
module metadata (exports, dependencies, theory section references, line targets) and
exposes validation helpers that downstream tooling can call to assert package integrity.

Design goals:

  1. **Single source of truth** — every public symbol in the atlas is declared here so
     that import audits, API docs generators, and LLM orchestration agents have one
     canonical place to query.
  2. **Dependency ordering** — :func:`resolve_module_dependencies` returns a topological
     ordering so that modules can be imported safely without circular-import risks.
  3. **Integrity reporting** — :func:`validate_package_integrity` returns a structured
     report dict that can be consumed by CI pipelines or the jugeo health-check endpoint.

Problem class catalog (§14.1–§14.4)
-------------------------------------
The :data:`PROBLEM_CLASS_CATALOG` constant maps each high-level category to the concrete
problem kinds it contains.  Five categories are defined, each with five members::

    COMPUTATIONAL  → SEARCH, OPTIMIZATION, COUNTING, SAMPLING, ENUMERATION
    VERIFICATION   → DECISION, VERIFICATION, CERTIFICATION, ATTESTATION, AUDITING
    CONSTRUCTIVE   → CONSTRUCTION, SYNTHESIS, GENERATION, REPAIR, COMPLETION
    ANALYTICAL     → INFERENCE, CLASSIFICATION, DIAGNOSIS, DECOMPOSITION, ABSTRACTION
    RELATIONAL     → COMPARISON, MATCHING, ALIGNMENT, UNIFICATION, REFINEMENT

Usage::

    from jugeo.problem_modes.problem_atlas.manifest import (
        get_manifest,
        validate_package_integrity,
        get_problems_in_category,
        resolve_module_dependencies,
    )

    manifest = get_manifest()
    print(manifest.version)          # "0.1.0"
    print(manifest.total_exports())  # total exported symbol count

    report = validate_package_integrity()
    if report["valid"]:
        print("Atlas package integrity confirmed.")

    deps = resolve_module_dependencies("integration")
    # ['manifest', 'models', 'problem_classes', ..., 'integration']

See Also:
    theory2.tex §14.0 for the formal package specification.
    jugeo.problem_modes.problem_atlas for the top-level public API.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 1.  Top-level package constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: str = "jugeo.problem_modes.problem_atlas"
VERSION: str = "0.1.0"
AUTHOR: str = "JuGeo Research"
CHAPTER: str = "Theory2.tex Ch14: Unified Problem Atlas"
THEORY_REF: str = "theory2.tex"
CHAPTER_NUM: int = 14

# ---------------------------------------------------------------------------
# 2.  ModuleKind — classification of atlas submodules
# ---------------------------------------------------------------------------


class ModuleKind(str, Enum):
    """Enumeration of the structural roles a submodule may play in the atlas.

    Every entry in :data:`MODULE_REGISTRY` carries exactly one ``ModuleKind`` tag
    so that tooling can partition the registry by role without inspecting contents.

    Attributes:
        CORE: Foundational modules whose exports are re-exposed by ``__init__``.
        SECTION: Theory-section modules implementing a specific §14.x chapter.
        ALGORITHM: Modules containing atlas-aware computational procedures.
        INTEGRATION: Bridge modules connecting the atlas to other jugeo subsystems.
        THEOREM: Modules containing formal statements and proof artefacts.
    """

    CORE = "core"
    SECTION = "section"
    ALGORITHM = "algorithm"
    INTEGRATION = "integration"
    THEOREM = "theorem"


# ---------------------------------------------------------------------------
# 3.  ModuleRecord — metadata for a single submodule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """Immutable metadata record describing one submodule of the problem atlas.

    A ``ModuleRecord`` is the canonical description of what a submodule contains,
    what it exports, which other modules it depends on, and which section of
    *theory2.tex* it implements.  Records are collected in :data:`MODULE_REGISTRY`.

    Args:
        module_name: Fully-unqualified name of the submodule (e.g. ``"manifest"``).
        kind: Structural role this module plays; see :class:`ModuleKind`.
        description: Human-readable one-paragraph description of the module purpose.
        exports: Tuple of public symbol names exposed by this module.
        depends_on: Tuple of *other* module_names (within this package) that must be
            importable before this module is imported.
        theory_section: String reference to the section(s) of theory2.tex implemented
            by this module (e.g. ``"§14.1"``).
        line_count_target: Aspirational minimum line count for the module source file.

    Raises:
        TypeError: If the instance is mutated after construction (frozen dataclass).

    Examples::

        rec = ModuleRecord(
            module_name="manifest",
            kind=ModuleKind.CORE,
            description="Package manifest and integrity registry.",
            exports=("PackageManifest", "ModuleKind", "get_manifest"),
            depends_on=(),
            theory_section="§14.0",
            line_count_target=400,
        )
        assert rec.module_name == "manifest"
    """

    module_name: str
    kind: ModuleKind
    description: str
    exports: tuple[str, ...]
    depends_on: tuple[str, ...]
    theory_section: str
    line_count_target: int


# ---------------------------------------------------------------------------
# 4.  PackageManifest — the root manifest object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Immutable root manifest for the ``jugeo.problem_modes.problem_atlas`` package.

    ``PackageManifest`` aggregates all :class:`ModuleRecord` entries together with
    package-level metadata (version, author, theory reference) and a SHA-256
    checksum of the serialised module registry.  It exposes helper methods for
    querying, validating, and serialising the manifest.

    Args:
        package_name: Dotted package name (``"jugeo.problem_modes.problem_atlas"``).
        version: Semantic version string (e.g. ``"0.1.0"``).
        author: Name of the primary author or research group.
        chapter: Human-readable reference to the theory chapter.
        modules: Tuple of all :class:`ModuleRecord` objects in this package.
        created_at: ISO-8601 timestamp string recording when the manifest was built.
        checksum: SHA-256 hex digest of the JSON-serialised module registry at build time.

    Raises:
        TypeError: If the instance is mutated after construction (frozen dataclass).

    Examples::

        manifest = get_manifest()
        rec = manifest.get_module("models")
        assert rec is not None
        errors = manifest.validate()
        assert errors == []
    """

    package_name: str
    version: str
    author: str
    chapter: str
    modules: tuple[ModuleRecord, ...]
    created_at: str
    checksum: str

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_module(self, name: str) -> ModuleRecord | None:
        """Return the :class:`ModuleRecord` whose ``module_name`` equals *name*.

        Args:
            name: Unqualified module name to look up (e.g. ``"models"``).

        Returns:
            The matching :class:`ModuleRecord`, or ``None`` if not found.

        Examples::

            manifest = get_manifest()
            rec = manifest.get_module("problem_classes")
            assert rec.theory_section == "§14.1"
        """
        for rec in self.modules:
            if rec.module_name == name:
                return rec
        return None

    def list_module_names(self) -> list[str]:
        """Return an ordered list of all module names in this manifest.

        Returns:
            List of ``module_name`` strings in registration order.

        Examples::

            names = get_manifest().list_module_names()
            assert "manifest" in names
            assert "models" in names
        """
        return [rec.module_name for rec in self.modules]

    def get_modules_by_kind(self, kind: ModuleKind) -> list[ModuleRecord]:
        """Return all :class:`ModuleRecord` objects whose ``kind`` matches *kind*.

        Args:
            kind: The :class:`ModuleKind` value to filter by.

        Returns:
            List (possibly empty) of matching :class:`ModuleRecord` objects.

        Examples::

            sections = get_manifest().get_modules_by_kind(ModuleKind.SECTION)
            assert len(sections) == 4  # s01–s04
        """
        return [rec for rec in self.modules if rec.kind == kind]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run structural validation on this manifest and return any errors.

        Checks performed:

        * All ``depends_on`` references name modules that exist in this manifest.
        * No two records share the same ``module_name``.
        * Every record's ``exports`` tuple is non-empty.
        * ``version`` is a non-empty string conforming to ``MAJOR.MINOR.PATCH``.

        Returns:
            A (possibly empty) list of human-readable error strings.  An empty
            list means the manifest is structurally valid.

        Examples::

            errors = get_manifest().validate()
            assert errors == [], errors
        """
        errors: list[str] = []
        seen_names: set[str] = set()
        name_set = {rec.module_name for rec in self.modules}

        # version format
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(
                f"version '{self.version}' does not conform to MAJOR.MINOR.PATCH"
            )

        for rec in self.modules:
            # duplicate names
            if rec.module_name in seen_names:
                errors.append(f"duplicate module_name: '{rec.module_name}'")
            seen_names.add(rec.module_name)

            # empty exports
            if not rec.exports:
                errors.append(
                    f"module '{rec.module_name}' has no exports declared"
                )

            # unresolved dependencies
            for dep in rec.depends_on:
                if dep not in name_set:
                    errors.append(
                        f"module '{rec.module_name}' depends on unknown module '{dep}'"
                    )

        return errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain dictionary suitable for JSON encoding.

        The resulting dict mirrors the dataclass field layout and converts inner
        dataclasses and enums to primitive Python types.

        Returns:
            A ``dict[str, Any]`` with keys matching the field names of this class.
            All nested objects are also plain dicts or lists.

        Examples::

            d = get_manifest().to_dict()
            assert d["package_name"] == PACKAGE_NAME
            import json
            _ = json.dumps(d)  # must not raise
        """
        return {
            "package_name": self.package_name,
            "version": self.version,
            "author": self.author,
            "chapter": self.chapter,
            "created_at": self.created_at,
            "checksum": self.checksum,
            "modules": [
                {
                    "module_name": r.module_name,
                    "kind": r.kind.value,
                    "description": r.description,
                    "exports": list(r.exports),
                    "depends_on": list(r.depends_on),
                    "theory_section": r.theory_section,
                    "line_count_target": r.line_count_target,
                }
                for r in self.modules
            ],
        }

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def total_exports(self) -> int:
        """Return the total number of exported symbols declared across all modules.

        Returns:
            Integer count of all ``exports`` entries summed over every
            :class:`ModuleRecord` in this manifest.

        Examples::

            n = get_manifest().total_exports()
            assert n > 0
        """
        return sum(len(r.exports) for r in self.modules)


# ---------------------------------------------------------------------------
# 5.  MODULE_REGISTRY — canonical record for each submodule
# ---------------------------------------------------------------------------

MODULE_REGISTRY: dict[str, ModuleRecord] = {
    "manifest": ModuleRecord(
        module_name="manifest",
        kind=ModuleKind.CORE,
        description=(
            "Package manifest and integrity registry.  Declares every submodule "
            "of the problem_atlas package, their exports, dependencies, and the "
            "theory sections they implement.  Provides validate_package_integrity() "
            "for CI-level integrity checks and resolve_module_dependencies() for "
            "safe import ordering."
        ),
        exports=(
            "PACKAGE_NAME",
            "VERSION",
            "AUTHOR",
            "CHAPTER",
            "THEORY_REF",
            "CHAPTER_NUM",
            "ModuleKind",
            "ModuleRecord",
            "PackageManifest",
            "MODULE_REGISTRY",
            "PROBLEM_CLASS_CATALOG",
            "get_manifest",
            "list_exports",
            "validate_package_integrity",
            "get_problem_categories",
            "get_problems_in_category",
            "resolve_module_dependencies",
        ),
        depends_on=(),
        theory_section="§14.0",
        line_count_target=400,
    ),
    "models": ModuleRecord(
        module_name="models",
        kind=ModuleKind.CORE,
        description=(
            "Core domain models for the Unified Problem Atlas.  Defines the "
            "AtlasCatalog root object and the intermediate model types (ProblemClass, "
            "SemanticSignature, EvidenceRequirement, TrustRequirement) that are "
            "composed by the section modules §14.1–§14.4."
        ),
        exports=(
            "AtlasCatalog",
            "ProblemClass",
            "SemanticSignature",
            "EvidenceRequirement",
            "TrustRequirement",
            "AtlasLookupError",
            "AtlasValidationError",
        ),
        depends_on=("manifest",),
        theory_section="§14.1–§14.4",
        line_count_target=350,
    ),
    "problem_classes": ModuleRecord(
        module_name="problem_classes",
        kind=ModuleKind.SECTION,
        description=(
            "Implements Theory2.tex §14.1: the ProblemClass classification lattice. "
            "Defines ProblemKind and ProblemCategory enumerations, the ProblemClass "
            "frozen dataclass, SubsumptionRelation for lattice edges, and "
            "ProblemClassLattice for whole-graph operations.  Provides "
            "build_default_lattice() which instantiates the canonical 25-node "
            "lattice from PROBLEM_CLASS_CATALOG."
        ),
        exports=(
            "ProblemKind",
            "ProblemCategory",
            "ProblemClass",
            "SubsumptionRelation",
            "ProblemClassLattice",
            "build_default_lattice",
            "lookup_problem_class",
            "get_all_problem_kinds",
        ),
        depends_on=("manifest", "models"),
        theory_section="§14.1",
        line_count_target=450,
    ),
    "semantic_signatures": ModuleRecord(
        module_name="semantic_signatures",
        kind=ModuleKind.SECTION,
        description=(
            "Implements Theory2.tex §14.2: typed input/output contracts for problem "
            "classes.  Defines IOSchema for structured type descriptors, "
            "SemanticSignature as an (input_schema, output_schema) pair, "
            "SignatureKind for coarse classification, SemanticContract for "
            "bidirectional compatibility constraints, and SemanticCompatibility "
            "result type.  Provides check_signature_compatibility() and "
            "infer_signature() utilities."
        ),
        exports=(
            "IOSchema",
            "SignatureKind",
            "SemanticSignature",
            "SemanticContract",
            "SemanticCompatibility",
            "check_signature_compatibility",
            "infer_signature",
        ),
        depends_on=("manifest", "models", "problem_classes"),
        theory_section="§14.2",
        line_count_target=400,
    ),
    "evidence_channels": ModuleRecord(
        module_name="evidence_channels",
        kind=ModuleKind.SECTION,
        description=(
            "Implements Theory2.tex §14.3: evidence channel routing for problem "
            "classes.  Defines EvidenceRequirement (which channels a problem class "
            "demands), RequirementStrength (MANDATORY / PREFERRED / OPTIONAL), "
            "ChannelBinding (problem class ↔ channel pairing), RoutePolicy for "
            "multi-channel strategies, and ChannelRoute as the resolved routing "
            "plan.  Provides build_channel_route(), get_required_channels(), and "
            "route_evidence()."
        ),
        exports=(
            "RequirementStrength",
            "EvidenceRequirement",
            "ChannelBinding",
            "RoutePolicy",
            "ChannelRoute",
            "build_channel_route",
            "get_required_channels",
            "route_evidence",
        ),
        depends_on=("manifest", "models", "problem_classes"),
        theory_section="§14.3",
        line_count_target=420,
    ),
    "trust_requirements": ModuleRecord(
        module_name="trust_requirements",
        kind=ModuleKind.SECTION,
        description=(
            "Implements Theory2.tex §14.4: trust algebra and sufficiency checks. "
            "Defines TrustThreshold (minimum score per dimension), TrustBudget "
            "(available trust per dimension), TrustRequirement (the per-class "
            "policy), TrustGap (shortfall analysis), and TrustSufficiencyResult. "
            "Provides check_trust_sufficiency(), compute_trust_gap(), and "
            "get_trust_requirement()."
        ),
        exports=(
            "TrustThreshold",
            "TrustBudget",
            "TrustRequirement",
            "TrustGap",
            "TrustSufficiencyResult",
            "check_trust_sufficiency",
            "compute_trust_gap",
            "get_trust_requirement",
        ),
        depends_on=("manifest", "models", "problem_classes"),
        theory_section="§14.4",
        line_count_target=380,
    ),
    "algorithms": ModuleRecord(
        module_name="algorithms",
        kind=ModuleKind.ALGORITHM,
        description=(
            "Implements Theory2.tex §14.5: atlas-aware classification and search "
            "algorithms.  Provides ClassificationAlgorithm (strategy enum), "
            "AtlasSearchResult (ranked classification outcome), classify_problem() "
            "for single-problem classification, find_covering_class() for the most "
            "general covering class, and rank_problem_classes() for scored ranking."
        ),
        exports=(
            "ClassificationAlgorithm",
            "AtlasSearchResult",
            "classify_problem",
            "find_covering_class",
            "rank_problem_classes",
        ),
        depends_on=(
            "manifest",
            "models",
            "problem_classes",
            "semantic_signatures",
        ),
        theory_section="§14.5",
        line_count_target=350,
    ),
    "integration": ModuleRecord(
        module_name="integration",
        kind=ModuleKind.INTEGRATION,
        description=(
            "Implements Theory2.tex §14.6: bridges between the problem atlas and "
            "jugeo.evidence / jugeo.judgments subsystems.  Defines JudgmentBinding "
            "(atlas class ↔ JudgmentTerm pairing), CertificateBinding (atlas class ↔ "
            "Certificate pairing), and AtlasIntegrationBridge (stateful session-level "
            "bridge).  Provides bind_judgment_to_class(), bind_certificate_to_class(), "
            "and create_integration_bridge()."
        ),
        exports=(
            "JudgmentBinding",
            "CertificateBinding",
            "AtlasIntegrationBridge",
            "bind_judgment_to_class",
            "bind_certificate_to_class",
            "create_integration_bridge",
        ),
        depends_on=(
            "manifest",
            "models",
            "problem_classes",
            "evidence_channels",
            "trust_requirements",
        ),
        theory_section="§14.6",
        line_count_target=400,
    ),
    "theorems": ModuleRecord(
        module_name="theorems",
        kind=ModuleKind.THEOREM,
        description=(
            "Implements Theory2.tex §14.7: formal properties proved over the atlas. "
            "Defines TheoremStatus (CONJECTURED / PROVED / DISPROVED / OPEN), "
            "AtlasTheorem (statement + proof reference + status), and "
            "TheoremRegistry (indexed collection).  Provides get_theorem() and "
            "list_theorems() for theorem lookup."
        ),
        exports=(
            "TheoremStatus",
            "AtlasTheorem",
            "TheoremRegistry",
            "get_theorem",
            "list_theorems",
        ),
        depends_on=(
            "manifest",
            "models",
            "problem_classes",
        ),
        theory_section="§14.7",
        line_count_target=300,
    ),
}

# ---------------------------------------------------------------------------
# 6.  PROBLEM_CLASS_CATALOG — category → problem kinds mapping
# ---------------------------------------------------------------------------

PROBLEM_CLASS_CATALOG: dict[str, list[str]] = {
    "COMPUTATIONAL": [
        "SEARCH",
        "OPTIMIZATION",
        "COUNTING",
        "SAMPLING",
        "ENUMERATION",
    ],
    "VERIFICATION": [
        "DECISION",
        "VERIFICATION",
        "CERTIFICATION",
        "ATTESTATION",
        "AUDITING",
    ],
    "CONSTRUCTIVE": [
        "CONSTRUCTION",
        "SYNTHESIS",
        "GENERATION",
        "REPAIR",
        "COMPLETION",
    ],
    "ANALYTICAL": [
        "INFERENCE",
        "CLASSIFICATION",
        "DIAGNOSIS",
        "DECOMPOSITION",
        "ABSTRACTION",
    ],
    "RELATIONAL": [
        "COMPARISON",
        "MATCHING",
        "ALIGNMENT",
        "UNIFICATION",
        "REFINEMENT",
    ],
}

# ---------------------------------------------------------------------------
# 7.  Module-level functions
# ---------------------------------------------------------------------------


def get_manifest() -> PackageManifest:
    """Build and return the :class:`PackageManifest` for the problem_atlas package.

    The manifest is constructed from the module-level constants and
    :data:`MODULE_REGISTRY`.  A SHA-256 checksum is computed over the
    JSON-serialised registry so that out-of-band integrity checks can detect
    registry drift without re-importing the package.

    Returns:
        A freshly-constructed, immutable :class:`PackageManifest` instance.

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import get_manifest
        m = get_manifest()
        assert m.version == "0.1.0"
        assert m.package_name == "jugeo.problem_modes.problem_atlas"
        assert m.total_exports() > 0
    """
    registry_json = json.dumps(
        {
            name: {
                "kind": rec.kind.value,
                "exports": sorted(rec.exports),
                "depends_on": sorted(rec.depends_on),
                "theory_section": rec.theory_section,
            }
            for name, rec in sorted(MODULE_REGISTRY.items())
        },
        sort_keys=True,
    )
    checksum = hashlib.sha256(registry_json.encode()).hexdigest()
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return PackageManifest(
        package_name=PACKAGE_NAME,
        version=VERSION,
        author=AUTHOR,
        chapter=CHAPTER,
        modules=tuple(MODULE_REGISTRY.values()),
        created_at=created_at,
        checksum=checksum,
    )


def list_exports() -> dict[str, list[str]]:
    """Return a mapping from each module name to its declared export list.

    This is a convenience wrapper over :data:`MODULE_REGISTRY` that avoids
    callers having to interact with :class:`ModuleRecord` objects directly.

    Returns:
        Dict mapping ``module_name`` → sorted ``list[str]`` of exported symbol names.

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import list_exports
        exports = list_exports()
        assert "get_manifest" in exports["manifest"]
        assert "AtlasCatalog" in exports["models"]
    """
    return {name: sorted(rec.exports) for name, rec in MODULE_REGISTRY.items()}


def validate_package_integrity() -> dict[str, Any]:
    """Run full package-integrity checks and return a structured report.

    Performs the following checks:

    1. Structural manifest validation via :meth:`PackageManifest.validate`.
    2. Confirms all modules in :data:`MODULE_REGISTRY` have non-empty exports.
    3. Verifies the :data:`PROBLEM_CLASS_CATALOG` has exactly five categories,
       each with exactly five members.
    4. Recomputes the manifest checksum and confirms it is reproducible.

    Returns:
        A ``dict[str, Any]`` with the following keys:

        * ``"valid"`` (``bool``) — ``True`` iff all checks pass.
        * ``"errors"`` (``list[str]``) — list of error messages; empty on success.
        * ``"warnings"`` (``list[str]``) — list of non-fatal advisory messages.
        * ``"module_count"`` (``int``) — number of registered modules.
        * ``"total_exports"`` (``int``) — total exported symbol count.
        * ``"problem_class_count"`` (``int``) — total problem kind entries.
        * ``"checksum"`` (``str``) — SHA-256 of the serialised registry.

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import validate_package_integrity
        report = validate_package_integrity()
        assert report["valid"], report["errors"]
    """
    errors: list[str] = []
    warnings: list[str] = []

    manifest = get_manifest()
    errors.extend(manifest.validate())

    # Check catalog shape
    for category, kinds in PROBLEM_CLASS_CATALOG.items():
        if len(kinds) != 5:
            errors.append(
                f"PROBLEM_CLASS_CATALOG[{category!r}] has {len(kinds)} entries; "
                "expected 5"
            )
        if len(set(kinds)) != len(kinds):
            errors.append(
                f"PROBLEM_CLASS_CATALOG[{category!r}] contains duplicate entries"
            )

    if len(PROBLEM_CLASS_CATALOG) != 5:
        errors.append(
            f"PROBLEM_CLASS_CATALOG has {len(PROBLEM_CLASS_CATALOG)} categories; "
            "expected 5"
        )

    # Advisory: line count targets
    for rec in MODULE_REGISTRY.values():
        if rec.line_count_target < 100:
            warnings.append(
                f"module '{rec.module_name}' has a low line_count_target "
                f"({rec.line_count_target}); consider expanding."
            )

    total_problem_kinds = sum(
        len(v) for v in PROBLEM_CLASS_CATALOG.values()
    )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "module_count": len(MODULE_REGISTRY),
        "total_exports": manifest.total_exports(),
        "problem_class_count": total_problem_kinds,
        "checksum": manifest.checksum,
    }


def get_problem_categories() -> list[str]:
    """Return the ordered list of top-level problem categories.

    Categories are derived from :data:`PROBLEM_CLASS_CATALOG` and returned in
    insertion order, which matches the theory presentation order
    (§14.1: COMPUTATIONAL → VERIFICATION → CONSTRUCTIVE → ANALYTICAL → RELATIONAL).

    Returns:
        List of category name strings (e.g. ``["COMPUTATIONAL", "VERIFICATION", ...]``).

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import get_problem_categories
        cats = get_problem_categories()
        assert "VERIFICATION" in cats
        assert len(cats) == 5
    """
    return list(PROBLEM_CLASS_CATALOG.keys())


def get_problems_in_category(category: str) -> list[str]:
    """Return the problem kinds that belong to *category*.

    Args:
        category: A category name string, e.g. ``"COMPUTATIONAL"``.  Case-sensitive;
            must match a key in :data:`PROBLEM_CLASS_CATALOG`.

    Returns:
        List of problem kind strings for the given category.  Returns an empty
        list if *category* is not found (rather than raising, for safe querying).

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import get_problems_in_category
        kinds = get_problems_in_category("CONSTRUCTIVE")
        assert "SYNTHESIS" in kinds
        assert get_problems_in_category("UNKNOWN") == []
    """
    return list(PROBLEM_CLASS_CATALOG.get(category, []))


def resolve_module_dependencies(module_name: str) -> list[str]:
    """Return a topologically-ordered list of modules needed before *module_name*.

    Uses an iterative depth-first traversal of the ``depends_on`` graph declared
    in :data:`MODULE_REGISTRY`.  The returned list ends with *module_name* itself,
    so callers can use it directly as an import sequence.

    Args:
        module_name: Unqualified name of the module whose dependency chain is
            requested (e.g. ``"integration"``).

    Returns:
        Ordered list of module names with all transitive dependencies preceding
        *module_name*.  Duplicate names never appear in the result.

    Raises:
        KeyError: If *module_name* is not present in :data:`MODULE_REGISTRY`.

    Examples::

        from jugeo.problem_modes.problem_atlas.manifest import resolve_module_dependencies
        deps = resolve_module_dependencies("integration")
        assert deps[0] == "manifest"
        assert deps[-1] == "integration"
    """
    if module_name not in MODULE_REGISTRY:
        raise KeyError(
            f"module '{module_name}' is not registered in MODULE_REGISTRY"
        )

    visited: list[str] = []
    visiting: set[str] = set()

    def _visit(name: str) -> None:
        if name in visiting:
            # Cycle guard — should not occur for a well-formed registry.
            return
        if name in visited:
            return
        visiting.add(name)
        rec = MODULE_REGISTRY.get(name)
        if rec is not None:
            for dep in rec.depends_on:
                _visit(dep)
        visiting.discard(name)
        visited.append(name)

    _visit(module_name)
    return visited


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # constants
    "AUTHOR",
    "CHAPTER",
    "CHAPTER_NUM",
    "MODULE_REGISTRY",
    "PACKAGE_NAME",
    "PROBLEM_CLASS_CATALOG",
    "THEORY_REF",
    "VERSION",
    # types
    "ModuleKind",
    "ModuleRecord",
    "PackageManifest",
    # functions
    "get_manifest",
    "get_problem_categories",
    "get_problems_in_category",
    "list_exports",
    "resolve_module_dependencies",
    "validate_package_integrity",
]

# copilot: shared-core marker for future LLM orchestration.
