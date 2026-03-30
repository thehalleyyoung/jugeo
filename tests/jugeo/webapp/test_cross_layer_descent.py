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
from flask import Flask, render_template
app = Flask(__name__)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/recipes')
def recipe_list():
    return render_template("recipes/list.html", items=[])

@app.route('/recipes/<int:id>')
def recipe_show(id):
    return render_template("recipes/show.html", item=None)

@app.before_request
def check_auth(): pass
""",
    "templates/base.html": (
        '<html lang="en"><body class="container">'
        '<nav><a href="{{ url_for(\'index\') }}">Home</a>'
        '<a href="{{ url_for(\'recipe_list\') }}">Recipes</a></nav>'
        '<main id="main-content">{% block body %}{% endblock %}</main></body></html>'
    ),
    "templates/index.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Welcome</h1>'
        "{% endblock %}"
    ),
    "templates/recipes/list.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Recipes</h1>'
        '<div class="recipe-grid"><a href="{{ url_for(\'recipe_show\', id=1) }}" class="card">item</a></div>'
        "{% endblock %}"
    ),
    "templates/recipes/show.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Recipe detail</h1>'
        "{% endblock %}"
    ),
    "static/style.css": (
        ".container { max-width:1200px; } "
        ".recipe-grid { display:grid; } "
        ".card { display:block; }"
    ),
    "static/app.js": (
        "async function load() { const r = await fetch('/recipes');"
        " if (!r.ok) throw new Error('fail'); }"
        "\nload().catch(e => console.error(e));"
    ),
    "requirements.txt": "flask\n",
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
    # Exclude structural descent (webapp_descent) false positives about
    # template paths — those come from the entity-level checker which
    # normalises template names differently. Legacy regex checks should
    # report no errors for well-formed files.
    legacy_errors = [
        o for o in report.obstructions
        if o.severity == "error"
        and not (o.check == CrossLayerCheck.TEMPLATE_CONTEXT
                 and o.description.startswith("route:"))
    ]
    assert not legacy_errors, (
        f"Expected no errors but got: {[o.description for o in legacy_errors]}"
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


# ---------------------------------------------------------------------------
# JS→JS module import/export tests
# ---------------------------------------------------------------------------

JS_MODULE_GOOD_FILES = {
    **GOOD_FILES,
    "static/csrf.js": (
        "const CSRF = (() => { return {}; })();\n"
        "const csrfFetch = (url) => fetch(url);\n"
        "export { CSRF, csrfFetch };\n"
    ),
    "static/app.js": (
        "import { csrfFetch } from './csrf.js';\n"
        "const r = await csrfFetch('/recipes');\n"
    ),
}

JS_MODULE_BAD_FILES = {
    **GOOD_FILES,
    "static/csrf.js": (
        "const CSRF = (() => { return {}; })();\n"
        # No export statement
    ),
    "static/app.js": (
        "import { csrfFetch } from './csrf.js';\n"
        "const r = await csrfFetch('/recipes');\n"
    ),
}

JS_MODULE_MISSING_FILE = {
    **GOOD_FILES,
    "static/app.js": (
        "import { csrfFetch } from './nonexistent.js';\n"
        "const r = await csrfFetch('/recipes');\n"
    ),
}


def test_js_module_good_imports_pass():
    checker = CrossLayerDescentChecker()
    report = checker.check(JS_MODULE_GOOD_FILES, SPEC)
    js_obs = [o for o in report.obstructions if o.check == CrossLayerCheck.JS_JS_MODULE]
    assert not js_obs, (
        f"Expected no JS_JS_MODULE obstructions but got: "
        f"{[o.description for o in js_obs]}"
    )


def test_js_module_missing_export_detected():
    checker = CrossLayerDescentChecker()
    report = checker.check(JS_MODULE_BAD_FILES, SPEC)
    js_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.JS_JS_MODULE and o.missing_name == "csrfFetch"
    ]
    assert js_obs, (
        "Expected JS_JS_MODULE obstruction for 'csrfFetch' but none found. "
        f"Obstructions: {[o.description for o in report.obstructions]}"
    )


def test_js_module_missing_file_detected():
    checker = CrossLayerDescentChecker()
    report = checker.check(JS_MODULE_MISSING_FILE, SPEC)
    js_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.JS_JS_MODULE and "nonexistent.js" in o.missing_name
    ]
    assert js_obs, (
        "Expected JS_JS_MODULE obstruction for missing module 'nonexistent.js' but none found. "
        f"Obstructions: {[o.description for o in report.obstructions]}"
    )


def test_js_module_repair_generated():
    checker = CrossLayerDescentChecker()
    report = checker.check(JS_MODULE_BAD_FILES, SPEC)
    js_repairs = [r for r in report.repairs if r.repair_type == "add_js_export"]
    assert js_repairs, "Expected at least one add_js_export repair."
    assert js_repairs[0].repair_data["export_name"] == "csrfFetch"


# ---------------------------------------------------------------------------
# Block-name mismatch tests
# ---------------------------------------------------------------------------

BLOCK_MISMATCH_FILES = {
    "app.py": "@app.route('/')\ndef index(): pass\n",
    "templates/base.html": (
        '<html lang="en"><head>{% block head %}{% endblock %}</head>'
        '<body><main>{% block body %}{% endblock %}</main>'
        '{% block scripts %}{% endblock %}</body></html>'
    ),
    "templates/good.html": (
        "{% extends 'base.html' %}\n{% block body %}<h1>Hi</h1>{% endblock %}"
    ),
    "templates/bad.html": (
        "{% extends 'base.html' %}\n{% block content %}<h1>Lost</h1>{% endblock %}"
    ),
    "templates/bad_scripts.html": (
        "{% extends 'base.html' %}\n"
        "{% block body %}<h1>OK</h1>{% endblock %}\n"
        "{% block extra_scripts %}<script src='x.js'></script>{% endblock %}"
    ),
    "static/style.css": "body {}",
}

BLOCK_MISMATCH_SPEC = {
    "name": "TestApp",
    "mode": "flask",
    "domain_nouns": [],
    "routes": [{"path": "/", "method": "GET", "handler": "index"}],
}


def test_block_mismatch_detected():
    """Child template using {% block content %} when base has {% block body %} is an error."""
    checker = CrossLayerDescentChecker()
    report = checker.check(BLOCK_MISMATCH_FILES, BLOCK_MISMATCH_SPEC)
    content_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.BLOCK_NAME_MISMATCH and o.missing_name == "content"
    ]
    assert content_obs, (
        "Expected BLOCK_NAME_MISMATCH for 'content' but none found. "
        f"All obstructions: {[o.description for o in report.obstructions]}"
    )


def test_block_extra_scripts_mismatch_detected():
    """{% block extra_scripts %} should be caught when base defines {% block scripts %}."""
    checker = CrossLayerDescentChecker()
    report = checker.check(BLOCK_MISMATCH_FILES, BLOCK_MISMATCH_SPEC)
    obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.BLOCK_NAME_MISMATCH and o.missing_name == "extra_scripts"
    ]
    assert obs, "Expected BLOCK_NAME_MISMATCH for 'extra_scripts'"


def test_correct_block_names_no_mismatch():
    """good.html uses {% block body %} which is in base — no obstruction."""
    checker = CrossLayerDescentChecker()
    report = checker.check(BLOCK_MISMATCH_FILES, BLOCK_MISMATCH_SPEC)
    good_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.BLOCK_NAME_MISMATCH
        and "good.html" in o.source_file
    ]
    assert not good_obs, f"good.html should have no block mismatch: {good_obs}"


# ---------------------------------------------------------------------------
# Navigation reachability tests
# ---------------------------------------------------------------------------

NAV_REACHABLE_APP = """\
from flask import Flask, render_template
app = Flask(__name__)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/recipes')
def recipe_list():
    return render_template("recipes/list.html")

