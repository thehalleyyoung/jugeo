"""Tests for stable JuGeo judgment and section exports.

These tests are intentionally richer than a narrow smoke check because
``preliminaries/theory2.tex`` asks the shared foundation to preserve a fairly
strict doctrine:

* judgments are canonical semantic authorities,
* sections are the real scope-bearing products,
* public exports are faithful but explicitly lossy projections,
* residual obligations must remain visible,
* provenance must survive serialization, and
* certificate-like surfaces must not silently overstate closure.

The goal of the suite is therefore twofold.  First, it protects backward
compatibility for the rest of shared JuGeo code, especially the API and
certificate modules that import ``ExportRecord``.  Second, it pins down the
more direct theory2 semantics introduced by the rewrite: stable judgment and
section exports, residual visibility, and provenance-preserving serialization
surfaces.  The tests are written in a straightforward style so that humans and
LLMs can quickly infer the contract of ``jugeo.judgments.exports``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.certificates import CertificateStatus, emit_certificate
from jugeo.evidence.channels import EvidenceKind, EvidenceRecord, build_channel
from jugeo.evidence.manifests import build_evidence_manifest
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.interfaces.api import JuGeoAPI
from jugeo.judgments.contexts import ContextBinding, SemanticContext
from jugeo.judgments.exports import (
    ClauseExport,
    ExportRecord,
    JudgmentExport,
    ProjectionKind,
    SectionExport,
    export_judgment,
    export_section,
)
from jugeo.judgments.judgment_terms import JudgmentClause, JudgmentStatus, LocalJudgment
from jugeo.judgments.sections import JudgmentSection


class CustomEvidence:
    """Small helper used to exercise normalization fallback paths."""

    def __repr__(self) -> str:
        return "CustomEvidence(token='copilot-proposal')"


def make_coordinate(*path: str) -> CoordinateObject:
    parts = path or ("coord",)
    return CoordinateObject(parts[-1], CoordinateKind.REGION, parts)


def make_context(coordinate: CoordinateObject | None = None) -> SemanticContext:
    coordinate = coordinate or make_coordinate()
    return SemanticContext(
        coordinate,
        (
            ContextBinding("x", 1),
            ContextBinding("mode", "analysis"),
        ),
    )


def make_clause(
    name: str,
    *,
    statement: str | None = None,
    satisfied: bool | None = True,
    evidence_channels: tuple[str, ...] = (),
    obligations: tuple[str, ...] = (),
) -> JudgmentClause:
    return JudgmentClause(
        name=name,
        statement=statement or f"{name} holds",
        satisfied=satisfied,
        evidence_channels=evidence_channels,
        obligations=obligations,
    )


def make_judgment(
    *,
    coordinate: CoordinateObject | None = None,
    proposition: str = "P",
    status: JudgmentStatus = JudgmentStatus.SETTLED,
    obligations: tuple[str, ...] = (),
    obstructions: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    provenance: tuple[str, ...] = ("capture", "review"),
    clauses: tuple[JudgmentClause, ...] = (),
    trust_vector: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
) -> LocalJudgment:
    coordinate = coordinate or make_coordinate()
    return LocalJudgment(
        coordinate,
        proposition,
        artifact or {"artifact": "x", "rank": 2},
        evidence_refs=evidence_refs,
        obligations=obligations,
        obstructions=obstructions,
        trust_vector=trust_vector if trust_vector is not None else {"tier": "verified", "channels": ["proof"]},
        provenance=provenance,
        clauses=clauses,
        status=status,
    )


def make_section(
    *,
    patch: str = "patch-a",
    patch_keys: frozenset[str] | None = None,
    support_labels: frozenset[str] | None = None,
    support_provenance: tuple[str, ...] = ("support:init",),
    section_provenance: tuple[str, ...] = ("section:init",),
    coordinate: CoordinateObject | None = None,
    proposition: str = "P",
    status: JudgmentStatus = JudgmentStatus.SETTLED,
    obligations: tuple[str, ...] = (),
    obstructions: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    judgment_provenance: tuple[str, ...] = ("capture", "review"),
    clauses: tuple[JudgmentClause, ...] = (),
    trust_vector: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
) -> JudgmentSection:
    coordinate = coordinate or make_coordinate()
    context = make_context(coordinate)
    judgment = make_judgment(
        coordinate=coordinate,
        proposition=proposition,
        status=status,
        obligations=obligations,
        obstructions=obstructions,
        evidence_refs=evidence_refs,
        provenance=judgment_provenance,
        clauses=clauses,
        trust_vector=trust_vector,
        artifact=artifact,
    )
    support = SupportRegion(
        coordinate,
        patch_keys or frozenset({patch}),
        support_labels or frozenset({"shared", "critical"}),
        support_provenance,
    )
    return JudgmentSection(coordinate, context, judgment, support, patch, section_provenance)


@pytest.mark.parametrize(
    ("projection", "loss_declared"),
    [
        (ProjectionKind.INTERNAL, False),
        (ProjectionKind.PUBLIC, True),
        (ProjectionKind.DIAGNOSTIC, True),
    ],
)
def test_export_section_declares_expected_loss(
    projection: ProjectionKind,
    loss_declared: bool,
) -> None:
    record = export_section(make_section(), projection=projection)
    assert record.loss_declared is loss_declared


def test_export_section_declares_public_loss() -> None:
    record = export_section(make_section(), projection=ProjectionKind.PUBLIC)
    assert record.loss_declared is True
    assert record.scope == ("patch-a",)


def test_export_section_accepts_projection_strings() -> None:
    record = export_section(make_section(), projection="diagnostic")
    assert record.projection is ProjectionKind.DIAGNOSTIC
    assert record.loss_declared is True


def test_export_judgment_accepts_projection_strings() -> None:
    judgment = make_judgment(status=JudgmentStatus.PROPOSED)
    record = export_judgment(judgment, projection="internal")
    assert record.projection is ProjectionKind.INTERNAL
    assert record.loss_declared is False


def test_export_record_alias_remains_section_export() -> None:
    record = export_section(make_section())
    assert ExportRecord is SectionExport
    assert isinstance(record, ExportRecord)


def test_export_judgment_returns_judgment_export() -> None:
    record = export_judgment(make_judgment())
    assert isinstance(record, JudgmentExport)
    assert not isinstance(record, SectionExport)


def test_scope_is_sorted_for_deterministic_serialization() -> None:
    section = make_section(patch_keys=frozenset({"patch-z", "patch-a", "patch-m"}))
    record = export_section(section)
    assert record.scope == ("patch-a", "patch-m", "patch-z")
    assert record.to_dict()["scope"] == ["patch-a", "patch-m", "patch-z"]


def test_support_labels_are_sorted_and_support_provenance_preserved() -> None:
    section = make_section(
        support_labels=frozenset({"beta", "alpha"}),
        support_provenance=("support:start", "support:refine"),
    )
    record = export_section(section)
    assert record.support_labels == ("alpha", "beta")
    assert record.support_provenance == ("support:start", "support:refine")


def test_section_provenance_is_preserved_with_length() -> None:
    section = make_section(section_provenance=("section:init", "section:glue"))
    record = export_section(section)
    payload = record.to_dict()
    assert record.section_provenance == ("section:init", "section:glue")
    assert payload["section_provenance_length"] == 2


def test_judgment_provenance_is_preserved_with_length() -> None:
    judgment = make_judgment(provenance=("capture", "solver", "review"))
    record = export_judgment(judgment)
    assert record.provenance == ("capture", "solver", "review")
    assert record.provenance_length == 3
    assert record.to_dict()["provenance"] == ["capture", "solver", "review"]


def test_residual_visibility_is_never_silenced() -> None:
    section = make_section(
        obligations=("prove-lemma", "rerun-runtime"),
        status=JudgmentStatus.SETTLED,
    )
    record = export_section(section)
    payload = record.to_dict()
    assert record.residual_count == 2
    assert record.residuals == ("prove-lemma", "rerun-runtime")
    assert payload["residual_visibility"] is True
    assert payload["residuals"] == ["prove-lemma", "rerun-runtime"]


def test_obstruction_count_and_fragility_survive_projection() -> None:
    section = make_section(
        status=JudgmentStatus.OBSTRUCTED,
        obstructions=("cover-mismatch",),
    )
    record = export_section(section, projection=ProjectionKind.DIAGNOSTIC)
    assert record.obstruction_count == 1
    assert "obstruction-present" in record.fragility
    assert "status:obstructed" in record.fragility
    assert record.to_dict()["obstructions"] == ["cover-mismatch"]


def test_thin_provenance_is_marked_when_empty() -> None:
    judgment = make_judgment(provenance=(), trust_vector={"tier": "verified"})
    record = export_judgment(judgment)
    assert record.provenance == ()
    assert "thin-provenance" in record.fragility


def test_empty_trust_is_marked_without_mutating_input() -> None:
    trust_vector: dict[str, Any] = {}
    record = export_judgment(make_judgment(trust_vector=trust_vector))
    assert record.trust_summary == {}
    assert "trust-unspecified" in record.fragility
    assert trust_vector == {}


def test_trust_summary_is_canonically_normalized() -> None:
    trust_vector = {
        "channels": {"solver", "proof"},
        "nested": {"levels": [3, 2, 1], "tier": TrustTier.VERIFIED},
        "custom": CustomEvidence(),
    }
    record = export_judgment(make_judgment(trust_vector=trust_vector))
    payload = record.to_dict()
    assert payload["trust_summary"] == {
        "channels": ["proof", "solver"],
        "custom": "CustomEvidence(token='copilot-proposal')",
        "nested": {"levels": [3, 2, 1], "tier": "verified"},
    }
    assert trust_vector["channels"] == {"solver", "proof"}


def test_public_evidence_summary_collects_refs_and_channels() -> None:
    judgment = make_judgment(
        evidence_refs=("proof:1", "runtime:2"),
        clauses=(
            make_clause("c1", evidence_channels=("proof", "runtime")),
            make_clause("c2", evidence_channels=("runtime", "semantic")),
        ),
    )
    record = export_judgment(judgment)
    assert record.public_evidence == {
        "evidence_refs": ("proof:1", "runtime:2"),
        "evidence_ref_count": 2,
        "clause_channels": ("proof", "runtime", "semantic"),
        "channel_count": 3,
        "clause_count": 2,
    }
    assert record.to_dict()["public_evidence"] == {
        "evidence_refs": ["proof:1", "runtime:2"],
        "evidence_ref_count": 2,
        "clause_channels": ["proof", "runtime", "semantic"],
        "channel_count": 3,
        "clause_count": 2,
    }


def test_clause_exports_preserve_clausewise_structure() -> None:
    judgment = make_judgment(
        clauses=(
            make_clause("c1", evidence_channels=("proof",)),
            make_clause("c2", satisfied=None, obligations=("supply-example",)),
        )
    )
    record = export_judgment(judgment)
    assert record.clauses == (
        ClauseExport("c1", "c1 holds", True, ("proof",), (), False),
        ClauseExport("c2", "c2 holds", None, (), ("supply-example",), True),
    )
    assert record.to_dict()["clauses"] == [
        {
            "name": "c1",
            "statement": "c1 holds",
            "satisfied": True,
            "evidence_channels": ["proof"],
            "obligations": [],
            "qualified": False,
        },
        {
            "name": "c2",
            "statement": "c2 holds",
            "satisfied": None,
            "evidence_channels": [],
            "obligations": ["supply-example"],
            "qualified": True,
        },
    ]


def test_positive_and_qualified_clauses_are_classified_from_clause_state() -> None:
    judgment = make_judgment(
        clauses=(
            make_clause("proved", satisfied=True),
            make_clause("pending", satisfied=True, obligations=("close-gap",)),
            make_clause("semantic", satisfied=None),
        )
    )
    record = export_judgment(judgment)
    assert record.positive_clauses == ("proved",)
    assert record.qualified_clauses == ("pending", "semantic")
    assert "qualified-claims" in record.fragility


def test_settled_proposition_becomes_positive_when_no_clauses_exist() -> None:
    record = export_judgment(make_judgment(proposition="Q"))
    assert record.positive_clauses == ("Q",)
    assert record.qualified_clauses == ()


def test_unsettled_proposition_becomes_qualified_when_no_clauses_exist() -> None:
    record = export_judgment(
        make_judgment(
            proposition="Q",
            status=JudgmentStatus.CHALLENGED,
            obligations=("justify-q",),
        )
    )
    assert record.positive_clauses == ()
    assert record.qualified_clauses == ("Q",)
    assert "status:challenged" in record.fragility


def test_artifact_summary_is_stable_and_scope_free_on_judgment_exports() -> None:
    judgment = make_judgment(artifact={"zeta": 1, "alpha": 2, "beta": 3})
    record = export_judgment(judgment)
    payload = record.to_dict()
    assert record.artifact_summary == {"keys": ("alpha", "beta", "zeta"), "item_count": 3}
    assert payload["artifact_summary"] == {"keys": ["alpha", "beta", "zeta"], "item_count": 3}
    assert "scope" not in payload


def test_section_export_contains_backward_compatible_front_fields() -> None:
    record = export_section(make_section())
    payload = record.to_dict()
    assert list(payload)[:10] == [
        "projection",
        "coordinate",
        "proposition",
        "status",
        "scope",
        "residual_count",
        "obstruction_count",
        "trust_summary",
        "provenance_length",
        "loss_declared",
    ]


def test_judgment_export_contains_backward_compatible_front_fields() -> None:
    record = export_judgment(make_judgment())
    payload = record.to_dict()
    assert list(payload)[:9] == [
        "projection",
        "coordinate",
        "proposition",
        "status",
        "residual_count",
        "obstruction_count",
        "trust_summary",
        "provenance_length",
        "loss_declared",
    ]


def test_export_is_json_serializable() -> None:
    record = export_section(make_section())
    rendered = json.dumps(record.to_dict(), sort_keys=True)
    assert '"coordinate": "coord"' in rendered
    assert '"scope": ["patch-a"]' in rendered


def test_export_is_deterministic_across_repeated_calls() -> None:
    section = make_section(
        patch_keys=frozenset({"patch-c", "patch-a"}),
        clauses=(
            make_clause("proved", evidence_channels=("proof",)),
            make_clause("pending", satisfied=None),
        ),
    )
    first = export_section(section).to_dict()
    second = export_section(section).to_dict()
    assert first == second


def test_certificate_surface_reflects_scope_and_fragility() -> None:
    record = export_section(
        make_section(
            obligations=("prove-lemma",),
            clauses=(make_clause("pending", obligations=("prove-lemma",)),),
        )
    )
    certificate_surface = record.certificate_surface()
    assert certificate_surface["certified_scope"] == ["patch-a"]
    assert certificate_surface["scope_honest"] is True
    assert certificate_surface["public_residuals"] == ["prove-lemma"]
    assert "qualified-claims" in certificate_surface["fragility"]


def test_judgment_certificate_surface_has_no_scope() -> None:
    record = export_judgment(make_judgment())
    certificate_surface = record.certificate_surface()
    assert "certified_scope" not in certificate_surface
    assert certificate_surface["claims_positive"] == ["P"]


def test_api_surface_remains_compatible_with_section_exports() -> None:
    api = JuGeoAPI()
    record = export_section(make_section())
    payload = api.export_record(record)
    assert payload["coordinate"] == "coord"
    assert api.serialize_export(record).startswith("{")


def test_certificate_emission_remains_compatible_with_export_record_alias() -> None:
    record = export_section(make_section())
    proof_record = EvidenceRecord(build_channel("proof", EvidenceKind.PROOF), "P")
    manifest = build_evidence_manifest(
        "coord",
        "P",
        (proof_record,),
        trust_profiles=(TrustProfile(TrustTier.VERIFIED),),
        provenance=ProvenanceTrace("root"),
    )
    certificate = emit_certificate(manifest, record, issuer="tester")
    assert certificate.status is CertificateStatus.SETTLED
    assert certificate.export == record
    assert certificate.to_dict()["export"]["coordinate"] == "coord"


def test_certificate_pending_status_still_sees_export_residuals() -> None:
    record = export_section(make_section(obligations=("prove-lemma",)))
    proof_record = EvidenceRecord(build_channel("proof", EvidenceKind.PROOF), "P", obligations=("manifest-open",))
    manifest = build_evidence_manifest(
        "coord",
        "P",
        (proof_record,),
        trust_profiles=(TrustProfile(TrustTier.PROPOSAL),),
        provenance=ProvenanceTrace("root"),
    )
    certificate = emit_certificate(manifest, record, issuer="tester")
    assert certificate.status is CertificateStatus.PENDING
    assert certificate.export.residual_count == 1
    assert certificate.to_dict()["export"]["residuals"] == ["prove-lemma"]


def test_patch_is_preserved_even_when_scope_contains_multiple_patches() -> None:
    record = export_section(
        make_section(patch="patch-a", patch_keys=frozenset({"patch-a", "patch-b"}))
    )
    assert record.patch == "patch-a"
    assert record.scope_size == 2


def test_provenance_visibility_flag_is_stable() -> None:
    record = export_section(make_section())
    payload = record.to_dict()
    assert record.provenance_visible is True
    assert payload["provenance_visible"] is True


def test_section_export_uses_section_coordinate_key() -> None:
    coordinate = make_coordinate("module", "coord")
    record = export_section(make_section(coordinate=coordinate))
    assert record.coordinate == "module/coord"
    assert record.to_dict()["coordinate"] == "module/coord"


def test_judgment_export_preserves_evidence_refs_in_order() -> None:
    record = export_judgment(
        make_judgment(evidence_refs=("proof:1", "runtime:2", "semantic:3"))
    )
    assert record.evidence_refs == ("proof:1", "runtime:2", "semantic:3")
    assert record.to_dict()["evidence_refs"] == ["proof:1", "runtime:2", "semantic:3"]


def test_clause_export_to_dict_is_json_ready() -> None:
    clause_export = ClauseExport(
        name="compat",
        statement="compatibility holds",
        satisfied=True,
        evidence_channels=("proof", "runtime"),
        obligations=("none",),
        qualified=False,
    )
    assert clause_export.to_dict() == {
        "name": "compat",
        "statement": "compatibility holds",
        "satisfied": True,
        "evidence_channels": ["proof", "runtime"],
        "obligations": ["none"],
        "qualified": False,
    }


def test_residual_count_matches_clause_and_judgment_residuals_without_dropping_either() -> None:
    record = export_section(
        make_section(
            obligations=("global-gap",),
            clauses=(make_clause("local-gap", obligations=("local-gap",)),),
        )
    )
    assert record.residual_count == 1
    assert record.qualified_clauses == ("local-gap",)
    assert record.to_dict()["certificate_surface"]["public_residuals"] == ["global-gap"]


def test_public_projection_preserves_scope_honesty_for_partial_sections() -> None:
    record = export_section(
        make_section(
            patch_keys=frozenset({"patch-a", "patch-b"}),
            obligations=("glue-overlap",),
            status=JudgmentStatus.SETTLED,
        ),
        projection=ProjectionKind.PUBLIC,
    )
    certificate_surface = record.to_dict()["certificate_surface"]
    assert certificate_surface["certified_scope"] == ["patch-a", "patch-b"]
    assert certificate_surface["scope_honest"] is True
    assert record.loss_declared is True


def test_internal_projection_remains_least_lossy_but_still_serializable() -> None:
    record = export_section(make_section(), projection=ProjectionKind.INTERNAL)
    payload = record.to_dict()
    assert payload["projection"] == "internal"
    assert payload["loss_declared"] is False
    assert payload["certificate_surface"]["loss_declared"] is False


def test_diagnostic_projection_reports_loss_without_mutating_source_section() -> None:
    section = make_section(obligations=("rerun",), obstructions=("mismatch",))
    original_support = section.support.patch_keys
    original_trust = dict(section.judgment.trust_vector)
    record = export_section(section, projection=ProjectionKind.DIAGNOSTIC)
    assert record.loss_declared is True
    assert section.support.patch_keys == original_support
    assert dict(section.judgment.trust_vector) == original_trust


def test_json_round_trip_like_view_retains_semantic_markers() -> None:
    record = export_section(
        make_section(
            clauses=(
                make_clause("proved", evidence_channels=("proof",)),
                make_clause("qualified", satisfied=None),
            ),
            obligations=("prove-lemma",),
            evidence_refs=("proof:1",),
        )
    )
    payload = json.loads(json.dumps(record.to_dict(), sort_keys=True))
    assert payload["positive_clauses"] == ["proved"]
    assert payload["qualified_clauses"] == ["qualified"]
    assert payload["residual_visibility"] is True
    assert payload["certificate_surface"]["public_evidence"]["evidence_refs"] == ["proof:1"]


def test_exported_fragility_is_tuple_even_for_clean_records() -> None:
    record = export_judgment(make_judgment())
    assert isinstance(record.fragility, tuple)
    assert "lossy-projection" in record.fragility


def test_clean_internal_judgment_has_minimal_fragility_markers() -> None:
    record = export_judgment(make_judgment(), projection=ProjectionKind.INTERNAL)
    assert "lossy-projection" not in record.fragility
    assert "status:settled" not in record.fragility
    assert "trust-unspecified" not in record.fragility


def test_export_handles_non_string_artifact_keys_stably() -> None:
    record = export_judgment(make_judgment(artifact={3: "three", "1": "one", ("z",): "tuple"}))
    assert record.artifact_summary == {"keys": ("('z',)", "1", "3"), "item_count": 3}
    assert record.to_dict()["artifact_summary"]["keys"] == ["('z',)", "1", "3"]


def test_export_preserves_public_evidence_order_from_clause_appearance() -> None:
    record = export_judgment(
        make_judgment(
            clauses=(
                make_clause("c1", evidence_channels=("semantic", "proof")),
                make_clause("c2", evidence_channels=("proof", "runtime")),
                make_clause("c3", evidence_channels=("runtime", "semantic")),
            )
        )
    )
    assert record.public_evidence["clause_channels"] == (
        "semantic",
        "proof",
        "runtime",
    )


def test_scope_and_residuals_can_be_read_by_humans_and_llms() -> None:
    record = export_section(
        make_section(
            patch_keys=frozenset({"north", "south"}),
            obligations=("bridge-overlap",),
            judgment_provenance=("seed", "copilot-review", "proof-pass"),
        )
    )
    payload = record.to_dict()
    assert payload["scope"] == ["north", "south"]
    assert payload["residuals"] == ["bridge-overlap"]
    assert payload["provenance"] == ["seed", "copilot-review", "proof-pass"]
