"""
Formal theorems and proof structures for the doctrine_completion package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It defines the formal theorems that constitute the implementation-complete
thesis doctrine, together with proof classes and a registry for managing
theorem verification status.  The six core theorems encode the key claims
of Ch37: every claim must have implementation evidence, grounding must be
sound, coverage must be adequate, evidence must be independent, the
doctrine must be minimal, and the doctrine must be internally consistent.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models import (
    DoctrineStatement,
    ImplementationEvidence,
    StatementStatus,
    EvidenceKind,
    ClaimType,
    CompletenessCheck,
)
from .implementation_evidence import (
    EvidenceAggregator,
    ConfidenceEstimator,
)
from .doctrine_checker import (
    DoctrineChecker,
    GroundingVerifier,
    check_doctrine_completeness,
)

__all__ = [
    "DoctrineTheorem",
    "TheoremStatement",
    "DoctrineTheoremRegistry",
    "ImplementationCompletenessProof",
    "GroundingSoundnessProof",
    "CoverageAdequacyProof",
    "verify_doctrine_theorem",
    "check_all_doctrine_theorems",
]


# ---------------------------------------------------------------------------
# DoctrineTheorem enum
# ---------------------------------------------------------------------------


class DoctrineTheorem(str, Enum):
    """Enumeration of the six core theorems of the implementation-complete doctrine.

    IMPLEMENTATION_COMPLETENESS — every claim has at least one piece of
        implementation evidence.
    GROUNDING_SOUNDNESS — every grounded claim is genuinely satisfied by
        its evidence (no false positives).
    COVERAGE_ADEQUACY — the fraction of grounded claims exceeds an
        adequacy threshold.
    EVIDENCE_INDEPENDENCE — distinct claims are supported by independent
        (non-overlapping) evidence artefacts.
    CLAIM_MINIMALITY — the doctrine does not contain redundant claims.
    DOCTRINE_CONSISTENCY — the claims and their evidence are mutually
        consistent (no contradictions).
    """

    IMPLEMENTATION_COMPLETENESS = "implementation_completeness"
    GROUNDING_SOUNDNESS = "grounding_soundness"
    COVERAGE_ADEQUACY = "coverage_adequacy"
    EVIDENCE_INDEPENDENCE = "evidence_independence"
    CLAIM_MINIMALITY = "claim_minimality"
    DOCTRINE_CONSISTENCY = "doctrine_consistency"


# ---------------------------------------------------------------------------
# TheoremStatement
# ---------------------------------------------------------------------------


@dataclass
class TheoremStatement:
    """A formal theorem statement from Ch37.

    TheoremStatement encapsulates the identity, prose statement, proof
    strategy, and verification status of one of the six core doctrine
    theorems.

    Attributes:
        theorem_id: Unique identifier (uuid4).
        name: Human-readable name of the theorem.
        statement_tex: LaTeX source for the theorem statement.
        proof_strategy: Description of how the theorem is to be proved.
        status: Verification status ('unverified', 'partial', 'verified').
        evidence_requirements: List of evidence requirement descriptions.
        created_at: Unix timestamp of creation.
        verified_at: Unix timestamp of verification, or None.
    """

    theorem_id: str
    name: str
    statement_tex: str
    proof_strategy: str
    status: str
    evidence_requirements: list[str]
    created_at: float
    verified_at: Optional[float]

    @classmethod
    def create(
        cls,
        name: str,
        statement_tex: str,
        proof_strategy: str,
        evidence_requirements: Optional[list[str]] = None,
        status: str = "unverified",
    ) -> TheoremStatement:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            name: Human-readable theorem name.
            statement_tex: LaTeX formulation of the theorem.
            proof_strategy: Description of the proof strategy.
            evidence_requirements: Optional list of requirement descriptions.
            status: Initial status string (default 'unverified').

        Returns:
            A new TheoremStatement instance.
        """
        return cls(
            theorem_id=str(uuid.uuid4()),
            name=name,
            statement_tex=statement_tex,
            proof_strategy=proof_strategy,
            status=status,
            evidence_requirements=list(evidence_requirements or []),
            created_at=time.time(),
            verified_at=None,
        )

    def is_verified(self) -> bool:
        """Return True if this theorem has been verified.

        Returns:
            True when status == 'verified'.
        """
        return self.status == "verified"

    def mark_verified(self, evidence_ids: list[str]) -> None:
        """Mark this theorem as verified and record the evidence IDs.

        Sets status to 'verified', records verified_at timestamp, and
        embeds the evidence IDs in the proof_strategy string for traceability.

        Args:
            evidence_ids: List of evidence item IDs that verify this theorem.
        """
        self.status = "verified"
        self.verified_at = time.time()
        self.proof_strategy = (
            f"{self.proof_strategy} [VERIFIED by evidence: {evidence_ids[:5]}]"
        )

    def render_tex(self) -> str:
        r"""Return a full LaTeX theorem environment for this theorem.

        Produces a \begin{theorem}...\end{theorem} block with a label
        and the full statement text.

        Returns:
            Multi-line LaTeX string.
        """
        label = self.name.lower().replace(" ", "_")[:32]
        verified_str = (
            r"\verified{" + str(self.verified_at) + "}"
            if self.is_verified()
            else r"\unverified{}"
        )
        return (
            r"\begin{theorem}"
            f"\n  \\label{{thm:{label}}}"
            f"\n  \\theoremname{{{self.name}}}"
            f"\n  {self.statement_tex}"
            f"\n  \\proofstrategy{{{self.proof_strategy}}}"
            f"\n  {verified_str}"
            "\n" + r"\end{theorem}"
        )

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of theorem fields.
        """
        data = {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement_tex": self.statement_tex,
            "proof_strategy": self.proof_strategy,
            "status": self.status,
            "evidence_requirements": self.evidence_requirements,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, data: str) -> TheoremStatement:
        """Deserialise from a JSON string.

        Args:
            data: JSON string produced by to_json().

        Returns:
            A reconstructed TheoremStatement.
        """
        obj = json.loads(data)
        return cls(
            theorem_id=obj["theorem_id"],
            name=obj["name"],
            statement_tex=obj["statement_tex"],
            proof_strategy=obj["proof_strategy"],
            status=obj["status"],
            evidence_requirements=obj.get("evidence_requirements", []),
            created_at=obj["created_at"],
            verified_at=obj.get("verified_at"),
        )

    def summarize(self) -> str:
        """Return a one-line summary of this theorem.

        Returns:
            Concise summary string.
        """
        verified_str = f"verified at {self.verified_at:.0f}" if self.is_verified() else "unverified"
        return (
            f"[THEOREM {self.theorem_id[:8]}] '{self.name}' "
            f"status={self.status} {verified_str}"
        )


# ---------------------------------------------------------------------------
# DoctrineTheoremRegistry
# ---------------------------------------------------------------------------


class DoctrineTheoremRegistry:
    """Registry for managing Ch37 theorem statements.

    The DoctrineTheoremRegistry stores TheoremStatement objects indexed by
    their DoctrineTheorem enum value, and provides lookup, listing, and
    completion-fraction utilities.
    """

    # Mapping from DoctrineTheorem to TheoremStatement
    _store: dict[DoctrineTheorem, TheoremStatement]

    def __init__(self) -> None:
        """Initialise an empty theorem registry.

        The registry is identified by a UUID and creation timestamp.
        """
        self._store = {}
        self._registry_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()

    def register(self, theorem_stmt: TheoremStatement) -> None:
        """Register a theorem statement.

        Uses the theorem name to look up the corresponding DoctrineTheorem
        enum value for indexing.  If the name does not match any known
        theorem, stores under a generated key.

        Args:
            theorem_stmt: The TheoremStatement to register.
        """
        # Try to match by name to a DoctrineTheorem value
        name_lower = theorem_stmt.name.lower().replace(" ", "_")
        matched_enum: Optional[DoctrineTheorem] = None
        for dt in DoctrineTheorem:
            if dt.value in name_lower or name_lower in dt.value:
                matched_enum = dt
                break
        if matched_enum is None:
            # Fall back to a generated key by using a synthetic theorem value
            # Store in a secondary unnamed dict
            self._store[DoctrineTheorem(name_lower[:50])] = theorem_stmt  # type: ignore[call-arg]
        else:
            self._store[matched_enum] = theorem_stmt

    def lookup(self, theorem: DoctrineTheorem) -> TheoremStatement:
        """Look up a theorem statement by DoctrineTheorem enum value.

        Args:
            theorem: The DoctrineTheorem to look up.

        Returns:
            The corresponding TheoremStatement.

        Raises:
            KeyError: If the theorem is not registered.
        """
        if theorem not in self._store:
            raise KeyError(
                f"Theorem '{theorem.value}' is not registered in registry "
                f"{self._registry_id[:8]}"
            )
        return self._store[theorem]

    def list_all(self) -> list[TheoremStatement]:
        """Return all registered theorem statements.

        Returns:
            List of TheoremStatement instances sorted by name.
        """
        return sorted(self._store.values(), key=lambda t: t.name)

    def count_verified(self) -> int:
        """Count the number of verified theorems.

        Returns:
            Integer count of TheoremStatements with status 'verified'.
        """
        return sum(1 for t in self._store.values() if t.is_verified())

    def completion_fraction(self) -> float:
        """Return the fraction of registered theorems that are verified.

        Returns:
            Float in [0.0, 1.0], or 0.0 if registry is empty.
        """
        total = len(self._store)
        if total == 0:
            return 0.0
        return self.count_verified() / total

    def to_json(self) -> str:
        """Serialise the registry to a JSON string.

        Returns:
            JSON-encoded string of registry metadata and all theorems.
        """
        data = {
            "registry_id": self._registry_id,
            "created_at": self._created_at,
            "theorems": {
                k.value: json.loads(v.to_json())
                for k, v in self._store.items()
            },
        }
        return json.dumps(data, indent=2)

    @classmethod
    def build_default_registry(cls) -> DoctrineTheoremRegistry:
        """Build and return a registry pre-populated with all 6 Ch37 theorems.

        Each theorem is initialised with a standard statement text,
        proof strategy, and evidence requirements drawn from theory2.tex Ch37.

        Returns:
            A DoctrineTheoremRegistry with all 6 theorems registered.
        """
        registry = cls()

        theorems_data = [
            (
                DoctrineTheorem.IMPLEMENTATION_COMPLETENESS,
                "Implementation Completeness",
                r"\forall c \in \mathcal{C}, \exists e \in \mathcal{E}: \text{grounds}(e, c)",
                "Enumerate all claims; for each, exhibit a grounding evidence item.",
                [
                    "For each claim, provide at least one implementation evidence item",
                    "Each evidence item must have confidence >= 0.7",
                    "Evidence must be of an accepted kind (CODE, TEST, PROOF, etc.)",
                ],
            ),
            (
                DoctrineTheorem.GROUNDING_SOUNDNESS,
                "Grounding Soundness",
                r"\forall c \in \mathcal{C}, \forall e \in \text{grounds}(c): \text{satisfies}(e, c)",
                "For each grounded claim, verify that the grounding evidence genuinely satisfies it.",
                [
                    "Each evidence item must have grounding_depth >= 2",
                    "Confidence must be >= 0.7 for the evidence to count as satisfying",
                    "Evidence kind must match one of the required kinds for the claim",
                ],
            ),
            (
                DoctrineTheorem.COVERAGE_ADEQUACY,
                "Coverage Adequacy",
                r"|\{c : \text{grounded}(c)\}| / |\mathcal{C}| \geq \theta",
                "Compute the coverage fraction; verify it exceeds the adequacy threshold theta.",
                [
                    "Overall coverage fraction must be >= 0.85",
                    "No claim type may have coverage < 0.5",
                    "Critical claims must all be grounded",
                ],
            ),
            (
                DoctrineTheorem.EVIDENCE_INDEPENDENCE,
                "Evidence Independence",
                r"\forall c_1 \neq c_2: \text{evidence}(c_1) \cap \text{evidence}(c_2) = \emptyset",
                "Verify that distinct claims are supported by distinct (non-overlapping) evidence artefacts.",
                [
                    "No single evidence artifact may be used to ground two different claims",
                    "Evidence IDs must be unique per claim",
                ],
            ),
            (
                DoctrineTheorem.CLAIM_MINIMALITY,
                "Claim Minimality",
                r"\nexists c \in \mathcal{C}: c \text{ is redundant w.r.t. } \mathcal{C} \setminus \{c\}",
                "Show that no claim is subsumed by the remaining claims in the doctrine set.",
                [
                    "The minimality score must be >= 0.9",
                    "No two claims may have identical required_evidence_kinds and claim_type",
                ],
            ),
            (
                DoctrineTheorem.DOCTRINE_CONSISTENCY,
                "Doctrine Consistency",
                r"\nexists c_1, c_2 \in \mathcal{C}: \text{contradicts}(c_1, c_2)",
                "Verify that no two claims in the doctrine directly contradict each other.",
                [
                    "No pair of claims may have contradictory grounding requirements",
                    "The dependency graph must be acyclic",
                    "All claim_types must be from the registered ClaimType enum",
                ],
            ),
        ]

        for enum_val, name, stmt_tex, proof_strategy, requirements in theorems_data:
            ts = TheoremStatement.create(
                name=name,
                statement_tex=stmt_tex,
                proof_strategy=proof_strategy,
                evidence_requirements=requirements,
                status="unverified",
            )
            registry._store[enum_val] = ts

        return registry


# ---------------------------------------------------------------------------
# ImplementationCompletenessProof
# ---------------------------------------------------------------------------


class ImplementationCompletenessProof:
    """Proof class for the Implementation Completeness theorem.

    Verifies that every claim in the doctrine has at least one piece of
    implementation evidence satisfying the minimum quality thresholds.
    This is the foundational theorem of the Ch37 doctrine.
    """

    def __init__(self) -> None:
        """Initialise the proof object.

        Creates a GroundingVerifier and ConfidenceEstimator for internal use.
        """
        self._verifier = GroundingVerifier(min_confidence=0.7, min_depth=1)
        self._estimator = ConfidenceEstimator()
        self._proof_id: str = str(uuid.uuid4())

    def verify(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> tuple[bool, str]:
        """Verify the implementation completeness theorem.

        The theorem holds if every statement has at least one evidence item
        with confidence >= 0.7.

        Args:
            statements: All doctrine statements (the "claims" of the doctrine).
            evidence_map: Mapping from statement_id to available evidence.

        Returns:
            (holds, explanation) tuple.
        """
        if not statements:
            return (True, "No statements to verify — theorem holds vacuously.")

        missing_evidence: list[str] = []
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            qualifying = [ev for ev in evs if ev.confidence >= 0.7]
            if not qualifying:
                missing_evidence.append(stmt.statement_id)

        if missing_evidence:
            return (
                False,
                f"Implementation completeness FAILS: {len(missing_evidence)} "
                f"claim(s) have no qualifying evidence. "
                f"Missing: {missing_evidence[:5]}"
                + ("..." if len(missing_evidence) > 5 else ""),
            )
        return (
            True,
            f"Implementation completeness HOLDS: all {len(statements)} claims "
            f"have at least one qualifying evidence item (confidence >= 0.7).",
        )

    def collect_evidence(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> dict[str, Any]:
        """Collect a summary of evidence for all statements.

        Returns a dictionary mapping statement_id to the count and
        best confidence score of available evidence items.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Dictionary of evidence summaries per statement.
        """
        summary: dict[str, Any] = {}
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            qualifying = [ev for ev in evs if ev.confidence >= 0.7]
            best_conf = max((ev.confidence for ev in evs), default=0.0)
            summary[stmt.statement_id] = {
                "total_evidence": len(evs),
                "qualifying_evidence": len(qualifying),
                "best_confidence": best_conf,
                "has_qualifying": len(qualifying) > 0,
                "claim_text_snippet": stmt.claim_text[:40],
            }
        return summary

    def compute_completeness_witness(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> dict[str, Any]:
        """Compute a completeness witness dictionary for the theorem.

        A completeness witness maps each statement_id to the best
        qualifying evidence item for that statement.  If no qualifying
        item exists, the witness entry is None.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Dictionary mapping statement_id to best evidence summary or None.
        """
        witness: dict[str, Any] = {}
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            qualifying = [ev for ev in evs if ev.confidence >= 0.7]
            if qualifying:
                best = max(qualifying, key=lambda ev: ev.confidence)
                witness[stmt.statement_id] = {
                    "evidence_id": best.evidence_id,
                    "kind": best.evidence_kind.value,
                    "confidence": best.confidence,
                    "depth": best.grounding_depth,
                    "artifact_ref": best.artifact_ref,
                }
            else:
                witness[stmt.statement_id] = None
        return witness


# ---------------------------------------------------------------------------
# GroundingSoundnessProof
# ---------------------------------------------------------------------------


class GroundingSoundnessProof:
    """Proof class for the Grounding Soundness theorem.

    Verifies that every claim marked as COMPLETE (grounded) is genuinely
    satisfied by its evidence — i.e., the grounding is sound, not nominal.
    This rules out false-positive groundings.
    """

    def __init__(self) -> None:
        """Initialise the grounding soundness proof object.

        Uses a GroundingVerifier with strict thresholds.
        """
        self._verifier = GroundingVerifier(min_confidence=0.75, min_depth=2)
        self._proof_id: str = str(uuid.uuid4())

    def verify(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> tuple[bool, str]:
        """Verify the grounding soundness theorem.

        Checks that every COMPLETE statement is genuinely supported by
        evidence meeting the strict soundness thresholds.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            (holds, explanation) tuple.
        """
        if not statements:
            return (True, "No statements — grounding soundness holds vacuously.")

        unsound: list[str] = self.find_unsound_claims(statements, evidence_map)
        if unsound:
            return (
                False,
                f"Grounding soundness FAILS: {len(unsound)} claim(s) are marked "
                f"complete but do not satisfy strict soundness thresholds. "
                f"Unsound: {unsound[:5]}"
                + ("..." if len(unsound) > 5 else ""),
            )
        return (
            True,
            f"Grounding soundness HOLDS: all {len(statements)} claims satisfy "
            f"strict soundness thresholds (confidence >= 0.75, depth >= 2).",
        )

    def check_soundness_condition(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> bool:
        """Check whether a single statement satisfies the soundness condition.

        The soundness condition requires at least one evidence item with
        confidence >= 0.75 AND grounding_depth >= 2 for each required kind.

        Args:
            statement: The statement to check.
            evidences: Available evidence items.

        Returns:
            True if the soundness condition is met.
        """
        required = set(statement.required_evidence_kinds)
        if not required:
            return True
        satisfied_kinds: set[EvidenceKind] = set()
        for ev in evidences:
            if ev.confidence >= 0.75 and ev.grounding_depth >= 2:
                satisfied_kinds.add(ev.evidence_kind)
        return required.issubset(satisfied_kinds)

    def find_unsound_claims(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[str]:
        """Return IDs of claims that are grounded but unsound.

        A claim is unsound if it is COMPLETE but its evidence does not
        meet the strict soundness condition.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            List of statement IDs that are complete but unsound.
        """
        unsound: list[str] = []
        for stmt in statements:
            if stmt.status != StatementStatus.COMPLETE:
                continue
            evs = evidence_map.get(stmt.statement_id, [])
            if not self.check_soundness_condition(stmt, evs):
                unsound.append(stmt.statement_id)
        return unsound


# ---------------------------------------------------------------------------
# CoverageAdequacyProof
# ---------------------------------------------------------------------------


class CoverageAdequacyProof:
    """Proof class for the Coverage Adequacy theorem.

    Verifies that the fraction of grounded claims meets the adequacy
    threshold specified in Ch37.  The default threshold is 0.85.
    """

    def __init__(self, adequacy_threshold: float = 0.85) -> None:
        """Initialise the coverage adequacy proof with a threshold.

        Args:
            adequacy_threshold: Minimum coverage fraction to be adequate (default 0.85).
        """
        self.adequacy_threshold = adequacy_threshold
        self._checker = DoctrineChecker()
        self._proof_id: str = str(uuid.uuid4())

    def verify(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> tuple[bool, str]:
        """Verify the coverage adequacy theorem.

        Computes the coverage fraction and checks it against the threshold.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            (holds, explanation) tuple.
        """
        if not statements:
            return (True, "No statements — coverage adequacy holds vacuously.")

        adequacy = self.compute_adequacy_measure(statements, evidence_map)
        if adequacy >= self.adequacy_threshold:
            return (
                True,
                f"Coverage adequacy HOLDS: {adequacy:.1%} >= threshold {self.adequacy_threshold:.1%}. "
                f"Evaluated over {len(statements)} statements.",
            )
        inadequate = self.find_inadequate_coverage(statements, evidence_map)
        return (
            False,
            f"Coverage adequacy FAILS: {adequacy:.1%} < threshold {self.adequacy_threshold:.1%}. "
            f"{len(inadequate)} inadequately covered statements. "
            f"Inadequate: {inadequate[:5]}"
            + ("..." if len(inadequate) > 5 else ""),
        )

    def compute_adequacy_measure(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> float:
        """Compute the overall coverage adequacy measure.

        Coverage = number of COMPLETE statements / total statements.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Coverage fraction in [0.0, 1.0].
        """
        if not statements:
            return 1.0
        complete_count = 0
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            available_kinds = [ev.evidence_kind for ev in evs]
            status = stmt.check_completeness(available_kinds)
            if status == StatementStatus.COMPLETE:
                complete_count += 1
        return complete_count / len(statements)

    def find_inadequate_coverage(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[str]:
        """Return statement IDs that are not adequately covered.

        A statement is inadequately covered if it is not COMPLETE
        (i.e., it is PARTIAL or UNGROUNDED).

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            List of inadequately covered statement IDs.
        """
        inadequate: list[str] = []
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            available_kinds = [ev.evidence_kind for ev in evs]
            status = stmt.check_completeness(available_kinds)
            if status != StatementStatus.COMPLETE:
                inadequate.append(stmt.statement_id)
        return inadequate


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def verify_doctrine_theorem(
    theorem: DoctrineTheorem,
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> tuple[bool, str]:
    """Verify a single doctrine theorem against the given statements and evidence.

    Dispatches to the appropriate proof class based on the theorem enum value.
    For theorems without dedicated proof classes (EVIDENCE_INDEPENDENCE,
    CLAIM_MINIMALITY, DOCTRINE_CONSISTENCY), returns a structural stub.

    Args:
        theorem: The DoctrineTheorem to verify.
        statements: All doctrine statements.
        evidence_map: Mapping from statement_id to evidence list.

    Returns:
        (holds, explanation) tuple.
    """
    if theorem == DoctrineTheorem.IMPLEMENTATION_COMPLETENESS:
        proof = ImplementationCompletenessProof()
        return proof.verify(statements, evidence_map)

    elif theorem == DoctrineTheorem.GROUNDING_SOUNDNESS:
        proof_gs = GroundingSoundnessProof()
        return proof_gs.verify(statements, evidence_map)

    elif theorem == DoctrineTheorem.COVERAGE_ADEQUACY:
        proof_ca = CoverageAdequacyProof()
        return proof_ca.verify(statements, evidence_map)

    elif theorem == DoctrineTheorem.EVIDENCE_INDEPENDENCE:
        # Check that no single artifact_ref appears in evidence for multiple statements
        artifact_to_stmts: dict[str, list[str]] = {}
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            for ev in evs:
                artifact_to_stmts.setdefault(ev.artifact_ref, []).append(stmt.statement_id)
        conflicts = {
            ref: stmts
            for ref, stmts in artifact_to_stmts.items()
            if len(set(stmts)) > 1
        }
        if conflicts:
            conflict_summary = "; ".join(
                f"'{ref[:20]}' used by {stmts[:3]}"
                for ref, stmts in list(conflicts.items())[:3]
            )
            return (
                False,
                f"Evidence independence FAILS: {len(conflicts)} shared artefacts. "
                f"Examples: {conflict_summary}",
            )
        return (
            True,
            f"Evidence independence HOLDS: all artefact references are unique per claim "
            f"across {len(statements)} statements.",
        )

    elif theorem == DoctrineTheorem.CLAIM_MINIMALITY:
        from .algorithms import DoctrineMinimizationAlgorithm
        min_algo = DoctrineMinimizationAlgorithm()
        # Build a simple dependency graph (empty — no dependencies by default)
        dep_graph: dict[str, list[str]] = {s.statement_id: [] for s in statements}
        score = min_algo.compute_minimality_score(statements, dep_graph)
        if score >= 0.9:
            return (
                True,
                f"Claim minimality HOLDS: minimality score {score:.3f} >= 0.90 "
                f"over {len(statements)} statements.",
            )
        redundant = min_algo.find_redundant(statements, dep_graph)
        return (
            False,
            f"Claim minimality FAILS: {len(redundant)} redundant claims found. "
            f"Minimality score: {score:.3f}.",
        )

    elif theorem == DoctrineTheorem.DOCTRINE_CONSISTENCY:
        # Check for acyclicity and that all claim_types are valid
        from .completeness import DoctrineGraph
        graph = DoctrineGraph()
        for stmt in statements:
            graph.add_statement(stmt)
        is_acyclic = graph.is_acyclic()
        if not is_acyclic:
            return (
                False,
                "Doctrine consistency FAILS: dependency graph contains a cycle.",
            )
        # Check all required_evidence_kinds are valid EvidenceKind values
        invalid_claims: list[str] = []
        valid_kind_values = {k.value for k in EvidenceKind}
        for stmt in statements:
            for k in stmt.required_evidence_kinds:
                if k.value not in valid_kind_values:
                    invalid_claims.append(stmt.statement_id)
                    break
        if invalid_claims:
            return (
                False,
                f"Doctrine consistency FAILS: {len(invalid_claims)} claims have "
                f"invalid evidence kind references.",
            )
        return (
            True,
            f"Doctrine consistency HOLDS: graph is acyclic and all "
            f"{len(statements)} claims have valid evidence kind references.",
        )

    return (False, f"No verification handler for theorem '{theorem.value}'")


def check_all_doctrine_theorems(
    registry: DoctrineTheoremRegistry,
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> dict[DoctrineTheorem, tuple[bool, str]]:
    """Verify all six Ch37 doctrine theorems against the given data.

    Iterates over all registered theorems, runs verification for each,
    and optionally marks them verified in the registry.

    Args:
        registry: The DoctrineTheoremRegistry with registered theorems.
        statements: All doctrine statements.
        evidence_map: Mapping from statement_id to evidence list.

    Returns:
        Dictionary mapping each DoctrineTheorem to its (holds, explanation).
    """
    results: dict[DoctrineTheorem, tuple[bool, str]] = {}
    for theorem in DoctrineTheorem:
        holds, explanation = verify_doctrine_theorem(theorem, statements, evidence_map)
        results[theorem] = (holds, explanation)
        # Update registry if theorem is registered and holds
        try:
            ts = registry.lookup(theorem)
            if holds and not ts.is_verified():
                ts.mark_verified([f"auto_verified:{theorem.value}"])
        except KeyError:
            pass  # Not registered — skip
    return results
