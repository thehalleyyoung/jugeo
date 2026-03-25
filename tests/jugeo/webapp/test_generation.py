"""Comprehensive tests for the Flask app generation module."""
from __future__ import annotations

import ast
import os
import shutil
import tempfile

import pytest

from jugeo.webapp.generation.models import (
    AppSpec, RouteSpec, ModelSpec, ColumnSpec, ColumnType,
    ResponseType, FormSpec, FormFieldSpec, FormFieldType,
    TemplateSpec, StaticFileSpec, BlueprintSpec, ConfigSpec,
    TestSpec, TestCaseSpec, GenerationResult,
)
from jugeo.webapp.generation.flask_generator import FlaskAppGenerator
from jugeo.webapp.generation.route_generator import RouteCodeGenerator, URLPatternGenerator
from jugeo.webapp.generation.model_generator import ModelCodeGenerator, SchemaGenerator
from jugeo.webapp.generation.template_generator import TemplateCodeGenerator, ComponentLibrary
from jugeo.webapp.generation.static_generator import CSSGenerator, JSGenerator
from jugeo.webapp.generation.test_generator import TestCodeGenerator
from jugeo.webapp.generation.blueprint_architect import BlueprintArchitect, ArchitectureValidator
from jugeo.webapp.generation.scaffold import AppScaffolder, SpecValidator
from jugeo.webapp.generation.config_generator import ConfigCodeGenerator, DockerfileGenerator
from jugeo.webapp.generation.migration_generator import MigrationCodeGenerator
from jugeo.webapp.generation.app_runner import AppRunner, SyntaxChecker
from jugeo.webapp.generation.verification_bridge import VerificationBridge
from jugeo.webapp.generation.templates.snippets import SNIPPETS
from jugeo.webapp.generation.blueprints.patterns import (
    PATTERNS, BlueprintPattern, auth_pattern, crud_pattern, api_pattern,
    dashboard_pattern, search_pattern, file_upload_pattern, admin_pattern,
)


# =====================================================================
# Models
# =====================================================================

class TestModels:
    """Tests for model dataclasses."""

    def test_app_spec_defaults(self):
        spec = AppSpec(name="test")
        assert spec.port == 5000
        assert spec.routes == []
        assert spec.models == []

    def test_app_spec_to_dict_from_dict(self):
        spec = AppSpec(name="myapp", port=8080, description="test app")
        d = spec.to_dict()
        assert d["name"] == "myapp"
        assert d["port"] == 8080
        spec2 = AppSpec.from_dict(d)
        assert spec2.name == "myapp"
        assert spec2.port == 8080

    def test_route_spec_defaults(self):
        route = RouteSpec(url="/")
        assert route.methods == ["GET"]
        assert route.response_type == ResponseType.TEMPLATE
        assert route.auth_required is False

    def test_route_spec_to_dict(self):
        route = RouteSpec(url="/api/users", methods=["GET", "POST"], response_type=ResponseType.JSON)
        d = route.to_dict()
        assert d["url"] == "/api/users"
        assert d["response_type"] == "json"

    def test_route_spec_from_dict(self):
        d = {"url": "/test", "methods": ["POST"], "response_type": "json"}
        route = RouteSpec.from_dict(d)
        assert route.url == "/test"
        assert route.response_type == ResponseType.JSON

    def test_model_spec_auto_table_name(self):
        model = ModelSpec(name="User")
        assert model.table_name == "users"

    def test_model_spec_custom_table_name(self):
        model = ModelSpec(name="User", table_name="app_users")
        assert model.table_name == "app_users"

    def test_model_spec_to_dict_from_dict(self):
        model = ModelSpec(name="Post", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])
        d = model.to_dict()
        assert d["name"] == "Post"
        m2 = ModelSpec.from_dict(d)
        assert m2.name == "Post"
        assert len(m2.columns) == 1

    def test_column_spec_to_dict(self):
        col = ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True)
        d = col.to_dict()
        assert d["name"] == "id"
        assert d["primary_key"] is True

    def test_column_spec_from_dict(self):
        d = {"name": "email", "type": "string", "unique": True}
        col = ColumnSpec.from_dict(d)
        assert col.name == "email"
        assert col.type == ColumnType.STRING
        assert col.unique is True

    def test_config_spec_defaults(self):
        config = ConfigSpec()
        assert config.database_url == "sqlite:///app.db"
        assert config.debug is False

    def test_config_spec_to_dict(self):
        config = ConfigSpec(secret_key="abc", debug=True)
        d = config.to_dict()
        assert d["secret_key"] == "abc"
        assert d["debug"] is True

    def test_form_field_spec_auto_label(self):
        field = FormFieldSpec(name="user_name")
        assert field.label == "User Name"

    def test_form_field_spec_custom_label(self):
        field = FormFieldSpec(name="user_name", label="Username")
        assert field.label == "Username"

    def test_form_field_spec_to_dict(self):
        field = FormFieldSpec(name="email", field_type=FormFieldType.EMAIL)
        d = field.to_dict()
        assert d["field_type"] == "email"

    def test_form_spec_to_dict_from_dict(self):
        form = FormSpec(name="login", fields=[
            FormFieldSpec(name="username"),
        ], action_url="/login")
        d = form.to_dict()
        assert d["name"] == "login"
        f2 = FormSpec.from_dict(d)
        assert f2.name == "login"
        assert len(f2.fields) == 1

    def test_blueprint_spec(self):
        bp = BlueprintSpec(name="auth", url_prefix="/auth")
        assert bp.routes == []

    def test_blueprint_spec_to_dict(self):
        bp = BlueprintSpec(name="api", url_prefix="/api", routes=[
            RouteSpec(url="/users", handler_name="users"),
        ])
        d = bp.to_dict()
        assert d["name"] == "api"
        assert len(d["routes"]) == 1

    def test_generation_result(self):
        result = GenerationResult(output_dir="/some/path")
        assert result.files_created == []
        assert result.warnings == []

    def test_generation_result_to_dict(self):
        result = GenerationResult(output_dir="/some/path", warnings=["w1"])
        d = result.to_dict()
        assert d["output_dir"] == "/some/path"
        assert d["warnings"] == ["w1"]

    def test_template_spec_to_dict(self):
        t = TemplateSpec(name="index.html", extends="base.html")
        d = t.to_dict()
        assert d["name"] == "index.html"

    def test_static_file_spec_to_dict(self):
        s = StaticFileSpec(path="css/custom.css", content="body{}")
        d = s.to_dict()
        assert d["path"] == "css/custom.css"

    def test_test_case_spec_to_dict(self):
        tc = TestCaseSpec(name="test_index", url="/", expected_status=200)
        d = tc.to_dict()
        assert d["expected_status"] == 200

    def test_test_spec_to_dict(self):
        ts = TestSpec(name="suite", test_cases=[TestCaseSpec(name="t1")])
        d = ts.to_dict()
        assert d["name"] == "suite"
        assert len(d["test_cases"]) == 1

    def test_response_type_enum_values(self):
        assert ResponseType.TEMPLATE == "template"
        assert ResponseType.JSON == "json"
        assert ResponseType.REDIRECT == "redirect"
        assert ResponseType.FORM == "form"

    def test_column_type_enum_values(self):
        assert ColumnType.STRING == "string"
        assert ColumnType.INTEGER == "integer"
        assert ColumnType.BOOLEAN == "boolean"
        assert ColumnType.TEXT == "text"

    def test_form_field_type_enum_values(self):
        assert FormFieldType.TEXT == "text"
        assert FormFieldType.EMAIL == "email"
        assert FormFieldType.PASSWORD == "password"
        assert FormFieldType.TEXTAREA == "textarea"

    def test_app_spec_with_nested_from_dict(self):
        d = {
            "name": "nested",
            "routes": [{"url": "/", "response_type": "json"}],
            "config": {"debug": True, "secret_key": "s"},
        }
        spec = AppSpec.from_dict(d)
        assert spec.routes[0].response_type == ResponseType.JSON
        assert spec.config.debug is True


