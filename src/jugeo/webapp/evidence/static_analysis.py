"""Cross-language static analyser for web applications.

Uses regex-based extraction to detect inconsistencies across the
Python ↔ Jinja2 ↔ JavaScript ↔ HTML ↔ CSS boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class CrossLanguageStaticAnalyzer:
    """Regex-powered cross-language consistency checker.

    Every ``check_*`` method returns a ``list[dict]`` where each dict has
    keys: *issue*, *severity*, *source_file*, *target_file*, *line*,
    *repair_hint*.
    """

    # ------------------------------------------------------------------
    # Template context
    # ------------------------------------------------------------------

    def check_template_context(
        self,
        route_handler_source: str,
        template_source: str,
        route_file: str = "route.py",
        template_file: str = "template.html",
    ) -> list[dict]:
        """Check that all template variables are provided by the route handler.

        Extracts ``{{ var }}`` occurrences from *template_source* and
        keyword arguments from ``render_template(...)`` calls in
        *route_handler_source*.
        """
        issues: list[dict] = []

        # Variables used in the template.
        tpl_vars: dict[str, int] = {}
        for line_no, line in enumerate(template_source.splitlines(), start=1):
            for m in re.finditer(r"\{\{\s*(\w+)", line):
                var = m.group(1)
                if var not in tpl_vars:
                    tpl_vars[var] = line_no

        # Variables provided via render_template.
        provided_vars: set[str] = set()
        for m in re.finditer(
            r"render_template\s*\([^)]+,\s*(.+)\)",
            route_handler_source,
            re.DOTALL,
        ):
            kwargs_str = m.group(1)
            provided_vars.update(re.findall(r"(\w+)\s*=", kwargs_str))

        # Variables provided via dict-style context.
        for m in re.finditer(
            r"context\s*=\s*\{(.+?)\}",
            route_handler_source,
            re.DOTALL,
        ):
            provided_vars.update(re.findall(r"['\"](\w+)['\"]", m.group(1)))

        # Built-in Jinja2 globals that don't need explicit provision.
        builtins = {
            "self", "loop", "request", "config", "g", "session",
            "url_for", "get_flashed_messages", "range", "lipsum",
            "dict", "cycler", "joiner", "namespace", "true",
            "false", "none",
        }

        missing = set(tpl_vars) - provided_vars - builtins
        for var in sorted(missing):
            issues.append({
                "issue": (
                    f"Template variable '{var}' used in "
                    f"{template_file} but not provided by route handler"
                ),
                "severity": "WARNING",
                "source_file": route_file,
                "target_file": template_file,
                "line": tpl_vars[var],
                "repair_hint": (
                    f"Add '{var}=<value>' to the render_template call "
                    f"in {route_file}"
                ),
            })

        return issues

    # ------------------------------------------------------------------
    # DOM references
    # ------------------------------------------------------------------

    def check_dom_references(
        self,
        js_source: str,
        html_source: str,
        js_file: str = "app.js",
        html_file: str = "index.html",
    ) -> list[dict]:
        """Check that JS getElementById calls reference existing HTML ids."""
        issues: list[dict] = []

        # IDs referenced in JavaScript.
        js_ids: dict[str, int] = {}
        for line_no, line in enumerate(js_source.splitlines(), start=1):
            for m in re.finditer(r"getElementById\(\s*['\"](\w+)['\"]\s*\)", line):
                eid = m.group(1)
                if eid not in js_ids:
                    js_ids[eid] = line_no

        # IDs defined in HTML.
        html_ids: set[str] = set()
        for m in re.finditer(r"id=['\"]([\\w-]+)['\"]", html_source):
            html_ids.add(m.group(1))
        # Broader pattern as fallback.
        for m in re.finditer(r'id=["\']([^"\']+)["\']', html_source):
            html_ids.add(m.group(1))

        missing = set(js_ids) - html_ids
        for eid in sorted(missing):
            issues.append({
                "issue": (
                    f"JS references DOM id '{eid}' via getElementById "
                    f"but no element with that id exists in {html_file}"
                ),
                "severity": "ERROR",
                "source_file": js_file,
                "target_file": html_file,
                "line": js_ids[eid],
                "repair_hint": (
                    f"Add an element with id='{eid}' to {html_file}, "
                    f"or remove the getElementById('{eid}') call in {js_file}"
                ),
            })

        return issues

    # ------------------------------------------------------------------
    # CSS references
    # ------------------------------------------------------------------

    def check_css_references(
        self,
        html_source: str,
        js_source: str,
        css_source: str,
        html_file: str = "",
        js_file: str = "",
        css_file: str = "",
    ) -> list[dict]:
        """Check that CSS classes used in HTML/JS are defined in CSS."""
        issues: list[dict] = []

        # Classes used in HTML.
        html_classes: set[str] = set()
        for m in re.finditer(r'class=["\']([\\w\\s-]+)["\']', html_source):
            html_classes.update(m.group(1).split())
        for m in re.finditer(r'class=["\']([^"\']+)["\']', html_source):
            html_classes.update(m.group(1).split())

        # Classes used in JS (classList manipulation).
        js_classes: set[str] = set()
        for m in re.finditer(
            r"classList\.(add|remove|toggle)\(\s*['\"](\w+)['\"]\s*\)", js_source
        ):
            js_classes.add(m.group(2))

        all_used = html_classes | js_classes

        # Classes defined in CSS.
        css_classes: set[str] = set()
        for m in re.finditer(r"\.([\w-]+)\s*\{", css_source):
            css_classes.add(m.group(1))

        undefined = all_used - css_classes
        for cls_name in sorted(undefined):
            # Determine source file for this class usage.
            if cls_name in js_classes:
                src = js_file or "script.js"
            else:
                src = html_file or "index.html"

            issues.append({
                "issue": (
                    f"CSS class '{cls_name}' used in {src} "
                    f"but not defined in {css_file or 'stylesheet'}"
                ),
                "severity": "WARNING",
                "source_file": src,
                "target_file": css_file or "styles.css",
                "line": 0,
                "repair_hint": (
                    f"Add a '.{cls_name}' rule to {css_file or 'styles.css'}, "
                    f"or remove the class reference in {src}"
                ),
            })

        return issues

    # ------------------------------------------------------------------
    # Form consistency
    # ------------------------------------------------------------------

    def check_form_consistency(
        self,
        py_source: str,
        html_source: str,
        py_file: str = "",
        html_file: str = "",
    ) -> list[dict]:
        """Check that HTML form actions point to existing Flask routes."""
        issues: list[dict] = []

        # Form actions from HTML.
        form_actions: list[dict] = []
        for line_no, line in enumerate(html_source.splitlines(), start=1):
            for m in re.finditer(
                r'action=["\']([/][^"\']*)["\']'
                r'\s+method=["\'](\w+)["\']',
                line,
                re.IGNORECASE,
            ):
                form_actions.append({
                    "action": m.group(1),
                    "method": m.group(2).upper(),
                    "line": line_no,
                })
            # Also match reversed order (method before action).
            for m in re.finditer(
                r'method=["\'](\w+)["\']'
                r'\s+action=["\']([/][^"\']*)["\']',
                line,
                re.IGNORECASE,
            ):
                form_actions.append({
                    "action": m.group(2),
                    "method": m.group(1).upper(),
                    "line": line_no,
                })

        # Flask routes from Python.
        routes: dict[str, set[str]] = {}
        for m in re.finditer(r"@\w+\.route\(\s*['\"]([^'\"]+)['\"]", py_source):
            route_path = m.group(1)
            methods_match = re.search(
                r"methods\s*=\s*\[([^\]]+)\]",
                py_source[m.start(): m.start() + 300],
            )
            if methods_match:
                methods_raw = methods_match.group(1)
                methods = {
                    s.strip().strip("'\"").upper()
                    for s in methods_raw.split(",")
                }
            else:
                methods = {"GET"}
            routes[route_path] = methods

        for fa in form_actions:
            action = fa["action"]
            method = fa["method"]

            # Try exact match first, then parameterised match.
            matched = False
            for route_path, route_methods in routes.items():
                # Convert Flask param syntax to regex.
                pattern = re.sub(r"<\w+:?\w*>", r"[^/]+", route_path)
                if re.fullmatch(pattern, action) and method in route_methods:
                    matched = True
                    break

            if not matched:
                issues.append({
                    "issue": (
                        f"Form action '{action}' (method={method}) in "
                        f"{html_file or 'template'} has no matching Flask route"
                    ),
                    "severity": "ERROR",
                    "source_file": html_file or "template.html",
                    "target_file": py_file or "routes.py",
                    "line": fa["line"],
                    "repair_hint": (
                        f"Add a route @app.route('{action}', "
                        f"methods=['{method}']) in {py_file or 'routes.py'}"
                    ),
                })

        return issues

    # ------------------------------------------------------------------
    # API consistency
    # ------------------------------------------------------------------

    def check_api_consistency(
        self,
        py_source: str,
        js_source: str,
        py_file: str = "",
        js_file: str = "",
    ) -> list[dict]:
        """Check that JS code only accesses JSON fields returned by Python."""
        issues: list[dict] = []

        # JSON keys returned from Python (in jsonify calls).
        py_json_keys: set[str] = set()
        for m in re.finditer(r"jsonify\s*\(\s*\{(.+?)\}\s*\)", py_source, re.DOTALL):
            body = m.group(1)
            py_json_keys.update(re.findall(r"['\"](\w+)['\"]\s*:", body))

        # Also handle dict-return patterns.
        for m in re.finditer(r"return\s+\{(.+?)\}", py_source, re.DOTALL):
            body = m.group(1)
            py_json_keys.update(re.findall(r"['\"](\w+)['\"]\s*:", body))

        if not py_json_keys:
            return issues

        # Fields accessed in JS from response data.
        js_fields: dict[str, int] = {}
        for line_no, line in enumerate(js_source.splitlines(), start=1):
            for m in re.finditer(r"response\.(\w+)", line):
                fld = m.group(1)
                if fld not in js_fields and fld not in {"json", "ok", "status", "text", "blob", "headers"}:
                    js_fields[fld] = line_no
            for m in re.finditer(r"data\[['\"](\w+)['\"]\]", line):
                fld = m.group(1)
                if fld not in js_fields:
                    js_fields[fld] = line_no
            for m in re.finditer(r"data\.(\w+)", line):
                fld = m.group(1)
                if fld not in js_fields and fld not in {"then", "catch", "finally", "length"}:
                    js_fields[fld] = line_no

        missing = set(js_fields) - py_json_keys
        for fld in sorted(missing):
            issues.append({
                "issue": (
                    f"JS accesses field '{fld}' from API response "
                    f"but Python handler does not return it"
                ),
                "severity": "WARNING",
                "source_file": js_file or "app.js",
                "target_file": py_file or "routes.py",
                "line": js_fields[fld],
                "repair_hint": (
                    f"Add '{fld}' to the jsonify/return dict in "
                    f"{py_file or 'routes.py'}, or remove the reference "
                    f"in {js_file or 'app.js'}"
                ),
            })

        return issues

    # ------------------------------------------------------------------
    # URL consistency
    # ------------------------------------------------------------------

    def check_url_consistency(
        self,
        py_source: str,
        template_source: str,
        py_file: str = "",
        template_file: str = "",
    ) -> list[dict]:
        """Check that url_for references in templates point to real routes."""
        issues: list[dict] = []

        # url_for targets in template.
        url_for_refs: dict[str, int] = {}
        for line_no, line in enumerate(template_source.splitlines(), start=1):
            for m in re.finditer(r"url_for\(\s*['\"]([^'\"]+)['\"]", line):
                fname = m.group(1)
                if fname not in url_for_refs:
                    url_for_refs[fname] = line_no

        # Route function names in Python.
        route_func_names: set[str] = set()
        lines = py_source.splitlines()
        for i, line in enumerate(lines):
            if re.search(r"@\w+\.route\(", line):
                # The function definition follows the decorator(s).
                for j in range(i + 1, min(i + 5, len(lines))):
                    fn_match = re.match(r"\s*def\s+(\w+)\s*\(", lines[j])
                    if fn_match:
                        route_func_names.add(fn_match.group(1))
                        break

        # Also handle blueprint-prefixed names.
        blueprint_names: set[str] = set()
        for m in re.finditer(
            r"(\w+)\s*=\s*Blueprint\(\s*['\"](\w+)['\"]", py_source
        ):
            blueprint_names.add(m.group(2))

        missing = set(url_for_refs) - route_func_names
        # Also allow blueprint.func form.
        still_missing: set[str] = set()
        for ref in missing:
            if "." in ref:
                parts = ref.split(".", 1)
                if parts[0] in blueprint_names and parts[1] in route_func_names:
                    continue
            still_missing.add(ref)

        for ref in sorted(still_missing):
            issues.append({
                "issue": (
                    f"url_for('{ref}') in {template_file or 'template'} "
                    f"references a function not found in {py_file or 'routes'}"
                ),
                "severity": "ERROR",
                "source_file": template_file or "template.html",
                "target_file": py_file or "routes.py",
                "line": url_for_refs[ref],
                "repair_hint": (
                    f"Add a route function named '{ref}' in "
                    f"{py_file or 'routes.py'}, or fix the url_for call"
                ),
            })

        return issues
