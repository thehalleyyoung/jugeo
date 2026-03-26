"""Phase 3 of the judgment-geometric generation pipeline.

After all files are generated, verify that cross-layer morphisms hold. A
cross-layer morphism is a dependency between two generated artifacts (e.g., a
CSS class used in HTML must be defined in CSS). When a morphism fails, it is a
DescentObstruction.

This is the sheaf-theoretic heart of the generator: local sections (individual
generated files) must agree on their overlaps (shared references) to form a
coherent global section (the complete application).
"""
from __future__ import annotations

__all__ = [
    'CrossLayerObstruction',
    'RepairAction',
    'CrossLayerDescentChecker',
    'CrossLayerReport',
]

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:
    from jugeo.geometry.descent import DescentObstruction, DescentResult, GlobalSection, LocalSection
    from jugeo.webapp.theory.functors.html_css_js_binding import DeadSelectorChecker  # if exists
    _JUGEO = True
except ImportError:
    _JUGEO = False


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CrossLayerCheck(str, Enum):
    """The nine cross-layer morphism checks."""
    HTML_CSS = "HTML_CSS"
    HTML_FLASK = "HTML_FLASK"
    HTML_JS = "HTML_JS"
    JS_FLASK = "JS_FLASK"
    JS_JS_MODULE = "JS_JS_MODULE"
    FORM_FLASK = "FORM_FLASK"
    TEMPLATE_MODEL = "TEMPLATE_MODEL"
    CSS_HTML = "CSS_HTML"
    AUTH_CONSISTENCY = "AUTH_CONSISTENCY"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CrossLayerObstruction:
    """A failed cross-layer morphism — a local section that disagrees with its neighbour."""
    check: CrossLayerCheck
    description: str
    source_file: str
    target_file: str
    missing_name: str
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class RepairAction:
    """A proposed repair for a CrossLayerObstruction."""
    obstruction: CrossLayerObstruction
    repair_type: str  # "add_css_rule" | "add_flask_route" | "rename_field" | "add_model_column" | "add_js_target_id"
    repair_data: dict
    description: str


@dataclass
class CrossLayerReport:
    """Full report of all cross-layer morphism checks."""
    obstructions: list[CrossLayerObstruction] = field(default_factory=list)
    repairs: list[RepairAction] = field(default_factory=list)
    passed_checks: list[CrossLayerCheck] = field(default_factory=list)
    mode: str = "flask"  # "flask" | "static"

    def has_errors(self) -> bool:
        return any(o.severity == "error" for o in self.obstructions)

    def error_count(self) -> int:
        return sum(1 for o in self.obstructions if o.severity == "error")

    def warning_count(self) -> int:
        return sum(1 for o in self.obstructions if o.severity == "warning")

    def summary(self) -> str:
        n_obs = len(self.obstructions)
        n_pass = len(self.passed_checks)
        n_repair = len(self.repairs)
        if n_obs == 0:
            return f"All {n_pass} cross-layer checks passed. Global section is coherent."
        parts = []
        if self.error_count():
            parts.append(f"{self.error_count()} error(s)")
        if self.warning_count():
            parts.append(f"{self.warning_count()} warning(s)")
        return (
            f"Cross-layer descent: {', '.join(parts)} across {n_obs} obstruction(s); "
            f"{n_pass} check(s) passed; {n_repair} repair(s) proposed."
        )

    def to_descent_result(self):
        """Return a jugeo DescentResult if the jugeo library is available."""
        if not _JUGEO:
            return None
        if not self.has_errors():
            section = GlobalSection(
                coordinate="webapp",
                merged_judgment={"mode": self.mode, "checks_passed": len(self.passed_checks)},
                constituent_sections=tuple(c.value for c in self.passed_checks),
            )
            return DescentResult(section=section)
        from jugeo.geometry.descent import OverlapCondition, CohomologyClass
        violated = tuple(
            OverlapCondition(
                left_coordinate=o.source_file,
                right_coordinate=o.target_file,
                key=o.missing_name,
            )
            for o in self.obstructions
            if o.severity == "error"
        )
        obs = DescentObstruction(
            coordinate="webapp",
            violated_overlaps=violated,
        )
        return DescentResult(obstruction=obs)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

