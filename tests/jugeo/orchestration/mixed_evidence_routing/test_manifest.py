"""Tests for jugeo.orchestration.mixed_evidence_routing.manifest (theory2.tex Ch45)."""

from __future__ import annotations

import time
from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.orchestration.mixed_evidence_routing.manifest import (
    MixedEvidenceRoutingManifest,
    ChannelRegistry,
    JurisdictionCatalog,
    RoutingConfiguration,
    build_manifest,
    validate_manifest,
    get_default_jurisdiction_maps,
    get_channel_trust_ceilings,
)
from jugeo.orchestration.mixed_evidence_routing.models import (
    EvidenceChannel,
    JurisdictionMap,
    RoutingStrategy,
)

# ---------------------------------------------------------------------------
# Graceful upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import OrchestratorState, MoveKind
    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember
    _FLEET_AVAILABLE = True
except Exception:
    _FLEET_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustCeiling
    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

try:
    from jugeo.geometry.descent import DescentEngine, GluingData, OverlapStatus
    _DESCENT_AVAILABLE = True
except Exception:
    _DESCENT_AVAILABLE = False


# ===========================================================================
# MixedEvidenceRoutingManifest tests
# ===========================================================================


class TestMixedEvidenceRoutingManifest:
    def test_default_values(self):
        m = MixedEvidenceRoutingManifest()
        assert m.version == "1.0.0"
        assert m.chapter_ref == "Ch45"
        assert m.package_name == "mixed_evidence_routing"
        assert m.author == "jugeo"
        assert len(m.description) > 10

    def test_created_at_is_recent(self):
        before = time.time()
        m = MixedEvidenceRoutingManifest()
        after = time.time()
        assert before <= m.created_at <= after

    def test_to_dict_keys(self):
        m = MixedEvidenceRoutingManifest()
        dct = m.to_dict()
        expected = {
            "version", "chapter_ref", "package_name",
            "author", "description", "created_at",
        }
        assert set(dct.keys()) == expected

    def test_to_dict_values_match_fields(self):
        m = MixedEvidenceRoutingManifest()
        dct = m.to_dict()
        assert dct["version"] == m.version
        assert dct["author"] == m.author

    def test_validate_default_is_valid(self):
        m = MixedEvidenceRoutingManifest()
        errors = m.validate()
        assert errors == []

    def test_is_valid_default(self):
        m = MixedEvidenceRoutingManifest()
        assert m.is_valid() is True

    def test_validate_bad_version(self):
        m = MixedEvidenceRoutingManifest(version="not-semver")
        errors = m.validate()
        assert any("version" in e for e in errors)

    def test_validate_empty_package_name(self):
        m = MixedEvidenceRoutingManifest(package_name="")
        errors = m.validate()
        assert any("package_name" in e for e in errors)

    def test_validate_empty_author(self):
        m = MixedEvidenceRoutingManifest(author="")
        errors = m.validate()
        assert any("author" in e for e in errors)

    def test_validate_bad_chapter_ref(self):
        m = MixedEvidenceRoutingManifest(chapter_ref="45")
        errors = m.validate()
        assert any("chapter_ref" in e for e in errors)

    def test_validate_negative_timestamp(self):
        m = MixedEvidenceRoutingManifest(created_at=-1.0)
        errors = m.validate()
        assert any("created_at" in e for e in errors)

    def test_summary_contains_package_name(self):
        m = MixedEvidenceRoutingManifest()
        s = m.summary()
        assert "mixed_evidence_routing" in s

    def test_summary_contains_version(self):
        m = MixedEvidenceRoutingManifest()
        s = m.summary()
        assert "1.0.0" in s

    def test_is_valid_false_for_bad_manifest(self):
        m = MixedEvidenceRoutingManifest(version="bad", package_name="", author="")
        assert m.is_valid() is False

    def test_frozen_immutability(self):
        m = MixedEvidenceRoutingManifest()
        with pytest.raises((AttributeError, TypeError)):
            m.version = "2.0.0"  # type: ignore[misc]


# ===========================================================================
# ChannelRegistry tests
# ===========================================================================


