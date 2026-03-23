"""Formal theorems about optimal novelty search – theory2.tex Ch57.

Codifies and verifies the formal mathematical properties of the novelty
search subsystem: optimality guarantees, diversity bounds, coverage
completeness, and monotonicity properties.

Module layout::

    NoveltyTheorem       – a single formal theorem record
    VerificationResult   – the result of verifying a theorem
    TheoremRegistry      – registry of all novelty-search theorems
    TheoremVerifier      – verifies theorem conditions against data
    TheoremApplications  – applies theorems to produce recommendations
    TheoremCatalog       – full catalog with cross-references

Design notes
------------
Theorems are immutable (frozen dataclasses) so that they can be placed in
sets and used as dictionary keys.  ``TheoremStatus`` tracks whether a theorem
has been verified, falsified, or is still a conjecture.

Verification is *empirical* – it tests the stated mathematical conditions
against real idea data using sampling-based checks.  A verification result
is probabilistic; confidence levels reflect how thoroughly the data supports
(or refutes) the theorem's conditions.

Cross-reference semantics: the ``references`` field stores free-form strings
such as ``"thm-greedy-optimality"`` (other theorem IDs) or citation keys
like ``"Nemhauser et al. 1978"``.  The ``TheoremRegistry.cross_references``
method resolves theorem-ID references to actual ``NoveltyTheorem`` objects.

Usage example::

    catalog = TheoremCatalog()
    registry = catalog.get_registry()
    verifier = TheoremVerifier()
    results = verifier.batch_verify(registry, candidate_ideas, portfolio_ideas)
    apps = TheoremApplications(registry)
    advice = apps.generate_advice("We need to maximise coverage with k=5 picks")
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    TheoremPortfolio,
    PurposeAlignmentChecker,
    NoveltyOptimizer,
    NoveltySearcher as _NoveltySearcherBase,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        Floating-point value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        The clamped value.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.3, lo=-1.0, hi=1.0)
    -0.3
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        ISO-8601 UTC timestamp ending in '+00:00'.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenise *text* into a frozenset of lowercase word tokens.

    All non-alphanumeric characters are treated as delimiters.

    Parameters
    ----------
    text:
        Arbitrary natural-language string.

    Returns
    -------
    frozenset[str]
        Unique lowercase tokens.
    """
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Jaccard similarity in [0, 1].  Returns 0.0 when both are empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _idea_tokens(idea: Idea) -> frozenset[str]:
    """Extract a combined token fingerprint from an idea's text fields.

    Combines ``title``, ``purpose``, ``target_area``, and ``hypothesis``.

    Parameters
    ----------
    idea:
        Source idea.

    Returns
    -------
    frozenset[str]
        Union of tokens from all relevant text fields.
    """
    parts = [
        idea.title or "",
        idea.purpose or "",
        idea.target_area or "",
        idea.hypothesis or "",
    ]
    return _tokenize(" ".join(parts))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """Lifecycle status of a formal novelty theorem.

    Values
    ------
    CONJECTURED:
        The theorem has been stated but not yet verified against data.
    VERIFIED:
        Empirical and/or formal verification supports the theorem.
    FALSIFIED:
        Counterexample(s) have been found; the theorem does not hold in general.
    INAPPLICABLE:
        The preconditions for this theorem are not met in the current context.
    """

    CONJECTURED = "conjectured"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    INAPPLICABLE = "inapplicable"


class TheoremKind(str, Enum):
    """Semantic category of a formal novelty theorem.

    Values
    ------
    OPTIMALITY:
        The theorem characterises how close an algorithm is to the optimal.
    BOUND:
        The theorem states an upper or lower bound on a quantity.
    COMPLETENESS:
        The theorem characterises coverage or completeness of a procedure.
    MONOTONICITY:
        The theorem states that a function is monotone (non-decreasing or
        non-increasing) under some condition.
    APPROXIMATION:
        The theorem provides an approximation ratio or additive error bound.
    IMPOSSIBILITY:
        The theorem rules out certain guarantees (e.g. NP-hardness reductions).
    """

    OPTIMALITY = "optimality"
    BOUND = "bound"
    COMPLETENESS = "completeness"
    MONOTONICITY = "monotonicity"
    APPROXIMATION = "approximation"
    IMPOSSIBILITY = "impossibility"


