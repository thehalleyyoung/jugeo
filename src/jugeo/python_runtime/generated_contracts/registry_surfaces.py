"""
jugeo.python_runtime.generated_contracts.registry_surfaces

theory2.tex Ch21 §3 — Registry Surfaces.

A ContractRegistry is a sheaf of contract entries indexed over the coordinate
site.  A RegistrySurface is the public API surface of such a registry — the
global sections accessible to external consumers.  In sheaf-theoretic terms,
the surface is a natural transformation from the registry presheaf to the
constant presheaf of public APIs.

Sheaf-theoretic framing:
    Let F_reg : Site^op → Set be the registry presheaf, assigning to each
    coordinate U the set of contract entries registered at U.  The surface
    functor S : F_reg → Δ(PublicAPI) is a natural transformation to the
    constant presheaf of public APIs.  Global sections Γ(S) are the APIs
    visible to external consumers.

    Restriction maps ρ_{V⊆U} : F_reg(U) → F_reg(V) encode how registrations
    at a broad coordinate restrict to narrower sub-coordinates.

Judgment tuple representation throughout:
    (c, φ, A, E, O, B, T, Π) where
        c  = coordinate (site / location in the sheaf)
        φ  = formula   (the contract predicate)
        A  = carrier   (the type / value being contracted)
        E  = evidence  (runtime witnesses)
        O  = obligations (what must hold)
        B  = obstructions (what must not hold)
        T  = trust     (TrustTier value)
        Π  = provenance (origin / source trace)

References:
    theory2.tex Ch21 §3: Registry Surfaces and Sheaf-Theoretic API Exposure.

# copilot: registry surface is a natural transformation from registry presheaf to public API
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import logging
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jugeo imports with inline stub fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind, Site
except Exception:
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple = ()
        kind: Any = None
        support_labels: frozenset = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class Site: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, Carrier,
        EvidenceBundle, EvidenceItem, ResidualObligation, Obstruction,
        TrustAnnotation, Provenance,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; provenance: tuple = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1; is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple = (); chain: tuple = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None; is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str = "rs") -> str:
    """Generate a short unique identifier with the given prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# TrustTier — ordered trust algebra
# ---------------------------------------------------------------------------

class TrustTier(enum.IntEnum):
    """Ordered trust algebra for registry surfaces.

    theory2.tex Ch21 §3 — trust tiers form a partial order:
        PROPOSAL ≤ REVIEWED ≤ VERIFIED ≤ RUNTIME_WITNESSED ≤ PROOF_BACKED
    Higher tiers require stronger evidence.
    """
    PROPOSAL          = 1
    REVIEWED          = 2
    VERIFIED          = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED      = 5

    def satisfies(self, minimum: "TrustTier") -> bool:
        """Return True iff self >= minimum in the trust order."""
        return self.value >= minimum.value

    def elevate(self, target: "TrustTier") -> "TrustTier":
        """Return the higher of self and target."""
        return TrustTier(max(self.value, target.value))


# ---------------------------------------------------------------------------
# JudgmentTuple — canonical (c, φ, A, E, O, B, T, Π) representation
# ---------------------------------------------------------------------------

class JudgmentTuple(NamedTuple):
    """The canonical (c, φ, A, E, O, B, T, Π) judgment representation.

    Fields:
        c   — coordinate: site / location in the sheaf
        phi — formula: the contract predicate
        A   — carrier: the type / value being contracted
        E   — evidence: runtime witnesses
        O   — obligations: what must hold
        B   — obstructions: what must not hold
        T   — trust: TrustTier value
        Pi  — provenance: origin / source trace
    """
    c: Any    # coordinate
    phi: Any  # formula
    A: Any    # carrier
    E: Any    # evidence
    O: Any    # obligations
    B: Any    # obstructions
    T: Any    # trust
    Pi: Any   # provenance


