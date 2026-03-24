"""Provenance Graph — tracing claims through the multi-agent pipeline.

Every factual claim produced by the JuGeo pipeline can be traced backward
through a directed acyclic graph of agent contributions.  This module builds
and analyses that graph so that auditors can answer questions like:

* Where did this claim originate?
* Did trust ever get silently promoted along the way?
* Which agents are critical bottlenecks for information flow?
* Are there fabrication cascades (ungrounded claims propagating unchecked)?
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from jugeo_agents.types import (
    AgentOutput,
    FactualClaim,
    ProvenanceChain,
    ProvenanceLink,
    TrustLevel,
)


# ---------------------------------------------------------------------------
# 1. ProvenanceNode — a node in the provenance graph
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ProvenanceNode:
    """A node representing an agent's contribution at a specific round."""

    agent_id: str
    claims: list[FactualClaim]
    trust: TrustLevel
    round_number: int
    incoming_edges: list[str] = field(default_factory=list)
    outgoing_edges: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 2. ProvenanceEdge — directed information flow
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    """An edge representing information flow between two agents."""

    source_agent: str
    target_agent: str
    claims_transferred: tuple[str, ...]  # frozen requires hashable fields
    trust_at_transfer: TrustLevel
    action: str  # "derived_from", "summarized", "verified", "included", …

    @staticmethod
    def create(
        source: str,
        target: str,
        claims: list[str],
        trust: TrustLevel,
        action: str,
    ) -> ProvenanceEdge:
        """Convenience factory that accepts a mutable list."""
        return ProvenanceEdge(
            source_agent=source,
            target_agent=target,
            claims_transferred=tuple(claims),
            trust_at_transfer=trust,
            action=action,
        )


# ---------------------------------------------------------------------------
# 3. ProvenanceGraph — the main tracking structure
# ---------------------------------------------------------------------------

