"""Tests for src/jugeo/webapp/site/ — the web application site model."""
from __future__ import annotations
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.webapp.site import (
    WebCoordinateKind, CrossLanguageMorphismKind,
    WebCoordinate, WebMorphism, WebCoveringFamily,
    RequestLifecycle, DescentCondition, DescentViolation, LanguageLayer,
    LANGUAGE_LAYERS, WebApplicationSite,
    LifecycleStage, DataFlowEdge, RequestLifecycleBuilder,
    STANDARD_LIFECYCLE_STAGES, lifecycle_overlap_conditions, trace_data_flow,
    WebTopology, is_covering, refinement, generate_standard_covers, sieve_from_morphisms,
    WebSiteToSite, SiteToWebSite, WebSiteAnalyzer, generate_report,
)

# ── Coordinate Kinds ───────────────────────────────────────────────────────────

class TestWebCoordinateKind:
    def test_all_python_kinds_in_python_layer(self):
        assert WebCoordinateKind.ROUTE_HANDLER.language_layer() == "python"
        assert WebCoordinateKind.MODEL_CLASS.language_layer() == "python"
        assert WebCoordinateKind.BLUEPRINT.language_layer() == "python"

    def test_all_javascript_kinds_in_javascript_layer(self):
        assert WebCoordinateKind.JS_MODULE.language_layer() == "javascript"
        assert WebCoordinateKind.JS_FETCH_CALL.language_layer() == "javascript"

    def test_template_layer(self):
        assert WebCoordinateKind.TEMPLATE_FILE.language_layer() == "template"
        assert WebCoordinateKind.TEMPLATE_VARIABLE.language_layer() == "template"

    def test_css_layer(self):
        assert WebCoordinateKind.CSS_RULE.language_layer() == "css"
        assert WebCoordinateKind.CSS_ANIMATION.language_layer() == "css"

    def test_html_layer(self):
        assert WebCoordinateKind.HTML_ELEMENT.language_layer() == "html"
        assert WebCoordinateKind.HTML_FORM.language_layer() == "html"

    def test_database_layer(self):
        assert WebCoordinateKind.DB_TABLE.language_layer() == "database"
        assert WebCoordinateKind.DB_MIGRATION.language_layer() == "database"

    def test_http_layer(self):
        assert WebCoordinateKind.API_ENDPOINT.language_layer() == "http"
        assert WebCoordinateKind.API_ERROR_CODE.language_layer() == "http"

    def test_auth_layer(self):
        assert WebCoordinateKind.SESSION_KEY.language_layer() == "auth"
        assert WebCoordinateKind.AUTH_DECORATOR.language_layer() == "auth"

    def test_is_server_side_python(self):
        assert WebCoordinateKind.ROUTE_HANDLER.is_server_side() is True

    def test_is_client_side_javascript(self):
        assert WebCoordinateKind.JS_MODULE.is_client_side() is True

    def test_is_not_client_side_python(self):
        assert WebCoordinateKind.ROUTE_HANDLER.is_client_side() is False

    def test_is_not_server_side_css(self):
        assert WebCoordinateKind.CSS_RULE.is_server_side() is False

    def test_total_count_at_least_40(self):
        assert len(WebCoordinateKind) >= 40

    def test_all_have_language_layer(self):
        for kind in WebCoordinateKind:
            layer = kind.language_layer()
            assert layer != "unknown", f"{kind} has unknown layer"

    def test_string_value(self):
        assert WebCoordinateKind.ROUTE_HANDLER == "route_handler"


# ── Morphism Kinds ─────────────────────────────────────────────────────────────

class TestCrossLanguageMorphismKind:
    def test_api_contract_crosses_trust_boundary(self):
        assert CrossLanguageMorphismKind.API_CONTRACT.crosses_trust_boundary() is True

    def test_dom_selection_does_not_cross(self):
        assert CrossLanguageMorphismKind.DOM_SELECTION.crosses_trust_boundary() is False

    def test_selector_match_does_not_cross(self):
        assert CrossLanguageMorphismKind.SELECTOR_MATCH.crosses_trust_boundary() is False

    def test_orm_mapping_source_layer(self):
        assert CrossLanguageMorphismKind.ORM_MAPPING.source_layer() == "python"

    def test_orm_mapping_target_layer(self):
        assert CrossLanguageMorphismKind.ORM_MAPPING.target_layer() == "database"

    def test_template_emission_source(self):
        assert CrossLanguageMorphismKind.TEMPLATE_EMISSION.source_layer() == "template"

    def test_error_propagation_crosses_trust(self):
        assert CrossLanguageMorphismKind.ERROR_PROPAGATION.crosses_trust_boundary() is True

    def test_total_count_at_least_25(self):
        assert len(CrossLanguageMorphismKind) >= 25

    def test_string_value(self):
        assert CrossLanguageMorphismKind.API_CONTRACT == "api_contract"

    def test_all_have_layers(self):
        for kind in CrossLanguageMorphismKind:
            assert kind.source_layer() != ""
            assert kind.target_layer() != ""


