"""
Interface discipline — overlap objectives ensuring local sections glue globally.

# copilot: This module implements the machinery for enforcing *interface discipline*
across pairs of local sections in a cover.  A cover is a collection of open sets (or
abstract "sections") that jointly describe a geometric or combinatorial object.  For
the local data to assemble into a globally consistent object, adjacent sections must
agree on their overlap — this agreement is captured here as *overlap objectives* and
*gluing conditions*.

Conceptually the module occupies the second slot (s02) of the local-construction
pipeline:

    s01  —  section scaffolding
    s02  —  interface discipline & overlap objectives   ← THIS FILE
    s03  —  global assembly & Čech cohomology obstruction detection

An :class:`InterfaceDiscipline` encodes a named set of rules (predicates) that every
pair of adjacent sections must satisfy.  Given two sections an
:class:`OverlapObjective` is a single verifiable requirement derived from the
discipline; its :meth:`~OverlapObjective.evaluate` method inspects the actual section
data and records whether the objective is met together with supporting evidence.

A :class:`GluingCondition` is the coarser, binary question: do the two restrictions
(left section restricted to the overlap, right section restricted to the overlap)
agree?  Failure of a gluing condition is the local signature of a Čech 1-cocycle
obstruction.

:class:`InterfaceObligation` bridges the gap between a detected violation and its
formal resolution: it records *what* must be proved and whether a proof has been
supplied.

The top-level functions orchestrate these primitives over a full cover:

* :func:`check_interface_discipline` — scan a cover for discipline violations.
* :func:`build_overlap_objectives` — derive objectives for a single pair of sections.
* :func:`verify_gluing_condition` — check the gluing condition for one overlap.
* :func:`enforce_discipline` — produce repaired sections and outstanding obligations.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jugeo error / judgment imports (graceful fallback)
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
# TrustTier — local trust lattice
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust lattice for interface-discipline verdicts.

    The tiers correspond roughly to the proof-theoretic weight of the evidence
    supporting a claim about section compatibility.

    Attributes
    ----------
    PROPOSAL:
        The claim is a bare conjecture with no supporting evidence.  It should
        not be relied upon without further verification.
    WITNESSED:
        A runtime execution has observed the claim to hold at least once.  This
        is stronger than a proposal but still defeasible.
    DISCHARGED:
        A solver (SMT, SAT, constraint-propagation) has discharged the claim
        within a bounded search.  Trusted for engineering purposes.
    CERTIFIED:
        A human reviewer or a trusted oracle has explicitly certified the claim.
        Carries institutional weight.
    PROOF_BACKED:
        A machine-checkable proof exists and has been verified.  This is the
        strongest tier and should be treated as ground truth.
    """

    PROPOSAL = 1
    WITNESSED = 2
    DISCHARGED = 3
    CERTIFIED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def join(self, other: "TrustTier") -> "TrustTier":
        """Return the least upper bound of *self* and *other*.

        In the linear order ``PROPOSAL < WITNESSED < DISCHARGED < CERTIFIED <
        PROOF_BACKED`` the join is simply the maximum.

        Parameters
        ----------
        other:
            Another :class:`TrustTier` value.

        Returns
        -------
        TrustTier
            The higher of the two tiers.
        """
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Return the greatest lower bound of *self* and *other*.

        The meet is the minimum of the two integer values.

        Parameters
        ----------
        other:
            Another :class:`TrustTier` value.

        Returns
        -------
        TrustTier
            The lower of the two tiers.
        """
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> "TrustTier":
        """Return the next higher tier, saturating at :attr:`PROOF_BACKED`.

        Returns
        -------
        TrustTier
            ``self + 1`` clamped to :attr:`PROOF_BACKED`.
        """
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> "TrustTier":
        """Return the next lower tier, saturating at :attr:`PROPOSAL`.

        Returns
        -------
        TrustTier
            ``self - 1`` clamped to :attr:`PROPOSAL`.
        """
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def __str__(self) -> str:  # noqa: D105
        return self.name.lower()

# ---------------------------------------------------------------------------
# Shared dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """An atomic verdict about a pair of sections satisfying some property.

    Parameters
    ----------
    judgment_id:
        Unique identifier for this judgment instance.
    proposition:
        Human-readable statement of the claim being judged.
    verdict:
        Whether the claim is considered to hold (``True``) or not (``False``).
    trust_tier:
        The epistemic weight assigned to this judgment.
    evidence_keys:
        Immutable tuple of opaque identifiers for the evidence items supporting
        the verdict.
    timestamp:
        Unix timestamp (float) at which the judgment was recorded.
    provenance:
        Short token indicating the system or agent that produced the judgment,
        e.g. ``"solver"``, ``"oracle"``, ``"human"``.
    """

    judgment_id: str
    proposition: str
    verdict: bool
    trust_tier: TrustTier
    evidence_keys: Tuple[str, ...]
    timestamp: float
    provenance: str

    # convenience -----------------------------------------------------------

    def is_trusted(self, threshold: TrustTier = TrustTier.DISCHARGED) -> bool:
        """Return ``True`` iff the trust tier meets or exceeds *threshold*."""
        return self.trust_tier >= threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding."""
        return {
            "judgment_id": self.judgment_id,
            "proposition": self.proposition,
            "verdict": self.verdict,
            "trust_tier": int(self.trust_tier),
            "trust_tier_name": str(self.trust_tier),
            "evidence_keys": list(self.evidence_keys),
            "timestamp": self.timestamp,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class CechObstruction:
    """Represents a Čech 1-cocycle obstruction to global section assembly.

    A Čech obstruction arises when the locally consistent data on triple overlaps
    fails the cocycle condition — the "transition functions" do not compose to the
    identity around a triple ``(i, j, k)``.

    Parameters
    ----------
    obstruction_id:
        Unique identifier.
    triple:
        The ordered triple ``(i, j, k)`` of section indices forming the overlap.
    cohomology_class:
        An opaque string encoding the cohomology class of the obstruction.  May
        be ``""`` if the class has not been computed.
    blocking_pairs:
        Frozenset of ``(left_id, right_id)`` pairs whose gluing failures
        contribute to this obstruction.
    severity:
        A float in ``[0.0, 1.0]`` indicating how "large" the obstruction is,
        where ``1.0`` means a complete failure to glue.
    repair_candidates:
        Tuple of opaque strategy tokens that may resolve the obstruction.
    """

    obstruction_id: str
    triple: Tuple[str, str, str]
    cohomology_class: str
    blocking_pairs: FrozenSet[Tuple[str, str]]
    severity: float
    repair_candidates: Tuple[str, ...]

    def is_trivial(self) -> bool:
        """Return ``True`` if the obstruction is the trivial class (no actual failure)."""
        return self.severity < 1e-9 and self.cohomology_class in ("", "trivial", "0")

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "obstruction_id": self.obstruction_id,
            "triple": list(self.triple),
            "cohomology_class": self.cohomology_class,
            "blocking_pairs": [list(p) for p in sorted(self.blocking_pairs)],
            "severity": self.severity,
            "repair_candidates": list(self.repair_candidates),
        }

# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterfaceDiscipline:
    """A named collection of rules governing pairwise section compatibility.

    An :class:`InterfaceDiscipline` is the primary configuration object for this
    module.  It bundles a list of *rules* (each rule is a callable or a serialisable
    predicate descriptor) with metadata about how strictly those rules should be
    applied.

    Parameters
    ----------
    discipline_id:
        Unique identifier for this discipline definition.
    name:
        Human-readable label, e.g. ``"strict_topology_discipline"``.
    rules:
        Immutable tuple of rule descriptors.  Each descriptor is an opaque dict
        with at least a ``"rule_id"`` key and a ``"kind"`` key.
    strictness_level:
        Integer in ``[0, 10]`` where ``0`` means advisory-only and ``10`` means
        every rule is a hard constraint with no tolerance.
    version:
        Semantic version string for the discipline definition, e.g. ``"1.2.0"``.
    """

    discipline_id: str
    name: str
    rules: Tuple[Dict[str, Any], ...]
    strictness_level: int
    version: str

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------

    def check(self, left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply all rules to the pair *(left, right)* and return failures.

        Each rule descriptor may carry a ``"predicate"`` key whose value is the
        string name of a built-in check, or a ``"custom_fn"`` callable.  Unrecognised
        predicates are skipped with a warning.

        Parameters
        ----------
        left:
            Serialisable dict representation of the left section.
        right:
            Serialisable dict representation of the right section.

        Returns
        -------
        list[dict]
            A (possibly empty) list of failure records, each containing
            ``"rule_id"``, ``"reason"``, and ``"severity"`` keys.
        """
        failures: List[Dict[str, Any]] = []
        for rule in self.rules:
            rule_id = rule.get("rule_id", "unknown")
            predicate = rule.get("predicate", "")
            severity = rule.get("severity", 1.0)

            passed = True
            reason = ""

            if predicate == "schema_match":
                left_keys = set(left.keys())
                right_keys = set(right.keys())
                required = set(rule.get("required_keys", []))
                missing_left = required - left_keys
                missing_right = required - right_keys
                if missing_left or missing_right:
                    passed = False
                    reason = (
                        f"schema_match failed: left missing {sorted(missing_left)}, "
                        f"right missing {sorted(missing_right)}"
                    )

            elif predicate == "overlap_nonempty":
                left_ids: Set[str] = set(left.get("element_ids", []))
                right_ids: Set[str] = set(right.get("element_ids", []))
                overlap = left_ids & right_ids
                if not overlap:
                    passed = False
                    reason = "overlap_nonempty failed: intersection of element sets is empty"

            elif predicate == "dimension_agree":
                ld = left.get("dimension")
                rd = right.get("dimension")
                if ld is not None and rd is not None and ld != rd:
                    passed = False
                    reason = f"dimension_agree failed: left={ld}, right={rd}"

            elif predicate == "orientation_compatible":
                lo = left.get("orientation", 1)
                ro = right.get("orientation", 1)
                if lo * ro < 0:
                    passed = False
                    reason = f"orientation_compatible failed: left={lo}, right={ro} are incompatible"

            elif predicate == "boundary_shared":
                lb = set(left.get("boundary_ids", []))
                rb = set(right.get("boundary_ids", []))
                if lb.isdisjoint(rb):
                    passed = False
                    reason = "boundary_shared failed: no shared boundary elements"

            else:
                if predicate:
                    logger.debug("InterfaceDiscipline.check: unknown predicate %r — skipping", predicate)
                continue

            if not passed:
                logger.info(
                    "Discipline %r rule %r FAILED (severity=%.2f): %s",
                    self.name, rule_id, severity, reason,
                )
                failures.append({"rule_id": rule_id, "reason": reason, "severity": severity})

        return failures

    def add_rule(self, rule: Dict[str, Any]) -> "InterfaceDiscipline":
        """Return a new discipline with *rule* appended.

        Because the dataclass is frozen the original object is unchanged.

        Parameters
        ----------
        rule:
            A rule descriptor dict (must contain at least ``"rule_id"``).

        Returns
        -------
        InterfaceDiscipline
            A new instance with the rule appended.
        """
        if "rule_id" not in rule:
            raise ValueError("Rule descriptor must contain 'rule_id'.")
        return replace(self, rules=self.rules + (rule,))

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the discipline to a plain dict."""
        return {
            "discipline_id": self.discipline_id,
            "name": self.name,
            "rules": list(self.rules),
            "strictness_level": self.strictness_level,
            "version": self.version,
        }

    def is_strict(self) -> bool:
        """Return ``True`` if the strictness level is at the maximum (10)."""
        return self.strictness_level >= 10


@dataclass(frozen=True)
class OverlapObjective:
    """A single verifiable requirement derived from an interface discipline.

    Parameters
    ----------
    objective_id:
        Unique identifier for this objective instance.
    objective_type:
        Short label such as ``"schema_match"``, ``"dimension_agree"``, etc.
    left_section_id:
        Identifier of the left section under examination.
    right_section_id:
        Identifier of the right section under examination.
    constraint:
        An opaque dict encoding the specific constraint to be verified.
    satisfied:
        ``True`` if the objective has been evaluated and found to hold.
        ``None`` if evaluation has not yet been performed.
    evidence:
        Tuple of opaque evidence strings recorded during evaluation.
    """

    objective_id: str
    objective_type: str
    left_section_id: str
    right_section_id: str
    constraint: Dict[str, Any]
    satisfied: Optional[bool]
    evidence: Tuple[str, ...]

    # ------------------------------------------------------------------

    def evaluate(
        self,
        left_section: Dict[str, Any],
        right_section: Dict[str, Any],
    ) -> "OverlapObjective":
        """Evaluate this objective against concrete section data.

        Parameters
        ----------
        left_section:
            The actual data for the left section.
        right_section:
            The actual data for the right section.

        Returns
        -------
        OverlapObjective
            A new frozen instance with ``satisfied`` and ``evidence`` populated.
        """
        obj_type = self.objective_type
        evidence_list: List[str] = []
        satisfied = True

        if obj_type == "schema_match":
            required = set(self.constraint.get("required_keys", []))
            missing_l = required - set(left_section.keys())
            missing_r = required - set(right_section.keys())
            if missing_l or missing_r:
                satisfied = False
                evidence_list.append(f"left_missing={sorted(missing_l)}")
                evidence_list.append(f"right_missing={sorted(missing_r)}")
            else:
                evidence_list.append("all_required_keys_present")

        elif obj_type == "overlap_nonempty":
            left_ids = set(left_section.get("element_ids", []))
            right_ids = set(right_section.get("element_ids", []))
            overlap = left_ids & right_ids
            if overlap:
                evidence_list.append(f"overlap_size={len(overlap)}")
            else:
                satisfied = False
                evidence_list.append("empty_overlap")

        elif obj_type == "dimension_agree":
            ld = left_section.get("dimension")
            rd = right_section.get("dimension")
            if ld is None or rd is None:
                evidence_list.append("dimension_key_absent")
            elif ld != rd:
                satisfied = False
                evidence_list.append(f"left_dim={ld},right_dim={rd}")
            else:
                evidence_list.append(f"dimension={ld}")

        elif obj_type == "orientation_compatible":
            lo = left_section.get("orientation", 1)
            ro = right_section.get("orientation", 1)
            if lo * ro < 0:
                satisfied = False
                evidence_list.append(f"left_orient={lo},right_orient={ro}")
            else:
                evidence_list.append(f"orientation_product={lo * ro}")

        elif obj_type == "boundary_shared":
            lb = set(left_section.get("boundary_ids", []))
            rb = set(right_section.get("boundary_ids", []))
            shared = lb & rb
            if not shared:
                satisfied = False
                evidence_list.append("no_shared_boundary")
            else:
                evidence_list.append(f"shared_boundary_count={len(shared)}")

        else:
            evidence_list.append(f"unknown_objective_type={obj_type}")

        logger.debug(
            "OverlapObjective %r (%s→%s) type=%r satisfied=%s",
            self.objective_id, self.left_section_id, self.right_section_id,
            obj_type, satisfied,
        )

        return replace(self, satisfied=satisfied, evidence=tuple(evidence_list))

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "objective_id": self.objective_id,
            "objective_type": self.objective_type,
            "left_section_id": self.left_section_id,
            "right_section_id": self.right_section_id,
            "constraint": self.constraint,
            "satisfied": self.satisfied,
            "evidence": list(self.evidence),
        }

    def is_critical(self) -> bool:
        """Return ``True`` if this objective is unsatisfied and explicitly critical.

        An objective is considered critical when its constraint dict carries a
        truthy ``"critical"`` key and the evaluation found it unsatisfied.
        """
        return self.satisfied is False and bool(self.constraint.get("critical", False))


