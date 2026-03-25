"""Comprehensive tests for jugeo.webapp.parsers — 90+ tests covering all parsers."""
from __future__ import annotations

import os
import shutil
import tempfile
import textwrap

import pytest

from jugeo.webapp.parsers.models import (
    CoordinateKind,
    ErrorSeverity,
    Language,
    ParsedCoordinate,
    ParsedReference,
    ParseError,
    ParseResult,
    ProjectParseResult,
    ReferenceType,
)
from jugeo.webapp.parsers.flask_loader import (
    FlaskRouteExtractor,
    extract_flask_coordinates,
    extract_render_template_kwargs,
)
from jugeo.webapp.parsers.jinja2_analyzer import (
    Jinja2TemplateParser,
    extract_template_coordinates,
    extract_template_variables,
    extract_template_blocks,
)
from jugeo.webapp.parsers.javascript_parser import (
    JavaScriptParser,
    extract_js_coordinates,
    extract_dom_references,
    extract_class_references,
    extract_fetch_urls,
)
from jugeo.webapp.parsers.css_analyzer import (
    CSSParser,
    CSSSpecificityCalculator,
    CSSCascadeAnalyzer,
    extract_css_coordinates,
    extract_css_classes,
    extract_css_ids,
    compute_specificity,
)
from jugeo.webapp.parsers.html_parser import (
    HTMLStructureParser,
    extract_html_coordinates,
    extract_html_ids,
    extract_html_classes,
    extract_form_actions,
)
from jugeo.webapp.parsers.sql_schema_loader import (
    SQLSchemaParser,
    extract_sql_coordinates,
    extract_tables,
    extract_foreign_keys,
)
from jugeo.webapp.parsers.project_scanner import (
    FlaskProjectScanner,
    scan_project,
    detect_flask_structure,
    resolve_cross_references,
)
from jugeo.webapp.parsers.integration import (
    coordinate_to_web_coordinate,
    reference_to_morphism,
    build_web_site_from_project,
    ParserPipeline,
)


# ===================================================================
# Model serialisation tests
# ===================================================================

class TestModels:
    def test_coordinate_kind_is_str(self):
        assert isinstance(CoordinateKind.ROUTE_HANDLER, str)
        assert CoordinateKind.ROUTE_HANDLER == "route_handler"

    def test_language_values(self):
        assert Language.PYTHON.value == "python"
        assert Language.JINJA2.value == "jinja2"

    def test_parsed_coordinate_to_from_dict(self):
        coord = ParsedCoordinate(
            id="abc", kind=CoordinateKind.ROUTE_HANDLER, name="index",
            file_path="app.py", line_number=5, end_line=10,
            language=Language.PYTHON, metadata={"path": "/"},
        )
        d = coord.to_dict()
        assert d["kind"] == "route_handler"
        assert d["language"] == "python"
        restored = ParsedCoordinate.from_dict(d)
        assert restored.kind is CoordinateKind.ROUTE_HANDLER
        assert restored.metadata == {"path": "/"}

    def test_parsed_reference_to_from_dict(self):
        ref = ParsedReference(
            source_id="s1", target_name="index.html",
            reference_type=ReferenceType.RENDERS_TEMPLATE,
            file_path="app.py", line_number=7,
        )
        d = ref.to_dict()
        assert d["reference_type"] == "renders_template"
        restored = ParsedReference.from_dict(d)
        assert restored.reference_type is ReferenceType.RENDERS_TEMPLATE

    def test_parse_error_to_from_dict(self):
        err = ParseError("app.py", 1, "bad syntax", ErrorSeverity.ERROR)
        d = err.to_dict()
        assert d["severity"] == "error"
        restored = ParseError.from_dict(d)
        assert restored.severity is ErrorSeverity.ERROR

    def test_parse_result_to_from_dict(self):
        pr = ParseResult(file_path="x.py", language=Language.PYTHON)
        d = pr.to_dict()
        restored = ParseResult.from_dict(d)
        assert restored.language is Language.PYTHON
        assert restored.coordinates == []

    def test_project_parse_result_to_from_dict(self):
        ppr = ProjectParseResult()
        d = ppr.to_dict()
        restored = ProjectParseResult.from_dict(d)
        assert restored.files == []


# ===================================================================
# Flask parser tests
# ===================================================================

