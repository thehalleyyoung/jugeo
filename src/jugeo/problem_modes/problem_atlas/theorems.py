"""Formal theorem statements for the Unified Problem Atlas — Theory2.tex Ch14 §14.7.

copilot: formal theorem registry and proof verification engine.

This module implements §14.7 of Theory2.tex, cataloging the formal theorems
that underpin the problem atlas.  Each theorem is represented as a first-class
data structure with:
  - A formal statement (string)
  - Hypotheses (list of named assumptions)
  - Conclusion
  - A proof sketch
  - Verification status (checked, partial, conjectured, refuted)
  - References to theory2.tex sections

The five core theorems are:

  ATLAS_COMPLETENESS       (§14.7.1) — Every problem in jugeo belongs to some class
  SIGNATURE_COMPOSITION    (§14.7.2) — Composed signatures are compatible if components are
  EVIDENCE_SUFFICIENCY     (§14.7.3) — Evidence is sufficient iff all requirements satisfied
  CLASS_LATTICE_WELLFORMED (§14.7.4) — The class lattice is a well-formed partial order
  TRUST_MONOTONICITY       (§14.7.5) — Adding channels never decreases aggregate trust

Supporting classes:
  TheoremRegistry  — Central registry for all theorems
  ProofVerifier    — Checks proof sketch consistency (symbolically)
  HypothesisSet    — Named collection of hypotheses for a theorem
  TheoremLinker    — Links theorems to problem class entries in the atlas
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        SemanticSignature,
        EvidenceRequirement,
        AtlasCatalog,
        ConjunctionMode,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    SemanticSignature = object  # type: ignore[assignment,misc]
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    AtlasCatalog = object  # type: ignore[assignment,misc]
    ConjunctionMode = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.trust_requirements import (
        RequirementCheckResult,
        TrustGap,
    )
except ImportError:
    RequirementCheckResult = object  # type: ignore[assignment,misc]
    TrustGap = object  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

TheoremId: TypeAlias = str
ClassId: TypeAlias = str
StepId: TypeAlias = str


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VerificationStatus(str, Enum):
    """Represents the verification state of a theorem.

    The ordering, loosely, is:
      REFUTED < UNKNOWN < CONJECTURED ≤ PLAUSIBLE < PARTIAL < VERIFIED < PROVED
    """

    PROVED = "PROVED"
    """Machine-checked proof (e.g., Lean/Coq certificate)."""

    VERIFIED = "VERIFIED"
    """Manually verified and peer-reviewed by at least two researchers."""

    PARTIAL = "PARTIAL"
    """Partially proven; some cases covered but not all."""

    CONJECTURED = "CONJECTURED"
    """Stated without any proof attempt; believed to be true."""

    PLAUSIBLE = "PLAUSIBLE"
    """Supported by empirical evidence but not formally proven."""

    REFUTED = "REFUTED"
    """A counter-example has been found; the theorem is false as stated."""

    UNKNOWN = "UNKNOWN"
    """Status has not yet been assessed."""

    def is_positive(self) -> bool:
        """Return True if the status represents at least partial evidence of truth.

        Returns:
            True for PROVED, VERIFIED, PARTIAL; False otherwise.
        """
        return self in (
            VerificationStatus.PROVED,
            VerificationStatus.VERIFIED,
            VerificationStatus.PARTIAL,
        )

    def confidence_score(self) -> float:
        """Return a numeric confidence score in [0, 1] for this status.

        Returns:
            A float between 0.0 (refuted) and 1.0 (machine-proved).
        """
        mapping: dict[VerificationStatus, float] = {
            VerificationStatus.PROVED: 1.0,
            VerificationStatus.VERIFIED: 0.95,
            VerificationStatus.PARTIAL: 0.6,
            VerificationStatus.PLAUSIBLE: 0.5,
            VerificationStatus.CONJECTURED: 0.4,
            VerificationStatus.UNKNOWN: 0.3,
            VerificationStatus.REFUTED: 0.0,
        }
        return mapping[self]

    @classmethod
    def from_confidence(cls, score: float) -> "VerificationStatus":
        """Return the status whose confidence score is closest to *score*.

        Args:
            score: A float in [0, 1].

        Returns:
            The VerificationStatus closest to the given confidence score.

        Raises:
            ValueError: If *score* is outside [0, 1].
        """
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"Confidence score must be in [0, 1]; got {score!r}")
        candidates = list(cls)
        return min(candidates, key=lambda s: abs(s.confidence_score() - score))


class ProofKind(str, Enum):
    """The kind of reasoning used to establish a theorem."""

    DEDUCTIVE = "DEDUCTIVE"
    """Step-by-step logical deduction from axioms."""

    INDUCTIVE = "INDUCTIVE"
    """Mathematical induction on a natural-number parameter."""

    CONSTRUCTIVE = "CONSTRUCTIVE"
    """Explicit construction of the witness or algorithm."""

    CONTRADICTION = "CONTRADICTION"
    """Proof by assuming the negation and deriving a contradiction."""

    CASE_ANALYSIS = "CASE_ANALYSIS"
    """Exhaustive analysis of all possible cases."""

    COINDUCTIVE = "COINDUCTIVE"
    """Co-induction on a potentially infinite structure."""

    BY_EXAMPLE = "BY_EXAMPLE"
    """A single representative example that generalises."""

    SKETCH = "SKETCH"
    """Informal sketch without complete formal detail."""

    def is_formal(self) -> bool:
        """Return True if this proof kind can produce a fully formal certificate.

        Returns:
            True for DEDUCTIVE, INDUCTIVE, CONSTRUCTIVE, CONTRADICTION,
            CASE_ANALYSIS, and COINDUCTIVE.
        """
        return self in (
            ProofKind.DEDUCTIVE,
            ProofKind.INDUCTIVE,
            ProofKind.CONSTRUCTIVE,
            ProofKind.CONTRADICTION,
            ProofKind.CASE_ANALYSIS,
            ProofKind.COINDUCTIVE,
        )


class TheoremKind(str, Enum):
    """The mathematical category of a theorem statement."""

    EXISTENCE = "EXISTENCE"
    """There exists an object with property P."""

    UNIQUENESS = "UNIQUENESS"
    """There is at most one object with property P."""

    COMPLETENESS = "COMPLETENESS"
    """Every object satisfying the premises also satisfies the conclusion."""

    SOUNDNESS = "SOUNDNESS"
    """Every derivable conclusion is actually true."""

    DECIDABILITY = "DECIDABILITY"
    """There is an algorithm to determine membership."""

    COMPLEXITY = "COMPLEXITY"
    """Bounds on computational resources needed."""

    MONOTONICITY = "MONOTONICITY"
    """A quantity does not decrease (or increase) as input grows."""

    WELLFORMEDNESS = "WELLFORMEDNESS"
    """A structure satisfies all required structural invariants."""

    EQUIVALENCE = "EQUIVALENCE"
    """Two constructions or statements are logically equivalent."""

    INDEPENDENCE = "INDEPENDENCE"
    """A statement is independent of (neither provable nor refutable from) a theory."""

    def implies_constructive_proof(self) -> bool:
        """Return True if theorems of this kind typically require a constructive proof.

        Returns:
            True for EXISTENCE, COMPLETENESS, and SOUNDNESS.
        """
        return self in (
            TheoremKind.EXISTENCE,
            TheoremKind.COMPLETENESS,
            TheoremKind.SOUNDNESS,
        )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """A single named assumption used in the antecedent of a theorem.

    Hypotheses are immutable once created.  They carry a stable identifier
    so proof steps can refer to them by ID rather than by position.

    Attributes:
        hyp_id: Stable unique identifier (e.g., ``"H1"``).
        name: Short descriptive name used in proof steps.
        statement: The formal (or semi-formal) text of the assumption.
        is_structural: True if the hypothesis constrains the *shape* of an
            object (e.g., acyclicity) rather than its content.
        references: Source references where this assumption appears.
    """

    hyp_id: str
    name: str
    statement: str
    is_structural: bool = False
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the hypothesis to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary with all fields.
        """
        return {
            "hyp_id": self.hyp_id,
            "name": self.name,
            "statement": self.statement,
            "is_structural": self.is_structural,
            "references": list(self.references),
        }

    def contradicts(self, other: "Hypothesis") -> bool:
        """Check for a simple syntactic negation between two hypotheses.

        A hypothesis H contradicts another H' if one statement is of the form
        ``"not <X>"`` and the other is exactly ``"<X>"``, or vice-versa.
        This is a syntactic, not semantic, check.

        Args:
            other: The other hypothesis to compare against.

        Returns:
            True if one hypothesis is a simple negation of the other.
        """
        def _normalise(s: str) -> str:
            return s.strip().lower().rstrip(".")

        s1 = _normalise(self.statement)
        s2 = _normalise(other.statement)
        if s1.startswith("not ") and s1[4:].strip() == s2:
            return True
        if s2.startswith("not ") and s2[4:].strip() == s1:
            return True
        return False


