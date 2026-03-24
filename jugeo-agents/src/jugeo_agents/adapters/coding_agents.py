"""Coding-Agent Adapters — Claude Code, Copilot CLI, Codex (o3/o4-mini).

Treats each coding agent's output as a **local section** of a presheaf
over the codebase.  The adapters normalize outputs from different agents
into ``AgentOutput`` records with appropriate trust levels, then the
``CodingAgentOrchestrator`` runs sheaf-theoretic verification across them.

Architecture
------------
::

    ┌─────────────┐  ┌──────────────┐  ┌───────────┐
    │ Claude Code  │  │ Copilot CLI  │  │   Codex   │
    └──────┬──────┘  └──────┬───────┘  └─────┬─────┘
           │                │                │
     ┌─────▼─────┐   ┌─────▼──────┐  ┌──────▼─────┐
     │ Adapter    │   │  Adapter   │  │  Adapter   │
     └─────┬─────┘   └─────┬──────┘  └──────┬─────┘
           │                │                │
           └────────────────┼────────────────┘
                            │
                   ┌────────▼─────────┐
                   │ GlobalSection    │
                   │ Assembler        │
                   │ (sheaf fusion)   │
                   └──────────────────┘

Each adapter knows how to:
1. Parse its agent's output format (code blocks, explanations, tool calls).
2. Classify trust: tool-backed code (tests pass) > generated code > hallucinated code.
3. Extract code-level claims: "function X does Y", "handles edge case Z", "complexity O(n)".

Usage
-----
::

    from jugeo_agents.adapters.coding_agents import (
        ClaudeCodeAdapter,
        CopilotCLIAdapter,
        CodexAdapter,
        CodingAgentOrchestrator,
    )

    orch = CodingAgentOrchestrator()

    # Each agent's output for the same task
    orch.add_output(ClaudeCodeAdapter.from_response(
        code="def fib(n): ...",
        explanation="Iterative fibonacci with O(n) time.",
        tools_used=["bash:pytest"],
    ))
    orch.add_output(CopilotCLIAdapter.from_response(
        code="def fib(n): ...",
        explanation="Recursive fibonacci with memoization.",
    ))

    # Fuse: detect contradictions in claims, pick trust-winners
    section = orch.verify()
    print(section.summary_text())
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Sequence

from jugeo_agents.types import AgentOutput, FactualClaim, TrustLevel
from jugeo_agents.adapters.base import BaseAdapter
from jugeo_agents.core.fusion import (
    GlobalSectionAssembler,
    VerifiedGlobalSection,
    compare_to_naive_vote,
    FusionReport,
)


__all__ = [
    "ClaudeCodeAdapter",
    "CopilotCLIAdapter",
    "CodexAdapter",
    "CodingAgentOrchestrator",
    "CodeOutput",
    "CodeClaimKind",
]


# ===================================================================
# Code Output — normalized representation of a coding agent's work
# ===================================================================

class CodeClaimKind(Enum):
    """Kinds of claims a coding agent can make."""
    IMPLEMENTATION = auto()      # "I implemented function X"
    CORRECTNESS = auto()         # "This handles edge case Y"
    COMPLEXITY = auto()          # "Time complexity is O(n)"
    ARCHITECTURE = auto()        # "Uses pattern X"
    DEPENDENCY = auto()          # "Requires package Y"
    TEST_RESULT = auto()         # "All 42 tests pass"
    SECURITY = auto()            # "Input is sanitized against XSS"
    EXPLANATION = auto()         # "This works because..."


@dataclass
class CodeOutput:
    """Normalized output from any coding agent."""
    agent_name: str              # "claude-code", "copilot-cli", "codex"
    agent_model: str             # "claude-sonnet-4", "gpt-4.1", etc.
    code: str                    # The generated/modified code
    explanation: str             # Natural language explanation
    files_modified: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    test_results: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_evidence(self) -> bool:
        return bool(self.tools_used) or bool(self.test_results)

    @property
    def tests_passed(self) -> bool:
        tr = self.test_results
        if not tr:
            return False
        if "passed" in tr:
            return bool(tr["passed"])
        if "exit_code" in tr:
            return tr["exit_code"] == 0
        return False


# ===================================================================
# Claude Code Adapter
# ===================================================================

class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Anthropic's Claude Code (claude-code CLI).

    Claude Code outputs structured responses with:
    - Code blocks (file modifications)
    - Tool calls (bash commands, file reads, grep, etc.)
    - Reasoning/explanation text
    - Test execution results

    Trust mapping:
    - Tests pass + tool calls → TOOL_VERIFIED
    - Tool calls but no tests → TOOL_EXECUTED (actually tool-backed)
    - Citations/reasoning only → CITATION_BACKED
    - Pure generation → WEAK_MODEL_GENERATED
    """

    AGENT_ID = "claude-code"
    DEFAULT_MODEL = "claude-sonnet-4"

    # Claude Code tool names → trust indicators
    _TOOL_COMMANDS = frozenset({
        "bash", "read_file", "write_file", "edit_file", "grep",
        "glob", "list_files", "search", "run_tests",
    })

    @classmethod
    def from_response(
        cls,
        code: str,
        explanation: str = "",
        tools_used: list[str] | None = None,
        test_results: dict[str, Any] | None = None,
        files_modified: list[str] | None = None,
        model: str = "",
        citations: list[str] | None = None,
    ) -> CodeOutput:
        return CodeOutput(
            agent_name=cls.AGENT_ID,
            agent_model=model or cls.DEFAULT_MODEL,
            code=code,
            explanation=explanation,
            tools_used=tools_used or [],
            test_results=test_results or {},
            files_modified=files_modified or [],
            citations=citations or [],
        )

    @classmethod
    def to_agent_output(cls, output: CodeOutput) -> AgentOutput:
        """Convert a CodeOutput into a JuGeo AgentOutput."""
        full_text = _build_full_text(output)

        return AgentOutput(
            agent_id=output.agent_name,
            output_text=full_text,
            model=output.agent_model,
            role="coding-agent",
            tools_used=output.tools_used,
            citations=output.citations,
            trust=cls._classify_trust(output),
        )

    @classmethod
    def _classify_trust(cls, output: CodeOutput) -> TrustLevel:
        if output.tests_passed and output.tools_used:
            return TrustLevel.TOOL_VERIFIED
        if output.tools_used:
            return TrustLevel.TOOL_EXECUTED
        if output.citations:
            return TrustLevel.CITATION_BACKED
        return TrustLevel.WEAK_MODEL_GENERATED

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        code_out = self.from_response(
            code=metadata.get("code", ""),
            explanation=output,
            tools_used=metadata.get("tools_used", []),
            test_results=metadata.get("test_results", {}),
            files_modified=metadata.get("files_modified", []),
            model=metadata.get("model", self.DEFAULT_MODEL),
        )
        return self.to_agent_output(code_out)

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        return ("", [])


