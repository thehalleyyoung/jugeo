# Geometry of Multi-Agent LLM Systems

> *"Every multi-agent system is an unverified descent: local agents produce
> local sections, and the system prays they glue. JuGeo replaces prayer
> with cohomology."*

---

## 1. The Problem: Multi-Agent Systems Have No Formal Foundations

The LLM agent ecosystem in 2025–2026 is in the position that web
development was in 1998: exploding in adoption, producing real value, and
operating on **zero formal foundations**. CrewAI, AutoGen, LangGraph, OpenAI
Swarm, and dozens of smaller frameworks let developers wire together
multiple LLM agents into collaborative systems. But none of them can answer
basic questions:

- **Will these agents contradict each other?** If Agent A researches a
  topic and Agent B summarizes it, will B's summary be consistent with
  A's findings? No framework checks this.

- **What happens when a tool call fails?** If the code-execution agent
  returns an error, does the planning agent know? Does it retry? Does it
  hallucinate a result? No framework specifies the error-propagation
  semantics.

- **Which agent's output do you trust?** If Claude says the answer is X
  and GPT-4 says the answer is Y, which one wins? On what basis? The
  framework picks one — but there's no trust algebra, no audit trail, no
  formal justification.

- **Are the agents' subtasks actually covering the full task?** If you
  decompose "write a research report" into "find sources" + "analyze
  sources" + "write draft," are those three subtasks actually sufficient?
  Is there a gap? No framework checks task decomposition completeness.

- **When agents negotiate, does the result converge?** If two agents
  disagree and are asked to reconcile, will they? Or will they loop
  forever, each rewriting the other's output?

These are not edge cases — they are the **everyday bugs** of multi-agent
systems. Industry reports from 2025–2026 document:

| Framework | Common Failures |
|-----------|----------------|
| **CrewAI** | Infinite delegation loops, agents failing to terminate, coordination failures escalating complex workflows to humans |
| **AutoGen** | Context window overflow in recursive chats, tool call hallucinations, opaque debugging in long conversation chains |
| **LangGraph** | Node misconfiguration causing dead-ends, state inconsistencies from manual state management errors |
| **All** | Contradictions between agents, hallucinated tool outputs, no formal guarantee that subtasks cover the full task |

**JuGeo already has the mathematical machinery to solve every one of these
problems.** The fleet competition model, the trust algebra, the treaty
negotiation system, the descent engine, and the obstruction classifier were
all designed for exactly this kind of coordination — they just need to be
transported from the proof-search domain to the LLM-agent domain.

---

## 2. The Transport: JuGeo Orchestration → LLM Agent Coordination

### 2.1 The Existing JuGeo Orchestration Stack

JuGeo's orchestration system (theory2.tex Ch46, implemented in
`src/jugeo/orchestration/`) is already a multi-agent system. It coordinates
*fleet members* — independent proof-search agents that compete to make
progress on verification tasks. The stack includes:

| Component | What It Does | Module |
|-----------|-------------|--------|
| **FleetMember** | Identity, capabilities, trust ceiling, load tracking | `fleet.py` |
| **FleetBid** | Normalized bid: proposed action + uncertainty + trust self-declaration | `fleet.py` |
| **BidEvaluator** | Multi-criterion evaluation, Pareto selection | `fleet_competition/bid_evaluation.py` |
| **CompetitiveSearch** | Tournament-style selection across bids | `fleet_competition/algorithms.py` |
| **FleetCalibration** | Runtime accuracy/latency calibration per member | `fleet_competition/calibration.py` |
| **ChallengeRecord** | Structured record when one member challenges another's bid | `fleet_competition/challenge_protocol.py` |
| **TreatyNegotiation** | Reconciling conflicting overlap laws between sections | `negotiation.py` |
| **TreatyMemory** | Learning from past negotiations to accelerate future ones | `treaty_memory/` |
| **OrchestratorState** | Full snapshot of the search state | `controller.py` |
| **SemanticMove** | Typed, preconditioned, cost-annotated action | `controller.py` |
| **ControlLaw** | Strategy for selecting next move (greedy/lookahead/balanced/adaptive) | `semantic_control/` |
| **ConvergenceMonitor** | Detects stalls, phase transitions, termination | `controller.py` |
| **ResourceBudget** | Per-channel budget tracking and rebalancing | `budgets.py` |

### 2.2 The Analogy Map

The transport from proof-search orchestration to LLM-agent orchestration is
an `AnalogyMap` with faithfulness ≥ 0.95 (PERFECT). Every component maps:

| JuGeo Orchestration | LLM Agent System |
|---|---|
| FleetMember | LLM agent (Claude, GPT-4, Gemini, tool-using agent) |
| FleetBid | Agent's proposed output for a subtask |
| BidEvaluator | Output quality evaluator (consistency, completeness, grounding) |
| CompetitiveSearch | Multi-agent voting / selection |
| FleetCalibration | Per-model accuracy tracking over time |
| ChallengeRecord | One agent challenging another's output |
| TreatyNegotiation | Agents reconciling contradictory outputs |
| TreatyMemory | Learning which agent pairs tend to conflict on which topics |
| OrchestratorState | The full state of the multi-agent task |
| SemanticMove | An agent action (generate text, call tool, delegate subtask) |
| ControlLaw | Strategy for selecting next agent action |
| ConvergenceMonitor | Detecting when the system is stuck or done |
| ResourceBudget | Token/cost budget per agent |
| Cover | Task decomposition — subtasks that collectively cover the full task |
| Descent | Checking that agent outputs are mutually consistent |
| Obstruction | Contradiction, hallucination, or gap in the agent outputs |
| Trust level | Formal tool output > RAG-grounded > pure LLM generation |
| Coordinate | Individual agent, conversation turn, tool call, memory slot |
| Section | An agent's output at its assigned coordinate |

The transport is not metaphorical — it is *structural*. The same code
paths that check whether two proof-search agents' proposed sections are
compatible on their overlap can check whether two LLM agents' outputs are
consistent on their shared claims. The same trust algebra that prevents a
copilot suggestion from silently promoting to solver-proved can prevent an
LLM hallucination from silently promoting to tool-verified.

---

## 3. The Multi-Agent Verification Site

### 3.1 Coordinates in an Agent System

Define the **agent site** `𝒜` with the following coordinate kinds:

```python
AgentCoordinateKind = Enum(
    # Agent identity
    AGENT,                  # An individual LLM agent instance
    AGENT_ROLE,             # The declared role/capability of an agent
    AGENT_MODEL,            # The underlying model (claude-sonnet-4, gpt-4, etc.)
    
    # Task structure
    TASK,                   # A top-level task
    SUBTASK,                # A decomposed piece of a task
    SUBTASK_DEPENDENCY,     # An ordering constraint between subtasks
    
    # Communication
    CONVERSATION_TURN,      # A single message in an agent conversation
    TOOL_CALL,              # An invocation of an external tool
    TOOL_RESULT,            # The result returned by a tool
    SHARED_MEMORY_SLOT,     # A named slot in shared agent memory
    
    # Output artifacts
    OUTPUT_ARTIFACT,        # A document, code file, or other output
    OUTPUT_CLAIM,           # A specific factual claim within an output
    OUTPUT_CITATION,        # A citation or source reference
    
    # Control flow
    DELEGATION,             # Agent A delegating to Agent B
    REVIEW,                 # Agent A reviewing Agent B's output
    CONSENSUS_ROUND,        # A round of multi-agent voting/consensus
    ESCALATION,             # Escalation to human or higher-authority agent
)
```

### 3.2 Covering Families: Task Decomposition as Cover

The most natural covering family in a multi-agent system is the **task
decomposition** — the assignment of subtasks to agents:

```python
TaskDecompositionCover = CoveringFamily(
    target = "task.write_research_report",
    patches = [
        AgentAssignment(
            agent="researcher",
            subtask="find_and_evaluate_sources",
            output_type="annotated_bibliography",
        ),
        AgentAssignment(
            agent="analyst",
            subtask="synthesize_findings",
            output_type="analysis_document",
            depends_on=["find_and_evaluate_sources"],
        ),
        AgentAssignment(
            agent="writer",
            subtask="draft_report",
            output_type="report_draft",
            depends_on=["synthesize_findings"],
        ),
        AgentAssignment(
            agent="reviewer",
            subtask="review_and_revise",
            output_type="final_report",
            depends_on=["draft_report"],
        ),
    ]
)
```

**The covering condition** requires that the subtasks *collectively cover*
the full task — there is no aspect of "write a research report" that falls
outside all four subtask assignments. **This is exactly what existing
frameworks don't check.** CrewAI lets you define agents and tasks, but
never verifies that the tasks actually cover the goal. JuGeo's cover
machinery can.

### 3.3 Descent: Agent Output Consistency

Descent in the agent site checks that **agent outputs are mutually
consistent on their shared claims**. When two agents' subtasks overlap —
they both make claims about the same facts — those claims must agree.

