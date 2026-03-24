"""WebApplicationSite — the central site model for web applications."""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict, deque
from .models import (
    WebCoordinate, WebMorphism, WebCoveringFamily,
    DescentCondition, DescentViolation, RequestLifecycle,
)
from .coordinate_kinds import WebCoordinateKind
from .morphism_kinds import CrossLanguageMorphismKind, _MORPHISM_LAYERS


# Maps morphism kinds to descent condition types and descriptions
_DESCENT_MAP: dict[CrossLanguageMorphismKind, tuple[str, str, str]] = {
    CrossLanguageMorphismKind.API_CONTRACT: (
        "api_contract", "Route ∩ JS fetch",
        "Response JSON schema matches JS destructuring",
    ),
    CrossLanguageMorphismKind.ORM_MAPPING: (
        "orm_schema", "Model ∩ DB Schema",
        "ORM column types match DDL column types; NULLability agrees",
    ),
    CrossLanguageMorphismKind.CONTEXT_PROVISION: (
        "template_variable", "Route ∩ Template",
        "Every {{ var }} in template has a corresponding key in render_template",
    ),
    CrossLanguageMorphismKind.DOM_SELECTION: (
        "dom_element", "JS DOM ref ∩ HTML",
        "Every getElementById(x) has id=\"x\" in emitted HTML",
    ),
    CrossLanguageMorphismKind.CLASS_MANIPULATION: (
        "css_class", "JS class ref ∩ CSS",
        "Every classList.add('x') has .x { ... } in stylesheet",
    ),
    CrossLanguageMorphismKind.CLASS_REFERENCE: (
        "css_class", "JS class ref ∩ CSS",
        "Every classList.add('x') has .x { ... } in stylesheet",
    ),
    CrossLanguageMorphismKind.FORM_BINDING: (
        "form_route", "Form ∩ Route",
        "Form action URL matches a route; form fields match expected args",
    ),
    CrossLanguageMorphismKind.AUTH_STATE_SYNC: (
        "auth_session", "Auth decorator ∩ Session",
        "@login_required checks session['user_id']; JS auth state consistent",
    ),
    CrossLanguageMorphismKind.CONSTRAINT_ENCODING: (
        "db_constraint", "DB constraint ∩ Handler",
        "NOT NULL columns have non-null writes in all code paths",
    ),
    CrossLanguageMorphismKind.ERROR_PROPAGATION: (
        "error_handler", "Error handler ∩ JS catch",
        "Server error codes have client-side handling",
    ),
    CrossLanguageMorphismKind.SELECTOR_MATCH: (
        "template_css", "Template ∩ CSS",
        "Template-emitted class/id attributes have CSS rules",
    ),
}

# The 12 standard request lifecycle stage names from §2.4
_LIFECYCLE_STAGES = [
    "browser.user_action",
    "browser.fetch_dispatch",
    "http.request_transit",
    "flask.url_routing",
    "flask.before_request",
    "flask.view_function",
    "flask.db_query",
    "flask.template_render",
    "http.response_transit",
    "browser.dom_update",
    "browser.css_reflow",
    "browser.paint",
]


