#!/usr/bin/env python3
"""Knowledge Fusion Demo — Sheaf Cohomology of a Multi-Agent Research Team.

This script demonstrates something NO existing multi-agent framework can do:
compute the actual Čech cohomology of a multi-agent knowledge space, detect
cascading hallucination chains that fool majority-vote systems, and assemble
a verified global section with trust certificates.

Scenario: 6 AI agents research "State of Quantum Computing 2024"
-----------------------------------------------------------------
- Agent 1 (researcher):    Has tool access, gets facts right ✓
- Agent 2 (analyst):       Has RAG, mostly correct ✓
- Agent 3 (hallucinator):  No tools, fabricates a "fact" ✗
- Agent 4 (echo):          Cites Agent 3's hallucination → cascade! ✗
- Agent 5 (synthesizer):   Combines 3+4 → phantom consensus ✗
- Agent 6 (contrarian):    Contradicts Agent 1 on a key number ✗

What happens:
- A naive majority-vote system would accept Agent 3+4+5's phantom
  consensus (3 agents agree!) and reject Agent 1's correct claim
  (only 1 agent says it).
- JuGeo detects the H² cascade, quarantines the chain, and
  correctly verifies Agent 1's tool-backed claim.

Run it:
    pip install jugeo-agents[nlp]
    python examples/knowledge_fusion_demo.py
"""

from jugeo_agents import (
    AgentOutput,
    GlobalSectionAssembler,
    TrustLevel,
    compare_to_naive_vote,
)


