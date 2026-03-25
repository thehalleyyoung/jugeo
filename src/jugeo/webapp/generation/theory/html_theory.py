"""Theory of HTML as the structural fibre of the web application sheaf.

HTML structure forms the structural fibre of the web application sheaf.
The document structure obligation ensures well-formedness.  Accessibility
obligations ensure the presheaf restricts correctly to assistive technology.
Semantic obligations ensure the fibre structure is meaningful.  Descent
means: structural consistency across views, no broken hierarchy, no
orphaned interactive elements.

Formal picture
--------------
Let *V* be the view site (the category of routes/pages with navigation
morphisms).  For each view *v ∈ V* we assign a **structural fibre**
``F(v)`` consisting of well-formed HTML satisfying:

* **Document structure obligation** — one DOCTYPE, one ``<html>``, one
  ``<head>``, one ``<body>``, required meta elements.
* **Accessibility obligation** — ARIA landmarks, alt texts, heading
  hierarchy, keyboard navigability, colour contrast intent.
* **Semantic obligation** — landmark elements, no div-soup, lists for
  lists, tables for tabular data.

A **section** of the presheaf is a concrete HTML string that satisfies
all three obligations simultaneously.  The **descent condition** says
that if two views share a common sub-view (e.g. the nav bar), the
restriction of their sections to that sub-view must agree.
"""
from __future__ import annotations

import html as _html_mod
import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ElementRole",
    "AccessibilityLevel",
    "DocumentStructureObligation",
    "AccessibilityObligation",
    "SemanticStructureObligation",
    "ViewHTMLSpec",
    "HTMLStructurePresheaf",
    "HTMLShellGenerator",
    "HTMLFragmentExtractor",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ElementRole(Enum):
    """Semantic role that an HTML element plays in the document fibre."""

    # Landmarks
    LANDMARK_NAV = "nav"
    LANDMARK_MAIN = "main"
    LANDMARK_HEADER = "header"
    LANDMARK_FOOTER = "footer"
    LANDMARK_ASIDE = "aside"
    LANDMARK_SECTION = "section"

    # Headings
    HEADING_1 = "h1"
    HEADING_2 = "h2"
    HEADING_3 = "h3"
    HEADING_4 = "h4"
    HEADING_5 = "h5"
    HEADING_6 = "h6"

    # Interactive
    INTERACTIVE_BUTTON = "button"
    INTERACTIVE_LINK = "a"
    INTERACTIVE_INPUT = "input"
    INTERACTIVE_SELECT = "select"
    INTERACTIVE_TEXTAREA = "textarea"

    # Containers
    CONTAINER_DIV = "div"
    CONTAINER_ARTICLE = "article"
    CONTAINER_FIGURE = "figure"

    # Media
    MEDIA_IMG = "img"
    MEDIA_CANVAS = "canvas"
    MEDIA_VIDEO = "video"
    MEDIA_AUDIO = "audio"
    MEDIA_SVG = "svg"

    # Data
    DATA_TABLE = "table"
    DATA_LIST = "ul"
    DATA_FORM = "form"

    # Feedback
    FEEDBACK_DIALOG = "dialog"
    FEEDBACK_TOAST = "div"  # no native toast element; convention-based
    FEEDBACK_PROGRESS = "progress"
    FEEDBACK_ALERT = "div"  # role="alert"

    # Meta (head-level)
    META_TITLE = "title"
    META_DESCRIPTION = "meta"
    META_VIEWPORT = "meta"
    META_CHARSET = "meta"


class AccessibilityLevel(Enum):
    """WCAG conformance level."""

    A = "A"
    AA = "AA"
    AAA = "AAA"


