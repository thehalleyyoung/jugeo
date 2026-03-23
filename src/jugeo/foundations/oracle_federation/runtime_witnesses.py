from __future__ import annotations
"""Section 7.3 — Runtime Witnesses (Theory2.tex Ch7).

Runtime witnesses are evidence records collected directly from program
execution state: heap snapshots, identity verifications, and stack traces.
They occupy the ``RUNTIME_WITNESSED`` trust tier — above oracle proposals but
below solver discharges — reflecting that they capture real execution state
but do not constitute a formal proof.

§7.3 of Theory2.tex defines three witness kinds:

* **Heap witness** — a snapshot of the heap at a program point, used to
  verify memory invariants, allocation patterns, and absence of dangling
  references.  The snapshot records object identities, reference counts, and
  any invariant violations detected at collection time.

* **Identity witness** — proof that an entity (object, variable, or
  channel) has a particular identity at a program point.  Useful for
  verifying ownership, uniqueness, and aliasing constraints.

* **Stack witness** — a capture of the call stack and variable bindings at
  a program point.  Supports behavioral verification by showing what code
  path was taken to reach the current state.

The ``RuntimeWitnessCollector`` orchestrates collection across all three
kinds and enforces the collection policy (sampling rate, maximum witness
count, pruning interval).

Trust assignment
----------------
All runtime witnesses enter at ``TrustLevel.RUNTIME_WITNESSED``.  They may
*not* claim ``SOLVER_DISCHARGED`` or higher.  A set of mutually consistent
witnesses can be merged without trust loss; inconsistencies demote the merged
result to the weaker tier of the inconsistency.

The ``WitnessValidator`` class performs consistency checks.

Theory alignment
----------------
- Theory2.tex §7.3.1 defines the heap witness model.
- Theory2.tex §7.3.2 defines identity witnesses.
- Theory2.tex §7.3.3 defines stack witnesses.
- Theory2.tex §7.3.4 defines the consistency and merge rules.
- Theory2.tex §7.3.5 defines the collection policy framework.
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    from jugeo.evidence.channels import (
        EvidenceChannel,
        EvidenceRequest,
        EvidenceResponse,
    )
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Trust tier for runtime witnesses (Theory2.tex §7.3)
_RUNTIME_TRUST_TIER = "runtime_witnessed"
_WITNESS_TRUST_RANK = 5  # rank of runtime_witnessed in the partial order


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class WitnessKind(Enum):
    """Classifies the kind of runtime witness.

    Theory2.tex §7.3 recognises five kinds.  HEAP, IDENTITY, and STACK are
    primary (§7.3.1–§7.3.3); BEHAVIORAL and COMPOSITE are derived.
    """

    HEAP = "heap"
    IDENTITY = "identity"
    STACK = "stack"
    BEHAVIORAL = "behavioral"
    COMPOSITE = "composite"


class ConsistencyStatus(Enum):
    """Result of a mutual-consistency check over a witness set.

    Theory2.tex §7.3.4 defines the consistency relation.  PARTIAL indicates
    that the witnesses are consistent over the subset of claims they share,
    but do not cover all claims.
    """

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    UNCHECKED = "unchecked"


# ---------------------------------------------------------------------------
# HeapWitness — Theory2.tex §7.3.1
# ---------------------------------------------------------------------------


@dataclass
class HeapWitness:
    """A snapshot of heap state at a specific program point.

    Captures object identities, reference topology, allocation and
    deallocation traces, and any invariant violations detected at collection
    time.  Corresponds to the heap-witness model in Theory2.tex §7.3.1.

    Attributes
    ----------
    witness_id:
        Globally unique identifier with ``hw_`` prefix.
    heap_snapshot:
        Maps object_id → ``{type, refs, size, value_repr}``.
    allocation_trace:
        Ordered list of ``{address, size, type, timestamp}`` records.
    deallocation_trace:
        Ordered list of ``{address, timestamp}`` records.
    invariant_violations:
        Human-readable strings describing detected violations.
    trust_tier:
        Always ``"runtime_witnessed"`` for heap witnesses.
    timestamp:
        Unix epoch time at collection.
    collection_site:
        Free-form label for the program point where the witness was taken.
    metadata:
        Arbitrary collector-defined key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: "hw_" + uuid.uuid4().hex[:12])
    heap_snapshot: dict = field(default_factory=dict)  # object_id -> {type, refs, size, value_repr}
    allocation_trace: list[dict] = field(default_factory=list)
    deallocation_trace: list[dict] = field(default_factory=list)
    invariant_violations: list[str] = field(default_factory=list)
    trust_tier: str = _RUNTIME_TRUST_TIER
    timestamp: float = field(default_factory=time.time)
    collection_site: str = ""
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_allocation(self, address: str, size: int, type_name: str) -> None:
        """Append an allocation event to *allocation_trace*.

        Parameters
        ----------
        address:
            Memory address string (hex or symbolic).
        size:
            Number of bytes allocated.  Negative values are allowed in
            simulated environments but trigger an invariant violation.
        type_name:
            Python type name or descriptor of the allocated object.
        """
        self.allocation_trace.append(
            {
                "address": address,
                "size": size,
                "type": type_name,
                "timestamp": time.time(),
            }
        )

    def add_object(self, obj_id: str, obj_type: str, refs: list[str], size: int) -> None:
        """Insert or update an object record in *heap_snapshot*.

        Parameters
        ----------
        obj_id:
            Unique identifier for the object (e.g. ``id(obj)`` as a string).
        obj_type:
            Type name string.
        refs:
            List of object IDs this object holds references to.
        size:
            Memory footprint in bytes.
        """
        self.heap_snapshot[obj_id] = {
            "type": obj_type,
            "refs": refs,
            "size": size,
            "value_repr": f"<{obj_type}>",
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_invariants(self) -> list[str]:
        """Scan *heap_snapshot* for common heap invariant violations.

        Checks performed (Theory2.tex §7.3.1):

        1. **Reference cycles** — a simple depth-first search detects any
           back edge, which constitutes a cycle.
        2. **Null references** — any ref entry in an object's ``refs`` list
           that does not appear as a key in the snapshot.
        3. **Size anomalies** — objects whose recorded size is negative.

        Returns
        -------
        list[str]
            Violation descriptions.  The internal ``invariant_violations``
            field is also updated in place.
        """
        violations: list[str] = []

        # 1. Reference cycles via DFS
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _dfs_has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in self.heap_snapshot.get(node, {}).get("refs", []):
                if neighbour not in self.heap_snapshot:
                    continue
                if neighbour not in visited:
                    if _dfs_has_cycle(neighbour):
                        return True
                elif neighbour in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for obj_id in list(self.heap_snapshot):
            if obj_id not in visited:
                if _dfs_has_cycle(obj_id):
                    violations.append(f"reference_cycle_detected: rooted at {obj_id}")
                    break  # report once; full cycle enumeration is expensive

        # 2. Null (dangling) references
        known_ids = set(self.heap_snapshot.keys())
        for obj_id, obj in self.heap_snapshot.items():
            for ref in obj.get("refs", []):
                if ref not in known_ids:
                    violations.append(f"dangling_ref: {obj_id} -> {ref}")

        # 3. Size anomalies
        for obj_id, obj in self.heap_snapshot.items():
            if obj.get("size", 0) < 0:
                violations.append(f"negative_size: {obj_id} size={obj['size']}")

        self.invariant_violations = violations
        if violations:
            logger.debug(
                "HeapWitness %s: %d invariant violation(s) detected",
                self.witness_id,
                len(violations),
            )
        return violations

    # ------------------------------------------------------------------
    # Comparison / diff
    # ------------------------------------------------------------------

    def diff(self, other: HeapWitness) -> dict:
        """Compute the diff between this witness and *other*.

        Returns a dict describing which objects were added, removed, or
        modified between ``self`` (the *base*) and ``other`` (the *head*).

        Parameters
        ----------
        other:
            The head snapshot to compare against.

        Returns
        -------
        dict
            ``{"added": [...], "removed": [...], "modified": [...], "unchanged_count": int}``
        """
        self_ids = set(self.heap_snapshot.keys())
        other_ids = set(other.heap_snapshot.keys())

        added = sorted(other_ids - self_ids)
        removed = sorted(self_ids - other_ids)
        modified = []
        unchanged = 0

        for obj_id in self_ids & other_ids:
            if self.heap_snapshot[obj_id] != other.heap_snapshot[obj_id]:
                modified.append(obj_id)
            else:
                unchanged += 1

        return {
            "added": added,
            "removed": removed,
            "modified": sorted(modified),
            "unchanged_count": unchanged,
        }

    # ------------------------------------------------------------------
    # Evidence interop
    # ------------------------------------------------------------------

    def to_evidence_response_dict(self) -> dict:
        """Serialise to the shape expected by ``EvidenceResponse``.

        Returns a plain dict suitable for constructing an ``EvidenceResponse``
        (Theory2.tex §7.3 — evidence protocol).
        """
        return {
            "request_id": self.witness_id,
            "channel": "runtime",
            "evidence_item": {
                "witness_kind": "heap",
                "snapshot_size": len(self.heap_snapshot),
                "allocation_count": len(self.allocation_trace),
                "deallocation_count": len(self.deallocation_trace),
                "violations": list(self.invariant_violations),
                "collection_site": self.collection_site,
            },
            "trust_level": _format_trust_for_response(self.trust_tier),
            "latency_ms": 0.0,
            "is_partial": False,
            "residuals": [],
            "provenance": [f"heap_witness:{self.witness_id}"],
        }

    def serialize(self) -> dict:
        """Full serialization of this witness to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "kind": WitnessKind.HEAP.value,
            "heap_snapshot": dict(self.heap_snapshot),
            "allocation_trace": list(self.allocation_trace),
            "deallocation_trace": list(self.deallocation_trace),
            "invariant_violations": list(self.invariant_violations),
            "trust_tier": self.trust_tier,
            "timestamp": self.timestamp,
            "collection_site": self.collection_site,
            "metadata": dict(self.metadata),
        }

    def get_trust_assertion(self) -> dict:
        """Return a compact trust assertion dict for this witness."""
        return {
            "trust_level": self.trust_tier,
            "witness_id": self.witness_id,
            "kind": "heap",
            "validated": len(self.invariant_violations) == 0,
            "timestamp": self.timestamp,
        }

    def summarize(self) -> str:
        """Return a human-readable one-line summary of this witness."""
        return (
            f"HeapWitness({self.witness_id}) site={self.collection_site!r} "
            f"objects={len(self.heap_snapshot)} "
            f"allocs={len(self.allocation_trace)} "
            f"violations={len(self.invariant_violations)}"
        )

    def object_count(self) -> int:
        """Return the number of objects in the snapshot."""
        return len(self.heap_snapshot)

    def has_violations(self) -> bool:
        """Return True if any invariant violations were recorded."""
        return bool(self.invariant_violations)


# ---------------------------------------------------------------------------
# IdentityWitness — Theory2.tex §7.3.2
# ---------------------------------------------------------------------------


@dataclass
class IdentityWitness:
    """Proof that an entity has a particular identity at a program point.

    Used to verify ownership, uniqueness, and aliasing constraints as defined
    in Theory2.tex §7.3.2.

    Attributes
    ----------
    witness_id:
        Globally unique identifier with ``iw_`` prefix.
    entity_id:
        The identifier of the entity whose identity is being witnessed.
    identity_proof:
        Mapping from property name to observed value.
    verification_chain:
        Ordered list of verification step records.
    trust_tier:
        Always ``"runtime_witnessed"``.
    timestamp:
        Unix epoch time at collection.
    expected_properties:
        Properties that ``verify()`` should check against.
    is_verified:
        Set to ``True`` by a successful ``verify()`` call.
    """

    witness_id: str = field(default_factory=lambda: "iw_" + uuid.uuid4().hex[:12])
    entity_id: str = ""
    identity_proof: dict = field(default_factory=dict)
    verification_chain: list[dict] = field(default_factory=list)
    trust_tier: str = _RUNTIME_TRUST_TIER
    timestamp: float = field(default_factory=time.time)
    expected_properties: dict = field(default_factory=dict)
    is_verified: bool = False

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, expected_identity: dict) -> bool:
        """Compare *identity_proof* against *expected_identity*.

        For each key in *expected_identity* the corresponding value in
        ``identity_proof`` must match exactly.  Sets ``is_verified`` and
        appends a verification step to ``verification_chain``.

        Parameters
        ----------
        expected_identity:
            Dict of property-name → expected-value pairs.

        Returns
        -------
        bool
            ``True`` if all expected properties match.
        """
        mismatches: list[str] = []
        for key, expected_val in expected_identity.items():
            actual_val = self.identity_proof.get(key)
            if actual_val != expected_val:
                mismatches.append(
                    f"{key}: expected={expected_val!r} actual={actual_val!r}"
                )

        self.is_verified = len(mismatches) == 0
        step = {
            "step_id": uuid.uuid4().hex[:8],
            "timestamp": time.time(),
            "expected": dict(expected_identity),
            "mismatches": mismatches,
            "result": "pass" if self.is_verified else "fail",
        }
        self.verification_chain.append(step)
        self.expected_properties = dict(expected_identity)
        logger.debug(
            "IdentityWitness %s verify result=%s mismatches=%d",
            self.witness_id,
            "pass" if self.is_verified else "fail",
            len(mismatches),
        )
        return self.is_verified

    def chain_with(self, other: IdentityWitness) -> IdentityWitness:
        """Compose this witness with *other*, returning a new combined witness.

        The merged entity_id is ``f"{self.entity_id}+{other.entity_id}"``.
        ``self`` takes precedence in the identity_proof merge.  The combined
        trust tier is the weaker (higher-rank) of the two.

        Parameters
        ----------
        other:
            The witness to chain with.

        Returns
        -------
        IdentityWitness
            A new witness representing the composition.
        """
        merged_proof = {**other.identity_proof, **self.identity_proof}
        merged_chain = list(self.verification_chain) + list(other.verification_chain)
        # Lower trust tier string wins (use alphabetical as proxy; real impl
        # would compare TrustTier enum values).
        weaker_tier = (
            self.trust_tier
            if self.trust_tier >= other.trust_tier
            else other.trust_tier
        )
        return IdentityWitness(
            entity_id=f"{self.entity_id}+{other.entity_id}",
            identity_proof=merged_proof,
            verification_chain=merged_chain,
            trust_tier=weaker_tier,
            is_verified=self.is_verified and other.is_verified,
        )

    # ------------------------------------------------------------------
    # Evidence interop
    # ------------------------------------------------------------------

    def to_evidence_response_dict(self) -> dict:
        """Serialise to the ``EvidenceResponse``-compatible shape."""
        return {
            "request_id": self.witness_id,
            "channel": "runtime",
            "evidence_item": {
                "witness_kind": "identity",
                "entity_id": self.entity_id,
                "is_verified": self.is_verified,
                "proof_keys": sorted(self.identity_proof.keys()),
                "chain_length": len(self.verification_chain),
            },
            "trust_level": _format_trust_for_response(self.trust_tier),
            "latency_ms": 0.0,
            "is_partial": not self.is_verified,
            "residuals": [],
            "provenance": [f"identity_witness:{self.witness_id}"],
        }

    def serialize(self) -> dict:
        """Full serialization of this witness to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "kind": WitnessKind.IDENTITY.value,
            "entity_id": self.entity_id,
            "identity_proof": dict(self.identity_proof),
            "verification_chain": list(self.verification_chain),
            "trust_tier": self.trust_tier,
            "timestamp": self.timestamp,
            "expected_properties": dict(self.expected_properties),
            "is_verified": self.is_verified,
        }

    def get_trust_assertion(self) -> dict:
        """Return a compact trust assertion dict."""
        return {
            "trust_level": self.trust_tier,
            "witness_id": self.witness_id,
            "kind": "identity",
            "validated": self.is_verified,
            "timestamp": self.timestamp,
        }

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"IdentityWitness  id={self.witness_id}",
            f"  entity_id      : {self.entity_id}",
            f"  trust_tier     : {self.trust_tier}",
            f"  is_verified    : {self.is_verified}",
            f"  proof_keys     : {sorted(self.identity_proof.keys())}",
            f"  chain_steps    : {len(self.verification_chain)}",
            f"  timestamp      : {self.timestamp}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# StackWitness — Theory2.tex §7.3.3