class TestChannelRegistry:
    def test_default_registers_all_channels(self):
        reg = ChannelRegistry.default()
        for channel in EvidenceChannel:
            assert reg.is_registered(channel.value), f"{channel.value} not registered"

    def test_default_channel_count(self):
        reg = ChannelRegistry.default()
        assert reg.channel_count() == len(list(EvidenceChannel))

    def test_registry_id_non_empty(self):
        reg = ChannelRegistry.default()
        assert reg.registry_id

    def test_get_config_returns_dict(self):
        reg = ChannelRegistry.default()
        cfg = reg.get_config(EvidenceChannel.Z3.value)
        assert isinstance(cfg, dict)
        assert "trust_ceiling" in cfg

    def test_get_config_missing_returns_none(self):
        reg = ChannelRegistry.default()
        assert reg.get_config("nonexistent") is None

    def test_get_config_returns_copy(self):
        reg = ChannelRegistry.default()
        cfg = reg.get_config(EvidenceChannel.Z3.value)
        cfg["extra_key"] = "should not persist"
        assert "extra_key" not in reg.get_config(EvidenceChannel.Z3.value)

    def test_register_new_channel(self):
        reg = ChannelRegistry.default()
        reg.register("custom_channel", {"capacity": 5, "trust_ceiling": "UNVERIFIED"})
        assert reg.is_registered("custom_channel")

    def test_register_overwrites_existing(self):
        reg = ChannelRegistry.default()
        reg.register(EvidenceChannel.Z3.value, {"capacity": 999})
        cfg = reg.get_config(EvidenceChannel.Z3.value)
        assert cfg["capacity"] == 999

    def test_deregister_existing(self):
        reg = ChannelRegistry.default()
        result = reg.deregister(EvidenceChannel.HUMAN.value)
        assert result is True
        assert not reg.is_registered(EvidenceChannel.HUMAN.value)

    def test_deregister_nonexistent(self):
        reg = ChannelRegistry.default()
        result = reg.deregister("no_such_channel")
        assert result is False

    def test_list_channels_sorted(self):
        reg = ChannelRegistry.default()
        channels = reg.list_channels()
        assert channels == sorted(channels)

    def test_update_config_success(self):
        reg = ChannelRegistry.default()
        result = reg.update_config(EvidenceChannel.Z3.value, "capacity", 42)
        assert result is True
        assert reg.get_config(EvidenceChannel.Z3.value)["capacity"] == 42

    def test_update_config_missing_channel(self):
        reg = ChannelRegistry.default()
        result = reg.update_config("ghost_channel", "capacity", 1)
        assert result is False

    def test_to_dict_structure(self):
        reg = ChannelRegistry.default()
        dct = reg.to_dict()
        assert "registry_id" in dct
        assert "channels" in dct
        assert isinstance(dct["channels"], dict)

    def test_default_z3_trust_ceiling(self):
        reg = ChannelRegistry.default()
        cfg = reg.get_config(EvidenceChannel.Z3.value)
        assert cfg["trust_ceiling"] == "SOLVER_DISCHARGED"

    def test_default_copilot_trust_ceiling(self):
        reg = ChannelRegistry.default()
        cfg = reg.get_config(EvidenceChannel.COPILOT_LLM.value)
        assert cfg["trust_ceiling"] == "COPILOT_SUGGESTED"

    def test_is_registered_false(self):
        reg = ChannelRegistry.default()
        assert reg.is_registered("definitely_not_a_channel") is False


# ===========================================================================
# JurisdictionCatalog tests
# ===========================================================================