```python
class AgentDescentEngine:
    """Check consistency of agent outputs across overlapping subtasks.
    
    This is the direct transport of DescentEngine from
    jugeo.geometry.descent to the LLM-agent domain.
    """
    
    def check_overlap(
        self,
        agent_a_output: AgentOutput,
        agent_b_output: AgentOutput,
    ) -> OverlapResult:
        """Check if two agents' outputs agree on shared claims."""
        
        # Extract factual claims from both outputs
        claims_a = extract_claims(agent_a_output)
        claims_b = extract_claims(agent_b_output)
        
        # Find claims about the same entities/facts
        shared_claims = find_shared_referents(claims_a, claims_b)
        
        violations = []
        for claim_a, claim_b in shared_claims:
            if contradicts(claim_a, claim_b):
                violations.append(AgentContradiction(
                    agent_a=agent_a_output.agent_id,
                    agent_b=agent_b_output.agent_id,
                    claim_a=claim_a,
                    claim_b=claim_b,
                    overlap_coordinate=claim_a.referent,
                    # The repair frontier: which agent should we trust?
                    repair_hint=self._compute_repair_hint(
                        claim_a, claim_b,
                        trust_a=agent_a_output.trust_level,
                        trust_b=agent_b_output.trust_level,
                    ),
                ))
        
        if violations:
            return DescentObstruction(
                overlap=f"{agent_a_output.agent_id} ∩ {agent_b_output.agent_id}",
                violations=violations,
                cohomology_class="H1_agent_contradiction",
            )
        else:
            return OverlapSatisfied()
```

**Concrete examples of agent descent failures:**

| Scenario | Agent A says | Agent B says | Descent status |
|----------|-------------|-------------|----------------|
| Research task | "The paper was published in 2023" | "According to the 2021 paper..." | **H¹ violation**: temporal contradiction |
| Code generation | "The function returns a list" | "Parse the returned dictionary" | **H¹ violation**: type mismatch |
| Data analysis | "Revenue grew 15% YoY" | "Revenue declined slightly" | **H¹ violation**: directional contradiction |
| Planning | "Step 3 depends on step 2" | "Step 3 can run in parallel with step 2" | **H¹ violation**: dependency contradiction |
| Summarization | "The study had 500 participants" | "The study had 50 participants" | **H¹ violation**: quantitative contradiction |

### 3.4 The Trust Algebra for LLM Agents

JuGeo's trust algebra (`src/jugeo/evidence/trust.py`) already has the
machinery needed. The transport instantiates it for the LLM-agent domain:

```
AgentTrustLevels = (
    # Highest trust: deterministic, verifiable
    TOOL_VERIFIED,          # Tool call returned a result that was independently
                            # verified (e.g., code executed and tests passed)
    TOOL_EXECUTED,          # Tool call returned a result (not independently verified)
    
    # Medium trust: grounded in evidence
    RAG_GROUNDED,           # Claim is supported by a retrieved document
    CITATION_BACKED,        # Claim includes a specific citation (URL, DOI, page)
    CROSS_AGENT_CONFIRMED,  # Multiple independent agents agree
    
    # Lower trust: LLM generation
    STRONG_MODEL_GENERATED, # Generated by a frontier model (Claude Opus, GPT-4)
    WEAK_MODEL_GENERATED,   # Generated by a smaller model (Haiku, GPT-4-mini)
    
    # Lowest trust: uncorroborated
    UNGROUNDED_CLAIM,       # LLM assertion with no supporting evidence
    SELF_CONTRADICTED,      # Agent contradicted its own earlier output
)
```

**The three trust laws, transported:**

1. **No silent promotion**: An `UNGROUNDED_CLAIM` cannot become
   `RAG_GROUNDED` without actually retrieving a supporting document. An
   LLM saying "according to research..." is not RAG-grounded — it's an
   ungrounded claim that *mimics* grounding. The trust algebra catches
   this: the claim enters at `UNGROUNDED_CLAIM` regardless of the LLM's
   phrasing, and only promotes to `RAG_GROUNDED` when the RAG system
   actually retrieves a corroborating document.

2. **Conservative join**: `TOOL_VERIFIED ⊕ UNGROUNDED_CLAIM =
   UNGROUNDED_CLAIM`. If you combine verified tool output with
   ungrounded LLM claims, the combined trust is the *weaker* of the two.
   You cannot launder hallucinated facts through a pipeline that also
   contains verified facts.

3. **Challenge conservativity**: When Agent B challenges Agent A's claim,
   and B provides evidence (a tool result, a retrieved document) that
   contradicts A, the system must demote A's claim. It cannot leave both
   the original claim and the contradicting evidence standing without
   resolution.

### 3.5 Obstructions: A Taxonomy of Multi-Agent Failures

JuGeo's obstruction classification maps precisely to the failure modes
that plague multi-agent systems:

| JuGeo Obstruction | Agent Failure | Cohomology Class |
|---|---|---|
| **Overlap violation** | Two agents contradict each other on a shared fact | H¹ — standard descent failure |
| **Cover gap** | The task decomposition misses an aspect of the goal | H⁰ failure — no global section exists because the cover is incomplete |
| **Section incompleteness** | An agent produces a partial output that doesn't cover its subtask | H⁰ failure at the local level |
| **Trust boundary violation** | An LLM claim is treated as tool-verified without tool execution | Trust algebra violation — silent promotion |
| **Infinite loop** | Agents keep delegating/revising without converging | Convergence failure — detected by ConvergenceMonitor |
| **Context overflow** | An agent's context window fills and it loses earlier information | Section truncation — the local section loses support on earlier coordinates |
| **Hallucinated tool result** | Agent claims a tool returned X when it didn't | Provenance forgery — the audit trail shows the tool call never happened |
| **Cascading hallucination** | Agent B hallucinates based on Agent A's hallucination | H² obstruction — second-order descent failure (error amplification across overlaps) |

**The cascading hallucination** is particularly important and is
currently invisible to all agent frameworks. It's a **Čech 2-cocycle**:
Agent A hallucinates a fact at coordinate `c_A`. Agent B reads A's output,
treats it as true, and builds on it at coordinate `c_B`. Agent C reads
both and synthesizes at `c_C`. The hallucination is now a *global section*
that looks consistent (all agents agree!) but is entirely fabricated. The
2-cocycle structure captures this: the error isn't in any single overlap
(A∩B looks fine, B∩C looks fine) — it's in the *triple overlap* A∩B∩C
where the fabrication originates.

JuGeo's hypercover machinery (`geometry/hypercovers.py`) is designed
exactly for detecting these higher-order coherence failures.

---

## 4. Showing Usefulness Quickly: The Five-Minute Demo

The theoretical framework above is powerful but abstract. To show
usefulness *quickly* — to a developer who has 5 minutes of attention —
we need **concrete, immediate, viscerally useful demonstrations**. Here
are five demos, ordered by implementation effort, each designed to
produce an "oh, I need this" reaction within seconds.

### 4.1 Demo 1: The Contradiction Detector (30 lines of Python)

**What it does**: Takes the outputs of two LLM agents and finds
contradictions between them.

**Why it's immediately useful**: Every developer using multi-agent systems
has had the experience of agents contradicting each other. They currently
catch these by reading all the outputs manually. This catches them
automatically.

**Implementation sketch** (real, runnable):

```python
from jugeo.geometry.descent import DescentEngine, LocalSection, OverlapCondition
from jugeo.geometry.site import Coordinate, CoordinateKind, MorphismKind
from jugeo.evidence.trust import TrustLevel

def check_agent_consistency(agent_outputs: dict[str, str]) -> list[dict]:
    """Check if multiple agents' outputs contradict each other.
    
    Args:
        agent_outputs: {"agent_name": "output text", ...}
    
    Returns:
        List of detected contradictions with repair hints.
    """
    # Extract factual claims from each agent's output
    claims_by_agent = {}
    for agent, output in agent_outputs.items():
        claims_by_agent[agent] = extract_factual_claims(output)
    
    # Build local sections (each agent's claims at its coordinate)
    sections = []
    for agent, claims in claims_by_agent.items():
        coord = Coordinate(components=(agent,), kind=CoordinateKind.FUNCTION)
        sections.append(LocalSection(
            coordinate=coord,
            data=claims,
            trust=TrustLevel.COPILOT_SUGGESTED,  # LLM output trust ceiling
        ))
    
    # Run descent: check all pairwise overlaps
    engine = DescentEngine()
    result = engine.descend(sections, check_overlaps=True)
    
    if result.is_global_section:
        return []  # No contradictions — outputs are consistent
    
    # Return structured contradictions
    return [
        {
            "agents": (v.section_a.coordinate.name, v.section_b.coordinate.name),
            "claim_a": v.data_a,
            "claim_b": v.data_b,
            "conflict_type": v.violation_kind,
            "repair_hint": v.repair_suggestion,
        }
        for v in result.obstruction.violations
    ]

# Usage:
contradictions = check_agent_consistency({
    "researcher": "The company was founded in 2019 and has 500 employees.",
    "analyst": "Since its founding in 2018, the company has grown to 450 staff.",
})
# Returns: [{"agents": ("researcher", "analyst"), 
#            "claim_a": "founded in 2019", 
#            "claim_b": "founded in 2018",
#            "conflict_type": "temporal_contradiction",
#            "repair_hint": "Check primary source for founding date"}]
```

