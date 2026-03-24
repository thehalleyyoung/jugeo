# jugeo-agents

**Sheaf-theoretic verification for multi-agent LLM systems.**

> *The type system for agent architectures: automatic contradiction detection,
> trust algebra, provenance tracing, convergence guarantees, and treaty
> negotiation for any multi-agent LLM pipeline.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## The Problem

Multi-agent LLM systems (CrewAI, AutoGen, LangGraph) are exploding in
adoption — and shipping with **zero formal guarantees**:

| Bug class | Example | Current detection |
|-----------|---------|-------------------|
| **Agent contradiction** | Researcher says "founded in 2019", analyst says "founded in 2018" | Manual reading |
| **Task decomposition gap** | Nobody assigned to write the executive summary | Hope |
| **Cascading hallucination** | Agent B builds on Agent A's hallucination; Agent C confirms both | Invisible |
| **Trust laundering** | LLM-generated number gets passed through pipeline, treated as verified | Invisible |
| **Infinite delegation loop** | Agents keep revising each other's output forever | `max_iterations=10` |

**jugeo-agents** catches all of these automatically, using the mathematics
of sheaf theory — without requiring you to know any sheaf theory.

## Installation

```bash
pip install jugeo-agents            # Core (zero dependencies)
pip install jugeo-agents[dashboard]  # + Flask trust dashboard
pip install jugeo-agents[crewai]     # + CrewAI adapter
pip install jugeo-agents[langgraph]  # + LangGraph adapter
pip install jugeo-agents[all]        # Everything
```

## Quick Start — 30-Second Contradiction Detection

```python
from jugeo_agents import JuGeoAgentWrapper

jugeo = JuGeoAgentWrapper()

# Feed agent outputs
jugeo.on_agent_output(
    agent_id="researcher",
    output="Acme Corp was founded in 2019. It has 500 employees and "
           "reported revenue of $4.2M in Q3 2024.",
    metadata={"model": "claude-sonnet-4"},
)

result = jugeo.on_agent_output(
    agent_id="analyst",
    output="Since its founding in 2018, Acme Corp has grown to 450 staff. "
           "Revenue declined slightly in Q3 2024 to $3.8M.",
    metadata={"model": "gpt-4o"},
)

# Contradictions detected automatically!
print(f"Status: {result.status}")          # "conflict_detected"
print(f"Obstructions: {len(result.obstructions)}")  # 3
for obs in result.obstructions:
    for c in obs.contradictions:
        print(f"  {c.kind.name}: {c.explanation}")
        # TEMPORAL_CONTRADICTION: "2019" vs "2018"
        # QUANTITATIVE_CONTRADICTION: "500" vs "450"
        # QUANTITATIVE_CONTRADICTION: "$4.2M" vs "$3.8M"
```

## Full Pipeline Example — Research Report with 4 Agents

This is the impressive demo. Copy-paste it and run it:

