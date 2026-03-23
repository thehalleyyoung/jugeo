"""Package manifest for trust_certificates chapter — Theory2 Ch6.

This module defines the manifest for Chapter 6 of theory2.tex, covering Trust,
Provenance, Evidence, and Certificates in the JuGeo geometric verification system.

Trust is modelled as an ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) where:
  - E_adm is the set of admissible evidence configurations
  - ≼ is the partial order (trust dominance)
  - ⊕ is evidence composition
  - ⊖ is trust attenuation
  - ↑_π is trust promotion (requires explicit justification — no silent promotion)
  - ↓_χ is trust demotion / ceiling enforcement

Author: copilot
Reference: theory2.tex Chapter 6
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
    from jugeo.package_manifest import PackageManifest, CapabilityFlag
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CHAPTER_NUMBER: int = 6
CHAPTER_STAGE: str = "chapter-06"
CHAPTER_SEQUENCE_START: int = 104
CHAPTER_TITLE: str = "Trust, Provenance, Evidence, and Certificates"
CHAPTER_SUMMARY: str = (
    "Chapter 6 develops the algebraic theory of trust as a partially ordered "
    "structure on admissible evidence configurations. It introduces the full "
    "manifest tuple (J, O, E, X, K, η, σ) and proves that certificates must "
    "faithfully project this tuple — no silent promotion, no residual erasure. "
    "Evidence plurality requires each clause type to be discharged by its "
    "authorised channel only."
)

THEOREM_TARGETS: Tuple[str, ...] = (
    "monotonicity_under_admissible_aggregation",
    "no_silent_promotion",
    "challenge_conservativity",
    "evidence_plurality_soundness",
    "certificate_faithful_projection",
    "manifest_consistency",
    "provenance_acyclicity",
)

EXPORTED_SYMBOLS: MappingProxyType[str, str] = MappingProxyType({
    "TrustAlgebraModel": "Algebraic model wrapping TrustAlgebra with admissibility tracking",
    "ProvenanceModel": "Append-only provenance chain manager",
    "EvidenceModel": "Evidence item store keyed by (coordinate, channel)",
    "CertificateModel": "Certificate collection with faithful-projection validation",
    "TrustCertificatesManifest": "Chapter-level manifest dataclass",
    "EvidenceChannel": "Enum of evidence discharge channels",
    "ChannelJurisdiction": "Maps clause types to authorised channels",
    "ClauseType": "Enum of clause types requiring different discharge channels",
    "ProofSolverInterface": "Routes discharge requests to appropriate channels",
    "EvidenceBundle": "Collection of channel-tagged evidence items",
    "PluralityChecker": "Verifies evidence plurality requirements",
    "AdmissibleConfig": "Validated evidence configuration in the trust algebra",
    "TrustOrderRelation": "Implements ≼ partial order on AdmissibleConfig",
    "TrustComposition": "Implements ⊕ composition on trust levels",
    "TrustAttenuation": "Implements ⊖ attenuation operation",
    "TrustPromotion": "Implements ↑_π promotion with audit trail",
    "TrustDemotion": "Implements ↓_χ ceiling enforcement",
    "TrustAlgebraInstance": "Integrates all trust algebra operations",
    "ManifestProjection": "Projects manifest tuple to certificate",
    "FaithfulnessChecker": "Validates certificate faithfulness",
    "CertificateProjector": "Creates certificates from judgment states",
    "ProjectionRecord": "Immutable record of a projection event",
    "ResidualPreserver": "Ensures residuals are not silently erased",
    "ObstructionRecord": "Persistent cohomological obstruction record",
    "ManifestTuple": "Full manifest (J, O, E, X, K, η, σ)",
    "EpochMap": "Maps coordinates to verification epochs",
    "InvalidationGraph": "Directed causal invalidation graph",
    "ManifestValidator": "Internal consistency checker for ManifestTuple",
    "IntegrityReport": "Result of manifest validation",
    "ManifestSerializer": "JSON serialization of ManifestTuple",
    "TrustResolutionAlgorithm": "Resolves trust for evidence item sets",
    "ProvenanceChainBuilder": "Builds complete provenance chains",
    "CertificateIssuanceAlgorithm": "Full certificate issuance pipeline",
    "EvidenceAggregationAlgorithm": "Multi-channel evidence aggregation",
    "TrustPathFinder": "Finds trust paths in provenance DAG",
    "BatchCertificationPipeline": "Processes multiple judgments in dependency order",
    "TrustCertificatesIntegration": "Main integration surface",
    "EvidenceBridge": "Bridges trust_certificates with jugeo.evidence.*",
    "JudgmentBridge": "Bridges with jugeo.judgments.*",
    "GeometryBridge": "Bridges with jugeo.geometry.*",
    "IntegrationConfig": "Integration configuration",
    "IntegrationReport": "Integration validation result",
    "TheoremStatement": "Immutable formal theorem record",
    "TheoremRegistry": "Collection of all Ch6 theorems",
    "ProofChecker": "Checks certificate satisfies theorem requirements",
})

CHAPTER_DEPENDS_ON: Tuple[str, ...] = (
    "jugeo.evidence.trust",
    "jugeo.evidence.provenance",
    "jugeo.evidence.certificates",
    "jugeo.evidence.manifests",
    "jugeo.judgments.judgment_terms",
    "jugeo.errors",
    "jugeo.package_manifest",
)

CHAPTER_CAPABILITIES: Tuple[str, ...] = (
    "trust_algebra",
    "evidence_plurality",
    "provenance_chains",
    "certificate_projection",
    "manifest_integrity",
    "no_silent_promotion",
)

_CHAPTER_CREATED_AT: float = time.time()


@dataclass
class TrustCertificatesManifest:
    """Manifest dataclass for the trust_certificates chapter.

    Captures metadata, theorem targets, exported symbols, and capability
    declarations for Chapter 6 of theory2.tex.

    Attributes:
        chapter_number: Integer chapter identifier (6).
        stage: Pipeline stage tag (e.g. 'chapter-06').
        title: Human-readable chapter title.
        theory_source: Canonical theory document reference.
        depends_on: Modules this chapter depends on.
        theorem_targets: Names of theorems to be proved in this chapter.
        exported_symbols: Mapping of symbol name → description.
        capabilities: Declared capability flags.
        created_at: Unix timestamp when this manifest was instantiated.
        manifest_id: UUID for this manifest instance.
    """

    chapter_number: int = CHAPTER_NUMBER
    stage: str = CHAPTER_STAGE
    title: str = CHAPTER_TITLE
    theory_source: str = "theory2.tex"
    depends_on: Tuple[str, ...] = field(default_factory=lambda: CHAPTER_DEPENDS_ON)
    theorem_targets: Tuple[str, ...] = field(default_factory=lambda: THEOREM_TARGETS)
    exported_symbols: Dict[str, str] = field(default_factory=lambda: dict(EXPORTED_SYMBOLS))
    capabilities: Tuple[str, ...] = field(default_factory=lambda: CHAPTER_CAPABILITIES)
    created_at: float = field(default_factory=time.time)
    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # copilot: These class-level defaults mirror module constants for convenience.
    _EXPECTED_SEQUENCE_START: ClassVar[int] = CHAPTER_SEQUENCE_START
    _EXPECTED_STAGE: ClassVar[str] = CHAPTER_STAGE

    def validate(self) -> List[str]:
        """Validate the manifest, returning a list of violation messages.

        Returns:
            List of error strings; empty list means the manifest is valid.
        """
        violations: List[str] = []

        if self.chapter_number != CHAPTER_NUMBER:
            violations.append(
                f"chapter_number mismatch: expected {CHAPTER_NUMBER}, got {self.chapter_number}"
            )
        if self.stage != CHAPTER_STAGE:
            violations.append(
                f"stage mismatch: expected '{CHAPTER_STAGE}', got '{self.stage}'"
            )
        if not self.title:
            violations.append("title must not be empty")
        if not self.theory_source:
            violations.append("theory_source must not be empty")
        missing_theorems = [t for t in THEOREM_TARGETS if t not in self.theorem_targets]
        if missing_theorems:
            violations.append(f"missing theorem targets: {missing_theorems}")
        if not self.exported_symbols:
            violations.append("exported_symbols must not be empty")
        if not self.capabilities:
            violations.append("capabilities must not be empty")
        if self.created_at <= 0:
            violations.append("created_at must be a positive Unix timestamp")
        if not self.manifest_id:
            violations.append("manifest_id must not be empty")
        return violations

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the manifest to a plain Python dict.

        Returns:
            Dictionary suitable for JSON serialisation.
        """
        return {
            "chapter_number": self.chapter_number,
            "stage": self.stage,
            "title": self.title,
            "theory_source": self.theory_source,
            "depends_on": list(self.depends_on),
            "theorem_targets": list(self.theorem_targets),
            "exported_symbols": dict(self.exported_symbols),
            "capabilities": list(self.capabilities),
            "created_at": self.created_at,
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustCertificatesManifest":
        """Deserialise a manifest from a plain Python dict.

        Args:
            data: Dictionary previously produced by to_dict().

        Returns:
            TrustCertificatesManifest instance.

        Raises:
            KeyError: If required keys are missing.
            ValueError: If values fail validation.
        """
        instance = cls(
            chapter_number=int(data["chapter_number"]),
            stage=str(data["stage"]),
            title=str(data["title"]),
            theory_source=str(data.get("theory_source", "theory2.tex")),
            depends_on=tuple(data.get("depends_on", CHAPTER_DEPENDS_ON)),
            theorem_targets=tuple(data["theorem_targets"]),
            exported_symbols=dict(data.get("exported_symbols", {})),
            capabilities=tuple(data.get("capabilities", CHAPTER_CAPABILITIES)),
            created_at=float(data.get("created_at", time.time())),
            manifest_id=str(data.get("manifest_id", str(uuid.uuid4()))),
        )
        violations = instance.validate()
        if violations:
            raise ValueError(f"Manifest validation failed: {violations}")
        return instance

    def list_theorems(self) -> List[str]:
        """Return the list of theorem target names for this chapter.

        Returns:
            Sorted list of theorem name strings.
        """
        return sorted(self.theorem_targets)

    def is_complete(self) -> bool:
        """Check whether this manifest declares all expected theorems and symbols.

        A manifest is considered complete when:
        - All THEOREM_TARGETS are present in theorem_targets.
        - All EXPORTED_SYMBOLS keys are present in exported_symbols.
        - validate() returns no violations.

        Returns:
            True if complete, False otherwise.
        """
        if self.validate():
            return False
        for key in THEOREM_TARGETS:
            if key not in self.theorem_targets:
                return False
        for key in EXPORTED_SYMBOLS:
            if key not in self.exported_symbols:
                return False
        return True

    def summary_report(self) -> str:
        """Produce a human-readable summary of this manifest.

        Returns:
            Multi-line string describing the manifest state.
        """
        violations = self.validate()
        lines = [
            f"TrustCertificatesManifest",
            f"  Chapter: {self.chapter_number} — {self.title}",
            f"  Stage:   {self.stage}",
            f"  Source:  {self.theory_source}",
            f"  Theorems ({len(self.theorem_targets)}): {', '.join(self.list_theorems())}",
            f"  Symbols ({len(self.exported_symbols)}): {len(self.exported_symbols)} declared",
            f"  Complete: {self.is_complete()}",
            f"  Valid:    {not violations}",
        ]
        if violations:
            lines.append(f"  Violations:")
            for v in violations:
                lines.append(f"    - {v}")
        return "\n".join(lines)


def build_chapter_manifest() -> TrustCertificatesManifest:
    """Factory function: build and validate the Chapter 6 manifest.

    Returns:
        A fully populated and validated TrustCertificatesManifest.

    Raises:
        ValueError: If the built manifest fails validation.
    """
    manifest = TrustCertificatesManifest(
        chapter_number=CHAPTER_NUMBER,
        stage=CHAPTER_STAGE,
        title=CHAPTER_TITLE,
        theory_source="theory2.tex",
        depends_on=CHAPTER_DEPENDS_ON,
        theorem_targets=THEOREM_TARGETS,
        exported_symbols=dict(EXPORTED_SYMBOLS),
        capabilities=CHAPTER_CAPABILITIES,
    )
    violations = manifest.validate()
    if violations:
        raise ValueError(
            f"Chapter manifest failed validation: {violations}"
        )
    return manifest


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-built)
# ---------------------------------------------------------------------------

