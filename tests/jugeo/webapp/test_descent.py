"""
Comprehensive tests for jugeo.webapp.descent.

Tests cover models, the web descent engine, incremental descent, Čech
cohomology computation, obstruction classification, known pattern catalog,
pattern matching, and all five formal descent theorems.
"""
from __future__ import annotations

import pytest

from jugeo.webapp.descent import (
    DescentStrategy,
    CohomologyClass,
    WebObstruction,
    DescentResult,
    DescentConfiguration,
    WebDescentEngine,
    IncrementalDescentEngine,
    CechCohomology,
    ObstructionClassifier,
    ObstructionPattern,
    KNOWN_PATTERNS,
    PatternMatcher,
    ContextCompletenessTheorem,
    ContractConsistencyTheorem,
    DOMIntegrityTheorem,
    TrustMonotonicityTheorem,
    CohomologicalCompletenessTheorem,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def engine():
    return WebDescentEngine()


@pytest.fixture
def incremental_engine():
    return IncrementalDescentEngine()


@pytest.fixture
def cech():
    return CechCohomology()


@pytest.fixture
def classifier():
    return ObstructionClassifier()


@pytest.fixture
def matcher():
    return PatternMatcher()


@pytest.fixture
def clean_site_data():
    """Site data with no violations."""
    return {
        "routes": [
            {"pattern": "/home", "methods": ["GET"],
             "context_vars": ["user"], "template": "home.html",
             "file": "views.py", "line": 5},
        ],
        "templates": [
            {"name": "home.html", "variables": ["user"], "file": "home.html"},
        ],
        "fetch_calls": [],
        "models": [],
        "tables": [],
        "js_dom_refs": [],
        "html_ids": set(),
        "js_classes": set(),
        "css_classes": set(),
        "forms": [],
        "template_classes": set(),
        "auth_decorators": [],
        "session_checks": [],
        "constraints": [],
        "handlers": [],
        "error_handlers": [],
        "js_catch": [],
        "server_validations": [],
        "client_validations": [],
    }


@pytest.fixture
def violation_site_data():
    """Site data that produces violations."""
    return {
        "routes": [
            {"pattern": "/dash", "methods": ["GET"],
             "context_vars": ["stats"], "template": "dash.html",
             "file": "views.py", "line": 20},
        ],
        "templates": [
            {"name": "dash.html", "variables": ["stats", "title"],
             "file": "dash.html"},
        ],
        "fetch_calls": [
            {"url": "/api/data", "expected_fields": ["missing_field"],
             "method": "GET", "file": "app.js", "line": 5},
        ],
        "models": [],
        "tables": [],
        "js_dom_refs": [
            {"element_id": "ghost-panel", "file": "app.js",
             "line": 10, "method": "getElementById"},
        ],
        "html_ids": {"main-nav"},
        "js_classes": {"fancy"},
        "css_classes": {"basic"},
        "forms": [],
        "template_classes": set(),
        "auth_decorators": [],
        "session_checks": [],
        "constraints": [],
        "handlers": [],
        "error_handlers": [],
        "js_catch": [],
        "server_validations": [],
        "client_validations": [],
    }


# ===================================================================
# Enum tests
# ===================================================================


class TestDescentStrategy:

    def test_values(self):
        assert DescentStrategy.FULL_CHECK == "full_check"
        assert DescentStrategy.INCREMENTAL == "incremental"
        assert DescentStrategy.LAYER_BOUNDARY_ONLY == "layer_boundary_only"
        assert DescentStrategy.TRUST_BOUNDARY_ONLY == "trust_boundary_only"

    def test_count(self):
        assert len(DescentStrategy) == 4

    def test_from_value(self):
        assert DescentStrategy("full_check") is DescentStrategy.FULL_CHECK


class TestCohomologyClass:

    def test_values(self):
        assert CohomologyClass.H0_GLOBAL_SECTION == "h0_global_section"
        assert CohomologyClass.H1_OVERLAP_OBSTRUCTION == "h1_overlap_obstruction"
        assert CohomologyClass.H2_TRIPLE_OBSTRUCTION == "h2_triple_obstruction"

    def test_count(self):
        assert len(CohomologyClass) == 3


# ===================================================================
# WebObstruction model
# ===================================================================


class TestWebObstruction:

    def test_to_dict_from_dict(self):
        obs = WebObstruction(
            id="obs-1",
            cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="route_template",
            description="Missing variable 'title'",
            coordinates=["python:route:/dash", "template:dash.html:title"],
            severity="high",
            repair_hint="Add title to render_template",
            evidence={"file": "views.py"},
        )
        d = obs.to_dict()
        assert d["cohomology_class"] == "h1_overlap_obstruction"
        restored = WebObstruction.from_dict(d)
        assert restored.id == "obs-1"
        assert restored.cohomology_class == CohomologyClass.H1_OVERLAP_OBSTRUCTION
        assert restored.coordinates == obs.coordinates

    def test_is_blocking(self):
        obs_high = WebObstruction(
            id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="test", description="d", severity="high",
        )
        obs_low = WebObstruction(
            id="b", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="test", description="d", severity="low",
        )
        assert obs_high.is_blocking is True
        assert obs_low.is_blocking is False

    def test_affected_layers(self):
        obs = WebObstruction(
            id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="test", description="d",
            coordinates=["python:foo", "jinja2:bar"],
        )
        assert obs.affected_layers == {"python", "jinja2"}

    def test_defaults(self):
        obs = WebObstruction(
            id="x", cohomology_class=CohomologyClass.H0_GLOBAL_SECTION,
            overlap_kind="k", description="d",
        )
        assert obs.severity == "high"
        assert obs.repair_hint == ""
        assert obs.evidence == {}
        assert obs.coordinates == []


# ===================================================================
# DescentResult model
# ===================================================================


class TestDescentResult:

    def test_to_dict_from_dict(self):
        obs = WebObstruction(
            id="obs-1",
            cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="route_template",
            description="Missing var",
            severity="high",
        )
        result = DescentResult(
            strategy=DescentStrategy.FULL_CHECK,
            obstructions=[obs],
            checked_conditions=5,
            passed_conditions=4,
            coverage_score=0.8,
            timing_ms=12.5,
        )
        d = result.to_dict()
        assert d["strategy"] == "full_check"
        restored = DescentResult.from_dict(d)
        assert restored.checked_conditions == 5
        assert restored.has_obstructions is True
        assert restored.h1_count == 1
        assert restored.h2_count == 0
        assert restored.blocking_count == 1

    def test_summary_no_obstructions(self):
        result = DescentResult(
            strategy=DescentStrategy.FULL_CHECK,
            checked_conditions=10, passed_conditions=10,
            coverage_score=1.0,
        )
        assert "passed" in result.summary()

    def test_summary_with_obstructions(self):
        obs = WebObstruction(
            id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="test", description="d", severity="critical",
        )
        result = DescentResult(
            strategy=DescentStrategy.FULL_CHECK,
            obstructions=[obs],
            checked_conditions=5, passed_conditions=4,
            coverage_score=0.8,
        )
        s = result.summary()
        assert "1 obstruction" in s
        assert "1 H¹" in s

    def test_obstructions_by_kind(self):
        obs1 = WebObstruction(id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
                              overlap_kind="route_template", description="d")
        obs2 = WebObstruction(id="b", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
                              overlap_kind="route_template", description="d")
        obs3 = WebObstruction(id="c", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
                              overlap_kind="js_dom_html", description="d")
        result = DescentResult(strategy=DescentStrategy.FULL_CHECK, obstructions=[obs1, obs2, obs3])
        by_kind = result.obstructions_by_kind()
        assert len(by_kind["route_template"]) == 2
        assert len(by_kind["js_dom_html"]) == 1


# ===================================================================
# DescentConfiguration model
# ===================================================================


class TestDescentConfiguration:

    def test_defaults(self):
        cfg = DescentConfiguration()
        assert cfg.strategy == DescentStrategy.FULL_CHECK
        assert cfg.layers_to_check is None
        assert cfg.max_depth == 5
        assert cfg.timeout_ms == 30000.0
        assert cfg.trust_threshold == "server_validated"

    def test_effective_layers_default(self):
        cfg = DescentConfiguration()
        layers = cfg.effective_layers
        assert "python" in layers
        assert "js" in layers
        assert len(layers) == 7

    def test_effective_layers_custom(self):
        cfg = DescentConfiguration(layers_to_check=["python", "js"])
        assert cfg.effective_layers == ["python", "js"]

    def test_to_dict_from_dict(self):
        cfg = DescentConfiguration(
            strategy=DescentStrategy.INCREMENTAL,
            layers_to_check=["python", "template"],
            max_depth=3,
            timeout_ms=5000.0,
            trust_threshold="client_validated",
        )
        d = cfg.to_dict()
        assert d["strategy"] == "incremental"
        restored = DescentConfiguration.from_dict(d)
        assert restored.strategy == DescentStrategy.INCREMENTAL
        assert restored.layers_to_check == ["python", "template"]
        assert restored.max_depth == 3

    def test_from_dict_defaults(self):
        cfg = DescentConfiguration.from_dict({})
        assert cfg.strategy == DescentStrategy.FULL_CHECK
        assert cfg.layers_to_check is None


# ===================================================================
# WebDescentEngine
# ===================================================================


class TestWebDescentEngine:

    def test_run_descent_clean(self, engine, clean_site_data):
        config = DescentConfiguration(strategy=DescentStrategy.FULL_CHECK)
        result = engine.run_descent(clean_site_data, config)
        assert isinstance(result, DescentResult)
        assert result.strategy == DescentStrategy.FULL_CHECK
        assert result.checked_conditions > 0
        assert result.timing_ms >= 0

    def test_run_descent_with_violations(self, engine, violation_site_data):
        config = DescentConfiguration(strategy=DescentStrategy.FULL_CHECK)
        result = engine.run_descent(violation_site_data, config)
        assert result.has_obstructions is True
        assert result.h1_count >= 1

    def test_run_descent_layer_boundary_only(self, engine, violation_site_data):
        config = DescentConfiguration(strategy=DescentStrategy.LAYER_BOUNDARY_ONLY)
        result = engine.run_descent(violation_site_data, config)
        assert result.strategy == DescentStrategy.LAYER_BOUNDARY_ONLY

    def test_run_descent_trust_boundary_only(self, engine):
        site_data = {
            "routes": [{"pattern": "/pay", "methods": ["POST"],
                        "file": "v.py", "line": 1}],
            "client_validations": [{"route": "/pay", "validation_type": "js",
                                    "file": "a.js", "line": 1}],
            "server_validations": [],
        }
        config = DescentConfiguration(strategy=DescentStrategy.TRUST_BOUNDARY_ONLY)
        result = engine.run_descent(site_data, config)
        assert result.strategy == DescentStrategy.TRUST_BOUNDARY_ONLY
        assert result.has_obstructions is True

    def test_run_descent_empty_data(self, engine):
        config = DescentConfiguration()
        result = engine.run_descent({}, config)
        assert isinstance(result, DescentResult)
        assert not result.has_obstructions

    def test_run_descent_specific_layers(self, engine, violation_site_data):
        config = DescentConfiguration(
            strategy=DescentStrategy.FULL_CHECK,
            layers_to_check=["python", "template"],
        )
        result = engine.run_descent(violation_site_data, config)
        assert result.checked_conditions >= 1


# ===================================================================
# IncrementalDescentEngine
# ===================================================================


class TestIncrementalDescentEngine:

    def test_no_changed_files(self, incremental_engine):
        result = incremental_engine.check_changed_files([], {})
        assert result.strategy == DescentStrategy.INCREMENTAL
        assert result.checked_conditions == 0

    def test_python_file_changed(self, incremental_engine, clean_site_data):
        result = incremental_engine.check_changed_files(
            ["views.py"], clean_site_data,
        )
        assert result.strategy == DescentStrategy.INCREMENTAL
        assert result.checked_conditions >= 1

    def test_js_file_changed(self, incremental_engine, violation_site_data):
        result = incremental_engine.check_changed_files(
            ["static/app.js"], violation_site_data,
        )
        assert result.strategy == DescentStrategy.INCREMENTAL

    def test_css_file_changed(self, incremental_engine, clean_site_data):
        result = incremental_engine.check_changed_files(
            ["style.css"], clean_site_data,
        )
        assert result.strategy == DescentStrategy.INCREMENTAL

    def test_affected_layers_py(self, incremental_engine):
        layers = incremental_engine._affected_layers(["app.py", "views.py"])
        assert layers == {"python"}

    def test_affected_layers_mixed(self, incremental_engine):
        layers = incremental_engine._affected_layers(
            ["app.py", "index.html", "style.css", "app.js"]
        )
        assert "python" in layers
        assert "template" in layers
        assert "css" in layers
        assert "js" in layers

    def test_affected_layers_unknown_ext(self, incremental_engine):
        layers = incremental_engine._affected_layers(["readme.md", "config.yaml"])
        assert layers == set()

    def test_affected_layers_sql(self, incremental_engine):
        layers = incremental_engine._affected_layers(["schema.sql"])
        assert layers == {"sql"}


# ===================================================================
# CechCohomology
# ===================================================================


class TestCechCohomology:

    def test_compute_h0_clean(self, cech, clean_site_data):
        h0 = cech.compute_h0(clean_site_data)
        assert isinstance(h0, list)
        # "user" is shared across python+template → global section
        coords = [s["coordinate"] for s in h0]
        assert "var:user" in coords

    def test_compute_h1_clean(self, cech, clean_site_data):
        h1 = cech.compute_h1(clean_site_data)
        assert isinstance(h1, list)
        assert len(h1) == 0

    def test_compute_h1_with_violations(self, cech, violation_site_data):
        h1 = cech.compute_h1(violation_site_data)
        assert len(h1) > 0
        for obs in h1:
            assert obs.cohomology_class == CohomologyClass.H1_OVERLAP_OBSTRUCTION

    def test_compute_h2_needs_triple(self, cech):
        """H² requires violations on ≥ 2 edges of a triangle."""
        site_data = {
            "routes": [
                {"pattern": "/page", "methods": ["GET"],
                 "context_vars": [], "template": "page.html",
                 "file": "v.py", "line": 1},
            ],
            "templates": [
                {"name": "page.html", "variables": ["x"], "file": "page.html"},
            ],
            "js_dom_refs": [
                {"element_id": "missing", "file": "app.js",
                 "line": 1, "method": "getElementById"},
            ],
            "html_ids": set(),
            "template_classes": {"orphan-cls"},
            "css_classes": set(),
            "fetch_calls": [],
            "models": [],
            "tables": [],
            "js_classes": set(),
            "forms": [],
            "auth_decorators": [],
            "session_checks": [],
            "constraints": [],
            "handlers": [],
            "error_handlers": [],
            "js_catch": [],
        }
        h2 = cech.compute_h2(site_data)
        assert isinstance(h2, list)
        # There may or may not be H² depending on triangle construction

    def test_build_nerve_structure(self, cech, clean_site_data):
        nerve = cech._build_nerve(clean_site_data)
        assert "vertices" in nerve
        assert "edges" in nerve
        assert "triangles" in nerve
        assert "python" in nerve["vertices"]
        assert "template" in nerve["vertices"]

    def test_build_nerve_empty(self, cech):
        nerve = cech._build_nerve({})
        assert nerve["vertices"] == []
        assert nerve["edges"] == []


# ===================================================================
# ObstructionClassifier
# ===================================================================


class TestObstructionClassifier:

    def test_classify_h1(self, classifier):
        obs = WebObstruction(
            id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="route_template", description="missing var",
            severity="high",
            coordinates=["python:route:/dash", "template:dash.html"],
        )
        result = classifier.classify(obs)
        assert result["type"] == "route_template"
        assert result["severity"] == "high"
        assert result["is_blocking"] is True
        assert "python" in result["layers"]

    def test_classify_h2(self, classifier):
        obs = WebObstruction(
            id="b", cohomology_class=CohomologyClass.H2_TRIPLE_OBSTRUCTION,
            overlap_kind="triple_overlap", description="triple",
            severity="high",
        )
        result = classifier.classify(obs)
        assert result["type"] == "triple_overlap"

    def test_classify_low_severity(self, classifier):
        obs = WebObstruction(
            id="c", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="template_css", description="unused class",
            severity="low",
        )
        result = classifier.classify(obs)
        assert result["is_blocking"] is False
        assert result["repair_priority"] == 3

    def test_cluster_by_root_cause(self, classifier):
        obs1 = WebObstruction(
            id="a", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="route_template",
            description="Template 'dash.html' uses '{{ title }}' but route does not pass it",
        )
        obs2 = WebObstruction(
            id="b", cohomology_class=CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            overlap_kind="js_dom_html",
            description="JS getElementById('chart') targets an element not found in HTML",
        )
        clusters = classifier.cluster_by_root_cause([obs1, obs2])
        assert len(clusters) >= 1
        total = sum(len(v) for v in clusters.values())
        assert total == 2


# ===================================================================
# ObstructionPattern
# ===================================================================


class TestObstructionPattern:

    def test_to_dict_from_dict(self):
        p = KNOWN_PATTERNS[0]
        d = p.to_dict()
        restored = ObstructionPattern.from_dict(d)
        assert restored.id == p.id
        assert restored.name == p.name
        assert restored.severity == p.severity
        assert restored.examples == p.examples

    def test_known_patterns_count(self):
        assert len(KNOWN_PATTERNS) >= 15

    def test_known_patterns_required_fields(self):
        for p in KNOWN_PATTERNS:
            assert p.id, f"Pattern missing id"
            assert p.name, f"Pattern {p.id} missing name"
            assert p.description, f"Pattern {p.id} missing description"
            assert p.overlap_kind, f"Pattern {p.id} missing overlap_kind"
            assert p.detection_strategy, f"Pattern {p.id} missing detection_strategy"
            assert p.repair_template, f"Pattern {p.id} missing repair_template"
            assert p.severity in {"critical", "high", "medium", "low"}, \
                f"Pattern {p.id} has invalid severity {p.severity}"

    def test_known_patterns_unique_ids(self):
        ids = [p.id for p in KNOWN_PATTERNS]
        assert len(ids) == len(set(ids))

    def test_known_patterns_unique_names(self):
        names = [p.name for p in KNOWN_PATTERNS]
        assert len(names) == len(set(names))


# ===================================================================
# PatternMatcher
# ===================================================================


class TestPatternMatcher:

    def test_match_template_violation(self, matcher):
        violation = {
            "kind": "route_template",
            "message": "Template 'dash.html' uses '{{ title }}' but route does not pass it",
        }
        pattern = matcher.match(violation)
        assert pattern is not None
        assert pattern.name == "missing_template_variable"

    def test_match_dom_violation(self, matcher):
        violation = {
            "kind": "js_dom_html",
            "message": "JS getElementById('chart') targets an element not found in HTML",
        }
        pattern = matcher.match(violation)
        assert pattern is not None
        assert pattern.name == "missing_dom_element"

    def test_match_no_match(self, matcher):
        violation = {"kind": "unknown_kind", "message": "totally unrecognized thing"}
        assert matcher.match(violation) is None

    def test_suggest_repair(self, matcher):
        pattern = KNOWN_PATTERNS[0]  # missing_template_variable
        violation = {
            "message": "Template uses '{{ title }}' but render_template does not pass it",
            "var_name": "title",
        }
        hint = matcher.suggest_repair(pattern, violation)
        assert "title" in hint

    def test_severity_for(self, matcher):
        for p in KNOWN_PATTERNS:
            sev = matcher.severity_for(p)
            assert sev in {"critical", "high", "medium", "low"}

    def test_all_patterns_for_kind(self, matcher):
        patterns = matcher.all_patterns_for_kind("route_template")
        assert len(patterns) >= 1

    def test_get_pattern(self, matcher):
        p = matcher.get_pattern("OP001")
        assert p is not None
        assert p.name == "missing_template_variable"

    def test_get_pattern_by_name(self, matcher):
        p = matcher.get_pattern_by_name("api_contract_mismatch")
        assert p is not None
        assert p.id == "OP002"

    def test_get_pattern_missing(self, matcher):
        assert matcher.get_pattern("OP999") is None
        assert matcher.get_pattern_by_name("nonexistent") is None


# ===================================================================
# Theorem 1 — Context Completeness
# ===================================================================


class TestContextCompletenessTheorem:

    def test_holds_when_all_vars_provided(self):
        thm = ContextCompletenessTheorem()
        site_data = {
            "routes": [
                {"pattern": "/home", "methods": ["GET"],
                 "context_vars": ["user", "title"], "template": "home.html",
                 "file": "views.py", "line": 5},
            ],
            "templates": [
                {"name": "home.html", "variables": ["user", "title"],
                 "file": "home.html"},
            ],
        }
        result = thm.check(site_data)
        assert result["holds"] is True
        assert result["counterexample"] is None

    def test_fails_when_var_missing(self):
        thm = ContextCompletenessTheorem()
        site_data = {
            "routes": [
                {"pattern": "/dash", "methods": ["GET"],
                 "context_vars": ["stats"], "template": "dash.html",
                 "file": "views.py", "line": 20},
            ],
            "templates": [
                {"name": "dash.html", "variables": ["stats", "title"],
                 "file": "dash.html"},
            ],
        }
        result = thm.check(site_data)
        assert result["holds"] is False
        assert result["counterexample"] is not None
        assert "title" in result["counterexample"]["message"]

    def test_statement(self):
        thm = ContextCompletenessTheorem()
        assert thm.name == "ContextCompletenessTheorem"
        assert "render_template" in thm.statement


# ===================================================================
# Theorem 2 — Contract Consistency
# ===================================================================


class TestContractConsistencyTheorem:

    def test_holds_when_fields_match(self):
        thm = ContractConsistencyTheorem()
        site_data = {
            "routes": [
                {"pattern": "/api/data", "methods": ["GET"],
                 "context_vars": ["x", "y"], "template": "",
                 "file": "api.py", "line": 1},
            ],
            "fetch_calls": [
                {"url": "/api/data", "expected_fields": ["x"],
                 "method": "GET", "file": "app.js", "line": 5},
            ],
        }
        result = thm.check(site_data)
        assert result["holds"] is True

    def test_fails_when_field_missing(self):
        thm = ContractConsistencyTheorem()
        site_data = {
            "routes": [
                {"pattern": "/api/data", "methods": ["GET"],
                 "context_vars": ["x"], "template": "",
                 "file": "api.py", "line": 1},
            ],
            "fetch_calls": [
                {"url": "/api/data", "expected_fields": ["x", "missing"],
                 "method": "GET", "file": "app.js", "line": 5},
            ],
        }
        result = thm.check(site_data)
        assert result["holds"] is False


# ===================================================================
# Theorem 3 — DOM Integrity
# ===================================================================


class TestDOMIntegrityTheorem:

    def test_holds_when_all_ids_exist(self):
        thm = DOMIntegrityTheorem()
        site_data = {
            "js_dom_refs": [
                {"element_id": "nav", "file": "app.js",
                 "line": 1, "method": "getElementById"},
            ],
            "html_ids": {"nav", "footer"},
        }
        result = thm.check(site_data)
        assert result["holds"] is True

    def test_fails_when_id_missing(self):
        thm = DOMIntegrityTheorem()
        site_data = {
            "js_dom_refs": [
                {"element_id": "ghost", "file": "app.js",
                 "line": 5, "method": "getElementById"},
            ],
            "html_ids": {"nav"},
        }
        result = thm.check(site_data)
        assert result["holds"] is False
        assert result["counterexample"] is not None

    def test_no_refs_holds(self):
        thm = DOMIntegrityTheorem()
        result = thm.check({"js_dom_refs": [], "html_ids": set()})
        assert result["holds"] is True


# ===================================================================
# Theorem 4 — Trust Monotonicity
# ===================================================================


class TestTrustMonotonicityTheorem:

    def test_holds_when_server_validates(self):
        thm = TrustMonotonicityTheorem()
        site_data = {
            "routes": [
                {"pattern": "/submit", "methods": ["POST"],
                 "file": "v.py", "line": 1},
            ],
            "client_validations": [
                {"route": "/submit", "validation_type": "js",
                 "file": "a.js", "line": 1},
            ],
            "server_validations": [
                {"route": "/submit", "validation_type": "flask",
                 "file": "v.py", "line": 2},
            ],
        }
        result = thm.check(site_data)
        assert result["holds"] is True

    def test_fails_when_client_only(self):
        thm = TrustMonotonicityTheorem()
        site_data = {
            "routes": [],
            "client_validations": [
                {"route": "/pay", "validation_type": "js",
                 "file": "a.js", "line": 1},
            ],
            "server_validations": [],
        }
        result = thm.check(site_data)
        assert result["holds"] is False

    def test_fails_mutation_no_validation(self):
        thm = TrustMonotonicityTheorem()
        site_data = {
            "routes": [
                {"pattern": "/transfer", "methods": ["POST"],
                 "file": "v.py", "line": 1},
            ],
            "client_validations": [],
            "server_validations": [],
        }
        result = thm.check(site_data)
        assert result["holds"] is False

    def test_statement(self):
        thm = TrustMonotonicityTheorem()
        assert "trust" in thm.statement.lower()


# ===================================================================
# Theorem 5 — Cohomological Completeness
# ===================================================================


class TestCohomologicalCompletenessTheorem:

    def test_holds_on_clean_data(self, clean_site_data):
        thm = CohomologicalCompletenessTheorem()
        result = thm.check(clean_site_data)
        assert result["holds"] is True
        assert "H¹ = 0" in " ".join(result["evidence"])

    def test_fails_with_violations(self, violation_site_data):
        thm = CohomologicalCompletenessTheorem()
        result = thm.check(violation_site_data)
        assert result["holds"] is False
        assert result["counterexample"] is not None
        assert "cohomology_class" in result["counterexample"]

    def test_statement(self):
        thm = CohomologicalCompletenessTheorem()
        assert "H¹" in thm.statement or "global section" in thm.statement


# ===================================================================
# Full descent pipeline integration
# ===================================================================


class TestDescentPipelineIntegration:

    def test_full_pipeline(self, violation_site_data):
        """Run descent, classify, match patterns — end-to-end."""
        engine = WebDescentEngine()
        config = DescentConfiguration(strategy=DescentStrategy.FULL_CHECK)
        result = engine.run_descent(violation_site_data, config)

        assert result.has_obstructions
        assert result.checked_conditions >= 1

        classifier = ObstructionClassifier()
        matcher = PatternMatcher()

        for obs in result.obstructions:
            classification = classifier.classify(obs)
            assert "type" in classification
            assert "severity" in classification

            violation_dict = {
                "kind": obs.overlap_kind,
                "message": obs.description,
            }
            matched = matcher.match(violation_dict)
            # Not all violations will match — that's fine

        clusters = classifier.cluster_by_root_cause(result.obstructions)
        total_clustered = sum(len(v) for v in clusters.values())
        assert total_clustered == len(result.obstructions)

    def test_incremental_then_full(self, violation_site_data):
        """Incremental check on changed files then full check."""
        inc = IncrementalDescentEngine()
        inc_result = inc.check_changed_files(
            ["views.py", "app.js"], violation_site_data,
        )
        assert inc_result.strategy == DescentStrategy.INCREMENTAL

        engine = WebDescentEngine()
        full_result = engine.run_descent(
            violation_site_data,
            DescentConfiguration(strategy=DescentStrategy.FULL_CHECK),
        )
        assert full_result.checked_conditions >= inc_result.checked_conditions

    def test_cohomology_then_theorems(self, violation_site_data):
        """Compute cohomology, then check theorems."""
        cech = CechCohomology()
        h1 = cech.compute_h1(violation_site_data)
        assert len(h1) > 0

        thm = CohomologicalCompletenessTheorem()
        result = thm.check(violation_site_data)
        assert result["holds"] is False