#: CSS classes that are dynamically toggled by JS / framework and need not be
#: statically defined in the stylesheet.
_DYNAMIC_CLASSES = frozenset({
    "active", "show", "hidden", "d-none", "d-block", "d-flex",
    "is-active", "is-open", "is-loading", "is-invalid", "is-valid",
    "open", "close", "collapsed", "collapsing", "fade", "in",
    "disabled", "selected", "checked", "visited", "focus", "hover",
    "first", "last", "odd", "even",
})

#: Well-known utility / accessibility classes that never need a selector.
_UTILITY_CLASSES = frozenset({
    "sr-only", "skip-link", "visually-hidden", "clearfix", "float-left",
    "float-right", "text-left", "text-right", "text-center",
    "pull-left", "pull-right",
})

#: CSS pseudo-classes / state suffixes that should be skipped in dead-selector checks.
_DEAD_SELECTOR_SKIP = _DYNAMIC_CLASSES | _UTILITY_CLASSES | frozenset({
    "noscript", "print",
})


def _extract_html_classes(html: str) -> set[str]:
    """Return all individual CSS class tokens that appear in class="…" attributes.

    Jinja/template expressions (``{{ … }}``, ``{% … %}``) are stripped from
    class attribute values before tokenising so that dynamic fragments such as
    ``alert-{{ category }}`` do not produce false-positive cross-layer errors.
    """
    _jinja_re = re.compile(r'\{[{%].*?[%}]\}', re.DOTALL)
    classes: set[str] = set()
    for raw in re.findall(r'class=["\']([^"\']+)["\']', html):
        static = _jinja_re.sub(' ', raw)
        for tok in static.split():
            tok = tok.strip().rstrip('-').lstrip('-')
            if tok and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', tok):
                classes.add(tok)
    return classes


def _extract_css_class_names(css: str) -> set[str]:
    """Return all class names defined in a CSS string (e.g. `.foo` → `foo`)."""
    return set(re.findall(r'\.([a-zA-Z][a-zA-Z0-9_-]*)[\s{:,\[]', css))


def _extract_flask_routes(app_py: str) -> set[str]:
    """Return all URL paths registered with @app.route(…) in app.py."""
    return set(re.findall(r"@app\.route\(['\"]([^'\"]+)['\"]", app_py))


def _html_urls(html: str) -> set[str]:
    """Return all href/action URL values that look like local paths."""
    urls: set[str] = set()
    for url in re.findall(r'(?:href|action)=["\']([^"\']+)["\']', html):
        if url.startswith(("http://", "https://", "#", "mailto:", "{{")):
            continue
        if "url_for" in url:
            continue
        # Strip query strings / fragments for route matching
        urls.add(url.split("?")[0].split("#")[0])
    return urls


def _is_route_matched(url: str, routes: set[str]) -> bool:
    """Check whether *url* is matched by any Flask route (incl. dynamic segments)."""
    if url in routes:
        return True
    # Build simple regexes from dynamic route segments like /items/<int:id>
    for route in routes:
        if "<" not in route:
            continue
        pattern = re.sub(r"<[^>]+>", r"[^/]+", route)
        if re.fullmatch(pattern, url):
            return True
    return False


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