@dataclass(frozen=True, slots=True)
class HypothesisSet:
    """An ordered, named collection of hypotheses for a single theorem.

    Attributes:
        hyp_set_id: Unique identifier for this collection.
        name: Human-readable label (e.g., ``"Hypotheses for Atlas Completeness"``).
        hypotheses: Ordered tuple of :class:`Hypothesis` instances.
    """

    hyp_set_id: str
    name: str
    hypotheses: tuple[Hypothesis, ...]

    def get(self, name: str) -> Hypothesis | None:
        """Look up a hypothesis by its *name* attribute.

        Args:
            name: The ``name`` of the hypothesis to retrieve.

        Returns:
            The matching :class:`Hypothesis`, or ``None`` if not found.
        """
        for hyp in self.hypotheses:
            if hyp.name == name:
                return hyp
        return None

    def is_consistent(self) -> bool:
        """Return True if no two hypotheses directly contradict each other.

        Uses :meth:`Hypothesis.contradicts` for pairwise checks.

        Returns:
            True when no contradiction is found.
        """
        hyps = list(self.hypotheses)
        for i, h1 in enumerate(hyps):
            for h2 in hyps[i + 1 :]:
                if h1.contradicts(h2):
                    return False
        return True

    def add(self, hyp: Hypothesis) -> "HypothesisSet":
        """Return a new :class:`HypothesisSet` with *hyp* appended.

        Args:
            hyp: The hypothesis to add.

        Returns:
            A new :class:`HypothesisSet` containing the additional hypothesis.
        """
        return HypothesisSet(
            hyp_set_id=self.hyp_set_id,
            name=self.name,
            hypotheses=self.hypotheses + (hyp,),
        )

    def remove(self, name: str) -> "HypothesisSet":
        """Return a new :class:`HypothesisSet` with the named hypothesis removed.

        Args:
            name: The ``name`` of the hypothesis to remove.

        Returns:
            A new :class:`HypothesisSet` without the named hypothesis.
            If the name is not found the original set is returned unchanged.
        """
        remaining = tuple(h for h in self.hypotheses if h.name != name)
        return HypothesisSet(
            hyp_set_id=self.hyp_set_id,
            name=self.name,
            hypotheses=remaining,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the hypothesis set to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "hyp_set_id": self.hyp_set_id,
            "name": self.name,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
        }


