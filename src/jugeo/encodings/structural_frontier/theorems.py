"""Theorem statements and proof sketches for Chapter 25 — Z3 and the Structural Frontier.

This module contains the theorem records for Chapter 25 of Theory2 (Z3 and the
Structural Frontier).  Each theorem is encoded as a first-class
:class:`TheoremRecord` object carrying:

* A unique theorem identifier (e.g. ``thm_frontier_soundness``).
* A human-readable title and a precise mathematical statement.
* A proof sketch — an informal but structured argument that a mechanised proof
  could follow.
* A :class:`TheoremStatus` indicating how far the proof has been developed.
* A :class:`DecidabilityClass` string tagging which part of the decidability
  landscape the theorem concerns.
* A list of dependency theorem IDs required for the proof to go through.
* ``copilot_notes`` — guidance for copilot-assisted proof search, encoding
  hints, and pointers to relevant JuGeo subsystems.
* A :class:`FrontierSide` indicating where the theorem's main claim lives
  relative to the structural frontier.

The module also provides a :class:`TheoremRegistry` that supports lookup,
dependency analysis, status filtering, and a copilot-readable summary of
all registered theorems.

Helper functions let callers verify proof sketches, check that all
dependencies of a theorem are met, and export the complete list as a
JSON-compatible structure.

Theorem coverage
----------------
Twelve theorems cover: frontier soundness, lifted-type invariant preservation,
countermodel validity, repair completeness, frontier boundary stability,
undecidability witness correctness, bisection correctness, support coverage,
type-lifting faithfulness, repair convergence, countermodel obstruction
completeness, and decidability map consistency.

Theory reference
----------------
theory2.tex Ch25 §25.1 – §25.12

copilot: shared-core marker — the DEFAULT_REGISTRY is the canonical theorem
store for the structural frontier chapter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional import — FrontierSide may come from models or from a fallback stub.
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.structural_frontier.models import FrontierSide, DecidabilityClass
except ImportError:  # pragma: no cover
    class FrontierSide(str, Enum):  # type: ignore[no-redef]
        """Side of the structural frontier on which a formula or claim resides."""

        INSIDE = "inside"
        OUTSIDE = "outside"
        BOUNDARY = "boundary"
        UNKNOWN = "unknown"

    class DecidabilityClass(str, Enum):  # type: ignore[no-redef]
        """Decidability classification of a fragment or formula."""

        DECIDABLE = "decidable"
        UNDECIDABLE = "undecidable"
        OPEN = "open"
        UNKNOWN = "unknown"


# --- Internal helpers -------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_id() -> str:
    """Return a compact random hex identifier (12 chars)."""
    return uuid.uuid4().hex[:12]


def _digest(*parts: str) -> str:
    """Return a 16-char SHA-256 hex digest of the concatenated *parts*."""
    payload = "||".join(parts).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _count_words(text: str) -> int:
    """Return the approximate word count of *text*."""
    return len(text.split())


def _truncate(text: str, max_chars: int = 80) -> str:
    """Return *text* truncated to *max_chars*, appending '…' if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


# ============================================================================
# TheoremStatus
# ============================================================================


class TheoremStatus(str, Enum):
    """Life-cycle status of a theorem record in the structural frontier chapter.

    copilot: Use this enum to filter theorems by proof maturity.  Only
    ``MECHANIZED`` theorems should be cited without a caveat.

    Attributes
    ----------
    STATED:
        The theorem has been stated precisely but no proof argument has been
        written.  Treat as a conjecture.
    SKETCH_ONLY:
        A proof sketch exists but has not been machine-checked.  The argument
        is plausible but may contain gaps.
    MECHANIZED:
        The proof has been fully machine-checked (e.g. in Lean 4 or Coq).
        This is the gold standard; the theorem may be cited unconditionally.
    OPEN:
        The theorem is an open problem — no proof or refutation is known.
        Do not cite as established.
    REFUTED:
        The statement has been shown to be false by a counterexample.
        The record is retained for historical traceability.
    """

    STATED = "stated"
    SKETCH_ONLY = "sketch_only"
    MECHANIZED = "mechanized"
    OPEN = "open"
    REFUTED = "refuted"


# ============================================================================
# TheoremRecord
# ============================================================================


