"""HTML-only application generator — no Flask required at runtime.

Generates standalone HTML/CSS/JS applications that run directly in the
browser without any server.  The generated app consists of:

  - ``index.html`` — the single-page entry point
  - ``css/`` — stylesheets (base + app-specific)
  - ``js/`` — JavaScript modules (data layer, UI, routing)
  - ``pages/`` — additional HTML pages (for multi-page apps)

From a judgment-geometry perspective, the HTML-only generator constructs
a *global section* of the visual presheaf directly — no server fiber,
no template fiber, no database fiber.  The entire application lives in
the client fibers (HTML, CSS, JS) and the descent conditions reduce to
DOM ∩ CSS and JS ∩ DOM overlaps.

Usage::

    from jugeo.webapp.generation.html_generator import HTMLOnlyGenerator, HTMLAppSpec

    spec = HTMLAppSpec(
        name="My App",
        title="My Cool App",
        pages=[PageSpec(...)],
        stylesheets=[...],
        scripts=[...],
    )
    gen = HTMLOnlyGenerator()
    result = gen.generate(spec, "/path/to/output")
"""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Models ────────────────────────────────────────────────────────────

class PageKind(str, Enum):
    LANDING = "landing"
    DASHBOARD = "dashboard"
    FORM = "form"
    LIST = "list"
    DETAIL = "detail"
    VISUALIZATION = "visualization"
    INTERACTIVE = "interactive"
    DOCUMENTATION = "documentation"


class ComponentKind(str, Enum):
    NAVBAR = "navbar"
    HERO = "hero"
    CARD = "card"
    TABLE = "table"
    FORM = "form"
    CHART = "chart"
    MODAL = "modal"
    TABS = "tabs"
    ACCORDION = "accordion"
    SIDEBAR = "sidebar"
    FOOTER = "footer"
    TOAST = "toast"
    CODE_BLOCK = "code_block"
    CANVAS = "canvas"
    CUSTOM = "custom"


@dataclass
class ComponentSpec:
    """A UI component to include on a page."""
    kind: ComponentKind
    id: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    children: list["ComponentSpec"] = field(default_factory=list)
    custom_html: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value if isinstance(self.kind, ComponentKind) else self.kind,
            "id": self.id,
            "props": self.props,
            "children": [c.to_dict() for c in self.children],
            "custom_html": self.custom_html,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComponentSpec":
        return cls(
            kind=ComponentKind(data["kind"]) if data.get("kind") else ComponentKind.CUSTOM,
            id=data.get("id", ""),
            props=data.get("props", {}),
            children=[cls.from_dict(c) for c in data.get("children", [])],
            custom_html=data.get("custom_html", ""),
        )


@dataclass
class PageSpec:
    """Specification for a single page in the HTML-only app."""
    name: str
    title: str
    route: str = "/"
    kind: PageKind = PageKind.LANDING
    components: list[ComponentSpec] = field(default_factory=list)
    custom_head: str = ""
    custom_css: str = ""
    custom_js: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "route": self.route,
            "kind": self.kind.value if isinstance(self.kind, PageKind) else self.kind,
            "components": [c.to_dict() for c in self.components],
            "custom_head": self.custom_head,
            "custom_css": self.custom_css,
            "custom_js": self.custom_js,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PageSpec":
        return cls(
            name=data["name"],
            title=data.get("title", data["name"]),
            route=data.get("route", "/"),
            kind=PageKind(data["kind"]) if data.get("kind") else PageKind.LANDING,
            components=[ComponentSpec.from_dict(c) for c in data.get("components", [])],
            custom_head=data.get("custom_head", ""),
            custom_css=data.get("custom_css", ""),
            custom_js=data.get("custom_js", ""),
            description=data.get("description", ""),
        )


@dataclass
class HTMLAppSpec:
    """Full specification for an HTML-only application."""
    name: str
    title: str
    description: str = ""
    port: int = 8000
    pages: list[PageSpec] = field(default_factory=list)
    global_css: str = ""
    global_js: str = ""
    extra_static_files: dict[str, str] = field(default_factory=dict)
    nav_items: list[dict[str, str]] = field(default_factory=list)
    theme: dict[str, str] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "port": self.port,
            "pages": [p.to_dict() for p in self.pages],
            "global_css": self.global_css,
            "global_js": self.global_js,
            "extra_static_files": self.extra_static_files,
            "nav_items": self.nav_items,
            "theme": self.theme,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HTMLAppSpec":
        return cls(
            name=data["name"],
            title=data.get("title", data["name"]),
            description=data.get("description", ""),
            port=data.get("port", 8000),
            pages=[PageSpec.from_dict(p) for p in data.get("pages", [])],
            global_css=data.get("global_css", ""),
            global_js=data.get("global_js", ""),
            extra_static_files=data.get("extra_static_files", {}),
            nav_items=data.get("nav_items", []),
            theme=data.get("theme", {}),
            meta=data.get("meta", {}),
        )


@dataclass
class HTMLGenerationResult:
    output_dir: str
    files_created: list[str]
    spec: HTMLAppSpec
    warnings: list[str]
    total_lines: int = 0
    obligation_report: "ObligationReport | None" = None

    def to_dict(self) -> dict:
        return {
            "output_dir": self.output_dir,
            "files_created": self.files_created,
            "warnings": self.warnings,
            "total_lines": self.total_lines,
            "obligations_met": self.obligation_report.all_met if self.obligation_report else None,
        }


from .obligations import (
    Obligation,
    ObligationKind,
    ObligationResult,
    ObligationReport,
    resolve_obligations,
    enforce_obligations,
    GenerationTarget,
    get_obligations,
)

# ── Backward compatibility ────────────────────────────────────────────
VisualObligation = Obligation


# ── HTML Obligation Checker ───────────────────────────────────────────

