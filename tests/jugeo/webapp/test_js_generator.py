from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))
from jugeo.webapp.cli.generators.js_generator import JSGenerator, GeneratedJS

FLASK_SPEC = {
    "name": "recipe_app",
    "mode": "flask",
    "auth_required": True,
    "domain_nouns": ["recipe"],
    "forms": [
        {
            "name": "RecipeForm",
            "fields": [
                {"name": "title", "required": True, "type": "text"},
                {"name": "email", "required": True, "type": "email"},
            ],
        }
    ],
}

STATIC_SPEC = {
    "name": "coffee",
    "mode": "static",
    "auth_required": False,
    "domain_nouns": ["coffee"],
    "forms": [],
    "ui_metaphors": ["hero section"],
}


@pytest.fixture(scope="module")
def flask_result() -> GeneratedJS:
    return JSGenerator().generate(FLASK_SPEC)


@pytest.fixture(scope="module")
def static_result() -> GeneratedJS:
    return JSGenerator().generate(STATIC_SPEC)


def _all_code(result: GeneratedJS) -> str:
    return "\n".join(filter(None, [result.csrf_js, result.form_validation_js, result.interactions_js]))


def test_flask_generates_csrf_js(flask_result: GeneratedJS) -> None:
    assert flask_result.csrf_js != ""


def test_static_no_csrf_js(static_result: GeneratedJS) -> None:
    assert static_result.csrf_js == "" or "X-CSRFToken" not in static_result.csrf_js


def test_no_var_keyword(flask_result: GeneratedJS) -> None:
    code = _all_code(flask_result)
    # Strip comments and strings before checking
    stripped = re.sub(r'//[^\n]*', '', code)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', stripped)
    stripped = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", stripped)
    stripped = re.sub(r'`[^`\\]*(?:\\.[^`\\]*)*`', '``', stripped)
    assert re.search(r'\bvar\s', stripped) is None, "Found 'var' keyword in generated JS"


def test_async_await_in_csrf(flask_result: GeneratedJS) -> None:
    assert "async" in flask_result.csrf_js
    assert "await" in flask_result.csrf_js


def test_arrow_functions_in_callbacks(flask_result: GeneratedJS) -> None:
    assert "=>" in flask_result.interactions_js


def test_nullish_coalescing_used(flask_result: GeneratedJS) -> None:
    code = _all_code(flask_result)
    assert "??" in code


def test_explicit_null_check(flask_result: GeneratedJS) -> None:
    code = _all_code(flask_result)
    assert ("== null" in code or "!= null" in code), (
        "Expected explicit null check (== null or != null), found neither"
    )


def test_parseint_has_radix(flask_result: GeneratedJS) -> None:
    code = _all_code(flask_result)
    if "parseInt" in code:
        # Every parseInt call must be followed by ", 10)"
        calls = re.findall(r'parseInt\([^)]+\)', code)
        for call in calls:
            assert ", 10)" in call, f"parseInt missing radix 10: {call}"


def test_form_validation_checks_required(flask_result: GeneratedJS) -> None:
    assert ".trim() ===" in flask_result.form_validation_js


def test_form_validation_email_regex(flask_result: GeneratedJS) -> None:
    # The FLASK_SPEC includes an email field; the validator should have a regex
    assert re.search(r'/\^.*@.*\$/', flask_result.form_validation_js) or \
           "@" in flask_result.form_validation_js and "test(" in flask_result.form_validation_js


def test_theory_annotations_nonempty(flask_result: GeneratedJS) -> None:
    assert len(flask_result.theory_annotations) > 0


def test_no_violations_in_generated(flask_result: GeneratedJS) -> None:
    assert flask_result.violations == [], (
        f"Generated code has violations: {flask_result.violations}"
    )
