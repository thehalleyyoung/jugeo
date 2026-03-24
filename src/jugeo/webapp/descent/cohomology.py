"""
Čech cohomology computation for web language layers.

Implements the Čech nerve construction and cohomology computation from §2.5:
* H⁰ — global sections (consistent cross-language states)
* H¹ — 1-cocycles (pairwise overlap failures)
* H² — 2-cocycles (triple overlap failures)
"""
from __future__ import annotations

import hashlib
import itertools
from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation
from jugeo.webapp.cross_language.overlap_checker import OverlapChecker
from jugeo.webapp.descent.models import CohomologyClass, WebObstruction


__all__ = [
    "CechCohomology",
    "ObstructionClassifier",
]


# ---------------------------------------------------------------------------
# Layer / overlap metadata (shared with web_descent.py)
# ---------------------------------------------------------------------------

ALL_LAYERS: list[str] = ["python", "template", "js", "css", "html", "sql", "orm"]

# Overlap kind → (left_layer, right_layer)
_OVERLAP_LAYER_MAP: dict[str, tuple[str, str]] = {
    OverlapKind.ROUTE_TEMPLATE.value: ("python", "template"),
    OverlapKind.ROUTE_JS_FETCH.value: ("python", "js"),
    OverlapKind.MODEL_DB_SCHEMA.value: ("orm", "sql"),
    OverlapKind.JS_DOM_HTML.value: ("js", "html"),
    OverlapKind.JS_CLASS_CSS.value: ("js", "css"),
    OverlapKind.FORM_ROUTE.value: ("html", "python"),
    OverlapKind.TEMPLATE_CSS.value: ("template", "css"),
    OverlapKind.AUTH_SESSION.value: ("python", "python"),
    OverlapKind.DB_CONSTRAINT_HANDLER.value: ("sql", "python"),
    OverlapKind.ERROR_HANDLER_JS.value: ("python", "js"),
}

# site_data keys whose presence indicates a layer is active
_LAYER_PRESENCE_KEYS: dict[str, list[str]] = {
    "python": ["routes", "auth_decorators", "session_checks",
               "handlers", "error_handlers"],
    "template": ["templates", "template_classes", "render_calls",
                 "template_variables"],
    "js": ["fetch_calls", "js_dom_refs", "js_classes", "js_catch"],
    "css": ["css_classes", "defined_classes"],
    "html": ["html_ids", "forms"],
    "sql": ["tables", "constraints"],
    "orm": ["models"],
}

# Method dispatch table: overlap kind → checker method + site_data arg keys
_CHECK_DISPATCH: dict[str, dict[str, Any]] = {
    OverlapKind.ROUTE_TEMPLATE.value: {
        "method": "check_route_template",
        "args_keys": ("routes", "templates"),
    },
    OverlapKind.ROUTE_JS_FETCH.value: {
        "method": "check_route_js_fetch",
        "args_keys": ("routes", "fetch_calls"),
    },
    OverlapKind.MODEL_DB_SCHEMA.value: {
        "method": "check_model_db_schema",
        "args_keys": ("models", "tables"),
    },
    OverlapKind.JS_DOM_HTML.value: {
        "method": "check_js_dom_html",
        "args_keys": ("js_dom_refs", "html_ids"),
    },
    OverlapKind.JS_CLASS_CSS.value: {
        "method": "check_js_class_css",
        "args_keys": ("js_classes", "css_classes"),
    },
    OverlapKind.FORM_ROUTE.value: {
        "method": "check_form_route",
        "args_keys": ("forms", "routes"),
    },
    OverlapKind.TEMPLATE_CSS.value: {
        "method": "check_template_css",
        "args_keys": ("template_classes", "css_classes"),
    },
    OverlapKind.AUTH_SESSION.value: {
        "method": "check_auth_session",
        "args_keys": ("auth_decorators", "session_checks"),
    },
    OverlapKind.DB_CONSTRAINT_HANDLER.value: {
        "method": "check_db_constraint_handler",
        "args_keys": ("constraints", "handlers"),
    },
    OverlapKind.ERROR_HANDLER_JS.value: {
        "method": "check_error_handler_js",
        "args_keys": ("error_handlers", "js_catch"),
    },
}


