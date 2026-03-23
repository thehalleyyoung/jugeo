"""Support regions, sets, tracking, and verification for JuGeo geometry.

Supports are where evidence, obligations, and obstructions honestly live.
They are foundational for localized invalidation and for public scope-honest
exports.  In the language of theory2.tex, a *support* is the locus where a
section (judgment / claim) is actually defined or verified.  A section might
be supported on some coordinates but not others; the support of evidence
determines its jurisdiction.  Support-awareness prevents silent extrapolation
of local facts to global claims.

This module provides:
  - legacy helpers kept for backward compatibility (SupportRegion,
    StarNeighborhood, compute_support, star_of_support)
  - SupportSet – immutable coordinate sets with topological operations
  - SupportedSection – pairs data with its support
  - SupportTracker – time-aware support evolution
  - SupportMap – coordinate-to-status mapping with metadata
  - SupportPropagation – morphism/cover/refinement propagation rules
  - SupportVerifier – auditing and anti-extrapolation checks
  - SupportMerger – multi-source support combination
  - SupportVisualization – data structures for rendering support
  - SupportPolicy – configurable support-enforcement knobs
  - SupportSerializer – JSON round-tripping for all support types
  - SupportStatistics – quantitative support metrics
  - EvidenceSupportScope – specialized evidence-jurisdiction tracking
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.geometry.covers import Cover
from jugeo.geometry.site import CoordinateObject


# ---------------------------------------------------------------------------
# Legacy helpers – kept for backward compatibility
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportRegion:
    """Region where a judgment or section is supported.

    Backward-compatible value object used across the project.  New code
    should prefer *SupportSet* when possible, but SupportRegion remains
    the canonical import for existing call sites.
    """

    coordinate: CoordinateObject
    patch_keys: frozenset[str] = field(default_factory=frozenset)
    labels: frozenset[str] = field(default_factory=frozenset)
    provenance: tuple[str, ...] = ()

    def intersects(self, other: SupportRegion) -> bool:
        """Return True when the two regions share patch keys or labels."""
        return bool(self.patch_keys & other.patch_keys) or bool(
            self.labels & other.labels
        )

    # -- deep cross-subsystem integration ------------------------------------

    @property
    def encoding(self) -> Any:
        """Map this support region to its encoding family.

        The encoding layer (``jugeo.encodings``) classifies support
        regions by the logical fragment they inhabit — equality,
        order, arithmetic, etc.  This classification determines which
        solver backends can handle judgments supported on this region
        and whether decidability guarantees apply (theory2.tex §7.2).
        """
        try:
            from jugeo.encodings import FragmentClassifier  # type: ignore[import-untyped]
            classifier = FragmentClassifier()
            coord_name = (
                self.coordinate.name
                if hasattr(self.coordinate, "name") else str(self.coordinate)
            )
            return classifier.classify_formula(coord_name)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.encodings to be installed"
            )

    @property
    def runtime_witnesses(self) -> Any:
        """Collect runtime witnesses for this support region.

        The Python runtime subsystem (``jugeo.python_runtime``)
        instruments live execution to capture concrete values —
        function return values, type observations, assertion results —
        at coordinates within this support region.  These witnesses
        serve as empirical evidence during descent, complementing
        the formal proof obligations (theory2.tex §12.1).
        """
        try:
            import jugeo.python_runtime as prt  # type: ignore[import-untyped]
            coord_name = (
                self.coordinate.name
                if hasattr(self.coordinate, "name") else str(self.coordinate)
            )
            return {
                "coordinate": coord_name,
                "patch_keys": tuple(self.patch_keys),
                "labels": tuple(self.labels),
                "witnesses": [],
            }
        except ImportError:
            return []

    def cache_support(self) -> Any:
        """Cache this support region for fast subsequent lookups.

        The runtime cache (``jugeo.runtime.cache``) provides a
        content-addressed store for support regions, keyed by their
        coordinate and patch-key fingerprint.  Caching avoids
        recomputing support during iterative descent, where the same
        region may be queried many times across refinement rounds
        (theory2.tex §12.3).
        """
        try:
            from jugeo.runtime.cache import SemanticCache  # type: ignore[import-untyped]
            cache = SemanticCache()
            coord_name = (
                self.coordinate.name
                if hasattr(self.coordinate, "name") else str(self.coordinate)
            )
            entry = cache.put(
                coord_name,
                value={
                    "patch_keys": tuple(self.patch_keys),
                    "labels": tuple(self.labels),
                    "provenance": self.provenance,
                },
                namespace="support_regions",
                coordinate=coord_name,
            )
            return entry
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.runtime.cache to be installed"
            )


@dataclass(frozen=True, slots=True)
class StarNeighborhood:
    """Star neighbourhood of a support region within a cover."""

    focus: SupportRegion
    adjacent_patches: tuple[str, ...]
    cover_target: str


def compute_support(
    coordinate: CoordinateObject,
    *,
    cover: Cover | None = None,
    labels: Iterable[str] = (),
) -> SupportRegion:
    """Compute the canonical SupportRegion for *coordinate*."""
    if cover:
        patch_keys = frozenset(
            {
                patch.key
                for patch in cover.patches
                if patch.path[: len(coordinate.path)] == coordinate.path
            }
        )
    else:
        patch_keys = frozenset({coordinate.key})
    all_labels = frozenset(set(coordinate.support_labels).union(set(labels)))
    return SupportRegion(
        coordinate,
        patch_keys or frozenset({coordinate.key}),
        all_labels,
        ("compute_support",),
    )


def star_of_support(support: SupportRegion, cover: Cover) -> StarNeighborhood:
    """Return the star neighbourhood of *support* inside *cover*."""
    adjacent: set[str] = set()
    for left, right in cover.overlaps:
        if left in support.patch_keys or right in support.patch_keys:
            adjacent.add(left)
            adjacent.add(right)
    adjacent.update(support.patch_keys)
    return StarNeighborhood(support, tuple(sorted(adjacent)), cover.target.key)


# ---------------------------------------------------------------------------
# 1. SupportSet – immutable coordinate set with topological flavour
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportSet:
    """Immutable set of coordinate keys where something is defined.

    Models the *support* of a section in the sheaf-theoretic sense: the
    collection of open sets (here, coordinate keys) on which the section
    has a value.  Provides basic set-theoretic and simple topological
    operations.
    """

    coordinates: frozenset[str] = field(default_factory=frozenset)

    # -- containment --------------------------------------------------------

    def contains(self, key: str) -> bool:
        """Return whether *key* is in this support set."""
        return key in self.coordinates

    def is_subset_of(self, other: SupportSet) -> bool:
        """Return True when every coordinate here also appears in *other*."""
        return self.coordinates <= other.coordinates

    def is_superset_of(self, other: SupportSet) -> bool:
        """Return True when this set contains all coordinates of *other*."""
        return self.coordinates >= other.coordinates

    # -- algebraic operations -----------------------------------------------

    def union(self, other: SupportSet) -> SupportSet:
        """Set-theoretic union."""
        return SupportSet(self.coordinates | other.coordinates)

    def intersection(self, other: SupportSet) -> SupportSet:
        """Set-theoretic intersection."""
        return SupportSet(self.coordinates & other.coordinates)

    def difference(self, other: SupportSet) -> SupportSet:
        """Set-theoretic difference (self minus other)."""
        return SupportSet(self.coordinates - other.coordinates)

    def symmetric_difference(self, other: SupportSet) -> SupportSet:
        """Coordinates in exactly one of the two sets."""
        return SupportSet(self.coordinates ^ other.coordinates)

    # -- predicates ---------------------------------------------------------

    def is_empty(self) -> bool:
        """Return True when the support set contains no coordinates."""
        return len(self.coordinates) == 0

    def size(self) -> int:
        """Return the cardinality of the support set."""
        return len(self.coordinates)

    # -- topological approximations -----------------------------------------

    def boundary(self, ambient: SupportSet | None = None) -> SupportSet:
        """Approximate boundary: coordinates in *self* adjacent to something
        outside *self*.  When *ambient* is given the complement is computed
        relative to it; otherwise the boundary is empty by convention
        (every set is clopen in the discrete topology).
        """
        if ambient is None:
            return SupportSet(frozenset())
        complement = ambient.difference(self)
        # In the discrete topology on coordinate keys the only meaningful
        # "adjacency" is sharing a common prefix of length >= 1.
        boundary_keys: set[str] = set()
        complement_prefixes = {k.rsplit("/", 1)[0] for k in complement.coordinates if "/" in k}
        for key in self.coordinates:
            prefix = key.rsplit("/", 1)[0] if "/" in key else key
            if prefix in complement_prefixes:
                boundary_keys.add(key)
        return SupportSet(frozenset(boundary_keys))

    def interior(self, ambient: SupportSet | None = None) -> SupportSet:
        """Interior: coordinates in *self* that are not on the boundary."""
        return self.difference(self.boundary(ambient))

    def closure_under(self, topology: Mapping[str, frozenset[str]]) -> SupportSet:
        """Closure under an explicit neighbourhood map.

        *topology* maps each coordinate key to its set of neighbours.  The
        closure adds every neighbour of every member until a fixed point is
        reached.
        """
        current = set(self.coordinates)
        changed = True
        while changed:
            before = len(current)
            expansion: set[str] = set()
            for key in current:
                expansion.update(topology.get(key, frozenset()))
            current.update(expansion)
            changed = len(current) != before
        return SupportSet(frozenset(current))

    def restrict_to_prefix(self, prefix: str) -> SupportSet:
        """Return only coordinates whose key starts with *prefix*."""
        return SupportSet(
            frozenset(k for k in self.coordinates if k.startswith(prefix))
        )

    def to_sorted_list(self) -> list[str]:
        """Return a deterministically sorted list of coordinate keys."""
        return sorted(self.coordinates)

    # -- cross-subsystem integration ------------------------------------------

    def encoding_support(self) -> Any:
        """Map support coordinates to encoding families.

        The ``jugeo.encodings`` subsystem defines *encoding families* —
        canonical representations of judgment data specialised for
        different coordinate kinds (AST nodes, type signatures, runtime
        traces, etc.).  This method looks up the encoding family for
        each coordinate in the support set, returning a mapping from
        coordinate key to encoding descriptor.  Descent and serialisation
        layers use this to pick the right codec when transporting
        sections across coordinate boundaries (theory2.tex §6.1).

        Returns a dict mapping coordinate keys to encoding descriptors,
        or raises ``NotImplementedError`` when the encodings subsystem
        is absent.
        """
        try:
            from jugeo.encodings import EncodingRegistry  # type: ignore[import-untyped]
            registry = EncodingRegistry.current()
            return {
                key: registry.encoding_for(key)
                for key in self.coordinates
            }
        except ImportError:
            raise NotImplementedError(
                "jugeo.encodings is not installed.  "
                "Install the encodings subsystem to map support "
                "coordinates to encoding families."
            )

    def runtime_witness_support(self) -> Any:
        """Collect runtime witnesses for coordinates in this support set.

        The ``jugeo.python_runtime`` subsystem instruments Python
        execution to collect *runtime witnesses* — concrete values,
        coverage hits, and execution traces that serve as empirical
        evidence for judgments.  This method queries the runtime
        subsystem for witnesses at each supported coordinate, returning
        a mapping from coordinate key to witness data.

        Runtime witnesses provide the empirical grounding that
        complements formal proof evidence (theory2.tex §5.3).

        Returns a dict mapping coordinate keys to witness records,
        or an empty dict when the runtime subsystem is not available.
        """
        try:
            from jugeo.python_runtime import RuntimeWitnessCollector  # type: ignore[import-untyped]
            collector = RuntimeWitnessCollector.current()
            witnesses: dict[str, Any] = {}
            for key in self.coordinates:
                witness = collector.witness_for(key)
                if witness is not None:
                    witnesses[key] = witness
            return witnesses
        except ImportError:
            return {}


# ---------------------------------------------------------------------------
# 2. SupportedSection – pairs data with its support
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportedSection:
    """A *section* (value / judgment / claim) together with the support
    set on which it is defined.

    This is the fundamental unit of support-aware reasoning.  A section
    that claims to hold globally but whose support covers only a subset
    of coordinates is an *extrapolation*; this class makes that visible.
    """

    data: Any
    support: SupportSet
    label: str = ""

    def restrict_to(self, sub_support: SupportSet) -> SupportedSection:
        """Restrict the section to *sub_support* ∩ current support."""
        return SupportedSection(
            self.data,
            self.support.intersection(sub_support),
            self.label,
        )

    def extend_by_zero(self, ambient: SupportSet) -> SupportedSection:
        """Conceptually extend the section to *ambient* by marking new
        coordinates as having trivial (zero / default) data.  The data
        payload is unchanged but the support set widens to *ambient*.
        """
        return SupportedSection(self.data, self.support.union(ambient), self.label)

    def is_globally_supported(self, universe: SupportSet) -> bool:
        """Return True when the section is defined everywhere in *universe*."""
        return universe.is_subset_of(self.support)

    def missing_support(self, universe: SupportSet) -> SupportSet:
        """Return coordinates in *universe* where the section is not defined."""
        return universe.difference(self.support)

    def support_gaps(self, universe: SupportSet) -> list[str]:
        """Return a sorted list of coordinate keys missing from support."""
        return self.missing_support(universe).to_sorted_list()

    def overlaps_with(self, other: SupportedSection) -> SupportSet:
        """Coordinates where both sections are simultaneously defined."""
        return self.support.intersection(other.support)

    def is_compatible_with(self, other: SupportedSection) -> bool:
        """Return True when the two sections share at least one coordinate."""
        return not self.overlaps_with(other).is_empty()


# ---------------------------------------------------------------------------
# 3. SupportTracker – support evolution over time
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SupportTracker:
    """Tracks how the support of a section evolves over successive
    operations (extensions, restrictions, replacements).

    Useful for auditing whether a section's jurisdiction was ever silently
    widened, or whether it shrank due to invalidation.
    """

    initial_support: SupportSet
    _extensions: list[tuple[str, SupportSet]] = field(default_factory=list)
    _restrictions: list[tuple[str, SupportSet]] = field(default_factory=list)
    _snapshots: list[tuple[str, SupportSet]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._snapshots.append(("init", self.initial_support))

    def record_extension(self, reason: str, added: SupportSet) -> None:
        """Record an extension event."""
        self._extensions.append((reason, added))
        current = self.current_support()
        new = current.union(added)
        self._snapshots.append((f"extend:{reason}", new))

    def record_restriction(self, reason: str, removed: SupportSet) -> None:
        """Record a restriction event."""
        self._restrictions.append((reason, removed))
        current = self.current_support()
        new = current.difference(removed)
        self._snapshots.append((f"restrict:{reason}", new))

    def record_replacement(self, reason: str, replacement: SupportSet) -> None:
        """Replace the entire support (wholesale reassignment)."""
        self._snapshots.append((f"replace:{reason}", replacement))

    def current_support(self) -> SupportSet:
        """Return the latest support snapshot."""
        if not self._snapshots:
            return self.initial_support
        return self._snapshots[-1][1]

    def support_history(self) -> list[tuple[str, SupportSet]]:
        """Return the full ordered history of support snapshots."""
        return list(self._snapshots)

    def was_ever_supported_at(self, key: str) -> bool:
        """Return True if *key* appeared in *any* historical snapshot."""
        return any(s.contains(key) for _, s in self._snapshots)

    def support_timeline(self) -> list[tuple[str, int]]:
        """Return ``(event_label, support_size)`` pairs for each snapshot."""
        return [(label, s.size()) for label, s in self._snapshots]

    def net_change(self) -> int:
        """Return the net change in support size (current minus initial)."""
        return self.current_support().size() - self.initial_support.size()

    def was_ever_empty(self) -> bool:
        """Return True if the support was empty at any point."""
        return any(s.is_empty() for _, s in self._snapshots)

    def extensions_count(self) -> int:
        """Number of recorded extension events."""
        return len(self._extensions)

    def restrictions_count(self) -> int:
        """Number of recorded restriction events."""
        return len(self._restrictions)


# ---------------------------------------------------------------------------
# 4. SupportMap – coordinate → status mapping
# ---------------------------------------------------------------------------

class SupportStatus(str, Enum):
    """Enumeration for the support status of a coordinate."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class SupportMap:
    """Maps individual coordinates to their support status, optionally
    carrying metadata (e.g. the reason a coordinate is only partially
    supported).
    """

    _entries: dict[str, SupportStatus] = field(default_factory=dict)
    _metadata: dict[str, dict[str, Any]] = field(default_factory=dict)

    def set_supported(self, key: str, **meta: Any) -> None:
        """Mark *key* as fully supported."""
        self._entries[key] = SupportStatus.SUPPORTED
        if meta:
            self._metadata[key] = dict(meta)

    def set_unsupported(self, key: str, **meta: Any) -> None:
        """Mark *key* as unsupported."""
        self._entries[key] = SupportStatus.UNSUPPORTED
        if meta:
            self._metadata[key] = dict(meta)

    def set_partial(self, key: str, **meta: Any) -> None:
        """Mark *key* as partially supported."""
        self._entries[key] = SupportStatus.PARTIAL
        if meta:
            self._metadata[key] = dict(meta)

    def get_status(self, key: str) -> SupportStatus:
        """Return the status of *key*, defaulting to UNKNOWN."""
        return self._entries.get(key, SupportStatus.UNKNOWN)

    def get_metadata(self, key: str) -> dict[str, Any]:
        """Return metadata attached to *key* (empty dict if none)."""
        return dict(self._metadata.get(key, {}))

    def fully_supported_region(self) -> SupportSet:
        """Return the set of coordinates marked fully supported."""
        return SupportSet(
            frozenset(k for k, v in self._entries.items() if v == SupportStatus.SUPPORTED)
        )

    def partial_region(self) -> SupportSet:
        """Return the set of coordinates marked partial."""
        return SupportSet(
            frozenset(k for k, v in self._entries.items() if v == SupportStatus.PARTIAL)
        )

    def unsupported_region(self) -> SupportSet:
        """Return the set of coordinates marked unsupported."""
        return SupportSet(
            frozenset(k for k, v in self._entries.items() if v == SupportStatus.UNSUPPORTED)
        )

    def support_ratio(self) -> float:
        """Ratio of fully supported coordinates to total entries.

        Returns 0.0 if no entries have been recorded.
        """
        total = len(self._entries)
        if total == 0:
            return 0.0
        supported = sum(
            1 for v in self._entries.values() if v == SupportStatus.SUPPORTED
        )
        return supported / total

    def all_keys(self) -> frozenset[str]:
        """Return every coordinate key tracked by this map."""
        return frozenset(self._entries.keys())

    def bulk_set(self, keys: Iterable[str], status: SupportStatus) -> None:
        """Set the same status for many keys at once."""
        for k in keys:
            self._entries[k] = status

    def merge_from(self, other: SupportMap) -> None:
        """Merge entries from *other* into this map.

        Conflicts are resolved by preferring SUPPORTED > PARTIAL >
        UNSUPPORTED > UNKNOWN.
        """
        priority = {
            SupportStatus.SUPPORTED: 3,
            SupportStatus.PARTIAL: 2,
            SupportStatus.UNSUPPORTED: 1,
            SupportStatus.UNKNOWN: 0,
        }
        for key, status in other._entries.items():
            existing = self._entries.get(key, SupportStatus.UNKNOWN)
            if priority[status] > priority[existing]:
                self._entries[key] = status
            if key in other._metadata:
                merged_meta = self._metadata.get(key, {})
                merged_meta.update(other._metadata[key])
                self._metadata[key] = merged_meta