# ===================================================================
# Copilot CLI Adapter
# ===================================================================

class CopilotCLIAdapter(BaseAdapter):
    """Adapter for GitHub Copilot CLI (copilot-cli).

    Copilot CLI outputs:
    - Code suggestions/edits
    - Bash commands
    - Explanations
    - File modifications

    Trust mapping:
    - Has bash tool calls (grep, tests) → TOOL_EXECUTED
    - Has file reads/analysis → CITATION_BACKED
    - Pure suggestion → WEAK_MODEL_GENERATED
    """

    AGENT_ID = "copilot-cli"
    DEFAULT_MODEL = "gpt-4.1"

    @classmethod
    def from_response(
        cls,
        code: str,
        explanation: str = "",
        tools_used: list[str] | None = None,
        test_results: dict[str, Any] | None = None,
        files_modified: list[str] | None = None,
        model: str = "",
    ) -> CodeOutput:
        return CodeOutput(
            agent_name=cls.AGENT_ID,
            agent_model=model or cls.DEFAULT_MODEL,
            code=code,
            explanation=explanation,
            tools_used=tools_used or [],
            test_results=test_results or {},
            files_modified=files_modified or [],
        )

    @classmethod
    def to_agent_output(cls, output: CodeOutput) -> AgentOutput:
        full_text = _build_full_text(output)
        return AgentOutput(
            agent_id=output.agent_name,
            output_text=full_text,
            model=output.agent_model,
            role="coding-agent",
            tools_used=output.tools_used,
            trust=cls._classify_trust(output),
        )

    @classmethod
    def _classify_trust(cls, output: CodeOutput) -> TrustLevel:
        if output.tests_passed:
            return TrustLevel.TOOL_VERIFIED
        if output.tools_used:
            return TrustLevel.TOOL_EXECUTED
        return TrustLevel.WEAK_MODEL_GENERATED

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        code_out = self.from_response(
            code=metadata.get("code", ""),
            explanation=output,
            tools_used=metadata.get("tools_used", []),
            test_results=metadata.get("test_results", {}),
            files_modified=metadata.get("files_modified", []),
            model=metadata.get("model", self.DEFAULT_MODEL),
        )
        return self.to_agent_output(code_out)

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        return ("", [])


# ===================================================================
# Codex Adapter (OpenAI o3/o4-mini via codex-cli)
# ===================================================================

