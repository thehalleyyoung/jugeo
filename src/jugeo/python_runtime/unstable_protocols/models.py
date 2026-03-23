"""Core data models for JuGeo unstable_protocols (Ch22).

This module defines the fundamental data structures used throughout the
unstable_protocols package.  Every type is a frozen or semi-mutable dataclass
with full serialisation/deserialisation support, strict type annotations,
and complete docstrings.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1  :class:`ProtocolSection`    – behavioral sections over semantic coordinates
* §2  :class:`ProxyRecord`        – transport-restricted section proxies
* §2  :class:`DelegationChain`    – chains of delegation morphisms
* §3  :class:`UnstableInterface`  – boundaries where support is retracting
* §4  :class:`StabilityMonitor`   – drift detector for declared vs observed behavior

All frozen dataclasses that need to expose "mutation" operations use
``dataclasses.replace()`` and return new instances rather than modifying in
place, preserving the immutability guarantees required for hashing and caching.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class StabilityLevel(Enum):
    """Discrete stability levels for a protocol section.

    Levels are ordered from most to least healthy.  The numeric ``severity_score``
    mirrors the position in the ordering, making it easy to compare sections
    programmatically.
    """

    STABLE = "stable"
    DEGRADING = "degrading"
    UNSTABLE = "unstable"
    RETRACTING = "retracting"
    COLLAPSED = "collapsed"

    # ordering for comparison
    _ORDER: dict[str, int]  # populated after class body

    def is_healthy(self) -> bool:
        """Return True when the level is STABLE or DEGRADING."""
        return self in (StabilityLevel.STABLE, StabilityLevel.DEGRADING)

    def severity_score(self) -> float:
        """Return a numeric severity in [0.0, 1.0]; higher means worse.

        The mapping is:
        ``STABLE→0.0, DEGRADING→0.25, UNSTABLE→0.5, RETRACTING→0.75, COLLAPSED→1.0``
        """
        _map = {
            StabilityLevel.STABLE: 0.0,
            StabilityLevel.DEGRADING: 0.25,
            StabilityLevel.UNSTABLE: 0.5,
            StabilityLevel.RETRACTING: 0.75,
            StabilityLevel.COLLAPSED: 1.0,
        }
        return _map[self]

    def next_level(self) -> StabilityLevel:
        """Return the next (worse) stability level, or self if already COLLAPSED."""
        sequence = [
            StabilityLevel.STABLE,
            StabilityLevel.DEGRADING,
            StabilityLevel.UNSTABLE,
            StabilityLevel.RETRACTING,
            StabilityLevel.COLLAPSED,
        ]
        idx = sequence.index(self)
        return sequence[min(idx + 1, len(sequence) - 1)]

    @classmethod
    def from_score(cls, score: float) -> StabilityLevel:
        """Return the stability level that best matches a numeric score in [0,1].

        Parameters
        ----------
        score:
            Float in [0.0, 1.0]; higher means less stable.
        """
        if score < 0.15:
            return cls.STABLE
        if score < 0.35:
            return cls.DEGRADING
        if score < 0.60:
            return cls.UNSTABLE
        if score < 0.85:
            return cls.RETRACTING
        return cls.COLLAPSED


class ProxyRestriction(Enum):
    """Transport restriction level applied to a proxy record.

    Restrictions are listed from least to most permissive in terms of what
    the proxy *blocks*.  A ``BLOCKED`` proxy denies all access.
    """

    NONE = "none"
    READ_ONLY = "read_only"
    TRANSPORT_ONLY = "transport_only"
    OPAQUE = "opaque"
    BLOCKED = "blocked"

    def allows_write(self) -> bool:
        """Return True only when the restriction is NONE."""
        return self == ProxyRestriction.NONE

    def allows_read(self) -> bool:
        """Return True unless the proxy is OPAQUE or BLOCKED."""
        return self not in (ProxyRestriction.OPAQUE, ProxyRestriction.BLOCKED)

    def severity(self) -> float:
        """Return a numeric severity in [0.0, 1.0]; higher means more restrictive.

        ``NONE→0.0, READ_ONLY→0.25, TRANSPORT_ONLY→0.5, OPAQUE→0.75, BLOCKED→1.0``
        """
        _map = {
            ProxyRestriction.NONE: 0.0,
            ProxyRestriction.READ_ONLY: 0.25,
            ProxyRestriction.TRANSPORT_ONLY: 0.5,
            ProxyRestriction.OPAQUE: 0.75,
            ProxyRestriction.BLOCKED: 1.0,
        }
        return _map[self]

    def is_restrictive(self) -> bool:
        """Return True when any restriction is in effect (severity > 0)."""
        return self != ProxyRestriction.NONE


class DelegationKind(Enum):
    """Kind of delegation morphism connecting two protocol sections.

    * DIRECT  – a point-to-point delegation with full trust transfer.
    * PROXY   – delegation through an intermediary proxy record.
    * CHAIN   – multi-hop delegation through a sequence of sections.
    * SPLIT   – one source fans out to multiple targets.
    * MERGE   – multiple sources fan in to one target.
    """

    DIRECT = "direct"
    PROXY = "proxy"
    CHAIN = "chain"
    SPLIT = "split"
    MERGE = "merge"

    def is_transitive(self) -> bool:
        """Return True for delegation kinds that may span multiple hops."""
        return self in (DelegationKind.CHAIN, DelegationKind.MERGE)

    def max_trust_reduction(self) -> float:
        """Return the maximum trust reduction allowed for this kind.

        * DIRECT   → 0.0  (no reduction)
        * PROXY    → 0.1
        * CHAIN    → 0.3 per hop
        * SPLIT    → 0.2
        * MERGE    → 0.15
        """
        _map = {
            DelegationKind.DIRECT: 0.0,
            DelegationKind.PROXY: 0.1,
            DelegationKind.CHAIN: 0.3,
            DelegationKind.SPLIT: 0.2,
            DelegationKind.MERGE: 0.15,
        }
        return _map[self]

    def description(self) -> str:
        """Return a one-sentence description of this delegation kind."""
        _desc = {
            DelegationKind.DIRECT: "Point-to-point delegation with full trust transfer.",
            DelegationKind.PROXY: "Delegation through an intermediary proxy record.",
            DelegationKind.CHAIN: "Multi-hop delegation through a sequence of sections.",
            DelegationKind.SPLIT: "One source fans out to multiple target sections.",
            DelegationKind.MERGE: "Multiple sources converge to a single target section.",
        }
        return _desc[self]


# ---------------------------------------------------------------------------
# ProtocolSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolSection:
    """Behavioral section over a semantic coordinate in the sheaf of protocols.

    A :class:`ProtocolSection` records both the *declared* methods (the interface
    promised at construction) and the *observed* methods (the interface actually
    seen during the last verification).  The gap between these two sets is the
    primary input to :meth:`drift_score`.

    Parameters
    ----------
    section_id:
        Unique identifier (UUID string).
    coordinate:
        Semantic coordinate (e.g. an object path or topic URI).
    declared_methods:
        Tuple of method names promised by the section's interface.
    observed_methods:
        Tuple of method names actually observed during the last verification.
    stability_level:
        Current :class:`StabilityLevel` of this section.
    support_keys:
        Frozenset of geometry-layer support keys this section depends on.
    created_at:
        Unix timestamp of section creation.
    last_verified:
        Unix timestamp of the most recent verification pass.
    provenance:
        Ordered tuple of provenance strings (e.g. source URIs or agent IDs).
    """

    section_id: str
    coordinate: str
    declared_methods: tuple[str, ...]
    observed_methods: tuple[str, ...]
    stability_level: StabilityLevel
    support_keys: frozenset[str]
    created_at: float
    last_verified: float
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate field values at construction time."""
        if not self.section_id:
            raise ValueError("section_id must not be empty")
        if not self.coordinate:
            raise ValueError("coordinate must not be empty")
        if self.last_verified < self.created_at:
            raise ValueError("last_verified cannot precede created_at")

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_stale(self, threshold_seconds: float = 300.0) -> bool:
        """Return True when the section has not been verified recently enough.

        Parameters
        ----------
        threshold_seconds:
            Maximum allowed age (in seconds) since :attr:`last_verified`.
        """
        return self.verification_lag() > threshold_seconds

    def drift_score(self) -> float:
        """Compute the Jaccard-based drift between declared and observed methods.

        The score is ``1 - (|declared ∩ observed| / |declared ∪ observed|)``.
        A score of ``0.0`` means perfect agreement; ``1.0`` means total
        divergence.  Returns ``0.0`` for empty sections.
        """
        declared = set(self.declared_methods)
        observed = set(self.observed_methods)
        union = declared | observed
        if not union:
            return 0.0
        intersection = declared & observed
        return 1.0 - len(intersection) / len(union)

    def supported_methods(self) -> frozenset[str]:
        """Return the methods present in both declared and observed sets."""
        return frozenset(self.declared_methods) & frozenset(self.observed_methods)

    def missing_methods(self) -> frozenset[str]:
        """Return declared methods that were not observed."""
        return frozenset(self.declared_methods) - frozenset(self.observed_methods)

    def excess_methods(self) -> frozenset[str]:
        """Return observed methods that were not declared."""
        return frozenset(self.observed_methods) - frozenset(self.declared_methods)

    def age_seconds(self) -> float:
        """Return seconds elapsed since :attr:`created_at`."""
        return time.time() - self.created_at

    def verification_lag(self) -> float:
        """Return seconds elapsed since :attr:`last_verified`."""
        return time.time() - self.last_verified

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "section_id": self.section_id,
            "coordinate": self.coordinate,
            "declared_methods": list(self.declared_methods),
            "observed_methods": list(self.observed_methods),
            "stability_level": self.stability_level.value,
            "support_keys": sorted(self.support_keys),
            "created_at": self.created_at,
            "last_verified": self.last_verified,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolSection:
        """Reconstruct a :class:`ProtocolSection` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`to_dict`.
        """
        return cls(
            section_id=data["section_id"],
            coordinate=data["coordinate"],
            declared_methods=tuple(data["declared_methods"]),
            observed_methods=tuple(data["observed_methods"]),
            stability_level=StabilityLevel(data["stability_level"]),
            support_keys=frozenset(data["support_keys"]),
            created_at=float(data["created_at"]),
            last_verified=float(data["last_verified"]),
            provenance=tuple(data.get("provenance", [])),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        drift = self.drift_score()
        return (
            f"ProtocolSection(id={self.section_id[:8]}, coord={self.coordinate!r}, "
            f"level={self.stability_level.value}, drift={drift:.3f}, "
            f"declared={len(self.declared_methods)}, observed={len(self.observed_methods)})"
        )


# ---------------------------------------------------------------------------
# ProxyRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyRecord:
    """Transport-restricted proxy for a :class:`ProtocolSection`.

    A proxy wraps a target section and enforces a :class:`ProxyRestriction`
    policy on every attribute access.  The ``allowed_attributes`` frozenset
    lists the only attributes that may be read (when the restriction permits
    reads at all).  Transport metadata is stored as an immutable tuple of
    key-value pairs.

    Parameters
    ----------
    proxy_id:
        Unique identifier (UUID string).
    target_section_id:
        ID of the :class:`ProtocolSection` this proxy wraps.
    restriction:
        :class:`ProxyRestriction` policy in effect.
    allowed_attributes:
        Frozenset of attribute names that are explicitly permitted.
    transport_metadata:
        Immutable ordered pairs ``(key, value)`` for transport-layer metadata.
    created_at:
        Unix timestamp of proxy creation.
    expires_at:
        Unix timestamp after which this proxy is considered expired.
    """

    proxy_id: str
    target_section_id: str
    restriction: ProxyRestriction
    allowed_attributes: frozenset[str]
    transport_metadata: tuple[tuple[str, str], ...]
    created_at: float
    expires_at: float

    def __post_init__(self) -> None:
        """Validate expiry is after creation."""
        if self.expires_at < self.created_at:
            raise ValueError("expires_at cannot precede created_at")

    def is_expired(self) -> bool:
        """Return True when the current time is past :attr:`expires_at`."""
        return time.time() >= self.expires_at

    def can_access(self, attr: str) -> bool:
        """Return True when ``attr`` may be accessed under this proxy's restriction.

        A BLOCKED proxy denies all access.  An OPAQUE proxy denies reads as
        well.  Otherwise, access is granted only if ``attr`` is in
        :attr:`allowed_attributes` (or :attr:`allowed_attributes` is empty,
        meaning no per-attribute filtering is applied).
        """
        if self.is_expired():
            return False
        if self.restriction == ProxyRestriction.BLOCKED:
            return False
        if self.restriction == ProxyRestriction.OPAQUE:
            return False
        if self.allowed_attributes and attr not in self.allowed_attributes:
            return False
        return True

    def transport_keys(self) -> list[str]:
        """Return a sorted list of all transport metadata keys."""
        return sorted(k for k, _ in self.transport_metadata)

    def transport_value(self, key: str) -> str | None:
        """Return the transport metadata value for ``key``, or None."""
        for k, v in self.transport_metadata:
            if k == key:
                return v
        return None

    def ttl_seconds(self) -> float:
        """Return remaining seconds until expiry; negative if already expired."""
        return self.expires_at - time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "proxy_id": self.proxy_id,
            "target_section_id": self.target_section_id,
            "restriction": self.restriction.value,
            "allowed_attributes": sorted(self.allowed_attributes),
            "transport_metadata": [list(pair) for pair in self.transport_metadata],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProxyRecord:
        """Reconstruct a :class:`ProxyRecord` from a plain dictionary."""
        return cls(
            proxy_id=data["proxy_id"],
            target_section_id=data["target_section_id"],
            restriction=ProxyRestriction(data["restriction"]),
            allowed_attributes=frozenset(data.get("allowed_attributes", [])),
            transport_metadata=tuple(
                tuple(pair) for pair in data.get("transport_metadata", [])  # type: ignore[misc]
            ),
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        expired = "EXPIRED" if self.is_expired() else f"ttl={self.ttl_seconds():.0f}s"
        return (
            f"ProxyRecord(id={self.proxy_id[:8]}, "
            f"target={self.target_section_id[:8]}, "
            f"restriction={self.restriction.value}, {expired})"
        )


# ---------------------------------------------------------------------------
# DelegationChain
# ---------------------------------------------------------------------------


@dataclass
class DelegationChain:
    """A chain of protocol section IDs connected by delegation morphisms.

    The chain is mutable: links can be added or removed during its lifetime.
    Cycle detection uses a simple set-membership check on the :attr:`links`
    list.

    Parameters
    ----------
    chain_id:
        Unique identifier.
    links:
        Ordered list of section IDs from head (delegator) to tail (delegatee).
    delegation_kind:
        The :class:`DelegationKind` that applies to every morphism in the chain.
    trust_ceiling:
        Maximum trust score (0.0–1.0) that any link in the chain may claim.
    created_at:
        Unix timestamp of chain creation.
    metadata:
        Arbitrary key-value metadata attached to this chain.
    """

    chain_id: str
    links: list[str] = field(default_factory=list)
    delegation_kind: DelegationKind = DelegationKind.DIRECT
    trust_ceiling: float = 1.0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_link(self, section_id: str) -> None:
        """Append a section ID to the end of the chain.

        Parameters
        ----------
        section_id:
            The section ID to append.

        Raises
        ------
        ValueError
            If adding the link would create a cycle.
        """
        if section_id in self.links:
            raise ValueError(
                f"Adding {section_id!r} would create a cycle in chain {self.chain_id!r}"
            )
        self.links.append(section_id)

    def remove_link(self, section_id: str) -> bool:
        """Remove the first occurrence of ``section_id`` from the chain.

        Returns
        -------
        bool
            ``True`` if the link was found and removed; ``False`` otherwise.
        """
        try:
            self.links.remove(section_id)
            return True
        except ValueError:
            return False

    def chain_length(self) -> int:
        """Return the number of links in the chain."""
        return len(self.links)

    def is_cyclic(self) -> bool:
        """Return True when the chain contains duplicate section IDs."""
        return len(self.links) != len(set(self.links))

    def head(self) -> str | None:
        """Return the first section ID (delegator), or None for an empty chain."""
        return self.links[0] if self.links else None

    def tail(self) -> str | None:
        """Return the last section ID (ultimate delegatee), or None if empty."""
        return self.links[-1] if self.links else None

    def contains(self, section_id: str) -> bool:
        """Return True when ``section_id`` is anywhere in the chain."""
        return section_id in self.links

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "chain_id": self.chain_id,
            "links": list(self.links),
            "delegation_kind": self.delegation_kind.value,
            "trust_ceiling": self.trust_ceiling,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelegationChain:
        """Reconstruct a :class:`DelegationChain` from a plain dictionary."""
        return cls(
            chain_id=data["chain_id"],
            links=list(data.get("links", [])),
            delegation_kind=DelegationKind(data.get("delegation_kind", "direct")),
            trust_ceiling=float(data.get("trust_ceiling", 1.0)),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"DelegationChain(id={self.chain_id[:8]}, "
            f"kind={self.delegation_kind.value}, "
            f"length={self.chain_length()}, "
            f"cyclic={self.is_cyclic()}, "
            f"trust_ceiling={self.trust_ceiling:.2f})"
        )


# ---------------------------------------------------------------------------
# UnstableInterface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnstableInterface:
    """The boundary (surface) of a protocol section where support is retracting.

    An :class:`UnstableInterface` tracks which methods remain on the surface of
    a protocol section at any given moment, the rate at which they are
    retracting, and the obstruction keys that prevent further retraction from
    being silently discarded.

    Parameters
    ----------
    interface_id:
        Unique identifier.
    protocol_section_id:
        ID of the :class:`ProtocolSection` whose surface this describes.
    surface_methods:
        Tuple of method names currently on the surface.
    retraction_rate:
        Estimated rate of retraction in methods per second (non-negative).
    last_retraction:
        Unix timestamp of the most recent retraction event.
    obstruction_keys:
        Frozenset of keys where retraction is blocked by an obstruction.
    """

    interface_id: str
    protocol_section_id: str
    surface_methods: tuple[str, ...]
    retraction_rate: float
    last_retraction: float
    obstruction_keys: frozenset[str]

    def is_retracting(self) -> bool:
        """Return True when the retraction rate is positive."""
        return self.retraction_rate > 0.0

    def surface_size(self) -> int:
        """Return the number of methods currently on the surface."""
        return len(self.surface_methods)

    def obstruction_count(self) -> int:
        """Return the number of active obstruction keys."""
        return len(self.obstruction_keys)

    def retraction_age_seconds(self) -> float:
        """Return seconds elapsed since the last retraction event."""
        return time.time() - self.last_retraction

    def effective_stability(self) -> StabilityLevel:
        """Infer a :class:`StabilityLevel` from the retraction rate.

        The mapping is based on rate thresholds chosen to reflect realistic
        protocol degradation patterns:
        * 0.0          → STABLE
        * (0, 0.01]    → DEGRADING
        * (0.01, 0.05] → UNSTABLE
        * (0.05, 0.2]  → RETRACTING
        * > 0.2        → COLLAPSED
        """
        r = self.retraction_rate
        if r <= 0.0:
            return StabilityLevel.STABLE
        if r <= 0.01:
            return StabilityLevel.DEGRADING
        if r <= 0.05:
            return StabilityLevel.UNSTABLE
        if r <= 0.2:
            return StabilityLevel.RETRACTING
        return StabilityLevel.COLLAPSED

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "interface_id": self.interface_id,
            "protocol_section_id": self.protocol_section_id,
            "surface_methods": list(self.surface_methods),
            "retraction_rate": self.retraction_rate,
            "last_retraction": self.last_retraction,
            "obstruction_keys": sorted(self.obstruction_keys),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnstableInterface:
        """Reconstruct an :class:`UnstableInterface` from a plain dictionary."""
        return cls(
            interface_id=data["interface_id"],
            protocol_section_id=data["protocol_section_id"],
            surface_methods=tuple(data.get("surface_methods", [])),
            retraction_rate=float(data.get("retraction_rate", 0.0)),
            last_retraction=float(data.get("last_retraction", time.time())),
            obstruction_keys=frozenset(data.get("obstruction_keys", [])),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"UnstableInterface(id={self.interface_id[:8]}, "
            f"section={self.protocol_section_id[:8]}, "
            f"surface_size={self.surface_size()}, "
            f"rate={self.retraction_rate:.4f}, "
            f"obstructions={self.obstruction_count()}, "
            f"stability={self.effective_stability().value})"
        )


# ---------------------------------------------------------------------------
# StabilityMonitor
# ---------------------------------------------------------------------------


@dataclass
class StabilityMonitor:
    """Monitors a collection of protocol sections for drift and instability.

    The monitor maintains a snapshot of the most recently observed
    :class:`ProtocolSection` for each tracked section ID.  Each call to
    :meth:`observe` overwrites the previous snapshot for that ID and appends
    an entry to the history list (up to :attr:`max_history` entries).

    Parameters
    ----------
    monitor_id:
        Unique identifier for this monitor instance.
    observed_sections:
        Mapping from section_id to the most recently observed section.
    alert_threshold:
        Drift score above which a section triggers an alert (0.0–1.0).
    history:
        Ordered list of observation records (section_id, drift, timestamp).
    max_history:
        Maximum number of history entries to retain.
    """

    monitor_id: str
    observed_sections: dict[str, ProtocolSection] = field(default_factory=dict)
    alert_threshold: float = 0.5
    history: list[dict[str, Any]] = field(default_factory=list)
    max_history: int = 1000

    def observe(self, section: ProtocolSection) -> None:
        """Record an observation of ``section``, updating the snapshot.

        Parameters
        ----------
        section:
            The :class:`ProtocolSection` instance to observe.
        """
        self.observed_sections[section.section_id] = section
        entry = {
            "section_id": section.section_id,
            "drift": section.drift_score(),
            "stability_level": section.stability_level.value,
            "timestamp": time.time(),
        }
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def remove(self, section_id: str) -> bool:
        """Stop monitoring a section.

        Parameters
        ----------
        section_id:
            The section to stop monitoring.

        Returns
        -------
        bool
            ``True`` if the section was found and removed.
        """
        if section_id in self.observed_sections:
            del self.observed_sections[section_id]
            return True
        return False

    def compute_drift(self) -> dict[str, float]:
        """Return a mapping of section_id → current drift score for all observed sections."""
        return {sid: s.drift_score() for sid, s in self.observed_sections.items()}

    def alert_if_unstable(self) -> list[dict[str, Any]]:
        """Return alert records for every section whose drift exceeds the threshold.

        Each alert record contains:
        ``section_id``, ``drift``, ``stability_level``, ``threshold``, ``timestamp``.
        """
        alerts: list[dict[str, Any]] = []
        now = time.time()
        for section_id, section in self.observed_sections.items():
            drift = section.drift_score()
            if drift > self.alert_threshold:
                alerts.append(
                    {
                        "section_id": section_id,
                        "drift": drift,
                        "stability_level": section.stability_level.value,
                        "threshold": self.alert_threshold,
                        "timestamp": now,
                    }
                )
        return alerts

    def section_count(self) -> int:
        """Return the number of currently observed sections."""
        return len(self.observed_sections)

    def most_unstable(self) -> ProtocolSection | None:
        """Return the observed section with the highest drift score, or None."""
        if not self.observed_sections:
            return None
        return max(self.observed_sections.values(), key=lambda s: s.drift_score())

    def least_unstable(self) -> ProtocolSection | None:
        """Return the observed section with the lowest drift score, or None."""
        if not self.observed_sections:
            return None
        return min(self.observed_sections.values(), key=lambda s: s.drift_score())

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the monitor state."""
        drifts = self.compute_drift()
        avg_drift = sum(drifts.values()) / len(drifts) if drifts else 0.0
        alerts = self.alert_if_unstable()
        return (
            f"StabilityMonitor(id={self.monitor_id[:8]}, "
            f"sections={self.section_count()}, "
            f"avg_drift={avg_drift:.3f}, "
            f"alerts={len(alerts)}, "
            f"history={len(self.history)}/{self.max_history})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "monitor_id": self.monitor_id,
            "observed_sections": {
                sid: s.to_dict() for sid, s in self.observed_sections.items()
            },
            "alert_threshold": self.alert_threshold,
            "history": list(self.history),
            "max_history": self.max_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StabilityMonitor:
        """Reconstruct a :class:`StabilityMonitor` from a plain dictionary."""
        monitor = cls(
            monitor_id=data["monitor_id"],
            alert_threshold=float(data.get("alert_threshold", 0.5)),
            max_history=int(data.get("max_history", 1000)),
        )
        for sid, s_data in data.get("observed_sections", {}).items():
            monitor.observed_sections[sid] = ProtocolSection.from_dict(s_data)
        monitor.history = list(data.get("history", []))
        return monitor

    def reset_history(self) -> None:
        """Clear the entire observation history list."""
        self.history.clear()


# ---------------------------------------------------------------------------

__all__ = [
    "StabilityLevel",
    "ProxyRestriction",
    "DelegationKind",
    "ProtocolSection",
    "ProxyRecord",
    "DelegationChain",
    "UnstableInterface",
    "StabilityMonitor",
]

# copilot: models.py – core frozen/mutable dataclasses and enumerations for the unstable_protocols package
