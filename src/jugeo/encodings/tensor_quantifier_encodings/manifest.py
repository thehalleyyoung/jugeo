"""
Package manifest for the tensor_quantifier_encodings subsystem.
================================================================
Chapter 30 of theory2.tex — "Exact Z3 encodings V: tensor extents, affine legality,
quantifier discipline, witness extraction".

This manifest declares all capabilities, dependencies, stable exports, and provenance
for the tensor_quantifier_encodings package within the JuGeo formal verification system.

copilot notes: The manifest is the authoritative declaration of what this package
provides and what it depends on. It is consumed by the JuGeo package registry and
by the copilot assist layer for capability discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Final, Mapping, Sequence

__all__ = [
    "SubsystemManifest",
    "TheoryProvenance",
    "CapabilityDeclaration",
    "DependencySpec",
    "ManifestValidator",
    "ManifestValidationError",
    "TENSOR_QUANTIFIER_MANIFEST",
    "CHAPTER_30_SECTIONS",
    "STABLE_EXPORTS",
    "PACKAGE_VERSION",
    "THEORY_CHAPTER",
    "get_manifest",
    "validate_manifest",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKAGE_VERSION: Final[str] = "1.0.0"
THEORY_CHAPTER: Final[int] = 30
THEORY_DOC: Final[str] = "theory2.tex"

CHAPTER_30_SECTIONS: Final[dict[str, str]] = {
    "30.1": "Why Tensor Encodings Require Special Treatment",
    "30.2": "Affine Normal Form Encoder",
    "30.3": "Quantifier Discipline for Tensor Formulas",
    "30.4": "Witness Extraction from SAT/UNSAT Results",
    "30.5": "Integration with the JuGeo Solver Layer",
    "30.6": "Formal Theorem Statements and Verification",
    "30.7": "Broadcast Compatibility and Shape Unification",
    "30.8": "Stride Encoding and Memory Layout",
    "30.9": "Polyhedral Compilation and Affine Legality",
    "30.10": "Farkas Lemma and Infeasibility Certificates",
}

STABLE_EXPORTS: Final[tuple[str, ...]] = (
    # models
    "TensorLayout",
    "TensorExtent",
    "AffineLegality",
    "DisciplineKind",
    "QuantifierDiscipline",
    "ExtractionStrategy",
    "WitnessExtractor",
    "ConstraintKind",
    "TensorConstraint",
    # manifest
    "SubsystemManifest",
    "TheoryProvenance",
    "CapabilityDeclaration",
    "DependencySpec",
    "TENSOR_QUANTIFIER_MANIFEST",
    # s01
    "TensorMotivationExamples",
    "TensorEncodingPrimer",
    # s02
    "AffineNormalFormEncoder",
    # s03
    "DisciplineReport",
    "QuantifierInfo",
    "QuantifierDisciplineChecker",
    "QuantifierInstantiator",
    # s04
    "TensorWitness",
    "DependenceWitness",
    "FarkasCoefficients",
    "TensorWitnessExtractor",
    "AffineLegalityWitnessExtractor",
    # algorithms
    "fourier_motzkin",
    "farkas_lemma_certificate",
    "affine_transformation_legality",
    "broadcast_shape_unification",
    "linearize_nd_index",
    "compute_tensor_stride",
    # integration
    "TensorQuantifierSolverIntegration",
    "TensorEncodingContext",
    # theorems
    "TensorQuantifierTheorem",
    "AffineTransformLegalityTheorem",
    "FarkasInfeasibilityTheorem",
    "QuantifierEliminationTheorem",
    "WitnessCompletenessTheorem",
    "BroadcastCompatibilityTheorem",
    "CHAPTER_30_THEOREMS",
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CapabilityKind(str, Enum):
    """Categories of capabilities declared in a subsystem manifest.

    Each capability kind corresponds to a distinct technical contribution
    that the subsystem makes to the overall JuGeo verification infrastructure.
    """

    ENCODING = "encoding"
    SOLVING = "solving"
    WITNESS_EXTRACTION = "witness_extraction"
    PROOF_RECONSTRUCTION = "proof_reconstruction"
    THEORY_INTEGRATION = "theory_integration"
    ALGORITHM = "algorithm"
    INTEGRATION = "integration"


class DependencyKind(str, Enum):
    """Kinds of inter-package dependencies tracked in the manifest.

    REQUIRED dependencies must be present at import time.
    OPTIONAL dependencies are guarded with try/except ImportError.
    SOFT dependencies are used opportunistically if available.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"
    SOFT = "soft"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubsystemManifest:
    """Top-level manifest for a JuGeo subsystem package.

    This dataclass is the authoritative record of what a package provides,
    what it depends on, and where it sits in the theoretical hierarchy.
    It is consumed by the JuGeo package registry and by the copilot assist
    layer for capability discovery and routing.

    Attributes:
        name: Human-readable subsystem name.
        package: Fully-qualified Python package path.
        capabilities: Tuple of capability strings this subsystem exposes.
        dependencies: Tuple of dependency package paths.
        stage: Development stage (e.g., 'root-foundation', 'alpha', 'stable').
        authority_boundary: Which layer of the architecture owns this package.
        scope_honesty: Honest description of what is and is not covered.
        stable_exports: Tuple of names in ``__all__`` that are considered stable.
        version: Package version string.
        theory_chapter: Chapter number in theory2.tex.
        theory_doc: Name of the theory document.
    """

    name: str
    package: str
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    stage: str = "root-foundation"
    authority_boundary: str = ""
    scope_honesty: str = ""
    stable_exports: tuple[str, ...] = ()
    version: str = PACKAGE_VERSION
    theory_chapter: int = THEORY_CHAPTER
    theory_doc: str = THEORY_DOC

    def describe(self) -> str:
        """Return a human-readable description of this manifest.

        Returns:
            Multi-line string describing the subsystem manifest.
        """
        lines = [
            f"SubsystemManifest: {self.name}",
            f"  Package     : {self.package}",
            f"  Stage       : {self.stage}",
            f"  Theory      : {self.theory_doc} Chapter {self.theory_chapter}",
            f"  Boundary    : {self.authority_boundary}",
            f"  Capabilities: {len(self.capabilities)}",
            f"  Dependencies: {len(self.dependencies)}",
            f"  Exports     : {len(self.stable_exports)}",
        ]
        return "\n".join(lines)

    def has_capability(self, cap: str) -> bool:
        """Check whether this manifest declares a given capability.

        Args:
            cap: Capability string to check.

        Returns:
            True if ``cap`` is in ``self.capabilities``.
        """
        return cap in self.capabilities

    def has_dependency(self, dep: str) -> bool:
        """Check whether this manifest declares a given dependency.

        Args:
            dep: Dependency path to check.

        Returns:
            True if ``dep`` is in ``self.dependencies``.
        """
        return dep in self.dependencies