# ---------------------------------------------------------------------------
# ContractEntry — a single leaf in the registry presheaf
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContractEntry:
    """A single contract entry in the registry sheaf.

    Represents a leaf section of the registry presheaf F_reg over one
    coordinate.  Each entry carries its own trust tier and can be discharged
    by supplying evidence.

    theory2.tex Ch21 §3.1 — entries are the atoms of the registry surface.
    """
    entry_id: str
    coordinate_key: str
    source: str
    contracts: tuple
    trust_tier: TrustTier
    is_active: bool = True
    metadata: dict = field(default_factory=dict)

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this entry."""
        logger.debug("ContractEntry.to_judgment_tuple: entry_id=%s", self.entry_id)
        c = self.coordinate_key
        phi = f"entry_contract:{self.entry_id}"
        A = Carrier(name=self.source, is_dependent=False)
        E = EvidenceBundle(items=(
            EvidenceItem(
                payload={"entry_id": self.entry_id, "contracts_count": len(self.contracts)},
                channel="registry",
                timestamp=_now_iso(),
            ),
        ))
        O = tuple(
            ResidualObligation(
                description=f"contract_{i}_obligation",
                obligation_id=f"{self.entry_id}_ob_{i}",
                priority=1,
            )
            for i, _ in enumerate(self.contracts)
        )
        B = (
            ()
            if self.is_active
            else (Obstruction(description="inactive_entry", obstruction_id=f"{self.entry_id}_inact"),)
        )
        T = self.trust_tier
        Pi = Provenance(sources=(self.source,), chain=(self.entry_id, self.coordinate_key))
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def discharge(self, evidence: str) -> "ContractEntry":
        """Return a new entry with metadata recording the discharged evidence."""
        logger.info("ContractEntry.discharge: entry_id=%s evidence=%s", self.entry_id, evidence[:40])
        new_meta = {**self.metadata, "discharged_evidence": evidence, "discharged_at": _now_iso()}
        return replace(self, metadata=new_meta)

    def elevate_trust(self, new_tier: TrustTier) -> "ContractEntry":
        """Return a new entry with trust_tier elevated to new_tier if higher."""
        elevated = self.trust_tier.elevate(new_tier)
        if elevated != self.trust_tier:
            logger.info(
                "ContractEntry.elevate_trust: entry_id=%s %s -> %s",
                self.entry_id, self.trust_tier.name, elevated.name,
            )
        return replace(self, trust_tier=elevated)

    def deactivate(self) -> "ContractEntry":
        """Return a new entry with is_active=False."""
        logger.debug("ContractEntry.deactivate: entry_id=%s", self.entry_id)
        return replace(self, is_active=False)

    def summary(self) -> str:
        """Return a one-line summary of this entry."""
        return (
            f"ContractEntry[{self.entry_id}] coord={self.coordinate_key!r} "
            f"source={self.source!r} trust={self.trust_tier.name} "
            f"active={self.is_active} contracts={len(self.contracts)}"
        )


# ---------------------------------------------------------------------------
# RegistryQuery — query predicate over ContractEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RegistryQuery:
    """A query predicate for filtering contract entries from a registry.

    theory2.tex Ch21 §3.2 — queries are morphisms in the site that select
    sections of the registry presheaf.
    """
    query_id: str
    coordinate_pattern: str
    source_filter: Optional[str] = None
    trust_minimum: TrustTier = TrustTier.PROPOSAL
    active_only: bool = True

    def matches(self, entry: ContractEntry) -> bool:
        """Return True iff the given entry satisfies this query predicate.

        Checks:
          1. coordinate_key contains coordinate_pattern (substring match)
          2. entry.trust_tier.satisfies(trust_minimum)
          3. active_only → entry.is_active
          4. source_filter (if set) matches entry.source exactly
        """
        if self.coordinate_pattern and self.coordinate_pattern not in entry.coordinate_key:
            logger.debug(
                "RegistryQuery.matches: REJECT entry=%s pattern=%r not in coord=%r",
                entry.entry_id, self.coordinate_pattern, entry.coordinate_key,
            )
            return False
        if not entry.trust_tier.satisfies(self.trust_minimum):
            logger.debug(
                "RegistryQuery.matches: REJECT entry=%s trust %s < min %s",
                entry.entry_id, entry.trust_tier.name, self.trust_minimum.name,
            )
            return False
        if self.active_only and not entry.is_active:
            logger.debug("RegistryQuery.matches: REJECT entry=%s inactive", entry.entry_id)
            return False
        if self.source_filter is not None and entry.source != self.source_filter:
            logger.debug(
                "RegistryQuery.matches: REJECT entry=%s source %r != filter %r",
                entry.entry_id, entry.source, self.source_filter,
            )
            return False
        logger.debug("RegistryQuery.matches: ACCEPT entry=%s", entry.entry_id)
        return True

    def summary(self) -> str:
        """Return a one-line summary of this query."""
        return (
            f"RegistryQuery[{self.query_id}] pattern={self.coordinate_pattern!r} "
            f"trust_min={self.trust_minimum.name} active_only={self.active_only} "
            f"source_filter={self.source_filter!r}"
        )


# ---------------------------------------------------------------------------
# ContractRegistry — the mutable registry (sheaf) of entries
# ---------------------------------------------------------------------------

@dataclass
class ContractRegistry:
    """A mutable registry of ContractEntry objects, keyed by entry_id.

    Acts as the underlying sheaf F_reg whose global sections form the
    RegistrySurface.  Thread-safe via an internal lock.

    theory2.tex Ch21 §3.3 — the registry is a presheaf over the coordinate site.
    """
    registry_id: str
    entries: dict = field(default_factory=dict)
    surface_cache: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def register(self, entry: ContractEntry) -> str:
        """Add an entry to the registry and return its entry_id.

        Invalidates the surface cache on every successful registration.
        Thread-safe.
        """
        with self._lock:
            self.entries[entry.entry_id] = entry
            self.invalidate_cache()
            logger.info(
                "ContractRegistry.register: registry=%s entry=%s trust=%s",
                self.registry_id, entry.entry_id, entry.trust_tier.name,
            )
        return entry.entry_id

    def query(self, q: RegistryQuery) -> list:
        """Return all entries that match the given RegistryQuery."""
        results = [e for e in self.entries.values() if q.matches(e)]
        logger.debug(
            "ContractRegistry.query: registry=%s query=%s results=%d",
            self.registry_id, q.query_id, len(results),
        )
        return results

    def invalidate_cache(self) -> None:
        """Clear the surface cache."""
        self.surface_cache.clear()
        logger.debug("ContractRegistry.invalidate_cache: registry=%s", self.registry_id)

    def snapshot(self) -> tuple:
        """Return all active entries as an immutable tuple. Thread-safe."""
        with self._lock:
            result = tuple(e for e in self.entries.values() if e.is_active)
        logger.debug(
            "ContractRegistry.snapshot: registry=%s active_entries=%d",
            self.registry_id, len(result),
        )
        return result

    def merge(self, other: "ContractRegistry") -> "ContractRegistry":
        """Return a new registry combining entries from both registries.

        Entries from `other` overwrite entries from `self` on key collision.
        """
        logger.info(
            "ContractRegistry.merge: self=%s other=%s",
            self.registry_id, other.registry_id,
        )
        merged_entries = {**self.entries, **other.entries}
        new_reg = ContractRegistry(registry_id=_new_id("reg"))
        new_reg.entries = merged_entries
        return new_reg

    def get_by_coordinate(self, coord: str) -> list:
        """Return all entries whose coordinate_key matches coord exactly."""
        result = [e for e in self.entries.values() if e.coordinate_key == coord]
        logger.debug(
            "ContractRegistry.get_by_coordinate: coord=%r found=%d", coord, len(result)
        )
        return result

    def discharge_entry(self, entry_id: str, evidence: str) -> bool:
        """Discharge the entry with the given entry_id, recording evidence.

        Returns True on success, False if entry_id is not found.
        """
        with self._lock:
            entry = self.entries.get(entry_id)
            if entry is None:
                logger.warning(
                    "ContractRegistry.discharge_entry: entry_id=%s not found in registry=%s",
                    entry_id, self.registry_id,
                )
                return False
            self.entries[entry_id] = entry.discharge(evidence)
            self.invalidate_cache()
            logger.info(
                "ContractRegistry.discharge_entry: entry=%s evidence=%s",
                entry_id, evidence[:40],
            )
        return True

    def all_judgment_tuples(self) -> list:
        """Return JudgmentTuple for every entry in the registry."""
        return [e.to_judgment_tuple() for e in self.entries.values()]

    def summary(self) -> dict:
        """Return a summary dict of the registry state."""
        active = sum(1 for e in self.entries.values() if e.is_active)
        return {
            "registry_id": self.registry_id,
            "total_entries": len(self.entries),
            "active_entries": active,
            "inactive_entries": len(self.entries) - active,
            "trust_breakdown": {
                tier.name: sum(1 for e in self.entries.values() if e.trust_tier == tier)
                for tier in TrustTier
            },
        }


# ---------------------------------------------------------------------------
# RegistrySurface — the public API surface (global sections of the presheaf)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RegistrySurface:
    """The public API surface of a ContractRegistry.

    Represents the global sections Γ(S) of the surface natural transformation
    S : F_reg → Δ(PublicAPI).  Only entries at or below trust_ceiling are
    exposed.

    theory2.tex Ch21 §3.4 — RegistrySurface is a natural transformation.
    """
    surface_id: str
    registry_ref_id: str
    public_api: tuple
    version: int
    trust_ceiling: TrustTier

    def expose(self, name: str) -> Optional[ContractEntry]:
        """Return the first ContractEntry whose coordinate_key contains name.

        Returns None if no such entry exists in public_api.
        """
        for entry in self.public_api:
            if isinstance(entry, ContractEntry) and name in entry.coordinate_key:
                logger.debug("RegistrySurface.expose: found entry %s for name=%r", entry.entry_id, name)
                return entry
        logger.debug("RegistrySurface.expose: no entry for name=%r in surface=%s", name, self.surface_id)
        return None

    def diff(self, other: "RegistrySurface") -> dict:
        """Return a dict of differences between self and other.

        Keys: added, removed, changed, version_delta.
        """
        self_ids = {e.entry_id: e for e in self.public_api if isinstance(e, ContractEntry)}
        other_ids = {e.entry_id: e for e in other.public_api if isinstance(e, ContractEntry)}
        added = [e for eid, e in other_ids.items() if eid not in self_ids]
        removed = [e for eid, e in self_ids.items() if eid not in other_ids]
        changed = [
            e for eid, e in other_ids.items()
            if eid in self_ids and self_ids[eid].metadata != e.metadata
        ]
        result = {
            "added": added,
            "removed": removed,
            "changed": changed,
            "version_delta": other.version - self.version,
        }
        logger.debug(
            "RegistrySurface.diff: surface=%s vs %s added=%d removed=%d changed=%d",
            self.surface_id, other.surface_id, len(added), len(removed), len(changed),
        )
        return result

    def to_judgment_tuple(self) -> JudgmentTuple:
        """Return the (c, φ, A, E, O, B, T, Π) representation of this surface."""
        logger.debug("RegistrySurface.to_judgment_tuple: surface_id=%s", self.surface_id)
        c = f"surface:{self.surface_id}"
        phi = f"registry_surface:ceiling={self.trust_ceiling.name}:v{self.version}"
        A = Carrier(name=f"surface:{self.surface_id}", is_dependent=True)
        E = EvidenceBundle(items=(
            EvidenceItem(
                payload={
                    "surface_id": self.surface_id,
                    "registry_ref_id": self.registry_ref_id,
                    "api_size": len(self.public_api),
                    "version": self.version,
                    "trust_ceiling": self.trust_ceiling.name,
                },
                channel="registry_surface",
                timestamp=_now_iso(),
            ),
        ))
        O = tuple(
            ResidualObligation(
                description=f"expose_entry_{i}",
                obligation_id=f"{self.surface_id}_ob_{i}",
                priority=1,
            )
            for i in range(len(self.public_api))
        )
        B = ()
        T = self.trust_ceiling
        Pi = Provenance(
            sources=(self.registry_ref_id,),
            chain=(self.surface_id, f"v{self.version}"),
        )
        return JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)

    def elevate(self, new_ceiling: TrustTier) -> "RegistrySurface":
        """Return a new surface with trust_ceiling elevated and version incremented."""
        elevated = self.trust_ceiling.elevate(new_ceiling)
        logger.info(
            "RegistrySurface.elevate: surface=%s ceiling %s -> %s",
            self.surface_id, self.trust_ceiling.name, elevated.name,
        )
        return replace(self, trust_ceiling=elevated, version=self.version + 1)

    def api_names(self) -> list:
        """Return the coordinate_keys of all entries in public_api."""
        return [e.coordinate_key for e in self.public_api if isinstance(e, ContractEntry)]


# ---------------------------------------------------------------------------
# SurfaceAPI — serialisable manifest of the surface's endpoints
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SurfaceAPI:
    """A serialisable manifest of the public API endpoints exposed by a surface.

    theory2.tex Ch21 §3.5 — SurfaceAPI is the concrete representation of
    the global sections as a set of named endpoints.
    """
    api_id: str
    endpoint_names: tuple
    backing_registry_id: str
    generated_at: float = field(default_factory=time.time)
    version: int = 1

    def to_manifest(self) -> dict:
        """Return a serialised dict representation of this SurfaceAPI."""
        return {
            "api_id": self.api_id,
            "endpoint_names": list(self.endpoint_names),
            "backing_registry_id": self.backing_registry_id,
            "generated_at": self.generated_at,
            "version": self.version,
            "endpoint_count": len(self.endpoint_names),
        }

    def has_endpoint(self, name: str) -> bool:
        """Return True iff name is one of the exposed endpoint names."""
        return name in self.endpoint_names

    def merge_endpoints(self, other: "SurfaceAPI") -> "SurfaceAPI":
        """Return a new SurfaceAPI with deduplicated endpoints from both APIs."""
        combined = tuple(dict.fromkeys(list(self.endpoint_names) + list(other.endpoint_names)))
        logger.info(
            "SurfaceAPI.merge_endpoints: api=%s other=%s combined=%d",
            self.api_id, other.api_id, len(combined),
        )
        return replace(self, endpoint_names=combined, version=self.version + 1, generated_at=time.time())

    def filter_endpoints(self, prefix: str) -> "SurfaceAPI":
        """Return a new SurfaceAPI with only endpoints starting with prefix."""
        filtered = tuple(n for n in self.endpoint_names if n.startswith(prefix))
        return replace(self, endpoint_names=filtered)


# ---------------------------------------------------------------------------
# ContractRegistryCoordinator — plan / execute / normalize workflow
# ---------------------------------------------------------------------------

@dataclass
class ContractRegistryCoordinator:
    """Coordinates the lifecycle of contract registrations.

    Implements a plan → execute → normalize workflow for building a
    RegistrySurface from a list of ContractEntry objects.

    theory2.tex Ch21 §3.6 — the coordinator is the functor that maps the
    category of contract entries to the category of registry surfaces.
    """
    coordinator_id: str
    registry: ContractRegistry
    surface: Optional[RegistrySurface] = None
    history: list = field(default_factory=list)
    _pending: dict = field(default_factory=dict, repr=False)

    def plan(self, entries: list) -> list:
        """Plan the registration of entries by staging them.

        Returns list of entry_ids that will be registered on execute().
        """
        logger.info(
            "ContractRegistryCoordinator.plan: coordinator=%s entries=%d",
            self.coordinator_id, len(entries),
        )
        for entry in entries:
            self._pending[entry.entry_id] = entry
        ids = [e.entry_id for e in entries]
        self.history.append({"action": "plan", "entry_ids": ids, "timestamp": _now_iso()})
        return ids

    def execute(self, entry_ids: list) -> list:
        """Execute registration of the staged entries matching entry_ids.

        Returns list of JudgmentTuple for successfully registered entries.
        """
        logger.info(
            "ContractRegistryCoordinator.execute: coordinator=%s ids=%d",
            self.coordinator_id, len(entry_ids),
        )
        judgment_tuples = []
        for eid in entry_ids:
            entry = self._pending.get(eid)
            if entry is None:
                # try fetching from registry in case already registered
                entry = self.registry.entries.get(eid)
            if entry is not None:
                self.registry.register(entry)
                jt = entry.to_judgment_tuple()
                judgment_tuples.append(jt)
                logger.debug("ContractRegistryCoordinator.execute: registered entry=%s", eid)
            else:
                logger.warning(
                    "ContractRegistryCoordinator.execute: entry_id=%s not found in pending", eid
                )
        self.history.append({
            "action": "execute",
            "entry_ids": entry_ids,
            "registered": len(judgment_tuples),
            "timestamp": _now_iso(),
        })
        return judgment_tuples

    def normalize(self, surface: RegistrySurface) -> RegistrySurface:
        """Normalize the surface by deduplicating entries by entry_id.

        Returns a new RegistrySurface with a deduplicated public_api.
        """
        seen: set = set()
        deduped = []
        for e in surface.public_api:
            if isinstance(e, ContractEntry):
                if e.entry_id not in seen:
                    seen.add(e.entry_id)
                    deduped.append(e)
            else:
                deduped.append(e)
        logger.info(
            "ContractRegistryCoordinator.normalize: surface=%s before=%d after=%d",
            surface.surface_id, len(surface.public_api), len(deduped),
        )
        return replace(surface, public_api=tuple(deduped), version=surface.version + 1)

    def coordinate(self, entries: list) -> RegistrySurface:
        """Run the full plan → execute → normalize pipeline.

        Registers all entries, builds a RegistrySurface, normalizes it,
        and stores it as self.surface.
        """
        logger.info(
            "ContractRegistryCoordinator.coordinate: coordinator=%s entries=%d",
            self.coordinator_id, len(entries),
        )
        ids = self.plan(entries)
        self.execute(ids)
        raw_surface = build_registry_surface(self.registry, TrustTier.PROOF_BACKED)
        normalized = self.normalize(raw_surface)
        self.surface = normalized
        self.history.append({
            "action": "coordinate",
            "surface_id": normalized.surface_id,
            "api_size": len(normalized.public_api),
            "timestamp": _now_iso(),
        })
        logger.info(
            "ContractRegistryCoordinator.coordinate: built surface=%s api_size=%d",
            normalized.surface_id, len(normalized.public_api),
        )
        return normalized


# ---------------------------------------------------------------------------
# ContractRegistryAnalyzer — introspects modules for contract patterns
# ---------------------------------------------------------------------------

@dataclass
class ContractRegistryAnalyzer:
    """Analyzes Python modules and classes to discover contract patterns.

    Inspects modules for dataclass, NamedTuple, TypedDict, and attrs patterns,
    generating ContractEntry objects for each discovered pattern.

    theory2.tex Ch21 §3.7 — the analyzer implements the restriction functor
    from module sections to contract entries.
    """
    analyzer_id: str
    findings: list = field(default_factory=list)

    def analyze_module(self, module: Any) -> list:
        """Inspect a module and return ContractEntry for each class pattern found."""
        logger.info(
            "ContractRegistryAnalyzer.analyze_module: analyzer=%s module=%s",
            self.analyzer_id, getattr(module, "__name__", repr(module)),
        )
        results = []
        try:
            members = inspect.getmembers(module, inspect.isclass)
        except Exception as exc:
            logger.warning("ContractRegistryAnalyzer.analyze_module: inspect error: %s", exc)
            members = []
        for name, cls in members:
            if name.startswith("_"):
                continue
            entry = self.analyze_class(cls)
            if entry is not None:
                results.append(entry)
                self.findings.append(entry)
        logger.info(
            "ContractRegistryAnalyzer.analyze_module: found %d contracts", len(results)
        )
        return results

    def analyze_class(self, cls: type) -> Optional[ContractEntry]:
        """Inspect a single class and return a ContractEntry if it matches.

        Detects: dataclasses (frozen/slots), NamedTuple subclasses, TypedDict.
        Returns None for classes that don't match any contract pattern.
        """
        try:
            qualname = getattr(cls, "__qualname__", getattr(cls, "__name__", repr(cls)))
            module_name = getattr(cls, "__module__", "unknown")
            coord_key = f"{module_name}.{qualname}"
            patterns_found = []
            metadata: dict = {"qualname": qualname, "module": module_name}
            # Check for dataclass
            if dataclasses.is_dataclass(cls) and isinstance(cls, type):
                params = getattr(cls, "__dataclass_params__", None)
                is_frozen = getattr(params, "frozen", False) if params else False
                has_slots = "__slots__" in cls.__dict__
                patterns_found.append("dataclass")
                metadata["is_frozen"] = is_frozen
                metadata["has_slots"] = has_slots
                metadata["field_count"] = len(dataclasses.fields(cls))
            # Check for NamedTuple
            elif issubclass(cls, tuple) and hasattr(cls, "_fields"):
                patterns_found.append("namedtuple")
                metadata["fields"] = list(cls._fields)
            # Check for TypedDict
            elif hasattr(cls, "__annotations__") and hasattr(cls, "__total__"):
                patterns_found.append("typeddict")
                metadata["annotations"] = list(cls.__annotations__.keys())
            else:
                return None
            annotation_text = "|".join(patterns_found)
            contracts = tuple(
                AnnotationContract(
                    symbol_name=f"{qualname}.{f}",
                    annotation_text=annotation_text,
                    trust_level=None,
                )
                for f in metadata.get("fields", [f.name for f in (dataclasses.fields(cls) if dataclasses.is_dataclass(cls) and isinstance(cls, type) else [])])
            ) if patterns_found else (AnnotationContract(symbol_name=qualname, annotation_text=annotation_text),)
            entry = ContractEntry(
                entry_id=_new_id("analyzed"),
                coordinate_key=coord_key,
                source="analyze_class",
                contracts=contracts,
                trust_tier=TrustTier.PROPOSAL,
                metadata={**metadata, "patterns": patterns_found, "analyzed_at": _now_iso()},
            )
            logger.debug(
                "ContractRegistryAnalyzer.analyze_class: %s patterns=%s",
                qualname, patterns_found,
            )
            return entry
        except Exception as exc:
            logger.warning("ContractRegistryAnalyzer.analyze_class: error: %s", exc)
            return None

    def summarize(self) -> dict:
        """Return a summary dict of findings."""
        pattern_counts: dict = {}
        for entry in self.findings:
            for p in entry.metadata.get("patterns", []):
                pattern_counts[p] = pattern_counts.get(p, 0) + 1
        return {
            "analyzer_id": self.analyzer_id,
            "total_findings": len(self.findings),
            "pattern_counts": pattern_counts,
            "unique_coordinates": len({e.coordinate_key for e in self.findings}),
        }


# ---------------------------------------------------------------------------
# ContractRegistryWitness — runtime introspection witness
# ---------------------------------------------------------------------------

@dataclass
class ContractRegistryWitness:
    """Runtime witness that introspects values against contract entries.

    Observes live values, checks them against contract predicates, and
    records JudgmentTuple results.

    theory2.tex Ch21 §3.8 — the witness applies the natural transformation
    S at a specific value, producing a local section of the surface.
    """
    witness_id: str
    observations: list = field(default_factory=list)

    def observe(self, entry: ContractEntry, value: Any) -> JudgmentTuple:
        """Perform runtime introspection on value against entry's contracts.

        Checks type identity, hasattr, length, and other structural properties.
        Returns a JudgmentTuple with trust based on how many checks pass.
        """
        logger.debug(
            "ContractRegistryWitness.observe: witness=%s entry=%s value_type=%s",
            self.witness_id, entry.entry_id, type(value).__name__,
        )
        checks_passed = 0
        checks_total = 0
        evidence_items = []
        # Check 1: value is not None
        checks_total += 1
        if value is not None:
            checks_passed += 1
            evidence_items.append(EvidenceItem(
                payload={"check": "not_none", "result": True},
                channel="witness",
                timestamp=_now_iso(),
            ))
        # Check 2: value has expected attributes from contract symbol names
        for contract in entry.contracts:
            if isinstance(contract, AnnotationContract) and contract.symbol_name:
                sym = contract.symbol_name.split(".")[-1]
                if sym and value is not None:
                    checks_total += 1
                    has_attr = hasattr(value, sym)
                    if has_attr:
                        checks_passed += 1
                    evidence_items.append(EvidenceItem(
                        payload={"check": f"hasattr_{sym}", "result": has_attr},
                        channel="witness",
                        timestamp=_now_iso(),
                    ))
        # Determine trust based on pass rate
        pass_rate = checks_passed / max(checks_total, 1)
        if pass_rate >= 0.9:
            trust = TrustTier.RUNTIME_WITNESSED
        elif pass_rate >= 0.5:
            trust = TrustTier.REVIEWED
        else:
            trust = TrustTier.PROPOSAL
        # Build judgment tuple
        c = entry.coordinate_key
        phi = f"witness_check:{entry.entry_id}:pass_rate={pass_rate:.2f}"
        A = Carrier(name=f"witness:{type(value).__name__}" if value is not None else "witness:None")
        E = EvidenceBundle(items=tuple(evidence_items))
        O = tuple(
            ResidualObligation(
                description=f"unmet_contract_{i}",
                obligation_id=f"{self.witness_id}_ob_{i}",
                priority=1,
                is_discharged=(checks_passed == checks_total),
            )
            for i in range(max(0, checks_total - checks_passed))
        )
        B = (
            (Obstruction(description=f"witness_failure:{entry.entry_id}", obstruction_id=f"{self.witness_id}_fail"),)
            if pass_rate < 0.5 else ()
        )
        T = trust
        Pi = Provenance(
            sources=(self.witness_id,),
            chain=(entry.entry_id, f"pass_rate={pass_rate:.2f}", _now_iso()),
        )
        jt = JudgmentTuple(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)
        self.observations.append({
            "entry_id": entry.entry_id,
            "value_type": type(value).__name__ if value is not None else "NoneType",
            "trust": trust.name,
            "pass_rate": pass_rate,
            "timestamp": _now_iso(),
            "judgment_tuple": jt,
        })
        logger.debug(
            "ContractRegistryWitness.observe: entry=%s trust=%s pass_rate=%.2f",
            entry.entry_id, trust.name, pass_rate,
        )
        return jt

    def witness_registry(self, registry: ContractRegistry) -> list:
        """Witness all active entries in the registry with value=None.

        Returns list of JudgmentTuple for each active entry.
        """
        logger.info(
            "ContractRegistryWitness.witness_registry: witness=%s registry=%s",
            self.witness_id, registry.registry_id,
        )
        results = []
        for entry in registry.entries.values():
            if entry.is_active:
                jt = self.observe(entry, None)
                results.append(jt)
        return results

    def report(self) -> dict:
        """Return a summary of all observations."""
        trust_counts: dict = {}
        for obs in self.observations:
            t = obs["trust"]
            trust_counts[t] = trust_counts.get(t, 0) + 1
        avg_pass_rate = (
            sum(obs["pass_rate"] for obs in self.observations) / len(self.observations)
            if self.observations else 0.0
        )
        return {
            "witness_id": self.witness_id,
            "total_observations": len(self.observations),
            "trust_distribution": trust_counts,
            "average_pass_rate": avg_pass_rate,
        }


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def register_contract(registry: ContractRegistry, entry: ContractEntry) -> str:
    """Register a contract entry in the registry and return its entry_id."""
    logger.info(
        "register_contract: registering entry=%s in registry=%s",
        entry.entry_id, registry.registry_id,
    )
    return registry.register(entry)


def query_registry(registry: ContractRegistry, query: RegistryQuery) -> list:
    """Query the registry and return matching entries."""
    logger.debug(
        "query_registry: query_id=%s pattern=%r registry=%s",
        query.query_id, query.coordinate_pattern, registry.registry_id,
    )
    results = registry.query(query)
    logger.info("query_registry: found %d results for query=%s", len(results), query.query_id)
    return results


def build_registry_surface(registry: ContractRegistry, trust_ceiling: TrustTier) -> RegistrySurface:
    """Build a RegistrySurface from a registry, exposing entries at or below trust_ceiling."""
    logger.info(
        "build_registry_surface: registry=%s trust_ceiling=%s",
        registry.registry_id, trust_ceiling.name,
    )
    public_api = tuple(
        e for e in registry.entries.values()
        if e.is_active and e.trust_tier.value <= trust_ceiling.value
    )
    surface_id = _new_id("surf")
    logger.debug("build_registry_surface: exposed %d entries in surface=%s", len(public_api), surface_id)
    return RegistrySurface(
        surface_id=surface_id,
        registry_ref_id=registry.registry_id,
        public_api=public_api,
        version=1,
        trust_ceiling=trust_ceiling,
    )


def build_surface_api(surface: RegistrySurface) -> SurfaceAPI:
    """Build a SurfaceAPI from a RegistrySurface."""
    endpoint_names = tuple(
        e.coordinate_key
        for e in surface.public_api
        if isinstance(e, ContractEntry)
    )
    return SurfaceAPI(
        api_id=_new_id("api"),
        endpoint_names=endpoint_names,
        backing_registry_id=surface.registry_ref_id,
        version=surface.version,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print("=== registry_surfaces smoke test ===")

    # TrustTier ordering
    assert TrustTier.PROPOSAL < TrustTier.REVIEWED < TrustTier.VERIFIED
    assert TrustTier.VERIFIED < TrustTier.RUNTIME_WITNESSED < TrustTier.PROOF_BACKED
    assert TrustTier.VERIFIED.satisfies(TrustTier.REVIEWED)
    assert not TrustTier.REVIEWED.satisfies(TrustTier.VERIFIED)
    assert TrustTier.VERIFIED.elevate(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert TrustTier.PROOF_BACKED.elevate(TrustTier.PROPOSAL) == TrustTier.PROOF_BACKED
    print("TrustTier ordering: OK")

    # ContractEntry creation and methods
    entry1 = ContractEntry(
        entry_id=_new_id("entry"),
        coordinate_key="jugeo.models.UserModel",
        source="analyze_class",
        contracts=(AnnotationContract(symbol_name="UserModel", annotation_text="dataclass"),),
        trust_tier=TrustTier.REVIEWED,
        metadata={"created_at": _now_iso()},
    )
    jt = entry1.to_judgment_tuple()
    assert isinstance(jt, JudgmentTuple)
    assert jt.T == TrustTier.REVIEWED
    assert jt.c == "jugeo.models.UserModel"
    discharged = entry1.discharge("manual review passed")
    assert "discharged_evidence" in discharged.metadata
    assert "discharged_at" in discharged.metadata
    elevated = entry1.elevate_trust(TrustTier.VERIFIED)
    assert elevated.trust_tier == TrustTier.VERIFIED
    print(f"ContractEntry (id={entry1.entry_id[:20]}...): OK")

    # RegistryQuery matching
    q = RegistryQuery(
        query_id=_new_id("q"),
        coordinate_pattern="UserModel",
        trust_minimum=TrustTier.PROPOSAL,
        active_only=True,
    )
    assert q.matches(entry1)
    q2 = RegistryQuery(query_id=_new_id("q"), coordinate_pattern="NonExistent", trust_minimum=TrustTier.PROPOSAL)
    assert not q2.matches(entry1)
    q3 = RegistryQuery(query_id=_new_id("q"), coordinate_pattern="UserModel", trust_minimum=TrustTier.PROOF_BACKED)
    assert not q3.matches(entry1)  # REVIEWED doesn't satisfy PROOF_BACKED
    print("RegistryQuery matching: OK")

    # ContractRegistry
    registry = ContractRegistry(registry_id=_new_id("reg"))
    eid = registry.register(entry1)
    assert eid == entry1.entry_id
    results = registry.query(q)
    assert len(results) == 1
    entry2 = ContractEntry(
        entry_id=_new_id("entry"),
        coordinate_key="jugeo.models.OrderModel",
        source="analyze_class",
        contracts=(AnnotationContract(symbol_name="OrderModel", annotation_text="dataclass"),),
        trust_tier=TrustTier.VERIFIED,
        metadata={"created_at": _now_iso()},
    )
    registry.register(entry2)
    snap = registry.snapshot()
    assert len(snap) == 2
    by_coord = registry.get_by_coordinate("jugeo.models.UserModel")
    assert len(by_coord) == 1
    ok_discharge = registry.discharge_entry(entry1.entry_id, "test evidence")
    assert ok_discharge is True
    assert "discharged_evidence" in registry.entries[entry1.entry_id].metadata
    print(f"ContractRegistry (id={registry.registry_id[:20]}..., entries={len(registry.entries)}): OK")

    # build_registry_surface
    surface = build_registry_surface(registry, TrustTier.PROOF_BACKED)
    assert len(surface.public_api) == 2
    assert surface.trust_ceiling == TrustTier.PROOF_BACKED
    jt_surf = surface.to_judgment_tuple()
    assert isinstance(jt_surf, JudgmentTuple)
    # surface with lower ceiling should exclude VERIFIED entry
    surface2 = build_registry_surface(registry, TrustTier.REVIEWED)
    assert len(surface2.public_api) == 1
    print(f"RegistrySurface (id={surface.surface_id[:20]}..., entries={len(surface.public_api)}): OK")

    # RegistrySurface.diff
    diff = surface2.diff(surface)
    assert "added" in diff and "removed" in diff and "changed" in diff
    assert diff["version_delta"] == surface.version - surface2.version
    print(f"RegistrySurface.diff: added={len(diff['added'])}, removed={len(diff['removed'])}")

    # RegistrySurface.expose
    exposed = surface.expose("OrderModel")
    assert exposed is not None and "OrderModel" in exposed.coordinate_key
    print(f"RegistrySurface.expose: found {exposed.coordinate_key!r}")

    # RegistrySurface.elevate
    elevated_surface = surface2.elevate(TrustTier.PROOF_BACKED)
    assert elevated_surface.trust_ceiling == TrustTier.PROOF_BACKED
    assert elevated_surface.version == surface2.version + 1
    print("RegistrySurface.elevate: OK")

    # SurfaceAPI
    api = build_surface_api(surface)
    assert api.has_endpoint("jugeo.models.OrderModel")
    assert api.has_endpoint("jugeo.models.UserModel")
    manifest = api.to_manifest()
    assert "api_id" in manifest and "endpoint_names" in manifest
    print(f"SurfaceAPI (id={api.api_id[:20]}..., endpoints={len(api.endpoint_names)}): OK")

    # SurfaceAPI merge
    api2 = SurfaceAPI(api_id=_new_id("api"), endpoint_names=("new.service",), backing_registry_id="test")
    merged_api = api.merge_endpoints(api2)
    assert merged_api.has_endpoint("new.service")
    print(f"SurfaceAPI.merge_endpoints: total={len(merged_api.endpoint_names)}")

    # ContractRegistryAnalyzer
    import jugeo.python_runtime.generated_contracts.registry_surfaces as _self_mod
    analyzer = ContractRegistryAnalyzer(analyzer_id=_new_id("anal"))
    found = analyzer.analyze_module(_self_mod)
    print(f"ContractRegistryAnalyzer: found {len(found)} contracts in module")
    summary = analyzer.summarize()
    assert "total_findings" in summary
    print(f"  summary: {summary}")

    # ContractRegistryWitness
    witness = ContractRegistryWitness(witness_id=_new_id("wit"))
    jt_w = witness.observe(entry1, {"name": "Alice", "age": 30})
    assert isinstance(jt_w, JudgmentTuple)
    all_jts = witness.witness_registry(registry)
    assert len(all_jts) == 2
    rep = witness.report()
    assert "total_observations" in rep
    print(f"ContractRegistryWitness: {rep}")

    # ContractRegistryCoordinator
    reg2 = ContractRegistry(registry_id=_new_id("reg2"))
    entry3 = ContractEntry(
        entry_id=_new_id("entry"),
        coordinate_key="jugeo.services.PaymentService",
        source="manual",
        contracts=(AnnotationContract(symbol_name="PaymentService", annotation_text="service"),),
        trust_tier=TrustTier.PROPOSAL,
    )
    coordinator = ContractRegistryCoordinator(coordinator_id=_new_id("coord"), registry=reg2)
    coord_surface = coordinator.coordinate([entry3])
    assert coord_surface is not None
    assert len(coord_surface.public_api) >= 1
    assert coordinator.surface is not None
    print(f"ContractRegistryCoordinator: surface={coord_surface.surface_id[:20]}..., entries={len(coord_surface.public_api)}")

    # Registry merge
    merged_reg = registry.merge(reg2)
    assert len(merged_reg.entries) >= 3
    print(f"ContractRegistry.merge: total entries={len(merged_reg.entries)}")

    # Registry summary and all_judgment_tuples
    reg_summary = registry.summary()
    assert "total_entries" in reg_summary and "trust_breakdown" in reg_summary
    all_jts2 = registry.all_judgment_tuples()
    assert len(all_jts2) == 2
    print(f"ContractRegistry.summary: {reg_summary}")

    # Summary table
    print("\n=== Summary Table ===")
    print(f"{'entry_id':<36} {'coordinate_key':<40} {'trust_tier':<20} {'active'}")
    print("-" * 100)
    for e in registry.snapshot():
        print(f"{e.entry_id:<36} {e.coordinate_key:<40} {e.trust_tier.name:<20} {e.is_active}")

    print("\n=== registry_surfaces smoke test PASSED ===")

# ---------------------------------------------------------------------------
# BEGIN LEGACY CONTENT (from previous version of this file)
# The classes below are from theory2.tex §21.3 and are preserved for
# backward compatibility. The new-spec classes above are the canonical API.
# ---------------------------------------------------------------------------


import abc
import dataclasses
import enum
import functools
import inspect
import logging
import threading
import time
import typing
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports with inline stub fallbacks
# copilot: identical fallback block used across all generated_contracts modules
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject, CoordinateKind, CoordinateMorphism, MorphismKind,
        Site, SiteBuilder,
    )
except Exception:
    class CoordinateKind(enum.Enum):
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"
    class MorphismKind(enum.Enum):
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"
    @dataclass(frozen=True, slots=True)
    class CoordinateObject:
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)
    class CoordinateMorphism:
        def __init__(self, source, target, reason=""): self.source=source; self.target=target; self.reason=reason
    class Site: pass
    class SiteBuilder: pass

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance, ProvenanceSource,
    )
except Exception:
    class TrustLevel(enum.IntEnum):
        CONTRADICTED=0; UNVERIFIED=1; ORACLE_PROPOSED=2
        RUNTIME_WITNESSED=3; SOLVER_DISCHARGED=4; VERIFIED_PROOF=5
    class JudgmentStatus(enum.Enum):
        PROPOSED="proposed"; CHALLENGED="challenged"; SETTLED="settled"; OBSTRUCTED="obstructed"
    class PropositionKind(enum.Enum):
        STRUCTURAL="structural"; BEHAVIORAL="behavioral"; RELATIONAL="relational"
        RESOURCE="resource"; SEMANTIC="semantic"
    class EvidenceItemKind(enum.Enum):
        SOLVER_PROOF="solver_proof"; RUNTIME_WITNESS="runtime_witness"
        ORACLE_PROPOSAL="oracle_proposal"; FORMAL_PROOF="formal_proof"
    class ProvenanceSource(enum.Enum):
        SOLVER="solver"; RUNTIME="runtime"; ORACLE="oracle"; HUMAN="human"; COMPOSED="composed"
    @dataclass(frozen=True, slots=True)
    class Proposition:
        kind: Any = None; formula: str = ""; free_variables: tuple[str,...] = ()
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class Carrier:
        name: str = ""; parameters: tuple[str,...] = (); is_dependent: bool = False
        metadata: dict = field(default_factory=dict)
    @dataclass(frozen=True, slots=True)
    class EvidenceItem:
        kind: Any = None; payload: dict = field(default_factory=dict); trust_level: Any = None
        channel: str = ""; timestamp: str = ""; expiry: str = ""; provenance: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:
        items: tuple[Any,...] = ()
    @dataclass(frozen=True, slots=True)
    class ResidualObligation:
        description: str = ""; obligation_id: str = ""; priority: int = 1
        is_discharged: bool = False
        def discharge(self, evidence=""): return replace(self, is_discharged=True)
    @dataclass(frozen=True, slots=True)
    class Obstruction:
        description: str = ""; obstruction_id: str = ""; severity: int = 1
    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:
        level: Any = None; rationale: str = ""
    @dataclass(frozen=True, slots=True)
    class Provenance:
        sources: tuple[Any,...] = (); chain: tuple[str,...] = ()
    @dataclass(frozen=True, slots=True)
    class Judgment:
        coordinate: Any = None; proposition: Any = None; carrier: Any = None
        evidence: Any = None; obligations: tuple = (); obstructions: tuple = ()
        trust: Any = None; provenance: Any = None

try:
    from jugeo.python_runtime.generated_contracts.models import (
        AnnotationContract, ContractRecord, DecoratorTransformer, RegistrySection,
    )
except ImportError:
    @dataclass(frozen=True, slots=True)
    class AnnotationContract:
        symbol_name: str = ""; annotation_text: str = ""; trust_level: Any = None
        is_discharged: bool = False
    @dataclass(frozen=True, slots=True)
    class ContractRecord:
        coordinate_key: str = ""; contracts: tuple = (); is_complete: bool = False
    @dataclass(frozen=True, slots=True)
    class DecoratorTransformer:
        decorator_name: str = ""; source_qualname: str = ""; target_qualname: str = ""
        morphism_kind: str = "REFINEMENT"
    @dataclass(frozen=True, slots=True)
    class RegistrySection:
        registry_name: str = ""; entries: tuple = (); is_covering: bool = False


# ---------------------------------------------------------------------------
# Module-level constants
# copilot: define the common Python type universe used for coverage calculation
# ---------------------------------------------------------------------------

_MODULE_VERSION: str = "0.1.0"
_MODULE_NAME: str = "registry_surfaces"

# The common type universe against which singledispatch coverage is measured
# theory2.tex §21.3.3 — T_common is the type set used as the reference universe
_COMMON_PYTHON_TYPES: tuple[type, ...] = (
    int, str, float, list, dict, tuple, bool, bytes, type(None),
)

# copilot: names that indicate this is not a real dispatcher
_SINGLEDISPATCH_SENTINELS: tuple[str, ...] = ("register", "dispatch", "registry")

# type names excluded from gap analysis (too abstract to be meaningful gaps)
_EXCLUDED_GAP_TYPES: frozenset[str] = frozenset({"object", "type", "NoneType"})

# threshold for "well-covered" registries
_COVERAGE_THRESHOLD: float = 0.5


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str = "rs") -> str:
    """Generate a short unique identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _trust_level_name(level: Any) -> str:
    """Return the name of a TrustLevel as a string, tolerating None."""
    if level is None:
        return "NONE"
    try:
        return level.name
    except AttributeError:
        return str(level)


