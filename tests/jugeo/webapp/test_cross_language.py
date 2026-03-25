"""
Comprehensive tests for jugeo.webapp.cross_language.

Tests cover models, reference resolution, overlap checking, morphism building,
API contract checking, trust topology, and the integration façade.
"""
from __future__ import annotations

import pytest

from jugeo.webapp.cross_language import (
    CrossLanguageAnalyzer,
    QuickCheck,
    OverlapChecker,
    OverlapKind,
    OverlapCondition,
    OverlapViolation,
    CrossReference,
    MorphismEvidence,
    DescentReport,
    CrossReferenceResolver,
    URLPatternMatcher,
    CrossLanguageMorphismBuilder,
    MorphismGraph,
    APIContractChecker,
    SchemaComparer,
    WebTrustLevel,
    TrustBoundary,
    WebTrustChecker,
    TrustTransportChecker,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def url_matcher():
    return URLPatternMatcher()


@pytest.fixture
def resolver():
    return CrossReferenceResolver()


@pytest.fixture
def overlap_checker():
    return OverlapChecker()


@pytest.fixture
def morphism_builder():
    return CrossLanguageMorphismBuilder()


@pytest.fixture
def contract_checker():
    return APIContractChecker()


@pytest.fixture
def schema_comparer():
    return SchemaComparer()


@pytest.fixture
def trust_checker():
    return WebTrustChecker()


@pytest.fixture
def trust_transport():
    return TrustTransportChecker()


# ===================================================================
# OverlapKind enum
# ===================================================================


class TestOverlapKind:

    def test_all_ten_values_exist(self):
        assert len(OverlapKind) == 10

    def test_string_representation(self):
        assert OverlapKind.ROUTE_TEMPLATE == "route_template"
        assert OverlapKind.ROUTE_JS_FETCH == "route_js_fetch"
        assert OverlapKind.MODEL_DB_SCHEMA == "model_db_schema"
        assert OverlapKind.JS_DOM_HTML == "js_dom_html"

    def test_construction_from_string(self):
        assert OverlapKind("route_template") is OverlapKind.ROUTE_TEMPLATE
        assert OverlapKind("auth_session") is OverlapKind.AUTH_SESSION

    def test_value_attribute(self):
        assert OverlapKind.JS_CLASS_CSS.value == "js_class_css"
        assert OverlapKind.FORM_ROUTE.value == "form_route"
        assert OverlapKind.TEMPLATE_CSS.value == "template_css"
        assert OverlapKind.DB_CONSTRAINT_HANDLER.value == "db_constraint_handler"
        assert OverlapKind.ERROR_HANDLER_JS.value == "error_handler_js"


# ===================================================================
# Model round-trips
# ===================================================================


class TestOverlapConditionRoundTrip:

    def test_to_dict_from_dict(self):
        oc = OverlapCondition(
            id="oc-1",
            kind=OverlapKind.ROUTE_TEMPLATE,
            left_layer="python",
            right_layer="jinja2",
            description="context vars must match",
            left_coordinates=["user", "items"],
            right_coordinates=["user", "items", "title"],
        )
        d = oc.to_dict()
        assert d["kind"] == "route_template"
        restored = OverlapCondition.from_dict(d)
        assert restored.id == oc.id
        assert restored.kind == oc.kind
        assert restored.left_coordinates == oc.left_coordinates
        assert restored.right_coordinates == oc.right_coordinates


class TestOverlapViolationRoundTrip:

    def test_to_dict_from_dict(self):
        v = OverlapViolation(
            id="v-1",
            condition_id="oc-1",
            kind=OverlapKind.JS_DOM_HTML,
            message="missing element",
            severity="error",
            left_detail="getElementById('foo')",
            right_detail="html has no 'foo'",
            repair_hint="add id=foo",
            file_path="app.js",
            line_number=42,
        )
        d = v.to_dict()
        assert d["kind"] == "js_dom_html"
        restored = OverlapViolation.from_dict(d)
        assert restored.id == v.id
        assert restored.file_path == "app.js"
        assert restored.line_number == 42

    def test_defaults(self):
        v = OverlapViolation(
            id="v-2", condition_id="c", kind=OverlapKind.AUTH_SESSION,
            message="m", severity="warning",
            left_detail="l", right_detail="r", repair_hint="h",
        )
        assert v.file_path == ""
        assert v.line_number == 0


class TestCrossReferenceRoundTrip:

    def test_to_dict_from_dict(self):
        cr = CrossReference(
            source_file="views.py", source_line=10,
            source_layer="python", target_name="username",
            target_layer="jinja2", reference_type="template_variable",
            resolved=True, resolution_target="login.html:username",
        )
        d = cr.to_dict()
        restored = CrossReference.from_dict(d)
        assert restored.resolved is True
        assert restored.resolution_target == "login.html:username"


class TestMorphismEvidenceRoundTrip:

    def test_to_dict_from_dict(self):
        me = MorphismEvidence(
            morphism_id="m-1", source_layer="python",
            target_layer="jinja2", evidence_type="kwarg",
            trust_level="server_validated",
            details={"var": "user"},
        )
        d = me.to_dict()
        restored = MorphismEvidence.from_dict(d)
        assert restored.details == {"var": "user"}


class TestDescentReportRoundTrip:

    def test_empty_report(self):
        report = DescentReport()
        d = report.to_dict()
        restored = DescentReport.from_dict(d)
        assert restored.error_count == 0
        assert restored.warning_count == 0
        assert restored.unresolved_references == []
        assert restored.layers_involved() == set()

    def test_report_with_violations(self):
        v_err = OverlapViolation(
            id="v1", condition_id="c1", kind=OverlapKind.JS_DOM_HTML,
            message="m", severity="error",
            left_detail="l", right_detail="r", repair_hint="h",
        )
        v_warn = OverlapViolation(
            id="v2", condition_id="c2", kind=OverlapKind.JS_CLASS_CSS,
            message="w", severity="warning",
            left_detail="l", right_detail="r", repair_hint="h",
        )
        ref = CrossReference(
            source_file="a.py", source_line=1,
            source_layer="python", target_name="x",
            target_layer="jinja2", reference_type="template_variable",
            resolved=False,
        )
        report = DescentReport(
            violations=[v_err, v_warn],
            cross_references=[ref],
            coverage_score=0.5,
            layer_connectivity={"python": ["jinja2"]},
            summary="test",
        )
        assert report.error_count == 1
        assert report.warning_count == 1
        assert len(report.unresolved_references) == 1
        by_kind = report.violations_by_kind()
        assert "js_dom_html" in by_kind
        assert report.layers_involved() == {"python", "jinja2"}

        d = report.to_dict()
        restored = DescentReport.from_dict(d)
        assert restored.error_count == 1
        assert restored.coverage_score == 0.5


# ===================================================================
# URLPatternMatcher
# ===================================================================


class TestURLPatternMatcher:

    def test_simple_match(self, url_matcher):
        assert url_matcher.matches("/users", "/users") is True

    def test_int_param(self, url_matcher):
        assert url_matcher.matches("/users/<int:id>", "/users/42") is True
        assert url_matcher.matches("/users/<int:id>", "/users/abc") is False

    def test_string_param(self, url_matcher):
        assert url_matcher.matches("/posts/<slug>", "/posts/hello-world") is True

    def test_path_param(self, url_matcher):
        assert url_matcher.matches("/files/<path:fp>", "/files/a/b/c.txt") is True

    def test_uuid_param(self, url_matcher):
        assert url_matcher.matches(
            "/item/<uuid:uid>",
            "/item/550e8400-e29b-41d4-a716-446655440000",
        ) is True

    def test_no_match(self, url_matcher):
        assert url_matcher.matches("/users/<int:id>", "/posts/1") is False

    def test_extract_params_basic(self, url_matcher):
        params = url_matcher.extract_params("/users/<int:id>/posts/<slug>")
        assert len(params) == 2
        assert params[0] == {"name": "id", "type": "int"}
        assert params[1] == {"name": "slug", "type": "string"}

    def test_extract_params_no_converter(self, url_matcher):
        params = url_matcher.extract_params("/items/<name>")
        assert params == [{"name": "name", "type": "string"}]

    def test_extract_params_empty(self, url_matcher):
        assert url_matcher.extract_params("/static") == []


# ===================================================================
# CrossReferenceResolver
# ===================================================================


class TestResolveTemplateVariables:

    def test_resolved_variable(self, resolver):
        render_calls = [
            {"template": "index.html", "kwargs": ["user", "items"],
             "file": "views.py", "line": 5},
        ]
        template_vars = [
            {"template": "index.html", "variable": "user",
             "file": "index.html", "line": 3},
        ]
        refs = resolver.resolve_template_variables(render_calls, template_vars)
        assert len(refs) == 1
        assert refs[0].resolved is True
        assert refs[0].target_name == "user"

    def test_unresolved_variable(self, resolver):
        render_calls = [
            {"template": "page.html", "kwargs": ["title"],
             "file": "views.py", "line": 10},
        ]
        template_vars = [
            {"template": "page.html", "variable": "subtitle",
             "file": "page.html", "line": 2},
        ]
        refs = resolver.resolve_template_variables(render_calls, template_vars)
        assert len(refs) == 1
        assert refs[0].resolved is False


class TestResolveDomReferences:

    def test_resolved_dom_ref(self, resolver):
        js_refs = [{"element_id": "main-nav", "file": "app.js",
                     "line": 20, "method": "getElementById"}]
        refs = resolver.resolve_dom_references(js_refs, {"main-nav", "footer"})
        assert refs[0].resolved is True
        assert refs[0].resolution_target == "id=main-nav"

    def test_unresolved_dom_ref(self, resolver):
        js_refs = [{"element_id": "ghost", "file": "app.js",
                     "line": 5, "method": "getElementById"}]
        refs = resolver.resolve_dom_references(js_refs, {"nav"})
        assert refs[0].resolved is False


class TestResolveCssClasses:

    def test_resolved_css_class(self, resolver):
        refs = resolver.resolve_css_classes({"btn", "active"}, {"btn", "active", "hidden"})
        assert all(r.resolved for r in refs)

    def test_unresolved_css_class(self, resolver):
        refs = resolver.resolve_css_classes({"highlight"}, {"btn"})
        assert refs[0].resolved is False


class TestResolveApiContracts:

    def test_resolved_api_field(self, resolver):
        route_responses = [
            {"route": "/api/users", "fields": ["id", "name", "email"],
             "status_codes": [200], "method": "GET"},
        ]
        fetch_expectations = [
            {"url": "/api/users", "expected_fields": ["id", "name"],
             "method": "GET", "file": "app.js", "line": 10},
        ]
        refs = resolver.resolve_api_contracts(route_responses, fetch_expectations)
        assert all(r.resolved for r in refs)

    def test_unresolved_api_field(self, resolver):
        route_responses = [
            {"route": "/api/users", "fields": ["id", "name"],
             "status_codes": [200], "method": "GET"},
        ]
        fetch_expectations = [
            {"url": "/api/users", "expected_fields": ["avatar"],
             "method": "GET", "file": "app.js", "line": 10},
        ]
        refs = resolver.resolve_api_contracts(route_responses, fetch_expectations)
        assert refs[0].resolved is False


class TestResolveFormActions:

    def test_form_action_resolved(self, resolver):
        form_actions = [
            {"action": "/login", "method": "POST", "fields": ["user", "pass"],
             "file": "login.html", "line": 5},
        ]
        route_urls = [
            {"pattern": "/login", "methods": ["POST"],
             "args": ["user", "pass"], "file": "auth.py", "line": 10},
        ]
        refs = resolver.resolve_form_actions(form_actions, route_urls)
        form_ref = refs[0]
        assert form_ref.resolved is True

    def test_form_action_unresolved(self, resolver):
        form_actions = [
            {"action": "/signup", "method": "POST", "fields": [],
             "file": "signup.html", "line": 3},
        ]
        refs = resolver.resolve_form_actions(form_actions, [])
        assert refs[0].resolved is False


class TestResolveStaticRefs:

    def test_static_resolved(self, resolver):
        static_refs = [{"filename": "css/style.css", "file": "base.html", "line": 5}]
        refs = resolver.resolve_static_refs(static_refs, {"css/style.css", "js/app.js"})
        assert refs[0].resolved is True

    def test_static_unresolved(self, resolver):
        static_refs = [{"filename": "js/missing.js", "file": "base.html", "line": 8}]
        refs = resolver.resolve_static_refs(static_refs, {"css/style.css"})
        assert refs[0].resolved is False


# ===================================================================
# OverlapChecker
# ===================================================================


class TestOverlapCheckerRouteTemplate:

    def test_no_violations_when_all_vars_match(self, overlap_checker):
        routes = [
            {"pattern": "/home", "methods": ["GET"],
             "context_vars": ["user", "items"], "template": "home.html",
             "file": "views.py", "line": 5},
        ]
        templates = [
            {"name": "home.html", "variables": ["user", "items"],
             "file": "home.html"},
        ]
        violations = overlap_checker.check_route_template(routes, templates)
        assert len(violations) == 0

    def test_missing_template_variable(self, overlap_checker):
        routes = [
            {"pattern": "/dash", "methods": ["GET"],
             "context_vars": ["stats"], "template": "dash.html",
             "file": "views.py", "line": 20},
        ]
        templates = [
            {"name": "dash.html", "variables": ["stats", "title"],
             "file": "dash.html"},
        ]
        violations = overlap_checker.check_route_template(routes, templates)
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) == 1
        assert "title" in errors[0].message

    def test_unused_context_var_is_warning(self, overlap_checker):
        routes = [
            {"pattern": "/p", "methods": ["GET"],
             "context_vars": ["a", "b"], "template": "p.html",
             "file": "v.py", "line": 1},
        ]
        templates = [{"name": "p.html", "variables": ["a"], "file": "p.html"}]
        violations = overlap_checker.check_route_template(routes, templates)
        warnings = [v for v in violations if v.severity == "warning"]
        assert len(warnings) == 1
        assert "b" in warnings[0].message


