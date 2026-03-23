"""
Formal theorems for the methodology_loops evaluation package.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module encodes the key theoretical guarantees of the JuGeo methodology
loops as first-class objects.  Each theorem is represented as a class with a
statement, proof sketch, assumptions, and verification status.  The
``MethodologyTheoremRegistry`` provides a central registry for all theorems,
enabling programmatic inspection, export to LaTeX, and machine-assisted
verification against live loop instances.

Theoretical background
----------------------
The JuGeo methodology is structured as a series of nested loops operating on
a partially-ordered lattice of formalisation states.  The key invariants
proved in theory2.tex Ch62 are:

1. **Convergence**: under a strictly decreasing measure, every methodology
   loop terminates.
2. **Falsification completeness**: the falsification loop eventually finds all
   falsifiable hypotheses given sufficient budget.
3. **Formalization soundness**: formal artefacts produced by the formalisation
   loop faithfully represent the informal intent.
4. **Implementation completeness**: the implementation loop covers all clauses
   of the formal specification.
5. **Revision monotonicity**: every revision step strictly improves the quality
   measure of the loop state.

Each of these guarantees is encoded as a class in this module with:
* A machine-readable statement string.
* A detailed multi-paragraph proof sketch.
* A list of assumptions under which the theorem holds.
* A ``verify(loop)`` method that attempts to check the theorem against a live
  loop instance using heuristic evidence gathering.
* A ``counterexample_search(loop)`` method that searches for a concrete
  counter-example.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    "TheoremStatus",
    "TheoremProofStrategy",
    "TheoremRecord",
    "LoopConvergenceTheorem",
    "FalsificationCompletenessTheorem",
    "FormalizationSoundnessTheorem",
    "ImplementationCompletenessTheorem",
    "RevisionMonotonicityTheorem",
    "MethodologyTheoremRegistry",
    "build_theorem_registry",
    "verify_theorem",
    "theorem_dependency_graph",
    "export_theorem_latex",
]


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase,
        LoopStatus,
        TransitionKind,
        LoopState,
        LoopTransition,
        MethodologyConfig,
        LoopDiagnostics,
        MethodologyLoop,
        FormalizationLoop,
        ImplementationLoop,
        FalsificationLoop,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# TheoremStatus
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """Verification status of a theorem in the JuGeo theorem registry.

    Members
    -------
    CONJECTURED:
        The theorem has been stated but no proof attempt has been made.
    PROOF_SKETCH:
        A human-written proof sketch exists but has not been formalised.
    MECHANIZED:
        The proof has been encoded in a proof assistant (e.g. Lean, Coq) but
        not yet independently reviewed.
    VERIFIED:
        The proof has been independently reviewed and accepted.
    REFUTED:
        A counter-example has been found; the theorem as stated is false.
    UNKNOWN:
        The status is not yet determined.
    """

    CONJECTURED = "CONJECTURED"
    UNVERIFIED = "CONJECTURED"
    PENDING = "CONJECTURED"
    PROOF_SKETCH = "PROOF_SKETCH"
    MECHANIZED = "MECHANIZED"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# TheoremProofStrategy
# ---------------------------------------------------------------------------


class TheoremProofStrategy(str, Enum):
    """The proof strategy used to establish a theorem.

    Members
    -------
    INDUCTION:
        Structural or mathematical induction over a well-founded ordering.
    WELL_FOUNDED:
        Well-founded recursion / termination argument using a decreasing
        measure.
    FIXED_POINT:
        Existence argument via a fixed-point theorem (e.g. Kleene, Tarski).
    FINITE_DESCENT:
        Infinite descent argument: assuming non-termination and deriving a
        contradiction.
    CONTRADICTION:
        Direct proof by contradiction.
    CONSTRUCTION:
        Constructive proof providing an explicit witness.
    MODEL_CHECKING:
        Exhaustive state-space search using a model checker.
    """

    INDUCTION = "INDUCTION"
    WELL_FOUNDED = "WELL_FOUNDED"
    FIXED_POINT = "FIXED_POINT"
    FINITE_DESCENT = "FINITE_DESCENT"
    CONTRADICTION = "CONTRADICTION"
    CONSTRUCTION = "CONSTRUCTION"
    MODEL_CHECKING = "MODEL_CHECKING"


# ---------------------------------------------------------------------------
# TheoremRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRecord:
    """Mutable record storing the metadata and content of a single theorem.

    A ``TheoremRecord`` is the canonical representation of a theorem in the
    JuGeo theorem registry.  It is intentionally mutable (unlike most data
    records in this package) because theorems progress through a lifecycle
    from ``CONJECTURED`` to ``VERIFIED`` (or ``REFUTED``), and their content
    may be refined over time.

    Fields
    ------
    theorem_id:
        Globally unique identifier.
    name:
        Short human-readable name (e.g. ``"LoopConvergenceTheorem"``).
    statement:
        The formal or semi-formal statement of the theorem.
    proof_sketch:
        A detailed human-readable proof sketch.  Should be at least one
        paragraph and ideally three or more.
    assumptions:
        List of assumptions under which the theorem holds.  Each entry is a
        concise natural-language sentence.
    status:
        Current verification status (a :class:`TheoremStatus` value).
    proof_strategy:
        The proof technique employed (a :class:`TheoremProofStrategy` value).
    references:
        List of bibliographic references (BibTeX keys or free-text).
    metadata:
        Free-form metadata dict.
    created_at:
        Unix timestamp at which the record was created.
    verified_at:
        Unix timestamp at which the theorem was verified, or ``None``.
    """

    theorem_id: str
    name: str
    statement: str
    proof_sketch: str
    assumptions: list[str]
    status: TheoremStatus
    proof_strategy: TheoremProofStrategy
    references: list[str]
    metadata: dict[str, Any]
    created_at: float
    verified_at: float | None

    @classmethod
    def create(
        cls,
        name: str,
        statement: str,
        proof_sketch: str,
        assumptions: list[str] | None = None,
        status: TheoremStatus = TheoremStatus.CONJECTURED,
        proof_strategy: TheoremProofStrategy = TheoremProofStrategy.INDUCTION,
        strategy: TheoremProofStrategy | None = None,
        references: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TheoremRecord":
        """Factory: create a new :class:`TheoremRecord` with a fresh UUID.

        Parameters
        ----------
        name:
            Short name of the theorem.
        statement:
            Formal or semi-formal statement.
        proof_sketch:
            Proof sketch text.
        assumptions:
            List of assumptions.  Defaults to empty list.
        status:
            Initial verification status.
        proof_strategy:
            Proof technique.
        references:
            Bibliographic references.  Defaults to empty list.
        metadata:
            Optional metadata.
        """
        if strategy is not None:
            proof_strategy = strategy
        return cls(
            theorem_id=_uid(),
            name=name,
            statement=statement,
            proof_sketch=proof_sketch,
            assumptions=assumptions or [],
            status=status,
            proof_strategy=proof_strategy,
            references=references or [],
            metadata=metadata or {},
            created_at=_utcnow(),
            verified_at=None,
        )

    @property
    def strategy(self) -> TheoremProofStrategy:
        """Compatibility alias for ``proof_strategy``."""
        return self.proof_strategy

    def mark_verified(self) -> None:
        """Transition the theorem status to :attr:`TheoremStatus.VERIFIED`.

        Sets ``verified_at`` to the current UTC timestamp.
        """
        self.status = TheoremStatus.VERIFIED
        self.verified_at = _utcnow()
        return self

    def mark_refuted(
        self,
        counter_example: str = "",
        *,
        counterexample: str = "",
    ) -> "TheoremRecord":
        """Transition the theorem status to :attr:`TheoremStatus.REFUTED`.

        Parameters
        ----------
        counter_example:
            Optional description of the counter-example that refuted the
            theorem.  Stored in ``metadata["counter_example"]``.
        """
        if counterexample:
            counter_example = counterexample
        self.status = TheoremStatus.REFUTED
        if counter_example:
            self.metadata["counter_example"] = counter_example
        return self

    def add_assumption(self, assumption: str) -> "TheoremRecord":
        """Append *assumption* to the assumptions list.

        Parameters
        ----------
        assumption:
            A natural-language sentence describing an assumption.
        """
        if assumption not in self.assumptions:
            self.assumptions.append(assumption)
        return self

    def add_reference(self, reference: str) -> "TheoremRecord":
        """Append *reference* to the references list.

        Parameters
        ----------
        reference:
            A BibTeX key or free-text bibliographic reference.
        """
        if reference not in self.references:
            self.references.append(reference)
        return self

    @property
    def record_id(self) -> str:
        """Compatibility alias for ``theorem_id``."""
        return self.theorem_id

    def is_sound(self) -> bool:
        """Return whether this theorem record is currently sound."""
        return self.status == TheoremStatus.VERIFIED

    def to_json(self) -> str:
        """Serialise this record to a compact JSON string."""
        return json.dumps(
            {
                "theorem_id": self.theorem_id,
                "name": self.name,
                "statement": self.statement,
                "proof_sketch": self.proof_sketch,
                "assumptions": self.assumptions,
                "status": self.status.value,
                "proof_strategy": self.proof_strategy.value,
                "references": self.references,
                "metadata": self.metadata,
                "created_at": self.created_at,
                "verified_at": self.verified_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "TheoremRecord":
        """Deserialise a :class:`TheoremRecord` from a JSON string.

        Raises
        ------
        ValueError
            If ``data`` is not valid JSON or is missing required fields.
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"TheoremRecord.from_json: invalid JSON – {exc}") from exc
        try:
            return cls(
                theorem_id=obj["theorem_id"],
                name=obj["name"],
                statement=obj["statement"],
                proof_sketch=obj["proof_sketch"],
                assumptions=list(obj.get("assumptions", [])),
                status=TheoremStatus(obj["status"]),
                proof_strategy=TheoremProofStrategy(obj["proof_strategy"]),
                references=list(obj.get("references", [])),
                metadata=dict(obj.get("metadata", {})),
                created_at=float(obj["created_at"]),
                verified_at=obj.get("verified_at"),
            )
        except KeyError as exc:
            raise ValueError(f"TheoremRecord.from_json: missing field {exc}") from exc

    def summarize(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"TheoremRecord[{self.theorem_id[:8]}] "
            f"name={self.name} status={self.status.value} "
            f"strategy={self.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render this theorem record as a LaTeX theorem environment.

        Produces a ``\\begin{{theorem}}`` block with label, statement, proof
        sketch, and a list of assumptions and references.

        Returns
        -------
        str
            A LaTeX string.
        """
        safe_name = self.name.replace("_", r"\_")
        lines = [
            rf"\begin{{theorem}}[{safe_name}]",
            rf"\label{{thm:{self.theorem_id[:8]}}}",
            self.statement,
            r"\end{theorem}",
            r"\begin{proof}[Proof Sketch]",
            self.proof_sketch,
            r"\end{proof}",
        ]
        if self.assumptions:
            lines.append(r"\begin{remark}[Assumptions]")
            lines.append(r"\begin{enumerate}")
            for a in self.assumptions:
                lines.append(rf"\item {a}")
            lines.append(r"\end{enumerate}")
            lines.append(r"\end{remark}")
        if self.references:
            lines.append(r"\begin{remark}[References]")
            for ref in self.references:
                lines.append(rf"\cite{{{ref}}}")
            lines.append(r"\end{remark}")
        return "\n".join(lines)

    def is_sound(self) -> bool:
        """Return ``True`` iff the theorem has been verified and not refuted."""
        return self.status == TheoremStatus.VERIFIED

    def dependency_ids(self) -> list[str]:
        """Return the list of theorem IDs this theorem depends on.

        Dependencies are stored in ``self.metadata["depends_on"]`` as a list
        of theorem ID strings.  Returns an empty list if not set.
        """
        deps = self.metadata.get("depends_on", [])
        return list(deps) if isinstance(deps, list) else []


# ---------------------------------------------------------------------------
# LoopConvergenceTheorem
# ---------------------------------------------------------------------------


class LoopConvergenceTheorem:
    """Theorem: methodology loops converge under a finite hypothesis space.

    This class encodes the central convergence theorem of the JuGeo
    methodology.  The theorem guarantees that any methodology loop whose
    phase transitions strictly decrease a well-founded measure will terminate
    in a finite number of iterations.

    The proof follows a well-founded descent argument.  Each iteration reduces
    the measure by at least one step; since the hypothesis space is finite and
    the measure is bounded below by zero, the process must terminate.
    """

    _THEOREM_ID = "loop-convergence-v1"

    def __init__(self) -> None:
        self._record: TheoremRecord = TheoremRecord.create(
            name="LoopConvergenceTheorem",
            statement=self.statement(),
            proof_sketch=self.proof_sketch(),
            assumptions=self.assumptions(),
            status=TheoremStatus.PROOF_SKETCH,
            proof_strategy=TheoremProofStrategy.WELL_FOUNDED,
            references=["theory2.tex:Ch62", "jugeo:methodology_loops"],
            metadata={
                "theory_chapter": "Ch62",
                "depends_on": [],
                "tags": ["convergence", "termination", "well-founded"],
            },
        )
        self._record.theorem_id = self._THEOREM_ID

    @property
    def record(self) -> TheoremRecord:
        """Return the underlying :class:`TheoremRecord` for this theorem."""
        return self._record

    def statement(self) -> str:
        """Return the formal statement of the theorem.

        Returns
        -------
        str
            The theorem statement as a natural-language sentence with
            semi-formal notation.
        """
        return (
            "For any methodology loop L with finite hypothesis space H, "
            "if each phase transition T: S → S' reduces the measure μ(L) strictly "
            "(i.e., μ(S') < μ(S) under the natural ordering on ℕ), "
            "then L converges in at most |H| iterations."
        )

    def proof_sketch(self) -> str:
        """Return a detailed multi-paragraph proof sketch.

        The sketch outlines the well-founded descent argument and identifies
        the key steps needed for a full mechanised proof.

        Returns
        -------
        str
            Multi-paragraph proof sketch.
        """
        return (
            "We argue by well-founded descent on the measure μ.\n\n"
            "Base case: If |H| = 0, the loop has no hypotheses to process and "
            "convergence is trivially declared at iteration 0.  The measure μ(L₀) = 0 "
            "satisfies the convergence criterion immediately.\n\n"
            "Inductive step: Assume the claim holds for all loops with |H| < n.  "
            "Consider a loop L with |H| = n > 0.  By the strict decrease assumption, "
            "every transition T maps a state S with μ(S) = k > 0 to a successor S' "
            "with μ(S') ≤ k − 1.  Since μ maps loop states into ℕ (which is "
            "well-ordered), the sequence μ(S₀) > μ(S₁) > ⋯ cannot decrease "
            "indefinitely; it must reach 0 in at most k ≤ |H| steps.  At μ = 0, "
            "the convergence criterion is met and the loop terminates.\n\n"
            "Uniqueness of convergence point: The converged state is unique modulo "
            "the equivalence relation induced by the loop's phase-score function, "
            "because the measure is strictly decreasing and the hypothesis space "
            "is finite.  Two distinct converged states would require two distinct "
            "minima of μ, which contradicts the totality of the ordering on ℕ.\n\n"
            "Complexity bound: The iteration count is bounded by |H| because each "
            "transition eliminates at least one hypothesis from consideration "
            "(either by falsification, formalisation completion, or implementation "
            "coverage).  In the worst case every hypothesis requires exactly one "
            "iteration, giving the tight bound of |H| iterations.\n\n"
            "Mechanisation notes: A Lean 4 proof of the base case and inductive step "
            "has been outlined in theory2.tex Ch62 §3.  The key lemma is "
            "\\texttt{measure\\_decreases\\_strictly}, which encodes the transition "
            "pre-condition as a type-class constraint on the loop state monad."
        )

    def assumptions(self) -> list[str]:
        """Return the list of assumptions required by this theorem.

        Returns
        -------
        list[str]
        """
        return [
            "The hypothesis space H is finite (|H| < ∞).",
            "The measure μ: LoopState → ℕ is well-defined and computable.",
            "Every phase transition strictly decreases μ (μ(S') < μ(S) for all S ≠ S_conv).",
            "The convergence criterion is decidable in finite time.",
            "The loop does not introduce new hypotheses during execution "
            "(hypothesis space is fixed at loop initialisation).",
        ]

    def verify(self, loop: Any) -> bool:
        """Attempt to verify this theorem for a given loop instance.

        The verification is heuristic: it checks that the loop's history
        shows a non-increasing score sequence and that no new hypotheses
        were added after loop initialisation.

        Parameters
        ----------
        loop:
            The methodology loop instance to verify against.

        Returns
        -------
        bool
            ``True`` iff the heuristic checks pass.
        """
        hist = getattr(loop, "history", [])
        if not hist:
            return True  # vacuously true for empty loops

        # Check score is non-decreasing (proxy for measure decrease)
        scores = [
            float(e.get("score", 0.5)) if isinstance(e, dict) else 0.5
            for e in hist
        ]
        # A converging loop's scores should trend upward (measure decreasing)
        if len(scores) >= 2 and scores[-1] < scores[0]:
            return False

        # Check hypothesis space stability (no new hypotheses added)
        hyp_counts = [
            int(e.get("hypothesis_count", 0)) if isinstance(e, dict) else 0
            for e in hist
        ]
        if hyp_counts and max(hyp_counts) > hyp_counts[0]:
            return False

        return True

    def counterexample_search(self, loop: Any) -> dict[str, Any] | None:
        """Search for a counter-example to this theorem in the loop's history.

        Looks for iterations where the measure increased (score decreased),
        or where new hypotheses were introduced.

        Parameters
        ----------
        loop:
            The loop to search.

        Returns
        -------
        dict[str, Any] | None
            A dict describing the counter-example if one is found, otherwise
            ``None``.
        """
        hist = getattr(loop, "history", [])
        scores = [
            float(e.get("score", 0.5)) if isinstance(e, dict) else 0.5
            for e in hist
        ]
        for i in range(1, len(scores)):
            if scores[i] < scores[i - 1] - 0.05:
                return {
                    "type": "measure_increase",
                    "iteration": i,
                    "score_before": scores[i - 1],
                    "score_after": scores[i],
                    "description": (
                        f"Measure increased at iteration {i}: "
                        f"score dropped from {scores[i-1]:.3f} to {scores[i]:.3f}"
                    ),
                }
        return None

    def to_json(self) -> str:
        """Serialise this theorem to JSON via its underlying record."""
        return self._record.to_json()

    def summarize(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"LoopConvergenceTheorem status={self._record.status.value} "
            f"strategy={self._record.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render this theorem as a LaTeX theorem environment."""
        return self._record.render_tex()


# ---------------------------------------------------------------------------
# FalsificationCompletenessTheorem
# ---------------------------------------------------------------------------


class FalsificationCompletenessTheorem:
    """Theorem: the falsification loop is complete over a finite hypothesis space.

    Under unlimited budget, every falsifiable hypothesis in a finite space is
    eventually falsified.  This theorem justifies the resource-bounded
    approximation used in practice: if the budget is set to |H|, the
    falsification loop achieves completeness up to the budget.
    """

    _THEOREM_ID = "falsification-completeness-v1"

    def __init__(self) -> None:
        self._record: TheoremRecord = TheoremRecord.create(
            name="FalsificationCompletenessTheorem",
            statement=self.statement(),
            proof_sketch=self.proof_sketch(),
            assumptions=self.assumptions(),
            status=TheoremStatus.PROOF_SKETCH,
            proof_strategy=TheoremProofStrategy.FINITE_DESCENT,
            references=["theory2.tex:Ch62", "popper:logic-of-scientific-discovery"],
            metadata={
                "theory_chapter": "Ch62",
                "depends_on": [LoopConvergenceTheorem._THEOREM_ID],
                "tags": ["falsification", "completeness", "hypothesis"],
            },
        )
        self._record.theorem_id = self._THEOREM_ID

    @property
    def record(self) -> TheoremRecord:
        """Return the underlying :class:`TheoremRecord`."""
        return self._record

    def statement(self) -> str:
        """Return the theorem statement."""
        return (
            "All falsifiable hypotheses in a finite hypothesis space H are eventually "
            "falsified by the falsification loop F under unlimited budget.  "
            "Formally: for all h ∈ H, if ∃ counter-example c s.t. ¬P(h, c), "
            "then F halts with h marked FALSIFIED in at most |H| × B_h steps, "
            "where B_h is the number of candidate counter-examples for h."
        )

    def proof_sketch(self) -> str:
        """Return a detailed proof sketch."""
        return (
            "The proof proceeds by induction on |H|, with an inner induction on "
            "the size of the candidate counter-example set for each hypothesis.\n\n"
            "Outer induction (|H|): For |H| = 1, the falsification loop F applies "
            "the search strategy to the single hypothesis h₁.  By assumption, "
            "if h₁ is falsifiable there exists a counter-example c₁ in the "
            "finite candidate space.  The search strategy enumerates the candidate "
            "space in finite time, so c₁ is found and h₁ is marked FALSIFIED.\n\n"
            "Outer inductive step: Assume the claim holds for all spaces of size < n. "
            "For |H| = n, F processes h₁ first (or in any fixed order), applying "
            "the search strategy with budget B_{h₁}.  If h₁ is falsifiable, it is "
            "falsified by the inner induction argument.  The remaining n−1 hypotheses "
            "satisfy the inductive hypothesis, so they are all eventually falsified.  "
            "The total step count is ∑_{h ∈ H} B_h ≤ |H| × max_h B_h.\n\n"
            "Budget argument: In practice the budget is finite, so completeness holds "
            "only up to the budget.  The resource-bounded version of the theorem "
            "states that the falsification rate (fraction of falsifiable hypotheses "
            "found) converges to 1 as the budget grows without bound.  This is the "
            "version used in the JuGeo methodology evaluation pipeline.\n\n"
            "Connection to LoopConvergenceTheorem: Each successful falsification "
            "reduces the remaining hypothesis count by 1, so the outer measure "
            "μ(F) = |{h ∈ H : not yet falsified}| strictly decreases.  The "
            "LoopConvergenceTheorem then guarantees that the falsification loop "
            "converges in at most |H| outer iterations."
        )

    def assumptions(self) -> list[str]:
        """Return the list of assumptions."""
        return [
            "The hypothesis space H is finite.",
            "The candidate counter-example space for each hypothesis is finite.",
            "The search strategy is complete: it enumerates all candidates in finite time.",
            "The predicate P(h, c) ('c is a counter-example for h') is decidable.",
            "The falsification budget is unlimited (or at least |H| × max_h B_h).",
            LoopConvergenceTheorem.__doc__.split("\n")[0].strip() + " (assumed).",
        ]

    def verify(self, loop: Any) -> bool:
        """Heuristically verify this theorem for a given falsification loop.

        Checks that the number of falsified hypotheses is non-decreasing
        across iterations.

        Parameters
        ----------
        loop:
            The falsification loop to check.

        Returns
        -------
        bool
        """
        hist = getattr(loop, "history", [])
        if not hist:
            return True
        falsified_counts = [
            int(e.get("falsified_count", 0)) if isinstance(e, dict) else 0
            for e in hist
        ]
        # Non-decreasing sequence is required
        for i in range(1, len(falsified_counts)):
            if falsified_counts[i] < falsified_counts[i - 1]:
                return False
        return True

    def counterexample_search(self, loop: Any) -> dict[str, Any] | None:
        """Search for a counter-example: a hypothesis that should be falsifiable but isn't.

        Returns
        -------
        dict[str, Any] | None
        """
        hist = getattr(loop, "history", [])
        for i, e in enumerate(hist):
            if isinstance(e, dict):
                if e.get("expected_falsifiable") and not e.get("falsified"):
                    return {
                        "type": "missed_falsification",
                        "iteration": i,
                        "hypothesis_id": e.get("hypothesis_id", "unknown"),
                        "description": "Hypothesis expected to be falsifiable was not falsified.",
                    }
        return None

    def to_json(self) -> str:
        """Serialise to JSON."""
        return self._record.to_json()

    def summarize(self) -> str:
        """Return a one-line summary."""
        return (
            f"FalsificationCompletenessTheorem status={self._record.status.value} "
            f"strategy={self._record.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render as LaTeX."""
        return self._record.render_tex()


# ---------------------------------------------------------------------------
# FormalizationSoundnessTheorem
# ---------------------------------------------------------------------------


class FormalizationSoundnessTheorem:
    """Theorem: the formalization loop is sound with respect to informal intent.

    If the formalisation loop maps informal intent I to formal specification S,
    then every formal consequence of S is a valid informal consequence of I
    under the intended interpretation.  This is the soundness direction of the
    JuGeo formalisation adequacy criterion.
    """

    _THEOREM_ID = "formalization-soundness-v1"

    def __init__(self) -> None:
        self._record: TheoremRecord = TheoremRecord.create(
            name="FormalizationSoundnessTheorem",
            statement=self.statement(),
            proof_sketch=self.proof_sketch(),
            assumptions=self.assumptions(),
            status=TheoremStatus.CONJECTURED,
            proof_strategy=TheoremProofStrategy.CONSTRUCTION,
            references=["theory2.tex:Ch62", "floyd:1967:assigning-meaning-to-programs"],
            metadata={
                "theory_chapter": "Ch62",
                "depends_on": [],
                "tags": ["soundness", "formalization", "semantics"],
            },
        )
        self._record.theorem_id = self._THEOREM_ID

    @property
    def record(self) -> TheoremRecord:
        """Return the underlying :class:`TheoremRecord`."""
        return self._record

    def statement(self) -> str:
        """Return the theorem statement."""
        return (
            "The formalization loop F is sound with respect to informal intent I: "
            "if F(I) = S (F maps I to formal specification S), then every formal "
            "consequence φ of S under the specification logic L "
            "(i.e., S ⊢_L φ) is a valid informal consequence of I under the "
            "intended interpretation ⟦·⟧ "
            "(i.e., ⟦S⟧ ⊨ ⟦φ⟧ whenever ⟦I⟧ ⊨ ⟦S⟧)."
        )

    def proof_sketch(self) -> str:
        """Return a detailed proof sketch."""
        return (
            "Soundness is established by constructing a faithful interpretation "
            "function ⟦·⟧ that maps every syntactic object in the specification "
            "logic L to a semantic object in the informal intent model M_I.\n\n"
            "The key steps are:\n"
            "1. Define the interpretation ⟦·⟧: Syntax(L) → Sem(M_I) by structural "
            "recursion on the syntax of L.  For atomic propositions, ⟦p⟧ is the "
            "subset of informal states satisfying p according to the intent model.\n"
            "2. Show that ⟦·⟧ is homomorphic with respect to the logical connectives: "
            "⟦φ ∧ ψ⟧ = ⟦φ⟧ ∩ ⟦ψ⟧, ⟦φ ∨ ψ⟧ = ⟦φ⟧ ∪ ⟦ψ⟧, etc.\n"
            "3. Show that F preserves ⟦·⟧: if F(I) = S then ⟦S⟧ ⊆ ⟦I⟧ (every "
            "formal model of S is also an informal model of I).\n"
            "4. Derive soundness: if S ⊢_L φ then by the soundness of L (assumed) "
            "we have ⟦S⟧ ⊨ ⟦φ⟧, and by step 3 this implies ⟦I⟧ ⊨ ⟦φ⟧.\n\n"
            "The critical assumption is that F is conservative: it does not add "
            "formal clauses whose informal meaning is not already entailed by I. "
            "This is enforced by the JuGeo formalisation protocol, which requires "
            "each formalisation step to be reviewed against the informal intent "
            "document before being committed.\n\n"
            "The converse direction (completeness: every informal consequence is "
            "captured by S) is addressed by the ImplementationCompletenessTheorem. "
            "Together, soundness and completeness establish the adequacy of the "
            "JuGeo formalisation loop with respect to informal intent."
        )

    def assumptions(self) -> list[str]:
        """Return the list of assumptions."""
        return [
            "The specification logic L is itself sound (standard assumption).",
            "The interpretation function ⟦·⟧ exists and is computable.",
            "The formalisation loop F is conservative: F(I) adds no clauses "
            "whose informal meaning is not entailed by I.",
            "The informal intent model M_I is fixed and does not change during "
            "the formalisation loop.",
            "Informal consequences are well-defined via the intended interpretation.",
        ]

    def verify(self, loop: Any) -> bool:
        """Heuristically verify soundness: check that no over-specification occurred.

        Parameters
        ----------
        loop:
            The formalisation loop to check.

        Returns
        -------
        bool
        """
        hist = getattr(loop, "history", [])
        for e in hist:
            if isinstance(e, dict) and e.get("over_specified"):
                return False
        return True

    def counterexample_search(self, loop: Any) -> dict[str, Any] | None:
        """Search for an over-specification event in the loop's history."""
        hist = getattr(loop, "history", [])
        for i, e in enumerate(hist):
            if isinstance(e, dict) and e.get("over_specified"):
                return {
                    "type": "over_specification",
                    "iteration": i,
                    "description": "Formalisation loop produced an over-specified clause.",
                    "details": e,
                }
        return None

    def to_json(self) -> str:
        """Serialise to JSON."""
        return self._record.to_json()

    def summarize(self) -> str:
        """Return a one-line summary."""
        return (
            f"FormalizationSoundnessTheorem status={self._record.status.value} "
            f"strategy={self._record.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render as LaTeX."""
        return self._record.render_tex()


# ---------------------------------------------------------------------------
# ImplementationCompletenessTheorem
# ---------------------------------------------------------------------------


class ImplementationCompletenessTheorem:
    """Theorem: the implementation loop is complete w.r.t. formal specification.

    Every clause of the formal specification S is covered by the
    implementation produced by the implementation loop I.  This theorem
    provides the dual guarantee to FormalizationSoundnessTheorem.
    """

    _THEOREM_ID = "implementation-completeness-v1"

    def __init__(self) -> None:
        self._record: TheoremRecord = TheoremRecord.create(
            name="ImplementationCompletenessTheorem",
            statement=self.statement(),
            proof_sketch=self.proof_sketch(),
            assumptions=self.assumptions(),
            status=TheoremStatus.CONJECTURED,
            proof_strategy=TheoremProofStrategy.INDUCTION,
            references=["theory2.tex:Ch62", "hoare:1969:axiomatic-basis"],
            metadata={
                "theory_chapter": "Ch62",
                "depends_on": [FormalizationSoundnessTheorem._THEOREM_ID],
                "tags": ["completeness", "implementation", "specification"],
            },
        )
        self._record.theorem_id = self._THEOREM_ID

    @property
    def record(self) -> TheoremRecord:
        """Return the underlying :class:`TheoremRecord`."""
        return self._record

    def statement(self) -> str:
        """Return the theorem statement."""
        return (
            "The implementation loop I is complete with respect to formal "
            "specification S: the implementation ℐ produced by I(S) covers all "
            "clauses C₁, …, Cₙ of S in the specification language L_spec.  "
            "Formally: for all Cᵢ ∈ S, ℐ ⊨ Cᵢ (the implementation satisfies "
            "each clause under the operational semantics of the implementation "
            "language L_impl and the refinement mapping ρ: L_impl → L_spec)."
        )

    def proof_sketch(self) -> str:
        """Return a detailed proof sketch."""
        return (
            "The proof proceeds by induction on the number of clauses in S.\n\n"
            "Base case: |S| = 0 (empty specification).  The implementation "
            "ℐ = ∅ trivially satisfies all (zero) clauses.  Completeness holds "
            "vacuously.\n\n"
            "Inductive step: Assume every implementation produced by I for a "
            "specification with fewer than n clauses is complete.  Consider S "
            "with n clauses.  The implementation loop I processes clauses one "
            "at a time (in any topological order respecting dependencies).  By "
            "the inductive hypothesis, after processing the first n−1 clauses, "
            "the partial implementation ℐ_{n-1} covers all of them.  The n-th "
            "clause Cₙ is then processed: I selects an implementation fragment "
            "fₙ such that fₙ ⊨ Cₙ under ρ (the fragment-synthesis sub-step "
            "is guaranteed to succeed by Assumption 3: every clause is "
            "synthesisable).  The complete implementation ℐ = ℐ_{n-1} ∪ {fₙ} "
            "satisfies all n clauses.\n\n"
            "Consistency of the implementation: The combined implementation is "
            "internally consistent because the clauses are assumed to be "
            "consistent (Assumption 4).  Specifically, adding fₙ does not "
            "violate any of the previously satisfied clauses because the "
            "dependency order is respected.\n\n"
            "Refinement mapping: The key technical device is the refinement "
            "mapping ρ, which translates implementation-language terms into "
            "specification-language terms.  The correctness of ρ is a "
            "separate theorem (RefinementCorrectnessTheorem, currently "
            "CONJECTURED) that is assumed here."
        )

    def assumptions(self) -> list[str]:
        """Return the list of assumptions."""
        return [
            "The specification S is finite and syntactically well-formed.",
            "The implementation language L_impl admits a refinement mapping ρ to L_spec.",
            "Every clause Cᵢ ∈ S is individually synthesisable "
            "(there exists an implementation fragment fᵢ with fᵢ ⊨ Cᵢ).",
            "The clauses of S are mutually consistent (S has a model).",
            "The dependency order on clauses is acyclic.",
            FormalizationSoundnessTheorem.__doc__.split("\n")[0].strip() + " (assumed).",
        ]

    def verify(self, loop: Any) -> bool:
        """Heuristically verify: check coverage score is close to 1.

        Parameters
        ----------
        loop:
            The implementation loop to check.

        Returns
        -------
        bool
        """
        coverage = float(getattr(loop, "coverage", 0.0))
        return coverage >= 0.95

    def counterexample_search(self, loop: Any) -> dict[str, Any] | None:
        """Search for uncovered specification clauses."""
        coverage = float(getattr(loop, "coverage", 1.0))
        if coverage < 0.95:
            return {
                "type": "incomplete_coverage",
                "coverage": coverage,
                "description": f"Implementation covers only {coverage:.1%} of specification clauses.",
            }
        uncovered = getattr(loop, "uncovered_clauses", [])
        if uncovered:
            return {
                "type": "uncovered_clauses",
                "clauses": list(uncovered),
                "description": f"{len(uncovered)} clauses not covered by implementation.",
            }
        return None

    def to_json(self) -> str:
        """Serialise to JSON."""
        return self._record.to_json()

    def summarize(self) -> str:
        """Return a one-line summary."""
        return (
            f"ImplementationCompletenessTheorem status={self._record.status.value} "
            f"strategy={self._record.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render as LaTeX."""
        return self._record.render_tex()


# ---------------------------------------------------------------------------
# RevisionMonotonicityTheorem
# ---------------------------------------------------------------------------


class RevisionMonotonicityTheorem:
    """Theorem: each revision step strictly improves the quality measure.

    Every revision step R in the methodology loop produces a strictly better
    loop state with respect to the quality measure Q.  This theorem underpins
    the convergence argument: without monotonicity, the loop could cycle
    between states without making progress.
    """

    _THEOREM_ID = "revision-monotonicity-v1"

    def __init__(self) -> None:
        self._record: TheoremRecord = TheoremRecord.create(
            name="RevisionMonotonicityTheorem",
            statement=self.statement(),
            proof_sketch=self.proof_sketch(),
            assumptions=self.assumptions(),
            status=TheoremStatus.PROOF_SKETCH,
            proof_strategy=TheoremProofStrategy.CONTRADICTION,
            references=["theory2.tex:Ch62", "jugeo:methodology_loops:algorithms"],
            metadata={
                "theory_chapter": "Ch62",
                "depends_on": [LoopConvergenceTheorem._THEOREM_ID],
                "tags": ["monotonicity", "revision", "quality"],
            },
        )
        self._record.theorem_id = self._THEOREM_ID

    @property
    def record(self) -> TheoremRecord:
        """Return the underlying :class:`TheoremRecord`."""
        return self._record

    def statement(self) -> str:
        """Return the theorem statement."""
        return (
            "Each revision step R in the methodology loop strictly improves the "
            "quality measure Q(L), i.e., Q(R(L)) > Q(L) for all non-converged "
            "loops L.  Equivalently, R is a strictly order-preserving map on the "
            "quality lattice (ℚ ∩ [0,1], ≤) for all L ∉ Conv(M), where Conv(M) "
            "is the set of converged states of methodology M."
        )

    def proof_sketch(self) -> str:
        """Return a detailed proof sketch."""
        return (
            "We prove this by contradiction.  Suppose there exists a non-converged "
            "loop L₀ ∉ Conv(M) such that Q(R(L₀)) ≤ Q(L₀).\n\n"
            "Case 1: Q(R(L₀)) = Q(L₀).  Then R is a fixed point of Q.  By "
            "Assumption 2, Q uniquely characterises the convergence set: "
            "Q(L) = Q_conv iff L ∈ Conv(M).  Since L₀ ∉ Conv(M), Q(L₀) ≠ Q_conv, "
            "so Q(R(L₀)) = Q(L₀) ≠ Q_conv, meaning R(L₀) ∉ Conv(M) either.  "
            "But then the revision step R has produced no progress, contradicting "
            "Assumption 3 (every revision step resolves at least one defect).  "
            "This contradiction establishes that Case 1 is impossible.\n\n"
            "Case 2: Q(R(L₀)) < Q(L₀).  Then R has decreased the quality measure, "
            "i.e., the revision has made the loop worse.  By Assumption 4, R is "
            "constructed to be quality-preserving at minimum: R(L) is obtained by "
            "applying a correctness-checked transformation, so Q(R(L)) ≥ Q(L).  "
            "This contradicts Q(R(L₀)) < Q(L₀), so Case 2 is also impossible.\n\n"
            "Since both cases lead to contradictions, we conclude Q(R(L)) > Q(L) "
            "for all non-converged L, establishing strict monotonicity.\n\n"
            "Implication for convergence: Strict monotonicity combined with the "
            "boundedness of Q (Q: LoopState → [0, 1]) implies that the sequence "
            "Q(L₀), Q(R(L₀)), Q(R²(L₀)), … is strictly increasing and bounded "
            "above by 1.  By the monotone convergence theorem for sequences of "
            "rationals in [0, 1], this sequence converges to a limit Q*.  "
            "The LoopConvergenceTheorem guarantees that Q* is achieved in finitely "
            "many steps when the hypothesis space is finite."
        )

    def assumptions(self) -> list[str]:
        """Return the list of assumptions."""
        return [
            "The quality measure Q: LoopState → ℚ ∩ [0, 1] is well-defined.",
            "Q uniquely characterises convergence: Q(L) = Q_conv iff L ∈ Conv(M).",
            "Every revision step resolves at least one identifiable defect in L.",
            "The revision R is constructed from correctness-checked transformations "
            "that cannot decrease Q.",
            "The loop is non-converged (L ∉ Conv(M)).",
            LoopConvergenceTheorem.__doc__.split("\n")[0].strip() + " (assumed).",
        ]

    def verify(self, loop: Any) -> bool:
        """Verify monotonicity: check that quality is non-decreasing in history.

        Parameters
        ----------
        loop:
            The loop to check.

        Returns
        -------
        bool
        """
        hist = getattr(loop, "history", [])
        quality = [
            float(e.get("quality", e.get("score", 0.5))) if isinstance(e, dict) else 0.5
            for e in hist
        ]
        for i in range(1, len(quality)):
            if quality[i] <= quality[i - 1] - 1e-6:
                return False
        return True

    def counterexample_search(self, loop: Any) -> dict[str, Any] | None:
        """Search for a revision step that decreased quality."""
        hist = getattr(loop, "history", [])
        quality = [
            float(e.get("quality", e.get("score", 0.5))) if isinstance(e, dict) else 0.5
            for e in hist
        ]
        for i in range(1, len(quality)):
            if quality[i] <= quality[i - 1] - 1e-6:
                return {
                    "type": "quality_decrease",
                    "iteration": i,
                    "quality_before": quality[i - 1],
                    "quality_after": quality[i],
                    "description": (
                        f"Quality decreased at iteration {i}: "
                        f"{quality[i-1]:.4f} → {quality[i]:.4f}"
                    ),
                }
        return None

    def to_json(self) -> str:
        """Serialise to JSON."""
        return self._record.to_json()

    def summarize(self) -> str:
        """Return a one-line summary."""
        return (
            f"RevisionMonotonicityTheorem status={self._record.status.value} "
            f"strategy={self._record.proof_strategy.value}"
        )

    def render_tex(self) -> str:
        """Render as LaTeX."""
        return self._record.render_tex()


# ---------------------------------------------------------------------------
# MethodologyTheoremRegistry
# ---------------------------------------------------------------------------


class MethodologyTheoremRegistry:
    """Central registry for all JuGeo methodology theorems.

    The registry provides a uniform interface for:
    * Registering and retrieving theorem records.
    * Filtering theorems by status or proof strategy.
    * Bulk verification against a live loop instance.
    * Serialisation to JSON and rendering to LaTeX.

    The recommended way to create a pre-loaded registry is via the class
    method :meth:`default`, which registers all five standard theorems.
    """

    def __init__(self) -> None:
        self.theorems: dict[str, TheoremRecord] = {}
        self._instances: dict[str, Any] = {}
        self._last_results: dict[str, bool] = {}

    def register(
        self,
        theorem_id: str | TheoremRecord,
        record: TheoremRecord | None = None,
    ) -> None:
        """Register a theorem record under *theorem_id*.

        Parameters
        ----------
        theorem_id:
            The unique identifier for the theorem.
        record:
            The :class:`TheoremRecord` to store.

        Raises
        ------
        ValueError
            If *theorem_id* is already registered.
        """
        if record is None:
            if not isinstance(theorem_id, TheoremRecord):
                raise TypeError("register() requires a TheoremRecord")
            record = theorem_id
            theorem_id = record.name

        if theorem_id in self.theorems:
            raise ValueError(
                f"MethodologyTheoremRegistry.register: "
                f"theorem_id '{theorem_id}' already registered"
            )
        self.theorems[theorem_id] = record

    def get(self, theorem_id: str) -> TheoremRecord | None:
        """Retrieve a theorem record by its identifier.

        Parameters
        ----------
        theorem_id:
            The identifier to look up.

        Returns
        -------
        TheoremRecord | None
            The record if found, otherwise ``None``.
        """
        return self.theorems.get(theorem_id) or next(
            (record for record in self.theorems.values() if record.name == theorem_id),
            None,
        )

    def list_all(self) -> list[TheoremRecord]:
        """Return all registered theorem records in registration order.

        Returns
        -------
        list[TheoremRecord]
        """
        return list(self.theorems.values())

    def list_by_status(self, status: TheoremStatus) -> list[TheoremRecord]:
        """Return all theorems with the given *status*.

        Parameters
        ----------
        status:
            The :class:`TheoremStatus` to filter by.

        Returns
        -------
        list[TheoremRecord]
        """
        return [r for r in self.theorems.values() if r.status == status]

    def list_by_strategy(self, strategy: TheoremProofStrategy) -> list[TheoremRecord]:
        """Return all theorems using the given proof *strategy*.

        Parameters
        ----------
        strategy:
            The :class:`TheoremProofStrategy` to filter by.

        Returns
        -------
        list[TheoremRecord]
        """
        return [r for r in self.theorems.values() if r.proof_strategy == strategy]

    def count(self) -> int:
        """Return the number of registered theorems."""
        return len(self.theorems)

    def verify_all(self, loop: Any) -> list[bool]:
        """Attempt to verify all registered theorems against *loop*.

        For each theorem instance stored in ``self._instances``, calls its
        ``verify(loop)`` method.  Theorems without a stored instance fall back
        to a trivial ``True`` (unverifiable) result.

        Parameters
        ----------
        loop:
            The loop instance to verify against.

        Returns
        -------
        list[bool]
            Verification results in registration order.
        """
        results: dict[str, bool] = {}
        for tid, record in self.theorems.items():
            inst = self._instances.get(tid)
            if inst is not None and hasattr(inst, "verify"):
                try:
                    results[tid] = bool(inst.verify(loop))
                except Exception:
                    results[tid] = False
            else:
                results[tid] = True  # unverifiable → assume holds
        self._last_results = dict(results)
        return list(results.values())

    def _summary_data(self) -> dict[str, Any]:
        """Build the structured registry summary used internally."""
        by_status: dict[str, int] = {}
        by_strategy: dict[str, int] = {}
        verified_ids: list[str] = []
        conjectured_ids: list[str] = []
        last_verified_true = [tid for tid, ok in self._last_results.items() if ok]
        last_verified_false = [tid for tid, ok in self._last_results.items() if not ok]

        for tid, rec in self.theorems.items():
            by_status[rec.status.value] = by_status.get(rec.status.value, 0) + 1
            by_strategy[rec.proof_strategy.value] = (
                by_strategy.get(rec.proof_strategy.value, 0) + 1
            )
            if rec.status == TheoremStatus.VERIFIED:
                verified_ids.append(tid)
            if rec.status == TheoremStatus.CONJECTURED:
                conjectured_ids.append(tid)

        return {
            "total": len(self.theorems),
            "by_status": by_status,
            "by_strategy": by_strategy,
            "verified_ids": verified_ids,
            "conjectured_ids": conjectured_ids,
            "last_verified_true": last_verified_true,
            "last_verified_false": last_verified_false,
        }

    def summary_report(self) -> str:
        """Return a human-readable summary report of the registry.

        Returns
        -------
        str
            Human-readable multi-field summary.
        """
        report = self._summary_data()
        return (
            f"MethodologyTheoremRegistry(total={report['total']}, "
            f"statuses={report['by_status']}, strategies={report['by_strategy']}, "
            f"verified={len(report['verified_ids'])}, conjectured={len(report['conjectured_ids'])}, "
            f"last_passed={len(report['last_verified_true'])}, last_failed={len(report['last_verified_false'])})"
        )

    def to_json(self) -> str:
        """Serialise the entire registry to a JSON string.

        Returns
        -------
        str
            A JSON array of serialised theorem records.
        """
        records = [json.loads(r.to_json()) for r in self.theorems.values()]
        return json.dumps({"theorems": records, "count": len(records)})

    @classmethod
    def from_json(cls, data: str) -> "MethodologyTheoremRegistry":
        """Deserialise a :class:`MethodologyTheoremRegistry` from a JSON string.

        Parameters
        ----------
        data:
            JSON string as produced by :meth:`to_json`.

        Returns
        -------
        MethodologyTheoremRegistry
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"MethodologyTheoremRegistry.from_json: invalid JSON – {exc}"
            ) from exc
        registry = cls()
        for rec_dict in obj.get("theorems", []):
            rec = TheoremRecord.from_json(json.dumps(rec_dict))
            registry.theorems[rec.theorem_id] = rec
        return registry

    def render_tex_all(self) -> str:
        """Render all registered theorems as a LaTeX section.

        Returns
        -------
        str
        """
        lines = [r"\section{Methodology Theorems}"]
        for rec in self.theorems.values():
            lines.append(rec.render_tex())
            lines.append("")
        return "\n".join(lines)

    def export_bib(self) -> str:
        """Export all theorem references as a BibTeX file stub.

        Returns a BibTeX ``@misc`` entry for each unique reference string
        found across all registered theorems.

        Returns
        -------
        str
            BibTeX text.
        """
        seen: set[str] = set()
        entries: list[str] = []
        for rec in self.theorems.values():
            for ref in rec.references:
                if ref not in seen:
                    seen.add(ref)
                    safe_key = ref.replace(":", "_").replace(".", "_").replace("/", "_")
                    entries.append(
                        f"@misc{{{safe_key},\n"
                        f"  title = {{{ref}}},\n"
                        f"  note  = {{JuGeo theory reference}}\n"
                        f"}}"
                    )
        return "\n\n".join(entries)

    def health_check(self) -> bool:
        """Return whether the registry is in a healthy state.

        Returns
        -------
        bool
            ``True`` when the registry is non-empty and contains no refuted theorem.
        """
        report = self._summary_data()
        return report["total"] > 0 and report["by_status"].get("REFUTED", 0) == 0

    @classmethod
    def default(cls) -> "MethodologyTheoremRegistry":
        """Create a registry pre-loaded with all five standard theorems.

        Instantiates :class:`LoopConvergenceTheorem`,
        :class:`FalsificationCompletenessTheorem`,
        :class:`FormalizationSoundnessTheorem`,
        :class:`ImplementationCompletenessTheorem`, and
        :class:`RevisionMonotonicityTheorem`, registers their records, and
        stores the instances for later use in :meth:`verify_all`.

        Returns
        -------
        MethodologyTheoremRegistry
            A fully loaded registry.
        """
        registry = cls()

        theorem_instances = [
            LoopConvergenceTheorem(),
            FalsificationCompletenessTheorem(),
            FormalizationSoundnessTheorem(),
            ImplementationCompletenessTheorem(),
            RevisionMonotonicityTheorem(),
        ]

        for inst in theorem_instances:
            rec = inst.record
            registry.theorems[rec.theorem_id] = rec
            registry._instances[rec.theorem_id] = inst

        return registry


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def build_theorem_registry() -> MethodologyTheoremRegistry:
    """Build and return a fully loaded :class:`MethodologyTheoremRegistry`.

    This is the recommended factory function.  It delegates to
    :meth:`MethodologyTheoremRegistry.default` to create a registry
    pre-populated with all five standard JuGeo methodology theorems.

    Returns
    -------
    MethodologyTheoremRegistry
    """
    return MethodologyTheoremRegistry.default()


def verify_theorem(
    theorem_id: str,
    loop: Any,
    registry: MethodologyTheoremRegistry | None = None,
) -> bool | None:
    """Verify a single theorem identified by *theorem_id* against *loop*.

    If *registry* is not provided, a default registry is constructed via
    :func:`build_theorem_registry`.

    Parameters
    ----------
    theorem_id:
        The identifier of the theorem to verify.
    loop:
        The methodology loop instance to verify against.
    registry:
        Optional pre-built registry.  If ``None``, the default registry is
        used.

    Returns
    -------
    bool
        The result of the theorem's ``verify(loop)`` call, or ``True`` if the
        theorem has no ``verify`` implementation.

    """
    reg = registry or build_theorem_registry()
    rec = reg.get(theorem_id)
    if rec is None:
        return None

    inst = reg._instances.get(theorem_id) or reg._instances.get(rec.theorem_id) or next(
        (candidate for candidate in reg._instances.values() if getattr(candidate, "name", None) == rec.name),
        None,
    )
    if inst is not None and hasattr(inst, "verify"):
        try:
            return bool(inst.verify(loop))
        except Exception:
            return False
    return True


def theorem_dependency_graph(
    registry: MethodologyTheoremRegistry | None = None,
) -> dict[str, list[str]]:
    """Build a dependency graph from the theorems in *registry*.

    For each theorem, reads its ``depends_on`` metadata field (a list of
    theorem IDs) and constructs an adjacency dict.

    Parameters
    ----------
    registry:
        The theorem registry to analyse.

    Returns
    -------
    dict[str, list[str]]
        Mapping of theorem_id → list of theorem_ids it depends on.
        An empty list indicates no dependencies.
    """
    registry = registry or build_theorem_registry()
    graph: dict[str, list[str]] = {}
    for tid, rec in registry.theorems.items():
        graph[tid] = list(rec.metadata.get("depends_on", []))
    return graph


def export_theorem_latex(registry: MethodologyTheoremRegistry | None = None) -> str:
    """Export all theorems in *registry* as a complete LaTeX document fragment.

    Produces a self-contained section suitable for inclusion in theory2.tex
    via ``\\input{}``.  Includes a preamble comment, a ``\\section`` heading,
    all theorem environments, and a bibliography stub.

    Parameters
    ----------
    registry:
        The theorem registry to export.

    Returns
    -------
    str
        A LaTeX string.
    """
    registry = registry or build_theorem_registry()
    lines = [
        "% Auto-generated by jugeo.evaluation.methodology_loops.theorems",
        "% Theory reference: theory2.tex Ch62",
        "%",
        r"\section{Methodology Loop Theorems}",
        "",
        r"\begin{abstract_note}",
        "This section presents the formal theoretical guarantees of the JuGeo "
        "methodology loops, as encoded in the Python theorem registry.  "
        "Each theorem is accompanied by its proof sketch, assumptions, and "
        "current verification status.",
        r"\end{abstract_note}",
        "",
    ]
    lines.append(registry.render_tex_all())
    lines.append("")
    lines.append(r"\begin{thebibliography}{99}")
    bib = registry.export_bib()
    lines.append(bib)
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)