def _type_name(t: Any) -> str:
    """Return a readable name for a type, handling None and NoneType."""
    if t is type(None) or t is None:
        return "NoneType"
    return getattr(t, "__qualname__", getattr(t, "__name__", repr(t)))


def _is_singledispatch(obj: Any) -> bool:
    """Return True when *obj* is a functools.singledispatch wrapper.

    Checks for the canonical attributes added by functools.singledispatch:
    .registry, .dispatch, and .register.
    """
    return (
        callable(obj)
        and hasattr(obj, "registry")
        and hasattr(obj, "dispatch")
        and hasattr(obj, "register")
    )


def _is_abc_class(obj: Any) -> bool:
    """Return True when *obj* is a class with ABCMeta metaclass."""
    return isinstance(obj, type) and isinstance(obj, abc.ABCMeta)


def _is_dataclass_type(obj: Any) -> bool:
    """Return True when *obj* is a dataclass type (not an instance)."""
    return isinstance(obj, type) and dataclasses.is_dataclass(obj)


def _safe_subclasses(cls: type) -> list[type]:
    """Safely retrieve direct subclasses of *cls*, returning [] on failure."""
    try:
        return cls.__subclasses__()
    except Exception:
        return []


def _coverage_fraction(covered: int, total: int) -> float:
    """Compute coverage fraction, returning 0.0 for empty universe."""
    if total == 0:
        return 0.0
    return min(covered / total, 1.0)


