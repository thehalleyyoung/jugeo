from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import importlib
import json
import sys
from typing import Any, Iterable, Mapping

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'src').exists())
SRC = ROOT / 'src'
TESTS = ROOT / 'tests'
SOURCE_FILE = ROOT / 'src' / 'jugeo' / 'package_manifest.py'
TEST_FILE = ROOT / 'tests' / 'jugeo' / 'test_package_manifest.py'
THEORY_TEX = ROOT / 'preliminaries' / 'theory2.tex'
BLUEPRINT_FILE = ROOT / 'theory2-src-blueprint.json'
GEN_ORDER_FILE = ROOT / 'theory2-generation-order.json'


def _bootstrap_src_package() -> None:
    """Force imports to resolve against ``src/jugeo`` instead of ``tests/jugeo``."""

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    to_remove: list[str] = []
    for name, module in list(sys.modules.items()):
        if name == __name__:
            continue
        if name != 'jugeo' and not name.startswith('jugeo.'):
            continue
        # Never remove the top-level 'jugeo' entry — doing so causes a
        # KeyError in Python 3.12+ when importlib is mid-import.
        if name == 'jugeo':
            continue
        module_file = getattr(module, '__file__', None)
        if module_file is None:
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
from jugeo.package_manifest import (
    MANIFEST_SPEC_PROVENANCE,
    THEOREM_TARGETS,
    CapabilityFlag,
    ManifestCapability,
    PackageManifest,
    SubsystemManifest,
    build_package_manifest,
    enumerate_subsystems,
    validate_manifest_shape,
)
from jugeo.runtime_defaults import PolicyPreset


EXPECTED_SUBSYSTEM_ORDER = (
    'kernel',
    'geometry',
    'judgments',
    'evidence',
    'packs',
    'solver',
    'runtime',
    'generation',
    'orchestration',
    'ideation',
    'interfaces',
)
EXPECTED_DEPENDENCIES = {
    'kernel': (),
    'geometry': ('kernel',),
    'judgments': ('geometry',),
    'evidence': ('judgments',),
    'packs': ('evidence',),
    'solver': ('evidence',),
    'runtime': ('solver',),
    'generation': ('runtime',),
    'orchestration': ('generation',),
    'ideation': ('orchestration',),
    'interfaces': ('ideation',),
}
EXPECTED_CAPABILITY_TO_SUBSYSTEM = {
    CapabilityFlag.CONFIGURATION.value: ('kernel',),
    CapabilityFlag.AUTHORITY.value: ('kernel',),
    CapabilityFlag.GEOMETRY.value: ('geometry',),
    CapabilityFlag.JUDGMENTS.value: ('judgments',),
    CapabilityFlag.EVIDENCE.value: ('evidence',),
    CapabilityFlag.COPILOT_PROPOSALS.value: ('evidence',),
    CapabilityFlag.PACKS.value: ('packs',),
    CapabilityFlag.SOLVER.value: ('solver',),
    CapabilityFlag.RUNTIME.value: ('runtime',),
    CapabilityFlag.GENERATION.value: ('generation',),
    CapabilityFlag.ORCHESTRATION.value: ('orchestration',),
    CapabilityFlag.IDEATION.value: ('ideation',),
    CapabilityFlag.INTERFACES.value: ('interfaces',),
}
EXPECTED_STAGE_PREFIX = {
    'kernel': 'shared-kernel',
    'geometry': 'shared-geometry',
    'judgments': 'shared-judgments',
    'evidence': 'shared-evidence',
    'packs': 'shared-packs',
    'solver': 'shared-solver',
    'runtime': 'shared-runtime',
    'generation': 'shared-generation',
    'orchestration': 'shared-orchestration',
    'ideation': 'shared-ideation',
    'interfaces': 'shared-interfaces',
}


@pytest.fixture(scope='module')
def manifest() -> PackageManifest:
    return build_package_manifest()


@pytest.fixture(scope='module')
def safe_manifest() -> PackageManifest:
    return build_package_manifest(preset=PolicyPreset.SAFE)