# =====================================================================
# Route generator
# =====================================================================

class TestRouteGenerator:
    def setup_method(self):
        self.gen = RouteCodeGenerator()

    def test_generate_template_route(self):
        route = RouteSpec(url="/", handler_name="index", template="index.html",
                          response_type=ResponseType.TEMPLATE)
        code = self.gen.generate_route(route)
        assert "@app.route('/'," in code or "@app.route('/')" in code
        assert "render_template" in code
        assert "index.html" in code

    def test_generate_api_route(self):
        route = RouteSpec(url="/api/users", handler_name="api_users",
                          response_type=ResponseType.JSON, methods=["GET"])
        code = self.gen.generate_route(route)
        assert "jsonify" in code

    def test_generate_form_route(self):
        route = RouteSpec(url="/login", handler_name="login",
                          response_type=ResponseType.FORM, methods=["GET", "POST"])
        code = self.gen.generate_route(route)
        assert "request.method" in code
        assert "POST" in code

    def test_generate_redirect_route(self):
        route = RouteSpec(url="/old", handler_name="old_page",
                          response_type=ResponseType.REDIRECT)
        code = self.gen.generate_route(route)
        assert "redirect" in code

    def test_generate_routes_module(self):
        routes = [
            RouteSpec(url="/", handler_name="index", template="index.html"),
            RouteSpec(url="/api/data", handler_name="api_data", response_type=ResponseType.JSON),
        ]
        module = self.gen.generate_routes_module(routes)
        assert "from flask import" in module
        assert "def index" in module
        assert "def api_data" in module

    def test_generate_auth_decorator(self):
        route = RouteSpec(url="/admin", handler_name="admin", auth_required=True)
        code = self.gen.generate_route(route)
        assert "login_required" in code

    def test_generate_error_handlers(self):
        handlers = self.gen._generate_error_handlers()
        assert "404" in handlers
        assert "500" in handlers

    def test_url_pattern_generator(self):
        gen = URLPatternGenerator()
        params = [{"name": "user_id", "type": "int"}]
        pattern = gen.flask_url_pattern(params)
        assert "<int:user_id>" in pattern

    def test_url_pattern_string_type(self):
        gen = URLPatternGenerator()
        params = [{"name": "slug", "type": "string"}]
        pattern = gen.flask_url_pattern(params)
        assert "<slug>" in pattern

    def test_url_for_call(self):
        gen = URLPatternGenerator()
        call = gen.url_for_call("user_detail", {"user_id": "1"})
        assert "url_for" in call
        assert "user_detail" in call

    def test_url_for_call_no_params(self):
        gen = URLPatternGenerator()
        call = gen.url_for_call("index", {})
        assert call == "url_for('index')"

    def test_generated_route_is_valid_python(self):
        route = RouteSpec(url="/test", handler_name="test_route",
                          response_type=ResponseType.TEMPLATE, template="test.html")
        code = self.gen.generate_route(route)
        checker = SyntaxChecker()
        full = (
            "from flask import render_template, redirect, url_for, jsonify, request, flash, session\n"
            "app = None\n" + code
        )
        errors = checker.check_python(full)
        assert errors == [], f"Syntax errors: {errors}"

    def test_generated_api_route_valid_python(self):
        route = RouteSpec(url="/api/test", handler_name="api_test",
                          response_type=ResponseType.JSON, methods=["GET", "POST"])
        code = self.gen.generate_route(route)
        full = (
            "from flask import render_template, redirect, url_for, jsonify, request, flash, session\n"
            "app = None\n" + code
        )
        errors = SyntaxChecker().check_python(full)
        assert errors == [], f"Syntax errors: {errors}"

    def test_generated_form_route_valid_python(self):
        route = RouteSpec(url="/form", handler_name="my_form",
                          response_type=ResponseType.FORM, methods=["GET", "POST"])
        code = self.gen.generate_route(route)
        full = (
            "from flask import render_template, redirect, url_for, jsonify, request, flash, session\n"
            "app = None\n" + code
        )
        errors = SyntaxChecker().check_python(full)
        assert errors == [], f"Syntax errors: {errors}"

    def test_handler_name_from_url(self):
        assert RouteCodeGenerator._handler_name_from_url("/") == "index"
        assert RouteCodeGenerator._handler_name_from_url("/users") == "users"
        assert RouteCodeGenerator._handler_name_from_url("/api/data") == "api_data"

    def test_routes_module_has_login_required(self):
        module = self.gen.generate_routes_module([])
        assert "login_required" in module
        assert "wraps" in module

    def test_routes_module_has_register_routes(self):
        module = self.gen.generate_routes_module([
            RouteSpec(url="/", handler_name="index"),
        ])
        assert "register_routes" in module


# =====================================================================
# Model generator
# =====================================================================

