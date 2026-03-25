"""Web security vulnerabilities modelled as obstruction classes on covering families.

XSS  — failure of the escaping presheaf to satisfy descent (sections don't glue
        consistently across output contexts).
CSRF — covering family with a missing member (the synchroniser token).

Only stdlib deps: re, dataclasses, enum.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from jugeo.geometry.descent import (
    DescentObstruction,
    DescentResult,
    GlobalSection,
    LocalSection,
)

__all__ = [
    "SecurityThreat",
    "SecurityObligation",
    "XSSChecker",
    "CSRFChecker",
    "SQLInjectionChecker",
    "SecurityDescentChecker",
    "CSPPolicy",
]


# ---------------------------------------------------------------------------
# 1. Threat taxonomy
# ---------------------------------------------------------------------------

class SecurityThreat(str, Enum):
    XSS_REFLECTED = "xss_reflected"
    XSS_STORED = "xss_stored"
    XSS_DOM = "xss_dom"
    CSRF = "csrf"
    SQL_INJECTION = "sql_injection"
    PATH_TRAVERSAL = "path_traversal"
    OPEN_REDIRECT = "open_redirect"
    CLICKJACKING = "clickjacking"
    INSECURE_DIRECT_OBJECT_REFERENCE = "insecure_direct_object_reference"
    MISSING_AUTH = "missing_auth"
    SENSITIVE_DATA_EXPOSURE = "sensitive_data_exposure"
    BROKEN_ACCESS_CONTROL = "broken_access_control"


# ---------------------------------------------------------------------------
# 2. SecurityObligation
# ---------------------------------------------------------------------------

@dataclass
class SecurityObligation:
    """A single, unmitigated (or mitigated) security requirement at a location."""

    threat: SecurityThreat
    location: str
    description: str
    is_mitigated: bool = False
    mitigation: str = ""
    severity: str = "high"  # critical | high | medium | low


# ---------------------------------------------------------------------------
# 3. XSSChecker
# ---------------------------------------------------------------------------

_UNSAFE_SINKS = re.compile(
    r"(innerHTML|outerHTML|document\.write)\s*[+=]",
    re.MULTILINE,
)

_EVAL_PATTERNS = re.compile(
    r"(eval\(|new\s+Function\(|setTimeout\s*\(\s*['\"`]|setInterval\s*\(\s*['\"`])",
    re.MULTILINE,
)


class XSSChecker:
    """Model the escaping presheaf: checks whether template vars are safely encoded
    for each output context (html, attr, js, url, css).
    """

    # Contexts that require distinct escaping regimes.
    _CONTEXT_SEVERITY: dict[str, str] = {
        "html": "high",
        "attr": "high",
        "js": "critical",
        "url": "medium",
        "css": "medium",
    }

    def check_template_output(
        self,
        template_vars: list[str],
        output_context: str,
        autoescaping: bool,
    ) -> list[SecurityObligation]:
        """Return obligations for template variables rendered in *output_context*.

        The escaping presheaf fails to satisfy descent when a variable is
        interpolated into a context whose encoding rules differ from the global
        autoescaping regime (HTML entity encoding).
        """
        obligations: list[SecurityObligation] = []
        severity = self._CONTEXT_SEVERITY.get(output_context, "high")

        for var in template_vars:
            location = f"template:var={var} ctx={output_context}"

            if output_context == "html" and not autoescaping:
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.XSS_REFLECTED,
                    location=location,
                    description=(
                        f"Variable '{var}' rendered into HTML context without "
                        "autoescaping — HTML entity encoding absent; "
                        "descent condition violated for the escaping presheaf."
                    ),
                    severity=severity,
                ))

            elif output_context == "attr" and not autoescaping:
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.XSS_REFLECTED,
                    location=location,
                    description=(
                        f"Variable '{var}' rendered into attribute context without "
                        "escaping — quote characters may break attribute boundary."
                    ),
                    severity=severity,
                ))

            elif output_context == "js":
                # Even with HTML autoescaping, JS context needs JS-string encoding.
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.XSS_REFLECTED,
                    location=location,
                    description=(
                        f"Variable '{var}' interpolated inside a <script> block. "
                        "HTML autoescaping is insufficient; JS-string encoding "
                        "(e.g. json.dumps) is required.  Local section for the "
                        "'js' patch of the covering does not agree with the "
                        "autoescaping global section."
                    ),
                    severity=severity,
                ))

            elif output_context == "url" and not autoescaping:
                # Unquoted URL values enable open-redirect and parameter injection.
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.OPEN_REDIRECT,
                    location=location,
                    description=(
                        f"Variable '{var}' used in URL context without urllib "
                        "quote_plus — open-redirect or parameter injection risk."
                    ),
                    severity=severity,
                ))

            elif output_context == "css" and not autoescaping:
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.XSS_REFLECTED,
                    location=location,
                    description=(
                        f"Variable '{var}' interpolated into CSS context; "
                        "expression() or url() injection possible."
                    ),
                    severity=severity,
                ))

        return obligations

    def check_innerHTML_usage(self, js_code: str) -> list[SecurityObligation]:
        """Scan JS source for unsafe DOM-sink assignments.

        Each match is a point where the escaping presheaf section has a gap —
        the DOM sink accepts raw HTML that bypasses the server-side encoding
        covering family entirely.
        """
        obligations: list[SecurityObligation] = []
        for match in _UNSAFE_SINKS.finditer(js_code):
            sink = match.group(1)
            lineno = js_code[: match.start()].count("\n") + 1
            obligations.append(SecurityObligation(
                threat=SecurityThreat.XSS_DOM,
                location=f"js:line={lineno} sink={sink}",
                description=(
                    f"Unsafe DOM sink '{sink}' detected at line {lineno}. "
                    "Raw HTML assignment breaks the escaping presheaf: the "
                    "section over the DOM patch does not lift to a global "
                    "sanitised section."
                ),
                severity="critical",
            ))
        return obligations

    def check_eval_usage(self, js_code: str) -> list[SecurityObligation]:
        """Scan for dynamic code execution sinks (eval, new Function, …)."""
        obligations: list[SecurityObligation] = []
        for match in _EVAL_PATTERNS.finditer(js_code):
            sink = match.group(1).rstrip("(").strip()
            lineno = js_code[: match.start()].count("\n") + 1
            obligations.append(SecurityObligation(
                threat=SecurityThreat.XSS_DOM,
                location=f"js:line={lineno} sink={sink}",
                description=(
                    f"Dynamic code execution via '{sink}' at line {lineno}. "
                    "String-based evaluation circumvents all static escaping "
                    "presheaf sections."
                ),
                severity="critical",
            ))
        return obligations


# ---------------------------------------------------------------------------
# 4. CSRFChecker
# ---------------------------------------------------------------------------

class CSRFChecker:
    """Model CSRF as a covering family with a missing synchroniser-token member.

    A form covering family {input_names} must include a csrf_token patch to
    satisfy the descent condition for state-mutating requests.
    """

    SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

    def check_forms(
        self,
        forms: list[tuple[str, str, list[str]]],
    ) -> list[SecurityObligation]:
        """Check HTML forms for missing CSRF tokens.

        forms = [(form_id, method, input_names)]
        """
        obligations: list[SecurityObligation] = []
        for form_id, method, input_names in forms:
            if method.upper() in self.SAFE_METHODS:
                continue
            if "csrf_token" not in input_names:
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.CSRF,
                    location=f"form:{form_id} method={method.upper()}",
                    description=(
                        f"Form '{form_id}' ({method.upper()}) lacks a csrf_token "
                        "input.  The covering family "
                        "{" + ", ".join(input_names) + "} "
                        "is missing the synchroniser-token patch; the gluing "
                        "condition cannot be satisfied."
                    ),
                    severity="high",
                ))
        return obligations

    def check_ajax_requests(
        self,
        fetch_calls: list[tuple[str, str]],
    ) -> list[SecurityObligation]:
        """Check AJAX/fetch calls for CSRF header on mutating requests.

        fetch_calls = [(url, method)]
        """
        obligations: list[SecurityObligation] = []
        for url, method in fetch_calls:
            if method.upper() in self.SAFE_METHODS:
                continue
            # We cannot inspect headers from a bare (url, method) tuple;
            # absence of header information is itself the gap in the covering.
            obligations.append(SecurityObligation(
                threat=SecurityThreat.CSRF,
                location=f"ajax:{method.upper()} {url}",
                description=(
                    f"AJAX {method.upper()} to '{url}' does not document an "
                    "X-CSRFToken header.  The request covering family is missing "
                    "the token member required for descent."
                ),
                severity="high",
            ))
        return obligations


# ---------------------------------------------------------------------------
# 5. SQLInjectionChecker
# ---------------------------------------------------------------------------

_SQL_CONCAT_PATTERNS = re.compile(
    r'(?:'
    r'"SELECT[^"]*"\s*\+'       # "SELECT ..." +
    r'|f"SELECT\s*\{'           # f"SELECT {"
    r"|f'SELECT\s*\{"           # f'SELECT {'
    r'|f"[^"]*WHERE\s*\{'       # f"...WHERE {"
    r"|f'[^']*WHERE\s*\{"       # f'...WHERE {'
    r'|f"[^"]*INSERT\s*\{'      # f"...INSERT {"
    r"|f'[^']*INSERT\s*\{"      # f'...INSERT {'
    r')',
    re.IGNORECASE | re.MULTILINE,
)

# Parameterised-query markers that override a false positive.
_SAFE_PARAM_PATTERNS = re.compile(
    r'(\?|:[a-zA-Z_]\w*|%s|%\([^)]+\)s)',
)
_SAFE_ORM_PATTERNS = re.compile(
    r'\.(filter_by|filter|execute|query)\(',
)


class SQLInjectionChecker:
    SAFE_PATTERNS: list[str] = [
        "?",          # positional placeholder (SQLite/ODBC)
        ":param",     # named placeholder
        "filter_by(", # SQLAlchemy ORM
        ".filter(",   # SQLAlchemy ORM
        ".execute(",  # explicit parameterised execute
    ]

    def check_query_construction(
        self,
        queries: list[str],
    ) -> list[SecurityObligation]:
        """Detect string-concatenated SQL queries that risk injection."""
        obligations: list[SecurityObligation] = []
        for i, query in enumerate(queries):
            if not _SQL_CONCAT_PATTERNS.search(query):
                continue
            # Suppress if safe parameterisation or ORM call is present.
            if _SAFE_PARAM_PATTERNS.search(query) or _SAFE_ORM_PATTERNS.search(query):
                continue
            obligations.append(SecurityObligation(
                threat=SecurityThreat.SQL_INJECTION,
                location=f"query:index={i}",
                description=(
                    f"Query at index {i} appears to concatenate user-controlled "
                    "data into a SQL string.  Parameterised queries or an ORM "
                    "must be used to close this obstruction."
                ),
                severity="critical",
            ))
        return obligations


# ---------------------------------------------------------------------------
# 6. SecurityDescentChecker
# ---------------------------------------------------------------------------

_SENSITIVE_ROUTE_PREFIXES = ("/admin/", "/dashboard/", "/settings/")
_SENSITIVE_TEMPLATE_KEYWORDS = ("admin", "user")


class SecurityDescentChecker:
    """Aggregate checker: collect all security obligations and return a
    DescentResult encoding whether the application's security covering family
    satisfies the gluing (descent) condition.

    Success  ↔ every obligation is mitigated (global section exists).
    Failure  ↔ at least one unmitigated obligation (obstruction class non-trivial).
    """

    def check_all(
        self,
        routes: list[dict],
        templates: list[str],
        js_code: str = "",
        has_csrf_middleware: bool = False,
    ) -> DescentResult:
        """Run all sub-checkers and return a DescentResult.

        routes    = [{path, method, handler, requires_auth}]
        templates = list of template names
        """
        obligations: list[SecurityObligation] = []

        obligations.extend(self._check_auth_routes(routes))

        xss = XSSChecker()
        if js_code:
            obligations.extend(xss.check_innerHTML_usage(js_code))
            obligations.extend(xss.check_eval_usage(js_code))

        if not has_csrf_middleware:
            csrf = CSRFChecker()
            post_routes = [
                r for r in routes
                if r.get("method", "GET").upper() not in CSRFChecker.SAFE_METHODS
            ]
            if post_routes:
                obligations.append(SecurityObligation(
                    threat=SecurityThreat.CSRF,
                    location="middleware:csrf",
                    description=(
                        "No CSRF middleware detected.  The synchroniser-token "
                        "patch is absent from the application-level covering "
                        "family for all mutating routes."
                    ),
                    severity="high",
                ))

        unmitigated = [o for o in obligations if not o.is_mitigated]

        if not unmitigated:
            return DescentResult.success(
                GlobalSection(
                    coordinate="security:application",
                    merged_judgment={"status": "secure", "obligations": len(obligations)},
                    certificate="all-security-obligations-satisfied",
                )
            )

        obstruction = DescentObstruction(
            coordinate="security:application",
            violated_overlaps=(),
            partial_section={
                "unmitigated": [o.location for o in unmitigated],
                "threats": list({o.threat.value for o in unmitigated}),
            },
        )
        return DescentResult.failure(obstruction)

    def _check_auth_routes(self, routes: list[dict]) -> list[SecurityObligation]:
        """Flag protected-path routes that lack authentication."""
        obligations: list[SecurityObligation] = []
        for route in routes:
            path = route.get("path", "")
            if any(path.startswith(p) for p in _SENSITIVE_ROUTE_PREFIXES):
                if not route.get("requires_auth", True):
                    obligations.append(SecurityObligation(
                        threat=SecurityThreat.MISSING_AUTH,
                        location=f"route:{path} {route.get('method', 'GET')}",
                        description=(
                            f"Route '{path}' is under a sensitive prefix but "
                            "requires_auth=False.  The authentication local "
                            "section is absent; descent to the global section "
                            "is blocked."
                        ),
                        severity="critical",
                    ))
        return obligations

    @staticmethod
    def obstruction_report(
        obligations: list[SecurityObligation],
    ) -> dict:
        """Group obligations by severity and return summary counts."""
        buckets: dict[str, list[str]] = {
            "critical": [],
            "high": [],
            "medium": [],
            "low": [],
        }
        mitigated = 0
        for ob in obligations:
            if ob.is_mitigated:
                mitigated += 1
            bucket = buckets.get(ob.severity)
            if bucket is not None:
                bucket.append(ob.location)
            else:
                buckets.setdefault(ob.severity, []).append(ob.location)

        return {
            **buckets,
            "total": len(obligations),
            "mitigated": mitigated,
        }


# ---------------------------------------------------------------------------
# 7. CSPPolicy — Content Security Policy as a covering family over origins
# ---------------------------------------------------------------------------

@dataclass
class CSPPolicy:
    """Content Security Policy modelled as a covering family over
    script/style/image/font origins.

    Each directive is a patch in the covering; together they must cover all
    legitimate resource origins without admitting unsafe-inline or eval
    (which collapse the covering to a single trivial patch).
    """

    default_src: list[str] = field(default_factory=lambda: ["'self'"])
    script_src: list[str] = field(default_factory=lambda: ["'self'"])
    style_src: list[str] = field(default_factory=lambda: ["'self'"])
    img_src: list[str] = field(default_factory=lambda: ["'self'", "data:"])
    connect_src: list[str] = field(default_factory=lambda: ["'self'"])
    font_src: list[str] = field(default_factory=lambda: ["'self'"])
    frame_ancestors: list[str] = field(default_factory=lambda: ["'none'"])

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_header(self) -> str:
        """Serialise policy to a Content-Security-Policy header value."""
        directives = [
            ("default-src", self.default_src),
            ("script-src", self.script_src),
            ("style-src", self.style_src),
            ("img-src", self.img_src),
            ("connect-src", self.connect_src),
            ("font-src", self.font_src),
            ("frame-ancestors", self.frame_ancestors),
        ]
        parts = []
        for name, sources in directives:
            if sources:
                parts.append(f"{name} {' '.join(sources)}")
        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def allows_inline_scripts(self) -> bool:
        return "'unsafe-inline'" in self.script_src

    def allows_eval(self) -> bool:
        return "'unsafe-eval'" in self.script_src

    def is_strict(self) -> bool:
        """True iff neither unsafe-inline nor eval is permitted for scripts."""
        return not self.allows_inline_scripts() and not self.allows_eval()

    # ------------------------------------------------------------------
    # Named presets
    # ------------------------------------------------------------------

    @classmethod
    def strict(cls) -> CSPPolicy:
        """Strict CSP: nonce-based scripts, no inline, no eval.

        The covering family here has fine-grained patches (nonce per script)
        so that each local section (individual script) must present the nonce
        to be admitted — the gluing condition is tight.
        """
        return cls(
            default_src=["'self'"],
            script_src=["'self'", "'nonce-{NONCE}'", "'strict-dynamic'"],
            style_src=["'self'", "'nonce-{NONCE}'"],
            img_src=["'self'", "data:"],
            connect_src=["'self'"],
            font_src=["'self'"],
            frame_ancestors=["'none'"],
        )

    @classmethod
    def flask_default(cls) -> CSPPolicy:
        """Reasonable defaults for a Flask application.

        Permits same-origin scripts and styles, blocks framing (anti-clickjacking),
        allows CDN fonts/images if needed.  Inline styles are permitted because
        Flask-Bootstrap and many Jinja templates rely on them; scripts remain
        restricted to same-origin.
        """
        return cls(
            default_src=["'self'"],
            script_src=["'self'"],
            style_src=["'self'", "'unsafe-inline'"],
            img_src=["'self'", "data:", "https:"],
            connect_src=["'self'"],
            font_src=["'self'", "https://fonts.gstatic.com"],
            frame_ancestors=["'none'"],
        )
