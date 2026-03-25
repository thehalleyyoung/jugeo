"""Descent engine for verifying generated HTML/CSS/JS satisfy all obligations.

Sheaf-theoretic interpretation
------------------------------
Generation descent is the **H⁰ computation** for the web-application
sheaf.  Given generated HTML, CSS, and JS code, descent verification
checks the **cocycle condition** across the three language fibres:

* **Structural descent** — the HTML fibre is well-formed, semantic, and
  satisfies document-structure obligations.
* **Visual descent** — the CSS fibre covers all components, provides
  responsive breakpoints, and satisfies layout obligations.
* **Behavioral descent** — the JS fibre wires all interactive elements,
  manages routing, and handles error / loading states.
* **Coherence** — the overlap condition between fibres.  A CSS selector
  is *coherent* with HTML if the targeted elements exist.  A JS
  ``querySelector`` is *coherent* with HTML if the targeted elements
  exist.  The full descent result is the H⁰ computation — successful
  descent means all fibres glue into a working application.
* **Coverage** — every view and every component obligation is satisfied
  somewhere in the generated code.

When all checks pass the generated code is a **global section** of the
web-application sheaf — a coherent, complete, working application.

This module is domain-agnostic and works for any web application that
is expressed as a single HTML + CSS + JS bundle (SPA pattern).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "GenerationDescentResult",
    "GenerationDescentEngine",
    "CoherenceChecker",
]


# ═══════════════════════════════════════════════════════════════════════
# §1  GenerationDescentResult — outcome of a full descent check
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class GenerationDescentResult:
    """Complete result of running all descent checks on generated code.

    ``passed`` is ``True`` only when *every* obstruction list is empty.
    Each list contains human-readable obstruction messages that explain
    what failed and where.

    Attributes
    ----------
    passed : bool
        ``True`` if every check passed with zero obstructions.
    visual_obstructions : list[str]
        CSS fibre failures — missing styles, no responsive rules, etc.
    behavioral_obstructions : list[str]
        JS fibre failures — missing handlers, broken router, etc.
    structural_obstructions : list[str]
        HTML fibre failures — malformed markup, missing semantics, etc.
    coherence_obstructions : list[str]
        Cross-fibre mismatches — HTML class with no CSS, JS selector
        targeting nonexistent HTML, etc.
    coverage_obstructions : list[str]
        Missing views, missing components, incomplete routes.
    """

    passed: bool = True
    visual_obstructions: list[str] = field(default_factory=list)
    behavioral_obstructions: list[str] = field(default_factory=list)
    structural_obstructions: list[str] = field(default_factory=list)
    coherence_obstructions: list[str] = field(default_factory=list)
    coverage_obstructions: list[str] = field(default_factory=list)

    @property
    def total_obstructions(self) -> int:
        """Total number of obstructions across all categories."""
        return (
            len(self.visual_obstructions)
            + len(self.behavioral_obstructions)
            + len(self.structural_obstructions)
            + len(self.coherence_obstructions)
            + len(self.coverage_obstructions)
        )

    @property
    def obstruction_summary(self) -> dict[str, int]:
        """Counts per category."""
        return {
            "visual": len(self.visual_obstructions),
            "behavioral": len(self.behavioral_obstructions),
            "structural": len(self.structural_obstructions),
            "coherence": len(self.coherence_obstructions),
            "coverage": len(self.coverage_obstructions),
        }

    def __repr__(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return (
            f"GenerationDescentResult({status}, "
            f"obstructions={self.total_obstructions})"
        )


# ═══════════════════════════════════════════════════════════════════════
# §2  CoherenceChecker — fibre-overlap analysis utilities
# ═══════════════════════════════════════════════════════════════════════

class CoherenceChecker:
    """Static utilities for analysing coherence between HTML, CSS, and JS.

    Coherence is the overlap (cocycle) condition between the three
    language fibres.  For a CSS selector to be *coherent* with HTML,
    the elements it targets must actually exist in the HTML.  For a JS
    ``querySelector`` call to be *coherent* with HTML, the targeted
    element must exist.

    All methods are stateless and operate on raw source strings.
    """

    # ── extraction ────────────────────────────────────────────────────

    @staticmethod
    def html_classes(html: str) -> set[str]:
        """Extract all CSS class names from HTML ``class="..."`` attributes."""
        classes: set[str] = set()
        for match in re.finditer(r'class="([^"]*)"', html):
            for cls in match.group(1).split():
                if cls:
                    classes.add(cls)
        # Also handle single-quoted attributes.
        for match in re.finditer(r"class='([^']*)'", html):
            for cls in match.group(1).split():
                if cls:
                    classes.add(cls)
        return classes

    @staticmethod
    def css_selectors(css: str) -> set[str]:
        """Extract all class selectors (``.name``) from CSS source.

        Returns class names *without* the leading dot.
        """
        selectors: set[str] = set()
        # Match .classname in selector context (before {).
        # We strip comments first.
        cleaned = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
        for match in re.finditer(r'\.([a-zA-Z_][\w-]*)', cleaned):
            selectors.add(match.group(1))
        return selectors

    @staticmethod
    def js_query_selectors(js: str) -> set[str]:
        """Extract all ``querySelector`` / ``querySelectorAll`` targets from JS.

        Returns the raw selector strings passed to these methods.
        """
        targets: set[str] = set()
        pattern = r'querySelectorAll?\(\s*[\'"]([^\'"]+)[\'"]\s*\)'
        for match in re.finditer(pattern, js):
            targets.add(match.group(1))
        return targets

    @staticmethod
    def js_get_element_by_id(js: str) -> set[str]:
        """Extract all ``getElementById`` targets from JS.

        Returns the id strings (without ``#`` prefix).
        """
        ids: set[str] = set()
        pattern = r'getElementById\(\s*[\'"]([^\'"]+)[\'"]\s*\)'
        for match in re.finditer(pattern, js):
            ids.add(match.group(1))
        return ids

    @staticmethod
    def html_ids(html: str) -> set[str]:
        """Extract all ``id="..."`` values from HTML."""
        ids: set[str] = set()
        for match in re.finditer(r'id="([^"]*)"', html):
            if match.group(1):
                ids.add(match.group(1))
        for match in re.finditer(r"id='([^']*)'", html):
            if match.group(1):
                ids.add(match.group(1))
        return ids

    @staticmethod
    def html_data_hooks(html: str) -> set[str]:
        """Extract all ``data-hook="..."`` values from HTML."""
        hooks: set[str] = set()
        for match in re.finditer(r'data-hook="([^"]*)"', html):
            if match.group(1):
                hooks.add(match.group(1))
        return hooks

    # ── cross-fibre analysis ──────────────────────────────────────────

    @staticmethod
    def orphan_css_selectors(html: str, css: str) -> set[str]:
        """CSS class selectors that target no element in the HTML.

        These are "orphan" styles — CSS rules that will never match
        anything in the generated markup.
        """
        html_cls = CoherenceChecker.html_classes(html)
        css_cls = CoherenceChecker.css_selectors(css)
        # Exclude CSS framework utility patterns that may be generated
        # dynamically (e.g. responsive prefixes).
        return css_cls - html_cls

    @staticmethod
    def orphan_js_selectors(html: str, js: str) -> set[str]:
        """JS ``querySelector`` targets that match no element in the HTML.

        A JS selector is orphaned if neither its class nor id target
        exists in the HTML.
        """
        html_cls = CoherenceChecker.html_classes(html)
        html_id_set = CoherenceChecker.html_ids(html)

        qs_targets = CoherenceChecker.js_query_selectors(js)
        gbi_targets = CoherenceChecker.js_get_element_by_id(js)

        orphans: set[str] = set()

        # Check querySelector targets.
        for selector in qs_targets:
            # Extract class and id references from the selector.
            classes_in_sel = set(re.findall(r'\.([a-zA-Z_][\w-]*)', selector))
            ids_in_sel = set(re.findall(r'#([a-zA-Z_][\w-]*)', selector))
            tags_in_sel = set(re.findall(r'^([a-zA-Z][\w-]*)', selector))

            # A selector is orphaned if none of its class/id targets exist.
            has_match = False
            if classes_in_sel & html_cls:
                has_match = True
            if ids_in_sel & html_id_set:
                has_match = True
            # Tag selectors (div, section, etc.) almost always match;
            # skip orphan check for pure tag selectors.
            if tags_in_sel and not classes_in_sel and not ids_in_sel:
                has_match = True
            # data-attribute selectors are hard to validate statically.
            if "[data-" in selector:
                has_match = True

            if not has_match:
                orphans.add(selector)

        # Check getElementById targets.
        for id_val in gbi_targets:
            if id_val not in html_id_set:
                orphans.add(f"#{id_val}")

        return orphans

    @staticmethod
    def unstyled_classes(html: str, css: str) -> set[str]:
        """HTML classes that have no matching CSS selector.

        These elements will receive no custom styling from the
        generated stylesheet.
        """
        html_cls = CoherenceChecker.html_classes(html)
        css_cls = CoherenceChecker.css_selectors(css)
        return html_cls - css_cls

    @staticmethod
    def unwired_hooks(html: str, js: str) -> set[str]:
        """``data-hook`` values in HTML with no corresponding JS handler.

        A hook is "unwired" if JS never references it via
        ``querySelector('[data-hook="..."]')`` or
        ``dataset.hook`` or a string literal matching the hook name.
        """
        hooks = CoherenceChecker.html_data_hooks(html)
        unwired: set[str] = set()
        for hook in hooks:
            # Check if JS references this hook in any common pattern.
            patterns = [
                f'data-hook="{hook}"',
                f"data-hook='{hook}'",
                f'[data-hook="{hook}"]',
                f"[data-hook='{hook}']",
                f'"{hook}"',
                f"'{hook}'",
            ]
            found = any(p in js for p in patterns)
            if not found:
                unwired.add(hook)
        return unwired


# ═══════════════════════════════════════════════════════════════════════
# §3  GenerationDescentEngine — the main descent verifier
# ═══════════════════════════════════════════════════════════════════════

class GenerationDescentEngine:
    """Descent engine for verifying generated HTML/CSS/JS.

    The engine takes the three generated source strings and runs
    structural, visual, behavioral, coherence, and coverage checks.
    Each check returns a list of obstruction messages.  The
    :meth:`full_descent` method runs all checks and returns a
    :class:`GenerationDescentResult`.

    Descent succeeds (the result passes) when all obstruction lists
    are empty — meaning the generated code forms a valid global
    section of the web-application sheaf.
    """

    def __init__(self, html: str, css: str, js: str) -> None:
        self._html = html
        self._css = css
        self._js = js
        self._checker = CoherenceChecker()

    # ── structural descent ────────────────────────────────────────────

    def check_structural_descent(self) -> list[str]:
        """Verify HTML structural obligations.

        Checks:
          1. Exactly one ``<!DOCTYPE html>`` declaration.
          2. Exactly one ``<html>`` root element.
          3. Presence of ``<head>`` and ``<body>``.
          4. Presence of ``<meta charset>`` in head.
          5. Presence of ``<title>`` in head.
          6. Semantic landmark elements (``<main>``, ``<nav>``,
             ``<header>`` or ``<footer>``).
          7. No duplicate ``id`` attributes.
          8. All ``<img>`` tags have ``alt`` attributes.
        """
        obstructions: list[str] = []
        html_lower = self._html.lower()

        # 1 — DOCTYPE
        doctype_count = len(re.findall(r'<!doctype\s+html\s*>', html_lower))
        if doctype_count == 0:
            obstructions.append("Missing <!DOCTYPE html> declaration")
        elif doctype_count > 1:
            obstructions.append(
                f"Multiple DOCTYPE declarations found ({doctype_count})"
            )

        # 2 — Single <html> root
        html_tags = re.findall(r'<html[\s>]', html_lower)
        if len(html_tags) == 0:
            obstructions.append("Missing <html> root element")
        elif len(html_tags) > 1:
            obstructions.append("Multiple <html> elements found")

        # 3 — <head> and <body>
        if '<head' not in html_lower:
            obstructions.append("Missing <head> element")
        if '<body' not in html_lower:
            obstructions.append("Missing <body> element")

        # 4 — <meta charset>
        if not re.search(r'<meta[^>]+charset\s*=', html_lower):
            obstructions.append("Missing <meta charset> in <head>")

        # 5 — <title>
        if '<title>' not in html_lower and '<title ' not in html_lower:
            obstructions.append("Missing <title> in <head>")

        # 6 — Semantic landmarks
        landmarks = {"<main", "<nav", "<header", "<footer"}
        found_landmarks = [lm for lm in landmarks if lm in html_lower]
        if len(found_landmarks) < 2:
            obstructions.append(
                f"Insufficient semantic landmarks (found {len(found_landmarks)}, "
                f"need ≥ 2 of main/nav/header/footer)"
            )

        # 7 — Duplicate ids
        all_ids = re.findall(r'id="([^"]*)"', self._html)
        all_ids += re.findall(r"id='([^']*)'", self._html)
        seen_ids: set[str] = set()
        for id_val in all_ids:
            if id_val in seen_ids:
                obstructions.append(f"Duplicate id attribute: '{id_val}'")
            seen_ids.add(id_val)

        # 8 — img alt attributes
        img_tags = re.finditer(r'<img\b([^>]*)/?>', self._html, re.IGNORECASE)
        for img in img_tags:
            attrs = img.group(1)
            if 'alt=' not in attrs.lower():
                obstructions.append(
                    f"<img> missing alt attribute near: "
                    f"...{img.group(0)[:60]}..."
                )

        return obstructions

    # ── visual descent ────────────────────────────────────────────────

    def check_visual_descent(self) -> list[str]:
        """Verify CSS visual obligations.

        Checks:
          1. CSS is non-empty and parses (has at least one rule block).
          2. At least one ``@media`` rule for responsive design.
          3. Box-sizing reset present (``box-sizing: border-box``).
          4. Body/root font and color declarations present.
          5. No ``!important`` overuse (warns if > 5 occurrences).
          6. Key component selectors present (navbar, footer if in HTML).
        """
        obstructions: list[str] = []
        css = self._css
        css_lower = css.lower()

        # 1 — Non-empty with at least one rule
        rule_blocks = re.findall(r'\{[^}]*\}', css)
        if not rule_blocks:
            obstructions.append("CSS contains no rule blocks")
            return obstructions  # No point checking further.

        # 2 — Responsive media queries
        media_queries = re.findall(r'@media\b', css_lower)
        if not media_queries:
            obstructions.append(
                "No @media queries found — responsive design may be missing"
            )

        # 3 — Box-sizing reset
        if 'box-sizing' not in css_lower:
            obstructions.append(
                "Missing box-sizing: border-box reset"
            )

        # 4 — Body/root font
        body_rule = re.search(r'(?:body|:root|html)\s*\{[^}]*font', css_lower)
        if not body_rule:
            obstructions.append(
                "No font declaration on body/:root/html"
            )

        # 5 — !important overuse
        important_count = css_lower.count('!important')
        if important_count > 5:
            obstructions.append(
                f"Excessive !important usage ({important_count} occurrences)"
            )

        # 6 — Key component selectors
        html_lower = self._html.lower()
        component_selectors = {
            "navbar": [".navbar"],
            "footer": [".footer", "footer"],
            "modal": [".modal"],
            "btn": [".btn"],
        }
        for component, selectors in component_selectors.items():
            # Only check if the component exists in HTML.
            if component in html_lower:
                found = any(sel in css_lower for sel in selectors)
                if not found:
                    obstructions.append(
                        f"HTML contains '{component}' but CSS has no "
                        f"matching selector ({selectors})"
                    )

        return obstructions

    # ── behavioral descent ────────────────────────────────────────────

    def check_behavioral_descent(self) -> list[str]:
        """Verify JS behavioral obligations.

        Checks:
          1. JS is non-empty.
          2. DOMContentLoaded or equivalent initialization.
          3. Router / hash-change handler if multiple views detected.
          4. Event listeners registered (addEventListener calls).
          5. Error handling present (try/catch or .catch).
          6. No console.log left in production code (warning).
        """
        obstructions: list[str] = []
        js = self._js
        js_lower = js.lower()

        # 1 — Non-empty
        if not js.strip():
            obstructions.append("JS source is empty")
            return obstructions

        # 2 — Initialization
        init_patterns = [
            'domcontentloaded',
            'window.onload',
            'document.ready',
            '(function(',         # IIFE
            '(() =>',             # Arrow IIFE
            'addEventListener',
        ]
        has_init = any(p in js_lower for p in init_patterns)
        if not has_init:
            obstructions.append(
                "No DOM initialization pattern found "
                "(DOMContentLoaded, window.onload, IIFE, etc.)"
            )

        # 3 — Router / hash-change
        # Detect if HTML has multiple view sections.
        view_sections = re.findall(
            r'(?:data-view|data-route|class="[^"]*view[^"]*")',
            self._html,
            re.IGNORECASE,
        )
        if len(view_sections) > 1:
            router_patterns = ['hashchange', 'popstate', 'navigate', 'router', 'route']
            has_router = any(p in js_lower for p in router_patterns)
            if not has_router:
                obstructions.append(
                    f"HTML has {len(view_sections)} view sections but JS "
                    f"has no router (hashchange/popstate/navigate)"
                )

        # 4 — Event listeners
        listener_count = js_lower.count('addeventlistener')
        if listener_count == 0:
            obstructions.append("No addEventListener calls found in JS")

        # 5 — Error handling
        error_patterns = ['try {', 'try{', '.catch(', '.catch (', 'onerror']
        has_error_handling = any(p in js_lower for p in error_patterns)
        if not has_error_handling:
            obstructions.append(
                "No error handling found (try/catch or .catch())"
            )

        # 6 — Console.log warning
        console_logs = len(re.findall(r'console\.log\(', js))
        if console_logs > 3:
            obstructions.append(
                f"Excessive console.log calls ({console_logs}) — "
                f"consider removing for production"
            )

        return obstructions

    # ── coherence ─────────────────────────────────────────────────────

    def check_coherence(self) -> list[str]:
        """Verify cross-fibre coherence (HTML ↔ CSS ↔ JS).

        This is the cocycle condition: the overlap between fibres must
        be consistent.

        Checks:
          1. HTML classes referenced in CSS (orphan CSS selectors).
          2. HTML classes with no CSS (unstyled classes — warning only
             for structural classes).
          3. JS querySelector targets exist in HTML.
          4. JS getElementById targets exist in HTML.
          5. data-hook values in HTML have JS handlers.
        """
        obstructions: list[str] = []

        # 1 — Orphan CSS selectors
        orphan_css = self._checker.orphan_css_selectors(self._html, self._css)
        # Filter out common framework/reset selectors.
        ignorable_css = {
            "clearfix", "sr-only", "visually-hidden",
            "container", "row", "col",
        }
        significant_orphans = orphan_css - ignorable_css
        if len(significant_orphans) > 10:
            obstructions.append(
                f"{len(significant_orphans)} CSS selectors target nonexistent "
                f"HTML elements (showing first 10): "
                f"{sorted(significant_orphans)[:10]}"
            )

        # 2 — Unstyled HTML classes
        unstyled = self._checker.unstyled_classes(self._html, self._css)
        # Many utility/state classes (is-*, has-*, js-*) are expected.
        significant_unstyled = {
            c for c in unstyled
            if not c.startswith(("is-", "has-", "js-", "no-"))
        }
        if len(significant_unstyled) > 15:
            obstructions.append(
                f"{len(significant_unstyled)} HTML classes have no CSS "
                f"styling (showing first 10): "
                f"{sorted(significant_unstyled)[:10]}"
            )

        # 3 — Orphan JS selectors
        orphan_js = self._checker.orphan_js_selectors(self._html, self._js)
        for sel in sorted(orphan_js):
            obstructions.append(
                f"JS targets '{sel}' but no matching element exists in HTML"
            )

        # 4 — Unwired data-hooks
        unwired = self._checker.unwired_hooks(self._html, self._js)
        for hook in sorted(unwired):
            obstructions.append(
                f"HTML data-hook='{hook}' has no JS handler"
            )

        return obstructions

    # ── coverage ──────────────────────────────────────────────────────

    def check_coverage(self, views: list[str] | None = None) -> list[str]:
        """Verify that all expected views and components are present.

        Parameters
        ----------
        views : list[str] | None
            Expected view ids.  If ``None``, the method attempts to
            auto-detect views from the HTML.

        Checks:
          1. Every expected view has a corresponding section/route in HTML.
          2. At least one ``<nav>`` element (navigation present).
          3. At least one ``<main>`` or content area.
          4. If views are provided, each view id appears as a
             ``data-view`` or ``id`` in the HTML.
        """
        obstructions: list[str] = []

        # Auto-detect views from HTML if not provided.
        if views is None:
            views = re.findall(r'data-view="([^"]*)"', self._html)
            if not views:
                views = re.findall(r'id="view-([^"]*)"', self._html)

        # 1 — Each view present in HTML
        html_views = set(re.findall(r'data-view="([^"]*)"', self._html))
        html_views.update(re.findall(r'id="view-([^"]*)"', self._html))
        # Also check for route-based sections.
        html_views.update(re.findall(r'data-route="([^"]*)"', self._html))

        for view_id in views:
            if view_id not in html_views:
                # Relaxed check: see if the view id appears anywhere.
                if view_id not in self._html:
                    obstructions.append(
                        f"View '{view_id}' has no section in generated HTML"
                    )

        # 2 — Navigation present
        if '<nav' not in self._html.lower():
            obstructions.append("No <nav> element found — navigation missing")

        # 3 — Content area
        if '<main' not in self._html.lower():
            obstructions.append("No <main> element found — content area missing")

        # 4 — View sections are non-empty
        for view_id in html_views:
            # Find the section for this view and check it has content.
            pattern = (
                rf'(?:data-view="{re.escape(view_id)}"|'
                rf'id="view-{re.escape(view_id)}")'
                rf'[^>]*>([^<]*(?:<(?!/(?:section|div))[^<]*)*)'
            )
            match = re.search(pattern, self._html, re.DOTALL)
            if match:
                content = match.group(1).strip()
                if len(content) < 10:
                    obstructions.append(
                        f"View '{view_id}' section appears empty or minimal"
                    )

        return obstructions

    # ── full descent ──────────────────────────────────────────────────

    def full_descent(self, views: list[str] | None = None) -> GenerationDescentResult:
        """Run all descent checks and return the combined result.

        This is the **H⁰ computation** — it determines whether the
        generated code forms a valid global section of the
        web-application sheaf.

        Parameters
        ----------
        views : list[str] | None
            Expected view ids for coverage checking.

        Returns
        -------
        GenerationDescentResult
            The complete descent result with all obstructions.
        """
        structural = self.check_structural_descent()
        visual = self.check_visual_descent()
        behavioral = self.check_behavioral_descent()
        coherence = self.check_coherence()
        coverage = self.check_coverage(views)

        passed = not any([structural, visual, behavioral, coherence, coverage])

        return GenerationDescentResult(
            passed=passed,
            structural_obstructions=structural,
            visual_obstructions=visual,
            behavioral_obstructions=behavioral,
            coherence_obstructions=coherence,
            coverage_obstructions=coverage,
        )

    # ── repair suggestions ────────────────────────────────────────────

    @staticmethod
    def repair_suggestions(result: GenerationDescentResult) -> list[str]:
        """Generate actionable repair suggestions from descent obstructions.

        Each suggestion maps an obstruction to a concrete fix that a
        code generator (or human) can apply.
        """
        suggestions: list[str] = []

        # Structural repairs
        for obs in result.structural_obstructions:
            if "DOCTYPE" in obs:
                suggestions.append(
                    "Add '<!DOCTYPE html>' as the first line of the HTML"
                )
            elif "<html>" in obs.lower() or "<html " in obs.lower():
                suggestions.append(
                    "Wrap all content in a single <html> root element"
                )
            elif "<head>" in obs.lower():
                suggestions.append(
                    "Add a <head> element with meta tags and title"
                )
            elif "<body>" in obs.lower():
                suggestions.append(
                    "Add a <body> element wrapping all visible content"
                )
            elif "charset" in obs.lower():
                suggestions.append(
                    "Add <meta charset=\"UTF-8\"> inside <head>"
                )
            elif "title" in obs.lower():
                suggestions.append(
                    "Add a <title> element inside <head>"
                )
            elif "landmark" in obs.lower():
                suggestions.append(
                    "Add semantic landmarks: <main>, <nav>, <header>, <footer>"
                )
            elif "Duplicate id" in obs:
                dup_id = re.search(r"'([^']+)'", obs)
                if dup_id:
                    suggestions.append(
                        f"Remove duplicate id='{dup_id.group(1)}' — "
                        f"each id must be unique"
                    )
            elif "alt" in obs.lower():
                suggestions.append(
                    "Add alt attributes to all <img> tags for accessibility"
                )
            else:
                suggestions.append(f"Fix structural issue: {obs}")

        # Visual repairs
        for obs in result.visual_obstructions:
            if "no rule blocks" in obs.lower():
                suggestions.append(
                    "Generate CSS with at least base styles for body, "
                    "typography, and components"
                )
            elif "@media" in obs:
                suggestions.append(
                    "Add @media queries for responsive breakpoints "
                    "(e.g. 768px, 1024px)"
                )
            elif "box-sizing" in obs.lower():
                suggestions.append(
                    "Add '*, *::before, *::after { box-sizing: border-box; }' "
                    "reset"
                )
            elif "font" in obs.lower():
                suggestions.append(
                    "Add font-family and font-size declarations on "
                    "body or :root"
                )
            elif "!important" in obs:
                suggestions.append(
                    "Reduce !important usage — use more specific selectors "
                    "instead"
                )
            elif "no matching selector" in obs.lower():
                component = re.search(r"'([^']+)'", obs)
                if component:
                    suggestions.append(
                        f"Add CSS rules for the '{component.group(1)}' "
                        f"component"
                    )
            else:
                suggestions.append(f"Fix visual issue: {obs}")

        # Behavioral repairs
        for obs in result.behavioral_obstructions:
            if "empty" in obs.lower():
                suggestions.append(
                    "Generate JS with initialization, event handlers, "
                    "and routing"
                )
            elif "initialization" in obs.lower():
                suggestions.append(
                    "Add document.addEventListener('DOMContentLoaded', "
                    "() => { ... }) wrapper"
                )
            elif "router" in obs.lower():
                suggestions.append(
                    "Add window.addEventListener('hashchange', ...) "
                    "for SPA routing"
                )
            elif "addEventListener" in obs:
                suggestions.append(
                    "Wire event listeners for interactive components "
                    "(buttons, forms, toggles)"
                )
            elif "error handling" in obs.lower():
                suggestions.append(
                    "Add try/catch blocks around async operations "
                    "and .catch() on promises"
                )
            elif "console.log" in obs.lower():
                suggestions.append(
                    "Remove or reduce console.log calls for production"
                )
            else:
                suggestions.append(f"Fix behavioral issue: {obs}")

        # Coherence repairs
        for obs in result.coherence_obstructions:
            if "CSS selectors target nonexistent" in obs:
                suggestions.append(
                    "Remove CSS rules that target elements not in the HTML, "
                    "or add the missing HTML elements"
                )
            elif "HTML classes have no CSS" in obs:
                suggestions.append(
                    "Add CSS rules for unstyled HTML classes, or remove "
                    "unused class attributes from HTML"
                )
            elif "JS targets" in obs and "no matching element" in obs:
                sel = re.search(r"'([^']+)'", obs)
                if sel:
                    suggestions.append(
                        f"JS references '{sel.group(1)}' but it doesn't "
                        f"exist in HTML — add the element or fix the selector"
                    )
            elif "data-hook" in obs and "no JS handler" in obs:
                hook = re.search(r"'([^']+)'", obs)
                if hook:
                    suggestions.append(
                        f"Add a JS handler for data-hook='{hook.group(1)}' "
                        f"(querySelector + addEventListener)"
                    )
            else:
                suggestions.append(f"Fix coherence issue: {obs}")

        # Coverage repairs
        for obs in result.coverage_obstructions:
            if "no section" in obs.lower():
                view = re.search(r"'([^']+)'", obs)
                if view:
                    suggestions.append(
                        f"Add a <section data-view=\"{view.group(1)}\"> "
                        f"block for the '{view.group(1)}' view"
                    )
            elif "<nav>" in obs.lower():
                suggestions.append(
                    "Add a <nav> element with navigation links"
                )
            elif "<main>" in obs.lower():
                suggestions.append(
                    "Add a <main> element as the primary content container"
                )
            elif "empty or minimal" in obs.lower():
                view = re.search(r"'([^']+)'", obs)
                if view:
                    suggestions.append(
                        f"Populate the '{view.group(1)}' view section "
                        f"with meaningful content"
                    )
            else:
                suggestions.append(f"Fix coverage issue: {obs}")

        return suggestions
