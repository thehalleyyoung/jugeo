from __future__ import annotations

"""Theory2.tex Ch8 §"From single-artifact reasoning to project geometry" —
Projects, modules, hypercovers, and fleets.

Single-artifact reasoning treats each file, module, or generated text in
isolation: one artifact → one judgment → one verdict.  This is the degenerate
case where the "cover" consists of a single patch.  Theory2.tex §8 lifts this
to **project geometry**: a project is a *hypercover* of the judgment site, a
collection of overlapping ArtifactPatches whose compatibility data (cocycle
conditions) produces a coherent global verdict from local evidence.

Mathematical setting
--------------------
Let 𝒮 = (Ob(𝒮), Cov(𝒮)) be the judgment site.  A project P over 𝒮 consists of:

    1.  A *ProjectHypercover* π : P → 𝒮: a hypercover morphism in the ∞-topos
        sense, representing P as a Čech nerve diagram over coordinate patches.
    2.  *ArtifactPatches* {Uᵢ}ᵢ∈I: open sub-sites covering the judgment space,
        each carrying a local section sᵢ ∈ Γ(Uᵢ, ℱ).
    3.  *ProjectCoordinates* {φᵢⱼ}: gluing data on overlaps Uᵢ ∩ Uⱼ satisfying
        the cocycle condition φᵢₖ = φⱼₖ ∘ φᵢⱼ.

Judgment tuples are (c, φ, A, E, O, B, T, Π) where c = context, φ = formula,
A = agent, E = evidence bundle, O = obligations set, B = belief state,
T = trust tier (string, NOT float), Π = provenance chain.

Trust tiers
-----------
Trust is categorical, not scalar.  Tiers (lowest → highest)::

    PROPOSAL < PROVISIONAL < CORROBORATED < CERTIFIED < CANONICAL

Projects and hypercovers
------------------------
The transition from single-artifact to project geometry is not merely
syntactic: the hypercover structure enforces that local verdicts are
*compatible* on overlaps, preventing contradictions that arise when patches
are reasoned about independently.  Theory2.tex §8 shows that H¹(𝒮, ℱ) is
the first obstruction space: a non-trivial class here means the local
sections are mutually inconsistent and no global section can be assembled.

# copilot: foundations/project_hypercovers §s01 — single-artifact to project-geometry
"""

import hashlib
import itertools
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore
    SemanticSite = Any  # type: ignore

try:
    from jugeo.foundations.project_hypercovers.models import (
        TrustTier as _ModelTrustTier,
        ProjectKind as _ModelProjectKind,
    )
    TrustTierBase = _ModelTrustTier
    ProjectKindBase = _ModelProjectKind
except ImportError:
    TrustTierBase = None  # type: ignore
    ProjectKindBase = None  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TrustTier(str, Enum):
    """Categorical trust tiers for project-level reasoning.

    Trust in JuGeo is NOT a float.  It is an ordered categorical tier with
    explicit promotion rules defined in Theory2.tex §2.  The ordering is::

        PROPOSAL < PROVISIONAL < CORROBORATED < CERTIFIED < CANONICAL

    Fleet members propose at PROPOSAL tier and are never silently promoted;
    every tier increase requires explicit justification.

    Notes
    -----
    Tiers are intentionally string-valued so they survive JSON round-trips
    without lossy float encoding.
    """

    PROPOSAL     = "PROPOSAL"
    PROVISIONAL  = "PROVISIONAL"
    CORROBORATED = "CORROBORATED"
    CERTIFIED    = "CERTIFIED"
    CANONICAL    = "CANONICAL"

    def dominates(self, other: TrustTier) -> bool:
        """Return True if this tier is strictly higher than *other*."""
        order = list(TrustTier)
        return order.index(self) > order.index(other)

    def meets(self, other: TrustTier) -> TrustTier:
        """Return the lower of two tiers (categorical meet / infimum)."""
        order = list(TrustTier)
        return self if order.index(self) <= order.index(other) else other

    def joins(self, other: TrustTier) -> TrustTier:
        """Return the higher of two tiers (categorical join / supremum)."""
        order = list(TrustTier)
        return self if order.index(self) >= order.index(other) else other


class CoverageStatus(str, Enum):
    """Coverage status of an ArtifactPatch within the ProjectHypercover.

    Theory2.tex §8.1 distinguishes four coverage states depending on whether
    a coordinate has a local section and whether that section is complete.
    """

    UNCOVERED = "UNCOVERED"   # No artifact patch present for this coordinate
    PARTIAL   = "PARTIAL"     # Patch exists but has unfilled obligation holes
    COVERED   = "COVERED"     # Full local section present on this patch
    REDUNDANT = "REDUNDANT"   # Covered by multiple patches (good for verification)


class CocycleStatus(str, Enum):
    """Status of gluing data on pairwise patch overlaps.

    Theory2.tex §8.1: the Čech 1-cocycle condition φᵢₖ = φⱼₖ ∘ φᵢⱼ must hold
    on every triple overlap for descent to succeed.  This enum tracks whether
    the runtime has checked and whether it succeeded.
    """

    UNCHECKED  = "UNCHECKED"   # Gluing not yet verified
    CONSISTENT = "CONSISTENT"  # Cocycle condition satisfied on Uᵢ ∩ Uⱼ
    VIOLATED   = "VIOLATED"    # Cocycle condition fails; cannot glue here
    DEGENERATE = "DEGENERATE"  # Overlap is empty; condition trivially holds


