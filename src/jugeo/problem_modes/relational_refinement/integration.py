"""Integration module for the relational_refinement package.

Provides the ``RelationalRefinementIntegration`` class, which bridges the
refinement structures (``RefinementRelation``, ``RefinementOrder``,
``RefinementWitness``) with the JuGeo judgment algebra infrastructure
(``Judgment``, ``EvidenceBundle``, ``ResidualObligation``, ``FailureChain``).

Design goals
------------
* **Judgment enrichment** — attach refinement relations and witnesses to
  existing ``Judgment`` objects by adding evidence items and updating the
  trust annotation.
* **Obligation export** — export the residual obligations implied by a
  ``RefinementOrder`` as ``ResidualObligation`` objects.
* **Failure chain export** — export a ``RefinementOrder`` as a ``FailureChain``
  for downstream error reporting.
* **Comparison judgment generation** — synthesise a new ``Judgment`` that
  represents the comparison of two existing judgments.

All operations are *pure* and return new ``Judgment`` / ``EvidenceBundle``
instances via ``dataclasses.replace``.  They do not mutate their inputs.
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

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# EvidenceItemKind used for refinement-derived evidence items
_REFINEMENT_EVIDENCE_KIND = EvidenceItemKind.DERIVED

# Trust level used when the relation does not imply a trust change
_NEUTRAL_TRUST = TrustLevel.VERIFIED

# Tag prefix for refinement-derived residual obligations
_OBLIGATION_TAG_PREFIX: str = "refinement_obligation"

# ---------------------------------------------------------------------------
# RelationalRefinementIntegration
# ---------------------------------------------------------------------------


class RelationalRefinementIntegration:
    """Bridges refinement structures with the JuGeo judgment algebra.

    ``RelationalRefinementIntegration`` is responsible for connecting the
    relational refinement world (``RefinementRelation``, ``RefinementOrder``,
    ``RefinementWitness``) with the core judgment infrastructure
    (``Judgment``, ``EvidenceBundle``, ``ResidualObligation``,
    ``FailureChain``).

    All methods are *pure*: they accept immutable inputs and return new
    instances.  No mutable state is stored.

    Usage
    -----
    ::

        integration = RelationalRefinementIntegration()
        enriched = integration.integrate_with_judgment(judgment, relation)
        obligations = integration.export_refinement_obligations(order)
    """

    # ------------------------------------------------------------------
    # integrate_with_judgment
    # ------------------------------------------------------------------

    def integrate_with_judgment(
        self,
        judgment: Judgment,
        relation: RefinementRelation,
    ) -> Judgment:
        """Attach a refinement relation to a judgment as a derived annotation.

        Creates a new ``EvidenceItem`` encoding the relation and appends it to
        the judgment's evidence bundle.  Also updates the trust annotation if
        the relation implies a trust promotion.

        Parameters
        ----------
        judgment:
            The judgment to enrich.
        relation:
            The refinement relation to attach.

        Returns
        -------
        Judgment
            A new judgment with the relation recorded as evidence.

        Raises
        ------
        JuGeoError
            If the judgment's coordinate does not match the relation's left or
            right coordinate.
        """
        coord = self._coord(judgment)
        if coord not in (relation.left_coordinate, relation.right_coordinate):
            raise_with_scope(
                f"Judgment coordinate {coord!r} does not appear in relation "
                f"({relation.left_coordinate!r} → {relation.right_coordinate!r}).",
                scope=FailureScope.REFINEMENT,
                classification=FailureClassification.STRUCTURE,
            )

        evidence_item = self.build_refinement_evidence_item(
            RefinementWitness.make(
                source=relation.left_coordinate,
                target=relation.right_coordinate,
            )
        )

        # Append the evidence item to the judgment's bundle
        enriched = self._append_evidence(judgment, evidence_item)

        # Promote trust if the relation implies it
        if relation.trust_delta > 0 and coord == relation.left_coordinate:
            enriched = self._promote_trust(enriched, relation.trust_delta)

        return enriched

    # ------------------------------------------------------------------
    # attach_witness_evidence
    # ------------------------------------------------------------------

    def attach_witness_evidence(
        self,
        judgment: Judgment,
        witness: RefinementWitness,
    ) -> Judgment:
        """Attach a refinement witness as an evidence item to a judgment.

        The witness is serialised and stored as a ``DERIVED`` evidence item.
        The judgment's trust annotation is updated if the witness promotes
        trust.

        Parameters
        ----------
        judgment:
            The judgment to enrich.
        witness:
            The refinement witness to attach.

        Returns
        -------
        Judgment
            A new judgment with the witness recorded as evidence.
        """
        evidence_item = self.build_refinement_evidence_item(witness)
        enriched = self._append_evidence(judgment, evidence_item)

        if witness.trust_delta() > 0:
            enriched = self._promote_trust(enriched, witness.trust_delta())

        return enriched

    # ------------------------------------------------------------------
    # export_refinement_obligations
    # ------------------------------------------------------------------

    def export_refinement_obligations(
        self,
        order: RefinementOrder,
    ) -> tuple[ResidualObligation, ...]:
        """Export the residual obligations implied by a refinement order.

        For each FORWARD relation in the order that is **not** witnessed
        (``is_witnessed=False``), creates a ``ResidualObligation`` requiring
        that a formal witness be constructed.

        Parameters
        ----------
        order:
            The refinement order to export obligations from.

        Returns
        -------
        tuple[ResidualObligation, ...]
            One obligation per unwitnessed FORWARD relation.
        """
        obligations: list[ResidualObligation] = []
        D = RefinementRelation.RefinementDirection
        for rel in order.relations:
            if rel.direction not in (D.FORWARD, D.EQUIVALENT):
                continue
            if rel.is_witnessed:
                continue
            ob = self._make_obligation_for_relation(rel)
            if ob is not None:
                obligations.append(ob)
        return tuple(obligations)

    # ------------------------------------------------------------------
    # build_refinement_evidence_item
    # ------------------------------------------------------------------

    def build_refinement_evidence_item(
        self,
        witness: RefinementWitness,
    ) -> EvidenceItem:
        """Build an ``EvidenceItem`` from a refinement witness.

        The item records the witness metadata (source, target, trust_delta,
        validity) as a derived evidence item with kind ``DERIVED``.

        Parameters
        ----------
        witness:
            The witness to encode as an evidence item.

        Returns
        -------
        EvidenceItem
            A new evidence item encoding the witness.
        """
        content = (
            f"RefinementWitness({witness.witness_id[:8]}): "
            f"{witness.source_coordinate!r} → {witness.target_coordinate!r}, "
            f"trust_delta={witness.trust_delta()}, "
            f"valid={witness.is_valid}"
        )
        return EvidenceItem(
            item_id=str(uuid.uuid4()),
            kind=_REFINEMENT_EVIDENCE_KIND,
            content=content,
            provenance=witness.provenance,
            metadata=(
                ("witness_id", witness.witness_id),
                ("source", witness.source_coordinate),
                ("target", witness.target_coordinate),
                ("trust_delta", str(witness.trust_delta())),
                ("is_valid", str(witness.is_valid)),
            ),
        )

    # ------------------------------------------------------------------
    # validate_and_update_judgment
    # ------------------------------------------------------------------

    def validate_and_update_judgment(
        self,
        judgment: Judgment,
        order: RefinementOrder,
    ) -> Judgment:
        """Validate a judgment against a refinement order and update its status.

        Checks whether the judgment's coordinate appears in the order and
        whether all relations involving it are consistent.  If the judgment
        participates in FORWARD relations as a source, its status may be
        updated to reflect that refinements have been found.

        Parameters
        ----------
        judgment:
            The judgment to validate and update.
        order:
            The refinement order to check against.

        Returns
        -------
        Judgment
            An updated judgment (possibly unchanged if no relevant relations
            exist).
        """
        coord = self._coord(judgment)
        relevant = [
            rel for rel in order.relations if coord in (rel.left_coordinate, rel.right_coordinate)
        ]
        if not relevant:
            return judgment

        D = RefinementRelation.RefinementDirection

        # Check for regressions
        regressions = [
            rel for rel in relevant
            if rel.direction == D.BACKWARD and rel.left_coordinate == coord
        ]
        if regressions:
            # Attach regression warnings
            for reg in regressions:
                evidence_item = self._make_regression_evidence_item(reg)
                judgment = self._append_evidence(judgment, evidence_item)

        # Check for forward refinements (this judgment is being refined)
        forward_as_source = [
            rel for rel in relevant
            if rel.direction == D.FORWARD and rel.left_coordinate == coord
        ]
        for rel in forward_as_source:
            evidence_item = self._make_forward_evidence_item(rel)
            judgment = self._append_evidence(judgment, evidence_item)

        return judgment

    # ------------------------------------------------------------------
    # merge_refinement_obstructions
    # ------------------------------------------------------------------

    def merge_refinement_obstructions(
        self,
        judgment: Judgment,
        relations: Sequence[RefinementRelation],
    ) -> Judgment:
        """Merge obstruction records from a set of relations into a judgment.

        For each INCOMPARABLE or regressive relation involving the judgment,
        creates an ``Obstruction`` and attaches it to the judgment.

        Parameters
        ----------
        judgment:
            The judgment to update.
        relations:
            Relations to scan for obstructions.

        Returns
        -------
        Judgment
            A new judgment with obstruction records merged in.
        """
        coord = self._coord(judgment)
        D = RefinementRelation.RefinementDirection
        obstructions_to_add: list[Obstruction] = []

        for rel in relations:
            if coord not in (rel.left_coordinate, rel.right_coordinate):
                continue
            if rel.direction == D.INCOMPARABLE:
                ob = self._make_obstruction_for_incomparable(rel)
                if ob is not None:
                    obstructions_to_add.append(ob)
            elif rel.is_regression():
                ob = self._make_obstruction_for_regression(rel)
                if ob is not None:
                    obstructions_to_add.append(ob)

        if not obstructions_to_add:
            return judgment

        existing_obs = tuple(getattr(judgment, "obstructions", None) or ())
        return replace(
            judgment,
            obstructions=existing_obs + tuple(obstructions_to_add),
        )

    # ------------------------------------------------------------------
    # export_order_chain
    # ------------------------------------------------------------------

    def export_order_chain(
        self, order: RefinementOrder
    ) -> FailureChain:
        """Export a refinement order as a ``FailureChain``.

        Converts each FORWARD relation in the order to a ``StructuredFailure``
        (informational) and packages them as a ``FailureChain``.  Useful for
        audit trails and downstream reporting.

        Parameters
        ----------
        order:
            The refinement order to export.

        Returns
        -------
        FailureChain
            A chain of informational failure records, one per FORWARD relation.
        """
        D = RefinementRelation.RefinementDirection
        records: list[StructuredFailure] = []
        for rel in order.relations:
            if rel.direction not in (D.FORWARD, D.EQUIVALENT):
                continue
            message = (
                f"Refinement: {rel.left_coordinate!r} "
                f"{'≤' if rel.direction == D.FORWARD else '≡'} "
                f"{rel.right_coordinate!r} "
                f"(trust_delta={rel.trust_delta}, confidence={rel.confidence:.2f})"
            )
            payload = as_failure_payload(
                message=message,
                details=[
                    f"direction: {rel.direction.value}",
                    f"trust_delta: {rel.trust_delta}",
                    f"is_witnessed: {rel.is_witnessed}",
                    f"confidence: {rel.confidence}",
                ],
                scope=FailureScope.REFINEMENT,
                classification=FailureClassification.INFO,
            )
            records.append(
                StructuredFailure(
                    failure_id=rel.relation_id,
                    scope=FailureScope.REFINEMENT,
                    classification=FailureClassification.INFO,
                    message=message,
                    evidence_family=EvidenceFamily.REFINEMENT,
                    obstruction_records=(),
                    repair_hints=(),
                    payload=payload,
                )
            )

        return FailureChain(
            chain_id=str(uuid.uuid4()),
            records=tuple(records),
            summary=(
                f"RefinementOrder {order.order_id[:8]}: "
                f"{len(order.coordinates)} coordinates, "
                f"{len(order.relations)} relations"
            ),
        )

    # ------------------------------------------------------------------
    # generate_comparison_judgment
    # ------------------------------------------------------------------

    def generate_comparison_judgment(
        self,
        left: Judgment,
        right: Judgment,
    ) -> Judgment:
        """Generate a synthetic comparison judgment from two existing judgments.

        The comparison judgment has:
        * A coordinate of the form ``cmp({left_coord},{right_coord})``.
        * A proposition encoding the comparison claim.
        * Evidence items derived from both input judgments.
        * Trust level equal to the minimum of the two input trust levels.
        * A ``DERIVED`` provenance.

        Parameters
        ----------
        left:
            First judgment to compare.
        right:
            Second judgment to compare.

        Returns
        -------
        Judgment
            A new synthetic judgment representing the comparison.
        """
        left_coord = self._coord(left)
        right_coord = self._coord(right)
        cmp_coord = f"cmp({left_coord},{right_coord})"

        left_trust = self._trust_of(left)
        right_trust = self._trust_of(right)
        trust_order = list(TrustLevel)
        li = trust_order.index(left_trust) if left_trust in trust_order else 0
        ri = trust_order.index(right_trust) if right_trust in trust_order else 0
        min_trust = trust_order[min(li, ri)]

        # Build evidence: combine items from both judgments
        left_items = self._evidence_items(left)
        right_items = self._evidence_items(right)
        all_items = tuple(left_items) + tuple(right_items)

        # Build proposition content
        prop_content = (
            f"Comparison({left_coord!r}, {right_coord!r}): "
            f"structural relation between the two judgment coordinates."
        )

        # Construct the comparison judgment using JudgmentAlgebra
        try:
            provenance = Provenance(
                source=ProvenanceSource.DERIVED,
                description=f"Generated by RelationalRefinementIntegration.generate_comparison_judgment",
                timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            )
            comparison_judgment = JudgmentAlgebra.make_judgment(
                coordinate=cmp_coord,
                proposition=Proposition(term=prop_content),
                trust_level=min_trust,
                evidence=EvidenceBundle(items=all_items) if all_items else None,
                provenance=provenance,
            )
        except Exception:
            # Fallback: copy left with updated coordinate
            comparison_judgment = replace(
                left,
                coordinate=cmp_coord,  # type: ignore[call-arg]
            )

        return comparison_judgment

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coord(judgment: Judgment) -> str:
        """Extract coordinate string from *judgment*."""
        return getattr(judgment, "coordinate", None) or str(id(judgment))

    @staticmethod
    def _trust_of(judgment: Judgment) -> TrustLevel:
        """Extract trust level from *judgment*."""
        trust = getattr(judgment, "trust_level", None)
        if trust is None:
            annotation = getattr(judgment, "trust_annotation", None)
            if annotation:
                trust = getattr(annotation, "level", None)
        return trust if isinstance(trust, TrustLevel) else TrustLevel.UNVERIFIED

    @staticmethod
    def _evidence_items(judgment: Judgment) -> list[EvidenceItem]:
        """Extract evidence items from *judgment*."""
        bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if bundle is None:
            return []
        items = getattr(bundle, "items", None)
        return list(items) if items else []

    @staticmethod
    def _append_evidence(judgment: Judgment, item: EvidenceItem) -> Judgment:
        """Return a new judgment with *item* appended to its evidence bundle.

        Parameters
        ----------
        judgment:
            The judgment to update.
        item:
            The evidence item to append.

        Returns
        -------
        Judgment
            A new judgment with the item added.
        """
        bundle: EvidenceBundle | None = getattr(judgment, "evidence", None)
        if bundle is None:
            new_bundle = EvidenceBundle(items=(item,))
        else:
            existing = tuple(getattr(bundle, "items", ()) or ())
            new_bundle = replace(bundle, items=existing + (item,))
        return replace(judgment, evidence=new_bundle)

    @staticmethod
    def _promote_trust(judgment: Judgment, delta: int) -> Judgment:
        """Return a new judgment with trust promoted by *delta* levels.

        Parameters
        ----------
        judgment:
            The judgment to promote.
        delta:
            Number of trust levels to promote (positive integer).

        Returns
        -------
        Judgment
            A new judgment with updated trust annotation.
        """
        trust_order = list(TrustLevel)
        current_trust = getattr(judgment, "trust_level", TrustLevel.UNVERIFIED)
        if not isinstance(current_trust, TrustLevel):
            current_trust = TrustLevel.UNVERIFIED
        ci = trust_order.index(current_trust)
        new_idx = min(ci + delta, len(trust_order) - 1)
        new_trust = trust_order[new_idx]
        annotation = TrustAnnotation(
            level=new_trust,
            justification=f"Promoted by {delta} level(s) via refinement.",
        )
        return replace(judgment, trust_annotation=annotation)

    def _make_obligation_for_relation(
        self, rel: RefinementRelation
    ) -> ResidualObligation | None:
        """Create a ``ResidualObligation`` for an unwitnessed FORWARD relation.

        Parameters
        ----------
        rel:
            The unwitnessed relation.

        Returns
        -------
        ResidualObligation | None
            A new obligation, or ``None`` if construction fails.
        """
        try:
            return ResidualObligation(
                obligation_id=str(uuid.uuid4()),
                tag=f"{_OBLIGATION_TAG_PREFIX}_{rel.relation_id[:8]}",
                description=(
                    f"Construct a formal refinement witness for relation "
                    f"{rel.left_coordinate!r} → {rel.right_coordinate!r} "
                    f"(direction={rel.direction.value})."
                ),
                priority=RepairPriority.MEDIUM.value,
                related_coordinate=rel.left_coordinate,
            )
        except Exception:
            return None

    def _make_regression_evidence_item(
        self, rel: RefinementRelation
    ) -> EvidenceItem:
        """Create a regression-warning evidence item.

        Parameters
        ----------
        rel:
            A regressive relation.

        Returns
        -------
        EvidenceItem
            An evidence item flagging the regression.
        """
        content = (
            f"REGRESSION: {rel.left_coordinate!r} ← {rel.right_coordinate!r} "
            f"(trust_delta={rel.trust_delta})"
        )
        return EvidenceItem(
            item_id=str(uuid.uuid4()),
            kind=EvidenceItemKind.DERIVED,
            content=content,
            provenance=None,
            metadata=(
                ("type", "regression_warning"),
                ("relation_id", rel.relation_id),
                ("trust_delta", str(rel.trust_delta)),
            ),
        )

    def _make_forward_evidence_item(
        self, rel: RefinementRelation
    ) -> EvidenceItem:
        """Create a forward-refinement evidence item.

        Parameters
        ----------
        rel:
            A forward relation.

        Returns
        -------
        EvidenceItem
            An evidence item recording the forward refinement.
        """
        content = (
            f"REFINEMENT: {rel.left_coordinate!r} → {rel.right_coordinate!r} "
            f"(trust_delta={rel.trust_delta}, confidence={rel.confidence:.2f})"
        )
        return EvidenceItem(
            item_id=str(uuid.uuid4()),
            kind=EvidenceItemKind.DERIVED,
            content=content,
            provenance=None,
            metadata=(
                ("type", "forward_refinement"),
                ("relation_id", rel.relation_id),
                ("trust_delta", str(rel.trust_delta)),
            ),
        )

    def _make_obstruction_for_incomparable(
        self, rel: RefinementRelation
    ) -> Obstruction | None:
        """Create an obstruction for an INCOMPARABLE relation.

        Parameters
        ----------
        rel:
            An INCOMPARABLE relation.

        Returns
        -------
        Obstruction | None
            A new obstruction, or ``None`` if construction fails.
        """
        try:
            return Obstruction(
                obstruction_id=str(uuid.uuid4()),
                tag=f"incomparable_{rel.relation_id[:8]}",
                description=(
                    f"Coordinates {rel.left_coordinate!r} and "
                    f"{rel.right_coordinate!r} are INCOMPARABLE in the "
                    f"refinement order."
                ),
                failure_class=FailureClass.INCOMPARABILITY,
            )
        except Exception:
            return None

    def _make_obstruction_for_regression(
        self, rel: RefinementRelation
    ) -> Obstruction | None:
        """Create an obstruction for a regressive relation.

        Parameters
        ----------
        rel:
            A regressive relation.

        Returns
        -------
        Obstruction | None
            A new obstruction, or ``None`` if construction fails.
        """
        try:
            return Obstruction(
                obstruction_id=str(uuid.uuid4()),
                tag=f"regression_{rel.relation_id[:8]}",
                description=(
                    f"Regressive refinement: {rel.left_coordinate!r} ← "
                    f"{rel.right_coordinate!r} "
                    f"(trust_delta={rel.trust_delta})."
                ),
                failure_class=FailureClass.REGRESSION,
            )
        except Exception:
            return None


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
    "RelationalRefinementIntegration",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: integration.py — bridge between refinement structures and JudgmentAlgebra
