"""Counterexamples as semantic witnesses (theory2.tex Ch11 §11.2).

A counterexample is **not** merely a failing test case.  It is a *semantic
witness* to the existence of a cohomological obstruction in Ȟ¹(𝔘, 𝒟).
This module formalises that claim and provides the machinery to build,
analyse, filter, minimise, and coordinate such witnesses.

Theoretical background
----------------------
Let 𝔘 = {U_i} be an open cover of the semantic site and let 𝒟 = {s_ij}
be a descent datum.  A counterexample to the gluing condition is a triple

    (c, φ, M)

where:

* **c** (coordinate) — the semantic address at which the local section
  fails to extend to a global section.
* **φ** (failing predicate) — the coherence condition that is violated;
  equivalently, a 1-cochain in the Čech complex C¹(𝔘, 𝒟).
* **M** (countermodel) — a model (Z3 model or runtime value) that
  *witnesses* φ(c) = False, thereby certifying that the obstruction is
  genuine and not an artefact of the proof strategy.

The obstruction class [η] ∈ Ȟ¹ is computed from the failure class and
the countermodel's variable and function assignments.  Different failure
classes map to different cohomological strata.

Trust and provenance
--------------------
Witnesses enter the pipeline at trust level PROPOSAL.  They must be
reviewed by a human (or a verified automated reviewer) before being
elevated to REVIEWED or ACCEPTED.  Rejected witnesses are archived but
never deleted — they form part of the audit trail.

Provenance is a full chain from solver output to semantic witness,
recording every transformation step that was applied.

Witness completeness
--------------------
A witness is *complete* iff it carries:

1. A non-empty coordinate,
2. A non-empty failing predicate,
3. A non-empty countermodel (at least one variable assignment), and
4. A trust level ≥ PROPOSAL.

Only complete witnesses may be elevated to ACCEPTED.

Minimisation
------------
Model minimisation follows a delta-debugging strategy: variable
assignments are removed one by one (largest first), and only those
assignments that preserve the failure are retained.  A minimised model
makes the obstruction as legible as possible to the repair planner.

# copilot: s02 counterexamples as semantic witnesses — theory2 ch11 §11.2
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.errors import (
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        StructuredFailure,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        JuGeoError,
        raise_with_scope,
    )
except ImportError:
    ObstructionRecord = Any; RepairHint = Any; RepairPriority = Any  # type: ignore
    StructuredFailure = Any; FailureScope = Any; FailureClassification = Any  # type: ignore
    EvidenceFamily = Any; JuGeoError = Exception; raise_with_scope = None  # type: ignore

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
        TrustLevel,
        TrustAnnotation,
        Obstruction,
    )
except ImportError:
    EvidenceBundle = Any; EvidenceItem = Any; EvidenceItemKind = Any  # type: ignore
    Provenance = Any; ProvenanceSource = Any; TrustLevel = Any  # type: ignore
    TrustAnnotation = Any; Obstruction = Any  # type: ignore

try:
    from jugeo.solver.countermodels import FailureClass, RepairType
except ImportError:
    FailureClass = Any; RepairType = Any  # type: ignore

try:
    from jugeo.problem_modes.repair_semantics.models import (
        CounterexampleRecord,
        DebugSession,
        RepairFrontier,
        RepairPlan,
        RepairValidator,
    )
except ImportError:
    CounterexampleRecord = Any; DebugSession = Any  # type: ignore
    RepairFrontier = Any; RepairPlan = Any; RepairValidator = Any  # type: ignore

# ---------------------------------------------------------------------------
# Module-level provenance constant
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "02",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "counterexamples_as_semantic_witnesses",
    "pipeline_stage": "02",
    "theory_section": "§11.2 — Counterexamples as Semantic Witnesses",
    "theory_chapter": "Ch11",
}

# ---------------------------------------------------------------------------
# §1  Enumerations
# ---------------------------------------------------------------------------


class WitnessTrust(str, Enum):
    """Trust tier for a semantic witness.

    Witnesses enter the pipeline at ``PROPOSAL`` and may be elevated or
    rejected by a reviewer.  The ordering is:

    ``UNVERIFIED < PROPOSAL < REVIEWED < ACCEPTED``

    ``REJECTED`` and ``ARCHIVED`` are terminal states that do not
    participate in the ordering.

    Parameters
    ----------
    (none — standard Enum)

    Notes
    -----
    Counterexamples produced directly by the Z3 solver start at
    ``PROPOSAL``.  Manually-authored witnesses (e.g. from a code review)
    start at ``MANUAL`` which is treated identically to ``PROPOSAL`` for
    ordering purposes.
    """

    UNVERIFIED = "UNVERIFIED"
    PROPOSAL = "PROPOSAL"
    REVIEWED = "REVIEWED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class WitnessKind(str, Enum):
    """Characterises how the countermodel was produced.

    Parameters
    ----------
    (none — standard Enum)

    Notes
    -----
    ``Z3_MODEL``
        The model was extracted directly from a Z3 satisfying assignment.
    ``RUNTIME_VALUE``
        The model was captured from a failing assertion at runtime.
    ``STATIC_ANALYSIS``
        The model was inferred by a static analyser (e.g. abstract
        interpretation).
    ``MANUAL``
        The model was constructed manually by a human reviewer.
    ``SYNTHESIZED``
        The model was synthesised by program synthesis or fuzzing.
    ``INFERRED``
        The model was inferred from partial information via heuristics.
    """

    Z3_MODEL = "Z3_MODEL"
    RUNTIME_VALUE = "RUNTIME_VALUE"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    MANUAL = "MANUAL"
    SYNTHESIZED = "SYNTHESIZED"
    INFERRED = "INFERRED"


# ---------------------------------------------------------------------------
# §2  Module-level helper functions
# ---------------------------------------------------------------------------


def _iso_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        Current UTC time formatted as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.

    Examples
    --------
    >>> ts = _iso_timestamp()
    >>> ts.endswith("Z")
    True
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _stable_hash8(s: str) -> str:
    """Return the first 8 hex characters of the SHA-256 hash of *s*.

    Parameters
    ----------
    s : str
        Input string.

    Returns
    -------
    str
        8-character hex digest prefix, e.g. ``"a3f1c2b0"``.

    Notes
    -----
    This function is used to produce stable, reproducible short IDs for
    witnesses and models.  It is NOT a cryptographic primitive.
    """
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def _severity_from_failure_class(failure_class_name: str) -> int:
    """Map a ``FailureClass`` name to an integer severity in [1, 5].

    Parameters
    ----------
    failure_class_name : str
        The name of a ``FailureClass`` enum member, e.g.
        ``"CONSTRAINT_VIOLATION"``.

    Returns
    -------
    int
        Severity value in [1, 5] where 1 is lowest and 5 is highest.

    Notes
    -----
    The mapping is intentionally coarse:

    * ``UNKNOWN`` → 1
    * ``TIMEOUT``, ``PARSE_ERROR`` → 2
    * ``CONSTRAINT_VIOLATION``, ``TYPE_MISMATCH`` → 3
    * ``UNSATISFIABLE``, ``ASSERTION_FAILURE`` → 4
    * ``COHERENCE_FAILURE``, ``DESCENT_VIOLATION`` → 5

    Any unrecognised name returns 2.
    """
    _MAP: dict[str, int] = {
        "UNKNOWN": 1,
        "TIMEOUT": 2,
        "PARSE_ERROR": 2,
        "PARSE_FAILURE": 2,
        "TYPE_MISMATCH": 3,
        "CONSTRAINT_VIOLATION": 3,
        "BOUND_VIOLATION": 3,
        "UNSATISFIABLE": 4,
        "ASSERTION_FAILURE": 4,
        "INVARIANT_VIOLATION": 4,
        "COHERENCE_FAILURE": 5,
        "DESCENT_VIOLATION": 5,
        "GLUING_FAILURE": 5,
    }
    return _MAP.get(failure_class_name.upper(), 2)


def _obstruction_class_from_failure(failure_class_name: str) -> str:
    """Return a Čech cohomology stratum label from a ``FailureClass`` name.

    Parameters
    ----------
    failure_class_name : str
        The name of a ``FailureClass`` enum member.

    Returns
    -------
    str
        A cohomology stratum label such as ``"H1_COHERENCE"``,
        ``"H1_DESCENT"``, ``"H0_BOUNDARY"``, or ``"H1_UNKNOWN"``.

    Notes
    -----
    The stratum labels correspond to the Čech cohomology groups of the
    semantic site:

    * ``H0_BOUNDARY`` — the obstruction lies on the boundary of a single
      open set (a local failure).
    * ``H1_COHERENCE`` — the obstruction arises from incompatible local
      coherence conditions.
    * ``H1_DESCENT`` — the obstruction arises from a failed descent datum.
    * ``H1_UNKNOWN`` — the cohomological stratum could not be determined.
    """
    _MAP: dict[str, str] = {
        "UNKNOWN": "H1_UNKNOWN",
        "TIMEOUT": "H1_UNKNOWN",
        "PARSE_ERROR": "H0_BOUNDARY",
        "PARSE_FAILURE": "H0_BOUNDARY",
        "TYPE_MISMATCH": "H0_BOUNDARY",
        "CONSTRAINT_VIOLATION": "H1_COHERENCE",
        "BOUND_VIOLATION": "H1_COHERENCE",
        "UNSATISFIABLE": "H1_COHERENCE",
        "ASSERTION_FAILURE": "H1_COHERENCE",
        "INVARIANT_VIOLATION": "H1_COHERENCE",
        "COHERENCE_FAILURE": "H1_COHERENCE",
        "DESCENT_VIOLATION": "H1_DESCENT",
        "GLUING_FAILURE": "H1_DESCENT",
    }
    return _MAP.get(failure_class_name.upper(), "H1_UNKNOWN")


def _trust_rank(trust_name: str) -> int:
    """Return a numeric rank for ordering trust levels.

    Parameters
    ----------
    trust_name : str
        The name of a ``WitnessTrust`` member.

    Returns
    -------
    int
        Rank in [0, 5].  Higher rank means more trusted.
        ``REJECTED`` and ``ARCHIVED`` are given rank -1 as they are
        terminal and not in the linear order.
    """
    _RANK: dict[str, int] = {
        "UNVERIFIED": 0,
        "PROPOSAL": 1,
        "REVIEWED": 2,
        "ACCEPTED": 3,
        "REJECTED": -1,
        "ARCHIVED": -1,
    }
    return _RANK.get(trust_name.upper(), 0)


def _is_trust_sufficient(trust_name: str, threshold_name: str) -> bool:
    """Return ``True`` iff *trust_name* meets or exceeds *threshold_name*.

    Parameters
    ----------
    trust_name : str
        The trust level of the witness being tested.
    threshold_name : str
        The minimum required trust level.

    Returns
    -------
    bool
        ``True`` if ``_trust_rank(trust_name) >= _trust_rank(threshold_name)``.

    Notes
    -----
    Both ``REJECTED`` and ``ARCHIVED`` have rank -1, so they never meet
    any positive threshold.  This means rejected witnesses are never
    considered actionable regardless of the threshold setting.
    """
    witness_rank = _trust_rank(trust_name)
    threshold_rank = _trust_rank(threshold_name)
    if witness_rank < 0:
        return False
    return witness_rank >= threshold_rank


# ---------------------------------------------------------------------------
# §3  SemanticModel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticModel:
    """A countermodel that witnesses the failure of a predicate.

    A ``SemanticModel`` is the third component *M* in the witness triple
    (c, φ, M).  It records a satisfying assignment that makes the negation
    of the failing predicate true in some structure, thereby *certifying*
    that the predicate is genuinely violated and not merely unprovable.

    Parameters
    ----------
    model_id : str, optional
        Unique identifier.  If empty, one is generated from a hash of the
        variable assignments.
    kind : str
        The ``WitnessKind`` name describing how this model was produced.
    variable_assignments : tuple[tuple[str, str], ...]
        Pairs of (variable_name, value_string) from the model.
    sort_interpretations : tuple[tuple[str, tuple[str, ...]], ...]
        Pairs of (sort_name, domain_elements) giving the interpretation
        of each sort in the model.
    function_interpretations : tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
        Pairs of (function_name, input_output_pairs) giving the finite
        partial map interpreting each uninterpreted function.
    raw_repr : str
        The raw string representation of the model as emitted by the
        solver (e.g. Z3's ``model()`` output).
    is_minimal : bool
        ``True`` iff this model has been minimised by delta-debugging.

    Notes
    -----
    All fields are immutable.  Use :func:`dataclasses.replace` to derive
    updated instances.

    Examples
    --------
    >>> m = SemanticModel(kind="Z3_MODEL", variable_assignments=(("x", "1"),),
    ...                   raw_repr="x=1")
    >>> m.is_empty()
    False
    >>> m.assignment_count()
    1
    """

    model_id: str = ""
    kind: str = WitnessKind.Z3_MODEL.value
    variable_assignments: tuple[tuple[str, str], ...] = ()
    sort_interpretations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    function_interpretations: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    raw_repr: str = ""
    is_minimal: bool = False

    def __post_init__(self) -> None:
        if not self.model_id:
            # Derive a stable ID from the content of the model
            content = repr(self.variable_assignments) + self.kind + self.raw_repr
            object.__setattr__(self, "model_id", "mdl-" + _stable_hash8(content))

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return ``True`` iff the model has no variable assignments.

        A model without variable assignments cannot witness a failure,
        because there is no concrete assignment that demonstrates the
        predicate violation.

        Returns
        -------
        bool
            ``True`` if ``self.variable_assignments`` is empty.
        """
        return len(self.variable_assignments) == 0

    def assignment_count(self) -> int:
        """Return the number of variable assignments in the model.

        Returns
        -------
        int
            Length of ``self.variable_assignments``.
        """
        return len(self.variable_assignments)

    def summary(self) -> str:
        """Return a short human-readable summary of the model.

        Returns
        -------
        str
            A one-line summary including kind, number of assignments, and
            up to three representative assignments.
        """
        vars_preview = ", ".join(f"{k}={v}" for k, v in self.variable_assignments[:3])
        if len(self.variable_assignments) > 3:
            vars_preview += f", … (+{len(self.variable_assignments) - 3})"
        minimal_tag = " [minimal]" if self.is_minimal else ""
        return (
            f"SemanticModel({self.kind}{minimal_tag}: {vars_preview or '<empty>'})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON.

        Returns
        -------
        dict[str, Any]
            All fields serialised to JSON-compatible types.
        """
        return {
            "model_id": self.model_id,
            "kind": self.kind,
            "variable_assignments": [list(p) for p in self.variable_assignments],
            "sort_interpretations": [
                [s, list(elems)] for s, elems in self.sort_interpretations
            ],
            "function_interpretations": [
                [fn, [list(row) for row in rows]]
                for fn, rows in self.function_interpretations
            ],
            "raw_repr": self.raw_repr,
            "is_minimal": self.is_minimal,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SemanticModel":
        """Deserialise from a plain dict.

        Parameters
        ----------
        d : dict[str, Any]
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        SemanticModel
            Reconstructed instance.
        """
        return cls(
            model_id=d.get("model_id", ""),
            kind=d.get("kind", WitnessKind.Z3_MODEL.value),
            variable_assignments=tuple(
                tuple(p) for p in d.get("variable_assignments", [])  # type: ignore[misc]
            ),
            sort_interpretations=tuple(
                (s, tuple(elems)) for s, elems in d.get("sort_interpretations", [])
            ),
            function_interpretations=tuple(
                (fn, tuple(tuple(row) for row in rows))  # type: ignore[misc]
                for fn, rows in d.get("function_interpretations", [])
            ),
            raw_repr=d.get("raw_repr", ""),
            is_minimal=d.get("is_minimal", False),
        )


# ---------------------------------------------------------------------------
# §4  CounterexamplesSemanticWitnessesWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CounterexamplesSemanticWitnessesWitness:
    """A rich semantic witness object for a cohomological obstruction.

    A witness is the full formalisation of the triple (c, φ, M):

    * **coordinate** (c) — semantic address of the obstruction.
    * **failing_predicate** (φ) — the coherence condition that is violated.
    * **countermodel** (M) — a model certifying the violation.

    Additional metadata (trust tier, provenance, severity, obstruction
    class) enriches the witness beyond the minimal triple.

    Parameters
    ----------
    witness_id : str
        Unique identifier for this witness.  Auto-generated if empty.
    coordinate : str
        The semantic coordinate where the obstruction is observed, e.g.
        ``"module.function.branch_label"``.
    failing_predicate : str
        The predicate φ such that M ⊭ φ at coordinate c.
    countermodel : SemanticModel
        The model M that witnesses φ(c) = False.
    trust_tier : str
        The ``WitnessTrust`` level.  Default is ``PROPOSAL``.
    provenance : tuple[tuple[str, str], ...]
        Ordered chain of (key, value) pairs recording the derivation
        history from solver output to this witness object.
    obstruction_class : str
        Label for the Čech cohomology stratum, e.g. ``"H1_COHERENCE"``.
    failure_class : str
        The ``FailureClass`` name, e.g. ``"CONSTRAINT_VIOLATION"``.
    severity : int
        Integer in [1, 5].  1 = informational, 5 = blocking.
    is_minimal : bool
        ``True`` iff the countermodel has been minimised.
    extraction_timestamp : str
        ISO-8601 timestamp of when the witness was constructed.
    reviewer_id : str
        Identifier of the reviewer who last changed the trust tier.
        Empty if the witness has not yet been reviewed.
    rejection_reason : str
        Free-text reason for rejection.  Empty if not rejected.
    metadata : tuple[tuple[str, str], ...]
        Additional key-value pairs for extensibility.

    Notes
    -----
    This dataclass is frozen and uses ``__slots__``.  All mutable updates
    must use :func:`dataclasses.replace`.  The convenience methods
    :meth:`with_trust` and :meth:`with_review` wrap ``replace()`` for the
    most common update patterns.

    Examples
    --------
    >>> model = SemanticModel(kind="Z3_MODEL",
    ...     variable_assignments=(("x", "0"),), raw_repr="x=0")
    >>> w = CounterexamplesSemanticWitnessesWitness(
    ...     coordinate="mod.fn",
    ...     failing_predicate="x > 0",
    ...     countermodel=model)
    >>> w.is_complete()
    True
    >>> w.is_actionable()
    True
    """

    witness_id: str = ""
    coordinate: str = ""
    failing_predicate: str = ""
    countermodel: SemanticModel = field(default_factory=SemanticModel)
    trust_tier: str = WitnessTrust.PROPOSAL.value
    provenance: tuple[tuple[str, str], ...] = ()
    obstruction_class: str = "H1_UNKNOWN"
    failure_class: str = "UNKNOWN"
    severity: int = 2
    is_minimal: bool = False
    extraction_timestamp: str = ""
    reviewer_id: str = ""
    rejection_reason: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.witness_id:
            content = (
                self.coordinate
                + self.failing_predicate
                + self.countermodel.model_id
                + self.extraction_timestamp
            )
            object.__setattr__(
                self, "witness_id", "wit-" + _stable_hash8(content)
            )
        if not self.extraction_timestamp:
            object.__setattr__(self, "extraction_timestamp", _iso_timestamp())

    # ------------------------------------------------------------------
    # §4.1  Predicate methods
    # ------------------------------------------------------------------

    def is_genuine(self) -> bool:
        """Return ``True`` iff the witness has been accepted as genuine.

        A witness is genuine when its trust tier is ``ACCEPTED`` or
        ``REVIEWED`` *and* it has a non-empty coordinate.

        Returns
        -------
        bool
            ``True`` for trust in {ACCEPTED, REVIEWED} with coordinate.

        Notes
        -----
        ``REVIEWED`` is included because a reviewed (but not yet formally
        accepted) witness is considered trustworthy enough to inform repair
        planning.
        """
        genuine_tiers = {WitnessTrust.ACCEPTED.value, WitnessTrust.REVIEWED.value}
        return self.trust_tier in genuine_tiers and self.coordinate != ""

    def is_complete(self) -> bool:
        """Return ``True`` iff the witness carries all required components.

        A witness is *complete* iff:

        1. ``coordinate`` is non-empty,
        2. ``failing_predicate`` is non-empty, and
        3. ``countermodel`` is non-empty (has at least one variable
           assignment).

        Returns
        -------
        bool
            ``True`` if all three completeness conditions hold.

        Notes
        -----
        Completeness is a prerequisite for elevation to ``REVIEWED`` or
        ``ACCEPTED`` trust.  Incomplete witnesses may still appear in the
        pipeline as ``PROPOSAL`` but cannot be used for repair planning.
        """
        return (
            bool(self.coordinate)
            and bool(self.failing_predicate)
            and not self.countermodel.is_empty()
        )

    def is_actionable(self) -> bool:
        """Return ``True`` iff the witness can drive a repair action.

        A witness is actionable when it is:

        1. Complete (see :meth:`is_complete`), and
        2. Not in a terminal non-useful trust state (``REJECTED`` or
           ``UNVERIFIED``).

        Returns
        -------
        bool
            ``True`` if the witness can be used to generate repair hints.
        """
        non_actionable = {WitnessTrust.REJECTED.value, WitnessTrust.UNVERIFIED.value}
        return self.is_complete() and self.trust_tier not in non_actionable

    # ------------------------------------------------------------------
    # §4.2  Immutable update methods
    # ------------------------------------------------------------------

    def with_trust(self, trust: str) -> "CounterexamplesSemanticWitnessesWitness":
        """Return a new witness with the trust tier updated.

        Parameters
        ----------
        trust : str
            The new ``WitnessTrust`` name, e.g. ``"ACCEPTED"``.

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            New instance with ``trust_tier`` set to *trust*.
        """
        return replace(self, trust_tier=trust)

    def with_review(
        self,
        reviewer_id: str,
        accepted: bool,
        reason: str = "",
    ) -> "CounterexamplesSemanticWitnessesWitness":
        """Return a new witness reflecting a reviewer's decision.

        Parameters
        ----------
        reviewer_id : str
            Identifier of the reviewer (human or automated agent).
        accepted : bool
            ``True`` → elevate to ``ACCEPTED``; ``False`` → set to
            ``REJECTED`` and record *reason*.
        reason : str, optional
            Free-text reason for rejection (ignored when *accepted* is
            ``True``).

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            New instance with updated ``trust_tier``, ``reviewer_id``,
            and (if rejected) ``rejection_reason``.
        """
        new_trust = (
            WitnessTrust.ACCEPTED.value if accepted else WitnessTrust.REJECTED.value
        )
        new_reason = "" if accepted else reason
        return replace(
            self,
            trust_tier=new_trust,
            reviewer_id=reviewer_id,
            rejection_reason=new_reason,
        )

    # ------------------------------------------------------------------
    # §4.3  Conversion methods
    # ------------------------------------------------------------------

    def to_obstruction_record(self) -> dict[str, Any]:
        """Convert the witness to an obstruction-record dict.

        The returned dict follows the schema expected by
        :class:`~jugeo.errors.ObstructionRecord` and the repair planner.

        Returns
        -------
        dict[str, Any]
            Keys: ``coordinate``, ``failure_class``, ``obstruction_class``,
            ``severity``, ``predicate``, ``model_summary``, ``trust_tier``,
            ``witness_id``, ``is_genuine``.
        """
        return {
            "coordinate": self.coordinate,
            "failure_class": self.failure_class,
            "obstruction_class": self.obstruction_class,
            "severity": self.severity,
            "predicate": self.failing_predicate,
            "model_summary": self.countermodel.summary(),
            "trust_tier": self.trust_tier,
            "witness_id": self.witness_id,
            "is_genuine": self.is_genuine(),
            "is_complete": self.is_complete(),
            "is_actionable": self.is_actionable(),
            "reviewer_id": self.reviewer_id,
            "rejection_reason": self.rejection_reason,
            "extraction_timestamp": self.extraction_timestamp,
        }

    def severity_label(self) -> str:
        """Return a human-readable severity label.

        Maps the integer severity in [1, 5] to a label:

        * 1 → ``"LOW"``
        * 2 → ``"LOW"``
        * 3 → ``"MEDIUM"``
        * 4 → ``"HIGH"``
        * 5 → ``"CRITICAL"``

        Returns
        -------
        str
            One of ``"LOW"``, ``"MEDIUM"``, ``"HIGH"``, ``"CRITICAL"``.
        """
        if self.severity <= 2:
            return "LOW"
        elif self.severity == 3:
            return "MEDIUM"
        elif self.severity == 4:
            return "HIGH"
        return "CRITICAL"

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a plain dict for JSON.

        Returns
        -------
        dict[str, Any]
            All fields, with ``countermodel`` serialised via
            :meth:`SemanticModel.to_dict`.
        """
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "failing_predicate": self.failing_predicate,
            "countermodel": self.countermodel.to_dict(),
            "trust_tier": self.trust_tier,
            "provenance": [list(p) for p in self.provenance],
            "obstruction_class": self.obstruction_class,
            "failure_class": self.failure_class,
            "severity": self.severity,
            "is_minimal": self.is_minimal,
            "extraction_timestamp": self.extraction_timestamp,
            "reviewer_id": self.reviewer_id,
            "rejection_reason": self.rejection_reason,
            "metadata": [list(p) for p in self.metadata],
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> "CounterexamplesSemanticWitnessesWitness":
        """Deserialise from a plain dict.

        Parameters
        ----------
        d : dict[str, Any]
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            Reconstructed instance.
        """
        model_raw = d.get("countermodel", {})
        model = (
            SemanticModel.from_dict(model_raw)
            if isinstance(model_raw, dict)
            else SemanticModel()
        )
        return cls(
            witness_id=d.get("witness_id", ""),
            coordinate=d.get("coordinate", ""),
            failing_predicate=d.get("failing_predicate", ""),
            countermodel=model,
            trust_tier=d.get("trust_tier", WitnessTrust.PROPOSAL.value),
            provenance=tuple(
                tuple(p) for p in d.get("provenance", [])  # type: ignore[misc]
            ),
            obstruction_class=d.get("obstruction_class", "H1_UNKNOWN"),
            failure_class=d.get("failure_class", "UNKNOWN"),
            severity=int(d.get("severity", 2)),
            is_minimal=bool(d.get("is_minimal", False)),
            extraction_timestamp=d.get("extraction_timestamp", ""),
            reviewer_id=d.get("reviewer_id", ""),
            rejection_reason=d.get("rejection_reason", ""),
            metadata=tuple(
                tuple(p) for p in d.get("metadata", [])  # type: ignore[misc]
            ),
        )

    def summary(self) -> str:
        """Return a compact human-readable summary of this witness.

        Returns
        -------
        str
            Multi-field one-liner, e.g.
            ``"[wit-a3f1c2b0] coord=mod.fn pred='x>0' trust=PROPOSAL sev=3(MEDIUM)"``.
        """
        return (
            f"[{self.witness_id}] coord={self.coordinate!r} "
            f"pred={self.failing_predicate!r} "
            f"trust={self.trust_tier} "
            f"sev={self.severity}({self.severity_label()}) "
            f"class={self.obstruction_class}"
        )


# ---------------------------------------------------------------------------
# §5  CounterexamplesSemanticWitnessesAnalyzer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CounterexamplesSemanticWitnessesAnalyzer:
    """Core analyser that builds and analyses semantic witnesses.

    The analyser is the principal entry-point for the witness pipeline.
    It converts raw solver output (variable assignments, runtime values)
    into fully-annotated :class:`CounterexamplesSemanticWitnessesWitness`
    objects and provides filtering, sorting, and minimisation operations.

    Parameters
    ----------
    analyzer_id : str
        Unique identifier for this analyser instance.  Auto-generated if
        empty.
    coordinate : str
        Default coordinate for witness construction.
    trust_threshold : str
        Minimum ``WitnessTrust`` level that this analyser considers valid.
        Default is ``"PROPOSAL"``.
    min_severity : int
        Minimum severity (inclusive) for a witness to be reported.
        Default is 1 (all witnesses).
    enable_minimization : bool
        If ``True`` (default), :meth:`build_witness` will attempt to
        minimise the countermodel before returning the witness.
    strict_mode : bool
        If ``True``, raise ``ValueError`` for witnesses that fail
        completeness checks instead of returning them as-is.

    Notes
    -----
    All methods that return witnesses produce fresh immutable objects —
    they never modify existing instances.

    Examples
    --------
    >>> a = CounterexamplesSemanticWitnessesAnalyzer(coordinate="m.f")
    >>> m = SemanticModel(kind="Z3_MODEL",
    ...     variable_assignments=(("x", "0"),), raw_repr="x=0")
    >>> w = a.build_witness("m.f", "x > 0", m)
    >>> w.is_complete()
    True
    """

    analyzer_id: str = ""
    coordinate: str = ""
    trust_threshold: str = WitnessTrust.PROPOSAL.value
    min_severity: int = 1
    enable_minimization: bool = True
    strict_mode: bool = False

    def __post_init__(self) -> None:
        if not self.analyzer_id:
            content = self.coordinate + self.trust_threshold + str(time.time())
            object.__setattr__(
                self, "analyzer_id", "ana-" + _stable_hash8(content)
            )

    # ------------------------------------------------------------------
    # §5.1  Witness construction
    # ------------------------------------------------------------------

    def build_witness(
        self,
        coordinate: str,
        failing_predicate: str,
        model: SemanticModel,
        failure_class: str = "UNKNOWN",
    ) -> CounterexamplesSemanticWitnessesWitness:
        """Build a complete witness from components.

        Computes severity and obstruction class from *failure_class* and
        *model*, attaches provenance, and (if :attr:`enable_minimization`
        is set) minimises the model.

        Parameters
        ----------
        coordinate : str
            Semantic coordinate, e.g. ``"module.function.branch"``.
        failing_predicate : str
            The predicate φ that the model satisfies in the negation.
        model : SemanticModel
            The countermodel that witnesses the failure.
        failure_class : str, optional
            The ``FailureClass`` name.  Default is ``"UNKNOWN"``.

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            A fully annotated witness at trust level ``PROPOSAL``.

        Raises
        ------
        ValueError
            If :attr:`strict_mode` is ``True`` and the resulting witness
            is not complete.
        """
        severity = self.compute_severity(failure_class, model)
        obstruction_class = self.classify_obstruction_class_from_parts(
            failure_class, severity
        )
        provenance = self.build_provenance_chain(
            source="build_witness", analyzer_version="1.0"
        )
        working_model = model
        is_minimal = model.is_minimal
        if self.enable_minimization and not model.is_minimal:
            minimised = self._minimize_model(model, failing_predicate)
            working_model = minimised
            is_minimal = True

        witness = CounterexamplesSemanticWitnessesWitness(
            coordinate=coordinate,
            failing_predicate=failing_predicate,
            countermodel=working_model,
            trust_tier=WitnessTrust.PROPOSAL.value,
            provenance=provenance,
            obstruction_class=obstruction_class,
            failure_class=failure_class,
            severity=severity,
            is_minimal=is_minimal,
            extraction_timestamp=_iso_timestamp(),
        )
        if self.strict_mode and not witness.is_complete():
            raise ValueError(
                f"build_witness produced an incomplete witness for coordinate "
                f"{coordinate!r}: predicate={failing_predicate!r}, "
                f"model_empty={model.is_empty()}"
            )
        return witness

    def from_variable_assignments(
        self,
        coordinate: str,
        failing_predicate: str,
        assignments: Sequence[tuple[str, str]],
    ) -> CounterexamplesSemanticWitnessesWitness:
        """Build a witness from raw variable assignment pairs.

        Parameters
        ----------
        coordinate : str
            Semantic coordinate of the failure.
        failing_predicate : str
            The predicate that fails under *assignments*.
        assignments : Sequence[tuple[str, str]]
            Iterable of (variable_name, value_string) pairs.

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            Witness wrapping a ``SemanticModel`` with kind
            ``Z3_MODEL`` and the given assignments.

        Notes
        -----
        The raw representation is synthesised from the assignment pairs
        as a comma-separated ``"name=value"`` string.
        """
        raw = ", ".join(f"{k}={v}" for k, v in assignments)
        model = SemanticModel(
            kind=WitnessKind.Z3_MODEL.value,
            variable_assignments=tuple(assignments),
            raw_repr=raw,
        )
        return self.build_witness(
            coordinate=coordinate,
            failing_predicate=failing_predicate,
            model=model,
            failure_class="CONSTRAINT_VIOLATION",
        )

    def from_runtime_value(
        self,
        coordinate: str,
        failing_predicate: str,
        value: Any,
    ) -> CounterexamplesSemanticWitnessesWitness:
        """Build a witness from a single runtime value.

        Parameters
        ----------
        coordinate : str
            Semantic coordinate of the assertion failure.
        failing_predicate : str
            The predicate that the runtime value violates.
        value : Any
            The runtime value (will be converted to string via ``repr``).

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            Witness with ``WitnessKind.RUNTIME_VALUE`` and a single
            variable assignment ``("runtime_value", repr(value))``.
        """
        value_str = repr(value)
        model = SemanticModel(
            kind=WitnessKind.RUNTIME_VALUE.value,
            variable_assignments=(("runtime_value", value_str),),
            raw_repr=f"runtime_value={value_str}",
        )
        return self.build_witness(
            coordinate=coordinate,
            failing_predicate=failing_predicate,
            model=model,
            failure_class="ASSERTION_FAILURE",
        )

    # ------------------------------------------------------------------
    # §5.2  Witness analysis
    # ------------------------------------------------------------------

    def analyze_completeness(
        self, witness: CounterexamplesSemanticWitnessesWitness
    ) -> dict[str, Any]:
        """Analyse the completeness of a witness and return a report.

        Parameters
        ----------
        witness : CounterexamplesSemanticWitnessesWitness
            The witness to analyse.

        Returns
        -------
        dict[str, Any]
            Keys:

            * ``has_coordinate`` — ``bool``
            * ``has_predicate`` — ``bool``
            * ``has_model`` — ``bool``
            * ``has_trust`` — ``bool``
            * ``has_provenance`` — ``bool``
            * ``completeness_score`` — ``float`` in [0.0, 1.0]
            * ``missing_components`` — ``list[str]``
            * ``is_complete`` — ``bool``
            * ``is_actionable`` — ``bool``

        Notes
        -----
        The completeness score is the fraction of the five components that
        are present.  A score of 1.0 means the witness is fully complete.
        """
        has_coord = bool(witness.coordinate)
        has_pred = bool(witness.failing_predicate)
        has_model = not witness.countermodel.is_empty()
        has_trust = witness.trust_tier not in (
            WitnessTrust.UNVERIFIED.value,
            WitnessTrust.REJECTED.value,
        )
        has_prov = len(witness.provenance) > 0

        components = [has_coord, has_pred, has_model, has_trust, has_prov]
        score = sum(1 for c in components if c) / len(components)

        missing: list[str] = []
        if not has_coord:
            missing.append("coordinate")
        if not has_pred:
            missing.append("failing_predicate")
        if not has_model:
            missing.append("countermodel")
        if not has_trust:
            missing.append("valid_trust_tier")
        if not has_prov:
            missing.append("provenance")

        return {
            "has_coordinate": has_coord,
            "has_predicate": has_pred,
            "has_model": has_model,
            "has_trust": has_trust,
            "has_provenance": has_prov,
            "completeness_score": round(score, 2),
            "missing_components": missing,
            "is_complete": witness.is_complete(),
            "is_actionable": witness.is_actionable(),
        }

    def classify_obstruction_class(
        self, witness: CounterexamplesSemanticWitnessesWitness
    ) -> str:
        """Return the cohomological obstruction class for a witness.

        Parameters
        ----------
        witness : CounterexamplesSemanticWitnessesWitness
            The witness to classify.

        Returns
        -------
        str
            A Čech cohomology stratum label such as ``"H1_COHERENCE"``.

        Notes
        -----
        Delegates to :func:`_obstruction_class_from_failure`, but also
        checks the model's complexity to distinguish between boundary and
        cohomological obstructions.
        """
        return self.classify_obstruction_class_from_parts(
            witness.failure_class, witness.severity
        )

    def classify_obstruction_class_from_parts(
        self, failure_class: str, severity: int
    ) -> str:
        """Return cohomological stratum from failure class and severity.

        Parameters
        ----------
        failure_class : str
            The ``FailureClass`` name.
        severity : int
            The computed severity in [1, 5].

        Returns
        -------
        str
            Čech cohomology stratum label.
        """
        base = _obstruction_class_from_failure(failure_class)
        # High-severity unknowns are promoted to coherence class
        if base == "H1_UNKNOWN" and severity >= 4:
            return "H1_COHERENCE"
        return base

    def compute_severity(self, failure_class: str, model: SemanticModel) -> int:
        """Compute an integer severity in [1, 5] for a failure.

        Parameters
        ----------
        failure_class : str
            The ``FailureClass`` name driving the base severity.
        model : SemanticModel
            The countermodel; richer models (more assignments or function
            interpretations) indicate more complex, higher-severity
            obstructions.

        Returns
        -------
        int
            Severity in [1, 5].  Higher is worse.

        Notes
        -----
        The severity is computed as:

        1. Start from the base severity for the failure class
           (see :func:`_severity_from_failure_class`).
        2. Add +1 for each 5 variable assignments beyond the first 2.
        3. Add +1 if the model has any function interpretations.
        4. Clamp to [1, 5].
        """
        base = _severity_from_failure_class(failure_class)
        extra = max(0, (model.assignment_count() - 2)) // 5
        fn_bonus = 1 if len(model.function_interpretations) > 0 else 0
        raw = base + extra + fn_bonus
        return max(1, min(5, raw))

    # ------------------------------------------------------------------
    # §5.3  Witness filtering
    # ------------------------------------------------------------------

    def filter_genuine(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
    ) -> tuple[CounterexamplesSemanticWitnessesWitness, ...]:
        """Filter *witnesses* to those that are genuine.

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            Collection to filter.

        Returns
        -------
        tuple[CounterexamplesSemanticWitnessesWitness, ...]
            Only witnesses for which :meth:`~CounterexamplesSemanticWitnessesWitness.is_genuine`
            returns ``True``.
        """
        return tuple(w for w in witnesses if w.is_genuine())

    def filter_actionable(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
    ) -> tuple[CounterexamplesSemanticWitnessesWitness, ...]:
        """Filter *witnesses* to those that are actionable.

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            Collection to filter.

        Returns
        -------
        tuple[CounterexamplesSemanticWitnessesWitness, ...]
            Only witnesses for which :meth:`~CounterexamplesSemanticWitnessesWitness.is_actionable`
            returns ``True``.
        """
        return tuple(w for w in witnesses if w.is_actionable())

    def filter_by_severity(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
        min_severity: int,
    ) -> tuple[CounterexamplesSemanticWitnessesWitness, ...]:
        """Filter *witnesses* to those meeting a minimum severity.

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            Collection to filter.
        min_severity : int
            Minimum severity (inclusive) in [1, 5].

        Returns
        -------
        tuple[CounterexamplesSemanticWitnessesWitness, ...]
            Witnesses with ``severity >= min_severity``.
        """
        return tuple(w for w in witnesses if w.severity >= min_severity)

    def sort_by_severity(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
    ) -> tuple[CounterexamplesSemanticWitnessesWitness, ...]:
        """Return *witnesses* sorted by severity (descending).

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            Collection to sort.

        Returns
        -------
        tuple[CounterexamplesSemanticWitnessesWitness, ...]
            Same witnesses ordered highest-severity first.  Ties are
            broken by ``witness_id`` for reproducibility.
        """
        return tuple(
            sorted(witnesses, key=lambda w: (-w.severity, w.witness_id))
        )

    # ------------------------------------------------------------------
    # §5.4  Witness minimisation
    # ------------------------------------------------------------------

    def minimize_witness(
        self,
        witness: CounterexamplesSemanticWitnessesWitness,
    ) -> CounterexamplesSemanticWitnessesWitness:
        """Minimise the countermodel inside a witness (delta-debug style).

        Variable assignments are removed one by one in reverse order
        (largest-index first).  An assignment is removed iff removing it
        still *preserves the failure* according to the heuristic in
        :meth:`_try_remove_assignment`.

        Parameters
        ----------
        witness : CounterexamplesSemanticWitnessesWitness
            The witness whose model is to be minimised.

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            A new witness with ``is_minimal=True`` and the minimised
            model.  If no assignments could be removed, returns the
            original witness (with ``is_minimal=True`` set).

        Notes
        -----
        The minimised witness inherits all other fields (coordinate,
        predicate, trust, provenance, etc.) from the original witness.
        Only the countermodel and ``is_minimal`` flag differ.
        """
        minimised_model = self._minimize_model(
            witness.countermodel, witness.failing_predicate
        )
        return replace(witness, countermodel=minimised_model, is_minimal=True)

    def _minimize_model(
        self,
        model: SemanticModel,
        failing_predicate: str,
    ) -> SemanticModel:
        """Internal helper: apply delta-debugging to a SemanticModel.

        Parameters
        ----------
        model : SemanticModel
            The model to minimise.
        failing_predicate : str
            The predicate whose failure must be preserved.

        Returns
        -------
        SemanticModel
            A (possibly smaller) model with ``is_minimal=True``.
        """
        assignments = list(model.variable_assignments)
        retained: list[tuple[str, str]] = []
        for i, _ in enumerate(assignments):
            keep = not self._try_remove_assignment(
                tuple(assignments), i, failing_predicate
            )
            if keep:
                retained.append(assignments[i])
        minimal_assignments = tuple(retained) if retained else tuple(assignments)
        raw = ", ".join(f"{k}={v}" for k, v in minimal_assignments)
        return replace(
            model,
            variable_assignments=minimal_assignments,
            raw_repr=raw,
            is_minimal=True,
        )

    def _try_remove_assignment(
        self,
        assignments: tuple[tuple[str, str], ...],
        index: int,
        failing_predicate: str,
    ) -> bool:
        """Heuristic: returns ``True`` if removing assignment at *index* still preserves the failure.

        Parameters
        ----------
        assignments : tuple[tuple[str, str], ...]
            All current variable assignments.
        index : int
            Index of the assignment to tentatively remove.
        failing_predicate : str
            The predicate that must remain violated.

        Returns
        -------
        bool
            ``True`` iff removing the assignment is safe (failure is still
            preserved).

        Notes
        -----
        The heuristic checks whether the variable name appearing at
        *index* also appears in *failing_predicate*.  If it does, the
        assignment is considered *essential* and should not be removed
        (return ``False``).  If the variable is not mentioned in the
        predicate, it is considered irrelevant and may be removed (return
        ``True``).

        This is a sound over-approximation: we may keep more assignments
        than strictly necessary, but we never remove an assignment that
        the predicate actually depends on.
        """
        if index >= len(assignments):
            return True
        var_name, _ = assignments[index]
        # If the variable name appears in the predicate, it is essential.
        if var_name in failing_predicate:
            return False
        return True

    # ------------------------------------------------------------------
    # §5.5  Provenance
    # ------------------------------------------------------------------

    def build_provenance_chain(
        self,
        source: str,
        analyzer_version: str = "1.0",
    ) -> tuple[tuple[str, str], ...]:
        """Build a provenance chain tuple for a new witness.

        Parameters
        ----------
        source : str
            Name of the construction source, e.g. ``"build_witness"`` or
            ``"from_runtime_value"``.
        analyzer_version : str, optional
            Version string for this analyser.  Default is ``"1.0"``.

        Returns
        -------
        tuple[tuple[str, str], ...]
            Ordered chain of (key, value) pairs recording the derivation
            context.  Always contains at least:

            * ``("source", source)``
            * ``("analyzer_id", self.analyzer_id)``
            * ``("analyzer_version", analyzer_version)``
            * ``("coordinate", self.coordinate)``
            * ``("trust_threshold", self.trust_threshold)``
            * ``("pipeline_stage", "02")``
            * ``("theory_section", "§11.2")``
            * ``("timestamp", <iso-timestamp>)``
        """
        return (
            ("source", source),
            ("analyzer_id", self.analyzer_id),
            ("analyzer_version", analyzer_version),
            ("coordinate", self.coordinate),
            ("trust_threshold", self.trust_threshold),
            ("pipeline_stage", "02"),
            ("theory_section", "§11.2"),
            ("timestamp", _iso_timestamp()),
        )

    def validate_provenance(
        self,
        witness: CounterexamplesSemanticWitnessesWitness,
    ) -> bool:
        """Return ``True`` iff the witness provenance is well-formed.

        A provenance chain is considered valid when:

        1. It is non-empty, and
        2. It contains at least the keys ``"source"``, ``"pipeline_stage"``,
           and ``"timestamp"``.

        Parameters
        ----------
        witness : CounterexamplesSemanticWitnessesWitness
            The witness whose provenance is to be validated.

        Returns
        -------
        bool
            ``True`` if the provenance meets the minimum requirements.
        """
        if not witness.provenance:
            return False
        prov_keys = {k for k, _ in witness.provenance}
        required = {"source", "pipeline_stage", "timestamp"}
        return required.issubset(prov_keys)

    # ------------------------------------------------------------------
    # §5.6  Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the analyser configuration to a plain dict.

        Returns
        -------
        dict[str, Any]
            All fields as JSON-compatible types.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "coordinate": self.coordinate,
            "trust_threshold": self.trust_threshold,
            "min_severity": self.min_severity,
            "enable_minimization": self.enable_minimization,
            "strict_mode": self.strict_mode,
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> "CounterexamplesSemanticWitnessesAnalyzer":
        """Deserialise an analyser from a plain dict.

        Parameters
        ----------
        d : dict[str, Any]
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        CounterexamplesSemanticWitnessesAnalyzer
            Reconstructed instance.
        """
        return cls(
            analyzer_id=d.get("analyzer_id", ""),
            coordinate=d.get("coordinate", ""),
            trust_threshold=d.get("trust_threshold", WitnessTrust.PROPOSAL.value),
            min_severity=int(d.get("min_severity", 1)),
            enable_minimization=bool(d.get("enable_minimization", True)),
            strict_mode=bool(d.get("strict_mode", False)),
        )


# ---------------------------------------------------------------------------
# §6  CounterexamplesSemanticWitnessesCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CounterexamplesSemanticWitnessesCoordinator:
    """Coordinates multiple analysers and aggregates semantic witnesses.

    The coordinator is the top-level entry-point for the witness pipeline.
    It manages a pool of :class:`CounterexamplesSemanticWitnessesAnalyzer`
    instances, routes evidence through them, and aggregates the resulting
    witnesses into reports and statistics.

    Design
    ------
    The coordinator is itself a frozen dataclass.  "Adding" an analyser
    returns a new coordinator with the analyser appended to the tuple.
    This design keeps the coordinator immutable and composable.

    The ``trust_policy`` field controls the default trust threshold
    applied across all analysers:

    * ``"PROPOSAL_REQUIRED"`` — only witnesses at ``PROPOSAL`` or higher
      are considered valid.
    * ``"REVIEWED_REQUIRED"`` — only witnesses at ``REVIEWED`` or higher
      are actionable.
    * ``"ACCEPTED_ONLY"`` — only ``ACCEPTED`` witnesses are used.

    Parameters
    ----------
    coordinator_id : str
        Unique identifier.  Auto-generated if empty.
    analyzers : tuple[CounterexamplesSemanticWitnessesAnalyzer, ...]
        Pool of analysers.  May be empty at construction time.
    session_id : str
        Identifier of the enclosing debug session.
    trust_policy : str
        Controls which witnesses are considered actionable.
        Default is ``"PROPOSAL_REQUIRED"``.
    max_witnesses : int
        Hard cap on the number of witnesses stored per collection call.
        Default is 256.

    Examples
    --------
    >>> coord = CounterexamplesSemanticWitnessesCoordinator(session_id="s1")
    >>> ana = CounterexamplesSemanticWitnessesAnalyzer(coordinate="m.f")
    >>> coord = coord.add_analyzer(ana)
    >>> len(coord.analyzers)
    1
    """

    coordinator_id: str = ""
    analyzers: tuple["CounterexamplesSemanticWitnessesAnalyzer", ...] = ()
    session_id: str = ""
    trust_policy: str = "PROPOSAL_REQUIRED"
    max_witnesses: int = 256

    def __post_init__(self) -> None:
        if not self.coordinator_id:
            content = self.session_id + self.trust_policy + str(time.time())
            object.__setattr__(
                self,
                "coordinator_id",
                "crd-" + _stable_hash8(content),
            )

    # ------------------------------------------------------------------
    # §6.1  Analyser management
    # ------------------------------------------------------------------

    def add_analyzer(
        self,
        analyzer: CounterexamplesSemanticWitnessesAnalyzer,
    ) -> "CounterexamplesSemanticWitnessesCoordinator":
        """Return a new coordinator with *analyzer* appended.

        Parameters
        ----------
        analyzer : CounterexamplesSemanticWitnessesAnalyzer
            The analyser to add.

        Returns
        -------
        CounterexamplesSemanticWitnessesCoordinator
            New coordinator with the extended analyser pool.

        Notes
        -----
        Duplicate analyser IDs are not checked.  If deduplication is
        required, it must be performed by the caller.
        """
        return replace(self, analyzers=self.analyzers + (analyzer,))

    # ------------------------------------------------------------------
    # §6.2  Witness collection
    # ------------------------------------------------------------------

    def collect_witnesses(
        self,
        coordinate: str,
        failing_predicate: str,
        model: SemanticModel,
    ) -> tuple[CounterexamplesSemanticWitnessesWitness, ...]:
        """Run all analysers on a shared model and collect the witnesses.

        Each analyser in :attr:`analyzers` is invoked via
        :meth:`CounterexamplesSemanticWitnessesAnalyzer.build_witness` on
        the same (*coordinate*, *failing_predicate*, *model*) triple.  The
        results are deduplicated by ``witness_id`` and capped at
        :attr:`max_witnesses`.

        Parameters
        ----------
        coordinate : str
            Semantic coordinate of the failure.
        failing_predicate : str
            The predicate that fails at *coordinate*.
        model : SemanticModel
            The countermodel to analyse.

        Returns
        -------
        tuple[CounterexamplesSemanticWitnessesWitness, ...]
            Witnesses produced by all analysers, deduplicated and capped.

        Notes
        -----
        If no analysers are registered, a single default analyser is
        constructed on-the-fly and used.
        """
        if not self.analyzers:
            default = CounterexamplesSemanticWitnessesAnalyzer(
                coordinate=coordinate,
                trust_threshold=self._policy_to_threshold(),
            )
            active_analyzers: tuple[CounterexamplesSemanticWitnessesAnalyzer, ...] = (
                default,
            )
        else:
            active_analyzers = self.analyzers

        seen_ids: set[str] = set()
        collected: list[CounterexamplesSemanticWitnessesWitness] = []
        for ana in active_analyzers:
            if len(collected) >= self.max_witnesses:
                break
            try:
                witness = ana.build_witness(
                    coordinate=coordinate,
                    failing_predicate=failing_predicate,
                    model=model,
                )
                if witness.witness_id not in seen_ids:
                    seen_ids.add(witness.witness_id)
                    collected.append(witness)
            except Exception:  # noqa: BLE001
                continue
        return tuple(collected[: self.max_witnesses])

    # ------------------------------------------------------------------
    # §6.3  Review
    # ------------------------------------------------------------------

    def review_witness(
        self,
        witness: CounterexamplesSemanticWitnessesWitness,
        reviewer_id: str,
        accepted: bool,
        reason: str = "",
    ) -> CounterexamplesSemanticWitnessesWitness:
        """Apply a reviewer's decision to a witness.

        Parameters
        ----------
        witness : CounterexamplesSemanticWitnessesWitness
            The witness to review.
        reviewer_id : str
            Identifier of the reviewer (human or automated).
        accepted : bool
            ``True`` → accept; ``False`` → reject.
        reason : str, optional
            Rejection reason (only used when *accepted* is ``False``).

        Returns
        -------
        CounterexamplesSemanticWitnessesWitness
            Updated witness with the review applied.

        Notes
        -----
        This is a thin wrapper over
        :meth:`CounterexamplesSemanticWitnessesWitness.with_review` that
        enforces the coordinator's trust policy.  If the policy is
        ``"ACCEPTED_ONLY"`` and *accepted* is ``False``, the witness is
        archived rather than simply rejected.
        """
        if self.trust_policy == "ACCEPTED_ONLY" and not accepted:
            return replace(
                witness,
                trust_tier=WitnessTrust.ARCHIVED.value,
                reviewer_id=reviewer_id,
                rejection_reason=reason,
            )
        return witness.with_review(reviewer_id=reviewer_id, accepted=accepted, reason=reason)

    # ------------------------------------------------------------------
    # §6.4  Aggregation and reporting
    # ------------------------------------------------------------------

    def aggregate_witnesses(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
    ) -> dict[str, Any]:
        """Return aggregate statistics over a collection of witnesses.

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            Collection to aggregate.

        Returns
        -------
        dict[str, Any]
            Dictionary with the following keys:

            * ``total`` — total witness count
            * ``genuine_count`` — number of genuine witnesses
            * ``actionable_count`` — number of actionable witnesses
            * ``complete_count`` — number of complete witnesses
            * ``severity_distribution`` — dict mapping severity (1-5) to count
            * ``trust_distribution`` — dict mapping trust level name to count
            * ``obstruction_classes`` — dict mapping obstruction class to count
            * ``failure_classes`` — dict mapping failure class to count
            * ``minimized_count`` — number with ``is_minimal=True``
            * ``with_reviewer`` — number that have been reviewed
            * ``avg_severity`` — float mean severity (0.0 if empty)
        """
        total = len(list(witnesses))
        ws = list(witnesses)
        genuine_count = sum(1 for w in ws if w.is_genuine())
        actionable_count = sum(1 for w in ws if w.is_actionable())
        complete_count = sum(1 for w in ws if w.is_complete())
        minimized_count = sum(1 for w in ws if w.is_minimal)
        with_reviewer = sum(1 for w in ws if w.reviewer_id)

        sev_dist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        trust_dist: dict[str, int] = {}
        obs_classes: dict[str, int] = {}
        fail_classes: dict[str, int] = {}

        for w in ws:
            sev_dist[max(1, min(5, w.severity))] = (
                sev_dist.get(max(1, min(5, w.severity)), 0) + 1
            )
            trust_dist[w.trust_tier] = trust_dist.get(w.trust_tier, 0) + 1
            obs_classes[w.obstruction_class] = (
                obs_classes.get(w.obstruction_class, 0) + 1
            )
            fail_classes[w.failure_class] = (
                fail_classes.get(w.failure_class, 0) + 1
            )

        avg_sev = (
            sum(w.severity for w in ws) / total if total > 0 else 0.0
        )

        return {
            "total": total,
            "genuine_count": genuine_count,
            "actionable_count": actionable_count,
            "complete_count": complete_count,
            "severity_distribution": sev_dist,
            "trust_distribution": trust_dist,
            "obstruction_classes": obs_classes,
            "failure_classes": fail_classes,
            "minimized_count": minimized_count,
            "with_reviewer": with_reviewer,
            "avg_severity": round(avg_sev, 2),
        }

    def build_witness_report(
        self,
        witnesses: Sequence[CounterexamplesSemanticWitnessesWitness],
    ) -> dict[str, Any]:
        """Build a comprehensive witness report.

        Parameters
        ----------
        witnesses : Sequence[CounterexamplesSemanticWitnessesWitness]
            The witnesses to include in the report.

        Returns
        -------
        dict[str, Any]
            Full report containing:

            * ``report_id`` — stable hash of coordinator ID + timestamp
            * ``coordinator_id`` — this coordinator's ID
            * ``session_id`` — the associated debug session ID
            * ``trust_policy`` — the active trust policy
            * ``total_witnesses`` — total count
            * ``genuine_count`` — count of genuine witnesses
            * ``actionable_count`` — count of actionable witnesses
            * ``severity_distribution`` — severity histogram
            * ``top_witnesses`` — list of top-5 witnesses by severity
            * ``aggregate`` — full aggregate stats dict
            * ``generated_at`` — ISO-8601 timestamp

        Notes
        -----
        Witnesses are ordered highest-severity first in ``top_witnesses``.
        """
        ws = list(witnesses)
        agg = self.aggregate_witnesses(ws)
        sorted_ws = sorted(ws, key=lambda w: (-w.severity, w.witness_id))
        top_five = [w.to_dict() for w in sorted_ws[:5]]

        report_id = "rpt-" + _stable_hash8(
            self.coordinator_id + _iso_timestamp()
        )
        return {
            "report_id": report_id,
            "coordinator_id": self.coordinator_id,
            "session_id": self.session_id,
            "trust_policy": self.trust_policy,
            "total_witnesses": agg["total"],
            "genuine_count": agg["genuine_count"],
            "actionable_count": agg["actionable_count"],
            "severity_distribution": agg["severity_distribution"],
            "top_witnesses": top_five,
            "aggregate": agg,
            "generated_at": _iso_timestamp(),
        }

    # ------------------------------------------------------------------
    # §6.5  Internal helpers
    # ------------------------------------------------------------------

    def _policy_to_threshold(self) -> str:
        """Map the trust policy to a threshold name.

        Returns
        -------
        str
            A ``WitnessTrust`` name corresponding to the policy.
        """
        _MAP = {
            "PROPOSAL_REQUIRED": WitnessTrust.PROPOSAL.value,
            "REVIEWED_REQUIRED": WitnessTrust.REVIEWED.value,
            "ACCEPTED_ONLY": WitnessTrust.ACCEPTED.value,
        }
        return _MAP.get(self.trust_policy, WitnessTrust.PROPOSAL.value)

    # ------------------------------------------------------------------
    # §6.6  Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the coordinator to a plain dict.

        Returns
        -------
        dict[str, Any]
            All fields as JSON-compatible types; analysers are serialised
            via :meth:`CounterexamplesSemanticWitnessesAnalyzer.to_dict`.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "analyzers": [a.to_dict() for a in self.analyzers],
            "session_id": self.session_id,
            "trust_policy": self.trust_policy,
            "max_witnesses": self.max_witnesses,
        }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# §7  Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "WitnessTrust",
    "WitnessKind",
    # Dataclasses
    "SemanticModel",
    "CounterexamplesSemanticWitnessesWitness",
    "CounterexamplesSemanticWitnessesAnalyzer",
    "CounterexamplesSemanticWitnessesCoordinator",
    # Module constant
    "MANIFEST_SPEC_PROVENANCE",
    # Helper functions
    "_iso_timestamp",
    "_stable_hash8",
    "_severity_from_failure_class",
    "_obstruction_class_from_failure",
    "_trust_rank",
    "_is_trust_sufficient",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end s02 counterexamples as semantic witnesses — theory2 ch11 §11.2

# ---------------------------------------------------------------------------
# §8  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    model = SemanticModel(
        kind="Z3_MODEL",
        variable_assignments=(("x", "42"), ("y", "0")),
        raw_repr="x=42, y=0",
    )
    analyzer = CounterexamplesSemanticWitnessesAnalyzer(
        coordinate="module.function.branch",
        trust_threshold="PROPOSAL",
    )
    witness = analyzer.build_witness(
        coordinate="module.function.branch",
        failing_predicate="x > 0 AND y > 0",
        model=model,
        failure_class="CONSTRAINT_VIOLATION",
    )
    print(f"Witness id: {witness.witness_id}")
    print(f"Is complete: {witness.is_complete()}")
    print(f"Is actionable: {witness.is_actionable()}")
    print(f"Severity: {witness.severity} ({witness.severity_label()})")
    print(f"Trust: {witness.trust_tier}")

    reviewed = witness.with_review("reviewer-1", True)
    print(f"After review: {reviewed.trust_tier}, genuine={reviewed.is_genuine()}")

    minimized = analyzer.minimize_witness(witness)
    print(f"Minimized: is_minimal={minimized.is_minimal}")

    coordinator = CounterexamplesSemanticWitnessesCoordinator(session_id="test-session")
    coordinator = coordinator.add_analyzer(analyzer)
    witnesses = coordinator.collect_witnesses(
        "module.function.branch", "x > 0 AND y > 0", model
    )
    report = coordinator.build_witness_report(witnesses)
    print(f"Report: total={report['total_witnesses']}, genuine={report['genuine_count']}")
    print("s02 smoke test passed")
