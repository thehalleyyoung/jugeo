"""Framework adapters for jugeo-agents."""

from jugeo_agents.adapters.base import BaseAdapter, GenericAdapter
from jugeo_agents.adapters.coding_agents import (
    ClaudeCodeAdapter,
    CopilotCLIAdapter,
    CodexAdapter,
    CodingAgentOrchestrator,
    CodeOutput,
)

__all__ = [
    "BaseAdapter",
    "GenericAdapter",
    "ClaudeCodeAdapter",
    "CopilotCLIAdapter",
    "CodexAdapter",
    "CodingAgentOrchestrator",
    "CodeOutput",
]