class TestOverlapCheckerRouteJsFetch:

    def test_no_violation_when_fields_match(self, overlap_checker):
        routes = [
            {"pattern": "/api/data", "methods": ["GET"],
             "context_vars": ["x", "y"], "template": "",
             "file": "api.py", "line": 1},
        ]
        fetch_calls = [
            {"url": "/api/data", "expected_fields": ["x"],
             "method": "GET", "file": "app.js", "line": 5},
        ]
        assert overlap_checker.check_route_js_fetch(routes, fetch_calls) == []

    def test_missing_field_violation(self, overlap_checker):
        routes = [
            {"pattern": "/api/data", "methods": ["GET"],
             "context_vars": ["x"], "template": "",
             "file": "api.py", "line": 1},
        ]
        fetch_calls = [
            {"url": "/api/data", "expected_fields": ["x", "missing_field"],
             "method": "GET", "file": "app.js", "line": 5},
        ]
        violations = overlap_checker.check_route_js_fetch(routes, fetch_calls)
        assert len(violations) == 1
        assert "missing_field" in violations[0].message


class TestOverlapCheckerModelDbSchema:

    def test_matching_model_and_table(self, overlap_checker):
        models = [
            {"name": "User", "columns": [
                {"name": "id", "type": "Integer", "nullable": False},
                {"name": "name", "type": "String", "nullable": False},
            ]},
        ]
        tables = [
            {"name": "users", "columns": [
                {"name": "id", "type": "INTEGER", "nullable": False},
                {"name": "name", "type": "VARCHAR", "nullable": False},
            ]},
        ]
        assert overlap_checker.check_model_db_schema(models, tables) == []

    def test_no_matching_table(self, overlap_checker):
        models = [{"name": "Widget", "columns": [{"name": "id", "type": "Integer", "nullable": False}]}]
        violations = overlap_checker.check_model_db_schema(models, [])
        assert len(violations) == 1
        assert violations[0].severity == "error"


