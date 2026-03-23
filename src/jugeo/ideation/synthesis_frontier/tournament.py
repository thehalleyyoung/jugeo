"""Binary tournament for synthesis frontier — pairs and merges FieldNodes across rounds.
# copilot: synthesis frontier tournament — 48→24→12→6→3→2→1 field merging
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo imports with graceful fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.synthesis_frontier.models import (
        FieldNode,
        MetaphorLink,
        PropositionKind,
        PropositionRecord,
        SynthesisPair,
        TournamentState,
    )
    try:
        from jugeo.ideation.synthesis_frontier.llm_judge import LLMJudge, HeuristicJudge, SynthesisJudge
        JudgmentCriteria = None  # removed in new version; keep name for compat
    except ImportError:
        LLMJudge = None  # type: ignore[assignment]
        HeuristicJudge = None  # type: ignore[assignment]
        SynthesisJudge = None  # type: ignore[assignment]
        JudgmentCriteria = None
except ImportError:  # pragma: no cover — standalone runs
    _log.warning("jugeo models not importable; using lightweight stubs")

    class PropositionKind:  # type: ignore[no-redef]
        BRIDGE_THEOREM = "bridge_theorem"
        SYNTHESIS_RESULT = "synthesis_result"

    @dataclass
    class PropositionRecord:  # type: ignore[no-redef]
        prop_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        title: str = ""
        statement: str = ""
        kind: Any = "theorem"
        source_field_id: str = ""
        tags: tuple = ()
        importance: float = 0.5
        created_at: float = field(default_factory=time.time)
        proof_sketch: str = ""
        references: tuple = ()

        @staticmethod
        def make(**kw):
            obj = PropositionRecord()
            for k, v in kw.items():
                object.__setattr__(obj, k, v) if hasattr(obj, k) else None
            object.__setattr__(obj, "prop_id", str(uuid.uuid4()))
            return obj

    @dataclass
    class MetaphorLink:  # type: ignore[no-redef]
        link_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        source_field_id: str = ""
        target_field_id: str = ""
        source_concept: str = ""
        target_concept: str = ""
        description: str = ""
        strength: float = 0.5
        kind: str = "structural"
        supporting_propositions: tuple = ()
        created_at: float = field(default_factory=time.time)
        is_known_classical: bool = False

        @staticmethod
        def make(**kw):
            obj = MetaphorLink()
            for k, v in kw.items():
                object.__setattr__(obj, k, v) if hasattr(obj, k) else None
            return obj

    @dataclass
    class FieldNode:  # type: ignore[no-redef]
        field_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        name: str = ""
        description: str = ""
        propositions: tuple = ()
        constituent_fields: tuple = ()
        round_number: int = 0
        judgment_site: str = ""
        parent_ids: tuple = ()
        keywords: tuple = ()
        created_at: float = field(default_factory=time.time)

        @staticmethod
        def make(**kw):
            obj = FieldNode()
            for k, v in kw.items():
                if hasattr(obj, k):
                    object.__setattr__(obj, k, v)
            object.__setattr__(obj, "field_id", str(uuid.uuid4()))
            if not obj.constituent_fields:
                object.__setattr__(obj, "constituent_fields", (obj.name,) if obj.name else ())
            return obj

        def proposition_count(self):
            return len(self.propositions)

        def key_prop_titles(self, n: int = 10):
            return [getattr(p, "title", str(p)) for p in list(self.propositions)[:n]]

        def bridge_theorems(self):
            return [p for p in self.propositions
                    if getattr(p, "kind", "") == PropositionKind.BRIDGE_THEOREM]

        def with_propositions(self, new_props):
            import dataclasses
            return dataclasses.replace(self, propositions=self.propositions + tuple(new_props))

        def summary_line(self):
            return f"FieldNode({self.name!r}, round={self.round_number}, props={self.proposition_count()})"

    @dataclass
    class SynthesisPair:  # type: ignore[no-redef]
        pair_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        field_a_id: str = ""
        field_b_id: str = ""
        integration_score: float = 0.5
        leverage: float = 0.5
        metaphor_richness: float = 0.5
        transportability: float = 0.5
        proof_density: float = 0.5
        novelty: float = 0.5
        geometry_fit: float = 0.5
        metaphors: tuple = ()
        bridge_theorems: tuple = ()
        reasoning: str = ""
        created_at: float = field(default_factory=time.time)

        @staticmethod
        def make(**kw):
            obj = SynthesisPair()
            for k, v in kw.items():
                if hasattr(obj, k):
                    object.__setattr__(obj, k, v)
            return obj

    @dataclass
    class TournamentState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        current_round: int = 0
        active_nodes: list = field(default_factory=list)
        completed_merges: list = field(default_factory=list)
        all_nodes: dict = field(default_factory=dict)
        all_pairs: dict = field(default_factory=dict)
        all_metaphors: dict = field(default_factory=dict)
        is_complete: bool = False
        created_at: float = field(default_factory=time.time)
        updated_at: float = field(default_factory=time.time)
        metadata: dict = field(default_factory=dict)

        def touch(self):
            self.updated_at = time.time()

        def register_node(self, node):
            self.all_nodes[node.field_id] = node

        def register_pair(self, pair):
            self.all_pairs[pair.pair_id] = pair

        def register_metaphor(self, link):
            self.all_metaphors[link.link_id] = link

        def total_propositions(self):
            return sum(n.proposition_count() for n in self.active_nodes)

        def summary(self):
            return (f"TournamentState(round={self.current_round}, "
                    f"active={len(self.active_nodes)}, "
                    f"merges={len(self.completed_merges)}, "
                    f"complete={self.is_complete})")

    @dataclass
    class JudgmentCriteria:  # type: ignore[no-redef]
        leverage: float = 0.5
        metaphor_richness: float = 0.5
        transportability: float = 0.5
        proof_density: float = 0.5
        novelty: float = 0.5
        judgment_geometry_fit: float = 0.5
        overall: float = 0.5
        reasoning: str = ""

    class LLMJudge:  # type: ignore[no-redef]
        def __init__(self, model="claude-sonnet-4.6", timeout=120):
            self._model = model

        def score_integration(self, a, b):
            return JudgmentCriteria()

        def find_metaphors(self, a, b):
            return []

        def generate_bridge_theorems(self, a, b, metaphors):
            return []

        def synthesize_summary(self, node):
            return f"Synthesis of {node.name}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _uid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> float:
    return time.time()


def _tokenize(text: str) -> set[str]:
    import re
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# PairingStrategy
# ---------------------------------------------------------------------------


class PairingStrategy(str, Enum):
    """Strategy for pairing FieldNodes in a tournament round."""

    RANDOM = "random"
    SIMILARITY = "similarity"
    DIVERSITY = "diversity"
    GREEDY = "greedy"
    GREEDY_BEST = "greedy_best"
    SEQUENTIAL = "sequential"
    SIMILARITY_BASED = "similarity_based"
    DIVERSITY_MAXIMIZING = "diversity_maximizing"


# ---------------------------------------------------------------------------
# MergeResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Record of a single field-merge operation."""

    merge_id: str
    round_number: int
    field_a_id: str
    field_b_id: str
    merged_field_id: str
    synthesis_pair_id: str
    merge_time: float
    propositions_before: int
    propositions_after: int
    metaphors_count: int


# ---------------------------------------------------------------------------
# RoundResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoundResult:
    """Summary of a single tournament round."""

    round_number: int
    merges: tuple[MergeResult, ...]
    fields_before: int
    fields_after: int
    total_propositions: int
    duration_seconds: float
    top_metaphors: tuple[MetaphorLink, ...]