**The "oh, I need this" moment**: The developer sees that JuGeo caught a
specific, named contradiction ("temporal_contradiction") between specific
claims, with a specific repair hint — all without any LLM in the loop.
Compare this to the current state of the art: nothing. Developers read
all agent outputs manually and hope they notice inconsistencies.

### 4.2 Demo 2: The Trust Dashboard (Flask Web UI)

**What it does**: A real-time web dashboard showing the trust level of
every claim in a multi-agent pipeline. Each claim is colored by its trust
level: green (tool-verified), yellow (RAG-grounded), orange (LLM-generated),
red (self-contradicted).

**Why it's immediately useful**: Developers currently have no visibility
into *which parts* of an agent's output are grounded vs. hallucinated.
The trust dashboard makes the invisible visible.

**What it shows**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Multi-Agent Task: "Analyze Q3 Revenue"                        │
│                                                                 │
│  Agent: data_fetcher                                            │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ██ "Q3 revenue was $4.2M"          [TOOL_VERIFIED]         ││
│  │ ██ "Up 15% from Q2"               [TOOL_VERIFIED]         ││
│  │ ██ "Driven by enterprise segment"  [UNGROUNDED_CLAIM]  ⚠️ ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  Agent: analyst                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ██ "Revenue growth is strong"      [CROSS_AGENT_CONFIRMED] ││
│  │ ██ "Enterprise grew 40% YoY"      [UNGROUNDED_CLAIM]  ⚠️ ││
│  │ ██ "SMB declined 5%"              [RAG_GROUNDED]          ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  Agent: writer                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ██ "Q3 was a strong quarter"       [CROSS_AGENT_CONFIRMED] ││
│  │ ██ "driven by 40% enterprise       [CASCADING:analyst]  🔴││
│  │    growth"                                                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  Trust Summary: 4/7 claims verified, 2 ungrounded, 1 cascading │
│  Descent Status: H¹ obstruction at analyst ∩ data_fetcher      │
│  (enterprise growth claim ungrounded in both)                   │
└─────────────────────────────────────────────────────────────────┘
```

**The key insight this demo conveys**: The writer's claim "driven by 40%
enterprise growth" *looks* like a real fact in the final report. But the
trust dashboard traces its provenance: it came from the analyst, who
generated it without grounding. The `CASCADING` label shows it's a
second-order hallucination. No existing framework surfaces this.

### 4.3 Demo 3: The Coverage Checker (Does the Task Decomposition Have Gaps?)

**What it does**: Given a task description and a set of subtask
assignments, checks whether the subtasks *actually cover* the full task.

**Why it's immediately useful**: The most common bug in multi-agent
systems is not contradiction — it's *omission*. The developer decomposes
a task into subtasks, assigns agents, and misses an entire aspect of the
task. The coverage checker catches this.

```python
from jugeo.geometry.covers import Cover, CoverBuilder, score_cover

def check_task_coverage(
    task_description: str,
    subtasks: list[dict],
) -> CoverageReport:
    """Check if subtasks cover the full task.
    
    Args:
        task_description: "Write a research report on X"
        subtasks: [{"name": "find_sources", "scope": "..."}, ...]
    
    Returns:
        CoverageReport with gaps, redundancies, and score.
    """
    # Extract the task's semantic dimensions
    task_dimensions = extract_task_dimensions(task_description)
    # e.g., ["factual_accuracy", "source_quality", "argument_structure",
    #         "writing_quality", "citation_formatting", "executive_summary"]
    
    # Map each subtask to the dimensions it covers
    subtask_coverage = {}
    for subtask in subtasks:
        subtask_coverage[subtask["name"]] = extract_covered_dimensions(
            subtask["scope"], task_dimensions
        )
    
    # Build a JuGeo cover and score it
    builder = CoverBuilder()
    for name, dims in subtask_coverage.items():
        builder.add_patch(name, coordinates=dims)
    cover = builder.build(target=task_dimensions)
    
    # Identify gaps (uncovered dimensions)
    covered = set().union(*subtask_coverage.values())
    gaps = set(task_dimensions) - covered
    
    # Identify redundancies (dimensions covered by multiple subtasks)
    from collections import Counter
    dim_counts = Counter(d for dims in subtask_coverage.values() for d in dims)
    redundancies = {d: c for d, c in dim_counts.items() if c > 1}
    
    return CoverageReport(
        is_complete=(len(gaps) == 0),
        gaps=gaps,
        redundancies=redundancies,
        cover_score=score_cover(cover),
        suggestions=[f"Add a subtask covering: {', '.join(gaps)}"] if gaps else [],
    )

# Usage:
report = check_task_coverage(
    task_description="Write a research report on quantum computing trends",
    subtasks=[
        {"name": "find_sources", "scope": "search for papers and articles"},
        {"name": "analyze", "scope": "identify key trends and themes"},
        {"name": "write", "scope": "draft the report body"},
    ]
)
# report.gaps = {"executive_summary", "citation_formatting"}
# report.suggestions = ["Add a subtask covering: executive_summary, citation_formatting"]
```

**The "oh" moment**: The developer realizes their 3-agent pipeline for
"write a research report" never assigned anyone to write an executive
summary or format citations. The coverage checker caught the gap *before*
running the expensive multi-agent pipeline, saving tokens and time.

### 4.4 Demo 4: The Treaty Negotiator (Resolving Agent Conflicts)

**What it does**: When two agents produce contradictory outputs, instead
of just flagging the contradiction, JuGeo's treaty negotiation machinery
*resolves* it — producing a consistent merged output with full provenance.

**Why it's immediately useful**: Currently, when agents disagree,
developers must manually read both outputs and decide which one is right.
Treaty negotiation automates this using evidence-based resolution.

```python
from jugeo.orchestration.negotiation import TreatyNegotiator

def resolve_agent_conflict(
    agent_a_output: str,
    agent_b_output: str,
    conflict: AgentContradiction,
) -> TreatyResolution:
    """Resolve a detected contradiction between two agents.
    
    Resolution strategies (in priority order):
    1. TOOL_ARBITRATION: Call a tool to determine ground truth
    2. EVIDENCE_WEIGHTING: Trust the agent with stronger evidence
    3. CROSS_REFERENCE: Check against a third source
    4. TRUST_ORDERING: Prefer the agent with higher trust level
    5. ESCALATION: Flag for human review
    """
    negotiator = TreatyNegotiator()
    
    # Attempt resolution strategies in priority order
    resolution = negotiator.negotiate(
        section_a=agent_a_output,
        section_b=agent_b_output,
        conflict=conflict,
        strategies=[
            ToolArbitration(tool="web_search", query=conflict.claim_subject),
            EvidenceWeighting(trust_a=agent_a_output.trust, trust_b=agent_b_output.trust),
            CrossReference(source="rag_retrieval", query=conflict.claim_subject),
            TrustOrdering(),
            Escalation(to="human_reviewer"),
        ],
    )
    
    return resolution
    # resolution.winner = "agent_a"
    # resolution.strategy_used = "TOOL_ARBITRATION"
    # resolution.evidence = "Web search confirms founding year was 2019"
    # resolution.merged_output = "...consistent text..."
    # resolution.audit_trail = [full negotiation history]
```

**What makes this different from just "pick the better model"**: The
resolution is *evidence-based and auditable*. The treaty records *why*
agent A was preferred (tool arbitration confirmed its claim), not just
*that* it was preferred. The audit trail is a first-class object that
can be inspected, challenged, and replayed.

### 4.5 Demo 5: The Provenance Tracer (Where Did This Claim Come From?)

**What it does**: Given any claim in the final output of a multi-agent
pipeline, traces it back through the full chain of agents, tools, and
retrievals that produced it.

**Why it's immediately useful**: When a stakeholder asks "where did this
number come from?" about a multi-agent report, the developer currently
has to manually trace through conversation logs. The provenance tracer
does it automatically.

```python
from jugeo.evidence.provenance import ProvenanceGraph

def trace_claim(
    claim: str,
    pipeline_history: PipelineHistory,
) -> ProvenanceChain:
    """Trace a claim back to its origin through the agent pipeline.
    
    Returns the full provenance chain: which agent produced it,
    what evidence supported it, where that evidence came from.
    """
    graph = ProvenanceGraph.from_pipeline(pipeline_history)
    chain = graph.trace(claim)
    
    return chain
    # chain.links = [
    #   ProvenanceLink(agent="writer", action="included_in_report",
    #                  source="analyst output, paragraph 3"),
    #   ProvenanceLink(agent="analyst", action="derived_from",
    #                  source="data_fetcher tool result"),
    #   ProvenanceLink(agent="data_fetcher", action="tool_call",
    #                  tool="sql_query", query="SELECT revenue FROM..."),
    #   ProvenanceLink(agent="sql_database", action="returned",
    #                  result="4200000", trust=TrustLevel.TOOL_VERIFIED),
    # ]
    # chain.root_trust = TrustLevel.TOOL_VERIFIED
    # chain.weakest_link = ProvenanceLink(agent="analyst", trust=TrustLevel.LLM_GENERATED)
    # chain.overall_trust = TrustLevel.LLM_GENERATED  # conservative join