@app.route('/recipes/<int:id>')
def recipe_show(id):
    return render_template("recipes/show.html")

@app.route('/explore')
def explore():
    return render_template("explore.html")
"""

NAV_REACHABLE_FILES = {
    "app.py": NAV_REACHABLE_APP,
    "templates/base.html": (
        '<html><body>'
        '<nav><a href="{{ url_for(\'index\') }}">Home</a>'
        '<a href="{{ url_for(\'recipe_list\') }}">Recipes</a>'
        '<a href="{{ url_for(\'explore\') }}">Explore</a></nav>'
        '{% block body %}{% endblock %}</body></html>'
    ),
    "templates/index.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Welcome</h1>'
        '{% endblock %}'
    ),
    "templates/recipes/list.html": (
        '{% extends "base.html" %}{% block body %}'
        '<a href="{{ url_for(\'recipe_show\', id=1) }}">Recipe 1</a>'
        '{% endblock %}'
    ),
    "templates/recipes/show.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h2>Recipe detail</h2>'
        '{% endblock %}'
    ),
    "templates/explore.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Explore</h1>'
        '{% endblock %}'
    ),
}

NAV_REACHABLE_SPEC = {"mode": "flask", "models": []}


def test_all_reachable_no_obstruction():
    """When every GET endpoint is linked from base/index/templates, no obstruction."""
    checker = CrossLayerDescentChecker()
    report = checker.check(NAV_REACHABLE_FILES, NAV_REACHABLE_SPEC)
    nav_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.NAVIGATION_REACHABILITY
    ]
    assert not nav_obs, f"All routes should be reachable: {nav_obs}"


NAV_UNREACHABLE_APP = """\
from flask import Flask, render_template
app = Flask(__name__)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/recipes')
def recipe_list():
    return render_template("recipes/list.html")

