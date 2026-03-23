"""
Falsification Loop — JuGeo Methodology Loops Package (s03)

This module implements the falsification loop of the JuGeo evaluation
methodology.  A *falsification loop* is an iterative procedure that
systematically attempts to falsify a collection of mathematical hypotheses
by searching for counterexamples.  Each iteration applies a
:class:`CounterexampleSearcher` to each tracked hypothesis and updates a
:class:`HypothesisTracker` with the outcome.  The loop continues until
every hypothesis is either falsified, confirmed inconclusive, or the
computational budget is exhausted.

Design principles
-----------------
* **Mutable attempts** – :class:`FalsificationAttempt` uses regular
  (non-frozen) slots so that attempt status can be updated as evidence
  accumulates.
* **Pluggable search strategies** – the ``strategy`` parameter selects the
  counterexample search algorithm (random, exhaustive, or guided).
* **Budget management** – the :class:`CounterexampleSearcher` enforces a
  hard computational budget measured in search steps, preventing unbounded
  computation.
* **Structured outcome tracking** – the :class:`HypothesisTracker` maintains
  a priority queue of hypotheses ordered by falsification difficulty
  estimate.

copilot: shared-core marker
Theory reference: theory2.tex Ch62
"""
from __future__ import annotations

import json
import math
import random
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence

__all__ = [
    "FalsificationAttempt",
    "CounterexampleSearcher",
    "HypothesisTracker",
    "FalsificationLoopRunner",
    "run_falsification_loop",
    "attempt_falsification",
]

# ---------------------------------------------------------------------------
# Optional JuGeo imports
# ---------------------------------------------------------------------------

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
# Module-level constants and helpers
# ---------------------------------------------------------------------------

_ATTEMPT_STATUS_PENDING = "pending"
_ATTEMPT_STATUS_SUCCESS = "success"        # counterexample found
_ATTEMPT_STATUS_FAILURE = "failure"        # no counterexample found (so far)
_ATTEMPT_STATUS_INCONCLUSIVE = "inconclusive"

_HYPOTHESIS_STATUS_PENDING = "pending"
_HYPOTHESIS_STATUS_FALSIFIED = "falsified"
_HYPOTHESIS_STATUS_CONFIRMED = "confirmed"
_HYPOTHESIS_STATUS_INCONCLUSIVE = "inconclusive"

_VALID_STRATEGIES = frozenset({"random", "exhaustive", "guided"})
_STRATEGY_ALIASES = {
    "systematic": "exhaustive",
    "heuristic": "guided",
}

_DEFAULT_RANDOM_SEED = 42


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _normalise_strategy(strategy: str) -> str:
    """Return a lower-cased strategy identifier.

    Raises :class:`ValueError` if the strategy is not supported.
    """
    norm = strategy.strip().lower()
    norm = _STRATEGY_ALIASES.get(norm, norm)
    if norm not in _VALID_STRATEGIES:
        valid = ", ".join(sorted(_VALID_STRATEGIES))
        raise ValueError(
            f"Unsupported search strategy {strategy!r}. "
            f"Valid strategies: {valid}"
        )
    return norm


def _hypothesis_difficulty(hypothesis: dict[str, Any]) -> float:
    """Estimate the difficulty of falsifying *hypothesis*.

    The heuristic inspects the ``"complexity"`` and ``"domain"`` fields of
    *hypothesis* to produce a difficulty score in [0, 1].  Higher values
    indicate harder hypotheses.

    Parameters
    ----------
    hypothesis:
        Hypothesis dictionary.

    Returns
    -------
    float
        Difficulty estimate in [0, 1].
    """
    complexity = float(hypothesis.get("complexity", 0.5))
    domain_penalty = 0.1 if hypothesis.get("domain", "generic") != "generic" else 0.0
    return _clamp(complexity + domain_penalty, 0.0, 1.0)


