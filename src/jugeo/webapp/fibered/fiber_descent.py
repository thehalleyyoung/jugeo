"""Descent engine for the web-application fibered category.

Descent in this context means checking that locally-defined data (within
each language fiber) glues together consistently across fiber boundaries
to form a globally well-defined web application.

The engine performs three levels of verification:

1. **Per-fiber descent** – internal consistency of each language fiber.
2. **Boundary descent** – consistency of the cartesian lifts connecting
   pairs of fibers.
3. **Global descent** – aggregation into a single ``FiberedSiteResult``.
"""
from __future__ import annotations

from .models import (
    CartesianLift,
    FiberDescentResult,
    FiberedSiteResult,
    LanguageFiber,
)


# ---------------------------------------------------------------------------
# Cross-fiber consistency conditions
# ---------------------------------------------------------------------------

_CROSS_FIBER_CONDITIONS: dict[tuple[str, str], list[str]] = {
    ("python", "template"): [
        "context_completeness",
        "url_for_resolution",
        "macro_signature_match",
    ],
    ("python", "sql"): [
        "orm_model_table_match",
        "column_type_agreement",
        "migration_completeness",
    ],
    ("python", "javascript"): [
        "api_endpoint_agreement",
        "json_schema_compatibility",
        "csrf_token_presence",
    ],
    ("html", "css"): [
        "class_selector_existence",
        "id_selector_uniqueness",
        "media_query_breakpoint_consistency",
    ],
    ("javascript", "html"): [
        "dom_id_existence",
        "event_handler_binding",
        "form_action_target",
    ],
    ("template", "html"): [
        "block_well_formedness",
        "include_path_existence",
        "macro_output_validity",
    ],
}


# ---------------------------------------------------------------------------
# FiberDescentEngine
# ---------------------------------------------------------------------------

