"""Phase 0 of the judgment-geometric Flask/HTML generation pipeline.

Converts a natural language prompt into a formal **obligation presheaf** — a
collection of :class:`LocalSection`s on an ``AppSite`` representing what the
app MUST do.

This is genuinely sheaf-theoretic: each obligation is a local section at a
specific coordinate of the app site; the obligation presheaf is satisfiable
(i.e., a global section exists) iff the obligations are mutually consistent.
"""
from __future__ import annotations

__all__ = [
    "AppMode",
    "ObligationKind",
    "AppObligation",
    "AppObligationPresheaf",
    "PromptObligationExtractor",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# jugeo core — real descent types
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    Site,
    SiteBuilder,
    Morphism,
    MorphismKind,
)
from jugeo.geometry.descent import (
    LocalSection,
    DescentResult,
    DescentObstruction,
    GlobalSection,
    RepairFrontier,
    CohomologyClass,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AppMode(str, Enum):
    """High-level rendering mode of the generated application."""

    FLASK = "flask"
    """Server-rendered with routes, models, and optional auth."""

    STATIC = "static"
    """Pure HTML/CSS/JS; no server state."""


class ObligationKind(str, Enum):
    """Semantic category of a single app obligation."""

    ROUTE = "route"
    MODEL = "model"
    TEMPLATE = "template"
    CSS_RULE = "css_rule"
    JS_MODULE = "js_module"
    AUTH = "auth"
    FORM = "form"
    API_ENDPOINT = "api_endpoint"
    RELATIONSHIP = "relationship"
    UI_METAPHOR = "ui_metaphor"
    ACCESSIBILITY = "accessibility"
    SECURITY = "security"
    PERFORMANCE = "performance"
    RESPONSIVE = "responsive"


# ---------------------------------------------------------------------------
# AppObligation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppObligation:
    """A single obligation living at a specific coordinate of the app site.

    Each obligation is a local section at ``coordinate_name``; together they
    form the obligation presheaf whose global section (if it exists) is the
    consistent app spec.
    """

    kind: ObligationKind
    name: str
    data: dict[str, Any]
    coordinate_name: str
    trust_level: float = 1.0

    def to_local_section(self) -> LocalSection:
        """Reify this obligation as a :class:`LocalSection` on the app site."""
        return LocalSection(
            coordinate=self.coordinate_name,
            judgment_data={
                "kind": self.kind.value,
                "name": self.name,
                **self.data,
            },
            trust_level=self.trust_level,
            provenance=("prompt_obligations",),
        )


# ---------------------------------------------------------------------------
# AppObligationPresheaf
# ---------------------------------------------------------------------------


@dataclass
class AppObligationPresheaf:
    """The obligation presheaf extracted from a natural language prompt.

    Each :class:`AppObligation` is a local section at a coordinate of the
    app site.  A global section exists iff the obligations are mutually
    consistent (checked by :meth:`check_satisfiability`).
    """

    prompt: str
    mode: AppMode
    obligations: list[AppObligation]
    domain_nouns: list[str]
    domain_verbs: list[str]
    ui_metaphors: list[str]
    auth_required: bool

    # -- filtering -----------------------------------------------------------

    def by_kind(self, kind: ObligationKind) -> list[AppObligation]:
        """Return all obligations with the given :class:`ObligationKind`."""
        return [o for o in self.obligations if o.kind == kind]

    # -- sheaf-theoretic helpers ---------------------------------------------

    def to_local_sections(self) -> list[LocalSection]:
        """Reify every obligation as a :class:`LocalSection`."""
        return [o.to_local_section() for o in self.obligations]

    def to_app_spec_seed(self) -> dict:
        """Return a partial spec dict seeding downstream pipeline stages.

        The seed encodes mode, route stubs, model stubs, and other metadata
        that later pipeline stages refine into full Flask/HTML artifacts.
        """
        routes: list[dict] = []
        for ob in self.by_kind(ObligationKind.ROUTE):
            routes.append({
                "name": ob.name,
                **ob.data,
            })

        models: list[dict] = []
        for ob in self.by_kind(ObligationKind.MODEL):
            models.append({
                "name": ob.name,
                **ob.data,
            })

        templates: list[str] = [
            ob.name for ob in self.by_kind(ObligationKind.TEMPLATE)
        ]

        return {
            "name": _slugify(self.prompt[:60]),
            "mode": self.mode.value,
            "auth_required": self.auth_required,
            "domain_nouns": list(self.domain_nouns),
            "domain_verbs": list(self.domain_verbs),
            "ui_metaphors": list(self.ui_metaphors),
            "routes": routes,
            "models": models,
            "templates": templates,
        }

    # -- satisfiability check ------------------------------------------------

    def check_satisfiability(self) -> DescentResult:
        """Check whether the obligation presheaf admits a global section.

        Rules
        -----
        * If an AUTH obligation exists, at least one ROUTE must carry
          ``auth_required=True``.
        * If MODEL obligations exist, at least one ROUTE must reference a
          model name.
        * If FORM obligations exist, at least one POST ROUTE must exist.

        Returns a :class:`DescentResult` — either :meth:`DescentResult.success`
        wrapping a :class:`GlobalSection` or :meth:`DescentResult.failure`
        wrapping a :class:`DescentObstruction`.
        """
        violations: list[str] = []

        auth_obs = self.by_kind(ObligationKind.AUTH)
        route_obs = self.by_kind(ObligationKind.ROUTE)
        model_obs = self.by_kind(ObligationKind.MODEL)
        form_obs = self.by_kind(ObligationKind.FORM)

        # Rule 1: AUTH implies at least one auth-guarded ROUTE
        if auth_obs:
            auth_routes = [r for r in route_obs if r.data.get("auth_required")]
            if not auth_routes:
                violations.append(
                    "AUTH obligation exists but no ROUTE carries auth_required=True"
                )

        # Rule 2: MODELs must be referenced by at least one ROUTE
        if model_obs:
            model_names_lower = {m.name.lower() for m in model_obs}
            route_has_model = any(
                any(mn in r.name.lower() or mn in str(r.data).lower()
                    for mn in model_names_lower)
                for r in route_obs
            )
            if not route_has_model:
                violations.append(
                    "MODEL obligations exist but no ROUTE references any model"
                )

        # Rule 3: FORM implies a POST ROUTE
        if form_obs:
            post_routes = [r for r in route_obs if r.data.get("method") == "POST"]
            if not post_routes:
                violations.append(
                    "FORM obligation exists but no POST ROUTE found"
                )

        if not violations:
            # Build a minimal global section from all local sections
            sections = self.to_local_sections()
            merged: dict[str, Any] = {}
            for s in sections:
                merged[s.coordinate] = s.judgment_data
            global_section = GlobalSection(
                coordinate="app.root",
                merged_judgment=merged,
                constituent_sections=tuple(s.coordinate for s in sections),
                trust_floor=min((s.trust_level for s in sections), default=1.0),
            )
            return DescentResult.success(global_section)
        else:
            obstruction = DescentObstruction(
                coordinate="app.root",
                violated_overlaps=(),
                partial_section={"violations": violations},
                repair_frontier=RepairFrontier(
                    missing_evidence=tuple(violations),
                ),
                cohomology_class=CohomologyClass(
                    dimension=1,
                    cocycle_data={f"v{i}": v for i, v in enumerate(violations)},
                ),
            )
            return DescentResult.failure(obstruction)


# ---------------------------------------------------------------------------
# PromptObligationExtractor
# ---------------------------------------------------------------------------


class PromptObligationExtractor:
    """Converts a free-text prompt into an :class:`AppObligationPresheaf`.

    The extractor operates in a single pass, matching against pre-compiled
    regex patterns to identify domain nouns, CRUD verbs, auth signals, and
    UI metaphors, then synthesises obligations from the matches.
    """

    # Nouns that signal models/entities
    ENTITY_PATTERNS: list[str] = [
        r'\b(user|account|profile|member)\b',
        r'\b(post|article|entry|note|blog)\b',
        r'\b(product|item|listing|inventory)\b',
        r'\b(recipe|dish|meal|food)\b',
        r'\b(order|cart|purchase|checkout)\b',
        r'\b(comment|review|rating|feedback)\b',
        r'\b(task|todo|ticket|issue)\b',
        r'\b(project|team|workspace)\b',
        r'\b(message|chat|thread|conversation)\b',
        r'\b(event|booking|reservation|appointment)\b',
        r'\b(photo|image|gallery|media)\b',
        r'\b(category|tag|label|topic)\b',
    ]

    # Verbs that signal CRUD operations
    CRUD_VERB_PATTERNS: list[str] = [
        r'\b(create|add|new|post|submit|upload)\b',
        r'\b(edit|update|modify|change)\b',
        r'\b(delete|remove|archive)\b',
        r'\b(view|list|browse|search|filter|find)\b',
        r'\b(share|sharing|publish|send)\b',
        r'\b(rate|review|comment|like|vote)\b',
        r'\b(login|signup|register|auth|account)\b',
        r'\b(download|export|import)\b',
    ]

    # Signals that the app needs multi-user auth
    AUTH_SIGNALS: list[str] = [
        r'\b(user|login|signup|register|account|profile|auth|member|admin)\b',
        r'\b(share|sharing|publish|post|submit)\b',  # implies authorship
        r'\b(my |your |their )',
    ]

    # Signals the app is static (no server state)
    STATIC_SIGNALS: list[str] = [
        r'\b(landing page|portfolio|resume|cv|brochure|showcase|one.?page)\b',
        r'\b(documentation|docs|blog)\b',
    ]

    # UI metaphors → layout patterns
    UI_METAPHORS: dict[str, list[str]] = {
        'card grid': [r'\b(sharing|gallery|shop|store|product|listing|browse)\b'],
        'hero section': [r'\b(landing|showcase|portfolio|marketing)\b'],
        'dashboard': [r'\b(dashboard|admin|analytics|stats|overview)\b'],
        'table': [r'\b(list|manage|admin|table|spreadsheet)\b'],
        'wizard': [r'\b(wizard|step|onboarding|checkout|form)\b'],
        'chat': [r'\b(chat|message|conversation|thread)\b'],
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, prompt: str) -> AppObligationPresheaf:
        """Extract an :class:`AppObligationPresheaf` from *prompt*.

        Parameters
        ----------
        prompt:
            Free-text description of the desired application.

        Returns
        -------
        AppObligationPresheaf
            The full obligation presheaf ready for satisfiability checking
            and downstream pipeline consumption.
        """
        lower = prompt.lower()

        domain_nouns = self._extract_nouns(lower)
        domain_verbs = self._extract_verbs(lower)
        auth_required = self._detect_auth(lower)
        mode = self._detect_mode(lower, auth_required)
        ui_metaphors = self._detect_ui_metaphors(lower)

        obligations: list[AppObligation] = []

        # MODEL obligations
        for noun in domain_nouns:
            obligations.append(AppObligation(
                kind=ObligationKind.MODEL,
                name=noun.capitalize(),
                data={"fields": ["id", "created_at"]},
                coordinate_name=f"model.{noun}",
            ))

        # ROUTE obligations based on verbs
        post_routes_exist = False
        for noun in domain_nouns:
            noun_routes = self._build_routes_for_noun(noun, domain_verbs, auth_required)
            for route_ob in noun_routes:
                obligations.append(route_ob)
                if route_ob.data.get("method") == "POST":
                    post_routes_exist = True

        # Auth routes (login/signup)
        if any(re.search(p, lower) for p in [r'\b(login|signup|register|auth)\b']):
            for route_ob in _auth_route_obligations():
                obligations.append(route_ob)

        # AUTH obligation
        if auth_required:
            obligations.append(AppObligation(
                kind=ObligationKind.AUTH,
                name="auth",
                data={"strategy": "session", "login_required": True},
                coordinate_name="auth.session",
            ))

        # FORM obligations for each POST route
        if post_routes_exist:
            post_routes = [
                o for o in obligations
                if o.kind == ObligationKind.ROUTE and o.data.get("method") == "POST"
            ]
            seen_forms: set[str] = set()
            for r in post_routes:
                form_name = f"{r.name}_form"
                if form_name not in seen_forms:
                    seen_forms.add(form_name)
                    obligations.append(AppObligation(
                        kind=ObligationKind.FORM,
                        name=form_name,
                        data={"action": r.data.get("path", "/"), "method": "POST"},
                        coordinate_name=f"form.{form_name}",
                    ))

        # Implicit obligations (always present)
        obligations.append(AppObligation(
            kind=ObligationKind.ACCESSIBILITY,
            name="wcag_baseline",
            data={"level": "AA", "requirements": ["alt_text", "aria_labels", "keyboard_nav"]},
            coordinate_name="accessibility.wcag",
        ))
        obligations.append(AppObligation(
            kind=ObligationKind.RESPONSIVE,
            name="mobile_first",
            data={"breakpoints": ["320px", "768px", "1024px"]},
            coordinate_name="responsive.mobile_first",
        ))
        obligations.append(AppObligation(
            kind=ObligationKind.PERFORMANCE,
            name="baseline_perf",
            data={"targets": {"lcp": "2.5s", "fid": "100ms", "cls": "0.1"}},
            coordinate_name="performance.core_web_vitals",
        ))

        # SECURITY obligation when auth or POST routes exist
        if auth_required or post_routes_exist:
            obligations.append(AppObligation(
                kind=ObligationKind.SECURITY,
                name="security_baseline",
                data={"csrf": True, "xss_headers": True, "sql_injection": "parameterized"},
                coordinate_name="security.baseline",
            ))

        # UI_METAPHOR obligations
        for metaphor in ui_metaphors:
            obligations.append(AppObligation(
                kind=ObligationKind.UI_METAPHOR,
                name=metaphor.replace(" ", "_"),
                data={"pattern": metaphor},
                coordinate_name=f"ui_metaphor.{metaphor.replace(' ', '_')}",
            ))

        # Standard implicit template/CSS/JS obligations
        obligations.extend(
            self._build_standard_obligations(mode, auth_required, post_routes_exist, ui_metaphors)
        )

        return AppObligationPresheaf(
            prompt=prompt,
            mode=mode,
            obligations=obligations,
            domain_nouns=domain_nouns,
            domain_verbs=domain_verbs,
            ui_metaphors=ui_metaphors,
            auth_required=auth_required,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_nouns(self, lower: str) -> list[str]:
        seen: dict[str, None] = {}
        for pattern in self.ENTITY_PATTERNS:
            for m in re.finditer(pattern, lower):
                seen[m.group(1)] = None
        return list(seen)

    def _extract_verbs(self, lower: str) -> list[str]:
        seen: dict[str, None] = {}
        for pattern in self.CRUD_VERB_PATTERNS:
            for m in re.finditer(pattern, lower):
                seen[m.group(1)] = None
        return list(seen)

    def _detect_auth(self, lower: str) -> bool:
        return any(re.search(p, lower) for p in self.AUTH_SIGNALS)

    def _detect_mode(self, lower: str, auth_required: bool) -> AppMode:
        is_static_signal = any(re.search(p, lower) for p in self.STATIC_SIGNALS)
        if is_static_signal and not auth_required:
            return AppMode.STATIC
        return AppMode.FLASK

    def _detect_ui_metaphors(self, lower: str) -> list[str]:
        found: list[str] = []
        for metaphor, patterns in self.UI_METAPHORS.items():
            if any(re.search(p, lower) for p in patterns):
                found.append(metaphor)
        return found

    def _build_routes_for_noun(
        self,
        noun: str,
        verbs: list[str],
        auth_required: bool,
    ) -> list[AppObligation]:
        """Build ROUTE obligations for a single domain noun based on verbs."""
        routes: list[AppObligation] = []
        path_base = f"/{noun}s"

        view_verbs = {"view", "list", "browse", "share", "sharing"}
        create_verbs = {"create", "add", "new", "share", "sharing", "publish"}
        edit_verbs = {"edit", "update", "modify", "change"}
        delete_verbs = {"delete", "remove", "archive"}
        search_verbs = {"search", "filter", "find"}

        verb_set = set(verbs)

        if verb_set & view_verbs:
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_list",
                data={
                    "path": path_base,
                    "method": "GET",
                    "auth_required": False,
                    "description": f"List all {noun}s",
                },
                coordinate_name=f"route.{noun}.list",
            ))
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_show",
                data={
                    "path": f"{path_base}/<int:id>",
                    "method": "GET",
                    "auth_required": False,
                    "description": f"Show a single {noun}",
                },
                coordinate_name=f"route.{noun}.show",
            ))

        if verb_set & create_verbs:
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_new",
                data={
                    "path": f"{path_base}/new",
                    "method": "GET",
                    "auth_required": auth_required,
                    "description": f"New {noun} form",
                },
                coordinate_name=f"route.{noun}.new",
            ))
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_create",
                data={
                    "path": path_base,
                    "method": "POST",
                    "auth_required": auth_required,
                    "description": f"Create a {noun}",
                },
                coordinate_name=f"route.{noun}.create",
            ))

        if verb_set & edit_verbs:
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_edit",
                data={
                    "path": f"{path_base}/<int:id>/edit",
                    "method": "GET",
                    "auth_required": auth_required,
                    "description": f"Edit {noun} form",
                },
                coordinate_name=f"route.{noun}.edit",
            ))
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_update",
                data={
                    "path": f"{path_base}/<int:id>",
                    "method": "POST",
                    "auth_required": auth_required,
                    "description": f"Update a {noun}",
                },
                coordinate_name=f"route.{noun}.update",
            ))

        if verb_set & delete_verbs:
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_delete",
                data={
                    "path": f"{path_base}/<int:id>",
                    "method": "POST",
                    "auth_required": auth_required,
                    "description": f"Delete a {noun} (POST + _method override)",
                    "_method": "DELETE",
                },
                coordinate_name=f"route.{noun}.delete",
            ))

        if verb_set & search_verbs:
            routes.append(AppObligation(
                kind=ObligationKind.ROUTE,
                name=f"{noun}_search",
                data={
                    "path": f"{path_base}?q=...",
                    "method": "GET",
                    "auth_required": False,
                    "description": f"Search/filter {noun}s",
                },
                coordinate_name=f"route.{noun}.search",
            ))

        return routes

    def _build_standard_obligations(
        self,
        mode: AppMode,
        auth_required: bool,
        has_forms: bool,
        ui_metaphors: list[str],
    ) -> list[AppObligation]:
        """Build implicit TEMPLATE, CSS_RULE, and JS_MODULE obligations."""
        result: list[AppObligation] = []

        # TEMPLATE: base.html always
        result.append(AppObligation(
            kind=ObligationKind.TEMPLATE,
            name="base.html",
            data={"extends": None, "blocks": ["title", "head", "body", "scripts"]},
            coordinate_name="template.base",
        ))

        # CSS_RULE: mobile-first grid for card grid metaphor; flexbox nav for all
        if "card grid" in ui_metaphors:
            result.append(AppObligation(
                kind=ObligationKind.CSS_RULE,
                name="card_grid",
                data={
                    "selector": ".card-grid",
                    "properties": {
                        "display": "grid",
                        "grid-template-columns": "repeat(auto-fill, minmax(280px, 1fr))",
                        "gap": "1.5rem",
                    },
                    "strategy": "mobile-first",
                },
                coordinate_name="css.card_grid",
            ))

        result.append(AppObligation(
            kind=ObligationKind.CSS_RULE,
            name="flexbox_nav",
            data={
                "selector": "nav",
                "properties": {
                    "display": "flex",
                    "align-items": "center",
                    "gap": "1rem",
                },
                "strategy": "flexbox",
            },
            coordinate_name="css.flexbox_nav",
        ))

        # JS_MODULE: csrf.js if FLASK + auth; form_validation.js if forms exist
        if mode == AppMode.FLASK and auth_required:
            result.append(AppObligation(
                kind=ObligationKind.JS_MODULE,
                name="csrf.js",
                data={"purpose": "Attach CSRF token to AJAX requests", "auto_attach": True},
                coordinate_name="js.csrf",
            ))

        if has_forms:
            result.append(AppObligation(
                kind=ObligationKind.JS_MODULE,
                name="form_validation.js",
                data={"purpose": "Client-side form validation", "strategy": "constraint_api"},
                coordinate_name="js.form_validation",
            ))

        return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert *text* to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


def _auth_route_obligations() -> list[AppObligation]:
    """Return the standard login/signup/logout ROUTE obligations."""
    return [
        AppObligation(
            kind=ObligationKind.ROUTE,
            name="login",
            data={"path": "/login", "method": "GET", "auth_required": False},
            coordinate_name="route.auth.login_get",
        ),
        AppObligation(
            kind=ObligationKind.ROUTE,
            name="login_post",
            data={"path": "/login", "method": "POST", "auth_required": False},
            coordinate_name="route.auth.login_post",
        ),
        AppObligation(
            kind=ObligationKind.ROUTE,
            name="signup",
            data={"path": "/signup", "method": "GET", "auth_required": False},
            coordinate_name="route.auth.signup_get",
        ),
        AppObligation(
            kind=ObligationKind.ROUTE,
            name="signup_post",
            data={"path": "/signup", "method": "POST", "auth_required": False},
            coordinate_name="route.auth.signup_post",
        ),
        AppObligation(
            kind=ObligationKind.ROUTE,
            name="logout",
            data={"path": "/logout", "method": "GET", "auth_required": True},
            coordinate_name="route.auth.logout",
        ),
    ]
