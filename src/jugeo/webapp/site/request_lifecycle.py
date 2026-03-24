"""Request lifecycle covering families for web applications."""
from __future__ import annotations
from dataclasses import dataclass, field
from .models import RequestLifecycle, WebCoveringFamily, DescentCondition, WebCoordinate
from .coordinate_kinds import WebCoordinateKind


@dataclass
class LifecycleStage:
    name: str
    layer: str
    description: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "layer": self.layer,
            "description": self.description,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> LifecycleStage:
        return cls(
            name=data["name"],
            layer=data["layer"],
            description=data["description"],
            inputs=data.get("inputs", []),
            outputs=data.get("outputs", []),
        )


@dataclass
class DataFlowEdge:
    source_stage: str
    target_stage: str
    data_key: str
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "source_stage": self.source_stage,
            "target_stage": self.target_stage,
            "data_key": self.data_key,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DataFlowEdge:
        return cls(
            source_stage=data["source_stage"],
            target_stage=data["target_stage"],
            data_key=data["data_key"],
            description=data.get("description", ""),
        )


STANDARD_LIFECYCLE_STAGES: list[LifecycleStage] = [
    LifecycleStage("browser.user_action", "javascript", "User interacts with UI element",
                   inputs=["user_gesture"], outputs=["dom_event"]),
    LifecycleStage("browser.fetch_dispatch", "javascript", "JS constructs and dispatches HTTP request",
                   inputs=["dom_event"], outputs=["http_request"]),
    LifecycleStage("http.request_transit", "http", "HTTP request travels to server",
                   inputs=["http_request"], outputs=["server_request"]),
    LifecycleStage("flask.url_routing", "python", "Flask URL routing resolves handler",
                   inputs=["server_request"], outputs=["route_match"]),
    LifecycleStage("flask.before_request", "python", "Middleware / before_request hooks",
                   inputs=["route_match"], outputs=["auth_context"]),
    LifecycleStage("flask.view_function", "python", "View function executes business logic",
                   inputs=["auth_context"], outputs=["response_data"]),
    LifecycleStage("flask.db_query", "database", "Database queries executed",
                   inputs=["response_data"], outputs=["db_result"]),
    LifecycleStage("flask.template_render", "template", "Jinja2 renders template with context",
                   inputs=["db_result"], outputs=["rendered_html"]),
    LifecycleStage("http.response_transit", "http", "HTTP response travels to browser",
                   inputs=["rendered_html"], outputs=["browser_response"]),
    LifecycleStage("browser.dom_update", "javascript", "JS processes response, updates DOM",
                   inputs=["browser_response"], outputs=["updated_dom"]),
    LifecycleStage("browser.css_reflow", "css", "Browser recomputes layout",
                   inputs=["updated_dom"], outputs=["layout_tree"]),
    LifecycleStage("browser.paint", "css", "Browser paints pixels",
                   inputs=["layout_tree"], outputs=["rendered_pixels"]),
]


class RequestLifecycleBuilder:
    """Builds a WebCoveringFamily for the 12-stage request lifecycle."""

    def build(self, route_url: str, method: str = "GET",
              lifecycle_id: str | None = None) -> tuple[RequestLifecycle, WebCoveringFamily]:
        """Build the lifecycle and its covering family."""
        lifecycle = self.build_lifecycle(route_url, method, lifecycle_id)
        lid = lifecycle.id
        family = WebCoveringFamily(
            id=f"cover:{lid}",
            base_id=route_url,
            member_ids=list(lifecycle.stages),
            label=f"Request lifecycle cover for {method} {route_url}",
            lifecycle_stage="full",
        )
        return lifecycle, family

    def build_lifecycle(self, route_url: str, method: str = "GET",
                        lifecycle_id: str | None = None) -> RequestLifecycle:
        """Build just the RequestLifecycle model."""
        lid = lifecycle_id or f"lifecycle:{method}:{route_url}"
        return RequestLifecycle(
            id=lid,
            route_url=route_url,
            method=method,
            stages=[s.name for s in STANDARD_LIFECYCLE_STAGES],
        )


def lifecycle_overlap_conditions(lifecycle: RequestLifecycle) -> list[DescentCondition]:
    """Generate overlap conditions between adjacent lifecycle stages."""
    conditions: list[DescentCondition] = []
    stages = lifecycle.stages
    for i in range(len(stages) - 1):
        left = stages[i]
        right = stages[i + 1]
        conditions.append(DescentCondition(
            id=f"lc:{lifecycle.id}:{i}",
            overlap_name=f"{left} ∩ {right}",
            description=f"Data handed from {left} to {right} satisfies boundary contract",
            left_coordinate_id=left,
            right_coordinate_id=right,
            condition_type="lifecycle_boundary",
        ))
    return conditions


def trace_data_flow(site: object, lifecycle: RequestLifecycle) -> list[DataFlowEdge]:
    """Trace data flow through the lifecycle based on site morphisms."""
    stage_lookup: dict[str, LifecycleStage] = {s.name: s for s in STANDARD_LIFECYCLE_STAGES}
    edges: list[DataFlowEdge] = []
    stages = lifecycle.stages
    for i in range(len(stages) - 1):
        src_stage = stage_lookup.get(stages[i])
        tgt_stage = stage_lookup.get(stages[i + 1])
        if src_stage and tgt_stage and src_stage.outputs and tgt_stage.inputs:
            data_key = src_stage.outputs[0]
            edges.append(DataFlowEdge(
                source_stage=src_stage.name,
                target_stage=tgt_stage.name,
                data_key=data_key,
                description=f"{data_key} flows from {src_stage.name} to {tgt_stage.name}",
            ))
    return edges