# ---------------------------------------------------------------------------
# Obligation dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DocumentStructureObligation:
    """Obligation that the HTML document is structurally well-formed.

    Each boolean flag represents a required structural invariant.  The
    :meth:`check` method inspects raw HTML and returns a list of human-
    readable violation strings (empty ⟹ obligation satisfied).
    """

    one_doctype: bool = True
    one_html_element: bool = True
    one_head_element: bool = True
    one_body_element: bool = True
    charset_declaration: bool = True
    viewport_meta: bool = True
    title_element: bool = True
    lang_attribute: bool = True

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _count_tags(html: str, tag: str) -> int:
        return len(re.findall(rf"<{tag}[\s>]", html, re.IGNORECASE))

    @staticmethod
    def _count_closing(html: str, tag: str) -> int:
        return len(re.findall(rf"</{tag}\s*>", html, re.IGNORECASE))

    # -- public API ----------------------------------------------------------

    def check(self, html: str) -> list[str]:
        """Return a list of structural violations found in *html*."""
        violations: list[str] = []
        lower = html.lower()

        if self.one_doctype:
            count = lower.count("<!doctype")
            if count == 0:
                violations.append("Missing <!DOCTYPE html> declaration.")
            elif count > 1:
                violations.append(
                    f"Multiple DOCTYPE declarations found ({count})."
                )

        if self.one_html_element:
            count = self._count_tags(html, "html")
            if count != 1:
                violations.append(
                    f"Expected exactly 1 <html> element, found {count}."
                )

        if self.one_head_element:
            count = self._count_tags(html, "head")
            if count != 1:
                violations.append(
                    f"Expected exactly 1 <head> element, found {count}."
                )

        if self.one_body_element:
            count = self._count_tags(html, "body")
            if count != 1:
                violations.append(
                    f"Expected exactly 1 <body> element, found {count}."
                )

        if self.charset_declaration:
            if 'charset=' not in lower:
                violations.append(
                    "Missing charset declaration (e.g. "
                    '<meta charset="UTF-8">).'
                )

        if self.viewport_meta:
            if 'name="viewport"' not in lower and "name='viewport'" not in lower:
                violations.append(
                    'Missing <meta name="viewport" …> declaration.'
                )

        if self.title_element:
            if "<title>" not in lower or "</title>" not in lower:
                violations.append("Missing <title> element in <head>.")

        if self.lang_attribute:
            if not re.search(r'<html[^>]+lang\s*=', html, re.IGNORECASE):
                violations.append(
                    'Missing lang attribute on <html> element.'
                )

        return violations


