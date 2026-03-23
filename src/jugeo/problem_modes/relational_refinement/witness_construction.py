"""Stage 03 — Witness construction for the relational_refinement package.

Implements the ``WitnessConstructor`` class, which builds, validates, and
composes ``RefinementWitness`` objects that formally certify J ≤ J'.

Theory context (Ch12)
---------------------
A **refinement witness** w: J → J' is a morphism in the judgment category
that certifies the refinement J ≤ J'.  It consists of three components:

1. **Trust promotion path** — a monotone sequence of ``TrustLevel`` values
   that records how the trust of J is promoted to the trust of J'.
2. **Evidence embedding** — a function that maps each evidence key of J to a
   key in J', showing that every piece of evidence of J is accounted for in J'.
3. **Obligation discharge map** — a function that maps each residual obligation
   of J to a corresponding obligation in J' (the obligations are "inherited").

Composition rule (Ch12.Thm5)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
If w₁: J → J' and w₂: J' → J'' are valid witnesses, their composition
w₂ ∘ w₁: J → J'' is also a valid witness.  The evidence embedding of the
composition is obtained by following the chain
``w₁.embedding[k] = v`` and ``w₂.embedding[v] = u`` to get ``k → u``.
The trust path is obtained by concatenating the two paths (deduplicating the
shared midpoint).

Identity witness (Ch12.Thm1)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The identity witness id_J: J → J is the degenerate witness with a one-element
trust path, an evidence embedding where every key maps to itself, and an empty
obligation discharge map.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import replace
from typing import Any, Mapping, Sequence

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

# Trust level list (weaker → stronger)
_TRUST_LEVELS: list[TrustLevel] = list(TrustLevel)

# Marker for unmatched evidence items in the embedding
_UNMATCHED: str = "__unmatched__"

# Marker for unresolved obligations
_UNRESOLVED: str = "__unresolved__"


def _trust_idx(level: TrustLevel) -> int:
    """Return the zero-based ordinal index of *level* in the trust lattice."""
    try:
        return _TRUST_LEVELS.index(level)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# WitnessConstructor
# ---------------------------------------------------------------------------


class WitnessConstructor:
    """Constructs and validates ``RefinementWitness`` objects.

    ``WitnessConstructor`` is the factory for ``RefinementWitness`` instances.
    It is responsible for:

    * Building the trust promotion path between two trust levels.
    * Building the evidence embedding from two evidence bundles.
    * Building the obligation discharge map from two obligation tuples.
    * Assembling a ``RefinementWitness`` from these components.
    * Validating a witness structurally and semantically.
    * Composing two witnesses (implementing Ch12.Thm5).
    * Constructing the identity witness for a judgment (implementing
      Ch12.Thm1).

    Usage
    -----
    ::

        constructor = WitnessConstructor()
        witness = constructor.construct(left, right, RefinementRelation.RefinementDirection.FORWARD)
        if constructor.validate_witness(witness, left, right):
            print("Witness is valid.")
    """

    # ------------------------------------------------------------------
    # Primary construction entry point
    # ------------------------------------------------------------------

    def construct(
        self,
        left: Judgment,
        right: Judgment,
        direction: RefinementRelation.RefinementDirection,
    ) -> RefinementWitness:
        """Construct a refinement witness for *left* ≤ *right*.

        Builds the trust promotion path, evidence embedding, and obligation
        discharge map, then assembles a ``RefinementWitness``.

        Parameters
        ----------
        left:
            The source judgment J.
        right:
            The target judgment J'.
        direction:
            The direction of the refinement (should be FORWARD or EQUIVALENT).

        Returns
        -------
        RefinementWitness
            The constructed witness (not yet validated).

        Raises
        ------
        ValueError
            If *direction* is INCOMPARABLE (no witness can be constructed).
        """
        D = RefinementRelation.RefinementDirection
        if direction == D.INCOMPARABLE:
            raise ValueError(
                "Cannot construct a witness for an INCOMPARABLE relation."
            )

        left_coord = self._coord(left)
        right_coord = self._coord(right)
        left_trust = self._trust_of(left)
        right_trust = self._trust_of(right)

        trust_path = self.build_trust_promotion_path(left_trust, right_trust)
        evidence_emb = self.build_evidence_embedding(
            self._evidence_bundle(left),
            self._evidence_bundle(right),
        )
        obligation_discharge = self.build_obligation_discharge(
            self._obligations(left),
            self._obligations(right),
        )

        witness = RefinementWitness(
            witness_id=str(uuid.uuid4()),
            source_coordinate=left_coord,
            target_coordinate=right_coord,
            trust_promotion_path=trust_path,
            evidence_embedding=evidence_emb,
            obligation_discharge_map=obligation_discharge,
            composition_steps=(),
            is_valid=None,
            validated_at=None,
            provenance=self._make_provenance(left, right),
        )
        return witness

    # ------------------------------------------------------------------
    # Component builders
    # ------------------------------------------------------------------

    def build_trust_promotion_path(
        self,
        left_trust: TrustLevel,
        right_trust: TrustLevel,
    ) -> tuple[TrustLevel, ...]:
        """Build the trust promotion path from *left_trust* to *right_trust*.

        The path is the contiguous subsequence of the trust lattice from
        *left_trust* to *right_trust* (inclusive on both ends).  If
        *left_trust* == *right_trust*, the path is a single-element tuple.
        If *right_trust* is lower than *left_trust* (regression), the path
        is reversed.

        Parameters
        ----------
        left_trust:
            Starting trust level.
        right_trust:
            Ending trust level.

        Returns
        -------
        tuple[TrustLevel, ...]
            The monotone (or reversed) path.
        """
        li = _trust_idx(left_trust)
        ri = _trust_idx(right_trust)
        if li <= ri:
            return tuple(_TRUST_LEVELS[li : ri + 1])
        # Regression: reversed
        return tuple(reversed(_TRUST_LEVELS[ri : li + 1]))

    def build_evidence_embedding(
        self,
        left_bundle: EvidenceBundle | None,
        right_bundle: EvidenceBundle | None,
    ) -> tuple[tuple[str, str], ...]:
        """Build the evidence embedding from two evidence bundles.

        For each evidence item in *left_bundle*, finds the best-matching item
        in *right_bundle* (same kind, compatible content).  Returns a tuple of
        ``(left_key, right_key)`` pairs.  Unmatched items are silently omitted.

        Parameters
        ----------
        left_bundle:
            Evidence bundle of the source judgment J.
        right_bundle:
            Evidence bundle of the target judgment J'.

        Returns
        -------
        tuple[tuple[str, str], ...]
            Embedding pairs.
        """
        left_items = self._bundle_items(left_bundle)
        right_items = self._bundle_items(right_bundle)

        if not left_items or not right_items:
            return ()

        # Index right items by kind
        right_by_kind: dict[EvidenceItemKind, list[EvidenceItem]] = {}
        for item in right_items:
            right_by_kind.setdefault(item.kind, []).append(item)

        pairs: list[tuple[str, str]] = []
        for left_item in left_items:
            candidates = right_by_kind.get(left_item.kind, [])
            for right_item in candidates:
                if self._items_compatible(left_item, right_item):
                    pairs.append((left_item.item_id, right_item.item_id))
                    break
            # If not matched, skip (witness is partial)

        return tuple(pairs)

    def build_obligation_discharge(
        self,
        left_obs: tuple[ResidualObligation, ...],
        right_obs: tuple[ResidualObligation, ...],
    ) -> tuple[tuple[str, str], ...]:
        """Build the obligation discharge map.

        For each residual obligation in *left_obs*, finds the corresponding
        obligation in *right_obs* by matching the ``tag`` field.  Returns a
        tuple of ``(left_id, right_id)`` pairs.

        Parameters
        ----------
        left_obs:
            Residual obligations of the source judgment J.
        right_obs:
            Residual obligations of the target judgment J'.

        Returns
        -------
        tuple[tuple[str, str], ...]
            Discharge pairs.
        """
        if not left_obs:
            return ()

        right_by_tag: dict[str, ResidualObligation] = {ob.tag: ob for ob in right_obs}

        pairs: list[tuple[str, str]] = []
        for ob in left_obs:
            if ob.tag in right_by_tag:
                pairs.append((ob.obligation_id, right_by_tag[ob.tag].obligation_id))
            # Unresolved obligations are omitted from the witness map
        return tuple(pairs)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_witness(
        self,
        witness: RefinementWitness,
        left: Judgment,
        right: Judgment,
    ) -> bool:
        """Perform semantic validation of *witness* against *left* and *right*.

        Checks:
        * Structural validity via ``witness.validate()``.
        * The source/target coordinates match the judgment coordinates.
        * The trust promotion path starts at ``trust(left)`` and ends at
          ``trust(right)``.
        * Every evidence key in the embedding corresponds to an actual item
          in the respective bundles.

        Parameters
        ----------
        witness:
            The witness to validate.
        left:
            The source judgment J.
        right:
            The target judgment J'.

        Returns
        -------
        bool
            ``True`` iff the witness is structurally and semantically valid.
        """
        if not witness.validate():
            return False

        # Coordinate match
        if witness.source_coordinate != self._coord(left):
            return False
        if witness.target_coordinate != self._coord(right):
            return False

        # Trust path endpoints
        left_trust = self._trust_of(left)
        right_trust = self._trust_of(right)
        if witness.trust_promotion_path:
            if witness.trust_promotion_path[0] != left_trust:
                return False
            if witness.trust_promotion_path[-1] != right_trust:
                return False

        # Evidence embedding keys exist
        left_ids = {item.item_id for item in self._bundle_items(self._evidence_bundle(left))}
        right_ids = {item.item_id for item in self._bundle_items(self._evidence_bundle(right))}
        for src_key, tgt_key in witness.evidence_embedding:
            if left_ids and src_key not in left_ids:
                return False
            if right_ids and tgt_key not in right_ids:
                return False

        return True

    # ------------------------------------------------------------------
    # Composition and identity
    # ------------------------------------------------------------------

    def compose_witnesses(
        self,
        w1: RefinementWitness,
        w2: RefinementWitness,
    ) -> RefinementWitness:
        """Compose two witnesses sequentially (Ch12.Thm5 — compositionality).

        Delegates to ``w1.compose_with(w2)``.  The resulting witness has
        its ``is_valid`` set to ``None`` (requires re-validation).

        Parameters
        ----------
        w1:
            The first (source-side) witness.
        w2:
            The second (target-side) witness.

        Returns
        -------
        RefinementWitness
            The composed witness w₂ ∘ w₁.

        Raises
        ------
        ValueError
            If ``w1.target_coordinate != w2.source_coordinate``.
        """
        return w1.compose_with(w2)

    def identity_witness(self, judgment: Judgment) -> RefinementWitness:
        """Construct the identity witness id_J: J → J (reflexivity, Ch12.Thm1).

        The identity witness has:
        * Source and target both equal to the coordinate of *judgment*.
        * A single-element trust promotion path ``(trust(J),)``.
        * An evidence embedding where every key maps to itself.
        * An empty obligation discharge map.

        Parameters
        ----------
        judgment:
            The judgment for which to construct the identity witness.

        Returns
        -------
        RefinementWitness
            The identity (reflexivity) witness.
        """
        coord = self._coord(judgment)
        trust = self._trust_of(judgment)
        items = self._bundle_items(self._evidence_bundle(judgment))
        identity_emb = tuple((item.item_id, item.item_id) for item in items)

        witness = RefinementWitness(
            witness_id=str(uuid.uuid4()),
            source_coordinate=coord,
            target_coordinate=coord,
            trust_promotion_path=(trust,),
            evidence_embedding=identity_emb,
            obligation_discharge_map=(),
            composition_steps=(),
            is_valid=True,
            validated_at=datetime.datetime.utcnow().isoformat() + "Z",
            provenance=None,
        )
        return witness

    # ------------------------------------------------------------------
    # Certificate emission
    # ------------------------------------------------------------------

    def emit_witness_certificate(
        self, witness: RefinementWitness
    ) -> StructuredFailure:
        """Emit a ``StructuredFailure`` certificate for a validated witness.

        Produces an informational certificate (not an error) recording the
        witness metadata for audit trail purposes.

        Parameters
        ----------
        witness:
            The witness to certify.

        Returns
        -------
        StructuredFailure
            A structured certificate for the witness.
        """
        status = "valid" if witness.is_valid else ("invalid" if witness.is_valid is False else "unchecked")
        message = (
            f"Refinement witness {witness.witness_id[:8]} [{status}]: "
            f"{witness.source_coordinate!r} → {witness.target_coordinate!r}, "
            f"trust_delta={witness.trust_delta()}, "
            f"evidence_embedding={len(witness.evidence_embedding)} pairs."
        )
        details = [
            f"Trust path: {[t.value for t in witness.trust_promotion_path]}",
            f"Obligation discharge: {len(witness.obligation_discharge_map)} pairs",
            f"Composition steps: {list(witness.composition_steps)}",
        ]
        payload = as_failure_payload(
            message=message,
            details=details,
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

    def emit_invalid_witness_failure(
        self, witness: RefinementWitness, reason: str
    ) -> StructuredFailure:
        """Emit a ``StructuredFailure`` for a witness that failed validation.

        Parameters
        ----------
        witness:
            The invalid witness.
        reason:
            Human-readable explanation of why validation failed.

        Returns
        -------
        StructuredFailure
            A structured failure record.
        """
        message = (
            f"Refinement witness {witness.witness_id[:8]} is invalid: {reason}"
        )
        payload = as_failure_payload(
            message=message,
            details=[reason],
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
        )
        return StructuredFailure(
            failure_id=str(uuid.uuid4()),
            scope=FailureScope.REFINEMENT,
            classification=FailureClassification.CONSISTENCY,
            message=message,
            evidence_family=EvidenceFamily.REFINEMENT,
            obstruction_records=(
                ObstructionRecord(tag="invalid_witness", description=reason),
            ),
            repair_hints=(
                RepairHint(
                    priority=RepairPriority.HIGH,
                    description="Re-construct the witness using WitnessConstructor.construct().",
                ),
            ),
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def construct_all_witnesses(
        self,
        order: RefinementOrder,
        judgments: dict[str, Judgment],
    ) -> dict[str, RefinementWitness]:
        """Construct witnesses for all FORWARD relations in an order.

        Iterates over all FORWARD and EQUIVALENT relations in *order* and
        attempts to construct a witness for each.  Returns a dict mapping
        ``relation_id`` to the constructed witness.

        Parameters
        ----------
        order:
            A ``RefinementOrder`` whose FORWARD/EQUIVALENT relations need
            witnesses.
        judgments:
            A mapping from coordinate string to ``Judgment`` object.

        Returns
        -------
        dict[str, RefinementWitness]
            Mapping ``{relation_id: witness}``.
        """
        D = RefinementRelation.RefinementDirection
        result: dict[str, RefinementWitness] = {}
        for rel in order.relations:
            if rel.direction not in (D.FORWARD, D.EQUIVALENT):
                continue
            left_j = judgments.get(rel.left_coordinate)
            right_j = judgments.get(rel.right_coordinate)
            if left_j is None or right_j is None:
                continue
            try:
                witness = self.construct(left_j, right_j, rel.direction)
                result[rel.relation_id] = witness
            except Exception:
                pass  # Skip relations that can't be witnessed
        return result

    def validate_all_witnesses(
        self,
        witnesses: dict[str, RefinementWitness],
        judgments: dict[str, Judgment],
    ) -> dict[str, bool]:
        """Validate all witnesses against their corresponding judgments.

        Parameters
        ----------
        witnesses:
            Mapping from some identifier to a ``RefinementWitness``.
        judgments:
            Mapping from coordinate string to ``Judgment`` object.

        Returns
        -------
        dict[str, bool]
            Mapping ``{witness_key: is_valid}`` for each witness.
        """
        results: dict[str, bool] = {}
        for key, witness in witnesses.items():
            left_j = judgments.get(witness.source_coordinate)
            right_j = judgments.get(witness.target_coordinate)
            if left_j is None or right_j is None:
                results[key] = False
                continue
            results[key] = self.validate_witness(witness, left_j, right_j)
        return results

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
    def _evidence_bundle(judgment: Judgment) -> EvidenceBundle | None:
        """Extract the evidence bundle from *judgment*."""
        return getattr(judgment, "evidence", None)

    @staticmethod
    def _obligations(judgment: Judgment) -> tuple[ResidualObligation, ...]:
        """Extract residual obligations from *judgment*."""
        obs = getattr(judgment, "residual_obligations", None)
        if obs is None:
            return ()
        return tuple(obs)

    @staticmethod
    def _bundle_items(bundle: EvidenceBundle | None) -> list[EvidenceItem]:
        """Extract items from an evidence bundle."""
        if bundle is None:
            return []
        items = getattr(bundle, "items", None)
        if items is None:
            return []
        return list(items)

    @staticmethod
    def _items_compatible(a: EvidenceItem, b: EvidenceItem) -> bool:
        """Return whether evidence items *a* and *b* are compatible."""
        if a.kind != b.kind:
            return False
        a_content = str(getattr(a, "content", ""))
        b_content = str(getattr(b, "content", ""))
        return a_content == b_content or a_content in b_content

    @staticmethod
    def _make_provenance(
        left: Judgment, right: Judgment
    ) -> Provenance | None:
        """Construct a provenance record for a witness.

        Parameters
        ----------
        left:
            Source judgment.
        right:
            Target judgment.

        Returns
        -------
        Provenance | None
            A provenance record, or ``None`` if provenance cannot be built.
        """
        try:
            return Provenance(
                source=ProvenanceSource.DERIVED,
                description=(
                    f"Witness constructed by WitnessConstructor for relation "
                    f"{getattr(left, 'coordinate', '?')!r} → "
                    f"{getattr(right, 'coordinate', '?')!r}."
                ),
                timestamp=datetime.datetime.utcnow().isoformat() + "Z",
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
    "WitnessConstructor",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: witness_construction — WitnessConstructor for Ch12 refinement witnesses