class TestModelGenerator:
    def setup_method(self):
        self.gen = ModelCodeGenerator()
        self.schema_gen = SchemaGenerator()

    def test_generate_simple_model(self):
        model = ModelSpec(
            name="User",
            columns=[
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                ColumnSpec(name="username", type=ColumnType.STRING, nullable=False, unique=True),
            ],
        )
        code = self.gen.generate_model(model)
        assert "class User" in code
        assert "username" in code
        assert "id" in code

    def test_generate_models_module(self):
        models = [
            ModelSpec(name="User", columns=[
                ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
                ColumnSpec(name="email", type=ColumnType.STRING),
            ]),
        ]
        module = self.gen.generate_models_module(models)
        assert "from flask_sqlalchemy" in module
        assert "class User" in module

    def test_column_definition_primary_key(self):
        col = ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True)
        defn = self.gen._column_definition(col)
        assert "primary_key=True" in defn

    def test_column_definition_foreign_key(self):
        col = ColumnSpec(name="user_id", type=ColumnType.INTEGER, foreign_key="users.id")
        defn = self.gen._column_definition(col)
        assert "ForeignKey" in defn

    def test_column_definition_unique(self):
        col = ColumnSpec(name="email", type=ColumnType.STRING, unique=True)
        defn = self.gen._column_definition(col)
        assert "unique=True" in defn

    def test_column_definition_nullable_false(self):
        col = ColumnSpec(name="name", type=ColumnType.STRING, nullable=False)
        defn = self.gen._column_definition(col)
        assert "nullable=False" in defn

    def test_type_mapping(self):
        assert "Integer" in self.gen._type_mapping("integer")
        assert "String" in self.gen._type_mapping("string")
        assert "Text" in self.gen._type_mapping("text")
        assert "Boolean" in self.gen._type_mapping("boolean")
        assert "Float" in self.gen._type_mapping("float")

    def test_generate_repr(self):
        model = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])
        repr_code = self.gen._generate_repr(model)
        assert "__repr__" in repr_code
        assert "User" in repr_code

    def test_generate_to_dict(self):
        model = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="username", type=ColumnType.STRING),
        ])
        to_dict_code = self.gen._generate_to_dict(model)
        assert "to_dict" in to_dict_code
        assert "id" in to_dict_code
        assert "username" in to_dict_code

    def test_generate_schema_sql(self):
        models = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="username", type=ColumnType.STRING),
        ])]
        sql = self.schema_gen.generate_schema_sql(models)
        assert "CREATE TABLE" in sql
        assert "users" in sql

    def test_generate_init_db(self):
        models = [ModelSpec(name="Post", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="title", type=ColumnType.STRING),
        ])]
        code = self.schema_gen.generate_init_db(models)
        assert "sqlite3" in code
        assert "CREATE TABLE" in code

    def test_generated_model_is_valid_python(self):
        model = ModelSpec(name="Article", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="title", type=ColumnType.STRING, nullable=False),
            ColumnSpec(name="body", type=ColumnType.TEXT),
        ])
        code = self.gen.generate_model(model)
        full = "from flask_sqlalchemy import SQLAlchemy\ndb = SQLAlchemy()\n" + code
        errors = SyntaxChecker().check_python(full)
        assert errors == [], f"Syntax errors: {errors}"

    def test_generated_init_db_valid_python(self):
        models = [ModelSpec(name="Item", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="name", type=ColumnType.STRING),
        ])]
        code = self.schema_gen.generate_init_db(models)
        errors = SyntaxChecker().check_python(code)
        assert errors == [], f"init_db syntax errors: {errors}"

    def test_auto_id_column_when_no_pk(self):
        model = ModelSpec(name="Tag", columns=[
            ColumnSpec(name="label", type=ColumnType.STRING),
        ])
        code = self.gen.generate_model(model)
        assert "id = db.Column(db.Integer, primary_key=True)" in code

    def test_schema_sql_auto_id(self):
        model = ModelSpec(name="Tag", columns=[
            ColumnSpec(name="label", type=ColumnType.STRING),
        ])
        sql = self.schema_gen.generate_schema_sql([model])
        assert "id INTEGER PRIMARY KEY" in sql


# =====================================================================
# Template generator
# =====================================================================

class TestTemplateGenerator:
    def setup_method(self):
        self.gen = TemplateCodeGenerator()
        self.lib = ComponentLibrary()

    def test_generate_base_template(self):
        tmpl = self.gen.generate_base_template("My App", [])
        assert "<!DOCTYPE html>" in tmpl
        assert "{% block content %}" in tmpl
        assert "{% block title %}" in tmpl
        assert "base.css" in tmpl

    def test_generate_base_template_with_nav(self):
        nav = [{"url": "/", "label": "Home"}, {"url": "/about", "label": "About"}]
        tmpl = self.gen.generate_base_template("App", nav)
        assert "Home" in tmpl

    def test_generate_list_template(self):
        tmpl = self.gen.generate_list_template("User", ["id", "username", "email"])
        assert "{% extends" in tmpl
        assert "{% block content %}" in tmpl
        assert "User" in tmpl

    def test_generate_detail_template(self):
        tmpl = self.gen.generate_detail_template("Post", ["id", "title", "body"])
        assert "{% block content %}" in tmpl
        assert "Post" in tmpl

    def test_generate_form_template(self):
        form = FormSpec(name="LoginForm", fields=[
            FormFieldSpec(name="username", field_type=FormFieldType.TEXT, required=True),
            FormFieldSpec(name="password", field_type=FormFieldType.PASSWORD, required=True),
        ], action_url="/login", method="POST")
        tmpl = self.gen.generate_form_template(form)
        assert "<form" in tmpl
        assert "username" in tmpl
        assert "password" in tmpl

    def test_generate_dashboard_template(self):
        widgets = [{"title": "Users", "value": "42"}, {"title": "Posts", "value": "100"}]
        tmpl = self.gen.generate_dashboard_template(widgets)
        assert "{% block content %}" in tmpl
        assert "Users" in tmpl

    def test_generate_error_404_template(self):
        tmpl = self.gen.generate_error_template(404)
        assert "404" in tmpl

    def test_generate_error_500_template(self):
        tmpl = self.gen.generate_error_template(500)
        assert "500" in tmpl

    def test_generate_login_template(self):
        tmpl = self.gen.generate_login_template()
        assert "{% block content %}" in tmpl
        assert "password" in tmpl.lower()

    def test_generate_template_from_spec(self):
        spec = TemplateSpec(name="about.html", blocks={"content": "<p>About us</p>"})
        tmpl = self.gen.generate_template(spec)
        assert "About us" in tmpl
        assert "{% block content %}" in tmpl

    def test_generate_template_default_block(self):
        spec = TemplateSpec(name="empty.html")
        tmpl = self.gen.generate_template(spec)
        assert "{% block content %}" in tmpl

    def test_component_navbar(self):
        html = self.lib.navbar("MyApp", [{"url": "/", "label": "Home"}])
        assert "MyApp" in html
        assert "Home" in html

    def test_component_card(self):
        html = self.lib.card("Title", "Body content")
        assert "Title" in html
        assert "Body content" in html

    def test_component_card_with_footer(self):
        html = self.lib.card("T", "B", "Footer text")
        assert "Footer text" in html

    def test_component_table(self):
        html = self.lib.table(["Name", "Email"], [["Alice", "alice@example.com"]])
        assert "Name" in html
        assert "Alice" in html

    def test_component_alert(self):
        html = self.lib.alert("Success!", "success")
        assert "Success!" in html
        assert "alert-success" in html

    def test_component_modal(self):
        html = self.lib.modal("myModal", "Dialog", "Content here")
        assert "myModal" in html
        assert "Dialog" in html

    def test_component_pagination(self):
        html = self.lib.pagination(2, 10)
        assert "page=2" in html or "2" in html

    def test_component_form_field_text(self):
        field = FormFieldSpec(name="email", field_type=FormFieldType.EMAIL, label="Email Address")
        html = self.lib.form_field(field)
        assert "email" in html
        assert "Email" in html

    def test_component_form_field_textarea(self):
        field = FormFieldSpec(name="bio", field_type=FormFieldType.TEXTAREA)
        html = self.lib.form_field(field)
        assert "textarea" in html

    def test_component_form_field_checkbox(self):
        field = FormFieldSpec(name="agree", field_type=FormFieldType.CHECKBOX)
        html = self.lib.form_field(field)
        assert "checkbox" in html

    def test_component_form_field_select(self):
        field = FormFieldSpec(name="role", field_type=FormFieldType.SELECT, choices=["admin", "user"])
        html = self.lib.form_field(field)
        assert "select" in html
        assert "admin" in html


