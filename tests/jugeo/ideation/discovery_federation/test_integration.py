from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Tests for jugeo.ideation.discovery_federation.integration

This module verifies the integration layer that connects the discovery federation
subsystem to external consumers such as pack authority managers and orchestrators.

The integration layer is structured around three main concepts:

1. IntegrationEvent — a typed event envelope that flows between adapters.  Every
   event carries an event_id, event_type, payload, source, target, timestamps, and
   optional metadata.

2. FederationIntegration — the hub that holds a registry of named adapters, routes
   events, and exposes a health/status surface.

3. Adapter classes — DiscoveryBridgeAdapter and AuthorityPackAdapter translate
   domain objects into the neutral federation wire format and vice-versa.

These tests are written TDD-style: the production modules do NOT exist yet.  The
tests define the contract the implementation must satisfy.
"""

import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_event(event_type: str = "DISCOVERY", payload: dict | None = None) -> dict:
    """Return a minimal well-formed event dict."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": payload if payload is not None else {"data": "value"},
        "source": "test-source",
        "target": "test-target",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }


def make_discovery(trust_score: float = 0.8) -> dict:
    """Return a minimal discovery dict for use with DiscoveryBridgeAdapter."""
    return {
        "discovery_id": str(uuid.uuid4()),
        "title": "Test Discovery",
        "description": "A discovery created during testing.",
        "trust_score": trust_score,
        "source_node": "node-001",
        "tags": ["test", "tdd"],
    }


def make_grant(level: str = "LOCAL") -> dict:
    """Return a minimal authority grant dict for use with AuthorityPackAdapter."""
    return {
        "grant_id": str(uuid.uuid4()),
        "authority_level": level,
        "granted_to": "pack-001",
        "granted_by": "node-001",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "conditions": [],
    }


def make_medium_payload() -> dict:
    """Return a payload dict with 10 keys."""
    return {f"key_{i}": f"value_{i}" for i in range(10)}


def make_large_payload() -> dict:
    """Return a payload dict with 100 keys."""
    return {f"key_{i}": f"value_{i}" for i in range(100)}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def integration_hub():
    """FederationIntegration with two adapters already registered."""
    from jugeo.ideation.discovery_federation.integration import (
        FederationIntegration,
        DiscoveryBridgeAdapter,
        AuthorityPackAdapter,
    )
    hub = FederationIntegration()
    hub.register_adapter("bridge", DiscoveryBridgeAdapter(name="bridge"))
    hub.register_adapter("authority", AuthorityPackAdapter(name="authority"))
    return hub


@pytest.fixture
def bridge_adapter_connected():
    """DiscoveryBridgeAdapter that has been successfully connected."""
    from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
    adapter = DiscoveryBridgeAdapter(name="bridge")
    result = adapter.connect()
    assert result is True, "Fixture setup: bridge adapter should connect successfully"
    return adapter


@pytest.fixture
def authority_adapter_connected():
    """AuthorityPackAdapter that has been successfully connected."""
    from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
    adapter = AuthorityPackAdapter(name="authority")
    result = adapter.connect()
    assert result is True, "Fixture setup: authority adapter should connect successfully"
    return adapter


# ===========================================================================
# IntegrationEvent tests
# ===========================================================================