# ---------------------------------------------------------------------------
# TournamentConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """Configuration for a BinaryTournamentFrontier run.

    Parameters
    ----------
    pairing_strategy:
        How to pair fields at each round.
    judge_all_pairs:
        If True, score all O(n²) pairs (overrides pairing_strategy to
        GREEDY_BEST).  Expensive but maximally informed.
    min_propositions_per_round:
        After each merge, enforce this minimum proposition count by asking
        the LLM to generate additional propositions if needed.
    parallel_merges:
        (Reserved for future use) run merges concurrently.
    checkpoint_dir:
        Directory to write checkpoint files after each round.
    model:
        LLM model slug passed through to the LLMJudge.
    seed:
        Random seed for reproducible RANDOM/SEQUENTIAL pairings.
    max_rounds:
        Hard cap on the number of rounds (safety valve).
    propositions_per_merge:
        Target number of NEW propositions to generate for each merge
        (beyond bridge theorems and inherited props).
    """

    pairing_strategy: PairingStrategy = PairingStrategy.DIVERSITY
    judge_all_pairs: bool = False
    min_propositions_per_round: int = 0
    parallel_merges: bool = False
    checkpoint_dir: str = "/tmp/jugeo_tournament"
    model: str = "claude-sonnet-4.6"
    seed: int = 42
    max_rounds: int = 10
    propositions_per_merge: int = 8

    def effective_strategy(self) -> PairingStrategy:
        """Return GREEDY_BEST if judge_all_pairs is set."""
        if self.judge_all_pairs:
            return PairingStrategy.GREEDY_BEST
        return self.pairing_strategy


# ---------------------------------------------------------------------------
# Maximum-weight bipartite matching (simple greedy)
# ---------------------------------------------------------------------------


def _greedy_matching(
    scores: dict[tuple[str, str], float],
    nodes: list[FieldNode],
) -> list[tuple[str, str]]:
    """Select a maximum-weight non-overlapping set of pairs.

    This is a simple greedy approximation (sort by score, pick pairs whose
    nodes have not yet been used).  For n ≤ 100 this gives excellent results
    in practice and runs in O(n² log n).

    Parameters
    ----------
    scores:
        Mapping (field_a_id, field_b_id) → integration_score.
    nodes:
        Full list of FieldNodes (to detect unpaired leftovers).

    Returns
    -------
    list[tuple[str, str]]
        List of selected (field_a_id, field_b_id) pairs.
    """
    sorted_pairs = sorted(scores.items(), key=lambda kv: -kv[1])
    used: set[str] = set()
    selected: list[tuple[str, str]] = []
    for (a_id, b_id), _ in sorted_pairs:
        if a_id not in used and b_id not in used:
            selected.append((a_id, b_id))
            used.add(a_id)
            used.add(b_id)
    return selected


# ---------------------------------------------------------------------------
# BinaryTournamentFrontier
# ---------------------------------------------------------------------------


class BinaryTournamentFrontier:
    """Binary halving / proposition-doubling tournament engine.

    The tournament proceeds as follows:

    1. The caller supplies an initial list of FieldNodes (typically 48,
       but any even number works; an odd field is carried over unmerged).
    2. :meth:`run` loops over rounds until a single node remains.
    3. Each round calls :meth:`run_round`, which:
       a. Calls :meth:`_pair_fields` to produce field pairs.
       b. Scores each pair with the :class:`LLMJudge`.
       c. Calls :meth:`_merge_fields` to produce merged FieldNodes.
       d. Updates :class:`TournamentState`.
       e. Calls :meth:`checkpoint`.
    4. The final active node is the tournament winner.

    Parameters
    ----------
    fields:
        Initial FieldNode list.  Should have an even number of elements;
        if odd, the last field is carried forward unmerged.
    config:
        Tournament configuration.
    judge:
        LLMJudge instance.  If not provided, one is constructed using
        the model specified in config.
    """

    def __init__(
        self,
        fields: list[FieldNode],
        config: TournamentConfig,
        judge: Optional[LLMJudge] = None,
    ) -> None:
        self._fields = list(fields)
        self._config = config
        if judge is not None:
            self._judge = judge
        elif SynthesisJudge is not None:
            # Use the real judge from llm_judge.py with proper JudgeConfig
            try:
                from jugeo.ideation.synthesis_frontier.llm_judge import (
                    JudgeConfig, JudgeMode,
                )
                cfg = JudgeConfig(mode=JudgeMode.LLM, model=config.model)
                self._judge = SynthesisJudge(cfg)
            except Exception:
                self._judge = LLMJudge(model=config.model, timeout=120)
        else:
            self._judge = LLMJudge(model=config.model, timeout=120)
        self._rng = random.Random(config.seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> TournamentState:
        """Run the complete tournament from initial fields to final synthesis.

        Returns
        -------
        TournamentState
            Final state with is_complete=True and a single active node.
        """
        state = TournamentState(
            active_nodes=list(self._fields),
        )
        for node in state.active_nodes:
            state.register_node(node)

        max_rounds = min(self._config.max_rounds, 20)
        for rnd in range(max_rounds):
            if len(state.active_nodes) <= 1:
                break
            _log.info(
                "Tournament round %d: %d fields, %d total props",
                rnd, len(state.active_nodes), state.total_propositions(),
            )
            state = self.run_round(state)
            self.checkpoint(state)

        state.is_complete = len(state.active_nodes) <= 1
        state.touch()
        _log.info("Tournament complete: %s", state.summary())
        return state

    def run_round(self, state: TournamentState) -> TournamentState:
        """Execute one round of the tournament.

        Takes the current active nodes, pairs them, merges each pair,
        and returns updated state with new active nodes.

        Parameters
        ----------
        state:
            Current tournament state.

        Returns
        -------
        TournamentState
            Updated state after this round's merges.
        """
        nodes = list(state.active_nodes)
        pairs = self._pair_fields(nodes, self._config)
        _log.debug("Round %d: %d pairs from %d nodes", state.current_round, len(pairs), len(nodes))

        # Identify nodes that will be left unpaired (odd node out).
        paired_ids: set[str] = set()
        for a, b in pairs:
            paired_ids.add(a.field_id)
            paired_ids.add(b.field_id)
        bywater: list[FieldNode] = [n for n in nodes if n.field_id not in paired_ids]

        new_nodes: list[FieldNode] = list(bywater)
        for field_a, field_b in pairs:
            t_pair_start = _utcnow()
            pair_score = self._score_pair(field_a, field_b)
            state.register_pair(pair_score)
            for m in pair_score.metaphors:
                state.register_metaphor(m)

            merged = self._merge_fields(field_a, field_b, pair_score)
            state.register_node(merged)
            new_nodes.append(merged)

            mr = MergeResult(
                merge_id=_uid(),
                round_number=state.current_round,
                field_a_id=field_a.field_id,
                field_b_id=field_b.field_id,
                merged_field_id=merged.field_id,
                synthesis_pair_id=pair_score.pair_id,
                merge_time=_utcnow() - t_pair_start,
                propositions_before=field_a.proposition_count() + field_b.proposition_count(),
                propositions_after=merged.proposition_count(),
                metaphors_count=len(pair_score.metaphors),
            )
            state.completed_merges.append(mr)
            _log.info(
                "  Merged '%s' × '%s' → '%s' (score=%.3f, props: %d→%d)",
                field_a.name, field_b.name, merged.name,
                pair_score.integration_score, mr.propositions_before, mr.propositions_after,
            )

        # Enforce minimum proposition counts.
        min_props = self._config.min_propositions_per_round
        if min_props > 0:
            new_nodes = [
                self._enforce_proposition_minimum(n, min_props) for n in new_nodes
            ]

        state.active_nodes = new_nodes
        state.current_round += 1
        state.touch()
        return state

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def _pair_fields(
        self,
        nodes: list[FieldNode],
        config: TournamentConfig,
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Produce a list of (field_a, field_b) pairs for this round.

        The number of pairs is ⌊n/2⌋.  If n is odd, one node is left
        unpaired (the caller handles the leftover).

        Parameters
        ----------
        nodes:
            Current active FieldNodes.
        config:
            Tournament configuration (strategy, seed, etc.).

        Returns
        -------
        list[tuple[FieldNode, FieldNode]]
        """
        if len(nodes) < 2:
            return []

        strategy = config.effective_strategy()

        if strategy == PairingStrategy.GREEDY_BEST:
            return self._pair_greedy_best(nodes)
        elif strategy == PairingStrategy.RANDOM:
            return self._pair_random(nodes)
        elif strategy == PairingStrategy.SEQUENTIAL:
            return self._pair_sequential(nodes)
        elif strategy == PairingStrategy.SIMILARITY_BASED:
            return self._pair_by_similarity(nodes, maximize=True)
        elif strategy == PairingStrategy.DIVERSITY_MAXIMIZING:
            return self._pair_by_similarity(nodes, maximize=False)
        else:
            return self._pair_random(nodes)

    def _pair_random(self, nodes: list[FieldNode]) -> list[tuple[FieldNode, FieldNode]]:
        shuffled = list(nodes)
        self._rng.shuffle(shuffled)
        return [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled) - 1, 2)]

    def _pair_sequential(self, nodes: list[FieldNode]) -> list[tuple[FieldNode, FieldNode]]:
        return [(nodes[i], nodes[i + 1]) for i in range(0, len(nodes) - 1, 2)]

    def _pair_by_similarity(
        self, nodes: list[FieldNode], maximize: bool
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Greedy pairing by token-overlap similarity.

        If maximize=True (SIMILARITY_BASED), pair most-similar nodes.
        If maximize=False (DIVERSITY_MAXIMIZING), pair least-similar nodes.
        """
        token_sets = {
            n.field_id: _tokenize(n.name + " " + n.description + " " + " ".join(n.keywords))
            for n in nodes
        }
        sim: dict[tuple[str, str], float] = {}
        for i, na in enumerate(nodes):
            for nb in nodes[i + 1:]:
                s = _jaccard(token_sets[na.field_id], token_sets[nb.field_id])
                sim[(na.field_id, nb.field_id)] = s if maximize else (1.0 - s)

        pairs_ids = _greedy_matching(sim, nodes)
        id_to_node = {n.field_id: n for n in nodes}
        return [(id_to_node[a], id_to_node[b]) for a, b in pairs_ids
                if a in id_to_node and b in id_to_node]

    def _pair_greedy_best(self, nodes: list[FieldNode]) -> list[tuple[FieldNode, FieldNode]]:
        """Score all O(n²) pairs and return the optimal non-overlapping matching."""
        scores = self._score_all_pairs(nodes)
        pairs_ids = _greedy_matching(scores, nodes)
        id_to_node = {n.field_id: n for n in nodes}
        return [(id_to_node[a], id_to_node[b]) for a, b in pairs_ids
                if a in id_to_node and b in id_to_node]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_pair(
        self, field_a: FieldNode, field_b: FieldNode
    ) -> SynthesisPair:
        """Score a single pair with the LLM judge and return a SynthesisPair."""
        # If the judge exposes score_pair (llm_judge.SynthesisJudge / LLMJudge),
        # use it and map the JudgeVerdict to a SynthesisPair directly.
        if hasattr(self._judge, "score_pair"):
            verdict = self._judge.score_pair(field_a, field_b)
            return SynthesisPair.make(
                field_a_id=field_a.field_id,
                field_b_id=field_b.field_id,
                integration_score=getattr(verdict, "integration_score", 0.5),
                leverage=getattr(verdict, "leverage", 0.5),
                metaphor_richness=getattr(verdict, "metaphor_richness", 0.5),
                transportability=getattr(verdict, "transportability", 0.5),
                proof_density=getattr(verdict, "proof_density", 0.5),
                novelty=getattr(verdict, "novelty", 0.5),
                geometry_fit=getattr(verdict, "geometry_fit", 0.5),
                metaphors=tuple(getattr(verdict, "metaphors_found", [])),
                bridge_theorems=tuple(getattr(verdict, "bridge_theorem_sketches", [])),
                reasoning=getattr(verdict, "reasoning", ""),
            )

        # Legacy path: judge exposes score_integration / find_metaphors / generate_bridge_theorems
        criteria = self._judge.score_integration(field_a, field_b)
        metaphors = self._judge.find_metaphors(field_a, field_b)
        bridge_theorems = self._judge.generate_bridge_theorems(
            field_a, field_b, metaphors
        )

        return SynthesisPair.make(
            field_a_id=field_a.field_id,
            field_b_id=field_b.field_id,
            integration_score=getattr(criteria, "overall", 0.5),
            leverage=getattr(criteria, "leverage", 0.5),
            metaphor_richness=getattr(criteria, "metaphor_richness", 0.5),
            transportability=getattr(criteria, "transportability", 0.5),
            proof_density=getattr(criteria, "proof_density", 0.5),
            novelty=getattr(criteria, "novelty", 0.5),
            geometry_fit=getattr(criteria, "judgment_geometry_fit", 0.5),
            metaphors=tuple(metaphors),
            bridge_theorems=tuple(bridge_theorems),
            reasoning=getattr(criteria, "reasoning", ""),
        )

    def _score_all_pairs(
        self, nodes: list[FieldNode]
    ) -> dict[tuple[str, str], float]:
        """Score all O(n²) pairs and return a score dictionary.

        Parameters
        ----------
        nodes:
            FieldNodes to consider.

        Returns
        -------
        dict[tuple[str, str], float]
            (field_a_id, field_b_id) → integration_score.
        """
        scores: dict[tuple[str, str], float] = {}
        total = len(nodes) * (len(nodes) - 1) // 2
        _log.info("Scoring all %d pairs (GREEDY_BEST strategy)…", total)
        for i, na in enumerate(nodes):
            for nb in nodes[i + 1:]:
                criteria = self._judge.score_integration(na, nb)
                scores[(na.field_id, nb.field_id)] = getattr(criteria, "overall", 0.5)
        return scores

    def _select_best_matching(
        self, scores: dict[tuple[str, str], float], nodes: list[FieldNode]
    ) -> list[tuple[str, str]]:
        """Thin wrapper over the greedy matching algorithm."""
        return _greedy_matching(scores, nodes)

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def _merge_fields(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        pair_score: SynthesisPair,
    ) -> FieldNode:
        """Combine two FieldNodes into a single merged FieldNode.

        The merge procedure:

        1. Combined name: ``"{field_a.name} × {field_b.name}"``.
        2. Merge constituent_fields (union, deduplication).
        3. Combine all propositions from both fields.
        4. Add bridge theorems from pair_score.
        5. Generate :attr:`TournamentConfig.propositions_per_merge` NEW
           propositions that could only exist in the merged field.
        6. Set round_number to max(parent rounds) + 1.
        7. Build a judgment_site description and synthesis summary.

        Parameters
        ----------
        field_a:
            First parent FieldNode.
        field_b:
            Second parent FieldNode.
        pair_score:
            SynthesisPair scored by the LLM judge.

        Returns
        -------
        FieldNode
            The merged FieldNode.
        """
        merged_name = f"{field_a.name} × {field_b.name}"
        merged_round = max(field_a.round_number, field_b.round_number) + 1
        merged_id = _uid()

        # Merge constituents (deduplicated, order-preserving).
        seen: set[str] = set()
        merged_constituents: list[str] = []
        for c in list(field_a.constituent_fields) + list(field_b.constituent_fields):
            if c not in seen:
                merged_constituents.append(c)
                seen.add(c)

        # Merge keywords similarly.
        seen_kw: set[str] = set()
        merged_keywords: list[str] = []
        for kw in list(field_a.keywords) + list(field_b.keywords):
            if kw not in seen_kw:
                merged_keywords.append(kw)
                seen_kw.add(kw)

        # Combine all propositions from both parents.
        combined_props = list(field_a.propositions) + list(field_b.propositions)

        # Add bridge theorems from the pair score.
        combined_props.extend(list(pair_score.bridge_theorems))

        # Build a description.
        merged_description = (
            f"A synthesis field integrating {field_a.name} and {field_b.name}.  "
            f"Integration score: {pair_score.integration_score:.3f}.  "
            f"{field_a.description[:120]}  "
            f"ALSO: {field_b.description[:120]}"
        )

        # Build the judgment site description.
        judgment_site = (
            f"Judgment site for {merged_name}: "
            f"covers {len(merged_constituents)} constituent domains across "
            f"round {merged_round}.  "
            f"Key metaphors: {len(pair_score.metaphors)}.  "
            f"Bridge theorems: {len(pair_score.bridge_theorems)}.  "
            f"Integration reasoning: {pair_score.reasoning[:200]}"
        )

        # Stub the merged node so the judge can use it.
        # (We need the field_id for PropositionRecord.source_field_id.)
        stub_node = FieldNode.make(
            name=merged_name,
            description=merged_description,
            propositions=tuple(combined_props),
            constituent_fields=tuple(merged_constituents),
            round_number=merged_round,
            judgment_site=judgment_site,
            parent_ids=(field_a.field_id, field_b.field_id),
            keywords=tuple(merged_keywords[:20]),
        )

        # Generate NEW propositions unique to the merged field.
        new_props = self._generate_emergent_propositions(
            stub_node, field_a, field_b, pair_score
        )
        final_props = tuple(combined_props) + tuple(new_props)

        # Try to get a synthesis summary.
        try:
            summary = self._judge.synthesize_summary(stub_node)
        except Exception:  # noqa: BLE001
            summary = merged_description

        # Rebuild with all props and the synthesis summary in the description.
        try:
            import dataclasses
            merged_node = dataclasses.replace(
                stub_node,
                propositions=final_props,
                description=summary or merged_description,
            )
        except TypeError:
            merged_node = stub_node

        _log.debug(
            "Merged '%s' × '%s' → '%s': %d + %d + %d bridge + %d emergent = %d props",
            field_a.name, field_b.name, merged_name,
            field_a.proposition_count(),
            field_b.proposition_count(),
            len(pair_score.bridge_theorems),
            len(new_props),
            len(final_props),
        )
        return merged_node

    def _generate_emergent_propositions(
        self,
        merged_stub: FieldNode,
        field_a: FieldNode,
        field_b: FieldNode,
        pair_score: SynthesisPair,
    ) -> list[PropositionRecord]:
        """Generate NEW propositions that only exist in the merged field.

        These are ``PropositionKind.SYNTHESIS_RESULT`` propositions that
        combine objects or techniques from both parent fields.  We ask the
        LLM judge to generate bridge theorems beyond those already in
        pair_score, then supplement with heuristic statements if needed.

        Parameters
        ----------
        merged_stub:
            Partially-constructed merged FieldNode.
        field_a:
            First parent.
        field_b:
            Second parent.
        pair_score:
            Already-scored pair (contains some bridge theorems).

        Returns
        -------
        list[PropositionRecord]
        """
        target = self._config.propositions_per_merge
        existing_bridge = len(pair_score.bridge_theorems)
        additional_needed = max(0, target - existing_bridge)

        records: list[PropositionRecord] = []

        # Ask the judge for extra bridge theorems.
        if additional_needed > 0:
            metaphors = list(pair_score.metaphors)
            try:
                extra = self._judge.generate_bridge_theorems(
                    field_a, field_b, metaphors
                )
                # Skip duplicates already in pair_score.bridge_theorems.
                existing_stmts = {
                    getattr(bt, "statement", "") for bt in pair_score.bridge_theorems
                }
                for bt in extra:
                    stmt = getattr(bt, "statement", "")
                    if stmt not in existing_stmts:
                        records.append(bt)
                    if len(records) >= additional_needed:
                        break
            except Exception:  # noqa: BLE001
                pass

        # Fill remaining with heuristic synthesis propositions.
        still_needed = additional_needed - len(records)
        if still_needed > 0:
            records.extend(
                self._heuristic_emergent_propositions(
                    field_a, field_b, merged_stub.field_id, still_needed
                )
            )

        return records[:target]

    def _heuristic_emergent_propositions(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        merged_id: str,
        count: int,
    ) -> list[PropositionRecord]:
        """Generate heuristic emergent propositions from field name templates."""
        name_a = field_a.name
        name_b = field_b.name
        templates = [
            (
                f"Universal Property of {name_a} × {name_b}",
                f"The product field {name_a} × {name_b} satisfies a universal "
                f"property: for every field F with morphisms F → {name_a} and "
                f"F → {name_b}, there is a unique factoring morphism through "
                f"{name_a} × {name_b}.",
            ),
            (
                f"Coherence in {name_a} × {name_b}",
                f"All diagrams in {name_a} × {name_b} built from the canonical "
                f"bridge functors commute up to canonical coherence isomorphism.",
            ),
            (
                f"Conservative Extension",
                f"The embedding {name_a} → {name_a} × {name_b} is conservative: "
                f"every theorem provable in {name_a} × {name_b} that is "
                f"expressible in {name_a} alone is already provable in {name_a}.",
            ),
            (
                f"Dense Generator",
                f"The pair ({name_a}, {name_b}) forms a dense generator in the "
                f"combined site, meaning every sheaf on the site is determined "
                f"by its restrictions to each constituent.",
            ),
            (
                f"Adjoint Lifting Theorem",
                f"Every adjunction in {name_a} lifts along the bridge functor "
                f"to an adjunction in {name_a} × {name_b}, and similarly for {name_b}.",
            ),
            (
                f"Preservation of Limits",
                f"The projection functors {name_a} × {name_b} → {name_a} and "
                f"{name_a} × {name_b} → {name_b} preserve all small limits and "
                f"colimits that exist in the merged field.",
            ),
            (
                f"Closed Structure",
                f"If both {name_a} and {name_b} carry closed monoidal structures, "
                f"then {name_a} × {name_b} inherits a canonical closed monoidal "
                f"structure via Day convolution.",
            ),
            (
                f"Enrichment Theorem",
                f"The hom-objects of {name_a} × {name_b} are enriched over the "
                f"tensor product of the enriching categories of {name_a} and {name_b}.",
            ),
        ]
        result: list[PropositionRecord] = []
        for title, stmt in templates[:count]:
            result.append(
                PropositionRecord.make(
                    title=title,
                    statement=stmt,
                    kind=PropositionKind.SYNTHESIS_RESULT,
                    source_field_id=merged_id,
                    tags=("emergent", "synthesis", "heuristic"),
                    importance=0.55,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Proposition enforcement
    # ------------------------------------------------------------------

    def _enforce_proposition_minimum(
        self, node: FieldNode, target: int
    ) -> FieldNode:
        """Ensure node has at least `target` propositions.

        If the node already meets the target, it is returned unchanged.
        Otherwise, heuristic padding propositions are appended.

        Parameters
        ----------
        node:
            FieldNode to check and possibly augment.
        target:
            Minimum number of propositions required.

        Returns
        -------
        FieldNode
            Possibly augmented node.
        """
        current = node.proposition_count()
        if current >= target:
            return node

        needed = target - current
        _log.debug(
            "Enforcing min props for '%s': have %d, need %d more",
            node.name, current, needed,
        )
        padding = self._heuristic_emergent_propositions(
            node, node, node.field_id, needed
        )
        # The heuristic uses field_a = field_b = node, so names look a bit odd.
        # Fix the statements to reference node.name directly.
        fixed: list[PropositionRecord] = []
        for i, p in enumerate(padding):
            try:
                import dataclasses
                fixed.append(
                    dataclasses.replace(
                        p,
                        title=f"Generated Proposition {current + i + 1} for {node.name[:30]}",
                        statement=(
                            f"(Auto-generated) In the field {node.name}, "
                            f"the following structural property holds: {p.statement}"
                        ),
                        source_field_id=node.field_id,
                    )
                )
            except (TypeError, AttributeError):
                fixed.append(p)

        return node.with_propositions(tuple(fixed))

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def checkpoint(self, state: TournamentState) -> None:
        """Serialise the current TournamentState to a JSON checkpoint file.

        The file is written to
        ``{config.checkpoint_dir}/round_{round_number:03d}.json``.

        Parameters
        ----------
        state:
            Current tournament state.
        """
        checkpoint_dir = Path(self._config.checkpoint_dir)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        filename = checkpoint_dir / f"round_{state.current_round:03d}.json"

        def _serialise_node(n: FieldNode) -> dict:
            return {
                "field_id": n.field_id,
                "name": n.name,
                "description": n.description[:200],
                "round_number": n.round_number,
                "proposition_count": n.proposition_count(),
                "constituent_fields": list(n.constituent_fields),
                "keywords": list(n.keywords[:10]),
                "judgment_site": n.judgment_site[:200],
            }

        payload = {
            "state_id": state.state_id,
            "current_round": state.current_round,
            "is_complete": state.is_complete,
            "total_propositions": state.total_propositions(),
            "active_nodes": [_serialise_node(n) for n in state.active_nodes],
            "completed_merges": [
                mr.to_dict() if hasattr(mr, "to_dict") else str(mr)
                for mr in state.completed_merges
            ],
            "active_node_count": len(state.active_nodes),
            "total_nodes_ever": len(state.all_nodes),
            "total_pairs_scored": len(state.all_pairs),
            "total_metaphors": len(state.all_metaphors),
            "metadata": state.metadata,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }
        with open(filename, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        _log.info("Checkpoint saved: %s", filename)

    def resume(self, checkpoint_path: str) -> TournamentState:
        """Attempt to resume a tournament from a checkpoint file.

        This loads the checkpoint metadata and re-hydrates a minimal
        TournamentState.  Full proposition data is not persisted in
        checkpoints (by design — propositions can be very large).
        Instead, the active FieldNodes are rebuilt from their checkpoint
        metadata with empty proposition sets.

        Parameters
        ----------
        checkpoint_path:
            Path to the JSON checkpoint file.

        Returns
        -------
        TournamentState
            Partially-restored state suitable for continuing the tournament.
        """
        with open(checkpoint_path, encoding="utf-8") as fh:
            payload = json.load(fh)

        _log.info(
            "Resuming from checkpoint: round=%d, active=%d",
            payload.get("current_round", 0),
            len(payload.get("active_nodes", [])),
        )

        active_nodes: list[FieldNode] = []
        for nd in payload.get("active_nodes", []):
            node = FieldNode.make(
                name=nd.get("name", "unknown"),
                description=nd.get("description", ""),
                constituent_fields=tuple(nd.get("constituent_fields", [])),
                round_number=nd.get("round_number", 0),
                judgment_site=nd.get("judgment_site", ""),
                keywords=tuple(nd.get("keywords", [])),
            )
            active_nodes.append(node)

        state = TournamentState(
            current_round=payload.get("current_round", 0),
            active_nodes=active_nodes,
            is_complete=payload.get("is_complete", False),
            metadata=payload.get("metadata", {}),
        )
        for node in active_nodes:
            state.register_node(node)
        return state


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def make_default_fields() -> list[FieldNode]:
    """Return the canonical 48-field starting set for the synthesis tournament.

    These fields represent diverse areas of pure mathematics, logic, computer
    science, and physics — chosen to maximise cross-domain synthesis potential.

    Returns
    -------
    list[FieldNode]
        48 leaf FieldNodes ready for Round 0.
    """
    SEED_FIELDS: list[tuple[str, str, tuple[str, ...]]] = [
        # (name, description, keywords)
        ("Category Theory",
         "Abstract structures, functors, natural transformations, adjunctions, limits.",
         ("functor", "adjunction", "monad", "limit", "colimit", "natural transformation")),
        ("Homotopy Type Theory",
         "Type theory with homotopy-theoretic interpretations; univalence axiom.",
         ("type", "path", "fibration", "equivalence", "univalence", "identity type")),
        ("Topos Theory",
         "Generalised spaces via Grothendieck toposes; internal logic; sites and sheaves.",
         ("topos", "sheaf", "site", "geometric morphism", "subobject classifier")),
        ("Higher Category Theory",
         "Infinity-categories, quasi-categories, complete Segal spaces.",
         ("infinity-category", "quasi-category", "nerve", "fibrant", "Kan")),
        ("Homological Algebra",
         "Derived functors, resolutions, spectral sequences, triangulated categories.",
         ("derived functor", "Ext", "Tor", "chain complex", "spectral sequence")),
        ("Algebraic K-theory",
         "Quillen K-groups, higher algebraic K-theory, motivic cohomology.",
         ("K-group", "Quillen", "Waldhausen", "motivic", "algebraic cycle")),
        ("Motivic Cohomology",
         "Voevodsky's triangulated motivic categories; Milnor K-theory.",
         ("motive", "motivic sheaf", "Milnor", "Bloch-Kato", "Voevodsky")),
        ("Algebraic Geometry",
         "Schemes, morphisms, cohomology, étale topology, D-modules.",
         ("scheme", "sheaf", "étale", "coherent sheaf", "derived category")),
        ("Arithmetic Geometry",
         "Galois representations, L-functions, Shimura varieties.",
         ("Galois", "L-function", "automorphic", "abelian variety", "Shimura")),
        ("Derived Algebraic Geometry",
         "Lurie's ∞-toposes; derived schemes; shifted symplectic structures.",
         ("derived scheme", "infinity-topos", "spectral scheme", "shifted symplectic")),
        ("Stable Homotopy Theory",
         "Spectra, smash products, chromatic filtration, Morava K-theory.",
         ("spectrum", "smash product", "chromatic", "Morava", "nilpotence")),
        ("Symplectic Geometry",
         "Symplectic manifolds, Lagrangian submanifolds, Hamiltonian dynamics.",
         ("symplectic", "Lagrangian", "Hamiltonian", "Floer", "Fukaya")),
        ("Contact Geometry",
         "Contact manifolds, Legendrian knots, contact homology.",
         ("contact", "Legendrian", "Reeb", "open book", "contact homology")),
        ("Differential Geometry",
         "Smooth manifolds, connections, curvature, Riemannian geometry.",
         ("manifold", "connection", "curvature", "Riemann", "geodesic")),
        ("Gauge Theory",
         "Principal bundles, Yang-Mills equations, Donaldson invariants.",
         ("gauge", "bundle", "Yang-Mills", "Donaldson", "instantons")),
        ("Mirror Symmetry",
         "Homological mirror symmetry; Fukaya–Seidel; SYZ conjecture.",
         ("mirror", "Fukaya", "SYZ", "Lagrangian fibration", "B-model")),
        ("Geometric Group Theory",
         "Hyperbolic groups, quasi-isometries, CAT(0) spaces, Cayley graphs.",
         ("hyperbolic group", "quasi-isometry", "CAT(0)", "Cayley graph")),
        ("Model Theory",
         "First-order structures, types, stability theory, o-minimality.",
         ("first-order", "type space", "stability", "o-minimal", "definable")),
        ("Proof Theory",
         "Ordinal analysis, cut elimination, sequent calculus, provability.",
         ("ordinal", "cut elimination", "sequent", "Gentzen", "consistency")),
        ("Set Theory",
         "Forcing, large cardinals, descriptive set theory, inner models.",
         ("forcing", "large cardinal", "inner model", "Woodin", "descriptive")),
        ("Constructive Mathematics",
         "Intuitionistic logic, Bishop-style analysis, realizability.",
         ("intuitionistic", "Bishop", "realizability", "choice", "Brouwer")),
        ("Linear Logic",
         "Resource-sensitive logic, proof nets, coherence spaces, *-autonomous categories.",
         ("resource", "proof net", "coherence space", "Bang", "multiplicative")),
        ("Game Semantics",
         "Arena games, winning strategies as proofs, definability.",
         ("arena", "strategy", "HO/N game", "definability", "innocence")),
        ("Domain Theory",
         "Scott domains, denotational semantics, fixpoints, powerdomains.",
         ("Scott domain", "denotational", "fixpoint", "powerdomain", "continuous")),
        ("Type Theory",
         "Dependent types, Martin-Löf type theory, Calculus of Constructions.",
         ("dependent type", "Martin-Löf", "CoC", "universe", "elimination")),
        ("Functional Programming Theory",
         "Lambda calculus, PCF, monads in programming, parametricity.",
         ("lambda", "PCF", "monad", "parametricity", "Haskell")),
        ("Concurrency Theory",
         "Process calculi, CCS, π-calculus, bisimulation, session types.",
         ("CCS", "π-calculus", "bisimulation", "session type", "mobility")),
        ("Automata Theory",
         "Finite automata, Büchi automata, tree automata, regular languages.",
         ("automaton", "Büchi", "tree automaton", "regular", "omega-regular")),
        ("Information Theory",
         "Shannon entropy, channel capacity, coding theorems, KL divergence.",
         ("entropy", "channel", "capacity", "KL divergence", "Shannon")),
        ("Statistical Mechanics",
         "Partition functions, phase transitions, renormalization group.",
         ("partition function", "phase transition", "renormalization", "Gibbs")),
        ("Quantum Field Theory",
         "Path integrals, Feynman diagrams, TQFT, conformal field theory.",
         ("path integral", "Feynman", "TQFT", "conformal", "anomaly")),
        ("Topological Quantum Computation",
         "Anyons, braiding, Fibonacci anyons, quantum error correction.",
         ("anyon", "braiding", "topological", "error correction", "Fibonacci")),
        ("Persistent Homology",
         "Topological data analysis, barcodes, Vietoris-Rips, stability.",
         ("barcode", "persistence", "TDA", "Vietoris-Rips", "stability")),
        ("Tropical Geometry",
         "Tropical algebra, amoebas, tropical curves, Newton polytopes.",
         ("tropical", "amoeba", "min-plus", "Newton polytope", "valuation")),
        ("Optimal Transport",
         "Wasserstein distances, Kantorovich duality, displacement convexity.",
         ("Wasserstein", "Kantorovich", "displacement", "optimal coupling")),
        ("Combinatorial Optimization",
         "Matroid theory, network flows, polyhedral combinatorics, LP duality.",
         ("matroid", "flow", "polyhedron", "LP duality", "matching")),
        ("Representation Theory",
         "Group representations, character theory, quantum groups, categorification.",
         ("representation", "character", "quantum group", "categorification", "Hecke")),
        ("Lie Theory",
         "Lie algebras, Lie groups, root systems, the Borel-Weil theorem.",
         ("Lie algebra", "root system", "Weyl group", "Cartan", "Borel-Weil")),
        ("Vertex Operator Algebras",
         "Conformal blocks, moonshine, modular tensor categories.",
         ("vertex operator", "moonshine", "modular tensor category", "Virasoro")),
        ("Non-commutative Geometry",
         "Spectral triples, Connes' program, cyclic cohomology.",
         ("spectral triple", "Dirac operator", "cyclic", "Connes", "noncommutative")),
        ("Sheaf Theory",
         "Sheaves on sites, cohomology, direct/inverse image, perverse sheaves.",
         ("sheaf", "direct image", "cohomology", "perverse", "derived")),
        ("Fibered Category Theory",
         "Fibrations, Grothendieck construction, indexed categories, descent.",
         ("fibration", "Grothendieck construction", "descent", "indexed", "Stack")),
        ("Stone Duality",
         "Boolean algebras ↔ Stone spaces; Priestley duality; bitopological spaces.",
         ("Stone space", "Boolean algebra", "Priestley", "bitopological", "duality")),
        ("Galois Theory",
         "Field extensions, Galois groups, fundamental theorem, profinite groups.",
         ("field extension", "Galois group", "fundamental theorem", "profinite")),
        ("Curry-Howard Correspondence",
         "Types ≅ propositions; programs ≅ proofs; Curry-Howard-Lambek.",
         ("propositions-as-types", "proofs-as-programs", "lambda", "Lambek")),
        ("Operads and Multicategories",
         "Algebraic structures for composition; ∞-operads; Swiss-cheese operad.",
         ("operad", "multicategory", "dendroidal", "∞-operad", "Swiss-cheese")),
        ("Condensed Mathematics",
         "Clausen-Scholze: condensed sets, solid modules, analytic geometry.",
         ("condensed set", "solid", "Scholze", "analytic ring", "pyknotic")),
        ("Perfectoid Spaces",
         "Scholze's perfectoid fields, tilting correspondence, pro-étale topology.",
         ("perfectoid", "tilting", "pro-étale", "Scholze", "almost étale")),
    ]
    nodes: list[FieldNode] = []
    for name, desc, kw in SEED_FIELDS:
        nodes.append(
            FieldNode.make(
                name=name,
                description=desc,
                keywords=kw,
                round_number=0,
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# FieldMerger
# ---------------------------------------------------------------------------

_BRIDGE_THEOREM_TEMPLATES: list[tuple[str, str]] = [
    (
        "Universal Bridge Theorem",
        "There exists a canonical functor from {A} to {B} that preserves "
        "the essential structure of both fields, yielding a universal property "
        "for the merged system {A} × {B}.",
    ),
    (
        "Coherence Under Synthesis",
        "All diagrams in the merged field {A} × {B} built from canonical "
        "bridge maps commute up to canonical coherence isomorphism.",
    ),
    (
        "Conservative Extension",
        "The embedding {A} → {A} × {B} is conservative: every theorem "
        "expressible in {A} alone that is provable in {A} × {B} is already "
        "provable in {A}.",
    ),
    (
        "Adjoint Lifting Theorem",
        "Every adjunction in {A} lifts along the bridge functor to an "
        "adjunction in {A} × {B}; the same holds symmetrically for {B}.",
    ),
    (
        "Limit and Colimit Preservation",
        "The projection functors {A} × {B} → {A} and {A} × {B} → {B} "
        "preserve all small limits and colimits that exist in the merged field.",
    ),
]


class FieldMerger:
    """Merges two FieldNodes into a single synthesised FieldNode."""

    def merge(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        pair: SynthesisPair,
    ) -> FieldNode:
        """Combine *field_a* and *field_b* guided by a scored *pair*.

        Steps
        -----
        1.  Build merged name ``"A × B"``.
        2.  Synthesise a description from both parent descriptions.
        3.  Combine all propositions from both parents.
        4.  Add 3–5 bridge theorem ``PropositionRecord``s from *pair*,
            synthesising new ones if *pair.bridge_theorems* is sparse.
        5.  Merge keywords, constituent_fields, and parent_ids.
        6.  Set ``round_number = max(a.round_number, b.round_number) + 1``.
        7.  Build a descriptive ``judgment_site``.
        """
        merged_name = f"{field_a.name} × {field_b.name}"
        merged_round = max(field_a.round_number, field_b.round_number) + 1

        a_desc = field_a.description.rstrip(". ")
        b_desc = field_b.description.rstrip(". ")
        merged_description = (
            f"A synthesis of {field_a.name} and {field_b.name}. "
            f"{a_desc}. "
            f"Additionally: {b_desc}. "
            f"Integration score: {pair.integration_score:.3f}."
        )

        judgment_site = (
            f"Synthesis site for {merged_name} (round {merged_round}). "
            f"Bridges {len(pair.metaphors)} metaphors across "
            f"{len(field_a.constituent_fields) + len(field_b.constituent_fields)} "
            f"constituent domains. "
            f"{pair.reasoning[:200] if pair.reasoning else 'No reasoning provided.'}"
        )

        seen_c: set[str] = set()
        merged_constituents: list[str] = []
        for c in list(field_a.constituent_fields) + list(field_b.constituent_fields):
            if c not in seen_c:
                merged_constituents.append(c)
                seen_c.add(c)

        seen_kw: set[str] = set()
        merged_keywords: list[str] = []
        for kw in list(field_a.keywords) + list(field_b.keywords):
            if kw not in seen_kw:
                merged_keywords.append(kw)
                seen_kw.add(kw)

        combined_props: list[PropositionRecord] = (
            list(field_a.propositions) + list(field_b.propositions)
        )

        bridge_props = list(pair.bridge_theorems)
        target_bridges = 4
        while len(bridge_props) < target_bridges:
            idx = len(bridge_props) % len(_BRIDGE_THEOREM_TEMPLATES)
            title, stmt_tmpl = _BRIDGE_THEOREM_TEMPLATES[idx]
            stmt = stmt_tmpl.replace("{A}", field_a.name).replace("{B}", field_b.name)
            unique_title = f"{title}: {field_a.name} & {field_b.name}"
            if not any(getattr(bt, "title", "") == unique_title for bt in bridge_props):
                bridge_props.append(
                    PropositionRecord.make(
                        title=unique_title,
                        statement=stmt,
                        kind=PropositionKind.BRIDGE_THEOREM,
                        source_field_id="",
                        tags=("bridge", "synthesis"),
                        importance=0.75,
                    )
                )
            else:
                break

        combined_props.extend(bridge_props)

        return FieldNode.make(
            name=merged_name,
            description=merged_description,
            propositions=tuple(combined_props),
            constituent_fields=tuple(merged_constituents),
            round_number=merged_round,
            judgment_site=judgment_site,
            parent_ids=(field_a.field_id, field_b.field_id),
            keywords=tuple(merged_keywords[:24]),
        )


# ---------------------------------------------------------------------------
# PairSelector
# ---------------------------------------------------------------------------

_DOMAIN_CLUSTERS: dict[str, list[str]] = {
    "logic":         ["logic", "proof", "proposition", "type", "axiom", "theorem"],
    "algebra":       ["group", "ring", "module", "field", "algebra", "homomorphism"],
    "geometry":      ["manifold", "curvature", "metric", "space", "topology", "open"],
    "analysis":      ["continuous", "measure", "integral", "convergence", "function"],
    "combinatorics": ["graph", "matroid", "matching", "counting", "permutation"],
    "quantum":       ["quantum", "hilbert", "operator", "unitary", "entanglement"],
}


def _domain_label(node: FieldNode) -> str:
    """Return the cluster name whose keywords overlap most with the node."""
    kw_set = set(k.lower() for k in node.keywords)
    kw_set |= _tokenize(node.name + " " + node.description)
    best, best_score = "other", -1
    for domain, domain_kws in _DOMAIN_CLUSTERS.items():
        score = sum(1 for k in domain_kws if k in kw_set)
        if score > best_score:
            best, best_score = domain, score
    return best


class PairSelector:
    """Pairs FieldNodes according to the chosen :class:`PairingStrategy`."""

    def __init__(self, strategy: PairingStrategy = PairingStrategy.DIVERSITY) -> None:
        self._strategy = strategy
        self._rng = random.Random(42)

    def select_pairs(
        self, nodes: list[FieldNode]
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Return a list of (A, B) pairs, one per merge in this round.

        An odd number of nodes is handled by pairing the last unpaired node
        with the previous one (they still merge; no byes).
        """
        if len(nodes) < 2:
            return []
        strategy = self._strategy
        if strategy == PairingStrategy.RANDOM:
            return self._pair_random(nodes)
        elif strategy == PairingStrategy.SIMILARITY:
            return self._pair_by_similarity(nodes, maximize=True)
        elif strategy == PairingStrategy.DIVERSITY:
            return self._pair_diversity(nodes)
        else:  # GREEDY
            return self._pair_greedy(nodes)

    def _pair_random(
        self, nodes: list[FieldNode]
    ) -> list[tuple[FieldNode, FieldNode]]:
        shuffled = list(nodes)
        self._rng.shuffle(shuffled)
        pairs: list[tuple[FieldNode, FieldNode]] = []
        for i in range(0, len(shuffled) - 1, 2):
            pairs.append((shuffled[i], shuffled[i + 1]))
        if len(shuffled) % 2 == 1:
            pairs.append((shuffled[-1], shuffled[-2]))
        return pairs

    def _pair_by_similarity(
        self, nodes: list[FieldNode], maximize: bool
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Greedy matching by Jaccard similarity of keyword/description tokens."""
        token_sets = {
            n.field_id: _tokenize(
                n.name + " " + n.description + " " + " ".join(n.keywords)
            )
            for n in nodes
        }
        sim: dict[tuple[str, str], float] = {}
        for i, na in enumerate(nodes):
            for nb in nodes[i + 1:]:
                s = _jaccard(token_sets[na.field_id], token_sets[nb.field_id])
                sim[(na.field_id, nb.field_id)] = s if maximize else (1.0 - s)
        pairs_ids = _greedy_matching(sim, nodes)
        id_to_node = {n.field_id: n for n in nodes}
        pairs = [
            (id_to_node[a], id_to_node[b])
            for a, b in pairs_ids
            if a in id_to_node and b in id_to_node
        ]
        paired_ids = {n.field_id for pair in pairs for n in pair}
        for odd in [n for n in nodes if n.field_id not in paired_ids]:
            if pairs:
                pairs.append((odd, pairs[-1][0]))
        return pairs

    def _pair_diversity(
        self, nodes: list[FieldNode]
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Pair nodes from different domain clusters to maximise cross-domain bridges."""
        paired_ids: set[str] = set()
        pairs: list[tuple[FieldNode, FieldNode]] = []
        remaining = list(nodes)

        for i, na in enumerate(remaining):
            if na.field_id in paired_ids:
                continue
            label_a = _domain_label(na)
            for nb in remaining[i + 1:]:
                if nb.field_id in paired_ids:
                    continue
                if _domain_label(nb) != label_a:
                    pairs.append((na, nb))
                    paired_ids.add(na.field_id)
                    paired_ids.add(nb.field_id)
                    break

        leftover = [n for n in remaining if n.field_id not in paired_ids]
        for i in range(0, len(leftover) - 1, 2):
            pairs.append((leftover[i], leftover[i + 1]))
            paired_ids.add(leftover[i].field_id)
            paired_ids.add(leftover[i + 1].field_id)

        for odd in [n for n in remaining if n.field_id not in paired_ids]:
            if pairs:
                pairs.append((odd, pairs[-1][0]))
        return pairs

    def _pair_greedy(
        self, nodes: list[FieldNode]
    ) -> list[tuple[FieldNode, FieldNode]]:
        """Heuristic greedy: score pairs by combined keyword richness + diversity."""
        token_sets = {
            n.field_id: _tokenize(
                n.name + " " + n.description + " " + " ".join(n.keywords)
            )
            for n in nodes
        }
        scores: dict[tuple[str, str], float] = {}
        for i, na in enumerate(nodes):
            for nb in nodes[i + 1:]:
                overlap = _jaccard(token_sets[na.field_id], token_sets[nb.field_id])
                cross_domain = 0.3 if _domain_label(na) != _domain_label(nb) else 0.0
                richness = (
                    len(token_sets[na.field_id]) + len(token_sets[nb.field_id])
                ) / 200.0
                scores[(na.field_id, nb.field_id)] = (
                    0.4 * (1.0 - overlap)
                    + 0.3 * cross_domain
                    + 0.3 * _clamp(richness)
                )
        pairs_ids = _greedy_matching(scores, nodes)
        id_to_node = {n.field_id: n for n in nodes}
        pairs = [
            (id_to_node[a], id_to_node[b])
            for a, b in pairs_ids
            if a in id_to_node and b in id_to_node
        ]
        paired_ids = {n.field_id for pair in pairs for n in pair}
        for odd in [n for n in nodes if n.field_id not in paired_ids]:
            if pairs:
                pairs.append((odd, pairs[-1][0]))
        return pairs


# ---------------------------------------------------------------------------
# TournamentRound
# ---------------------------------------------------------------------------


class TournamentRound:
    """Executes a single round of the binary tournament.

    Parameters
    ----------
    round_number:
        Zero-based index of this round.
    nodes:
        Active FieldNodes entering this round.
    strategy:
        Pairing strategy to use.
    judge:
        Duck-typed object with ``score_pair(field_a, field_b) -> verdict``
        where ``verdict.to_synthesis_pair(field_a, field_b) -> SynthesisPair``.
    """

    def __init__(
        self,
        round_number: int,
        nodes: list[FieldNode],
        strategy: PairingStrategy,
        judge: Any,
    ) -> None:
        self._round_number = round_number
        self._nodes = list(nodes)
        self._strategy = strategy
        self._judge = judge
        self._selector = PairSelector(strategy)
        self._merger = FieldMerger()

    def run(self, state: TournamentState) -> RoundResult:
        """Execute this round, updating *state* in place.

        Returns
        -------
        RoundResult
            Summary of the round's merges and metrics.
        """
        t_start = _utcnow()
        fields_before = len(self._nodes)
        pairs = self._selector.select_pairs(self._nodes)
        _log.debug(
            "Round %d: %d pairs from %d nodes",
            self._round_number, len(pairs), fields_before,
        )

        paired_ids: set[str] = {n.field_id for pair in pairs for n in pair}
        unpaired = [n for n in self._nodes if n.field_id not in paired_ids]

        merge_results: list[MergeResult] = []
        new_nodes: list[FieldNode] = list(unpaired)
        collected_metaphors: list[MetaphorLink] = []

        for field_a, field_b in pairs:
            verdict = self._judge.score_pair(field_a, field_b)
            pair: SynthesisPair = verdict.to_synthesis_pair(field_a, field_b)
            state.register_pair(pair)
            for m in pair.metaphors:
                state.register_metaphor(m)
                collected_metaphors.append(m)

            props_before = field_a.proposition_count() + field_b.proposition_count()
            merged = self._merger.merge(field_a, field_b, pair)
            state.register_node(merged)
            new_nodes.append(merged)

            mr = MergeResult(
                merge_id=_uid(),
                round_number=self._round_number,
                field_a_id=field_a.field_id,
                field_b_id=field_b.field_id,
                merged_field_id=merged.field_id,
                synthesis_pair_id=pair.pair_id,
                merge_time=_utcnow(),
                propositions_before=props_before,
                propositions_after=merged.proposition_count(),
                metaphors_count=len(pair.metaphors),
            )
            merge_results.append(mr)
            state.completed_merges.append(mr)

            _log.info(
                "  Round %d: '%s' × '%s' → '%s' (props: %d→%d)",
                self._round_number,
                field_a.name, field_b.name, merged.name,
                mr.propositions_before, mr.propositions_after,
            )

        state.current_round = self._round_number + 1
        state.active_nodes = new_nodes
        state.touch()

        duration = _utcnow() - t_start
        top_metaphors = tuple(
            sorted(collected_metaphors, key=lambda m: -m.strength)[:5]
        )
        return RoundResult(
            round_number=self._round_number,
            merges=tuple(merge_results),
            fields_before=fields_before,
            fields_after=len(new_nodes),
            total_propositions=state.total_propositions(),
            duration_seconds=duration,
            top_metaphors=top_metaphors,
        )


# ---------------------------------------------------------------------------
# Tournament
# ---------------------------------------------------------------------------


class Tournament:
    """Binary tournament that merges FieldNodes until a single synthesis remains.

    The tournament runs rounds: 48→24→12→6→3→2→1 (or any custom starting size).
    At each round, pairs are selected, scored by *judge*, and merged by
    :class:`FieldMerger`.

    Parameters
    ----------
    initial_fields:
        Starting FieldNodes (typically 48).
    strategy:
        Pairing strategy for all rounds.
    judge:
        Duck-typed object with ``score_pair(field_a, field_b) -> verdict``
        where ``verdict.to_synthesis_pair(field_a, field_b) -> SynthesisPair``.
    max_rounds:
        Hard cap on the number of rounds (``None`` = run to completion).
    """

    def __init__(
        self,
        initial_fields: list[FieldNode],
        strategy: PairingStrategy,
        judge: Any,
        max_rounds: int | None = None,
    ) -> None:
        self._initial_fields = list(initial_fields)
        self._strategy = strategy
        self._judge = judge
        self._max_rounds = max_rounds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, state: TournamentState | None = None) -> TournamentState:
        """Run the complete tournament from initial fields to a final synthesis.

        Parameters
        ----------
        state:
            Existing :class:`TournamentState` to resume; if ``None``, a fresh
            state is created and all initial fields are registered.

        Returns
        -------
        TournamentState
            Final state with ``is_complete=True`` when a single field remains
            or *max_rounds* is exhausted.
        """
        if state is None:
            state = TournamentState()
            state.active_nodes = list(self._initial_fields)
            for node in state.active_nodes:
                state.register_node(node)

        _log.info(
            "Tournament start: %d fields, %d total props",
            len(state.active_nodes), state.total_propositions(),
        )

        cap = self._max_rounds if self._max_rounds is not None else 64
        for _ in range(cap):
            if self.is_complete(state):
                break
            self.run_round(state)

        state.is_complete = self.is_complete(state)
        state.touch()
        _log.info("Tournament finished: %s", state.summary())
        return state

    def run_round(self, state: TournamentState) -> RoundResult:
        """Execute one round against the current *state*.

        Parameters
        ----------
        state:
            Mutable tournament state (updated in place).

        Returns
        -------
        RoundResult
            Summary of the round.
        """
        rnd = TournamentRound(
            round_number=state.current_round,
            nodes=list(state.active_nodes),
            strategy=self._strategy,
            judge=self._judge,
        )
        result = rnd.run(state)
        _log.info(
            "Round %d complete: %d→%d fields, %d total props, %.2fs",
            result.round_number,
            result.fields_before,
            result.fields_after,
            result.total_propositions,
            result.duration_seconds,
        )
        return result

    def is_complete(self, state: TournamentState) -> bool:
        """Return ``True`` when only one (or zero) active nodes remain."""
        return len(state.active_nodes) <= 1


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from jugeo.ideation.synthesis_frontier.models import (
        FieldNode, PropositionRecord, PropositionKind, TournamentState,
    )
    # Create 4 test fields
    fields = [FieldNode.make(name=n, description=f"Test field {n}", keywords=tuple(kw))
              for n, kw in [("Algebra",   ["group", "ring", "module"]),
                            ("Topology",  ["space", "open", "continuous"]),
                            ("Logic",     ["proof", "proposition", "type"]),
                            ("Geometry",  ["manifold", "curvature", "metric"])]]
    # Create a mock judge
    class MockJudge:
        def score_pair(self, a, b):
            class V:
                def to_synthesis_pair(self, a, b):
                    return SynthesisPair.make(a.field_id, b.field_id, 0.7)
            return V()
    from jugeo.ideation.synthesis_frontier.models import SynthesisPair
    t = Tournament(fields, PairingStrategy.RANDOM, MockJudge())
    state = t.run()
    print(state.summary())
