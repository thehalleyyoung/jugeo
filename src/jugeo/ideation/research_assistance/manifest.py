r"""Oracle capability manifests for JuGeo research assistance — Chapter 51.

This module defines the *manifest* layer that describes which oracles are
available, what capabilities they expose, and how the system should route
research assistance requests.  A :class:`ResearchAssistanceManifest` is the
single source of truth for a deployed research session.

Mathematical background
-----------------------

Let :math:`\mathcal{O}` be the set of registered oracles and
:math:`\mathcal{C}` the set of assistance capabilities.  Each oracle
:math:`o \in \mathcal{O}` exposes a capability subset
:math:`\text{caps}(o) \subseteq \mathcal{C}`.

A manifest :math:`M` is *complete* with respect to capability set
:math:`S \subseteq \mathcal{C}` if and only if:

.. math::

    \forall c \in S,\; \exists o \in M.\text{oracles} : c \in \text{caps}(o)

The :class:`ManifestValidator` checks completeness and structural validity.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OracleType(str, Enum):
    """Classifies the kind of oracle backing a research capability.

    COPILOT denotes a language-model-based oracle.  FORMAL denotes a
    certified proof checker.  HYBRID combines both.
    """

    COPILOT = "copilot"
    FORMAL = "formal"
    HYBRID = "hybrid"


class AssistanceCapability(str, Enum):
    """The four primary research assistance capabilities supported by JuGeo.

    Each capability corresponds to a class of research task that can be
    delegated to an oracle under the controlled-oracle discipline.
    """

    PROOF_SUGGESTION = "proof_suggestion"
    LEMMA_MINING = "lemma_mining"
    CONJECTURE_GENERATION = "conjecture_generation"
    FALSIFICATION = "falsification"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OracleDescriptor:
    """Describes a single oracle available within a research assistance session.

    An oracle descriptor captures the oracle's identity, type, capability
    surface, and trust parameters.  Descriptors are immutable: to change an
    oracle's configuration, register a new manifest with a new descriptor.

    Attributes:
        oracle_id: Stable unique identifier for this oracle.
        oracle_type: Broad category of oracle (copilot, formal, hybrid).
        capabilities: Frozenset-like tuple of capabilities this oracle provides.
        max_queries_per_session: Hard cap on oracle queries per research session.
        confidence_threshold: Minimum confidence score for an oracle response to
            be considered acceptable without additional verification.
        description: Human-readable description of the oracle.
    """

    oracle_id: str
    oracle_type: OracleType
    capabilities: tuple[AssistanceCapability, ...]
    max_queries_per_session: int = 100
    confidence_threshold: float = 0.7
    description: str = ""

    def supports(self, cap: AssistanceCapability) -> bool:
        """Return True if this oracle provides the given capability."""
        return cap in self.capabilities

    def is_high_confidence(self) -> bool:
        """Return True if the confidence threshold is at or above 0.8."""
        return self.confidence_threshold >= 0.8

    def summary(self) -> str:
        """Return a compact one-line description of this oracle."""
        cap_names = ", ".join(c.value for c in self.capabilities)
        return (
            f"Oracle[{self.oracle_id}] type={self.oracle_type.value} "
            f"caps=[{cap_names}] threshold={self.confidence_threshold:.2f} "
            f"max_queries={self.max_queries_per_session}"
        )


@dataclass(frozen=True, slots=True)
class ResearchAssistanceManifest:
    """Top-level manifest describing a full research assistance deployment.

    A manifest ties together a set of oracles and the aggregate capability
    surface they expose.  It is the document that a :class:`ManifestValidator`
    checks and a :class:`ManifestRegistry` indexes.

    Attributes:
        manifest_id: Stable unique identifier for this manifest version.
        created_at: Unix timestamp of manifest creation.
        oracles: Ordered tuple of oracle descriptors in this manifest.
        capabilities: Aggregate capability set offered by the manifest.
        version: Semantic version string (e.g. ``"1.0"``).
        description: Human-readable purpose statement for this manifest.
    """

    manifest_id: str
    created_at: float
    oracles: tuple[OracleDescriptor, ...]
    capabilities: tuple[AssistanceCapability, ...]
    version: str = "1.0"
    description: str = ""

    def oracle_by_id(self, oracle_id: str) -> OracleDescriptor | None:
        """Return the descriptor for the oracle with the given id, or None."""
        for oracle in self.oracles:
            if oracle.oracle_id == oracle_id:
                return oracle
        return None

    def supports(self, cap: AssistanceCapability) -> bool:
        """Return True if at least one oracle in this manifest provides cap."""
        return cap in self.capabilities

    def oracle_count(self) -> int:
        """Return the number of oracles registered in this manifest."""
        return len(self.oracles)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest to a plain dictionary."""
        return {
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "version": self.version,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "oracles": [
                {
                    "oracle_id": o.oracle_id,
                    "oracle_type": o.oracle_type.value,
                    "capabilities": [c.value for c in o.capabilities],
                    "max_queries_per_session": o.max_queries_per_session,
                    "confidence_threshold": o.confidence_threshold,
                    "description": o.description,
                }
                for o in self.oracles
            ],
        }

    def summary(self) -> str:
        """Return a compact human-readable summary of this manifest."""
        cap_names = ", ".join(c.value for c in self.capabilities)
        return (
            f"Manifest[{self.manifest_id}] v={self.version} "
            f"oracles={len(self.oracles)} caps=[{cap_names}]"
        )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates :class:`ResearchAssistanceManifest` instances.

    Validation is purely structural: it checks that identifiers are non-empty,
    numerical parameters are in range, and no oracle identifiers are duplicated.
    An empty error list means the manifest is valid.
    """

    def validate(self, manifest: ResearchAssistanceManifest) -> list[str]:
        """Return a list of error strings; empty list means the manifest is valid."""
        errors: list[str] = []

        if not manifest.manifest_id or not manifest.manifest_id.strip():
            errors.append("manifest_id must be non-empty")

        if not manifest.version or not manifest.version.strip():
            errors.append("version must be non-empty")

        seen_ids: set[str] = set()
        for oracle in manifest.oracles:
            if not oracle.oracle_id or not oracle.oracle_id.strip():
                errors.append(f"oracle has empty oracle_id: {oracle!r}")
                continue

            if oracle.oracle_id in seen_ids:
                errors.append(f"duplicate oracle_id: {oracle.oracle_id!r}")
            seen_ids.add(oracle.oracle_id)

            if not (0.0 <= oracle.confidence_threshold <= 1.0):
                errors.append(
                    f"oracle {oracle.oracle_id!r} confidence_threshold "
                    f"{oracle.confidence_threshold} is outside [0, 1]"
                )

            if oracle.max_queries_per_session <= 0:
                errors.append(
                    f"oracle {oracle.oracle_id!r} max_queries_per_session "
                    f"{oracle.max_queries_per_session} must be > 0"
                )

        _log.debug(
            "ManifestValidator: manifest=%s errors=%d",
            manifest.manifest_id,
            len(errors),
        )
        return errors

    def is_complete(
        self,
        manifest: ResearchAssistanceManifest,
        required: set[AssistanceCapability],
    ) -> bool:
        """Return True if the manifest covers all required capabilities."""
        covered = set(manifest.capabilities)
        return required.issubset(covered)

    def has_oracle_for(
        self,
        manifest: ResearchAssistanceManifest,
        cap: AssistanceCapability,
    ) -> bool:
        """Return True if at least one oracle explicitly supports the capability."""
        return any(oracle.supports(cap) for oracle in manifest.oracles)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """An in-memory registry of :class:`ResearchAssistanceManifest` instances.

    Manifests are keyed by their ``manifest_id``.  The registry maintains
    insertion order so that :meth:`latest` returns the most recently registered
    manifest.
    """

    def __init__(self) -> None:
        self._store: dict[str, ResearchAssistanceManifest] = {}
        self._order: list[str] = []

    def register(self, manifest: ResearchAssistanceManifest) -> None:
        """Register a manifest, replacing any existing entry with the same id."""
        if manifest.manifest_id in self._store:
            self._order.remove(manifest.manifest_id)
        self._store[manifest.manifest_id] = manifest
        self._order.append(manifest.manifest_id)
        _log.debug("ManifestRegistry: registered manifest=%s", manifest.manifest_id)

    def by_id(self, manifest_id: str) -> ResearchAssistanceManifest | None:
        """Return the manifest with the given id, or None if not found."""
        return self._store.get(manifest_id)

    def all(self) -> tuple[ResearchAssistanceManifest, ...]:
        """Return all registered manifests in insertion order."""
        return tuple(self._store[mid] for mid in self._order)

    def latest(self) -> ResearchAssistanceManifest | None:
        """Return the most recently registered manifest, or None if empty."""
        if not self._order:
            return None
        return self._store[self._order[-1]]

    def count(self) -> int:
        """Return the number of registered manifests."""
        return len(self._store)

    def remove(self, manifest_id: str) -> bool:
        """Remove a manifest by id; return True if it existed."""
        if manifest_id not in self._store:
            return False
        del self._store[manifest_id]
        self._order.remove(manifest_id)
        return True

    def clear(self) -> None:
        """Remove all registered manifests."""
        self._store.clear()
        self._order.clear()


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_default_manifest(
    *,
    manifest_id: str | None = None,
    description: str = "Default JuGeo research assistance manifest",
) -> ResearchAssistanceManifest:
    """Build a sensible default manifest with one copilot and one formal oracle."""
    mid = manifest_id or str(uuid.uuid4())
    copilot_oracle = OracleDescriptor(
        oracle_id="copilot-default",
        oracle_type=OracleType.COPILOT,
        capabilities=(
            AssistanceCapability.PROOF_SUGGESTION,
            AssistanceCapability.LEMMA_MINING,
            AssistanceCapability.CONJECTURE_GENERATION,
        ),
        max_queries_per_session=200,
        confidence_threshold=0.7,
        description="Default copilot oracle using language-model reasoning.",
    )
    formal_oracle = OracleDescriptor(
        oracle_id="formal-default",
        oracle_type=OracleType.FORMAL,
        capabilities=(
            AssistanceCapability.FALSIFICATION,
            AssistanceCapability.PROOF_SUGGESTION,
        ),
        max_queries_per_session=500,
        confidence_threshold=0.95,
        description="Default formal verifier oracle.",
    )
    all_caps_set: set[AssistanceCapability] = set(copilot_oracle.capabilities) | set(
        formal_oracle.capabilities
    )
    all_caps = tuple(sorted(all_caps_set, key=lambda c: c.value))
    return ResearchAssistanceManifest(
        manifest_id=mid,
        created_at=time.time(),
        oracles=(copilot_oracle, formal_oracle),
        capabilities=all_caps,
        version="1.0",
        description=description,
    )


def make_hybrid_manifest(
    *,
    manifest_id: str | None = None,
    description: str = "Hybrid JuGeo research assistance manifest",
) -> ResearchAssistanceManifest:
    """Build a hybrid manifest combining all four capabilities in one oracle."""
    mid = manifest_id or str(uuid.uuid4())
    hybrid_oracle = OracleDescriptor(
        oracle_id="hybrid-default",
        oracle_type=OracleType.HYBRID,
        capabilities=tuple(AssistanceCapability),
        max_queries_per_session=300,
        confidence_threshold=0.75,
        description="Hybrid oracle combining LLM and formal reasoning.",
    )
    return ResearchAssistanceManifest(
        manifest_id=mid,
        created_at=time.time(),
        oracles=(hybrid_oracle,),
        capabilities=tuple(AssistanceCapability),
        version="1.0",
        description=description,
    )


# ---------------------------------------------------------------------------
# Module-level default registry instance
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY: ManifestRegistry = ManifestRegistry()


def register_default() -> ResearchAssistanceManifest:
    """Create and register the default manifest in the module-level registry."""
    manifest = make_default_manifest()
    DEFAULT_REGISTRY.register(manifest)
    return manifest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AssistanceCapability",
    "DEFAULT_REGISTRY",
    "ManifestRegistry",
    "ManifestValidator",
    "OracleDescriptor",
    "OracleType",
    "ResearchAssistanceManifest",
    "make_default_manifest",
    "make_hybrid_manifest",
    "register_default",
]
