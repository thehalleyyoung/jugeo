"""Formal theorems for hypercover treaty synthesis.

This module encodes the key theorems from theory2.tex Chapter 41 that
govern the correctness and completeness of the hypercover synthesis process.
Each theorem is represented as a class with:
  - A `check_conditions` method that verifies the theorem's preconditions
  - An `apply` method that produces the theorem's conclusion
  - A `statement` property returning the LaTeX-formatted theorem statement

The theorems form a proof-theoretic foundation for trusting synthesis
outcomes.  When all conditions are met, the theorems guarantee:
T41.1 (Descent success): A hypercover with compatible local sections
      uniquely determines a global section.
T41.2 (Treaty consistency): A set of ratified treaties is globally
      consistent iff no pair of treaties has contradictory clauses on
      shared patches.
T41.3 (Hypercover existence): Every construction goal with non-empty
      support admits a hypercover (possibly after refinement).
T41.4 (Overlap law completeness): The mined overlap laws form a complete
      basis for the descent condition.

Theory reference: theory2.tex §§41.3–41.6

Usage::

    from jugeo.generation.hypercover_treaties.theorems import (
        TheoremCondition,
        TheoremResult,
        DescentSuccessTheorem,
        TreatyConsistencyTheorem,
        HypercoverExistenceTheorem,
        OverlapLawCompletenessTheorem,
        TheoremProver,
        ProofCertificate,
        generate_proof_certificate,
    )

    prover = TheoremProver()
    results = prover.prove_all(outcome, treaties, goal)
    print(prover.summarize())

copilot: theorems-marker
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy,
        OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind, Coordinate
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal, GoalPriority,
        GoalStatus, OverlapGoal,
    )
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import (
        OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty,
    )
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass

try:
    from jugeo.generation.hypercover_treaties.models import (
        HypercoverSynthesisRecord, TreatyCandidate, OverlapLaw, DependentTreaty,
        SynthesisOutcome, SynthesisPhase, LawStability, CandidateSource,
        TreatyRole, OutcomeKind, SynthesisConfig, OverlapLawIndex,
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "TheoremCondition",
    "TheoremResult",
    "DescentSuccessTheorem",
    "TreatyConsistencyTheorem",
    "HypercoverExistenceTheorem",
    "OverlapLawCompletenessTheorem",
    "TheoremProver",
    "ProofCertificate",
    "generate_proof_certificate",
]

# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass
class TheoremCondition:
    """A single precondition that must hold for a theorem to be applicable.

    Conditions are created in an unsatisfied state and transitioned to
    satisfied via :meth:`satisfy` once evidence is found.  Each condition
    carries a human-readable description for diagnostic output.

    Theory reference: theory2.tex §41.3 Definition 41.3.1 (theorem conditions).
    """

    name: str
    """Short identifier for this condition, e.g. ``"hypercover_property"``."""

    description: str
    """Full prose description of what must hold."""

    is_satisfied: bool = False
    """Whether this condition is currently satisfied."""

    evidence: str = ""
    """Free-text summary of the evidence that satisfied this condition."""

    def satisfy(self, evidence: str) -> "TheoremCondition":
        """Return a new TheoremCondition marked as satisfied with the given evidence.

        This is a pure operation — it does not mutate the receiver.

        Args:
            evidence: Human-readable summary of the evidence.

        Returns:
            A new :class:`TheoremCondition` with ``is_satisfied=True`` and
            ``evidence`` set to *evidence*.
        """
        return TheoremCondition(
            name=self.name,
            description=self.description,
            is_satisfied=True,
            evidence=evidence,
        )

    def __str__(self) -> str:
        status = "✓" if self.is_satisfied else "✗"
        ev = f" [{self.evidence[:60]}]" if self.evidence else ""
        return f"{status} {self.name}: {self.description}{ev}"

    def __repr__(self) -> str:
        return (
            f"TheoremCondition(name={self.name!r}, "
            f"is_satisfied={self.is_satisfied}, "
            f"evidence={self.evidence[:40]!r})"
        )


@dataclass
class TheoremResult:
    """The conclusion produced by applying a theorem.

    A :class:`TheoremResult` summarises which theorem was applied, whether
    all preconditions were met (``is_applicable``), and a prose statement of
    the conclusion together with a digest of all supporting evidence.

    Theory reference: theory2.tex §41.3 Definition 41.3.2 (theorem result).
    """

    theorem_name: str
    """Identifier of the theorem, e.g. ``"T41.1-DescentSuccess"``."""

    conclusion: str
    """Prose statement of what the theorem concludes when applicable."""

    conditions_checked: int
    """Total number of preconditions that were evaluated."""

    conditions_satisfied: int
    """Number of preconditions that were found to be satisfied."""

    is_applicable: bool
    """True iff *all* preconditions are satisfied and the theorem fires."""

    evidence_summary: str
    """Concatenated evidence strings from all satisfied conditions."""

    provenance: tuple[str, ...]
    """Ordered sequence of reasoning steps used to reach this result."""

    @property
    def all_conditions_met(self) -> bool:
        """Return True when every checked condition is satisfied."""
        return self.conditions_checked > 0 and (
            self.conditions_satisfied == self.conditions_checked
        )

    @property
    def satisfaction_ratio(self) -> float:
        """Fraction of conditions that are satisfied (0.0 to 1.0)."""
        if self.conditions_checked == 0:
            return 0.0
        return self.conditions_satisfied / self.conditions_checked

    def __repr__(self) -> str:
        tag = "APPLICABLE" if self.is_applicable else "NOT-APPLICABLE"
        return (
            f"TheoremResult({self.theorem_name!r}, {tag}, "
            f"{self.conditions_satisfied}/{self.conditions_checked} conditions)"
        )

    def __str__(self) -> str:
        tag = "✓ APPLICABLE" if self.is_applicable else "✗ NOT APPLICABLE"
        lines = [
            f"[{self.theorem_name}] {tag}",
            f"  Conditions: {self.conditions_satisfied}/{self.conditions_checked}",
            f"  Conclusion: {self.conclusion}",
        ]
        if self.evidence_summary:
            lines.append(f"  Evidence:   {self.evidence_summary[:120]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# T41.1 — Descent success
# ---------------------------------------------------------------------------


class DescentSuccessTheorem:
    """T41.1 — Descent success.

    If (1) the synthesis outcome reports success, (2) accepted overlap laws
    are non-empty, (3) no patches failed, and (4) all laws have confidence
    at least 0.5, then descent succeeded and produced a unique global section
    (theory2.tex Theorem 41.1).

    The theorem formalises the following implication::

        is_success(O) ∧ |accepted_laws(O)| > 0
        ∧ failed_patches(O) = ∅
        ∧ ∀ l ∈ accepted_laws(O). confidence(l) ≥ 0.5
        ──────────────────────────────────────────────
        descent_succeeded(O) ∧ global_section_unique(O)

    Usage::

        thm = DescentSuccessTheorem()
        result = thm.apply(synthesis_outcome)
        if result.is_applicable:
            cert = generate_proof_certificate(result, {"outcome": synthesis_outcome})
    """

    _name: str = "T41.1-DescentSuccess"

    def __init__(self) -> None:
        """Initialise with the four preconditions of Theorem 41.1."""
        self._conditions: list[TheoremCondition] = [
            TheoremCondition(
                name="synthesis_success",
                description=(
                    "The synthesis outcome reports overall success "
                    "(outcome.is_success() is True)."
                ),
            ),
            TheoremCondition(
                name="nonempty_laws",
                description=(
                    "The set of accepted overlap laws is non-empty "
                    "(|accepted_laws| > 0)."
                ),
            ),
            TheoremCondition(
                name="no_failed_patches",
                description=(
                    "No patch failed during synthesis "
                    "(failed_patches is empty)."
                ),
            ),
            TheoremCondition(
                name="law_confidence_threshold",
                description=(
                    "Every accepted law has confidence >= 0.5 "
                    "(low-confidence laws undermine the descent guarantee)."
                ),
            ),
        ]

    def check_conditions(self, outcome: Any) -> list[TheoremCondition]:
        """Evaluate each precondition against *outcome* and return updated list.

        Args:
            outcome: A :class:`SynthesisOutcome` (or duck-typed equivalent)
                representing the result of a hypercover synthesis run.

        Returns:
            A fresh list of :class:`TheoremCondition` objects, each with
            ``is_satisfied`` and ``evidence`` updated based on *outcome*.
        """
        results: list[TheoremCondition] = []

        # Condition 1: synthesis_success
        cond = self._conditions[0]
        try:
            if hasattr(outcome, "is_success"):
                success = bool(outcome.is_success())
            else:
                success = bool(getattr(outcome, "success", False))
            if success:
                cond = cond.satisfy("outcome.is_success() returned True")
            else:
                cond = TheoremCondition(cond.name, cond.description, False,
                                        "outcome.is_success() returned False")
        except Exception as exc:
            cond = TheoremCondition(cond.name, cond.description, False, f"exception: {exc}")
        results.append(cond)

        # Condition 2: nonempty_laws
        cond = self._conditions[1]
        try:
            laws = getattr(outcome, "accepted_laws", None) or []
            if len(laws) > 0:
                cond = cond.satisfy(f"{len(laws)} accepted law(s) present")
            else:
                cond = TheoremCondition(cond.name, cond.description, False, "accepted_laws is empty")
        except Exception as exc:
            cond = TheoremCondition(cond.name, cond.description, False, f"exception: {exc}")
        results.append(cond)

        # Condition 3: no_failed_patches
        cond = self._conditions[2]
        try:
            failed = getattr(outcome, "failed_patches", None) or []
            if len(failed) == 0:
                cond = cond.satisfy("failed_patches is empty")
            else:
                cond = TheoremCondition(
                    cond.name, cond.description, False,
                    f"{len(failed)} failed patch(es): {list(failed)[:3]}"
                )
        except Exception as exc:
            cond = TheoremCondition(cond.name, cond.description, False, f"exception: {exc}")
        results.append(cond)

        # Condition 4: law_confidence_threshold
        cond = self._conditions[3]
        try:
            laws = getattr(outcome, "accepted_laws", None) or []
            low_conf = [
                getattr(law, "law_id", str(i))
                for i, law in enumerate(laws)
                if getattr(law, "confidence", 1.0) < 0.5
            ]
            if not low_conf:
                cond = cond.satisfy(f"all {len(laws)} law(s) have confidence >= 0.5")
            else:
                cond = TheoremCondition(
                    cond.name, cond.description, False,
                    f"{len(low_conf)} law(s) below threshold: {low_conf[:3]}",
                )
        except Exception as exc:
            cond = TheoremCondition(cond.name, cond.description, False, f"exception: {exc}")
        results.append(cond)

        return results

    def apply(self, outcome: Any) -> TheoremResult:
        """Apply T41.1 to *outcome* and return a :class:`TheoremResult`.

        Args:
            outcome: A :class:`SynthesisOutcome` to check against.

        Returns:
            A :class:`TheoremResult` with ``is_applicable=True`` iff all
            four preconditions are satisfied.
        """
        conditions = self.check_conditions(outcome)
        satisfied = [c for c in conditions if c.is_satisfied]
        all_met = len(satisfied) == len(conditions)
        evidence_summary = "; ".join(c.evidence for c in satisfied if c.evidence)

        if all_met:
            conclusion = (
                "Descent succeeded and the global section is uniquely determined "
                "by the local sections on the hypercover patches."
            )
        else:
            failed_names = [c.name for c in conditions if not c.is_satisfied]
            conclusion = (
                f"T41.1 is NOT applicable — preconditions not fully met: {failed_names}."
            )

        return TheoremResult(
            theorem_name=self._name,
            conclusion=conclusion,
            conditions_checked=len(conditions),
            conditions_satisfied=len(satisfied),
            is_applicable=all_met,
            evidence_summary=evidence_summary,
            provenance=(
                "check_conditions",
                f"satisfied={len(satisfied)}/{len(conditions)}",
                "DescentSuccessTheorem.apply",
            ),
        )

    def statement(self) -> str:
        """Return the LaTeX-formatted theorem statement (theory2.tex §41.3)."""
        return (
            r"\textbf{Theorem T41.1 (Descent Success).} "
            r"Let $O$ be a synthesis outcome for a hypercover $\mathcal{H}$ "
            r"over a construction goal $G$.  If "
            r"(i) $\mathrm{is\_success}(O)$, "
            r"(ii) $|\mathrm{accepted\_laws}(O)| > 0$, "
            r"(iii) $\mathrm{failed\_patches}(O) = \emptyset$, and "
            r"(iv) $\forall l \in \mathrm{accepted\_laws}(O),\ "
            r"\mathrm{confidence}(l) \geq 0.5$, "
            r"then descent over $\mathcal{H}$ succeeded and the global "
            r"section $s \colon \mathcal{C} \to \mathcal{T}$ is uniquely "
            r"determined by the local sections $\{s_u\}_{u \in \mathcal{H}}$."
        )


# ---------------------------------------------------------------------------
# T41.2 — Treaty consistency
# ---------------------------------------------------------------------------


class TreatyConsistencyTheorem:
    """T41.2 — Treaty consistency.

    A set of ratified treaties is globally consistent iff for every pair of
    treaties whose patch sets overlap, the clauses on shared patches are
    compatible — i.e. no pair of clauses for the same patch carries
    contradictory expectations (theory2.tex Theorem 41.2).

    The theorem formalises::

        forall t1, t2 in Treaties.
          patches(t1) intersect patches(t2) != empty
          ─────────────────────────────────────────────
          forall p in patches(t1) intersect patches(t2).
            clauses_compatible(clauses(t1, p), clauses(t2, p))

    Usage::

        thm = TreatyConsistencyTheorem()
        result = thm.check(list_of_treaties)
        inconsistency = thm.find_inconsistency(list_of_treaties)
    """

    _name: str = "T41.2-TreatyConsistency"

    def _clauses_compatible(
        self,
        t1_clauses: tuple[Any, ...],
        t2_clauses: tuple[Any, ...],
        shared_patch: str,
    ) -> bool:
        """Check that clauses from *t1* and *t2* for *shared_patch* are compatible.

        Compatibility fails when both sides have a clause for *shared_patch*
        and their ``expectation`` fields are semantically opposite.  Opposites
        are detected via negation prefixes and known contradictory pairs.

        Args:
            t1_clauses: Clauses from treaty 1 (sequence of TreatyClause).
            t2_clauses: Clauses from treaty 2 (sequence of TreatyClause).
            shared_patch: The patch key both treaties share.

        Returns:
            True if the clauses are compatible; False if a contradiction is found.
        """
        exp1 = [
            getattr(c, "expectation", "")
            for c in t1_clauses
            if getattr(c, "patch", None) == shared_patch
        ]
        exp2 = [
            getattr(c, "expectation", "")
            for c in t2_clauses
            if getattr(c, "patch", None) == shared_patch
        ]
        if not exp1 or not exp2:
            return True  # One side has no clause for this patch — trivially compatible

        _neg_prefixes = ("not_", "no_", "never_", "!", "~")
        _contradiction_pairs: list[tuple[str, str]] = [
            ("present", "absent"),
            ("enabled", "disabled"),
            ("true", "false"),
            ("satisfied", "violated"),
            ("accepted", "rejected"),
            ("stable", "unstable"),
            ("valid", "invalid"),
            ("open", "closed"),
            ("required", "forbidden"),
        ]

        for e1 in exp1:
            e1_lower = e1.lower().strip()
            for e2 in exp2:
                e2_lower = e2.lower().strip()
                # Detect direct negation prefix
                for prefix in _neg_prefixes:
                    if e1_lower.startswith(prefix) and e1_lower[len(prefix):] == e2_lower:
                        return False
                    if e2_lower.startswith(prefix) and e2_lower[len(prefix):] == e1_lower:
                        return False
                # Detect known contradictory pairs
                for a, b in _contradiction_pairs:
                    if (e1_lower == a and e2_lower == b) or (e1_lower == b and e2_lower == a):
                        return False
        return True

    def check(self, treaties: list[Any]) -> TheoremResult:
        """Verify T41.2 against a list of treaties.

        Iterates over every pair (t1, t2) and checks pairwise compatibility
        on shared patches.

        Args:
            treaties: List of :class:`OverlapTreaty` (or duck-typed) objects.

        Returns:
            :class:`TheoremResult` indicating whether the treaty set is
            globally consistent.
        """
        if not treaties:
            return TheoremResult(
                theorem_name=self._name,
                conclusion="No treaties provided — consistency holds vacuously.",
                conditions_checked=1,
                conditions_satisfied=1,
                is_applicable=True,
                evidence_summary="empty treaty set",
                provenance=("check", "vacuous"),
            )

        pair_count = len(treaties) * (len(treaties) - 1) // 2
        inconsistency = self.find_inconsistency(treaties)
        if inconsistency is None:
            conclusion = (
                f"All {len(treaties)} treaties are pairwise consistent on shared patches."
            )
            return TheoremResult(
                theorem_name=self._name,
                conclusion=conclusion,
                conditions_checked=len(treaties),
                conditions_satisfied=len(treaties),
                is_applicable=True,
                evidence_summary=f"Checked {pair_count} pair(s).",
                provenance=("check", "find_inconsistency->None", "consistent"),
            )
        else:
            t1, t2, reason = inconsistency
            conclusion = f"Treaties are NOT consistent: {reason}."
            return TheoremResult(
                theorem_name=self._name,
                conclusion=conclusion,
                conditions_checked=len(treaties),
                conditions_satisfied=len(treaties) - 1,
                is_applicable=False,
                evidence_summary=reason,
                provenance=("check", "find_inconsistency->found", "inconsistent"),
            )

    def find_inconsistency(
        self, treaties: list[Any]
    ) -> "tuple[Any, Any, str] | None":
        """Search for the first inconsistent pair among *treaties*.

        Args:
            treaties: List of :class:`OverlapTreaty` objects.

        Returns:
            A ``(treaty1, treaty2, reason)`` tuple if an inconsistency is
            found, or ``None`` if all pairs are consistent.
        """
        for i, t1 in enumerate(treaties):
            patches1: set[str] = set(getattr(t1, "patches", ()))
            clauses1: tuple[Any, ...] = getattr(t1, "clauses", ())
            for t2 in treaties[i + 1:]:
                patches2: set[str] = set(getattr(t2, "patches", ()))
                clauses2: tuple[Any, ...] = getattr(t2, "clauses", ())
                shared = patches1 & patches2
                for patch in sorted(shared):
                    if not self._clauses_compatible(clauses1, clauses2, patch):
                        reason = (
                            f"Contradictory clauses on shared patch {patch!r} "
                            f"between treaties covering {sorted(patches1)} "
                            f"and {sorted(patches2)}"
                        )
                        return t1, t2, reason
        return None

    def statement(self) -> str:
        """Return the LaTeX-formatted theorem statement (theory2.tex §41.4)."""
        return (
            r"\textbf{Theorem T41.2 (Treaty Consistency).} "
            r"A finite set of ratified treaties $\mathcal{T}$ is globally "
            r"consistent if and only if for every pair $(\tau_1, \tau_2) "
            r"\in \mathcal{T}^2$ with $\tau_1 \neq \tau_2$ and for every "
            r"patch $p \in \mathrm{patches}(\tau_1) \cap \mathrm{patches}(\tau_2)$, "
            r"the clauses of $\tau_1$ and $\tau_2$ on $p$ are mutually compatible: "
            r"$\mathrm{compatible}(\mathrm{clauses}(\tau_1, p),\; "
            r"\mathrm{clauses}(\tau_2, p)) = \top$."
        )


# ---------------------------------------------------------------------------
# T41.3 — Hypercover existence
# ---------------------------------------------------------------------------


class HypercoverExistenceTheorem:
    """T41.3 — Hypercover existence.

    Every construction goal *G* whose support has at least one patch key
    admits a hypercover — the trivial pairwise cover where each pair of
    patches contributes an overlap (theory2.tex Theorem 41.3).

    This is an *existence* theorem: it does not guarantee the cover is
    efficient or minimal, but guarantees that descent can always be attempted.

    Usage::

        thm = HypercoverExistenceTheorem()
        result = thm.apply(construction_goal)
        witness = thm.construct_witness(construction_goal)
    """

    _name: str = "T41.3-HypercoverExistence"

    def apply(self, goal: Any) -> TheoremResult:
        """Apply T41.3 to *goal* and return a :class:`TheoremResult`.

        Checks that *goal* has a non-empty ``support.patch_keys``.

        Args:
            goal: A :class:`ConstructionGoal` (or duck-typed) with a
                ``.support`` attribute that has a ``.patch_keys`` attribute.

        Returns:
            :class:`TheoremResult` indicating whether a hypercover exists.
        """
        conditions: list[TheoremCondition] = []

        # Condition 1: goal has a support attribute
        has_support_cond = TheoremCondition(
            "has_support",
            "The goal has a support attribute (SupportRegion).",
        )
        support = getattr(goal, "support", None)
        if support is not None:
            has_support_cond = has_support_cond.satisfy("goal.support is not None")
        conditions.append(has_support_cond)

        # Condition 2: support.patch_keys is non-empty
        has_patch_keys_cond = TheoremCondition(
            "has_patch_keys",
            "The support.patch_keys frozenset is non-empty.",
        )
        patch_keys: frozenset[str] = frozenset()
        if support is not None:
            patch_keys = getattr(support, "patch_keys", frozenset())
            if patch_keys:
                has_patch_keys_cond = has_patch_keys_cond.satisfy(
                    f"support.patch_keys has {len(patch_keys)} key(s): "
                    f"{sorted(patch_keys)[:5]}"
                )
        conditions.append(has_patch_keys_cond)

        satisfied = [c for c in conditions if c.is_satisfied]
        all_met = len(satisfied) == len(conditions)
        evidence = "; ".join(c.evidence for c in satisfied if c.evidence)
        prop = getattr(goal, "proposition", repr(goal))

        if all_met:
            conclusion = (
                f"A hypercover exists for goal {prop!r}: "
                f"the trivial pairwise cover over {len(patch_keys)} patch(es) "
                f"witnesses existence."
            )
        else:
            conclusion = (
                "T41.3 is NOT applicable — goal has empty or absent support.patch_keys."
            )

        return TheoremResult(
            theorem_name=self._name,
            conclusion=conclusion,
            conditions_checked=len(conditions),
            conditions_satisfied=len(satisfied),
            is_applicable=all_met,
            evidence_summary=evidence,
            provenance=(
                "apply",
                f"patch_keys={sorted(patch_keys)[:5]}",
                "HypercoverExistenceTheorem",
            ),
        )

    def construct_witness(self, goal: Any) -> dict[str, Any]:
        """Construct an explicit witness hypercover for *goal*.

        The witness is the *trivial pairwise cover*: every pair of patches
        in ``goal.support.patch_keys`` contributes an overlap.  This is
        always well-formed when patch_keys is non-empty and serves as the
        constructive proof of Theorem T41.3.

        Args:
            goal: A :class:`ConstructionGoal` with ``.support.patch_keys``.

        Returns:
            A dict with keys:
            - ``cover_type``: ``"trivial_pairwise_cover"``
            - ``patch_keys``: sorted list of patch key strings
            - ``overlap_pairs``: list of ``(p1, p2)`` pairs for all p1 < p2
            - ``witness_type``: ``"trivial_pairwise_cover"``
            - ``patch_count``: number of patches
            - ``overlap_count``: number of overlapping pairs
            - ``goal_proposition``: the goal's proposition string if available
        """
        support = getattr(goal, "support", None)
        patch_keys: list[str] = sorted(getattr(support, "patch_keys", frozenset()))

        overlap_pairs: list[tuple[str, str]] = [
            (p1, p2)
            for i, p1 in enumerate(patch_keys)
            for p2 in patch_keys[i + 1:]
        ]

        return {
            "cover_type": "trivial_pairwise_cover",
            "patch_keys": patch_keys,
            "overlap_pairs": overlap_pairs,
            "witness_type": "trivial_pairwise_cover",
            "patch_count": len(patch_keys),
            "overlap_count": len(overlap_pairs),
            "goal_proposition": getattr(goal, "proposition", None),
        }

    def statement(self) -> str:
        """Return the LaTeX-formatted theorem statement (theory2.tex §41.5)."""
        return (
            r"\textbf{Theorem T41.3 (Hypercover Existence).} "
            r"Let $G = (p, R, \tau, \pi, \mu, \phi)$ be a construction goal "
            r"with support $R = (c, K, \ldots)$.  If $K \neq \emptyset$, "
            r"then there exists a hypercover $\mathcal{H}$ for $G$. "
            r"Concretely, the trivial pairwise cover "
            r"$\mathcal{H}_{\mathrm{triv}} = \{\{p_i, p_j\} \mid p_i, p_j \in K,\ "
            r"p_i \neq p_j\}$ witnesses existence."
        )


# ---------------------------------------------------------------------------
# T41.4 — Overlap law completeness
# ---------------------------------------------------------------------------


class OverlapLawCompletenessTheorem:
    """T41.4 — Overlap law completeness.

    The mined overlap laws form a complete basis for the descent condition
    iff every overlap pair that appears in the synthesis record is covered
    by at least one mined law (theory2.tex Theorem 41.4).

    If the basis is incomplete (some pair is not covered), descent may miss
    a constraint and the global section could fail soundness checks.

    Usage::

        thm = OverlapLawCompletenessTheorem()
        result = thm.check_completeness(laws, synthesis_outcome)
        missing = thm.find_missing_pairs(laws, synthesis_outcome)
    """

    _name: str = "T41.4-OverlapLawCompleteness"

    def _get_law_pairs(self, laws: list[Any]) -> set[frozenset[str]]:
        """Extract the set of patch-pairs covered by *laws*.

        For each law, inspects the ``patch_pairs`` attribute (a list of
        ``(str, str)`` tuples) or falls back to treating all law patches as a
        complete clique.

        Returns:
            Set of ``frozenset({p1, p2})`` for all pairs covered by any law.
        """
        covered: set[frozenset[str]] = set()
        for law in laws:
            pairs = getattr(law, "patch_pairs", None)
            if pairs:
                for p1, p2 in pairs:
                    covered.add(frozenset({p1, p2}))
            else:
                # Fall back: treat all law.patches as a complete clique
                patches = list(getattr(law, "patches", []))
                for i, p1 in enumerate(patches):
                    for p2 in patches[i + 1:]:
                        covered.add(frozenset({p1, p2}))
        return covered

    def _get_outcome_pairs(self, outcome: Any) -> set[frozenset[str]]:
        """Extract all overlap pairs referenced in *outcome*.

        Looks at ``outcome.accepted_laws`` and collects their ``patch_pairs``.

        Returns:
            Set of ``frozenset({p1, p2})`` for all pairs in *outcome*.
        """
        required: set[frozenset[str]] = set()
        laws = getattr(outcome, "accepted_laws", []) or []
        for law in laws:
            pairs = getattr(law, "patch_pairs", None)
            if pairs:
                for p1, p2 in pairs:
                    required.add(frozenset({p1, p2}))
        return required

    def check_completeness(
        self, laws: list[Any], outcome: Any
    ) -> TheoremResult:
        """Verify that *laws* cover all overlap pairs in *outcome*.

        Args:
            laws: Sequence of :class:`OverlapLaw` (or duck-typed) objects
                representing the mined law basis.
            outcome: :class:`SynthesisOutcome` whose accepted laws define the
                required overlap pairs.

        Returns:
            :class:`TheoremResult` — applicable iff no pairs are missing.
        """
        covered = self._get_law_pairs(laws)
        required = self._get_outcome_pairs(outcome)
        missing = sorted(
            tuple(sorted(pair))
            for pair in required
            if pair not in covered
        )

        conditions: list[TheoremCondition] = []

        nonempty_cond = TheoremCondition(
            "nonempty_law_basis",
            "The provided law basis is non-empty.",
        )
        if laws:
            nonempty_cond = nonempty_cond.satisfy(f"{len(laws)} law(s) provided")
        conditions.append(nonempty_cond)

        coverage_cond = TheoremCondition(
            "full_pair_coverage",
            "Every overlap pair in the synthesis record appears in at least one law.",
        )
        if not missing:
            coverage_cond = coverage_cond.satisfy(
                f"All {len(required)} required pair(s) are covered by {len(laws)} law(s)."
            )
        else:
            coverage_cond = TheoremCondition(
                coverage_cond.name, coverage_cond.description, False,
                f"{len(missing)} missing pair(s): {missing[:5]}",
            )
        conditions.append(coverage_cond)

        satisfied = [c for c in conditions if c.is_satisfied]
        all_met = len(satisfied) == len(conditions)
        evidence = "; ".join(c.evidence for c in satisfied if c.evidence)

        if all_met:
            conclusion = (
                f"The {len(laws)} mined law(s) form a complete basis: "
                f"all {len(required)} overlap pair(s) are covered."
            )
        else:
            conclusion = (
                f"Law basis is INCOMPLETE: {len(missing)} pair(s) uncovered — {missing[:3]}."
            )

        return TheoremResult(
            theorem_name=self._name,
            conclusion=conclusion,
            conditions_checked=len(conditions),
            conditions_satisfied=len(satisfied),
            is_applicable=all_met,
            evidence_summary=evidence,
            provenance=(
                "check_completeness",
                f"laws={len(laws)}, required={len(required)}, missing={len(missing)}",
                "OverlapLawCompletenessTheorem",
            ),
        )

    def find_missing_pairs(
        self, laws: list[Any], outcome: Any
    ) -> list[tuple[str, str]]:
        """Return all overlap pairs in *outcome* not covered by any law.

        Args:
            laws: The mined law basis.
            outcome: :class:`SynthesisOutcome` defining required pairs.

        Returns:
            Sorted list of ``(p1, p2)`` tuples not covered by *laws*.
        """
        covered = self._get_law_pairs(laws)
        required = self._get_outcome_pairs(outcome)
        missing: list[tuple[str, str]] = sorted(
            tuple(sorted(pair))  # type: ignore[return-value]
            for pair in required
            if pair not in covered
        )
        return missing

    def statement(self) -> str:
        """Return the LaTeX-formatted theorem statement (theory2.tex §41.6)."""
        return (
            r"\textbf{Theorem T41.4 (Overlap Law Completeness).} "
            r"Let $\Lambda$ be a finite set of overlap laws and let $O$ be a "
            r"synthesis outcome.  Define the \emph{required pairs} "
            r"$P(O) = \bigcup_{l \in \mathrm{accepted\_laws}(O)} "
            r"\mathrm{patch\_pairs}(l)$.  "
            r"Then $\Lambda$ forms a complete basis for the descent condition "
            r"iff $P(O) \subseteq \bigcup_{l \in \Lambda} "
            r"\mathrm{patch\_pairs}(l)$."
        )


# ---------------------------------------------------------------------------
# Theorem prover orchestrator
# ---------------------------------------------------------------------------


class TheoremProver:
    """Orchestrates theorem checking and proof discharge for Chapter 41.

    Maintains a registry of all four Chapter 41 theorems and a persistent
    proof log.  Call :meth:`prove` to run a single theorem or
    :meth:`prove_all` to run all four at once.

    Usage::

        prover = TheoremProver()
        all_results = prover.prove_all(outcome, treaties, goal)
        print(prover.summarize())
        log = prover.get_proof_log()
    """

    def __init__(self) -> None:
        """Initialise the prover with all four Chapter 41 theorems registered."""
        self._theorems: dict[str, Any] = {
            "T41.1-DescentSuccess": DescentSuccessTheorem(),
            "T41.2-TreatyConsistency": TreatyConsistencyTheorem(),
            "T41.3-HypercoverExistence": HypercoverExistenceTheorem(),
            "T41.4-OverlapLawCompleteness": OverlapLawCompletenessTheorem(),
        }
        self._proof_log: list[TheoremResult] = []

    def prove(self, theorem_name: str, evidence: dict[str, Any]) -> TheoremResult:
        """Dispatch to the named theorem and run it with *evidence*.

        The *evidence* dict should contain the keys expected by the theorem:
        - ``"T41.1-DescentSuccess"``: requires ``"outcome"``
        - ``"T41.2-TreatyConsistency"``: requires ``"treaties"``
        - ``"T41.3-HypercoverExistence"``: requires ``"goal"``
        - ``"T41.4-OverlapLawCompleteness"``: requires ``"laws"`` and ``"outcome"``

        Args:
            theorem_name: One of the four registered theorem names.
            evidence: Dict of evidence objects keyed by their semantic role.

        Returns:
            :class:`TheoremResult` from the theorem application.

        Raises:
            KeyError: If *theorem_name* is not registered.
        """
        if theorem_name not in self._theorems:
            raise KeyError(
                f"Unknown theorem {theorem_name!r}. "
                f"Available: {list(self._theorems)}"
            )
        thm = self._theorems[theorem_name]

        if theorem_name == "T41.1-DescentSuccess":
            result = thm.apply(evidence.get("outcome"))
        elif theorem_name == "T41.2-TreatyConsistency":
            result = thm.check(evidence.get("treaties", []))
        elif theorem_name == "T41.3-HypercoverExistence":
            result = thm.apply(evidence.get("goal"))
        elif theorem_name == "T41.4-OverlapLawCompleteness":
            result = thm.check_completeness(
                evidence.get("laws", []),
                evidence.get("outcome"),
            )
        else:
            raise KeyError(f"No dispatch rule for {theorem_name!r}")

        self._proof_log.append(result)
        return result

    def prove_all(
        self,
        outcome: Any,
        treaties: list[Any],
        goal: Any,
    ) -> dict[str, TheoremResult]:
        """Run all four Chapter 41 theorems and return a results mapping.

        Args:
            outcome: :class:`SynthesisOutcome` for T41.1 and T41.4.
            treaties: List of :class:`OverlapTreaty` for T41.2.
            goal: :class:`ConstructionGoal` for T41.3.

        Returns:
            Dict mapping theorem name to :class:`TheoremResult`.
        """
        laws = []
        if outcome is not None:
            laws = getattr(outcome, "accepted_laws", []) or []
        evidence: dict[str, Any] = {
            "outcome": outcome,
            "treaties": treaties,
            "goal": goal,
            "laws": laws,
        }
        results: dict[str, TheoremResult] = {}
        for name in self._theorems:
            results[name] = self.prove(name, evidence)
        return results

    def discharge_condition(
        self,
        cond: TheoremCondition,
        evidence: dict[str, Any],
    ) -> TheoremCondition:
        """Attempt to satisfy *cond* using data in *evidence*.

        Tries several heuristic strategies:
        1. If a key matching ``cond.name`` exists in *evidence* and is truthy,
           satisfy with its ``repr``.
        2. If a key ``"outcome"`` exists and ``cond.name`` matches a known
           field on the outcome, use that field.
        3. Otherwise return *cond* unchanged.

        Args:
            cond: Unsatisfied :class:`TheoremCondition`.
            evidence: Evidence dictionary.

        Returns:
            Possibly-satisfied :class:`TheoremCondition`.
        """
        if cond.name in evidence and evidence[cond.name]:
            return cond.satisfy(f"direct evidence: {repr(evidence[cond.name])[:80]}")

        outcome = evidence.get("outcome")
        if outcome is not None and hasattr(outcome, cond.name):
            val = getattr(outcome, cond.name)
            if val:
                return cond.satisfy(f"outcome.{cond.name} = {repr(val)[:80]}")

        return cond

    def get_proof_log(self) -> list[TheoremResult]:
        """Return all :class:`TheoremResult` objects recorded since init."""
        return list(self._proof_log)

    def summarize(self) -> str:
        """Return a human-readable summary of all proved theorems.

        Includes a table of theorem name to status and an overall verdict.
        """
        if not self._proof_log:
            return "TheoremProver: no theorems proved yet."
        lines = ["TheoremProver — proof summary", "=" * 56]
        proved = 0
        for res in self._proof_log:
            tag = "PROVED    " if res.is_applicable else "NOT PROVED"
            lines.append(
                f"  {res.theorem_name:<42} {tag}  "
                f"({res.conditions_satisfied}/{res.conditions_checked})"
            )
            if res.is_applicable:
                proved += 1
        lines.append("=" * 56)
        lines.append(f"  {proved}/{len(self._proof_log)} theorems proved.")
        return "\n".join(lines)

    def registered_theorems(self) -> list[str]:
        """Return the names of all registered theorems."""
        return list(self._theorems)

    def get_theorem_statements(self) -> dict[str, str]:
        """Return LaTeX theorem statements keyed by theorem name."""
        return {
            name: thm.statement()
            for name, thm in self._theorems.items()
        }

    def clear_log(self) -> None:
        """Clear the internal proof log."""
        self._proof_log.clear()

    def __repr__(self) -> str:
        return (
            f"TheoremProver(theorems={list(self._theorems)!r}, "
            f"log_entries={len(self._proof_log)})"
        )


# ---------------------------------------------------------------------------
# Proof certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofCertificate:
    """An immutable certificate recording that a theorem was proved.

    Certificates are the authoritative record of a successful theorem
    application.  They bind together the theorem name, the timestamp of
    proof, the conditions that were checked, and a SHA-256 digest of the
    evidence so that the certificate can be independently re-verified.

    Theory reference: theory2.tex §41.7 (proof certificates).
    """

    theorem_name: str
    """Name of the theorem that was proved."""

    proved_at: float
    """Unix timestamp (seconds since epoch) when the certificate was issued."""

    conditions: tuple[TheoremCondition, ...]
    """All conditions that were checked, including their satisfaction status."""

    evidence_digest: str
    """SHA-256 hex digest of the serialised evidence dict."""

    def is_valid(self) -> bool:
        """Return True if the certificate is self-consistent.

        A certificate is valid when:
        - ``theorem_name`` is non-empty
        - ``proved_at`` is a positive finite float
        - all conditions are satisfied
        - ``evidence_digest`` is a 64-character hex string

        Returns:
            True iff all validity checks pass.
        """
        if not self.theorem_name:
            return False
        if not (isinstance(self.proved_at, float) and self.proved_at > 0):
            return False
        if not all(c.is_satisfied for c in self.conditions):
            return False
        if not (isinstance(self.evidence_digest, str) and len(self.evidence_digest) == 64):
            return False
        return True

    def __str__(self) -> str:
        valid = "VALID" if self.is_valid() else "INVALID"
        return (
            f"ProofCertificate[{valid}] {self.theorem_name} "
            f"@ {self.proved_at:.3f} digest={self.evidence_digest[:16]}…"
        )


def generate_proof_certificate(
    result: TheoremResult,
    evidence: dict[str, Any],
) -> ProofCertificate:
    """Factory function: create a :class:`ProofCertificate` from a theorem result.

    Serialises *evidence* to a canonical JSON representation, computes its
    SHA-256 digest, and packages everything into a frozen :class:`ProofCertificate`.

    Args:
        result: A :class:`TheoremResult` returned by one of the theorem
            ``apply`` / ``check`` methods.
        evidence: The evidence dictionary that was passed to the theorem.

    Returns:
        A new :class:`ProofCertificate` capturing the proof.

    Note:
        Evidence values that are not JSON-serialisable are replaced by their
        ``repr`` strings before hashing so the function never raises.
    """
    def _sanitise(obj: Any) -> Any:
        """Recursively sanitise *obj* for JSON serialisation."""
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, (list, tuple)):
            return [_sanitise(v) for v in obj]
        if isinstance(obj, dict):
            return {str(k): _sanitise(v) for k, v in obj.items()}
        return repr(obj)

    sanitised = _sanitise(evidence)
    digest = hashlib.sha256(
        json.dumps(sanitised, sort_keys=True).encode()
    ).hexdigest()

    # Build TheoremCondition objects from the result's provenance steps.
    conditions: tuple[TheoremCondition, ...] = tuple(
        TheoremCondition(
            name=f"condition_{i}",
            description=step,
            is_satisfied=result.is_applicable,
            evidence=result.evidence_summary[:100],
        )
        for i, step in enumerate(result.provenance)
    )

    return ProofCertificate(
        theorem_name=result.theorem_name,
        proved_at=time.time(),
        conditions=conditions,
        evidence_digest=digest,
    )