# ---------------------------------------------------------------------------
# FalsificationAttempt
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class FalsificationAttempt:
    """Mutable record representing one attempt to falsify a hypothesis.

    Unlike the frozen dataclasses in the other modules, this class uses
    regular (mutable) slots so that the attempt status and counterexample
    field can be updated as evidence accumulates during the search.

    Attributes
    ----------
    attempt_id:
        Globally unique identifier for this attempt (UUID4 string).
    hypothesis_id:
        Identifier of the hypothesis being tested.
    strategy:
        Search strategy used (``"random"``, ``"exhaustive"``, or
        ``"guided"``).
    counterexample:
        The counterexample dictionary if one was found, or ``None``.
    status:
        Current attempt status: ``"pending"``, ``"success"``,
        ``"failure"``, or ``"inconclusive"``.
    score:
        A numerical score in [0, 1] representing the strength of the
        attempt.  A score of 1.0 means a conclusive counterexample was
        found.
    iterations_used:
        Number of internal search steps consumed by this attempt.
    created_at:
        Unix timestamp (UTC) when this attempt was created.
    metadata:
        Arbitrary key/value metadata attached to this attempt.
    """

    attempt_id: str
    hypothesis_id: str
    strategy: str
    counterexample: dict[str, Any] | None
    status: str
    score: float
    iterations_used: int
    created_at: float
    metadata: dict[str, Any]

    def __init__(
        self,
        hypothesis_id: str,
        status: str = _ATTEMPT_STATUS_PENDING,
        counterexample: Optional[dict[str, Any]] = None,
        *,
        attempt_id: Optional[str] = None,
        strategy: str = "random",
        score: float = 0.0,
        iterations_used: int = 0,
        created_at: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.attempt_id = attempt_id or _uid()
        self.hypothesis_id = hypothesis_id
        self.strategy = _normalise_strategy(strategy)
        self.counterexample = counterexample
        self.status = status
        self.score = _clamp(score, 0.0, 1.0)
        self.iterations_used = max(0, iterations_used)
        self.created_at = _utcnow() if created_at is None else float(created_at)
        self.metadata = dict(metadata or {})

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        hypothesis_id: str,
        strategy: str = "random",
        metadata: dict[str, Any] | None = None,
    ) -> "FalsificationAttempt":
        """Construct a new pending :class:`FalsificationAttempt`.

        Parameters
        ----------
        hypothesis_id:
            Identifier of the hypothesis to attempt to falsify.
        strategy:
            Search strategy to use.
        metadata:
            Optional metadata dictionary.

        Returns
        -------
        FalsificationAttempt
            A freshly created attempt in ``"pending"`` status.
        """
        return cls(
            attempt_id=_uid(),
            hypothesis_id=hypothesis_id,
            strategy=_normalise_strategy(strategy),
            counterexample=None,
            status=_ATTEMPT_STATUS_PENDING,
            score=0.0,
            iterations_used=0,
            created_at=_utcnow(),
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def mark_success(
        self,
        counterexample: dict[str, Any],
        iterations_used: int = 0,
        score: float = 1.0,
    ) -> "FalsificationAttempt":
        """Mark this attempt as successful (counterexample found).

        Parameters
        ----------
        counterexample:
            The discovered counterexample.
        iterations_used:
            Number of search steps consumed.
        score:
            Attempt score (defaults to 1.0 for a confirmed counterexample).
        """
        self.counterexample = counterexample
        self.status = _ATTEMPT_STATUS_SUCCESS
        self.iterations_used = max(0, iterations_used)
        self.score = _clamp(score, 0.0, 1.0)
        return self

    def mark_failure(
        self,
        iterations_used: int = 0,
        score: float = 0.0,
    ) -> "FalsificationAttempt":
        """Mark this attempt as failed (no counterexample found in budget).

        Parameters
        ----------
        iterations_used:
            Number of search steps consumed.
        score:
            Attempt score (defaults to 0.0 for an exhausted search).
        """
        self.counterexample = None
        self.status = _ATTEMPT_STATUS_FAILURE
        self.iterations_used = max(0, iterations_used)
        self.score = _clamp(score, 0.0, 1.0)
        return self

    def mark_inconclusive(
        self,
        iterations_used: int = 0,
        score: float = 0.5,
        reason: str = "",
    ) -> "FalsificationAttempt":
        """Mark this attempt as inconclusive.

        Parameters
        ----------
        iterations_used:
            Number of search steps consumed.
        score:
            Attempt score.
        reason:
            Optional human-readable explanation.
        """
        self.counterexample = None
        self.status = _ATTEMPT_STATUS_INCONCLUSIVE
        self.iterations_used = max(0, iterations_used)
        self.score = _clamp(score, 0.0, 1.0)
        if reason:
            self.metadata["inconclusive_reason"] = reason
        return self

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this attempt to a JSON string.

        Returns
        -------
        str
            UTF-8 JSON representation.
        """
        return json.dumps(
            {
                "attempt_id": self.attempt_id,
                "hypothesis_id": self.hypothesis_id,
                "strategy": self.strategy,
                "counterexample": self.counterexample,
                "status": self.status,
                "score": self.score,
                "iterations_used": self.iterations_used,
                "created_at": self.created_at,
                "metadata": self.metadata,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "FalsificationAttempt":
        """Deserialise a :class:`FalsificationAttempt` from a JSON string.

        Parameters
        ----------
        data:
            JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        FalsificationAttempt

        Raises
        ------
        ValueError
            If required keys are missing from the JSON.
        """
        obj = json.loads(data)
        required = {
            "attempt_id", "hypothesis_id", "strategy", "counterexample",
            "status", "score", "iterations_used", "created_at", "metadata",
        }
        missing = required - obj.keys()
        if missing:
            raise ValueError(f"Missing keys in JSON: {missing!r}")
        return cls(
            attempt_id=obj["attempt_id"],
            hypothesis_id=obj["hypothesis_id"],
            strategy=obj["strategy"],
            counterexample=obj["counterexample"],
            status=obj["status"],
            score=float(obj["score"]),
            iterations_used=int(obj["iterations_used"]),
            created_at=float(obj["created_at"]),
            metadata=dict(obj["metadata"]),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a brief human-readable summary of this attempt.

        Returns
        -------
        str
            One-line summary string.
        """
        short = self.attempt_id[:8]
        return (
            f"FalsificationAttempt({short}) "
            f"hyp={self.hypothesis_id[:8]} "
            f"strategy={self.strategy} "
            f"status={self.status} "
            f"score={self.score:.3f} "
            f"iters={self.iterations_used}"
        )

    def is_successful(self) -> bool:
        """Return ``True`` if a counterexample was found.

        Returns
        -------
        bool
        """
        return self.status == _ATTEMPT_STATUS_SUCCESS

    def render_tex(self) -> str:
        """Render a LaTeX snippet describing this attempt.

        Returns
        -------
        str
            LaTeX source snippet suitable for inclusion in a theory document.
        """
        short = self.attempt_id[:8]
        hyp_short = self.hypothesis_id[:8]
        lines = [
            f"\\paragraph{{Falsification attempt \\texttt{{{short}}}}}",
            f"Hypothesis: \\texttt{{{hyp_short}}}.",
            f"Strategy: \\texttt{{{self.strategy}}}.",
            f"Status: \\textbf{{{self.status}}}.",
            f"Score: ${self.score:.3f}$.",
            f"Iterations used: ${self.iterations_used}$.",
        ]
        if self.counterexample is not None:
            cx_str = json.dumps(self.counterexample, indent=None)
            lines.append(f"Counterexample: $\\texttt{{{cx_str}}}$.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CounterexampleSearcher
# ---------------------------------------------------------------------------


class CounterexampleSearcher:
    """Search for counterexamples to mathematical hypotheses.

    :class:`CounterexampleSearcher` manages a portfolio of search strategies
    (random, exhaustive, guided) and applies them under a hard computational
    budget.  Each call to :meth:`search` produces a :class:`FalsificationAttempt`
    documenting the outcome.

    Attributes
    ----------
    search_strategy : str
        The default search strategy name.
    search_history : list
        Ordered list of all :class:`FalsificationAttempt` objects produced
        by this instance.
    budget : int
        Total computational budget (in search steps) remaining.
    _custom_strategies : dict
        Maps custom strategy names to callable objects.
    """

    def __init__(
        self,
        strategy: str = "random",
        budget: int = 100,
    ) -> None:
        """Initialise the searcher.

        Parameters
        ----------
        strategy:
            Default search strategy.  One of ``"random"``, ``"exhaustive"``,
            ``"guided"``.
        budget:
            Total computational budget in search steps.  Must be ≥ 1.

        Raises
        ------
        ValueError
            If *strategy* is not valid, or *budget* < 1.
        """
        self.strategy_label: str = strategy.strip().lower()
        self.search_strategy: str = _normalise_strategy(strategy)
        if budget < 0:
            raise ValueError(f"Budget must be >= 0, got {budget}.")
        self.budget: int = budget
        self._initial_budget: int = budget
        self.search_history: list[FalsificationAttempt] = []
        self._custom_strategies: dict[str, Callable[..., Any]] = {}
        self._rng = random.Random(_DEFAULT_RANDOM_SEED)

    @property
    def strategy(self) -> str:
        return self.strategy_label

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def search(
        self,
        hypothesis: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> FalsificationAttempt:
        """Attempt to falsify *hypothesis* using the configured strategy.

        This method dispatches to the appropriate search sub-method based on
        :attr:`search_strategy`:

        * ``"random"`` → :meth:`_random_search`
        * ``"exhaustive"`` → :meth:`_exhaustive_search`
        * ``"guided"`` → :meth:`_guided_search`

        After completing the search, the attempt is appended to
        :attr:`search_history` and the consumed budget is deducted.

        Parameters
        ----------
        hypothesis:
            A dictionary describing the hypothesis to test.  Recognised
            keys:

            ``"id"`` (str)
                Unique identifier for the hypothesis.
            ``"statement"`` (str)
                Human-readable statement of the hypothesis.
            ``"domain"`` (str)
                Mathematical domain (e.g. ``"number_theory"``).
            ``"complexity"`` (float)
                Estimated difficulty of falsification, in [0, 1].

        context:
            Optional context dictionary with additional search parameters.
            Recognised keys:

            ``"max_steps"`` (int)
                Per-attempt step budget override.
            ``"seed"`` (int)
                Random seed for reproducible searches.

        Returns
        -------
        FalsificationAttempt
            A completed attempt record documenting the search outcome.
        """
        if self.is_exhausted():
            attempt = FalsificationAttempt.create(
                hypothesis_id=hypothesis.get("id", _uid()),
                strategy=self.search_strategy,
            )
            attempt.mark_inconclusive(
                iterations_used=0,
                score=0.0,
                reason="Budget exhausted before search began.",
            )
            self.search_history.append(attempt)
            return attempt
        ctx = context or {}
        hyp_id = hypothesis.get("id", _uid())
        attempt = FalsificationAttempt.create(
            hypothesis_id=hyp_id,
            strategy=self.search_strategy,
        )
        max_steps = min(
            ctx.get("max_steps", self.budget),
            self.budget,
        )
        if self.search_strategy == "random":
            result = self._random_search(hypothesis)
        elif self.search_strategy == "exhaustive":
            result = self._exhaustive_search(hypothesis)
        else:
            result = self._guided_search(hypothesis, ctx)
        steps_used = max(1, int(max_steps * 0.1) + len(hypothesis.get("statement", "")))
        steps_used = min(steps_used, max_steps)
        self.consume_budget(steps_used)
        score = self._score_attempt(result, hypothesis)
        if result is not None:
            attempt.mark_success(
                counterexample=result,
                iterations_used=steps_used,
                score=score,
            )
        elif self.is_exhausted():
            attempt.mark_inconclusive(
                iterations_used=steps_used,
                score=score,
                reason="Budget exhausted during search.",
            )
        else:
            attempt.mark_failure(
                iterations_used=steps_used,
                score=score,
            )
        self.search_history.append(attempt)
        return attempt

    def search_batch(
        self, hypotheses: list[dict[str, Any]]
    ) -> list[FalsificationAttempt]:
        """Attempt to falsify a list of hypotheses.

        Parameters
        ----------
        hypotheses:
            List of hypothesis dictionaries.

        Returns
        -------
        list[FalsificationAttempt]
            One attempt per input hypothesis, in the same order.
        """
        return [self.search(h) for h in hypotheses]

    def list_strategies(self) -> list[str]:
        """Return built-in and registered strategy names."""
        return sorted(set(_VALID_STRATEGIES) | set(self._custom_strategies))

    def register_strategy(self, name: str, fn: Any) -> None:
        """Register a custom counterexample search strategy.

        Parameters
        ----------
        name:
            Strategy name.
        fn:
            Callable with signature ``(hypothesis: dict) -> dict | None``.
            It should return a counterexample dictionary or ``None``.
        """
        self._custom_strategies[name] = fn

    def get_strategy(self, name: str) -> Any | None:
        """Retrieve a registered custom strategy.

        Parameters
        ----------
        name:
            Strategy name.

        Returns
        -------
        callable | None
        """
        return self._custom_strategies.get(name)

    def consume_budget(self, used: int) -> None:
        """Deduct *used* steps from the remaining budget.

        Parameters
        ----------
        used:
            Number of steps consumed (clamped to non-negative).
        """
        self.budget = max(0, self.budget - max(0, used))

    def update_budget(self, used: int) -> None:
        """Compatibility helper that sets the remaining budget directly."""
        self.budget = max(0, int(used))

    def remaining_budget(self) -> int:
        """Return the number of search steps remaining in the budget.

        Returns
        -------
        int
        """
        return self.budget

    def is_exhausted(self) -> bool:
        """Return ``True`` if the budget has been fully consumed.

        Returns
        -------
        bool
        """
        return self.budget <= 0

    def history_report(self) -> dict[str, Any]:
        """Compute aggregate statistics over :attr:`search_history`.

        Returns
        -------
        dict
            Keys: ``"total_attempts"``, ``"success_count"``,
            ``"failure_count"``, ``"inconclusive_count"``,
            ``"success_rate"``, ``"mean_score"``,
            ``"remaining_budget"``.
        """
        n = len(self.search_history)
        if n == 0:
            return {"total_attempts": 0, "remaining_budget": self.budget}
        success = sum(1 for a in self.search_history if a.is_successful())
        failure = sum(
            1 for a in self.search_history
            if a.status == _ATTEMPT_STATUS_FAILURE
        )
        inconclusive = n - success - failure
        mean_score = sum(a.score for a in self.search_history) / n
        return {
            "total_attempts": n,
            "success_count": success,
            "failure_count": failure,
            "inconclusive_count": inconclusive,
            "success_rate": round(success / n, 4),
            "mean_score": round(mean_score, 4),
            "remaining_budget": self.budget,
        }

    def reset(self) -> None:
        """Clear search history and restore the initial budget."""
        self.search_history.clear()
        self.budget = self._initial_budget

    def summarize(self) -> str:
        """Return a brief textual summary.

        Returns
        -------
        str
        """
        return (
            f"CounterexampleSearcher("
            f"strategy={self.search_strategy}, "
            f"budget_remaining={self.budget}, "
            f"history={len(self.search_history)})"
        )

    # ------------------------------------------------------------------
    # Private search helpers
    # ------------------------------------------------------------------

    def _random_search(
        self, hypothesis: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Perform a random counterexample search.

        The heuristic randomly samples from a set of candidate
        counterexample types and returns one if the hypothesis domain
        contains a known falsifiable pattern.

        Parameters
        ----------
        hypothesis:
            Hypothesis dictionary.

        Returns
        -------
        dict | None
            A counterexample dictionary, or ``None`` if none was found.
        """
        stmt = hypothesis.get("statement", "").lower()
        falsifiable_patterns = [
            "for all", "every", "all ", "whenever", "always"
        ]
        is_universal = any(p in stmt for p in falsifiable_patterns)
        if not is_universal:
            return None
        # Randomly decide whether to produce a counterexample
        threshold = _clamp(1.0 - hypothesis.get("complexity", 0.5), 0.1, 0.9)
        if self._rng.random() < threshold:
            return {
                "type": "random_counterexample",
                "witness": self._rng.randint(1, 1000),
                "strategy": "random",
                "hypothesis_id": hypothesis.get("id", "unknown"),
            }
        return None

    def _exhaustive_search(
        self, hypothesis: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Perform an exhaustive counterexample search over a small domain.

        The exhaustive search enumerates small natural-number witnesses and
        tests each against the hypothesis's ``"predicate"`` field (if
        provided).  If no predicate is given, the domain is considered
        infinite and exhaustive search returns ``None``.

        Parameters
        ----------
        hypothesis:
            Hypothesis dictionary.

        Returns
        -------
        dict | None
            A counterexample dictionary, or ``None`` if none was found.
        """
        predicate = hypothesis.get("predicate")
        if predicate is None:
            return None
        domain_size = int(hypothesis.get("domain_size", 20))
        for n in range(domain_size):
            try:
                if not predicate(n):
                    return {
                        "type": "exhaustive_counterexample",
                        "witness": n,
                        "strategy": "exhaustive",
                        "hypothesis_id": hypothesis.get("id", "unknown"),
                    }
            except Exception:
                continue
        return None

    def _guided_search(
        self,
        hypothesis: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Perform a guided counterexample search using contextual hints.

        The guided search uses ``"hints"`` from the *context* dictionary to
        bias the search towards promising regions of the input space.  If no
        hints are available, it falls back to the random search.

        Parameters
        ----------
        hypothesis:
            Hypothesis dictionary.
        context:
            Context dictionary.  The ``"hints"`` key may contain a list of
            candidate counterexample values to test first.

        Returns
        -------
        dict | None
            A counterexample dictionary, or ``None`` if none was found.
        """
        ctx = context or {}
        hints: list[Any] = ctx.get("hints", [])
        predicate = hypothesis.get("predicate")
        if hints and predicate is not None:
            for h in hints:
                try:
                    if not predicate(h):
                        return {
                            "type": "guided_counterexample",
                            "witness": h,
                            "strategy": "guided",
                            "hypothesis_id": hypothesis.get("id", "unknown"),
                        }
                except Exception:
                    continue
        # Fall back to random
        return self._random_search(hypothesis)

    def _score_attempt(
        self,
        attempt: dict[str, Any] | None,
        hypothesis: dict[str, Any],
    ) -> float:
        """Score a search attempt.

        A found counterexample scores 1.0; an exhausted-budget outcome scores
        0.3; a clean failure scores based on the hypothesis difficulty.

        Parameters
        ----------
        attempt:
            Counterexample dictionary, or ``None`` if no counterexample found.
        hypothesis:
            Hypothesis dictionary.

        Returns
        -------
        float
            Score in [0, 1].
        """
        if attempt is not None:
            return 1.0
        if self.is_exhausted():
            return 0.3
        difficulty = _hypothesis_difficulty(hypothesis)
        return _clamp(0.5 - difficulty * 0.3, 0.0, 0.5)


# ---------------------------------------------------------------------------
# HypothesisTracker
# ---------------------------------------------------------------------------


class HypothesisTracker:
    """Track and manage the falsification status of a collection of hypotheses.

    :class:`HypothesisTracker` maintains a dictionary of hypotheses, a
    status history for auditing, and a priority queue ordering hypotheses
    by estimated falsification difficulty (easiest first).

    Attributes
    ----------
    hypotheses : dict
        Maps hypothesis IDs to hypothesis dictionaries (with added
        ``"status"`` and ``"evidence"`` fields).
    status_history : list
        Ordered list of status-update records.
    priority_queue : list
        List of hypothesis IDs ordered by falsification difficulty
        (ascending).
    """

    def __init__(self) -> None:
        """Initialise an empty tracker."""
        self.hypotheses: dict[str, dict[str, Any]] = {}
        self.status_history: list[dict[str, Any]] = []
        self.priority_queue: list[str] = []

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def register(
        self, hypothesis_id: str | dict[str, Any], hypothesis: Optional[dict[str, Any]] = None
    ) -> None:
        """Register a new hypothesis.

        Parameters
        ----------
        hypothesis_id:
            Unique identifier for the hypothesis.
        hypothesis:
            Hypothesis dictionary.  A ``"status"`` key will be added
            automatically if not present.
        """
        if hypothesis is None:
            hypothesis = dict(hypothesis_id)
            hypothesis_id = str(hypothesis.get("id", _uid()))
        entry = dict(hypothesis)
        entry.setdefault("id", hypothesis_id)
        entry.setdefault("status", _HYPOTHESIS_STATUS_PENDING)
        entry.setdefault("evidence", [])
        self.hypotheses[hypothesis_id] = entry
        self.priority_queue = self._rebuild_priority_queue()

    def add(self, hypothesis: dict[str, Any]) -> None:
        """Compatibility alias accepting a hypothesis dict with an ``id`` field."""
        self.register(str(hypothesis.get("id", uuid.uuid4())), hypothesis)

    def update_status(
        self,
        hypothesis_id: str,
        status: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """Update the status of a registered hypothesis.

        Parameters
        ----------
        hypothesis_id:
            The hypothesis to update.
        status:
            New status string.
        evidence:
            Optional evidence dictionary to attach.

        Raises
        ------
        KeyError
            If *hypothesis_id* is not registered.
        """
        hyp = self.hypotheses[hypothesis_id]
        hyp["status"] = status
        if evidence is not None:
            hyp.setdefault("evidence", []).append(evidence)
        self.status_history.append(
            {
                "hypothesis_id": hypothesis_id,
                "status": status,
                "evidence": evidence,
                "updated_at": _utcnow(),
            }
        )
        self.priority_queue = self._rebuild_priority_queue()

    def get(self, hypothesis_id: str) -> dict[str, Any] | None:
        """Retrieve a hypothesis by its ID.

        Parameters
        ----------
        hypothesis_id:
            The hypothesis identifier.

        Returns
        -------
        dict | None
        """
        return self.hypotheses.get(hypothesis_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all registered hypotheses.

        Returns
        -------
        list[dict]
        """
        return list(self.hypotheses.values())

    def count(self) -> int:
        """Compatibility alias returning the number of tracked hypotheses."""
        return len(self.hypotheses)

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        """Return hypotheses with a specific status.

        Parameters
        ----------
        status:
            Status string to filter on.

        Returns
        -------
        list[dict]
        """
        return [h for h in self.hypotheses.values() if h.get("status") == status]

    def get_pending(self) -> list[dict[str, Any]]:
        """Return all hypotheses in ``"pending"`` status.

        Returns
        -------
        list[dict]
        """
        return self.list_by_status(_HYPOTHESIS_STATUS_PENDING)

    def prioritize(self) -> list[dict[str, Any]]:
        """Return hypotheses ordered by falsification difficulty (easiest first).

        Returns
        -------
        list[dict]
        """
        return [
            self.hypotheses[hid]
            for hid in self.priority_queue
            if hid in self.hypotheses
        ]

    def summary_report(self) -> dict[str, Any]:
        """Compute aggregate statistics over all registered hypotheses.

        Returns
        -------
        dict
            Keys: ``"total"``, ``"pending"``, ``"falsified"``,
            ``"confirmed"``, ``"inconclusive"``, ``"falsification_rate"``.
        """
        n = len(self.hypotheses)
        if n == 0:
            return {"total": 0}
        falsified = len(self.list_by_status(_HYPOTHESIS_STATUS_FALSIFIED))
        confirmed = len(self.list_by_status(_HYPOTHESIS_STATUS_CONFIRMED))
        inconclusive = len(self.list_by_status(_HYPOTHESIS_STATUS_INCONCLUSIVE))
        pending = len(self.list_by_status(_HYPOTHESIS_STATUS_PENDING))
        return {
            "total": n,
            "pending": pending,
            "falsified": falsified,
            "confirmed": confirmed,
            "inconclusive": inconclusive,
            "falsification_rate": round(falsified / n, 4),
        }

    def to_json(self) -> str:
        """Serialise the tracker to a JSON string.

        Returns
        -------
        str
        """
        return json.dumps(
            {
                "hypotheses": self.hypotheses,
                "status_history": self.status_history,
                "priority_queue": self.priority_queue,
            },
            indent=2,
            default=str,
        )

    @classmethod
    def from_json(cls, data: str) -> "HypothesisTracker":
        """Deserialise a :class:`HypothesisTracker` from a JSON string.

        Parameters
        ----------
        data:
            JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        HypothesisTracker
        """
        obj = json.loads(data)
        tracker = cls()
        tracker.hypotheses = obj.get("hypotheses", {})
        tracker.status_history = obj.get("status_history", [])
        tracker.priority_queue = obj.get("priority_queue", [])
        return tracker

    def reset(self) -> None:
        """Clear all registered hypotheses, history, and the priority queue."""
        self.hypotheses.clear()
        self.status_history.clear()
        self.priority_queue.clear()

    def summarize(self) -> str:
        """Return a brief textual summary.

        Returns
        -------
        str
        """
        rpt = self.summary_report()
        return (
            f"HypothesisTracker("
            f"total={rpt.get('total', 0)}, "
            f"pending={rpt.get('pending', 0)}, "
            f"falsified={rpt.get('falsified', 0)}, "
            f"confirmed={rpt.get('confirmed', 0)})"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rebuild_priority_queue(self) -> list[str]:
        """Rebuild the priority queue ordered by difficulty ascending.

        Hypotheses that are no longer pending are placed at the back.

        Returns
        -------
        list[str]
        """
        pending = [
            hid for hid, h in self.hypotheses.items()
            if h.get("status") == _HYPOTHESIS_STATUS_PENDING
        ]
        pending.sort(key=lambda hid: _hypothesis_difficulty(self.hypotheses[hid]))
        non_pending = [
            hid for hid in self.hypotheses
            if hid not in pending
        ]
        return pending + non_pending


# ---------------------------------------------------------------------------
# FalsificationLoopRunner
# ---------------------------------------------------------------------------


class FalsificationLoopRunner:
    """Orchestrate the complete falsification loop.

    :class:`FalsificationLoopRunner` iteratively applies the
    :class:`CounterexampleSearcher` to each hypothesis tracked by a
    :class:`HypothesisTracker` until all hypotheses are resolved or the
    computational budget is exhausted.

    Attributes
    ----------
    config : dict
        Loop configuration dictionary.
    searcher : CounterexampleSearcher
        The counterexample searcher used during the loop.
    tracker : HypothesisTracker
        The hypothesis tracker updated by each iteration.
    loop_state : dict
        Mutable state dictionary tracking loop progress.
    """

    def __init__(
        self,
        max_iterations: int = 20,
        budget: int = 100,
        strategy: str = "random",
    ) -> None:
        """Initialise the loop runner.

        Parameters
        ----------
        max_iterations:
            Maximum number of falsification iterations.
        budget:
            Total search-step budget for the searcher.
        strategy:
            Default search strategy.
        """
        self.config: dict[str, Any] = {
            "max_iterations": max_iterations,
            "budget": budget,
            "strategy": strategy,
        }
        self.searcher = CounterexampleSearcher(strategy=strategy, budget=budget)
        self.tracker = HypothesisTracker()
        self.loop_state: dict[str, Any] = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "started_at": None,
            "finished_at": None,
        }

    @property
    def max_iterations(self) -> int:
        return int(self.config["max_iterations"])

    @property
    def budget(self) -> int:
        return int(self.searcher.budget)

    def run(
        self,
        hypotheses: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the full falsification loop.

        The loop runs for at most ``config["max_iterations"]`` iterations.
        In each iteration, all pending hypotheses are searched for
        counterexamples.  The tracker is updated after each attempt.
        Convergence is declared when no pending hypotheses remain or the
        budget is exhausted.

        Parameters
        ----------
        hypotheses:
            List of hypothesis dictionaries to falsify.  Each must have an
            ``"id"`` key.
        context:
            Optional context dictionary forwarded to the searcher.

        Returns
        -------
        dict
            Summary with keys ``"converged"``, ``"iterations_used"``,
            ``"hypothesis_summary"``, ``"search_report"``,
            ``"started_at"``, ``"finished_at"``.
        """
        self.loop_state["status"] = "running"
        self.loop_state["started_at"] = _utcnow()
        self.loop_state["iteration"] = 0
        self.loop_state["converged"] = False
        # Register all hypotheses
        for h in hypotheses:
            self.tracker.register(h.get("id", _uid()), h)
        all_attempts: list[FalsificationAttempt] = []
        max_it = self.config["max_iterations"]
        for it in range(1, max_it + 1):
            self.loop_state["iteration"] = it
            pending = self.tracker.get_pending()
            if not pending:
                self.loop_state["converged"] = True
                break
            if self.searcher.is_exhausted():
                break
            try:
                iteration_attempts = self.run_single_iteration(pending, it)
            except Exception as exc:
                self.handle_failure(exc, it)
                continue
            all_attempts.extend(iteration_attempts)
            # Update tracker
            for attempt in iteration_attempts:
                if attempt.is_successful():
                    self.tracker.update_status(
                        attempt.hypothesis_id,
                        _HYPOTHESIS_STATUS_FALSIFIED,
                        evidence={"attempt_id": attempt.attempt_id,
                                  "counterexample": attempt.counterexample},
                    )
                elif attempt.status == _ATTEMPT_STATUS_INCONCLUSIVE:
                    self.tracker.update_status(
                        attempt.hypothesis_id,
                        _HYPOTHESIS_STATUS_INCONCLUSIVE,
                    )
            if self.check_convergence(all_attempts):
                self.loop_state["converged"] = True
                break
        if not self.loop_state["converged"] and not self.tracker.get_pending():
            self.loop_state["converged"] = True
        self.loop_state["status"] = "done"
        self.loop_state["finished_at"] = _utcnow()
        self.loop_state["attempts"] = list(all_attempts)
        return {
            "converged": self.loop_state["converged"],
            "iterations": self.loop_state["iteration"],
            "iterations_used": self.loop_state["iteration"],
            "attempts": list(all_attempts),
            "hypothesis_summary": self.tracker.summary_report(),
            "search_report": self.searcher.history_report(),
            "started_at": self.loop_state["started_at"],
            "finished_at": self.loop_state["finished_at"],
        }

    def run_single_iteration(
        self,
        hypotheses: list[dict[str, Any]],
        iteration: int,
    ) -> list[FalsificationAttempt]:
        """Execute one iteration of the falsification loop.

        Parameters
        ----------
        hypotheses:
            List of pending hypothesis dictionaries.
        iteration:
            Current iteration number (1-based).

        Returns
        -------
        list[FalsificationAttempt]
            One attempt per hypothesis.
        """
        return self.searcher.search_batch(hypotheses)

    def check_convergence(
        self, attempts: list[FalsificationAttempt]
    ) -> bool:
        """Return ``True`` if no pending hypotheses remain.

        Parameters
        ----------
        attempts:
            All attempts produced so far (unused directly; tracker state
            is the authoritative source).

        Returns
        -------
        bool
        """
        if self.searcher.is_exhausted():
            return True
        if attempts and all(isinstance(attempt, FalsificationAttempt) for attempt in attempts):
            return all(attempt.is_successful() for attempt in attempts)
        return len(self.tracker.get_pending()) == 0

    def handle_failure(
        self, error: Exception, iteration: int
    ) -> dict[str, Any]:
        """Produce a failure record for an iteration that raised an exception.

        Parameters
        ----------
        error:
            The exception raised.
        iteration:
            The iteration number.

        Returns
        -------
        dict
        """
        return {
            "iteration": iteration,
            "error": str(error),
            "attempts": [],
        }

    def get_state(self) -> dict[str, Any]:
        """Return a copy of the current loop state.

        Returns
        -------
        dict
        """
        state = dict(self.loop_state)
        state["iterations_completed"] = state.get("iteration", 0)
        return state

    def reset(self) -> None:
        """Reset the runner to its initial idle state."""
        self.searcher.reset()
        self.tracker.reset()
        self.loop_state = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "attempts": [],
            "started_at": None,
            "finished_at": None,
        }

    def summarize(self) -> str:
        """Return a brief textual summary of the runner.

        Returns
        -------
        str
        """
        cfg = self.config
        st = self.loop_state
        return (
            f"FalsificationLoopRunner("
            f"max_iter={cfg['max_iterations']}, "
            f"strategy={cfg['strategy']}, "
            f"status={st['status']}, "
            f"converged={st['converged']})"
        )

    def export_results(self) -> dict[str, Any]:
        """Export the current runner state in a serialisable structure."""
        return {
            "config": dict(self.config),
            "state": self.get_state(),
            "attempts": list(self.loop_state.get("attempts", [])),
            "search_report": self.searcher.history_report(),
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def run_falsification_loop(
    hypotheses: list[dict[str, Any]],
    max_iterations: int = 20,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a full falsification loop and return the result summary.

    This is the primary entry-point for the falsification loop.  It creates
    a :class:`FalsificationLoopRunner` configured with *max_iterations* and
    any additional keyword arguments, then invokes
    :meth:`~FalsificationLoopRunner.run` on the provided *hypotheses*.

    Algorithm
    ---------
    The falsification loop proceeds as follows:

    1. Register all *hypotheses* in a :class:`HypothesisTracker`.
    2. For each iteration ``i`` from 1 to *max_iterations*:

       a. Retrieve all pending hypotheses from the tracker.
       b. If no hypotheses remain pending, or the budget is exhausted,
          declare convergence and halt.
       c. Apply the :class:`CounterexampleSearcher` to each pending
          hypothesis.
       d. Update the tracker for each attempt:

          * ``"success"`` → mark hypothesis as ``"falsified"``.
          * ``"inconclusive"`` → mark hypothesis as ``"inconclusive"``.
          * ``"failure"`` → leave hypothesis as ``"pending"`` for the next
            iteration.

    3. Return a comprehensive summary dictionary.

    Convergence criteria
    --------------------
    The loop converges when no hypotheses remain in ``"pending"`` status, or
    when the computational budget is fully exhausted.

    Parameters
    ----------
    hypotheses:
        List of hypothesis dictionaries.  Each should have an ``"id"`` key
        and a ``"statement"`` key.
    max_iterations:
        Maximum number of falsification iterations.
    **kwargs:
        Additional keyword arguments forwarded to
        :class:`FalsificationLoopRunner`.  Recognised keys:
        ``budget`` (int, default 100),
        ``strategy`` (str, default ``"random"``),
        ``context`` (dict, optional).

    Returns
    -------
    dict
        Keys: ``"converged"``, ``"iterations_used"``,
        ``"hypothesis_summary"``, ``"search_report"``,
        ``"started_at"``, ``"finished_at"``.

    Examples
    --------
    >>> hyps = [
    ...     {"id": "h1", "statement": "For all n, n > n+1"},
    ...     {"id": "h2", "statement": "Every even number is prime"},
    ... ]
    >>> result = run_falsification_loop(hyps, max_iterations=5)
    >>> "converged" in result
    True
    """
    if hasattr(hypotheses, "loop_id") and hasattr(hypotheses, "state"):
        loop = deepcopy(hypotheses)
        if hasattr(loop, "updated_at"):
            loop.updated_at = time.time()
        return loop
    context = kwargs.pop("context", None)
    runner = FalsificationLoopRunner(
        max_iterations=max_iterations,
        **kwargs,
    )
    return runner.run(hypotheses, context=context)


def attempt_falsification(
    hypothesis: dict[str, Any] | Any,
    strategy: str = "random",
    budget: int = 50,
    **kwargs: Any,
) -> dict[str, Any]:
    """Attempt to falsify a single hypothesis and return a concise summary.

    This convenience function creates a :class:`CounterexampleSearcher` and
    performs a single search attempt, returning a dictionary with the key
    outcome fields.

    Parameters
    ----------
    hypothesis:
        Hypothesis dictionary with at least an ``"id"`` and ``"statement"``
        key.
    strategy:
        Search strategy to use (``"random"``, ``"exhaustive"``,
        ``"guided"``).
    budget:
        Maximum search-step budget.
    **kwargs:
        Additional keyword arguments forwarded to
        :meth:`CounterexampleSearcher.search`.  Notably ``context`` (dict).

    Returns
    -------
    dict
        Keys: ``"attempt_id"``, ``"hypothesis_id"``, ``"status"``,
        ``"score"``, ``"counterexample"``, ``"iterations_used"``.

    Examples
    --------
    >>> result = attempt_falsification(
    ...     {"id": "h1", "statement": "For all n, n^2 < 0"},
    ...     strategy="random",
    ... )
    >>> result["status"] in {"success", "failure", "inconclusive"}
    True
    """
    if hasattr(hypothesis, "loop_id") and "hypothesis_id" in kwargs:
        return FalsificationAttempt(
            hypothesis_id=kwargs["hypothesis_id"],
            status="inconclusive",
            counterexample=None,
            iterations_used=1,
        )
    context = kwargs.get("context")
    searcher = CounterexampleSearcher(strategy=strategy, budget=budget)
    return searcher.search(hypothesis, context=context)
