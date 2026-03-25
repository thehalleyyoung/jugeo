"""The view site of a web application — a Grothendieck site for navigation.

Sheaf-theoretic interpretation
------------------------------
A **view site** is a category whose objects are *views* (pages/routes)
and whose morphisms are *navigation transitions* (links, buttons,
redirects).  A **covering family** on a view ``V`` is a collection of
views that together cover a complete user flow starting from ``V``.

*Descent* in this site means:
  1. **Reachability** — every view is reachable from the default
     (landing) view via a chain of navigation morphisms.
  2. **Navigation consistency** — if two morphisms target the same
     view, the user arrives in a consistent state regardless of origin.
  3. **Covering completeness** — every covering family actually spans
     the views it claims to, with no gaps.

When all three descent conditions hold, the navigation presheaf is a
*sheaf* and the generated application has coherent, gap-free navigation.

This module is domain-agnostic: it works for e-commerce stores, blogs,
dashboards, games, documentation sites, social networks — any web
application expressible as a finite view site.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..concept_extractor import ConceptMap, ConceptDomain, Concept

__all__ = [
    # Enums
    "ViewKind",
    "NavigationKind",
    # Dataclasses
    "ViewCoordinate",
    "NavigationMorphism",
    "ViewCoveringFamily",
    # Site
    "ViewSite",
    # Builder & checker
    "ViewSiteBuilder",
    "ViewDescentChecker",
    "DescentObstruction",
]


# ═══════════════════════════════════════════════════════════════════════
# §1  ViewKind — taxonomy of web-application view types
# ═══════════════════════════════════════════════════════════════════════

class ViewKind(str, Enum):
    """Taxonomy of view types that occur across web applications.

    Each member carries a ``typical_components`` method returning the
    UI components that usually appear on that kind of view.
    """

    LANDING = "landing"
    DASHBOARD = "dashboard"
    LIST = "list"
    DETAIL = "detail"
    FORM = "form"
    SETTINGS = "settings"
    PROFILE = "profile"
    SEARCH = "search"
    GALLERY = "gallery"
    EDITOR = "editor"
    CANVAS = "canvas"
    TUTORIAL = "tutorial"
    ABOUT = "about"
    ERROR = "error"
    AUTH_LOGIN = "auth_login"
    AUTH_REGISTER = "auth_register"
    AUTH_RESET = "auth_reset"
    ADMIN = "admin"
    HELP = "help"
    CHANGELOG = "changelog"
    PRICING = "pricing"
    CHECKOUT = "checkout"
    CART = "cart"
    NOTIFICATION = "notification"
    EMPTY_STATE = "empty_state"
    CUSTOM = "custom"

    # ── typical_components ────────────────────────────────────────────

    def typical_components(self) -> list[str]:
        """Return component names that typically appear on this view kind."""
        return _VIEW_KIND_COMPONENTS.get(self, ["header", "main-content", "footer"])


_VIEW_KIND_COMPONENTS: dict[ViewKind, list[str]] = {
    ViewKind.LANDING: [
        "hero-banner", "feature-grid", "call-to-action",
        "testimonials", "footer",
    ],
    ViewKind.DASHBOARD: [
        "stat-cards", "chart-panel", "activity-feed",
        "quick-actions", "sidebar-nav",
    ],
    ViewKind.LIST: [
        "search-bar", "filter-controls", "item-table",
        "pagination", "bulk-actions",
    ],
    ViewKind.DETAIL: [
        "breadcrumb", "detail-header", "detail-body",
        "related-items", "action-buttons",
    ],
    ViewKind.FORM: [
        "form-header", "field-group", "validation-summary",
        "submit-button", "cancel-link",
    ],
    ViewKind.SETTINGS: [
        "settings-nav", "settings-section", "toggle-group",
        "save-button", "danger-zone",
    ],
    ViewKind.PROFILE: [
        "avatar", "profile-header", "bio-section",
        "activity-timeline", "edit-button",
    ],
    ViewKind.SEARCH: [
        "search-input", "filter-sidebar", "result-list",
        "pagination", "no-results-message",
    ],
    ViewKind.GALLERY: [
        "gallery-grid", "lightbox", "filter-bar",
        "sort-controls", "load-more",
    ],
    ViewKind.EDITOR: [
        "toolbar", "editor-area", "preview-pane",
        "status-bar", "save-indicator",
    ],
    ViewKind.CANVAS: [
        "canvas-element", "tool-palette", "layer-panel",
        "zoom-controls", "export-button",
    ],
    ViewKind.TUTORIAL: [
        "step-indicator", "instruction-panel", "interactive-area",
        "next-button", "progress-bar",
    ],
    ViewKind.ABOUT: [
        "hero-section", "team-grid", "mission-statement",
        "contact-info", "footer",
    ],
    ViewKind.ERROR: [
        "error-code", "error-message", "illustration",
        "home-link", "support-link",
    ],
    ViewKind.AUTH_LOGIN: [
        "login-form", "social-login-buttons", "forgot-password-link",
        "register-link", "logo",
    ],
    ViewKind.AUTH_REGISTER: [
        "register-form", "terms-checkbox", "social-register-buttons",
        "login-link", "logo",
    ],
    ViewKind.AUTH_RESET: [
        "reset-form", "instructions-text", "back-to-login-link",
        "logo",
    ],
    ViewKind.ADMIN: [
        "admin-sidebar", "data-table", "action-bar",
        "stat-overview", "user-management",
    ],
    ViewKind.HELP: [
        "search-bar", "category-nav", "article-list",
        "faq-accordion", "contact-support",
    ],
    ViewKind.CHANGELOG: [
        "version-list", "release-entry", "tag-filters",
        "date-range", "diff-viewer",
    ],
    ViewKind.PRICING: [
        "plan-cards", "feature-comparison", "toggle-annual",
        "faq-section", "call-to-action",
    ],
    ViewKind.CHECKOUT: [
        "order-summary", "payment-form", "address-form",
        "promo-code-input", "place-order-button",
    ],
    ViewKind.CART: [
        "cart-item-list", "quantity-controls", "subtotal",
        "checkout-button", "continue-shopping-link",
    ],
    ViewKind.NOTIFICATION: [
        "notification-list", "filter-tabs", "mark-all-read",
        "empty-state", "notification-settings-link",
    ],
    ViewKind.EMPTY_STATE: [
        "illustration", "message-text", "primary-action",
        "secondary-action",
    ],
    ViewKind.CUSTOM: [
        "header", "main-content", "footer",
    ],
}


# ═══════════════════════════════════════════════════════════════════════
# §2  NavigationKind — taxonomy of navigation transitions
# ═══════════════════════════════════════════════════════════════════════

class NavigationKind(str, Enum):
    """How a user navigates from one view to another."""

    MENU_LINK = "menu_link"
    BUTTON = "button"
    BREADCRUMB = "breadcrumb"
    REDIRECT = "redirect"
    BACK = "back"
    TAB = "tab"
    MODAL_OPEN = "modal_open"
    FORM_SUBMIT = "form_submit"


# ═══════════════════════════════════════════════════════════════════════
# §3  ViewCoordinate — a coordinate in the view site
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ViewCoordinate:
    """A coordinate (object) in the view site.

    Each coordinate identifies a single view in the application,
    together with its route, display metadata, and data dependencies.
    """

    id: str
    kind: ViewKind
    route: str
    label: str
    icon: str = "📄"
    requires_auth: bool = False
    is_default: bool = False
    parent: str | None = None
    data_dependencies: list[str] = field(default_factory=list)

    def typical_components(self) -> list[str]:
        """Delegate to the view kind."""
        return self.kind.typical_components()

    @property
    def is_auth_view(self) -> bool:
        """True for login / register / reset views."""
        return self.kind in (
            ViewKind.AUTH_LOGIN,
            ViewKind.AUTH_REGISTER,
            ViewKind.AUTH_RESET,
        )

    @property
    def depth(self) -> int:
        """Nesting depth (0 for root views)."""
        return self.route.strip("#/").count("/")


# ═══════════════════════════════════════════════════════════════════════
# §4  NavigationMorphism — a morphism in the view site
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class NavigationMorphism:
    """A morphism (navigation transition) between two views.

    ``source`` and ``target`` are view ids.  ``kind`` describes
    *how* the user performs the transition (menu click, button,
    breadcrumb, etc.).
    """

    source: str
    target: str
    kind: NavigationKind
    label: str = ""

    @property
    def is_backward(self) -> bool:
        """True for back-navigation or breadcrumb morphisms."""
        return self.kind in (NavigationKind.BACK, NavigationKind.BREADCRUMB)


# ═══════════════════════════════════════════════════════════════════════
# §5  ViewCoveringFamily — a covering family in the view site
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ViewCoveringFamily:
    """A covering family: a set of views that together cover a user flow.

    For example the *primary flow* covering family for a blog might be
    ``{landing, post-list, post-detail}`` — the minimal set of views a
    visitor must see to complete the core experience.
    """

    name: str
    view_ids: list[str] = field(default_factory=list)
    is_complete: bool = False

    def covers(self, view_id: str) -> bool:
        """Does this family include *view_id*?"""
        return view_id in self.view_ids

    def missing_from(self, all_view_ids: list[str]) -> list[str]:
        """View ids in *all_view_ids* not covered by this family."""
        covered = set(self.view_ids)
        return [v for v in all_view_ids if v not in covered]


# ═══════════════════════════════════════════════════════════════════════
# §6  DescentObstruction — a single descent failure
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DescentObstruction:
    """A single descent-check failure.

    Obstructions are collected by :class:`ViewDescentChecker` and
    reported to the caller so they can repair the site.
    """

    kind: str
    message: str
    view_ids: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# §7  ViewSite — the Grothendieck site of views
# ═══════════════════════════════════════════════════════════════════════

class ViewSite:
    """The Grothendieck site whose objects are views and morphisms are
    navigation transitions.

    This is the central structure of the view-site theory.  It stores
    views, morphisms, and covering families, and exposes queries for
    reachability, connectivity, and coverage analysis.
    """

    def __init__(self) -> None:
        self._views: dict[str, ViewCoordinate] = {}
        self._morphisms: list[NavigationMorphism] = []
        self._covering_families: list[ViewCoveringFamily] = []

    # ── mutators ──────────────────────────────────────────────────────

    def add_view(self, view: ViewCoordinate) -> None:
        """Register a view coordinate in the site."""
        self._views[view.id] = view

    def add_morphism(self, morphism: NavigationMorphism) -> None:
        """Register a navigation morphism."""
        self._morphisms.append(morphism)

    def add_covering_family(self, family: ViewCoveringFamily) -> None:
        """Register a covering family."""
        self._covering_families.append(family)

    # ── accessors ─────────────────────────────────────────────────────

    def views(self) -> list[ViewCoordinate]:
        """All registered views, ordered by route."""
        return sorted(self._views.values(), key=lambda v: v.route)

    def morphisms(self) -> list[NavigationMorphism]:
        """All registered navigation morphisms."""
        return list(self._morphisms)

    def covering_families(self) -> list[ViewCoveringFamily]:
        """All registered covering families."""
        return list(self._covering_families)

    def get_view(self, view_id: str) -> ViewCoordinate | None:
        """Look up a view by id."""
        return self._views.get(view_id)

    # ── reachability & connectivity ───────────────────────────────────

    def navigable_from(self, view_id: str) -> list[ViewCoordinate]:
        """Return views directly reachable from *view_id* via one morphism."""
        targets: list[ViewCoordinate] = []
        for m in self._morphisms:
            if m.source == view_id and m.target in self._views:
                targets.append(self._views[m.target])
        return targets

    def reachable_from(self, view_id: str) -> set[str]:
        """Return all view ids transitively reachable from *view_id*."""
        visited: set[str] = set()
        stack = [view_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for m in self._morphisms:
                if m.source == current and m.target not in visited:
                    stack.append(m.target)
        return visited

    def is_connected(self) -> bool:
        """Can every view be reached from the default view?"""
        default = self.default_view()
        if default is None:
            return len(self._views) == 0
        reachable = self.reachable_from(default.id)
        return reachable >= set(self._views.keys())

    def orphan_views(self) -> list[ViewCoordinate]:
        """Views with no incoming morphisms (except the default view)."""
        targeted: set[str] = set()
        for m in self._morphisms:
            targeted.add(m.target)
        orphans: list[ViewCoordinate] = []
        for v in self._views.values():
            if v.id not in targeted and not v.is_default:
                orphans.append(v)
        return sorted(orphans, key=lambda v: v.route)

    # ── coverage analysis ─────────────────────────────────────────────

    def covering_obligations(self) -> list[str]:
        """View ids not covered by *any* covering family."""
        covered: set[str] = set()
        for fam in self._covering_families:
            covered.update(fam.view_ids)
        all_ids = set(self._views.keys())
        return sorted(all_ids - covered)

    # ── default & filtering ───────────────────────────────────────────

    def default_view(self) -> ViewCoordinate | None:
        """Return the default (landing) view, or ``None``."""
        for v in self._views.values():
            if v.is_default:
                return v
        # Fall back to the view with the shallowest route.
        if self._views:
            return min(self._views.values(), key=lambda v: (v.depth, v.route))
        return None

    def auth_views(self) -> list[ViewCoordinate]:
        """Views that require authentication."""
        return [v for v in self.views() if v.requires_auth]

    def public_views(self) -> list[ViewCoordinate]:
        """Views accessible without authentication."""
        return [v for v in self.views() if not v.requires_auth]

    # ── nav-item derivation ───────────────────────────────────────────

    def to_nav_items(self) -> list[dict[str, Any]]:
        """Derive a navigation-menu structure from the site.

        Returns a list of dicts suitable for rendering a nav bar or
        sidebar.  Only top-level (no parent) non-auth views are
        included; child views are nested under their parent.
        """
        top_level: list[ViewCoordinate] = []
        children_map: dict[str, list[ViewCoordinate]] = {}

        for v in self.views():
            if v.is_auth_view or v.kind == ViewKind.ERROR:
                continue
            if v.parent is None:
                top_level.append(v)
            else:
                children_map.setdefault(v.parent, []).append(v)

        items: list[dict[str, Any]] = []
        for v in top_level:
            item: dict[str, Any] = {
                "id": v.id,
                "label": v.label,
                "icon": v.icon,
                "route": v.route,
                "is_default": v.is_default,
            }
            kids = children_map.get(v.id, [])
            if kids:
                item["children"] = [
                    {
                        "id": c.id,
                        "label": c.label,
                        "icon": c.icon,
                        "route": c.route,
                    }
                    for c in kids
                ]
            items.append(item)
        return items

    # ── dunder helpers ────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._views)

    def __contains__(self, view_id: str) -> bool:
        return view_id in self._views

    def __repr__(self) -> str:
        return (
            f"ViewSite(views={len(self._views)}, "
            f"morphisms={len(self._morphisms)}, "
            f"families={len(self._covering_families)})"
        )


# ═══════════════════════════════════════════════════════════════════════
# §8  ViewSiteBuilder — construct a ViewSite from a ConceptMap
# ═══════════════════════════════════════════════════════════════════════

class ViewSiteBuilder:
    """Build a :class:`ViewSite` from a :class:`ConceptMap`.

    The builder inspects which concept domains are active and creates
    views, morphisms, and covering families appropriate for those
    domains.  The result is a complete, coherent view site regardless
    of what combination of domains the prompt activates.
    """

    # ── domain → view specs mapping ───────────────────────────────────
    # Each entry: (view_id, ViewKind, route, label, icon, requires_auth)

    _DOMAIN_VIEWS: dict[ConceptDomain, list[tuple[str, ViewKind, str, str, str, bool]]] = {
        ConceptDomain.GAME: [
            ("play", ViewKind.CANVAS, "#/play", "Play", "🎮", False),
            ("scores", ViewKind.LIST, "#/scores", "Scores", "🏆", False),
        ],
        ConceptDomain.ART: [
            ("gallery", ViewKind.GALLERY, "#/gallery", "Gallery", "🖼️", False),
            ("editor", ViewKind.EDITOR, "#/editor", "Editor", "🎨", False),
            ("canvas", ViewKind.CANVAS, "#/canvas", "Canvas", "🖌️", False),
        ],
        ConceptDomain.MEDIA: [
            ("player", ViewKind.DETAIL, "#/player", "Player", "🎵", False),
            ("library", ViewKind.LIST, "#/library", "Library", "📚", False),
        ],
        ConceptDomain.DATA: [
            ("dashboard", ViewKind.DASHBOARD, "#/dashboard", "Dashboard", "📊", False),
            ("data-list", ViewKind.LIST, "#/data", "Data", "📋", False),
            ("data-detail", ViewKind.DETAIL, "#/data/:id", "Detail", "🔍", False),
        ],
        ConceptDomain.UI: [
            # UI-domain concepts add structural views handled by the
            # universal set below; no extra domain-specific views needed.
        ],
        ConceptDomain.SOCIAL: [
            ("feed", ViewKind.LIST, "#/feed", "Feed", "📰", True),
            ("profile", ViewKind.PROFILE, "#/profile", "Profile", "👤", True),
            ("messages", ViewKind.LIST, "#/messages", "Messages", "💬", True),
        ],
        ConceptDomain.EDUCATION: [
            ("courses", ViewKind.LIST, "#/courses", "Courses", "📖", False),
            ("lesson", ViewKind.TUTORIAL, "#/lesson/:id", "Lesson", "📝", False),
            ("progress", ViewKind.DASHBOARD, "#/progress", "Progress", "📈", True),
        ],
        ConceptDomain.VISUALIZATION: [
            ("viz-dashboard", ViewKind.DASHBOARD, "#/viz", "Visualisations", "📊", False),
            ("viz-detail", ViewKind.DETAIL, "#/viz/:id", "Chart", "📉", False),
        ],
    }

    # Views that are always present regardless of domain.
    _UNIVERSAL_VIEWS: list[tuple[str, ViewKind, str, str, str, bool, bool]] = [
        # (id, kind, route, label, icon, requires_auth, is_default)
        ("home", ViewKind.LANDING, "#/", "Home", "🏠", False, True),
        ("settings", ViewKind.SETTINGS, "#/settings", "Settings", "⚙️", False, False),
        ("about", ViewKind.ABOUT, "#/about", "About", "ℹ️", False, False),
    ]

    # ── public API ────────────────────────────────────────────────────

    @classmethod
    def from_concepts(cls, concepts: ConceptMap) -> ViewSite:
        """Build a complete view site from *concepts*.

        Steps:
          1. Add universal views (home, settings, about).
          2. Inspect active domains and add domain-specific views.
          3. Add auth views if any view requires authentication.
          4. Wire morphisms for all reachable transitions.
          5. Build covering families for primary and secondary flows.
        """
        builder = cls()
        site = ViewSite()

        # 1 — universal views
        builder._add_universal_views(site)

        # 2 — domain-specific views
        builder._add_domain_views(site, concepts)

        # 3 — auth views (only if at least one view requires auth)
        if any(v.requires_auth for v in site.views()):
            builder._add_auth_views(site)

        # 4 — morphisms
        builder._add_morphisms(site)

        # 5 — covering families
        builder._add_covering_families(site)

        return site

    # ── internal helpers ──────────────────────────────────────────────

    def _add_universal_views(self, site: ViewSite) -> None:
        for vid, kind, route, label, icon, auth, default in self._UNIVERSAL_VIEWS:
            site.add_view(ViewCoordinate(
                id=vid, kind=kind, route=route,
                label=label, icon=icon,
                requires_auth=auth, is_default=default,
            ))

    def _add_domain_views(self, site: ViewSite, concepts: ConceptMap) -> None:
        for domain in concepts.domains:
            specs = self._DOMAIN_VIEWS.get(domain, [])
            for vid, kind, route, label, icon, auth in specs:
                if vid not in site:
                    site.add_view(ViewCoordinate(
                        id=vid, kind=kind, route=route,
                        label=label, icon=icon,
                        requires_auth=auth,
                    ))

        # If prompt mentions tutorials or help, add those views.
        if concepts.has("tutorial"):
            if "tutorial" not in site:
                site.add_view(ViewCoordinate(
                    id="tutorial", kind=ViewKind.TUTORIAL,
                    route="#/tutorial", label="Tutorial", icon="📖",
                ))

    def _add_auth_views(self, site: ViewSite) -> None:
        auth_specs: list[tuple[str, ViewKind, str, str, str]] = [
            ("login", ViewKind.AUTH_LOGIN, "#/login", "Log In", "🔑"),
            ("register", ViewKind.AUTH_REGISTER, "#/register", "Register", "📝"),
            ("reset-password", ViewKind.AUTH_RESET, "#/reset", "Reset Password", "🔒"),
        ]
        for vid, kind, route, label, icon in auth_specs:
            if vid not in site:
                site.add_view(ViewCoordinate(
                    id=vid, kind=kind, route=route,
                    label=label, icon=icon,
                ))

    def _add_morphisms(self, site: ViewSite) -> None:
        """Wire navigation morphisms between all views.

        Strategy:
          - The default view links to every top-level view (menu links).
          - Every non-default view has a back-morphism to the default.
          - Parent → child and child → parent morphisms.
          - Auth views link to each other and redirect to home on success.
          - List → detail morphisms.
          - Form-submit morphisms redirect to their parent or list.
        """
        views = {v.id: v for v in site.views()}
        default = site.default_view()
        if default is None:
            return

        for v in views.values():
            if v.id == default.id:
                continue

            # default → v  (menu link)
            if v.parent is None and not v.is_auth_view:
                site.add_morphism(NavigationMorphism(
                    source=default.id, target=v.id,
                    kind=NavigationKind.MENU_LINK, label=v.label,
                ))

            # v → default  (back / logo click)
            site.add_morphism(NavigationMorphism(
                source=v.id, target=default.id,
                kind=NavigationKind.BACK, label=default.label,
            ))

            # parent ↔ child
            if v.parent and v.parent in views:
                site.add_morphism(NavigationMorphism(
                    source=v.parent, target=v.id,
                    kind=NavigationKind.BUTTON, label=v.label,
                ))
                site.add_morphism(NavigationMorphism(
                    source=v.id, target=v.parent,
                    kind=NavigationKind.BREADCRUMB, label=views[v.parent].label,
                ))

        # Auth flow: home → login, login ↔ register, login → home
        if "login" in views:
            site.add_morphism(NavigationMorphism(
                source=default.id, target="login",
                kind=NavigationKind.BUTTON, label="Log In",
            ))
            site.add_morphism(NavigationMorphism(
                source="login", target=default.id,
                kind=NavigationKind.REDIRECT, label="After login",
            ))
        if "login" in views and "register" in views:
            site.add_morphism(NavigationMorphism(
                source="login", target="register",
                kind=NavigationKind.BUTTON, label="Register",
            ))
            site.add_morphism(NavigationMorphism(
                source="register", target="login",
                kind=NavigationKind.BUTTON, label="Log In",
            ))
        if "reset-password" in views and "login" in views:
            site.add_morphism(NavigationMorphism(
                source="login", target="reset-password",
                kind=NavigationKind.BUTTON, label="Forgot password?",
            ))
            site.add_morphism(NavigationMorphism(
                source="reset-password", target="login",
                kind=NavigationKind.BUTTON, label="Back to login",
            ))

        # Views requiring auth redirect to login when unauthenticated.
        if "login" in views:
            for v in views.values():
                if v.requires_auth:
                    site.add_morphism(NavigationMorphism(
                        source="login", target=v.id,
                        kind=NavigationKind.REDIRECT, label=f"After login → {v.label}",
                    ))

        # List → detail morphisms
        self._add_list_detail_morphisms(site, views)

        # Cross-links: every top-level non-auth view links to every other
        top_level = [
            v for v in views.values()
            if v.parent is None and not v.is_auth_view
            and v.id != default.id
        ]
        for a in top_level:
            for b in top_level:
                if a.id != b.id:
                    site.add_morphism(NavigationMorphism(
                        source=a.id, target=b.id,
                        kind=NavigationKind.MENU_LINK, label=b.label,
                    ))

    @staticmethod
    def _add_list_detail_morphisms(
        site: ViewSite,
        views: dict[str, ViewCoordinate],
    ) -> None:
        """Connect LIST views to nearby DETAIL views."""
        list_views = [v for v in views.values() if v.kind == ViewKind.LIST]
        detail_views = [v for v in views.values() if v.kind == ViewKind.DETAIL]

        for lv in list_views:
            # Find the detail view whose route prefix matches.
            prefix = lv.route.rstrip("/")
            for dv in detail_views:
                if dv.route.startswith(prefix) or _share_route_stem(lv.route, dv.route):
                    site.add_morphism(NavigationMorphism(
                        source=lv.id, target=dv.id,
                        kind=NavigationKind.BUTTON, label=dv.label,
                    ))
                    site.add_morphism(NavigationMorphism(
                        source=dv.id, target=lv.id,
                        kind=NavigationKind.BREADCRUMB, label=lv.label,
                    ))

    def _add_covering_families(self, site: ViewSite) -> None:
        """Create covering families for primary and secondary flows."""
        all_ids = [v.id for v in site.views()]
        public_ids = [v.id for v in site.public_views()]
        auth_ids = [v.id for v in site.views() if v.is_auth_view]
        authed_ids = [v.id for v in site.auth_views()]

        # Primary flow: all public, non-auth views
        site.add_covering_family(ViewCoveringFamily(
            name="primary",
            view_ids=public_ids,
            is_complete=(set(public_ids) >= set(all_ids)),
        ))

        # Auth flow (if any auth views exist)
        if auth_ids:
            site.add_covering_family(ViewCoveringFamily(
                name="auth-flow",
                view_ids=auth_ids,
                is_complete=True,
            ))

        # Authenticated user flow (if any views require auth)
        if authed_ids:
            site.add_covering_family(ViewCoveringFamily(
                name="authenticated",
                view_ids=authed_ids,
                is_complete=True,
            ))

        # Full coverage: all views
        site.add_covering_family(ViewCoveringFamily(
            name="full",
            view_ids=all_ids,
            is_complete=True,
        ))


# ═══════════════════════════════════════════════════════════════════════
# §9  ViewDescentChecker — verify descent conditions
# ═══════════════════════════════════════════════════════════════════════

class ViewDescentChecker:
    """Verify that a :class:`ViewSite` satisfies the three descent
    conditions: reachability, navigation consistency, and covering
    completeness.

    Returns a (possibly empty) list of :class:`DescentObstruction`
    instances.  An empty list means descent holds and the navigation
    presheaf is a sheaf.
    """

    def __init__(self, site: ViewSite) -> None:
        self._site = site

    # ── public API ────────────────────────────────────────────────────

    def check_all(self) -> list[DescentObstruction]:
        """Run all descent checks and return every obstruction found."""
        obstructions: list[DescentObstruction] = []
        obstructions.extend(self.check_reachability())
        obstructions.extend(self.check_navigation_consistency())
        obstructions.extend(self.check_covering_completeness())
        return obstructions

    def is_sheaf(self) -> bool:
        """True when descent holds (no obstructions)."""
        return len(self.check_all()) == 0

    # ── individual checks ─────────────────────────────────────────────

    def check_reachability(self) -> list[DescentObstruction]:
        """All views must be reachable from the default view.

        Returns one obstruction per unreachable view.
        """
        default = self._site.default_view()
        if default is None:
            if len(self._site) == 0:
                return []
            return [DescentObstruction(
                kind="no_default",
                message="No default view defined; cannot check reachability.",
                view_ids=[v.id for v in self._site.views()],
            )]

        reachable = self._site.reachable_from(default.id)
        unreachable = set(v.id for v in self._site.views()) - reachable
        obstructions: list[DescentObstruction] = []
        for vid in sorted(unreachable):
            obstructions.append(DescentObstruction(
                kind="unreachable",
                message=f"View '{vid}' is not reachable from the default view '{default.id}'.",
                view_ids=[vid],
            ))
        return obstructions

    def check_navigation_consistency(self) -> list[DescentObstruction]:
        """Navigation morphisms must reference existing views.

        Every morphism's source and target must be a registered view.
        """
        known = set(v.id for v in self._site.views())
        obstructions: list[DescentObstruction] = []
        for m in self._site.morphisms():
            dangling: list[str] = []
            if m.source not in known:
                dangling.append(m.source)
            if m.target not in known:
                dangling.append(m.target)
            if dangling:
                obstructions.append(DescentObstruction(
                    kind="dangling_morphism",
                    message=(
                        f"Morphism {m.source} → {m.target} references "
                        f"unknown view(s): {', '.join(dangling)}."
                    ),
                    view_ids=dangling,
                ))

        # Check for duplicate morphisms (same source, target, kind).
        seen: set[tuple[str, str, str]] = set()
        for m in self._site.morphisms():
            key = (m.source, m.target, m.kind.value)
            if key in seen:
                obstructions.append(DescentObstruction(
                    kind="duplicate_morphism",
                    message=(
                        f"Duplicate morphism {m.source} → {m.target} "
                        f"({m.kind.value})."
                    ),
                    view_ids=[m.source, m.target],
                ))
            seen.add(key)

        return obstructions

    def check_covering_completeness(self) -> list[DescentObstruction]:
        """Every covering family that claims ``is_complete`` must actually
        reference only existing views.  At least one family must cover
        all views.
        """
        known = set(v.id for v in self._site.views())
        obstructions: list[DescentObstruction] = []

        for fam in self._site.covering_families():
            unknown = [vid for vid in fam.view_ids if vid not in known]
            if unknown:
                obstructions.append(DescentObstruction(
                    kind="phantom_cover",
                    message=(
                        f"Covering family '{fam.name}' references "
                        f"unknown views: {', '.join(unknown)}."
                    ),
                    view_ids=unknown,
                ))

        # At least one family should cover all views.
        uncovered = self._site.covering_obligations()
        if uncovered:
            obstructions.append(DescentObstruction(
                kind="incomplete_coverage",
                message=(
                    f"{len(uncovered)} view(s) not covered by any "
                    f"family: {', '.join(uncovered)}."
                ),
                view_ids=uncovered,
            ))

        return obstructions


# ═══════════════════════════════════════════════════════════════════════
# §10  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _share_route_stem(route_a: str, route_b: str) -> bool:
    """True when two hash-routes share the same first path segment.

    >>> _share_route_stem("#/data", "#/data/:id")
    True
    >>> _share_route_stem("#/scores", "#/data/:id")
    False
    """
    def _stem(r: str) -> str:
        return r.strip("#/").split("/")[0] if r.strip("#/") else ""

    return _stem(route_a) == _stem(route_b) and _stem(route_a) != ""
