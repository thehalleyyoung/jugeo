from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

import pytest
from jugeo.webapp.cli.visual_correctness import VisualCorrectnessChecker, VisualCheck

GOOD_CSS = """
:root { --color-text: #1a1a1a; --color-bg: #ffffff; }
body { color: #1a1a1a; background: #ffffff; font-size: 1rem; }
h1 { font-size: 1.953rem; }
.btn { min-height: 44px; padding: 0.75rem 1.5rem; }
:focus-visible { outline: 2px solid #1d5fa8; }
@media (min-width: 640px) { .grid { grid-template-columns: repeat(2,1fr); } }
"""

GOOD_HTML = {
    "templates/base.html": (
        '<html lang="en"><head><title>App</title></head><body>'
        '<main><h1>Hello</h1><h2>Sub</h2>'
        '<img src="x.jpg" alt="desc" width="800" height="400">'
        '</main></body></html>'
    )
}

BAD_CSS = """
body { color: #aaaaaa; background: #ffffff; }  /* low contrast ~2.3:1 */
h1 { font-size: 2.5rem; }  /* off scale */
@media (max-width: 768px) { .grid { display: block; } }  /* max-width not mobile-first */
"""

BAD_HTML = {
    "templates/base.html": (
        '<html><head><title>App</title></head><body>'
        '<h3>Skipped h1 and h2</h3>'
        '<img src="x.jpg">'
        '</body></html>'
    )
}

_checker = VisualCorrectnessChecker()


def _check(css: str = "", html: dict[str, str] | None = None) -> object:
    files: dict[str, str] = {}
    if css:
        files["static/style.css"] = css
    if html:
        files.update(html)
    return _checker.check(files)


# ---------------------------------------------------------------------------
# Contrast
# ---------------------------------------------------------------------------

def test_good_css_no_contrast_violations():
    report = _check(css=GOOD_CSS)
    contrast_violations = [v for v in report.violations if v.check == VisualCheck.TEXT_CONTRAST]
    assert contrast_violations == [], contrast_violations


def test_bad_css_contrast_violation():
    report = _check(css=BAD_CSS)
    contrast_violations = [v for v in report.violations if v.check == VisualCheck.TEXT_CONTRAST]
    assert len(contrast_violations) >= 1


# ---------------------------------------------------------------------------
# Mobile layout
# ---------------------------------------------------------------------------

def test_max_width_media_query_violation():
    report = _check(css=BAD_CSS)
    layout_violations = [v for v in report.violations if v.check == VisualCheck.MOBILE_LAYOUT]
    assert any(v.severity == "error" for v in layout_violations)


# ---------------------------------------------------------------------------
# Focus style
# ---------------------------------------------------------------------------

def test_good_css_has_focus_style():
    report = _check(css=GOOD_CSS)
    focus_violations = [v for v in report.violations if v.check == VisualCheck.FOCUS_STYLE]
    assert focus_violations == [], focus_violations


# ---------------------------------------------------------------------------
# Touch targets
# ---------------------------------------------------------------------------

def test_touch_target_present():
    report = _check(css=GOOD_CSS)
    touch_violations = [v for v in report.violations if v.check == VisualCheck.TOUCH_TARGETS]
    assert touch_violations == [], touch_violations


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------

def test_heading_hierarchy_good():
    report = _check(html=GOOD_HTML)
    heading_violations = [v for v in report.violations if v.check == VisualCheck.HEADING_HIERARCHY]
    assert heading_violations == [], heading_violations


def test_heading_hierarchy_bad():
    report = _check(html=BAD_HTML)
    heading_violations = [v for v in report.violations if v.check == VisualCheck.HEADING_HIERARCHY]
    assert len(heading_violations) >= 1


# ---------------------------------------------------------------------------
# Image dimensions
# ---------------------------------------------------------------------------

def test_image_dimensions_good():
    report = _check(html=GOOD_HTML)
    img_violations = [v for v in report.violations if v.check == VisualCheck.IMAGE_DIMENSIONS]
    assert img_violations == [], img_violations


def test_image_dimensions_bad():
    report = _check(html=BAD_HTML)
    img_violations = [v for v in report.violations if v.check == VisualCheck.IMAGE_DIMENSIONS]
    assert len(img_violations) >= 1


# ---------------------------------------------------------------------------
# WCAG AA + summary
# ---------------------------------------------------------------------------

def test_passes_wcag_aa_for_good():
    report = _check(css=GOOD_CSS, html=GOOD_HTML)
    assert report.passes_wcag_aa() is True


def test_summary_nonempty():
    report = _check(css=GOOD_CSS, html=GOOD_HTML)
    s = report.summary()
    assert isinstance(s, str) and len(s) > 0


# ---------------------------------------------------------------------------
# Estimated LCP
# ---------------------------------------------------------------------------

def test_estimated_lcp_reasonable():
    report = _check(css=GOOD_CSS, html=GOOD_HTML)
    assert report.estimated_lcp_ms is None or report.estimated_lcp_ms > 0
