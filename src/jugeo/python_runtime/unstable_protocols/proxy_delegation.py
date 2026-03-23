"""Proxy and delegation theory for JuGeo unstable protocols (Ch22 §2).

Proxy objects as transport-restricted sections; delegation as morphisms
between protocol sections.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §2  Proxy delegation – a proxy is a transport-restricted section, i.e. a
      section that may only be accessed through a restricted channel.  The
      restriction is encoded as a :class:`~jugeo.python_runtime.unstable_protocols.models.ProxyRestriction`
      enum value, while the allowed attributes define the sub-sheaf accessible
      through the proxy.
* §2  Delegation morphisms – each delegation is a morphism
      ``φ : P(U) → P(V)`` that carries a trust factor ``t ∈ [0,1]``.
      Composition of morphisms reduces trust multiplicatively:
      ``trust(φ₂ ∘ φ₁) = trust(φ₁) × trust(φ₂)``.
* §2  Delegation chains – sequences of composable morphisms; a cycle in the
      chain graph corresponds to a non-trivial 1-cocycle in the nerve of the
      cover, which is an obstruction to gluing.
* §2  Proxy expiry – proxies are sections with a finite lifetime; after expiry
      the proxy collapses and all access is denied (Theorem T22.6).

The central invariant maintained by this module is that trust never increases
along a delegation chain.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dc_replace
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
# ProxyManager
# ---------------------------------------------------------------------------


@dataclass
class ProxyManager:
    """Manages proxy records and enforces transport restrictions on protocol sections.

    Proxies are created with a TTL; once expired they are automatically
    rejected by :meth:`check_access`.  Revoked proxies are permanently
    denied regardless of expiry status.

    Every access check (granted or denied) is recorded in :attr:`access_log`
    for audit purposes.

    Parameters
    ----------
    proxies:
        Live proxy records, keyed by proxy_id.
    revoked:
        Set of proxy IDs that have been permanently revoked.
    access_log:
        Ordered list of access-check records.
    """

    proxies: dict[str, ProxyRecord] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)
    access_log: list[dict[str, Any]] = field(default_factory=list)

    def create_proxy(
        self,
        target_section_id: str,
        restriction: ProxyRestriction,
        allowed_attributes: frozenset[str],
        transport_metadata: tuple[tuple[str, str], ...],
        ttl_seconds: float = 3600.0,
    ) -> ProxyRecord:
        """Create a new :class:`ProxyRecord` and register it.

        Parameters
        ----------
        target_section_id:
            ID of the protocol section being proxied.
        restriction:
            The :class:`ProxyRestriction` to apply.
        allowed_attributes:
            Frozenset of attribute names explicitly permitted.
        transport_metadata:
            Immutable sequence of ``(key, value)`` transport metadata pairs.
        ttl_seconds:
            Seconds from now until the proxy expires.

        Returns
        -------
        ProxyRecord
            The newly created and registered proxy.
        """
        now = time.time()
        proxy = ProxyRecord(
            proxy_id=str(uuid.uuid4()),
            target_section_id=target_section_id,
            restriction=restriction,
            allowed_attributes=allowed_attributes,
            transport_metadata=transport_metadata,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        self.proxies[proxy.proxy_id] = proxy
        return proxy

    def revoke(self, proxy_id: str) -> bool:
        """Permanently revoke a proxy, preventing all future access.

        Parameters
        ----------
        proxy_id:
            The proxy to revoke.

        Returns
        -------
        bool
            ``True`` if the proxy was known; ``False`` otherwise.
        """
        known = proxy_id in self.proxies or proxy_id in self.revoked
        self.revoked.add(proxy_id)
        return known

    def get(self, proxy_id: str) -> ProxyRecord | None:
        """Retrieve a proxy record by ID, returning None if not found.

        Parameters
        ----------
        proxy_id:
            The proxy to look up.
        """
        return self.proxies.get(proxy_id)

    def check_access(self, proxy_id: str, attribute: str) -> bool:
        """Check whether ``attribute`` may be accessed through proxy ``proxy_id``.

        Access is denied when the proxy is revoked, not found, expired,
        or its restriction policy forbids the attribute.  The result is
        recorded in the access log.

        Parameters
        ----------
        proxy_id:
            The proxy through which access is requested.
        attribute:
            The attribute name being requested.
        """
        if proxy_id in self.revoked:
            self.log_access(proxy_id, attribute, granted=False)
            return False
        proxy = self.proxies.get(proxy_id)
        if proxy is None:
            self.log_access(proxy_id, attribute, granted=False)
            return False
        granted = proxy.can_access(attribute)
        self.log_access(proxy_id, attribute, granted=granted)
        return granted

    def list_active(self) -> list[ProxyRecord]:
        """Return all non-revoked, non-expired proxies."""
        now = time.time()
        return [
            p
            for pid, p in self.proxies.items()
            if pid not in self.revoked and p.expires_at > now
        ]

    def list_expired(self) -> list[ProxyRecord]:
        """Return all proxies that have passed their expiry time."""
        now = time.time()
        return [p for p in self.proxies.values() if p.expires_at <= now]

    def purge_expired(self) -> int:
        """Remove all expired proxies from the registry.

        Returns
        -------
        int
            Number of proxies removed.
        """
        now = time.time()
        expired_ids = [pid for pid, p in self.proxies.items() if p.expires_at <= now]
        for pid in expired_ids:
            del self.proxies[pid]
        return len(expired_ids)

    def log_access(self, proxy_id: str, attribute: str, granted: bool) -> None:
        """Record an access-check event in the access log.

        Parameters
        ----------
        proxy_id:
            The proxy involved.
        attribute:
            The attribute being accessed.
        granted:
            Whether access was granted.
        """
        self.access_log.append(
            {
                "proxy_id": proxy_id,
                "attribute": attribute,
                "granted": granted,
                "timestamp": time.time(),
            }
        )

    def access_audit(self, proxy_id: str) -> list[dict[str, Any]]:
        """Return all access log entries for a specific proxy.

        Parameters
        ----------
        proxy_id:
            The proxy to filter by.
        """
        return [e for e in self.access_log if e["proxy_id"] == proxy_id]

    def export_state(self) -> dict[str, Any]:
        """Serialise manager state to a plain dictionary."""
        return {
            "proxies": {pid: p.to_dict() for pid, p in self.proxies.items()},
            "revoked": sorted(self.revoked),
            "access_log_count": len(self.access_log),
        }


# ---------------------------------------------------------------------------
# DelegationMorphism
# ---------------------------------------------------------------------------


@dataclass
class DelegationMorphism:
    """Represents a morphism between two protocol sections encoding a delegation.

    A delegation morphism ``φ : P(source) → P(target)`` carries a trust factor
    ``t ∈ [0,1]`` and a ``method_map`` that translates source method names to
    target method names.  Composition of two morphisms produces a new morphism
    whose trust is the product of the two factors.

    Parameters
    ----------
    morphism_id:
        Unique identifier.
    source_section_id:
        ID of the source protocol section.
    target_section_id:
        ID of the target protocol section.
    delegation_kind:
        The :class:`DelegationKind` classifying the morphism.
    trust_factor:
        Trust scalar in [0.0, 1.0].
    method_map:
        Mapping from source method name to target method name.
    created_at:
        Unix timestamp of morphism creation.
    metadata:
        Arbitrary extra metadata.
    """

    morphism_id: str
    source_section_id: str
    target_section_id: str
    delegation_kind: DelegationKind
    trust_factor: float
    method_map: dict[str, str]
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Return True when the morphism passes structural validity checks.

        Checks:
        * ``trust_factor ∈ [0.0, 1.0]``
        * ``source_section_id ≠ target_section_id`` (no self-loops)
        * ``method_map`` is non-empty
        """
        if not (0.0 <= self.trust_factor <= 1.0):
            return False
        if self.source_section_id == self.target_section_id:
            return False
        if not self.method_map:
            return False
        return True

    def compose(self, other: DelegationMorphism) -> DelegationMorphism | None:
        """Compose ``self`` with ``other`` (self then other).

        Composition requires that ``self.target_section_id == other.source_section_id``.
        The composed morphism's method_map is built by following the chain
        ``self.method_map`` then ``other.method_map``.  Trust is multiplied.

        Parameters
        ----------
        other:
            The morphism to compose with (applied after self).

        Returns
        -------
        DelegationMorphism | None
            The composed morphism, or ``None`` if composition is not possible.
        """
        if self.target_section_id != other.source_section_id:
            return None

        composed_map: dict[str, str] = {}
        for src_method, mid_method in self.method_map.items():
            if mid_method in other.method_map:
                composed_map[src_method] = other.method_map[mid_method]

        if not composed_map:
            return None

        composed_trust = self.trust_factor * other.trust_factor
        # choose the more general delegation kind
        kind = (
            DelegationKind.CHAIN
            if self.delegation_kind != other.delegation_kind
            else self.delegation_kind
        )
        return DelegationMorphism(
            morphism_id=str(uuid.uuid4()),
            source_section_id=self.source_section_id,
            target_section_id=other.target_section_id,
            delegation_kind=kind,
            trust_factor=composed_trust,
            method_map=composed_map,
            created_at=time.time(),
            metadata={
                "composed_from": [self.morphism_id, other.morphism_id],
            },
        )

    def invert(self) -> DelegationMorphism | None:
        """Return the inverse morphism, only valid for DIRECT kind with trust 1.0.

        A morphism is invertible only when it is a bijection on methods
        (injective and surjective) with full trust and DIRECT kind.

        Returns
        -------
        DelegationMorphism | None
            The inverted morphism, or ``None`` if not invertible.
        """
        if self.delegation_kind != DelegationKind.DIRECT:
            return None
        if abs(self.trust_factor - 1.0) > 1e-9:
            return None
        # Check that method_map is a bijection
        if len(self.method_map) != len(set(self.method_map.values())):
            return None
        inverted_map = {v: k for k, v in self.method_map.items()}
        return DelegationMorphism(
            morphism_id=str(uuid.uuid4()),
            source_section_id=self.target_section_id,
            target_section_id=self.source_section_id,
            delegation_kind=DelegationKind.DIRECT,
            trust_factor=1.0,
            method_map=inverted_map,
            created_at=time.time(),
            metadata={"inverted_from": self.morphism_id},
        )

    def image_methods(self) -> set[str]:
        """Return the set of target methods that this morphism maps to."""
        return set(self.method_map.values())

    def preimage_methods(self) -> set[str]:
        """Return the set of source methods that this morphism maps from."""
        return set(self.method_map.keys())

    def trust_reduction(self) -> float:
        """Return the fractional trust reduction: ``1.0 - trust_factor``."""
        return 1.0 - self.trust_factor

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "morphism_id": self.morphism_id,
            "source_section_id": self.source_section_id,
            "target_section_id": self.target_section_id,
            "delegation_kind": self.delegation_kind.value,
            "trust_factor": self.trust_factor,
            "method_map": dict(self.method_map),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelegationMorphism:
        """Reconstruct a :class:`DelegationMorphism` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.
        """
        return cls(
            morphism_id=data["morphism_id"],
            source_section_id=data["source_section_id"],
            target_section_id=data["target_section_id"],
            delegation_kind=DelegationKind(data["delegation_kind"]),
            trust_factor=float(data["trust_factor"]),
            method_map=dict(data.get("method_map", {})),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"DelegationMorphism(id={self.morphism_id[:8]}, "
            f"{self.source_section_id[:8]} → {self.target_section_id[:8]}, "
            f"kind={self.delegation_kind.value}, "
            f"trust={self.trust_factor:.3f}, "
            f"methods={len(self.method_map)})"
        )


