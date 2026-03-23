"""
Finite maps (Python dicts) as explicit bipartite sheaves over key×value space.

# copilot: This module encodes Python dicts as sections of a bipartite sheaf F over
# KeySpace × ValueSpace. Each key kᵢ is an "address" in KeySpace, and the stalk
# F(kᵢ) = {vᵢ} is the singleton containing the value. A dict update d[k]=v is a
# sheaf transition that replaces the stalk at k. Two dicts can be merged iff they
# agree on overlapping keys (the gluing condition). The bipartite structure reflects
# the fact that keys and values live in disjoint "universes", connected only through
# the assignment relation.

Theory
------
A Python dict d = {k₁:v₁, k₂:v₂, ..., kₙ:vₙ} is formalised as a section of a
sheaf F over KeySpace × ValueSpace. Concretely:

  * KeySpace K = {k₁, k₂, ..., kₙ}           (left vertices)
  * ValueSpace V = {v₁, v₂, ..., vₙ}          (right vertices)
  * The "bipartite" open cover of K × V: for each key kᵢ, define the open set
    U(kᵢ) = {kᵢ} × V. The stalk F(kᵢ) is the set of values that kᵢ can take.
  * A section σ ∈ F(K) selects for each kᵢ exactly one element of F(kᵢ), i.e.
    one value vᵢ. This is precisely the dict d.

Sheaf transitions (dict updates):
  * d[k] = v  →  sheaf transition τ(k, vₒₗ_d, v_new): F(k) changes from {vₒₗ_d}
    to {v_new}. This is a morphism of stalks.

Gluing condition (dict merge):
  * Dicts dₐ and d_b can be merged iff for every key k in Keys(dₐ) ∩ Keys(d_b),
    dₐ[k] = d_b[k]. If this fails, there is a Čech 1-cocycle obstruction that
    prevents the two local sections from gluing to a global section.

Interface dictionaries:
  * An interface dict summary records the static/type-level structure: what keys
    are possible, bounds on cardinality, whether the function is total (defined on
    all possible keys).

References
----------
  * Spivak, D.I. "Category Theory for Scientists" — sheaves on simple categories
  * Mac Lane & Moerdijk "Sheaves in Geometry and Logic" — Section II.1
  * Ghrist, R. "Elementary Applied Topology" — sheaf cohomology primer
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from functools import reduce
from itertools import chain, product
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger(__name__)
_MODULE_VERSION: str = "0.4.0"
_MAX_KEY_REPR_LENGTH: int = 256
_MAX_VALUE_REPR_LENGTH: int = 512
_HASH_ALGORITHM: str = "sha256"
_MERGE_CONFLICT_PREFIX: str = "MERGE_CONFLICT"
_ENCODING_SENTINEL: str = "__JUGEO_FINITE_MAP__"

# ---------------------------------------------------------------------------
# Jugeo error imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"
        DESCENT_OBSTRUCTION = "descent_obstruction"
        UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError):  # type: ignore[no-redef]
        pass

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message

    def raise_with_scope(  # type: ignore[misc]
        code: str, *, message: str, provenance: Any = None, **kw: Any
    ) -> None:
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """
    Lattice of trust levels ordered by evidence strength.

    PROPOSAL          — oracle-proposed, not yet reviewed
    REVIEWED          — human-reviewed but not formally verified
    VERIFIED          — statically verified by type checker or SMT solver
    RUNTIME_WITNESSED — witnessed at runtime by a concrete execution
    PROOF_BACKED      — backed by a machine-checked proof
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Lattice join — least upper bound."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Lattice meet — greatest lower bound."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Increment trust by one level, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Decrement trust by one level, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    def is_at_least(self, threshold: TrustTier) -> bool:
        """True iff this tier satisfies *threshold* in the ordering ⪯."""
        return int(self) >= int(threshold)