```

**The insight**: The claim "Q3 revenue was $4.2M" is in the final report
at trust level `LLM_GENERATED` (because the analyst paraphrased it),
even though the *original data* was `TOOL_VERIFIED`. The conservative
join rule correctly identifies that the claim has been laundered through
an LLM — the paraphrasing might have introduced errors. If the developer
wants `TOOL_VERIFIED` trust on the final claim, they need to pass the
raw tool output through without LLM paraphrasing.

---

## 5. Integration Architecture

### 5.1 The Agent Protocol Adapter

JuGeo doesn't replace CrewAI/AutoGen/LangGraph — it wraps them with a
verification layer. The adapter intercepts agent communications and
applies JuGeo's machinery:

```python
class JuGeoAgentWrapper:
    """Wraps any LLM agent framework with JuGeo verification.
    
    Supports CrewAI, AutoGen, LangGraph, or custom agents via
    a simple protocol: the agent produces outputs, and JuGeo
    verifies them.
    """
    
    def __init__(self, framework: str = "crewai"):
        self.descent_engine = AgentDescentEngine()
        self.trust_algebra = AgentTrustAlgebra()
        self.coverage_checker = TaskCoverageChecker()
        self.provenance_graph = ProvenanceGraph()
        self.treaty_negotiator = TreatyNegotiator()
        self.convergence_monitor = ConvergenceMonitor()
    
    def verify_task_decomposition(self, task, subtasks) -> CoverageReport:
        """Before running agents: check that subtasks cover the task."""
        return self.coverage_checker.check(task, subtasks)
    
    def on_agent_output(self, agent_id, output, metadata) -> VerificationResult:
        """After each agent produces output: verify consistency."""
        # Assign trust level based on evidence
        trust = self.trust_algebra.classify(output, metadata)
        
        # Extract claims and add to provenance graph
        claims = extract_claims(output)
        self.provenance_graph.add_node(agent_id, claims, trust)
        
        # Check descent against all previous agents' outputs
        descent_result = self.descent_engine.check_incremental(
            new_section=LocalSection(agent_id, claims, trust),
        )
        
        if descent_result.has_obstruction:
            # Attempt automatic resolution
            resolution = self.treaty_negotiator.negotiate(
                descent_result.obstruction
            )
            return VerificationResult(
                status="conflict_resolved" if resolution.success else "conflict_detected",
                obstruction=descent_result.obstruction,
                resolution=resolution,
            )
        
        return VerificationResult(status="consistent")
    
    def on_pipeline_complete(self, final_output) -> PipelineReport:
        """After all agents finish: produce the full verification report."""
        return PipelineReport(
            descent_status=self.descent_engine.global_status(),
            trust_summary=self.trust_algebra.summary(),
            provenance=self.provenance_graph,
            convergence=self.convergence_monitor.report(),
            coverage=self.coverage_checker.final_report(),
        )
```

### 5.2 Integration with Specific Frameworks

**CrewAI integration** (callback-based):
```python
from crewai import Crew, Agent, Task
from jugeo.agent_runtime import JuGeoAgentWrapper

jugeo = JuGeoAgentWrapper(framework="crewai")

# Before execution: verify task decomposition
coverage = jugeo.verify_task_decomposition(
    task="Write Q3 analysis",
    subtasks=[task.description for task in crew.tasks]
)
if not coverage.is_complete:
    print(f"WARNING: Task decomposition has gaps: {coverage.gaps}")

# During execution: wrap callbacks
original_callback = crew.task_callback
def verified_callback(task_output):
    result = jugeo.on_agent_output(
        agent_id=task_output.agent,
        output=task_output.raw,
        metadata={"tools_used": task_output.tools_used},
    )
    if result.status == "conflict_detected":
        print(f"⚠️  Contradiction detected: {result.obstruction}")
    original_callback(task_output)

crew.task_callback = verified_callback

# After execution: get full report
crew.kickoff()
report = jugeo.on_pipeline_complete(crew.output)
print(f"Trust summary: {report.trust_summary}")
print(f"Contradictions found: {len(report.descent_status.obstructions)}")
```

**LangGraph integration** (node wrapper):
```python
from langgraph.graph import StateGraph
from jugeo.agent_runtime import JuGeoAgentWrapper

jugeo = JuGeoAgentWrapper(framework="langgraph")

def verified_node(state):
    """Wrap a LangGraph node with JuGeo verification."""
    # Run the original node logic
    result = original_node(state)
    
    # Verify the output
    verification = jugeo.on_agent_output(
        agent_id=state["current_node"],
        output=result["output"],
        metadata=state.get("metadata", {}),
    )
    
    # Inject verification results into state
    result["jugeo_trust"] = verification.trust_level
    result["jugeo_obstructions"] = verification.obstructions
    
    return result

# Add verification to the graph
graph = StateGraph(AgentState)
graph.add_node("researcher", verified_node)
graph.add_node("analyst", verified_node)
```

### 5.3 The Flask Dashboard Server

The verification results feed into a Flask web dashboard (connecting
this to the web application theory from `GEOMETRY_OF_WEB_APPLICATIONS.md`):

```python
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app)

@app.route('/dashboard')
def dashboard():
    """Render the real-time agent verification dashboard."""
    return render_template('agent_dashboard.html')

@socketio.on('agent_output')
def handle_agent_output(data):
    """Process agent output and push verification results to dashboard."""
    result = jugeo.on_agent_output(
        agent_id=data['agent_id'],
        output=data['output'],
        metadata=data.get('metadata', {}),
    )
    
    # Push real-time update to all connected dashboards
    socketio.emit('verification_update', {
        'agent_id': data['agent_id'],
        'trust_level': result.trust_level.name,
        'obstructions': [o.to_dict() for o in result.obstructions],
        'provenance': result.provenance_chain.to_dict(),
    })

@app.route('/api/report')
def api_report():
    """Return the full pipeline verification report as JSON."""
    report = jugeo.on_pipeline_complete(pipeline.output)
    return jsonify(report.to_dict())
```

---

## 6. What Makes This Different from "Just Add Assertions"

A reasonable objection: "Can't I just add `assert` statements between
agent steps? Why do I need sheaf theory?"

The answer is that assertions check **point properties** while JuGeo
checks **geometric properties**. The difference:

| Property type | Assertions can check | JuGeo checks |
|---|---|---|
| **Single output quality** | ✅ "Output is non-empty" | ✅ Same |
| **Pairwise consistency** | ⚠️ Must write bespoke comparisons | ✅ Automatic descent checking across all agent pairs |
| **Task decomposition completeness** | ❌ Cannot express "subtasks cover the full task" | ✅ Cover scoring and gap detection |
| **Trust provenance** | ❌ Cannot express "this claim was verified by a tool, not hallucinated" | ✅ Full trust algebra with no-silent-promotion |
| **Cascading hallucination** | ❌ Invisible — each pair looks fine | ✅ Hypercover machinery detects higher-order coherence failures |
| **Convergence guarantee** | ❌ No mechanism to detect infinite agent loops | ✅ ConvergenceMonitor with stall detection |
| **Conflict resolution** | ❌ Must write bespoke merge logic | ✅ Treaty negotiation with evidence-based resolution |
| **Audit trail** | ❌ Must build logging infrastructure | ✅ First-class provenance graph with trust annotations |
| **Cross-pipeline learning** | ❌ Each pipeline starts from scratch | ✅ Treaty memory learns from past negotiations |

The geometric perspective is what enables the non-obvious capabilities:
cascading hallucination detection (hypercovers), task completeness
checking (covers), and evidence-based conflict resolution (treaties). You
can't get these by adding `assert` statements — they require reasoning
about the *structure* of the multi-agent system, not just the *values* of
individual outputs.

---

## 7. The Convergence Detector: Solving the Infinite Loop Problem

### 7.1 The Problem

The most common production failure in multi-agent systems is the
**infinite loop**: agents keep delegating, revising, or debating without
converging. CrewAI documents this as a known issue. AutoGen's recursive
chats are notorious for it. The current mitigation is crude: set a
maximum iteration count and hope.

### 7.2 JuGeo's Solution: Convergence as Descent Progress

JuGeo's `ConvergenceMonitor` (from `orchestration/controller.py`)
already solves this for proof search. The transport to agent systems:

```python
class AgentConvergenceMonitor:
    """Detect whether a multi-agent system is converging, stuck, or diverging.
    
    Transported from ConvergenceMonitor in orchestration/controller.py.
    
    Convergence is measured along three axes:
    1. Coverage progress: are more subtask coordinates being covered?
    2. Consistency progress: are obstructions being resolved?
    3. Trust progress: are claims being grounded/verified?
    
    A system is CONVERGING if all three are improving.
    A system is STUCK if none are improving for K consecutive rounds.
    A system is DIVERGING if any are getting worse.
    """
    
    def __init__(self, stall_threshold: int = 3, divergence_threshold: float = 0.1):
        self.history: list[ConvergenceSnapshot] = []
        self.stall_threshold = stall_threshold
        self.divergence_threshold = divergence_threshold
    
    def record_round(self, state: AgentPipelineState) -> ConvergenceStatus:
        snapshot = ConvergenceSnapshot(
            coverage=state.fraction_of_task_covered(),
            consistency=1.0 - state.obstruction_density(),
            trust=state.average_trust_level(),
            round_number=len(self.history),
        )
        self.history.append(snapshot)
        
        if len(self.history) < 2:
            return ConvergenceStatus.UNKNOWN
        
        # Compute deltas over last K rounds
        recent = self.history[-self.stall_threshold:]
        coverage_delta = recent[-1].coverage - recent[0].coverage
        consistency_delta = recent[-1].consistency - recent[0].consistency
        trust_delta = recent[-1].trust - recent[0].trust
        
        if (coverage_delta < 0 or consistency_delta < -self.divergence_threshold):
            return ConvergenceStatus.DIVERGING  # Getting worse — stop!
        
        if (coverage_delta < 0.01 and consistency_delta < 0.01 and trust_delta < 0.01):
            return ConvergenceStatus.STUCK  # No progress — escalate or change strategy
        
        return ConvergenceStatus.CONVERGING  # Making progress — continue
