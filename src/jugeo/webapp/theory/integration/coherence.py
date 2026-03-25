"""Master cross-fiber coherence checker for web technology fibers.

Verifies consistency across: HTML↔CSS, HTML↔JS, CSS↔JS, client↔server,
template↔Python context, security, and accessibility concerns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jugeo.geometry.descent import DescentObstruction, DescentResult, LocalSection

__all__ = [
    "CoherenceKind",
    "CoherenceViolation",
    "HTMLCSSCoherenceChecker",
    "HTMLJSCoherenceChecker",
    "TemplateContextCoherenceChecker",
    "ClientServerCoherenceChecker",
    "SecurityCoherenceChecker",
    "WebAppCoherenceReport",
    "WebAppCoherenceEngine",
]


# ---------------------------------------------------------------------------
# CoherenceKind
# ---------------------------------------------------------------------------


class CoherenceKind(str, Enum):
    HTML_CSS = "html_css"
    HTML_JS = "html_js"
    CSS_JS = "css_js"
    CLIENT_SERVER = "client_server"
    TEMPLATE_CONTEXT = "template_context"
    SQL_ORM = "sql_orm"
    JS_PY_TYPE = "js_py_type"
    ACCESSIBILITY = "accessibility"
    SECURITY = "security"
    PERFORMANCE = "performance"


# ---------------------------------------------------------------------------
# CoherenceViolation
# ---------------------------------------------------------------------------


@dataclass
class CoherenceViolation:
    kind: CoherenceKind
    description: str
    source_location: str
    target_location: str
    severity: str = "error"
    fix_hint: str = ""


# ---------------------------------------------------------------------------
# HTMLCSSCoherenceChecker
# ---------------------------------------------------------------------------


class HTMLCSSCoherenceChecker:
    """Check consistency between CSS selectors and HTML structure."""

    def _parse_simple_selector(
        self, selector: str
    ) -> tuple[str | None, set[str], str | None]:
        """Parse a simple CSS selector into (tag, classes, id).

        Handles compound selectors like ``div.foo#bar`` but ignores
        combinators and pseudo-classes/elements — only the first simple
        selector token is analysed.
        """
        # Strip pseudo-classes and pseudo-elements
        selector = re.sub(r'::?[\w-]+(\([^)]*\))?', '', selector)
        # Take only the first simple selector (before any combinator)
        simple = re.split(r'[\s>~+]', selector.strip())[0]

        tag: str | None = None
        classes: set[str] = set()
        element_id: str | None = None

        # Extract tag (leading identifier before any . or #)
        tag_match = re.match(r'^([a-zA-Z][a-zA-Z0-9-]*)', simple)
        if tag_match:
            tag = tag_match.group(1).lower()
            simple = simple[tag_match.end():]

        # Extract id
        id_match = re.search(r'#([a-zA-Z_][\w-]*)', simple)
        if id_match:
            element_id = id_match.group(1)

        # Extract classes
        for cls_match in re.finditer(r'\.([a-zA-Z_][\w-]*)', simple):
            classes.add(cls_match.group(1))

        return tag, classes, element_id

    def check(
        self,
        css_selectors: list[str],
        html_element_classes: set[str],
        html_element_ids: set[str],
        html_tags: set[str],
    ) -> list[CoherenceViolation]:
        """Return violations for CSS selectors that reference absent HTML targets."""
        violations: list[CoherenceViolation] = []

        for selector in css_selectors:
            tag, classes, element_id = self._parse_simple_selector(selector)

            # Dead class references
            for cls in classes:
                if cls not in html_element_classes:
                    violations.append(
                        CoherenceViolation(
                            kind=CoherenceKind.HTML_CSS,
                            description=(
                                f"CSS selector '{selector}' references class "
                                f"'.{cls}' not found in HTML"
                            ),
                            source_location=f"css:{selector}",
                            target_location=f"html:.{cls}",
                            severity="warning",
                            fix_hint=(
                                f"Add class='{cls}' to an HTML element or "
                                f"remove the CSS rule for '.{cls}'"
                            ),
                        )
                    )

            # Dead id reference
            if element_id is not None and element_id not in html_element_ids:
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.HTML_CSS,
                        description=(
                            f"CSS selector '{selector}' references id "
                            f"'#{element_id}' not found in HTML"
                        ),
                        source_location=f"css:{selector}",
                        target_location=f"html:#{element_id}",
                        severity="warning",
                        fix_hint=(
                            f"Add id='{element_id}' to an HTML element or "
                            f"remove the CSS rule for '#{element_id}'"
                        ),
                    )
                )

            # Dead tag reference (only flag bare tag selectors with no
            # class/id qualifiers, to avoid excessive noise)
            if tag is not None and not classes and element_id is None:
                if tag not in html_tags:
                    violations.append(
                        CoherenceViolation(
                            kind=CoherenceKind.HTML_CSS,
                            description=(
                                f"CSS selector '{selector}' targets tag "
                                f"'<{tag}>' not found in HTML"
                            ),
                            source_location=f"css:{selector}",
                            target_location=f"html:<{tag}>",
                            severity="warning",
                            fix_hint=(
                                f"Add a '<{tag}>' element to the HTML or "
                                f"remove the CSS rule for '{tag}'"
                            ),
                        )
                    )

        return violations


# ---------------------------------------------------------------------------
# HTMLJSCoherenceChecker
# ---------------------------------------------------------------------------


class HTMLJSCoherenceChecker:
    """Check consistency between JavaScript DOM queries and HTML structure."""

    # Patterns for common DOM query methods
    _GET_BY_ID = re.compile(
        r'document\.getElementById\(\s*["\']([^"\']+)["\']\s*\)'
    )
    _QUERY_SELECTOR = re.compile(
        r'document\.querySelector(?:All)?\(\s*["\']([^"\']+)["\']\s*\)'
    )
    _GET_BY_CLASS = re.compile(
        r'document\.getElementsByClassName\(\s*["\']([^"\']+)["\']\s*\)'
    )

    def _extract_selector_from_query(self, query: str) -> str | None:
        """Extract the CSS selector or identifier string from a DOM query call."""
        for pattern in (self._GET_BY_ID, self._QUERY_SELECTOR, self._GET_BY_CLASS):
            m = pattern.search(query)
            if m:
                return m.group(1)
        return None

    def check(
        self,
        js_dom_queries: list[str],
        html_element_ids: set[str],
        html_element_classes: set[str],
    ) -> list[CoherenceViolation]:
        """Return violations for JS DOM queries that reference absent HTML elements."""
        violations: list[CoherenceViolation] = []

        for query in js_dom_queries:
            # --- getElementById ---
            for m in self._GET_BY_ID.finditer(query):
                elem_id = m.group(1)
                if elem_id not in html_element_ids:
                    violations.append(
                        CoherenceViolation(
                            kind=CoherenceKind.HTML_JS,
                            description=(
                                f"JS queries getElementById('{elem_id}') "
                                f"but no HTML element has id='{elem_id}'"
                            ),
                            source_location=f"js:{query[:60]}",
                            target_location=f"html:#{elem_id}",
                            severity="error",
                            fix_hint="Add element or remove JS query",
                        )
                    )

            # --- getElementsByClassName ---
            for m in self._GET_BY_CLASS.finditer(query):
                cls = m.group(1)
                if cls not in html_element_classes:
                    violations.append(
                        CoherenceViolation(
                            kind=CoherenceKind.HTML_JS,
                            description=(
                                f"JS queries getElementsByClassName('{cls}') "
                                f"but no HTML element has class='{cls}'"
                            ),
                            source_location=f"js:{query[:60]}",
                            target_location=f"html:.{cls}",
                            severity="error",
                            fix_hint="Add element or remove JS query",
                        )
                    )

            # --- querySelector / querySelectorAll ---
            for m in self._QUERY_SELECTOR.finditer(query):
                selector = m.group(1)
                self._check_query_selector(
                    selector, query, html_element_ids, html_element_classes, violations
                )

        return violations

    def _check_query_selector(
        self,
        selector: str,
        raw_query: str,
        html_element_ids: set[str],
        html_element_classes: set[str],
        violations: list[CoherenceViolation],
    ) -> None:
        """Validate a querySelector/querySelectorAll selector string."""
        # Check id selectors
        for id_match in re.finditer(r'#([a-zA-Z_][\w-]*)', selector):
            elem_id = id_match.group(1)
            if elem_id not in html_element_ids:
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.HTML_JS,
                        description=(
                            f"JS querySelector('{selector}') references "
                            f"id '#{elem_id}' not found in HTML"
                        ),
                        source_location=f"js:{raw_query[:60]}",
                        target_location=f"html:#{elem_id}",
                        severity="error",
                        fix_hint="Add element or remove JS query",
                    )
                )

        # Check class selectors
        for cls_match in re.finditer(r'\.([a-zA-Z_][\w-]*)', selector):
            cls = cls_match.group(1)
            if cls not in html_element_classes:
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.HTML_JS,
                        description=(
                            f"JS querySelector('{selector}') references "
                            f"class '.{cls}' not found in HTML"
                        ),
                        source_location=f"js:{raw_query[:60]}",
                        target_location=f"html:.{cls}",
                        severity="error",
                        fix_hint="Add element or remove JS query",
                    )
                )


# ---------------------------------------------------------------------------
# TemplateContextCoherenceChecker
# ---------------------------------------------------------------------------


class TemplateContextCoherenceChecker:
    """Check consistency between template variable usage and Python context."""

    def check(
        self,
        template_vars_used: set[str],
        context_vars_provided: set[str],
        template_name: str,
    ) -> list[CoherenceViolation]:
        """Return violations for missing or unused template context variables."""
        violations: list[CoherenceViolation] = []

        # Missing: template expects a variable the context doesn't supply
        for var in sorted(template_vars_used - context_vars_provided):
            violations.append(
                CoherenceViolation(
                    kind=CoherenceKind.TEMPLATE_CONTEXT,
                    description=(
                        f"Template '{template_name}' uses variable "
                        f"'{{{{ {var} }}}}' but it is not provided in context"
                    ),
                    source_location=f"templates/{template_name}",
                    target_location="view:context",
                    severity="error",
                    fix_hint=(
                        f"Add '{var}' to the context dict passed to render()"
                    ),
                )
            )

        # Unused: context provides a variable the template never references
        for var in sorted(context_vars_provided - template_vars_used):
            violations.append(
                CoherenceViolation(
                    kind=CoherenceKind.TEMPLATE_CONTEXT,
                    description=(
                        f"Context variable '{var}' is provided to "
                        f"'{template_name}' but never used in the template"
                    ),
                    source_location="view:context",
                    target_location=f"templates/{template_name}",
                    severity="info",
                    fix_hint=(
                        f"Remove '{var}' from context if it is no longer needed"
                    ),
                )
            )

        return violations


# ---------------------------------------------------------------------------
# ClientServerCoherenceChecker
# ---------------------------------------------------------------------------


class ClientServerCoherenceChecker:
    """Check consistency between client fetch URLs and server route patterns."""

    # Flask/Django-style path parameters: <int:id>, <str:slug>, <uuid:pk>, <id>
    _PARAM_RE = re.compile(r'<(?:[a-zA-Z_]\w*:)?([a-zA-Z_]\w*)>')

    def _url_matches_pattern(self, url: str, pattern: str) -> bool:
        """Return True if *url* matches a server route *pattern*.

        Handles Flask/Django angle-bracket path parameters by converting them
        to a ``[^/]+`` wildcard before matching.  Query strings on the URL are
        ignored during matching.
        """
        # Strip query string from the URL under test
        url_path = url.split('?')[0].rstrip('/')

        # Build a regex from the route pattern
        regex = self._PARAM_RE.sub(r'[^/]+', pattern)
        regex = regex.rstrip('/') + r'(/.*)?$'
        return bool(re.fullmatch(regex, url_path))

    def check(
        self,
        client_fetch_urls: list[str],
        server_route_patterns: list[str],
    ) -> list[CoherenceViolation]:
        """Return violations for client fetch URLs with no matching server route."""
        violations: list[CoherenceViolation] = []

        for url in client_fetch_urls:
            matched = any(
                self._url_matches_pattern(url, pattern)
                for pattern in server_route_patterns
            )
            if not matched:
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.CLIENT_SERVER,
                        description=(
                            f"Client fetches '{url}' but no server route matches"
                        ),
                        source_location=f"js:fetch('{url}')",
                        target_location="server:routes",
                        severity="error",
                        fix_hint=(
                            f"Add a server route for '{url}' or correct the "
                            f"fetch URL on the client"
                        ),
                    )
                )

        return violations


# ---------------------------------------------------------------------------
# SecurityCoherenceChecker
# ---------------------------------------------------------------------------


class SecurityCoherenceChecker:
    """Check security-relevant coherence properties of a web application."""

    # Contexts where raw interpolation is dangerous
    _DANGEROUS_CONTEXTS = {"html", "attr", "js", "url", "css"}

    def check_xss_risks(
        self,
        template_vars_unescaped: list[str],
        output_contexts: dict[str, str],
    ) -> list[CoherenceViolation]:
        """Return XSS-risk violations for unescaped variables in dangerous contexts.

        ``output_contexts`` maps variable name → output context string
        (``"html"``, ``"attr"``, ``"js"``, ``"url"``, ``"css"``).
        """
        violations: list[CoherenceViolation] = []

        for var in template_vars_unescaped:
            context = output_contexts.get(var, "html")

            if context == "html":
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.SECURITY,
                        description=(
                            f"Variable '{var}' is output unescaped into HTML "
                            f"context — potential XSS"
                        ),
                        source_location=f"template:{var}",
                        target_location="html:body",
                        severity="error",
                        fix_hint=(
                            f"Use autoescaping or explicitly escape '{var}' "
                            f"with |e / html.escape() before rendering"
                        ),
                    )
                )
            elif context == "js":
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.SECURITY,
                        description=(
                            f"Variable '{var}' is interpolated into a <script> "
                            f"block without JS-safe encoding — potential XSS"
                        ),
                        source_location=f"template:{var}",
                        target_location="html:<script>",
                        severity="error",
                        fix_hint=(
                            f"Use json.dumps() / tojson filter to safely "
                            f"embed '{var}' inside JavaScript"
                        ),
                    )
                )
            elif context in self._DANGEROUS_CONTEXTS:
                violations.append(
                    CoherenceViolation(
                        kind=CoherenceKind.SECURITY,
                        description=(
                            f"Variable '{var}' is output unescaped into "
                            f"'{context}' context — potential injection"
                        ),
                        source_location=f"template:{var}",
                        target_location=f"html:{context}",
                        severity="error",
                        fix_hint=(
                            f"Apply context-appropriate escaping for '{context}' "
                            f"before outputting '{var}'"
                        ),
                    )
                )

        return violations

    def check_csrf_protection(
        self,
        form_methods: list[str],
        has_csrf_token: bool,
    ) -> list[CoherenceViolation]:
        """Return a CSRF violation if any POST form lacks a CSRF token."""
        violations: list[CoherenceViolation] = []

        state_changing = {m.upper() for m in form_methods} & {"POST", "PUT", "PATCH", "DELETE"}
        if state_changing and not has_csrf_token:
            methods_str = ", ".join(sorted(state_changing))
            violations.append(
                CoherenceViolation(
                    kind=CoherenceKind.SECURITY,
                    description=(
                        f"Form(s) with state-changing method(s) "
                        f"[{methods_str}] lack CSRF token protection"
                    ),
                    source_location="html:form",
                    target_location="server:csrf",
                    severity="error",
                    fix_hint=(
                        "Add {% csrf_token %} / {{ csrf_token() }} inside "
                        "every state-changing form, or use a CSRF middleware"
                    ),
                )
            )

        return violations


# ---------------------------------------------------------------------------
# WebAppCoherenceReport
# ---------------------------------------------------------------------------


@dataclass
class WebAppCoherenceReport:
    violations: list[CoherenceViolation]
    checked_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z"
    )

    def errors(self) -> list[CoherenceViolation]:
        return [v for v in self.violations if v.severity == "error"]

    def warnings(self) -> list[CoherenceViolation]:
        return [v for v in self.violations if v.severity == "warning"]

    def by_kind(self, kind: CoherenceKind) -> list[CoherenceViolation]:
        return [v for v in self.violations if v.kind == kind]

    def to_descent_result(self) -> DescentResult:
        """Map report to a DescentResult.

        Success when there are no errors; failure with a DescentObstruction
        whose ``coordinate`` names the dominant violation kind.
        """
        from jugeo.geometry.descent import (  # lazy
            DescentObstruction,
            DescentResult,
            GlobalSection,
        )

        errors = self.errors()
        if not errors:
            section = GlobalSection(
                coordinate="webapp_coherence",
                merged_judgment={"coherent": True, "checked_at": self.checked_at},
            )
            return DescentResult(section=section)

        # Choose the most common error kind as the primary coordinate
        kind_counts: dict[str, int] = {}
        for v in errors:
            kind_counts[v.kind.value] = kind_counts.get(v.kind.value, 0) + 1
        primary_kind = max(kind_counts, key=lambda k: kind_counts[k])

        obstruction = DescentObstruction(coordinate=primary_kind)
        return DescentResult(obstruction=obstruction)

    def summary(self) -> str:
        """Human-readable one-line summary of the report."""
        n_errors = len(self.errors())
        n_warnings = len(self.warnings())
        # Unique source files across all violations
        files: set[str] = set()
        for v in self.violations:
            loc = v.source_location.split(":")[0]
            if loc:
                files.add(loc)
        n_files = len(files)
        return (
            f"{n_errors} error{'s' if n_errors != 1 else ''}, "
            f"{n_warnings} warning{'s' if n_warnings != 1 else ''} "
            f"across {n_files} file{'s' if n_files != 1 else ''}"
        )


# ---------------------------------------------------------------------------
# WebAppCoherenceEngine
# ---------------------------------------------------------------------------


class WebAppCoherenceEngine:
    """Orchestrate all coherence checkers and aggregate results."""

    def __init__(self) -> None:
        self._html_css = HTMLCSSCoherenceChecker()
        self._html_js = HTMLJSCoherenceChecker()
        self._template = TemplateContextCoherenceChecker()
        self._client_server = ClientServerCoherenceChecker()
        self._security = SecurityCoherenceChecker()

    def run_all(
        self,
        html_css: dict,
        html_js: dict,
        template: dict,
        client_server: dict,
        security: dict,
    ) -> WebAppCoherenceReport:
        """Run all checkers and return an aggregated report.

        Each dict carries keyword arguments for the corresponding checker:

        * ``html_css`` → :meth:`HTMLCSSCoherenceChecker.check`
          Keys: ``css_selectors``, ``html_element_classes``,
          ``html_element_ids``, ``html_tags``

        * ``html_js`` → :meth:`HTMLJSCoherenceChecker.check`
          Keys: ``js_dom_queries``, ``html_element_ids``,
          ``html_element_classes``

        * ``template`` → :meth:`TemplateContextCoherenceChecker.check`
          Keys: ``template_vars_used``, ``context_vars_provided``,
          ``template_name``

        * ``client_server`` → :meth:`ClientServerCoherenceChecker.check`
          Keys: ``client_fetch_urls``, ``server_route_patterns``

        * ``security`` — supports two sub-keys:

          * ``xss``: dict with ``template_vars_unescaped``,
            ``output_contexts`` → :meth:`SecurityCoherenceChecker.check_xss_risks`
          * ``csrf``: dict with ``form_methods``, ``has_csrf_token``
            → :meth:`SecurityCoherenceChecker.check_csrf_protection`
        """
        all_violations: list[CoherenceViolation] = []

        if html_css:
            all_violations.extend(self._html_css.check(**html_css))

        if html_js:
            all_violations.extend(self._html_js.check(**html_js))

        if template:
            all_violations.extend(self._template.check(**template))

        if client_server:
            all_violations.extend(self._client_server.check(**client_server))

        if security:
            xss_inputs = security.get("xss")
            if xss_inputs:
                all_violations.extend(
                    self._security.check_xss_risks(**xss_inputs)
                )
            csrf_inputs = security.get("csrf")
            if csrf_inputs:
                all_violations.extend(
                    self._security.check_csrf_protection(**csrf_inputs)
                )

        return WebAppCoherenceReport(violations=all_violations)