```python
#!/usr/bin/env python3
"""Full multi-agent verification pipeline.

Simulates a 4-agent research report pipeline and verifies every stage:
task coverage, cross-agent consistency, trust classification, provenance
tracing, convergence monitoring, treaty negotiation, and challenge
adjudication.

Run with:  python research_pipeline.py
"""

from jugeo_agents import JuGeoAgentWrapper, TrustLevel

jugeo = JuGeoAgentWrapper(
    auto_negotiate=True,
    auto_challenge=True,
    convergence_patience=3,
    token_budget=100.0,
)

# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Pre-flight coverage check
# ═══════════════════════════════════════════════════════════════════════

coverage = jugeo.verify_task_decomposition(
    task="Write a comprehensive research report on the competitive "
         "landscape of quantum computing startups, including market size, "
         "key players, technology approaches, funding history, and "
         "future outlook with executive summary and citations",
    subtasks=[
        {"name": "research", "scope": "find and evaluate primary sources on quantum computing startups"},
        {"name": "analysis", "scope": "synthesize findings into key themes and trends"},
        {"name": "writing", "scope": "draft the full report body"},
        {"name": "review", "scope": "review for accuracy and coherence"},
    ],
)

print("=" * 70)
print("STEP 1: TASK DECOMPOSITION COVERAGE CHECK")
print("=" * 70)
print(f"  Coverage score: {coverage.coverage_score:.0%}")
print(f"  Complete: {coverage.is_complete}")
if coverage.gaps:
    print(f"  ⚠️  GAPS DETECTED:")
    for gap in sorted(coverage.gaps):
        print(f"      - {gap}")
if coverage.suggestions:
    print(f"  Suggestions:")
    for s in coverage.suggestions[:5]:
        print(f"      {s}")
print()

# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Feed agent outputs through verification pipeline
# ═══════════════════════════════════════════════════════════════════════

agents = [
    {
        "agent_id": "researcher",
        "output": (
            "Based on analysis of 15 primary sources, the quantum computing "
            "startup landscape in 2024 is dominated by several key players. "
            "IonQ, founded in 2015, leads the trapped-ion approach with a "
            "market cap of $3.2B. Rigetti Computing, founded in 2013, pursues "
            "superconducting qubits. The total addressable market for quantum "
            "computing was valued at $1.3B in 2024 and is projected to reach "
            "$5.3B by 2028. PsiQuantum has raised $665M in total funding. "
            "IBM's Osprey processor achieved 433 qubits in November 2022. "
            "Google's Willow chip demonstrated quantum error correction in "
            "December 2024 with 105 qubits."
        ),
        "metadata": {
            "model": "claude-sonnet-4",
            "tools_used": ["web_search", "arxiv_search"],
            "rag_sources": ["crunchbase", "arxiv", "techcrunch"],
            "subtask": "research",
            "derived_from": [],
        },
    },
    {
        "agent_id": "analyst",
        "output": (
            "Key themes from the quantum computing startup landscape: "
            "1) Hardware diversity: trapped-ion (IonQ), superconducting "
            "(Rigetti, IBM), photonic (PsiQuantum, Xanadu), and neutral atom "
            "(QuEra) approaches are all competing. "
            "2) The market was valued at $1.1B in 2024 (conservative estimate). "
            "3) IonQ is the market leader by revenue with 200 employees. "
            "4) PsiQuantum raised $450M in total funding. "
            "5) Funding is concentrated: top 5 startups hold 70% of total VC "
            "investment. "
            "6) Error correction is the key technical milestone — Google's "
            "Willow achieved this in 2024."
        ),
        "metadata": {
            "model": "gpt-4o",
            "subtask": "analysis",
            "derived_from": ["researcher"],
        },
    },
    {
        "agent_id": "writer",
        "output": (
            "# Quantum Computing Startup Landscape 2024\n\n"
            "## Executive Summary\n"
            "The quantum computing market, valued at $1.3 billion in 2024, "
            "is experiencing rapid growth driven by hardware breakthroughs "
            "and increasing enterprise adoption.\n\n"
            "## Key Players\n"
            "IonQ leads the trapped-ion approach with 200 employees and a "
            "$3.2B market cap. Rigetti Computing pursues superconducting "
            "qubits. PsiQuantum has raised $665M pursuing photonic quantum "
            "computing.\n\n"
            "## Market Size\n"
            "The total addressable market was $1.3B in 2024, projected to "
            "reach $5.3B by 2028, representing a CAGR of 42%.\n\n"
            "## Technology Landscape\n"
            "Google achieved a major error correction milestone in December "
            "2024 with 105 qubits on the Willow chip. IBM's Eagle processor "
            "reached 433 qubits in 2022."
        ),
        "metadata": {
            "model": "claude-sonnet-4",
            "subtask": "writing",
            "derived_from": ["researcher", "analyst"],
        },
    },
    {
        "agent_id": "reviewer",
        "output": (
            "Review findings:\n"
            "1. CONFIRMED: IonQ market cap $3.2B (verified via NASDAQ)\n"
            "2. CONFIRMED: Google Willow error correction December 2024\n"
            "3. CONFLICT: Market size — researcher says $1.3B, analyst says "
            "$1.1B. The $1.3B figure from Statista is more widely cited.\n"
            "4. CONFLICT: PsiQuantum funding — researcher says $665M, analyst "
            "says $450M. Crunchbase confirms $665M total.\n"
            "5. ISSUE: Writer says 'IBM Eagle' but researcher said 'IBM Osprey' "
            "— the 433-qubit processor is Osprey, not Eagle.\n"
            "6. GAP: No discussion of Chinese quantum computing efforts "
            "(Origin Quantum, SpinQ).\n"
            "7. CONFIRMED: IonQ employee count approximately 200."
        ),
        "metadata": {
            "model": "gpt-4o",
            "tools_used": ["web_search"],
            "subtask": "review",
            "derived_from": ["writer", "researcher", "analyst"],
        },
    },
]

print("=" * 70)
print("STEP 2: AGENT OUTPUT VERIFICATION")
print("=" * 70)

for agent in agents:
    result = jugeo.on_agent_output(**agent)
    status_icon = {"consistent": "✅", "conflict_detected": "⚠️", "conflict_resolved": "🔧"}.get(
        result.status, "❓"
    )
    print(f"\n  Agent: {agent['agent_id']}")
    print(f"    Status: {status_icon} {result.status}")
    print(f"    Trust:  {result.trust_level.name}")
    print(f"    Claims: {result.claims_extracted}")
    if result.obstructions:
        print(f"    Obstructions: {len(result.obstructions)}")
        for obs in result.obstructions:
            for c in obs.contradictions:
                print(f"      ❌ {c.kind.name}: {c.explanation[:80]}")
    if result.treaties:
        resolved = sum(1 for t in result.treaties if t.success)
        print(f"    Treaties: {resolved}/{len(result.treaties)} resolved")
    if result.suggestions:
        for s in result.suggestions:
            print(f"    💡 {s}")

print()

# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Full pipeline report
# ═══════════════════════════════════════════════════════════════════════

report = jugeo.on_pipeline_complete()

print("=" * 70)
print("STEP 3: FULL PIPELINE REPORT")
print("=" * 70)
print()
print(report.summary_text())
print()

# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Provenance tracing
# ═══════════════════════════════════════════════════════════════════════

print("=" * 70)
print("STEP 4: PROVENANCE TRACING")
print("=" * 70)

# Trace a specific claim
for claim_text in ["IonQ", "1.3", "PsiQuantum"]:
    chain = jugeo.provenance_for(claim_text)
    if chain:
        print(f"\n  Claim: '{chain.claim.text[:60]}...'")
        print(f"  Overall trust: {chain.overall_trust.name}")
        if chain.weakest_link:
            print(f"  Weakest link: {chain.weakest_link.agent_id} ({chain.weakest_link.trust.name})")
        for lk in chain.links:
            print(f"    → {lk.agent_id}: {lk.action} [{lk.trust.name}]")

print()

# ═══════════════════════════════════════════════════════════════════════
# STEP 5: Convergence analysis
# ═══════════════════════════════════════════════════════════════════════

print("=" * 70)
print("STEP 5: CONVERGENCE ANALYSIS")
print("=" * 70)
print(f"  Status: {jugeo.convergence_status().value}")
print(f"  Phase:  {jugeo._convergence.current_phase().value}")
history = jugeo._convergence.history()
if history:
    for snap in history:
        print(
            f"  Round {snap.round_number}: V={snap.lyapunov_v:.3f} "
            f"cov={snap.coverage:.2f} con={snap.consistency:.2f} "
            f"trust={snap.trust_level:.2f} [{snap.phase.value}]"
        )
print()

# ═══════════════════════════════════════════════════════════════════════
# STEP 6: Next action suggestion
# ═══════════════════════════════════════════════════════════════════════

print("=" * 70)
print("STEP 6: RECOMMENDED NEXT ACTION")
print("=" * 70)
action = jugeo.suggest_next_action()
print(f"  Action:    {action.action_type.name}")
print(f"  Target:    {action.target_agent}")
print(f"  Priority:  {action.priority:.2f}")
print(f"  Rationale: {action.rationale}")
print()
print("Done! 🎉")
```

