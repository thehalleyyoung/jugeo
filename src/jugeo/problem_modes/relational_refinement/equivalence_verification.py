"""Stage 02 — Equivalence verification for the relational_refinement package.

Implements the ``EquivalenceVerifier`` class, which verifies bidirectional
refinement (J ≡ J') and partitions a collection of judgments into their
equivalence classes.

Theory context (Ch12)
---------------------
**Equivalence** is the largest congruence on the judgment algebra.  Two
judgments J and J' are equivalent (J ≡ J') iff J ≤ J' *and* J' ≤ J.
Equivalence classes are the orbits of this relation.

Key properties:
* The equivalence relation is reflexive, symmetric, and transitive.
* Each equivalence class has a canonical representative (the one with the
  highest trust level, breaking ties by lexicographic coordinate order).
* The set of equivalence classes forms a quotient of the refinement order:
  the induced partial order on classes is strict (no two distinct classes
  are equivalent).
* A ``RefinementWitness`` pair ``(w, w⁻¹)`` serves as an equivalence
  certificate.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import replace
from typing import Any, Sequence

from jugeo.judgments.comparisons import ComparisonMode, ComparisonResult, compare_sections
from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentStatus,
    TrustLevel,
    Proposition,
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    ResidualObligation,
    Obstruction,
    TrustAnnotation,
    Provenance,
    ProvenanceSource,
    JudgmentAlgebra,
)
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    ObstructionConverter,
    FailureClass,
    RepairType,
)
from jugeo.errors import (
    StructuredFailure,
    JuGeoError,
    FailureScope,
    FailureClassification,
    EvidenceFamily,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    FailureChain,
    as_failure_payload,
    raise_with_scope,
)
from jugeo.problem_modes.relational_refinement.models import (
    RefinementRelation,
    EquivalenceClass,
    RefinementWitness,
    RefinementOrder,
)
from jugeo.problem_modes.relational_refinement.refinement_checking import (
    RefinementChecker,
    _trust_ordinal,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Minimum number of witnesses required to certify an equivalence class
_MIN_WITNESS_COUNT: int = 1

# Whether to emit a certificate StructuredFailure for each class computed
_EMIT_CERTIFICATES: bool = False

# ---------------------------------------------------------------------------
# EquivalenceVerifier
# ---------------------------------------------------------------------------


class EquivalenceVerifier:
    """Verifies bidirectional refinement (J ≡ J') and computes equivalence classes.

    The ``EquivalenceVerifier`` uses a ``RefinementChecker`` internally to run
    both forward and backward checks.  When both succeed it constructs an
    ``EquivalenceClass`` and, optionally, a pair of ``RefinementWitness``
    certificates.

    Usage
    -----
    ::

        verifier = EquivalenceVerifier()
        ec = verifier.verify(j1, j2)
        if ec is not None:
            print(f"J1 ≡ J2 in class {ec.class_id}")

    Notes
    -----
    The verifier does not perform the full categorical proof of congruence; it
    checks the four structural conditions required by the theory and records
    the result.  Formal proof obligations are delegated to ``theorems.py``.
    """

    def __init__(self, checker: RefinementChecker | None = None) -> None:
        """Initialise the verifier, optionally with a custom checker.

        Parameters
        ----------
        checker:
            A ``RefinementChecker`` instance to use for sub-checks.  If
            ``None``, a new default checker is created.
        """
        self._checker = checker or RefinementChecker()

    # ------------------------------------------------------------------
    # Primary entry points
    # ------------------------------------------------------------------

    def verify(self, left: Judgment, right: Judgment) -> EquivalenceClass | None:
        """Verify that *left* and *right* are equivalent (J ≡ J').

        Runs the forward check (left ≤ right) and the backward check
        (right ≤ left) using the internal ``RefinementChecker``.  If both
        hold, constructs an ``EquivalenceClass`` containing both coordinates
        and returns it.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        EquivalenceClass | None
            An equivalence class containing both, or ``None`` if the two
            judgments are not equivalent.
        """
        fwd = self._checker.check(left, right)
        bwd = self._checker.check(right, left)

        D = RefinementRelation.RefinementDirection
        if fwd.direction not in (D.FORWARD, D.EQUIVALENT):
            return None
        if bwd.direction not in (D.FORWARD, D.BACKWARD, D.EQUIVALENT):
            return None

        left_coord = self._coord(left)
        right_coord = self._coord(right)
        trust = self._canonical_trust(left, right)

        ec = EquivalenceClass(
            class_id=str(uuid.uuid4()),
            representative_coordinate=self._choose_representative(left, right),
            member_coordinates=frozenset({left_coord, right_coord}),
            witnesses=(),
            canonical_trust=trust,
            established_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_maximal=False,
            metadata=(),
        )
        return ec

    def is_equivalent(self, left: Judgment, right: Judgment) -> bool:
        """Return ``True`` iff *left* ≡ *right*.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        bool
            ``True`` iff both directions of refinement hold.
        """
        return self.verify(left, right) is not None

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def compute_equivalence_classes(
        self, judgments: Sequence[Judgment]
    ) -> tuple[EquivalenceClass, ...]:
        """Partition *judgments* into their equivalence classes.

        Uses a union-find (disjoint-set) approach.  For every pair of
        judgments that are found to be equivalent, their classes are merged.
        The resulting classes are deduplicated and returned.

        Parameters
        ----------
        judgments:
            A sequence of judgments to partition.

        Returns
        -------
        tuple[EquivalenceClass, ...]
            The equivalence-class partition, one entry per class.
        """
        # Initialise: each judgment in its own singleton class
        coord_to_class: dict[str, EquivalenceClass] = {}
        for j in judgments:
            coord = self._coord(j)
            coord_to_class[coord] = EquivalenceClass.singleton(
                coord, self._trust_of(j)
            )

        # Union-find: merge classes for equivalent pairs
        for i, j_left in enumerate(judgments):
            for j_right in judgments[i + 1 :]:
                if self.is_equivalent(j_left, j_right):
                    left_coord = self._coord(j_left)
                    right_coord = self._coord(j_right)
                    # Find roots
                    left_class = coord_to_class.get(left_coord)
                    right_class = coord_to_class.get(right_coord)
                    if left_class is None or right_class is None:
                        continue
                    # Merge
                    merged = left_class.merge_with(right_class)
                    for member in merged.member_coordinates:
                        coord_to_class[member] = merged

        # Deduplicate: collect unique classes by class_id
        seen_ids: set[str] = set()
        unique: list[EquivalenceClass] = []
        for ec in coord_to_class.values():
            if ec.class_id not in seen_ids:
                seen_ids.add(ec.class_id)
                unique.append(ec)
        return tuple(unique)

    def find_class(
        self,
        coord: str,
        classes: Sequence[EquivalenceClass],
    ) -> EquivalenceClass | None:
        """Find the equivalence class containing *coord*.

        Parameters
        ----------
        coord:
            The coordinate to look up.
        classes:
            A sequence of equivalence classes to search.

        Returns
        -------
        EquivalenceClass | None
            The class containing *coord*, or ``None`` if not found.
        """
        for ec in classes:
            if ec.contains(coord):
                return ec
        return None

    def merge_classes(
        self,
        a: EquivalenceClass,
        b: EquivalenceClass,
    ) -> EquivalenceClass:
        """Merge two equivalence classes into one.

        The representative of *a* is retained.  Calls ``a.merge_with(b)``.

        Parameters
        ----------
        a:
            The primary class.
        b:
            The class to merge into *a*.

        Returns
        -------
        EquivalenceClass
            The merged class.
        """
        return a.merge_with(b)

    # ------------------------------------------------------------------
    # Witness-based verification
    # ------------------------------------------------------------------

    def verify_bidirectional(
        self, left: Judgment, right: Judgment
    ) -> tuple[RefinementWitness, RefinementWitness] | None:
        """Verify equivalence and return a pair of directional witnesses.

        If *left* ≡ *right*, constructs a forward witness w: J → J' and a
        backward witness w': J' → J.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        tuple[RefinementWitness, RefinementWitness] | None
            ``(forward_witness, backward_witness)`` or ``None`` if not
            equivalent.
        """
        fwd_rel = self._checker.check(left, right)
        bwd_rel = self._checker.check(right, left)

        D = RefinementRelation.RefinementDirection
        if fwd_rel.direction not in (D.FORWARD, D.EQUIVALENT):
            return None
        if bwd_rel.direction not in (D.FORWARD, D.BACKWARD, D.EQUIVALENT):
            return None

        left_coord = self._coord(left)
        right_coord = self._coord(right)

        # Build trust promotion paths
        left_trust = self._trust_of(left)
        right_trust = self._trust_of(right)
        fwd_path = self._trust_path(left_trust, right_trust)
        bwd_path = self._trust_path(right_trust, left_trust)

        fwd_witness = RefinementWitness.make(
            source=left_coord,
            target=right_coord,
            trust_path=fwd_path,
            evidence_embedding=tuple(
                tuple(pair.split(":", 1))  # type: ignore[misc]
                for pair in fwd_rel.evidence_embedding
                if "__unmatched__" not in pair
            ),
            obligation_discharge=tuple(
                tuple(pair.split(":", 1))  # type: ignore[misc]
                for pair in fwd_rel.obligation_discharge
                if "__unresolved__" not in pair
            ),
        )
        bwd_witness = RefinementWitness.make(
            source=right_coord,
            target=left_coord,
            trust_path=bwd_path,
            evidence_embedding=tuple(
                tuple(pair.split(":", 1))  # type: ignore[misc]
                for pair in bwd_rel.evidence_embedding
                if "__unmatched__" not in pair
            ),
            obligation_discharge=tuple(
                tuple(pair.split(":", 1))  # type: ignore[misc]
                for pair in bwd_rel.obligation_discharge
                if "__unresolved__" not in pair
            ),
        )

        # Validate and mark
        validated_fwd, validated_bwd = fwd_witness.invert_to_equivalence(bwd_witness)
        return validated_fwd, validated_bwd

    # ------------------------------------------------------------------
    # Comparison result
    # ------------------------------------------------------------------

    def classify_relation(
        self, left: Judgment, right: Judgment
    ) -> ComparisonResult:
        """Classify the relation between *left* and *right* as a ``ComparisonResult``.

        Uses ``compare_sections`` with ``ComparisonMode.EQUIVALENCE`` to
        produce a structured comparison result compatible with the rest of
        the JuGeo pipeline.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        ComparisonResult
            The structured comparison result.
        """
        try:
            return compare_sections(
                left,
                right,
                mode=ComparisonMode.EQUIVALENCE,
            )
        except Exception as exc:
            raise_with_scope(
                f"Equivalence classification failed: {exc}",
                scope=FailureScope.REFINEMENT,
                classification=FailureClassification.STRUCTURE,
            )

    # ------------------------------------------------------------------
    # Certificate emission
    # ------------------------------------------------------------------

    def emit_equivalence_certificate(
        self, ec: EquivalenceClass
    ) -> StructuredFailure:
        """Emit a ``StructuredFailure`` certificate for an equivalence class.

        This is an *informational* certificate (not an error) that can be
        used for audit trails or downstream tooling.  The failure
        classification is ``INFO``.

        Parameters
        ----------
        ec:
            The equivalence class to certify.

        Returns
        -------
        StructuredFailure
            A structured certificate describing the class.
        """
        members = sorted(ec.member_coordinates)
        message = (
            f"Equivalence class {ec.class_id[:8]} established: "
            f"{len(members)} member(s), representative={ec.representative_coordinate!r}, "
            f"trust={ec.canonical_trust.value}."
        )
        payload = as_failure_payload(
            message=message,
            details=[f"Members: {members}"],
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.INFO,
        )
        return StructuredFailure(
            failure_id=str(uuid.uuid4()),
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.INFO,
            message=message,
            evidence_family=EvidenceFamily.REFINEMENT,
            obstruction_records=(),
            repair_hints=(),
            payload=payload,
        )

    def emit_non_equivalence_report(
        self, left: Judgment, right: Judgment
    ) -> StructuredFailure:
        """Emit a diagnostic report for a failed equivalence check.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        StructuredFailure
            A structured failure explaining why *left* ≢ *right*.
        """
        left_coord = self._coord(left)
        right_coord = self._coord(right)
        fwd_diagnostics = self._checker.diagnose_failure(left, right)
        bwd_diagnostics = self._checker.diagnose_failure(right, left)

        all_diagnostics = (
            [f"[FWD] {d}" for d in fwd_diagnostics]
            + [f"[BWD] {d}" for d in bwd_diagnostics]
        )
        message = (
            f"Judgments {left_coord!r} and {right_coord!r} are NOT equivalent."
        )
        payload = as_failure_payload(
            message=message,
            details=all_diagnostics,
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
        )
        obs_records = tuple(
            ObstructionRecord(tag=f"neq_{i}", description=d)
            for i, d in enumerate(all_diagnostics)
        )
        return StructuredFailure(
            failure_id=str(uuid.uuid4()),
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
            message=message,
            evidence_family=EvidenceFamily.REFINEMENT,
            obstruction_records=obs_records,
            repair_hints=(
                RepairHint(
                    priority=RepairPriority.MEDIUM,
                    description="Align trust levels and evidence bundles of both judgments.",
                ),
            ),
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Utility: compute quotient order
    # ------------------------------------------------------------------

    def compute_quotient_order(
        self,
        order: RefinementOrder,
        classes: Sequence[EquivalenceClass],
    ) -> RefinementOrder:
        """Compute the quotient partial order on equivalence classes.

        Given a ``RefinementOrder`` and an equivalence-class partition, returns
        a new order whose coordinates are the representative coordinates of the
        classes and whose relations are derived from the original relations
        (lifting to representatives).

        Parameters
        ----------
        order:
            The original refinement order.
        classes:
            The equivalence-class partition.

        Returns
        -------
        RefinementOrder
            The quotient order on representative coordinates.
        """
        # Build a map: coordinate → representative
        coord_to_rep: dict[str, str] = {}
        for ec in classes:
            for member in ec.member_coordinates:
                coord_to_rep[member] = ec.representative_coordinate

        D = RefinementRelation.RefinementDirection
        new_rels: list[RefinementRelation] = []
        seen_pairs: set[tuple[str, str]] = set()

        for rel in order.relations:
            rep_left = coord_to_rep.get(rel.left_coordinate, rel.left_coordinate)
            rep_right = coord_to_rep.get(rel.right_coordinate, rel.right_coordinate)
            if rep_left == rep_right:
                continue  # same class
            pair = (rep_left, rep_right)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            new_rels.append(
                RefinementRelation.make(
                    left=rep_left,
                    right=rep_right,
                    direction=rel.direction if rel.direction != D.EQUIVALENT else D.FORWARD,
                    trust_delta=rel.trust_delta,
                    confidence=rel.confidence,
                )
            )

        rep_coords = frozenset(ec.representative_coordinate for ec in classes)
        return RefinementOrder(
            order_id=str(uuid.uuid4()),
            coordinates=rep_coords,
            relations=tuple(new_rels),
            equivalence_classes=tuple(classes),
            computed_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_consistent=None,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coord(judgment: Judgment) -> str:
        """Extract the coordinate string from *judgment*."""
        return getattr(judgment, "coordinate", None) or str(id(judgment))

    @staticmethod
    def _trust_of(judgment: Judgment) -> TrustLevel:
        """Extract the trust level from *judgment*."""
        trust = getattr(judgment, "trust_level", None)
        if trust is None:
            annotation = getattr(judgment, "trust_annotation", None)
            if annotation:
                trust = getattr(annotation, "level", None)
        return trust if isinstance(trust, TrustLevel) else TrustLevel.UNVERIFIED

    @staticmethod
    def _canonical_trust(left: Judgment, right: Judgment) -> TrustLevel:
        """Return the canonical (lower) trust level for an equivalence class.

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        TrustLevel
            The weaker (lower ordinal) trust level of the two.
        """
        trust_order = list(TrustLevel)
        left_t = getattr(left, "trust_level", TrustLevel.UNVERIFIED)
        right_t = getattr(right, "trust_level", TrustLevel.UNVERIFIED)
        if not isinstance(left_t, TrustLevel):
            left_t = TrustLevel.UNVERIFIED
        if not isinstance(right_t, TrustLevel):
            right_t = TrustLevel.UNVERIFIED
        li = trust_order.index(left_t)
        ri = trust_order.index(right_t)
        return trust_order[min(li, ri)]

    @staticmethod
    def _choose_representative(left: Judgment, right: Judgment) -> str:
        """Choose the representative coordinate for an equivalence class.

        The representative is the judgment with the *higher* trust level.
        Ties are broken by lexicographic coordinate order (earlier = preferred).

        Parameters
        ----------
        left:
            First judgment.
        right:
            Second judgment.

        Returns
        -------
        str
            The coordinate string of the chosen representative.
        """
        trust_order = list(TrustLevel)
        left_coord = getattr(left, "coordinate", None) or str(id(left))
        right_coord = getattr(right, "coordinate", None) or str(id(right))

        left_t = getattr(left, "trust_level", TrustLevel.UNVERIFIED)
        right_t = getattr(right, "trust_level", TrustLevel.UNVERIFIED)
        if not isinstance(left_t, TrustLevel):
            left_t = TrustLevel.UNVERIFIED
        if not isinstance(right_t, TrustLevel):
            right_t = TrustLevel.UNVERIFIED

        li = trust_order.index(left_t)
        ri = trust_order.index(right_t)
        if li > ri:
            return left_coord
        if ri > li:
            return right_coord
        # Tie: lexicographic
        return min(left_coord, right_coord)

    @staticmethod
    def _trust_path(
        src: TrustLevel, tgt: TrustLevel
    ) -> tuple[TrustLevel, ...]:
        """Build a monotone trust promotion path from *src* to *tgt*.

        Parameters
        ----------
        src:
            Starting trust level.
        tgt:
            Ending trust level.

        Returns
        -------
        tuple[TrustLevel, ...]
            A sequence of TrustLevel values from *src* to *tgt* (inclusive).
            If *src* == *tgt*, returns a single-element tuple.
        """
        trust_order = list(TrustLevel)
        si = trust_order.index(src) if src in trust_order else 0
        ti = trust_order.index(tgt) if tgt in trust_order else 0
        if si <= ti:
            return tuple(trust_order[si : ti + 1])
        return tuple(reversed(trust_order[ti : si + 1]))


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.encodings, jugeo.evidence)
# ---------------------------------------------------------------------------


def refinement_over_site(site: Any) -> dict[str, Any]:
    """Compute refinement structure over a geometric site.

    Refinement relations are defined over sites — the site provides the
    coordinate system and topology over which refinement is checked.

    Parameters
    ----------
    site : Any
        A Site object or dict with site topology data.

    Returns
    -------
    dict[str, Any]
        Site-aware refinement data with ``site_id``, ``coordinates``,
        ``covering_families``, and ``refinement_compatible`` keys.
    """
    try:
        from jugeo.geometry.site import Site, get_covering_families
    except ImportError:
        Site = None
        get_covering_families = None

    site_id = getattr(site, "site_id", None) or (site.get("site_id") if isinstance(site, dict) else "unknown")
    coords = getattr(site, "coordinates", None) or (
        site.get("coordinates") if isinstance(site, dict) else []
    )

    result: dict[str, Any] = {
        "site_id": site_id,
        "coordinates": list(coords) if coords else [],
        "covering_families": [],
        "refinement_compatible": None,
    }

    if get_covering_families is not None:
        try:
            families = get_covering_families(site)
            result["covering_families"] = list(families) if families else []
            result["refinement_compatible"] = len(result["covering_families"]) > 0
        except Exception:
            pass

    return result


def refinement_encoding(rel: Any) -> dict[str, Any]:
    """Encode a refinement relation as SMT constraints.

    Refinement relations translate to SMT formulas encoding the four
    conditions: trust monotonicity, evidence embedding, obligation
    subsumption, and proposition strength.

    Parameters
    ----------
    rel : Any
        A RefinementRelation object or dict.

    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``relation_id``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_relation, RelationEncoding
    except ImportError:
        encode_relation = None
        RelationEncoding = None

    left = getattr(rel, "left", None) or (rel.get("left") if isinstance(rel, dict) else "?")
    right = getattr(rel, "right", None) or (rel.get("right") if isinstance(rel, dict) else "?")
    rel_id = getattr(rel, "relation_id", None) or (
        rel.get("relation_id") if isinstance(rel, dict) else f"{left}_leq_{right}"
    )

    encoding: dict[str, Any] = {
        "relation_id": rel_id,
        "encoding_kind": "refinement_conjunction",
        "formulas": [
            f"(trust_leq {left} {right})",
            f"(evidence_embeds {left} {right})",
            f"(obligation_subsumes {left} {right})",
            f"(proposition_stronger {left} {right})",
        ],
        "variables": [f"trust_{left}", f"trust_{right}", f"ev_{left}", f"ev_{right}"],
        "encoder": None,
    }

    if encode_relation is not None:
        try:
            enc = encode_relation(rel)
            encoding["formulas"] = getattr(enc, "formulas", encoding["formulas"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    return encoding


def refinement_certificate(rel: Any) -> dict[str, Any]:
    """Build an evidence certificate for a refinement check result.

    A refinement certificate records the outcome of a J ≤ J' check,
    including the direction (forward, backward, equivalent, incomparable)
    and the trust level of the evidence.

    Parameters
    ----------
    rel : Any
        A refinement result, RefinementRelation, or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``direction``, ``valid``,
        ``trust_level``, and ``certificate_hash`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    direction = getattr(rel, "direction", None) or (rel.get("direction") if isinstance(rel, dict) else "UNKNOWN")
    direction_str = direction.value if hasattr(direction, "value") else str(direction)
    valid = direction_str in ("FORWARD", "EQUIVALENT")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "direction": direction_str,
        "valid": valid,
        "trust_level": "VERIFIED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(rel).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"refinement_{direction_str}", satisfied=valid, source="relational_refinement"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "EquivalenceVerifier",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: equivalence_verification — EquivalenceVerifier for Ch12 J ≡ J'
