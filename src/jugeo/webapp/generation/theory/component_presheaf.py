"""Components as a presheaf over the view site with fibres in HTML, CSS, JS.

Sheaf-theoretic interpretation
------------------------------
A **component** is a *local section* of a presheaf over the view site.
At each view (object in the site) the section restricts to a triple
(HTML fragment, CSS selectors, JS hooks) — the three **fibres** of the
component presheaf.

The **descent condition** for a component requires:

1. **Restriction coherence** — if a component appears on views *U* and
   *V*, its restriction along any navigation morphism *U → V* produces
   the same section at *V*.  (The navbar looks the same no matter
   which view you arrived from.)

2. **Fibre coherence** — the HTML, CSS, and JS fibres agree: every CSS
   class referenced in the HTML fragment exists as a CSS selector, and
   every ``data-hook`` in HTML has a matching JS handler.

3. **Gluing** — local component sections on a covering family glue
   into a global section: the component catalog for the entire
   application is uniquely determined by its restrictions to views.

When all three conditions hold the component presheaf is a **sheaf**
and the generated application has a coherent, complete component
system.

This module is domain-agnostic and works for any kind of web
application expressible over a finite view site.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "ComponentFiber",
    # Dataclasses
    "ComponentObligation",
    "ComponentSection",
    # Presheaf
    "ComponentPresheaf",
    # Catalog
    "ComponentCatalog",
]


# ═══════════════════════════════════════════════════════════════════════
# §1  ComponentFiber — the three language fibres
# ═══════════════════════════════════════════════════════════════════════

class ComponentFiber(str, Enum):
    """The three language fibres of the component presheaf.

    Every component has a representation in each fibre:
      * **HTML** — the structural markup fragment.
      * **CSS**  — the styling selectors and properties.
      * **JS**   — the behavioral hooks and event handlers.

    Coherence across fibres means that the HTML, CSS, and JS
    representations agree: every CSS class in HTML exists in CSS,
    and every data-hook in HTML has a matching JS handler.
    """

    HTML = "html"
    CSS = "css"
    JS = "js"


# ═══════════════════════════════════════════════════════════════════════
# §2  ComponentObligation — what a component must provide
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ComponentObligation:
    """The obligation a component must satisfy in all three fibres.

    An obligation specifies *what* structure, style, and behavior the
    component must provide.  It does not prescribe exact code — only
    what the generated code must contain.

    Parameters
    ----------
    component_kind : str
        Canonical name of the component (``navbar``, ``hero``, etc.).
    html_obligation : str
        Description of required HTML structure.
    css_obligation : str
        Description of required CSS classes/properties.
    js_obligation : str
        Description of required JS behavior.
    views : list[str]
        View ids where this component must appear.
    interactive : bool
        If ``True``, the component requires JS event handling.
    html_skeleton : str
        Optional HTML skeleton showing the expected DOM outline.
    css_classes : list[str]
        Canonical CSS classes the component must use.
    js_hooks : list[str]
        ``data-hook`` values the component exposes to JS.
    """

    component_kind: str
    html_obligation: str
    css_obligation: str
    js_obligation: str
    views: list[str] = field(default_factory=list)
    interactive: bool = False
    html_skeleton: str = ""
    css_classes: list[str] = field(default_factory=list)
    js_hooks: list[str] = field(default_factory=list)

    @property
    def is_global(self) -> bool:
        """True if the component appears on every view (e.g. navbar, footer)."""
        return len(self.views) == 0

    @property
    def fibers(self) -> list[ComponentFiber]:
        """Fibres this component occupies (always all three)."""
        return [ComponentFiber.HTML, ComponentFiber.CSS, ComponentFiber.JS]


# ═══════════════════════════════════════════════════════════════════════
# §3  ComponentSection — a component's local section at a view
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ComponentSection:
    """A local section of the component presheaf at a specific view.

    This is the *restriction* of a component to one view.  It contains
    the concrete HTML fragment, CSS selectors, and JS hooks that the
    component contributes to that view.

    Parameters
    ----------
    component_id : str
        Matches the ``component_kind`` of the originating obligation.
    view_id : str
        The view this section is restricted to.
    html_fragment : str
        Concrete HTML fragment for this component at this view.
    css_selectors : list[str]
        CSS selectors this component adds at this view.
    js_hooks : list[str]
        ``data-hook`` values this component uses at this view.
    data_attributes : dict[str, str]
        Additional ``data-*`` attributes on the root element.
    """

    component_id: str
    view_id: str
    html_fragment: str = ""
    css_selectors: list[str] = field(default_factory=list)
    js_hooks: list[str] = field(default_factory=list)
    data_attributes: dict[str, str] = field(default_factory=dict)

    @property
    def css_classes_used(self) -> set[str]:
        """Extract CSS class names referenced in the HTML fragment."""
        return _extract_classes(self.html_fragment)

    @property
    def data_hooks_used(self) -> set[str]:
        """Extract ``data-hook`` values from the HTML fragment."""
        return set(re.findall(r'data-hook="([^"]*)"', self.html_fragment))


def _extract_classes(html: str) -> set[str]:
    """Extract all individual CSS class names from an HTML string."""
    classes: set[str] = set()
    for match in re.finditer(r'class="([^"]*)"', html):
        for cls in match.group(1).split():
            if cls:
                classes.add(cls)
    return classes


# ═══════════════════════════════════════════════════════════════════════
# §4  ComponentPresheaf — the presheaf over the view site
# ═══════════════════════════════════════════════════════════════════════

class ComponentPresheaf:
    """The component presheaf over the view site.

    Objects: views.
    Morphisms: navigation transitions.
    Sections at a view: the list of component sections assigned there.

    Descent is verified by :meth:`check_descent`, which confirms that
    global components (navbar, footer, etc.) restrict identically to
    every view, and shared components are consistently accessible.

    Coherence across fibres is verified by
    :meth:`html_css_js_coherence`, which checks that CSS selectors,
    HTML classes, and JS hooks agree.
    """

    def __init__(self) -> None:
        self._obligations: dict[str, ComponentObligation] = {}
        self._sections: dict[str, list[ComponentSection]] = {}  # view_id -> sections
        self._all_views: list[str] = []

    # ── mutators ──────────────────────────────────────────────────────

    def add_component(self, obligation: ComponentObligation) -> None:
        """Register a component obligation in the presheaf.

        If the obligation's ``views`` list is empty the component is
        treated as **global** and will be expected on every view.
        """
        self._obligations[obligation.component_kind] = obligation

    def add_section(self, section: ComponentSection) -> None:
        """Register a local section at a specific view."""
        self._sections.setdefault(section.view_id, []).append(section)
        if section.view_id not in self._all_views:
            self._all_views.append(section.view_id)

    def register_views(self, view_ids: list[str]) -> None:
        """Inform the presheaf of the complete set of views."""
        for vid in view_ids:
            if vid not in self._all_views:
                self._all_views.append(vid)

    # ── accessors ─────────────────────────────────────────────────────

    def obligations(self) -> list[ComponentObligation]:
        """All registered component obligations."""
        return list(self._obligations.values())

    def section_at(self, view_id: str) -> list[ComponentSection]:
        """Return component sections restricted to *view_id*.

        This is the *stalk* of the presheaf at *view_id*.
        """
        return list(self._sections.get(view_id, []))

    def restrict(self, view_from: str, view_to: str) -> list[ComponentSection]:
        """Restriction map: restrict sections at *view_from* to *view_to*.

        In the component presheaf, restriction means: take the sections
        at *view_from* that are global (appear on all views) or whose
        obligation lists *view_to* as a target, and return copies
        re-targeted at *view_to*.
        """
        restricted: list[ComponentSection] = []
        for section in self._sections.get(view_from, []):
            obligation = self._obligations.get(section.component_id)
            if obligation is None:
                continue
            # Global components restrict to every view.
            if obligation.is_global:
                restricted.append(ComponentSection(
                    component_id=section.component_id,
                    view_id=view_to,
                    html_fragment=section.html_fragment,
                    css_selectors=list(section.css_selectors),
                    js_hooks=list(section.js_hooks),
                    data_attributes=dict(section.data_attributes),
                ))
            # Non-global components restrict only if view_to is in their view list.
            elif view_to in obligation.views:
                restricted.append(ComponentSection(
                    component_id=section.component_id,
                    view_id=view_to,
                    html_fragment=section.html_fragment,
                    css_selectors=list(section.css_selectors),
                    js_hooks=list(section.js_hooks),
                    data_attributes=dict(section.data_attributes),
                ))
        return restricted

    # ── descent checks ────────────────────────────────────────────────

    def check_descent(self) -> list[str]:
        """Verify the descent (gluing) condition across views.

        Descent requires:
          1. **Navbar consistency** — the navbar section is identical on
             every view.
          2. **Footer consistency** — the footer section is identical on
             every view.
          3. **Shared-component accessibility** — components like modal,
             toast, and loading-screen are reachable from every view.
          4. **Obligation coverage** — every obligation has at least one
             section somewhere.

        Returns a list of obstruction messages (empty = descent holds).
        """
        obstructions: list[str] = []

        # 1 — Nav consistency
        obstructions.extend(self._check_global_consistency("navbar"))

        # 2 — Footer consistency
        obstructions.extend(self._check_global_consistency("footer"))

        # 3 — Shared-component accessibility
        shared_kinds = {"modal", "toast", "loading-screen"}
        for kind in shared_kinds:
            if kind in self._obligations:
                views_with = {
                    s.view_id
                    for sections in self._sections.values()
                    for s in sections
                    if s.component_id == kind
                }
                missing = set(self._all_views) - views_with
                if missing:
                    obstructions.append(
                        f"Shared component '{kind}' missing from views: "
                        f"{sorted(missing)}"
                    )

        # 4 — Obligation coverage
        all_section_kinds = {
            s.component_id
            for sections in self._sections.values()
            for s in sections
        }
        for kind, obligation in self._obligations.items():
            if kind not in all_section_kinds:
                obstructions.append(
                    f"Obligation '{kind}' has no section on any view"
                )

        return obstructions

    def _check_global_consistency(self, component_kind: str) -> list[str]:
        """Check that a global component is identical across all views."""
        obstructions: list[str] = []
        if component_kind not in self._obligations:
            return obstructions

        obligation = self._obligations[component_kind]
        if not obligation.is_global:
            return obstructions

        # Collect the HTML fragments for this component on each view.
        fragments: dict[str, str] = {}
        for view_id, sections in self._sections.items():
            for s in sections:
                if s.component_id == component_kind:
                    fragments[view_id] = s.html_fragment

        # Check all views have it.
        missing = set(self._all_views) - set(fragments.keys())
        if missing:
            obstructions.append(
                f"Global component '{component_kind}' missing from views: "
                f"{sorted(missing)}"
            )

        # Check consistency — all fragments should be identical.
        unique_fragments = set(fragments.values())
        if len(unique_fragments) > 1:
            obstructions.append(
                f"Global component '{component_kind}' differs across views: "
                f"found {len(unique_fragments)} distinct fragments on "
                f"{sorted(fragments.keys())}"
            )

        return obstructions

    # ── fibre coherence ───────────────────────────────────────────────

    def html_css_js_coherence(self) -> list[str]:
        """Verify fibre coherence: HTML ↔ CSS ↔ JS agreement.

        Checks:
          1. Every CSS class referenced in any HTML fragment has at
             least one matching CSS selector in the obligation's
             ``css_classes``.
          2. Every ``data-hook`` in any HTML fragment has a matching
             entry in the section's ``js_hooks``.
          3. Every JS hook declared in a section has a matching
             ``data-hook`` in the HTML fragment.

        Returns a list of obstruction messages (empty = coherent).
        """
        obstructions: list[str] = []

        for view_id, sections in self._sections.items():
            for section in sections:
                obligation = self._obligations.get(section.component_id)
                if obligation is None:
                    continue

                # 1 — HTML classes vs obligation CSS classes
                html_classes = _extract_classes(section.html_fragment)
                if obligation.css_classes:
                    expected_css = set(obligation.css_classes)
                    for cls in html_classes:
                        if cls not in expected_css and not cls.startswith("is-"):
                            obstructions.append(
                                f"[{view_id}/{section.component_id}] "
                                f"HTML class '{cls}' has no matching CSS "
                                f"obligation"
                            )

                # 2 — HTML data-hooks vs section JS hooks
                html_hooks = set(
                    re.findall(r'data-hook="([^"]*)"', section.html_fragment)
                )
                declared_hooks = set(section.js_hooks)
                for hook in html_hooks:
                    if hook not in declared_hooks:
                        obstructions.append(
                            f"[{view_id}/{section.component_id}] "
                            f"HTML data-hook '{hook}' has no JS handler"
                        )

                # 3 — JS hooks vs HTML data-hooks
                for hook in declared_hooks:
                    if hook not in html_hooks:
                        obstructions.append(
                            f"[{view_id}/{section.component_id}] "
                            f"JS hook '{hook}' has no matching data-hook "
                            f"in HTML"
                        )

        return obstructions

    # ── factory ───────────────────────────────────────────────────────

    @classmethod
    def from_view_site(
        cls,
        view_site: Any,
        concepts: Any | None = None,
    ) -> "ComponentPresheaf":
        """Build a ComponentPresheaf from a ViewSite and optional concepts.

        Steps:
          1. Read all views from the view site.
          2. Use :class:`ComponentCatalog` to determine which components
             belong on each view kind.
          3. Create obligations and placeholder sections.

        Parameters
        ----------
        view_site
            A ``ViewSite`` instance providing views and morphisms.
        concepts
            An optional ``ConceptMap`` for refining component selection.
        """
        presheaf = cls()
        catalog = ComponentCatalog()

        # Gather view info.
        views = view_site.views() if hasattr(view_site, "views") else []
        view_ids = [v.id for v in views]
        presheaf.register_views(view_ids)

        # Determine obligations from catalog.
        assigned: dict[str, ComponentObligation] = {}

        for view in views:
            kind_str = view.kind.value if hasattr(view.kind, "value") else str(view.kind)
            view_obligations = catalog.for_view_kind(kind_str)
            for obl in view_obligations:
                if obl.component_kind not in assigned:
                    assigned[obl.component_kind] = ComponentObligation(
                        component_kind=obl.component_kind,
                        html_obligation=obl.html_obligation,
                        css_obligation=obl.css_obligation,
                        js_obligation=obl.js_obligation,
                        views=[],
                        interactive=obl.interactive,
                        html_skeleton=obl.html_skeleton,
                        css_classes=list(obl.css_classes),
                        js_hooks=list(obl.js_hooks),
                    )
                if obl.is_global:
                    assigned[obl.component_kind].views = []
                elif view.id not in assigned[obl.component_kind].views:
                    assigned[obl.component_kind].views.append(view.id)

        # Register all obligations.
        for obl in assigned.values():
            presheaf.add_component(obl)

        # Create placeholder sections.
        for view in views:
            kind_str = view.kind.value if hasattr(view.kind, "value") else str(view.kind)
            view_obligations = catalog.for_view_kind(kind_str)
            for obl in view_obligations:
                presheaf.add_section(ComponentSection(
                    component_id=obl.component_kind,
                    view_id=view.id,
                    html_fragment=obl.html_skeleton,
                    css_selectors=list(obl.css_classes),
                    js_hooks=list(obl.js_hooks),
                ))

        return presheaf

    # ── dunder helpers ────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._obligations)

    def __repr__(self) -> str:
        total_sections = sum(len(s) for s in self._sections.values())
        return (
            f"ComponentPresheaf(obligations={len(self._obligations)}, "
            f"views={len(self._all_views)}, sections={total_sections})"
        )


# ═══════════════════════════════════════════════════════════════════════
# §5  ComponentCatalog — catalog of standard web components
# ═══════════════════════════════════════════════════════════════════════

class ComponentCatalog:
    """Catalog of standard web components with their obligations.

    This class serves as the *classifying object* of the component
    presheaf: it knows which components exist, what HTML/CSS/JS each
    requires, and which view kinds typically include each component.
    """

    # ── public API ────────────────────────────────────────────────────

    @staticmethod
    def standard_components() -> list[ComponentObligation]:
        """Return all standard web components with their obligations.

        Each component carries obligations in all three fibres
        (HTML, CSS, JS) describing what the generated code must contain.
        """
        return list(_STANDARD_COMPONENTS)

    @staticmethod
    def for_view_kind(kind: str) -> list[ComponentObligation]:
        """Return components that typically appear on a view of *kind*.

        The mapping covers all :class:`ViewKind` values and falls back
        to a minimal set (navbar + main-content + footer) for unknown
        kinds.
        """
        # Global components appear on every view.
        global_kinds = {"navbar", "footer", "toast", "modal", "loading-screen"}
        globals_ = [c for c in _STANDARD_COMPONENTS if c.component_kind in global_kinds]

        # View-kind-specific components.
        specific_kinds = _VIEW_KIND_TO_COMPONENTS.get(kind, ["empty-state"])
        specifics = [
            c for c in _STANDARD_COMPONENTS
            if c.component_kind in specific_kinds
        ]

        # Deduplicate (globals first, then specifics).
        seen: set[str] = set()
        result: list[ComponentObligation] = []
        for c in globals_ + specifics:
            if c.component_kind not in seen:
                seen.add(c.component_kind)
                result.append(c)
        return result

    @staticmethod
    def get_component(kind: str) -> ComponentObligation | None:
        """Look up a single component obligation by kind name."""
        for c in _STANDARD_COMPONENTS:
            if c.component_kind == kind:
                return c
        return None


# ── Standard component definitions ────────────────────────────────────

_STANDARD_COMPONENTS: list[ComponentObligation] = [
    # ── navbar ────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="navbar",
        html_obligation="nav.navbar > .nav-container > .nav-brand + .nav-links",
        css_obligation="Fixed top, backdrop blur, z-index layering, responsive collapse",
        js_obligation="Mobile toggle (hamburger), active link highlighting, scroll shadow",
        interactive=True,
        html_skeleton=(
            '<nav class="navbar">'
            '<div class="nav-container">'
            '<a class="nav-brand" data-hook="brand-link"></a>'
            '<button class="nav-toggle" data-hook="nav-toggle">&#9776;</button>'
            '<ul class="nav-links" data-hook="nav-links"></ul>'
            '</div></nav>'
        ),
        css_classes=["navbar", "nav-container", "nav-brand", "nav-links", "nav-toggle"],
        js_hooks=["nav-toggle", "nav-links", "brand-link"],
    ),
    # ── hero ──────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="hero",
        html_obligation="section.hero > .hero-inner > h1.hero-title + p.hero-subtitle + .hero-cta",
        css_obligation="Full-width, gradient or background image, centered text, responsive sizing",
        js_obligation="Optional parallax scroll effect, CTA click tracking",
        interactive=False,
        html_skeleton=(
            '<section class="hero">'
            '<div class="hero-inner">'
            '<h1 class="hero-title"></h1>'
            '<p class="hero-subtitle"></p>'
            '<div class="hero-cta" data-hook="hero-cta"></div>'
            '</div></section>'
        ),
        css_classes=["hero", "hero-inner", "hero-title", "hero-subtitle", "hero-cta"],
        js_hooks=["hero-cta"],
    ),
    # ── card ──────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="card",
        html_obligation="div.card > .card-header + .card-body + .card-footer",
        css_obligation="Rounded corners, box shadow, hover lift, responsive grid",
        js_obligation="Optional click handler, expandable content",
        interactive=False,
        html_skeleton=(
            '<div class="card">'
            '<div class="card-header"></div>'
            '<div class="card-body"></div>'
            '<div class="card-footer"></div>'
            '</div>'
        ),
        css_classes=["card", "card-header", "card-body", "card-footer"],
        js_hooks=[],
    ),
    # ── button ────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="button",
        html_obligation="button.btn with variant classes (.btn-primary, .btn-outline, .btn-ghost)",
        css_obligation="Variants (primary, outline, ghost), sizes (sm, md, lg), focus ring, disabled state",
        js_obligation="Click delegation, loading state spinner",
        interactive=True,
        html_skeleton='<button class="btn btn-primary" data-hook="btn-action"></button>',
        css_classes=["btn", "btn-primary", "btn-outline", "btn-ghost", "btn-sm", "btn-md", "btn-lg"],
        js_hooks=["btn-action"],
    ),
    # ── modal ─────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="modal",
        html_obligation="div.modal > .modal-backdrop + .modal-content > .modal-header + .modal-body + .modal-footer",
        css_obligation="Overlay, centered, animate open/close, z-index above navbar",
        js_obligation="Open/close triggers, Escape key dismiss, focus trap, click-outside close",
        interactive=True,
        html_skeleton=(
            '<div class="modal" data-hook="modal">'
            '<div class="modal-backdrop" data-hook="modal-backdrop"></div>'
            '<div class="modal-content">'
            '<div class="modal-header"><button class="modal-close" data-hook="modal-close">&times;</button></div>'
            '<div class="modal-body"></div>'
            '<div class="modal-footer"></div>'
            '</div></div>'
        ),
        css_classes=["modal", "modal-backdrop", "modal-content", "modal-header", "modal-body", "modal-footer", "modal-close"],
        js_hooks=["modal", "modal-backdrop", "modal-close"],
    ),
    # ── toast ─────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="toast",
        html_obligation="div.toast-container > .toast with .toast-message + .toast-dismiss",
        css_obligation="Fixed bottom-right, slide-in animation, severity colors (success, error, warning, info)",
        js_obligation="Auto-dismiss timer, queue management, manual dismiss",
        interactive=True,
        html_skeleton=(
            '<div class="toast-container" data-hook="toast-container">'
            '<div class="toast">'
            '<span class="toast-message"></span>'
            '<button class="toast-dismiss" data-hook="toast-dismiss">&times;</button>'
            '</div></div>'
        ),
        css_classes=["toast-container", "toast", "toast-message", "toast-dismiss"],
        js_hooks=["toast-container", "toast-dismiss"],
    ),
    # ── tabs ──────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="tabs",
        html_obligation="div.tabs-container > .tab-list > .tab-btn* + .tab-panel*",
        css_obligation="Active indicator underline/highlight, smooth transition",
        js_obligation="Tab switching with ARIA roles, keyboard navigation (arrow keys)",
        interactive=True,
        html_skeleton=(
            '<div class="tabs-container" data-hook="tabs">'
            '<div class="tab-list" role="tablist">'
            '<button class="tab-btn" role="tab" data-hook="tab-btn"></button>'
            '</div>'
            '<div class="tab-panel" role="tabpanel" data-hook="tab-panel"></div>'
            '</div>'
        ),
        css_classes=["tabs-container", "tab-list", "tab-btn", "tab-panel"],
        js_hooks=["tabs", "tab-btn", "tab-panel"],
    ),
    # ── accordion ─────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="accordion",
        html_obligation="div.accordion > .accordion-item > .accordion-trigger + .accordion-content",
        css_obligation="Expand/collapse animation, border between items, icon rotation",
        js_obligation="Toggle open/close, optional exclusive mode (one open at a time)",
        interactive=True,
        html_skeleton=(
            '<div class="accordion" data-hook="accordion">'
            '<div class="accordion-item">'
            '<button class="accordion-trigger" data-hook="accordion-trigger"></button>'
            '<div class="accordion-content" data-hook="accordion-content"></div>'
            '</div></div>'
        ),
        css_classes=["accordion", "accordion-item", "accordion-trigger", "accordion-content"],
        js_hooks=["accordion", "accordion-trigger", "accordion-content"],
    ),
    # ── form ──────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="form",
        html_obligation="form > .form-group > label + input/select/textarea, .form-actions > submit button",
        css_obligation="Styled inputs with focus ring, error state borders, label positioning",
        js_obligation="Client-side validation, submit handler with loading state, error display",
        interactive=True,
        html_skeleton=(
            '<form class="form" data-hook="form">'
            '<div class="form-group">'
            '<label class="form-label"></label>'
            '<input class="form-input" />'
            '<span class="form-error"></span>'
            '</div>'
            '<div class="form-actions">'
            '<button class="btn btn-primary" type="submit" data-hook="form-submit">Submit</button>'
            '</div></form>'
        ),
        css_classes=["form", "form-group", "form-label", "form-input", "form-error", "form-actions"],
        js_hooks=["form", "form-submit"],
    ),
    # ── table ─────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="table",
        html_obligation="div.table-wrapper > table > thead + tbody, optional tfoot",
        css_obligation="Striped rows, hover highlight, responsive horizontal scroll, sticky header",
        js_obligation="Optional column sorting, row selection",
        interactive=False,
        html_skeleton=(
            '<div class="table-wrapper">'
            '<table class="table">'
            '<thead><tr></tr></thead>'
            '<tbody></tbody>'
            '</table></div>'
        ),
        css_classes=["table-wrapper", "table"],
        js_hooks=[],
    ),
    # ── sidebar ───────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="sidebar",
        html_obligation="aside.sidebar > .sidebar-header + .sidebar-nav + .sidebar-footer",
        css_obligation="Fixed or toggleable, width transition, overlay on mobile",
        js_obligation="Toggle open/close, active link tracking, responsive collapse",
        interactive=True,
        html_skeleton=(
            '<aside class="sidebar" data-hook="sidebar">'
            '<div class="sidebar-header"></div>'
            '<nav class="sidebar-nav" data-hook="sidebar-nav"></nav>'
            '<div class="sidebar-footer"></div>'
            '</aside>'
        ),
        css_classes=["sidebar", "sidebar-header", "sidebar-nav", "sidebar-footer"],
        js_hooks=["sidebar", "sidebar-nav"],
    ),
    # ── loading-screen ────────────────────────────────────────────────
    ComponentObligation(
        component_kind="loading-screen",
        html_obligation="div#loading-screen > .loading-content > progress + .loading-steps",
        css_obligation="Full-screen overlay, centered content, fade-out transition",
        js_obligation="Progress bar driver, step display, dismiss on load complete",
        interactive=True,
        html_skeleton=(
            '<div id="loading-screen" class="loading-screen" data-hook="loading-screen">'
            '<div class="loading-content">'
            '<progress class="loading-progress" data-hook="loading-progress"></progress>'
            '<div class="loading-steps" data-hook="loading-steps"></div>'
            '</div></div>'
        ),
        css_classes=["loading-screen", "loading-content", "loading-progress", "loading-steps"],
        js_hooks=["loading-screen", "loading-progress", "loading-steps"],
    ),
    # ── footer ────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="footer",
        html_obligation="footer.footer > .footer-content with links, copyright",
        css_obligation="Bottom margin auto, muted text, border-top separator",
        js_obligation="None typically (static content)",
        interactive=False,
        html_skeleton=(
            '<footer class="footer">'
            '<div class="footer-content">'
            '<span class="footer-copyright"></span>'
            '<nav class="footer-links"></nav>'
            '</div></footer>'
        ),
        css_classes=["footer", "footer-content", "footer-copyright", "footer-links"],
        js_hooks=[],
    ),
    # ── empty-state ───────────────────────────────────────────────────
    ComponentObligation(
        component_kind="empty-state",
        html_obligation="div.empty-state > .empty-icon + .empty-message + .empty-action",
        css_obligation="Centered content, muted colors, generous padding",
        js_obligation="Optional action button handler",
        interactive=False,
        html_skeleton=(
            '<div class="empty-state">'
            '<div class="empty-icon"></div>'
            '<p class="empty-message"></p>'
            '<button class="empty-action btn"></button>'
            '</div>'
        ),
        css_classes=["empty-state", "empty-icon", "empty-message", "empty-action"],
        js_hooks=[],
    ),
    # ── error-state ───────────────────────────────────────────────────
    ComponentObligation(
        component_kind="error-state",
        html_obligation="div.error-state > .error-icon + .error-message + .error-retry",
        css_obligation="Centered content, danger/red accent, icon emphasis",
        js_obligation="Retry button click handler",
        interactive=True,
        html_skeleton=(
            '<div class="error-state">'
            '<div class="error-icon"></div>'
            '<p class="error-message"></p>'
            '<button class="error-retry btn" data-hook="error-retry">Retry</button>'
            '</div>'
        ),
        css_classes=["error-state", "error-icon", "error-message", "error-retry"],
        js_hooks=["error-retry"],
    ),
    # ── breadcrumb ────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="breadcrumb",
        html_obligation="nav.breadcrumb > ol > li > a, with aria-label='Breadcrumb'",
        css_obligation="Inline list, separator characters, muted last item",
        js_obligation="None (static links)",
        interactive=False,
        html_skeleton=(
            '<nav class="breadcrumb" aria-label="Breadcrumb">'
            '<ol class="breadcrumb-list"><li class="breadcrumb-item">'
            '<a class="breadcrumb-link"></a></li></ol></nav>'
        ),
        css_classes=["breadcrumb", "breadcrumb-list", "breadcrumb-item", "breadcrumb-link"],
        js_hooks=[],
    ),
    # ── pagination ────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="pagination",
        html_obligation="nav.pagination > .page-btn* with prev/next and page numbers",
        css_obligation="Inline button group, active page highlight, disabled state",
        js_obligation="Page click handler, boundary checks",
        interactive=True,
        html_skeleton=(
            '<nav class="pagination" data-hook="pagination">'
            '<button class="page-btn page-prev" data-hook="page-prev">&laquo;</button>'
            '<button class="page-btn page-num"></button>'
            '<button class="page-btn page-next" data-hook="page-next">&raquo;</button>'
            '</nav>'
        ),
        css_classes=["pagination", "page-btn", "page-prev", "page-next", "page-num"],
        js_hooks=["pagination", "page-prev", "page-next"],
    ),
    # ── badge ─────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="badge",
        html_obligation="span.badge with variant classes (.badge-primary, .badge-success, etc.)",
        css_obligation="Pill shape, color variants, small font",
        js_obligation="None (decorative)",
        interactive=False,
        html_skeleton='<span class="badge badge-primary"></span>',
        css_classes=["badge", "badge-primary", "badge-success", "badge-warning", "badge-danger"],
        js_hooks=[],
    ),
    # ── progress ──────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="progress",
        html_obligation="div.progress-bar > .progress-fill with aria-valuenow",
        css_obligation="Track and fill colors, width transition, optional stripes",
        js_obligation="Value update, optional animation",
        interactive=False,
        html_skeleton=(
            '<div class="progress-bar" role="progressbar">'
            '<div class="progress-fill" data-hook="progress-fill"></div>'
            '</div>'
        ),
        css_classes=["progress-bar", "progress-fill"],
        js_hooks=["progress-fill"],
    ),
    # ── tooltip ───────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="tooltip",
        html_obligation="[data-tooltip] attribute on trigger + .tooltip element",
        css_obligation="Absolute positioned, arrow, fade-in, dark background",
        js_obligation="Show on hover/focus, position calculation, hide on blur",
        interactive=True,
        html_skeleton='<span class="tooltip" data-hook="tooltip" role="tooltip"></span>',
        css_classes=["tooltip"],
        js_hooks=["tooltip"],
    ),
    # ── dropdown ──────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="dropdown",
        html_obligation="div.dropdown > .dropdown-trigger + .dropdown-menu > .dropdown-item*",
        css_obligation="Absolute positioned menu, hover/focus reveal, shadow, z-index",
        js_obligation="Toggle on click, close on outside click, keyboard navigation",
        interactive=True,
        html_skeleton=(
            '<div class="dropdown" data-hook="dropdown">'
            '<button class="dropdown-trigger" data-hook="dropdown-trigger"></button>'
            '<div class="dropdown-menu">'
            '<a class="dropdown-item"></a>'
            '</div></div>'
        ),
        css_classes=["dropdown", "dropdown-trigger", "dropdown-menu", "dropdown-item"],
        js_hooks=["dropdown", "dropdown-trigger"],
    ),
    # ── skeleton ──────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="skeleton",
        html_obligation="div.skeleton with .skeleton-line and .skeleton-circle variants",
        css_obligation="Pulse animation, grey placeholder colors, matching target dimensions",
        js_obligation="None (pure CSS animation)",
        interactive=False,
        html_skeleton=(
            '<div class="skeleton">'
            '<div class="skeleton-line"></div>'
            '<div class="skeleton-circle"></div>'
            '</div>'
        ),
        css_classes=["skeleton", "skeleton-line", "skeleton-circle"],
        js_hooks=[],
    ),
    # ── avatar ────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="avatar",
        html_obligation="div.avatar > img or .avatar-initials fallback",
        css_obligation="Circle clip, sizes (sm, md, lg), border, status indicator",
        js_obligation="Fallback to initials on image error",
        interactive=False,
        html_skeleton=(
            '<div class="avatar">'
            '<img class="avatar-img" />'
            '<span class="avatar-initials"></span>'
            '</div>'
        ),
        css_classes=["avatar", "avatar-img", "avatar-initials"],
        js_hooks=[],
    ),
    # ── chip ──────────────────────────────────────────────────────────
    ComponentObligation(
        component_kind="chip",
        html_obligation="span.chip > .chip-label + optional .chip-remove",
        css_obligation="Pill shape, closeable variant, color variants",
        js_obligation="Optional remove handler",
        interactive=False,
        html_skeleton=(
            '<span class="chip">'
            '<span class="chip-label"></span>'
            '<button class="chip-remove" data-hook="chip-remove">&times;</button>'
            '</span>'
        ),
        css_classes=["chip", "chip-label", "chip-remove"],
        js_hooks=["chip-remove"],
    ),
]


# ── View-kind → component mapping ─────────────────────────────────────

_VIEW_KIND_TO_COMPONENTS: dict[str, list[str]] = {
    "landing": ["hero", "card", "button"],
    "dashboard": ["card", "sidebar", "table", "progress", "badge"],
    "list": ["table", "pagination", "badge", "button", "empty-state"],
    "detail": ["breadcrumb", "card", "button", "badge", "tabs"],
    "form": ["form", "button"],
    "settings": ["form", "tabs", "button", "sidebar"],
    "profile": ["avatar", "card", "button", "badge", "tabs"],
    "search": ["form", "card", "pagination", "empty-state", "skeleton"],
    "gallery": ["card", "pagination", "skeleton"],
    "editor": ["sidebar", "tabs", "button", "dropdown"],
    "canvas": ["sidebar", "button", "tooltip", "dropdown"],
    "tutorial": ["progress", "card", "button", "accordion"],
    "about": ["hero", "card"],
    "error": ["error-state"],
    "auth_login": ["form", "button"],
    "auth_register": ["form", "button"],
    "auth_reset": ["form", "button"],
    "admin": ["sidebar", "table", "pagination", "badge", "dropdown", "button"],
    "help": ["accordion", "card", "breadcrumb"],
    "changelog": ["card", "badge", "pagination"],
    "pricing": ["card", "button", "badge", "tabs"],
    "checkout": ["form", "card", "button", "progress"],
    "cart": ["card", "button", "badge", "table"],
    "notification": ["card", "badge", "tabs", "empty-state"],
    "empty_state": ["empty-state", "button"],
    "custom": ["card", "button"],
}
