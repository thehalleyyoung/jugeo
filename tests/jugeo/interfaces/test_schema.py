from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.errors import (
    EvidenceFamily,
    FailureClassification,
    FailureScope,
    RepairHint,
    StructuredFailure,
)
from jugeo.evidence.manifests import ManifestBuilder, ObstructionKind
from jugeo.evidence.provenance import ProvenanceStep, ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.interfaces.diagnostics import DiagnosticLevel, DiagnosticMessage, DiagnosticReport
from jugeo.interfaces.schema import (
    BLUEPRINT_SOURCE,
    DEFAULT_SCHEMA_REGISTRY,
    GENERATION_ORDER_SOURCE,
    SCHEMA_VERSION,
    THEORY_SOURCE,
    SchemaDecodeError,
    SchemaRegistry,
    WireSchema,
    certificate_schema,
    decode_wire_payload,
    diagnostic_report_schema,
    encode_wire_payload,
    get_wire_schema,
    judgment_schema,
    manifest_schema,
    provenance_trace_schema,
    report_schema,
    trust_profile_schema,
)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text())


def _schema_blueprint_entry() -> dict[str, object]:
    blueprint = _read_json(ROOT / BLUEPRINT_SOURCE)
    stack: list[object] = [blueprint]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("file") == "schema.py":
                return current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    raise AssertionError("schema.py blueprint entry not found")


def _schema_generation_item() -> dict[str, object]:
    generation_order = _read_json(ROOT / GENERATION_ORDER_SOURCE)
    assert isinstance(generation_order, dict)
    items = generation_order["items"]
    assert isinstance(items, list)
    for item in items:
        if isinstance(item, dict) and item.get("target") == "src/jugeo/interfaces/schema.py":
            return item
    raise AssertionError("generation-order entry for schema.py not found")


def _decode_json(text: str) -> dict[str, object]:
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def _source_file(path: str) -> Path:
    return ROOT / path


def test_generated_files_stay_within_requested_size_window() -> None:
    source_size = (_source_file("src/jugeo/interfaces/schema.py")).stat().st_size
    test_size = (_source_file("tests/jugeo/interfaces/test_schema.py")).stat().st_size
    assert 15_000 < source_size <= 100_000
    assert 15_000 < test_size <= 100_000


def test_spec_files_and_theory_sources_are_wired_into_public_metadata() -> None:
    descriptor = report_schema()
    x_jugeo = descriptor["x-jugeo"]
    assert x_jugeo["theory_source"] == THEORY_SOURCE
    assert x_jugeo["structural_blueprint"] == BLUEPRINT_SOURCE
    assert x_jugeo["generation_order"] == GENERATION_ORDER_SOURCE
    assert "serialization determinism" in x_jugeo["theorem_targets"]
    assert "no-silent-promotion" in x_jugeo["theorem_targets"]


def test_blueprint_entry_matches_current_public_surface() -> None:
    entry = _schema_blueprint_entry()
    assert entry["role"] == "Defines JSON-compatible wire schemas for reports, manifests, and diagnostics."
    assert entry["estimatedLoC"] == 638
    assert entry["classes"] == ["WireSchema", "SchemaRegistry"]
    assert entry["functions"] == ["encode_wire_payload()", "decode_wire_payload()"]


def test_generation_order_declares_schema_before_serialization() -> None:
    item = _schema_generation_item()
    assert item["sequence"] == 54
    assert item["stage"] == "shared-interfaces"
    assert item["scope"] == "shared"
    depends_on = item["dependsOn"]
    assert "src/jugeo/errors.py" in depends_on
    assert "src/jugeo/runtime_defaults.py" in depends_on
    assert item["test"] == "tests/jugeo/interfaces/test_schema.py"


def test_registry_exposes_expected_canonical_names_and_types() -> None:
    registry = DEFAULT_SCHEMA_REGISTRY
    assert isinstance(registry, SchemaRegistry)
    assert registry.names() == (
        "certificate",
        "diagnostic_message",
        "diagnostic_report",
        "judgment",
        "manifest",
        "provenance_step",
        "provenance_trace",
        "report",
        "structured_failure",
        "trust_profile",
    )
    assert isinstance(get_wire_schema("report"), WireSchema)
    assert registry.aliases()["diagnostics"] == "diagnostic_report"
    assert registry.aliases()["trust"] == "trust_profile"