# =====================================================================
# Static generator
# =====================================================================

class TestStaticGenerator:
    def setup_method(self):
        self.css_gen = CSSGenerator()
        self.js_gen = JSGenerator()

    def test_generate_base_css(self):
        css = self.css_gen.generate_base_css()
        assert len(css) > 500
        assert "--color" in css

    def test_base_css_has_responsive_breakpoints(self):
        css = self.css_gen.generate_base_css()
        assert "576px" in css or "768px" in css or "992px" in css

    def test_base_css_has_dark_mode(self):
        css = self.css_gen.generate_base_css()
        assert "prefers-color-scheme" in css

    def test_base_css_has_print_styles(self):
        css = self.css_gen.generate_base_css()
        assert "@media print" in css

    def test_generate_custom_css(self):
        spec = AppSpec(name="myapp")
        css = self.css_gen.generate_custom_css(spec)
        assert isinstance(css, str)
        assert "myapp" in css

    def test_generate_base_js(self):
        js = self.js_gen.generate_base_js()
        assert len(js) > 200
        assert "DOMContentLoaded" in js

    def test_base_js_has_fetch_wrapper(self):
        js = self.js_gen.generate_base_js()
        assert "fetch" in js

    def test_base_js_has_flash_dismiss(self):
        js = self.js_gen.generate_base_js()
        assert "alert" in js.lower()

    def test_base_js_has_modal(self):
        js = self.js_gen.generate_base_js()
        assert "modal" in js.lower()

    def test_generate_api_client(self):
        routes = [RouteSpec(url="/api/users", handler_name="api_users",
                            response_type=ResponseType.JSON)]
        js = self.js_gen.generate_api_client(routes)
        assert isinstance(js, str)
        assert "api_users" in js

    def test_generate_form_validation(self):
        forms = [FormSpec(name="login")]
        js = self.js_gen.generate_form_validation(forms)
        assert "login" in js

    def test_css_balanced_braces(self):
        css = self.css_gen.generate_base_css()
        errors = SyntaxChecker().check_css(css)
        assert errors == [], f"CSS errors: {errors}"

    def test_js_balanced_braces(self):
        js = self.js_gen.generate_base_js()
        errors = SyntaxChecker().check_js(js)
        assert errors == [], f"JS errors: {errors}"


# =====================================================================
# Test generator
# =====================================================================

class TestTestGenerator:
    def setup_method(self):
        self.gen = TestCodeGenerator()

    def test_generate_test_suite(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/", handler_name="index"),
        ])
        suite = self.gen.generate_test_suite(spec)
        assert "import pytest" in suite
        assert "client" in suite

    def test_test_suite_has_route_tests(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/about", handler_name="about"),
        ])
        suite = self.gen.generate_test_suite(spec)
        assert "test_about" in suite

    def test_test_suite_valid_python(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/", handler_name="index"),
            RouteSpec(url="/api/data", handler_name="api_data", response_type=ResponseType.JSON),
        ])
        suite = self.gen.generate_test_suite(spec)
        errors = SyntaxChecker().check_python(suite)
        assert errors == [], f"Test suite syntax errors: {errors}"


# =====================================================================
# Blueprint architect
# =====================================================================