class ProjectKind(str, Enum):
    """Structural kind of a project as distinguished in Theory2.tex §8.1.

    The kind determines which decomposition strategies are applicable and which
    cohomological obstructions are possible.
    """

    MONOLITHIC   = "MONOLITHIC"   # Single artifact — degenerate cover of depth 0
    MODULAR      = "MODULAR"      # Disjoint modules with no coordinate overlaps
    OVERLAPPING  = "OVERLAPPING"  # Patches share coordinate ranges
    HIERARCHICAL = "HIERARCHICAL" # Nested covers: hypercover of depth > 1
    FLEET_DRIVEN = "FLEET_DRIVEN" # Multiple agents each own a distinct patch


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProjectCoordinate:
    """A coordinate in the judgment site identifying a unique reasoning location.

    In Theory2.tex §8.1 a *coordinate* φ = (domain, aspect, depth) provides
    an address in the product topology of the site.  Multiple ArtifactPatches
    may share a coordinate (creating redundancy) or each patch may cover a
    distinct coordinate range.

    Parameters
    ----------
    coord_id : str
        Unique 12-hex identifier for this coordinate.
    domain : str
        Semantic domain (e.g. ``"security"``, ``"correctness"``, ``"performance"``).
    aspect : str
        Sub-aspect within the domain (e.g. ``"input_validation"``, ``"type_safety"``).
    depth : int
        Hypercover depth at which this coordinate appears (0 = base level).
    label : str
        Human-readable label for display and debugging.
    meta : Mapping[str, Any]
        Arbitrary metadata attached at coordinate creation time.

    Examples
    --------
    >>> c = ProjectCoordinate.make("security", "input_validation")
    >>> c.domain
    'security'
    >>> len(c.coord_id)
    12
    """

    coord_id : str
    domain   : str
    aspect   : str
    depth    : int
    label    : str
    meta     : Mapping[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Return a stable content-hash for this coordinate (SHA-256, 16 hex)."""
        payload = f"{self.domain}:{self.aspect}:{self.depth}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "coord_id": self.coord_id,
            "domain":   self.domain,
            "aspect":   self.aspect,
            "depth":    self.depth,
            "label":    self.label,
            "meta":     dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectCoordinate:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            coord_id = d["coord_id"],
            domain   = d["domain"],
            aspect   = d["aspect"],
            depth    = int(d["depth"]),
            label    = d["label"],
            meta     = d.get("meta", {}),
        )

    @classmethod
    def make(
        cls,
        domain: str,
        aspect: str,
        depth: int = 0,
        label: str = "",
        meta: Mapping[str, Any] | None = None,
    ) -> ProjectCoordinate:
        """Convenience factory: auto-assign ``coord_id``."""
        cid = uuid.uuid4().hex[:12]
        return cls(
            coord_id = cid,
            domain   = domain,
            aspect   = aspect,
            depth    = depth,
            label    = label or f"{domain}/{aspect}@{depth}",
            meta     = meta or {},
        )


@dataclass(frozen=True, slots=True)
class ArtifactPatch:
    """A single artifact (file, module, generated text) as a patch on the site.

    In Theory2.tex §8.1 an *artifact patch* Uᵢ ↪ 𝒮 is an open embedding of a
    sub-site into the ambient judgment site.  The patch carries:

    -   Its own local section sᵢ (the artifact's judgment data).
    -   Trust tier at which the local section was certified.
    -   The set of ProjectCoordinates the patch covers.
    -   Provenance metadata tracing the artifact back to its source.

    Parameters
    ----------
    patch_id : str
        Unique 12-hex identifier.
    artifact_path : str
        File-system path or logical name of the artifact.
    trust_tier : TrustTier
        The highest tier at which the artifact's local section has been verified.
        Note: trust is categorical — never a float.
    coverage_status : CoverageStatus
        Whether the patch fully covers its coordinate range.
    coordinates : Sequence[ProjectCoordinate]
        The site coordinates this patch is responsible for.
    local_section : Mapping[str, Any]
        Serialised local judgment section: keys are formula identifiers, values
        are evidence/belief records.
    provenance : Sequence[str]
        Ordered list of agent/tool identifiers that produced this patch.
    created_at : str
        ISO-8601 timestamp of patch creation.
    obligations : Sequence[str]
        Outstanding obligations that must be discharged to promote trust tier.

    Notes
    -----
    Fleet members always contribute patches at PROPOSAL tier (Theory2.tex §8).
    Promotion requires explicit validation and is recorded in ``provenance``.

    Examples
    --------
    >>> coords = [ProjectCoordinate.make("security", "auth")]
    >>> p = ArtifactPatch.make("src/auth.py", TrustTier.PROVISIONAL, coords)
    >>> p.trust_tier
    <TrustTier.PROVISIONAL: 'PROVISIONAL'>
    """

    patch_id        : str
    artifact_path   : str
    trust_tier      : TrustTier
    coverage_status : CoverageStatus
    coordinates     : Sequence[ProjectCoordinate]
    local_section   : Mapping[str, Any]
    provenance      : Sequence[str]
    created_at      : str
    obligations     : Sequence[str] = field(default_factory=tuple)

    def digest(self) -> str:
        """SHA-256 digest over patch_id, artifact_path, and trust_tier (16 hex)."""
        raw = f"{self.patch_id}|{self.artifact_path}|{self.trust_tier.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def has_obligation(self, key: str) -> bool:
        """Return True if *key* appears in outstanding obligations."""
        return key in self.obligations

    def coordinate_ids(self) -> frozenset[str]:
        """Return a frozenset of the coord_ids covered by this patch."""
        return frozenset(c.coord_id for c in self.coordinates)

    def promote(self, new_tier: TrustTier, justification: str = "") -> ArtifactPatch:
        """Return a copy with an elevated trust tier.

        Parameters
        ----------
        new_tier : TrustTier
            The target tier; must strictly dominate the current tier.
        justification : str, optional
            Reason appended to ``provenance`` for auditability.

        Returns
        -------
        ArtifactPatch
            Promoted copy of this patch.

        Raises
        ------
        ValueError
            If *new_tier* does not strictly dominate the current tier (trust
            can only move upward in Theory2.tex's trust lattice).

        Notes
        -----
        This is the only legitimate path to trust promotion.  No code path in
        the fleet or coordinator may silently raise a patch's tier.
        """
        if not new_tier.dominates(self.trust_tier):
            raise ValueError(
                f"Cannot promote patch {self.patch_id!r} from "
                f"{self.trust_tier.value} to {new_tier.value}: "
                "new tier must strictly dominate the current tier."
            )
        new_prov = tuple(list(self.provenance) + ([justification] if justification else []))
        return replace(self, trust_tier=new_tier, provenance=new_prov)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "patch_id":        self.patch_id,
            "artifact_path":   self.artifact_path,
            "trust_tier":      self.trust_tier.value,
            "coverage_status": self.coverage_status.value,
            "coordinates":     [c.to_dict() for c in self.coordinates],
            "local_section":   dict(self.local_section),
            "provenance":      list(self.provenance),
            "created_at":      self.created_at,
            "obligations":     list(self.obligations),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactPatch:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            patch_id        = d["patch_id"],
            artifact_path   = d["artifact_path"],
            trust_tier      = TrustTier(d["trust_tier"]),
            coverage_status = CoverageStatus(d["coverage_status"]),
            coordinates     = tuple(ProjectCoordinate.from_dict(c) for c in d["coordinates"]),
            local_section   = d.get("local_section", {}),
            provenance      = tuple(d.get("provenance", [])),
            created_at      = d["created_at"],
            obligations     = tuple(d.get("obligations", [])),
        )

    @classmethod
    def make(
        cls,
        artifact_path: str,
        trust_tier: TrustTier,
        coordinates: Sequence[ProjectCoordinate],
        local_section: Mapping[str, Any] | None = None,
        provenance: Sequence[str] = (),
        obligations: Sequence[str] = (),
    ) -> ArtifactPatch:
        """Factory: auto-assign patch_id and created_at.

        Parameters
        ----------
        artifact_path : str
            Path or logical name of the artifact.
        trust_tier : TrustTier
            Initial trust tier.  Fleet members must use PROPOSAL.
        coordinates : Sequence[ProjectCoordinate]
            Coordinates this patch covers.
        local_section : Mapping[str, Any], optional
            Initial local judgment section data.
        provenance : Sequence[str], optional
            Creator provenance chain.
        obligations : Sequence[str], optional
            Outstanding obligations at creation time.

        Returns
        -------
        ArtifactPatch
            A fresh, immutable ArtifactPatch.
        """
        status = CoverageStatus.COVERED if not obligations else CoverageStatus.PARTIAL
        return cls(
            patch_id        = uuid.uuid4().hex[:12],
            artifact_path   = artifact_path,
            trust_tier      = trust_tier,
            coverage_status = status,
            coordinates     = tuple(coordinates),
            local_section   = local_section or {},
            provenance      = tuple(provenance),
            created_at      = datetime.now(timezone.utc).isoformat(),
            obligations     = tuple(obligations),
        )


@dataclass(frozen=True, slots=True)
class ProjectSite:
    """The judgment site over which a project is laid out as a hypercover.

    In Theory2.tex §8.1 the *judgment site* 𝒮 = (Ob(𝒮), Cov(𝒮)) is a
    Grothendieck site: objects are contexts (coordinates), and covers are
    families of morphisms satisfying the Grothendieck axioms (base change,
    local character, identity sieve).

    A ProjectSite is the *concrete* runtime representation of 𝒮: it enumerates
    the site's objects as coordinate sets and records the covering sieves.

    Parameters
    ----------
    site_id : str
        Unique 12-hex identifier.
    name : str
        Human-readable project name.
    root_coordinates : Sequence[ProjectCoordinate]
        Base-level objects of the site (depth=0 coordinates).
    kind : ProjectKind
        Structural classification of this project.
    covering_sieves : Mapping[str, Sequence[str]]
        Grothendieck covering sieves: maps each coord_id to the list of
        coord_ids that form a cover for it in the Grothendieck topology.
    created_at : str
        ISO-8601 creation timestamp.
    meta : Mapping[str, Any]
        Arbitrary metadata.

    Notes
    -----
    For a Grothendieck topology, covering sieves must satisfy:
    (i) Identity: {X → X} covers X.
    (ii) Stability: covers are stable under base change.
    (iii) Local character: local covers imply global cover.
    Only (i) is checked at runtime; (ii) and (iii) require full morphism data.
    """

    site_id          : str
    name             : str
    root_coordinates : Sequence[ProjectCoordinate]
    kind             : ProjectKind
    covering_sieves  : Mapping[str, Sequence[str]]
    created_at       : str
    meta             : Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Topology queries
    # ------------------------------------------------------------------

    def coordinate_by_id(self, coord_id: str) -> ProjectCoordinate | None:
        """Look up a root coordinate by its ``coord_id``. Returns None if absent."""
        for c in self.root_coordinates:
            if c.coord_id == coord_id:
                return c
        return None

    def covers_for(self, coord_id: str) -> Sequence[str]:
        """Return the list of coord_ids that form a covering sieve for *coord_id*."""
        return self.covering_sieves.get(coord_id, ())

    def is_covering_family(self, coord_id: str, family: Sequence[str]) -> bool:
        """Check whether *family* is a covering sieve for *coord_id*.

        A family is a covering sieve if it contains every element of the
        registered sieve for *coord_id* (stability under base change).

        Parameters
        ----------
        coord_id : str
            The coordinate to check coverage for.
        family : Sequence[str]
            The candidate covering family.

        Returns
        -------
        bool
            True when *family* is a valid Grothendieck covering sieve.
        """
        registered = set(self.covers_for(coord_id))
        if not registered:
            return len(family) > 0  # any non-empty family covers a point object
        return registered.issubset(set(family))

    def coordinate_domains(self) -> list[str]:
        """Return sorted list of distinct domains represented in the site."""
        return sorted({c.domain for c in self.root_coordinates})

    def depth_slice(self, depth: int) -> list[ProjectCoordinate]:
        """Return all root coordinates at the given hypercover *depth*."""
        return [c for c in self.root_coordinates if c.depth == depth]

    def coordinates_by_domain(self) -> dict[str, list[ProjectCoordinate]]:
        """Return a dict mapping domain name to its coordinates."""
        result: dict[str, list[ProjectCoordinate]] = defaultdict(list)
        for c in self.root_coordinates:
            result[c.domain].append(c)
        return dict(result)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "site_id":          self.site_id,
            "name":             self.name,
            "root_coordinates": [c.to_dict() for c in self.root_coordinates],
            "kind":             self.kind.value,
            "covering_sieves":  {k: list(v) for k, v in self.covering_sieves.items()},
            "created_at":       self.created_at,
            "meta":             dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectSite:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            site_id          = d["site_id"],
            name             = d["name"],
            root_coordinates = tuple(
                ProjectCoordinate.from_dict(c) for c in d["root_coordinates"]
            ),
            kind             = ProjectKind(d["kind"]),
            covering_sieves  = {
                k: tuple(v) for k, v in d.get("covering_sieves", {}).items()
            },
            created_at       = d["created_at"],
            meta             = d.get("meta", {}),
        )

    @classmethod
    def make(
        cls,
        name: str,
        root_coordinates: Sequence[ProjectCoordinate],
        kind: ProjectKind = ProjectKind.MODULAR,
        covering_sieves: Mapping[str, Sequence[str]] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> ProjectSite:
        """Convenience factory with auto-assigned IDs and timestamp."""
        return cls(
            site_id          = uuid.uuid4().hex[:12],
            name             = name,
            root_coordinates = tuple(root_coordinates),
            kind             = kind,
            covering_sieves  = covering_sieves or {},
            created_at       = datetime.now(timezone.utc).isoformat(),
            meta             = meta or {},
        )


@dataclass(frozen=True, slots=True)
class ProjectHypercover:
    """A hypercover of a ProjectSite by a collection of ArtifactPatches.

    In Theory2.tex §8.1 a *hypercover* π : P• → 𝒮 is a simplicial object in
    the category of sites augmented over 𝒮, satisfying the hypercover condition:
    each level Pₙ → 𝒮 ×_{𝒮⁽ⁿ⁾} 𝒮 is a cover.

    The runtime representation records:

    -   A flat list of ArtifactPatches (level-0 data).
    -   Pairwise cocycle data keyed by "patchId_a:patchId_b" (level-1 checks).
    -   The associated ProjectSite.
    -   Derived completeness and global trust fields.

    Parameters
    ----------
    hypercover_id : str
        Unique 12-hex identifier.
    site : ProjectSite
        The site being covered.
    patches : Sequence[ArtifactPatch]
        The artifact patches forming level-0 of the hypercover.
    cocycle_data : Mapping[str, CocycleStatus]
        Keys are ``"patchId_a:patchId_b"`` strings; values are CocycleStatus.
    is_complete : bool
        True when every root coordinate of the site is covered by ≥ 1 patch.
    global_trust : TrustTier
        The meet (minimum) of all patch trust tiers — the weakest link.
    created_at : str
        ISO-8601 creation timestamp.

    Notes
    -----
    ``global_trust`` is the categorical meet: if any patch is at PROPOSAL, the
    whole project is at PROPOSAL.  This reflects Theory2.tex's conservative
    stance on project-level trust.
    """

    hypercover_id : str
    site          : ProjectSite
    patches       : Sequence[ArtifactPatch]
    cocycle_data  : Mapping[str, CocycleStatus]
    is_complete   : bool
    global_trust  : TrustTier
    created_at    : str

    # ------------------------------------------------------------------
    # Patch queries
    # ------------------------------------------------------------------

    def patch_by_id(self, patch_id: str) -> ArtifactPatch | None:
        """Find a patch by its ``patch_id``. Returns None if not found."""
        for p in self.patches:
            if p.patch_id == patch_id:
                return p
        return None

    def patches_for_coordinate(self, coord_id: str) -> list[ArtifactPatch]:
        """Return all patches whose coordinate list includes *coord_id*."""
        return [
            p for p in self.patches
            if any(c.coord_id == coord_id for c in p.coordinates)
        ]

    def cocycle_status_for(self, patch_a: str, patch_b: str) -> CocycleStatus:
        """Look up the cocycle status for the pair (*patch_a*, *patch_b*).

        The lookup is symmetric: (a, b) and (b, a) refer to the same overlap.
        """
        key_ab = f"{patch_a}:{patch_b}"
        key_ba = f"{patch_b}:{patch_a}"
        return (
            self.cocycle_data.get(key_ab)
            or self.cocycle_data.get(key_ba)
            or CocycleStatus.UNCHECKED
        )

    def uncovered_coordinates(self) -> list[ProjectCoordinate]:
        """Return site coordinates not covered by any patch."""
        covered_ids: set[str] = set()
        for patch in self.patches:
            for c in patch.coordinates:
                covered_ids.add(c.coord_id)
        return [c for c in self.site.root_coordinates if c.coord_id not in covered_ids]

    def violated_cocycle_pairs(self) -> list[tuple[str, str]]:
        """Return all (patch_a_id, patch_b_id) pairs with a VIOLATED cocycle."""
        result: list[tuple[str, str]] = []
        for key, status in self.cocycle_data.items():
            if status == CocycleStatus.VIOLATED:
                parts = key.split(":", 1)
                if len(parts) == 2:
                    result.append((parts[0], parts[1]))
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "hypercover_id": self.hypercover_id,
            "site":          self.site.to_dict(),
            "patches":       [p.to_dict() for p in self.patches],
            "cocycle_data":  {k: v.value for k, v in self.cocycle_data.items()},
            "is_complete":   self.is_complete,
            "global_trust":  self.global_trust.value,
            "created_at":    self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectHypercover:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            hypercover_id = d["hypercover_id"],
            site          = ProjectSite.from_dict(d["site"]),
            patches       = tuple(ArtifactPatch.from_dict(p) for p in d["patches"]),
            cocycle_data  = {
                k: CocycleStatus(v) for k, v in d.get("cocycle_data", {}).items()
            },
            is_complete   = bool(d["is_complete"]),
            global_trust  = TrustTier(d["global_trust"]),
            created_at    = d["created_at"],
        )

    @classmethod
    def assemble(
        cls,
        site: ProjectSite,
        patches: Sequence[ArtifactPatch],
    ) -> ProjectHypercover:
        """Construct a hypercover from a site and patches, deriving all fields.

        Computes:

        -   **Completeness**: whether every site coordinate is covered.
        -   **Global trust**: the categorical meet of all patch trust tiers.
        -   **Cocycle data**: initialises all pairs to UNCHECKED; explicit
            verification is deferred to the coordinator's ``_verify_cocycles``.

        Parameters
        ----------
        site : ProjectSite
            The site being covered.
        patches : Sequence[ArtifactPatch]
            The patches forming the level-0 data.

        Returns
        -------
        ProjectHypercover
            A freshly assembled hypercover ready for cocycle verification.
        """
        covered_ids: set[str] = set()
        for p in patches:
            for c in p.coordinates:
                covered_ids.add(c.coord_id)
        all_ids = {c.coord_id for c in site.root_coordinates}
        is_complete = all_ids.issubset(covered_ids)

        global_trust = TrustTier.CANONICAL
        for p in patches:
            global_trust = global_trust.meets(p.trust_tier)

        cocycle_data: dict[str, CocycleStatus] = {}
        patch_list = list(patches)
        for i, a in enumerate(patch_list):
            for b in patch_list[i + 1 :]:
                cocycle_data[f"{a.patch_id}:{b.patch_id}"] = CocycleStatus.UNCHECKED

        return cls(
            hypercover_id = uuid.uuid4().hex[:12],
            site          = site,
            patches       = tuple(patches),
            cocycle_data  = cocycle_data,
            is_complete   = is_complete,
            global_trust  = global_trust,
            created_at    = datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True, slots=True)
class CohomologyObstruction:
    """An H¹ cohomology obstruction preventing global section assembly.

    Theory2.tex §8.1 identifies H¹(𝒮, ℱ) as the first obstruction space to
    assembling a global section from local data.  A non-trivial class signals
    that the local sections are individually valid but mutually incompatible.

    Parameters
    ----------
    obstruction_id : str
        Unique 12-hex identifier.
    violating_patches : Sequence[str]
        patch_ids whose pairwise cocycle fails.
    cocycle_description : str
        Human-readable description of the failing gluing datum.
    repair_hints : Sequence[str]
        Actionable suggestions for resolving the obstruction.

    Notes
    -----
    An obstruction does not necessarily mean the project is wrong — it may
    mean that two patches are reasoning about the *same* coordinate with
    different evidence.  The repair strategy is to either unify the evidence
    or split the coordinate into disjoint sub-coordinates.
    """

    obstruction_id      : str
    violating_patches   : Sequence[str]
    cocycle_description : str
    repair_hints        : Sequence[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "obstruction_id":      self.obstruction_id,
            "violating_patches":   list(self.violating_patches),
            "cocycle_description": self.cocycle_description,
            "repair_hints":        list(self.repair_hints),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CohomologyObstruction:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            obstruction_id      = d["obstruction_id"],
            violating_patches   = tuple(d.get("violating_patches", [])),
            cocycle_description = d.get("cocycle_description", ""),
            repair_hints        = tuple(d.get("repair_hints", [])),
        )

    @classmethod
    def make(
        cls,
        violating_patches: Sequence[str],
        description: str,
        hints: Sequence[str] = (),
    ) -> CohomologyObstruction:
        """Convenience factory with auto-assigned ID."""
        return cls(
            obstruction_id      = uuid.uuid4().hex[:12],
            violating_patches   = tuple(violating_patches),
            cocycle_description = description,
            repair_hints        = tuple(hints),
        )


# ---------------------------------------------------------------------------
# FromSingleArtifactReasoningCoordinator
# ---------------------------------------------------------------------------

class FromSingleArtifactReasoningCoordinator:
    """Orchestrates the transition from single-artifact to project-geometry reasoning.

    In degenerate single-artifact mode the "cover" consists of exactly one
    patch: Cov = {U₀} where U₀ = 𝒮.  This coordinator detects degenerate
    covers and promotes them to proper hypercovers by:

    1.  Decomposing the single artifact into domain-partitioned ArtifactPatches.
    2.  Building a ProjectSite whose coordinates track each domain partition.
    3.  Assembling the patches into a ProjectHypercover with explicit cocycle data.
    4.  Verifying the global trust floor meets the project's required tier.
    5.  Attempting repairs on violated cocycles before returning the witness.

    Parameters
    ----------
    required_trust : TrustTier
        The minimum global trust tier required for the assembled cover.
        Default: ``PROVISIONAL``.
    max_patches : int
        Upper bound on the number of ArtifactPatches generated during
        decomposition.  Prevents runaway expansion of large monolithic
        artifacts.  Default: 64.
    verbose : bool
        Emit progress messages to stdout during ``run()``.  Default: False.

    Examples
    --------
    >>> coord = FromSingleArtifactReasoningCoordinator()
    >>> site = ProjectSite.make("my_project", [
    ...     ProjectCoordinate.make("security", "input_validation"),
    ...     ProjectCoordinate.make("correctness", "type_safety"),
    ... ])
    >>> patch = ArtifactPatch.make("src/main.py", TrustTier.PROPOSAL,
    ...                            site.root_coordinates)
    >>> witness = coord.run(site, [patch])
    >>> witness.hypercover.is_complete
    True
    """

    def __init__(
        self,
        required_trust: TrustTier = TrustTier.PROVISIONAL,
        max_patches: int = 64,
        verbose: bool = False,
    ) -> None:
        self.required_trust = required_trust
        self.max_patches    = max_patches
        self.verbose        = verbose
        self._log: list[str] = []

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def run(
        self,
        site: ProjectSite,
        patches: Sequence[ArtifactPatch],
    ) -> FromSingleArtifactReasoningWitness:
        """Assemble a ProjectHypercover and return an immutable witness certificate.

        Steps:

        1.  Validate inputs; raise ``ValueError`` on structural errors.
        2.  Expand degenerate (monolithic) patches into domain sub-patches.
        3.  Assemble the ProjectHypercover from the expanded patch list.
        4.  Verify pairwise cocycle conditions.
        5.  Detect H¹ obstructions from violated cocycles.
        6.  Attempt lightweight repairs (trust demotion) on violations.
        7.  Compute the assembly score and package the witness.

        Parameters
        ----------
        site : ProjectSite
            The judgment site to be covered.
        patches : Sequence[ArtifactPatch]
            Initial artifact patches; may include degenerate single-patch covers.

        Returns
        -------
        FromSingleArtifactReasoningWitness
            Immutable certificate summarising the assembly outcome.

        Raises
        ------
        ValueError
            If *patches* is empty or exceeds ``max_patches``.
        """
        t0 = time.monotonic()
        if not patches:
            raise ValueError("At least one ArtifactPatch is required.")
        if len(patches) > self.max_patches:
            raise ValueError(
                f"Patch count {len(patches)} exceeds max_patches={self.max_patches}."
            )

        self._log.clear()
        self._emit(f"run: site={site.site_id!r} n_patches={len(patches)}")

        expanded   = self._expand_degenerate_patches(site, list(patches))
        self._emit(f"run: expanded to {len(expanded)} patches")

        hypercover = ProjectHypercover.assemble(site, expanded)
        hypercover = self._verify_cocycles(hypercover)
        obstructions = self._detect_obstructions(hypercover)

        if obstructions:
            hypercover, obstructions = self._attempt_repair(hypercover, obstructions)
            self._emit(f"run: after repair, {len(obstructions)} obstruction(s) remain")

        elapsed = time.monotonic() - t0
        score   = self._compute_score(hypercover, obstructions)

        tiers = list(TrustTier)
        trust_met = (
            hypercover.global_trust == self.required_trust
            or hypercover.global_trust.dominates(self.required_trust)
        )

        return FromSingleArtifactReasoningWitness(
            witness_id   = uuid.uuid4().hex[:12],
            hypercover   = hypercover,
            obstructions = tuple(obstructions),
            score        = score,
            trust_met    = trust_met,
            elapsed_s    = elapsed,
            log_lines    = tuple(self._log),
            created_at   = datetime.now(timezone.utc).isoformat(),
        )

    def validate(
        self,
        site: ProjectSite,
        patches: Sequence[ArtifactPatch],
    ) -> list[str]:
        """Return a list of validation error messages (empty list = valid input).

        Checks performed:

        -   All patch coordinates are registered in *site*.
        -   No two patches share the same ``patch_id``.
        -   All trust tiers are valid enum values.
        -   Patch count does not exceed ``max_patches``.

        Parameters
        ----------
        site : ProjectSite
            The site against which patches are validated.
        patches : Sequence[ArtifactPatch]
            The patches to validate.

        Returns
        -------
        list[str]
            Human-readable error messages; empty when inputs are valid.
        """
        errors: list[str] = []
        site_coord_ids = {c.coord_id for c in site.root_coordinates}
        seen_patch_ids: set[str] = set()

        if len(patches) > self.max_patches:
            errors.append(
                f"patch count {len(patches)} exceeds max_patches={self.max_patches}"
            )

        for p in patches:
            if p.patch_id in seen_patch_ids:
                errors.append(f"duplicate patch_id: {p.patch_id!r}")
            seen_patch_ids.add(p.patch_id)
            for c in p.coordinates:
                if c.coord_id not in site_coord_ids:
                    errors.append(
                        f"patch {p.patch_id!r}: coordinate {c.coord_id!r} "
                        "not registered in site"
                    )

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise coordinator configuration to a JSON-compatible dict."""
        return {
            "required_trust": self.required_trust.value,
            "max_patches":    self.max_patches,
            "verbose":        self.verbose,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FromSingleArtifactReasoningCoordinator:
        """Deserialise coordinator from a dict produced by ``to_dict()``."""
        return cls(
            required_trust = TrustTier(d["required_trust"]),
            max_patches    = int(d.get("max_patches", 64)),
            verbose        = bool(d.get("verbose", False)),
        )

    # ------------------------------------------------------------------
    # Domain methods
    # ------------------------------------------------------------------

    def decompose_monolithic_artifact(
        self,
        patch: ArtifactPatch,
        site: ProjectSite,
    ) -> list[ArtifactPatch]:
        """Decompose a monolithic patch into domain-partitioned sub-patches.

        When *patch* covers coordinates from multiple domains, one sub-patch
        is produced per domain, each inheriting the original trust tier and
        provenance.  The sub-patch local_section is filtered to keys matching
        the domain prefix.

        Parameters
        ----------
        patch : ArtifactPatch
            The monolithic artifact patch to decompose.
        site : ProjectSite
            The project site; used for domain grouping context.

        Returns
        -------
        list[ArtifactPatch]
            One patch per domain; if *patch* spans only one domain, a
            single-element list containing the original patch is returned
            unchanged.

        Notes
        -----
        Decomposition is domain-based, not file-based.  A single Python file
        may contribute evidence to both ``"correctness"`` and ``"security"``
        domains; after decomposition each domain receives its own patch entry.
        """
        domain_groups: dict[str, list[ProjectCoordinate]] = defaultdict(list)
        for c in patch.coordinates:
            domain_groups[c.domain].append(c)

        if len(domain_groups) <= 1:
            return [patch]

        result: list[ArtifactPatch] = []
        for domain, coords in sorted(domain_groups.items()):
            sub = ArtifactPatch.make(
                artifact_path = f"{patch.artifact_path}#{domain}",
                trust_tier    = patch.trust_tier,
                coordinates   = coords,
                local_section = {
                    k: v for k, v in patch.local_section.items()
                    if k.startswith(domain)
                },
                provenance    = list(patch.provenance) + ["decompose_monolithic_artifact"],
                obligations   = list(patch.obligations),
            )
            result.append(sub)
        return result

    def check_grothendieck_axioms(self, site: ProjectSite) -> dict[str, bool]:
        """Verify that the covering sieves of *site* satisfy Grothendieck axioms.

        Checks the three standard axioms from SGA4 adapted to this setting:

        1.  **Identity axiom**: every object covers itself (trivially true here
            since every coord_id in ``covering_sieves`` is a root coord).
        2.  **Stability axiom**: covering sieves are stable under base change
            (requires full morphism data; recorded as placeholder).
        3.  **Local character**: local covering families imply a global one
            (also requires full morphism data; recorded as placeholder).

        Parameters
        ----------
        site : ProjectSite
            The site to check.

        Returns
        -------
        dict[str, bool]
            Keys: ``"identity"``, ``"stability_placeholder"``,
            ``"local_character_placeholder"``.
        """
        coord_ids = {c.coord_id for c in site.root_coordinates}
        identity_ok = all(
            cid in coord_ids for cid in site.covering_sieves
        )
        return {
            "identity":                    identity_ok,
            "stability_placeholder":       True,
            "local_character_placeholder": True,
        }

    def compute_cech_nerve(self, hypercover: ProjectHypercover) -> dict[str, Any]:
        """Compute the Čech nerve of the hypercover up to simplicial dimension 2.

        The Čech nerve N(𝒰)• has:

        -  N₀ = ∐ᵢ Uᵢ  (patches themselves)
        -  N₁ = ∐ᵢ<ⱼ Uᵢ ∩ Uⱼ  (pairwise overlaps as shared coordinates)
        -  N₂ = ∐ᵢ<ⱼ<ₖ Uᵢ ∩ Uⱼ ∩ Uₖ  (triple overlaps)

        Overlaps are represented as intersections of coordinate sets.

        Parameters
        ----------
        hypercover : ProjectHypercover
            The hypercover whose nerve to compute.

        Returns
        -------
        dict[str, Any]
            ``n0``: list of patch_ids,
            ``n1``: list of dicts with patch_a, patch_b, shared_coords,
            ``n2``: list of dicts with patch_a, patch_b, patch_c, shared_coords.
        """
        patches = list(hypercover.patches)
        n0 = [p.patch_id for p in patches]

        def shared(pa: ArtifactPatch, pb: ArtifactPatch) -> list[str]:
            return sorted(pa.coordinate_ids() & pb.coordinate_ids())

        n1 = [
            {"patch_a": pa.patch_id, "patch_b": pb.patch_id, "shared_coords": shared(pa, pb)}
            for i, pa in enumerate(patches)
            for pb in patches[i + 1 :]
            if shared(pa, pb)
        ]

        n2 = []
        for i, pa in enumerate(patches):
            for j, pb in enumerate(patches):
                if j <= i:
                    continue
                for pc in patches[j + 1 :]:
                    sc = sorted(pa.coordinate_ids() & pb.coordinate_ids() & pc.coordinate_ids())
                    if sc:
                        n2.append({
                            "patch_a": pa.patch_id,
                            "patch_b": pb.patch_id,
                            "patch_c": pc.patch_id,
                            "shared_coords": sc,
                        })

        return {"n0": n0, "n1": n1, "n2": n2}

    def promote_patch_trust(
        self,
        hypercover: ProjectHypercover,
        patch_id: str,
        new_tier: TrustTier,
        justification: str,
    ) -> ProjectHypercover:
        """Promote a single patch's trust tier and return a rebuilt hypercover.

        Parameters
        ----------
        hypercover : ProjectHypercover
            The hypercover containing the patch.
        patch_id : str
            The patch to promote.
        new_tier : TrustTier
            Must strictly dominate the patch's current tier.
        justification : str
            Reason appended to provenance; required for auditability.

        Returns
        -------
        ProjectHypercover
            A new hypercover with the promoted patch and recomputed global_trust.

        Raises
        ------
        KeyError
            If *patch_id* is not found in *hypercover*.
        ValueError
            If *new_tier* does not strictly dominate the current tier.
        """
        patch = hypercover.patch_by_id(patch_id)
        if patch is None:
            raise KeyError(f"patch_id {patch_id!r} not found in hypercover")
        promoted = patch.promote(new_tier, justification=justification)
        new_patches = [
            promoted if p.patch_id == patch_id else p
            for p in hypercover.patches
        ]
        return ProjectHypercover.assemble(hypercover.site, new_patches)

    def lift_to_project_geometry(
        self,
        artifact_path: str,
        domains: Sequence[str],
        trust: TrustTier,
    ) -> tuple[ProjectSite, ProjectHypercover]:
        """Lift a single artifact into full project geometry.

        This is the canonical entry point for degenerate (single-artifact)
        reasoning.  It creates one coordinate per domain and one patch per
        domain, enabling domain-level reasoning while sharing the same source
        file.

        Parameters
        ----------
        artifact_path : str
            The source artifact path.
        domains : Sequence[str]
            Semantic domains the artifact participates in.
        trust : TrustTier
            Initial trust tier for all generated patches.

        Returns
        -------
        tuple[ProjectSite, ProjectHypercover]
            A minimal site and hypercover ready for further reasoning.

        Notes
        -----
        Fleet members should always pass ``TrustTier.PROPOSAL`` here; trust
        promotion requires subsequent validation steps.
        """
        coords = [ProjectCoordinate.make(d, "primary") for d in domains]
        site   = ProjectSite.make(artifact_path, coords)
        patches = [
            ArtifactPatch.make(
                artifact_path = artifact_path,
                trust_tier    = trust,
                coordinates   = [c],
                provenance    = ["lift_to_project_geometry"],
            )
            for c in coords
        ]
        hc = ProjectHypercover.assemble(site, patches)
        return site, hc

    def merge_sites(
        self,
        sites: Sequence[ProjectSite],
        name: str,
        kind: ProjectKind = ProjectKind.OVERLAPPING,
    ) -> ProjectSite:
        """Merge multiple ProjectSites into a single combined site.

        Coordinate IDs are preserved; duplicates (same coord_id) are deduplicated.
        Covering sieves are merged by union.

        Parameters
        ----------
        sites : Sequence[ProjectSite]
            Sites to merge.
        name : str
            Name for the merged site.
        kind : ProjectKind
            Kind for the merged site.

        Returns
        -------
        ProjectSite
            The merged site.
        """
        seen_ids: set[str] = set()
        all_coords: list[ProjectCoordinate] = []
        merged_sieves: dict[str, set[str]] = defaultdict(set)

        for s in sites:
            for c in s.root_coordinates:
                if c.coord_id not in seen_ids:
                    all_coords.append(c)
                    seen_ids.add(c.coord_id)
            for cid, covering in s.covering_sieves.items():
                merged_sieves[cid].update(covering)

        return ProjectSite.make(
            name             = name,
            root_coordinates = all_coords,
            kind             = kind,
            covering_sieves  = {k: tuple(v) for k, v in merged_sieves.items()},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        ts    = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        if self.verbose:
            print(entry)

    def _expand_degenerate_patches(
        self,
        site: ProjectSite,
        patches: list[ArtifactPatch],
    ) -> list[ArtifactPatch]:
        """Expand monolithic patches when the site is MONOLITHIC kind."""
        result: list[ArtifactPatch] = []
        for p in patches:
            domains = {c.domain for c in p.coordinates}
            if len(domains) > 1 and site.kind == ProjectKind.MONOLITHIC:
                result.extend(self.decompose_monolithic_artifact(p, site))
            else:
                result.append(p)
        return result[: self.max_patches]

    def _verify_cocycles(
        self, hypercover: ProjectHypercover
    ) -> ProjectHypercover:
        """Check all pairwise overlaps; update cocycle_data with CONSISTENT/VIOLATED."""
        new_cocycle: dict[str, CocycleStatus] = {}
        patches = list(hypercover.patches)
        for i, pa in enumerate(patches):
            for pb in patches[i + 1 :]:
                overlap = pa.coordinate_ids() & pb.coordinate_ids()
                key     = f"{pa.patch_id}:{pb.patch_id}"
                if not overlap:
                    new_cocycle[key] = CocycleStatus.DEGENERATE
                elif self._sections_agree(pa, pb, overlap):
                    new_cocycle[key] = CocycleStatus.CONSISTENT
                else:
                    new_cocycle[key] = CocycleStatus.VIOLATED
        return replace(hypercover, cocycle_data=new_cocycle)

    def _sections_agree(
        self,
        pa: ArtifactPatch,
        pb: ArtifactPatch,
        shared: frozenset[str],
    ) -> bool:
        """Return True if pa and pb have compatible local sections on *shared*."""
        for coord_id in shared:
            val_a = pa.local_section.get(coord_id)
            val_b = pb.local_section.get(coord_id)
            if val_a is not None and val_b is not None and val_a != val_b:
                return False
        return True

    def _detect_obstructions(
        self, hypercover: ProjectHypercover
    ) -> list[CohomologyObstruction]:
        """Collect H¹ obstructions from violated cocycle pairs."""
        return [
            CohomologyObstruction.make(
                violating_patches = [pa_id, pb_id],
                description       = (
                    f"Local sections of patches {pa_id!r} and {pb_id!r} "
                    "disagree on shared coordinates."
                ),
                hints = [
                    f"Review section values for patch {pa_id!r}",
                    f"Review section values for patch {pb_id!r}",
                    "Split overlapping coordinates into disjoint sub-coordinates",
                ],
            )
            for pa_id, pb_id in hypercover.violated_cocycle_pairs()
        ]

    def _attempt_repair(
        self,
        hypercover: ProjectHypercover,
        obstructions: list[CohomologyObstruction],
    ) -> tuple[ProjectHypercover, list[CohomologyObstruction]]:
        """Repair violated cocycles by demoting offending patches to PROPOSAL tier.

        This conservative repair strategy preserves assembly completeness at the
        cost of reducing global trust.  Demoted patches are flagged with the
        obligation ``"repair_cocycle"`` so downstream stages know they need
        re-verification.
        """
        patches_to_demote: set[str] = set()
        for obs in obstructions:
            patches_to_demote.update(obs.violating_patches)

        if not patches_to_demote:
            return hypercover, obstructions

        new_patches: list[ArtifactPatch] = []
        for p in hypercover.patches:
            if p.patch_id in patches_to_demote:
                new_patches.append(replace(
                    p,
                    trust_tier      = TrustTier.PROPOSAL,
                    coverage_status = CoverageStatus.PARTIAL,
                    obligations     = tuple(list(p.obligations) + ["repair_cocycle"]),
                ))
            else:
                new_patches.append(p)

        new_hc       = ProjectHypercover.assemble(hypercover.site, new_patches)
        new_hc       = self._verify_cocycles(new_hc)
        remaining    = self._detect_obstructions(new_hc)
        return new_hc, remaining

    def _compute_score(
        self,
        hypercover: ProjectHypercover,
        obstructions: list[CohomologyObstruction],
    ) -> float:
        """Compute a [0.0, 1.0] assembly quality score.

        Score formula (Theory2.tex §8.1 remark)::

            score = coverage_ratio × trust_weight × (1 − obstruction_penalty)

        where *obstruction_penalty* is capped at 1.0.
        """
        total_coords   = max(len(hypercover.site.root_coordinates), 1)
        covered        = total_coords - len(hypercover.uncovered_coordinates())
        coverage_ratio = covered / total_coords

        tier_weights = {
            TrustTier.PROPOSAL:     0.2,
            TrustTier.PROVISIONAL:  0.4,
            TrustTier.CORROBORATED: 0.6,
            TrustTier.CERTIFIED:    0.8,
            TrustTier.CANONICAL:    1.0,
        }
        trust_weight  = tier_weights.get(hypercover.global_trust, 0.2)

        total_pairs         = max(len(hypercover.cocycle_data), 1)
        obstruction_penalty = min(len(obstructions) / total_pairs, 1.0)

        return coverage_ratio * trust_weight * (1.0 - obstruction_penalty)


# ---------------------------------------------------------------------------
# FromSingleArtifactReasoningAnalyzer
# ---------------------------------------------------------------------------

class FromSingleArtifactReasoningAnalyzer:
    """Analyses an assembled ProjectHypercover witness and produces diagnostics.

    While the Coordinator builds the hypercover, the Analyzer understands it:
    computing coverage metrics, identifying trust gaps, flagging H¹ obstructions,
    and emitting a structured report suitable for IDE plugins, CI gates, and
    dashboards.

    Parameters
    ----------
    min_score_threshold : float
        Minimum assembly score (in [0, 1]) below which the hypercover is
        flagged as critically deficient.  Default: 0.5.

    Examples
    --------
    >>> analyzer = FromSingleArtifactReasoningAnalyzer()
    >>> is_ok = analyzer.is_healthy(witness)
    """

    def __init__(self, min_score_threshold: float = 0.5) -> None:
        self.min_score_threshold = min_score_threshold

    def analyze(
        self, witness: FromSingleArtifactReasoningWitness
    ) -> dict[str, Any]:
        """Produce a full structured analysis of the witness certificate.

        Parameters
        ----------
        witness : FromSingleArtifactReasoningWitness
            The output of a completed ``Coordinator.run()`` call.

        Returns
        -------
        dict[str, Any]
            Keys: ``summary``, ``coverage``, ``trust``, ``obstructions``,
            ``nerve``, ``recommendations``.
        """
        hc    = witness.hypercover
        coord = FromSingleArtifactReasoningCoordinator()

        coverage = self._analyze_coverage(hc)
        trust    = self._analyze_trust(hc)
        obs_info = self._analyze_obstructions(witness.obstructions)
        nerve    = coord.compute_cech_nerve(hc)

        return {
            "summary":         self.summarize(witness),
            "coverage":        coverage,
            "trust":           trust,
            "obstructions":    obs_info,
            "nerve":           nerve,
            "recommendations": self._build_recommendations(coverage, trust, obs_info),
        }

    def score(self, witness: FromSingleArtifactReasoningWitness) -> float:
        """Return the numeric score stored in *witness*, in [0.0, 1.0].

        Score interpretation::

            1.0 = complete coverage + maximum trust + no obstructions
            0.0 = zero coverage or complete obstruction
        """
        return witness.score

    def report(self, witness: FromSingleArtifactReasoningWitness) -> str:
        """Produce a human-readable text report of the witness.

        Parameters
        ----------
        witness : FromSingleArtifactReasoningWitness
            The certificate to report on.

        Returns
        -------
        str
            Multi-line text report suitable for printing to a terminal.
        """
        hc = witness.hypercover
        lines = [
            "=" * 72,
            "FromSingleArtifactReasoning — Project Geometry Assembly Report",
            f"  witness_id   : {witness.witness_id}",
            f"  created_at   : {witness.created_at}",
            f"  elapsed_s    : {witness.elapsed_s:.4f}",
            f"  score        : {witness.score:.4f}",
            f"  trust_met    : {witness.trust_met}",
            "-" * 72,
            f"  site         : {hc.site.name!r}  ({len(hc.site.root_coordinates)} coords)",
            f"  patches      : {len(hc.patches)}",
            f"  complete     : {hc.is_complete}",
            f"  global_trust : {hc.global_trust.value}",
            f"  obstructions : {len(witness.obstructions)}",
            "-" * 72,
        ]
        if witness.obstructions:
            lines.append("  OBSTRUCTIONS:")
            for obs in witness.obstructions:
                lines.append(f"    [{obs.obstruction_id}] {obs.cocycle_description}")
                for hint in obs.repair_hints:
                    lines.append(f"      → {hint}")
        uncovered = hc.uncovered_coordinates()
        if uncovered:
            lines.append(f"  UNCOVERED COORDINATES ({len(uncovered)}):")
            for c in uncovered:
                lines.append(f"    {c.label!r}  ({c.domain}/{c.aspect})")
        lines.append("=" * 72)
        return "\n".join(lines)

    def summarize(
        self, witness: FromSingleArtifactReasoningWitness
    ) -> dict[str, Any]:
        """Return a compact summary dict suitable for JSON serialisation.

        Parameters
        ----------
        witness : FromSingleArtifactReasoningWitness
            The certificate to summarise.

        Returns
        -------
        dict[str, Any]
            Keys: ``witness_id``, ``score``, ``is_complete``, ``global_trust``,
            ``n_patches``, ``n_obstructions``, ``trust_met``, ``elapsed_s``.
        """
        hc = witness.hypercover
        return {
            "witness_id":     witness.witness_id,
            "score":          round(witness.score, 6),
            "is_complete":    hc.is_complete,
            "global_trust":   hc.global_trust.value,
            "n_patches":      len(hc.patches),
            "n_obstructions": len(witness.obstructions),
            "trust_met":      witness.trust_met,
            "elapsed_s":      round(witness.elapsed_s, 6),
        }

    def compare(
        self,
        baseline: FromSingleArtifactReasoningWitness,
        candidate: FromSingleArtifactReasoningWitness,
    ) -> dict[str, Any]:
        """Compare two witnesses and return a delta summary.

        Useful for before/after analysis when patches are added or promoted.

        Parameters
        ----------
        baseline : FromSingleArtifactReasoningWitness
            The reference (before) witness.
        candidate : FromSingleArtifactReasoningWitness
            The candidate (after) witness.

        Returns
        -------
        dict[str, Any]
            Delta metrics: ``score_delta``, ``n_patches_delta``,
            ``obstruction_delta``, ``trust_improved``.
        """
        return {
            "score_delta":       candidate.score - baseline.score,
            "n_patches_delta":   (
                len(candidate.hypercover.patches)
                - len(baseline.hypercover.patches)
            ),
            "obstruction_delta": (
                len(candidate.obstructions) - len(baseline.obstructions)
            ),
            "trust_improved":    (
                candidate.hypercover.global_trust.dominates(
                    baseline.hypercover.global_trust
                )
            ),
        }

    def is_healthy(
        self, witness: FromSingleArtifactReasoningWitness
    ) -> bool:
        """Return True when the witness meets all health criteria.

        A healthy assembly satisfies ALL of:

        -   ``hypercover.is_complete`` is True.
        -   ``trust_met`` is True.
        -   No cohomology obstructions.
        -   Score ≥ ``min_score_threshold``.
        """
        return (
            witness.hypercover.is_complete
            and witness.trust_met
            and len(witness.obstructions) == 0
            and witness.score >= self.min_score_threshold
        )

    def tier_histogram(
        self, witness: FromSingleArtifactReasoningWitness
    ) -> dict[str, int]:
        """Return a histogram of trust tiers across all patches.

        Parameters
        ----------
        witness : FromSingleArtifactReasoningWitness
            The certificate to analyse.

        Returns
        -------
        dict[str, int]
            Maps TrustTier.value → count of patches at that tier.
        """
        hist: dict[str, int] = defaultdict(int)
        for p in witness.hypercover.patches:
            hist[p.trust_tier.value] += 1
        return dict(hist)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_coverage(self, hc: ProjectHypercover) -> dict[str, Any]:
        total   = len(hc.site.root_coordinates)
        covered = total - len(hc.uncovered_coordinates())
        return {
            "total_coords":    total,
            "covered_coords":  covered,
            "coverage_ratio":  covered / max(total, 1),
            "uncovered_ids":   [c.coord_id for c in hc.uncovered_coordinates()],
            "redundant_count": sum(
                1 for c in hc.site.root_coordinates
                if len(hc.patches_for_coordinate(c.coord_id)) > 1
            ),
        }

    def _analyze_trust(self, hc: ProjectHypercover) -> dict[str, Any]:
        tier_dist: dict[str, int] = defaultdict(int)
        for p in hc.patches:
            tier_dist[p.trust_tier.value] += 1
        return {
            "global_trust":      hc.global_trust.value,
            "tier_distribution": dict(tier_dist),
            "all_certified":     all(
                p.trust_tier in (TrustTier.CERTIFIED, TrustTier.CANONICAL)
                for p in hc.patches
            ),
        }

    def _analyze_obstructions(
        self, obstructions: Sequence[CohomologyObstruction]
    ) -> dict[str, Any]:
        return {
            "count":    len(obstructions),
            "ids":      [o.obstruction_id for o in obstructions],
            "critical": any(len(o.violating_patches) > 2 for o in obstructions),
        }

    def _build_recommendations(
        self,
        coverage: dict[str, Any],
        trust: dict[str, Any],
        obs_info: dict[str, Any],
    ) -> list[str]:
        recs: list[str] = []
        if coverage["coverage_ratio"] < 1.0:
            n = len(coverage["uncovered_ids"])
            recs.append(f"Add patches to cover {n} uncovered coordinate(s).")
        if not trust["all_certified"]:
            recs.append("Promote sub-CERTIFIED patches by supplying additional evidence.")
        if obs_info["count"] > 0:
            recs.append(
                f"Resolve {obs_info['count']} H¹ obstruction(s) before trust promotion."
            )
        if not recs:
            recs.append("Assembly is healthy; no action required.")
        return recs


# ---------------------------------------------------------------------------
# FromSingleArtifactReasoningWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FromSingleArtifactReasoningWitness:
    """Immutable output certificate for a single-artifact-to-project-geometry run.

    A *Witness* is the formal output of a Coordinator run: it certifies what
    was computed and provides all data needed to audit, reproduce, or extend
    the result.  Witnesses are fully serialisable and can be persisted to disk
    or transmitted between pipeline stages.

    Theory2.tex §8.1: a witness for hypercover assembly captures the full
    assembled Čech complex, any H¹ obstructions, and the trust floor achieved.

    Parameters
    ----------
    witness_id : str
        Unique 12-hex identifier for this certificate.
    hypercover : ProjectHypercover
        The assembled hypercover including all patch data and cocycle status.
    obstructions : tuple[CohomologyObstruction, ...]
        H¹ obstructions that could not be resolved during assembly.
    score : float
        Assembly quality score in [0.0, 1.0].
    trust_met : bool
        True when global_trust meets or exceeds the coordinator's required tier.
    elapsed_s : float
        Wall-clock seconds for the ``Coordinator.run()`` call.
    log_lines : tuple[str, ...]
        Ordered log lines emitted during the run; useful for debugging.
    created_at : str
        ISO-8601 timestamp of witness creation.

    Examples
    --------
    >>> w = coordinator.run(site, patches)
    >>> assert w.is_successful() or len(w.obstructions) > 0
    >>> serialised = json.dumps(w.to_dict())
    >>> w2 = FromSingleArtifactReasoningWitness.from_dict(json.loads(serialised))
    >>> w2.witness_id == w.witness_id
    True
    """

    witness_id   : str
    hypercover   : ProjectHypercover
    obstructions : tuple[CohomologyObstruction, ...]
    score        : float
    trust_met    : bool
    elapsed_s    : float
    log_lines    : tuple[str, ...]
    created_at   : str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "witness_id":   self.witness_id,
            "hypercover":   self.hypercover.to_dict(),
            "obstructions": [o.to_dict() for o in self.obstructions],
            "score":        self.score,
            "trust_met":    self.trust_met,
            "elapsed_s":    self.elapsed_s,
            "log_lines":    list(self.log_lines),
            "created_at":   self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FromSingleArtifactReasoningWitness:
        """Deserialise from a dict produced by ``to_dict()``."""
        return cls(
            witness_id   = d["witness_id"],
            hypercover   = ProjectHypercover.from_dict(d["hypercover"]),
            obstructions = tuple(
                CohomologyObstruction.from_dict(o) for o in d.get("obstructions", [])
            ),
            score        = float(d["score"]),
            trust_met    = bool(d["trust_met"]),
            elapsed_s    = float(d.get("elapsed_s", 0.0)),
            log_lines    = tuple(d.get("log_lines", [])),
            created_at   = d["created_at"],
        )

    def digest(self) -> str:
        """Content-hash of this witness (SHA-256 over canonical JSON, 24 hex)."""
        raw = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def is_successful(self) -> bool:
        """True when assembly is complete, trust was met, and no obstructions remain."""
        return (
            self.hypercover.is_complete
            and self.trust_met
            and len(self.obstructions) == 0
            and self.score > 0.0
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== FromSingleArtifactReasoningTo smoke test ===")

    # Build a 3-coordinate site representing a typical Python module
    coords = [
        ProjectCoordinate.make("security",    "input_validation"),
        ProjectCoordinate.make("correctness", "type_safety"),
        ProjectCoordinate.make("performance", "hot_path"),
    ]
    site = ProjectSite.make(
        "demo_project",
        coords,
        kind = ProjectKind.MODULAR,
    )

    patches = [
        ArtifactPatch.make(
            "src/auth.py",
            TrustTier.PROVISIONAL,
            [coords[0]],
            local_section = {"security/input_validation": "validated"},
        ),
        ArtifactPatch.make(
            "src/models.py",
            TrustTier.CORROBORATED,
            [coords[1]],
            local_section = {"correctness/type_safety": "typed"},
        ),
        ArtifactPatch.make(
            "src/engine.py",
            TrustTier.PROPOSAL,
            [coords[2]],
            obligations   = ["benchmark_required"],
        ),
    ]

    coord   = FromSingleArtifactReasoningCoordinator(required_trust=TrustTier.PROVISIONAL)
    errors  = coord.validate(site, patches)
    assert not errors, f"Validation errors: {errors}"

    witness  = coord.run(site, patches)
    analyzer = FromSingleArtifactReasoningAnalyzer()

    print(analyzer.report(witness))
    summary = analyzer.summarize(witness)
    assert summary["n_patches"] == 3, f"Expected 3 patches, got {summary['n_patches']}"
    assert summary["is_complete"] is True, "Expected complete coverage"

    # Round-trip serialisation
    reloaded = FromSingleArtifactReasoningWitness.from_dict(witness.to_dict())
    assert reloaded.witness_id == witness.witness_id
    assert reloaded.digest() == witness.digest()

    # Lift from single artifact
    _, hc2 = coord.lift_to_project_geometry(
        "src/all_in_one.py",
        ["security", "correctness", "performance"],
        TrustTier.PROPOSAL,
    )
    assert len(hc2.patches) == 3
    assert hc2.global_trust == TrustTier.PROPOSAL

    print("\nsmoke test PASSED")
    print(json.dumps(summary, indent=2))
    sys.exit(0)