# ---------------------------------------------------------------------------
# DelegationChainBuilder
# ---------------------------------------------------------------------------


@dataclass
class DelegationChainBuilder:
    """Builds and validates delegation chains as sequences of morphisms.

    A delegation chain is a path through the delegation graph: a sequence of
    morphisms ``φ₁, φ₂, …, φₙ`` where the target of each morphism is the
    source of the next.  The builder validates acyclicity and monotone trust
    reduction.

    Parameters
    ----------
    morphisms:
        Registry of known morphisms, keyed by morphism_id.
    chains:
        Mapping from chain_id to ordered list of morphism_ids.
    """

    morphisms: dict[str, DelegationMorphism] = field(default_factory=dict)
    chains: dict[str, list[str]] = field(default_factory=dict)

    def add_morphism(self, morphism: DelegationMorphism) -> None:
        """Register a morphism in the local registry.

        Parameters
        ----------
        morphism:
            The morphism to register.

        Raises
        ------
        ValueError
            If the morphism fails :meth:`DelegationMorphism.is_valid`.
        """
        if not morphism.is_valid():
            raise ValueError(f"Morphism {morphism.morphism_id!r} is not valid")
        self.morphisms[morphism.morphism_id] = morphism

    def remove_morphism(self, morphism_id: str) -> bool:
        """Remove a morphism from the registry.

        Parameters
        ----------
        morphism_id:
            ID to remove.

        Returns
        -------
        bool
            ``True`` if found and removed.
        """
        if morphism_id in self.morphisms:
            del self.morphisms[morphism_id]
            # also remove from any chains
            for chain_id, mids in list(self.chains.items()):
                if morphism_id in mids:
                    mids.remove(morphism_id)
            return True
        return False

    def build_chain(
        self, chain_id: str, morphism_ids: list[str]
    ) -> DelegationChain | None:
        """Build a :class:`DelegationChain` from an ordered list of morphism IDs.

        The chain is valid when all morphisms exist, they form a consecutive
        path (target of each = source of next), and the chain is acyclic.

        Parameters
        ----------
        chain_id:
            Identifier for the new chain.
        morphism_ids:
            Ordered list of morphism IDs forming the chain.

        Returns
        -------
        DelegationChain | None
            The assembled chain, or ``None`` if invalid.
        """
        if not morphism_ids:
            return None

        morphisms = [self.morphisms.get(mid) for mid in morphism_ids]
        if any(m is None for m in morphisms):
            return None

        # Validate path connectivity
        for i in range(len(morphisms) - 1):
            if morphisms[i].target_section_id != morphisms[i + 1].source_section_id:  # type: ignore[union-attr]
                return None

        # Build link list from section IDs
        links: list[str] = [morphisms[0].source_section_id]  # type: ignore[union-attr]
        for m in morphisms:
            links.append(m.target_section_id)  # type: ignore[union-attr]

        if len(links) != len(set(links)):
            # cycle detected
            return None

        trust = self.compute_chain_trust(morphism_ids)
        kind = morphisms[0].delegation_kind  # type: ignore[union-attr]
        chain = DelegationChain(
            chain_id=chain_id,
            links=links,
            delegation_kind=kind,
            trust_ceiling=trust,
            created_at=time.time(),
            metadata={"morphism_ids": list(morphism_ids)},
        )
        self.chains[chain_id] = list(morphism_ids)
        return chain

    def validate_chain(self, chain_id: str) -> bool:
        """Validate that a registered chain is acyclic and trust is non-increasing.

        Parameters
        ----------
        chain_id:
            The chain to validate.
        """
        morphism_ids = self.chains.get(chain_id)
        if not morphism_ids:
            return False
        if self.detect_cycles_in(morphism_ids):
            return False
        # trust must be non-increasing along the chain
        prev_trust = 1.0
        for mid in morphism_ids:
            m = self.morphisms.get(mid)
            if m is None:
                return False
            if m.trust_factor > prev_trust + 1e-9:
                return False
            prev_trust = m.trust_factor
        return True

    def detect_cycles_in(self, morphism_ids: list[str]) -> bool:
        """Return True when the given morphism sequence contains a cycle.

        A cycle exists when any section ID appears more than once in the
        node list of the path induced by the morphisms.

        Parameters
        ----------
        morphism_ids:
            Ordered morphism IDs to check.
        """
        seen: set[str] = set()
        for mid in morphism_ids:
            m = self.morphisms.get(mid)
            if m is None:
                continue
            if m.source_section_id in seen or m.target_section_id in seen:
                return True
            seen.add(m.source_section_id)
            seen.add(m.target_section_id)
        return False

    def compute_chain_trust(self, morphism_ids: list[str]) -> float:
        """Compute the cumulative trust for a chain as the product of factors.

        Parameters
        ----------
        morphism_ids:
            Ordered morphism IDs.
        """
        trust = 1.0
        for mid in morphism_ids:
            m = self.morphisms.get(mid)
            if m is not None:
                trust *= m.trust_factor
        return trust

    def chain_statistics(self, chain_id: str) -> dict[str, Any]:
        """Return statistics about a registered chain.

        Parameters
        ----------
        chain_id:
            The chain to analyse.

        Returns
        -------
        dict[str, Any]
            Keys: ``chain_id``, ``length``, ``trust``, ``valid``,
            ``morphism_kinds``.
        """
        morphism_ids = self.chains.get(chain_id, [])
        trust = self.compute_chain_trust(morphism_ids)
        kinds: list[str] = []
        for mid in morphism_ids:
            m = self.morphisms.get(mid)
            if m is not None:
                kinds.append(m.delegation_kind.value)
        return {
            "chain_id": chain_id,
            "length": len(morphism_ids),
            "trust": trust,
            "valid": self.validate_chain(chain_id),
            "morphism_kinds": kinds,
        }

    def all_chains(self) -> list[str]:
        """Return a list of all registered chain IDs."""
        return list(self.chains.keys())

    def export_chains(self) -> dict[str, Any]:
        """Serialise all chains and morphisms to a plain dictionary."""
        return {
            "morphisms": {mid: m.to_dict() for mid, m in self.morphisms.items()},
            "chains": dict(self.chains),
        }

    def import_chains(self, data: dict[str, Any]) -> None:
        """Replace builder state with data from a previously exported snapshot.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`export_chains`.
        """
        self.morphisms.clear()
        self.chains.clear()
        for mid, m_data in data.get("morphisms", {}).items():
            self.morphisms[mid] = DelegationMorphism.from_dict(m_data)
        self.chains = {k: list(v) for k, v in data.get("chains", {}).items()}


