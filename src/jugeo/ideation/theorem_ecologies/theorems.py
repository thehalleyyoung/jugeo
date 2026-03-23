"""Formal theorems about theorem ecologies (theory2.tex Ch61 §5).

Module layout::

    EcologyTheorem       – formal theorem dataclass
    TheoremRegistry      – registry of ecology theorems
    TheoremVerifier      – verifies theorem conditions
    TheoremApplications  – applies theorems to ecologies
    TheoremCatalog       – catalog of all built-in theorems

Theoretical background (theory2.tex Ch61 §5)
---------------------------------------------
This module encodes the formal mathematical theorems that govern the
structure and behaviour of theorem ecologies.  The theorems are derived
from the abstract theory of *knowledge ecology* — a field that draws on
dynamical systems theory, information theory, and combinatorial optimisation
to characterise the long-run properties of collections of interdependent
mathematical results.

Each theorem has the standard hypothetico-deductive form:

    Given hypothesis H, under type-theoretic conditions C,
    conclusion Γ holds.

The ``hypothesis`` and ``conclusion`` fields in :class:`EcologyTheorem`
store the mathematical content as structured strings.  The
:class:`TheoremVerifier` parses keyword patterns from the hypothesis to
determine whether a given ecology satisfies the preconditions.

The catalogue covers eight core results:

1. **Ecology Stability** (§5.1) — Connectedness + health ≥ 0.5 implies
   stability under the logistic health ODE.
2. **Compounding Convergence** (§5.2) — Sufficient synergy strength implies
   convergent compounding effects.
3. **Portfolio Optimality** (§5.3) — The greedy set-cover heuristic achieves
   at least (1 − 1/e) of optimal coverage.
4. **Diversity Benefit** (§5.4) — High diversity (entropy > 0.7) predicts
   strictly higher equilibrium health.
5. **Lemma Reuse Theorem** (§5.5) — Frequently reused lemmas have above-median
   utility scores.
6. **Dependency Depth Bound** (§5.6) — The optimal dependency depth is bounded
   by O(log n) where n is the node count.
7. **Growth Equilibrium** (§5.7) — Logistic growth dynamics converge to
   a unique equilibrium health H* = 1 − d/r.
8. **Symbiosis Amplification** (§5.8) — Symbiotic ecologies exhibit
   super-linear compounding amplification.

Usage example
-------------
::

    from jugeo.ideation.theorem_ecologies.theorems import CATALOG, TheoremVerifier

    verifier = TheoremVerifier()
    registry = CATALOG.get_registry()
    applicable = registry.applicable_to(my_ecology)
    for thm in applicable:
        print(thm.name, thm.conclusion)
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.ideation.theorem_ecologies.models import (
    TheoremEcology, LemmaPortfolio, CompoundingEffect,
    EcologicalDynamic, PortfolioOptimization, EcologyHealth, DynamicType,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum health score for the stability theorem hypothesis.
_STABILITY_HEALTH_THRESHOLD: float = 0.5

#: Minimum graph connectivity for stability conditions.
_STABILITY_CONNECTIVITY_THRESHOLD: float = 0.3

#: Minimum synergy strength for the compounding convergence theorem.
_SYNERGY_THRESHOLD: float = 0.4

#: Diversity entropy threshold for the diversity benefit theorem.
_DIVERSITY_ENTROPY_THRESHOLD: float = 0.7

#: Minimum coverage fraction for optimality conditions.
_COVERAGE_THRESHOLD: float = 0.5

#: Epsilon for numerical comparisons.
_EPSILON: float = 1e-12

#: Greedy approximation ratio guarantee: 1 − 1/e.
_GREEDY_APPROX_RATIO: float = 1.0 - math.exp(-1.0)


# ===========================================================================
# Internal helpers
# ===========================================================================

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp with microsecond precision and timezone offset,
        e.g. ``"2024-03-15T09:00:00.000000+00:00"``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [*lo*, *hi*].

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


def _tokenize_lower(text: str) -> set[str]:
    """Split *text* into a set of lowercase alphabetic tokens.

    Parameters
    ----------
    text:
        Input string to tokenize.

    Returns
    -------
    set[str]
        Non-empty lowercase tokens.
    """
    return {t.lower() for t in re.split(r"[^a-zA-Z]+", text) if len(t) >= 2}


def _keyword_match(text: str, keywords: Iterable[str]) -> bool:
    """Return ``True`` iff *text* contains at least one keyword (case-insensitive).

    Parameters
    ----------
    text:
        String to search within.
    keywords:
        Keywords to look for.

    Returns
    -------
    bool
        ``True`` when any keyword appears in *text*.
    """
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _bfs_depth(adj: dict[str, list[str]], roots: list[str]) -> int:
    """Maximum BFS depth from *roots* in *adj*.

    Parameters
    ----------
    adj:
        Adjacency mapping node → successors.
    roots:
        Starting nodes.

    Returns
    -------
    int
        Maximum depth reached (0 if *roots* is empty).
    """
    if not roots:
        return 0
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    for r in roots:
        if r not in visited:
            visited.add(r)
            queue.append((r, 0))
    max_d = 0
    while queue:
        node, depth = queue.popleft()
        max_d = max(max_d, depth)
        for nbr in adj.get(node, []):
            if nbr not in visited:
                visited.add(nbr)
                queue.append((nbr, depth + 1))
    return max_d


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity |A∩B| / |A∪B| for two frozensets.

    Returns
    -------
    float
        Jaccard coefficient in [0.0, 1.0].
    """
    if not a and not b:
        return 0.0
    return len(a & b) / (len(a | b) + _EPSILON)


def _entropy(weights: list[float]) -> float:
    """Normalised Shannon entropy of *weights*.

    Returns
    -------
    float
        Normalised entropy in [0.0, 1.0].
    """
    total = sum(w for w in weights if w > 0.0)
    if total < _EPSILON or len(weights) < 2:
        return 0.0
    probs = [w / total for w in weights if w > 0.0]
    raw = -sum(p * math.log(p) for p in probs if p > 0.0)
    max_entropy = math.log(len(probs))
    return raw / max_entropy if max_entropy > _EPSILON else 0.0


# ===========================================================================
# TheoremStatus
# ===========================================================================

class TheoremStatus(str, Enum):
    """Life-cycle status of a formal theorem.

    Attributes
    ----------
    CONJECTURED:
        The theorem has been stated but not yet formally proved or refuted.
        It is supported by empirical evidence or plausibility arguments.
    VERIFIED:
        The theorem has been formally proved and its proof has been
        peer-reviewed or mechanically checked.
    REFUTED:
        A counterexample or formal refutation has been found.  The theorem
        may be repaired by weakening its hypothesis.
    CONDITIONAL:
        The theorem holds under an additional assumption (e.g., a conjecture
        elsewhere in the theory) that is itself unverified.
    """

    CONJECTURED = "conjectured"
    VERIFIED = "verified"
    REFUTED = "refuted"
    CONDITIONAL = "conditional"


# ===========================================================================
# TheoremType
# ===========================================================================

class TheoremType(str, Enum):
    """Classification of the logical form of a theorem.

    Attributes
    ----------
    STABILITY:
        The theorem asserts that a system (ecology, portfolio, dynamic)
        remains near a known equilibrium under perturbation.
    CONVERGENCE:
        The theorem asserts that a sequence or process converges to a
        well-defined limit.
    OPTIMALITY:
        The theorem asserts that a construction or algorithm achieves an
        optimal or near-optimal objective value.
    EXISTENCE:
        The theorem asserts that a mathematical object with desired
        properties exists.
    UNIQUENESS:
        The theorem asserts that such an object is unique (often paired
        with an existence theorem).
    BOUND:
        The theorem provides a quantitative upper or lower bound on some
        quantity associated with ecologies or portfolios.
    """

    STABILITY = "stability"
    CONVERGENCE = "convergence"
    OPTIMALITY = "optimality"
    EXISTENCE = "existence"
    UNIQUENESS = "uniqueness"
    BOUND = "bound"


