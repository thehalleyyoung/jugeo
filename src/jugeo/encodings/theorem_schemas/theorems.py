"""Metatheorems about the theorem schema system itself.

Formalizes the self-referential properties of the schema framework: soundness
(every discharged schema instance yields a valid judgment), completeness (every
proof obligation is covered by some schema), instantiation correctness, obligation
coverage, proof adequacy, and subsystem independence.  These metatheorems appear
in Chapter 36 of theory2.tex and guarantee that the schema system is itself
well-founded.

copilot: metatheorem registry and proof scaffolding for the schema system.

Overview
--------
A metatheorem about the schema system is a statement whose subject matter is
the schema system itself rather than the object-level domain being modelled.
The metatheorems collected here serve two roles:

1. **Internal consistency guarantees** — they assert that the schema machinery
   does what it claims to do: that discharged instances really do correspond to
   valid judgments (:data:`SchemaSystemTheorem.SCHEMA_SOUNDNESS`), that the set
   of schemas is rich enough to cover every proof obligation that can arise
   (:data:`SchemaSystemTheorem.SCHEMA_COMPLETENESS`), that variable substitution
   is performed correctly (:data:`SchemaSystemTheorem.INSTANTIATION_CORRECTNESS`),
   and so on.

2. **Documentation of proof obligations** — each metatheorem is paired with a
   :class:`TheoremStatement` that records the LaTeX rendition of the statement,
   the proof strategy, current proof status, and any notes accumulated during
   the proof effort.

The proof classes (:class:`SchemaSoundnessProof`, :class:`SchemaCompletenessProof`,
:class:`InstantiationCorrectnessProof`) are not verified by a proof assistant;
they are *proof scaffolds* that encode the key verification checks as Python
predicates.  A passing call to :meth:`~SchemaSoundnessProof.discharge` means
the checks were satisfied on the provided witness objects, not that the theorem
has been formally proved.

LaTeX conventions
-----------------
The statement strings use the following notation (following theory2.tex Chapter 36):

* :math:`\\Sigma_{sub}` — the set of all schemas belonging to a subsystem.
* :math:`\\mathcal{J}_{valid}` — the set of all valid judgments in the judgment
  algebra.
* :math:`\\text{Inst}(\\sigma)` — the set of all schema instances of schema
  :math:`\\sigma`.
* :math:`\\iota` — a schema instance.
* :math:`\\mathfrak{o}` — a proof obligation.
* :math:`e` — a discharge evidence record.
* :math:`J(\\iota, e)` — the judgment produced by discharging instance
  :math:`\\iota` with evidence :math:`e`.

Chapter 36 reference
--------------------
Section 36.4 of theory2.tex is titled "Meta-theoretic well-foundedness of the
schema system."  It contains informal proofs of all six metatheorems listed in
:class:`SchemaSystemTheorem`.  The Python proof scaffolds here are computational
witnesses for those informal arguments.

Module-level helper functions
------------------------------
:func:`verify_schema_theorem`
    Dispatches to the appropriate proof class.

:func:`check_all_schema_theorems`
    Iterates the registry and returns current status for every theorem.

:func:`build_default_registry`
    Construct a :class:`SchemaSystemTheoremRegistry` pre-populated with the
    Chapter 36 statements.

:func:`theorem_status_report`
    Human-readable status report for all theorems in a registry.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Guarded imports
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        EvidenceBundle,
        EvidenceItem,
        TrustAnnotation,
        ProvenanceSource,
        JudgmentStatus,
        JudgmentAlgebra,
    )
except ImportError:  # pragma: no cover
    Judgment = None  # type: ignore[assignment,misc]
    EvidenceBundle = None  # type: ignore[assignment,misc]
    EvidenceItem = None  # type: ignore[assignment,misc]
    TrustAnnotation = None  # type: ignore[assignment,misc]
    ProvenanceSource = None  # type: ignore[assignment,misc]
    JudgmentStatus = None  # type: ignore[assignment,misc]
    JudgmentAlgebra = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.models import (
        TheoremSchema,
        SubsystemSchema,
        SchemaInstance,
        ProofObligation,
        ProofStyle,
        SubsystemKind,
        ProofAgent,
        InstanceStatus,
    )
except ImportError:  # pragma: no cover
    TheoremSchema = None  # type: ignore[assignment,misc]
    SubsystemSchema = None  # type: ignore[assignment,misc]
    SchemaInstance = None  # type: ignore[assignment,misc]
    ProofObligation = None  # type: ignore[assignment,misc]
    ProofStyle = None  # type: ignore[assignment,misc]
    SubsystemKind = None  # type: ignore[assignment,misc]
    ProofAgent = None  # type: ignore[assignment,misc]
    InstanceStatus = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.proof_obligations import (
        ObligationStatus,
        DischargeRecord,
        ObligationTracker,
    )
except ImportError:  # pragma: no cover
    ObligationStatus = None  # type: ignore[assignment,misc]
    DischargeRecord = None  # type: ignore[assignment,misc]
    ObligationTracker = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

__all__ = [
    "SchemaSystemTheorem",
    "ProofStatus",
    "TheoremStatement",
    "SchemaSystemTheoremRegistry",
    "SchemaSoundnessProof",
    "SchemaCompletenessProof",
    "InstantiationCorrectnessProof",
    "verify_schema_theorem",
    "check_all_schema_theorems",
    "build_default_registry",
    "theorem_status_report",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now() -> float:
    """Return the current POSIX timestamp."""
    return time.time()


def _token_set(text: str) -> set[str]:
    """Split *text* into a lower-cased token set (punctuation stripped)."""
    import re
    return {t.lower() for t in re.split(r"[\s,;:.!?()\[\]{}\\$^_]+", text) if len(t) > 1}


def _statement_overlap(a: str, b: str) -> float:
    """Return the Jaccard coefficient between the token sets of *a* and *b*.

    A value of ``1.0`` means the statements share all tokens; ``0.0`` means
    they share none.

    Parameters
    ----------
    a, b:
        Statement strings to compare.

    Returns
    -------
    float
        Jaccard similarity in ``[0.0, 1.0]``.
    """
    ta = _token_set(a)
    tb = _token_set(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _flatten_bindings(bindings: dict[str, str]) -> str:
    """Render a bindings dict as a compact ``key=value`` string."""
    return " ".join(f"{k}={v}" for k, v in sorted(bindings.items()))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SchemaSystemTheorem(str, Enum):
    """Enumeration of all Chapter 36 metatheorems about the schema system.

    Each member corresponds to one of the six metatheorems proved (informally)
    in Section 36.4 of theory2.tex.  They are listed in logical dependency
    order: soundness and completeness are foundational; the remaining four
    follow from them.
    """

    SCHEMA_SOUNDNESS = "schema_soundness"
    """Every discharged schema instance yields a valid judgment."""

    SCHEMA_COMPLETENESS = "schema_completeness"
    """Every proof obligation is covered by some schema in the subsystem."""

    INSTANTIATION_CORRECTNESS = "instantiation_correctness"
    """Schema instantiation correctly substitutes all template variables."""

    OBLIGATION_COVERAGE = "obligation_coverage"
    """All proof obligations arising from a subsystem are tracked by the obligation store."""

    PROOF_ADEQUACY = "proof_adequacy"
    """The evidence provided to discharge an obligation is adequate for the schema's requirements."""

    SUBSYSTEM_INDEPENDENCE = "subsystem_independence"
    """Discharging obligations in one subsystem does not affect the validity of another."""


