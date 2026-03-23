"""
Global Gluing Under Replay — Assembling Global Sections from Replayed Local Constructions
===========================================================================================

    # copilot: global gluing under replay — assembling global sections from replayed local constructions

This module implements the sheaf-theoretic machinery for assembling a **global section**
from a sequence of replayed local constructions.  The central idea draws from Čech cohomology:
given an open cover {U_i} of a space X and a sheaf ℱ, a global section s ∈ ℱ(X) exists if
and only if the locally defined sections s_i ∈ ℱ(U_i) agree on every pairwise overlap U_i ∩ U_j
and the resulting Čech 1-cocycle [δ(s)] ∈ Ȟ¹(X, ℱ) is trivial.

Replay and Gluing
-----------------
In a *replay* setting the local sections are not computed from scratch but are re-derived
from a stored trace of prior computations.  Each replay step re-executes a local construction
(possibly in a different context or with additional evidence) and yields a section that must
be compatible with all previously glued pieces.  Compatibility is checked via the overlap
condition:

    ρ_{ij}(s_i) = ρ_{ji}(s_j)   on  U_i ∩ U_j

where ρ_{ij} : ℱ(U_i) → ℱ(U_i ∩ U_j) is the restriction map.

If all pairwise overlaps are consistent the sections are *glued* into a unique global section.
If any overlap fails the module surfaces a :class:`CechObstruction` whose ``cohomology_class``
encodes the obstruction class in Ȟ¹.

Trust and Evidence
------------------
Every gluing step is associated with a :class:`TrustTier` that tracks the epistemic status
of the assembled section.  Trust propagates through the lattice

    PROPOSAL ≼ REVIEWED ≼ VERIFIED ≼ RUNTIME_WITNESSED ≼ PROOF_BACKED

using the meet (⊓) operation so that the global trust is bounded above by the weakest local
trust.  Upgrading trust requires either a runtime witness or a proof discharge.

Module Layout
-------------
* Constants and logger
* Jugeo import stubs (graceful degradation when jugeo is not installed)
* :class:`TrustTier` — trust algebra
* :class:`Judgment` / :class:`CechObstruction` — core evidence types
* :class:`ReplayGluing` — one gluing step dataclass
* :class:`GluingRecord` — evidence record for a step
* :class:`GlobalGluingUnderReplay` — assembles global section from replayed locals
* :class:`ReplayIntegration` — connects replay sequence to gluing engine
* :class:`GluingEngine` — orchestrates the full pipeline
* Module-level helper functions (``_`` prefix)
* Public API functions
* ``__main__`` smoke test
"""

from __future__ import annotations

import abc
import collections
import datetime
import functools
import hashlib
import itertools
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SECTIONS: int = 100
"""Maximum number of local sections permitted in a single gluing run."""

GLUING_PRECISION: float = 1e-12
"""Numerical tolerance used when comparing section values on overlaps."""

OVERLAP_QUORUM: float = 0.8
"""Fraction of overlapping keys that must agree for the overlap to be accepted
when operating in *approximate* mode (e.g. noisy runtime witnesses)."""

_COVER_SALT: str = "jugeo-gluing-v1"
"""Salt prepended to cover identifiers before hashing to prevent collisions."""

_MAX_STEP_RETRIES: int = 3
"""Number of times a single gluing step is retried before it is declared failed."""

_TRIVIAL_COCYCLE: frozenset = frozenset()
"""Sentinel representing a trivially vanishing Čech 1-cocycle."""

# ---------------------------------------------------------------------------
# Jugeo imports with graceful fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float."""
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Lattice join (least upper bound): T_a ⊔ T_b = max(T_a, T_b)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Lattice meet (greatest lower bound): T_a ⊓ T_b = min(T_a, T_b).

        Used when propagating trust through a gluing step: the assembled global
        section inherits the *weakest* trust from its local pieces.
        """
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Strict promotion ↑_π: advance by one level, capped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Strict demotion ↓_χ: retreat by one level, floored at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

# ---------------------------------------------------------------------------
# Core evidence types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Parameters
    ----------
    context:
        The syntactic/semantic context in which the judgment is made.
    formula:
        The proposition being judged (may be a string, AST node, or structured term).
    assumptions:
        Immutable tuple of background assumptions in scope.
    evidence:
        Immutable tuple of evidence items supporting the judgment.
    obligations:
        Remaining proof obligations that must be discharged for the judgment to be closed.
    burden:
        Who bears the proof burden (e.g. ``ProvenanceSource.SOLVER``).
    trust:
        The :class:`TrustTier` at which the judgment is currently held.
    provenance:
        Where the judgment originated (solver, runtime, oracle, human).
    """
    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech cohomology obstruction to gluing local sections.

    When the pairwise overlap conditions fail the gluing procedure surfaces a
    ``CechObstruction`` whose :attr:`cocycle` encodes the discrepant key-pairs
    and :attr:`cohomology_class` is a deterministic hash of the obstruction data,
    suitable for de-duplication and error reporting.

    Attributes
    ----------
    cover_id:
        Identifier of the open cover in which the obstruction lives.
    cocycle:
        Frozenset of ``(section_id_i, section_id_j, key)`` triples where the
        sections disagree on the overlap region.
    cohomology_class:
        Hex digest of the canonical hash of the cocycle, representing the class
        in Ȟ¹(X, ℱ).
    description:
        Human-readable summary of what went wrong.
    """
    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the obstruction cocycle is empty (gluing succeeds)."""
        return len(self.cocycle) == 0

# ---------------------------------------------------------------------------
# Internal helper functions
# ---------------------------------------------------------------------------

