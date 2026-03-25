from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))
from jugeo.webapp.cli.generators.css_generator import CSSGenerator, ColorPalette, GeneratedCSS

RECIPE_SPEC = {
    "name": "recipe_app",
    "mode": "flask",
    "auth_required": True,
    "ui_metaphors": ["card grid"],
    "domain_nouns": ["recipe"],
}

LANDING_SPEC = {
    "name": "coffee_shop",
    "mode": "static",
    "auth_required": False,
    "ui_metaphors": ["hero section"],
    "domain_nouns": ["coffee"],
}


@pytest.fixture(scope="module")
def recipe_result() -> GeneratedCSS:
    return CSSGenerator().generate(RECIPE_SPEC)


@pytest.fixture(scope="module")
def landing_result() -> GeneratedCSS:
    return CSSGenerator().generate(LANDING_SPEC)


# ---------------------------------------------------------------------------
# Basic output
# ---------------------------------------------------------------------------

def test_generates_nonempty_css(recipe_result: GeneratedCSS) -> None:
    assert len(recipe_result.style_css) > 0


def test_css_has_root_variables(recipe_result: GeneratedCSS) -> None:
    assert ":root" in recipe_result.style_css


def test_css_has_color_text_var(recipe_result: GeneratedCSS) -> None:
    assert "--color-text" in recipe_result.style_css


def test_css_has_font_size_vars(recipe_result: GeneratedCSS) -> None:
    assert "--text-base" in recipe_result.style_css


# ---------------------------------------------------------------------------
# Theory: colour contrast
# ---------------------------------------------------------------------------

def test_palette_contrast_passes(recipe_result: GeneratedCSS) -> None:
    violations = recipe_result.palette.verify_contrast()
    assert violations == [], f"Contrast violations: {violations}"


# ---------------------------------------------------------------------------
# Theory: responsive / mobile-first
# ---------------------------------------------------------------------------

def test_mobile_first_breakpoints(recipe_result: GeneratedCSS) -> None:
    assert "min-width" in recipe_result.style_css


# ---------------------------------------------------------------------------
# Theory: CSS specificity — no #id selectors, no !important
# ---------------------------------------------------------------------------

def test_no_id_selectors(recipe_result: GeneratedCSS) -> None:
    # Strip hex colour values (#rrggbb / #rgb) before checking for ID selectors.
    body = re.sub(r'#[0-9a-fA-F]{3,8}', '', recipe_result.style_css)
    assert re.search(r'#[a-z]', body) is None, "CSS ID selector found"


def test_no_important(recipe_result: GeneratedCSS) -> None:
    # The generated rules must not use !important
    # (prefers-reduced-motion block may use it for overriding animations — strip comments)
    body = recipe_result.style_css
    # Remove the prefers-reduced-motion block before checking
    body_no_motion = re.sub(
        r'@media\s*\(prefers-reduced-motion[^}]+\}\s*\}', '', body, flags=re.DOTALL
    )
    assert "!important" not in body_no_motion


# ---------------------------------------------------------------------------
# Theory: WCAG touch targets and focus
# ---------------------------------------------------------------------------

def test_btn_primary_has_touch_target(recipe_result: GeneratedCSS) -> None:
    assert "44px" in recipe_result.style_css


def test_focus_visible_present(recipe_result: GeneratedCSS) -> None:
    assert "focus-visible" in recipe_result.style_css


# ---------------------------------------------------------------------------
# Accessibility utilities
# ---------------------------------------------------------------------------

def test_sr_only_present(recipe_result: GeneratedCSS) -> None:
    assert ".sr-only" in recipe_result.style_css


# ---------------------------------------------------------------------------
# Theory annotations
# ---------------------------------------------------------------------------

def test_theory_annotations_present(recipe_result: GeneratedCSS) -> None:
    assert len(recipe_result.theory_annotations) > 0


# ---------------------------------------------------------------------------
# UI-metaphor driven generation
# ---------------------------------------------------------------------------

def test_card_grid_for_card_metaphor(recipe_result: GeneratedCSS) -> None:
    assert ".card" in recipe_result.style_css


def test_hero_section_for_landing(landing_result: GeneratedCSS) -> None:
    assert ".hero" in landing_result.style_css


# ---------------------------------------------------------------------------
# Type scale
# ---------------------------------------------------------------------------

def test_type_scale_ratio(recipe_result: GeneratedCSS) -> None:
    assert recipe_result.type_scale.ratio == 1.25