## What It Detects

### H¹ Obstructions — Pairwise Contradictions
Two agents disagree on a specific fact:
```
❌ QUANTITATIVE_CONTRADICTION: "market valued at $1.3B" vs "market valued at $1.1B"
❌ QUANTITATIVE_CONTRADICTION: "raised $665M" vs "raised $450M"  
❌ ENTITY_CONTRADICTION: "IBM Osprey" vs "IBM Eagle"
```

### H² Obstructions — Cascading Hallucinations
Agent A hallucinates → Agent B builds on it → Agent C confirms both.
All pairwise overlaps look consistent, but the entire chain is fabricated:
```
🔴 CASCADING_HALLUCINATION: Ungrounded claim chain: analyst → writer → reviewer
   Root: analyst generated "40% enterprise growth" without evidence
   Cascade: writer included it, reviewer confirmed it
   All claims at UNGROUNDED_CLAIM trust — phantom global section
```

### H⁰ Obstructions — Coverage Gaps
The task decomposition misses an aspect of the goal:
```
⚠️ COVER_GAP: No subtask covers: executive_summary, citation_formatting
   Suggestion: Add a subtask covering these dimensions
```

### Trust Boundary Violations
An LLM claim is treated as tool-verified without tool execution:
```
⚠️ TRUST_BOUNDARY_VIOLATION: Claim "revenue grew 40%" at TOOL_VERIFIED
   but no tool was executed — silent promotion detected
```

## Architecture