@pytest.fixture(scope='module')
def exploratory_manifest() -> PackageManifest:
    return build_package_manifest(preset=PolicyPreset.EXPLORATORY)


@pytest.fixture(scope='module')
def manifest_payload(manifest: PackageManifest) -> dict[str, Any]:
    return manifest.to_dict()


@pytest.fixture(scope='module')
def blueprint_payload() -> dict[str, Any]:
    return json.loads(BLUEPRINT_FILE.read_text())


@pytest.fixture(scope='module')
def generation_order_payload() -> dict[str, Any]:
    return json.loads(GEN_ORDER_FILE.read_text())


def _root_blueprint_entry(blueprint_payload: dict[str, Any], *, filename: str) -> dict[str, Any]:
    return next(entry for entry in blueprint_payload['rootFiles'] if entry['file'] == filename)


def _generation_entry(generation_order_payload: dict[str, Any], *, target: str) -> dict[str, Any]:
    return next(item for item in generation_order_payload['items'] if item['target'] == target)


def _subsystem_map(manifest: PackageManifest) -> dict[str, SubsystemManifest]:
    return {subsystem.name: subsystem for subsystem in manifest.subsystems}


def _capability_payload(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    capabilities = payload['capabilities']
    assert isinstance(capabilities, Mapping)
    capability_payload = capabilities[name]
    assert isinstance(capability_payload, Mapping)
    return capability_payload


def _iter_textual_sequences(subsystem: SubsystemManifest) -> Iterable[tuple[str, tuple[str, ...]]]:
    yield 'stable_exports', subsystem.stable_exports
    yield 'residual_exports', subsystem.residual_exports
    yield 'evidence_channels', subsystem.evidence_channels
    yield 'public_surfaces', subsystem.public_surfaces
    yield 'generation_notes', subsystem.generation_notes


def test_target_files_meet_requested_size_window() -> None:
    assert 15_000 < SOURCE_FILE.stat().st_size < 100_000
    assert 15_000 < TEST_FILE.stat().st_size < 100_000


def test_governing_spec_files_exist_and_are_nontrivial() -> None:
    for path in (THEORY_TEX, BLUEPRINT_FILE, GEN_ORDER_FILE):
        assert path.exists()
        assert path.stat().st_size > 100


def test_manifest_provenance_points_at_authoritative_and_structural_sources() -> None:
    assert MANIFEST_SPEC_PROVENANCE['semantic_source'] == 'preliminaries/theory2.tex'
    assert MANIFEST_SPEC_PROVENANCE['semantic_source_role'] == 'authoritative-semantic-source'
    assert MANIFEST_SPEC_PROVENANCE['structural_blueprint'] == 'theory2-src-blueprint.json'
    assert MANIFEST_SPEC_PROVENANCE['structural_generation_order'] == 'theory2-generation-order.json'
    assert MANIFEST_SPEC_PROVENANCE['structural_hint_role'] == 'structure-only'
    assert MANIFEST_SPEC_PROVENANCE['target_file'] == 'src/jugeo/package_manifest.py'
    assert MANIFEST_SPEC_PROVENANCE['target_test'] == 'tests/jugeo/test_package_manifest.py'
    assert MANIFEST_SPEC_PROVENANCE['stage'] == 'root-foundation'
    assert MANIFEST_SPEC_PROVENANCE['sequence'] == 3


def test_blueprint_entry_matches_manifest_surface(blueprint_payload: dict[str, Any]) -> None:
    entry = _root_blueprint_entry(blueprint_payload, filename='package_manifest.py')
    assert entry['role'] == 'Declares package-wide manifests, versioned capability flags, and the canonical...'
    assert set(entry['classes']) == {'PackageManifest', 'CapabilityFlag'}
    assert set(entry['functions']) == {'build_package_manifest()', 'validate_manifest_shape()', 'enumerate_subsystems()'}


def test_generation_order_entry_matches_requested_stage(generation_order_payload: dict[str, Any]) -> None:
    entry = _generation_entry(generation_order_payload, target='src/jugeo/package_manifest.py')
    assert entry['sequence'] == 3
    assert entry['scope'] == 'root'
    assert entry['stage'] == 'root-foundation'
    assert entry['dependsOn'] == ['src/jugeo/errors.py', 'src/jugeo/runtime_defaults.py']
    assert entry['test'] == 'tests/jugeo/test_package_manifest.py'


def test_package_manifest_lists_shared_subsystems(manifest: PackageManifest) -> None:
    assert enumerate_subsystems(manifest) == EXPECTED_SUBSYSTEM_ORDER
    assert 'geometry' in enumerate_subsystems(manifest)
    assert 'interfaces' in enumerate_subsystems(manifest)


def test_package_name_alias_matches_manifest_name(manifest: PackageManifest) -> None:
    assert manifest.name == 'jugeo'
    assert manifest.package_name == manifest.name
    assert manifest.version == '0.1.0'


def test_package_manifest_exposes_expected_top_level_metadata(manifest: PackageManifest) -> None:
    assert manifest.format_version == '1.0'
    assert manifest.semantic_source == 'preliminaries/theory2.tex'
    assert manifest.semantic_source_role == 'authoritative-semantic-source'
    assert manifest.structural_hints == ('theory2-src-blueprint.json', 'theory2-generation-order.json')
    assert manifest.structural_hint_role == 'structure-only'
    assert manifest.theorem_targets == THEOREM_TARGETS


def test_theorem_targets_cover_manifest_integrity_and_public_honesty() -> None:
    assert 'serialization determinism' in THEOREM_TARGETS
    assert 'dependency-trace integrity' in THEOREM_TARGETS
    assert 'stale-manifest conservativity' in THEOREM_TARGETS
    assert 'projection faithfulness' in THEOREM_TARGETS
    assert 'scope honesty' in THEOREM_TARGETS
    assert 'jurisdiction soundness' in THEOREM_TARGETS
    assert 'escalation honesty' in THEOREM_TARGETS


def test_subsystem_order_matches_expected_canonical_chain(manifest: PackageManifest) -> None:
    assert manifest.subsystem_order == EXPECTED_SUBSYSTEM_ORDER
    assert manifest.subsystem_names() == EXPECTED_SUBSYSTEM_ORDER
    assert len(manifest.subsystems) == len(EXPECTED_SUBSYSTEM_ORDER)


def test_subsystem_packages_align_with_namespaces(manifest: PackageManifest) -> None:
    subsystem_map = _subsystem_map(manifest)
    for name, subsystem in subsystem_map.items():
        assert subsystem.package == f'jugeo.{name}'
        assert subsystem.stage == EXPECTED_STAGE_PREFIX[name]


def test_subsystem_dependencies_match_expected_chain(manifest: PackageManifest) -> None:
    subsystem_map = _subsystem_map(manifest)
    assert {name: subsystem.dependencies for name, subsystem in subsystem_map.items()} == EXPECTED_DEPENDENCIES


def test_dependency_pairs_are_deterministic(manifest: PackageManifest) -> None:
    assert manifest.dependency_pairs == (
        ('kernel', 'geometry'),
        ('geometry', 'judgments'),
        ('judgments', 'evidence'),
        ('evidence', 'packs'),
        ('evidence', 'solver'),
        ('solver', 'runtime'),
        ('runtime', 'generation'),
        ('generation', 'orchestration'),
        ('orchestration', 'ideation'),
        ('ideation', 'interfaces'),
    )


def test_dependencies_name_known_prior_subsystems(manifest: PackageManifest) -> None:
    seen: set[str] = set()
    for subsystem in manifest.subsystems:
        for dependency in subsystem.dependencies:
            assert dependency in seen
        seen.add(subsystem.name)


def test_subsystem_map_and_authority_boundaries_are_complete(manifest: PackageManifest) -> None:
    assert set(manifest.subsystem_map) == set(EXPECTED_SUBSYSTEM_ORDER)
    assert set(manifest.authority_boundaries) == set(EXPECTED_SUBSYSTEM_ORDER)
    for name, boundary in manifest.authority_boundaries.items():
        assert name in EXPECTED_SUBSYSTEM_ORDER
        assert isinstance(boundary, str)
        assert boundary.strip()


def test_subsystem_scope_honesty_notes_name_non_overclaim_boundaries(manifest: PackageManifest) -> None:
    for subsystem in manifest.subsystems:
        assert isinstance(subsystem.scope_honesty, str)
        assert subsystem.scope_honesty.strip()
        assert len(subsystem.scope_honesty.split()) >= 8


def test_subsystems_publish_readable_sequence_fields(manifest: PackageManifest) -> None:
    for subsystem in manifest.subsystems:
        for field_name, values in _iter_textual_sequences(subsystem):
            assert values, f'{subsystem.name} must populate {field_name}'
            assert len(values) == len(set(values)), f'{subsystem.name} must avoid duplicates in {field_name}'
            for value in values:
                assert isinstance(value, str)
                assert value.strip()


def test_only_kernel_exposes_authority_capability(manifest: PackageManifest) -> None:
    authority_holders = [subsystem.name for subsystem in manifest.subsystems if CapabilityFlag.AUTHORITY in subsystem.capabilities]
    assert authority_holders == ['kernel']


def test_copilot_proposals_are_confined_to_evidence_subsystem(manifest: PackageManifest) -> None:
    proposal_holders = [subsystem.name for subsystem in manifest.subsystems if CapabilityFlag.COPILOT_PROPOSALS in subsystem.capabilities]
    assert proposal_holders == ['evidence']
    evidence = _subsystem_map(manifest)['evidence']
    assert 'copilot' in ' '.join(evidence.generation_notes).lower()
    assert CapabilityFlag.AUTHORITY not in evidence.capabilities


def test_interfaces_subsystem_is_last_public_projection_layer(manifest: PackageManifest) -> None:
    interfaces = manifest.subsystem_map['interfaces']
    assert interfaces.dependencies == ('ideation',)
    assert interfaces.public_surfaces == ('api', 'cli', 'serialization', 'schema', 'diagnostics')
    assert manifest.subsystem_order[-1] == 'interfaces'


def test_manifest_capability_surface_contains_expected_exporters(manifest: PackageManifest) -> None:
    assert set(manifest.capabilities) == set(EXPECTED_CAPABILITY_TO_SUBSYSTEM)
    for capability_name, exporters in EXPECTED_CAPABILITY_TO_SUBSYSTEM.items():
        surface = manifest.capabilities[capability_name]
        assert isinstance(surface, ManifestCapability)
        assert surface.enabled is True
        assert surface.surfaced_by == exporters
        assert surface.stage == manifest.subsystem_map[exporters[0]].stage
        assert surface.rationale
        assert surface.authority_boundary
        assert surface.honest_scope


def test_capability_surface_reports_non_settling_copilot_boundary(manifest: PackageManifest) -> None:
    surface = manifest.capabilities[CapabilityFlag.COPILOT_PROPOSALS.value]
    assert 'copilot' in surface.rationale.lower()
    assert 'non-settling' in surface.rationale.lower()
    assert surface.surfaced_by == ('evidence',)


def test_manifest_to_dict_contains_cli_facing_fields(manifest_payload: dict[str, Any]) -> None:
    assert manifest_payload['name'] == 'jugeo'
    assert manifest_payload['package_name'] == 'jugeo'
    assert manifest_payload['subsystem_order'] == list(EXPECTED_SUBSYSTEM_ORDER)
    assert manifest_payload['semantic_source_role'] == 'authoritative-semantic-source'
    assert manifest_payload['structural_hint_role'] == 'structure-only'
    assert set(manifest_payload['capabilities']) == set(EXPECTED_CAPABILITY_TO_SUBSYSTEM)


def test_manifest_to_dict_is_deterministic(manifest: PackageManifest) -> None:
    left = json.dumps(manifest.to_dict(), sort_keys=True)
    right = json.dumps(manifest.to_dict(), sort_keys=True)
    assert left == right


def test_validate_manifest_shape_accepts_current_payload(manifest_payload: dict[str, Any]) -> None:
    validate_manifest_shape(manifest_payload)


def test_validate_manifest_shape_rejects_missing_fields() -> None:
    with pytest.raises(JuGeoError):
        validate_manifest_shape({'name': 'jugeo'})


def test_validate_manifest_shape_rejects_non_mapping_defaults(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['defaults'] = 'not-a-mapping'
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_string_subsystems(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['subsystems'] = 'kernel'
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_non_iterable_order(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['subsystem_order'] = 17
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_length_mismatch(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['subsystem_order'] = list(manifest_payload['subsystem_order'])[:-1]
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_subsystem_ordering_violation(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    corrupted['subsystem_order'][0], corrupted['subsystem_order'][1] = corrupted['subsystem_order'][1], corrupted['subsystem_order'][0]
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_unknown_dependency_position(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    corrupted['subsystems'][1]['dependencies'] = ['runtime']
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_missing_subsystem_fields(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    del corrupted['subsystems'][0]['stage']
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_non_iterable_subsystem_sequence_field(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    corrupted['subsystems'][0]['stable_exports'] = None
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_non_mapping_capabilities(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['capabilities'] = ['runtime']
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_empty_capability_map(manifest_payload: dict[str, Any]) -> None:
    corrupted = dict(manifest_payload)
    corrupted['capabilities'] = {}
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_missing_capability_fields(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    del corrupted['capabilities'][CapabilityFlag.RUNTIME.value]['stage']
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_non_boolean_enabled_flag(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    corrupted['capabilities'][CapabilityFlag.RUNTIME.value]['enabled'] = 'yes'
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_validate_manifest_shape_rejects_invalid_capability_surfaced_by(manifest_payload: dict[str, Any]) -> None:
    corrupted = json.loads(json.dumps(manifest_payload))
    corrupted['capabilities'][CapabilityFlag.RUNTIME.value]['surfaced_by'] = 'runtime'
    with pytest.raises(JuGeoError):
        validate_manifest_shape(corrupted)


def test_manifest_payload_capabilities_match_runtime_projection(manifest: PackageManifest, manifest_payload: dict[str, Any]) -> None:
    for capability_name, surface in manifest.capabilities.items():
        payload = _capability_payload(manifest_payload, capability_name)
        assert payload['enabled'] is surface.enabled
        assert payload['stage'] == surface.stage
        assert payload['rationale'] == surface.rationale
        assert payload['surfaced_by'] == list(surface.surfaced_by)
        assert payload['authority_boundary'] == surface.authority_boundary
        assert payload['honest_scope'] == surface.honest_scope


def test_presets_change_defaults_without_changing_manifest_structure(
    manifest: PackageManifest,
    safe_manifest: PackageManifest,
    exploratory_manifest: PackageManifest,
) -> None:
    assert manifest.subsystem_order == safe_manifest.subsystem_order == exploratory_manifest.subsystem_order
    assert manifest.dependency_pairs == safe_manifest.dependency_pairs == exploratory_manifest.dependency_pairs
    assert tuple(manifest.capabilities) == tuple(safe_manifest.capabilities) == tuple(exploratory_manifest.capabilities)
    assert manifest.defaults.preset is PolicyPreset.BALANCED
    assert safe_manifest.defaults.preset is PolicyPreset.SAFE
    assert exploratory_manifest.defaults.preset is PolicyPreset.EXPLORATORY
    assert manifest.defaults.replay_depth < exploratory_manifest.defaults.replay_depth
    assert safe_manifest.defaults.replay_depth < exploratory_manifest.defaults.replay_depth


def test_manifest_objects_are_immutable(manifest: PackageManifest) -> None:
    with pytest.raises(FrozenInstanceError):
        manifest.name = 'other'

    with pytest.raises(FrozenInstanceError):
        manifest.subsystems[0].name = 'changed'


def test_manifest_capability_object_is_immutable(manifest: PackageManifest) -> None:
    capability = manifest.capabilities[CapabilityFlag.RUNTIME.value]
    with pytest.raises(FrozenInstanceError):
        capability.stage = 'mutable-now'


def test_subsystem_declares_helper_handles_enum_and_string(manifest: PackageManifest) -> None:
    runtime = manifest.subsystem_map['runtime']
    assert runtime.declares(CapabilityFlag.RUNTIME)
    assert runtime.declares('runtime')
    assert not runtime.declares(CapabilityFlag.SOLVER)


def test_manifest_capabilities_are_easy_to_serialize(manifest: PackageManifest) -> None:
    payload = {name: capability.to_dict() for name, capability in manifest.capabilities.items()}
    encoded = json.dumps(payload, sort_keys=True)
    assert 'copilot-proposals' in encoded
    assert 'runtime' in encoded


def test_each_subsystem_mentions_its_public_surface_or_boundary(manifest: PackageManifest) -> None:
    for subsystem in manifest.subsystems:
        combined = ' '.join(subsystem.public_surfaces + subsystem.generation_notes + subsystem.stable_exports).lower()
        assert subsystem.name in combined or subsystem.package.split('.')[-1] in combined or combined


def test_subsystem_evidence_channels_reflect_theory_plurality(manifest: PackageManifest) -> None:
    evidence = manifest.subsystem_map['evidence']
    assert set(evidence.evidence_channels) == {'proof', 'solver', 'runtime', 'controlled-semantic', 'human-review'}
    runtime = manifest.subsystem_map['runtime']
    assert set(runtime.evidence_channels) == {'runtime', 'solver'}


def test_runtime_boundary_mentions_stale_manifest_conservativity(manifest: PackageManifest) -> None:
    runtime = manifest.subsystem_map['runtime']
    assert 'stale' in runtime.authority_boundary.lower() or 'invalidating' in runtime.authority_boundary.lower()
    assert 'reopen' in runtime.scope_honesty.lower() or 'downgrade' in runtime.scope_honesty.lower()


def test_interfaces_boundary_mentions_public_honesty(manifest: PackageManifest) -> None:
    interfaces = manifest.subsystem_map['interfaces']
    assert 'public' in interfaces.scope_honesty.lower()
    assert 'invent stronger closure' in interfaces.authority_boundary.lower()


def test_package_manifest_builds_cli_facing_capability_projection(manifest: PackageManifest) -> None:
    interfaces_surface = manifest.capabilities[CapabilityFlag.INTERFACES.value]
    assert interfaces_surface.stage == 'shared-interfaces'
    assert interfaces_surface.surfaced_by == ('interfaces',)
    assert 'public api' in interfaces_surface.rationale.lower() or 'api' in interfaces_surface.rationale.lower()


def test_subsystem_serialization_round_trips_textually(manifest: PackageManifest) -> None:
    first = manifest.subsystems[0].to_dict()
    assert first['name'] == 'kernel'
    assert first['package'] == 'jugeo.kernel'
    assert first['capabilities'] == ['configuration', 'authority']
    assert first['dependencies'] == []
    assert first['stage'] == 'shared-kernel'


def test_manifest_payload_subsystems_round_trip_expected_dependencies(manifest_payload: dict[str, Any]) -> None:
    payload_map = {entry['name']: entry for entry in manifest_payload['subsystems']}
    assert {name: tuple(entry['dependencies']) for name, entry in payload_map.items()} == EXPECTED_DEPENDENCIES


def test_manifest_payload_retains_authority_and_scope_text(manifest_payload: dict[str, Any]) -> None:
    for subsystem in manifest_payload['subsystems']:
        assert subsystem['authority_boundary']
        assert subsystem['scope_honesty']
        assert subsystem['stage'].startswith('shared-')


def test_manifest_package_constructor_rejects_wrong_source_roles(manifest: PackageManifest) -> None:
    with pytest.raises(ValueError):
        PackageManifest(
            name=manifest.name,
            version=manifest.version,
            defaults=manifest.defaults,
            subsystems=manifest.subsystems,
            semantic_source_role='blueprint',
        )

    with pytest.raises(ValueError):
        PackageManifest(
            name=manifest.name,
            version=manifest.version,
            defaults=manifest.defaults,
            subsystems=manifest.subsystems,
            structural_hint_role='authoritative',
        )


def test_subsystem_constructor_rejects_copilot_authority_escalation() -> None:
    with pytest.raises(ValueError):
        SubsystemManifest(
            name='bad',
            package='jugeo.bad',
            capabilities=(CapabilityFlag.COPILOT_PROPOSALS, CapabilityFlag.AUTHORITY),
            authority_boundary='bad boundary',
            scope_honesty='cannot overclaim',
            stable_exports=('broken',),
            evidence_channels=('controlled-semantic',),
        )


def test_subsystem_constructor_rejects_self_dependencies() -> None:
    with pytest.raises(ValueError):
        SubsystemManifest(
            name='loop',
            package='jugeo.loop',
            capabilities=(CapabilityFlag.RUNTIME,),
            dependencies=('loop',),
            authority_boundary='self-loop boundary',
            scope_honesty='does not overclaim beyond loop',
            stable_exports=('loop export',),
            evidence_channels=('runtime',),
        )


def test_manifest_capability_constructor_requires_exporters() -> None:
    with pytest.raises(ValueError):
        ManifestCapability(
            name='runtime',
            enabled=True,
            stage='shared-runtime',
            rationale='runtime export',
            surfaced_by=(),
            authority_boundary='runtime only',
            honest_scope='does not widen scope',
        )


def test_manifest_serialization_preserves_capability_order(manifest_payload: dict[str, Any]) -> None:
    assert tuple(manifest_payload['capabilities']) == tuple(EXPECTED_CAPABILITY_TO_SUBSYSTEM)


def test_manifest_contains_expected_runtime_defaults_shape(manifest_payload: dict[str, Any]) -> None:
    defaults = manifest_payload['defaults']
    assert defaults['preset'] == 'balanced'
    assert defaults['trust_policy']['silent_promotion_allowed'] is False
    assert defaults['frontier_budget']['max_parallel'] == 4


def test_manifest_source_policy_is_explicitly_theory_first(manifest_payload: dict[str, Any]) -> None:
    assert manifest_payload['semantic_source'] == 'preliminaries/theory2.tex'
    assert manifest_payload['semantic_source_role'] == 'authoritative-semantic-source'
    assert manifest_payload['structural_hints'] == ['theory2-src-blueprint.json', 'theory2-generation-order.json']
    assert manifest_payload['structural_hint_role'] == 'structure-only'


def test_manifest_scope_honesty_is_human_and_llm_readable(manifest: PackageManifest) -> None:
    for subsystem in manifest.subsystems:
        text = ' '.join((subsystem.authority_boundary, subsystem.scope_honesty, *subsystem.generation_notes))
        assert len(text) > 40
        assert '.' in text or ',' in text


def test_manifest_dependency_chain_keeps_branch_at_evidence(manifest: PackageManifest) -> None:
    packs = manifest.subsystem_map['packs']
    solver = manifest.subsystem_map['solver']
    assert packs.dependencies == ('evidence',)
    assert solver.dependencies == ('evidence',)
    assert packs.stage == 'shared-packs'
    assert solver.stage == 'shared-solver'


def test_manifest_enumeration_matches_subsystem_map_keys(manifest: PackageManifest) -> None:
    assert enumerate_subsystems(manifest) == tuple(manifest.subsystem_map.keys())


def test_capability_payloads_reference_existing_subsystems(manifest_payload: dict[str, Any]) -> None:
    known = set(manifest_payload['subsystem_order'])
    for payload in manifest_payload['capabilities'].values():
        assert set(payload['surfaced_by']).issubset(known)


def test_manifest_generation_notes_keep_semantic_and_structural_roles_distinct(manifest: PackageManifest) -> None:
    for subsystem in manifest.subsystems:
        joined = ' '.join(subsystem.generation_notes).lower()
        assert 'structural hint' in joined
        assert 'semantic' in joined or 'theory' in joined


def test_manifest_fields_support_json_round_trip_without_loss(manifest_payload: dict[str, Any]) -> None:
    encoded = json.dumps(manifest_payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded['package_name'] == 'jugeo'
    assert decoded['subsystem_order'] == list(EXPECTED_SUBSYSTEM_ORDER)
    assert decoded['capabilities'][CapabilityFlag.COPILOT_PROPOSALS.value]['surfaced_by'] == ['evidence']