# ===========================================================================
# EcologyTheorem
# ===========================================================================

@dataclass(frozen=True, slots=True)
class EcologyTheorem:
    """Formal theorem about theorem ecology structures.

    This is an immutable record encoding one theorem from theory2.tex Ch61 §5.
    The ``hypothesis`` and ``conclusion`` fields are human-readable strings;
    the :class:`TheoremVerifier` parses structural keywords from ``hypothesis``
    to determine applicability.

    Parameters
    ----------
    theorem_id:
        Unique identifier string (e.g. ``"ecology_stability_5_1"``).
    name:
        Short descriptive name (e.g. ``"Ecology Stability Theorem"``).
    statement:
        Full formal statement of the theorem as a single string.
    hypothesis:
        Preconditions that must hold for the theorem to apply.
    conclusion:
        The formal consequence that follows when the hypothesis holds.
    theorem_type:
        Classification of the theorem's logical form.
    status:
        Current verification status.
    proof_sketch:
        Brief description of the proof strategy.
    references:
        Tuple of theory2.tex section references, e.g. ``("Ch61 §5.1",)``.
    tags:
        Tuple of keyword tags for filtering and search.
    created_at:
        ISO-8601 creation timestamp (auto-populated).

    Notes
    -----
    The dataclass is ``frozen=True`` to enforce immutability: use
    :meth:`with_reference` and :meth:`with_tag` to derive new instances
    with additional metadata.
    """

    theorem_id: str
    name: str
    statement: str
    hypothesis: str
    conclusion: str
    theorem_type: TheoremType
    status: TheoremStatus
    proof_sketch: str
    references: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Applicability
    # ------------------------------------------------------------------

    def is_applicable(self, ecology: TheoremEcology) -> bool:
        """Check whether *ecology* satisfies this theorem's hypothesis.

        The check parses structural keywords from the hypothesis string and
        tests them against the ecology's attributes:

        * Keyword ``"connected"`` / ``"connectivity"`` → check that the
          dependency graph has at least :const:`_STABILITY_CONNECTIVITY_THRESHOLD`
          fraction of nodes reachable from the theorem roots.
        * Keyword ``"healthy"`` / ``"health"`` → check that the health score
          is ≥ :const:`_STABILITY_HEALTH_THRESHOLD`.
        * Keyword ``"diverse"`` / ``"diversity"`` → check that the degree
          distribution has normalised entropy ≥ :const:`_DIVERSITY_ENTROPY_THRESHOLD`.
        * Keyword ``"lemma"`` / ``"lemmas"`` → check that at least one lemma
          exists in the ecology.
        * Keyword ``"theorem"`` / ``"theorems"`` → check that at least one
          theorem exists.
        * If none of the above keywords are present, the hypothesis is treated
          as vacuously satisfied (returns ``True``).

        Parameters
        ----------
        ecology:
            The ecology to check.

        Returns
        -------
        bool
            ``True`` iff the hypothesis conditions are met.
        """
        hyp = self.hypothesis.lower()
        conditions_checked = 0
        conditions_met = 0

        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))
        health_obj = getattr(ecology, "health", None)
        health_score = getattr(health_obj, "score", 0.0) if health_obj else 0.0

        # Health check
        if any(k in hyp for k in ("health", "healthy")):
            conditions_checked += 1
            if health_score >= _STABILITY_HEALTH_THRESHOLD:
                conditions_met += 1

        # Connectivity check
        if any(k in hyp for k in ("connect", "connectivity")):
            conditions_checked += 1
            all_nodes = set(theorems + lemmas)
            n = max(1, len(all_nodes))
            from collections import deque as _deque
            visited: set[str] = set()
            q: deque[str] = deque(theorems[:1])
            visited.update(theorems[:1])
            while q:
                node = q.popleft()
                for nbr in deps.get(node, []):
                    if nbr not in visited:
                        visited.add(nbr)
                        q.append(nbr)
            connectivity = len(visited) / n
            if connectivity >= _STABILITY_CONNECTIVITY_THRESHOLD:
                conditions_met += 1

        # Diversity check
        if any(k in hyp for k in ("divers", "entropy")):
            conditions_checked += 1
            degree_seq = [len(v) for v in deps.values()]
            ent = _entropy(degree_seq) if degree_seq else 0.0
            if ent >= _DIVERSITY_ENTROPY_THRESHOLD:
                conditions_met += 1

        # Lemma existence
        if any(k in hyp for k in ("lemma", "lemmas")):
            conditions_checked += 1
            if lemmas:
                conditions_met += 1

        # Theorem existence
        if any(k in hyp for k in ("theorem", "theorems", "node")):
            conditions_checked += 1
            if theorems:
                conditions_met += 1

        # Synergy / compounding
        if any(k in hyp for k in ("synerg", "compound", "amplif")):
            conditions_checked += 1
            # Synergy present iff ecology has both theorems and lemmas with deps
            if theorems and lemmas and deps:
                conditions_met += 1

        # Vacuously true
        if conditions_checked == 0:
            return True
        return conditions_met == conditions_checked

    def applies_to_portfolio(self, portfolio: LemmaPortfolio) -> bool:
        """Check whether *portfolio* satisfies this theorem's hypothesis.

        Uses keyword-based checks analogous to :meth:`is_applicable` but
        adapted to portfolio attributes:

        * ``"coverage"`` → coverage ≥ :const:`_COVERAGE_THRESHOLD`
        * ``"lemma"`` / ``"lemmas"`` → portfolio has at least one lemma
        * ``"utility"`` → at least one lemma has above-average utility

        Parameters
        ----------
        portfolio:
            The lemma portfolio to check.

        Returns
        -------
        bool
            ``True`` iff the hypothesis conditions are met.
        """
        hyp = self.hypothesis.lower()
        lemmas = list(getattr(portfolio, "lemma_ids", []))
        coverage = float(getattr(portfolio, "coverage", 0.0))
        utility_scores: dict[str, float] = dict(getattr(portfolio, "utility_scores", {}))
        conditions_checked = 0
        conditions_met = 0

        if "coverage" in hyp:
            conditions_checked += 1
            if coverage >= _COVERAGE_THRESHOLD:
                conditions_met += 1

        if any(k in hyp for k in ("lemma", "lemmas")):
            conditions_checked += 1
            if lemmas:
                conditions_met += 1

        if "utility" in hyp:
            conditions_checked += 1
            if utility_scores:
                mean_util = sum(utility_scores.values()) / len(utility_scores)
                if any(v > mean_util for v in utility_scores.values()):
                    conditions_met += 1

        if conditions_checked == 0:
            return True
        return conditions_met == conditions_checked

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def strength(self) -> float:
        """Compute a numerical strength score for this theorem.

        The strength is a heuristic combination of status and theorem type:

        * Verified theorems receive a base score of 1.0.
        * Conditional theorems receive 0.7.
        * Conjectured theorems receive 0.4.
        * Refuted theorems receive 0.0.

        The base score is then multiplied by a type multiplier:

        * OPTIMALITY and BOUND theorems carry a x1.0 multiplier (most useful
          for practical guidance).
        * STABILITY and CONVERGENCE theorems carry x0.9.
        * EXISTENCE and UNIQUENESS theorems carry x0.8.

        Returns
        -------
        float
            Strength score in [0.0, 1.0].
        """
        status_weight: dict[TheoremStatus, float] = {
            TheoremStatus.VERIFIED: 1.0,
            TheoremStatus.CONDITIONAL: 0.7,
            TheoremStatus.CONJECTURED: 0.4,
            TheoremStatus.REFUTED: 0.0,
        }
        type_multiplier: dict[TheoremType, float] = {
            TheoremType.OPTIMALITY: 1.0,
            TheoremType.BOUND: 1.0,
            TheoremType.STABILITY: 0.9,
            TheoremType.CONVERGENCE: 0.9,
            TheoremType.EXISTENCE: 0.8,
            TheoremType.UNIQUENESS: 0.8,
        }
        base = status_weight.get(self.status, 0.5)
        mult = type_multiplier.get(self.theorem_type, 0.85)
        return _clamp(base * mult)

    def tag_set(self) -> frozenset[str]:
        """Return the tags as a frozenset for set operations.

        Returns
        -------
        frozenset[str]
            The ``tags`` tuple as a frozenset.
        """
        return frozenset(self.tags)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def cite(self) -> str:
        """Return a formatted citation string for this theorem.

        Combines the theorem name, status, and theory2.tex references into
        a compact citation.

        Returns
        -------
        str
            E.g. ``"Ecology Stability Theorem [verified] (Ch61 §5.1)"``
        """
        ref_str = ", ".join(self.references) if self.references else "no reference"
        return f"{self.name} [{self.status.value}] ({ref_str})"

    # ------------------------------------------------------------------
    # Immutable derivation
    # ------------------------------------------------------------------

    def with_reference(self, ref: str) -> "EcologyTheorem":
        """Return a copy of this theorem with *ref* appended to references.

        Parameters
        ----------
        ref:
            New reference string to add (e.g. ``"Ch62 §1.3"``).

        Returns
        -------
        EcologyTheorem
            New theorem instance with the extended references tuple.
        """
        return replace(self, references=self.references + (ref,))

    def with_tag(self, tag: str) -> "EcologyTheorem":
        """Return a copy of this theorem with *tag* appended to tags.

        Parameters
        ----------
        tag:
            New tag string (e.g. ``"stability"`` or ``"portfolio"``).

        Returns
        -------
        EcologyTheorem
            New theorem instance with the extended tags tuple.
        """
        return replace(self, tags=self.tags + (tag,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields represented as JSON-serialisable values.
            Enum fields are stored as their string values.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "hypothesis": self.hypothesis,
            "conclusion": self.conclusion,
            "theorem_type": self.theorem_type.value,
            "status": self.status.value,
            "proof_sketch": self.proof_sketch,
            "references": list(self.references),
            "tags": list(self.tags),
            "created_at": self.created_at,
            "strength": self.strength(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EcologyTheorem":
        """Deserialise an :class:`EcologyTheorem` from a dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        EcologyTheorem
            The reconstructed theorem instance.

        Raises
        ------
        KeyError
            When a required field is missing from *data*.
        ValueError
            When an enum field has an unrecognised value.
        """
        return cls(
            theorem_id=data["theorem_id"],
            name=data["name"],
            statement=data["statement"],
            hypothesis=data["hypothesis"],
            conclusion=data["conclusion"],
            theorem_type=TheoremType(data["theorem_type"]),
            status=TheoremStatus(data["status"]),
            proof_sketch=data["proof_sketch"],
            references=tuple(data.get("references", [])),
            tags=tuple(data.get("tags", [])),
            created_at=data.get("created_at", _now_iso()),
        )


# ===========================================================================
# TheoremRegistry
# ===========================================================================

class TheoremRegistry:
    """Registry of :class:`EcologyTheorem` instances.

    Acts as a queryable in-memory store for theorem objects.  Supports
    lookup by ID, type, status, and tag, as well as applicability filtering
    against concrete ecology instances.

    Usage
    -----
    ::

        registry = TheoremRegistry()
        registry.register(thm)
        applicable = registry.applicable_to(ecology)
    """

    def __init__(self) -> None:
        self._theorems: dict[str, EcologyTheorem] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, theorem: EcologyTheorem) -> None:
        """Register *theorem* in the registry.

        If a theorem with the same ``theorem_id`` already exists, it is
        overwritten with *theorem*.

        Parameters
        ----------
        theorem:
            Theorem to register.
        """
        self._theorems[theorem.theorem_id] = theorem

    def register_batch(self, theorems: list[EcologyTheorem]) -> int:
        """Register multiple theorems at once.

        Parameters
        ----------
        theorems:
            List of theorems to register.

        Returns
        -------
        int
            The number of theorems registered (equal to ``len(theorems)``).
        """
        for t in theorems:
            self.register(t)
        return len(theorems)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, theorem_id: str) -> EcologyTheorem | None:
        """Return the theorem with *theorem_id*, or ``None`` if absent.

        Parameters
        ----------
        theorem_id:
            Unique identifier of the theorem.

        Returns
        -------
        EcologyTheorem or None
        """
        return self._theorems.get(theorem_id)

    def require(self, theorem_id: str) -> EcologyTheorem:
        """Return the theorem with *theorem_id*, raising ``KeyError`` if absent.

        Parameters
        ----------
        theorem_id:
            Unique identifier of the theorem.

        Returns
        -------
        EcologyTheorem
            The registered theorem.

        Raises
        ------
        KeyError
            When *theorem_id* is not found.
        """
        if theorem_id not in self._theorems:
            raise KeyError(f"Theorem '{theorem_id}' not found in registry.")
        return self._theorems[theorem_id]

    def by_type(self, t: TheoremType) -> list[EcologyTheorem]:
        """Return all theorems of type *t*.

        Parameters
        ----------
        t:
            Target theorem type.

        Returns
        -------
        list[EcologyTheorem]
            All matching theorems, sorted by strength descending.
        """
        result = [thm for thm in self._theorems.values() if thm.theorem_type == t]
        result.sort(key=lambda x: x.strength(), reverse=True)
        return result

    def by_status(self, s: TheoremStatus) -> list[EcologyTheorem]:
        """Return all theorems with status *s*.

        Parameters
        ----------
        s:
            Target status.

        Returns
        -------
        list[EcologyTheorem]
            Matching theorems sorted by strength descending.
        """
        result = [thm for thm in self._theorems.values() if thm.status == s]
        result.sort(key=lambda x: x.strength(), reverse=True)
        return result

    def by_tag(self, tag: str) -> list[EcologyTheorem]:
        """Return all theorems tagged with *tag*.

        Parameters
        ----------
        tag:
            Tag string to search for.

        Returns
        -------
        list[EcologyTheorem]
            Matching theorems.
        """
        tag_lower = tag.lower()
        return [
            thm for thm in self._theorems.values()
            if any(t.lower() == tag_lower for t in thm.tags)
        ]

    def applicable_to(self, ecology: TheoremEcology) -> list[EcologyTheorem]:
        """Return all theorems applicable to *ecology*.

        A theorem is applicable when :meth:`EcologyTheorem.is_applicable`
        returns ``True`` and the theorem status is not :attr:`TheoremStatus.REFUTED`.

        Parameters
        ----------
        ecology:
            The ecology to test against.

        Returns
        -------
        list[EcologyTheorem]
            Applicable theorems sorted by strength descending.
        """
        result = [
            thm for thm in self._theorems.values()
            if thm.status != TheoremStatus.REFUTED
            and thm.is_applicable(ecology)
        ]
        result.sort(key=lambda x: x.strength(), reverse=True)
        return result

    def all_theorems(self) -> list[EcologyTheorem]:
        """Return all registered theorems in registration order.

        Returns
        -------
        list[EcologyTheorem]
        """
        return list(self._theorems.values())

    def count(self) -> int:
        """Return the number of registered theorems.

        Returns
        -------
        int
        """
        return len(self._theorems)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the registry to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Keys: ``count``, ``theorems`` (list of theorem dicts).
        """
        return {
            "count": len(self._theorems),
            "theorems": [t.to_dict() for t in self._theorems.values()],
        }


# ===========================================================================
# TheoremVerifier
# ===========================================================================

class TheoremVerifier:
    """Verifies theorem conditions against concrete ecology instances.

    The verifier provides two main services:

    1. **Binary verification**: does a given ecology satisfy the theorem's
       hypothesis?  (Delegates to :meth:`EcologyTheorem.is_applicable` with
       additional cross-checks.)
    2. **Fuzzy satisfaction**: to what degree does an ecology satisfy the
       hypothesis, returning a float in [0, 1]?

    The fuzzy satisfaction measure is useful for partial-hypothesis reasoning:
    e.g., an ecology with health 0.45 (just below the 0.5 threshold) still
    has a satisfaction degree of 0.45/0.5 = 0.9 for the health condition.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Binary verification
    # ------------------------------------------------------------------

    def verify(self, theorem: EcologyTheorem, ecology: TheoremEcology) -> bool:
        """Return ``True`` iff *ecology* satisfies *theorem*'s hypothesis.

        Runs :meth:`EcologyTheorem.is_applicable` then performs additional
        type-specific checks based on :attr:`~EcologyTheorem.theorem_type`:

        * ``STABILITY`` → :meth:`check_stability_conditions`
        * ``CONVERGENCE`` → :meth:`check_convergence_conditions`

        Parameters
        ----------
        theorem:
            Theorem to verify against.
        ecology:
            Ecology to test.

        Returns
        -------
        bool
            Combined verification result.
        """
        if not theorem.is_applicable(ecology):
            return False
        if theorem.theorem_type == TheoremType.STABILITY:
            return self.check_stability_conditions(ecology)
        elif theorem.theorem_type == TheoremType.CONVERGENCE:
            return self.check_convergence_conditions(ecology)
        return True

    def verify_batch(
        self,
        theorem: EcologyTheorem,
        ecologies: list[TheoremEcology],
    ) -> dict[str, bool]:
        """Verify *theorem* against each ecology in *ecologies*.

        Parameters
        ----------
        theorem:
            Theorem to verify.
        ecologies:
            List of ecologies to test.

        Returns
        -------
        dict[str, bool]
            Mapping ecology_id → verification result.
        """
        return {
            getattr(eco, "ecology_id", str(i)): self.verify(theorem, eco)
            for i, eco in enumerate(ecologies)
        }

    def verify_all(
        self,
        theorems: list[EcologyTheorem],
        ecology: TheoremEcology,
    ) -> dict[str, bool]:
        """Verify each theorem in *theorems* against *ecology*.

        Parameters
        ----------
        theorems:
            List of theorems to check.
        ecology:
            Ecology to test.

        Returns
        -------
        dict[str, bool]
            Mapping theorem_id → verification result.
        """
        return {thm.theorem_id: self.verify(thm, ecology) for thm in theorems}

    # ------------------------------------------------------------------
    # Structural condition checks
    # ------------------------------------------------------------------

    def check_stability_conditions(self, ecology: TheoremEcology) -> bool:
        """Check the structural preconditions for the stability theorem.

        The ecology is deemed stable if:

        1. Health score ≥ 0.5 (sufficient vitality).
        2. Graph connectivity ≥ 0.3 (not too sparse).
        3. Number of theorems ≥ 2 (non-trivial ecology).
        4. Dependency depth ≤ 10 (not overgrown / unboundedly deep).

        Parameters
        ----------
        ecology:
            Ecology to check.

        Returns
        -------
        bool
            ``True`` iff all four conditions hold.
        """
        health_obj = getattr(ecology, "health", None)
        score = getattr(health_obj, "score", 0.0) if health_obj else 0.0
        if score < _STABILITY_HEALTH_THRESHOLD:
            return False

        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))

        if len(theorems) < 2:
            return False

        all_nodes = set(theorems + lemmas)
        n = max(1, len(all_nodes))
        visited: set[str] = set(theorems[:1])
        queue: deque[str] = deque(theorems[:1])
        while queue:
            node = queue.popleft()
            for nbr in deps.get(node, []):
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
        connectivity = len(visited) / n
        if connectivity < _STABILITY_CONNECTIVITY_THRESHOLD:
            return False

        depth = _bfs_depth(deps, theorems[:1])
        if depth > 10:
            return False

        return True

    def check_convergence_conditions(self, ecology: TheoremEcology) -> bool:
        """Check preconditions for the compounding-convergence theorem.

        The convergence conditions require:

        1. At least 2 theorems in the ecology.
        2. At least 1 lemma in the ecology.
        3. Dependency depth ≥ 1 (there is at least one dependency edge).
        4. The fraction of lemmas with in-degree ≥ 2 (shared lemmas)
           is at least 20 % — sufficient shared structure for synergy.

        Parameters
        ----------
        ecology:
            Ecology to check.

        Returns
        -------
        bool
            ``True`` iff all conditions hold.
        """
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))

        if len(theorems) < 2:
            return False
        if not lemmas:
            return False

        n_edges = sum(len(v) for v in deps.values())
        if n_edges < 1:
            return False

        # Count in-degree for each lemma
        in_degree: dict[str, int] = defaultdict(int)
        for dep_list in deps.values():
            for d in dep_list:
                if d in set(lemmas):
                    in_degree[d] += 1

        shared = sum(1 for v in in_degree.values() if v >= 2)
        shared_fraction = shared / max(1, len(lemmas))
        return shared_fraction >= 0.20

    def check_optimality_conditions(self, portfolio: LemmaPortfolio) -> bool:
        """Check whether *portfolio* satisfies the optimality theorem conditions.

        Optimality conditions (from §5.3):

        1. Portfolio coverage ≥ 0.5.
        2. Portfolio has at least one lemma.
        3. At least one lemma has above-average utility (avoids degenerate
           uniform-utility portfolios where any swap is indifferent).

        Parameters
        ----------
        portfolio:
            Portfolio to check.

        Returns
        -------
        bool
            ``True`` iff all three conditions hold.
        """
        coverage = float(getattr(portfolio, "coverage", 0.0))
        if coverage < _COVERAGE_THRESHOLD:
            return False

        lemmas = list(getattr(portfolio, "lemma_ids", []))
        if not lemmas:
            return False

        utility_scores: dict[str, float] = dict(getattr(portfolio, "utility_scores", {}))
        if not utility_scores:
            return True  # Cannot check utility; pass

        mean_util = sum(utility_scores.values()) / len(utility_scores)
        return any(v > mean_util for v in utility_scores.values())

    # ------------------------------------------------------------------
    # Fuzzy satisfaction
    # ------------------------------------------------------------------

    def estimate_satisfaction(
        self,
        theorem: EcologyTheorem,
        ecology: TheoremEcology,
    ) -> float:
        """Compute a fuzzy satisfaction degree in [0, 1].

        Each applicable hypothesis condition contributes to the degree:

        * If a condition is fully satisfied its normalised score is 1.0.
        * If partially satisfied (e.g. health = 0.4 vs threshold 0.5) the
          score is the ratio of the observed value to the threshold.

        The final degree is the harmonic mean of individual condition scores.
        The harmonic mean is used rather than arithmetic mean because it is
        sensitive to any single condition being near zero, which better
        reflects the logical conjunction of the hypothesis clauses.

        Parameters
        ----------
        theorem:
            Theorem to evaluate.
        ecology:
            Ecology to score against.

        Returns
        -------
        float
            Satisfaction degree in [0.0, 1.0].
        """
        hyp = theorem.hypothesis.lower()
        scores: list[float] = []

        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))
        health_obj = getattr(ecology, "health", None)
        health_score = getattr(health_obj, "score", 0.0) if health_obj else 0.0

        if any(k in hyp for k in ("health", "healthy")):
            scores.append(_clamp(health_score / _STABILITY_HEALTH_THRESHOLD))

        if any(k in hyp for k in ("connect", "connectivity")):
            all_nodes = set(theorems + lemmas)
            n = max(1, len(all_nodes))
            visited: set[str] = set(theorems[:1])
            q: deque[str] = deque(theorems[:1])
            while q:
                node = q.popleft()
                for nbr in deps.get(node, []):
                    if nbr not in visited:
                        visited.add(nbr)
                        q.append(nbr)
            connectivity = len(visited) / n
            scores.append(_clamp(connectivity / _STABILITY_CONNECTIVITY_THRESHOLD))

        if any(k in hyp for k in ("divers", "entropy")):
            degree_seq = [len(v) for v in deps.values()]
            ent = _entropy(degree_seq) if degree_seq else 0.0
            scores.append(_clamp(ent / _DIVERSITY_ENTROPY_THRESHOLD))

        if any(k in hyp for k in ("lemma", "lemmas")):
            scores.append(1.0 if lemmas else 0.0)

        if any(k in hyp for k in ("theorem", "theorems", "node")):
            scores.append(1.0 if theorems else 0.0)

        if not scores:
            return 1.0  # Vacuously satisfied

        # Harmonic mean
        total_recip = sum(1.0 / (s + _EPSILON) for s in scores)
        return _clamp(len(scores) / total_recip)

    def counterexample_check(
        self,
        theorem: EcologyTheorem,
        ecologies: list[TheoremEcology],
    ) -> TheoremEcology | None:
        """Find an ecology that satisfies the hypothesis but violates the conclusion.

        A counterexample is an ecology for which:
        - The hypothesis is satisfied (:meth:`verify` returns ``True``).
        - The ecology's health or diversity score falls below what the
          conclusion predicts as achievable.

        This check is a heuristic approximation — a genuine refutation
        would require formal proof.

        Parameters
        ----------
        theorem:
            Theorem to look for counterexamples to.
        ecologies:
            Pool of ecologies to search through.

        Returns
        -------
        TheoremEcology or None
            The first ecology found that may constitute a counterexample,
            or ``None`` if none is found.
        """
        for eco in ecologies:
            if not self.verify(theorem, eco):
                continue
            # Heuristic counterexample: hypothesis satisfied but health very low
            health_obj = getattr(eco, "health", None)
            score = getattr(health_obj, "score", 0.5) if health_obj else 0.5
            conclusion_lower = theorem.conclusion.lower()
            if any(k in conclusion_lower for k in ("stable", "converge", "optimal", "healthy")):
                if score < _STABILITY_HEALTH_THRESHOLD * 0.5:
                    return eco  # Suspicious: conclusion predicts positive outcome but health is low
        return None


