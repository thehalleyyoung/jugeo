"""App scaffolding — high-level spec builders. Stdlib only."""
from __future__ import annotations

from .models import (
    AppSpec, RouteSpec, ModelSpec, ColumnSpec, ColumnType,
    ResponseType, ConfigSpec, TemplateSpec, FormSpec,
    FormFieldSpec, FormFieldType, StaticFileSpec,
)
from .html_generator import (
    HTMLAppSpec, PageSpec, PageKind,
    ComponentSpec, ComponentKind,
)


class AppScaffolder:
    """High-level builders that produce complete AppSpec objects."""

    # ------------------------------------------------------------------
    # CRUD app
    # ------------------------------------------------------------------

    def scaffold_crud_app(self, name: str, models: list) -> AppSpec:
        routes: list[RouteSpec] = []
        model_specs: list[ModelSpec] = []
        templates: list[TemplateSpec] = []

        routes.append(RouteSpec(url="/", handler_name="index", template="index.html"))
        templates.append(TemplateSpec(name="index.html"))

        for mdict in models:
            mname = mdict["name"]
            lower = mname.lower()
            cols = [
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ] + [
                ColumnSpec(
                    name=c["name"],
                    type=ColumnType(c.get("type", "string")),
                )
                for c in mdict.get("columns", [])
            ]
            model_specs.append(ModelSpec(name=mname, columns=cols))

            routes.extend([
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
            ])
            templates.extend([
                TemplateSpec(name=f"{lower}_list.html"),
                TemplateSpec(name=f"{lower}_detail.html"),
                TemplateSpec(name=f"{lower}_form.html"),
            ])

        return AppSpec(
            name=name,
            routes=routes,
            models=model_specs,
            templates=templates,
            config=ConfigSpec(debug=True),
            dependencies=["flask"],
        )

    # ------------------------------------------------------------------
    # API app
    # ------------------------------------------------------------------

    def scaffold_api_app(self, name: str, resources: list) -> AppSpec:
        routes: list[RouteSpec] = []
        model_specs: list[ModelSpec] = []

        for res in resources:
            rname = res["name"]
            lower = rname.lower()
            cols = [
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ] + [
                ColumnSpec(
                    name=f["name"],
                    type=ColumnType(f.get("type", "string")),
                )
                for f in res.get("fields", [])
            ]
            model_specs.append(ModelSpec(name=rname.capitalize(), columns=cols))

            routes.extend([
                RouteSpec(url=f"/api/{lower}s", handler_name=f"api_{lower}_list",
                          methods=["GET"], response_type=ResponseType.JSON),
                RouteSpec(url=f"/api/{lower}s", handler_name=f"api_{lower}_create",
                          methods=["POST"], response_type=ResponseType.JSON),
                RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_detail",
                          methods=["GET"], response_type=ResponseType.JSON,
                          params=[{"name": "id", "type": "int"}]),
                RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_update",
                          methods=["PUT"], response_type=ResponseType.JSON,
                          params=[{"name": "id", "type": "int"}]),
                RouteSpec(url=f"/api/{lower}s/<int:id>", handler_name=f"api_{lower}_delete",
                          methods=["DELETE"], response_type=ResponseType.JSON,
                          params=[{"name": "id", "type": "int"}]),
            ])

        return AppSpec(
            name=name,
            routes=routes,
            models=model_specs,
            config=ConfigSpec(debug=True),
            dependencies=["flask"],
        )

    # ------------------------------------------------------------------
    # Dashboard app
    # ------------------------------------------------------------------

    def scaffold_dashboard_app(self, name: str, data_sources: list) -> AppSpec:
        routes = [
            RouteSpec(url="/", handler_name="dashboard", template="dashboard.html"),
        ]
        templates = [TemplateSpec(name="dashboard.html")]

        for ds in data_sources:
            lower = ds.get("name", "data").lower()
            routes.append(
                RouteSpec(url=f"/api/{lower}", handler_name=f"api_{lower}",
                          response_type=ResponseType.JSON),
            )

        return AppSpec(
            name=name,
            routes=routes,
            templates=templates,
            config=ConfigSpec(debug=True),
            dependencies=["flask"],
        )

    # ------------------------------------------------------------------
    # Form app
    # ------------------------------------------------------------------

    def scaffold_form_app(self, name: str, forms: list) -> AppSpec:
        routes = [
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ]
        templates = [TemplateSpec(name="index.html")]

        for form in forms:
            fname = form.get("name", "form").lower()
            routes.append(
                RouteSpec(url=f"/{fname}", handler_name=fname,
                          template=f"{fname}.html",
                          methods=["GET", "POST"],
                          response_type=ResponseType.FORM),
            )
            templates.append(TemplateSpec(name=f"{fname}.html"))

        return AppSpec(
            name=name,
            routes=routes,
            templates=templates,
            config=ConfigSpec(debug=True),
            dependencies=["flask"],
        )

    # ------------------------------------------------------------------
    # Description-based scaffolding
    # ------------------------------------------------------------------

    def scaffold_from_description(self, name: str, description: str) -> AppSpec:
        desc_lower = description.lower()

        if "api" in desc_lower or "rest" in desc_lower:
            resources = self._extract_resources(desc_lower)
            if resources:
                return self.scaffold_api_app(name, resources)
            return self.scaffold_api_app(name, [{"name": "item", "fields": [{"name": "name", "type": "string"}]}])

        if "dashboard" in desc_lower:
            return self.scaffold_dashboard_app(name, [{"name": "data", "label": "Data"}])

        if "form" in desc_lower or "survey" in desc_lower:
            return self.scaffold_form_app(name, [{"name": "form"}])

        # Default: CRUD for detected nouns
        nouns = self._extract_nouns(desc_lower)
        if nouns:
            models = [{"name": n.capitalize(), "columns": [{"name": "title", "type": "string"}]} for n in nouns]
            return self.scaffold_crud_app(name, models)

        return AppSpec(
            name=name,
            description=description,
            routes=[RouteSpec(url="/", handler_name="index", template="index.html")],
            templates=[TemplateSpec(name="index.html")],
            config=ConfigSpec(debug=True),
            dependencies=["flask"],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_resources(self, desc: str) -> list:
        common_resources = [
            "user", "post", "comment", "article", "product", "order",
            "item", "task", "project", "tag", "category", "message",
        ]
        found = []
        for r in common_resources:
            if r in desc or r + "s" in desc:
                found.append({"name": r, "fields": [{"name": "name", "type": "string"}]})
        return found

    def _extract_nouns(self, desc: str) -> list:
        common = [
            "post", "comment", "article", "blog", "user", "product",
            "order", "item", "task", "project", "note", "tag", "page",
        ]
        return [n for n in common if n in desc or n + "s" in desc]

    # ------------------------------------------------------------------
    # HTML-only scaffold helpers
    # ------------------------------------------------------------------

    def scaffold_html_landing(self, name: str, **kwargs: str) -> HTMLAppSpec:
        """Scaffold a single-page HTML landing site."""
        title = kwargs.get("title", name.replace("_", " ").title())
        subtitle = kwargs.get("subtitle", "Built with jugeo-webapp")
        nav_items = [
            {"label": "Home", "href": "#"},
            {"label": "Features", "href": "#features"},
            {"label": "About", "href": "#about"},
        ]
        page = PageSpec(
            name="index",
            title=title,
            route="/",
            kind=PageKind.LANDING,
            components=[
                ComponentSpec(kind=ComponentKind.NAVBAR, id="main-nav", props={
                    "brand": title, "items": nav_items,
                }),
                ComponentSpec(kind=ComponentKind.HERO, id="hero", props={
                    "title": title, "subtitle": subtitle,
                    "cta_text": "Get Started", "cta_href": "#features",
                }),
                ComponentSpec(kind=ComponentKind.CUSTOM, id="features", custom_html=(
                    '<section class="section" id="features"><div class="container">'
                    '<h2 class="section-title">Features</h2>'
                    '<div class="card-grid">'
                    + "".join(
                        f'<div class="card fade-in fade-in-{i+1}"><div class="card-icon">{ic}</div>'
                        f'<h3 class="card-title">{t}</h3><div class="card-body">{b}</div></div>'
                        for i, (ic, t, b) in enumerate([
                            ("🔍", "Analysis", "Deep structural analysis"),
                            ("⚡", "Performance", "Optimized for speed"),
                            ("🛡️", "Verified", "Proof-carrying guarantees"),
                        ])
                    )
                    + '</div></div></section>'
                )),
                ComponentSpec(kind=ComponentKind.FOOTER, id="footer", props={
                    "text": f"© 2025 {title} — Generated by jugeo-webapp",
                }),
            ],
        )
        return HTMLAppSpec(name=name, title=title, pages=[page], nav_items=nav_items)

    def scaffold_html_dashboard(self, name: str, panels: list[dict] | None = None) -> HTMLAppSpec:
        """Scaffold an HTML-only dashboard with charts and data panels."""
        title = name.replace("_", " ").title()
        panels = panels or [
            {"title": "Overview", "kind": "chart", "chart_type": "bar"},
            {"title": "Recent Activity", "kind": "table",
             "headers": ["Time", "Event", "Status"],
             "rows": [["Now", "Initialized", "Active"]]},
        ]
        comps: list[ComponentSpec] = [
            ComponentSpec(kind=ComponentKind.NAVBAR, id="dash-nav", props={
                "brand": title,
                "items": [{"label": "Dashboard", "href": "#"}, {"label": "Settings", "href": "#settings"}],
            }),
        ]
        panel_cards = []
        for i, p in enumerate(panels):
            if p.get("kind") == "chart":
                panel_cards.append(ComponentSpec(kind=ComponentKind.CHART, id=f"chart-{i}", props={
                    "chart_type": p.get("chart_type", "bar"),
                    "width": p.get("width", 600),
                    "height": p.get("height", 350),
                }))
            elif p.get("kind") == "table":
                panel_cards.append(ComponentSpec(kind=ComponentKind.TABLE, id=f"table-{i}", props={
                    "headers": p.get("headers", []),
                    "rows": p.get("rows", []),
                }))
            else:
                panel_cards.append(ComponentSpec(kind=ComponentKind.CARD, id=f"panel-{i}", props={
                    "title": p.get("title", "Panel"),
                    "body": p.get("body", ""),
                }))
        comps.append(ComponentSpec(kind=ComponentKind.CUSTOM, id="dashboard-grid", custom_html=(
            '<main class="container" style="padding-top:calc(var(--nav-height) + 2rem);">'
            + f'<h1>{title}</h1>'
            + '<div class="card-grid">'
        )))
        comps.extend(panel_cards)
        comps.append(ComponentSpec(kind=ComponentKind.CUSTOM, id="dashboard-close", custom_html='</div></main>'))
        comps.append(ComponentSpec(kind=ComponentKind.FOOTER, id="footer", props={
            "text": f"Generated by jugeo-webapp",
        }))

        page = PageSpec(name="index", title=title, route="/", kind=PageKind.DASHBOARD, components=comps)
        return HTMLAppSpec(name=name, title=title, pages=[page])

    def scaffold_html_interactive(self, name: str, description: str = "") -> HTMLAppSpec:
        """Scaffold an interactive HTML-only app using localStorage as backend."""
        title = name.replace("_", " ").title()
        nav_items = [
            {"label": "Home", "href": "#/"},
            {"label": "Data", "href": "#/data"},
            {"label": "About", "href": "#/about"},
        ]
        page = PageSpec(
            name="index",
            title=title,
            route="/",
            kind=PageKind.INTERACTIVE,
            components=[
                ComponentSpec(kind=ComponentKind.NAVBAR, id="main-nav", props={
                    "brand": title, "items": nav_items,
                }),
                ComponentSpec(kind=ComponentKind.CUSTOM, id="app-root", custom_html=(
                    '<main class="container" style="padding-top:calc(var(--nav-height) + 2rem);">'
                    '<div id="app"></div></main>'
                )),
                ComponentSpec(kind=ComponentKind.TOAST, id="toasts"),
                ComponentSpec(kind=ComponentKind.FOOTER, id="footer", props={
                    "text": f"© 2025 {title} — Built with jugeo-webapp",
                }),
            ],
            custom_js="""
// Client-side SPA using JugeoRouter + JugeoStore
const store = new JugeoStore('""" + name + """');
const router = new JugeoRouter();
const app = document.getElementById('app');

router.on('/', () => {
  app.innerHTML = '<h1>Welcome to """ + title + """</h1><p>Navigate using the menu above.</p>';
});
router.on('/data', () => {
  const items = store.all();
  const rows = Object.entries(items).map(([k, v]) => `<tr><td>${k}</td><td>${JSON.stringify(v)}</td></tr>`).join('');
  app.innerHTML = '<h2>Data Store</h2><div class="table-container"><table class="data-table"><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody>' + (rows || '<tr><td colspan="2">No data yet</td></tr>') + '</tbody></table></div>';
});
router.on('/about', () => {
  app.innerHTML = '<h2>About</h2><p>This is a jugeo-webapp generated HTML-only application. No server required.</p>';
});
router.on('*', (path) => {
  app.innerHTML = '<h2>404</h2><p>Page not found: ' + path + '</p>';
});
router.start();
""",
        )
        return HTMLAppSpec(name=name, title=title, description=description, pages=[page], nav_items=nav_items)

    def scaffold_html_from_description(self, name: str, description: str) -> HTMLAppSpec:
        """Auto-detect the best HTML-only scaffold based on a description."""
        desc_lower = description.lower()
        if "dashboard" in desc_lower or "chart" in desc_lower or "analytics" in desc_lower:
            return self.scaffold_html_dashboard(name)
        if "interactive" in desc_lower or "spa" in desc_lower or "app" in desc_lower:
            return self.scaffold_html_interactive(name, description)
        return self.scaffold_html_landing(name, title=name.replace("_", " ").title(), subtitle=description)


class SpecValidator:
    """Validates an AppSpec for completeness and consistency."""

    def validate(self, spec: AppSpec) -> list:
        errors: list[str] = []
        if not spec.name:
            errors.append("App name is required")

        route_urls = {r.url for r in spec.routes}
        template_names = {t.name for t in spec.templates}

        for route in spec.routes:
            if route.template and route.template not in template_names:
                pass  # templates are auto-generated so this is just advisory

            if not route.handler_name and not route.url:
                errors.append("Route must have url or handler_name")

        # Check for duplicate handler names
        handlers = [r.handler_name for r in spec.routes if r.handler_name]
        seen: set[str] = set()
        for h in handlers:
            if h in seen:
                errors.append(f"Duplicate handler name: {h}")
            seen.add(h)

        # Check model foreign keys
        table_names = {m.table_name for m in spec.models}
        for model in spec.models:
            for col in model.columns:
                if isinstance(col, ColumnSpec) and col.foreign_key:
                    ref_table = col.foreign_key.split(".")[0]
                    if ref_table not in table_names:
                        errors.append(f"Foreign key '{col.foreign_key}' references unknown table '{ref_table}'")

        # Check auth routes have login
        auth_routes = [r for r in spec.routes if r.auth_required]
        if auth_routes:
            login_urls = [r.url for r in spec.routes if "login" in (r.handler_name or "").lower() or "login" in r.url.lower()]
            if not login_urls:
                errors.append("Auth-required routes exist but no login route defined")

        return errors