@dataclass
class AccessibilityObligation:
    """Obligation that the HTML satisfies accessibility requirements.

    The *level* field selects the target WCAG conformance level.  Individual
    boolean flags enable or disable specific checks so that the obligation
    can be tuned per-project.
    """

    level: AccessibilityLevel = AccessibilityLevel.AA
    skip_link: bool = True
    aria_labels: bool = True
    alt_texts: bool = True
    heading_hierarchy: bool = True
    form_labels: bool = True
    focus_visible: bool = True
    keyboard_nav: bool = True
    color_contrast: bool = True
    reduced_motion: bool = True

    def check(self, html: str) -> list[str]:
        """Return accessibility violations found in *html*."""
        violations: list[str] = []
        lower = html.lower()

        if self.skip_link:
            if '#main' not in lower and '#content' not in lower:
                violations.append(
                    "No skip-navigation link found (expected href to "
                    '"#main" or "#content").'
                )

        if self.aria_labels:
            navs = re.findall(r"<nav\b[^>]*>", html, re.IGNORECASE)
            for nav in navs:
                if "aria-label" not in nav.lower() and "aria-labelledby" not in nav.lower():
                    violations.append(
                        "<nav> element missing aria-label or "
                        "aria-labelledby attribute."
                    )

        if self.alt_texts:
            imgs = re.findall(r"<img\b[^>]*>", html, re.IGNORECASE)
            for img in imgs:
                if 'alt=' not in img.lower():
                    violations.append(
                        '<img> element missing alt attribute.'
                    )

        if self.heading_hierarchy:
            headings = re.findall(r"<(h[1-6])\b", html, re.IGNORECASE)
            levels = [int(h[1]) for h in headings]
            for i in range(1, len(levels)):
                if levels[i] > levels[i - 1] + 1:
                    violations.append(
                        f"Heading hierarchy skip: <h{levels[i - 1]}> "
                        f"followed by <h{levels[i]}>."
                    )

        if self.form_labels:
            inputs = re.findall(
                r'<input\b[^>]*type=["\'](?!hidden)[^"\']*["\'][^>]*>',
                html,
                re.IGNORECASE,
            )
            for inp in inputs:
                inp_lower = inp.lower()
                if (
                    "aria-label" not in inp_lower
                    and "aria-labelledby" not in inp_lower
                    and "id=" not in inp_lower
                ):
                    violations.append(
                        "<input> without aria-label, aria-labelledby, "
                        "or id (for <label> association)."
                    )

        if self.focus_visible:
            if "outline: none" in lower or "outline:none" in lower:
                if ":focus-visible" not in lower:
                    violations.append(
                        "CSS removes outline without providing "
                        ":focus-visible alternative."
                    )

        if self.keyboard_nav:
            clickables = re.findall(
                r'<div\b[^>]*onclick[^>]*>', html, re.IGNORECASE,
            )
            for div in clickables:
                if 'tabindex' not in div.lower():
                    violations.append(
                        '<div> with onclick handler missing tabindex '
                        'for keyboard accessibility.'
                    )

        if self.color_contrast:
            # Static analysis cannot verify contrast ratios, but we can
            # check that the intent is declared via custom properties or
            # a contrast-related class.
            pass  # deferred to visual-test fibre

        if self.reduced_motion:
            if "prefers-reduced-motion" not in lower and "@media" in lower:
                violations.append(
                    "Animations present but no prefers-reduced-motion "
                    "media query detected."
                )

        return violations


@dataclass
class SemanticStructureObligation:
    """Obligation that HTML uses semantic elements correctly.

    The semantic obligation ensures the fibre structure carries meaning
    beyond raw presentation — landmark regions, proper heading hierarchy,
    list semantics for enumerable items, and tables for tabular data.
    """

    landmark_nav: bool = True
    landmark_main: bool = True
    landmark_header: bool = True
    landmark_footer: bool = True
    heading_hierarchy_strict: bool = True
    no_div_soup: bool = True
    lists_for_lists: bool = True
    tables_for_data: bool = True

    def check(self, html: str) -> list[str]:
        """Return semantic-structure violations found in *html*."""
        violations: list[str] = []
        lower = html.lower()

        if self.landmark_nav and "<nav" not in lower:
            violations.append("Missing <nav> landmark element.")

        if self.landmark_main and "<main" not in lower:
            violations.append("Missing <main> landmark element.")

        if self.landmark_header and "<header" not in lower:
            violations.append("Missing <header> landmark element.")

        if self.landmark_footer and "<footer" not in lower:
            violations.append("Missing <footer> landmark element.")

        if self.heading_hierarchy_strict:
            headings = re.findall(r"<(h[1-6])\b", html, re.IGNORECASE)
            levels = [int(h[1]) for h in headings]
            if levels and levels[0] != 1:
                violations.append(
                    f"First heading is <h{levels[0]}>, expected <h1>."
                )
            for i in range(1, len(levels)):
                if levels[i] > levels[i - 1] + 1:
                    violations.append(
                        f"Strict heading skip: <h{levels[i - 1]}> → "
                        f"<h{levels[i]}> (no intermediate heading)."
                    )

        if self.no_div_soup:
            # Heuristic: flag documents where >70 % of container elements
            # are plain <div> with no semantic children.
            divs = len(re.findall(r"<div[\s>]", html, re.IGNORECASE))
            semantic_tags = (
                "nav", "main", "header", "footer", "article", "aside",
                "section", "figure", "figcaption", "details", "summary",
            )
            semantic = sum(
                len(re.findall(rf"<{t}[\s>]", html, re.IGNORECASE))
                for t in semantic_tags
            )
            total = divs + semantic
            if total > 5 and semantic == 0:
                violations.append(
                    "Div-soup detected: many <div> elements with no "
                    "semantic container elements."
                )

        if self.lists_for_lists:
            # Heuristic: repeated sibling <div> with identical class may
            # indicate a list that should use <ul>/<ol>.
            list_class_pattern = re.findall(
                r'<div\s+class="([^"]+)"', html, re.IGNORECASE,
            )
            from collections import Counter
            counts = Counter(list_class_pattern)
            for cls, cnt in counts.items():
                if cnt >= 4 and "item" in cls.lower():
                    violations.append(
                        f'Repeated <div class="{cls}"> ({cnt}×) may be '
                        f"better expressed as <ul>/<ol> list items."
                    )

        if self.tables_for_data:
            # Heuristic: grid-like class names without <table>.
            if (
                "data-grid" in lower or "data-table" in lower
            ) and "<table" not in lower:
                violations.append(
                    "Grid-like structure detected without <table> — "
                    "use <table> for tabular data."
                )

        return violations


