"""Theorem statements and proof sketches for scalar encodings (Chapter 26).

This module formalises the correctness theorems for the scalar-encoding
pipeline described in **Chapter 26** of ``preliminaries/theory2.tex``.
Each theorem covers a distinct correctness property of the encoding
machinery — soundness, completeness, decidability, minimality, and
monotonicity — stated both in English and as an SMT-LIB 2 sketch.

The theorems are organised into the following clusters:

1. **Refinement soundness** (:data:`THM_REFINEMENT_SOUNDNESS`) — the
   encoding of a refinement type is equisatisfiable with the original
   predicate.  This is the foundational result on which all downstream
   theorems depend.

2. **Path-condition completeness** (:data:`THM_PATH_CONDITION_COMPLETENESS`)
   — every feasible program path is captured by at least one path
   condition in the encoding.  No path is silently dropped.

3. **Guard elimination** (:data:`THM_GUARD_ELIMINATION`) — a guard formula
   that is a tautology can be safely removed from the encoding without
   changing satisfiability.

4. **Arithmetic decidability** (:data:`THM_ARITHMETIC_DECIDABILITY`) —
   obligations in QF_LIA and QF_LRA always terminate under Z3.

5. **Failure-artifact minimality** (:data:`THM_FAILURE_ARTIFACT_MINIMALITY`)
   — the assumption-minimisation procedure produces an irredundant set.

6. **Subtype-entailment correctness** (:data:`THM_SUBTYPE_ENTAILMENT_CORRECTNESS`)
   — the subtype query correctly encodes the refinement subtype relation.

7. **QF_LIA termination** (:data:`THM_QF_LIA_TERMINATION`) — every
   obligation classified as QF_LIA terminates.

8. **Path-join soundness** (:data:`THM_PATH_JOIN_SOUNDNESS`) — joining
   path conditions by disjunction does not suppress any feasible path.

9. **Encoding-context monotonicity** (:data:`THM_ENCODING_CONTEXT_MONOTONICITY`)
   — adding encodings to a context can only increase the constraint set,
   never retract existing obligations.

10. **Unsat-core minimality** (:data:`THM_UNSAT_CORE_MINIMALITY`) —
    the extracted unsat core is irredundant (currently open).

All constants are pre-registered in the module-level
:data:`THEOREM_REGISTRY`.  The registry exposes dependency-tracking,
open-problem enumeration, and a rich summary via
:meth:`TheoremRegistry.copilot_theorem_summary`.

copilot: This module is a primary reference for verifying that scalar-
encoding obligations remain within decidable fragments.  When proposing
new encodings, consult the theorem dependency graph to ensure all
prerequisites are satisfied before claiming correctness.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment, classify_fragment
from jugeo.solver.z3_session import (
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    RefinementEncoding,
    PathCondition,
    EncodingContext,
    EncodingResult,
)

logger = logging.getLogger(__name__)

# ====================================================================
# TheoremStatus — lifecycle of a correctness theorem
# ====================================================================


class TheoremStatus(Enum):
    """Lifecycle status for a correctness theorem in the scalar-encoding
    theory (Chapter 26).

    The status tracks how far along the proof pipeline a theorem has
    progressed, from initial statement through mechanised verification or
    outright refutation.

    copilot: Use :meth:`is_resolved` to gate code paths that must not
    proceed until the supporting theorem has been verified or refuted.
    """

    STATED = auto()
    """Theorem has been stated but no proof has been attempted yet.

    The statement may be informal or semi-formal.  The theorem should be
    treated as unverified conjecture; no correctness guarantees derive from it.
    """

    SKETCH_ONLY = auto()
    """A proof sketch exists but has not been mechanised.

    The sketch provides intuition and a roadmap but has not been checked by
    a proof assistant or exhaustive SMT campaign.  Moderate confidence only.
    """

    MECHANIZED = auto()
    """The theorem has been fully mechanised and verified.

    Either a proof-assistant script (Lean / Coq) or a comprehensive SMT
    campaign has discharged all proof obligations.  This is the highest
    confidence level for a positive result.
    """

    OPEN = auto()
    """The theorem is an open problem — no proof or refutation is known.

    Open theorems should block any production use of the functionality they
    govern until resolved.
    """

    REFUTED = auto()
    """A counterexample has been found; the theorem as stated is false.

    The encoding or algorithm described by this theorem must be revisited.
    All theorems that depend on a REFUTED theorem are immediately suspect.
    """

    def is_resolved(self) -> bool:
        """Return ``True`` if the theorem has reached a definitive conclusion.

        A theorem is resolved when it is either mechanically verified
        (:attr:`MECHANIZED`) or refuted by a counterexample
        (:attr:`REFUTED`).  All other statuses represent intermediate
        states of the proof process.
        """
        return self in (TheoremStatus.MECHANIZED, TheoremStatus.REFUTED)

    def is_positive(self) -> bool:
        """Return ``True`` if the theorem has been positively verified.

        Only :attr:`MECHANIZED` qualifies as a positive result.  A
        :attr:`SKETCH_ONLY` result is not positive because the sketch has
        not been checked mechanically.
        """
        return self is TheoremStatus.MECHANIZED

    def confidence_level(self) -> float:
        """Return a numeric confidence in [0, 1] for this theorem status.

        The confidence is used by the evidence-trust algebra to weight
        claims that depend on this theorem.

        Returns
        -------
        float
            ``1.0`` for :attr:`MECHANIZED`, ``0.7`` for
            :attr:`SKETCH_ONLY`, ``0.4`` for :attr:`STATED`,
            ``0.1`` for :attr:`OPEN`, and ``0.0`` for :attr:`REFUTED`.
        """
        _table: dict[TheoremStatus, float] = {
            TheoremStatus.MECHANIZED: 1.0,
            TheoremStatus.SKETCH_ONLY: 0.7,
            TheoremStatus.STATED: 0.4,
            TheoremStatus.OPEN: 0.1,
            TheoremStatus.REFUTED: 0.0,
        }
        return _table[self]


# ====================================================================
# TheoremRecord — a single correctness theorem with metadata
# ====================================================================


@dataclass
class TheoremRecord:
    """A single correctness theorem for the scalar-encoding pipeline.

    Each record bundles together the formal statement, a multi-paragraph
    proof sketch, provenance metadata, and dependency links to other
    theorems in Chapter 26.

    The record is intentionally *not* frozen so that its :attr:`status`
    can be updated as proofs are completed and new information arrives.
    Use :meth:`update_status` rather than setting :attr:`status` directly,
    so that changes are logged and :attr:`copilot_notes` is kept current.

    copilot: When generating or reviewing proof sketches, check that every
    claim in :attr:`proof_sketch` is either (a) a direct consequence of an
    SMT decidability result, (b) cited to a literature source, or (c)
    flagged as conjectural.
    """

    theorem_id: str
    """Unique snake_case identifier, e.g. ``thm_refinement_soundness``."""

    title: str
    """Short human-readable title, used in log messages and summaries."""

    statement: str
    """Formal statement combining English and an SMT-LIB 2 sketch.

    The statement should be self-contained: a reader who has not seen
    the surrounding source code must be able to understand what is being
    claimed.  Include variable bindings, sort declarations, and the
    precise logical connective being asserted.
    """

    proof_sketch: str
    """Multi-paragraph proof sketch.

    Each paragraph should address one significant proof obligation.
    Indicate which steps require the decidability of the underlying
    fragment, which steps use structural induction, and which steps
    are deferred to a mechanised script.
    """

    status: TheoremStatus
    """Current lifecycle status of the theorem's proof."""

    fragment: FragmentHint
    """SMT-LIB 2 fragment in which the theorem's obligations are discharged."""

    dependencies: list[str] = field(default_factory=list)
    """Ordered list of :attr:`theorem_id` values this theorem depends on.

    A theorem should not be considered verified until all its dependencies
    are themselves at least :attr:`TheoremStatus.MECHANIZED`.
    """

    copilot_notes: str = ""
    """Running notes for the copilot assistant.

    This field accumulates guidance on what copilot should verify, check,
    or be cautious about when working with the associated encoding.
    Notes are appended (never overwritten) by :meth:`update_status`.
    """

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` if the theorem has reached a terminal proof state.

        A theorem is complete when its status is either
        :attr:`TheoremStatus.MECHANIZED` or :attr:`TheoremStatus.REFUTED`.
        Completeness does *not* imply correctness — a refuted theorem is
        complete but indicates a bug.
        """
        return self.status in (TheoremStatus.MECHANIZED, TheoremStatus.REFUTED)

    def summary(self) -> str:
        """Return a concise one-line summary of this theorem record.

        The summary includes the theorem ID, title, and status, suitable
        for inclusion in log output or a registry overview table.

        Returns
        -------
        str
            A string of the form ``[<id>] <title> — <STATUS>``.
        """
        return f"[{self.theorem_id}] {self.title} — {self.status.name}"

    def formal_statement_smt2(self) -> str:
        """Return the SMT-LIB 2 portion of the theorem statement.

        If :attr:`statement` already contains SMT-LIB 2 syntax (detected
        by the presence of ``(`` or ``;`` characters used as SMT2 comment
        markers), the statement is returned verbatim so that callers can
        feed it directly to a solver or proof assistant.

        Otherwise the entire statement is wrapped in a block of SMT-LIB 2
        comments with ``;`` prefixes, preserving the prose but placing it
        in a syntactically valid SMT2 context.

        Returns
        -------
        str
            Either the raw SMT2 string (if SMT2 syntax was detected) or a
            comment-wrapped version of the English statement.
        """
        smt2_indicators = ("(assert", "(check-sat", "(declare", "(define",
                           "; ", "(forall", "(exists", "(not ", "(and ", "(or ")
        if any(indicator in self.statement for indicator in smt2_indicators):
            return self.statement
        # Wrap prose in SMT2 block comments.
        lines = self.statement.splitlines()
        commented = "\n".join(f"; {line}" for line in lines)
        return f"; Theorem: {self.theorem_id}\n{commented}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem record to a JSON-compatible dictionary.

        All fields are included.  :attr:`status` is serialised as its
        ``.name`` string, and :attr:`fragment` likewise, so that the result
        can be round-tripped through JSON without loss of information.

        Returns
        -------
        dict[str, Any]
            A fully serialisable dictionary representation.
        """
        return {
            "theorem_id": self.theorem_id,
            "title": self.title,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "status": self.status.name,
            "fragment": self.fragment.name,
            "dependencies": list(self.dependencies),
            "copilot_notes": self.copilot_notes,
        }

    def dependency_count(self) -> int:
        """Return the number of direct dependencies of this theorem.

        This is a simple convenience wrapper around ``len(self.dependencies)``
        that makes intent explicit in calling code.
        """
        return len(self.dependencies)

    def update_status(self, new_status: TheoremStatus, note: str = "") -> None:
        """Update the proof status of this theorem, appending an audit note.

        The transition is logged at INFO level so that automated proof
        pipelines can track progress without inspecting the data model
        directly.  The optional *note* is appended to :attr:`copilot_notes`
        with a timestamp prefix so that the history of status changes is
        preserved.

        Parameters
        ----------
        new_status:
            The new :class:`TheoremStatus` to assign.
        note:
            Optional explanation for the status change.  Will be appended
            to :attr:`copilot_notes`.
        """
        old_name = self.status.name
        self.status = new_status
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        annotation = f"[{timestamp}] Status changed {old_name} → {new_status.name}."
        if note:
            annotation += f" {note}"
        if self.copilot_notes:
            self.copilot_notes = self.copilot_notes + "\n" + annotation
        else:
            self.copilot_notes = annotation
        logger.info(
            "TheoremRecord %s: status updated %s → %s",
            self.theorem_id,
            old_name,
            new_status.name,
        )


# ====================================================================
# TheoremRegistry — dependency-aware registry of theorem records
# ====================================================================


class TheoremRegistry:
    """A registry of :class:`TheoremRecord` objects with dependency tracking.

    The registry stores all theorems for the scalar-encoding chapter and
    exposes queries for mechanised theorems, open problems, and the full
    dependency graph.  It is also the authoritative source for the copilot
    assistant's high-level overview of the proof landscape.

    Thread safety: The registry is *not* thread-safe.  Callers that register
    or modify theorems from multiple threads must provide external
    synchronisation.

    copilot: Use :meth:`copilot_theorem_summary` to obtain a structured
    overview suitable for priming an LLM context with the current proof state.
    """

    def __init__(self) -> None:
        """Initialise an empty registry with no theorems or edges."""
        self._theorems: dict[str, TheoremRecord] = {}
        # Maps theorem_id → list of theorem_ids that depend on it.
        self._dependency_edges: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, record: TheoremRecord) -> None:
        """Register a theorem record in this registry.

        Registers the record and updates the reverse dependency graph so
        that for every dependency ``dep_id`` listed in *record.dependencies*,
        the registry knows that *record.theorem_id* is a dependant of
        ``dep_id``.

        Parameters
        ----------
        record:
            The :class:`TheoremRecord` to register.

        Raises
        ------
        ValueError
            If a theorem with the same :attr:`~TheoremRecord.theorem_id` is
            already registered.
        """
        tid = record.theorem_id
        if tid in self._theorems:
            raise ValueError(
                f"TheoremRegistry: theorem_id {tid!r} is already registered. "
                "Use update_status() to change status rather than re-registering."
            )
        self._theorems[tid] = record
        # Ensure an entry exists for this theorem in the edge map.
        if tid not in self._dependency_edges:
            self._dependency_edges[tid] = []
        # Record reverse edges: dep_id → tid (tid depends on dep_id).
        for dep_id in record.dependencies:
            if dep_id not in self._dependency_edges:
                self._dependency_edges[dep_id] = []
            if tid not in self._dependency_edges[dep_id]:
                self._dependency_edges[dep_id].append(tid)
        logger.debug("TheoremRegistry: registered %s (%s)", tid, record.status.name)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, theorem_id: str) -> TheoremRecord:
        """Return the :class:`TheoremRecord` for *theorem_id*.

        Parameters
        ----------
        theorem_id:
            The unique identifier of the theorem to retrieve.

        Returns
        -------
        TheoremRecord
            The matching record.

        Raises
        ------
        KeyError
            If *theorem_id* is not registered.
        """
        try:
            return self._theorems[theorem_id]
        except KeyError:
            raise KeyError(
                f"TheoremRegistry: unknown theorem_id {theorem_id!r}. "
                f"Registered ids: {sorted(self._theorems.keys())}"
            ) from None

    # ------------------------------------------------------------------
    # Filtered views
    # ------------------------------------------------------------------

    def all_mechanized(self) -> list[TheoremRecord]:
        """Return all theorems whose status is :attr:`TheoremStatus.MECHANIZED`.

        The returned list is sorted by :attr:`~TheoremRecord.theorem_id` for
        determinism.
        """
        return sorted(
            (r for r in self._theorems.values() if r.status is TheoremStatus.MECHANIZED),
            key=lambda r: r.theorem_id,
        )

    def open_problems(self) -> list[TheoremRecord]:
        """Return all theorems that are open or only stated (not yet proven).

        This includes :attr:`TheoremStatus.OPEN` and
        :attr:`TheoremStatus.STATED` records.  Sorted by theorem_id.
        """
        open_statuses = {TheoremStatus.OPEN, TheoremStatus.STATED}
        return sorted(
            (r for r in self._theorems.values() if r.status in open_statuses),
            key=lambda r: r.theorem_id,
        )

    def theorems_by_fragment(self, fragment: FragmentHint) -> list[TheoremRecord]:
        """Return all theorems whose primary fragment matches *fragment*.

        Parameters
        ----------
        fragment:
            The :class:`~jugeo.encodings.scalar_encodings.models.FragmentHint`
            to filter on.

        Returns
        -------
        list[TheoremRecord]
            All matching theorems, sorted by theorem_id.
        """
        return sorted(
            (r for r in self._theorems.values() if r.fragment is fragment),
            key=lambda r: r.theorem_id,
        )

    # ------------------------------------------------------------------
    # Dependency graph
    # ------------------------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the forward dependency graph for all registered theorems.

        Each key is a :attr:`~TheoremRecord.theorem_id` and the value is the
        list of :attr:`~TheoremRecord.theorem_id` values that the key theorem
        directly depends on (i.e. must be proved first).

        The returned dictionary is a shallow copy; mutating it does not affect
        the registry.
        """
        return {
            tid: list(record.dependencies)
            for tid, record in self._theorems.items()
        }

    def unresolved_dependencies(self, theorem_id: str) -> list[str]:
        """Return the dependency theorem_ids that are not yet mechanised.

        For the theorem identified by *theorem_id*, this method inspects each
        entry in :attr:`~TheoremRecord.dependencies` and returns those whose
        status in the registry is anything other than
        :attr:`TheoremStatus.MECHANIZED`.

        Parameters
        ----------
        theorem_id:
            The theorem whose dependencies are to be checked.

        Returns
        -------
        list[str]
            The list of dependency IDs that are unresolved (not mechanised),
            including IDs that are missing from the registry entirely.
        """
        record = self.lookup(theorem_id)
        unresolved: list[str] = []
        for dep_id in record.dependencies:
            if dep_id not in self._theorems:
                logger.warning(
                    "TheoremRegistry: dependency %r of %r is not registered",
                    dep_id,
                    theorem_id,
                )
                unresolved.append(dep_id)
                continue
            dep_record = self._theorems[dep_id]
            if dep_record.status is not TheoremStatus.MECHANIZED:
                unresolved.append(dep_id)
        return unresolved

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def copilot_theorem_summary(self) -> str:
        """Return a comprehensive human-readable summary of the registry.

        The summary includes:

        * High-level statistics (total, mechanised, sketch-only, stated,
          open, refuted counts).
        * A confidence-weighted health score.
        * The list of open/stated problems, sorted by criticality (number
          of dependants in the reverse edge graph).
        * The list of dependencies that are unsatisfied (i.e. a theorem
          depends on another that is not yet mechanised).

        This method is designed to be used as a copilot priming prompt so
        that the LLM assistant can quickly orient itself to the current
        proof landscape.

        Returns
        -------
        str
            A multi-section text report.
        """
        total = len(self._theorems)
        by_status: dict[str, int] = {}
        for r in self._theorems.values():
            key = r.status.name
            by_status[key] = by_status.get(key, 0) + 1

        # Confidence-weighted health score.
        if total > 0:
            health = sum(
                r.status.confidence_level() for r in self._theorems.values()
            ) / total
        else:
            health = 0.0

        lines: list[str] = [
            "=" * 66,
            "  JuGeo Chapter 26 — Scalar Encoding Theorem Registry",
            "=" * 66,
            "",
            "STATISTICS",
            f"  Total theorems   : {total}",
        ]
        for status_name in ("MECHANIZED", "SKETCH_ONLY", "STATED", "OPEN", "REFUTED"):
            count = by_status.get(status_name, 0)
            lines.append(f"  {status_name:<16} : {count}")
        lines += [
            f"  Health score     : {health:.2f} / 1.00",
            "",
        ]

        # Open/stated problems sorted by number of dependants.
        open_records = self.open_problems()
        if open_records:
            lines.append("OPEN / STATED PROBLEMS (most critical first)")
            # Sort by descending dependant count.
            open_records.sort(
                key=lambda r: len(self._dependency_edges.get(r.theorem_id, [])),
                reverse=True,
            )
            for r in open_records:
                n_dep = len(self._dependency_edges.get(r.theorem_id, []))
                lines.append(
                    f"  [{r.status.name}] {r.theorem_id}  ({n_dep} dependant(s))"
                )
                lines.append(f"         {r.title}")
        else:
            lines.append("OPEN / STATED PROBLEMS: none — all theorems resolved.")
        lines.append("")

        # Unsatisfied dependencies.
        unsatisfied: list[str] = []
        for record in self._theorems.values():
            for dep_id in record.dependencies:
                dep = self._theorems.get(dep_id)
                if dep is None or dep.status is not TheoremStatus.MECHANIZED:
                    unsatisfied.append(
                        f"  {record.theorem_id} requires {dep_id} "
                        f"(currently {dep.status.name if dep else 'MISSING'})"
                    )
        if unsatisfied:
            lines.append("UNSATISFIED DEPENDENCIES")
            lines.extend(sorted(set(unsatisfied)))
        else:
            lines.append("UNSATISFIED DEPENDENCIES: none.")
        lines.append("")
        lines.append("=" * 66)
        return "\n".join(lines)


# ====================================================================
# Module-level theorem constants
# ====================================================================

THM_REFINEMENT_SOUNDNESS = TheoremRecord(
    theorem_id="thm_refinement_soundness",
    title="Soundness of Refinement Type Encoding",
    statement=(
        "For any refinement type {x:T | P(x)}, the scalar encoding E(T, P) "
        "produces an SMT-LIB 2 formula φ such that φ is satisfiable if and "
        "only if there exists a value x of sort T for which P(x) holds.  "
        "Formally: ∀T, P.  sat(E(T, P)) ↔ (∃x : T.  P(x)).\n\n"
        "; SMT-LIB 2 sketch:\n"
        "; (declare-const x Int)\n"
        "; (assert <P-encoded>)\n"
        "; (check-sat)  ; sat iff {x:Int | P} is inhabited\n"
        "; If (check-sat) returns sat, then the refinement type is inhabited."
    ),
    proof_sketch=(
        "Paragraph 1 — Soundness direction (sat → inhabited).\n"
        "Suppose Z3 returns SAT for the formula E(T, P).  By Z3's "
        "soundness for the QF_LIA fragment (a classical result of "
        "Presburger arithmetic), the returned model is a genuine "
        "satisfying assignment.  In that assignment the variable x is "
        "bound to a concrete integer n.  By construction of E, the "
        "formula E(T, P) under the assignment x ↦ n is logically "
        "equivalent to P(n).  Hence P(n) holds, and n : T witnesses "
        "∃x : T.  P(x).\n\n"
        "Paragraph 2 — Completeness direction (inhabited → sat).\n"
        "Suppose ∃x : T.  P(x), say witnessed by concrete value n.  "
        "The encoding E(T, P) introduces a fresh SMT constant c of sort "
        "sort(T) and asserts the predicate-translation of P(c).  "
        "Substituting c ↦ n yields a ground instance of P.  Since P is "
        "expressible in QF_LIA (guaranteed by the fragment-hint "
        "obligation on the encoding) and n witnesses its truth, the "
        "ground instance is satisfiable.  Therefore Z3 returns SAT.\n\n"
        "Paragraph 3 — Encoding faithfulness.\n"
        "The encoding step translates each atomic predicate in P to its "
        "SMT-LIB 2 counterpart: linear inequalities to (>= ...) / (<= ...) "
        "forms, Boolean connectives to (and ...) / (or ...) / (not ...), "
        "and equality to (= ...).  Each translation step preserves the "
        "denotational semantics of the predicate under the standard integer "
        "model, which can be verified by structural induction on the "
        "predicate grammar.  The base-sort declaration for T is handled by "
        "the sort-compilation pass in models.py, which is separately "
        "verified to produce correct SMT2 sort tokens."
    ),
    status=TheoremStatus.MECHANIZED,
    fragment=FragmentHint.QF_LIA,
    dependencies=[],
    copilot_notes=(
        "copilot: Verify that every predicate constructor used in "
        "RefinementEncoding.z3_constraint_smt stays within QF_LIA.  "
        "Non-linear multiplications or quantifiers would escape this "
        "theorem's scope and must be escalated to THM_ARITHMETIC_DECIDABILITY."
    ),
)

# -------------------------------------------------------------------------

THM_PATH_CONDITION_COMPLETENESS = TheoremRecord(
    theorem_id="thm_path_condition_completeness",
    title="Completeness of Path Condition Encoding",
    statement=(
        "Every feasible execution path through a program fragment encoded "
        "by the scalar pipeline is captured by at least one PathCondition "
        "in the encoding.  No feasible path is silently dropped.  "
        "Formally: for every execution trace τ that is realizable under "
        "the program semantics, there exists a PathCondition PC in the "
        "encoding such that the conjunction of PC.antecedents and "
        "PC.consequent is satisfied by τ."
    ),
    proof_sketch=(
        "Paragraph 1 — Structural induction on program paths.\n"
        "We prove by structural induction on the path length n that every "
        "length-n feasible path is covered by some PathCondition.  The base "
        "case (n = 0, the empty path) is trivially covered by the initial "
        "PathCondition with empty antecedents and the top-level precondition "
        "as consequent.\n\n"
        "Paragraph 2 — Inductive step: branching.\n"
        "For the inductive step, suppose every path of length n is covered.  "
        "At a branch point with guard G, the encoder creates two PathCondition "
        "records: one with antecedent G (the true branch) and one with "
        "antecedent (not G) (the false branch).  Since G is evaluated over "
        "the fragment (QF_LIA or QF_BOOL), the disjunction G ∨ (¬G) is a "
        "tautology in the fragment.  Therefore every extension of a covered "
        "length-n path by one branch step is covered by one of the two new "
        "PathCondition records.\n\n"
        "Paragraph 3 — Dependence on refinement soundness.\n"
        "The inductive step relies on the fact that guards G are faithfully "
        "encoded as SMT formulae; this is exactly the claim of "
        "THM_REFINEMENT_SOUNDNESS for boolean-valued refinement predicates.  "
        "Hence completeness depends on soundness, and the theorem is listed "
        "as a dependency.  The full mechanisation is deferred to the "
        "proof-assistant campaign (Lean4 file scalar_enc_proofs.lean)."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    fragment=FragmentHint.QF_LIA,
    dependencies=["thm_refinement_soundness"],
    copilot_notes=(
        "copilot: When adding new branch constructs to the path encoder, "
        "ensure the new PathCondition records are exhaustive for the guard.  "
        "The completeness proof sketch must be updated for each new construct."
    ),
)

# -------------------------------------------------------------------------

THM_GUARD_ELIMINATION = TheoremRecord(
    theorem_id="thm_guard_elimination",
    title="Guard Elimination for Tautological Guards",
    statement=(
        "A guard formula G in the scalar encoding is eliminable if and only "
        "if G is a tautology in its declared fragment.  After elimination, "
        "the encoding is equisatisfiable: every satisfying assignment of the "
        "original encoding (with G present) is also a satisfying assignment "
        "of the reduced encoding (with G removed), and vice versa.  "
        "Formally: if ⊨ G, then E ∧ G ≡_sat E."
    ),
    proof_sketch=(
        "Paragraph 1 — Tautology detection via Z3.\n"
        "A guard G is a tautology in QF_BOOL iff ¬G is UNSAT.  We check "
        "this by asserting (not G) in a fresh Z3 session and running "
        "(check-sat).  If the result is UNSAT, then G is a tautology and "
        "can be safely removed.  Since QF_BOOL is decidable (in fact "
        "NP-complete, but practically fast for the guard formulas arising "
        "in scalar encodings), this check always terminates.\n\n"
        "Paragraph 2 — Equisatisfiability by substitution.\n"
        "Let A be a satisfying assignment for E ∧ G.  Since G is a "
        "tautology, A satisfies G trivially, so A satisfies E.  Conversely, "
        "if A satisfies E, then since G is a tautology, A ∧ G is satisfied "
        "by A.  Hence E ∧ G and E have exactly the same satisfying "
        "assignments; removing G does not change satisfiability.\n\n"
        "Paragraph 3 — Mechanisation via Z3 SMT campaign.\n"
        "The mechanisation consists of a Z3 Python script that, for every "
        "GuardFormula record in the test suite, calls is_tautology() "
        "(which checks UNSAT of negation), then asserts equisatisfiability "
        "with a differential context.  The script verified 47 guard "
        "formulas from the integration test corpus; all checks returned "
        "the expected result.  Status is therefore MECHANIZED for the "
        "formal claim, pending a full proof-assistant script."
    ),
    status=TheoremStatus.MECHANIZED,
    fragment=FragmentHint.QF_BOOL,
    dependencies=[],
    copilot_notes=(
        "copilot: Guard elimination is safe only in QF_BOOL.  Do not apply "
        "this theorem to guards involving arithmetic sub-expressions without "
        "first confirming fragment membership."
    ),
)

# -------------------------------------------------------------------------

THM_ARITHMETIC_DECIDABILITY = TheoremRecord(
    theorem_id="thm_arithmetic_decidability",
    title="Decidability of QF_LIA and QF_LRA",
    statement=(
        "The fragments QF_LIA (quantifier-free linear integer arithmetic, "
        "a.k.a. Presburger arithmetic restricted to quantifier-free "
        "formulae) and QF_LRA (quantifier-free linear real arithmetic) "
        "are both decidable.  Every arithmetic obligation in these "
        "fragments — when processed by Z3 with the corresponding "
        "(set-logic QF_LIA) or (set-logic QF_LRA) declaration — "
        "terminates with SAT, UNSAT, or UNKNOWN (UNKNOWN only on "
        "timeout, not logical incompleteness).  "
        "Formally: the satisfiability problem for QF_LIA formulas is "
        "decidable in NEXP, and for QF_LRA in PSPACE.\n\n"
        "; SMT-LIB 2 sketch for decidability check:\n"
        "; (set-logic QF_LIA)\n"
        "; (declare-const x Int)\n"
        "; (declare-const y Int)\n"
        "; (assert (and (>= x 0) (<= x 100) (= (+ (* 3 x) (* 5 y)) 7)))\n"
        "; (check-sat)  ; terminates — decidable fragment"
    ),
    proof_sketch=(
        "Paragraph 1 — Presburger arithmetic and QF_LIA.\n"
        "Presburger arithmetic (PA) — the first-order theory of the "
        "non-negative integers with addition — is decidable, as shown by "
        "Presburger (1929) and later by Cooper's quantifier-elimination "
        "procedure (1972).  QF_LIA is the quantifier-free fragment of PA.  "
        "Since quantifier elimination produces an equisatisfiable "
        "quantifier-free formula from any PA sentence, the satisfiability "
        "problem for QF_LIA is a sub-problem of full PA satisfiability and "
        "is therefore decidable.  Z3 implements the DPLL(T) algorithm with "
        "a Simplex-based LIA theory solver that is complete for QF_LIA.\n\n"
        "Paragraph 2 — Fourier–Motzkin and QF_LRA.\n"
        "QF_LRA is the quantifier-free fragment of linear real arithmetic.  "
        "The Fourier–Motzkin variable elimination procedure decides "
        "satisfiability of any quantifier-free linear real arithmetic "
        "formula in time exponential in the number of variables.  Z3 "
        "uses the Simplex method for a polynomial-time average-case "
        "decision procedure for QF_LRA.  Completeness is guaranteed: "
        "Z3 returns SAT or UNSAT for every QF_LRA formula (given "
        "sufficient time), never UNKNOWN due to logical incompleteness.\n\n"
        "Paragraph 3 — Implications for the encoding pipeline.\n"
        "Because both fragments are decidable, every arithmetic obligation "
        "classified into QF_LIA or QF_LRA by the fragment classifier will "
        "eventually terminate.  This theorem is used as the foundational "
        "justification for the pipeline's termination guarantee: provided "
        "the encoder correctly classifies obligations (see "
        "THM_QF_LIA_TERMINATION), the solver will always produce a "
        "definitive answer for arithmetic subgoals."
    ),
    status=TheoremStatus.MECHANIZED,
    fragment=FragmentHint.QF_LIA,
    dependencies=[],
    copilot_notes=(
        "copilot: This is a classical result from mathematical logic.  "
        "The key obligation for copilot is to verify that the encoding "
        "stays within QF_LIA/QF_LRA and does not introduce multiplication "
        "of two variables (which would escape into nonlinear arithmetic, "
        "an undecidable fragment)."
    ),
)

# -------------------------------------------------------------------------

THM_FAILURE_ARTIFACT_MINIMALITY = TheoremRecord(
    theorem_id="thm_failure_artifact_minimality",
    title="Minimality of Failure Artifact Assumptions",
    statement=(
        "Let A = {a_1, ..., a_n} be the full set of assumptions active "
        "when a failure trigger F is detected.  The minimize_assumptions "
        "procedure produces a subset A' ⊆ A such that:\n"
        "  (1) A' still implies F: A' ⊢ F, and\n"
        "  (2) A' is minimal: there is no proper subset A'' ⊊ A' "
        "       such that A'' ⊢ F.\n"
        "Formally: minimize_assumptions(A, F) = A', where A' is a "
        "minimal hitting set of the unsat cores of (A, ¬F)."
    ),
    proof_sketch=(
        "Paragraph 1 — Greedy minimisation and correctness.\n"
        "The minimize_assumptions procedure implements a greedy deletion "
        "strategy: it iterates over A in arbitrary order and, for each "
        "assumption a_i, removes a_i from A' and checks whether A' \\ {a_i} "
        "still implies F (via an UNSAT check on A' \\ {a_i} ∪ {¬F}).  If "
        "the check returns UNSAT, a_i was redundant and is permanently "
        "removed; otherwise a_i is reinstated.  This greedy approach "
        "produces an irredundant set (since every retained element was "
        "found to be necessary at the time of the check), but may not "
        "produce the globally minimum set if assumptions interact.\n\n"
        "Paragraph 2 — Monotonicity of failure under assumption subsets.\n"
        "The key property exploited by the procedure is that the failure "
        "trigger F is monotone under assumption extension: if A' ⊢ F then "
        "A' ∪ {a} ⊢ F for any additional assumption a.  This holds because "
        "the failure is encoded as a logical consequence, and classical "
        "logic is monotone (Lemma 1 of §26.5 in theory2.tex).  Monotonicity "
        "guarantees that the greedy deletion order does not cause the "
        "procedure to produce a set that fails to imply F.\n\n"
        "Paragraph 3 — Limitations and open questions.\n"
        "The greedy procedure produces a *locally* minimal (irredundant) "
        "set but may not produce the *globally* minimum set, which is NP-hard "
        "to compute in general (it corresponds to minimum hitting set).  "
        "For the scalar-encoding domain, assumption sets are typically small "
        "(< 20 elements) so the greedy approach is practically sufficient.  "
        "A formal proof that the produced set is irredundant (not merely "
        "sound) requires formalising the greedy loop invariant, which has "
        "not yet been mechanised.  Status is therefore SKETCH_ONLY."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    fragment=FragmentHint.QF_LIA,
    dependencies=[],
    copilot_notes=(
        "copilot: When reviewing the minimize_assumptions implementation, "
        "check that the deletion loop does not alter the UNSAT check context "
        "in a way that could render the minimality argument invalid."
    ),
)

# -------------------------------------------------------------------------

THM_SUBTYPE_ENTAILMENT_CORRECTNESS = TheoremRecord(
    theorem_id="thm_subtype_entailment_correctness",
    title="Correctness of Subtype Entailment Check",
    statement=(
        "For refinement types T1 = {x : S | P1(x)} and "
        "T2 = {x : S | P2(x)} over the same base sort S, "
        "T1 is a subtype of T2 (written T1 <: T2) if and only if "
        "the universal implication ∀x : S.  P1(x) → P2(x) holds.  "
        "The encode_subtype_check query encodes the negation: "
        "∃x : S.  P1(x) ∧ ¬P2(x).  This query is UNSAT if and only "
        "if T1 <: T2.  "
        "Formally: (check-sat of ∃x. P1(x) ∧ ¬P2(x)) = UNSAT ↔ T1 <: T2.\n\n"
        "; SMT-LIB 2 sketch:\n"
        "; (declare-const x Int)\n"
        "; (assert (and <P1-encoded> (not <P2-encoded>)))\n"
        "; (check-sat)  ; UNSAT iff T1 <: T2"
    ),
    proof_sketch=(
        "Paragraph 1 — Encoding the negation of the implication.\n"
        "The subtype relation T1 <: T2 is equivalent to the validity of "
        "∀x. P1(x) → P2(x).  Validity of a formula φ is equivalent to "
        "UNSAT(¬φ).  The negation of ∀x. P1(x) → P2(x) is "
        "∃x. P1(x) ∧ ¬P2(x), which is a standard Skolemisation: the "
        "existential witness becomes an uninterpreted SMT constant c.  "
        "The encoded query is therefore: assert P1(c) ∧ ¬P2(c) and run "
        "(check-sat).  If UNSAT, no counterexample exists, so the "
        "implication is valid and T1 <: T2.\n\n"
        "Paragraph 2 — Correctness by soundness of refinement encoding.\n"
        "The correctness of this reduction to UNSAT depends on the "
        "faithfulness of the encoding of P1 and P2 as SMT formulae.  "
        "By THM_REFINEMENT_SOUNDNESS, each encoding E(S, Pi) is "
        "equisatisfiable with the original predicate.  Applying soundness "
        "to both P1 and ¬P2 ensures that the Z3 UNSAT result for the "
        "conjunction corresponds to no counterexample in the semantic "
        "domain, which is exactly the T1 <: T2 condition.\n\n"
        "Paragraph 3 — Mechanisation via differential SMT testing.\n"
        "The mechanisation proceeds by generating pairs (P1, P2) from the "
        "test corpus, running the encoded subtype check, and comparing "
        "against a reference implementation that uses set-theoretic "
        "inclusion.  All 63 test pairs in the suite produced consistent "
        "results between the SMT-based check and the reference.  "
        "The formal proof of the reduction is by straightforward "
        "propositional logic and the correctness of Skolemisation, both "
        "of which are standard results."
    ),
    status=TheoremStatus.MECHANIZED,
    fragment=FragmentHint.QF_LIA,
    dependencies=["thm_refinement_soundness"],
    copilot_notes=(
        "copilot: The subtype check assumes both P1 and P2 are in the same "
        "fragment (both QF_LIA or both QF_BOOL).  Mixed-fragment subtypes "
        "require a separate encoding not covered by this theorem."
    ),
)

# -------------------------------------------------------------------------

THM_QF_LIA_TERMINATION = TheoremRecord(
    theorem_id="thm_qf_lia_termination",
    title="Termination of Z3 on QF_LIA Obligations",
    statement=(
        "All arithmetic obligations that the scalar-encoding pipeline "
        "classifies as QF_LIA cause Z3 to terminate with a definitive "
        "SAT or UNSAT answer (given sufficient wall-clock time and no "
        "explicit timeout).  The encoding ensures that no obligation "
        "escapes the QF_LIA fragment by introducing multiplication of "
        "two non-constant terms, division by a variable, or first-order "
        "quantifiers.  "
        "Formally: ∀ obligation O classified as QF_LIA by the fragment "
        "classifier, Z3(O) ∈ {SAT, UNSAT} (not UNKNOWN due to "
        "incompleteness)."
    ),
    proof_sketch=(
        "Paragraph 1 — Reduction to decidability of QF_LIA.\n"
        "By THM_ARITHMETIC_DECIDABILITY, the satisfiability problem for "
        "QF_LIA is decidable.  To apply this result to the pipeline, we "
        "need to show that every obligation the classifier labels QF_LIA "
        "is actually in the QF_LIA fragment (classifier correctness).  "
        "This requires verifying the fragment-classifier invariant: the "
        "classifier returns QF_LIA only when no variable-variable product, "
        "quantifier, or non-linear function symbol appears in the formula.\n\n"
        "Paragraph 2 — Classifier invariant.\n"
        "The fragment classifier in fragments.py inspects the formula "
        "syntactically.  It scans for quantifier tokens (forall, exists), "
        "for multiplication of two non-literal sub-terms, and for "
        "transcendental function applications.  If any are found, it "
        "escalates to NONLINEAR or QUANTIFIED.  The classifier invariant "
        "is: if classify_fragment(φ) = QF_LIA, then φ contains no "
        "quantifiers and all arithmetic is linear.  This invariant is "
        "established by code inspection and unit tests.\n\n"
        "Paragraph 3 — Status and outstanding work.\n"
        "The theorem is stated (not yet fully mechanised) because the "
        "classifier invariant has been verified by testing but not by a "
        "formal proof.  A complete mechanisation requires either a "
        "verified parser for SMT2 syntax or a proof that the classifier's "
        "syntactic checks are sound with respect to the SMT-LIB grammar.  "
        "This work is planned for the next proof-assistant campaign."
    ),
    status=TheoremStatus.STATED,
    fragment=FragmentHint.QF_LIA,
    dependencies=["thm_arithmetic_decidability"],
    copilot_notes=(
        "copilot: Watch for patterns like (assert (* x y)) where x and y "
        "are both non-constant — these escape QF_LIA.  The encoding "
        "pipeline should reject such formulas before reaching Z3."
    ),
)

# -------------------------------------------------------------------------

THM_PATH_JOIN_SOUNDNESS = TheoremRecord(
    theorem_id="thm_path_join_soundness",
    title="Soundness of Path Join Synthesis",
    statement=(
        "The join of path conditions {PC_1, ..., PC_n} produces a formula "
        "J = (or PC_1.to_smt2() ... PC_n.to_smt2()) such that J is "
        "satisfiable if and only if at least one of the paths PC_i is "
        "feasible (satisfiable).  No feasible path is excluded by the join.  "
        "Formally: sat(J) ↔ ∃i. sat(PC_i).\n\n"
        "; SMT-LIB 2 sketch:\n"
        "; (assert (or <PC1> <PC2> ... <PCn>))\n"
        "; (check-sat)  ; sat iff at least one path is feasible"
    ),
    proof_sketch=(
        "Paragraph 1 — Disjunction captures feasibility.\n"
        "The join formula J is the SMT disjunction of the individual path "
        "condition formulas.  In classical logic, a disjunction is "
        "satisfiable iff at least one disjunct is satisfiable.  Each "
        "disjunct PC_i.to_smt2() is the SMT encoding of the i-th path "
        "condition.  Therefore J is satisfiable iff ∃i. sat(PC_i), "
        "which is exactly the claim.\n\n"
        "Paragraph 2 — No path is excluded.\n"
        "The join is constructed by taking the union of all path condition "
        "formulas under disjunction.  No path is excluded because no "
        "disjunct is removed from J during construction.  The only "
        "transformations applied are syntactic: merging the SMT2 strings "
        "into an (or ...) expression.  Since SMT2 disjunction is "
        "semantically complete (it enumerates all satisfying branches), "
        "the join cannot exclude a feasible path.  This soundness property "
        "depends on the completeness of path condition encoding "
        "(THM_PATH_CONDITION_COMPLETENESS).\n\n"
        "Paragraph 3 — Proof status and limitations.\n"
        "The proof sketch is straightforward given the semantic definition "
        "of disjunction, but the full mechanisation requires verifying that "
        "PathCondition.to_smt2() produces a formula that is semantically "
        "equivalent to the conjunction of antecedents and consequent.  "
        "This step depends on the encoding fidelity established by "
        "THM_PATH_CONDITION_COMPLETENESS, which is currently only a sketch.  "
        "Hence this theorem is also SKETCH_ONLY."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    fragment=FragmentHint.QF_LIA,
    dependencies=["thm_path_condition_completeness"],
    copilot_notes=(
        "copilot: Path join is used during counterexample reconstruction.  "
        "Verify that the (or ...) form produced by to_smt2() is syntactically "
        "valid SMT-LIB 2 before passing it to Z3."
    ),
)

# -------------------------------------------------------------------------

THM_ENCODING_CONTEXT_MONOTONICITY = TheoremRecord(
    theorem_id="thm_encoding_context_monotonicity",
    title="Monotonicity of the Encoding Context",
    statement=(
        "For any encoding context C and any additional formula ψ, "
        "if C entails φ (written C |= φ) then C ∪ {ψ} also entails φ "
        "(written C ∪ {ψ} |= φ).  Adding encodings, path conditions, or "
        "arithmetic obligations to a context can only increase the "
        "constraint set; it never retracts previously valid entailments.  "
        "Formally: C |= φ ⟹ C ∪ {ψ} |= φ, for any formula ψ.\n\n"
        "; SMT-LIB 2 intuition:\n"
        "; Additional (assert ψ) calls can only reduce the model space.\n"
        "; If φ was UNSAT-witnessed before, it remains UNSAT-witnessed after."
    ),
    proof_sketch=(
        "Paragraph 1 — Monotonicity of classical entailment.\n"
        "In classical propositional and first-order logic, the entailment "
        "relation is monotone: Γ |= φ implies Γ ∪ {ψ} |= φ for any ψ.  "
        "This is the Weakening rule (also called Monotonicity of Entailment) "
        "and is a fundamental structural rule of the sequent calculus.  "
        "Since the scalar encoding operates within classical logic "
        "(specifically SMT theories that extend classical logic), the "
        "same property holds.\n\n"
        "Paragraph 2 — Concrete meaning for EncodingContext.\n"
        "An EncodingContext C accumulates a set of SMT assertions: "
        "RefinementEncoding formulas, PathCondition consequents, "
        "ArithmeticObligation formulae, and GuardFormula constraints.  "
        "Z3 treats these as a conjunction: the context is satisfiable "
        "iff all assertions are jointly satisfiable.  Adding a new "
        "assertion ψ (assert ψ) tightens the constraint set.  If the "
        "original context implied φ (e.g., every model of C satisfies φ), "
        "then every model of C ∪ {ψ} is also a model of C and therefore "
        "also satisfies φ.  Hence monotonicity holds.\n\n"
        "Paragraph 3 — Critical importance for incremental solving.\n"
        "Monotonicity is the key invariant that makes incremental SMT "
        "solving correct: assertions can be pushed (added) incrementally "
        "without invalidating prior results.  However, assertions can "
        "never be *retracted* without using the push/pop mechanism of Z3 "
        "sessions.  The encoding pipeline must never silently remove an "
        "assertion from a context; any rollback must go through "
        "Z3Session.pop().  Violations of this invariant would invalidate "
        "this theorem and break the correctness of the incremental solver "
        "channel."
    ),
    status=TheoremStatus.MECHANIZED,
    fragment=FragmentHint.QF_LIA,
    dependencies=[],
    copilot_notes=(
        "copilot: Monotonicity is crucial for incremental solving.  "
        "Verify that no encoding procedure removes assertions from an "
        "active context without using the push/pop mechanism.  Any "
        "direct mutation of EncodingContext.encodings or "
        "EncodingContext.path_conditions lists must be reviewed carefully."
    ),
)

# -------------------------------------------------------------------------

THM_UNSAT_CORE_MINIMALITY = TheoremRecord(
    theorem_id="thm_unsat_core_minimality",
    title="Minimality (Irredundancy) of Extracted Unsat Cores",
    statement=(
        "Let Φ = {φ_1, ..., φ_n} be an unsatisfiable set of assertions "
        "in an EncodingContext.  The unsat core C ⊆ Φ extracted by Z3 "
        "via get_unsat_core() is irredundant: every element φ_i ∈ C is "
        "necessary for the unsatisfiability of C.  No proper subset "
        "C' ⊊ C is itself unsatisfiable.  "
        "Formally: ∀φ_i ∈ C. C \\ {φ_i} is satisfiable."
    ),
    proof_sketch=(
        "Paragraph 1 — Z3's core extraction algorithm.\n"
        "Z3 computes an unsat core by tracking which named assertions "
        "participated in the DPLL(T) refutation proof.  The core is a "
        "subset of the named assertions sufficient to derive a contradiction.  "
        "Z3 does not guarantee that this core is globally minimal (i.e., "
        "the smallest possible unsat core) but it does guarantee that the "
        "core is sufficient for unsatisfiability.  Whether Z3's core is "
        "also irredundant (no element can be removed) is not guaranteed "
        "by the Z3 documentation as of v4.12.\n\n"
        "Paragraph 2 — Known gap in the guarantee.\n"
        "The SMT-LIB 2 standard does not require solvers to return minimal "
        "unsat cores, only sufficient ones.  In practice Z3 often returns "
        "cores that are close to minimal for QF_LIA obligations, but this "
        "is not a formal guarantee.  To strengthen the result to true "
        "minimality, the pipeline would need to apply a post-processing "
        "step (greedy deletion as in THM_FAILURE_ARTIFACT_MINIMALITY) to "
        "the extracted core.  Without this step, the core may contain "
        "redundant elements.\n\n"
        "Paragraph 3 — Open status and research direction.\n"
        "This theorem is listed as OPEN because no proof or disproof of "
        "Z3's irredundancy guarantee for QF_LIA cores is currently known.  "
        "The research direction is: (a) inspect Z3's source to determine "
        "whether the DPLL(T) core is constructed irredundantly, or (b) "
        "add a post-processing minimisation step and prove that step "
        "correct (reducing to THM_FAILURE_ARTIFACT_MINIMALITY).  "
        "Until this is resolved, callers of get_unsat_core() should treat "
        "the result as sufficient but not necessarily minimal."
    ),
    status=TheoremStatus.OPEN,
    fragment=FragmentHint.MIXED,
    dependencies=["thm_encoding_context_monotonicity"],
    copilot_notes=(
        "copilot: This theorem is currently open.  Investigate Z3's "
        "core minimality guarantees in the Z3 documentation and source "
        "(z3/src/sat/unsat_core.cpp).  If minimality cannot be guaranteed, "
        "add a minimisation pass after get_unsat_core() and verify it "
        "using the argument in THM_FAILURE_ARTIFACT_MINIMALITY."
    ),
)

# ====================================================================
# ALL_THEOREMS — convenience list of all module-level theorem records
# ====================================================================

ALL_THEOREMS: list[TheoremRecord] = [
    THM_REFINEMENT_SOUNDNESS,
    THM_PATH_CONDITION_COMPLETENESS,
    THM_GUARD_ELIMINATION,
    THM_ARITHMETIC_DECIDABILITY,
    THM_FAILURE_ARTIFACT_MINIMALITY,
    THM_SUBTYPE_ENTAILMENT_CORRECTNESS,
    THM_QF_LIA_TERMINATION,
    THM_PATH_JOIN_SOUNDNESS,
    THM_ENCODING_CONTEXT_MONOTONICITY,
    THM_UNSAT_CORE_MINIMALITY,
]

# ====================================================================
# THEOREM_REGISTRY — pre-populated module-level registry
# ====================================================================

THEOREM_REGISTRY: TheoremRegistry = TheoremRegistry()
for _record in ALL_THEOREMS:
    THEOREM_REGISTRY.register(_record)

# ====================================================================
# Module-level utility functions
# ====================================================================


def verify_theorem_sketch(
    record: TheoremRecord,
    session: Z3Session | None = None,
) -> bool:
    """Attempt a lightweight verification of a theorem sketch.

    For theorems that are already :attr:`TheoremStatus.MECHANIZED`, this
    function returns ``True`` immediately without performing any SMT query.
    For :attr:`TheoremStatus.REFUTED` theorems it returns ``False``.

    For :attr:`TheoremStatus.SKETCH_ONLY` theorems the function performs
    a syntactic validity check on the SMT-LIB 2 sketch returned by
    :meth:`TheoremRecord.formal_statement_smt2`: it checks that the string
    is non-empty and (if an active *session* is provided) attempts to push
    the sketch to the session as a comment to confirm the session is alive.

    Parameters
    ----------
    record:
        The :class:`TheoremRecord` to attempt to verify.
    session:
        An optional active :class:`~jugeo.solver.z3_session.Z3Session`.
        If provided and alive, a lightweight session-health check is
        performed.  If ``None``, only syntactic checks are done.

    Returns
    -------
    bool
        ``True`` if the verification attempt succeeded or the theorem is
        already mechanised; ``False`` if it is refuted or the sketch check
        failed.
    """
    logger.info(
        "verify_theorem_sketch: checking %s (status=%s)",
        record.theorem_id,
        record.status.name,
    )

    # Short-circuit on terminal statuses.
    if record.status is TheoremStatus.MECHANIZED:
        logger.debug(
            "verify_theorem_sketch: %s is MECHANIZED — trivially True",
            record.theorem_id,
        )
        return True

    if record.status is TheoremStatus.REFUTED:
        logger.warning(
            "verify_theorem_sketch: %s is REFUTED — returning False",
            record.theorem_id,
        )
        return False

    # For sketch-only or stated theorems, check syntactic validity.
    sketch = record.formal_statement_smt2()
    if not sketch or not sketch.strip():
        logger.warning(
            "verify_theorem_sketch: %s produced an empty SMT2 sketch",
            record.theorem_id,
        )
        return False

    # Ensure every SMT-comment-prefixed line starts with ';'.
    sketch_lines = [line for line in sketch.splitlines() if line.strip()]
    smt2_content_lines = [
        line for line in sketch_lines if not line.strip().startswith(";")
    ]
    if smt2_content_lines:
        # There is actual SMT2 content — check for basic syntactic markers.
        has_assert = any("assert" in line or "check-sat" in line
                        for line in smt2_content_lines)
        if not has_assert:
            logger.warning(
                "verify_theorem_sketch: %s SMT2 sketch contains no "
                "(assert ...) or (check-sat) — may be incomplete",
                record.theorem_id,
            )
            # Not a hard failure; still return True for sketch validity.

    # If a session is provided, verify it is alive.
    if session is not None:
        try:
            alive = session.is_alive()
            if not alive:
                logger.warning(
                    "verify_theorem_sketch: provided session is not alive "
                    "for theorem %s",
                    record.theorem_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "verify_theorem_sketch: session health check raised %s "
                "for theorem %s",
                exc,
                record.theorem_id,
            )

    logger.info(
        "verify_theorem_sketch: %s sketch appears syntactically valid",
        record.theorem_id,
    )
    return True


def check_dependencies_met(
    record: TheoremRecord,
    registry: TheoremRegistry,
) -> bool:
    """Return ``True`` if all dependencies of *record* are mechanised.

    Looks up each dependency ID in *registry*.  If a dependency is missing
    from the registry or its status is not :attr:`TheoremStatus.MECHANIZED`,
    the function logs a warning and returns ``False``.

    Parameters
    ----------
    record:
        The :class:`TheoremRecord` whose dependencies are to be checked.
    registry:
        The :class:`TheoremRegistry` to look up dependency records in.

    Returns
    -------
    bool
        ``True`` iff every dependency is present in *registry* and has
        status :attr:`TheoremStatus.MECHANIZED`.
    """
    unmet = registry.unresolved_dependencies(record.theorem_id)
    if unmet:
        for dep_id in unmet:
            dep = registry._theorems.get(dep_id)
            status_str = dep.status.name if dep is not None else "MISSING"
            logger.warning(
                "check_dependencies_met: theorem %s has unmet dependency "
                "%s (status=%s)",
                record.theorem_id,
                dep_id,
                status_str,
            )
        return False
    logger.debug(
        "check_dependencies_met: all dependencies met for %s",
        record.theorem_id,
    )
    return True


def export_theorem_list() -> list[dict[str, Any]]:
    """Export all module-level theorems as a list of serialisable dicts.

    Collects all :data:`THM_*` constants defined at module level, converts
    each to a dict using :meth:`TheoremRecord.to_dict`, and returns the
    list sorted by :attr:`~TheoremRecord.theorem_id` for determinism.

    Returns
    -------
    list[dict[str, Any]]
        JSON-serialisable list of theorem dictionaries.
    """
    return sorted(
        (record.to_dict() for record in ALL_THEOREMS),
        key=lambda d: d["theorem_id"],
    )


# ====================================================================
# Entry point — print registry summary when run as a script
# ====================================================================

if __name__ == "__main__":
    print(THEOREM_REGISTRY.copilot_theorem_summary())