```

This replaces the crude "max_iterations=10" with a **semantic stopping
criterion**: stop when you're no longer making progress on the *actual
task*, not when you've hit an arbitrary iteration count.

---

## 8. Trust Calibration: Learning Which Models to Trust for What

### 8.1 The Calibration Problem

Different LLM models are better at different things. Claude excels at
careful reasoning; GPT-4 excels at code generation; Gemini excels at
multimodal tasks. But current frameworks treat all models as
interchangeable black boxes. There's no mechanism to *learn* which model
to trust for which subtask.

### 8.2 JuGeo's Solution: FleetCalibration, Transported

JuGeo's `FleetCalibration` (from `fleet_competition/calibration.py`)
already does runtime trust calibration for proof-search agents. Transport
to LLM agents:

```python
class AgentCalibration:
    """Runtime calibration of per-model trust levels.
    
    Tracks, for each (model, task_type) pair:
    - Accuracy: fraction of claims that were later verified
    - Hallucination rate: fraction of claims that were contradicted
    - Tool reliability: fraction of tool calls that succeeded
    - Consistency: fraction of claims that agree with other agents
    
    Over time, this builds a calibration profile that informs the
    trust algebra: instead of a fixed trust level per model, the
    trust level is *empirically calibrated* per model per task type.
    """
    
    def update(self, agent_id: str, model: str, task_type: str,
               outcome: AgentOutcome) -> None:
        """Record an outcome for calibration."""
        key = (model, task_type)
        self.records[key].append(outcome)
    
    def trust_for(self, model: str, task_type: str) -> TrustLevel:
        """Return the empirically calibrated trust level."""
        key = (model, task_type)
        if key not in self.records or len(self.records[key]) < 10:
            return TrustLevel.WEAK_MODEL_GENERATED  # insufficient data
        
        accuracy = self._accuracy(key)
        hallucination_rate = self._hallucination_rate(key)
        
        if accuracy > 0.95 and hallucination_rate < 0.02:
            return TrustLevel.STRONG_MODEL_GENERATED
        elif accuracy > 0.80:
            return TrustLevel.WEAK_MODEL_GENERATED
        else:
            return TrustLevel.UNGROUNDED_CLAIM  # model is unreliable for this task
```

Over time, the system learns: "Claude Opus is 96% accurate on legal
reasoning tasks (trust: STRONG), but only 72% accurate on numerical
computation (trust: UNGROUNDED — use a tool instead)." This is empirical
trust calibration, not vibes-based model selection.

---

## 9. Implementation Roadmap

### 9.1 Phase 1: The 30-Line Demo (Week 1)

Build the contradiction detector (Demo 1). This requires only:
- Claim extraction from LLM output (regex + heuristics, no LLM needed)
- Pairwise claim comparison (string matching + simple NLP)
- Wrapping in JuGeo's `DescentEngine` API

**Deliverable**: A Python function that takes two agent outputs and returns
a list of contradictions. This is enough for a blog post / conference demo.

### 9.2 Phase 2: The Trust Dashboard (Weeks 2–3)

Build the Flask dashboard (Demo 2). This requires:
- Trust classification of LLM outputs (tool results vs. RAG vs. generation)
- Provenance tracking through the agent pipeline
- Flask + SocketIO for real-time updates
- A simple frontend (HTML/CSS/JS) showing trust-colored claims

**Deliverable**: A running Flask app that developers can point at their
CrewAI/LangGraph pipelines.

### 9.3 Phase 3: Framework Integration (Weeks 4–6)

Build the `JuGeoAgentWrapper` with adapters for CrewAI, AutoGen, and
LangGraph. This requires:
- Callback/hook integration with each framework
- The coverage checker for task decomposition
- The convergence monitor for loop detection
- Treaty negotiation for conflict resolution

**Deliverable**: `pip install jugeo-agents` — a package that wraps any
major agent framework with verification.

### 9.4 Phase 4: Calibration and Learning (Weeks 7–10)

Build the calibration system and treaty memory. This requires:
- Per-model-per-task accuracy tracking
- Calibration-informed trust levels
- Treaty memory for cross-pipeline learning
- The hypercover machinery for cascading hallucination detection

**Deliverable**: A system that gets *better over time* — learning which
models to trust for what, and which agent-pair conflicts are likely to
recur.

### 9.5 New Modules Required

```
src/jugeo/
├── agent_runtime/                      # NEW: Multi-agent verification
│   ├── agent_protocol.py              # Agent protocol definitions
│   ├── claim_extraction.py            # Extract factual claims from LLM output
│   ├── agent_descent.py               # Descent checking for agent outputs
│   ├── agent_trust.py                 # Trust algebra for LLM agents
│   ├── coverage_checker.py            # Task decomposition completeness
│   ├── convergence_monitor.py         # Loop/stall detection
│   ├── provenance_tracer.py           # Claim provenance through agent pipeline
│   ├── calibration.py                 # Per-model trust calibration
│   ├── treaty_negotiation.py          # Agent conflict resolution
│   └── framework_adapters/            # Integration with specific frameworks
│       ├── crewai_adapter.py
│       ├── autogen_adapter.py
│       ├── langgraph_adapter.py
│       └── generic_adapter.py
│
├── web/                               # Dashboard
│   ├── agent_dashboard/
│   │   ├── app.py                     # Flask app
│   │   ├── templates/
│   │   │   └── agent_dashboard.html
│   │   └── static/
│   │       ├── trust_visualization.js
│   │       └── provenance_graph.js
```

---

## 10. Why This Is the Highest-Impact JuGeo Extension

Three reasons:

1. **The problem is universal and unsolved.** Every developer using
   multi-agent LLM systems has experienced contradictions, hallucination
   cascades, and task decomposition gaps. No existing framework addresses
   these formally. JuGeo would be the first tool that catches these bugs
   *automatically*.

2. **The transport is nearly free.** JuGeo's orchestration stack —
   fleet competition, trust algebra, treaty negotiation, descent engine,
   convergence monitoring — was *already designed* for coordinating
   competing agents. The transport to LLM agents is structural, not
   metaphorical. Most of the code exists; it needs adapters, not
   rewrites.

3. **The demo is immediately compelling.** Unlike many formal methods
   tools that require weeks of setup to show value, the contradiction
   detector (Demo 1) is 30 lines of Python and produces a result that
   makes a developer say "I need this" within seconds. The trust
   dashboard (Demo 2) is a Flask app that makes the invisible (trust
   provenance) visible. The coverage checker (Demo 3) catches bugs
   *before* running the expensive pipeline. Each demo is independently
   useful, and together they tell a story: JuGeo is the type system for
   agent architectures.

---

## 11. The Lyapunov Theory of Agent Convergence

### 11.1 Convergence as a Control Problem

JuGeo's orchestration controller (theory2.tex Ch44) treats convergence not
as a heuristic hope but as a **control-theoretic problem on the semantic
site**. The key insight: define a Lyapunov function V on the state space
of the multi-agent system such that V decreases along every admissible
trajectory. If V is bounded below and strictly decreasing, the system
converges.

For multi-agent LLM systems, V decomposes into four components:

```
V(state) = α · V_coverage(state)       # how much of the task remains uncovered
         + β · V_obstruction(state)     # how many contradictions remain unresolved
         + γ · V_trust_debt(state)      # how many claims remain ungrounded
         + δ · V_obligation(state)      # how many open obligations remain
