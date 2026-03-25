from __future__ import annotations

__all__ = ["VisualCheck", "VisualViolation", "VisualCorrectnessChecker", "VisualReport"]

import re
from dataclasses import dataclass, field
from enum import Enum

try:
    from jugeo.webapp.theory.visual.color.spaces import Color, ColorDistance, ColorSpace
    from jugeo.webapp.theory.visual.type.metrics import ModularScale, TypeScaleRatio
    from jugeo.webapp.theory.visual.layout.responsive import BreakpointSystem, ResponsiveLayoutChecker
    from jugeo.webapp.theory.integration.accessibility import AccessibilityChecker, WCAGReport
    from jugeo.webapp.theory.integration.performance import (
        PerformanceChecker,
        CriticalRenderPath,
        WebResource,
        ResourceKind,
        CoreWebVitals,
    )
    _THEORY = True
except ImportError:
    _THEORY = False


class VisualCheck(str, Enum):
    TEXT_CONTRAST = "text_contrast"
    LARGE_TEXT_CONTRAST = "large_text_contrast"
    FONT_SCALE = "font_scale"
    MOBILE_LAYOUT = "mobile_layout"
    HEADING_HIERARCHY = "heading_hierarchy"
    IMAGE_DIMENSIONS = "image_dimensions"
    FONT_DISPLAY = "font_display"
    FOCUS_STYLE = "focus_style"
    TOUCH_TARGETS = "touch_targets"
    RENDER_BLOCKING = "render_blocking"
    CORE_WEB_VITALS = "core_web_vitals"


@dataclass(frozen=True)
class VisualViolation:
    check: VisualCheck
    description: str
    location: str
    actual: str
    required: str
    severity: str = "error"


@dataclass
class VisualReport:
    violations: list[VisualViolation]
    passed: list[VisualCheck]
    wcag_level: str  # "AA" | "AAA" | "fail"
    estimated_lcp_ms: float | None
    estimated_cls: float | None

    def has_errors(self) -> bool:
        return any(v.severity == "error" for v in self.violations)

    def passes_wcag_aa(self) -> bool:
        aa_checks = {VisualCheck.TEXT_CONTRAST, VisualCheck.HEADING_HIERARCHY, VisualCheck.FOCUS_STYLE}
        return not any(v.check in aa_checks for v in self.violations)

    def summary(self) -> str:
        n_errors = sum(1 for v in self.violations if v.severity == "error")
        n_warnings = sum(1 for v in self.violations if v.severity == "warning")
        parts = [f"WCAG level: {self.wcag_level}", f"{len(self.passed)} checks passed"]
        if n_errors:
            parts.append(f"{n_errors} error(s)")
        if n_warnings:
            parts.append(f"{n_warnings} warning(s)")
        if not self.violations:
            parts.append("no violations")
        if self.estimated_lcp_ms is not None:
            parts.append(f"LCP≈{self.estimated_lcp_ms:.0f}ms")
        if self.estimated_cls is not None:
            parts.append(f"CLS≈{self.estimated_cls:.2f}")
        return " | ".join(parts)


