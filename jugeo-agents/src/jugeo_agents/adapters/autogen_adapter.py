"""AutoGen adapter for jugeo-agents.

Provides hooks into AutoGen's ``ConversableAgent`` message-handling
system to feed outputs to the JuGeo verification pipeline.

Requires the ``autogen`` optional dependency::

    pip install jugeo-agents[autogen]
"""

from __future__ import annotations

from typing import Any

from jugeo_agents.types import AgentOutput
from jugeo_agents.adapters.base import BaseAdapter

try:
    from autogen import ConversableAgent  # type: ignore[import-untyped]

    _HAS_AUTOGEN = True
except ImportError:
    _HAS_AUTOGEN = False


class AutoGenAdapter(BaseAdapter):
    """Adapter that hooks into AutoGen agent message handling.

    Usage::

        from autogen import ConversableAgent, GroupChat, GroupChatManager
        from jugeo_agents import JuGeoAgentWrapper
        from jugeo_agents.adapters.autogen_adapter import AutoGenAdapter

        jugeo = JuGeoAgentWrapper()
        adapter = AutoGenAdapter(task="Research report", subtasks=[...])

        # Register hook on each agent
        for agent in [researcher, analyst, writer]:
            adapter.register_hook(agent, jugeo)

    Parameters
    ----------
    task : str
        Top-level task description.
    subtasks : list[dict]
        Subtask definitions for coverage checking.
    """

    def __init__(
        self,
        task: str = "",
        subtasks: list[dict[str, str]] | None = None,
    ) -> None:
        self._task = task
        self._subtasks = subtasks or []

    def intercept_output(
        self, agent_id: str, output: str, metadata: dict[str, Any],
    ) -> AgentOutput:
        return AgentOutput(
            agent_id=agent_id,
            output_text=output,
            model=metadata.get("model", ""),
            role=metadata.get("role", agent_id),
            subtask=metadata.get("subtask", ""),
            tools_used=metadata.get("tools_used", []),
        )

    def get_task_decomposition(self) -> tuple[str, list[dict[str, str]]]:
        return self._task, self._subtasks

    def register_hook(self, agent: Any, jugeo: Any) -> None:
        """Register a reply hook on an AutoGen ``ConversableAgent``.

        The hook fires after the agent generates a reply, passing
        the output through JuGeo verification.

        Parameters
        ----------
        agent : ConversableAgent
            The AutoGen agent to instrument.
        jugeo : JuGeoAgentWrapper
            The JuGeo wrapper instance.
        """
        if not _HAS_AUTOGEN:
            raise ImportError(
                "AutoGen is required for AutoGenAdapter.  "
                "Install with: pip install jugeo-agents[autogen]"
            )

        agent_name = getattr(agent, "name", str(agent))
        model = ""
        llm_config = getattr(agent, "llm_config", None)
        if isinstance(llm_config, dict):
            model = llm_config.get("model", "")

        original_generate = getattr(agent, "generate_reply", None)
        if original_generate is None:
            return

        def _hooked_generate(*args: Any, **kwargs: Any) -> Any:
            reply = original_generate(*args, **kwargs)
            reply_text = reply if isinstance(reply, str) else str(reply)
            if reply_text:
                jugeo.on_agent_output(
                    agent_id=agent_name,
                    output=reply_text,
                    metadata={"model": model, "role": agent_name},
                )
            return reply

        agent.generate_reply = _hooked_generate  # type: ignore[attr-defined]

    def from_chat_history(
        self,
        messages: list[dict[str, Any]],
        jugeo: Any,
    ) -> None:
        """Replay a recorded AutoGen chat history through JuGeo.

        Useful for verifying a previously-run conversation offline.

        Parameters
        ----------
        messages : list[dict]
            AutoGen-format messages with ``"role"``/``"name"`` and ``"content"`` keys.
        jugeo : JuGeoAgentWrapper
            The wrapper instance.
        """
        for msg in messages:
            agent_id = msg.get("name", msg.get("role", "unknown"))
            content = msg.get("content", "")
            if content:
                jugeo.on_agent_output(
                    agent_id=agent_id,
                    output=content,
                    metadata={
                        "role": msg.get("role", ""),
                        "model": msg.get("model", ""),
                    },
                )
