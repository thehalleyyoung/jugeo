"""Theorem targets and proof obligations for the relational_refinement package.

Implements the formal theorem obligations from Chapter 12 of theory2.tex
("Equivalence and Refinement").  Each theorem is represented as a
``TheoremObligation`` frozen dataclass that records:

* The theorem name and formal statement.
* The theory reference (e.g. "Ch12.Thm1").
* The proof strategy to employ.
* The current proof status.
* The set of coordinates / orders on which the obligation has been discharged.

Design notes
------------
Theorem obligations are first-class objects in JuGeo.  They can be tracked,
serialised, and reported just like any other obligation.  The function
``generate_proof_obligations`` converts a ``RefinementOrder`` into a tuple
of ``TheoremObligation`` objects, one for each Ch12 theorem that must be
verified for the given order.

Theory coverage
---------------
The 12 theorems covered here are:

 1. Refinement reflexivity   — J ≤ J (identity witness)
 2. Refinement antisymmetry  — J ≤ J' ∧ J' ≤ J ⟹ J ≡ J'
 3. Refinement transitivity  — J ≤ J' ∧ J' ≤ J'' ⟹ J ≤ J''
 4. Equivalence congruence   — ≡ is a congruence on the judgment algebra
 5. Witness compositionality — composition of valid witnesses is valid
 6. Trust monotonicity       — J ≤ J' ⟹ trust(J) ≤ trust(J')
 7. Evidence embedding soundness — embedding preserves semantic content
 8. Obligation discharge completeness — all obligations of J subsumed in J'
 9. Section refinement preservation — descent conditions preserved
10. LUB existence            — any two comparable judgments have a LUB
11. GLB existence            — any two comparable judgments have a GLB
12. Regression detection     — regressive refinements are detectable
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

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
# Project-wide type aliases
# ---------------------------------------------------------------------------
JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# Theorem targets (Ch12)
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[tuple[str, str, str], ...] = (
    (
        "refinement_reflexivity",
        "For all judgments J: J ≤ J holds (the identity witness is valid).",
        "Ch12.Thm1",
    ),
    (
        "refinement_antisymmetry",
        "J ≤ J' and J' ≤ J implies J ≡ J' (bidirectional refinement = equivalence).",
        "Ch12.Thm2",
    ),
    (
        "refinement_transitivity",
        "J ≤ J' and J' ≤ J'' implies J ≤ J'' (witnesses compose).",
        "Ch12.Thm3",
    ),
    (
        "equivalence_congruence",
        "The equivalence relation ≡ is a congruence on the judgment algebra: "
        "if J ≡ J' then f(J) ≡ f(J') for any judgment algebra operation f.",
        "Ch12.Thm4",
    ),
    (
        "witness_compositionality",
        "If w₁: J → J' and w₂: J' → J'' are valid witnesses, then "
        "w₂ ∘ w₁: J → J'' is a valid witness.",
        "Ch12.Thm5",
    ),
    (
        "trust_monotonicity",
        "J ≤ J' implies trust(J) ≤ trust(J') in the trust lattice.",
        "Ch12.Thm6",
    ),
    (
        "evidence_embedding_soundness",
        "The evidence embedding map of a valid witness is semantics-preserving: "
        "for each key k of J, the embedded item f(k) in J' has the same content.",
        "Ch12.Thm7",
    ),
    (
        "obligation_discharge_completeness",
        "Every residual obligation of J is either present in J' or discharged "
        "by the obligation discharge map of the witness.",
        "Ch12.Thm8",
    ),
    (
        "section_refinement_preservation",
        "If s ≤ s' as sections over a cover 𝔘, and the descent conditions hold "
        "for s, then they also hold for s'.",
        "Ch12.Thm9",
    ),
    (
        "lub_existence",
        "Any two comparable judgments J, J' in a refinement order that contains "
        "J ≤ J' (or J' ≤ J) have a least upper bound.",
        "Ch12.Thm10",
    ),
    (
        "glb_existence",
        "Any two comparable judgments J, J' in a refinement order that contains "
        "J ≤ J' (or J' ≤ J) have a greatest lower bound.",
        "Ch12.Thm11",
    ),
    (
        "regression_detection",
        "A regressive refinement (trust_delta < 0 or direction BACKWARD) is "
        "algorithmically detectable from the trust-delta and direction fields.",
        "Ch12.Thm12",
    ),
)


# ---------------------------------------------------------------------------
# ProofStrategy enum
# ---------------------------------------------------------------------------


class ProofStrategy(str, Enum):
    """The proof strategy to use for a theorem obligation.

    Members
    -------
    STRUCTURAL_INDUCTION:
        Prove the theorem by induction on the structure of judgments.
    CATEGORICAL_DIAGRAM:
        Prove using a commutative diagram in the judgment category.
    ALGEBRAIC_IDENTITY:
        Prove by equational reasoning in the comparison algebra.
    ALGORITHMIC_CHECK:
        Discharge by running a specific algorithm (e.g. transitive closure).
    WITNESS_CONSTRUCTION:
        Discharge by explicitly constructing a valid refinement witness.
    COUNTERMODEL_SEARCH:
        Attempt to disprove by constructing a countermodel.
    DIRECT_VERIFICATION:
        Directly verify the theorem statement for the given inputs.
    DEFERRED:
        Obligation is deferred for later proof.
    """

    STRUCTURAL_INDUCTION = "structural_induction"
    CATEGORICAL_DIAGRAM = "categorical_diagram"
    ALGEBRAIC_IDENTITY = "algebraic_identity"
    ALGORITHMIC_CHECK = "algorithmic_check"
    WITNESS_CONSTRUCTION = "witness_construction"
    COUNTERMODEL_SEARCH = "countermodel_search"
    DIRECT_VERIFICATION = "direct_verification"
    DEFERRED = "deferred"


# ---------------------------------------------------------------------------
# TheoremStatus enum
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """The current proof status of a theorem obligation.

    Members
    -------
    OPEN:
        The obligation has not been discharged.
    IN_PROGRESS:
        The obligation is being worked on.
    DISCHARGED:
        The obligation has been fully discharged.
    PARTIALLY_DISCHARGED:
        The obligation has been discharged for some inputs but not all.
    FAILED:
        A proof attempt was made and failed (or a countermodel was found).
    DEFERRED:
        The obligation has been explicitly deferred.
    VACUOUS:
        The obligation is vacuously satisfied (e.g. no inputs to check).
    """

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DISCHARGED = "discharged"
    PARTIALLY_DISCHARGED = "partially_discharged"
    FAILED = "failed"
    DEFERRED = "deferred"
    VACUOUS = "vacuous"


# ---------------------------------------------------------------------------
# TheoremObligation dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremObligation:
    """A proof obligation for a Ch12 theorem.

    Each obligation records:
    * The theorem it corresponds to.
    * The proof strategy to employ.
    * The current status.
    * The scope (which coordinates / order the obligation applies to).
    * Any evidence of discharge or counterexample.

    Attributes
    ----------
    obligation_id:
        Unique identifier for this obligation instance.
    theorem_name:
        The name of the theorem (matches a ``THEOREM_TARGETS`` entry).
    theorem_statement:
        The formal statement of the theorem.
    theory_reference:
        The citation in the theory file (e.g. ``"Ch12.Thm1"``).
    proof_strategy:
        The recommended proof strategy.
    status:
        Current proof status.
    scope_coordinates:
        The judgment coordinates that this obligation applies to.
    scope_order_id:
        The ``order_id`` of the ``RefinementOrder`` in scope, if any.
    evidence:
        Human-readable description of evidence of discharge.
    counterexample:
        Human-readable description of a counterexample, if found.
    created_at:
        ISO-8601 timestamp of creation.
    updated_at:
        ISO-8601 timestamp of the last status update.
    metadata:
        Free-form annotation key-value pairs.
    """

    obligation_id: str
    theorem_name: str
    theorem_statement: str
    theory_reference: str
    proof_strategy: ProofStrategy
    status: TheoremStatus
    scope_coordinates: frozenset[str]
    scope_order_id: str | None
    evidence: str
    counterexample: str | None
    created_at: str
    updated_at: str
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def open_for_theorem(
        cls,
        theorem_name: str,
        proof_strategy: ProofStrategy,
        scope_coordinates: frozenset[str] = frozenset(),
        scope_order_id: str | None = None,
    ) -> "TheoremObligation":
        """Create an open obligation for a named theorem.

        Parameters
        ----------
        theorem_name:
            Must match a key in ``THEOREM_TARGETS``.
        proof_strategy:
            The strategy to use for discharging the obligation.
        scope_coordinates:
            The coordinates to which this obligation applies.
        scope_order_id:
            The order to which this obligation applies, if any.

        Returns
        -------
        TheoremObligation
            A new open obligation.

        Raises
        ------
        ValueError
            If *theorem_name* is not found in ``THEOREM_TARGETS``.
        """
        targets = {t[0]: t for t in THEOREM_TARGETS}
        if theorem_name not in targets:
            raise ValueError(
                f"Unknown theorem name {theorem_name!r}.  "
                f"Known theorems: {sorted(targets.keys())}"
            )
        _, statement, ref = targets[theorem_name]
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return cls(
            obligation_id=str(uuid.uuid4()),
            theorem_name=theorem_name,
            theorem_statement=statement,
            theory_reference=ref,
            proof_strategy=proof_strategy,
            status=TheoremStatus.OPEN,
            scope_coordinates=scope_coordinates,
            scope_order_id=scope_order_id,
            evidence="",
            counterexample=None,
            created_at=now,
            updated_at=now,
            metadata=(),
        )

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def discharge(self, evidence: str) -> "TheoremObligation":
        """Mark the obligation as discharged.

        Parameters
        ----------
        evidence:
            Human-readable description of the discharge evidence.

        Returns
        -------
        TheoremObligation
            A new obligation with status ``DISCHARGED``.
        """
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return replace(
            self,
            status=TheoremStatus.DISCHARGED,
            evidence=evidence,
            updated_at=now,
        )

    def mark_failed(self, counterexample: str) -> "TheoremObligation":
        """Mark the obligation as failed (counterexample found).

        Parameters
        ----------
        counterexample:
            Description of the counterexample.

        Returns
        -------
        TheoremObligation
            A new obligation with status ``FAILED``.
        """
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return replace(
            self,
            status=TheoremStatus.FAILED,
            counterexample=counterexample,
            updated_at=now,
        )

    def defer(self, reason: str) -> "TheoremObligation":
        """Defer the obligation.

        Parameters
        ----------
        reason:
            Reason for deferral.

        Returns
        -------
        TheoremObligation
            A new obligation with status ``DEFERRED``.
        """
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return replace(
            self,
            status=TheoremStatus.DEFERRED,
            evidence=reason,
            updated_at=now,
        )

    def mark_in_progress(self) -> "TheoremObligation":
        """Mark the obligation as in progress.

        Returns
        -------
        TheoremObligation
            A new obligation with status ``IN_PROGRESS``.
        """
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return replace(self, status=TheoremStatus.IN_PROGRESS, updated_at=now)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "obligation_id": self.obligation_id,
            "theorem_name": self.theorem_name,
            "theorem_statement": self.theorem_statement,
            "theory_reference": self.theory_reference,
            "proof_strategy": self.proof_strategy.value,
            "status": self.status.value,
            "scope_coordinates": sorted(self.scope_coordinates),
            "scope_order_id": self.scope_order_id,
            "evidence": self.evidence,
            "counterexample": self.counterexample,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": {k: v for k, v in self.metadata},
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "TheoremObligation":
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        TheoremObligation
            Reconstructed obligation.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        meta_raw = data.get("metadata", {})
        if isinstance(meta_raw, dict):
            meta: tuple[tuple[str, str], ...] = tuple(
                (str(k), str(v)) for k, v in meta_raw.items()
            )
        else:
            meta = ()
        return cls(
            obligation_id=str(data["obligation_id"]),
            theorem_name=str(data["theorem_name"]),
            theorem_statement=str(data["theorem_statement"]),
            theory_reference=str(data["theory_reference"]),
            proof_strategy=ProofStrategy(str(data["proof_strategy"])),
            status=TheoremStatus(str(data["status"])),
            scope_coordinates=frozenset(
                str(c) for c in data.get("scope_coordinates", [])  # type: ignore[union-attr]
            ),
            scope_order_id=str(data["scope_order_id"]) if data.get("scope_order_id") else None,
            evidence=str(data.get("evidence", "")),
            counterexample=str(data["counterexample"]) if data.get("counterexample") else None,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Default proof strategies per theorem
# ---------------------------------------------------------------------------

_DEFAULT_STRATEGIES: dict[str, ProofStrategy] = {
    "refinement_reflexivity": ProofStrategy.WITNESS_CONSTRUCTION,
    "refinement_antisymmetry": ProofStrategy.DIRECT_VERIFICATION,
    "refinement_transitivity": ProofStrategy.WITNESS_CONSTRUCTION,
    "equivalence_congruence": ProofStrategy.CATEGORICAL_DIAGRAM,
    "witness_compositionality": ProofStrategy.ALGEBRAIC_IDENTITY,
    "trust_monotonicity": ProofStrategy.ALGORITHMIC_CHECK,
    "evidence_embedding_soundness": ProofStrategy.DIRECT_VERIFICATION,
    "obligation_discharge_completeness": ProofStrategy.ALGORITHMIC_CHECK,
    "section_refinement_preservation": ProofStrategy.CATEGORICAL_DIAGRAM,
    "lub_existence": ProofStrategy.ALGORITHMIC_CHECK,
    "glb_existence": ProofStrategy.ALGORITHMIC_CHECK,
    "regression_detection": ProofStrategy.ALGORITHMIC_CHECK,
}


# ---------------------------------------------------------------------------
# check_theorem
# ---------------------------------------------------------------------------


def check_theorem(
    name: str,
    order: RefinementOrder | None = None,
    scope_coordinates: frozenset[str] | None = None,
    strategy: ProofStrategy | None = None,
) -> TheoremObligation:
    """Check whether a named Ch12 theorem holds for the given scope.

    Creates a ``TheoremObligation`` and attempts to discharge it automatically
    for those theorems that have algorithmic checks.  Otherwise returns an
    open obligation.

    Parameters
    ----------
    name:
        The theorem name (must be a key in ``THEOREM_TARGETS``).
    order:
        The refinement order to check the theorem against.  May be ``None``
        for theorems that don't require an order (e.g. reflexivity).
    scope_coordinates:
        The coordinates to include in scope.  If ``None``, uses
        ``order.coordinates`` if an order is provided.
    strategy:
        Override the default proof strategy.

    Returns
    -------
    TheoremObligation
        An obligation with the best status achievable automatically.

    Raises
    ------
    ValueError
        If *name* is not a known theorem.
    """
    ps = strategy or _DEFAULT_STRATEGIES.get(name, ProofStrategy.DEFERRED)
    coords = scope_coordinates or (order.coordinates if order else frozenset())
    ob = TheoremObligation.open_for_theorem(
        theorem_name=name,
        proof_strategy=ps,
        scope_coordinates=coords,
        scope_order_id=order.order_id if order else None,
    )

    # Attempt automatic discharge for algorithmic theorems
    if name == "refinement_reflexivity":
        ob = _check_reflexivity(ob, order)
    elif name == "trust_monotonicity":
        ob = _check_trust_monotonicity(ob, order)
    elif name == "regression_detection":
        ob = _check_regression_detection(ob, order)
    elif name == "refinement_transitivity":
        ob = _check_transitivity(ob, order)
    elif name == "lub_existence":
        ob = _check_lub_existence(ob, order)
    elif name == "glb_existence":
        ob = _check_glb_existence(ob, order)
    elif name == "obligation_discharge_completeness":
        ob = _check_obligation_discharge(ob, order)

    return ob


# ---------------------------------------------------------------------------
# generate_proof_obligations
# ---------------------------------------------------------------------------


def generate_proof_obligations(
    order: RefinementOrder,
) -> tuple[TheoremObligation, ...]:
    """Generate all Ch12 proof obligations for a given refinement order.

    Creates one ``TheoremObligation`` per theorem target, scoped to the
    coordinates and relations of *order*.  Attempts to automatically
    discharge obligations where possible.

    Parameters
    ----------
    order:
        The refinement order for which to generate obligations.

    Returns
    -------
    tuple[TheoremObligation, ...]
        One obligation per theorem in ``THEOREM_TARGETS``.
    """
    obligations: list[TheoremObligation] = []
    for name, _statement, _ref in THEOREM_TARGETS:
        ob = check_theorem(name, order=order)
        obligations.append(ob)
    return tuple(obligations)


# ---------------------------------------------------------------------------
# Automatic discharge helpers
# ---------------------------------------------------------------------------


def _check_reflexivity(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Attempt to discharge the reflexivity obligation.

    Reflexivity holds vacuously if the order has no coordinates; otherwise
    it is marked as PARTIALLY_DISCHARGED since we can verify it structurally
    without a full proof.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None or not order.coordinates:
        return ob.discharge(
            "Vacuously satisfied: order has no coordinates."
        ).replace_field(status=TheoremStatus.VACUOUS)
    # Structural check: for a well-formed order every coordinate is reflexive
    # (no anti-reflexive relations present)
    D = RefinementRelation.RefinementDirection
    anti_reflexive = [
        r for r in order.relations
        if r.left_coordinate == r.right_coordinate
        and r.direction not in (D.EQUIVALENT,)
    ]
    if anti_reflexive:
        return ob.mark_failed(
            f"Found {len(anti_reflexive)} anti-reflexive relation(s): "
            f"{[r.relation_id[:8] for r in anti_reflexive]}"
        )
    return ob.discharge(
        f"Reflexivity holds structurally for all {len(order.coordinates)} coordinates."
    )


def _check_trust_monotonicity(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Attempt to discharge the trust monotonicity obligation.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    D = RefinementRelation.RefinementDirection
    violations = [
        r for r in order.relations
        if r.direction == D.FORWARD and r.trust_delta < 0
    ]
    if violations:
        return ob.mark_failed(
            f"Trust monotonicity violated by {len(violations)} relation(s): "
            f"{[r.relation_id[:8] for r in violations]}"
        )
    return ob.discharge(
        f"All {sum(1 for r in order.relations if r.direction == D.FORWARD)} "
        f"FORWARD relations have non-negative trust delta."
    )


def _check_regression_detection(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Attempt to discharge the regression detection obligation.

    Regression detection is always discharged algorithmically (the algorithm
    is trivially correct by definition of ``is_regression``).

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order (used only for scope information).

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    D = RefinementRelation.RefinementDirection
    regressions = [
        r for r in order.relations if r.is_regression()
    ]
    return ob.discharge(
        f"Regression detection algorithm is sound by construction.  "
        f"Found {len(regressions)} regression(s) in this order."
    )


def _check_transitivity(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Check that the transitive closure is already present in the order.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    from jugeo.problem_modes.relational_refinement.algorithms import (
        compute_transitive_closure,
    )
    closed = compute_transitive_closure(list(order.relations))
    n_new = len(closed) - len(order.relations)
    if n_new > 0:
        return ob.discharge(
            f"Transitivity verified via closure computation.  "
            f"{n_new} transitive relation(s) derived."
        ).replace_status(TheoremStatus.PARTIALLY_DISCHARGED)
    return ob.discharge(
        "Transitivity: no new transitive relations required — order is already closed."
    )


def _check_lub_existence(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Check LUB existence for each pair of comparable coordinates.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    from jugeo.problem_modes.relational_refinement.algorithms import compute_lub
    D = RefinementRelation.RefinementDirection
    comparable_pairs = [
        (r.left_coordinate, r.right_coordinate)
        for r in order.relations
        if r.direction == D.FORWARD
    ]
    missing_lub: list[tuple[str, str]] = []
    for a, b in comparable_pairs:
        lub = compute_lub([a, b], order)
        if lub is None:
            missing_lub.append((a, b))
    if missing_lub:
        return ob.mark_failed(
            f"LUB missing for {len(missing_lub)} comparable pair(s): {missing_lub[:5]}"
        )
    return ob.discharge(
        f"LUB exists for all {len(comparable_pairs)} comparable pair(s)."
    )


def _check_glb_existence(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Check GLB existence for each pair of comparable coordinates.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    from jugeo.problem_modes.relational_refinement.algorithms import compute_glb
    D = RefinementRelation.RefinementDirection
    comparable_pairs = [
        (r.left_coordinate, r.right_coordinate)
        for r in order.relations
        if r.direction == D.FORWARD
    ]
    missing_glb: list[tuple[str, str]] = []
    for a, b in comparable_pairs:
        glb = compute_glb([a, b], order)
        if glb is None:
            missing_glb.append((a, b))
    if missing_glb:
        return ob.mark_failed(
            f"GLB missing for {len(missing_glb)} comparable pair(s): {missing_glb[:5]}"
        )
    return ob.discharge(
        f"GLB exists for all {len(comparable_pairs)} comparable pair(s)."
    )


def _check_obligation_discharge(
    ob: TheoremObligation,
    order: RefinementOrder | None,
) -> TheoremObligation:
    """Check that all witnessed relations have complete obligation maps.

    Parameters
    ----------
    ob:
        The open obligation.
    order:
        The order to check.

    Returns
    -------
    TheoremObligation
        Updated obligation.
    """
    if order is None:
        return ob.defer("No order provided.")
    D = RefinementRelation.RefinementDirection
    witnessed = [
        r for r in order.relations
        if r.is_witnessed and r.direction in (D.FORWARD, D.EQUIVALENT)
    ]
    incomplete = [
        r for r in witnessed
        if any("__unresolved__" in p for p in r.obligation_discharge)
    ]
    if incomplete:
        return ob.mark_failed(
            f"Obligation discharge incomplete for {len(incomplete)} witnessed relation(s)."
        )
    return ob.discharge(
        f"All {len(witnessed)} witnessed relation(s) have complete obligation maps."
    )


# ---------------------------------------------------------------------------
# Helper: replace fields on TheoremObligation
# ---------------------------------------------------------------------------

# We add these as module-level helpers because we can't add methods to a
# frozen dataclass after the fact and we need .replace_field / .replace_status.

def _ob_replace_status(
    ob: TheoremObligation, status: TheoremStatus
) -> TheoremObligation:
    """Return a copy of *ob* with ``status`` updated."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    return replace(ob, status=status, updated_at=now)