class TestOverlapCheckerJsDomHtml:

    def test_matching_id(self, overlap_checker):
        js_refs = [{"element_id": "app", "file": "a.js", "line": 1, "method": "getElementById"}]
        assert overlap_checker.check_js_dom_html(js_refs, {"app"}) == []

    def test_missing_id(self, overlap_checker):
        js_refs = [{"element_id": "missing", "file": "a.js", "line": 1, "method": "getElementById"}]
        violations = overlap_checker.check_js_dom_html(js_refs, {"other"})
        assert len(violations) == 1


class TestOverlapCheckerJsClassCss:

    def test_no_missing(self, overlap_checker):
        assert overlap_checker.check_js_class_css({"active"}, {"active", "hidden"}) == []

    def test_missing_class(self, overlap_checker):
        violations = overlap_checker.check_js_class_css({"fancy"}, {"basic"})
        assert len(violations) == 1
        assert violations[0].kind == OverlapKind.JS_CLASS_CSS


class TestOverlapCheckerFormRoute:

    def test_matching_form(self, overlap_checker):
        forms = [{"action": "/submit", "method": "POST", "fields": ["name"],
                  "file": "f.html", "line": 1}]
        routes = [{"pattern": "/submit", "methods": ["POST"],
                   "context_vars": ["name"], "template": "", "file": "v.py", "line": 1}]
        assert overlap_checker.check_form_route(forms, routes) == []

    def test_broken_form(self, overlap_checker):
        forms = [{"action": "/nowhere", "method": "POST", "fields": [],
                  "file": "f.html", "line": 1}]
        violations = overlap_checker.check_form_route(forms, [])
        assert len(violations) == 1