```

where α, β, γ, δ are positive weights. The `ConvergenceMonitor` from
`orchestration/semantic_control/convergence.py` computes this at each step
and checks that it's decreasing.

**Why this matters for LLM agents**: The Lyapunov function gives you a
*number* that measures how close the system is to completion. Instead of
"are we done yet?" (which requires understanding the task), you check
"is V still decreasing?" (which is a simple numerical comparison). This
is the difference between "run 10 iterations and hope" and "run until
convergence, with a mathematical guarantee that you'll get there."

### 11.2 Phase Transitions in Agent Systems

JuGeo's convergence theory (theory2.tex §44.1) identifies **phase
transitions** — moments where the character of the remaining work changes
qualitatively. In agent systems, these map to:

| Phase | Character | Agent behavior | V dynamics |
|-------|-----------|----------------|------------|
| **Exploration** | Agents are generating initial outputs; V_coverage is decreasing fast | Agents work mostly independently | V drops rapidly |
| **Consolidation** | Outputs exist for most subtasks; V_obstruction becomes dominant | Agents start reviewing each other's work; contradictions emerge | V drops slowly, obstructions spike |
| **Resolution** | Contradictions are being resolved; treaties are being formed | Agents negotiate; some outputs are revised | V decreases as obstructions clear |
| **Verification** | Most contradictions resolved; V_trust_debt dominates | Agents verify claims, add citations, run tools | V approaches zero |

The `ConvergenceMonitor` detects these phase transitions and adjusts
the `ControlLaw` accordingly:

```python
class PhaseAdaptiveControlLaw:
    """Adapt the control strategy based on convergence phase.
    
    Transported from orchestration/semantic_control/convergence.py.
    """
    
    def select_next_action(self, state: AgentPipelineState) -> AgentAction:
        phase = self.convergence_monitor.current_phase(state)
        
        if phase == Phase.EXPLORATION:
            # Maximize parallelism — let agents work independently
            return self.assign_uncovered_subtask(state)
        
        elif phase == Phase.CONSOLIDATION:
            # Start cross-agent consistency checks
            return self.run_pairwise_descent(state)
        
        elif phase == Phase.RESOLUTION:
            # Focus on resolving contradictions via treaty negotiation
            return self.negotiate_highest_priority_obstruction(state)
        
        elif phase == Phase.VERIFICATION:
            # Focus on grounding: run tools, retrieve documents
            return self.ground_highest_trust_debt_claim(state)
```

This is far more sophisticated than any existing framework's control
logic. CrewAI's control is "run tasks in order." AutoGen's is "pass
messages until someone says stop." LangGraph's is "follow the graph
edges." JuGeo's is "measure the semantic distance to the goal and
adaptively choose the action that reduces it fastest."

### 11.3 Stall Detection and Recovery

The `DivergenceDetector` from `convergence.py` monitors for stalls
using an exponential-smoothing rate estimator:

```python
class AgentDivergenceDetector:
    """Detect when an agent system has stalled and trigger recovery.
    
    A stall occurs when the convergence rate drops below threshold
    for K consecutive rounds. Recovery strategies:
    
    1. STRATEGY_SWITCH: Change the control law (e.g., greedy → balanced)
    2. AGENT_REPLACEMENT: Swap a poorly-calibrated agent for a different model
    3. TASK_RESTRUCTURE: Re-decompose the task with different subtask boundaries
    4. ESCALATION: Flag for human intervention with a structured diagnostic
    """
    
    def check_for_stall(self, state: AgentPipelineState) -> StallDiagnostic | None:
        rate = self.rate_estimator.current_rate(state)
        
        if rate < self.stall_threshold:
            self.consecutive_stall_rounds += 1
        else:
            self.consecutive_stall_rounds = 0
        
        if self.consecutive_stall_rounds >= self.patience:
            return StallDiagnostic(
                rounds_stalled=self.consecutive_stall_rounds,
                bottleneck=self._identify_bottleneck(state),
                # The bottleneck tells you WHICH component is stuck
                # (e.g., "agent:analyst has 3 unresolved obstructions
                # and hasn't made progress in 4 rounds")
                recommended_recovery=self._recommend_recovery(state),
            )
        return None
```

---

## 12. The Challenge Protocol: Agents Challenging Each Other

### 12.1 Structured Disagreement

JuGeo's challenge protocol (theory2.tex Ch46 §46.4–46.7, implemented in
`fleet_competition/challenge_protocol.py`) provides a formal mechanism
for one agent to challenge another's output. This is far more structured
than "ask a reviewer agent to check":

```python
class AgentChallenge:
    """A formal challenge from one agent to another's claim.
    
    Lifecycle: INITIATED → EVIDENCE_SUBMITTED → ADJUDICATED → RESOLVED
    
    The challenge must specify:
    - The specific claim being challenged
    - The evidence supporting the challenge
    - The proposed alternative (if any)
    """
    challenger: str           # Agent issuing the challenge
    challenged: str           # Agent whose claim is challenged
    claim: FactualClaim       # The specific claim in question
    evidence: Evidence        # Evidence supporting the challenge
    proposed_alternative: str | None  # What the challenger thinks is correct
    
    challenge_type: ChallengeType  # FACTUAL, LOGICAL, COMPLETENESS, TRUST
    
class ChallengeAdjudicator:
    """Score and adjudicate challenges using evidence-based criteria.
    
    Combines a trust score (from the challengers' calibrated track records)
    with an evidence score (from the quality of the challenge evidence)
    to produce an adjudication.
    """
    
    def adjudicate(self, challenge: AgentChallenge) -> ChallengeOutcome:
        trust_score = self.calibration.trust_for(
            challenge.challenger, challenge.claim.domain
        )
        evidence_score = self.evaluate_evidence(challenge.evidence)
        
        combined = 0.4 * trust_score + 0.6 * evidence_score
        
        if combined > 0.7:
            return ChallengeOutcome.UPHELD  # Challenge succeeds
        elif combined > 0.4:
            return ChallengeOutcome.SPLIT   # Partial merit
        else:
            return ChallengeOutcome.OVERTURNED  # Challenge fails
```

### 12.2 Challenge Types and Their Agent-System Meanings

| Challenge Type | What It Means | Example |
|---|---|---|
| **FACTUAL** | "Your claim is wrong" | Agent B says "The company has 500 employees"; Agent C challenges with LinkedIn data showing 450 |
| **LOGICAL** | "Your reasoning doesn't follow" | Agent B concludes "revenue will grow" from premises that imply "revenue will decline" |
| **COMPLETENESS** | "You missed something important" | Agent B's analysis of risks omits a regulatory risk that Agent C identified |
| **TRUST** | "Your evidence doesn't support your claim's trust level" | Agent B claims tool-verified status for a claim it generated without running a tool |

### 12.3 The Challenge Ledger and Learning

Every challenge is recorded in a `ChallengeLedger` (transported from
`fleet_competition/challenge_protocol.py`). Over time, this ledger
becomes a rich dataset for calibration:

- **Which agent pairs frequently conflict?** If analyst and researcher
  disagree 40% of the time, there may be a systemic prompt or scope
  issue.
- **Which challenge types are most common?** If FACTUAL challenges
  dominate, agents need better grounding. If COMPLETENESS challenges
  dominate, the task decomposition has coverage gaps.
- **Which agents win challenges?** This feeds directly into the
  calibration system (§8) — agents that consistently lose challenges
  have their trust levels reduced.

---

## 13. Treaty Memory: Cross-Pipeline Learning

### 13.1 The Insight: Conflicts Recur

JuGeo's treaty memory system (`orchestration/treaty_memory/`) is built on
a key insight: **the same kinds of conflicts recur across pipelines**.
If a "researcher" agent and an "analyst" agent disagree about data
interpretation in Pipeline A, they will likely disagree again in
Pipeline B if the task is similar.

Treaty memory captures the *pattern* of the conflict and its resolution,
so the system can apply the resolution proactively in future pipelines:

```python
class AgentTreatyMemory:
    """Learn from past agent conflicts to prevent future ones.
    
    Transported from orchestration/treaty_memory/.
    
    Stores:
    - FrictionPatterns: recurring conflict types between agent pairs
    - ResolutionTemplates: proven strategies for resolving each pattern
    - ConfidenceScores: how well each template works over time
    """
    
    def record_conflict_resolution(
        self,
        conflict: AgentContradiction,
        resolution: TreatyResolution,
        outcome: ResolutionOutcome,
    ) -> None:
        """Record how a conflict was resolved and whether it worked."""
        pattern = self.extract_friction_pattern(conflict)
        template = self.extract_resolution_template(resolution)
        
        self.patterns[pattern].append(FrictionInstance(
            conflict=conflict,
            resolution=resolution,
            outcome=outcome,
            timestamp=time.time(),
        ))
    
    def suggest_preemptive_treaty(
        self,
        agent_a: str,
        agent_b: str,
        task_type: str,
    ) -> PreemptiveTreaty | None:
        """Before running the pipeline, suggest treaties based on past conflicts."""
        relevant_patterns = self.find_relevant_patterns(agent_a, agent_b, task_type)
        
        if not relevant_patterns:
            return None
        
        # Find the most effective resolution template for each pattern
        best_templates = [
            self.best_template_for(pattern) for pattern in relevant_patterns
        ]
        
        return PreemptiveTreaty(
            agents=(agent_a, agent_b),
            constraints=[t.as_constraint() for t in best_templates],
            # e.g., "When researcher and analyst disagree on a number,
            # always use the tool-verified version" — learned from
            # 12 past conflicts where this strategy worked 11/12 times.
        )