def _vid(*parts: str) -> str:
    """Deterministic id from constituent strings."""
    raw = ":".join(parts)
    return "obs-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _data_for_key(site_data: dict, key: str) -> Any:
    """Retrieve a value from site_data with an appropriate empty default."""
    val = site_data.get(key)
    if val is not None:
        return val
    if key in ("html_ids", "js_classes", "css_classes", "template_classes",
               "used_classes", "defined_classes"):
        return set()
    return []


# ---------------------------------------------------------------------------
# CechCohomology
# ---------------------------------------------------------------------------

class CechCohomology:
    """
    Čech cohomology computation for the web language-layer covering.

    The *covering* is the collection of language layers (Python, Template,
    JS, CSS, HTML, SQL, ORM).  The *nerve* has:
    * vertices = layers with data in ``site_data``
    * edges = layer pairs connected by an overlap condition
    * triangles = layer triples where all three pairs have edges

    Cohomology groups:
    * H⁰ = global sections (coordinates consistent across all layers)
    * H¹ = ker δ₁ / im δ₀  — pairwise overlap failures
    * H² = ker δ₂ / im δ₁  — triple overlap failures
    """

    def __init__(self) -> None:
        self._checker = OverlapChecker()

    # -- public API ----------------------------------------------------------

    def compute_h0(self, site_data: dict) -> list[dict]:
        """
        Compute H⁰ — global sections.

        A global section is a named coordinate (e.g. a route path, a CSS
        class, a template variable) that is *consistently* referenced
        across every layer that mentions it.

        Returns a list of ``{"coordinate": str, "value": str,
        "layers": list[str]}`` dicts.
        """
        nerve = self._build_nerve(site_data)
        # Track named coordinates across layers
        coord_layers: dict[str, set[str]] = {}
        coord_values: dict[str, str] = {}

        # Routes are global coordinates
        for route in site_data.get("routes", []):
            name = f"route:{route['pattern']}"
            coord_layers.setdefault(name, set()).add("python")
            coord_values[name] = route["pattern"]

        # Template variables
        for tpl in site_data.get("templates", []):
            for var in tpl.get("variables", []):
                name = f"var:{var}"
                coord_layers.setdefault(name, set()).add("template")
                coord_values[name] = var

        # Routes that render templates → coordinate shared across python+template
        for route in site_data.get("routes", []):
            tpl_name = route.get("template", "")
            if tpl_name:
                for var in route.get("context_vars", []):
                    name = f"var:{var}"
                    coord_layers.setdefault(name, set()).add("python")
                    coord_values.setdefault(name, var)

        # CSS classes shared between template/js/css
        for cls_name in site_data.get("css_classes", set()):
            name = f"class:{cls_name}"
            coord_layers.setdefault(name, set()).add("css")
            coord_values[name] = cls_name
        for cls_name in site_data.get("template_classes", set()):
            name = f"class:{cls_name}"
            coord_layers.setdefault(name, set()).add("template")
            coord_values.setdefault(name, cls_name)
        for cls_name in site_data.get("js_classes", set()):
            name = f"class:{cls_name}"
            coord_layers.setdefault(name, set()).add("js")
            coord_values.setdefault(name, cls_name)

        # HTML ids shared between html/js
        for eid in site_data.get("html_ids", set()):
            name = f"id:{eid}"
            coord_layers.setdefault(name, set()).add("html")
            coord_values[name] = eid
        for ref in site_data.get("js_dom_refs", []):
            name = f"id:{ref['element_id']}"
            coord_layers.setdefault(name, set()).add("js")
            coord_values.setdefault(name, ref["element_id"])

        # A coordinate is a global section if it appears in ≥ 2 layers
        # AND has no violations on any of its overlap edges.
        violations = self._checker.check_all(site_data)
        violation_coords: set[str] = set()
        for v in violations:
            # Extract mentioned coordinates from the violation message
            msg = v.message
            for coord_name in coord_layers:
                val = coord_values.get(coord_name, "")
                if val and val in msg:
                    violation_coords.add(coord_name)

        global_sections: list[dict] = []
        for coord_name, layers in sorted(coord_layers.items()):
            if len(layers) >= 2 and coord_name not in violation_coords:
                global_sections.append({
                    "coordinate": coord_name,
                    "value": coord_values.get(coord_name, ""),
                    "layers": sorted(layers),
                })

        return global_sections

    def compute_h1(self, site_data: dict) -> list[WebObstruction]:
        """
        Compute H¹ — pairwise overlap failures (1-cocycles).

        Uses :class:`OverlapChecker` to detect all violations on the
        edges of the Čech nerve, then converts each violation into a
        :class:`WebObstruction` with cohomology class H¹.
        """
        violations = self._checker.check_all(site_data)
        obstructions: list[WebObstruction] = []

        for v in violations:
            kind_value = v.kind.value if isinstance(v.kind, OverlapKind) else str(v.kind)
            layers = _OVERLAP_LAYER_MAP.get(kind_value, ("unknown", "unknown"))
            obstructions.append(WebObstruction(
                id=v.id,
                cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
                overlap_kind=kind_value,
                description=v.message,
                coordinates=[
                    f"{layers[0]}:{v.left_detail}",
                    f"{layers[1]}:{v.right_detail}",
                ],
                severity=v.severity,
                repair_hint=v.repair_hint,
                evidence={
                    "condition_id": v.condition_id,
                    "left_detail": v.left_detail,
                    "right_detail": v.right_detail,
                    "file_path": v.file_path,
                    "line_number": v.line_number,
                },
            ))

        return obstructions

    def compute_h2(self, site_data: dict) -> list[WebObstruction]:
        """
        Compute H² — triple overlap failures (2-cocycles).

        A triple overlap failure occurs when three layers A, B, C all
        interact and there are violations on at least two of the three
        pairwise edges.  This means no single pairwise fix suffices —
        the three-way interaction itself is obstructed.

        For example: Python provides context vars → Template uses them
        with CSS classes → CSS defines those classes.  If the context var
        is missing AND the CSS class is missing, the triple (Python,
        Template, CSS) has an H² obstruction.
        """
        nerve = self._build_nerve(site_data)
        triangles = nerve.get("triangles", [])
        if not triangles:
            return []

        # Compute all pairwise violations and index by edge
        violations = self._checker.check_all(site_data)
        edge_violations: dict[tuple[str, str], list[OverlapViolation]] = {}
        for v in violations:
            kind_value = v.kind.value if isinstance(v.kind, OverlapKind) else str(v.kind)
            pair = _OVERLAP_LAYER_MAP.get(kind_value)
            if pair is None:
                continue
            edge = tuple(sorted(pair))
            edge_violations.setdefault(edge, []).append(v)  # type: ignore[arg-type]

        obstructions: list[WebObstruction] = []

        for triangle in triangles:
            a, b, c = sorted(triangle)
            edges_of_triangle = [(a, b), (a, c), (b, c)]
            violated_edges: list[tuple[str, str]] = []
            involved_violations: list[OverlapViolation] = []

            for edge in edges_of_triangle:
                vs = edge_violations.get(edge, [])
                if vs:
                    violated_edges.append(edge)
                    involved_violations.extend(vs)

            # An H² obstruction requires violations on ≥ 2 of the 3 edges
            if len(violated_edges) >= 2:
                obs_id = _vid("h2", a, b, c)
                descriptions = [v.message for v in involved_violations[:3]]
                obstructions.append(WebObstruction(
                    id=obs_id,
                    cohomology_class=CohomologyClass.H2_TRIPLE_OBSTRUCTION,
                    overlap_kind="triple_overlap",
                    description=(
                        f"Triple overlap obstruction on ({a}, {b}, {c}): "
                        f"violations on edges {violated_edges}. "
                        f"{descriptions[0]}"
                    ),
                    coordinates=[
                        f"{a}:triple_overlap",
                        f"{b}:triple_overlap",
                        f"{c}:triple_overlap",
                    ],
                    severity="high",
                    repair_hint=(
                        f"Fix consistency across all three layers: "
                        f"{a}, {b}, {c}. "
                        f"Start with: {involved_violations[0].repair_hint}"
                        if involved_violations
                        else f"Investigate the {a}↔{b}↔{c} triple interaction"
                    ),
                    evidence={
                        "triangle": [a, b, c],
                        "violated_edges": [list(e) for e in violated_edges],
                        "violation_count": len(involved_violations),
                        "violation_ids": [v.id for v in involved_violations],
                    },
                ))

        return obstructions

    # -- nerve construction --------------------------------------------------

    def _build_nerve(self, site_data: dict) -> dict:
        """
        Build the Čech nerve of the language-layer covering.

        Returns
        -------
        dict
            ``{"vertices": list[str], "edges": list[tuple[str, str]],
            "triangles": list[tuple[str, str, str]]}``

        Vertices are layers present in *site_data* (those with non-empty
        data for at least one of their indicator keys).  Edges are layer
        pairs that have an overlap condition between them.  Triangles are
        triples where all three pairwise edges exist.
        """
        # Determine active layers
        vertices: list[str] = []
        for layer in ALL_LAYERS:
            indicator_keys = _LAYER_PRESENCE_KEYS.get(layer, [])
            for key in indicator_keys:
                val = site_data.get(key)
                if val:
                    vertices.append(layer)
                    break

        vertex_set = set(vertices)

        # Determine edges from overlap map
        edges: list[tuple[str, str]] = []
        edge_set: set[tuple[str, str]] = set()
        for _kind, (left, right) in _OVERLAP_LAYER_MAP.items():
            if left in vertex_set and right in vertex_set:
                edge = tuple(sorted((left, right)))
                if edge not in edge_set:
                    edge_set.add(edge)  # type: ignore[arg-type]
                    edges.append(edge)  # type: ignore[arg-type]

        # Determine triangles: triples where all 3 edges exist
        triangles: list[tuple[str, str, str]] = []
        for combo in itertools.combinations(sorted(vertex_set), 3):
            a, b, c = combo
            if ((a, b) in edge_set and (a, c) in edge_set
                    and (b, c) in edge_set):
                triangles.append((a, b, c))

        return {
            "vertices": vertices,
            "edges": edges,
            "triangles": triangles,
        }

    # -- cochain algebra (abstract / structural) -----------------------------

    def _compute_cochains(
        self,
        nerve: dict,
        dimension: int,
    ) -> list:
        """
        Compute cochains at the given dimension.

        * dimension 0 → vertices (0-simplices)
        * dimension 1 → edges (1-simplices)
        * dimension 2 → triangles (2-simplices)

        Returns the list of simplices at that dimension.
        """
        if dimension == 0:
            return [(v,) for v in nerve.get("vertices", [])]
        elif dimension == 1:
            return [tuple(e) for e in nerve.get("edges", [])]
        elif dimension == 2:
            return [tuple(t) for t in nerve.get("triangles", [])]
        return []

    def _compute_coboundary(self, cochains: list) -> list:
        """
        Compute the coboundary map δ: Cⁿ → Cⁿ⁺¹.

        For a simplex σ = (v₀, …, vₙ), δσ is the list of (n+1)-simplices
        that have σ as a face (i.e. the cofaces of σ in the nerve).

        Returns a list of (simplex, cofaces) pairs.
        """
        result: list[tuple[tuple, list[tuple]]] = []
        for simplex in cochains:
            simplex_set = set(simplex)
            cofaces: list[tuple] = []
            # A coface is a simplex of dimension n+1 that contains all
            # vertices of the current simplex
            for layer in ALL_LAYERS:
                if layer not in simplex_set:
                    candidate = tuple(sorted(simplex_set | {layer}))
                    cofaces.append(candidate)
            result.append((simplex, cofaces))
        return result

    def _cocycles(self, cochains: list, coboundary: list) -> list:
        """
        Compute cocycles: kernel of the coboundary map (elements with δ = 0).

        A cochain is a cocycle if its coboundary vanishes — meaning there
        is no higher-dimensional simplex where it fails to extend.
        """
        # In our concrete setting, a cocycle at dimension n is a
        # simplex whose overlap conditions all pass.
        return [
            simplex for simplex, cofaces in coboundary
            if not cofaces or all(len(cf) > len(ALL_LAYERS) for cf in cofaces)
        ]

    def _coboundaries(self, cochains: list, prev_coboundary: list) -> list:
        """
        Compute coboundaries: image of the previous coboundary map.

        These are the cochains that are in the image of δ from the
        previous dimension.
        """
        image: list = []
        for _simplex, cofaces in prev_coboundary:
            image.extend(cofaces)
        # Deduplicate
        seen: set[tuple] = set()
        result: list = []
        for cf in image:
            if cf not in seen:
                seen.add(cf)
                result.append(cf)
        return result

    def _quotient(self, cocycles: list, coboundaries: list) -> list:
        """
        Compute H^n = cocycles / coboundaries.

        Returns elements in cocycles that are not in the coboundary image.
        """
        coboundary_set = {tuple(b) for b in coboundaries}
        return [z for z in cocycles if tuple(z) not in coboundary_set]