@dataclass(frozen=True, slots=True)
class TheoryProvenance:
    """Records the theoretical origin of a subsystem in a formal theory document.

    Provenance information allows the JuGeo system to trace every implementation
    decision back to a formal theorem or section in the theory document (theory2.tex).

    Attributes:
        chapter: Chapter number (e.g., 30).
        section: Section identifier string (e.g., '30.2').
        theorem_refs: Tuple of theorem reference strings (e.g., ('Thm30.1', 'Lemma30.2')).
        document: Theory document filename.
        description: Short description of the theoretical content.
        copilot_accessible: Whether the copilot layer has been trained on this content.
    """

    chapter: int
    section: str
    theorem_refs: tuple[str, ...]
    document: str = THEORY_DOC
    description: str = ""
    copilot_accessible: bool = True

    def citation(self) -> str:
        """Return a human-readable citation string.

        Returns:
            Citation like 'theory2.tex §30.2 (Thm30.1, Lemma30.2)'.
        """
        refs = ", ".join(self.theorem_refs) if self.theorem_refs else "none"
        return f"{self.document} §{self.section} ({refs})"


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    """Declares a single capability of a subsystem.

    A capability is a concrete technical feature that the subsystem exposes
    to the rest of the JuGeo system. Capabilities are checked at runtime by
    the integration layer and by the copilot assist.

    Attributes:
        name: Short capability identifier.
        kind: CapabilityKind enum value.
        description: Human-readable description.
        provenance: Theoretical provenance for this capability.
        is_stable: Whether this capability is part of the stable API.
        requires_z3: Whether this capability requires the z3 package.
        copilot_notes: Notes for the copilot assist layer.
    """

    name: str
    kind: CapabilityKind
    description: str
    provenance: TheoryProvenance
    is_stable: bool = True
    requires_z3: bool = True
    copilot_notes: str = ""

    def summary(self) -> str:
        """Return a one-line summary of this capability declaration.

        Returns:
            Summary string including name, kind, and stability.
        """
        stable = "stable" if self.is_stable else "unstable"
        z3_note = " [requires z3]" if self.requires_z3 else ""
        return f"[{self.kind.value}] {self.name} ({stable}){z3_note}"


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """Specifies a dependency of a subsystem.

    This dataclass records not just *what* is depended on but also *why*
    and *how* — whether the dependency is required, optional, or soft, and
    which specific symbols are imported from it.

    Attributes:
        package: Fully-qualified Python package path.
        kind: DependencyKind enum value.
        imported_symbols: Tuple of symbol names imported from the package.
        reason: Why this dependency is needed.
        fallback: What happens when this dependency is unavailable.
        min_version: Minimum required version string (if any).
    """

    package: str
    kind: DependencyKind
    imported_symbols: tuple[str, ...]
    reason: str = ""
    fallback: str = "raise ImportError"
    min_version: str = ""

    def import_stub(self) -> str:
        """Return a Python import statement string for this dependency.

        Returns:
            A Python import line like 'from pkg import sym1, sym2'.
        """
        symbols = ", ".join(self.imported_symbols) if self.imported_symbols else "*"
        return f"from {self.package} import {symbols}"