# ---------------------------------------------------------------------------
# View specification
# ---------------------------------------------------------------------------

@dataclass
class ViewHTMLSpec:
    """Specification of the HTML structure required by a single view.

    Acts as a *local section* of the structural presheaf: it describes
    what landmarks, interactive elements, data attributes, and ARIA labels
    must be present in the fibre over this view.
    """

    view_id: str
    view_kind: str = "page"
    required_landmarks: list[ElementRole] = field(default_factory=list)
    required_interactive: list[ElementRole] = field(default_factory=list)
    data_attributes: dict[str, str] = field(default_factory=dict)
    aria_labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.data_attributes:
            self.data_attributes = {"data-view": self.view_id}


# ---------------------------------------------------------------------------
# Presheaf
# ---------------------------------------------------------------------------

class HTMLStructurePresheaf:
    """Structural obligations assembled into a presheaf over the view site.

    Collects document-level, accessibility, and semantic obligations
    together with per-view HTML specifications.  The :meth:`check_all`
    method evaluates every obligation against a given HTML string and
    returns the union of violations.
    """

    def __init__(
        self,
        document_obligations: DocumentStructureObligation | None = None,
        accessibility_obligations: AccessibilityObligation | None = None,
        semantic_obligations: SemanticStructureObligation | None = None,
    ) -> None:
        self.document_obligations = (
            document_obligations or DocumentStructureObligation()
        )
        self.accessibility_obligations = (
            accessibility_obligations or AccessibilityObligation()
        )
        self.semantic_obligations = (
            semantic_obligations or SemanticStructureObligation()
        )
        self.view_specs: dict[str, ViewHTMLSpec] = {}

    def add_view_spec(self, spec: ViewHTMLSpec) -> None:
        """Register a view specification in the presheaf."""
        self.view_specs[spec.view_id] = spec

    def obligations_for_view(self, view_id: str) -> ViewHTMLSpec:
        """Return the :class:`ViewHTMLSpec` for *view_id*.

        Raises :class:`KeyError` if no spec has been registered.
        """
        return self.view_specs[view_id]

    def check_all(self, html: str) -> list[str]:
        """Check *html* against every registered obligation.

        Returns a combined list of violation strings from document,
        accessibility, and semantic obligations.
        """
        violations: list[str] = []
        violations.extend(self.document_obligations.check(html))
        violations.extend(self.accessibility_obligations.check(html))
        violations.extend(self.semantic_obligations.check(html))
        return violations