class TestOverlapCheckerTemplateCss:

    def test_matching_classes(self, overlap_checker):
        assert overlap_checker.check_template_css({"btn"}, {"btn", "card"}) == []

    def test_missing_class(self, overlap_checker):
        violations = overlap_checker.check_template_css({"missing-cls"}, {"btn"})
        assert len(violations) == 1


class TestOverlapCheckerAuthSession:

    def test_auth_with_session(self, overlap_checker):
        auth = [{"route": "/admin", "file": "v.py", "line": 1}]
        session = [{"key": "user_id", "file": "v.py", "line": 5}]
        assert overlap_checker.check_auth_session(auth, session) == []

    def test_auth_no_session(self, overlap_checker):
        auth = [{"route": "/admin", "file": "v.py", "line": 1}]
        violations = overlap_checker.check_auth_session(auth, [])
        assert len(violations) >= 1


class TestOverlapCheckerDbConstraintHandler:

    def test_not_null_violation(self, overlap_checker):
        constraints = [{"table": "users", "column": "email", "constraint_type": "NOT NULL"}]
        handlers = [{"table": "users", "column": "email", "sets_null": True,
                     "file": "h.py", "line": 10}]
        violations = overlap_checker.check_db_constraint_handler(constraints, handlers)
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) == 1

    def test_no_violation(self, overlap_checker):
        constraints = [{"table": "users", "column": "name", "constraint_type": "NOT NULL"}]
        handlers = [{"table": "users", "column": "name", "sets_null": False,
                     "file": "h.py", "line": 5}]
        violations = overlap_checker.check_db_constraint_handler(constraints, handlers)
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) == 0


