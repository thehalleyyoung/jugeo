"""CrewAI adapter for jugeo-agents.

Wraps a CrewAI ``Crew`` with JuGeo verification by intercepting task
callbacks and converting CrewAI-specific output structures into
``AgentOutput`` records.

Requires the ``crewai`` optional dependency::

    pip install jugeo-agents[crewai]
"""

from __future__ import annotations

from typing import Any

from jugeo_agents.types import AgentOutput
from jugeo_agents.adapters.base import BaseAdapter

try:
    from crewai import Crew, Agent as CrewAgent, Task as CrewTask  # type: ignore[import-untyped]

    _HAS_CREWAI = True
except ImportError:
    _HAS_CREWAI = False


class CrewAIAdapter(BaseAdapter):
    """Adapter that bridges CrewAI's callback system with JuGeo verification.

    Usage::

        from crewai import Crew, Agent, Task
        from jugeo_agents import JuGeoAgentWrapper
        from jugeo_agents.adapters.crewai_adapter import CrewAIAdapter

        crew = Crew(agents=[...], tasks=[...])
        adapter = CrewAIAdapter(crew)
        jugeo = JuGeoAgentWrapper()

        # Pre-flight coverage check
        task_desc, subtasks = adapter.get_task_decomposition()
        coverage = jugeo.verify_task_decomposition(task_desc, subtasks)

        # Install verification callback
        adapter.install_callback(jugeo)

        # Run as usual
        crew.kickoff()
        report = jugeo.on_pipeline_complete()

    Parameters
    ----------
    crew : Crew
        A fully configured CrewAI ``Crew`` instance.
    """

    def __init__(self, crew: Any = None) -> None:
        if not _HAS_CREWAI and crew is not None:
            raise ImportError(
                "CrewAI is required for CrewAIAdapter.  "
                "Install with: pip install jugeo-agents[crewai]"
            )
        self._crew = crew

    # ---- BaseAdapter interface -------------------------------------------

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        return AgentOutput(
            agent_id=agent_id,
            output_text=output,
            model=metadata.get("model", ""),
            role=metadata.get("role", agent_id),
            subtask=metadata.get("task_description", ""),
            tools_used=metadata.get("tools_used", []),
            tool_results=metadata.get("tool_results", {}),
        )

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        """Extract task and subtask info from the CrewAI Crew object."""
        if self._crew is None:
            return "", []

        # Build a combined task description from the crew
        task_descs: list[str] = []
        subtasks: list[dict[str, str]] = []
        for i, task in enumerate(getattr(self._crew, "tasks", [])):
            desc = getattr(task, "description", f"task_{i}")
            agent = getattr(task, "agent", None)
            agent_role = getattr(agent, "role", f"agent_{i}") if agent else f"agent_{i}"
            task_descs.append(desc)
            subtasks.append({
                "name": f"task_{i}_{agent_role}",
                "scope": desc,
                "agent": agent_role,
            })

        combined_task = "; ".join(task_descs) if task_descs else "CrewAI pipeline"
        return combined_task, subtasks

    # ---- CrewAI-specific helpers ----------------------------------------

    def install_callback(self, jugeo: Any) -> None:
        """Install a task callback on the crew that feeds JuGeo.

        Parameters
        ----------
        jugeo : JuGeoAgentWrapper
            The wrapper instance to send outputs to.
        """
        if self._crew is None:
            return

        original_cb = getattr(self._crew, "task_callback", None)

        def _verified_callback(task_output: Any) -> None:
            agent_name = getattr(task_output, "agent", "unknown")
            raw = getattr(task_output, "raw", str(task_output))
            tools = getattr(task_output, "tools_used", [])
            jugeo.on_agent_output(
                agent_id=str(agent_name),
                output=raw,
                metadata={"tools_used": tools, "role": str(agent_name)},
            )
            if original_cb is not None:
                original_cb(task_output)

        self._crew.task_callback = _verified_callback
