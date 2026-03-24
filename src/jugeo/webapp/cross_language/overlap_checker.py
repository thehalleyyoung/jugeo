"""
Overlap checker for cross-language analysis.

Implements the 10 overlap conditions between web-application language
layers.  Each check verifies that the sheaf condition holds on the
intersection of two coordinate patches; violations are Čech 1-cocycles.
"""
from __future__ import annotations

import hashlib
from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation


__all__ = [
    "OverlapChecker",
]


def _vid(*parts: str) -> str:
    """Deterministic violation id from constituent strings."""
    raw = ":".join(parts)
    return "v-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


class OverlapChecker:
    """
    Run all 10 overlap checks on parsed project data.

    Each ``check_*`` method accepts parsed artefact dicts and returns a
    list of ``OverlapViolation`` instances.  ``check_all`` dispatches to
    all of them.
    """

    # ------------------------------------------------------------------ 1
    def check_route_template(
        self,
        routes: list[dict],
        templates: list[dict],
    ) -> list[OverlapViolation]:
        """
        Every ``{{ var }}`` in a template must have a matching
        ``render_template`` kwarg in the route that renders it.

        *routes*: ``[{"pattern", "methods", "context_vars", "template",
        "file", "line"}, ...]``

        *templates*: ``[{"name", "variables", "file"}, ...]``
        """
        violations: list[OverlapViolation] = []

        template_map: dict[str, dict] = {t["name"]: t for t in templates}

        for route in routes:
            tpl_name = route.get("template", "")
            if not tpl_name or tpl_name not in template_map:
                continue

            tpl = template_map[tpl_name]
            context_vars = set(route.get("context_vars", []))
            template_vars = set(tpl.get("variables", []))

            # Variables used in template but not passed by route
            missing = template_vars - context_vars
            for var in sorted(missing):
                violations.append(OverlapViolation(
                    id=_vid("route_template", route["pattern"], tpl_name, var),
                    condition_id=f"oc:route_template:{tpl_name}",
                    kind=OverlapKind.ROUTE_TEMPLATE,
                    message=(
                        f"Template '{tpl_name}' uses '{{{{ {var} }}}}' but "
                        f"route '{route['pattern']}' does not pass it"
                    ),
                    severity="error",
                    left_detail=f"route {route['pattern']} context_vars={sorted(context_vars)}",
                    right_detail=f"template {tpl_name} variables={sorted(template_vars)}",
                    repair_hint=f"Add {var}={var} to render_template() call",
                    file_path=route.get("file", ""),
                    line_number=route.get("line", 0),
                ))

            # Variables passed by route but unused in template (warning)
            unused = context_vars - template_vars
            for var in sorted(unused):
                violations.append(OverlapViolation(
                    id=_vid("route_template_unused", route["pattern"], tpl_name, var),
                    condition_id=f"oc:route_template:{tpl_name}",
                    kind=OverlapKind.ROUTE_TEMPLATE,
                    message=(
                        f"Route '{route['pattern']}' passes '{var}' but "
                        f"template '{tpl_name}' never uses it"
                    ),
                    severity="warning",
                    left_detail=f"route passes {var}",
                    right_detail=f"template does not use {var}",
                    repair_hint=f"Remove {var} from render_template() call or use it in template",
                    file_path=route.get("file", ""),
                    line_number=route.get("line", 0),
                ))

        return violations

    # ------------------------------------------------------------------ 2
    def check_route_js_fetch(
        self,
        routes: list[dict],
        fetch_calls: list[dict],
    ) -> list[OverlapViolation]:
        """
        Response JSON fields must match JS destructuring / access patterns.

        *routes*: same schema as above, with ``context_vars`` representing
        JSON response fields.

        *fetch_calls*: ``[{"url", "expected_fields", "method", "file",
        "line"}, ...]``
        """
        violations: list[OverlapViolation] = []
        from jugeo.webapp.cross_language.reference_resolver import URLPatternMatcher
        matcher = URLPatternMatcher()

        for fetch in fetch_calls:
            url = fetch["url"]
            method = fetch.get("method", "GET").upper()
            expected = set(fetch.get("expected_fields", []))

            matched_route: dict | None = None
            for route in routes:
                route_methods = {m.upper() for m in route.get("methods", ["GET"])}
                if method in route_methods and matcher.matches(route["pattern"], url):
                    matched_route = route
                    break

            if matched_route is None:
                violations.append(OverlapViolation(
                    id=_vid("route_js_fetch_no_route", url, method),
                    condition_id="oc:route_js_fetch:no_route",
                    kind=OverlapKind.ROUTE_JS_FETCH,
                    message=f"JS fetches '{url}' ({method}) but no matching route found",
                    severity="error",
                    left_detail=f"fetch {method} {url}",
                    right_detail="no matching route",
                    repair_hint=f"Add a route for {method} {url}",
                    file_path=fetch.get("file", ""),
                    line_number=fetch.get("line", 0),
                ))
                continue

            server_fields = set(matched_route.get("context_vars", []))
            missing = expected - server_fields
            for fld in sorted(missing):
                violations.append(OverlapViolation(
                    id=_vid("route_js_fetch", url, fld),
                    condition_id=f"oc:route_js_fetch:{matched_route['pattern']}",
                    kind=OverlapKind.ROUTE_JS_FETCH,
                    message=(
                        f"JS expects field '{fld}' from {method} {url} but "
                        f"route '{matched_route['pattern']}' does not provide it"
                    ),
                    severity="error",
                    left_detail=f"route provides {sorted(server_fields)}",
                    right_detail=f"JS expects {sorted(expected)}",
                    repair_hint=f"Add '{fld}' to the route's JSON response",
                    file_path=fetch.get("file", ""),
                    line_number=fetch.get("line", 0),
                ))

        return violations

    # ------------------------------------------------------------------ 3
    def check_model_db_schema(
        self,
        models: list[dict],
        tables: list[dict],
    ) -> list[OverlapViolation]:
        """
        ORM column types must match DDL types; NULLability must agree.

        *models*: ``[{"name", "columns": [{"name", "type", "nullable"}]}, ...]``
        *tables*: ``[{"name", "columns": [{"name", "type", "nullable"}]}, ...]``
        """
        violations: list[OverlapViolation] = []

        # Type compatibility map (ORM type → acceptable DDL types)
        type_compat: dict[str, set[str]] = {
            "String": {"VARCHAR", "TEXT", "CHAR", "NVARCHAR", "varchar", "text", "char"},
            "Integer": {"INTEGER", "INT", "BIGINT", "SMALLINT", "integer", "int", "bigint"},
            "Float": {"FLOAT", "REAL", "DOUBLE", "NUMERIC", "DECIMAL", "float", "real"},
            "Boolean": {"BOOLEAN", "TINYINT", "BIT", "boolean", "tinyint"},
            "DateTime": {"DATETIME", "TIMESTAMP", "DATE", "datetime", "timestamp"},
            "Text": {"TEXT", "CLOB", "MEDIUMTEXT", "LONGTEXT", "text"},
            "Date": {"DATE", "date"},
            "Time": {"TIME", "time"},
            "LargeBinary": {"BLOB", "BYTEA", "BINARY", "blob", "bytea"},
            "JSON": {"JSON", "JSONB", "json", "jsonb"},
        }

        table_map: dict[str, dict] = {}
        for t in tables:
            table_map[t["name"].lower()] = t

        for model in models:
            model_name = model["name"]
            # Convention: model "User" → table "user" or "users"
            tbl = (
                table_map.get(model_name.lower())
                or table_map.get(model_name.lower() + "s")
                or table_map.get(model_name.lower() + "es")
            )
            if tbl is None:
                violations.append(OverlapViolation(
                    id=_vid("model_db_no_table", model_name),
                    condition_id=f"oc:model_db:{model_name}",
                    kind=OverlapKind.MODEL_DB_SCHEMA,
                    message=f"ORM model '{model_name}' has no matching DB table",
                    severity="error",
                    left_detail=f"model {model_name}",
                    right_detail="no matching table",
                    repair_hint=f"Create DB table for model '{model_name}'",
                ))
                continue

            tbl_cols: dict[str, dict] = {
                c["name"].lower(): c for c in tbl.get("columns", [])
            }

            for col in model.get("columns", []):
                col_name = col["name"].lower()
                if col_name not in tbl_cols:
                    violations.append(OverlapViolation(
                        id=_vid("model_db_missing_col", model_name, col["name"]),
                        condition_id=f"oc:model_db:{model_name}",
                        kind=OverlapKind.MODEL_DB_SCHEMA,
                        message=(
                            f"ORM column '{model_name}.{col['name']}' has no "
                            f"matching DB column in table '{tbl['name']}'"
                        ),
                        severity="error",
                        left_detail=f"model column {col['name']} ({col['type']})",
                        right_detail=f"table '{tbl['name']}' columns: {sorted(tbl_cols)}",
                        repair_hint=f"Add column '{col['name']}' to table '{tbl['name']}'",
                    ))
                    continue

                db_col = tbl_cols[col_name]

                # Type check
                orm_type = col["type"]
                db_type = db_col["type"]
                acceptable = type_compat.get(orm_type, set())
                if acceptable and db_type not in acceptable:
                    violations.append(OverlapViolation(
                        id=_vid("model_db_type", model_name, col["name"]),
                        condition_id=f"oc:model_db:{model_name}",
                        kind=OverlapKind.MODEL_DB_SCHEMA,
                        message=(
                            f"Type mismatch: ORM '{model_name}.{col['name']}' "
                            f"is {orm_type} but DB column is {db_type}"
                        ),
                        severity="error",
                        left_detail=f"ORM type {orm_type}",
                        right_detail=f"DB type {db_type}",
                        repair_hint=f"Align types for '{col['name']}'",
                    ))

                # Nullability check
                if col.get("nullable") != db_col.get("nullable"):
                    violations.append(OverlapViolation(
                        id=_vid("model_db_null", model_name, col["name"]),
                        condition_id=f"oc:model_db:{model_name}",
                        kind=OverlapKind.MODEL_DB_SCHEMA,
                        message=(
                            f"Nullability mismatch: ORM '{model_name}.{col['name']}' "
                            f"nullable={col['nullable']} but DB nullable={db_col['nullable']}"
                        ),
                        severity="warning",
                        left_detail=f"ORM nullable={col['nullable']}",
                        right_detail=f"DB nullable={db_col['nullable']}",
                        repair_hint=f"Align nullable for '{col['name']}'",
                    ))

        return violations

    # ------------------------------------------------------------------ 4
    def check_js_dom_html(
        self,
        js_refs: list[dict],
        html_ids: set[str],
    ) -> list[OverlapViolation]:
        """
        Every ``getElementById`` / ``querySelector`` call must have a
        matching HTML ``id``.

        *js_refs*: ``[{"element_id", "file", "line", "method"}, ...]``
        """
        violations: list[OverlapViolation] = []
        for ref in js_refs:
            eid = ref["element_id"]
            if eid not in html_ids:
                violations.append(OverlapViolation(
                    id=_vid("js_dom_html", eid, ref["file"]),
                    condition_id=f"oc:js_dom_html:{eid}",
                    kind=OverlapKind.JS_DOM_HTML,
                    message=(
                        f"JS {ref['method']}('{eid}') targets an element not "
                        f"found in HTML"
                    ),
                    severity="error",
                    left_detail=f"{ref['method']}('{eid}')",
                    right_detail=f"HTML ids: {sorted(html_ids)[:10]}{'...' if len(html_ids) > 10 else ''}",
                    repair_hint=f"Add id=\"{eid}\" to an HTML element",
                    file_path=ref.get("file", ""),
                    line_number=ref.get("line", 0),
                ))
        return violations

    # ------------------------------------------------------------------ 5
    def check_js_class_css(
        self,
        js_classes: set[str],
        css_classes: set[str],
    ) -> list[OverlapViolation]:
        """
        ``classList.add()`` target must have a corresponding CSS definition.
        """
        violations: list[OverlapViolation] = []
        missing = js_classes - css_classes
        for cls_name in sorted(missing):
            violations.append(OverlapViolation(
                id=_vid("js_class_css", cls_name),
                condition_id=f"oc:js_class_css:{cls_name}",
                kind=OverlapKind.JS_CLASS_CSS,
                message=f"JS adds class '{cls_name}' but no CSS definition found",
                severity="warning",
                left_detail=f"classList.add('{cls_name}')",
                right_detail=f"CSS defines: {sorted(css_classes)[:10]}{'...' if len(css_classes) > 10 else ''}",
                repair_hint=f"Add .{cls_name} rule to CSS",
            ))
        return violations

    # ------------------------------------------------------------------ 6
    def check_form_route(
        self,
        forms: list[dict],
        routes: list[dict],
    ) -> list[OverlapViolation]:
        """
        Form ``action`` must match a route; form ``fields`` must match
        route ``args``.

        *forms*: ``[{"action", "method", "fields", "file", "line"}, ...]``
        *routes*: same schema as for route_template checks.
        """
        violations: list[OverlapViolation] = []
        from jugeo.webapp.cross_language.reference_resolver import URLPatternMatcher
        matcher = URLPatternMatcher()

        for form in forms:
            action = form["action"]
            form_method = form.get("method", "POST").upper()

            matched_route: dict | None = None
            for route in routes:
                route_methods = {m.upper() for m in route.get("methods", ["GET"])}
                if form_method in route_methods and matcher.matches(route["pattern"], action):
                    matched_route = route
                    break

            if matched_route is None:
                violations.append(OverlapViolation(
                    id=_vid("form_route_no_match", action, form_method),
                    condition_id="oc:form_route:no_match",
                    kind=OverlapKind.FORM_ROUTE,
                    message=(
                        f"Form action='{action}' method={form_method} has "
                        f"no matching route"
                    ),
                    severity="error",
                    left_detail=f"form action={action} method={form_method}",
                    right_detail="no matching route",
                    repair_hint=f"Add a {form_method} route for '{action}'",
                    file_path=form.get("file", ""),
                    line_number=form.get("line", 0),
                ))
                continue

            # Check fields match route context_vars / args
            route_args = set(matched_route.get("context_vars", []))
            form_fields = set(form.get("fields", []))
            missing = form_fields - route_args
            for fld in sorted(missing):
                violations.append(OverlapViolation(
                    id=_vid("form_route_field", action, fld),
                    condition_id=f"oc:form_route:{action}",
                    kind=OverlapKind.FORM_ROUTE,
                    message=(
                        f"Form sends field '{fld}' to '{action}' but route "
                        f"'{matched_route['pattern']}' does not expect it"
                    ),
                    severity="warning",
                    left_detail=f"form fields={sorted(form_fields)}",
                    right_detail=f"route expects={sorted(route_args)}",
                    repair_hint=f"Handle field '{fld}' in route or remove from form",
                    file_path=form.get("file", ""),
                    line_number=form.get("line", 0),
                ))

        return violations

    # ------------------------------------------------------------------ 7
    def check_template_css(
        self,
        template_classes: set[str],
        css_classes: set[str],
    ) -> list[OverlapViolation]:
        """
        Template ``class="..."`` names must have styling in CSS.
        """
        violations: list[OverlapViolation] = []
        missing = template_classes - css_classes
        for cls_name in sorted(missing):
            violations.append(OverlapViolation(
                id=_vid("template_css", cls_name),
                condition_id=f"oc:template_css:{cls_name}",
                kind=OverlapKind.TEMPLATE_CSS,
                message=f"Template uses class '{cls_name}' but no CSS definition found",
                severity="warning",
                left_detail=f"class=\"{cls_name}\"",
                right_detail=f"CSS defines: {sorted(css_classes)[:10]}{'...' if len(css_classes) > 10 else ''}",
                repair_hint=f"Add .{cls_name} rule to CSS or remove class from template",
            ))
        return violations

    # ------------------------------------------------------------------ 8
    def check_auth_session(
        self,
        auth_decorators: list[dict],
        session_checks: list[dict],
    ) -> list[OverlapViolation]:
        """
        ``@login_required`` implies a ``session['user_id']`` (or similar)
        check exists somewhere in the handler.

        *auth_decorators*: ``[{"route", "file", "line"}, ...]``
        *session_checks*: ``[{"key", "file", "line"}, ...]``
        """
        violations: list[OverlapViolation] = []
        session_keys = {sc["key"] for sc in session_checks}

        # If there are auth decorators but no session checks at all, flag it
        if auth_decorators and not session_keys:
            for ad in auth_decorators:
                violations.append(OverlapViolation(
                    id=_vid("auth_session_no_check", ad["route"]),
                    condition_id=f"oc:auth_session:{ad['route']}",
                    kind=OverlapKind.AUTH_SESSION,
                    message=(
                        f"Route '{ad['route']}' has @login_required but no "
                        f"session checks found in the codebase"
                    ),
                    severity="warning",
                    left_detail=f"@login_required on {ad['route']}",
                    right_detail="no session['...'] checks",
                    repair_hint="Add session checks or verify auth middleware sets session keys",
                    file_path=ad.get("file", ""),
                    line_number=ad.get("line", 0),
                ))

        # Typical expected key
        expected_keys = {"user_id", "user", "current_user", "logged_in"}
        if auth_decorators and session_keys and not (session_keys & expected_keys):
            for ad in auth_decorators:
                violations.append(OverlapViolation(
                    id=_vid("auth_session_key", ad["route"]),
                    condition_id=f"oc:auth_session:{ad['route']}",
                    kind=OverlapKind.AUTH_SESSION,
                    message=(
                        f"Route '{ad['route']}' has @login_required but "
                        f"session keys {sorted(session_keys)} don't include "
                        f"standard user identification"
                    ),
                    severity="warning",
                    left_detail=f"@login_required on {ad['route']}",
                    right_detail=f"session keys: {sorted(session_keys)}",
                    repair_hint="Ensure session stores user identity (e.g. session['user_id'])",
                    file_path=ad.get("file", ""),
                    line_number=ad.get("line", 0),
                ))

        return violations

    # ------------------------------------------------------------------ 9
    def check_db_constraint_handler(
        self,
        constraints: list[dict],
        handlers: list[dict],
    ) -> list[OverlapViolation]:
        """
        NOT NULL columns must have non-null value writes in handlers.

        *constraints*: ``[{"table", "column", "constraint_type"}, ...]``
        *handlers*: ``[{"table", "column", "sets_null", "file", "line"}, ...]``
        """
        violations: list[OverlapViolation] = []

        # Build set of (table, column) with NOT NULL constraint
        not_null: set[tuple[str, str]] = set()
        unique_cols: set[tuple[str, str]] = set()
        for c in constraints:
            key = (c["table"].lower(), c["column"].lower())
            ct = c.get("constraint_type", "").upper()
            if ct == "NOT NULL":
                not_null.add(key)
            elif ct == "UNIQUE":
                unique_cols.add(key)

        for h in handlers:
            key = (h["table"].lower(), h["column"].lower())
            if key in not_null and h.get("sets_null", False):
                violations.append(OverlapViolation(
                    id=_vid("db_constraint_null", h["table"], h["column"]),
                    condition_id=f"oc:db_constraint:{h['table']}.{h['column']}",
                    kind=OverlapKind.DB_CONSTRAINT_HANDLER,
                    message=(
                        f"Handler sets '{h['table']}.{h['column']}' to NULL "
                        f"but column has NOT NULL constraint"
                    ),
                    severity="error",
                    left_detail=f"NOT NULL on {h['table']}.{h['column']}",
                    right_detail=f"handler sets null at {h['file']}:{h['line']}",
                    repair_hint=(
                        f"Provide a non-null value for '{h['column']}' or "
                        f"make the column nullable"
                    ),
                    file_path=h.get("file", ""),
                    line_number=h.get("line", 0),
                ))

        # Check NOT NULL columns without any handler writing to them
        handled_cols: set[tuple[str, str]] = {
            (h["table"].lower(), h["column"].lower()) for h in handlers
        }
        for tbl, col in sorted(not_null - handled_cols):
            violations.append(OverlapViolation(
                id=_vid("db_constraint_no_handler", tbl, col),
                condition_id=f"oc:db_constraint:{tbl}.{col}",
                kind=OverlapKind.DB_CONSTRAINT_HANDLER,
                message=(
                    f"NOT NULL column '{tbl}.{col}' has no handler "
                    f"writing to it"
                ),
                severity="warning",
                left_detail=f"NOT NULL on {tbl}.{col}",
                right_detail="no handler found",
                repair_hint=f"Add a handler that writes to '{tbl}.{col}'",
            ))

        return violations

    # ----------------------------------------------------------------- 10
    def check_error_handler_js(
        self,
        error_handlers: list[dict],
        js_catch: list[dict],
    ) -> list[OverlapViolation]:
        """
        Server error codes must have corresponding client-side handling.

        *error_handlers*: ``[{"status_code", "file"}, ...]``
        *js_catch*: ``[{"handles_status", "file"}, ...]``
        """
        violations: list[OverlapViolation] = []

        # Collect all status codes handled by JS
        js_handled: set[int] = set()
        for jc in js_catch:
            js_handled.update(jc.get("handles_status", []))

        for eh in error_handlers:
            code = eh["status_code"]
            if code not in js_handled:
                violations.append(OverlapViolation(
                    id=_vid("error_handler_js", str(code)),
                    condition_id=f"oc:error_handler_js:{code}",
                    kind=OverlapKind.ERROR_HANDLER_JS,
                    message=(
                        f"Server defines error handler for status {code} "
                        f"but no JS catch block handles it"
                    ),
                    severity="warning",
                    left_detail=f"errorhandler({code}) in {eh.get('file', '?')}",
                    right_detail=f"JS handles: {sorted(js_handled)}",
                    repair_hint=f"Add client-side handling for HTTP {code}",
                    file_path=eh.get("file", ""),
                ))

        return violations

    # -- aggregate -----------------------------------------------------------

    def check_all(self, project_data: dict) -> list[OverlapViolation]:
        """
        Run all 10 overlap checks using *project_data*.

        Expected keys::

            routes, templates, fetch_calls, models, tables,
            js_refs, html_ids, js_classes, css_classes, forms,
            template_classes, auth_decorators, session_checks,
            constraints, handlers, error_handlers, js_catch
        """
        violations: list[OverlapViolation] = []

        violations.extend(self.check_route_template(
            project_data.get("routes", []),
            project_data.get("templates", []),
        ))
        violations.extend(self.check_route_js_fetch(
            project_data.get("routes", []),
            project_data.get("fetch_calls", []),
        ))
        violations.extend(self.check_model_db_schema(
            project_data.get("models", []),
            project_data.get("tables", []),
        ))
        violations.extend(self.check_js_dom_html(
            project_data.get("js_refs", []),
            project_data.get("html_ids", set()),
        ))
        violations.extend(self.check_js_class_css(
            project_data.get("js_classes", set()),
            project_data.get("css_classes", set()),
        ))
        violations.extend(self.check_form_route(
            project_data.get("forms", []),
            project_data.get("routes", []),
        ))
        violations.extend(self.check_template_css(
            project_data.get("template_classes", set()),
            project_data.get("css_classes", set()),
        ))
        violations.extend(self.check_auth_session(
            project_data.get("auth_decorators", []),
            project_data.get("session_checks", []),
        ))
        violations.extend(self.check_db_constraint_handler(
            project_data.get("constraints", []),
            project_data.get("handlers", []),
        ))
        violations.extend(self.check_error_handler_js(
            project_data.get("error_handlers", []),
            project_data.get("js_catch", []),
        ))

        return violations