class TestJurisdictionCatalog:
    def test_default_creates_catalog(self):
        cat = JurisdictionCatalog.default()
        assert len(cat.jurisdiction_maps) == 5  # one per channel

    def test_default_catalog_id_non_empty(self):
        cat = JurisdictionCatalog.default()
        assert cat.catalog_id

    def test_add_map_increases_count(self):
        cat = JurisdictionCatalog.default()
        n_before = len(cat.jurisdiction_maps)
        new_map = JurisdictionMap.new(EvidenceChannel.Z3, ["special"])
        cat.add_map(new_map)
        assert len(cat.jurisdiction_maps) == n_before + 1

    def test_remove_map_success(self):
        cat = JurisdictionCatalog.default()
        jmap = cat.jurisdiction_maps[0]
        result = cat.remove_map(jmap.map_id)
        assert result is True

    def test_remove_map_not_found(self):
        cat = JurisdictionCatalog.default()
        result = cat.remove_map("fake-id-xyz")
        assert result is False

    def test_get_maps_for_channel_z3(self):
        cat = JurisdictionCatalog.default()
        maps = cat.get_maps_for_channel(EvidenceChannel.Z3)
        assert len(maps) >= 1
        assert all(m.channel == EvidenceChannel.Z3 for m in maps)

    def test_find_capable_channels_for_equality(self):
        cat = JurisdictionCatalog.default()
        channels = cat.find_capable_channels("equality")
        assert EvidenceChannel.Z3 in channels or EvidenceChannel.COMPOSITE in channels

    def test_find_capable_channels_for_natural_language(self):
        cat = JurisdictionCatalog.default()
        channels = cat.find_capable_channels("natural_language")
        assert EvidenceChannel.COPILOT_LLM in channels or EvidenceChannel.COMPOSITE in channels

    def test_find_capable_channels_unknown_kind(self):
        cat = JurisdictionCatalog.default()
        channels = cat.find_capable_channels("unknown_claim_kind_xyz")
        assert channels == []

    def test_all_claim_kinds_non_empty(self):
        cat = JurisdictionCatalog.default()
        kinds = cat.all_claim_kinds()
        assert len(kinds) > 0

    def test_all_claim_kinds_sorted(self):
        cat = JurisdictionCatalog.default()
        kinds = cat.all_claim_kinds()
        assert kinds == sorted(kinds)

    def test_all_claim_kinds_no_duplicates(self):
        cat = JurisdictionCatalog.default()
        kinds = cat.all_claim_kinds()
        assert len(kinds) == len(set(kinds))

    def test_to_dict_structure(self):
        cat = JurisdictionCatalog.default()
        dct = cat.to_dict()
        assert "catalog_id" in dct
        assert "jurisdiction_maps" in dct
        assert "jurisdiction_map_count" in dct

    def test_to_dict_count_matches_list(self):
        cat = JurisdictionCatalog.default()
        dct = cat.to_dict()
        assert dct["jurisdiction_map_count"] == len(dct["jurisdiction_maps"])

    def test_validate_default_no_errors(self):
        cat = JurisdictionCatalog.default()
        errors = cat.validate()
        assert errors == []

    def test_validate_empty_catalog(self):
        cat = JurisdictionCatalog(catalog_id="x", jurisdiction_maps=[])
        errors = cat.validate()
        assert any("no jurisdiction maps" in e.lower() for e in errors)

    def test_find_capable_channels_deduplicates(self):
        cat = JurisdictionCatalog.default()
        # Add a duplicate map for Z3
        cat.add_map(JurisdictionMap.new(EvidenceChannel.Z3, ["equality"]))
        channels = cat.find_capable_channels("equality")
        z3_count = channels.count(EvidenceChannel.Z3)
        assert z3_count == 1


# ===========================================================================
# RoutingConfiguration tests
# ===========================================================================


class TestRoutingConfiguration:
    def test_default_creates_config(self):
        cfg = RoutingConfiguration.default()
        assert cfg.config_id
        assert cfg.retry_limit >= 0

    def test_default_strategy_is_trust_optimal(self):
        cfg = RoutingConfiguration.default()
        assert cfg.default_strategy == RoutingStrategy.TRUST_OPTIMAL

    def test_default_fallback_is_human(self):
        cfg = RoutingConfiguration.default()
        assert cfg.fallback_channel == EvidenceChannel.HUMAN

    def test_default_copilot_ceiling(self):
        cfg = RoutingConfiguration.default()
        assert cfg.trust_ceiling_copilot == "COPILOT_SUGGESTED"

    def test_to_dict_keys(self):
        cfg = RoutingConfiguration.default()
        dct = cfg.to_dict()
        expected = {
            "config_id", "default_strategy", "max_routing_latency_ms",
            "cost_budget_per_task", "trust_ceiling_copilot",
            "enable_composite_routing", "fallback_channel", "retry_limit",
        }
        assert set(dct.keys()) == expected

    def test_to_dict_strategy_is_string(self):
        cfg = RoutingConfiguration.default()
        dct = cfg.to_dict()
        assert isinstance(dct["default_strategy"], str)

    def test_validate_default_no_errors(self):
        cfg = RoutingConfiguration.default()
        errors = cfg.validate()
        assert errors == []

    def test_validate_negative_latency(self):
        cfg = RoutingConfiguration.default()
        cfg.max_routing_latency_ms = -1.0
        errors = cfg.validate()
        assert any("latency" in e for e in errors)

    def test_validate_negative_cost_budget(self):
        cfg = RoutingConfiguration.default()
        cfg.cost_budget_per_task = -0.01
        errors = cfg.validate()
        assert any("cost" in e for e in errors)

    def test_validate_negative_retry_limit(self):
        cfg = RoutingConfiguration.default()
        cfg.retry_limit = -1
        errors = cfg.validate()
        assert any("retry" in e for e in errors)

    def test_with_strategy_returns_new_config(self):
        cfg = RoutingConfiguration.default()
        cfg2 = cfg.with_strategy(RoutingStrategy.COST_OPTIMAL)
        assert cfg2.default_strategy == RoutingStrategy.COST_OPTIMAL
        # Original unchanged
        assert cfg.default_strategy == RoutingStrategy.TRUST_OPTIMAL

    def test_with_strategy_preserves_other_fields(self):
        cfg = RoutingConfiguration.default()
        original_retry = cfg.retry_limit
        cfg2 = cfg.with_strategy(RoutingStrategy.LOAD_BALANCED)
        assert cfg2.retry_limit == original_retry
        assert cfg2.config_id == cfg.config_id