class CodexAdapter(BaseAdapter):
    """Adapter for OpenAI Codex CLI (codex-cli, o3, o4-mini).

    Codex outputs:
    - Code patches/diffs
    - Shell command execution results
    - Reasoning chains
    - File modifications

    Trust mapping:
    - Shell commands executed + tests pass → TOOL_VERIFIED
    - Shell commands executed → TOOL_EXECUTED
    - Reasoning with references → CITATION_BACKED
    - Pure code generation → WEAK_MODEL_GENERATED
    """

    AGENT_ID = "codex"
    DEFAULT_MODEL = "o4-mini"

    @classmethod
    def from_response(
        cls,
        code: str,
        explanation: str = "",
        tools_used: list[str] | None = None,
        test_results: dict[str, Any] | None = None,
        files_modified: list[str] | None = None,
        model: str = "",
        citations: list[str] | None = None,
    ) -> CodeOutput:
        return CodeOutput(
            agent_name=cls.AGENT_ID,
            agent_model=model or cls.DEFAULT_MODEL,
            code=code,
            explanation=explanation,
            tools_used=tools_used or [],
            test_results=test_results or {},
            files_modified=files_modified or [],
            citations=citations or [],
        )

    @classmethod
    def to_agent_output(cls, output: CodeOutput) -> AgentOutput:
        full_text = _build_full_text(output)
        return AgentOutput(
            agent_id=output.agent_name,
            output_text=full_text,
            model=output.agent_model,
            role="coding-agent",
            tools_used=output.tools_used,
            citations=output.citations,
            trust=cls._classify_trust(output),
        )

    @classmethod
    def _classify_trust(cls, output: CodeOutput) -> TrustLevel:
        if output.tests_passed and output.tools_used:
            return TrustLevel.TOOL_VERIFIED
        if output.tools_used:
            return TrustLevel.TOOL_EXECUTED
        if output.citations:
            return TrustLevel.CITATION_BACKED
        return TrustLevel.WEAK_MODEL_GENERATED

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        code_out = self.from_response(
            code=metadata.get("code", ""),
            explanation=output,
            tools_used=metadata.get("tools_used", []),
            test_results=metadata.get("test_results", {}),
            files_modified=metadata.get("files_modified", []),
            model=metadata.get("model", self.DEFAULT_MODEL),
            citations=metadata.get("citations", []),
        )
        return self.to_agent_output(code_out)

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        return ("", [])


# ===================================================================
# Code Claim Extraction helpers
# ===================================================================

_COMPLEXITY_RE = re.compile(
    r"[Oo]\(([^)]+)\)|"
    r"(?:time|space)\s+complexity\s+(?:is|of)\s+[Oo]\(([^)]+)\)|"
    r"(?:linear|quadratic|logarithmic|constant|exponential)\s+(?:time|space)",
    re.I,
)

_HANDLES_RE = re.compile(
    r"handles?\s+(.{5,60}?)(?:\.|,|;|$)|"
    r"edge\s+case[s]?\s*:?\s*(.{5,60}?)(?:\.|,|;|$)|"
    r"(?:validates?|checks?|ensures?|guards?\s+against)\s+(.{5,60}?)(?:\.|,|;|$)",
    re.I,
)

_PATTERN_RE = re.compile(
    r"(?:uses?|implements?|follows?|applies?)\s+(?:the\s+)?"
    r"((?:factory|singleton|observer|strategy|decorator|adapter|proxy|"
    r"builder|command|iterator|mediator|visitor|MVC|MVVM|repository|"
    r"dependency injection|event sourcing|CQRS)\s*(?:pattern|design)?)",
    re.I,
)

_DEPENDENCY_RE = re.compile(
    r"(?:requires?|depends?\s+on|imports?|uses?)\s+"
    r"(?:the\s+)?[`'\"]?(\w[\w.-]+)[`'\"]?\s+(?:package|library|module|dependency)",
    re.I,
)


def extract_code_claims(
    explanation: str, agent_id: str,
) -> list[FactualClaim]:
    """Extract coding-specific claims from an agent's explanation text."""
    claims: list[FactualClaim] = []

    for m in _COMPLEXITY_RE.finditer(explanation):
        value = m.group(1) or m.group(2) or m.group(0)
        claims.append(FactualClaim(
            text=m.group(0),
            subject="algorithm",
            predicate="complexity",
            value=value.strip(),
            source_agent=agent_id,
        ))

    for m in _HANDLES_RE.finditer(explanation):
        value = m.group(1) or m.group(2) or m.group(3) or ""
        if value.strip():
            claims.append(FactualClaim(
                text=m.group(0),
                subject="implementation",
                predicate="handles",
                value=value.strip(),
                source_agent=agent_id,
            ))

    for m in _PATTERN_RE.finditer(explanation):
        claims.append(FactualClaim(
            text=m.group(0),
            subject="architecture",
            predicate="pattern",
            value=m.group(1).strip(),
            source_agent=agent_id,
        ))

    for m in _DEPENDENCY_RE.finditer(explanation):
        claims.append(FactualClaim(
            text=m.group(0),
            subject="dependencies",
            predicate="requires",
            value=m.group(1).strip(),
            source_agent=agent_id,
        ))

    return claims


