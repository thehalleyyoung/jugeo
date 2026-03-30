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

import ast
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
    """The cross-layer morphism checks (10 structural + 1 behavioral)."""
    HTML_CSS = "HTML_CSS"
    HTML_FLASK = "HTML_FLASK"
    HTML_JS = "HTML_JS"
    JS_FLASK = "JS_FLASK"
    JS_JS_MODULE = "JS_JS_MODULE"
    FORM_FLASK = "FORM_FLASK"
    TEMPLATE_MODEL = "TEMPLATE_MODEL"
    CSS_HTML = "CSS_HTML"
    AUTH_CONSISTENCY = "AUTH_CONSISTENCY"
    ARTIFACT_REACHABILITY = "ARTIFACT_REACHABILITY"
    BEHAVIORAL_ERROR_FREEDOM = "BEHAVIORAL_ERROR_FREEDOM"
    APP_MODEL_IMPORT = "APP_MODEL_IMPORT"
    TEMPLATE_CONTEXT = "TEMPLATE_CONTEXT"
    ROUTE_PARAM_ACCESS = "ROUTE_PARAM_ACCESS"
    ENDPOINT_EXISTS = "ENDPOINT_EXISTS"
    MODEL_DUPLICATE_COLUMN = "MODEL_DUPLICATE_COLUMN"
    TEMPLATE_INHERITANCE = "TEMPLATE_INHERITANCE"
    TEMPLATE_INCLUDE = "TEMPLATE_INCLUDE"
    STATIC_FILE_EXISTS = "STATIC_FILE_EXISTS"
    IMPORT_PACKAGE = "IMPORT_PACKAGE"
    MODEL_RELATIONSHIP_CONSISTENCY = "MODEL_RELATIONSHIP_CONSISTENCY"
    CONFIG_ORDERING = "CONFIG_ORDERING"
    FORM_METHOD_ROUTE = "FORM_METHOD_ROUTE"
    URL_FOR_PARAMS = "URL_FOR_PARAMS"
    TEMPLATE_SET_VARS = "TEMPLATE_SET_VARS"
    PYTHON_SYNTAX = "PYTHON_SYNTAX"
    RESPONSE_TYPE_CONSISTENCY = "RESPONSE_TYPE_CONSISTENCY"
    REDIRECT_AFTER_POST = "REDIRECT_AFTER_POST"
    CSS_CUSTOM_PROPERTY = "CSS_CUSTOM_PROPERTY"
    JINJA_IMPORT = "JINJA_IMPORT"
    JS_REQUIRE = "JS_REQUIRE"
    PROLOG_MODULE = "PROLOG_MODULE"
    PROLOG_PREDICATE = "PROLOG_PREDICATE"
    PROLOG_PYTHON_BRIDGE = "PROLOG_PYTHON_BRIDGE"
    HTML_ASSET_INTEGRITY = "HTML_ASSET_INTEGRITY"
    SEED_DATA_ABSENT = "SEED_DATA_ABSENT"
    BLOCK_NAME_MISMATCH = "BLOCK_NAME_MISMATCH"
    NAVIGATION_REACHABILITY = "NAVIGATION_REACHABILITY"


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

        PRIMARY PATH: uses webapp_descent module to parse all files into
        LocalSections, build structural OverlapConditions, and run
        DescentEngine for genuine judgment-geometric descent.

        FALLBACK: if webapp_descent is not available, falls through to
        the legacy per-check regex methods.

        Parameters
        ----------
        files:
            Mapping of filename → file content, e.g.
            ``{"app.py": "...", "templates/base.html": "...", "static/style.css": "..."}``.
        spec:
            Generation spec containing at least ``"mode"`` ("flask" | "static")
            and optionally ``"models"`` (list of model dicts with ``"fields"``).
        """
        # ── PRIMARY: entity-level judgment-geometric descent ─────────────
        # Uses webapp_descent.extract_site() to build the site category
        # where every named entity is a Coordinate and every cross-reference
        # is a Morphism. Descent = every morphism target exists.
        # Only used when called from the pipeline (many files); individual
        # _check_* tests bypass this via direct method calls.
        _structural_obs: list[CrossLayerObstruction] = []
        try:
            from jugeo.webapp.cli.webapp_descent import extract_site  # lazy import
            if len(files) < 3:
                raise ImportError("skip entity descent for small file sets")
            site = extract_site(files, spec)
            ok, dangling = site.check_descent()
            if not ok:
                # Convert dangling morphisms to CrossLayerObstructions
                # but do NOT return early — fall through so legacy checks
                # (navigation reachability, block names, etc.) also run.
                for ref in dangling:
                    _structural_obs.append(CrossLayerObstruction(
                        check=CrossLayerCheck.TEMPLATE_CONTEXT,
                        description=ref.label,
                        source_file=ref.source.split(":")[-1] if ":" in ref.source else ref.source,
                        target_file=ref.target.split(":")[-1] if ":" in ref.target else ref.target,
                        missing_name=ref.target.split(":")[-1] if ":" in ref.target else ref.target,
                        severity="error",
                    ))
            # Fall through to legacy checks even on success — they catch
            # additional warnings (dead CSS, PRG pattern, etc.) that the
            # entity-level descent doesn't model as morphisms.
        except Exception:
            pass  # Fall through to legacy regex checks

        # ── LEGACY: per-check regex methods (also catches warnings) ──────
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
            run(CrossLayerCheck.JS_JS_MODULE, self._check_js_nonmodule_export, html_files, js_files)
            run(CrossLayerCheck.FORM_FLASK, self._check_form_flask, html_files, app_py)
            run(CrossLayerCheck.TEMPLATE_MODEL, self._check_template_model, html_files, spec)
            run(CrossLayerCheck.CSS_HTML, self._check_css_html, css_content, html_files)
            run(CrossLayerCheck.AUTH_CONSISTENCY, self._check_auth_consistency, app_py, spec)
            run(CrossLayerCheck.ARTIFACT_REACHABILITY, self._check_artifact_reachability, files, mode)
            run(CrossLayerCheck.BEHAVIORAL_ERROR_FREEDOM, self._check_behavioral, files)
            run(CrossLayerCheck.APP_MODEL_IMPORT, self._check_app_model_imports, app_py, files, spec)
            run(CrossLayerCheck.TEMPLATE_CONTEXT, self._check_template_context, app_py, html_files)
            run(CrossLayerCheck.ROUTE_PARAM_ACCESS, self._check_route_param_access, app_py)
            run(CrossLayerCheck.ENDPOINT_EXISTS, self._check_endpoint_exists, app_py, html_files)
            run(CrossLayerCheck.MODEL_DUPLICATE_COLUMN, self._check_duplicate_columns, files)
            run(CrossLayerCheck.TEMPLATE_INHERITANCE, self._check_template_inheritance, html_files)
            run(CrossLayerCheck.BLOCK_NAME_MISMATCH, self._check_block_name_mismatch, html_files)
            run(CrossLayerCheck.NAVIGATION_REACHABILITY, self._check_navigation_reachability, app_py, html_files, spec)
            run(CrossLayerCheck.TEMPLATE_INCLUDE, self._check_template_include, html_files)
            run(CrossLayerCheck.STATIC_FILE_EXISTS, self._check_static_file_exists, html_files, files)
            run(CrossLayerCheck.IMPORT_PACKAGE, self._check_import_package, files)
            run(CrossLayerCheck.MODEL_RELATIONSHIP_CONSISTENCY, self._check_model_relationship_consistency, files)
            run(CrossLayerCheck.CONFIG_ORDERING, self._check_config_ordering, app_py)
            run(CrossLayerCheck.FORM_METHOD_ROUTE, self._check_form_method_route, html_files, app_py)
            run(CrossLayerCheck.URL_FOR_PARAMS, self._check_url_for_params, app_py, html_files)
            run(CrossLayerCheck.TEMPLATE_SET_VARS, self._check_template_set_vars, app_py, html_files)
            run(CrossLayerCheck.PYTHON_SYNTAX, self._check_python_syntax, files)
            run(CrossLayerCheck.RESPONSE_TYPE_CONSISTENCY, self._check_response_type_consistency, app_py)
            run(CrossLayerCheck.REDIRECT_AFTER_POST, self._check_redirect_after_post, app_py)
            run(CrossLayerCheck.CSS_CUSTOM_PROPERTY, self._check_css_custom_property, css_content)
            run(CrossLayerCheck.JINJA_IMPORT, self._check_jinja_import, html_files)
            run(CrossLayerCheck.JS_REQUIRE, self._check_js_require, js_files)
            run(CrossLayerCheck.PROLOG_MODULE, self._check_prolog_module, files)
            run(CrossLayerCheck.PROLOG_PREDICATE, self._check_prolog_predicate, files)
            run(CrossLayerCheck.PROLOG_PYTHON_BRIDGE, self._check_prolog_python_bridge, files)
            run(CrossLayerCheck.HTML_ASSET_INTEGRITY, self._check_html_asset_integrity, html_files, js_files)
            run(CrossLayerCheck.SEED_DATA_ABSENT, self._check_seed_data_present, app_py, spec)
        else:
            # Static mode: HTML_CSS, JS→HTML (reused as HTML_JS), CSS→HTML, plus asset/link checks
            run(CrossLayerCheck.HTML_CSS, self._check_html_css, html_files, css_content)
            run(CrossLayerCheck.CSS_HTML, self._check_css_html, css_content, html_files)
            # Static asset / link checks reuse the JS_FLASK slot for JS→HTML querySelector
            run(CrossLayerCheck.JS_FLASK, self._check_js_flask, js_files, app_py)
            run(CrossLayerCheck.JS_JS_MODULE, self._check_js_modules, js_files)
            run(CrossLayerCheck.ARTIFACT_REACHABILITY, self._check_artifact_reachability, files, mode)

        all_obstructions = _structural_obs + obstructions
        repairs = self._generate_repairs(all_obstructions)
        return CrossLayerReport(
            obstructions=all_obstructions,
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

    def _check_js_nonmodule_export(
        self,
        html_files: dict[str, str],
        js_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """HTML→JS: a <script src="X"> without type="module" must not use ES module export.

        A top-level ``export`` statement in a script loaded as a classic (non-module)
        script causes ``SyntaxError: Unexpected token 'export'`` in the browser.
        Either add ``type="module"`` to the <script> tag or rewrite the file to expose
        its API as a global (IIFE / window.X pattern) without ``export``.
        """
        # Collect JS files that have at least one top-level export statement.
        _export_re = re.compile(
            r'^\s*export\s+(?:default\s+)?(?:function|class|const|let|var|\{)',
            re.MULTILINE,
        )
        files_with_exports: set[str] = set()
        for js_name, js_content in js_files.items():
            if _export_re.search(js_content):
                base = js_name.rsplit('/', 1)[-1]
                files_with_exports.add(base)
                files_with_exports.add(js_name)

        if not files_with_exports:
            return []

        # Find <script src="..."> tags in HTML that lack type="module".
        _script_re = re.compile(
            r'<script\b([^>]*)>',
            re.IGNORECASE,
        )
        obstructions: list[CrossLayerObstruction] = []
        for html_name, html_content in html_files.items():
            for m in _script_re.finditer(html_content):
                attrs = m.group(1)
                is_module = bool(re.search(r'''type\s*=\s*['"]module['"]''', attrs, re.IGNORECASE))
                if is_module:
                    continue
                src_match = re.search(r'''src\s*=\s*['"]([^'"]+)['"]''', attrs, re.IGNORECASE)
                if not src_match:
                    continue
                src = src_match.group(1)
                # Resolve filename from static url_for or bare filename
                src_file = src.rsplit('/', 1)[-1]
                if src_file in files_with_exports or src in files_with_exports:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_JS_MODULE,
                        description=(
                            f"{html_name} loads '{src_file}' as a classic script "
                            f"(no type=\"module\") but '{src_file}' uses ES module "
                            f"'export' syntax — this causes SyntaxError in the browser. "
                            f"Either add type=\"module\" to the <script> tag or rewrite "
                            f"'{src_file}' to expose its API via a global (IIFE pattern)."
                        ),
                        source_file=html_name,
                        target_file=src_file,
                        missing_name=src_file,
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
                if attr in (
                    "items", "values", "keys", "loop", "url_for", "config",
                    "is_authenticated", "is_active", "is_anonymous",
                    "year", "month", "day", "isoformat",
                    "id", "created_at", "updated_at",
                ):
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
                        severity="error",
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

    def _check_artifact_reachability(
        self,
        files: dict[str, str],
        mode: str,
    ) -> list[CrossLayerObstruction]:
        """Structural gap check: detect artifacts that exist but are unreachable.

        Uses the general-purpose StructuralGapChecker to find templates
        without routes, JS/CSS files never loaded, Python modules never
        imported, etc.
        """
        try:
            from jugeo.cli._structural_gaps import StructuralGapChecker
        except ImportError:
            return []

        repo_kind = "flask_app" if mode == "flask" else "generic"
        checker = StructuralGapChecker()
        report = checker.check(files, repo_kind=repo_kind)

        obstructions: list[CrossLayerObstruction] = []
        for gap in report.gaps:
            # In cross-layer context, reachability gaps are advisory warnings
            # (the improve command uses the gaps directly for error-level blocking)
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.ARTIFACT_REACHABILITY,
                description=gap.message,
                source_file=gap.artifact,
                target_file="(no route/import)",
                missing_name=gap.artifact,
                severity="warning",
            ))
        return obstructions

    # ------------------------------------------------------------------
    # Behavioral error-freedom (branching presheaf)
    # ------------------------------------------------------------------

    def _check_behavioral(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Run the branching presheaf to check every user-behavior fork.

        Complexity: O(n) — checks each branching point independently.
        The sheaf condition guarantees: local error-freedom at each fork
        implies global error-freedom across all composed paths.
        """
        try:
            from jugeo.webapp.theory.behavioral.branching_presheaf import (
                BranchingExtractor,
                BranchingChecker,
            )
        except ImportError:
            return []

        extractor = BranchingExtractor()
        branches = extractor.extract(files)
        checker = BranchingChecker()
        report = checker.check(branches, files)

        obstructions: list[CrossLayerObstruction] = []
        for error in report.errors:
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.BEHAVIORAL_ERROR_FREEDOM,
                description=(
                    f"{error.error_kind} {error.error_type} when "
                    f"'{error.outcome.label}' at {error.branch.expression}: "
                    f"{error.error_description}"
                ),
                source_file=error.branch.file,
                target_file=error.branch.file,
                missing_name=f"{error.branch.kind.value}:{error.outcome.label}",
                severity="error",
            ))
        return obstructions

    # -- Flask/Jinja2 built-in context names (provided by the framework) -----
    _FLASK_BUILTINS: frozenset[str] = frozenset({
        "csrf_token", "url_for", "get_flashed_messages", "config",
        "request", "session", "g", "current_user",
        # Jinja2 loop/control variables
        "loop", "self", "range", "lipsum", "cycler", "joiner", "namespace",
        "true", "false", "none",
    })

    def _check_template_context(
        self,
        app_py: str,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """App→Template context descent: per-route render_template vars ⊇ template vars.

        This is the judgment-geometric check for the app.py ↔ template overlap
        in the webapp presheaf.  For each ``render_template("foo.html", **kw)``
        call in app.py, we build two LocalSections:

        - **Route section** (coordinate ``route:<handler>``): judgment_data =
          {"provided_vars": {set of kwarg names passed to render_template}}.
        - **Template section** (coordinate ``template:<filename>``): judgment_data =
          {"required_vars": {set of top-level variable names in Jinja source}}.

        The overlap condition checks ``provided_vars ⊇ (required_vars - builtins)``.
        A violation produces a DescentObstruction whose RepairFrontier lists the
        missing variable names as ``missing_evidence`` items — telling the repair
        loop exactly which ``render_template`` kwargs to add.

        In addition to per-route descent, we check global Flask extension
        requirements (CSRFProtect, LoginManager, db.create_all).
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # ── Phase 1: Global Flask extension checks ────────────────────────

        uses_csrf = any("csrf_token()" in c for c in html_files.values())
        if uses_csrf and not re.search(r'CSRFProtect\s*\(', app_py):
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.TEMPLATE_CONTEXT,
                description=(
                    "Templates use {{ csrf_token() }} but app.py does not initialize "
                    "CSRFProtect(app). Add: from flask_wtf.csrf import CSRFProtect; "
                    "csrf = CSRFProtect(app)"
                ),
                source_file="app.py",
                target_file="templates/base.html",
                missing_name="CSRFProtect",
                severity="error",
            ))

        uses_current_user = any("current_user" in c for c in html_files.values())
        if uses_current_user and not re.search(
            r'LoginManager|login_manager|current_user', app_py
        ):
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.TEMPLATE_CONTEXT,
                description=(
                    "Templates use {{ current_user }} but app.py does not set up "
                    "Flask-Login (LoginManager)."
                ),
                source_file="app.py",
                target_file="templates/base.html",
                missing_name="LoginManager",
                severity="warning",
            ))

        uses_models = bool(re.search(r'db\.(?:select|get_or_404|session)', app_py))
        if uses_models and "db.create_all()" not in app_py:
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.TEMPLATE_CONTEXT,
                description=(
                    "app.py uses database models but never calls db.create_all(). "
                    "Add: with app.app_context(): db.create_all()"
                ),
                source_file="app.py",
                target_file="models.py",
                missing_name="db.create_all",
                severity="error",
            ))

        # ── Phase 2: Per-route context variable descent ───────────────────
        #
        # Build LocalSections for each (route → template) pair and run the
        # overlap compatibility check: provided_vars ⊇ required_vars.

        # 2a. Extract render_template calls from app.py → {template: provided_vars}
        render_calls = self._extract_render_calls(app_py)

        # 2b. Extract required vars from each template
        template_vars: dict[str, set[str]] = {}
        for filename, content in html_files.items():
            raw = self._extract_jinja_vars(content)
            # Filter out Flask/Jinja2 builtins and Jinja filters/tests
            template_vars[filename] = raw - self._FLASK_BUILTINS

        # 2c. For each render_template call, check the descent condition
        for tmpl_name, provided in render_calls.items():
            # Find the matching template file
            required = set[str]()
            matched_file = ""
            for fname, tvars in template_vars.items():
                # Match "explore.html" against "templates/explore.html" or "explore.html"
                if fname == tmpl_name or fname.endswith("/" + tmpl_name):
                    required = tvars
                    matched_file = fname
                    break

            if not required or not matched_file:
                continue

            # The overlap condition: provided ⊇ required
            missing = required - provided
            if missing:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.TEMPLATE_CONTEXT,
                    description=(
                        f"Route renders '{tmpl_name}' but does not provide context "
                        f"variables: {sorted(missing)}. The template references these "
                        f"variables but render_template() does not pass them. "
                        f"Add them as kwargs: render_template('{tmpl_name}', "
                        + ", ".join(f"{v}=..." for v in sorted(missing)) + ")"
                    ),
                    source_file="app.py",
                    target_file=matched_file,
                    missing_name=", ".join(sorted(missing)),
                    severity="error",
                ))

        return obstructions

    @staticmethod
    def _extract_render_calls(app_py: str) -> dict[str, set[str]]:
        """Parse render_template() calls from app.py source code.

        Returns a dict mapping template filename → set of kwarg names provided.
        This is the route-side LocalSection's judgment_data["provided_vars"].
        """
        results: dict[str, set[str]] = {}
        # Match: render_template("foo.html" [, key=val, ...])
        # We capture the full call including kwargs
        pattern = re.compile(
            r'render_template\(\s*["\']([^"\']+)["\']\s*'
            r'((?:,\s*\w+\s*=\s*[^,)]+)*)\s*\)',
        )
        kwarg_pattern = re.compile(r'(\w+)\s*=')

        for m in pattern.finditer(app_py):
            tmpl_name = m.group(1)
            kwargs_str = m.group(2) or ""
            provided = set(kwarg_pattern.findall(kwargs_str))
            # Merge with any previous calls to the same template
            if tmpl_name in results:
                results[tmpl_name] |= provided
            else:
                results[tmpl_name] = provided

        return results

    @staticmethod
    def _extract_jinja_vars(source: str) -> set[str]:
        """Extract top-level *context* variable names from Jinja2 template source.

        For ``{{ item.title }}``, returns ``item``.
        For ``{% for p in projects %}``, returns ``projects`` (not ``p`` —
        ``p`` is a loop-scoped variable, not a context variable).

        This is the template-side LocalSection's judgment_data["required_vars"].
        """
        names: set[str] = set()
        # Collect loop-scoped variables so we can exclude them
        loop_vars: set[str] = set()
        for m in re.finditer(
            r'\{%[-\s]*for\s+(\w+)\s+in\s+([a-zA-Z_][a-zA-Z0-9_.]*)', source,
        ):
            loop_vars.add(m.group(1))  # the iteration variable (e.g., "p")
            names.add(m.group(2).split(".")[0])  # the collection (e.g., "projects")

        # {{ var }} and {{ var.attr }} expressions
        for m in re.finditer(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)', source):
            full = m.group(1)
            top = full.split(".")[0]
            names.add(top)

        # {% if var %} / {% elif var %} → var might be a context var
        for m in re.finditer(
            r'\{%[-\s]*(?:if|elif)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', source,
        ):
            names.add(m.group(1).split(".")[0])

        # Collect {% set %} variables — these are template-local, not context vars
        set_vars: set[str] = set()
        for m in re.finditer(r'\{%[-\s]*set\s+(\w+)\s*=', source):
            set_vars.add(m.group(1))

        # Remove loop-scoped variables — they come from {% for %}, not from context
        # Remove {% set %} variables — they are template-local definitions
        names -= loop_vars
        names -= set_vars
        return names

    # ------------------------------------------------------------------
    # Route parameter access descent (route_def ∩ route_body)
    # ------------------------------------------------------------------

    def _check_route_param_access(
        self,
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """Route parameter names must match how the handler accesses them.

        The descent condition for the route_definition ∩ route_body overlap:
        every ``kwargs["X"]`` access in a handler body must correspond to a
        ``<type:X>`` parameter in the route's URL pattern.  A mismatch produces
        a KeyError at runtime.
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Parse route→handler pairs: @app.route("/path/<type:param>") → def handler(...)
        route_pattern = re.compile(
            r'@app\.route\(\s*["\']([^"\']+)["\']\s*'
            r'(?:,\s*methods\s*=\s*\[[^\]]*\])?\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'  # skip decorators
            r'def\s+(\w+)\s*\(',
            re.MULTILINE,
        )
        # Extract URL parameter names: <int:project_id> → project_id
        param_pattern = re.compile(r'<\w+:(\w+)>')
        # Extract kwargs access: kwargs["name"] or kwargs['name']
        access_pattern = re.compile(r'kwargs\[\s*["\'](\w+)["\']\s*\]')

        for route_match in route_pattern.finditer(app_py):
            url_path = route_match.group(1)
            handler_name = route_match.group(2)
            route_params = set(param_pattern.findall(url_path))

            if not route_params:
                continue

            # Find handler body (up to next @app.route or end of file)
            handler_start = route_match.end()
            next_route = re.search(r'\n@app\.route\(', app_py[handler_start:])
            handler_end = handler_start + next_route.start() if next_route else len(app_py)
            handler_body = app_py[handler_start:handler_end]

            accessed_params = set(access_pattern.findall(handler_body))
            for accessed in accessed_params:
                if accessed not in route_params:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.ROUTE_PARAM_ACCESS,
                        description=(
                            f"Handler '{handler_name}' accesses kwargs[\"{accessed}\"] "
                            f"but route '{url_path}' only defines parameters: "
                            f"{sorted(route_params)}. This causes KeyError at runtime. "
                            f"Change to kwargs[\"{next(iter(route_params))}\"]"
                        ),
                        source_file="app.py",
                        target_file="app.py",
                        missing_name=accessed,
                        severity="error",
                    ))

        return obstructions

    # ------------------------------------------------------------------
    # Endpoint existence descent (template url_for ∩ app.py routes)
    # ------------------------------------------------------------------

    def _check_endpoint_exists(
        self,
        app_py: str,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Every url_for() endpoint in templates and app.py must exist as a route.

        The descent condition: the set of endpoint names referenced by url_for()
        calls (in templates AND in app.py redirect/url_for) must be a subset of
        the set of route handler function names defined in app.py.  A reference
        to a non-existent endpoint causes BuildError at runtime.
        """
        if not app_py:
            return []

        # Extract all defined route handler names — match both @app.route(...)
        # and blueprint routes like @auth.route(...) or @bp.route(...)
        defined_endpoints: set[str] = set(
            re.findall(r'@\w+\.route\([^)]*\)\s*\n(?:@\w+\s*\n)*def\s+(\w+)\s*\(', app_py)
        )
        # Also add Flask builtins
        defined_endpoints.add("static")

        obstructions: list[CrossLayerObstruction] = []

        # Check url_for() in templates
        for html_name, html_content in html_files.items():
            for m in re.finditer(r"url_for\(\s*['\"]([^'\"]+)['\"]", html_content):
                endpoint = m.group(1)
                # Skip blueprint-style endpoints (caught by TEMPLATE_MODEL or separately)
                if "." in endpoint:
                    continue
                if endpoint not in defined_endpoints:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.ENDPOINT_EXISTS,
                        description=(
                            f"Template '{html_name}' calls url_for('{endpoint}') "
                            f"but no route with handler '{endpoint}' exists in app.py. "
                            f"Defined endpoints: {sorted(defined_endpoints)}"
                        ),
                        source_file=html_name,
                        target_file="app.py",
                        missing_name=endpoint,
                        severity="error",
                    ))

        # Check url_for() in app.py (redirects)
        for m in re.finditer(r'url_for\(\s*["\']([^"\']+)["\']', app_py):
            endpoint = m.group(1)
            if "." in endpoint or endpoint == "static":
                continue
            if endpoint not in defined_endpoints:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.ENDPOINT_EXISTS,
                    description=(
                        f"app.py calls url_for('{endpoint}') but no route "
                        f"with that handler name exists. "
                        f"Did you mean one of: {sorted(defined_endpoints)}?"
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name=endpoint,
                    severity="error",
                ))

        return obstructions

    # ------------------------------------------------------------------
    # Duplicate column descent (models.py internal consistency)
    # ------------------------------------------------------------------

    def _check_duplicate_columns(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Each model class must define each column exactly once.

        The descent condition for the models.py internal overlap: within each
        class body, column name assignments must be unique.  Duplicate column
        definitions cause silent data loss (SQLAlchemy uses the last one).
        """
        models_py = files.get("models.py", "")
        if not models_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Parse class boundaries and column assignments
        class_pattern = re.compile(r'^class\s+(\w+)\s*\(', re.MULTILINE)
        col_pattern = re.compile(r'^\s+(\w+)\s*=\s*db\.Column\(', re.MULTILINE)

        class_starts = [(m.group(1), m.start()) for m in class_pattern.finditer(models_py)]

        for i, (class_name, start) in enumerate(class_starts):
            end = class_starts[i + 1][1] if i + 1 < len(class_starts) else len(models_py)
            class_body = models_py[start:end]

            columns: dict[str, int] = {}
            for col_match in col_pattern.finditer(class_body):
                col_name = col_match.group(1)
                columns[col_name] = columns.get(col_name, 0) + 1

            for col_name, count in columns.items():
                if count > 1:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.MODEL_DUPLICATE_COLUMN,
                        description=(
                            f"Model '{class_name}' defines column '{col_name}' "
                            f"{count} times. SQLAlchemy silently uses the last "
                            f"definition. Remove the duplicate."
                        ),
                        source_file="models.py",
                        target_file="models.py",
                        missing_name=f"{class_name}.{col_name}",
                        severity="warning",
                    ))

        return obstructions

    def _check_app_model_imports(
        self,
        app_py: str,
        files: dict[str, str],
        spec: dict,
    ) -> list[CrossLayerObstruction]:
        """Verify that app.py imports every model class it references.

        This is the app.py ↔ models.py cross-layer morphism: every model name
        used in app.py (e.g., ``db.select(User)``, ``User(...)``) must be
        imported from models.py via ``from models import ...``.
        """
        if not app_py:
            return []

        models = spec.get("models", []) if isinstance(spec, dict) else []
        model_names = set()
        for m in models:
            name = m.get("name", "") if isinstance(m, dict) else getattr(m, "name", "")
            if name:
                model_names.add(name)

        if not model_names:
            return []

        # Check which model names are used in app.py (as bare identifiers)
        obstructions: list[CrossLayerObstruction] = []
        for name in sorted(model_names):
            # Look for usage: db.select(Name), db.get_or_404(Name, ...), Name(...), Name.query
            usage_pattern = re.compile(
                rf'(?:db\.(?:select|get_or_404)\s*\(\s*{re.escape(name)}(?:\s*[,)])'
                rf'|(?<![\'"\w.]){re.escape(name)}\s*\('
                rf'|{re.escape(name)}\.(?:query|username|email|password_hash|id)\b)'
            )
            if not usage_pattern.search(app_py):
                continue  # model not referenced in app.py — no import needed

            # Model is used — check it's imported
            import_pattern = re.compile(
                rf'from\s+models\s+import\s+.*\b{re.escape(name)}\b'
            )
            if not import_pattern.search(app_py):
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.APP_MODEL_IMPORT,
                    description=(
                        f"app.py uses model '{name}' but does not import it from models.py. "
                        f"Add: from models import db, {name}"
                    ),
                    source_file="app.py",
                    target_file="models.py",
                    missing_name=name,
                    severity="error",
                ))
        return obstructions

    # ------------------------------------------------------------------
    # Template inheritance descent (extends → file exists)
    # ------------------------------------------------------------------

    def _check_template_inheritance(
        self,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """{% extends 'X.html' %} → X.html must exist in the html_files dict."""
        extends_re = re.compile(r'\{%\s*extends\s+[\'"]([^\'"]+)[\'"]\s*%\}')
        obstructions: list[CrossLayerObstruction] = []

        # Build lookup set of template basenames and full paths
        known_templates: set[str] = set()
        for fname in html_files:
            known_templates.add(fname)
            # Also add the basename so "base.html" matches "templates/base.html"
            known_templates.add(fname.rsplit("/", 1)[-1])

        for html_name, html_content in html_files.items():
            for m in extends_re.finditer(html_content):
                target = m.group(1)
                if target not in known_templates:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.TEMPLATE_INHERITANCE,
                        description=(
                            f"Template '{html_name}' extends '{target}' but "
                            f"'{target}' does not exist. This causes a Jinja2 "
                            f"TemplateNotFoundError at runtime. Create '{target}' "
                            f"or fix the extends path."
                        ),
                        source_file=html_name,
                        target_file=target,
                        missing_name=target,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Block-name mismatch descent (child block ⊆ parent blocks)
    # ------------------------------------------------------------------

    def _check_block_name_mismatch(
        self,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Every {% block X %} in a child template must exist in its parent.

        Jinja2 silently ignores blocks whose name doesn't match any block in
        the parent, causing content to vanish.  This check detects the mismatch
        at generation time so it can be repaired.
        """
        extends_re = re.compile(r'\{%\s*extends\s+[\'"]([^\'"]+)[\'"]\s*%\}')
        block_re = re.compile(r'\{%[-\s]*block\s+(\w+)')
        obstructions: list[CrossLayerObstruction] = []

        # Resolve template name to key in html_files
        def _resolve(name: str) -> str | None:
            if name in html_files:
                return name
            for key in html_files:
                if key.rsplit("/", 1)[-1] == name:
                    return key
            return None

        for html_name, html_content in html_files.items():
            m_ext = extends_re.search(html_content)
            if not m_ext:
                continue
            parent_key = _resolve(m_ext.group(1))
            if parent_key is None:
                continue  # missing parent caught by TEMPLATE_INHERITANCE
            parent_blocks = set(block_re.findall(html_files[parent_key]))
            child_blocks = set(block_re.findall(html_content))
            for block in sorted(child_blocks - parent_blocks):
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.BLOCK_NAME_MISMATCH,
                    description=(
                        f"Template '{html_name}' fills {{% block {block} %}} but "
                        f"its parent '{m_ext.group(1)}' does not define that block. "
                        f"Content will be silently dropped. Parent blocks: "
                        f"{sorted(parent_blocks)}. If the body block is intended, "
                        f"use {{% block body %}}."
                    ),
                    source_file=html_name,
                    target_file=m_ext.group(1),
                    missing_name=block,
                    severity="error",
                ))
        return obstructions

    # ------------------------------------------------------------------
    # Navigation reachability descent (index →* every GET endpoint)
    # ------------------------------------------------------------------

    def _check_navigation_reachability(
        self,
        app_py: str,
        html_files: dict[str, str],
        spec: dict,
    ) -> list[CrossLayerObstruction]:
        """Every GET route must be reachable from the index page via links.

        This is the global-section connectivity condition: the presheaf of
        pages forms a connected graph when restricted to the navigation
        sub-site.  An unreachable page is a descent obstruction because the
        user has no path to it from the landing page.

        Algorithm: BFS from index through url_for() and href links in
        templates, following render_template() calls in app.py to resolve
        which template each endpoint renders.
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # 1. Build the set of all GET endpoints defined in app.py
        route_re = re.compile(
            r'@\w+\.route\(\s*["\']([^"\']+)["\']\s*'
            r'(?:,\s*methods\s*=\s*\[([^\]]*)\])?\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'
            r'def\s+(\w+)\s*\('
        )
        all_get_endpoints: set[str] = set()
        endpoint_to_template: dict[str, str] = {}

        for m in route_re.finditer(app_py):
            methods_str = m.group(2) or '"GET"'
            if "GET" in methods_str.upper():
                ep = m.group(3)
                all_get_endpoints.add(ep)

        # Skip auth-flow endpoints — they are reachable by definition
        # (login/register are in the nav, logout is behind auth)
        _auth_eps = {"login", "register", "logout", "login_form",
                     "register_form", "login_submit", "register_submit"}
        navigable_endpoints = all_get_endpoints - _auth_eps

        if not navigable_endpoints:
            return []

        # 2. Build endpoint→template map from render_template() calls
        render_re = re.compile(
            r'def\s+(\w+)\s*\([^)]*\):[^}]*?'
            r'render_template\(\s*["\']([^"\']+)["\']',
            re.DOTALL,
        )
        for m in render_re.finditer(app_py):
            endpoint_to_template[m.group(1)] = m.group(2)

        # 3. Build template→{endpoints linked} map from url_for() in templates
        url_for_re = re.compile(r"url_for\(\s*['\"](\w+)['\"]")
        href_re = re.compile(r'href\s*=\s*["\']([^"\'#][^"\']*)["\']')
        template_links: dict[str, set[str]] = {}
        for fname, content in html_files.items():
            links: set[str] = set()
            for m in url_for_re.finditer(content):
                links.add(m.group(1))
            template_links[fname] = links

        # Also extract url_for() from app.py redirect() calls
        redirect_re = re.compile(r'redirect\(\s*url_for\(\s*["\'](\w+)["\']')
        app_redirects: dict[str, set[str]] = {}
        # Parse per-handler
        handler_re = re.compile(r'def\s+(\w+)\s*\(')
        handler_starts = [(m.start(), m.group(1)) for m in handler_re.finditer(app_py)]
        for i, (start, handler) in enumerate(handler_starts):
            end = handler_starts[i + 1][0] if i + 1 < len(handler_starts) else len(app_py)
            body = app_py[start:end]
            redirects = set(redirect_re.findall(body))
            if redirects:
                app_redirects[handler] = redirects

        # 4. Resolve template names: "foo.html" matches "templates/foo.html"
        def _resolve_template(name: str) -> str | None:
            if name in html_files:
                return name
            for key in html_files:
                if key.endswith("/" + name) or key.rsplit("/", 1)[-1] == name:
                    return key
            return None

        # 5. BFS from "index" endpoint
        visited: set[str] = set()
        queue = ["index"] if "index" in all_get_endpoints else []
        # Also seed from base.html (nav links are available on every page)
        base_key = _resolve_template("base.html")
        if base_key:
            queue.extend(template_links.get(base_key, set()) & all_get_endpoints)

        while queue:
            ep = queue.pop(0)
            if ep in visited:
                continue
            visited.add(ep)

            # Follow links in this endpoint's template
            tmpl_name = endpoint_to_template.get(ep, "")
            tmpl_key = _resolve_template(tmpl_name) if tmpl_name else None
            if tmpl_key:
                for linked_ep in template_links.get(tmpl_key, set()):
                    if linked_ep in all_get_endpoints and linked_ep not in visited:
                        queue.append(linked_ep)

            # Follow redirect targets from this endpoint
            for redirect_target in app_redirects.get(ep, set()):
                if redirect_target in all_get_endpoints and redirect_target not in visited:
                    queue.append(redirect_target)

        # 6. Report unreachable endpoints
        unreachable = navigable_endpoints - visited
        for ep in sorted(unreachable):
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.NAVIGATION_REACHABILITY,
                description=(
                    f"GET endpoint '{ep}' is not reachable from the index page "
                    f"via any chain of links. Users have no way to navigate to "
                    f"this page. Add a link in base.html nav, the index template, "
                    f"or another reachable template."
                ),
                source_file="templates/base.html",
                target_file="app.py",
                missing_name=ep,
                severity="error",
            ))

        return obstructions

    # ------------------------------------------------------------------
    # Template include descent (include → file exists)
    # ------------------------------------------------------------------

    def _check_template_include(
        self,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """{% include 'X.html' %} → X.html must exist in the html_files dict."""
        include_re = re.compile(r'\{%[-\s]*include\s+[\'"]([^\'"]+)[\'"]\s*%\}')
        obstructions: list[CrossLayerObstruction] = []

        known_templates: set[str] = set()
        for fname in html_files:
            known_templates.add(fname)
            known_templates.add(fname.rsplit("/", 1)[-1])

        for html_name, html_content in html_files.items():
            for m in include_re.finditer(html_content):
                target = m.group(1)
                if target not in known_templates:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.TEMPLATE_INCLUDE,
                        description=(
                            f"Template '{html_name}' includes '{target}' but "
                            f"'{target}' does not exist. This causes a Jinja2 "
                            f"TemplateNotFoundError at runtime. Create '{target}' "
                            f"or fix the include path."
                        ),
                        source_file=html_name,
                        target_file=target,
                        missing_name=target,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Static file existence descent (url_for('static') → file exists)
    # ------------------------------------------------------------------

    def _check_static_file_exists(
        self,
        html_files: dict[str, str],
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """url_for('static', filename='X') → static/X must exist in files dict."""
        url_for_static_re = re.compile(
            r'url_for\(\s*[\'"]static[\'"]\s*,\s*filename\s*=\s*[\'"]([^\'"]+)[\'"]\s*\)'
        )
        # Also check <link href="static/X"> and <script src="static/X">
        static_href_re = re.compile(
            r'<link\b[^>]*href=[\'"](?:\.\./|/)?static/([^\'"]+)[\'"]'
        )
        static_src_re = re.compile(
            r'<script\b[^>]*src=[\'"](?:\.\./|/)?static/([^\'"]+)[\'"]'
        )

        obstructions: list[CrossLayerObstruction] = []
        known_files: set[str] = set(files.keys())

        for html_name, html_content in html_files.items():
            referenced: set[str] = set()
            for m in url_for_static_re.finditer(html_content):
                referenced.add(m.group(1))
            for m in static_href_re.finditer(html_content):
                referenced.add(m.group(1))
            for m in static_src_re.finditer(html_content):
                referenced.add(m.group(1))

            for ref in referenced:
                static_path = f"static/{ref}"
                if static_path not in known_files:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.STATIC_FILE_EXISTS,
                        description=(
                            f"Template '{html_name}' references static file '{ref}' "
                            f"but '{static_path}' does not exist. This causes a "
                            f"404 Not Found error. Create the file or fix the path."
                        ),
                        source_file=html_name,
                        target_file=static_path,
                        missing_name=ref,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Import package descent (import X → X in requirements.txt)
    # ------------------------------------------------------------------

    #: Known Flask ecosystem packages and their pip name mappings
    _FLASK_ECOSYSTEM_IMPORTS: dict[str, str] = {
        "flask": "flask",
        "flask_sqlalchemy": "flask-sqlalchemy",
        "flask_wtf": "flask-wtf",
        "flask_login": "flask-login",
        "werkzeug": "werkzeug",
        "sqlalchemy": "sqlalchemy",
        "jinja2": "jinja2",
        "wtforms": "wtforms",
        "email_validator": "email-validator",
    }

    def _check_import_package(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """from X import Y / import X → X must be in requirements.txt or stdlib."""
        requirements_txt = files.get("requirements.txt", "")
        # Normalize requirements: strip version specifiers and lowercase
        installed: set[str] = set()
        for line in requirements_txt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip version specifiers: flask>=2.0 → flask
            pkg = re.split(r'[><=!~\[]', line)[0].strip().lower()
            if pkg:
                installed.add(pkg)

        obstructions: list[CrossLayerObstruction] = []
        import_re = re.compile(r'^\s*(?:from\s+(\w+)|import\s+(\w+))', re.MULTILINE)

        py_files = {
            name: content for name, content in files.items()
            if name.endswith(".py")
        }

        for py_name, py_content in py_files.items():
            for m in import_re.finditer(py_content):
                import_name = (m.group(1) or m.group(2)).lower()
                if import_name not in self._FLASK_ECOSYSTEM_IMPORTS:
                    continue
                pip_name = self._FLASK_ECOSYSTEM_IMPORTS[import_name]
                if pip_name.lower() not in installed:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.IMPORT_PACKAGE,
                        description=(
                            f"'{py_name}' imports '{import_name}' but '{pip_name}' "
                            f"is not listed in requirements.txt. This causes "
                            f"ModuleNotFoundError at deploy time. Add '{pip_name}' "
                            f"to requirements.txt."
                        ),
                        source_file=py_name,
                        target_file="requirements.txt",
                        missing_name=pip_name,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Model relationship consistency descent
    # ------------------------------------------------------------------

    def _check_model_relationship_consistency(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """db.relationship('Target', back_populates='attr') consistency check.

        - TargetModel must exist as a class in models.py
        - 'attr' must exist as a relationship attribute on TargetModel
        """
        models_py = files.get("models.py", "")
        if not models_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Extract class names
        class_names: set[str] = set(re.findall(r'^class\s+(\w+)\s*\(', models_py, re.MULTILINE))

        # Extract class boundaries for attribute lookup
        class_pattern = re.compile(r'^class\s+(\w+)\s*\(', re.MULTILINE)
        class_starts = [(m.group(1), m.start()) for m in class_pattern.finditer(models_py)]

        # Build map: class_name → set of attribute names (columns + relationships)
        class_attrs: dict[str, set[str]] = {}
        for i, (cname, start) in enumerate(class_starts):
            end = class_starts[i + 1][1] if i + 1 < len(class_starts) else len(models_py)
            body = models_py[start:end]
            attrs: set[str] = set()
            for attr_m in re.finditer(r'^\s+(\w+)\s*=\s*db\.(?:Column|relationship)\(', body, re.MULTILINE):
                attrs.add(attr_m.group(1))
            class_attrs[cname] = attrs

        # Find all relationship declarations with back_populates
        rel_re = re.compile(
            r'db\.relationship\(\s*[\'"](\w+)[\'"]\s*'
            r'(?:,\s*\w+\s*=[^,)]*)*'
            r',\s*back_populates\s*=\s*[\'"](\w+)[\'"]'
        )

        for i, (cname, start) in enumerate(class_starts):
            end = class_starts[i + 1][1] if i + 1 < len(class_starts) else len(models_py)
            body = models_py[start:end]

            for m in rel_re.finditer(body):
                target_model = m.group(1)
                back_attr = m.group(2)

                if target_model not in class_names:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.MODEL_RELATIONSHIP_CONSISTENCY,
                        description=(
                            f"Model '{cname}' has db.relationship('{target_model}', ...) "
                            f"but class '{target_model}' does not exist in models.py. "
                            f"This causes a SQLAlchemy mapper configuration error. "
                            f"Define the '{target_model}' model or fix the relationship target."
                        ),
                        source_file="models.py",
                        target_file="models.py",
                        missing_name=f"{cname}.relationship.{target_model}",
                        severity="error",
                    ))
                    continue

                target_attrs = class_attrs.get(target_model, set())
                if back_attr not in target_attrs:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.MODEL_RELATIONSHIP_CONSISTENCY,
                        description=(
                            f"Model '{cname}' has db.relationship('{target_model}', "
                            f"back_populates='{back_attr}') but '{target_model}' has no "
                            f"attribute '{back_attr}'. Add a corresponding "
                            f"db.relationship() named '{back_attr}' to '{target_model}'. "
                            f"This causes a SQLAlchemy mapper configuration error."
                        ),
                        source_file="models.py",
                        target_file="models.py",
                        missing_name=f"{target_model}.{back_attr}",
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Config ordering descent (Flask extension initialization order)
    # ------------------------------------------------------------------

    def _check_config_ordering(
        self,
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """Check Flask config ordering constraints.

        - SECRET_KEY must be set BEFORE CSRFProtect(app)
        - db.init_app(app) must happen AFTER app.config is set
        - CSRFProtect must be imported when used
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Check SECRET_KEY before CSRFProtect
        csrf_match = re.search(r'CSRFProtect\s*\(', app_py)
        secret_match = re.search(r'(?:app\.config\s*\[\s*[\'"]SECRET_KEY[\'"]\s*\]|secret_key)\s*=', app_py, re.IGNORECASE)

        if csrf_match:
            # Check CSRFProtect is imported
            if not re.search(r'from\s+flask_wtf(?:\.csrf)?\s+import\s+.*CSRFProtect', app_py):
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.CONFIG_ORDERING,
                    description=(
                        "CSRFProtect() is used but not imported. Add: "
                        "from flask_wtf.csrf import CSRFProtect"
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name="CSRFProtect_import",
                    severity="error",
                ))

            if secret_match and csrf_match.start() < secret_match.start():
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.CONFIG_ORDERING,
                    description=(
                        "CSRFProtect(app) is called before SECRET_KEY is set. "
                        "Move app.config['SECRET_KEY'] = ... before CSRFProtect(app). "
                        "Without this, CSRF token generation fails silently."
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name="SECRET_KEY_before_CSRFProtect",
                    severity="error",
                ))
            elif not secret_match and csrf_match:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.CONFIG_ORDERING,
                    description=(
                        "CSRFProtect(app) is used but SECRET_KEY is never set. "
                        "Add app.config['SECRET_KEY'] = 'your-secret-key' before "
                        "CSRFProtect(app). CSRF tokens require a secret key."
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name="SECRET_KEY",
                    severity="error",
                ))

        # Check db.init_app after config
        db_init_match = re.search(r'db\.init_app\(', app_py)
        config_match = re.search(r'app\.config\[', app_py)
        if db_init_match and config_match and db_init_match.start() < config_match.start():
            obstructions.append(CrossLayerObstruction(
                check=CrossLayerCheck.CONFIG_ORDERING,
                description=(
                    "db.init_app(app) is called before app.config is set. "
                    "Move database configuration (SQLALCHEMY_DATABASE_URI etc.) "
                    "before db.init_app(app). Config changes after init_app are ignored."
                ),
                source_file="app.py",
                target_file="app.py",
                missing_name="config_before_db_init",
                severity="error",
            ))

        return obstructions

    # ------------------------------------------------------------------
    # Form method ↔ route method descent
    # ------------------------------------------------------------------

    def _check_form_method_route(
        self,
        html_files: dict[str, str],
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """<form method="post" action="url_for('X')"> → route X must accept POST."""
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Build endpoint → allowed methods map
        route_methods: dict[str, set[str]] = {}
        route_re = re.compile(
            r'@app\.route\(\s*["\'][^"\']+["\']\s*'
            r'(?:,\s*methods\s*=\s*\[([^\]]*)\])?\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'
            r'def\s+(\w+)\s*\(',
            re.MULTILINE,
        )
        for m in route_re.finditer(app_py):
            methods_str = m.group(1)
            handler = m.group(2)
            if methods_str:
                methods = set(
                    s.strip().strip("'\"").upper()
                    for s in methods_str.split(",")
                )
            else:
                methods = {"GET"}
            route_methods[handler] = methods

        # Extract form method + action endpoint from templates
        form_re = re.compile(
            r'<form\b[^>]*method=["\'](\w+)["\'][^>]*action=["\']'
            r'\{\{\s*url_for\(\s*[\'"](\w+)[\'"]\s*(?:,[^)]*?)?\)\s*\}\}["\']',
            re.IGNORECASE,
        )
        # Also try action before method
        form_re2 = re.compile(
            r'<form\b[^>]*action=["\']'
            r'\{\{\s*url_for\(\s*[\'"](\w+)[\'"]\s*(?:,[^)]*?)?\)\s*\}\}["\']'
            r'[^>]*method=["\'](\w+)["\']',
            re.IGNORECASE,
        )

        for html_name, html_content in html_files.items():
            for m in form_re.finditer(html_content):
                form_method = m.group(1).upper()
                endpoint = m.group(2)
                if endpoint in route_methods and form_method not in route_methods[endpoint]:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.FORM_METHOD_ROUTE,
                        description=(
                            f"Form in '{html_name}' submits {form_method} to endpoint "
                            f"'{endpoint}' but that route only accepts "
                            f"{sorted(route_methods[endpoint])}. This causes 405 Method "
                            f"Not Allowed. Add '{form_method}' to the route's methods list."
                        ),
                        source_file=html_name,
                        target_file="app.py",
                        missing_name=f"{endpoint}:{form_method}",
                        severity="error",
                    ))
            for m in form_re2.finditer(html_content):
                endpoint = m.group(1)
                form_method = m.group(2).upper()
                if endpoint in route_methods and form_method not in route_methods[endpoint]:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.FORM_METHOD_ROUTE,
                        description=(
                            f"Form in '{html_name}' submits {form_method} to endpoint "
                            f"'{endpoint}' but that route only accepts "
                            f"{sorted(route_methods[endpoint])}. This causes 405 Method "
                            f"Not Allowed. Add '{form_method}' to the route's methods list."
                        ),
                        source_file=html_name,
                        target_file="app.py",
                        missing_name=f"{endpoint}:{form_method}",
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # url_for parameter descent (url_for params ∩ route URL params)
    # ------------------------------------------------------------------

    def _check_url_for_params(
        self,
        app_py: str,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """url_for('endpoint', param=val) → route must have <param> in URL pattern."""
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Build endpoint → route URL params map
        route_params_map: dict[str, set[str]] = {}
        route_re = re.compile(
            r'@app\.route\(\s*["\']([^"\']+)["\']\s*'
            r'(?:,\s*methods\s*=\s*\[[^\]]*\])?\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'
            r'def\s+(\w+)\s*\(',
            re.MULTILINE,
        )
        param_re = re.compile(r'<(?:\w+:)?(\w+)>')
        for m in route_re.finditer(app_py):
            url_path = m.group(1)
            handler = m.group(2)
            params = set(param_re.findall(url_path))
            route_params_map[handler] = params

        # Extract url_for calls with keyword arguments from templates and app.py
        url_for_re = re.compile(
            r'url_for\(\s*[\'"](\w+)[\'"]\s*'
            r'((?:,\s*\w+\s*=[^,)]+)*)\s*\)'
        )
        kwarg_re = re.compile(r'(\w+)\s*=')

        all_sources: dict[str, str] = dict(html_files)
        all_sources["app.py"] = app_py

        for src_name, src_content in all_sources.items():
            for m in url_for_re.finditer(src_content):
                endpoint = m.group(1)
                kwargs_str = m.group(2) or ""
                if not kwargs_str or endpoint not in route_params_map:
                    continue
                provided_kwargs = set(kwarg_re.findall(kwargs_str))
                route_params = route_params_map[endpoint]

                # Check for kwargs that don't match any route parameter
                for kwarg in provided_kwargs:
                    if kwarg not in route_params and route_params:
                        obstructions.append(CrossLayerObstruction(
                            check=CrossLayerCheck.URL_FOR_PARAMS,
                            description=(
                                f"url_for('{endpoint}', {kwarg}=...) in '{src_name}' "
                                f"passes unexpected parameter '{kwarg}'. Route expects: "
                                f"{sorted(route_params)}. This causes a BuildError. "
                                f"Use the correct parameter name."
                            ),
                            source_file=src_name,
                            target_file="app.py",
                            missing_name=f"{endpoint}:{kwarg}",
                            severity="error",
                        ))

                # Check for missing required route params
                for param in route_params:
                    if param not in provided_kwargs:
                        obstructions.append(CrossLayerObstruction(
                            check=CrossLayerCheck.URL_FOR_PARAMS,
                            description=(
                                f"url_for('{endpoint}', ...) in '{src_name}' does not "
                                f"provide required parameter '{param}'. Route URL requires "
                                f"<{param}>. This causes a BuildError. Add {param}=... "
                                f"to the url_for() call."
                            ),
                            source_file=src_name,
                            target_file="app.py",
                            missing_name=f"{endpoint}:{param}",
                            severity="error",
                        ))
        return obstructions

    # ------------------------------------------------------------------
    # Template set vars descent (improves TEMPLATE_CONTEXT accuracy)
    # ------------------------------------------------------------------

    def _check_template_set_vars(
        self,
        app_py: str,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Verify that {% set %} vars are not falsely flagged as missing context.

        This is a refinement check: it re-examines the template context descent
        with {% set %} variables properly excluded. Any remaining violations
        that were already caught by TEMPLATE_CONTEXT are not duplicated — this
        check only catches cases where {% set %} usage itself is inconsistent
        (e.g., {% set x = y %} where y is undefined).
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # For each template, check that {% set x = expr %} uses valid context vars in expr
        render_calls = self._extract_render_calls(app_py)

        for html_name, html_content in html_files.items():
            # Determine provided context for this template
            provided: set[str] = set()
            for tmpl_name, tvars in render_calls.items():
                if html_name == tmpl_name or html_name.endswith("/" + tmpl_name):
                    provided = tvars
                    break

            # Collect loop vars and set vars for exclusion
            loop_vars: set[str] = set()
            for m in re.finditer(r'\{%[-\s]*for\s+(\w+)\s+in\s+', html_content):
                loop_vars.add(m.group(1))

            set_vars: set[str] = set()
            set_re = re.compile(r'\{%[-\s]*set\s+(\w+)\s*=\s*([^%]+)%\}')
            for m in set_re.finditer(html_content):
                var_name = m.group(1)
                expr = m.group(2).strip()
                set_vars.add(var_name)

                # Check if the expression references an undefined variable
                expr_vars = set(re.findall(r'\b([a-zA-Z_]\w*)\b', expr))
                # Remove known safe names
                expr_vars -= self._FLASK_BUILTINS
                expr_vars -= loop_vars
                expr_vars -= set_vars
                expr_vars -= provided
                # Remove common Python/Jinja builtins
                expr_vars -= {"true", "false", "none", "True", "False", "None",
                              "int", "str", "float", "len", "dict", "list"}
                # Remove string literals that regex falsely captured
                expr_vars = {v for v in expr_vars if not v.isdigit()}

                for undefined in expr_vars:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.TEMPLATE_SET_VARS,
                        description=(
                            f"Template '{html_name}' has '{{% set {var_name} = {expr} %}}' "
                            f"but variable '{undefined}' in the expression is not defined "
                            f"in context or prior set/for statements. Pass '{undefined}' "
                            f"from render_template() or define it earlier in the template."
                        ),
                        source_file=html_name,
                        target_file="app.py",
                        missing_name=f"set:{var_name}:{undefined}",
                        severity="warning",
                    ))

        return obstructions

    # ------------------------------------------------------------------
    # Python / Jinja2 syntax descent
    # ------------------------------------------------------------------

    def _check_python_syntax(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Every .py file must parse with ast.parse(); .html must have balanced blocks."""
        obstructions: list[CrossLayerObstruction] = []

        # Check Python files
        for fname, content in files.items():
            if not fname.endswith(".py"):
                continue
            try:
                ast.parse(content, filename=fname)
            except SyntaxError as e:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.PYTHON_SYNTAX,
                    description=(
                        f"'{fname}' has a SyntaxError at line {e.lineno}: {e.msg}. "
                        f"Fix the syntax error before deployment."
                    ),
                    source_file=fname,
                    target_file=fname,
                    missing_name=f"syntax:{fname}:{e.lineno}",
                    severity="error",
                ))

        # Check Jinja2 block balance in HTML files
        block_pairs = [
            ("block", "endblock"),
            ("for", "endfor"),
            ("if", "endif"),
            ("macro", "endmacro"),
            ("call", "endcall"),
            ("filter", "endfilter"),
        ]
        for fname, content in files.items():
            if not fname.endswith((".html", ".jinja", ".jinja2")):
                continue
            for open_tag, close_tag in block_pairs:
                open_count = len(re.findall(
                    rf'\{{%[-\s]*{open_tag}\b', content
                ))
                close_count = len(re.findall(
                    rf'\{{%[-\s]*{close_tag}\s*%\}}', content
                ))
                if open_count != close_count:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.PYTHON_SYNTAX,
                        description=(
                            f"'{fname}' has {open_count} '{{% {open_tag} %}}' but "
                            f"{close_count} '{{% {close_tag} %}}'. Mismatched blocks "
                            f"cause Jinja2 TemplateSyntaxError. Add the missing "
                            f"'{{% {close_tag} %}}' tag(s)."
                        ),
                        source_file=fname,
                        target_file=fname,
                        missing_name=f"balance:{open_tag}:{close_tag}",
                        severity="error",
                    ))

        return obstructions

    # ------------------------------------------------------------------
    # Response type consistency descent
    # ------------------------------------------------------------------

    def _check_response_type_consistency(
        self,
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """API routes should return JSON; non-API routes should render HTML."""
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        route_re = re.compile(
            r'@app\.route\(\s*["\']([^"\']+)["\']\s*'
            r'(?:,\s*methods\s*=\s*\[[^\]]*\])?\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'
            r'def\s+(\w+)\s*\(',
            re.MULTILINE,
        )

        for m in route_re.finditer(app_py):
            url_path = m.group(1)
            handler = m.group(2)

            # Find handler body
            handler_start = m.end()
            next_route = re.search(r'\n@app\.route\(', app_py[handler_start:])
            handler_end = handler_start + next_route.start() if next_route else len(app_py)
            body = app_py[handler_start:handler_end]

            is_api = url_path.startswith("/api/") or url_path.startswith("/api_")
            uses_render = bool(re.search(r'\breturn\s+render_template\(', body))
            uses_jsonify = bool(re.search(r'\breturn\s+jsonify\(', body))

            if is_api and uses_render and not uses_jsonify:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.RESPONSE_TYPE_CONSISTENCY,
                    description=(
                        f"API route '{url_path}' (handler '{handler}') returns "
                        f"render_template() but API endpoints should return jsonify() "
                        f"or JSON responses. Change to return jsonify(data)."
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name=f"api_render:{handler}",
                    severity="warning",
                ))
            elif not is_api and uses_jsonify and not uses_render:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.RESPONSE_TYPE_CONSISTENCY,
                    description=(
                        f"Non-API route '{url_path}' (handler '{handler}') returns "
                        f"jsonify() but appears to be a page route. Consider using "
                        f"render_template() for HTML responses."
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name=f"page_jsonify:{handler}",
                    severity="warning",
                ))

        return obstructions

    # ------------------------------------------------------------------
    # Redirect-after-POST descent (PRG pattern)
    # ------------------------------------------------------------------

    def _check_redirect_after_post(
        self,
        app_py: str,
    ) -> list[CrossLayerObstruction]:
        """POST handlers should redirect (PRG pattern), not render_template().

        Exception: handlers that return error status codes (4xx).
        """
        if not app_py:
            return []

        obstructions: list[CrossLayerObstruction] = []

        route_re = re.compile(
            r'@app\.route\(\s*["\']([^"\']+)["\']\s*'
            r',\s*methods\s*=\s*\[([^\]]*)\]\s*\)\s*\n'
            r'(?:@\w+\s*\n)*'
            r'def\s+(\w+)\s*\(',
            re.MULTILINE,
        )

        for m in route_re.finditer(app_py):
            url_path = m.group(1)
            methods_str = m.group(2)
            handler = m.group(3)

            methods = set(s.strip().strip("'\"").upper() for s in methods_str.split(","))
            if "POST" not in methods:
                continue

            # Find handler body
            handler_start = m.end()
            next_route = re.search(r'\n@app\.route\(', app_py[handler_start:])
            handler_end = handler_start + next_route.start() if next_route else len(app_py)
            body = app_py[handler_start:handler_end]

            # Check if the POST branch uses render_template without redirect
            # Look for "if request.method == 'POST'" block pattern
            has_redirect = bool(re.search(r'\breturn\s+redirect\(', body))
            has_render_in_post = False

            # If methods only include POST (no GET), the whole body handles POST
            if methods == {"POST"}:
                has_render_in_post = bool(re.search(r'\breturn\s+render_template\(', body))
            else:
                # Mixed methods — look for render_template in POST branch
                post_block = re.search(
                    r'if\s+request\.method\s*==\s*[\'"]POST[\'"]\s*:', body
                )
                if post_block:
                    post_body = body[post_block.end():]
                    # Simple heuristic: check up to next elif/else at same indent
                    next_branch = re.search(r'\n\s{4}(?:elif|else)', post_body)
                    post_section = post_body[:next_branch.start()] if next_branch else post_body
                    has_render_in_post = bool(re.search(r'\breturn\s+render_template\(', post_section))
                    has_redirect = has_redirect or bool(re.search(r'\breturn\s+redirect\(', post_section))

            # Exception: error returns (status 4xx)
            has_error_return = bool(re.search(r'\breturn\s+render_template\([^)]+,\s*(?:4\d\d|422|400)\s*\)', body))

            if has_render_in_post and not has_redirect and not has_error_return:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.REDIRECT_AFTER_POST,
                    description=(
                        f"POST handler '{handler}' for '{url_path}' returns "
                        f"render_template() instead of redirect(). This violates "
                        f"the Post/Redirect/Get pattern and causes form resubmission "
                        f"on browser refresh. Use redirect(url_for('...')) after "
                        f"successful POST processing."
                    ),
                    source_file="app.py",
                    target_file="app.py",
                    missing_name=f"prg:{handler}",
                    severity="warning",
                ))

        return obstructions

    # ------------------------------------------------------------------
    # CSS custom property descent (var(--X) → --X defined)
    # ------------------------------------------------------------------

    def _check_css_custom_property(
        self,
        css_content: str,
    ) -> list[CrossLayerObstruction]:
        """var(--X) usage in CSS → --X must be defined somewhere in the CSS."""
        if not css_content:
            return []

        obstructions: list[CrossLayerObstruction] = []

        # Extract definitions: --X: value
        defined_props: set[str] = set(
            re.findall(r'--([a-zA-Z][\w-]*)\s*:', css_content)
        )

        # Extract usages: var(--X)
        used_props: set[str] = set(
            re.findall(r'var\(--([a-zA-Z][\w-]*)\)', css_content)
        )

        for prop in used_props:
            if prop not in defined_props:
                obstructions.append(CrossLayerObstruction(
                    check=CrossLayerCheck.CSS_CUSTOM_PROPERTY,
                    description=(
                        f"CSS uses var(--{prop}) but --{prop} is never defined. "
                        f"Add '--{prop}: <value>;' to :root or the appropriate "
                        f"selector. The property may be defined in an external "
                        f"stylesheet not checked here."
                    ),
                    source_file="static/style.css",
                    target_file="static/style.css",
                    missing_name=f"--{prop}",
                    severity="warning",
                ))

        return obstructions

    # ------------------------------------------------------------------
    # Jinja2 import/from descent ({% import %}, {% from %})
    # ------------------------------------------------------------------

    def _check_jinja_import(
        self,
        html_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """{% import 'macros.html' as m %} / {% from 'X' import Y %} → file must exist."""
        import_re = re.compile(
            r'\{%[-\s]*(?:import|from)\s+[\'"]([^\'"]+)[\'"]\s+'
        )
        obstructions: list[CrossLayerObstruction] = []

        known: set[str] = set()
        for fname in html_files:
            known.add(fname)
            known.add(fname.rsplit("/", 1)[-1])

        for html_name, html_content in html_files.items():
            for m in import_re.finditer(html_content):
                target = m.group(1)
                if target not in known:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JINJA_IMPORT,
                        description=(
                            f"Template '{html_name}' imports '{target}' but "
                            f"'{target}' does not exist. This causes Jinja2 "
                            f"TemplateNotFoundError. Create the macro file or "
                            f"fix the import path."
                        ),
                        source_file=html_name,
                        target_file=target,
                        missing_name=target,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # JS CommonJS require() descent
    # ------------------------------------------------------------------

    def _check_js_require(
        self,
        js_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """require('./X') → X must exist as a JS file."""
        require_re = re.compile(r'require\(\s*[\'"](\.[^\'"]+)[\'"]\s*\)')
        obstructions: list[CrossLayerObstruction] = []

        known_js: set[str] = set()
        for fname in js_files:
            known_js.add(fname)
            known_js.add(fname.rsplit("/", 1)[-1])
            # Also add without .js extension
            if fname.endswith(".js"):
                known_js.add(fname[:-3])
                known_js.add(fname.rsplit("/", 1)[-1][:-3])

        for js_name, js_content in js_files.items():
            for m in require_re.finditer(js_content):
                target = m.group(1)
                # Normalize: ./module → module, ./module.js → module.js
                normalized = target.lstrip("./")
                if normalized not in known_js and normalized + ".js" not in known_js:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.JS_REQUIRE,
                        description=(
                            f"'{js_name}' requires '{target}' but the module "
                            f"does not exist. This causes a module resolution error. "
                            f"Create the module or fix the require path."
                        ),
                        source_file=js_name,
                        target_file=target,
                        missing_name=target,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Prolog module resolution descent (:- use_module, :- consult)
    # ------------------------------------------------------------------

    def _check_prolog_module(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """:- use_module(X) / :- consult(X) → X.pl must exist in files."""
        prolog_files = {
            name: content for name, content in files.items()
            if name.endswith(".pl")
        }
        if not prolog_files:
            return []

        obstructions: list[CrossLayerObstruction] = []
        # :- use_module(path) or :- consult(path)
        use_re = re.compile(r':-\s*(?:use_module|consult)\(\s*([^\s)]+)\s*\)')

        known_modules: set[str] = set()
        for fname in prolog_files:
            known_modules.add(fname)
            known_modules.add(fname.rsplit("/", 1)[-1])
            if fname.endswith(".pl"):
                known_modules.add(fname[:-3])
                known_modules.add(fname.rsplit("/", 1)[-1][:-3])

        for pl_name, pl_content in prolog_files.items():
            for m in use_re.finditer(pl_content):
                target = m.group(1).strip("'\"")
                normalized = target.lstrip("./")
                if (normalized not in known_modules
                        and normalized + ".pl" not in known_modules
                        and not target.startswith("library(")):
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.PROLOG_MODULE,
                        description=(
                            f"'{pl_name}' uses module '{target}' but it does not "
                            f"exist. This causes an existence_error at consult time. "
                            f"Create '{target}.pl' or fix the module path."
                        ),
                        source_file=pl_name,
                        target_file=target + ".pl" if not target.endswith(".pl") else target,
                        missing_name=target,
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Prolog predicate export descent (:- module(Name, [pred/arity]))
    # ------------------------------------------------------------------

    def _check_prolog_predicate(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """Exported predicates in :- module(Name, [...]) must be defined in the file."""
        prolog_files = {
            name: content for name, content in files.items()
            if name.endswith(".pl")
        }
        if not prolog_files:
            return []

        obstructions: list[CrossLayerObstruction] = []
        module_re = re.compile(
            r':-\s*module\(\s*\w+\s*,\s*\[([^\]]+)\]\s*\)'
        )
        # Matches pred/arity declarations like: foo/2, bar/3
        export_re = re.compile(r'(\w+)/(\d+)')

        for pl_name, pl_content in prolog_files.items():
            module_match = module_re.search(pl_content)
            if not module_match:
                continue

            exports = export_re.findall(module_match.group(1))
            # Check each exported predicate is defined
            for pred_name, arity in exports:
                # Look for predicate definition: pred_name(arg1, ..., argN) :-
                # or pred_name(arg1, ..., argN).
                # Simple heuristic: pred_name appears at start of a line as a head
                head_pattern = re.compile(
                    rf'^{re.escape(pred_name)}\s*[\(.]', re.MULTILINE
                )
                if not head_pattern.search(pl_content):
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.PROLOG_PREDICATE,
                        description=(
                            f"'{pl_name}' exports {pred_name}/{arity} in its "
                            f":- module declaration but no clause for '{pred_name}' "
                            f"is defined. This causes an existence_error when "
                            f"another module calls {pred_name}/{arity}."
                        ),
                        source_file=pl_name,
                        target_file=pl_name,
                        missing_name=f"{pred_name}/{arity}",
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Prolog ↔ Python bridge descent (py_call references)
    # ------------------------------------------------------------------

    def _check_prolog_python_bridge(
        self,
        files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """py_call(Module, Function, ...) → Python Module.Function must exist."""
        prolog_files = {
            name: content for name, content in files.items()
            if name.endswith(".pl")
        }
        python_files = {
            name: content for name, content in files.items()
            if name.endswith(".py")
        }
        if not prolog_files or not python_files:
            return []

        obstructions: list[CrossLayerObstruction] = []
        # py_call(module_name, function_name, Args, Result)
        pycall_re = re.compile(
            r'py_call\(\s*(\w+)\s*,\s*(\w+)'
        )

        # Build Python module → functions map
        py_modules: dict[str, set[str]] = {}
        for py_name, py_content in python_files.items():
            mod_name = py_name.rsplit("/", 1)[-1].replace(".py", "")
            funcs = set(re.findall(r'^def\s+(\w+)\s*\(', py_content, re.MULTILINE))
            py_modules[mod_name] = funcs

        for pl_name, pl_content in prolog_files.items():
            for m in pycall_re.finditer(pl_content):
                py_module = m.group(1)
                py_func = m.group(2)

                if py_module not in py_modules:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.PROLOG_PYTHON_BRIDGE,
                        description=(
                            f"'{pl_name}' calls py_call({py_module}, {py_func}, ...) "
                            f"but Python module '{py_module}' does not exist. "
                            f"Create '{py_module}.py' or fix the py_call target."
                        ),
                        source_file=pl_name,
                        target_file=f"{py_module}.py",
                        missing_name=f"{py_module}.{py_func}",
                        severity="error",
                    ))
                elif py_func not in py_modules[py_module]:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.PROLOG_PYTHON_BRIDGE,
                        description=(
                            f"'{pl_name}' calls py_call({py_module}, {py_func}, ...) "
                            f"but '{py_module}.py' has no function '{py_func}'. "
                            f"Defined functions: {sorted(py_modules[py_module])}. "
                            f"Add the function or fix the py_call target."
                        ),
                        source_file=pl_name,
                        target_file=f"{py_module}.py",
                        missing_name=f"{py_module}.{py_func}",
                        severity="error",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # HTML asset integrity (script/link refer to existing JS/CSS)
    # ------------------------------------------------------------------

    def _check_html_asset_integrity(
        self,
        html_files: dict[str, str],
        js_files: dict[str, str],
    ) -> list[CrossLayerObstruction]:
        """<script src="X.js"> in templates → X.js must exist in js_files.

        Complements STATIC_FILE_EXISTS by checking non-static-url references
        (direct paths, CDN-like paths within the project).
        """
        obstructions: list[CrossLayerObstruction] = []
        # Match script tags that reference local (non-CDN) JS files
        script_re = re.compile(
            r'<script\b[^>]*src=[\'"](?:\{\{[^}]*\}\}|([^\'"{}]+\.js))[\'"]',
            re.IGNORECASE,
        )

        known_js: set[str] = set()
        for fname in js_files:
            known_js.add(fname)
            known_js.add(fname.rsplit("/", 1)[-1])

        for html_name, html_content in html_files.items():
            for m in script_re.finditer(html_content):
                src = m.group(1)
                if not src:  # Jinja2 expression (handled by STATIC_FILE_EXISTS)
                    continue
                # Skip external URLs
                if src.startswith(("http:", "https:", "//", "data:")):
                    continue
                basename = src.rsplit("/", 1)[-1]
                if basename not in known_js:
                    obstructions.append(CrossLayerObstruction(
                        check=CrossLayerCheck.HTML_ASSET_INTEGRITY,
                        description=(
                            f"Template '{html_name}' loads script '{src}' "
                            f"but '{basename}' does not exist in JS files. "
                            f"Create the script or fix the src path."
                        ),
                        source_file=html_name,
                        target_file=src,
                        missing_name=basename,
                        severity="warning",
                    ))
        return obstructions

    # ------------------------------------------------------------------
    # Seed data presence check
    # ------------------------------------------------------------------

    def _check_seed_data_present(
        self,
        app_py: str,
        spec: dict,
    ) -> list[CrossLayerObstruction]:
        """Warn when models are defined but no seed data is present in app.py.

        A spec that defines models and content pages (explore/list/gallery)
        creates an implicit obligation that those pages render data.  If
        ``app.py`` contains no seed-data block the generated app will show
        empty pages on first run — a violation of the content obligation.

        Detection heuristic: scan ``app.py`` for at least one
        ``db.session.add(`` or ``db.session.add_all(`` call that occurs
        *outside* a route handler (i.e. at module scope, inside an
        ``app.app_context()`` block, or in an ``if __name__`` block).
        Bulk insert patterns (``INSERT INTO`` in raw SQL) are also accepted.

        Severity: warning (static analysis cannot confirm the page will be
        empty — it might have a separate seed script).  The browser descent
        phase upgrades this to an error (SPARSE_CONTENT) if the live page
        confirms it.
        """
        if not app_py:
            return []

        models = spec.get("models", [])
        if not models:
            return []

        # Only flag when the spec has content pages (routes with list/explore/gallery)
        content_route_handlers = {
            r.get("handler", "") if isinstance(r, dict) else getattr(r, "handler", "")
            for r in spec.get("routes", [])
        }
        has_content_pages = any(
            handler.endswith("_list")
            or handler in {"index", "home", "explore", "browse", "gallery",
                           "dashboard", "feed", "catalog"}
            or re.search(r"(explore|browse|gallery|list|dashboard|feed)", handler)
            for handler in content_route_handlers
        )
        if not has_content_pages:
            return []

        # Look for seed data patterns in the code
        # We accept: db.session.add(, db.session.add_all(, or bulk inserts
        seed_patterns = [
            r"db\.session\.add\s*\(",
            r"db\.session\.add_all\s*\(",
            r"db\.session\.bulk_insert_mappings\s*\(",
            r"db\.engine\.execute\s*\(",
            r"INSERT\s+INTO\s+\w+",   # raw SQL seeds
        ]
        has_seed = any(re.search(p, app_py, re.IGNORECASE) for p in seed_patterns)

        if has_seed:
            return []

        # Build the model names list for the error message
        model_names = [
            m.get("name", "?") if isinstance(m, dict) else getattr(m, "name", "?")
            for m in models[:4]
        ]
        names_str = ", ".join(model_names)
        if len(models) > 4:
            names_str += f", … ({len(models)} total)"

        return [CrossLayerObstruction(
            check=CrossLayerCheck.SEED_DATA_ABSENT,
            description=(
                f"app.py has no seed data block. Models ({names_str}) will have "
                f"zero rows on first run, causing list/explore/gallery pages to "
                f"show empty states. "
                f"Fix: add an idempotent seed block after db.create_all() — "
                f"check if a row exists before inserting, then call "
                f"db.session.add_all([Model(...), ...]) and db.session.commit(). "
                f"The live browser descent check will confirm with SPARSE_CONTENT "
                f"if pages render empty after seeding fails."
            ),
            source_file="app.py",
            target_file="app.py",
            missing_name="seed_data",
            severity="warning",
        )]

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
            elif obs.check == CrossLayerCheck.ARTIFACT_REACHABILITY:
                # Suggest wiring the artifact based on its type
                artifact = obs.missing_name
                if artifact.endswith('.html'):
                    stem = artifact.rsplit('.', 1)[0].rsplit('/', 1)[-1]
                    repairs.append(RepairAction(
                        obstruction=obs,
                        repair_type="add_flask_route",
                        repair_data={
                            "path": f"/{stem}",
                            "function_name": stem.replace("-", "_"),
                            "target_file": "app.py",
                            "template": artifact,
                        },
                        description=f"Add route /{stem} → render_template('{artifact}')",
                    ))
                elif artifact.endswith('.js'):
                    repairs.append(RepairAction(
                        obstruction=obs,
                        repair_type="add_script_tag",
                        repair_data={
                            "src": artifact,
                            "target_file": "templates/base.html",
                        },
                        description=f"Add <script src=\"{artifact}\"> to base.html",
                    ))
                elif artifact.endswith('.css'):
                    repairs.append(RepairAction(
                        obstruction=obs,
                        repair_type="add_css_link",
                        repair_data={
                            "href": artifact,
                            "target_file": "templates/base.html",
                        },
                        description=f"Add <link href=\"{artifact}\"> to base.html",
                    ))
            elif obs.check == CrossLayerCheck.APP_MODEL_IMPORT:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_model_import",
                    repair_data={
                        "model_name": obs.missing_name,
                        "target_file": "app.py",
                    },
                    description=(
                        f"Add 'from models import db, {obs.missing_name}' to app.py "
                        f"(or add {obs.missing_name} to existing models import)"
                    ),
                ))
            elif obs.check == CrossLayerCheck.TEMPLATE_CONTEXT:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_flask_extension",
                    repair_data={
                        "extension_name": obs.missing_name,
                        "target_file": "app.py",
                    },
                    description=f"Initialize {obs.missing_name} in app.py to provide template context",
                ))
            elif obs.check == CrossLayerCheck.TEMPLATE_INHERITANCE:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="create_template",
                    repair_data={
                        "template_name": obs.missing_name,
                        "target_file": obs.target_file,
                    },
                    description=f"Create parent template '{obs.missing_name}' referenced by {{% extends %}}",
                ))
            elif obs.check == CrossLayerCheck.TEMPLATE_INCLUDE:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="create_template",
                    repair_data={
                        "template_name": obs.missing_name,
                        "target_file": obs.target_file,
                    },
                    description=f"Create included template '{obs.missing_name}' referenced by {{% include %}}",
                ))
            elif obs.check == CrossLayerCheck.STATIC_FILE_EXISTS:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="create_static_file",
                    repair_data={
                        "filename": obs.missing_name,
                        "target_file": obs.target_file,
                    },
                    description=f"Create static file 'static/{obs.missing_name}'",
                ))
            elif obs.check == CrossLayerCheck.IMPORT_PACKAGE:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_requirement",
                    repair_data={
                        "package_name": obs.missing_name,
                        "target_file": "requirements.txt",
                    },
                    description=f"Add '{obs.missing_name}' to requirements.txt",
                ))
            elif obs.check == CrossLayerCheck.MODEL_RELATIONSHIP_CONSISTENCY:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="fix_relationship",
                    repair_data={
                        "relationship": obs.missing_name,
                        "target_file": "models.py",
                    },
                    description=f"Fix relationship '{obs.missing_name}' in models.py",
                ))
            elif obs.check == CrossLayerCheck.CONFIG_ORDERING:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="fix_config_order",
                    repair_data={
                        "config_issue": obs.missing_name,
                        "target_file": "app.py",
                    },
                    description=f"Fix config ordering issue '{obs.missing_name}' in app.py",
                ))
            elif obs.check == CrossLayerCheck.FORM_METHOD_ROUTE:
                endpoint_method = obs.missing_name.split(":")
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_route_method",
                    repair_data={
                        "endpoint": endpoint_method[0] if len(endpoint_method) > 0 else "",
                        "method": endpoint_method[1] if len(endpoint_method) > 1 else "",
                        "target_file": "app.py",
                    },
                    description=(
                        f"Add method '{endpoint_method[1] if len(endpoint_method) > 1 else ''}' "
                        f"to route '{endpoint_method[0] if endpoint_method else ''}' in app.py"
                    ),
                ))
            elif obs.check == CrossLayerCheck.URL_FOR_PARAMS:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="fix_url_for_params",
                    repair_data={
                        "param_info": obs.missing_name,
                        "target_file": obs.source_file,
                    },
                    description=f"Fix url_for() parameter mismatch: {obs.missing_name}",
                ))
            elif obs.check == CrossLayerCheck.PYTHON_SYNTAX:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="fix_syntax",
                    repair_data={
                        "file": obs.source_file,
                        "target_file": obs.source_file,
                    },
                    description=f"Fix syntax error in {obs.source_file}",
                ))
            # CSS_HTML, AUTH_CONSISTENCY, RESPONSE_TYPE_CONSISTENCY,
            # REDIRECT_AFTER_POST, CSS_CUSTOM_PROPERTY, and TEMPLATE_SET_VARS
            # obstructions are warnings; no repair needed
            elif obs.check == CrossLayerCheck.NAVIGATION_REACHABILITY:
                repairs.append(RepairAction(
                    obstruction=obs,
                    repair_type="add_nav_link",
                    repair_data={
                        "endpoint": obs.missing_name,
                        "target_file": "templates/base.html",
                    },
                    description=(
                        f"Add <a href=\"{{{{ url_for('{obs.missing_name}') }}}}\"> "
                        f"to base.html nav or index.html to make '{obs.missing_name}' reachable"
                    ),
                ))
        return repairs