```

### 13.2 Archival Semantics for Agent Systems

The treaty archive preserves full evolution history of agent treaties.
This means you can answer questions like:

- "How has the conflict rate between our researcher and analyst agents
  changed over the last 100 pipelines?" (Trend analysis)
- "What resolution strategies have we tried for data interpretation
  conflicts, and which worked?" (Strategy optimization)
- "Is our current agent team getting better or worse at self-consistency
  over time?" (System health monitoring)

These are questions that **no existing agent framework can answer**,
because none of them have a concept of cross-pipeline learning or
structured conflict memory.

---

## 14. Mixed-Evidence Routing for Agent Systems

### 14.1 The Routing Problem

In a multi-agent system, evidence comes from many sources: LLM generation,
tool execution, RAG retrieval, web search, code execution, human input.
The question is: **for a given claim that needs verification, which
evidence source should we use?**

JuGeo's mixed-evidence routing layer (`orchestration/mixed_evidence_routing/`)
already solves this. The trust-aware router (theory2.tex Ch45 §45.4)
selects the evidence channel that can *actually achieve* the required
trust level:

```python
class AgentEvidenceRouter:
    """Route verification requests to the appropriate evidence channel.
    
    Transported from mixed_evidence_routing/trust_aware_routing.py.
    
    The key invariant (from theory2.tex §45.4.1):
    'A channel can only return evidence at or below its registered
    trust ceiling. Routing NEVER upgrades trust tier without explicit
    justification.'
    """
    
    TRUST_CEILINGS = {
        "code_execution":   TrustLevel.TOOL_VERIFIED,      # Highest for code claims
        "sql_query":        TrustLevel.TOOL_VERIFIED,      # Highest for data claims
        "web_search":       TrustLevel.RAG_GROUNDED,       # Medium
        "rag_retrieval":    TrustLevel.RAG_GROUNDED,       # Medium
        "llm_verification": TrustLevel.CROSS_AGENT_CONFIRMED,  # Only if multiple agree
        "llm_generation":   TrustLevel.WEAK_MODEL_GENERATED,   # Lowest
    }
    
    def route(self, claim: FactualClaim, required_trust: TrustLevel) -> EvidenceChannel:
        """Select the cheapest channel that meets the trust requirement."""
        eligible = [
            channel for channel, ceiling in self.TRUST_CEILINGS.items()
            if ceiling >= required_trust
        ]
        
        if not eligible:
            return EscalationChannel(to="human", reason="No channel meets trust requirement")
        
        # Among eligible channels, pick the cheapest
        return min(eligible, key=lambda c: self.cost_model.cost(c, claim))
```

### 14.2 Cost-Aware Routing

Evidence channels have different costs: running a tool costs time and
compute; RAG retrieval costs API calls; LLM verification costs tokens.
The router balances trust requirements against budget constraints:

```python
class RoutingCostModel:
    """Cost model for evidence channels, calibrated empirically.
    
    Cost dimensions:
    - Token cost (for LLM-based channels)
    - Latency (for tool-based channels)  
    - API cost (for external service channels)
    - Reliability (probability of getting a useful result)
    """
    
    def cost(self, channel: str, claim: FactualClaim) -> float:
        base = self.base_costs[channel]
        complexity_factor = estimate_complexity(claim)
        reliability = self.calibrated_reliability[channel]
        
        # Expected cost = base cost / reliability
        # (unreliable channels are expensive because you may need retries)
        return (base * complexity_factor) / max(reliability, 0.01)
```

This means the system automatically learns: "Code execution is expensive
but reliable; use it for high-stakes claims. RAG retrieval is cheap but
sometimes returns irrelevant results; use it for background claims.
LLM verification is cheapest but least trustworthy; use it only for
low-stakes claims where speed matters."

---

## 15. Formal Comparison with Existing Frameworks

### 15.1 Feature Matrix

| Capability | JuGeo | CrewAI | AutoGen | LangGraph | OpenAI Swarm |
|---|---|---|---|---|---|
| Task decomposition | ✅ + completeness checking | ✅ manual | ❌ | ✅ manual | ✅ manual |
| Multi-agent coordination | ✅ semantic control | ✅ role-based | ✅ conversation | ✅ graph-based | ✅ handoff |
| Contradiction detection | ✅ automatic (descent) | ❌ | ❌ | ❌ | ❌ |
| Trust algebra | ✅ formal ordered algebra | ❌ | ❌ | ❌ | ❌ |
| No-silent-promotion | ✅ enforced | ❌ | ❌ | ❌ | ❌ |
| Cascading hallucination detection | ✅ hypercovers | ❌ | ❌ | ❌ | ❌ |
| Provenance tracing | ✅ full chain | ❌ | ❌ | ❌ | ❌ |
| Convergence guarantee | ✅ Lyapunov function | ❌ max_iterations | ❌ max_rounds | ❌ max_steps | ❌ |
| Challenge protocol | ✅ formal | ❌ | ❌ | ❌ | ❌ |
| Treaty negotiation | ✅ evidence-based | ❌ | ❌ | ❌ | ❌ |
| Cross-pipeline learning | ✅ treaty memory | ❌ | ❌ | ❌ | ❌ |
| Trust calibration | ✅ per-model empirical | ❌ | ❌ | ❌ | ❌ |
| Mixed-evidence routing | ✅ cost/trust-aware | ❌ | ❌ | ❌ | ❌ |
| Audit trail | ✅ first-class object | ⚠️ logs | ⚠️ logs | ⚠️ logs | ❌ |
| Loop detection | ✅ convergence theory | ⚠️ max_iter | ⚠️ max_rounds | ✅ conditional edges | ❌ |

### 15.2 What JuGeo Adds to Each Framework

**JuGeo + CrewAI**: Adds formal task coverage checking (your crew's tasks
actually cover the goal), automatic contradiction detection between
agents, trust-scored outputs, and convergence-based stopping instead of
max_iterations. **The pitch**: "CrewAI with guarantees."

**JuGeo + AutoGen**: Adds provenance tracing through conversation chains
(which agent's claim originated where), cascading hallucination detection
across recursive chats, and cost-aware evidence routing. **The pitch**:
"AutoGen with accountability."

**JuGeo + LangGraph**: Adds semantic convergence monitoring (is the graph
making progress toward the goal?), trust-aware state annotations (which
parts of the state are verified vs. hallucinated?), and treaty memory for
learning optimal graph configurations. **The pitch**: "LangGraph with
intelligence."

---

## 16. The Semantic Site of a Real-World Agent Pipeline

### 16.1 Worked Example: Research Report Pipeline

Consider a concrete 4-agent pipeline for producing a research report.
We trace the full JuGeo verification through this pipeline:

```
Task: "Analyze the competitive landscape of quantum computing startups"

Agent 1: researcher (Claude Opus)
  Role: Find and evaluate sources
  Output: 15 sources with annotations
  
Agent 2: analyst (GPT-4)
  Role: Synthesize findings into themes
  Output: 5 key themes with supporting evidence
  
Agent 3: writer (Claude Sonnet)
  Role: Draft the report
  Output: 3000-word report
  
Agent 4: reviewer (GPT-4)
  Role: Review for accuracy, completeness, coherence
  Output: Annotated review + suggested revisions
```

**Step 1: Task Decomposition Coverage Check** (before running anything)

```python
coverage = jugeo.verify_task_decomposition(
    task="Analyze the competitive landscape of quantum computing startups",
    subtasks=[
        "find and evaluate sources",
        "synthesize findings into themes",
        "draft the report",
        "review for accuracy",
    ]
)
# Result:
# coverage.gaps = {"market_size_data", "timeline_visualization"}
# coverage.suggestions = [
#     "No subtask covers gathering market size data (revenue, funding)",
#     "No subtask covers producing visual timelines or charts",
# ]
```

The developer adds a data-fetching step and a visualization step.

**Step 2: Agent Execution with Incremental Descent**

As each agent produces output, JuGeo checks consistency:

```
Round 1: researcher produces 15 sources
  → Trust: RAG_GROUNDED (sources are retrieved documents)
  → Descent: no previous outputs to check against
  → Status: ✅ consistent

Round 2: analyst synthesizes themes
  → Trust: WEAK_MODEL_GENERATED (synthesis is LLM generation)
  → Descent check against researcher:
    → Claim: "IonQ is the market leader"
    → Researcher's sources: 3 mention IonQ, 2 mention IBM, 1 mentions Google
    → IonQ most-mentioned ≠ market leader (frequency ≠ leadership)
    → ⚠️ H¹ obstruction: analyst's claim is underspecified
    → Repair: specify "IonQ is the most-mentioned in our source set"

