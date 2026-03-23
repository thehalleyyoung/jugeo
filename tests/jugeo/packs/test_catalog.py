from __future__ import annotations

from pathlib import Path
import importlib
import json
import sys
from typing import Any

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src").exists())
SRC = ROOT / "src"
TESTS = ROOT / "tests"


def _bootstrap_src_package() -> None:
    """Force imports to resolve against ``src/jugeo`` instead of ``tests/jugeo``."""

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    to_remove: list[str] = []
    for name, module in list(sys.modules.items()):
        if name == __name__:
            continue
        if name != "jugeo" and not name.startswith("jugeo."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            if name in {"jugeo", "jugeo.packs"}:
                to_remove.append(name)
            continue
        try:
            resolved = Path(module_file).resolve()
        except OSError:
            continue
        if TESTS in resolved.parents:
            to_remove.append(name)

    for name in sorted(to_remove, reverse=True):
        sys.modules.pop(name, None)

    importlib.invalidate_caches()


_bootstrap_src_package()

from jugeo.errors import JuGeoError
from jugeo.evidence.trust import TrustTier
from jugeo.package_manifest import build_package_manifest, enumerate_subsystems
from jugeo.packs.authority import PackAuthority, PackJurisdiction, authorize_pack
from jugeo.packs.bridges import PackBridge
from jugeo.packs.catalog import (
    DEFAULT_PACK_DESCRIPTORS,
    KNOWN_AUTHORITY_LEVELS,
    PACK_SPEC_PROVENANCE,
    PackAdapter,
    PackBoundary,
    PackCatalog,
    PackDescriptor,
    PackLaw,
    list_available_packs,
    load_pack_catalog,
)
from jugeo.packs.federation import PackFederation
from jugeo.packs.loading import PackLoadRequest, load_pack

SOURCE_FILE = ROOT / "src" / "jugeo" / "packs" / "catalog.py"
TEST_FILE = ROOT / "tests" / "jugeo" / "packs" / "test_catalog.py"
BLUEPRINT_FILE = ROOT / "theory2-src-blueprint.json"
GEN_ORDER_FILE = ROOT / "theory2-generation-order.json"
THEORY_TEX = ROOT / "preliminaries" / "theory2.tex"
THEORY_PDF = ROOT / "preliminaries" / "theory2.pdf"


@pytest.fixture(scope="module")
def builtin_catalog() -> PackCatalog:
    return load_pack_catalog()


@pytest.fixture(scope="module")
def blueprint_payload() -> dict[str, Any]:
    return json.loads(BLUEPRINT_FILE.read_text())


@pytest.fixture(scope="module")
def generation_order_payload() -> dict[str, Any]:
    return json.loads(GEN_ORDER_FILE.read_text())


@pytest.fixture(scope="module")
def manifest_subsystems() -> tuple[str, ...]:
    return enumerate_subsystems(build_package_manifest())


def _blueprint_packs_directory(blueprint_payload: dict[str, Any]) -> dict[str, Any]:
    return next(
        item
        for item in blueprint_payload["sharedDirectories"]
        if item["path"] == "src/jugeo/packs"
    )


def _catalog_blueprint_entry(blueprint_payload: dict[str, Any]) -> dict[str, Any]:
    packs_dir = _blueprint_packs_directory(blueprint_payload)
    return next(file_entry for file_entry in packs_dir["files"] if file_entry["file"] == "catalog.py")


def _catalog_generation_entry(generation_order_payload: dict[str, Any]) -> dict[str, Any]:
    return next(
        item
        for item in generation_order_payload["items"]
        if item["target"] == "src/jugeo/packs/catalog.py"
    )


def _custom_catalog() -> PackCatalog:
    return load_pack_catalog(
        [
            {
                "name": "base",
                "version": "1",
                "capabilities": ("core",),
                "exported_kinds": ("kind.base",),
                "metadata": {"bridges": ("base-to-leaf",)},
                "site_region": "test.base",
                "cover_name": "base-cover",
                "admissible_contexts": ("base-local",),
                "laws": [
                    {
                        "name": "base-law",
                        "statement": "Base exports remain explicit.",
                        "law_kind": "test",
                    }
                ],
                "bridge_slots": ("base-to-leaf",),
                "provenance": {"source_tex": "synthetic"},
                "trust": {"policy": "strict", "trust_floor": "residual"},
            },
            {
                "name": "leaf",
                "version": "1",
                "capabilities": ("leaf",),
                "exported_kinds": ("kind.leaf",),
                "dependencies": ("base",),
                "metadata": {"bridges": ("base-to-leaf", "leaf-to-ui")},
                "site_region": "test.leaf",
                "cover_name": "leaf-cover",
                "admissible_contexts": ("leaf-local",),
                "laws": [
                    {
                        "name": "leaf-law",
                        "statement": "Leaf claims remain local until glued.",
                        "law_kind": "test",
                    }
                ],
                "bridge_slots": ("base-to-leaf", "leaf-to-ui"),
                "adapters": [
                    {
                        "name": "leaf-adapter",
                        "source_kind": "kind.leaf",
                        "target_kind": "kind.view",
                        "via_boundary": "leaf-ui",
                    }
                ],
                "federation_boundaries": [
                    {
                        "boundary_id": "leaf-ui",
                        "authority": "exploratory",
                        "outbound_packs": ("ui",),
                        "egress_kinds": ("kind.leaf",),
                        "trust_channels": ("runtime",),
                    }
                ],
                "provenance": {"source_tex": "synthetic"},
                "trust": {"policy": "strict", "trust_floor": "residual"},
            },
        ]
    )


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 115_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_files_exist_and_are_nontrivial() -> None:
    for path in (BLUEPRINT_FILE, GEN_ORDER_FILE, THEORY_TEX, THEORY_PDF):
        assert path.exists()
        assert path.stat().st_size > 100

    assert THEORY_PDF.stat().st_size > 500_000


def test_pack_spec_provenance_points_at_governing_files() -> None:
    assert PACK_SPEC_PROVENANCE["source_tex"] == "preliminaries/theory2.tex"
    assert PACK_SPEC_PROVENANCE["source_pdf"] == "preliminaries/theory2.pdf"
    assert PACK_SPEC_PROVENANCE["blueprint_path"] == "theory2-src-blueprint.json"
    assert PACK_SPEC_PROVENANCE["generation_order_path"] == "theory2-generation-order.json"
    assert PACK_SPEC_PROVENANCE["target_file"] == "src/jugeo/packs/catalog.py"
    assert PACK_SPEC_PROVENANCE["target_test"] == "tests/jugeo/packs/test_catalog.py"
    assert PACK_SPEC_PROVENANCE["stage"] == "shared-packs"
    assert PACK_SPEC_PROVENANCE["sequence"] == 24


def test_blueprint_entry_matches_catalog_surface(blueprint_payload: dict[str, Any]) -> None:
    packs_dir = _blueprint_packs_directory(blueprint_payload)
    assert packs_dir["purpose"] == "Owns domain packs, bridge theorems, federation boundaries, and pack-level authority metadata."

    entry = _catalog_blueprint_entry(blueprint_payload)
    assert entry["role"] == "Catalogs installed packs and their exported semantic kinds, laws, and adapters."
    assert set(entry["classes"]) == {"PackCatalog", "PackDescriptor"}
    assert set(entry["functions"]) == {"load_pack_catalog()", "list_available_packs()"}


def test_generation_order_entry_matches_requested_stage(generation_order_payload: dict[str, Any]) -> None:
    entry = _catalog_generation_entry(generation_order_payload)
    assert entry["sequence"] == 24
    assert entry["scope"] == "shared"
    assert entry["stage"] == "shared-packs"
    assert entry["dependsOn"] == [
        "src/jugeo/errors.py",
        "src/jugeo/runtime_defaults.py",
        "src/jugeo/evidence/certificates.py",
        "src/jugeo/geometry/descent.py",
        "src/jugeo/judgments/sections.py",
    ]


def test_theory2_pack_chapter_mentions_local_theory_record() -> None:
    text = THEORY_TEX.read_text()
    assert "A domain pack must be introduced as a local semantic theory over a region or cover" in text
    assert "\\Gamma^{\\mathfrak{P}}" in text
    assert "\\Sigma^{\\mathfrak{P}}" in text
    assert "\\mathcal{L}^{\\mathfrak{P}}" in text
    assert "\\mathcal{T}^{\\mathfrak{P}}" in text
    assert "\\mathcal{B}^{\\mathfrak{P}}" in text
    assert "\\mathrm{seal}^{\\mathfrak{P}}" in text


def test_pack_descriptor_fields_match_theory2_worldview() -> None:
    field_names = PackDescriptor.__dataclass_fields__.keys()
    assert "site_region" in field_names
    assert "cover_name" in field_names
    assert "admissible_contexts" in field_names
    assert "laws" in field_names
    assert "routing_policies" in field_names
    assert "bridge_slots" in field_names
    assert "federation_boundaries" in field_names
    assert "seal" in field_names


def test_pack_law_roundtrip_and_json_shape() -> None:
    law = PackLaw(
        "descent-law",
        "Claims must glue explicitly.",
        law_kind="descent",
        locality="cover-local",
        evidence_channels=("proof", "runtime"),
        metadata={"family": "gluing"},
    )

    payload = law.to_dict()
    rebuilt = PackLaw.from_mapping(payload)

    assert rebuilt == law
    assert payload["name"] == "descent-law"
    assert payload["metadata"]["family"] == "gluing"


def test_pack_adapter_roundtrip_and_support_lookup() -> None:
    adapter = PackAdapter(
        "section-to-export",
        "judgments.section",
        "judgments.section-export",
        via_boundary="judgments-pack",
        notes=("explicit-only",),
    )

    payload = adapter.to_dict()
    rebuilt = PackAdapter.from_mapping(payload)

    assert rebuilt == adapter
    assert rebuilt.supports("judgments.section", "judgments.section-export") is True
    assert rebuilt.supports("judgments.section", "evidence.certificate") is False


def test_pack_boundary_roundtrip_and_shape() -> None:
    boundary = PackBoundary(
        "pack-runtime",
        "provisional",
        inbound_packs=("packs",),
        outbound_packs=("runtime",),
        ingress_kinds=("packs.pack-descriptor",),
        egress_kinds=("runtime.checkpoint",),
        trust_channels=("runtime",),
        metadata={"support_scope": "shared"},
    )

    payload = boundary.to_dict()
    rebuilt = PackBoundary.from_mapping(payload)

    assert rebuilt == boundary
    assert payload["metadata"]["support_scope"] == "shared"


def test_pack_descriptor_roundtrip_to_dict_and_theory_record() -> None:
    descriptor = PackDescriptor(
        "sample",
        "1.2.0",
        ("capability",),
        ("kind.sample",),
        ("base",),
        "provisional",
        {"bridges": ("sample-to-ui",)},
        description="Test descriptor.",
        site_region="sample.region",
        cover_name="sample-cover",
        admissible_contexts=("sample-local",),
        laws=(PackLaw("sample-law", "Statements remain explicit."),),
        routing_policies={"policy": "strict"},
        bridge_slots=("sample-to-ui",),
        adapters=(PackAdapter("sample-adapter", "kind.sample", "kind.view"),),
        federation_boundaries=(PackBoundary("sample-ui", "provisional", outbound_packs=("ui",)),),
        provenance={"source_tex": "synthetic"},
        trust={"policy": "strict", "trust_floor": "residual"},
    )

    rebuilt = PackDescriptor.from_mapping(descriptor.to_dict())

    assert rebuilt == descriptor
    assert descriptor.to_theory_record()["region"] == "sample.region"
    assert descriptor.to_theory_record()["cover"] == "sample-cover"
    assert descriptor.bridge_names() == ("sample-to-ui",)
    assert descriptor.law_names() == ("sample-law",)
    assert descriptor.adapter_names() == ("sample-adapter",)
    assert descriptor.boundary_ids() == ("sample-ui",)


def test_pack_descriptor_normalizes_bridge_metadata_and_seal() -> None:
    descriptor = PackDescriptor(
        "bridge-pack",
        "1",
        ("cap",),
        ("kind.bridge",),
        bridge_slots=("one", "two"),
        provenance={"source_tex": "synthetic"},
        trust={"policy": "strict", "trust_floor": "residual"},
    )

    assert descriptor.bridge_names() == ("one", "two")
    assert descriptor.metadata["seal"] == "pack:bridge-pack@1"
    assert descriptor.seal == "pack:bridge-pack@1"


def test_pack_descriptor_validation_flags_missing_surface_data() -> None:
    descriptor = PackDescriptor(
        "thin",
        "1",
        ("cap",),
        (),
        provenance={"source_tex": "synthetic"},
        trust={"policy": "strict", "trust_floor": "residual"},
    )

    assert "missing-exported-kinds" in descriptor.validation_issues()


def test_catalog_register_get_and_sorted_names() -> None:
    catalog = PackCatalog()
    left = PackDescriptor("left", "1", ("a",), ("kind.left",), provenance={"source_tex": "synthetic"}, trust={"policy": "strict", "trust_floor": "residual"})
    right = PackDescriptor("right", "1", ("b",), ("kind.right",), provenance={"source_tex": "synthetic"}, trust={"policy": "strict", "trust_floor": "residual"})

    catalog.register(right)
    catalog.register(left)

    assert catalog.get("left") == left
    assert catalog.names() == ("left", "right")
    assert len(tuple(iter(catalog))) == 2


def test_catalog_duplicate_registration_rejects_conflicting_descriptors() -> None:
    catalog = PackCatalog()
    catalog.register(
        PackDescriptor(
            "dup",
            "1",
            ("a",),
            ("kind.a",),
            provenance={"source_tex": "synthetic"},
            trust={"policy": "strict", "trust_floor": "residual"},
        )
    )

    with pytest.raises(JuGeoError):
        catalog.register(
            PackDescriptor(
                "dup",
                "2",
                ("a",),
                ("kind.a",),
                provenance={"source_tex": "synthetic"},
                trust={"policy": "strict", "trust_floor": "residual"},
            )
        )


def test_catalog_exported_kind_index_and_adapter_queries() -> None:
    catalog = _custom_catalog()

    assert catalog.exported_kind_index()["kind.base"] == ("base",)
    adapters = catalog.adapters_for(source_kind="kind.leaf", target_kind="kind.view")
    assert len(adapters) == 1
    assert adapters[0].name == "leaf-adapter"


def test_catalog_dependency_closure_orders_dependencies_before_dependents() -> None:
    catalog = _custom_catalog()
    assert catalog.dependency_closure(("leaf",)) == ("base", "leaf")


def test_catalog_dependency_closure_rejects_cycles() -> None:
    catalog = load_pack_catalog(
        [
            {
                "name": "a",
                "version": "1",
                "capabilities": ("a",),
                "exported_kinds": ("kind.a",),
                "dependencies": ("b",),
                "provenance": {"source_tex": "synthetic"},
                "trust": {"policy": "strict", "trust_floor": "residual"},
            },
            {
                "name": "b",
                "version": "1",
                "capabilities": ("b",),
                "exported_kinds": ("kind.b",),
                "dependencies": ("a",),
                "provenance": {"source_tex": "synthetic"},
                "trust": {"policy": "strict", "trust_floor": "residual"},
            },
        ]
    )

    with pytest.raises(JuGeoError):
        catalog.dependency_closure(("a",))


def test_catalog_validate_flags_missing_dependencies() -> None:
    catalog = load_pack_catalog(
        [
            {
                "name": "leaf",
                "version": "1",
                "capabilities": ("leaf",),
                "exported_kinds": ("kind.leaf",),
                "dependencies": ("missing",),
                "provenance": {"source_tex": "synthetic"},
                "trust": {"policy": "strict", "trust_floor": "residual"},
            }
        ]
    )

    issues = catalog.validate()
    assert "leaf:missing-dependency:missing" in issues


def test_load_pack_catalog_preserves_legacy_custom_entry_behavior() -> None:
    catalog = load_pack_catalog(
        [
            {"name": "only", "version": "1", "capabilities": (), "exported_kinds": ("kind.only",)},
        ]
    )

    assert list_available_packs(catalog) == ("only",)


def test_load_pack_catalog_default_includes_builtin_descriptors(builtin_catalog: PackCatalog) -> None:
    available = list_available_packs(builtin_catalog)
    assert "packs" in available
    assert "evidence" in available
    assert "generation" in available
    assert len(available) == len(DEFAULT_PACK_DESCRIPTORS)


def test_list_available_packs_filters_by_authority_capability_and_kind(builtin_catalog: PackCatalog) -> None:
    assert "kernel" in list_available_packs(builtin_catalog, authority_floor="foundational")
    assert "ideation" not in list_available_packs(builtin_catalog, authority_floor="provisional")
    assert list_available_packs(builtin_catalog, capability="bridge-theorems") == ("packs",)
    assert list_available_packs(builtin_catalog, exported_kind="evidence.certificate") == ("evidence",)


def test_builtin_catalog_covers_manifest_subsystems(
    builtin_catalog: PackCatalog,
    manifest_subsystems: tuple[str, ...],
) -> None:
    names = set(list_available_packs(builtin_catalog))
    assert set(manifest_subsystems) <= names


def test_builtin_descriptors_have_existing_module_roots_and_test_roots(builtin_catalog: PackCatalog) -> None:
    for descriptor in builtin_catalog:
        module_root = ROOT / str(descriptor.metadata["module_root"])
        test_root = ROOT / str(descriptor.metadata["test_root"])
        assert module_root.exists(), descriptor.name
        assert test_root.exists(), descriptor.name
        assert descriptor.seal.startswith(f"pack:{descriptor.name}@")


def test_builtin_descriptors_preserve_provenance_trust_and_bridge_slots(builtin_catalog: PackCatalog) -> None:
    for descriptor in builtin_catalog:
        assert descriptor.provenance["source_tex"] == "preliminaries/theory2.tex"
        assert descriptor.trust["preset"] == "balanced"
        assert descriptor.bridge_names() == descriptor.bridge_slots
        assert descriptor.metadata["stage"] == "shared-packs"
        assert descriptor.metadata["seal"] == descriptor.seal


def test_builtin_descriptors_expose_laws_adapters_and_boundaries(builtin_catalog: PackCatalog) -> None:
    for descriptor in builtin_catalog:
        assert descriptor.laws, descriptor.name
        assert descriptor.adapters, descriptor.name
        assert descriptor.federation_boundaries, descriptor.name


def test_builtin_catalog_summary_is_json_serializable_and_issue_free(builtin_catalog: PackCatalog) -> None:
    summary = builtin_catalog.summary()
    json.dumps(summary, sort_keys=True)
    assert summary["pack_count"] == len(DEFAULT_PACK_DESCRIPTORS)
    assert summary["issues"] == []


def test_builtin_catalog_dependency_chain_reflects_stage_growth(builtin_catalog: PackCatalog) -> None:
    closure = builtin_catalog.dependency_closure(("interfaces",))
    assert closure[0] == "kernel@0.1.0"
    assert closure[-1] == "interfaces@0.1.0"
    assert "generation@0.1.0" in closure
    assert "orchestration@0.1.0" in closure
    assert "ideation@0.1.0" in closure


def test_builtin_catalog_interoperates_with_pack_loader() -> None:
    catalog = _custom_catalog()
    authority = PackAuthority(
        pack_id="leaf",
        granted_domains={"coord"},
        coordinate_jurisdiction=PackJurisdiction(coordinate_patterns=["coord"]),
        requires_certificate=False,
    )
    request = PackLoadRequest("leaf@1", "coord", TrustTier.PROPOSAL)
    loaded = load_pack(catalog, authority, request)
    assert loaded.loaded is True
    assert loaded.descriptor is not None
    assert loaded.descriptor.name == "leaf"


def test_catalog_get_and_require_handle_unknown_packs(builtin_catalog: PackCatalog) -> None:
    assert builtin_catalog.get("does-not-exist") is None

    with pytest.raises(JuGeoError) as exc_info:
        builtin_catalog.require("does-not-exist")

    assert exc_info.value.failure.code == "unknown-pack"
    assert exc_info.value.failure.coordinate == "does-not-exist"


def test_authorize_pack_interoperates_with_builtin_descriptor(builtin_catalog: PackCatalog) -> None:
    pack = builtin_catalog.require("packs")
    authority = PackAuthority(
        pack_id="packs",
        granted_domains={"coord"},
        coordinate_jurisdiction=PackJurisdiction(coordinate_patterns=["coord"]),
        requires_certificate=False,
    )

    assert authorize_pack(pack, authority, coordinate="coord", tier=TrustTier.PROPOSAL) is True
    assert authorize_pack(pack, authority, coordinate="other", tier=TrustTier.PROPOSAL) is False


def test_pack_bridge_interoperates_with_catalog_bridge_slots(builtin_catalog: PackCatalog) -> None:
    pack = builtin_catalog.require("packs")
    bridge = PackBridge(
        source_pack=pack.name,
        target_pack="solver",
        theorem_name=pack.bridge_slots[0],
        transported_symbols=("packs.pack-descriptor",),
        provenance=("catalog",),
    )
    assert bridge.connects("packs", "solver") is True
    assert bridge.provenance == ("catalog",)


def test_federation_conflict_resolution_prefers_higher_authority() -> None:
    left = PackCatalog({"left@1": PackDescriptor("left", "1", exported_kinds=("kind.left",))})
    right = PackCatalog({"right@1": PackDescriptor("right", "1", exported_kinds=("kind.right",))})
    federation = PackFederation((left, right), (PackBridge("left", "right", "transport"),))

    merged = federation.merged_catalog()
    assert merged.get("left@1") is not None
    assert merged.get("right@1") is not None
    assert federation.reachable_packs("left") == ("right",)


def test_catalog_strict_mode_rejects_invalid_catalog() -> None:
    with pytest.raises(JuGeoError):
        load_pack_catalog(
            [
                {
                    "name": "broken",
                    "version": "1",
                    "capabilities": (),
                    "exported_kinds": ("kind.broken",),
                    "dependencies": ("missing",),
                }
            ],
            strict=True,
        )


def test_builtin_catalog_names_respect_known_authority_levels(builtin_catalog: PackCatalog) -> None:
    assert KNOWN_AUTHORITY_LEVELS == ("quarantined", "exploratory", "provisional", "foundational")
    assert all(descriptor.authority in KNOWN_AUTHORITY_LEVELS for descriptor in builtin_catalog)


def test_builtin_catalog_contains_laws_matching_jugeo_semantic_goals(builtin_catalog: PackCatalog) -> None:
    evidence = builtin_catalog.get("evidence")
    packs = builtin_catalog.get("packs")
    generation = builtin_catalog.get("generation")

    assert "no-silent-promotion" in evidence.law_names()
    assert "pack-record-explicitness" in packs.law_names()
    assert "frontier-honesty" in generation.law_names()


def test_catalog_to_dict_and_summary_are_stable_shapes(builtin_catalog: PackCatalog) -> None:
    payload = builtin_catalog.to_dict()

    assert "descriptors" in payload
    assert "exported_kind_index" in payload
    assert payload["descriptors"]["packs"]["bridge_slots"]
    json.dumps(payload, sort_keys=True)


def test_catalog_future_surface_metadata_points_at_plausible_files(builtin_catalog: PackCatalog) -> None:
    for descriptor in builtin_catalog:
        future_surface = descriptor.metadata["future_surface"]
        assert isinstance(future_surface, tuple)
        assert all(isinstance(item, str) and item for item in future_surface)


def test_catalog_can_query_federation_boundaries_by_authority(builtin_catalog: PackCatalog) -> None:
    foundational = builtin_catalog.federation_boundaries(authority="foundational")
    provisional = builtin_catalog.federation_boundaries(authority="provisional")

    assert foundational
    assert all(boundary.authority == "foundational" for boundary in foundational)
    assert len(provisional) >= len(foundational)


def test_catalog_can_query_packs_exporting_kinds(builtin_catalog: PackCatalog) -> None:
    owners = builtin_catalog.packs_exporting("evidence.certificate")
    assert [descriptor.name for descriptor in owners] == ["evidence"]


def test_catalog_supports_adapter_queries_with_authority_floor(builtin_catalog: PackCatalog) -> None:
    adapters = builtin_catalog.adapters_for(
        target_kind="packs.routing-policy",
        minimum_authority="foundational",
    )

    assert adapters
    assert all(adapter.target_kind == "packs.routing-policy" for adapter in adapters)


def test_builtin_catalog_is_honest_about_default_authorities(builtin_catalog: PackCatalog) -> None:
    expected = {
        "kernel": "foundational",
        "geometry": "foundational",
        "judgments": "foundational",
        "evidence": "foundational",
        "packs": "foundational",
        "solver": "provisional",
        "runtime": "provisional",
        "generation": "provisional",
        "orchestration": "provisional",
        "ideation": "exploratory",
        "interfaces": "provisional",
        "encodings": "exploratory",
        "evaluation": "provisional",
        "foundations": "foundational",
    }
    observed = {descriptor.name: descriptor.authority for descriptor in builtin_catalog}
    assert observed == expected


def test_builtin_catalog_metadata_references_this_generation_turn(blueprint_payload: dict[str, Any], builtin_catalog: PackCatalog) -> None:
    entry = _catalog_blueprint_entry(blueprint_payload)
    packs_descriptor = builtin_catalog.get("packs")

    assert packs_descriptor.metadata["module_root"] == "src/jugeo/packs"
    assert entry["estimatedLoC"] == 717
    assert "semantic kinds, laws, and adapters" in entry["role"]