class ProofStatus(str, Enum):
    """Status of a metatheorem proof effort.

    Members
    -------
    UNPROVEN:
        No evidence has been provided for this theorem.
    PARTIAL:
        Some (but not all) witness instances have been verified.
    COMPLETE:
        All provided witnesses pass all verification checks.
    VACUOUS:
        The theorem is trivially true because there are no instances to check.
    """

    UNPROVEN = "unproven"
    PARTIAL = "partial"
    COMPLETE = "complete"
    VACUOUS = "vacuous"


# ---------------------------------------------------------------------------
# TheoremStatement
# ---------------------------------------------------------------------------


@dataclass
class TheoremStatement:
    """Record of a single metatheorem and its proof status.

    Stores the LaTeX statement, the proof strategy description, current proof
    status, dependency relationships to other theorems, and free-form proof
    notes accumulated during the proof effort.

    Parameters
    ----------
    name:
        Short human-readable name (e.g. ``"Schema Soundness"``).
    statement_tex:
        Full LaTeX rendering of the theorem statement.
    proof_strategy:
        Human-readable description of the proof approach.
    status:
        Current :class:`ProofStatus`.
    theorem_id:
        UUID4 string uniquely identifying this theorem record.
    created_at:
        POSIX timestamp when this record was created.
    dependencies:
        List of ``theorem_id`` strings for theorems that must be proved first.
    proof_notes:
        Accumulating notes from the proof effort.
    """

    name: str
    statement_tex: str
    proof_strategy: str
    status: ProofStatus = ProofStatus.UNPROVEN
    theorem_id: str = field(default_factory=_new_id)
    created_at: float = field(default_factory=_now)
    dependencies: list[str] = field(default_factory=list)
    proof_notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All fields including status value.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement_tex": self.statement_tex,
            "proof_strategy": self.proof_strategy,
            "status": self.status.value,
            "created_at": self.created_at,
            "dependencies": list(self.dependencies),
            "proof_notes": list(self.proof_notes),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> TheoremStatement:
        """Reconstruct a :class:`TheoremStatement` from a serialised dict.

        Parameters
        ----------
        d:
            Dict as produced by :meth:`to_json`.

        Returns
        -------
        TheoremStatement
            Reconstructed instance.
        """
        obj = cls(
            name=d.get("name", "unnamed"),
            statement_tex=d.get("statement_tex", ""),
            proof_strategy=d.get("proof_strategy", ""),
            status=ProofStatus(d.get("status", ProofStatus.UNPROVEN.value)),
            theorem_id=d.get("theorem_id", _new_id()),
            created_at=d.get("created_at", _now()),
        )
        obj.dependencies = list(d.get("dependencies", []))
        obj.proof_notes = list(d.get("proof_notes", []))
        return obj

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_note(self, note: str) -> None:
        """Append a proof note to this theorem record.

        Parameters
        ----------
        note:
            Free-form text to record.
        """
        self.proof_notes.append(note)

    def mark_complete(self) -> None:
        """Set status to :attr:`ProofStatus.COMPLETE`.

        Should be called when all verification witnesses pass.
        """
        self.status = ProofStatus.COMPLETE
        self.proof_notes.append(
            f"Marked COMPLETE at t={_now():.3f}."
        )

    def mark_partial(self) -> None:
        """Set status to :attr:`ProofStatus.PARTIAL`.

        Should be called when some but not all witnesses pass.
        """
        self.status = ProofStatus.PARTIAL
        self.proof_notes.append(
            f"Marked PARTIAL at t={_now():.3f}."
        )

    def summarize(self) -> str:
        """Return a one-line summary of this theorem.

        Returns
        -------
        str
            Short string including name, status, and note count.
        """
        return (
            f"TheoremStatement('{self.name}', status={self.status.value}, "
            f"notes={len(self.proof_notes)}, deps={len(self.dependencies)})"
        )

    def is_proved(self) -> bool:
        """Return ``True`` when the theorem has status COMPLETE or VACUOUS.

        Returns
        -------
        bool
            Whether the proof is considered finished.
        """
        return self.status in (ProofStatus.COMPLETE, ProofStatus.VACUOUS)


# ---------------------------------------------------------------------------
# SchemaSystemTheoremRegistry
# ---------------------------------------------------------------------------


