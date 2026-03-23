"""Treaty formation for hypercover synthesis.

Chapter 41 of theory2.tex §41.6 introduces the *treaty formation process*:
given a SynthesisOutcome containing mined OverlapLaws and a collection of
TreatyCandidates, the formation process produces a set of ratified OverlapTreaty
objects that can be handed to the descent engine.

Treaty formation proceeds in three stages:
1. Negotiation — candidates compete; the highest-scoring candidate per patch
   pair is selected.
2. Dependency resolution — DependentTreaty objects are topologically sorted and
   resolved in dependency order.
3. Validation — each formed treaty is checked for overlap-law compliance and
   descent compatibility.

The formation process maintains an explicit audit trail (provenance tuples)
so that every treaty can be traced back to its originating synthesis record.
"""
from __future__ import annotations

import collections
import dataclasses
import itertools
import logging
import time
from typing import Any

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy, OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind, Coordinate
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal,
        GoalPriority, GoalStatus, OverlapGoal,
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

from jugeo.generation.hypercover_treaties.models import (
    HypercoverSynthesisRecord, TreatyCandidate, OverlapLaw, DependentTreaty,
    SynthesisOutcome, SynthesisPhase, LawStability, CandidateSource, TreatyRole,
    OutcomeKind, SynthesisConfig, OverlapLawIndex,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FormationReport
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FormationReport:
    """Immutable summary of a single treaty formation run.

    Attributes:
        treaties_formed: Number of OverlapTreaty objects successfully formed
            and validated.
        candidates_rejected: Number of TreatyCandidate objects that lost
            negotiation or failed validation.
        validation_failures: Number of treaties that failed the validation
            stage (overlap-law or descent compatibility check).
        resolution_cycles_broken: Number of dependency cycles broken during
            topological resolution.
        provenance: Tuple of human-readable provenance strings tracing this
            report back to its originating synthesis record.
    """

    treaties_formed: int
    candidates_rejected: int
    validation_failures: int
    resolution_cycles_broken: int
    provenance: tuple[str, ...]


def build_formation_report(
    *,
    treaties_formed: int = 0,
    candidates_rejected: int = 0,
    validation_failures: int = 0,
    resolution_cycles_broken: int = 0,
    provenance: tuple[str, ...] = (),
) -> FormationReport:
    """Factory function for :class:`FormationReport`.

    Accepts keyword-only arguments so callers can omit fields that are not
    relevant.  All numeric fields default to ``0``; *provenance* defaults to
    the empty tuple.

    Example::

        report = build_formation_report(
            treaties_formed=5,
            candidates_rejected=2,
            provenance=("record-abc", "outcome-xyz"),
        )
    """
    if treaties_formed < 0:
        raise ValueError("treaties_formed must be non-negative")
    if candidates_rejected < 0:
        raise ValueError("candidates_rejected must be non-negative")
    if validation_failures < 0:
        raise ValueError("validation_failures must be non-negative")
    if resolution_cycles_broken < 0:
        raise ValueError("resolution_cycles_broken must be non-negative")
    return FormationReport(
        treaties_formed=treaties_formed,
        candidates_rejected=candidates_rejected,
        validation_failures=validation_failures,
        resolution_cycles_broken=resolution_cycles_broken,
        provenance=tuple(provenance),
    )


# ---------------------------------------------------------------------------
# FormationValidator
# ---------------------------------------------------------------------------


class FormationValidator:
    """Validates treaties against overlap laws and descent conditions.

    The validator checks two orthogonal properties for each treaty:

    1. *Overlap-law compliance* — every clause expectation matches a known
       OverlapLaw in the supplied index.
    2. *Descent compatibility* — the patches tuple is structurally valid
       (non-empty, no duplicates, at least two patches so that a cover pair
       can be formed).

    When *strict_mode* is ``True`` the validator treats any unmatched clause
    as a hard failure.  In lenient mode a warning is recorded but the treaty
    is still considered valid so that development can continue with partial
    law coverage.

    Args:
        law_index: The OverlapLawIndex used for compliance checking.
        strict_mode: Whether unmatched clauses are hard failures.
    """

    def __init__(self, law_index: OverlapLawIndex, strict_mode: bool = True) -> None:
        self._law_index: OverlapLawIndex = law_index
        self._strict_mode: bool = strict_mode

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def validate(self, treaty: Any) -> tuple[bool, list[str]]:
        """Validate a single treaty (OverlapTreaty or DependentTreaty).

        Dispatches on whether the object has a ``clauses`` attribute and
        optionally a ``patches`` attribute.  Objects lacking both attributes
        are rejected immediately.

        Returns:
            ``(valid, issues)`` — *valid* is ``True`` iff no hard failures
            were found; *issues* lists diagnostic messages (may be non-empty
            even when *valid* is ``True`` in lenient mode).
        """
        if treaty is None:
            return False, ["treaty is None"]

        if not hasattr(treaty, "clauses"):
            return False, [f"unsupported treaty type: {type(treaty).__name__}"]

        issues: list[str] = []
        is_valid = True

        # Stage 1: overlap-law compliance.
        law_ok, law_violations = self.check_overlap_law_compliance(treaty)
        if not law_ok:
            if self._strict_mode:
                is_valid = False
            issues.extend(law_violations)

        # Stage 2: descent compatibility (requires patches attribute).
        if hasattr(treaty, "patches"):
            compat_ok, compat_issues = self.check_descent_compatibility(treaty)
            if not compat_ok:
                is_valid = False
                issues.extend(compat_issues)

        return is_valid, issues

    def check_overlap_law_compliance(self, treaty: Any) -> tuple[bool, list[str]]:
        """Check that every clause expectation is covered by an OverlapLaw.

        For each TreatyClause in ``treaty.clauses`` the method searches the
        law index for a law whose ``predicate_description`` appears in or
        contains the clause ``expectation`` string.

        Returns:
            ``(compliant, violations)`` where *violations* lists the
            expectations that could not be matched.
        """
        if not hasattr(treaty, "clauses"):
            return True, []

        violations: list[str] = []
        clauses = treaty.clauses if treaty.clauses is not None else ()

        for clause in clauses:
            # Support both object-style and dict-style clauses.
            if isinstance(clause, dict):
                expectation = clause.get("expectation")
            else:
                expectation = getattr(clause, "expectation", None)

            if expectation is None:
                continue

            if not self._find_matching_law(str(expectation)):
                patch = (
                    clause.get("patch") if isinstance(clause, dict)
                    else getattr(clause, "patch", "?")
                )
                msg = (
                    f"clause expectation {expectation!r} has no matching "
                    f"OverlapLaw in index (patch={patch!r})"
                )
                violations.append(msg)
                logger.debug("FormationValidator: %s", msg)

        return len(violations) == 0, violations

    def check_descent_compatibility(self, treaty: Any) -> tuple[bool, list[str]]:
        """Check that the treaty's patches form a structurally valid cover pair.

        Rules:
        * ``patches`` is non-empty.
        * ``patches`` has at least two distinct elements.
        * No element appears twice (no duplicates).

        Returns:
            ``(compatible, issues)``
        """
        issues: list[str] = []
        patches = getattr(treaty, "patches", None)

        if not patches:
            issues.append(
                "treaty.patches is empty; descent requires at least two patches"
            )
            return False, issues

        patches_list = list(patches)

        if len(patches_list) < 2:
            issues.append(
                f"treaty has only {len(patches_list)} patch(es) "
                f"({patches_list}); a valid cover requires at least two"
            )
            return False, issues

        seen: set[str] = set()
        for p in patches_list:
            if p in seen:
                issues.append(
                    f"duplicate patch {p!r} in treaty.patches; patches must be distinct"
                )
            seen.add(p)

        if issues:
            return False, issues

        # Verify at least one overlap pair can be formed.
        pair_count = sum(1 for _ in itertools.combinations(patches_list, 2))
        if pair_count == 0:
            issues.append("no overlap pairs can be formed from patches")
            return False, issues

        return True, []

    def validate_batch(
        self, treaties: list[Any]
    ) -> dict[str, tuple[bool, list[str]]]:
        """Validate a batch of treaties.

        Keyed by ``treaty.treaty_id`` if the attribute is present, otherwise
        by ``str(id(treaty))``.

        Returns:
            Dict mapping treaty key → ``(valid, issues)``.
        """
        results: dict[str, tuple[bool, list[str]]] = {}
        for treaty in treaties:
            key = str(getattr(treaty, "treaty_id", None) or id(treaty))
            results[key] = self.validate(treaty)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_matching_law(self, expectation: str) -> bool:
        """Return True if any law in the index matches *expectation*."""
        laws: list[Any] = []
        if hasattr(self._law_index, "laws"):
            laws = list(self._law_index.laws)
        elif hasattr(self._law_index, "all_laws"):
            laws = list(self._law_index.all_laws())
        elif hasattr(self._law_index, "values"):
            laws = list(self._law_index.values())

        for law in laws:
            pred = str(getattr(law, "predicate_description", "") or "")
            if pred and (pred in expectation or expectation in pred):
                return True
        return False


# ---------------------------------------------------------------------------
# DependencyResolver
# ---------------------------------------------------------------------------


class DependencyResolver:
    """Resolves DependentTreaty objects in topological dependency order.

    Kahn's algorithm is applied to the DAG formed by the ``depends_on``
    fields of each DependentTreaty.  Cycles are detected with DFS three-colour
    marking and broken by removing the lowest-priority treaty in each cycle.

    After resolution every treaty in :attr:`resolution_order` references only
    treaties that appear earlier in the list.

    Attributes:
        _resolved: Maps treaty_id → resolved DependentTreaty.
        _resolution_order: Treaty ids in resolution order.
    """

    def __init__(self) -> None:
        self._resolved: dict[str, DependentTreaty] = {}
        self._resolution_order: list[str] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def resolve(self, treaties: list[DependentTreaty]) -> list[DependentTreaty]:
        """Resolve a list of DependentTreaty objects in dependency order.

        Returns:
            DependentTreaty objects ordered so that all dependencies of a
            treaty appear before it in the list.
        """
        if not treaties:
            return []

        working = list(treaties)
        cycles_broken = 0

        while True:
            cycles = self.detect_cycles(working)
            if not cycles:
                break
            for cycle in cycles:
                logger.warning(
                    "DependencyResolver: breaking cycle %s (len=%d)",
                    cycle, len(cycle),
                )
                working = self.break_cycle(cycle, working)
                cycles_broken += 1
            if cycles_broken > len(treaties) * 2:
                logger.error(
                    "DependencyResolver: excessive cycles; aborting resolution"
                )
                break

        sorted_treaties = self.topological_sort(working)

        self._resolved.clear()
        self._resolution_order.clear()
        for t in sorted_treaties:
            tid = str(getattr(t, "treaty_id", id(t)))
            self._resolved[tid] = t
            self._resolution_order.append(tid)

        logger.info(
            "DependencyResolver: resolved %d treaties (%d cycles broken)",
            len(sorted_treaties), cycles_broken,
        )
        return sorted_treaties

    def topological_sort(
        self, treaties: list[DependentTreaty]
    ) -> list[DependentTreaty]:
        """Sort treaties topologically using Kahn's algorithm.

        Returns:
            Topologically ordered list.  Any nodes not reachable via the
            dependency graph are appended at the end.
        """
        if not treaties:
            return []

        id_to_treaty: dict[str, DependentTreaty] = {
            str(getattr(t, "treaty_id", id(t))): t for t in treaties
        }
        graph = self._build_dep_graph(treaties)
        order = self._compute_resolution_order(graph)

        result: list[DependentTreaty] = []
        seen: set[str] = set()
        for tid in order:
            if tid in id_to_treaty and tid not in seen:
                result.append(id_to_treaty[tid])
                seen.add(tid)

        for t in treaties:
            tid = str(getattr(t, "treaty_id", id(t)))
            if tid not in seen:
                result.append(t)

        return result

    def detect_cycles(
        self, treaties: list[DependentTreaty]
    ) -> list[list[str]]:
        """Detect all cycles in the dependency graph using DFS.

        Returns:
            List of cycles; each cycle is an ordered list of treaty_ids.
            Returns ``[]`` if the graph is acyclic.
        """
        graph = self._build_dep_graph(treaties)
        all_nodes: set[str] = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)

        colour: dict[str, int] = {n: 0 for n in all_nodes}
        path: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            colour[node] = 1
            path.append(node)
            for neighbour in graph.get(node, []):
                c = colour.get(neighbour, 0)
                if c == 1:
                    start_idx = path.index(neighbour)
                    cycles.append(list(path[start_idx:]))
                elif c == 0:
                    dfs(neighbour)
            path.pop()
            colour[node] = 2

        for node in list(all_nodes):
            if colour[node] == 0:
                dfs(node)

        return cycles

    def break_cycle(
        self, cycle: list[str], treaties: list[DependentTreaty]
    ) -> list[DependentTreaty]:
        """Remove the lowest-priority treaty in *cycle* from *treaties*.

        Tie-break: among equal-priority treaties, the lexicographically
        largest ``treaty_id`` is removed for determinism.

        Returns:
            New list of DependentTreaty objects with the chosen treaty removed.
        """
        id_to_treaty: dict[str, DependentTreaty] = {
            str(getattr(t, "treaty_id", id(t))): t for t in treaties
        }
        candidates = [id_to_treaty[tid] for tid in cycle if tid in id_to_treaty]
        if not candidates:
            return treaties

        def _sort_key(t: DependentTreaty) -> tuple[int, str]:
            prio = int(getattr(t, "priority", 0))
            tid = str(getattr(t, "treaty_id", id(t)))
            return (-prio, tid)  # lowest priority first; largest id first on tie

        to_remove_id = str(getattr(sorted(candidates, key=_sort_key)[0], "treaty_id",
                                   id(sorted(candidates, key=_sort_key)[0])))
        logger.debug(
            "DependencyResolver.break_cycle: removing %r from cycle %s",
            to_remove_id, cycle,
        )
        return [
            t for t in treaties
            if str(getattr(t, "treaty_id", id(t))) != to_remove_id
        ]

    def _build_dep_graph(
        self, treaties: list[DependentTreaty]
    ) -> dict[str, list[str]]:
        """Build adjacency dict: treaty_id → list of dependency ids."""
        graph: dict[str, list[str]] = {}
        for t in treaties:
            tid = str(getattr(t, "treaty_id", id(t)))
            deps = list(getattr(t, "depends_on", None) or [])
            graph[tid] = [str(d) for d in deps]
        return graph

    def _compute_resolution_order(
        self, graph: dict[str, list[str]]
    ) -> list[str]:
        """Kahn's algorithm topological sort.

        Returns:
            Node ids in topological order.  Shorter than all nodes if a
            cycle remains (caller should have pre-broken all cycles).
        """
        all_nodes: set[str] = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)

        # in_degree[n] = number of nodes that n depends on.
        in_degree: dict[str, int] = {n: 0 for n in all_nodes}
        for node, deps in graph.items():
            in_degree[node] = len(deps)

        queue: collections.deque[str] = collections.deque(
            n for n in all_nodes if in_degree[n] == 0
        )
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for candidate, deps in graph.items():
                if node in deps:
                    in_degree[candidate] -= 1
                    if in_degree[candidate] == 0:
                        queue.append(candidate)

        return order

    @property
    def resolution_order(self) -> list[str]:
        """Treaty ids in resolution order from the last :meth:`resolve` call."""
        return list(self._resolution_order)