@pytest.mark.parametrize(
    ("schema_factory", "required_field"),
    [
        (trust_profile_schema, "tier"),
        (provenance_trace_schema, "origin"),
        (diagnostic_report_schema, "messages"),
        (judgment_schema, "coordinate"),
        (manifest_schema, "manifest_id"),
        (report_schema, "report_id"),
        (certificate_schema, "issued_by"),
    ],
)
def test_descriptor_helpers_expose_required_fields(schema_factory, required_field: str) -> None:
    descriptor = schema_factory()
    assert descriptor["version"] == SCHEMA_VERSION
    assert required_field in descriptor["required"]
    assert descriptor["$schema"].startswith("https://json-schema.org/")


def test_certificate_schema_preserves_backward_aliases_and_honest_required_fields() -> None:
    descriptor = certificate_schema()
    properties = descriptor["properties"]
    assert properties["issued_by"]["x-jugeo-aliases"] == ["issuer"]
    assert properties["verified"]["x-jugeo-aliases"] == ["verified_propositions"]
    assert "issued_by" in descriptor["required"]
    assert "verified" in descriptor["required"]


def test_trust_profile_round_trips_actual_trustprofile_objects_without_flattening() -> None:
    trust = TrustProfile(TrustTier.REVIEWED, ("module/auth",), ("solver corroborated",))
    encoded = encode_wire_payload(trust, "trust_profile")
    decoded = decode_wire_payload(encoded)
    assert decoded == {
        "tier": "reviewed",
        "support_scope": ["module/auth"],
        "reasons": ["solver corroborated"],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("COPILOT_SUGGESTED", "copilot_suggested"),
        ("MECHANICALLY_VERIFIED", "mechanically_verified"),
        (TrustTier.VERIFIED, "verified"),
        (40, "certified"),
    ],
)
def test_trust_profile_accepts_existing_trust_flavors(raw: object, expected: str) -> None:
    encoded = encode_wire_payload(raw, "trust_profile")
    assert decode_wire_payload(encoded)["tier"] == expected


def test_provenance_trace_round_trips_existing_provenance_dataclasses() -> None:
    trace = ProvenanceTrace(
        "planner",
        (
            ProvenanceStep("copilot", "proposed", "module/auth", {"kind": "candidate"}),
            ProvenanceStep("solver", "checked", "module/auth", {"result": "ok"}),
        ),
    )
    decoded = decode_wire_payload(encode_wire_payload(trace, "provenance_trace"))
    assert decoded["origin"] == "planner"
    assert decoded["steps"][0]["actor"] == "copilot"
    assert decoded["steps"][1]["action"] == "checked"


def test_diagnostic_report_round_trips_existing_diagnostic_module_objects() -> None:
    report = DiagnosticReport(
        (
            DiagnosticMessage(DiagnosticLevel.INFO, "health snapshots: 1"),
            DiagnosticMessage(DiagnosticLevel.WARNING, "residual obligations remain"),
        )
    )
    decoded = decode_wire_payload(encode_wire_payload(report, "diagnostic_report"))
    assert decoded == {
        "messages": [
            {"level": "info", "message": "health snapshots: 1"},
            {"level": "warning", "message": "residual obligations remain"},
        ],
        "summary": "",
        "generated_at": None,
        "trust": None,
        "provenance": None,
    }


def test_structured_failure_round_trips_existing_error_surface() -> None:
    failure = StructuredFailure(
        message="trust boundary crossed",
        scope=FailureScope.INTERFACE,
        classification=FailureClassification.TRUST_VIOLATION,
        evidence_family=EvidenceFamily.SEMANTIC,
        coordinate="module/auth/login",
        support_scope="module/auth",
        semantic_boundary="public-report",
        trust_boundary="no-silent-promotion",
        repair_hints=(RepairHint("add-solver-corroboration", "add solver corroboration", priority=2),),
        affected_obligations=("prove-auth-invariant",),
        provenance={"source": "test"},
        trust={"tier": "reviewed"},
        notes=("copilot proposal must stay provisional",),
        recoverable=True,
    )
    decoded = decode_wire_payload(encode_wire_payload(failure, "structured_failure"))
    assert decoded["message"] == "trust boundary crossed"
    assert decoded["scope"] == "interface"
    assert decoded["classification"] == "trust_violation"
    assert decoded["affected_obligations"] == ["prove-auth-invariant"]
    assert decoded["recoverable"] is True