# ── Models ────────────────────────────────────────────────────────────────────

class TestWebCoordinate:
    def test_create_basic(self):
        c = WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="get_user")
        assert c.id == "c1"
        assert c.kind == WebCoordinateKind.ROUTE_HANDLER

    def test_auto_language_layer(self):
        c = WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="get_user")
        assert c.language_layer == "python"

    def test_to_dict(self):
        c = WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="get_user", file_path="app.py", line_number=10)
        d = c.to_dict()
        assert d["id"] == "c1"
        assert d["kind"] == "route_handler"
        assert d["name"] == "get_user"
        assert d["file_path"] == "app.py"
        assert d["line_number"] == 10

    def test_from_dict_round_trip(self):
        c = WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="get_user")
        c2 = WebCoordinate.from_dict(c.to_dict())
        assert c2.id == c.id
        assert c2.kind == c.kind
        assert c2.name == c.name


class TestWebMorphism:
    def test_create_basic(self):
        m = WebMorphism(id="m1", source_id="c1", target_id="c2", kind=CrossLanguageMorphismKind.CONTEXT_PROVISION)
        assert m.id == "m1"
        assert m.source_id == "c1"

    def test_to_dict(self):
        m = WebMorphism(id="m1", source_id="c1", target_id="c2", kind=CrossLanguageMorphismKind.API_CONTRACT, label="user API")
        d = m.to_dict()
        assert d["kind"] == "api_contract"
        assert d["label"] == "user API"

    def test_from_dict_round_trip(self):
        m = WebMorphism(id="m1", source_id="c1", target_id="c2", kind=CrossLanguageMorphismKind.ORM_MAPPING)
        m2 = WebMorphism.from_dict(m.to_dict())
        assert m2.id == m.id
        assert m2.kind == m.kind


class TestWebCoveringFamily:
    def test_create(self):
        f = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c2", "c3"], label="test")
        assert f.id == "f1"
        assert len(f.member_ids) == 2

    def test_to_dict_from_dict(self):
        f = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c2", "c3"])
        f2 = WebCoveringFamily.from_dict(f.to_dict())
        assert f2.member_ids == ["c2", "c3"]


class TestRequestLifecycle:
    def test_create(self):
        r = RequestLifecycle(id="r1", route_url="/users/<id>", method="GET",
                             stages=["browser.user_action", "flask.view_function"])
        assert r.route_url == "/users/<id>"
        assert len(r.stages) == 2

    def test_to_dict_from_dict(self):
        r = RequestLifecycle(id="r1", route_url="/api/data", method="POST", stages=["a", "b"])
        r2 = RequestLifecycle.from_dict(r.to_dict())
        assert r2.route_url == r.route_url
        assert r2.stages == r.stages


class TestDescentCondition:
    def test_create(self):
        d = DescentCondition(
            id="dc1", overlap_name="route∩template",
            description="Template variables must be provided",
            left_coordinate_id="c1", right_coordinate_id="c2",
            condition_type="template_variable"
        )
        assert d.condition_type == "template_variable"

    def test_to_dict_from_dict(self):
        d = DescentCondition(id="dc1", overlap_name="x", description="y", left_coordinate_id="a", right_coordinate_id="b", condition_type="api_contract")
        d2 = DescentCondition.from_dict(d.to_dict())
        assert d2.id == d.id


class TestDescentViolation:
    def test_create(self):
        v = DescentViolation(id="v1", condition_id="dc1", message="Missing variable 'user'", severity="error", repair_hint="Add user= to render_template")
        assert v.severity == "error"

    def test_to_dict_from_dict(self):
        v = DescentViolation(id="v1", condition_id="dc1", message="test", repair_hint="fix it")
        v2 = DescentViolation.from_dict(v.to_dict())
        assert v2.message == v.message