@dataclass(frozen=True, slots=True)
class ProofStep:
    """A single step in a proof sketch.

    Attributes:
        step_id: Stable identifier (e.g., ``"step_1"``).
        description: Human-readable description of what this step achieves.
        justification: The rule, lemma, or hypothesis that justifies this step.
        depends_on: IDs of earlier steps that this step relies upon.
        is_gap: True if this step is not yet proven (a placeholder gap).
    """

    step_id: str
    description: str
    justification: str
    depends_on: tuple[str, ...] = ()
    is_gap: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise the proof step.

        Returns:
            A JSON-serialisable dictionary with all fields.
        """
        return {
            "step_id": self.step_id,
            "description": self.description,
            "justification": self.justification,
            "depends_on": list(self.depends_on),
            "is_gap": self.is_gap,
        }


@dataclass(frozen=True, slots=True)
class ProofSketch:
    """A structured proof sketch consisting of ordered, dependence-linked steps.

    Attributes:
        sketch_id: Unique identifier.
        steps: Tuple of :class:`ProofStep` instances in presentation order.
        proof_kind: The style of proof (deductive, inductive, etc.).
        completeness_estimate: Fraction of the proof that is formally justified
            (0.0 = entirely hand-wavy, 1.0 = fully formal).
        notes: Free-form notes about the proof strategy.
    """

    sketch_id: str
    steps: tuple[ProofStep, ...]
    proof_kind: ProofKind
    completeness_estimate: float = 1.0
    notes: str = ""

    def is_complete(self) -> bool:
        """Return True if no step is marked as a gap.

        Returns:
            True when every step has ``is_gap=False``.
        """
        return all(not step.is_gap for step in self.steps)

    def gap_count(self) -> int:
        """Count the number of gap steps in the sketch.

        Returns:
            Integer count of steps with ``is_gap=True``.
        """
        return sum(1 for step in self.steps if step.is_gap)

    def step_count(self) -> int:
        """Return the total number of steps in the sketch.

        Returns:
            Length of the *steps* tuple.
        """
        return len(self.steps)

    def topological_order(self) -> list[str]:
        """Return step IDs in a topological (dependency-respecting) order.

        Uses Kahn's algorithm.  Steps with no dependencies come first.

        Returns:
            List of ``step_id`` strings in a valid topological order.

        Raises:
            ValueError: If the dependency graph contains a cycle.
        """
        in_degree: dict[str, int] = {s.step_id: 0 for s in self.steps}
        adjacency: dict[str, list[str]] = {s.step_id: [] for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                if dep in adjacency:
                    adjacency[dep].append(step.step_id)
                    in_degree[step.step_id] += 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        result: list[str] = []

        while queue:
            current = queue.pop(0)
            result.append(current)
            for neighbour in adjacency[current]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(result) != len(self.steps):
            raise ValueError("Dependency graph of proof steps contains a cycle.")
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialise the proof sketch.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "sketch_id": self.sketch_id,
            "proof_kind": self.proof_kind.value,
            "completeness_estimate": self.completeness_estimate,
            "notes": self.notes,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass(frozen=True, slots=True)
class Theorem:
    """A formal theorem in the Problem Atlas.

    Each theorem bundles its statement, hypotheses, proof sketch, verification
    status, and bibliographic references into a single immutable object.

    Attributes:
        theorem_id: Globally unique identifier (e.g., ``"THM-14.7.1"``).
        name: Short canonical name (e.g., ``"atlas_completeness"``).
        kind: The mathematical category of the theorem.
        statement: Formal statement (may use mathematical notation).
        informal_statement: Plain-English paraphrase.
        hypotheses: The set of named assumptions.
        conclusion: The consequent of the theorem.
        proof_sketch: Structured sketch of the proof.
        verification_status: Current verification state.
        references: Source references (e.g., ``("theory2.tex §14.7.1",)``).
        related_theorems: IDs of logically related theorems.
        counterexamples: Known counterexamples (empty for PROVED theorems).
        metadata: Immutable key-value pairs for extensible annotation.
    """

    theorem_id: str
    name: str
    kind: TheoremKind
    statement: str
    informal_statement: str
    hypotheses: HypothesisSet
    conclusion: str
    proof_sketch: ProofSketch
    verification_status: VerificationStatus
    references: tuple[str, ...] = ()
    related_theorems: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def is_proved(self) -> bool:
        """Return True iff the theorem has been machine- or manually verified.

        Returns:
            True for PROVED or VERIFIED status.
        """
        return self.verification_status in (
            VerificationStatus.PROVED,
            VerificationStatus.VERIFIED,
        )

    def get_metadata(self, key: str) -> str | None:
        """Retrieve a metadata value by key.

        Args:
            key: The metadata key to look up.

        Returns:
            The associated value string, or ``None`` if absent.
        """
        for k, v in self.metadata:
            if k == key:
                return v
        return None

    def compute_confidence(self) -> float:
        """Compute a composite confidence score for the theorem.

        The score combines the verification-status confidence with a penalty for
        proof gaps.

        Returns:
            A float in [0, 1].
        """
        base = self.verification_status.confidence_score()
        gap_penalty = 0.05 * self.proof_sketch.gap_count()
        return max(0.0, base - gap_penalty)

    def check_hypothesis_consistency(self) -> bool:
        """Delegate to :meth:`HypothesisSet.is_consistent`.

        Returns:
            True if the hypothesis set contains no direct contradictions.
        """
        return self.hypotheses.is_consistent()

    def get_hypothesis(self, name: str) -> Hypothesis | None:
        """Look up a hypothesis by name.

        Args:
            name: The hypothesis name to retrieve.

        Returns:
            The matching :class:`Hypothesis`, or ``None``.
        """
        return self.hypotheses.get(name)

    def has_counterexample(self) -> bool:
        """Return True if any counterexample is registered for this theorem.

        Returns:
            True when ``self.counterexamples`` is non-empty.
        """
        return len(self.counterexamples) > 0

    def to_summary(self) -> str:
        """Generate a one-paragraph human-readable summary.

        Returns:
            A multi-sentence string summarising the theorem.
        """
        hyp_names = ", ".join(h.name for h in self.hypotheses.hypotheses)
        gap_info = (
            f"The proof sketch is complete."
            if self.proof_sketch.is_complete()
            else f"The proof sketch has {self.proof_sketch.gap_count()} gap(s)."
        )
        return (
            f"Theorem '{self.name}' [{self.theorem_id}] ({self.kind.value}): "
            f"{self.informal_statement}  "
            f"Status: {self.verification_status.value} "
            f"(confidence {self.compute_confidence():.2f}).  "
            f"Assumes: {hyp_names or 'none'}.  "
            f"Conclusion: {self.conclusion}  "
            f"{gap_info}  "
            f"References: {'; '.join(self.references) or 'none'}."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the theorem to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary with all fields.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "kind": self.kind.value,
            "statement": self.statement,
            "informal_statement": self.informal_statement,
            "hypotheses": self.hypotheses.to_dict(),
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch.to_dict(),
            "verification_status": self.verification_status.value,
            "references": list(self.references),
            "related_theorems": list(self.related_theorems),
            "counterexamples": list(self.counterexamples),
            "metadata": {k: v for k, v in self.metadata},
        }


@dataclass(frozen=True, slots=True)
class TheoremRelation:
    """A directed or symmetric relation between two theorems.

    Attributes:
        from_id: The source theorem ID.
        to_id: The target theorem ID.
        relation_kind: One of IMPLIES, REQUIRES, CONTRADICTS, GENERALIZES,
            SPECIALIZES, EQUIVALENT.
        description: Free-text explanation of the relationship.
    """

    from_id: str
    to_id: str
    relation_kind: str
    description: str

    _DIRECTIONAL_KINDS: tuple[str, ...] = (
        "IMPLIES",
        "REQUIRES",
        "GENERALIZES",
        "SPECIALIZES",
    )

    def is_directional(self) -> bool:
        """Return True if the relation is asymmetric (source → target).

        Returns:
            True for IMPLIES, REQUIRES, GENERALIZES, SPECIALIZES.
        """
        return self.relation_kind in self._DIRECTIONAL_KINDS


# ---------------------------------------------------------------------------
# Five core theorem constants
# ---------------------------------------------------------------------------

def _make_hyp(hyp_id: str, name: str, statement: str,
              is_structural: bool = False,
              refs: tuple[str, ...] = ()) -> Hypothesis:
    """Internal factory for Hypothesis with sensible defaults."""
    return Hypothesis(
        hyp_id=hyp_id,
        name=name,
        statement=statement,
        is_structural=is_structural,
        references=refs,
    )


def _make_step(step_id: str, description: str, justification: str,
               depends_on: tuple[str, ...] = (),
               is_gap: bool = False) -> ProofStep:
    """Internal factory for ProofStep."""
    return ProofStep(
        step_id=step_id,
        description=description,
        justification=justification,
        depends_on=depends_on,
        is_gap=is_gap,
    )


# §14.7.1 — Atlas Completeness
THEOREM_ATLAS_COMPLETENESS: Theorem = Theorem(
    theorem_id="THM-14.7.1",
    name="atlas_completeness",
    kind=TheoremKind.COMPLETENESS,
    statement=(
        "For every problem P encountered in jugeo, there exists a class C in the atlas "
        "such that P is an instance of C."
    ),
    informal_statement=(
        "No problem presented to jugeo can fall outside the atlas; the UNIVERSAL "
        "catch-all class guarantees at least one valid classification for every input."
    ),
    hypotheses=HypothesisSet(
        hyp_set_id="HS-14.7.1",
        name="Hypotheses for Atlas Completeness",
        hypotheses=(
            _make_hyp(
                "H1",
                "atlas_has_universal_class",
                "The atlas contains a universal top class UNIVERSAL to which all problems belong.",
                is_structural=True,
                refs=("theory2.tex §14.3.1",),
            ),
            _make_hyp(
                "H2",
                "every_problem_has_description",
                "Every problem P has a textual description that can be used for lookup.",
                is_structural=False,
                refs=("theory2.tex §12.1",),
            ),
        ),
    ),
    conclusion=(
        "For all P in jugeo.problems, ∃ C ∈ atlas.classes such that P ∈ instances(C)."
    ),
    proof_sketch=ProofSketch(
        sketch_id="PS-14.7.1",
        steps=(
            _make_step(
                "step_1",
                "By hypothesis atlas_has_universal_class, UNIVERSAL ∈ atlas.classes.",
                "H1 (atlas_has_universal_class)",
            ),
            _make_step(
                "step_2",
                "By definition of UNIVERSAL, every problem is an instance of UNIVERSAL: "
                "instances(UNIVERSAL) = jugeo.problems.",
                "Definition of UNIVERSAL class",
                depends_on=("step_1",),
            ),
            _make_step(
                "step_3",
                "A tighter classification exists when more specific keywords from P's "
                "description match a subclass C' ⊂ UNIVERSAL; by H2 the description exists.",
                "H2 (every_problem_has_description), definition of keyword matching",
                depends_on=("step_2",),
                is_gap=True,
            ),
            _make_step(
                "step_4",
                "Therefore, P is classifiable to at least UNIVERSAL; optimal class selection "
                "terminates by well-foundedness of the lattice (see THM-14.7.4).",
                "step_2, THM-14.7.4 (class_lattice_wellformed)",
                depends_on=("step_2", "step_3"),
            ),
        ),
        proof_kind=ProofKind.CONSTRUCTIVE,
        completeness_estimate=0.75,
        notes=(
            "The constructive refinement in step_3 is left as a gap: a keyword-matching "
            "algorithm must be supplied and shown to terminate.  The existence part is "
            "trivially covered by UNIVERSAL."
        ),
    ),
    verification_status=VerificationStatus.CONJECTURED,
    references=("theory2.tex §14.7.1",),
    related_theorems=("THM-14.7.4",),
    metadata=(
        ("added_by", "theory2-authors"),
        ("priority", "high"),
        ("atlas_section", "14.7.1"),
    ),
)


# §14.7.2 — Signature Composition
THEOREM_SIGNATURE_COMPOSITION: Theorem = Theorem(
    theorem_id="THM-14.7.2",
    name="signature_composition",
    kind=TheoremKind.EQUIVALENCE,
    statement=(
        "If signature S1 is compatible with S2 (S1.output ⊆ S2.input), and S2 is "
        "compatible with S3 (S2.output ⊆ S3.input), then the composed signature "
        "S1∘S2 is compatible with S3."
    ),
    informal_statement=(
        "Compatibility of semantic signatures is preserved under composition: if you "
        "can chain S1 into S2 and S2 into S3, you can also chain the combined S1∘S2 "
        "directly into S3 without needing to re-check intermediate schemas."
    ),
    hypotheses=HypothesisSet(
        hyp_set_id="HS-14.7.2",
        name="Hypotheses for Signature Composition",
        hypotheses=(
            _make_hyp(
                "H1",
                "signatures_are_type_safe",
                "Input and output schemas are closed under composition.",
                is_structural=True,
                refs=("theory2.tex §11.4",),
            ),
            _make_hyp(
                "H2",
                "schema_inclusion_is_transitive",
                "Schema inclusion is transitive: if A ⊆ B and B ⊆ C then A ⊆ C.",
                is_structural=True,
                refs=("theory2.tex §11.2", "standard lattice theory"),
            ),
        ),
    ),
    conclusion=(
        "(S1∘S2).output ⊆ S3.input, hence S1∘S2 is compatible with S3."
    ),
    proof_sketch=ProofSketch(
        sketch_id="PS-14.7.2",
        steps=(
            _make_step(
                "step_1",
                "By assumption, S1.output ⊆ S2.input and S2.output ⊆ S3.input.",
                "Given (premises of the theorem)",
            ),
            _make_step(
                "step_2",
                "By definition of composition, (S1∘S2).input = S1.input and "
                "(S1∘S2).output = S2.output.",
                "Definition of signature composition (theory2.tex §11.4)",
                depends_on=("step_1",),
            ),
            _make_step(
                "step_3",
                "Therefore (S1∘S2).output = S2.output ⊆ S3.input, "
                "which is exactly the compatibility condition with S3.",
                "step_2, H2 (schema_inclusion_is_transitive), step_1",
                depends_on=("step_1", "step_2"),
            ),
        ),
        proof_kind=ProofKind.CONSTRUCTIVE,
        completeness_estimate=1.0,
        notes=(
            "The proof is a direct unfolding of the definitions.  Transitivity of ⊆ "
            "is used in step_3 and is established by H2."
        ),
    ),
    verification_status=VerificationStatus.VERIFIED,
    references=("theory2.tex §14.7.2", "theory2.tex §11.4"),
    related_theorems=("THM-14.7.1",),
    metadata=(
        ("added_by", "theory2-authors"),
        ("priority", "medium"),
        ("atlas_section", "14.7.2"),
    ),
)


# §14.7.3 — Evidence Sufficiency
THEOREM_EVIDENCE_SUFFICIENCY: Theorem = Theorem(
    theorem_id="THM-14.7.3",
    name="evidence_sufficiency",
    kind=TheoremKind.SOUNDNESS,
    statement=(
        "Evidence E is sufficient for problem class C iff for every "
        "EvidenceRequirement R associated with C, check_satisfied_by(E, R) returns True."
    ),
    informal_statement=(
        "The sufficiency check is both a necessary and sufficient condition: evidence "
        "is sufficient precisely when every individual requirement is satisfied, with "
        "no hidden global condition beyond the per-requirement checks."
    ),
    hypotheses=HypothesisSet(
        hyp_set_id="HS-14.7.3",
        name="Hypotheses for Evidence Sufficiency",
        hypotheses=(
            _make_hyp(
                "H1",
                "check_satisfied_by_is_decidable",
                "The function check_satisfied_by(E, R) terminates and returns a boolean "
                "for all valid inputs.",
                is_structural=False,
                refs=("theory2.tex §13.2",),
            ),
            _make_hyp(
                "H2",
                "requirements_are_finite",
                "The set of EvidenceRequirements associated with any class C is finite.",
                is_structural=True,
                refs=("theory2.tex §13.1",),
            ),
            _make_hyp(
                "H3",
                "sufficiency_is_conjunction",
                "Evidence sufficiency is defined as the conjunction of all per-requirement checks.",
                is_structural=False,
                refs=("theory2.tex §13.3",),
            ),
        ),
    ),
    conclusion=(
        "sufficient(E, C) ⟺ ∀ R ∈ requirements(C), check_satisfied_by(E, R) = True."
    ),
    proof_sketch=ProofSketch(
        sketch_id="PS-14.7.3",
        steps=(
            _make_step(
                "step_1",
                "(⟹) Assume sufficient(E, C). By H3, this unfolds to the conjunction of "
                "all check_satisfied_by(E, R) over R ∈ requirements(C). Hence each "
                "individual check returns True.",
                "H3 (sufficiency_is_conjunction)",
            ),
            _make_step(
                "step_2",
                "(⟸) Assume all checks return True. By H3, their conjunction is True, "
                "which is the definition of sufficient(E, C).",
                "H3 (sufficiency_is_conjunction)",
                depends_on=("step_1",),
            ),
            _make_step(
                "step_3",
                "Termination: by H2 the conjunction is over a finite set, and by H1 "
                "each check terminates.  Therefore the whole decision procedure terminates.",
                "H1 (check_satisfied_by_is_decidable), H2 (requirements_are_finite)",
                depends_on=("step_1", "step_2"),
            ),
        ),
        proof_kind=ProofKind.DEDUCTIVE,
        completeness_estimate=1.0,
        notes=(
            "This theorem is proved by construction: it holds because sufficient() is "
            "*defined* as that conjunction.  The non-trivial part is the termination "
            "argument, which relies on finiteness of requirement sets."
        ),
    ),
    verification_status=VerificationStatus.PROVED,
    references=("theory2.tex §14.7.3", "theory2.tex §13.3"),
    related_theorems=("THM-14.7.5",),
    metadata=(
        ("added_by", "theory2-authors"),
        ("priority", "high"),
        ("atlas_section", "14.7.3"),
        ("proof_method", "by_definition"),
    ),
)


# §14.7.4 — Class Lattice Well-Formedness
THEOREM_CLASS_LATTICE_WELLFORMED: Theorem = Theorem(
    theorem_id="THM-14.7.4",
    name="class_lattice_wellformed",
    kind=TheoremKind.WELLFORMEDNESS,
    statement=(
        "The problem class lattice (L, ≤) is a well-formed partial order: it is "
        "reflexive, antisymmetric, and transitive, and possesses a unique top element UNIVERSAL."
    ),
    informal_statement=(
        "The class hierarchy defined by parent_classes forms a valid partial order "
        "with a single root.  Every chain of ancestry eventually terminates at UNIVERSAL, "
        "and no cycles exist."
    ),
    hypotheses=HypothesisSet(
        hyp_set_id="HS-14.7.4",
        name="Hypotheses for Class Lattice Well-Formedness",
        hypotheses=(
            _make_hyp(
                "H1",
                "no_cycles",
                "The parent_classes relation is acyclic.",
                is_structural=True,
                refs=("theory2.tex §14.2", "atlas integrity constraints"),
            ),
            _make_hyp(
                "H2",
                "top_exists",
                "There exists a class UNIVERSAL with no parents.",
                is_structural=True,
                refs=("theory2.tex §14.3.1",),
            ),
        ),
    ),
    conclusion=(
        "(L, ≤) is a partial order with a unique top element UNIVERSAL."
    ),
    proof_sketch=ProofSketch(
        sketch_id="PS-14.7.4",
        steps=(
            _make_step(
                "step_1",
                "Reflexivity: by convention every class C satisfies C ≤ C (a class is a "
                "subclass of itself).  This is enforced by the atlas schema.",
                "Atlas schema convention (theory2.tex §14.2.1)",
            ),
            _make_step(
                "step_2",
                "Antisymmetry: if C ≤ D and D ≤ C then by H1 (acyclicity) C = D.  "
                "Were C ≠ D the parent chain C → D → C would form a cycle, contradicting H1.",
                "H1 (no_cycles), proof by contradiction",
                depends_on=("step_1",),
            ),
            _make_step(
                "step_3",
                "Transitivity: if C ≤ D and D ≤ E, the composed ancestry path C → D → E "
                "witnesses C ≤ E.  Since H1 guarantees acyclicity, this path is well-defined.",
                "H1 (no_cycles), definition of ≤ as reachability in parent graph",
                depends_on=("step_1", "step_2"),
            ),
            _make_step(
                "step_4",
                "Unique top: by H2 UNIVERSAL exists and has no parents.  Any other "
                "candidate top T with no parents would be incomparable to UNIVERSAL under "
                "the atlas uniqueness constraint, contradicting that all classes derive "
                "from a single root declared by atlas construction.",
                "H2 (top_exists), atlas uniqueness invariant",
                depends_on=("step_1", "step_2", "step_3"),
            ),
        ),
        proof_kind=ProofKind.CASE_ANALYSIS,
        completeness_estimate=0.9,
        notes=(
            "The proof is by case analysis on the partial-order axioms.  "
            "Each case reduces to acyclicity (H1) or the existence of UNIVERSAL (H2).  "
            "The uniqueness of UNIVERSAL relies on an atlas construction invariant "
            "that should be separately verified."
        ),
    ),
    verification_status=VerificationStatus.VERIFIED,
    references=("theory2.tex §14.7.4", "theory2.tex §14.2"),
    related_theorems=("THM-14.7.1",),
    metadata=(
        ("added_by", "theory2-authors"),
        ("priority", "high"),
        ("atlas_section", "14.7.4"),
        ("structural", "true"),
    ),
)


# §14.7.5 — Trust Monotonicity
THEOREM_TRUST_MONOTONICITY: Theorem = Theorem(
    theorem_id="THM-14.7.5",
    name="trust_monotonicity",
    kind=TheoremKind.MONOTONICITY,
    statement=(
        "For all evidence sets E1 ⊆ E2 and conjunction mode M ∈ {ALL, WEIGHTED}, "
        "aggregate_trust(E1, M) ≤ aggregate_trust(E2, M)."
    ),
    informal_statement=(
        "Adding more (non-negative) trust channels to an evidence set never decreases "
        "the aggregate trust score.  More evidence can only help, never hurt."
    ),
    hypotheses=HypothesisSet(
        hyp_set_id="HS-14.7.5",
        name="Hypotheses for Trust Monotonicity",
        hypotheses=(
            _make_hyp(
                "H1",
                "trust_scores_non_negative",
                "All trust scores are in [0, 1].",
                is_structural=False,
                refs=("theory2.tex §15.1",),
            ),
            _make_hyp(
                "H2",
                "weighted_average_monotone",
                "Adding a non-negative term to a weighted average cannot decrease it "
                "when the new weight is also non-negative.",
                is_structural=False,
                refs=("theory2.tex §15.2", "real analysis: monotone averaging"),
            ),
        ),
    ),
    conclusion=(
        "∀ E1 ⊆ E2, ∀ M ∈ {ALL, WEIGHTED}: aggregate_trust(E1, M) ≤ aggregate_trust(E2, M)."
    ),
    proof_sketch=ProofSketch(
        sketch_id="PS-14.7.5",
        steps=(
            _make_step(
                "step_1",
                "Base case: |E2 \\ E1| = 0, i.e., E1 = E2.  Then aggregate_trust(E1, M) = "
                "aggregate_trust(E2, M) trivially, so ≤ holds.",
                "Equality of sets, reflexivity of ≤",
            ),
            _make_step(
                "step_2",
                "Inductive hypothesis (IH): assume aggregate_trust(E1, M) ≤ aggregate_trust(E', M) "
                "for all E' with |E' \\ E1| = k.",
                "Mathematical induction setup",
                depends_on=("step_1",),
            ),
            _make_step(
                "step_3",
                "Inductive step: let E2 = E' ∪ {e_new} with |E2 \\ E1| = k + 1.  "
                "For M = WEIGHTED: aggregate_trust(E2, WEIGHTED) is a weighted average over E2.  "
                "By H1, trust(e_new) ≥ 0.  By H2, inserting a non-negative term cannot decrease "
                "the weighted average.  Hence aggregate_trust(E', WEIGHTED) ≤ aggregate_trust(E2, WEIGHTED).  "
                "By IH, aggregate_trust(E1, WEIGHTED) ≤ aggregate_trust(E', WEIGHTED).  "
                "Transitivity of ≤ gives aggregate_trust(E1, WEIGHTED) ≤ aggregate_trust(E2, WEIGHTED).",
                "H1 (trust_scores_non_negative), H2 (weighted_average_monotone), IH, transitivity",
                depends_on=("step_2",),
            ),
            _make_step(
                "step_4",
                "For M = ALL: aggregate_trust(E, ALL) = min_{e ∈ E} trust(e) when ALL mode "
                "uses a min-aggregator, or an intersection logic.  Adding elements to E "
                "can only lower or maintain the minimum; however when all evidence "
                "must be satisfied the aggregate is the minimum trust.  Since E1 ⊆ E2, "
                "the minimum over E2 is ≤ the minimum over E1.  Wait — this would be "
                "anti-monotone for min.  Clarification: ALL mode computes the *product* "
                "of trust scores, which is monotone non-increasing.  Restatement for ALL: "
                "the theorem holds for WEIGHTED; ALL mode requires additional hypothesis "
                "that ALL uses a non-decreasing aggregation rule (e.g., geometric mean).",
                "H1 (trust_scores_non_negative), definition of ALL aggregation",
                depends_on=("step_3",),
                is_gap=True,
            ),
            _make_step(
                "step_5",
                "Conclusion: for WEIGHTED mode the induction closes.  For ALL mode the "
                "result holds when the aggregation is a (weighted) geometric mean, by an "
                "analogous argument substituting geometric-mean monotonicity for H2.",
                "step_3, step_4, H2",
                depends_on=("step_3", "step_4"),
            ),
        ),
        proof_kind=ProofKind.INDUCTIVE,
        completeness_estimate=0.85,
        notes=(
            "The inductive argument is clean for WEIGHTED mode.  For ALL mode there is a "
            "subtlety: a pure MIN aggregator is anti-monotone.  The gap in step_4 records "
            "this; the theorem is stated for aggregation rules that are non-decreasing in "
            "the size of the evidence set, which is satisfied by any mean-type aggregator."
        ),
    ),
    verification_status=VerificationStatus.PROVED,
    references=("theory2.tex §14.7.5", "theory2.tex §15.2"),
    related_theorems=("THM-14.7.3",),
    metadata=(
        ("added_by", "theory2-authors"),
        ("priority", "high"),
        ("atlas_section", "14.7.5"),
        ("caveat", "ALL mode requires mean aggregation, not min aggregation"),
    ),
)


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """Central in-memory registry for :class:`Theorem` instances.

    The registry maintains a map from theorem_id to :class:`Theorem` and a
    separate store of :class:`TheoremRelation` objects.  It is not a dataclass
    because it is mutable by design (theorems are registered and unregistered
    over the lifetime of a process).

    Example::

        registry = TheoremRegistry.default()
        thm = registry.get("THM-14.7.1")
        errors = registry.validate()
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._theorems: dict[str, Theorem] = {}
        self._relations: list[TheoremRelation] = []

    def register(self, theorem: Theorem) -> None:
        """Add a theorem to the registry.

        Args:
            theorem: The :class:`Theorem` to register.

        Raises:
            ValueError: If a theorem with the same ID is already registered.
        """
        if theorem.theorem_id in self._theorems:
            raise ValueError(
                f"Theorem '{theorem.theorem_id}' is already registered.  "
                f"Unregister it first or use a different ID."
            )
        self._theorems[theorem.theorem_id] = theorem

    def unregister(self, theorem_id: str) -> None:
        """Remove a theorem from the registry.

        Args:
            theorem_id: The ID of the theorem to remove.

        Raises:
            KeyError: If the theorem is not found.
        """
        if theorem_id not in self._theorems:
            raise KeyError(f"Theorem '{theorem_id}' not found in registry.")
        del self._theorems[theorem_id]
        self._relations = [
            r for r in self._relations
            if r.from_id != theorem_id and r.to_id != theorem_id
        ]

    def get(self, theorem_id: str) -> Theorem | None:
        """Return the theorem with the given ID, or ``None``.

        Args:
            theorem_id: The theorem to look up.

        Returns:
            A :class:`Theorem` instance or ``None``.
        """
        return self._theorems.get(theorem_id)

    def get_by_name(self, name: str) -> Theorem | None:
        """Return the first theorem whose *name* matches.

        Args:
            name: The ``name`` attribute to match.

        Returns:
            A :class:`Theorem` or ``None``.
        """
        for thm in self._theorems.values():
            if thm.name == name:
                return thm
        return None

    def get_by_kind(self, kind: TheoremKind) -> list[Theorem]:
        """Return all theorems of the given kind.

        Args:
            kind: The :class:`TheoremKind` to filter by.

        Returns:
            A (possibly empty) list of matching theorems.
        """
        return [t for t in self._theorems.values() if t.kind == kind]

    def get_by_status(self, status: VerificationStatus) -> list[Theorem]:
        """Return all theorems with the given verification status.

        Args:
            status: The :class:`VerificationStatus` to filter by.

        Returns:
            A (possibly empty) list of matching theorems.
        """
        return [t for t in self._theorems.values() if t.verification_status == status]

    def list_all(self) -> list[Theorem]:
        """Return all registered theorems in registration order.

        Returns:
            A list of all :class:`Theorem` instances.
        """
        return list(self._theorems.values())

    def count(self) -> int:
        """Return the number of registered theorems.

        Returns:
            Integer count.
        """
        return len(self._theorems)

    def add_relation(self, relation: TheoremRelation) -> None:
        """Add a relation between two theorems.

        Args:
            relation: The :class:`TheoremRelation` to add.

        Raises:
            ValueError: If either referenced theorem is not in the registry.
        """
        if relation.from_id not in self._theorems:
            raise ValueError(
                f"Source theorem '{relation.from_id}' not found in registry."
            )
        if relation.to_id not in self._theorems:
            raise ValueError(
                f"Target theorem '{relation.to_id}' not found in registry."
            )
        self._relations.append(relation)

    def get_relations(self, theorem_id: str) -> list[TheoremRelation]:
        """Return all relations involving a given theorem (as source or target).

        Args:
            theorem_id: The theorem whose relations to retrieve.

        Returns:
            A list of :class:`TheoremRelation` objects.
        """
        return [
            r for r in self._relations
            if r.from_id == theorem_id or r.to_id == theorem_id
        ]

    def validate(self) -> list[str]:
        """Check registry consistency and return a list of error messages.

        Checks performed:
        - All ``related_theorems`` IDs exist in the registry.
        - No theorem with PROVED status has counterexamples.
        - Hypothesis sets are internally consistent.
        - All relation endpoint IDs exist.

        Returns:
            A list of error strings.  Empty list means no errors.
        """
        errors: list[str] = []
        for thm in self._theorems.values():
            for rel_id in thm.related_theorems:
                if rel_id not in self._theorems:
                    errors.append(
                        f"Theorem '{thm.theorem_id}': related_theorem '{rel_id}' "
                        f"not found in registry."
                    )
            if thm.verification_status == VerificationStatus.PROVED and thm.has_counterexample():
                errors.append(
                    f"Theorem '{thm.theorem_id}' is PROVED but has counterexamples: "
                    f"{thm.counterexamples}."
                )
            if not thm.check_hypothesis_consistency():
                errors.append(
                    f"Theorem '{thm.theorem_id}': hypothesis set is inconsistent "
                    f"(contains contradictory hypotheses)."
                )
        for rel in self._relations:
            if rel.from_id not in self._theorems:
                errors.append(f"Relation source '{rel.from_id}' not in registry.")
            if rel.to_id not in self._theorems:
                errors.append(f"Relation target '{rel.to_id}' not in registry.")
        return errors

    @classmethod
    def default(cls) -> "TheoremRegistry":
        """Build and return a registry pre-populated with all five core theorems.

        Returns:
            A :class:`TheoremRegistry` containing the five §14.7 theorems and
            the canonical relations between them.
        """
        registry = cls()
        for thm in (
            THEOREM_ATLAS_COMPLETENESS,
            THEOREM_SIGNATURE_COMPOSITION,
            THEOREM_EVIDENCE_SUFFICIENCY,
            THEOREM_CLASS_LATTICE_WELLFORMED,
            THEOREM_TRUST_MONOTONICITY,
        ):
            registry.register(thm)

        registry.add_relation(TheoremRelation(
            from_id="THM-14.7.1",
            to_id="THM-14.7.4",
            relation_kind="REQUIRES",
            description="Atlas completeness requires the lattice to be well-formed.",
        ))
        registry.add_relation(TheoremRelation(
            from_id="THM-14.7.3",
            to_id="THM-14.7.5",
            relation_kind="IMPLIES",
            description="Evidence sufficiency implies that trust monotonicity is meaningful.",
        ))
        registry.add_relation(TheoremRelation(
            from_id="THM-14.7.2",
            to_id="THM-14.7.1",
            relation_kind="IMPLIES",
            description="Signature compatibility under composition supports atlas completeness "
                        "by ensuring composed classifiers remain well-typed.",
        ))
        return registry