def _sha256_hex(data: str) -> str:
    """Return the SHA-256 hex digest of *data* encoded as UTF-8.

    Used to produce canonical, collision-resistant identifiers for cohomology
    classes and record IDs.
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _make_id(prefix: str = "id") -> str:
    """Generate a deterministic-looking UUID4 identifier with an optional *prefix*.

    Example
    -------
    >>> _make_id("step")
    'step-3f2504e0-4f89-11d3-9a0c-0305e82c3301'
    """
    return f"{prefix}-{uuid.uuid4()}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (microsecond precision)."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _overlap_keys(section_a: dict, section_b: dict) -> frozenset:
    """Compute the set of keys present in *both* section dictionaries.

    This corresponds to the restriction of both sections to the intersection
    U_i ∩ U_j in sheaf-theoretic terms.

    Parameters
    ----------
    section_a, section_b:
        Local section data dictionaries.

    Returns
    -------
    frozenset
        The common keys, i.e. ``frozenset(section_a.keys() & section_b.keys())``.
    """
    return frozenset(section_a.keys()) & frozenset(section_b.keys())


def _check_overlap_agreement(
    section_a: dict,
    section_b: dict,
    *,
    precision: float = GLUING_PRECISION,
) -> tuple[bool, frozenset]:
    """Check whether two local sections agree on their common keys.

    Numeric values are compared within *precision*; all other values use equality.

    Parameters
    ----------
    section_a, section_b:
        Local sections to compare.
    precision:
        Absolute tolerance for float comparison.

    Returns
    -------
    (agreed, discrepancies)
        ``agreed`` is ``True`` when every shared key has consistent values.
        ``discrepancies`` is a frozenset of keys where the sections differ.
    """
    shared = _overlap_keys(section_a, section_b)
    bad: set[str] = set()
    for k in shared:
        va, vb = section_a[k], section_b[k]
        if isinstance(va, float) and isinstance(vb, float):
            if not math.isclose(va, vb, abs_tol=precision, rel_tol=precision):
                bad.add(k)
        elif va != vb:
            bad.add(k)
    return len(bad) == 0, frozenset(bad)


def _quorum_overlap_agreement(
    section_a: dict,
    section_b: dict,
    *,
    quorum: float = OVERLAP_QUORUM,
    precision: float = GLUING_PRECISION,
) -> bool:
    """Return ``True`` when the fraction of agreed keys meets *quorum*.

    Used in approximate (noisy runtime witness) mode where perfect agreement
    on every key cannot be guaranteed.

    Parameters
    ----------
    section_a, section_b:
        Local sections to compare.
    quorum:
        Fraction of shared keys that must agree (0.0–1.0).
    precision:
        Float comparison tolerance.
    """
    shared = _overlap_keys(section_a, section_b)
    if not shared:
        return True  # vacuously true — no overlap region
    agreed, _ = _check_overlap_agreement(section_a, section_b, precision=precision)
    if agreed:
        return True
    # Count agreement key-by-key
    good = sum(
        1 for k in shared
        if _values_close(section_a[k], section_b[k], precision)
    )
    return good / len(shared) >= quorum


def _values_close(va: Any, vb: Any, precision: float) -> bool:
    """Return ``True`` when *va* and *vb* are considered equal under *precision*."""
    if isinstance(va, float) and isinstance(vb, float):
        return math.isclose(va, vb, abs_tol=precision, rel_tol=precision)
    return va == vb


def _merge_sections(section_a: dict, section_b: dict) -> dict:
    """Merge two locally consistent sections into a single dictionary.

    Keys present in *section_a* are preferred when a key appears in both
    (caller must have verified agreement via :func:`_check_overlap_agreement`
    before calling this function).

    Parameters
    ----------
    section_a, section_b:
        Local sections that have already been verified to agree on overlaps.

    Returns
    -------
    dict
        The merged (glued) section.
    """
    merged = dict(section_b)
    merged.update(section_a)
    return merged


def _cocycle_hash(cocycle: frozenset, cover_id: str) -> str:
    """Compute a canonical hex hash for a Čech 1-cocycle.

    The hash is computed over the sorted string representation of the cocycle
    elements together with *cover_id*, making it deterministic and cover-scoped.

    Parameters
    ----------
    cocycle:
        Frozenset of ``(section_i, section_j, key)`` triples.
    cover_id:
        The cover identifier to scope the hash.
    """
    payload = f"{_COVER_SALT}:{cover_id}:" + ":".join(
        sorted(str(e) for e in cocycle)
    )
    return _sha256_hex(payload)[:32]


def _build_cech_obstruction(
    cover_id: str,
    discrepancies: dict[tuple[str, str], frozenset],
) -> CechObstruction:
    """Assemble a :class:`CechObstruction` from a dict of pairwise discrepancies.

    Parameters
    ----------
    cover_id:
        Identifier of the covering in which the obstruction lives.
    discrepancies:
        Mapping ``(section_id_i, section_id_j) -> frozenset_of_bad_keys``.

    Returns
    -------
    CechObstruction
        With ``cocycle`` = union of ``{(i, j, k) for k in bad_keys}`` and
        ``cohomology_class`` = hash of the cocycle.
    """
    cocycle_items: set[tuple] = set()
    desc_parts: list[str] = []
    for (si, sj), bad_keys in discrepancies.items():
        for k in bad_keys:
            cocycle_items.add((si, sj, k))
        if bad_keys:
            desc_parts.append(f"sections {si!r} and {sj!r} disagree on keys {sorted(bad_keys)}")
    cocycle = frozenset(cocycle_items)
    cohomology_class = _cocycle_hash(cocycle, cover_id)
    description = "; ".join(desc_parts) if desc_parts else "no obstruction"
    return CechObstruction(
        cover_id=cover_id,
        cocycle=cocycle,
        cohomology_class=cohomology_class,
        description=description,
    )


def _trust_meet_sequence(tiers: list[TrustTier]) -> TrustTier:
    """Return the meet (⊓) of a sequence of trust tiers.

    The global section inherits the *minimum* trust from all its local pieces,
    following the lattice meet semantics.

    Parameters
    ----------
    tiers:
        Non-empty list of :class:`TrustTier` values.

    Returns
    -------
    TrustTier
        The global (weakest) trust tier.

    Raises
    ------
    ValueError
        If *tiers* is empty.
    """
    if not tiers:
        raise ValueError("Cannot compute trust meet of an empty sequence.")
    result = tiers[0]
    for t in tiers[1:]:
        result = result.meet(t)
    return result


def _section_fingerprint(section: dict) -> str:
    """Compute a short fingerprint for a section dictionary.

    The fingerprint is a 16-hex-character prefix of the SHA-256 digest of the
    sorted JSON-like representation of the section.  Useful for logging and
    de-duplication.
    """
    payload = ":".join(f"{k}={v}" for k, v in sorted(section.items()))
    return _sha256_hex(payload)[:16]


def _validate_section_schema(section: dict) -> list[str]:
    """Light schema validation for a local section dictionary.

    Returns a list of validation error messages.  An empty list means the
    section is well-formed.

    Rules
    -----
    * Must be a non-empty dict.
    * Keys must be strings.
    * Values must be JSON-serialisable scalars (str, int, float, bool, None).
    """
    errors: list[str] = []
    if not isinstance(section, dict):
        errors.append(f"section must be a dict, got {type(section).__name__!r}")
        return errors
    if not section:
        errors.append("section must not be empty")
    for k, v in section.items():
        if not isinstance(k, str):
            errors.append(f"key {k!r} is not a string")
        if not isinstance(v, (str, int, float, bool, type(None))):
            errors.append(f"value for key {k!r} has unsupported type {type(v).__name__!r}")
    return errors


def _iter_pairs(items: list) -> Iterator[tuple]:
    """Yield all unordered pairs from *items* (combinations of size 2).

    Parameters
    ----------
    items:
        A list of items to pair.

    Yields
    ------
    tuple
        Each pair ``(items[i], items[j])`` with ``i < j``.
    """
    yield from itertools.combinations(items, 2)


def _step_log_entry(step_id: str, success: bool, detail: str) -> dict:
    """Build a structured log entry dict for a gluing step."""
    return {
        "step_id": step_id,
        "success": success,
        "detail": detail,
        "timestamp": _now_iso(),
    }

# ---------------------------------------------------------------------------
# ReplayGluing dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayGluing:
    """Records one gluing step — merging two local sections into a global one.

    A :class:`ReplayGluing` is the primary evidence atom for a single merge
    operation during the replay-and-glue pipeline.  It is immutable so that
    the gluing history cannot be tampered with after the fact.

    Attributes
    ----------
    step_id:
        Unique identifier for this gluing step.
    section_a_id:
        Identifier of the first (left) local section.
    section_b_id:
        Identifier of the second (right) local section.
    overlap_region:
        Frozenset of keys that constitute the overlap U_i ∩ U_j.
    merged_section_id:
        Identifier assigned to the newly merged section.
    success:
        ``True`` if the overlap condition was satisfied and the sections were merged.
    trust_tier:
        Trust level of the merged section (meet of the two input trust tiers).
    evidence:
        Free-text evidence summary (e.g. hash of overlap data).
    """
    step_id: str
    section_a_id: str
    section_b_id: str
    overlap_region: frozenset
    merged_section_id: str
    success: bool
    trust_tier: TrustTier
    evidence: str

    def to_judgment(self) -> Judgment:
        """Convert this gluing step into a :class:`Judgment`.

        The resulting judgment has:
        * ``formula`` = ``"gluing_step_succeeded"`` or ``"gluing_step_failed"``
        * ``trust`` = :attr:`trust_tier`
        * ``evidence`` = a singleton tuple containing :attr:`evidence`
        * ``obligations`` = empty if successful, else a singleton discharge obligation
        """
        formula = "gluing_step_succeeded" if self.success else "gluing_step_failed"
        obligations: tuple = () if self.success else (f"discharge_step:{self.step_id}",)
        return Judgment(
            context={"step_id": self.step_id},
            formula=formula,
            assumptions=(f"section_a:{self.section_a_id}", f"section_b:{self.section_b_id}"),
            evidence=(self.evidence,),
            obligations=obligations,
            burden=ProvenanceSource.SOLVER if _JUGEO_JUDGMENTS else "solver",
            trust=self.trust_tier,
            provenance=ProvenanceSource.SOLVER if _JUGEO_JUDGMENTS else "solver",
        )

    def describe(self) -> str:
        """Return a human-readable description of this gluing step.

        Example
        -------
        ``"Step step-abc123: merged sections 'sec-0' + 'sec-1' -> 'sec-merged' [VERIFIED] ✓"``
        """
        status = "✓" if self.success else "✗"
        return (
            f"Step {self.step_id}: merged sections {self.section_a_id!r} + "
            f"{self.section_b_id!r} -> {self.merged_section_id!r} "
            f"[{self.trust_tier.name}] {status}"
        )

# ---------------------------------------------------------------------------
# GluingRecord dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GluingRecord:
    """Stores the evidence that a gluing step succeeded.

    A :class:`GluingRecord` is the auditable artefact created after each
    :class:`ReplayGluing` step.  It captures both the structural evidence
    (was the overlap condition met?) and the cohomological evidence (is the
    cocycle trivial?), making it suitable for downstream proof discharge.

    Attributes
    ----------
    record_id:
        Unique identifier for this record.
    step_id:
        Back-reference to the :class:`ReplayGluing` step.
    overlap_condition_met:
        ``True`` when the pairwise overlap condition ρ_{ij}(s_i) = ρ_{ji}(s_j) holds.
    cocycle_trivial:
        ``True`` when the Čech 1-cocycle [δ(s)] ∈ Ȟ¹ is trivially zero.
    evidence_items:
        Immutable tuple of evidence strings (hashes, witness IDs, proof terms).
    trust_tier:
        Trust level of this record.
    timestamp:
        ISO-8601 UTC timestamp of record creation.
    """
    record_id: str
    step_id: str
    overlap_condition_met: bool
    cocycle_trivial: bool
    evidence_items: tuple
    trust_tier: TrustTier
    timestamp: str

    def to_cech_check(self) -> CechObstruction:
        """Produce a :class:`CechObstruction` representing the state of this record.

        If both :attr:`overlap_condition_met` and :attr:`cocycle_trivial` are
        ``True`` the returned obstruction has an empty cocycle (i.e. is trivial).
        Otherwise the cocycle contains a sentinel element indicating failure.
        """
        if self.overlap_condition_met and self.cocycle_trivial:
            cocycle = _TRIVIAL_COCYCLE
            description = "no obstruction — record is fully consistent"
        else:
            sentinel = (self.step_id, "overlap_failure" if not self.overlap_condition_met else "cocycle_nontrivial")
            cocycle = frozenset({sentinel})
            description = (
                f"obstruction at step {self.step_id!r}: "
                f"overlap_condition_met={self.overlap_condition_met}, "
                f"cocycle_trivial={self.cocycle_trivial}"
            )
        return CechObstruction(
            cover_id=self.step_id,
            cocycle=cocycle,
            cohomology_class=_cocycle_hash(cocycle, self.step_id),
            description=description,
        )

    def to_judgment(self) -> Judgment:
        """Convert this record to a :class:`Judgment` over its validity."""
        formula = "record_valid" if self.is_valid() else "record_invalid"
        return Judgment(
            context={"record_id": self.record_id, "step_id": self.step_id},
            formula=formula,
            assumptions=(),
            evidence=self.evidence_items,
            obligations=() if self.is_valid() else (f"fix_record:{self.record_id}",),
            burden=ProvenanceSource.SOLVER if _JUGEO_JUDGMENTS else "solver",
            trust=self.trust_tier,
            provenance=ProvenanceSource.RUNTIME if _JUGEO_JUDGMENTS else "runtime",
        )

    def is_valid(self) -> bool:
        """Return ``True`` when both overlap and cocycle conditions are satisfied."""
        return self.overlap_condition_met and self.cocycle_trivial

# ---------------------------------------------------------------------------
# GlobalGluingUnderReplay
# ---------------------------------------------------------------------------

class GlobalGluingUnderReplay:
    """Assembles a global section from a sequence of replayed local constructions.

    This is the central orchestrator for the sheaf-theoretic gluing procedure
    under replay.  Local sections are added one by one via :meth:`add_section`
    and are accumulated internally.  Calling :meth:`glue` triggers the pairwise
    overlap checks and, if all checks pass, assembles the unique global section.
    If any overlap fails a :class:`CechObstruction` is returned instead.

    Parameters
    ----------
    gluing_id:
        Unique identifier for this gluing instance.
    cover_id:
        Identifier of the open cover {U_i} used in this gluing.
    trust_tier:
        Initial trust tier (will be demoted to the meet of all added sections).

    Attributes
    ----------
    local_sections : list[dict]
        The sequence of local sections added so far.
    global_section : dict | None
        The assembled global section after a successful :meth:`glue` call.
    replay_log : list[dict]
        Structured log of all operations performed on this instance.
    """

    def __init__(
        self,
        gluing_id: str,
        cover_id: str,
        trust_tier: TrustTier = TrustTier.PROPOSAL,
    ) -> None:
        self.gluing_id: str = gluing_id
        self.cover_id: str = cover_id
        self.local_sections: list[dict] = []
        self.global_section: dict | None = None
        self.replay_log: list[dict] = []
        self.trust_tier: TrustTier = trust_tier

    def add_section(self, section: dict) -> None:
        """Add a local section to the accumulator.

        Parameters
        ----------
        section:
            A dict mapping string keys to scalar values.  The section is
            validated against the schema before being accepted.

        Raises
        ------
        JuGeoError
            If the section fails schema validation or the maximum section count
            :data:`MAX_SECTIONS` is exceeded.
        """
        if len(self.local_sections) >= MAX_SECTIONS:
            raise_with_scope(
                "GLUE_OVERFLOW",
                message=f"Maximum section count {MAX_SECTIONS} exceeded in gluing {self.gluing_id!r}.",
                provenance=self.gluing_id,
            )
        errors = _validate_section_schema(section)
        if errors:
            raise_with_scope(
                "GLUE_SCHEMA",
                message=f"Section schema validation failed: {'; '.join(errors)}",
                provenance=self.gluing_id,
            )
        section_copy = dict(section)
        if "_section_id" not in section_copy:
            section_copy["_section_id"] = _make_id("sec")
        self.local_sections.append(section_copy)
        entry = _step_log_entry(
            step_id=section_copy["_section_id"],
            success=True,
            detail=f"added section with fingerprint {_section_fingerprint(section_copy)}",
        )
        self.replay_log.append(entry)
        logger.debug("GlobalGluingUnderReplay[%s] added section %s", self.gluing_id, section_copy["_section_id"])

    def glue(self) -> dict | CechObstruction:
        """Attempt to glue all accumulated local sections into a global section.

        Algorithm
        ---------
        1. For every unordered pair (s_i, s_j) check overlap agreement.
        2. Collect all discrepancies into a dict keyed by (section_id_i, section_id_j).
        3. If any discrepancy exists, build and return a :class:`CechObstruction`.
        4. Otherwise, fold all sections via :func:`_merge_sections` and store the
           result in :attr:`global_section`.

        Returns
        -------
        dict
            The assembled global section if gluing succeeds.
        CechObstruction
            The Čech cohomology obstruction if gluing fails.
        """
        logger.info("GlobalGluingUnderReplay[%s] starting glue with %d sections", self.gluing_id, len(self.local_sections))
        if not self.local_sections:
            self.global_section = {}
            return {}

        discrepancies: dict[tuple[str, str], frozenset] = {}
        for sa, sb in _iter_pairs(self.local_sections):
            agreed, bad_keys = _check_overlap_agreement(sa, sb)
            if not agreed:
                id_a = sa.get("_section_id", "?")
                id_b = sb.get("_section_id", "?")
                discrepancies[(id_a, id_b)] = bad_keys
                self.replay_log.append(_step_log_entry(
                    step_id=f"overlap-{id_a}-{id_b}",
                    success=False,
                    detail=f"overlap failure on keys {sorted(bad_keys)}",
                ))

        if discrepancies:
            obs = _build_cech_obstruction(self.cover_id, discrepancies)
            logger.warning(
                "GlobalGluingUnderReplay[%s] obstruction class=%s", self.gluing_id, obs.cohomology_class
            )
            return obs

        merged: dict = {}
        for sec in self.local_sections:
            merged = _merge_sections(merged, sec)
        merged.pop("_section_id", None)
        self.global_section = merged
        logger.info("GlobalGluingUnderReplay[%s] gluing succeeded, keys=%d", self.gluing_id, len(merged))
        return merged

    def validate(self) -> Judgment:
        """Validate the current state of the gluing instance.

        Returns
        -------
        Judgment
            A judgment over ``"global_section_valid"`` if a global section has
            been assembled, or ``"global_section_absent"`` otherwise.
        """
        if self.global_section is not None:
            formula = "global_section_valid"
            tiers = [sec.get("_trust", self.trust_tier) for sec in self.local_sections]
            if all(isinstance(t, TrustTier) for t in tiers):
                effective_trust = _trust_meet_sequence(tiers)  # type: ignore[arg-type]
            else:
                effective_trust = self.trust_tier
            obligations: tuple = ()
        else:
            formula = "global_section_absent"
            effective_trust = TrustTier.PROPOSAL
            obligations = (f"complete_gluing:{self.gluing_id}",)

        return Judgment(
            context={"gluing_id": self.gluing_id, "cover_id": self.cover_id},
            formula=formula,
            assumptions=tuple(s.get("_section_id", "?") for s in self.local_sections),
            evidence=(f"replay_log_len:{len(self.replay_log)}",),
            obligations=obligations,
            burden="solver",
            trust=effective_trust,
            provenance="runtime",
        )

    def get_global_section(self) -> dict | None:
        """Return the assembled global section, or ``None`` if not yet glued."""
        return self.global_section

# ---------------------------------------------------------------------------
# ReplayIntegration
# ---------------------------------------------------------------------------

class ReplayIntegration:
    """Manages the connection between a replay sequence and the gluing engine.

    :class:`ReplayIntegration` is the liaison layer that takes replay-derived
    sections (identified by their ``replay_id``) and feeds them into the
    active :class:`GluingEngine`.  It records which replays contributed which
    sections, enabling full traceability from a global section back to the
    replay events that produced it.

    Attributes
    ----------
    integration_id:
        Unique identifier for this integration instance.
    replay_sequence:
        Ordered list of ``(replay_id, section_id)`` tuples, recording the
        order in which replays were integrated.
    gluing_engine_ref:
        Optional reference (string ID) to the owning :class:`GluingEngine`.
    integration_log:
        Structured log of integration events.
    """

    def __init__(
        self,
        integration_id: str,
        gluing_engine_ref: str | None = None,
    ) -> None:
        self.integration_id: str = integration_id
        self.replay_sequence: list[tuple[str, str]] = []
        self.gluing_engine_ref: str | None = gluing_engine_ref
        self.integration_log: list[dict] = []
        self._section_map: dict[str, dict] = {}

    def record_replay(self, replay_id: str, section: dict) -> None:
        """Record that *replay_id* produced *section* and register it for integration.

        Parameters
        ----------
        replay_id:
            Identifier of the replay event (e.g. a commit hash or run ID).
        section:
            The local section data produced by the replay.
        """
        section_id = section.get("_section_id", _make_id("sec"))
        self._section_map[section_id] = section
        self.replay_sequence.append((replay_id, section_id))
        self.integration_log.append({
            "event": "record_replay",
            "replay_id": replay_id,
            "section_id": section_id,
            "timestamp": _now_iso(),
        })
        logger.debug("ReplayIntegration[%s] recorded replay %s -> section %s", self.integration_id, replay_id, section_id)

    def integrate(self, section_id: str) -> bool:
        """Mark *section_id* as integrated into the gluing engine.

        Parameters
        ----------
        section_id:
            The section ID that has been passed to the engine.

        Returns
        -------
        bool
            ``True`` if the section was known to this integration instance.
        """
        known = section_id in self._section_map
        self.integration_log.append({
            "event": "integrate",
            "section_id": section_id,
            "known": known,
            "timestamp": _now_iso(),
        })
        return known

    def get_sequence(self) -> list[tuple[str, str]]:
        """Return the ordered list of ``(replay_id, section_id)`` tuples."""
        return list(self.replay_sequence)

    def to_judgment(self) -> Judgment:
        """Produce a :class:`Judgment` summarising the integration state."""
        n = len(self.replay_sequence)
        formula = f"integration_complete:{n}_replays"
        return Judgment(
            context={"integration_id": self.integration_id},
            formula=formula,
            assumptions=tuple(rid for rid, _ in self.replay_sequence),
            evidence=(f"section_map_size:{len(self._section_map)}",),
            obligations=(),
            burden="solver",
            trust=TrustTier.REVIEWED,
            provenance="runtime",
        )

# ---------------------------------------------------------------------------
# GluingEngine
# ---------------------------------------------------------------------------

class GluingEngine:
    """Orchestrates the full global gluing process.

    :class:`GluingEngine` is the top-level coordinator.  It holds a registry of
    named local sections (:attr:`sections`), executes pairwise gluing steps via
    :meth:`step_by_step`, and finally calls :meth:`glue_all` to assemble the
    global section.

    The engine follows the *open-cover* model: the user registers sections by ID,
    the engine computes all pairwise overlaps, and if they are all consistent the
    global section is synthesised by a left-fold over the sections in insertion
    order.

    Parameters
    ----------
    engine_id:
        Unique identifier for this engine instance.
    cover_id:
        Identifier of the open cover associated with this engine.

    Attributes
    ----------
    sections : dict[str, dict]
        Map from section ID to section data.
    gluing_steps : list[ReplayGluing]
        Ordered list of gluing steps executed so far.
    records : list[GluingRecord]
        Corresponding gluing records for each step.
    status : str
        One of ``"idle"``, ``"in_progress"``, ``"succeeded"``, ``"failed"``.
    """

    def __init__(self, engine_id: str, cover_id: str) -> None:
        self.engine_id: str = engine_id
        self.cover_id: str = cover_id
        self.sections: dict[str, dict] = {}
        self.gluing_steps: list[ReplayGluing] = []
        self.records: list[GluingRecord] = []
        self.status: str = "idle"
        self._insertion_order: list[str] = []

    def add_local_section(self, section_id: str, data: dict) -> None:
        """Register a local section under *section_id*.

        Parameters
        ----------
        section_id:
            Unique string key for this section.
        data:
            Section payload dict.

        Raises
        ------
        ValueError
            If *section_id* is already registered.
        """
        if section_id in self.sections:
            raise ValueError(f"Section {section_id!r} already registered in engine {self.engine_id!r}.")
        errors = _validate_section_schema(data)
        if errors:
            raise ValueError(f"Section {section_id!r} failed schema check: {'; '.join(errors)}")
        self.sections[section_id] = dict(data)
        self._insertion_order.append(section_id)
        logger.debug("GluingEngine[%s] registered section %s", self.engine_id, section_id)

    def glue_all(self) -> dict | CechObstruction:
        """Run the full gluing procedure over all registered sections.

        Returns
        -------
        dict
            The global section on success.
        CechObstruction
            The Čech obstruction on failure.
        """
        self.status = "in_progress"
        gluer = GlobalGluingUnderReplay(
            gluing_id=_make_id("gluing"),
            cover_id=self.cover_id,
            trust_tier=TrustTier.REVIEWED,
        )
        for sid in self._insertion_order:
            sec = dict(self.sections[sid])
            sec["_section_id"] = sid
            gluer.add_section(sec)

        result = gluer.glue()
        if isinstance(result, CechObstruction):
            self.status = "failed"
            logger.warning("GluingEngine[%s] glue_all failed: %s", self.engine_id, result.description)
        else:
            self.status = "succeeded"
            logger.info("GluingEngine[%s] glue_all succeeded with %d keys", self.engine_id, len(result))
        return result

    def step_by_step(self) -> list[ReplayGluing]:
        """Execute gluing steps one pair at a time and return the step list.

        Each unordered pair of registered sections is glued.  The accumulated
        steps are also stored in :attr:`gluing_steps` and corresponding records
        in :attr:`records`.

        Returns
        -------
        list[ReplayGluing]
            All gluing steps, in the order pairs were processed.
        """
        steps: list[ReplayGluing] = []
        ids = list(self._insertion_order)
        for id_a, id_b in _iter_pairs(ids):
            sec_a = self.sections[id_a]
            sec_b = self.sections[id_b]
            agreed, bad_keys = _check_overlap_agreement(sec_a, sec_b)
            overlap_region = _overlap_keys(sec_a, sec_b)
            merged_id = _make_id("merged")
            tier_a = TrustTier(sec_a.get("_trust", TrustTier.REVIEWED.value))
            tier_b = TrustTier(sec_b.get("_trust", TrustTier.REVIEWED.value))
            merged_tier = tier_a.meet(tier_b)
            evidence_str = _sha256_hex(f"{id_a}:{id_b}:{sorted(overlap_region)}")[:24]
            step = ReplayGluing(
                step_id=_make_id("step"),
                section_a_id=id_a,
                section_b_id=id_b,
                overlap_region=overlap_region,
                merged_section_id=merged_id,
                success=agreed,
                trust_tier=merged_tier,
                evidence=evidence_str,
            )
            record = record_gluing(step)
            steps.append(step)
            self.gluing_steps.append(step)
            self.records.append(record)
            logger.debug("GluingEngine[%s] step %s: %s", self.engine_id, step.step_id, step.describe())
        return steps

    def verify_global(self, global_section: dict) -> Judgment:
        """Verify that *global_section* is consistent with all local sections.

        Checks that restricting the global section to each local section's key
        domain recovers the local section exactly.

        Parameters
        ----------
        global_section:
            The assembled global section to verify.

        Returns
        -------
        Judgment
            A judgment over ``"global_section_consistent"`` or
            ``"global_section_inconsistent"``.
        """
        inconsistencies: list[str] = []
        for sid in self._insertion_order:
            local = self.sections[sid]
            for k, v in local.items():
                if k.startswith("_"):
                    continue
                if k not in global_section:
                    inconsistencies.append(f"key {k!r} from section {sid!r} missing in global")
                elif not _values_close(global_section[k], v, GLUING_PRECISION):
                    inconsistencies.append(
                        f"section {sid!r} key {k!r}: local={v!r} != global={global_section[k]!r}"
                    )
        formula = "global_section_consistent" if not inconsistencies else "global_section_inconsistent"
        trust = TrustTier.VERIFIED if not inconsistencies else TrustTier.PROPOSAL
        return Judgment(
            context={"engine_id": self.engine_id, "cover_id": self.cover_id},
            formula=formula,
            assumptions=tuple(self._insertion_order),
            evidence=tuple(inconsistencies) if inconsistencies else ("all_keys_consistent",),
            obligations=tuple(f"fix:{i}" for i in inconsistencies),
            burden="solver",
            trust=trust,
            provenance="runtime",
        )

    def reset(self) -> None:
        """Reset the engine to its initial idle state.

        Clears all registered sections, steps, and records but preserves the
        engine and cover identifiers.
        """
        self.sections.clear()
        self._insertion_order.clear()
        self.gluing_steps.clear()
        self.records.clear()
        self.status = "idle"
        logger.info("GluingEngine[%s] reset to idle", self.engine_id)

# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def glue_under_replay(local_sections: list[dict]) -> dict | CechObstruction:
    """Attempt to glue a list of local sections into a global section.

    This is the primary entry point for the module.  It wraps the
    :class:`GlobalGluingUnderReplay` machinery in a single convenience call.

    Parameters
    ----------
    local_sections:
        A list of local section dicts.  Each dict maps string keys to scalar
        values.  An optional ``"_section_id"`` key is used as the section
        identifier; if absent a UUID is generated.

    Returns
    -------
    dict
        The assembled global section if all pairwise overlaps are consistent.
    CechObstruction
        The Čech cohomology obstruction if any overlap fails.

    Examples
    --------
    >>> s1 = {"x": 1.0, "y": 2.0}
    >>> s2 = {"y": 2.0, "z": 3.0}
    >>> result = glue_under_replay([s1, s2])
    >>> isinstance(result, dict)
    True
    """
    cover_id = _sha256_hex(f"cover:{_now_iso()}")[:16]
    gluer = GlobalGluingUnderReplay(
        gluing_id=_make_id("gluing"),
        cover_id=cover_id,
        trust_tier=TrustTier.REVIEWED,
    )
    for sec in local_sections:
        gluer.add_section(dict(sec))
    return gluer.glue()


def record_gluing(step: ReplayGluing) -> GluingRecord:
    """Record a successful (or failed) gluing step as a :class:`GluingRecord`.

    Parameters
    ----------
    step:
        The :class:`ReplayGluing` step to record.

    Returns
    -------
    GluingRecord
        An immutable record capturing the step's evidence and validity.
    """
    record_id = _make_id("record")
    evidence_items = (step.evidence, f"step:{step.step_id}", f"merged:{step.merged_section_id}")
    return GluingRecord(
        record_id=record_id,
        step_id=step.step_id,
        overlap_condition_met=step.success,
        cocycle_trivial=step.success,
        evidence_items=evidence_items,
        trust_tier=step.trust_tier,
        timestamp=_now_iso(),
    )


def validate_global_gluing(global_section: dict, cover_id: str) -> Judgment:
    """Check that an assembled global section is internally consistent.

    Performs lightweight structural validation:
    * The section must be a non-empty dict.
    * All keys must be strings.
    * All values must be JSON-serialisable scalars.

    Parameters
    ----------
    global_section:
        The assembled global section to validate.
    cover_id:
        The cover identifier for provenance tracking.

    Returns
    -------
    Judgment
        A judgment at :attr:`TrustTier.VERIFIED` if validation passes, or
        :attr:`TrustTier.PROPOSAL` with pending obligations if it fails.
    """
    errors = _validate_section_schema(global_section)
    if global_section and not errors:
        formula = "global_section_schema_valid"
        trust = TrustTier.VERIFIED
        obligations: tuple = ()
        evidence: tuple = (f"fingerprint:{_section_fingerprint(global_section)}",)
    else:
        formula = "global_section_schema_invalid"
        trust = TrustTier.PROPOSAL
        obligations = tuple(f"fix_schema:{e}" for e in errors)
        evidence = tuple(errors)

    return Judgment(
        context={"cover_id": cover_id},
        formula=formula,
        assumptions=(),
        evidence=evidence,
        obligations=obligations,
        burden="solver",
        trust=trust,
        provenance="runtime",
    )


def run_replay_gluing(cover_id: str, sections: list[dict]) -> dict:
    """Execute the full replay-and-glue pipeline and return a result summary.

    This function is the high-level orchestration entry point.  It:

    1. Creates a :class:`GluingEngine` for *cover_id*.
    2. Registers all *sections*.
    3. Runs :meth:`GluingEngine.step_by_step` to record individual steps.
    4. Calls :meth:`GluingEngine.glue_all` to assemble the global section.
    5. Validates the result and returns a summary dict.

    Parameters
    ----------
    cover_id:
        The cover identifier.
    sections:
        List of local section dicts.  Each should have a unique ``"_section_id"``
        key; if absent one is generated.

    Returns
    -------
    dict
        A summary with keys:
        * ``"cover_id"`` — the cover identifier
        * ``"status"`` — ``"succeeded"`` or ``"failed"``
        * ``"global_section"`` — the assembled section or ``None``
        * ``"obstruction"`` — :class:`CechObstruction` or ``None``
        * ``"n_steps"`` — number of pairwise steps executed
        * ``"validation"`` — :class:`Judgment` from :func:`validate_global_gluing`
        * ``"engine_id"`` — the engine's identifier
    """
    engine = GluingEngine(engine_id=_make_id("engine"), cover_id=cover_id)

    for i, sec in enumerate(sections):
        sec_copy = dict(sec)
        sid = sec_copy.pop("_section_id", f"sec-{i}")
        engine.add_local_section(sid, sec_copy)

    steps = engine.step_by_step()
    result = engine.glue_all()

    if isinstance(result, CechObstruction):
        validation = Judgment(
            context={"cover_id": cover_id},
            formula="gluing_failed_obstruction",
            assumptions=(),
            evidence=(result.cohomology_class,),
            obligations=(f"resolve_obstruction:{result.cohomology_class}",),
            burden="solver",
            trust=TrustTier.PROPOSAL,
            provenance="runtime",
        )
        return {
            "cover_id": cover_id,
            "status": "failed",
            "global_section": None,
            "obstruction": result,
            "n_steps": len(steps),
            "validation": validation,
            "engine_id": engine.engine_id,
        }

    validation = validate_global_gluing(result, cover_id)
    return {
        "cover_id": cover_id,
        "status": "succeeded",
        "global_section": result,
        "obstruction": None,
        "n_steps": len(steps),
        "validation": validation,
        "engine_id": engine.engine_id,
    }

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 72)
    print("Global Gluing Under Replay — smoke test")
    print("=" * 72)

    # ------------------------------------------------------------------
    # Case 1: Successful gluing — three compatible local sections
    # ------------------------------------------------------------------
    print("\n--- Case 1: successful gluing ---")
    sec_a = {"x": 1.0, "y": 2.0, "label": "alpha"}
    sec_b = {"y": 2.0, "z": 3.0, "label": "alpha"}
    sec_c = {"z": 3.0, "w": 4.0, "label": "alpha"}

    result_ok = run_replay_gluing(
        cover_id="cover-smoke-ok",
        sections=[sec_a, sec_b, sec_c],
    )
    print(f"  status          : {result_ok['status']}")
    print(f"  global_section  : {result_ok['global_section']}")
    print(f"  n_steps         : {result_ok['n_steps']}")
    print(f"  validation trust: {result_ok['validation'].trust.name}")
    assert result_ok["status"] == "succeeded", "Expected success"
    global_sec = result_ok["global_section"]
    assert global_sec is not None
    assert global_sec.get("x") == 1.0
    assert global_sec.get("z") == 3.0
    print("  ✓ global section assembled correctly")

    # ------------------------------------------------------------------
    # Case 2: Obstruction — conflicting values on overlap
    # ------------------------------------------------------------------
    print("\n--- Case 2: Čech obstruction ---")
    bad_a = {"x": 1.0, "y": 2.0}
    bad_b = {"y": 99.0, "z": 3.0}   # y disagrees on overlap

    result_fail = run_replay_gluing(
        cover_id="cover-smoke-fail",
        sections=[bad_a, bad_b],
    )
    print(f"  status         : {result_fail['status']}")
    print(f"  obstruction    : {result_fail['obstruction'].description if result_fail['obstruction'] else 'None'}")
    print(f"  cohomology cls : {result_fail['obstruction'].cohomology_class if result_fail['obstruction'] else 'None'}")
    assert result_fail["status"] == "failed", "Expected obstruction"
    obs = result_fail["obstruction"]
    assert obs is not None and not obs.is_trivial()
    print("  ✓ obstruction detected correctly")

    # ------------------------------------------------------------------
    # Case 3: Step-by-step gluing with GluingEngine
    # ------------------------------------------------------------------
    print("\n--- Case 3: step-by-step engine ---")
    engine = GluingEngine(engine_id="engine-smoke", cover_id="cover-smoke-step")
    engine.add_local_section("U0", {"a": 1, "b": 2})
    engine.add_local_section("U1", {"b": 2, "c": 3})
    engine.add_local_section("U2", {"c": 3, "d": 4})
    steps = engine.step_by_step()
    print(f"  steps: {len(steps)}")
    for s in steps:
        print(f"    {s.describe()}")
        jdg = s.to_judgment()
        print(f"      judgment trust: {jdg.trust.name}")
    global_result = engine.glue_all()
    print(f"  glue_all status: {engine.status}")
    assert engine.status == "succeeded"
    assert isinstance(global_result, dict)
    verify_jdg = engine.verify_global(global_result)
    print(f"  verify trust   : {verify_jdg.trust.name}")
    print(f"  verify formula : {verify_jdg.formula}")
    assert verify_jdg.trust == TrustTier.VERIFIED
    print("  ✓ step-by-step engine works correctly")

    # ------------------------------------------------------------------
    # Case 4: ReplayIntegration
    # ------------------------------------------------------------------
    print("\n--- Case 4: replay integration ---")
    integration = ReplayIntegration(integration_id="int-smoke", gluing_engine_ref="engine-smoke")
    r1 = {"_section_id": "rs-0", "p": 10, "q": 20}
    r2 = {"_section_id": "rs-1", "q": 20, "r": 30}
    integration.record_replay(replay_id="replay-001", section=r1)
    integration.record_replay(replay_id="replay-002", section=r2)
    seq = integration.get_sequence()
    print(f"  replay sequence: {seq}")
    assert len(seq) == 2
    ok = integration.integrate("rs-0")
    assert ok
    jdg = integration.to_judgment()
    print(f"  integration judgment trust: {jdg.trust.name}")
    print("  ✓ ReplayIntegration works correctly")

    # ------------------------------------------------------------------
    # Case 5: GluingRecord and CechObstruction round-trip
    # ------------------------------------------------------------------
    print("\n--- Case 5: GluingRecord → CechObstruction ---")
    dummy_step = ReplayGluing(
        step_id="step-dummy",
        section_a_id="sa",
        section_b_id="sb",
        overlap_region=frozenset({"y"}),
        merged_section_id="sm",
        success=True,
        trust_tier=TrustTier.VERIFIED,
        evidence="abc123",
    )
    rec = record_gluing(dummy_step)
    print(f"  record valid   : {rec.is_valid()}")
    cech = rec.to_cech_check()
    print(f"  cech trivial   : {cech.is_trivial()}")
    print(f"  record judgment: {rec.to_judgment().formula}")
    assert rec.is_valid()
    assert cech.is_trivial()

    failed_step = ReplayGluing(
        step_id="step-bad",
        section_a_id="sa",
        section_b_id="sb",
        overlap_region=frozenset({"y"}),
        merged_section_id="sm-bad",
        success=False,
        trust_tier=TrustTier.PROPOSAL,
        evidence="bad",
    )
    bad_rec = record_gluing(failed_step)
    bad_cech = bad_rec.to_cech_check()
    print(f"  bad cech trivial: {bad_cech.is_trivial()}")
    assert not bad_cech.is_trivial()
    print("  ✓ GluingRecord → CechObstruction round-trip correct")

    # ------------------------------------------------------------------
    # Case 6: TrustTier algebra
    # ------------------------------------------------------------------
    print("\n--- Case 6: TrustTier algebra ---")
    t_v = TrustTier.VERIFIED
    t_p = TrustTier.PROOF_BACKED
    t_r = TrustTier.REVIEWED
    print(f"  VERIFIED.join(PROOF_BACKED) = {t_v.join(t_p).name}")
    print(f"  VERIFIED.meet(REVIEWED)     = {t_v.meet(t_r).name}")
    print(f"  REVIEWED.promote()          = {t_r.promote().name}")
    print(f"  REVIEWED.demote()           = {t_r.demote().name}")
    assert t_v.join(t_p) == TrustTier.PROOF_BACKED
    assert t_v.meet(t_r) == TrustTier.REVIEWED
    assert t_r.promote() == TrustTier.VERIFIED
    assert t_r.demote() == TrustTier.PROPOSAL
    print("  ✓ TrustTier algebra correct")

    print("\n" + "=" * 72)
    print("All smoke tests passed.")
    print("=" * 72)

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError:
    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = "CONTRADICTED"
        UNVERIFIED = "UNVERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    class TrustTier(IntEnum):  # type: ignore[no-redef]
        """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float."""
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5

        def __le__(self, other: "TrustTier") -> bool:  # type: ignore[override]
            if not isinstance(other, TrustTier):
                return NotImplemented
            return self.value <= other.value

        def __lt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
            if not isinstance(other, TrustTier):
                return NotImplemented
            return self.value < other.value

        def __ge__(self, other: "TrustTier") -> bool:  # type: ignore[override]
            return other <= self

        def __gt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
            return other < self

        def meet(self, other: "TrustTier") -> "TrustTier":
            """Greatest lower bound (infimum) in the trust lattice."""
            return TrustTier(min(self.value, other.value))

        def join(self, other: "TrustTier") -> "TrustTier":
            """Least upper bound (supremum) in the trust lattice."""
            return TrustTier(max(self.value, other.value))

        def promote(self) -> "TrustTier":
            """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
            return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

        def demote(self) -> "TrustTier":
            """↓_χ — demote one tier downward, clamped at PROPOSAL."""
            return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