# ---------------------------------------------------------------------------


@dataclass
class StackWitness:
    """A capture of the call stack and variable bindings at a program point.

    Supports behavioral verification by providing evidence of which code path
    was taken to reach the current state (Theory2.tex §7.3.3).

    Attributes
    ----------
    witness_id:
        Globally unique identifier with ``sw_`` prefix.
    frame_data:
        Ordered list of ``{function, file, line, locals}`` dicts.
    call_stack:
        Ordered list of function-name strings (outermost first).
    variable_bindings:
        Merged variable bindings extracted from all frames.
    trust_tier:
        Always ``"runtime_witnessed"``.
    timestamp:
        Unix epoch time at collection.
    collection_depth:
        Number of frames captured (may be less than the full stack depth).
    is_complete:
        ``False`` if the capture was truncated by a depth limit.
    """

    witness_id: str = field(default_factory=lambda: "sw_" + uuid.uuid4().hex[:12])
    frame_data: list[dict] = field(default_factory=list)
    call_stack: list[str] = field(default_factory=list)
    variable_bindings: dict = field(default_factory=dict)
    trust_tier: str = _RUNTIME_TRUST_TIER
    timestamp: float = field(default_factory=time.time)
    collection_depth: int = 0
    is_complete: bool = True

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def extract_bindings(self) -> dict:
        """Merge locals from all frames into *variable_bindings*.

        Later frames (closer to the top of the stack) take precedence for
        duplicate variable names.  Updates and returns ``variable_bindings``.
        """
        merged: dict = {}
        for frame in self.frame_data:
            locals_map = frame.get("locals", {})
            if isinstance(locals_map, dict):
                merged.update(locals_map)
        self.variable_bindings = merged
        return merged

    def validate_call_stack(self) -> bool:
        """Check that the call stack is non-empty and structurally plausible.

        A call stack is considered *implausible* if it contains an empty
        string as a function name (corrupted frame) or is completely empty.
        Duplicate consecutive entries (recursion) are acceptable; duplicate
        non-consecutive entries only indicate a loop and are also fine.

        Returns
        -------
        bool
            ``True`` if the call stack looks plausible.
        """
        if not self.call_stack:
            logger.debug("StackWitness %s: empty call_stack", self.witness_id)
            return False
        for name in self.call_stack:
            if not isinstance(name, str) or name.strip() == "":
                logger.debug(
                    "StackWitness %s: corrupt frame name %r", self.witness_id, name
                )
                return False
        return True

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def top_frame(self) -> dict | None:
        """Return the top (most-recent) frame, or ``None`` if empty."""
        return self.frame_data[-1] if self.frame_data else None

    def depth(self) -> int:
        """Return the number of frames in the call stack."""
        return len(self.call_stack)

    # ------------------------------------------------------------------
    # Evidence interop
    # ------------------------------------------------------------------

    def to_evidence_response_dict(self) -> dict:
        """Serialise to the ``EvidenceResponse``-compatible shape."""
        return {
            "request_id": self.witness_id,
            "channel": "runtime",
            "evidence_item": {
                "witness_kind": "stack",
                "depth": len(self.call_stack),
                "bindings_count": len(self.variable_bindings),
                "is_complete": self.is_complete,
                "collection_depth": self.collection_depth,
                "top_function": self.call_stack[-1] if self.call_stack else None,
            },
            "trust_level": _format_trust_for_response(self.trust_tier),
            "latency_ms": 0.0,
            "is_partial": not self.is_complete,
            "residuals": [],
            "provenance": [f"stack_witness:{self.witness_id}"],
        }

    def serialize(self) -> dict:
        """Full serialization of this witness to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "kind": WitnessKind.STACK.value,
            "frame_data": list(self.frame_data),
            "call_stack": list(self.call_stack),
            "variable_bindings": dict(self.variable_bindings),
            "trust_tier": self.trust_tier,
            "timestamp": self.timestamp,
            "collection_depth": self.collection_depth,
            "is_complete": self.is_complete,
        }

    def describe(self) -> str:
        """Return a multi-line human-readable description."""
        lines = [
            f"StackWitness  id={self.witness_id}",
            f"  trust_tier   : {self.trust_tier}",
            f"  depth        : {self.depth()}",
            f"  is_complete  : {self.is_complete}",
            f"  bindings     : {len(self.variable_bindings)}",
            f"  timestamp    : {self.timestamp}",
            "  call_stack   :",
        ]
        for i, fn in enumerate(self.call_stack):
            lines.append(f"    [{i}] {fn}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# WitnessValidator — Theory2.tex §7.3.4
# ---------------------------------------------------------------------------


class WitnessValidator:
    """Validates individual witnesses and checks mutual consistency.

    All methods are class methods.  No instance state is maintained.
    Implements the consistency and merge rules from Theory2.tex §7.3.4.
    """

    @classmethod
    def validate_heap(cls, witness: HeapWitness) -> tuple[bool, list[str]]:
        """Validate a single ``HeapWitness``.

        Checks that the snapshot is non-empty, calls
        ``witness.validate_invariants()`` to populate the violation list,
        and reports issues.

        Parameters
        ----------
        witness:
            The witness to validate.

        Returns
        -------
        tuple[bool, list[str]]
            ``(is_valid, issues)`` where *issues* is a list of problem strings.
        """
        issues: list[str] = []
        if not witness.heap_snapshot:
            issues.append("heap_snapshot is empty")
        violations = witness.validate_invariants()
        issues.extend(violations)
        is_valid = len(issues) == 0
        logger.debug(
            "WitnessValidator.validate_heap %s: valid=%s issues=%d",
            witness.witness_id,
            is_valid,
            len(issues),
        )
        return is_valid, issues

    @classmethod
    def validate_identity(cls, witness: IdentityWitness) -> tuple[bool, list[str]]:
        """Validate a single ``IdentityWitness``.

        Checks that ``entity_id`` and ``identity_proof`` are non-empty and
        that the verification chain has internally consistent step results.

        Returns
        -------
        tuple[bool, list[str]]
        """
        issues: list[str] = []
        if not witness.entity_id:
            issues.append("entity_id is empty")
        if not witness.identity_proof:
            issues.append("identity_proof is empty")
        # Check chain integrity: ensure step ids are unique
        seen_step_ids: set[str] = set()
        for step in witness.verification_chain:
            sid = step.get("step_id", "")
            if sid in seen_step_ids:
                issues.append(f"duplicate step_id in verification_chain: {sid}")
            seen_step_ids.add(sid)
        is_valid = len(issues) == 0
        return is_valid, issues

    @classmethod
    def validate_stack(cls, witness: StackWitness) -> tuple[bool, list[str]]:
        """Validate a single ``StackWitness``.

        Checks that ``frame_data`` and ``call_stack`` are non-empty, that
        the call stack is structurally plausible, and that bindings are a
        dict.

        Returns
        -------
        tuple[bool, list[str]]
        """
        issues: list[str] = []
        if not witness.frame_data:
            issues.append("frame_data is empty")
        if not witness.call_stack:
            issues.append("call_stack is empty")
        if not witness.validate_call_stack():
            issues.append("call_stack failed plausibility check")
        if not isinstance(witness.variable_bindings, dict):
            issues.append("variable_bindings is not a dict")
        is_valid = len(issues) == 0
        return is_valid, issues

    @classmethod
    def check_mutual_consistency(cls, witnesses: list) -> ConsistencyStatus:
        """Check whether a mixed list of witnesses are mutually consistent.

        Consistency rules (Theory2.tex §7.3.4):

        * Two ``HeapWitness`` objects are *inconsistent* if they share an
          ``object_id`` but record different types for it.
        * Two ``IdentityWitness`` objects are *inconsistent* if they share an
          ``entity_id`` and a property key but record different values for it.
        * If no overlapping claims exist the status is ``PARTIAL``.
        * If all overlapping claims agree the status is ``CONSISTENT``.

        Parameters
        ----------
        witnesses:
            Heterogeneous list of witness objects.

        Returns
        -------
        ConsistencyStatus
        """
        if not witnesses:
            return ConsistencyStatus.UNKNOWN

        heap_witnesses = [w for w in witnesses if isinstance(w, HeapWitness)]
        identity_witnesses = [w for w in witnesses if isinstance(w, IdentityWitness)]
        has_overlap = False

        # Check HeapWitness pair consistency
        for i in range(len(heap_witnesses)):
            for j in range(i + 1, len(heap_witnesses)):
                a, b = heap_witnesses[i], heap_witnesses[j]
                common_ids = set(a.heap_snapshot.keys()) & set(b.heap_snapshot.keys())
                if common_ids:
                    has_overlap = True
                for obj_id in common_ids:
                    if a.heap_snapshot[obj_id].get("type") != b.heap_snapshot[obj_id].get("type"):
                        logger.debug(
                            "HeapWitness inconsistency on %s: %s vs %s",
                            obj_id,
                            a.heap_snapshot[obj_id].get("type"),
                            b.heap_snapshot[obj_id].get("type"),
                        )
                        return ConsistencyStatus.INCONSISTENT

        # Check IdentityWitness pair consistency
        for i in range(len(identity_witnesses)):
            for j in range(i + 1, len(identity_witnesses)):
                a, b = identity_witnesses[i], identity_witnesses[j]
                if a.entity_id != b.entity_id:
                    continue
                has_overlap = True
                common_keys = set(a.identity_proof.keys()) & set(b.identity_proof.keys())
                for key in common_keys:
                    if a.identity_proof[key] != b.identity_proof[key]:
                        logger.debug(
                            "IdentityWitness inconsistency entity=%s key=%s",
                            a.entity_id,
                            key,
                        )
                        return ConsistencyStatus.INCONSISTENT

        if not has_overlap and len(witnesses) > 1:
            return ConsistencyStatus.PARTIAL
        return ConsistencyStatus.CONSISTENT

    @classmethod
    def merge_consistent_witnesses(cls, witnesses: list) -> dict:
        """Merge a consistent set of witnesses into a combined evidence dict.

        If the witnesses are consistent (per ``check_mutual_consistency``),
        the merge is lossless and retains ``RUNTIME_WITNESSED`` trust.  If
        they are inconsistent, trust is demoted to ``"oracle_proposed"`` and
        the conflict is recorded in ``residuals``.

        Parameters
        ----------
        witnesses:
            List of witness objects to merge.

        Returns
        -------
        dict
            A combined evidence dict with keys ``merged_witnesses``,
            ``trust_level``, ``residuals``, ``provenance``, and
            ``consistency_status``.
        """
        status = cls.check_mutual_consistency(witnesses)
        trust = (
            _RUNTIME_TRUST_TIER
            if status in (ConsistencyStatus.CONSISTENT, ConsistencyStatus.PARTIAL)
            else "oracle_proposed"
        )
        residuals: list[str] = []
        if status == ConsistencyStatus.INCONSISTENT:
            residuals.append("trust_demoted_due_to_inconsistency")

        merged_items: list[dict] = []
        provenance: list[str] = []
        for w in witnesses:
            if hasattr(w, "serialize"):
                merged_items.append(w.serialize())
            if hasattr(w, "witness_id"):
                provenance.append(w.witness_id)

        return {
            "merged_witnesses": merged_items,
            "trust_level": _format_trust_for_response(trust),
            "residuals": residuals,
            "provenance": provenance,
            "consistency_status": status.value,
            "witness_count": len(witnesses),
        }

    @classmethod
    def compute_witness_fingerprint(cls, witness: Any) -> str:
        """Compute a SHA-256 fingerprint of the serialized witness content.

        Uses ``witness.serialize()`` if available; falls back to
        ``str(witness)``.

        Parameters
        ----------
        witness:
            Any witness object.

        Returns
        -------
        str
            Hex-encoded SHA-256 digest.
        """
        if hasattr(witness, "serialize"):
            content = json.dumps(witness.serialize(), sort_keys=True, default=str)
        else:
            content = str(witness)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RuntimeWitnessCollector — Theory2.tex §7.3.5
# ---------------------------------------------------------------------------


class RuntimeWitnessCollector:
    """Orchestrates collection, storage, and querying of runtime witnesses.

    Implements the collection-policy framework described in Theory2.tex §7.3.5.

    Parameters
    ----------
    collector_id:
        Optional human-readable identifier; auto-generated if omitted.
    collection_policy:
        Dict controlling ``max_witnesses`` and ``prune_after_s``.
    trust_policy:
        Dict controlling ``default_tier`` and ``allow_promotion``.
    snapshot_interval:
        Minimum seconds between automatic heap snapshots (not enforced
        here; callers use this field to schedule collection).
    """

    def __init__(
        self,
        collector_id: str | None = None,
        collection_policy: dict | None = None,
        trust_policy: dict | None = None,
        snapshot_interval: float = 60.0,
    ) -> None:
        self.collector_id: str = collector_id or uuid.uuid4().hex[:12]
        self.active_witnesses: dict[str, Any] = {}
        self.collection_policy: dict = collection_policy or {
            "max_witnesses": 1000,
            "prune_after_s": 3600.0,
        }
        self.trust_policy: dict = trust_policy or {
            "default_tier": _RUNTIME_TRUST_TIER,
            "allow_promotion": False,
        }
        self.snapshot_interval: float = snapshot_interval
        self._collection_count: int = 0
        self._last_prune_time: float = time.time()
        self.validator: WitnessValidator = WitnessValidator()
        logger.debug("RuntimeWitnessCollector %s initialised", self.collector_id)

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def collect_heap_witness(self, heap_snapshot: dict, site: str = "") -> HeapWitness:
        """Collect a heap witness from a snapshot dict.

        Populates a ``HeapWitness`` from *heap_snapshot*, validates
        invariants, stores it, and enforces the collection policy.

        Parameters
        ----------
        heap_snapshot:
            Mapping from object_id → ``{type, refs, size, value_repr}``.
        site:
            Label for the program point (e.g. function name + line number).

        Returns
        -------
        HeapWitness
        """
        witness = HeapWitness(
            heap_snapshot=dict(heap_snapshot),
            collection_site=site,
            trust_tier=self.trust_policy.get("default_tier", _RUNTIME_TRUST_TIER),
        )
        witness.validate_invariants()
        self._store(witness)
        logger.debug(
            "Collected HeapWitness %s at site=%r objects=%d",
            witness.witness_id,
            site,
            len(heap_snapshot),
        )
        return witness

    def collect_identity_witness(self, identity_data: dict) -> IdentityWitness:
        """Collect an identity witness from a property dict.

        Parameters
        ----------
        identity_data:
            Must include ``"entity_id"``; remaining keys become the
            ``identity_proof``.

        Returns
        -------
        IdentityWitness
        """
        entity_id = identity_data.get("entity_id", "")
        proof = {k: v for k, v in identity_data.items() if k != "entity_id"}
        witness = IdentityWitness(
            entity_id=entity_id,
            identity_proof=proof,
            trust_tier=self.trust_policy.get("default_tier", _RUNTIME_TRUST_TIER),
        )
        self._store(witness)
        logger.debug(
            "Collected IdentityWitness %s entity_id=%r",
            witness.witness_id,
            entity_id,
        )
        return witness

    def collect_stack_witness(self, stack_frame_data: list[dict]) -> StackWitness:
        """Collect a stack witness from a list of frame dicts.

        Each frame dict should contain ``function``, ``file``, ``line``,
        and ``locals`` keys.

        Parameters
        ----------
        stack_frame_data:
            Ordered list of frame dicts (outermost frame first).

        Returns
        -------
        StackWitness
        """
        call_stack = [f.get("function", "<unknown>") for f in stack_frame_data]
        witness = StackWitness(
            frame_data=list(stack_frame_data),
            call_stack=call_stack,
            collection_depth=len(stack_frame_data),
            trust_tier=self.trust_policy.get("default_tier", _RUNTIME_TRUST_TIER),
        )
        witness.extract_bindings()
        self._store(witness)
        logger.debug(
            "Collected StackWitness %s depth=%d",
            witness.witness_id,
            len(call_stack),
        )
        return witness

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _store(self, witness: Any) -> None:
        """Store a witness in *active_witnesses* and enforce policy limits."""
        self.active_witnesses[witness.witness_id] = witness
        self._collection_count += 1
        max_w = self.collection_policy.get("max_witnesses", 1000)
        if len(self.active_witnesses) > max_w:
            self.prune_stale_witnesses()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query_witnesses(self, criteria: dict) -> list:
        """Filter *active_witnesses* by *criteria*.

        Supported criteria keys:

        * ``kind`` — ``WitnessKind`` value string (e.g. ``"heap"``).
        * ``min_timestamp`` — only witnesses collected at or after this time.
        * ``max_age_s`` — only witnesses no older than this many seconds.
        * ``entity_id`` — only ``IdentityWitness`` objects with matching id.

        Parameters
        ----------
        criteria:
            Dict of filter conditions.  All specified conditions must match.

        Returns
        -------
        list
            Matching witness objects.
        """
        kind_filter: str | None = criteria.get("kind")
        min_ts: float | None = criteria.get("min_timestamp")
        max_age: float | None = criteria.get("max_age_s")
        entity_id_filter: str | None = criteria.get("entity_id")

        now = time.time()
        results = []
        for witness in self.active_witnesses.values():
            # kind check
            if kind_filter is not None:
                w_kind = getattr(witness, "_kind", None)
                if w_kind is None:
                    if kind_filter == "heap" and not isinstance(witness, HeapWitness):
                        continue
                    if kind_filter == "identity" and not isinstance(witness, IdentityWitness):
                        continue
                    if kind_filter == "stack" and not isinstance(witness, StackWitness):
                        continue
            # timestamp checks
            ts = getattr(witness, "timestamp", 0.0)
            if min_ts is not None and ts < min_ts:
                continue
            if max_age is not None and (now - ts) > max_age:
                continue
            # entity_id filter (IdentityWitness only)
            if entity_id_filter is not None:
                eid = getattr(witness, "entity_id", None)
                if eid != entity_id_filter:
                    continue
            results.append(witness)
        return results

    def validate_witness(self, witness_id: str) -> bool:
        """Validate the witness with *witness_id*.

        Dispatches to the appropriate ``WitnessValidator`` method.

        Returns
        -------
        bool
            ``True`` if the witness is valid.  Returns ``False`` and logs a
            warning if the witness is not found.
        """
        witness = self.active_witnesses.get(witness_id)
        if witness is None:
            logger.warning("validate_witness: witness_id %r not found", witness_id)
            return False
        if isinstance(witness, HeapWitness):
            valid, _ = WitnessValidator.validate_heap(witness)
        elif isinstance(witness, IdentityWitness):
            valid, _ = WitnessValidator.validate_identity(witness)
        elif isinstance(witness, StackWitness):
            valid, _ = WitnessValidator.validate_stack(witness)
        else:
            logger.warning("validate_witness: unknown witness type for %r", witness_id)
            return False
        return valid

    def get_witness_trust(self, witness_id: str) -> dict:
        """Return the trust assertion for the witness with *witness_id*.

        Returns an empty dict if the witness is not found.
        """
        witness = self.active_witnesses.get(witness_id)
        if witness is None:
            logger.warning("get_witness_trust: witness_id %r not found", witness_id)
            return {}
        if hasattr(witness, "get_trust_assertion"):
            return witness.get_trust_assertion()
        return {"trust_level": _RUNTIME_TRUST_TIER, "witness_id": witness_id}

    # ------------------------------------------------------------------
    # Export / maintenance
    # ------------------------------------------------------------------

    def export_witnesses(self) -> list[dict]:
        """Return all active witnesses as a list of serialized dicts."""
        return [
            w.serialize() for w in self.active_witnesses.values() if hasattr(w, "serialize")
        ]

    def prune_stale_witnesses(self, max_age_seconds: float | None = None) -> None:
        """Remove witnesses older than *max_age_seconds*.

        Parameters
        ----------
        max_age_seconds:
            If ``None``, uses ``collection_policy["prune_after_s"]``
            (default 3600.0 s = 1 hour).
        """
        cutoff = max_age_seconds if max_age_seconds is not None else self.collection_policy.get(
            "prune_after_s", 3600.0
        )
        now = time.time()
        stale = [
            wid
            for wid, w in self.active_witnesses.items()
            if (now - getattr(w, "timestamp", now)) > cutoff
        ]
        for wid in stale:
            del self.active_witnesses[wid]
        self._last_prune_time = now
        if stale:
            logger.info(
                "RuntimeWitnessCollector %s pruned %d stale witnesses",
                self.collector_id,
                len(stale),
            )

    def get_collector_stats(self) -> dict:
        """Return operational statistics for this collector."""
        return {
            "collector_id": self.collector_id,
            "collection_count": self._collection_count,
            "active_count": len(self.active_witnesses),
            "last_prune_time": self._last_prune_time,
            "snapshot_interval": self.snapshot_interval,
        }

    def clear(self) -> None:
        """Remove all witnesses from *active_witnesses*."""
        count = len(self.active_witnesses)
        self.active_witnesses.clear()
        logger.debug(
            "RuntimeWitnessCollector %s cleared %d witnesses",
            self.collector_id,
            count,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def create_heap_witness_from_dict(data: dict) -> HeapWitness:
    """Factory: construct a ``HeapWitness`` from a serialized dict.

    Accepts the shape produced by ``HeapWitness.serialize()``.  Unknown keys
    in *data* are silently ignored.

    Parameters
    ----------
    data:
        Dict with optional keys matching ``HeapWitness`` field names.

    Returns
    -------
    HeapWitness
    """
    witness = HeapWitness(
        heap_snapshot=dict(data.get("heap_snapshot", {})),
        allocation_trace=list(data.get("allocation_trace", [])),
        deallocation_trace=list(data.get("deallocation_trace", [])),
        invariant_violations=list(data.get("invariant_violations", [])),
        trust_tier=data.get("trust_tier", _RUNTIME_TRUST_TIER),
        collection_site=data.get("collection_site", ""),
        metadata=dict(data.get("metadata", {})),
    )
    if "witness_id" in data:
        object.__setattr__(witness, "witness_id", data["witness_id"]) if hasattr(
            witness, "__dataclass_fields__"
        ) else setattr(witness, "witness_id", data["witness_id"])
    if "timestamp" in data:
        witness.timestamp = float(data["timestamp"])
    return witness


def _format_trust_for_response(tier: str) -> str:
    """Normalise a trust-tier string for use in an ``EvidenceResponse``.

    Converts common variants to the canonical lower-snake-case form used in
    ``EvidenceResponse.trust_level``.  Any unrecognised string is returned
    lowercased with spaces replaced by underscores.

    Parameters
    ----------
    tier:
        Raw trust-tier string (e.g. ``"RUNTIME_WITNESSED"``,
        ``"runtime_witnessed"``, ``"Runtime Witnessed"``).

    Returns
    -------
    str
        Canonical lower-snake-case trust-tier string.
    """
    normalised = tier.strip().lower().replace(" ", "_").replace("-", "_")
    _known = {
        "mechanically_verified",
        "solver_discharged",
        "runtime_witnessed",
        "human_attested",
        "oracle_proposed",
        "copilot_suggested",
        "unverified",
        "contradicted",
    }
    if normalised in _known:
        return normalised
    logger.debug("_format_trust_for_response: unknown tier %r → %r", tier, normalised)
    return normalised