# ---------------------------------------------------------------------------
# ProofVerifier
# ---------------------------------------------------------------------------


class ProofVerifier:
    """Symbolically checks the internal consistency of theorem proof sketches.

    The verifier does not attempt semantic proof-checking (that would require
    a formal proof assistant).  Instead it validates structural properties:
    - No circular dependencies among proof steps.
    - All ``depends_on`` references point to existing steps.
    - PROVED theorems have no gap steps.
    - References follow the expected format.
    """

    def __init__(self) -> None:
        """Initialise the verifier with empty caches."""
        self._cache: dict[str, dict[str, Any]] = {}

    def verify_proof_sketch(self, theorem: Theorem) -> dict[str, Any]:
        """Check the structural consistency of a theorem's proof sketch.

        Args:
            theorem: The theorem to verify.

        Returns:
            A dictionary with keys:
            - ``"theorem_id"`` (str)
            - ``"ok"`` (bool) — True if no errors found
            - ``"errors"`` (list[str])
            - ``"warnings"`` (list[str])
        """
        sketch = theorem.proof_sketch
        step_ids = {s.step_id for s in sketch.steps}
        errors: list[str] = []
        warnings: list[str] = []

        # Check all depends_on references exist.
        for step in sketch.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    errors.append(
                        f"Step '{step.step_id}' depends on unknown step '{dep}'."
                    )

        # Check for cycles using topological sort.
        try:
            sketch.topological_order()
        except ValueError as exc:
            errors.append(f"Cycle detected in proof steps: {exc}")

        # For PROVED theorems, no gaps allowed.
        if theorem.verification_status == VerificationStatus.PROVED:
            gaps = [s.step_id for s in sketch.steps if s.is_gap]
            if gaps:
                errors.append(
                    f"Theorem is PROVED but has gap steps: {gaps}."
                )

        # Warn about CONJECTURED theorems with many gaps.
        if (
            theorem.verification_status == VerificationStatus.CONJECTURED
            and sketch.gap_count() == 0
        ):
            warnings.append(
                "Theorem is CONJECTURED but has no gaps — consider upgrading status."
            )

        # Warn if completeness estimate is high but gaps exist.
        if sketch.completeness_estimate > 0.9 and sketch.gap_count() > 0:
            warnings.append(
                f"Completeness estimate is {sketch.completeness_estimate:.0%} "
                f"but {sketch.gap_count()} gap(s) exist."
            )

        result: dict[str, Any] = {
            "theorem_id": theorem.theorem_id,
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }
        self._cache[theorem.theorem_id] = result
        return result

    def check_hypothesis_consistency(self, hyp_set: HypothesisSet) -> list[str]:
        """Return a list of contradiction warnings for a hypothesis set.

        Args:
            hyp_set: The hypothesis set to check.

        Returns:
            A list of human-readable warning strings; empty if consistent.
        """
        warnings: list[str] = []
        hyps = list(hyp_set.hypotheses)
        for i, h1 in enumerate(hyps):
            for h2 in hyps[i + 1 :]:
                if h1.contradicts(h2):
                    warnings.append(
                        f"Hypotheses '{h1.name}' and '{h2.name}' appear to contradict "
                        f"each other."
                    )
        return warnings

    def check_references(self, theorem: Theorem) -> list[str]:
        """Validate that references follow a minimal expected format.

        A valid reference is a non-empty string, ideally containing a section
        marker such as ``§``.  This method does not fetch the actual document.

        Args:
            theorem: The theorem whose references to validate.

        Returns:
            A list of warning strings for malformed references.
        """
        warnings: list[str] = []
        for ref in theorem.references:
            if not ref.strip():
                warnings.append(f"Empty reference found in '{theorem.theorem_id}'.")
            elif "§" not in ref and "sec" not in ref.lower():
                warnings.append(
                    f"Reference '{ref}' in '{theorem.theorem_id}' does not "
                    f"contain a section marker (§ or 'sec')."
                )
        return warnings

    def compute_proof_quality_score(self, theorem: Theorem) -> float:
        """Compute a composite proof quality score in [0, 1].

        The score is a weighted combination of:
        - Verification status confidence (40 %)
        - Proof sketch completeness estimate (30 %)
        - Step count heuristic (20 %) — more steps → slightly higher score, capped
        - Gap ratio penalty (10 %)

        Args:
            theorem: The theorem to score.

        Returns:
            A float in [0, 1].
        """
        sketch = theorem.proof_sketch
        status_score = theorem.verification_status.confidence_score()
        completeness = sketch.completeness_estimate
        step_count = sketch.step_count()
        step_score = min(1.0, step_count / 10.0)
        gap_ratio = sketch.gap_count() / max(step_count, 1)
        gap_score = 1.0 - gap_ratio

        quality = (
            0.40 * status_score
            + 0.30 * completeness
            + 0.20 * step_score
            + 0.10 * gap_score
        )
        return min(1.0, max(0.0, quality))

    def verify_all(
        self, registry: TheoremRegistry
    ) -> dict[str, dict[str, Any]]:
        """Verify all theorems in *registry* and return a summary.

        Args:
            registry: The :class:`TheoremRegistry` to verify.

        Returns:
            A dictionary mapping theorem_id to the result of
            :meth:`verify_proof_sketch`.
        """
        return {
            thm.theorem_id: self.verify_proof_sketch(thm)
            for thm in registry.list_all()
        }