class TestLanguageLayer:
    def test_predefined_python(self):
        assert "python" in LANGUAGE_LAYERS
        assert LANGUAGE_LAYERS["python"].is_server_side is True

    def test_predefined_javascript(self):
        assert LANGUAGE_LAYERS["javascript"].is_server_side is False

    def test_to_dict_from_dict(self):
        ll = LANGUAGE_LAYERS["python"]
        ll2 = LanguageLayer.from_dict(ll.to_dict())
        assert ll2.name == ll.name


# ── WebApplicationSite ────────────────────────────────────────────────────────

def make_simple_site() -> WebApplicationSite:
    """Create a simple site for testing."""
    site = WebApplicationSite(name="test_app")

    # Python layer
    site.add_coordinate(WebCoordinate(id="route1", kind=WebCoordinateKind.ROUTE_HANDLER, name="get_user", file_path="app.py"))
    site.add_coordinate(WebCoordinate(id="model1", kind=WebCoordinateKind.MODEL_CLASS, name="User", file_path="models.py"))

    # Template layer
    site.add_coordinate(WebCoordinate(id="tmpl1", kind=WebCoordinateKind.TEMPLATE_FILE, name="user.html", file_path="templates/user.html"))
    site.add_coordinate(WebCoordinate(id="tmplvar1", kind=WebCoordinateKind.TEMPLATE_VARIABLE, name="user.name", file_path="templates/user.html"))

    # JavaScript layer
    site.add_coordinate(WebCoordinate(id="js1", kind=WebCoordinateKind.JS_FETCH_CALL, name="fetchUser", file_path="static/app.js"))

    # Database layer
    site.add_coordinate(WebCoordinate(id="db1", kind=WebCoordinateKind.DB_TABLE, name="users", file_path="schema.sql"))

    # CSS layer
    site.add_coordinate(WebCoordinate(id="css1", kind=WebCoordinateKind.CSS_RULE, name=".user-card", file_path="static/style.css"))

    # HTML layer
    site.add_coordinate(WebCoordinate(id="html1", kind=WebCoordinateKind.HTML_ELEMENT, name="div.user-card", file_path="templates/user.html"))

    # Morphisms
    site.add_morphism(WebMorphism(id="m1", source_id="route1", target_id="tmpl1", kind=CrossLanguageMorphismKind.CONTEXT_PROVISION))
    site.add_morphism(WebMorphism(id="m2", source_id="model1", target_id="db1", kind=CrossLanguageMorphismKind.ORM_MAPPING))
    site.add_morphism(WebMorphism(id="m3", source_id="route1", target_id="js1", kind=CrossLanguageMorphismKind.API_CONTRACT))
    site.add_morphism(WebMorphism(id="m4", source_id="css1", target_id="html1", kind=CrossLanguageMorphismKind.SELECTOR_MATCH))

    return site