class TestOverlapCheckerErrorHandlerJs:

    def test_unhandled_error_code(self, overlap_checker):
        error_handlers = [{"status_code": 429, "file": "app.py"}]
        js_catch = [{"handles_status": [400, 500], "file": "app.js"}]
        violations = overlap_checker.check_error_handler_js(error_handlers, js_catch)
        assert len(violations) == 1
        assert "429" in violations[0].message

    def test_all_handled(self, overlap_checker):
        error_handlers = [{"status_code": 404, "file": "app.py"}]
        js_catch = [{"handles_status": [404, 500], "file": "app.js"}]
        assert overlap_checker.check_error_handler_js(error_handlers, js_catch) == []


class TestOverlapCheckerCheckAll:

    def test_check_all_empty_data(self, overlap_checker):
        assert overlap_checker.check_all({}) == []


# ===================================================================
# CrossLanguageMorphismBuilder
# ===================================================================


class TestMorphismBuilder:

    def test_build_morphisms_from_refs(self, morphism_builder):
        ref = CrossReference(
            source_file="v.py", source_line=5, source_layer="python",
            target_name="user", target_layer="jinja2",
            reference_type="template_variable", resolved=True,
            resolution_target="index.html:user",
        )
        morphisms = morphism_builder.build_morphisms([ref])
        assert len(morphisms) == 1
        m = morphisms[0]
        assert m["resolved"] is True
        assert m["kind"] == "template_variable"
        assert m["id"].startswith("m-")

    def test_build_context_provision(self, morphism_builder):
        render_calls = [{"template": "t.html", "kwargs": ["a"], "file": "v.py", "line": 1}]
        template_vars = [{"template": "t.html", "variable": "a", "file": "t.html", "line": 2}]
        morphisms = morphism_builder.build_context_provision_morphisms(render_calls, template_vars)
        assert len(morphisms) == 1
        assert morphisms[0]["resolved"] is True

    def test_build_dom_selection(self, morphism_builder):
        js_refs = [{"element_id": "nav", "file": "a.js", "line": 3, "method": "getElementById"}]
        morphisms = morphism_builder.build_dom_selection_morphisms(js_refs, {"nav"})
        assert morphisms[0]["resolved"] is True

    def test_build_orm_mapping(self, morphism_builder):
        models = [{"name": "User", "columns": [{"name": "id", "type": "Integer", "nullable": False}]}]
        tables = [{"name": "users", "columns": [{"name": "id", "type": "INTEGER", "nullable": False}]}]
        morphisms = morphism_builder.build_orm_mapping_morphisms(models, tables)
        assert morphisms[0]["resolved"] is True

    def test_build_event_binding(self, morphism_builder):
        handlers = [{"element_id": "btn", "event": "click", "file": "a.js", "line": 1}]
        elements = [{"id": "btn", "tag": "button", "file": "index.html"}]
        morphisms = morphism_builder.build_event_binding_morphisms(handlers, elements)
        assert morphisms[0]["resolved"] is True

    def test_build_selector_match(self, morphism_builder):
        selectors = [{"selector": ".active", "file": "style.css", "line": 1}]
        elements = [{"id": "", "classes": ["active"], "tag": "div", "file": "index.html"}]
        morphisms = morphism_builder.build_selector_match_morphisms(selectors, elements)
        assert morphisms[0]["resolved"] is True

    def test_build_api_contract(self, morphism_builder):
        routes = [{"pattern": "/api/data", "methods": ["GET"],
                   "context_vars": ["x"], "template": "", "file": "v.py", "line": 1}]
        fetch_calls = [{"url": "/api/data", "expected_fields": ["x"],
                        "method": "GET", "file": "app.js", "line": 5}]
        morphisms = morphism_builder.build_api_contract_morphisms(routes, fetch_calls)
        assert len(morphisms) == 1
        assert morphisms[0]["resolved"] is True


# ===================================================================
# MorphismGraph
# ===================================================================