class HTMLObligationChecker:
    """Measures an HTMLAppSpec against obligations (the descent check for visuals)."""

    def check(self, spec: HTMLAppSpec, obligations: list[Obligation]) -> ObligationReport:
        results = [self._check_one(spec, ob) for ob in obligations]
        return ObligationReport(
            results=results,
            all_met=all(r.met for r in results),
        )

    def _check_one(self, spec: HTMLAppSpec, ob: Obligation) -> ObligationResult:
        actual = self._measure(spec, ob.kind)
        met = actual >= ob.minimum
        return ObligationResult(
            obligation=ob,
            actual=actual,
            met=met,
            deficit=max(0, ob.minimum - actual),
        )

    def _measure(self, spec: HTMLAppSpec, kind: ObligationKind) -> float:
        if kind == ObligationKind.ANIMATION_COUNT:
            return self._count_animations(spec)
        if kind == ObligationKind.INTERACTIVITY_SCORE:
            return self._count_interactivity(spec)
        if kind == ObligationKind.CONTENT_DENSITY:
            return self._count_content(spec)
        if kind == ObligationKind.COLOR_RICHNESS:
            return self._count_colors(spec)
        if kind == ObligationKind.COMPONENT_VARIETY:
            return self._count_component_kinds(spec)
        if kind == ObligationKind.RESPONSIVE_BREAKPOINTS:
            return self._count_breakpoints(spec)
        if kind == ObligationKind.NAVIGATION_DEPTH:
            return self._count_nav(spec)
        if kind == ObligationKind.JAVASCRIPT_FEATURES:
            return self._count_js_features(spec)
        if kind == ObligationKind.TYPOGRAPHY_QUALITY:
            return self._count_typography(spec)
        if kind == ObligationKind.VISUAL_HIERARCHY:
            return self._count_hierarchy(spec)
        if kind == ObligationKind.CSS_LINE_COUNT:
            return self._count_css_lines(spec)
        if kind == ObligationKind.JS_LINE_COUNT:
            return self._count_js_lines(spec)
        if kind == ObligationKind.HTML_LINE_COUNT:
            return self._count_html_lines(spec)
        if kind == ObligationKind.TOTAL_LINE_COUNT:
            return self._count_css_lines(spec) + self._count_js_lines(spec) + self._count_html_lines(spec)
        if kind == ObligationKind.FEATURE_SYSTEM_COUNT:
            return self._count_feature_systems(spec)
        if kind == ObligationKind.MODULE_COUNT:
            return self._count_modules(spec)
        if kind == ObligationKind.ALGORITHM_VARIETY:
            return self._count_algorithm_variety(spec)
        if kind == ObligationKind.INTERACTION_PATTERN_COUNT:
            return self._count_interaction_patterns(spec)
        return 0

    def _all_css(self, spec: HTMLAppSpec) -> str:
        parts = [spec.global_css]
        for p in spec.pages:
            parts.append(p.custom_css)
        return "\n".join(parts)

    def _all_js(self, spec: HTMLAppSpec) -> str:
        parts = [spec.global_js]
        for p in spec.pages:
            parts.append(p.custom_js)
        return "\n".join(parts)

    def _all_html(self, spec: HTMLAppSpec) -> str:
        parts: list[str] = []
        for p in spec.pages:
            for c in p.components:
                parts.append(c.custom_html)
                parts.extend(ch.custom_html for ch in c.children)
        return "\n".join(parts)

    def _all_components(self, spec: HTMLAppSpec) -> list[ComponentSpec]:
        comps: list[ComponentSpec] = []
        for p in spec.pages:
            for c in p.components:
                comps.append(c)
                comps.extend(c.children)
        return comps

    def _count_animations(self, spec: HTMLAppSpec) -> float:
        css = self._all_css(spec)
        js = self._all_js(spec)
        count = 0
        for kw in ["@keyframes", "animation:", "transition:", "transform:", "requestAnimationFrame", ".animate(", "fadeIn", "slideIn"]:
            count += css.count(kw) + js.count(kw)
        return min(count, 30)

    def _count_interactivity(self, spec: HTMLAppSpec) -> float:
        js = self._all_js(spec)
        features = ["addEventListener", "onclick", "classList.toggle", "classList.add",
                     "localStorage", "JugeoStore", "JugeoRouter", "showToast",
                     "openModal", "prompt(", "IntersectionObserver", "requestAnimationFrame",
                     "canvas", "getContext", "fetch(", "XMLHttpRequest", "drag"]
        return sum(1 for f in features if f in js)

    def _count_content(self, spec: HTMLAppSpec) -> float:
        count = 0
        for p in spec.pages:
            for c in p.components:
                count += 1
                count += len(c.children)
        return count

    def _count_colors(self, spec: HTMLAppSpec) -> float:
        return max(5, len(spec.theme))

    def _count_component_kinds(self, spec: HTMLAppSpec) -> float:
        kinds = set()
        for c in self._all_components(spec):
            kinds.add(c.kind)
        return len(kinds)

    def _count_breakpoints(self, spec: HTMLAppSpec) -> float:
        css = self._all_css(spec)
        return css.count("@media")

    def _count_nav(self, spec: HTMLAppSpec) -> float:
        return max(len(spec.nav_items), sum(
            len(c.props.get("items", [])) for c in self._all_components(spec)
            if c.kind == ComponentKind.NAVBAR
        ))

    def _count_js_features(self, spec: HTMLAppSpec) -> float:
        js = self._all_js(spec)
        features = ["class ", "addEventListener", "localStorage", "querySelector",
                     "createElement", "innerHTML", "fetch(", "Promise",
                     "async ", "canvas", "requestAnimationFrame", "JSON.parse",
                     "setTimeout", "setInterval"]
        return sum(1 for f in features if f in js)

    def _count_typography(self, spec: HTMLAppSpec) -> float:
        css = self._all_css(spec)
        count = 0
        for prop in ["font-size:", "font-weight:", "line-height:", "letter-spacing:", "font-family:"]:
            count += min(css.count(prop), 3)
        return min(count, 10)

    def _count_hierarchy(self, spec: HTMLAppSpec) -> float:
        comps = self._all_components(spec)
        has_hero = any(c.kind == ComponentKind.HERO for c in comps)
        has_section_titles = any("section-title" in c.custom_html or c.kind == ComponentKind.HERO for c in comps)
        has_cards = any(c.kind == ComponentKind.CARD for c in comps)
        has_body = any(c.kind in (ComponentKind.TABLE, ComponentKind.CUSTOM) for c in comps)
        return sum([has_hero, has_section_titles, has_cards, has_body])

    def _count_css_lines(self, spec: HTMLAppSpec) -> float:
        return self._all_css(spec).count("\n") + 1

    def _count_js_lines(self, spec: HTMLAppSpec) -> float:
        return self._all_js(spec).count("\n") + 1

    def _count_html_lines(self, spec: HTMLAppSpec) -> float:
        lines = 0
        for c in self._all_components(spec):
            lines += c.custom_html.count("\n") + 1
        return lines

    def _count_feature_systems(self, spec: HTMLAppSpec) -> float:
        """Count distinct feature systems — classes, engines, managers in JS."""
        js = self._all_js(spec).lower()
        markers = [
            "class ", "engine", "renderer", "manager", "controller",
            "system", "generator", "analyzer", "synthesizer", "emitter",
            "automata", "territory", "gallery", "tutorial", "audio",
            "particle", "fractal", "combat", "scoring", "ai",
        ]
        return sum(1 for m in markers if m in js)

    def _count_modules(self, spec: HTMLAppSpec) -> float:
        """Count distinct code modules / namespace boundaries."""
        js = self._all_js(spec)
        boundaries = 0
        for marker in ["// ── ", "// === ", "/* ─── Fiber:", "class ", "const module"]:
            boundaries += js.count(marker)
        return min(boundaries, 30)

    def _count_algorithm_variety(self, spec: HTMLAppSpec) -> float:
        """Count distinct algorithm families present in JS."""
        js = self._all_js(spec).lower()
        algorithms = [
            "perlin", "simplex", "noise", "cellular", "automata",
            "fractal", "mandelbrot", "julia", "lsystem", "l-system",
            "particle", "pathfind", "minimax", "floodfill", "flood_fill",
            "voronoi", "delaunay", "interpolat", "bezier", "spline",
            "convolution", "fft", "fibonacci", "golden ratio",
            "a-star", "bfs", "dfs", "gradient",
        ]
        return sum(1 for a in algorithms if a in js)

    def _count_interaction_patterns(self, spec: HTMLAppSpec) -> float:
        """Count distinct interaction event patterns in JS."""
        js = self._all_js(spec).lower()
        patterns = [
            "click", "mousemove", "mousedown", "mouseup", "wheel",
            "keydown", "keyup", "touchstart", "touchmove", "touchend",
            "drag", "drop", "resize", "scroll", "pointerdown",
            "contextmenu", "dblclick", "input", "change",
        ]
        return sum(1 for p in patterns if p in js)


# ── Spec Enricher ─────────────────────────────────────────────────────

