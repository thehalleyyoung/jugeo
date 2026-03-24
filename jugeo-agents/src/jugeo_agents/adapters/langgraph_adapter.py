"""LangGraph adapter for jugeo-agents.

Wraps LangGraph ``StateGraph`` nodes with JuGeo verification by
providing a node-wrapper function that intercepts outputs and feeds
them to the verification pipeline.

Requires the ``langgraph`` optional dependency::

    pip install jugeo-agents[langgraph]
"""

from __future__ import annotations

from typing import Any, Callable

from jugeo_agents.types import AgentOutput
from jugeo_agents.adapters.base import BaseAdapter

try:
    from langgraph.graph import StateGraph  # type: ignore[import-untyped]

    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False


class LangGraphAdapter(BaseAdapter):
    """Adapter that wraps LangGraph nodes with JuGeo verification.

    Usage::

        from langgraph.graph import StateGraph
        from jugeo_agents import JuGeoAgentWrapper
        from jugeo_agents.adapters.langgraph_adapter import LangGraphAdapter

        jugeo = JuGeoAgentWrapper()
        adapter = LangGraphAdapter(task="Analyze data", subtasks=[...])

        # Wrap individual nodes
        graph = StateGraph(MyState)
        graph.add_node("researcher", adapter.wrap_node(researcher_fn, jugeo))
        graph.add_node("analyst", adapter.wrap_node(analyst_fn, jugeo))

    Parameters
    ----------
    task : str
        Top-level task description.
    subtasks : list[dict]
        Subtask definitions for coverage checking.
    output_key : str
        Key in the state dict where the node stores its output text.
    """

    def __init__(
        self,
        task: str = "",
        subtasks: list[dict[str, str]] | None = None,
        output_key: str = "output",
    ) -> None:
        self._task = task
        self._subtasks = subtasks or []
        self._output_key = output_key

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

    def wrap_node(
        self,
        node_fn: Callable[..., dict[str, Any]],
        jugeo: Any,
        *,
        agent_id: str = "",
        model: str = "",
    ) -> Callable[..., dict[str, Any]]:
        """Return a wrapped version of *node_fn* that feeds JuGeo.

        Parameters
        ----------
        node_fn : callable
            Original LangGraph node function ``state -> state``.
        jugeo : JuGeoAgentWrapper
            The wrapper instance.
        agent_id : str
            Override agent ID (defaults to the function's ``__name__``).
        model : str
            LLM model name used by this node (for trust classification).
        """
        name = agent_id or getattr(node_fn, "__name__", "node")

        def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
            result = node_fn(state)
            output_text = result.get(self._output_key, "")
            if isinstance(output_text, str) and output_text:
                verification = jugeo.on_agent_output(
                    agent_id=name,
                    output=output_text,
                    metadata={
                        "model": model,
                        "subtask": name,
                        "role": name,
                        "tools_used": result.get("tools_used", []),
                    },
                )
                result["jugeo_trust"] = verification.trust_level.name
                result["jugeo_obstructions"] = [
                    o.kind.name for o in verification.obstructions
                ]
                result["jugeo_status"] = verification.status
            return result

        _wrapped.__name__ = f"jugeo_{name}"  # type: ignore[attr-defined]
        return _wrapped