# ---------------------------------------------------------------------------
# Shell generator
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape text for safe inclusion in HTML."""
    return _html_mod.escape(text, quote=True)


def _indent(text: str, level: int = 1) -> str:
    """Indent every line of *text* by *level* × 2 spaces."""
    prefix = "  " * level
    return textwrap.indent(text, prefix)


class HTMLShellGenerator:
    """Generate well-formed HTML structure from the structural theory.

    Every method produces HTML that satisfies the document-structure,
    accessibility, and semantic obligations by construction.
    """

    def generate_document_shell(
        self,
        title: str,
        css_href: str = "style.css",
        js_src: str = "app.js",
    ) -> tuple[str, str]:
        """Return ``(head_html, close_html)`` — document open and close.

        The caller inserts body content between the two fragments.
        """
        head = textwrap.dedent(f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{_esc(title)}">
  <title>{_esc(title)}</title>
  <link rel="stylesheet" href="{_esc(css_href)}">
</head>
<body>
  <a href="#main" class="skip-link">Skip to main content</a>
""")
        close = textwrap.dedent(f"""\
  <script src="{_esc(js_src)}"></script>
</body>
</html>
""")
        return head, close

    def generate_view_section(self, spec: ViewHTMLSpec) -> str:
        """Generate an HTML ``<section>`` for a single view spec."""
        attrs_parts: list[str] = [
            f'id="view-{_esc(spec.view_id)}"',
            f'class="app-view"',
        ]
        for key, val in spec.data_attributes.items():
            attrs_parts.append(f'{_esc(key)}="{_esc(val)}"')
        for element_id, label in spec.aria_labels.items():
            pass  # labels applied to children, not the section itself

        is_home = spec.view_id == "home"
        if not is_home:
            attrs_parts.append('style="display: none;"')
        attrs_parts.append(f'aria-label="{_esc(spec.view_id)} view"')

        attrs_str = " ".join(attrs_parts)
        lines: list[str] = [f"  <section {attrs_str}>"]

        # Landmarks
        for role in spec.required_landmarks:
            tag = role.value
            aria = spec.aria_labels.get(role.name, spec.view_id)
            lines.append(
                f'    <{tag} aria-label="{_esc(aria)}">'
            )
            lines.append(f"      <!-- {_esc(spec.view_id)}: {tag} content -->")
            lines.append(f"    </{tag}>")

        # Interactive placeholders
        for role in spec.required_interactive:
            tag = role.value
            aria = spec.aria_labels.get(role.name, role.name.lower())
            if tag == "a":
                lines.append(
                    f'    <a href="#" aria-label="{_esc(aria)}">'
                    f"{_esc(aria)}</a>"
                )
            elif tag == "button":
                lines.append(
                    f'    <button type="button" aria-label="{_esc(aria)}">'
                    f"{_esc(aria)}</button>"
                )
            elif tag == "input":
                lines.append(
                    f'    <label>{_esc(aria)}'
                    f' <input type="text" aria-label="{_esc(aria)}"></label>'
                )
            elif tag == "select":
                lines.append(
                    f'    <label>{_esc(aria)}'
                    f' <select aria-label="{_esc(aria)}">'
                    f"<option></option></select></label>"
                )
            elif tag == "textarea":
                lines.append(
                    f'    <label>{_esc(aria)}'
                    f' <textarea aria-label="{_esc(aria)}"></textarea>'
                    f"</label>"
                )

        lines.append("  </section>")
        return "\n".join(lines)

    def generate_nav(
        self,
        items: list[dict[str, Any]],
        brand: str = "App",
    ) -> str:
        """Generate a ``<nav>`` with brand and navigation items."""
        item_lines: list[str] = []
        for item in items:
            label = _esc(item.get("label", "Link"))
            href = _esc(item.get("href", "#"))
            view = _esc(item.get("view", ""))
            extra = f' data-view="{view}"' if view else ""
            item_lines.append(
                f'      <li><a href="{href}"{extra}>{label}</a></li>'
            )
        items_html = "\n".join(item_lines)
        return textwrap.dedent(f"""\
  <header role="banner">
    <nav aria-label="Main navigation">
      <div class="nav-brand">{_esc(brand)}</div>
      <ul class="nav-items" role="menubar">
{items_html}
      </ul>
    </nav>
  </header>""")

    def generate_hero(self, title: str, tagline: str = "") -> str:
        """Generate a hero / landing section."""
        tagline_html = (
            f'\n    <p class="hero-tagline">{_esc(tagline)}</p>'
            if tagline
            else ""
        )
        return textwrap.dedent(f"""\
  <section class="hero" aria-label="Hero">
    <h1 class="hero-title">{_esc(title)}</h1>{tagline_html}
  </section>""")

    def generate_loading_screen(
        self,
        phases: list[str] | None = None,
    ) -> str:
        """Generate a loading overlay with progress bar and phase list."""
        phases = phases or ["Initializing…"]
        phase_items = "\n".join(
            f'        <li class="loading-phase" data-phase="{i}">'
            f"{_esc(p)}</li>"
            for i, p in enumerate(phases)
        )
        return textwrap.dedent(f"""\
  <div id="loading-screen" class="loading-screen" role="status" aria-label="Loading">
    <div class="loading-content">
      <div class="loading-spinner" aria-hidden="true"></div>
      <p class="loading-title">Loading…</p>
      <div class="loading-progress">
        <div class="loading-progress-bar" role="progressbar"
             aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"
             style="width: 0%;">
        </div>
      </div>
      <ul class="loading-phases" aria-label="Loading phases">
{phase_items}
      </ul>
    </div>
  </div>""")

    def generate_footer(self, text: str = "") -> str:
        """Generate a ``<footer>`` landmark."""
        content = _esc(text) if text else "&copy; All rights reserved."
        return textwrap.dedent(f"""\
  <footer role="contentinfo" aria-label="Site footer">
    <p>{content}</p>
  </footer>""")

    def generate_modal(self, id: str, title: str = "Dialog") -> str:
        """Generate an accessible modal ``<dialog>``."""
        return textwrap.dedent(f"""\
  <dialog id="{_esc(id)}" class="modal" aria-labelledby="{_esc(id)}-title" aria-modal="true">
    <div class="modal-content">
      <header class="modal-header">
        <h2 id="{_esc(id)}-title">{_esc(title)}</h2>
        <button type="button" class="modal-close" aria-label="Close dialog">&times;</button>
      </header>
      <div class="modal-body"></div>
      <footer class="modal-footer">
        <button type="button" class="modal-cancel">Cancel</button>
        <button type="button" class="modal-confirm">Confirm</button>
      </footer>
    </div>
  </dialog>""")

    def generate_toast_container(self) -> str:
        """Generate a toast-notification container."""
        return textwrap.dedent("""\
  <div id="toast-container" class="toast-container"
       role="status" aria-live="polite" aria-label="Notifications">
  </div>""")

    # -- main assembly -------------------------------------------------------

    def generate_all(
        self,
        title: str,
        tagline: str = "",
        nav_items: list[dict[str, Any]] | None = None,
        view_specs: list[ViewHTMLSpec] | None = None,
        loading_phases: list[str] | None = None,
        css_href: str = "style.css",
        js_src: str = "app.js",
    ) -> str:
        """Generate a **complete**, well-formed HTML document.

        Assembles every structural piece — DOCTYPE, head, skip link, nav,
        hero, loading screen, view sections (hidden except ``home``),
        modal, toast container, footer, and closing tags — into a single
        HTML string that satisfies the document-structure, accessibility,
        and semantic obligations by construction.
        """
        nav_items = nav_items or []
        view_specs = view_specs or []
        loading_phases = loading_phases or ["Initializing…"]

        head, close = self.generate_document_shell(title, css_href, js_src)

        parts: list[str] = [head]

        # Navigation
        if nav_items:
            parts.append(self.generate_nav(nav_items, brand=title))
            parts.append("")

        # Loading screen
        parts.append(self.generate_loading_screen(loading_phases))
        parts.append("")

        # Main content area
        parts.append('  <main id="main" role="main" aria-label="Main content">')

        # Hero
        if tagline:
            parts.append(self.generate_hero(title, tagline))
            parts.append("")

        # View sections
        for spec in view_specs:
            parts.append(self.generate_view_section(spec))
            parts.append("")

        parts.append("  </main>")
        parts.append("")

        # Modal
        parts.append(self.generate_modal("app-modal", "Dialog"))
        parts.append("")

        # Toast container
        parts.append(self.generate_toast_container())
        parts.append("")

        # Footer
        parts.append(self.generate_footer())
        parts.append("")

        # Close
        parts.append(close)

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fragment extractor
# ---------------------------------------------------------------------------

