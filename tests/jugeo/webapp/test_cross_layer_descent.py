"""Tests for Phase 3 cross-layer descent checking."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

import pytest

from jugeo.webapp.cli.cross_layer_descent import (
    CrossLayerCheck,
    CrossLayerDescentChecker,
    CrossLayerObstruction,
    CrossLayerReport,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_FILES = {
    "app.py": """
@app.route('/recipes')
def recipe_list(): pass
@app.route('/recipes/<int:id>')
def recipe_show(id): pass
@app.before_request
def check_auth(): pass
""",
    "templates/base.html": (
        '<html lang="en"><body class="container">'
        '<main id="main-content">{% block body %}{% endblock %}</main></body></html>'
    ),
    "templates/recipes/list.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Recipes</h1>'
        '<div class="recipe-grid"><a href="/recipes/1" class="card">item</a></div>'
        "{% endblock %}"
    ),
    "static/style.css": (
        ".container { max-width:1200px; } "
        ".recipe-grid { display:grid; } "
        ".card { display:block; }"
    ),
    "static/app.js": "const r = await fetch('/recipes');",
}

SPEC = {
    "mode": "flask",
    "models": [
        {"name": "Recipe", "fields": [{"name": "id"}, {"name": "title"}]},
    ],
}

# BAD_FILES introduces a missing CSS class and a bad fetch URL.
BAD_FILES = {
    **GOOD_FILES,
    "templates/recipes/list.html": (
        '{% extends "base.html" %}{% block body %}'
        '<div class="missing-class card">x</div>'
        "{% endblock %}"
    ),
    "static/app.js": "const r = await fetch('/nonexistent-route');",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_clean_files_no_errors():
    checker = CrossLayerDescentChecker()
    report = checker.check(GOOD_FILES, SPEC)
    assert not report.has_errors(), (
        f"Expected no errors but got: {[o.description for o in report.obstructions if o.severity == 'error']}"
    )


def test_missing_css_class_detected():
    checker = CrossLayerDescentChecker()
    report = checker.check(BAD_FILES, SPEC)
    html_css_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.HTML_CSS and o.missing_name == "missing-class"
    ]
    assert html_css_obs, (
        "Expected HTML_CSS obstruction for 'missing-class' but none found. "
        f"Obstructions: {[o.missing_name for o in report.obstructions]}"
    )


def test_missing_js_route_detected():
    checker = CrossLayerDescentChecker()
    report = checker.check(BAD_FILES, SPEC)
    js_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.JS_FLASK and "/nonexistent-route" in o.missing_name
    ]
    assert js_obs, (
        "Expected JS_FLASK obstruction for '/nonexistent-route' but none found. "
        f"Obstructions: {[o.missing_name for o in report.obstructions]}"
    )


def test_report_has_passed_checks():
    checker = CrossLayerDescentChecker()
    report = checker.check(GOOD_FILES, SPEC)
    assert report.passed_checks, "Expected at least one passed check but got none."


def test_repair_generated_for_obstruction():
    checker = CrossLayerDescentChecker()
    report = checker.check(BAD_FILES, SPEC)
    assert report.repairs, (
        "Expected repairs to be generated for obstructions but got none."
    )


def test_css_repair_has_rule():
    checker = CrossLayerDescentChecker()
    report = checker.check(BAD_FILES, SPEC)
    css_repairs = [r for r in report.repairs if r.repair_type == "add_css_rule"]
    assert css_repairs, "Expected at least one add_css_rule repair."
    assert css_repairs[0].repair_type == "add_css_rule"


def test_static_mode_fewer_checks():
    checker = CrossLayerDescentChecker()
    static_spec = {"mode": "static", "models": []}
    report = checker.check(GOOD_FILES, static_spec)
    all_checks = set(report.passed_checks) | {o.check for o in report.obstructions}
    assert CrossLayerCheck.HTML_FLASK not in all_checks, (
        "HTML_FLASK check should not run in static mode"
    )


def test_summary_string_nonempty():
    checker = CrossLayerDescentChecker()
    report = checker.check(GOOD_FILES, SPEC)
    summary = report.summary()
    assert isinstance(summary, str) and len(summary) > 0


def test_to_descent_result_success():
    checker = CrossLayerDescentChecker()
    report = checker.check(GOOD_FILES, SPEC)
    result = report.to_descent_result()
    # If jugeo is available the result should indicate success; otherwise None is fine.
    if result is not None:
        assert result.is_success, f"Expected successful DescentResult but got: {result}"