class TestBlueprintArchitect:
    def setup_method(self):
        self.architect = BlueprintArchitect()
        self.validator = ArchitectureValidator()

    def test_detect_auth_blueprint(self):
        routes = [
            RouteSpec(url="/login", handler_name="login"),
            RouteSpec(url="/logout", handler_name="logout"),
            RouteSpec(url="/register", handler_name="register"),
            RouteSpec(url="/", handler_name="index"),
        ]
        bp = self.architect._detect_auth_blueprint(routes)
        assert bp is not None
        assert any(r.url in ["/login", "/logout", "/register"] for r in bp.routes)

    def test_detect_api_blueprint(self):
        routes = [
            RouteSpec(url="/api/users", handler_name="api_users", response_type=ResponseType.JSON),
            RouteSpec(url="/api/posts", handler_name="api_posts", response_type=ResponseType.JSON),
            RouteSpec(url="/", handler_name="index"),
        ]
        bp = self.architect._detect_api_blueprint(routes)
        assert bp is not None
        assert len(bp.routes) == 2

    def test_detect_admin_blueprint(self):
        routes = [
            RouteSpec(url="/admin/dashboard", handler_name="admin_dash"),
            RouteSpec(url="/", handler_name="index"),
        ]
        bp = self.architect._detect_admin_blueprint(routes)
        assert bp is not None

    def test_design_blueprints(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/", handler_name="index"),
            RouteSpec(url="/api/data", handler_name="api_data", response_type=ResponseType.JSON),
        ])
        blueprints = self.architect.design_blueprints(spec)
        assert isinstance(blueprints, list)
        assert len(blueprints) > 0

    def test_generate_blueprint_module(self):
        bp = BlueprintSpec(name="auth", url_prefix="/auth", routes=[
            RouteSpec(url="/login", handler_name="login", template="auth/login.html"),
        ])
        code = self.architect.generate_blueprint_module(bp)
        assert "Blueprint" in code
        assert "auth" in code

    def test_blueprint_module_valid_python(self):
        bp = BlueprintSpec(name="api", url_prefix="/api", routes=[
            RouteSpec(url="/data", handler_name="api_data", response_type=ResponseType.JSON),
        ])
        code = self.architect.generate_blueprint_module(bp)
        errors = SyntaxChecker().check_python(code)
        assert errors == [], f"Blueprint module syntax errors: {errors}"

    def test_validate_url_uniqueness_no_conflicts(self):
        blueprints = [
            BlueprintSpec(name="main", url_prefix="", routes=[
                RouteSpec(url="/", handler_name="index"),
            ]),
            BlueprintSpec(name="api", url_prefix="/api", routes=[
                RouteSpec(url="/users", handler_name="api_users"),
            ]),
        ]
        errors = self.validator.validate_url_uniqueness(blueprints)
        assert errors == []

    def test_validate_url_uniqueness_with_conflicts(self):
        blueprints = [
            BlueprintSpec(name="main", url_prefix="", routes=[
                RouteSpec(url="/users", handler_name="users"),
            ]),
            BlueprintSpec(name="api", url_prefix="", routes=[
                RouteSpec(url="/users", handler_name="users2"),
            ]),
        ]
        errors = self.validator.validate_url_uniqueness(blueprints)
        assert len(errors) > 0

    def test_validate_isolation(self):
        blueprints = [
            BlueprintSpec(name="a", routes=[
                RouteSpec(url="/x", handler_name="handler_x"),
            ]),
            BlueprintSpec(name="b", routes=[
                RouteSpec(url="/y", handler_name="handler_x"),
            ]),
        ]
        errors = self.validator.validate_blueprint_isolation(blueprints)
        assert len(errors) > 0

    def test_group_by_resource(self):
        routes = [
            RouteSpec(url="/users", handler_name="users_list"),
            RouteSpec(url="/users/1", handler_name="user_detail"),
            RouteSpec(url="/posts", handler_name="posts_list"),
        ]
        groups = self.architect._group_by_resource(routes)
        assert "users" in groups
        assert "posts" in groups


# =====================================================================
# Scaffolder
# =====================================================================

class TestScaffolder:
    def setup_method(self):
        self.scaffolder = AppScaffolder()
        self.validator = SpecValidator()

    def test_scaffold_crud_app(self):
        spec = self.scaffolder.scaffold_crud_app("blog", [
            {"name": "Post", "columns": [
                {"name": "title", "type": "string"},
                {"name": "body", "type": "text"},
            ]},
        ])
        assert spec.name == "blog"
        assert len(spec.routes) > 0
        assert len(spec.models) > 0
        urls = [r.url for r in spec.routes]
        assert any("post" in u.lower() for u in urls)

    def test_scaffold_api_app(self):
        spec = self.scaffolder.scaffold_api_app("myapi", [
            {"name": "user", "fields": [
                {"name": "username", "type": "string"},
                {"name": "email", "type": "string"},
            ]},
        ])
        assert spec.name == "myapi"
        assert any(r.response_type == ResponseType.JSON for r in spec.routes)

    def test_scaffold_dashboard_app(self):
        spec = self.scaffolder.scaffold_dashboard_app("dashboard", [
            {"name": "users", "label": "Total Users"},
        ])
        assert spec.name == "dashboard"
        assert len(spec.routes) > 0

    def test_scaffold_form_app(self):
        spec = self.scaffolder.scaffold_form_app("forms", [{"name": "contact"}])
        assert spec.name == "forms"
        assert any(r.response_type == ResponseType.FORM for r in spec.routes)

    def test_scaffold_from_description_api(self):
        spec = self.scaffolder.scaffold_from_description("myapp", "REST API for managing users")
        assert spec.name == "myapp"
        assert any(r.response_type == ResponseType.JSON for r in spec.routes)

    def test_scaffold_from_description_crud(self):
        spec = self.scaffolder.scaffold_from_description("blog", "blog with posts and comments")
        assert spec.name == "blog"

    def test_scaffold_from_description_dashboard(self):
        spec = self.scaffolder.scaffold_from_description("dash", "admin dashboard")
        assert spec.name == "dash"

    def test_spec_validator_no_errors(self):
        spec = AppSpec(name="test", routes=[RouteSpec(url="/", handler_name="index")])
        errors = self.validator.validate(spec)
        assert isinstance(errors, list)

    def test_spec_validator_empty_name(self):
        spec = AppSpec(name="")
        errors = self.validator.validate(spec)
        assert any("name" in e.lower() for e in errors)

    def test_spec_validator_duplicate_handlers(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/a", handler_name="handler"),
            RouteSpec(url="/b", handler_name="handler"),
        ])
        errors = self.validator.validate(spec)
        assert any("duplicate" in e.lower() for e in errors)

    def test_crud_routes_have_correct_methods(self):
        spec = self.scaffolder.scaffold_crud_app("todo", [
            {"name": "Todo", "columns": [{"name": "title", "type": "string"}]},
        ])
        assert any("POST" in r.methods for r in spec.routes)

    def test_crud_has_index_route(self):
        spec = self.scaffolder.scaffold_crud_app("app", [
            {"name": "Item", "columns": [{"name": "name", "type": "string"}]},
        ])
        assert any(r.url == "/" for r in spec.routes)


# =====================================================================
# Config generator
# =====================================================================