# ---------------------------------------------------------------------------
# Judgment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Judgment:
    """
    A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Fields
    ------
    context     : the ambient context in which this judgment is made
    formula     : the proposition being asserted
    assumptions : tuple of background assumptions
    evidence    : tuple of evidence items supporting this judgment
    obligations : tuple of proof obligations that remain
    burden      : the party responsible for discharging obligations
    trust       : the TrustTier of this judgment
    provenance  : source/lineage of this judgment
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


# ---------------------------------------------------------------------------
# CechObstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CechObstruction:
    """
    A Čech H¹ cohomology class witnessing descent failure.

    When two local sections (dict fragments) cannot be glued because they
    disagree on overlapping keys, the failure is recorded as a non-trivial
    1-cocycle in the Čech complex of the cover. This obstruction prevents
    the existence of a global section.

    Fields
    ------
    cover_id         : identifier of the covering used to detect the obstruction
    cocycle          : frozenset of (key, val_a, val_b) conflict triples
    cohomology_class : string label for the Čech cohomology class
    description      : human-readable description of the failure
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the cocycle is empty (no conflicts → trivial class)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string to max_len, appending '...' if truncated."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _repr_of(obj: Any) -> str:
    """
    Compute a stable, truncated string representation of a Python object for use
    as a key or value repr in the stalk_map. Handles common types explicitly so
    that the repr is stable across Python sessions.
    """
    if obj is None:
        return "None"
    if isinstance(obj, bool):
        return str(obj)
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        return _truncate(repr(obj), _MAX_VALUE_REPR_LENGTH)
    if isinstance(obj, bytes):
        return _truncate(f"bytes({len(obj)})", _MAX_VALUE_REPR_LENGTH)
    if isinstance(obj, (list, tuple)):
        inner = ", ".join(_repr_of(x) for x in obj[:8])
        suffix = ", ..." if len(obj) > 8 else ""
        bracket = "[]" if isinstance(obj, list) else "()"
        return _truncate(f"{bracket[0]}{inner}{suffix}{bracket[1]}", _MAX_VALUE_REPR_LENGTH)
    if isinstance(obj, dict):
        inner = ", ".join(f"{_repr_of(k)}:{_repr_of(v)}" for k, v in list(obj.items())[:4])
        suffix = ", ..." if len(obj) > 4 else ""
        return _truncate("{" + inner + suffix + "}", _MAX_VALUE_REPR_LENGTH)
    if isinstance(obj, (set, frozenset)):
        inner = ", ".join(_repr_of(x) for x in sorted(str(x) for x in obj)[:4])
        suffix = ", ..." if len(obj) > 4 else ""
        return _truncate(f"{{{inner}{suffix}}}", _MAX_VALUE_REPR_LENGTH)
    return _truncate(repr(obj), _MAX_VALUE_REPR_LENGTH)


def _key_repr_of(obj: Any) -> str:
    """Like _repr_of but applies the stricter _MAX_KEY_REPR_LENGTH limit."""
    return _truncate(_repr_of(obj), _MAX_KEY_REPR_LENGTH)


def _hash_stalk_map(stalk_map: dict) -> str:
    """
    Compute a stable SHA-256 hash of a stalk_map (dict[str, str]).

    The map is serialised as a JSON array of sorted (key, value) pairs so that
    the hash is independent of Python dict insertion order.
    """
    pairs = sorted(stalk_map.items())
    raw = json.dumps(pairs, ensure_ascii=True, sort_keys=True)
    return hashlib.new(_HASH_ALGORITHM, raw.encode()).hexdigest()


def _new_id(prefix: str = "") -> str:
    """Generate a fresh UUID-based identifier with optional prefix."""
    uid = str(uuid.uuid4()).replace("-", "")
    return f"{prefix}{uid}" if prefix else uid


# ---------------------------------------------------------------------------
# FiniteMapEncoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FiniteMapEncoding:
    """
    Encoding of a finite Python dict as a bipartite sheaf section.

    A FiniteMapEncoding records, for each key kᵢ, the repr of both the key and
    its associated value (the stalk). The stalk_map is the sheaf section: a
    mapping from key_repr strings to value_repr strings.

    Sheaf-theoretic interpretation
    --------------------------------
    * BaseSpace  = {key_repr strings}       (the discrete topological space K)
    * ValueSpace = {value_repr strings}     (the discrete space V)
    * Stalk at k = {stalk_map[k]}           (a singleton)
    * Section    = the stalk_map itself

    Fields
    ------
    map_id      : unique identifier for this encoding
    key_type    : string name of the key type (e.g. "str", "int")
    value_type  : string name of the value type (e.g. "str", "int", "Any")
    stalk_map   : the section — maps key_repr to value_repr
    trust       : TrustTier of this encoding
    provenance  : origin of this encoding (e.g. call-site, timestamp, …)
    """

    map_id: str
    key_type: str
    value_type: str
    stalk_map: dict  # key_repr (str) → value_repr (str)
    trust: TrustTier
    provenance: Any

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def key_count(self) -> int:
        """Return the number of keys in this finite map (the section's support)."""
        return len(self.stalk_map)

    def has_key(self, k: Any) -> bool:
        """Return True iff the key k (or its repr) exists in the stalk_map."""
        kr = _key_repr_of(k)
        return kr in self.stalk_map

    def stalk_at(self, k: Any) -> Optional[str]:
        """
        Return the value_repr at key k, or None if k is not in the map.

        This is the sheaf operation of taking the stalk at a point.
        """
        kr = _key_repr_of(k)
        return self.stalk_map.get(kr)

    def keys(self) -> frozenset:
        """Return the set of all key_repr strings (the support of the section)."""
        return frozenset(self.stalk_map.keys())

    def overlap_keys(self, other: FiniteMapEncoding) -> frozenset:
        """
        Return the set of key_repr strings shared between self and other.

        This is the intersection of the two supports; the gluing condition for
        the sheaf merge must be checked on exactly these keys.
        """
        return self.keys() & other.keys()

    def encoding_hash(self) -> str:
        """
        Compute a content-addressed hash of this encoding.

        The hash is over (map_id, key_type, value_type, stalk_map) so that two
        encodings with identical structure but different provenance still hash
        equally if their content is the same.
        """
        return _hash_stalk_map(
            {
                "__map_id__": self.map_id,
                "__key_type__": self.key_type,
                "__value_type__": self.value_type,
                **self.stalk_map,
            }
        )

    def to_judgment(self) -> Judgment:
        """
        Lift this encoding to a Judgment asserting that the encoding is valid.

        The formula states that the stalk_map is a consistent section of the
        bipartite sheaf F over KeySpace × ValueSpace.
        """
        formula = (
            f"IsSection(F, stalk_map={self.encoding_hash()[:16]},"
            f" key_type={self.key_type}, value_type={self.value_type})"
        )
        return Judgment(
            context={"map_id": self.map_id, "version": _MODULE_VERSION},
            formula=formula,
            assumptions=(),
            evidence=({"encoding_hash": self.encoding_hash()},),
            obligations=(),
            burden=None,
            trust=self.trust,
            provenance=self.provenance,
        )