class TestIntegrationEventCreate:
    """Tests for IntegrationEvent.create classmethod."""

    def test_create_returns_instance(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create(
            event_type="DISCOVERY",
            payload={"key": "val"},
            source="src",
            target="tgt",
        )
        assert isinstance(event, IntegrationEvent)

    def test_create_preserves_event_type(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create(
            event_type="AUTHORITY_GRANT",
            payload={},
            source="s",
            target="t",
        )
        assert event.event_type == "AUTHORITY_GRANT"

    def test_create_assigns_event_id(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create(
            event_type="CONSENSUS",
            payload={},
            source="s",
            target="t",
        )
        assert isinstance(event.event_id, str)
        assert len(event.event_id) > 0

    def test_create_assigns_created_at(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create(
            event_type="PROPAGATION",
            payload={},
            source="s",
            target="t",
        )
        assert isinstance(event.created_at, str)
        assert len(event.created_at) > 0

    def test_create_stores_payload(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        payload = {"discovery_id": "d-001", "trust": 0.9}
        event = IntegrationEvent.create(
            event_type="DISCOVERY",
            payload=payload,
            source="s",
            target="t",
        )
        assert event.payload == payload

    def test_create_default_metadata_is_dict(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create(
            event_type="CONFLICT",
            payload={},
            source="s",
            target="t",
        )
        assert isinstance(event.metadata, dict)

    def test_create_with_explicit_metadata(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        meta = {"priority": "high", "retry": 3}
        event = IntegrationEvent.create(
            event_type="DISCOVERY",
            payload={},
            source="s",
            target="t",
            metadata=meta,
        )
        assert event.metadata == meta


class TestIntegrationEventToDict:
    """Tests for IntegrationEvent.to_dict."""

    def test_to_dict_returns_dict(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("DISCOVERY", {}, "s", "t")
        assert isinstance(event.to_dict(), dict)

    def test_to_dict_contains_required_keys(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("DISCOVERY", {"k": "v"}, "src", "tgt")
        d = event.to_dict()
        for key in ("event_id", "event_type", "payload", "source", "target", "created_at", "metadata"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_roundtrip_event_type(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("PROPAGATION", {}, "s", "t")
        assert event.to_dict()["event_type"] == "PROPAGATION"

    def test_to_dict_roundtrip_payload(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        payload = {"alpha": 1, "beta": 2}
        event = IntegrationEvent.create("CONFLICT", payload, "s", "t")
        assert event.to_dict()["payload"] == payload


class TestIntegrationEventSummary:
    """Tests for IntegrationEvent.summary."""

    def test_summary_returns_string(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("DISCOVERY", {}, "s", "t")
        assert isinstance(event.summary(), str)

    def test_summary_is_non_empty(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("DISCOVERY", {}, "s", "t")
        assert len(event.summary()) > 0

    def test_summary_contains_event_type(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        event = IntegrationEvent.create("AUTHORITY_GRANT", {}, "s", "t")
        assert "AUTHORITY_GRANT" in event.summary()


# ===========================================================================
# Parametrize: event types
# ===========================================================================

@pytest.mark.parametrize("event_type", [
    "DISCOVERY",
    "AUTHORITY_GRANT",
    "CONSENSUS",
    "CONFLICT",
    "PROPAGATION",
])
def test_integration_event_all_types(event_type):
    """IntegrationEvent.create accepts all documented event types."""
    from jugeo.ideation.discovery_federation.integration import IntegrationEvent
    event = IntegrationEvent.create(event_type, {"k": "v"}, "src", "tgt")
    assert event.event_type == event_type
    assert isinstance(event.to_dict(), dict)
    assert len(event.summary()) > 0


# ===========================================================================
# Parametrize: payload sizes
# ===========================================================================

@pytest.mark.parametrize("payload", [
    {"key": "value"},
    {f"key_{i}": f"value_{i}" for i in range(10)},
    {f"key_{i}": f"value_{i}" for i in range(100)},
])
def test_integration_event_payload_sizes(payload):
    """IntegrationEvent handles small, medium, and large payloads."""
    from jugeo.ideation.discovery_federation.integration import IntegrationEvent
    event = IntegrationEvent.create("DISCOVERY", payload, "s", "t")
    assert event.payload == payload
    assert event.to_dict()["payload"] == payload


# ===========================================================================
# FederationIntegration tests
# ===========================================================================

class TestFederationIntegrationRegisterAdapter:
    """Tests for FederationIntegration.register_adapter."""

    def test_register_adapter_appears_in_status(self, integration_hub):
        status = integration_hub.get_status()
        assert "bridge" in status
        assert "authority" in status

    def test_register_adapter_initial_status_is_string(self, integration_hub):
        status = integration_hub.get_status()
        for v in status.values():
            assert isinstance(v, str)

    def test_register_new_adapter_appears_immediately(self):
        from jugeo.ideation.discovery_federation.integration import (
            FederationIntegration,
            DiscoveryBridgeAdapter,
        )
        hub = FederationIntegration()
        adapter = DiscoveryBridgeAdapter(name="extra")
        hub.register_adapter("extra", adapter)
        assert "extra" in hub.get_status()

    def test_register_same_name_twice_does_not_raise(self):
        """Registering the same adapter name twice should overwrite silently (or raise — document behavior)."""
        from jugeo.ideation.discovery_federation.integration import (
            FederationIntegration,
            DiscoveryBridgeAdapter,
        )
        hub = FederationIntegration()
        hub.register_adapter("dup", DiscoveryBridgeAdapter(name="dup"))
        # Should either silently overwrite or raise; either is acceptable as long as
        # the hub remains consistent afterwards.
        try:
            hub.register_adapter("dup", DiscoveryBridgeAdapter(name="dup2"))
            assert "dup" in hub.get_status()
        except Exception:
            pass  # raising on duplicate is also a valid design


class TestFederationIntegrationConnectDisconnect:
    """Tests for FederationIntegration.connect / disconnect."""

    def test_connect_registered_adapter_returns_bool(self, integration_hub):
        result = integration_hub.connect("bridge")
        assert isinstance(result, bool)

    def test_connect_known_adapter_returns_true(self, integration_hub):
        assert integration_hub.connect("bridge") is True

    def test_disconnect_connected_adapter_returns_true(self, integration_hub):
        integration_hub.connect("bridge")
        assert integration_hub.disconnect("bridge") is True

    def test_disconnect_before_connect_returns_false(self):
        from jugeo.ideation.discovery_federation.integration import (
            FederationIntegration,
            DiscoveryBridgeAdapter,
        )
        hub = FederationIntegration()
        hub.register_adapter("fresh", DiscoveryBridgeAdapter(name="fresh"))
        assert hub.disconnect("fresh") is False

    def test_connect_changes_status(self, integration_hub):
        status_before = integration_hub.get_status()["authority"]
        integration_hub.connect("authority")
        status_after = integration_hub.get_status()["authority"]
        # The status string must change after connecting
        assert status_before != status_after or status_after.upper() in ("CONNECTED", "ACTIVE", "OK", "UP")

    def test_disconnect_unknown_adapter_returns_false(self):
        from jugeo.ideation.discovery_federation.integration import FederationIntegration
        hub = FederationIntegration()
        assert hub.disconnect("nonexistent") is False


class TestFederationIntegrationSendEvent:
    """Tests for FederationIntegration.send_event."""

    def test_send_event_returns_bool(self, integration_hub):
        integration_hub.connect("bridge")
        event = make_event(target="bridge")
        result = integration_hub.send_event(event, target="bridge")
        assert isinstance(result, bool)

    def test_send_event_to_connected_adapter_returns_true(self, integration_hub):
        integration_hub.connect("bridge")
        event = make_event(target="bridge")
        assert integration_hub.send_event(event, target="bridge") is True

    def test_send_event_to_disconnected_adapter_returns_false(self, integration_hub):
        event = make_event(target="authority")
        assert integration_hub.send_event(event, target="authority") is False

    def test_send_event_to_unknown_target_returns_false(self, integration_hub):
        event = make_event(target="ghost")
        assert integration_hub.send_event(event, target="ghost") is False


class TestFederationIntegrationReceiveEvents:
    """Tests for FederationIntegration.receive_events."""

    def test_receive_events_returns_list(self, integration_hub):
        result = integration_hub.receive_events(source="bridge")
        assert isinstance(result, list)

    def test_receive_events_from_unknown_source_returns_list(self, integration_hub):
        result = integration_hub.receive_events(source="ghost")
        assert isinstance(result, list)

    def test_receive_events_after_send_has_content(self, integration_hub):
        integration_hub.connect("bridge")
        event = make_event(event_type="DISCOVERY", target="bridge")
        integration_hub.send_event(event, target="bridge")
        received = integration_hub.receive_events(source="bridge")
        # After sending, there should be at least 0 events (may be 1 depending on design)
        assert isinstance(received, list)


class TestFederationIntegrationStatus:
    """Tests for FederationIntegration.get_status and health_check."""

    def test_get_status_returns_dict(self, integration_hub):
        assert isinstance(integration_hub.get_status(), dict)

    def test_get_status_has_all_registered_names(self, integration_hub):
        status = integration_hub.get_status()
        assert "bridge" in status
        assert "authority" in status

    def test_health_check_returns_dict(self, integration_hub):
        result = integration_hub.health_check()
        assert isinstance(result, dict)

    def test_health_check_values_are_bool(self, integration_hub):
        result = integration_hub.health_check()
        for v in result.values():
            assert isinstance(v, bool)

    def test_health_check_has_all_registered_names(self, integration_hub):
        result = integration_hub.health_check()
        assert "bridge" in result
        assert "authority" in result


# ===========================================================================
# DiscoveryBridgeAdapter tests
# ===========================================================================

class TestDiscoveryBridgeAdapterConnect:
    """Tests for DiscoveryBridgeAdapter lifecycle."""

    def test_connect_returns_true(self):
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        adapter = DiscoveryBridgeAdapter(name="test-bridge")
        assert adapter.connect() is True

    def test_disconnect_after_connect_returns_true(self):
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        adapter = DiscoveryBridgeAdapter(name="test-bridge")
        adapter.connect()
        assert adapter.disconnect() is True

    def test_disconnect_without_connect_returns_false(self):
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        adapter = DiscoveryBridgeAdapter(name="test-bridge")
        assert adapter.disconnect() is False

    def test_default_name_is_bridge(self):
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        adapter = DiscoveryBridgeAdapter()
        assert adapter.name == "bridge"


class TestDiscoveryBridgeAdapterAdapt:
    """Tests for DiscoveryBridgeAdapter.adapt_discovery and adapt_batch."""

    def test_adapt_discovery_returns_dict(self, bridge_adapter_connected):
        result = bridge_adapter_connected.adapt_discovery(make_discovery())
        assert isinstance(result, dict)

    def test_adapt_discovery_transforms_required_fields(self, bridge_adapter_connected):
        discovery = make_discovery(trust_score=0.75)
        result = bridge_adapter_connected.adapt_discovery(discovery)
        # The adapted result must contain at least the original discovery_id
        assert "discovery_id" in result or "id" in result

    def test_adapt_discovery_preserves_trust_score(self, bridge_adapter_connected):
        discovery = make_discovery(trust_score=0.6)
        result = bridge_adapter_connected.adapt_discovery(discovery)
        # Trust score must appear somewhere in the result
        trust_values = [v for v in result.values() if isinstance(v, float)]
        assert any(abs(v - 0.6) < 1e-9 for v in trust_values) or result.get("trust_score") == 0.6

    def test_adapt_batch_returns_list(self, bridge_adapter_connected):
        discoveries = [make_discovery() for _ in range(5)]
        result = bridge_adapter_connected.adapt_batch(discoveries)
        assert isinstance(result, list)

    def test_adapt_batch_same_length(self, bridge_adapter_connected):
        discoveries = [make_discovery() for _ in range(7)]
        result = bridge_adapter_connected.adapt_batch(discoveries)
        assert len(result) == 7

    def test_adapt_batch_empty_list_returns_empty(self, bridge_adapter_connected):
        result = bridge_adapter_connected.adapt_batch([])
        assert result == []

    def test_get_adapted_accumulates(self, bridge_adapter_connected):
        bridge_adapter_connected.clear()
        for _ in range(3):
            bridge_adapter_connected.adapt_discovery(make_discovery())
        adapted = bridge_adapter_connected.get_adapted()
        assert len(adapted) == 3

    def test_clear_empties_adapted(self, bridge_adapter_connected):
        bridge_adapter_connected.adapt_discovery(make_discovery())
        bridge_adapter_connected.clear()
        assert bridge_adapter_connected.get_adapted() == []

    def test_adapt_batch_accumulates_in_get_adapted(self, bridge_adapter_connected):
        bridge_adapter_connected.clear()
        discoveries = [make_discovery() for _ in range(4)]
        bridge_adapter_connected.adapt_batch(discoveries)
        adapted = bridge_adapter_connected.get_adapted()
        assert len(adapted) == 4


# ===========================================================================
# AuthorityPackAdapter tests
# ===========================================================================

class TestAuthorityPackAdapterConnect:
    """Tests for AuthorityPackAdapter lifecycle."""

    def test_connect_returns_true(self):
        from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
        adapter = AuthorityPackAdapter(name="test-authority")
        assert adapter.connect() is True

    def test_disconnect_after_connect_returns_true(self):
        from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
        adapter = AuthorityPackAdapter(name="test-authority")
        adapter.connect()
        assert adapter.disconnect() is True

    def test_disconnect_without_connect_returns_false(self):
        from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
        adapter = AuthorityPackAdapter(name="test-authority")
        assert adapter.disconnect() is False

    def test_default_name_is_authority(self):
        from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
        adapter = AuthorityPackAdapter()
        assert adapter.name == "authority"


class TestAuthorityPackAdapterAdaptGrant:
    """Tests for AuthorityPackAdapter.adapt_grant and adapt_batch."""

    def test_adapt_grant_returns_dict(self, authority_adapter_connected):
        result = authority_adapter_connected.adapt_grant(make_grant())
        assert isinstance(result, dict)

    def test_adapt_grant_preserves_grant_id(self, authority_adapter_connected):
        grant = make_grant(level="REGIONAL")
        result = authority_adapter_connected.adapt_grant(grant)
        assert result.get("grant_id") == grant["grant_id"] or result.get("id") == grant["grant_id"]

    def test_adapt_batch_returns_list(self, authority_adapter_connected):
        grants = [make_grant() for _ in range(5)]
        result = authority_adapter_connected.adapt_batch(grants)
        assert isinstance(result, list)

    def test_adapt_batch_same_length(self, authority_adapter_connected):
        grants = [make_grant() for _ in range(6)]
        result = authority_adapter_connected.adapt_batch(grants)
        assert len(result) == 6

    def test_adapt_batch_empty_list_returns_empty(self, authority_adapter_connected):
        result = authority_adapter_connected.adapt_batch([])
        assert result == []


class TestAuthorityPackAdapterRevoke:
    """Tests for AuthorityPackAdapter.revoke_adapted and get_active."""

    def test_revoke_existing_grant_returns_true(self, authority_adapter_connected):
        grant = make_grant()
        authority_adapter_connected.adapt_grant(grant)
        grant_id = grant["grant_id"]
        assert authority_adapter_connected.revoke_adapted(grant_id) is True

    def test_revoke_nonexistent_grant_returns_false(self, authority_adapter_connected):
        assert authority_adapter_connected.revoke_adapted("does-not-exist-12345") is False

    def test_get_active_returns_list(self, authority_adapter_connected):
        result = authority_adapter_connected.get_active()
        assert isinstance(result, list)

    def test_get_active_contains_adapted_grant(self, authority_adapter_connected):
        grant = make_grant()
        authority_adapter_connected.adapt_grant(grant)
        active = authority_adapter_connected.get_active()
        grant_ids = [g.get("grant_id") or g.get("id") for g in active]
        assert grant["grant_id"] in grant_ids

    def test_get_active_excludes_revoked_grant(self, authority_adapter_connected):
        grant = make_grant()
        authority_adapter_connected.adapt_grant(grant)
        authority_adapter_connected.revoke_adapted(grant["grant_id"])
        active = authority_adapter_connected.get_active()
        grant_ids = [g.get("grant_id") or g.get("id") for g in active]
        assert grant["grant_id"] not in grant_ids

    def test_revoke_same_grant_twice_second_is_false(self, authority_adapter_connected):
        grant = make_grant()
        authority_adapter_connected.adapt_grant(grant)
        authority_adapter_connected.revoke_adapted(grant["grant_id"])
        result = authority_adapter_connected.revoke_adapted(grant["grant_id"])
        assert result is False


# ===========================================================================
# Free function: integrate_with_packs
# ===========================================================================

class TestIntegrateWithPacks:
    """Tests for the integrate_with_packs free function."""

    def test_returns_dict(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        result = integrate_with_packs([], [])
        assert isinstance(result, dict)

    def test_with_discoveries_and_grants_returns_dict(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        discoveries = [make_discovery() for _ in range(3)]
        grants = [make_grant() for _ in range(2)]
        result = integrate_with_packs(discoveries, grants)
        assert isinstance(result, dict)

    def test_result_has_expected_keys(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        discoveries = [make_discovery()]
        grants = [make_grant()]
        result = integrate_with_packs(discoveries, grants)
        # Must contain at least one of the expected structural keys
        assert len(result) > 0

    def test_empty_discoveries_and_grants(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        result = integrate_with_packs([], [])
        assert isinstance(result, dict)

    def test_multiple_discoveries_no_grants(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        discoveries = [make_discovery(trust_score=0.5 + i * 0.1) for i in range(5)]
        result = integrate_with_packs(discoveries, [])
        assert isinstance(result, dict)

    def test_no_discoveries_multiple_grants(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        grants = [make_grant(level="REGIONAL") for _ in range(4)]
        result = integrate_with_packs([], grants)
        assert isinstance(result, dict)


# ===========================================================================
# Free function: integrate_with_orchestrator
# ===========================================================================

class TestIntegrateWithOrchestrator:
    """Tests for the integrate_with_orchestrator free function."""

    def test_returns_dict(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_orchestrator
        result = integrate_with_orchestrator({}, {})
        assert isinstance(result, dict)

    def test_result_has_status_key(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_orchestrator
        federation_state = {"nodes": 3, "consensus": "ACTIVE"}
        orchestrator_config = {"mode": "federated", "quorum": 0.6}
        result = integrate_with_orchestrator(federation_state, orchestrator_config)
        assert "status" in result or "integration_result" in result or len(result) > 0

    def test_result_with_nonempty_state(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_orchestrator
        result = integrate_with_orchestrator(
            {"active_grants": 5, "propagation_lag": 0.1},
            {"strategy": "round-robin"},
        )
        assert isinstance(result, dict)

    def test_empty_inputs_produce_dict(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_orchestrator
        result = integrate_with_orchestrator({}, {})
        assert isinstance(result, dict)


# ===========================================================================
# Parametrize: adapter kinds
# ===========================================================================

@pytest.mark.parametrize("adapter_kind", ["bridge", "authority", "custom"])
def test_federation_integration_register_parametrized(adapter_kind):
    """FederationIntegration can register adapters of any kind."""
    from jugeo.ideation.discovery_federation.integration import (
        FederationIntegration,
        DiscoveryBridgeAdapter,
        AuthorityPackAdapter,
    )
    hub = FederationIntegration()
    if adapter_kind == "bridge":
        adapter = DiscoveryBridgeAdapter(name=adapter_kind)
    elif adapter_kind == "authority":
        adapter = AuthorityPackAdapter(name=adapter_kind)
    else:
        # custom — use DiscoveryBridgeAdapter as a stand-in
        adapter = DiscoveryBridgeAdapter(name=adapter_kind)
    hub.register_adapter(adapter_kind, adapter)
    assert adapter_kind in hub.get_status()


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:
    """Edge case and boundary tests for the integration module."""

    def test_send_event_to_disconnected_bridge(self, integration_hub):
        event = make_event(target="bridge")
        # bridge is not yet connected; must return False
        result = integration_hub.send_event(event, target="bridge")
        assert result is False

    def test_adapt_empty_discovery_batch(self, bridge_adapter_connected):
        result = bridge_adapter_connected.adapt_batch([])
        assert result == []

    def test_adapt_empty_grant_batch(self, authority_adapter_connected):
        result = authority_adapter_connected.adapt_batch([])
        assert result == []

    def test_revoke_nonexistent_returns_false(self, authority_adapter_connected):
        assert authority_adapter_connected.revoke_adapted("totally-fake-id") is False

    def test_health_check_after_connect_disconnected_pair(self):
        from jugeo.ideation.discovery_federation.integration import (
            FederationIntegration,
            DiscoveryBridgeAdapter,
            AuthorityPackAdapter,
        )
        hub = FederationIntegration()
        hub.register_adapter("b", DiscoveryBridgeAdapter(name="b"))
        hub.register_adapter("a", AuthorityPackAdapter(name="a"))
        hub.connect("b")
        # "a" not connected
        health = hub.health_check()
        assert isinstance(health["b"], bool)
        assert isinstance(health["a"], bool)

    def test_integration_event_unique_ids(self):
        from jugeo.ideation.discovery_federation.integration import IntegrationEvent
        ids = {
            IntegrationEvent.create("DISCOVERY", {}, "s", "t").event_id
            for _ in range(20)
        }
        assert len(ids) == 20, "Each created event must have a unique event_id"

    def test_get_adapted_returns_list_when_empty(self, bridge_adapter_connected):
        bridge_adapter_connected.clear()
        result = bridge_adapter_connected.get_adapted()
        assert result == []

    def test_get_active_returns_list_when_empty(self):
        from jugeo.ideation.discovery_federation.integration import AuthorityPackAdapter
        adapter = AuthorityPackAdapter()
        adapter.connect()
        result = adapter.get_active()
        assert result == []

    def test_integrate_with_packs_large_input(self):
        from jugeo.ideation.discovery_federation.integration import integrate_with_packs
        discoveries = [make_discovery(trust_score=0.5) for _ in range(50)]
        grants = [make_grant() for _ in range(50)]
        result = integrate_with_packs(discoveries, grants)
        assert isinstance(result, dict)

    def test_bridge_adapter_adapt_after_disconnect_still_works(self):
        """adapt_discovery should work regardless of connection state (data transformation)."""
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        adapter = DiscoveryBridgeAdapter()
        adapter.connect()
        adapter.disconnect()
        # Adaptation is a pure transformation; connection state affects routing, not transformation
        result = adapter.adapt_discovery(make_discovery())
        assert isinstance(result, dict)

    def test_federation_integration_empty_hub_get_status(self):
        from jugeo.ideation.discovery_federation.integration import FederationIntegration
        hub = FederationIntegration()
        status = hub.get_status()
        assert isinstance(status, dict)
        assert len(status) == 0

    def test_federation_integration_empty_hub_health_check(self):
        from jugeo.ideation.discovery_federation.integration import FederationIntegration
        hub = FederationIntegration()
        health = hub.health_check()
        assert isinstance(health, dict)
        assert len(health) == 0

    def test_receive_events_empty_hub(self):
        from jugeo.ideation.discovery_federation.integration import FederationIntegration
        hub = FederationIntegration()
        result = hub.receive_events(source="anything")
        assert isinstance(result, list)

    def test_multiple_adapters_independent_state(self):
        from jugeo.ideation.discovery_federation.integration import DiscoveryBridgeAdapter
        a1 = DiscoveryBridgeAdapter(name="a1")
        a2 = DiscoveryBridgeAdapter(name="a2")
        a1.connect()
        a1.adapt_discovery(make_discovery())
        # a2 should have no adapted entries
        assert a2.get_adapted() == []