class TestWebApplicationSite:
    def test_add_coordinate(self):
        site = WebApplicationSite()
        site.add_coordinate(WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="index"))
        assert len(site.coordinates) == 1

    def test_add_morphism(self):
        site = make_simple_site()
        assert len(site.morphisms) >= 4

    def test_get_coordinate(self):
        site = make_simple_site()
        c = site.get_coordinate("route1")
        assert c is not None
        assert c.name == "get_user"

    def test_get_coordinate_missing(self):
        site = WebApplicationSite()
        assert site.get_coordinate("nonexistent") is None

    def test_coordinates_in_layer(self):
        site = make_simple_site()
        python_coords = site.coordinates_in_layer("python")
        assert any(c.id == "route1" for c in python_coords)
        assert not any(c.id == "js1" for c in python_coords)

    def test_morphisms_between_layers(self):
        site = make_simple_site()
        morphs = site.morphisms_between_layers("python", "template")
        assert len(morphs) >= 1

    def test_cross_language_morphisms(self):
        site = make_simple_site()
        cross = site.cross_language_morphisms()
        cross_ids = {m.id for m in cross}
        assert "m3" in cross_ids  # API_CONTRACT

    def test_build_request_lifecycle_cover(self):
        site = make_simple_site()
        family = site.build_request_lifecycle_cover("/users/<id>", "GET")
        assert family is not None
        assert len(family.member_ids) == 12

    def test_overlap_pairs(self):
        site = make_simple_site()
        pairs = site.overlap_pairs()
        assert len(pairs) >= 4

    def test_descent_conditions(self):
        site = make_simple_site()
        conditions = site.descent_conditions()
        assert isinstance(conditions, list)
        assert all(isinstance(c, DescentCondition) for c in conditions)

    def test_check_descent_returns_list(self):
        site = make_simple_site()
        violations = site.check_descent()
        assert isinstance(violations, list)

    def test_connected_components(self):
        site = make_simple_site()
        components = site.connected_components()
        assert len(components) >= 1
        total = sum(len(c) for c in components)
        assert total == len(site.coordinates)

    def test_language_boundary_graph(self):
        site = make_simple_site()
        graph = site.language_boundary_graph()
        assert isinstance(graph, dict)
        assert "python" in graph

    def test_serialize(self):
        site = make_simple_site()
        data = site.serialize()
        assert "coordinates" in data
        assert "morphisms" in data
        assert "covering_families" in data
        assert len(data["coordinates"]) == len(site.coordinates)

    def test_parse_round_trip(self):
        site = make_simple_site()
        data = site.serialize()
        site2 = WebApplicationSite.parse(data)
        assert len(site2.coordinates) == len(site.coordinates)
        assert len(site2.morphisms) == len(site.morphisms)

    def test_add_covering_family(self):
        site = WebApplicationSite()
        site.add_coordinate(WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="index"))
        f = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c1"])
        site.add_covering_family(f)
        assert len(site.covering_families) == 1

    def test_empty_site_connected_components(self):
        site = WebApplicationSite()
        assert site.connected_components() == []

    def test_empty_site_language_boundary_graph(self):
        site = WebApplicationSite()
        assert site.language_boundary_graph() == {}


# ── Request Lifecycle ────────────────────────────────────────────────────────

class TestLifecycleStage:
    def test_standard_stages_count(self):
        assert len(STANDARD_LIFECYCLE_STAGES) == 12

    def test_first_stage(self):
        assert STANDARD_LIFECYCLE_STAGES[0].name == "browser.user_action"

    def test_last_stage(self):
        assert STANDARD_LIFECYCLE_STAGES[-1].name == "browser.paint"

    def test_stages_have_layers(self):
        for stage in STANDARD_LIFECYCLE_STAGES:
            assert stage.layer != ""

    def test_to_dict_from_dict(self):
        stage = STANDARD_LIFECYCLE_STAGES[0]
        d = stage.to_dict()
        stage2 = LifecycleStage.from_dict(d)
        assert stage2.name == stage.name
        assert stage2.layer == stage.layer


class TestRequestLifecycleBuilder:
    def test_build_returns_lifecycle_and_family(self):
        builder = RequestLifecycleBuilder()
        lifecycle, family = builder.build("/users", "GET")
        assert lifecycle.route_url == "/users"
        assert len(lifecycle.stages) == 12
        assert len(family.member_ids) == 12

    def test_build_lifecycle_only(self):
        builder = RequestLifecycleBuilder()
        lifecycle = builder.build_lifecycle("/api/data", "POST")
        assert lifecycle.method == "POST"
        assert len(lifecycle.stages) == 12

    def test_lifecycle_stages_are_standard(self):
        builder = RequestLifecycleBuilder()
        lifecycle, _ = builder.build("/", "GET")
        assert lifecycle.stages[0] == "browser.user_action"
        assert lifecycle.stages[-1] == "browser.paint"


class TestLifecycleOverlapConditions:
    def test_generates_conditions(self):
        builder = RequestLifecycleBuilder()
        lifecycle, _ = builder.build("/users", "GET")
        conditions = lifecycle_overlap_conditions(lifecycle)
        assert len(conditions) >= 11

    def test_conditions_are_descent_conditions(self):
        builder = RequestLifecycleBuilder()
        lifecycle, _ = builder.build("/users", "GET")
        conditions = lifecycle_overlap_conditions(lifecycle)
        assert all(isinstance(c, DescentCondition) for c in conditions)


class TestTraceDataFlow:
    def test_returns_list(self):
        site = make_simple_site()
        builder = RequestLifecycleBuilder()
        lifecycle, _ = builder.build("/users", "GET")
        edges = trace_data_flow(site, lifecycle)
        assert isinstance(edges, list)


# ── Topology ─────────────────────────────────────────────────────────────────

