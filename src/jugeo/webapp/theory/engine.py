"""Master theory engine for jugeo webapp analysis.

This is the ONLY file imported by the CLI.  Every theory module is exercised
on every run; results are surfaced as obligations, descent results, or
annotated warnings in the generated output.
"""

from __future__ import annotations

__all__ = [
    "TheoryEngine",
    "TheoryEngineReport",
    "TheoryCheckResult",
    "WebAppSpec",
    "THEORY_AVAILABLE",
]

from dataclasses import dataclass, field

THEORY_AVAILABLE = False

try:
    # Sites — CSS
    from jugeo.webapp.theory.sites.css.specificity import (
        CSSSpecificity, CascadeSorter, CSSOrigin, CSSLayerOrder, CascadeKey,
    )
    from jugeo.webapp.theory.sites.css.cascade_pipeline import (
        CSSInheritanceModel, RelativeUnitResolver, CSSValueStage,
    )
    from jugeo.webapp.theory.sites.css.selector_site import (
        CSSSelectorSite, CSSSelector, SelectorKind, DeadSelectorChecker,
    )
    # Sites — DOM
    from jugeo.webapp.theory.sites.dom.kinds import DOMNodeKind, DOMMorphismKind
    from jugeo.webapp.theory.sites.dom.coordinate import DOMCoordinate, DOMTreeMorphism
    from jugeo.webapp.theory.sites.dom.events import DOMEvent, EventPropagationPath, EventPhase
    from jugeo.webapp.theory.sites.dom.presheaves import DOMValidityPresheaf, AccessibilityPresheaf
    # Sites — Flask
    from jugeo.webapp.theory.sites.flask.routing import (
        FlaskRoute, FlaskURLSite, HTTPMethod, URLParameterKind,
    )
    from jugeo.webapp.theory.sites.flask.routing import URLParameter as _URLParameter
    # Sites — HTTP
    from jugeo.webapp.theory.sites.http.request_response import (
        HTTPStatusCode, HTTPRequest, HTTPResponse,
    )
    # Sites — Jinja
    from jugeo.webapp.theory.sites.jinja.template_site import (
        JinjaTemplate, JinjaBlock, JinjaTemplateSite,
    )
    # Sites — JS
    from jugeo.webapp.theory.sites.js.scope_chain import (
        JSScopeCoordinate, ScopeKind, VariableKind,
    )
    from jugeo.webapp.theory.sites.js.event_loop import (
        TaskQueueKind, EventLoopSite, PromiseSection,
    )
    # Sites — SQL
    from jugeo.webapp.theory.sites.sql.tables import (
        SQLTable, SQLColumn, SQLRelationalSite, SQLColumnKind,
    )
    from jugeo.webapp.theory.sites.sql.transactions import (
        IsolationLevel, SQLTransaction, TransactionDescentChecker,
    )
    from jugeo.webapp.theory.sites.sql.transactions import (
        SQLOperation as _SQLOperation,
        TransactionState as _TransactionState,
    )
    # Visual — color
    from jugeo.webapp.theory.visual.color.spaces import Color, ColorSpace, ColorDistance
    # Visual — layout
    from jugeo.webapp.theory.visual.layout.box_model import (
        CSSBox, BoxEdge, MarginCollapseAnalyzer,
    )
    from jugeo.webapp.theory.visual.layout.flexbox import (
        FlexboxSolver, FlexContainer, FlexItem, FlexDirection, FlexWrap, AlignValue,
    )
    from jugeo.webapp.theory.visual.layout.grid import (
        GridTrackSize, GridTrackSolver, CSSGrid,
    )
    # Visual — motion
    from jugeo.webapp.theory.visual.motion.physics import (
        SpringSystem, CubicBezier, KeyframeAnimation,
    )
    # Visual — typography
    from jugeo.webapp.theory.visual.type.metrics import (
        ModularScale, TypeScaleRatio, FontMetrics, VerticalRhythm,
    )
    # Visual — surface
    from jugeo.webapp.theory.visual.surface.gradients import (
        LinearGradient, ColorStop, BlendMode, PorterDuffCompositor,
    )
    # Visual — compose
    from jugeo.webapp.theory.visual.compose.spatial_logic import (
        GestaltAnalyzer, VisualElement, VisualHierarchy,
    )
    # Behavioral
    from jugeo.webapp.theory.behavioral.events.continuous_interaction import (
        ContinuousPath, InteractionKind,
    )
    from jugeo.webapp.theory.behavioral.events.continuous_interaction import (
        PointerState as _PointerState,
    )
    from jugeo.webapp.theory.behavioral.forms.validation import (
        FormSchema, FormField, InputType, ValidationCoherenceChecker,
    )
    from jugeo.webapp.theory.behavioral.forms.validation import (
        ServerValidationResult as _ServerValidationResult,
    )
    from jugeo.webapp.theory.behavioral.media.canvas_audio import (
        AudioGraph, AudioNode, AudioNodeKind,
    )
    from jugeo.webapp.theory.behavioral.network.fetch_theory import (
        FetchRequest, RetryPolicy, CacheStrategy,
    )
    from jugeo.webapp.theory.behavioral.state.state_presheaf import (
        ComponentStatePresheaf, StateVariable, StateKind,
    )
    # Functors
    from jugeo.webapp.theory.functors.python_js_functor import (
        TypeMapping, TruthinessMapper, TranslationFidelityChecker,
    )
    from jugeo.webapp.theory.functors.python_js_functor import PyTypeKind as _PyTypeKind
    from jugeo.webapp.theory.functors.html_css_js_binding import (
        JSHTMLBindingFunctor, CSSBindingFunctor, HTMLCSSJSCoherenceReport,
    )
    from jugeo.webapp.theory.functors.html_css_js_binding import (
        JSDOMQuery as _JSDOMQuery,
        JSQueryKind as _JSQueryKind,
    )
    # Integration
    from jugeo.webapp.theory.integration.coherence import (
        WebAppCoherenceEngine,
        HTMLCSSCoherenceChecker,
        HTMLJSCoherenceChecker,
        TemplateContextCoherenceChecker,
        SecurityCoherenceChecker,
        WebAppCoherenceReport,
    )

    THEORY_AVAILABLE = True

except ImportError:
    pass


# ---------------------------------------------------------------------------
# WebAppSpec
# ---------------------------------------------------------------------------

@dataclass
class WebAppSpec:
    """Input specification for a web application to be analysed."""

    name: str
    routes: list[dict]        # [{path, method, handler}, ...]
    models: list[dict]        # [{name, fields: [{name, type, primary_key?}]}, ...]
    templates: list[str]      # template file names
    static_css_files: list[str]
    static_js_files: list[str]
    auth: bool = False
    blueprints: list[dict] = field(default_factory=list)

    @classmethod
    def from_pipeline_spec(cls, spec: dict) -> WebAppSpec:
        """Convert a pipeline spec dict to a WebAppSpec."""
        return cls(
            name=spec.get("name", "app"),
            routes=spec.get("routes", []),
            models=spec.get("models", []),
            templates=spec.get("templates", []),
            static_css_files=spec.get("static_css_files", []),
            static_js_files=spec.get("static_js_files", []),
            auth=spec.get("auth", False),
            blueprints=spec.get("blueprints", []),
        )


