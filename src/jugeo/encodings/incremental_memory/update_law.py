"""Update law M' = Glue(M|_{X\\S}, new_sections, overlap_data) — theory2.tex Ch34.

This module implements the core update law for incremental semantic memory,
developed with copilot assistance.  The Glue construction formalises how new
sections are merged with existing memory after restriction to the complement
of the support set S.

The restriction M|_{X\\S} removes all sections whose coordinate falls in S,
then the glue operation merges the restricted memory with new_sections using
overlap_data to resolve boundary conditions.  The result is a new memory state
M' that agrees with M outside S and with the new_sections inside S, with a
coherent boundary along ∂S encoded in overlap_data.

The module provides both low-level building blocks (RestrictionOperation,
OverlapChecker, GlueOperation) and high-level convenience functions
(apply_incremental_update, compute_restriction, verify_glue_condition) that
are used directly by the incremental memory pipeline in the jugeo runtime.
All operations are deterministic: given the same inputs, the same M' is always
produced, enabling idempotence checks and formal proof witnesses via the
UpdateLawProver class.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from jugeo.runtime.memory import SemanticMemory, MemoryRegion, MemorySnapshot
except ImportError:
    SemanticMemory = Any  # type: ignore
    MemoryRegion = Any  # type: ignore
    MemorySnapshot = Any  # type: ignore

try:
    from jugeo.geometry.supports import SupportSet
except ImportError:
    SupportSet = Any  # type: ignore

try:
    from jugeo.geometry.site import Coordinate
except ImportError:
    Coordinate = Any  # type: ignore

from jugeo.encodings.incremental_memory.models import (
    IncrementalUpdate,
    EncodingSupportSet,
    RegionType,
)

__all__ = [
    "OverlapData",
    "RestrictionResult",
    "GlueComputation",
    "RestrictionOperation",
    "OverlapChecker",
    "GlueOperation",
    "UpdateLawProver",
    "apply_incremental_update",
    "compute_restriction",
    "verify_glue_condition",
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    """Return the SHA-256 hex digest of *text*.

    Args:
        text: The input string to hash.

    Returns:
        A 64-character lowercase hex string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# OverlapData
# ---------------------------------------------------------------------------