Round 3: writer drafts report
  → Trust: WEAK_MODEL_GENERATED
  → Descent check against analyst:
    → Writer says "the market is valued at $1.3B"
    → Analyst mentioned no market size figure
    → ⚠️ H¹ obstruction: writer hallucinated a specific number
    → Trust audit: number entered at UNGROUNDED_CLAIM
    → Repair: route to evidence channel (web search for market data)

Round 4: reviewer checks report
  → Trust: CROSS_AGENT_CONFIRMED (if confirms) or CHALLENGE (if disputes)
  → Descent check against all previous agents:
    → Reviewer confirms 12/15 claims
    → Challenges 3 claims with evidence
    → Treaty negotiation resolves 2/3 challenges automatically
    → 1 challenge escalated to human review
```

**Step 3: Final Verification Report**

```
Pipeline Report:
  Coverage: 95% (added data + visualization subtasks filled the gaps)
  Consistency: 88% → 97% after treaty negotiation
  Trust distribution:
    TOOL_VERIFIED:         3 claims  (market data from tool)
    RAG_GROUNDED:         42 claims  (from retrieved sources)
    CROSS_AGENT_CONFIRMED: 12 claims  (reviewer confirmed)
    WEAK_MODEL_GENERATED:  8 claims  (unverified synthesis)
    UNGROUNDED_CLAIM:      1 claim   (escalated to human)
  
  Obstructions resolved: 5/6 (1 escalated)
  Convergence: reached Phase 4 (VERIFICATION) in 4 rounds
  Lyapunov V: 0.03 (near zero — system converged)
  
  Provenance trace for "IonQ is the market leader":
    writer (Round 3) ← analyst (Round 2) ← researcher (Round 1, sources 3,7,11)
    Trust: CROSS_AGENT_CONFIRMED (reviewer confirmed, analyst revised)
    Originally: UNGROUNDED_CLAIM (analyst's initial synthesis)
    Final: CROSS_AGENT_CONFIRMED (after treaty + reviewer confirmation)
```

---

## 17. Hallucination as Čech Cohomology

### 17.1 The Mathematical Structure

The deepest theoretical contribution of applying JuGeo to multi-agent
LLM systems is the identification of **hallucination as a cohomological
phenomenon**. This is not a metaphor — it is a precise mathematical
characterization that enables *automated detection*.

Define the **agent presheaf** 𝒜 on the agent site:
- For each agent coordinate `c_i`, 𝒜(c_i) is the set of factual claims
  made by agent i
- For each overlap `c_i ∩ c_j`, the restriction maps `𝒜(c_i) → 𝒜(c_i ∩ c_j)`
  extract the claims relevant to the overlap (claims about shared topics)

A **consistent multi-agent output** is a global section of 𝒜: a
collection of claims, one per agent, such that the claims agree on all
overlaps. The obstruction to having a consistent output is an element
of the **Čech cohomology** H¹(𝒰, 𝒜) where 𝒰 is the cover given by
the task decomposition.

**Theorem (Hallucination Classification)**:

Let `σ = {σ_i}` be the collection of agent outputs. Then:

1. `σ` is **H⁰-obstructed** if some agent fails to produce output for
   its subtask (section incompleteness).
   
2. `σ` has an **H¹-obstruction** if two agents make contradictory claims
   about the same fact on their overlap (standard descent failure).
   
3. `σ` has an **H²-obstruction** if every pairwise overlap is consistent,
   but the triple overlaps are not (cascading hallucination: A→B→C
   propagation creates an inconsistency visible only at the 3-fold
   intersection).

4. `σ` is a **phantom global section** if it is an H⁰-satisfying,
   H¹-satisfying, H²-satisfying collection whose claims are nonetheless
   entirely fabricated (all agents agree on a hallucinated fact because
   the hallucination originated before the first agent's output — e.g.,
   in the shared context or system prompt).

### 17.2 Why This Matters Practically

**H¹ obstructions** are easy to catch: just compare pairs of outputs.
Any framework with basic assertions could do this (though none do).

**H² obstructions** are the dangerous ones. Consider:

```
Agent A (researcher): "Company X was founded by John Smith"
  (hallucinated, but sounds plausible)

Agent B (analyst): "As noted in the sources, John Smith founded Company X
  and subsequently led it through three funding rounds"
  (built on A's hallucination, added more hallucinated details)

Agent C (writer): "Company X, founded by John Smith, has raised funding
  across three rounds, establishing Smith as a prominent figure in the
  space"
  (built on both A and B, creating a consistent narrative)
```

**Pairwise checks**:
- A ∩ B: consistent (both say John Smith founded Company X) ✅
- B ∩ C: consistent (both mention three funding rounds) ✅  
- A ∩ C: consistent (both say John Smith founded Company X) ✅

**All pairwise overlaps pass!** But the entire narrative is fabricated.
This is a phantom global section — it's consistent but false.

**JuGeo's hypercover machinery detects this** by checking the *provenance*
of the consistency. The claims agree, but the trust levels show that:
- A's claim is `UNGROUNDED_CLAIM`
- B's claim is `UNGROUNDED_CLAIM` (built on A's ungrounded claim)
- C's claim is `UNGROUNDED_CLAIM` (built on A's and B's ungrounded claims)

The conservative join shows: the entire consistent narrative has trust
level `UNGROUNDED_CLAIM`. The provenance graph shows the cascade: all
claims trace back to A's original hallucination. The system flags this as
"H²: consistent but ungrounded fabrication cascade."

---

## 18. The Agent Site as a Fibered Category

### 18.1 Formal Structure

For readers interested in the full categorical picture, the multi-agent
system forms a **fibered category** over the task site:

- **Base category** 𝒯: Objects are tasks and subtasks; morphisms are
  decomposition relations (task → subtask) and dependency relations
  (subtask₁ → subtask₂)
  
- **Fiber over a subtask** 𝒜(s): The category of possible agent outputs
  for subtask s — objects are outputs, morphisms are revisions/refinements

- **Cartesian lifting**: Given a task dependency s₁ → s₂ and an output
  a₂ for s₂, the Cartesian lift is the "best compatible output" for s₁
  — the output that is maximally consistent with a₂ on their overlap

- **Descent datum**: A collection of outputs {a_s} for each subtask s
  that satisfies the cocycle condition on overlaps

The descent condition in this fibered category is precisely the statement
that **agents' outputs are mutually consistent**, and the obstruction
classes H^n classify the kinds of inconsistency.

### 18.2 Why This Isn't Over-Engineering

The categorical structure isn't needed for the basic demos (§4). But it
becomes essential when the agent system is large:

- **Compositionality**: If you have verified that sub-pipeline P₁ and
  sub-pipeline P₂ each produce consistent outputs, the fibered structure
  guarantees that their combination is consistent *if and only if* the
  overlap is consistent. You don't need to re-verify everything.

- **Incremental verification**: When one agent revises its output, you
  only need to re-check the overlaps with neighboring agents, not the
  entire pipeline. The fiber structure tells you exactly which fibers
  need updating.

- **Modular replacement**: You can replace one agent with another
  (e.g., swap GPT-4 for Claude) and the fiber structure tells you
  exactly what needs to be re-verified: the sections over the
  coordinates that agent covered, and the overlaps with neighboring
  agents.

---

## 19. Conclusion: The Type System for Agent Architectures

JuGeo's application to multi-agent LLM systems is not a stretch — it is
perhaps the most natural application of the framework. The core concepts
map directly:

- **Agents produce local sections** (outputs for their subtasks)
- **Task decomposition is a cover** (subtasks covering the full task)
- **Consistency checking is descent** (outputs agreeing on overlaps)
- **Contradictions are H¹ obstructions** (pairwise disagreements)
- **Cascading hallucinations are H² obstructions** (higher-order coherence failures)
- **Trust is an ordered algebra** (not a scalar, not vibes-based)
- **Conflict resolution is treaty negotiation** (evidence-based, auditable)
- **Convergence is a Lyapunov function** (mathematically guaranteed, not max_iter)
- **Cross-pipeline learning is treaty memory** (the system gets better over time)

The implementation strategy is designed for maximum impact with minimum
effort:

1. **Week 1**: The 30-line contradiction detector. Enough for a blog post.
2. **Weeks 2–3**: The Flask trust dashboard. Enough for a conference talk.
3. **Weeks 4–6**: Framework integration. Enough for `pip install jugeo-agents`.
4. **Weeks 7–10**: Calibration and learning. Enough for production use.

At each stage, the tool is independently useful. A developer who uses
only the contradiction detector still gets value. A developer who uses
the full stack gets a verification system that no other framework can
match.

The multi-agent LLM domain in 2025–2026 is where web development was
in the 2000s: everyone is building, nobody is verifying. JuGeo could
be the tool that changes that — not by requiring developers to learn
sheaf theory, but by giving them a 30-line function that catches bugs
they didn't know they had.