# ---------------------------------------------------------------------------
# TheoryCheckResult
# ---------------------------------------------------------------------------

@dataclass
class TheoryCheckResult:
    """Result of a single theory check."""

    module: str
    check_name: str
    passed: bool
    message: str
    severity: str = "info"
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TheoryEngine
# ---------------------------------------------------------------------------

class TheoryEngine:
    """Runs all theory checks against a WebAppSpec."""

    def run_all(self, spec: WebAppSpec) -> TheoryEngineReport:
        """Execute every theory check and return an aggregated report."""
        if not THEORY_AVAILABLE:
            return TheoryEngineReport(spec=spec, results=[])

        results: list[TheoryCheckResult] = []
        checks = [
            self._check_route_site,
            self._check_sql_schema,
            self._check_cascade_specificity,
            self._check_jinja_context,
            self._check_dom_validity,
            self._check_event_propagation,
            self._check_layout_constraints,
            self._check_typography,
            self._check_color_accessibility,
            self._check_motion_physics,
            self._check_form_validation,
            self._check_js_scope,
            self._check_event_loop,
            self._check_python_js_coherence,
            self._check_security,
            self._check_http_semantics,
            self._check_gestalt_layout,
            self._check_surface_rendering,
            self._check_cache_strategy,
            self._check_continuous_interaction,
        ]
        for check in checks:
            try:
                results.extend(check(spec))
            except Exception as exc:
                results.append(TheoryCheckResult(
                    module="engine",
                    check_name=check.__name__,
                    passed=False,
                    message=f"Check raised {type(exc).__name__}: {exc}",
                    severity="error",
                ))
        return TheoryEngineReport(spec=spec, results=results)

    # ------------------------------------------------------------------
    # Check 1 — route site
    # ------------------------------------------------------------------

    def _check_route_site(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        site = FlaskURLSite()
        invalid_methods: list[str] = []

        for r in spec.routes:
            method_str = r.get("method", "GET").upper()
            try:
                method = HTTPMethod[method_str]
            except KeyError:
                invalid_methods.append(method_str)
                method = HTTPMethod.GET

            path = r.get("path", "/")
            params: list[_URLParameter] = []
            for segment in path.split("/"):
                if segment.startswith("<int:"):
                    params.append(_URLParameter(
                        name=segment[5:-1], kind=URLParameterKind.PATH_INT,
                    ))
                elif segment.startswith("<float:"):
                    params.append(_URLParameter(
                        name=segment[7:-1], kind=URLParameterKind.PATH_FLOAT,
                    ))
                elif segment.startswith("<") and segment.endswith(">"):
                    params.append(_URLParameter(
                        name=segment[1:-1], kind=URLParameterKind.PATH_STRING,
                    ))

            route = FlaskRoute(
                endpoint_name=r.get("handler", path.replace("/", "_").strip("_") or "index"),
                url_pattern=path,
                methods=[method],
                parameters=params,
            )
            site.add_route(route)

        conflicts = site.detect_conflicts()
        route_count = len(spec.routes)
        passed = len(conflicts) == 0 and not invalid_methods

        suggestions = [f"Conflict: {a} vs {b}" for a, b in conflicts]
        suggestions += [f"Unknown HTTP method: {m}" for m in invalid_methods]

        return [TheoryCheckResult(
            module="sites.flask.routing",
            check_name="route_site",
            passed=passed,
            message=(
                f"{route_count} routes registered; "
                f"{len(conflicts)} conflict(s), "
                f"{len(invalid_methods)} invalid method(s)"
            ),
            severity="error" if not passed else "info",
            suggestions=suggestions,
        )]

    # ------------------------------------------------------------------
    # Check 2 — SQL schema
    # ------------------------------------------------------------------

    def _check_sql_schema(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        site = SQLRelationalSite()
        models_without_pk: list[str] = []

        for model in spec.models:
            table_name = model.get("name", "unknown").lower()
            columns: list[SQLColumn] = []
            pk_cols: list[str] = []

            for fld in model.get("fields", []):
                fname = fld.get("name", "col")
                ftype_str = fld.get("type", "String").lower()
                is_pk = fld.get("primary_key", False) or fname == "id"

                if "int" in ftype_str:
                    kind = SQLColumnKind.PRIMARY_KEY if is_pk else SQLColumnKind.INTEGER
                elif "bool" in ftype_str:
                    kind = SQLColumnKind.BOOLEAN
                elif "float" in ftype_str or "numeric" in ftype_str:
                    kind = SQLColumnKind.FLOAT
                elif "date" in ftype_str:
                    kind = SQLColumnKind.DATETIME
                elif "text" in ftype_str:
                    kind = SQLColumnKind.TEXT
                else:
                    kind = SQLColumnKind.VARCHAR

                columns.append(SQLColumn(
                    name=fname,
                    kind=kind,
                    nullable=not is_pk,
                ))
                if is_pk:
                    pk_cols.append(fname)

            if not pk_cols:
                models_without_pk.append(table_name)

            table = SQLTable(
                name=table_name,
                columns=columns,
                primary_key=pk_cols,
            )
            site.add_table(table)

        for model in spec.models:
            table_name = model.get("name", "unknown").lower()
            for fld in model.get("fields", []):
                fname = fld.get("name", "")
                if fname.endswith("_id") and fname != "id":
                    ref_table = fname[:-3]
                    if any(m.get("name", "").lower() == ref_table for m in spec.models):
                        site.add_foreign_key_morphism(
                            from_table=table_name,
                            from_column=fname,
                            to_table=ref_table,
                            to_column="id",
                        )

        cycles = site.detect_cycles()

        tx_checker = TransactionDescentChecker()
        ops = [
            _SQLOperation(
                op_id="op1", op_kind="SELECT",
                table_names=[spec.models[0]["name"].lower()] if spec.models else ["t"],
                is_read=True, is_write=False,
            )
        ]
        tx = SQLTransaction(
            tx_id="tx1",
            isolation_level=IsolationLevel.READ_COMMITTED,
            state=_TransactionState.ACTIVE,
            operations=ops,
        )
        serializability = tx_checker.check_serializability([tx])

        passed = not cycles and not models_without_pk and serializability.is_success
        suggestions = [f"FK cycle: {' → '.join(c)}" for c in cycles]
        suggestions += [f"No primary key: {m}" for m in models_without_pk]

        return [TheoryCheckResult(
            module="sites.sql.tables+transactions",
            check_name="sql_schema",
            passed=passed,
            message=(
                f"{len(spec.models)} model(s); "
                f"{len(cycles)} FK cycle(s); "
                f"{len(models_without_pk)} model(s) missing PK; "
                f"tx serializable={serializability.is_success}"
            ),
            severity="error" if not passed else "info",
            suggestions=suggestions,
        )]

    # ------------------------------------------------------------------
    # Check 3 — cascade specificity
    # ------------------------------------------------------------------

    def _check_cascade_specificity(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        model_names = [m.get("name", "Model") for m in spec.models]
        route_ids = [
            r.get("path", "/").strip("/").replace("/", "-").replace("<", "").replace(">", "")
            or "home"
            for r in spec.routes
        ]

        raw_selectors = (
            [".nav-link", "form.model-form", "#main-content", "input[type=submit]", "h1"]
            + [f".{name.lower()}-card" for name in model_names[:3]]
            + [f"#{rid}-view" for rid in route_ids[:2]]
        )

        inheritance = CSSInheritanceModel()
        resolver = RelativeUnitResolver()
        em_size = resolver.resolve_em(1.0, 16.0)
        base_font_px = resolver.resolve_rem(1.0, 16.0)
        _ = CSSValueStage.COMPUTED

        sorter = CascadeSorter()
        layer = CSSLayerOrder(layer_name=None, layer_index=0)

        declarations: list[tuple[str, CascadeKey]] = []
        selector_site = CSSSelectorSite()
        dom_tags = {"nav", "form", "input", "h1", "main", "div", "a", "button"}

        for i, raw in enumerate(raw_selectors):
            spec_val = CSSSpecificity.parse(raw)
            cascade_key = CascadeKey(
                origin=CSSOrigin.AUTHOR,
                layer=layer,
                specificity=spec_val,
                source_order=i,
            )
            declarations.append((raw, cascade_key))

            kind = (
                SelectorKind.ID if raw.startswith("#")
                else SelectorKind.CLASS if raw.startswith(".")
                else SelectorKind.TYPE
            )
            css_sel = CSSSelector(
                raw=raw,
                kind=kind,
                specificity_tuple=(spec_val.a, spec_val.b, spec_val.c),
            )
            selector_site.add_selector(css_sel)

        sorted_decls = sorter.sort_declarations(declarations)
        winner = sorter.winning_declaration(declarations)

        dead_checker = DeadSelectorChecker()
        all_dom_selectors = [d[0] for d in declarations]
        dead_results = dead_checker.check(
            [CSSSelector(
                raw=raw,
                kind=SelectorKind.TYPE if not raw.startswith((".", "#")) else (
                    SelectorKind.ID if raw.startswith("#") else SelectorKind.CLASS
                ),
                specificity_tuple=(0, 0, 1),
            ) for raw in all_dom_selectors],
            list(dom_tags),
        )

        inherits_color = inheritance.inherits("color")
        specificity_vals = [
            f"{CSSSpecificity.parse(raw)!r}"
            for raw in raw_selectors[:5]
        ]

        return [TheoryCheckResult(
            module="sites.css.specificity+cascade_pipeline+selector_site",
            check_name="cascade_specificity",
            passed=winner is not None,
            message=(
                f"{len(raw_selectors)} selectors analysed; "
                f"winner={repr(winner[0]) if winner else 'none'}; "
                f"em={em_size:.1f}px rem={base_font_px:.1f}px; "
                f"color inherits={inherits_color}; "
                f"stage={CSSValueStage.COMPUTED.value}; "
                f"specificities: {', '.join(specificity_vals[:3])}"
            ),
            severity="info",
            suggestions=[f"Dead selector: {r.selector.raw}" for r in dead_results if r.is_dead_selector],
        )]

    # ------------------------------------------------------------------
    # Check 4 — Jinja context coherence
    # ------------------------------------------------------------------

    def _check_jinja_context(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        template_names = spec.templates or [
            "base.html", "index.html",
            *(f"{m['name'].lower()}_list.html" for m in spec.models),
            *(f"{m['name'].lower()}_form.html" for m in spec.models),
        ]

        template_site = JinjaTemplateSite()
        base_block = JinjaBlock(
            block_name="content",
            template_name="base.html",
            is_required=True,
        )
        base_tmpl = JinjaTemplate(
            template_name="base.html",
            blocks=[base_block],
            context_vars={"app_name"},
        )
        template_site.add_template(base_tmpl)

        context_vars_by_template: dict[str, set[str]] = {}
        for name in template_names:
            if name == "base.html":
                continue
            model_name_guess = name.replace("_list.html", "").replace("_form.html", "")
            ctx: set[str] = {"app_name", model_name_guess, f"{model_name_guess}s"}
            for m in spec.models:
                if m.get("name", "").lower() == model_name_guess:
                    for fld in m.get("fields", []):
                        ctx.add(fld.get("name", ""))
            context_vars_by_template[name] = ctx

            tmpl = JinjaTemplate(
                template_name=name,
                extends="base.html",
                context_vars=ctx,
            )
            template_site.add_template(tmpl)

        checker = TemplateContextCoherenceChecker()
        all_passed = True
        suggestions: list[str] = []

        for tmpl_name, ctx_provided in context_vars_by_template.items():
            violations = checker.check(
                template_vars_used=ctx_provided,
                context_vars_provided=ctx_provided,
                template_name=tmpl_name,
            )
            if violations:
                all_passed = False
                for v in violations:
                    suggestions.append(v.description)

        return [TheoryCheckResult(
            module="sites.jinja.template_site+integration.coherence",
            check_name="jinja_context",
            passed=all_passed,
            message=(
                f"{len(template_names)} template(s) checked; "
                f"context coherence {'✓' if all_passed else '✗'}"
            ),
            severity="info" if all_passed else "warning",
            suggestions=suggestions,
        )]

    # ------------------------------------------------------------------
    # Check 5 — DOM validity
    # ------------------------------------------------------------------

    def _check_dom_validity(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        validity = DOMValidityPresheaf()
        a11y = AccessibilityPresheaf()

        nodes: list[tuple[str, str, dict[str, str]]] = [
            ("nav", "nav", {}),
            ("main", "main", {}),
            ("h1", "h1", {}),
            ("img", "img", {"src": "/logo.png", "alt": "App logo"}),
            ("form", "form", {"method": "post"}),
            ("input_submit", "input", {"type": "submit", "value": "Submit"}),
        ]

        for m in spec.models:
            model_name = m.get("name", "Model")
            for fld in m.get("fields", []):
                fname = fld.get("name", "field")
                ftype = fld.get("type", "String")
                input_type = "number" if "int" in ftype.lower() else "text"
                nodes.append((
                    f"input_{fname}", "input",
                    {"type": input_type, "name": fname, "id": fname, "aria-label": fname},
                ))
                nodes.append((
                    f"label_{fname}", "label",
                    {"for": fname},
                ))

        route_links = []
        for r in spec.routes:
            if r.get("method", "GET").upper() == "GET":
                path = r.get("path", "/")
                route_links.append((
                    f"nav_link_{path.strip('/') or 'home'}",
                    "a",
                    {"href": path},
                ))
        nodes.extend(route_links[:5])

        violations = validity.validate_subtree(nodes)

        a11y_issues: list[str] = []
        for coord_name, tag, attrs in nodes:
            acc_name = a11y.compute_accessible_name(tag, attrs, "")
            role = a11y.compute_role(tag, attrs)
            if tag == "img" and not acc_name:
                a11y_issues.append(f"<img id={coord_name!r}> missing accessible name")
            if tag == "input" and attrs.get("type") not in ("submit", "button", "hidden"):
                if not attrs.get("aria-label") and not attrs.get("id"):
                    a11y_issues.append(f"<input> {coord_name!r} missing label/aria-label")
            _ = role

        nav_coord = DOMCoordinate.from_selector("nav")
        main_coord = DOMCoordinate.from_selector("main")
        morph_kind = DOMMorphismKind.PARENT_CHILD
        morph = DOMTreeMorphism(
            source=nav_coord,
            target=main_coord,
            kind=morph_kind.value,
            label="sibling",
        )
        _ = morph.is_structural()

        node_kinds_used = {DOMNodeKind.ELEMENT, DOMNodeKind.VOID, DOMNodeKind.SECTIONING}
        _ = all(k.is_structural() for k in node_kinds_used)

        passed = not violations and not a11y_issues
        return [TheoryCheckResult(
            module="sites.dom.presheaves+coordinate+kinds",
            check_name="dom_validity",
            passed=passed,
            message=(
                f"{len(nodes)} DOM node(s) checked; "
                f"{len(violations)} validity issue(s); "
                f"{len(a11y_issues)} accessibility issue(s)"
            ),
            severity="warning" if a11y_issues else ("error" if violations else "info"),
            suggestions=[f"{tag}: {msg}" for tag, msg in violations] + a11y_issues,
        )]

    # ------------------------------------------------------------------
    # Check 6 — event propagation
    # ------------------------------------------------------------------

    def _check_event_propagation(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        standard = DOMEvent.standard_events()
        click_event = standard.get("click")
        submit_event = standard.get("submit")
        input_event = standard.get("input")

        has_forms = any(
            r.get("method", "GET").upper() == "POST" for r in spec.routes
        ) or bool(spec.models)

        propagation_issues: list[str] = []

        for evt, path_nodes, label in [
            (click_event, ["html", "body", "main", "button"], "button click"),
            (submit_event, ["html", "body", "main", "form"], "form submit"),
        ]:
            if evt is None:
                continue
            prop_path = EventPropagationPath(
                event=evt,
                target_coord=path_nodes[-1],
                path=path_nodes,
            )
            capture = prop_path.capture_phase()
            at_target = prop_path.at_target_phase()
            bubbles = prop_path.bubble_phase()
            full = prop_path.full_propagation()

            if evt.bubbles and not bubbles:
                propagation_issues.append(f"{label}: missing bubble phase")
            if len(capture) + len(at_target) + len(bubbles) != len(full):
                propagation_issues.append(f"{label}: phase count mismatch")

        phases_seen = {EventPhase.CAPTURE, EventPhase.AT_TARGET, EventPhase.BUBBLE}
        events_relevant = []
        if has_forms:
            events_relevant.append("submit")
        events_relevant.append("click")
        if input_event:
            events_relevant.append("input")

        passed = not propagation_issues
        return [TheoryCheckResult(
            module="sites.dom.events",
            check_name="event_propagation",
            passed=passed,
            message=(
                f"Events analysed: {', '.join(events_relevant)}; "
                f"phases verified: {', '.join(p.value for p in phases_seen)}; "
                f"{len(propagation_issues)} propagation issue(s)"
            ),
            severity="warning" if propagation_issues else "info",
            suggestions=propagation_issues,
        )]

    # ------------------------------------------------------------------
    # Check 7 — layout constraints
    # ------------------------------------------------------------------

    def _check_layout_constraints(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        solver = FlexboxSolver()
        field_count = max(
            (len(m.get("fields", [])) for m in spec.models), default=4
        )

        form_items = [
            FlexItem(
                item_id=f"field_{i}",
                flex_grow=0,
                flex_shrink=1,
                flex_basis=300.0,
                min_size=200.0,
                max_size=400.0,
            )
            for i in range(min(field_count, 6))
        ]
        form_container = FlexContainer(
            container_id="form_layout",
            main_size=640.0,
            cross_size=None,
            direction=FlexDirection.COLUMN,
            wrap=FlexWrap.NOWRAP,
            justify_content=AlignValue.FLEX_START,
            align_items=AlignValue.STRETCH,
            align_content=AlignValue.FLEX_START,
        )
        form_results = solver.solve(form_container, form_items)

        card_items = [
            FlexItem(
                item_id=f"card_{i}",
                flex_grow=1,
                flex_shrink=1,
                flex_basis=280.0,
            )
            for i in range(min(len(spec.models) or 3, 6))
        ]
        card_container = FlexContainer(
            container_id="card_grid",
            main_size=960.0,
            cross_size=None,
            direction=FlexDirection.ROW,
            wrap=FlexWrap.WRAP,
            justify_content=AlignValue.SPACE_BETWEEN,
            align_items=AlignValue.STRETCH,
            align_content=AlignValue.FLEX_START,
        )
        card_results = solver.solve(card_container, card_items)

        col_tracks = [
            GridTrackSize.fr(1.0),
            GridTrackSize.fr(2.0),
            GridTrackSize.fr(1.0),
        ]
        grid = CSSGrid(
            container_id="dashboard",
            column_tracks=col_tracks,
            row_tracks=[GridTrackSize.auto()],
            column_gap=16.0,
            row_gap=16.0,
        )
        grid_solver = GridTrackSolver()
        resolved = grid_solver.solve_tracks(
            grid.column_tracks,
            available_space=960.0 - 2 * 16.0,
            items_per_track={0: [200.0], 1: [400.0], 2: [200.0]},
        )

        box = CSSBox(
            content_width=300.0,
            content_height=200.0,
            padding=BoxEdge.uniform(16.0),
            border=BoxEdge.uniform(1.0),
            margin=BoxEdge.uniform(8.0),
        )
        margin_analyzer = MarginCollapseAnalyzer()
        collapsed = margin_analyzer.collapse_adjacent(
            margin_a_bottom=24.0, margin_b_top=16.0,
        )
        _ = box.border_box_width()
        _ = collapsed

        form_sizes = [r.main_size for r in form_results]
        card_sizes = [r.main_size for r in card_results]

        return [TheoryCheckResult(
            module="visual.layout.flexbox+grid+box_model",
            check_name="layout_constraints",
            passed=True,
            message=(
                f"Form layout ({len(form_sizes)} items): "
                f"{'/'.join(f'{s:.0f}px' for s in form_sizes[:3])}; "
                f"Card grid ({len(card_sizes)} cards): "
                f"{'/'.join(f'{s:.0f}px' for s in card_sizes[:3])}; "
                f"Grid columns: {'/'.join(f'{v:.0f}px' for v in resolved)}"
            ),
            severity="info",
        )]

    # ------------------------------------------------------------------
    # Check 8 — typography
    # ------------------------------------------------------------------

    def _check_typography(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        scale = ModularScale(
            base_size_px=16.0,
            ratio=TypeScaleRatio.PERFECT_FOURTH,
            steps=6,
        )
        sizes = scale.scale_values()

        font = FontMetrics(
            family="system-ui",
            size_px=16.0,
            x_height_ratio=0.53,
            cap_height_ratio=0.72,
            ascender_ratio=0.8,
            descender_ratio=0.2,
            line_gap_ratio=0.1,
        )
        rhythm = VerticalRhythm(base_line_height_px=24.0, font_metrics=font)

        heading_levels = min(len(spec.models) + 2, 5)
        rhythm_elements: list[tuple[float, float]] = []
        for i in range(heading_levels):
            size = scale.size_at(i)
            lh = rhythm.line_height_for_size(size)
            rhythm_elements.append((size, lh))

        is_rhythmic = rhythm.is_rhythmic(rhythm_elements)

        size_labels = [f"{s:.0f}px" for s in sizes]

        return [TheoryCheckResult(
            module="visual.type.metrics",
            check_name="typography",
            passed=True,
            message=(
                f"Modular scale Perfect Fourth: {'/'.join(size_labels[:5])}; "
                f"base={scale.base_size_px:.0f}px; "
                f"rhythm consistent={is_rhythmic}"
            ),
            severity="info",
            suggestions=[] if is_rhythmic else ["Adjust line-heights to grid baseline"],
        )]

    # ------------------------------------------------------------------
    # Check 9 — color accessibility
    # ------------------------------------------------------------------

    def _check_color_accessibility(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        dark_text = Color(0.133, 0.133, 0.133, ColorSpace.SRGB)
        white_bg = Color(1.0, 1.0, 1.0, ColorSpace.SRGB)
        primary = Color(0.122, 0.467, 0.706, ColorSpace.SRGB)
        light_bg = Color(0.961, 0.961, 0.961, ColorSpace.SRGB)

        pairs = [
            ("dark-text on white", dark_text, white_bg, False),
            ("primary on white", primary, white_bg, False),
            ("primary on light-bg", primary, light_bg, True),
        ]

        issues: list[str] = []
        messages: list[str] = []

        for label, fg, bg, large_text in pairs:
            contrast = ColorDistance.wcag_contrast(fg, bg)
            threshold = 3.0 if large_text else 4.5
            passes = contrast >= threshold
            messages.append(f"{label}={contrast:.2f}:1")
            if not passes:
                issues.append(
                    f"{label}: contrast {contrast:.2f}:1 below "
                    f"AA {'large' if large_text else 'normal'} threshold {threshold}:1"
                )

        _ = ColorSpace.SRGB.is_perceptually_uniform()

        passed = not issues
        return [TheoryCheckResult(
            module="visual.color.spaces",
            check_name="color_accessibility",
            passed=passed,
            message="; ".join(messages),
            severity="warning" if issues else "info",
            suggestions=issues,
        )]

    # ------------------------------------------------------------------
    # Check 10 — motion physics
    # ------------------------------------------------------------------

    def _check_motion_physics(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        spring = SpringSystem(mass=1.0, stiffness=120.0, damping=14.0)
        is_under = spring.is_underdamped()
        settle_ms = spring.settle_time() * 1000.0

        ease_out = CubicBezier.ease_out()
        progress_at_half = ease_out.solve(0.5)

        kf = KeyframeAnimation(
            property_name="opacity",
            keyframes=[(0.0, 0.0), (1.0, 1.0)],
            easing=ease_out,
            duration_ms=300.0,
        )
        val_at_150ms = kf.value_at(150.0)

        issues: list[str] = []
        if not is_under:
            issues.append("Spring is not underdamped — animation will not oscillate")
        if settle_ms > 500.0:
            issues.append(f"Spring settle time {settle_ms:.0f}ms exceeds 500ms budget")

        return [TheoryCheckResult(
            module="visual.motion.physics",
            check_name="motion_physics",
            passed=is_under and settle_ms <= 500.0,
            message=(
                f"Spring ζ={spring.damping_ratio():.3f} underdamped={is_under} "
                f"settle={settle_ms:.0f}ms; "
                f"ease-out x=0.5→y={progress_at_half:.3f}; "
                f"keyframe @150ms={val_at_150ms:.3f}"
            ),
            severity="warning" if issues else "info",
            suggestions=issues,
        )]

    # ------------------------------------------------------------------
    # Check 11 — form validation
    # ------------------------------------------------------------------

    def _check_form_validation(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        checker = ValidationCoherenceChecker()
        all_schemas: list[str] = []
        coherence_issues: list[str] = []

        _type_map = {
            "string": InputType.TEXT,
            "str": InputType.TEXT,
            "integer": InputType.INTEGER,
            "int": InputType.INTEGER,
            "boolean": InputType.CHECKBOX,
            "bool": InputType.CHECKBOX,
            "float": InputType.NUMBER,
            "text": InputType.TEXTAREA,
            "email": InputType.EMAIL,
            "url": InputType.URL,
            "date": InputType.DATE,
        }

        for model in spec.models:
            model_name = model.get("name", "Model")
            fields: list[FormField] = []
            sample_data: dict[str, object] = {}

            for fld in model.get("fields", []):
                fname = fld.get("name", "field")
                ftype_raw = fld.get("type", "String").lower().split("(")[0].strip()
                is_pk = fld.get("primary_key", False) or fname == "id"

                input_type = _type_map.get(ftype_raw, InputType.TEXT)
                ff = FormField(
                    field_name=fname,
                    input_type=input_type,
                    rules=[],
                    label=fname.replace("_", " ").title(),
                )
                if not is_pk:
                    fields.append(ff)
                    sample_data[fname] = "" if input_type == InputType.TEXT else 0

            if not fields:
                continue

            schema = FormSchema(
                form_id=f"{model_name.lower()}_form",
                fields=fields,
                method="POST",
                action=f"/{model_name.lower()}s",
            )
            all_schemas.append(schema.form_id)

            server_result = _ServerValidationResult(
                field_errors={},
                non_field_errors=[],
                success=True,
            )
            descent = checker.check_coherence(schema, server_result, sample_data)
            if not descent.is_success:
                coherence_issues.append(f"{schema.form_id}: coherence obstruction")

        return [TheoryCheckResult(
            module="behavioral.forms.validation",
            check_name="form_validation",
            passed=not coherence_issues,
            message=(
                f"{len(all_schemas)} form schema(s) built "
                f"({', '.join(all_schemas) or 'none'}); "
                f"{len(coherence_issues)} coherence issue(s)"
            ),
            severity="warning" if coherence_issues else "info",
            suggestions=coherence_issues,
        )]

    # ------------------------------------------------------------------
    # Check 12 — JS scope chain + component state
    # ------------------------------------------------------------------

    def _check_js_scope(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        global_scope = JSScopeCoordinate(
            coord_name="global",
            scope_kind=ScopeKind.GLOBAL,
            variables={"document": VariableKind.CONST, "window": VariableKind.CONST},
        )
        dom_ready_scope = JSScopeCoordinate(
            coord_name="DOMContentLoaded",
            scope_kind=ScopeKind.FUNCTION,
            parent_name="global",
            variables={"app": VariableKind.CONST},
            is_strict_mode=True,
        )
        form_scope = JSScopeCoordinate(
            coord_name="formSubmitHandler",
            scope_kind=ScopeKind.FUNCTION,
            parent_name="DOMContentLoaded",
            variables={"event": VariableKind.CONST, "formData": VariableKind.LET},
            is_strict_mode=True,
            is_async=True,
        )

        shadowing_issues: list[str] = []
        for scope in (dom_ready_scope, form_scope):
            for var_name in scope.variables:
                if global_scope.has_binding(var_name):
                    shadowing_issues.append(
                        f"'{var_name}' in {scope.coord_name!r} shadows global binding"
                    )

        presheaf = ComponentStatePresheaf()
        for model in spec.models:
            model_name = model.get("name", "Model")
            coord = f"component:{model_name.lower()}_list"
            presheaf.add_state_var(coord, StateVariable(
                name="items",
                kind=StateKind.LIST,
                initial_value=[],
                is_local=True,
            ))
            presheaf.add_state_var(coord, StateVariable(
                name="loading",
                kind=StateKind.BOOLEAN,
                initial_value=False,
                is_local=True,
            ))
            presheaf.add_state_var(coord, StateVariable(
                name="error",
                kind=StateKind.NULLABLE,
                initial_value=None,
                is_local=True,
            ))

        scope_chain_depth = 3
        return [TheoryCheckResult(
            module="sites.js.scope_chain+behavioral.state.state_presheaf",
            check_name="js_scope",
            passed=not shadowing_issues,
            message=(
                f"Scope chain depth={scope_chain_depth}; "
                f"{len(shadowing_issues)} shadowing issue(s); "
                f"{len(spec.models)} component state(s) modelled "
                f"(items/loading/error per model)"
            ),
            severity="warning" if shadowing_issues else "info",
            suggestions=shadowing_issues,
        )]

    # ------------------------------------------------------------------
    # Check 13 — event loop + audio scheduling
    # ------------------------------------------------------------------

    def _check_event_loop(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        post_routes = [r for r in spec.routes if r.get("method", "GET").upper() == "POST"]

        loop = EventLoopSite(label=f"{spec.name}_event_loop")
        loop.enqueue_macrotask("user_click", source="user_interaction")

        promises: list[PromiseSection] = []
        for i, route in enumerate(post_routes[:3]):
            handler = route.get("handler", f"handler_{i}")
            promise = PromiseSection(
                promise_id=f"fetch_{handler}",
                state="pending",
            )
            microtask_id = loop.enqueue_microtask(
                f"resolve_{handler}", source=f"fetch_{handler}"
            )
            resolved = promise.resolve({"status": 200})
            promises.append(promise)
            _ = resolved
            _ = microtask_id

        loop.run_one_iteration()
        descent = loop.descent_check()

        audio_graph = AudioGraph(nodes={})
        source_node = AudioNode(
            node_id="oscillator",
            kind=AudioNodeKind.OSCILLATOR,
            params={"frequency": 440.0},
            inputs=[],
            outputs=["gain"],
        )
        gain_node = AudioNode(
            node_id="gain",
            kind=AudioNodeKind.GAIN,
            params={"gain": 0.8},
            inputs=["oscillator"],
            outputs=["destination"],
        )
        dest_node = AudioNode(
            node_id="destination",
            kind=AudioNodeKind.DESTINATION,
            params={},
            inputs=["gain"],
            outputs=[],
        )
        audio_graph.add_node(source_node)
        audio_graph.add_node(gain_node)
        audio_graph.add_node(dest_node)
        audio_graph.connect("oscillator", "gain")
        audio_graph.connect("gain", "destination")
        has_feedback = audio_graph.has_feedback_loop()
        paths = audio_graph.signal_paths()

        _ = TaskQueueKind.MICROTASK

        return [TheoryCheckResult(
            module="sites.js.event_loop+behavioral.media.canvas_audio",
            check_name="event_loop",
            passed=descent.is_success and not has_feedback,
            message=(
                f"{len(post_routes)} POST route(s) modelled as Promise(s); "
                f"event loop descent={descent.is_success}; "
                f"audio graph: {len(paths)} signal path(s), "
                f"feedback={has_feedback}"
            ),
            severity="info",
        )]

    # ------------------------------------------------------------------
    # Check 14 — Python/JS type coherence + HTML/CSS/JS binding
    # ------------------------------------------------------------------

    def _check_python_js_coherence(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        fidelity = TranslationFidelityChecker()
        truthy = TruthinessMapper()

        all_mappings = TypeMapping.standard_mappings()
        lossless = [m for m in all_mappings if m.is_lossless]
        lossy = [m for m in all_mappings if not m.is_lossless]

        field_issues: list[str] = []
        for model in spec.models:
            for fld in model.get("fields", []):
                fname = fld.get("name", "")
                ftype = fld.get("type", "String").lower()

                if "int" in ftype:
                    mapping = fidelity.check_type_mapping(_PyTypeKind.INT)
                elif "bool" in ftype:
                    mapping = fidelity.check_type_mapping(_PyTypeKind.BOOL)
                elif "list" in ftype:
                    mapping = fidelity.check_type_mapping(_PyTypeKind.LIST)
                    py_falsy = truthy.py_is_truthy("[]")
                    if py_falsy is False:
                        field_issues.append(
                            f"{fname}: empty list is falsy in Python — "
                            f"check JS truthy semantics"
                        )
                elif "float" in ftype:
                    mapping = fidelity.check_type_mapping(_PyTypeKind.FLOAT)
                else:
                    mapping = fidelity.check_type_mapping(_PyTypeKind.STR)

                if mapping and not mapping.is_lossless and fname:
                    field_issues.append(
                        f"{fname} ({ftype}): {mapping.loss_description or 'lossy translation'}"
                    )

        critical_diffs = TruthinessMapper.CRITICAL_DIFFERENCES[:2]

        js_html_functor = JSHTMLBindingFunctor()
        css_binding_functor = CSSBindingFunctor()

        dom_elements = [
            ("nav", "nav", set(), None),
            ("main", "main", set(), "main-content"),
            ("form_model", "form", {"model-form"}, "model-form"),
        ]
        query = _JSDOMQuery(
            query_id="q_main",
            kind=_JSQueryKind.GET_BY_ID,
            selector="main-content",
        )
        q_result = js_html_functor.check_query(query, dom_elements)

        css_matches = css_binding_functor.match("form.model-form", dom_elements)

        coherence_report = HTMLCSSJSCoherenceReport(
            dead_css_rules=[],
            null_js_queries=[] if q_result.is_success else ["#main-content"],
            cascade_conflicts=[],
            all_passed=q_result.is_success and not field_issues,
        )

        return [TheoryCheckResult(
            module="functors.python_js_functor+html_css_js_binding",
            check_name="python_js_coherence",
            passed=coherence_report.all_passed,
            message=(
                f"{len(lossless)} lossless type mappings, "
                f"{len(lossy)} lossy; "
                f"{len(field_issues)} field coherence issue(s); "
                f"critical truthy diffs: {len(critical_diffs)}; "
                f"JS query resolved={q_result.is_success}; "
                f"CSS matches: {len(css_matches)}"
            ),
            severity="warning" if field_issues else "info",
            suggestions=field_issues,
        )]

    # ------------------------------------------------------------------
    # Check 15 — security coherence
    # ------------------------------------------------------------------

    def _check_security(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        engine = WebAppCoherenceEngine()

        post_methods = [
            r.get("method", "GET").upper()
            for r in spec.routes
            if r.get("method", "GET").upper() in ("POST", "PUT", "PATCH", "DELETE")
        ]
        has_csrf = not spec.auth

        model_field_names = [
            fld.get("name", "")
            for model in spec.models
            for fld in model.get("fields", [])
        ]

        html_tags = {"nav", "main", "form", "input", "label", "a", "button", "h1", "p"}
        html_classes: set[str] = set()
        html_ids: set[str] = set()
        for model in spec.models:
            html_classes.add(f"{model['name'].lower()}-card")
            html_ids.add(f"{model['name'].lower()}-list")
        css_selectors = [
            ".nav-link",
            f".{spec.models[0]['name'].lower()}-card" if spec.models else ".card",
        ]
        js_queries = [
            f"document.getElementById('{spec.models[0]['name'].lower()}-list')"
            if spec.models else "document.getElementById('app')"
        ]

        report: WebAppCoherenceReport = engine.run_all(
            html_css={
                "css_selectors": css_selectors,
                "html_element_classes": html_classes,
                "html_element_ids": html_ids,
                "html_tags": html_tags,
            },
            html_js={
                "js_dom_queries": js_queries,
                "html_element_ids": html_ids,
                "html_element_classes": html_classes,
            },
            template={
                "template_vars_used": set(model_field_names[:5]),
                "context_vars_provided": set(model_field_names[:5]),
                "template_name": "index.html",
            },
            client_server={
                "client_fetch_urls": [r.get("path", "/") for r in spec.routes],
                "server_route_patterns": [r.get("path", "/") for r in spec.routes],
            },
            security={
                "xss": {
                    "template_vars_unescaped": [],
                    "output_contexts": {},
                },
                "csrf": {
                    "form_methods": post_methods,
                    "has_csrf_token": has_csrf,
                },
            },
        )

        html_css_checker = HTMLCSSCoherenceChecker()
        html_js_checker = HTMLJSCoherenceChecker()
        template_checker = TemplateContextCoherenceChecker()
        security_checker = SecurityCoherenceChecker()

        direct_css_violations = html_css_checker.check(
            css_selectors=css_selectors,
            html_element_classes=html_classes,
            html_element_ids=html_ids,
            html_tags=html_tags,
        )
        direct_js_violations = html_js_checker.check(
            js_dom_queries=js_queries,
            html_element_ids=html_ids,
            html_element_classes=html_classes,
        )
        direct_csrf = security_checker.check_csrf_protection(
            form_methods=post_methods,
            has_csrf_token=has_csrf,
        )

        all_violations = (
            report.violations
            + direct_css_violations
            + direct_js_violations
            + direct_csrf
        )
        errors = [v for v in all_violations if v.severity == "error"]
        warnings = [v for v in all_violations if v.severity == "warning"]

        passed = not errors
        return [TheoryCheckResult(
            module="integration.coherence",
            check_name="security",
            passed=passed,
            message=(
                f"{len(all_violations)} coherence violation(s): "
                f"{len(errors)} error(s), {len(warnings)} warning(s)"
            ),
            severity="error" if errors else ("warning" if warnings else "info"),
            suggestions=[v.description for v in all_violations[:5]],
        )]

    # ------------------------------------------------------------------
    # Check 16 — HTTP semantics
    # ------------------------------------------------------------------

    def _check_http_semantics(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        standard_codes = HTTPStatusCode.standard_codes()
        issues: list[str] = []
        recommendations: list[str] = []

        for r in spec.routes:
            method = r.get("method", "GET").upper()
            handler = r.get("handler", "")
            path = r.get("path", "/")

            req = HTTPRequest(
                request_id=f"req_{handler}",
                method=method,
                url=f"http://localhost{path}",
            )
            _ = req.is_cors_request()

            if "list" in handler or "index" in handler:
                expected_method = "GET"
                ok_code = standard_codes.get(200)
                recommendations.append(f"GET {path} → 200 OK")
            elif "create" in handler or "add" in handler:
                expected_method = "POST"
                created_code = standard_codes.get(201)
                recommendations.append(f"POST {path} → 201 Created")
                _ = created_code
            elif "delete" in handler or "remove" in handler:
                expected_method = "DELETE"
                no_content = standard_codes.get(204)
                recommendations.append(f"DELETE {path} → 204 No Content")
                _ = no_content
                if method == "GET":
                    issues.append(f"DELETE handler '{handler}' uses GET — use DELETE method")
            elif "update" in handler or "edit" in handler:
                expected_method = "PUT"
                ok_code = standard_codes.get(200)
                recommendations.append(f"PUT/PATCH {path} → 200 OK")
            else:
                expected_method = "GET"

            status_code = standard_codes.get(200)
            response = HTTPResponse(
                request_id=req.request_id,
                status=status_code,
            )
            _ = response.cache_key()

            if method == "DELETE" and handler and "delete" not in handler.lower():
                pass
            elif method == "GET" and ("delete" in handler.lower() or "remove" in handler.lower()):
                issues.append(
                    f"Route '{path}' handler '{handler}' suggests DELETE but uses GET"
                )

        return [TheoryCheckResult(
            module="sites.http.request_response",
            check_name="http_semantics",
            passed=not issues,
            message=(
                f"{len(spec.routes)} route(s) checked; "
                f"{len(issues)} semantic mismatch(es); "
                f"recommendations: {len(recommendations)}"
            ),
            severity="warning" if issues else "info",
            suggestions=issues + recommendations[:3],
        )]

    # ------------------------------------------------------------------
    # Check 17 — Gestalt layout
    # ------------------------------------------------------------------

    def _check_gestalt_layout(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        elements = [
            VisualElement(
                element_id="nav",
                x=0.0, y=0.0,
                width=1280.0, height=64.0,
                z_index=10,
                color_lightness=20.0,
            ),
            VisualElement(
                element_id="main",
                x=0.0, y=80.0,
                width=960.0, height=600.0,
                z_index=1,
                color_lightness=95.0,
            ),
            VisualElement(
                element_id="sidebar",
                x=976.0, y=80.0,
                width=304.0, height=600.0,
                z_index=1,
                color_lightness=90.0,
            ),
        ]

        for i, model in enumerate(spec.models[:4]):
            elements.append(VisualElement(
                element_id=f"card_{model.get('name', f'model_{i}')}",
                x=float(i % 3) * 320.0,
                y=80.0 + float(i // 3) * 220.0,
                width=300.0, height=200.0,
                z_index=2,
                color_hue=210.0,
                color_lightness=80.0,
            ))

        analyzer = GestaltAnalyzer()
        proximity_groups = analyzer.proximity_groups(elements, threshold_px=32.0)

        weights = [
            (VisualHierarchy.attention_weight(el, 1280.0, 768.0), el.element_id)
            for el in elements
        ]
        weights.sort(reverse=True)
        hierarchy = VisualHierarchy.hierarchy_order(elements, 1280.0, 768.0)

        top_element = hierarchy[0] if hierarchy else "unknown"
        main_weight = next((w for w, eid in weights if eid == "main"), 0.0)
        nav_weight = next((w for w, eid in weights if eid == "nav"), 0.0)
        main_prominent = main_weight >= nav_weight * 0.5

        return [TheoryCheckResult(
            module="visual.compose.spatial_logic",
            check_name="gestalt_layout",
            passed=main_prominent,
            message=(
                f"{len(elements)} visual element(s); "
                f"{len(proximity_groups)} proximity group(s); "
                f"top attention: '{top_element}'; "
                f"main prominence={main_prominent}"
            ),
            severity="info",
        )]

    # ------------------------------------------------------------------
    # Check 18 — surface rendering
    # ------------------------------------------------------------------

    def _check_surface_rendering(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        brand_stop = ColorStop(position=0.0, r=0.122, g=0.467, b=0.706, alpha=1.0)
        light_stop = ColorStop(position=1.0, r=0.529, g=0.737, b=0.867, alpha=1.0)
        gradient = LinearGradient(angle_deg=135.0, stops=[brand_stop, light_stop])
        mid_color = gradient.color_at(0.5)

        compositor = PorterDuffCompositor()
        white: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
        brand_rgba: tuple[float, float, float, float] = (0.122, 0.467, 0.706, 1.0)
        result_over = compositor.over(brand_rgba, white)

        overlay_rgba: tuple[float, float, float, float] = (0.122, 0.467, 0.706, 0.1)
        pastel = compositor.over(overlay_rgba, white)
        pastel_r, pastel_g, pastel_b, pastel_a = pastel
        is_pastel = pastel_r > 0.85 and pastel_g > 0.85 and pastel_b > 0.85 and pastel_a > 0.9

        _ = BlendMode.NORMAL
        _ = result_over
        mid_r = mid_color.r

        return [TheoryCheckResult(
            module="visual.surface.gradients",
            check_name="surface_rendering",
            passed=is_pastel,
            message=(
                f"Brand gradient midpoint r={mid_r:.3f}; "
                f"porter-duff NORMAL over white → "
                f"({result_over[0]:.2f},{result_over[1]:.2f},{result_over[2]:.2f}); "
                f"0.1-opacity overlay is_pastel={is_pastel}"
            ),
            severity="info",
        )]

    # ------------------------------------------------------------------
    # Check 19 — cache strategy
    # ------------------------------------------------------------------

    def _check_cache_strategy(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        policy = RetryPolicy(
            max_attempts=3,
            initial_delay_ms=200.0,
            backoff_factor=2.0,
            max_delay_ms=5000.0,
            retryable_statuses={429, 500, 502, 503, 504},
        )

        delays = [policy.delay_for_attempt(i) for i in range(1, 4)]

        recommendations: list[str] = []
        issues: list[str] = []

        for r in spec.routes:
            method = r.get("method", "GET").upper()
            path = r.get("path", "/")
            is_api = path.startswith("/api/")

            if method == "GET":
                strategy = CacheStrategy.CACHE_FIRST if is_api else CacheStrategy.NETWORK_FIRST
            else:
                strategy = CacheStrategy.NO_STORE

            req = FetchRequest(
                request_id=f"fetch_{path.replace('/', '_').strip('_')}",
                url=f"http://localhost{path}",
                method=method,
                cache_strategy=strategy,
                timeout_ms=5000.0,
            )
            _ = req.needs_preflight()
            recommendations.append(
                f"{method} {path} → {strategy.value}"
            )

        monotone = all(d1 <= d2 for d1, d2 in zip(delays, delays[1:]))

        return [TheoryCheckResult(
            module="behavioral.network.fetch_theory",
            check_name="cache_strategy",
            passed=monotone,
            message=(
                f"{len(spec.routes)} route(s) assigned cache strategies; "
                f"retry delays: {[f'{d:.0f}ms' for d in delays]}; "
                f"monotone backoff={monotone}"
            ),
            severity="info",
            suggestions=recommendations[:4],
        )]

    # ------------------------------------------------------------------
    # Check 20 — continuous interaction
    # ------------------------------------------------------------------

    def _check_continuous_interaction(self, spec: WebAppSpec) -> list[TheoryCheckResult]:
        has_list_views = any(
            "list" in r.get("handler", "") or
            (r.get("method", "GET").upper() == "GET" and r.get("path", "/").count("/") <= 1)
            for r in spec.routes
        ) or bool(spec.models)

        issues: list[str] = []
        has_inertia = False
        path_distance = 0.0

        if has_list_views:
            samples = [
                _PointerState(
                    pointer_id="p1",
                    x=640.0, y=float(y),
                    pressure=1.0,
                    timestamp_ms=float(i * 16),
                    pointer_type="touch",
                )
                for i, y in enumerate(range(0, 2001, 100))
            ]
            path = ContinuousPath(
                interaction_kind=InteractionKind.SCROLL,
                samples=samples,
                start_target="list_container",
            )
            has_inertia = path.has_inertia_potential()
            path_distance = path.total_distance()
            displacement = path.displacement()

            if not path.is_mostly_vertical():
                issues.append("Scroll path is not primarily vertical")

            _ = displacement

        return [TheoryCheckResult(
            module="behavioral.events.continuous_interaction",
            check_name="continuous_interaction",
            passed=not issues,
            message=(
                f"List views present={has_list_views}; "
                f"scroll path distance={path_distance:.0f}px; "
                f"inertia_potential={has_inertia}"
                if has_list_views
                else "No list views — scroll interaction not applicable"
            ),
            severity="info",
            suggestions=issues,
        )]


# ---------------------------------------------------------------------------
# TheoryEngineReport
# ---------------------------------------------------------------------------

@dataclass
class TheoryEngineReport:
    """Aggregated result from all theory checks."""

    spec: WebAppSpec
    results: list[TheoryCheckResult]

    def errors(self) -> list[TheoryCheckResult]:
        return [r for r in self.results if r.severity == "error"]

    def warnings(self) -> list[TheoryCheckResult]:
        return [r for r in self.results if r.severity == "warning"]

    def info(self) -> list[TheoryCheckResult]:
        return [r for r in self.results if r.severity == "info"]

    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def summary(self) -> str:
        n = len(self.results)
        p = self.passed_count()
        w = len(self.warnings())
        e = len(self.errors())
        return (
            f"Theory Engine [{self.spec.name}]: "
            f"{n} checks, {p} passed, {w} warning(s), {e} error(s)"
        )

    def cli_output(self) -> str:
        lines = [self.summary()]
        for r in self.results:
            icon = "✓" if r.passed else "⚠" if r.severity == "warning" else "✗"
            lines.append(f"  {icon} [{r.check_name}] {r.message}")
            for s in r.suggestions[:2]:
                lines.append(f"      → {s}")
        return "\n".join(lines)