# ---------------------------------------------------------------------------
# TreatyNegotiator
# ---------------------------------------------------------------------------


class TreatyNegotiator:
    """Selects the best TreatyCandidate for each patch pair.

    Given a list of TreatyCandidate objects covering the same patch pair, the
    negotiator computes a composite score for each and selects the winner.
    Ties are broken deterministically by ``candidate_id`` lexicographic order.

    Score formula (theory2.tex §41.6.1)::

        score = confidence * weight_confidence
              + len(supporting_evidence) * weight_evidence
              - counterexample_count * 0.1

    Args:
        weight_confidence: Multiplier for the confidence component (default 1.0).
        weight_evidence: Multiplier per supporting evidence item (default 0.05).
    """

    def __init__(
        self,
        weight_confidence: float = 1.0,
        weight_evidence: float = 0.05,
    ) -> None:
        self._candidates: list[TreatyCandidate] = []
        self._weight_confidence: float = weight_confidence
        self._weight_evidence: float = weight_evidence

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def negotiate(
        self, candidates: list[TreatyCandidate]
    ) -> list[TreatyCandidate]:
        """Negotiate over a list of candidates and return one winner per pair.

        Groups candidates by ``patch_pair`` attribute (or ``("", "")`` for
        candidates without one) and calls :meth:`pick_winner` on each group.

        Returns:
            One winner per distinct patch pair.
        """
        self._candidates = list(candidates)
        if not candidates:
            return []

        groups = self._group_by_patch_pair(candidates)
        winners: list[TreatyCandidate] = []

        for pair_key, group in groups.items():
            winner = self.pick_winner(group)
            winners.append(winner)
            logger.debug(
                "TreatyNegotiator: pair %s → winner %s "
                "(score=%.3f, %d candidates evaluated)",
                pair_key,
                getattr(winner, "candidate_id", "?"),
                self.score_candidate(winner),
                len(group),
            )

        return winners

    def score_candidate(self, c: TreatyCandidate) -> float:
        """Compute the composite score for *c*.

        Missing attributes are treated as their zero-value so that the scorer
        degrades gracefully when TreatyCandidate is partially populated.
        """
        confidence = float(getattr(c, "confidence", 0.0))
        evidence = list(getattr(c, "supporting_evidence", []) or [])
        counterexamples = int(getattr(c, "counterexample_count", 0))

        return (
            confidence * self._weight_confidence
            + len(evidence) * self._weight_evidence
            - counterexamples * 0.1
        )

    def pick_winner(
        self, candidates: list[TreatyCandidate]
    ) -> TreatyCandidate:
        """Return the highest-scoring candidate; break ties with :meth:`_resolve_ties`."""
        if not candidates:
            raise ValueError("pick_winner called with empty candidate list")

        max_score = max(self.score_candidate(c) for c in candidates)
        top_group = [c for c in candidates if self.score_candidate(c) == max_score]

        if len(top_group) == 1:
            return top_group[0]
        return self._resolve_ties(top_group)

    def _group_by_patch_pair(
        self, candidates: list[TreatyCandidate]
    ) -> dict[tuple[str, str], list[TreatyCandidate]]:
        """Group candidates by their ``patch_pair`` attribute."""
        groups: dict[tuple[str, str], list[TreatyCandidate]] = {}
        for c in candidates:
            pair = getattr(c, "patch_pair", None)
            if pair and len(pair) >= 2:
                key = (str(pair[0]), str(pair[1]))
            else:
                key = ("", "")
            groups.setdefault(key, []).append(c)
        return groups

    def _resolve_ties(
        self, tied: list[TreatyCandidate]
    ) -> TreatyCandidate:
        """Return the candidate with the lexicographically smallest candidate_id."""
        return min(tied, key=lambda c: str(getattr(c, "candidate_id", id(c))))