class FiberDescentEngine:
    """Check descent (gluing) conditions across the web-app fibered category."""

    # Dispatch table: fiber name → checker method name.
    _FIBER_CHECKERS: dict[str, str] = {
        "python": "_python_fiber_descent",
        "javascript": "_js_fiber_descent",
        "css": "_css_fiber_descent",
        "html": "_html_fiber_descent",
        "sql": "_sql_fiber_descent",
        "template": "_template_fiber_descent",
    }

    # -- public API ---------------------------------------------------------

    def check_per_fiber_descent(
        self,
        site_data: dict,
    ) -> dict[str, FiberDescentResult]:
        """Run per-fiber descent checks and return results by fiber name.

        *site_data* should contain per-fiber data under keys ``"python"``,
        ``"javascript"``, ``"css"``, ``"html"``, ``"sql"``, ``"template"``.
        Missing fibers are silently skipped.
        """
        results: dict[str, FiberDescentResult] = {}
        for fiber_name, method_name in self._FIBER_CHECKERS.items():
            fiber_data = site_data.get(fiber_name)
            if fiber_data is None:
                continue
            checker = getattr(self, method_name)
            results[fiber_name] = checker(fiber_data)
        return results

    def check_boundary_descent(
        self,
        site_data: dict,
    ) -> list[dict]:
        """Check consistency across fiber boundaries.

        Examines all inter-fiber morphisms in ``site_data["morphisms"]``
        and validates that source / target coordinates exist in their
        respective fiber data.

        Returns a list of obstruction dicts, each with keys *boundary*,
        *obstruction*, *severity*, and *repair_hint*.
        """
        morphisms = site_data.get("morphisms", [])
        coordinates = site_data.get("coordinates", [])
        coord_ids = {
            (c["coordinate_id"] if isinstance(c, dict) else c.coordinate_id)
            for c in coordinates
        }

        obstructions: list[dict] = []

        for m in morphisms:
            mid = m["morphism_id"] if isinstance(m, dict) else m.morphism_id
            sf = m["source_fiber"] if isinstance(m, dict) else m.source_fiber
            tf = m["target_fiber"] if isinstance(m, dict) else m.target_fiber
            sc = m["source_coord"] if isinstance(m, dict) else m.source_coord
            tc = m["target_coord"] if isinstance(m, dict) else m.target_coord

            # Skip internal morphisms.
            if sf == tf:
                continue

            boundary = f"{sf}->{tf}"

            if sc not in coord_ids:
                obstructions.append({
                    "boundary": boundary,
                    "obstruction": (
                        f"Source coordinate {sc!r} of lift {mid!r} "
                        f"not found in site data"
                    ),
                    "severity": "error",
                    "repair_hint": (
                        f"Add coordinate {sc!r} to the {sf!r} fiber "
                        f"or remove the lift."
                    ),
                })

            if tc not in coord_ids:
                obstructions.append({
                    "boundary": boundary,
                    "obstruction": (
                        f"Target coordinate {tc!r} of lift {mid!r} "
                        f"not found in site data"
                    ),
                    "severity": "error",
                    "repair_hint": (
                        f"Add coordinate {tc!r} to the {tf!r} fiber "
                        f"or remove the lift."
                    ),
                })

            # Cross-fiber conditions.
            conditions = self._cross_fiber_conditions(sf, tf, morphisms)
            for cond in conditions:
                if not self._condition_satisfied(
                    cond, sf, tf, site_data,
                ):
                    obstructions.append({
                        "boundary": boundary,
                        "obstruction": (
                            f"Cross-fiber condition {cond!r} not satisfied "
                            f"for lift {mid!r}"
                        ),
                        "severity": "warning",
                        "repair_hint": (
                            f"Verify that {cond!r} holds between "
                            f"the {sf!r} and {tf!r} fibers."
                        ),
                    })

        return obstructions

    def check_global_descent(
        self,
        per_fiber: dict[str, FiberDescentResult],
        boundary: list[dict],
    ) -> FiberedSiteResult:
        """Combine per-fiber and boundary results into a global result.

        *overall_passed* is ``True`` only when every fiber passed **and**
        there are no critical (severity == "error") boundary obstructions.
        """
        all_fibers_passed = all(r.passed for r in per_fiber.values())
        critical_boundary = [
            o for o in boundary if o.get("severity") == "error"
        ]

        total = sum(
            len(r.local_obstructions) + len(r.boundary_obstructions)
            for r in per_fiber.values()
        ) + len(boundary)

        return FiberedSiteResult(
            fibers={},
            lifts=[],
            global_descent=boundary,
            per_fiber_descent={
                name: result.to_dict()
                for name, result in per_fiber.items()
            },
            overall_passed=all_fibers_passed and len(critical_boundary) == 0,
            total_obstructions=total,
        )

    # -- per-fiber checkers -------------------------------------------------

    def _python_fiber_descent(self, py_data: dict) -> FiberDescentResult:
        """Check internal consistency of the Python fiber.

        Expected keys in *py_data*: ``routes`` (list), ``models`` (list),
        ``imports`` (list).
        """
        obstructions: list[dict] = []

        routes = py_data.get("routes", [])
        models = py_data.get("models", [])
        imports = py_data.get("imports", [])

        # Each route should reference a handler function.
        for route in routes:
            handler = (
                route.get("handler")
                if isinstance(route, dict)
                else getattr(route, "handler", None)
            )
            if not handler:
                obstructions.append({
                    "id": f"py_route_no_handler_{_route_path(route)}",
                    "description": (
                        f"Route {_route_path(route)!r} has no handler function"
                    ),
                    "severity": "error",
                })

        # Each model should have at least one field.
        for model in models:
            fields = (
                model.get("fields", [])
                if isinstance(model, dict)
                else getattr(model, "fields", [])
            )
            model_name = (
                model.get("name", "<unnamed>")
                if isinstance(model, dict)
                else getattr(model, "name", "<unnamed>")
            )
            if not fields:
                obstructions.append({
                    "id": f"py_model_no_fields_{model_name}",
                    "description": (
                        f"Model {model_name!r} declares no fields"
                    ),
                    "severity": "warning",
                })

        # Check for circular or missing imports.
        import_names = {
            (i.get("name") if isinstance(i, dict) else getattr(i, "name", ""))
            for i in imports
        }
        for imp in imports:
            target = (
                imp.get("target")
                if isinstance(imp, dict)
                else getattr(imp, "target", None)
            )
            if target and target not in import_names:
                obstructions.append({
                    "id": f"py_import_missing_{target}",
                    "description": (
                        f"Import target {target!r} not found in site"
                    ),
                    "severity": "warning",
                })

        passed = not any(o["severity"] == "error" for o in obstructions)
        coverage = 1.0 if not routes else (
            sum(
                1 for r in routes
                if (r.get("handler") if isinstance(r, dict)
                    else getattr(r, "handler", None))
            ) / len(routes)
        )

        return FiberDescentResult(
            fiber_name="python",
            local_obstructions=obstructions,
            passed=passed,
            coverage_score=coverage,
        )

    def _js_fiber_descent(self, js_data: dict) -> FiberDescentResult:
        """Check internal consistency of the JavaScript fiber.

        Expected keys: ``functions`` (list), ``event_handlers`` (list),
        ``fetch_calls`` (list).  Optional: ``html_ids`` (list).
        """
        obstructions: list[dict] = []

        functions = js_data.get("functions", [])
        event_handlers = js_data.get("event_handlers", [])
        fetch_calls = js_data.get("fetch_calls", [])
        html_ids = set(js_data.get("html_ids", []))
        endpoints = set(js_data.get("endpoints", []))

        func_names = {
            (f.get("name") if isinstance(f, dict) else getattr(f, "name", ""))
            for f in functions
        }

        # Event handlers should reference existing DOM ids when available.
        for handler in event_handlers:
            target_id = (
                handler.get("target_id")
                if isinstance(handler, dict)
                else getattr(handler, "target_id", None)
            )
            if target_id and html_ids and target_id not in html_ids:
                obstructions.append({
                    "id": f"js_handler_bad_target_{target_id}",
                    "description": (
                        f"Event handler targets DOM id {target_id!r} "
                        f"which does not exist in provided HTML ids"
                    ),
                    "severity": "error",
                })

            callback = (
                handler.get("callback")
                if isinstance(handler, dict)
                else getattr(handler, "callback", None)
            )
            if callback and callback not in func_names:
                obstructions.append({
                    "id": f"js_handler_bad_callback_{callback}",
                    "description": (
                        f"Event handler callback {callback!r} is not "
                        f"a known JS function"
                    ),
                    "severity": "warning",
                })

        # Fetch calls should target valid endpoints when available.
        for call in fetch_calls:
            url = (
                call.get("url")
                if isinstance(call, dict)
                else getattr(call, "url", None)
            )
            if url and endpoints and url not in endpoints:
                obstructions.append({
                    "id": f"js_fetch_bad_endpoint_{url}",
                    "description": (
                        f"fetch() call targets {url!r} which is not "
                        f"a known API endpoint"
                    ),
                    "severity": "warning",
                })

        passed = not any(o["severity"] == "error" for o in obstructions)
        return FiberDescentResult(
            fiber_name="javascript",
            local_obstructions=obstructions,
            passed=passed,
            coverage_score=1.0 if passed else 0.5,
        )

    def _css_fiber_descent(self, css_data: dict) -> FiberDescentResult:
        """Check internal consistency of the CSS fiber.

        Expected keys: ``rules`` (list of selector strings),
        ``referenced_classes`` (list of class names used in HTML/templates).
        """
        obstructions: list[dict] = []

        rules = css_data.get("rules", [])
        referenced_classes = set(css_data.get("referenced_classes", []))

        # Build set of selectors defined in CSS.
        defined_selectors: set[str] = set()
        for rule in rules:
            selector = rule if isinstance(rule, str) else (
                rule.get("selector", "")
                if isinstance(rule, dict)
                else getattr(rule, "selector", "")
            )
            # Extract class names from composite selectors.
            for token in selector.replace(",", " ").split():
                if token.startswith("."):
                    defined_selectors.add(token.lstrip("."))
                else:
                    defined_selectors.add(token)

        # Every referenced class should have a corresponding CSS rule.
        for cls_name in sorted(referenced_classes):
            if cls_name not in defined_selectors:
                obstructions.append({
                    "id": f"css_missing_rule_{cls_name}",
                    "description": (
                        f"Class {cls_name!r} is referenced in HTML but "
                        f"has no corresponding CSS rule"
                    ),
                    "severity": "warning",
                })

        total = len(referenced_classes) or 1
        matched = total - len(obstructions)
        coverage = max(matched / total, 0.0)

        return FiberDescentResult(
            fiber_name="css",
            local_obstructions=obstructions,
            passed=len(obstructions) == 0,
            coverage_score=coverage,
        )

    def _html_fiber_descent(self, html_data: dict) -> FiberDescentResult:
        """Check internal consistency of the HTML fiber.

        Expected keys: ``elements`` (list), ``ids`` (list),
        ``forms`` (list).
        """
        obstructions: list[dict] = []

        elements = html_data.get("elements", [])
        ids = html_data.get("ids", [])
        forms = html_data.get("forms", [])

        # Check id uniqueness.
        seen_ids: set[str] = set()
        for eid in ids:
            if eid in seen_ids:
                obstructions.append({
                    "id": f"html_duplicate_id_{eid}",
                    "description": (
                        f"Duplicate HTML id {eid!r}"
                    ),
                    "severity": "error",
                })
            seen_ids.add(eid)

        # Forms should have an action.
        for form in forms:
            action = (
                form.get("action")
                if isinstance(form, dict)
                else getattr(form, "action", None)
            )
            if not action:
                form_id = (
                    form.get("id", "<anonymous>")
                    if isinstance(form, dict)
                    else getattr(form, "id", "<anonymous>")
                )
                obstructions.append({
                    "id": f"html_form_no_action_{form_id}",
                    "description": (
                        f"Form {form_id!r} has no action attribute"
                    ),
                    "severity": "warning",
                })

        passed = not any(o["severity"] == "error" for o in obstructions)
        return FiberDescentResult(
            fiber_name="html",
            local_obstructions=obstructions,
            passed=passed,
            coverage_score=1.0 if passed else 0.5,
        )

    def _sql_fiber_descent(self, sql_data: dict) -> FiberDescentResult:
        """Check internal consistency of the SQL fiber.

        Expected keys: ``tables`` (list), ``columns`` (list),
        ``constraints`` (list).
        """
        obstructions: list[dict] = []

        tables = sql_data.get("tables", [])
        columns = sql_data.get("columns", [])
        constraints = sql_data.get("constraints", [])

        table_names = {
            (t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
            for t in tables
        }

        # Columns should reference existing tables.
        for col in columns:
            table = (
                col.get("table")
                if isinstance(col, dict)
                else getattr(col, "table", None)
            )
            if table and table not in table_names:
                col_name = (
                    col.get("name", "<unnamed>")
                    if isinstance(col, dict)
                    else getattr(col, "name", "<unnamed>")
                )
                obstructions.append({
                    "id": f"sql_col_orphan_{col_name}",
                    "description": (
                        f"Column {col_name!r} references table {table!r} "
                        f"which does not exist"
                    ),
                    "severity": "error",
                })

        # Foreign key constraints should reference existing tables.
        for constraint in constraints:
            ref_table = (
                constraint.get("references")
                if isinstance(constraint, dict)
                else getattr(constraint, "references", None)
            )
            if ref_table and ref_table not in table_names:
                cname = (
                    constraint.get("name", "<unnamed>")
                    if isinstance(constraint, dict)
                    else getattr(constraint, "name", "<unnamed>")
                )
                obstructions.append({
                    "id": f"sql_fk_broken_{cname}",
                    "description": (
                        f"Constraint {cname!r} references table "
                        f"{ref_table!r} which does not exist"
                    ),
                    "severity": "error",
                })

        passed = not any(o["severity"] == "error" for o in obstructions)
        return FiberDescentResult(
            fiber_name="sql",
            local_obstructions=obstructions,
            passed=passed,
            coverage_score=1.0 if passed else 0.0,
        )

    def _template_fiber_descent(
        self,
        template_data: dict,
    ) -> FiberDescentResult:
        """Check internal consistency of the template fiber.

        Expected keys: ``variables`` (list), ``provided_context`` (list),
        ``blocks`` (list).
        """
        obstructions: list[dict] = []

        variables = template_data.get("variables", [])
        provided_context = set(template_data.get("provided_context", []))
        blocks = template_data.get("blocks", [])

        # Every template variable should be present in the provided context.
        for var in variables:
            var_name = var if isinstance(var, str) else (
                var.get("name", "")
                if isinstance(var, dict)
                else getattr(var, "name", "")
            )
            if var_name and var_name not in provided_context:
                obstructions.append({
                    "id": f"tpl_var_missing_{var_name}",
                    "description": (
                        f"Template variable {var_name!r} is not provided "
                        f"in the render context"
                    ),
                    "severity": "error",
                })

        # Blocks extending a parent should reference known block names.
        block_names = {
            (b.get("name") if isinstance(b, dict) else getattr(b, "name", ""))
            for b in blocks
        }
        for block in blocks:
            extends = (
                block.get("extends")
                if isinstance(block, dict)
                else getattr(block, "extends", None)
            )
            if extends and extends not in block_names:
                bname = (
                    block.get("name", "<unnamed>")
                    if isinstance(block, dict)
                    else getattr(block, "name", "<unnamed>")
                )
                obstructions.append({
                    "id": f"tpl_block_missing_parent_{bname}",
                    "description": (
                        f"Block {bname!r} extends {extends!r} which "
                        f"is not defined"
                    ),
                    "severity": "warning",
                })

        total_vars = len(variables) or 1
        missing = sum(
            1 for o in obstructions
            if o["id"].startswith("tpl_var_missing_")
        )
        coverage = max((total_vars - missing) / total_vars, 0.0)

        passed = not any(o["severity"] == "error" for o in obstructions)
        return FiberDescentResult(
            fiber_name="template",
            local_obstructions=obstructions,
            passed=passed,
            coverage_score=coverage,
        )

    # -- cross-fiber helpers ------------------------------------------------

    def _cross_fiber_conditions(
        self,
        fiber1: str,
        fiber2: str,
        boundary_morphisms: list,
    ) -> list[str]:
        """Return expected consistency conditions for a pair of fibers."""
        key = (fiber1, fiber2)
        if key in _CROSS_FIBER_CONDITIONS:
            return list(_CROSS_FIBER_CONDITIONS[key])
        # Try the reverse direction.
        rkey = (fiber2, fiber1)
        if rkey in _CROSS_FIBER_CONDITIONS:
            return list(_CROSS_FIBER_CONDITIONS[rkey])
        return []

    def _condition_satisfied(
        self,
        condition: str,
        source_fiber: str,
        target_fiber: str,
        site_data: dict,
    ) -> bool:
        """Check whether a named cross-fiber condition holds.

        For now every condition is assumed to be satisfied unless explicit
        negative evidence is present in ``site_data["violations"]``.
        """
        violations = site_data.get("violations", [])
        for v in violations:
            if isinstance(v, dict):
                if (
                    v.get("condition") == condition
                    and v.get("source_fiber") == source_fiber
                    and v.get("target_fiber") == target_fiber
                ):
                    return False
            else:
                if (
                    getattr(v, "condition", None) == condition
                    and getattr(v, "source_fiber", None) == source_fiber
                    and getattr(v, "target_fiber", None) == target_fiber
                ):
                    return False
        return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _route_path(route: dict | object) -> str:
    """Extract a printable route path."""
    if isinstance(route, dict):
        return route.get("path", route.get("url", "<unknown>"))
    return getattr(route, "path", getattr(route, "url", "<unknown>"))