class TestMorphismGraph:

    def _make_morphism(self, mid, src, tgt, kind="test"):
        return {"id": mid, "source": src, "target": tgt,
                "kind": kind, "resolved": True, "details": {}}

    def test_add_and_query(self):
        g = MorphismGraph()
        m = self._make_morphism("m1", "python:v.py:1", "jinja2:user")
        g.add_morphism(m)
        assert g.edge_count == 1
        assert g.node_count == 2
        assert len(g.morphisms_from("python:v.py:1")) == 1
        assert len(g.morphisms_to("jinja2:user")) == 1

    def test_path_between_direct(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "A", "B"))
        path = g.path_between("A", "B")
        assert path is not None
        assert len(path) == 1

    def test_path_between_multi_hop(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "A", "B"))
        g.add_morphism(self._make_morphism("m2", "B", "C"))
        path = g.path_between("A", "C")
        assert path is not None
        assert len(path) == 2

    def test_path_between_no_path(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "A", "B"))
        assert g.path_between("B", "A") is None

    def test_path_same_node(self):
        g = MorphismGraph()
        assert g.path_between("X", "X") == []

    def test_connected_components(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "A", "B"))
        g.add_morphism(self._make_morphism("m2", "C", "D"))
        components = g.connected_components()
        assert len(components) == 2

    def test_cross_boundary_morphisms(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "js:app.js:1", "python:v.py:5"))
        g.add_morphism(self._make_morphism("m2", "python:v.py:1", "python:v.py:2"))
        cross = g.cross_boundary_morphisms()
        assert len(cross) == 1
        assert cross[0]["id"] == "m1"

    def test_all_morphisms(self):
        g = MorphismGraph()
        g.add_morphism(self._make_morphism("m1", "A", "B"))
        g.add_morphism(self._make_morphism("m2", "B", "C"))
        assert len(g.all_morphisms) == 2


# ===================================================================
# APIContractChecker
# ===================================================================


class TestAPIContractChecker:

    def test_check_request_schema_missing_field(self, contract_checker):
        route_def = {"pattern": "/api/users", "required_fields": ["name", "email"],
                     "optional_fields": []}
        js_fetch = {"url": "/api/users", "method": "POST",
                    "body_fields": ["name"], "expected_response_fields": []}
        violations = contract_checker.check_request_schema(route_def, js_fetch)
        assert len(violations) == 1
        assert "email" in violations[0].message

    def test_check_request_schema_extra_field(self, contract_checker):
        route_def = {"pattern": "/api/users", "required_fields": ["name"],
                     "optional_fields": []}
        js_fetch = {"url": "/api/users", "method": "POST",
                    "body_fields": ["name", "extra"], "expected_response_fields": []}
        violations = contract_checker.check_request_schema(route_def, js_fetch)
        warnings = [v for v in violations if v.severity == "warning"]
        assert len(warnings) == 1

    def test_check_response_schema_missing(self, contract_checker):
        route_return = {"fields": ["id", "name"], "nullable_fields": [], "status_codes": [200]}
        js_response = {"accessed_fields": ["id", "name", "avatar"],
                       "null_checked_fields": [], "handled_status_codes": [200]}
        violations = contract_checker.check_response_schema(route_return, js_response)
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) == 1
        assert "avatar" in errors[0].message

    def test_check_response_nullable_unchecked(self, contract_checker):
        route_return = {"fields": ["bio"], "nullable_fields": ["bio"], "status_codes": [200]}
        js_response = {"accessed_fields": ["bio"], "null_checked_fields": [],
                       "handled_status_codes": [200]}
        violations = contract_checker.check_response_schema(route_return, js_response)
        warnings = [v for v in violations if v.severity == "warning"]
        assert len(warnings) == 1

    def test_check_error_codes_unhandled(self, contract_checker):
        route_handlers = [{"status_code": 403, "file": "app.py"}]
        js_handlers = [{"handles_status": [401, 500], "file": "app.js"}]
        violations = contract_checker.check_error_codes(route_handlers, js_handlers)
        assert len(violations) == 1
        assert "403" in violations[0].message

    def test_check_http_methods_mismatch(self, contract_checker):
        violations = contract_checker.check_http_methods({"GET"}, "POST")
        assert len(violations) == 1

    def test_check_http_methods_ok(self, contract_checker):
        assert contract_checker.check_http_methods({"GET", "POST"}, "post") == []

    def test_check_content_type_mismatch(self, contract_checker):
        violations = contract_checker.check_content_type("application/json", "text/html")
        assert len(violations) == 1

    def test_check_content_type_ok(self, contract_checker):
        assert contract_checker.check_content_type("application/json", "application/json") == []

    def test_check_content_type_wildcard(self, contract_checker):
        assert contract_checker.check_content_type("application/json", "*/*") == []

    def test_check_content_type_empty(self, contract_checker):
        assert contract_checker.check_content_type("", "application/json") == []


# ===================================================================
# SchemaComparer
# ===================================================================