```
jugeo_agents/
├── types.py              # Shared types: TrustLevel, Obstruction, FactualClaim, ...
├── wrapper.py            # JuGeoAgentWrapper — the 3-method API
├── core/
│   ├── trust.py          # Trust algebra (ordered, no-silent-promotion)
│   ├── claims.py         # Regex claim extraction + contradiction detection
│   ├── descent.py        # Descent engine (consistency checking)
│   ├── covers.py         # Task decomposition completeness
│   ├── obstructions.py   # H0/H1/H2/phantom classification
│   └── provenance.py     # Provenance graph tracing
├── orchestration/
│   ├── convergence.py    # Lyapunov convergence monitor
│   ├── calibration.py    # Per-model trust calibration
│   ├── challenge.py      # Agent challenge protocol
│   ├── treaty.py         # Treaty negotiation
│   ├── treaty_memory.py  # Cross-pipeline learning
│   ├── routing.py        # Cost/trust-aware evidence routing
│   └── control.py        # Phase-adaptive control law
├── adapters/
│   ├── base.py           # Base protocol + GenericAdapter
│   ├── crewai_adapter.py
│   ├── langgraph_adapter.py
│   └── autogen_adapter.py
└── dashboard/
    ├── app.py            # Flask real-time dashboard
    ├── templates/
    └── static/
```

## Framework Integration

### CrewAI

```python
from crewai import Crew, Agent, Task
from jugeo_agents import JuGeoAgentWrapper
from jugeo_agents.adapters.crewai_adapter import CrewAIAdapter

crew = Crew(agents=[...], tasks=[...])
jugeo = JuGeoAgentWrapper()
adapter = CrewAIAdapter(crew)

# Pre-flight check
coverage = jugeo.verify_task_decomposition(*adapter.get_task_decomposition())

# Install verification callback
adapter.install_callback(jugeo)

# Run as usual
crew.kickoff()
report = jugeo.on_pipeline_complete()
print(report.summary_text())
```

### LangGraph

```python
from langgraph.graph import StateGraph
from jugeo_agents import JuGeoAgentWrapper
from jugeo_agents.adapters.langgraph_adapter import LangGraphAdapter

jugeo = JuGeoAgentWrapper()
adapter = LangGraphAdapter(task="Analyze data", subtasks=[...])

graph = StateGraph(MyState)
graph.add_node("researcher", adapter.wrap_node(researcher_fn, jugeo))
graph.add_node("analyst", adapter.wrap_node(analyst_fn, jugeo))
```

### AutoGen

```python
from jugeo_agents import JuGeoAgentWrapper
from jugeo_agents.adapters.autogen_adapter import AutoGenAdapter

jugeo = JuGeoAgentWrapper()
adapter = AutoGenAdapter(task="Research", subtasks=[...])
for agent in [researcher, analyst, writer]:
    adapter.register_hook(agent, jugeo)
```

## Trust Dashboard

Launch a real-time web dashboard showing trust-colored claims, obstruction
timeline, provenance graphs, and convergence charts:

```bash
pip install jugeo-agents[dashboard]
jugeo-dashboard  # Opens at http://localhost:5050
```

Or programmatically:

```python
from jugeo_agents.dashboard.app import create_app
app = create_app(jugeo_wrapper)
app.run(port=5050)
```

Push agent outputs via HTTP:

```bash
curl -X POST http://localhost:5050/api/push \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "researcher", "output": "The company has 500 employees."}'
```

## The Three Trust Laws

jugeo-agents enforces three fundamental invariants from JuGeo's trust algebra:

1. **No silent promotion**: An `UNGROUNDED_CLAIM` cannot become
   `RAG_GROUNDED` without actually retrieving a supporting document.
   An LLM saying "according to research..." is still `UNGROUNDED_CLAIM`.

2. **Conservative join**: `TOOL_VERIFIED ⊕ UNGROUNDED_CLAIM = UNGROUNDED_CLAIM`.
   You cannot launder hallucinated facts through a pipeline that also
   contains verified facts.

3. **Challenge conservativity**: When Agent B provides evidence contradicting
   Agent A, the system must demote A's claim. It cannot leave both standing.

## Theory

jugeo-agents is the practical implementation of the theory described in
[Geometry of Multi-Agent LLM Systems](GEOMETRY_OF_MULTI_AGENT_LLM_SYSTEMS.md).

The mathematical foundation is **Čech cohomology on agent sites**:
- **H⁰** = section incompleteness (agent didn't produce output)
- **H¹** = pairwise contradiction (two agents disagree)
- **H²** = cascading hallucination (pairwise consistent but globally fabricated)
- **Phantom** = consistent everywhere but entirely ungrounded

The convergence monitor uses a **Lyapunov function** V(state) =
α(1-coverage) + β·obstructions + γ·trust_debt + δ·obligations,
providing mathematical convergence guarantees instead of `max_iterations=10`.

## License

MIT
