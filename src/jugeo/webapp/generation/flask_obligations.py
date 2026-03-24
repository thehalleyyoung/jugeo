"""Flask-app obligation presheaf — typed quality enforcement.

Parallel to the HTML-only obligation system in ``html_generator.py``,
this module defines obligations over Flask ``AppSpec`` objects and an
enrichment engine that inflates sparse specs into rich, full-featured
applications.

From the JG perspective, each obligation is a stalk of the obligation
presheaf O over the web application site.  The obligation
checker is the descent verifier: it checks whether the produced
AppSpec satisfies the sheaf condition (all obligations met).  The
enricher is the repair functor: given an obstruction report, it
applies the minimal structural additions to restore descent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import (
    AppSpec,
    RouteSpec,
    ModelSpec,
    ColumnSpec,
    ColumnType,
    ResponseType,
    ConfigSpec,
    TemplateSpec,
    StaticFileSpec,
    FormSpec,
    FormFieldSpec,
    FormFieldType,
    GenerationResult,
)
from .obligations import (
    Obligation,
    ObligationKind,
    ObligationResult,
    ObligationReport,
    resolve_obligations,
    enforce_obligations,
    GenerationTarget,
)


# ── Checker ───────────────────────────────────────────────────────────

class FlaskObligationChecker:
    """Measures an AppSpec against Flask obligations."""

    def check(self, spec: AppSpec, obligations: list[Obligation]) -> ObligationReport:
        results = [self._check_one(spec, ob) for ob in obligations]
        return ObligationReport(
            results=results,
            all_met=all(r.met for r in results),
        )

    def _check_one(self, spec: AppSpec, ob: Obligation) -> ObligationResult:
        actual = self._measure(spec, ob.kind)
        met = actual >= ob.minimum
        return ObligationResult(
            obligation=ob, actual=actual, met=met,
            deficit=max(0, ob.minimum - actual),
        )

    def _measure(self, spec: AppSpec, kind: ObligationKind) -> float:
        if kind == ObligationKind.ROUTE_COUNT:
            return len(spec.routes)
        if kind == ObligationKind.MODEL_COUNT:
            return len(spec.models)
        if kind == ObligationKind.TEMPLATE_COUNT:
            return len(spec.templates)
        if kind == ObligationKind.STATIC_FILE_COUNT:
            return len(spec.static_files)
        if kind == ObligationKind.API_ENDPOINT_COUNT:
            return sum(1 for r in spec.routes if r.response_type == ResponseType.JSON)
        if kind == ObligationKind.FORM_COUNT:
            return sum(1 for r in spec.routes if r.response_type == ResponseType.FORM)
        if kind == ObligationKind.AUTH_PRESENT:
            return 1.0 if any(r.auth_required for r in spec.routes) or any(
                "login" in (r.handler_name or "").lower() for r in spec.routes
            ) else 0.0
        if kind == ObligationKind.ERROR_HANDLING:
            error_names = {t.name for t in spec.templates}
            return sum(1 for n in ["404.html", "500.html"] if n in error_names)
        if kind == ObligationKind.CSS_LINE_COUNT:
            return sum(
                sf.content.count("\n") + 1
                for sf in spec.static_files
                if sf.path.endswith(".css") and sf.content
            )
        if kind == ObligationKind.JS_LINE_COUNT:
            return sum(
                sf.content.count("\n") + 1
                for sf in spec.static_files
                if sf.path.endswith(".js") and sf.content
            )
        if kind == ObligationKind.NAVIGATION_DEPTH:
            return sum(1 for r in spec.routes if r.response_type in (ResponseType.TEMPLATE, ResponseType.FORM))
        if kind == ObligationKind.CRUD_COMPLETENESS:
            crud_methods = {"list", "detail", "create", "edit", "delete"}
            total = 0
            for m in spec.models:
                lower = m.name.lower()
                names = {(r.handler_name or "").lower() for r in spec.routes}
                total += sum(1 for op in crud_methods if f"{lower}_{op}" in names)
            return total
        if kind == ObligationKind.DATABASE_SCHEMA_DEPTH:
            if not spec.models:
                return 0
            return min(len(m.columns) for m in spec.models) if spec.models else 0
        if kind == ObligationKind.BLUEPRINT_COUNT:
            return len(spec.blueprints)
        if kind == ObligationKind.OVERALL_FILE_COUNT:
            base = 3  # main, config, requirements
            if spec.models:
                base += 2  # models.py + init_db.py
            if spec.routes:
                base += 1  # routes.py
            base += len(spec.templates) + 2  # +2 for error templates the generator adds
            base += max(2, len(spec.static_files))  # base.css + base.js at minimum
            return base
        if kind == ObligationKind.TOTAL_LINE_COUNT:
            css = sum(sf.content.count("\n") + 1 for sf in spec.static_files if sf.path.endswith(".css") and sf.content)
            js = sum(sf.content.count("\n") + 1 for sf in spec.static_files if sf.path.endswith(".js") and sf.content)
            html = sum(len(t.blocks.get("content", "").split("\n")) for t in spec.templates if t.blocks)
            return css + js + html
        if kind == ObligationKind.FEATURE_SYSTEM_COUNT:
            all_js = " ".join(sf.content for sf in spec.static_files if sf.path.endswith(".js") and sf.content).lower()
            markers = [
                "class ", "engine", "renderer", "manager", "controller",
                "system", "generator", "analyzer", "synthesizer", "emitter",
                "automata", "territory", "gallery", "tutorial", "audio",
                "particle", "fractal", "combat", "scoring",
            ]
            return sum(1 for m in markers if m in all_js)
        if kind == ObligationKind.MODULE_COUNT:
            all_js = " ".join(sf.content for sf in spec.static_files if sf.path.endswith(".js") and sf.content)
            boundaries = 0
            for marker in ["// ── ", "// === ", "class ", "const module"]:
                boundaries += all_js.count(marker)
            return min(boundaries, 30)
        if kind == ObligationKind.ALGORITHM_VARIETY:
            all_js = " ".join(sf.content for sf in spec.static_files if sf.path.endswith(".js") and sf.content).lower()
            algorithms = [
                "perlin", "simplex", "noise", "cellular", "automata",
                "fractal", "mandelbrot", "julia", "lsystem", "l-system",
                "particle", "pathfind", "minimax", "floodfill",
                "voronoi", "interpolat", "bezier", "spline", "fft",
            ]
            return sum(1 for a in algorithms if a in all_js)
        if kind == ObligationKind.INTERACTION_PATTERN_COUNT:
            all_js = " ".join(sf.content for sf in spec.static_files if sf.path.endswith(".js") and sf.content).lower()
            patterns = [
                "click", "mousemove", "mousedown", "mouseup", "wheel",
                "keydown", "keyup", "touchstart", "touchmove", "drag",
                "resize", "scroll", "pointerdown", "contextmenu", "input", "change",
            ]
            return sum(1 for p in patterns if p in all_js)
        return 0


# ── Enricher ──────────────────────────────────────────────────────────

class FlaskSpecEnricher:
    """Enriches an AppSpec to meet unmet Flask obligations.

    The repair functor for the Flask obligation presheaf: given
    obstruction reports, applies minimal structural additions.
    """

    def enrich(self, spec: AppSpec, unmet: list[ObligationResult]) -> AppSpec:
        for result in unmet:
            method = getattr(self, f"_enrich_{result.obligation.kind.value}", None)
            if method:
                spec = method(spec, result)
        return spec

    # ── Per-obligation enrichers ──────────────────────────────────────

    def _enrich_route_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add routes: index, about, dashboard, search, settings, API endpoints."""
        existing_handlers = {r.handler_name for r in spec.routes}
        candidates = [
            RouteSpec(url="/", handler_name="index", template="index.html"),
            RouteSpec(url="/about", handler_name="about", template="about.html"),
            RouteSpec(url="/dashboard", handler_name="dashboard", template="dashboard.html"),
            RouteSpec(url="/search", handler_name="search", template="search.html",
                      methods=["GET", "POST"]),
            RouteSpec(url="/settings", handler_name="settings", template="settings.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url="/api/stats", handler_name="api_stats",
                      methods=["GET"], response_type=ResponseType.JSON),
            RouteSpec(url="/api/search", handler_name="api_search",
                      methods=["GET"], response_type=ResponseType.JSON),
            RouteSpec(url="/api/health", handler_name="api_health",
                      methods=["GET"], response_type=ResponseType.JSON),
        ]
        for c in candidates:
            if len(spec.routes) >= int(result.obligation.minimum):
                break
            if c.handler_name not in existing_handlers:
                spec.routes.append(c)
                existing_handlers.add(c.handler_name)
        return spec

    def _enrich_model_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add rich database models with proper schemas."""
        existing_names = {m.name for m in spec.models}
        candidates = [
            ModelSpec(
                name="User",
                columns=[
                    ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                    ColumnSpec(name="username", type=ColumnType.STRING, unique=True, nullable=False),
                    ColumnSpec(name="email", type=ColumnType.STRING, unique=True, nullable=False),
                    ColumnSpec(name="password_hash", type=ColumnType.STRING, nullable=False),
                    ColumnSpec(name="display_name", type=ColumnType.STRING),
                    ColumnSpec(name="bio", type=ColumnType.TEXT),
                    ColumnSpec(name="is_active", type=ColumnType.BOOLEAN, default=True),
                    ColumnSpec(name="created_at", type=ColumnType.DATETIME),
                ],
            ),
            ModelSpec(
                name="Project",
                columns=[
                    ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                    ColumnSpec(name="title", type=ColumnType.STRING, nullable=False),
                    ColumnSpec(name="description", type=ColumnType.TEXT),
                    ColumnSpec(name="status", type=ColumnType.STRING, default="active"),
                    ColumnSpec(name="priority", type=ColumnType.INTEGER, default=0),
                    ColumnSpec(name="owner_id", type=ColumnType.INTEGER, foreign_key="users.id"),
                    ColumnSpec(name="created_at", type=ColumnType.DATETIME),
                    ColumnSpec(name="updated_at", type=ColumnType.DATETIME),
                ],
                indexes=["status", "owner_id"],
            ),
            ModelSpec(
                name="Activity",
                columns=[
                    ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                    ColumnSpec(name="project_id", type=ColumnType.INTEGER, foreign_key="projects.id"),
                    ColumnSpec(name="user_id", type=ColumnType.INTEGER, foreign_key="users.id"),
                    ColumnSpec(name="action", type=ColumnType.STRING, nullable=False),
                    ColumnSpec(name="details", type=ColumnType.TEXT),
                    ColumnSpec(name="created_at", type=ColumnType.DATETIME),
                ],
                indexes=["project_id", "user_id"],
            ),
        ]
        for m in candidates:
            if len(spec.models) >= int(result.obligation.minimum):
                break
            if m.name not in existing_names:
                spec.models.append(m)
                existing_names.add(m.name)
                self._add_crud_routes(spec, m)
        return spec

    def _add_crud_routes(self, spec: AppSpec, model: ModelSpec) -> None:
        """Add full CRUD routes for a model."""
        lower = model.name.lower()
        existing = {r.handler_name for r in spec.routes}
        crud = [
            RouteSpec(url=f"/{lower}s", handler_name=f"{lower}_list",
                      template=f"{lower}_list.html"),
            RouteSpec(url=f"/{lower}s/<int:id>", handler_name=f"{lower}_detail",
                      template=f"{lower}_detail.html",
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/{lower}s/create", handler_name=f"{lower}_create",
                      template=f"{lower}_form.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url=f"/{lower}s/<int:id>/edit", handler_name=f"{lower}_edit",
                      template=f"{lower}_form.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM,
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/{lower}s/<int:id>/delete", handler_name=f"{lower}_delete",
                      methods=["POST"], response_type=ResponseType.REDIRECT,
                      params=[{"name": "id", "type": "int"}]),
            RouteSpec(url=f"/api/{lower}s", handler_name=f"api_{lower}_list",
                      methods=["GET"], response_type=ResponseType.JSON),
        ]
        for r in crud:
            if r.handler_name not in existing:
                spec.routes.append(r)
                existing.add(r.handler_name)

    def _enrich_template_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add templates for all template-serving routes."""
        existing = {t.name for t in spec.templates}
        for route in spec.routes:
            if route.template and route.template not in existing:
                spec.templates.append(TemplateSpec(name=route.template))
                existing.add(route.template)
        # Error templates
        for err in ["404.html", "500.html"]:
            if err not in existing:
                spec.templates.append(TemplateSpec(name=err))
                existing.add(err)
        # About page
        if "about.html" not in existing and len(spec.templates) < int(result.obligation.minimum):
            spec.templates.append(TemplateSpec(name="about.html"))
        return spec

    def _enrich_static_file_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add rich static assets."""
        existing_paths = {sf.path for sf in spec.static_files}
        if "css/app.css" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="css/app.css",
                content_type="text/css",
                content=_RICH_APP_CSS,
            ))
        if "js/app.js" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="js/app.js",
                content_type="application/javascript",
                content=_RICH_APP_JS,
            ))
        if "js/charts.js" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="js/charts.js",
                content_type="application/javascript",
                content=_CHARTS_JS,
            ))
        return spec

    def _enrich_api_endpoint_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add JSON API endpoints."""
        existing = {r.handler_name for r in spec.routes}
        apis = [
            RouteSpec(url="/api/stats", handler_name="api_stats",
                      methods=["GET"], response_type=ResponseType.JSON),
            RouteSpec(url="/api/health", handler_name="api_health",
                      methods=["GET"], response_type=ResponseType.JSON),
            RouteSpec(url="/api/search", handler_name="api_search",
                      methods=["GET"], response_type=ResponseType.JSON),
        ]
        for a in apis:
            if a.handler_name not in existing:
                spec.routes.append(a)
                existing.add(a.handler_name)
        return spec

    def _enrich_form_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add form routes if missing."""
        existing = {r.handler_name for r in spec.routes}
        forms = [
            RouteSpec(url="/settings", handler_name="settings",
                      template="settings.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
            RouteSpec(url="/feedback", handler_name="feedback",
                      template="feedback.html",
                      methods=["GET", "POST"], response_type=ResponseType.FORM),
        ]
        for f in forms:
            if f.handler_name not in existing:
                spec.routes.append(f)
                existing.add(f.handler_name)
                if f.template:
                    tmpl_names = {t.name for t in spec.templates}
                    if f.template not in tmpl_names:
                        spec.templates.append(TemplateSpec(name=f.template))
        return spec

    def _enrich_css_line_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add rich CSS static file."""
        existing_paths = {sf.path for sf in spec.static_files}
        if "css/app.css" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="css/app.css", content_type="text/css", content=_RICH_APP_CSS,
            ))
        if "css/animations.css" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="css/animations.css", content_type="text/css", content=_ANIMATIONS_CSS,
            ))
        if "css/responsive.css" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="css/responsive.css", content_type="text/css", content=_RESPONSIVE_CSS,
            ))
        return spec

    def _enrich_js_line_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        """Add rich JS static files."""
        existing_paths = {sf.path for sf in spec.static_files}
        if "js/app.js" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="js/app.js", content_type="application/javascript", content=_RICH_APP_JS,
            ))
        if "js/charts.js" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="js/charts.js", content_type="application/javascript", content=_CHARTS_JS,
            ))
        if "js/forms.js" not in existing_paths:
            spec.static_files.append(StaticFileSpec(
                path="js/forms.js", content_type="application/javascript", content=_FORMS_JS,
            ))
        return spec

    def _enrich_navigation_depth(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        return self._enrich_route_count(spec, result)

    def _enrich_crud_completeness(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        for m in spec.models:
            self._add_crud_routes(spec, m)
        return spec

    def _enrich_database_schema_depth(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        return self._enrich_model_count(spec, result)

    def _enrich_error_handling(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        existing = {t.name for t in spec.templates}
        for err in ["404.html", "500.html"]:
            if err not in existing:
                spec.templates.append(TemplateSpec(name=err))
        return spec

    def _enrich_overall_file_count(self, spec: AppSpec, result: ObligationResult) -> AppSpec:
        # Collective enrichments handle this
        return spec


# ══════════════════════════════════════════════════════════════════════
# Rich static content strings
# ══════════════════════════════════════════════════════════════════════

_RICH_APP_CSS = """\
/* ═══ Generated by jugeo-webapp — app-specific CSS ═══ */
:root {
  --color-primary: #6366f1;
  --color-primary-light: #818cf8;
  --color-primary-dark: #4338ca;
  --color-accent: #f59e0b;
  --color-success: #10b981;
  --color-danger: #ef4444;
  --color-warning: #f59e0b;
  --color-bg-dark: #0f172a;
  --color-bg-card: #1e293b;
  --color-text: #e2e8f0;
  --color-text-muted: #94a3b8;
  --color-border: #334155;
  --radius: 8px;
  --radius-lg: 16px;
  --shadow: 0 4px 24px rgba(0,0,0,.2);
  --transition: .25s cubic-bezier(.4,0,.2,1);
}

/* Dark theme override */
body.dark-theme {
  background: var(--color-bg-dark);
  color: var(--color-text);
}

/* Card grid */
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; }
.app-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.5rem;
  transition: all var(--transition);
}
.app-card:hover {
  border-color: var(--color-primary);
  transform: translateY(-4px);
  box-shadow: var(--shadow);
}
.app-card-icon { font-size: 2rem; margin-bottom: .75rem; }
.app-card-title { font-size: 1.2rem; font-weight: 700; margin-bottom: .5rem; }

/* Stats */
.stat-row { display: flex; gap: 1rem; flex-wrap: wrap; }
.stat-badge {
  display: flex; flex-direction: column; align-items: center;
  padding: 1rem 1.5rem; background: var(--color-bg-card);
  border-radius: var(--radius); border: 1px solid var(--color-border);
}
.stat-value { font-size: 1.75rem; font-weight: 800; color: var(--color-primary-light); }
.stat-label { font-size: .8rem; color: var(--color-text-muted); }

/* Gradient text */
.gradient-text {
  background: linear-gradient(135deg, var(--color-primary-light), var(--color-accent));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}

/* Toast */
.toast-container { position: fixed; bottom: 1rem; right: 1rem; z-index: 3000; }
.toast {
  padding: .75rem 1.25rem; border-radius: var(--radius);
  background: var(--color-bg-card); border: 1px solid var(--color-border);
  margin-top: .5rem; box-shadow: var(--shadow);
  animation: slideIn .3s ease;
}
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Tables */
.data-table { width: 100%; border-collapse: collapse; }
.data-table th, .data-table td { padding: .75rem; text-align: left; border-bottom: 1px solid var(--color-border); }
.data-table th { font-weight: 600; color: var(--color-primary-light); }
.data-table tr:hover td { background: rgba(99,102,241,.05); }

/* Forms */
.form-group { margin-bottom: 1.25rem; }
.form-group label { display: block; margin-bottom: .4rem; font-weight: 500; }
.form-control {
  width: 100%; padding: .6rem .9rem; border-radius: var(--radius);
  border: 1px solid var(--color-border); background: var(--color-bg-dark);
  color: var(--color-text); transition: border-color var(--transition);
}
.form-control:focus { outline: none; border-color: var(--color-primary); }

/* Badges */
.badge { display: inline-block; padding: .2rem .6rem; border-radius: 12px; font-size: .75rem; font-weight: 600; }
.badge-success { background: rgba(16,185,129,.15); color: #10b981; }
.badge-warning { background: rgba(245,158,11,.15); color: #f59e0b; }
.badge-danger { background: rgba(239,68,68,.15); color: #ef4444; }
"""

_ANIMATIONS_CSS = """\
/* ═══ Generated by jugeo-webapp — animations ═══ */
@keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
@keyframes fadeInLeft { from { opacity: 0; transform: translateX(-20px); } to { opacity: 1; transform: translateX(0); } }
@keyframes scaleIn { from { transform: scale(.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
@keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .6; } }
@keyframes gradientShift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }

.animate-fade-up { animation: fadeInUp .6s ease forwards; opacity: 0; }
.animate-fade-left { animation: fadeInLeft .6s ease forwards; opacity: 0; }
.animate-scale { animation: scaleIn .5s ease forwards; opacity: 0; }
.animate-shimmer { background: linear-gradient(90deg, transparent, rgba(255,255,255,.03), transparent); background-size: 200% 100%; animation: shimmer 2s infinite; }
.animate-pulse { animation: pulse 2s infinite; }
.delay-1 { animation-delay: .1s; } .delay-2 { animation-delay: .2s; }
.delay-3 { animation-delay: .3s; } .delay-4 { animation-delay: .4s; }
"""

_RESPONSIVE_CSS = """\
/* ═══ Generated by jugeo-webapp — responsive breakpoints ═══ */
@media (max-width: 480px) {
  .card-grid { grid-template-columns: 1fr; }
  .stat-row { flex-direction: column; }
  .hide-mobile { display: none; }
  h1 { font-size: 1.5rem; }
}
@media (min-width: 481px) and (max-width: 768px) {
  .card-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 769px) and (max-width: 1024px) {
  .container { max-width: 960px; }
}
@media (min-width: 1025px) {
  .container { max-width: 1200px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
"""

_RICH_APP_JS = """\
/* ═══ Generated by jugeo-webapp — app interactivity ═══ */
'use strict';

/* Toast notifications */
window.showToast = function(message, type, duration) {
  type = type || 'info'; duration = duration || 3000;
  var container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(function() { toast.style.opacity = '0'; setTimeout(function() { toast.remove(); }, 300); }, duration);
};

/* Scroll progress bar */
(function() {
  var bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;z-index:9999;transition:width .1s;width:0;background:linear-gradient(90deg,#6366f1,#f59e0b);';
  document.body.appendChild(bar);
  window.addEventListener('scroll', function() {
    var pct = window.scrollY / (document.documentElement.scrollHeight - window.innerHeight) * 100;
    bar.style.width = Math.min(pct, 100) + '%';
  });
})();

/* Back to top */
(function() {
  var btn = document.createElement('button');
  btn.innerHTML = '&uarr;';
  btn.style.cssText = 'position:fixed;bottom:2rem;right:2rem;width:44px;height:44px;border-radius:50%;background:#6366f1;color:#fff;border:none;font-size:1.2rem;cursor:pointer;display:none;z-index:1500;';
  document.body.appendChild(btn);
  window.addEventListener('scroll', function() { btn.style.display = window.scrollY > 300 ? 'block' : 'none'; });
  btn.addEventListener('click', function() { window.scrollTo({top:0, behavior:'smooth'}); });
})();

/* Flash message auto-dismiss */
document.querySelectorAll('.flash-message').forEach(function(el) {
  setTimeout(function() { el.style.opacity = '0'; setTimeout(function() { el.remove(); }, 300); }, 4000);
});

/* Confirm delete */
document.querySelectorAll('[data-confirm]').forEach(function(el) {
  el.addEventListener('click', function(e) {
    if (!confirm(el.dataset.confirm || 'Are you sure?')) e.preventDefault();
  });
});

/* AJAX form submission */
document.querySelectorAll('form[data-ajax]').forEach(function(form) {
  form.addEventListener('submit', function(e) {
    e.preventDefault();
    var formData = new FormData(form);
    fetch(form.action, { method: form.method || 'POST', body: formData })
      .then(function(r) { return r.json(); })
      .then(function(data) { showToast(data.message || 'Success!', 'success'); })
      .catch(function() { showToast('An error occurred', 'error'); });
  });
});
"""

_CHARTS_JS = """\
/* ═══ Generated by jugeo-webapp — simple canvas charts ═══ */
'use strict';

window.JugeoChart = {
  bar: function(canvasId, data, options) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    options = options || {};
    var maxVal = Math.max.apply(null, data.map(function(d) { return d.value; }));
    var barW = options.barWidth || 50;
    var gap = options.gap || 20;
    var startX = 60, startY = canvas.height - 40;
    var chartH = startY - 20;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = options.textColor || '#94a3b8';
    ctx.font = '12px sans-serif';

    data.forEach(function(d, i) {
      var x = startX + i * (barW + gap);
      var h = (d.value / maxVal) * chartH;
      var gradient = ctx.createLinearGradient(x, startY - h, x, startY);
      gradient.addColorStop(0, d.color || '#818cf8');
      gradient.addColorStop(1, d.colorEnd || '#4338ca');
      ctx.fillStyle = gradient;
      ctx.fillRect(x, startY - h, barW, h);
      ctx.fillStyle = options.textColor || '#94a3b8';
      ctx.textAlign = 'center';
      ctx.fillText(d.label || '', x + barW/2, startY + 16);
      ctx.fillText(d.value.toString(), x + barW/2, startY - h - 6);
    });
  },

  line: function(canvasId, data, options) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    options = options || {};
    var maxVal = Math.max.apply(null, data.map(function(d) { return d.value; }));
    var startX = 60, startY = canvas.height - 40, chartH = startY - 20;
    var stepX = (canvas.width - startX - 20) / Math.max(data.length - 1, 1);

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = options.lineColor || '#818cf8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach(function(d, i) {
      var x = startX + i * stepX;
      var y = startY - (d.value / maxVal) * chartH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    data.forEach(function(d, i) {
      var x = startX + i * stepX;
      var y = startY - (d.value / maxVal) * chartH;
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = options.dotColor || '#f59e0b';
      ctx.fill();
    });
  },

  pie: function(canvasId, data, options) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    options = options || {};
    var cx = canvas.width / 2, cy = canvas.height / 2;
    var r = Math.min(cx, cy) - 30;
    var total = data.reduce(function(s, d) { return s + d.value; }, 0);
    var colors = ['#6366f1','#f59e0b','#10b981','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    var angle = -Math.PI / 2;
    data.forEach(function(d, i) {
      var slice = (d.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, angle, angle + slice);
      ctx.fillStyle = d.color || colors[i % colors.length];
      ctx.fill();
      angle += slice;
    });
  }
};
"""

_FORMS_JS = """\
/* ═══ Generated by jugeo-webapp — form validation ═══ */
'use strict';

window.JugeoForms = {
  validate: function(form) {
    var valid = true;
    form.querySelectorAll('[required]').forEach(function(field) {
      var value = field.value.trim();
      var group = field.closest('.form-group');
      var error = group ? group.querySelector('.field-error') : null;

      if (!value) {
        valid = false;
        field.style.borderColor = '#ef4444';
        if (error) error.textContent = 'This field is required';
        else if (group) {
          var span = document.createElement('span');
          span.className = 'field-error';
          span.style.cssText = 'color:#ef4444;font-size:.8rem;';
          span.textContent = 'This field is required';
          group.appendChild(span);
        }
      } else {
        field.style.borderColor = '';
        if (error) error.textContent = '';

        // Email validation
        if (field.type === 'email' && !/^[^@]+@[^@]+\\.[^@]+$/.test(value)) {
          valid = false;
          field.style.borderColor = '#ef4444';
          if (error) error.textContent = 'Invalid email address';
        }
      }
    });
    return valid;
  },

  init: function() {
    document.querySelectorAll('form[data-validate]').forEach(function(form) {
      form.addEventListener('submit', function(e) {
        if (!JugeoForms.validate(form)) {
          e.preventDefault();
          if (window.showToast) showToast('Please fix the errors above', 'error');
        }
      });
    });
  }
};

document.addEventListener('DOMContentLoaded', function() { JugeoForms.init(); });
"""


# ── Backward compatibility aliases ────────────────────────────────────
FlaskObligation = Obligation
FlaskObligationKind = ObligationKind
FlaskObligationResult = ObligationResult
FlaskObligationReport = ObligationReport
FLASK_OBLIGATION_PRESETS: dict[str, list[Obligation]] = {
    "minimal": resolve_obligations("minimal", GenerationTarget.FLASK),
    "standard": resolve_obligations("standard", GenerationTarget.FLASK),
    "stunning": resolve_obligations("stunning", GenerationTarget.FLASK),
}