# ---------------------------------------------------------------------------
# TreatyFormationProcess
# ---------------------------------------------------------------------------


class TreatyFormationProcess:
    """Orchestrates the full treaty formation pipeline.

    Pipeline stages:
    1. :class:`TreatyNegotiator` — one winner per patch pair.
    2. :class:`DependencyResolver` — topological ordering of dependencies.
    3. :class:`FormationValidator` — structural and law-compliance checks.

    The primary input is a :class:`SynthesisOutcome`; its ``accepted_laws``
    are converted to :class:`OverlapTreaty` objects via
    :meth:`_build_treaty_from_law`.

    Args:
        config: Optional synthesis configuration.
        strict_validation: Forwarded to FormationValidator; defaults to True.
    """

    def __init__(
        self,
        config: SynthesisConfig | None = None,
        strict_validation: bool = True,
    ) -> None:
        self._config: SynthesisConfig | None = config
        self._negotiator = TreatyNegotiator()
        self._resolver = DependencyResolver()
        self._validator = FormationValidator(
            OverlapLawIndex(), strict_mode=strict_validation
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def form_from_synthesis(
        self, outcome: SynthesisOutcome
    ) -> tuple[list[Any], list[str]]:
        """Form OverlapTreaty objects from a SynthesisOutcome.

        Iterates over ``outcome.accepted_laws``, calls
        :meth:`_build_treaty_from_law` for each, validates the result, and
        collects passing treaties.

        Returns:
            ``(treaties, provenance_notes)``
        """
        if outcome is None:
            logger.warning(
                "TreatyFormationProcess.form_from_synthesis: outcome is None"
            )
            return [], ["outcome is None"]

        provenance = self._collect_provenance(outcome)
        provenance_notes: list[str] = list(provenance)
        laws = list(getattr(outcome, "accepted_laws", None) or [])

        logger.info(
            "TreatyFormationProcess: forming treaties from %d accepted laws",
            len(laws),
        )

        treaties: list[Any] = []
        failed_count = 0

        for law in laws:
            treaty = self._build_treaty_from_law(law, provenance)
            if treaty is None:
                failed_count += 1
                provenance_notes.append(
                    f"failed to build treaty for law {getattr(law, 'law_id', '?')!r}"
                )
                continue

            valid, issues = self._validator.validate(treaty)
            if not valid:
                failed_count += 1
                law_id = getattr(law, "law_id", "?")
                issues_str = "; ".join(issues)
                provenance_notes.append(
                    f"treaty for law {law_id!r} failed validation: {issues_str}"
                )
                logger.debug(
                    "TreatyFormationProcess: treaty for %s failed: %s",
                    law_id, issues_str,
                )
                continue

            treaties.append(treaty)

        provenance_notes.append(
            f"formation complete: {len(treaties)} treaties formed, "
            f"{failed_count} failed"
        )
        logger.info(
            "TreatyFormationProcess: formed %d treaties (%d failed)",
            len(treaties), failed_count,
        )
        return treaties, provenance_notes

    def negotiate(
        self, candidates: list[TreatyCandidate]
    ) -> list[TreatyCandidate]:
        """Delegate to the internal TreatyNegotiator."""
        return self._negotiator.negotiate(candidates)

    def resolve_dependencies(
        self, treaties: list[DependentTreaty]
    ) -> list[DependentTreaty]:
        """Delegate to the internal DependencyResolver."""
        return self._resolver.resolve(treaties)

    def _build_treaty_from_law(
        self, law: OverlapLaw, provenance: tuple[str, ...]
    ) -> Any | None:
        """Construct an OverlapTreaty from an OverlapLaw.

        Falls back to a plain dict when ``jugeo.generation.treaties`` is not
        importable.  Returns ``None`` only if an unexpected exception occurs.
        """
        try:
            pair = getattr(law, "patch_pair", ("?", "?"))
            left = str(pair[0]) if len(pair) >= 1 else "?"
            right = str(pair[1]) if len(pair) >= 2 else "?"
            pred = str(getattr(law, "predicate_description", "") or "")
            law_id = str(getattr(law, "law_id", "unknown-law"))

            clause = TreatyClause(  # type: ignore[name-defined]
                patch=left,
                expectation=pred,
            )
            treaty = OverlapTreaty(  # type: ignore[name-defined]
                patches=(left, right),
                clauses=(clause,),
                provenance=provenance + (f"law:{law_id}",),
            )
            return treaty
        except NameError:
            # OverlapTreaty / TreatyClause not available — use dict fallback.
            pair = getattr(law, "patch_pair", ("?", "?"))
            left = str(pair[0]) if len(pair) >= 1 else "?"
            right = str(pair[1]) if len(pair) >= 2 else "?"
            law_id = str(getattr(law, "law_id", "unknown-law"))
            return {
                "_type": "OverlapTreaty",
                "patches": (left, right),
                "clauses": [
                    {
                        "patch": left,
                        "expectation": str(
                            getattr(law, "predicate_description", "") or ""
                        ),
                    }
                ],
                "provenance": list(provenance) + [f"law:{law_id}"],
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "_build_treaty_from_law: unexpected error for law %r: %s", law, exc
            )
            return None

    def _collect_provenance(
        self, outcome: SynthesisOutcome
    ) -> tuple[str, ...]:
        """Assemble a provenance tuple from a SynthesisOutcome."""
        parts: list[str] = []

        record_id = getattr(outcome, "record_id", None)
        if record_id:
            parts.append(f"record:{record_id}")

        outcome_id = getattr(outcome, "outcome_id", None)
        if outcome_id:
            parts.append(f"outcome:{outcome_id}")

        accepted = list(getattr(outcome, "accepted_laws", None) or [])
        parts.append(f"accepted_laws:{len(accepted)}")

        rejected = list(getattr(outcome, "rejected_laws", None) or [])
        parts.append(f"rejected_laws:{len(rejected)}")

        phase = getattr(outcome, "phase", None)
        if phase is not None:
            parts.append(f"phase:{phase}")

        return tuple(parts)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def negotiator(self) -> TreatyNegotiator:
        """The underlying TreatyNegotiator."""
        return self._negotiator

    @property
    def resolver(self) -> DependencyResolver:
        """The underlying DependencyResolver."""
        return self._resolver

    @property
    def validator(self) -> FormationValidator:
        """The underlying FormationValidator."""
        return self._validator


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def make_formation_process(
    config: SynthesisConfig | None = None,
    strict_validation: bool = True,
) -> TreatyFormationProcess:
    """Convenience factory for TreatyFormationProcess.

    Creates a process with a fresh empty OverlapLawIndex.  Callers that have
    a pre-populated index should construct FormationValidator manually.
    """
    return TreatyFormationProcess(config=config, strict_validation=strict_validation)


def run_formation_pipeline(
    outcome: SynthesisOutcome,
    candidates: list[TreatyCandidate] | None = None,
    dependent_treaties: list[DependentTreaty] | None = None,
    config: SynthesisConfig | None = None,
) -> tuple[list[Any], FormationReport]:
    """Run the full treaty formation pipeline.

    1. Negotiate over *candidates* (if provided).
    2. Form treaties from *outcome*.
    3. Resolve dependency order for *dependent_treaties* (if provided).

    Returns:
        ``(treaties, report)``
    """
    start = time.monotonic()
    process = make_formation_process(config=config)
    all_provenance: list[str] = []
    candidates_rejected = 0
    validation_failures = 0
    cycles_broken = 0

    if candidates:
        original_count = len(candidates)
        winning = process.negotiate(candidates)
        candidates_rejected = original_count - len(winning)
        all_provenance.append(
            f"negotiation: {len(winning)} winners from {original_count} candidates"
        )

    treaties, notes = process.form_from_synthesis(outcome)
    all_provenance.extend(notes)
    for note in notes:
        if "failed validation" in note or "failed to build" in note:
            validation_failures += 1

    if dependent_treaties:
        resolved = process.resolve_dependencies(dependent_treaties)
        all_provenance.append(
            f"dep-resolution: {len(resolved)} / {len(dependent_treaties)} resolved"
        )

    elapsed = time.monotonic() - start
    all_provenance.append(f"wall_seconds:{elapsed:.4f}")

    report = build_formation_report(
        treaties_formed=len(treaties),
        candidates_rejected=candidates_rejected,
        validation_failures=validation_failures,
        resolution_cycles_broken=cycles_broken,
        provenance=tuple(all_provenance),
    )

    logger.info(
        "run_formation_pipeline: %d treaties in %.3fs", len(treaties), elapsed
    )
    return treaties, report