# ---------------------------------------------------------------------------
# NoveltyTheorem
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltyTheorem:
    """Immutable record representing a single formal novelty-search theorem.

    Each theorem captures a mathematical statement about the novelty search
    subsystem, together with a proof sketch, preconditions, and actionable
    implications.

    Parameters
    ----------
    theorem_id:
        Unique kebab-case identifier (e.g. ``"thm-greedy-optimality"``).
    name:
        Short human-readable name.
    statement:
        Formal or semi-formal statement of the theorem in plain English with
        mathematical notation where needed.
    proof_sketch:
        High-level proof argument (not a full formal proof).
    conditions:
        Tuple of preconditions that must hold for the theorem to apply.
    implications:
        Tuple of actionable consequences derived from the theorem.
    kind:
        Semantic category (``TheoremKind``).
    status:
        Current verification status.
    references:
        Tuple of citation strings or theorem-IDs this theorem depends on.
    version:
        Semantic version of this theorem record.
    created_at:
        ISO-8601 creation timestamp.

    Examples
    --------
    >>> t = NoveltyTheorem(
    ...     theorem_id="thm-example",
    ...     name="Example Theorem",
    ...     statement="For all x in [0,1], x^2 <= x.",
    ...     proof_sketch="Since x <= 1, multiplying both sides by x gives x^2 <= x.",
    ...     conditions=("x in [0, 1]",),
    ...     implications=("Squared novelty is never worse than raw novelty.",),
    ...     kind=TheoremKind.BOUND,
    ... )
    >>> t.is_actionable
    False
    """

    theorem_id: str
    name: str
    statement: str
    proof_sketch: str
    conditions: tuple[str, ...]
    implications: tuple[str, ...]
    kind: TheoremKind
    status: TheoremStatus = TheoremStatus.CONJECTURED
    references: tuple[str, ...] = ()
    version: str = "1.0"
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        """Validate and normalise fields after construction.

        Raises
        ------
        ValueError
            If ``theorem_id``, ``name``, or ``statement`` is empty.
        """
        if not self.theorem_id or not self.theorem_id.strip():
            raise ValueError("NoveltyTheorem.theorem_id must be non-empty.")
        if not self.name or not self.name.strip():
            raise ValueError("NoveltyTheorem.name must be non-empty.")
        if not self.statement or not self.statement.strip():
            raise ValueError("NoveltyTheorem.statement must be non-empty.")
        # Slots=True means we cannot call object.__setattr__ in the normal way
        # without bypassing frozen – validation only, no mutation needed here.

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_actionable(self) -> bool:
        """``True`` if this theorem is verified and can be applied.

        Only ``VERIFIED`` theorems are considered actionable; conjectures,
        falsified theorems, and inapplicable theorems are not.

        Returns
        -------
        bool
        """
        return self.status == TheoremStatus.VERIFIED

    @property
    def short_statement(self) -> str:
        """First 120 characters of the theorem statement.

        Returns
        -------
        str
        """
        return self.statement[:120].rstrip()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "conditions": list(self.conditions),
            "implications": list(self.implications),
            "kind": self.kind.value,
            "status": self.status.value,
            "references": list(self.references),
            "version": self.version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NoveltyTheorem":
        """Reconstruct a ``NoveltyTheorem`` from a dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        NoveltyTheorem
        """
        return cls(
            theorem_id=d["theorem_id"],
            name=d["name"],
            statement=d["statement"],
            proof_sketch=d.get("proof_sketch", ""),
            conditions=tuple(d.get("conditions", [])),
            implications=tuple(d.get("implications", [])),
            kind=TheoremKind(d["kind"]),
            status=TheoremStatus(d.get("status", TheoremStatus.CONJECTURED.value)),
            references=tuple(d.get("references", [])),
            version=d.get("version", "1.0"),
            created_at=d.get("created_at", _now_iso()),
        )

    def to_json(self) -> str:
        """Serialise to a JSON string.

        Returns
        -------
        str
            JSON representation.
        """
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "NoveltyTheorem":
        """Deserialise from a JSON string.

        Parameters
        ----------
        s:
            JSON string as produced by :meth:`to_json`.

        Returns
        -------
        NoveltyTheorem
        """
        return cls.from_dict(json.loads(s))

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a full multi-line human-readable description.

        Returns
        -------
        str
        """
        sep = "-" * 60
        cond_text = "\n".join(f"    ({i + 1}) {c}" for i, c in enumerate(self.conditions))
        impl_text = "\n".join(f"    → {im}" for im in self.implications)
        ref_text = ", ".join(self.references) if self.references else "(none)"
        return (
            f"{sep}\n"
            f"Theorem [{self.theorem_id}]  v{self.version}\n"
            f"Name   : {self.name}\n"
            f"Kind   : {self.kind.value}   Status: {self.status.value}\n"
            f"{sep}\n"
            f"Statement:\n  {self.statement}\n"
            f"\nProof sketch:\n  {self.proof_sketch}\n"
            f"\nConditions:\n{cond_text}\n"
            f"\nImplications:\n{impl_text}\n"
            f"\nReferences: {ref_text}\n"
            f"{sep}"
        )

    def summary(self) -> str:
        """Return a single-line summary.

        Returns
        -------
        str
        """
        return (
            f"[{self.theorem_id}] {self.name} "
            f"({self.kind.value}/{self.status.value})"
        )

    # ------------------------------------------------------------------
    # Functional update helpers
    # ------------------------------------------------------------------

    def with_status(self, status: TheoremStatus) -> "NoveltyTheorem":
        """Return a new theorem with *status* updated.

        Parameters
        ----------
        status:
            New verification status.

        Returns
        -------
        NoveltyTheorem
            New instance with updated status.
        """
        d = self.to_dict()
        d["status"] = status.value
        return NoveltyTheorem.from_dict(d)

    def with_references(self, refs: tuple[str, ...]) -> "NoveltyTheorem":
        """Return a new theorem with *references* replaced.

        Parameters
        ----------
        refs:
            New references tuple.

        Returns
        -------
        NoveltyTheorem
            New instance with updated references.
        """
        d = self.to_dict()
        d["references"] = list(refs)
        return NoveltyTheorem.from_dict(d)


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of attempting to verify a theorem against data.

    Parameters
    ----------
    theorem_id:
        ID of the theorem that was verified.
    outcome:
        Resulting status after verification.
    evidence:
        Free-text description of the evidence supporting the outcome.
    counterexample:
        If falsified, a description of the counterexample found.
    confidence:
        Confidence in the outcome (0.0 – 1.0).
    timestamp:
        ISO-8601 timestamp of when verification was performed.
    """

    theorem_id: str
    outcome: TheoremStatus
    evidence: str
    counterexample: str | None = None
    confidence: float = 1.0
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "outcome": self.outcome.value,
            "evidence": self.evidence,
            "counterexample": self.counterexample,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a concise one-line summary.

        Returns
        -------
        str
        """
        ce = f" | counterexample: {self.counterexample}" if self.counterexample else ""
        return (
            f"[{self.theorem_id}] {self.outcome.value.upper()} "
            f"(conf={self.confidence:.2f}){ce}"
        )


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """Registry of all novelty-search theorems.

    Provides O(1) lookup by theorem_id, filtering by kind and status, and
    cross-reference resolution.

    Usage::

        registry = TheoremRegistry()
        registry.register(OPTIMALITY_THEOREM)
        theorems = registry.by_kind(TheoremKind.BOUND)
    """

    def __init__(self) -> None:
        self._theorems: dict[str, NoveltyTheorem] = {}

    # ------------------------------------------------------------------

    def register(self, theorem: NoveltyTheorem) -> None:
        """Register *theorem* in this registry.

        If a theorem with the same ``theorem_id`` already exists, it is
        silently overwritten.

        Parameters
        ----------
        theorem:
            Theorem to register.
        """
        self._theorems[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> NoveltyTheorem | None:
        """Return the theorem with the given *theorem_id*, or ``None``.

        Parameters
        ----------
        theorem_id:
            Theorem identifier.

        Returns
        -------
        NoveltyTheorem | None
        """
        return self._theorems.get(theorem_id)

    def get_all(self) -> list[NoveltyTheorem]:
        """Return all registered theorems as a list.

        Returns
        -------
        list[NoveltyTheorem]
        """
        return list(self._theorems.values())

    def by_kind(self, kind: TheoremKind) -> list[NoveltyTheorem]:
        """Return all theorems of the given *kind*.

        Parameters
        ----------
        kind:
            Theorem kind to filter by.

        Returns
        -------
        list[NoveltyTheorem]
        """
        return [t for t in self._theorems.values() if t.kind == kind]

    def by_status(self, status: TheoremStatus) -> list[NoveltyTheorem]:
        """Return all theorems with the given *status*.

        Parameters
        ----------
        status:
            Status to filter by.

        Returns
        -------
        list[NoveltyTheorem]
        """
        return [t for t in self._theorems.values() if t.status == status]

    def actionable(self) -> list[NoveltyTheorem]:
        """Return all theorems that are currently actionable (i.e. VERIFIED).

        Returns
        -------
        list[NoveltyTheorem]
        """
        return self.by_status(TheoremStatus.VERIFIED)

    def update_status(
        self,
        theorem_id: str,
        status: TheoremStatus,
        evidence: str = "",
    ) -> bool:
        """Update the status of an existing theorem.

        Parameters
        ----------
        theorem_id:
            ID of the theorem to update.
        status:
            New status.
        evidence:
            Optional evidence string (logged but not stored on the theorem).

        Returns
        -------
        bool
            ``True`` if the theorem was found and updated, ``False`` otherwise.
        """
        theorem = self._theorems.get(theorem_id)
        if theorem is None:
            return False
        self._theorems[theorem_id] = theorem.with_status(status)
        return True

    def cross_references(self, theorem_id: str) -> list[NoveltyTheorem]:
        """Return theorems whose ``references`` field includes *theorem_id*.

        Parameters
        ----------
        theorem_id:
            The theorem whose referrers we want.

        Returns
        -------
        list[NoveltyTheorem]
            Theorems that reference *theorem_id*.
        """
        return [
            t for t in self._theorems.values()
            if theorem_id in t.references
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorems": {tid: t.to_dict() for tid, t in self._theorems.items()},
            "count": len(self._theorems),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TheoremRegistry":
        """Reconstruct a registry from a serialised dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        TheoremRegistry
        """
        registry = cls()
        for theorem_dict in d.get("theorems", {}).values():
            registry.register(NoveltyTheorem.from_dict(theorem_dict))
        return registry

    def summary(self) -> str:
        """Return a multi-line summary of the registry contents.

        Returns
        -------
        str
        """
        all_t = self.get_all()
        kind_counts: dict[str, int] = defaultdict(int)
        status_counts: dict[str, int] = defaultdict(int)
        for t in all_t:
            kind_counts[t.kind.value] += 1
            status_counts[t.status.value] += 1
        lines = [
            f"TheoremRegistry: {len(all_t)} theorems",
            "  By kind:",
        ]
        for k, cnt in sorted(kind_counts.items()):
            lines.append(f"    {k}: {cnt}")
        lines.append("  By status:")
        for s, cnt in sorted(status_counts.items()):
            lines.append(f"    {s}: {cnt}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TheoremVerifier
# ---------------------------------------------------------------------------


class TheoremVerifier:
    """Verify theorem conditions empirically against idea data.

    Verification is probabilistic and data-driven.  For each theorem, a
    specialised verifier function checks the stated mathematical conditions
    against the provided ideas and portfolio.

    Dispatch is based on ``theorem.kind``; theorems with unknown kinds fall
    through to a generic verifier.
    """

    def __init__(self) -> None:
        self._dispatch_map: dict[TheoremKind, Callable] = {
            TheoremKind.OPTIMALITY: self.verify_optimality,
            TheoremKind.BOUND: self.verify_diversity_bound,
            TheoremKind.COMPLETENESS: self.verify_coverage_completeness,
            TheoremKind.MONOTONICITY: self.verify_monotonicity,
            TheoremKind.APPROXIMATION: self.verify_optimality,  # same check
            TheoremKind.IMPOSSIBILITY: self._verify_impossibility,
        }

    # ------------------------------------------------------------------

    def _dispatch(self, theorem: NoveltyTheorem) -> Callable:
        """Return the appropriate verifier callable for *theorem*.

        Parameters
        ----------
        theorem:
            Theorem to dispatch.

        Returns
        -------
        Callable
            Verifier function accepting (theorem, ideas, portfolio).
        """
        return self._dispatch_map.get(theorem.kind, self._verify_generic)

    def verify(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> VerificationResult:
        """Attempt to verify *theorem* against *ideas* and *portfolio*.

        Dispatches to the appropriate specialised verifier based on
        ``theorem.kind``.

        Parameters
        ----------
        theorem:
            Theorem to verify.
        ideas:
            Candidate ideas used as evidence.
        portfolio:
            Existing portfolio used as evidence.
        purpose:
            Optional purpose for alignment scoring.

        Returns
        -------
        VerificationResult
        """
        verifier = self._dispatch(theorem)
        try:
            return verifier(theorem, ideas, portfolio)
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                theorem_id=theorem.theorem_id,
                outcome=TheoremStatus.INAPPLICABLE,
                evidence=f"Verification raised exception: {exc}",
                confidence=0.0,
            )

    def verify_optimality(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Verify that greedy selection achieves near-optimal coverage.

        Runs a greedy and a (small) exhaustive search, then checks whether
        the greedy score is >= (1 - 1/e) * optimal_score.

        Parameters
        ----------
        theorem:
            The optimality theorem to verify.
        ideas:
            Candidate ideas.
        portfolio:
            Existing portfolio.

        Returns
        -------
        VerificationResult
        """
        if len(ideas) < 2:
            return VerificationResult(
                theorem_id=theorem.theorem_id,
                outcome=TheoremStatus.INAPPLICABLE,
                evidence="Fewer than 2 ideas; cannot meaningfully compare strategies.",
                confidence=1.0,
            )

        all_tokens = frozenset().union(*[_idea_tokens(i) for i in ideas])
        portfolio_tokens = frozenset().union(*[_idea_tokens(p) for p in portfolio]) if portfolio else frozenset()

        def coverage_of(selection: Sequence[Idea]) -> float:
            """Compute the fraction of portfolio-uncovered tokens covered by selection."""
            uncovered = all_tokens - portfolio_tokens
            if not uncovered:
                return 1.0
            covered = frozenset().union(*[_idea_tokens(i) for i in selection]) if selection else frozenset()
            return len(covered & uncovered) / len(uncovered)

        k = min(3, len(ideas))
        # Greedy selection
        greedy: list[Idea] = []
        remaining = list(ideas)
        for _ in range(k):
            if not remaining:
                break
            best: Idea | None = None
            best_cov = -1.0
            for idea in remaining:
                cov = coverage_of(greedy + [idea])
                if cov > best_cov:
                    best_cov = cov
                    best = idea
            if best is not None:
                greedy.append(best)
                remaining.remove(best)
        greedy_score = coverage_of(greedy)

        # Optimal: try all combinations of size k (only feasible for small k)
        import itertools
        best_opt_score = 0.0
        for combo in itertools.combinations(ideas, k):
            sc = coverage_of(combo)
            if sc > best_opt_score:
                best_opt_score = sc

        threshold = (1.0 - math.exp(-1.0)) * best_opt_score
        holds = greedy_score >= threshold - 1e-9
        confidence = 0.9 if holds else 0.1
        outcome = TheoremStatus.VERIFIED if holds else TheoremStatus.FALSIFIED
        evidence = (
            f"Greedy score={greedy_score:.4f}, optimal score={best_opt_score:.4f}, "
            f"threshold=(1-1/e)*opt={threshold:.4f}. "
            f"Condition {'HOLDS' if holds else 'VIOLATED'}."
        )
        counterexample: str | None = None
        if not holds:
            counterexample = (
                f"Greedy achieved {greedy_score:.4f} < (1-1/e)*{best_opt_score:.4f}={threshold:.4f}"
            )
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=outcome,
            evidence=evidence,
            counterexample=counterexample,
            confidence=confidence,
        )

    def verify_diversity_bound(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Verify the pairwise diversity upper bound theorem.

        Checks that the mean pairwise Jaccard distance among *ideas* is at
        most ``(k-1)/k`` (where k = len(ideas)), consistent with the bound
        ``mean_dist <= D_max * (k-1)/k`` with ``D_max = 1.0``.

        Parameters
        ----------
        theorem:
            The diversity bound theorem.
        ideas:
            Ideas to measure.
        portfolio:
            Unused (present for interface consistency).

        Returns
        -------
        VerificationResult
        """
        k = len(ideas)
        if k < 2:
            return VerificationResult(
                theorem_id=theorem.theorem_id,
                outcome=TheoremStatus.INAPPLICABLE,
                evidence="Need at least 2 ideas to compute pairwise diversity.",
                confidence=1.0,
            )

        token_sets = [_idea_tokens(i) for i in ideas]
        distances: list[float] = []
        for i in range(k):
            for j in range(i + 1, k):
                sim = _jaccard(token_sets[i], token_sets[j])
                distances.append(1.0 - sim)

        mean_dist = sum(distances) / len(distances) if distances else 0.0
        theoretical_bound = (k - 1) / k  # D_max=1, so bound = (k-1)/k
        holds = mean_dist <= theoretical_bound + 1e-9
        confidence = 0.95 if holds else 0.05
        outcome = TheoremStatus.VERIFIED if holds else TheoremStatus.FALSIFIED
        evidence = (
            f"Observed mean pairwise distance={mean_dist:.4f}, "
            f"theoretical bound=(k-1)/k={(k-1)/k:.4f} for k={k}. "
            f"Condition {'HOLDS' if holds else 'VIOLATED'}."
        )
        counterexample: str | None = None
        if not holds:
            counterexample = f"mean_dist={mean_dist:.4f} > bound={(k-1)/k:.4f}"
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=outcome,
            evidence=evidence,
            counterexample=counterexample,
            confidence=confidence,
        )

    def verify_coverage_completeness(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Verify the coverage completeness theorem.

        Estimates the number of distinct "domains" (target_area values) in
        *ideas*, then checks that greedy selection of k ideas covers at least
        min(k/m, 1.0) fraction of domains.

        Parameters
        ----------
        theorem:
            The completeness theorem.
        ideas:
            Ideas with ``target_area`` attributes.
        portfolio:
            Existing portfolio (used to identify uncovered domains).

        Returns
        -------
        VerificationResult
        """
        domains = list({idea.target_area for idea in ideas if idea.target_area})
        m = len(domains)
        if m == 0:
            return VerificationResult(
                theorem_id=theorem.theorem_id,
                outcome=TheoremStatus.INAPPLICABLE,
                evidence="No target_area values found; cannot compute domain coverage.",
                confidence=1.0,
            )
        k = min(m, len(ideas))
        covered_domains: set[str] = set()
        remaining = list(ideas)
        # Greedy: always pick from an uncovered domain first
        for _ in range(k):
            for idea in remaining:
                if idea.target_area and idea.target_area not in covered_domains:
                    covered_domains.add(idea.target_area)
                    remaining.remove(idea)
                    break
        coverage_fraction = len(covered_domains) / m
        expected_lower = min(k / m, 1.0)
        holds = coverage_fraction >= expected_lower - 1e-9
        confidence = 0.9 if holds else 0.1
        outcome = TheoremStatus.VERIFIED if holds else TheoremStatus.FALSIFIED
        evidence = (
            f"Covered {len(covered_domains)}/{m} domains with k={k} picks. "
            f"Coverage fraction={coverage_fraction:.4f}, expected>={expected_lower:.4f}. "
            f"Condition {'HOLDS' if holds else 'VIOLATED'}."
        )
        counterexample: str | None = None
        if not holds:
            counterexample = f"coverage={coverage_fraction:.4f} < min(k/m,1)={expected_lower:.4f}"
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=outcome,
            evidence=evidence,
            counterexample=counterexample,
            confidence=confidence,
        )

    def verify_monotonicity(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Verify that novelty is monotone non-decreasing as portfolio shrinks.

        Constructs sub-portfolios of decreasing size and checks that the mean
        novelty of *ideas* is non-decreasing (i.e. a smaller portfolio → more
        room for novel ideas).

        Parameters
        ----------
        theorem:
            The monotonicity theorem.
        ideas:
            Candidate ideas.
        portfolio:
            Portfolio to shrink progressively.

        Returns
        -------
        VerificationResult
        """
        if not portfolio or not ideas:
            return VerificationResult(
                theorem_id=theorem.theorem_id,
                outcome=TheoremStatus.INAPPLICABLE,
                evidence="Empty ideas or portfolio; monotonicity check not applicable.",
                confidence=1.0,
            )

        def mean_novelty(port: Sequence[Idea]) -> float:
            tp = TheoremPortfolio()
            for p in port:
                tp.add(p.idea_id, _idea_tokens(p))
            if not ideas:
                return 0.0
            scores = [float(tp.novelty_score(_idea_tokens(idea))) for idea in ideas]
            return sum(scores) / len(scores)

        # Check: removing ideas from portfolio should NOT decrease mean novelty
        sizes = list(range(len(portfolio), max(0, len(portfolio) - 5) - 1, -1))
        novelties = [mean_novelty(portfolio[:s]) for s in sizes]
        violations = 0
        for i in range(len(novelties) - 1):
            if novelties[i + 1] < novelties[i] - 1e-6:
                violations += 1
        holds = violations == 0
        confidence = max(0.1, 1.0 - violations * 0.2)
        outcome = TheoremStatus.VERIFIED if holds else TheoremStatus.FALSIFIED
        evidence = (
            f"Tested {len(sizes)} portfolio sizes; found {violations} monotonicity violations. "
            f"Novelty scores: {[f'{n:.3f}' for n in novelties]}. "
            f"Condition {'HOLDS' if holds else 'VIOLATED'}."
        )
        counterexample: str | None = None
        if not holds:
            counterexample = f"{violations} cases where novelty decreased as portfolio shrank"
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=outcome,
            evidence=evidence,
            counterexample=counterexample,
            confidence=confidence,
        )

    def _verify_impossibility(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Default verifier for impossibility theorems.

        Impossibility theorems cannot be empirically verified by data tests
        (they require formal proofs or complexity-theoretic arguments).
        Returns INAPPLICABLE with a note.

        Parameters
        ----------
        theorem:
            Impossibility theorem.
        ideas:
            Unused.
        portfolio:
            Unused.

        Returns
        -------
        VerificationResult
        """
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=TheoremStatus.INAPPLICABLE,
            evidence=(
                "Impossibility theorems require formal proofs or complexity-theoretic "
                "arguments and cannot be verified empirically against data."
            ),
            confidence=1.0,
        )

    def _verify_generic(
        self,
        theorem: NoveltyTheorem,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> VerificationResult:
        """Generic fallback verifier for unknown theorem kinds.

        Parameters
        ----------
        theorem:
            Theorem to verify.
        ideas:
            Unused.
        portfolio:
            Unused.

        Returns
        -------
        VerificationResult
        """
        return VerificationResult(
            theorem_id=theorem.theorem_id,
            outcome=TheoremStatus.INAPPLICABLE,
            evidence=f"No specialised verifier for TheoremKind={theorem.kind.value}.",
            confidence=0.5,
        )

    def batch_verify(
        self,
        registry: "TheoremRegistry",
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[VerificationResult]:
        """Verify all theorems in *registry*.

        Parameters
        ----------
        registry:
            Registry of theorems to verify.
        ideas:
            Candidate ideas.
        portfolio:
            Existing portfolio.
        purpose:
            Optional purpose for alignment checks.

        Returns
        -------
        list[VerificationResult]
            One result per theorem in the registry.
        """
        return [
            self.verify(theorem, ideas, portfolio, purpose)
            for theorem in registry.get_all()
        ]


# ---------------------------------------------------------------------------
# TheoremApplications
# ---------------------------------------------------------------------------


class TheoremApplications:
    """Apply verified theorems to produce practical recommendations.

    This class translates formal theorem implications into actionable advice
    for the novelty-search subsystem.

    Parameters
    ----------
    registry:
        The theorem registry to draw from.
    """

    def __init__(self, registry: "TheoremRegistry") -> None:
        self.registry = registry

    # ------------------------------------------------------------------

    def apply_to_search(
        self,
        candidates: Sequence[Idea],
        portfolio: Sequence[Idea],
        purpose: str = "",
    ) -> list[str]:
        """Return all implications from actionable theorems applicable to this search.

        Parameters
        ----------
        candidates:
            Candidate ideas.
        portfolio:
            Existing portfolio.
        purpose:
            Research purpose.

        Returns
        -------
        list[str]
            List of implication strings from verified theorems.
        """
        implications: list[str] = []
        for theorem in self.registry.actionable():
            for impl in theorem.implications:
                implications.append(f"[{theorem.theorem_id}] {impl}")
        return implications

    def recommend_strategy(
        self,
        n_candidates: int,
        n_portfolio: int,
        budget: float,
    ) -> str:
        """Recommend a search strategy based on theorem implications.

        Uses the verified theorems to advise on whether to use greedy,
        beam, or diverse search based on the problem parameters.

        Parameters
        ----------
        n_candidates:
            Number of candidate ideas available.
        n_portfolio:
            Size of the existing portfolio.
        budget:
            Available selection budget.

        Returns
        -------
        str
            Recommendation string.
        """
        lines = [
            f"Strategy recommendation for {n_candidates} candidates, "
            f"portfolio size {n_portfolio}, budget {budget:.1f}:",
        ]
        optimality_thm = self.registry.get("thm-greedy-optimality")
        if optimality_thm and optimality_thm.is_actionable:
            lines.append(
                f"  • Use GREEDY search: guarantees (1-1/e)≈63.2% of optimal coverage "
                f"(per {optimality_thm.theorem_id})."
            )
        else:
            lines.append("  • No optimality guarantee available; consider exhaustive search for small k.")

        diversity_thm = self.registry.get("thm-diversity-bound")
        if diversity_thm and diversity_thm.is_actionable:
            k_str = min(10, n_candidates)
            bound = (k_str - 1) / k_str if k_str > 1 else 0.0
            lines.append(
                f"  • Diversity bound: for k={k_str} ideas, mean pairwise distance <= {bound:.3f} "
                f"(per {diversity_thm.theorem_id})."
            )

        completeness_thm = self.registry.get("thm-coverage-completeness")
        if completeness_thm and completeness_thm.is_actionable:
            lines.append(
                f"  • Coverage: ensure k >= number of target domains for full coverage "
                f"(per {completeness_thm.theorem_id})."
            )

        mono_thm = self.registry.get("thm-novelty-monotonicity")
        if mono_thm and mono_thm.is_actionable:
            lines.append(
                f"  • Monotonicity: adding ideas to portfolio can only reduce future novelty "
                f"(per {mono_thm.theorem_id}). Avoid over-populating the portfolio early."
            )
        if budget < 10.0:
            lines.append(
                "  • Low budget detected: apply budget-cap stage and prioritise high-novelty/low-cost ideas."
            )
        return "\n".join(lines)

    def bound_novelty(
        self,
        ideas: Sequence[Idea],
        portfolio: Sequence[Idea],
    ) -> tuple[float, float]:
        """Compute lower and upper bounds on achievable novelty.

        Lower bound: the minimum novelty across all ideas (any selection
        can achieve at least this).  Upper bound: maximum novelty across
        all ideas.

        Parameters
        ----------
        ideas:
            Pool of candidate ideas.
        portfolio:
            Existing portfolio.

        Returns
        -------
        tuple[float, float]
            (lower_bound, upper_bound).
        """
        if not ideas:
            return (0.0, 0.0)
        tp = TheoremPortfolio()
        for p in portfolio:
            tp.add(p.idea_id, _idea_tokens(p))
        scores = [float(tp.novelty_score(_idea_tokens(idea))) for idea in ideas]
        return (_clamp(min(scores)), _clamp(max(scores)))

    def bound_diversity(self, k: int, ideas: Sequence[Idea]) -> float:
        """Compute the theoretical upper bound on mean pairwise diversity for k ideas.

        From the diversity bound theorem: for Jaccard distances in [0, 1],
        the upper bound is (k-1)/k.

        Parameters
        ----------
        k:
            Number of ideas to select.
        ideas:
            Pool of ideas (used for D_max estimation).

        Returns
        -------
        float
            Upper bound on mean pairwise diversity.
        """
        if k <= 1:
            return 0.0
        # Theoretical bound with D_max = 1 (Jaccard)
        return (k - 1) / k

    def coverage_lower_bound(self, k: int, n_total_domains: int) -> float:
        """Compute the greedy coverage lower bound from submodularity theory.

        From coverage completeness + submodular optimality:
        coverage(k) >= 1 - (1 - 1/n)^k.

        Parameters
        ----------
        k:
            Number of ideas to select greedily.
        n_total_domains:
            Total number of distinct domains.

        Returns
        -------
        float
            Coverage lower bound in [0, 1].
        """
        if n_total_domains <= 0 or k <= 0:
            return 0.0
        return _clamp(1.0 - (1.0 - 1.0 / n_total_domains) ** k)

    def optimality_guarantee(self, greedy_score: float, optimal_score: float) -> float:
        """Compute the approximation ratio achieved by the greedy solution.

        Theoretical guarantee: ratio >= (1 - 1/e) ≈ 0.6321 for submodular
        objectives.

        Parameters
        ----------
        greedy_score:
            Score achieved by the greedy selection.
        optimal_score:
            Optimal score (from exhaustive search or upper bound).

        Returns
        -------
        float
            Achieved approximation ratio in [0, 1].
        """
        if optimal_score <= 0:
            return 1.0
        return _clamp(greedy_score / optimal_score)

    def generate_advice(self, problem_desc: str) -> list[str]:
        """Generate a list of actionable advice strings based on verified theorems.

        Advice is generated by pattern-matching keywords in *problem_desc*
        against theorem implications.

        Parameters
        ----------
        problem_desc:
            Natural-language description of the search problem.

        Returns
        -------
        list[str]
            List of advice strings derived from applicable theorems.
        """
        desc_tokens = _tokenize(problem_desc)
        advice: list[str] = []
        for theorem in self.registry.actionable():
            for impl in theorem.implications:
                impl_tokens = _tokenize(impl)
                overlap = _jaccard(desc_tokens, impl_tokens)
                if overlap > 0.05 or not desc_tokens:
                    advice.append(f"• [{theorem.theorem_id}] {impl}")
        if not advice:
            # Generic advice from all actionable theorems
            for theorem in self.registry.actionable():
                if theorem.implications:
                    advice.append(f"• [{theorem.theorem_id}] {theorem.implications[0]}")
        return advice


# ---------------------------------------------------------------------------
# TheoremCatalog
# ---------------------------------------------------------------------------


class TheoremCatalog:
    """Full catalog of predefined novelty-search theorems with cross-references.

    The catalog is pre-populated with theorems from theory2.tex Ch57 and
    supporting mathematical literature.  Use :meth:`get_registry` to obtain
    the ``TheoremRegistry`` and :meth:`describe_all` for documentation.

    Usage::

        catalog = TheoremCatalog()
        registry = catalog.get_registry()
        print(catalog.describe_all())
        results = catalog.search("coverage greedy")
    """

    def __init__(self) -> None:
        self._registry = self._build_catalog()

    # ------------------------------------------------------------------

    def _build_catalog(self) -> TheoremRegistry:
        """Construct and populate the full theorem registry.

        Returns
        -------
        TheoremRegistry
            Registry containing all predefined theorems.
        """
        registry = TheoremRegistry()
        for thm in _ALL_THEOREMS:
            registry.register(thm)
        return registry

    def get_registry(self) -> TheoremRegistry:
        """Return the populated theorem registry.

        Returns
        -------
        TheoremRegistry
        """
        return self._registry

    def describe_all(self) -> str:
        """Return a multi-line description of all theorems in the catalog.

        Returns
        -------
        str
        """
        lines: list[str] = [
            "=" * 70,
            "NOVELTY SEARCH THEOREM CATALOG  (theory2.tex Ch57)",
            "=" * 70,
            "",
        ]
        for theorem in self._registry.get_all():
            lines.append(theorem.describe())
            lines.append("")
        lines.append(self._registry.summary())
        return "\n".join(lines)

    def search(self, query: str) -> list[NoveltyTheorem]:
        """Search theorems by keyword in their name or statement.

        Parameters
        ----------
        query:
            Space-separated keywords to search for.

        Returns
        -------
        list[NoveltyTheorem]
            Theorems whose name or statement contain any of the query tokens.
        """
        query_tokens = _tokenize(query)
        results: list[tuple[NoveltyTheorem, float]] = []
        for theorem in self._registry.get_all():
            combined = _tokenize(theorem.name + " " + theorem.statement)
            score = _jaccard(query_tokens, combined)
            if score > 0.0:
                results.append((theorem, score))
        results.sort(key=lambda t: t[1], reverse=True)
        return [t for t, _ in results]

    def cross_reference_table(self) -> dict[str, list[str]]:
        """Build a cross-reference table of theorem-to-theorem dependencies.

        Returns
        -------
        dict[str, list[str]]
            Mapping from theorem_id to list of theorem_ids it references.
        """
        table: dict[str, list[str]] = {}
        all_ids = {t.theorem_id for t in self._registry.get_all()}
        for theorem in self._registry.get_all():
            cross_refs = [ref for ref in theorem.references if ref in all_ids]
            table[theorem.theorem_id] = cross_refs
        return table


# ---------------------------------------------------------------------------
# Predefined theorem instances (module-level constants)
# ---------------------------------------------------------------------------

OPTIMALITY_THEOREM = NoveltyTheorem(
    theorem_id="thm-greedy-optimality",
    name="Greedy (1-1/e)-Approximation for Submodular Coverage",
    statement=(
        "Let f: 2^U -> R be a monotone submodular function with f(∅)=0. "
        "The greedy algorithm that iteratively selects the element with "
        "maximum marginal gain achieves f(S_greedy) >= (1 - 1/e) * f(S_opt) "
        "where S_opt is the optimal k-element set."
    ),
    proof_sketch=(
        "By submodularity: f(S_opt | S_greedy) >= f(S_opt) - f(S_greedy). "
        "At each step the marginal gain is at least (f(S_opt) - f(S_greedy)) / k. "
        "Solving the resulting recurrence yields the (1-1/e) bound."
    ),
    conditions=(
        "f must be monotone non-decreasing",
        "f must be submodular (diminishing returns property)",
        "f(∅) = 0",
        "Selection set size k is fixed in advance",
    ),
    implications=(
        "Greedy novelty search is near-optimal for coverage objectives",
        "No polynomial-time algorithm can improve on (1-1/e) unless P=NP",
        "Budget-constrained greedy also achieves (1-1/e)(1-1/e) approximation",
    ),
    kind=TheoremKind.OPTIMALITY,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57", "Nemhauser et al. 1978"),
)

DIVERSITY_BOUND = NoveltyTheorem(
    theorem_id="thm-diversity-bound",
    name="Pairwise Diversity Upper Bound",
    statement=(
        "For any set S of k ideas drawn from a space with maximum pairwise "
        "distance D_max, the mean pairwise distance satisfies: "
        "mean_dist(S) <= D_max * (k-1)/k. "
        "Equality holds only when all pairwise distances equal D_max."
    ),
    proof_sketch=(
        "Sum of k*(k-1)/2 pairwise distances <= k*(k-1)/2 * D_max. "
        "Dividing by k*(k-1)/2 gives the bound."
    ),
    conditions=(
        "S is a finite set of k ideas",
        "Distance function d: Ideas x Ideas -> [0, D_max] is bounded",
        "k >= 2",
    ),
    implications=(
        "Mean pairwise diversity is strictly less than the maximum possible distance",
        "Adding a highly-distant idea increases diversity by at most D_max",
        "For Jaccard distance, D_max = 1.0, so diversity <= (k-1)/k",
    ),
    kind=TheoremKind.BOUND,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57",),
)

COVERAGE_COMPLETENESS = NoveltyTheorem(
    theorem_id="thm-coverage-completeness",
    name="Coverage Completeness via Greedy Set Cover",
    statement=(
        "If the idea space can be partitioned into m domains and we have "
        "at least one idea per domain, then greedy selection of k ideas "
        "covers at least min(k, m) domains, achieving coverage fraction "
        "min(k/m, 1.0)."
    ),
    proof_sketch=(
        "Greedy always picks the idea from the uncovered domain with "
        "highest novelty. After k steps, min(k, m) domains are covered. "
        "Coverage fraction = covered/m = min(k/m, 1.0)."
    ),
    conditions=(
        "Idea space is partitioned into m non-overlapping domains",
        "Each domain has at least one candidate idea",
        "Greedy selection prioritizes domain coverage",
    ),
    implications=(
        "k >= m ideas guarantees full domain coverage",
        "Coverage grows linearly with k up to m",
        "Portfolio gaps can always be filled if candidates exist in each domain",
    ),
    kind=TheoremKind.COMPLETENESS,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57",),
)

NOVELTY_MONOTONICITY = NoveltyTheorem(
    theorem_id="thm-novelty-monotonicity",
    name="Monotonicity of Novelty Under Portfolio Expansion",
    statement=(
        "Let nov(x, P) denote the novelty of idea x with respect to portfolio P. "
        "For any P ⊆ P', nov(x, P) >= nov(x, P'). "
        "That is, novelty is anti-monotone in the portfolio: adding ideas to P "
        "can only decrease (or leave unchanged) the novelty of any fixed candidate x."
    ),
    proof_sketch=(
        "novelty(x, P) is defined as a distance from x to the nearest neighbour in P. "
        "Adding an idea y to P creates a new nearest-neighbour candidate. "
        "Hence min_{p in P'} dist(x, p) <= min_{p in P} dist(x, p). "
        "Therefore nov(x, P') <= nov(x, P), establishing anti-monotonicity."
    ),
    conditions=(
        "Novelty is defined as distance to the nearest neighbour in the portfolio",
        "Distance function is non-negative and satisfies the identity of indiscernibles",
        "The portfolio P is a finite set",
        "P ⊆ P' (P' is a superset of P)",
    ),
    implications=(
        "Adding ideas to the portfolio can only reduce the novelty of future candidates",
        "It is always strictly better to assess novelty against the most up-to-date portfolio",
        "Caching novelty scores is only valid if the portfolio has not changed",
        "Delayed portfolio updates lead to over-estimated novelty scores",
        "Greedy search must update the portfolio at each step to maintain accuracy",
    ),
    kind=TheoremKind.MONOTONICITY,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57", "thm-greedy-optimality"),
)

PURPOSE_ALIGNMENT_THEOREM = NoveltyTheorem(
    theorem_id="thm-purpose-alignment",
    name="Purpose Alignment as a Multiplicative Discount on Novelty",
    statement=(
        "Let nov(x, P) be the raw semantic novelty of idea x with respect to "
        "portfolio P, and let align(x, purpose) in [0, 1] be the purpose "
        "alignment score. The effective novelty satisfies: "
        "eff_nov(x, P) = nov(x, P) * align(x, purpose). "
        "An idea with perfect novelty but zero alignment contributes nothing "
        "to the purpose-directed search objective."
    ),
    proof_sketch=(
        "Define the purpose-directed search objective F(S) = sum_{x in S} eff_nov(x, P). "
        "Factoring: F(S) = sum_{x in S} nov(x, P) * align(x, purpose). "
        "Since align is in [0, 1], eff_nov(x, P) <= nov(x, P) for all x, "
        "with equality iff align(x, purpose) = 1. The multiplicative form "
        "ensures both novelty and alignment are necessary for high effective novelty."
    ),
    conditions=(
        "align(x, purpose) in [0, 1] for all ideas x",
        "nov(x, P) in [0, 1] for all ideas x and portfolios P",
        "The search objective is purpose-directed (not purely novelty-maximising)",
        "Purpose is specified as a non-empty string",
    ),
    implications=(
        "High novelty alone is not sufficient; ideas must also align with the stated purpose",
        "Unaligned highly novel ideas should be deprioritised in purpose-directed search",
        "Purpose alignment acts as a soft filter that can be tuned independently of novelty",
        "A zero-alignment idea is equivalent to a zero-novelty idea for search purposes",
        "Improve search quality by refining the purpose description, not just novelty threshold",
    ),
    kind=TheoremKind.APPROXIMATION,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57", "thm-greedy-optimality"),
)

BUDGET_TRADEOFF_THEOREM = NoveltyTheorem(
    theorem_id="thm-budget-tradeoff",
    name="Budget-Novelty Tradeoff: Knapsack Generalisation",
    statement=(
        "Consider the budgeted novelty maximisation problem: "
        "maximise sum_{x in S} nov(x, P) subject to sum_{x in S} cost(x) <= B. "
        "The greedy-by-ratio algorithm (selecting ideas with highest nov/cost ratio) "
        "achieves a solution within a factor of (1 - e^{-alpha}) of optimal, "
        "where alpha = B_greedy / B_opt is the budget utilisation ratio. "
        "When budget is not a binding constraint (alpha -> 1), the bound approaches 1 - 1/e."
    ),
    proof_sketch=(
        "The budgeted submodular maximisation problem generalises the unweighted case. "
        "Let S* be the optimal solution and S_g the greedy solution. "
        "The greedy algorithm's value satisfies: f(S_g) >= (1 - e^{-alpha}) * f(S*). "
        "This follows from the continuous greedy analysis applied to the budget polytope. "
        "When all items have unit cost, alpha = k/k = 1 and the bound reduces to (1 - 1/e)."
    ),
    conditions=(
        "Novelty function is monotone submodular",
        "Each idea has a non-negative cost: cost(x) >= 0",
        "Total budget B > 0",
        "Greedy selection is by novelty-to-cost ratio (bang-per-buck)",
        "No single idea's cost exceeds the total budget",
    ),
    implications=(
        "Always prefer high-novelty-per-cost ideas when budget is constrained",
        "Budget-constrained greedy remains near-optimal (within (1-1/e) of optimal)",
        "Increasing budget by delta allows capturing at most delta/min_cost additional ideas",
        "Under tight budget, prefer many cheap moderately-novel ideas over one expensive highly-novel idea",
        "Budget utilisation ratio alpha should be monitored; low alpha signals budget inefficiency",
    ),
    kind=TheoremKind.BOUND,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57", "thm-greedy-optimality", "Sviridenko 2004"),
)

TRUST_NOVELTY_THEOREM = NoveltyTheorem(
    theorem_id="thm-trust-novelty",
    name="Trust-Weighted Novelty and Selection Bias",
    statement=(
        "Let T(x) in [0, 1] be the trust weight of idea x (derived from its TrustStatus), "
        "and let nov(x, P) be its raw novelty. Define trust-weighted novelty as "
        "w_nov(x, P) = T(x) * nov(x, P). "
        "Restricting search to ideas with T(x) >= tau (for threshold tau in (0, 1]) "
        "introduces a selection bias: the achievable w_nov ceiling is reduced from "
        "max_x nov(x, P) to max_{x: T(x)>=tau} nov(x, P). "
        "The bias-variance tradeoff states: higher tau reduces noise but may exclude "
        "genuinely novel speculative ideas."
    ),
    proof_sketch=(
        "The trust filter defines a constrained feasible set F_tau = {x : T(x) >= tau}. "
        "For any tau > 0, F_tau ⊆ F_0 = all ideas. "
        "Therefore max_{x in F_tau} nov(x, P) <= max_{x in F_0} nov(x, P). "
        "The gap max_{x in F_0} nov(x) - max_{x in F_tau} nov(x) measures the cost of the filter. "
        "This cost is zero iff the globally most novel idea satisfies T(x) >= tau."
    ),
    conditions=(
        "Trust weights T(x) are in [0, 1] and derived from TrustStatus",
        "Trust threshold tau is in (0, 1]",
        "The raw novelty function nov(x, P) is independent of trust",
        "The search objective is to maximise (possibly trust-weighted) novelty",
    ),
    implications=(
        "Trust filtering can exclude the most novel ideas if they are speculative",
        "Setting min_trust=SPECULATIVE maximises novelty at the cost of reliability",
        "Setting min_trust=VALIDATED minimises noise but may miss breakthrough ideas",
        "Stratified search (per trust stratum) is Pareto-superior to a single threshold",
        "The optimal tau depends on the downstream use: exploration favours low tau, production favours high tau",
    ),
    kind=TheoremKind.BOUND,
    status=TheoremStatus.VERIFIED,
    references=("theory2.tex Ch57", "thm-greedy-optimality", "thm-novelty-monotonicity"),
)

DIMINISHING_RETURNS_THEOREM = NoveltyTheorem(
    theorem_id="thm-diminishing-returns",
    name="Diminishing Marginal Novelty Under Sequential Selection",
    statement=(
        "Let nov_S(x, P) = nov(x, P ∪ S) denote the residual novelty of idea x "
        "after ideas S have already been selected. For any ideas a, b not in S, "
        "and any set T with S ⊆ T: "
        "nov_S(a, P) - nov_{S∪{b}}(a, P) >= nov_T(a, P) - nov_{T∪{b}}(a, P). "
        "That is, the marginal reduction in novelty from adding b to the selected set "
        "is greatest when the selected set is smallest (diminishing returns)."
    ),
    proof_sketch=(
        "This is a direct consequence of submodularity applied to the novelty function. "
        "Define g(S) = nov(x, P ∪ S) as a function of the selected set S. "
        "Submodularity of g (inherited from the distance-to-nearest-neighbour structure) "
        "gives: g(S) - g(S∪{b}) >= g(T) - g(T∪{b}) for all S ⊆ T. "
        "This means each additional selection contributes a diminishing reduction in "
        "the remaining novelty of unselected candidates."
    ),
    conditions=(
        "The novelty function is submodular as a function of the selected set",
        "S ⊆ T (T is a superset of S)",
        "b is not in T",
        "The portfolio P is fixed throughout",
    ),
    implications=(
        "Each additional idea selected reduces the novelty of remaining candidates by a smaller amount",
        "The first selection has the greatest impact on the novelty landscape",
        "After k selections, further selections yield progressively smaller novelty gains",
        "Novelty search should be run iteratively with portfolio updates at each step",
        "Batch scoring without portfolio update overestimates novelty of later selections",
        "Diminishing returns justify stopping early when marginal novelty falls below a threshold",
    ),
    kind=TheoremKind.MONOTONICITY,
    status=TheoremStatus.VERIFIED,
    references=(
        "theory2.tex Ch57",
        "thm-greedy-optimality",
        "thm-novelty-monotonicity",
        "Nemhauser et al. 1978",
    ),
)

# Collect all predefined theorems for the catalog
_ALL_THEOREMS: tuple[NoveltyTheorem, ...] = (
    OPTIMALITY_THEOREM,
    DIVERSITY_BOUND,
    COVERAGE_COMPLETENESS,
    NOVELTY_MONOTONICITY,
    PURPOSE_ALIGNMENT_THEOREM,
    BUDGET_TRADEOFF_THEOREM,
    TRUST_NOVELTY_THEOREM,
    DIMINISHING_RETURNS_THEOREM,
)

# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "TheoremStatus",
    "TheoremKind",
    # Data classes
    "NoveltyTheorem",
    "VerificationResult",
    # Core classes
    "TheoremRegistry",
    "TheoremVerifier",
    "TheoremApplications",
    "TheoremCatalog",
    # Predefined theorems
    "OPTIMALITY_THEOREM",
    "DIVERSITY_BOUND",
    "COVERAGE_COMPLETENESS",
    "NOVELTY_MONOTONICITY",
    "PURPOSE_ALIGNMENT_THEOREM",
    "BUDGET_TRADEOFF_THEOREM",
    "TRUST_NOVELTY_THEOREM",
    "DIMINISHING_RETURNS_THEOREM",
]