# ---------------------------------------------------------------------------
# Manifest validator
# ---------------------------------------------------------------------------


class ManifestValidationError(ValueError):
    """Raised when a SubsystemManifest fails validation.

    Attributes:
        manifest_name: Name of the manifest that failed validation.
        violations: List of validation violation messages.
    """

    def __init__(self, manifest_name: str, violations: list[str]) -> None:
        """Initialise a ManifestValidationError.

        Args:
            manifest_name: Name of the failing manifest.
            violations: List of human-readable violation descriptions.
        """
        self.manifest_name = manifest_name
        self.violations = violations
        msg = f"Manifest '{manifest_name}' has {len(violations)} violation(s):\n" + "\n".join(
            f"  • {v}" for v in violations
        )
        super().__init__(msg)


class ManifestValidator:
    """Validates a SubsystemManifest against a set of structural rules.

    The validator checks that a manifest has a non-empty name, a valid
    package path, at least one capability, and that all stable exports
    are non-empty strings.  Additional rules can be registered at runtime.

    copilot notes: The ManifestValidator is invoked automatically at import
    time to catch manifest definition errors early.

    Example::

        validator = ManifestValidator()
        errors = validator.validate(TENSOR_QUANTIFIER_MANIFEST)
        assert errors == [], errors
    """

    _REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ("name", "package", "capabilities")

    def __init__(self, strict: bool = True) -> None:
        """Initialise a ManifestValidator.

        Args:
            strict: When True, any violation raises ManifestValidationError.
                    When False, violations are only returned as a list.
        """
        self.strict = strict
        self._custom_rules: list[Any] = []

    def validate(self, manifest: SubsystemManifest) -> list[str]:
        """Validate a SubsystemManifest and return all violation messages.

        This method runs all built-in structural checks on the manifest, as
        well as any custom rules registered with ``add_rule``.

        Args:
            manifest: The SubsystemManifest instance to validate.

        Returns:
            A list of violation strings.  Empty list means valid.

        Raises:
            ManifestValidationError: If ``self.strict`` is True and there are
                any violations.
        """
        violations: list[str] = []

        # Basic field presence
        for field_name in self._REQUIRED_FIELDS:
            value = getattr(manifest, field_name, None)
            if not value:
                violations.append(f"Field '{field_name}' must be non-empty")

        # Package path must look like a dotted path
        if manifest.package and "." not in manifest.package:
            violations.append(
                f"Package '{manifest.package}' does not look like a dotted module path"
            )

        # At least one capability
        if not manifest.capabilities:
            violations.append("Manifest must declare at least one capability")

        # Stage must be a known string
        known_stages = {"root-foundation", "alpha", "beta", "stable", "deprecated"}
        if manifest.stage not in known_stages:
            violations.append(
                f"Unknown stage '{manifest.stage}'. Expected one of {sorted(known_stages)}"
            )

        # Stable exports must all be non-empty strings
        for i, exp in enumerate(manifest.stable_exports):
            if not isinstance(exp, str) or not exp.strip():
                violations.append(f"stable_exports[{i}] is empty or not a string")

        # Version must be non-empty
        if not manifest.version:
            violations.append("Manifest version must be set")

        # Custom rules
        for rule_fn in self._custom_rules:
            result = rule_fn(manifest)
            if result:
                violations.extend(result if isinstance(result, list) else [str(result)])

        if self.strict and violations:
            raise ManifestValidationError(manifest.name, violations)

        return violations

    def add_rule(self, rule_fn: Any) -> None:
        """Register a custom validation rule function.

        The rule function must accept a SubsystemManifest and return either
        None (pass), a string (single violation), or a list of strings.

        Args:
            rule_fn: Callable[[SubsystemManifest], list[str] | str | None].
        """
        self._custom_rules.append(rule_fn)

    def validate_provenance(self, provenance: TheoryProvenance) -> list[str]:
        """Validate a TheoryProvenance dataclass.

        Args:
            provenance: The TheoryProvenance to validate.

        Returns:
            List of violation strings.
        """
        violations: list[str] = []
        if provenance.chapter <= 0:
            violations.append("Theory chapter must be a positive integer")
        if not provenance.section:
            violations.append("Theory section must be non-empty")
        if not provenance.document:
            violations.append("Theory document must be non-empty")
        return violations

    def validate_capability(self, cap: CapabilityDeclaration) -> list[str]:
        """Validate a CapabilityDeclaration dataclass.

        Args:
            cap: The CapabilityDeclaration to validate.

        Returns:
            List of violation strings.
        """
        violations: list[str] = []
        if not cap.name:
            violations.append("Capability name must be non-empty")
        if not cap.description:
            violations.append(f"Capability '{cap.name}' has no description")
        return violations

    def validate_dependency_spec(self, dep: DependencySpec) -> list[str]:
        """Validate a DependencySpec dataclass.

        Args:
            dep: The DependencySpec to validate.

        Returns:
            List of violation strings.
        """
        violations: list[str] = []
        if not dep.package:
            violations.append("Dependency package must be non-empty")
        if dep.kind == DependencyKind.REQUIRED and not dep.imported_symbols:
            violations.append(
                f"Required dependency '{dep.package}' must declare imported symbols"
            )
        return violations