class TestFlaskParser:
    def test_extract_simple_route(self):
        src = textwrap.dedent("""\
            from flask import Flask
            app = Flask(__name__)

            @app.route('/hello')
            def hello():
                return 'Hello'
        """)
        result = extract_flask_coordinates(src, "app.py")
        routes = [c for c in result.coordinates if c.kind == CoordinateKind.ROUTE_HANDLER]
        assert len(routes) == 1
        assert routes[0].name == "hello"
        assert routes[0].metadata["path"] == "/hello"

    def test_extract_route_with_methods(self):
        src = textwrap.dedent("""\
            @app.route('/api/data', methods=['GET', 'POST'])
            def api_data():
                pass
        """)
        result = extract_flask_coordinates(src, "api.py")
        routes = [c for c in result.coordinates if c.kind == CoordinateKind.ROUTE_HANDLER]
        assert len(routes) == 1
        assert set(routes[0].metadata["methods"]) == {"GET", "POST"}

    def test_extract_blueprint_route(self):
        src = textwrap.dedent("""\
            @auth.route('/login')
            def login():
                pass
        """)
        result = extract_flask_coordinates(src, "auth.py")
        routes = [c for c in result.coordinates if c.kind == CoordinateKind.ROUTE_HANDLER]
        assert len(routes) == 1
        assert routes[0].metadata["path"] == "/login"

    def test_extract_model_class(self):
        src = textwrap.dedent("""\
            class User(db.Model):
                id = db.Column(db.Integer, primary_key=True)
                username = db.Column(db.String(80))
        """)
        result = extract_flask_coordinates(src, "models.py")
        models = [c for c in result.coordinates if c.kind == CoordinateKind.MODEL_CLASS]
        assert len(models) == 1
        assert models[0].name == "User"
        assert "id" in models[0].metadata["columns"]
        assert "username" in models[0].metadata["columns"]

    def test_extract_form_class(self):
        src = textwrap.dedent("""\
            class LoginForm(FlaskForm):
                username = StringField('Username')
                password = PasswordField('Password')
                submit = SubmitField('Sign In')
        """)
        result = extract_flask_coordinates(src, "forms.py")
        forms = [c for c in result.coordinates if c.kind == CoordinateKind.FORM_CLASS]
        assert len(forms) == 1
        assert forms[0].name == "LoginForm"
        assert "username" in forms[0].metadata["fields"]

    def test_extract_blueprint_instantiation(self):
        src = "auth = Blueprint('auth', __name__, url_prefix='/auth')\n"
        result = extract_flask_coordinates(src, "auth.py")
        bps = [c for c in result.coordinates if c.kind == CoordinateKind.BLUEPRINT]
        assert len(bps) == 1
        assert bps[0].name == "auth"

    def test_extract_middleware(self):
        src = textwrap.dedent("""\
            @app.before_request
            def load_user():
                pass
        """)
        result = extract_flask_coordinates(src, "app.py")
        mws = [c for c in result.coordinates if c.kind == CoordinateKind.MIDDLEWARE]
        assert len(mws) == 1
        assert mws[0].metadata["hook"] == "app.before_request"

    def test_extract_error_handler(self):
        src = textwrap.dedent("""\
            @app.errorhandler(404)
            def not_found(e):
                return 'Not Found', 404
        """)
        result = extract_flask_coordinates(src, "app.py")
        ehs = [c for c in result.coordinates if c.kind == CoordinateKind.ERROR_HANDLER]
        assert len(ehs) == 1
        assert ehs[0].metadata["error_code"] == 404

    def test_extract_render_template(self):
        src = textwrap.dedent("""\
            def index():
                return render_template('index.html', user=user, title='Home')
        """)
        result = extract_flask_coordinates(src, "views.py")
        refs = [r for r in result.references if r.reference_type == ReferenceType.RENDERS_TEMPLATE]
        assert len(refs) == 1
        assert refs[0].target_name == "index.html"

    def test_extract_url_for(self):
        src = "redirect(url_for('auth.login'))\n"
        result = extract_flask_coordinates(src, "views.py")
        refs = [r for r in result.references if r.reference_type == ReferenceType.URL_FOR]
        assert len(refs) == 1
        assert refs[0].target_name == "auth.login"

    def test_extract_config_access(self):
        src = "secret = app.config['SECRET_KEY']\n"
        result = extract_flask_coordinates(src, "app.py")
        refs = [r for r in result.references if r.reference_type == ReferenceType.CONFIG_ACCESS]
        assert len(refs) == 1
        assert refs[0].target_name == "SECRET_KEY"

    def test_extract_session_access(self):
        src = "uid = session['user_id']\n"
        result = extract_flask_coordinates(src, "views.py")
        refs = [r for r in result.references if r.reference_type == ReferenceType.SESSION_ACCESS]
        assert len(refs) == 1
        assert refs[0].target_name == "user_id"

    def test_render_template_kwargs(self):
        src = textwrap.dedent("""\
            render_template('home.html', user=u, items=items)
            render_template('about.html')
        """)
        results = extract_render_template_kwargs(src)
        assert len(results) == 2
        assert results[0]["template_name"] == "home.html"
        assert set(results[0]["kwargs"]) == {"user", "items"}
        assert results[1]["kwargs"] == []

    def test_multiple_routes_in_file(self):
        src = textwrap.dedent("""\
            @app.route('/')
            def index():
                pass

            @app.route('/about')
            def about():
                pass

            @app.route('/contact', methods=['POST'])
            def contact():
                pass
        """)
        result = extract_flask_coordinates(src, "app.py")
        routes = [c for c in result.coordinates if c.kind == CoordinateKind.ROUTE_HANDLER]
        assert len(routes) == 3

    def test_parse_result_structure(self):
        src = "@app.route('/')\ndef index(): pass\n"
        result = extract_flask_coordinates(src, "app.py")
        assert isinstance(result, ParseResult)
        assert result.language is Language.PYTHON
        assert result.parse_time_ms >= 0

    def test_syntax_error_handling(self):
        src = "def broken(:\n"
        result = extract_flask_coordinates(src, "bad.py")
        assert len(result.errors) >= 1
        assert result.errors[0].severity is ErrorSeverity.ERROR


