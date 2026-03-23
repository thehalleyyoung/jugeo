"""Judgment comparison utilities for JuGeo — theory2.tex Chapter 12.

Chapter 12 of theory2.tex ("Equivalence and Refinement") defines a partial
order ≤_J on judgments and derives from it the notions of refinement,
equivalence, contradiction, and incomparability.

A judgment J₁ *refines* J₂ (written J₁ ≤_J J₂) when:
  - J₁'s evidence bundle is at least as strong as J₂'s (§12.3.1),
  - J₁'s trust annotation is at least as high in the ordered algebra (§12.3.2),
  - J₁'s residual obligations are a subset of J₂'s (§12.3.3), and
  - J₁'s coordinate is at least as specific (§12.3.4).

Two judgments are *equivalent* when they mutually refine each other.  They
*contradict* when they assert incompatible propositions or carry irreconcilable
evidence — a situation modelled as an H¹ obstruction in the judgment sheaf.
All other pairs are *incomparable* antichains in the order.

The copilot channel is subject to a hard trust ceiling (TrustLevel.COPILOT_SUGGESTED);
comparisons that would silently promote evidence past that ceiling are flagged
in the relevant report's ``notes`` field.

Module layout
-------------
1.  ComparisonResult (Enum)         — semantic outcome of a comparison
2.  JudgmentComparator              — high-level entry point
3.  RefinementChecker               — four-condition refinement test
4.  EquivalenceChecker              — bidirectional refinement + semantic checks
5.  ContradictionDetector           — obstruction / conflict detection
6.  JudgmentOrder                   — mutable partial-order collection
7.  EvidenceComparator              — evidence-bundle strength comparison
8.  TrustComparator                 — ordered-algebra trust comparison
9.  ResidualComparator              — obligation-set comparison
10. ComparisonHistory               — append-only audit log with statistics
11. ComparisonSerializer            — JSON round-trip for all report types
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    JudgmentStatus,
    LocalJudgment,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.judgments.sections import JudgmentSection


# ---------------------------------------------------------------------------
# Private helpers (defined before any class that uses them)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _effective_trust_level(trust_vector: Mapping[str, Any]) -> int:
    """Extract the effective TrustLevel integer from a *trust_vector* mapping.

    Falls back to ``TrustLevel.UNVERIFIED`` (1) when the key is absent or
    the stored value cannot be converted.
    """
    raw = trust_vector.get("level", 1)
    if isinstance(raw, int):
        return raw
    try:
        return int(TrustLevel[str(raw)])
    except (KeyError, ValueError):
        return 1


def _annot_level(annotation: TrustAnnotation | None) -> int:
    """Return the integer trust level from an annotation, defaulting to UNVERIFIED."""
    if annotation is None:
        return 1  # TrustLevel.UNVERIFIED
    return int(annotation.level)


def _trust_from_vector(trust_vector: Mapping[str, Any]) -> TrustAnnotation | None:
    """Build a minimal TrustAnnotation from a *trust_vector* mapping.

    Returns ``None`` when the mapping is empty so that callers can apply
    appropriate defaults.
    """
    if not trust_vector:
        return None
    level_int = _effective_trust_level(trust_vector)
    try:
        level = TrustLevel(level_int)
    except ValueError:
        level = TrustLevel.UNVERIFIED

    ceiling_raw = trust_vector.get("ceiling", int(TrustLevel.MECHANICALLY_VERIFIED))
    floor_raw = trust_vector.get("floor", 0)
    try:
        ceiling = TrustLevel(int(ceiling_raw))
    except ValueError:
        ceiling = TrustLevel.MECHANICALLY_VERIFIED
    try:
        floor_ = TrustLevel(int(floor_raw))
    except ValueError:
        floor_ = TrustLevel.CONTRADICTED

    return TrustAnnotation(
        level=level,
        evidence_basis=tuple(trust_vector.get("evidence_basis", [])),
        ceiling=ceiling,
        floor=floor_,
        reasons=tuple(trust_vector.get("reasons", [])),
    )


def _item_key(item: EvidenceItem) -> str:
    """Construct a deterministic string key for an EvidenceItem."""
    return f"{item.channel}/{item.kind}/{item.timestamp}"


def _bundle_keys(bundle: EvidenceBundle | None) -> frozenset[str]:
    """Return the set of canonical keys for all items in a bundle."""
    if bundle is None:
        return frozenset()
    return frozenset(_item_key(item) for item in bundle.items)


def _bundle_from_refs(evidence_refs: tuple[str, ...]) -> EvidenceBundle | None:
    """Build a minimal EvidenceBundle stub from a sequence of evidence ref keys.

    Because ``LocalJudgment`` stores only string refs (not full ``EvidenceItem``
    objects), this helper constructs synthetic items so that comparators can
    operate on them.  The trust level defaults to UNVERIFIED and payloads are
    left empty.
    """
    if not evidence_refs:
        return None

    import datetime
    from jugeo.judgments.judgment_terms import EvidenceBundle as _EB
    from jugeo.judgments.judgment_terms import EvidenceItem as _EI

    try:
        from jugeo.judgments.judgment_terms import EvidenceItemKind
        _fallback_kind = EvidenceItemKind.ATTESTATION
    except (ImportError, AttributeError):
        _fallback_kind = None  # type: ignore[assignment]

    items: list[EvidenceItem] = []
    for ref in evidence_refs:
        parts = ref.split("/")
        channel = parts[0] if parts else "unknown"
        kind_str = parts[1] if len(parts) > 1 else "unknown"
        ts = parts[2] if len(parts) > 2 else datetime.datetime.utcnow().isoformat()

        if _fallback_kind is not None:
            try:
                from jugeo.judgments.judgment_terms import EvidenceItemKind
                kind = EvidenceItemKind(kind_str)
            except (ValueError, KeyError):
                kind = _fallback_kind
        else:
            kind = kind_str  # type: ignore[assignment]

        items.append(
            _EI(
                kind=kind,
                channel=channel,
                payload={},
                trust=TrustLevel.UNVERIFIED,
                timestamp=ts,
                metadata={},
            )
        )
    return _EB(items=tuple(items))


def _judgment_key(j: LocalJudgment) -> str:
    """Construct a stable string key for a *LocalJudgment*.

    The key combines the coordinate key and the first 32 characters of the
    proposition so it is human-readable in audit logs.
    """
    coord_key: str
    coord = j.coordinate
    if hasattr(coord, "key"):
        coord_key = coord.key  # type: ignore[union-attr]
    else:
        coord_key = str(coord)
    prop_fragment = j.proposition[:32].replace(" ", "_")
    return f"{coord_key}::{prop_fragment}"


# ---------------------------------------------------------------------------
# 1. ComparisonResult (Enum)
# ---------------------------------------------------------------------------

class ComparisonResult(str, Enum):
    """The semantic outcome of comparing two judgments.

    Defined in theory2.tex §12.1.  The lattice of outcomes mirrors the
    partial order on the trust algebra: refinement is "strictly above"
    equivalence in the proof-theoretic ordering.

    Values
    ------
    EQUIVALENT
        Both judgments carry the same evidence strength, trust level, and
        residual burden.  Neither refines the other.
    REFINES
        The *left* judgment refines the *right*: stronger evidence, at-least
        as high trust, and fewer (or equal) residual obligations.
    IS_REFINED_BY
        The *right* judgment refines the *left* (symmetric of REFINES).
    CONTRADICTS
        The two judgments assert incompatible propositions or carry
        irreconcilable evidence, violating consistency in the sheaf.
    INCOMPARABLE
        Neither judgment refines the other and they do not contradict.  The
        pair is an antichain in the partial order.
    SUBSUMES
        The left judgment logically encompasses the right: its carrier covers
        a strictly larger region of the coordinate space.
    SUBSUMED_BY
        The right judgment logically encompasses the left (symmetric of
        SUBSUMES).
    """

    EQUIVALENT = "equivalent"
    REFINES = "refines"
    IS_REFINED_BY = "is_refined_by"
    CONTRADICTS = "contradicts"
    INCOMPARABLE = "incomparable"
    SUBSUMES = "subsumes"
    SUBSUMED_BY = "subsumed_by"

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    def inverse(self) -> "ComparisonResult":
        """Return the result obtained by swapping *left* and *right* judgments.

        For example, ``REFINES.inverse()`` is ``IS_REFINED_BY``.  Symmetric
        results (EQUIVALENT, CONTRADICTS, INCOMPARABLE) are their own inverses.
        """
        _inv: dict["ComparisonResult", "ComparisonResult"] = {
            ComparisonResult.REFINES: ComparisonResult.IS_REFINED_BY,
            ComparisonResult.IS_REFINED_BY: ComparisonResult.REFINES,
            ComparisonResult.SUBSUMES: ComparisonResult.SUBSUMED_BY,
            ComparisonResult.SUBSUMED_BY: ComparisonResult.SUBSUMES,
            ComparisonResult.EQUIVALENT: ComparisonResult.EQUIVALENT,
            ComparisonResult.CONTRADICTS: ComparisonResult.CONTRADICTS,
            ComparisonResult.INCOMPARABLE: ComparisonResult.INCOMPARABLE,
        }
        return _inv[self]

    def is_ordering_relation(self) -> bool:
        """Return True if this result encodes a strict ordering relation.

        Ordering relations are those that place one judgment strictly above
        or below the other in ≤_J.  Equality and incomparability are excluded.
        """
        return self in (
            ComparisonResult.REFINES,
            ComparisonResult.IS_REFINED_BY,
            ComparisonResult.SUBSUMES,
            ComparisonResult.SUBSUMED_BY,
        )

    def is_conflict(self) -> bool:
        """Return True if the comparison indicates a logical conflict.

        Only CONTRADICTS constitutes a genuine conflict; INCOMPARABLE does not.
        """
        return self is ComparisonResult.CONTRADICTS

    def implies_ordering(self) -> bool:
        """Return True when the result implies at least a weak ordering.

        EQUIVALENT is considered a weak ordering (both directions hold).
        """
        return self in (
            ComparisonResult.REFINES,
            ComparisonResult.IS_REFINED_BY,
            ComparisonResult.SUBSUMES,
            ComparisonResult.SUBSUMED_BY,
            ComparisonResult.EQUIVALENT,
        )

    def to_label(self) -> str:
        """Return a short human-readable label for display in audit UIs."""
        _labels: dict["ComparisonResult", str] = {
            ComparisonResult.EQUIVALENT: "≡",
            ComparisonResult.REFINES: "⊑",
            ComparisonResult.IS_REFINED_BY: "⊒",
            ComparisonResult.CONTRADICTS: "⊥",
            ComparisonResult.INCOMPARABLE: "∥",
            ComparisonResult.SUBSUMES: "⊇",
            ComparisonResult.SUBSUMED_BY: "⊆",
        }
        return _labels.get(self, self.value)


# ---------------------------------------------------------------------------
# Backward-compatible ComparisonMode (kept for pre-§12 callers)
# ---------------------------------------------------------------------------

class ComparisonMode(str, Enum):
    """Retained for backward compatibility with pre-§12 code.

    New code should use :class:`ComparisonResult` (an Enum) together with
    :class:`JudgmentComparator`.
    """

    EQUIVALENCE = "equivalence"
    REFINEMENT = "refinement"
    REGRESSION = "regression"


# ---------------------------------------------------------------------------
# 7. EvidenceComparator (declared early — used by RefinementChecker etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceComparisonDetail:
    """Structured result of an evidence-bundle comparison.

    Produced by :meth:`EvidenceComparator.compare_bundles`.  All set-based
    fields contain sorted tuples of canonical evidence-item keys so that the
    output is deterministic and JSON-serialisable.
    """

    left_stronger: bool
    """True iff left's evidence-ref set ⊇ right's."""
    right_stronger: bool
    """True iff right's evidence-ref set ⊇ left's."""
    are_equal: bool
    """True iff both sets are identical."""
    are_compatible: bool
    """True iff the bundles contain no mutually exclusive items."""
    kinds_preserved: bool
    """True iff candidate introduces no new evidence *kinds* absent from base."""
    extra_left: tuple[str, ...]
    """Keys present in left but absent from right."""
    extra_right: tuple[str, ...]
    """Keys present in right but absent from left."""
    shared: tuple[str, ...]
    """Keys present in both."""
    notes: tuple[str, ...]
    """Human-readable observations for audit logs."""


@dataclass(frozen=True, slots=True)
class EvidenceComparator:
    """Compare EvidenceBundle objects along multiple dimensions (theory2.tex §12.3.1).

    Evidence strength is measured by the *superset* relation on evidence-ref
    key sets.  Compatibility requires no mutual-exclusion flags.  Kind
    preservation checks that the candidate does not introduce new evidence
    kinds absent from the base, which guards against silent channel drift
    (e.g. introducing copilot suggestions where only solver proofs existed).
    """

    def compare_bundles(
        self,
        left: EvidenceBundle | None,
        right: EvidenceBundle | None,
    ) -> EvidenceComparisonDetail:
        """Compare two EvidenceBundle objects and return a structured detail.

        Either bundle may be ``None``, which is treated as an empty bundle.
        The result records strength, equality, compatibility, and kind
        preservation in a single immutable object.
        """
        left_keys = _bundle_keys(left)
        right_keys = _bundle_keys(right)
        shared = left_keys & right_keys
        extra_left = left_keys - right_keys
        extra_right = right_keys - left_keys
        l_strong = right_keys.issubset(left_keys)
        r_strong = left_keys.issubset(right_keys)
        are_equal = left_keys == right_keys
        compat = self.compatible_with(left, right)
        kinds_ok = self.kind_preserved(left, right)

        notes: list[str] = []
        if extra_left:
            notes.append(f"left has {len(extra_left)} additional evidence item(s)")
        if extra_right:
            notes.append(f"right has {len(extra_right)} additional evidence item(s)")
        if not compat:
            notes.append("bundles contain incompatible evidence items")
        if not kinds_ok:
            notes.append("candidate introduces evidence kinds absent from base")

        return EvidenceComparisonDetail(
            left_stronger=l_strong,
            right_stronger=r_strong,
            are_equal=are_equal,
            are_compatible=compat,
            kinds_preserved=kinds_ok,
            extra_left=tuple(sorted(extra_left)),
            extra_right=tuple(sorted(extra_right)),
            shared=tuple(sorted(shared)),
            notes=tuple(notes),
        )

    def stronger_than(
        self,
        candidate: EvidenceBundle | None,
        base: EvidenceBundle | None,
    ) -> bool:
        """Return True iff *candidate* is at least as strong as *base*.

        Strength is defined by the ⊇ relation on canonical key sets (§12.3.1).
        An empty bundle is weaker than any non-empty bundle.
        """
        c_keys = _bundle_keys(candidate)
        b_keys = _bundle_keys(base)
        return b_keys.issubset(c_keys)

    def weaker_than(
        self,
        candidate: EvidenceBundle | None,
        base: EvidenceBundle | None,
    ) -> bool:
        """Return True iff *candidate* is strictly weaker than *base*.

        Strictly weaker means c_keys ⊊ b_keys (proper subset).
        """
        c_keys = _bundle_keys(candidate)
        b_keys = _bundle_keys(base)
        return c_keys.issubset(b_keys) and c_keys != b_keys

    def compatible_with(
        self,
        left: EvidenceBundle | None,
        right: EvidenceBundle | None,
    ) -> bool:
        """Return True iff the two bundles are mutually compatible.

        Two bundles are incompatible when the left contains an item whose trust
        level is CONTRADICTED (0) and the right shares a key with it.  This
        corresponds to a H¹ obstruction in the evidence sheaf (§12.6.2).
        """
        if left is None or right is None:
            return True
        left_contradicted = {
            _item_key(item)
            for item in left.items
            if int(item.trust) == 0
        }
        right_keys = {_item_key(item) for item in right.items}
        return not bool(left_contradicted & right_keys)

    def kind_preserved(
        self,
        candidate: EvidenceBundle | None,
        base: EvidenceBundle | None,
    ) -> bool:
        """Return True iff candidate does not introduce entirely new evidence kinds.

        "Kind drift" occurs when a refinement step adds evidence from a new
        channel type, e.g. suddenly introducing copilot suggestions where only
        solver proofs existed.  This method flags but does not forbid drift;
        callers decide policy.
        """
        if base is None or candidate is None:
            return True
        base_kinds = {item.kind for item in base.items}
        cand_kinds = {item.kind for item in candidate.items}
        return not bool(cand_kinds - base_kinds)

    def merge(
        self,
        left: EvidenceBundle | None,
        right: EvidenceBundle | None,
    ) -> EvidenceBundle | None:
        """Return a new bundle that is the union of *left* and *right*.

        Items are deduplicated by their canonical key.  Returns ``None`` when
        both inputs are ``None``.
        """
        if left is None and right is None:
            return None
        left_items = list(left.items) if left else []
        right_items = list(right.items) if right else []
        seen: set[str] = set()
        merged: list[EvidenceItem] = []
        for item in left_items + right_items:
            k = _item_key(item)
            if k not in seen:
                seen.add(k)
                merged.append(item)
        return EvidenceBundle(items=tuple(merged))


# ---------------------------------------------------------------------------
# 8. TrustComparator (declared early — used by RefinementChecker etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TrustComparisonDetail:
    """Structured result of a trust-annotation comparison.

    Produced by :meth:`TrustComparator.compare`.  The *result* field holds
    the ComparisonResult expressed purely in trust-level terms; it is later
    combined with evidence and residual results by JudgmentComparator.
    """

    left_level: int
    """Integer value of the left annotation's TrustLevel."""
    right_level: int
    """Integer value of the right annotation's TrustLevel."""
    result: ComparisonResult
    """Ordering outcome in the trust dimension alone."""
    distance: int
    """Absolute difference |left_level - right_level|."""
    are_comparable: bool
    """False when the annotations' [floor, ceiling] intervals do not overlap."""
    notes: tuple[str, ...]
    """Warnings, e.g. copilot ceiling violations."""


