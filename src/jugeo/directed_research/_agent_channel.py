"""Agent-based evidence channel for directed research.

This module replaces the old monolithic _llm_call() with a proper multi-agent
dispatch system. The key insight: we are NOT calling raw LLMs. We are dispatching
to **coding agents** (copilot, claude, codex) that can:

    - Read files in the workspace
    - Write files directly
    - Run shell commands (tests, benchmarks, jg prove, etc.)
    - Search code
    - Edit existing code
    - Self-correct when something fails

Each agent call is typed as a local section at a coordinate on one of the four
workspace surfaces (Theory, Code, Evidence, Claims), carrying initial trust
COPILOT_SUGGESTED. The agent backend determines which CLI tool is invoked.

The dispatch priority is:
    1. copilot (GitHub Copilot CLI) — fast, tool-capable, good for code gen
    2. claude (Claude Code CLI) — deep reasoning, tool-capable, good for theory
    3. codex (OpenAI Codex CLI) — code-focused, good for implementation
    4. sdk (Anthropic SDK direct) — fallback, no tools, text only

In geometric terms (§252 of theory2.tex), each agent is an *evidence channel*:
a typed source of local sections. The channel determines the initial trust level
and the kinds of evidence it can produce. The CopilotChannel, RuntimeChannel,
and SolverChannel from jugeo.evidence.channels are the formal counterparts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

log = logging.getLogger(__name__)

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    AgentBackend,
    AgentCapability,
    AGENT_CAPABILITIES,
    LLMSection,
    HAS_CHANNELS,
)

if HAS_CHANNELS:
    from jugeo.evidence.channels import (
        CopilotChannel,
        RuntimeChannel,
        SolverChannel,
        ChannelRouter,
        EvidenceBundle,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Agent detection
# ═══════════════════════════════════════════════════════════════════════

def _detect_agents() -> dict[AgentBackend, str]:
    """Detect which agent CLIs are available on this system."""
    agents = {}
    for backend, cmd in [
        (AgentBackend.COPILOT, "copilot"),
        (AgentBackend.CLAUDE, "claude"),
        (AgentBackend.CODEX, "codex"),
    ]:
        path = shutil.which(cmd)
        if path:
            agents[backend] = path
    # SDK is always "available" (but may fail at runtime)
    agents[AgentBackend.SDK] = "anthropic-sdk"
    return agents


_AVAILABLE_AGENTS: dict[AgentBackend, str] = _detect_agents()


def available_agents() -> list[AgentBackend]:
    """Return list of available agent backends, ordered by preference."""
    order = [AgentBackend.COPILOT, AgentBackend.CLAUDE, AgentBackend.CODEX, AgentBackend.SDK]
    return [a for a in order if a in _AVAILABLE_AGENTS]


def best_agent() -> AgentBackend:
    """Return the best available agent backend."""
    agents = available_agents()
    return agents[0] if agents else AgentBackend.SDK


# ═══════════════════════════════════════════════════════════════════════
#  Output cleaning
# ═══════════════════════════════════════════════════════════════════════

def _clean_agent_output(raw: str) -> str:
    """Strip agent CLI narration, tool-failure lines, stats, and markdown fences.

    Agents (copilot, claude, codex) produce output that includes:
    - Tool invocation markers (●, ✗, │, └)
    - Usage statistics (Total usage, API time, etc.)
    - Markdown code fences (```python ... ```)

    This function strips all of that to get the raw content.
    """
    lines = raw.split("\n")
    cleaned = []
    skip = False
    for line in lines:
        s = line.strip()
        # Skip tool invocation/failure blocks
        if s.startswith(("●", "✗")):
            skip = True
            continue
        if skip and (s.startswith(("│", "└")) or s == ""):
            continue
        skip = False
        # Skip agent stats
        if any(tag in line for tag in [
            "Total usage", "API time", "Total session", "Total code",
            "Breakdown by AI", "claude-sonnet", "Est. ", "Premium request",
            "Total cost", "Input tokens", "Output tokens",
        ]):
            continue
        cleaned.append(line)
    # Strip leading/trailing empty lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    text = "\n".join(cleaned).strip()
    # Strip outermost markdown fences if present
    if text.startswith("```") and text.endswith("```"):
        inner = text.split("\n", 1)
        if len(inner) > 1:
            text = inner[1]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


# ═══════════════════════════════════════════════════════════════════════
#  Agent dispatch — the core of the evidence channel
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AgentCallConfig:
    """Configuration for an agent call."""
    prompt: str
    surface: SurfaceKind
    coordinate: str
    backend: AgentBackend = AgentBackend.COPILOT
    working_dir: Optional[str] = None
    output_file: Optional[str] = None  # If set, agent writes here
    max_retries: int = 1  # each agent call is expensive; prefer fallthrough
    timeout: Optional[int] = None
    model: str = "claude-sonnet-4.6"
    allow_file_write: bool = True
    allow_commands: bool = True
    context_files: list[str] = field(default_factory=list)


def _call_copilot(config: AgentCallConfig) -> tuple[str, list[str], list[str]]:
    """Dispatch to GitHub Copilot CLI.

    For text-only output (no output_file), copilot is invoked without
    --allow-all-tools to avoid slow codebase scanning. For file-write
    tasks, full tool access is enabled.

    Returns (text, files_touched, commands_run).
    """
    prompt_hash = hashlib.sha256(config.prompt.encode()).hexdigest()[:12]
    text = ""
    files_touched: list[str] = []
    commands_run: list[str] = []

    for attempt in range(config.max_retries):
        try:
            if config.output_file:
                out_path = config.output_file
                with open(out_path, "w") as f:
                    f.write("")
                file_prompt = (
                    f"Write the following content to {out_path} — write ONLY the "
                    f"requested content, no explanations:\n\n{config.prompt}"
                )
                cmd = [
                    "copilot", "-p", file_prompt,
                    "--model", config.model,
                    "--allow-all-tools", "--allow-all-paths",
                ]
            else:
                cmd = [
                    "copilot", "-p", config.prompt,
                    "--model", config.model,
                ]

            if config.context_files:
                cmd[2] = (
                    f"Context files to read first: {', '.join(config.context_files)}\n\n"
                    + cmd[2]
                )

            commands_run.append("copilot -p ...")

            timeout = 300
            r = subprocess.run(
                cmd,
                capture_output=True, text=True,
                cwd=config.working_dir,
                timeout=timeout,
            )

            # Check output file if one was specified
            if config.output_file:
                if os.path.exists(config.output_file) and os.path.getsize(config.output_file) > 20:
                    with open(config.output_file, errors="replace") as f:
                        text = f.read().strip()
                    if len(text) > 20:
                        files_touched.append(config.output_file)
                        break
                    text = ""

            # Capture stdout
            if r.returncode == 0 and r.stdout.strip():
                cleaned = _clean_agent_output(r.stdout)
                if len(cleaned) > 20:
                    text = cleaned
                    break
        except subprocess.TimeoutExpired as te:
            # Capture partial output from timed-out process
            partial = (te.stdout or "") if isinstance(te.stdout, str) else ""
            if partial:
                cleaned = _clean_agent_output(partial)
                if len(cleaned) > 100 and _looks_like_code(cleaned):
                    text = cleaned
                    break
        except Exception:
            pass
        if attempt < config.max_retries - 1:
            time.sleep(3)

    return text, files_touched, commands_run


def _call_claude(config: AgentCallConfig) -> tuple[str, list[str], list[str]]:
    """Dispatch to Claude Code CLI.

    Claude Code is invoked with -p (print mode) for non-interactive use.
    No timeout — large code generation can take several minutes.
    """
    text = ""
    files_touched: list[str] = []
    commands_run: list[str] = []

    for attempt in range(config.max_retries):
        try:
            cmd = [
                "claude", "-p", config.prompt,
                "--output-format", "text",
            ]
            if config.working_dir:
                cmd.extend(["--cwd", config.working_dir])
            commands_run.append("claude -p ...")

            r = subprocess.run(
                cmd,
                capture_output=True, text=True,
                cwd=config.working_dir,
                timeout=300,
            )

            if r.returncode == 0 and r.stdout.strip():
                cleaned = _clean_agent_output(r.stdout)
                if len(cleaned) > 20:
                    text = cleaned
                    break

        except subprocess.TimeoutExpired as te:
            partial = (te.stdout or "") if isinstance(te.stdout, str) else ""
            if partial:
                cleaned = _clean_agent_output(partial)
                if len(cleaned) > 100 and _looks_like_code(cleaned):
                    text = cleaned
                    break
        except Exception:
            pass
        if attempt < config.max_retries - 1:
            time.sleep(3)

    return text, files_touched, commands_run


def _call_codex(config: AgentCallConfig) -> tuple[str, list[str], list[str]]:
    """Dispatch to OpenAI Codex CLI.

    Codex is invoked with --full-auto for autonomous operation.
    """
    text = ""
    files_touched: list[str] = []
    commands_run: list[str] = []

    for attempt in range(config.max_retries):
        try:
            cmd = ["codex", "--full-auto", config.prompt]
            commands_run.append("codex --full-auto ...")

            r = subprocess.run(
                cmd,
                capture_output=True, text=True,
                cwd=config.working_dir,
                timeout=300,
            )

            if r.returncode == 0 and r.stdout.strip():
                cleaned = _clean_agent_output(r.stdout)
                if len(cleaned) > 20:
                    text = cleaned
                    break
        except subprocess.TimeoutExpired as te:
            partial = (te.stdout or "") if isinstance(te.stdout, str) else ""
            if partial:
                cleaned = _clean_agent_output(partial)
                if len(cleaned) > 100 and _looks_like_code(cleaned):
                    text = cleaned
                    break
        except Exception:
            pass
        if attempt < config.max_retries - 1:
            time.sleep(3)

    return text, files_touched, commands_run


def _call_sdk(config: AgentCallConfig) -> tuple[str, list[str], list[str]]:
    """Fallback: call Anthropic SDK directly (no tool use).

    This is the degraded path when no agent CLI is available. It uses the
    Anthropic SDK to make a direct API call. No file operations, no command
    execution — text generation only.
    """
    text = ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=16384,
            messages=[{"role": "user", "content": config.prompt}],
        )
        text = msg.content[0].text
    except Exception:
        pass
    return text, [], []


# Agent dispatcher registry
_AGENT_DISPATCHERS = {
    AgentBackend.COPILOT: _call_copilot,
    AgentBackend.CLAUDE: _call_claude,
    AgentBackend.CODEX: _call_codex,
    AgentBackend.SDK: _call_sdk,
}


def _looks_like_code(text: str) -> bool:
    """Check if text looks like actual source code rather than narration.

    Returns True if the text contains code-like tokens: function definitions,
    class declarations, variable declarations, imports, braces, etc.
    This prevents agent narration/error messages from being accepted as code.
    """
    code_indicators = [
        "function", "class ", "const ", "let ", "var ", "return ",
        "import ", "from ", "def ", "if (", "for (", "while (",
        "this.", "self.", "window.", "document.", "module.",
        "{", "=>", "/**", "# ", "//", "/*",
        "<!DOCTYPE", "<html", "<div", "<section",
        ":root", "@media", "@keyframes", ".ct-",
    ]
    # Require at least 3 different code indicators
    found = sum(1 for ind in code_indicators if ind in text)
    return found >= 3


# ═══════════════════════════════════════════════════════════════════════
#  The unified agent call — THE interface between agents and geometry
# ═══════════════════════════════════════════════════════════════════════

def agent_call(
    prompt: str,
    *,
    surface: SurfaceKind,
    coordinate: str,
    backend: Optional[AgentBackend] = None,
    working_dir: Optional[str] = None,
    output_file: Optional[str] = None,
    max_retries: int = 1,
    timeout: Optional[int] = None,
    model: str = "claude-sonnet-4.6",
    context_files: Optional[list[str]] = None,
    expect_json: bool = False,
) -> LLMSection:
    """Call a coding agent and return a typed, trust-annotated section.

    This is THE interface between the agent layer and the geometry. Every
    agent interaction in the system goes through here. The result is always
    a section at a specific coordinate with COPILOT_SUGGESTED trust.

    The function tries agents in priority order (copilot → claude → codex → sdk)
    until one succeeds. Each attempt produces an LLMSection recording what was
    generated, where it lives, at what trust, and via which agent.

    Args:
        prompt: The task for the agent to perform.
        surface: Which workspace surface this section belongs to.
        coordinate: The coordinate within the surface (e.g., "theory.domain_analysis").
        backend: Force a specific agent backend (None = auto-select).
        working_dir: Directory for the agent to work in.
        output_file: If set, agent writes output to this file.
        max_retries: Number of retries per agent backend.
        timeout: Timeout in seconds per attempt.
        model: Model to use (for agents that support model selection).
        context_files: Files the agent should read before starting.

    Returns:
        LLMSection with the agent's output, trust level, and provenance.
    """
    t0 = time.time()
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:12]

    config = AgentCallConfig(
        prompt=prompt,
        surface=surface,
        coordinate=coordinate,
        working_dir=working_dir,
        output_file=output_file,
        max_retries=max_retries,
        timeout=timeout,
        model=model,
        context_files=context_files or [],
    )

    text = ""
    files_touched: list[str] = []
    commands_run: list[str] = []
    used_backend = AgentBackend.SDK

    if backend:
        # Use the specified backend
        backends_to_try = [backend]
    else:
        # Try all available agents in priority order
        backends_to_try = available_agents()

    for be in backends_to_try:
        if be not in _AVAILABLE_AGENTS:
            continue
        config.backend = be
        dispatcher = _AGENT_DISPATCHERS.get(be)
        if not dispatcher:
            continue

        log.info("agent: trying %s for %s", be.value, coordinate)
        text, files_touched, commands_run = dispatcher(config)
        # Require substantive output — not just narration or error text.
        # For JSON-expected calls, accept anything with a JSON object;
        # for code calls, require code-like tokens.
        if text and len(text) > 10:
            if expect_json:
                acceptable = "{" in text
            else:
                acceptable = len(text) > 100 and _looks_like_code(text)
        else:
            acceptable = False

        if acceptable:
            used_backend = be
            log.info("agent: %s succeeded: %d chars, %d lines",
                      be.value, len(text), text.count("\n"))
            break
        # If this backend returned something short/non-code, try next
        reason = "empty" if not text else f"too short ({len(text)})" if len(text) <= 10 else ("no JSON" if expect_json else "not code")
        log.info("agent: %s failed: %s", be.value, reason)
        text = ""

    # If nothing worked, produce a placeholder
    if not text or (not expect_json and len(text) <= 100):
        text = f"[Agent unavailable — {prompt[:80]}...]"

    elapsed = time.time() - t0
    return LLMSection(
        surface=surface,
        coordinate=coordinate,
        content=text,
        trust=TRUST_COPILOT,
        provenance=f"agent-{used_backend.value}:{coordinate}",
        prompt_hash=prompt_hash,
        elapsed=elapsed,
        token_count=len(text.split()),
        agent_backend=used_backend,
        files_touched=files_touched,
        commands_run=commands_run,
    )


def agent_json(
    prompt: str,
    **kwargs,
) -> tuple[dict, LLMSection]:
    """Call an agent and parse JSON from the response.

    Appends "Respond with ONLY valid JSON" to the prompt, then parses
    the result. Falls back to regex extraction if direct parsing fails.
    """
    full_prompt = prompt + "\n\nRespond with ONLY valid JSON, no markdown fences."
    section = agent_call(full_prompt, expect_json=True, **kwargs)
    text = section.content.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = "\n".join(l for l in text.split("\n") if not l.startswith("```"))

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the text
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                data = {"raw": text, "parse_error": True}
        else:
            data = {"raw": text, "parse_error": True}

    return data, section


def agent_file_write(
    prompt: str,
    output_path: str,
    *,
    surface: SurfaceKind,
    coordinate: str,
    working_dir: Optional[str] = None,
    backend: Optional[AgentBackend] = None,
    context_files: Optional[list[str]] = None,
) -> LLMSection:
    """Have an agent write content directly to a file.

    This leverages the agent's file-write capability rather than
    capturing stdout. The agent is told to write to output_path
    and we read the result back.
    """
    section = agent_call(
        prompt,
        surface=surface,
        coordinate=coordinate,
        backend=backend,
        working_dir=working_dir,
        output_file=output_path,
        context_files=context_files,
    )

    # If the agent wrote to the file, read it back
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        with open(output_path, errors="replace") as f:
            content = f.read()
        if len(content) > len(section.content):
            section = LLMSection(
                surface=section.surface,
                coordinate=section.coordinate,
                content=content,
                trust=section.trust,
                provenance=section.provenance,
                prompt_hash=section.prompt_hash,
                elapsed=section.elapsed,
                token_count=len(content.split()),
                agent_backend=section.agent_backend,
                files_touched=section.files_touched + [output_path],
                commands_run=section.commands_run,
            )

    return section
