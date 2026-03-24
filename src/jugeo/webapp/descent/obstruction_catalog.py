"""
Catalog of known web-application obstruction patterns.

Each :class:`ObstructionPattern` captures a recurring class of sheaf-condition
failure — its overlap kind, how to detect it, and a templated repair hint.
:class:`PatternMatcher` matches a raw violation dict against the catalog and
generates a concrete repair suggestion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


__all__ = [
    "ObstructionPattern",
    "KNOWN_PATTERNS",
    "PatternMatcher",
]


# ---------------------------------------------------------------------------
# Pattern dataclass
# ---------------------------------------------------------------------------

@dataclass
class ObstructionPattern:
    """
    A known, recurring obstruction pattern.

    Parameters
    ----------
    id : str
        Short stable identifier (e.g. ``"OP001"``).
    name : str
        Machine-readable slug.
    description : str
        Human-readable explanation of when this pattern fires.
    overlap_kind : str
        The :class:`OverlapKind` value(s) this pattern applies to.  Comma-
        separated if it covers more than one kind.
    detection_strategy : str
        Prose description of how the pattern is detected.
    repair_template : str
        Template string for the repair hint.  May contain ``{var_name}``,
        ``{route}``, ``{table}``, etc. placeholders.
    severity : str
        Default severity: ``"critical"`` | ``"high"`` | ``"medium"`` | ``"low"``.
    examples : list[str]
        Concrete examples of this pattern in the wild.
    """

    id: str
    name: str
    description: str
    overlap_kind: str
    detection_strategy: str
    repair_template: str
    severity: str = "high"
    examples: list[str] = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "overlap_kind": self.overlap_kind,
            "detection_strategy": self.detection_strategy,
            "repair_template": self.repair_template,
            "severity": self.severity,
            "examples": list(self.examples),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObstructionPattern:
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            overlap_kind=data["overlap_kind"],
            detection_strategy=data["detection_strategy"],
            repair_template=data["repair_template"],
            severity=data.get("severity", "high"),
            examples=data.get("examples", []),
        )


# ---------------------------------------------------------------------------
# Catalog of 15 known patterns
# ---------------------------------------------------------------------------

KNOWN_PATTERNS: list[ObstructionPattern] = [
    # --- OP001 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP001",
        name="missing_template_variable",
        description=(
            "Template uses {{ var }} but the render_template call does not "
            "pass it, causing an UndefinedError at runtime."
        ),
        overlap_kind="route_template",
        detection_strategy=(
            "Match render_template kwargs against {{ }} variables extracted "
            "from the Jinja2 template AST."
        ),
        repair_template="Add {var_name}={var_name} to render_template() call",
        severity="high",
        examples=[
            "render_template('page.html') misses 'user' that {{ user.name }} expects",
            "render_template('dash.html', stats=stats) misses 'title'",
        ],
    ),
    # --- OP002 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP002",
        name="api_contract_mismatch",
        description=(
            "JS fetch() destructures fields that the server route does not "
            "include in its JSON response."
        ),
        overlap_kind="route_js_fetch",
        detection_strategy=(
            "Compare the set of JSON response keys in the route handler with "
            "the destructured/accessed fields in the JS fetch callback."
        ),
        repair_template=(
            "Add '{field}' to the JSON response of route '{route}'"
        ),
        severity="high",
        examples=[
            "fetch('/api/user').then(r => r.json()).then(d => d.avatar) but "
            "route returns {name, email} only",
        ],
    ),
    # --- OP003 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP003",
        name="orm_schema_drift",
        description=(
            "ORM model defines a column that does not exist in the DB schema, "
            "or the types/nullability disagree."
        ),
        overlap_kind="model_db_schema",
        detection_strategy=(
            "Pair ORM model columns with DDL/migration columns by name; "
            "compare type families and nullable flags."
        ),
        repair_template=(
            "Add column '{column}' to table '{table}' or update the ORM model"
        ),
        severity="high",
        examples=[
            "Model User.avatar_url (String) but table 'users' has no avatar_url column",
            "Model Post.published (Boolean) but DDL says published INTEGER NOT NULL",
        ],
    ),
    # --- OP004 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP004",
        name="missing_dom_element",
        description=(
            "JS getElementById or querySelector references an element id "
            "that does not exist in any HTML template."
        ),
        overlap_kind="js_dom_html",
        detection_strategy=(
            "Collect all getElementById/querySelector string arguments in JS "
            "and verify each appears as an id attribute in the HTML."
        ),
        repair_template="Add id=\"{element_id}\" to the appropriate HTML element",
        severity="high",
        examples=[
            "document.getElementById('chart-container') but no element has id='chart-container'",
        ],
    ),
    # --- OP005 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP005",
        name="missing_css_class",
        description=(
            "JS classList.add/toggle or a template class=\"...\" references "
            "a CSS class with no corresponding rule."
        ),
        overlap_kind="js_class_css,template_css",
        detection_strategy=(
            "Collect class names from JS classList mutations and template "
            "class attributes; check each against CSS rule selectors."
        ),
        repair_template="Add .{class_name} {{ ... }} rule to the stylesheet",
        severity="medium",
        examples=[
            "classList.add('active') but no .active rule in any CSS file",
            "class=\"card-highlight\" in template but .card-highlight is never defined",
        ],
    ),
    # --- OP006 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP006",
        name="broken_form_action",
        description=(
            "HTML form action URL or method does not match any route."
        ),
        overlap_kind="form_route",
        detection_strategy=(
            "Match each <form action=... method=...> against Flask route "
            "patterns and allowed methods."
        ),
        repair_template=(
            "Add a {method} route for '{action}' or fix the form action URL"
        ),
        severity="high",
        examples=[
            "<form action='/signup' method='POST'> but no POST /signup route exists",
        ],
    ),
    # --- OP007 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP007",
        name="auth_bypass",
        description=(
            "A route with @login_required has no corresponding session check, "
            "or a mutation route lacks any authentication."
        ),
        overlap_kind="auth_session",
        detection_strategy=(
            "Verify that routes decorated with @login_required have session "
            "keys being read, and mutation routes have auth."
        ),
        repair_template=(
            "Add authentication to route '{route}' or verify session middleware"
        ),
        severity="critical",
        examples=[
            "@login_required on /admin but session['user_id'] is never read",
            "POST /transfer has no authentication at all",
        ],
    ),
    # --- OP008 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP008",
        name="null_violation",
        description=(
            "A handler sets a NOT NULL column to None/NULL, or a NOT NULL "
            "column has no handler writing to it."
        ),
        overlap_kind="db_constraint_handler",
        detection_strategy=(
            "Cross-reference NOT NULL constraints from DDL with handler code "
            "that writes to those columns."
        ),
        repair_template=(
            "Provide a non-null value for '{table}.{column}' or make the "
            "column nullable"
        ),
        severity="high",
        examples=[
            "User.email is NOT NULL but create_user() doesn't set email",
        ],
    ),
    # --- OP009 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP009",
        name="xss_unsafe_render",
        description=(
            "Template renders a user-controlled variable with |safe or "
            "{% autoescape false %}, enabling XSS."
        ),
        overlap_kind="route_template",
        detection_strategy=(
            "Find {{ var|safe }} and {% autoescape false %} blocks in "
            "templates; flag when 'var' originates from user input."
        ),
        repair_template=(
            "Remove |safe filter from '{var_name}' or sanitise the value "
            "server-side before passing to render_template"
        ),
        severity="critical",
        examples=[
            "{{ user_bio|safe }} where user_bio comes from a form POST",
        ],
    ),
    # --- OP010 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP010",
        name="csrf_missing",
        description=(
            "A state-changing form does not include a CSRF token."
        ),
        overlap_kind="form_route",
        detection_strategy=(
            "Check that every POST/PUT/DELETE form includes a hidden "
            "csrf_token field or the route has CSRF exemption."
        ),
        repair_template=(
            "Add <input type='hidden' name='csrf_token' value='{{ csrf_token() }}'> "
            "to the form at '{action}'"
        ),
        severity="critical",
        examples=[
            "<form action='/delete-account' method='POST'> has no csrf_token field",
        ],
    ),
    # --- OP011 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP011",
        name="dangling_static_ref",
        description=(
            "Template or HTML references a static file (CSS, JS, image) "
            "that does not exist on disk."
        ),
        overlap_kind="route_template",
        detection_strategy=(
            "Collect url_for('static', filename=...) and <link>/<script> "
            "src paths; verify each file exists in the static directory."
        ),
        repair_template=(
            "Create the missing static file '{filename}' or fix the reference"
        ),
        severity="medium",
        examples=[
            "url_for('static', filename='js/app.min.js') but file does not exist",
        ],
    ),
    # --- OP012 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP012",
        name="orphaned_route",
        description=(
            "A Flask route exists but no template, form, or JS fetch "
            "references it — dead code."
        ),
        overlap_kind="route_template,route_js_fetch",
        detection_strategy=(
            "Collect all route patterns and check that each is referenced "
            "by at least one template link, form action, or JS fetch URL."
        ),
        repair_template=(
            "Remove orphaned route '{route}' or add a reference to it"
        ),
        severity="low",
        examples=[
            "Route GET /api/legacy-users is defined but nothing calls it",
        ],
    ),
    # --- OP013 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP013",
        name="dead_css",
        description=(
            "A CSS class rule is defined but never referenced by any "
            "template or JS code."
        ),
        overlap_kind="template_css,js_class_css",
        detection_strategy=(
            "Collect all .class selectors from CSS; check that each is "
            "used in at least one template class attribute or JS classList call."
        ),
        repair_template="Remove unused CSS rule .{class_name}",
        severity="low",
        examples=[
            ".old-banner { display:none } is defined but never used anywhere",
        ],
    ),
    # --- OP014 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP014",
        name="inconsistent_error_handling",
        description=(
            "Server defines an error handler for a status code but the "
            "client JS does not handle that status in its catch blocks."
        ),
        overlap_kind="error_handler_js",
        detection_strategy=(
            "Cross-reference Flask @app.errorhandler(code) registrations "
            "with JS fetch .catch / response.status handling."
        ),
        repair_template=(
            "Add client-side handling for HTTP {status_code} in the JS "
            "fetch error path"
        ),
        severity="medium",
        examples=[
            "Server defines errorhandler(429) but JS only handles 400/401/500",
        ],
    ),
    # --- OP015 ---------------------------------------------------------------
    ObstructionPattern(
        id="OP015",
        name="session_key_mismatch",
        description=(
            "Code writes to session['key_a'] but reads session['key_b'], "
            "or different parts of the app use inconsistent session key names."
        ),
        overlap_kind="auth_session",
        detection_strategy=(
            "Collect all session key write sites and read sites; flag "
            "reads of keys that are never written and vice versa."
        ),
        repair_template=(
            "Align session key names: use '{expected_key}' consistently"
        ),
        severity="high",
        examples=[
            "Login sets session['uid'] but middleware reads session['user_id']",
        ],
    ),
]


# Index for fast lookup
_PATTERNS_BY_ID: dict[str, ObstructionPattern] = {p.id: p for p in KNOWN_PATTERNS}
_PATTERNS_BY_NAME: dict[str, ObstructionPattern] = {p.name: p for p in KNOWN_PATTERNS}


# ---------------------------------------------------------------------------
# Keyword → pattern mapping for matching violations to patterns
# ---------------------------------------------------------------------------

_KEYWORD_MAP: dict[str, list[str]] = {
    "missing_template_variable": [
        "does not pass", "template", "variable", "render_template",
        "UndefinedError", "context_vars",
    ],
    "api_contract_mismatch": [
        "fetch", "expected field", "JSON response", "does not provide",
        "api", "contract",
    ],
    "orm_schema_drift": [
        "ORM", "model", "DB column", "table", "type mismatch",
        "nullable", "schema",
    ],
    "missing_dom_element": [
        "getElementById", "querySelector", "DOM", "element",
        "not found in HTML", "html_ids",
    ],
    "missing_css_class": [
        "classList", "class", "CSS definition", "no CSS", "css_classes",
    ],
    "broken_form_action": [
        "form", "action", "no matching route", "form_route",
    ],
    "auth_bypass": [
        "login_required", "auth", "session", "no session checks",
        "no authentication", "no validation",
    ],
    "null_violation": [
        "NOT NULL", "sets_null", "null", "constraint",
    ],
    "xss_unsafe_render": [
        "safe", "autoescape", "XSS", "unsanitised",
    ],
    "csrf_missing": [
        "csrf", "CSRF", "csrf_token",
    ],
    "dangling_static_ref": [
        "static", "url_for", "file does not exist", "missing file",
    ],
    "orphaned_route": [
        "orphaned", "dead route", "unreferenced route",
    ],
    "dead_css": [
        "dead CSS", "unused CSS", "never used",
    ],
    "inconsistent_error_handling": [
        "error handler", "errorhandler", "status code", "catch",
    ],
    "session_key_mismatch": [
        "session key", "mismatch", "inconsistent session",
    ],
}


# ---------------------------------------------------------------------------
# Pattern matcher
# ---------------------------------------------------------------------------

class PatternMatcher:
    """Match raw violation dicts against the known obstruction catalog."""

    def __init__(self) -> None:
        self._kind_index: dict[str, list[ObstructionPattern]] = {}
        for pattern in KNOWN_PATTERNS:
            for kind in pattern.overlap_kind.split(","):
                self._kind_index.setdefault(kind.strip(), []).append(pattern)

    # -----------------------------------------------------------------------

    def match(self, violation: dict) -> ObstructionPattern | None:
        """
        Match a violation dict to a known pattern.

        The violation dict should have at least ``"kind"`` (or
        ``"overlap_kind"``) and ``"message"`` keys.

        Returns the best-matching :class:`ObstructionPattern`, or ``None``
        if no pattern matches.
        """
        kind = violation.get("kind", violation.get("overlap_kind", ""))
        message = violation.get("message", "").lower()

        candidates = self._kind_index.get(kind, [])
        if not candidates:
            # Fallback: try all patterns by keyword
            candidates = KNOWN_PATTERNS

        best: ObstructionPattern | None = None
        best_score = 0

        for pattern in candidates:
            keywords = _KEYWORD_MAP.get(pattern.name, [])
            score = sum(1 for kw in keywords if kw.lower() in message)
            # Boost score if the overlap kind matches directly
            if kind in pattern.overlap_kind.split(","):
                score += 2
            if score > best_score:
                best_score = score
                best = pattern

        # Require at least a minimal match
        if best_score < 2:
            return None
        return best

    # -----------------------------------------------------------------------

    def suggest_repair(
        self,
        pattern: ObstructionPattern,
        violation: dict,
    ) -> str:
        """
        Generate a concrete repair hint from the pattern's template.

        Substitutes any ``{key}`` placeholders in the template with values
        from the *violation* dict.  Unknown placeholders are left as-is.
        """
        hint = pattern.repair_template

        # Build a substitution dict from the violation
        subs: dict[str, str] = {}
        for key in ("var_name", "field", "route", "table", "column",
                     "element_id", "class_name", "action", "method",
                     "filename", "status_code", "expected_key"):
            if key in violation:
                subs[key] = str(violation[key])

        # Also try to extract from message
        message = violation.get("message", "")
        if "var_name" not in subs and "'" in message:
            # Grab the first quoted token
            parts = message.split("'")
            if len(parts) >= 2:
                subs.setdefault("var_name", parts[1])
                subs.setdefault("field", parts[1])

        for key, value in subs.items():
            hint = hint.replace("{" + key + "}", value)

        return hint

    # -----------------------------------------------------------------------

    def severity_for(self, pattern: ObstructionPattern) -> str:
        """
        Return the canonical severity for a pattern.

        Returns one of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``.
        """
        valid = {"critical", "high", "medium", "low"}
        if pattern.severity in valid:
            return pattern.severity
        return "medium"

    # -----------------------------------------------------------------------

    def all_patterns_for_kind(self, overlap_kind: str) -> list[ObstructionPattern]:
        """Return all patterns that apply to the given overlap kind."""
        return list(self._kind_index.get(overlap_kind, []))

    def get_pattern(self, pattern_id: str) -> ObstructionPattern | None:
        """Look up a pattern by its id (e.g. ``"OP001"``)."""
        return _PATTERNS_BY_ID.get(pattern_id)

    def get_pattern_by_name(self, name: str) -> ObstructionPattern | None:
        """Look up a pattern by its name slug."""
        return _PATTERNS_BY_NAME.get(name)