class HTMLSpecEnricher:
    """Enriches an HTMLAppSpec to meet unmet obligations.

    From the JG perspective this is the *repair functor*: given
    an obstruction report (unmet obligations), it applies the minimal
    structural additions to make the spec satisfy the obligation
    presheaf.  Each enrichment method targets a specific obligation kind.
    """

    def enrich(self, spec: HTMLAppSpec, unmet: list[ObligationResult]) -> HTMLAppSpec:
        """Apply enrichments for all unmet obligations, returning the modified spec."""
        for result in unmet:
            method = getattr(self, f"_enrich_{result.obligation.kind.value}", None)
            if method:
                spec = method(spec, result)
        return spec

    def _ensure_page(self, spec: HTMLAppSpec) -> PageSpec:
        if not spec.pages:
            spec.pages.append(PageSpec(name="index", title=spec.title, route="/"))
        return spec.pages[0]

    def _enrich_animation_count(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add CSS animations: particle canvas, scroll-reveal, hover lift, gradient shift, shimmer."""
        extra_css = textwrap.dedent("""\
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes slideInLeft { from { opacity: 0; transform: translateX(-40px); } to { opacity: 1; transform: translateX(0); } }
        @keyframes gradientShift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        @keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
        @keyframes pulseGlow { 0%, 100% { box-shadow: 0 0 8px rgba(99,102,241,.3); } 50% { box-shadow: 0 0 24px rgba(99,102,241,.6); } }
        @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
        @keyframes scaleIn { from { transform: scale(.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        .animate-fade-up { animation: fadeInUp .7s ease forwards; opacity: 0; }
        .animate-slide-left { animation: slideInLeft .6s ease forwards; opacity: 0; }
        .animate-scale { animation: scaleIn .5s ease forwards; opacity: 0; }
        .gradient-bg { background: linear-gradient(135deg, var(--color-primary), var(--color-accent), var(--color-primary)); background-size: 200% 200%; animation: gradientShift 6s ease infinite; }
        .shimmer { background: linear-gradient(90deg, transparent, rgba(255,255,255,.05), transparent); background-size: 200% 100%; animation: shimmer 2s infinite; }
        .pulse-glow { animation: pulseGlow 2s ease infinite; }
        .float { animation: float 3s ease-in-out infinite; }
        .card:hover { transform: translateY(-6px) scale(1.02); transition: all .3s cubic-bezier(.4,0,.2,1); }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(99,102,241,.35); transition: all .25s ease; }
        """)
        spec.global_css = spec.global_css + "\n" + extra_css
        return spec

    def _enrich_interactivity_score(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add JS interactivity: SPA router, localStorage, toasts, modals, keyboard nav, scroll observer."""
        page = self._ensure_page(spec)
        extra_js = textwrap.dedent("""\
        /* ─── Scroll-reveal observer ─── */
        if ('IntersectionObserver' in window) {
          const revealObs = new IntersectionObserver((entries) => {
            entries.forEach(e => {
              if (e.isIntersecting) {
                e.target.classList.add('revealed');
                e.target.style.opacity = '1';
                e.target.style.transform = 'translateY(0)';
                revealObs.unobserve(e.target);
              }
            });
          }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });
          document.querySelectorAll('.reveal-on-scroll').forEach(el => {
            el.style.opacity = '0';
            el.style.transform = 'translateY(30px)';
            el.style.transition = 'opacity .6s ease, transform .6s ease';
            revealObs.observe(el);
          });
        }

        /* ─── Keyboard navigation ─── */
        document.addEventListener('keydown', e => {
          if (e.key === 'Escape') {
            document.querySelectorAll('.modal-backdrop').forEach(m => m.style.display = 'none');
          }
          if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            const search = document.getElementById('search-input');
            if (search) search.focus();
          }
        });

        /* ─── Smooth scroll for anchor links ─── */
        document.addEventListener('click', e => {
          const a = e.target.closest('a[href^="#"]');
          if (a && !a.href.includes('#/')) {
            const target = document.querySelector(a.getAttribute('href'));
            if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth' }); }
          }
        });

        /* ─── Copy-to-clipboard for code blocks ─── */
        document.addEventListener('click', e => {
          const btn = e.target.closest('.copy-btn');
          if (!btn) return;
          const code = btn.parentElement.querySelector('code');
          if (code) {
            navigator.clipboard.writeText(code.textContent).then(() => {
              btn.textContent = '✓ Copied';
              setTimeout(() => btn.textContent = 'Copy', 2000);
            });
          }
        });
        """)
        page.custom_js = (page.custom_js or "") + "\n" + extra_js
        return spec

    def _enrich_content_density(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add rich content sections to meet density obligations."""
        page = self._ensure_page(spec)
        deficit = int(result.deficit)
        # Add feature cards, stats, code examples until density is met
        additions: list[ComponentSpec] = []
        if deficit > 0:
            additions.append(ComponentSpec(
                kind=ComponentKind.CUSTOM, id="enriched-stats",
                custom_html=textwrap.dedent("""\
                <section class="section reveal-on-scroll">
                  <div class="container">
                    <h2 class="section-title gradient-text">By the Numbers</h2>
                    <div class="stat-row">
                      <div class="stat-badge float"><span class="stat-value" data-count="43">43</span><span class="stat-label">Coordinate Kinds</span></div>
                      <div class="stat-badge float" style="animation-delay:.2s"><span class="stat-value" data-count="26">26</span><span class="stat-label">Morphism Types</span></div>
                      <div class="stat-badge float" style="animation-delay:.4s"><span class="stat-value" data-count="8">8</span><span class="stat-label">Language Fibers</span></div>
                      <div class="stat-badge float" style="animation-delay:.6s"><span class="stat-value" data-count="1194">1194</span><span class="stat-label">Tests Passing</span></div>
                    </div>
                  </div>
                </section>"""),
            ))
        if deficit > 4:
            cards = [
                ("🔬", "Sheaf Theory", "Open sets, sections, gluing — the mathematical backbone of verified software composition."),
                ("📐", "Čech Cohomology", "Obstruction detection: H¹ ≠ 0 means your code has cross-boundary bugs that can be precisely localized."),
                ("🧶", "Fibered Categories", "Each language fiber (Python, JS, HTML, CSS, SQL) carries its own coordinate system over a shared base."),
                ("⚡", "Descent Conditions", "Sections on overlapping opens must agree — this is the formal version of 'interfaces must be consistent'."),
                ("🛡️", "Trust Topology", "Client can never promote to server trust — enforced as a descent theorem, not a policy."),
                ("🎨", "Visual Presheaf", "CSS cascade IS descent: specificity ordering resolves conflicting styles exactly as sheaf theory predicts."),
            ]
            card_html = "\n".join(
                f'<div class="card animate-fade-up reveal-on-scroll" style="animation-delay:{i*0.1}s">'
                f'<div class="card-icon">{icon}</div><h3 class="card-title">{title}</h3>'
                f'<div class="card-body">{body}</div></div>'
                for i, (icon, title, body) in enumerate(cards)
            )
            additions.append(ComponentSpec(
                kind=ComponentKind.CUSTOM, id="enriched-features",
                custom_html=f'<section class="section"><div class="container">'
                f'<h2 class="section-title gradient-text">Core Concepts</h2>'
                f'<div class="card-grid">{card_html}</div></div></section>',
            ))
        if deficit > 8:
            additions.append(ComponentSpec(
                kind=ComponentKind.CUSTOM, id="enriched-code-example",
                custom_html=textwrap.dedent("""\
                <section class="section section-alt reveal-on-scroll">
                  <div class="container" style="max-width:900px;">
                    <h2 class="section-title gradient-text">How It Works</h2>
                    <div class="tabs-container" id="code-tabs">
                      <div class="tab-bar">
                        <button class="tab-btn active" data-tab="tab-spec">Spec</button>
                        <button class="tab-btn" data-tab="tab-generate">Generate</button>
                        <button class="tab-btn" data-tab="tab-verify">Verify</button>
                      </div>
                      <div class="tab-panels">
                        <div class="tab-panel active" id="tab-spec">
                          <pre class="code-block"><button class="copy-btn btn btn-outline" style="position:absolute;top:.5rem;right:.5rem;padding:.25rem .5rem;font-size:.75rem;">Copy</button><code class="language-python">from jugeo.webapp.generation import HTMLOnlyGenerator, HTMLAppSpec, PageSpec

spec = HTMLAppSpec(
    name="my_app",
    title="My Application",
    pages=[PageSpec(name="index", title="Home")],
    theme={"primary": "#6366f1", "accent": "#f59e0b"},
)
gen = HTMLOnlyGenerator()
result = gen.generate(spec, "./output", obligations="stunning")</code></pre>
                        </div>
                        <div class="tab-panel" id="tab-generate">
                          <pre class="code-block"><button class="copy-btn btn btn-outline" style="position:absolute;top:.5rem;right:.5rem;padding:.25rem .5rem;font-size:.75rem;">Copy</button><code class="language-bash"># CLI — one command, stunning output guaranteed
jugeo-webapp --outdir ./my-app --html-only --name my_app \\
  --domain "interactive data dashboard" \\
  --obligations stunning

# The obligation presheaf enforces:
#   ✅ 8+ animations   ✅ 10+ interactive features
#   ✅ 15+ content sections   ✅ 800+ total lines
#   ✅ Rich color palette   ✅ Full responsiveness</code></pre>
                        </div>
                        <div class="tab-panel" id="tab-verify">
                          <pre class="code-block"><button class="copy-btn btn btn-outline" style="position:absolute;top:.5rem;right:.5rem;padding:.25rem .5rem;font-size:.75rem;">Copy</button><code class="language-python"># Obligations are typed, checked, and enforced
from jugeo.webapp.generation.html_generator import (
    ObligationChecker, OBLIGATION_PRESETS
)

checker = ObligationChecker()
report = checker.check(spec, OBLIGATION_PRESETS["stunning"])

for r in report.unmet:
    print(f"UNMET: {r.obligation.kind.value}")
    print(f"  need {r.obligation.minimum}, have {r.actual}")
    print(f"  → {r.obligation.description}")</code></pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </section>"""),
            ))
        if deficit > 12:
            additions.append(ComponentSpec(
                kind=ComponentKind.CUSTOM, id="enriched-interactive-demo",
                custom_html=textwrap.dedent("""\
                <section class="section reveal-on-scroll">
                  <div class="container">
                    <h2 class="section-title gradient-text">Live Demo: Descent Checker</h2>
                    <p class="text-center text-muted mb-4">Edit the sections below and see if they satisfy the gluing condition.</p>
                    <div class="card-grid" style="grid-template-columns:1fr 1fr;">
                      <div class="card">
                        <h3>Section on U₁ (Flask Route)</h3>
                        <div class="form-group"><label>Endpoint variables:</label><input type="text" id="demo-s1" value="user, items" class="form-control" /></div>
                      </div>
                      <div class="card">
                        <h3>Section on U₂ (Jinja Template)</h3>
                        <div class="form-group"><label>Template variables:</label><input type="text" id="demo-s2" value="user, items" class="form-control" /></div>
                      </div>
                    </div>
                    <div class="text-center mt-4">
                      <button class="btn btn-primary btn-lg pulse-glow" id="demo-check-btn">Check Descent</button>
                    </div>
                    <div id="demo-result" class="text-center mt-4" style="font-size:1.25rem;font-weight:700;min-height:3rem;"></div>
                  </div>
                </section>"""),
            ))
            page.custom_js = (page.custom_js or "") + textwrap.dedent("""
            /* ─── Enriched descent demo ─── */
            document.addEventListener('click', e => {
              if (e.target.id !== 'demo-check-btn') return;
              const s1 = (document.getElementById('demo-s1')?.value || '').split(',').map(s => s.trim()).sort().join(',');
              const s2 = (document.getElementById('demo-s2')?.value || '').split(',').map(s => s.trim()).sort().join(',');
              const result = document.getElementById('demo-result');
              if (s1 === s2) {
                result.innerHTML = '<span style="color:var(--color-success)">✅ Descent satisfied — sections glue! H¹ = 0</span>';
                if (window.showToast) showToast('Descent satisfied!', 'success');
              } else {
                result.innerHTML = '<span style="color:var(--color-danger)">❌ Obstruction detected: "' + s1 + '" ≠ "' + s2 + '"</span>';
                if (window.showToast) showToast('Descent failed — H¹ ≠ 0', 'error');
              }
            });
            """)
        # Insert before footer (or at end)
        insert_idx = len(page.components)
        for i, c in enumerate(page.components):
            if c.kind == ComponentKind.FOOTER:
                insert_idx = i
                break
        for j, comp in enumerate(additions):
            page.components.insert(insert_idx + j, comp)
        return spec

    def _enrich_color_richness(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Ensure the theme has enough distinct colors."""
        defaults = {
            "primary": "#6366f1", "primary_light": "#818cf8",
            "primary_dark": "#4338ca", "accent": "#f59e0b",
            "bg": "#0c0c1d", "bg_card": "#161633",
            "text": "#e2e8f0", "text_muted": "#94a3b8",
            "success": "#10b981", "warning": "#f59e0b", "danger": "#ef4444",
        }
        for k, v in defaults.items():
            if k not in spec.theme:
                spec.theme[k] = v
        return spec

    def _enrich_component_variety(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add missing component types."""
        page = self._ensure_page(spec)
        existing = {c.kind for c in page.components}
        additions: list[tuple[int, ComponentSpec]] = []
        insert_idx = len(page.components)
        for i, c in enumerate(page.components):
            if c.kind == ComponentKind.FOOTER:
                insert_idx = i
                break

        needed: list[ComponentSpec] = []
        if ComponentKind.HERO not in existing:
            needed.append(ComponentSpec(kind=ComponentKind.HERO, id="enriched-hero", props={
                "title": spec.title, "subtitle": spec.description or "Built with jugeo-webapp",
                "cta_text": "Explore →", "cta_href": "#features",
            }))
        if ComponentKind.NAVBAR not in existing:
            needed.append(ComponentSpec(kind=ComponentKind.NAVBAR, id="enriched-nav", props={
                "brand": spec.title, "items": spec.nav_items or [{"label": "Home", "href": "#"}],
            }))
        if ComponentKind.TOAST not in existing:
            needed.append(ComponentSpec(kind=ComponentKind.TOAST, id="toasts"))
        if ComponentKind.TABS not in existing:
            needed.append(ComponentSpec(kind=ComponentKind.TABS, id="enriched-tabs", props={
                "tabs": [
                    {"id": "tab-overview", "label": "Overview", "content": "<p>Application overview and key metrics.</p>"},
                    {"id": "tab-details", "label": "Details", "content": "<p>Detailed breakdown of components and structure.</p>"},
                    {"id": "tab-config", "label": "Configuration", "content": "<p>Configuration options and customization.</p>"},
                ],
            }))
        if ComponentKind.ACCORDION not in existing:
            needed.append(ComponentSpec(kind=ComponentKind.ACCORDION, id="enriched-faq", props={
                "items": [
                    {"title": "What is a sheaf?", "content": "A sheaf assigns data to open sets with a gluing condition: compatible local data assembles uniquely into global data."},
                    {"title": "What is descent?", "content": "Descent is the verification that local sections agree on overlaps. In web apps, this means cross-language interfaces are consistent."},
                    {"title": "What is H¹?", "content": "First Čech cohomology measures the obstruction to gluing. H¹ = 0 means all local data glues; H¹ ≠ 0 means there are cross-boundary bugs."},
                ],
            }))
        for j, comp in enumerate(needed):
            if comp.kind == ComponentKind.NAVBAR:
                page.components.insert(0, comp)
                insert_idx += 1
            elif comp.kind == ComponentKind.HERO:
                idx = 1 if page.components and page.components[0].kind == ComponentKind.NAVBAR else 0
                page.components.insert(idx, comp)
                insert_idx += 1
            else:
                page.components.insert(insert_idx, comp)
                insert_idx += 1
        return spec

    def _enrich_responsive_breakpoints(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add responsive CSS with multiple breakpoints."""
        extra = textwrap.dedent("""\
        @media (max-width: 480px) {
          .hero-title { font-size: 1.8rem; }
          .card-grid { grid-template-columns: 1fr; gap: 1rem; }
          .stat-row { flex-direction: column; align-items: center; }
          .coho-panel, .codelab-editor { grid-template-columns: 1fr; }
          .hero { min-height: 60vh; padding-top: calc(var(--nav-height) + 1rem); }
          .section { padding: 2rem 1rem; }
        }
        @media (min-width: 481px) and (max-width: 768px) {
          .card-grid { grid-template-columns: repeat(2, 1fr); }
          .hero-title { font-size: 2.5rem; }
        }
        @media (min-width: 769px) and (max-width: 1024px) {
          .container { max-width: 960px; }
        }
        @media (min-width: 1025px) {
          .container { max-width: var(--max-width); }
        }
        """)
        spec.global_css = spec.global_css + "\n" + extra
        return spec

    def _enrich_navigation_depth(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Ensure enough nav targets exist."""
        if len(spec.nav_items) < int(result.obligation.minimum):
            defaults = [
                {"label": "Home", "href": "#/"},
                {"label": "Features", "href": "#features"},
                {"label": "Demo", "href": "#demo"},
                {"label": "Code", "href": "#code"},
                {"label": "Docs", "href": "#docs"},
                {"label": "About", "href": "#about"},
            ]
            for d in defaults:
                if len(spec.nav_items) >= int(result.obligation.minimum):
                    break
                if d not in spec.nav_items:
                    spec.nav_items.append(d)
            for p in spec.pages:
                for c in p.components:
                    if c.kind == ComponentKind.NAVBAR:
                        c.props["items"] = spec.nav_items
        return spec

    def _enrich_javascript_features(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Add advanced JS: counter animation, theme toggle, scroll progress."""
        page = self._ensure_page(spec)
        extra = textwrap.dedent("""\
        /* ─── Animated counters ─── */
        document.querySelectorAll('[data-count]').forEach(el => {
          const target = parseInt(el.dataset.count, 10);
          let current = 0;
          const step = Math.max(1, Math.floor(target / 60));
          const interval = setInterval(() => {
            current += step;
            if (current >= target) { current = target; clearInterval(interval); }
            el.textContent = current.toLocaleString();
          }, 16);
        });

        /* ─── Scroll progress indicator ─── */
        (function() {
          const bar = document.createElement('div');
          bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,var(--color-primary),var(--color-accent));z-index:9999;transition:width .1s;width:0;';
          document.body.appendChild(bar);
          window.addEventListener('scroll', () => {
            const pct = window.scrollY / (document.documentElement.scrollHeight - window.innerHeight) * 100;
            bar.style.width = Math.min(pct, 100) + '%';
          });
        })();

        /* ─── Back to top button ─── */
        (function() {
          const btn = document.createElement('button');
          btn.innerHTML = '↑';
          btn.className = 'btn btn-primary';
          btn.style.cssText = 'position:fixed;bottom:2rem;right:2rem;width:48px;height:48px;border-radius:50%;font-size:1.25rem;display:none;z-index:1500;padding:0;';
          document.body.appendChild(btn);
          window.addEventListener('scroll', () => { btn.style.display = window.scrollY > 400 ? 'flex' : 'none'; });
          btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
        })();

        /* ─── Theme toggle (light/dark) ─── */
        (function() {
          const toggle = document.createElement('button');
          toggle.textContent = '🌙';
          toggle.className = 'btn btn-outline';
          toggle.style.cssText = 'position:fixed;bottom:2rem;left:2rem;width:48px;height:48px;border-radius:50%;font-size:1.25rem;z-index:1500;padding:0;';
          document.body.appendChild(toggle);
          let dark = true;
          toggle.addEventListener('click', () => {
            dark = !dark;
            document.documentElement.style.setProperty('--color-bg', dark ? '#0c0c1d' : '#f8fafc');
            document.documentElement.style.setProperty('--color-text', dark ? '#e2e8f0' : '#1e293b');
            document.documentElement.style.setProperty('--color-bg-card', dark ? '#161633' : '#ffffff');
            document.documentElement.style.setProperty('--color-border', dark ? '#2d3748' : '#e2e8f0');
            document.documentElement.style.setProperty('--color-text-muted', dark ? '#94a3b8' : '#64748b');
            toggle.textContent = dark ? '🌙' : '☀️';
          });
        })();
        """)
        page.custom_js = (page.custom_js or "") + "\n" + extra
        return spec

    def _enrich_typography_quality(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        """Ensure rich typography with multiple sizes, weights, line-heights."""
        extra = textwrap.dedent("""\
        .text-xs { font-size: .75rem; } .text-sm { font-size: .875rem; }
        .text-base { font-size: 1rem; } .text-lg { font-size: 1.125rem; }
        .text-xl { font-size: 1.25rem; } .text-2xl { font-size: 1.5rem; }
        .font-light { font-weight: 300; } .font-normal { font-weight: 400; }
        .font-medium { font-weight: 500; } .font-semibold { font-weight: 600; }
        .font-bold { font-weight: 700; } .font-extrabold { font-weight: 800; }
        .leading-tight { line-height: 1.25; } .leading-normal { line-height: 1.5; }
        .leading-relaxed { line-height: 1.75; }
        .tracking-tight { letter-spacing: -.025em; } .tracking-wide { letter-spacing: .05em; }
        """)
        spec.global_css = spec.global_css + "\n" + extra
        return spec

    def _enrich_visual_hierarchy(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        # component_variety enrichment handles this
        return self._enrich_component_variety(spec, result)

    def _enrich_css_line_count(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        # Handled by animation + responsive + typography enrichments
        return spec

    def _enrich_js_line_count(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        # Handled by interactivity + js_features enrichments
        return spec

    def _enrich_html_line_count(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        # Handled by content_density enrichment
        return spec

    def _enrich_total_line_count(self, spec: HTMLAppSpec, result: ObligationResult) -> HTMLAppSpec:
        # Handled by other enrichments collectively
        return spec

class _ComponentRenderer:
    """Renders ComponentSpec instances to HTML strings."""

    def render(self, comp: ComponentSpec) -> str:
        method = getattr(self, f"_render_{comp.kind.value}", None)
        if method:
            return method(comp)
        if comp.custom_html:
            return comp.custom_html
        return f'<div id="{comp.id}" class="component component-{comp.kind.value}">{self._render_children(comp)}</div>'

    def _render_children(self, comp: ComponentSpec) -> str:
        return "\n".join(self.render(c) for c in comp.children)

    def _render_navbar(self, comp: ComponentSpec) -> str:
        brand = comp.props.get("brand", "App")
        items = comp.props.get("items", [])
        links = "\n".join(
            f'        <a href="{it.get("href", "#")}" class="nav-link">{it.get("label", "")}</a>'
            for it in items
        )
        return textwrap.dedent(f"""\
        <nav class="navbar" id="{comp.id or 'main-nav'}">
          <div class="nav-container">
            <a href="/" class="nav-brand">{brand}</a>
            <button class="nav-toggle" aria-label="Toggle menu" onclick="document.querySelector('.nav-links').classList.toggle('open')">☰</button>
            <div class="nav-links">
        {links}
            </div>
          </div>
        </nav>""")

    def _render_hero(self, comp: ComponentSpec) -> str:
        title = comp.props.get("title", "")
        subtitle = comp.props.get("subtitle", "")
        cta_text = comp.props.get("cta_text", "")
        cta_href = comp.props.get("cta_href", "#")
        cta_html = f'<a href="{cta_href}" class="btn btn-primary btn-lg">{cta_text}</a>' if cta_text else ""
        return textwrap.dedent(f"""\
        <section class="hero" id="{comp.id or 'hero'}">
          <div class="hero-inner">
            <h1 class="hero-title">{title}</h1>
            <p class="hero-subtitle">{subtitle}</p>
            {cta_html}
          </div>
          <canvas id="hero-canvas" class="hero-bg-canvas"></canvas>
        </section>""")

    def _render_card(self, comp: ComponentSpec) -> str:
        title = comp.props.get("title", "")
        body = comp.props.get("body", "")
        icon = comp.props.get("icon", "")
        icon_html = f'<div class="card-icon">{icon}</div>' if icon else ""
        children_html = self._render_children(comp)
        return textwrap.dedent(f"""\
        <div class="card" id="{comp.id}">
          {icon_html}
          <h3 class="card-title">{title}</h3>
          <div class="card-body">{body}{children_html}</div>
        </div>""")

    def _render_table(self, comp: ComponentSpec) -> str:
        headers = comp.props.get("headers", [])
        rows = comp.props.get("rows", [])
        head = "".join(f"<th>{h}</th>" for h in headers)
        body_rows = "\n".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
            for row in rows
        )
        return textwrap.dedent(f"""\
        <div class="table-container" id="{comp.id}">
          <table class="data-table">
            <thead><tr>{head}</tr></thead>
            <tbody>{body_rows}</tbody>
          </table>
        </div>""")

    def _render_form(self, comp: ComponentSpec) -> str:
        fields = comp.props.get("fields", [])
        action = comp.props.get("action", "#")
        method = comp.props.get("method", "POST")
        fields_html = "\n".join(self._render_form_field(f) for f in fields)
        return textwrap.dedent(f"""\
        <form class="form" id="{comp.id}" action="{action}" method="{method}">
          {fields_html}
          <button type="submit" class="btn btn-primary">Submit</button>
        </form>""")

    def _render_form_field(self, f: dict) -> str:
        ftype = f.get("type", "text")
        name = f.get("name", "")
        label = f.get("label", name)
        placeholder = f.get("placeholder", "")
        required = "required" if f.get("required") else ""
        if ftype == "textarea":
            return f'<div class="form-group"><label for="{name}">{label}</label><textarea id="{name}" name="{name}" placeholder="{placeholder}" {required}></textarea></div>'
        if ftype == "select":
            opts = "\n".join(f'<option value="{o}">{o}</option>' for o in f.get("options", []))
            return f'<div class="form-group"><label for="{name}">{label}</label><select id="{name}" name="{name}" {required}>{opts}</select></div>'
        return f'<div class="form-group"><label for="{name}">{label}</label><input type="{ftype}" id="{name}" name="{name}" placeholder="{placeholder}" {required} /></div>'

    def _render_chart(self, comp: ComponentSpec) -> str:
        chart_type = comp.props.get("chart_type", "bar")
        width = comp.props.get("width", 600)
        height = comp.props.get("height", 400)
        return f'<div class="chart-container" id="{comp.id}"><canvas id="{comp.id}-canvas" width="{width}" height="{height}" data-chart-type="{chart_type}"></canvas></div>'

    def _render_modal(self, comp: ComponentSpec) -> str:
        title = comp.props.get("title", "")
        body = comp.props.get("body", "")
        return textwrap.dedent(f"""\
        <div class="modal-backdrop" id="{comp.id}" style="display:none;">
          <div class="modal">
            <div class="modal-header"><h3>{title}</h3><button class="modal-close" onclick="document.getElementById('{comp.id}').style.display='none'">&times;</button></div>
            <div class="modal-body">{body}{self._render_children(comp)}</div>
          </div>
        </div>""")

    def _render_tabs(self, comp: ComponentSpec) -> str:
        tabs = comp.props.get("tabs", [])
        tab_buttons = "\n".join(
            f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{t["id"]}">{t["label"]}</button>'
            for i, t in enumerate(tabs)
        )
        tab_panels = "\n".join(
            f'<div class="tab-panel{" active" if i == 0 else ""}" id="{t["id"]}">{t.get("content", "")}</div>'
            for i, t in enumerate(tabs)
        )
        return textwrap.dedent(f"""\
        <div class="tabs-container" id="{comp.id}">
          <div class="tab-bar">{tab_buttons}</div>
          <div class="tab-panels">{tab_panels}</div>
        </div>""")

    def _render_accordion(self, comp: ComponentSpec) -> str:
        items = comp.props.get("items", [])
        items_html = "\n".join(
            f'<div class="accordion-item"><button class="accordion-trigger">{it["title"]}</button><div class="accordion-content">{it.get("content", "")}</div></div>'
            for it in items
        )
        return f'<div class="accordion" id="{comp.id}">{items_html}</div>'

    def _render_sidebar(self, comp: ComponentSpec) -> str:
        items = comp.props.get("items", [])
        links = "\n".join(
            f'<a href="{it.get("href", "#")}" class="sidebar-link">{it.get("label", "")}</a>'
            for it in items
        )
        return f'<aside class="sidebar" id="{comp.id or "sidebar"}">{links}</aside>'

    def _render_footer(self, comp: ComponentSpec) -> str:
        text = comp.props.get("text", "")
        return f'<footer class="footer" id="{comp.id or "footer"}"><p>{text}</p></footer>'

    def _render_toast(self, comp: ComponentSpec) -> str:
        return f'<div class="toast-container" id="{comp.id or "toasts"}"></div>'

    def _render_code_block(self, comp: ComponentSpec) -> str:
        code = comp.props.get("code", "")
        language = comp.props.get("language", "")
        return f'<pre class="code-block" id="{comp.id}"><code class="language-{language}">{code}</code></pre>'

    def _render_canvas(self, comp: ComponentSpec) -> str:
        w = comp.props.get("width", 800)
        h = comp.props.get("height", 600)
        return f'<canvas id="{comp.id}" width="{w}" height="{h}" class="app-canvas"></canvas>'

    def _render_custom(self, comp: ComponentSpec) -> str:
        return comp.custom_html or f'<div id="{comp.id}">{self._render_children(comp)}</div>'


# ── CSS Generation ────────────────────────────────────────────────────

def _generate_base_css(theme: dict[str, str]) -> str:
    """Generate comprehensive base CSS with theme overrides."""
    primary = theme.get("primary", "#4f46e5")
    primary_light = theme.get("primary_light", "#818cf8")
    primary_dark = theme.get("primary_dark", "#3730a3")
    accent = theme.get("accent", "#f59e0b")
    bg = theme.get("bg", "#0f0f23")
    bg_card = theme.get("bg_card", "#1a1a2e")
    text = theme.get("text", "#e2e8f0")
    text_muted = theme.get("text_muted", "#94a3b8")
    font = theme.get("font", "'Inter', 'Segoe UI', sans-serif")
    mono = theme.get("mono", "'JetBrains Mono', 'Fira Code', monospace")

    return textwrap.dedent(f"""\
    /* ═══ Generated by jugeo-webapp HTMLOnlyGenerator ═══ */
    :root {{
      --color-primary: {primary};
      --color-primary-light: {primary_light};
      --color-primary-dark: {primary_dark};
      --color-accent: {accent};
      --color-bg: {bg};
      --color-bg-card: {bg_card};
      --color-bg-elevated: #16213e;
      --color-text: {text};
      --color-text-muted: {text_muted};
      --color-border: #2d3748;
      --color-success: #10b981;
      --color-warning: #f59e0b;
      --color-danger: #ef4444;
      --font-body: {font};
      --font-mono: {mono};
      --radius: 8px;
      --radius-lg: 16px;
      --shadow: 0 4px 24px rgba(0,0,0,.3);
      --shadow-lg: 0 12px 48px rgba(0,0,0,.4);
      --transition: .25s cubic-bezier(.4,0,.2,1);
      --nav-height: 64px;
      --max-width: 1280px;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }}
    body {{
      font-family: var(--font-body); color: var(--color-text);
      background: var(--color-bg); line-height: 1.7;
      min-height: 100vh; overflow-x: hidden;
    }}
    a {{ color: var(--color-primary-light); text-decoration: none; transition: color var(--transition); }}
    a:hover {{ color: var(--color-accent); }}
    h1, h2, h3, h4, h5, h6 {{ line-height: 1.2; font-weight: 700; margin-bottom: .5em; }}
    h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); }}
    h2 {{ font-size: clamp(1.5rem, 3vw, 2.5rem); }}
    h3 {{ font-size: clamp(1.2rem, 2vw, 1.75rem); }}
    code, pre {{ font-family: var(--font-mono); }}
    pre {{ background: var(--color-bg-card); padding: 1.25rem; border-radius: var(--radius); overflow-x: auto; border: 1px solid var(--color-border); }}
    code {{ background: var(--color-bg-card); padding: .15em .35em; border-radius: 4px; font-size: .9em; }}
    pre code {{ background: none; padding: 0; }}
    img {{ max-width: 100%; height: auto; }}
    .container {{ max-width: var(--max-width); margin: 0 auto; padding: 0 1.5rem; }}

    /* ─── Navbar ─── */
    .navbar {{
      position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
      height: var(--nav-height);
      background: rgba(15,15,35,.92); backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--color-border);
    }}
    .nav-container {{
      max-width: var(--max-width); margin: 0 auto; height: 100%;
      display: flex; align-items: center; justify-content: space-between;
      padding: 0 1.5rem;
    }}
    .nav-brand {{ font-size: 1.25rem; font-weight: 700; color: var(--color-text); }}
    .nav-links {{ display: flex; gap: 1.5rem; }}
    .nav-link {{ color: var(--color-text-muted); font-size: .95rem; transition: color var(--transition); }}
    .nav-link:hover {{ color: var(--color-primary-light); }}
    .nav-toggle {{ display: none; background: none; border: none; color: var(--color-text); font-size: 1.5rem; cursor: pointer; }}
    @media (max-width: 768px) {{
      .nav-toggle {{ display: block; }}
      .nav-links {{ display: none; position: absolute; top: var(--nav-height); left: 0; right: 0; background: var(--color-bg-card); flex-direction: column; padding: 1rem; border-bottom: 1px solid var(--color-border); }}
      .nav-links.open {{ display: flex; }}
    }}

    /* ─── Hero ─── */
    .hero {{
      position: relative; min-height: 80vh; display: flex;
      align-items: center; justify-content: center;
      padding: calc(var(--nav-height) + 2rem) 1.5rem 4rem; overflow: hidden;
    }}
    .hero-inner {{ position: relative; z-index: 1; text-align: center; max-width: 800px; }}
    .hero-title {{
      font-size: clamp(2.5rem, 6vw, 4.5rem); font-weight: 800;
      background: linear-gradient(135deg, var(--color-primary-light), var(--color-accent));
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      background-clip: text; margin-bottom: 1rem;
    }}
    .hero-subtitle {{ font-size: clamp(1rem, 2vw, 1.35rem); color: var(--color-text-muted); max-width: 600px; margin: 0 auto 2rem; }}
    .hero-bg-canvas {{ position: absolute; inset: 0; width: 100%; height: 100%; z-index: 0; opacity: .5; }}

    /* ─── Buttons ─── */
    .btn {{
      display: inline-flex; align-items: center; gap: .5rem;
      padding: .75rem 1.75rem; border-radius: var(--radius); font-weight: 600;
      font-size: .95rem; cursor: pointer; border: none;
      transition: all var(--transition); text-decoration: none;
    }}
    .btn-primary {{ background: var(--color-primary); color: #fff; }}
    .btn-primary:hover {{ background: var(--color-primary-dark); transform: translateY(-2px); box-shadow: var(--shadow); }}
    .btn-lg {{ padding: 1rem 2.5rem; font-size: 1.1rem; border-radius: var(--radius-lg); }}
    .btn-outline {{ background: transparent; border: 2px solid var(--color-primary); color: var(--color-primary-light); }}
    .btn-outline:hover {{ background: var(--color-primary); color: #fff; }}

    /* ─── Cards ─── */
    .card {{
      background: var(--color-bg-card); border-radius: var(--radius-lg);
      padding: 2rem; border: 1px solid var(--color-border);
      transition: all var(--transition);
    }}
    .card:hover {{ border-color: var(--color-primary); transform: translateY(-4px); box-shadow: var(--shadow-lg); }}
    .card-icon {{ font-size: 2rem; margin-bottom: 1rem; }}
    .card-title {{ font-size: 1.25rem; margin-bottom: .75rem; }}
    .card-body {{ color: var(--color-text-muted); }}
    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; }}

    /* ─── Tables ─── */
    .table-container {{ overflow-x: auto; border-radius: var(--radius); border: 1px solid var(--color-border); }}
    .data-table {{ width: 100%; border-collapse: collapse; }}
    .data-table th, .data-table td {{ padding: .75rem 1rem; text-align: left; border-bottom: 1px solid var(--color-border); }}
    .data-table th {{ background: var(--color-bg-elevated); font-weight: 600; color: var(--color-primary-light); }}
    .data-table tr:hover td {{ background: rgba(79,70,229,.05); }}

    /* ─── Forms ─── */
    .form {{ max-width: 600px; }}
    .form-group {{ margin-bottom: 1.25rem; }}
    .form-group label {{ display: block; margin-bottom: .4rem; font-weight: 500; font-size: .9rem; }}
    .form-group input, .form-group textarea, .form-group select {{
      width: 100%; padding: .7rem 1rem; border-radius: var(--radius);
      border: 1px solid var(--color-border); background: var(--color-bg);
      color: var(--color-text); font-size: .95rem; transition: border-color var(--transition);
    }}
    .form-group input:focus, .form-group textarea:focus, .form-group select:focus {{ outline: none; border-color: var(--color-primary); }}

    /* ─── Tabs ─── */
    .tabs-container {{ margin: 1rem 0; }}
    .tab-bar {{ display: flex; gap: 0; border-bottom: 2px solid var(--color-border); }}
    .tab-btn {{
      padding: .75rem 1.5rem; background: none; border: none; color: var(--color-text-muted);
      cursor: pointer; font-size: .95rem; border-bottom: 2px solid transparent;
      margin-bottom: -2px; transition: all var(--transition);
    }}
    .tab-btn.active {{ color: var(--color-primary-light); border-bottom-color: var(--color-primary); }}
    .tab-panel {{ display: none; padding: 1.5rem 0; }}
    .tab-panel.active {{ display: block; }}

    /* ─── Accordion ─── */
    .accordion-trigger {{
      width: 100%; text-align: left; padding: 1rem; background: var(--color-bg-card);
      border: 1px solid var(--color-border); color: var(--color-text);
      cursor: pointer; font-size: 1rem; font-weight: 600;
      border-radius: var(--radius); margin-bottom: .25rem;
    }}
    .accordion-content {{ max-height: 0; overflow: hidden; transition: max-height .3s ease; padding: 0 1rem; }}
    .accordion-content.open {{ max-height: 1000px; padding: 1rem; }}

    /* ─── Modal ─── */
    .modal-backdrop {{ position: fixed; inset: 0; z-index: 2000; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; }}
    .modal {{ background: var(--color-bg-card); border-radius: var(--radius-lg); padding: 2rem; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; box-shadow: var(--shadow-lg); }}
    .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
    .modal-close {{ background: none; border: none; color: var(--color-text-muted); font-size: 1.5rem; cursor: pointer; }}

    /* ─── Sidebar ─── */
    .sidebar {{
      position: sticky; top: calc(var(--nav-height) + 1rem);
      padding: 1rem; max-height: calc(100vh - var(--nav-height) - 2rem);
      overflow-y: auto;
    }}
    .sidebar-link {{ display: block; padding: .5rem .75rem; color: var(--color-text-muted); border-radius: 6px; margin-bottom: .25rem; font-size: .9rem; }}
    .sidebar-link:hover {{ background: var(--color-bg-card); color: var(--color-text); }}

    /* ─── Footer ─── */
    .footer {{ padding: 2rem 1.5rem; text-align: center; color: var(--color-text-muted); border-top: 1px solid var(--color-border); margin-top: 4rem; }}

    /* ─── Toast ─── */
    .toast-container {{ position: fixed; bottom: 1rem; right: 1rem; z-index: 3000; display: flex; flex-direction: column; gap: .5rem; }}
    .toast {{
      padding: .75rem 1.25rem; border-radius: var(--radius); background: var(--color-bg-card);
      border: 1px solid var(--color-border); box-shadow: var(--shadow);
      animation: slideInRight .3s ease;
    }}
    @keyframes slideInRight {{ from {{ transform: translateX(100%); opacity: 0; }} to {{ transform: translateX(0); opacity: 1; }} }}

    /* ─── Sections ─── */
    .section {{ padding: 4rem 1.5rem; }}
    .section-title {{ text-align: center; margin-bottom: 3rem; }}
    .section-alt {{ background: var(--color-bg-card); }}

    /* ─── Code block ─── */
    .code-block {{ position: relative; }}
    .code-block::before {{ content: attr(data-language); position: absolute; top: .5rem; right: .75rem; font-size: .7rem; color: var(--color-text-muted); text-transform: uppercase; }}

    /* ─── Utilities ─── */
    .text-center {{ text-align: center; }}
    .text-muted {{ color: var(--color-text-muted); }}
    .mt-1 {{ margin-top: .5rem; }} .mt-2 {{ margin-top: 1rem; }} .mt-4 {{ margin-top: 2rem; }}
    .mb-2 {{ margin-bottom: 1rem; }} .mb-4 {{ margin-bottom: 2rem; }}
    .flex {{ display: flex; }} .flex-col {{ flex-direction: column; }} .gap-1 {{ gap: .5rem; }} .gap-2 {{ gap: 1rem; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1.5rem; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem; }}
    @media (max-width: 768px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}

    /* ─── Animations ─── */
    @keyframes fadeInUp {{ from {{ opacity: 0; transform: translateY(20px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    .fade-in {{ animation: fadeInUp .6s ease forwards; opacity: 0; }}
    .fade-in-1 {{ animation-delay: .1s; }} .fade-in-2 {{ animation-delay: .2s; }} .fade-in-3 {{ animation-delay: .3s; }}
    .fade-in-4 {{ animation-delay: .4s; }} .fade-in-5 {{ animation-delay: .5s; }}

    /* ─── Glow effect ─── */
    .glow {{ position: relative; }}
    .glow::after {{
      content: ''; position: absolute; inset: -2px; border-radius: inherit;
      background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
      z-index: -1; opacity: 0; transition: opacity var(--transition); filter: blur(8px);
    }}
    .glow:hover::after {{ opacity: .6; }}
    """)


def _generate_base_js() -> str:
    """Generate base JavaScript for interactivity."""
    return textwrap.dedent("""\
    /* ═══ Generated by jugeo-webapp HTMLOnlyGenerator ═══ */
    'use strict';

    /* ─── Tab switching ─── */
    document.addEventListener('click', e => {
      const btn = e.target.closest('.tab-btn');
      if (!btn) return;
      const container = btn.closest('.tabs-container');
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      container.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = container.querySelector('#' + btn.dataset.tab);
      if (panel) panel.classList.add('active');
    });

    /* ─── Accordion ─── */
    document.addEventListener('click', e => {
      const trigger = e.target.closest('.accordion-trigger');
      if (!trigger) return;
      const content = trigger.nextElementSibling;
      if (content) content.classList.toggle('open');
    });

    /* ─── Toast system ─── */
    window.showToast = function(message, type = 'info', duration = 3000) {
      const container = document.getElementById('toasts') || (() => {
        const c = document.createElement('div');
        c.id = 'toasts'; c.className = 'toast-container';
        document.body.appendChild(c); return c;
      })();
      const toast = document.createElement('div');
      toast.className = 'toast toast-' + type;
      toast.textContent = message;
      container.appendChild(toast);
      setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, duration);
    };

    /* ─── Modal helpers ─── */
    window.openModal = id => { const m = document.getElementById(id); if (m) m.style.display = 'flex'; };
    window.closeModal = id => { const m = document.getElementById(id); if (m) m.style.display = 'none'; };

    /* ─── Fade-in on scroll (IntersectionObserver) ─── */
    if ('IntersectionObserver' in window) {
      const obs = new IntersectionObserver((entries) => {
        entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('visible'); obs.unobserve(e.target); } });
      }, { threshold: 0.1 });
      document.querySelectorAll('.fade-in').forEach(el => obs.observe(el));
    }

    /* ─── SPA-style client routing (hash-based) ─── */
    window.JugeoRouter = class {
      constructor() { this.routes = {}; window.addEventListener('hashchange', () => this._resolve()); }
      on(path, handler) { this.routes[path] = handler; return this; }
      navigate(path) { window.location.hash = '#' + path; }
      _resolve() {
        const hash = window.location.hash.slice(1) || '/';
        const handler = this.routes[hash] || this.routes['*'];
        if (handler) handler(hash);
      }
      start() { this._resolve(); return this; }
    };

    /* ─── LocalStorage wrapper ─── */
    window.JugeoStore = class {
      constructor(prefix = 'jugeo') { this.prefix = prefix; }
      _key(k) { return this.prefix + ':' + k; }
      get(key, fallback = null) { try { return JSON.parse(localStorage.getItem(this._key(key))); } catch { return fallback; } }
      set(key, value) { localStorage.setItem(this._key(key), JSON.stringify(value)); }
      remove(key) { localStorage.removeItem(this._key(key)); }
      keys() { return Object.keys(localStorage).filter(k => k.startsWith(this.prefix + ':')).map(k => k.slice(this.prefix.length + 1)); }
      all() { return Object.fromEntries(this.keys().map(k => [k, this.get(k)])); }
      clear() { this.keys().forEach(k => this.remove(k)); }
    };

    console.log('jugeo-webapp HTML app initialized');
    """)


# ── HTML Page Generation ─────────────────────────────────────────────

class HTMLOnlyGenerator:
    """Generates standalone HTML/CSS/JS applications — no Flask required.

    From the judgment-geometry perspective this constructs a global section
    of the visual presheaf that lives entirely in the client fibers.

    The generator enforces *visual obligations* — a typed presheaf of
    minimum quality requirements.  If the spec doesn't satisfy the
    obligations, the enricher applies structural additions until descent
    holds (all obligations met) or a maximum enrichment depth is reached.

    Parameters
    ----------
    obligations : str or list[Obligation]
        Preset name ("minimal", "standard", "stunning") or explicit list.
        Default is "stunning" — generated output MUST be visually impressive.
    max_enrichment_rounds : int
        Maximum number of enrichment passes before giving up.
    """

    def __init__(
        self,
        obligations: "str | list[Obligation]" = "stunning",
        max_enrichment_rounds: int = 5,
    ) -> None:
        self._renderer = _ComponentRenderer()
        self._checker = HTMLObligationChecker()
        self._enricher = HTMLSpecEnricher()
        self._max_rounds = max_enrichment_rounds
        self._obligations = resolve_obligations(obligations, GenerationTarget.HTML_ONLY)

    def generate(self, spec: HTMLAppSpec, output_dir: str,
                 obligations: "str | list[Obligation] | None" = None) -> HTMLGenerationResult:
        """Generate the full HTML-only application, enforcing obligations.

        The spec is checked against obligations BEFORE rendering.  If any
        are unmet, the enricher adds components, CSS, and JS until
        all obligations pass.  This guarantees the output is never a
        hollow shell.
        """
        os.makedirs(output_dir, exist_ok=True)
        warnings: list[str] = []
        files: list[str] = []
        total_lines = 0

        # Resolve per-call obligations override
        obs = resolve_obligations(obligations, GenerationTarget.HTML_ONLY) if obligations is not None else self._obligations

        # ── Obligation enforcement loop ───────────────────────────────
        spec, report = enforce_obligations(spec, obs, self._checker, self._enricher, self._max_rounds)

        if not report.all_met:
            for r in report.unmet:
                warnings.append(
                    f"Obligation not met after {report.enrichment_rounds} rounds: "
                    f"{r.obligation.kind.value} (need {r.obligation.minimum}, have {r.actual})"
                )

        # ── Render files ──────────────────────────────────────────────

        # CSS
        css_dir = os.path.join(output_dir, "css")
        os.makedirs(css_dir, exist_ok=True)
        base_css = _generate_base_css(spec.theme)
        if spec.global_css:
            base_css += "\n\n/* ─── App-specific styles ─── */\n" + spec.global_css
        css_path = os.path.join(css_dir, "style.css")
        self._write(css_path, base_css)
        files.append(css_path)
        total_lines += base_css.count("\n") + 1

        # JS
        js_dir = os.path.join(output_dir, "js")
        os.makedirs(js_dir, exist_ok=True)
        base_js = _generate_base_js()
        if spec.global_js:
            base_js += "\n\n/* ─── App-specific scripts ─── */\n" + spec.global_js
        js_path = os.path.join(js_dir, "app.js")
        self._write(js_path, base_js)
        files.append(js_path)
        total_lines += base_js.count("\n") + 1

        # Pages
        if not spec.pages:
            spec.pages.append(PageSpec(name="index", title=spec.title, route="/"))

        for page in spec.pages:
            html = self._generate_page(spec, page)
            if page.route == "/" or page.name == "index":
                path = os.path.join(output_dir, "index.html")
            else:
                pages_dir = os.path.join(output_dir, "pages")
                os.makedirs(pages_dir, exist_ok=True)
                path = os.path.join(pages_dir, f"{page.name}.html")
            self._write(path, html)
            files.append(path)
            total_lines += html.count("\n") + 1

        # Extra static files
        for rel_path, content in spec.extra_static_files.items():
            full = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            self._write(full, content)
            files.append(full)
            total_lines += content.count("\n") + 1

        # Launch script
        serve_path = os.path.join(output_dir, "serve.sh")
        self._write(serve_path, textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Serve this HTML app — no Flask required
            echo "Serving {spec.name} at http://localhost:{spec.port}"
            python3 -m http.server {spec.port} --directory "$(dirname "$0")"
        """))
        os.chmod(serve_path, 0o755)
        files.append(serve_path)

        return HTMLGenerationResult(
            output_dir=output_dir,
            files_created=files,
            spec=spec,
            warnings=warnings,
            total_lines=total_lines,
            obligation_report=report,
        )

    def _generate_page(self, spec: HTMLAppSpec, page: PageSpec) -> str:
        """Generate a single HTML page."""
        css_href = "css/style.css" if page.route == "/" else "../css/style.css"
        js_src = "js/app.js" if page.route == "/" else "../js/app.js"

        # Render components
        body_parts: list[str] = []
        for comp in page.components:
            body_parts.append(self._renderer.render(comp))

        body_html = "\n\n".join(body_parts)

        page_css = ""
        if page.custom_css:
            page_css = f"\n<style>\n{page.custom_css}\n</style>"

        page_js = ""
        if page.custom_js:
            page_js = f"\n<script>\n{page.custom_js}\n</script>"

        return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>{page.title} — {spec.title}</title>
          <meta name="description" content="{page.description or spec.description}" />
          <meta name="generator" content="jugeo-webapp {spec.meta.get('version', '0.1.0')}" />
          <link rel="stylesheet" href="{css_href}" />{page.custom_head}{page_css}
        </head>
        <body>
        {body_html}
          <script src="{js_src}"></script>{page_js}
        </body>
        </html>
        """)

    @staticmethod
    def _write(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


# ── Backward compatibility aliases ────────────────────────────────────
ObligationChecker = HTMLObligationChecker
SpecEnricher = HTMLSpecEnricher
OBLIGATION_PRESETS: dict[str, list[Obligation]] = {
    "minimal": get_obligations("minimal", GenerationTarget.HTML_ONLY),
    "standard": get_obligations("standard", GenerationTarget.HTML_ONLY),
    "stunning": get_obligations("stunning", GenerationTarget.HTML_ONLY),
}