# ===========================================================================
# Module-level function tests
# ===========================================================================


class TestBuildManifest:
    def test_returns_dict(self):
        result = build_manifest()
        assert isinstance(result, dict)

    def test_top_level_keys(self):
        result = build_manifest()
        assert "manifest" in result
        assert "registry" in result
        assert "catalog" in result
        assert "config" in result

    def test_manifest_section_has_version(self):
        result = build_manifest()
        assert "version" in result["manifest"]

    def test_registry_section_has_channels(self):
        result = build_manifest()
        assert "channels" in result["registry"]

    def test_catalog_section_has_maps(self):
        result = build_manifest()
        assert "jurisdiction_maps" in result["catalog"]
        assert len(result["catalog"]["jurisdiction_maps"]) > 0

    def test_config_section_has_strategy(self):
        result = build_manifest()
        assert "default_strategy" in result["config"]


class TestValidateManifest:
    def test_valid_manifest_passes(self):
        m_dict = build_manifest()
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is True
        assert errors == []

    def test_missing_key_fails(self):
        m_dict = build_manifest()
        del m_dict["registry"]
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False
        assert any("registry" in e for e in errors)

    def test_empty_dict_fails(self):
        is_valid, errors = validate_manifest({})
        assert is_valid is False
        assert len(errors) > 0

    def test_bad_inner_manifest_version(self):
        m_dict = build_manifest()
        m_dict["manifest"]["version"] = "bad-version"
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False

    def test_empty_registry_channels(self):
        m_dict = build_manifest()
        m_dict["registry"]["channels"] = {}
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False

    def test_registry_missing_channels_key(self):
        m_dict = build_manifest()
        del m_dict["registry"]["channels"]
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False

    def test_catalog_missing_maps_key(self):
        m_dict = build_manifest()
        del m_dict["catalog"]["jurisdiction_maps"]
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False

    def test_config_missing_strategy_key(self):
        m_dict = build_manifest()
        del m_dict["config"]["default_strategy"]
        is_valid, errors = validate_manifest(m_dict)
        assert is_valid is False

    def test_returns_tuple(self):
        result = validate_manifest(build_manifest())
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestGetDefaultJurisdictionMaps:
    def test_returns_list(self):
        maps = get_default_jurisdiction_maps()
        assert isinstance(maps, list)

    def test_returns_five_maps(self):
        maps = get_default_jurisdiction_maps()
        assert len(maps) == 5

    def test_all_channels_represented(self):
        maps = get_default_jurisdiction_maps()
        channels = {m.channel for m in maps}
        assert EvidenceChannel.Z3 in channels
        assert EvidenceChannel.COPILOT_LLM in channels
        assert EvidenceChannel.RUNTIME_WITNESS in channels
        assert EvidenceChannel.HUMAN in channels
        assert EvidenceChannel.COMPOSITE in channels

    def test_z3_map_handles_equality(self):
        maps = get_default_jurisdiction_maps()
        z3_map = next(m for m in maps if m.channel == EvidenceChannel.Z3)
        assert "equality" in z3_map.supported_claim_kinds

    def test_copilot_map_handles_natural_language(self):
        maps = get_default_jurisdiction_maps()
        copliot_map = next(m for m in maps if m.channel == EvidenceChannel.COPILOT_LLM)
        assert "natural_language" in copliot_map.supported_claim_kinds

    def test_human_map_max_complexity_highest(self):
        maps = get_default_jurisdiction_maps()
        human_map = next(m for m in maps if m.channel == EvidenceChannel.HUMAN)
        z3_map = next(m for m in maps if m.channel == EvidenceChannel.Z3)
        assert human_map.max_complexity >= z3_map.max_complexity

    def test_composite_map_covers_all_others(self):
        maps = get_default_jurisdiction_maps()
        composite = next(m for m in maps if m.channel == EvidenceChannel.COMPOSITE)
        non_composite = [m for m in maps if m.channel != EvidenceChannel.COMPOSITE]
        for other in non_composite:
            for kind in other.supported_claim_kinds:
                assert kind in composite.supported_claim_kinds, (
                    f"Composite missing kind {kind!r} from {other.channel.value}"
                )

    def test_all_maps_have_unique_ids(self):
        maps = get_default_jurisdiction_maps()
        ids = [m.map_id for m in maps]
        assert len(ids) == len(set(ids))