# ---------------------------------------------------------------------------
# Declared capabilities
# ---------------------------------------------------------------------------

_PROV_30_1 = TheoryProvenance(
    chapter=30,
    section="30.1",
    theorem_refs=(),
    description="Motivational section — why tensor encodings need special treatment",
)

_PROV_30_2 = TheoryProvenance(
    chapter=30,
    section="30.2",
    theorem_refs=("Lemma30.1", "Lemma30.2"),
    description="Affine normal form encoding for polyhedral constraints",
)

_PROV_30_3 = TheoryProvenance(
    chapter=30,
    section="30.3",
    theorem_refs=("Thm30.1",),
    description="Quantifier discipline for tensor index formulas",
)

_PROV_30_4 = TheoryProvenance(
    chapter=30,
    section="30.4",
    theorem_refs=("Thm30.2", "Cor30.1"),
    description="Witness extraction from SAT/UNSAT solver results",
)

_PROV_30_5 = TheoryProvenance(
    chapter=30,
    section="30.5",
    theorem_refs=(),
    description="Integration with the JuGeo solver session pool",
)

_PROV_30_6 = TheoryProvenance(
    chapter=30,
    section="30.6",
    theorem_refs=("Thm30.3", "Thm30.4", "Thm30.5"),
    description="Formal theorem statements for Chapter 30",
)