def test_judgment_schema_accepts_theory_shaped_payload_with_actual_trust_and_provenance_objects() -> None:
    payload = {
        "coordinate": "module/auth/login",
        "proposition": "login preserves session invariants",
        "artifact": {"function": "login"},
        "status": "Residual",
        "evidence": [{"channel": "solver", "claim": "branch-safe"}],
        "obligations": ["prove token freshness"],
        "blame": {"kind": "evidence_gap", "message": "runtime witness missing"},
        "trust": TrustProfile(TrustTier.PROPOSAL, ("module/auth",), ("copilot candidate",)),
        "provenance": ProvenanceTrace("planner", (ProvenanceStep("copilot", "drafted", "module/auth/login", {}),)),
        "metadata": {"surface": "api"},
    }
    normalized = decode_wire_payload(encode_wire_payload(payload, "judgment"))
    assert normalized["carrier"] == {"function": "login"}
    assert normalized["status"] == "residual"
    assert normalized["obstructions"] == [{"kind": "evidence_gap", "message": "runtime witness missing"}]
    assert normalized["trust"]["tier"] == "proposal"
    assert normalized["provenance"]["origin"] == "planner"


def test_manifest_schema_accepts_real_manifest_snapshots_from_nearby_module() -> None:
    manifest = (
        ManifestBuilder()
        .add_judgment("module/auth", "tokens remain scoped", status="settled")
        .add_obligation("module/auth", "prove logout invalidation")
        .add_obstruction("module/auth", kind=ObstructionKind.EVIDENCE_GAP, message="runtime witness missing")
        .add_certificate(
            "cert-auth",
            "module/auth",
            {
                "certificate_id": "cert-auth",
                "coordinate": "module/auth",
                "verified": ["tokens remain scoped"],
                "trust_level": "reviewed",
                "evidence_summary": "solver + review",
                "issued_by": "tests",
            },
        )
        .add_epoch("module/auth", 2)
        .add_invalidation("module/auth", "module/session")
        .build()
    )
    decoded = decode_wire_payload(encode_wire_payload(manifest, "manifest"))
    assert decoded["manifest_id"].startswith("m-")
    assert len(decoded["judgments"]) == 1
    assert len(decoded["obligations"]) == 1
    assert len(decoded["obstructions"]) == 1
    assert decoded["epoch_map"]["module/auth"] == 2
    assert decoded["invalidation_graph"]["module/auth"] == ["module/session"]


def test_report_schema_carries_manifest_diagnostics_trust_and_provenance_together() -> None:
    report_payload = {
        "report_id": "report-auth-1",
        "title": "Authentication status",
        "status": "Obstructed",
        "summary": "Residual proof and runtime obligations remain.",
        "items": [{"kind": "summary", "count": 2}],
        "diagnostics": DiagnosticReport((DiagnosticMessage(DiagnosticLevel.ERROR, "runtime witness missing"),)),
        "manifest": {
            "manifest_id": "m-report",
            "judgments": [],
            "obligations": [{"description": "prove logout invalidation"}],
            "evidence_archive": [],
            "obstructions": [],
            "certificates": [],
            "epoch_map": {},
            "invalidation_graph": {},
        },
        "trust": "reviewed",
        "provenance": ProvenanceTrace("reporter", ()),
    }
    decoded = decode_wire_payload(encode_wire_payload(report_payload, "report"))
    assert decoded["status"] == "obstructed"
    assert decoded["diagnostics"]["messages"][0]["level"] == "error"
    assert decoded["manifest"]["manifest_id"] == "m-report"
    assert decoded["trust"]["tier"] == "reviewed"
    assert decoded["provenance"]["origin"] == "reporter"


def test_certificate_alias_inputs_normalize_to_stable_wire_shape() -> None:
    certificate_payload = {
        "certificate_id": "k-1",
        "coordinate": "module/auth",
        "verified_propositions": ["tokens remain scoped"],
        "trust_level": "VERIFIED",
        "evidence_summary": {"channels": ["solver", "human"]},
        "residual_obligations": ["prove logout invalidation"],
        "issuer": "authority-alpha",
        "valid": True,
    }
    decoded = decode_wire_payload(encode_wire_payload(certificate_payload, "certificate"))
    assert decoded == {
        "certificate_id": "k-1",
        "coordinate": "module/auth",
        "verified": ["tokens remain scoped"],
        "trust_level": "verified",
        "evidence_summary": {"channels": ["solver", "human"]},
        "residuals": ["prove logout invalidation"],
        "obstructions": [],
        "issued_at": None,
        "issued_by": "authority-alpha",
        "expiry": None,
        "valid": True,
    }


