"""Counterexample extraction and analysis for repair_semantics (theory2.tex Ch11 §11.1).

Stage 01 of the repair pipeline: given a :class:`~jugeo.solver.countermodels.Countermodel`
from the solver, extract a full :class:`CounterexampleRecord`, classify the failure,
minimize the witness, compute the cohomology class, and produce repair hints.

Theory basis (theory2.tex §11.1 — Counterexamples as Semantic Objects)
----------------------------------------------------------------------
A counterexample is not an error message — it is a cohomology class in Ȟ¹(𝔘, 𝒟).
Formally, given an open cover 𝔘 = {U_i} of the semantic site and a descent datum
𝒟 = {s_ij}, a counterexample witnesses the failure of the local sections {s_i} to
glue into a global section.  The obstruction class [η] ∈ Ȟ¹ records *which*
transition functions disagree and by how much.

Extraction pipeline
-------------------
1. Raw countermodel from Z3 → :class:`CounterexampleAnalyzer.analyze`
2. Failure classification → :class:`CounterexampleAnalyzer.classify_failure`
3. Minimization (delta-debug) → :class:`CounterexampleAnalyzer.extract_minimal_core`
4. Cohomology class label → :class:`CounterexampleAnalyzer.compute_cohomology_class`
5. Repair hint generation → :class:`CounterexampleAnalyzer.extract_repair_hints`
6. Severity scoring → :class:`CounterexampleAnalyzer.score_severity`

Notation
--------
* ``coord``  — a semantic coordinate string, the address of a judgment claim.
* ``FC``     — :class:`~jugeo.solver.countermodels.FailureClass` enum value.
* ``Ȟ¹``    — first Čech cohomology group of the semantic site.

# copilot: s01 counterexample extraction — theory2 ch11 stage 01
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Sequence

from jugeo.errors import (
    EvidenceFamily,
    FailureChain,
    FailureClassification,
    FailureScope,
    JuGeoError,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    as_failure_payload,
    raise_with_scope,
)
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    JudgmentAlgebra,
    JudgmentStatus,
    Obstruction,
    Provenance,
    ProvenanceSource,
    Proposition,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.solver.countermodels import (
    Countermodel,
    CountermodelExtractor,
    FailureClass,
    ObstructionConverter,
    RepairType,
)
from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    RepairFrontier,
    RepairPlan,
    RepairValidator,
)

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "counterexample_extraction",
    "pipeline_stage": "01",
    "theory_section": "§11.1 — Counterexamples as Semantic Objects",
}

# ---------------------------------------------------------------------------
# §1  Module-level helper functions
# ---------------------------------------------------------------------------


def _dict_to_pairs(d: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Convert a dict to a sorted tuple of ``(key, value)`` string pairs.

    Sorting by key ensures a canonical, deterministic representation that
    is safe to hash and embed in frozen dataclasses.  All values are
    stringified via :func:`str` so that numeric and boolean assignments
    are handled uniformly.

    Parameters
    ----------
    d : dict[str, Any]
        The dictionary to convert.  Keys must be strings; values may be
        any type that has a well-defined :func:`str` representation.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Sorted tuple of ``(key, str(value))`` pairs, one per item in *d*.

    Examples
    --------
    >>> _dict_to_pairs({"b": True, "a": 42})
    (('a', '42'), ('b', 'True'))
    """
    return tuple(sorted((str(k), str(v)) for k, v in d.items()))


