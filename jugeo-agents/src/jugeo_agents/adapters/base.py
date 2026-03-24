"""Base adapter protocol and generic adapter for any agent framework.

Every framework adapter converts framework-specific events into
``AgentOutput`` records that the ``JuGeoAgentWrapper`` can verify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from jugeo_agents.types import AgentOutput, TrustLevel


# ---------------------------------------------------------------------------
# Base protocol (structural typing — no inheritance required)
# ---------------------------------------------------------------------------

class BaseAdapter:
    """Convenience base class for framework adapters.

    Sub-classes must implement ``intercept_output`` and
    ``get_task_decomposition``.
    """

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        """Convert framework-specific output into an ``AgentOutput``."""
        raise NotImplementedError

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        """Return ``(task_description, subtask_list)``."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Generic adapter — works with any framework or custom agents
# ---------------------------------------------------------------------------

class GenericAdapter(BaseAdapter):
    """Adapter for custom / ad-hoc agent systems.

    Use this when your agents are plain Python functions rather than a
    framework like CrewAI or LangGraph.

    Parameters
    ----------
    task : str
        Top-level task description.
    subtasks : list[dict]
        Each dict has ``"name"`` and optional ``"scope"``, ``"agent"``.
    model_map : dict, optional
        Maps agent_id → model name for trust classification.
    """

    def __init__(
        self,
        task: str = "",
        subtasks: list[dict[str, str]] | None = None,
        model_map: dict[str, str] | None = None,
    ) -> None:
        self._task = task
        self._subtasks = subtasks or []
        self._model_map = model_map or {}

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        model = metadata.get("model", self._model_map.get(agent_id, ""))
        return AgentOutput(
            agent_id=agent_id,
            output_text=output,
            model=model,
            role=metadata.get("role", ""),
            subtask=metadata.get("subtask", ""),
            tools_used=metadata.get("tools_used", []),
            tool_results=metadata.get("tool_results", {}),
            rag_sources=metadata.get("rag_sources", []),
            citations=metadata.get("citations", []),
        )

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        return self._task, self._subtasks