_MODULE_MANIFEST: Optional[TrustCertificatesManifest] = None


def get_module_manifest() -> TrustCertificatesManifest:
    """Return (or lazily build) the module-level manifest singleton.

    Returns:
        The module-level TrustCertificatesManifest.
    """
    global _MODULE_MANIFEST
    if _MODULE_MANIFEST is None:
        _MODULE_MANIFEST = build_chapter_manifest()
    return _MODULE_MANIFEST


def chapter_info() -> Dict[str, Any]:
    """Return a compact dict of chapter metadata.

    Returns:
        Dict with chapter_number, stage, title, sequence_start.
    """
    return {
        "chapter_number": CHAPTER_NUMBER,
        "stage": CHAPTER_STAGE,
        "sequence_start": CHAPTER_SEQUENCE_START,
        "title": CHAPTER_TITLE,
        "summary": CHAPTER_SUMMARY,
    }


# ---------------------------------------------------------------------------
# TheoremStatement and TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TheoremStatement:
    """Immutable formal theorem record for Chapter 6.

    Attributes:
        name: Unique theorem identifier (snake_case).
        statement: Plain-language statement of the theorem.
        section: Section of theory2.tex where the theorem appears.
        depends_on: Theorem names that must hold for this one to be proved.
        tags: Informal classification tags.
    """

    name: str
    statement: str
    section: str
    depends_on: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to plain dict.

        Returns:
            Dictionary representation of this theorem statement.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "section": self.section,
            "depends_on": list(self.depends_on),
            "tags": list(self.tags),
        }


