r"""Research integration: connecting thesis claims to implementation artifacts.

This module provides the integration layer that maps abstract thesis claims
to concrete implementation artifacts across the ``jugeo`` package tree.  It
answers the question: *"Given a thesis claim, where is it implemented?"* — and
conversely, *"Given a module or class, which claim does it support?"*

The integration layer is essential for:

1. **Traceability** — reviewers can navigate from a theory statement to the
   implementing code in one step.
2. **Coverage analysis** — automated tools can check whether every claim has
   at least one implementing artifact.
3. **Impact analysis** — changes to a module can be traced to the claims they
   affect, prompting re-verification.
4. **CI gating** — the integration map can be included in CI to detect
   coverage regressions.

Copilot-assisted artifacts
--------------------------

Artifacts that were partially generated with copilot assistance are flagged
with ``copilot_assisted=True``.  The integration layer tracks these so that
coverage analysis tools can weight them differently: a copilot-assisted
implementation of a claim carries ``COPILOT_SUGGESTED`` trust until reviewed.

Theory alignment
----------------

Section 280 of Theory2.tex describes the integration requirements.  This
module is the Python implementation of the theory-to-code map.
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


class ArtifactKind(Enum):
    """Kind of implementation artifact."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    TEST = "test"
    THEOREM_PROOF = "theorem_proof"
    DOCUMENTATION = "documentation"
    BENCHMARK = "benchmark"


class IntegrationStatus(Enum):
    """Status of the integration between a claim and an artifact."""

    LINKED = "linked"
    PARTIAL = "partial"
    STUB = "stub"
    MISSING = "missing"
    DEPRECATED = "deprecated"


class ClaimArtifactRelation(Enum):
    """Relationship type between a claim and an artifact."""

    IMPLEMENTS = "implements"
    TESTS = "tests"
    DOCUMENTS = "documents"
    FALSIFIES = "falsifies"
    DEPENDS_ON = "depends_on"
    EXTENDS = "extends"