class TestSchemaComparer:

    def test_compare_matching(self, schema_comparer):
        server = {"fields": [{"name": "id", "type": "int", "nullable": False}]}
        client = {"fields": [{"name": "id", "type": "number", "handles_null": False}]}
        diffs = schema_comparer.compare_schemas(server, client)
        assert all(d["severity"] != "error" for d in diffs)

    def test_compare_type_mismatch(self, schema_comparer):
        server = {"fields": [{"name": "count", "type": "int", "nullable": False}]}
        client = {"fields": [{"name": "count", "type": "string", "handles_null": False}]}
        diffs = schema_comparer.compare_schemas(server, client)
        errors = [d for d in diffs if d["severity"] == "error"]
        assert len(errors) == 1
        assert "type mismatch" in errors[0]["issue"]

    def test_compare_missing_client_field(self, schema_comparer):
        server = {"fields": [{"name": "a", "type": "str", "nullable": False}]}
        client = {"fields": [{"name": "a", "type": "string", "handles_null": False},
                             {"name": "b", "type": "string", "handles_null": False}]}
        diffs = schema_comparer.compare_schemas(server, client)
        client_only = [d for d in diffs if "client accesses" in d["issue"]]
        assert len(client_only) == 1

    def test_compare_nullability_warning(self, schema_comparer):
        server = {"fields": [{"name": "bio", "type": "str", "nullable": True}]}
        client = {"fields": [{"name": "bio", "type": "string", "handles_null": False}]}
        diffs = schema_comparer.compare_schemas(server, client)
        warnings = [d for d in diffs if d["severity"] == "warning"]
        assert len(warnings) == 1


# ===================================================================
# WebTrustLevel
# ===================================================================


class TestWebTrustLevel:

    def test_values_exist(self):
        assert len(WebTrustLevel) == 12

    def test_ordering_conceptual(self):
        # DB_CONSTRAINT_ENFORCED is highest, USER_INPUT is lowest
        assert WebTrustLevel.DB_CONSTRAINT_ENFORCED.value == "db_constraint_enforced"
        assert WebTrustLevel.USER_INPUT.value == "user_input"

    def test_str_enum(self):
        assert isinstance(WebTrustLevel.SERVER_VALIDATED, str)
        assert WebTrustLevel.SERVER_VALIDATED == "server_validated"


# ===================================================================
# WebTrustChecker
# ===================================================================


class TestWebTrustChecker:

    def test_check_trust_promotion_allowed(self, trust_checker):
        morphism = {"kind": "template_render", "source": "python:v.py:1",
                    "target": "jinja2:t.html:1"}
        result = trust_checker.check_trust_promotion(
            morphism, "server_validated", "template_type_checked")
        assert result is True

    def test_check_trust_promotion_blocked_across_boundary(self, trust_checker):
        morphism = {"kind": "api_call", "source": "js:app.js:1",
                    "target": "python:v.py:1"}
        result = trust_checker.check_trust_promotion(
            morphism, "client_validated", "server_validated")
        assert result is False

    def test_max_trust_at_layer(self, trust_checker):
        assert trust_checker.max_trust_at_layer("sql") == WebTrustLevel.DB_CONSTRAINT_ENFORCED
        assert trust_checker.max_trust_at_layer("python") == WebTrustLevel.SERVER_VALIDATED
        assert trust_checker.max_trust_at_layer("js") == WebTrustLevel.JS_TYPE_CHECKED
        assert trust_checker.max_trust_at_layer("html") == WebTrustLevel.CLIENT_VALIDATED
        assert trust_checker.max_trust_at_layer("unknown") == WebTrustLevel.USER_INPUT

    def test_check_never_trust_client_clean(self, trust_checker):
        project_data = {
            "routes": [{"pattern": "/submit", "methods": ["POST"], "file": "v.py", "line": 1}],
            "client_validations": [{"route": "/submit", "validation_type": "js", "file": "a.js", "line": 1}],
            "server_validations": [{"route": "/submit", "validation_type": "flask", "file": "v.py", "line": 2}],
        }
        violations = trust_checker.check_never_trust_client(project_data)
        assert len(violations) == 0

    def test_check_never_trust_client_violation(self, trust_checker):
        project_data = {
            "routes": [],
            "client_validations": [{"route": "/submit", "validation_type": "js", "file": "a.js", "line": 1}],
            "server_validations": [],
        }
        violations = trust_checker.check_never_trust_client(project_data)
        assert len(violations) >= 1

    def test_mutation_route_no_validation(self, trust_checker):
        project_data = {
            "routes": [{"pattern": "/delete", "methods": ["DELETE"], "file": "v.py", "line": 1}],
            "client_validations": [],
            "server_validations": [],
        }
        violations = trust_checker.check_never_trust_client(project_data)
        assert len(violations) >= 1


# ===================================================================
# TrustTransportChecker
# ===================================================================


class TestTrustTransportChecker:

    def test_empty_chain(self, trust_transport):
        result = trust_transport.verify_trust_transport([])
        assert result["valid"] is True
        assert result["boundary_crossings"] == 0

    def test_server_chain(self, trust_transport):
        chain = [
            {"source": "python:v.py:1", "target": "orm:model.py:5",
             "kind": "orm_mapping", "resolved": True},
        ]
        result = trust_transport.verify_trust_transport(chain)
        assert result["valid"] is True

    def test_cross_boundary_chain(self, trust_transport):
        chain = [
            {"source": "js:app.js:1", "target": "python:v.py:1",
             "kind": "fetch", "resolved": True},
        ]
        result = trust_transport.verify_trust_transport(chain)
        assert result["boundary_crossings"] == 1