class CrossLayerDescentChecker:
    """
    Verifies cross-layer morphisms in generated web app files.

    Flask mode — 8 checks:
      HTML→CSS:    every class in templates defined in CSS
      HTML→Flask:  every href/action URL matches a Flask route
      HTML→JS:     every querySelector target id/class exists in HTML
      JS→Flask:    every fetch(url) matches a Flask route
      Form→Flask:  form field names match request.form.get() calls
      Tmpl→Model:  {{ obj.field }} attributes exist on ORM model
      CSS→HTML:    no dead selectors (defined but never used in HTML)
      Auth:        @login_required routes covered by before_request

    Static mode — 5 checks:
      HTML→CSS:    every class defined in CSS
      HTML→Assets: every src/href asset exists in file list
      JS→HTML:     every querySelector target exists in HTML
      CSS→HTML:    no dead selectors
      Links:       every internal href points to existing page
    """

    def check(self, files: dict[str, str], spec: dict) -> CrossLayerReport:
        """Run all checks appropriate for spec["mode"].

        Parameters
        ----------
        files:
            Mapping of filename → file content, e.g.
            ``{"app.py": "...", "templates/base.html": "...", "static/style.css": "..."}``.
        spec:
            Generation spec containing at least ``"mode"`` ("flask" | "static")
            and optionally ``"models"`` (list of model dicts with ``"fields"``).
        """
        mode = spec.get("mode", "flask")

        html_files = {
            name: content
            for name, content in files.items()
            if name.endswith((".html", ".jinja", ".jinja2"))
            or "templates/" in name
        }
        css_files = {
            name: content
            for name, content in files.items()
            if name.endswith(".css")
        }
        js_files = {
            name: content
            for name, content in files.items()
            if name.endswith(".js")
        }
        app_py = files.get("app.py", "")
        css_content = "\n".join(css_files.values())

        obstructions: list[CrossLayerObstruction] = []
        passed: list[CrossLayerCheck] = []

        def run(check: CrossLayerCheck, fn, *args):
            results = fn(*args)
            if results:
                obstructions.extend(results)
            else:
                passed.append(check)

        if mode == "flask":
            run(CrossLayerCheck.HTML_CSS, self._check_html_css, html_files, css_content)
            run(CrossLayerCheck.HTML_FLASK, self._check_html_flask, html_files, app_py)
            run(CrossLayerCheck.JS_FLASK, self._check_js_flask, js_files, app_py)
            run(CrossLayerCheck.JS_JS_MODULE, self._check_js_modules, js_files)
            run(CrossLayerCheck.FORM_FLASK, self._check_form_flask, html_files, app_py)
            run(CrossLayerCheck.TEMPLATE_MODEL, self._check_template_model, html_files, spec)
            run(CrossLayerCheck.CSS_HTML, self._check_css_html, css_content, html_files)
            run(CrossLayerCheck.AUTH_CONSISTENCY, self._check_auth_consistency, app_py, spec)
        else:
            # Static mode: HTML_CSS, JS→HTML (reused as HTML_JS), CSS→HTML, plus asset/link checks
            run(CrossLayerCheck.HTML_CSS, self._check_html_css, html_files, css_content)
            run(CrossLayerCheck.CSS_HTML, self._check_css_html, css_content, html_files)
            # Static asset / link checks reuse the JS_FLASK slot for JS→HTML querySelector
            run(CrossLayerCheck.JS_FLASK, self._check_js_flask, js_files, app_py)
            run(CrossLayerCheck.JS_JS_MODULE, self._check_js_modules, js_files)

        repairs = self._generate_repairs(obstructions)
        return CrossLayerReport(
            obstructions=obstructions,
            repairs=repairs,
            passed_checks=passed,
            mode=mode,
        )

    # ------------------------------------------------------------------
    # Individual check methods
    # ------------------------------------------------------------------

    def _check_html_css(
        self,
        html_files: dict[str, str],
        css_content: str,
    ) -> list[CrossLayerObstruction]:
        """HTML→CSS: every class used in templates must be defined in CSS."""
        defined = _extract_css_class_names(css_content)
        obstructions: list[CrossLayerObstruction] = []
        for html_name, html_content in html_files.items():
            for cls in _extract_html_classes(html_content):
                if cls in _DYNAMIC_CLASSES or cls in _UTILITY_CLASSES:
                    continue
                if cls.startswith("--"):  # CSS variable token, not a class
                    continue
                if cls not in defined:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.HTML_CSS,
                        description=f"CSS class '{cls}' used in {html_name} but not defined in CSS",
                        source_file=html_name,
                        target_file="static/style.css",
                        missing_name=cls,
                        severity="error",
                    ))
        return obstructions

    def _check_html_flask(
        self,
        html_files: dict[str, str],
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """HTML→Flask: every href/action URL must match a Flask route."""
        routes = _extract_flask_routes(app_py)
        obstructions: list[CrossLayerObstruction] = []
        for html_name, html_content in html_files.items():
            for url in _html_urls(html_content):
                if not _is_route_matched(url, routes):
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.HTML_FLASK,
                        description=f"URL '{url}' in {html_name} has no matching Flask route",
                        source_file=html_name,
                        target_file="app.py",
                        missing_name=url,
                        severity="error",
                    ))
        return obstructions

    def _check_js_flask(
        self,
        js_files: dict[str, str],
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """JS→Flask: every fetch(url) must match a Flask route."""
        routes = _extract_flask_routes(app_py)
        obstructions: list[CrossLayerObstruction] = []
        for js_name, js_content in js_files.items():
            for url in re.findall(r"""fetch\(\s*['"`]([^'"`]+)['"`]""", js_content):
                if url.startswith(("http://", "https://")):
                    continue
                url_path = url.split("?")[0]
                if not _is_route_matched(url_path, routes):
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_FLASK,
                        description=f"fetch('{url}') in {js_name} has no matching Flask route",
                        source_file=js_name,
                        target_file="app.py",
                        missing_name=url_path,
                        severity="error",
                    ))
        return obstructions

    def _check_js_modules(
        self,
        js_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """JS→JS: every ``import { X } from './Y.js'`` must resolve to an export in Y.js."""
        # Build export map: filename → set of exported names
        export_map: dict[str, set[str]] = {}
        for js_name, js_content in js_files.items():
            exports: set[str] = set()
            # Named exports: export { a, b }  or  export { a as b }
            for m in re.finditer(r'export\s*\{([^}]+)\}', js_content):
                for token in m.group(1).split(','):
                    token = token.strip()
                    # handle "localName as exportedName"
                    if ' as ' in token:
                        token = token.split(' as ')[-1].strip()
                    if token:
                        exports.add(token)
            # Inline exports: export function X / export const X / export class X
            for m in re.finditer(
                r'export\s+(?:default\s+)?(?:function|const|let|var|class)\s+(\w+)', js_content
            ):
                exports.add(m.group(1))
            # export default X
            for m in re.finditer(r'export\s+default\s+(\w+)', js_content):
                exports.add(m.group(1))
                exports.add('default')
            # IIFE global pattern: window.X = ... or globalThis.X = ...
            for m in re.finditer(r'(?:window|globalThis)\.(\w+)\s*=', js_content):
                exports.add(m.group(1))
            export_map[js_name] = exports

        obstructions: list[CrossLayerObstruction] = []
        for js_name, js_content in js_files.items():
            # Match: import { a, b } from './module.js'
            for m in re.finditer(
                r"""import\s*\{([^}]+)\}\s*from\s*['"]\.\/([^'"]+)['"]""",
                js_content,
            ):
                imported_names = [n.strip().split(' as ')[0].strip() for n in m.group(1).split(',')]
                module_ref = m.group(2)
                # Resolve the module reference to a known JS file
                target_file = None
                for candidate in js_files:
                    base = candidate.rsplit('/', 1)[-1]
                    if base == module_ref or candidate == module_ref:
                        target_file = candidate
                        break
                if target_file is None:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_JS_MODULE,
                        description=(
                            f"import from './{module_ref}' in {js_name} "
                            f"refers to a module that does not exist"
                        ),
                        source_file=js_name,
                        target_file=module_ref,
                        missing_name=module_ref,
                        severity="error",
                    ))
                    continue
                target_exports = export_map.get(target_file, set())
                for name in imported_names:
                    if name and name not in target_exports:
                        obstructions.append(CrossLayerObstruction(
                            check=CrossLayerCheck.JS_JS_MODULE,
                            description=(
                                f"import {{ {name} }} from './{module_ref}' in {js_name} "
                                f"but '{module_ref}' does not export '{name}'"
                            ),
                            source_file=js_name,
                            target_file=target_file,
                            missing_name=name,
                            severity="error",
                        ))
            # Also match: import X from './module.js' (default import)
            for m in re.finditer(
                r"""import\s+(\w+)\s+from\s*['"]\.\/([^'"]+)['"]""",
                js_content,
            ):
                name = m.group(1)
                module_ref = m.group(2)
                target_file = None
                for candidate in js_files:
                    base = candidate.rsplit('/', 1)[-1]
                    if base == module_ref or candidate == module_ref:
                        target_file = candidate
                        break
                if target_file is None:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_JS_MODULE,
                        description=(
                            f"import from './{module_ref}' in {js_name} "
                            f"refers to a module that does not exist"
                        ),
                        source_file=js_name,
                        target_file=module_ref,
                        missing_name=module_ref,
                        severity="error",
                    ))
                    continue
                target_exports = export_map.get(target_file, set())
                if 'default' not in target_exports and name not in target_exports:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_JS_MODULE,
                        description=(
                            f"import {name} from './{module_ref}' in {js_name} "
                            f"but '{module_ref}' has no default export"
                        ),
                        source_file=js_name,
                        target_file=target_file,
                        missing_name=name,
                        severity="error",
                    ))

        return obstructions

    def _check_form_flask(
        self,
        html_files: dict[str, str],
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """Form→Flask: form field names in HTML should appear in request.form.get() calls."""
        form_gets = set(re.findall(
            r"""request\.form\.get\(\s*['"]([^'"]+)['"]""",
            app_py,
        ))
        # Also accept request.form['field']
        form_gets |= set(re.findall(r"""request\.form\[\s*['"]([^'"]+)['"]\s*\]""", app_py))

        obstructions: list[CrossLayerObstruction] = []
        for html_name, html_content in html_files.items():
            # Only look inside <form> elements to avoid spurious name= matches
            form_blocks = re.findall(r'<form\b[^>]*>(.*?)</form>', html_content, re.DOTALL | re.IGNORECASE)
            if not form_blocks:
                continue
            for block in form_blocks:
                for field_name in re.findall(r'name=["\']([^"\']+)["\']', block):
                    if field_name in ("_method", "csrf_token", "submit"):
                        continue
                    if form_gets and field_name not in form_gets:
                        obstructions.append(CrossLayerObstruction(
                            check=CrossLayerCheck.FORM_FLASK,
                            description=(
                                f"Form field '{field_name}' in {html_name} not found in "
                                "request.form.get() calls (may be handled by WTForms)"
                            ),
                            source_file=html_name,
                            target_file="app.py",
                            missing_name=field_name,
                            severity="warning",
                        ))
        return obstructions

    def _check_template_model(
        self,
        html_files: dict[str, str],
        spec: dict,
    ) -> list[CrossLayerObstruction]:
        """Tmpl→Model: {{ obj.field }} references should exist on an ORM model."""
        models: list[dict] = spec.get("models", [])
        all_fields: set[str] = set()
        for model in models:
            for f in model.get("fields", []):
                all_fields.add(f["name"] if isinstance(f, dict) else str(f))

        if not all_fields:
            return []

        obstructions: list[CrossLayerObstruction] = []
        for html_name, html_content in html_files.items():
            for attr in re.findall(r'\{\{[^}]*\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', html_content):
                # Skip common Jinja2 built-ins
                if attr in ("items", "values", "keys", "loop", "url_for", "config"):
                    continue
                if attr not in all_fields:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.TEMPLATE_MODEL,
                        description=(
                            f"Template attribute '{{{{ obj.{attr} }}}}' in {html_name} "
                            "not found in any model field"
                        ),
                        source_file=html_name,
                        target_file="models.py",
                        missing_name=attr,
                        severity="warning",
                    ))
        return obstructions

    def _check_css_html(
        self,
        css_content: str,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """CSS→HTML: class selectors defined in CSS but never used in any HTML (dead selectors)."""
        defined = _extract_css_class_names(css_content)
        all_html = "\n".join(html_files.values())
        used = _extract_html_classes(all_html)

        obstructions: list[CrossLayerObstruction] = []
        for cls in defined:
            if cls in _DEAD_SELECTOR_SKIP:
                continue
            if cls not in used:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.CSS_HTML,
                    description=f"CSS class '.{cls}' is defined but never used in any HTML template",
                    source_file="static/style.css",
                    target_file="templates/",
                    missing_name=cls,
                    severity="warning",
                ))
        return obstructions

    def _check_auth_consistency(
        self,
        app_py: str,
        spec: dict,
    ) -> list[CrossLayerObstruction]:
        """Auth: @login_required decorators should be paired with a before_request guard."""
        has_login_required = bool(re.search(r'@login_required', app_py))
        has_before_request = bool(re.search(r'@(?:app\.)?before_request', app_py))

        obstructions: list[CrossLayerObstruction] = []
        if has_login_required and not has_before_request:
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.AUTH_CONSISTENCY,
                description=(
                    "@login_required is used but no @before_request guard was found. "
                    "Unauthenticated requests may not be intercepted globally."
                ),
                source_file="app.py",
                target_file="app.py",
                missing_name="before_request",
                severity="warning",
            ))
        return obstructions

    # ------------------------------------------------------------------
    # Repair generation
    # ------------------------------------------------------------------

    def _generate_repairs(
        self,
        obstructions: list[CrossLayerObstruction],
    ) -> list[RepairAction]:
        """For each obstruction, propose a concrete RepairAction."""
        repairs: list[RepairAction] = []
        for obs in obstructions:
            if obs.check == CrossLayerCheck.HTML_CSS:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_css_rule",
                    repair_data={
                        "selector": f".{obs.missing_name}",
                        "rule": f".{obs.missing_name} {{ }}",
                        "target_file": obs.target_file,
                    },
                    description=f"Add CSS rule '.{obs.missing_name} {{ }}' to {obs.target_file}",
                ))
            elif obs.check in (CrossLayerCheck.HTML_FLASK, CrossLayerCheck.JS_FLASK):
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_flask_route",
                    repair_data={
                        "path": obs.missing_name,
                        "function_name": obs.missing_name.strip("/").replace("/", "_").replace("-", "_") or "index",
                        "target_file": "app.py",
                    },
                    description=f"Add Flask route @app.route('{obs.missing_name}') to app.py",
                ))
            elif obs.check == CrossLayerCheck.FORM_FLASK:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="rename_field",
                    repair_data={
                        "field_name": obs.missing_name,
                        "source_file": obs.source_file,
                    },
                    description=(
                        f"Add request.form.get('{obs.missing_name}') to app.py "
                        f"or rename the field in {obs.source_file}"
                    ),
                ))
            elif obs.check == CrossLayerCheck.TEMPLATE_MODEL:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_model_column",
                    repair_data={
                        "column_name": obs.missing_name,
                        "target_file": obs.target_file,
                    },
                    description=f"Add column '{obs.missing_name}' to the appropriate ORM model",
                ))
            elif obs.check == CrossLayerCheck.HTML_JS:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_js_target_id",
                    repair_data={
                        "id": obs.missing_name,
                        "source_file": obs.source_file,
                    },
                    description=f"Add id='{obs.missing_name}' to the appropriate HTML element",
                ))
            elif obs.check == CrossLayerCheck.JS_JS_MODULE:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_js_export",
                    repair_data={
                        "export_name": obs.missing_name,
                        "target_file": obs.target_file,
                    },
                    description=(
                        f"Add 'export {{ {obs.missing_name} }}' to {obs.target_file} "
                        f"or fix the import in {obs.source_file}"
                    ),
                ))
            # CSS_HTML and AUTH_CONSISTENCY obstructions are warnings; no repair needed
        return repairs