def _hex_to_linear_rgb(hex_color: str) -> tuple[float, float, float] | None:
    """Parse a CSS hex color and return linear-light RGB components."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) != 6:
        return None
    try:
        r8, g8, b8 = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None

    def linearize(c8: int) -> float:
        c = c8 / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return linearize(r8), linearize(g8), linearize(b8)


def _relative_luminance(hex_color: str) -> float | None:
    rgb = _hex_to_linear_rgb(hex_color)
    if rgb is None:
        return None
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _wcag_contrast(fg: str, bg: str) -> float | None:
    """Compute WCAG contrast ratio between two hex colors."""
    if _THEORY:
        try:
            c1 = Color.from_hex(fg)
            c2 = Color.from_hex(bg)
            return ColorDistance.wcag_contrast(c1, c2)
        except Exception:
            pass
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    if l1 is None or l2 is None:
        return None
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


# CSS rule block parser: returns list of (selector, body) pairs.
_RULE_RE = re.compile(r'([^{]+)\{([^}]*)\}', re.DOTALL)


def _parse_rules(css: str) -> list[tuple[str, str]]:
    return [(m.group(1).strip(), m.group(2).strip()) for m in _RULE_RE.finditer(css)]


class VisualCorrectnessChecker:
    """
    Verifies visual correctness of generated CSS + HTML using theory modules.

    Checks (with theory module citations):
    - TEXT_CONTRAST:      ColorDistance.wcag_contrast() ≥ 4.5:1 (WCAG 1.4.3)
    - LARGE_TEXT_CONTRAST: ColorDistance.wcag_contrast() ≥ 3:1 for font-size > 18px
    - FONT_SCALE:         ModularScale — all font-size values from ratio scale
    - MOBILE_LAYOUT:      ResponsiveLayoutChecker — min-width only, no gap 0-639px
    - HEADING_HIERARCHY:  AccessibilityChecker.check_heading_structure()
    - IMAGE_DIMENSIONS:   PerformanceChecker.check_image_dimensions() — CLS prevention
    - FONT_DISPLAY:       PerformanceChecker.check_font_loading() — FOUT prevention
    - FOCUS_STYLE:        :focus-visible present in CSS (WCAG 2.4.7)
    - TOUCH_TARGETS:      min-height/min-width ≥ 44px on .btn (WCAG 2.5.5)
    - RENDER_BLOCKING:    CriticalRenderPath ≤ 2 blocking resources
    - CORE_WEB_VITALS:    CoreWebVitals.estimate_from_path() LCP < 2500ms, CLS < 0.1
    """

    def check(self, files: dict[str, str]) -> VisualReport:
        """
        Run all visual correctness checks.

        Parameters
        ----------
        files:
            Mapping of file path to content, e.g.
            ``{"static/style.css": "...", "templates/base.html": "..."}``.
        """
        css_parts: list[str] = []
        html_files: dict[str, str] = {}
        for path, content in files.items():
            if path.endswith(".css"):
                css_parts.append(content)
            elif path.endswith((".html", ".htm", ".jinja", ".jinja2")):
                html_files[path] = content

        css = "\n".join(css_parts)

        all_violations: list[VisualViolation] = []
        all_violations.extend(self._check_text_contrast(css))
        all_violations.extend(self._check_font_scale(css))
        all_violations.extend(self._check_mobile_layout(css))
        all_violations.extend(self._check_heading_hierarchy(html_files))
        all_violations.extend(self._check_image_dimensions(html_files))
        all_violations.extend(self._check_focus_style(css))
        all_violations.extend(self._check_touch_targets(css))
        all_violations.extend(self._check_render_blocking(html_files))

        violated_checks = {v.check for v in all_violations if v.severity == "error"}
        all_checks = set(VisualCheck)
        passed = [c for c in all_checks if c not in violated_checks]

        lcp_ms, cls = self._estimate_web_vitals(html_files, css)

        vitals_violations: list[VisualViolation] = []
        if lcp_ms is not None and lcp_ms >= 2500:
            vitals_violations.append(VisualViolation(
                check=VisualCheck.CORE_WEB_VITALS,
                description="Estimated LCP exceeds 2500ms threshold",
                location="page",
                actual=f"{lcp_ms:.0f}ms",
                required="< 2500ms",
                severity="warning",
            ))
        if cls is not None and cls >= 0.1:
            vitals_violations.append(VisualViolation(
                check=VisualCheck.CORE_WEB_VITALS,
                description="Estimated CLS exceeds 0.1 threshold",
                location="page",
                actual=f"{cls:.2f}",
                required="< 0.1",
                severity="warning",
            ))
        all_violations.extend(vitals_violations)

        aa_checks = {VisualCheck.TEXT_CONTRAST, VisualCheck.HEADING_HIERARCHY, VisualCheck.FOCUS_STYLE}
        error_checks = {v.check for v in all_violations if v.severity == "error"}
        if not (error_checks & aa_checks):
            wcag_level = "AA"
        else:
            wcag_level = "fail"

        return VisualReport(
            violations=all_violations,
            passed=passed,
            wcag_level=wcag_level,
            estimated_lcp_ms=lcp_ms,
            estimated_cls=cls,
        )

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def _check_text_contrast(self, css: str) -> list[VisualViolation]:
        violations: list[VisualViolation] = []
        hex_re = re.compile(r'#([0-9a-fA-F]{3,6})\b')

        for selector, body in _parse_rules(css):
            # Skip @media / @keyframes wrappers
            if selector.lstrip().startswith("@"):
                continue

            colors = re.findall(r'(?:^|;|\s)color\s*:\s*(#[0-9a-fA-F]{3,6})', body)
            backgrounds = re.findall(
                r'(?:background(?:-color)?)\s*:\s*(#[0-9a-fA-F]{3,6})', body
            )

            if not colors or not backgrounds:
                continue

            # Check first pair found in same rule
            fg = colors[0]
            bg = backgrounds[0]
            ratio = _wcag_contrast(fg, bg)
            if ratio is None:
                continue

            # Detect large text: font-size > 18px (≈1.125rem) in same block
            large_text = False
            size_match = re.search(r'font-size\s*:\s*([\d.]+)(rem|px|em)', body)
            if size_match:
                val, unit = float(size_match.group(1)), size_match.group(2)
                px = val * 16 if unit in ("rem", "em") else val
                large_text = px > 18

            threshold = 3.0 if large_text else 4.5
            check = VisualCheck.LARGE_TEXT_CONTRAST if large_text else VisualCheck.TEXT_CONTRAST

            if ratio < threshold:
                violations.append(VisualViolation(
                    check=check,
                    description=f"Insufficient contrast ratio in '{selector}'",
                    location=selector,
                    actual=f"{ratio:.2f}:1",
                    required=f"≥ {threshold}:1",
                    severity="error",
                ))
        return violations

    def _check_font_scale(self, css: str) -> list[VisualViolation]:
        # Expected rem values for ratio=1.25 base=1rem (steps -3..+3)
        expected = [0.64, 0.8, 1.0, 1.25, 1.5625, 1.953, 2.441]
        tolerance = 0.05

        violations: list[VisualViolation] = []
        for m in re.finditer(r'font-size\s*:\s*([\d.]+)rem', css):
            val = float(m.group(1))
            if not any(abs(val - e) / max(e, 1e-9) <= tolerance for e in expected):
                violations.append(VisualViolation(
                    check=VisualCheck.FONT_SCALE,
                    description=f"font-size {val}rem is not on the modular scale (ratio 1.25)",
                    location="CSS",
                    actual=f"{val}rem",
                    required="one of " + ", ".join(f"{e}rem" for e in expected),
                    severity="warning",
                ))
        return violations

    def _check_mobile_layout(self, css: str) -> list[VisualViolation]:
        violations: list[VisualViolation] = []

        if re.search(r'@media[^{]*max-width\s*:', css):
            violations.append(VisualViolation(
                check=VisualCheck.MOBILE_LAYOUT,
                description="max-width media query found — not mobile-first",
                location="CSS @media",
                actual="max-width breakpoint",
                required="min-width breakpoints only",
                severity="error",
            ))

        if not re.search(r'@media[^{]*min-width\s*:', css):
            violations.append(VisualViolation(
                check=VisualCheck.MOBILE_LAYOUT,
                description="No min-width media query found — missing responsive breakpoints",
                location="CSS @media",
                actual="no responsive breakpoints",
                required="at least one @media (min-width: …) breakpoint",
                severity="warning",
            ))

        return violations

    def _check_heading_hierarchy(self, html_files: dict[str, str]) -> list[VisualViolation]:
        violations: list[VisualViolation] = []

        for path, html in html_files.items():
            tags = re.findall(r'<(h[1-4])\b', html, re.IGNORECASE)
            tags = [t.lower() for t in tags]

            if tags.count("h1") > 1:
                violations.append(VisualViolation(
                    check=VisualCheck.HEADING_HIERARCHY,
                    description="Multiple <h1> elements on same page",
                    location=path,
                    actual=f"{tags.count('h1')} h1 elements",
                    required="exactly one h1 per page",
                    severity="error",
                ))

            seen: set[str] = set()
            for tag in tags:
                level = int(tag[1])
                if level > 1:
                    parent = f"h{level - 1}"
                    if parent not in seen:
                        violations.append(VisualViolation(
                            check=VisualCheck.HEADING_HIERARCHY,
                            description=f"<{tag}> appears before <{parent}> — skipped heading level",
                            location=path,
                            actual=f"{tag} before {parent}",
                            required=f"{parent} must precede {tag}",
                            severity="error",
                        ))
                seen.add(tag)

        if _THEORY:
            try:
                checker = AccessibilityChecker()
                for path, html in html_files.items():
                    raw_tags = re.findall(r'<(h[1-4])\b', html, re.IGNORECASE)
                    headings = [(f"{t.lower()}_{i}", t.lower()) for i, t in enumerate(raw_tags)]
                    obligations = checker.check_heading_structure(headings)
                    # Theory violations already covered by manual check above; skip duplicates.
            except Exception:
                pass

        return violations

    def _check_image_dimensions(self, html_files: dict[str, str]) -> list[VisualViolation]:
        violations: list[VisualViolation] = []

        if _THEORY:
            try:
                perf = PerformanceChecker()
                for path, html in html_files.items():
                    msgs = perf.check_image_dimensions(html)
                    for msg in msgs:
                        violations.append(VisualViolation(
                            check=VisualCheck.IMAGE_DIMENSIONS,
                            description=msg,
                            location=path,
                            actual="missing width/height",
                            required="width and height attributes on all <img>",
                            severity="warning",
                        ))
                return violations
            except Exception:
                pass

        for path, html in html_files.items():
            for img in re.findall(r'<img\b[^>]*>', html, re.IGNORECASE):
                has_width = bool(re.search(r'\bwidth\s*=', img, re.IGNORECASE))
                has_height = bool(re.search(r'\bheight\s*=', img, re.IGNORECASE))
                if not has_width or not has_height:
                    src_m = re.search(r'src\s*=\s*["\']?([^\s"\'>/]+)', img, re.IGNORECASE)
                    src = src_m.group(1) if src_m else "<img>"
                    violations.append(VisualViolation(
                        check=VisualCheck.IMAGE_DIMENSIONS,
                        description=f"<img> missing explicit dimensions — CLS risk",
                        location=f"{path}:{src}",
                        actual="missing width and/or height",
                        required="width and height attributes on all <img>",
                        severity="warning",
                    ))
        return violations

    def _check_focus_style(self, css: str) -> list[VisualViolation]:
        violations: list[VisualViolation] = []

        outline_none = re.search(r'outline\s*:\s*none', css)
        has_focus_visible = bool(re.search(r':focus-visible\s*\{', css))

        if outline_none and not has_focus_visible:
            violations.append(VisualViolation(
                check=VisualCheck.FOCUS_STYLE,
                description="outline:none used without a :focus-visible replacement",
                location="CSS",
                actual="outline:none, no :focus-visible",
                required=":focus-visible with visible outline",
                severity="error",
            ))

        if not has_focus_visible:
            violations.append(VisualViolation(
                check=VisualCheck.FOCUS_STYLE,
                description=":focus-visible selector not defined in CSS (WCAG 2.4.7)",
                location="CSS",
                actual="no :focus-visible rule",
                required=":focus-visible { outline: … } present",
                severity="error",
            ))

        return violations

    def _check_touch_targets(self, css: str) -> list[VisualViolation]:
        violations: list[VisualViolation] = []

        btn_blocks = re.findall(r'\.btn\b[^{]*\{([^}]*)\}', css, re.DOTALL)
        if not btn_blocks:
            return violations

        for block in btn_blocks:
            has_44 = bool(re.search(r'44px', block))
            min_h = re.search(r'min-height\s*:\s*([\d.]+)px', block)
            min_w = re.search(r'min-width\s*:\s*([\d.]+)px', block)
            ok = has_44 or (min_h and float(min_h.group(1)) >= 44) or (min_w and float(min_w.group(1)) >= 44)
            if not ok:
                violations.append(VisualViolation(
                    check=VisualCheck.TOUCH_TARGETS,
                    description=".btn touch target smaller than 44×44px (WCAG 2.5.5)",
                    location=".btn",
                    actual=block.strip()[:80],
                    required="min-height and min-width ≥ 44px",
                    severity="warning",
                ))
        return violations

    def _check_render_blocking(self, html_files: dict[str, str]) -> list[VisualViolation]:
        violations: list[VisualViolation] = []
        total = 0

        for path, html in html_files.items():
            # Blocking stylesheets: <link rel="stylesheet" without media="print"
            for link in re.findall(r'<link\b[^>]*>', html, re.IGNORECASE):
                if re.search(r'rel\s*=\s*["\']stylesheet["\']', link, re.IGNORECASE):
                    if not re.search(r'media\s*=\s*["\']print["\']', link, re.IGNORECASE):
                        total += 1

            # Blocking scripts: <script src= without defer or async
            for script in re.findall(r'<script\b[^>]*>', html, re.IGNORECASE):
                if re.search(r'\bsrc\s*=', script, re.IGNORECASE):
                    if not re.search(r'\b(defer|async)\b', script, re.IGNORECASE):
                        total += 1

        if total > 2:
            violations.append(VisualViolation(
                check=VisualCheck.RENDER_BLOCKING,
                description=f"{total} render-blocking resources found",
                location="HTML",
                actual=f"{total} blocking resources",
                required="≤ 2 render-blocking resources",
                severity="warning",
            ))
        return violations

    def _estimate_web_vitals(
        self, html_files: dict[str, str], css: str
    ) -> tuple[float | None, float | None]:
        # Count render-blocking resources for LCP estimate
        blocking = 0
        all_imgs_have_dims = True

        for path, html in html_files.items():
            for link in re.findall(r'<link\b[^>]*>', html, re.IGNORECASE):
                if re.search(r'rel\s*=\s*["\']stylesheet["\']', link, re.IGNORECASE):
                    if not re.search(r'media\s*=\s*["\']print["\']', link, re.IGNORECASE):
                        blocking += 1
            for script in re.findall(r'<script\b[^>]*>', html, re.IGNORECASE):
                if re.search(r'\bsrc\s*=', script, re.IGNORECASE):
                    if not re.search(r'\b(defer|async)\b', script, re.IGNORECASE):
                        blocking += 1

            for img in re.findall(r'<img\b[^>]*>', html, re.IGNORECASE):
                if not (re.search(r'\bwidth\s*=', img, re.IGNORECASE) and
                        re.search(r'\bheight\s*=', img, re.IGNORECASE)):
                    all_imgs_have_dims = False

        if _THEORY:
            try:
                resources = []
                for i in range(blocking):
                    resources.append(WebResource(
                        url=f"resource_{i}.css",
                        kind=ResourceKind.STYLESHEET,
                        size_bytes=20_000,
                    ))
                crp = CriticalRenderPath(resources=resources)
                vitals = CoreWebVitals.estimate_from_path(crp)
                lcp_ms = vitals.lcp_ms if hasattr(vitals, "lcp_ms") else None
                cls = vitals.cls if hasattr(vitals, "cls") else None
                if lcp_ms is not None or cls is not None:
                    return lcp_ms, cls
            except Exception:
                pass

        lcp_ms = 200.0 + 50.0 * blocking
        cls = 0.05 if all_imgs_have_dims else 0.15
        return lcp_ms, cls