@dataclass
class WebApplicationSite:
    name: str = "web_application"
    coordinates_store: dict[str, WebCoordinate] = field(default_factory=dict, repr=False)
    morphisms_store: dict[str, WebMorphism] = field(default_factory=dict, repr=False)
    covering_families_store: dict[str, WebCoveringFamily] = field(default_factory=dict, repr=False)

    # ── Mutators ──────────────────────────────────────────────────────────

    def add_coordinate(self, coord: WebCoordinate) -> None:
        self.coordinates_store[coord.id] = coord

    def add_morphism(self, m: WebMorphism) -> None:
        self.morphisms_store[m.id] = m

    def add_covering_family(self, f: WebCoveringFamily) -> None:
        self.covering_families_store[f.id] = f

    # ── Accessors ─────────────────────────────────────────────────────────

    def get_coordinate(self, coord_id: str) -> WebCoordinate | None:
        return self.coordinates_store.get(coord_id)

    def get_morphism(self, morphism_id: str) -> WebMorphism | None:
        return self.morphisms_store.get(morphism_id)

    @property
    def coordinates(self) -> list[WebCoordinate]:
        return list(self.coordinates_store.values())

    @property
    def morphisms(self) -> list[WebMorphism]:
        return list(self.morphisms_store.values())

    @property
    def covering_families(self) -> list[WebCoveringFamily]:
        return list(self.covering_families_store.values())

    # ── Layer queries ─────────────────────────────────────────────────────

    def coordinates_in_layer(self, layer: str) -> list[WebCoordinate]:
        """Return all coordinates in a given language layer."""
        return [c for c in self.coordinates if c.language_layer == layer]

    def morphisms_between_layers(self, source_layer: str, target_layer: str) -> list[WebMorphism]:
        """Return morphisms where source coord is in source_layer and target in target_layer."""
        result: list[WebMorphism] = []
        for m in self.morphisms:
            src = self.get_coordinate(m.source_id)
            tgt = self.get_coordinate(m.target_id)
            if src and tgt and src.language_layer == source_layer and tgt.language_layer == target_layer:
                result.append(m)
        return result

    def cross_language_morphisms(self) -> list[WebMorphism]:
        """Return all morphisms that cross language layer boundaries."""
        result: list[WebMorphism] = []
        for m in self.morphisms:
            src = self.get_coordinate(m.source_id)
            tgt = self.get_coordinate(m.target_id)
            if src and tgt and src.language_layer != tgt.language_layer:
                result.append(m)
        return result

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def build_request_lifecycle_cover(self, route_url: str, method: str = "GET") -> WebCoveringFamily:
        """Construct a covering family for the full request lifecycle from §2.4."""
        family_id = f"lifecycle:{method}:{route_url}"
        return WebCoveringFamily(
            id=family_id,
            base_id=route_url,
            member_ids=list(_LIFECYCLE_STAGES),
            label=f"Request lifecycle for {method} {route_url}",
            lifecycle_stage="full",
        )

    # ── Overlap / Descent ─────────────────────────────────────────────────

    def overlap_pairs(self) -> list[tuple[str, str]]:
        """Return pairs of coordinate IDs connected by a morphism."""
        return [(m.source_id, m.target_id) for m in self.morphisms]

    def descent_conditions(self) -> list[DescentCondition]:
        """Auto-generate descent conditions from §2.5 based on current morphisms."""
        conditions: list[DescentCondition] = []
        for m in self.morphisms:
            entry = _DESCENT_MAP.get(m.kind)
            if entry is None:
                continue
            cond_type, overlap_name, description = entry
            conditions.append(DescentCondition(
                id=f"dc:{m.id}",
                overlap_name=overlap_name,
                description=description,
                left_coordinate_id=m.source_id,
                right_coordinate_id=m.target_id,
                condition_type=cond_type,
            ))
        return conditions

    def check_descent(self) -> list[DescentViolation]:
        """Run all descent checks. Returns list of violations found."""
        violations: list[DescentViolation] = []
        for cond in self.descent_conditions():
            left = self.get_coordinate(cond.left_coordinate_id)
            right = self.get_coordinate(cond.right_coordinate_id)
            if left is None or right is None:
                violations.append(DescentViolation(
                    id=f"v:{cond.id}",
                    condition_id=cond.id,
                    message=f"Missing coordinate for descent condition '{cond.overlap_name}': "
                            f"left={cond.left_coordinate_id} right={cond.right_coordinate_id}",
                    severity="error",
                    repair_hint="Ensure both coordinates exist in the site.",
                ))
        return violations

    # ── Graph analysis ────────────────────────────────────────────────────

    def connected_components(self) -> list[list[str]]:
        """BFS to find connected components of the morphism graph."""
        if not self.coordinates_store:
            return []

        adj: dict[str, set[str]] = defaultdict(set)
        for m in self.morphisms:
            adj[m.source_id].add(m.target_id)
            adj[m.target_id].add(m.source_id)

        visited: set[str] = set()
        components: list[list[str]] = []
        for cid in self.coordinates_store:
            if cid in visited:
                continue
            component: list[str] = []
            queue: deque[str] = deque([cid])
            visited.add(cid)
            while queue:
                node = queue.popleft()
                component.append(node)
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited and neighbor in self.coordinates_store:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(component)
        return components

    def language_boundary_graph(self) -> dict[str, list[str]]:
        """Adjacency between language layers via cross-language morphisms."""
        graph: dict[str, set[str]] = defaultdict(set)
        for m in self.cross_language_morphisms():
            src = self.get_coordinate(m.source_id)
            tgt = self.get_coordinate(m.target_id)
            if src and tgt:
                graph[src.language_layer].add(tgt.language_layer)
                graph[tgt.language_layer].add(src.language_layer)
        return {k: sorted(v) for k, v in graph.items()}

    # ── Serialization ─────────────────────────────────────────────────────

    def serialize(self) -> dict:
        """Serialize to dict."""
        return {
            "name": self.name,
            "coordinates": [c.to_dict() for c in self.coordinates],
            "morphisms": [m.to_dict() for m in self.morphisms],
            "covering_families": [f.to_dict() for f in self.covering_families],
        }

    @classmethod
    def parse(cls, data: dict) -> WebApplicationSite:
        """Deserialize from dict."""
        site = cls(name=data.get("name", "web_application"))
        for cd in data.get("coordinates", []):
            site.add_coordinate(WebCoordinate.from_dict(cd))
        for md in data.get("morphisms", []):
            site.add_morphism(WebMorphism.from_dict(md))
        for fd in data.get("covering_families", []):
            site.add_covering_family(WebCoveringFamily.from_dict(fd))
        return site