class TestWebTopology:
    def test_sieve_from_morphisms(self):
        m1 = WebMorphism(id="m1", source_id="c1", target_id="c3", kind=CrossLanguageMorphismKind.CONTEXT_PROVISION)
        m2 = WebMorphism(id="m2", source_id="c2", target_id="c3", kind=CrossLanguageMorphismKind.TEMPLATE_RENDERING)
        sieve = sieve_from_morphisms("c3", [m1, m2])
        assert "c1" in sieve
        assert "c2" in sieve
        assert "c3" not in sieve

    def test_sieve_empty_when_no_incoming(self):
        m1 = WebMorphism(id="m1", source_id="c1", target_id="c2", kind=CrossLanguageMorphismKind.API_CONTRACT)
        sieve = sieve_from_morphisms("c3", [m1])
        assert len(sieve) == 0

    def test_generate_standard_covers(self):
        site = make_simple_site()
        covers = generate_standard_covers(site)
        assert isinstance(covers, list)

    def test_is_covering_empty_family(self):
        site = make_simple_site()
        family = WebCoveringFamily(id="f1", base_id="route1", member_ids=[])
        assert is_covering(family, site) is False

    def test_is_covering_with_members(self):
        site = make_simple_site()
        family = WebCoveringFamily(id="f1", base_id="route1", member_ids=["tmpl1", "js1", "db1"])
        result = is_covering(family, site)
        assert isinstance(result, bool)

    def test_refinement_identity(self):
        f = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c2", "c3"])
        assert refinement(f, f) is True

    def test_refinement_subset(self):
        f1 = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c2"])
        f2 = WebCoveringFamily(id="f2", base_id="c1", member_ids=["c2", "c3"])
        assert refinement(f1, f2) is True

    def test_refinement_superset(self):
        f1 = WebCoveringFamily(id="f1", base_id="c1", member_ids=["c2", "c3", "c4"])
        f2 = WebCoveringFamily(id="f2", base_id="c1", member_ids=["c2"])
        assert refinement(f1, f2) is False


# ── Integration ───────────────────────────────────────────────────────────────

class TestWebSiteToSite:
    def test_returns_dict(self):
        site = make_simple_site()
        result = WebSiteToSite(site)
        assert isinstance(result, dict)

    def test_has_expected_keys(self):
        site = make_simple_site()
        result = WebSiteToSite(site)
        assert "coordinates" in result or "nodes" in result or len(result) > 0

    def test_site_to_web_site_round_trip(self):
        site = make_simple_site()
        site_dict = WebSiteToSite(site)
        site2 = SiteToWebSite(site_dict)
        assert isinstance(site2, WebApplicationSite)
        assert len(site2.coordinates) == len(site.coordinates)


class TestWebSiteAnalyzer:
    def test_analyze_returns_dict(self):
        site = make_simple_site()
        analyzer = WebSiteAnalyzer(site=site)
        result = analyzer.analyze()
        assert "coordinate_count" in result
        assert "morphism_count" in result
        assert "violation_count" in result

    def test_analyze_counts_correct(self):
        site = make_simple_site()
        analyzer = WebSiteAnalyzer(site=site)
        result = analyzer.analyze()
        assert result["coordinate_count"] == len(site.coordinates)
        assert result["morphism_count"] == len(site.morphisms)

    def test_fluent_api(self):
        analyzer = WebSiteAnalyzer()
        c = WebCoordinate(id="c1", kind=WebCoordinateKind.ROUTE_HANDLER, name="index")
        result = analyzer.add_coordinate(c)
        assert result is analyzer


class TestGenerateReport:
    def test_returns_dict(self):
        site = make_simple_site()
        report = generate_report(site)
        assert isinstance(report, dict)

    def test_has_language_layers(self):
        site = make_simple_site()
        report = generate_report(site)
        assert "language_layers" in report

    def test_has_site_name(self):
        site = make_simple_site()
        report = generate_report(site)
        assert report["site_name"] == "test_app"

    def test_layer_counts_correct(self):
        site = make_simple_site()
        report = generate_report(site)
        layers = report["language_layers"]
        assert layers["python"]["coordinate_count"] == 2
        assert layers["javascript"]["coordinate_count"] == 1

    def test_has_cross_language_morphisms(self):
        site = make_simple_site()
        report = generate_report(site)
        assert "cross_language_morphism_count" in report

    def test_has_boundary_graph(self):
        site = make_simple_site()
        report = generate_report(site)
        assert "language_boundary_graph" in report
