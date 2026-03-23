"""Integration of theory navigation with other jugeo packages.

Reference: theory2.tex — the semantic navigation framework, federated across
the full jugeo proof-discovery pipeline.

This module bridges the theory-navigation machinery with the ideation,
federation, novelty, and trust layers so that abstract *navigation paths*
through ``TheorySpace`` acquire meaning in terms of concrete research ideas,
cross-regime analogies, novelty frontiers, and trust-weighted exploration.

Module layout::

    ┌────────────────────────────────────┬──────────────────────────────────────────┐
    │ Symbol                             │ Role                                     │
    ├────────────────────────────────────┼──────────────────────────────────────────┤
    │ _sanitize_id                       │ Make arbitrary strings safe as node IDs  │
    │ _tokenize                          │ Text tokenisation for similarity checks  │
    │ _jaccard                           │ Jaccard similarity between token sets    │
    │ _clamp                             │ Clamp float to [lo, hi]                  │
    │ _now_iso                           │ UTC timestamp helper                     │
    │ _text_similarity                   │ Quick token-overlap similarity for texts │
    │ _iter_idea_pairs                   │ Yield all (Idea, Idea) pairs from a list │
    │ IdeaNavigator                      │ Bridges Idea / IdeaPortfolio ↔ Theory   │
    │ FederationNavigator                │ Bridges CrossRegimeBridge ↔ TheorySpace  │
    │ NoveltyNavigator                   │ Novelty-maximising paths in theory space │
    │ NavigationFederator                │ Merges and partitions multiple spaces    │
    │ TrustAwareNavigator                │ Trust-filtered navigation                │
    │ IntegratedNavigationPipeline       │ End-to-end navigation pipeline           │
    └────────────────────────────────────┴──────────────────────────────────────────┘
"""
from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterator

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import Idea, IdeaPortfolio, TrustStatus
from jugeo.ideation.novelty import NoveltyScore, TheoremPortfolio
from jugeo.ideation.federation import CrossRegimeBridge, FederatedIdeaProposal
from jugeo.ideation.theory_navigation.models import (
    TheoryNode,
    TheorySpace,
    NavigationPath,
    NavigationState,
    PurposeCondition,
    NodeMaturity,
    NavigationStrategy,
)
from jugeo.ideation.theory_navigation.algorithms import (
    TheoryNavigator,
    MapBuilder,
    NavigationAlgorithm,
    NavigationHistory,
    NavigationDiagnostics,
)
from jugeo.ideation.theory_navigation.purpose_conditioning import (
    PurposeConditioner,
    HeuristicComputer,
    PurposeAligner,
)
from jugeo.ideation.theory_navigation.path_finding import (
    PathFinder,
    DiversePathFinder,
    PurposeGuidedSearch,
    PathEvaluator,
    PathCache,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _sanitize_id(raw: str) -> str:
    """Convert *raw* to a safe snake_case node identifier.

    Replaces any sequence of non-alphanumeric characters with ``_``, strips
    leading/trailing underscores, and truncates to 64 characters.

    Parameters
    ----------
    raw:
        Arbitrary input string.

    Returns
    -------
    str
        A safe identifier derived from *raw*.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_")
    return cleaned[:64] if cleaned else f"node_{uuid.uuid4().hex[:8]}"


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase word tokens.

    Single-character and two-character tokens are discarded.

    Parameters
    ----------
    text:
        Raw text.

    Returns
    -------
    set[str]
        Token set.
    """
    return {t for t in re.findall(r"[a-z]+", text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Return the Jaccard similarity between token sets *a* and *b*.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Jaccard similarity in [0, 1].
    """
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _text_similarity(a: str, b: str) -> float:
    """Quick token-overlap similarity between two texts.

    Parameters
    ----------
    a:
        First text.
    b:
        Second text.

    Returns
    -------
    float
        Jaccard similarity of their token sets.
    """
    return _jaccard(_tokenize(a), _tokenize(b))


def _iter_idea_pairs(ideas: list[Idea]) -> Iterator[tuple[Idea, Idea]]:
    """Yield all unordered pairs from *ideas*.

    Parameters
    ----------
    ideas:
        List of :class:`~jugeo.ideation.ideas.Idea` objects.

    Yields
    ------
    tuple[Idea, Idea]
        Each unordered pair exactly once.
    """
    for i in range(len(ideas)):
        for j in range(i + 1, len(ideas)):
            yield ideas[i], ideas[j]


# ---------------------------------------------------------------------------
# IdeaNavigator
# ---------------------------------------------------------------------------


class IdeaNavigator:
    """Integrate :class:`~jugeo.ideation.ideas.Idea` objects with theory navigation.

    Converts ideas and portfolios into theory-space constructs, then uses
    navigation algorithms to reason about relationships and suggest next
    research directions.
    """

    _TRUST_TO_MATURITY: dict[TrustStatus, NodeMaturity] = {
        TrustStatus.SPECULATIVE: NodeMaturity.NASCENT,
        TrustStatus.PROVISIONAL: NodeMaturity.DEVELOPING,
        TrustStatus.GROUNDED: NodeMaturity.MATURE,
        TrustStatus.VALIDATED: NodeMaturity.ESTABLISHED,
        TrustStatus.RETIRED: NodeMaturity.NASCENT,
    }

    def __init__(self, navigator: TheoryNavigator | None = None) -> None:
        self._navigator = navigator
        self._finder = PathFinder()
        self._evaluator = PathEvaluator()
        self._cache: PathCache = PathCache(max_size=100)

    # ------------------------------------------------------------------
    # Conversion utilities
    # ------------------------------------------------------------------

    def idea_to_node(self, idea: Idea) -> TheoryNode:
        """Convert an :class:`~jugeo.ideation.ideas.Idea` to a :class:`TheoryNode`.

        The mapping is:
          - ``idea_id``       → ``node_id``
          - ``title``         → ``name``
          - ``hypothesis``    → ``description``
          - ``novelty_score`` → ``purpose_alignment``
          - ``trust_status``  → ``maturity`` (via :attr:`_TRUST_TO_MATURITY`)
          - ``target_area``   and ``purpose`` → ``metadata``

        Parameters
        ----------
        idea:
            Source idea.

        Returns
        -------
        TheoryNode
            Equivalent theory node.
        """
        maturity = self._TRUST_TO_MATURITY.get(idea.trust_status, NodeMaturity.NASCENT)
        metadata: dict[str, str] = {
            "target_area": idea.target_area,
            "purpose": idea.purpose,
            "expected_value": f"{idea.expected_value():.4f}",
            "theorem_yield": f"{idea.theorem_yield():.4f}",
            "estimated_cost": f"{idea.estimated_cost():.4f}",
        }
        return TheoryNode(
            node_id=idea.idea_id,
            name=idea.title,
            description=idea.hypothesis,
            purpose_alignment=_clamp(idea.novelty_score),
            maturity=maturity,
            connections=(),
            metadata=metadata,
            created_at=_now_iso(),
        )

    def portfolio_to_space(self, portfolio: IdeaPortfolio) -> TheorySpace:
        """Convert an :class:`~jugeo.ideation.ideas.IdeaPortfolio` to a :class:`TheorySpace`.

        Each idea becomes a node.  Bidirectional edges are added between
        idea pairs whose hypothesis token sets share a Jaccard similarity
        strictly above 0.1.

        Parameters
        ----------
        portfolio:
            Portfolio of ideas to convert.

        Returns
        -------
        TheorySpace
            Theory space containing one node per idea, with edges between
            thematically related ideas.
        """
        space = TheorySpace()
        ideas_list = list(portfolio.ideas.values())

        # Add nodes
        for idea in ideas_list:
            space.add_node(self.idea_to_node(idea))

        # Precompute token sets for edges
        token_sets: dict[str, set[str]] = {
            idea.idea_id: _tokenize(idea.hypothesis + " " + idea.purpose)
            for idea in ideas_list
        }

        # Add edges where Jaccard > 0.1
        for idea_a, idea_b in _iter_idea_pairs(ideas_list):
            similarity = _jaccard(
                token_sets[idea_a.idea_id],
                token_sets[idea_b.idea_id],
            )
            if similarity > 0.1:
                space.add_edge(idea_a.idea_id, idea_b.idea_id)
                space.add_edge(idea_b.idea_id, idea_a.idea_id)

        return space

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_from_idea(
        self,
        idea: Idea,
        goal_idea: Idea,
        space: TheorySpace,
    ) -> NavigationPath:
        """Navigate between two ideas in a :class:`TheorySpace`.

        If a navigator is attached, uses it; otherwise falls back to the
        internal :class:`~jugeo.ideation.theory_navigation.path_finding.PathFinder`.

        Parameters
        ----------
        idea:
            Starting idea.
        goal_idea:
            Destination idea.
        space:
            Theory space containing both ideas.

        Returns
        -------
        NavigationPath
            Path from *idea* to *goal_idea*, possibly empty.
        """
        if self._navigator is not None:
            condition_kw = _tokenize(idea.purpose + " " + goal_idea.purpose)
            condition = PurposeCondition(
                condition_id=f"navigate_{idea.idea_id[:8]}",
                label="idea_navigation",
                description=f"{idea.purpose} → {goal_idea.purpose}",
                keywords=tuple(condition_kw),
                weight=1.0,
            )
            hc = HeuristicComputer(condition=condition)
            return self._finder.find_path_astar(
                idea.idea_id, goal_idea.idea_id, space, heuristic=hc
            )
        return self._finder.find_path_astar(idea.idea_id, goal_idea.idea_id, space)

    def find_purpose_aligned_ideas(
        self,
        portfolio: IdeaPortfolio,
        purpose: str,
        limit: int = 5,
    ) -> list[Idea]:
        """Return ideas whose purpose or hypothesis aligns with *purpose*.

        Scores each idea by the Jaccard overlap between *purpose* tokens and
        the idea's ``purpose + hypothesis`` token set.  Returns the top
        *limit* ideas by descending score.

        Parameters
        ----------
        portfolio:
            Idea pool to search.
        purpose:
            Target purpose string.
        limit:
            Maximum number of ideas to return.

        Returns
        -------
        list[Idea]
            Up to *limit* ideas sorted by purpose alignment, highest first.
        """
        purpose_tokens = _tokenize(purpose)
        if not purpose_tokens:
            return list(portfolio.ideas.values())[:limit]

        scored: list[tuple[float, Idea]] = []
        for idea in portfolio.ideas.values():
            idea_tokens = _tokenize(idea.purpose + " " + idea.hypothesis)
            score = _jaccard(purpose_tokens, idea_tokens)
            # Boost by novelty_score so high-novelty aligned ideas rank higher
            combined = 0.6 * score + 0.4 * _clamp(idea.novelty_score)
            scored.append((combined, idea))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [idea for _, idea in scored[:limit]]

    def suggest_next_ideas(
        self,
        current_idea: Idea,
        portfolio: IdeaPortfolio,
        space: TheorySpace,
    ) -> list[Idea]:
        """Suggest ideas to explore after *current_idea* using graph neighbors.

        Retrieves the neighbors of the current idea's node in *space*, then
        maps those node IDs back to ideas in *portfolio*, ranking by
        expected value.

        Parameters
        ----------
        current_idea:
            The idea the researcher is currently investigating.
        portfolio:
            Full portfolio of ideas.
        space:
            Theory space (should have been built from *portfolio*).

        Returns
        -------
        list[Idea]
            Neighboring ideas ranked by expected value, descending.
        """
        neighbors = space.get_neighbors(current_idea.idea_id)
        neighbor_ideas: list[Idea] = []
        for neighbor_node in neighbors:
            idea = portfolio.ideas.get(neighbor_node.node_id)
            if idea is not None and idea.idea_id != current_idea.idea_id:
                neighbor_ideas.append(idea)

        # Rank by expected value; fall back to novelty_score
        neighbor_ideas.sort(
            key=lambda i: (i.expected_value(), i.novelty_score),
            reverse=True,
        )
        return neighbor_ideas

    def idea_path_to_ideas(
        self,
        path: NavigationPath,
        portfolio: IdeaPortfolio,
    ) -> list[Idea]:
        """Convert node IDs in *path* back to :class:`~jugeo.ideation.ideas.Idea` objects.

        Node IDs that do not correspond to any idea in *portfolio* are
        silently skipped.

        Parameters
        ----------
        path:
            Navigation path (typically produced by :meth:`navigate_from_idea`).
        portfolio:
            Portfolio that was the source of the theory space.

        Returns
        -------
        list[Idea]
            Ideas corresponding to the nodes in the path, in order.
        """
        result: list[Idea] = []
        for node_id in path.node_ids:
            idea = portfolio.ideas.get(node_id)
            if idea is not None:
                result.append(idea)
        return result

    def integration_report(self, portfolio: IdeaPortfolio) -> str:
        """Generate a multi-line report on portfolio-to-space mapping.

        Parameters
        ----------
        portfolio:
            Portfolio to analyse.

        Returns
        -------
        str
            Human-readable multi-line report.
        """
        space = self.portfolio_to_space(portfolio)
        n_ideas = len(portfolio.ideas)
        n_nodes = space.node_count()
        n_edges = space.edge_count()

        # Trust status distribution
        trust_counts: dict[str, int] = defaultdict(int)
        for idea in portfolio.ideas.values():
            trust_counts[idea.trust_status.value] += 1

        # Maturity distribution (via nodes)
        maturity_counts: dict[str, int] = defaultdict(int)
        for node in space.iter_nodes():
            maturity_counts[node.maturity.value] += 1

        # Alignment stats
        alignments = [node.purpose_alignment for node in space.iter_nodes()]
        avg_align = sum(alignments) / max(len(alignments), 1)
        max_align = max(alignments, default=0.0)

        lines: list[str] = [
            "=== IdeaNavigator Integration Report ===",
            f"Ideas in portfolio : {n_ideas}",
            f"Nodes in space     : {n_nodes}",
            f"Edges in space     : {n_edges}",
            f"Avg degree         : {(2 * n_edges / max(n_nodes, 1)):.2f}",
            "",
            "Trust status distribution:",
        ]
        for status, count in sorted(trust_counts.items()):
            pct = count / max(n_ideas, 1) * 100
            lines.append(f"  {status:12s}: {count:4d}  ({pct:.1f}%)")

        lines += [
            "",
            "Node maturity distribution:",
        ]
        for mat, count in sorted(maturity_counts.items()):
            pct = count / max(n_nodes, 1) * 100
            lines.append(f"  {mat:12s}: {count:4d}  ({pct:.1f}%)")

        lines += [
            "",
            f"Purpose alignment  : avg={avg_align:.3f}  max={max_align:.3f}",
        ]
        isolated = space.isolated_nodes()
        lines.append(f"Isolated nodes     : {len(isolated)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FederationNavigator
# ---------------------------------------------------------------------------


class FederationNavigator:
    """Bridge :class:`~jugeo.ideation.federation.CrossRegimeBridge` objects to theory navigation.

    Builds a :class:`TheorySpace` from federation bridges and proposals so
    that standard navigation algorithms can reason about cross-regime
    analogical paths.
    """

    def __init__(self) -> None:
        self._finder = PathFinder()
        self._evaluator = PathEvaluator()

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def bridge_to_node(self, bridge: CrossRegimeBridge) -> TheoryNode:
        """Convert a :class:`~jugeo.ideation.federation.CrossRegimeBridge` to a :class:`TheoryNode`.

        The trust attenuation factor is inverted to produce purpose_alignment:
        bridges with low attenuation (high fidelity) get high alignment.
        Maturity reflects whether the bridge has been validated.

        Parameters
        ----------
        bridge:
            Source bridge.

        Returns
        -------
        TheoryNode
            Equivalent node representing the bridge.
        """
        node_id = _sanitize_id(bridge.bridge_id)
        name = f"{bridge.source} → {bridge.target}"
        # Use description if set, otherwise summarise the analogy map
        if bridge.description:
            description = bridge.description
        elif bridge.analogy_map:
            pairs = "; ".join(f"{k}≈{v}" for k, v in list(bridge.analogy_map.items())[:5])
            description = f"Analogy: {pairs}"
        else:
            description = f"Bridge from {bridge.source} to {bridge.target}"

        # High-fidelity bridges (low attenuation) score higher
        alignment = _clamp(1.0 - bridge.trust_attenuation)

        maturity = NodeMaturity.ESTABLISHED if bridge.validated else NodeMaturity.DEVELOPING

        metadata: dict[str, str] = {
            "source": bridge.source,
            "target": bridge.target,
            "validated": str(bridge.validated),
            "trust_attenuation": f"{bridge.trust_attenuation:.4f}",
            "analogy_count": str(len(bridge.analogy_map)),
            "purpose_tags": ",".join(sorted(bridge.purpose_tags)),
        }
        return TheoryNode(
            node_id=node_id,
            name=name,
            description=description,
            purpose_alignment=alignment,
            maturity=maturity,
            connections=(),
            metadata=metadata,
            created_at=_now_iso(),
        )

    def build_federation_space(
        self,
        bridges: list[CrossRegimeBridge],
        proposals: list[FederatedIdeaProposal],
    ) -> TheorySpace:
        """Build a :class:`TheorySpace` from bridges and federation proposals.

        Bridges become nodes; proposals become additional nodes linked to
        the bridge they used.  Bridge nodes are connected when they share
        a source or target regime.

        Parameters
        ----------
        bridges:
            Cross-regime bridges.
        proposals:
            Federated idea proposals.

        Returns
        -------
        TheorySpace
            A theory space representing the federation topology.
        """
        space = TheorySpace()
        bridge_id_map: dict[str, str] = {}  # bridge.bridge_id -> node_id

        # Add bridge nodes
        for bridge in bridges:
            node = self.bridge_to_node(bridge)
            space.add_node(node)
            bridge_id_map[bridge.bridge_id] = node.node_id

        # Add edges between bridges sharing a source or target regime
        for i, ba in enumerate(bridges):
            for j in range(i + 1, len(bridges)):
                bb = bridges[j]
                shared = (
                    ba.source == bb.source
                    or ba.target == bb.target
                    or ba.target == bb.source
                    or ba.source == bb.target
                )
                if shared:
                    nid_a = bridge_id_map[ba.bridge_id]
                    nid_b = bridge_id_map[bb.bridge_id]
                    space.add_edge(nid_a, nid_b)
                    space.add_edge(nid_b, nid_a)

        # Add proposal nodes and link to their bridge
        for proposal in proposals:
            prop_node_id = _sanitize_id(proposal.proposal_id)
            prop_node = TheoryNode(
                node_id=prop_node_id,
                name=proposal.transported_idea.title,
                description=proposal.transported_idea.hypothesis,
                purpose_alignment=_clamp(
                    proposal.transported_idea.normalized_payoff()
                ),
                maturity=NodeMaturity.DEVELOPING,
                connections=(),
                metadata={
                    "source_regime": proposal.source_regime,
                    "target_regime": proposal.target_regime,
                    "bridge_used": proposal.bridge_used,
                    "trust_adjustment": f"{proposal.trust_adjustment:.4f}",
                },
                created_at=_now_iso(),
            )
            space.add_node(prop_node)

            # Link proposal to its bridge node
            bridge_node_id = bridge_id_map.get(proposal.bridge_used)
            if bridge_node_id and space.has_node(bridge_node_id):
                space.add_edge(prop_node_id, bridge_node_id)
                space.add_edge(bridge_node_id, prop_node_id)

        return space

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_across_bridges(
        self,
        source_regime: str,
        target_regime: str,
        federation_space: TheorySpace,
    ) -> NavigationPath:
        """Find a path through bridge nodes from *source_regime* to *target_regime*.

        Identifies bridge nodes associated with each regime and attempts to
        navigate between them, preferring validated bridges.

        Parameters
        ----------
        source_regime:
            Label of the source regime.
        target_regime:
            Label of the target regime.
        federation_space:
            Theory space of bridge nodes.

        Returns
        -------
        NavigationPath
            Best path found, or an empty path when no route exists.
        """
        source_nodes: list[str] = []
        target_nodes: list[str] = []

        for node in federation_space.iter_nodes():
            meta_source = node.metadata.get("source", "")
            meta_target = node.metadata.get("target", "")
            if meta_source == source_regime or meta_target == source_regime:
                source_nodes.append(node.node_id)
            if meta_source == target_regime or meta_target == target_regime:
                target_nodes.append(node.node_id)

        if not source_nodes or not target_nodes:
            # Return empty path
            return self._finder.find_path_bfs(
                source_regime, target_regime, federation_space
            )

        best_path: NavigationPath | None = None
        for s_id in source_nodes:
            for t_id in target_nodes:
                if s_id == t_id:
                    continue
                path = self._finder.find_path_astar(s_id, t_id, federation_space)
                if not path.is_empty():
                    if best_path is None or path.quality_score() > best_path.quality_score():
                        best_path = path

        if best_path is None:
            # Fallback: BFS from first source to first target
            return self._finder.find_path_bfs(
                source_nodes[0], target_nodes[0], federation_space
            )
        return best_path

    def find_bridge_clusters(
        self,
        federation_space: TheorySpace,
        min_cluster_size: int = 3,
    ) -> list[list[str]]:
        """Find clusters of connected bridge nodes using BFS components.

        Parameters
        ----------
        federation_space:
            Space to analyse.
        min_cluster_size:
            Only return clusters with at least this many nodes.

        Returns
        -------
        list[list[str]]
            List of clusters, each a list of node IDs.  Clusters are sorted
            by size (largest first).
        """
        all_ids = {nd.node_id for nd in federation_space.iter_nodes()}
        visited: set[str] = set()
        clusters: list[list[str]] = []

        for start_id in all_ids:
            if start_id in visited:
                continue
            # BFS to find the component
            component: list[str] = []
            queue: deque[str] = deque([start_id])
            visited.add(start_id)
            while queue:
                nid = queue.popleft()
                component.append(nid)
                for neighbor in federation_space.get_neighbors(nid):
                    if neighbor.node_id not in visited:
                        visited.add(neighbor.node_id)
                        queue.append(neighbor.node_id)
            if len(component) >= min_cluster_size:
                clusters.append(component)

        clusters.sort(key=len, reverse=True)
        return clusters

    def assess_federation_coverage(
        self,
        bridges: list[CrossRegimeBridge],
    ) -> dict[str, Any]:
        """Analyse how well *bridges* cover different regime pairs.

        Parameters
        ----------
        bridges:
            List of bridges to analyse.

        Returns
        -------
        dict
            Dictionary with keys: ``regime_pairs`` (count),
            ``bridges_per_pair`` (avg), ``validated_fraction``,
            ``mean_attenuation``, ``all_regimes`` (list),
            ``pair_counts`` (dict mapping "A→B" to count).
        """
        pair_counts: dict[str, int] = defaultdict(int)
        all_regimes: set[str] = set()
        validated_count = 0
        total_attenuation = 0.0

        for bridge in bridges:
            pair_key = f"{bridge.source}→{bridge.target}"
            pair_counts[pair_key] += 1
            all_regimes.add(bridge.source)
            all_regimes.add(bridge.target)
            if bridge.validated:
                validated_count += 1
            total_attenuation += bridge.trust_attenuation

        n = len(bridges)
        regime_pairs = len(pair_counts)
        avg_per_pair = sum(pair_counts.values()) / max(regime_pairs, 1)
        validated_frac = validated_count / max(n, 1)
        mean_att = total_attenuation / max(n, 1)

        return {
            "regime_pairs": regime_pairs,
            "bridges_per_pair": round(avg_per_pair, 2),
            "validated_fraction": round(validated_frac, 3),
            "mean_attenuation": round(mean_att, 3),
            "all_regimes": sorted(all_regimes),
            "pair_counts": dict(pair_counts),
        }

    def federation_navigation_report(self, federation_space: TheorySpace) -> str:
        """Return a multi-line navigation report for a federation space.

        Parameters
        ----------
        federation_space:
            The space to report on.

        Returns
        -------
        str
            Human-readable multi-line report.
        """
        lines: list[str] = [
            "=== FederationNavigator Report ===",
            federation_space.summary(),
            "",
        ]

        # Regime coverage
        regimes: set[str] = set()
        for node in federation_space.iter_nodes():
            if "source" in node.metadata:
                regimes.add(node.metadata["source"])
            if "target" in node.metadata:
                regimes.add(node.metadata["target"])
        lines.append(f"Regimes covered: {len(regimes)}")
        for r in sorted(regimes):
            lines.append(f"  • {r}")

        # Validated vs. unvalidated
        validated = sum(
            1 for nd in federation_space.iter_nodes()
            if nd.metadata.get("validated", "False") == "True"
        )
        lines.append(f"\nValidated nodes: {validated}/{federation_space.node_count()}")

        # Top-alignment bridges
        top = sorted(
            federation_space.iter_nodes(),
            key=lambda nd: nd.purpose_alignment,
            reverse=True,
        )[:5]
        lines.append("\nTop-aligned bridge nodes:")
        for nd in top:
            lines.append(f"  [{nd.purpose_alignment:.3f}] {nd.name} ({nd.node_id})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NoveltyNavigator
# ---------------------------------------------------------------------------


class NoveltyNavigator:
    """Find novelty-maximising paths in a theory space built from novelty scores.

    Converts :class:`~jugeo.ideation.novelty.NoveltyScore` objects into
    :class:`TheoryNode` instances so that standard graph navigation can be
    used to plan research trajectories that maximise novelty.
    """

    def __init__(self, condition: PurposeCondition | None = None) -> None:
        self._condition = condition
        self._finder = PathFinder()

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def novelty_score_to_alignment(self, score: NoveltyScore) -> float:
        """Convert a :class:`~jugeo.ideation.novelty.NoveltyScore` to a purpose_alignment float.

        Weights the composite score and purpose alignment together, so nodes
        that are both novel and purpose-aligned rank highest.

        Parameters
        ----------
        score:
            Novelty score to convert.

        Returns
        -------
        float
            Purpose alignment in [0, 1].
        """
        return _clamp(0.55 * score.composite + 0.45 * score.purpose_alignment)

    def build_novelty_space(
        self,
        scores: list[NoveltyScore],
        portfolio: TheoremPortfolio | None = None,
    ) -> TheorySpace:
        """Build a :class:`TheorySpace` from a list of novelty scores.

        Each score becomes a node with purpose_alignment derived from
        :meth:`novelty_score_to_alignment`.  Edges are added between scores
        whose ``explanation`` texts share a Jaccard similarity above 0.12,
        or that share the same purpose tag.

        Parameters
        ----------
        scores:
            Source novelty scores.
        portfolio:
            Optional theorem portfolio (currently reserved for future
            provenance linking).

        Returns
        -------
        TheorySpace
            Theory space of novelty nodes.
        """
        space = TheorySpace()
        token_sets: dict[str, set[str]] = {}

        for score in scores:
            node_id = _sanitize_id(str(score.idea_id))
            alignment = self.novelty_score_to_alignment(score)
            maturity = NodeMaturity.from_score(score.composite)
            description = score.explanation or f"novelty={score.composite:.3f}"
            node = TheoryNode(
                node_id=node_id,
                name=score.title or node_id,
                description=description,
                purpose_alignment=alignment,
                maturity=maturity,
                connections=(),
                metadata={
                    "semantic_distance": f"{score.semantic_distance:.4f}",
                    "composite": f"{score.composite:.4f}",
                    "feasibility": f"{score.feasibility:.4f}",
                },
                created_at=_now_iso(),
            )
            space.add_node(node)
            token_sets[node_id] = _tokenize(description)

        # Add edges by text similarity
        node_ids = list(token_sets.keys())
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                a_id, b_id = node_ids[i], node_ids[j]
                sim = _jaccard(token_sets[a_id], token_sets[b_id])
                if sim >= 0.12:
                    space.add_edge(a_id, b_id)
                    space.add_edge(b_id, a_id)

        return space

    # ------------------------------------------------------------------
    # Frontier and navigation
    # ------------------------------------------------------------------

    def find_novelty_frontier(
        self,
        space: TheorySpace,
        known_ids: set[str],
    ) -> list[TheoryNode]:
        """Return nodes NOT in *known_ids* with high purpose_alignment.

        Nodes with alignment ≥ 0.5 that have not been explored form the
        novelty frontier.

        Parameters
        ----------
        space:
            Theory space to search.
        known_ids:
            Set of already-explored node IDs.

        Returns
        -------
        list[TheoryNode]
            Frontier nodes sorted by purpose_alignment descending.
        """
        frontier = [
            nd for nd in space.iter_nodes()
            if nd.node_id not in known_ids and nd.purpose_alignment >= 0.5
        ]
        frontier.sort(key=lambda nd: nd.purpose_alignment, reverse=True)
        return frontier

    def navigate_to_novel(
        self,
        start_id: str,
        space: TheorySpace,
        known_ids: set[str],
    ) -> NavigationPath:
        """Navigate from *start_id* toward the nearest novelty frontier node.

        Selects the highest-alignment frontier node reachable from *start_id*
        and navigates toward it using A*.

        Parameters
        ----------
        start_id:
            Source node ID.
        space:
            Theory space.
        known_ids:
            Already-explored IDs (excluded from the frontier).

        Returns
        -------
        NavigationPath
            Path toward the best frontier node, or empty if none reachable.
        """
        frontier = self.find_novelty_frontier(space, known_ids)
        if not frontier:
            return self._finder.find_path_bfs(start_id, start_id, space)

        # Try frontier nodes in order of alignment until we find a reachable one
        for goal_node in frontier:
            path = self._finder.find_path_astar(start_id, goal_node.node_id, space)
            if not path.is_empty():
                return path

        # Fallback to BFS to first frontier node
        return self._finder.find_path_bfs(start_id, frontier[0].node_id, space)

    def maximize_novelty_path(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        novelty_weight: float = 0.7,
    ) -> NavigationPath:
        """Find a path that maximises the sum of purpose_alignment along nodes.

        Uses A* where edge cost is reduced for high-alignment nodes:
        ``cost(u→v) = novelty_weight * (1 - alignment(v)) + (1 - novelty_weight)``.

        Parameters
        ----------
        start_id:
            Source node ID.
        goal_id:
            Destination node ID.
        space:
            Theory space.
        novelty_weight:
            Weight given to novelty vs. path length [0, 1].

        Returns
        -------
        NavigationPath
            Path maximising weighted novelty sum.
        """
        nw = _clamp(novelty_weight)
        # Build a custom condition that heavily rewards high-alignment nodes
        keywords: tuple[str, ...] = ()
        if self._condition is not None:
            keywords = self._condition.keywords
        novelty_condition = PurposeCondition(
            condition_id=f"novelty_{uuid.uuid4().hex[:8]}",
            label="novelty_maximization",
            description="Maximize novelty along path",
            keywords=keywords,
            weight=nw,
        )
        hc = HeuristicComputer(condition=novelty_condition)
        return self._finder.find_path_astar(start_id, goal_id, space, heuristic=hc)

    def novelty_report(self, space: TheorySpace, known_ids: set[str]) -> str:
        """Return a multi-line novelty analysis report.

        Parameters
        ----------
        space:
            Theory space of novelty nodes.
        known_ids:
            Set of already-explored node IDs.

        Returns
        -------
        str
            Human-readable report.
        """
        n = space.node_count()
        explored = sum(1 for nd in space.iter_nodes() if nd.node_id in known_ids)
        frontier = self.find_novelty_frontier(space, known_ids)

        alignments = [nd.purpose_alignment for nd in space.iter_nodes()]
        avg = sum(alignments) / max(len(alignments), 1)

        lines: list[str] = [
            "=== NoveltyNavigator Report ===",
            f"Total nodes   : {n}",
            f"Explored      : {explored} ({explored / max(n, 1):.1%})",
            f"Frontier size : {len(frontier)}",
            f"Avg alignment : {avg:.3f}",
            "",
            "Top-5 frontier nodes:",
        ]
        for nd in frontier[:5]:
            lines.append(
                f"  [{nd.purpose_alignment:.3f}] {nd.name[:50]} ({nd.node_id})"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NavigationFederator
# ---------------------------------------------------------------------------


class NavigationFederator:
    """Merge and partition multiple :class:`TheorySpace` instances.

    Enables cross-space navigation by federating spaces into a single
    unified graph, or by splitting a large space into manageable partitions.
    """

    def __init__(self) -> None:
        self._finder = PathFinder()

    def federate_spaces(
        self,
        spaces: list[TheorySpace],
        bridge_threshold: float = 0.1,
    ) -> TheorySpace:
        """Merge multiple :class:`TheorySpace` instances into one federated space.

        Nodes from all spaces are merged.  For nodes from *different* source
        spaces, a bridge edge is added when name/description Jaccard similarity
        exceeds *bridge_threshold*.

        Parameters
        ----------
        spaces:
            Spaces to federate.
        bridge_threshold:
            Minimum Jaccard similarity to add a cross-space edge.

        Returns
        -------
        TheorySpace
            Federated super-space.
        """
        federated = TheorySpace()
        # Track which space each node came from (space index)
        origin: dict[str, int] = {}

        for space_idx, space in enumerate(spaces):
            for node in space.iter_nodes():
                # Avoid ID collisions across spaces by prefixing
                new_id = f"s{space_idx}_{node.node_id}"
                prefixed = replace(
                    node,
                    node_id=new_id,
                    metadata={**node.metadata, "origin_space": str(space_idx)},
                )
                federated.add_node(prefixed)
                origin[new_id] = space_idx

            # Preserve intra-space edges with prefixed IDs
            for node in space.iter_nodes():
                src_id = f"s{space_idx}_{node.node_id}"
                for neighbor in space.get_neighbors(node.node_id):
                    tgt_id = f"s{space_idx}_{neighbor.node_id}"
                    if federated.has_node(src_id) and federated.has_node(tgt_id):
                        federated.add_edge(src_id, tgt_id)

        # Add cross-space edges by text similarity
        all_nodes = list(federated.iter_nodes())
        node_tokens: dict[str, set[str]] = {
            nd.node_id: _tokenize(nd.name + " " + nd.description)
            for nd in all_nodes
        }
        for i, nd_a in enumerate(all_nodes):
            for nd_b in all_nodes[i + 1:]:
                if origin.get(nd_a.node_id) == origin.get(nd_b.node_id):
                    continue
                sim = _jaccard(node_tokens[nd_a.node_id], node_tokens[nd_b.node_id])
                if sim >= bridge_threshold:
                    federated.add_edge(nd_a.node_id, nd_b.node_id)
                    federated.add_edge(nd_b.node_id, nd_a.node_id)

        return federated

    def split_space(
        self,
        space: TheorySpace,
        n_partitions: int = 2,
    ) -> list[TheorySpace]:
        """Partition *space* into *n_partitions* roughly equal subspaces.

        Uses BFS-based seed expansion: seeds are chosen by highest
        purpose_alignment among unexplored nodes.  Each node is assigned
        to the first seed that reaches it.

        Parameters
        ----------
        space:
            Space to partition.
        n_partitions:
            Target number of partitions.

        Returns
        -------
        list[TheorySpace]
            List of *n_partitions* subspaces (last partition absorbs any remainder).
        """
        n_partitions = max(1, n_partitions)
        all_nodes = list(space.iter_nodes())
        if not all_nodes:
            return [TheorySpace() for _ in range(n_partitions)]

        # Choose seed nodes — highest alignment, spread across the node list
        sorted_by_align = sorted(all_nodes, key=lambda nd: nd.purpose_alignment, reverse=True)
        seeds: list[str] = []
        step = max(1, len(sorted_by_align) // n_partitions)
        for k in range(n_partitions):
            idx = min(k * step, len(sorted_by_align) - 1)
            seeds.append(sorted_by_align[idx].node_id)

        # Assign nodes to partitions via BFS from seeds
        assignment: dict[str, int] = {}
        queues: list[deque[str]] = [deque([s]) for s in seeds]
        for k, seed in enumerate(seeds):
            assignment[seed] = k

        changed = True
        while changed:
            changed = False
            for k, queue in enumerate(queues):
                if not queue:
                    continue
                nid = queue.popleft()
                for neighbor in space.get_neighbors(nid):
                    if neighbor.node_id not in assignment:
                        assignment[neighbor.node_id] = k
                        queues[k].append(neighbor.node_id)
                        changed = True

        # Catch any unassigned nodes — put in last partition
        for nd in all_nodes:
            if nd.node_id not in assignment:
                assignment[nd.node_id] = n_partitions - 1

        # Build sub-spaces
        sub_spaces: list[TheorySpace] = [TheorySpace() for _ in range(n_partitions)]
        for nd in all_nodes:
            p = assignment[nd.node_id]
            sub_spaces[p].add_node(nd)

        # Preserve intra-partition edges
        for nd in all_nodes:
            p = assignment[nd.node_id]
            for neighbor in space.get_neighbors(nd.node_id):
                if assignment.get(neighbor.node_id) == p:
                    sub_spaces[p].add_edge(nd.node_id, neighbor.node_id)

        return sub_spaces

    def cross_space_navigate(
        self,
        start_id: str,
        goal_id: str,
        spaces: list[TheorySpace],
    ) -> tuple[NavigationPath, list[str]]:
        """Navigate across federated spaces from *start_id* to *goal_id*.

        Federates all spaces first, then navigates, and records which
        original space each node in the path belongs to.

        Parameters
        ----------
        start_id:
            Source node ID (in its original space, without prefix).
        goal_id:
            Destination node ID (in its original space, without prefix).
        spaces:
            List of spaces to federate.

        Returns
        -------
        tuple[NavigationPath, list[str]]
            The navigation path (with prefixed node IDs) and a parallel list
            indicating which original space index each node came from.
        """
        # Detect which space each ID belongs to
        start_space_idx = -1
        goal_space_idx = -1
        for idx, sp in enumerate(spaces):
            if sp.has_node(start_id):
                start_space_idx = idx
            if sp.has_node(goal_id):
                goal_space_idx = idx

        federated = self.federate_spaces(spaces)

        prefixed_start = f"s{start_space_idx}_{start_id}" if start_space_idx >= 0 else start_id
        prefixed_goal = f"s{goal_space_idx}_{goal_id}" if goal_space_idx >= 0 else goal_id

        path = self._finder.find_path_astar(prefixed_start, prefixed_goal, federated)

        # Determine origin space for each node in path
        space_labels: list[str] = []
        for nid in path.node_ids:
            node = federated.get_node(nid)
            if node is not None:
                space_labels.append(node.metadata.get("origin_space", "unknown"))
            else:
                space_labels.append("unknown")

        return path, space_labels

    def align_spaces(
        self,
        a: TheorySpace,
        b: TheorySpace,
    ) -> dict[str, str]:
        """Find node correspondences between two spaces by name/description similarity.

        For every node in *a*, finds the node in *b* with the highest
        Jaccard similarity.  Only returns pairs where similarity > 0.

        Parameters
        ----------
        a:
            First theory space.
        b:
            Second theory space.

        Returns
        -------
        dict[str, str]
            Mapping from node ID in *a* to best-matching node ID in *b*.
        """
        b_nodes = list(b.iter_nodes())
        b_tokens: dict[str, set[str]] = {
            nd.node_id: _tokenize(nd.name + " " + nd.description)
            for nd in b_nodes
        }
        alignment: dict[str, str] = {}

        for nd_a in a.iter_nodes():
            a_tokens = _tokenize(nd_a.name + " " + nd_a.description)
            best_sim = 0.0
            best_id = ""
            for nd_b in b_nodes:
                sim = _jaccard(a_tokens, b_tokens[nd_b.node_id])
                if sim > best_sim:
                    best_sim = sim
                    best_id = nd_b.node_id
            if best_sim > 0.0:
                alignment[nd_a.node_id] = best_id

        return alignment

    def federation_report(self, spaces: list[TheorySpace]) -> str:
        """Return a multi-line federation report for a list of spaces.

        Parameters
        ----------
        spaces:
            Spaces being federated.

        Returns
        -------
        str
            Human-readable multi-line report.
        """
        lines: list[str] = [
            f"=== NavigationFederator Report ({len(spaces)} spaces) ===",
        ]
        total_nodes = 0
        total_edges = 0
        for idx, sp in enumerate(spaces):
            n = sp.node_count()
            e = sp.edge_count()
            total_nodes += n
            total_edges += e
            lines.append(f"  Space {idx}: {n} nodes, {e} edges")
        lines += [
            "",
            f"Total nodes (pre-federation): {total_nodes}",
            f"Total edges (pre-federation): {total_edges}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TrustAwareNavigator
# ---------------------------------------------------------------------------


class TrustAwareNavigator:
    """Navigate a :class:`TheorySpace` while respecting :class:`TrustLevel` constraints.

    Filters the space to exclude nodes that fall below a minimum trust level
    before invoking navigation algorithms, preventing paths through
    unverified or contradicted territory.
    """

    _TRUST_ORDER: list[TrustLevel] = [
        TrustLevel.CONTRADICTED,
        TrustLevel.UNVERIFIED,
        TrustLevel.COPILOT_SUGGESTED,
        TrustLevel.ORACLE_PROPOSED,
        TrustLevel.HUMAN_ATTESTED,
        TrustLevel.RUNTIME_WITNESSED,
        TrustLevel.SOLVER_DISCHARGED,
        TrustLevel.MECHANICALLY_VERIFIED,
    ]

    _TRUST_TO_MATURITY: dict[TrustLevel, NodeMaturity] = {
        TrustLevel.MECHANICALLY_VERIFIED: NodeMaturity.ESTABLISHED,
        TrustLevel.SOLVER_DISCHARGED: NodeMaturity.ESTABLISHED,
        TrustLevel.RUNTIME_WITNESSED: NodeMaturity.MATURE,
        TrustLevel.HUMAN_ATTESTED: NodeMaturity.MATURE,
        TrustLevel.ORACLE_PROPOSED: NodeMaturity.DEVELOPING,
        TrustLevel.COPILOT_SUGGESTED: NodeMaturity.DEVELOPING,
        TrustLevel.UNVERIFIED: NodeMaturity.NASCENT,
        TrustLevel.CONTRADICTED: NodeMaturity.NASCENT,
    }

    def __init__(
        self,
        min_trust: TrustLevel = TrustLevel.UNVERIFIED,
        navigator: TheoryNavigator | None = None,
    ) -> None:
        self._min_trust = min_trust
        self._navigator = navigator
        self._finder = PathFinder()

    def _trust_rank(self, trust: TrustLevel) -> int:
        """Return a numeric rank for *trust* (higher = more trusted)."""
        try:
            return self._TRUST_ORDER.index(trust)
        except ValueError:
            return 0

    def trust_to_maturity(self, trust: TrustLevel) -> NodeMaturity:
        """Map a :class:`TrustLevel` to a :class:`NodeMaturity`.

        Parameters
        ----------
        trust:
            Trust level to convert.

        Returns
        -------
        NodeMaturity
            Corresponding maturity stage.
        """
        return self._TRUST_TO_MATURITY.get(trust, NodeMaturity.NASCENT)

    def filter_by_trust(
        self,
        space: TheorySpace,
        trust_levels: dict[str, TrustLevel],
    ) -> TheorySpace:
        """Create a new space containing only nodes that meet the minimum trust level.

        Nodes absent from *trust_levels* are treated as :attr:`TrustLevel.UNVERIFIED`.
        Edges between surviving nodes are preserved.

        Parameters
        ----------
        space:
            Source space.
        trust_levels:
            Mapping from node_id to its trust level.

        Returns
        -------
        TheorySpace
            Filtered sub-space.
        """
        min_rank = self._trust_rank(self._min_trust)
        filtered = TheorySpace()

        allowed: set[str] = set()
        for node in space.iter_nodes():
            node_trust = trust_levels.get(node.node_id, TrustLevel.UNVERIFIED)
            if self._trust_rank(node_trust) >= min_rank:
                allowed.add(node.node_id)
                maturity = self.trust_to_maturity(node_trust)
                trust_node = replace(node, maturity=maturity)
                filtered.add_node(trust_node)

        # Preserve edges between allowed nodes
        for node in space.iter_nodes():
            if node.node_id not in allowed:
                continue
            for neighbor in space.get_neighbors(node.node_id):
                if neighbor.node_id in allowed:
                    filtered.add_edge(node.node_id, neighbor.node_id)

        return filtered

    def trusted_navigate(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        trust_registry: dict[str, TrustLevel],
    ) -> NavigationPath:
        """Navigate from *start_id* to *goal_id* through trusted nodes only.

        Filters the space by the minimum trust level first, then delegates
        to A* search on the filtered space.

        Parameters
        ----------
        start_id:
            Source node ID.
        goal_id:
            Destination node ID.
        space:
            Full theory space.
        trust_registry:
            Mapping from node_id to trust level.

        Returns
        -------
        NavigationPath
            Path through trusted nodes, or empty if not reachable.
        """
        trusted_space = self.filter_by_trust(space, trust_registry)
        if not trusted_space.has_node(start_id) or not trusted_space.has_node(goal_id):
            # Return empty path — start or goal filtered out
            return self._finder.find_path_bfs(start_id, goal_id, trusted_space)
        return self._finder.find_path_astar(start_id, goal_id, trusted_space)

    def audit_path(
        self,
        path: NavigationPath,
        trust_registry: dict[str, TrustLevel],
    ) -> list[tuple[str, TrustLevel]]:
        """Return the trust level for each node in *path*.

        Nodes absent from *trust_registry* are reported as
        :attr:`TrustLevel.UNVERIFIED`.

        Parameters
        ----------
        path:
            Navigation path to audit.
        trust_registry:
            Mapping from node_id to trust level.

        Returns
        -------
        list[tuple[str, TrustLevel]]
            Ordered list of (node_id, TrustLevel) pairs.
        """
        return [
            (nid, trust_registry.get(nid, TrustLevel.UNVERIFIED))
            for nid in path.node_ids
        ]

    def trust_report(
        self,
        space: TheorySpace,
        trust_registry: dict[str, TrustLevel],
    ) -> str:
        """Return a multi-line report on trust coverage in *space*.

        Parameters
        ----------
        space:
            Theory space to inspect.
        trust_registry:
            Trust-level registry for the space's nodes.

        Returns
        -------
        str
            Human-readable multi-line report.
        """
        n = space.node_count()
        trust_counts: dict[str, int] = defaultdict(int)
        covered = 0

        for node in space.iter_nodes():
            trust = trust_registry.get(node.node_id)
            if trust is not None:
                covered += 1
                trust_counts[trust.value] += 1
            else:
                trust_counts["unregistered"] += 1

        # Count nodes meeting minimum trust
        min_rank = self._trust_rank(self._min_trust)
        passing = sum(
            1 for nd in space.iter_nodes()
            if self._trust_rank(trust_registry.get(nd.node_id, TrustLevel.UNVERIFIED)) >= min_rank
        )

        lines: list[str] = [
            "=== TrustAwareNavigator Trust Report ===",
            f"Total nodes        : {n}",
            f"Registered nodes   : {covered} ({covered / max(n, 1):.1%})",
            f"Min trust level    : {self._min_trust.value}",
            f"Nodes passing filter: {passing} ({passing / max(n, 1):.1%})",
            "",
            "Trust level distribution:",
        ]
        for level in self._TRUST_ORDER[::-1]:
            count = trust_counts.get(level.value, 0)
            pct = count / max(n, 1) * 100
            lines.append(f"  {level.value:25s}: {count:4d}  ({pct:.1f}%)")
        unreg = trust_counts.get("unregistered", 0)
        if unreg:
            lines.append(f"  {'unregistered':25s}: {unreg:4d}  ({unreg / max(n, 1):.1%})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# IntegratedNavigationPipeline
# ---------------------------------------------------------------------------


class IntegratedNavigationPipeline:
    """End-to-end navigation pipeline combining all integration layers.

    Orchestrates: space construction → purpose conditioning →
    navigation → path evaluation, returning a rich result dictionary.

    Configuration keys (all optional):
      - ``beam_width`` (int, default 3): beam search width.
      - ``cache_size`` (int, default 200): path cache size.
      - ``edge_threshold`` (float, default 0.08): Jaccard threshold for auto-edges.
      - ``min_trust`` (str): minimum trust level label for trust filtering.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._builder = MapBuilder()
        self._diagnostics = NavigationDiagnostics()
        self._history = NavigationHistory()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        raw_nodes: list[dict[str, Any]],
        start_id: str,
        goal_id: str,
        *,
        purpose: str = "",
        algorithm: str = "a_star",
        trust_filter: bool = False,
        find_diverse: bool = False,
        k_diverse: int = 3,
    ) -> dict[str, Any]:
        """Execute the full navigation pipeline.

        Steps:
          1. Build :class:`TheorySpace` from *raw_nodes*.
          2. Condition the space with *purpose*.
          3. Navigate from *start_id* to *goal_id*.
          4. Evaluate the resulting paths.
          5. Return a comprehensive result dict.

        Parameters
        ----------
        raw_nodes:
            List of node descriptor dicts (see :class:`MapBuilder`).
        start_id:
            Source node ID.
        goal_id:
            Destination node ID.
        purpose:
            Purpose string for conditioning.
        algorithm:
            Algorithm name; one of the :class:`NavigationAlgorithm` values.
        trust_filter:
            If ``True``, apply trust filtering (nodes without a trust entry
            default to UNVERIFIED).
        find_diverse:
            If ``True``, also find *k_diverse* diverse paths.
        k_diverse:
            Number of diverse paths to find when *find_diverse* is ``True``.

        Returns
        -------
        dict
            Keys: ``space_stats``, ``paths``, ``best_path``,
            ``evaluation``, ``report``.
        """
        space, _ = self._build_space(raw_nodes)
        condition, conditioned_space = self._condition_space(space, purpose)
        paths = self._navigate(conditioned_space, start_id, goal_id, algorithm, condition)

        if find_diverse:
            diverse_finder = DiversePathFinder()
            extra = diverse_finder.find_k_paths(
                start_id, goal_id, conditioned_space, k=k_diverse
            )
            # Merge without duplicates
            seen_ids = {p.path_id for p in paths}
            for p in extra:
                if p.path_id not in seen_ids:
                    paths.append(p)
                    seen_ids.add(p.path_id)

        evaluations = self._evaluate_paths(paths, conditioned_space, condition)

        best_path = None
        if paths:
            best_path = max(paths, key=lambda p: p.quality_score())

        space_stats = {
            "node_count": conditioned_space.node_count(),
            "edge_count": conditioned_space.edge_count(),
        }

        result: dict[str, Any] = {
            "space_stats": space_stats,
            "paths": [p.to_dict() for p in paths],
            "best_path": best_path.to_dict() if best_path else None,
            "evaluation": evaluations,
            "report": self.pipeline_report(
                {
                    "space_stats": space_stats,
                    "paths": paths,
                    "best_path": best_path,
                    "evaluation": evaluations,
                }
            ),
        }
        return result

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _build_space(self, raw_nodes: list[dict[str, Any]]) -> tuple[TheorySpace, Any]:
        """Build a :class:`TheorySpace` from raw node descriptors.

        Parameters
        ----------
        raw_nodes:
            List of node dicts.

        Returns
        -------
        tuple[TheorySpace, SpaceIndexer]
            Space and its lookup index.
        """
        return self._builder.build_from_dicts(raw_nodes)

    def _condition_space(
        self,
        space: TheorySpace,
        purpose: str,
    ) -> tuple[PurposeCondition, TheorySpace]:
        """Build a :class:`PurposeCondition` and condition *space* with it.

        Parameters
        ----------
        space:
            Unconditioned space.
        purpose:
            Purpose string.

        Returns
        -------
        tuple[PurposeCondition, TheorySpace]
            The condition and the conditioned space.
        """
        keywords = tuple(t for t in re.findall(r"[a-z]+", purpose.lower()) if len(t) > 2)
        condition = PurposeCondition(
            condition_id=f"pipeline_{uuid.uuid4().hex[:8]}",
            label="pipeline_purpose",
            description=purpose or "general navigation",
            keywords=keywords,
            weight=1.0,
        )
        conditioner = PurposeConditioner(condition=condition)
        conditioned = conditioner.condition_space(space)
        return condition, conditioned

    def _navigate(
        self,
        space: TheorySpace,
        start_id: str,
        goal_id: str,
        algorithm: str,
        condition: PurposeCondition,
    ) -> list[NavigationPath]:
        """Execute navigation and return list of paths.

        Parameters
        ----------
        space:
            Conditioned theory space.
        start_id:
            Source node ID.
        goal_id:
            Destination node ID.
        algorithm:
            Algorithm name string.
        condition:
            Purpose condition for heuristic guidance.

        Returns
        -------
        list[NavigationPath]
            List containing the primary found path (non-empty if reachable).
        """
        try:
            alg = NavigationAlgorithm(algorithm)
        except ValueError:
            alg = NavigationAlgorithm.A_STAR

        navigator = TheoryNavigator(space, condition=condition)
        path = navigator.navigate(
            start_id, goal_id, algorithm=alg, purpose=condition.description
        )
        return [path] if not path.is_empty() else []

    def _evaluate_paths(
        self,
        paths: list[NavigationPath],
        space: TheorySpace,
        condition: PurposeCondition,
    ) -> list[dict[str, Any]]:
        """Evaluate each path in *paths* and return evaluation dicts.

        Parameters
        ----------
        paths:
            Paths to evaluate.
        space:
            Theory space the paths were navigated in.
        condition:
            Purpose condition for alignment scoring.

        Returns
        -------
        list[dict]
            One evaluation dict per path with keys: ``path_id``,
            ``quality``, ``length``, ``cost``, ``purpose_alignment``.
        """
        evaluator = PathEvaluator(condition=condition)
        results: list[dict[str, Any]] = []
        for path in paths:
            eval_result = evaluator.evaluate(path, space)
            results.append(
                {
                    "path_id": path.path_id,
                    "quality": path.quality_score(),
                    "length": path.length(),
                    "cost": path.total_cost,
                    "purpose_alignment": path.purpose_alignment,
                    "score": eval_result.get("overall_score", 0.0),
                }
            )
        return results

    def pipeline_report(self, result: dict[str, Any]) -> str:
        """Format a pipeline result as a multi-line human-readable report.

        Parameters
        ----------
        result:
            Result dict produced by :meth:`run`.

        Returns
        -------
        str
            Formatted multi-line report.
        """
        stats = result.get("space_stats", {})
        paths = result.get("paths", [])
        best = result.get("best_path")
        evaluations = result.get("evaluation", [])

        lines: list[str] = [
            "=== IntegratedNavigationPipeline Report ===",
            "",
            "Space statistics:",
            f"  Nodes : {stats.get('node_count', 'N/A')}",
            f"  Edges : {stats.get('edge_count', 'N/A')}",
            "",
            f"Paths found: {len(paths)}",
        ]

        if best is not None:
            # best may be a dict (from to_dict()) or a NavigationPath
            if isinstance(best, dict):
                b_id = best.get("path_id", "?")
                b_len = len(best.get("node_ids", []))
                b_cost = best.get("total_cost", 0.0)
                b_qual = best.get("purpose_alignment", 0.0)
            else:
                b_id = best.path_id
                b_len = best.length()
                b_cost = best.total_cost
                b_qual = best.quality_score()
            lines += [
                "",
                "Best path:",
                f"  Path ID  : {b_id}",
                f"  Length   : {b_len}",
                f"  Cost     : {b_cost:.4f}",
                f"  Quality  : {b_qual:.4f}",
            ]

        if evaluations:
            lines += ["", "Evaluations:"]
            for ev in evaluations:
                lines.append(
                    f"  [{ev.get('path_id', '?')[:12]}]"
                    f"  quality={ev.get('quality', 0):.3f}"
                    f"  len={ev.get('length', 0)}"
                    f"  align={ev.get('purpose_alignment', 0):.3f}"
                )

        lines.append("")
        lines.append("Pipeline completed.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "IdeaNavigator",
    "FederationNavigator",
    "NoveltyNavigator",
    "NavigationFederator",
    "TrustAwareNavigator",
    "IntegratedNavigationPipeline",
]