DECLARED_CAPABILITIES: Final[tuple[CapabilityDeclaration, ...]] = (
    CapabilityDeclaration(
        name="tensor_extent_encoding",
        kind=CapabilityKind.ENCODING,
        description="Encode tensor shapes, strides, and index validity as QF_LIA formulas",
        provenance=_PROV_30_1,
        copilot_notes="Primary encoding for multi-dimensional array shapes",
    ),
    CapabilityDeclaration(
        name="affine_legality_encoding",
        kind=CapabilityKind.ENCODING,
        description="Encode affine transformation legality for polyhedral compilation",
        provenance=_PROV_30_2,
        copilot_notes="Lex-positivity conditions for dependence vectors under a linear transform",
    ),
    CapabilityDeclaration(
        name="quantifier_discipline",
        kind=CapabilityKind.ENCODING,
        description="Enforce quantifier discipline — skolemize, instantiate, or restrict to QF",
        provenance=_PROV_30_3,
        copilot_notes="Avoids e-matching loops and ensures decidability",
    ),
    CapabilityDeclaration(
        name="witness_extraction",
        kind=CapabilityKind.WITNESS_EXTRACTION,
        description="Extract tensor shape witnesses and Farkas certificates from solver results",
        provenance=_PROV_30_4,
        copilot_notes="Converts Z3 models to Python-typed tensor witnesses",
    ),
    CapabilityDeclaration(
        name="solver_integration",
        kind=CapabilityKind.INTEGRATION,
        description="Bridge tensor encoding layer with Z3SessionPool and ReconstructionPipeline",
        provenance=_PROV_30_5,
        requires_z3=True,
        copilot_notes="Routes QF_LIA queries through the fragment classifier",
    ),
    CapabilityDeclaration(
        name="chapter30_theorems",
        kind=CapabilityKind.THEORY_INTEGRATION,
        description="Formal theorem statements for Chapter 30, encodable and verifiable in Z3",
        provenance=_PROV_30_6,
        copilot_notes="Each theorem class corresponds to a numbered theorem in theory2.tex §30",
    ),
    CapabilityDeclaration(
        name="fourier_motzkin",
        kind=CapabilityKind.ALGORITHM,
        description="Fourier-Motzkin variable elimination for integer linear arithmetic",
        provenance=_PROV_30_2,
        requires_z3=False,
        copilot_notes="Pure Python implementation — no Z3 required",
    ),
    CapabilityDeclaration(
        name="farkas_certificate",
        kind=CapabilityKind.ALGORITHM,
        description="Farkas lemma infeasibility certificate computation",
        provenance=_PROV_30_4,
        requires_z3=False,
        copilot_notes="Used to certify UNSAT results for affine legality queries",
    ),
)

# ---------------------------------------------------------------------------
# Declared dependencies
# ---------------------------------------------------------------------------