# ---------------------------------------------------------------------------
# ArtifactReference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactReference:
    """A reference to a concrete implementation artifact.

    Parameters
    ----------
    artifact_id:
        Short identifier, e.g. ``"AR-C1.JudgmentPresheaf"``.
    kind:
        :class:`ArtifactKind`.
    dotted_path:
        Fully qualified Python path, e.g.
        ``"jugeo.thesis.research_program.representation.JudgmentPresheaf"``.
    description:
        What this artifact does.
    status:
        :class:`IntegrationStatus` of the link.
    copilot_assisted:
        Whether copilot assistance was used in creating this artifact.
    relation:
        How this artifact relates to its claim.
    theory_section:
        Theory2.tex section that this artifact implements.
    created_at:
        Unix timestamp.
    """

    artifact_id: str
    kind: ArtifactKind
    dotted_path: str
    description: str
    status: IntegrationStatus
    copilot_assisted: bool
    relation: ClaimArtifactRelation
    theory_section: str
    created_at: float = field(default_factory=time.time)

    def is_complete(self) -> bool:
        """Return True if the artifact is fully linked and not a stub."""
        return self.status == IntegrationStatus.LINKED

    def short_name(self) -> str:
        """Return the last component of the dotted path."""
        return self.dotted_path.rsplit(".", 1)[-1]

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of the artifact reference."""
        raw = json.dumps(
            {"path": self.dotted_path, "kind": self.kind.value},
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "dotted_path": self.dotted_path,
            "description": self.description,
            "status": self.status.value,
            "copilot_assisted": self.copilot_assisted,
            "relation": self.relation.value,
            "theory_section": self.theory_section,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# ClaimArtifactLink
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimArtifactLink:
    """A directed link from a thesis claim to an implementation artifact.

    Parameters
    ----------
    link_id:
        Unique identifier.
    claim_id:
        The claim identifier (e.g. ``"C1"``).
    artifact:
        The :class:`ArtifactReference` being linked.
    strength:
        How strongly this artifact covers the claim: ``"primary"``,
        ``"supporting"``, or ``"auxiliary"``.
    notes:
        Optional notes about the link.
    """

    link_id: str
    claim_id: str
    artifact: ArtifactReference
    strength: str
    notes: str = ""

    _VALID_STRENGTHS = frozenset(["primary", "supporting", "auxiliary"])

    def __post_init__(self) -> None:
        if self.strength not in self._VALID_STRENGTHS:
            raise ValueError(
                f"strength must be one of {sorted(self._VALID_STRENGTHS)}, "
                f"got {self.strength!r}"
            )

    def is_primary(self) -> bool:
        """Return True if this is a primary implementation link."""
        return self.strength == "primary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "claim_id": self.claim_id,
            "artifact": self.artifact.to_dict(),
            "strength": self.strength,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# TheoryCodeMap
# ---------------------------------------------------------------------------


@dataclass
class TheoryCodeMap:
    """Maps Theory2.tex sections to their implementing Python artifacts.

    Parameters
    ----------
    name:
        Identifier.
    """

    name: str
    _section_to_artifacts: dict[str, list[ArtifactReference]] = field(
        default_factory=dict, repr=False
    )
    _path_to_section: dict[str, str] = field(default_factory=dict, repr=False)

    def register(self, section: str, artifact: ArtifactReference) -> None:
        """Register an artifact under a theory section."""
        self._section_to_artifacts.setdefault(section, []).append(artifact)
        self._path_to_section[artifact.dotted_path] = section

    def artifacts_for_section(self, section: str) -> list[ArtifactReference]:
        """Return all artifacts registered for a theory section."""
        return list(self._section_to_artifacts.get(section, []))

    def section_for_path(self, dotted_path: str) -> str | None:
        """Return the theory section for a given artifact path."""
        return self._path_to_section.get(dotted_path)

    def coverage_report(self) -> dict[str, Any]:
        """Return a coverage report: sections with and without artifacts."""
        covered = sorted(self._section_to_artifacts.keys())
        stub_sections = [
            sec
            for sec, arts in self._section_to_artifacts.items()
            if all(a.status == IntegrationStatus.STUB for a in arts)
        ]
        return {
            "name": self.name,
            "n_sections_mapped": len(covered),
            "n_stub_only_sections": len(stub_sections),
            "covered_sections": covered,
            "stub_sections": stub_sections,
        }

    def all_artifacts(self) -> list[ArtifactReference]:
        """Return a flat list of all registered artifacts."""
        result: list[ArtifactReference] = []
        for arts in self._section_to_artifacts.values():
            result.extend(arts)
        return result

    def copilot_assisted_fraction(self) -> float:
        """Return the fraction of artifacts that are copilot-assisted."""
        all_arts = self.all_artifacts()
        if not all_arts:
            return 0.0
        return sum(1 for a in all_arts if a.copilot_assisted) / len(all_arts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "coverage": self.coverage_report(),
            "copilot_assisted_fraction": self.copilot_assisted_fraction(),
            "sections": {
                sec: [a.to_dict() for a in arts]
                for sec, arts in self._section_to_artifacts.items()
            },
        }


# ---------------------------------------------------------------------------
# ResearchIntegration — root integration object
# ---------------------------------------------------------------------------


@dataclass
class ResearchIntegration:
    """Connects thesis claims to implementation artifacts.

    Parameters
    ----------
    name:
        Identifier for this integration instance.
    """

    name: str
    theory_code_map: TheoryCodeMap = field(
        default_factory=lambda: TheoryCodeMap(name="theory_code_map")
    )
    _links: list[ClaimArtifactLink] = field(default_factory=list, repr=False)
    _by_claim: dict[str, list[ClaimArtifactLink]] = field(
        default_factory=dict, repr=False
    )
    _by_artifact_path: dict[str, list[ClaimArtifactLink]] = field(
        default_factory=dict, repr=False
    )

    def add_link(self, link: ClaimArtifactLink) -> None:
        """Register a claim-artifact link."""
        self._links.append(link)
        self._by_claim.setdefault(link.claim_id, []).append(link)
        self._by_artifact_path.setdefault(link.artifact.dotted_path, []).append(link)
        self.theory_code_map.register(link.artifact.theory_section, link.artifact)

    def links_for_claim(self, claim_id: str) -> list[ClaimArtifactLink]:
        """Return all links for the given claim."""
        return list(self._by_claim.get(claim_id, []))

    def primary_links_for_claim(self, claim_id: str) -> list[ClaimArtifactLink]:
        """Return only primary links for the given claim."""
        return [l for l in self.links_for_claim(claim_id) if l.is_primary()]

    def claims_for_artifact(self, dotted_path: str) -> list[str]:
        """Return claim IDs that are linked to the given artifact path."""
        return list({
            l.claim_id
            for l in self._by_artifact_path.get(dotted_path, [])
        })

    def coverage_by_claim(self) -> dict[str, dict[str, Any]]:
        """Return a per-claim coverage summary."""
        result: dict[str, dict[str, Any]] = {}
        for claim_id, links in self._by_claim.items():
            primary = [l for l in links if l.is_primary()]
            stubs = [l for l in links if l.artifact.status == IntegrationStatus.STUB]
            copilot = [l for l in links if l.artifact.copilot_assisted]
            result[claim_id] = {
                "n_links": len(links),
                "n_primary": len(primary),
                "n_stubs": len(stubs),
                "n_copilot_assisted": len(copilot),
                "has_primary_implementation": bool(primary),
            }
        return result

    def claims_without_primary_artifact(self) -> list[str]:
        """Return claim IDs that have no primary implementation artifact."""
        return [
            cid
            for cid, summary in self.coverage_by_claim().items()
            if not summary["has_primary_implementation"]
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_links": len(self._links),
            "coverage_by_claim": self.coverage_by_claim(),
            "claims_without_primary": self.claims_without_primary_artifact(),
            "theory_code_map": self.theory_code_map.to_dict(),
        }


# ---------------------------------------------------------------------------
# Canonical integration map
# ---------------------------------------------------------------------------


def _ar(
    artifact_id: str,
    kind: ArtifactKind,
    dotted_path: str,
    description: str,
    status: IntegrationStatus,
    copilot_assisted: bool,
    relation: ClaimArtifactRelation,
    section: str,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact_id,
        kind=kind,
        dotted_path=dotted_path,
        description=description,
        status=status,
        copilot_assisted=copilot_assisted,
        relation=relation,
        theory_section=section,
    )


def _link(
    link_id: str,
    claim_id: str,
    artifact: ArtifactReference,
    strength: str,
    notes: str = "",
) -> ClaimArtifactLink:
    return ClaimArtifactLink(
        link_id=link_id,
        claim_id=claim_id,
        artifact=artifact,
        strength=strength,
        notes=notes,
    )


def build_canonical_integration() -> ResearchIntegration:
    """Construct the canonical integration map for JuGeo Ch. 2.

    Returns
    -------
    ResearchIntegration
        A fully populated integration object linking all four claims to their
        implementing artifacts.
    """
    ri = ResearchIntegration(name="jugeo_ch2_integration")

    # C1 artifacts
    c1_presheaf = _ar(
        "AR-C1.JudgmentPresheaf",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.representation.JudgmentPresheaf",
        "Implements the judgment presheaf and verifies presheaf laws",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§231",
    )
    c1_coord = _ar(
        "AR-C1.CoordinateSystem",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.representation.CoordinateSystem",
        "Assigns coordinates to semantic states and verifies injectivity",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§232",
    )
    c1_cover = _ar(
        "AR-C1.CoverStructure",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.representation.CoverStructure",
        "Manages the cover and verifies locality and gluing",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§233",
    )
    c1_repr = _ar(
        "AR-C1.SemanticStateRepresentation",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.representation.SemanticStateRepresentation",
        "Top-level C1 verification orchestrator",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§230",
    )
    ri.add_link(_link("L-C1.1", "C1", c1_repr, "primary",
                       "Top-level C1 claim implementation"))
    ri.add_link(_link("L-C1.2", "C1", c1_presheaf, "primary",
                       "Presheaf law verification — copilot assisted"))
    ri.add_link(_link("L-C1.3", "C1", c1_coord, "primary"))
    ri.add_link(_link("L-C1.4", "C1", c1_cover, "supporting"))

    # C2 artifacts
    c2_boundary = _ar(
        "AR-C2.ChannelBoundary",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mixed_evidence.ChannelBoundary",
        "Enforces copilot trust ceiling at the channel boundary",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§241",
    )
    c2_jmap = _ar(
        "AR-C2.JurisdictionMap",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mixed_evidence.JurisdictionMap",
        "Declares channel jurisdictions",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§241",
    )
    c2_fed = _ar(
        "AR-C2.FederationProtocol",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mixed_evidence.FederationProtocol",
        "Implements the ⊕ federation operation with kind preservation",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§242",
    )
    c2_plurality = _ar(
        "AR-C2.EvidencePlurality",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mixed_evidence.EvidencePlurality",
        "Multi-channel evidence collection with ingestion enforcement",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§240",
    )
    ri.add_link(_link("L-C2.1", "C2", c2_fed, "primary",
                       "Primary federation implementation"))
    ri.add_link(_link("L-C2.2", "C2", c2_boundary, "primary",
                       "Copilot ceiling enforcement"))
    ri.add_link(_link("L-C2.3", "C2", c2_jmap, "supporting"))
    ri.add_link(_link("L-C2.4", "C2", c2_plurality, "supporting"))

    # C3 artifacts
    c3_spec = _ar(
        "AR-C3.OrchestratorSpecification",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.long_horizon_orchestration.OrchestratorSpecification",
        "Full orchestrator spec with control law and convergence condition",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§250",
    )
    c3_law = _ar(
        "AR-C3.ControlLawDefinition",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.long_horizon_orchestration.ControlLawDefinition",
        "Orchestrator control law with policy-based action selection",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§253",
    )
    c3_conv = _ar(
        "AR-C3.ConvergenceCondition",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.long_horizon_orchestration.ConvergenceCondition",
        "Lyapunov convergence verification",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§252",
    )
    ri.add_link(_link("L-C3.1", "C3", c3_spec, "primary",
                       "Copilot advisory notes present; reviewed"))
    ri.add_link(_link("L-C3.2", "C3", c3_law, "primary"))
    ri.add_link(_link("L-C3.3", "C3", c3_conv, "primary"))

    # C4 artifacts
    c4_engine = _ar(
        "AR-C4.DiscoveryEngine",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mathematical_ideation.DiscoveryEngine",
        "Ideation loop: generate, evaluate, accept, record",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§263",
    )
    c4_novelty = _ar(
        "AR-C4.NoveltyMeasure",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mathematical_ideation.NoveltyMeasure",
        "Computes μ(s) for candidate structures",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§261",
    )
    c4_purpose = _ar(
        "AR-C4.PurposeCondition",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mathematical_ideation.PurposeCondition",
        "Verifies purpose condition P for candidate structures",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§262",
    )
    c4_spec = _ar(
        "AR-C4.IdeationSpec",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.mathematical_ideation.IdeationSpec",
        "Ideation task specification including copilot guidance notes",
        IntegrationStatus.LINKED,
        copilot_assisted=True,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§260",
    )
    ri.add_link(_link("L-C4.1", "C4", c4_engine, "primary",
                       "Copilot assisted in generator scaffolding"))
    ri.add_link(_link("L-C4.2", "C4", c4_novelty, "primary"))
    ri.add_link(_link("L-C4.3", "C4", c4_purpose, "primary"))
    ri.add_link(_link("L-C4.4", "C4", c4_spec, "supporting"))

    # Cross-cutting: algorithms and theorems
    alg = _ar(
        "AR-ALG.ResearchAlgorithms",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.algorithms.ResearchAlgorithms",
        "Stateful container for all three research algorithms",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.IMPLEMENTS,
        section="§270",
    )
    thm = _ar(
        "AR-THM.TheoremCatalog",
        ArtifactKind.CLASS,
        "jugeo.thesis.research_program.theorems.TheoremCatalog",
        "Catalog of theorems and lemmas from Ch. 2",
        IntegrationStatus.LINKED,
        copilot_assisted=False,
        relation=ClaimArtifactRelation.DOCUMENTS,
        section="§290",
    )
    for cid in ("C1", "C2", "C3", "C4"):
        ri.add_link(_link(f"L-ALG.{cid}", cid, alg, "auxiliary"))
        ri.add_link(_link(f"L-THM.{cid}", cid, thm, "auxiliary"))

    return ri