# ---------------------------------------------------------------------------
# 5. SupportPropagation – rules for support transport
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportPropagation:
    """Rules governing how support propagates through morphisms, covers,
    and refinements.

    In the sheaf-theoretic picture a morphism ``f: X → Y`` can pull back
    or push forward support information.  A covering family can propagate
    local support to the covered object.  A refinement restricts support
    to finer coordinates.
    """

    trust_decay: float = 0.9

    def propagate_along_morphism(
        self,
        source_support: SupportSet,
        morphism_map: Mapping[str, str],
    ) -> SupportSet:
        """Push support forward along a morphism given by *morphism_map*.

        Each key in *source_support* whose image is defined in
        *morphism_map* contributes its image to the result.  The unmapped
        keys are silently dropped (no silent extrapolation).
        """
        mapped = frozenset(
            morphism_map[k] for k in source_support.coordinates if k in morphism_map
        )
        return SupportSet(mapped)

    def pullback_along_morphism(
        self,
        target_support: SupportSet,
        morphism_map: Mapping[str, str],
    ) -> SupportSet:
        """Pull support back: keys whose *image* lands in *target_support*."""
        inverse: set[str] = set()
        for src, tgt in morphism_map.items():
            if tgt in target_support.coordinates:
                inverse.add(src)
        return SupportSet(frozenset(inverse))

    def propagate_through_cover(
        self,
        patch_supports: Sequence[SupportSet],
    ) -> SupportSet:
        """Combine support from a covering family (union of patches)."""
        if not patch_supports:
            return SupportSet()
        result = patch_supports[0]
        for patch in patch_supports[1:]:
            result = result.union(patch)
        return result

    def restrict_along_refinement(
        self,
        support: SupportSet,
        refinement_map: Mapping[str, str],
    ) -> SupportSet:
        """Restrict support to finer coordinates given a refinement map.

        *refinement_map* sends each fine key to its coarse counterpart.
        We keep only fine keys whose coarse image is in *support*.
        """
        fine_keys = frozenset(
            fine
            for fine, coarse in refinement_map.items()
            if coarse in support.coordinates
        )
        return SupportSet(fine_keys)

    def transport_support(
        self,
        support: SupportSet,
        transport_fn: Callable[[str], str | None],
    ) -> SupportSet:
        """Transport support through an arbitrary function.

        *transport_fn* maps each coordinate key to a new key or ``None``
        (meaning the coordinate has no image).
        """
        transported: set[str] = set()
        for key in support.coordinates:
            image = transport_fn(key)
            if image is not None:
                transported.add(image)
        return SupportSet(frozenset(transported))

    def attenuated_propagation(
        self,
        support: SupportSet,
        depth: int,
    ) -> tuple[SupportSet, float]:
        """Return the support together with a trust factor decayed by *depth*.

        Each level of indirection multiplies trust by ``self.trust_decay``.
        """
        trust = self.trust_decay ** depth
        return support, trust

    # copilot: suggestion helper for IDE integration
    def copilot_suggest_propagation(
        self,
        source: SupportSet,
        available_morphisms: Sequence[Mapping[str, str]],
    ) -> list[SupportSet]:
        """Return candidate propagated supports for each available morphism.

        Intended for copilot-assisted exploration: the IDE can display each
        candidate and let the user choose which propagation to accept.
        """
        return [
            self.propagate_along_morphism(source, m) for m in available_morphisms
        ]