# ===================================================================
# Coding Agent Orchestrator
# ===================================================================

class CodingAgentOrchestrator:
    """Orchestrate multiple coding agents with sheaf-theoretic verification.

    Send the same task to Claude Code, Copilot CLI, and Codex, then
    use the ``GlobalSectionAssembler`` to:

    1. Extract claims from each agent's code + explanation.
    2. Detect contradictions (different algorithms, conflicting claims).
    3. Resolve via trust algebra (tool-verified > ungrounded).
    4. Produce a verified global section — the best composite answer.

    The sheaf-theoretic model:

    - **Site**: The codebase (files, functions, modules).
    - **Local sections**: Each agent's output = claims about code behavior.
    - **Restriction maps**: Where agents modify the same file/function.
    - **Sheaf condition**: Agents must agree on shared code semantics.
    - **Obstructions**: Contradictory complexity claims, incompatible
      patterns, conflicting dependency requirements.

    Example
    -------
    >>> orch = CodingAgentOrchestrator()
    >>> orch.add_output(ClaudeCodeAdapter.from_response(
    ...     code="def sort(arr): return sorted(arr)",
    ...     explanation="Uses Python's built-in Timsort. O(n log n) time.",
    ...     tools_used=["bash:pytest"],
    ...     test_results={"passed": True, "count": 12},
    ... ))
    >>> orch.add_output(CodexAdapter.from_response(
    ...     code="def sort(arr): return merge_sort(arr)",
    ...     explanation="Implements merge sort. O(n^2) worst case.",
    ... ))
    >>> section = orch.verify()
    >>> # Claude Code wins: tool-verified + correct complexity claim
    """

    _ADAPTERS: dict[str, type] = {
        "claude-code": ClaudeCodeAdapter,
        "copilot-cli": CopilotCLIAdapter,
        "codex": CodexAdapter,
    }

    def __init__(
        self,
        *,
        trust_threshold: TrustLevel = TrustLevel.WEAK_MODEL_GENERATED,
    ) -> None:
        self._assembler = GlobalSectionAssembler(
            trust_threshold=trust_threshold,
        )
        self._code_outputs: list[CodeOutput] = []

    def add_output(self, output: CodeOutput) -> None:
        """Add a coding agent's output to the verification pipeline."""
        self._code_outputs.append(output)

        adapter_cls = self._ADAPTERS.get(output.agent_name)
        if adapter_cls is None:
            # Generic: build AgentOutput directly
            agent_output = AgentOutput(
                agent_id=output.agent_name,
                output_text=_build_full_text(output),
                model=output.agent_model,
                role="coding-agent",
                tools_used=output.tools_used,
                citations=output.citations,
            )
        else:
            agent_output = adapter_cls.to_agent_output(output)

        self._assembler.ingest(agent_output)

    def verify(self) -> VerifiedGlobalSection:
        """Run sheaf-theoretic verification across all agents' outputs.

        Returns a ``VerifiedGlobalSection`` where:
        - Verified claims = consistent, trust-certified code facts.
        - Quarantined claims = contradictions resolved by trust algebra.
        - Cohomology = H⁰ (verified), H¹ (contradictions), H² (cascades).
        """
        return self._assembler.assemble()

    def full_report(self) -> FusionReport:
        """Verify and also compare to naive majority vote."""
        section = self.verify()
        naive = compare_to_naive_vote(section, self._assembler._all_claims)
        return FusionReport(global_section=section, naive_comparison=naive)

    def reset(self) -> None:
        """Reset for a new task."""
        self._assembler.reset()
        self._code_outputs.clear()

    @property
    def agent_count(self) -> int:
        return len(self._code_outputs)


# ===================================================================
# Helpers
# ===================================================================

def _build_full_text(output: CodeOutput) -> str:
    """Build a full-text representation for claim extraction."""
    parts: list[str] = []

    if output.explanation:
        parts.append(output.explanation)

    if output.code:
        # Extract docstrings and comments as claimable text
        for line in output.code.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                parts.append(stripped.lstrip("# "))
            elif stripped.startswith('"""') or stripped.startswith("'''"):
                parts.append(stripped.strip("\"'"))

    if output.test_results:
        if output.tests_passed:
            count = output.test_results.get("count", "all")
            parts.append(f"All {count} tests passed successfully.")
        else:
            failed = output.test_results.get("failed", "some")
            parts.append(f"{failed} tests failed.")

    if output.files_modified:
        parts.append(f"Modified files: {', '.join(output.files_modified)}.")

    return " ".join(parts)