# ---------------------------------------------------------------------------
# ObstructionClassifier
# ---------------------------------------------------------------------------

# Root cause categories
_ROOT_CAUSES: dict[str, list[str]] = {
    "missing_template_var": [
        "does not pass", "template", "context_vars", "render_template",
    ],
    "api_mismatch": [
        "fetch", "expected field", "JSON", "does not provide", "api",
    ],
    "dom_missing": [
        "getElementById", "querySelector", "DOM", "not found in HTML",
    ],
    "css_missing": [
        "classList", "class", "CSS definition", "no CSS",
    ],
    "auth_bypass": [
        "login_required", "auth", "session", "no session",
        "no validation", "no authentication",
    ],
    "schema_drift": [
        "ORM", "model", "DB column", "table", "type mismatch",
        "nullable", "NOT NULL",
    ],
    "form_broken": [
        "form", "action", "no matching route", "form sends",
    ],
    "error_unhandled": [
        "error handler", "errorhandler", "status code", "catch",
    ],
}


class ObstructionClassifier:
    """
    Classify and cluster web obstructions.

    Provides severity/type classification and root-cause clustering for
    a set of :class:`WebObstruction` instances.
    """

    def classify(self, obstruction: WebObstruction) -> dict:
        """
        Classify an obstruction by type, severity, and affected layers.

        Returns
        -------
        dict
            ``{"type": str, "severity": str, "layers": list[str],
            "is_blocking": bool, "repair_priority": int}``
        """
        layers = sorted(obstruction.affected_layers)
        severity = obstruction.severity
        is_blocking = severity in ("critical", "high")
        priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3}

        # Determine type from cohomology class
        if obstruction.cohomology_class == CohomologyClass.H2_TRIPLE_OBSTRUCTION:
            obs_type = "triple_overlap"
        elif obstruction.cohomology_class == CohomologyClass.H0_GLOBAL_SECTION:
            obs_type = "global_section"
        else:
            obs_type = obstruction.overlap_kind

        return {
            "type": obs_type,
            "severity": severity,
            "layers": layers,
            "is_blocking": is_blocking,
            "repair_priority": priority_map.get(severity, 3),
        }

    def cluster_by_root_cause(
        self,
        obstructions: list[WebObstruction],
    ) -> dict[str, list[WebObstruction]]:
        """
        Group obstructions by likely root cause.

        Root cause categories:
        ``missing_template_var``, ``api_mismatch``, ``dom_missing``,
        ``css_missing``, ``auth_bypass``, ``schema_drift``,
        ``form_broken``, ``error_unhandled``.

        Returns
        -------
        dict[str, list[WebObstruction]]
            Mapping from root cause key to the obstructions in that cluster.
        """
        clusters: dict[str, list[WebObstruction]] = {}

        for obs in obstructions:
            cause = self._identify_root_cause(obs)
            clusters.setdefault(cause, []).append(obs)

        return clusters

    def _identify_root_cause(self, obstruction: WebObstruction) -> str:
        """Identify the most likely root cause for an obstruction."""
        desc_lower = obstruction.description.lower()
        kind_lower = obstruction.overlap_kind.lower()

        best_cause = "unknown"
        best_score = 0

        for cause, keywords in _ROOT_CAUSES.items():
            score = sum(1 for kw in keywords if kw.lower() in desc_lower)
            # Boost if the overlap kind matches common patterns
            if cause == "missing_template_var" and "route_template" in kind_lower:
                score += 2
            elif cause == "api_mismatch" and "route_js_fetch" in kind_lower:
                score += 2
            elif cause == "dom_missing" and "js_dom_html" in kind_lower:
                score += 2
            elif cause == "css_missing" and ("js_class_css" in kind_lower or "template_css" in kind_lower):
                score += 2
            elif cause == "auth_bypass" and "auth_session" in kind_lower:
                score += 2
            elif cause == "schema_drift" and "model_db_schema" in kind_lower:
                score += 2
            elif cause == "form_broken" and "form_route" in kind_lower:
                score += 2
            elif cause == "error_unhandled" and "error_handler_js" in kind_lower:
                score += 2

            if score > best_score:
                best_score = score
                best_cause = cause

        return best_cause
