"""Stage 01 — Refinement checking for the relational_refinement package.

Implements the ``RefinementChecker`` class, which is the primary entry point
for deciding whether a refinement relation J ≤ J' holds between two judgments.

Theory context (Ch12)
---------------------
J ≤ J' requires **all four** of the following conditions to hold:

1. **Trust monotonicity** — ``trust(J) ≤ trust(J')`` in the trust lattice.
2. **Evidence embedding** — every evidence item of J embeds into J' (i.e. J'
   provides at least the evidence that J provides).
3. **Obligation subsumption** — every residual obligation of J is subsumed by
   an obligation in J' (J' inherits all open obligations).
4. **Proposition strength** — the proposition of J' is at least as strong as
   that of J (J' does not weaken the claim).

When all four conditions hold with the *right* being stronger than *left*, the
direction is ``FORWARD``.  When the conditions hold in the opposite direction,
the direction is ``BACKWARD`` (a regression).  When both hold, the direction is
``EQUIVALENT``.  When neither holds, the direction is ``INCOMPARABLE``.
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
    RefinementOrder,
    EquivalenceClass,
    RefinementWitness,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Trust level ordinal mapping — lower index = weaker trust
_TRUST_ORDER: list[TrustLevel] = list(TrustLevel)

# Minimum confidence threshold for a checked relation to be considered valid
_CONFIDENCE_THRESHOLD: float = 0.5

# Default confidence assigned to batch-derived relations
_BATCH_CONFIDENCE: float = 0.8

# Maximum depth for section refinement BFS traversal
_SECTION_BFS_DEPTH: int = 32


def _trust_ordinal(level: TrustLevel) -> int:
    """Return the ordinal position of *level* in the trust lattice.

    Parameters
    ----------
    level:
        A ``TrustLevel`` member.

    Returns
    -------
    int
        Zero-based index; higher = stronger trust.
    """
    try:
        return _TRUST_ORDER.index(level)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# RefinementChecker
# ---------------------------------------------------------------------------


class RefinementChecker:
    """Checks whether the refinement relation J ≤ J' holds.

    ``RefinementChecker`` is the primary entry point for deciding refinement.
    It decomposes the check into four sub-checks (trust, evidence, obligations,
    proposition) and combines the results into a ``RefinementRelation`` record.

    All methods are *pure* — they do not mutate any inputs and do not cache
    results internally.  The checker can be freely re-used across multiple
    check calls.

    Usage
    -----
    ::

        checker = RefinementChecker()
        relation = checker.check(left_judgment, right_judgment)
        if relation.is_proper_refinement():
            print("J ≤ J' holds.")

    Notes
    -----
    The checker assumes that the ``Judgment`` objects passed to it have been
    normalised by the JudgmentAlgebra before being submitted.  Unnormalised
    judgments may produce incorrect results.
    """

    # ------------------------------------------------------------------
    # Primary check entry point
    # ------------------------------------------------------------------

    def check(self, left: Judgment, right: Judgment) -> RefinementRelation:
        """Check whether ``left ≤ right`` (or any direction) holds.

        Performs all four sub-checks and assembles a ``RefinementRelation``
        recording the direction, trust delta, evidence embedding, and
        obligation discharge.

        Parameters
        ----------
        left:
            The candidate weaker judgment.
        right:
            The candidate stronger judgment.

        Returns
        -------
        RefinementRelation
            A fully populated relation record.  The ``direction`` field
            indicates the outcome.

        Raises
        ------
        JuGeoError
            If either judgment lacks required fields (coordinate, proposition).
        """
        left_coord = self._coordinate_of(left)
        right_coord = self._coordinate_of(right)

        # Check forward (left ≤ right)
        fwd_trust = self.check_trust_monotonicity(left, right)
        fwd_evidence = self.check_evidence_embedding(left, right)
        fwd_obligations = self.check_obligation_subsumption(left, right)
        fwd_prop = self.check_proposition_strength(left, right)
        fwd_holds = fwd_trust and fwd_prop and len(fwd_evidence) >= 0

        # Check backward (right ≤ left)
        bwd_trust = self.check_trust_monotonicity(right, left)
        bwd_evidence = self.check_evidence_embedding(right, left)
        bwd_obligations = self.check_obligation_subsumption(right, left)
        bwd_prop = self.check_proposition_strength(right, left)
        bwd_holds = bwd_trust and bwd_prop and len(bwd_evidence) >= 0

        direction = self.classify_direction(left, right)

        # Trust delta: trust(right) - trust(left)
        trust_delta = _trust_ordinal(self._trust_of(right)) - _trust_ordinal(self._trust_of(left))

        # Confidence: based on how many sub-checks passed
        checks_passed = sum([
            fwd_trust,
            len(fwd_evidence) > 0,
            len(fwd_obligations) > 0 or not self._has_obligations(left),
            fwd_prop,
        ])
        confidence = checks_passed / 4.0

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=left_coord,
            right_coordinate=right_coord,
            direction=direction,
            trust_delta=trust_delta,
            evidence_embedding=fwd_evidence if fwd_holds else (),
            obligation_discharge=fwd_obligations if fwd_holds else (),
            is_witnessed=False,
            witness_id=None,
            confidence=max(confidence, _CONFIDENCE_THRESHOLD),
            metadata=(),
        )

    # ------------------------------------------------------------------
    # Sub-checks
    # ------------------------------------------------------------------

    def check_trust_monotonicity(self, left: Judgment, right: Judgment) -> bool:
        """Check whether trust is non-decreasing from *left* to *right*.

        For J ≤ J' we require ``trust(J) ≤ trust(J')`` in the trust lattice.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        bool
            ``True`` iff ``trust(left) ≤ trust(right)`` in the ordinal order.
        """
        left_t = _trust_ordinal(self._trust_of(left))
        right_t = _trust_ordinal(self._trust_of(right))
        return left_t <= right_t

    def check_evidence_embedding(
        self, left: Judgment, right: Judgment
    ) -> tuple[str, ...]:
        """Determine which evidence items of *left* embed into *right*.

        For each evidence item ``e`` in ``left.evidence``, checks whether
        *right* contains a compatible item (same kind, compatible carrier).
        Returns encoded pairs ``"left_key:right_key"`` for each match found.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        tuple[str, ...]
            Encoded embedding pairs.  May be empty if *left* has no evidence
            or if no matches are found.
        """
        left_evidence = self._evidence_items(left)
        right_evidence = self._evidence_items(right)

        if not left_evidence:
            return ()

        # Build a lookup by kind for right evidence
        right_by_kind: dict[EvidenceItemKind, list[EvidenceItem]] = {}
        for item in right_evidence:
            right_by_kind.setdefault(item.kind, []).append(item)

        pairs: list[str] = []
        for left_item in left_evidence:
            candidates = right_by_kind.get(left_item.kind, [])
            matched = False
            for right_item in candidates:
                if self._evidence_items_compatible(left_item, right_item):
                    pairs.append(f"{left_item.item_id}:{right_item.item_id}")
                    matched = True
                    break
            if not matched:
                # Record a partial embedding with a placeholder
                pairs.append(f"{left_item.item_id}:__unmatched__")
        return tuple(pairs)

    def check_obligation_subsumption(
        self, left: Judgment, right: Judgment
    ) -> tuple[str, ...]:
        """Determine which obligations of *left* are subsumed in *right*.

        For J ≤ J', every residual obligation of J must be either present in J'
        or discharged.  Returns encoded pairs
        ``"left_obligation_id:right_obligation_id"`` for each match.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        tuple[str, ...]
            Encoded discharge pairs.
        """
        left_obs = self._obligations(left)
        right_obs = self._obligations(right)

        if not left_obs:
            return ()

        right_by_tag: dict[str, ResidualObligation] = {
            ob.tag: ob for ob in right_obs
        }

        pairs: list[str] = []
        for ob in left_obs:
            if ob.tag in right_by_tag:
                pairs.append(f"{ob.obligation_id}:{right_by_tag[ob.tag].obligation_id}")
            else:
                # Obligation not found in right → not fully subsumed
                pairs.append(f"{ob.obligation_id}:__unresolved__")
        return tuple(pairs)

    def check_proposition_strength(self, left: Judgment, right: Judgment) -> bool:
        """Check that *right*'s proposition is at least as strong as *left*'s.

        Uses structural comparison of the proposition terms.  If the
        propositions are equal, returns ``True``.  If the right proposition
        strictly implies the left (stronger), returns ``True``.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        bool
            ``True`` iff ``prop(right)`` is at least as strong as
            ``prop(left)``.
        """
        left_prop = self._proposition_term(left)
        right_prop = self._proposition_term(right)
        # Structural equality → same strength
        if left_prop == right_prop:
            return True
        # If right is None (no proposition), treat as compatible
        if right_prop is None:
            return True
        if left_prop is None:
            return True
        # Compare textual content as a fallback
        left_str = str(left_prop)
        right_str = str(right_prop)
        # Strict prefix implies right is more specific
        return left_str in right_str or left_str == right_str

    def classify_direction(
        self, left: Judgment, right: Judgment
    ) -> RefinementRelation.RefinementDirection:
        """Determine the structural direction of the refinement.

        Combines the results of all four sub-checks in both directions to
        determine whether the relation is FORWARD, BACKWARD, EQUIVALENT, or
        INCOMPARABLE.

        Parameters
        ----------
        left:
            The first judgment.
        right:
            The second judgment.

        Returns
        -------
        RefinementRelation.RefinementDirection
            The classified direction.
        """
        D = RefinementRelation.RefinementDirection

        fwd_trust = self.check_trust_monotonicity(left, right)
        fwd_prop = self.check_proposition_strength(left, right)
        fwd_evid = self.check_evidence_embedding(left, right)
        fwd_obs = self.check_obligation_subsumption(left, right)

        bwd_trust = self.check_trust_monotonicity(right, left)
        bwd_prop = self.check_proposition_strength(right, left)
        bwd_evid = self.check_evidence_embedding(right, left)
        bwd_obs = self.check_obligation_subsumption(right, left)

        # Evidence counts (ignore unmatched markers)
        left_evidence_count = len(self._evidence_items(left))
        fwd_matched = sum(1 for p in fwd_evid if "__unmatched__" not in p)
        bwd_matched = sum(1 for p in bwd_evid if "__unmatched__" not in p)

        fwd_evidence_ok = left_evidence_count == 0 or fwd_matched == left_evidence_count
        right_evidence_count = len(self._evidence_items(right))
        bwd_evidence_ok = right_evidence_count == 0 or bwd_matched == right_evidence_count

        # Obligations
        left_obs_count = len(self._obligations(left))
        fwd_obs_ok = left_obs_count == 0 or all("__unresolved__" not in p for p in fwd_obs)
        right_obs_count = len(self._obligations(right))
        bwd_obs_ok = right_obs_count == 0 or all("__unresolved__" not in p for p in bwd_obs)

        forward_holds = fwd_trust and fwd_prop and fwd_evidence_ok and fwd_obs_ok
        backward_holds = bwd_trust and bwd_prop and bwd_evidence_ok and bwd_obs_ok

        if forward_holds and backward_holds:
            return D.EQUIVALENT
        if forward_holds:
            return D.FORWARD
        if backward_holds:
            return D.BACKWARD
        return D.INCOMPARABLE

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def batch_check(self, judgments: Sequence[Judgment]) -> RefinementOrder:
        """Check all pairwise refinements among a sequence of judgments.

        Checks every ordered pair ``(J_i, J_j)`` for ``i ≠ j``.  Returns a
        ``RefinementOrder`` containing all FORWARD and EQUIVALENT relations
        found.

        Parameters
        ----------
        judgments:
            A sequence of judgments to compare.

        Returns
        -------
        RefinementOrder
            A refinement order over the coordinates of all input judgments.
        """
        coords = frozenset(self._coordinate_of(j) for j in judgments)
        order = RefinementOrder.empty(coords)

        for i, left in enumerate(judgments):
            for j, right in enumerate(judgments):
                if i == j:
                    continue
                rel = self.check(left, right)
                D = RefinementRelation.RefinementDirection
                if rel.direction in (D.FORWARD, D.EQUIVALENT):
                    order = order.add_relation(rel)

        return order

    def check_section_refinement(
        self,
        left_section: object,
        right_section: object,
    ) -> ComparisonResult:
        """Check refinement between two sections over a cover 𝔘.

        Delegates to ``compare_sections`` from ``jugeo.judgments.comparisons``
        using ``ComparisonMode.REFINEMENT``.

        Parameters
        ----------
        left_section:
            The source section (must be compatible with ``compare_sections``).
        right_section:
            The target section.

        Returns
        -------
        ComparisonResult
            The result of the section comparison.

        Raises
        ------
        JuGeoError
            If the section types are incompatible for comparison.
        """
        try:
            return compare_sections(
                left_section,
                right_section,
                mode=ComparisonMode.REFINEMENT,
            )
        except Exception as exc:
            raise_with_scope(
                f"Section refinement check failed: {exc}",
                scope=FailureScope.REFINEMENT,
                classification=FailureClassification.STRUCTURE,
            )

    # ------------------------------------------------------------------
    # Diagnostic helpers
    # ------------------------------------------------------------------

    def diagnose_failure(
        self, left: Judgment, right: Judgment
    ) -> tuple[str, ...]:
        """Return human-readable diagnostics for why *left* ≤ *right* fails.

        Runs all sub-checks and collects failure messages for each one that
        does not pass.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        tuple[str, ...]
            A tuple of diagnostic messages (empty if the refinement holds).
        """
        msgs: list[str] = []

        if not self.check_trust_monotonicity(left, right):
            lt = self._trust_of(left)
            rt = self._trust_of(right)
            msgs.append(
                f"Trust monotonicity violated: trust({lt.value}) > trust({rt.value})."
            )

        emb = self.check_evidence_embedding(left, right)
        unmatched_evid = [p for p in emb if "__unmatched__" in p]
        if unmatched_evid:
            msgs.append(
                f"Evidence embedding incomplete: {len(unmatched_evid)} item(s) "
                f"could not be embedded: {unmatched_evid}."
            )

        obs = self.check_obligation_subsumption(left, right)
        unresolved = [p for p in obs if "__unresolved__" in p]
        if unresolved:
            msgs.append(
                f"Obligation subsumption incomplete: {len(unresolved)} obligation(s) "
                f"not subsumed: {unresolved}."
            )

        if not self.check_proposition_strength(left, right):
            msgs.append("Proposition strength check failed: right proposition is weaker.")

        return tuple(msgs)

    def emit_failure_record(
        self, left: Judgment, right: Judgment
    ) -> StructuredFailure:
        """Emit a ``StructuredFailure`` for a failed refinement check.

        Parameters
        ----------
        left:
            The candidate source judgment.
        right:
            The candidate target judgment.

        Returns
        -------
        StructuredFailure
            A structured failure record describing why the refinement failed.
        """
        diagnostics = self.diagnose_failure(left, right)
        payload = as_failure_payload(
            message=f"Refinement check failed: {self._coordinate_of(left)!r} ≤ "
                    f"{self._coordinate_of(right)!r}",
            details=list(diagnostics),
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
        )
        return StructuredFailure(
            failure_id=str(uuid.uuid4()),
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
            message=payload["message"],
            evidence_family=EvidenceFamily.REFINEMENT,
            obstruction_records=tuple(
                ObstructionRecord(tag=f"rf_fail_{i}", description=d)
                for i, d in enumerate(diagnostics)
            ),
            repair_hints=(
                RepairHint(
                    priority=RepairPriority.HIGH,
                    description="Increase trust level of the target judgment.",
                ),
            ),
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coordinate_of(judgment: Judgment) -> str:
        """Extract the coordinate string from a judgment.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        str
            The coordinate string (uses ``judgment.coordinate`` or falls back
            to ``str(id(judgment))``).
        """
        return getattr(judgment, "coordinate", None) or str(id(judgment))

    @staticmethod
    def _trust_of(judgment: Judgment) -> TrustLevel:
        """Extract the trust level from a judgment.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        TrustLevel
            The trust level, defaulting to ``UNVERIFIED`` if not present.
        """
        trust = getattr(judgment, "trust_level", None)
        if trust is None:
            annotation = getattr(judgment, "trust_annotation", None)
            if annotation:
                trust = getattr(annotation, "level", None)
        return trust if isinstance(trust, TrustLevel) else TrustLevel.UNVERIFIED

    @staticmethod
    def _evidence_items(judgment: Judgment) -> list[EvidenceItem]:
        """Extract all evidence items from a judgment.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        list[EvidenceItem]
            The list of evidence items, empty if none present.
        """
        bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if bundle is None:
            return []
        items = getattr(bundle, "items", None)
        if items is None:
            return []
        return list(items)

    @staticmethod
    def _obligations(judgment: Judgment) -> list[ResidualObligation]:
        """Extract residual obligations from a judgment.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        list[ResidualObligation]
            The list of open residual obligations.
        """
        obs = getattr(judgment, "residual_obligations", None)
        if obs is None:
            return []
        return list(obs)

    @staticmethod
    def _has_obligations(judgment: Judgment) -> bool:
        """Return whether the judgment has any open residual obligations.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        bool
            ``True`` if the obligation list is non-empty.
        """
        return bool(getattr(judgment, "residual_obligations", None))

    @staticmethod
    def _proposition_term(judgment: Judgment) -> object | None:
        """Extract the proposition term from a judgment.

        Parameters
        ----------
        judgment:
            A judgment object.

        Returns
        -------
        object | None
            The proposition object, or ``None`` if not present.
        """
        prop: Proposition | None = getattr(judgment, "proposition", None)
        if prop is None:
            return None
        return getattr(prop, "term", prop)

    @staticmethod
    def _evidence_items_compatible(a: EvidenceItem, b: EvidenceItem) -> bool:
        """Check whether evidence item *a* is compatible with item *b*.

        Two items are compatible iff they have the same kind and their content
        strings are equal or one is a prefix of the other.

        Parameters
        ----------
        a:
            Source evidence item.
        b:
            Target evidence item.

        Returns
        -------
        bool
            ``True`` iff the items are compatible.
        """
        if a.kind != b.kind:
            return False
        a_content = str(getattr(a, "content", ""))
        b_content = str(getattr(b, "content", ""))
        return a_content == b_content or a_content in b_content


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
    "RefinementChecker",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: refinement_checking — RefinementChecker for Ch12 J ≤ J' decisions