# Canonical theorem registry for Chapter 6
_CHAPTER_THEOREMS: Tuple[TheoremStatement, ...] = (
    TheoremStatement(
        name="monotonicity_under_admissible_aggregation",
        statement=(
            "If e₁ ≼ e₂ in E_adm and f is an admissible aggregation function, "
            "then f(e₁) ≼ f(e₂).  Trust order is monotone under admissible aggregation."
        ),
        section="6.2",
        depends_on=(),
        tags=("monotonicity", "order", "aggregation"),
    ),
    TheoremStatement(
        name="no_silent_promotion",
        statement=(
            "For every promotion ↑_π(e) in the system, there exists a non-empty "
            "justification record j and a named policy π such that the audit log "
            "records (e, ↑_π(e), j, π).  No promotion occurs without an explicit "
            "justification trail."
        ),
        section="6.3",
        depends_on=("monotonicity_under_admissible_aggregation",),
        tags=("promotion", "audit", "no_silent_promotion"),
    ),
    TheoremStatement(
        name="challenge_conservativity",
        statement=(
            "Issuing a challenge C against a claim at trust level t does not "
            "increase the trust level of any evidence item.  Challenge issuance "
            "is trust-conservative: it can only decrease or preserve trust."
        ),
        section="6.4",
        depends_on=("no_silent_promotion",),
        tags=("challenge", "conservativity", "demotion"),
    ),
    TheoremStatement(
        name="evidence_plurality_soundness",
        statement=(
            "A judgment J is sound under evidence plurality if and only if for "
            "every clause type τ ∈ clauses(J), there exists at least one admissible "
            "evidence item e with channel(e) = authorised_channel(τ).  No clause "
            "may be discharged by an unauthorised channel."
        ),
        section="6.5",
        depends_on=("monotonicity_under_admissible_aggregation",),
        tags=("plurality", "soundness", "channels"),
    ),
    TheoremStatement(
        name="certificate_faithful_projection",
        statement=(
            "A certificate C faithfully projects the manifest tuple (J, O, E, X, K, η, σ) "
            "if and only if: (i) trust(C) ≼ composed_trust(E), (ii) residuals(C) ⊇ "
            "open_residuals(J), and (iii) obstructions(C) ⊇ obstructions(J).  "
            "No silent strengthening or residual erasure is permitted."
        ),
        section="6.6",
        depends_on=(
            "no_silent_promotion",
            "evidence_plurality_soundness",
        ),
        tags=("certificate", "faithfulness", "projection"),
    ),
    TheoremStatement(
        name="manifest_consistency",
        statement=(
            "The manifest tuple (J, O, E, X, K, η, σ) is internally consistent if "
            "and only if: (i) E is admissible, (ii) K is acyclic, (iii) η maps each "
            "coordinate to a valid epoch, and (iv) σ is a valid signature over "
            "(J, O, E, X, K, η).  Consistency is decidable in polynomial time."
        ),
        section="6.7",
        depends_on=(
            "certificate_faithful_projection",
            "provenance_acyclicity",
        ),
        tags=("manifest", "consistency", "decidability"),
    ),
    TheoremStatement(
        name="provenance_acyclicity",
        statement=(
            "The provenance graph K = (V, E_K) of any well-formed judgment is a "
            "directed acyclic graph (DAG).  Cycles in provenance are forbidden because "
            "they would create circular justification, undermining the partial order ≼."
        ),
        section="6.2",
        depends_on=(),
        tags=("provenance", "DAG", "acyclicity"),
    ),
)