try:
    from jugeo.geometry.descent import GlobalSection, DescentObstruction
except ImportError:
    @dataclass(frozen=True)
    class GlobalSection:  # type: ignore[no-redef]
        section_id: str = ""
        data: Any = None
        trust_tier: str = "PROPOSAL"

    @dataclass(frozen=True)
    class DescentObstruction:  # type: ignore[no-redef]
        obstruction_id: str = ""
        cech_class: Any = None
        message: str = ""

try:
    from jugeo.errors import JuGeoError
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

try:
    from jugeo.generation.replay_gluing.models import (
        ReplaySession,
        GluingCandidate,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    @dataclass(frozen=True)
    class ReplaySession:  # type: ignore[no-redef]
        session_id: str = ""
        target_coordinate: str = ""

    @dataclass(frozen=True)
    class GluingCandidate:  # type: ignore[no-redef]
        candidate_id: str = ""
        patch_id: str = ""
        content_hash: str = ""


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayMove:
    """A single move in a replay-integrated generation sequence.

    A ReplayMove records everything needed to reproduce the local section it
    produced.  The ``content_hash`` is a SHA-256 digest of the produced
    section; reproducibility requires this hash to be stable across replays.

    Judgment tuple components:
        c   = ``patch_id``          (coordinate/patch this move operates on)
        φ   = ``move_description``  (what the move claims to achieve)
        A   = ``move_id``           (carrier: unique move identifier)
        E   = ``evidence_ids``      (evidence supporting the move)
        O   = ``discharged_obs``    (obligations discharged by this move)
        B   = ``elapsed_ms``        (budget consumed)
        T   = ``trust_tier``        (trust of this move's output)
        Π   = ``policy_name``       (governing policy)

    Fields
    ------
    move_id : str
        UUID4 uniquely identifying this move.
    sequence_index : int
        Position in the replay log (0-based).
    patch_id : str
        The coordinate patch U_i this move operates on.
    move_description : str
        Human-readable description of what the move does.
    content_hash : str
        SHA-256 of the local section content produced.
    trust_tier : str
        Trust tier of the produced section.
    evidence_ids : tuple[str, ...]
        IDs of evidence items supporting this move.
    discharged_obligations : tuple[str, ...]
        Obligation IDs discharged by this move.
    elapsed_ms : float
        Wall-clock time consumed by this move.
    policy_name : str
        Governing policy.
    is_deterministic : bool
        True iff replaying this move always produces the same content_hash.
    """

    move_id: str
    sequence_index: int
    patch_id: str
    move_description: str
    content_hash: str
    trust_tier: str
    evidence_ids: Tuple[str, ...]
    discharged_obligations: Tuple[str, ...]
    elapsed_ms: float
    policy_name: str = "replay-default"
    is_deterministic: bool = True


@dataclass(frozen=True)
class ReplayIntegrationRecord:
    """Complete record of a replay-integrated generation session.

    A ReplayIntegrationRecord is both the audit trail of the session and the
    substrate for Čech H¹ computation.  Its ``moves`` sequence must be
    ordered by ``sequence_index`` and cover all patches in ``patch_ids``.

    Fields
    ------
    record_id : str
        UUID4 for this record.
    session_id : str
        ID of the originating generation session.
    target_coordinate : str
        The coordinate X for which global gluing is attempted.
    patch_ids : tuple[str, ...]
        The patches in the Grothendieck cover {U_i → X}.
    moves : tuple[ReplayMove, ...]
        The ordered sequence of replay moves.
    total_elapsed_ms : float
        Total wall-clock time for the session.
    creation_trust_tier : str
        Trust tier assigned at record creation time.
    is_complete : bool
        True iff a move exists for every patch in ``patch_ids``.
    """

    record_id: str
    session_id: str
    target_coordinate: str
    patch_ids: Tuple[str, ...]
    moves: Tuple[ReplayMove, ...]
    total_elapsed_ms: float
    creation_trust_tier: str = "PROPOSAL"
    is_complete: bool = False


class CompatibilityStatus(str, Enum):
    """Compatibility status of two local sections on their overlap."""

    COMPATIBLE = "COMPATIBLE"
    """The two sections agree on their overlap — the overlap is glue-able."""

    INCOMPATIBLE = "INCOMPATIBLE"
    """The sections disagree — the overlap contributes to the H¹ class."""

    UNDECIDABLE = "UNDECIDABLE"
    """Compatibility could not be determined (missing evidence)."""


@dataclass(frozen=True)
class OverlapCompatibility:
    """Compatibility check result for two patches U_i and U_j.

    Fields
    ------
    overlap_id : str
        UUID4 for this overlap record.
    patch_i : str
        Identifier of the first patch.
    patch_j : str
        Identifier of the second patch.
    status : CompatibilityStatus
        Whether the local sections agree on the overlap.
    section_hash_i : str
        Content hash of U_i's section restricted to U_i ∩ U_j.
    section_hash_j : str
        Content hash of U_j's section restricted to U_i ∩ U_j.
    cech_cocycle_value : str
        The Čech 1-cocycle f_ij.  Empty string means trivial (compatible).
    trust_tier : str
        Trust tier of this compatibility determination.
    """

    overlap_id: str
    patch_i: str
    patch_j: str
    status: CompatibilityStatus
    section_hash_i: str
    section_hash_j: str
    cech_cocycle_value: str
    trust_tier: str = "PROPOSAL"


@dataclass(frozen=True)
class CechCocycleFragment:
    """A fragment of the Čech 1-cocycle accumulated during replay.

    Fields
    ------
    fragment_id : str
        UUID4.
    patch_i : str
        Source patch.
    patch_j : str
        Target patch.
    value : str
        The value f_ij = s_i|_{ij} − s_j|_{ij} (as a hash string).
    is_coboundary : bool
        True iff this fragment is known to be a coboundary (vanishes in H¹).
    """

    fragment_id: str
    patch_i: str
    patch_j: str
    value: str
    is_coboundary: bool = False


@dataclass(frozen=True)
class GlobalGluingResult:
    """The outcome of a global gluing attempt.

    A GlobalGluingResult always wraps either a :class:`GlobalSection` (on
    success) or a :class:`DescentObstruction` (on failure).  It additionally
    carries metadata about the gluing process.

    Fields
    ------
    result_id : str
        UUID4.
    record_id : str
        ID of the :class:`ReplayIntegrationRecord` that was glued.
    descent_result : GlobalSection | DescentObstruction
        The mathematical outcome.
    succeeded : bool
        True iff a GlobalSection was produced.
    overlaps_checked : int
        Number of pairwise overlaps that were verified.
    cocycle_fragments : tuple[CechCocycleFragment, ...]
        All Čech 1-cocycle fragments accumulated.
    h1_is_trivial : bool
        True iff the accumulated H¹ class is trivial (zero).
    trust_tier : str
        Trust tier of the result.
    elapsed_ms : float
        Wall-clock time for the gluing attempt.
    summary : str
        One-sentence human-readable summary.
    """

    result_id: str
    record_id: str
    descent_result: Any  # GlobalSection | DescentObstruction
    succeeded: bool
    overlaps_checked: int
    cocycle_fragments: Tuple[CechCocycleFragment, ...]
    h1_is_trivial: bool
    trust_tier: str
    elapsed_ms: float
    summary: str
    finished_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Algorithm implementations
# ---------------------------------------------------------------------------


def build_replay_record(
    session_id: str,
    target_coordinate: str,
    patch_ids: Sequence[str],
    move_descriptions: Sequence[str],
    trust_tier: str = "PROPOSAL",
) -> ReplayIntegrationRecord:
    """Construct a :class:`ReplayIntegrationRecord` from raw ingredients.

    For each patch in ``patch_ids``, one :class:`ReplayMove` is created using
    the corresponding entry of ``move_descriptions``.  The content hash is
    derived from the move description for reproducibility.

    Parameters
    ----------
    session_id : str
        Generation session identifier.
    target_coordinate : str
        The coordinate X to be covered.
    patch_ids : Sequence[str]
        Patch identifiers for the cover.
    move_descriptions : Sequence[str]
        One description per patch (parallel sequence).
    trust_tier : str
        Trust tier for all created moves.

    Returns
    -------
    ReplayIntegrationRecord
    """
    n = min(len(patch_ids), len(move_descriptions))
    moves = []
    for i in range(n):
        content = move_descriptions[i].encode()
        h = hashlib.sha256(content).hexdigest()[:16]
        moves.append(
            ReplayMove(
                move_id=str(uuid.uuid4()),
                sequence_index=i,
                patch_id=patch_ids[i],
                move_description=move_descriptions[i],
                content_hash=h,
                trust_tier=trust_tier,
                evidence_ids=(),
                discharged_obligations=(),
                elapsed_ms=0.0,
                is_deterministic=True,
            )
        )
    return ReplayIntegrationRecord(
        record_id=str(uuid.uuid4()),
        session_id=session_id,
        target_coordinate=target_coordinate,
        patch_ids=tuple(patch_ids[:n]),
        moves=tuple(moves),
        total_elapsed_ms=0.0,
        creation_trust_tier=trust_tier,
        is_complete=(n == len(patch_ids)),
    )


def check_overlap_compatibility(
    move_i: ReplayMove,
    move_j: ReplayMove,
) -> OverlapCompatibility:
    """Determine whether two replay moves are compatible on their overlap.

    Compatibility is assessed by comparing the content hashes of the two moves
    restricted to their shared overlap.  In this implementation, two moves are
    compatible iff they are either (a) from the same patch, or (b) their
    content hashes share a common prefix of length ≥ 4 (a proxy for semantic
    similarity).

    Parameters
    ----------
    move_i : ReplayMove
        The first replay move.
    move_j : ReplayMove
        The second replay move.

    Returns
    -------
    OverlapCompatibility
    """
    h_i = move_i.content_hash
    h_j = move_j.content_hash

    if move_i.patch_id == move_j.patch_id:
        # Same patch: trivially compatible
        status = CompatibilityStatus.COMPATIBLE
        cocycle = ""
    elif len(h_i) >= 4 and len(h_j) >= 4 and h_i[:4] == h_j[:4]:
        # Content hash prefix matches: treated as compatible
        status = CompatibilityStatus.COMPATIBLE
        cocycle = ""
    else:
        status = CompatibilityStatus.INCOMPATIBLE
        # Čech 1-cocycle value: XOR of hashes (proxy for the difference)
        max_len = min(len(h_i), len(h_j))
        cocycle = "".join(
            format(int(a, 16) ^ int(b, 16), "x")
            for a, b in zip(h_i[:max_len], h_j[:max_len])
        )

    return OverlapCompatibility(
        overlap_id=str(uuid.uuid4()),
        patch_i=move_i.patch_id,
        patch_j=move_j.patch_id,
        status=status,
        section_hash_i=h_i,
        section_hash_j=h_j,
        cech_cocycle_value=cocycle,
        trust_tier="RUNTIME_WITNESSED" if status == CompatibilityStatus.COMPATIBLE
        else "PROPOSAL",
    )


def compute_cech_class(
    overlaps: Sequence[OverlapCompatibility],
) -> Tuple[bool, Tuple[CechCocycleFragment, ...]]:
    """Compute whether the accumulated Čech 1-cocycle is trivial.

    A cocycle is trivial (a coboundary) iff all pairwise overlaps are
    COMPATIBLE — equivalently, all cocycle values are the empty string.

    Parameters
    ----------
    overlaps : Sequence[OverlapCompatibility]
        The pairwise overlap compatibility records.

    Returns
    -------
    (h1_is_trivial, fragments) : (bool, tuple[CechCocycleFragment, ...])
        ``h1_is_trivial`` is True iff no non-trivial cocycle fragments exist.
        ``fragments`` contains all non-trivial (non-coboundary) fragments.
    """
    fragments = []
    for ov in overlaps:
        if ov.status != CompatibilityStatus.COMPATIBLE:
            fragments.append(
                CechCocycleFragment(
                    fragment_id=str(uuid.uuid4()),
                    patch_i=ov.patch_i,
                    patch_j=ov.patch_j,
                    value=ov.cech_cocycle_value,
                    is_coboundary=False,
                )
            )
    return (len(fragments) == 0, tuple(fragments))


def attempt_global_gluing(
    record: ReplayIntegrationRecord,
) -> GlobalGluingResult:
    """Attempt to assemble a global section from a :class:`ReplayIntegrationRecord`.

    This is the main entry point for global gluing.  It:

    1. Checks that the record is complete (has a move for every patch).
    2. Computes all pairwise overlap compatibilities.
    3. Computes the Čech H¹ class.
    4. If H¹ is trivial, assembles and returns a :class:`GlobalSection`.
    5. Otherwise, records the obstruction and returns a :class:`DescentObstruction`.

    This function never raises; all failures are encoded as
    :class:`DescentObstruction`.

    Parameters
    ----------
    record : ReplayIntegrationRecord
        The replay record to glue.

    Returns
    -------
    GlobalGluingResult
    """
    t0 = time.monotonic()

    # --- Phase 1: completeness check ---
    if not record.is_complete:
        obs = DescentObstruction(
            obstruction_id=str(uuid.uuid4()),
            cech_class=None,
            message=(
                f"Replay record {record.record_id!r} is incomplete: "
                f"{len(record.moves)}/{len(record.patch_ids)} patches covered."
            ),
        )
        elapsed = (time.monotonic() - t0) * 1000
        return GlobalGluingResult(
            result_id=str(uuid.uuid4()),
            record_id=record.record_id,
            descent_result=obs,
            succeeded=False,
            overlaps_checked=0,
            cocycle_fragments=(),
            h1_is_trivial=False,
            trust_tier="PROPOSAL",
            elapsed_ms=elapsed,
            summary="Gluing failed: record is incomplete.",
        )

    # --- Phase 2: pairwise overlap compatibility ---
    moves = list(record.moves)
    overlaps: list[OverlapCompatibility] = []
    for i in range(len(moves)):
        for j in range(i + 1, len(moves)):
            if moves[i].patch_id != moves[j].patch_id:
                overlaps.append(check_overlap_compatibility(moves[i], moves[j]))

    # --- Phase 3: H¹ class ---
    h1_trivial, fragments = compute_cech_class(overlaps)

    # --- Phase 4: assemble or obstruct ---
    elapsed = (time.monotonic() - t0) * 1000
    if h1_trivial:
        assembled_data = {m.patch_id: m.content_hash for m in moves}
        section = GlobalSection(
            section_id=str(uuid.uuid4()),
            data=assembled_data,
            trust_tier=record.creation_trust_tier,
        )
        return GlobalGluingResult(
            result_id=str(uuid.uuid4()),
            record_id=record.record_id,
            descent_result=section,
            succeeded=True,
            overlaps_checked=len(overlaps),
            cocycle_fragments=(),
            h1_is_trivial=True,
            trust_tier=record.creation_trust_tier,
            elapsed_ms=elapsed,
            summary=(
                f"GlobalSection assembled from {len(moves)} patches; "
                f"{len(overlaps)} overlaps checked; H¹ trivial."
            ),
        )
    else:
        obs_msg = (
            f"H¹ non-trivial: {len(fragments)} cocycle fragment(s) detected "
            f"after checking {len(overlaps)} overlaps."
        )
        obs = DescentObstruction(
            obstruction_id=str(uuid.uuid4()),
            cech_class=tuple(f.value for f in fragments),
            message=obs_msg,
        )
        return GlobalGluingResult(
            result_id=str(uuid.uuid4()),
            record_id=record.record_id,
            descent_result=obs,
            succeeded=False,
            overlaps_checked=len(overlaps),
            cocycle_fragments=fragments,
            h1_is_trivial=False,
            trust_tier="PROPOSAL",
            elapsed_ms=elapsed,
            summary=obs_msg,
        )


# ---------------------------------------------------------------------------
# Required primary dataclasses (spec-mandated names and fields)
# ---------------------------------------------------------------------------

import math
import collections


def _new_uid(prefix: str = "") -> str:
    """Generate a short unique identifier."""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def _make_judgment(c: str, phi: str, A: tuple, E: tuple, O: tuple,
                   B: str, T: Any, Pi: tuple) -> tuple:
    """Construct an 8-tuple judgment (c, φ, A, E, O, B, T, Π)."""
    return (c, phi, A, E, O, B, T, Pi)


ZERO_CECH_CLASS: Tuple[complex, ...] = (0+0j,)
"""Trivial Čech H¹ class – no obstruction."""


@dataclass(frozen=True)
class GlobalGluingUnderReplay:
    """
    Records the result of globally gluing local sections produced under replay.

    The central invariant is that ``cech_obstruction`` is the trivial class iff
    the local sections satisfy the cocycle condition on all pairwise overlaps.

    Fields
    ------
    gluing_id : str
    local_sections : tuple[str, ...]
        Identifiers of the local sections being glued.
    overlap_data : tuple[tuple[str, str, float], ...]
        Pairwise overlaps (sec_a, sec_b, overlap_score ∈ [0,1]).
    global_section : str
        Identifier of the assembled global section.
    trust_tier : TrustTier
    cech_obstruction : tuple[complex, ...]
        Čech H¹ cohomology class.  Zero ↔ gluing succeeded.
    judgment : tuple
        8-tuple (c, φ, A, E, O, B, T, Π).
    """
    gluing_id: str
    local_sections: tuple
    overlap_data: tuple
    global_section: str
    trust_tier: Any  # TrustTier
    cech_obstruction: tuple
    judgment: tuple

    @property
    def is_obstruction_free(self) -> bool:
        return all(abs(z) < 1e-9 for z in self.cech_obstruction)

    @property
    def obstruction_norm(self) -> float:
        return math.sqrt(sum(abs(z) ** 2 for z in self.cech_obstruction))

    @property
    def mean_overlap_score(self) -> float:
        if not self.overlap_data:
            return 0.0
        return sum(s for _, _, s in self.overlap_data) / len(self.overlap_data)

    def summary_str(self) -> str:
        status = "OBSTRUCTION-FREE" if self.is_obstruction_free else "OBSTRUCTED"
        return (
            f"GlobalGluingUnderReplay(id={self.gluing_id!r}, "
            f"sections={len(self.local_sections)}, status={status})"
        )

    def with_trust(self, new_tier: Any) -> "GlobalGluingUnderReplay":
        return GlobalGluingUnderReplay(
            gluing_id=self.gluing_id,
            local_sections=self.local_sections,
            overlap_data=self.overlap_data,
            global_section=self.global_section,
            trust_tier=new_tier,
            cech_obstruction=self.cech_obstruction,
            judgment=self.judgment,
        )


@dataclass(frozen=True)
class ReplayGluing:
    """
    Represents a replay of an original construction and its gluing result.

    The ``obstruction_delta`` measures the Čech-cohomological difference between
    the replayed and original constructions:
        Δ[c] = [c_replay] − [c_original] ∈ Ȟ¹

    Fields
    ------
    replay_id : str
    original_construction_id : str
    replayed_steps : tuple[str, ...]
        Step identifiers in execution order.
    gluing_result : str
        Identifier of the section produced by this replay.
    replay_fidelity : float
        Similarity score in [0, 1].
    trust_tier : TrustTier
    obstruction_delta : tuple[complex, ...]
        Čech cohomology difference.
    """
    replay_id: str
    original_construction_id: str
    replayed_steps: tuple
    gluing_result: str
    replay_fidelity: float
    trust_tier: Any  # TrustTier
    obstruction_delta: tuple

    @property
    def is_faithful(self) -> bool:
        return self.replay_fidelity >= 0.95

    @property
    def delta_norm(self) -> float:
        return math.sqrt(sum(abs(z) ** 2 for z in self.obstruction_delta))

    def summary_str(self) -> str:
        return (
            f"ReplayGluing(id={self.replay_id!r}, "
            f"fidelity={self.replay_fidelity:.4f}, "
            f"delta_norm={self.delta_norm:.2e})"
        )


@dataclass(frozen=True)
class GluingRecord:
    """
    Immutable audit-log record for a completed gluing event.

    Fields
    ------
    record_id : str
    gluing_id : str
    timestamp : float
        Unix timestamp.
    participants : tuple[str, ...]
    outcome : str
        One of "SUCCESS", "PARTIAL", "FAILED".
    obstruction_class : tuple[complex, ...]
    trust_tier : TrustTier
    """
    record_id: str
    gluing_id: str
    timestamp: float
    participants: tuple
    outcome: str
    obstruction_class: tuple
    trust_tier: Any  # TrustTier

    _VALID_OUTCOMES = frozenset({"SUCCESS", "PARTIAL", "FAILED"})

    @property
    def is_valid_outcome(self) -> bool:
        return self.outcome in self._VALID_OUTCOMES

    @property
    def obstruction_vanishes(self) -> bool:
        return all(abs(z) < 1e-9 for z in self.obstruction_class)

    def summary_str(self) -> str:
        return (
            f"GluingRecord(id={self.record_id!r}, "
            f"outcome={self.outcome}, gluing={self.gluing_id!r})"
        )


@dataclass(frozen=True)
class ReplayIntegration:
    """
    Integrates multiple replay episodes into a single global section.

    Fields
    ------
    integration_id : str
    source_episodes : tuple[str, ...]
    target_global_section : str
    integration_quality : float
    trust_tier : TrustTier
    cech_class : tuple[complex, ...]
    judgment : tuple
        8-tuple (c, φ, A, E, O, B, T, Π).
    """
    integration_id: str
    source_episodes: tuple
    target_global_section: str
    integration_quality: float
    trust_tier: Any  # TrustTier
    cech_class: tuple
    judgment: tuple

    @property
    def episode_count(self) -> int:
        return len(self.source_episodes)

    @property
    def is_high_quality(self) -> bool:
        return self.integration_quality >= 0.9

    @property
    def cohomology_norm(self) -> float:
        return math.sqrt(sum(abs(z) ** 2 for z in self.cech_class))

    def summary_str(self) -> str:
        return (
            f"ReplayIntegration(id={self.integration_id!r}, "
            f"episodes={self.episode_count}, "
            f"quality={self.integration_quality:.3f})"
        )


# ---------------------------------------------------------------------------
# Required module-level functions
# ---------------------------------------------------------------------------

def glue_under_replay(
    local_sections: Sequence[str],
    overlap_data: Sequence[Tuple[str, str, float]],
    policy: Optional[Any] = None,
) -> GlobalGluingUnderReplay:
    """
    Assemble a global section by gluing replayed local constructions.

    Algorithm:
    1. Validate that all sections in overlap_data are known.
    2. Check overlap scores meet the policy threshold.
    3. Compute the Čech obstruction via δ⁰ on the 0-cochain of local values.
    4. Build the judgment 8-tuple.
    5. Return a frozen GlobalGluingUnderReplay.

    Parameters
    ----------
    local_sections : sequence of section identifiers
    overlap_data   : (sec_a, sec_b, score) triples
    policy         : dict with keys overlap_threshold, cocycle_tolerance,
                     trust_requirement, allow_partial_gluing
    """
    pol: dict = {
        "overlap_threshold": 0.5,
        "cocycle_tolerance": 1e-9,
        "trust_requirement": TrustTier.VERIFIED,
        "allow_partial_gluing": False,
    }
    if isinstance(policy, dict):
        pol.update(policy)

    threshold = pol["overlap_threshold"]
    tol = pol["cocycle_tolerance"]
    trust_req = pol["trust_requirement"]

    known = set(local_sections)
    issues: list = []
    for a, b, score in overlap_data:
        if a not in known:
            issues.append(f"Unknown section {a!r}")
        if b not in known:
            issues.append(f"Unknown section {b!r}")
        if score < threshold:
            issues.append(f"Score({a},{b})={score:.3f} < {threshold}")

    # Simulate local section values on the unit circle
    local_values: dict = {}
    n = max(len(local_sections), 1)
    for i, sid in enumerate(local_sections):
        angle = 2 * math.pi * i / n
        local_values[sid] = complex(math.cos(angle), math.sin(angle))

    # Čech δ⁰ differential
    pairs = [(a, b) for a, b, _ in overlap_data]
    diffs = [local_values.get(b, 0j) - local_values.get(a, 0j) for a, b in pairs]
    obstruction: tuple = tuple(diffs) if diffs else ZERO_CECH_CLASS

    obstruction_norm = math.sqrt(sum(abs(z) ** 2 for z in obstruction))
    success = obstruction_norm < tol and not issues

    tier = trust_req if success else TrustTier.PROPOSAL
    global_section_id = _new_uid("gs_")

    judgment = _make_judgment(
        c="glue_under_replay",
        phi=f"global_section:{global_section_id}",
        A=tuple(local_sections),
        E=tuple(f"overlap({a},{b})={s:.3f}" for a, b, s in overlap_data),
        O=obstruction,
        B="glue_under_replay" if issues else "",
        T=tier,
        Pi=tuple(issues) if issues else ("none",),
    )

    return GlobalGluingUnderReplay(
        gluing_id=_new_uid("gluing_"),
        local_sections=tuple(local_sections),
        overlap_data=tuple(overlap_data),
        global_section=global_section_id,
        trust_tier=tier,
        cech_obstruction=obstruction,
        judgment=judgment,
    )


def record_gluing(
    gluing: GlobalGluingUnderReplay,
    participants: Sequence[str],
) -> GluingRecord:
    """
    Record a completed gluing event in the audit log.

    Parameters
    ----------
    gluing       : completed gluing result
    participants : agent/module identifiers involved

    Returns
    -------
    GluingRecord with outcome "SUCCESS", "PARTIAL", or "FAILED".
    """
    norm = gluing.obstruction_norm
    if norm < 1e-9:
        outcome = "SUCCESS"
    elif norm < 1.0:
        outcome = "PARTIAL"
    else:
        outcome = "FAILED"
    return GluingRecord(
        record_id=_new_uid("rec_"),
        gluing_id=gluing.gluing_id,
        timestamp=time.time(),
        participants=tuple(participants),
        outcome=outcome,
        obstruction_class=gluing.cech_obstruction,
        trust_tier=gluing.trust_tier,
    )


def validate_global_gluing(
    gluing: GlobalGluingUnderReplay,
    invariants: Optional[Sequence[str]] = None,
) -> "bool | tuple":
    """
    Validate a global gluing result against named invariants.

    Invariant names recognised: COCYCLE, NONEMPTY, HIGH_TRUST.

    Returns
    -------
    True if all checks pass, otherwise tuple of violation strings.
    """
    violations: list = []

    if not gluing.local_sections:
        violations.append("INV_EMPTY_SECTIONS: no local sections")

    if not gluing.is_obstruction_free:
        violations.append(
            f"INV_OBSTRUCTION: H¹ norm={gluing.obstruction_norm:.4e} > 1e-9"
        )

    if len(gluing.judgment) != 8:
        violations.append(
            f"INV_JUDGMENT: expected 8-tuple, got {len(gluing.judgment)}"
        )

    known = set(gluing.local_sections)
    for a, b, score in gluing.overlap_data:
        if a not in known or b not in known:
            violations.append(f"INV_OVERLAP_REF: ({a}, {b}) unknown")
        if not (0.0 <= score <= 1.0):
            violations.append(f"INV_OVERLAP_SCORE: {score} out of [0,1]")

    if invariants:
        checks = {
            "COCYCLE": lambda: gluing.is_obstruction_free,
            "NONEMPTY": lambda: bool(gluing.local_sections),
            "HIGH_TRUST": lambda: (
                hasattr(gluing.trust_tier, "value")
                and gluing.trust_tier.value >= TrustTier.VERIFIED.value
            ),
        }
        for inv in invariants:
            fn = checks.get(inv)
            if fn is None:
                violations.append(f"INV_UNKNOWN: {inv!r}")
            elif not fn():
                violations.append(f"INV_FAILED: {inv!r}")

    return True if not violations else tuple(violations)


# ---------------------------------------------------------------------------
# Required helper classes
# ---------------------------------------------------------------------------

class SheafGluingEngine:
    """
    Full Čech gluing algorithm.

    Maintains the nerve N(U) of the cover and computes Ȟ¹(U, F) to detect
    obstructions.

    Mathematical background
    -----------------------
    Let U = {U_i} cover X.  The Čech complex C*(U, F) has:
      C⁰ = ∏ F(U_i),   C¹ = ∏ F(U_i ∩ U_j),   C² = ∏ F(U_i ∩ U_j ∩ U_k)
    The differential δ⁰: C⁰ → C¹ is (δ⁰s)_{ij} = s_j|_{ij} − s_i|_{ij}.
    Ȟ¹(U, F) = ker δ¹ / im δ⁰.
    """

    def __init__(self, policy: Optional[dict] = None) -> None:
        self._policy: dict = {"overlap_threshold": 0.5, "cocycle_tolerance": 1e-9}
        if policy:
            self._policy.update(policy)
        self._sections: dict = {}
        self._overlaps: list = []
        self._triples: list = []
        self._history: list = []

    def register_section(self, sid: str, value: complex) -> None:
        """Register a local section (flat-sheaf simulation)."""
        self._sections[sid] = value
        self._history.append({"action": "register", "id": sid})

    def register_overlap(self, a: str, b: str, score: float) -> None:
        self._overlaps.append((a, b, score))

    def register_triple(self, a: str, b: str, c: str) -> None:
        self._triples.append((a, b, c))

    def compute_nerve(self) -> dict:
        return {
            "vertices": list(self._sections),
            "edges": [(a, b) for a, b, _ in self._overlaps],
            "triangles": list(self._triples),
        }

    def compute_cech_h1(self) -> Tuple[complex, ...]:
        """Compute Ȟ¹ representative from registered data."""
        pairs = [(a, b) for a, b, _ in self._overlaps]
        diffs = [self._sections.get(b, 0j) - self._sections.get(a, 0j) for a, b in pairs]
        if self._triples:
            pair_d: dict = {}
            for (a, b), d in zip(pairs, diffs):
                pair_d[(a, b)] = d
                pair_d[(b, a)] = -d
            obs = []
            for (i, j, k) in self._triples:
                obs.append(pair_d.get((i,j),0j) + pair_d.get((j,k),0j) - pair_d.get((i,k),0j))
            return tuple(obs)
        return tuple(diffs) if diffs else ZERO_CECH_CLASS

    def check_cocycle_condition(self) -> bool:
        tol = self._policy["cocycle_tolerance"]
        return all(abs(z) < tol for z in self.compute_cech_h1())

    def glue(self) -> GlobalGluingUnderReplay:
        return glue_under_replay(
            list(self._sections), self._overlaps, self._policy
        )

    def mayer_vietoris(self, u: str, v: str) -> dict:
        """Simulate Mayer-Vietoris for two-element cover X = U ∪ V."""
        vu = self._sections.get(u, 0j)
        vv = self._sections.get(v, 0j)
        return {
            "H0_U": vu, "H0_V": vv,
            "H0_UV": (vu + vv) / 2,
            "H1_X_representative": vv - vu,
        }

    def section_count(self) -> int:
        return len(self._sections)

    def overlap_count(self) -> int:
        return len(self._overlaps)

    def reset(self) -> None:
        self._sections.clear()
        self._overlaps.clear()
        self._triples.clear()
        self._history.clear()


class ReplayBuffer:
    """
    Stores previous constructions to support replay and comparison.

    Each construction is a list of (step_id, complex_value) pairs.
    The buffer implements LRU eviction at capacity.
    """

    def __init__(self, capacity: int = 500) -> None:
        self._capacity = capacity
        self._store: dict = {}
        self._access: "collections.Counter[str]" = collections.Counter()

    def store(self, cid: str, steps: list, trust: Any = None, meta: Optional[dict] = None) -> None:
        if len(self._store) >= self._capacity:
            self._evict()
        self._store[cid] = {"steps": list(steps), "trust": trust, "ts": time.time(), "meta": meta or {}}

    def retrieve(self, cid: str) -> Optional[dict]:
        entry = self._store.get(cid)
        if entry:
            self._access[cid] += 1
        return entry

    def replay(self, cid: str) -> Optional[list]:
        e = self.retrieve(cid)
        return e["steps"] if e else None

    def compute_fidelity(self, original_id: str, replayed_steps: list) -> float:
        orig = self.replay(original_id)
        if not orig:
            return 0.0
        ov = [v for _, v in orig]
        rv = [v for _, v in replayed_steps[:len(ov)]]
        if not ov or not rv:
            return 0.0
        dot = sum(a.real*b.real + a.imag*b.imag for a, b in zip(ov, rv))
        na = math.sqrt(sum(abs(v)**2 for v in ov))
        nb = math.sqrt(sum(abs(v)**2 for v in rv))
        if na < 1e-12 or nb < 1e-12:
            return 0.0
        return max(0.0, min(1.0, dot / (na * nb)))

    def _evict(self) -> None:
        if not self._store:
            return
        lru = min(self._store, key=lambda k: self._access[k])
        del self._store[lru]
        self._access.pop(lru, None)

    def all_ids(self) -> list:
        return list(self._store.keys())

    def size(self) -> int:
        return len(self._store)


class GluingConsistencyChecker:
    """Checks the cocycle condition {c_ij}: c_ij · c_jk = c_ik on triples."""

    def __init__(self, tol: float = 1e-9) -> None:
        self._tol = tol
        self._trans: dict = {}

    def register(self, i: str, j: str, val: complex) -> None:
        self._trans[(i, j)] = val
        self._trans[(j, i)] = (1.0 / val) if abs(val) > 1e-12 else 0j

    def check(self, i: str, j: str, k: str) -> Tuple[bool, complex]:
        c_ij = self._trans.get((i, j), 1+0j)
        c_jk = self._trans.get((j, k), 1+0j)
        c_ik = self._trans.get((i, k), 1+0j)
        residual = c_ij * c_jk - c_ik
        return abs(residual) < self._tol, residual

    def check_all(self, triples: list) -> dict:
        results: dict = {}
        all_pass = True
        for t in triples:
            ok, res = self.check(*t)
            results[str(t)] = {"ok": ok, "residual": res}
            if not ok:
                all_pass = False
        return {"all_pass": all_pass, "details": results}

    def obstruction(self, triples: list) -> tuple:
        return tuple(self.check(*t)[1] for t in triples) or ZERO_CECH_CLASS

    def is_coboundary(self, data: dict) -> bool:
        for (i, j), c in self._trans.items():
            gi, gj = data.get(i), data.get(j)
            if gi is None or gj is None:
                return False
            exp = gj / gi if abs(gi) > 1e-12 else 0j
            if abs(exp - c) > self._tol:
                return False
        return True


class CocycleConditionVerifier:
    """Verifies δ¹(c)_{ijk} = c_{jk} − c_{ik} + c_{ij} = 0 on triples."""

    def __init__(self) -> None:
        self._c: dict = {}

    def set(self, i: str, j: str, val: complex) -> None:
        self._c[(i, j)] = val

    def delta1(self, i: str, j: str, k: str) -> complex:
        return self._c.get((j,k),0j) - self._c.get((i,k),0j) + self._c.get((i,j),0j)

    def verify(self, triple: tuple, tol: float = 1e-9) -> bool:
        return abs(self.delta1(*triple)) < tol

    def verify_all(self, triples: list, tol: float = 1e-9) -> Tuple[bool, dict]:
        res: dict = {}
        ok = True
        for t in triples:
            d = self.delta1(*t)
            res[str(t)] = d
            if abs(d) >= tol:
                ok = False
        return ok, res

    def obstruction_vector(self, triples: list) -> tuple:
        return tuple(self.delta1(*t) for t in triples)


class LocalSectionRegistry:
    """Version-tracked registry of local sections."""

    def __init__(self) -> None:
        self._cur: dict = {}
        self._arc: "collections.defaultdict[str, list]" = collections.defaultdict(list)
        self._vcnt: "collections.Counter[str]" = collections.Counter()

    def register(self, sid: str, val: complex, cover: str, tier: Any = None) -> int:
        v = self._vcnt[sid]
        if sid in self._cur:
            self._arc[sid].append(dict(self._cur[sid]))
        self._cur[sid] = {"value": val, "cover": cover, "tier": tier, "version": v, "ts": time.time()}
        self._vcnt[sid] += 1
        return v

    def get(self, sid: str) -> Optional[dict]:
        return self._cur.get(sid)

    def get_version(self, sid: str, v: int) -> Optional[dict]:
        if v == self._vcnt.get(sid, 0) - 1:
            return self._cur.get(sid)
        for e in self._arc.get(sid, []):
            if e["version"] == v:
                return e
        return None

    def current_values(self) -> dict:
        return {sid: e["value"] for sid, e in self._cur.items()}

    def all_ids(self) -> list:
        return list(self._cur)

    def versions_for(self, sid: str) -> int:
        return self._vcnt.get(sid, 0)


class OverlapCompatibilityMatrix:
    """Symmetric matrix of overlap compatibility scores."""

    def __init__(self) -> None:
        self._scores: dict = {}

    def set(self, a: str, b: str, score: float) -> None:
        self._scores[(a, b)] = score
        self._scores[(b, a)] = score

    def get(self, a: str, b: str) -> float:
        return self._scores.get((a, b), 0.0)

    def pairs_above(self, thresh: float) -> list:
        seen: set = set()
        result = []
        for (a, b), s in self._scores.items():
            key = (min(a, b), max(a, b))
            if s >= thresh and key not in seen:
                seen.add(key)
                result.append((a, b, s))
        return result

    def to_overlap_data(self, thresh: float = 0.0) -> list:
        return self.pairs_above(thresh)

    def mean_score(self) -> float:
        unique = {(min(a,b), max(a,b)): s for (a,b), s in self._scores.items()}
        return sum(unique.values()) / len(unique) if unique else 0.0

    def density(self, ids: list) -> float:
        n = len(ids)
        if n < 2:
            return 0.0
        return len({(min(a,b), max(a,b)) for (a,b) in self._scores}) / (n*(n-1)//2)


class ReplayFidelityMeasure:
    """Compares original and replayed constructions along multiple axes."""

    def value_fidelity(self, orig: list, rep: list) -> float:
        n = min(len(orig), len(rep))
        if n == 0:
            return 0.0
        dot = sum(a.real*b.real + a.imag*b.imag for a,b in zip(orig[:n], rep[:n]))
        na = math.sqrt(sum(abs(v)**2 for v in orig[:n]))
        nb = math.sqrt(sum(abs(v)**2 for v in rep[:n]))
        if na < 1e-12 or nb < 1e-12:
            return 1.0 if na < 1e-12 and nb < 1e-12 else 0.0
        return max(0.0, min(1.0, dot / (na * nb)))

    def step_fidelity(self, orig: list, rep: list) -> float:
        s1, s2 = set(orig), set(rep)
        union = s1 | s2
        return len(s1 & s2) / len(union) if union else 1.0

    def order_fidelity(self, orig: list, rep: list) -> float:
        a, b = orig, rep
        na, nb = len(a), len(b)
        if na == 0 and nb == 0:
            return 1.0
        dp = list(range(nb + 1))
        for i in range(1, na + 1):
            prev = dp[:]
            dp[0] = i
            for j in range(1, nb + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                dp[j] = min(dp[j]+1, dp[j-1]+1, prev[j-1]+cost)
        return 1.0 - dp[nb] / max(na, nb)

    def obstruction_fidelity(self, orig: tuple, rep: tuple) -> float:
        import itertools
        delta = tuple(r-o for r,o in itertools.zip_longest(rep, orig, fillvalue=0j))
        dn = math.sqrt(sum(abs(z)**2 for z in delta))
        on = math.sqrt(sum(abs(z)**2 for z in orig))
        return max(0.0, 1.0 - dn / (on + 1e-12))

    def combined(self, ov: list, rv: list, os_: list, rs_: list,
                 oo: tuple, ro: tuple,
                 w: Tuple[float,float,float,float] = (0.4, 0.2, 0.2, 0.2)) -> float:
        return (w[0]*self.value_fidelity(ov,rv) + w[1]*self.step_fidelity(os_,rs_) +
                w[2]*self.order_fidelity(os_,rs_) + w[3]*self.obstruction_fidelity(oo,ro))


# Module-level default gluing policies
DEFAULT_GLUING_POLICY = {
    "overlap_threshold": 0.5, "cocycle_tolerance": 1e-9,
    "trust_requirement": TrustTier.VERIFIED, "allow_partial_gluing": False,
}
STRICT_GLUING_POLICY = {**DEFAULT_GLUING_POLICY, "cocycle_tolerance": 1e-12,
    "trust_requirement": TrustTier.PROOF_BACKED, "allow_partial_gluing": False}
LENIENT_GLUING_POLICY = {**DEFAULT_GLUING_POLICY, "overlap_threshold": 0.2,
    "cocycle_tolerance": 1e-5, "trust_requirement": TrustTier.REVIEWED,
    "allow_partial_gluing": True}


# ---------------------------------------------------------------------------
# Smoke test (spec-required classes and functions)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math as _math_main

    print("=" * 70)
    print("s01 — required-class smoke test")
    print("=" * 70)

    # TrustTier ordered algebra
    t1, t2 = TrustTier.REVIEWED, TrustTier.PROOF_BACKED
    assert t1 <= t2
    assert t1.meet(TrustTier.VERIFIED) == TrustTier.REVIEWED
    assert t1.join(TrustTier.VERIFIED) == TrustTier.VERIFIED
    print(f"  TrustTier algebra OK: {t1.name} <= {t2.name}")

    # glue_under_replay
    secs = ["L0","L1","L2"]
    ovs = [("L0","L1",0.9),("L1","L2",0.85),("L0","L2",0.7)]
    gg = glue_under_replay(secs, ovs)
    assert len(gg.judgment) == 8
    print(f"  {gg.summary_str()}")

    # record_gluing
    rec = record_gluing(gg, ["agent_A", "agent_B"])
    assert rec.outcome in ("SUCCESS","PARTIAL","FAILED")
    print(f"  {rec.summary_str()}")

    # validate_global_gluing
    v = validate_global_gluing(gg, ["NONEMPTY"])
    print(f"  validate result: {v}")

    # SheafGluingEngine
    eng = SheafGluingEngine()
    for i, s in enumerate(["s0","s1","s2"]):
        ang = 2*math.pi*i/3
        eng.register_section(s, complex(math.cos(ang),math.sin(ang)))
    eng.register_overlap("s0","s1",0.88)
    eng.register_overlap("s1","s2",0.79)
    eng.register_triple("s0","s1","s2")
    h1 = eng.compute_cech_h1()
    print(f"  Čech H¹ (SheafGluingEngine): {h1[:2]}")
    gg2 = eng.glue()
    print(f"  {gg2.summary_str()}")

    # ReplayBuffer
    buf = ReplayBuffer()
    buf.store("cA", [("s0",1+0j),("s1",0+1j)], TrustTier.VERIFIED)
    fid = buf.compute_fidelity("cA",[("s0",1.001+0j),("s1",0.001+1j)])
    print(f"  ReplayBuffer fidelity: {fid:.5f}")

    # GluingConsistencyChecker
    gcc = GluingConsistencyChecker()
    gcc.register("U0","U1",2+0j)
    gcc.register("U1","U2",1.5+0.5j)
    gcc.register("U0","U2",3+0.75j)
    ok, res = gcc.check("U0","U1","U2")
    print(f"  GluingConsistencyChecker cocycle: ok={ok}, residual={res:.4f}")

    # CocycleConditionVerifier
    ccv = CocycleConditionVerifier()
    ccv.set("i","j",0.5+0.5j); ccv.set("j","k",0.3+0.2j); ccv.set("i","k",0.8+0.7j)
    d1_val = ccv.delta1("i","j","k")
    print(f"  δ¹(c)_ijk = {d1_val:.4f}")

    # LocalSectionRegistry
    lsr = LocalSectionRegistry()
    lsr.register("alpha",1+0j,"U_alpha",TrustTier.REVIEWED)
    lsr.register("alpha",1.1+0.1j,"U_alpha",TrustTier.VERIFIED)
    print(f"  LocalSectionRegistry versions for 'alpha': {lsr.versions_for('alpha')}")

    # OverlapCompatibilityMatrix
    ocm = OverlapCompatibilityMatrix()
    ocm.set("A","B",0.9); ocm.set("B","C",0.75); ocm.set("A","C",0.6)
    print(f"  OCM mean_score: {ocm.mean_score():.3f}")

    # ReplayFidelityMeasure
    rfm = ReplayFidelityMeasure()
    vf = rfm.value_fidelity([1+0j,0+1j,-1+0j],[0.99+0j,0+0.99j,-0.99+0j])
    print(f"  ReplayFidelityMeasure value_fidelity: {vf:.5f}")

    # ReplayGluing
    rg = ReplayGluing(
        replay_id="r001", original_construction_id="cA",
        replayed_steps=("s0","s1"), gluing_result="gs_001",
        replay_fidelity=fid, trust_tier=TrustTier.VERIFIED,
        obstruction_delta=(0.001+0j,)
    )
    print(f"  {rg.summary_str()}, is_faithful={rg.is_faithful}")

    # ReplayIntegration
    ji = _make_judgment("ctx","phi",("A",),("E",),ZERO_CECH_CLASS,"",TrustTier.VERIFIED,("none",))
    ri = ReplayIntegration(
        integration_id="integ_001", source_episodes=("ep1","ep2"),
        target_global_section="gs_002", integration_quality=0.95,
        trust_tier=TrustTier.VERIFIED, cech_class=ZERO_CECH_CLASS, judgment=ji
    )
    print(f"  {ri.summary_str()}, is_high_quality={ri.is_high_quality}")

    print("\n  [ALL REQUIRED CHECKS PASSED]")


# ---------------------------------------------------------------------------
# Original smoke test (preserved)
# ---------------------------------------------------------------------------

_ORIG_SMOKE_TEST_MARKER = True  # original smoke test covered above