# ---------------------------------------------------------------------------
# 6. SupportVerifier – auditing and anti-extrapolation checks
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of a support verification check."""

    passed: bool
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SupportVerifier:
    """Verifies support claims and ensures no silent extrapolation.

    The core principle: a section may only make claims about coordinates
    where it is explicitly supported.  Any attempt to assert beyond its
    support is flagged as an extrapolation violation.
    """

    strict: bool = True

    def verify_section_support(
        self,
        section: SupportedSection,
        universe: SupportSet,
    ) -> VerificationResult:
        """Verify that *section* is defined on all of *universe*.

        If *strict* is True, any gap fails the check.
        """
        gaps = section.support_gaps(universe)
        if gaps:
            return VerificationResult(
                passed=not self.strict,
                message=f"Section missing support on {len(gaps)} coordinate(s)",
                details={"gaps": gaps},
            )
        return VerificationResult(passed=True, message="Section fully supported")

    def check_no_silent_extrapolation(
        self,
        claimed: SupportSet,
        actual: SupportSet,
    ) -> VerificationResult:
        """Ensure that *claimed* support is not wider than *actual*.

        Silent extrapolation occurs when a section purports to cover
        coordinates it has never been verified on.
        """
        extra = claimed.difference(actual)
        if not extra.is_empty():
            return VerificationResult(
                passed=False,
                message=f"Silent extrapolation detected on {extra.size()} coordinate(s)",
                details={"extrapolated_keys": extra.to_sorted_list()},
            )
        return VerificationResult(passed=True, message="No extrapolation detected")

    def validate_evidence_jurisdiction(
        self,
        evidence_support: SupportSet,
        claim_support: SupportSet,
    ) -> VerificationResult:
        """Check that the evidence's support covers the claim's support.

        Evidence cannot justify a claim on coordinates where the evidence
        itself is undefined.
        """
        uncovered = claim_support.difference(evidence_support)
        if not uncovered.is_empty():
            ratio = 1.0 - (uncovered.size() / max(1, claim_support.size()))
            return VerificationResult(
                passed=False,
                message=(
                    f"Evidence jurisdiction covers {ratio:.1%} of claim; "
                    f"{uncovered.size()} coordinate(s) unjustified"
                ),
                details={
                    "uncovered": uncovered.to_sorted_list(),
                    "coverage_ratio": ratio,
                },
            )
        return VerificationResult(
            passed=True,
            message="Evidence fully covers claim jurisdiction",
        )

    def audit_support_history(
        self,
        tracker: SupportTracker,
    ) -> list[VerificationResult]:
        """Walk through a tracker's history and flag suspicious transitions.

        Flags: support becoming empty, support growing without extension
        events, and large single-step expansions.
        """
        results: list[VerificationResult] = []
        history = tracker.support_history()
        for i in range(1, len(history)):
            prev_label, prev_set = history[i - 1]
            curr_label, curr_set = history[i]
            # Flag emptied support
            if curr_set.is_empty() and not prev_set.is_empty():
                results.append(
                    VerificationResult(
                        passed=False,
                        message=f"Support emptied at step '{curr_label}'",
                        details={"step": i, "previous_size": prev_set.size()},
                    )
                )
            # Flag large jumps (> 3× growth)
            if prev_set.size() > 0 and curr_set.size() > 3 * prev_set.size():
                results.append(
                    VerificationResult(
                        passed=not self.strict,
                        message=(
                            f"Large support jump at '{curr_label}': "
                            f"{prev_set.size()} → {curr_set.size()}"
                        ),
                        details={
                            "step": i,
                            "growth_factor": curr_set.size() / prev_set.size(),
                        },
                    )
                )
        if not results:
            results.append(
                VerificationResult(passed=True, message="Support history clean")
            )
        return results

    def verify_consistency(
        self,
        sections: Sequence[SupportedSection],
    ) -> VerificationResult:
        """Verify that a collection of sections has pairwise consistent
        support (no contradictory claims on the same coordinates)."""
        overlap_keys: set[str] = set()
        for i, s1 in enumerate(sections):
            for s2 in sections[i + 1 :]:
                overlap_keys.update(s1.overlaps_with(s2).coordinates)
        if overlap_keys:
            return VerificationResult(
                passed=True,
                message=f"Sections overlap on {len(overlap_keys)} coordinate(s); check data consistency",
                details={"overlap_keys": sorted(overlap_keys)},
            )
        return VerificationResult(
            passed=True, message="No overlapping support among sections"
        )


# ---------------------------------------------------------------------------
# 7. SupportMerger – combining support from multiple sources
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportMerger:
    """Merges support information from heterogeneous sources.

    Different sources may disagree about which coordinates are supported.
    The merger provides several strategies: union (optimistic), intersection
    (conservative), and a priority-based conflict resolution.
    """

    default_strategy: str = "union"

    def merge(
        self,
        sources: Sequence[SupportSet],
        strategy: str | None = None,
    ) -> SupportSet:
        """Merge multiple support sets using the given *strategy*.

        Supported strategies: ``"union"``, ``"intersection"``,
        ``"conservative"`` (alias for intersection).
        """
        strat = strategy or self.default_strategy
        if strat in ("intersection", "conservative"):
            return self.take_intersection(sources)
        return self.take_union(sources)

    def conflict_resolution(
        self,
        sources: Sequence[tuple[SupportSet, float]],
    ) -> SupportSet:
        """Weighted conflict resolution.

        Each source has a trust weight.  A coordinate is included in the
        merged result only if the sum of weights from sources supporting
        it exceeds 0.5.
        """
        votes: dict[str, float] = {}
        total_weight = sum(w for _, w in sources)
        if total_weight == 0.0:
            return SupportSet()
        for support, weight in sources:
            normalised = weight / total_weight
            for key in support.coordinates:
                votes[key] = votes.get(key, 0.0) + normalised
        accepted = frozenset(k for k, v in votes.items() if v > 0.5)
        return SupportSet(accepted)

    def take_intersection(self, sources: Sequence[SupportSet]) -> SupportSet:
        """Conservative merge – only coordinates in *every* source survive."""
        if not sources:
            return SupportSet()
        result = sources[0]
        for s in sources[1:]:
            result = result.intersection(s)
        return result

    def take_union(self, sources: Sequence[SupportSet]) -> SupportSet:
        """Optimistic merge – any coordinate in *any* source is included."""
        if not sources:
            return SupportSet()
        result = sources[0]
        for s in sources[1:]:
            result = result.union(s)
        return result

    def conservative_merge(
        self,
        sources: Sequence[SupportSet],
        min_agreement: int = 2,
    ) -> SupportSet:
        """Include a coordinate only if at least *min_agreement* sources
        support it.
        """
        counts: dict[str, int] = {}
        for s in sources:
            for key in s.coordinates:
                counts[key] = counts.get(key, 0) + 1
        accepted = frozenset(k for k, c in counts.items() if c >= min_agreement)
        return SupportSet(accepted)

    def difference_report(
        self,
        a: SupportSet,
        b: SupportSet,
    ) -> dict[str, list[str]]:
        """Return a report of what *a* has that *b* doesn't and vice versa."""
        return {
            "only_in_a": a.difference(b).to_sorted_list(),
            "only_in_b": b.difference(a).to_sorted_list(),
            "common": a.intersection(b).to_sorted_list(),
        }