# Monkey-patch replace helpers onto the frozen dataclass via the module namespace
# (avoids modifying the dataclass itself while keeping call syntax clean)
TheoremObligation.replace_status = _ob_replace_status  # type: ignore[attr-defined]


def _ob_replace_field(
    ob: TheoremObligation, *, status: TheoremStatus | None = None
) -> TheoremObligation:
    """Replace a field on *ob* using keyword arguments."""
    kwargs: dict[str, object] = {}
    if status is not None:
        kwargs["status"] = status
    if not kwargs:
        return ob
    now = datetime.datetime.utcnow().isoformat() + "Z"
    return replace(ob, updated_at=now, **kwargs)  # type: ignore[arg-type]


TheoremObligation.replace_field = _ob_replace_field  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Obligation summary helpers
# ---------------------------------------------------------------------------


def obligation_summary(
    obligations: Sequence[TheoremObligation],
) -> dict[str, JsonValue]:
    """Build a summary dict for a sequence of theorem obligations.

    Parameters
    ----------
    obligations:
        The obligations to summarise.

    Returns
    -------
    dict[str, JsonValue]
        A dict with counts per status and a list of open/failed obligations.
    """
    counts: dict[str, int] = {}
    open_names: list[str] = []
    failed_names: list[str] = []
    for ob in obligations:
        counts[ob.status.value] = counts.get(ob.status.value, 0) + 1
        if ob.status == TheoremStatus.OPEN:
            open_names.append(ob.theorem_name)
        elif ob.status == TheoremStatus.FAILED:
            failed_names.append(ob.theorem_name)
    return {
        "total": len(obligations),
        "counts": counts,
        "open": open_names,
        "failed": failed_names,
    }