# ---------------------------------------------------------------------------
# KeyValueSheaf
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyValueSheaf:
    """
    An explicit bipartite sheaf over KeySpace × ValueSpace.

    This is the full sheaf-theoretic object of which a FiniteMapEncoding is a
    section. The KeyValueSheaf records not just the section but also:
    * The full key_space and value_space (the vertices of the bipartite graph)
    * All sections chosen so far (a tuple of (key, value) assignment pairs)
    * Restriction maps: for each pair (src_key, tgt_key) there may be a
      restriction morphism that sends F(src_key) to F(tgt_key).

    Restriction maps arise when the key space carries additional structure; e.g.
    when the keys are types and there is a subtype relation, restricting from a
    supertype key to a subtype key.

    Fields
    ------
    sheaf_id          : unique identifier
    key_space         : frozenset of key_repr strings
    value_space       : frozenset of value_repr strings
    sections          : tuple of (key_repr, value_repr) pairs (the chosen section)
    restriction_maps  : tuple of (src_key_repr, tgt_key_repr, fn_repr) triples
    """

    sheaf_id: str
    key_space: frozenset
    value_space: frozenset
    sections: tuple  # of (key_repr: str, value_repr: str) pairs
    restriction_maps: tuple  # of (src_key, tgt_key, restriction_fn_repr) triples

    def section_at(self, key: Any) -> Optional[Any]:
        """
        Return the value assigned to key in the chosen section, or None.

        Iterates the sections tuple linearly; for large sheaves a dict would be
        more efficient, but the frozen dataclass constraint requires a tuple.
        """
        kr = _key_repr_of(key)
        for k, v in self.sections:
            if k == kr:
                return v
        return None

    def is_consistent(self) -> bool:
        """
        Check that no key appears twice with different values in sections.

        A sheaf section must assign a unique stalk element to each point of the
        base space. Duplicate keys with differing values violate this.
        """
        seen: Dict[str, str] = {}
        for k, v in self.sections:
            if k in seen and seen[k] != v:
                return False
            seen[k] = v
        return True

    def total_space_size(self) -> int:
        """Return |KeySpace| × |ValueSpace| — the size of the total bipartite graph."""
        return len(self.key_space) * len(self.value_space)

    def to_bipartite_repr(self) -> dict:
        """
        Return a JSON-serialisable bipartite graph representation.

        Keys become left-vertices, values become right-vertices, and each
        section pair becomes a directed edge from left to right.
        """
        edges = [{"from": k, "to": v} for k, v in self.sections]
        return {
            "sheaf_id": self.sheaf_id,
            "left_vertices": sorted(self.key_space),
            "right_vertices": sorted(self.value_space),
            "edges": edges,
            "restriction_count": len(self.restriction_maps),
        }