@dataclass(frozen=True)
class InterfaceObligation:
    """Formal obligation to prove that a section pair satisfies a property.

    Parameters
    ----------
    obligation_id:
        Unique identifier.
    left_id:
        Identifier of the left section.
    right_id:
        Identifier of the right section.
    obligation_type:
        Short label, e.g. ``"gluing"``, ``"dimension"``, ``"orientation"``.
    description:
        Human-readable statement of what must be proved.
    discharged:
        ``True`` once a proof has been accepted.
    proof:
        The proof object (may be ``None`` until discharged).
    """

    obligation_id: str
    left_id: str
    right_id: str
    obligation_type: str
    description: str
    discharged: bool
    proof: Optional[Any]

    # ------------------------------------------------------------------

    def discharge(self, proof: Any) -> "InterfaceObligation":
        """Return a new obligation marked as discharged with the supplied proof.

        Parameters
        ----------
        proof:
            Any object representing the proof (e.g. a certificate dict, a solver
            output record, or a human-signed attestation).

        Returns
        -------
        InterfaceObligation
            A new frozen instance with ``discharged=True`` and ``proof`` set.

        Raises
        ------
        JuGeoError
            If the obligation is already discharged and ``_JUGEO_ERRORS`` is
            available, or a plain :class:`ValueError` otherwise.
        """
        if self.discharged:
            msg = f"Obligation {self.obligation_id!r} is already discharged."
            if _JUGEO_ERRORS:
                raise_with_scope(
                    "OBLIGATION_ALREADY_DISCHARGED",
                    message=msg,
                    provenance=self.obligation_id,
                )
            raise ValueError(msg)
        logger.info("Discharging obligation %r (%s)", self.obligation_id, self.obligation_type)
        return replace(self, discharged=True, proof=proof)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        proof_repr = self.proof if isinstance(self.proof, (str, int, float, bool, type(None))) else repr(self.proof)
        return {
            "obligation_id": self.obligation_id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "obligation_type": self.obligation_type,
            "description": self.description,
            "discharged": self.discharged,
            "proof": proof_repr,
        }

    def requires_proof(self) -> bool:
        """Return ``True`` if the obligation has not yet been discharged."""
        return not self.discharged