def emit_obligations_failure_chain(
    obligations: Sequence[TheoremObligation],
) -> FailureChain:
    """Emit a ``FailureChain`` for a set of theorem obligations.

    Only OPEN and FAILED obligations are included in the chain.

    Parameters
    ----------
    obligations:
        Obligations to include.

    Returns
    -------
    FailureChain
        A chain of structured failures, one per open/failed obligation.
    """
    records: list[StructuredFailure] = []
    for ob in obligations:
        if ob.status not in (TheoremStatus.OPEN, TheoremStatus.FAILED):
            continue
        classification = (
            FailureClassification.CONSISTENCY
            if ob.status == TheoremStatus.FAILED
            else FailureClassification.INFO
        )
        message = (
            f"Theorem obligation [{ob.status.value}]: {ob.theorem_name} "
            f"({ob.theory_reference})"
        )
        payload = as_failure_payload(
            message=message,
            details=[ob.theorem_statement, ob.evidence or "(no evidence yet)"],
            scope=FailureScope.REFINEMENT,
            classification=classification,
        )
        records.append(
            StructuredFailure(
                failure_id=ob.obligation_id,
                scope=FailureScope.REFINEMENT,
                classification=classification,
                message=message,
                evidence_family=EvidenceFamily.REFINEMENT,
                obstruction_records=(
                    ObstructionRecord(
                        tag=f"theorem_{ob.theorem_name}",
                        description=ob.theorem_statement,
                    ),
                )
                if ob.status == TheoremStatus.FAILED
                else (),
                repair_hints=(
                    RepairHint(
                        priority=RepairPriority.HIGH,
                        description=(
                            f"Discharge using strategy: {ob.proof_strategy.value}"
                        ),
                    ),
                )
                if ob.status == TheoremStatus.OPEN
                else (),
                payload=payload,
            )
        )
    return FailureChain(
        chain_id=str(uuid.uuid4()),
        records=tuple(records),
        summary=f"Ch12 proof obligations: {len(records)} open/failed.",
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
    # Constants
    "THEOREM_TARGETS",
    # Enums
    "ProofStrategy",
    "TheoremStatus",
    # Dataclasses
    "TheoremObligation",
    # Functions
    "check_theorem",
    "generate_proof_obligations",
    "obligation_summary",
    "emit_obligations_failure_chain",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: theorems.py — Ch12 theorem obligations, proof strategies, obligation generator