@dataclass
class TheoremRegistry:
    """Collection of all Chapter 6 theorem statements.

    Attributes:
        theorems: Mapping theorem name → TheoremStatement.
    """

    theorems: Dict[str, TheoremStatement] = field(
        default_factory=lambda: {t.name: t for t in _CHAPTER_THEOREMS}
    )

    def get(self, name: str) -> Optional[TheoremStatement]:
        """Retrieve a theorem by name.

        Args:
            name: Theorem name.

        Returns:
            TheoremStatement if found, None otherwise.
        """
        return self.theorems.get(name)

    def names(self) -> List[str]:
        """Return sorted list of all theorem names.

        Returns:
            Sorted list of strings.
        """
        return sorted(self.theorems.keys())

    def by_tag(self, tag: str) -> List[TheoremStatement]:
        """Return theorems that carry a given tag.

        Args:
            tag: Tag string to filter by.

        Returns:
            List of matching TheoremStatement objects.
        """
        return [t for t in self.theorems.values() if tag in t.tags]

    def dependency_order(self) -> List[str]:
        """Return theorem names in a valid proof order (dependencies first).

        Uses a simple topological sort (Kahn's algorithm).

        Returns:
            List of theorem names in dependency order.

        Raises:
            ValueError: If a cycle is detected (should never happen for well-formed data).
        """
        in_degree: Dict[str, int] = {name: 0 for name in self.theorems}
        for thm in self.theorems.values():
            for dep in thm.depends_on:
                if dep in in_degree:
                    in_degree[thm.name] += 1
        queue = [n for n, d in in_degree.items() if d == 0]
        order: List[str] = []
        while queue:
            queue.sort()  # deterministic ordering
            name = queue.pop(0)
            order.append(name)
            for thm in self.theorems.values():
                if name in thm.depends_on:
                    in_degree[thm.name] -= 1
                    if in_degree[thm.name] == 0:
                        queue.append(thm.name)
        if len(order) != len(self.theorems):
            raise ValueError("Cycle detected in theorem dependency graph")
        return order

    def to_list(self) -> List[Dict[str, Any]]:
        """Serialise all theorems to a list of dicts.

        Returns:
            List of theorem dicts in dependency order.
        """
        ordered = self.dependency_order()
        return [self.theorems[n].to_dict() for n in ordered]


def get_theorem_registry() -> TheoremRegistry:
    """Return a fresh TheoremRegistry populated with all Chapter 6 theorems.

    Returns:
        TheoremRegistry instance.
    """
    return TheoremRegistry()
