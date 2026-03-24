"""
Cross-language reference resolver.

Resolves names that flow between language layers: Python kwargs → Jinja2
template variables, JS DOM refs → HTML ids, CSS class usage ↔ definitions,
API contracts, form actions, and static file references.

All resolution methods accept plain dicts (parsed earlier by the parsers
layer) and return ``CrossReference`` instances.
"""
from __future__ import annotations

import re
from typing import Any

from jugeo.webapp.cross_language.models import CrossReference


__all__ = [
    "CrossReferenceResolver",
    "URLPatternMatcher",
]


# ---------------------------------------------------------------------------
# URL pattern matching
# ---------------------------------------------------------------------------

class URLPatternMatcher:
    """Match Flask URL rule patterns against concrete URL strings."""

    # Flask type converters and their regex equivalents
    _TYPE_PATTERNS: dict[str, str] = {
        "int": r"\d+",
        "float": r"\d+(?:\.\d+)?",
        "path": r".+",
        "string": r"[^/]+",
        "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    }

    # Matches <converter:name> or <name>
    _PARAM_RE = re.compile(r"<(?:(\w+):)?(\w+)>")

    def extract_params(self, pattern: str) -> list[dict]:
        """
        Extract parameter names and types from a Flask URL pattern.

        Parameters
        ----------
        pattern : str
            A Flask URL rule, e.g. ``"/users/<int:id>/posts/<slug>"``.

        Returns
        -------
        list[dict]
            Each dict has keys ``"name"`` and ``"type"`` (default
            ``"string"`` when the converter is omitted).
        """
        params: list[dict] = []
        for match in self._PARAM_RE.finditer(pattern):
            converter = match.group(1) or "string"
            name = match.group(2)
            params.append({"name": name, "type": converter})
        return params

    def matches(self, flask_pattern: str, url_string: str) -> bool:
        """
        Test whether *url_string* matches *flask_pattern*.

        Parameters
        ----------
        flask_pattern : str
            A Flask URL rule, e.g. ``"/users/<int:id>"``.
        url_string : str
            A concrete URL, e.g. ``"/users/42"``.

        Returns
        -------
        bool
        """
        regex = self._pattern_to_regex(flask_pattern)
        return regex.fullmatch(url_string) is not None

    # -- internals -----------------------------------------------------------

    def _pattern_to_regex(self, pattern: str) -> re.Pattern:
        """Convert a Flask URL rule to a compiled regex."""
        def _replace(m: re.Match) -> str:
            converter = m.group(1) or "string"
            sub = self._TYPE_PATTERNS.get(converter, r"[^/]+")
            return f"({sub})"

        regex_str = self._PARAM_RE.sub(_replace, re.escape(pattern).replace(r"\<", "<").replace(r"\>", ">"))
        # Re-escape was too aggressive — undo escaping inside our subs.
        # Simpler: build from scratch.
        regex_str = self._build_regex(pattern)
        return re.compile(regex_str)

    def _build_regex(self, pattern: str) -> str:
        """Build a regex string from a Flask URL pattern."""
        parts: list[str] = []
        pos = 0
        for m in self._PARAM_RE.finditer(pattern):
            # Literal text before this parameter
            parts.append(re.escape(pattern[pos:m.start()]))
            converter = m.group(1) or "string"
            sub = self._TYPE_PATTERNS.get(converter, r"[^/]+")
            parts.append(f"({sub})")
            pos = m.end()
        # Trailing literal
        parts.append(re.escape(pattern[pos:]))
        return "".join(parts)


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

class CrossReferenceResolver:
    """
    Resolve cross-language references in a web project.

    Each ``resolve_*`` method takes parsed artefact dicts and produces
    ``CrossReference`` instances, indicating whether the reference was
    successfully resolved to a concrete target.
    """

    def __init__(self) -> None:
        self._url_matcher = URLPatternMatcher()

    # -- individual resolvers ------------------------------------------------

    def resolve_template_variables(
        self,
        render_calls: list[dict],
        template_variables: list[dict],
    ) -> list[CrossReference]:
        """
        Match ``render_template`` kwargs to ``{{ var }}`` usage.

        Each *render_call* dict::

            {"template": str, "kwargs": [str, ...], "file": str, "line": int}

        Each *template_variable* dict::

            {"template": str, "variable": str, "file": str, "line": int}

        Returns one ``CrossReference`` per template variable, resolved if a
        matching kwarg exists in some render call for the same template.
        """
        # Build map: template -> set of kwargs passed
        kwargs_by_template: dict[str, set[str]] = {}
        call_by_template: dict[str, dict] = {}
        for call in render_calls:
            tpl = call["template"]
            kwargs_by_template.setdefault(tpl, set()).update(call["kwargs"])
            call_by_template.setdefault(tpl, call)

        refs: list[CrossReference] = []
        for tv in template_variables:
            tpl = tv["template"]
            var = tv["variable"]
            available = kwargs_by_template.get(tpl, set())
            resolved = var in available
            source = call_by_template.get(tpl, {"file": "", "line": 0})
            refs.append(CrossReference(
                source_file=source.get("file", ""),
                source_line=source.get("line", 0),
                source_layer="python",
                target_name=var,
                target_layer="jinja2",
                reference_type="template_variable",
                resolved=resolved,
                resolution_target=f"{tpl}:{var}" if resolved else "",
            ))
        return refs

    def resolve_dom_references(
        self,
        js_dom_refs: list[dict],
        html_ids: set[str],
    ) -> list[CrossReference]:
        """
        Match ``getElementById`` / ``querySelector`` to HTML ``id`` attrs.

        Each *js_dom_ref* dict::

            {"element_id": str, "file": str, "line": int, "method": str}
        """
        refs: list[CrossReference] = []
        for ref in js_dom_refs:
            eid = ref["element_id"]
            resolved = eid in html_ids
            refs.append(CrossReference(
                source_file=ref["file"],
                source_line=ref["line"],
                source_layer="js",
                target_name=eid,
                target_layer="html",
                reference_type="dom_access",
                resolved=resolved,
                resolution_target=f"id={eid}" if resolved else "",
            ))
        return refs

    def resolve_css_classes(
        self,
        used_classes: set[str],
        defined_classes: set[str],
    ) -> list[CrossReference]:
        """
        Match CSS class usage to CSS definitions.

        Returns one ``CrossReference`` per used class.
        """
        refs: list[CrossReference] = []
        for cls_name in sorted(used_classes):
            resolved = cls_name in defined_classes
            refs.append(CrossReference(
                source_file="",
                source_line=0,
                source_layer="html",
                target_name=cls_name,
                target_layer="css",
                reference_type="class_reference",
                resolved=resolved,
                resolution_target=f".{cls_name}" if resolved else "",
            ))
        return refs

    def resolve_api_contracts(
        self,
        route_responses: list[dict],
        fetch_expectations: list[dict],
    ) -> list[CrossReference]:
        """
        Match Flask response shapes to JS fetch handling.

        Each *route_response* dict::

            {"route": str, "fields": [str], "status_codes": [int], "method": str}

        Each *fetch_expectation* dict::

            {"url": str, "expected_fields": [str], "method": str,
             "file": str, "line": int}
        """
        refs: list[CrossReference] = []
        for fetch in fetch_expectations:
            url = fetch["url"]
            method = fetch.get("method", "GET").upper()
            # Find matching route
            matched_route: dict | None = None
            for route in route_responses:
                if self._url_matcher.matches(route["route"], url):
                    if method in [m.upper() for m in route.get("method", "GET").split(",")]:
                        matched_route = route
                        break
                    # Also accept if method field is a plain string
                    if route.get("method", "GET").upper() == method:
                        matched_route = route
                        break

            if matched_route is None:
                # No matching route found — unresolved
                for ef in fetch.get("expected_fields", []):
                    refs.append(CrossReference(
                        source_file=fetch["file"],
                        source_line=fetch["line"],
                        source_layer="js",
                        target_name=ef,
                        target_layer="python",
                        reference_type="api_field",
                        resolved=False,
                    ))
                continue

            server_fields = set(matched_route.get("fields", []))
            for ef in fetch.get("expected_fields", []):
                resolved = ef in server_fields
                refs.append(CrossReference(
                    source_file=fetch["file"],
                    source_line=fetch["line"],
                    source_layer="js",
                    target_name=ef,
                    target_layer="python",
                    reference_type="api_field",
                    resolved=resolved,
                    resolution_target=f"{matched_route['route']}:{ef}" if resolved else "",
                ))
        return refs

    def resolve_form_actions(
        self,
        form_actions: list[dict],
        route_urls: list[dict],
    ) -> list[CrossReference]:
        """
        Match HTML form ``action`` URLs to Flask routes.

        Each *form_action* dict::

            {"action": str, "method": str, "fields": [str],
             "file": str, "line": int}

        Each *route_url* dict::

            {"pattern": str, "methods": [str], "args": [str],
             "file": str, "line": int}
        """
        refs: list[CrossReference] = []
        for form in form_actions:
            action = form["action"]
            form_method = form.get("method", "POST").upper()

            matched_route: dict | None = None
            for route in route_urls:
                if self._url_matcher.matches(route["pattern"], action):
                    route_methods = {m.upper() for m in route.get("methods", ["GET"])}
                    if form_method in route_methods:
                        matched_route = route
                        break

            resolved = matched_route is not None
            refs.append(CrossReference(
                source_file=form["file"],
                source_line=form["line"],
                source_layer="html",
                target_name=action,
                target_layer="python",
                reference_type="form_action",
                resolved=resolved,
                resolution_target=matched_route["pattern"] if matched_route else "",
            ))

            # Also check that form fields match route args
            if matched_route is not None:
                route_args = set(matched_route.get("args", []))
                for fld in form.get("fields", []):
                    field_resolved = fld in route_args
                    refs.append(CrossReference(
                        source_file=form["file"],
                        source_line=form["line"],
                        source_layer="html",
                        target_name=fld,
                        target_layer="python",
                        reference_type="form_field",
                        resolved=field_resolved,
                        resolution_target=f"{matched_route['pattern']}:{fld}" if field_resolved else "",
                    ))
        return refs

    def resolve_static_refs(
        self,
        static_refs: list[dict],
        static_files: set[str],
    ) -> list[CrossReference]:
        """
        Match ``url_for('static', ...)`` references to actual files.

        Each *static_ref* dict::

            {"filename": str, "file": str, "line": int}
        """
        refs: list[CrossReference] = []
        for sref in static_refs:
            fname = sref["filename"]
            resolved = fname in static_files
            refs.append(CrossReference(
                source_file=sref["file"],
                source_line=sref["line"],
                source_layer="python",
                target_name=fname,
                target_layer="static",
                reference_type="static_file",
                resolved=resolved,
                resolution_target=fname if resolved else "",
            ))
        return refs

    # -- aggregate resolver --------------------------------------------------

    def resolve_all(self, project_data: dict) -> list[CrossReference]:
        """
        Run all resolvers on *project_data*.

        Expected keys in *project_data*::

            render_calls, template_variables, js_dom_refs, html_ids,
            used_classes, defined_classes, route_responses,
            fetch_expectations, form_actions, route_urls,
            static_refs, static_files
        """
        refs: list[CrossReference] = []

        refs.extend(self.resolve_template_variables(
            project_data.get("render_calls", []),
            project_data.get("template_variables", []),
        ))
        refs.extend(self.resolve_dom_references(
            project_data.get("js_dom_refs", []),
            project_data.get("html_ids", set()),
        ))
        refs.extend(self.resolve_css_classes(
            project_data.get("used_classes", set()),
            project_data.get("defined_classes", set()),
        ))
        refs.extend(self.resolve_api_contracts(
            project_data.get("route_responses", []),
            project_data.get("fetch_expectations", []),
        ))
        refs.extend(self.resolve_form_actions(
            project_data.get("form_actions", []),
            project_data.get("route_urls", []),
        ))
        refs.extend(self.resolve_static_refs(
            project_data.get("static_refs", []),
            project_data.get("static_files", set()),
        ))

        return refs