@dataclass(frozen=True)
class GluingCondition:
    """Binary verdict on whether two sections agree on their overlap region.

    The *restriction* of a section to a region is its data projected onto that
    region's element set.  The gluing condition holds iff the two restrictions
    are equal (or compatible under the active encoding).

    Parameters
    ----------
    condition_id:
        Unique identifier.
    left_section_id:
        Identifier of the left section.
    right_section_id:
        Identifier of the right section.
    overlap_region:
        Serialisable description of the overlap (element ids, dimension, etc.).
    left_restriction:
        The left section restricted to the overlap region.
    right_restriction:
        The right section restricted to the overlap region.
    is_satisfied:
        ``True`` if the two restrictions agree.  ``None`` if not yet evaluated.
    """

    condition_id: str
    left_section_id: str
    right_section_id: str
    overlap_region: Dict[str, Any]
    left_restriction: Dict[str, Any]
    right_restriction: Dict[str, Any]
    is_satisfied: Optional[bool]

    # ------------------------------------------------------------------

    def check(self) -> "GluingCondition":
        """Evaluate the gluing condition and return an updated instance.

        The comparison is performed key-by-key on the two restriction dicts.
        Values that are lists are compared as sorted tuples to be order-agnostic
        for element-id sets; scalar values are compared directly.

        Returns
        -------
        GluingCondition
            A new frozen instance with ``is_satisfied`` set.
        """
        def _normalise(v: Any) -> Any:
            if isinstance(v, list):
                try:
                    return tuple(sorted(v))
                except TypeError:
                    return tuple(v)
            return v

        l_keys = set(self.left_restriction.keys())
        r_keys = set(self.right_restriction.keys())
        all_keys = l_keys | r_keys

        mismatch = False
        for k in all_keys:
            lv = _normalise(self.left_restriction.get(k))
            rv = _normalise(self.right_restriction.get(k))
            if lv != rv:
                logger.debug(
                    "GluingCondition %r: key %r differs — left=%r, right=%r",
                    self.condition_id, k, lv, rv,
                )
                mismatch = True
                break

        satisfied = not mismatch
        logger.info(
            "GluingCondition %r (%s ∩ %s): satisfied=%s",
            self.condition_id, self.left_section_id, self.right_section_id, satisfied,
        )
        return replace(self, is_satisfied=satisfied)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "condition_id": self.condition_id,
            "left_section_id": self.left_section_id,
            "right_section_id": self.right_section_id,
            "overlap_region": self.overlap_region,
            "left_restriction": self.left_restriction,
            "right_restriction": self.right_restriction,
            "is_satisfied": self.is_satisfied,
        }

    def witness(self) -> Optional[Dict[str, Any]]:
        """Return a witness to the gluing failure, or ``None`` if satisfied.

        The witness is a dict mapping each differing key to the pair of
        disagreeing values found in the two restrictions.

        Returns
        -------
        dict or None
            ``None`` if satisfied; a dict of ``{key: (left_val, right_val)}``
            otherwise.
        """
        if self.is_satisfied is True:
            return None
        diffs: Dict[str, Any] = {}
        all_keys = set(self.left_restriction) | set(self.right_restriction)
        for k in sorted(all_keys):
            lv = self.left_restriction.get(k)
            rv = self.right_restriction.get(k)
            if lv != rv:
                diffs[k] = (lv, rv)
        return diffs if diffs else None