class TestConfigGenerator:
    def setup_method(self):
        self.gen = ConfigCodeGenerator()
        self.docker_gen = DockerfileGenerator()

    def test_generate_config_module(self):
        config = ConfigSpec(secret_key="test", database_url="sqlite:///test.db")
        code = self.gen.generate_config_module(config)
        assert "class" in code
        assert "SECRET_KEY" in code

    def test_generate_env_template(self):
        config = ConfigSpec()
        env = self.gen.generate_env_template(config)
        assert "SECRET_KEY" in env

    def test_generate_secret_key(self):
        key = self.gen.generate_secret_key()
        assert len(key) > 16
        key2 = self.gen.generate_secret_key()
        assert key != key2

    def test_generate_database_config(self):
        models = [ModelSpec(name="User")]
        code = self.gen.generate_database_config(models)
        assert "database" in code.lower() or "sqlite" in code.lower()

    def test_generate_dockerfile(self):
        spec = AppSpec(name="myapp", port=5000)
        dockerfile = self.docker_gen.generate_dockerfile(spec)
        assert "FROM python" in dockerfile
        assert "EXPOSE" in dockerfile

    def test_generate_docker_compose(self):
        spec = AppSpec(name="myapp", port=5000)
        compose = self.docker_gen.generate_docker_compose(spec)
        assert "services" in compose
        assert "5000" in compose

    def test_config_module_is_valid_python(self):
        config = ConfigSpec(secret_key="abc123", database_url="sqlite:///app.db", debug=True)
        code = self.gen.generate_config_module(config)
        errors = SyntaxChecker().check_python(code)
        assert errors == [], f"Config syntax errors: {errors}"

    def test_config_has_testing_class(self):
        code = self.gen.generate_config_module(ConfigSpec())
        assert "TestingConfig" in code

    def test_config_has_production_class(self):
        code = self.gen.generate_config_module(ConfigSpec())
        assert "ProductionConfig" in code


# =====================================================================
# Migration generator
# =====================================================================

class TestMigrationGenerator:
    def setup_method(self):
        self.gen = MigrationCodeGenerator()

    def test_generate_init_migration(self):
        models = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="email", type=ColumnType.STRING),
        ])]
        sql = self.gen.generate_init_migration(models)
        assert "CREATE TABLE" in sql
        assert "id" in sql

    def test_generate_seed_data(self):
        models = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="username", type=ColumnType.STRING),
        ])]
        sql = self.gen.generate_seed_data(models)
        assert isinstance(sql, str)

    def test_diff_models_added_column(self):
        old = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])
        new = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="email", type=ColumnType.STRING),
        ])
        diffs = self.gen._diff_models(old, new)
        assert any(d.get("action") == "add" for d in diffs)

    def test_diff_models_removed_column(self):
        old = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="old_field", type=ColumnType.STRING),
        ])
        new = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])
        diffs = self.gen._diff_models(old, new)
        assert any(d.get("action") == "remove" for d in diffs)

    def test_diff_models_no_change(self):
        model = ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])
        diffs = self.gen._diff_models(model, model)
        assert diffs == []

    def test_generate_migration(self):
        old = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])]
        new = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
            ColumnSpec(name="email", type=ColumnType.STRING),
        ])]
        migration = self.gen.generate_migration(old, new)
        assert isinstance(migration, str)
        assert "ALTER TABLE" in migration or "email" in migration

    def test_generate_migration_no_change(self):
        models = [ModelSpec(name="User", columns=[
            ColumnSpec(name="id", type=ColumnType.INTEGER, primary_key=True),
        ])]
        migration = self.gen.generate_migration(models, models)
        assert "No migration" in migration


# =====================================================================
# App runner & syntax checker
# =====================================================================

class TestAppRunner:
    def setup_method(self):
        self.runner = AppRunner()
        self.checker = SyntaxChecker()
        self.output_dir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_check_python_valid(self):
        errors = self.checker.check_python("x = 1\ny = 2\nprint(x + y)")
        assert errors == []

    def test_check_python_invalid(self):
        errors = self.checker.check_python("def broken(:\n  pass")
        assert len(errors) > 0

    def test_check_html_valid(self):
        errors = self.checker.check_html("<html><body><p>Hello</p></body></html>")
        assert isinstance(errors, list)

    def test_check_css_balanced(self):
        css = "body { color: red; } .nav { margin: 0; }"
        errors = self.checker.check_css(css)
        assert errors == []

    def test_check_css_unbalanced(self):
        css = "body { color: red; .nav { margin: 0; }"
        errors = self.checker.check_css(css)
        assert len(errors) > 0

    def test_check_js_balanced(self):
        js = "function hello() { return 'world'; }"
        errors = self.checker.check_js(js)
        assert errors == []

    def test_check_js_unbalanced(self):
        js = "function hello() { return 'world';"
        errors = self.checker.check_js(js)
        assert len(errors) > 0

    def test_validate_syntax_on_dir(self):
        with open(os.path.join(self.output_dir, "test.py"), "w") as f:
            f.write("x = 1\n")
        errors = self.runner.validate_syntax(self.output_dir)
        assert errors == []

    def test_validate_syntax_catches_errors(self):
        with open(os.path.join(self.output_dir, "bad.py"), "w") as f:
            f.write("def broken(:\n  pass\n")
        errors = self.runner.validate_syntax(self.output_dir)
        assert len(errors) > 0

    def test_generate_launch_script(self):
        script = self.runner.generate_launch_script(self.output_dir, 5000)
        assert "5000" in script
        assert "python" in script.lower()

    def test_check_template_syntax_valid(self):
        tmpl_dir = os.path.join(self.output_dir, "templates")
        os.makedirs(tmpl_dir)
        with open(os.path.join(tmpl_dir, "index.html"), "w") as f:
            f.write("{% extends 'base.html' %}{% block content %}Hello{% endblock %}")
        errors = self.runner.check_template_syntax(self.output_dir)
        assert errors == []

    def test_check_template_syntax_unbalanced(self):
        tmpl_dir = os.path.join(self.output_dir, "templates")
        os.makedirs(tmpl_dir)
        with open(os.path.join(tmpl_dir, "bad.html"), "w") as f:
            f.write("{% block content %}Hello{% endblock %}{{ missing_close")
        errors = self.runner.check_template_syntax(self.output_dir)
        assert len(errors) > 0

    def test_check_imports(self):
        with open(os.path.join(self.output_dir, "app.py"), "w") as f:
            f.write("import os\nimport json\n")
        warnings = self.runner.check_imports(self.output_dir)
        assert warnings == []


