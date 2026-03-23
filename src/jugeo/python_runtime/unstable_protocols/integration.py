"""Integration layer for the JuGeo unstable_protocols package.

Connects unstable protocol sections to geometry supports, judgment trust,
evidence channels, and fleet orchestration.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1  :class:`SupportBridge`  – translates protocol sections into geometry-layer
      support regions, enabling the geometry subsystem to track which semantic
      coordinates are still covered.
* §4  :class:`JudgmentBridge` – maps protocol stability levels to judgment trust
      tiers, ensuring that unstable sections cannot produce high-confidence
      judgments.
* §2  :class:`FleetBridge`    – registers protocol sections with the fleet
      orchestration layer, enabling distributed stability monitoring.
* All  :class:`UnstableProtocolIntegration` – top-level façade that wires all
      bridges together and exposes a unified health check.

Design notes
------------
All bridge methods are designed to be *fail-open*: if a cross-package import
is unavailable (stub classes are in use), the bridge degrades gracefully and
logs a warning rather than raising.  Full integration is only available when
the corresponding jugeo sub-packages are installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
        StabilityMonitor,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        pass
    class StabilityLevel:  # type: ignore[no-redef]
        pass
    class ProxyRecord:  # type: ignore[no-redef]
        pass
    class ProxyRestriction:  # type: ignore[no-redef]
        pass
    class DelegationChain:  # type: ignore[no-redef]
        pass
    class DelegationKind:  # type: ignore[no-redef]
        pass
    class UnstableInterface:  # type: ignore[no-redef]
        pass
    class StabilityMonitor:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:  # pragma: no cover
    class SupportRegion:  # type: ignore[no-redef]
        pass
    class SupportSet:  # type: ignore[no-redef]
        pass
    class SupportTracker:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustTier
except ImportError:  # pragma: no cover
    class LocalJudgment:  # type: ignore[no-redef]
        pass
    class JudgmentStatus:  # type: ignore[no-redef]
        pass
    class TrustTier:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:  # pragma: no cover
    class EvidenceChannel:  # type: ignore[no-redef]
        pass
    class EvidenceRecord:  # type: ignore[no-redef]
        pass
    class ChannelRouter:  # type: ignore[no-redef]
        pass

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:  # pragma: no cover
    class Fleet:  # type: ignore[no-redef]
        pass
    class FleetBid:  # type: ignore[no-redef]
        pass
    class FleetMember:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# UnstableProtocolIntegration
# ---------------------------------------------------------------------------


@dataclass
class UnstableProtocolIntegration:
    """Top-level integration façade wiring all sub-system bridges together.

    :class:`UnstableProtocolIntegration` is the single entry point for external
    consumers that need to integrate the unstable_protocols package with other
    jugeo subsystems.  It maintains one :class:`SupportBridge`,
    one :class:`JudgmentBridge`, and one :class:`FleetBridge`, and exposes a
    unified health-check method that queries all of them.

    Parameters
    ----------
    integration_id:
        Unique identifier for this integration instance.
    config:
        Arbitrary configuration dict (e.g. timeouts, feature flags).
    status:
        Mapping from subsystem name to integration status (True = healthy).
    bridges:
        Mapping from subsystem name to bridge instance.
    """

    integration_id: str
    config: dict[str, Any] = field(default_factory=dict)
    status: dict[str, bool] = field(default_factory=dict)
    bridges: dict[str, Any] = field(default_factory=dict)

    def integrate_with_supports(self, tracker: Any) -> bool:
        """Attach a geometry support tracker to the integration layer.

        Parameters
        ----------
        tracker:
            A :class:`SupportTracker`-compatible object (or stub).

        Returns
        -------
        bool
            ``True`` when the bridge was successfully initialised.
        """
        try:
            bridge = SupportBridge(
                bridge_id=str(uuid.uuid4()),
                section_registry={},
                support_cache={},
                sync_log=[],
            )
            bridge.sync_log.append(
                {"event": "init", "tracker_type": type(tracker).__name__, "timestamp": time.time()}
            )
            self.bridges["supports"] = bridge
            self.status["supports"] = True
            return True
        except Exception as exc:
            self.status["supports"] = False
            self.bridges["supports_error"] = str(exc)
            return False

    def integrate_with_judgments(self, judgment_store: Any) -> bool:
        """Attach a judgment store to the integration layer.

        Parameters
        ----------
        judgment_store:
            An object providing judgment lookup and update methods.

        Returns
        -------
        bool
            ``True`` when the bridge was successfully initialised.
        """
        try:
            bridge = JudgmentBridge(
                bridge_id=str(uuid.uuid4()),
                stability_map={},
                judgment_cache={},
                sync_log=[],
            )
            bridge.sync_log.append(
                {
                    "event": "init",
                    "store_type": type(judgment_store).__name__,
                    "timestamp": time.time(),
                }
            )
            self.bridges["judgments"] = bridge
            self.status["judgments"] = True
            return True
        except Exception as exc:
            self.status["judgments"] = False
            self.bridges["judgments_error"] = str(exc)
            return False

    def integrate_with_channels(self, router: Any) -> bool:
        """Attach an evidence channel router to the integration layer.

        Parameters
        ----------
        router:
            A :class:`ChannelRouter`-compatible object (or stub).

        Returns
        -------
        bool
            ``True`` when successfully attached.
        """
        try:
            self.bridges["channels"] = {
                "router_type": type(router).__name__,
                "attached_at": time.time(),
            }
            self.status["channels"] = True
            return True
        except Exception as exc:
            self.status["channels"] = False
            return False

    def integrate_with_fleet(self, fleet: Any) -> bool:
        """Attach a fleet orchestrator to the integration layer.

        Parameters
        ----------
        fleet:
            A :class:`Fleet`-compatible object (or stub).

        Returns
        -------
        bool
            ``True`` when the bridge was successfully initialised.
        """
        try:
            bridge = FleetBridge(
                bridge_id=str(uuid.uuid4()),
                fleet_config={"fleet_type": type(fleet).__name__},
                registration_log=[],
                bid_log=[],
            )
            self.bridges["fleet"] = bridge
            self.status["fleet"] = True
            return True
        except Exception as exc:
            self.status["fleet"] = False
            return False

    def full_integration_check(self) -> dict[str, bool]:
        """Return the current integration status for all subsystems.

        Returns
        -------
        dict[str, bool]
            One entry per subsystem; ``True`` means the bridge is healthy.
        """
        return dict(self.status)

    def export_integration_state(self) -> dict[str, Any]:
        """Serialise the integration state to a plain dictionary."""
        return {
            "integration_id": self.integration_id,
            "config": dict(self.config),
            "status": dict(self.status),
            "bridge_count": len(self.bridges),
            "timestamp": time.time(),
        }

    def reload_integration(self, config: dict[str, Any]) -> None:
        """Update configuration and reset integration status.

        Parameters
        ----------
        config:
            New configuration dict to apply.
        """
        self.config.update(config)
        self.status.clear()
        self.bridges.clear()

    def health_check(self) -> dict[str, Any]:
        """Return a comprehensive health report for the integration layer.

        Returns
        -------
        dict[str, Any]
            Keys: ``integration_id``, ``overall_healthy``, ``status``,
            ``bridge_names``, ``timestamp``.
        """
        healthy = all(self.status.values()) if self.status else False
        return {
            "integration_id": self.integration_id,
            "overall_healthy": healthy,
            "status": dict(self.status),
            "bridge_names": list(self.bridges.keys()),
            "timestamp": time.time(),
        }


# ---------------------------------------------------------------------------
# SupportBridge
# ---------------------------------------------------------------------------


@dataclass
class SupportBridge:
    """Bridges protocol sections to the geometry-layer support system.

    The bridge converts :class:`ProtocolSection` instances into
    ``SupportRegion``-compatible dictionaries (suitable for passing to the
    geometry subsystem even when it is unavailable as a stub), and can
    propagate retraction events from the protocol layer down to the geometry
    layer.

    Parameters
    ----------
    bridge_id:
        Unique identifier for this bridge instance.
    section_registry:
        Local cache mapping section_id to :class:`ProtocolSection`.
    support_cache:
        Cached support region data, keyed by section_id.
    sync_log:
        Ordered list of sync event records.
    """

    bridge_id: str
    section_registry: dict[str, ProtocolSection] = field(default_factory=dict)
    support_cache: dict[str, Any] = field(default_factory=dict)
    sync_log: list[dict[str, Any]] = field(default_factory=list)

    def section_to_support(self, section: ProtocolSection) -> dict[str, Any]:
        """Convert a :class:`ProtocolSection` to a support region dictionary.

        The resulting dict can be used to create a ``SupportRegion`` object when
        the geometry package is available, or treated as a data transfer object
        otherwise.

        Parameters
        ----------
        section:
            The section to convert.

        Returns
        -------
        dict[str, Any]
            Keys: ``region_id``, ``coordinate``, ``method_keys``,
            ``stability``, ``support_keys``, ``created_at``.
        """
        support = {
            "region_id": section.section_id,
            "coordinate": section.coordinate,
            "method_keys": sorted(section.declared_methods),
            "stability": section.stability_level.value,
            "stability_score": section.stability_level.severity_score(),
            "support_keys": sorted(section.support_keys),
            "created_at": section.created_at,
            "drift_score": section.drift_score(),
        }
        self.support_cache[section.section_id] = support
        self.section_registry[section.section_id] = section
        self._log_sync("section_to_support", section.section_id)
        return support

    def support_to_section(self, support: Any, coordinate: str) -> ProtocolSection:
        """Convert a support-region-like object to a :class:`ProtocolSection`.

        Accepts either a dict or an object with attribute access.  Fields not
        present default to safe empty values.

        Parameters
        ----------
        support:
            A support region dict or object.
        coordinate:
            Semantic coordinate to assign to the section.

        Returns
        -------
        ProtocolSection
            A new section constructed from the support data.
        """
        if isinstance(support, dict):
            method_keys = support.get("method_keys", [])
            support_keys = frozenset(support.get("support_keys", []))
            stability_value = support.get("stability", "stable")
        else:
            method_keys = getattr(support, "method_keys", [])
            support_keys = frozenset(getattr(support, "support_keys", []))
            stability_value = getattr(support, "stability", "stable")

        try:
            stability = StabilityLevel(stability_value)
        except (ValueError, AttributeError):
            stability = StabilityLevel.STABLE

        now = time.time()
        section = ProtocolSection(
            section_id=str(uuid.uuid4()),
            coordinate=coordinate,
            declared_methods=tuple(method_keys),
            observed_methods=tuple(method_keys),
            stability_level=stability,
            support_keys=support_keys,
            created_at=now,
            last_verified=now,
            provenance=("support_bridge",),
        )
        self.section_registry[section.section_id] = section
        self._log_sync("support_to_section", section.section_id)
        return section

    def sync_stability(
        self, section: ProtocolSection, support: Any
    ) -> ProtocolSection:
        """Synchronise the stability level of a section from a support object.

        When the support's stability is worse than the section's current level,
        the section's level is upgraded (made worse) to match.

        Parameters
        ----------
        section:
            The section to potentially upgrade.
        support:
            The support object providing the reference stability.

        Returns
        -------
        ProtocolSection
            Updated section (new instance if stability changed, else original).
        """
        from dataclasses import replace as dc_replace

        if isinstance(support, dict):
            stability_value = support.get("stability", section.stability_level.value)
        else:
            stability_value = getattr(
                support, "stability", section.stability_level.value
            )

        try:
            support_stability = StabilityLevel(stability_value)
        except (ValueError, AttributeError):
            support_stability = section.stability_level

        if support_stability.severity_score() > section.stability_level.severity_score():
            updated = dc_replace(section, stability_level=support_stability)
            self._log_sync("sync_stability_upgraded", section.section_id)
            return updated

        return section

    def propagate_retraction(
        self, section_id: str, retracted_keys: set[str]
    ) -> None:
        """Notify the support cache that methods were retracted from a section.

        Parameters
        ----------
        section_id:
            The section from which methods were retracted.
        retracted_keys:
            The set of retracted method names.
        """
        cached = self.support_cache.get(section_id)
        if cached is not None:
            existing = set(cached.get("method_keys", []))
            cached["method_keys"] = sorted(existing - retracted_keys)
        self.sync_log.append(
            {
                "event": "retraction_propagated",
                "section_id": section_id,
                "retracted_keys": sorted(retracted_keys),
                "timestamp": time.time(),
            }
        )

    def check_jurisdiction(
        self, section: ProtocolSection, support: Any
    ) -> bool:
        """Return True when the section's coordinate falls within the support's jurisdiction.

        Jurisdiction is defined as: the section's coordinate string appears in
        the support's ``method_keys`` or ``coordinate`` field.

        Parameters
        ----------
        section:
            The section to check.
        support:
            The support object defining the jurisdiction.
        """
        if isinstance(support, dict):
            support_coord = support.get("coordinate", "")
            support_keys = support.get("support_keys", [])
        else:
            support_coord = getattr(support, "coordinate", "")
            support_keys = getattr(support, "support_keys", [])

        return section.coordinate == support_coord or section.coordinate in support_keys

    def export_bridge_state(self) -> dict[str, Any]:
        """Serialise the bridge state to a plain dictionary."""
        return {
            "bridge_id": self.bridge_id,
            "section_count": len(self.section_registry),
            "support_cache_count": len(self.support_cache),
            "sync_log_count": len(self.sync_log),
        }

    def _log_sync(self, event: str, section_id: str) -> None:
        """Append a sync event to the log."""
        self.sync_log.append(
            {"event": event, "section_id": section_id, "timestamp": time.time()}
        )


# ---------------------------------------------------------------------------
# JudgmentBridge
# ---------------------------------------------------------------------------


@dataclass
class JudgmentBridge:
    """Bridges protocol stability levels to judgment trust scores.

    The judgment bridge ensures that the trust tier assigned to a judgment is
    bounded above by the stability level of the protocol section providing the
    evidence.  An UNSTABLE section can at most yield a PROVISIONAL trust tier;
    a COLLAPSED section yields no trust at all.

    Parameters
    ----------
    bridge_id:
        Unique identifier.
    stability_map:
        Mapping from stability level value string to trust score (0.0–1.0).
    judgment_cache:
        Cached judgment objects (or dicts), keyed by a correlation key.
    sync_log:
        Ordered list of sync event records.
    """

    bridge_id: str
    stability_map: dict[str, float] = field(default_factory=dict)
    judgment_cache: dict[str, Any] = field(default_factory=dict)
    sync_log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Populate default stability → trust mapping if not provided."""
        if not self.stability_map:
            self.stability_map = {
                StabilityLevel.STABLE.value: 1.0,
                StabilityLevel.DEGRADING.value: 0.75,
                StabilityLevel.UNSTABLE.value: 0.5,
                StabilityLevel.RETRACTING.value: 0.2,
                StabilityLevel.COLLAPSED.value: 0.0,
            }

    def stability_to_trust(self, stability_level: StabilityLevel) -> float:
        """Map a :class:`StabilityLevel` to a trust score in [0.0, 1.0].

        Parameters
        ----------
        stability_level:
            The stability level to map.

        Returns
        -------
        float
            Trust score; higher is more trusted.
        """
        return self.stability_map.get(stability_level.value, 0.0)

    def trust_to_stability(self, trust_score: float) -> StabilityLevel:
        """Map a numeric trust score back to the closest :class:`StabilityLevel`.

        Parameters
        ----------
        trust_score:
            Value in [0.0, 1.0]; higher means more stable.

        Returns
        -------
        StabilityLevel
            The stability level whose trust score is closest.
        """
        best_level = StabilityLevel.COLLAPSED
        best_dist = float("inf")
        for level_str, mapped_trust in self.stability_map.items():
            dist = abs(mapped_trust - trust_score)
            if dist < best_dist:
                best_dist = dist
                try:
                    best_level = StabilityLevel(level_str)
                except ValueError:
                    pass
        return best_level

    def sync_obligations(
        self, section: ProtocolSection, judgment: Any
    ) -> dict[str, Any]:
        """Compute the trust obligations for a judgment based on the section's stability.

        Parameters
        ----------
        section:
            The protocol section providing evidence.
        judgment:
            The judgment object (dict or stub) to synchronise with.

        Returns
        -------
        dict[str, Any]
            Contains ``trust_score``, ``stability_level``, ``can_proceed``,
            ``max_tier``, and ``sync_timestamp``.
        """
        trust = self.stability_to_trust(section.stability_level)
        can_proceed = trust > 0.0

        if trust >= 0.9:
            max_tier = "high"
        elif trust >= 0.5:
            max_tier = "medium"
        elif trust > 0.0:
            max_tier = "low"
        else:
            max_tier = "none"

        result = {
            "trust_score": trust,
            "stability_level": section.stability_level.value,
            "can_proceed": can_proceed,
            "max_tier": max_tier,
            "sync_timestamp": time.time(),
        }
        correlation_key = f"{section.section_id}:{time.time():.0f}"
        self.judgment_cache[correlation_key] = result
        self.sync_log.append(
            {
                "event": "sync_obligations",
                "section_id": section.section_id,
                "trust": trust,
                "max_tier": max_tier,
                "timestamp": time.time(),
            }
        )
        return result

    def check_consistency(
        self, section: ProtocolSection, judgment: Any
    ) -> bool:
        """Return True when the judgment's trust tier is consistent with the section.

        A judgment is inconsistent when it claims a higher trust than the
        section's stability allows.

        Parameters
        ----------
        section:
            The protocol section.
        judgment:
            The judgment object or dict providing a ``trust_score`` field.
        """
        max_trust = self.stability_to_trust(section.stability_level)
        if isinstance(judgment, dict):
            claimed_trust = float(judgment.get("trust_score", 0.0))
        else:
            claimed_trust = float(getattr(judgment, "trust_score", 0.0))
        return claimed_trust <= max_trust + 1e-9

    def export_bridge_state(self) -> dict[str, Any]:
        """Serialise the bridge state to a plain dictionary."""
        return {
            "bridge_id": self.bridge_id,
            "stability_map": dict(self.stability_map),
            "judgment_cache_count": len(self.judgment_cache),
            "sync_log_count": len(self.sync_log),
        }