# ---------------------------------------------------------------------------
# RegistryKind, RegistryEntry, RegistrySurfaceRecord
# ---------------------------------------------------------------------------

class RegistryKind(enum.Enum):
    """The kind of registry / constraint surface.

    theory2.tex §21.3.0 — each kind corresponds to a different category of
    covering family in the Python runtime Grothendieck topology.
    """
    SINGLE_DISPATCH = "single_dispatch"
    ABC_ABSTRACT    = "abc_abstract"
    DATACLASS_FIELDS = "dataclass_fields"
    PLUGIN          = "plugin"
    CUSTOM          = "custom"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """A single entry in a registry, binding a key type to an implementation.

    theory2.tex §21.3.5 — an entry corresponds to a covering morphism
        φ_i: U_key_i → U_base
    in the Grothendieck topology of the runtime site.

    Fields
    ------
    key_type : str
        The type (or type name) that this entry covers.
    implementation_qualname : str
        Qualified name of the registered implementation.
    trust_level : TrustLevel
        Trust level assigned to this entry.
    covering_morphism_id : str
        Unique identifier for the covering morphism (for audit trails).
    metadata : dict
        Arbitrary metadata.
    """

    key_type: str = ""
    implementation_qualname: str = ""
    trust_level: Any = None
    covering_morphism_id: str = ""
    metadata: dict = field(default_factory=dict)

    def summary(self) -> str:
        """One-line summary of this registry entry."""
        trust_str = _trust_level_name(self.trust_level)
        return (
            f"RegistryEntry({self.key_type!r} → {self.implementation_qualname!r}"
            f" trust={trust_str})"
        )