@dataclass(frozen=True)
class DisciplineChecker:
    """Stateless runner that applies an :class:`InterfaceDiscipline` over many pairs.

    Parameters
    ----------
    checker_id:
        Unique identifier for this checker instance.
    discipline:
        The :class:`InterfaceDiscipline` to apply.
    objectives_checked:
        Running count of objectives evaluated so far.
    failures:
        Accumulated failure records as a frozen tuple of dicts.
    """

    checker_id: str
    discipline: InterfaceDiscipline
    objectives_checked: int
    failures: Tuple[Dict[str, Any], ...]

    # ------------------------------------------------------------------

    def check_all(
        self,
        pairs: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
    ) -> "DisciplineChecker":
        """Run the discipline over every pair in *pairs*.

        Parameters
        ----------
        pairs:
            Sequence of ``(left_section, right_section)`` dicts.

        Returns
        -------
        DisciplineChecker
            A new frozen instance with updated ``objectives_checked`` and
            ``failures`` fields.
        """
        new_failures = list(self.failures)
        checked = self.objectives_checked

        for left, right in pairs:
            pair_failures = self.discipline.check(left, right)
            checked += len(self.discipline.rules)
            for f in pair_failures:
                f_record = dict(f)
                f_record["left_id"] = left.get("section_id", "?")
                f_record["right_id"] = right.get("section_id", "?")
                new_failures.append(f_record)

        logger.info(
            "DisciplineChecker %r: checked %d objectives across %d pairs, %d failures",
            self.checker_id, checked, len(pairs), len(new_failures),
        )
        return replace(self, objectives_checked=checked, failures=tuple(new_failures))

    def report(self) -> Dict[str, Any]:
        """Produce a structured summary of the checker's state.

        Returns
        -------
        dict
            Contains ``"checker_id"``, ``"discipline_name"``, ``"objectives_checked"``,
            ``"failure_count"``, ``"failures"``, and ``"pass_rate"``.
        """
        total = self.objectives_checked
        fail_count = len(self.failures)
        pass_rate = 1.0 if total == 0 else max(0.0, (total - fail_count) / total)
        return {
            "checker_id": self.checker_id,
            "discipline_name": self.discipline.name,
            "objectives_checked": total,
            "failure_count": fail_count,
            "failures": list(self.failures),
            "pass_rate": round(pass_rate, 4),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (alias of :meth:`report`)."""
        return self.report()

    def reset(self) -> "DisciplineChecker":
        """Return a new checker with counters and failures cleared.

        Returns
        -------
        DisciplineChecker
            A new frozen instance with ``objectives_checked=0`` and
            ``failures=()``.
        """
        return replace(self, objectives_checked=0, failures=())


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _section_id(section: Dict[str, Any]) -> str:
    """Extract or synthesise a stable identifier for *section*."""
    sid = section.get("section_id") or section.get("id")
    if sid:
        return str(sid)
    raw = json.dumps(section, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _restrict(section: Dict[str, Any], region: Dict[str, Any]) -> Dict[str, Any]:
    """Restrict a section's data to the element ids in *region*.

    Parameters
    ----------
    section:
        The section dict, expected to have an ``"element_ids"`` list and a
        ``"data"`` dict mapping element id → value.
    region:
        A region dict with an ``"element_ids"`` key.

    Returns
    -------
    dict
        A new dict with ``"element_ids"`` and ``"data"`` projected onto the region.
    """
    region_ids: Set[str] = set(str(e) for e in region.get("element_ids", []))
    section_ids: Set[str] = set(str(e) for e in section.get("element_ids", []))
    common = sorted(region_ids & section_ids)

    section_data: Dict[str, Any] = section.get("data", {})
    restricted_data = {k: section_data[k] for k in common if k in section_data}

    return {
        "element_ids": common,
        "data": restricted_data,
        "dimension": section.get("dimension"),
        "orientation": section.get("orientation"),
    }


def _make_objective_id(left_id: str, right_id: str, obj_type: str) -> str:
    """Generate a deterministic objective id from its components."""
    raw = f"{left_id}:{right_id}:{obj_type}"
    return "obj-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_interface_discipline(
    cover: List[Dict[str, Any]],
    discipline: InterfaceDiscipline,
) -> Dict[str, Any]:
    """Scan every adjacent pair in *cover* for discipline violations.

    The cover is interpreted as an ordered list of sections.  All consecutive
    pairs ``(cover[i], cover[i+1])`` are checked, as well as all pairs that share
    at least one element id (non-consecutive overlaps).

    Parameters
    ----------
    cover:
        List of section dicts.  Each dict should have at least a ``"section_id"``
        and an ``"element_ids"`` list.
    discipline:
        The :class:`InterfaceDiscipline` to apply.

    Returns
    -------
    dict
        A result dict with keys:

        ``"passed"`` : bool
            ``True`` iff no objectives failed.
        ``"failed_objectives"`` : list[dict]
            Records for each failed objective.
        ``"gluing_violations"`` : list[dict]
            :class:`GluingCondition` dicts for pairs that failed gluing.
        ``"judgment"`` : dict
            A :class:`Judgment` summarising the overall verdict.
        ``"pair_count"`` : int
            Number of pairs examined.
        ``"obstruction"`` : dict or None
            A :class:`CechObstruction` if a triple-overlap cocycle failure is
            detected, else ``None``.
    """
    t0 = time.monotonic()
    logger.info(
        "check_interface_discipline: cover size=%d, discipline=%r",
        len(cover), discipline.name,
    )

    failed_objectives: List[Dict[str, Any]] = []
    gluing_violations: List[Dict[str, Any]] = []
    pairs_examined = 0

    # Build an index of pairs that share elements
    def _shares(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return bool(
            set(str(e) for e in a.get("element_ids", []))
            & set(str(e) for e in b.get("element_ids", []))
        )

    examined_pairs: List[Tuple[int, int]] = []
    for i in range(len(cover)):
        for j in range(i + 1, len(cover)):
            if _shares(cover[i], cover[j]):
                examined_pairs.append((i, j))

    # Also always include consecutive pairs
    for i in range(len(cover) - 1):
        pair = (i, i + 1)
        if pair not in examined_pairs:
            examined_pairs.append(pair)

    blocking_pairs: Set[Tuple[str, str]] = set()

    for i, j in examined_pairs:
        left, right = cover[i], cover[j]
        left_id = _section_id(left)
        right_id = _section_id(right)
        pairs_examined += 1

        # Build and evaluate overlap objectives
        objectives = build_overlap_objectives(left, right, discipline)
        for obj in objectives:
            evald = obj.evaluate(left, right)
            if evald.satisfied is False:
                failed_objectives.append(evald.to_dict())
                if evald.is_critical():
                    blocking_pairs.add((left_id, right_id))

        # Compute overlap region
        left_elem_ids = set(str(e) for e in left.get("element_ids", []))
        right_elem_ids = set(str(e) for e in right.get("element_ids", []))
        overlap_ids = sorted(left_elem_ids & right_elem_ids)
        overlap_region = {
            "element_ids": overlap_ids,
            "dimension": left.get("dimension"),
        }

        # Verify gluing condition on the overlap
        gc = verify_gluing_condition(left, right, overlap_region)
        if gc.is_satisfied is False:
            gluing_violations.append(gc.to_dict())
            blocking_pairs.add((left_id, right_id))

    # Detect triple-overlap Čech obstruction
    obstruction: Optional[CechObstruction] = None
    triples = list(itertools.combinations(range(len(cover)), 3))
    for ti, tj, tk in triples:
        a, b, c = cover[ti], cover[tj], cover[tk]
        ids_a = set(str(e) for e in a.get("element_ids", []))
        ids_b = set(str(e) for e in b.get("element_ids", []))
        ids_c = set(str(e) for e in c.get("element_ids", []))
        triple_ids = ids_a & ids_b & ids_c
        if not triple_ids:
            continue
        # Cocycle check: restriction of (a→b) ∘ (b→c) should equal (a→c)
        # Simplified: all three pairwise restrictions to the triple overlap must agree
        triple_region = {"element_ids": sorted(triple_ids)}
        ra = _restrict(a, triple_region)
        rb = _restrict(b, triple_region)
        rc = _restrict(c, triple_region)

        def _dicts_equal(x: Dict, y: Dict) -> bool:
            return x.get("data", {}) == y.get("data", {})

        ab_ok = _dicts_equal(ra, rb)
        bc_ok = _dicts_equal(rb, rc)
        ac_ok = _dicts_equal(ra, rc)

        if not (ab_ok and bc_ok and ac_ok):
            severity = sum([not ab_ok, not bc_ok, not ac_ok]) / 3.0
            aid, bid, cid = _section_id(a), _section_id(b), _section_id(c)
            obstruction = CechObstruction(
                obstruction_id="cech-" + uuid.uuid4().hex[:8],
                triple=(aid, bid, cid),
                cohomology_class="H1_nontrivial",
                blocking_pairs=frozenset(blocking_pairs),
                severity=severity,
                repair_candidates=("re-index", "adjust-orientation", "extend-overlap"),
            )
            logger.warning(
                "Čech 1-obstruction detected at triple (%s, %s, %s), severity=%.2f",
                aid, bid, cid, severity,
            )
            break

    passed = (len(failed_objectives) == 0 and len(gluing_violations) == 0)
    tier = TrustTier.WITNESSED if passed else TrustTier.PROPOSAL
    judgment = Judgment(
        judgment_id="jdg-" + uuid.uuid4().hex[:8],
        proposition=f"Cover of {len(cover)} sections satisfies discipline '{discipline.name}'",
        verdict=passed,
        trust_tier=tier,
        evidence_keys=tuple(
            f["objective_id"] for f in failed_objectives
        ),
        timestamp=time.time(),
        provenance="check_interface_discipline",
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "check_interface_discipline: done in %.4fs — passed=%s, failed_obj=%d, gluing_viol=%d",
        elapsed, passed, len(failed_objectives), len(gluing_violations),
    )

    return {
        "passed": passed,
        "failed_objectives": failed_objectives,
        "gluing_violations": gluing_violations,
        "judgment": judgment.to_dict(),
        "pair_count": pairs_examined,
        "obstruction": obstruction.to_dict() if obstruction else None,
    }


def build_overlap_objectives(
    left_section: Dict[str, Any],
    right_section: Dict[str, Any],
    discipline: InterfaceDiscipline,
) -> List[OverlapObjective]:
    """Derive :class:`OverlapObjective` instances for *left_section* / *right_section*.

    One objective is created for each rule in *discipline* whose predicate maps
    to a known objective type.  The returned list is ready to be evaluated with
    :meth:`OverlapObjective.evaluate`.

    Parameters
    ----------
    left_section:
        The left section dict.
    right_section:
        The right section dict.
    discipline:
        The interface discipline supplying the rules.

    Returns
    -------
    list[OverlapObjective]
        Unevaluated objectives (``satisfied=None``).
    """
    left_id = _section_id(left_section)
    right_id = _section_id(right_section)

    objectives: List[OverlapObjective] = []
    seen_types: Set[str] = set()

    for rule in discipline.rules:
        obj_type = rule.get("predicate", "")
        if not obj_type:
            continue
        # Deduplicate: one objective per type per pair
        dedup_key = f"{obj_type}"
        if dedup_key in seen_types:
            continue
        seen_types.add(dedup_key)

        constraint: Dict[str, Any] = {
            k: v for k, v in rule.items()
            if k not in ("rule_id", "predicate")
        }
        constraint["critical"] = bool(discipline.strictness_level >= 7 or rule.get("critical", False))

        obj = OverlapObjective(
            objective_id=_make_objective_id(left_id, right_id, obj_type),
            objective_type=obj_type,
            left_section_id=left_id,
            right_section_id=right_id,
            constraint=constraint,
            satisfied=None,
            evidence=(),
        )
        objectives.append(obj)
        logger.debug(
            "build_overlap_objectives: created objective %r for pair (%s, %s)",
            obj.objective_id, left_id, right_id,
        )

    return objectives


def verify_gluing_condition(
    left_section: Dict[str, Any],
    right_section: Dict[str, Any],
    overlap_region: Dict[str, Any],
) -> GluingCondition:
    """Build and immediately evaluate the gluing condition for one overlap.

    Parameters
    ----------
    left_section:
        The left section dict.
    right_section:
        The right section dict.
    overlap_region:
        Dict describing the overlap (must have ``"element_ids"`` key).

    Returns
    -------
    GluingCondition
        An evaluated (``is_satisfied`` populated) gluing condition.
    """
    left_id = _section_id(left_section)
    right_id = _section_id(right_section)

    left_restriction = _restrict(left_section, overlap_region)
    right_restriction = _restrict(right_section, overlap_region)

    raw = f"{left_id}:{right_id}:{json.dumps(overlap_region, sort_keys=True, default=str)}"
    condition_id = "gc-" + hashlib.sha1(raw.encode()).hexdigest()[:10]

    gc = GluingCondition(
        condition_id=condition_id,
        left_section_id=left_id,
        right_section_id=right_id,
        overlap_region=overlap_region,
        left_restriction=left_restriction,
        right_restriction=right_restriction,
        is_satisfied=None,
    )
    return gc.check()


def enforce_discipline(
    cover: List[Dict[str, Any]],
    violations: List[Any],
    discipline: InterfaceDiscipline,
) -> Tuple[List[Dict[str, Any]], List[InterfaceObligation]]:
    """Attempt to repair *cover* and produce obligations for unresolvable violations.

    For each violation this function first tries a simple automatic repair:

    * **dimension mismatch** — the right section's dimension is overwritten with
      the left section's dimension (canonical-direction normalisation).
    * **orientation incompatibility** — the right section's orientation is flipped.
    * **empty overlap** — the element-id sets are merged so the overlap is
      non-empty (structural repair).
    * **schema mismatch** — missing required keys are injected with a sentinel
      value ``None``.

    Violations that cannot be automatically repaired generate an
    :class:`InterfaceObligation` that must be manually discharged.

    Parameters
    ----------
    cover:
        The list of section dicts to repair in-place (copies are made).
    violations:
        List of failure records as returned by :func:`check_interface_discipline`.
        Each record must have at least ``"left_id"``, ``"right_id"``, and
        ``"rule_id"`` keys.
    discipline:
        The discipline under which the violations were detected.

    Returns
    -------
    tuple[list[dict], list[InterfaceObligation]]
        ``(repaired_cover, obligations)`` where *repaired_cover* is the patched
        cover and *obligations* is the list of outstanding proof obligations.
    """
    repaired: List[Dict[str, Any]] = [dict(s) for s in cover]
    obligations: List[InterfaceObligation] = []

    id_to_idx: Dict[str, int] = {
        _section_id(s): idx for idx, s in enumerate(repaired)
    }

    auto_repairs = 0
    obligation_count = 0

    for viol in violations:
        left_id = viol.get("left_id", "")
        right_id = viol.get("right_id", "")
        rule_id = viol.get("rule_id", "")
        reason = viol.get("reason", "")

        left_idx = id_to_idx.get(left_id)
        right_idx = id_to_idx.get(right_id)

        repaired_automatically = False

        if left_idx is not None and right_idx is not None:
            l = repaired[left_idx]
            r = repaired[right_idx]

            if "dimension" in rule_id or "dimension_agree" in reason:
                if l.get("dimension") is not None:
                    r = dict(r, dimension=l["dimension"])
                    repaired[right_idx] = r
                    repaired_automatically = True
                    logger.info("enforce_discipline: repaired dimension for %r", right_id)

            elif "orientation" in rule_id or "orientation" in reason:
                old_orient = r.get("orientation", 1)
                r = dict(r, orientation=-old_orient)
                repaired[right_idx] = r
                repaired_automatically = True
                logger.info("enforce_discipline: flipped orientation for %r", right_id)

            elif "overlap_nonempty" in rule_id or "overlap_nonempty" in reason:
                merged_ids = list(
                    set(l.get("element_ids", [])) | set(r.get("element_ids", []))
                )
                r = dict(r, element_ids=merged_ids)
                repaired[right_idx] = r
                repaired_automatically = True
                logger.info("enforce_discipline: merged element_ids for %r", right_id)

            elif "schema_match" in rule_id or "schema_match" in reason:
                required_keys: List[str] = []
                for rule in discipline.rules:
                    if rule.get("predicate") == "schema_match":
                        required_keys.extend(rule.get("required_keys", []))
                for k in required_keys:
                    if k not in l:
                        l = dict(l, **{k: None})
                        repaired[left_idx] = l
                    if k not in r:
                        r = dict(r, **{k: None})
                        repaired[right_idx] = r
                repaired_automatically = bool(required_keys)
                if repaired_automatically:
                    logger.info(
                        "enforce_discipline: injected missing schema keys %r for pair (%r, %r)",
                        required_keys, left_id, right_id,
                    )

        if not repaired_automatically:
            obl = InterfaceObligation(
                obligation_id="obl-" + uuid.uuid4().hex[:8],
                left_id=left_id,
                right_id=right_id,
                obligation_type=rule_id or "unknown",
                description=(
                    f"Manual proof required: discipline '{discipline.name}' rule '{rule_id}' "
                    f"violated between sections {left_id!r} and {right_id!r}. "
                    f"Reason: {reason}"
                ),
                discharged=False,
                proof=None,
            )
            obligations.append(obl)
            obligation_count += 1
            logger.warning(
                "enforce_discipline: could not auto-repair violation %r → obligation %r",
                rule_id, obl.obligation_id,
            )
        else:
            auto_repairs += 1

    logger.info(
        "enforce_discipline: %d auto-repairs, %d obligations outstanding",
        auto_repairs, obligation_count,
    )
    return repaired, obligations


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("interface_discipline_overlap_objec — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Build a simple discipline
    # ------------------------------------------------------------------
    discipline = InterfaceDiscipline(
        discipline_id="disc-001",
        name="basic_topology_discipline",
        rules=(
            {"rule_id": "r1", "predicate": "schema_match", "required_keys": ["dimension", "orientation", "element_ids"], "severity": 1.0},
            {"rule_id": "r2", "predicate": "overlap_nonempty", "severity": 0.9, "critical": True},
            {"rule_id": "r3", "predicate": "dimension_agree", "severity": 1.0},
            {"rule_id": "r4", "predicate": "orientation_compatible", "severity": 0.8},
            {"rule_id": "r5", "predicate": "boundary_shared", "severity": 0.5},
        ),
        strictness_level=8,
        version="1.0.0",
    )

    print(f"\n[1] Discipline: {discipline.name!r}, rules={len(discipline.rules)}, strict={discipline.is_strict()}")
    assert not discipline.is_strict(), "strictness_level=8 should NOT be strict (requires 10)"

    strict_disc = InterfaceDiscipline(
        discipline_id="disc-002",
        name="strict_discipline",
        rules=discipline.rules,
        strictness_level=10,
        version="1.0.0",
    )
    assert strict_disc.is_strict()
    print(f"    Strict discipline: {strict_disc.is_strict()}")

    # ------------------------------------------------------------------
    # 2. Add a rule
    # ------------------------------------------------------------------
    new_rule = {"rule_id": "r6", "predicate": "schema_match", "required_keys": ["metadata"]}
    extended = discipline.add_rule(new_rule)
    print(f"\n[2] add_rule: rules went from {len(discipline.rules)} to {len(extended.rules)}")
    assert len(extended.rules) == len(discipline.rules) + 1

    # ------------------------------------------------------------------
    # 3. TrustTier operations
    # ------------------------------------------------------------------
    print(f"\n[3] TrustTier lattice:")
    t = TrustTier.WITNESSED
    print(f"    {t}.promote() = {t.promote()}")
    print(f"    {t}.demote()  = {t.demote()}")
    print(f"    {t}.join(PROOF_BACKED) = {t.join(TrustTier.PROOF_BACKED)}")
    print(f"    {t}.meet(PROPOSAL)     = {t.meet(TrustTier.PROPOSAL)}")
    assert t.promote() == TrustTier.DISCHARGED
    assert t.demote() == TrustTier.PROPOSAL
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert t.meet(TrustTier.PROPOSAL) == TrustTier.PROPOSAL

    # ------------------------------------------------------------------
    # 4. Sections for a small cover
    # ------------------------------------------------------------------
    sec_a = {
        "section_id": "A",
        "element_ids": ["e1", "e2", "e3", "e4"],
        "dimension": 2,
        "orientation": 1,
        "boundary_ids": ["b1", "b2"],
        "data": {"e1": 0.1, "e2": 0.2, "e3": 0.3, "e4": 0.4},
    }
    sec_b = {
        "section_id": "B",
        "element_ids": ["e3", "e4", "e5", "e6"],
        "dimension": 2,
        "orientation": 1,
        "boundary_ids": ["b2", "b3"],
        "data": {"e3": 0.3, "e4": 0.4, "e5": 0.5, "e6": 0.6},
    }
    sec_c = {
        "section_id": "C",
        "element_ids": ["e5", "e6", "e7"],
        "dimension": 2,
        "orientation": 1,
        "boundary_ids": ["b3", "b4"],
        "data": {"e5": 0.5, "e6": 0.6, "e7": 0.7},
    }

    # ------------------------------------------------------------------
    # 5. build_overlap_objectives
    # ------------------------------------------------------------------
    print(f"\n[4] build_overlap_objectives(A, B):")
    objectives = build_overlap_objectives(sec_a, sec_b, discipline)
    print(f"    Created {len(objectives)} objectives")
    assert len(objectives) > 0
    for o in objectives:
        assert o.satisfied is None
        print(f"    - {o.objective_type} (id={o.objective_id[:16]}...)")

    # ------------------------------------------------------------------
    # 6. Evaluate objectives
    # ------------------------------------------------------------------
    print(f"\n[5] evaluate objectives:")
    for o in objectives:
        evald = o.evaluate(sec_a, sec_b)
        print(f"    {evald.objective_type}: satisfied={evald.satisfied}, evidence={evald.evidence}")

    # ------------------------------------------------------------------
    # 7. verify_gluing_condition — compatible pair
    # ------------------------------------------------------------------
    print(f"\n[6] verify_gluing_condition(A, B) — should satisfy:")
    overlap_ab = {"element_ids": ["e3", "e4"]}
    gc_ab = verify_gluing_condition(sec_a, sec_b, overlap_ab)
    print(f"    is_satisfied={gc_ab.is_satisfied}")
    assert gc_ab.is_satisfied is True
    assert gc_ab.witness() is None

    # ------------------------------------------------------------------
    # 8. verify_gluing_condition — incompatible pair
    # ------------------------------------------------------------------
    sec_b_bad = dict(sec_b, data={"e3": 999.0, "e4": 0.4, "e5": 0.5, "e6": 0.6})
    gc_bad = verify_gluing_condition(sec_a, sec_b_bad, overlap_ab)
    print(f"\n[7] verify_gluing_condition(A, B_bad) — should fail:")
    print(f"    is_satisfied={gc_bad.is_satisfied}")
    print(f"    witness={gc_bad.witness()}")
    assert gc_bad.is_satisfied is False
    assert gc_bad.witness() is not None

    # ------------------------------------------------------------------
    # 9. check_interface_discipline on clean cover
    # ------------------------------------------------------------------
    print(f"\n[8] check_interface_discipline on clean cover [A, B, C]:")
    result = check_interface_discipline([sec_a, sec_b, sec_c], discipline)
    print(f"    passed={result['passed']}")
    print(f"    pair_count={result['pair_count']}")
    print(f"    failed_objectives={len(result['failed_objectives'])}")
    print(f"    gluing_violations={len(result['gluing_violations'])}")
    print(f"    obstruction={result['obstruction']}")

    # ------------------------------------------------------------------
    # 10. check_interface_discipline on dirty cover
    # ------------------------------------------------------------------
    sec_b_dirty = {
        "section_id": "B_dirty",
        "element_ids": ["e3", "e4", "e5", "e6"],
        "dimension": 3,          # mismatch with A (dim=2)
        "orientation": -1,        # incompatible orientation
        "boundary_ids": [],       # no shared boundary
        "data": {"e3": 999.0, "e4": 0.4, "e5": 0.5, "e6": 0.6},
    }
    print(f"\n[9] check_interface_discipline on dirty cover [A, B_dirty]:")
    result_dirty = check_interface_discipline([sec_a, sec_b_dirty], discipline)
    print(f"    passed={result_dirty['passed']}")
    print(f"    failed_objectives={len(result_dirty['failed_objectives'])}")
    print(f"    gluing_violations={len(result_dirty['gluing_violations'])}")
    for fo in result_dirty["failed_objectives"]:
        print(f"      - [{fo['rule_id']}] {fo['reason'][:60]}")

    # ------------------------------------------------------------------
    # 11. DisciplineChecker
    # ------------------------------------------------------------------
    print(f"\n[10] DisciplineChecker:")
    checker = DisciplineChecker(
        checker_id="chkr-001",
        discipline=discipline,
        objectives_checked=0,
        failures=(),
    )
    pairs = [(sec_a, sec_b), (sec_b, sec_c), (sec_a, sec_b_dirty)]
    checker2 = checker.check_all(pairs)
    report = checker2.report()
    print(f"    objectives_checked={report['objectives_checked']}")
    print(f"    failure_count={report['failure_count']}")
    print(f"    pass_rate={report['pass_rate']}")
    checker3 = checker2.reset()
    assert checker3.objectives_checked == 0
    assert checker3.failures == ()
    print(f"    after reset: objectives_checked={checker3.objectives_checked}")

    # ------------------------------------------------------------------
    # 12. enforce_discipline
    # ------------------------------------------------------------------
    print(f"\n[11] enforce_discipline:")
    violations = result_dirty["failed_objectives"]
    repaired_cover, obligations = enforce_discipline(
        [sec_a, sec_b_dirty], violations, discipline
    )
    print(f"    repaired_cover has {len(repaired_cover)} sections")
    print(f"    outstanding obligations: {len(obligations)}")
    for obl in obligations:
        print(f"      - [{obl.obligation_type}] {obl.description[:60]}...")
        assert obl.requires_proof()
        discharged_obl = obl.discharge({"cert": "manual_review_v1"})
        assert not discharged_obl.requires_proof()
        print(f"        discharged: {not discharged_obl.requires_proof()}")

    # ------------------------------------------------------------------
    # 13. InterfaceObligation double-discharge guard
    # ------------------------------------------------------------------
    print(f"\n[12] InterfaceObligation double-discharge guard:")
    dummy_obl = InterfaceObligation(
        obligation_id="obl-test",
        left_id="A", right_id="B",
        obligation_type="test",
        description="test obligation",
        discharged=False,
        proof=None,
    )
    d1 = dummy_obl.discharge("proof_v1")
    try:
        d1.discharge("proof_v2")
        print("    ERROR: should have raised on double-discharge")
    except (JuGeoError, ValueError) as exc:
        print(f"    Correctly raised on double-discharge: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # 14. CechObstruction
    # ------------------------------------------------------------------
    print(f"\n[13] CechObstruction:")
    obs = CechObstruction(
        obstruction_id="cech-test",
        triple=("A", "B", "C"),
        cohomology_class="trivial",
        blocking_pairs=frozenset(),
        severity=0.0,
        repair_candidates=(),
    )
    print(f"    trivial obstruction: {obs.is_trivial()}")
    assert obs.is_trivial()
    obs2 = CechObstruction(
        obstruction_id="cech-test2",
        triple=("A", "B_dirty", "C"),
        cohomology_class="H1_nontrivial",
        blocking_pairs=frozenset([("A", "B_dirty")]),
        severity=0.67,
        repair_candidates=("re-index",),
    )
    print(f"    non-trivial obstruction: is_trivial={obs2.is_trivial()}, severity={obs2.severity}")
    assert not obs2.is_trivial()

    # ------------------------------------------------------------------
    # 15. Judgment
    # ------------------------------------------------------------------
    print(f"\n[14] Judgment:")
    jdg = Judgment(
        judgment_id="jdg-test",
        proposition="Test proposition",
        verdict=True,
        trust_tier=TrustTier.CERTIFIED,
        evidence_keys=("ev1", "ev2"),
        timestamp=time.time(),
        provenance="smoke_test",
    )
    print(f"    is_trusted(DISCHARGED)={jdg.is_trusted(TrustTier.DISCHARGED)}")
    print(f"    is_trusted(PROOF_BACKED)={jdg.is_trusted(TrustTier.PROOF_BACKED)}")
    assert jdg.is_trusted(TrustTier.DISCHARGED)
    assert not jdg.is_trusted(TrustTier.PROOF_BACKED)

    # ------------------------------------------------------------------
    # 16. Serialisation round-trip
    # ------------------------------------------------------------------
    print(f"\n[15] Serialisation round-trip (JSON):")
    for obj_name, obj in [
        ("discipline", discipline),
        ("judgment", jdg),
        ("obstruction", obs2),
    ]:
        d = obj.to_dict()
        s = json.dumps(d, default=str)
        assert len(s) > 10
        print(f"    {obj_name}: {len(s)} bytes JSON")

    print(f"\n{'=' * 70}")
    print("All smoke-test assertions passed ✓")
    print("=" * 70)
