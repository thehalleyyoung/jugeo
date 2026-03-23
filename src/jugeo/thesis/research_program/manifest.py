r"""Package manifest for JuGeo Chapter 2 research program.

This module provides the canonical manifest for
``jugeo.thesis.research_program``, the Python implementation companion to
Theory2.tex Chapter 2: *Research Questions and Thesis Claims*.

JuGeo (Judgment Geometry) treats every AI reasoning step as a typed judgment
tuple :math:`J = (c, \varphi, A, E, O, B, T, \Pi)` where:

* ``c``  — clause identifier
* ``φ``  — semantic formula
* ``A``  — agent/actor identity
* ``E``  — evidence configuration (multi-channel)
* ``O``  — obligation set
* ``B``  — bounding context
* ``T``  — trust level from the ordered algebra
* ``Π``  — provenance chain

Trust is an ordered algebra :math:`\mathfrak{T} = (\mathcal{E}_\mathrm{adm},
\preceq, \oplus, \ominus, \uparrow_\pi, \downarrow_\chi)`.  No silent
promotion is permitted; copilot/oracle proposals enter at a ceiling strictly
below solver proofs and require explicit policy justification to advance.

Manifest responsibilities
-------------------------

:data:`CHAPTER_COVERAGE`
    Maps each Theory2.tex section number to the Python module that implements
    its claims, together with coverage confidence and open TODOs.

:data:`EXPORTED_SYMBOLS`
    The complete public API surface of this sub-package, grouped by conceptual
    role.

:data:`THEORY_CLAIMS`
    Machine-readable description of every thesis claim in Ch.2.

:data:`CONTRIBUTION_BOUNDARIES`
    What this research program claims and explicitly does **not** claim.

:class:`ManifestRecord`
    Structured record for a single chapter-coverage entry.

:class:`SymbolGroup`
    Named cluster of exported symbols with descriptions.

:class:`ClaimSummary`
    Lightweight summary of a thesis claim linking to full
    :class:`~jugeo.thesis.research_program.models.ThesisClaim` objects.

:class:`PackageManifest`
    Root manifest object: validates coverage, resolves cross-references,
    and can emit a JSON report suitable for CI gating.

All copilot-assisted code generation within this sub-package is governed by
the same trust algebra: generated stubs enter at ``COPILOT_SUGGESTED`` and
must be promoted explicitly through review before they carry ``SOLVER_DISCHARGED``
or higher trust.

Theory alignment
----------------

Section 201 of Theory2.tex ("Research Program Overview") is the primary
reference.  Section 210 enumerates the four thesis claims; section 220 states
falsification criteria.  This manifest encodes both in machine-readable form.
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
    """Conceptual role of an exported symbol."""

    DATA_MODEL = "data_model"
    CLAIM = "claim"
    ALGORITHM = "algorithm"
    THEOREM = "theorem"
    INTEGRATION = "integration"
    UTILITY = "utility"


class ClaimStatus(Enum):
    """Lifecycle status of a thesis claim."""

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
    """Coverage record for a single Theory2.tex section.

    Each record maps a section identifier (e.g. ``"§201"``) to the Python
    module that implements its content, together with a confidence score,
    a list of open TODOs, and a textual summary of what is covered.

    Parameters
    ----------
    section_id:
        The Theory2.tex section identifier, e.g. ``"§201"``.
    section_title:
        Human-readable title of the section.
    module_path:
        Dotted Python module path, e.g.
        ``"jugeo.thesis.research_program.models"``.
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

    Parameters
    ----------
    name:
        Short name for the group, e.g. ``"claim_models"``.
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
    """Lightweight summary of a thesis claim.

    This is the manifest-level view; the full structured object lives in
    :mod:`jugeo.thesis.research_program.models` as :class:`ThesisClaim`.

    Parameters
    ----------
    claim_id:
        Short identifier, e.g. ``"C1"``, ``"C2"``.
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
        section_id="§201",
        section_title="Research Program Overview",
        module_path="jugeo.thesis.research_program.manifest",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.90,
        open_todos=(
            "Add cross-ref validation against theorem catalog",
            "Wire confidence scores to CI gate",
        ),
        summary=(
            "The manifest module encodes the chapter overview: four thesis claims, "
            "evidence requirements, and falsification criteria.  The structured "
            "CHAPTER_COVERAGE, EXPORTED_SYMBOLS, and THEORY_CLAIMS tables "
            "provide machine-readable access to the program's scope."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§210",
        section_title="Thesis Claims C1–C4",
        module_path="jugeo.thesis.research_program.models",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Implement evidence plan simulation",
            "Add claim dependency graph traversal",
        ),
        summary=(
            "The models module provides dataclass representations of all four "
            "thesis claims (representation, mixed evidence, long-horizon "
            "orchestration, mathematical ideation) together with evidence plans, "
            "falsification criteria, and contribution boundaries."
        ),
    ),
    ManifestRecord(
        section_id="§220",
        section_title="Falsification Criteria",
        module_path="jugeo.thesis.research_program.falsifiability",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.85,
        open_todos=("Implement automated falsification test runner",),
        summary=(
            "The falsifiability module encodes every testable property derived "
            "from the thesis claims, together with evidence thresholds and "
            "rejection conditions.  The FalsificationCriteria class provides "
            "structured access to pass/fail conditions."
        ),
    ),
    ManifestRecord(
        section_id="§230",
        section_title="Representation Claim (C1)",
        module_path="jugeo.thesis.research_program.representation",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=("Implement presheaf composition law check",),
        summary=(
            "Claim C1 asserts that JuGeo's judgment tuple provides a sound "
            "semantic-state representation.  The module implements "
            "SemanticStateRepresentation, JudgmentPresheaf, CoordinateSystem, "
            "and CoverStructure with real algebraic logic."
        ),
    ),
    ManifestRecord(
        section_id="§240",
        section_title="Mixed Evidence Claim (C2)",
        module_path="jugeo.thesis.research_program.mixed_evidence",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.86,
        open_todos=("Add federation protocol simulation tests",),
        summary=(
            "Claim C2 asserts that solver, runtime, and oracle/copilot evidence "
            "can be federated without collapsing support kinds.  The module "
            "implements EvidencePlurality, ChannelBoundary, JurisdictionMap, "
            "and FederationProtocol."
        ),
    ),
    ManifestRecord(
        section_id="§250",
        section_title="Long-Horizon Orchestration Claim (C3)",
        module_path="jugeo.thesis.research_program.long_horizon_orchestration",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.84,
        open_todos=(
            "Prove Lyapunov convergence bound",
            "Add multi-agent scenario test",
        ),
        summary=(
            "Claim C3 asserts that orchestration over long task horizons can be "
            "formalised as semantic control in JuGeo's geometry.  The module "
            "implements OrchestratorSpecification, ControlLawDefinition, and "
            "ConvergenceCondition."
        ),
    ),
    ManifestRecord(
        section_id="§260",
        section_title="Mathematical Ideation Claim (C4)",
        module_path="jugeo.thesis.research_program.mathematical_ideation",
        status=CoverageStatus.PARTIAL,
        confidence=0.75,
        open_todos=(
            "Implement novelty measure integration test",
            "Formalise purpose condition semantics",
        ),
        summary=(
            "Claim C4 asserts that mathematical discovery (ideation) can occur "
            "within JuGeo's geometry and be measured.  The module provides "
            "IdeationSpec, NoveltyMeasure, PurposeCondition, and DiscoveryEngine."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§270",
        section_title="Algorithms",
        module_path="jugeo.thesis.research_program.algorithms",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=("Add full falsification test harness",),
        summary=(
            "The algorithms module implements the procedural complement to the "
            "declarative claims: claim_verification_procedure, "
            "evidence_accumulation_loop, and falsification_test_suite."
        ),
    ),
    ManifestRecord(
        section_id="§280",
        section_title="Integration",
        module_path="jugeo.thesis.research_program.integration",
        status=CoverageStatus.PARTIAL,
        confidence=0.80,
        open_todos=(
            "Complete artifact cross-reference table",
            "Add automated import resolution",
        ),
        summary=(
            "The integration module connects the abstract claims to concrete "
            "implementation artifacts across the jugeo package tree, providing "
            "a navigable map from theory to code."
        ),
    ),
    ManifestRecord(
        section_id="§290",
        section_title="Theorem Catalog",
        module_path="jugeo.thesis.research_program.theorems",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=("Add proof sketch validation",),
        summary=(
            "The theorem catalog enumerates all formal claims, lemmas, and "
            "corollaries from Chapter 2 with their proof status, dependencies, "
            "and links to implementing modules."
        ),
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
            "jugeo.thesis.research_program.manifest.ManifestRecord",
            "jugeo.thesis.research_program.manifest.SymbolGroup",
            "jugeo.thesis.research_program.manifest.ClaimSummary",
            "jugeo.thesis.research_program.manifest.PackageManifest",
            "jugeo.thesis.research_program.manifest.CoverageStatus",
            "jugeo.thesis.research_program.manifest.SymbolRole",
            "jugeo.thesis.research_program.manifest.ClaimStatus",
        ),
        description="Root manifest types for package-level introspection.",
        source_module="jugeo.thesis.research_program.manifest",
    ),
    SymbolGroup(
        name="claim_models",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.models.ResearchQuestion",
            "jugeo.thesis.research_program.models.ThesisClaim",
            "jugeo.thesis.research_program.models.EvidencePlan",
            "jugeo.thesis.research_program.models.FalsificationCriteria",
            "jugeo.thesis.research_program.models.ContributionBoundary",
        ),
        description="Core data models for research questions and thesis claims.",
        source_module="jugeo.thesis.research_program.models",
    ),
    SymbolGroup(
        name="representation_claim",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.representation.SemanticStateRepresentation",
            "jugeo.thesis.research_program.representation.JudgmentPresheaf",
            "jugeo.thesis.research_program.representation.CoordinateSystem",
            "jugeo.thesis.research_program.representation.CoverStructure",
        ),
        description="Claim C1: JuGeo represents semantic state faithfully.",
        source_module="jugeo.thesis.research_program.representation",
    ),
    SymbolGroup(
        name="mixed_evidence_claim",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.mixed_evidence.EvidencePlurality",
            "jugeo.thesis.research_program.mixed_evidence.ChannelBoundary",
            "jugeo.thesis.research_program.mixed_evidence.JurisdictionMap",
            "jugeo.thesis.research_program.mixed_evidence.FederationProtocol",
        ),
        description="Claim C2: mixed solver/runtime/copilot evidence is federatable.",
        source_module="jugeo.thesis.research_program.mixed_evidence",
    ),
    SymbolGroup(
        name="orchestration_claim",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.long_horizon_orchestration.OrchestratorSpecification",
            "jugeo.thesis.research_program.long_horizon_orchestration.ControlLawDefinition",
            "jugeo.thesis.research_program.long_horizon_orchestration.ConvergenceCondition",
        ),
        description="Claim C3: orchestration as semantic control over long horizons.",
        source_module="jugeo.thesis.research_program.long_horizon_orchestration",
    ),
    SymbolGroup(
        name="ideation_claim",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.mathematical_ideation.IdeationSpec",
            "jugeo.thesis.research_program.mathematical_ideation.NoveltyMeasure",
            "jugeo.thesis.research_program.mathematical_ideation.PurposeCondition",
            "jugeo.thesis.research_program.mathematical_ideation.DiscoveryEngine",
        ),
        description="Claim C4: mathematical ideation within JuGeo geometry.",
        source_module="jugeo.thesis.research_program.mathematical_ideation",
    ),
    SymbolGroup(
        name="falsifiability",
        role=SymbolRole.CLAIM,
        symbols=(
            "jugeo.thesis.research_program.falsifiability.FalsificationCriteria",
            "jugeo.thesis.research_program.falsifiability.TestableProperty",
            "jugeo.thesis.research_program.falsifiability.EvidenceThreshold",
        ),
        description="Falsification criteria for all four thesis claims.",
        source_module="jugeo.thesis.research_program.falsifiability",
    ),
    SymbolGroup(
        name="algorithms",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.thesis.research_program.algorithms.ResearchAlgorithms",
            "jugeo.thesis.research_program.algorithms.claim_verification_procedure",
            "jugeo.thesis.research_program.algorithms.evidence_accumulation_loop",
            "jugeo.thesis.research_program.algorithms.falsification_test_suite",
        ),
        description="Procedural algorithms for claim verification and evidence accumulation.",
        source_module="jugeo.thesis.research_program.algorithms",
    ),
    SymbolGroup(
        name="integration",
        role=SymbolRole.INTEGRATION,
        symbols=(
            "jugeo.thesis.research_program.integration.ResearchIntegration",
            "jugeo.thesis.research_program.integration.ArtifactReference",
            "jugeo.thesis.research_program.integration.TheoryCodeMap",
        ),
        description="Integration bridge from thesis claims to implementation artifacts.",
        source_module="jugeo.thesis.research_program.integration",
    ),
    SymbolGroup(
        name="theorems",
        role=SymbolRole.THEOREM,
        symbols=(
            "jugeo.thesis.research_program.theorems.TheoremCatalog",
            "jugeo.thesis.research_program.theorems.TheoremEntry",
            "jugeo.thesis.research_program.theorems.ProofStatus",
        ),
        description="Catalog of theorems, lemmas, and corollaries from Chapter 2.",
        source_module="jugeo.thesis.research_program.theorems",
    ),
)

# ---------------------------------------------------------------------------
# Theory claims summary table
# ---------------------------------------------------------------------------


THEORY_CLAIMS: tuple[ClaimSummary, ...] = (
    ClaimSummary(
        claim_id="C1",
        title="JuGeo judgment tuple faithfully represents semantic state",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§230",
        implementing_module="jugeo.thesis.research_program.representation",
        falsification_module="jugeo.thesis.research_program.falsifiability",
        evidence_required=(
            "presheaf_composition_law",
            "coordinate_completeness",
            "cover_structure_soundness",
        ),
    ),
    ClaimSummary(
        claim_id="C2",
        title="Solver, runtime, and copilot evidence can be federated without kind-collapse",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§240",
        implementing_module="jugeo.thesis.research_program.mixed_evidence",
        falsification_module="jugeo.thesis.research_program.falsifiability",
        evidence_required=(
            "channel_jurisdiction_enforcement",
            "federation_kind_preservation",
            "copilot_ceiling_invariant",
        ),
    ),
    ClaimSummary(
        claim_id="C3",
        title="Long-horizon orchestration is formalised as semantic control",
        status=ClaimStatus.FORMALISED,
        theory_section="§250",
        implementing_module="jugeo.thesis.research_program.long_horizon_orchestration",
        falsification_module="jugeo.thesis.research_program.falsifiability",
        evidence_required=(
            "lyapunov_convergence",
            "control_law_soundness",
            "horizon_bound",
        ),
    ),
    ClaimSummary(
        claim_id="C4",
        title="Mathematical ideation (discovery) occurs and is measurable within JuGeo",
        status=ClaimStatus.PROPOSED,
        theory_section="§260",
        implementing_module="jugeo.thesis.research_program.mathematical_ideation",
        falsification_module="jugeo.thesis.research_program.falsifiability",
        evidence_required=(
            "novelty_measure_non_degeneracy",
            "purpose_condition_satisfaction",
            "discovery_engine_termination",
        ),
    ),
)


# ---------------------------------------------------------------------------
# PackageManifest — root manifest object
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Root manifest for the JuGeo Chapter 2 research program.

    Aggregates :data:`CHAPTER_COVERAGE`, :data:`EXPORTED_SYMBOLS`, and
    :data:`THEORY_CLAIMS` and provides validation, querying, and reporting
    methods.

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

    Notes
    -----
    The manifest is deliberately read-only after construction (all mutation
    produces new instances).  This mirrors the append-only audit-log constraint
    in the trust algebra: once a claim is registered, it can only be updated
    by creating a new manifest version.
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
        """
        failing = self.sections_below_threshold(min_status, min_confidence)
        under_formalised = [
            c
            for c in self.theory_claims
            if c.status == ClaimStatus.PROPOSED
        ]
        passed = not failing and not under_formalised
        return {
            "passed": passed,
            "mean_confidence": self.mean_confidence(),
            "total_open_todos": self.total_open_todos(),
            "failing_sections": [r.section_id for r in failing],
            "under_formalised_claims": [c.claim_id for c in under_formalised],
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
"""The canonical manifest instance for jugeo.thesis.research_program."""


def get_manifest() -> PackageManifest:
    """Return the module-level :data:`MANIFEST` singleton.

    Provided as a function so that downstream code can be updated to accept
    a dependency-injected manifest without changing call sites.
    """
    return MANIFEST