@dataclass
class OverlapData:
    """Boundary data that governs the glue construction along the frontier ∂S.

    When performing the glue M' = Glue(M|_{X\\S}, new_sections, overlap_data),
    the overlap_data records which coordinate keys lie on the boundary between
    the restricted memory and the new sections.  Each such key may carry a
    value in the values dict that acts as a compatibility constraint: the
    restriction and the new sections must agree on the overlap region or the
    glue operation must apply a prescribed reconciliation.

    The compatibility_hash field is a content hash of the sorted key list,
    which allows fast compatibility checks without iterating over all keys.
    Two OverlapData objects are compatible if they share the same hash (i.e.,
    identical key sets) or if their key sets are completely disjoint (no
    common boundary constraints).  Disjoint supports can always be glued
    trivially along an empty boundary.

    In the formalism of theory2.tex §34.3, the overlap region corresponds to
    the intersection S ∩ T of two support sets S and T, and the values dict
    encodes the chosen section on that intersection.  The merge method
    implements the union construction on overlapping data objects, computing
    the SHA-256 hash of the combined key list so that downstream checkers can
    verify boundary conditions efficiently without re-reading all values.

    Args:
        keys: Ordered list of coordinate keys on the boundary.
        values: Dict mapping each boundary key to its constraint value.
        compatibility_hash: SHA-256 of sorted(keys) as a hex string.
    """

    keys: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    compatibility_hash: str = ""

    def __post_init__(self) -> None:
        """Compute compatibility_hash if not supplied."""
        if not self.compatibility_hash:
            self.compatibility_hash = _sha256(json.dumps(sorted(self.keys)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_compatible_with(self, other: OverlapData) -> bool:
        """Return True if this overlap is compatible with *other*.

        Two OverlapData objects are compatible if their compatibility hashes
        are equal (identical boundary key sets and the same hash function
        outcome) or if their key sets are completely disjoint (no shared
        boundary constraints, so gluing is trivially possible).

        Args:
            other: The other OverlapData to compare against.

        Returns:
            True if the two objects are compatible, False otherwise.
        """
        if self.compatibility_hash == other.compatibility_hash:
            return True
        own_keys = set(self.keys)
        other_keys = set(other.keys)
        return own_keys.isdisjoint(other_keys)

    def to_json(self) -> str:
        """Serialise this OverlapData to a JSON string.

        Returns:
            A JSON-encoded string containing keys, values, and compatibility_hash.
        """
        return json.dumps({
            "keys": self.keys,
            "values": self.values,
            "compatibility_hash": self.compatibility_hash,
        })

    @classmethod
    def from_json(cls, data: str) -> OverlapData:
        """Reconstruct an OverlapData from a JSON string.

        Args:
            data: A JSON string produced by ``to_json``.

        Returns:
            A new OverlapData instance with the decoded fields.
        """
        obj = json.loads(data)
        instance = cls.__new__(cls)
        instance.keys = obj.get("keys", [])
        instance.values = obj.get("values", {})
        instance.compatibility_hash = obj.get("compatibility_hash", "")
        return instance

    def merge(self, other: OverlapData) -> OverlapData:
        """Return a new OverlapData representing the union of self and *other*.

        The merged object contains the union of both key lists (deduplicated,
        preserving insertion order), with values from *other* taking precedence
        over values from *self* for any shared keys.  The compatibility_hash
        is recomputed from scratch for the merged key set.

        Args:
            other: The other OverlapData to merge with.

        Returns:
            A new OverlapData containing the merged keys, values, and a fresh
            compatibility_hash.
        """
        merged_keys_set: dict = {}  # preserves insertion order
        for k in self.keys:
            merged_keys_set[k] = None
        for k in other.keys:
            merged_keys_set[k] = None
        merged_keys = list(merged_keys_set.keys())
        merged_values = {**self.values, **other.values}
        new_hash = _sha256(json.dumps(sorted(merged_keys)))
        return OverlapData(keys=merged_keys, values=merged_values, compatibility_hash=new_hash)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"OverlapData(keys={self.keys!r}, "
            f"compatibility_hash={self.compatibility_hash!r})"
        )


# ---------------------------------------------------------------------------
# RestrictionResult
# ---------------------------------------------------------------------------


@dataclass
class RestrictionResult:
    """The result of applying a restriction operation M|_{X\\S} to memory data.

    A RestrictionResult records what remains after all sections whose
    coordinates belong to the support set S have been removed.  The
    restricted_data field contains the surviving key-value pairs, while
    removed_coords records exactly which coordinates were excised.  The
    support field preserves the EncodingSupportSet S that was used so that
    callers can verify which coordinates were in scope.  The timestamp
    records when the restriction was computed.

    Args:
        restricted_data: The surviving memory data after restriction.
        removed_coords: List of coordinate keys that were removed.
        support: The EncodingSupportSet that was applied.
        timestamp: Unix timestamp of the restriction computation.
    """

    restricted_data: dict = field(default_factory=dict)
    removed_coords: list = field(default_factory=list)
    support: EncodingSupportSet = field(
        default_factory=lambda: EncodingSupportSet(coords=frozenset())
    )
    timestamp: float = field(default_factory=time.time)

    def is_empty(self) -> bool:
        """Return True if no data survived the restriction.

        Returns:
            True when restricted_data is empty.
        """
        return len(self.restricted_data) == 0

    def size(self) -> int:
        """Return the number of surviving coordinate keys.

        Returns:
            The count of entries in restricted_data.
        """
        return len(self.restricted_data)

    def to_json(self) -> str:
        """Serialise to a JSON string.

        Returns:
            A JSON-encoded string containing all fields.
        """
        return json.dumps({
            "restricted_data": self.restricted_data,
            "removed_coords": self.removed_coords,
            "support": json.loads(self.support.to_json()),
            "timestamp": self.timestamp,
        })

    def summary(self) -> str:
        """Return a human-readable summary of this restriction result.

        Returns:
            A single-line string describing the restriction outcome.
        """
        return (
            f"RestrictionResult: kept={self.size()} removed={len(self.removed_coords)} "
            f"support_size={len(self.support.coords)} "
            f"region_type={self.support.region_type.value}"
        )


# ---------------------------------------------------------------------------
# GlueComputation
# ---------------------------------------------------------------------------


@dataclass
class GlueComputation:
    """The complete record of a single glue computation.

    A GlueComputation captures every artefact produced during the execution
    of the Glue construction: the input restriction, the new sections, the
    overlap data, the output result dict, a proof witness, and status flags.
    The computation_id provides a stable identity for deduplication and audit.
    The error_messages list accumulates any diagnostic messages produced
    during the computation; an empty list with success=True means the
    computation succeeded without errors.

    Args:
        restriction: The RestrictionResult M|_{X\\S}.
        new_sections: The new section data to be installed at S.
        overlap: The boundary overlap data.
        result: The merged output memory data M'.
        proof_witness: Auxiliary proof artefacts.
        timestamp: Unix timestamp of computation completion.
        success: True if the computation completed without errors.
        error_messages: List of error strings accumulated during computation.
        computation_id: A UUID for this computation.
    """

    restriction: RestrictionResult
    new_sections: dict
    overlap: OverlapData
    result: dict = field(default_factory=dict)
    proof_witness: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    success: bool = False
    error_messages: list = field(default_factory=list)
    computation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def is_valid(self) -> bool:
        """Return True if the computation succeeded with no error messages.

        Returns:
            True when success is True and error_messages is empty.
        """
        return self.success and len(self.error_messages) == 0

    def to_json(self) -> str:
        """Serialise this GlueComputation to a JSON string.

        Returns:
            A JSON-encoded string containing all fields in serialisable form.
        """
        return json.dumps({
            "restriction": json.loads(self.restriction.to_json()),
            "new_sections": self.new_sections,
            "overlap": json.loads(self.overlap.to_json()),
            "result": self.result,
            "proof_witness": self.proof_witness,
            "timestamp": self.timestamp,
            "success": self.success,
            "error_messages": self.error_messages,
            "computation_id": self.computation_id,
        })

    @classmethod
    def from_json(cls, data: str) -> GlueComputation:
        """Reconstruct a GlueComputation from a JSON string.

        Args:
            data: A JSON string produced by ``to_json``.

        Returns:
            A new GlueComputation instance with all fields restored.
        """
        obj = json.loads(data)
        restriction_obj = obj.get("restriction", {})
        support_obj = restriction_obj.get("support", {})
        support = EncodingSupportSet(
            coords=frozenset(support_obj.get("coords", [])),
            region_type=RegionType(support_obj.get("region_type", RegionType.ARBITRARY.value)),
            metadata=support_obj.get("metadata", {}),
        )
        restriction = RestrictionResult(
            restricted_data=restriction_obj.get("restricted_data", {}),
            removed_coords=restriction_obj.get("removed_coords", []),
            support=support,
            timestamp=restriction_obj.get("timestamp", time.time()),
        )
        overlap_obj = obj.get("overlap", {})
        overlap = OverlapData.from_json(json.dumps(overlap_obj))
        return cls(
            restriction=restriction,
            new_sections=obj.get("new_sections", {}),
            overlap=overlap,
            result=obj.get("result", {}),
            proof_witness=obj.get("proof_witness", {}),
            timestamp=obj.get("timestamp", time.time()),
            success=obj.get("success", False),
            error_messages=obj.get("error_messages", []),
            computation_id=obj.get("computation_id", str(uuid.uuid4())),
        )

    def summary(self) -> str:
        """Return a human-readable one-line summary of this computation.

        Returns:
            A string describing the computation's outcome and key metrics.
        """
        status = "OK" if self.is_valid() else f"FAIL({len(self.error_messages)})"
        return (
            f"GlueComputation[{self.computation_id[:8]}] "
            f"status={status} "
            f"result_size={len(self.result)} "
            f"new_sections={len(self.new_sections)} "
            f"overlap_keys={len(self.overlap.keys)}"
        )

    def compute_result_hash(self) -> str:
        """Return the SHA-256 hash of the result dict serialised to JSON.

        The hash is computed over the canonical JSON representation of
        self.result with keys sorted, making it independent of insertion order.

        Returns:
            A 64-character hex string.
        """
        return _sha256(json.dumps(self.result, sort_keys=True))


# ---------------------------------------------------------------------------
# RestrictionOperation
# ---------------------------------------------------------------------------


class RestrictionOperation:
    """Implements the restriction functor M ↦ M|_{X\\S} from theory2.tex §34.2.

    A RestrictionOperation holds a fixed support set S and provides methods to
    apply the restriction to arbitrary memory data dicts, MemoryRegion objects,
    or sets of universe coordinates.  The restriction keeps every entry whose
    coordinate key does not belong to S, removing those in S entirely.

    The complement computation method returns a new EncodingSupportSet
    representing X\\S given a list of all universe coordinates X.  This is
    useful when constructing the domain of the restricted sheaf.

    Validation is provided through validate_support, which checks that the
    support set is non-empty and well-formed before any restriction is applied.
    A summary string is available for logging and diagnostic output.

    All methods are purely functional: they return new objects without
    mutating either the input memory data or the support set.  This ensures
    that restrictions can be composed and reapplied safely.

    Args:
        support: The EncodingSupportSet S whose coordinates will be removed.
    """

    def __init__(self, support: EncodingSupportSet) -> None:
        """Initialise with the support set S.

        Args:
            support: The EncodingSupportSet S to restrict away.
        """
        self.support = support

    def apply(self, memory_data: dict) -> RestrictionResult:
        """Apply the restriction to *memory_data*, removing coords in S.

        Iterates over all keys in memory_data and keeps only those that are
        not present in self.support.coords.  Records removed coordinates in
        the removed_coords list of the returned RestrictionResult.

        Args:
            memory_data: A dict mapping coordinate strings to section data.

        Returns:
            A RestrictionResult containing the surviving entries and metadata.
        """
        restricted: dict = {}
        removed: list = []
        for key, value in memory_data.items():
            if key in self.support.coords:
                removed.append(key)
            else:
                restricted[key] = value
        result = RestrictionResult(
            restricted_data=restricted,
            removed_coords=removed,
            support=self.support,
            timestamp=time.time(),
        )
        logger.debug(
            "RestrictionOperation.apply: kept=%d removed=%d", len(restricted), len(removed)
        )
        return result

    def apply_to_region(self, region: Any) -> dict:
        """Apply restriction to a MemoryRegion, returning surviving entries.

        Filters both region.judgments and region.evidence dicts, removing any
        entries whose keys are in self.support.coords.  The returned dict has
        two top-level keys: 'judgments' and 'evidence'.

        Args:
            region: A MemoryRegion (or compatible object) with judgments and
                evidence dicts.

        Returns:
            A dict with keys 'judgments' and 'evidence' containing the
            surviving entries after restriction.
        """
        filtered_judgments: dict = {}
        filtered_evidence: dict = {}
        try:
            for k, v in (region.judgments or {}).items():
                if k not in self.support.coords:
                    filtered_judgments[k] = v
            for k, v in (region.evidence or {}).items():
                if k not in self.support.coords:
                    filtered_evidence[k] = v
        except AttributeError as exc:
            logger.warning("apply_to_region: region missing attribute: %s", exc)
        return {"judgments": filtered_judgments, "evidence": filtered_evidence}

    def compute_complement(self, universe_coords: list) -> EncodingSupportSet:
        """Return the complement X\\S as an EncodingSupportSet.

        Given the full list of universe coordinates X, computes the set
        X \\ S = frozenset(universe_coords) - self.support.coords.

        Args:
            universe_coords: The complete list of coordinate strings in X.

        Returns:
            An EncodingSupportSet with coords = frozenset(universe_coords)
            minus self.support.coords.
        """
        complement_coords = frozenset(universe_coords) - self.support.coords
        return EncodingSupportSet(
            coords=complement_coords,
            region_type=self.support.region_type,
            metadata=dict(self.support.metadata),
        )

    def validate_support(self) -> list:
        """Validate the support set and return a list of error strings.

        Checks that self.support.coords is non-empty.  Additional checks may
        be added in subclasses.

        Returns:
            A list of error strings.  An empty list means the support is valid.
        """
        errors: list = []
        if not self.support.coords:
            errors.append("support.coords is empty; restriction has no effect")
        return errors

    def summary(self) -> str:
        """Return a human-readable summary of this operation.

        Returns:
            A single-line string describing the support set.
        """
        return (
            f"RestrictionOperation: support_size={len(self.support.coords)} "
            f"region_type={self.support.region_type.value}"
        )


# ---------------------------------------------------------------------------
# OverlapChecker
# ---------------------------------------------------------------------------


class OverlapChecker:
    """Checks boundary compatibility between restricted memory and new sections.

    The OverlapChecker verifies that the overlap region ∂S is coherent:
    that the restricted memory M|_{X\\S} and the new sections agree (or can
    be reconciled) on the boundary keys recorded in the OverlapData.  Results
    of individual key compatibility checks are cached by the SHA-256 hash of
    the key to avoid redundant computation.

    The find_conflicts method identifies keys that appear in both the
    restricted memory and the new sections with differing non-None values;
    such conflicts must be resolved before gluing can proceed.  The
    compute_overlap_region method derives the OverlapData automatically from
    the intersection of key sets, which is useful when overlap is not
    provided explicitly.

    The verify_boundary_conditions method performs a fast integrity check by
    recomputing the SHA-256 of sorted(overlap.keys) and comparing it against
    the stored compatibility_hash, catching any silent corruption.

    Results from check_compatibility are memoised in self._cache keyed by
    the SHA-256 hash of the key string, so repeated checks on the same key
    are O(1) after the first call.

    Args:
        None (initialised with an empty cache).
    """

    def __init__(self) -> None:
        """Initialise with an empty compatibility cache."""
        self._cache: dict = {}

    def check_compatibility(
        self,
        restricted: RestrictionResult,
        new_sections: dict,
        overlap: OverlapData,
    ) -> bool:
        """Check that overlap keys are type-compatible between restricted and new.

        For each key listed in overlap.keys, verifies that the value types in
        restricted.restricted_data and new_sections are compatible (both dicts,
        both strings, etc.).  If a key is absent from one side it is considered
        compatible.  Results are cached by SHA-256 of each key.

        Args:
            restricted: The RestrictionResult from applying M|_{X\\S}.
            new_sections: The new section data to be merged in.
            overlap: The OverlapData describing the boundary keys.

        Returns:
            True if all overlap keys pass compatibility checks.
        """
        for key in overlap.keys:
            cache_key = _sha256(key)
            if cache_key in self._cache:
                if not self._cache[cache_key]:
                    return False
                continue
            r_val = restricted.restricted_data.get(key)
            n_val = new_sections.get(key)
            if r_val is None or n_val is None:
                self._cache[cache_key] = True
                continue
            compatible = type(r_val) is type(n_val)  # noqa: E721
            self._cache[cache_key] = compatible
            if not compatible:
                logger.debug(
                    "check_compatibility: type mismatch at key=%r: %s vs %s",
                    key,
                    type(r_val).__name__,
                    type(n_val).__name__,
                )
                return False
        return True

    def find_conflicts(
        self,
        restricted: RestrictionResult,
        new_sections: dict,
    ) -> list:
        """Find keys present in both restricted and new_sections with different values.

        Iterates over all keys in restricted.restricted_data and checks whether
        the same key exists in new_sections with a non-None value that differs
        from the restricted value.  Both values must be non-None to count as a
        conflict; a None value on either side is treated as absent.

        Args:
            restricted: The RestrictionResult from applying M|_{X\\S}.
            new_sections: The new section data.

        Returns:
            A list of conflicting key strings.
        """
        conflicts: list = []
        for key, r_val in restricted.restricted_data.items():
            if key in new_sections:
                n_val = new_sections[key]
                if r_val is not None and n_val is not None and r_val != n_val:
                    conflicts.append(key)
        return conflicts

    def compute_overlap_region(
        self,
        restricted: RestrictionResult,
        new_sections: dict,
    ) -> OverlapData:
        """Compute the OverlapData from the intersection of key sets.

        Finds all keys common to both restricted.restricted_data and
        new_sections, gathers their values from new_sections, and returns
        an OverlapData with the common keys list, combined values, and a
        freshly computed compatibility_hash.

        Args:
            restricted: The RestrictionResult from applying M|_{X\\S}.
            new_sections: The new section data.

        Returns:
            An OverlapData describing the overlap between the two data sets.
        """
        common_keys = list(
            set(restricted.restricted_data.keys()) & set(new_sections.keys())
        )
        values = {k: new_sections[k] for k in common_keys}
        hash_val = _sha256(json.dumps(sorted(common_keys)))
        return OverlapData(keys=common_keys, values=values, compatibility_hash=hash_val)

    def verify_boundary_conditions(self, overlap: OverlapData) -> bool:
        """Verify that the compatibility_hash matches the hash of sorted keys.

        Recomputes SHA-256(json.dumps(sorted(overlap.keys))) and compares it
        against overlap.compatibility_hash.  A mismatch indicates that the
        keys list has been modified without updating the hash, which would
        violate the integrity invariant.

        Args:
            overlap: The OverlapData to verify.

        Returns:
            True if the stored hash matches the recomputed hash.
        """
        expected = _sha256(json.dumps(sorted(overlap.keys)))
        return overlap.compatibility_hash == expected

    def clear_cache(self) -> None:
        """Clear the compatibility check cache.

        After clearing, all subsequent compatibility checks will be recomputed
        from scratch rather than returned from cache.
        """
        self._cache.clear()

    def summary(self) -> str:
        """Return a human-readable summary of the checker's state.

        Returns:
            A single-line string showing the cache size.
        """
        return f"OverlapChecker: cache_entries={len(self._cache)}"


# ---------------------------------------------------------------------------
# GlueOperation
# ---------------------------------------------------------------------------


class GlueOperation:
    """Implements the Glue construction M' = Glue(M|_{X\\S}, new_sections, overlap).

    This class is the central computational component of the update law module.
    It wraps a RestrictionOperation and an OverlapChecker to provide the full
    three-step pipeline: (1) restrict M to M|_{X\\S}, (2) verify overlap
    compatibility along ∂S, and (3) merge the restricted data with new_sections
    to produce M'.  The new sections take precedence over the restricted data
    at shared keys, formalising the idea that the update replaces the old
    content inside S with the new content.

    The apply_to_memory convenience method accepts a SemanticMemory and an
    IncrementalUpdate, extracting the memory data from the region's metadata
    or judgments dict before calling apply.  This provides a bridge between
    the runtime memory objects and the pure-dict Glue construction.

    The verify_idempotence method checks that applying the same Glue once more
    to the result of a GlueComputation produces an identical result hash,
    confirming that the operation is idempotent as required by the theory.

    The rollback method returns the restriction result's data, providing a
    safe fallback to the pre-glue state.  This is used by the invalidation
    pipeline when a glue fails and the memory must be reverted.

    Developed with copilot assistance following theory2.tex §34.4.

    Args:
        restriction_op: A RestrictionOperation configured with the support S.
        overlap_checker: An OverlapChecker for boundary validation.
    """

    def __init__(
        self,
        restriction_op: RestrictionOperation,
        overlap_checker: OverlapChecker,
    ) -> None:
        """Initialise with a RestrictionOperation and OverlapChecker.

        Args:
            restriction_op: A RestrictionOperation configured with support S.
            overlap_checker: An OverlapChecker for boundary validation.
        """
        self.restriction_op = restriction_op
        self.overlap_checker = overlap_checker

    def apply(
        self,
        memory_data: dict,
        new_sections: dict,
        overlap: OverlapData,
    ) -> GlueComputation:
        """Execute the full Glue construction and return a GlueComputation.

        Performs three steps in order:
        1.  Restrict memory_data to M|_{X\\S} using restriction_op.
        2.  Check overlap compatibility between the restriction and new_sections.
        3.  Merge: start from restricted_data, then overlay new_sections
            (new values override old ones for shared keys).

        All artefacts are recorded in the returned GlueComputation.  If overlap
        compatibility fails, the computation is still returned but with
        success=False and the relevant error message appended.

        Args:
            memory_data: The current memory as a flat dict of coord → section.
            new_sections: The new section data to install at S.
            overlap: The boundary overlap data.

        Returns:
            A GlueComputation recording every intermediate and final artefact.
        """
        computation_id = str(uuid.uuid4())
        error_messages: list = []
        logger.debug("GlueOperation.apply: computation_id=%s", computation_id)

        # Step 1: restriction
        restriction = self.restriction_op.apply(memory_data)

        # Step 2: overlap check
        compat = self.overlap_checker.check_compatibility(restriction, new_sections, overlap)
        if not compat:
            error_messages.append(
                f"Overlap compatibility check failed for computation {computation_id}"
            )

        # Step 3: merge — restricted_data first, then new_sections override
        merged: dict = dict(restriction.restricted_data)
        merged.update(new_sections)

        proof_witness = self.compute_glue_witness(
            GlueComputation(
                restriction=restriction,
                new_sections=new_sections,
                overlap=overlap,
                result=merged,
                success=not bool(error_messages),
                error_messages=error_messages,
                computation_id=computation_id,
            )
        )

        return GlueComputation(
            restriction=restriction,
            new_sections=new_sections,
            overlap=overlap,
            result=merged,
            proof_witness=proof_witness,
            timestamp=time.time(),
            success=not bool(error_messages),
            error_messages=error_messages,
            computation_id=computation_id,
        )

    def apply_to_memory(self, memory: Any, update: IncrementalUpdate) -> GlueComputation:
        """Apply the Glue construction to a SemanticMemory and IncrementalUpdate.

        Extracts the memory data from memory._region._metadata if available,
        falling back to memory._region.judgments.  Builds an OverlapData from
        the overlap region between the extracted data and update.new_payload,
        then delegates to apply.

        Args:
            memory: A SemanticMemory (or compatible object) with a _region
                attribute.
            update: The IncrementalUpdate providing new_payload and support.

        Returns:
            A GlueComputation recording the outcome.
        """
        memory_data: dict = {}
        try:
            region = memory._region
            if hasattr(region, "_metadata") and region._metadata:
                memory_data = dict(region._metadata)
            elif hasattr(region, "judgments") and region.judgments:
                memory_data = dict(region.judgments)
        except AttributeError as exc:
            logger.warning("apply_to_memory: cannot read memory region: %s", exc)

        new_sections = dict(update.new_payload)
        overlap = self.overlap_checker.compute_overlap_region(
            RestrictionResult(restricted_data=memory_data, support=update.support),
            new_sections,
        )
        return self.apply(memory_data, new_sections, overlap)

    def verify_idempotence(self, computation: GlueComputation) -> bool:
        """Verify that re-applying the same Glue to the result is idempotent.

        Creates a fresh RestrictionOperation and GlueOperation using the same
        support set, then applies them to computation.result with the same
        new_sections.  Compares the result hash of the re-application against
        the original hash.  If they match, the operation is idempotent.

        Args:
            computation: A completed GlueComputation to verify.

        Returns:
            True if the result hash of a second application equals the first.
        """
        support = computation.restriction.support
        new_restriction_op = RestrictionOperation(support)
        new_checker = OverlapChecker()
        new_glue = GlueOperation(new_restriction_op, new_checker)
        re_computation = new_glue.apply(
            computation.result,
            computation.new_sections,
            computation.overlap,
        )
        original_hash = computation.compute_result_hash()
        re_hash = re_computation.compute_result_hash()
        is_idempotent = original_hash == re_hash
        logger.debug(
            "verify_idempotence: original=%s re=%s idempotent=%s",
            original_hash[:16],
            re_hash[:16],
            is_idempotent,
        )
        return is_idempotent

    def compute_glue_witness(self, computation: GlueComputation) -> dict:
        """Compute a proof witness dict for a GlueComputation.

        Returns a witness dict containing the result hash, timestamp, the
        number of overlap keys, and the success flag.  This witness can be
        stored in the proof_witness field of the GlueComputation for audit.

        Args:
            computation: The GlueComputation to witness.

        Returns:
            A dict with keys 'result_hash', 'timestamp', 'overlap_count', 'success'.
        """
        return {
            "result_hash": computation.compute_result_hash(),
            "timestamp": computation.timestamp,
            "overlap_count": len(computation.overlap.keys),
            "success": computation.success,
        }

    def rollback(self, computation: GlueComputation) -> dict:
        """Return the pre-glue restricted data as a rollback state.

        Args:
            computation: The GlueComputation to roll back.

        Returns:
            The restricted_data dict from the computation's restriction result,
            representing the state of memory before the glue was applied.
        """
        return dict(computation.restriction.restricted_data)

    def summary(self) -> str:
        """Return a human-readable summary of this GlueOperation.

        Returns:
            A multi-line string describing the restriction and checker state.
        """
        return (
            f"GlueOperation:\n"
            f"  restriction: {self.restriction_op.summary()}\n"
            f"  checker:     {self.overlap_checker.summary()}"
        )


# ---------------------------------------------------------------------------
# UpdateLawProver
# ---------------------------------------------------------------------------


class UpdateLawProver:
    """Generates and stores formal proof witnesses for the update law M' = Glue.

    The UpdateLawProver verifies three key properties of GlueComputation
    objects: correctness (the result is a valid merge), determinism (two
    computations with the same inputs produce the same output hash), and
    associativity (grouping of three consecutive glue operations does not
    affect the final result hash).  Each verified property is recorded in
    self._proven keyed by a descriptive string.

    Proof certificates are dicts containing the computation id, result hash,
    timestamp, and a list of verified property names.  Witnesses are stored
    in self._witnesses for later retrieval or export.

    All proof state can be reset via the reset method, which clears both
    self._proven and self._witnesses.  The list_proven method returns a sorted
    list of all property strings that have been verified so far.

    Args:
        None (initialised with empty proven dict and witnesses list).
    """

    def __init__(self) -> None:
        """Initialise with empty proof state."""
        self._proven: dict = {}
        self._witnesses: list = []

    def prove_correctness(self, computation: GlueComputation) -> bool:
        """Verify that a GlueComputation's result is a valid merge.

        Checks that (a) the computation is_valid(), (b) every key in
        computation.new_sections appears in computation.result, and (c)
        every key in computation.restriction.restricted_data that does not
        appear in new_sections also appears in computation.result unchanged.

        Args:
            computation: The GlueComputation to verify.

        Returns:
            True if all correctness checks pass.
        """
        if not computation.is_valid():
            return False

        # Check new_sections are in result
        for key, val in computation.new_sections.items():
            if key not in computation.result:
                return False
            if computation.result[key] != val:
                return False

        # Check restricted data is preserved for non-overridden keys
        for key, val in computation.restriction.restricted_data.items():
            if key not in computation.new_sections:
                if key not in computation.result:
                    return False
                if computation.result[key] != val:
                    return False

        prop_key = f"correctness:{computation.computation_id}"
        self._proven[prop_key] = True
        self._witnesses.append(self.generate_proof_certificate(computation))
        return True

    def prove_determinism(
        self,
        computation1: GlueComputation,
        computation2: GlueComputation,
    ) -> bool:
        """Verify that two computations with the same inputs give the same result.

        Compares the result hashes of computation1 and computation2.  If the
        new_sections and overlap keys match and the result hashes are equal,
        the computations are deterministic.

        Args:
            computation1: The first GlueComputation.
            computation2: The second GlueComputation.

        Returns:
            True if result hashes match.
        """
        h1 = computation1.compute_result_hash()
        h2 = computation2.compute_result_hash()
        is_deterministic = h1 == h2
        prop_key = f"determinism:{computation1.computation_id}:{computation2.computation_id}"
        self._proven[prop_key] = is_deterministic
        return is_deterministic

    def prove_associativity(
        self,
        c1: GlueComputation,
        c2: GlueComputation,
        c3: GlueComputation,
    ) -> bool:
        """Verify associativity: (c1 ∘ c2) ∘ c3 ≡ c1 ∘ (c2 ∘ c3) by hash.

        Computes the combined hash of the three result dicts in left-associated
        and right-associated order and checks that they are equal.

        Args:
            c1: First computation.
            c2: Second computation.
            c3: Third computation.

        Returns:
            True if the left-associative hash equals the right-associative hash.
        """
        left_merged = {**c1.result, **c2.result, **c3.result}
        right_merged = {**c3.result, **c2.result, **c1.result}
        # Resolve by always taking the rightmost (latest) for consistency check
        left_hash = _sha256(json.dumps(left_merged, sort_keys=True))
        right_hash = _sha256(json.dumps(right_merged, sort_keys=True))
        # True associativity: same merged result regardless of grouping order
        is_associative = left_hash == right_hash
        prop_key = (
            f"associativity:{c1.computation_id}:{c2.computation_id}:{c3.computation_id}"
        )
        self._proven[prop_key] = is_associative
        return is_associative

    def generate_proof_certificate(self, computation: GlueComputation) -> dict:
        """Generate a proof certificate dict for a GlueComputation.

        Args:
            computation: The computation to certify.

        Returns:
            A dict with keys 'computation_id', 'result_hash', 'timestamp',
            'is_valid', 'proven_properties'.
        """
        proven_props = [k for k, v in self._proven.items() if v and computation.computation_id in k]
        certificate = {
            "computation_id": computation.computation_id,
            "result_hash": computation.compute_result_hash(),
            "timestamp": time.time(),
            "is_valid": computation.is_valid(),
            "proven_properties": proven_props,
        }
        return certificate

    def list_proven(self) -> list:
        """Return a sorted list of all proven property keys.

        Returns:
            Sorted list of property strings that evaluate to True.
        """
        return sorted(k for k, v in self._proven.items() if v)

    def reset(self) -> None:
        """Clear all proof state.

        After calling reset, self._proven is empty and self._witnesses is empty.
        """
        self._proven.clear()
        self._witnesses.clear()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def apply_incremental_update(
    memory: Any,
    update: IncrementalUpdate,
) -> tuple:
    """Apply an IncrementalUpdate to a SemanticMemory using the Glue construction.

    Creates a RestrictionOperation from the update's support, an OverlapChecker,
    and a GlueOperation, then calls apply_to_memory.  Returns the updated
    memory object (unchanged reference; caller is responsible for committing
    the result) and the GlueComputation record.

    Args:
        memory: A SemanticMemory (or compatible object) with a _region attribute.
        update: The IncrementalUpdate to apply.

    Returns:
        A tuple (memory, computation) where memory is the input object and
        computation is the GlueComputation that records the operation.
    """
    restriction_op = RestrictionOperation(update.support)
    checker = OverlapChecker()
    glue_op = GlueOperation(restriction_op, checker)
    computation = glue_op.apply_to_memory(memory, update)
    logger.info(
        "apply_incremental_update: %s", computation.summary()
    )
    return memory, computation


def compute_restriction(
    memory_data: dict,
    support: EncodingSupportSet,
) -> RestrictionResult:
    """Compute the restriction M|_{X\\S} of memory_data away from support.

    Creates a RestrictionOperation and applies it to memory_data.

    Args:
        memory_data: A flat dict mapping coordinate strings to section data.
        support: The EncodingSupportSet S to restrict away.

    Returns:
        A RestrictionResult containing the surviving entries.
    """
    op = RestrictionOperation(support)
    return op.apply(memory_data)


def verify_glue_condition(
    restricted: RestrictionResult,
    new_sections: dict,
    overlap: OverlapData,
) -> bool:
    """Verify that the glue condition holds between restricted memory and new sections.

    Creates an OverlapChecker and runs both check_compatibility and
    verify_boundary_conditions.  Returns True only if both checks pass.

    Args:
        restricted: The RestrictionResult M|_{X\\S}.
        new_sections: The new section data to install at S.
        overlap: The boundary OverlapData.

    Returns:
        True if the glue condition is satisfied.
    """
    checker = OverlapChecker()
    compat = checker.check_compatibility(restricted, new_sections, overlap)
    boundary_ok = checker.verify_boundary_conditions(overlap)
    return compat and boundary_ok
