"""Stage 04 — Comparison algebra for the relational_refinement package.

Implements the ``ComparisonAlgebra`` class, which provides algebraic
operations on ``RefinementRelation`` objects.

Theory context (Ch12)
---------------------
The **comparison algebra** gives the refinement relations the structure of a
*partial groupoid with units*:

compose(r₁, r₂)
    Sequential composition (transitivity): if r₁: A → B and r₂: B → C then
    compose(r₁, r₂): A → C.  Requires r₁.right == r₂.left.

invert(r)
    Symmetric inversion: if r: A → B then invert(r): B → A.  Turns FORWARD
    into BACKWARD and vice versa.  For EQUIVALENT relations, the inverse is
    also EQUIVALENT.

tensor(r₁, r₂)
    Parallel composition (disjoint coordinates): if r₁: A → B and r₂: C → D
    with {A,B} ∩ {C,D} = ∅ then tensor(r₁, r₂) is a combined relation
    covering all four coordinates.  Used to reason about independent
    refinements simultaneously.

diagonal(coord)
    The identity (reflexivity) element at coordinate *coord*: a relation
    A → A of direction EQUIVALENT with trust_delta=0.

join_relations(rels)
    Compute the join (least upper bound) of a set of relations sharing the
    same pair of endpoint coordinates.  The join has the weakest direction
    that subsumes all of the inputs.

meet_relations(rels)
    Compute the meet (greatest lower bound) of a set of relations sharing
    the same pair of endpoint coordinates.  The meet has the strongest
    direction that is subsumed by all inputs.

compare_orders(o1, o2)
    Compare two ``RefinementOrder`` objects structurally, returning a
    ``ComparisonResult``.

detect_cycles(order)
    Detect cycles in the underlying directed graph of FORWARD edges.
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
# Direction shorthand
# ---------------------------------------------------------------------------
_D = RefinementRelation.RefinementDirection
_FORWARD = _D.FORWARD
_BACKWARD = _D.BACKWARD
_EQUIVALENT = _D.EQUIVALENT
_INCOMPARABLE = _D.INCOMPARABLE

# ---------------------------------------------------------------------------
# Direction composition table
# Used by ComparisonAlgebra.compose and tensor
# ---------------------------------------------------------------------------
_COMPOSE_TABLE: dict[
    tuple[
        RefinementRelation.RefinementDirection,
        RefinementRelation.RefinementDirection,
    ],
    RefinementRelation.RefinementDirection | None,
] = {
    (_FORWARD, _FORWARD): _FORWARD,
    (_FORWARD, _EQUIVALENT): _FORWARD,
    (_EQUIVALENT, _FORWARD): _FORWARD,
    (_BACKWARD, _BACKWARD): _BACKWARD,
    (_BACKWARD, _EQUIVALENT): _BACKWARD,
    (_EQUIVALENT, _BACKWARD): _BACKWARD,
    (_EQUIVALENT, _EQUIVALENT): _EQUIVALENT,
    (_FORWARD, _BACKWARD): None,
    (_BACKWARD, _FORWARD): None,
    (_FORWARD, _INCOMPARABLE): None,
    (_INCOMPARABLE, _FORWARD): None,
    (_BACKWARD, _INCOMPARABLE): None,
    (_INCOMPARABLE, _BACKWARD): None,
    (_INCOMPARABLE, _INCOMPARABLE): _INCOMPARABLE,
    (_EQUIVALENT, _INCOMPARABLE): None,
    (_INCOMPARABLE, _EQUIVALENT): None,
}

# ---------------------------------------------------------------------------
# ComparisonAlgebra
# ---------------------------------------------------------------------------


class ComparisonAlgebra:
    """Algebraic operations on ``RefinementRelation`` objects.

    ``ComparisonAlgebra`` provides compose, invert, tensor, diagonal, join,
    meet, order comparison, and cycle detection.  All operations are *pure*
    — they return new ``RefinementRelation`` or ``RefinementOrder`` objects
    and do not mutate their inputs.

    Usage
    -----
    ::

        algebra = ComparisonAlgebra()
        r12 = algebra.compose(r_AB, r_BC)   # A → C
        r_inv = algebra.invert(r_AB)         # B → A
        id_A = algebra.diagonal("A")         # identity at A
    """

    # ------------------------------------------------------------------
    # compose
    # ------------------------------------------------------------------

    def compose(
        self,
        r1: RefinementRelation,
        r2: RefinementRelation,
    ) -> RefinementRelation | None:
        """Compose two refinement relations sequentially.

        For ``r1: A → B`` and ``r2: B → C``, returns the composed relation
        ``r1 ; r2: A → C``.  Returns ``None`` if:
        * The coordinates don't align (``r1.right != r2.left``).
        * The direction combination is undefined (see the composition table).

        Parameters
        ----------
        r1:
            The first (left) relation.
        r2:
            The second (right) relation.

        Returns
        -------
        RefinementRelation | None
            The composed relation, or ``None`` if composition is undefined.
        """
        if r1.right_coordinate != r2.left_coordinate:
            return None
        composed_dir = _COMPOSE_TABLE.get((r1.direction, r2.direction))
        if composed_dir is None:
            return None

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=r1.left_coordinate,
            right_coordinate=r2.right_coordinate,
            direction=composed_dir,
            trust_delta=r1.trust_delta + r2.trust_delta,
            evidence_embedding=r1.evidence_embedding + r2.evidence_embedding,
            obligation_discharge=r1.obligation_discharge + r2.obligation_discharge,
            is_witnessed=r1.is_witnessed and r2.is_witnessed,
            witness_id=None,
            confidence=min(r1.confidence, r2.confidence),
            metadata=r1.metadata + r2.metadata,
        )

    # ------------------------------------------------------------------
    # invert
    # ------------------------------------------------------------------

    def invert(self, r: RefinementRelation) -> RefinementRelation:
        """Return the inverse of a refinement relation.

        Swaps ``left_coordinate`` and ``right_coordinate``, negates
        ``trust_delta``, flips the direction (FORWARD ↔ BACKWARD), and
        reverses the evidence/obligation pair encodings.

        Parameters
        ----------
        r:
            The relation to invert.

        Returns
        -------
        RefinementRelation
            The inverse relation.
        """
        return r.invert()

    # ------------------------------------------------------------------
    # tensor
    # ------------------------------------------------------------------

    def tensor(
        self,
        r1: RefinementRelation,
        r2: RefinementRelation,
    ) -> RefinementRelation:
        """Return the tensor (parallel composition) of two relations.

        The tensor of ``r1: A → B`` and ``r2: C → D`` (with disjoint
        coordinate pairs) represents both refinements simultaneously.  The
        resulting relation uses a synthetic combined coordinate.

        The direction of the tensor is the "weakest" direction that subsumes
        both:

        * FORWARD ⊗ FORWARD = FORWARD
        * EQUIVALENT ⊗ EQUIVALENT = EQUIVALENT
        * BACKWARD ⊗ BACKWARD = BACKWARD
        * Otherwise = INCOMPARABLE

        Parameters
        ----------
        r1:
            First relation.
        r2:
            Second relation.

        Returns
        -------
        RefinementRelation
            The tensor product relation.
        """
        # Direction of tensor
        if r1.direction == r2.direction:
            tensor_dir = r1.direction
        elif {r1.direction, r2.direction} <= {_FORWARD, _EQUIVALENT}:
            tensor_dir = _FORWARD
        elif {r1.direction, r2.direction} <= {_BACKWARD, _EQUIVALENT}:
            tensor_dir = _BACKWARD
        else:
            tensor_dir = _INCOMPARABLE

        # Synthetic coordinates
        left_combined = f"({r1.left_coordinate})⊗({r2.left_coordinate})"
        right_combined = f"({r1.right_coordinate})⊗({r2.right_coordinate})"

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=left_combined,
            right_coordinate=right_combined,
            direction=tensor_dir,
            trust_delta=r1.trust_delta + r2.trust_delta,
            evidence_embedding=r1.evidence_embedding + r2.evidence_embedding,
            obligation_discharge=r1.obligation_discharge + r2.obligation_discharge,
            is_witnessed=r1.is_witnessed and r2.is_witnessed,
            witness_id=None,
            confidence=min(r1.confidence, r2.confidence),
            metadata=r1.metadata + r2.metadata,
        )

    # ------------------------------------------------------------------
    # diagonal (identity)
    # ------------------------------------------------------------------

    def diagonal(self, coordinate: str) -> RefinementRelation:
        """Return the identity (diagonal / reflexivity) relation at *coordinate*.

        The diagonal relation id_A: A → A has direction EQUIVALENT,
        trust_delta=0, empty evidence embedding, and empty obligation
        discharge.  It is the unit for composition.

        Parameters
        ----------
        coordinate:
            The coordinate at which to place the identity.

        Returns
        -------
        RefinementRelation
            The identity relation.
        """
        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=coordinate,
            right_coordinate=coordinate,
            direction=_EQUIVALENT,
            trust_delta=0,
            evidence_embedding=(),
            obligation_discharge=(),
            is_witnessed=True,
            witness_id=None,
            confidence=1.0,
            metadata=(),
        )

    # ------------------------------------------------------------------
    # join_relations
    # ------------------------------------------------------------------

    def join_relations(
        self, relations: Sequence[RefinementRelation]
    ) -> RefinementRelation | None:
        """Compute the join (least upper bound) of a sequence of relations.

        The join is defined only when all relations share the same left and
        right coordinates.  The direction of the join is the *weakest*
        direction that is consistent with all inputs (following the direction
        lattice: FORWARD ≥ EQUIVALENT ≥ BACKWARD; INCOMPARABLE is the bottom).

        The trust delta of the join is the *minimum* of all input trust deltas
        (weakest guarantee).

        Parameters
        ----------
        relations:
            A sequence of relations to join.

        Returns
        -------
        RefinementRelation | None
            The join relation, or ``None`` if the sequence is empty or the
            relations do not share coordinates.
        """
        if not relations:
            return None

        left_set = {r.left_coordinate for r in relations}
        right_set = {r.right_coordinate for r in relations}
        if len(left_set) != 1 or len(right_set) != 1:
            return None

        left = next(iter(left_set))
        right = next(iter(right_set))

        # Direction join: weakest consistent direction
        # Lattice: FORWARD > EQUIVALENT > BACKWARD > INCOMPARABLE
        direction_strength = {
            _FORWARD: 3,
            _EQUIVALENT: 2,
            _BACKWARD: 1,
            _INCOMPARABLE: 0,
        }
        min_dir = min(
            (r.direction for r in relations),
            key=lambda d: direction_strength[d],
        )
        min_delta = min(r.trust_delta for r in relations)
        min_confidence = min(r.confidence for r in relations)

        all_evidence = tuple(
            pair for r in relations for pair in r.evidence_embedding
        )
        all_obligations = tuple(
            pair for r in relations for pair in r.obligation_discharge
        )

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=left,
            right_coordinate=right,
            direction=min_dir,
            trust_delta=min_delta,
            evidence_embedding=all_evidence,
            obligation_discharge=all_obligations,
            is_witnessed=all(r.is_witnessed for r in relations),
            witness_id=None,
            confidence=min_confidence,
            metadata=(),
        )

    # ------------------------------------------------------------------
    # meet_relations
    # ------------------------------------------------------------------

    def meet_relations(
        self, relations: Sequence[RefinementRelation]
    ) -> RefinementRelation | None:
        """Compute the meet (greatest lower bound) of a sequence of relations.

        The meet is defined only when all relations share the same left and
        right coordinates.  The direction of the meet is the *strongest*
        direction consistent with all inputs.  The trust delta is the
        *maximum* of all input trust deltas (strongest guarantee).

        Parameters
        ----------
        relations:
            A sequence of relations to meet.

        Returns
        -------
        RefinementRelation | None
            The meet relation, or ``None`` if the sequence is empty or the
            relations do not share coordinates.
        """
        if not relations:
            return None

        left_set = {r.left_coordinate for r in relations}
        right_set = {r.right_coordinate for r in relations}
        if len(left_set) != 1 or len(right_set) != 1:
            return None

        left = next(iter(left_set))
        right = next(iter(right_set))

        direction_strength = {
            _FORWARD: 3,
            _EQUIVALENT: 2,
            _BACKWARD: 1,
            _INCOMPARABLE: 0,
        }
        max_dir = max(
            (r.direction for r in relations),
            key=lambda d: direction_strength[d],
        )
        max_delta = max(r.trust_delta for r in relations)
        max_confidence = max(r.confidence for r in relations)

        all_evidence = tuple(
            pair for r in relations for pair in r.evidence_embedding
        )
        all_obligations = tuple(
            pair for r in relations for pair in r.obligation_discharge
        )

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=left,
            right_coordinate=right,
            direction=max_dir,
            trust_delta=max_delta,
            evidence_embedding=all_evidence,
            obligation_discharge=all_obligations,
            is_witnessed=any(r.is_witnessed for r in relations),
            witness_id=None,
            confidence=max_confidence,
            metadata=(),
        )

    # ------------------------------------------------------------------
    # compare_orders
    # ------------------------------------------------------------------

    def compare_orders(
        self,
        o1: RefinementOrder,
        o2: RefinementOrder,
    ) -> ComparisonResult:
        """Compare two ``RefinementOrder`` objects structurally.

        Delegates to ``compare_sections`` using ``ComparisonMode.REFINEMENT``.
        Returns a ``ComparisonResult`` that describes whether the two orders
        are comparable, and in which direction.

        Parameters
        ----------
        o1:
            First refinement order.
        o2:
            Second refinement order.

        Returns
        -------
        ComparisonResult
            The structured comparison result.

        Raises
        ------
        JuGeoError
            If the orders cannot be compared.
        """
        try:
            return compare_sections(o1, o2, mode=ComparisonMode.REFINEMENT)
        except Exception as exc:
            raise_with_scope(
                f"Order comparison failed: {exc}",
                scope=FailureScope.REFINEMENT,
                classification=FailureClassification.STRUCTURE,
            )

    # ------------------------------------------------------------------
    # detect_cycles
    # ------------------------------------------------------------------

    def detect_cycles(
        self, order: RefinementOrder
    ) -> tuple[tuple[str, ...], ...]:
        """Detect cycles in the underlying directed graph of FORWARD relations.

        Uses iterative DFS with a three-colour marking scheme:
        * White (0): unvisited
        * Grey (1): in current DFS path
        * Black (2): fully processed

        Parameters
        ----------
        order:
            The refinement order whose FORWARD edges to analyse.

        Returns
        -------
        tuple[tuple[str, ...], ...]
            A tuple of cycles, each cycle being a tuple of coordinate strings.
            Empty if the order is acyclic.
        """
        # Build forward adjacency (FORWARD edges only)
        adj: dict[str, list[str]] = {c: [] for c in order.coordinates}
        for rel in order.relations:
            if rel.direction == _FORWARD:
                adj.setdefault(rel.left_coordinate, []).append(rel.right_coordinate)

        colour: dict[str, int] = {c: 0 for c in order.coordinates}
        parent: dict[str, str | None] = {c: None for c in order.coordinates}
        cycles: list[tuple[str, ...]] = []

        def dfs(node: str, path: list[str]) -> None:
            colour[node] = 1
            path.append(node)
            for neighbour in adj.get(node, []):
                if colour[neighbour] == 1:
                    # Found a cycle — extract it
                    cycle_start = path.index(neighbour)
                    cycle = tuple(path[cycle_start:])
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif colour[neighbour] == 0:
                    dfs(neighbour, path)
            path.pop()
            colour[node] = 2

        for coord in order.coordinates:
            if colour[coord] == 0:
                dfs(coord, [])

        return tuple(cycles)

    # ------------------------------------------------------------------
    # Additional algebraic utilities
    # ------------------------------------------------------------------

    def is_idempotent(self, r: RefinementRelation) -> bool:
        """Return whether composing *r* with itself produces itself.

        A relation r is idempotent iff compose(r, r) is defined and has the
        same direction, coordinates, and trust delta as r.  This holds for
        EQUIVALENT relations with trust_delta = 0.

        Parameters
        ----------
        r:
            The relation to test.

        Returns
        -------
        bool
            ``True`` iff r ∘ r ≅ r.
        """
        composed = self.compose(r, r)
        if composed is None:
            return False
        return (
            composed.direction == r.direction
            and composed.trust_delta == r.trust_delta
            and composed.left_coordinate == r.left_coordinate
            and composed.right_coordinate == r.right_coordinate
        )

    def is_unit(self, r: RefinementRelation) -> bool:
        """Return whether *r* is the identity (unit) element.

        A relation is the unit iff it is an EQUIVALENT self-loop with
        trust_delta = 0.

        Parameters
        ----------
        r:
            The relation to test.

        Returns
        -------
        bool
            ``True`` iff r is the identity.
        """
        return (
            r.direction == _EQUIVALENT
            and r.left_coordinate == r.right_coordinate
            and r.trust_delta == 0
        )

    def closure_under_composition(
        self,
        relations: Sequence[RefinementRelation],
    ) -> tuple[RefinementRelation, ...]:
        """Close a set of relations under composition (transitivity closure).

        Iteratively applies ``compose`` to all pairs until no new relations
        are produced.  Deduplicates by ``(left_coordinate, right_coordinate,
        direction)`` triples.

        Parameters
        ----------
        relations:
            Initial set of relations.

        Returns
        -------
        tuple[RefinementRelation, ...]
            The transitivity-closed set.
        """
        current: list[RefinementRelation] = list(relations)
        seen: set[tuple[str, str, str]] = {
            (r.left_coordinate, r.right_coordinate, r.direction.value)
            for r in current
        }

        changed = True
        while changed:
            changed = False
            new_rels: list[RefinementRelation] = []
            for r1 in current:
                for r2 in current:
                    composed = self.compose(r1, r2)
                    if composed is None:
                        continue
                    key = (
                        composed.left_coordinate,
                        composed.right_coordinate,
                        composed.direction.value,
                    )
                    if key not in seen:
                        seen.add(key)
                        new_rels.append(composed)
                        changed = True
            current.extend(new_rels)

        return tuple(current)

    def normalise_direction(
        self, direction: RefinementRelation.RefinementDirection
    ) -> RefinementRelation.RefinementDirection:
        """Normalise a direction to its canonical form.

        * FORWARD and BACKWARD are returned unchanged.
        * EQUIVALENT is returned unchanged.
        * INCOMPARABLE is returned unchanged.

        This method exists as a hook for subclasses to override direction
        normalisation logic.

        Parameters
        ----------
        direction:
            The direction to normalise.

        Returns
        -------
        RefinementRelation.RefinementDirection
            The normalised direction.
        """
        return direction

    def emit_cycle_failure(
        self, cycle: tuple[str, ...]
    ) -> StructuredFailure:
        """Emit a ``StructuredFailure`` for a detected cycle.

        Parameters
        ----------
        cycle:
            A tuple of coordinate strings forming a cycle.

        Returns
        -------
        StructuredFailure
            A structured failure record describing the cycle.
        """
        message = (
            f"Cycle detected in refinement order: "
            f"{' → '.join(cycle)} → {cycle[0]}"
        )
        payload = as_failure_payload(
            message=message,
            details=[f"Cycle length: {len(cycle)}", f"Coordinates: {list(cycle)}"],
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
                ObstructionRecord(
                    tag="cycle_detected",
                    description=message,
                ),
            ),
            repair_hints=(
                RepairHint(
                    priority=RepairPriority.CRITICAL,
                    description=(
                        "Break the cycle by reclassifying one of the FORWARD "
                        "relations as EQUIVALENT or INCOMPARABLE."
                    ),
                ),
            ),
            payload=payload,
        )


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
    "ComparisonAlgebra",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: comparison_algebra — ComparisonAlgebra for Ch12 refinement algebraic ops