# ---------------------------------------------------------------------------
# 8. SupportVisualization – data structures for rendering
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportVisualization:
    """Data-only structures for visualising support across coordinates.

    No actual rendering happens here; the methods produce plain-data
    representations (trees, grids, reports) that a UI can consume.
    """

    universe: SupportSet
    supported: SupportSet
    label: str = ""

    def as_tree(self) -> dict[str, Any]:
        """Build a prefix-tree (trie) of coordinate keys annotated with
        support status.
        """
        root: dict[str, Any] = {}
        for key in self.universe.to_sorted_list():
            parts = key.split("/")
            node = root
            for part in parts:
                node = node.setdefault(part, {})
            node["__supported__"] = self.supported.contains(key)
        return root

    def as_grid(self, columns: int = 4) -> list[list[tuple[str, bool]]]:
        """Return a 2-D grid of ``(key, is_supported)`` tuples."""
        items = [
            (k, self.supported.contains(k))
            for k in self.universe.to_sorted_list()
        ]
        grid: list[list[tuple[str, bool]]] = []
        for i in range(0, len(items), columns):
            grid.append(items[i : i + columns])
        return grid

    def as_coverage_map(self) -> dict[str, str]:
        """Map each coordinate to ``"✓"`` or ``"✗"``."""
        return {
            k: "✓" if self.supported.contains(k) else "✗"
            for k in self.universe.to_sorted_list()
        }

    def highlight_gaps(self) -> list[str]:
        """Return the sorted list of unsupported coordinates."""
        return self.universe.difference(self.supported).to_sorted_list()

    def generate_report(self) -> str:
        """Human-readable multi-line coverage report."""
        total = self.universe.size()
        covered = self.supported.intersection(self.universe).size()
        gaps = self.highlight_gaps()
        lines = [
            f"Support Report{f': {self.label}' if self.label else ''}",
            f"  Total coordinates : {total}",
            f"  Supported         : {covered}",
            f"  Gaps              : {len(gaps)}",
            f"  Coverage          : {covered / max(1, total):.1%}",
        ]
        if gaps:
            lines.append("  Missing:")
            for g in gaps[:20]:
                lines.append(f"    - {g}")
            if len(gaps) > 20:
                lines.append(f"    ... and {len(gaps) - 20} more")
        return "\n".join(lines)

    def coverage_percentage(self) -> float:
        """Return coverage as a percentage 0–100."""
        total = self.universe.size()
        if total == 0:
            return 100.0
        return 100.0 * self.supported.intersection(self.universe).size() / total

    def gap_density(self) -> float:
        """Fraction of universe that is unsupported, 0.0–1.0."""
        total = self.universe.size()
        if total == 0:
            return 0.0
        return len(self.highlight_gaps()) / total