# ===========================================================================
# TheoremApplications
# ===========================================================================

class TheoremApplications:
    """Applies theorems to derive formal conclusions about ecologies.

    Given a :class:`TheoremRegistry` populated with :class:`EcologyTheorem`
    instances, this class provides methods for:

    - Applying individual theorems by ID.
    - Bulk application of all applicable theorems.
    - Deriving quantitative bounds on health and coverage.
    - Generating stability certificates.
    - Detecting contradictions among applicable theorems.

    Parameters
    ----------
    registry:
        Pre-populated theorem registry to draw from.
    """

    def __init__(self, registry: TheoremRegistry) -> None:
        self._registry: TheoremRegistry = registry
        self._verifier: TheoremVerifier = TheoremVerifier()

    # ------------------------------------------------------------------
    # Application methods
    # ------------------------------------------------------------------

    def apply(
        self,
        theorem_id: str,
        ecology: TheoremEcology,
    ) -> str | None:
        """Apply the theorem identified by *theorem_id* to *ecology*.

        If the theorem's hypothesis is satisfied, returns the conclusion
        string.  Otherwise returns ``None``.

        Parameters
        ----------
        theorem_id:
            ID of the theorem to apply.
        ecology:
            Ecology to apply the theorem to.

        Returns
        -------
        str or None
            The theorem's conclusion string, or ``None`` if inapplicable.
        """
        theorem = self._registry.get(theorem_id)
        if theorem is None:
            return None
        if self._verifier.verify(theorem, ecology):
            return theorem.conclusion
        return None

    def apply_all(
        self,
        ecology: TheoremEcology,
    ) -> list[tuple[EcologyTheorem, str]]:
        """Apply all applicable theorems to *ecology*.

        Parameters
        ----------
        ecology:
            The ecology to apply theorems to.

        Returns
        -------
        list[tuple[EcologyTheorem, str]]
            List of (theorem, conclusion) pairs, sorted by theorem strength
            descending.  Only theorems whose hypothesis is satisfied are
            included.
        """
        applicable = self._registry.applicable_to(ecology)
        results: list[tuple[EcologyTheorem, str]] = []
        for thm in applicable:
            if self._verifier.verify(thm, ecology):
                results.append((thm, thm.conclusion))
        return results

    def derive_health_bounds(
        self,
        ecology: TheoremEcology,
    ) -> tuple[float, float]:
        """Use stability theorem to derive a health bound interval [lo, hi].

        The lower bound is derived from the stability theorem's equilibrium
        prediction: if the ecology is stable, health ≥ H* = 1 − d/r.
        The upper bound is 1.0 (theoretical maximum).

        When the stability theorem is not applicable (hypothesis not met),
        returns ``(0.0, 1.0)`` — an uninformative bound.

        Parameters
        ----------
        ecology:
            Ecology to derive bounds for.

        Returns
        -------
        tuple[float, float]
            ``(lower_bound, upper_bound)`` both in [0, 1].
        """
        # Try to find a stability theorem in the registry
        stability_theorems = self._registry.by_type(TheoremType.STABILITY)
        lower = 0.0
        upper = 1.0
        for thm in stability_theorems:
            if self._verifier.verify(thm, ecology):
                # Estimate equilibrium health from the ecology parameters
                deps = dict(getattr(ecology, "dependencies", {}))
                theorems = list(getattr(ecology, "theorem_ids", []))
                lemmas = list(getattr(ecology, "lemma_ids", []))
                n_nodes = max(1, len(theorems) + len(lemmas))
                n_edges = sum(len(v) for v in deps.values())
                connectivity = min(1.0, n_edges / (n_nodes * (n_nodes - 1) + _EPSILON))
                r = 0.3 + 0.4 * connectivity
                depth = _bfs_depth(deps, theorems[:1])
                d = 0.05 + 0.02 * depth
                H_star = _clamp(1.0 - d / (r + _EPSILON))
                lower = max(lower, H_star * 0.9)  # conservative lower bound
                break
        return (round(lower, 4), round(upper, 4))

    def predict_portfolio_gain(self, portfolio: LemmaPortfolio) -> float:
        """Predict the coverage gain achievable by greedy optimisation.

        Uses the portfolio-optimality theorem guarantee: the greedy algorithm
        achieves at least (1 − 1/e) ≈ 63.2 % of the optimal coverage gain.

        If the portfolio does not satisfy the optimality theorem conditions,
        returns 0.0 (no guarantee available).

        Parameters
        ----------
        portfolio:
            Portfolio to predict gain for.

        Returns
        -------
        float
            Predicted coverage gain in [0.0, 1.0].
        """
        optimality_theorems = self._registry.by_type(TheoremType.OPTIMALITY)
        coverage = float(getattr(portfolio, "coverage", 0.0))
        for thm in optimality_theorems:
            if self._verifier.check_optimality_conditions(portfolio):
                # The greedy approximation guarantees coverage gain ≥ (1−1/e)·(1−current_coverage)
                remaining = 1.0 - coverage
                predicted_gain = _GREEDY_APPROX_RATIO * remaining
                return round(_clamp(predicted_gain), 4)
        return 0.0

    def stability_certificate(
        self,
        ecology: TheoremEcology,
    ) -> dict[str, Any]:
        """Return a stability certificate for *ecology* backed by theorem references.

        A certificate is issued when at least one stability theorem in the
        registry has its conditions met by *ecology*.  It includes the
        health bounds, applicable theorem IDs, and a summary of the stability
        analysis.

        Parameters
        ----------
        ecology:
            Ecology to certify.

        Returns
        -------
        dict[str, Any]
            Keys: ``certified``, ``ecology_id``, ``health_bounds``,
            ``supporting_theorems``, ``estimated_equilibrium``,
            ``generated_at``.
        """
        eco_id = getattr(ecology, "ecology_id", "")
        bounds = self.derive_health_bounds(ecology)
        supporting = [
            thm.theorem_id
            for thm in self._registry.by_type(TheoremType.STABILITY)
            if self._verifier.verify(thm, ecology)
        ]
        deps = dict(getattr(ecology, "dependencies", {}))
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        n_nodes = max(1, len(theorems) + len(lemmas))
        n_edges = sum(len(v) for v in deps.values())
        connectivity = min(1.0, n_edges / (n_nodes * (n_nodes - 1) + _EPSILON))
        r = 0.3 + 0.4 * connectivity
        depth = _bfs_depth(deps, theorems[:1])
        d = 0.05 + 0.02 * depth
        H_star = _clamp(1.0 - d / (r + _EPSILON))
        return {
            "certified": bool(supporting),
            "ecology_id": eco_id,
            "health_bounds": {"lower": bounds[0], "upper": bounds[1]},
            "supporting_theorems": supporting,
            "estimated_equilibrium": round(H_star, 4),
            "growth_rate": round(r, 4),
            "decay_rate": round(d, 4),
            "generated_at": _now_iso(),
        }

    def contradiction_check(
        self,
        ecology: TheoremEcology,
    ) -> list[str]:
        """Detect pairs of applicable theorems with conflicting conclusions.

        Two theorems are considered conflicting when:
        - Both are applicable to *ecology*.
        - One predicts a positive/stable outcome (keywords: ``"stable"``,
          ``"converge"``, ``"optimal"``) and the other predicts a negative/
          unstable outcome (keywords: ``"unstable"``, ``"diverge"``,
          ``"sub-optimal"``).

        In practice, well-formed theorem catalogues should not contain
        such pairs; this method serves as a consistency check.

        Parameters
        ----------
        ecology:
            Ecology to check applicable theorems for.

        Returns
        -------
        list[str]
            List of human-readable conflict descriptions.  Empty when no
            conflicts are found.
        """
        applicable = self._registry.applicable_to(ecology)
        positive_keywords = {"stable", "converge", "optimal", "maximum", "guaranteed", "lower bound"}
        negative_keywords = {"unstable", "diverge", "sub-optimal", "minimum", "upper bound", "fails"}

        positive_theorems = [
            thm for thm in applicable
            if _keyword_match(thm.conclusion, positive_keywords)
        ]
        negative_theorems = [
            thm for thm in applicable
            if _keyword_match(thm.conclusion, negative_keywords)
        ]

        conflicts: list[str] = []
        for pt in positive_theorems:
            for nt in negative_theorems:
                if pt.theorem_id != nt.theorem_id:
                    overlap = _tokenize_lower(pt.conclusion) & _tokenize_lower(nt.conclusion)
                    if len(overlap) >= 2:
                        conflicts.append(
                            f"Potential conflict: '{pt.name}' (positive) vs "
                            f"'{nt.name}' (negative) share terms {sorted(overlap)[:3]}"
                        )
        return conflicts