# ===================================================================
# Jinja2 parser tests
# ===================================================================

class TestJinja2Parser:
    def test_extract_block(self):
        src = "{% block content %}Hello{% endblock %}"
        result = extract_template_coordinates(src, "tpl.html")
        blocks = [c for c in result.coordinates if c.kind == CoordinateKind.TEMPLATE_BLOCK]
        assert len(blocks) == 1
        assert blocks[0].name == "content"

    def test_extract_macro(self):
        src = "{% macro render_field(field) %}<div>{{ field }}</div>{% endmacro %}"
        result = extract_template_coordinates(src, "macros.html")
        macros = [c for c in result.coordinates if c.kind == CoordinateKind.TEMPLATE_MACRO]
        assert len(macros) == 1
        assert macros[0].name == "render_field"
        assert macros[0].metadata["args"] == ["field"]

    def test_extract_variable(self):
        src = "{{ user.name }}"
        result = extract_template_coordinates(src, "tpl.html")
        vars_ = [c for c in result.coordinates if c.kind == CoordinateKind.TEMPLATE_VARIABLE]
        assert any(v.name == "user.name" for v in vars_)

    def test_extract_filter(self):
        src = "{{ user.name | upper }}"
        result = extract_template_coordinates(src, "tpl.html")
        filters = [c for c in result.coordinates if c.kind == CoordinateKind.TEMPLATE_FILTER]
        assert any(f.name == "upper" for f in filters)

    def test_extract_extends(self):
        src = "{% extends 'base.html' %}"
        result = extract_template_coordinates(src, "child.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.TEMPLATE_EXTENDS]
        assert len(refs) == 1
        assert refs[0].target_name == "base.html"

    def test_extract_include(self):
        src = "{% include 'nav.html' %}"
        result = extract_template_coordinates(src, "page.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.TEMPLATE_INCLUDES]
        assert len(refs) == 1
        assert refs[0].target_name == "nav.html"

    def test_extract_url_for(self):
        src = '<a href="{{ url_for(\'index\') }}">Home</a>'
        result = extract_template_coordinates(src, "nav.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.TEMPLATE_URL_FOR]
        assert len(refs) == 1
        assert refs[0].target_name == "index"

    def test_extract_static_ref(self):
        src = '<link href="{{ url_for(\'static\', filename=\'css/main.css\') }}">'
        result = extract_template_coordinates(src, "base.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.STATIC_REFERENCE]
        assert len(refs) == 1
        assert refs[0].target_name == "css/main.css"

    def test_extract_conditional(self):
        src = "{% if user.is_authenticated %}Welcome{% endif %}"
        result = extract_template_coordinates(src, "tpl.html")
        conds = [c for c in result.coordinates if c.kind == CoordinateKind.CONDITIONAL_RENDER]
        assert len(conds) == 1
        assert "user.is_authenticated" in conds[0].name

    def test_extract_html_elements(self):
        src = '<div id="main" class="container">Hello</div>'
        result = extract_template_coordinates(src, "tpl.html")
        elems = [c for c in result.coordinates if c.kind == CoordinateKind.HTML_ELEMENT]
        assert len(elems) >= 1
        assert any(e.metadata.get("id") == "main" for e in elems)

    def test_template_variables_function(self):
        src = "{{ user.name }} {{ post.title }} {{ count }}"
        vars_ = extract_template_variables(src)
        assert "user" in vars_
        assert "post" in vars_
        assert "count" in vars_

    def test_template_blocks_function(self):
        src = "{% block header %}{% endblock %}{% block content %}{% endblock %}"
        blocks = extract_template_blocks(src)
        assert "header" in blocks
        assert "content" in blocks

    def test_multiple_variables(self):
        src = "{{ a }} {{ b }} {{ c }}"
        vars_ = extract_template_variables(src)
        assert vars_ == {"a", "b", "c"}

    def test_nested_attribute_variable(self):
        src = "{{ post.author.name }}"
        vars_ = extract_template_variables(src)
        assert "post" in vars_

    def test_chained_filters(self):
        src = "{{ value | trim | upper }}"
        result = extract_template_coordinates(src, "tpl.html")
        filters = [c for c in result.coordinates if c.kind == CoordinateKind.TEMPLATE_FILTER]
        names = {f.name for f in filters}
        assert "trim" in names
        assert "upper" in names


# ===================================================================
# JavaScript parser tests
# ===================================================================

class TestJavaScriptParser:
    def test_extract_function_declaration(self):
        src = "function greet(name) { return 'Hi ' + name; }"
        result = extract_js_coordinates(src, "app.js")
        fns = [c for c in result.coordinates if c.kind == CoordinateKind.JS_FUNCTION]
        assert any(f.name == "greet" for f in fns)

    def test_extract_arrow_function(self):
        src = "const add = (a, b) => a + b;"
        result = extract_js_coordinates(src, "app.js")
        fns = [c for c in result.coordinates if c.kind == CoordinateKind.JS_FUNCTION]
        assert any(f.name == "add" for f in fns)

    def test_extract_fetch_call(self):
        src = "fetch('/api/users').then(r => r.json());"
        result = extract_js_coordinates(src, "app.js")
        fetches = [c for c in result.coordinates if c.kind == CoordinateKind.JS_FETCH_CALL]
        assert len(fetches) == 1
        assert fetches[0].metadata["url"] == "/api/users"

    def test_extract_axios_get(self):
        src = "axios.get('/api/data');"
        result = extract_js_coordinates(src, "app.js")
        fetches = [c for c in result.coordinates if c.kind == CoordinateKind.JS_FETCH_CALL]
        assert len(fetches) == 1
        assert fetches[0].name == "/api/data"

    def test_extract_getelementbyid(self):
        src = "document.getElementById('main-content');"
        result = extract_js_coordinates(src, "app.js")
        doms = [c for c in result.coordinates if c.kind == CoordinateKind.JS_DOM_MANIPULATION]
        assert any(d.name == "main-content" for d in doms)

    def test_extract_queryselector(self):
        src = "document.querySelector('.header');"
        result = extract_js_coordinates(src, "app.js")
        doms = [c for c in result.coordinates if c.kind == CoordinateKind.JS_DOM_MANIPULATION]
        assert any(d.name == ".header" for d in doms)

    def test_extract_addeventlistener(self):
        src = "btn.addEventListener('click', handler);"
        result = extract_js_coordinates(src, "app.js")
        evts = [c for c in result.coordinates if c.kind == CoordinateKind.JS_EVENT_HANDLER]
        assert any(e.name == "click" for e in evts)

    def test_extract_classlist_add(self):
        src = "elem.classList.add('active');"
        result = extract_js_coordinates(src, "app.js")
        refs = [r for r in result.references if r.reference_type == ReferenceType.CLASS_MANIPULATION]
        assert any(r.target_name == "active" for r in refs)

    def test_extract_style_mutation(self):
        src = "elem.style.display = 'none';"
        result = extract_js_coordinates(src, "app.js")
        refs = [r for r in result.references if r.reference_type == ReferenceType.STYLE_MUTATION]
        assert any(r.target_name == "display" for r in refs)

    def test_extract_import(self):
        src = "import { utils } from './utils';"
        result = extract_js_coordinates(src, "app.js")
        refs = [r for r in result.references if r.reference_type == ReferenceType.MODULE_IMPORT]
        assert any(r.target_name == "./utils" for r in refs)

    def test_extract_require(self):
        src = "const express = require('express');"
        result = extract_js_coordinates(src, "server.js")
        refs = [r for r in result.references if r.reference_type == ReferenceType.MODULE_IMPORT]
        assert any(r.target_name == "express" for r in refs)

    def test_extract_state_variable(self):
        src = "let count = 0;\nconst MAX = 100;"
        result = extract_js_coordinates(src, "app.js")
        svs = [c for c in result.coordinates if c.kind == CoordinateKind.JS_STATE_VARIABLE]
        names = {s.name for s in svs}
        assert "count" in names
        assert "MAX" in names

    def test_dom_references_function(self):
        src = textwrap.dedent("""\
            document.getElementById('header');
            document.querySelector('.nav');
        """)
        refs = extract_dom_references(src)
        assert "header" in refs
        assert ".nav" in refs

    def test_class_references_function(self):
        src = "el.classList.add('active');\nel.classList.toggle('hidden');"
        refs = extract_class_references(src)
        assert "active" in refs
        assert "hidden" in refs

    def test_fetch_urls_function(self):
        src = "fetch('/api/a');\nfetch('/api/b');"
        urls = extract_fetch_urls(src)
        assert "/api/a" in urls
        assert "/api/b" in urls


# ===================================================================
# CSS parser tests
# ===================================================================

class TestCSSParser:
    def test_extract_class_rule(self):
        src = ".container { width: 100%; }"
        result = extract_css_coordinates(src, "style.css")
        rules = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_RULE]
        assert any(".container" in r.name for r in rules)

    def test_extract_id_rule(self):
        src = "#header { background: blue; }"
        result = extract_css_coordinates(src, "style.css")
        rules = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_RULE]
        assert any("#header" in r.name for r in rules)

    def test_extract_element_rule(self):
        src = "body { margin: 0; }"
        result = extract_css_coordinates(src, "style.css")
        rules = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_RULE]
        assert any("body" in r.name for r in rules)

    def test_extract_media_query(self):
        src = "@media (max-width: 768px) { .col { width: 100%; } }"
        result = extract_css_coordinates(src, "style.css")
        mqs = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_MEDIA_QUERY]
        assert len(mqs) == 1

    def test_extract_keyframes(self):
        src = "@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }"
        result = extract_css_coordinates(src, "style.css")
        anims = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_ANIMATION]
        assert len(anims) == 1
        assert anims[0].name == "fadeIn"

    def test_css_classes_function(self):
        src = ".foo { } .bar { } #baz { }"
        classes = extract_css_classes(src)
        assert "foo" in classes
        assert "bar" in classes
        assert "baz" not in classes

    def test_css_ids_function(self):
        src = "#main { } #sidebar { } .widget { }"
        ids = extract_css_ids(src)
        assert "main" in ids
        assert "sidebar" in ids
        assert "widget" not in ids

    def test_specificity_id_selector(self):
        assert compute_specificity("#foo") == (1, 0, 0)

    def test_specificity_class_selector(self):
        assert compute_specificity(".foo") == (0, 1, 0)

    def test_specificity_element_selector(self):
        assert compute_specificity("div") == (0, 0, 1)

    def test_specificity_combined(self):
        assert compute_specificity("div.foo#bar") == (1, 1, 1)

    def test_specificity_pseudo_class(self):
        assert compute_specificity("a:hover") == (0, 1, 1)

    def test_extract_css_properties(self):
        src = ".box { color: red; padding: 10px; }"
        result = extract_css_coordinates(src, "style.css")
        props = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_PROPERTY]
        names = {p.name for p in props}
        assert "color" in names
        assert "padding" in names

    def test_cascade_analyzer_conflicts(self):
        coords = [
            ParsedCoordinate(
                id="c1", kind=CoordinateKind.CSS_RULE, name="div",
                file_path="s.css", line_number=1, end_line=1,
                language=Language.CSS,
            ),
            ParsedCoordinate(
                id="c2", kind=CoordinateKind.CSS_RULE, name="div.highlight",
                file_path="s.css", line_number=5, end_line=5,
                language=Language.CSS,
            ),
        ]
        analyzer = CSSCascadeAnalyzer()
        conflicts = analyzer.find_specificity_conflicts(coords)
        assert len(conflicts) >= 1

    def test_cascade_analyzer_media_overlap(self):
        coords = [
            ParsedCoordinate(
                id="m1", kind=CoordinateKind.CSS_MEDIA_QUERY,
                name="(max-width: 768px)",
                file_path="s.css", line_number=1, end_line=1,
                language=Language.CSS,
            ),
            ParsedCoordinate(
                id="m2", kind=CoordinateKind.CSS_MEDIA_QUERY,
                name="(min-width: 600px)",
                file_path="s.css", line_number=10, end_line=10,
                language=Language.CSS,
            ),
        ]
        analyzer = CSSCascadeAnalyzer()
        overlaps = analyzer.find_media_query_overlaps(coords)
        assert len(overlaps) >= 1