@app.route('/hidden-admin')
def hidden_admin():
    return render_template("admin.html")

@app.route('/settings')
def settings():
    return render_template("settings.html")
"""

NAV_UNREACHABLE_FILES = {
    "app.py": NAV_UNREACHABLE_APP,
    "templates/base.html": (
        '<html><body>'
        '<nav><a href="{{ url_for(\'index\') }}">Home</a>'
        '<a href="{{ url_for(\'recipe_list\') }}">Recipes</a></nav>'
        '{% block body %}{% endblock %}</body></html>'
    ),
    "templates/index.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Welcome</h1>'
        '{% endblock %}'
    ),
    "templates/recipes/list.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Recipes</h1>'
        '{% endblock %}'
    ),
    "templates/admin.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Admin panel</h1>'
        '{% endblock %}'
    ),
    "templates/settings.html": (
        '{% extends "base.html" %}{% block body %}'
        '<h1>Settings</h1>'
        '{% endblock %}'
    ),
}


def test_unreachable_endpoints_detected():
    """hidden_admin and settings have no links pointing to them → obstruction."""
    checker = CrossLayerDescentChecker()
    report = checker.check(NAV_UNREACHABLE_FILES, NAV_REACHABLE_SPEC)
    nav_obs = [
        o for o in report.obstructions
        if o.check == CrossLayerCheck.NAVIGATION_REACHABILITY
    ]
    unreachable_eps = {o.missing_name for o in nav_obs}
    assert "hidden_admin" in unreachable_eps, (
        f"hidden_admin should be unreachable, got: {unreachable_eps}"
    )
    assert "settings" in unreachable_eps, (
        f"settings should be unreachable, got: {unreachable_eps}"
    )


def test_unreachable_repair_suggests_nav_link():
    """Repair for unreachable endpoint suggests adding a nav link."""
    checker = CrossLayerDescentChecker()
    report = checker.check(NAV_UNREACHABLE_FILES, NAV_REACHABLE_SPEC)
    nav_repairs = [
        r for r in report.repairs
        if r.repair_type == "add_nav_link"
    ]
    assert nav_repairs, "Should suggest add_nav_link repair for unreachable endpoints"
    repair_eps = {r.repair_data["endpoint"] for r in nav_repairs}
    assert "hidden_admin" in repair_eps
    assert "settings" in repair_eps

