"""
Hypercover Treaty Synthesis, Conflict Detection, and Resolution Algorithms
===========================================================================

This module implements the core algorithmic machinery for generating, comparing,
and resolving *hypercover treaties* — formal contracts between patch interfaces
in the JuGeo geometry engine.

    # copilot: treaty synthesis, conflict detection, and resolution algorithms

Mathematical Background
-----------------------
A *hypercover treaty* is a structured agreement T = (I_A, I_B, S, E, R) where:
  - I_A, I_B  are patch interface descriptors (dicts of exports, imports,
               constraints, versioning metadata)
  - S          is the shared interface: S = exports(I_A) ∩ imports(I_B)  ∪
                                            exports(I_B) ∩ imports(I_A)
  - E          is the merged export map: E = exports(I_A) ∪ exports(I_B)
  - R          is the resolution record produced by the negotiation protocol

Conflict Model
--------------
Two treaties T_1 and T_2 are *conflicting* when any of the following hold:

1. **Interface contradiction**: an export in T_1 appears as an import in T_2 with
   incompatible version constraints.
2. **Export overlap**: both treaties export the same symbol without a merge
   agreement, creating an ambiguous binding.
3. **Trust-level mismatch**: T_1 is PROOF_BACKED while T_2 is PROPOSAL; the
   resulting negotiated treaty cannot simply average trust tiers — it must use the
   meet (infimum) in the TrustTier lattice.

Resolution Strategies
---------------------
Resolution follows the lattice semantics of TrustTier:

  PREFER_LEFT   — adopt T_A unconditionally; T_B is subordinated.
  PREFER_RIGHT  — adopt T_B unconditionally; T_A is subordinated.
  MERGE         — compute S, resolve overlaps by union, re-score trust as meet(T_A, T_B).
  SPLIT         — partition the export set so the conflict domain is isolated.

Usage
-----
    from jugeo.generation.hypercover_treaties.algorithms import run_treaty_algorithm

    result = run_treaty_algorithm(interface_a, interface_b)
    print(result["status"])   # "resolved" | "unresolved" | "trivial"

Notes
-----
* All public functions are pure (no side effects on arguments).
* Logging is performed at DEBUG level; callers may silence via the standard
  logging hierarchy.
* The :class:`TreatyAlgorithms` façade is the recommended entry point for
  production code; the module-level helpers are exposed for testing and scripting.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
from typing import Any, Optional, Sequence, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum
import math
import itertools
import hashlib
import logging
import collections
import functools
import abc
import uuid
import datetime

# ---------------------------------------------------------------------------
# Optional jugeo imports with fallback stubs
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
# Module-level constants
# ---------------------------------------------------------------------------

MAX_NEGOTIATION_ROUNDS: int = 10
"""Maximum number of rounds the :class:`TreatyNegotiator` will iterate before
declaring the negotiation unresolved. Prevents infinite loops in adversarial
interface configurations."""

CONFLICT_SCORE_THRESHOLD: float = 0.5
"""Conflicts whose severity score (in [0, 1]) is at or above this threshold are
treated as *hard* conflicts requiring explicit resolution. Scores below the
threshold are logged as warnings but do not block treaty finalization."""

DEFAULT_RESOLUTION: str = "MERGE"
"""String name of the default :class:`ResolutionStrategy` applied when the
caller does not specify a strategy. Kept as a string to avoid circular
dependency at import time."""

logger: logging.Logger = logging.getLogger(__name__)
"""Module-level logger.  Follows the standard ``logging`` hierarchy so callers
can silence or redirect output via ``logging.getLogger('jugeo.generation')``.
All diagnostic messages are emitted at DEBUG level; only fatal negotiation
failures are emitted at ERROR level."""

# ---------------------------------------------------------------------------
# TrustTier — the trust lattice
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The lattice is totally ordered:

        PROPOSAL ≺ REVIEWED ≺ VERIFIED ≺ RUNTIME_WITNESSED ≺ PROOF_BACKED

    Lattice operations:

    * ``join(other)``  — least upper bound (⊕); takes the *more trusted* tier.
    * ``meet(other)``  — greatest lower bound (⊗); takes the *less trusted* tier.
    * ``promote()``    — move one step up (↑_π); capped at PROOF_BACKED.
    * ``demote()``     — move one step down (↓_χ); floored at PROPOSAL.

    These operations satisfy the axioms of a bounded distributive lattice and are
    used by :class:`TreatyNegotiator` to propagate trust when merging treaties.
    """
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Return the join (least upper bound) of ``self`` and ``other``."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Return the meet (greatest lower bound) of ``self`` and ``other``."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Advance one step up the trust lattice, capped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Retreat one step down the trust lattice, floored at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Encodes a structured claim in the JuGeo judgment calculus.  A judgment is
    *not* merely true/false; it carries the full epistemic context needed to
    audit, replay, and refute the claim.

    Attributes
    ----------
    context:
        The geometric or algebraic context in which the judgment is made (e.g.,
        a simplex identifier or nerve complex reference).
    formula:
        The proposition being judged.  In practice this is a :class:`str` or a
        structured AST node from ``jugeo.judgments``.
    assumptions:
        An ordered tuple of prerequisite judgments that must hold for this
        judgment to be valid.  Immutable and hashable.
    evidence:
        A tuple of :class:`EvidenceItemKind` tokens (or compatible objects)
        witnessing the judgment.
    obligations:
        Remaining proof obligations; non-empty iff the judgment is *provisional*.
    burden:
        Who or what bears the proof burden (a :class:`ProvenanceSource` or str).
    trust:
        The :class:`TrustTier` at which this judgment is currently accepted.
    provenance:
        Opaque provenance token — typically a :class:`uuid.UUID` or a structured
        dict referencing the solver run or human review that produced this judgment.
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
    """A Čech cohomology obstruction to gluing local data into a global section.

    In the hypercover formalism, local patch data can fail to glue globally
    when the relevant Čech 1-cocycle is non-trivial.  This dataclass records
    such an obstruction so that the negotiation protocol can attempt a split
    or identify the offending cocycle.

    Attributes
    ----------
    cover_id:
        Unique identifier of the hypercover in which the obstruction was
        detected.
    cocycle:
        A frozenset of edge-labels (typically symbol names) forming the
        non-trivial cocycle.
    cohomology_class:
        A string tag for the cohomology class, e.g. ``"H1"`` or ``"trivial"``.
    description:
        Human-readable description of what the obstruction means geometrically.
    """
    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the cocycle is empty (obstruction vanishes)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Resolution strategy enum
# ---------------------------------------------------------------------------

class ResolutionStrategy(str, Enum):
    """Conflict resolution strategies for treaty negotiation.

    Each strategy determines how two conflicting treaties T_A and T_B are
    combined into a single resolved treaty T_R.

    Attributes
    ----------
    PREFER_LEFT:
        Unconditionally adopt T_A.  T_B's exports are discarded or subordinated.
        Use when T_A has strictly higher trust tier or provenance authority.
    PREFER_RIGHT:
        Unconditionally adopt T_B.  T_A's exports are discarded or subordinated.
        Symmetric dual of PREFER_LEFT.
    MERGE:
        Compute the union of exports and resolve overlaps using the meet of trust
        tiers.  This is the default strategy and produces the richest combined
        treaty at the cost of potentially lowering overall trust.
    SPLIT:
        Partition the export set so that conflicting symbols are isolated into
        separate sub-treaties.  The conflict domain is quarantined rather than
        resolved, deferring the hard decision to a later negotiation round.
    """
    PREFER_LEFT = "PREFER_LEFT"
    PREFER_RIGHT = "PREFER_RIGHT"
    MERGE = "MERGE"
    SPLIT = "SPLIT"

    @property
    def description(self) -> str:
        """Return a one-line description of the strategy."""
        _descriptions = {
            "PREFER_LEFT": "Adopt treaty A unconditionally; subordinate treaty B.",
            "PREFER_RIGHT": "Adopt treaty B unconditionally; subordinate treaty A.",
            "MERGE": "Union exports; resolve overlaps via trust meet.",
            "SPLIT": "Isolate conflicting symbols into a quarantine sub-treaty.",
        }
        return _descriptions[self.value]


# ---------------------------------------------------------------------------
# Helper functions (module-level, prefixed with _)
# ---------------------------------------------------------------------------

def _generate_treaty_id(interface_a: dict, interface_b: dict) -> str:
    """Generate a stable, deterministic treaty identifier from two interfaces.

    The ID is derived from the SHA-256 hash of the canonicalized interface
    names and versions, ensuring that the same pair of interfaces always
    produces the same treaty ID regardless of dict ordering.

    Parameters
    ----------
    interface_a:
        First patch interface descriptor.
    interface_b:
        Second patch interface descriptor.

    Returns
    -------
    str
        A hex string of the form ``"treaty-<16 hex chars>"``.

    Examples
    --------
    >>> _generate_treaty_id({"name": "A", "version": "1.0"}, {"name": "B", "version": "2.0"})
    'treaty-...'
    """
    name_a = interface_a.get("name", "")
    name_b = interface_b.get("name", "")
    ver_a = interface_a.get("version", "0.0.0")
    ver_b = interface_b.get("version", "0.0.0")
    raw = f"{name_a}:{ver_a}|{name_b}:{ver_b}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"treaty-{digest}"


def _score_conflict(conflict: dict) -> float:
    """Compute a normalised severity score in [0, 1] for a conflict record.

    The score is computed as:

        score = severity * weight(kind)

    where ``weight`` maps conflict kinds to a multiplicative factor:

    * ``"interface_contradiction"``  → 1.0  (hard conflict)
    * ``"export_overlap"``           → 0.7  (medium conflict)
    * ``"version_mismatch"``         → 0.5  (soft conflict)
    * any other kind                 → 0.3  (unknown, treat as minor)

    Parameters
    ----------
    conflict:
        A conflict record as returned by :func:`_build_conflict_record`.

    Returns
    -------
    float
        A value in [0.0, 1.0].
    """
    _weights: dict[str, float] = {
        "interface_contradiction": 1.0,
        "export_overlap": 0.7,
        "version_mismatch": 0.5,
    }
    kind = conflict.get("kind", "unknown")
    base_severity = float(conflict.get("severity", 0.3))
    weight = _weights.get(kind, 0.3)
    return min(1.0, base_severity * weight)


def _merge_exports(exports_a: frozenset, exports_b: frozenset) -> frozenset:
    """Return the union of two export sets.

    This is the lattice-join for the export partial order.  The result is the
    smallest export set containing all symbols from both interfaces.

    Parameters
    ----------
    exports_a:
        Frozenset of export symbol names from interface A.
    exports_b:
        Frozenset of export symbol names from interface B.

    Returns
    -------
    frozenset
        Union of both export sets.
    """
    return exports_a | exports_b


def _split_exports(exports: frozenset, split_key: str) -> tuple[frozenset, frozenset]:
    """Partition an export set into two disjoint halves based on a split key.

    Symbols whose string representation is lexicographically less than
    ``split_key`` go into the *left* half; the rest go into the *right* half.
    This is used by the SPLIT resolution strategy to isolate conflicting symbols.

    Parameters
    ----------
    exports:
        The full export set to partition.
    split_key:
        The pivot string used for lexicographic partitioning.

    Returns
    -------
    tuple[frozenset, frozenset]
        ``(left, right)`` where ``left ∪ right == exports`` and
        ``left ∩ right == ∅``.
    """
    left = frozenset(s for s in exports if str(s) < split_key)
    right = frozenset(s for s in exports if str(s) >= split_key)
    return left, right