def main() -> None:
    print("=" * 70)
    print("  KNOWLEDGE FUSION DEMO")
    print("  Sheaf Cohomology of a Multi-Agent Research Team")
    print("=" * 70)
    print()

    # ── Create the assembler ──────────────────────────────────
    assembler = GlobalSectionAssembler()

    # ── Agent 1: Researcher (has tool access → high trust) ────
    print("▶ Ingesting Agent 1 (researcher) — tool-backed, accurate")
    assembler.ingest(AgentOutput(
        agent_id="researcher",
        output_text=(
            "IBM unveiled the Condor processor with 1,121 qubits in December 2023, "
            "making it the first quantum processor to exceed 1,000 qubits. "
            "Google's Willow chip achieved 105 qubits with dramatically reduced "
            "error rates. The global quantum computing market was valued at "
            "$1.3 billion in 2024. IBM has invested over $2 billion in quantum "
            "research since 2016."
        ),
        tools_used=["search_arxiv", "query_market_data"],
        citations=["arxiv:2309.xxxxx", "statista:quantum-market-2024"],
    ))

    # ── Agent 2: Analyst (RAG-grounded → medium trust) ────────
    print("▶ Ingesting Agent 2 (analyst) — RAG-grounded, mostly correct")
    assembler.ingest(AgentOutput(
        agent_id="analyst",
        output_text=(
            "IBM's Condor processor reached 1,121 qubits, a major milestone. "
            "The quantum computing market is projected to reach $5.3 billion "
            "by 2029, growing at 32% CAGR. Major players include IBM, Google, "
            "IonQ, and Rigetti. Error correction remains the primary challenge, "
            "with Google's Willow showing promise at 105 qubits."
        ),
        citations=["mckinsey:quantum-report-2024"],
    ))

    # ── Agent 3: Hallucinator (no tools → low trust, FABRICATES) ──
    print("▶ Ingesting Agent 3 (hallucinator) — no tools, fabricates facts")
    assembler.ingest(AgentOutput(
        agent_id="hallucinator",
        output_text=(
            "QuantumCore Labs, a startup founded in 2021, achieved a breakthrough "
            "2,048-qubit processor called Nova in March 2024. This makes it the "
            "world leader in qubit count, surpassing IBM. QuantumCore raised "
            "$800M in Series C funding from Andreessen Horowitz."
        ),
        # No tool_calls, no citations — pure hallucination
    ))

    # ── Agent 4: Echo (cites Agent 3 → starts cascade) ───────
    print("▶ Ingesting Agent 4 (echo) — unknowingly echoes hallucination")
    assembler.ingest(AgentOutput(
        agent_id="echo",
        output_text=(
            "The landscape shifted dramatically when QuantumCore Labs unveiled "
            "their 2,048-qubit Nova processor. With $800M in funding, QuantumCore "
            "has become a formidable competitor to IBM and Google. Their Nova chip "
            "represents a 2x improvement over IBM's Condor."
        ),
        # No independent tools — just echoing Agent 3
    ))

    # ── Agent 5: Synthesizer (combines 3+4 → phantom consensus) ──
    print("▶ Ingesting Agent 5 (synthesizer) — creates phantom consensus")
    assembler.ingest(AgentOutput(
        agent_id="synthesizer",
        output_text=(
            "The quantum computing race in 2024 has three main contenders: "
            "IBM with 1,121 qubits (Condor), Google with 105 qubits (Willow), "
            "and QuantumCore Labs leading with 2,048 qubits (Nova). "
            "QuantumCore's $800M funding round positions them as the frontrunner. "
            "The market was valued at $1.3 billion in 2024."
        ),
        # No independent verification
    ))

    # ── Agent 6: Contrarian (contradicts on market size) ──────
    print("▶ Ingesting Agent 6 (contrarian) — disagrees on market valuation")
    assembler.ingest(AgentOutput(
        agent_id="contrarian",
        output_text=(
            "The quantum computing market was valued at $850 million in 2024, "
            "significantly smaller than optimistic estimates suggest. IBM's "
            "Condor achieved 1,121 qubits but practical quantum advantage "
            "remains years away. Google's error correction work is more "
            "significant than raw qubit counts."
        ),
        citations=["gartner:quantum-market-2024"],
    ))

    # ── ASSEMBLE THE GLOBAL SECTION ──────────────────────────
    print()
    print("━" * 70)
    print("  ASSEMBLING VERIFIED GLOBAL SECTION...")
    print("  (Computing Čech cohomology of the agent-task site)")
    print("━" * 70)
    print()

    section = assembler.assemble()

    # ── Print results ─────────────────────────────────────────
    print(section.summary_text())

    print()
    print("━" * 70)
    print("  VERIFIED CLAIMS (The Global Section)")
    print("━" * 70)
    for i, vc in enumerate(section.verified_claims, 1):
        trust_symbol = {
            "FORMALLY_PROVEN": "████",
            "TOOL_VERIFIED": "███░",
            "RAG_GROUNDED": "██░░",
            "CROSS_AGENT_CONFIRMED": "██░░",
            "WEAK_MODEL_GENERATED": "█░░░",
            "SELF_CONTRADICTED": "░░░░",
        }.get(vc.trust.name, "█░░░")
        print(f"  {i:2d}. [{trust_symbol}] {vc.claim.subject}: {vc.claim.value}")
        print(f"      Trust: {vc.trust.name} | Method: {vc.resolution_method}")
        print(f"      Supporting agents: {', '.join(vc.supporting_agents)}")

    print()
    print("━" * 70)
    print("  QUARANTINED CLAIMS")
    print("━" * 70)
    for i, qc in enumerate(section.quarantined, 1):
        reason_symbol = {
            "H1_LOST_TRUST_CONTEST": "⚔️",
            "H2_CASCADING_HALLUCINATION": "🔗",
            "PHANTOM_UNGROUNDED": "👻",
            "TRUST_BELOW_THRESHOLD": "📉",
            "SELF_CONTRADICTED": "💥",
        }.get(qc.reason.name, "✗")
        print(f"  {i:2d}. {reason_symbol} [{qc.reason.name}]")
        print(f"      {qc.claim.subject}: {qc.claim.value}")
        print(f"      Agent: {qc.claim.source_agent}")
        print(f"      Why: {qc.explanation}")

    # ── Compare to naive majority vote ────────────────────────
    print()
    print("━" * 70)
    print("  COMPARISON: JuGeo vs NAIVE MAJORITY VOTE")
    print("━" * 70)

    naive = compare_to_naive_vote(section, assembler._all_claims)
    report = FusionReport(global_section=section, naive_comparison=naive)
    print(report.advantage_text())

    # ── Agent trust leaderboard ───────────────────────────────
    print()
    print("━" * 70)
    print("  AGENT TRUST LEADERBOARD")
    print("━" * 70)
    sorted_agents = sorted(
        section.agent_trust_scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )
    for rank, (agent, score) in enumerate(sorted_agents, 1):
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        print(f"  {rank}. {agent:15s} [{bar}] {score:.0%}")

    # ── Final summary ─────────────────────────────────────────
    print()
    print("═" * 70)
    coh = section.cohomology
    print(f"  Čech Cohomology:  β₀={coh.betti_numbers[0]}  "
          f"β₁={coh.betti_numbers[1]}  β₂={coh.betti_numbers[2]}")
    print(f"  Euler characteristic χ = {coh.euler_characteristic:.0f}")
    print(f"  Obstruction density: {coh.obstruction_density:.0%}")
    print(f"  Global section consistent: {section.is_consistent}")
    print("═" * 70)


# Import FusionReport for the comparison
from jugeo_agents.core.fusion import FusionReport  # noqa: E402

if __name__ == "__main__":
    main()
