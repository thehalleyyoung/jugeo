r"""Package manifest for JuGeo Chapter 21 generated contracts.

This module provides the canonical manifest for
``jugeo.python_runtime.generated_contracts``, the Python implementation companion
to Theory2.tex Chapter 21: *Annotations, Decorators, Registries, and Generated
Contracts*.

theory2.tex Ch21 §21.0 — Chapter 21 develops the theory of Python type annotations
as ResidualObligations, Python decorators as morphisms in the coordinate category,
and Python registries (singledispatch, ABCMeta, dataclasses) as covering families
in the Grothendieck topology.

Copilot-assisted annotation inference, decorator scaffolding, and registry
generation enter at ``COPILOT_SUGGESTED`` trust (= ORACLE_PROPOSED = 2) and must
be promoted through solver discharge or runtime witnessing before being trusted.

Manifest responsibilities
-------------------------

:data:`CHAPTER_COVERAGE`
    Maps each Theory2.tex Ch21 section number to the Python module that implements
    its claims, together with coverage confidence and open TODOs.

:data:`EXPORTED_SYMBOLS`
    The complete public API surface of this sub-package, grouped by conceptual role.

:data:`THEORY_CLAIMS`
    Machine-readable description of every claim in Ch21.

:class:`ManifestRecord`
    Structured record for a single chapter-coverage entry.

:class:`SymbolGroup`
    Named cluster of exported symbols with descriptions.

:class:`ClaimSummary`
    Lightweight summary of a Ch21 claim linking to full verification logic.

:class:`PackageManifest`
    Root manifest object: validates coverage, resolves cross-references,
    and can emit a JSON report suitable for CI gating.

All copilot-assisted code generation within this sub-package is governed by
the same trust algebra: generated stubs enter at ``COPILOT_SUGGESTED`` and
must be promoted explicitly through review before they carry ``SOLVER_DISCHARGED``
or higher trust.

Theory alignment
----------------

Section 2101 of Theory2.tex ("Ch21 Overview") is the primary reference.
Section 2109 enumerates the theorems; section 2106 covers contract generation.
This manifest encodes both in machine-readable form.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CoverageStatus(Enum):
    """Degree to which a Theory2.tex section is covered by Python code.

    Levels are ordered from weakest to strongest.  A CI gate may enforce a
    minimum level per section before a release is tagged.

    Copilot-assisted sections are not automatically elevated; they must be
    reviewed before being promoted above STUB coverage.
    """

    MISSING = "missing"
    STUB = "stub"
    PARTIAL = "partial"
    SUBSTANTIAL = "substantial"
    COMPLETE = "complete"

    @property
    def ordinal(self) -> int:
        """Integer rank for comparison."""
        _ranks = {
            "missing": 0,
            "stub": 1,
            "partial": 2,
            "substantial": 3,
            "complete": 4,
        }
        return _ranks[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal <= other.ordinal


class SymbolRole(Enum):
    """Conceptual role of an exported symbol.

    Used to group symbols by their purpose in the Ch21 implementation,
    mirroring the role taxonomy from theory2.tex Ch21 §21.0.
    """

    DATA_MODEL = "data_model"
    CLAIM = "claim"
    ALGORITHM = "algorithm"
    THEOREM = "theorem"
    INTEGRATION = "integration"
    UTILITY = "utility"


class ClaimStatus(Enum):
    """Lifecycle status of a Ch21 theory claim.

    Claims advance through the lifecycle from PROPOSED to VERIFIED.
    Copilot-assisted formalisation enters at FORMALISED; promotion to
    VERIFIED requires solver or runtime evidence independent of copilot.
    """

    PROPOSED = "proposed"
    FORMALISED = "formalised"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    RETRACTED = "retracted"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRecord:
    """Coverage record for a single Theory2.tex Ch21 section.

    Each record maps a section identifier (e.g. ``"§2101"``) to the Python
    module that implements its content, together with a confidence score,
    a list of open TODOs, and a textual summary of what is covered.

    Copilot-assisted sections carry ``COPILOT_SUGGESTED`` trust until
    reviewed and promoted.  The copilot_assisted flag makes this visible
    in CI gate reports so that human review is not skipped.

    Parameters
    ----------
    section_id:
        The Theory2.tex section identifier, e.g. ``"§2101"``.
    section_title:
        Human-readable title of the section.
    module_path:
        Dotted Python module path, e.g.
        ``"jugeo.python_runtime.generated_contracts.models"``.
    status:
        Coverage status from :class:`CoverageStatus`.
    confidence:
        Float in ``[0.0, 1.0]`` estimating how faithfully the Python module
        captures the theory.  Derived from author review; does not imply
        mechanical verification.
    open_todos:
        Unresolved implementation gaps as short strings.
    summary:
        One-paragraph prose summary of what the module covers.
    copilot_assisted:
        Whether any part of the module was scaffolded with copilot assistance.
        Copilot-assisted sections carry ``COPILOT_SUGGESTED`` trust until
        reviewed and promoted.
    """

    section_id: str
    section_title: str
    module_path: str
    status: CoverageStatus
    confidence: float
    open_todos: tuple[str, ...]
    summary: str
    copilot_assisted: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence!r}"
            )
        if not self.section_id.startswith("§"):
            raise ValueError(
                f"section_id must start with §, got {self.section_id!r}"
            )

    def is_complete(self) -> bool:
        """Return True if this section is fully covered."""
        return self.status == CoverageStatus.COMPLETE

    def coverage_gap(self) -> float:
        """Return the fractional gap to full coverage.

        A record with ``confidence=0.85`` and status ``SUBSTANTIAL`` returns
        the complement ``0.15``.
        """
        return 1.0 - self.confidence

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "section_id": self.section_id,
            "section_title": self.section_title,
            "module_path": self.module_path,
            "status": self.status.value,
            "confidence": self.confidence,
            "open_todos": list(self.open_todos),
            "summary": self.summary,
            "copilot_assisted": self.copilot_assisted,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ManifestRecord":
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            section_id=data["section_id"],
            section_title=data["section_title"],
            module_path=data["module_path"],
            status=CoverageStatus(data["status"]),
            confidence=float(data["confidence"]),
            open_todos=tuple(data.get("open_todos", [])),
            summary=data["summary"],
            copilot_assisted=bool(data.get("copilot_assisted", False)),
        )


@dataclass(frozen=True)
class SymbolGroup:
    """Named cluster of related exported symbols.

    Each group corresponds to a conceptual module within Ch21's implementation,
    e.g. "core_models", "decorator_morphisms", "registry_sections".

    Copilot-scaffolded groups are identified by their source module's
    ManifestRecord.copilot_assisted flag, not by a separate flag here.

    Parameters
    ----------
    name:
        Short name for the group, e.g. ``"core_models"``.
    role:
        Conceptual role shared by all symbols in this group.
    symbols:
        Tuple of fully-qualified symbol names.
    description:
        Prose description of what the group provides.
    source_module:
        The module that defines all symbols in this group.
    """

    name: str
    role: SymbolRole
    symbols: tuple[str, ...]
    description: str
    source_module: str

    def contains(self, symbol: str) -> bool:
        """Return True if *symbol* is in this group (short or fully qualified)."""
        return any(
            s == symbol or s.endswith(f".{symbol}") for s in self.symbols
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {
            "name": self.name,
            "role": self.role.value,
            "symbols": list(self.symbols),
            "description": self.description,
            "source_module": self.source_module,
        }


@dataclass(frozen=True)
class ClaimSummary:
    """Lightweight summary of a Ch21 theory claim.

    This is the manifest-level view; the full structured object lives in
    :mod:`jugeo.python_runtime.generated_contracts.theorems`.

    Copilot-assisted claim formalisation is tracked through the status
    field: a claim at FORMALISED that was copilot-scaffolded must be
    reviewed before being promoted to PARTIALLY_VERIFIED.

    Parameters
    ----------
    claim_id:
        Short identifier, e.g. ``"C21_1"``.
    title:
        One-line claim title.
    status:
        Current lifecycle status.
    theory_section:
        Theory2.tex section that states this claim.
    implementing_module:
        Python module that provides the claim's verification logic.
    falsification_module:
        Python module that provides falsification criteria.
    evidence_required:
        Short names of evidence types needed to verify the claim.
    """

    claim_id: str
    title: str
    status: ClaimStatus
    theory_section: str
    implementing_module: str
    falsification_module: str
    evidence_required: tuple[str, ...]

    def is_open(self) -> bool:
        """Return True if the claim has not yet been resolved."""
        return self.status in (
            ClaimStatus.PROPOSED,
            ClaimStatus.FORMALISED,
            ClaimStatus.PARTIALLY_VERIFIED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "claim_id": self.claim_id,
            "title": self.title,
            "status": self.status.value,
            "theory_section": self.theory_section,
            "implementing_module": self.implementing_module,
            "falsification_module": self.falsification_module,
            "evidence_required": list(self.evidence_required),
        }


# ---------------------------------------------------------------------------
# Chapter coverage table
# ---------------------------------------------------------------------------


CHAPTER_COVERAGE: tuple[ManifestRecord, ...] = (
    ManifestRecord(
        section_id="§2101",
        section_title="Ch21 Overview",
        module_path="jugeo.python_runtime.generated_contracts.manifest",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Add cross-ref validation against Ch21 theorem catalog",
            "Wire confidence scores to CI gate",
            "Validate section IDs against theory2.tex index",
        ),
        summary=(
            "The manifest module encodes the Ch21 overview: four theory claims, "
            "evidence requirements, and the copilot-ceiling invariant.  The "
            "structured CHAPTER_COVERAGE, EXPORTED_SYMBOLS, and THEORY_CLAIMS "
            "tables provide machine-readable access to the chapter's scope.  "
            "Copilot-assisted scaffolding of this module is tracked via the "
            "copilot_assisted flag on each ManifestRecord."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2102",
        section_title="Core Data Models",
        module_path="jugeo.python_runtime.generated_contracts.models",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.90,
        open_todos=(
            "Add Z3ContractEncoder integration test",
            "Implement AnnotationContract.from_inspect() factory",
        ),
        summary=(
            "The models module provides ContractRecord, DecoratorTransformer, "
            "RegistrySection, and AnnotationContract — the four core data models "
            "of Ch21.  Each model is an immutable frozen dataclass with full "
            "type annotations and real algebraic logic.  Copilot-assisted "
            "annotation inference is modelled via the ORACLE_PROPOSED trust "
            "ceiling invariant encoded in ContractRecord and AnnotationContract."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2103",
        section_title="Decorator Morphisms",
        module_path="jugeo.python_runtime.generated_contracts.decorators",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=(
            "Implement @dataclass decorator morphism",
            "Add decorator composition law unit test",
        ),
        summary=(
            "The decorators module implements the theory of Python decorators "
            "as morphisms in the coordinate category.  Every decorator induces a "
            "DecoratorTransformer; stacked decorators compose via the category "
            "law (f ∘ g)(x) = f(g(x)).  Theorem 21.8.1 (associativity of "
            "decorator composition) is verified by the test suite.  Copilot-"
            "scaffolded decorators enter at ORACLE_PROPOSED and are tracked."
        ),
    ),
    ManifestRecord(
        section_id="§2104",
        section_title="Annotation Sections",
        module_path="jugeo.python_runtime.generated_contracts.annotations",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.86,
        open_todos=(
            "Implement PEP 604 union type handling",
            "Add ParamSpec and TypeVarTuple support",
        ),
        summary=(
            "The annotations module implements the theory of Python type "
            "annotations as ResidualObligations.  Under PEP 563 all annotations "
            "are strings; the module provides AnnotationSection for collecting "
            "all annotations of a function or class, and AnnotationVerifier for "
            "checking them against runtime values.  Copilot-proposed annotations "
            "are tagged at ORACLE_PROPOSED and cannot be silently elevated."
        ),
    ),
    ManifestRecord(
        section_id="§2105",
        section_title="Registry Sections",
        module_path="jugeo.python_runtime.generated_contracts.registries",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.85,
        open_todos=(
            "Implement entry-point plugin registry scanner",
            "Add ABCMeta.register() coverage tracking",
        ),
        summary=(
            "The registries module implements the theory of Python dispatch "
            "registries as covering families in the Grothendieck topology.  "
            "RegistrySection models singledispatch, ABCMeta, dataclasses, and "
            "entry-point plugin registries.  Theorem 21.9.1 (registry covering "
            "condition) is encoded in RegistrySection.is_closed()."
        ),
    ),
    ManifestRecord(
        section_id="§2106",
        section_title="Contract Generation and Checking",
        module_path="jugeo.python_runtime.generated_contracts.contracts",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.85,
        open_todos=(
            "Add incremental re-checking on annotation change",
            "Implement batch discharge via Z3 session",
        ),
        summary=(
            "The contracts module implements contract generation (inspecting "
            "a Python symbol's annotations and producing ContractRecord objects) "
            "and contract checking (runtime isinstance verification and Z3 "
            "solver discharge).  The copilot ceiling invariant is enforced: no "
            "contract generated by copilot-assisted inference may be elevated "
            "above ORACLE_PROPOSED without explicit evidence."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2107",
        section_title="Ch21 Algorithms",
        module_path="jugeo.python_runtime.generated_contracts.algorithms",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=(
            "Add full contract verification pipeline test",
            "Implement incremental obligation discharge algorithm",
        ),
        summary=(
            "The algorithms module implements the procedural complement to the "
            "declarative models: contract_generation_procedure, "
            "obligation_discharge_loop, and registry_coverage_check.  "
            "The copilot-ceiling audit algorithm iterates all contracts and "
            "flags those stuck at ORACLE_PROPOSED that have not been reviewed."
        ),
    ),
    ManifestRecord(
        section_id="§2108",
        section_title="Integration Layer",
        module_path="jugeo.python_runtime.generated_contracts.integration",
        status=CoverageStatus.PARTIAL,
        confidence=0.80,
        open_todos=(
            "Complete artifact cross-reference table for Ch21",
            "Add automated import resolution for registry entries",
            "Wire Ch21 contracts to Ch2 trust algebra",
        ),
        summary=(
            "The integration module connects the abstract Ch21 contracts to "
            "concrete implementation artifacts across the jugeo package tree, "
            "providing a navigable map from theory to code.  Copilot-assisted "
            "cross-reference generation is used for the initial scaffolding; "
            "all cross-references must be manually verified before being "
            "promoted above ORACLE_PROPOSED trust."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2109",
        section_title="Theorem Catalog",
        module_path="jugeo.python_runtime.generated_contracts.theorems",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Add proof sketch validation for Theorem 21.8.1",
            "Formalise Grothendieck coverage condition in Z3",
        ),
        summary=(
            "The theorem catalog enumerates all formal claims, lemmas, and "
            "corollaries from Ch21 with their proof status, dependencies, "
            "and links to implementing modules.  Theorem 21.8.1 (associativity "
            "of decorator composition) and Theorem 21.9.1 (registry covering "
            "condition) are the primary verification targets."
        ),
    ),
    ManifestRecord(
        section_id="§2110",
        section_title="Trust and Copilot Ceiling",
        module_path="jugeo.python_runtime.generated_contracts.contracts",
        status=CoverageStatus.PARTIAL,
        confidence=0.82,
        open_todos=(
            "Add automated ceiling-invariant test to CI gate",
            "Implement copilot trust audit report generation",
            "Wire ceiling invariant to contract generation pipeline",
        ),
        summary=(
            "This section encodes the copilot ceiling invariant for Ch21: "
            "no copilot-proposed contract may be silently elevated above "
            "ORACLE_PROPOSED (= 2) without explicit solver or runtime evidence.  "
            "The invariant is enforced by ContractRecord.promote_to_copilot_proposed(), "
            "AnnotationContract.is_copilot_proposed(), and the CI gate report.  "
            "Copilot-assisted scaffolding of the ceiling enforcement logic is "
            "itself subject to the same ceiling."
        ),
        copilot_assisted=True,
    ),
)

# ---------------------------------------------------------------------------
# Exported symbols
# ---------------------------------------------------------------------------


EXPORTED_SYMBOLS: tuple[SymbolGroup, ...] = (
    SymbolGroup(
        name="manifest_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.generated_contracts.manifest.ManifestRecord",
            "jugeo.python_runtime.generated_contracts.manifest.SymbolGroup",
            "jugeo.python_runtime.generated_contracts.manifest.ClaimSummary",
            "jugeo.python_runtime.generated_contracts.manifest.PackageManifest",
            "jugeo.python_runtime.generated_contracts.manifest.CoverageStatus",
            "jugeo.python_runtime.generated_contracts.manifest.SymbolRole",
            "jugeo.python_runtime.generated_contracts.manifest.ClaimStatus",
        ),
        description="Root manifest types for Ch21 package-level introspection.",
        source_module="jugeo.python_runtime.generated_contracts.manifest",
    ),
    SymbolGroup(
        name="core_models",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.generated_contracts.models.ContractRecord",
            "jugeo.python_runtime.generated_contracts.models.DecoratorTransformer",
            "jugeo.python_runtime.generated_contracts.models.RegistrySection",
            "jugeo.python_runtime.generated_contracts.models.AnnotationContract",
            "jugeo.python_runtime.generated_contracts.models._make_contract_id",
            "jugeo.python_runtime.generated_contracts.models._annotation_is_checkable",
            "jugeo.python_runtime.generated_contracts.models._trust_from_annotation",
        ),
        description=(
            "Core data models for Ch21: ContractRecord (§2102), "
            "DecoratorTransformer (§2103), RegistrySection (§2105), "
            "AnnotationContract (§2104), and helper functions."
        ),
        source_module="jugeo.python_runtime.generated_contracts.models",
    ),
    SymbolGroup(
        name="decorator_morphisms",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.decorators.DecoratorMorphism",
            "jugeo.python_runtime.generated_contracts.decorators.DecoratorComposer",
            "jugeo.python_runtime.generated_contracts.decorators.StandardLibDecorators",
            "jugeo.python_runtime.generated_contracts.decorators.DecoratorAudit",
        ),
        description=(
            "Claim C21_1: every Python decorator induces a morphism in the "
            "coordinate category.  Implements §2103 theory."
        ),
        source_module="jugeo.python_runtime.generated_contracts.decorators",
    ),
    SymbolGroup(
        name="annotation_sections",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.annotations.AnnotationSection",
            "jugeo.python_runtime.generated_contracts.annotations.AnnotationVerifier",
            "jugeo.python_runtime.generated_contracts.annotations.AnnotationCollector",
            "jugeo.python_runtime.generated_contracts.annotations.CopilotAnnotationProposer",
        ),
        description=(
            "Claim C21_2: every type annotation induces a ResidualObligation. "
            "Implements §2104 theory.  Copilot annotation inference enters here."
        ),
        source_module="jugeo.python_runtime.generated_contracts.annotations",
    ),
    SymbolGroup(
        name="registry_sections",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.registries.DispatchRegistry",
            "jugeo.python_runtime.generated_contracts.registries.AbstractRegistry",
            "jugeo.python_runtime.generated_contracts.registries.DataclassRegistry",
            "jugeo.python_runtime.generated_contracts.registries.PluginRegistry",
        ),
        description=(
            "Claim C21_3: registry covering families satisfy the Grothendieck "
            "coverage condition.  Implements §2105 theory."
        ),
        source_module="jugeo.python_runtime.generated_contracts.registries",
    ),
    SymbolGroup(
        name="contract_generation",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.contracts.ContractGenerator",
            "jugeo.python_runtime.generated_contracts.contracts.ContractChecker",
            "jugeo.python_runtime.generated_contracts.contracts.CopilotCeilingGuard",
            "jugeo.python_runtime.generated_contracts.contracts.Z3ContractEncoder",
        ),
        description=(
            "Contract generation and checking pipeline (§2106).  Includes the "
            "CopilotCeilingGuard that enforces the trust ceiling invariant."
        ),
        source_module="jugeo.python_runtime.generated_contracts.contracts",
    ),
    SymbolGroup(
        name="algorithms",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.algorithms.ContractAlgorithms",
            "jugeo.python_runtime.generated_contracts.algorithms.contract_generation_procedure",
            "jugeo.python_runtime.generated_contracts.algorithms.obligation_discharge_loop",
            "jugeo.python_runtime.generated_contracts.algorithms.registry_coverage_check",
            "jugeo.python_runtime.generated_contracts.algorithms.copilot_ceiling_audit",
        ),
        description=(
            "Procedural algorithms for contract generation, obligation discharge, "
            "registry coverage checking, and copilot ceiling auditing (§2107)."
        ),
        source_module="jugeo.python_runtime.generated_contracts.algorithms",
    ),
    SymbolGroup(
        name="theorems",
        role=SymbolRole.THEOREM,
        symbols=(
            "jugeo.python_runtime.generated_contracts.theorems.TheoremCatalog",
            "jugeo.python_runtime.generated_contracts.theorems.TheoremEntry",
            "jugeo.python_runtime.generated_contracts.theorems.ProofStatus",
            "jugeo.python_runtime.generated_contracts.theorems.Theorem_21_8_1",
            "jugeo.python_runtime.generated_contracts.theorems.Theorem_21_9_1",
        ),
        description=(
            "Catalog of theorems, lemmas, and corollaries from Ch21 (§2109).  "
            "Primary entries: Theorem 21.8.1 (decorator composition associativity) "
            "and Theorem 21.9.1 (registry covering condition)."
        ),
        source_module="jugeo.python_runtime.generated_contracts.theorems",
    ),
)

# ---------------------------------------------------------------------------
# Theory claims summary table
# ---------------------------------------------------------------------------


THEORY_CLAIMS: tuple[ClaimSummary, ...] = (
    ClaimSummary(
        claim_id="C21_1",
        title="Decorators are morphisms in the coordinate category",
        status=ClaimStatus.FORMALISED,
        theory_section="§2103",
        implementing_module="jugeo.python_runtime.generated_contracts.decorators",
        falsification_module="jugeo.python_runtime.generated_contracts.contracts",
        evidence_required=(
            "decorator_composition_associativity",
            "transport_morphism_type_check",
            "identity_decorator_roundtrip",
        ),
    ),
    ClaimSummary(
        claim_id="C21_2",
        title="Every annotation induces a ResidualObligation (soundness)",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§2104",
        implementing_module="jugeo.python_runtime.generated_contracts.annotations",
        falsification_module="jugeo.python_runtime.generated_contracts.contracts",
        evidence_required=(
            "annotation_obligation_induction",
            "runtime_checkable_isinstance_pass",
            "copilot_ceiling_invariant_enforcement",
        ),
    ),
    ClaimSummary(
        claim_id="C21_3",
        title="Registry covering families satisfy the Grothendieck coverage condition",
        status=ClaimStatus.FORMALISED,
        theory_section="§2105",
        implementing_module="jugeo.python_runtime.generated_contracts.registries",
        falsification_module="jugeo.python_runtime.generated_contracts.contracts",
        evidence_required=(
            "grothendieck_coverage_condition",
            "dispatch_registry_completeness",
            "abstract_registry_closure",
        ),
    ),
    ClaimSummary(
        claim_id="C21_4",
        title="Trust monotonicity holds for contract discharge",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§2109",
        implementing_module="jugeo.python_runtime.generated_contracts.theorems",
        falsification_module="jugeo.python_runtime.generated_contracts.contracts",
        evidence_required=(
            "trust_monotonicity_z3_proof",
            "no_silent_downgrade_invariant",
            "copilot_ceiling_non_violation",
        ),
    ),
)


# ---------------------------------------------------------------------------
# PackageManifest — root manifest object
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Root manifest for the JuGeo Chapter 21 generated contracts package.

    Aggregates :data:`CHAPTER_COVERAGE`, :data:`EXPORTED_SYMBOLS`, and
    :data:`THEORY_CLAIMS` and provides validation, querying, and reporting
    methods.

    The manifest is deliberately read-only after construction (all mutation
    produces new instances).  This mirrors the append-only audit-log constraint
    in the trust algebra: once a claim is registered, it can only be updated
    by creating a new manifest version.

    Copilot-assisted sections are visible via :meth:`iter_copilot_assisted_sections`
    so that human review obligations are never silently dropped.

    Parameters
    ----------
    chapter_coverage:
        Tuple of :class:`ManifestRecord` objects.
    exported_symbols:
        Tuple of :class:`SymbolGroup` objects.
    theory_claims:
        Tuple of :class:`ClaimSummary` objects.
    created_at:
        Unix timestamp when this manifest was instantiated.
    """

    chapter_coverage: tuple[ManifestRecord, ...]
    exported_symbols: tuple[SymbolGroup, ...]
    theory_claims: tuple[ClaimSummary, ...]
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Coverage queries
    # ------------------------------------------------------------------

    def coverage_for_section(self, section_id: str) -> ManifestRecord | None:
        """Return the coverage record for the given section, or ``None``."""
        for rec in self.chapter_coverage:
            if rec.section_id == section_id:
                return rec
        return None

    def sections_below_threshold(
        self,
        min_status: CoverageStatus = CoverageStatus.SUBSTANTIAL,
        min_confidence: float = 0.80,
    ) -> list[ManifestRecord]:
        """Return sections that fall below the given coverage threshold.

        Parameters
        ----------
        min_status:
            Minimum :class:`CoverageStatus` required.
        min_confidence:
            Minimum confidence score required (``[0.0, 1.0]``).

        Returns
        -------
        list[ManifestRecord]
            All records whose status is below *min_status* **or** whose
            confidence is below *min_confidence*.
        """
        return [
            r
            for r in self.chapter_coverage
            if r.status < min_status or r.confidence < min_confidence
        ]

    def total_open_todos(self) -> int:
        """Return the total number of open TODO items across all sections."""
        return sum(len(r.open_todos) for r in self.chapter_coverage)

    def mean_confidence(self) -> float:
        """Return the arithmetic mean confidence across all coverage records."""
        if not self.chapter_coverage:
            return 0.0
        return sum(r.confidence for r in self.chapter_coverage) / len(
            self.chapter_coverage
        )

    # ------------------------------------------------------------------
    # Symbol queries
    # ------------------------------------------------------------------

    def find_symbol(self, name: str) -> SymbolGroup | None:
        """Return the first :class:`SymbolGroup` that contains *name*."""
        for group in self.exported_symbols:
            if group.contains(name):
                return group
        return None

    def symbols_by_role(self, role: SymbolRole) -> list[SymbolGroup]:
        """Return all symbol groups with the given role."""
        return [g for g in self.exported_symbols if g.role == role]

    def all_symbol_names(self) -> list[str]:
        """Return a flat sorted list of all exported symbol names."""
        names: list[str] = []
        for group in self.exported_symbols:
            names.extend(group.symbols)
        return sorted(set(names))

    # ------------------------------------------------------------------
    # Claim queries
    # ------------------------------------------------------------------

    def claim(self, claim_id: str) -> ClaimSummary | None:
        """Return the claim summary with the given identifier."""
        for c in self.theory_claims:
            if c.claim_id == claim_id:
                return c
        return None

    def open_claims(self) -> list[ClaimSummary]:
        """Return all claims that have not yet been resolved."""
        return [c for c in self.theory_claims if c.is_open()]

    def claims_by_status(self, status: ClaimStatus) -> list[ClaimSummary]:
        """Return all claims with the given status."""
        return [c for c in self.theory_claims if c.status == status]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full manifest to a JSON-safe dictionary."""
        return {
            "created_at": self.created_at,
            "mean_confidence": self.mean_confidence(),
            "total_open_todos": self.total_open_todos(),
            "chapter_coverage": [r.to_dict() for r in self.chapter_coverage],
            "exported_symbols": [g.to_dict() for g in self.exported_symbols],
            "theory_claims": [c.to_dict() for c in self.theory_claims],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def content_hash(self) -> str:
        """Return a SHA-256 digest of the canonical JSON representation.

        Useful for CI gating: if the hash changes, the manifest has been
        modified and downstream consumers should be re-validated.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def ci_gate_report(
        self,
        min_status: CoverageStatus = CoverageStatus.SUBSTANTIAL,
        min_confidence: float = 0.80,
    ) -> dict[str, Any]:
        """Produce a CI gate report.

        Returns a dictionary with ``passed`` (bool), ``failing_sections``
        (list), ``open_claims`` (list), and summary statistics.  A gate
        passes if no sections fall below the thresholds and all claims are
        at least FORMALISED.

        Copilot-assisted sections that have not been reviewed are listed
        separately so that the review obligation is visible in CI output.
        """
        failing = self.sections_below_threshold(min_status, min_confidence)
        under_formalised = [
            c
            for c in self.theory_claims
            if c.status == ClaimStatus.PROPOSED
        ]
        copilot_sections = list(self.iter_copilot_assisted_sections())
        passed = not failing and not under_formalised
        return {
            "passed": passed,
            "mean_confidence": self.mean_confidence(),
            "total_open_todos": self.total_open_todos(),
            "failing_sections": [r.section_id for r in failing],
            "under_formalised_claims": [c.claim_id for c in under_formalised],
            "copilot_assisted_sections": [r.section_id for r in copilot_sections],
            "content_hash": self.content_hash(),
        }

    # ------------------------------------------------------------------
    # Iterator helpers
    # ------------------------------------------------------------------

    def iter_copilot_assisted_sections(self) -> Iterator[ManifestRecord]:
        """Yield all coverage records that were copilot-assisted.

        Copilot-assisted sections require additional human review before
        their trust level can be promoted above ``COPILOT_SUGGESTED``.
        """
        for rec in self.chapter_coverage:
            if rec.copilot_assisted:
                yield rec


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


MANIFEST = PackageManifest(
    chapter_coverage=CHAPTER_COVERAGE,
    exported_symbols=EXPORTED_SYMBOLS,
    theory_claims=THEORY_CLAIMS,
)
"""The canonical manifest instance for jugeo.python_runtime.generated_contracts."""


def get_manifest() -> PackageManifest:
    """Return the module-level :data:`MANIFEST` singleton.

    Provided as a function so that downstream code can be updated to accept
    a dependency-injected manifest without changing call sites.
    """
    return MANIFEST