def test_encoding_is_deterministic_for_semantically_equal_payloads() -> None:
    payload_a = {
        "report_id": "r-1",
        "title": "Status",
        "status": "settled",
        "summary": "ok",
        "items": [{"b": 2, "a": 1}],
    }
    payload_b = {
        "title": "Status",
        "report_id": "r-1",
        "summary": "ok",
        "status": "settled",
        "items": [{"a": 1, "b": 2}],
    }
    assert encode_wire_payload(payload_a, "report") == encode_wire_payload(payload_b, "report")


def test_decoding_detects_tampered_payload_hash() -> None:
    encoded = _decode_json(encode_wire_payload({"report_id": "r-1", "title": "Title", "status": "pending", "summary": "wait"}, "report"))
    encoded["payload"]["summary"] = "tampered"
    tampered = json.dumps(encoded, sort_keys=True, separators=(",", ":"))
    with pytest.raises(SchemaDecodeError):
        decode_wire_payload(tampered)


def test_unknown_fields_are_preserved_for_forward_compatible_schemas() -> None:
    report_decoded = decode_wire_payload(
        encode_wire_payload(
            {
                "report_id": "r-2",
                "title": "Status",
                "status": "pending",
                "summary": "waiting",
                "extra_notes": ["kept for forward compatibility"],
            },
            "report",
        )
    )
    assert report_decoded["extra_notes"] == ["kept for forward compatibility"]


def test_missing_required_fields_produce_explicit_validation_errors() -> None:
    report = get_wire_schema("report")
    result = report.validate({"report_id": "r-3"}, registry=DEFAULT_SCHEMA_REGISTRY)
    assert not result.ok
    error_paths = {issue.path for issue in result.errors}
    assert "payload.title" in error_paths
    assert "payload.status" in error_paths
    assert "payload.summary" in error_paths


def test_decode_requires_schema_when_no_envelope_declares_it() -> None:
    with pytest.raises(SchemaDecodeError):
        decode_wire_payload({"report_id": "r-4", "title": "Title"})


def test_raw_payload_can_be_decoded_when_schema_is_supplied_explicitly() -> None:
    payload = {
        "report_id": "r-5",
        "title": "Explicit schema",
        "status": "settled",
        "summary": "decoded from raw payload",
    }
    decoded = decode_wire_payload(payload, "report")
    assert decoded["title"] == "Explicit schema"
    assert decoded["status"] == "settled"


def test_descriptor_bundle_is_machine_readable_and_references_all_schema_ids() -> None:
    bundle = DEFAULT_SCHEMA_REGISTRY.descriptor_bundle()
    assert bundle["version"] == SCHEMA_VERSION
    schemas = bundle["schemas"]
    for name in DEFAULT_SCHEMA_REGISTRY.names():
        descriptor = schemas[name]
        assert descriptor["$id"].endswith(f"/{name}/{SCHEMA_VERSION}")
        assert descriptor["x-jugeo"]["schema_name"] == name


def test_nearby_source_files_and_current_descriptors_stay_aligned_on_key_terms() -> None:
    diagnostics_source = _source_file("src/jugeo/interfaces/diagnostics.py").read_text()
    manifests_source = _source_file("src/jugeo/evidence/manifests.py").read_text()
    certificates_source = _source_file("src/jugeo/evidence/certificates.py").read_text()
    theory_source = _source_file(THEORY_SOURCE).read_text()

    assert "class DiagnosticReport" in diagnostics_source
    assert "class Manifest" in manifests_source
    assert "issuer" in certificates_source
    assert "Manifest integrity" in theory_source
    assert "Trust as an ordered algebra of admissible support" in theory_source
    assert "provenance" in theory_source.lower()

    assert "messages" in diagnostic_report_schema()["properties"]
    assert "epoch_map" in manifest_schema()["properties"]
    assert "issued_by" in certificate_schema()["properties"]


def test_public_source_mentions_wire_first_api_instead_of_old_objectschema_focus() -> None:
    source = _source_file("src/jugeo/interfaces/schema.py").read_text()
    assert "class WireSchema" in source
    assert "class SchemaRegistry" in source
    assert "def encode_wire_payload" in source
    assert "def decode_wire_payload" in source


def test_wire_schema_api_remains_friendly_for_humans_and_llms() -> None:
    report = get_wire_schema("report")
    field_names = [field.name for field in report.fields]
    assert field_names == [
        "report_id",
        "title",
        "status",
        "summary",
        "items",
        "diagnostics",
        "manifest",
        "trust",
        "provenance",
    ]
    descriptor = report.descriptor(DEFAULT_SCHEMA_REGISTRY)
    assert descriptor["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "settled",
        "obstructed",
        "residual",
        "failed",
    ]