# ---------------------------------------------------------------------------
# DictInterfaceSummary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DictInterfaceSummary:
    """
    Static/type-level summary of a dict interface (i.e. a dict type annotation).

    This encodes the interface of a dict as seen by a static analysis tool: what
    keys are possible, how many keys are there (bounds), and whether the mapping
    is total (defined for all possible keys).

    Sheaf interpretation
    --------------------
    An interface summary is a description of the *type* of the sheaf F rather
    than a specific section. It records:
    * The domain of discourse for KeySpace (possible_keys)
    * Cardinality bounds (known_key_bounds)
    * Whether F is defined everywhere on KeySpace (is_total)

    Fields
    ------
    dict_id          : identifier linking back to the map being summarised
    key_type_repr    : string repr of the key type annotation
    value_type_repr  : string repr of the value type annotation
    possible_keys    : frozenset of known possible key_repr strings
    known_key_bounds : (lower, upper) bounds on dict cardinality; upper=-1 → ∞
    is_total         : True if every key in possible_keys is guaranteed to be present
    trust            : TrustTier of this summary
    """

    dict_id: str
    key_type_repr: str
    value_type_repr: str
    possible_keys: frozenset
    known_key_bounds: tuple  # (int, int)  upper = -1 means unbounded
    is_total: bool
    trust: TrustTier

    def key_in_domain(self, k: Any) -> bool:
        """
        Return True iff k (or its repr) is in the known possible_keys domain.

        Note: this is a sound under-approximation. A key not in possible_keys
        may still be present at runtime if possible_keys is not exhaustive.
        """
        kr = _key_repr_of(k)
        return kr in self.possible_keys

    def summary_hash(self) -> str:
        """Stable hash of the summary's type structure (not the dict_id)."""
        payload = {
            "key_type_repr": self.key_type_repr,
            "value_type_repr": self.value_type_repr,
            "possible_keys": sorted(self.possible_keys),
            "known_key_bounds": list(self.known_key_bounds),
            "is_total": self.is_total,
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.new(_HASH_ALGORITHM, raw.encode()).hexdigest()

    def to_uninterpreted_fn(self) -> str:
        """
        Render this summary as an SMT-style uninterpreted function declaration.

        Example output:
          (declare-fun dict_abc123 (key_type) (Option value_type))
        """
        domain = self.key_type_repr
        codomain = self.value_type_repr
        totality = "" if self.is_total else "(Option "
        close = "" if self.is_total else ")"
        return f"(declare-fun {self.dict_id} ({domain}) {totality}{codomain}{close})"


# ---------------------------------------------------------------------------
# MapUpdateObligation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MapUpdateObligation:
    """
    Proof obligation generated by a sheaf transition (dict update).

    When d[k] = v is executed, we must discharge the obligation that the new
    stalk F(k) = {v_new} is consistent with the rest of the section. In
    particular:
    * If k was already in the map, the old stalk must be recorded and
      the transition must be justified.
    * If k was not in the map, this is a fresh insert and the obligation is
      weaker (no stalk-preservation requirement).

    Fields
    ------
    map_id          : the map being updated
    key_repr        : repr of the key being updated
    old_value_repr  : repr of the old value (None if fresh insert)
    new_value_repr  : repr of the new value
    obligation_id   : unique identifier for this obligation
    trust           : TrustTier
    """

    map_id: str
    key_repr: str
    old_value_repr: Optional[str]
    new_value_repr: str
    obligation_id: str
    trust: TrustTier

    def is_fresh_insert(self) -> bool:
        """Return True iff the key was not previously in the map (stalk was empty)."""
        return self.old_value_repr is None

    def consistency_formula(self) -> str:
        """
        Return a string formula asserting the consistency of this update.

        For a fresh insert: Satisfiable(F(k) := {v_new})
        For an overwrite:   Consistent(F(k): {v_old} → {v_new}, map_id)
        """
        if self.is_fresh_insert():
            return (
                f"FreshInsert(map={self.map_id!r}, key={self.key_repr!r},"
                f" value={self.new_value_repr!r})"
            )
        return (
            f"StalkTransition(map={self.map_id!r}, key={self.key_repr!r},"
            f" old={self.old_value_repr!r}, new={self.new_value_repr!r})"
        )

    def to_judgment(self) -> Judgment:
        """Lift this obligation into a pending Judgment."""
        return Judgment(
            context={"map_id": self.map_id, "obligation_id": self.obligation_id},
            formula=self.consistency_formula(),
            assumptions=(),
            evidence=(),
            obligations=(self.consistency_formula(),),
            burden="caller",
            trust=self.trust,
            provenance={"key_repr": self.key_repr, "new_value_repr": self.new_value_repr},
        )


# ---------------------------------------------------------------------------
# MapMorphism
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MapMorphism:
    """
    A morphism between two finite-map encodings viewed as sheaf sections.

    A MapMorphism consists of:
    * A key_map: for each key_repr in the source, optionally a key_repr in the target
    * A value_map: for each value_repr in the source, a value_repr in the target

    This is a morphism of the underlying bipartite sheaves: it sends the source
    section to a section over the target.

    Injectivity: the key_map is injective (no two source keys map to the same target)
    Totality:    the key_map is total (every source key has an image)

    Fields
    ------
    source_map_id : map_id of the source encoding
    target_map_id : map_id of the target encoding
    morphism_id   : unique identifier
    key_map       : dict[str, str]  source_key_repr → target_key_repr
    value_map     : dict[str, str]  source_value_repr → target_value_repr
    is_injective  : True iff the key_map has no collisions
    is_total      : True iff every source key_repr appears in key_map
    """

    source_map_id: str
    target_map_id: str
    morphism_id: str
    key_map: dict   # source_key_repr → target_key_repr
    value_map: dict  # source_value_repr → target_value_repr
    is_injective: bool
    is_total: bool

    def apply_to_key(self, k: str) -> Optional[str]:
        """Apply the key_map to a source key_repr, returning the image or None."""
        return self.key_map.get(k)

    def is_identity(self) -> bool:
        """
        Return True iff this morphism is the identity (key_map and value_map are both id).

        An identity morphism has source == target and every key/value maps to itself.
        """
        if self.source_map_id != self.target_map_id:
            return False
        keys_ok = all(v == k for k, v in self.key_map.items())
        vals_ok = all(v == k for k, v in self.value_map.items())
        return keys_ok and vals_ok

    def compose(self, other: MapMorphism) -> MapMorphism:
        """
        Compose self (f: A→B) with other (g: B→C) to get g∘f: A→C.

        Requires self.target_map_id == other.source_map_id.
        """
        if self.target_map_id != other.source_map_id:
            raise JuGeoError(
                f"Cannot compose morphisms: target {self.target_map_id!r} ≠ "
                f"source {other.source_map_id!r}"
            )
        composed_key_map = {
            k: other.key_map[v]
            for k, v in self.key_map.items()
            if v in other.key_map
        }
        composed_value_map = {
            k: other.value_map[v]
            for k, v in self.value_map.items()
            if v in other.value_map
        }
        composed_injective = self.is_injective and other.is_injective
        composed_total = (
            self.is_total
            and all(v in other.key_map for v in self.key_map.values())
        )
        return MapMorphism(
            source_map_id=self.source_map_id,
            target_map_id=other.target_map_id,
            morphism_id=_new_id("morph_"),
            key_map=composed_key_map,
            value_map=composed_value_map,
            is_injective=composed_injective,
            is_total=composed_total,
        )


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def encode_finite_map(
    d: dict,
    map_id: str = "",
    trust: TrustTier = TrustTier.PROPOSAL,
) -> FiniteMapEncoding:
    """
    Encode a Python dict as a FiniteMapEncoding (a bipartite sheaf section).

    Parameters
    ----------
    d       : the dict to encode
    map_id  : optional identifier; auto-generated if empty
    trust   : TrustTier for the resulting encoding

    Returns
    -------
    FiniteMapEncoding with stalk_map populated from d.

    The key_type and value_type are inferred from the Python types of the
    first key and value; empty dict → "unknown" / "unknown".

    Algorithm
    ---------
    1. Compute key_repr and value_repr for each entry using _key_repr_of / _repr_of.
    2. Build stalk_map = {key_repr: value_repr, ...}.
    3. Infer key_type and value_type from the actual runtime types.
    4. Construct FiniteMapEncoding with the given trust and a timestamp provenance.

    Notes
    -----
    * If two keys have the same repr (e.g. 1 and True in Python), the later
      entry wins in stalk_map. A warning is logged.
    * The encoding is purely structural: it does not evaluate the values.
    """
    mid = map_id or _new_id("fmap_")
    stalk_map: Dict[str, str] = {}
    key_types: List[str] = []
    value_types: List[str] = []

    for k, v in d.items():
        kr = _key_repr_of(k)
        vr = _repr_of(v)
        if kr in stalk_map and stalk_map[kr] != vr:
            _LOGGER.warning(
                "encode_finite_map: key_repr collision for %r (old=%r, new=%r)",
                kr,
                stalk_map[kr],
                vr,
            )
        stalk_map[kr] = vr
        key_types.append(type(k).__name__)
        value_types.append(type(v).__name__)

    key_type = key_types[0] if len(set(key_types)) == 1 else ("Union[" + ",".join(sorted(set(key_types))) + "]")
    value_type = value_types[0] if len(set(value_types)) == 1 else ("Union[" + ",".join(sorted(set(value_types))) + "]")
    if not key_types:
        key_type = "unknown"
        value_type = "unknown"

    provenance = {
        "encoded_at": time.time(),
        "source": "encode_finite_map",
        "original_len": len(d),
    }
    enc = FiniteMapEncoding(
        map_id=mid,
        key_type=key_type,
        value_type=value_type,
        stalk_map=stalk_map,
        trust=trust,
        provenance=provenance,
    )
    _LOGGER.debug("encode_finite_map: encoded %d keys → map_id=%s", len(stalk_map), mid)
    return enc


def dict_update_as_transition(
    encoding: FiniteMapEncoding,
    key: Any,
    new_value: Any,
) -> tuple[FiniteMapEncoding, MapUpdateObligation]:
    """
    Model a dict update d[key] = new_value as a sheaf transition.

    Returns a pair:
    * The new FiniteMapEncoding after the update (new stalk at key)
    * A MapUpdateObligation recording the before/after stalks and requiring
      that the transition be justified.

    Sheaf-theoretic view
    --------------------
    The update replaces the stalk F(key_repr) from {old_val_repr} to {new_val_repr}.
    The obligation asserts that this replacement is semantically valid.

    Parameters
    ----------
    encoding  : the before-state encoding
    key       : the key being updated (any Python object)
    new_value : the new value (any Python object)

    Returns
    -------
    (new_encoding, obligation) where new_encoding.stalk_map[key_repr] = new_value_repr
    """
    kr = _key_repr_of(key)
    nvr = _repr_of(new_value)
    old_vr = encoding.stalk_map.get(kr)

    new_stalk_map = dict(encoding.stalk_map)
    new_stalk_map[kr] = nvr

    new_encoding = FiniteMapEncoding(
        map_id=encoding.map_id,
        key_type=encoding.key_type,
        value_type=encoding.value_type,
        stalk_map=new_stalk_map,
        trust=encoding.trust.demote(),  # update demotes trust until re-verified
        provenance={
            "updated_at": time.time(),
            "key_repr": kr,
            "old_value_repr": old_vr,
            "new_value_repr": nvr,
            "parent_hash": encoding.encoding_hash(),
        },
    )

    obligation = MapUpdateObligation(
        map_id=encoding.map_id,
        key_repr=kr,
        old_value_repr=old_vr,
        new_value_repr=nvr,
        obligation_id=_new_id("oblig_"),
        trust=TrustTier.PROPOSAL,
    )
    return new_encoding, obligation


def map_merge_gluing(
    enc_a: FiniteMapEncoding,
    enc_b: FiniteMapEncoding,
) -> FiniteMapEncoding | CechObstruction:
    """
    Merge two FiniteMapEncodings, checking the sheaf gluing condition.

    Two sections sₐ and s_b of the sheaf F can be glued to a global section iff
    they agree on all overlapping keys (the sheaf axiom / gluing condition). If
    they agree, the merged section is sₐ ∪ s_b. If they disagree on any key, a
    CechObstruction is returned, representing the non-trivial 1-cocycle.

    Parameters
    ----------
    enc_a : first FiniteMapEncoding
    enc_b : second FiniteMapEncoding

    Returns
    -------
    FiniteMapEncoding (merged) if the gluing condition holds, otherwise
    CechObstruction recording the conflicting keys.

    Algorithm
    ---------
    1. Compute overlap_keys = keys(enc_a) ∩ keys(enc_b).
    2. For each key k in overlap_keys, check enc_a.stalk_map[k] == enc_b.stalk_map[k].
    3. If any conflict, collect all conflict triples (k, val_a, val_b) and return
       a CechObstruction with these as the cocycle.
    4. If no conflict, build merged_stalk_map = enc_a.stalk_map | enc_b.stalk_map
       and return a new FiniteMapEncoding with trust = enc_a.trust.meet(enc_b.trust).
    """
    overlap = enc_a.overlap_keys(enc_b)
    conflicts: set = set()
    for k in overlap:
        va = enc_a.stalk_map[k]
        vb = enc_b.stalk_map[k]
        if va != vb:
            conflicts.add((k, va, vb))

    if conflicts:
        cover_id = f"cover_{enc_a.map_id}_{enc_b.map_id}"
        conflict_keys = ", ".join(sorted(t[0] for t in conflicts))
        obstruction = CechObstruction(
            cover_id=cover_id,
            cocycle=frozenset(conflicts),
            cohomology_class=f"H1_conflict({cover_id})",
            description=(
                f"Čech obstruction: dicts {enc_a.map_id!r} and {enc_b.map_id!r} "
                f"disagree on keys: {conflict_keys}"
            ),
        )
        _LOGGER.warning(
            "map_merge_gluing: gluing obstruction on %d keys between %s and %s",
            len(conflicts),
            enc_a.map_id,
            enc_b.map_id,
        )
        return obstruction

    merged = {**enc_a.stalk_map, **enc_b.stalk_map}
    merged_trust = enc_a.trust.meet(enc_b.trust)
    return FiniteMapEncoding(
        map_id=_new_id("merged_"),
        key_type=(
            enc_a.key_type
            if enc_a.key_type == enc_b.key_type
            else f"Union[{enc_a.key_type},{enc_b.key_type}]"
        ),
        value_type=(
            enc_a.value_type
            if enc_a.value_type == enc_b.value_type
            else f"Union[{enc_a.value_type},{enc_b.value_type}]"
        ),
        stalk_map=merged,
        trust=merged_trust,
        provenance={
            "merged_from": [enc_a.map_id, enc_b.map_id],
            "merged_at": time.time(),
            "overlap_key_count": len(overlap),
        },
    )


def encode_dict_interface(encoding: FiniteMapEncoding) -> DictInterfaceSummary:
    """
    Build a DictInterfaceSummary from a FiniteMapEncoding.

    The summary captures the type-level (static) information that would appear
    in a type annotation such as Dict[str, int]. It is a sound abstraction: the
    possible_keys are exactly the observed keys, bounds are tight given the
    observed data, and is_total is True iff the encoding was constructed from a
    complete dict (not a partial view).

    Parameters
    ----------
    encoding : the FiniteMapEncoding to summarise

    Returns
    -------
    DictInterfaceSummary with possible_keys = encoding.keys()

    Notes
    -----
    * is_total is True because encode_finite_map captures the entire dict.
    * known_key_bounds = (n, n) where n = encoding.key_count().
    """
    n = encoding.key_count()
    return DictInterfaceSummary(
        dict_id=encoding.map_id,
        key_type_repr=encoding.key_type,
        value_type_repr=encoding.value_type,
        possible_keys=encoding.keys(),
        known_key_bounds=(n, n),
        is_total=True,
        trust=encoding.trust,
    )


# ---------------------------------------------------------------------------
# Additional utility: building a KeyValueSheaf from an encoding
# ---------------------------------------------------------------------------


def encoding_to_sheaf(encoding: FiniteMapEncoding) -> KeyValueSheaf:
    """
    Construct the explicit KeyValueSheaf corresponding to a FiniteMapEncoding.

    The key_space is the set of key_repr strings, the value_space is the set of
    value_repr strings, and the sections are the (key_repr, value_repr) pairs
    from the stalk_map.

    Parameters
    ----------
    encoding : the source FiniteMapEncoding

    Returns
    -------
    KeyValueSheaf representing the bipartite sheaf structure
    """
    key_space = frozenset(encoding.stalk_map.keys())
    value_space = frozenset(encoding.stalk_map.values())
    sections = tuple((k, v) for k, v in encoding.stalk_map.items())
    return KeyValueSheaf(
        sheaf_id=_new_id("sheaf_"),
        key_space=key_space,
        value_space=value_space,
        sections=sections,
        restriction_maps=(),
    )


def identity_morphism(encoding: FiniteMapEncoding) -> MapMorphism:
    """
    Construct the identity morphism on a FiniteMapEncoding.

    The identity morphism is the unique morphism f: enc → enc such that
    f(k) = k for every key and f(v) = v for every value.
    """
    key_map = {k: k for k in encoding.stalk_map}
    value_map = {v: v for v in encoding.stalk_map.values()}
    return MapMorphism(
        source_map_id=encoding.map_id,
        target_map_id=encoding.map_id,
        morphism_id=_new_id("id_morph_"),
        key_map=key_map,
        value_map=value_map,
        is_injective=True,
        is_total=True,
    )


def restrict_encoding(
    encoding: FiniteMapEncoding, key_subset: frozenset
) -> FiniteMapEncoding:
    """
    Compute the restriction of a FiniteMapEncoding to a subset of keys.

    This is the sheaf restriction operation: given an encoding over K and a
    subset S ⊆ K, return the encoding over S with stalk_map restricted to S.

    Parameters
    ----------
    encoding   : the full encoding
    key_subset : frozenset of key_repr strings to keep

    Returns
    -------
    A new FiniteMapEncoding with stalk_map restricted to key_subset.
    """
    restricted = {k: v for k, v in encoding.stalk_map.items() if k in key_subset}
    return FiniteMapEncoding(
        map_id=_new_id("restricted_"),
        key_type=encoding.key_type,
        value_type=encoding.value_type,
        stalk_map=restricted,
        trust=encoding.trust,
        provenance={
            "restricted_from": encoding.map_id,
            "key_subset_size": len(key_subset),
            "restricted_at": time.time(),
        },
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "DictInterfaceSummary",
    "FiniteMapEncoding",
    "KeyValueSheaf",
    "MapMorphism",
    "MapUpdateObligation",
    "TrustTier",
    "dict_update_as_transition",
    "encode_dict_interface",
    "encode_finite_map",
    "encoding_to_sheaf",
    "identity_morphism",
    "map_merge_gluing",
    "restrict_encoding",
]


# ---------------------------------------------------------------------------
# __main__ smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger(__name__)

    print("=" * 70)
    print(f"finite_maps_and_interface_dictiona.py  v{_MODULE_VERSION}")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # 1. Encode a simple dict
    # -----------------------------------------------------------------------
    d1 = {"alpha": 1, "beta": 2, "gamma": 3}
    enc1 = encode_finite_map(d1, map_id="map_alpha", trust=TrustTier.REVIEWED)
    print(f"\n[1] FiniteMapEncoding of {d1}")
    print(f"    key_count  = {enc1.key_count()}")
    print(f"    has_key('alpha') = {enc1.has_key('alpha')}")
    print(f"    stalk_at('beta') = {enc1.stalk_at('beta')}")
    print(f"    encoding_hash    = {enc1.encoding_hash()[:32]}...")
    assert enc1.key_count() == 3
    assert enc1.has_key("alpha")
    assert enc1.stalk_at("beta") is not None

    # -----------------------------------------------------------------------
    # 2. Dict update as sheaf transition
    # -----------------------------------------------------------------------
    enc2, oblig = dict_update_as_transition(enc1, "alpha", 999)
    print(f"\n[2] After update d['alpha'] = 999")
    print(f"    new stalk at 'alpha' = {enc2.stalk_at('alpha')}")
    print(f"    obligation: is_fresh_insert = {oblig.is_fresh_insert()}")
    print(f"    consistency_formula: {oblig.consistency_formula()}")
    judgment = oblig.to_judgment()
    print(f"    judgment.trust = {judgment.trust.name}")
    assert enc2.stalk_at("alpha") == repr(999)
    assert not oblig.is_fresh_insert()

    # -----------------------------------------------------------------------
    # 3. Fresh insert
    # -----------------------------------------------------------------------
    enc3, fresh_oblig = dict_update_as_transition(enc1, "delta", "new_val")
    print(f"\n[3] Fresh insert 'delta'")
    print(f"    is_fresh_insert = {fresh_oblig.is_fresh_insert()}")
    assert fresh_oblig.is_fresh_insert()

    # -----------------------------------------------------------------------
    # 4. Map merge — no conflict
    # -----------------------------------------------------------------------
    d2 = {"epsilon": 5, "zeta": 6}
    enc_b = encode_finite_map(d2, trust=TrustTier.VERIFIED)
    merged = map_merge_gluing(enc1, enc_b)
    print(f"\n[4] Merge disjoint maps → type: {type(merged).__name__}")
    assert isinstance(merged, FiniteMapEncoding)
    assert merged.key_count() == 5

    # -----------------------------------------------------------------------
    # 5. Map merge — conflict → CechObstruction
    # -----------------------------------------------------------------------
    d_conflict = {"alpha": 99, "eta": 7}
    enc_conflict = encode_finite_map(d_conflict, trust=TrustTier.PROPOSAL)
    result = map_merge_gluing(enc1, enc_conflict)
    print(f"\n[5] Merge conflicting maps → type: {type(result).__name__}")
    assert isinstance(result, CechObstruction)
    print(f"    cocycle size: {len(result.cocycle)}")
    print(f"    is_trivial:   {result.is_trivial()}")
    assert not result.is_trivial()

    # -----------------------------------------------------------------------
    # 6. DictInterfaceSummary
    # -----------------------------------------------------------------------
    summary = encode_dict_interface(enc1)
    print(f"\n[6] DictInterfaceSummary")
    print(f"    key_type_repr   = {summary.key_type_repr}")
    print(f"    value_type_repr = {summary.value_type_repr}")
    print(f"    known_key_bounds = {summary.known_key_bounds}")
    print(f"    is_total        = {summary.is_total}")
    print(f"    to_uninterpreted_fn = {summary.to_uninterpreted_fn()}")
    assert summary.is_total
    assert summary.known_key_bounds == (3, 3)

    # -----------------------------------------------------------------------
    # 7. KeyValueSheaf bipartite repr
    # -----------------------------------------------------------------------
    sheaf = encoding_to_sheaf(enc1)
    bipartite = sheaf.to_bipartite_repr()
    print(f"\n[7] KeyValueSheaf")
    print(f"    left_vertices  = {bipartite['left_vertices']}")
    print(f"    right_vertices = {bipartite['right_vertices']}")
    print(f"    total_space_size = {sheaf.total_space_size()}")
    assert sheaf.is_consistent()

    # -----------------------------------------------------------------------
    # 8. Identity morphism
    # -----------------------------------------------------------------------
    id_morph = identity_morphism(enc1)
    print(f"\n[8] Identity morphism: is_identity = {id_morph.is_identity()}")
    assert id_morph.is_identity()

    # -----------------------------------------------------------------------
    # 9. Morphism composition
    # -----------------------------------------------------------------------
    d3 = {"'alpha'": "X", "'beta'": "Y", "'gamma'": "Z"}
    enc_tgt = encode_finite_map(d3, map_id="map_target", trust=TrustTier.REVIEWED)
    km = {k: k for k in enc1.stalk_map}
    vm = {v: _repr_of("X") for v in enc1.stalk_map.values()}
    morph_f = MapMorphism(
        source_map_id=enc1.map_id,
        target_map_id=enc1.map_id,
        morphism_id="morph_f",
        key_map=km,
        value_map=vm,
        is_injective=True,
        is_total=True,
    )
    morph_g = MapMorphism(
        source_map_id=enc1.map_id,
        target_map_id="other_map",
        morphism_id="morph_g",
        key_map=km,
        value_map=vm,
        is_injective=True,
        is_total=True,
    )
    composed = morph_f.compose(morph_g)
    print(f"\n[9] Composed morphism: {composed.source_map_id!r} → {composed.target_map_id!r}")

    # -----------------------------------------------------------------------
    # 10. Restriction
    # -----------------------------------------------------------------------
    subset = frozenset(["'alpha'", "'beta'"])
    restricted = restrict_encoding(enc1, subset)
    print(f"\n[10] Restriction to {subset}: key_count = {restricted.key_count()}")

    # -----------------------------------------------------------------------
    # 11. TrustTier lattice
    # -----------------------------------------------------------------------
    ta = TrustTier.PROPOSAL
    tb = TrustTier.PROOF_BACKED
    print(f"\n[11] TrustTier: {ta.name}.join({tb.name}) = {ta.join(tb).name}")
    print(f"     {ta.name}.meet({tb.name}) = {ta.meet(tb).name}")
    print(f"     {ta.name}.promote() = {ta.promote().name}")
    print(f"     {tb.name}.demote()  = {tb.demote().name}")
    assert ta.join(tb) == TrustTier.PROOF_BACKED
    assert ta.meet(tb) == TrustTier.PROPOSAL
    assert ta.promote() == TrustTier.REVIEWED
    assert tb.demote() == TrustTier.RUNTIME_WITNESSED

    print("\n✓ All smoke tests passed.")