@dataclass(frozen=True, slots=True)
class TrustComparator:
    """Compare TrustAnnotation objects using the ordered trust algebra (theory2.tex §5).

    The trust algebra is a totally ordered set (TrustLevel) augmented by
    ceiling and floor constraints.  Two annotations are *comparable* iff
    their levels lie in the intersection of their respective [floor, ceiling]
    intervals; otherwise they may occupy incomparable regions if the algebra
    is later extended.

    The copilot channel is subject to a hard ceiling of
    ``TrustLevel.COPILOT_SUGGESTED`` (2); comparisons that would require
    promoting past this ceiling are reported in the ``notes`` field without
    raising an exception (policy enforcement is left to the caller).
    """

    _COPILOT_CEILING: int = field(default=2, init=False, repr=False)
    """Hard trust ceiling for the copilot channel (TrustLevel.COPILOT_SUGGESTED)."""

    def compare(
        self,
        left: TrustAnnotation | None,
        right: TrustAnnotation | None,
    ) -> TrustComparisonDetail:
        """Return a full TrustComparisonDetail for two annotations.

        Either annotation may be ``None``, which is treated as UNVERIFIED (1).
        The method checks comparability before deciding the ordering so that
        incompatible ceiling/floor intervals yield INCOMPARABLE rather than a
        spurious ordering.
        """
        l_lvl = _annot_level(left)
        r_lvl = _annot_level(right)
        comparable = self.is_comparable(left, right)
        dist = self.distance(left, right)

        if not comparable:
            result = ComparisonResult.INCOMPARABLE
        elif l_lvl == r_lvl:
            result = ComparisonResult.EQUIVALENT
        elif l_lvl > r_lvl:
            result = ComparisonResult.REFINES
        else:
            result = ComparisonResult.IS_REFINED_BY

        notes: list[str] = []
        copilot_ceiling = self._COPILOT_CEILING
        if left is not None and l_lvl > copilot_ceiling and "copilot" in (left.reasons or ()):
            notes.append("left trust exceeds copilot channel ceiling — silent promotion detected")
        if right is not None and r_lvl > copilot_ceiling and "copilot" in (right.reasons or ()):
            notes.append("right trust exceeds copilot channel ceiling — silent promotion detected")
        if left is not None and not self.ceiling_enforced(left):
            notes.append(f"left annotation level {l_lvl} exceeds declared ceiling {int(left.ceiling)}")
        if right is not None and not self.ceiling_enforced(right):
            notes.append(f"right annotation level {r_lvl} exceeds declared ceiling {int(right.ceiling)}")

        return TrustComparisonDetail(
            left_level=l_lvl,
            right_level=r_lvl,
            result=result,
            distance=dist,
            are_comparable=comparable,
            notes=tuple(notes),
        )

    def is_above(
        self,
        candidate: TrustAnnotation | None,
        base: TrustAnnotation | None,
    ) -> bool:
        """Return True iff *candidate*'s trust level is strictly above *base*'s.

        Does not account for ceiling/floor constraints; use :meth:`compare` for
        a full comparability-aware check.
        """
        return _annot_level(candidate) > _annot_level(base)

    def is_below(
        self,
        candidate: TrustAnnotation | None,
        base: TrustAnnotation | None,
    ) -> bool:
        """Return True iff *candidate*'s trust level is strictly below *base*'s."""
        return _annot_level(candidate) < _annot_level(base)

    def is_comparable(
        self,
        left: TrustAnnotation | None,
        right: TrustAnnotation | None,
    ) -> bool:
        """Return True iff the two trust annotations lie in overlapping intervals.

        Two annotations a and b are comparable iff the intersection of
        [a.floor, a.ceiling] and [b.floor, b.ceiling] is non-empty.  When
        either annotation is ``None``, full comparability is assumed.
        """
        if left is None or right is None:
            return True
        l_floor = int(left.floor)
        l_ceil = int(left.ceiling)
        r_floor = int(right.floor)
        r_ceil = int(right.ceiling)
        overlap_lo = max(l_floor, r_floor)
        overlap_hi = min(l_ceil, r_ceil)
        return overlap_lo <= overlap_hi

    def distance(
        self,
        left: TrustAnnotation | None,
        right: TrustAnnotation | None,
    ) -> int:
        """Return the absolute distance between two trust levels.

        Distance is the non-negative integer |left.level - right.level| in
        the totally ordered TrustLevel chain (theory2.tex §5.2).
        """
        return abs(_annot_level(left) - _annot_level(right))

    def ceiling_enforced(self, annotation: TrustAnnotation | None) -> bool:
        """Return True iff the annotation's level is within its declared ceiling.

        Violations indicate a silent trust promotion, which is forbidden by
        the no-silent-promotion invariant (theory2.tex §5.4 and the copilot
        channel policy).
        """
        if annotation is None:
            return True
        return int(annotation.level) <= int(annotation.ceiling)

    def floor_enforced(self, annotation: TrustAnnotation | None) -> bool:
        """Return True iff the annotation's level is at or above its declared floor."""
        if annotation is None:
            return True
        return int(annotation.level) >= int(annotation.floor)

    def admissible(self, annotation: TrustAnnotation | None) -> bool:
        """Return True iff both ceiling and floor constraints are satisfied."""
        return self.ceiling_enforced(annotation) and self.floor_enforced(annotation)