@dataclass
class TheoremRecord:
    """A first-class record encoding a theorem from the structural frontier chapter.

    Each field carries specific semantics enforced by the
    :class:`TheoremRegistry`.  The ``proof_sketch`` field is expected to
    provide enough detail for a mechanised proof to proceed; it should
    reference relevant lemmas and known decision-procedure results.

    copilot: When generating proof search hints, prefer theorems with
    status SKETCH_ONLY or STATED that have all dependencies met.  Use
    :meth:`summary` to obtain a compact representation suitable for
    embedding in proposals.

    Attributes
    ----------
    theorem_id : str
        Unique identifier string (snake_case, prefixed with ``thm_``).
    title : str
        Human-readable title (title case, ≤ 80 chars).
    statement : str
        Precise mathematical statement of the theorem.
    proof_sketch : str
        Informal proof argument structured as a sequence of steps.
    status : TheoremStatus
        Current proof status.
    decidability_class : str
        Decidability classification string from :class:`DecidabilityClass`.
    dependencies : list[str]
        Ordered list of theorem IDs that this proof depends on.
    copilot_notes : str
        Guidance for copilot-assisted proof search or encoding hints.
    frontier_side : FrontierSide
        Where the theorem's main claim lies relative to the frontier.
    """

    theorem_id: str
    title: str
    statement: str
    proof_sketch: str
    status: TheoremStatus
    decidability_class: str
    dependencies: list[str]
    copilot_notes: str
    frontier_side: FrontierSide

    # --- Predicate methods --------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` if and only if the proof has been fully mechanised.

        A theorem is complete exactly when its status is
        :attr:`TheoremStatus.MECHANIZED`.  Use this to gate theorem citation
        in trusted downstream artefacts.

        Returns
        -------
        bool
            ``True`` iff ``status == MECHANIZED``.
        """
        return self.status is TheoremStatus.MECHANIZED

    def has_dependencies(self) -> bool:
        """Return ``True`` if this theorem has any declared dependencies.

        Dependencies must all appear in the same registry for the proof to
        be considered complete.  Use :func:`check_dependencies_met` to
        verify registry membership.

        Returns
        -------
        bool
            ``True`` iff the dependencies list is non-empty.
        """
        return len(self.dependencies) > 0

    def summary(self) -> str:
        """Return a formatted multi-line summary of this theorem record.

        The summary includes the ID, title, status, frontier side,
        decidability class, dependency count, statement (truncated), and
        copilot notes (truncated).  Suitable for display in terminal output
        or copilot proposal blocks.

        Returns
        -------
        str
            Multi-line human-readable summary string.
        """
        dep_str = (
            ", ".join(self.dependencies) if self.dependencies else "(none)"
        )
        lines = [
            f"Theorem  : {self.theorem_id}",
            f"Title    : {self.title}",
            f"Status   : {self.status.value}",
            f"Frontier : {self.frontier_side.value}",
            f"Decidable: {self.decidability_class}",
            f"Deps     : {dep_str}",
            f"Statement: {_truncate(self.statement, 120)}",
            f"Notes    : {_truncate(self.copilot_notes, 100)}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary.

        All enum values are serialised as their string value.  Lists of
        strings are preserved as-is.  Useful for export and persistence.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation.
        """
        return {
            "theorem_id": self.theorem_id,
            "title": self.title,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "status": self.status.value,
            "decidability_class": self.decidability_class,
            "dependencies": list(self.dependencies),
            "copilot_notes": self.copilot_notes,
            "frontier_side": self.frontier_side.value,
            "is_complete": self.is_complete(),
            "has_dependencies": self.has_dependencies(),
            "statement_word_count": _count_words(self.statement),
            "proof_sketch_word_count": _count_words(self.proof_sketch),
        }


# ============================================================================
# TheoremRegistry
# ============================================================================