# ===================================================================
# QuickCheck
# ===================================================================


class TestQuickCheck:

    def test_check_template_vars_all_present(self):
        py_src = "render_template('page.html', user=u, title=t)"
        tpl_src = "<h1>{{ title }}</h1><p>{{ user }}</p>"
        assert QuickCheck.check_template_vars(py_src, tpl_src) == []

    def test_check_template_vars_missing(self):
        py_src = "render_template('page.html', user=u)"
        tpl_src = "<h1>{{ title }}</h1><p>{{ user }}</p>"
        missing = QuickCheck.check_template_vars(py_src, tpl_src)
        assert "title" in missing

    def test_check_template_vars_builtin_excluded(self):
        py_src = "render_template('page.html')"
        tpl_src = "{% for item in loop %}{{ request.path }}{% endfor %}"
        missing = QuickCheck.check_template_vars(py_src, tpl_src)
        assert "loop" not in missing
        assert "request" not in missing

    def test_check_dom_refs_all_present(self):
        js_src = "document.getElementById('nav')"
        html_src = '<div id="nav">menu</div>'
        assert QuickCheck.check_dom_refs(js_src, html_src) == []

    def test_check_dom_refs_missing(self):
        js_src = "document.getElementById('sidebar')"
        html_src = '<div id="nav">menu</div>'
        missing = QuickCheck.check_dom_refs(js_src, html_src)
        assert "sidebar" in missing

    def test_check_dom_refs_query_selector(self):
        js_src = "document.querySelector('#chart')"
        html_src = '<canvas id="chart"></canvas>'
        assert QuickCheck.check_dom_refs(js_src, html_src) == []

    def test_check_css_classes_all_defined(self):
        html_src = '<div class="btn primary">click</div>'
        css_src = ".btn { padding: 4px; }\n.primary { color: blue; }"
        assert QuickCheck.check_css_classes(html_src, css_src) == []

    def test_check_css_classes_missing(self):
        html_src = '<div class="btn highlight">click</div>'
        css_src = ".btn { padding: 4px; }"
        missing = QuickCheck.check_css_classes(html_src, css_src)
        assert "highlight" in missing


# ===================================================================
# CrossLanguageAnalyzer (integration)
# ===================================================================


class TestCrossLanguageAnalyzer:

    def test_analyze_empty_project(self):
        analyzer = CrossLanguageAnalyzer()
        report = analyzer.analyze({})
        assert isinstance(report, DescentReport)
        assert report.coverage_score == 1.0  # 0/0 = 1.0

    def test_analyze_clean_project(self):
        analyzer = CrossLanguageAnalyzer()
        project_data = {
            "render_calls": [
                {"template": "home.html", "kwargs": ["user"],
                 "file": "views.py", "line": 5},
            ],
            "template_variables": [
                {"template": "home.html", "variable": "user",
                 "file": "home.html", "line": 3},
            ],
            "routes": [
                {"pattern": "/home", "methods": ["GET"],
                 "context_vars": ["user"], "template": "home.html",
                 "file": "views.py", "line": 5},
            ],
            "templates": [
                {"name": "home.html", "variables": ["user"], "file": "home.html"},
            ],
            "html_ids": set(),
            "js_dom_refs": [],
            "used_classes": set(),
            "defined_classes": set(),
            "route_responses": [],
            "fetch_expectations": [],
            "form_actions": [],
            "route_urls": [],
            "static_refs": [],
            "static_files": set(),
            "server_validations": [],
            "client_validations": [],
        }
        report = analyzer.analyze(project_data)
        assert report.coverage_score == 1.0
        assert report.error_count == 0

    def test_analyze_with_violation(self):
        analyzer = CrossLanguageAnalyzer()
        project_data = {
            "render_calls": [
                {"template": "page.html", "kwargs": [],
                 "file": "views.py", "line": 1},
            ],
            "template_variables": [
                {"template": "page.html", "variable": "title",
                 "file": "page.html", "line": 1},
            ],
            "routes": [
                {"pattern": "/page", "methods": ["GET"],
                 "context_vars": [], "template": "page.html",
                 "file": "views.py", "line": 1},
            ],
            "templates": [
                {"name": "page.html", "variables": ["title"], "file": "page.html"},
            ],
        }
        report = analyzer.analyze(project_data)
        assert report.error_count >= 1
        assert len(report.unresolved_references) >= 1


# ===================================================================
# TrustBoundary model
# ===================================================================


class TestTrustBoundary:

    def test_round_trip(self):
        tb = TrustBoundary(source_layer="js", target_layer="python",
                           requires_revalidation=True)
        d = tb.to_dict()
        restored = TrustBoundary.from_dict(d)
        assert restored.source_layer == "js"
        assert restored.requires_revalidation is True