@dataclass(frozen=True, slots=True)
class RegistrySurfaceRecord:
    """A constraint surface extracted from a Python registry object.

    theory2.tex §21.3.6 — the surface record encodes the full covering family
        { φ_i: U_i → U_base }_{i ∈ I}
    together with the coverage fraction and any residual gap obligations.

    Fields
    ------
    kind : RegistryKind
        The kind of registry (singledispatch, ABC, dataclass, …).
    registry_name : str
        Qualified name of the registry object.
    entries : tuple[RegistryEntry, ...]
        All registered (key, impl) pairs found in the registry.
    has_base_case : bool
        True when a catch-all / default implementation exists.
    coverage_fraction : float
        Fraction of the reference type universe that is covered.
    obligations : tuple[ResidualObligation, ...]
        Obligations for uncovered types / unimplemented abstract members.
    metadata : dict
        Arbitrary metadata.
    """

    kind: Any = None
    registry_name: str = ""
    entries: tuple = ()
    has_base_case: bool = False
    coverage_fraction: float = 0.0
    obligations: tuple = ()
    metadata: dict = field(default_factory=dict)

    def gap_count(self) -> int:
        """Return the number of residual gap obligations (undischarged)."""
        return sum(
            1 for ob in self.obligations
            if not getattr(ob, "is_discharged", False)
        )

    def is_fully_covered(self) -> bool:
        """Return True when coverage_fraction == 1.0 and no undischarged gaps."""
        return self.coverage_fraction >= 1.0 and self.gap_count() == 0

    def entry_count(self) -> int:
        """Return the total number of entries."""
        return len(self.entries)

    def summary(self) -> str:
        """Return a human-readable one-line summary of this surface record."""
        kind_str = self.kind.value if self.kind else "unknown"
        covered_tag = "FULL" if self.is_fully_covered() else f"PARTIAL({self.coverage_fraction:.0%})"
        base_tag = "+base" if self.has_base_case else ""
        return (
            f"RegistrySurfaceRecord({self.registry_name!r} [{kind_str}]"
            f" entries={self.entry_count()} {covered_tag}{base_tag}"
            f" gaps={self.gap_count()})"
        )

    def to_dict(self) -> dict:
        """Serialize this surface record to a plain dictionary."""
        return {
            "kind": self.kind.value if self.kind else None,
            "registry_name": self.registry_name,
            "entry_count": self.entry_count(),
            "entries": [
                {"key_type": e.key_type, "impl": e.implementation_qualname}
                for e in self.entries
            ],
            "has_base_case": self.has_base_case,
            "coverage_fraction": self.coverage_fraction,
            "gap_count": self.gap_count(),
            "obligations": [
                getattr(ob, "description", str(ob)) for ob in self.obligations
            ],
        }


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Record of a single observed dispatch event.

    theory2.tex §21.3.7 — a dispatch witness is a section of the evidence
    sheaf restricted to the dispatch event U_dispatch ⊆ U_registry.
    """

    dispatch_id: str = ""
    registry_name: str = ""
    dispatch_type: str = ""
    matched_impl: str = ""
    was_miss: bool = False
    error_type: str = ""
    timestamp: str = ""

    def summary(self) -> str:
        """One-line summary of this dispatch witness record."""
        hit_miss = "MISS" if self.was_miss else "HIT"
        return (
            f"WitnessRecord(registry={self.registry_name!r}"
            f" type={self.dispatch_type!r} impl={self.matched_impl!r}"
            f" {hit_miss} @{self.timestamp})"
        )


# ---------------------------------------------------------------------------
# SingleDispatchSurfaceAnalyzer
# ---------------------------------------------------------------------------

class SingleDispatchSurfaceAnalyzer:
    """Analyze a functools.singledispatch function as a registry surface.

    theory2.tex §21.3.3 — a singledispatch registry is a discrete covering
    family where each registered type forms an independent covering morphism.
    The base case (registered for `object`) acts as a universal cover.
    """

    def can_analyze(self, obj: Any) -> bool:
        """Return True when *obj* is a singledispatch wrapper."""
        return _is_singledispatch(obj)

    def analyze(self, func: Any) -> RegistrySurfaceRecord:
        """Analyze singledispatch *func* and return a RegistrySurfaceRecord.

        Reads func.registry to discover all (type → implementation) pairs.
        Computes coverage against _COMMON_PYTHON_TYPES.
        Generates ResidualObligation for each uncovered common type.

        Parameters
        ----------
        func : Any
            A functools.singledispatch wrapper.

        Returns
        -------
        RegistrySurfaceRecord
        """
        qualname = getattr(func, "__qualname__", getattr(func.__wrapped__, "__qualname__", repr(func)))
        registry_dict: dict = getattr(func, "registry", {})

        entries = tuple(
            self._build_entry(_type_name(k), v)
            for k, v in registry_dict.items()
        )

        # check for base-case registration (object)
        has_base = object in registry_dict
        covered_types = set(registry_dict.keys())
        common_types = self._common_types()
        n_covered = sum(1 for t in common_types if t in covered_types or has_base)
        cov_frac = _coverage_fraction(n_covered, len(common_types))

        gaps = self._find_gaps(registry_dict)

        metadata = {
            "registered_types": [_type_name(k) for k in registry_dict],
            "wrapped_qualname": getattr(getattr(func, "__wrapped__", None), "__qualname__", ""),
        }

        logger.debug(
            "SingleDispatchSurfaceAnalyzer.analyze: %s entries=%d coverage=%.2f",
            qualname, len(entries), cov_frac,
        )
        return RegistrySurfaceRecord(
            kind=RegistryKind.SINGLE_DISPATCH,
            registry_name=qualname,
            entries=entries,
            has_base_case=has_base,
            coverage_fraction=cov_frac,
            obligations=tuple(gaps),
            metadata=metadata,
        )

    def _common_types(self) -> list[type]:
        """Return the reference type universe for coverage analysis.

        theory2.tex §21.3.3 — T_common contains the most frequently dispatched
        types in real Python code.
        """
        return list(_COMMON_PYTHON_TYPES)

    def _build_entry(self, key_type_name: str, impl: Any) -> RegistryEntry:
        """Build a RegistryEntry from a (type_name, impl) pair."""
        impl_qualname = getattr(impl, "__qualname__", repr(impl))
        return RegistryEntry(
            key_type=key_type_name,
            implementation_qualname=impl_qualname,
            trust_level=TrustLevel.RUNTIME_WITNESSED,
            covering_morphism_id=_new_id("morph"),
            metadata={},
        )

    def _find_gaps(self, registry_dict: dict) -> list[ResidualObligation]:
        """Find uncovered types in the common type universe.

        A type is covered if it or one of its bases is in registry_dict, or
        if `object` is registered (universal base case).

        Returns a ResidualObligation for each uncovered type.
        """
        gaps: list[ResidualObligation] = []
        if object in registry_dict:
            return gaps  # universal base case covers everything

        covered = set(registry_dict.keys())
        for t in self._common_types():
            t_name = _type_name(t)
            if t_name in _EXCLUDED_GAP_TYPES:
                continue
            # check if t or any of its MRO bases is registered
            mro_covered = any(base in covered for base in getattr(t, "__mro__", [t]))
            if not mro_covered:
                gaps.append(ResidualObligation(
                    description=f"singledispatch registry missing case for {t_name!r}",
                    obligation_id=_new_id("gap"),
                    priority=2,
                    is_discharged=False,
                ))
                logger.debug("_find_gaps: gap for type %s", t_name)
        return gaps


# ---------------------------------------------------------------------------
# ABCSurfaceAnalyzer
# ---------------------------------------------------------------------------

class ABCSurfaceAnalyzer:
    """Analyze an abstract base class as a registry surface.

    theory2.tex §21.3.2 — an ABC defines abstract methods that form the
    interface obligation φ_abstract.  Each concrete subclass must provide
    covering morphisms for all abstract methods.

    The surface record encodes which abstract methods exist and which
    subclasses provide full implementations.
    """

    def can_analyze(self, obj: Any) -> bool:
        """Return True when *obj* is an ABCMeta class."""
        return _is_abc_class(obj)

    def analyze(self, cls: type) -> RegistrySurfaceRecord:
        """Analyze ABC *cls* and return a RegistrySurfaceRecord.

        Finds all abstract methods on *cls* and all concrete subclasses.
        For each subclass, checks whether all abstract methods are implemented.
        Computes coverage_fraction as the fraction of subclasses that fully
        implement the interface.

        Parameters
        ----------
        cls : type
            An ABCMeta class.

        Returns
        -------
        RegistrySurfaceRecord
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        abstract_methods = sorted(getattr(cls, "__abstractmethods__", set()))
        concrete_subs = self._get_concrete_subclasses(cls)

        # each abstract method becomes an entry in the surface
        entries: list[RegistryEntry] = []
        for method_name in abstract_methods:
            entries.append(RegistryEntry(
                key_type=method_name,
                implementation_qualname=f"{qualname}.{method_name} (abstract)",
                trust_level=TrustLevel.ORACLE_PROPOSED,
                covering_morphism_id=_new_id("morph"),
                metadata={"abstract": True},
            ))

        # check each concrete subclass for full implementation
        fully_covered_subs = 0
        obligations: list[ResidualObligation] = []
        for sub in concrete_subs:
            missing = self._check_subclass_impl(sub, abstract_methods)
            if missing:
                for mname in missing:
                    obligations.append(ResidualObligation(
                        description=(
                            f"Subclass {sub.__qualname__!r} of {qualname!r} "
                            f"is missing implementation of {mname!r}"
                        ),
                        obligation_id=_new_id("gap"),
                        priority=3,
                        is_discharged=False,
                    ))
            else:
                fully_covered_subs += 1
                # add an entry for this subclass's implementation
                for method_name in abstract_methods:
                    impl_fn = getattr(sub, method_name, None)
                    impl_qualname = getattr(impl_fn, "__qualname__", f"{sub.__qualname__}.{method_name}")
                    entries.append(RegistryEntry(
                        key_type=f"{sub.__qualname__}::{method_name}",
                        implementation_qualname=impl_qualname,
                        trust_level=TrustLevel.RUNTIME_WITNESSED,
                        covering_morphism_id=_new_id("morph"),
                        metadata={"subclass": sub.__qualname__},
                    ))

        total_subs = len(concrete_subs)
        cov_frac = _coverage_fraction(fully_covered_subs, max(total_subs, 1))

        metadata = {
            "abstract_methods": abstract_methods,
            "concrete_subclasses": [s.__qualname__ for s in concrete_subs],
            "fully_covered_subs": fully_covered_subs,
        }

        logger.debug(
            "ABCSurfaceAnalyzer.analyze: %s abstract=%d subs=%d coverage=%.2f",
            qualname, len(abstract_methods), total_subs, cov_frac,
        )
        return RegistrySurfaceRecord(
            kind=RegistryKind.ABC_ABSTRACT,
            registry_name=qualname,
            entries=tuple(entries),
            has_base_case=len(abstract_methods) == 0,
            coverage_fraction=cov_frac,
            obligations=tuple(obligations),
            metadata=metadata,
        )

    def _get_concrete_subclasses(self, cls: type) -> list[type]:
        """Recursively find all non-abstract subclasses of *cls*.

        A subclass is concrete when its __abstractmethods__ set is empty.
        """
        concrete: list[type] = []
        visited: set[int] = set()

        def _recurse(c: type) -> None:
            if id(c) in visited:
                return
            visited.add(id(c))
            for sub in _safe_subclasses(c):
                if not getattr(sub, "__abstractmethods__", None):
                    concrete.append(sub)
                _recurse(sub)

        _recurse(cls)
        return concrete

    def _check_subclass_impl(self, sub: type, abstract_methods: list[str]) -> list[str]:
        """Return names of abstract methods not implemented by *sub*.

        A method is implemented when it exists on *sub* and is not abstract
        itself (i.e., not in sub.__abstractmethods__).
        """
        sub_abstract: frozenset = getattr(sub, "__abstractmethods__", frozenset())
        missing: list[str] = []
        for mname in abstract_methods:
            if mname in sub_abstract or not hasattr(sub, mname):
                missing.append(mname)
            else:
                # check that the method is actually overridden (not inherited as abstract)
                member = getattr(sub, mname, None)
                if member is None or getattr(member, "__isabstractmethod__", False):
                    missing.append(mname)
        return missing


