"""
Cross-language morphism builder.

Constructs the morphism graph connecting coordinates across language
layers.  Morphisms are the structure maps of the sheaf; each one
asserts that two coordinates in different layers correspond.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from typing import Any

from jugeo.webapp.cross_language.models import CrossReference


__all__ = [
    "CrossLanguageMorphismBuilder",
    "MorphismGraph",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLIENT_LAYERS: set[str] = {"js", "javascript", "css", "html", "browser"}
SERVER_LAYERS: set[str] = {"python", "flask", "jinja2", "sql", "orm"}


def _mid(*parts: str) -> str:
    """Deterministic morphism id."""
    raw = ":".join(parts)
    return "m-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Morphism builder
# ---------------------------------------------------------------------------

class CrossLanguageMorphismBuilder:
    """
    Build cross-language morphism dicts from parsed artefacts.

    Morphism dict format::

        {"id": str, "source": str, "target": str, "kind": str,
         "resolved": bool, "details": dict}

    where ``source`` and ``target`` are coordinate ids (typically
    ``"layer:name"``).
    """

    # -- from resolved cross-references -------------------------------------

    def build_morphisms(self, cross_refs: list[CrossReference]) -> list[dict]:
        """
        Convert resolved cross-references to morphism dicts.

        Each ``CrossReference`` becomes one morphism.  Unresolved
        references produce morphisms with ``resolved=False``.
        """
        morphisms: list[dict] = []
        for ref in cross_refs:
            source_id = f"{ref.source_layer}:{ref.source_file}:{ref.source_line}"
            target_id = f"{ref.target_layer}:{ref.target_name}"
            morphisms.append({
                "id": _mid(source_id, target_id, ref.reference_type),
                "source": source_id,
                "target": target_id,
                "kind": ref.reference_type,
                "resolved": ref.resolved,
                "details": {
                    "source_file": ref.source_file,
                    "source_line": ref.source_line,
                    "target_name": ref.target_name,
                    "resolution_target": ref.resolution_target,
                },
            })
        return morphisms

    # -- context provision (render_template → template vars) ----------------

    def build_context_provision_morphisms(
        self,
        render_calls: list[dict],
        template_vars: list[dict],
    ) -> list[dict]:
        """
        Build morphisms from ``render_template`` context to template
        variables.

        Each *render_call*::

            {"template": str, "kwargs": [str], "file": str, "line": int}

        Each *template_var*::

            {"template": str, "variable": str, "file": str, "line": int}
        """
        morphisms: list[dict] = []

        # Map template → set of kwargs
        kwargs_by_template: dict[str, set[str]] = {}
        calls_by_template: dict[str, dict] = {}
        for call in render_calls:
            tpl = call["template"]
            kwargs_by_template.setdefault(tpl, set()).update(call["kwargs"])
            calls_by_template.setdefault(tpl, call)

        for tv in template_vars:
            tpl = tv["template"]
            var = tv["variable"]
            available = kwargs_by_template.get(tpl, set())
            resolved = var in available
            call = calls_by_template.get(tpl, {"file": "", "line": 0})
            source_id = f"python:{call.get('file', '')}:{call.get('line', 0)}"
            target_id = f"jinja2:{tpl}:{var}"
            morphisms.append({
                "id": _mid(source_id, target_id, "context_provision"),
                "source": source_id,
                "target": target_id,
                "kind": "context_provision",
                "resolved": resolved,
                "details": {
                    "template": tpl,
                    "variable": var,
                    "kwarg_passed": resolved,
                },
            })

        return morphisms

    # -- API contract (route responses → JS fetch) --------------------------

    def build_api_contract_morphisms(
        self,
        routes: list[dict],
        fetch_calls: list[dict],
    ) -> list[dict]:
        """
        Build morphisms from route responses to JS fetch handling.

        Each *route*::

            {"pattern": str, "methods": [str], "context_vars": [str],
             "template": str, "file": str, "line": int}

        Each *fetch_call*::

            {"url": str, "expected_fields": [str], "method": str,
             "file": str, "line": int}
        """
        from jugeo.webapp.cross_language.reference_resolver import URLPatternMatcher
        matcher = URLPatternMatcher()
        morphisms: list[dict] = []

        for fetch in fetch_calls:
            url = fetch["url"]
            method = fetch.get("method", "GET").upper()

            matched_route: dict | None = None
            for route in routes:
                route_methods = {m.upper() for m in route.get("methods", ["GET"])}
                if method in route_methods and matcher.matches(route["pattern"], url):
                    matched_route = route
                    break

            if matched_route is None:
                source_id = f"python:unknown_route:{url}"
                target_id = f"js:{fetch['file']}:{fetch['line']}"
                morphisms.append({
                    "id": _mid(source_id, target_id, "api_contract"),
                    "source": source_id,
                    "target": target_id,
                    "kind": "api_contract",
                    "resolved": False,
                    "details": {"url": url, "method": method, "error": "no matching route"},
                })
                continue

            server_fields = set(matched_route.get("context_vars", []))
            expected = set(fetch.get("expected_fields", []))

            source_id = f"python:{matched_route['file']}:{matched_route['line']}"
            target_id = f"js:{fetch['file']}:{fetch['line']}"
            morphisms.append({
                "id": _mid(source_id, target_id, "api_contract"),
                "source": source_id,
                "target": target_id,
                "kind": "api_contract",
                "resolved": expected.issubset(server_fields),
                "details": {
                    "route_pattern": matched_route["pattern"],
                    "url": url,
                    "method": method,
                    "server_fields": sorted(server_fields),
                    "expected_fields": sorted(expected),
                    "missing": sorted(expected - server_fields),
                },
            })

        return morphisms

    # -- DOM selection (JS → HTML ids) --------------------------------------

    def build_dom_selection_morphisms(
        self,
        js_dom_refs: list[dict],
        html_ids: set[str],
    ) -> list[dict]:
        """
        Build morphisms from JS DOM refs to HTML element ids.

        Each *js_dom_ref*::

            {"element_id": str, "file": str, "line": int, "method": str}
        """
        morphisms: list[dict] = []
        for ref in js_dom_refs:
            eid = ref["element_id"]
            source_id = f"js:{ref['file']}:{ref['line']}"
            target_id = f"html:id={eid}"
            morphisms.append({
                "id": _mid(source_id, target_id, "dom_selection"),
                "source": source_id,
                "target": target_id,
                "kind": "dom_selection",
                "resolved": eid in html_ids,
                "details": {
                    "element_id": eid,
                    "method": ref["method"],
                },
            })
        return morphisms

    # -- ORM mapping (model columns → DB columns) ---------------------------

    def build_orm_mapping_morphisms(
        self,
        models: list[dict],
        tables: list[dict],
    ) -> list[dict]:
        """
        Build morphisms from ORM model columns to DB table columns.

        Each *model*::

            {"name": str, "columns": [{"name": str, "type": str,
             "nullable": bool}]}

        Each *table*::

            {"name": str, "columns": [{"name": str, "type": str,
             "nullable": bool}]}
        """
        morphisms: list[dict] = []

        table_map: dict[str, dict] = {}
        for t in tables:
            table_map[t["name"].lower()] = t

        for model in models:
            model_name = model["name"]
            tbl = (
                table_map.get(model_name.lower())
                or table_map.get(model_name.lower() + "s")
                or table_map.get(model_name.lower() + "es")
            )
            if tbl is None:
                for col in model.get("columns", []):
                    source_id = f"orm:{model_name}.{col['name']}"
                    target_id = f"sql:?.{col['name']}"
                    morphisms.append({
                        "id": _mid(source_id, target_id, "orm_mapping"),
                        "source": source_id,
                        "target": target_id,
                        "kind": "orm_mapping",
                        "resolved": False,
                        "details": {
                            "model": model_name,
                            "column": col["name"],
                            "error": "no matching table",
                        },
                    })
                continue

            tbl_cols = {c["name"].lower(): c for c in tbl.get("columns", [])}
            for col in model.get("columns", []):
                col_lower = col["name"].lower()
                source_id = f"orm:{model_name}.{col['name']}"
                target_id = f"sql:{tbl['name']}.{col['name']}"
                resolved = col_lower in tbl_cols
                morphisms.append({
                    "id": _mid(source_id, target_id, "orm_mapping"),
                    "source": source_id,
                    "target": target_id,
                    "kind": "orm_mapping",
                    "resolved": resolved,
                    "details": {
                        "model": model_name,
                        "column": col["name"],
                        "orm_type": col["type"],
                        "db_type": tbl_cols[col_lower]["type"] if resolved else None,
                    },
                })

        return morphisms

    # -- event binding (JS handlers → DOM elements) -------------------------

    def build_event_binding_morphisms(
        self,
        event_handlers: list[dict],
        dom_elements: list[dict],
    ) -> list[dict]:
        """
        Build morphisms from JS event handlers to DOM elements.

        Each *event_handler*::

            {"element_id": str, "event": str, "file": str, "line": int}

        Each *dom_element*::

            {"id": str, "tag": str, "file": str}
        """
        morphisms: list[dict] = []
        element_ids = {e["id"] for e in dom_elements}

        for eh in event_handlers:
            eid = eh["element_id"]
            source_id = f"js:{eh['file']}:{eh['line']}"
            target_id = f"html:id={eid}"
            morphisms.append({
                "id": _mid(source_id, target_id, "event_binding"),
                "source": source_id,
                "target": target_id,
                "kind": "event_binding",
                "resolved": eid in element_ids,
                "details": {
                    "element_id": eid,
                    "event": eh["event"],
                },
            })

        return morphisms

    # -- CSS selector match (selectors → HTML elements) ---------------------

    def build_selector_match_morphisms(
        self,
        css_selectors: list[dict],
        html_elements: list[dict],
    ) -> list[dict]:
        """
        Build morphisms from CSS selectors to HTML elements.

        Each *css_selector*::

            {"selector": str, "file": str, "line": int}

        Each *html_element*::

            {"id": str, "classes": [str], "tag": str, "file": str}
        """
        morphisms: list[dict] = []

        # Collect available classes and ids from HTML
        available_ids: set[str] = set()
        available_classes: set[str] = set()
        available_tags: set[str] = set()
        for el in html_elements:
            if el.get("id"):
                available_ids.add(el["id"])
            available_classes.update(el.get("classes", []))
            if el.get("tag"):
                available_tags.add(el["tag"].lower())

        for sel in css_selectors:
            selector = sel["selector"]
            source_id = f"css:{sel['file']}:{sel['line']}"
            target_id = f"html:{selector}"

            # Simple selector resolution
            resolved = False
            if selector.startswith("#"):
                resolved = selector[1:] in available_ids
            elif selector.startswith("."):
                resolved = selector[1:] in available_classes
            else:
                # Tag selector
                resolved = selector.lower() in available_tags

            morphisms.append({
                "id": _mid(source_id, target_id, "selector_match"),
                "source": source_id,
                "target": target_id,
                "kind": "selector_match",
                "resolved": resolved,
                "details": {
                    "selector": selector,
                    "selector_type": (
                        "id" if selector.startswith("#")
                        else "class" if selector.startswith(".")
                        else "tag"
                    ),
                },
            })

        return morphisms


# ---------------------------------------------------------------------------
# Morphism graph
# ---------------------------------------------------------------------------

class MorphismGraph:
    """
    Graph of cross-language morphisms.

    Supports traversal, path-finding, connected-component detection,
    and filtering for cross-boundary morphisms.
    """

    def __init__(self) -> None:
        self._morphisms: dict[str, dict] = {}
        self._from_index: dict[str, list[str]] = defaultdict(list)
        self._to_index: dict[str, list[str]] = defaultdict(list)

    # -- mutation ------------------------------------------------------------

    def add_morphism(self, m: dict) -> None:
        """Add a morphism dict to the graph."""
        mid = m["id"]
        self._morphisms[mid] = m
        self._from_index[m["source"]].append(mid)
        self._to_index[m["target"]].append(mid)

    # -- queries -------------------------------------------------------------

    def morphisms_from(self, coord_id: str) -> list[dict]:
        """Get all morphisms starting from *coord_id*."""
        return [
            self._morphisms[mid]
            for mid in self._from_index.get(coord_id, [])
        ]

    def morphisms_to(self, coord_id: str) -> list[dict]:
        """Get all morphisms ending at *coord_id*."""
        return [
            self._morphisms[mid]
            for mid in self._to_index.get(coord_id, [])
        ]

    def path_between(
        self,
        source_id: str,
        target_id: str,
    ) -> list[dict] | None:
        """
        BFS shortest path from *source_id* to *target_id*.

        Returns a list of morphism dicts forming the path, or ``None``
        if no path exists.
        """
        if source_id == target_id:
            return []

        visited: set[str] = {source_id}
        # Queue of (current_node, path_of_morphism_ids)
        queue: deque[tuple[str, list[str]]] = deque()

        for mid in self._from_index.get(source_id, []):
            m = self._morphisms[mid]
            queue.append((m["target"], [mid]))

        while queue:
            node, path = queue.popleft()
            if node == target_id:
                return [self._morphisms[mid] for mid in path]
            if node in visited:
                continue
            visited.add(node)
            for mid in self._from_index.get(node, []):
                m = self._morphisms[mid]
                if m["target"] not in visited:
                    queue.append((m["target"], path + [mid]))

        return None

    def connected_components(self) -> list[set[str]]:
        """
        Find connected components in the morphism graph.

        Treats morphisms as undirected edges for this purpose.
        """
        # Collect all nodes
        all_nodes: set[str] = set()
        adj: dict[str, set[str]] = defaultdict(set)
        for m in self._morphisms.values():
            src, tgt = m["source"], m["target"]
            all_nodes.add(src)
            all_nodes.add(tgt)
            adj[src].add(tgt)
            adj[tgt].add(src)

        visited: set[str] = set()
        components: list[set[str]] = []

        for node in all_nodes:
            if node in visited:
                continue
            component: set[str] = set()
            stack = [node]
            while stack:
                n = stack.pop()
                if n in visited:
                    continue
                visited.add(n)
                component.add(n)
                for neighbour in adj.get(n, set()):
                    if neighbour not in visited:
                        stack.append(neighbour)
            components.append(component)

        return components

    def cross_boundary_morphisms(self) -> list[dict]:
        """
        Return morphisms crossing the client/server boundary.

        Client layers: js, javascript, css, html, browser.
        Server layers: python, flask, jinja2, sql, orm.
        """
        result: list[dict] = []
        for m in self._morphisms.values():
            src_layer = m["source"].split(":")[0].lower()
            tgt_layer = m["target"].split(":")[0].lower()
            src_side = (
                "client" if src_layer in CLIENT_LAYERS
                else "server" if src_layer in SERVER_LAYERS
                else "unknown"
            )
            tgt_side = (
                "client" if tgt_layer in CLIENT_LAYERS
                else "server" if tgt_layer in SERVER_LAYERS
                else "unknown"
            )
            if src_side != tgt_side and src_side != "unknown" and tgt_side != "unknown":
                result.append(m)
        return result

    # -- introspection -------------------------------------------------------

    @property
    def all_morphisms(self) -> list[dict]:
        """All morphisms in the graph."""
        return list(self._morphisms.values())

    @property
    def node_count(self) -> int:
        """Number of distinct nodes (coordinates)."""
        nodes: set[str] = set()
        for m in self._morphisms.values():
            nodes.add(m["source"])
            nodes.add(m["target"])
        return len(nodes)

    @property
    def edge_count(self) -> int:
        """Number of morphisms (edges)."""
        return len(self._morphisms)