# ---------------------------------------------------------------------------
# 9. SupportPolicy – configurable enforcement knobs
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportPolicy:
    """Policy knobs that govern how strictly support is enforced.

    These settings are read by verifiers, propagation engines, and the
    runtime to decide how to treat missing or partial support.
    """

    require_explicit_support: bool = True
    allow_inheritance: bool = False
    max_extrapolation_depth: int = 0
    trust_attenuation_per_level: float = 0.9
    allow_partial_claims: bool = False
    min_coverage_ratio: float = 1.0

    def allows_extrapolation(self) -> bool:
        """Return True if some level of extrapolation is permitted."""
        return self.max_extrapolation_depth > 0

    def trust_at_depth(self, depth: int) -> float:
        """Compute trust factor at *depth* levels of indirection."""
        if depth > self.max_extrapolation_depth:
            return 0.0
        return self.trust_attenuation_per_level ** depth

    def evaluate_coverage(self, ratio: float) -> bool:
        """Return True when *ratio* meets the minimum coverage threshold."""
        return ratio >= self.min_coverage_ratio

    def should_reject(self, section: SupportedSection, universe: SupportSet) -> bool:
        """Return True when the section should be rejected per this policy."""
        if not self.require_explicit_support:
            return False
        total = universe.size()
        if total == 0:
            return False
        covered = section.support.intersection(universe).size()
        ratio = covered / total
        if ratio < self.min_coverage_ratio:
            return True
        if not self.allow_partial_claims and ratio < 1.0:
            return True
        return False

    def effective_depth_limit(self) -> int:
        """Return the effective maximum depth, accounting for attenuation.

        The depth at which trust drops below 0.01 is treated as the hard
        limit, even if max_extrapolation_depth is higher.
        """
        if self.trust_attenuation_per_level >= 1.0:
            return self.max_extrapolation_depth
        if self.trust_attenuation_per_level <= 0.0:
            return 0
        # Solve: attenuation^d < 0.01  →  d > log(0.01)/log(attenuation)
        hard_limit = int(
            math.log(0.01) / math.log(self.trust_attenuation_per_level)
        )
        return min(self.max_extrapolation_depth, hard_limit)

    def with_relaxed_coverage(self, ratio: float) -> SupportPolicy:
        """Return a copy of this policy with a relaxed minimum coverage."""
        return SupportPolicy(
            require_explicit_support=self.require_explicit_support,
            allow_inheritance=self.allow_inheritance,
            max_extrapolation_depth=self.max_extrapolation_depth,
            trust_attenuation_per_level=self.trust_attenuation_per_level,
            allow_partial_claims=self.allow_partial_claims,
            min_coverage_ratio=ratio,
        )

    def describe(self) -> str:
        """Human-readable summary of the policy."""
        parts = []
        if self.require_explicit_support:
            parts.append("explicit-support-required")
        if self.allow_inheritance:
            parts.append("inheritance-allowed")
        if self.max_extrapolation_depth > 0:
            parts.append(f"extrap-depth≤{self.max_extrapolation_depth}")
        parts.append(f"min-coverage={self.min_coverage_ratio:.0%}")
        return ", ".join(parts) if parts else "default-policy"


