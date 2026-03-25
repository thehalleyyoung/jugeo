from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))
from jugeo.webapp.cli.generators.flask_generator import FlaskGenerator, GeneratedFlaskApp

RECIPE_SPEC = {
    "name": "recipe_app",
    "mode": "flask",
    "port": 5000,
    "auth_required": True,
    "models": [
        {
            "name": "Recipe",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True},
                {"name": "title", "type": "String(200)", "nullable": False},
                {"name": "user_id", "type": "Integer", "foreign_key": "users.id"},
            ],
        },
        {
            "name": "User",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True},
                {"name": "username", "type": "String(80)", "nullable": False},
            ],
        },
    ],
    "routes": [
        {"path": "/", "method": "GET", "handler": "index", "auth_required": False},
        {"path": "/recipes", "method": "GET", "handler": "recipe_list", "auth_required": False},
        {"path": "/recipes", "method": "POST", "handler": "recipe_create", "auth_required": True},
        {"path": "/recipes/<int:id>", "method": "GET", "handler": "recipe_show", "auth_required": False},
        {"path": "/login", "method": "GET", "handler": "login", "auth_required": False},
        {"path": "/login", "method": "POST", "handler": "login_post", "auth_required": False},
        {"path": "/logout", "method": "GET", "handler": "logout", "auth_required": True},
    ],
}

STATIC_SPEC = {
    "name": "static_site",
    "mode": "static",
    "auth_required": False,
    "models": [],
    "routes": [
        {"path": "/", "method": "GET", "handler": "index", "auth_required": False},
    ],
}


@pytest.fixture(scope="module")
def recipe_result() -> GeneratedFlaskApp:
    return FlaskGenerator().generate(RECIPE_SPEC)


@pytest.fixture(scope="module")
def static_result() -> GeneratedFlaskApp:
    return FlaskGenerator().generate(STATIC_SPEC)


# ------------------------------------------------------------------
# Basic structure
# ------------------------------------------------------------------

def test_generates_app_py(recipe_result: GeneratedFlaskApp) -> None:
    assert isinstance(recipe_result.app_py, str)
    assert len(recipe_result.app_py) > 0


def test_app_py_has_flask_import(recipe_result: GeneratedFlaskApp) -> None:
    assert "from flask import" in recipe_result.app_py


# ------------------------------------------------------------------
# BeforeRequestChecker constraint
# ------------------------------------------------------------------

def test_app_py_has_before_request(recipe_result: GeneratedFlaskApp) -> None:
    assert "before_request" in recipe_result.app_py


def test_csrf_in_before_request(recipe_result: GeneratedFlaskApp) -> None:
    """CSRF check must appear in the before_request block for POST."""
    app_py = recipe_result.app_py
    before_idx = app_py.find("before_request")
    assert before_idx != -1
    # CSRF token handling should appear near / after the before_request definition
    assert "csrf" in app_py.lower() or "_csrf_token" in app_py or "WTF_CSRF" in app_py


# ------------------------------------------------------------------
# Auth / login_required constraint
# ------------------------------------------------------------------

def test_app_py_has_login_required(recipe_result: GeneratedFlaskApp) -> None:
    """auth_required=True spec must include login_required decorator."""
    assert "login_required" in recipe_result.app_py


# ------------------------------------------------------------------
# FlaskURLSite constraint: Integer PK → int converter
# ------------------------------------------------------------------

def test_int_converter_used(recipe_result: GeneratedFlaskApp) -> None:
    """Integer PK fields must produce <int:id> path parameters."""
    assert "<int:id>" in recipe_result.app_py


# ------------------------------------------------------------------
# RoutesCachePolicy constraint
# ------------------------------------------------------------------

def test_cache_header_on_routes(recipe_result: GeneratedFlaskApp) -> None:
    assert "Cache-Control" in recipe_result.app_py


# ------------------------------------------------------------------
# SQLTransactionDescentChecker constraint
# ------------------------------------------------------------------

def test_db_session_commit_on_post(recipe_result: GeneratedFlaskApp) -> None:
    """All mutating routes must call db.session.commit()."""
    assert "db.session.commit" in recipe_result.app_py


# ------------------------------------------------------------------
# Theory annotations
# ------------------------------------------------------------------

def test_theory_annotations_present(recipe_result: GeneratedFlaskApp) -> None:
    assert isinstance(recipe_result.theory_annotations, list)
    assert len(recipe_result.theory_annotations) > 0


# ------------------------------------------------------------------
# Requirements
# ------------------------------------------------------------------

def test_requirements_has_flask(recipe_result: GeneratedFlaskApp) -> None:
    assert "flask" in recipe_result.requirements_txt.lower()


def test_requirements_has_flask_login(recipe_result: GeneratedFlaskApp) -> None:
    """auth_required=True must pull in flask-login."""
    assert "flask-login" in recipe_result.requirements_txt.lower()


# ------------------------------------------------------------------
# Static mode: no before_request
# ------------------------------------------------------------------

def test_static_mode_no_before_request(static_result: GeneratedFlaskApp) -> None:
    """Static-mode specs should not emit an @app.before_request block."""
    assert "@app.before_request" not in static_result.app_py