# ===========================================================================
# TheoremCatalog
# ===========================================================================

class TheoremCatalog:
    """Catalog of all built-in formal theorems about theorem ecologies.

    Builds and stores all eight theorems defined in theory2.tex Ch61 §5.
    Each theorem is an :class:`EcologyTheorem` instance with a real
    mathematical statement, hypothesis, conclusion, and proof sketch.

    The catalog is also accessible at module level as the singleton
    :data:`CATALOG`.

    Theorems
    --------
    1. **ECOLOGY_STABILITY** (§5.1) — Stability under logistic health dynamics.
    2. **COMPOUNDING_CONVERGENCE** (§5.2) — Convergence of compound effects.
    3. **PORTFOLIO_OPTIMALITY** (§5.3) — Greedy approximation ratio guarantee.
    4. **DIVERSITY_BENEFIT** (§5.4) — Diversity→health monotone relationship.
    5. **LEMMA_REUSE_THEOREM** (§5.5) — Reuse frequency predicts utility.
    6. **DEPENDENCY_DEPTH_BOUND** (§5.6) — O(log n) depth bound.
    7. **GROWTH_EQUILIBRIUM** (§5.7) — Logistic growth unique equilibrium.
    8. **SYMBIOSIS_AMPLIFICATION** (§5.8) — Super-linear compounding in symbiosis.
    """

    def __init__(self) -> None:
        self.ECOLOGY_STABILITY = EcologyTheorem(
            theorem_id="ecology_stability_5_1",
            name="Ecology Stability Theorem",
            statement=(
                "Let E be a theorem ecology with health score H(E) ≥ 0.5 and "
                "dependency-graph connectivity κ(E) ≥ 0.3.  Then under the "
                "logistic health ODE dH/dt = r·H·(1−H/K) − d·H with intrinsic "
                "growth rate r > d, E has a unique globally attracting equilibrium "
                "H* = K·(1 − d/r) > 0 and is Lyapunov stable."
            ),
            hypothesis=(
                "The ecology has health score H(E) ≥ 0.5.  The dependency graph "
                "has connectivity κ(E) ≥ 0.3 (at least 30 % of nodes are reachable "
                "from the theorem roots).  The ecology contains at least two theorems "
                "and the dependency depth does not exceed 10."
            ),
            conclusion=(
                "The ecology is Lyapunov stable: for any ε > 0 there exists δ > 0 "
                "such that perturbations of H smaller than δ remain within ε of H*. "
                "Furthermore the health trajectory converges to H* as t → ∞."
            ),
            theorem_type=TheoremType.STABILITY,
            status=TheoremStatus.VERIFIED,
            proof_sketch=(
                "Lyapunov function V(H) = (H − H*)² / 2.  Differentiating: "
                "dV/dt = (H − H*)·dH/dt = (H − H*)·r·H·(1−H/K−d/r).  At H = H* "
                "the right factor vanishes; for H ≠ H* a direct sign analysis shows "
                "dV/dt < 0, establishing global convergence by LaSalle's invariance "
                "principle."
            ),
            references=("Ch61 §5.1", "Ch61 §4.5"),
            tags=("stability", "health", "ode", "lyapunov", "ecology"),
        )

        self.COMPOUNDING_CONVERGENCE = EcologyTheorem(
            theorem_id="compounding_convergence_5_2",
            name="Compounding Convergence Theorem",
            statement=(
                "Let E be a theorem ecology with at least two theorems, at least "
                "one lemma, and a shared-lemma fraction (fraction of lemmas with "
                "in-degree ≥ 2) of σ ≥ 0.2.  Let {c_k} be the sequence of "
                "compounding-effect magnitudes produced by successive applications "
                "of the CompoundingEngine.  Then {c_k} converges absolutely: "
                "Σ_k c_k < C* where C* = 1 / (1 − ρ) and ρ = synergy_strength < 1."
            ),
            hypothesis=(
                "The ecology has at least two theorem nodes and at least one lemma. "
                "The dependency graph has at least one edge (depth ≥ 1).  The fraction "
                "of lemmas reused by two or more theorems (shared-lemma fraction) "
                "is at least 0.20.  The synergy coefficient ρ satisfies 0 ≤ ρ < 1."
            ),
            conclusion=(
                "The compounding-effect series converges absolutely to a finite sum "
                "C* ≤ 1/(1−ρ).  The dominant compound effect accounts for at least "
                "(1−ρ)·C* of the total magnitude.  No runaway amplification occurs."
            ),
            theorem_type=TheoremType.CONVERGENCE,
            status=TheoremStatus.VERIFIED,
            proof_sketch=(
                "Model compound magnitudes as a geometric series: c_k ≤ M·ρ^k where "
                "M is the initial magnitude and ρ < 1 is the synergy coefficient. "
                "Absolute convergence follows from Σ ρ^k = 1/(1−ρ) < ∞.  The "
                "dominance of the leading term follows from c_0/C* = 1−ρ > 0."
            ),
            references=("Ch61 §5.2", "Ch61 §3.7"),
            tags=("convergence", "compounding", "synergy", "series"),
        )

        self.PORTFOLIO_OPTIMALITY = EcologyTheorem(
            theorem_id="portfolio_optimality_5_3",
            name="Portfolio Optimality Theorem",
            statement=(
                "Let P be a lemma portfolio with coverage c(P) ≥ 0.5 and at least "
                "one lemma with above-average utility.  Let f: 2^L → [0,1] be the "
                "coverage function (assumed monotone submodular with f(∅)=0).  Then "
                "the greedy algorithm that adds the lemma of maximum marginal gain "
                "at each step achieves coverage f(P*) ≥ (1 − 1/e)·OPT where OPT is "
                "the optimal k-lemma portfolio coverage."
            ),
            hypothesis=(
                "Portfolio coverage c(P) ≥ 0.5.  Portfolio contains at least one "
                "lemma.  At least one lemma has utility score strictly above the "
                "portfolio mean utility.  The coverage function is monotone and "
                "submodular (diminishing marginal returns)."
            ),
            conclusion=(
                "The greedy algorithm achieves at least (1 − 1/e) ≈ 63.2 % of the "
                "optimal k-lemma coverage.  This bound is tight: there exist inputs "
                "for which the greedy solution achieves exactly (1 − 1/e)·OPT.  The "
                "bound holds for all k ≥ 1 independent of the lemma corpus size."
            ),
            theorem_type=TheoremType.OPTIMALITY,
            status=TheoremStatus.VERIFIED,
            proof_sketch=(
                "Classical Nemhauser-Wolsey-Fisher (1978) result for submodular "
                "maximisation under a cardinality constraint.  Submodularity of f "
                "follows from the marginal utility being non-increasing: adding a "
                "lemma to a larger portfolio yields at most as much coverage gain "
                "as adding it to a smaller one."
            ),
            references=("Ch61 §5.3", "Ch61 §4.3"),
            tags=("optimality", "portfolio", "greedy", "submodular", "coverage"),
        )

        self.DIVERSITY_BENEFIT = EcologyTheorem(
            theorem_id="diversity_benefit_5_4",
            name="Diversity Benefit Theorem",
            statement=(
                "Let E be a theorem ecology with normalised dependency-degree "
                "entropy H_deg(E) ≥ 0.7.  Let H_equil be the equilibrium health "
                "under the logistic ODE.  Then H_equil(E) > H_equil(E') for any "
                "ecology E' that is identical to E except with H_deg(E') < 0.7, "
                "provided the growth and decay parameters r, d are identical."
            ),
            hypothesis=(
                "The degree-distribution entropy of the dependency graph satisfies "
                "H_deg(E) ≥ 0.7 (high diversity).  The ecology has identical "
                "growth rate r and decay rate d to the comparison ecology E'.  "
                "Both ecologies have the same number of nodes and edges."
            ),
            conclusion=(
                "High-diversity ecologies achieve strictly higher equilibrium health "
                "than low-diversity ecologies under otherwise identical conditions. "
                "Specifically H_equil(E) − H_equil(E') ≥ Δ_div > 0 where Δ_div "
                "depends on the entropy gap H_deg(E) − H_deg(E')."
            ),
            theorem_type=TheoremType.BOUND,
            status=TheoremStatus.CONJECTURED,
            proof_sketch=(
                "The entropy of the degree distribution acts as a measure of "
                "robustness to targeted removal of high-degree nodes.  Higher entropy "
                "implies that no single node is disproportionately critical, reducing "
                "the effective decay rate d and hence raising H* = 1 − d/r. "
                "A formal proof requires showing monotonicity of H* in the degree "
                "entropy; this is plausible but not yet mechanically verified."
            ),
            references=("Ch61 §5.4",),
            tags=("diversity", "entropy", "health", "benefit", "robustness"),
        )

        self.LEMMA_REUSE_THEOREM = EcologyTheorem(
            theorem_id="lemma_reuse_theorem_5_5",
            name="Lemma Reuse Utility Theorem",
            statement=(
                "Let L be a lemma in ecology E and let ρ(L) be the reuse count of "
                "L (the number of theorems in E that depend on L).  Let U(L) be the "
                "utility score of L as estimated by the LemmaUtilityEstimator.  Then "
                "for any quantile threshold τ ∈ (0, 1), if ρ(L) ≥ Q_{τ}(ρ) (i.e., "
                "ρ(L) is in the top (1−τ) quantile of reuse counts) then U(L) ≥ "
                "median(U) with probability at least 1 − 2·exp(−2n·(τ−0.5)²) over "
                "random lemma portfolios of size n."
            ),
            hypothesis=(
                "The lemma L appears in the dependency lists of at least two theorem "
                "nodes (reuse count ρ(L) ≥ 2).  The ecology has at least three "
                "theorems.  The utility estimator has been calibrated on at least "
                "10 historical proof-obligation traces."
            ),
            conclusion=(
                "Frequently reused lemmas (reuse count in the top quartile) have "
                "utility scores above the portfolio median with high probability. "
                "This justifies using reuse count as a cheap proxy for utility "
                "when the full utility estimator is unavailable."
            ),
            theorem_type=TheoremType.BOUND,
            status=TheoremStatus.CONDITIONAL,
            proof_sketch=(
                "Model utility U(L) as a noisy linear function of ρ(L): "
                "U(L) = α·ρ(L) + ε, where ε is mean-zero noise and α > 0. "
                "By Hoeffding's inequality the probability that a high-reuse lemma "
                "has below-median utility decays exponentially in n.  "
                "The conditional nature arises from the calibration requirement: "
                "without sufficient historical data α cannot be estimated reliably."
            ),
            references=("Ch61 §5.5", "Ch61 §2.3"),
            tags=("lemma", "reuse", "utility", "portfolio", "probabilistic"),
        )

        self.DEPENDENCY_DEPTH_BOUND = EcologyTheorem(
            theorem_id="dependency_depth_bound_5_6",
            name="Dependency Depth Bound Theorem",
            statement=(
                "Let E be a theorem ecology with n nodes and an acyclic dependency "
                "graph G(E).  Let d*(E) be the depth of the optimal dependency "
                "structure that maximises health H(E).  Then d*(E) = O(log n): "
                "specifically d*(E) ≤ ⌈2·log₂(n)⌉ for all n ≥ 2."
            ),
            hypothesis=(
                "The dependency graph of the ecology is acyclic (a DAG).  The "
                "ecology has n ≥ 2 nodes in total (theorems + lemmas).  The health "
                "function H is the equilibrium of the logistic ODE with r, d > 0."
            ),
            conclusion=(
                "The health-maximising dependency depth is at most 2·log₂(n). "
                "Ecologies with depth exceeding this bound are suboptimal: reducing "
                "depth to ⌈2·log₂(n)⌉ cannot decrease equilibrium health H*. "
                "Furthermore the depth bound implies that proof-search depth in the "
                "lemma portfolio need not exceed O(log n)."
            ),
            theorem_type=TheoremType.BOUND,
            status=TheoremStatus.VERIFIED,
            proof_sketch=(
                "Increasing depth increases the decay parameter d = 0.05 + 0.02·depth "
                "without commensurate increase in r.  H* = 1 − d/r is maximised by "
                "minimising d.  The minimum depth consistent with a connected acyclic "
                "graph on n nodes is ⌈log₂(n)⌉ (balanced binary DAG), giving "
                "d_min = 0.05 + 0.02·⌈log₂(n)⌉.  Depths up to twice this minimum "
                "still yield H* > 0.5 for typical r values, hence the factor-of-2 "
                "slack in the bound."
            ),
            references=("Ch61 §5.6", "Ch61 §4.1"),
            tags=("depth", "bound", "dag", "optimality", "structural"),
        )

        self.GROWTH_EQUILIBRIUM = EcologyTheorem(
            theorem_id="growth_equilibrium_5_7",
            name="Growth Equilibrium Uniqueness Theorem",
            statement=(
                "Let E be a theorem ecology and let H(t) be its health trajectory "
                "under the logistic ODE with r > d > 0 and initial condition "
                "H(0) = H₀ ∈ (0, 1).  Then: (i) there is a unique positive "
                "equilibrium H* = 1 − d/r ∈ (0, 1); (ii) H(t) → H* as t → ∞ "
                "for all H₀ ∈ (0, 1); (iii) the convergence rate is exponential "
                "with time constant 1/|r(1−2H*)−d|."
            ),
            hypothesis=(
                "The intrinsic growth rate r and decay rate d satisfy 0 < d < r ≤ 1. "
                "The initial health H₀ lies strictly between 0 and 1.  The logistic "
                "ODE dH/dt = r·H·(1−H) − d·H governs the health evolution."
            ),
            conclusion=(
                "The logistic growth ODE has a unique globally stable positive "
                "equilibrium H* = 1 − d/r.  All trajectories starting in (0, 1) "
                "converge monotonically to H* if H₀ < H* and approach from above "
                "if H₀ > H*.  The trivial equilibrium H = 0 is unstable (it has "
                "eigenvalue r − d > 0)."
            ),
            theorem_type=TheoremType.UNIQUENESS,
            status=TheoremStatus.VERIFIED,
            proof_sketch=(
                "The ODE dH/dt = H·(r(1−H) − d) has equilibria at H=0 and H=H*. "
                "Linearise at each: J(0) = r−d > 0 (unstable), J(H*) = −(r−d) < 0 "
                "(asymptotically stable).  The interval (0, H*) is positively "
                "invariant (f > 0 there) and (H*, 1) is also positively invariant "
                "(f < 0 there), so all trajectories converge to H* by the "
                "Poincaré-Bendixson theorem for 1-D systems."
            ),
            references=("Ch61 §5.7", "Ch61 §4.5"),
            tags=("growth", "equilibrium", "uniqueness", "ode", "convergence"),
        )

        self.SYMBIOSIS_AMPLIFICATION = EcologyTheorem(
            theorem_id="symbiosis_amplification_5_8",
            name="Symbiosis Amplification Theorem",
            statement=(
                "Let E_A and E_B be theorem ecologies in a symbiotic relationship "
                "with shared-lemma Jaccard coefficient J(L_A, L_B) = β ∈ (0, 1). "
                "Let C(E) denote the total compounding magnitude for ecology E in "
                "isolation and C(E_A, E_B) the compounding magnitude when the "
                "two ecologies interact symbiotically.  Then "
                "C(E_A, E_B) ≥ C(E_A) + C(E_B) + β·C(E_A)·C(E_B)."
            ),
            hypothesis=(
                "Ecologies E_A and E_B share at least one lemma (Jaccard similarity "
                "J(L_A, L_B) = β > 0).  Both ecologies have positive compounding "
                "magnitude in isolation (C(E_A) > 0 and C(E_B) > 0).  The symbiosis "
                "coefficient β is estimated as the shared-lemma Jaccard similarity."
            ),
            conclusion=(
                "The combined compounding magnitude under symbiotic interaction is "
                "strictly super-linear: C(E_A, E_B) > C(E_A) + C(E_B).  The "
                "super-linear surplus is at least β·C(E_A)·C(E_B) > 0.  This "
                "justifies preferentially merging or co-maintaining ecologies "
                "with high Jaccard similarity."
            ),
            theorem_type=TheoremType.BOUND,
            status=TheoremStatus.CONJECTURED,
            proof_sketch=(
                "Model the compound interaction as a bilinear coupling term in "
                "the compounding ODE: dC_AB/dt = f(C_A, C_B) + β·C_A·C_B.  The "
                "β·C_A·C_B term generates super-linear growth when both ecologies "
                "have positive compounding magnitude.  Integrating over t and "
                "comparing with the uncoupled sum yields the stated inequality. "
                "Rigorous proof requires showing the coupling term is bounded away "
                "from zero under the steady-state Lotka-Volterra symbiosis dynamics."
            ),
            references=("Ch61 §5.8", "Ch61 §3.4"),
            tags=("symbiosis", "amplification", "compounding", "super-linear", "jaccard"),
        )

    # ------------------------------------------------------------------
    # Accessor methods
    # ------------------------------------------------------------------

    def all(self) -> list[EcologyTheorem]:
        """Return a list of all built-in theorems.

        Returns
        -------
        list[EcologyTheorem]
            All eight catalog theorems in definition order.
        """
        return [
            self.ECOLOGY_STABILITY,
            self.COMPOUNDING_CONVERGENCE,
            self.PORTFOLIO_OPTIMALITY,
            self.DIVERSITY_BENEFIT,
            self.LEMMA_REUSE_THEOREM,
            self.DEPENDENCY_DEPTH_BOUND,
            self.GROWTH_EQUILIBRIUM,
            self.SYMBIOSIS_AMPLIFICATION,
        ]

    def get_registry(self) -> TheoremRegistry:
        """Return a :class:`TheoremRegistry` pre-loaded with all catalog theorems.

        Returns
        -------
        TheoremRegistry
            Registry containing all eight built-in theorems, ready for use
            with :class:`TheoremVerifier` and :class:`TheoremApplications`.
        """
        registry = TheoremRegistry()
        registry.register_batch(self.all())
        return registry

    def by_name(self, name: str) -> EcologyTheorem | None:
        """Look up a catalog theorem by its human-readable name.

        The comparison is case-insensitive and uses substring matching so that
        ``by_name("stability")`` matches ``"Ecology Stability Theorem"``.

        Parameters
        ----------
        name:
            Name or name fragment to search for.

        Returns
        -------
        EcologyTheorem or None
            The first matching theorem, or ``None`` if none matches.
        """
        name_lower = name.lower()
        for thm in self.all():
            if name_lower in thm.name.lower():
                return thm
        return None


# ===========================================================================
# Module-level catalog singleton
# ===========================================================================

# Module-level catalog instance
CATALOG = TheoremCatalog()

__all__ = [
    # Helpers
    "_now_iso",
    "_clamp",
    "_tokenize_lower",
    "_keyword_match",
    "_bfs_depth",
    "_jaccard",
    "_entropy",
    # Constants
    "_STABILITY_HEALTH_THRESHOLD",
    "_STABILITY_CONNECTIVITY_THRESHOLD",
    "_SYNERGY_THRESHOLD",
    "_DIVERSITY_ENTROPY_THRESHOLD",
    "_COVERAGE_THRESHOLD",
    "_GREEDY_APPROX_RATIO",
    # Enumerations
    "TheoremStatus",
    "TheoremType",
    # Core classes
    "EcologyTheorem",
    "TheoremRegistry",
    "TheoremVerifier",
    "TheoremApplications",
    "TheoremCatalog",
    # Module-level instance
    "CATALOG",
]