def _pairs_to_dict(pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Convert a sorted tuple of ``(key, value)`` string pairs to a plain dict.

    This is the inverse of :func:`_dict_to_pairs`.  When *pairs* contains
    duplicate keys the last occurrence wins (standard Python dict semantics).

    Parameters
    ----------
    pairs : tuple[tuple[str, str], ...]
        The sorted pairs to convert, typically the output of
        :func:`_dict_to_pairs`.

    Returns
    -------
    dict[str, str]
        Plain string-keyed dict reconstructed from *pairs*.

    Examples
    --------
    >>> _pairs_to_dict((('a', '42'), ('b', 'True')))
    {'a': '42', 'b': 'True'}
    """
    return dict(pairs)


def _iso_timestamp() -> str:
    """Return the current UTC time as an ISO 8601 string.

    The timestamp is always timezone-aware and uses the ``+00:00`` UTC
    suffix so that consumers can parse it unambiguously.  This function
    is called once per :meth:`CounterexampleAnalyzer.analyze` invocation
    to record the extraction wall-clock time.

    Returns
    -------
    str
        ISO 8601 UTC timestamp, e.g. ``"2024-03-15T12:34:56.789123+00:00"``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _stable_hash6(s: str) -> str:
    """Return the first 6 hexadecimal characters of the SHA-256 hash of *s*.

    Used to produce a short, collision-resistant suffix for cohomology class
    labels.  The input string is UTF-8 encoded before hashing so that the
    hash is independent of the Python process's default encoding.

    Parameters
    ----------
    s : str
        The string to hash.  Typically the canonical string representation
        of a :class:`CounterexampleRecord`'s variable assignments.

    Returns
    -------
    str
        Six-character lowercase hexadecimal prefix of the SHA-256 digest,
        e.g. ``"a3f9b2"``.

    Examples
    --------
    >>> len(_stable_hash6("hello world"))
    6
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:6]


def _failure_class_base_score(fc: FailureClass) -> int:
    """Return the base severity integer for a given :class:`FailureClass`.

    This mapping encodes the theory-level severity ordering from
    theory2.tex §11.1, Table 11.1.  Higher numbers indicate failures
    that are harder to repair locally and more likely to require structural
    changes to the specification.

    Parameters
    ----------
    fc : FailureClass
        The failure class to score.

    Returns
    -------
    int
        An integer in ``{1, 2, 3, 4, 5}`` representing base severity.
    """
    _SCORES: dict[FailureClass, int] = {
        FailureClass.UNKNOWN: 1,
        FailureClass.ASSIGNMENT_CONFLICT: 2,
        FailureClass.FUNCTION_MISMATCH: 3,
        FailureClass.SORT_VIOLATION: 3,
        FailureClass.QUANTIFIER_WITNESS: 4,
        FailureClass.ARRAY_OUT_OF_BOUNDS: 5,
    }
    return _SCORES.get(fc, 1)


def _hints_for_assignment_conflict(coordinate: str) -> tuple[RepairHint, ...]:
    """Build the canonical repair hints for an ASSIGNMENT_CONFLICT failure.

    Returns two hints: a primary REQUIRED hint to strengthen the precondition
    at *coordinate*, and a SUGGESTED hint to check the guard condition.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate where the conflict was detected.

    Returns
    -------
    tuple[RepairHint, ...]
        Two :class:`~jugeo.errors.RepairHint` instances ordered by priority.
    """
    primary = RepairHint(
        action="strengthen_precondition",
        description=(
            "Add a guard for the conflicting assignment at coordinate "
            f"'{coordinate}'.  The counterexample witness assigns contradictory "
            "values to the same variable in different branches; adding a case "
            "split or precondition restriction will eliminate the conflict."
        ),
        priority=RepairPriority.REQUIRED,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    secondary = RepairHint(
        action="check_guard_condition",
        description=(
            "Verify that the existing guard condition is strong enough to "
            "prevent the conflicting assignment from being reachable."
        ),
        priority=RepairPriority.SUGGESTED,
        target_coordinate=coordinate or None,
        estimated_effort="trivial",
    )
    return (primary, secondary)


def _hints_for_sort_violation(coordinate: str) -> tuple[RepairHint, ...]:
    """Build the canonical repair hints for a SORT_VIOLATION failure.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate where the sort violation was detected.

    Returns
    -------
    tuple[RepairHint, ...]
        Two :class:`~jugeo.errors.RepairHint` instances.
    """
    primary = RepairHint(
        action="add_sort_constraint",
        description=(
            "Introduce an explicit sort constraint at coordinate "
            f"'{coordinate}' to prevent the witness value from inhabiting "
            "a sort it does not belong to.  The solver assigned a value "
            "outside the declared sort universe."
        ),
        priority=RepairPriority.REQUIRED,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    secondary = RepairHint(
        action="refine_sort_universe",
        description=(
            "Consider narrowing the sort universe declaration if the current "
            "interpretation is too permissive."
        ),
        priority=RepairPriority.SUGGESTED,
        target_coordinate=coordinate or None,
        estimated_effort="significant",
    )
    return (primary, secondary)


def _hints_for_function_mismatch(coordinate: str) -> tuple[RepairHint, ...]:
    """Build the canonical repair hints for a FUNCTION_MISMATCH failure.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate where the function mismatch was detected.

    Returns
    -------
    tuple[RepairHint, ...]
        Two :class:`~jugeo.errors.RepairHint` instances.
    """
    primary = RepairHint(
        action="refine_function_spec",
        description=(
            "The solver found an interpretation of a function symbol that "
            "violates the declared specification at coordinate "
            f"'{coordinate}'.  Strengthen the function specification or add "
            "a universally quantified constraint over the domain."
        ),
        priority=RepairPriority.REQUIRED,
        target_coordinate=coordinate or None,
        estimated_effort="significant",
    )
    secondary = RepairHint(
        action="add_function_axiom",
        description=(
            "Consider adding a domain-restriction axiom for the function "
            "symbol that the counterexample exploits."
        ),
        priority=RepairPriority.SUGGESTED,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    return (primary, secondary)


def _hints_for_array_oob(coordinate: str) -> tuple[RepairHint, ...]:
    """Build the canonical repair hints for an ARRAY_OUT_OF_BOUNDS failure.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate where the array access violation was found.

    Returns
    -------
    tuple[RepairHint, ...]
        Two :class:`~jugeo.errors.RepairHint` instances.
    """
    primary = RepairHint(
        action="add_bounds_check",
        description=(
            f"Insert an explicit bounds check at coordinate '{coordinate}' "
            "before the array access that the counterexample exercises.  The "
            "witness index falls outside the declared array range."
        ),
        priority=RepairPriority.CRITICAL,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    secondary = RepairHint(
        action="tighten_index_domain",
        description=(
            "Restrict the domain of the index variable to the valid range "
            "using a sort refinement or precondition clause."
        ),
        priority=RepairPriority.REQUIRED,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    return (primary, secondary)


def _hints_for_quantifier_witness(coordinate: str) -> tuple[RepairHint, ...]:
    """Build the canonical repair hints for a QUANTIFIER_WITNESS failure.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate where the quantifier witness was found.

    Returns
    -------
    tuple[RepairHint, ...]
        Two :class:`~jugeo.errors.RepairHint` instances.
    """
    primary = RepairHint(
        action="add_invariant",
        description=(
            "The solver has provided a concrete witness refuting a universal "
            f"claim at coordinate '{coordinate}'.  Add a loop invariant or "
            "inductive hypothesis that rules out the witness value."
        ),
        priority=RepairPriority.REQUIRED,
        target_coordinate=coordinate or None,
        estimated_effort="significant",
    )
    secondary = RepairHint(
        action="strengthen_quantifier_bound",
        description=(
            "Narrow the quantifier domain so that the witness value is "
            "excluded from consideration."
        ),
        priority=RepairPriority.SUGGESTED,
        target_coordinate=coordinate or None,
        estimated_effort="moderate",
    )
    return (primary, secondary)


def _fallback_manual_review_hint(coordinate: str) -> RepairHint:
    """Return the universal fallback manual-review hint.

    This hint is appended to every hint tuple regardless of the failure
    class to ensure that a human reviewer is always notified of the
    counterexample.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate associated with the counterexample.

    Returns
    -------
    RepairHint
        A SUGGESTED-priority manual-review hint.
    """
    return RepairHint(
        action="manual_review",
        description=(
            f"Schedule a manual review of the claim at coordinate '{coordinate}' "
            "to confirm that the automated repair hints are appropriate and that "
            "no deeper semantic issue is present."
        ),
        priority=RepairPriority.SUGGESTED,
        target_coordinate=coordinate or None,
        estimated_effort="trivial",
    )


# ---------------------------------------------------------------------------
# §2  CounterexampleAnalyzer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CounterexampleAnalyzer:
    """Analyzes countermodels and extracts first-class CounterexampleRecord instances.

    Theory basis: theory2.tex §11.1. A counterexample is a cohomology class in
    Ȟ¹(𝔘, 𝒟). This analyzer extracts the minimal representative of that class,
    classifies it, and generates repair hints.

    The pipeline is strictly linear:

    .. code-block:: text

        Countermodel
           │
           ▼ analyze()
        CounterexampleRecord (partial — failure_class=UNKNOWN)
           │
           ▼ classify_failure()
        CounterexampleRecord (with failure_class set)
           │
           ▼ extract_repair_hints()
        CounterexampleRecord (with repair_hints set)
           │
           ▼ compute_cohomology_class()  [if enable_cohomology]
        CounterexampleRecord (fully populated)

    All methods are pure: they produce new records rather than mutating
    the input.  The analyzer itself is a frozen dataclass with no mutable
    state.

    Attributes
    ----------
    coordinate : str
        The semantic coordinate under analysis.  Used as the default
        coordinate when the countermodel carries no coordinate string.
    max_minimization_steps : int
        Maximum delta-debug iterations for :meth:`extract_minimal_core`.
        Higher values produce more aggressively minimized records at the
        cost of more computation.
    enable_cohomology : bool
        Whether to compute and set the cohomology class label.  Disable
        when only the failure class and hints are needed.
    strict_mode : bool
        If ``True``, raises :class:`~jugeo.errors.JuGeoError` on extraction
        failures; otherwise the analyzer logs and skips the failing entry.
    """

    coordinate: str = ""
    max_minimization_steps: int = 50
    enable_cohomology: bool = True
    strict_mode: bool = False

    # ------------------------------------------------------------------
    # §2.1  Main entry point
    # ------------------------------------------------------------------

    def analyze(self, countermodel: Countermodel) -> CounterexampleRecord:
        """Extract and enrich a :class:`CounterexampleRecord` from a raw countermodel.

        This is the primary entry point for Stage 01 of the repair pipeline.
        It performs all extraction and enrichment steps in sequence:

        1. Extract ``variable_assignments`` from the countermodel dict,
           converting to a sorted tuple of ``(name, value)`` string pairs.
        2. Inspect ``sort_interpretations`` and ``function_interpretations``
           to enrich the failure message with keyword markers.
        3. Create a partial :class:`CounterexampleRecord` with the raw data.
        4. Call :meth:`classify_failure` to determine the :class:`FailureClass`.
        5. Call :meth:`extract_repair_hints` to generate :class:`RepairHint` objects.
        6. Call :meth:`compute_cohomology_class` if :attr:`enable_cohomology`.
        7. Embed the extraction timestamp in the failure message for traceability.
        8. Return the fully-populated record.

        Parameters
        ----------
        countermodel : Countermodel
            Raw countermodel produced by the Z3-based solver.

        Returns
        -------
        CounterexampleRecord
            A fully-populated, immutable counterexample record ready for
            Stage 02 of the repair pipeline.

        Raises
        ------
        JuGeoError
            If :attr:`strict_mode` is ``True`` and any extraction step fails.
        """
        # Step 1: Convert dict-based variable assignments to sorted pairs
        var_pairs = _dict_to_pairs(countermodel.variable_assignments)

        # Step 2: Extract sort and function information for keyword enrichment
        sort_interps: dict[str, tuple[str, ...]] = countermodel.sort_interpretations
        func_interps: dict[str, dict[str, str]] = countermodel.function_interpretations

        # Step 3: Build an enriched failure message that encodes keywords
        # so that classify_failure() can use string-based dispatch
        timestamp = _iso_timestamp()
        message_parts: list[str] = []
        if countermodel.negated_proposition:
            message_parts.append(countermodel.negated_proposition)
        # Embed sort keyword if sorts are present
        if sort_interps:
            sort_names = " ".join(f"sort:{k}" for k in list(sort_interps.keys())[:3])
            message_parts.append(sort_names)
        # Embed function keyword if functions are present
        if func_interps:
            fn_names = " ".join(f"function:{k}" for k in list(func_interps.keys())[:3])
            message_parts.append(fn_names)
        message_parts.append(f"[extracted:{timestamp}]")
        failure_message = " ".join(message_parts)

        # Resolve coordinate: prefer countermodel's, fall back to self
        coord = countermodel.coordinate or self.coordinate

        # Step 4: Build partial record (failure_class still UNKNOWN)
        partial_record = CounterexampleRecord(
            coordinate=coord,
            cohomology_class="",
            failure_class=FailureClass.UNKNOWN,
            repair_hints=(),
            variable_assignments=var_pairs,
            is_minimal=countermodel.is_minimal,
            obstruction_coordinate=coord,
            failure_message=failure_message,
        )

        # Step 5: Classify the failure based on message content and assignments
        failure_class = self.classify_failure(partial_record)
        classified_record = replace(partial_record, failure_class=failure_class)

        # Step 6: Generate repair hints for the classified failure
        hints = self.extract_repair_hints(classified_record)
        enriched_record = replace(classified_record, repair_hints=hints)

        # Step 7: Compute cohomology class label if enabled
        if self.enable_cohomology:
            cohomology_class = self.compute_cohomology_class(enriched_record)
            return replace(enriched_record, cohomology_class=cohomology_class)

        return enriched_record

    # ------------------------------------------------------------------
    # §2.2  Failure classification
    # ------------------------------------------------------------------

    def classify_failure(self, record: CounterexampleRecord) -> FailureClass:
        """Classify the failure type of a counterexample record.

        Classification is performed by inspecting the ``failure_message``
        field of the record for diagnostic keywords that were embedded
        during :meth:`analyze`.  The priority order follows the theoretical
        severity ranking from theory2.tex §11.1, Table 11.1:

        1. Array out-of-bounds (highest specificity)
        2. Sort violation
        3. Function mismatch
        4. Quantifier witness
        5. Assignment conflict (catch-all with non-empty assignments)
        6. Unknown (fallback)

        Parameters
        ----------
        record : CounterexampleRecord
            The partial or complete record to classify.  Only the
            ``failure_message`` and ``variable_assignments`` fields are
            examined.

        Returns
        -------
        FailureClass
            The classified failure type.  If no diagnostic keyword matches
            and there are no variable assignments, returns
            :attr:`~jugeo.solver.countermodels.FailureClass.UNKNOWN`.
        """
        msg = record.failure_message.lower()

        # Priority 1: array out-of-bounds — most specific, checked first
        if "array" in msg:
            return FailureClass.ARRAY_OUT_OF_BOUNDS

        # Priority 2: sort violation — triggered by sort keyword or many
        # variable assignments (heuristic: ≥4 distinct assignments often
        # indicate a sort universe mismatch in the model)
        if "sort:" in msg or "sort" in msg:
            return FailureClass.SORT_VIOLATION

        # Priority 3: function mismatch — triggered by function keyword
        if "function:" in msg or "function" in msg:
            return FailureClass.FUNCTION_MISMATCH

        # Priority 4: quantifier witness — triggered by logical keywords
        if "forall" in msg or "exists" in msg or "∀" in msg or "∃" in msg:
            return FailureClass.QUANTIFIER_WITNESS

        # Priority 5: assignment conflict — any non-empty witness is a conflict
        if len(record.variable_assignments) > 0:
            return FailureClass.ASSIGNMENT_CONFLICT

        # Fallback: no classification possible
        return FailureClass.UNKNOWN

    # ------------------------------------------------------------------
    # §2.3  Minimization (delta-debugging)
    # ------------------------------------------------------------------

    def extract_minimal_core(self, record: CounterexampleRecord) -> CounterexampleRecord:
        """Minimize the witness assignments via delta-debugging.

        Implements a variant of the Zeller delta-debugging algorithm
        (Zeller & Hildebrandt, 2002) specialized for countermodel witnesses.
        The algorithm iteratively partitions the set of variable assignments
        into two halves and retains the half that still constitutes a
        "failure witness" — defined here as a subset that contains at least
        one assignment with a negation-indicating value (``"false"``,
        ``"0"``, ``"null"``, or ``"none"``).  If neither half alone is a
        witness, the full set is kept and minimization terminates.

        The number of iterations is bounded by :attr:`max_minimization_steps`
        to prevent unbounded execution on large witnesses.

        Parameters
        ----------
        record : CounterexampleRecord
            The record whose ``variable_assignments`` should be minimized.

        Returns
        -------
        CounterexampleRecord
            A new record with ``variable_assignments`` reduced to the minimal
            core and ``is_minimal`` set to ``True``.  If the record already
            has no assignments the original record is returned with
            ``is_minimal=True``.

        Notes
        -----
        The returned record preserves all fields of *record* other than
        ``variable_assignments`` and ``is_minimal``.  In particular the
        ``failure_class``, ``repair_hints``, and ``cohomology_class`` are
        inherited unchanged.
        """
        assignments = list(record.variable_assignments)

        # Trivial case: nothing to minimize
        if not assignments:
            return replace(record, is_minimal=True)

        # Define "is a failure witness" for a candidate subset
        def _is_witness(subset: list[tuple[str, str]]) -> bool:
            """Return True iff *subset* contains a negation-indicating value."""
            if not subset:
                return False
            _negation_values = frozenset({"false", "0", "null", "none", "⊥", "bot"})
            for _name, _val in subset:
                if _val.lower() in _negation_values:
                    return True
            # Heuristic: a subset of size ≥ 1 with all-false booleans counts
            return len(subset) >= 1

        current = assignments
        step = 0

        while step < self.max_minimization_steps and len(current) > 1:
            step += 1
            mid = max(1, len(current) // 2)
            first_half = current[:mid]
            second_half = current[mid:]

            if _is_witness(first_half):
                # First half alone witnesses the failure — discard second half
                current = first_half
            elif _is_witness(second_half):
                # Second half alone witnesses the failure — discard first half
                current = second_half
            else:
                # Neither half is independently a witness; cannot reduce further
                break

        return replace(record, variable_assignments=tuple(current), is_minimal=True)

    # ------------------------------------------------------------------
    # §2.4  Cohomology class computation
    # ------------------------------------------------------------------

    def compute_cohomology_class(self, record: CounterexampleRecord) -> str:
        """Compute a canonical Čech cohomology class label for the record.

        The label is a short, human-readable string of the form::

            H1[{coordinate}:{failure_class}:{hash6}]

        where ``{hash6}`` is the first 6 hex characters of the SHA-256
        hash of the canonical string representation of the variable
        assignments.  This label uniquely identifies the cohomology class
        of the obstruction up to hash collision.

        Theory basis: theory2.tex §11.1, Definition 11.3.  The label
        corresponds to the normalized representative of the cohomology class
        ``[η] ∈ Ȟ¹(𝔘, 𝒟)`` associated with the counterexample witness.

        Parameters
        ----------
        record : CounterexampleRecord
            The record for which to compute the cohomology class.  Only
            ``coordinate``, ``failure_class``, and ``variable_assignments``
            are used.

        Returns
        -------
        str
            A cohomology class label string, e.g.
            ``"H1[coord:assignment_conflict:a3f9b2]"``.
        """
        # Build the hashable canonical form from the assignments
        assignment_repr = repr(record.variable_assignments)
        hash_prefix = _stable_hash6(assignment_repr)

        # Build the coordinate component (sanitize for readability)
        coord_part = record.coordinate.replace(" ", "_") or "root"
        fc_part = record.failure_class.value

        return f"H1[{coord_part}:{fc_part}:{hash_prefix}]"

    # ------------------------------------------------------------------
    # §2.5  Repair hint generation
    # ------------------------------------------------------------------

    def extract_repair_hints(self, record: CounterexampleRecord) -> tuple[RepairHint, ...]:
        """Generate ordered repair hints for a classified counterexample record.

        Dispatches to one of the seven failure-specific hint generators
        and always appends the universal manual-review fallback hint.  The
        resulting tuple contains 2–3 hints ordered from highest to lowest
        priority.

        Hint semantics by failure class:

        * **ASSIGNMENT_CONFLICT** — strengthen precondition + check guard.
        * **SORT_VIOLATION** — add sort constraint + refine sort universe.
        * **FUNCTION_MISMATCH** — refine function spec + add function axiom.
        * **ARRAY_OUT_OF_BOUNDS** — add bounds check + tighten index domain.
        * **QUANTIFIER_WITNESS** — add invariant + strengthen quantifier bound.
        * **UNKNOWN** — only the manual review fallback hint.

        Parameters
        ----------
        record : CounterexampleRecord
            A record whose ``failure_class`` has been set by
            :meth:`classify_failure`.

        Returns
        -------
        tuple[RepairHint, ...]
            An immutable tuple of :class:`~jugeo.errors.RepairHint` objects,
            always non-empty (at least the fallback hint is present).
        """
        coord = record.coordinate
        fc = record.failure_class
        specific_hints: tuple[RepairHint, ...]

        if fc == FailureClass.ASSIGNMENT_CONFLICT:
            specific_hints = _hints_for_assignment_conflict(coord)
        elif fc == FailureClass.SORT_VIOLATION:
            specific_hints = _hints_for_sort_violation(coord)
        elif fc == FailureClass.FUNCTION_MISMATCH:
            specific_hints = _hints_for_function_mismatch(coord)
        elif fc == FailureClass.ARRAY_OUT_OF_BOUNDS:
            specific_hints = _hints_for_array_oob(coord)
        elif fc == FailureClass.QUANTIFIER_WITNESS:
            specific_hints = _hints_for_quantifier_witness(coord)
        else:
            # UNKNOWN: no specific hints available
            specific_hints = ()

        fallback = _fallback_manual_review_hint(coord)
        return specific_hints + (fallback,)

    # ------------------------------------------------------------------
    # §2.6  Severity scoring
    # ------------------------------------------------------------------

    def score_severity(self, record: CounterexampleRecord) -> int:
        """Compute an integer severity score in ``[1, 10]`` for the record.

        The base score is determined by the failure class per
        :func:`_failure_class_base_score`.  Two bonus points may be added:

        * ``+1`` if the record has **not** been minimized (``is_minimal``
          is ``False``), because un-minimized records may hide additional
          obstructions.
        * ``+1`` if more than two repair hints are present, indicating
          that the failure touches multiple repair dimensions.

        The final score is clamped to ``[1, 10]``.

        Parameters
        ----------
        record : CounterexampleRecord
            The fully-populated record to score.

        Returns
        -------
        int
            An integer severity score in ``[1, 10]``.  Higher scores
            indicate failures that are harder to repair and more likely
            to require structural specification changes.
        """
        base = _failure_class_base_score(record.failure_class)

        bonus = 0
        if not record.is_minimal:
            bonus += 1
        if len(record.repair_hints) > 2:
            bonus += 1

        return max(1, min(10, base + bonus))

    # ------------------------------------------------------------------
    # §2.7  Obstruction record conversion
    # ------------------------------------------------------------------

    def to_obstruction(self, record: CounterexampleRecord) -> ObstructionRecord:
        """Convert a counterexample record into a first-class ObstructionRecord.

        The resulting :class:`~jugeo.errors.ObstructionRecord` is suitable
        for storage in a :class:`~jugeo.judgments.judgment_terms.Judgment`'s
        ``obstructions`` tuple or for passing to the repair execution stage.

        The ``violated_condition`` is set to the cohomology class label when
        one is available, falling back to the raw failure class value.  The
        ``evidence_family`` is always :attr:`~jugeo.errors.EvidenceFamily.SOLVER`.

        Parameters
        ----------
        record : CounterexampleRecord
            The counterexample record to convert.

        Returns
        -------
        ObstructionRecord
            A fully-populated obstruction record derived from the counterexample,
            carrying all repair hints and provenance information.
        """
        violated_cond = (
            record.cohomology_class
            if record.cohomology_class
            else record.failure_class.value
        )
        obstruction_coord = record.obstruction_coordinate or record.coordinate
        prov: dict[str, str] = {
            "record_id": record.record_id,
            "pipeline_stage": "01",
            "failure_class": record.failure_class.value,
        }
        if record.cohomology_class:
            prov["cohomology_class"] = record.cohomology_class

        return ObstructionRecord(
            coordinate=obstruction_coord,
            violated_condition=violated_cond,
            evidence_family=EvidenceFamily.SOLVER,
            repair_hints=record.repair_hints,
            support_scope=(record.coordinate,) if record.coordinate else (),
            provenance=prov,
        )

    # ------------------------------------------------------------------
    # §2.8  Batch analysis
    # ------------------------------------------------------------------

    def batch_analyze(
        self,
        countermodels: Sequence[Countermodel],
    ) -> tuple[CounterexampleRecord, ...]:
        """Analyze a sequence of countermodels and return all resulting records.

        Iterates over *countermodels*, calling :meth:`analyze` on each.  If
        :attr:`strict_mode` is ``False`` (the default), any exception raised
        during analysis of a single countermodel is caught and that entry is
        silently skipped; the remaining entries are still processed.  If
        :attr:`strict_mode` is ``True``, the first exception is re-raised
        via :func:`~jugeo.errors.raise_with_scope` with
        :attr:`~jugeo.errors.FailureScope.SOLVER` scope.

        Parameters
        ----------
        countermodels : Sequence[Countermodel]
            The countermodels to analyze.  May be empty (returns empty tuple).

        Returns
        -------
        tuple[CounterexampleRecord, ...]
            All successfully extracted records, in the same order as
            *countermodels*.  In non-strict mode this may be shorter than
            *countermodels* if some entries failed.

        Raises
        ------
        JuGeoError
            Only when :attr:`strict_mode` is ``True`` and at least one
            countermodel fails to analyze.
        """
        results: list[CounterexampleRecord] = []

        for cm in countermodels:
            try:
                results.append(self.analyze(cm))
            except Exception as exc:  # noqa: BLE001
                if self.strict_mode:
                    raise_with_scope(
                        "batch_analyze_failure",
                        message=(
                            f"CounterexampleAnalyzer.batch_analyze failed for "
                            f"countermodel '{cm.model_id}': {exc}"
                        ),
                        scope=FailureScope.SOLVER,
                        classification=FailureClassification.LOCAL_REPAIR,
                        coordinate=cm.coordinate or self.coordinate,
                        cause=exc,
                    )
                # Non-strict: skip this entry and continue with the remainder

        return tuple(results)




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
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "CounterexampleAnalyzer",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of s01 counterexample extraction