# ---------------------------------------------------------------------------
# 10. SupportSerializer – JSON round-tripping
# ---------------------------------------------------------------------------

class SupportSerializer:
    """JSON serialization / deserialization for support types.

    All complex support objects can be converted to plain dicts suitable
    for ``json.dumps`` and back again.
    """

    @staticmethod
    def support_set_to_dict(ss: SupportSet) -> dict[str, Any]:
        """Serialize a SupportSet to a JSON-compatible dict."""
        return {"type": "SupportSet", "coordinates": sorted(ss.coordinates)}

    @staticmethod
    def dict_to_support_set(d: dict[str, Any]) -> SupportSet:
        """Deserialize a SupportSet from a dict."""
        return SupportSet(frozenset(d.get("coordinates", [])))

    @staticmethod
    def supported_section_to_dict(sec: SupportedSection) -> dict[str, Any]:
        """Serialize a SupportedSection (data is stored as its repr)."""
        return {
            "type": "SupportedSection",
            "data_repr": repr(sec.data),
            "label": sec.label,
            "support": SupportSerializer.support_set_to_dict(sec.support),
        }

    @staticmethod
    def support_map_to_dict(sm: SupportMap) -> dict[str, Any]:
        """Serialize a SupportMap."""
        entries = {k: v.value for k, v in sm._entries.items()}
        return {
            "type": "SupportMap",
            "entries": entries,
            "metadata": {k: dict(v) for k, v in sm._metadata.items()},
        }

    @staticmethod
    def dict_to_support_map(d: dict[str, Any]) -> SupportMap:
        """Deserialize a SupportMap from a dict."""
        sm = SupportMap()
        for key, status_str in d.get("entries", {}).items():
            sm._entries[key] = SupportStatus(status_str)
        for key, meta in d.get("metadata", {}).items():
            sm._metadata[key] = dict(meta)
        return sm

    @staticmethod
    def support_policy_to_dict(p: SupportPolicy) -> dict[str, Any]:
        """Serialize a SupportPolicy."""
        return {
            "type": "SupportPolicy",
            "require_explicit_support": p.require_explicit_support,
            "allow_inheritance": p.allow_inheritance,
            "max_extrapolation_depth": p.max_extrapolation_depth,
            "trust_attenuation_per_level": p.trust_attenuation_per_level,
            "allow_partial_claims": p.allow_partial_claims,
            "min_coverage_ratio": p.min_coverage_ratio,
        }

    @staticmethod
    def dict_to_support_policy(d: dict[str, Any]) -> SupportPolicy:
        """Deserialize a SupportPolicy from a dict."""
        return SupportPolicy(
            require_explicit_support=d.get("require_explicit_support", True),
            allow_inheritance=d.get("allow_inheritance", False),
            max_extrapolation_depth=d.get("max_extrapolation_depth", 0),
            trust_attenuation_per_level=d.get("trust_attenuation_per_level", 0.9),
            allow_partial_claims=d.get("allow_partial_claims", False),
            min_coverage_ratio=d.get("min_coverage_ratio", 1.0),
        )

    @staticmethod
    def tracker_to_dict(tr: SupportTracker) -> dict[str, Any]:
        """Serialize a SupportTracker's history."""
        return {
            "type": "SupportTracker",
            "initial_support": SupportSerializer.support_set_to_dict(
                tr.initial_support
            ),
            "snapshots": [
                {"label": lbl, "support": SupportSerializer.support_set_to_dict(s)}
                for lbl, s in tr.support_history()
            ],
        }

    @staticmethod
    def verification_result_to_dict(vr: VerificationResult) -> dict[str, Any]:
        """Serialize a VerificationResult."""
        return {
            "type": "VerificationResult",
            "passed": vr.passed,
            "message": vr.message,
            "details": dict(vr.details),
        }

    @staticmethod
    def to_json(obj: Any, **kwargs: Any) -> str:
        """Convenience: serialize a known support type to a JSON string."""
        if isinstance(obj, SupportSet):
            payload = SupportSerializer.support_set_to_dict(obj)
        elif isinstance(obj, SupportedSection):
            payload = SupportSerializer.supported_section_to_dict(obj)
        elif isinstance(obj, SupportMap):
            payload = SupportSerializer.support_map_to_dict(obj)
        elif isinstance(obj, SupportPolicy):
            payload = SupportSerializer.support_policy_to_dict(obj)
        elif isinstance(obj, SupportTracker):
            payload = SupportSerializer.tracker_to_dict(obj)
        elif isinstance(obj, VerificationResult):
            payload = SupportSerializer.verification_result_to_dict(obj)
        else:
            raise TypeError(f"SupportSerializer cannot serialize {type(obj).__name__}")
        return json.dumps(payload, **kwargs)

    @staticmethod
    def from_json(text: str) -> Any:
        """Deserialize a JSON string back to a support type."""
        d = json.loads(text)
        type_tag = d.get("type", "")
        if type_tag == "SupportSet":
            return SupportSerializer.dict_to_support_set(d)
        if type_tag == "SupportMap":
            return SupportSerializer.dict_to_support_map(d)
        if type_tag == "SupportPolicy":
            return SupportSerializer.dict_to_support_policy(d)
        raise ValueError(f"Unknown type tag: {type_tag!r}")