class TestGetChannelTrustCeilings:
    def test_returns_dict(self):
        ceilings = get_channel_trust_ceilings()
        assert isinstance(ceilings, dict)

    def test_all_channels_present(self):
        ceilings = get_channel_trust_ceilings()
        for channel in EvidenceChannel:
            assert channel.value in ceilings, f"{channel.value} missing from ceilings"

    def test_z3_ceiling_is_solver_discharged(self):
        ceilings = get_channel_trust_ceilings()
        assert ceilings[EvidenceChannel.Z3.value] == "SOLVER_DISCHARGED"

    def test_copilot_ceiling_is_copilot_suggested(self):
        ceilings = get_channel_trust_ceilings()
        assert ceilings[EvidenceChannel.COPILOT_LLM.value] == "COPILOT_SUGGESTED"

    def test_human_ceiling_is_human_attested(self):
        ceilings = get_channel_trust_ceilings()
        assert ceilings[EvidenceChannel.HUMAN.value] == "HUMAN_ATTESTED"

    def test_runtime_witness_ceiling(self):
        ceilings = get_channel_trust_ceilings()
        assert ceilings[EvidenceChannel.RUNTIME_WITNESS.value] == "RUNTIME_WITNESSED"

    def test_all_ceilings_are_strings(self):
        ceilings = get_channel_trust_ceilings()
        for k, v in ceilings.items():
            assert isinstance(v, str), f"Ceiling for {k} is not a string"


# ===========================================================================
# Integration tests with upstream modules (graceful skip)
# ===========================================================================


@pytest.mark.skipif(not _TRUST_AVAILABLE, reason="jugeo.evidence.trust not available")
class TestTrustIntegration:
    def test_trust_level_ceilings_align(self):
        """Every trust ceiling in the registry must name a valid TrustLevel."""
        trust_names = {m.name for m in TrustLevel}
        reg = ChannelRegistry.default()
        for channel_name, cfg in reg.channels.items():
            ceiling = cfg.get("trust_ceiling", "")
            assert ceiling in trust_names, (
                f"Channel {channel_name!r} has unknown ceiling {ceiling!r}"
            )

    def test_jurisdiction_map_trust_levels_align(self):
        trust_names = {m.name for m in TrustLevel}
        maps = get_default_jurisdiction_maps()
        for jm in maps:
            assert jm.min_trust_level in trust_names, (
                f"JurisdictionMap for {jm.channel.value} has unknown min_trust_level "
                f"{jm.min_trust_level!r}"
            )


@pytest.mark.skipif(not _CONTROLLER_AVAILABLE, reason="jugeo.orchestration.controller not available")
class TestControllerIntegration:
    def test_orchestrator_state_exists(self):
        assert OrchestratorState is not None

    def test_routing_config_retry_limit_compatible(self):
        cfg = RoutingConfiguration.default()
        # retry_limit should be a reasonable non-negative integer
        assert isinstance(cfg.retry_limit, int)
        assert cfg.retry_limit >= 0


@pytest.mark.skipif(not _FLEET_AVAILABLE, reason="jugeo.orchestration.fleet not available")
class TestFleetIntegration:
    def test_fleet_and_member_imports(self):
        assert Fleet is not None
        assert FleetMember is not None


@pytest.mark.skipif(not _DESCENT_AVAILABLE, reason="jugeo.geometry.descent not available")
class TestDescentIntegration:
    def test_descent_imports(self):
        assert DescentEngine is not None
        assert GluingData is not None
        assert OverlapStatus is not None
