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
    """Generate a stable, deterministic treaty identifier from two interfaces."""
    name_a = interface_a.get("name", "")
    name_b = interface_b.get("name", "")
    ver_a = interface_a.get("version", "0.0.0")
    ver_b = interface_b.get("version", "0.0.0")
    raw = f"{name_a}:{ver_a}|{name_b}:{ver_b}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"treaty-{digest}"


def _score_conflict(conflict: dict) -> float:
    """Compute a normalised severity score in [0, 1] for a conflict record."""
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
    """Return the union of two export sets."""
    return exports_a | exports_b


def _split_exports(exports: frozenset, split_key: str) -> tuple[frozenset, frozenset]:
    """Partition an export set into two disjoint halves based on a split key."""
    left = frozenset(s for s in exports if str(s) < split_key)
    right = frozenset(s for s in exports if str(s) >= split_key)
    return left, right


def _compute_treaty_hash(treaty: dict) -> str:
    """Compute a content-addressed hash of a treaty dict."""
    import json
    try:
        raw = json.dumps(treaty, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        raw = str(sorted(treaty.items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _validate_interface(interface: dict) -> bool:
    """Return ``True`` iff *interface* has the minimum required keys."""
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
    """Return a normalised copy of *interface* with canonical types."""
    return {
        "name": str(interface.get("name", "")).strip(),
        "version": str(interface.get("version", "0.0.0")),
        "exports": sorted(str(e) for e in interface.get("exports", [])),
        "imports": sorted(str(i) for i in interface.get("imports", [])),
        "constraints": dict(interface.get("constraints", {})),
    }


def _extract_exports(interface: dict) -> list[str]:
    """Extract and return the exports list from an interface descriptor."""
    return [str(e) for e in interface.get("exports", [])]


def _extract_imports(interface: dict) -> list[str]:
    """Extract and return the imports list from an interface descriptor."""
    return [str(i) for i in interface.get("imports", [])]


def _check_version_compatibility(version_a: str, version_b: str) -> bool:
    """Determine whether two semantic version strings are compatible."""
    def _major(v: str) -> int:
        parts = str(v).split(".")
        try:
            return int(parts[0])
        except (IndexError, ValueError):
            return 0
    return _major(version_a) == _major(version_b)


def _build_conflict_record(kind: str, detail: str, severity: float) -> dict:
    """Construct a standardised conflict record dict."""
    return {
        "conflict_id": str(uuid.uuid4()),
        "kind": kind,
        "detail": detail,
        "severity": max(0.0, min(1.0, float(severity))),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


def _annotate_treaty(treaty: dict, annotation: str) -> dict:
    """Return a copy of *treaty* with an additional annotation string appended."""
    result = dict(treaty)
    annotations = list(result.get("annotations", []))
    annotations.append(annotation)
    result["annotations"] = annotations
    return result


# ---------------------------------------------------------------------------
# Module-level resolution function
# ---------------------------------------------------------------------------

def apply_resolution_strategy(
    strategy: ResolutionStrategy,
    conflict: dict,
    treaty_a: dict,
    treaty_b: dict,
) -> dict:
    """Apply *strategy* to resolve *conflict* between *treaty_a* and *treaty_b*."""
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
        trust = TrustTier.PROPOSAL.name

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
    """Generates a :class:`HypercoverTreaty` from two patch interface descriptors."""

    def __init__(self, strategy: str = "default") -> None:
        self.synthesizer_id: str = str(uuid.uuid4())
        self.strategy: str = strategy
        self.synthesis_count: int = 0

    def synthesize(self, interface_a: dict, interface_b: dict) -> dict:
        """Synthesise a treaty from two interface descriptors."""
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
            "version_compatible": _check_version_compatibility(norm_a["version"], norm_b["version"]),
        }

    def _compute_shared_interface(self, a: dict, b: dict) -> frozenset:
        """Compute the shared interface S = (exp_A ∩ imp_B) ∪ (exp_B ∩ imp_A)."""
        exp_a = frozenset(_extract_exports(a))
        imp_b = frozenset(_extract_imports(b))
        exp_b = frozenset(_extract_exports(b))
        imp_a = frozenset(_extract_imports(a))
        return (exp_a & imp_b) | (exp_b & imp_a)

    def _build_export_map(self, interface: dict) -> frozenset:
        """Return the export set of a single normalised interface as a frozenset."""
        return frozenset(_extract_exports(interface))


# ---------------------------------------------------------------------------
# ConflictDetector
# ---------------------------------------------------------------------------

class ConflictDetector:
    """Finds conflicts between two treaties."""

    def __init__(self) -> None:
        self.detector_id: str = str(uuid.uuid4())
        self.conflict_log: list[dict] = []

    def detect(self, treaty_a: dict, treaty_b: dict) -> list[dict]:
        """Detect conflicts between *treaty_a* and *treaty_b*."""
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
        """Return ``True`` iff exports of *a* overlap with imports of *b* contradictorily."""
        exports_a = frozenset(_extract_exports(a))
        imports_b = frozenset(_extract_imports(b))
        exports_b = frozenset(_extract_exports(b))
        imports_a = frozenset(_extract_imports(a))
        shared_exp = exports_a & exports_b
        mutual_import = imports_a & imports_b
        return bool(shared_exp & mutual_import)

    def _check_export_overlap(self, a: dict, b: dict) -> list[str]:
        """Return the list of symbol names exported by both treaties."""
        exports_a = frozenset(_extract_exports(a))
        exports_b = frozenset(_extract_exports(b))
        return sorted(exports_a & exports_b)

    def report(self) -> dict:
        """Return an aggregate report of all conflicts detected so far."""
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
    """Orchestrates the full treaty negotiation process."""

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
        """Run the full negotiation protocol and return the resolved treaty."""
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
        """Apply the default resolution strategy to each conflict."""
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
            treaty = {**treaty, "export_map": resolution.get("resolved_exports", treaty.get("export_map", []))}
        return treaty

    def get_history(self) -> list[dict]:
        """Return the complete negotiation log for all negotiations run so far."""
        return list(self.negotiation_log)


# ---------------------------------------------------------------------------
# TreatyAlgorithms façade
# ---------------------------------------------------------------------------

class TreatyAlgorithms:
    """Façade exposing all treaty algorithms as class methods."""

    _synthesizer: TreatySynthesizer = TreatySynthesizer()
    _detector: ConflictDetector = ConflictDetector()
    _negotiator: TreatyNegotiator = TreatyNegotiator(
        synthesizer=_synthesizer,
        detector=_detector,
        default_strategy=ResolutionStrategy[DEFAULT_RESOLUTION],
    )

    @classmethod
    def synthesize(cls, a: dict, b: dict) -> dict:
        """Synthesise a treaty from two interface descriptors."""
        return cls._synthesizer.synthesize(a, b)

    @classmethod
    def detect_conflict(cls, t_a: dict, t_b: dict) -> list[dict]:
        """Detect conflicts between two treaties."""
        return cls._detector.detect(t_a, t_b)

    @classmethod
    def resolve(cls, conflict: dict, strategy: ResolutionStrategy) -> dict:
        """Resolve a single conflict using the given strategy."""
        return apply_resolution_strategy(strategy, conflict, {}, {})

    @classmethod
    def run(cls, interface_a: dict, interface_b: dict) -> dict:
        """Run the full treaty algorithm pipeline (main entry point)."""
        return cls._negotiator.negotiate(interface_a, interface_b)


# ---------------------------------------------------------------------------
# Public module-level API functions
# ---------------------------------------------------------------------------

def synthesize_treaty(interface_a: dict, interface_b: dict) -> dict:
    """Synthesise a hypercover treaty from two patch interface descriptors."""
    synth = TreatySynthesizer()
    return synth.synthesize(interface_a, interface_b)


def detect_treaty_conflict(treaty_a: dict, treaty_b: dict) -> list[dict]:
    """Detect conflicts between two treaties."""
    det = ConflictDetector()
    return det.detect(treaty_a, treaty_b)


def resolve_treaty_conflict(conflict: dict, strategy: ResolutionStrategy) -> dict:
    """Resolve a single conflict using the given strategy."""
    return apply_resolution_strategy(strategy, conflict, {}, {})


def run_treaty_algorithm(interface_a: dict, interface_b: dict) -> dict:
    """Run the full treaty synthesis and negotiation pipeline."""
    negotiator = TreatyNegotiator()
    return negotiator.negotiate(interface_a, interface_b)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