DECLARED_DEPENDENCIES: Final[tuple[DependencySpec, ...]] = (
    DependencySpec(
        package="jugeo.solver.z3_session",
        kind=DependencyKind.OPTIONAL,
        imported_symbols=(
            "Z3Session",
            "Z3SessionPool",
            "Z3Formula",
            "Z3Encoder",
            "Z3Decoder",
            "Z3QueryBuilder",
            "Z3Result",
            "Z3FragmentClassifier",
            "Z3TacticRouter",
            "Z3SessionMonitor",
            "Z3Serializer",
            "Z3CopilotAssist",
            "SolveOutcome",
            "FormulaKind",
            "TrustLevel",
        ),
        reason="Session management and formula solving",
        fallback="stub types for type-checking only",
    ),
    DependencySpec(
        package="jugeo.solver.fragments",
        kind=DependencyKind.OPTIONAL,
        imported_symbols=(
            "Fragment",
            "FragmentSignature",
            "FragmentClassifier",
            "FragmentDecomposer",
            "EncodingStrategy",
            "TacticSelector",
            "FragmentCache",
            "FragmentStatistics",
            "CopilotFragmentAssist",
            "LogicalFragment",
            "SolverFragment",
            "classify_fragment",
        ),
        reason="Fragment classification and tactic routing",
        fallback="stub types for type-checking only",
    ),
    DependencySpec(
        package="jugeo.solver.reconstruction",
        kind=DependencyKind.OPTIONAL,
        imported_symbols=(
            "ReconstructionKind",
            "ValidationStatus",
            "ProofStep",
            "WitnessBinding",
            "SortInterpretation",
            "FunctionInterpretation",
            "ArrayInterpretation",
            "DatatypeInterpretation",
            "ReconstructionResult",
            "ReconstructionReport",
            "ProofReconstructor",
            "WitnessReconstructor",
            "ModelReconstructor",
            "EvidenceAssembler",
            "PartialReconstructor",
            "ReconstructionCache",
            "ReconstructionValidator",
            "ReconstructionPipeline",
            "ReconstructionStatistics",
            "reconstruct_countermodel",
        ),
        reason="Proof reconstruction and witness assembly",
        fallback="stub types for type-checking only",
    ),
    DependencySpec(
        package="z3",
        kind=DependencyKind.OPTIONAL,
        imported_symbols=("Int", "Bool", "And", "Or", "Not", "ForAll", "Exists", "Solver"),
        reason="Z3 SMT solver for formula construction and solving",
        fallback="symbolic string stubs for all z3 operations",
    ),
)

# ---------------------------------------------------------------------------
# The manifest itself
# ---------------------------------------------------------------------------

TENSOR_QUANTIFIER_MANIFEST: Final[SubsystemManifest] = SubsystemManifest(
    name="tensor_quantifier_encodings",
    package="jugeo.encodings.tensor_quantifier_encodings",
    capabilities=tuple(cap.name for cap in DECLARED_CAPABILITIES),
    dependencies=tuple(dep.package for dep in DECLARED_DEPENDENCIES),
    stage="root-foundation",
    authority_boundary="jugeo.encodings — encoding layer (Chapter 30)",
    scope_honesty=(
        "This package encodes tensor extent and affine legality constraints as Z3 QF_LIA "
        "formulas, enforces quantifier discipline, and extracts witnesses from solver results. "
        "It does NOT perform general-purpose tensor computation or provide a tensor runtime. "
        "Rank must be known statically for most encodings; dynamic rank support is limited."
    ),
    stable_exports=STABLE_EXPORTS,
    version=PACKAGE_VERSION,
    theory_chapter=THEORY_CHAPTER,
    theory_doc=THEORY_DOC,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_manifest() -> SubsystemManifest:
    """Return the canonical manifest for this package.

    Returns:
        The TENSOR_QUANTIFIER_MANIFEST constant.
    """
    return TENSOR_QUANTIFIER_MANIFEST


def validate_manifest(strict: bool = False) -> list[str]:
    """Validate the canonical manifest and return any violations.

    This function is safe to call at import time.  By default ``strict`` is
    False so it returns violations rather than raising.

    Args:
        strict: If True, raise ManifestValidationError on any violation.

    Returns:
        List of violation strings (empty if valid).
    """
    validator = ManifestValidator(strict=strict)
    return validator.validate(TENSOR_QUANTIFIER_MANIFEST)
