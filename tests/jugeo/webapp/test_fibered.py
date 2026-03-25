"""Tests for the webapp fibered category module."""
from __future__ import annotations

import pytest

from jugeo.webapp.fibered.models import (
    LanguageFiber,
    FiberedCoordinate,
    CartesianLift,
    FiberDescentResult,
    FiberedSiteResult,
)
from jugeo.webapp.fibered.fibered_category import WebFiberedCategory
from jugeo.webapp.fibered.fiber_descent import FiberDescentEngine
from jugeo.webapp.fibered.language_fibers import (
    PythonFiber,
    JavaScriptFiber,
    CSSFiber,
    HTMLFiber,
    SQLFiber,
    TemplateFiber,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def category() -> WebFiberedCategory:
    return WebFiberedCategory()


@pytest.fixture
def descent_engine() -> FiberDescentEngine:
    return FiberDescentEngine()


@pytest.fixture
def sample_fiber() -> LanguageFiber:
    return LanguageFiber(
        name="test_lang",
        coordinate_kinds=["KIND_A", "KIND_B"],
        morphism_kinds=["MORPH_A"],
        internal_topology={"type": "test"},
        description="A test fiber",
    )


@pytest.fixture
def sample_coordinate() -> FiberedCoordinate:
    return FiberedCoordinate(
        coordinate_id="py_route_1",
        fiber_name="python",
        local_id="route_1",
        kind="ROUTE_HANDLER",
        metadata={"path": "/index"},
    )


@pytest.fixture
def sample_lift() -> CartesianLift:
    return CartesianLift(
        morphism_id="python_to_template_1",
        source_fiber="python",
        target_fiber="template",
        lift_type="CONTEXT_PROVISION",
        source_coord="py_route_1",
        target_coord="tpl_base",
        is_cartesian=True,
        metadata={},
    )


# ===================================================================
# LanguageFiber tests
# ===================================================================

class TestLanguageFiber:

    def test_language_fiber_creation(self, sample_fiber):
        assert sample_fiber.name == "test_lang"
        assert sample_fiber.coordinate_kinds == ["KIND_A", "KIND_B"]

    def test_language_fiber_to_dict(self, sample_fiber):
        d = sample_fiber.to_dict()
        assert d["name"] == "test_lang"
        assert d["coordinate_kinds"] == ["KIND_A", "KIND_B"]
        assert d["morphism_kinds"] == ["MORPH_A"]
        assert d["description"] == "A test fiber"

    def test_language_fiber_from_dict_roundtrip(self, sample_fiber):
        d = sample_fiber.to_dict()
        restored = LanguageFiber.from_dict(d)
        assert restored.name == sample_fiber.name
        assert restored.coordinate_kinds == sample_fiber.coordinate_kinds
        assert restored.morphism_kinds == sample_fiber.morphism_kinds
        assert restored.internal_topology == sample_fiber.internal_topology
        assert restored.description == sample_fiber.description

    def test_language_fiber_coordinate_kinds_is_list(self, sample_fiber):
        assert isinstance(sample_fiber.coordinate_kinds, list)

    def test_language_fiber_defaults(self):
        fib = LanguageFiber(name="minimal")
        assert fib.coordinate_kinds == []
        assert fib.morphism_kinds == []
        assert fib.internal_topology == {}
        assert fib.description == ""


# ===================================================================
# FiberedCoordinate tests
# ===================================================================

class TestFiberedCoordinate:

    def test_fibered_coordinate_creation(self, sample_coordinate):
        assert sample_coordinate.coordinate_id == "py_route_1"
        assert sample_coordinate.fiber_name == "python"
        assert sample_coordinate.local_id == "route_1"
        assert sample_coordinate.kind == "ROUTE_HANDLER"

    def test_fibered_coordinate_to_dict(self, sample_coordinate):
        d = sample_coordinate.to_dict()
        assert d["coordinate_id"] == "py_route_1"
        assert d["fiber_name"] == "python"
        assert "metadata" in d

    def test_fibered_coordinate_from_dict_roundtrip(self, sample_coordinate):
        d = sample_coordinate.to_dict()
        restored = FiberedCoordinate.from_dict(d)
        assert restored.coordinate_id == sample_coordinate.coordinate_id
        assert restored.fiber_name == sample_coordinate.fiber_name
        assert restored.local_id == sample_coordinate.local_id
        assert restored.kind == sample_coordinate.kind
        assert restored.metadata == sample_coordinate.metadata


# ===================================================================
# CartesianLift tests
# ===================================================================

class TestCartesianLift:

    def test_cartesian_lift_creation(self, sample_lift):
        assert sample_lift.morphism_id == "python_to_template_1"
        assert sample_lift.source_fiber == "python"
        assert sample_lift.target_fiber == "template"

    def test_cartesian_lift_to_dict(self, sample_lift):
        d = sample_lift.to_dict()
        assert d["morphism_id"] == "python_to_template_1"
        assert d["lift_type"] == "CONTEXT_PROVISION"
        assert d["is_cartesian"] is True

    def test_cartesian_lift_from_dict_roundtrip(self, sample_lift):
        d = sample_lift.to_dict()
        restored = CartesianLift.from_dict(d)
        assert restored.morphism_id == sample_lift.morphism_id
        assert restored.source_fiber == sample_lift.source_fiber
        assert restored.target_fiber == sample_lift.target_fiber
        assert restored.source_coord == sample_lift.source_coord
        assert restored.target_coord == sample_lift.target_coord
        assert restored.is_cartesian == sample_lift.is_cartesian

    def test_cartesian_lift_is_cartesian_default_true(self):
        lift = CartesianLift(
            morphism_id="m1",
            source_fiber="python",
            target_fiber="sql",
            lift_type="ORM",
            source_coord="py_model",
            target_coord="sql_table",
        )
        assert lift.is_cartesian is True


# ===================================================================
# FiberDescentResult tests
# ===================================================================

class TestFiberDescentResult:

    def test_fiber_descent_result_creation(self):
        result = FiberDescentResult(
            fiber_name="python",
            local_obstructions=[{"id": "obs1", "description": "test"}],
            passed=False,
            coverage_score=0.8,
        )
        assert result.fiber_name == "python"
        assert len(result.local_obstructions) == 1
        assert result.passed is False

    def test_fiber_descent_result_to_dict(self):
        result = FiberDescentResult(fiber_name="css", passed=True)
        d = result.to_dict()
        assert d["fiber_name"] == "css"
        assert d["passed"] is True
        assert "local_obstructions" in d
        assert "boundary_obstructions" in d
        assert "coverage_score" in d

    def test_fiber_descent_result_from_dict_roundtrip(self):
        result = FiberDescentResult(
            fiber_name="javascript",
            local_obstructions=[{"id": "o1", "description": "test", "severity": "error"}],
            boundary_obstructions=[{"id": "b1", "description": "boundary"}],
            passed=False,
            coverage_score=0.5,
        )
        d = result.to_dict()
        restored = FiberDescentResult.from_dict(d)
        assert restored.fiber_name == result.fiber_name
        assert len(restored.local_obstructions) == 1
        assert len(restored.boundary_obstructions) == 1
        assert restored.passed == result.passed
        assert restored.coverage_score == result.coverage_score

    def test_fiber_descent_result_passed_default_true(self):
        result = FiberDescentResult(fiber_name="sql")
        assert result.passed is True
        assert result.coverage_score == 1.0


# ===================================================================
# FiberedSiteResult tests
# ===================================================================

class TestFiberedSiteResult:

    def test_fibered_site_result_creation(self):
        result = FiberedSiteResult(
            fibers={"python": {"name": "python"}},
            overall_passed=True,
            total_obstructions=0,
        )
        assert result.overall_passed is True
        assert result.total_obstructions == 0

    def test_fibered_site_result_to_dict(self):
        result = FiberedSiteResult(overall_passed=False, total_obstructions=3)
        d = result.to_dict()
        assert d["overall_passed"] is False
        assert d["total_obstructions"] == 3
        assert "fibers" in d
        assert "lifts" in d
        assert "global_descent" in d
        assert "per_fiber_descent" in d

    def test_fibered_site_result_from_dict_roundtrip(self):
        result = FiberedSiteResult(
            fibers={"python": {"name": "python"}},
            lifts=[{"morphism_id": "m1"}],
            global_descent=[{"obstruction": "obs1"}],
            per_fiber_descent={"python": {"fiber_name": "python", "passed": True}},
            overall_passed=True,
            total_obstructions=0,
        )
        d = result.to_dict()
        restored = FiberedSiteResult.from_dict(d)
        assert restored.overall_passed == result.overall_passed
        assert restored.total_obstructions == result.total_obstructions
        assert "python" in restored.fibers


# ===================================================================
# WebFiberedCategory tests
# ===================================================================

class TestWebFiberedCategory:

    def test_language_fibers_dict_has_all_languages(self, category):
        expected = ["python", "javascript", "html", "css", "sql", "template"]
        for lang in expected:
            assert lang in WebFiberedCategory.LANGUAGE_FIBERS

    def test_fiber_returns_language_fiber(self, category):
        fib = category.fiber("python")
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "python"

    def test_fiber_python_has_route_handler(self, category):
        fib = category.fiber("python")
        assert "ROUTE_HANDLER" in fib.coordinate_kinds

    def test_fiber_raises_on_unknown(self, category):
        with pytest.raises(KeyError):
            category.fiber("nonexistent_language")

    def test_base_category_has_objects(self, category):
        bc = category.base_category()
        assert "objects" in bc
        assert isinstance(bc["objects"], list)
        assert len(bc["objects"]) == 6

    def test_base_category_has_morphisms(self, category):
        bc = category.base_category()
        assert "morphisms" in bc
        assert isinstance(bc["morphisms"], list)
        assert len(bc["morphisms"]) >= 1

    def test_total_category_structure(self, category):
        tc = category.total_category({})
        assert "base" in tc
        assert "fibers" in tc
        assert "coordinates" in tc
        assert "morphisms" in tc

    def test_projection_from_coord_id(self, category):
        assert category.projection("py_route_1") == "python"
        assert category.projection("js_handler") == "javascript"
        assert category.projection("css_rule") == "css"
        assert category.projection("html_element") == "html"
        assert category.projection("sql_table") == "sql"
        assert category.projection("tpl_block") == "template"

    def test_projection_from_site_data(self, category):
        site_data = {
            "coordinates": [
                {"coordinate_id": "custom_1", "fiber_name": "python"},
            ],
        }
        assert category.projection("custom_1", site_data) == "python"

    def test_projection_raises_on_unknown(self, category):
        with pytest.raises(ValueError):
            category.projection("unknown_prefix_xyz")

    def test_fiber_over_filters_correctly(self, category):
        site_data = {
            "coordinates": [
                {"coordinate_id": "py_1", "fiber_name": "python"},
                {"coordinate_id": "js_1", "fiber_name": "javascript"},
            ],
            "morphisms": [
                {"morphism_id": "m1", "source_fiber": "python", "target_fiber": "python",
                 "source_coord": "py_1", "target_coord": "py_1", "lift_type": "INTERNAL"},
                {"morphism_id": "m2", "source_fiber": "python", "target_fiber": "javascript",
                 "source_coord": "py_1", "target_coord": "js_1", "lift_type": "API_CONTRACT"},
            ],
        }
        fiber_data = category.fiber_over("python", site_data)
        assert "fiber" in fiber_data
        assert "coordinates" in fiber_data
        assert "morphisms" in fiber_data
        # Only python coordinates
        assert len(fiber_data["coordinates"]) == 1
        # Only internal morphisms
        assert len(fiber_data["morphisms"]) == 1

    def test_cartesian_lifts_returns_list(self, category):
        site_data = {
            "morphisms": [
                {"morphism_id": "python_to_sql_1", "source_fiber": "python",
                 "target_fiber": "sql", "lift_type": "ORM_MAPPING",
                 "source_coord": "py_1", "target_coord": "sql_1"},
            ],
        }
        lifts = category.cartesian_lifts("python_to_sql", site_data)
        assert isinstance(lifts, list)
        assert len(lifts) >= 1

    def test_change_of_fiber_returns_dict(self, category):
        site_data = {
            "coordinates": [
                {"coordinate_id": "py_1", "fiber_name": "python"},
                {"coordinate_id": "tpl_1", "fiber_name": "template"},
            ],
            "morphisms": [
                {"morphism_id": "m1", "source_fiber": "python", "target_fiber": "template",
                 "source_coord": "py_1", "target_coord": "tpl_1", "lift_type": "CONTEXT"},
            ],
        }
        result = category.change_of_fiber("python", "template", site_data)
        assert "mappings" in result
        assert "unmapped_source" in result
        assert "unmapped_target" in result
        assert len(result["mappings"]) == 1


# ===================================================================
# FiberDescentEngine tests
# ===================================================================

class TestFiberDescentEngine:

    def test_check_per_fiber_descent_empty(self, descent_engine):
        result = descent_engine.check_per_fiber_descent({})
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_check_per_fiber_descent_python(self, descent_engine):
        result = descent_engine.check_per_fiber_descent({
            "python": {"routes": [], "models": [], "imports": []},
        })
        assert "python" in result
        assert isinstance(result["python"], FiberDescentResult)
        assert result["python"].fiber_name == "python"

    def test_check_boundary_descent_empty(self, descent_engine):
        result = descent_engine.check_boundary_descent({})
        assert isinstance(result, list)

    def test_check_global_descent_structure(self, descent_engine):
        per_fiber = {
            "python": FiberDescentResult(fiber_name="python", passed=True),
        }
        result = descent_engine.check_global_descent(per_fiber, [])
        assert isinstance(result, FiberedSiteResult)
        assert result.overall_passed is True

    def test_python_fiber_descent_clean(self, descent_engine):
        result = descent_engine._python_fiber_descent({
            "routes": [{"path": "/index", "handler": "index_view"}],
            "models": [{"name": "User", "fields": ["id", "name"]}],
            "imports": [],
        })
        assert result.fiber_name == "python"
        assert result.passed is True

    def test_python_fiber_descent_no_handler(self, descent_engine):
        result = descent_engine._python_fiber_descent({
            "routes": [{"path": "/index"}],
            "models": [],
            "imports": [],
        })
        assert result.passed is False
        assert len(result.local_obstructions) >= 1

    def test_js_fiber_descent_clean(self, descent_engine):
        result = descent_engine._js_fiber_descent({
            "functions": [{"name": "fetchUser"}],
            "event_handlers": [],
            "fetch_calls": [],
        })
        assert result.fiber_name == "javascript"
        assert result.passed is True

    def test_css_fiber_descent_clean(self, descent_engine):
        result = descent_engine._css_fiber_descent({
            "rules": [".user-card"],
            "referenced_classes": ["user-card"],
        })
        assert result.fiber_name == "css"
        assert result.passed is True
        assert len(result.local_obstructions) == 0

    def test_css_fiber_descent_missing_class(self, descent_engine):
        result = descent_engine._css_fiber_descent({
            "rules": [],
            "referenced_classes": ["nonexistent"],
        })
        assert len(result.local_obstructions) >= 1

    def test_template_fiber_descent_clean(self, descent_engine):
        result = descent_engine._template_fiber_descent({
            "variables": ["user"],
            "provided_context": ["user"],
            "blocks": [],
        })
        assert result.fiber_name == "template"
        assert result.passed is True

    def test_template_fiber_descent_missing_var(self, descent_engine):
        result = descent_engine._template_fiber_descent({
            "variables": ["user", "title"],
            "provided_context": ["user"],
            "blocks": [],
        })
        assert result.passed is False
        assert len(result.local_obstructions) >= 1

    def test_cross_fiber_conditions_python_template(self, descent_engine):
        conditions = descent_engine._cross_fiber_conditions("python", "template", [])
        assert isinstance(conditions, list)
        assert len(conditions) >= 1
        assert "context_completeness" in conditions

    def test_cross_fiber_conditions_unknown_pair(self, descent_engine):
        conditions = descent_engine._cross_fiber_conditions("unknown1", "unknown2", [])
        assert conditions == []

    def test_global_descent_fails_on_critical_boundary(self, descent_engine):
        per_fiber = {
            "python": FiberDescentResult(fiber_name="python", passed=True),
        }
        boundary = [{"severity": "error", "obstruction": "critical issue"}]
        result = descent_engine.check_global_descent(per_fiber, boundary)
        assert result.overall_passed is False


# ===================================================================
# Language fiber class tests
# ===================================================================

class TestLanguageFiberClasses:

    def test_python_fiber_instance(self):
        fib = PythonFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "python"

    def test_python_fiber_coordinate_kinds(self):
        assert "ROUTE_HANDLER" in PythonFiber.coordinate_kinds

    def test_python_fiber_has_descent_conditions(self):
        assert len(PythonFiber.internal_descent_conditions) > 0

    def test_javascript_fiber_instance(self):
        fib = JavaScriptFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "javascript"

    def test_javascript_fiber_coordinate_kinds(self):
        assert "JS_FUNCTION" in JavaScriptFiber.coordinate_kinds

    def test_css_fiber_instance(self):
        fib = CSSFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "css"

    def test_css_fiber_coordinate_kinds(self):
        assert "CSS_RULE" in CSSFiber.coordinate_kinds

    def test_html_fiber_instance(self):
        fib = HTMLFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "html"

    def test_sql_fiber_instance(self):
        fib = SQLFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "sql"

    def test_template_fiber_instance(self):
        fib = TemplateFiber.get_fiber()
        assert isinstance(fib, LanguageFiber)
        assert fib.name == "template"

    def test_template_fiber_descent_conditions(self):
        assert "variable_defined_in_context" in TemplateFiber.internal_descent_conditions

    def test_all_fibers_have_non_empty_coordinate_kinds(self):
        fiber_classes = [
            PythonFiber, JavaScriptFiber, CSSFiber,
            HTMLFiber, SQLFiber, TemplateFiber,
        ]
        for cls in fiber_classes:
            assert len(cls.coordinate_kinds) > 0, f"{cls.__name__} has empty coordinate_kinds"

    def test_all_fibers_have_internal_morphisms(self):
        fiber_classes = [
            PythonFiber, JavaScriptFiber, CSSFiber,
            HTMLFiber, SQLFiber, TemplateFiber,
        ]
        for cls in fiber_classes:
            assert len(cls.internal_morphisms) > 0, f"{cls.__name__} has empty internal_morphisms"

    def test_all_fibers_get_fiber_returns_language_fiber(self):
        fiber_classes = [
            PythonFiber, JavaScriptFiber, CSSFiber,
            HTMLFiber, SQLFiber, TemplateFiber,
        ]
        for cls in fiber_classes:
            fib = cls.get_fiber()
            assert isinstance(fib, LanguageFiber), f"{cls.__name__}.get_fiber() did not return LanguageFiber"