# ---------------------------------------------------------------------------
# DataclassFieldSurfaceAnalyzer
# ---------------------------------------------------------------------------

class DataclassFieldSurfaceAnalyzer:
    """Analyze a dataclass's __dataclass_fields__ dict as a registry surface.

    theory2.tex §21.3.4 — the field dict maps field names to Field descriptors.
    Required fields (no default) are uncovered entries that generate obligations.
    The coverage fraction is the ratio of optional (defaulted) fields to total.
    """

    def can_analyze(self, obj: Any) -> bool:
        """Return True when *obj* is a dataclass type."""
        return _is_dataclass_type(obj)

    def analyze(self, cls: type) -> RegistrySurfaceRecord:
        """Analyze dataclass *cls* and return a RegistrySurfaceRecord.

        Reads __dataclass_fields__ for all field descriptors.  Required fields
        (those with no default) produce gap obligations.

        Parameters
        ----------
        cls : type
            A dataclass type.

        Returns
        -------
        RegistrySurfaceRecord
        """
        qualname = getattr(cls, "__qualname__", repr(cls))
        try:
            fields_raw = dataclasses.fields(cls)
        except TypeError:
            fields_raw = ()

        entries: list[RegistryEntry] = []
        obligations: list[ResidualObligation] = []
        n_optional = 0

        for f in fields_raw:
            has_def = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            )
            if has_def:
                n_optional += 1
            trust = TrustLevel.RUNTIME_WITNESSED if has_def else TrustLevel.ORACLE_PROPOSED
            entries.append(RegistryEntry(
                key_type=f.name,
                implementation_qualname=str(f.type),
                trust_level=trust,
                covering_morphism_id=_new_id("morph"),
                metadata={"has_default": has_def, "required": not has_def},
            ))
            if not has_def:
                obligations.append(ResidualObligation(
                    description=(
                        f"Dataclass {qualname!r} field {f.name!r} "
                        f"is required (no default value)"
                    ),
                    obligation_id=_new_id("gap"),
                    priority=2,
                    is_discharged=False,
                ))

        total = len(fields_raw)
        cov_frac = _coverage_fraction(n_optional, max(total, 1))
        params = getattr(cls, "__dataclass_params__", None)
        is_frozen = getattr(params, "frozen", False) if params else False

        metadata = {
            "frozen": is_frozen,
            "total_fields": total,
            "optional_fields": n_optional,
            "required_fields": total - n_optional,
        }

        logger.debug(
            "DataclassFieldSurfaceAnalyzer.analyze: %s fields=%d optional=%d",
            qualname, total, n_optional,
        )
        return RegistrySurfaceRecord(
            kind=RegistryKind.DATACLASS_FIELDS,
            registry_name=qualname,
            entries=tuple(entries),
            has_base_case=n_optional == total,
            coverage_fraction=cov_frac,
            obligations=tuple(obligations),
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# RegistrySurfacesAnalyzer
# ---------------------------------------------------------------------------

class RegistrySurfacesAnalyzer:
    """High-level analyzer that maps Python registry objects to surface records.

    theory2.tex §21.3.8 — the analyzer implements the functor
        S: RegistryObjects → SurfaceRecords(F_registry)
    by composing sub-analyzer dispatch with obligation collection and judgment
    construction.

    Usage
    -----
    >>> analyzer = RegistrySurfacesAnalyzer()
    >>> record = analyzer.analyze(my_singledispatch_func)
    >>> judgments = analyzer.emit_judgments([record])
    """

    def __init__(self) -> None:
        # copilot: try each sub-analyzer in priority order
        self._sub_analyzers: list = [
            SingleDispatchSurfaceAnalyzer(),
            ABCSurfaceAnalyzer(),
            DataclassFieldSurfaceAnalyzer(),
        ]
        self._analyzed_count: int = 0
        self._judgment_count: int = 0

    def analyze(self, obj: Any) -> Optional[RegistrySurfaceRecord]:
        """Analyze *obj* and return a RegistrySurfaceRecord, or None.

        Tries each sub-analyzer in order; returns the first successful result.
        Returns None when no sub-analyzer can handle *obj*.

        Parameters
        ----------
        obj : Any
            The object to analyze (dispatch function, ABC, or dataclass).

        Returns
        -------
        RegistrySurfaceRecord | None
        """
        self._analyzed_count += 1
        for analyzer in self._sub_analyzers:
            if analyzer.can_analyze(obj):
                try:
                    record = analyzer.analyze(obj)
                    logger.debug(
                        "RegistrySurfacesAnalyzer.analyze: %s analyzed by %s",
                        getattr(obj, "__qualname__", obj),
                        type(analyzer).__name__,
                    )
                    return record
                except Exception as exc:
                    logger.warning(
                        "RegistrySurfacesAnalyzer.analyze: %s failed on %s: %s",
                        type(analyzer).__name__, obj, exc,
                    )
        logger.debug(
            "RegistrySurfacesAnalyzer.analyze: no sub-analyzer matched for %s",
            getattr(obj, "__qualname__", repr(obj)),
        )
        return None

    def analyze_module(self, module: Any) -> list[RegistrySurfaceRecord]:
        """Find all registries in *module* and analyze them.

        Scans the module namespace for:
        - functools.singledispatch wrappers
        - ABCMeta subclasses
        - dataclass types

        Parameters
        ----------
        module : Any
            A Python module object.

        Returns
        -------
        list[RegistrySurfaceRecord]
            One record per registry found in the module.
        """
        records: list[RegistrySurfaceRecord] = []
        for name in dir(module):
            if name.startswith("_"):
                continue
            try:
                obj = getattr(module, name)
            except AttributeError:
                continue
            record = self.analyze(obj)
            if record is not None:
                records.append(record)
        logger.debug("analyze_module: found %d registry surfaces in %s", len(records), module)
        return records

    def find_gaps(self, records: list[RegistrySurfaceRecord]) -> list[ResidualObligation]:
        """Collect all undischarged gap obligations from a list of records.

        theory2.tex §21.3.9 — gaps are types or abstract methods for which
        no covering morphism has been registered.

        Parameters
        ----------
        records : list[RegistrySurfaceRecord]
            Surface records to scan.

        Returns
        -------
        list[ResidualObligation]
            All undischarged obligations.
        """
        gaps: list[ResidualObligation] = []
        for rec in records:
            for ob in rec.obligations:
                if not getattr(ob, "is_discharged", False):
                    gaps.append(ob)
        return gaps

    def emit_judgments(self, records: list[RegistrySurfaceRecord]) -> list[Judgment]:
        """Convert surface records into JuGeo Judgment objects.

        theory2.tex §21.3.10 — each registry surface becomes a Judgment with:
        - A structural Proposition asserting coverage
        - A Carrier naming the registry
        - An EvidenceBundle derived from the entry count and coverage fraction
        - Obligations for any coverage gaps

        Parameters
        ----------
        records : list[RegistrySurfaceRecord]
            Surface records to convert.

        Returns
        -------
        list[Judgment]
            One Judgment per surface record.
        """
        judgments: list[Judgment] = []
        for rec in records:
            kind_str = rec.kind.value if rec.kind else "unknown"
            proposition = Proposition(
                kind=PropositionKind.STRUCTURAL,
                formula=(
                    f"registry_covered({rec.registry_name!r}, {kind_str!r}) "
                    f": coverage={rec.coverage_fraction:.2f}"
                ),
                free_variables=(rec.registry_name,),
                metadata={
                    "coverage_fraction": rec.coverage_fraction,
                    "entry_count": rec.entry_count(),
                    "gap_count": rec.gap_count(),
                },
            )
            carrier = Carrier(
                name=rec.registry_name,
                parameters=(kind_str,),
                is_dependent=False,
                metadata={"fully_covered": rec.is_fully_covered()},
            )
            evidence_item = EvidenceItem(
                kind=EvidenceItemKind.RUNTIME_WITNESS,
                payload={
                    "entries": rec.entry_count(),
                    "has_base_case": rec.has_base_case,
                    "coverage_fraction": rec.coverage_fraction,
                },
                trust_level=(
                    TrustLevel.RUNTIME_WITNESSED if rec.entry_count() > 0
                    else TrustLevel.ORACLE_PROPOSED
                ),
                channel="registry_surfaces_analyzer",
                timestamp=_now_iso(),
                expiry="",
                provenance=("RegistrySurfacesAnalyzer",),
            )
            bundle = EvidenceBundle(items=(evidence_item,))
            trust_ann = TrustAnnotation(
                level=TrustLevel.RUNTIME_WITNESSED if rec.is_fully_covered() else TrustLevel.ORACLE_PROPOSED,
                rationale=(
                    f"coverage fraction {rec.coverage_fraction:.2%} for registry {rec.registry_name!r}"
                ),
            )
            prov = Provenance(
                sources=(ProvenanceSource.RUNTIME,),
                chain=("RegistrySurfacesAnalyzer",),
            )
            judgment = Judgment(
                coordinate=None,
                proposition=proposition,
                carrier=carrier,
                evidence=bundle,
                obligations=rec.obligations,
                obstructions=(),
                trust=trust_ann,
                provenance=prov,
            )
            judgments.append(judgment)
            self._judgment_count += 1

        logger.debug("emit_judgments: produced %d judgments", len(judgments))
        return judgments

    def summary(self, records: list[RegistrySurfaceRecord]) -> str:
        """Return a multi-line summary of a list of surface records."""
        total = len(records)
        fully_covered = sum(1 for r in records if r.is_fully_covered())
        total_gaps = sum(r.gap_count() for r in records)
        avg_coverage = (
            sum(r.coverage_fraction for r in records) / total if total else 0.0
        )
        lines = [
            f"RegistrySurfacesAnalyzer Summary",
            f"  Total surfaces analyzed : {self._analyzed_count}",
            f"  Surfaces in report      : {total}",
            f"  Fully covered           : {fully_covered}",
            f"  Total gaps              : {total_gaps}",
            f"  Average coverage        : {avg_coverage:.2%}",
            f"  Judgments emitted       : {self._judgment_count}",
        ]
        for rec in records:
            lines.append(f"    {rec.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# RegistrySurfacesWitness
# ---------------------------------------------------------------------------

class RegistrySurfacesWitness:
    """Runtime witness that observes dispatch events and records misses.

    theory2.tex §21.3.11 — the dispatch witness applies the restriction
    morphism at the call level:
        ρ_{U_dispatch → U_registry}: dispatch_event → registry_section
    A *miss* occurs when the dispatched type is not covered by any registered
    implementation, falling back to the default (if any) or raising TypeError.
    """

    def __init__(self) -> None:
        # copilot: accumulate dispatch records for gap analysis
        self._records: list[WitnessRecord] = []
        self._hit_count: int = 0
        self._miss_count: int = 0
        self._lock = threading.Lock()

    def observe_dispatch(
        self,
        registry_name: str,
        dispatch_type: str,
        matched_impl: str,
        was_miss: bool,
        error_type: str = "",
    ) -> WitnessRecord:
        """Record a dispatch event.

        theory2.tex §21.3.12 — each dispatch event is a local restriction
        of the registry surface section to a single type neighbourhood.

        Parameters
        ----------
        registry_name : str
            Qualified name of the singledispatch function.
        dispatch_type : str
            String representation of the dispatched type.
        matched_impl : str
            Qualified name of the matched implementation.
        was_miss : bool
            True when no specific implementation was found (fell to default or raised).
        error_type : str
            Type of error raised, if any.

        Returns
        -------
        WitnessRecord
        """
        record = WitnessRecord(
            dispatch_id=_new_id("disp"),
            registry_name=registry_name,
            dispatch_type=dispatch_type,
            matched_impl=matched_impl,
            was_miss=was_miss,
            error_type=error_type,
            timestamp=_now_iso(),
        )
        with self._lock:
            self._records.append(record)
            if was_miss:
                self._miss_count += 1
            else:
                self._hit_count += 1

        logger.debug(
            "observe_dispatch: %s type=%s %s",
            registry_name, dispatch_type, "MISS" if was_miss else "HIT",
        )
        return record

    def wrap_dispatch(self, func: Any) -> Any:
        """Wrap a singledispatch *func* to record all dispatch calls.

        Returns a new callable that, when called, resolves the dispatch
        implementation and records the event before delegating.

        Parameters
        ----------
        func : Any
            A functools.singledispatch wrapper.

        Returns
        -------
        callable
            The wrapped dispatcher.
        """
        if not _is_singledispatch(func):
            logger.warning("wrap_dispatch: %s is not a singledispatch function", func)
            return func

        witness = self
        registry_name = getattr(func, "__qualname__", repr(func))

        @functools.wraps(func)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            dispatch_type = _type_name(type(args[0])) if args else "unknown"
            try:
                # resolve which implementation will be used
                impl = func.dispatch(type(args[0]) if args else object)
                impl_qualname = getattr(impl, "__qualname__", repr(impl))
                was_miss = impl is func.dispatch(object)  # same as base case = miss
                witness.observe_dispatch(registry_name, dispatch_type, impl_qualname, was_miss)
            except Exception as exc:
                witness.observe_dispatch(
                    registry_name, dispatch_type, "<error>",
                    was_miss=True, error_type=type(exc).__name__,
                )
            return func(*args, **kwargs)

        # preserve singledispatch interface attributes
        _wrapped.registry = func.registry  # type: ignore[attr-defined]
        _wrapped.dispatch = func.dispatch  # type: ignore[attr-defined]
        _wrapped.register = func.register  # type: ignore[attr-defined]
        logger.debug("wrap_dispatch: wrapped %s", registry_name)
        return _wrapped

    def get_misses(self) -> list[WitnessRecord]:
        """Return all records where was_miss=True."""
        with self._lock:
            return [r for r in self._records if r.was_miss]

    def get_hits(self) -> list[WitnessRecord]:
        """Return all records where was_miss=False."""
        with self._lock:
            return [r for r in self._records if not r.was_miss]

    def get_all(self) -> list[WitnessRecord]:
        """Return a copy of all dispatch witness records."""
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        """Clear all records and reset counters."""
        with self._lock:
            self._records.clear()
            self._hit_count = 0
            self._miss_count = 0

    def summary(self) -> str:
        """Return a multi-line summary of witness dispatch activity."""
        with self._lock:
            total = len(self._records)
            return (
                f"RegistrySurfacesWitness Summary\n"
                f"  Total dispatches : {total}\n"
                f"  Hits             : {self._hit_count}\n"
                f"  Misses           : {self._miss_count}\n"
            )


# ---------------------------------------------------------------------------
# RegistrySurfacesCoordinator
# ---------------------------------------------------------------------------

class RegistrySurfacesCoordinator:
    """Top-level coordinator for registry surface analysis.

    theory2.tex §21.3.13 — the coordinator implements the full pipeline:
        RegistryObject → SurfaceRecord → Gaps → Judgments → WitnessWrapping

    Thread-safe; can be shared across threads.
    """

    def __init__(self) -> None:
        # copilot: create sub-components once and reuse
        self._analyzer = RegistrySurfacesAnalyzer()
        self._witness = RegistrySurfacesWitness()
        self._lock = threading.Lock()
        self._coordinator_id = _new_id("coord")
        self._audited_objects: list[str] = []
        self._surface_records: list[RegistrySurfaceRecord] = []

    def coordinate(self, obj: Any) -> CoordinateObject:
        """Build a CoordinateObject for *obj* in the Python runtime site.

        theory2.tex §21.3.14 — the coordinate situates the registry object
        in the site topology.
        """
        qualname = getattr(obj, "__qualname__", repr(obj))
        module_name = getattr(obj, "__module__", "<unknown>")
        components = tuple(
            part for part in f"{module_name}.{qualname}".split(".") if part
        )
        kind = CoordinateKind.FUNCTION if callable(obj) else CoordinateKind.INTERFACE
        return CoordinateObject(
            components=components,
            kind=kind,
            support_labels=frozenset({module_name}),
            metadata={"coordinator_id": self._coordinator_id},
        )

    def audit_module(self, module: Any) -> dict:
        """Analyze all registries in *module* and return an audit report.

        Thread-safe. Returns a dict with keys:
        - registries     : list[RegistrySurfaceRecord]
        - gaps           : list[ResidualObligation]
        - judgments      : list[Judgment]
        - witness_summary: str
        - summary        : str

        Parameters
        ----------
        module : Any
            A Python module object.

        Returns
        -------
        dict
        """
        with self._lock:
            module_name = getattr(module, "__name__", repr(module))
            logger.info("audit_module: starting on %s", module_name)

            records = self._analyzer.analyze_module(module)
            self._surface_records.extend(records)

            gaps = self._analyzer.find_gaps(records)
            judgments = self._analyzer.emit_judgments(records)

            result = {
                "registries": records,
                "gaps": gaps,
                "judgments": judgments,
                "witness_summary": self._witness.summary(),
                "summary": self._analyzer.summary(records),
            }
            logger.info(
                "audit_module: done on %s — surfaces=%d gaps=%d judgments=%d",
                module_name, len(records), len(gaps), len(judgments),
            )
            return result

    def audit_object(self, obj: Any) -> dict:
        """Analyze a single registry object and return an audit report.

        Parameters
        ----------
        obj : Any
            A singledispatch function, ABC class, or dataclass to audit.

        Returns
        -------
        dict
            Keys: record, gaps, judgments, coordinate, summary.
        """
        with self._lock:
            qualname = getattr(obj, "__qualname__", repr(obj))
            logger.info("audit_object: starting on %s", qualname)
            self._audited_objects.append(qualname)

            record = self._analyzer.analyze(obj)
            if record is None:
                return {
                    "record": None,
                    "gaps": [],
                    "judgments": [],
                    "coordinate": self.coordinate(obj),
                    "summary": f"No registry surface found for {qualname!r}",
                }

            self._surface_records.append(record)
            gaps = self._analyzer.find_gaps([record])
            judgments = self._analyzer.emit_judgments([record])
            coord = self.coordinate(obj)

            result = {
                "record": record,
                "gaps": gaps,
                "judgments": judgments,
                "coordinate": coord,
                "summary": record.summary(),
            }
            logger.info(
                "audit_object: done on %s — entries=%d gaps=%d coverage=%.2f",
                qualname, record.entry_count(), len(gaps), record.coverage_fraction,
            )
            return result

    def emit_judgments(self) -> list[Judgment]:
        """Emit judgments for all surface records accumulated so far."""
        with self._lock:
            return self._analyzer.emit_judgments(list(self._surface_records))

    def install_witness(self, func: Any) -> Any:
        """Wrap *func* (a singledispatch function) with the witness."""
        return self._witness.wrap_dispatch(func)

    def report(self) -> str:
        """Return a comprehensive multi-line coordinator report."""
        with self._lock:
            lines = [
                f"RegistrySurfacesCoordinator Report",
                f"  Coordinator ID    : {self._coordinator_id}",
                f"  Audited objects   : {len(self._audited_objects)}",
                f"  Surface records   : {len(self._surface_records)}",
                "",
                "  Audited symbols:",
            ]
            for sym in self._audited_objects:
                lines.append(f"    - {sym}")
            lines.append("")
            lines.append(self._analyzer.summary(list(self._surface_records)))
            lines.append("")
            lines.append(self._witness.summary())
            return "\n".join(lines)

    def get_witness(self) -> RegistrySurfacesWitness:
        """Return the internal witness for external inspection."""
        return self._witness

    def get_analyzer(self) -> RegistrySurfacesAnalyzer:
        """Return the internal analyzer for external inspection."""
        return self._analyzer


# ---------------------------------------------------------------------------
# Additional helpers and utilities
# ---------------------------------------------------------------------------

def build_surface_summary_table(records: list[RegistrySurfaceRecord]) -> str:
    """Build a formatted ASCII table summarizing a list of RegistrySurfaceRecord.

    Each row contains registry name, kind, entry count, coverage, and gap count.
    """
    if not records:
        return "(no surface records)"

    col_widths = [40, 18, 8, 10, 6]
    headers = ["Registry", "Kind", "Entries", "Coverage", "Gaps"]

    def row(cols: list[str]) -> str:
        padded = [c[:col_widths[i]].ljust(col_widths[i]) for i, c in enumerate(cols)]
        return "| " + " | ".join(padded) + " |"

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    lines = [sep, row(headers), sep]
    for rec in records:
        kind_str = rec.kind.value if rec.kind else "unknown"
        cov_str = f"{rec.coverage_fraction:.0%}"
        gap_str = str(rec.gap_count())
        lines.append(row([
            rec.registry_name,
            kind_str,
            str(rec.entry_count()),
            cov_str,
            gap_str,
        ]))
    lines.append(sep)
    return "\n".join(lines)


def compute_registry_coverage_stats(records: list[RegistrySurfaceRecord]) -> dict:
    """Compute aggregate coverage statistics over a list of surface records.

    Returns: total, fully_covered, partial, avg_coverage, total_gaps, by_kind.
    """
    total = len(records)
    fully_covered = sum(1 for r in records if r.is_fully_covered())
    partial = total - fully_covered
    avg_cov = sum(r.coverage_fraction for r in records) / total if total else 0.0
    total_gaps = sum(r.gap_count() for r in records)
    by_kind: dict = {}
    for r in records:
        kind_str = r.kind.value if r.kind else "unknown"
        by_kind[kind_str] = by_kind.get(kind_str, 0) + 1
    return {
        "total": total,
        "fully_covered": fully_covered,
        "partial": partial,
        "avg_coverage": avg_cov,
        "total_gaps": total_gaps,
        "by_kind": by_kind,
    }


def discharge_gap_if_default_exists(
    surface: RegistrySurfaceRecord,
) -> RegistrySurfaceRecord:
    """Discharge all gap obligations for a surface that has a base case.

    If the surface has has_base_case=True (a default / catch-all), all gap
    obligations are discharged because the default covers the remaining types.

    theory2.tex §21.3.15 — a universal base case morphism (object → U_base)
    discharges all gap obligations by providing a covering for every type.
    """
    if not surface.has_base_case:
        return surface
    discharged_obs = tuple(
        ob.discharge("default_base_case_exists") if hasattr(ob, "discharge") else ob
        for ob in surface.obligations
    )
    return replace(surface, obligations=discharged_obs, coverage_fraction=1.0)


def merge_surface_records(
    records_a: list[RegistrySurfaceRecord],
    records_b: list[RegistrySurfaceRecord],
) -> list[RegistrySurfaceRecord]:
    """Merge two lists of surface records, de-duplicating by registry_name.

    When both lists contain a record for the same registry, the one with the
    higher coverage fraction is kept (monotone merge).
    """
    merged: dict[str, RegistrySurfaceRecord] = {}
    for rec in records_a:
        merged[rec.registry_name] = rec
    for rec in records_b:
        existing = merged.get(rec.registry_name)
        if existing is None:
            merged[rec.registry_name] = rec
        elif rec.coverage_fraction > existing.coverage_fraction:
            merged[rec.registry_name] = rec
    return list(merged.values())


def gap_obligations_to_obstructions(
    obligations: list[ResidualObligation],
) -> list[Obstruction]:
    """Convert undischarged gap obligations to Obstruction objects.

    theory2.tex §21.3.16 — gaps that cannot be discharged become obstructions
    in the global section (the registry cannot be fully coherent).
    """
    obstructions: list[Obstruction] = []
    for ob in obligations:
        if not getattr(ob, "is_discharged", False):
            obstructions.append(Obstruction(
                description=getattr(ob, "description", str(ob)),
                obstruction_id=_new_id("obs"),
                severity=getattr(ob, "priority", 1),
            ))
    return obstructions


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    print(f"[smoke] {__file__}")
    try:
        # build a sample singledispatch function with a few implementations
        @functools.singledispatch
        def process(value: Any) -> str:
            """Default processor."""
            return f"default({value!r})"

        @process.register(int)
        def _process_int(value: int) -> str:
            return f"int({value})"

        @process.register(str)
        def _process_str(value: str) -> str:
            return f"str({value!r})"

        @process.register(list)
        def _process_list(value: list) -> str:
            return f"list(len={len(value)})"

        # analyze the singledispatch function
        coordinator = RegistrySurfacesCoordinator()
        result = coordinator.audit_object(process)

        assert result["record"] is not None, "expected a registry surface record"
        record = result["record"]
        assert record.kind == RegistryKind.SINGLE_DISPATCH, f"expected SINGLE_DISPATCH, got {record.kind}"
        assert record.coverage_fraction > 0, f"expected coverage_fraction > 0, got {record.coverage_fraction}"
        assert record.entry_count() >= 3, f"expected >= 3 entries, got {record.entry_count()}"

        # test witness wrapping
        witness = RegistrySurfacesWitness()
        wrapped = witness.wrap_dispatch(process)
        result_int = wrapped(42)
        result_str = wrapped("hello")
        result_list = wrapped([1, 2, 3])
        result_default = wrapped(3.14)
        assert result_int == "int(42)"
        assert result_str == "str('hello')"
        assert result_list == "list(len=3)"
        assert result_default == "default(3.14)"

        # check witness records
        all_records = witness.get_all()
        assert len(all_records) == 4, f"expected 4 witness records, got {len(all_records)}"

        # test coverage stats
        stats = compute_registry_coverage_stats([record])
        assert "avg_coverage" in stats, "missing avg_coverage key"
        assert stats["total"] == 1

        # test ASCII table
        table = build_surface_summary_table([record])
        assert "Registry" in table, "expected table header"

        # test ABC surface analyzer
        class AbstractAnimal(abc.ABC):
            @abc.abstractmethod
            def speak(self) -> str: ...
            @abc.abstractmethod
            def move(self) -> str: ...

        class Dog(AbstractAnimal):
            def speak(self) -> str: return "woof"
            def move(self) -> str: return "run"

        abc_result = coordinator.audit_object(AbstractAnimal)
        assert abc_result["record"] is not None, "expected ABC surface record"
        abc_record = abc_result["record"]
        assert abc_record.kind == RegistryKind.ABC_ABSTRACT, f"expected ABC_ABSTRACT, got {abc_record.kind}"

        # test dataclass surface
        @dataclasses.dataclass(frozen=True)
        class SamplePoint:
            x: float
            y: float
            label: str = ""

        dc_result = coordinator.audit_object(SamplePoint)
        assert dc_result["record"] is not None, "expected dataclass surface record"
        dc_record = dc_result["record"]
        assert dc_record.kind == RegistryKind.DATACLASS_FIELDS
        assert dc_record.coverage_fraction > 0

        # judgments
        judgments = coordinator.emit_judgments()
        assert len(judgments) > 0, "expected at least one judgment"

        print(f"[smoke] singledispatch entries={record.entry_count()}")
        print(f"[smoke] singledispatch coverage={record.coverage_fraction:.2%}")
        print(f"[smoke] ABC surface entries={abc_record.entry_count()}")
        print(f"[smoke] Dataclass coverage={dc_record.coverage_fraction:.2%}")
        print(f"[smoke] Total judgments={len(judgments)}")
        print(coordinator.report())
        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