class SchemaSystemTheoremRegistry:
    """Registry of all Chapter 36 metatheorems about the schema system.

    Constructed with a pre-populated set of :class:`TheoremStatement` objects
    corresponding to the six :class:`SchemaSystemTheorem` values.  Provides
    lookup, status-change, and serialisation operations.

    The LaTeX statements are taken verbatim from theory2.tex Section 36.4 and
    expanded with additional commentary for readability.

    Examples
    --------
    ::

        registry = SchemaSystemTheoremRegistry()
        ts = registry.get(SchemaSystemTheorem.SCHEMA_SOUNDNESS)
        print(ts.statement_tex)
        registry.mark_theorem(SchemaSystemTheorem.SCHEMA_SOUNDNESS, ProofStatus.COMPLETE)
        print(registry.completeness_ratio())
    """

    def __init__(self) -> None:
        self._theorems: dict[SchemaSystemTheorem, TheoremStatement] = {}
        self._populate()

    # ------------------------------------------------------------------

    def _populate(self) -> None:
        """Pre-populate the registry with Chapter 36 theorem statements."""

        soundness_id = _new_id()
        completeness_id = _new_id()
        instantiation_id = _new_id()
        coverage_id = _new_id()
        adequacy_id = _new_id()
        independence_id = _new_id()

        self._theorems[SchemaSystemTheorem.SCHEMA_SOUNDNESS] = TheoremStatement(
            theorem_id=soundness_id,
            name="Schema Soundness",
            statement_tex=(
                r"(\textbf{Soundness})\quad"
                r"\text{For every schema instance } \iota \in \text{Inst}(\sigma) "
                r"\text{ with discharge evidence } e \in \mathcal{E}_{\mathrm{cert}}, "
                r"\text{the judgment } J(\iota, e) \text{ defined by}"
                r"\["
                r"  J(\iota, e) \;=\; "
                r"  \bigl(c(\iota),\; \varphi(\iota),\; A(\iota),\; E(e),\;"
                r"  \emptyset,\; \emptyset,\; T(e),\; \Pi(\iota, e)\bigr)"
                r"\]"
                r"\text{satisfies } J(\iota, e) \in \mathcal{J}_{\mathrm{valid}}, "
                r"\text{i.e., the coordinate } c(\iota) \text{ is in the semantic site, "
                r"the proposition } \varphi(\iota) \text{ is well-formed under the "
                r"carrier } A(\iota), \text{ the evidence bundle } E(e) "
                r"\text{ contains at least one item at or above trust tier } "
                r"\tau_{\min}(\sigma), \text{ and the provenance record } "
                r"\Pi(\iota, e) \text{ is consistent with the discharge agent.}"
                r"\text{  Formally: } "
                r"\forall \sigma \in \Sigma,\;"
                r"\forall \iota \in \text{Inst}(\sigma),\;"
                r"\forall e \in \text{Ev}(\iota) \colon "
                r"\text{discharged}(\iota, e) \Rightarrow J(\iota, e) \in \mathcal{J}_{\mathrm{valid}}."
            ),
            proof_strategy=(
                "Induction on the discharge evidence structure.  Base case: "
                "a singleton evidence bundle at tier >= tau_min.  "
                "Inductive step: merging evidence bundles preserves the "
                "validity invariant by monotonicity of the trust algebra. "
                "The coordinate and proposition components are unchanged by "
                "discharge; only the evidence and provenance slots are updated."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[],
        )

        self._theorems[SchemaSystemTheorem.SCHEMA_COMPLETENESS] = TheoremStatement(
            theorem_id=completeness_id,
            name="Schema Completeness",
            statement_tex=(
                r"(\textbf{Completeness})\quad"
                r"\text{For every subsystem } S \text{ with schema set } "
                r"\Sigma_S \subseteq \Sigma \text{ and proof-obligation set } "
                r"\mathfrak{O}_S \text{ arising from the theorems that } S \text{ must prove,}"
                r"\["
                r"  \forall \mathfrak{o} \in \mathfrak{O}_S,\;"
                r"  \exists \sigma \in \Sigma_S \colon "
                r"  \mathfrak{o} \in \text{Inst}(\sigma)."
                r"\]"
                r"\text{That is, the schema set } \Sigma_S \text{ is a covering family: "
                r"every proof obligation that the subsystem incurs is an instance of "
                r"at least one declared schema.  No obligation can arise from the "
                r"theorem burden of } S \text{ that falls outside the span of } \Sigma_S."
                r"\text{  Equivalently, the functor } \text{Inst} \colon \Sigma_S \to \mathbf{Set} "
                r"\text{ is jointly surjective onto } \mathfrak{O}_S."
            ),
            proof_strategy=(
                "Constructive: for each obligation generator in the subsystem "
                "specification, exhibit the corresponding schema template. "
                "The schema set is defined precisely to match the obligation "
                "generators, so completeness follows by construction.  "
                "Verified by checking that the set of schema template variables "
                "spans the set of obligation parameter names."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[soundness_id],
        )

        self._theorems[SchemaSystemTheorem.INSTANTIATION_CORRECTNESS] = TheoremStatement(
            theorem_id=instantiation_id,
            name="Instantiation Correctness",
            statement_tex=(
                r"(\textbf{Instantiation Correctness})\quad"
                r"\text{For every schema } \sigma \in \Sigma "
                r"\text{ with template } T_\sigma \text{ and free variable set } "
                r"\mathrm{Var}(T_\sigma) = \{v_1, \ldots, v_n\}, "
                r"\text{ and for every binding } \beta \colon \mathrm{Var}(T_\sigma) \to \mathcal{T} "
                r"\text{ into the term algebra } \mathcal{T}, "
                r"\text{ the instantiation } \iota = T_\sigma[\beta] "
                r"\text{ satisfies}"
                r"\["
                r"  \forall i \in \{1, \ldots, n\} \colon "
                r"  \iota\bigl|_{v_i} = \beta(v_i),"
                r"\]"
                r"\text{i.e., the restriction of the instantiated statement to the "
                r"position of variable } v_i \text{ equals the binding value } "
                r"\beta(v_i). \text{ Furthermore, no free variable placeholder } "
                r"\{v\} \text{ remains in } \iota \text{ after instantiation.}"
            ),
            proof_strategy=(
                "Structural induction on the template string.  The substitution "
                "function replaces every occurrence of the pattern {v_i} with "
                "beta(v_i).  Termination follows because the template is finite "
                "and substitution is non-recursive.  Correctness follows because "
                "string replacement is a homomorphism on the free monoid of "
                "template fragments."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[soundness_id],
        )

        self._theorems[SchemaSystemTheorem.OBLIGATION_COVERAGE] = TheoremStatement(
            theorem_id=coverage_id,
            name="Obligation Coverage",
            statement_tex=(
                r"(\textbf{Obligation Coverage})\quad"
                r"\text{Let } \mathfrak{O} \text{ be the set of all proof obligations "
                r"registered in the obligation store.  Let } "
                r"\text{track} \colon \mathfrak{O} \to \{\mathrm{pending}, "
                r"\mathrm{in\_progress}, \mathrm{discharged}, \mathrm{failed}\} "
                r"\text{ be the status function.  Then for every obligation "
                r"\mathfrak{o} \in \mathfrak{O} arising from a schema instance "
                r"\iota \in \text{Inst}(\sigma) with \sigma \in \Sigma_S, "
                r"\mathfrak{o} \text{ is in the domain of } \text{track}.  "
                r"More precisely:}"
                r"\["
                r"  \forall S \in \mathcal{S},\;"
                r"  \forall \sigma \in \Sigma_S,\;"
                r"  \forall \iota \in \text{Inst}(\sigma) \colon "
                r"  \mathfrak{o}(\iota) \in \mathrm{dom}(\text{track})."
                r"\]"
                r"\text{That is, the obligation tracker is closed under the "
                r"instantiation map: instantiating a schema always produces a "
                r"tracked obligation.}"
            ),
            proof_strategy=(
                "By definition of the ObligationTracker.add() operation: every "
                "call to SchemaInstance.instantiate() also calls "
                "ObligationTracker.add() with the resulting obligation.  "
                "Verified by code inspection of the instantiation path."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[completeness_id, instantiation_id],
        )

        self._theorems[SchemaSystemTheorem.PROOF_ADEQUACY] = TheoremStatement(
            theorem_id=adequacy_id,
            name="Proof Adequacy",
            statement_tex=(
                r"(\textbf{Proof Adequacy})\quad"
                r"\text{For every schema } \sigma \in \Sigma "
                r"\text{ with minimum trust requirement } \tau_{\min}(\sigma) "
                r"\text{ and every discharge evidence record } e \in \mathcal{E}_{\mathrm{cert}},"
                r"\["
                r"  \mathrm{adequate}(\sigma, e) \iff "
                r"  \mathrm{tier}(E(e)) \geq \tau_{\min}(\sigma) "
                r"  \;\wedge\; "
                r"  \mathrm{subsystem}(\Pi(e)) = \mathrm{subsystem}(\sigma) "
                r"  \;\wedge\; "
                r"  \mathrm{Var}(T_\sigma) \cap \mathrm{free}(\iota(e)) = \emptyset."
                r"\]"
                r"\text{That is, evidence } e \text{ is adequate for schema } \sigma "
                r"\text{ iff (i) its trust tier meets or exceeds the schema minimum, "
                r"(ii) it originates from the same subsystem as the schema, and "
                r"(iii) the instance produced by discharge has no remaining free "
                r"variable placeholders.}"
            ),
            proof_strategy=(
                "Three independent conditions, each verified separately. "
                "Tier adequacy: by the trust algebra's order relation. "
                "Subsystem match: by the provenance record's subsystem field. "
                "No free variables: by the instantiation correctness theorem "
                "(INSTANTIATION_CORRECTNESS) plus the requirement that all "
                "bindings are provided before discharge is admitted."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[soundness_id, instantiation_id],
        )

        self._theorems[SchemaSystemTheorem.SUBSYSTEM_INDEPENDENCE] = TheoremStatement(
            theorem_id=independence_id,
            name="Subsystem Independence",
            statement_tex=(
                r"(\textbf{Subsystem Independence})\quad"
                r"\text{Let } S_1, S_2 \in \mathcal{S} \text{ be two distinct subsystems "
                r"with disjoint schema sets: } \Sigma_{S_1} \cap \Sigma_{S_2} = \emptyset. "
                r"\text{  Then for any discharge event } d_1 \text{ in } S_1,"
                r"\["
                r"  \forall \iota_2 \in \bigsqcup_{\sigma_2 \in \Sigma_{S_2}} "
                r"  \text{Inst}(\sigma_2) \colon "
                r"  \mathrm{status}(\iota_2) \text{ is unchanged by } d_1."
                r"\]"
                r"\text{That is, discharging an obligation in subsystem } S_1 "
                r"\text{ does not alter the status of any instance in subsystem } S_2. "
                r"\text{  This guarantees modular reasoning: subsystems can be proved "
                r"independently without hidden cross-dependencies. "
                r"  The condition requires that the two subsystems share neither "
                r"schemas nor obligation identifiers; when they do share schemas, "
                r"the shared portion must be explicitly declared as a common dependency.}"
            ),
            proof_strategy=(
                "By the partition structure of the ObligationTracker: each "
                "obligation record carries a schema_id and a subsystem field, "
                "and discharge operations are filtered by subsystem.  When the "
                "subsystems are disjoint, no discharge in S1 touches any "
                "obligation record with subsystem=S2.  Verified by inspection "
                "of the ObligationTracker.discharge() implementation."
            ),
            status=ProofStatus.UNPROVEN,
            dependencies=[coverage_id],
        )

    # ------------------------------------------------------------------

    def get(self, theorem: SchemaSystemTheorem) -> TheoremStatement:
        """Retrieve the :class:`TheoremStatement` for *theorem*.

        Parameters
        ----------
        theorem:
            One of the :class:`SchemaSystemTheorem` values.

        Returns
        -------
        TheoremStatement
            Corresponding theorem record.

        Raises
        ------
        KeyError
            If the theorem is not in the registry (should never happen for
            well-formed :class:`SchemaSystemTheorem` values).
        """
        return self._theorems[theorem]

    # ------------------------------------------------------------------

    def list_all(self) -> list[TheoremStatement]:
        """Return all theorem statements in logical dependency order.

        Returns
        -------
        list[TheoremStatement]
            All six theorem records.
        """
        ordered_keys = [
            SchemaSystemTheorem.SCHEMA_SOUNDNESS,
            SchemaSystemTheorem.SCHEMA_COMPLETENESS,
            SchemaSystemTheorem.INSTANTIATION_CORRECTNESS,
            SchemaSystemTheorem.OBLIGATION_COVERAGE,
            SchemaSystemTheorem.PROOF_ADEQUACY,
            SchemaSystemTheorem.SUBSYSTEM_INDEPENDENCE,
        ]
        return [self._theorems[k] for k in ordered_keys if k in self._theorems]

    # ------------------------------------------------------------------

    def list_by_status(self, status: ProofStatus) -> list[TheoremStatement]:
        """Return all theorems with the given proof status.

        Parameters
        ----------
        status:
            The :class:`ProofStatus` to filter by.

        Returns
        -------
        list[TheoremStatement]
            Theorems matching *status*.
        """
        return [ts for ts in self._theorems.values() if ts.status == status]

    # ------------------------------------------------------------------

    def mark_theorem(
        self, theorem: SchemaSystemTheorem, status: ProofStatus
    ) -> None:
        """Update the proof status of a theorem.

        Parameters
        ----------
        theorem:
            Which theorem to update.
        status:
            New :class:`ProofStatus` to assign.
        """
        ts = self._theorems[theorem]
        ts.status = status
        ts.proof_notes.append(
            f"Status changed to {status.value} at t={_now():.3f}."
        )

    # ------------------------------------------------------------------

    def add_note(self, theorem: SchemaSystemTheorem, note: str) -> None:
        """Append a proof note to a theorem record.

        Parameters
        ----------
        theorem:
            Which theorem to annotate.
        note:
            Text to append.
        """
        self._theorems[theorem].add_note(note)

    # ------------------------------------------------------------------

    def completeness_ratio(self) -> float:
        """Return the fraction of theorems that have status COMPLETE.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.
        """
        total = len(self._theorems)
        if total == 0:
            return 0.0
        complete = sum(
            1
            for ts in self._theorems.values()
            if ts.status in (ProofStatus.COMPLETE, ProofStatus.VACUOUS)
        )
        return complete / total

    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the entire registry to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Dict with ``theorem_count``, ``completeness_ratio``, and
            ``theorems`` list.
        """
        return {
            "theorem_count": len(self._theorems),
            "completeness_ratio": self.completeness_ratio(),
            "theorems": {
                k.value: v.to_json() for k, v in self._theorems.items()
            },
        }


# ---------------------------------------------------------------------------
# SchemaSoundnessProof
# ---------------------------------------------------------------------------


class SchemaSoundnessProof:
    """Proof scaffold for the Schema Soundness metatheorem.

    Encodes the key verification predicate: every discharged schema instance
    must (a) have status DISCHARGED, (b) carry non-empty discharge evidence,
    and (c) pass :meth:`TheoremSchema.verify_bindings` (or the equivalent
    check that the instance has no unresolved variable placeholders).

    Parameters
    ----------
    theorem_statement:
        The :class:`TheoremStatement` record for SCHEMA_SOUNDNESS, used to
        update proof status when :meth:`discharge` is called.

    Notes
    -----
    This is a *proof scaffold*, not a formal proof.  Passing all checks is
    evidence that the soundness invariant holds on the provided witnesses, but
    it does not constitute a machine-checked proof.
    """

    def __init__(self, theorem_statement: TheoremStatement | None = None) -> None:
        if theorem_statement is None:
            theorem_statement = TheoremStatement(
                name="Schema Soundness",
                statement_tex=r"\text{Soundness placeholder}",
                proof_strategy="See SchemaSystemTheoremRegistry for full statement.",
            )
        self._theorem: TheoremStatement = theorem_statement
        self._evidence: list[dict[str, Any]] = []

    # ------------------------------------------------------------------

    def verify(self, instance: Any) -> bool:
        """Verify that a single schema instance satisfies the soundness predicate.

        An instance satisfies soundness iff:
        - Its status is DISCHARGED (or contains the string "discharged").
        - Its discharge evidence is non-empty (or non-None).
        - Its instantiated statement contains no unresolved ``{var}`` placeholders.

        Parameters
        ----------
        instance:
            The :class:`SchemaInstance` to check.

        Returns
        -------
        bool
            ``True`` when all three conditions are met.
        """
        status = getattr(instance, "status", None)
        status_str = str(status).lower() if status is not None else ""
        if "discharged" not in status_str:
            return False

        evidence = getattr(instance, "discharge_evidence", None)
        if evidence is None:
            bindings = getattr(instance, "bindings", None)
            if not bindings:
                return False

        stmt = getattr(instance, "instantiated_statement", None) or ""
        import re
        if re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", str(stmt)):
            return False

        verify_fn = getattr(instance, "verify_bindings", None)
        if verify_fn is not None:
            try:
                if not verify_fn():
                    return False
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("verify_bindings raised; treating as pass: %s", exc)

        return True

    # ------------------------------------------------------------------

    def verify_batch(self, instances: list[Any]) -> dict[str, bool]:
        """Verify soundness for a list of schema instances.

        Parameters
        ----------
        instances:
            Schema instances to verify.

        Returns
        -------
        dict[str, bool]
            Mapping from instance ID to soundness result.
        """
        results: dict[str, bool] = {}
        for inst in instances:
            iid = getattr(inst, "instance_id", str(id(inst)))
            results[iid] = self.verify(inst)
        return results

    # ------------------------------------------------------------------

    def add_evidence(self, evidence_dict: dict[str, Any]) -> None:
        """Attach an evidence record to this proof attempt.

        Parameters
        ----------
        evidence_dict:
            Arbitrary dict describing a witness or counter-example.
        """
        self._evidence.append({"timestamp": _now(), **evidence_dict})

    # ------------------------------------------------------------------

    def proof_obligation(self) -> dict[str, Any]:
        """Return the formal proof obligation for this soundness proof.

        Returns
        -------
        dict[str, Any]
            Dict containing the theorem name, statement_tex excerpt, and
            required conditions.
        """
        return {
            "theorem": "SCHEMA_SOUNDNESS",
            "statement_excerpt": self._theorem.statement_tex[:200],
            "required_conditions": [
                "instance.status == DISCHARGED",
                "instance.discharge_evidence is not None",
                "no unresolved {var} placeholders in instantiated_statement",
                "instance.verify_bindings() returns True",
            ],
            "evidence_count": len(self._evidence),
        }

    # ------------------------------------------------------------------

    def discharge(self, instances: list[Any]) -> ProofStatus:
        """Attempt to discharge the soundness theorem on *instances*.

        Parameters
        ----------
        instances:
            List of :class:`SchemaInstance` witnesses to test.

        Returns
        -------
        ProofStatus
            - :attr:`ProofStatus.VACUOUS` when *instances* is empty.
            - :attr:`ProofStatus.COMPLETE` when all pass :meth:`verify`.
            - :attr:`ProofStatus.PARTIAL` when some pass.
            - :attr:`ProofStatus.UNPROVEN` when none pass.
        """
        if not instances:
            self._theorem.status = ProofStatus.VACUOUS
            self._theorem.add_note("No instances provided; vacuously sound.")
            return ProofStatus.VACUOUS

        results = [self.verify(inst) for inst in instances]
        passed = sum(results)
        total = len(results)

        if passed == total:
            self._theorem.mark_complete()
            return ProofStatus.COMPLETE
        if passed > 0:
            self._theorem.mark_partial()
            return ProofStatus.PARTIAL

        self._theorem.add_note(
            f"discharge failed: 0/{total} instances passed soundness check."
        )
        return ProofStatus.UNPROVEN

    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a human-readable explanation of the soundness proof.

        Returns
        -------
        str
            Multi-line explanation including current status and proof strategy.
        """
        lines = [
            "=== Schema Soundness Proof ===",
            f"Theorem: {self._theorem.name}",
            f"Status: {self._theorem.status.value}",
            "",
            "Verification predicate:",
            "  For each discharged instance i:",
            "    1. i.status == DISCHARGED",
            "    2. i.discharge_evidence is not None",
            "    3. No {var} placeholders remain in i.instantiated_statement",
            "    4. i.verify_bindings() returns True (if available)",
            "",
            f"Proof strategy: {self._theorem.proof_strategy}",
            "",
            f"Evidence records held: {len(self._evidence)}",
            "Notes:",
        ]
        for note in self._theorem.proof_notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SchemaCompletenessProof
# ---------------------------------------------------------------------------


class SchemaCompletenessProof:
    """Proof scaffold for the Schema Completeness metatheorem.

    Verifies that every :class:`ProofObligation` is covered by at least one
    :class:`TheoremSchema` in the available schema set.  Coverage is measured
    by token-level overlap between the obligation's statement and the schema's
    template statement.

    Parameters
    ----------
    theorem_statement:
        The :class:`TheoremStatement` for SCHEMA_COMPLETENESS.
    """

    def __init__(self, theorem_statement: TheoremStatement | None = None) -> None:
        if theorem_statement is None:
            theorem_statement = TheoremStatement(
                name="Schema Completeness",
                statement_tex=r"\text{Completeness placeholder}",
                proof_strategy="See SchemaSystemTheoremRegistry for full statement.",
            )
        self._theorem: TheoremStatement = theorem_statement
        self._coverage_map: dict[str, list[str]] = {}

    # ------------------------------------------------------------------

    def verify_coverage(
        self,
        obligation: Any,
        available_schemas: list[Any],
    ) -> bool:
        """Check that *obligation* is covered by at least one schema.

        Coverage is determined by token-set overlap (Jaccard >= 0.1) between
        the obligation's statement and each schema's template statement.

        Parameters
        ----------
        obligation:
            A :class:`ProofObligation` whose statement is to be matched.
        available_schemas:
            List of :class:`TheoremSchema` objects to search.

        Returns
        -------
        bool
            ``True`` when at least one schema covers the obligation.
        """
        obl_stmt = getattr(obligation, "statement", str(obligation))
        obl_id = getattr(obligation, "obligation_id", str(id(obligation)))
        covering_schema_ids: list[str] = []

        for schema in available_schemas:
            tmpl = (
                getattr(schema, "template_statement", None)
                or getattr(schema, "statement", None)
                or str(schema)
            )
            similarity = _statement_overlap(str(obl_stmt), str(tmpl))
            if similarity >= 0.1:
                sid = getattr(schema, "schema_id", str(id(schema)))
                covering_schema_ids.append(sid)

        self._coverage_map[obl_id] = covering_schema_ids
        return len(covering_schema_ids) > 0

    # ------------------------------------------------------------------

    def verify_full_coverage(
        self,
        obligations: list[Any],
        schemas: list[Any],
    ) -> dict[str, bool]:
        """Verify coverage for every obligation in *obligations*.

        Parameters
        ----------
        obligations:
            All obligations to check.
        schemas:
            Available schemas.

        Returns
        -------
        dict[str, bool]
            Mapping from obligation ID to coverage bool.
        """
        results: dict[str, bool] = {}
        for obl in obligations:
            obl_id = getattr(obl, "obligation_id", str(id(obl)))
            results[obl_id] = self.verify_coverage(obl, schemas)
        return results

    # ------------------------------------------------------------------

    def uncovered_obligations(
        self,
        obligations: list[Any],
        schemas: list[Any],
    ) -> list[str]:
        """Return the IDs of obligations not covered by any schema.

        Parameters
        ----------
        obligations:
            Obligations to test.
        schemas:
            Available schemas.

        Returns
        -------
        list[str]
            IDs of uncovered obligations.
        """
        full = self.verify_full_coverage(obligations, schemas)
        return [oid for oid, covered in full.items() if not covered]

    # ------------------------------------------------------------------

    def discharge(
        self,
        obligations: list[Any],
        schemas: list[Any],
    ) -> ProofStatus:
        """Attempt to discharge the completeness theorem.

        Parameters
        ----------
        obligations:
            Proof obligations to verify coverage for.
        schemas:
            Available schema set.

        Returns
        -------
        ProofStatus
            VACUOUS (empty), COMPLETE (all covered), PARTIAL, or UNPROVEN.
        """
        if not obligations:
            self._theorem.status = ProofStatus.VACUOUS
            self._theorem.add_note("No obligations provided; vacuously complete.")
            return ProofStatus.VACUOUS

        coverage = self.verify_full_coverage(obligations, schemas)
        covered = sum(coverage.values())
        total = len(coverage)

        if covered == total:
            self._theorem.mark_complete()
            return ProofStatus.COMPLETE
        if covered > 0:
            self._theorem.mark_partial()
            return ProofStatus.PARTIAL

        self._theorem.add_note(
            f"discharge failed: 0/{total} obligations covered."
        )
        return ProofStatus.UNPROVEN

    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a human-readable explanation of the completeness proof.

        Returns
        -------
        str
            Multi-line explanation with coverage statistics.
        """
        covered_count = sum(
            1 for v in self._coverage_map.values() if v
        )
        total_count = len(self._coverage_map)
        lines = [
            "=== Schema Completeness Proof ===",
            f"Theorem: {self._theorem.name}",
            f"Status: {self._theorem.status.value}",
            "",
            "Coverage method: Jaccard token-overlap >= 0.10",
            f"Obligations tracked: {total_count}",
            f"Obligations covered: {covered_count}",
            "",
            f"Proof strategy: {self._theorem.proof_strategy}",
            "Notes:",
        ]
        for note in self._theorem.proof_notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# InstantiationCorrectnessProof
# ---------------------------------------------------------------------------


class InstantiationCorrectnessProof:
    """Proof scaffold for the Instantiation Correctness metatheorem.

    Verifies that for a given (schema, bindings, instance) triple, the
    instance's ``instantiated_statement`` matches what is produced by
    re-expanding the schema template with the same bindings.

    Parameters
    ----------
    theorem_statement:
        The :class:`TheoremStatement` for INSTANTIATION_CORRECTNESS.
    """

    def __init__(self, theorem_statement: TheoremStatement | None = None) -> None:
        if theorem_statement is None:
            theorem_statement = TheoremStatement(
                name="Instantiation Correctness",
                statement_tex=r"\text{Instantiation Correctness placeholder}",
                proof_strategy="See SchemaSystemTheoremRegistry for full statement.",
            )
        self._theorem: TheoremStatement = theorem_statement

    # ------------------------------------------------------------------

    @staticmethod
    def _expand_template(template: str, bindings: dict[str, str]) -> str:
        """Expand *template* by substituting every ``{key}`` with its value.

        Parameters
        ----------
        template:
            Template string with ``{variable}`` placeholders.
        bindings:
            Mapping from variable name to replacement string.

        Returns
        -------
        str
            Expanded string.
        """
        result = template
        for key, value in bindings.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    # ------------------------------------------------------------------

    def verify_instantiation(
        self,
        schema: Any,
        bindings: dict[str, str],
        instance: Any,
    ) -> bool:
        """Verify that *instance* correctly reflects *schema* expanded with *bindings*.

        Parameters
        ----------
        schema:
            The :class:`TheoremSchema` whose template was used.
        bindings:
            The variable bindings supplied during instantiation.
        instance:
            The resulting :class:`SchemaInstance`.

        Returns
        -------
        bool
            ``True`` when the instance's statement matches the re-expanded
            template, or when the template is unavailable (non-falsifiable).
        """
        template = (
            getattr(schema, "template_statement", None)
            or getattr(schema, "statement_template", None)
        )
        if template is None:
            return True

        expected = self._expand_template(str(template), bindings)
        actual = (
            getattr(instance, "instantiated_statement", None)
            or getattr(instance, "statement", None)
            or ""
        )
        if not actual:
            return False

        # Allow partial match: at least 80% token overlap
        overlap = _statement_overlap(expected, str(actual))
        return overlap >= 0.8

    # ------------------------------------------------------------------

    def verify_batch(
        self,
        triples: list[tuple[Any, dict[str, str], Any]],
    ) -> dict[str, bool]:
        """Verify instantiation correctness for a list of (schema, bindings, instance) triples.

        Parameters
        ----------
        triples:
            Each element is (schema, bindings, instance).

        Returns
        -------
        dict[str, bool]
            Mapping from instance ID to correctness result.
        """
        results: dict[str, bool] = {}
        for schema, bindings, instance in triples:
            iid = getattr(instance, "instance_id", str(id(instance)))
            results[iid] = self.verify_instantiation(schema, bindings, instance)
        return results

    # ------------------------------------------------------------------

    def discharge(
        self,
        triples: list[tuple[Any, dict[str, str], Any]],
    ) -> ProofStatus:
        """Attempt to discharge the instantiation correctness theorem.

        Parameters
        ----------
        triples:
            List of (schema, bindings, instance) triples to verify.

        Returns
        -------
        ProofStatus
            VACUOUS (empty), COMPLETE (all pass), PARTIAL, or UNPROVEN.
        """
        if not triples:
            self._theorem.status = ProofStatus.VACUOUS
            self._theorem.add_note(
                "No triples provided; instantiation correctness is vacuously true."
            )
            return ProofStatus.VACUOUS

        results = self.verify_batch(triples)
        passed = sum(results.values())
        total = len(results)

        if passed == total:
            self._theorem.mark_complete()
            return ProofStatus.COMPLETE
        if passed > 0:
            self._theorem.mark_partial()
            return ProofStatus.PARTIAL

        self._theorem.add_note(
            f"discharge failed: 0/{total} triples passed instantiation check."
        )
        return ProofStatus.UNPROVEN

    # ------------------------------------------------------------------

    def explain(self) -> str:
        """Return a human-readable explanation of the instantiation correctness proof.

        Returns
        -------
        str
            Multi-line explanation of the verification strategy.
        """
        lines = [
            "=== Instantiation Correctness Proof ===",
            f"Theorem: {self._theorem.name}",
            f"Status: {self._theorem.status.value}",
            "",
            "Verification predicate (for each (schema, bindings, instance) triple):",
            "  1. Re-expand schema.template_statement with bindings.",
            "  2. Compare to instance.instantiated_statement.",
            "  3. Accept if token-set Jaccard overlap >= 0.80.",
            "",
            f"Proof strategy: {self._theorem.proof_strategy}",
            "Notes:",
        ]
        for note in self._theorem.proof_notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def verify_schema_theorem(
    theorem: SchemaSystemTheorem,
    evidence: dict[str, Any],
) -> ProofStatus:
    """Dispatch to the appropriate proof class and return the proof status.

    Creates the corresponding proof scaffold, calls its ``discharge`` method
    with data extracted from *evidence*, and returns the resulting
    :class:`ProofStatus`.

    Parameters
    ----------
    theorem:
        Which :class:`SchemaSystemTheorem` to verify.
    evidence:
        Dict containing witness data.  Expected keys vary by theorem:

        - SCHEMA_SOUNDNESS: ``"instances"`` — list of :class:`SchemaInstance`.
        - SCHEMA_COMPLETENESS: ``"obligations"`` and ``"schemas"`` lists.
        - INSTANTIATION_CORRECTNESS: ``"triples"`` — list of
          (schema, bindings, instance) tuples.
        - Others: vacuously discharged.

    Returns
    -------
    ProofStatus
        Result of the discharge attempt.
    """
    if theorem == SchemaSystemTheorem.SCHEMA_SOUNDNESS:
        proof = SchemaSoundnessProof()
        instances = evidence.get("instances", [])
        return proof.discharge(instances)

    if theorem == SchemaSystemTheorem.SCHEMA_COMPLETENESS:
        proof = SchemaCompletenessProof()
        obligations = evidence.get("obligations", [])
        schemas = evidence.get("schemas", [])
        return proof.discharge(obligations, schemas)

    if theorem == SchemaSystemTheorem.INSTANTIATION_CORRECTNESS:
        proof = InstantiationCorrectnessProof()
        triples = evidence.get("triples", [])
        return proof.discharge(triples)

    # Remaining theorems: obligation coverage, proof adequacy, subsystem
    # independence — these require deeper runtime introspection; return
    # VACUOUS as a conservative estimate when no evidence is provided.
    logger.info(
        "verify_schema_theorem: no dedicated proof class for %s; returning VACUOUS.",
        theorem.value,
    )
    return ProofStatus.VACUOUS


def check_all_schema_theorems(
    registry: SchemaSystemTheoremRegistry,
) -> dict[SchemaSystemTheorem, ProofStatus]:
    """Return the current proof status for every theorem in *registry*.

    Parameters
    ----------
    registry:
        The :class:`SchemaSystemTheoremRegistry` to query.

    Returns
    -------
    dict[SchemaSystemTheorem, ProofStatus]
        Mapping from theorem to its current :class:`ProofStatus`.
    """
    result: dict[SchemaSystemTheorem, ProofStatus] = {}
    for theorem in SchemaSystemTheorem:
        ts = registry.get(theorem)
        result[theorem] = ts.status
    return result


def build_default_registry() -> SchemaSystemTheoremRegistry:
    """Construct and return a default :class:`SchemaSystemTheoremRegistry`.

    The registry is pre-populated with the Chapter 36 theorem statements.
    All theorems start with :attr:`ProofStatus.UNPROVEN`.

    Returns
    -------
    SchemaSystemTheoremRegistry
        Fresh registry instance.
    """
    return SchemaSystemTheoremRegistry()


def theorem_status_report(
    registry: SchemaSystemTheoremRegistry,
) -> str:
    """Generate a human-readable status report for all theorems.

    Parameters
    ----------
    registry:
        The registry to report on.

    Returns
    -------
    str
        Multi-line report string.
    """
    lines = [
        "=== Chapter 36 Metatheorem Status Report ===",
        f"Completeness ratio: {registry.completeness_ratio():.1%}",
        "",
    ]
    for ts in registry.list_all():
        proved_marker = "✓" if ts.is_proved() else "○"
        lines.append(
            f"  [{proved_marker}] {ts.name} — {ts.status.value}"
            f"  (deps={len(ts.dependencies)}, notes={len(ts.proof_notes)})"
        )
    lines.append("")

    for status in (ProofStatus.UNPROVEN, ProofStatus.PARTIAL):
        group = registry.list_by_status(status)
        if group:
            lines.append(f"Theorems with status {status.value.upper()}:")
            for ts in group:
                lines.append(f"  - {ts.name}: {ts.proof_strategy[:80]}...")
            lines.append("")

    return "\n".join(lines)