class ProvenanceGraph:
    """Directed graph tracking how claims flow through the agent pipeline.

    Nodes are agent contributions (one per ``AgentOutput``).  Edges record
    information flow — which claims were passed between agents and what
    action was applied (derivation, summarisation, verification, …).
    """

    def __init__(self) -> None:
        self.nodes: dict[str, ProvenanceNode] = {}
        self.edges: list[ProvenanceEdge] = []
        # Fast look-ups
        self._claim_to_agent: dict[str, str] = {}
        self._agent_edges_in: dict[str, list[ProvenanceEdge]] = defaultdict(list)
        self._agent_edges_out: dict[str, list[ProvenanceEdge]] = defaultdict(list)

    # -- mutation -----------------------------------------------------------

    def add_agent_output(
        self,
        output: AgentOutput,
        derived_from: list[str] | None = None,
    ) -> ProvenanceNode:
        """Register an agent's output as a provenance node.

        Parameters
        ----------
        output:
            The ``AgentOutput`` produced by the agent.
        derived_from:
            Optional list of *agent_ids* whose outputs were used as input.
            An edge with action ``"derived_from"`` is created for each.

        Returns
        -------
        The newly created ``ProvenanceNode``.
        """
        incoming = list(derived_from) if derived_from else []
        node = ProvenanceNode(
            agent_id=output.agent_id,
            claims=list(output.claims),
            trust=output.trust,
            round_number=output.round_number,
            incoming_edges=incoming,
            timestamp=output.timestamp,
        )
        self.nodes[output.agent_id] = node

        # Index every claim → owning agent
        for claim in output.claims:
            self._claim_to_agent[claim.claim_id] = output.agent_id

        # Create edges for derivation relationships
        if derived_from:
            claim_ids = [c.claim_id for c in output.claims]
            for source_id in derived_from:
                self.add_edge(
                    source=source_id,
                    target=output.agent_id,
                    claims=claim_ids,
                    action="derived_from",
                )

        return node

    def add_edge(
        self,
        source: str,
        target: str,
        claims: list[str],
        action: str,
    ) -> ProvenanceEdge:
        """Manually add a provenance edge.

        Parameters
        ----------
        source:  agent_id of the information provider.
        target:  agent_id of the information consumer.
        claims:  list of ``claim_id`` strings transferred.
        action:  semantic label ("derived_from", "summarized", …).

        Returns
        -------
        The new ``ProvenanceEdge``.
        """
        # Determine trust at transfer — use source node trust if available
        trust = TrustLevel.UNGROUNDED_CLAIM
        if source in self.nodes:
            trust = self.nodes[source].trust

        edge = ProvenanceEdge.create(source, target, claims, trust, action)
        self.edges.append(edge)
        self._agent_edges_in[target].append(edge)
        self._agent_edges_out[source].append(edge)

        # Keep node adjacency lists in sync
        if target in self.nodes and source not in self.nodes[target].incoming_edges:
            self.nodes[target].incoming_edges.append(source)
        if source in self.nodes and target not in self.nodes[source].outgoing_edges:
            self.nodes[source].outgoing_edges.append(target)

        return edge

    # -- single-claim tracing -----------------------------------------------

    def trace_claim(self, claim: FactualClaim) -> ProvenanceChain:
        """Trace *claim* back to its origin via BFS over incoming edges.

        The resulting ``ProvenanceChain`` records every agent hop from the
        current holder of the claim back to the originating agent.
        """
        chain = ProvenanceChain(claim=claim)
        owning_agent = self._claim_to_agent.get(claim.claim_id)
        if owning_agent is None:
            # Claim is not registered — return a single-link chain
            chain.links.append(
                ProvenanceLink(
                    agent_id=claim.source_agent or "unknown",
                    action="originated",
                    trust=claim.trust,
                    timestamp=claim.timestamp,
                )
            )
            return chain

        visited: set[str] = set()
        queue: deque[str] = deque([owning_agent])

        while queue:
            agent_id = queue.popleft()
            if agent_id in visited:
                continue
            visited.add(agent_id)

            node = self.nodes.get(agent_id)
            if node is None:
                chain.links.append(
                    ProvenanceLink(
                        agent_id=agent_id,
                        action="external_origin",
                        trust=TrustLevel.UNGROUNDED_CLAIM,
                        timestamp=0.0,
                    )
                )
                continue

            # Determine what action brought the claim to this agent
            incoming = self._agent_edges_in.get(agent_id, [])
            relevant_edges = [
                e
                for e in incoming
                if claim.claim_id in e.claims_transferred
            ]

            if not relevant_edges:
                # This agent is the origin of the claim
                chain.links.append(
                    ProvenanceLink(
                        agent_id=agent_id,
                        action="originated",
                        trust=node.trust,
                        timestamp=node.timestamp,
                    )
                )
            else:
                for edge in relevant_edges:
                    chain.links.append(
                        ProvenanceLink(
                            agent_id=agent_id,
                            action=edge.action,
                            source=edge.source_agent,
                            trust=node.trust,
                            timestamp=node.timestamp,
                        )
                    )
                    if edge.source_agent not in visited:
                        queue.append(edge.source_agent)

        return chain

    # -- bulk analysis ------------------------------------------------------

    def trace_all_claims(self) -> list[ProvenanceChain]:
        """Trace every registered claim and return all provenance chains."""
        chains: list[ProvenanceChain] = []
        for agent_id, node in self.nodes.items():
            for claim in node.claims:
                chains.append(self.trace_claim(claim))
        return chains

    def find_ungrounded_chains(self) -> list[ProvenanceChain]:
        """Find chains whose root trust is ``UNGROUNDED_CLAIM``.

        These are potential hallucination cascades — claims that were never
        grounded yet may have been incorporated into later outputs.
        """
        return [
            chain
            for chain in self.trace_all_claims()
            if chain.root_trust == TrustLevel.UNGROUNDED_CLAIM
        ]

    def find_trust_laundering(self) -> list[ProvenanceChain]:
        """Detect trust laundering: trust dips then rises along a chain.

        Trust laundering occurs when an intermediate agent lowers the trust
        (e.g. by summarising without evidence) and a subsequent agent raises
        it again — effectively "laundering" the claim through the pipeline.
        """
        laundered: list[ProvenanceChain] = []
        for chain in self.trace_all_claims():
            if len(chain.links) < 3:
                continue
            trusts = [link.trust for link in chain.links]
            # Look for a valley: trust goes down then comes back up
            saw_descent = False
            min_so_far = trusts[0]
            for i in range(1, len(trusts)):
                if trusts[i] < min_so_far:
                    min_so_far = trusts[i]
                    saw_descent = True
                elif saw_descent and trusts[i] > min_so_far:
                    laundered.append(chain)
                    break
        return laundered

    def weakest_links(self) -> list[ProvenanceLink]:
        """Collect the single weakest link from every provenance chain."""
        result: list[ProvenanceLink] = []
        for chain in self.trace_all_claims():
            wl = chain.weakest_link
            if wl is not None:
                result.append(wl)
        return result

    def trust_distribution(self) -> dict[str, dict[str, int]]:
        """Per-agent trust distribution.

        Returns ``{agent_id: {trust_level_name: count}}``.
        """
        dist: dict[str, dict[str, int]] = {}
        for agent_id, node in self.nodes.items():
            counts: dict[str, int] = defaultdict(int)
            for claim in node.claims:
                counts[claim.trust.name] += 1
            dist[agent_id] = dict(counts)
        return dist

    # -- serialisation / display --------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the graph."""
        return {
            "nodes": {
                aid: {
                    "agent_id": n.agent_id,
                    "trust": n.trust.name,
                    "round_number": n.round_number,
                    "num_claims": len(n.claims),
                    "claim_ids": [c.claim_id for c in n.claims],
                    "incoming_edges": n.incoming_edges,
                    "outgoing_edges": n.outgoing_edges,
                    "timestamp": n.timestamp,
                }
                for aid, n in self.nodes.items()
            },
            "edges": [
                {
                    "source": e.source_agent,
                    "target": e.target_agent,
                    "claims_transferred": list(e.claims_transferred),
                    "trust_at_transfer": e.trust_at_transfer.name,
                    "action": e.action,
                }
                for e in self.edges
            ],
            "stats": {
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges),
                "total_claims": sum(
                    len(n.claims) for n in self.nodes.values()
                ),
            },
        }

    def summary(self) -> str:
        """Human-readable summary of the provenance graph."""
        total_claims = sum(len(n.claims) for n in self.nodes.values())
        ungrounded = self.find_ungrounded_chains()
        laundered = self.find_trust_laundering()

        lines = [
            "Provenance Graph Summary",
            "=" * 40,
            f"  Agents (nodes):          {len(self.nodes)}",
            f"  Information flows (edges):{len(self.edges)}",
            f"  Total claims tracked:    {total_claims}",
            f"  Ungrounded chains:       {len(ungrounded)}",
            f"  Trust-laundered chains:  {len(laundered)}",
        ]

        if self.nodes:
            lines.append("")
            lines.append("  Per-agent breakdown:")
            for aid, node in sorted(self.nodes.items()):
                lines.append(
                    f"    {aid}: {len(node.claims)} claims, "
                    f"trust={node.trust.name}, "
                    f"round={node.round_number}"
                )

        td = self.trust_distribution()
        if td:
            lines.append("")
            lines.append("  Trust distribution:")
            for aid in sorted(td):
                parts = ", ".join(
                    f"{lvl}={cnt}" for lvl, cnt in sorted(td[aid].items())
                )
                lines.append(f"    {aid}: {parts}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. ProvenanceAnalyzer — advanced analysis utilities
# ---------------------------------------------------------------------------

class ProvenanceAnalyzer:
    """Static analysis routines over a ``ProvenanceGraph``."""

    @staticmethod
    def find_fabrication_cascades(
        graph: ProvenanceGraph,
    ) -> list[list[str]]:
        """Find chains where ungrounded claims propagate across agents.

        A fabrication cascade is a path A → B → C → … where every node
        holds claims at ``UNGROUNDED_CLAIM`` trust.  These represent the
        most dangerous failure mode: a hallucination propagating unchecked.

        Uses DFS from every ungrounded root.
        """
        # Build adjacency from edges
        adj: dict[str, list[str]] = defaultdict(list)
        for edge in graph.edges:
            adj[edge.source_agent].append(edge.target_agent)

        ungrounded_agents = {
            aid
            for aid, node in graph.nodes.items()
            if node.trust <= TrustLevel.UNGROUNDED_CLAIM
        }

        # Find root agents (no incoming edges from other ungrounded agents)
        has_ungrounded_parent: set[str] = set()
        for edge in graph.edges:
            if (
                edge.source_agent in ungrounded_agents
                and edge.target_agent in ungrounded_agents
            ):
                has_ungrounded_parent.add(edge.target_agent)

        roots = ungrounded_agents - has_ungrounded_parent
        if not roots:
            # All ungrounded agents form cycles — pick any as root
            roots = ungrounded_agents

        cascades: list[list[str]] = []

        def _dfs(agent: str, path: list[str], visited: set[str]) -> None:
            extended = False
            for neighbour in adj.get(agent, []):
                if neighbour in visited:
                    continue
                if neighbour in ungrounded_agents:
                    visited.add(neighbour)
                    path.append(neighbour)
                    _dfs(neighbour, path, visited)
                    path.pop()
                    visited.discard(neighbour)
                    extended = True
            if not extended and len(path) >= 2:
                cascades.append(list(path))

        for root in sorted(roots):
            _dfs(root, [root], {root})

        # Deduplicate cascades that are sub-paths of longer ones
        cascades.sort(key=len, reverse=True)
        unique: list[list[str]] = []
        seen_sets: list[set[str]] = []
        for cascade in cascades:
            cset = set(cascade)
            if not any(cset <= existing for existing in seen_sets):
                unique.append(cascade)
                seen_sets.append(cset)

        return unique

    @staticmethod
    def information_flow_matrix(
        graph: ProvenanceGraph,
    ) -> dict[tuple[str, str], int]:
        """Count how many claims flow between each ordered agent pair.

        Returns ``{(source, target): claim_count}``.
        """
        matrix: dict[tuple[str, str], int] = defaultdict(int)
        for edge in graph.edges:
            matrix[(edge.source_agent, edge.target_agent)] += len(
                edge.claims_transferred
            )
        return dict(matrix)

    @staticmethod
    def critical_path(graph: ProvenanceGraph) -> list[str]:
        """Find the longest provenance chain (most hops from origin).

        Performs BFS from every root node (no incoming edges) and returns
        the path with the most hops.
        """
        if not graph.nodes:
            return []

        # Build forward adjacency
        adj: dict[str, list[str]] = defaultdict(list)
        has_incoming: set[str] = set()
        for edge in graph.edges:
            adj[edge.source_agent].append(edge.target_agent)
            has_incoming.add(edge.target_agent)

        roots = [
            aid for aid in graph.nodes if aid not in has_incoming
        ]
        if not roots:
            # Cycle — use all nodes as potential roots
            roots = list(graph.nodes.keys())

        longest: list[str] = []

        for root in roots:
            # BFS tracking full paths
            queue: deque[list[str]] = deque([[root]])
            visited: set[str] = {root}
            while queue:
                path = queue.popleft()
                current = path[-1]
                extended = False
                for neighbour in adj.get(current, []):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        new_path = path + [neighbour]
                        queue.append(new_path)
                        extended = True
                if not extended and len(path) > len(longest):
                    longest = path

        return longest

    @staticmethod
    def trust_decay_analysis(
        graph: ProvenanceGraph,
    ) -> list[dict[str, Any]]:
        """Analyse how trust evolves along each provenance chain.

        For every chain, returns a dict with:
        - ``claim_id``: the claim being traced
        - ``claim_text``: first 80 chars of the claim text
        - ``hops``: list of ``{agent, trust, trust_value}`` dicts
        - ``trust_delta``: overall change from origin to current holder
        - ``monotonic``: whether trust never increased along the chain
        """
        results: list[dict[str, Any]] = []

        for chain in graph.trace_all_claims():
            if not chain.links:
                continue

            hops: list[dict[str, Any]] = []
            for link in chain.links:
                hops.append(
                    {
                        "agent": link.agent_id,
                        "trust": link.trust.name,
                        "trust_value": int(link.trust),
                    }
                )

            trust_values = [h["trust_value"] for h in hops]
            first = trust_values[0]
            last = trust_values[-1]

            # Monotonic means trust never went up along the chain
            monotonic = all(
                trust_values[i] >= trust_values[i + 1]
                for i in range(len(trust_values) - 1)
            )

            results.append(
                {
                    "claim_id": chain.claim.claim_id,
                    "claim_text": chain.claim.text[:80],
                    "hops": hops,
                    "trust_delta": last - first,
                    "monotonic": monotonic,
                }
            )

        return results