# ---------------------------------------------------------------------------
# ProxyValidator
# ---------------------------------------------------------------------------


@dataclass
class ProxyValidator:
    """Validates proxy records against their target protocol sections.

    Validation checks that:
    * The proxy has not expired.
    * The proxy's allowed_attributes are a subset of the section's
      declared methods.
    * The proxy's restriction level is compatible with the section's
      current stability level (e.g. a RETRACTING section should not
      have a NONE restriction proxy).

    Parameters
    ----------
    validation_log:
        Ordered list of validation event records.
    strict:
        When ``True``, any policy mismatch is a validation failure.
    """

    validation_log: list[dict[str, Any]] = field(default_factory=list)
    strict: bool = True

    def validate(self, proxy: ProxyRecord, section: ProtocolSection) -> bool:
        """Validate a proxy against its target section.

        Parameters
        ----------
        proxy:
            The proxy to validate.
        section:
            The section the proxy is supposed to wrap.

        Returns
        -------
        bool
            ``True`` when the proxy passes all checks.
        """
        if proxy.is_expired():
            self.log_validation(proxy.proxy_id, section.section_id, False, "proxy expired")
            return False

        if proxy.target_section_id != section.section_id:
            self.log_validation(
                proxy.proxy_id, section.section_id, False, "target section ID mismatch"
            )
            return False

        if proxy.allowed_attributes:
            declared_set = set(section.declared_methods)
            forbidden = proxy.allowed_attributes - declared_set
            if forbidden and self.strict:
                self.log_validation(
                    proxy.proxy_id,
                    section.section_id,
                    False,
                    f"allowed_attributes {sorted(forbidden)} not in section declared_methods",
                )
                return False

        # Policy consistency: retracting/collapsed sections should be opaque or blocked
        if section.stability_level in (StabilityLevel.RETRACTING, StabilityLevel.COLLAPSED):
            if proxy.restriction not in (ProxyRestriction.OPAQUE, ProxyRestriction.BLOCKED):
                if self.strict:
                    self.log_validation(
                        proxy.proxy_id,
                        section.section_id,
                        False,
                        f"section {section.stability_level.value} requires OPAQUE/BLOCKED restriction",
                    )
                    return False

        self.log_validation(proxy.proxy_id, section.section_id, True, "all checks passed")
        return True

    def check_restrictions(self, proxy: ProxyRecord, requested_attr: str) -> bool:
        """Check whether a specific attribute access is permitted by the proxy.

        Parameters
        ----------
        proxy:
            The proxy record.
        requested_attr:
            The attribute name being requested.
        """
        return proxy.can_access(requested_attr)

    def check_transport(self, proxy: ProxyRecord, transport_key: str) -> bool:
        """Return True when the proxy carries a transport metadata entry for ``key``.

        Parameters
        ----------
        proxy:
            The proxy record.
        transport_key:
            The metadata key to check.
        """
        return proxy.transport_value(transport_key) is not None

    def validate_batch(
        self,
        proxies: list[ProxyRecord],
        sections: dict[str, ProtocolSection],
    ) -> dict[str, bool]:
        """Validate a list of proxies against their respective sections.

        Parameters
        ----------
        proxies:
            List of proxy records to validate.
        sections:
            Mapping from section_id to :class:`ProtocolSection`.

        Returns
        -------
        dict[str, bool]
            Mapping from proxy_id to validation result.
        """
        results: dict[str, bool] = {}
        for proxy in proxies:
            section = sections.get(proxy.target_section_id)
            if section is None:
                self.log_validation(
                    proxy.proxy_id, proxy.target_section_id, False, "section not found"
                )
                results[proxy.proxy_id] = False
            else:
                results[proxy.proxy_id] = self.validate(proxy, section)
        return results

    def compliance_report(
        self, proxy: ProxyRecord, section: ProtocolSection
    ) -> dict[str, Any]:
        """Return a structured compliance report for a proxy/section pair.

        Parameters
        ----------
        proxy:
            The proxy record.
        section:
            The target section.
        """
        expired = proxy.is_expired()
        section_id_match = proxy.target_section_id == section.section_id
        forbidden_attrs: list[str] = []
        if proxy.allowed_attributes:
            declared = set(section.declared_methods)
            forbidden_attrs = sorted(proxy.allowed_attributes - declared)

        policy_ok = True
        policy_note = "ok"
        if section.stability_level in (StabilityLevel.RETRACTING, StabilityLevel.COLLAPSED):
            if proxy.restriction not in (ProxyRestriction.OPAQUE, ProxyRestriction.BLOCKED):
                policy_ok = False
                policy_note = (
                    f"section {section.stability_level.value} should use "
                    "OPAQUE/BLOCKED restriction"
                )

        return {
            "proxy_id": proxy.proxy_id,
            "section_id": section.section_id,
            "expired": expired,
            "section_id_match": section_id_match,
            "forbidden_attrs": forbidden_attrs,
            "policy_ok": policy_ok,
            "policy_note": policy_note,
            "overall_valid": (
                not expired
                and section_id_match
                and not forbidden_attrs
                and policy_ok
            ),
            "timestamp": time.time(),
        }

    def patch_proxy_attributes(
        self, proxy: ProxyRecord, new_attrs: frozenset[str]
    ) -> ProxyRecord:
        """Return a new :class:`ProxyRecord` with updated ``allowed_attributes``.

        Parameters
        ----------
        proxy:
            Original proxy record.
        new_attrs:
            Replacement frozenset of allowed attribute names.
        """
        return dc_replace(proxy, allowed_attributes=new_attrs)

    def log_validation(
        self,
        proxy_id: str,
        section_id: str,
        result: bool,
        reason: str,
    ) -> None:
        """Append a validation event to the log.

        Parameters
        ----------
        proxy_id:
            Proxy involved.
        section_id:
            Target section involved.
        result:
            Pass (True) or fail (False).
        reason:
            Short description of the outcome.
        """
        self.validation_log.append(
            {
                "proxy_id": proxy_id,
                "section_id": section_id,
                "result": result,
                "reason": reason,
                "timestamp": time.time(),
            }
        )

    def export_log(self) -> list[dict[str, Any]]:
        """Return a copy of the validation log."""
        return list(self.validation_log)


# ---------------------------------------------------------------------------

__all__ = [
    "ProxyManager",
    "DelegationMorphism",
    "DelegationChainBuilder",
    "ProxyValidator",
]

# copilot: proxy_delegation.py – proxy manager, delegation morphisms, chain builder, and proxy validator (Ch22 §2)