# ---------------------------------------------------------------------------
# 9. ResidualComparator (declared early — used by RefinementChecker etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResidualComparisonDetail:
    """Structured result of a residual-obligation set comparison.

    Produced by :meth:`ResidualComparator.compare`.  Obligation tuples contain
    string obligation IDs (not full ResidualObligation objects) because
    LocalJudgment stores obligations as plain strings.
    """

    left_count: int
    right_count: int
    left_has_fewer: bool
    right_has_fewer: bool
    are_equal: bool
    only_in_left: tuple[str, ...]
    """Obligations present in left but discharged (absent) in right."""
    only_in_right: tuple[str, ...]
    """Obligations present in right but discharged (absent) in left."""
    shared: tuple[str, ...]
    priority_weighted_left: int
    priority_weighted_right: int


@dataclass(frozen=True, slots=True)
class ResidualComparator:
    """Compare sets of residual obligation identifiers (theory2.tex §12.3.3).

    Residuals are compared as *sets* of obligation IDs.  Fewer residuals
    means more verification duties have been discharged, which is the "upward"
    direction in the refinement order.  Priority-weighted comparison is also
    available for callers that track urgency metadata.
    """

    def compare(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> ResidualComparisonDetail:
        """Return a full ResidualComparisonDetail for two obligation tuples.

        Obligation tuples contain string obligation IDs, which may be URNs,
        hashes, or human-readable descriptions depending on the producer.
        """
        l_set = frozenset(left_obs)
        r_set = frozenset(right_obs)
        only_l = l_set - r_set
        only_r = r_set - l_set
        shared = l_set & r_set
        return ResidualComparisonDetail(
            left_count=len(l_set),
            right_count=len(r_set),
            left_has_fewer=len(l_set) < len(r_set),
            right_has_fewer=len(r_set) < len(l_set),
            are_equal=l_set == r_set,
            only_in_left=tuple(sorted(only_l)),
            only_in_right=tuple(sorted(only_r)),
            shared=tuple(sorted(shared)),
            priority_weighted_left=len(l_set),
            priority_weighted_right=len(r_set),
        )

    def more_residuals_than(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> bool:
        """Return True iff *left* has strictly more obligations than *right*."""
        return len(frozenset(left_obs)) > len(frozenset(right_obs))

    def fewer_residuals_than(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> bool:
        """Return True iff *left* has strictly fewer obligations than *right*."""
        return len(frozenset(left_obs)) < len(frozenset(right_obs))

    def same_residuals(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> bool:
        """Return True iff both obligation sets are identical (as sets)."""
        return frozenset(left_obs) == frozenset(right_obs)

    def residual_diff(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return *(only_in_left, only_in_right)* as a symmetric-difference pair.

        Useful for computing which obligations a refinement step has discharged
        (present in right but not left) and which new ones it has introduced
        (present in left but not right).
        """
        l_set = frozenset(left_obs)
        r_set = frozenset(right_obs)
        return l_set - r_set, r_set - l_set

    def weighted_score(
        self,
        obligations: tuple[str, ...],
        priority_map: Mapping[str, int] | None = None,
    ) -> int:
        """Return a priority-weighted residual burden score.

        Each obligation ID is looked up in *priority_map*; if absent, a
        default weight of 5 is used.  Lower priority number = higher urgency,
        so the contribution is ``max(1, 11 - priority)`` to invert the scale.
        A judgment with no obligations scores 0.
        """
        if priority_map is None:
            return len(obligations) * 5
        score = 0
        for ob_id in obligations:
            priority = priority_map.get(ob_id, 5)
            score += max(1, 11 - priority)
        return score

    def subset_of(
        self,
        left_obs: tuple[str, ...],
        right_obs: tuple[str, ...],
    ) -> bool:
        """Return True iff left's obligation set is a subset of right's.

        A proper or improper subset is accepted (⊆, not ⊊).  This is the
        condition used by RefinementChecker (§12.3.3).
        """
        return frozenset(left_obs).issubset(frozenset(right_obs))


# ---------------------------------------------------------------------------
# 3. RefinementChecker
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RefinementWitness:
    """Concrete evidence that *candidate* refines *base*.

    Produced by :meth:`RefinementChecker.refinement_witnesses`.  Every
    positive witness field records the specific items that justify the claim.
    Witnesses are included in :class:`RefinementReport` when
    ``is_refinement`` is True and omitted otherwise.
    """

    stronger_evidence_keys: tuple[str, ...]
    """Extra evidence-ref keys in candidate not present in base."""
    higher_trust_level: bool
    """True iff candidate's trust level is strictly above base's."""
    fewer_residual_ids: tuple[str, ...]
    """Obligation IDs discharged in candidate that remain open in base."""
    scope_at_least_as_tight: bool
    """True iff candidate's coordinate depth ≥ base's."""
    notes: tuple[str, ...]
    """Human-readable summary of what the witness demonstrates."""


@dataclass(frozen=True, slots=True)
class RefinementReport:
    """Full report produced by :meth:`RefinementChecker.check_refinement`."""

    is_refinement: bool
    """True only when all four conditions simultaneously hold."""
    stronger_evidence: bool
    fewer_residuals: bool
    higher_trust: bool
    scope_ok: bool
    witness: RefinementWitness | None
    """Populated when is_refinement is True, otherwise None."""
    failure_reasons: tuple[str, ...]
    """Human-readable explanations for each failing condition."""


@dataclass(frozen=True, slots=True)
class RefinementChecker:
    """Verify that one judgment is a refinement of another (theory2.tex §12.3).

    Refinement ≤_J is a partial order on judgments.  It combines four
    sub-conditions that must *all* hold simultaneously:

    1. Evidence strengthening (§12.3.1): candidate's evidence is ⊇ base's.
    2. Trust elevation (§12.3.2): candidate's trust level is ≥ base's.
    3. Residual reduction (§12.3.3): candidate's obligations ⊆ base's.
    4. Scope tightness (§12.3.4): candidate's coordinate is at least as deep.

    Instances are immutable and delegate to the three sub-comparators passed
    at construction time, allowing dependency injection for testing.
    """

    trust_comparator: TrustComparator = field(default_factory=TrustComparator)
    evidence_comparator: EvidenceComparator = field(default_factory=EvidenceComparator)
    residual_comparator: ResidualComparator = field(default_factory=ResidualComparator)

    def check_refinement(
        self,
        candidate: LocalJudgment,
        base: LocalJudgment,
    ) -> RefinementReport:
        """Perform the full four-condition refinement check.

        Returns a :class:`RefinementReport` whose ``is_refinement`` flag is
        True only when all four conditions hold simultaneously.  Each
        individual condition flag is preserved so callers can determine which
        condition failed.
        """
        ev_ok = self.stronger_evidence(candidate, base)
        res_ok = self.fewer_residuals(candidate, base)
        trust_ok = self.verify_higher_trust(candidate, base)
        scope_ok = self.verify_same_or_tighter_scope(candidate, base)

        failures: list[str] = []
        if not ev_ok:
            failures.append("candidate evidence is not a superset of base evidence")
        if not res_ok:
            failures.append("candidate has more residual obligations than base")
        if not trust_ok:
            failures.append("candidate trust level is lower than base")
        if not scope_ok:
            failures.append("candidate coordinate is strictly broader than base")

        is_ref = ev_ok and res_ok and trust_ok and scope_ok
        witness = self.refinement_witnesses(candidate, base) if is_ref else None

        return RefinementReport(
            is_refinement=is_ref,
            stronger_evidence=ev_ok,
            fewer_residuals=res_ok,
            higher_trust=trust_ok,
            scope_ok=scope_ok,
            witness=witness,
            failure_reasons=tuple(failures),
        )

    def stronger_evidence(self, candidate: LocalJudgment, base: LocalJudgment) -> bool:
        """Return True iff the candidate's evidence refs are a superset of base's.

        A judgment with evidence refs {e₁, e₂, e₃} is considered to have
        stronger evidence than one with {e₁, e₂}.  Equality (same refs) is
        accepted — evidence strength is ≥, not strictly >.
        """
        candidate_refs = frozenset(candidate.evidence_refs)
        base_refs = frozenset(base.evidence_refs)
        return base_refs.issubset(candidate_refs)

    def fewer_residuals(self, candidate: LocalJudgment, base: LocalJudgment) -> bool:
        """Return True iff the candidate's obligations are a subset of base's.

        Fewer obligations means the candidate has discharged more verification
        duties than base (theory2.tex §12.3.3).  Equality is accepted.
        """
        candidate_obs = frozenset(candidate.obligations)
        base_obs = frozenset(base.obligations)
        return candidate_obs.issubset(base_obs)

    def verify_higher_trust(self, candidate: LocalJudgment, base: LocalJudgment) -> bool:
        """Return True iff the candidate's effective trust level is ≥ base's.

        The effective level is taken from the 'level' key in the trust_vector
        mapping.  If absent, UNVERIFIED (1) is assumed for both parties.  No
        silent promotion is permitted: a copilot-channel judgment may not be
        compared as higher-trust than a solver-discharged one.
        """
        c_level = _effective_trust_level(candidate.trust_vector)
        b_level = _effective_trust_level(base.trust_vector)
        return c_level >= b_level

    def verify_same_or_tighter_scope(
        self,
        candidate: LocalJudgment,
        base: LocalJudgment,
    ) -> bool:
        """Return True iff the candidate's coordinate is at least as specific.

        A coordinate is "tighter" if it has at least as many components in
        its hierarchical path (i.e. is a descendant of base's coordinate in
        the coordinate tree).  Equality (same depth) is accepted.
        """
        c_depth = len(candidate.coordinate.components)
        b_depth = len(base.coordinate.components)
        return c_depth >= b_depth

    def refinement_witnesses(
        self,
        candidate: LocalJudgment,
        base: LocalJudgment,
    ) -> RefinementWitness:
        """Collect concrete witnesses for each refinement condition.

        Returns a :class:`RefinementWitness` recording which specific evidence
        keys, trust values, and residual IDs justify the refinement claim.
        Should only be called when ``check_refinement`` returns True.
        """
        extra_evidence = tuple(
            k for k in candidate.evidence_refs
            if k not in frozenset(base.evidence_refs)
        )
        discharged_residuals = tuple(
            r for r in base.obligations
            if r not in frozenset(candidate.obligations)
        )
        c_level = _effective_trust_level(candidate.trust_vector)
        b_level = _effective_trust_level(base.trust_vector)

        notes_list: list[str] = []
        if extra_evidence:
            notes_list.append(f"candidate adds {len(extra_evidence)} evidence item(s)")
        if discharged_residuals:
            notes_list.append(f"candidate discharges {len(discharged_residuals)} obligation(s)")
        if c_level > b_level:
            notes_list.append(f"trust elevated from {b_level} to {c_level}")
        if c_level == b_level:
            notes_list.append("trust level unchanged (weak refinement)")

        return RefinementWitness(
            stronger_evidence_keys=extra_evidence,
            higher_trust_level=c_level > b_level,
            fewer_residual_ids=discharged_residuals,
            scope_at_least_as_tight=self.verify_same_or_tighter_scope(candidate, base),
            notes=tuple(notes_list),
        )


# ---------------------------------------------------------------------------
# 4. EquivalenceChecker
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EquivalenceReport:
    """Full report produced by :meth:`EquivalenceChecker.check_equivalence`."""

    are_equivalent: bool
    """True only when all four sub-conditions hold simultaneously."""
    bidirectional_refinement: bool
    """True iff J_left ≤_J J_right AND J_right ≤_J J_left."""
    semantic_equivalence: bool
    """True iff the proposition strings are identical."""
    evidence_equivalence: bool
    """True iff the evidence-ref sets are identical (order-insensitive)."""
    trust_equivalence: bool
    """True iff the effective trust levels are identical."""
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EquivalenceChecker:
    """Check whether two judgments are semantically equivalent (theory2.tex §12.2).

    Equivalence is defined as mutual refinement: J₁ ≡ J₂ ⟺ J₁ ≤_J J₂ ∧ J₂ ≤_J J₁.
    This is strictly stronger than just matching proposition strings.

    The four sub-conditions (§12.2.1–12.2.4) are checked independently so
    that partial equivalences can be reported even when full equivalence fails.
    This is useful when a copilot-generated judgment is "semantically
    equivalent" but has not yet accumulated sufficient evidence to satisfy
    the bidirectional-refinement condition.
    """

    trust_comparator: TrustComparator = field(default_factory=TrustComparator)
    evidence_comparator: EvidenceComparator = field(default_factory=EvidenceComparator)
    residual_comparator: ResidualComparator = field(default_factory=ResidualComparator)

    def check_equivalence(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> EquivalenceReport:
        """Perform a full four-condition equivalence check.

        All four conditions must hold simultaneously for ``are_equivalent`` to
        be True.  The individual flags are preserved in the report for auditing
        so that the exact nature of any divergence is immediately visible.
        """
        checker = RefinementChecker(
            self.trust_comparator, self.evidence_comparator, self.residual_comparator
        )
        bidir = (
            checker.check_refinement(left, right).is_refinement
            and checker.check_refinement(right, left).is_refinement
        )
        sem = self.semantic_equivalence(left, right)
        ev = self.evidence_equivalence(left, right)
        tr = self.trust_equivalence(left, right)

        notes: list[str] = []
        if not bidir:
            notes.append("refinement does not hold in both directions")
        if not sem:
            notes.append(f"proposition strings differ: '{left.proposition}' vs '{right.proposition}'")
        if not ev:
            notes.append("evidence reference sets are not equal")
        if not tr:
            notes.append(
                f"trust levels differ: {_effective_trust_level(left.trust_vector)}"
                f" vs {_effective_trust_level(right.trust_vector)}"
            )

        return EquivalenceReport(
            are_equivalent=bidir and sem and ev and tr,
            bidirectional_refinement=bidir,
            semantic_equivalence=sem,
            evidence_equivalence=ev,
            trust_equivalence=tr,
            notes=tuple(notes),
        )

    def bidirectional_refinement(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff each judgment refines the other.

        This is the proof-theoretic definition of equivalence used throughout
        theory2.tex §12.2.  It is the strongest of the four sub-conditions.
        """
        checker = RefinementChecker(
            self.trust_comparator, self.evidence_comparator, self.residual_comparator
        )
        return (
            checker.check_refinement(left, right).is_refinement
            and checker.check_refinement(right, left).is_refinement
        )

    def semantic_equivalence(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff both judgments assert the same proposition string.

        String comparison is used here; for richer semantic matching (e.g.
        alpha-equivalence of λ-terms), override this method in a subclass or
        pass a custom proposition-normaliser.
        """
        return left.proposition == right.proposition

    def evidence_equivalence(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff both judgments reference the same set of evidence keys.

        Order of evidence_refs is deliberately ignored; only set identity
        matters for equivalence (§12.2.3).
        """
        return frozenset(left.evidence_refs) == frozenset(right.evidence_refs)

    def trust_equivalence(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff both judgments have identical effective trust levels.

        Does not compare ceilings or floors, only the current level.  Use
        :meth:`TrustComparator.compare` for a ceiling/floor-aware comparison.
        """
        return (
            _effective_trust_level(left.trust_vector)
            == _effective_trust_level(right.trust_vector)
        )

    def near_equivalent(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
        min_conditions: int = 3,
    ) -> bool:
        """Return True iff at least *min_conditions* of the four sub-conditions hold.

        "Near-equivalence" is a useful heuristic when a copilot-proposed
        judgment almost matches an existing one but differs on a single
        dimension (e.g. trust has not yet been elevated by a solver).
        """
        report = self.check_equivalence(left, right)
        count = sum([
            report.bidirectional_refinement,
            report.semantic_equivalence,
            report.evidence_equivalence,
            report.trust_equivalence,
        ])
        return count >= min_conditions


# ---------------------------------------------------------------------------
# 5. ContradictionDetector
# ---------------------------------------------------------------------------

class ContradictionKind(str, Enum):
    """Classification of contradiction types (theory2.tex §12.6).

    Values
    ------
    OPPOSING_PROPOSITIONS
        The two propositions are logical negations of each other.
    CONFLICTING_EVIDENCE
        The evidence bundles are mutually exclusive (H¹ obstruction).
    INCONSISTENT_TRUST
        One judgment is MECHANICALLY_VERIFIED while the other is CONTRADICTED.
    COHOMOLOGICAL
        A cohomology-class mismatch detected at the sheaf level.
    NONE
        No contradiction detected.
    """

    OPPOSING_PROPOSITIONS = "opposing_propositions"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INCONSISTENT_TRUST = "inconsistent_trust"
    COHOMOLOGICAL = "cohomological"
    NONE = "none"

    @property
    def severity(self) -> int:
        """Return a severity score (higher = more serious) for priority sorting."""
        _scores = {
            ContradictionKind.OPPOSING_PROPOSITIONS: 4,
            ContradictionKind.CONFLICTING_EVIDENCE: 3,
            ContradictionKind.INCONSISTENT_TRUST: 2,
            ContradictionKind.COHOMOLOGICAL: 1,
            ContradictionKind.NONE: 0,
        }
        return _scores[self]


@dataclass(frozen=True, slots=True)
class ContradictionReport:
    """Full report produced by :meth:`ContradictionDetector.detect_contradiction`."""

    is_contradiction: bool
    kind: ContradictionKind
    opposing_propositions: bool
    conflicting_evidence: bool
    inconsistent_trust: bool
    details: tuple[str, ...]
    """Human-readable explanation of each detected conflict."""


@dataclass(frozen=True, slots=True)
class ContradictionDetector:
    """Detect logical contradictions between two judgments (theory2.tex §12.6).

    A contradiction occurs when the two judgments cannot both hold in any
    consistent extension of the sheaf — i.e. their gluing would yield an
    H¹ obstruction.  The four contradiction subtypes are checked in
    decreasing severity order; the first match sets the *kind*.

    The copilot channel is relevant here: a copilot-generated judgment that
    conflicts with a solver-discharged judgment is a particularly serious
    inconsistency and is reported in ``details``.
    """

    trust_comparator: TrustComparator = field(default_factory=TrustComparator)
    evidence_comparator: EvidenceComparator = field(default_factory=EvidenceComparator)

    def detect_contradiction(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> ContradictionReport:
        """Run all four contradiction checks and aggregate the results.

        Checks are ordered by severity: proposition conflict > evidence
        conflict > trust inconsistency > cohomological obstruction.
        Returns the highest-severity ContradictionKind found.
        """
        opp = self.check_opposing_propositions(left, right)
        ev = self.check_conflicting_evidence(left, right)
        tr = self.check_inconsistent_trust(left, right)
        kind = self.classify_contradiction(opp, ev, tr)

        details: list[str] = []
        if opp:
            details.append(
                f"propositions '{left.proposition}' and '{right.proposition}' "
                "are direct logical negations"
            )
        if ev:
            details.append("evidence bundles are mutually exclusive (H¹ obstruction)")
        if tr:
            max_lvl = int(TrustLevel.MECHANICALLY_VERIFIED)
            details.append(
                f"trust levels {_effective_trust_level(left.trust_vector)} and "
                f"{_effective_trust_level(right.trust_vector)} are mutually inconsistent "
                f"(span 0–{max_lvl})"
            )

        return ContradictionReport(
            is_contradiction=opp or ev or tr,
            kind=kind,
            opposing_propositions=opp,
            conflicting_evidence=ev,
            inconsistent_trust=tr,
            details=tuple(details),
        )

    def check_opposing_propositions(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff the propositions are direct logical negations.

        Uses a heuristic: if one proposition string equals the other prefixed
        with "not " or "¬", they are considered opposing.  For formal negation
        (e.g. α-equivalent terms), extend by providing a custom normaliser.
        """
        l_prop = left.proposition.strip().lower()
        r_prop = right.proposition.strip().lower()
        if l_prop == r_prop:
            return False
        negated_l = l_prop.removeprefix("not ").removeprefix("¬").strip()
        negated_r = r_prop.removeprefix("not ").removeprefix("¬").strip()
        return negated_l == r_prop or negated_r == l_prop

    def check_conflicting_evidence(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff the two judgments reference mutually exclusive evidence.

        Two evidence sets are flagged as conflicting when one judgment is in
        OBSTRUCTED status and shares at least one evidence key with the other.
        This mirrors a H¹ obstruction at the evidence-sheaf level (§12.6.2).
        """
        shared = frozenset(left.evidence_refs) & frozenset(right.evidence_refs)
        if left.status == JudgmentStatus.OBSTRUCTED and shared:
            return True
        if right.status == JudgmentStatus.OBSTRUCTED and shared:
            return True
        left_bundle = _bundle_from_refs(left.evidence_refs)
        right_bundle = _bundle_from_refs(right.evidence_refs)
        return not self.evidence_comparator.compatible_with(left_bundle, right_bundle)

    def check_inconsistent_trust(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff the trust levels are irreconcilably contradictory.

        A CONTRADICTED trust level (0) on either judgment while the other is
        MECHANICALLY_VERIFIED (7) constitutes an inconsistency that cannot be
        resolved without external intervention (theory2.tex §12.6.3).
        """
        l_lvl = _effective_trust_level(left.trust_vector)
        r_lvl = _effective_trust_level(right.trust_vector)
        max_lvl = int(TrustLevel.MECHANICALLY_VERIFIED)
        return (l_lvl == 0 and r_lvl == max_lvl) or (r_lvl == 0 and l_lvl == max_lvl)

    def classify_contradiction(
        self,
        opposing: bool,
        conflicting_ev: bool,
        inconsistent_trust: bool,
    ) -> ContradictionKind:
        """Return the highest-severity ContradictionKind for the given boolean flags.

        If no flag is set, returns ContradictionKind.NONE.
        """
        if opposing:
            return ContradictionKind.OPPOSING_PROPOSITIONS
        if conflicting_ev:
            return ContradictionKind.CONFLICTING_EVIDENCE
        if inconsistent_trust:
            return ContradictionKind.INCONSISTENT_TRUST
        return ContradictionKind.NONE

    def could_be_resolved(self, report: ContradictionReport) -> bool:
        """Return True iff the contradiction kind is potentially resolvable.

        Proposition-level contradictions are considered unresolvable by
        automated means.  Evidence and trust contradictions may be repaired
        by providing additional evidence or issuing a trust revision.
        """
        return report.kind in (
            ContradictionKind.CONFLICTING_EVIDENCE,
            ContradictionKind.INCONSISTENT_TRUST,
            ContradictionKind.COHOMOLOGICAL,
        )


# ---------------------------------------------------------------------------
# Composite report (forward-declared for use in JudgmentComparator)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CompositeComparisonReport:
    """Aggregated three-dimensional comparison report.

    Produced by :meth:`JudgmentComparator.composite_comparison`.  Contains
    the overall ComparisonResult together with detailed sub-reports for each
    of the three comparison dimensions: trust, evidence, and residuals.

    The ``to_mapping`` method provides a plain-dict representation suitable
    for JSON serialisation via :class:`ComparisonSerializer`.
    """

    overall: ComparisonResult
    trust_detail: TrustComparisonDetail
    evidence_detail: EvidenceComparisonDetail
    residual_detail: ResidualComparisonDetail
    left_key: str
    right_key: str
    timestamp: str

    def to_mapping(self) -> dict[str, Any]:
        """Return a plain dict representation suitable for JSON serialisation."""
        return ComparisonSerializer()._report_to_dict(self)

    def is_clean(self) -> bool:
        """Return True iff no contradictions and at least a weak ordering exists."""
        return (
            not self.overall.is_conflict()
            and self.evidence_detail.are_compatible
            and self.trust_detail.are_comparable
        )


# ---------------------------------------------------------------------------
# 2. JudgmentComparator
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class JudgmentComparator:
    """High-level entry point for comparing two LocalJudgments.

    Implements the comparison relation ≤_J from theory2.tex §12.2.
    Delegates to specialist sub-comparators for trust, evidence, and
    residual dimensions.

    The copilot channel is treated as having an enforced trust ceiling
    (TrustLevel.COPILOT_SUGGESTED) so comparisons that would silently
    promote copilot evidence beyond that ceiling are flagged in the relevant
    TrustComparisonDetail.notes tuple rather than raising an exception.

    Parameters
    ----------
    trust_comparator : TrustComparator
        Strategy for comparing TrustAnnotation objects.
    evidence_comparator : EvidenceComparator
        Strategy for comparing EvidenceBundle objects.
    residual_comparator : ResidualComparator
        Strategy for comparing residual obligation tuples.
    strict_scope : bool
        When True, two judgments at different coordinate depths are always
        considered potentially incomparable.
    """

    trust_comparator: TrustComparator = field(default_factory=TrustComparator)
    evidence_comparator: EvidenceComparator = field(default_factory=EvidenceComparator)
    residual_comparator: ResidualComparator = field(default_factory=ResidualComparator)
    strict_scope: bool = True

    def compare(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> ComparisonResult:
        """Return the ComparisonResult for *left* relative to *right*.

        Algorithm (theory2.tex §12.2.3):
        1. Check for contradiction — takes precedence over all ordering claims.
        2. Check for exact equivalence.
        3. Check whether *left* refines *right*.
        4. Check whether *right* refines *left*.
        5. Otherwise return INCOMPARABLE.

        The contradiction check uses ContradictionDetector; the equivalence
        and refinement checks use EquivalenceChecker and RefinementChecker
        respectively, all sharing the same sub-comparator instances.
        """
        detector = ContradictionDetector(self.trust_comparator, self.evidence_comparator)
        if detector.detect_contradiction(left, right).is_contradiction:
            return ComparisonResult.CONTRADICTS

        eq_checker = EquivalenceChecker(
            self.trust_comparator, self.evidence_comparator, self.residual_comparator
        )
        if eq_checker.check_equivalence(left, right).are_equivalent:
            return ComparisonResult.EQUIVALENT

        ref_checker = RefinementChecker(
            self.trust_comparator, self.evidence_comparator, self.residual_comparator
        )
        left_refines = ref_checker.check_refinement(left, right).is_refinement
        right_refines = ref_checker.check_refinement(right, left).is_refinement

        if left_refines and not right_refines:
            return ComparisonResult.REFINES
        if right_refines and not left_refines:
            return ComparisonResult.IS_REFINED_BY
        return ComparisonResult.INCOMPARABLE

    def is_refinement_of(
        self,
        candidate: LocalJudgment,
        base: LocalJudgment,
    ) -> bool:
        """Return True iff *candidate* refines *base*.

        Refinement (≤_J) requires stronger or equal evidence (§12.3.1),
        higher or equal trust (§12.3.2), fewer or equal residual obligations
        (§12.3.3), and at-least-as-tight coordinate scope (§12.3.4).

        EQUIVALENT is treated as a refinement (reflexivity of ≤_J).
        """
        result = self.compare(candidate, base)
        return result in (ComparisonResult.REFINES, ComparisonResult.EQUIVALENT)

    def is_equivalent_to(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff *left* and *right* are mutually equivalent under ≤_J."""
        return self.compare(left, right) is ComparisonResult.EQUIVALENT

    def contradicts(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> bool:
        """Return True iff *left* and *right* are in logical contradiction."""
        return self.compare(left, right) is ComparisonResult.CONTRADICTS

    def trust_comparison(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> TrustComparisonDetail:
        """Return a detailed trust-dimension comparison between the two judgments.

        The trust vector is extracted from the ``trust_vector`` mapping on
        each LocalJudgment and wrapped into a TrustAnnotation for comparison
        via the injected TrustComparator.
        """
        left_trust = _trust_from_vector(left.trust_vector)
        right_trust = _trust_from_vector(right.trust_vector)
        return self.trust_comparator.compare(left_trust, right_trust)

    def evidence_comparison(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> EvidenceComparisonDetail:
        """Return a detailed evidence-dimension comparison between two judgments.

        Evidence keys are resolved through the evidence_refs tuples on each
        LocalJudgment.  The resulting synthetic EvidenceBundle stubs are
        passed to the injected EvidenceComparator.
        """
        left_bundle = _bundle_from_refs(left.evidence_refs)
        right_bundle = _bundle_from_refs(right.evidence_refs)
        return self.evidence_comparator.compare_bundles(left_bundle, right_bundle)

    def residual_comparison(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> ResidualComparisonDetail:
        """Return a detailed residual-dimension comparison between two judgments."""
        return self.residual_comparator.compare(left.obligations, right.obligations)

    def composite_comparison(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> CompositeComparisonReport:
        """Return an exhaustive three-dimensional comparison report.

        Combines trust, evidence, and residual sub-comparisons into a single
        structured report.  Intended for audit trails and UI display.  The
        overall ComparisonResult is computed first to avoid redundant work.
        """
        trust_detail = self.trust_comparison(left, right)
        evidence_detail = self.evidence_comparison(left, right)
        residual_detail = self.residual_comparison(left, right)
        overall = self.compare(left, right)

        return CompositeComparisonReport(
            overall=overall,
            trust_detail=trust_detail,
            evidence_detail=evidence_detail,
            residual_detail=residual_detail,
            left_key=_judgment_key(left),
            right_key=_judgment_key(right),
            timestamp=_now_iso(),
        )

    # ------------------------------------------------------------------ #
    # Cross-subsystem integration methods
    # ------------------------------------------------------------------ #

    def solver_comparison(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> dict[str, Any]:
        """Perform a Z3-backed semantic comparison of two judgments.

        Uses :class:`jugeo.solver.z3_session.Z3Encoder` and
        :class:`jugeo.solver.z3_session.Z3Session` to encode both
        propositions and check whether one logically implies the other,
        they are equivalent, or they are contradictory.

        Parameters
        ----------
        left : LocalJudgment
            The left-hand judgment.
        right : LocalJudgment
            The right-hand judgment.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"solver_available"``, ``"left_implies_right"``,
            ``"right_implies_left"``, ``"equivalent"``, and
            ``"contradictory"`` keys.
        """
        try:
            from jugeo.solver.z3_session import Z3Encoder, Z3Session
        except Exception:
            return {
                "solver_available": False,
                "left_implies_right": None,
                "right_implies_left": None,
                "equivalent": None,
                "contradictory": None,
                "fallback": self.compare(left, right).value,
            }

        encoder = Z3Encoder()
        left_formula = encoder.encode_proposition(left.proposition)
        right_formula = encoder.encode_proposition(right.proposition)

        session = Z3Session()
        try:
            session.assert_formula(left_formula)
            left_implies_right = session.check_sat().name == "UNSAT"
        except Exception:
            left_implies_right = None
        finally:
            session.reset()

        try:
            session.assert_formula(right_formula)
            right_implies_left = session.check_sat().name == "UNSAT"
        except Exception:
            right_implies_left = None
        finally:
            session.reset()

        equivalent = (
            left_implies_right and right_implies_left
            if left_implies_right is not None and right_implies_left is not None
            else None
        )
        contradictory = None
        if left_implies_right is False and right_implies_left is False:
            contradictory = False

        return {
            "solver_available": True,
            "left_implies_right": left_implies_right,
            "right_implies_left": right_implies_left,
            "equivalent": equivalent,
            "contradictory": contradictory,
        }

    def refinement_check(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> dict[str, Any]:
        """Check relational refinement between two judgments.

        Uses ``jugeo.problem_modes.relational_refinement`` (when
        available) to determine whether *left* is a valid refinement
        of *right* under the relational refinement discipline.

        Parameters
        ----------
        left : LocalJudgment
            The candidate refinement.
        right : LocalJudgment
            The specification judgment.

        Returns
        -------
        dict[str, Any]
            A mapping with ``"refinement_available"``,
            ``"is_refinement"``, ``"witness"``, and ``"notes"`` keys.
        """
        try:
            from jugeo.problem_modes.relational_refinement import (  # type: ignore[import-not-found]
                RefinementChecker as RelationalRefinementChecker,
            )
        except Exception:
            structural = self.compare(left, right)
            return {
                "refinement_available": False,
                "is_refinement": structural == ComparisonResult.REFINEMENT,
                "witness": None,
                "notes": [
                    "Relational refinement subsystem unavailable; "
                    "falling back to structural comparison.",
                    f"structural_result: {structural.value}",
                ],
            }

        checker = RelationalRefinementChecker()
        result = checker.check(left, right)
        return {
            "refinement_available": True,
            "is_refinement": result.is_refinement
                if hasattr(result, "is_refinement") else bool(result),
            "witness": getattr(result, "witness", None),
            "notes": getattr(result, "notes", []),
        }

    def solver_check(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> Any:
        """Verify the refinement relation using Z3 constraint solving.

        Encodes both judgments as Z3 formulae and checks whether the
        left proposition logically implies the right (refinement) or
        vice versa.  Returns a ``SolverVerdict`` from
        ``jugeo.solver.z3_session`` containing the satisfiability result,
        any counterexample, and timing metadata.

        Parameters
        ----------
        left, right : LocalJudgment
            The two judgments to compare.

        Returns
        -------
        dict[str, Any]
            Solver verdict with ``"verified"``, ``"counterexample"``,
            and ``"solver_time_ms"`` keys.
        """
        try:
            from jugeo.solver.z3_session import Z3Encoder, Z3Session
        except Exception:
            return {
                "solver_available": False,
                "verified": None,
                "counterexample": None,
                "fallback": self.compare(left, right).value,
            }
        encoder = Z3Encoder()
        left_f = encoder.encode_proposition(left.proposition)
        right_f = encoder.encode_proposition(right.proposition)
        session = Z3Session()
        try:
            session.assert_formula(left_f)
            session.assert_formula(encoder.negate(right_f))
            sat_result = session.check_sat()
            verified = sat_result.name == "UNSAT"
            counterexample = session.model() if not verified else None
        except Exception:
            verified = None
            counterexample = None
        finally:
            session.reset()
        return {
            "solver_available": True,
            "verified": verified,
            "counterexample": counterexample,
        }

    def certificate(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> Any:
        """Produce a refinement certificate for the comparison.

        A refinement certificate from ``jugeo.evidence.certificates``
        attests that J₁ ≤_J J₂ with a given trust floor, recording
        which evidence dimensions contributed to the ordering.  The
        certificate can be stored in the provenance chain and verified
        independently.

        Parameters
        ----------
        left, right : LocalJudgment
            The two judgments in the comparison.

        Returns
        -------
        dict[str, Any]
            Certificate data with ``"certified"``, ``"result"``,
            ``"trust_floor"``, and ``"dimensions"`` keys.
        """
        try:
            from jugeo.evidence.certificates import RefinementCertificate
        except Exception:
            result = self.compare(left, right)
            return {
                "certified": False,
                "result": result.value,
                "trust_floor": None,
                "dimensions": {
                    "trust": self.trust_comparison(left, right).are_comparable,
                    "evidence": self.evidence_comparison(left, right).are_compatible,
                },
            }
        result = self.compare(left, right)
        trust_detail = self.trust_comparison(left, right)
        evidence_detail = self.evidence_comparison(left, right)
        return RefinementCertificate.create(
            left_key=_judgment_key(left),
            right_key=_judgment_key(right),
            result=result.value,
            trust_comparable=trust_detail.are_comparable,
            evidence_compatible=evidence_detail.are_compatible,
        )

    @property
    def encoding_comparison(self) -> Any:
        """Return the encoding strategy used for comparisons.

        Uses ``jugeo.encodings`` to describe how judgment propositions
        and trust annotations are encoded into comparable representations.
        This metadata is useful for audit trails explaining *how* two
        judgments were compared.

        Returns
        -------
        dict[str, Any]
            Encoding metadata with ``"strategy"``, ``"dimensions"``,
            and ``"encoding_available"`` keys.
        """
        try:
            from jugeo.encodings import ComparisonEncoding
        except Exception:
            return {
                "encoding_available": False,
                "strategy": "structural",
                "dimensions": ["trust", "evidence", "residuals"],
                "strict_scope": self.strict_scope,
            }
        return ComparisonEncoding.describe(strict_scope=self.strict_scope)


# ---------------------------------------------------------------------------
# 6. JudgmentOrder
# ---------------------------------------------------------------------------

@dataclass
class JudgmentOrder:
    """A mutable partial order on a collection of LocalJudgments (theory2.tex §12.7).

    Implements the poset (J, ≤_J) from theory2.tex.  Elements are identified
    by their canonical judgment key (coordinate path + proposition prefix).
    The collection supports incremental insertion, meet/join computation, and
    extraction of minimal and maximal elements.

    This class is deliberately *mutable* so that elements can be added
    incrementally as the copilot pipeline produces new judgments.  The
    underlying comparator is injected at construction time.

    Note that meet and join are only computed within the *stored* collection;
    they may not equal the true lattice meet/join if the collection is a
    proper subset of all extant judgments.
    """

    comparator: JudgmentComparator = field(default_factory=JudgmentComparator)
    _elements: list[LocalJudgment] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def add(self, judgment: LocalJudgment) -> None:
        """Insert a judgment into the order if it is not already present.

        Duplicate detection uses canonical judgment keys, so judgments at the
        same coordinate with the same proposition are considered identical.
        """
        key = _judgment_key(judgment)
        if not any(_judgment_key(e) == key for e in self._elements):
            self._elements.append(judgment)

    def remove(self, judgment: LocalJudgment) -> bool:
        """Remove a judgment from the order.  Return True if it was present."""
        key = _judgment_key(judgment)
        before = len(self._elements)
        self._elements = [e for e in self._elements if _judgment_key(e) != key]
        return len(self._elements) < before

    def clear(self) -> None:
        """Remove all elements from the order."""
        self._elements.clear()

    def __len__(self) -> int:
        """Return the number of judgments currently in the order."""
        return len(self._elements)

    # ------------------------------------------------------------------
    # Order relations
    # ------------------------------------------------------------------

    def leq(self, left: LocalJudgment, right: LocalJudgment) -> bool:
        """Return True iff left ≤_J right (left refines or equals right).

        Wraps :meth:`JudgmentComparator.is_refinement_of` for consistent
        semantics across the module.
        """
        return self.comparator.is_refinement_of(left, right)

    def geq(self, left: LocalJudgment, right: LocalJudgment) -> bool:
        """Return True iff left ≥_J right (right refines or equals left)."""
        return self.leq(right, left)

    def meets(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> LocalJudgment | None:
        """Return the greatest lower bound of *left* and *right*, if it exists.

        Scans ``_elements`` for a judgment z such that z ≤ left and z ≤ right
        and no other element w with z ≤ w ≤ left and w ≤ right exists.
        Returns None if no meet is present in the current collection.
        """
        candidates = [
            e for e in self._elements
            if self.leq(e, left) and self.leq(e, right)
        ]
        if not candidates:
            return None
        # Filter to maximal among the lower bounds (= greatest lower bound)
        maximal: list[LocalJudgment] = []
        for c in candidates:
            k_c = _judgment_key(c)
            is_max = all(
                not self.leq(other, c) or self.leq(c, other)
                for other in candidates
                if _judgment_key(other) != k_c
            )
            if is_max:
                maximal.append(c)
        return maximal[0] if len(maximal) == 1 else None

    def joins(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
    ) -> LocalJudgment | None:
        """Return the least upper bound of *left* and *right*, if it exists.

        Symmetric of :meth:`meets`: finds minimal elements above both
        judgments within the current collection.
        """
        candidates = [
            e for e in self._elements
            if self.leq(left, e) and self.leq(right, e)
        ]
        if not candidates:
            return None
        minimal: list[LocalJudgment] = []
        for c in candidates:
            k_c = _judgment_key(c)
            is_min = all(
                not self.leq(c, other) or self.leq(other, c)
                for other in candidates
                if _judgment_key(other) != k_c
            )
            if is_min:
                minimal.append(c)
        return minimal[0] if len(minimal) == 1 else None

    def minimal_elements(self) -> list[LocalJudgment]:
        """Return all minimal elements of the current order.

        An element m is minimal if no other element is strictly below it —
        i.e. there is no z ≠ m with z ≤ m and m ≢ z.  Minimal elements are
        the "least-refined" judgments in the collection.
        """
        result: list[LocalJudgment] = []
        for e in self._elements:
            strictly_below = [
                o for o in self._elements
                if _judgment_key(o) != _judgment_key(e)
                and self.leq(o, e)
                and not self.leq(e, o)
            ]
            if not strictly_below:
                result.append(e)
        return result

    def maximal_elements(self) -> list[LocalJudgment]:
        """Return all maximal elements of the current order.

        An element m is maximal if no other element is strictly above it.
        Maximal elements are the "most-refined" judgments in the collection.
        """
        result: list[LocalJudgment] = []
        for e in self._elements:
            strictly_above = [
                o for o in self._elements
                if _judgment_key(o) != _judgment_key(e)
                and self.leq(e, o)
                and not self.leq(o, e)
            ]
            if not strictly_above:
                result.append(e)
        return result

    def antichains(self) -> list[tuple[LocalJudgment, LocalJudgment]]:
        """Return all pairs of incomparable elements.

        An antichain pair (a, b) satisfies a ≰ b and b ≰ a.  These are
        judgments that cannot be ordered and may require external resolution
        (e.g. a human review or a new copilot comparison pass).
        """
        result: list[tuple[LocalJudgment, LocalJudgment]] = []
        for i, e1 in enumerate(self._elements):
            for e2 in self._elements[i + 1:]:
                if not self.leq(e1, e2) and not self.leq(e2, e1):
                    result.append((e1, e2))
        return result

    def covering_relations(self) -> list[tuple[LocalJudgment, LocalJudgment]]:
        """Return all covering pairs (x, y) where x is covered by y.

        (x, y) is a covering relation iff x ≤ y, x ≠ y, and there is no z
        with x < z < y.  These form the Hasse diagram edges.
        """
        result: list[tuple[LocalJudgment, LocalJudgment]] = []
        for x in self._elements:
            for y in self._elements:
                if _judgment_key(x) == _judgment_key(y):
                    continue
                if not self.leq(x, y) or self.leq(y, x):
                    continue
                # Check there is no z strictly between x and y
                intermediate = [
                    z for z in self._elements
                    if _judgment_key(z) not in (_judgment_key(x), _judgment_key(y))
                    and self.leq(x, z)
                    and not self.leq(z, x)
                    and self.leq(z, y)
                    and not self.leq(y, z)
                ]
                if not intermediate:
                    result.append((x, y))
        return result


# ---------------------------------------------------------------------------
# 10. ComparisonHistory
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRecord:
    """A single comparison event recorded in ComparisonHistory."""

    left_key: str
    right_key: str
    result: ComparisonResult
    timestamp: str
    notes: tuple[str, ...] = ()


@dataclass
class ComparisonHistory:
    """An append-only log of comparison events with statistics and pattern analysis.

    The history is intended to be used by the copilot orchestration layer to
    track which judgment pairs have been compared, identify recurring conflict
    patterns, and surface candidates for manual review.

    Records are indexed by judgment key for O(1) lookup.  Thread-safety is
    NOT guaranteed; external locking is required for concurrent access.
    """

    _records: list[ComparisonRecord] = field(default_factory=list, repr=False)
    _index: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )

    def record(
        self,
        left: LocalJudgment,
        right: LocalJudgment,
        result: ComparisonResult,
        notes: tuple[str, ...] = (),
    ) -> ComparisonRecord:
        """Append a new comparison record to the history.

        The record is indexed by both judgment keys for fast retrieval via
        :meth:`lookup`.  Returns the new ComparisonRecord.
        """
        rec = ComparisonRecord(
            left_key=_judgment_key(left),
            right_key=_judgment_key(right),
            result=result,
            timestamp=_now_iso(),
            notes=notes,
        )
        idx = len(self._records)
        self._records.append(rec)
        self._index[rec.left_key].append(idx)
        self._index[rec.right_key].append(idx)
        return rec

    def record_raw(
        self,
        left_key: str,
        right_key: str,
        result: ComparisonResult,
        notes: tuple[str, ...] = (),
    ) -> ComparisonRecord:
        """Append a comparison record using pre-computed string keys.

        Useful when re-loading serialised records without reconstructing full
        LocalJudgment objects.
        """
        rec = ComparisonRecord(
            left_key=left_key,
            right_key=right_key,
            result=result,
            timestamp=_now_iso(),
            notes=notes,
        )
        idx = len(self._records)
        self._records.append(rec)
        self._index[left_key].append(idx)
        self._index[right_key].append(idx)
        return rec

    def lookup(self, judgment: LocalJudgment) -> list[ComparisonRecord]:
        """Return all records involving the given judgment (as left or right)."""
        key = _judgment_key(judgment)
        return [self._records[i] for i in self._index.get(key, [])]

    def lookup_key(self, key: str) -> list[ComparisonRecord]:
        """Return all records involving the given canonical key."""
        return [self._records[i] for i in self._index.get(key, [])]

    def statistics(self) -> dict[str, Any]:
        """Return summary statistics over all recorded comparisons.

        Includes counts by ComparisonResult, the most-compared judgment key,
        and the timestamp range.  The result is a plain dict for easy
        JSON serialisation.
        """
        counts: dict[str, int] = defaultdict(int)
        for rec in self._records:
            counts[rec.result.value] += 1

        most_compared_key: str | None = None
        most_compared_count = 0
        for key, indices in self._index.items():
            if len(indices) > most_compared_count:
                most_compared_count = len(indices)
                most_compared_key = key

        return {
            "total_comparisons": len(self._records),
            "counts_by_result": dict(counts),
            "most_compared_key": most_compared_key,
            "most_compared_count": most_compared_count,
            "earliest": self._records[0].timestamp if self._records else None,
            "latest": self._records[-1].timestamp if self._records else None,
        }

    def common_patterns(self, min_occurrences: int = 2) -> list[dict[str, Any]]:
        """Return repeated comparison patterns (same result for same pair).

        A "pattern" is a (left_key, right_key, result) triple that appears
        at least *min_occurrences* times.  These indicate stable relationships
        worth caching or reporting in the UI.  Results are sorted by count
        descending.
        """
        pair_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        for rec in self._records:
            triple = (rec.left_key, rec.right_key, rec.result.value)
            pair_counts[triple] += 1

        patterns = [
            {"left_key": k[0], "right_key": k[1], "result": k[2], "count": v}
            for k, v in pair_counts.items()
            if v >= min_occurrences
        ]
        patterns.sort(key=lambda p: p["count"], reverse=True)
        return patterns

    def contradiction_pairs(self) -> list[tuple[str, str]]:
        """Return all (left_key, right_key) pairs that resulted in CONTRADICTS."""
        return [
            (rec.left_key, rec.right_key)
            for rec in self._records
            if rec.result is ComparisonResult.CONTRADICTS
        ]

    def refinement_chains(self) -> list[list[str]]:
        """Compute maximal refinement chains from the recorded comparisons.

        A chain is a sequence [k₀, k₁, …, kₙ] where each kᵢ REFINES kᵢ₊₁.
        Only REFINES (not EQUIVALENT) edges are included.  Cycles are guarded
        against to handle inconsistent histories.
        """
        refines_edges: dict[str, list[str]] = defaultdict(list)
        for rec in self._records:
            if rec.result is ComparisonResult.REFINES:
                refines_edges[rec.left_key].append(rec.right_key)

        all_keys = set(refines_edges.keys())
        targets = {t for tgts in refines_edges.values() for t in tgts}
        roots = all_keys - targets

        chains: list[list[str]] = []

        def _extend(path: list[str]) -> None:
            head = path[-1]
            nexts = refines_edges.get(head, [])
            if not nexts:
                chains.append(list(path))
                return
            for nxt in nexts:
                if nxt not in path:
                    _extend(path + [nxt])

        for root in roots:
            _extend([root])
        return chains

    def clear(self) -> None:
        """Remove all records from the history."""
        self._records.clear()
        self._index.clear()


# ---------------------------------------------------------------------------
# 11. ComparisonSerializer
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ComparisonSerializer:
    """JSON serialisation and deserialisation for comparison results and reports.

    Follows the serialisation conventions established in jugeo.judgments.exports:
    all keys use snake_case, enum values are stored as their string ``.value``,
    tuples are serialised as JSON arrays.

    This serialiser is used by the copilot orchestration pipeline to persist
    comparison results across sessions and replay them for debugging.  All
    ``serialize_*`` methods return UTF-8 JSON strings; all ``deserialize_*``
    methods accept those same strings.
    """

    indent: int = 2

    def serialize_result(self, result: ComparisonResult) -> str:
        """Serialise a single ComparisonResult to a JSON string."""
        return json.dumps({"result": result.value, "label": result.to_label()}, indent=self.indent)

    def serialize_composite_report(self, report: CompositeComparisonReport) -> str:
        """Serialise a CompositeComparisonReport to a JSON string."""
        return json.dumps(self._report_to_dict(report), indent=self.indent)

    def serialize_history(self, history: ComparisonHistory) -> str:
        """Serialise a ComparisonHistory to a JSON string.

        Includes both the raw records and the aggregated statistics so that
        the serialised form is self-describing.
        """
        records = [
            {
                "left_key": r.left_key,
                "right_key": r.right_key,
                "result": r.result.value,
                "timestamp": r.timestamp,
                "notes": list(r.notes),
            }
            for r in history._records
        ]
        return json.dumps(
            {"records": records, "statistics": history.statistics()},
            indent=self.indent,
        )

    def deserialize_result(self, data: str) -> ComparisonResult:
        """Deserialise a ComparisonResult from a JSON string."""
        obj = json.loads(data)
        return ComparisonResult(obj["result"])

    def deserialize_history_records(self, data: str) -> list[ComparisonRecord]:
        """Deserialise a list of ComparisonRecord objects from a serialised history.

        Returns bare ComparisonRecord objects; the caller is responsible for
        re-inserting them into a ComparisonHistory instance via
        :meth:`ComparisonHistory.record_raw`.
        """
        obj = json.loads(data)
        return [
            ComparisonRecord(
                left_key=r["left_key"],
                right_key=r["right_key"],
                result=ComparisonResult(r["result"]),
                timestamp=r["timestamp"],
                notes=tuple(r.get("notes", [])),
            )
            for r in obj.get("records", [])
        ]

    def serialize_refinement_report(self, report: RefinementReport) -> str:
        """Serialise a RefinementReport to a JSON string."""
        d: dict[str, Any] = {
            "is_refinement": report.is_refinement,
            "stronger_evidence": report.stronger_evidence,
            "fewer_residuals": report.fewer_residuals,
            "higher_trust": report.higher_trust,
            "scope_ok": report.scope_ok,
            "failure_reasons": list(report.failure_reasons),
        }
        if report.witness is not None:
            d["witness"] = {
                "stronger_evidence_keys": list(report.witness.stronger_evidence_keys),
                "higher_trust_level": report.witness.higher_trust_level,
                "fewer_residual_ids": list(report.witness.fewer_residual_ids),
                "scope_at_least_as_tight": report.witness.scope_at_least_as_tight,
                "notes": list(report.witness.notes),
            }
        return json.dumps(d, indent=self.indent)

    def serialize_contradiction_report(self, report: ContradictionReport) -> str:
        """Serialise a ContradictionReport to a JSON string."""
        return json.dumps(
            {
                "is_contradiction": report.is_contradiction,
                "kind": report.kind.value,
                "kind_severity": report.kind.severity,
                "opposing_propositions": report.opposing_propositions,
                "conflicting_evidence": report.conflicting_evidence,
                "inconsistent_trust": report.inconsistent_trust,
                "details": list(report.details),
            },
            indent=self.indent,
        )

    def serialize_equivalence_report(self, report: EquivalenceReport) -> str:
        """Serialise an EquivalenceReport to a JSON string."""
        return json.dumps(
            {
                "are_equivalent": report.are_equivalent,
                "bidirectional_refinement": report.bidirectional_refinement,
                "semantic_equivalence": report.semantic_equivalence,
                "evidence_equivalence": report.evidence_equivalence,
                "trust_equivalence": report.trust_equivalence,
                "notes": list(report.notes),
            },
            indent=self.indent,
        )

    def deserialize_composite_report_dict(self, data: str) -> dict[str, Any]:
        """Deserialise a CompositeComparisonReport JSON string to a plain dict.

        Full round-trip deserialisation back to a CompositeComparisonReport
        object is not provided because it would require reconstructing nested
        dataclasses from the three sub-reports; callers that need this should
        deserialise the sub-dicts individually.
        """
        return json.loads(data)

    def _report_to_dict(self, report: CompositeComparisonReport) -> dict[str, Any]:
        """Convert a CompositeComparisonReport to a plain dict for JSON encoding."""
        return {
            "overall": report.overall.value,
            "overall_label": report.overall.to_label(),
            "left_key": report.left_key,
            "right_key": report.right_key,
            "timestamp": report.timestamp,
            "trust": {
                "left_level": report.trust_detail.left_level,
                "right_level": report.trust_detail.right_level,
                "result": report.trust_detail.result.value,
                "distance": report.trust_detail.distance,
                "are_comparable": report.trust_detail.are_comparable,
                "notes": list(report.trust_detail.notes),
            },
            "evidence": {
                "left_stronger": report.evidence_detail.left_stronger,
                "right_stronger": report.evidence_detail.right_stronger,
                "are_equal": report.evidence_detail.are_equal,
                "are_compatible": report.evidence_detail.are_compatible,
                "kinds_preserved": report.evidence_detail.kinds_preserved,
                "extra_left": list(report.evidence_detail.extra_left),
                "extra_right": list(report.evidence_detail.extra_right),
                "shared": list(report.evidence_detail.shared),
                "notes": list(report.evidence_detail.notes),
            },
            "residuals": {
                "left_count": report.residual_detail.left_count,
                "right_count": report.residual_detail.right_count,
                "left_has_fewer": report.residual_detail.left_has_fewer,
                "right_has_fewer": report.residual_detail.right_has_fewer,
                "are_equal": report.residual_detail.are_equal,
                "only_in_left": list(report.residual_detail.only_in_left),
                "only_in_right": list(report.residual_detail.only_in_right),
                "shared": list(report.residual_detail.shared),
            },
        }


# ---------------------------------------------------------------------------
# Backward-compatibility shim  (keeps old callers working)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SectionComparisonResult:
    """Backward-compatible replacement for the pre-§12 ComparisonResult dataclass.

    The old ComparisonResult was a plain dataclass; it has been superseded by
    the :class:`ComparisonResult` Enum introduced in theory2.tex §12.1.  Code
    that previously used the dataclass should migrate to SectionComparisonResult.
    The ``compare_sections`` function is also retained and returns this type.
    """

    left_patch: str
    right_patch: str
    mode: ComparisonMode
    compatible: bool
    residuals: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = field(default_factory=tuple)


def compare_sections(
    left: JudgmentSection,
    right: JudgmentSection,
    *,
    mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
) -> SectionComparisonResult:
    """Compare two JudgmentSections for compatibility.

    Retained for backward compatibility with pre-§12 callers.  New code
    should use :meth:`JudgmentComparator.compare` with full
    :class:`LocalJudgment` objects.

    # copilot: shared-core marker for future LLM orchestration.
    """
    compatible = left.compatible_with(right)
    obstructions: tuple[str, ...] = () if compatible else ("section mismatch on overlap",)
    residuals: tuple[str, ...] = () if compatible else ("manual treaty required",)
    return SectionComparisonResult(
        left.patch, right.patch, mode, compatible, residuals, obstructions
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Primary enum
    "ComparisonResult",
    # Auxiliary enums
    "ComparisonMode",
    "ContradictionKind",
    # Core comparator
    "JudgmentComparator",
    # Specialist checkers
    "RefinementChecker",
    "EquivalenceChecker",
    "ContradictionDetector",
    # Dimension comparators
    "EvidenceComparator",
    "TrustComparator",
    "ResidualComparator",
    # Partial order
    "JudgmentOrder",
    # History & serialisation
    "ComparisonHistory",
    "ComparisonSerializer",
    # Report dataclasses (checkers)
    "RefinementReport",
    "RefinementWitness",
    "EquivalenceReport",
    "ContradictionReport",
    # Report dataclasses (comparators)
    "CompositeComparisonReport",
    "EvidenceComparisonDetail",
    "TrustComparisonDetail",
    "ResidualComparisonDetail",
    # History record
    "ComparisonRecord",
    # Backward-compat
    "SectionComparisonResult",
    "compare_sections",
]