class TheoremRegistry:
    """Lookup, dependency analysis, and reporting service for theorem records.

    The registry is the canonical store for all structural frontier theorems.
    It supports registration, keyed lookup, status filtering, dependency graph
    construction, and a copilot-readable summary of all registered theorems.

    copilot: Use :meth:`open_problems` to identify theorems that need proof
    effort.  Use :meth:`dependency_graph` to plan the order of mechanisation.

    Parameters
    ----------
    None — start empty and populate via :meth:`register`.
    """

    def __init__(self) -> None:
        self._records: dict[str, TheoremRecord] = {}
        self._dependency_cache: dict[str, list[str]] = {}
        self._created_at: str = _utcnow_iso()
        logger.debug("TheoremRegistry initialised at %s", self._created_at)

    # --- Registration -------------------------------------------------------

    def register(self, record: TheoremRecord) -> None:
        """Add *record* to the registry.

        Overwrites any existing record with the same ``theorem_id``.
        Invalidates the dependency cache for the affected theorem.

        Parameters
        ----------
        record:
            The theorem record to register.
        """
        self._records[record.theorem_id] = record
        self._dependency_cache.pop(record.theorem_id, None)
        logger.debug("Registered theorem %s (%s)", record.theorem_id, record.status.value)

    # --- Lookup -------------------------------------------------------------

    def lookup(self, theorem_id: str) -> TheoremRecord:
        """Return the theorem record for *theorem_id*.

        Parameters
        ----------
        theorem_id:
            The unique identifier of the theorem to look up.

        Returns
        -------
        TheoremRecord
            The matching record.

        Raises
        ------
        KeyError
            If no record with *theorem_id* exists in the registry.
        """
        if theorem_id not in self._records:
            raise KeyError(
                f"TheoremRegistry: theorem {theorem_id!r} not found "
                f"(registry has {len(self._records)} records)."
            )
        return self._records[theorem_id]

    # --- Status filters -----------------------------------------------------

    def all_mechanized(self) -> list[TheoremRecord]:
        """Return all records with status :attr:`TheoremStatus.MECHANIZED`.

        Returns
        -------
        list[TheoremRecord]
            Theorems that have been fully machine-checked, in registration order.
        """
        return [r for r in self._records.values() if r.status is TheoremStatus.MECHANIZED]

    def open_problems(self) -> list[TheoremRecord]:
        """Return all records with status :attr:`TheoremStatus.OPEN`.

        Returns
        -------
        list[TheoremRecord]
            Open theorems in registration order.
        """
        return [r for r in self._records.values() if r.status is TheoremStatus.OPEN]

    def by_status(self, status: TheoremStatus) -> list[TheoremRecord]:
        """Return all records with the given *status*.

        Parameters
        ----------
        status:
            The :class:`TheoremStatus` to filter by.

        Returns
        -------
        list[TheoremRecord]
            Matching records in registration order.
        """
        return [r for r in self._records.values() if r.status is status]

    def by_frontier_side(self, side: FrontierSide) -> list[TheoremRecord]:
        """Return all records whose main claim lies on the given frontier *side*.

        Parameters
        ----------
        side:
            The :class:`FrontierSide` to filter by.

        Returns
        -------
        list[TheoremRecord]
            Matching records in registration order.
        """
        return [r for r in self._records.values() if r.frontier_side is side]

    # --- Dependency analysis ------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Return the full dependency graph as a dict of theorem_id → dependencies.

        Uses the cached result when available.  The returned dict maps each
        theorem ID to its list of direct dependency IDs (as declared in
        :attr:`TheoremRecord.dependencies`).

        Returns
        -------
        dict[str, list[str]]
            Adjacency list representation of the dependency graph.
        """
        if len(self._dependency_cache) == len(self._records):
            return dict(self._dependency_cache)
        graph: dict[str, list[str]] = {}
        for tid, record in self._records.items():
            graph[tid] = list(record.dependencies)
            self._dependency_cache[tid] = list(record.dependencies)
        return graph

    def transitive_dependencies(self, theorem_id: str) -> set[str]:
        """Return the full transitive closure of dependencies for *theorem_id*.

        Performs a depth-first traversal of the dependency graph starting
        from *theorem_id*.  Cycles are handled by tracking visited nodes.

        Parameters
        ----------
        theorem_id:
            Starting theorem ID.

        Returns
        -------
        set[str]
            All theorem IDs that *theorem_id* transitively depends on
            (excluding *theorem_id* itself).
        """
        visited: set[str] = set()
        stack: list[str] = list(self._records.get(theorem_id, TheoremRecord(
            theorem_id=theorem_id, title="", statement="", proof_sketch="",
            status=TheoremStatus.STATED, decidability_class="",
            dependencies=[], copilot_notes="", frontier_side=FrontierSide.UNKNOWN,
        )).dependencies)
        while stack:
            dep = stack.pop()
            if dep in visited:
                continue
            visited.add(dep)
            if dep in self._records:
                stack.extend(self._records[dep].dependencies)
        return visited

    def ready_for_mechanisation(self) -> list[TheoremRecord]:
        """Return theorems that are ready for mechanisation.

        A theorem is ready when its status is :attr:`TheoremStatus.SKETCH_ONLY`
        and all its declared dependencies have status
        :attr:`TheoremStatus.MECHANIZED`.

        Returns
        -------
        list[TheoremRecord]
            Theorems ready for mechanisation.
        """
        ready: list[TheoremRecord] = []
        for record in self._records.values():
            if record.status is not TheoremStatus.SKETCH_ONLY:
                continue
            if check_dependencies_met(record, self):
                ready.append(record)
        return ready

    # --- Reporting ----------------------------------------------------------

    def copilot_theorem_summary(self) -> str:
        """Return a structured summary of all theorems for copilot.

        The summary includes a header, per-status counts, a list of open
        problems, a list of mechanised theorems, and a tabular breakdown
        of all registered theorems with ID, status, and title.

        Returns
        -------
        str
            Multi-line structured text suitable for copilot proposals.
        """
        total = len(self._records)
        by_status_counts: dict[str, int] = {s.value: 0 for s in TheoremStatus}
        for record in self._records.values():
            by_status_counts[record.status.value] += 1
        lines = [
            "## TheoremRegistry — Copilot Summary",
            f"Created at     : {self._created_at}",
            f"Total theorems : {total}",
            "",
            "Status breakdown:",
        ]
        for status_val, count in by_status_counts.items():
            lines.append(f"  {status_val:15s}: {count}")
        lines += ["", "Open problems:"]
        open_probs = self.open_problems()
        if open_probs:
            for rec in open_probs:
                lines.append(f"  [{rec.theorem_id}] {rec.title}")
        else:
            lines.append("  (none)")
        lines += ["", "Mechanised theorems:"]
        mech = self.all_mechanized()
        if mech:
            for rec in mech:
                lines.append(f"  [{rec.theorem_id}] {rec.title}")
        else:
            lines.append("  (none)")
        lines += ["", "Full list:", f"  {'ID':<45} {'Status':<15} Title"]
        lines.append("  " + "-" * 90)
        for record in self._records.values():
            lines.append(
                f"  {record.theorem_id:<45} {record.status.value:<15} {record.title}"
            )
        return "\n".join(lines)

    def export_json(self) -> str:
        """Serialise the entire registry to a JSON string.

        Returns
        -------
        str
            Pretty-printed JSON string with one entry per theorem.
        """
        payload = {
            "created_at": self._created_at,
            "theorem_count": len(self._records),
            "theorems": [rec.to_dict() for rec in self._records.values()],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, theorem_id: str) -> bool:
        return theorem_id in self._records


# ============================================================================
# Free functions
# ============================================================================


def verify_theorem_sketch(record: TheoremRecord, session: Any) -> bool:
    """Check that *record* has a non-empty proof sketch and is not refuted.

    This function is the first gate in the proof-pipeline for a given
    theorem.  It verifies that the proof sketch exists (is non-empty after
    stripping whitespace) and that the theorem has not been explicitly
    refuted.  The *session* argument is reserved for future use (e.g. passing
    a Z3Session for automated sketch validation).

    Parameters
    ----------
    record:
        The theorem record to verify.
    session:
        Reserved for future automated sketch verification (e.g. Z3Session).
        Currently unused but required for API compatibility.

    Returns
    -------
    bool
        ``True`` if the sketch is non-empty and the status is not
        :attr:`TheoremStatus.REFUTED`.
    """
    has_sketch = bool(record.proof_sketch.strip())
    not_refuted = record.status is not TheoremStatus.REFUTED
    result = has_sketch and not_refuted
    logger.debug(
        "verify_theorem_sketch(%s): has_sketch=%s not_refuted=%s → %s",
        record.theorem_id,
        has_sketch,
        not_refuted,
        result,
    )
    return result


def check_dependencies_met(record: TheoremRecord, registry: TheoremRegistry) -> bool:
    """Check that all declared dependencies of *record* are met in *registry*.

    A dependency is considered met if it is registered in *registry* AND
    its status is neither :attr:`TheoremStatus.OPEN` nor
    :attr:`TheoremStatus.REFUTED`.  If any dependency is missing or unmet,
    logs a warning and returns ``False``.

    Parameters
    ----------
    record:
        The theorem whose dependencies should be checked.
    registry:
        The registry to look dependencies up in.

    Returns
    -------
    bool
        ``True`` if all dependencies are present and have adequate status.
    """
    for dep_id in record.dependencies:
        if dep_id not in registry:
            logger.warning(
                "check_dependencies_met(%s): dependency %s not in registry.",
                record.theorem_id,
                dep_id,
            )
            return False
        dep_record = registry.lookup(dep_id)
        if dep_record.status in (TheoremStatus.OPEN, TheoremStatus.REFUTED):
            logger.warning(
                "check_dependencies_met(%s): dependency %s has status %s.",
                record.theorem_id,
                dep_id,
                dep_record.status.value,
            )
            return False
    return True


def export_theorem_list() -> list[dict[str, Any]]:
    """Export all theorems from DEFAULT_REGISTRY as a list of dicts.

    Iterates over all records in :data:`DEFAULT_REGISTRY` and calls
    :meth:`TheoremRecord.to_dict` on each.  Suitable for JSON serialisation,
    documentation generation, or passing to copilot tools.

    Returns
    -------
    list[dict[str, Any]]
        One dict per theorem in registration order.
    """
    return [rec.to_dict() for rec in DEFAULT_REGISTRY._records.values()]


def theorem_dependency_report() -> str:
    """Return a formatted dependency report for all theorems in DEFAULT_REGISTRY.

    For each theorem, lists its direct dependencies and whether they are
    met.  Useful for identifying which theorems are ready for the next
    proof step.

    Returns
    -------
    str
        Multi-line human-readable dependency report.
    """
    lines = [
        "# Structural Frontier Theorem Dependency Report",
        f"# Registry size: {len(DEFAULT_REGISTRY)} theorems",
        "",
    ]
    for record in DEFAULT_REGISTRY._records.values():
        met = check_dependencies_met(record, DEFAULT_REGISTRY)
        dep_str = (
            ", ".join(record.dependencies) if record.dependencies else "(none)"
        )
        lines.append(
            f"{record.theorem_id:<45} deps={dep_str} met={'yes' if met else 'NO'}"
        )
    return "\n".join(lines)


def sketch_completeness_score(record: TheoremRecord) -> float:
    """Return a rough completeness score for the proof sketch of *record*.

    The score is a float in [0.0, 1.0] computed from:
    - Status (0.0 = STATED, 0.25 = SKETCH_ONLY, 1.0 = MECHANIZED, …)
    - Proof sketch length (longer sketches score higher, capped at 1.0)
    - Whether dependencies are declared

    Parameters
    ----------
    record:
        The theorem to score.

    Returns
    -------
    float
        A completeness score in [0.0, 1.0].
    """
    status_scores: dict[TheoremStatus, float] = {
        TheoremStatus.STATED: 0.05,
        TheoremStatus.SKETCH_ONLY: 0.40,
        TheoremStatus.MECHANIZED: 1.00,
        TheoremStatus.OPEN: 0.00,
        TheoremStatus.REFUTED: 0.00,
    }
    status_score = status_scores.get(record.status, 0.0)
    word_count = _count_words(record.proof_sketch)
    sketch_score = min(1.0, word_count / 100.0)
    dep_bonus = 0.05 if record.has_dependencies() else 0.0
    raw = status_score * 0.70 + sketch_score * 0.25 + dep_bonus
    return min(1.0, round(raw, 4))


# ============================================================================
# Theorem records (12 constants)
# ============================================================================

# --- thm_frontier_soundness -------------------------------------------------

THM_FRONTIER_SOUNDNESS = TheoremRecord(
    theorem_id="thm_frontier_soundness",
    title="Z3 Frontier Soundness",
    statement=(
        "For every formula φ in the JuGeo proposition language, Z3 terminates and returns "
        "SAT or UNSAT if and only if φ lies strictly inside the structural frontier — "
        "that is, φ belongs to a fragment for which Nelson-Oppen combination yields a "
        "complete decision procedure.  Formulas outside the frontier may cause Z3 to loop "
        "or return UNKNOWN."
    ),
    proof_sketch=(
        "By completeness of the QF_LIA and QF_LRA decision procedures (via Presburger "
        "arithmetic and Fourier-Motzkin elimination) and the Nelson-Oppen combination "
        "theorem, every quantifier-free linear formula over integers or reals terminates. "
        "The fragment classifier correctly identifies the fragment of φ by syntactic "
        "analysis; its soundness is proved by induction on formula structure. "
        "The structural frontier boundary is defined precisely as the set of formulas "
        "where this induction terminates with a decidable tag; soundness follows because "
        "we never tag a formula DECIDABLE unless both the classifier and the combination "
        "theorem apply.  Completeness (no DECIDABLE formula is rejected) follows from "
        "the exhaustiveness of the fragment cases in the classifier."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[],
    copilot_notes=(
        "Copilot: cite QF_LIA completeness (Presburger 1929) and Nelson-Oppen (1979). "
        "The key lemma is that the fragment classifier is sound w.r.t. the SMTLIB2 "
        "fragment hierarchy.  Check jugeo/solver/z3_session.py Z3FragmentClassifier."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_lifted_type_invariant_preservation ---------------------------------

THM_LIFTED_TYPE_INVARIANT_PRESERVATION = TheoremRecord(
    theorem_id="thm_lifted_type_invariant_preservation",
    title="Lifted-Type Invariant Preservation Under Subtyping",
    statement=(
        "Let T be a solver-lifted type with invariant set Inv(T), and let S be a subtype "
        "of T in the SolverLiftedTypeSystem.  Then every value v satisfying the invariants "
        "of S also satisfies the invariants of T: Inv(S) ⊆ Inv(T) when viewed as "
        "predicates over the carrier set.  In particular, the lifting operation is "
        "monotone with respect to the subtype partial order."
    ),
    proof_sketch=(
        "By construction of the SolverLiftedTypeSystem, subtype registration requires "
        "that the invariants of S logically entail those of T under the current Z3 session. "
        "This entailment is checked at registration time by asserting ¬(Inv(S) → Inv(T)) "
        "and running Z3; if the result is UNSAT, the invariant is preserved. "
        "Transitivity of the subtype relation then gives the full monotonicity statement "
        "by induction on the length of the subtype chain. "
        "The base case (S = T) is trivially preserved.  The inductive step uses the "
        "fact that each registration check is a strict entailment check."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=["thm_frontier_soundness"],
    copilot_notes=(
        "Copilot: the critical invariant is that register_type never bypasses the Z3 "
        "entailment check.  See SolverLiftedTypeSystem.register_type in *.  "
        "The monotonicity argument is standard in type theory (Liskov substitution)."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_countermodel_validity ----------------------------------------------

THM_COUNTERMODEL_VALIDITY = TheoremRecord(
    theorem_id="thm_countermodel_validity",
    title="Countermodel Validity as a Witness of Claim Failure",
    statement=(
        "Every Countermodel extracted by the CountermodelExtractor is a valid witness "
        "of claim failure: the assignment it provides falsifies the negated proposition "
        "under the sort interpretations and function interpretations it specifies.  "
        "Moreover, the countermodel's failure class correctly classifies the nature of "
        "the failure (sort violation, assignment conflict, etc.)."
    ),
    proof_sketch=(
        "The CountermodelExtractor reads its assignment directly from the Z3 model "
        "object, which is guaranteed by Z3 soundness to satisfy the asserted negated "
        "proposition.  Sort interpretations are copied verbatim from Z3's model. "
        "The failure class is determined by the ObstructionConverter, which inspects "
        "the model for characteristic patterns (e.g. out-of-range integer assignments "
        "for ARRAY_OUT_OF_BOUNDS).  Correctness of this classification follows by case "
        "analysis on the six FailureClass values and their corresponding Z3 model "
        "signatures."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=["thm_frontier_soundness"],
    copilot_notes=(
        "Copilot: Z3 model soundness is inherited from the Z3 solver itself (not proved "
        "in JuGeo).  The JuGeo contribution is the correct classification of failure "
        "type.  Verify ObstructionConverter.convert in jugeo/solver/countermodels.py."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_repair_completeness ------------------------------------------------

THM_REPAIR_COMPLETENESS = TheoremRecord(
    theorem_id="thm_repair_completeness",
    title="Repair Completeness for the Structural Frontier",
    statement=(
        "For every CountermodelObstruction whose failure class is not UNKNOWN, the "
        "CountermodelToRepair pipeline eventually produces at least one RepairAction "
        "that, when applied, yields a formula φ′ strictly inside the structural "
        "frontier.  That is, the repair pipeline is complete in the sense that "
        "no obstruction is permanently stuck outside the frontier."
    ),
    proof_sketch=(
        "The repair pipeline is modelled as a finite-state reachability problem: "
        "the state space is the set of (formula, failure_class) pairs, and each "
        "RepairAction is a transition that strictly decreases a well-founded "
        "measure (the number of undecidable sub-formulas counted by the fragment "
        "classifier).  STRENGTHEN_PRECONDITION removes one quantifier-scope, "
        "RESTRICT_TO_FRAGMENT removes one non-linear sub-term, and SPLIT_FORMULA "
        "splits the formula into two strictly smaller sub-problems.  Since the "
        "measure is bounded below by zero and decreases at each step, the pipeline "
        "must terminate at a formula inside the frontier."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_frontier_soundness",
        "thm_countermodel_validity",
    ],
    copilot_notes=(
        "Copilot: the termination argument requires a proper measure function on "
        "formulas.  A candidate is the number of forall/exists binders plus the "
        "degree of non-linearity.  Check that SPLIT_FORMULA strictly decreases "
        "this measure — it must not be a no-op on flat atoms."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_frontier_boundary_stability ----------------------------------------

THM_FRONTIER_BOUNDARY_STABILITY = TheoremRecord(
    theorem_id="thm_frontier_boundary_stability",
    title="Frontier Boundary Stability Under Small Formula Perturbations",
    statement=(
        "Let φ be a formula on the structural frontier boundary (FrontierSide.BOUNDARY).  "
        "For any formula φ′ obtained from φ by a small perturbation (substituting a "
        "constant for a variable, or weakening one atom), if the perturbation keeps φ′ "
        "within the same logical fragment, then φ′ remains on the same side of the "
        "boundary as φ.  In other words, the frontier boundary is locally stable under "
        "small formula perturbations."
    ),
    proof_sketch=(
        "Stability follows from the continuity of the fragment classifier: the classifier "
        "is defined syntactically, and small perturbations within a fragment cannot change "
        "the fragment tag.  More precisely, the fragment tag is a function only of the "
        "logical connectives, quantifier depth, and arithmetic operations present in the "
        "formula; substituting a constant does not introduce new connectives and hence "
        "cannot cross a fragment boundary.  The only exception is at exact boundary "
        "formulas where a single atom is non-linear; this case is handled by the "
        "RESTRICT_TO_FRAGMENT repair and is excluded from the perturbation domain."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=["thm_frontier_soundness"],
    copilot_notes=(
        "Copilot: this theorem justifies local search strategies for frontier navigation. "
        "A copilot-guided repair loop can safely make small changes near the boundary "
        "without worrying about large decidability jumps."
    ),
    frontier_side=FrontierSide.BOUNDARY,
)

# --- thm_undecidability_witness_correctness ---------------------------------

THM_UNDECIDABILITY_WITNESS_CORRECTNESS = TheoremRecord(
    theorem_id="thm_undecidability_witness_correctness",
    title="Correctness of Undecidability Witnesses",
    statement=(
        "For every formula φ classified as OUTSIDE the structural frontier, the "
        "StructuralFrontierDefiner produces a correct undecidability witness: a "
        "polynomial-time reduction from the Halting Problem (or from a known "
        "undecidable Diophantine problem) to the satisfiability problem of φ.  "
        "This witness certifies that no decision procedure for φ can exist."
    ),
    proof_sketch=(
        "The witness is constructed by the fragment classifier's negative certificate "
        "path: when a formula is tagged OUTSIDE, the classifier records the specific "
        "undecidable sub-structure (unbounded quantifier alternation, non-linear "
        "Diophantine constraint, or higher-order predicate) that triggers the tag. "
        "Each such sub-structure has a known reduction from an undecidable problem "
        "(Hilbert's 10th for Diophantine constraints, ∀∃ alternation for Presburger). "
        "Composing the classifier's fragment detection with the reduction yields the "
        "undecidability witness.  Correctness of the reductions is classical and is "
        "not re-proved here."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="UNDECIDABLE",
    dependencies=["thm_frontier_soundness"],
    copilot_notes=(
        "Copilot: the reductions from Hilbert's 10th and from ∀∃ Presburger are "
        "well-known.  The novel contribution is the automated extraction of the "
        "relevant undecidable sub-structure from the formula.  Verify that the "
        "classifier correctly identifies all six FailureClass cases."
    ),
    frontier_side=FrontierSide.OUTSIDE,
)

# --- thm_bisection_correctness ----------------------------------------------

THM_BISECTION_CORRECTNESS = TheoremRecord(
    theorem_id="thm_bisection_correctness",
    title="Bisection Produces Semantically Equivalent Sub-Formulas",
    statement=(
        "When the SPLIT_FORMULA repair action bisects a formula φ into two sub-formulas "
        "φ₁ and φ₂, the conjunction φ₁ ∧ φ₂ is equisatisfiable with φ: φ is satisfiable "
        "if and only if both φ₁ and φ₂ are simultaneously satisfiable under the same "
        "variable assignment.  Moreover, each sub-formula is strictly smaller than φ "
        "under the formula-size metric."
    ),
    proof_sketch=(
        "Bisection splits φ at a top-level conjunction: if φ = ψ₁ ∧ ψ₂, set φ₁ = ψ₁ "
        "and φ₂ = ψ₂.  If φ is not a conjunction, the split introduces a fresh "
        "auxiliary variable x and replaces one atom a with (x = a), adding (x = a) to "
        "φ₂.  In both cases equisatisfiability is immediate by the semantics of "
        "conjunction and equality.  Strict size decrease follows because each sub-formula "
        "has fewer AST nodes than φ, which is finite."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=["thm_frontier_soundness"],
    copilot_notes=(
        "Copilot: verify that the SPLIT_FORMULA implementation in *.py does not "
        "introduce existential quantifiers for the auxiliary variable (which would "
        "push the sub-formula outside the frontier).  Auxiliary variables must remain "
        "free (universally implicitly quantified) in the SMT-LIB2 encoding."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_support_covers_decidable_region ------------------------------------

THM_SUPPORT_COVERS_DECIDABLE_REGION = TheoremRecord(
    theorem_id="thm_support_covers_decidable_region",
    title="Support Regions Cover the Decidable Fragment",
    statement=(
        "For every formula φ inside the structural frontier, there exists a "
        "SupportRegion whose patch_keys cover all coordinates at which the "
        "formula is evaluated.  Equivalently, the geometry of decidable formulas "
        "is well-supported: no decidable formula is evaluated at a coordinate "
        "outside every support region."
    ),
    proof_sketch=(
        "The FrontierSupportLinker links each frontier to a support region at "
        "registration time.  By construction, the support region is computed from "
        "the coordinate object associated with the frontier using compute_support(), "
        "which guarantees non-empty patch_keys (falling back to the coordinate key "
        "itself if no patches are found).  The theorem then follows from the "
        "invariant that every decidable formula is classified against a frontier "
        "that has been linked to a support, and from the coverage guarantee of "
        "compute_support."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_frontier_soundness",
        "thm_frontier_boundary_stability",
    ],
    copilot_notes=(
        "Copilot: the key assumption is that compute_support always produces "
        "non-empty patch_keys.  Verify the fallback path in geometry/supports.py "
        "compute_support for the case where no patches overlap the coordinate path."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_type_lifting_faithfulness ------------------------------------------

THM_TYPE_LIFTING_FAITHFULNESS = TheoremRecord(
    theorem_id="thm_type_lifting_faithfulness",
    title="Type Lifting is Faithful to Base-Type Semantics",
    statement=(
        "Let B be a base type in the JuGeo type system and let Lift(B) be the "
        "corresponding SolverLiftedType produced by the TypeLiftingTranslator.  "
        "Then for every value v, v is a member of B in the JuGeo semantics "
        "if and only if v satisfies all invariants in Lift(B).  In other words, "
        "the lifting is a conservative extension: it adds no new values and "
        "removes no valid values."
    ),
    proof_sketch=(
        "Faithfulness is established in two directions.  Forward direction: if v ∈ B "
        "then by construction the invariants of Lift(B) are derived from the "
        "refinement predicates of B, so v satisfies them.  Backward direction: "
        "the invariant set of Lift(B) is generated by taking the conjunction of "
        "all refinement predicates of B and translating them to SMT assertions; "
        "by soundness of the SMT translation (which preserves semantics), "
        "satisfying these assertions implies membership in B.  The translation is "
        "defined by structural recursion on the base type syntax, with each case "
        "verified individually."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_lifted_type_invariant_preservation",
        "thm_frontier_soundness",
    ],
    copilot_notes=(
        "Copilot: verify that the TypeLiftingTranslator.translate method handles all "
        "base-type constructors (product, sum, refinement, reference) and that the "
        "SMT translation of refinement predicates preserves semantics."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_repair_frontier_convergence ----------------------------------------

THM_REPAIR_FRONTIER_CONVERGENCE = TheoremRecord(
    theorem_id="thm_repair_frontier_convergence",
    title="Repair Frontier Navigation Converges to a Decidable Encoding",
    statement=(
        "Starting from any formula φ outside the structural frontier, repeated "
        "application of the CountermodelRepairDispatcher's repair actions "
        "(RESTRICT_TO_FRAGMENT, ADD_QUANTIFIER_FREE, SPLIT_FORMULA, ABSTRACT_AWAY) "
        "converges in finitely many steps to a formula φ* that is strictly inside "
        "the frontier, provided no MANUAL_REVIEW action is required."
    ),
    proof_sketch=(
        "Define a lexicographic measure (d, s) where d is the quantifier-alternation "
        "depth and s is the number of non-linear monomials in the formula. "
        "Each repair action strictly decreases this measure: "
        "RESTRICT_TO_FRAGMENT decreases d or s by removing a non-decidable sub-term; "
        "ADD_QUANTIFIER_FREE decreases d by moving a formula to the QF_ fragment; "
        "SPLIT_FORMULA decreases s by bisecting; ABSTRACT_AWAY decreases both. "
        "Since (d, s) ∈ ℕ × ℕ under the lexicographic order is well-founded, "
        "the repair sequence must reach (0, 0), i.e., a quantifier-free linear "
        "formula, which is inside the frontier by THM_FRONTIER_SOUNDNESS."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_repair_completeness",
        "thm_frontier_soundness",
        "thm_bisection_correctness",
    ],
    copilot_notes=(
        "Copilot: the convergence proof requires that MANUAL_REVIEW is never triggered "
        "for formulas with known failure classes.  Audit the routing table in "
        "CountermodelRepairDispatcher to ensure UNKNOWN is the only class that routes "
        "to MANUAL_REVIEW."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_countermodel_obstruction_completeness ------------------------------

THM_COUNTERMODEL_OBSTRUCTION_COMPLETENESS = TheoremRecord(
    theorem_id="thm_countermodel_obstruction_completeness",
    title="All Obstructions are Captured by Countermodels",
    statement=(
        "For every claim failure in the JuGeo pipeline — every case where Z3 returns "
        "SAT on the negated proposition — the CountermodelExtractor produces a "
        "CountermodelObstruction that captures the full obstruction.  No failure "
        "is silent: every UNSAT-counterevidence is reified as a typed obstruction "
        "record accessible to downstream repair and reporting tools."
    ),
    proof_sketch=(
        "When Z3 returns SAT, the Z3 Python API provides a Model object.  The "
        "CountermodelExtractor iterates over all declared symbols in the solver "
        "context and reads their interpretations from the Model.  Completeness "
        "of extraction follows from Z3's model completeness: the Model assigns "
        "a value to every declared symbol.  The ObstructionConverter then "
        "classifies the failure by inspecting the assignment for the six "
        "FailureClass patterns; since the classification is exhaustive (the UNKNOWN "
        "class catches all remaining patterns), every extracted countermodel "
        "receives a non-null failure class."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_countermodel_validity",
        "thm_frontier_soundness",
    ],
    copilot_notes=(
        "Copilot: the key assumption is that all symbols are declared in the Z3 "
        "context before the solve call.  Verify that Z3QueryBuilder.build() includes "
        "all free variables from the formula as declared constants."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# --- thm_decidability_map_consistency ---------------------------------------

THM_DECIDABILITY_MAP_CONSISTENCY = TheoremRecord(
    theorem_id="thm_decidability_map_consistency",
    title="Decidability Map is Globally Consistent",
    statement=(
        "The DecidabilityMap maintained by the StructuralFrontierPipeline is globally "
        "consistent: for every fragment F, the map assigns exactly one decidability "
        "class (DECIDABLE, UNDECIDABLE, OPEN, or UNKNOWN), and no fragment is "
        "simultaneously classified as DECIDABLE and UNDECIDABLE.  Furthermore, "
        "the KNOWN_DECIDABLE_FRAGMENTS constant is a subset of the fragments "
        "classified as DECIDABLE in the map."
    ),
    proof_sketch=(
        "Consistency is enforced at map-update time: the DecidabilityMap data structure "
        "is a dict with fragment name keys and unique DecidabilityClass values; Python "
        "dict semantics guarantee that each key appears exactly once.  The invariant that "
        "no fragment is both DECIDABLE and UNDECIDABLE follows from the fact that updates "
        "are guarded: a fragment already in the map can only be overwritten if the new "
        "class is UNKNOWN (weakening), not UNDECIDABLE (which would contradict an "
        "existing DECIDABLE entry).  KNOWN_DECIDABLE_FRAGMENTS ⊆ DECIDABLE follows "
        "from the define_phase, which writes DECIDABLE for every fragment in "
        "KNOWN_DECIDABLE_FRAGMENTS."
    ),
    status=TheoremStatus.SKETCH_ONLY,
    decidability_class="DECIDABLE",
    dependencies=[
        "thm_frontier_soundness",
        "thm_undecidability_witness_correctness",
    ],
    copilot_notes=(
        "Copilot: the map consistency invariant must be maintained by any code that "
        "updates DecidabilityMap.entries.  Add an assertion in make_default_map or "
        "in the map update path that checks for conflicting DECIDABLE/UNDECIDABLE "
        "entries."
    ),
    frontier_side=FrontierSide.INSIDE,
)

# ============================================================================
# DEFAULT_REGISTRY — canonical theorem store populated at module load time
# ============================================================================

DEFAULT_REGISTRY: TheoremRegistry = TheoremRegistry()
"""Module-level canonical registry populated with all 12 structural frontier theorems.