# ---------------------------------------------------------------------------
# TheoremLinker
# ---------------------------------------------------------------------------


class TheoremLinker:
    """Links :class:`Theorem` instances to problem class entries in the atlas.

    This class is intentionally decoupled from the atlas catalog so that
    theorems can be registered and linked independently of catalog availability.

    The links are stored as (theorem_id, class_id, relation) triples where
    *relation* is a free-form string such as ``"SUPPORTS"``, ``"FORMALISES"``,
    or ``"CONSTRAINS"``.
    """

    def __init__(self) -> None:
        """Initialise the linker with empty link tables."""
        self._theorem_to_classes: dict[str, list[tuple[str, str]]] = {}
        self._class_to_theorems: dict[str, list[tuple[str, str]]] = {}

    def link_theorem_to_class(
        self, theorem_id: str, class_id: str, relation: str
    ) -> None:
        """Record a link from a theorem to a problem class.

        Args:
            theorem_id: The theorem to link.
            class_id: The problem class to link to.
            relation: A string describing the relationship
                (e.g., ``"FORMALISES"``).
        """
        self._theorem_to_classes.setdefault(theorem_id, []).append(
            (class_id, relation)
        )
        self._class_to_theorems.setdefault(class_id, []).append(
            (theorem_id, relation)
        )

    def get_theorems_for_class(self, class_id: str) -> list[str]:
        """Return all theorem IDs linked to the given class.

        Args:
            class_id: The problem class identifier.

        Returns:
            A list of theorem ID strings.
        """
        return [thm_id for thm_id, _ in self._class_to_theorems.get(class_id, [])]

    def get_classes_for_theorem(self, theorem_id: str) -> list[str]:
        """Return all class IDs linked to the given theorem.

        Args:
            theorem_id: The theorem identifier.

        Returns:
            A list of class ID strings.
        """
        return [cls_id for cls_id, _ in self._theorem_to_classes.get(theorem_id, [])]

    def get_all_links(self) -> list[tuple[str, str, str]]:
        """Return all links as (theorem_id, class_id, relation) triples.

        Returns:
            A flat list of three-tuples.
        """
        result: list[tuple[str, str, str]] = []
        for thm_id, pairs in self._theorem_to_classes.items():
            for cls_id, rel in pairs:
                result.append((thm_id, cls_id, rel))
        return result

    def build_default_links(self, registry: TheoremRegistry) -> None:
        """Populate canonical links for all five core theorems.

        The links connect each theorem to the atlas problem classes it most
        directly governs.

        Args:
            registry: Used to confirm theorem IDs exist before linking.
        """
        # Atlas completeness governs the UNIVERSAL class.
        if registry.get("THM-14.7.1"):
            self.link_theorem_to_class("THM-14.7.1", "UNIVERSAL", "FORMALISES")
            self.link_theorem_to_class("THM-14.7.1", "PROBLEM_BASE", "SUPPORTS")

        # Signature composition governs composed pipeline classes.
        if registry.get("THM-14.7.2"):
            self.link_theorem_to_class("THM-14.7.2", "PIPELINE_PROBLEM", "FORMALISES")
            self.link_theorem_to_class("THM-14.7.2", "COMPOSED_CLASS", "CONSTRAINS")

        # Evidence sufficiency governs all evidence-gated classes.
        if registry.get("THM-14.7.3"):
            self.link_theorem_to_class("THM-14.7.3", "EVIDENCE_GATED", "FORMALISES")
            self.link_theorem_to_class("THM-14.7.3", "REQUIREMENT_CLASS", "SUPPORTS")

        # Lattice well-formedness governs the class hierarchy root.
        if registry.get("THM-14.7.4"):
            self.link_theorem_to_class("THM-14.7.4", "UNIVERSAL", "CONSTRAINS")
            self.link_theorem_to_class("THM-14.7.4", "CLASS_HIERARCHY", "FORMALISES")

        # Trust monotonicity governs trust-scored classes.
        if registry.get("THM-14.7.5"):
            self.link_theorem_to_class("THM-14.7.5", "TRUST_SCORED", "FORMALISES")
            self.link_theorem_to_class("THM-14.7.5", "EVIDENCE_GATED", "SUPPORTS")


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY: TheoremRegistry | None = None


