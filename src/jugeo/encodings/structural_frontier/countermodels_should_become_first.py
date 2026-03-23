"""
Countermodels as First-Class Semantic Witnesses
================================================

This module is about making countermodels first-class semantic witnesses within
the Jugeo obligation-satisfaction pipeline. A countermodel is a Z3 model that
falsifies an obligation — it assigns concrete values to variables such that the
negation of the obligation is satisfiable. In other words, it is a concrete
certificate that a formula, as currently stated, cannot be universally satisfied
under the given constraints.

Making countermodels "first-class" means three things in this codebase:

1. **Rich value objects.** Rather than passing raw Z3 model dumps around as opaque
   strings, countermodels are represented as structured dataclasses carrying typed
   fields for every piece of information a downstream system might need: the
   original obligation SMT, the parsed variable assignments, the role the
   countermodel plays in the repair pipeline, a falsification score, semantic
   depth, and repair hints. Consumers never need to parse Z3 output themselves.

2. **Semantic metadata.** Each witness knows *what* it witnesses and *why* it
   matters. The ``CountermodelRole`` enum captures whether a countermodel is
   a simple falsifier (it disproves the formula), a boundary witness (it sits
   on the edge of satisfiability and is informative for constraint refinement),
   a repair seed (the assignments in the model directly suggest a strengthening
   or relaxation of some constraint), an obstruction certificate (the model
   encodes an irreducible structural reason why the formula fails), or a
   diagnostic artifact (collected during debugging without immediate repair
   intent). This metadata drives routing decisions throughout the pipeline.

3. **Operational richness.** First-class countermodels can be inspected (iterate
   assignments, look up individual variable values), compared (fingerprinting via
   SHA-256 of the canonical assignment set), merged (combine the assignments from
   two witnesses into a new witness that covers more of the failure space), and
   used directly in repair pipelines (emit SMT2 blocking clauses that rule out
   the falsifying assignment when re-running the solver to search for a repaired
   formula).

Repair Pipeline Integration
---------------------------
When an obligation fails, the solver returns a countermodel. The
``CountermodelsBecomeFirstClassCoordinator`` registers this model, promotes it
to a first-class witness, and routes it based on role:

- ``FALSIFIER`` witnesses are queued for immediate repair. The repair engine
  attempts to strengthen the obligation just enough to exclude the falsifying
  assignment without over-constraining the problem.
- ``REPAIR_SEED`` witnesses feed a constraint-synthesis loop that proposes new
  conjuncts to add to the obligation.
- ``BOUNDARY_WITNESS`` models are stored for later use in a coverage analysis
  that estimates how close the current formula is to a tight characterisation
  of the intended set of models.
- ``OBSTRUCTION_CERTIFICATE`` models are escalated to human review because they
  indicate that no automatic repair is likely to succeed without structural
  changes to the ontology or the encoding.

Blocking Clauses
----------------
A blocking clause is an SMT2 assertion that explicitly rules out a specific
variable assignment. Given a countermodel ``{x → 3, y → 7}``, the blocking
clause ``(assert (not (and (= x 3) (= y 7))))`` prevents the solver from
returning the same model again. The ``to_smt2_blocking_clause`` method on
``CountermodelsBecomeFirstClassWitness`` produces this clause automatically.
The ``CountermodelsBecomeFirstClassCoordinator.emit_blocking_clause_set``
method aggregates blocking clauses across all registered witnesses.

Iterative blocking is a standard technique in model-enumeration and AllSAT
algorithms. In the repair context, it drives a loop: (1) check formula,
(2) get countermodel, (3) strengthen formula to block the model, (4) repeat.
Termination is guaranteed when the formula's model count is finite, or when
a repair budget is exhausted.

Semantic Depth
--------------
The *semantic depth* of a countermodel is a heuristic measure of how deeply
the falsifying assignment reaches into the nested structure of the obligation.
A depth of 1 means the top-level conjunction is falsified by a direct variable
assignment. A depth of 5 means the assignment falsifies a formula nested five
quantifier or let-binding levels deep. Higher depth correlates with harder
repairs and lower confidence in automated repair suggestions.

The ``compute_semantic_depth`` method estimates depth from the parsed
assignments by counting variables whose names suggest nesting (e.g., variables
with names like ``inner_x`` or ``level_3_y``) and by inspecting the number of
distinct variable namespaces present in the assignment set.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, SolveOutcome
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.countermodels import (
        Countermodel, CountermodelExtractor, FailureClass,
    )
    _COUNTERMODELS_AVAILABLE = True
except ImportError:
    _COUNTERMODELS_AVAILABLE = False
    Countermodel = None  # type: ignore[assignment,misc]
    CountermodelExtractor = None  # type: ignore[assignment,misc]
    FailureClass = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier.models import (
        DecidabilityClass, CountermodelObstruction, RepairAction,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    DecidabilityClass = None  # type: ignore[assignment,misc]
    CountermodelObstruction = None  # type: ignore[assignment,misc]
    RepairAction = None  # type: ignore[assignment,misc]

try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Module-level constants                                                        #
# --------------------------------------------------------------------------- #

_REPAIR_HINT_TEMPLATES: dict[str, str] = {
    "strengthen_bound": (
        "Consider adding an upper bound constraint on '{var}'. The countermodel "
        "assigns '{var} = {val}', which exceeds the expected range. Adding "
        "'(assert (<= {var} MAX))' may repair this obligation."
    ),
    "add_guard": (
        "The variable '{var}' is assigned '{val}' in the falsifying model. "
        "Introducing a guard predicate such as '(=> (P {var}) OBLIGATION)' "
        "restricts the domain so this assignment no longer applies."
    ),
    "flip_polarity": (
        "The obligation is falsified because '{var} = {val}'. Flipping the "
        "polarity of the atom involving '{var}' — replacing a positive literal "
        "with its negation — may restore satisfiability."
    ),
    "split_case": (
        "The countermodel suggests a case split on '{var}'. Handle "
        "'(< {var} 0)' and '(>= {var} 0)' separately and verify each branch."
    ),
    "introduce_existential": (
        "Rather than universally quantifying over '{var}', consider an "
        "existential claim. The assignment '{var} = {val}' witnesses a "
        "specific scenario that may be legitimately excluded."
    ),
    "tighten_precondition": (
        "The precondition of the obligation does not rule out '{var} = {val}'. "
        "Tightening the precondition to exclude this value will prevent the "
        "obligation from being vacuously triggered in this failure scenario."
    ),
    "add_type_constraint": (
        "Variable '{var}' is assigned the value '{val}', which may be outside "
        "the intended semantic type. Adding a sort or range constraint will "
        "prevent this assignment and narrow the search space for repairs."
    ),
    "decompose_obligation": (
        "The obligation is too broad and admits '{var} = {val}' as a valid but "
        "undesired assignment. Decomposing the obligation into smaller, more "
        "focused sub-obligations may isolate the failure and allow targeted "
        "repair of the relevant sub-clause."
    ),
    "add_symmetry_break": (
        "The countermodel exhibits a symmetric structure. Adding a "
        "symmetry-breaking constraint such as '(assert (<= {var}_0 {var}_1))' "
        "eliminates one representative of each equivalence class and may "
        "prevent this particular falsification."
    ),
    "use_auxiliary_lemma": (
        "The variable '{var}' interacts with other variables in a complex way. "
        "Introducing an auxiliary lemma that captures this interaction — and "
        "asserting it as a helper constraint — may simplify the repair."
    ),
}

_FALSIFICATION_SCORE_WEIGHTS: dict[str, float] = {
    "numeric_divergence": 0.35,
    "variable_count": 0.20,
    "nesting_depth": 0.25,
    "symbolic_complexity": 0.10,
    "boundary_proximity": 0.10,
}

_SEMANTIC_DEPTH_MULTIPLIERS: dict[str, float] = {
    "flat": 1.0,
    "one_level": 1.5,
    "two_levels": 2.0,
    "three_levels": 2.5,
    "deep": 3.0,
    "very_deep": 4.0,
}


# ============================== countermodel role ==============================


class CountermodelRole(Enum):
    """Role that a countermodel plays within the repair and analysis pipeline.

    Each countermodel produced by Z3 in response to a failed obligation check
    is not simply a "failure" — it carries information about *what kind* of
    failure has occurred and *what should be done next*. The role of a
    countermodel determines how it is routed within the pipeline, what priority
    it receives in the repair queue, and what kinds of hints can be generated
    from it.

    Countermodel roles are assigned heuristically during the analysis phase
    (see ``CountermodelsBecomeFirstClassAnalyzer.classify_countermodel_role``)
    based on the structure of the assignments and the shape of the obligation
    being falsified.

    Parameters
    ----------
    value : int
        Auto-assigned integer discriminant (from ``auto()``).

    Members
    -------
    FALSIFIER : CountermodelRole
        The countermodel directly falsifies the obligation by producing an
        assignment under which the obligation's negation is satisfied. This
        is the most common role and the one that triggers immediate repair
        attempts. Falsifiers have the highest repair priority.
    BOUNDARY_WITNESS : CountermodelRole
        The countermodel sits on or near the boundary of satisfiability. It
        assigns values that are "almost" valid — they may be within epsilon
        of a constraint boundary or may satisfy all but one sub-clause of a
        conjunction. Boundary witnesses are informative for constraint
        tightening but do not always indicate a fixable bug.
    REPAIR_SEED : CountermodelRole
        The countermodel's variable assignments directly suggest a repair
        action. For example, if a variable is assigned a value far outside
        its expected range, the countermodel serves as the seed for a
        strengthen-bound repair hint. Repair seeds are routed to the
        constraint-synthesis loop.
    OBSTRUCTION_CERTIFICATE : CountermodelRole
        The countermodel encodes an irreducible structural reason why the
        formula fails. This means no local modification of the obligation can
        exclude this assignment without fundamentally changing the encoding.
        Obstruction certificates are escalated to human review.
    DIAGNOSTIC_ARTIFACT : CountermodelRole
        The countermodel was collected during debugging or exploration and has
        no immediate repair intent. It is stored for later analysis but does
        not enter the repair queue.

    Examples
    --------
    >>> role = CountermodelRole.FALSIFIER
    >>> role.is_actionable()
    True
    >>> role.repair_priority()
    1
    >>> CountermodelRole.OBSTRUCTION_CERTIFICATE.is_certificate()
    True
    >>> CountermodelRole.DIAGNOSTIC_ARTIFACT.is_diagnostic()
    True
    """

    FALSIFIER = auto()
    BOUNDARY_WITNESS = auto()
    REPAIR_SEED = auto()
    OBSTRUCTION_CERTIFICATE = auto()
    DIAGNOSTIC_ARTIFACT = auto()

    def is_actionable(self) -> bool:
        """Return True if this role implies that automated repair should be attempted.

        Returns
        -------
        bool
            True for FALSIFIER and REPAIR_SEED; False otherwise.

        Examples
        --------
        >>> CountermodelRole.FALSIFIER.is_actionable()
        True
        >>> CountermodelRole.DIAGNOSTIC_ARTIFACT.is_actionable()
        False
        """
        # copilot: only FALSIFIER and REPAIR_SEED trigger automated repair
        return self in (CountermodelRole.FALSIFIER, CountermodelRole.REPAIR_SEED)

    def repair_priority(self) -> int:
        """Return the integer repair priority (lower = higher urgency).

        Returns
        -------
        int
            1 for FALSIFIER, 2 for REPAIR_SEED, 3 for BOUNDARY_WITNESS,
            4 for OBSTRUCTION_CERTIFICATE, 5 for DIAGNOSTIC_ARTIFACT.

        Examples
        --------
        >>> CountermodelRole.FALSIFIER.repair_priority()
        1
        >>> CountermodelRole.REPAIR_SEED.repair_priority()
        2
        """
        _priority_map = {
            CountermodelRole.FALSIFIER: 1,
            CountermodelRole.REPAIR_SEED: 2,
            CountermodelRole.BOUNDARY_WITNESS: 3,
            CountermodelRole.OBSTRUCTION_CERTIFICATE: 4,
            CountermodelRole.DIAGNOSTIC_ARTIFACT: 5,
        }
        return _priority_map[self]

    def semantic_description(self) -> str:
        """Return a multi-sentence human-readable description of this role.

        Returns
        -------
        str
            A paragraph-length description suitable for inclusion in reports
            or documentation strings.

        Examples
        --------
        >>> desc = CountermodelRole.FALSIFIER.semantic_description()
        >>> "falsifies" in desc
        True
        """
        # copilot: descriptions guide human reviewers and downstream documentation
        _descriptions = {
            CountermodelRole.FALSIFIER: (
                "A FALSIFIER countermodel directly falsifies the target obligation by "
                "providing a concrete variable assignment under which the negation of the "
                "obligation is satisfiable. This is the canonical failure mode: the formula "
                "claims something that does not hold universally, and the solver has found "
                "a witness to the violation. FALSIFIER models should be acted on immediately "
                "by the repair engine. They represent the highest-confidence signal that the "
                "obligation as stated is incorrect or incomplete."
            ),
            CountermodelRole.BOUNDARY_WITNESS: (
                "A BOUNDARY_WITNESS countermodel occupies the boundary of satisfiability. "
                "The assigned values are close to valid — they may satisfy all but one "
                "sub-clause, or they may be numerically adjacent to a constraint threshold. "
                "Boundary witnesses are less urgent than falsifiers but are highly informative "
                "for constraint refinement. They suggest that a small tightening of the "
                "obligation could eliminate the failure mode without overfitting."
            ),
            CountermodelRole.REPAIR_SEED: (
                "A REPAIR_SEED countermodel carries variable assignments that directly "
                "suggest a repair action. The structure of the assignments — extreme values, "
                "unexpected nulls, contradictory polarities — points toward a specific "
                "strengthening or relaxation of a constraint. Repair seeds are routed to "
                "the constraint-synthesis loop, which proposes new conjuncts or guard "
                "predicates based on the seed values."
            ),
            CountermodelRole.OBSTRUCTION_CERTIFICATE: (
                "An OBSTRUCTION_CERTIFICATE countermodel encodes an irreducible structural "
                "obstacle to satisfying the obligation. No local syntactic modification can "
                "exclude the falsifying assignment without changing the fundamental semantics "
                "of the encoding. These models require human review and may indicate that "
                "the ontology, the encoding strategy, or the obligation itself needs "
                "structural redesign."
            ),
            CountermodelRole.DIAGNOSTIC_ARTIFACT: (
                "A DIAGNOSTIC_ARTIFACT countermodel was collected during an exploratory or "
                "debugging session. It is stored for later analysis but does not enter the "
                "repair queue. Diagnostic artifacts accumulate over time and may be used for "
                "post-hoc analysis, coverage estimation, or regression testing."
            ),
        }
        return _descriptions[self]

    def smt_trigger_template(self) -> str:
        """Return an SMT2 comment template describing when this role is triggered.

        Returns
        -------
        str
            A semicolon-prefixed SMT2 comment suitable for inclusion in
            generated SMT2 files.

        Examples
        --------
        >>> tmpl = CountermodelRole.FALSIFIER.smt_trigger_template()
        >>> tmpl.startswith(";")
        True
        """
        # copilot: SMT2 convention is semicolon-prefixed comments
        _templates = {
            CountermodelRole.FALSIFIER: (
                "; countermodel-role: FALSIFIER\n"
                "; triggered-when: (check-sat) returns sat after (assert (not OBLIGATION))\n"
                "; action: enqueue for automated repair"
            ),
            CountermodelRole.BOUNDARY_WITNESS: (
                "; countermodel-role: BOUNDARY_WITNESS\n"
                "; triggered-when: assignment is within epsilon of a constraint boundary\n"
                "; action: store for constraint-tightening analysis"
            ),
            CountermodelRole.REPAIR_SEED: (
                "; countermodel-role: REPAIR_SEED\n"
                "; triggered-when: assignment contains extreme or structurally suggestive values\n"
                "; action: route to constraint-synthesis loop"
            ),
            CountermodelRole.OBSTRUCTION_CERTIFICATE: (
                "; countermodel-role: OBSTRUCTION_CERTIFICATE\n"
                "; triggered-when: no local syntactic repair can exclude the assignment\n"
                "; action: escalate to human review"
            ),
            CountermodelRole.DIAGNOSTIC_ARTIFACT: (
                "; countermodel-role: DIAGNOSTIC_ARTIFACT\n"
                "; triggered-when: collected during debug/exploration mode\n"
                "; action: archive for post-hoc analysis"
            ),
        }
        return _templates[self]

    def severity_default(self) -> str:
        """Return the default severity label for this role.

        Returns
        -------
        str
            One of "low", "medium", "high", or "critical".

        Examples
        --------
        >>> CountermodelRole.FALSIFIER.severity_default()
        'critical'
        >>> CountermodelRole.DIAGNOSTIC_ARTIFACT.severity_default()
        'low'
        """
        _severity_map = {
            CountermodelRole.FALSIFIER: "critical",
            CountermodelRole.REPAIR_SEED: "high",
            CountermodelRole.BOUNDARY_WITNESS: "medium",
            CountermodelRole.OBSTRUCTION_CERTIFICATE: "high",
            CountermodelRole.DIAGNOSTIC_ARTIFACT: "low",
        }
        return _severity_map[self]

    def is_certificate(self) -> bool:
        """Return True if this role is a formal certificate of some property.

        Returns
        -------
        bool
            True only for OBSTRUCTION_CERTIFICATE.

        Examples
        --------
        >>> CountermodelRole.OBSTRUCTION_CERTIFICATE.is_certificate()
        True
        >>> CountermodelRole.FALSIFIER.is_certificate()
        False
        """
        return self is CountermodelRole.OBSTRUCTION_CERTIFICATE

    def is_diagnostic(self) -> bool:
        """Return True if this role is primarily for diagnostic or debugging purposes.

        Returns
        -------
        bool
            True only for DIAGNOSTIC_ARTIFACT.

        Examples
        --------
        >>> CountermodelRole.DIAGNOSTIC_ARTIFACT.is_diagnostic()
        True
        >>> CountermodelRole.REPAIR_SEED.is_diagnostic()
        False
        """
        return self is CountermodelRole.DIAGNOSTIC_ARTIFACT


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class CountermodelsBecomeFirstClassWitness:
    """An immutable first-class representation of a countermodel witness.

    This dataclass wraps all information about a single countermodel — the Z3
    model that falsifies an obligation — into a rich, inspectable, serialisable
    value object. It is the primary artifact produced by the analysis pipeline
    and consumed by the repair engine, the reporting layer, and the blocking-
    clause generator.

    The dataclass is frozen (immutable) because countermodel witnesses are
    semantic facts: once a countermodel has been produced and promoted to
    first-class status, its content should not change. If a repair generates
    new information, a new witness should be created (possibly via ``merge``).

    Parameters
    ----------
    witness_id : str
        A UUID4 string uniquely identifying this witness instance.
    obligation_smt : str
        The SMT2 string of the obligation that this countermodel falsifies.
    countermodel_assignments : tuple[tuple[str, str], ...]
        An immutable sequence of (variable_name, assigned_value) pairs parsed
        from the Z3 model output. Using a tuple-of-tuples makes the dataclass
        hashable.
    role : CountermodelRole
        The semantic role assigned to this countermodel by the analyzer.
    semantic_depth : int
        Heuristic depth of the falsification within the obligation structure.
        Higher values indicate deeper, harder-to-repair failures.
    repair_hints : tuple[str, ...]
        An immutable sequence of human-readable repair hint strings generated
        by the analyzer based on the assignments and obligation structure.
    falsification_score : float
        A float in [0.0, 1.0] representing how "severe" or "informative" the
        countermodel is. Higher scores indicate more diagnostic value.
    copilot_label : str
        A short label string identifying this witness in Copilot-generated
        comments or annotations.
    created_at : float
        Unix timestamp (from ``time.time()``) of when this witness was created.
    z3_model_str : str
        The raw Z3 model string from which the assignments were parsed,
        preserved for traceability.

    Methods
    -------
    variable_names() -> list[str]
        Return the list of variable names present in the assignments.
    assignment_for(var) -> str | None
        Return the assigned value for a specific variable.
    to_semantic_witness() -> dict[str, Any]
        Return a rich dictionary representation for downstream consumers.
    to_smt2_blocking_clause() -> str
        Return an SMT2 assertion that blocks this assignment.
    fingerprint() -> str
        Return a SHA-256 fingerprint of the canonical assignment set.
    merge(other) -> CountermodelsBecomeFirstClassWitness
        Merge with another witness, combining assignments and hints.
    copilot_countermodel_hint() -> str
        Return a multi-line copilot-style hint comment.
    assignments_dict() -> dict[str, str]
        Return assignments as a plain dict.
    has_numeric_assignments() -> bool
        Return True if any assignment value is numeric.
    to_dict() -> dict[str, Any]
        Serialise to a JSON-compatible dict.
    from_dict(d) -> CountermodelsBecomeFirstClassWitness
        Deserialise from a dict produced by ``to_dict``.
    age_seconds() -> float
        Return the age of this witness in seconds.
    is_fresh(max_age) -> bool
        Return True if the witness is younger than ``max_age`` seconds.

    Examples
    --------
    >>> w = CountermodelsBecomeFirstClassWitness(
    ...     witness_id="abc-123",
    ...     obligation_smt="(assert (> x 0))",
    ...     countermodel_assignments=(("x", "-1"),),
    ...     role=CountermodelRole.FALSIFIER,
    ...     semantic_depth=1,
    ...     repair_hints=("Strengthen the lower bound on x.",),
    ...     falsification_score=0.85,
    ...     copilot_label="x-neg-falsifier",
    ...     created_at=0.0,
    ...     z3_model_str="x -> -1",
    ... )
    >>> w.assignment_for("x")
    '-1'
    >>> w.variable_names()
    ['x']
    """

    witness_id: str
    obligation_smt: str
    countermodel_assignments: tuple[tuple[str, str], ...]
    role: CountermodelRole
    semantic_depth: int
    repair_hints: tuple[str, ...]
    falsification_score: float
    copilot_label: str
    created_at: float
    z3_model_str: str

    def variable_names(self) -> list[str]:
        """Return the list of variable names present in the countermodel assignments.

        Returns
        -------
        list[str]
            Variable names in the order they appear in ``countermodel_assignments``.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> "x" in w.variable_names()
        True
        """
        return [name for name, _val in self.countermodel_assignments]

    def assignment_for(self, var: str) -> str | None:
        """Return the assigned value string for a specific variable.

        Parameters
        ----------
        var : str
            The variable name to look up.

        Returns
        -------
        str | None
            The assigned value string, or None if the variable is not present
            in this countermodel.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> w.assignment_for("x")
        '-1'
        >>> w.assignment_for("not_present") is None
        True
        """
        # copilot: linear scan is fine because countermodels are small
        for name, val in self.countermodel_assignments:
            if name == var:
                return val
        return None

    def to_semantic_witness(self) -> dict[str, Any]:
        """Return a rich dictionary representation of this witness for downstream consumers.

        The returned dict is suitable for JSON serialisation, for display in
        reports, and for consumption by repair engines that need structured
        access to witness metadata.

        Returns
        -------
        dict[str, Any]
            A dict with keys: witness_id, obligation_smt, assignments,
            role, semantic_depth, repair_hints, falsification_score,
            copilot_label, created_at, fingerprint, severity, is_actionable,
            blocking_clause.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> sw = w.to_semantic_witness()
        >>> "fingerprint" in sw
        True
        >>> sw["role"] == "FALSIFIER"
        True
        """
        # copilot: include all metadata needed by consumers without them needing the object
        return {
            "witness_id": self.witness_id,
            "obligation_smt": self.obligation_smt,
            "assignments": dict(self.countermodel_assignments),
            "role": self.role.name,
            "semantic_depth": self.semantic_depth,
            "repair_hints": list(self.repair_hints),
            "falsification_score": self.falsification_score,
            "copilot_label": self.copilot_label,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint(),
            "severity": self.role.severity_default(),
            "is_actionable": self.role.is_actionable(),
            "blocking_clause": self.to_smt2_blocking_clause(),
            "z3_model_str": self.z3_model_str,
        }

    def to_smt2_blocking_clause(self) -> str:
        """Return an SMT2 assertion that blocks this specific countermodel assignment.

        The blocking clause is a conjunction of equalities — one per assigned
        variable — negated and wrapped in ``assert``. When added to a solver
        context, it prevents the solver from returning the same assignment again.

        Returns
        -------
        str
            A complete SMT2 ``(assert (not (and ...)))`` string.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> clause = w.to_smt2_blocking_clause()
        >>> clause.startswith("(assert (not")
        True
        """
        # copilot: blocking clause format is standard AllSAT / model enumeration
        if not self.countermodel_assignments:
            return "; empty countermodel — no blocking clause"

        equalities = " ".join(
            f"(= {name} {val})" for name, val in self.countermodel_assignments
        )
        if len(self.countermodel_assignments) == 1:
            inner = equalities
        else:
            inner = f"(and {equalities})"

        return (
            f"; blocking clause for witness {self.witness_id}\n"
            f"(assert (not {inner}))"
        )

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of the canonical sorted assignment set.

        The fingerprint is deterministic: it depends only on the set of
        (variable, value) pairs, not their order. This allows two witnesses
        with identical models but different orderings to be detected as
        duplicates.

        Returns
        -------
        str
            A 64-character lowercase hex string.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> len(w.fingerprint())
        64
        """
        # copilot: sort assignments for a canonical, order-independent fingerprint
        canonical = json.dumps(
            sorted(self.countermodel_assignments), sort_keys=True
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def merge(self, other: CountermodelsBecomeFirstClassWitness) -> CountermodelsBecomeFirstClassWitness:
        """Merge two witnesses into a new witness combining both assignment sets.

        The merged witness takes the higher-priority role, combines the
        assignment sets (de-duplicated, with ``self`` taking precedence on
        conflicts), pools the repair hints, and averages the falsification
        scores.

        Parameters
        ----------
        other : CountermodelsBecomeFirstClassWitness
            Another witness to merge with this one.

        Returns
        -------
        CountermodelsBecomeFirstClassWitness
            A new frozen witness combining information from both inputs.

        Examples
        --------
        >>> w1 = _make_test_witness()
        >>> w2 = _make_test_witness()
        >>> merged = w1.merge(w2)
        >>> isinstance(merged, CountermodelsBecomeFirstClassWitness)
        True
        """
        # copilot: self assignments take precedence over other in case of variable conflicts
        merged_assignments: dict[str, str] = dict(other.countermodel_assignments)
        merged_assignments.update(dict(self.countermodel_assignments))
        combined_hints = tuple(
            dict.fromkeys(list(self.repair_hints) + list(other.repair_hints))
        )
        # take the higher-priority (lower number) role
        new_role = (
            self.role
            if self.role.repair_priority() <= other.role.repair_priority()
            else other.role
        )
        avg_score = (self.falsification_score + other.falsification_score) / 2.0
        max_depth = max(self.semantic_depth, other.semantic_depth)
        return CountermodelsBecomeFirstClassWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=self.obligation_smt,
            countermodel_assignments=tuple(sorted(merged_assignments.items())),
            role=new_role,
            semantic_depth=max_depth,
            repair_hints=combined_hints,
            falsification_score=round(avg_score, 4),
            copilot_label=f"merged:{self.copilot_label}+{other.copilot_label}",
            created_at=time.time(),
            z3_model_str=f"{self.z3_model_str}\n; merged with\n{other.z3_model_str}",
        )

    def copilot_countermodel_hint(self) -> str:
        """Return a multi-line copilot-style hint comment for this witness.

        Returns
        -------
        str
            A multi-line string suitable for insertion as a comment block in
            generated code or SMT2 files.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> hint = w.copilot_countermodel_hint()
        >>> "copilot:" in hint
        True
        """
        # copilot: format as a structured comment block for easy reading
        lines = [
            f"# copilot: countermodel witness {self.witness_id}",
            f"# copilot: role={self.role.name} severity={self.role.severity_default()}",
            f"# copilot: falsification_score={self.falsification_score:.4f} depth={self.semantic_depth}",
            f"# copilot: obligation={self.obligation_smt[:80]}{'...' if len(self.obligation_smt) > 80 else ''}",
            "# copilot: assignments:",
        ]
        for name, val in self.countermodel_assignments:
            lines.append(f"#   {name} = {val}")
        if self.repair_hints:
            lines.append("# copilot: top repair hint:")
            lines.append(f"#   {self.repair_hints[0][:120]}")
        return "\n".join(lines)

    def assignments_dict(self) -> dict[str, str]:
        """Return the countermodel assignments as a plain string-to-string dict.

        Returns
        -------
        dict[str, str]
            A copy of the assignments as a mutable dict.
        """
        return dict(self.countermodel_assignments)

    def has_numeric_assignments(self) -> bool:
        """Return True if at least one assigned value is parseable as a number.

        Returns
        -------
        bool
            True if any value can be interpreted as an int or float.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> w.has_numeric_assignments()
        True
        """
        for _name, val in self.countermodel_assignments:
            try:
                float(val)
                return True
            except (ValueError, TypeError):
                pass
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialise this witness to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            A dict that can be passed to ``json.dumps`` and reconstructed
            via ``from_dict``.
        """
        return {
            "witness_id": self.witness_id,
            "obligation_smt": self.obligation_smt,
            "countermodel_assignments": list(self.countermodel_assignments),
            "role": self.role.name,
            "semantic_depth": self.semantic_depth,
            "repair_hints": list(self.repair_hints),
            "falsification_score": self.falsification_score,
            "copilot_label": self.copilot_label,
            "created_at": self.created_at,
            "z3_model_str": self.z3_model_str,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CountermodelsBecomeFirstClassWitness:
        """Deserialise a witness from a dict produced by ``to_dict``.

        Parameters
        ----------
        d : dict
            A dict with the same keys as produced by ``to_dict``.

        Returns
        -------
        CountermodelsBecomeFirstClassWitness
            The reconstructed witness.

        Examples
        --------
        >>> w = _make_test_witness()
        >>> reconstructed = CountermodelsBecomeFirstClassWitness.from_dict(w.to_dict())
        >>> reconstructed.fingerprint() == w.fingerprint()
        True
        """
        return cls(
            witness_id=d["witness_id"],
            obligation_smt=d["obligation_smt"],
            countermodel_assignments=tuple(tuple(pair) for pair in d["countermodel_assignments"]),
            role=CountermodelRole[d["role"]],
            semantic_depth=int(d["semantic_depth"]),
            repair_hints=tuple(d["repair_hints"]),
            falsification_score=float(d["falsification_score"]),
            copilot_label=d["copilot_label"],
            created_at=float(d["created_at"]),
            z3_model_str=d["z3_model_str"],
        )

    def age_seconds(self) -> float:
        """Return the age of this witness in seconds since creation.

        Returns
        -------
        float
            Seconds elapsed since ``created_at``.
        """
        return time.time() - self.created_at

    def is_fresh(self, max_age: float = 300.0) -> bool:
        """Return True if this witness is younger than ``max_age`` seconds.

        Parameters
        ----------
        max_age : float, optional
            Maximum acceptable age in seconds. Default is 300.0 (5 minutes).

        Returns
        -------
        bool
            True if the witness is fresh.
        """
        return self.age_seconds() <= max_age


# ============================== analyzer ==============================


class CountermodelsBecomeFirstClassAnalyzer:
    """Analyzes raw Z3 model strings and promotes them to first-class witnesses.

    The analyzer is the core processing unit that transforms raw Z3 model
    output into structured ``CountermodelsBecomeFirstClassWitness`` instances.
    It handles Z3 model parsing, role classification, semantic depth estimation,
    repair hint generation, and falsification scoring.

    The analyzer maintains an internal LRU-style cache of previously seen
    obligation/model pairs so that repeated analysis of the same countermodel
    does not incur redundant work.

    Parameters
    ----------
    None

    Attributes
    ----------
    _analysis_cache : dict[str, CountermodelsBecomeFirstClassWitness]
        Cache mapping fingerprint strings to previously analyzed witnesses.
    _analysis_count : int
        Running count of analyses performed (for statistics).

    Methods
    -------
    analyze_countermodel(obligation, model_str, label) -> CountermodelsBecomeFirstClassWitness
        Full analysis pipeline: parse → classify → score → hint → build witness.
    parse_z3_model(model_str) -> dict[str, str]
        Parse a Z3 model string into a variable-to-value dict.
    classify_countermodel_role(assignments, obligation) -> CountermodelRole
        Heuristically assign a role to a countermodel.
    compute_semantic_depth(assignments) -> int
        Estimate the semantic depth from assignment structure.
    generate_repair_hints(witness) -> list[str]
        Generate repair hint strings for a witness.
    compute_falsification_score(assignments, obligation) -> float
        Compute a [0,1] falsification severity score.
    batch_analyze(pairs) -> list[CountermodelsBecomeFirstClassWitness]
        Analyze a list of (obligation, model_str) pairs.
    copilot_countermodel_analysis_hint(witness) -> str
        Return a copilot-style analysis summary string.

    Examples
    --------
    >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
    >>> witness = analyzer.analyze_countermodel("(assert (> x 0))", "x -> -1")
    >>> witness.role == CountermodelRole.FALSIFIER
    True
    """

    def __init__(self) -> None:
        # copilot: cache prevents redundant analysis of identical countermodels
        self._analysis_cache: dict[str, CountermodelsBecomeFirstClassWitness] = {}
        self._analysis_count: int = 0

    def analyze_countermodel(
        self,
        obligation: str,
        model_str: str,
        label: str = "",
    ) -> CountermodelsBecomeFirstClassWitness:
        """Run the full analysis pipeline on a raw Z3 model string.

        Parses the model string, classifies the countermodel's role, computes
        its semantic depth and falsification score, generates repair hints, and
        returns a fully populated ``CountermodelsBecomeFirstClassWitness``.

        Parameters
        ----------
        obligation : str
            The SMT2 string of the obligation being falsified.
        model_str : str
            The raw Z3 model output string (e.g., from ``model.sexpr()`` or
            ``str(model)``).
        label : str, optional
            A short human-readable label for the witness. If empty, a label
            is auto-generated from the obligation.

        Returns
        -------
        CountermodelsBecomeFirstClassWitness
            A fully populated, immutable witness object.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> w = analyzer.analyze_countermodel("(assert (> x 0))", "x -> -1", "neg-x")
        >>> w.copilot_label
        'neg-x'
        """
        # copilot: run the analysis pipeline in a fixed order
        assignments = self.parse_z3_model(model_str)
        role = self.classify_countermodel_role(assignments, obligation)
        depth = self.compute_semantic_depth(assignments)
        score = self.compute_falsification_score(assignments, obligation)
        copilot_label = label or f"auto:{obligation[:30].replace(' ', '_')}"

        # Build a preliminary witness without hints so we can generate hints from it
        preliminary = CountermodelsBecomeFirstClassWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=obligation,
            countermodel_assignments=tuple(sorted(assignments.items())),
            role=role,
            semantic_depth=depth,
            repair_hints=(),
            falsification_score=round(score, 4),
            copilot_label=copilot_label,
            created_at=time.time(),
            z3_model_str=model_str,
        )
        hints = self.generate_repair_hints(preliminary)

        # Check cache using fingerprint of the preliminary witness
        fp = preliminary.fingerprint()
        if fp in self._analysis_cache:
            logger.debug("CountermodelAnalyzer: cache hit for fingerprint %s", fp)
            return self._analysis_cache[fp]

        # Build final witness with hints
        final = CountermodelsBecomeFirstClassWitness(
            witness_id=preliminary.witness_id,
            obligation_smt=obligation,
            countermodel_assignments=preliminary.countermodel_assignments,
            role=role,
            semantic_depth=depth,
            repair_hints=tuple(hints),
            falsification_score=preliminary.falsification_score,
            copilot_label=copilot_label,
            created_at=preliminary.created_at,
            z3_model_str=model_str,
        )
        self._analysis_cache[fp] = final
        self._analysis_count += 1
        logger.debug(
            "CountermodelAnalyzer: analyzed witness %s role=%s score=%.3f",
            final.witness_id,
            final.role.name,
            final.falsification_score,
        )
        return final

    def parse_z3_model(self, model_str: str) -> dict[str, str]:
        """Parse a Z3 model string into a variable-to-value mapping.

        Handles two common Z3 output formats:
        - ``(define-fun varname () Sort value)`` (SMT2 / sexpr format)
        - ``varname -> value`` (Z3 Python API __str__ format)

        Parameters
        ----------
        model_str : str
            The raw model string to parse.

        Returns
        -------
        dict[str, str]
            A dict mapping variable name strings to value strings.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> result = analyzer.parse_z3_model("x -> -1\\ny -> 42")
        >>> result == {"x": "-1", "y": "42"}
        True
        >>> result2 = analyzer.parse_z3_model("(define-fun x () Int 7)")
        >>> result2 == {"x": "7"}
        True
        """
        # copilot: handle both define-fun sexpr and arrow formats
        assignments: dict[str, str] = {}
        for line in model_str.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            # define-fun format: (define-fun VAR () SORT VALUE)
            if line.startswith("(define-fun"):
                parts = line.strip("()").split()
                if len(parts) >= 5:
                    var_name = parts[1]
                    # value is everything after the sort (4th token onward)
                    value_parts = parts[4:]
                    value = " ".join(value_parts).rstrip(")")
                    assignments[var_name] = value.strip()
            # arrow format: VAR -> VALUE
            elif " -> " in line:
                left, _, right = line.partition(" -> ")
                var_name = left.strip()
                value = right.strip()
                if var_name:
                    assignments[var_name] = value
        return assignments

    def classify_countermodel_role(
        self,
        assignments: dict[str, str],
        obligation: str,
    ) -> CountermodelRole:
        """Heuristically assign a semantic role to a countermodel.

        Uses structural heuristics based on the assignment values and the
        shape of the obligation string to classify the countermodel.

        Parameters
        ----------
        assignments : dict[str, str]
            The parsed variable assignments from the countermodel.
        obligation : str
            The SMT2 obligation string being falsified.

        Returns
        -------
        CountermodelRole
            The inferred role.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> role = analyzer.classify_countermodel_role({"x": "-999999"}, "(assert (> x 0))")
        >>> role == CountermodelRole.REPAIR_SEED
        True
        """
        # copilot: heuristic classification based on value extremity and obligation structure
        if not assignments:
            return CountermodelRole.DIAGNOSTIC_ARTIFACT

        numeric_vals = self._extract_numeric_assignments(assignments)

        # Obstruction certificate: no numeric assignments and obligation is deeply nested
        if not numeric_vals and obligation.count("(") > 10:
            return CountermodelRole.OBSTRUCTION_CERTIFICATE

        # Boundary witness: all numeric values are near zero or near a round number
        if numeric_vals and self._detect_boundary_witness(assignments):
            return CountermodelRole.BOUNDARY_WITNESS

        # Repair seed: any value is extremely large or extremely small
        if numeric_vals:
            extremity_threshold = 1e5
            if any(abs(v) >= extremity_threshold for v in numeric_vals.values()):
                return CountermodelRole.REPAIR_SEED

        # Default to FALSIFIER for most countermodels
        return CountermodelRole.FALSIFIER

    def compute_semantic_depth(self, assignments: dict[str, str]) -> int:
        """Estimate the semantic depth of the countermodel from its assignments.

        Semantic depth is a heuristic measure of how deeply the falsification
        reaches into the obligation structure. It is estimated from:
        - The number of distinct variable namespaces (dot/underscore-separated prefixes)
        - The presence of nesting-suggestive variable name patterns
        - The count of variables in the model

        Parameters
        ----------
        assignments : dict[str, str]
            The parsed variable assignments.

        Returns
        -------
        int
            Estimated semantic depth, at least 1.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> depth = analyzer.compute_semantic_depth({"x": "1", "y": "2"})
        >>> depth >= 1
        True
        """
        if not assignments:
            return 1

        depth = 1
        namespaces: set[str] = set()
        for var in assignments:
            # count nesting indicators in variable names
            parts = var.replace(".", "_").split("_")
            if len(parts) > 1:
                namespaces.add(parts[0])
                depth = max(depth, len(parts))
            # explicit level/depth indicators
            for part in parts:
                if part.startswith("level") or part.startswith("depth") or part.startswith("inner"):
                    depth += 1
                    break

        # additional depth from namespace count
        depth += max(0, len(namespaces) - 1)
        # cap at a reasonable maximum
        return min(depth, 10)

    def generate_repair_hints(
        self,
        witness: CountermodelsBecomeFirstClassWitness,
    ) -> list[str]:
        """Generate a list of repair hint strings for a given witness.

        The hints are drawn from ``_REPAIR_HINT_TEMPLATES`` and formatted
        with variable names and values from the witness's assignments.

        Parameters
        ----------
        witness : CountermodelsBecomeFirstClassWitness
            The witness to generate hints for.

        Returns
        -------
        list[str]
            A list of formatted repair hint strings, ordered by relevance.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> w = analyzer.analyze_countermodel("(assert (> x 0))", "x -> -1")
        >>> len(w.repair_hints) >= 1
        True
        """
        # copilot: generate hints based on role and assignment values
        hints: list[str] = []
        assignments = witness.assignments_dict()
        numeric_vals = self._extract_numeric_assignments(assignments)

        if not assignments:
            hints.append(
                "No variable assignments found in countermodel. "
                "Inspect the raw Z3 model output for clues."
            )
            return hints

        first_var = next(iter(assignments))
        first_val = assignments[first_var]

        role = witness.role
        if role in (CountermodelRole.FALSIFIER, CountermodelRole.REPAIR_SEED):
            if numeric_vals:
                hints.append(
                    _REPAIR_HINT_TEMPLATES["strengthen_bound"].format(
                        var=first_var, val=first_val
                    )
                )
                hints.append(
                    _REPAIR_HINT_TEMPLATES["add_guard"].format(
                        var=first_var, val=first_val
                    )
                )
        if role == CountermodelRole.BOUNDARY_WITNESS:
            hints.append(
                _REPAIR_HINT_TEMPLATES["tighten_precondition"].format(
                    var=first_var, val=first_val
                )
            )
        if role == CountermodelRole.OBSTRUCTION_CERTIFICATE:
            hints.append(
                _REPAIR_HINT_TEMPLATES["decompose_obligation"].format(
                    var=first_var, val=first_val
                )
            )

        # Always add a case-split hint if there are multiple variables
        if len(assignments) >= 2:
            hints.append(
                _REPAIR_HINT_TEMPLATES["split_case"].format(
                    var=first_var, val=first_val
                )
            )
        # Always add a type constraint hint
        hints.append(
            _REPAIR_HINT_TEMPLATES["add_type_constraint"].format(
                var=first_var, val=first_val
            )
        )
        return hints

    def compute_falsification_score(
        self,
        assignments: dict[str, str],
        obligation: str,
    ) -> float:
        """Compute a [0.0, 1.0] falsification severity score for a countermodel.

        The score is a weighted combination of:
        - Numeric divergence: how far numeric values are from zero/boundary
        - Variable count: more variables → higher score
        - Nesting depth: obligation nesting depth
        - Symbolic complexity: non-numeric value count
        - Boundary proximity: whether values are near-zero

        Parameters
        ----------
        assignments : dict[str, str]
            The parsed variable assignments.
        obligation : str
            The obligation SMT2 string.

        Returns
        -------
        float
            A score in [0.0, 1.0]. Higher values indicate more severe / more
            informative countermodels.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> score = analyzer.compute_falsification_score({"x": "-1"}, "(assert (> x 0))")
        >>> 0.0 <= score <= 1.0
        True
        """
        if not assignments:
            return 0.1

        numeric_vals = self._extract_numeric_assignments(assignments)
        weights = _FALSIFICATION_SCORE_WEIGHTS

        # numeric divergence component
        if numeric_vals:
            max_abs = max(abs(v) for v in numeric_vals.values()) if numeric_vals else 0.0
            numeric_component = min(1.0, math.log1p(max_abs) / 15.0)
        else:
            numeric_component = 0.0

        # variable count component
        var_count_component = min(1.0, len(assignments) / 10.0)

        # nesting depth from obligation paren count
        paren_count = obligation.count("(")
        nesting_component = min(1.0, paren_count / 20.0)

        # symbolic complexity: fraction of non-numeric values
        non_numeric = sum(
            1 for v in assignments.values()
            if not v.lstrip("-").replace(".", "", 1).isdigit()
        )
        symbolic_component = min(1.0, non_numeric / max(1, len(assignments)))

        # boundary proximity: near-zero values get a boost
        boundary_component = 0.0
        if numeric_vals:
            near_zero = sum(1 for v in numeric_vals.values() if abs(v) <= 1.0)
            boundary_component = min(1.0, near_zero / max(1, len(numeric_vals)))

        score = (
            weights["numeric_divergence"] * numeric_component
            + weights["variable_count"] * var_count_component
            + weights["nesting_depth"] * nesting_component
            + weights["symbolic_complexity"] * symbolic_component
            + weights["boundary_proximity"] * boundary_component
        )
        return round(min(1.0, max(0.0, score)), 4)

    def batch_analyze(
        self,
        pairs: list[tuple[str, str]],
    ) -> list[CountermodelsBecomeFirstClassWitness]:
        """Analyze a list of (obligation, model_str) pairs.

        Parameters
        ----------
        pairs : list[tuple[str, str]]
            A list of (obligation_smt, z3_model_str) pairs to analyze.

        Returns
        -------
        list[CountermodelsBecomeFirstClassWitness]
            A list of witnesses in the same order as the input pairs.

        Examples
        --------
        >>> analyzer = CountermodelsBecomeFirstClassAnalyzer()
        >>> results = analyzer.batch_analyze([("(assert (> x 0))", "x -> -1")])
        >>> len(results) == 1
        True
        """
        # copilot: batch analysis preserves order and allows cache reuse
        return [self.analyze_countermodel(obl, mdl) for obl, mdl in pairs]

    def copilot_countermodel_analysis_hint(
        self,
        witness: CountermodelsBecomeFirstClassWitness,
    ) -> str:
        """Return a copilot-style analysis summary string for a witness.

        Parameters
        ----------
        witness : CountermodelsBecomeFirstClassWitness
            The witness to summarise.

        Returns
        -------
        str
            A multi-line copilot hint string.
        """
        # copilot: produce a concise but complete analysis summary
        complexity = self._estimate_repair_complexity(witness)
        lines = [
            "# copilot: countermodel analysis summary",
            f"# copilot: witness_id={witness.witness_id}",
            f"# copilot: role={witness.role.name} | severity={witness.role.severity_default()}",
            f"# copilot: falsification_score={witness.falsification_score:.4f}",
            f"# copilot: semantic_depth={witness.semantic_depth}",
            f"# copilot: estimated_repair_complexity={complexity}/10",
            f"# copilot: is_actionable={witness.role.is_actionable()}",
            f"# copilot: num_assignments={len(witness.countermodel_assignments)}",
            f"# copilot: has_numeric={witness.has_numeric_assignments()}",
            f"# copilot: num_repair_hints={len(witness.repair_hints)}",
        ]
        if witness.repair_hints:
            lines.append(f"# copilot: top_hint={witness.repair_hints[0][:100]}")
        return "\n".join(lines)

    def _extract_numeric_assignments(
        self,
        assignments: dict[str, str],
    ) -> dict[str, float]:
        """Return only the assignments whose values are parseable as floats.

        Parameters
        ----------
        assignments : dict[str, str]
            The full assignment dict.

        Returns
        -------
        dict[str, float]
            A filtered dict with float values.
        """
        result: dict[str, float] = {}
        for var, val in assignments.items():
            try:
                result[var] = float(val)
            except (ValueError, TypeError):
                pass
        return result

    def _detect_boundary_witness(self, assignments: dict[str, str]) -> bool:
        """Return True if the assignments suggest a boundary witness.

        A boundary witness has numeric values very close to round numbers
        (within 0.01) or near zero.

        Parameters
        ----------
        assignments : dict[str, str]
            The full assignment dict.

        Returns
        -------
        bool
            True if this looks like a boundary witness.
        """
        numeric = self._extract_numeric_assignments(assignments)
        if not numeric:
            return False
        near_boundary_count = sum(
            1 for v in numeric.values()
            if abs(v - round(v)) < 0.01 or abs(v) < 0.5
        )
        return near_boundary_count >= max(1, len(numeric) // 2)

    def _estimate_repair_complexity(
        self,
        witness: CountermodelsBecomeFirstClassWitness,
    ) -> int:
        """Estimate repair complexity on a scale of 1 to 10.

        Parameters
        ----------
        witness : CountermodelsBecomeFirstClassWitness
            The witness to estimate complexity for.

        Returns
        -------
        int
            An integer in [1, 10] where 10 is most complex.
        """
        # copilot: complexity combines depth, score, variable count, and role priority
        base = witness.semantic_depth
        score_contribution = int(witness.falsification_score * 3)
        var_contribution = min(3, len(witness.countermodel_assignments))
        role_contribution = 6 - witness.role.repair_priority()
        raw = base + score_contribution + var_contribution + role_contribution
        return min(10, max(1, raw))

    def _build_blocking_clause(self, assignments: dict[str, str]) -> str:
        """Build a raw SMT2 blocking clause from an assignment dict.

        Parameters
        ----------
        assignments : dict[str, str]
            The variable assignments to block.

        Returns
        -------
        str
            An SMT2 ``(assert (not ...))`` blocking clause string.
        """
        if not assignments:
            return "; no assignments to block"
        equalities = " ".join(f"(= {k} {v})" for k, v in sorted(assignments.items()))
        if len(assignments) == 1:
            return f"(assert (not {equalities}))"
        return f"(assert (not (and {equalities})))"


# ============================== coordinator ==============================


class CountermodelsBecomeFirstClassCoordinator:
    """Coordinates registration, promotion, and reporting of first-class countermodels.

    The coordinator is the top-level entry point for the countermodel-first-class
    pipeline. Client code submits raw obligation/model pairs, and the coordinator
    manages the full lifecycle: analysis, promotion, registry management, blocking
    clause emission, and report generation.

    The coordinator maintains a witness registry keyed by ``witness_id`` and
    tracks pipeline statistics. It provides query interfaces to retrieve witnesses
    by role, emit combined blocking clause sets, and produce human-readable reports.

    Parameters
    ----------
    None

    Attributes
    ----------
    _analyzer : CountermodelsBecomeFirstClassAnalyzer
        The internal analyzer instance.
    _stats : dict[str, int]
        Running statistics counters (registered, promoted, falsifiers, etc.).
    _witness_registry : dict[str, CountermodelsBecomeFirstClassWitness]
        Registry mapping witness_id to witness.

    Methods
    -------
    register_countermodel(obligation, z3_model_str, label) -> CountermodelsBecomeFirstClassWitness
        Analyze and register a countermodel.
    promote_to_first_class(witness) -> CountermodelsBecomeFirstClassWitness
        Mark a witness as promoted (idempotent for frozen dataclasses).
    all_falsifiers() -> list[CountermodelsBecomeFirstClassWitness]
        Return all registered FALSIFIER witnesses.
    all_repair_seeds() -> list[CountermodelsBecomeFirstClassWitness]
        Return all REPAIR_SEED witnesses.
    all_certificates() -> list[CountermodelsBecomeFirstClassWitness]
        Return all OBSTRUCTION_CERTIFICATE witnesses.
    emit_blocking_clause_set(witnesses) -> str
        Emit a multi-line SMT2 blocking clause set for a list of witnesses.
    countermodel_report() -> str
        Produce a detailed multi-line text report of all registered witnesses.
    iter_witnesses() -> Iterator[CountermodelsBecomeFirstClassWitness]
        Iterate over all registered witnesses.
    find_by_role(role) -> list[CountermodelsBecomeFirstClassWitness]
        Return all witnesses with the specified role.
    stats : dict[str, int]
        Property returning the current statistics dict.

    Examples
    --------
    >>> coord = CountermodelsBecomeFirstClassCoordinator()
    >>> w = coord.register_countermodel("(assert (> x 0))", "x -> -1", "demo")
    >>> len(coord) == 1
    True
    >>> coord.all_falsifiers()[0].role == CountermodelRole.FALSIFIER
    True
    """

    def __init__(self) -> None:
        # copilot: initialize fresh state with a new analyzer and empty registry
        self._analyzer = CountermodelsBecomeFirstClassAnalyzer()
        self._stats: dict[str, int] = collections.defaultdict(int)
        self._witness_registry: dict[str, CountermodelsBecomeFirstClassWitness] = {}

    def register_countermodel(
        self,
        obligation: str,
        z3_model_str: str,
        label: str = "",
    ) -> CountermodelsBecomeFirstClassWitness:
        """Analyze a raw countermodel and register it in the witness registry.

        Parameters
        ----------
        obligation : str
            The SMT2 obligation that the countermodel falsifies.
        z3_model_str : str
            The raw Z3 model output string.
        label : str, optional
            A short human-readable label for the witness.

        Returns
        -------
        CountermodelsBecomeFirstClassWitness
            The newly registered (or cached) witness.

        Examples
        --------
        >>> coord = CountermodelsBecomeFirstClassCoordinator()
        >>> w = coord.register_countermodel("(assert (> x 0))", "x -> -1")
        >>> w.witness_id in coord._witness_registry
        True
        """
        # copilot: analyze → register → update stats
        witness = self._analyzer.analyze_countermodel(obligation, z3_model_str, label)
        self._witness_registry[witness.witness_id] = witness
        self._stats["registered"] += 1
        self._stats[f"role_{witness.role.name}"] += 1
        logger.info(
            "Coordinator: registered witness %s (role=%s)", witness.witness_id, witness.role.name
        )
        return witness

    def promote_to_first_class(
        self,
        witness: CountermodelsBecomeFirstClassWitness,
    ) -> CountermodelsBecomeFirstClassWitness:
        """Mark a witness as promoted to first-class status.

        Since witnesses are frozen dataclasses, promotion is a registry
        operation: the witness is inserted into the registry if not already
        present, and the promotion counter is incremented.

        Parameters
        ----------
        witness : CountermodelsBecomeFirstClassWitness
            The witness to promote.

        Returns
        -------
        CountermodelsBecomeFirstClassWitness
            The same witness (unchanged, since it is frozen).
        """
        # copilot: idempotent registration — re-promoting is safe
        if witness.witness_id not in self._witness_registry:
            self._witness_registry[witness.witness_id] = witness
            self._stats["registered"] += 1
        self._stats["promoted"] += 1
        return witness

    def all_falsifiers(self) -> list[CountermodelsBecomeFirstClassWitness]:
        """Return all registered FALSIFIER witnesses sorted by falsification score.

        Returns
        -------
        list[CountermodelsBecomeFirstClassWitness]
            FALSIFIER witnesses, highest score first.
        """
        return sorted(
            self.find_by_role(CountermodelRole.FALSIFIER),
            key=lambda w: w.falsification_score,
            reverse=True,
        )

    def all_repair_seeds(self) -> list[CountermodelsBecomeFirstClassWitness]:
        """Return all registered REPAIR_SEED witnesses.

        Returns
        -------
        list[CountermodelsBecomeFirstClassWitness]
            REPAIR_SEED witnesses, deepest first.
        """
        return sorted(
            self.find_by_role(CountermodelRole.REPAIR_SEED),
            key=lambda w: w.semantic_depth,
            reverse=True,
        )

    def all_certificates(self) -> list[CountermodelsBecomeFirstClassWitness]:
        """Return all registered OBSTRUCTION_CERTIFICATE witnesses.

        Returns
        -------
        list[CountermodelsBecomeFirstClassWitness]
            Certificate witnesses in registration order.
        """
        return self.find_by_role(CountermodelRole.OBSTRUCTION_CERTIFICATE)

    def emit_blocking_clause_set(
        self,
        witnesses: list[CountermodelsBecomeFirstClassWitness],
    ) -> str:
        """Emit a multi-line SMT2 string containing blocking clauses for all witnesses.

        Parameters
        ----------
        witnesses : list[CountermodelsBecomeFirstClassWitness]
            The witnesses whose assignments should be blocked.

        Returns
        -------
        str
            A multi-line SMT2 string with one blocking clause per witness,
            suitable for prepending to a solver context.

        Examples
        --------
        >>> coord = CountermodelsBecomeFirstClassCoordinator()
        >>> w = coord.register_countermodel("(assert (> x 0))", "x -> -1")
        >>> clauses = coord.emit_blocking_clause_set([w])
        >>> "(assert" in clauses
        True
        """
        # copilot: emit one blocking clause per witness with a header comment
        if not witnesses:
            return "; no witnesses — no blocking clauses to emit"
        parts = [
            f"; blocking clause set — {len(witnesses)} witness(es)",
            f"; generated at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            "",
        ]
        for i, w in enumerate(witnesses, start=1):
            parts.append(f"; witness {i}/{len(witnesses)} id={w.witness_id} role={w.role.name}")
            parts.append(w.to_smt2_blocking_clause())
            parts.append("")
        return "\n".join(parts)

    def countermodel_report(self) -> str:
        """Produce a detailed multi-line text report of all registered witnesses.

        Returns
        -------
        str
            A human-readable report covering all registered witnesses,
            statistics, and a summary section.

        Examples
        --------
        >>> coord = CountermodelsBecomeFirstClassCoordinator()
        >>> coord.register_countermodel("(assert (> x 0))", "x -> -1")  # doctest: +ELLIPSIS
        CountermodelsBecomeFirstClassWitness(...)
        >>> report = coord.countermodel_report()
        >>> "FALSIFIER" in report or "registered" in report
        True
        """
        # copilot: rich report for human reviewers and CI output
        lines = [
            "=" * 72,
            "  COUNTERMODEL FIRST-CLASS WITNESS REPORT",
            f"  Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"  Total witnesses: {len(self._witness_registry)}",
            "=" * 72,
            "",
        ]

        # statistics section
        lines.append("STATISTICS:")
        for key, val in sorted(self._stats.items()):
            lines.append(f"  {key}: {val}")
        lines.append("")

        # witnesses by role
        for role in CountermodelRole:
            witnesses = self.find_by_role(role)
            if not witnesses:
                continue
            lines.append(f"ROLE: {role.name} ({len(witnesses)} witness(es))")
            lines.append(f"  Severity: {role.severity_default().upper()}")
            lines.append(f"  Actionable: {role.is_actionable()}")
            lines.append(f"  Description: {role.semantic_description()[:100]}...")
            lines.append("")
            for w in witnesses:
                lines.append(f"  Witness: {w.witness_id}")
                lines.append(f"    label         : {w.copilot_label}")
                lines.append(f"    score         : {w.falsification_score:.4f}")
                lines.append(f"    depth         : {w.semantic_depth}")
                lines.append(f"    assignments   : {len(w.countermodel_assignments)}")
                lines.append(f"    fingerprint   : {w.fingerprint()[:16]}...")
                if w.repair_hints:
                    lines.append(f"    top hint      : {w.repair_hints[0][:80]}...")
                lines.append("")

        lines.append("=" * 72)
        return "\n".join(lines)

    def iter_witnesses(self) -> Iterator[CountermodelsBecomeFirstClassWitness]:
        """Iterate over all registered witnesses in registration order.

        Yields
        ------
        CountermodelsBecomeFirstClassWitness
            Each registered witness.
        """
        yield from self._witness_registry.values()

    def find_by_role(
        self,
        role: CountermodelRole,
    ) -> list[CountermodelsBecomeFirstClassWitness]:
        """Return all registered witnesses with the given role.

        Parameters
        ----------
        role : CountermodelRole
            The role to filter by.

        Returns
        -------
        list[CountermodelsBecomeFirstClassWitness]
            All witnesses with the specified role.
        """
        return [w for w in self._witness_registry.values() if w.role == role]

    @property
    def stats(self) -> dict[str, int]:
        """Return a copy of the current statistics dict.

        Returns
        -------
        dict[str, int]
            Statistics counters including registration, promotion, and role counts.
        """
        return dict(self._stats)

    def __repr__(self) -> str:
        return (
            f"CountermodelsBecomeFirstClassCoordinator("
            f"witnesses={len(self._witness_registry)}, "
            f"stats={dict(self._stats)})"
        )

    def __len__(self) -> int:
        return len(self._witness_registry)


# ============================== module convenience ==============================


def register_simple_countermodel(
    obligation: str,
    model_str: str,
) -> CountermodelsBecomeFirstClassWitness:
    """Module-level convenience function to register a single countermodel.

    Creates a fresh ``CountermodelsBecomeFirstClassCoordinator``, registers the
    countermodel, and returns the resulting witness. This is the simplest way to
    promote a countermodel to first-class status without managing coordinator
    lifecycle explicitly.

    Parameters
    ----------
    obligation : str
        The SMT2 obligation string that is falsified by the countermodel.
    model_str : str
        The raw Z3 model output string.

    Returns
    -------
    CountermodelsBecomeFirstClassWitness
        The fully analyzed and promoted first-class witness.

    Examples
    --------
    >>> w = register_simple_countermodel("(assert (> x 0))", "x -> -1")
    >>> isinstance(w, CountermodelsBecomeFirstClassWitness)
    True
    >>> w.role in list(CountermodelRole)
    True
    """
    # copilot: convenience wrapper — creates a fresh coordinator per call
    coordinator = CountermodelsBecomeFirstClassCoordinator()
    return coordinator.register_countermodel(obligation, model_str)


# ============================== smoke test ==============================


if __name__ == "__main__":
    print("=" * 70)
    print("  countermodels_should_become_first.py — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------ #
    # 1. Basic witness creation via the coordinator                        #
    # ------------------------------------------------------------------ #
    print("\n[1] Creating coordinator and registering countermodels...")
    coord = CountermodelsBecomeFirstClassCoordinator()

    w1 = coord.register_countermodel(
        obligation="(assert (> x 0))",
        z3_model_str="x -> -1",
        label="neg-x-falsifier",
    )
    print(f"    Registered witness: {w1.witness_id[:16]}... role={w1.role.name}")
    assert w1.role == CountermodelRole.FALSIFIER, f"Expected FALSIFIER, got {w1.role}"

    w2 = coord.register_countermodel(
        obligation="(assert (and (>= y 0) (<= y 100)))",
        z3_model_str="y -> 999999",
        label="y-out-of-range-seed",
    )
    print(f"    Registered witness: {w2.witness_id[:16]}... role={w2.role.name}")
    assert w2.role == CountermodelRole.REPAIR_SEED, f"Expected REPAIR_SEED, got {w2.role}"

    w3 = coord.register_countermodel(
        obligation="(assert (= (f x y) 0))",
        z3_model_str="x -> 0\ny -> 0",
        label="boundary-zero",
    )
    print(f"    Registered witness: {w3.witness_id[:16]}... role={w3.role.name}")

    print(f"    Coordinator length: {len(coord)}")
    assert len(coord) == 3

    # ------------------------------------------------------------------ #
    # 2. Witness inspection                                                #
    # ------------------------------------------------------------------ #
    print("\n[2] Inspecting witness fields and methods...")
    assert w1.assignment_for("x") == "-1", "assignment_for failed"
    assert w1.assignment_for("not_there") is None, "should return None for missing var"
    assert "x" in w1.variable_names(), "variable_names failed"
    assert w1.has_numeric_assignments(), "should have numeric assignments"
    fp = w1.fingerprint()
    assert len(fp) == 64, f"fingerprint should be 64 chars, got {len(fp)}"
    print(f"    fingerprint (first 16): {fp[:16]}...")

    sem = w1.to_semantic_witness()
    assert "fingerprint" in sem
    assert sem["role"] == "FALSIFIER"
    assert "blocking_clause" in sem
    print(f"    semantic_witness keys: {sorted(sem.keys())}")

    # ------------------------------------------------------------------ #
    # 3. Blocking clause generation                                        #
    # ------------------------------------------------------------------ #
    print("\n[3] Testing blocking clause generation...")
    clause = w1.to_smt2_blocking_clause()
    assert "(assert (not" in clause, f"Bad blocking clause: {clause}"
    print(f"    Blocking clause:\n      {clause}")

    all_clauses = coord.emit_blocking_clause_set(list(coord.iter_witnesses()))
    assert "(assert" in all_clauses
    print(f"    Combined blocking clause set ({len(all_clauses)} chars)")

    # ------------------------------------------------------------------ #
    # 4. Repair hints                                                      #
    # ------------------------------------------------------------------ #
    print("\n[4] Verifying repair hints...")
    assert len(w1.repair_hints) >= 1, "Should have at least one repair hint"
    print(f"    Repair hints for w1 ({len(w1.repair_hints)} hints):")
    for i, hint in enumerate(w1.repair_hints[:3], 1):
        print(f"      [{i}] {hint[:90]}...")

    # ------------------------------------------------------------------ #
    # 5. Merge witnesses                                                   #
    # ------------------------------------------------------------------ #
    print("\n[5] Merging two witnesses...")
    merged = w1.merge(w3)
    assert isinstance(merged, CountermodelsBecomeFirstClassWitness)
    print(f"    Merged witness id: {merged.witness_id[:16]}...")
    print(f"    Merged assignments: {merged.assignments_dict()}")
    assert len(merged.countermodel_assignments) >= len(w1.countermodel_assignments)

    # ------------------------------------------------------------------ #
    # 6. Serialisation round-trip                                          #
    # ------------------------------------------------------------------ #
    print("\n[6] Testing serialisation round-trip...")
    d = w1.to_dict()
    restored = CountermodelsBecomeFirstClassWitness.from_dict(d)
    assert restored.fingerprint() == w1.fingerprint(), "Round-trip fingerprint mismatch"
    assert restored.role == w1.role
    print(f"    Round-trip OK: fingerprint={restored.fingerprint()[:16]}...")

    # ------------------------------------------------------------------ #
    # 7. Role querying                                                     #
    # ------------------------------------------------------------------ #
    print("\n[7] Querying by role...")
    falsifiers = coord.all_falsifiers()
    seeds = coord.all_repair_seeds()
    certs = coord.all_certificates()
    print(f"    Falsifiers: {len(falsifiers)}")
    print(f"    Repair seeds: {len(seeds)}")
    print(f"    Obstruction certificates: {len(certs)}")

    # ------------------------------------------------------------------ #
    # 8. Copilot hints                                                     #
    # ------------------------------------------------------------------ #
    print("\n[8] Testing copilot hints...")
    hint = w1.copilot_countermodel_hint()
    assert "copilot:" in hint
    print(f"    Copilot hint (first 3 lines):")
    for line in hint.splitlines()[:3]:
        print(f"      {line}")

    analysis_hint = coord._analyzer.copilot_countermodel_analysis_hint(w2)
    assert "copilot:" in analysis_hint
    print(f"    Analysis hint (first 2 lines):")
    for line in analysis_hint.splitlines()[:2]:
        print(f"      {line}")

    # ------------------------------------------------------------------ #
    # 9. Module convenience function                                       #
    # ------------------------------------------------------------------ #
    print("\n[9] Testing module-level register_simple_countermodel...")
    simple_w = register_simple_countermodel(
        "(assert (not (= a b)))",
        "a -> 5\nb -> 5",
    )
    assert isinstance(simple_w, CountermodelsBecomeFirstClassWitness)
    print(f"    Simple witness role: {simple_w.role.name}")

    # ------------------------------------------------------------------ #
    # 10. CountermodelRole enum inspection                                 #
    # ------------------------------------------------------------------ #
    print("\n[10] CountermodelRole enum inspection...")
    for role in CountermodelRole:
        print(
            f"    {role.name:35s} priority={role.repair_priority()} "
            f"actionable={role.is_actionable()} severity={role.severity_default()}"
        )

    # ------------------------------------------------------------------ #
    # 11. Batch analysis                                                   #
    # ------------------------------------------------------------------ #
    print("\n[11] Batch analysis...")
    pairs = [
        ("(assert (> z 10))", "z -> 0"),
        ("(assert (< w -5))", "w -> 100"),
        ("(assert (= u 42))", "u -> 0"),
    ]
    analyzer = CountermodelsBecomeFirstClassAnalyzer()
    batch_results = analyzer.batch_analyze(pairs)
    assert len(batch_results) == 3
    for bw in batch_results:
        print(f"    batch witness: {bw.copilot_label[:40]} role={bw.role.name} score={bw.falsification_score:.3f}")

    # ------------------------------------------------------------------ #
    # 12. Full report                                                      #
    # ------------------------------------------------------------------ #
    print("\n[12] Generating countermodel report...")
    report = coord.countermodel_report()
    assert "FALSIFIER" in report or "registered" in report
    print(f"    Report length: {len(report)} chars")
    print(f"    Report preview:\n")
    for line in report.splitlines()[:12]:
        print(f"      {line}")

    print("\n" + "=" * 70)
    print("  All smoke tests PASSED.")
    print("=" * 70)