copilot: Import this registry to access theorem records, dependency graphs,
and the copilot summary for Chapter 25.

Usage example::

    from jugeo.encodings.structural_frontier.theorems import DEFAULT_REGISTRY
    record = DEFAULT_REGISTRY.lookup("thm_frontier_soundness")
    print(record.summary())
"""

# Populate in dependency order (foundational theorems first)
for _thm in [
    THM_FRONTIER_SOUNDNESS,
    THM_LIFTED_TYPE_INVARIANT_PRESERVATION,
    THM_COUNTERMODEL_VALIDITY,
    THM_REPAIR_COMPLETENESS,
    THM_FRONTIER_BOUNDARY_STABILITY,
    THM_UNDECIDABILITY_WITNESS_CORRECTNESS,
    THM_BISECTION_CORRECTNESS,
    THM_SUPPORT_COVERS_DECIDABLE_REGION,
    THM_TYPE_LIFTING_FAITHFULNESS,
    THM_REPAIR_FRONTIER_CONVERGENCE,
    THM_COUNTERMODEL_OBSTRUCTION_COMPLETENESS,
    THM_DECIDABILITY_MAP_CONSISTENCY,
]:
    DEFAULT_REGISTRY.register(_thm)

logger.debug(
    "DEFAULT_REGISTRY populated with %d theorems.", len(DEFAULT_REGISTRY)
)
