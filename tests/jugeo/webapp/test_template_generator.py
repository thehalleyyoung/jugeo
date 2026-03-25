"""
Tests for TemplateGenerator — Phase 2b of the judgment-geometric generation pipeline.

Theory constraints verified:
- DOMValidityPresheaf: valid HTML5 structure
- AccessibilityChecker: lang, title, headings, labels, alt
- JinjaTemplateSite: base.html inheritance
- CSRFChecker: csrf_token in every POST form (Flask mode)
- WCAGCriterion 2.4.1: skip-link present
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from jugeo.webapp.cli.generators.template_generator import TemplateGenerator, GeneratedTemplates

FLASK_SPEC = {
    "name": "RecipeApp",
    "mode": "flask",
    "auth_required": True,
    "domain_nouns": ["recipe"],
    "ui_metaphors": ["card grid"],
    "models": [
        {
            "name": "Recipe",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True},
                {"name": "title", "type": "String", "nullable": False},
            ],
        }
    ],
    "forms": [
        {
            "name": "RecipeForm",
            "fields": [{"name": "title", "required": True, "type": "text"}],
        }
    ],
}

STATIC_SPEC = {
    "name": "RecipeApp",
    "mode": "static",
    "auth_required": False,
    "domain_nouns": ["recipe"],
    "ui_metaphors": ["card grid"],
    "models": [
        {
            "name": "Recipe",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True},
                {"name": "title", "type": "String", "nullable": False},
            ],
        }
    ],
    "forms": [
        {
            "name": "RecipeForm",
            "fields": [{"name": "title", "required": True, "type": "text"}],
        }
    ],
}


def _flask_result() -> GeneratedTemplates:
    return TemplateGenerator().generate(FLASK_SPEC)


def _static_result() -> GeneratedTemplates:
    return TemplateGenerator().generate(STATIC_SPEC)


def _form_template(result: GeneratedTemplates) -> str:
    for key, content in result.files.items():
        if "form" in key:
            return content
    return ""


# ---------------------------------------------------------------------------
# Base template structure
# ---------------------------------------------------------------------------

def test_generates_base_html():
    """JinjaTemplateSite: base.html must be present."""
    result = _flask_result()
    assert "base.html" in result.files


def test_base_has_lang_attr():
    """AccessibilityChecker: lang attribute satisfies WCAG 3.1.1."""
    result = _flask_result()
    assert 'lang="en"' in result.files["base.html"]


def test_base_has_skip_link():
    """WCAGCriterion 2.4.1: skip navigation link to #main-content."""
    result = _flask_result()
    base = result.files["base.html"]
    assert "skip" in base.lower()
    assert "#main-content" in base


def test_base_has_main_landmark():
    """AccessibilityChecker: <main> landmark element present."""
    result = _flask_result()
    assert "<main" in result.files["base.html"]


def test_base_has_csrf_meta():
    """CSRFChecker: csrf-token meta tag present in Flask base for JS reads."""
    result = _flask_result()
    assert "csrf-token" in result.files["base.html"]


# ---------------------------------------------------------------------------
# CRUD templates
# ---------------------------------------------------------------------------

def test_generates_list_template():
    """DOMValidityPresheaf: list template generated for each domain noun."""
    result = _flask_result()
    list_keys = [k for k in result.files if "list" in k]
    assert list_keys, f"No list template found. Files: {list(result.files.keys())}"
    key = list_keys[0]
    assert "recipe" in key.lower()


def test_generates_form_template():
    """DOMValidityPresheaf: form template generated for each domain noun."""
    result = _flask_result()
    form_keys = [k for k in result.files if "form" in k]
    assert form_keys, f"No form template found. Files: {list(result.files.keys())}"


def test_form_has_csrf_token():
    """CSRFChecker: {{ csrf_token() }} present in every Flask POST form."""
    result = _flask_result()
    form_content = _form_template(result)
    assert form_content, "Form template not found"
    assert "csrf_token" in form_content


def test_form_inputs_have_labels():
    """AccessibilityChecker: every <input> must have an associated <label> (WCAG 1.3.1)."""
    result = _flask_result()
    form_content = _form_template(result)
    assert form_content, "Form template not found"

    inputs = re.findall(r'<input[^>]+name=["\'](\w+)["\']', form_content)
    for input_name in inputs:
        assert f'for="{input_name}"' in form_content, (
            f"Input '{input_name}' has no associated <label for='{input_name}'>"
        )


# ---------------------------------------------------------------------------
# Auth templates
# ---------------------------------------------------------------------------

def test_generates_login_template():
    """Auth mode: login.html must be generated."""
    result = _flask_result()
    assert "login.html" in result.files


def test_login_has_csrf_token():
    """CSRFChecker: login POST form has csrf_token."""
    result = _flask_result()
    assert "csrf_token" in result.files["login.html"]


def test_generates_register_template():
    """Auth mode: register.html must be generated."""
    result = _flask_result()
    assert "register.html" in result.files


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------

def test_heading_hierarchy():
    """AccessibilityChecker: h1 present in each page template; no h3 without preceding h2."""
    result = _flask_result()

    for name, content in result.files.items():
        if name == "base.html":
            continue
        assert "<h1" in content, f"{name}: missing <h1> primary heading"
        if "<h3" in content:
            assert "<h2" in content, f"{name}: h3 present without h2 (broken hierarchy)"


# ---------------------------------------------------------------------------
# Theory annotations & violations
# ---------------------------------------------------------------------------

def test_theory_annotations_nonempty():
    """Theory integration: annotations must be populated to document constraints."""
    result = _flask_result()
    assert result.theory_annotations, "theory_annotations must not be empty"


def test_violations_empty():
    """All theory constraints satisfied: no violations for a valid spec."""
    result = _flask_result()
    assert result.violations == [], f"Unexpected violations: {result.violations}"


# ---------------------------------------------------------------------------
# Static mode
# ---------------------------------------------------------------------------

def test_static_mode_no_jinja():
    """Static mode must produce plain HTML with no Jinja2 syntax."""
    result = _static_result()
    for name, content in result.files.items():
        assert "{% " not in content, (
            f"Static file '{name}' contains Jinja2 block syntax"
        )
        assert "{{" not in content, (
            f"Static file '{name}' contains Jinja2 variable syntax"
        )


def test_static_mode_has_lang_attr():
    """AccessibilityChecker: static pages must have lang attribute (WCAG 3.1.1)."""
    result = _static_result()
    for name, content in result.files.items():
        assert 'lang="en"' in content, f"{name}: missing lang attribute"


def test_static_mode_has_skip_link():
    """WCAGCriterion 2.4.1: skip-link present in every static page."""
    result = _static_result()
    for name, content in result.files.items():
        assert "skip" in content.lower() and "#main-content" in content, (
            f"{name}: missing skip navigation link"
        )