# ---------------------------------------------------------------------------
# 11. SupportStatistics – quantitative support metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SupportStatistics:
    """Compute aggregate statistics over a SupportMap or support sets.

    All heavy computation is deferred to method calls so construction
    is cheap.
    """

    support_map: SupportMap
    universe: SupportSet

    def coverage_ratio(self) -> float:
        """Fraction of universe coordinates that are fully supported."""
        total = self.universe.size()
        if total == 0:
            return 1.0
        covered = self.support_map.fully_supported_region().intersection(
            self.universe
        ).size()
        return covered / total

    def gap_count(self) -> int:
        """Number of universe coordinates not fully supported."""
        supported = self.support_map.fully_supported_region()
        return self.universe.difference(supported).size()

    def partial_count(self) -> int:
        """Number of universe coordinates with partial support."""
        return self.support_map.partial_region().intersection(self.universe).size()

    def average_depth(self, depth_map: Mapping[str, int] | None = None) -> float:
        """Average depth of supported coordinates.

        *depth_map* maps coordinate keys to their depth (e.g. in a
        hierarchy).  Only supported coordinates are considered.
        """
        if depth_map is None:
            # Default: depth = number of "/" separators in key
            depth_map = {k: k.count("/") for k in self.universe.coordinates}
        supported_keys = self.support_map.fully_supported_region().intersection(
            self.universe
        ).coordinates
        if not supported_keys:
            return 0.0
        total_depth = sum(depth_map.get(k, 0) for k in supported_keys)
        return total_depth / len(supported_keys)

    def fragmentation_index(self) -> float:
        """Measure how fragmented the support is (0 = contiguous, 1 = scattered).

        Uses a simple prefix-grouping heuristic: coordinates sharing the
        same parent prefix are considered adjacent.  The index is the
        ratio of distinct parent groups to total supported coordinates.
        """
        supported = self.support_map.fully_supported_region().intersection(
            self.universe
        ).coordinates
        if len(supported) <= 1:
            return 0.0
        parents = {k.rsplit("/", 1)[0] if "/" in k else "" for k in supported}
        return (len(parents) - 1) / (len(supported) - 1)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of all computed statistics."""
        return {
            "coverage_ratio": self.coverage_ratio(),
            "gap_count": self.gap_count(),
            "partial_count": self.partial_count(),
            "average_depth": self.average_depth(),
            "fragmentation_index": self.fragmentation_index(),
            "universe_size": self.universe.size(),
        }

    def is_fully_covered(self) -> bool:
        """Return True when every universe coordinate is supported."""
        return self.gap_count() == 0

    def worst_prefix(self, top_n: int = 1) -> list[tuple[str, int]]:
        """Return the *top_n* prefixes with the most unsupported children."""
        gaps = self.universe.difference(
            self.support_map.fully_supported_region()
        ).coordinates
        prefix_counts: dict[str, int] = {}
        for key in gaps:
            prefix = key.rsplit("/", 1)[0] if "/" in key else ""
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        ranked = sorted(prefix_counts.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]


# ---------------------------------------------------------------------------
# 12. EvidenceSupportScope – specialised evidence-jurisdiction tracking
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EvidenceSupportScope:
    """Tracks support contributed by different kinds of evidence.

    In JuGeo, evidence can come from solvers, runtime observations,
    oracles, or formal proofs.  Each source has its own support set and
    trustworthiness.  This class combines them and exposes a unified
    jurisdiction check.
    """

    solver_support: SupportSet = field(default_factory=SupportSet)
    runtime_support: SupportSet = field(default_factory=SupportSet)
    oracle_support: SupportSet = field(default_factory=SupportSet)
    proof_support: SupportSet = field(default_factory=SupportSet)

    _solver_trust: float = 0.8
    _runtime_trust: float = 0.7
    _oracle_trust: float = 0.5
    _proof_trust: float = 1.0

    def combined_support(self) -> SupportSet:
        """Union of all evidence supports regardless of source."""
        result = self.solver_support
        for s in (self.runtime_support, self.oracle_support, self.proof_support):
            result = result.union(s)
        return result

    def weighted_support(self, threshold: float = 0.5) -> SupportSet:
        """Include a coordinate only if its aggregate trust exceeds *threshold*.

        Trust is computed as the maximum trust among sources that support
        the coordinate.
        """
        sources = [
            (self.solver_support, self._solver_trust),
            (self.runtime_support, self._runtime_trust),
            (self.oracle_support, self._oracle_trust),
            (self.proof_support, self._proof_trust),
        ]
        scores: dict[str, float] = {}
        for support, trust in sources:
            for key in support.coordinates:
                scores[key] = max(scores.get(key, 0.0), trust)
        accepted = frozenset(k for k, v in scores.items() if v >= threshold)
        return SupportSet(accepted)

    def jurisdiction_check(
        self,
        claim: SupportSet,
        policy: SupportPolicy | None = None,
    ) -> VerificationResult:
        """Verify that the combined evidence covers *claim*.

        When a *policy* is given its minimum coverage ratio is enforced.
        """
        combined = self.combined_support()
        uncovered = claim.difference(combined)
        if uncovered.is_empty():
            return VerificationResult(
                passed=True,
                message="All claimed coordinates covered by evidence",
            )
        total = claim.size()
        covered = total - uncovered.size()
        ratio = covered / max(1, total)
        if policy and policy.evaluate_coverage(ratio):
            return VerificationResult(
                passed=True,
                message=f"Coverage {ratio:.1%} meets policy threshold",
                details={"uncovered": uncovered.to_sorted_list(), "ratio": ratio},
            )
        return VerificationResult(
            passed=False,
            message=f"Evidence covers only {ratio:.1%} of claim",
            details={"uncovered": uncovered.to_sorted_list(), "ratio": ratio},
        )

    def strongest_source(self) -> str:
        """Return the name of the source with the most supported coordinates."""
        sources = {
            "solver": self.solver_support.size(),
            "runtime": self.runtime_support.size(),
            "oracle": self.oracle_support.size(),
            "proof": self.proof_support.size(),
        }
        return max(sources, key=lambda k: sources[k])

    def source_breakdown(self) -> dict[str, int]:
        """Return coordinate count per evidence source."""
        return {
            "solver": self.solver_support.size(),
            "runtime": self.runtime_support.size(),
            "oracle": self.oracle_support.size(),
            "proof": self.proof_support.size(),
        }

    def exclusive_support(self, source_name: str) -> SupportSet:
        """Coordinates supported *only* by the named source.

        Useful for identifying single-points-of-failure in evidence.
        """
        mapping: dict[str, SupportSet] = {
            "solver": self.solver_support,
            "runtime": self.runtime_support,
            "oracle": self.oracle_support,
            "proof": self.proof_support,
        }
        target = mapping.get(source_name, SupportSet())
        others = SupportSet()
        for name, s in mapping.items():
            if name != source_name:
                others = others.union(s)
        return target.difference(others)

    def redundancy_map(self) -> dict[str, int]:
        """Map each coordinate to the number of evidence sources that cover it."""
        counts: dict[str, int] = {}
        for s in (self.solver_support, self.runtime_support,
                  self.oracle_support, self.proof_support):
            for key in s.coordinates:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def trust_floor(self) -> float:
        """Return the minimum trust level among non-empty sources."""
        trusts = []
        for support, trust in [
            (self.solver_support, self._solver_trust),
            (self.runtime_support, self._runtime_trust),
            (self.oracle_support, self._oracle_trust),
            (self.proof_support, self._proof_trust),
        ]:
            if not support.is_empty():
                trusts.append(trust)
        return min(trusts) if trusts else 0.0

    def add_solver_evidence(self, keys: Iterable[str]) -> None:
        """Extend solver support with additional coordinate keys."""
        self.solver_support = self.solver_support.union(
            SupportSet(frozenset(keys))
        )

    def add_runtime_evidence(self, keys: Iterable[str]) -> None:
        """Extend runtime support with additional coordinate keys."""
        self.runtime_support = self.runtime_support.union(
            SupportSet(frozenset(keys))
        )

    def add_oracle_evidence(self, keys: Iterable[str]) -> None:
        """Extend oracle support with additional coordinate keys."""
        self.oracle_support = self.oracle_support.union(
            SupportSet(frozenset(keys))
        )

    def add_proof_evidence(self, keys: Iterable[str]) -> None:
        """Extend proof support with additional coordinate keys."""
        self.proof_support = self.proof_support.union(
            SupportSet(frozenset(keys))
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # legacy – backward compatible
    "SupportRegion",
    "StarNeighborhood",
    "compute_support",
    "star_of_support",
    # new public types
    "SupportSet",
    "SupportedSection",
    "SupportTracker",
    "SupportStatus",
    "SupportMap",
    "SupportPropagation",
    "VerificationResult",
    "SupportVerifier",
    "SupportMerger",
    "SupportVisualization",
    "SupportPolicy",
    "SupportSerializer",
    "SupportStatistics",
    "EvidenceSupportScope",
]

# copilot: shared-core marker for future LLM orchestration.