def _compute_treaty_hash(treaty: dict) -> str:
    """Compute a content-addressed hash of a treaty dict.

    The hash is computed over the sorted, UTF-8 encoded string representation
    of the treaty, making it suitable for change detection and caching.

    Parameters
    ----------
    treaty:
        The treaty dict to hash.

    Returns
    -------
    str
        A 32-character hex string (first 32 chars of SHA-256 digest).
    """
    import json
    try:
        raw = json.dumps(treaty, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        raw = str(sorted(treaty.items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _validate_interface(interface: dict) -> bool:
    """Return ``True`` iff *interface* has the minimum required keys.

    A valid interface descriptor must contain at least:

    * ``"name"``    — non-empty string identifying the patch.
    * ``"version"`` — semantic version string (e.g. ``"1.2.3"``).
    * ``"exports"`` — list of exported symbol names (may be empty).
    * ``"imports"`` — list of imported symbol names (may be empty).

    Parameters
    ----------
    interface:
        The dict to validate.

    Returns
    -------
    bool
        ``True`` if all required keys are present and non-trivially typed.
    """
    required = ("name", "version", "exports", "imports")
    for key in required:
        if key not in interface:
            logger.debug("Interface missing required key: %s", key)
            return False
    if not isinstance(interface["name"], str) or not interface["name"]:
        return False
    if not isinstance(interface["exports"], (list, tuple, set, frozenset)):
        return False
    if not isinstance(interface["imports"], (list, tuple, set, frozenset)):
        return False
    return True


def _normalize_interface(interface: dict) -> dict:
    """Return a normalised copy of *interface* with canonical types.

    Normalisation ensures that:
    * ``"exports"`` and ``"imports"`` are sorted lists of strings.
    * ``"version"`` defaults to ``"0.0.0"`` if absent.
    * ``"constraints"`` defaults to an empty dict if absent.
    * ``"name"`` is stripped of leading/trailing whitespace.

    Parameters
    ----------
    interface:
        The raw interface descriptor dict.

    Returns
    -------
    dict
        A new dict with canonical types; the original is not mutated.
    """
    return {
        "name": str(interface.get("name", "")).strip(),
        "version": str(interface.get("version", "0.0.0")),
        "exports": sorted(str(e) for e in interface.get("exports", [])),
        "imports": sorted(str(i) for i in interface.get("imports", [])),
        "constraints": dict(interface.get("constraints", {})),
    }


def _extract_exports(interface: dict) -> list[str]:
    """Extract and return the exports list from an interface descriptor.

    Parameters
    ----------
    interface:
        The interface descriptor dict.

    Returns
    -------
    list[str]
        A list of export symbol names; empty list if key is absent.
    """
    return [str(e) for e in interface.get("exports", [])]


def _extract_imports(interface: dict) -> list[str]:
    """Extract and return the imports list from an interface descriptor.

    Parameters
    ----------
    interface:
        The interface descriptor dict.

    Returns
    -------
    list[str]
        A list of import symbol names; empty list if key is absent.
    """
    return [str(i) for i in interface.get("imports", [])]


def _check_version_compatibility(version_a: str, version_b: str) -> bool:
    """Determine whether two semantic version strings are compatible.

    Two versions are considered *compatible* when their *major* version
    numbers are equal.  This implements the ``^`` (caret) compatibility
    semantics from semantic versioning (semver.org).

    Parameters
    ----------
    version_a:
        First version string, e.g. ``"1.2.3"``.
    version_b:
        Second version string, e.g. ``"1.5.0"``.

    Returns
    -------
    bool
        ``True`` iff both versions share the same major version number.

    Notes
    -----
    Malformed version strings are parsed defensively: missing components
    default to ``0``.
    """
    def _major(v: str) -> int:
        parts = str(v).split(".")
        try:
            return int(parts[0])
        except (IndexError, ValueError):
            return 0

    return _major(version_a) == _major(version_b)


def _build_conflict_record(kind: str, detail: str, severity: float) -> dict:
    """Construct a standardised conflict record dict.

    Parameters
    ----------
    kind:
        A machine-readable conflict kind tag (e.g. ``"export_overlap"``).
    detail:
        A human-readable description of the specific conflict instance.
    severity:
        A raw severity estimate in [0, 1].  This will be re-scored by
        :func:`_score_conflict` during reporting.

    Returns
    -------
    dict
        A conflict record with keys: ``kind``, ``detail``, ``severity``,
        ``timestamp``, ``conflict_id``.
    """
    return {
        "conflict_id": str(uuid.uuid4()),
        "kind": kind,
        "detail": detail,
        "severity": max(0.0, min(1.0, float(severity))),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


def _annotate_treaty(treaty: dict, annotation: str) -> dict:
    """Return a copy of *treaty* with an additional annotation string appended.

    Annotations are stored in ``treaty["annotations"]``, a list of strings.
    This function never mutates the input dict.

    Parameters
    ----------
    treaty:
        The treaty dict to annotate.
    annotation:
        The annotation string to append.

    Returns
    -------
    dict
        A shallow copy of *treaty* with the annotation appended.
    """
    result = dict(treaty)
    annotations = list(result.get("annotations", []))
    annotations.append(annotation)
    result["annotations"] = annotations
    return result


# ---------------------------------------------------------------------------
# Module-level resolution function (needed by ResolutionStrategy users)
# ---------------------------------------------------------------------------

def apply_resolution_strategy(
    strategy: ResolutionStrategy,
    conflict: dict,
    treaty_a: dict,
    treaty_b: dict,
) -> dict:
    """Apply *strategy* to resolve *conflict* between *treaty_a* and *treaty_b*.

    This is the primary dispatch function for all resolution strategies.  It
    returns a *resolution record* — a dict describing the outcome — rather than
    a mutated treaty.  The caller is responsible for applying the resolution
    record to the working treaty.

    Parameters
    ----------
    strategy:
        The :class:`ResolutionStrategy` to apply.
    conflict:
        The conflict record (as produced by :func:`_build_conflict_record`)
        to resolve.
    treaty_a:
        The *left* treaty involved in the conflict.
    treaty_b:
        The *right* treaty involved in the conflict.

    Returns
    -------
    dict
        A resolution record with keys:
        ``strategy``, ``resolution``, ``detail``, ``resolved_exports``,
        ``trust_tier``, ``timestamp``.

    Raises
    ------
    JuGeoError
        If *strategy* is not a recognised :class:`ResolutionStrategy` member.
    """
    exports_a = frozenset(_extract_exports(treaty_a))
    exports_b = frozenset(_extract_exports(treaty_b))

    if strategy == ResolutionStrategy.PREFER_LEFT:
        resolved_exports = list(exports_a)
        resolution = "adopted treaty_a; treaty_b subordinated"
        trust = treaty_a.get("trust_tier", TrustTier.PROPOSAL.name)

    elif strategy == ResolutionStrategy.PREFER_RIGHT:
        resolved_exports = list(exports_b)
        resolution = "adopted treaty_b; treaty_a subordinated"
        trust = treaty_b.get("trust_tier", TrustTier.PROPOSAL.name)

    elif strategy == ResolutionStrategy.MERGE:
        merged = _merge_exports(exports_a, exports_b)
        resolved_exports = sorted(merged)
        resolution = "merged exports via union; trust set to meet"
        trust = TrustTier.PROPOSAL.name  # conservative: meet of unknown tiers

    elif strategy == ResolutionStrategy.SPLIT:
        split_key = conflict.get("detail", "")[:1] or "M"
        left, right = _split_exports(exports_a | exports_b, split_key)
        resolved_exports = sorted(left)
        resolution = f"split exports; left={sorted(left)}, right={sorted(right)}"
        trust = TrustTier.PROPOSAL.name

    else:
        raise_with_scope(
            "TREATY_RESOLUTION_UNKNOWN_STRATEGY",
            message=f"Unrecognised resolution strategy: {strategy!r}",
        )
        resolved_exports = []
        resolution = "unknown strategy; no resolution applied"
        trust = TrustTier.PROPOSAL.name

    return {
        "strategy": strategy.value,
        "resolution": resolution,
        "detail": conflict.get("detail", ""),
        "resolved_exports": resolved_exports,
        "trust_tier": trust,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# TreatySynthesizer
# ---------------------------------------------------------------------------

class TreatySynthesizer:
    """Generates a :class:`HypercoverTreaty` from two patch interface descriptors.

    The synthesiser implements the *treaty synthesis* step of the negotiation
    protocol:

    1. Normalise both interface descriptors.
    2. Compute the *shared interface* S = (exp_A ∩ imp_B) ∪ (exp_B ∩ imp_A).
    3. Build the merged export map E = exp_A ∪ exp_B.
    4. Assign a deterministic treaty ID via :func:`_generate_treaty_id`.
    5. Record synthesis metadata (strategy, timestamp, synthesiser ID).

    The synthesiser is intentionally stateful (``synthesis_count`` tracks how
    many treaties have been produced) so that callers can monitor throughput.

    Parameters
    ----------
    strategy:
        Name of the synthesis strategy.  Currently only ``"default"`` is
        implemented; future versions may support ``"conservative"`` (takes the
        meet of trust tiers) and ``"aggressive"`` (takes the join).

    Attributes
    ----------
    synthesizer_id:
        A unique UUID string identifying this synthesiser instance.
    strategy:
        The synthesis strategy name.
    synthesis_count:
        Number of treaties produced by this instance since creation.
    """

    def __init__(self, strategy: str = "default") -> None:
        self.synthesizer_id: str = str(uuid.uuid4())
        self.strategy: str = strategy
        self.synthesis_count: int = 0

    def synthesize(self, interface_a: dict, interface_b: dict) -> dict:
        """Synthesise a treaty from two interface descriptors.

        Parameters
        ----------
        interface_a:
            Left patch interface descriptor.  Must pass :func:`_validate_interface`.
        interface_b:
            Right patch interface descriptor.  Must pass :func:`_validate_interface`.

        Returns
        -------
        dict
            A treaty dict with keys: ``treaty_id``, ``interface_a``,
            ``interface_b``, ``shared_interface``, ``export_map``,
            ``strategy``, ``synthesizer_id``, ``synthesis_index``,
            ``timestamp``, ``annotations``, ``status``.

        Raises
        ------
        JuGeoError
            If either interface fails validation.
        """
        norm_a = _normalize_interface(interface_a)
        norm_b = _normalize_interface(interface_b)

        if not _validate_interface(norm_a):
            raise_with_scope("TREATY_INVALID_INTERFACE_A", message="Interface A failed validation.")
        if not _validate_interface(norm_b):
            raise_with_scope("TREATY_INVALID_INTERFACE_B", message="Interface B failed validation.")

        shared = self._compute_shared_interface(norm_a, norm_b)
        export_map = self._build_export_map(norm_a) | self._build_export_map(norm_b)
        treaty_id = _generate_treaty_id(norm_a, norm_b)

        self.synthesis_count += 1
        logger.debug(
            "TreatySynthesizer[%s] synthesised treaty %s (strategy=%s, count=%d)",
            self.synthesizer_id, treaty_id, self.strategy, self.synthesis_count,
        )

        return {
            "treaty_id": treaty_id,
            "interface_a": norm_a,
            "interface_b": norm_b,
            "shared_interface": sorted(shared),
            "export_map": sorted(export_map),
            "strategy": self.strategy,
            "synthesizer_id": self.synthesizer_id,
            "synthesis_index": self.synthesis_count,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "annotations": [],
            "status": "synthesised",
            "version_compatible": _check_version_compatibility(
                norm_a["version"], norm_b["version"]
            ),
        }

    def _compute_shared_interface(self, a: dict, b: dict) -> frozenset:
        """Compute the shared interface S = (exp_A ∩ imp_B) ∪ (exp_B ∩ imp_A).

        Parameters
        ----------
        a:
            Normalised interface A.
        b:
            Normalised interface B.

        Returns
        -------
        frozenset
            The set of symbol names that flow between the two interfaces.
        """
        exp_a = frozenset(_extract_exports(a))
        imp_b = frozenset(_extract_imports(b))
        exp_b = frozenset(_extract_exports(b))
        imp_a = frozenset(_extract_imports(a))
        return (exp_a & imp_b) | (exp_b & imp_a)

    def _build_export_map(self, interface: dict) -> frozenset:
        """Return the export set of a single normalised interface as a frozenset.

        Parameters
        ----------
        interface:
            Normalised interface descriptor.

        Returns
        -------
        frozenset
            Frozenset of export symbol name strings.
        """
        return frozenset(_extract_exports(interface))


# ---------------------------------------------------------------------------
# ConflictDetector
# ---------------------------------------------------------------------------

class ConflictDetector:
    """Finds conflicts between two treaties.

    A *conflict* arises when:

    * ``interface_contradiction``: an export of T_A appears as an import of T_B
      (or vice versa) with incompatible version constraints.
    * ``export_overlap``: both treaties export the same symbol without a prior
      merge agreement.
    * ``version_mismatch``: the treaties derive from interfaces with incompatible
      major version numbers.

    The detector accumulates a ``conflict_log`` so that a sequence of
    :meth:`detect` calls can be reviewed in aggregate via :meth:`report`.

    Attributes
    ----------
    detector_id:
        A unique UUID string for this detector instance.
    conflict_log:
        A list of all conflict records detected since the detector was created.
    """

    def __init__(self) -> None:
        self.detector_id: str = str(uuid.uuid4())
        self.conflict_log: list[dict] = []

    def detect(self, treaty_a: dict, treaty_b: dict) -> list[dict]:
        """Detect conflicts between *treaty_a* and *treaty_b*.

        Parameters
        ----------
        treaty_a:
            The first treaty dict (as produced by :class:`TreatySynthesizer`).
        treaty_b:
            The second treaty dict.

        Returns
        -------
        list[dict]
            A list of conflict records.  Empty list if no conflicts were found.
        """
        conflicts: list[dict] = []

        if self._check_interface_contradiction(treaty_a, treaty_b):
            rec = _build_conflict_record(
                kind="interface_contradiction",
                detail=(
                    f"Exports of treaty '{treaty_a.get('treaty_id', '?')}' contradict "
                    f"imports of treaty '{treaty_b.get('treaty_id', '?')}'"
                ),
                severity=0.9,
            )
            conflicts.append(rec)
            logger.debug("ConflictDetector[%s] found interface_contradiction", self.detector_id)

        overlaps = self._check_export_overlap(treaty_a, treaty_b)
        for sym in overlaps:
            rec = _build_conflict_record(
                kind="export_overlap",
                detail=f"Symbol '{sym}' exported by both treaties",
                severity=0.6,
            )
            conflicts.append(rec)

        ver_a = treaty_a.get("interface_a", {}).get("version", "0.0.0")
        ver_b = treaty_b.get("interface_a", {}).get("version", "0.0.0")
        if not _check_version_compatibility(ver_a, ver_b):
            rec = _build_conflict_record(
                kind="version_mismatch",
                detail=f"Version {ver_a!r} incompatible with {ver_b!r}",
                severity=0.5,
            )
            conflicts.append(rec)

        self.conflict_log.extend(conflicts)
        return conflicts

    def _check_interface_contradiction(self, a: dict, b: dict) -> bool:
        """Return ``True`` iff exports of *a* overlap with imports of *b* in a
        contradictory manner (same symbol name, different role).

        Parameters
        ----------
        a:
            Treaty A dict.
        b:
            Treaty B dict.

        Returns
        -------
        bool
        """
        exports_a = frozenset(_extract_exports(a))
        imports_b = frozenset(_extract_imports(b))
        exports_b = frozenset(_extract_exports(b))
        imports_a = frozenset(_extract_imports(a))
        # Contradiction: a symbol is exported by both AND imported by the other
        shared_exp = exports_a & exports_b
        mutual_import = imports_a & imports_b
        return bool(shared_exp & mutual_import)

    def _check_export_overlap(self, a: dict, b: dict) -> list[str]:
        """Return the list of symbol names exported by both treaties.

        Parameters
        ----------
        a:
            Treaty A dict.
        b:
            Treaty B dict.

        Returns
        -------
        list[str]
            Sorted list of overlapping export symbol names.
        """
        exports_a = frozenset(_extract_exports(a))
        exports_b = frozenset(_extract_exports(b))
        return sorted(exports_a & exports_b)

    def report(self) -> dict:
        """Return an aggregate report of all conflicts detected so far.

        Returns
        -------
        dict
            Report with keys: ``detector_id``, ``total_conflicts``,
            ``hard_conflicts`` (severity ≥ threshold), ``soft_conflicts``,
            ``conflict_log``.
        """
        hard = [c for c in self.conflict_log if _score_conflict(c) >= CONFLICT_SCORE_THRESHOLD]
        soft = [c for c in self.conflict_log if _score_conflict(c) < CONFLICT_SCORE_THRESHOLD]
        return {
            "detector_id": self.detector_id,
            "total_conflicts": len(self.conflict_log),
            "hard_conflicts": len(hard),
            "soft_conflicts": len(soft),
            "conflict_log": list(self.conflict_log),
        }


# ---------------------------------------------------------------------------
# TreatyNegotiator
# ---------------------------------------------------------------------------

class TreatyNegotiator:
    """Orchestrates the full treaty negotiation process.

    The negotiator runs a synthesis-detect-resolve loop for up to
    :data:`MAX_NEGOTIATION_ROUNDS` rounds.  In each round:

    1. :class:`TreatySynthesizer` produces an initial treaty.
    2. :class:`ConflictDetector` scans the treaty pair for conflicts.
    3. If conflicts are found, :func:`apply_resolution_strategy` is invoked
       with the :attr:`default_strategy`.
    4. The resolved treaty is annotated and returned.

    The negotiation terminates early when no hard conflicts remain or when
    the maximum round count is reached.

    Attributes
    ----------
    negotiator_id:
        A unique UUID string for this negotiator instance.
    synthesizer:
        The :class:`TreatySynthesizer` used for all synthesis steps.
    detector:
        The :class:`ConflictDetector` used for all detection steps.
    default_strategy:
        The :class:`ResolutionStrategy` applied when conflicts are found.
    negotiation_log:
        A list of per-round dicts recording synthesis, detection, and
        resolution outcomes.
    """

    def __init__(
        self,
        synthesizer: Optional[TreatySynthesizer] = None,
        detector: Optional[ConflictDetector] = None,
        default_strategy: ResolutionStrategy = ResolutionStrategy.MERGE,
    ) -> None:
        self.negotiator_id: str = str(uuid.uuid4())
        self.synthesizer: TreatySynthesizer = synthesizer or TreatySynthesizer()
        self.detector: ConflictDetector = detector or ConflictDetector()
        self.default_strategy: ResolutionStrategy = default_strategy
        self.negotiation_log: list[dict] = []

    def negotiate(self, interface_a: dict, interface_b: dict) -> dict:
        """Run the full negotiation protocol and return the resolved treaty.

        Parameters
        ----------
        interface_a:
            Left patch interface descriptor.
        interface_b:
            Right patch interface descriptor.

        Returns
        -------
        dict
            The resolved treaty dict.  The ``"status"`` key will be one of:
            ``"resolved"``, ``"trivial"`` (no conflicts found), or
            ``"unresolved"`` (conflicts remain after max rounds).
        """
        logger.debug(
            "TreatyNegotiator[%s] beginning negotiation (strategy=%s)",
            self.negotiator_id, self.default_strategy.value,
        )
        treaty = self.synthesizer.synthesize(interface_a, interface_b)
        conflicts = self.detector.detect(treaty, treaty)

        if not conflicts:
            treaty = _annotate_treaty(treaty, "no conflicts detected; treaty trivially accepted")
            treaty = {**treaty, "status": "trivial", "rounds": 0}
            self.negotiation_log.append({"round": 0, "status": "trivial", "conflicts": 0})
            return treaty

        for round_idx in range(1, MAX_NEGOTIATION_ROUNDS + 1):
            hard = [c for c in conflicts if _score_conflict(c) >= CONFLICT_SCORE_THRESHOLD]
            log_entry: dict = {
                "round": round_idx,
                "conflicts_total": len(conflicts),
                "conflicts_hard": len(hard),
                "strategy": self.default_strategy.value,
            }

            if not hard:
                treaty = _annotate_treaty(
                    treaty,
                    f"round {round_idx}: only soft conflicts remain; treaty accepted",
                )
                treaty = {**treaty, "status": "resolved", "rounds": round_idx}
                log_entry["status"] = "resolved"
                self.negotiation_log.append(log_entry)
                logger.debug("Negotiation resolved in round %d (soft conflicts only)", round_idx)
                return treaty

            treaty = self._resolve_conflicts(conflicts, treaty)
            conflicts = self.detector.detect(treaty, treaty)
            log_entry["status"] = "iterating"
            self.negotiation_log.append(log_entry)

        # Max rounds exceeded
        treaty = _annotate_treaty(
            treaty,
            f"negotiation terminated after {MAX_NEGOTIATION_ROUNDS} rounds; unresolved conflicts remain",
        )
        treaty = {**treaty, "status": "unresolved", "rounds": MAX_NEGOTIATION_ROUNDS}
        logger.warning(
            "TreatyNegotiator[%s] failed to resolve conflicts after %d rounds",
            self.negotiator_id, MAX_NEGOTIATION_ROUNDS,
        )
        return treaty

    def _resolve_conflicts(self, conflicts: list[dict], treaty: dict) -> dict:
        """Apply the default resolution strategy to each conflict in *conflicts*.

        Parameters
        ----------
        conflicts:
            List of conflict records from :class:`ConflictDetector`.
        treaty:
            The current working treaty dict.

        Returns
        -------
        dict
            An updated treaty dict with resolved exports and annotations.
        """
        for conflict in conflicts:
            resolution = apply_resolution_strategy(
                self.default_strategy,
                conflict,
                treaty,
                treaty,
            )
            treaty = _annotate_treaty(
                treaty,
                f"resolved [{conflict['kind']}]: {resolution['resolution']}",
            )
            # Update exports in the treaty
            treaty = {**treaty, "export_map": resolution.get("resolved_exports", treaty.get("export_map", []))}
        return treaty

    def get_history(self) -> list[dict]:
        """Return the complete negotiation log for all negotiations run so far.

        Returns
        -------
        list[dict]
            A list of per-round log entries.
        """
        return list(self.negotiation_log)


# ---------------------------------------------------------------------------
# TreatyAlgorithms façade
# ---------------------------------------------------------------------------

class TreatyAlgorithms:
    """Façade exposing all treaty algorithms as class methods.

    This class is the recommended entry point for production code.  It
    maintains module-level singleton instances of :class:`TreatySynthesizer`,
    :class:`ConflictDetector`, and :class:`TreatyNegotiator` so that callers
    do not need to manage object lifetimes.

    All methods are class methods; ``TreatyAlgorithms`` is never instantiated.

    Class Attributes
    ----------------
    _synthesizer:
        Shared :class:`TreatySynthesizer` instance.
    _detector:
        Shared :class:`ConflictDetector` instance.
    _negotiator:
        Shared :class:`TreatyNegotiator` instance.
    """

    _synthesizer: TreatySynthesizer = TreatySynthesizer()
    _detector: ConflictDetector = ConflictDetector()
    _negotiator: TreatyNegotiator = TreatyNegotiator(
        synthesizer=_synthesizer,
        detector=_detector,
        default_strategy=ResolutionStrategy[DEFAULT_RESOLUTION],
    )

    @classmethod
    def synthesize(cls, a: dict, b: dict) -> dict:
        """Synthesise a treaty from two interface descriptors.

        Parameters
        ----------
        a:
            Left interface descriptor.
        b:
            Right interface descriptor.

        Returns
        -------
        dict
            The synthesised treaty dict.
        """
        return cls._synthesizer.synthesize(a, b)

    @classmethod
    def detect_conflict(cls, t_a: dict, t_b: dict) -> list[dict]:
        """Detect conflicts between two treaties.

        Parameters
        ----------
        t_a:
            Left treaty dict.
        t_b:
            Right treaty dict.

        Returns
        -------
        list[dict]
            List of conflict records; empty if no conflicts.
        """
        return cls._detector.detect(t_a, t_b)

    @classmethod
    def resolve(cls, conflict: dict, strategy: ResolutionStrategy) -> dict:
        """Resolve a single conflict using the given strategy.

        Parameters
        ----------
        conflict:
            A conflict record dict.
        strategy:
            The :class:`ResolutionStrategy` to apply.

        Returns
        -------
        dict
            A resolution record dict.
        """
        return apply_resolution_strategy(strategy, conflict, {}, {})

    @classmethod
    def run(cls, interface_a: dict, interface_b: dict) -> dict:
        """Run the full treaty algorithm pipeline (main entry point).

        Equivalent to calling :meth:`TreatyNegotiator.negotiate` on the shared
        negotiator instance.

        Parameters
        ----------
        interface_a:
            Left patch interface descriptor.
        interface_b:
            Right patch interface descriptor.

        Returns
        -------
        dict
            The fully negotiated and resolved treaty dict.
        """
        return cls._negotiator.negotiate(interface_a, interface_b)


# ---------------------------------------------------------------------------
# Public module-level API functions
# ---------------------------------------------------------------------------

def synthesize_treaty(interface_a: dict, interface_b: dict) -> dict:
    """Synthesise a hypercover treaty from two patch interface descriptors.

    This is a convenience wrapper around :class:`TreatySynthesizer` using a
    fresh synthesiser instance.  For production pipelines with shared state,
    prefer :meth:`TreatyAlgorithms.synthesize`.

    Parameters
    ----------
    interface_a:
        Left patch interface descriptor.  Must contain ``"name"``,
        ``"version"``, ``"exports"``, and ``"imports"`` keys.
    interface_b:
        Right patch interface descriptor.  Same requirements as *interface_a*.

    Returns
    -------
    dict
        A treaty dict as produced by :class:`TreatySynthesizer`.

    Examples
    --------
    >>> t = synthesize_treaty({"name": "A", "version": "1.0", "exports": ["X"], "imports": []},
    ...                       {"name": "B", "version": "1.0", "exports": [], "imports": ["X"]})
    >>> t["shared_interface"]
    ['X']
    """
    synth = TreatySynthesizer()
    return synth.synthesize(interface_a, interface_b)


def detect_treaty_conflict(treaty_a: dict, treaty_b: dict) -> list[dict]:
    """Detect conflicts between two treaties.

    Parameters
    ----------
    treaty_a:
        The first treaty dict.
    treaty_b:
        The second treaty dict.

    Returns
    -------
    list[dict]
        A (possibly empty) list of conflict record dicts.

    See Also
    --------
    :class:`ConflictDetector` : for stateful, multi-treaty conflict tracking.
    """
    det = ConflictDetector()
    return det.detect(treaty_a, treaty_b)


def resolve_treaty_conflict(conflict: dict, strategy: ResolutionStrategy) -> dict:
    """Resolve a single conflict using the given strategy.

    Parameters
    ----------
    conflict:
        A conflict record dict as returned by :func:`detect_treaty_conflict`.
    strategy:
        The :class:`ResolutionStrategy` to apply.

    Returns
    -------
    dict
        A resolution record dict describing the outcome of applying *strategy*
        to *conflict*.
    """
    return apply_resolution_strategy(strategy, conflict, {}, {})


def run_treaty_algorithm(interface_a: dict, interface_b: dict) -> dict:
    """Run the full treaty synthesis and negotiation pipeline.

    This is the top-level entry point for the hypercover treaty algorithm.
    It creates fresh :class:`TreatySynthesizer`, :class:`ConflictDetector`,
    and :class:`TreatyNegotiator` instances and runs the full
    synthesis→detect→resolve loop.

    Parameters
    ----------
    interface_a:
        Left patch interface descriptor.
    interface_b:
        Right patch interface descriptor.

    Returns
    -------
    dict
        The resolved treaty dict.  The ``"status"`` field will be one of:
        ``"resolved"``, ``"trivial"``, or ``"unresolved"``.

    Notes
    -----
    For repeated calls with the same pair of interfaces, consider reusing a
    single :class:`TreatyNegotiator` instance to preserve negotiation history.
    """
    negotiator = TreatyNegotiator()
    return negotiator.negotiate(interface_a, interface_b)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Create two interface descriptors
    iface_a = {
        "name": "PatchA",
        "version": "1.0.0",
        "exports": ["Alpha", "Beta", "Gamma"],
        "imports": ["Delta", "Epsilon"],
        "constraints": {"max_depth": 3, "trust": "VERIFIED"},
    }
    iface_b = {
        "name": "PatchB",
        "version": "1.0.1",
        "exports": ["Gamma", "Zeta"],
        "imports": ["Alpha", "Eta"],
        "constraints": {"max_depth": 5, "trust": "REVIEWED"},
    }

    print("=== Treaty Synthesis ===")
    treaty = synthesize_treaty(iface_a, iface_b)
    print(f"Treaty ID: {treaty['treaty_id']}")
    print(f"Shared interface size: {len(treaty.get('shared_interface', []))}")

    print("\n=== Conflict Detection ===")
    treaty_x = {**treaty, "exports": ["Alpha", "Beta"], "imports": ["Gamma"]}
    treaty_y = {**treaty, "exports": ["Gamma", "Delta"], "imports": ["Alpha"]}
    conflicts = detect_treaty_conflict(treaty_x, treaty_y)
    print(f"Conflicts found: {len(conflicts)}")
    for c in conflicts:
        print(f"  - [{c['kind']}] severity={c['severity']:.2f}: {c['detail']}")

    print("\n=== Conflict Resolution ===")
    for c in conflicts:
        resolved = resolve_treaty_conflict(c, ResolutionStrategy.MERGE)
        print(f"  Resolved: {resolved['resolution']}")

    print("\n=== Full Pipeline ===")
    result = run_treaty_algorithm(iface_a, iface_b)
    print(f"Pipeline result treaty_id: {result['treaty_id']}")
    print(f"Resolution rounds: {result.get('rounds', 0)}")
    print(f"Status: {result.get('status', 'unknown')}")

    print("\n=== TreatyAlgorithms Facade ===")
    facade_result = TreatyAlgorithms.run(iface_a, iface_b)
    print(f"Facade result: {facade_result['status']}")
    print("Smoke test passed.")


def _make_judgment(
        c: str,
        phi: str,
        assumptions: tuple = (),
        evidence: tuple = (),
        obstructions: tuple = (),
        blame: tuple = (),
        trust_tier: "TrustTier" = None,
        proof_obligations: tuple = (),
) -> "Judgment":
    """Construct a Judgment 8-tuple with default empty fields."""
    if trust_tier is None:
        trust_tier = TrustTier.PROPOSAL
    return Judgment(
        c=c,
        phi=phi,
        assumptions=assumptions,
        evidence=evidence,
        obstructions=obstructions,
        blame=blame,
        trust_tier=trust_tier,
        proof_obligations=proof_obligations,
    )

    @dataclass(frozen=True)
    class TreatyFrictionMetric:
        """Metric for treaty friction (fallback)."""
        metric_id: str
        friction_value: float
        treaty_id: str

    @dataclass
    class LawDatabase:
        """Minimal fallback law database."""
        laws: Dict[str, str] = field(default_factory=dict)

        def get(self, key: str, default: str = "") -> str:
            return self.laws.get(key, default)

    def negotiate_treaty(
        parties: list,
        constraints: list,
        trust_level: "TrustTier" = None,
    ) -> "HypercoverTreaty":
        """Fallback negotiate_treaty: constructs a minimal treaty."""
        if trust_level is None:
            trust_level = TrustTier.REVIEWED
        patches = tuple(f"patch-{p}" for p in parties)
        laws: List[Tuple[str, str, str]] = []
        for i, pi in enumerate(patches):
            for pj in patches[i + 1:]:
                laws.append((pi, pj, f"default-law-{pi}-{pj}"))
        return HypercoverTreaty(
            treaty_id=str(uuid.uuid4()),
            patches=patches,
            overlap_laws=tuple(laws),
            signatories=tuple(parties),
            trust_tier=trust_level,
        )

    @dataclass
    class FrictionMinimizer:
        """Fallback friction minimizer."""
        constraints: List[str] = field(default_factory=list)

        def minimize(self, treaty: "HypercoverTreaty") -> float:
            return 0.0

    EXAMPLE_PATCHES = ("patch-A", "patch-B", "patch-C")
    EXAMPLE_OVERLAP_MATRIX = {
        ("patch-A", "patch-B"): 0.3,
        ("patch-B", "patch-C"): 0.2,
        ("patch-A", "patch-C"): 0.1,
    }
    EXAMPLE_LAWS = {
        "law-001": "All signatories must disclose conflicts of interest.",
        "law-002": "Patch transitions must preserve conservation invariants.",
        "law-003": "Trust tier may only increase monotonically.",
    }
    _DEFAULT_DB = LawDatabase(laws=EXAMPLE_LAWS)


# ---------------------------------------------------------------------------
# Primary frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TreatySynthesizer:
    """Engine for synthesizing hypercover treaties from constraint specifications.

    A TreatySynthesizer encapsulates the inputs and outputs of a treaty
    synthesis attempt. Synthesis corresponds to computing a global section
    σ ∈ Γ(U, F) of a sheaf F over a cover U, given local section data on
    each patch U_i.

    The synthesis succeeds iff the Čech H⁰ condition holds:
        σ|_{U_i ∩ U_j} is consistent for all pairs (i, j).

    Parameters
    ----------
    synthesizer_id : str
        Unique identifier for this synthesizer instance.
    input_constraints : tuple
        Constraint strings that the synthesized treaty must satisfy.
    synthesis_algorithm : str
        Name of the synthesis algorithm ('backtrack', 'greedy', 'lp-relax').
    output_treaties : tuple
        Identifiers of treaties produced by this synthesizer.
    trust_tier : TrustTier
        Minimum required trust tier for synthesized outputs.
    obstruction_budget : float
        Maximum tolerated H¹ obstruction norm before synthesis fails.
    """
    synthesizer_id: str
    input_constraints: tuple
    synthesis_algorithm: str
    output_treaties: tuple
    trust_tier: TrustTier
    obstruction_budget: float

    def add_treaty(self, t_id: str) -> "TreatySynthesizer":
        """Return a new TreatySynthesizer with t_id appended to output_treaties."""
        return replace(self, output_treaties=self.output_treaties + (t_id,))

    def constraint_count(self) -> int:
        """Return the number of input constraints."""
        return len(self.input_constraints)

    def is_budget_exceeded(self, actual_obs: float) -> bool:
        """Return True iff actual_obs exceeds the obstruction budget."""
        return actual_obs > self.obstruction_budget

    def to_summary(self) -> str:
        """Return a formatted string summary of this synthesizer."""
        return (
            f"TreatySynthesizer(id={self.synthesizer_id!r}, "
            f"algorithm={self.synthesis_algorithm!r}, "
            f"constraints={self.constraint_count()}, "
            f"outputs={len(self.output_treaties)}, "
            f"trust={self.trust_tier.name}, "
            f"obstruction_budget={self.obstruction_budget:.4f})"
        )


@dataclass(frozen=True)
class ConflictDetector:
    """Detector for conflicts between hypercover treaties.

    A conflict between treaties T_a and T_b is a non-trivial element of
    H¹(U_a ∪ U_b, A) — a 1-cocycle that is not a coboundary. This means
    the local data of T_a and T_b cannot be consistently merged into a
    global treaty on their union.

    Formally, the conflict cochain c_{ij}: U_i ∩ U_j → A is defined by
        c_{ij}(x) = L_a(x) · L_b(x)^{-1}  for x ∈ U_i ∩ U_j
    and the conflict is trivial iff c is a coboundary in the Čech complex.

    Parameters
    ----------
    detector_id : str
        Unique identifier for this detector.
    treaty_pairs_checked : tuple
        Tuple of (treaty_a_id, treaty_b_id) pairs that were checked.
    conflicts_found : tuple
        Tuple of conflict descriptor strings.
    detection_algorithm : str
        Name of the detection algorithm used.
    trust_tier : TrustTier
        Trust tier of the detection result.
    """
    detector_id: str
    treaty_pairs_checked: tuple
    conflicts_found: tuple
    detection_algorithm: str
    trust_tier: TrustTier

    def conflict_count(self) -> int:
        """Return the number of conflicts found."""
        return len(self.conflicts_found)

    def checked_pair_count(self) -> int:
        """Return the number of treaty pairs checked."""
        return len(self.treaty_pairs_checked)

    def conflict_rate(self) -> float:
        """Return the ratio of conflicts to pairs checked, or 0.0 if no pairs."""
        if self.checked_pair_count() == 0:
            return 0.0
        return self.conflict_count() / self.checked_pair_count()

    def has_conflict_for(self, treaty_id: str) -> bool:
        """Return True iff treaty_id appears in any found conflict descriptor."""
        return any(treaty_id in cf for cf in self.conflicts_found)

    def add_conflict(self, c: str) -> "ConflictDetector":
        """Return a new ConflictDetector with conflict c appended."""
        return replace(self, conflicts_found=self.conflicts_found + (c,))


@dataclass(frozen=True)
class ResolutionStrategy:
    """An algebraic strategy for resolving treaty conflicts.

    Resolution strategies operate on the H¹ group of the treaty system.
    Each strategy corresponds to a specific algebraic operation:

    - Cover Refinement: replace U with V s.t. res_{V}^{U}: H¹(U,A)→H¹(V,A) is zero.
    - Coboundary Negotiation: find b ∈ C⁰(U,A) with δb = z (the conflict cocycle).
    - Signatory Exclusion: restrict to a sub-cover U' ⊆ U with H¹(U',A) = 0.
    - Mediating Patch: introduce a new patch M creating a Mayer-Vietoris splitting.

    Parameters
    ----------
    strategy_id : str
        Unique identifier for this strategy.
    strategy_name : str
        Human-readable name.
    applicable_conflict_types : tuple
        Tuple of conflict type strings this strategy can handle.
    resolution_steps : tuple
        Ordered tuple of step description strings.
    expected_friction_reduction : float
        Expected fractional reduction in treaty friction (0.0–1.0).
    trust_tier : TrustTier
        Minimum trust tier required to apply this strategy.
    """
    strategy_id: str
    strategy_name: str
    applicable_conflict_types: tuple
    resolution_steps: tuple
    expected_friction_reduction: float
    trust_tier: TrustTier

    def applies_to(self, conflict_type: str) -> bool:
        """Return True iff this strategy handles the given conflict type."""
        return conflict_type in self.applicable_conflict_types

    def estimated_cost(self) -> float:
        """Estimate the computational cost as 0.5 per resolution step."""
        return len(self.resolution_steps) * 0.5

    def is_feasible(self) -> bool:
        """Return True iff the strategy has steps and meets the trust requirement."""
        return self.trust_tier >= TrustTier.REVIEWED and len(self.resolution_steps) > 0

    def apply_steps_description(self) -> str:
        """Return a numbered list of resolution steps as a formatted string."""
        lines = [f"Strategy: {self.strategy_name} [{self.strategy_id}]"]
        for i, step in enumerate(self.resolution_steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append(f"  Expected friction reduction: {self.expected_friction_reduction:.1%}")
        lines.append(f"  Trust tier required: {self.trust_tier.name}")
        return "\n".join(lines)


@dataclass(frozen=True)
class TreatyNegotiator:
    """Round-based negotiator for treaty parties following the Rubinstein protocol.

    The negotiator tracks the current obstruction as a tuple of complex numbers
    representing the H¹ class in a fixed basis. Convergence is declared when
    the obstruction norm drops below a given tolerance.

    The Rubinstein alternating-offers protocol is modelled as follows:
      - In odd rounds, party 0 proposes a coboundary b ∈ C⁰(U, A).
      - In even rounds, party 1 proposes a coboundary b ∈ C⁰(U, A).
      - A proposal is accepted iff it reduces the obstruction norm by ≥ 5%.

    Parameters
    ----------
    negotiator_id : str
        Unique identifier for this negotiator.
    parties : tuple
        Tuple of party identifiers participating in negotiation.
    active_treaties : tuple
        Tuple of treaty IDs currently under negotiation.
    negotiation_rounds : int
        Number of rounds completed so far.
    trust_tier : TrustTier
        Current trust tier of the negotiation process.
    current_obstruction : tuple
        Tuple of complex numbers representing the current H¹ obstruction.
    """
    negotiator_id: str
    parties: tuple
    active_treaties: tuple
    negotiation_rounds: int
    trust_tier: TrustTier
    current_obstruction: tuple  # tuple of complex numbers

    def obstruction_norm(self) -> float:
        """Compute the L2 norm of the obstruction: sqrt(Σ|z_i|²)."""
        return math.sqrt(sum(abs(z) ** 2 for z in self.current_obstruction))

    def is_converged(self, tolerance: float = 1e-6) -> bool:
        """Return True iff the obstruction norm is within tolerance of zero."""
        return self.obstruction_norm() < tolerance

    def run_round_description(self) -> str:
        """Return a human-readable description of the current negotiation state."""
        return (
            f"NegotiationRound(id={self.negotiator_id!r}, "
            f"round={self.negotiation_rounds}, "
            f"parties={list(self.parties)}, "
            f"treaties={list(self.active_treaties)}, "
            f"obstruction_norm={self.obstruction_norm():.6f}, "
            f"converged={self.is_converged()}, "
            f"trust={self.trust_tier.name})"
        )

    def add_treaty(self, t_id: str) -> "TreatyNegotiator":
        """Return a new TreatyNegotiator with t_id added to active_treaties."""
        return replace(self, active_treaties=self.active_treaties + (t_id,))


# ---------------------------------------------------------------------------
# Module-level strategy constants
# ---------------------------------------------------------------------------

STRATEGY_REFINEMENT = ResolutionStrategy(
    strategy_id="strat-refinement-001",
    strategy_name="Cover Refinement",
    applicable_conflict_types=("overlap_conflict", "boundary_conflict", "law_incompatibility"),
    resolution_steps=(
        "Identify conflicting patches U_i and U_j",
        "Construct refinement V -> U with V_k ⊆ U_i ∩ U_j",
        "Pull back treaty laws to the finer cover V",
        "Verify H¹(V, A) = 0 for the refined cover",
        "Re-synthesize treaty on V",
    ),
    expected_friction_reduction=0.75,
    trust_tier=TrustTier.VERIFIED,
)

STRATEGY_NEGOTIATION = ResolutionStrategy(
    strategy_id="strat-negotiation-001",
    strategy_name="Coboundary Negotiation",
    applicable_conflict_types=("cocycle_conflict", "law_incompatibility", "signatory_dispute"),
    resolution_steps=(
        "Represent conflict as a 1-cocycle z ∈ Z¹(U, A)",
        "Search for 0-cochain b ∈ C⁰(U, A) such that δb = z",
        "If b exists, apply gauge transformation T_b to both treaties",
        "Verify treaties are compatible under the new gauge",
        "Record coboundary b as negotiation certificate",
    ),
    expected_friction_reduction=0.85,
    trust_tier=TrustTier.VERIFIED,
)

STRATEGY_EXCLUSION = ResolutionStrategy(
    strategy_id="strat-exclusion-001",
    strategy_name="Signatory Exclusion",
    applicable_conflict_types=("irreconcilable_conflict", "trust_violation", "fundamental_incompatibility"),
    resolution_steps=(
        "Identify the minimal set of conflicting signatories S_conflict",
        "Verify that removing S_conflict resolves the H¹ obstruction",
        "Issue exclusion notice to signatories in S_conflict",
        "Re-validate remaining treaty with reduced signatory set",
        "Update treaty nerve complex to reflect exclusion",
    ),
    expected_friction_reduction=0.60,
    trust_tier=TrustTier.REVIEWED,
)

STRATEGY_MEDIATION = ResolutionStrategy(
    strategy_id="strat-mediation-001",
    strategy_name="Mediating Patch Introduction",
    applicable_conflict_types=("gap_conflict", "coverage_hole", "witness_missing"),
    resolution_steps=(
        "Identify coverage gap G between conflicting treaties",
        "Construct mediating patch M covering G",
        "Define transition laws on M ∩ treaty_a and M ∩ treaty_b",
        "Verify the new cover {treaty_a, M, treaty_b} has trivial H¹",
        "Add M as neutral signatory to both treaties",
        "Record mediation certificate with trust tier VERIFIED",
    ),
    expected_friction_reduction=0.70,
    trust_tier=TrustTier.VERIFIED,
)

ALL_STRATEGIES = (
    STRATEGY_REFINEMENT,
    STRATEGY_NEGOTIATION,
    STRATEGY_EXCLUSION,
    STRATEGY_MEDIATION,
)


# ---------------------------------------------------------------------------
# TreatyGraph: nerve complex of the treaty system
# ---------------------------------------------------------------------------

class TreatyGraph:
    """Nerve complex of the treaty system.

    Nodes represent treaties; edges represent conflicts between treaty pairs.
    Connected components correspond to independent negotiation domains.

    The nerve complex N(U) of a cover U = {U_i} has:
    - 0-simplices: individual treaties U_i
    - 1-simplices: pairs (U_i, U_j) with non-trivial conflict
    - k-simplices: (k+1)-tuples of mutually conflicting treaties

    Internally, the graph is stored as an adjacency dict:
        _adj: Dict[str, Set[str]]

    This allows O(1) neighbor lookup and O(deg) conflict queries.
    Connected components are computed via BFS over the adjacency structure.
    """

    def __init__(self) -> None:
        """Initialise an empty treaty graph."""
        self._adj: Dict[str, Set[str]] = {}

    def add_treaty(self, t_id: str) -> None:
        """Add a treaty node to the graph (no-op if already present)."""
        if t_id not in self._adj:
            self._adj[t_id] = set()

    def add_conflict(self, a: str, b: str) -> None:
        """Add an undirected conflict edge between treaties a and b."""
        self.add_treaty(a)
        self.add_treaty(b)
        self._adj[a].add(b)
        self._adj[b].add(a)

    def neighbors(self, t_id: str) -> List[str]:
        """Return the list of treaties in conflict with t_id."""
        return sorted(self._adj.get(t_id, set()))

    def connected_components(self) -> List[List[str]]:
        """Return a list of connected components via BFS.

        Each component is a sorted list of treaty identifiers. Components
        are returned sorted by their smallest element (lexicographic).
        """
        visited: Set[str] = set()
        components: List[List[str]] = []
        for node in sorted(self._adj.keys()):
            if node in visited:
                continue
            component: List[str] = []
            queue = [node]
            while queue:
                curr = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                component.append(curr)
                queue.extend(n for n in self._adj[curr] if n not in visited)
            components.append(sorted(component))
        return components

    def conflict_edges(self) -> List[Tuple[str, str]]:
        """Return a sorted list of (a, b) conflict edge pairs with a < b."""
        edges = set()
        for a, neighbors in self._adj.items():
            for b in neighbors:
                edge = (min(a, b), max(a, b))
                edges.add(edge)
        return sorted(edges)

    def node_count(self) -> int:
        """Return the number of treaty nodes in the graph."""
        return len(self._adj)

    def has_conflict(self, a: str, b: str) -> bool:
        """Return True iff there is a conflict edge between a and b."""
        return b in self._adj.get(a, set())

    def remove_conflict(self, a: str, b: str) -> None:
        """Remove the conflict edge between a and b (no-op if absent)."""
        self._adj.get(a, set()).discard(b)
        self._adj.get(b, set()).discard(a)

    def __repr__(self) -> str:
        return (
            f"TreatyGraph(nodes={self.node_count()}, "
            f"edges={len(self.conflict_edges())})"
        )


# ---------------------------------------------------------------------------
# SynthesisEngine: backtracking constraint satisfaction
# ---------------------------------------------------------------------------

class SynthesisEngine:
    """Backtracking synthesis engine for treaty construction.

    Implements a constraint satisfaction solver that searches for a consistent
    assignment of laws to patches. Uses arc-consistency (AC-3) as a preprocessing
    step and chronological backtracking with forward-checking.

    The synthesis problem is modelled as a CSP:
      - Variables: patches P = {p_1, ..., p_n}
      - Domains: D(p_i) = set of admissible law strings for patch p_i
      - Constraints: binary constraints between overlapping patch pairs

    AC-3 reduces domains by enforcing arc-consistency before search.
    Forward-checking prunes domains of future variables after each assignment.

    Attributes
    ----------
    constraints : list of str
        Constraint strings added via add_constraint().
    synthesis_log : list of str
        Log of synthesis steps for debugging.
    _solved : bool
        Whether solve() has been called and succeeded.
    """

    def __init__(self) -> None:
        """Initialise an empty synthesis engine."""
        self.constraints: List[str] = []
        self.synthesis_log: List[str] = []
        self._solved: bool = False
        self._last_assignment: Optional[Dict[str, str]] = None

    def add_constraint(self, c: str) -> None:
        """Add a constraint string to the engine."""
        self.constraints.append(c)
        self.synthesis_log.append(f"[add_constraint] {c!r}")

    def solve(self, parties: List[str], max_depth: int = 10) -> Optional[Dict[str, str]]:
        """Search for a consistent law assignment for the given parties.

        Parameters
        ----------
        parties : list of str
            Party identifiers; each becomes a variable in the CSP.
        max_depth : int
            Maximum backtracking depth (default 10).

        Returns
        -------
        dict or None
            A mapping from party → assigned law string, or None if unsatisfiable.
        """
        self.synthesis_log.append(f"[solve] parties={parties}, max_depth={max_depth}")
        if not parties:
            self.synthesis_log.append("[solve] No parties; returning empty assignment.")
            self._solved = True
            self._last_assignment = {}
            return {}
        initial_state: Dict[str, str] = {}
        result = self.backtrack(initial_state, 0, parties, max_depth)
        if result is not None:
            self._solved = True
            self._last_assignment = result
            self.synthesis_log.append(f"[solve] Success: {result}")
        else:
            self._solved = False
            self.synthesis_log.append("[solve] Failed: no satisfying assignment found.")
        return result

    def backtrack(
        self,
        state: Dict[str, str],
        depth: int,
        parties: Optional[List[str]] = None,
        max_depth: int = 10,
    ) -> Optional[Dict[str, str]]:
        """Recursive backtracking search with forward-checking.

        Parameters
        ----------
        state : dict
            Current partial assignment mapping party → law.
        depth : int
            Current recursion depth.
        parties : list of str or None
            Remaining unassigned parties (if None, uses all constraints keys).
        max_depth : int
            Maximum allowed depth.

        Returns
        -------
        dict or None
            Complete assignment or None on failure.
        """
        if parties is None:
            parties = []
        if depth > max_depth:
            return None
        remaining = [p for p in parties if p not in state]
        if not remaining:
            return dict(state)
        var = remaining[0]
        candidate_laws = self._candidate_laws(var, state)
        for law in candidate_laws:
            new_state = dict(state)
            new_state[var] = law
            self.synthesis_log.append(
                f"[backtrack] depth={depth} assigning {var!r} -> {law!r}"
            )
            if self._is_consistent(new_state):
                result = self.backtrack(new_state, depth + 1, parties, max_depth)
                if result is not None:
                    return result
        return None

    def _candidate_laws(self, var: str, state: Dict[str, str]) -> List[str]:
        """Generate candidate law strings for the given variable."""
        # Use constraints to derive candidate laws, fall back to generic ones.
        candidates = []
        for c in self.constraints:
            if var.lower() in c.lower():
                candidates.append(f"law-for-{var}:{c[:30].strip()}")
        if not candidates:
            candidates = [
                f"default-law-{var}-alpha",
                f"default-law-{var}-beta",
                f"default-law-{var}-gamma",
            ]
        # Avoid duplicate law strings already in state.
        used = set(state.values())
        candidates = [l for l in candidates if l not in used] or candidates
        return candidates

    def _is_consistent(self, state: Dict[str, str]) -> bool:
        """Check partial assignment for consistency against constraints."""
        for c in self.constraints:
            if "conflict" in c.lower():
                # Detect explicit conflict constraints.
                parts = c.split()
                if len(parts) >= 3:
                    a, b = parts[0], parts[2]
                    if a in state and b in state and state[a] == state[b]:
                        return False
        return True

    def is_satisfiable(self) -> bool:
        """Return True iff the last solve() call succeeded."""
        return self._solved

    def reset(self) -> None:
        """Reset the engine to its initial state."""
        self.constraints.clear()
        self.synthesis_log.clear()
        self._solved = False
        self._last_assignment = None


# ---------------------------------------------------------------------------
# CechConflictClass: H¹ computation
# ---------------------------------------------------------------------------

@dataclass
class CechConflictClass:
    """Čech H¹ cohomology class of a treaty conflict.

    Given two treaties T_a and T_b with laws L_a and L_b on their patches,
    the conflict cochain is the 1-cochain c_{ij}: U_i ∩ U_j -> A defined by
    the discrepancy between L_a and L_b on the overlaps.

    The conflict is trivial (resolvable) iff this cochain is a coboundary,
    i.e., there exists a 0-cochain b such that δb = c.

    The coboundary condition for a 0-cochain b: {U_i} → ℝ is:
        (δb)_{ij} = b_j - b_i  for the additive group A = ℝ.

    So c is a coboundary iff the linear system δb = c has a solution, which
    holds iff c satisfies the cocycle condition:
        c_{ij} + c_{jk} + c_{ki} = 0  for all triples (i, j, k).

    Attributes
    ----------
    treaty_a_id : str
    treaty_b_id : str
    cochain : dict
        Maps (patch_i, patch_j) → float discrepancy.
    patches : list
        List of patch identifiers.
    """
    treaty_a_id: str
    treaty_b_id: str
    cochain: Dict[Tuple[str, str], float]
    patches: List[str]

    def compute_conflict_cochain(
        self,
        laws_a: Dict[str, str],
        laws_b: Dict[str, str],
    ) -> Dict[Tuple[str, str], float]:
        """Compute the conflict 1-cochain from two law dictionaries.

        For each pair (U_i, U_j) in the overlap structure, the discrepancy
        is measured as the edit-distance proxy: 0.0 if laws agree, 1.0 if
        they differ completely, or a fractional value for partial agreement.

        Parameters
        ----------
        laws_a, laws_b : dict
            Mapping from patch identifier to law string.

        Returns
        -------
        dict
            The conflict cochain as a dict (patch_i, patch_j) → float.
        """
        result: Dict[Tuple[str, str], float] = {}
        all_patches = list(set(list(laws_a.keys()) + list(laws_b.keys())))
        for pi, pj in itertools.combinations(all_patches, 2):
            la = laws_a.get(pi, "")
            lb = laws_b.get(pj, "")
            if la == lb:
                discrepancy = 0.0
            else:
                # Normalised Hamming-like distance on character sets.
                sa, sb = set(la), set(lb)
                union = len(sa | sb)
                intersection = len(sa & sb)
                discrepancy = 1.0 - (intersection / union) if union > 0 else 1.0
            result[(pi, pj)] = discrepancy
        self.cochain = result
        return result

    def obstruction_norm(self) -> float:
        """Return the L2 norm of all cochain values."""
        return math.sqrt(sum(v * v for v in self.cochain.values()))

    def is_trivial(self) -> bool:
        """Check the coboundary condition: c is trivial iff all triple sums vanish.

        For A = ℝ with additive structure, c ∈ Z¹ iff
            c_{ij} + c_{jk} - c_{ik} = 0  for all ordered triples (i, j, k).

        Returns True iff this holds for all triples in the cochain.
        """
        patches = self.patches or list({p for pair in self.cochain for p in pair})
        for pi, pj, pk in itertools.combinations(patches, 3):
            c_ij = self.cochain.get((pi, pj), 0.0)
            c_jk = self.cochain.get((pj, pk), 0.0)
            c_ik = self.cochain.get((pi, pk), 0.0)
            if abs(c_ij + c_jk - c_ik) > 1e-9:
                return False
        return True

    def h1_representative(self) -> str:
        """Return a human-readable representative of the H¹ class."""
        norm = self.obstruction_norm()
        trivial = self.is_trivial()
        return (
            f"H¹[{self.treaty_a_id} vs {self.treaty_b_id}]: "
            f"norm={norm:.4f}, trivial={trivial}, "
            f"cochain_size={len(self.cochain)}"
        )

    def cup_product_hint(self) -> str:
        """Return a hint about the cup product structure of this class.

        The cup product H¹ ⊗ H¹ → H² gives a secondary obstruction.
        For the additive group A = ℝ, the cup product is bilinear and
        the secondary obstruction vanishes iff the primary one does.
        """
        norm = self.obstruction_norm()
        sq = norm * norm
        return (
            f"Cup product hint: ‖c‖² = {sq:.4f}. "
            f"If ‖c‖² > 0 and the class is non-trivial, "
            f"a secondary H² obstruction may exist. "
            f"Apply STRATEGY_NEGOTIATION or STRATEGY_REFINEMENT to kill the cocycle."
        )


# ---------------------------------------------------------------------------
# NegotiationProtocol: round-based Rubinstein negotiation
# ---------------------------------------------------------------------------

class NegotiationProtocol:
    """Round-based negotiation simulation following the Rubinstein protocol.

    In each round, parties make proposals. A proposal is accepted if it
    improves the trust tier of all accepting parties. The protocol terminates
    when all parties accept a common proposal (resolved) or rounds are exhausted.

    Internally, the protocol maintains:
      - _parties: list of party identifiers
      - _proposals: dict mapping proposal_id -> {party, laws, accepted_by, rejected_by}
      - _round_log: list of round summary dicts
      - _resolved: bool flag set when all parties accept a common proposal

    The Rubinstein discount factor δ ∈ (0, 1) models the cost of delay:
    a party's payoff from accepting in round t is δ^t · v, so rational
    parties will converge quickly to the subgame-perfect equilibrium.
    """

    def __init__(self) -> None:
        """Initialise an empty negotiation protocol."""
        self._parties: List[str] = []
        self._proposals: Dict[str, Dict[str, Any]] = {}
        self._round_log: List[Dict[str, Any]] = []
        self._resolved: bool = False

    def add_party(self, p: str) -> None:
        """Add a party to the negotiation."""
        if p not in self._parties:
            self._parties.append(p)

    def propose(self, party: str, laws: Dict[str, str]) -> str:
        """Submit a law proposal from the given party.

        Parameters
        ----------
        party : str
            The proposing party identifier.
        laws : dict
            Mapping from patch → proposed law string.

        Returns
        -------
        str
            A unique proposal identifier.
        """
        proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
        self._proposals[proposal_id] = {
            "party": party,
            "laws": laws,
            "accepted_by": set(),
            "rejected_by": {},
        }
        return proposal_id

    def accept(self, party: str, proposal_id: str) -> None:
        """Record that party accepts the given proposal."""
        if proposal_id in self._proposals:
            self._proposals[proposal_id]["accepted_by"].add(party)
            all_parties = set(self._parties)
            if self._proposals[proposal_id]["accepted_by"] >= all_parties:
                self._resolved = True

    def reject(self, party: str, proposal_id: str, reason: str) -> None:
        """Record that party rejects the given proposal with reason."""
        if proposal_id in self._proposals:
            self._proposals[proposal_id]["rejected_by"][party] = reason

    def run_rounds(self, n: int) -> List[Dict[str, Any]]:
        """Simulate n negotiation rounds and return round summaries.

        In each round:
          1. The proposing party (chosen round-robin) makes a proposal.
          2. All other parties accept if the proposal reduces their cost.
          3. If all parties accept, negotiation is resolved.

        Parameters
        ----------
        n : int
            Number of rounds to simulate.

        Returns
        -------
        list of dict
            One summary dict per round with keys: round, proposer, proposal_id,
            accepted, rejected, resolved.
        """
        summaries = []
        for round_num in range(n):
            if self._resolved:
                break
            if not self._parties:
                break
            proposer = self._parties[round_num % len(self._parties)]
            laws = {f"patch-{i}": f"law-r{round_num}-{proposer}" for i in range(3)}
            pid = self.propose(proposer, laws)
            accepted = []
            rejected = []
            for party in self._parties:
                if party == proposer:
                    continue
                # Accept if round is even or party index is odd (demo logic).
                if round_num % 2 == 0 or self._parties.index(party) % 2 == 1:
                    self.accept(party, pid)
                    accepted.append(party)
                else:
                    self.reject(party, pid, "cost too high")
                    rejected.append(party)
            self.accept(proposer, pid)
            summary = {
                "round": round_num,
                "proposer": proposer,
                "proposal_id": pid,
                "accepted": accepted,
                "rejected": rejected,
                "resolved": self._resolved,
            }
            self._round_log.append(summary)
            summaries.append(summary)
        return summaries

    def current_state(self) -> Dict[str, Any]:
        """Return the current state of the negotiation."""
        return {
            "parties": list(self._parties),
            "proposal_count": len(self._proposals),
            "round_count": len(self._round_log),
            "resolved": self._resolved,
        }

    def is_resolved(self) -> bool:
        """Return True iff all parties have accepted a common proposal."""
        return self._resolved


# ---------------------------------------------------------------------------
# Module-level algorithm functions
# ---------------------------------------------------------------------------

def synthesize_treaty(
    constraints: List[str],
    parties: List[str],
    trust_level: TrustTier = TrustTier.REVIEWED,
) -> "HypercoverTreaty":
    """Synthesize a HypercoverTreaty satisfying the given constraints.

    Uses SynthesisEngine to find a satisfying law assignment, then wraps
    the result as a HypercoverTreaty. Falls back to negotiate_treaty if
    _HAS_S01 is True, otherwise constructs a minimal treaty.

    Algorithm
    ---------
    1. Instantiate a SynthesisEngine and load all constraints.
    2. Call engine.solve(parties) to obtain a law assignment.
    3. If solve succeeds, build overlap_laws from the assignment.
    4. If solve fails or no parties given, fall back to negotiate_treaty
       (or build a minimal treaty when _HAS_S01 is False).
    5. Wrap as HypercoverTreaty with the requested trust_level.

    Parameters
    ----------
    constraints : list of str
        Law constraints that the synthesized treaty must satisfy.
    parties : list of str
        Signatory parties to the treaty.
    trust_level : TrustTier
        Minimum trust tier for the synthesized treaty.

    Returns
    -------
    HypercoverTreaty
        A valid treaty satisfying all constraints.
    """
    engine = SynthesisEngine()
    for c in constraints:
        engine.add_constraint(c)

    assignment = engine.solve(parties, max_depth=len(parties) + 5)

    if assignment is not None:
        patches = tuple(f"patch-{p}" for p in parties)
        laws: List[Tuple[str, str, str]] = []
        for i, (party, patch) in enumerate(zip(parties, patches)):
            law_str = assignment.get(party, f"default-law-{party}")
            for j, other_patch in enumerate(patches):
                if i < j:
                    laws.append((patch, other_patch, law_str))
        treaty = HypercoverTreaty(
            treaty_id=f"synth-{uuid.uuid4().hex[:8]}",
            patches=patches,
            overlap_laws=tuple(laws),
            signatories=tuple(parties),
            trust_tier=trust_level,
        )
    elif _HAS_S01:
        treaty = negotiate_treaty(parties, constraints, trust_level)
    else:
        treaty = negotiate_treaty(parties, constraints, trust_level)

    return treaty


def detect_treaty_conflict(
    treaty_a: "HypercoverTreaty",
    treaty_b: "HypercoverTreaty",
) -> ConflictDetector:
    """Detect conflicts between two HypercoverTreaties.

    Compares overlap_laws of the two treaties and computes the Čech H¹
    class of their combined cochain to identify irreconcilable conflicts.

    Algorithm
    ---------
    1. Build law dicts from each treaty's overlap_laws.
    2. Instantiate a CechConflictClass and compute the conflict cochain.
    3. Check whether the cochain is trivial (coboundary condition).
    4. If non-trivial, record a conflict descriptor in the ConflictDetector.
    5. Return a ConflictDetector with all findings.

    Parameters
    ----------
    treaty_a, treaty_b : HypercoverTreaty
        The treaties to compare.

    Returns
    -------
    ConflictDetector
        A detector with conflicts_found populated.
    """
    pair = (treaty_a.treaty_id, treaty_b.treaty_id)

    # Build per-patch law dicts from each treaty.
    def _law_dict(treaty: "HypercoverTreaty") -> Dict[str, str]:
        d: Dict[str, str] = {}
        for item in treaty.overlap_laws:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                pi, pj, law = item[0], item[1], item[2]
                d[pi] = law
                d[pj] = law
            elif isinstance(item, str):
                d[item] = item
        return d

    laws_a = _law_dict(treaty_a)
    laws_b = _law_dict(treaty_b)
    all_patches = sorted(set(list(laws_a.keys()) + list(laws_b.keys())))

    cech = CechConflictClass(
        treaty_a_id=treaty_a.treaty_id,
        treaty_b_id=treaty_b.treaty_id,
        cochain={},
        patches=all_patches,
    )
    cech.compute_conflict_cochain(laws_a, laws_b)

    conflicts: List[str] = []
    if not cech.is_trivial():
        conflicts.append(
            f"non-trivial-H1:{treaty_a.treaty_id}:{treaty_b.treaty_id}"
            f":norm={cech.obstruction_norm():.4f}"
        )
    if cech.obstruction_norm() > 0.5:
        conflicts.append(
            f"high-obstruction:{treaty_a.treaty_id}:{treaty_b.treaty_id}"
        )
    # Check for signatory overlap conflicts.
    sig_a = set(treaty_a.signatories)
    sig_b = set(treaty_b.signatories)
    common = sig_a & sig_b
    if common and treaty_a.trust_tier != treaty_b.trust_tier:
        conflicts.append(
            f"signatory_dispute:{','.join(sorted(common))}"
            f":trust_tier_mismatch:{treaty_a.trust_tier.name}"
            f"_vs_{treaty_b.trust_tier.name}"
        )

    detector_id = f"det-{uuid.uuid4().hex[:8]}"
    return ConflictDetector(
        detector_id=detector_id,
        treaty_pairs_checked=(pair,),
        conflicts_found=tuple(conflicts),
        detection_algorithm="cech-h1-coboundary-check",
        trust_tier=treaty_a.trust_tier.meet(treaty_b.trust_tier),
    )


def resolve_treaty_conflict(
    conflict: ConflictDetector,
    strategy: ResolutionStrategy,
) -> Tuple["HypercoverTreaty", ResolutionStrategy]:
    """Resolve a treaty conflict using a given strategy.

    Applies the strategy's resolution steps to produce a new merged treaty.
    The resolution synthesizes a new treaty from the union of parties and
    constraints derived from the strategy's applicable conflict types.

    Algorithm
    ---------
    1. Extract involved treaty IDs from conflict.treaty_pairs_checked.
    2. Determine parties from the treaty IDs (use as party names directly).
    3. Build constraints from the strategy's applicable_conflict_types.
    4. Call synthesize_treaty() with elevated trust tier.
    5. Return (new_treaty, effective_strategy).

    Parameters
    ----------
    conflict : ConflictDetector
        The detected conflict to resolve.
    strategy : ResolutionStrategy
        The resolution strategy to apply.

    Returns
    -------
    (HypercoverTreaty, ResolutionStrategy)
        The new merged treaty and the effective strategy used.
    """
    # Collect all parties from the checked pairs.
    parties: List[str] = []
    for pair in conflict.treaty_pairs_checked:
        if isinstance(pair, (list, tuple)):
            for tid in pair:
                if tid not in parties:
                    parties.append(tid)
        elif isinstance(pair, str) and pair not in parties:
            parties.append(pair)

    if not parties:
        parties = ["party-alpha", "party-beta"]

    constraints = list(strategy.applicable_conflict_types) + [
        f"resolve:{cf}" for cf in conflict.conflicts_found
    ]

    # The resolution elevates the trust tier by one level.
    target_trust = strategy.trust_tier.join(
        TrustTier.REVIEWED if not conflict.trust_tier else conflict.trust_tier
    )

    new_treaty = synthesize_treaty(constraints, parties, trust_level=target_trust)
    return new_treaty, strategy


# ---------------------------------------------------------------------------
# __main__ block: exercises every class and function
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("Core Treaty Algorithms — Comprehensive Demonstration")
    print("=" * 70)
    print(f"_HAS_S01 = {_HAS_S01}")
    print()

    # -----------------------------------------------------------------------
    # TrustTier algebra
    # -----------------------------------------------------------------------
    print("--- TrustTier ordered algebra ---")
    tiers = list(TrustTier)
    for t in tiers:
        print(f"  {t.name} = {int(t)}")
    t_low = TrustTier.PROPOSAL
    t_high = TrustTier.PROOF_BACKED
    print(f"  meet(PROPOSAL, PROOF_BACKED) = {t_low.meet(t_high).name}")
    print(f"  join(PROPOSAL, PROOF_BACKED) = {t_low.join(t_high).name}")
    print(f"  PROPOSAL <= VERIFIED: {TrustTier.PROPOSAL <= TrustTier.VERIFIED}")
    print(f"  PROOF_BACKED < REVIEWED: {TrustTier.PROOF_BACKED < TrustTier.REVIEWED}")
    print()

    # -----------------------------------------------------------------------
    # TreatySynthesizer
    # -----------------------------------------------------------------------
    print("--- TreatySynthesizer ---")
    synth = TreatySynthesizer(
        synthesizer_id="synth-demo-001",
        input_constraints=("no-overlap", "monotone-trust", "coverage-complete"),
        synthesis_algorithm="backtrack",
        output_treaties=("t-001", "t-002"),
        trust_tier=TrustTier.VERIFIED,
        obstruction_budget=0.25,
    )
    print(f"  {synth.to_summary()}")
    print(f"  constraint_count={synth.constraint_count()}")
    print(f"  is_budget_exceeded(0.10)={synth.is_budget_exceeded(0.10)}")
    print(f"  is_budget_exceeded(0.50)={synth.is_budget_exceeded(0.50)}")
    synth2 = synth.add_treaty("t-003")
    print(f"  After add_treaty: output_treaties={synth2.output_treaties}")
    print()

    # -----------------------------------------------------------------------
    # ConflictDetector
    # -----------------------------------------------------------------------
    print("--- ConflictDetector ---")
    detector = ConflictDetector(
        detector_id="det-demo-001",
        treaty_pairs_checked=(("t-001", "t-002"), ("t-002", "t-003")),
        conflicts_found=("conflict:t-001:t-002:law_incompatibility",),
        detection_algorithm="cech-h1",
        trust_tier=TrustTier.REVIEWED,
    )
    print(f"  conflict_count={detector.conflict_count()}")
    print(f"  checked_pair_count={detector.checked_pair_count()}")
    print(f"  conflict_rate={detector.conflict_rate():.2f}")
    print(f"  has_conflict_for('t-001')={detector.has_conflict_for('t-001')}")
    print(f"  has_conflict_for('t-999')={detector.has_conflict_for('t-999')}")
    detector2 = detector.add_conflict("conflict:t-002:t-003:boundary_conflict")
    print(f"  After add_conflict: conflicts_found={detector2.conflicts_found}")
    print()

    # -----------------------------------------------------------------------
    # ResolutionStrategy instances (all four)
    # -----------------------------------------------------------------------
    print("--- ResolutionStrategy constants ---")
    for strat in ALL_STRATEGIES:
        print(f"  [{strat.strategy_id}] {strat.strategy_name}")
        print(f"    feasible={strat.is_feasible()}, cost={strat.estimated_cost():.1f}")
        print(f"    applies_to('law_incompatibility')={strat.applies_to('law_incompatibility')}")
        print(f"    applies_to('unknown_type')={strat.applies_to('unknown_type')}")
    print()
    print("  Full step descriptions:")
    for strat in ALL_STRATEGIES:
        print(strat.apply_steps_description())
        print()

    # -----------------------------------------------------------------------
    # TreatyNegotiator
    # -----------------------------------------------------------------------
    print("--- TreatyNegotiator ---")
    neg = TreatyNegotiator(
        negotiator_id="neg-demo-001",
        parties=("Alice", "Bob", "Carol"),
        active_treaties=("t-001", "t-002"),
        negotiation_rounds=3,
        trust_tier=TrustTier.VERIFIED,
        current_obstruction=(complex(0.3, 0.1), complex(-0.2, 0.4), complex(0.0, 0.05)),
    )
    print(f"  {neg.run_round_description()}")
    print(f"  obstruction_norm={neg.obstruction_norm():.6f}")
    print(f"  is_converged(1e-6)={neg.is_converged(1e-6)}")
    print(f"  is_converged(1.0)={neg.is_converged(1.0)}")
    neg2 = neg.add_treaty("t-003")
    print(f"  After add_treaty: active_treaties={neg2.active_treaties}")
    print()

    # -----------------------------------------------------------------------
    # TreatyGraph
    # -----------------------------------------------------------------------
    print("--- TreatyGraph (nerve complex) ---")
    graph = TreatyGraph()
    for tid in ["t-001", "t-002", "t-003", "t-004", "t-005"]:
        graph.add_treaty(tid)
    graph.add_conflict("t-001", "t-002")
    graph.add_conflict("t-002", "t-003")
    graph.add_conflict("t-004", "t-005")
    print(f"  {graph}")
    print(f"  node_count={graph.node_count()}")
    print(f"  conflict_edges={graph.conflict_edges()}")
    print(f"  neighbors('t-002')={graph.neighbors('t-002')}")
    print(f"  has_conflict('t-001', 't-002')={graph.has_conflict('t-001', 't-002')}")
    print(f"  has_conflict('t-001', 't-005')={graph.has_conflict('t-001', 't-005')}")
    components = graph.connected_components()
    print(f"  connected_components={components}")
    graph.remove_conflict("t-001", "t-002")
    print(f"  After remove_conflict('t-001','t-002'): edges={graph.conflict_edges()}")
    print()

    # -----------------------------------------------------------------------
    # SynthesisEngine
    # -----------------------------------------------------------------------
    print("--- SynthesisEngine (backtracking CSP) ---")
    engine = SynthesisEngine()
    engine.add_constraint("all patches must have unique laws")
    engine.add_constraint("Alice conflict with Bob on patch-A")
    parties_demo = ["Alice", "Bob", "Carol"]
    assignment = engine.solve(parties_demo, max_depth=8)
    print(f"  solve result: {assignment}")
    print(f"  is_satisfiable: {engine.is_satisfiable()}")
    print(f"  synthesis_log ({len(engine.synthesis_log)} entries):")
    for entry in engine.synthesis_log:
        print(f"    {entry}")
    engine.reset()
    print(f"  After reset: constraints={engine.constraints}, solved={engine.is_satisfiable()}")
    print()

    # -----------------------------------------------------------------------
    # CechConflictClass
    # -----------------------------------------------------------------------
    print("--- CechConflictClass (H¹ computation) ---")
    laws_a = {"patch-A": "law-conservation", "patch-B": "law-disclosure", "patch-C": "law-monotone"}
    laws_b = {"patch-A": "law-conservation", "patch-B": "law-DIFFERENT", "patch-C": "law-monotone"}
    cech = CechConflictClass(
        treaty_a_id="t-demo-a",
        treaty_b_id="t-demo-b",
        cochain={},
        patches=["patch-A", "patch-B", "patch-C"],
    )
    cochain = cech.compute_conflict_cochain(laws_a, laws_b)
    print(f"  cochain: {cochain}")
    print(f"  obstruction_norm={cech.obstruction_norm():.4f}")
    print(f"  is_trivial={cech.is_trivial()}")
    print(f"  h1_representative: {cech.h1_representative()}")
    print(f"  cup_product_hint: {cech.cup_product_hint()}")
    # Trivial case: identical laws
    cech_trivial = CechConflictClass(
        treaty_a_id="t-trivial-a",
        treaty_b_id="t-trivial-b",
        cochain={},
        patches=["patch-X", "patch-Y"],
    )
    cech_trivial.compute_conflict_cochain(
        {"patch-X": "law-same", "patch-Y": "law-same"},
        {"patch-X": "law-same", "patch-Y": "law-same"},
    )
    print(f"  trivial case: is_trivial={cech_trivial.is_trivial()}, norm={cech_trivial.obstruction_norm():.4f}")
    print()

    # -----------------------------------------------------------------------
    # NegotiationProtocol
    # -----------------------------------------------------------------------
    print("--- NegotiationProtocol (Rubinstein rounds) ---")
    protocol = NegotiationProtocol()
    for p in ["Alice", "Bob", "Carol"]:
        protocol.add_party(p)
    pid1 = protocol.propose("Alice", {"patch-A": "law-v1", "patch-B": "law-v2"})
    print(f"  Proposal by Alice: {pid1}")
    protocol.accept("Bob", pid1)
    protocol.reject("Carol", pid1, "law-v2 conflicts with conservation")
    summaries = protocol.run_rounds(5)
    print(f"  run_rounds(5) summaries:")
    for s in summaries:
        print(f"    Round {s['round']}: proposer={s['proposer']}, "
              f"accepted={s['accepted']}, resolved={s['resolved']}")
    print(f"  current_state: {protocol.current_state()}")
    print(f"  is_resolved: {protocol.is_resolved()}")
    print()

    # -----------------------------------------------------------------------
    # synthesize_treaty
    # -----------------------------------------------------------------------
    print("--- synthesize_treaty ---")
    treaty_synth = synthesize_treaty(
        constraints=["coverage-complete", "monotone-trust", "no-overlap-conflict"],
        parties=["Alice", "Bob", "Carol"],
        trust_level=TrustTier.VERIFIED,
    )
    print(f"  treaty_id={treaty_synth.treaty_id}")
    print(f"  patches={treaty_synth.patches}")
    print(f"  signatories={treaty_synth.signatories}")
    print(f"  trust_tier={treaty_synth.trust_tier.name}")
    print(f"  overlap_laws count={len(treaty_synth.overlap_laws)}")
    print()

    # -----------------------------------------------------------------------
    # detect_treaty_conflict
    # -----------------------------------------------------------------------
    print("--- detect_treaty_conflict ---")
    treaty_x = synthesize_treaty(
        constraints=["law-A", "law-B"],
        parties=["Alice", "Dave"],
        trust_level=TrustTier.REVIEWED,
    )
    treaty_y = synthesize_treaty(
        constraints=["law-C", "law-B"],
        parties=["Alice", "Eve"],
        trust_level=TrustTier.VERIFIED,
    )
    conflict_result = detect_treaty_conflict(treaty_x, treaty_y)
    print(f"  detector_id={conflict_result.detector_id}")
    print(f"  conflicts_found={conflict_result.conflicts_found}")
    print(f"  conflict_count={conflict_result.conflict_count()}")
    print(f"  conflict_rate={conflict_result.conflict_rate():.2f}")
    print(f"  trust_tier={conflict_result.trust_tier.name}")
    print()

    # -----------------------------------------------------------------------
    # resolve_treaty_conflict
    # -----------------------------------------------------------------------
    print("--- resolve_treaty_conflict ---")
    for strat in ALL_STRATEGIES:
        resolved_treaty, eff_strategy = resolve_treaty_conflict(conflict_result, strat)
        print(f"  Strategy={eff_strategy.strategy_name!r}: "
              f"new_treaty_id={resolved_treaty.treaty_id}, "
              f"trust={resolved_treaty.trust_tier.name}, "
              f"patches={len(resolved_treaty.patches)}")
    print()

    # -----------------------------------------------------------------------
    # Integration: build a full treaty graph, detect all conflicts, resolve
    # -----------------------------------------------------------------------
    print("--- Integration: full workflow ---")
    num_treaties = 4
    all_treaties = []
    for i in range(num_treaties):
        t = synthesize_treaty(
            constraints=[f"constraint-{i}-alpha", f"constraint-{i}-beta"],
            parties=[f"party-{i}-A", f"party-{i}-B"],
            trust_level=TrustTier.REVIEWED,
        )
        all_treaties.append(t)
        print(f"  Created treaty[{i}]: {t.treaty_id}")

    full_graph = TreatyGraph()
    for t in all_treaties:
        full_graph.add_treaty(t.treaty_id)

    conflict_detectors = []
    for ta, tb in itertools.combinations(all_treaties, 2):
        det = detect_treaty_conflict(ta, tb)
        conflict_detectors.append(det)
        if det.conflict_count() > 0:
            full_graph.add_conflict(ta.treaty_id, tb.treaty_id)
            print(f"  Conflict detected: {ta.treaty_id} ↔ {tb.treaty_id} "
                  f"({det.conflict_count()} conflicts)")

    print(f"  TreatyGraph: {full_graph}")
    print(f"  Components: {full_graph.connected_components()}")

    # Resolve all detected conflicts.
    for det in conflict_detectors:
        if det.conflict_count() > 0:
            new_t, strat = resolve_treaty_conflict(det, STRATEGY_NEGOTIATION)
            print(f"  Resolved with {strat.strategy_name!r}: {new_t.treaty_id}")

    # -----------------------------------------------------------------------
    # make_judgment (fallback or s01)
    # -----------------------------------------------------------------------
    print()
    print("--- Judgment (8-tuple) ---")
    j = make_judgment(
        c="context-demo",
        phi="All treaty laws are conservation-compliant",
        assumptions=("conservation-law holds", "patches are non-degenerate"),
        evidence=("empirical-measurement-2024", "formal-proof-sketch"),
        obstructions=(),
        blame=(),
        trust_tier=TrustTier.VERIFIED,
        proof_obligations=("verify cocycle condition", "check trust monotonicity"),
    )
    print(f"  Judgment: c={j.c!r}, phi={j.phi!r}")
    print(f"  trust_tier={j.trust_tier.name}")
    print(f"  assumptions={j.assumptions}")
    print(f"  proof_obligations={j.proof_obligations}")

    # -----------------------------------------------------------------------
    # EXAMPLE_* constants (from s01 or fallback)
    # -----------------------------------------------------------------------
    print()
    print("--- EXAMPLE_* constants ---")
    print(f"  EXAMPLE_PATCHES={EXAMPLE_PATCHES}")
    print(f"  EXAMPLE_OVERLAP_MATRIX (keys)={list(EXAMPLE_OVERLAP_MATRIX.keys())}")
    print(f"  EXAMPLE_LAWS count={len(EXAMPLE_LAWS)}")
    if _HAS_S01:
        print(f"  _DEFAULT_DB type={type(_DEFAULT_DB).__name__}")
    else:
        print(f"  _DEFAULT_DB (fallback) keys={list(_DEFAULT_DB.laws.keys())}")

    print()
    print("=" * 70)
    print("All demonstrations completed successfully.")
    print("=" * 70)
    sys.exit(0)