# ===================================================================
# HTML parser tests
# ===================================================================

class TestHTMLParser:
    def test_extract_element_with_id(self):
        src = '<div id="main">Content</div>'
        result = extract_html_coordinates(src, "page.html")
        elems = [c for c in result.coordinates if c.kind == CoordinateKind.HTML_ELEMENT]
        assert any(e.metadata.get("id") == "main" for e in elems)

    def test_extract_form(self):
        src = '<form action="/login" method="post"><input name="user"><input name="pass"></form>'
        result = extract_html_coordinates(src, "login.html")
        forms = [c for c in result.coordinates if c.kind == CoordinateKind.HTML_FORM]
        assert len(forms) == 1
        assert forms[0].metadata["action"] == "/login"
        assert forms[0].metadata["method"] == "post"

    def test_extract_link(self):
        src = '<a href="/about">About</a>'
        result = extract_html_coordinates(src, "nav.html")
        links = [c for c in result.coordinates if c.kind == CoordinateKind.HTML_LINK]
        assert len(links) == 1
        assert links[0].name == "/about"

    def test_extract_script_ref(self):
        src = '<script src="/static/js/app.js"></script>'
        result = extract_html_coordinates(src, "page.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.HTML_SCRIPT]
        assert len(refs) == 1
        assert refs[0].target_name == "/static/js/app.js"

    def test_extract_stylesheet_ref(self):
        src = '<link rel="stylesheet" href="/static/css/main.css">'
        result = extract_html_coordinates(src, "page.html")
        refs = [r for r in result.references if r.reference_type == ReferenceType.HTML_STYLESHEET]
        assert len(refs) == 1
        assert refs[0].target_name == "/static/css/main.css"

    def test_html_ids_function(self):
        src = '<div id="a"></div><span id="b"></span>'
        ids = extract_html_ids(src)
        assert ids == {"a", "b"}

    def test_html_classes_function(self):
        src = '<div class="foo bar"></div><p class="baz"></p>'
        classes = extract_html_classes(src)
        assert classes == {"foo", "bar", "baz"}

    def test_form_actions_function(self):
        src = '<form action="/submit" method="post"><input name="field1"></form>'
        forms = extract_form_actions(src)
        assert len(forms) == 1
        assert forms[0]["action"] == "/submit"
        assert forms[0]["method"] == "post"
        assert "field1" in forms[0]["fields"]

    def test_extract_img_element(self):
        src = '<img src="/static/logo.png">'
        result = extract_html_coordinates(src, "page.html")
        elems = [c for c in result.coordinates if c.kind == CoordinateKind.HTML_ELEMENT]
        assert any("logo.png" in e.name for e in elems)

    def test_multiple_classes(self):
        src = '<div class="alpha beta gamma"></div>'
        classes = extract_html_classes(src)
        assert "alpha" in classes
        assert "beta" in classes
        assert "gamma" in classes


# ===================================================================
# SQL parser tests
# ===================================================================

class TestSQLParser:
    def test_extract_table(self):
        src = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
        result = extract_sql_coordinates(src, "schema.sql")
        tables = [c for c in result.coordinates if c.kind == CoordinateKind.DB_TABLE]
        assert len(tables) == 1
        assert tables[0].name == "users"

    def test_extract_columns(self):
        src = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT);"
        result = extract_sql_coordinates(src, "schema.sql")
        cols = [c for c in result.coordinates if c.kind == CoordinateKind.DB_COLUMN]
        assert len(cols) == 3

    def test_extract_primary_key(self):
        src = "CREATE TABLE items (id INTEGER, name TEXT, PRIMARY KEY (id));"
        result = extract_sql_coordinates(src, "schema.sql")
        constraints = [c for c in result.coordinates if c.kind == CoordinateKind.DB_CONSTRAINT]
        pk = [c for c in constraints if c.metadata.get("constraint_type") == "PRIMARY KEY"]
        assert len(pk) == 1

    def test_extract_foreign_key(self):
        src = textwrap.dedent("""\
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        result = extract_sql_coordinates(src, "schema.sql")
        fk_refs = [r for r in result.references if r.reference_type == ReferenceType.FK_REFERENCE]
        assert len(fk_refs) >= 1
        assert fk_refs[0].target_name == "users.id"

    def test_extract_index(self):
        src = "CREATE INDEX idx_users_email ON users(email);"
        result = extract_sql_coordinates(src, "schema.sql")
        indexes = [c for c in result.coordinates if c.kind == CoordinateKind.DB_INDEX]
        assert len(indexes) == 1
        assert indexes[0].name == "idx_users_email"

    def test_extract_unique_constraint(self):
        src = "CREATE TABLE users (id INTEGER, email TEXT, UNIQUE(email));"
        result = extract_sql_coordinates(src, "schema.sql")
        constraints = [c for c in result.coordinates if c.kind == CoordinateKind.DB_CONSTRAINT]
        uniq = [c for c in constraints if c.metadata.get("constraint_type") == "UNIQUE"]
        assert len(uniq) == 1

    def test_tables_function(self):
        src = "CREATE TABLE t1 (a INT, b TEXT); CREATE TABLE t2 (x REAL);"
        tables = extract_tables(src)
        assert len(tables) == 2
        assert tables[0]["name"] == "t1"
        assert len(tables[0]["columns"]) == 2

    def test_foreign_keys_function(self):
        src = textwrap.dedent("""\
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                author_id INTEGER REFERENCES users(id)
            );
        """)
        fks = extract_foreign_keys(src)
        assert len(fks) == 1
        assert fks[0]["from_table"] == "posts"
        assert fks[0]["to_table"] == "users"

    def test_multiple_tables(self):
        src = textwrap.dedent("""\
            CREATE TABLE a (id INT);
            CREATE TABLE b (id INT);
            CREATE TABLE c (id INT);
        """)
        tables = extract_tables(src)
        assert len(tables) == 3

    def test_not_null_column(self):
        src = "CREATE TABLE t (name TEXT NOT NULL);"
        tables = extract_tables(src)
        assert tables[0]["columns"][0]["nullable"] is False


# ===================================================================
# Cross-reference and integration tests
# ===================================================================

class TestIntegration:
    def test_resolve_cross_refs_template_to_python(self):
        py_result = ParseResult(
            file_path="app.py", language=Language.PYTHON,
            references=[
                ParsedReference(
                    source_id="s1", target_name="index.html",
                    reference_type=ReferenceType.RENDERS_TEMPLATE,
                    file_path="app.py", line_number=5,
                ),
            ],
        )
        html_result = ParseResult(
            file_path="templates/index.html", language=Language.JINJA2,
            coordinates=[
                ParsedCoordinate(
                    id="h1", kind=CoordinateKind.TEMPLATE_BLOCK, name="content",
                    file_path="templates/index.html", line_number=1, end_line=10,
                    language=Language.JINJA2,
                ),
            ],
        )
        resolved = resolve_cross_references([py_result, html_result])
        assert len(resolved) == 1
        assert resolved[0].target_name == "index.html"

    def test_resolve_css_html_refs(self):
        js_result = ParseResult(
            file_path="app.js", language=Language.JAVASCRIPT,
            references=[
                ParsedReference(
                    source_id="j1", target_name="active",
                    reference_type=ReferenceType.CLASS_MANIPULATION,
                    file_path="app.js", line_number=3,
                ),
            ],
        )
        css_result = ParseResult(
            file_path="style.css", language=Language.CSS,
            coordinates=[
                ParsedCoordinate(
                    id="css1", kind=CoordinateKind.CSS_RULE, name=".active",
                    file_path="style.css", line_number=1, end_line=1,
                    language=Language.CSS,
                ),
            ],
        )
        resolved = resolve_cross_references([js_result, css_result])
        assert len(resolved) == 1

    def test_project_scanner_with_temp_dir(self):
        tmpdir = tempfile.mkdtemp()
        try:
            # Write a minimal Flask app
            with open(os.path.join(tmpdir, "app.py"), "w") as f:
                f.write(textwrap.dedent("""\
                    from flask import Flask, render_template
                    app = Flask(__name__)

                    @app.route('/')
                    def index():
                        return render_template('index.html')
                """))
            tpl_dir = os.path.join(tmpdir, "templates")
            os.makedirs(tpl_dir)
            with open(os.path.join(tpl_dir, "index.html"), "w") as f:
                f.write("{% block content %}{{ greeting }}{% endblock %}")

            static_dir = os.path.join(tmpdir, "static")
            os.makedirs(static_dir)
            with open(os.path.join(static_dir, "style.css"), "w") as f:
                f.write(".container { width: 100%; }")

            result = scan_project(tmpdir)
            assert len(result.files) >= 2
            assert len(result.all_coordinates) >= 2
        finally:
            shutil.rmtree(tmpdir)

    def test_build_web_site_from_project(self):
        ppr = ProjectParseResult(
            files=[],
            all_coordinates=[
                ParsedCoordinate(
                    id="c1", kind=CoordinateKind.ROUTE_HANDLER, name="index",
                    file_path="app.py", line_number=4, end_line=6,
                    language=Language.PYTHON,
                ),
            ],
            all_references=[
                ParsedReference(
                    source_id="r1", target_name="index.html",
                    reference_type=ReferenceType.RENDERS_TEMPLATE,
                    file_path="app.py", line_number=5,
                ),
            ],
            cross_language_refs=[
                ParsedReference(
                    source_id="r1", target_name="index.html",
                    reference_type=ReferenceType.RENDERS_TEMPLATE,
                    file_path="app.py", line_number=5,
                ),
            ],
            errors=[],
        )
        site = build_web_site_from_project(ppr)
        assert len(site["coordinates"]) == 1
        assert len(site["morphisms"]) == 1
        assert len(site["cross_language_morphisms"]) == 1
        assert site["error_count"] == 0

    def test_parser_pipeline(self):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "app.py"), "w") as f:
                f.write(textwrap.dedent("""\
                    from flask import Flask
                    app = Flask(__name__)

                    @app.route('/hello')
                    def hello():
                        return 'Hello'
                """))

            pipeline = ParserPipeline(tmpdir)
            site = pipeline.run()
            assert "coordinates" in site
            assert "morphisms" in site
            assert site["file_count"] >= 1
        finally:
            shutil.rmtree(tmpdir)

    def test_coordinate_to_web_coordinate(self):
        coord = ParsedCoordinate(
            id="c1", kind=CoordinateKind.JS_FUNCTION, name="init",
            file_path="app.js", line_number=1, end_line=5,
            language=Language.JAVASCRIPT, metadata={"async": True},
        )
        d = coordinate_to_web_coordinate(coord)
        assert d["id"] == "c1"
        assert d["kind"] == "js_function"
        assert d["language"] == "javascript"

    def test_reference_to_morphism(self):
        ref = ParsedReference(
            source_id="r1", target_name="index.html",
            reference_type=ReferenceType.RENDERS_TEMPLATE,
            file_path="app.py", line_number=10,
        )
        d = reference_to_morphism(ref)
        assert d["morphism_type"] == "renders_template"
        assert d["source_id"] == "r1"

    def test_detect_flask_structure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "app.py"), "w") as f:
                f.write("app = Flask(__name__)\n")
            os.makedirs(os.path.join(tmpdir, "templates"))
            os.makedirs(os.path.join(tmpdir, "static"))
            structure = detect_flask_structure(tmpdir)
            assert structure["app_file"] is not None
            assert structure["template_dir"] is not None
            assert structure["static_dir"] is not None
        finally:
            shutil.rmtree(tmpdir)


# ===================================================================
# Additional edge-case tests to reach 90+
# ===================================================================

class TestEdgeCases:
    def test_empty_python_source(self):
        result = extract_flask_coordinates("", "empty.py")
        assert result.coordinates == []
        assert result.errors == []

    def test_empty_template(self):
        result = extract_template_coordinates("", "empty.html")
        assert result.coordinates == []

    def test_empty_js(self):
        result = extract_js_coordinates("", "empty.js")
        assert result.coordinates == []

    def test_empty_css(self):
        result = extract_css_coordinates("", "empty.css")
        assert result.coordinates == []

    def test_empty_html(self):
        result = extract_html_coordinates("", "empty.html")
        assert result.coordinates == []

    def test_empty_sql(self):
        result = extract_sql_coordinates("", "empty.sql")
        assert result.coordinates == []

    def test_coord_ids_are_deterministic(self):
        src = "@app.route('/x')\ndef x(): pass\n"
        r1 = extract_flask_coordinates(src, "a.py")
        r2 = extract_flask_coordinates(src, "a.py")
        assert r1.coordinates[0].id == r2.coordinates[0].id

    def test_coord_ids_differ_by_file(self):
        src = "@app.route('/x')\ndef x(): pass\n"
        r1 = extract_flask_coordinates(src, "a.py")
        r2 = extract_flask_coordinates(src, "b.py")
        assert r1.coordinates[0].id != r2.coordinates[0].id

    def test_css_comment_stripping(self):
        src = "/* comment */ .real { color: red; }"
        result = extract_css_coordinates(src, "s.css")
        rules = [c for c in result.coordinates if c.kind == CoordinateKind.CSS_RULE]
        assert any(".real" in r.name for r in rules)

    def test_sql_if_not_exists(self):
        src = "CREATE TABLE IF NOT EXISTS t (id INT);"
        tables = extract_tables(src)
        assert len(tables) == 1
        assert tables[0]["name"] == "t"

    def test_js_async_function(self):
        src = "async function fetchData() { await fetch('/'); }"
        result = extract_js_coordinates(src, "app.js")
        fns = [c for c in result.coordinates if c.kind == CoordinateKind.JS_FUNCTION]
        assert any(f.name == "fetchData" for f in fns)

    def test_html_form_fields_collected(self):
        src = textwrap.dedent("""\
            <form action="/register" method="post">
                <input name="username">
                <input name="email">
                <select name="role"><option>Admin</option></select>
                <textarea name="bio"></textarea>
            </form>
        """)
        forms = extract_form_actions(src)
        assert len(forms) == 1
        assert set(forms[0]["fields"]) == {"username", "email", "role", "bio"}

    def test_jinja2_whitespace_control(self):
        src = "{%- block sidebar -%}content{%- endblock -%}"
        blocks = extract_template_blocks(src)
        assert "sidebar" in blocks

    def test_css_specificity_with_attribute(self):
        spec = compute_specificity("input[type='text']")
        assert spec[1] >= 1  # attribute counts as b

    def test_unreferenced_classes(self):
        css_coords = [
            ParsedCoordinate(
                id="c1", kind=CoordinateKind.CSS_RULE, name=".orphan",
                file_path="s.css", line_number=1, end_line=1, language=Language.CSS,
            ),
        ]
        html_coords = [
            ParsedCoordinate(
                id="h1", kind=CoordinateKind.HTML_ELEMENT, name="div.used",
                file_path="p.html", line_number=1, end_line=1, language=Language.HTML,
                metadata={"classes": ["used"]},
            ),
        ]
        analyzer = CSSCascadeAnalyzer()
        unreferenced = analyzer.find_unreferenced_classes(css_coords, html_coords)
        assert "orphan" in unreferenced