# =====================================================================
# Verification bridge
# =====================================================================

class TestVerificationBridge:
    def setup_method(self):
        self.bridge = VerificationBridge()
        self.output_dir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_verify_empty_dir(self):
        result = self.bridge.verify_generated_app(self.output_dir)
        assert isinstance(result, dict)
        assert "total_issues" in result

    def test_scan_generated_project(self):
        with open(os.path.join(self.output_dir, "main.py"), "w") as f:
            f.write("from flask import Flask\napp = Flask(__name__)\n")
        project = self.bridge._scan_generated_project(self.output_dir)
        assert isinstance(project, dict)
        assert len(project["routes"]) > 0

    def test_check_url_consistency(self):
        routes = {"main.py": "url_for('index')\ndef index(): pass"}
        errors = self.bridge._check_url_consistency(routes)
        assert isinstance(errors, list)

    def test_check_css_references_empty(self):
        errors = self.bridge._check_css_references({}, {})
        assert errors == []

    def test_generate_verification_report(self):
        results = {
            "template_variables": [],
            "css_references": [],
            "form_actions": [],
            "url_consistency": ["Warning: url_for target not found"],
        }
        report = self.bridge._generate_verification_report(results)
        assert "total_issues" in report
        assert report["total_issues"] == 1

    def test_check_form_actions_empty(self):
        errors = self.bridge._check_form_actions({}, {})
        assert errors == []

    def test_check_template_variables_empty(self):
        errors = self.bridge._check_template_variables("", {})
        assert errors == []

    def test_verify_generated_app_with_files(self):
        with open(os.path.join(self.output_dir, "main.py"), "w") as f:
            f.write("from flask import Flask\napp = Flask(__name__)\n")
        os.makedirs(os.path.join(self.output_dir, "templates"), exist_ok=True)
        with open(os.path.join(self.output_dir, "templates", "index.html"), "w") as f:
            f.write("<h1>Hello</h1>")
        result = self.bridge.verify_generated_app(self.output_dir)
        assert isinstance(result, dict)


# =====================================================================
# Snippets
# =====================================================================

class TestSnippets:
    def test_snippets_dict_exists(self):
        assert isinstance(SNIPPETS, dict)
        assert len(SNIPPETS) > 0

    def test_base_html_snippet(self):
        assert "base_html" in SNIPPETS
        assert "<!DOCTYPE html>" in SNIPPETS["base_html"]

    def test_navbar_snippet(self):
        assert "navbar" in SNIPPETS
        assert "nav" in SNIPPETS["navbar"].lower()

    def test_all_snippets_are_strings(self):
        for key, val in SNIPPETS.items():
            assert isinstance(val, str), f"Snippet {key} is not a string"

    def test_required_snippets_present(self):
        required = [
            "base_html", "navbar", "footer", "flash_messages", "pagination",
            "form_field_text", "table_view", "login_form", "modal", "alert",
        ]
        for key in required:
            assert key in SNIPPETS, f"Missing snippet: {key}"

    def test_snippet_has_form_field_select(self):
        assert "form_field_select" in SNIPPETS

    def test_snippet_has_form_field_textarea(self):
        assert "form_field_textarea" in SNIPPETS

    def test_snippet_has_form_field_checkbox(self):
        assert "form_field_checkbox" in SNIPPETS

    def test_snippet_has_card_view(self):
        assert "card_view" in SNIPPETS

    def test_snippet_has_detail_view(self):
        assert "detail_view" in SNIPPETS

    def test_snippet_has_list_view(self):
        assert "list_view" in SNIPPETS

    def test_snippet_has_register_form(self):
        assert "register_form" in SNIPPETS

    def test_snippet_has_search_form(self):
        assert "search_form" in SNIPPETS

    def test_snippet_has_breadcrumb(self):
        assert "breadcrumb" in SNIPPETS

    def test_snippet_has_tabs(self):
        assert "tabs" in SNIPPETS


# =====================================================================
# Blueprint patterns
# =====================================================================

class TestBlueprintPatterns:
    def test_patterns_dict_exists(self):
        assert isinstance(PATTERNS, dict)
        assert len(PATTERNS) > 0

    def test_auth_pattern(self):
        bp = auth_pattern()
        assert isinstance(bp, BlueprintPattern)
        assert len(bp.routes) > 0
        urls = [r.url for r in bp.routes]
        assert any("login" in u for u in urls)

    def test_crud_pattern(self):
        bp = crud_pattern("post")
        assert isinstance(bp, BlueprintPattern)
        assert len(bp.routes) > 0
        methods = [m for r in bp.routes for m in r.methods]
        assert "POST" in methods

    def test_api_pattern(self):
        bp = api_pattern("user")
        assert isinstance(bp, BlueprintPattern)
        assert any(r.response_type == ResponseType.JSON for r in bp.routes)

    def test_dashboard_pattern(self):
        bp = dashboard_pattern()
        assert isinstance(bp, BlueprintPattern)
        assert len(bp.routes) > 0

    def test_search_pattern(self):
        bp = search_pattern()
        assert isinstance(bp, BlueprintPattern)
        assert len(bp.routes) > 0

    def test_file_upload_pattern(self):
        bp = file_upload_pattern()
        assert isinstance(bp, BlueprintPattern)

    def test_admin_pattern(self):
        bp = admin_pattern()
        assert isinstance(bp, BlueprintPattern)

    def test_required_patterns_present(self):
        for name in ["auth", "crud", "api", "dashboard"]:
            assert name in PATTERNS, f"Missing pattern: {name}"

    def test_blueprint_pattern_to_dict(self):
        bp = auth_pattern()
        d = bp.to_dict()
        assert d["name"] == bp.name

    def test_blueprint_pattern_from_dict(self):
        bp = auth_pattern()
        d = bp.to_dict()
        bp2 = BlueprintPattern.from_dict(d)
        assert bp2.name == bp.name
        assert len(bp2.routes) == len(bp.routes)

    def test_crud_pattern_has_models(self):
        bp = crud_pattern("article")
        assert len(bp.models) > 0

    def test_api_pattern_all_json(self):
        bp = api_pattern("item")
        for r in bp.routes:
            assert r.response_type == ResponseType.JSON


# =====================================================================
# End-to-end tests
# =====================================================================