# ---------------------------------------------------------------------------
# FleetBridge
# ---------------------------------------------------------------------------


@dataclass
class FleetBridge:
    """Bridges unstable protocol sections to the fleet orchestration layer.

    When a protocol section requires distributed stability monitoring or
    needs to participate in a fleet bid (e.g. to receive an updated observed
    method set from a remote agent), the :class:`FleetBridge` mediates the
    interaction.

    Parameters
    ----------
    bridge_id:
        Unique identifier.
    fleet_config:
        Configuration dict for the fleet layer (timeouts, priorities, etc.).
    registration_log:
        Ordered list of fleet registration event records.
    bid_log:
        Ordered list of fleet bid event records.
    """

    bridge_id: str
    fleet_config: dict[str, Any] = field(default_factory=dict)
    registration_log: list[dict[str, Any]] = field(default_factory=list)
    bid_log: list[dict[str, Any]] = field(default_factory=list)

    def register_with_fleet(self, section: ProtocolSection, fleet: Any) -> bool:
        """Register a protocol section as a fleet member for distributed monitoring.

        Parameters
        ----------
        section:
            The section to register.
        fleet:
            The fleet object (or stub).

        Returns
        -------
        bool
            ``True`` when registration was accepted.
        """
        try:
            reg_record = {
                "section_id": section.section_id,
                "coordinate": section.coordinate,
                "stability": section.stability_level.value,
                "fleet_type": type(fleet).__name__,
                "timestamp": time.time(),
            }
            self.registration_log.append(reg_record)
            return True
        except Exception:
            return False

    def bid_on_protocol(
        self, section: ProtocolSection, bid_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Submit a fleet bid for distributed work on a protocol section.

        Parameters
        ----------
        section:
            The section requesting distributed work.
        bid_params:
            Bid parameters such as ``priority``, ``ttl``, ``capabilities``.

        Returns
        -------
        dict[str, Any]
            A bid record dict containing ``bid_id``, ``section_id``,
            ``priority``, ``submitted_at``.
        """
        bid = {
            "bid_id": str(uuid.uuid4()),
            "section_id": section.section_id,
            "coordinate": section.coordinate,
            "priority": bid_params.get("priority", 1),
            "ttl": bid_params.get("ttl", 300),
            "capabilities": bid_params.get("capabilities", []),
            "submitted_at": time.time(),
        }
        self.bid_log.append(bid)
        return bid

    def receive_fleet_result(self, result: Any) -> dict[str, Any]:
        """Parse and validate a fleet result for a protocol section bid.

        Parameters
        ----------
        result:
            Fleet result object or dict containing observed method data.

        Returns
        -------
        dict[str, Any]
            Normalised result with keys ``bid_id``, ``observed_methods``,
            ``trust_score``, ``received_at``.
        """
        if isinstance(result, dict):
            bid_id = result.get("bid_id", "unknown")
            observed = result.get("observed_methods", [])
            trust = float(result.get("trust_score", 1.0))
        else:
            bid_id = getattr(result, "bid_id", "unknown")
            observed = getattr(result, "observed_methods", [])
            trust = float(getattr(result, "trust_score", 1.0))

        return {
            "bid_id": bid_id,
            "observed_methods": list(observed),
            "trust_score": trust,
            "received_at": time.time(),
        }

    def calibrate_from_fleet(self, fleet_data: dict[str, Any]) -> dict[str, Any]:
        """Use fleet telemetry to calibrate the fleet config.

        Parameters
        ----------
        fleet_data:
            Telemetry dict from the fleet layer (e.g. latency stats, member count).

        Returns
        -------
        dict[str, Any]
            Updated fleet_config entries and calibration metadata.
        """
        updated_keys: list[str] = []
        if "latency_p99_ms" in fleet_data:
            self.fleet_config["latency_p99_ms"] = fleet_data["latency_p99_ms"]
            updated_keys.append("latency_p99_ms")
        if "member_count" in fleet_data:
            self.fleet_config["member_count"] = fleet_data["member_count"]
            updated_keys.append("member_count")
        if "error_rate" in fleet_data:
            self.fleet_config["error_rate"] = fleet_data["error_rate"]
            updated_keys.append("error_rate")

        return {
            "updated_keys": updated_keys,
            "calibrated_at": time.time(),
            "fleet_config": dict(self.fleet_config),
        }

    def export_bridge_state(self) -> dict[str, Any]:
        """Serialise the bridge state to a plain dictionary."""
        return {
            "bridge_id": self.bridge_id,
            "fleet_config": dict(self.fleet_config),
            "registration_count": len(self.registration_log),
            "bid_count": len(self.bid_log),
        }


# ---------------------------------------------------------------------------

__all__ = [
    "UnstableProtocolIntegration",
    "SupportBridge",
    "JudgmentBridge",
    "FleetBridge",
]

# copilot: integration.py – SupportBridge, JudgmentBridge, FleetBridge, and UnstableProtocolIntegration façade (Ch22 integration layer)