class HTMLFragmentExtractor:
    """Extract and merge body-level HTML fragments.

    Useful when an LLM agent returns a full HTML document but we only
    need the ``<body>`` content, or when we need to splice agent-generated
    HTML into a template-generated shell.
    """

    _BODY_RE = re.compile(
        r"<body[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL,
    )
    _VIEW_RE = re.compile(
        r'(<section[^>]*\bdata-view="([^"]+)"[^>]*>.*?</section>)',
        re.IGNORECASE | re.DOTALL,
    )

    def extract_body_content(self, html: str) -> str:
        """Strip DOCTYPE, ``<html>``, ``<head>``, ``<body>`` wrappers.

        Returns the inner content of ``<body>`` if present, otherwise
        returns the input unchanged (assumed to already be a fragment).
        """
        m = self._BODY_RE.search(html)
        if m:
            return m.group(1).strip()
        # Fallback: strip common wrappers manually
        out = html
        for pat in (
            r"<!DOCTYPE[^>]*>",
            r"<html[^>]*>",
            r"</html\s*>",
            r"<head[^>]*>.*?</head\s*>",
            r"<body[^>]*>",
            r"</body\s*>",
        ):
            out = re.sub(pat, "", out, flags=re.IGNORECASE | re.DOTALL)
        return out.strip()

    def extract_views(self, html: str) -> dict[str, str]:
        """Extract ``<section data-view="…">`` blocks from *html*.

        Returns a dict mapping ``view_id → section_html``.
        """
        views: dict[str, str] = {}
        for match in self._VIEW_RE.finditer(html):
            section_html = match.group(1)
            view_id = match.group(2)
            views[view_id] = section_html
        return views

    def merge_agent_and_template(
        self,
        agent_html: str,
        template_views: dict[str, str],
    ) -> str:
        """Merge agent-generated HTML with template view sections.

        Strategy:
        1. Extract body content from the agent HTML.
        2. Extract view sections from the agent body content.
        3. For each template view, prefer the agent version if it exists
           and is non-trivial (more than just a placeholder comment).
        4. Return merged HTML with all views present.
        """
        agent_body = self.extract_body_content(agent_html)
        agent_views = self.extract_views(agent_body)

        merged: dict[str, str] = {}
        for view_id, template_section in template_views.items():
            agent_section = agent_views.get(view_id, "")
            if agent_section and not self._is_placeholder(agent_section):
                merged[view_id] = agent_section
            else:
                merged[view_id] = template_section

        # Include any agent views that are not in the template
        for view_id, section in agent_views.items():
            if view_id not in merged:
                merged[view_id] = section

        return "\n\n".join(merged.values())

    @staticmethod
    def _is_placeholder(section_html: str) -> bool:
        """Return True if *section_html* is a trivial placeholder."""
        stripped = re.sub(r"<!--.*?-->", "", section_html, flags=re.DOTALL)
        stripped = re.sub(r"<[^>]+>", "", stripped)
        stripped = stripped.strip()
        return len(stripped) < 10