class TestEndToEnd:
    """Full end-to-end: scaffold → generate → verify syntax."""

    def setup_method(self):
        self.output_dir = tempfile.mkdtemp()
        self.scaffolder = AppScaffolder()
        self.generator = FlaskAppGenerator()
        self.runner = AppRunner()
        self.checker = SyntaxChecker()

    def teardown_method(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_scaffold_and_generate_simple_app(self):
        spec = AppSpec(name="hello", port=5001, routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ])
        result = self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "main.py"))
        assert isinstance(result, GenerationResult)

    def test_generated_main_py_valid_syntax(self):
        spec = AppSpec(name="testapp", port=5002, routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ])
        self.generator.generate(spec, self.output_dir)
        main_py = os.path.join(self.output_dir, "main.py")
        with open(main_py) as f:
            source = f.read()
        errors = self.checker.check_python(source)
        assert errors == [], f"main.py syntax errors: {errors}"

    def test_generated_app_has_requirements_txt(self):
        spec = AppSpec(name="myapp", port=5003)
        self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "requirements.txt"))

    def test_generated_app_directory_structure(self):
        spec = AppSpec(name="myapp", port=5004)
        self.generator.generate(spec, self.output_dir)
        assert os.path.isdir(os.path.join(self.output_dir, "templates"))
        assert os.path.isdir(os.path.join(self.output_dir, "static"))

    def test_full_crud_scaffold_and_generate(self):
        spec = self.scaffolder.scaffold_crud_app("blog", [
            {"name": "Post", "columns": [
                {"name": "title", "type": "string"},
                {"name": "body", "type": "text"},
            ]},
        ])
        result = self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "main.py"))
        with open(os.path.join(self.output_dir, "main.py")) as f:
            source = f.read()
        errors = self.checker.check_python(source)
        assert errors == [], f"CRUD app main.py syntax errors: {errors}"

    def test_validate_syntax_all_py_files(self):
        spec = AppSpec(name="syntaxtest", port=5005, routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
            RouteSpec(url="/api/data", handler_name="api_data", response_type=ResponseType.JSON),
        ])
        self.generator.generate(spec, self.output_dir)
        errors = self.runner.validate_syntax(self.output_dir)
        assert errors == [], f"Syntax errors in generated app: {errors}"

    def test_api_scaffold_and_generate(self):
        spec = self.scaffolder.scaffold_api_app("myapi", [
            {"name": "item", "fields": [
                {"name": "name", "type": "string"},
                {"name": "value", "type": "integer"},
            ]},
        ])
        result = self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "main.py"))

    def test_generated_requirements_contains_flask(self):
        spec = AppSpec(name="flaskapp", port=5006)
        self.generator.generate(spec, self.output_dir)
        with open(os.path.join(self.output_dir, "requirements.txt")) as f:
            reqs = f.read()
        assert "flask" in reqs.lower() or "Flask" in reqs

    def test_main_py_has_port(self):
        spec = AppSpec(name="porttest", port=7777)
        self.generator.generate(spec, self.output_dir)
        with open(os.path.join(self.output_dir, "main.py")) as f:
            source = f.read()
        assert "7777" in source

    def test_main_py_has_if_main_block(self):
        spec = AppSpec(name="maintest", port=5007)
        self.generator.generate(spec, self.output_dir)
        with open(os.path.join(self.output_dir, "main.py")) as f:
            source = f.read()
        assert "__main__" in source
        assert "app.run" in source

    def test_main_py_has_get_db(self):
        spec = AppSpec(name="dbtest", port=5008)
        self.generator.generate(spec, self.output_dir)
        with open(os.path.join(self.output_dir, "main.py")) as f:
            source = f.read()
        assert "get_db" in source

    def test_generated_config_valid_python(self):
        spec = AppSpec(name="cfgtest", port=5009, config=ConfigSpec(debug=True))
        self.generator.generate(spec, self.output_dir)
        config_py = os.path.join(self.output_dir, "config.py")
        with open(config_py) as f:
            source = f.read()
        errors = self.checker.check_python(source)
        assert errors == [], f"config.py syntax errors: {errors}"

    def test_generated_init_db_valid_python(self):
        spec = self.scaffolder.scaffold_crud_app("test", [
            {"name": "Item", "columns": [{"name": "name", "type": "string"}]},
        ])
        self.generator.generate(spec, self.output_dir)
        init_db = os.path.join(self.output_dir, "init_db.py")
        assert os.path.exists(init_db)
        with open(init_db) as f:
            source = f.read()
        errors = self.checker.check_python(source)
        assert errors == [], f"init_db.py syntax errors: {errors}"

    def test_generated_routes_py_valid_python(self):
        spec = AppSpec(name="routetest", routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ])
        self.generator.generate(spec, self.output_dir)
        routes_py = os.path.join(self.output_dir, "routes.py")
        with open(routes_py) as f:
            source = f.read()
        errors = self.checker.check_python(source)
        assert errors == [], f"routes.py syntax errors: {errors}"

    def test_generated_templates_have_extends(self):
        spec = AppSpec(name="tmpltest", routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ])
        self.generator.generate(spec, self.output_dir)
        idx_path = os.path.join(self.output_dir, "templates", "index.html")
        assert os.path.exists(idx_path)
        with open(idx_path) as f:
            content = f.read()
        assert "{% extends" in content

    def test_generated_static_css_exists(self):
        spec = AppSpec(name="statictest")
        self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "static", "css", "base.css"))

    def test_generated_static_js_exists(self):
        spec = AppSpec(name="jstest")
        self.generator.generate(spec, self.output_dir)
        assert os.path.exists(os.path.join(self.output_dir, "static", "js", "base.js"))

    def test_full_api_all_files_valid_python(self):
        spec = self.scaffolder.scaffold_api_app("api", [
            {"name": "widget", "fields": [{"name": "label", "type": "string"}]},
        ])
        self.generator.generate(spec, self.output_dir)
        errors = self.runner.validate_syntax(self.output_dir)
        assert errors == [], f"API app syntax errors: {errors}"

    def test_generation_result_has_files(self):
        spec = AppSpec(name="test", routes=[
            RouteSpec(url="/", handler_name="index", template="index.html"),
        ])
        result = self.generator.generate(spec, self.output_dir)
        assert len(result.files_created) > 0

    def test_dashboard_scaffold_generate_valid(self):
        spec = self.scaffolder.scaffold_dashboard_app("dash", [{"name": "stats"}])
        self.generator.generate(spec, self.output_dir)
        errors = self.runner.validate_syntax(self.output_dir)
        assert errors == [], f"Dashboard app syntax errors: {errors}"