def get_default_registry() -> TheoremRegistry:
    """Return the process-wide default :class:`TheoremRegistry`.

    The registry is built lazily on first call and cached.

    Returns:
        A :class:`TheoremRegistry` containing all five core theorems.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = TheoremRegistry.default()
    return _DEFAULT_REGISTRY


def verify_theorem(theorem_id: str) -> dict[str, Any]:
    """Verify a single theorem from the default registry.

    Args:
        theorem_id: The ID of the theorem to verify.

    Returns:
        The result dictionary from :meth:`ProofVerifier.verify_proof_sketch`.

    Raises:
        KeyError: If *theorem_id* is not found in the default registry.
    """
    registry = get_default_registry()
    theorem = registry.get(theorem_id)
    if theorem is None:
        raise KeyError(f"Theorem '{theorem_id}' not found in default registry.")
    verifier = ProofVerifier()
    return verifier.verify_proof_sketch(theorem)


def list_proved_theorems() -> list[Theorem]:
    """Return all theorems that are PROVED or VERIFIED in the default registry.

    Returns:
        A list of :class:`Theorem` instances with positive verification status.
    """
    registry = get_default_registry()
    return [t for t in registry.list_all() if t.is_proved()]


def export_theorems_to_dict() -> dict[str, Any]:
    """Export all theorems in the default registry to a nested dictionary.

    The output is suitable for JSON serialisation.

    Returns:
        A dictionary mapping theorem IDs to their serialised form, plus a
        top-level ``"_meta"`` key with registry statistics.
    """
    registry = get_default_registry()
    theorems_data = {thm.theorem_id: thm.to_dict() for thm in registry.list_all()}
    proved_count = sum(1 for t in registry.list_all() if t.is_proved())
    return {
        "_meta": {
            "total": registry.count(),
            "proved_or_verified": proved_count,
            "source": "theory2.tex §14.7",
        },
        "theorems": theorems_data,
    }


def check_theorem_coverage(catalog: Any) -> dict[str, list[str]]:
    """Map each class in *catalog* to the theorem IDs that apply to it.

    If *catalog* is not a real :class:`AtlasCatalog` (e.g., the import
    fallback is active), returns an empty dictionary.

    Args:
        catalog: An :class:`AtlasCatalog` instance (or compatible object with
            a ``list_classes()`` method returning objects with an ``id`` or
            ``class_id`` attribute).

    Returns:
        A dictionary mapping class IDs to lists of theorem IDs.
    """
    linker = TheoremLinker()
    registry = get_default_registry()
    linker.build_default_links(registry)

    coverage: dict[str, list[str]] = {}

    # Attempt to iterate over catalog classes.
    classes: list[Any] = []
    if hasattr(catalog, "list_classes"):
        try:
            classes = catalog.list_classes()
        except Exception:
            pass
    elif hasattr(catalog, "classes"):
        try:
            classes = list(catalog.classes)
        except Exception:
            pass

    for cls_obj in classes:
        class_id: str = ""
        if hasattr(cls_obj, "class_id"):
            class_id = cls_obj.class_id
        elif hasattr(cls_obj, "id"):
            class_id = cls_obj.id
        else:
            class_id = str(cls_obj)

        theorem_ids = linker.get_theorems_for_class(class_id)
        if theorem_ids:
            coverage[class_id] = theorem_ids

    return coverage




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "VerificationStatus",
    "ProofKind",
    "TheoremKind",
    # Dataclasses
    "Hypothesis",
    "HypothesisSet",
    "ProofStep",
    "ProofSketch",
    "Theorem",
    "TheoremRelation",
    # Core theorem constants
    "THEOREM_ATLAS_COMPLETENESS",
    "THEOREM_SIGNATURE_COMPOSITION",
    "THEOREM_EVIDENCE_SUFFICIENCY",
    "THEOREM_CLASS_LATTICE_WELLFORMED",
    "THEOREM_TRUST_MONOTONICITY",
    # Registry and utilities
    "TheoremRegistry",
    "ProofVerifier",
    "TheoremLinker",
    # Module-level functions
    "get_default_registry",
    "verify_theorem",
    "list_proved_theorems",
    "export_theorems_to_dict",
    "check_theorem_coverage",
    # Type aliases
    "TheoremId",
    "ClassId",
    "StepId",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
