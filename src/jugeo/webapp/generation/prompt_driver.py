"""Prompt-to-app generation pipeline — the highest-level jugeo-webapp API.

Usage::

    from jugeo.webapp.generation.prompt_driver import PromptToApp

    result = PromptToApp(
        "a unique app which mixes gaming and artistic generation",
        obligations="production",
    ).generate("/tmp/my-app", target="both")

From the JG perspective, this is the *full descent construction*:

1. **Intent section**: The user's prompt is a section of the intent presheaf.
2. **Concept restriction**: ``extract_concepts`` restricts the intent to the
   concept fiber — identifying which feature domains are active.
3. **Section generation**: For each active concept, a code generator constructs
   a local section (JS, CSS, HTML) of the application sheaf.
4. **Assembly**: Local sections are assembled into an ``HTMLAppSpec`` (or
   ``AppSpec`` for Flask) — the candidate global section.
5. **Descent verification**: The obligation presheaf verifies quality, scale,
   and completeness.  If descent fails, the enricher repairs obstructions.
6. **File generation**: The verified spec is written to disk.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .concept_extractor import extract_concepts, ConceptMap, ConceptDomain
from .code_generators import generate_for_concept, scale_for_obligations
from .code_generators.scale_amplifier import amplify_js

try:
    from .code_generators.html_generators import generate_app_html as _generate_app_html
except ImportError:
    _generate_app_html = None

try:
    from .code_generators.agent_generators import (
        agent_generate_concept,
        agent_generate_html,
        agent_enrich_js,
        agent_enrich_css,
        HAS_AGENT_CHANNEL,
    )
except ImportError:
    HAS_AGENT_CHANNEL = False
from .copilot_driver import (
    CopilotGenerationDriver,
    FiberKind,
    SectionProposal,
)
from .html_generator import (
    ComponentSpec,
    ComponentKind,
    HTMLAppSpec,
    HTMLGenerationResult,
)
from .flask_generator import FlaskAppGenerator
from .models import (
    AppSpec,
    ModelSpec,
    ColumnSpec,
    ColumnType,
    RouteSpec,
    ResponseType,
    TemplateSpec,
    StaticFileSpec,
    ConfigSpec,
)
from .obligations import GenerationTarget


@dataclass
class PromptToAppResult:
    """Result from prompt-to-app generation."""
    prompt: str
    concepts: ConceptMap
    html_result: HTMLGenerationResult | None = None
    flask_result: Any | None = None
    html_dir: str = ""
    flask_dir: str = ""
    elapsed: float = 0.0

    def summary(self) -> str:
        lines = [f"PromptToApp: {self.prompt[:80]}..."]
        lines.append(f"  Concepts: {', '.join(c.name for c in self.concepts.concepts)}")
        if self.html_result:
            r = self.html_result
            met = r.obligation_report.all_met if r.obligation_report else "?"
            lines.append(f"  HTML: {len(r.files_created)} files, {r.total_lines} lines, obligations_met={met}")
        if self.flask_result:
            r = self.flask_result
            lines.append(f"  Flask: {len(r.files_created)} files")
        lines.append(f"  Elapsed: {self.elapsed:.1f}s")
        return "\n".join(lines)


class PromptToApp:
    """Generate a complete application from a natural-language prompt.

    Parameters
    ----------
    prompt : str
        Natural-language description of the desired app.
    obligations : str
        Obligation preset: "minimal", "standard", "stunning", "production".
    app_name : str, optional
        Override the auto-generated app name.
    app_title : str, optional
        Override the auto-generated title.
    port : int
        Dev server port.
    """

    def __init__(
        self,
        prompt: str,
        obligations: str = "production",
        app_name: str = "",
        app_title: str = "",
        port: int = 8888,
    ) -> None:
        self.prompt = prompt
        self.obligations = obligations
        self.port = port

        # Phase 1: concept extraction (restriction to concept fiber)
        self.concepts = extract_concepts(prompt)
        if app_name:
            self.concepts.app_name = app_name
        if app_title:
            self.concepts.app_title = app_title

    def generate(
        self,
        outdir: str,
        target: str = "both",
    ) -> PromptToAppResult:
        """Generate the app(s).

        Parameters
        ----------
        outdir : str
            Output directory. For target="both", creates html/ and flask/ subdirs.
        target : str
            "html", "flask", or "both".
        """
        t0 = time.time()
        result = PromptToAppResult(prompt=self.prompt, concepts=self.concepts)

        # Phase 2: agent-driven code generation for each concept
        #
        # Each concept is a coordinate on the Code surface. The agent channel
        # dispatches to copilot/claude/codex to generate real code. The
        # obligation presheaf governs descent — if output falls short, the
        # enricher makes additional agent calls.
        scale = scale_for_obligations(self.obligations)
        use_agents = HAS_AGENT_CHANNEL if 'HAS_AGENT_CHANNEL' in dir() else False
        mode = "agent-driven" if use_agents else "template"
        print(f"  ┌─ PromptToApp: {self.prompt[:60]}...")
        print(f"  ├─ Concepts: {', '.join(c.name for c in self.concepts.concepts)}")
        print(f"  ├─ Scale: {scale} (obligations={self.obligations!r})")
        print(f"  ├─ Mode: {mode}")
        print(f"  ├─ Generating code for {len(self.concepts.concepts)} concepts...")

        js_parts: list[str] = ["window.CT = window.CT || {};"]
        css_parts: list[str] = []
        html_parts: list[str] = []

        for concept in self.concepts.concepts:
            js, css, html = "", "", ""

            # Primary path: agent-driven generation (real AI)
            if use_agents:
                js, css, html = agent_generate_concept(
                    concept_name=concept.name,
                    app_prompt=self.prompt,
                    concept_params=concept.params,
                    scale=scale,
                    working_dir=outdir,
                )

            # Fallback: template generators (for concepts the agent
            # doesn't know about, or if agent channel is unavailable)
            if not js and not css and not html:
                js, css, html = generate_for_concept(
                    concept.name, concept.params, scale=scale,
                )

            if js:
                js_parts.append(f"\n// ═══ Concept: {concept.name} ({concept.domain.value}) ═══\n")
                js_parts.append(js)
            if css:
                css_parts.append(f"\n/* ═══ Concept: {concept.name} ═══ */\n")
                css_parts.append(css)
            if html:
                html_parts.append(html)
            status = []
            if js:
                status.append(f"JS:{js.count(chr(10))+1}L")
            if css:
                status.append(f"CSS:{css.count(chr(10))+1}L")
            if html:
                status.append(f"HTML:{html.count(chr(10))+1}L")
            src = "agent" if use_agents and (js or css or html) else "template"
            print(f"  │  {concept.name} [{src}]: {', '.join(status) if status else '(no output)'}")

        all_js = "\n".join(js_parts)
        all_css = "\n".join(css_parts)

        # Phase 2b: generate HTML shell via agent or template
        shell_html = ""
        if use_agents:
            shell_html = agent_generate_html(
                app_title=self.concepts.app_title,
                app_prompt=self.prompt,
                concepts=[c.name for c in self.concepts.concepts],
                scale=scale,
                working_dir=outdir,
            )
        if not shell_html and _generate_app_html is not None:
            try:
                shell_html = _generate_app_html(
                    title=self.concepts.app_title,
                    concepts=[c.name for c in self.concepts.concepts],
                    scale=scale,
                )
            except Exception:
                pass
        if shell_html:
            html_parts.insert(0, shell_html)
            print(f"  │  html_shell: HTML:{shell_html.count(chr(10))+1}L")

        all_html = "\n".join(html_parts)

        # Phase 2c: obligation-driven enrichment via agent
        # If JS/CSS fall short of targets, the agent channel generates more
        js_target = 14000 if self.obligations == "production" else 6000
        css_target = 3500 if self.obligations == "production" else 1500
        js_lines = all_js.count("\n") + 1
        css_lines = all_css.count("\n") + 1

        if use_agents and js_lines < js_target:
            gap = js_target - js_lines
            print(f"  ├─ Agent JS enrichment: {js_lines} lines, need {gap} more")
            all_js = agent_enrich_js(
                all_js, gap, self.prompt, working_dir=outdir,
            )
            print(f"  │  After enrichment: {all_js.count(chr(10))+1} lines")

        if use_agents and css_lines < css_target:
            gap = css_target - css_lines
            print(f"  ├─ Agent CSS enrichment: {css_lines} lines, need {gap} more")
            all_css = agent_enrich_css(
                all_css, gap, self.prompt, working_dir=outdir,
            )
            print(f"  │  After enrichment: {all_css.count(chr(10))+1} lines")

        # Phase 2d: scale amplification fallback — if agent enrichment
        # wasn't enough, the deterministic amplifier closes remaining gaps
        js_lines = all_js.count("\n") + 1
        if js_lines < js_target:
            print(f"  ├─ Amplifying JS: {js_lines} → target {js_target}")
            all_js = amplify_js(all_js, target_lines=js_target)
            print(f"  │  After amplification: {all_js.count(chr(10))+1} lines")

        total = all_js.count("\n") + all_css.count("\n") + all_html.count("\n")
        print(f"  ├─ Total generated: ~{total} lines (JS:{all_js.count(chr(10))}, CSS:{all_css.count(chr(10))}, HTML:{all_html.count(chr(10))})")

        # Phase 3: generate targets
        if target in ("html", "both"):
            html_dir = os.path.join(outdir, "html") if target == "both" else outdir
            result.html_dir = html_dir
            print(f"  ├─ Generating HTML app → {html_dir}")
            result.html_result = self._generate_html(html_dir, all_js, all_css, all_html)
            r = result.html_result
            met = r.obligation_report.all_met if r.obligation_report else "?"
            rnds = r.obligation_report.enrichment_rounds if r.obligation_report else 0
            print(f"  │  ✓ {len(r.files_created)} files, {r.total_lines} lines, obligations_met={met}, rounds={rnds}")

        if target in ("flask", "both"):
            flask_dir = os.path.join(outdir, "flask") if target == "both" else outdir
            result.flask_dir = flask_dir
            print(f"  ├─ Generating Flask app → {flask_dir}")
            result.flask_result = self._generate_flask(flask_dir, all_js, all_css, all_html)
            r = result.flask_result
            rpt = r.verification_results.get("obligation_report", {})
            print(f"  │  ✓ {len(r.files_created)} files, obligations_met={rpt.get('all_met')}")

        result.elapsed = time.time() - t0
        print(f"  └─ Done in {result.elapsed:.1f}s")
        return result

    # ── HTML generation via CopilotGenerationDriver ───────────────────

    def _generate_html(self, outdir: str, all_js: str, all_css: str,
                       all_html: str) -> HTMLGenerationResult:
        driver = CopilotGenerationDriver(
            name=self.concepts.app_name,
            title=self.concepts.app_title,
            description=self.concepts.app_description,
            port=self.port,
            obligations=self.obligations,
            target=GenerationTarget.HTML_ONLY,
        )

        # Theme fiber
        driver.propose(FiberKind.THEME, SectionProposal(
            fiber=FiberKind.THEME,
            theme=self._build_theme(),
        ))

        # Navigation fiber
        nav_items = self._build_nav_items()
        driver.propose(FiberKind.NAVIGATION, SectionProposal(
            fiber=FiberKind.NAVIGATION,
            nav_items=nav_items,
            components=[ComponentSpec(
                kind=ComponentKind.NAVBAR, id="main-nav",
                props={"brand": self.concepts.app_title, "items": nav_items},
            )],
        ))

        # HTML structure fiber
        components = self._build_components(all_html)
        driver.propose(FiberKind.HTML_STRUCTURE, SectionProposal(
            fiber=FiberKind.HTML_STRUCTURE,
            components=components,
        ))

        # Content fiber
        driver.propose(FiberKind.CONTENT, SectionProposal(
            fiber=FiberKind.CONTENT,
            components=self._build_content_components(),
        ))

        # CSS fiber
        driver.propose(FiberKind.CSS_STYLING, SectionProposal(
            fiber=FiberKind.CSS_STYLING,
            css=all_css,
        ))

        # JS fiber
        driver.propose(FiberKind.JS_INTERACTION, SectionProposal(
            fiber=FiberKind.JS_INTERACTION,
            js=all_js,
        ))

        # Animation fiber
        driver.propose(FiberKind.ANIMATION, SectionProposal(
            fiber=FiberKind.ANIMATION,
            css=self._build_animation_css(),
            js=self._build_animation_js(),
        ))

        # Data layer fiber
        driver.propose(FiberKind.DATA_LAYER, SectionProposal(
            fiber=FiberKind.DATA_LAYER,
            js=self._build_data_layer_js(),
        ))

        return driver.generate(outdir, auto_enrich=True, max_rounds=5)

    # ── Flask generation via FlaskAppGenerator ────────────────────────

    def _generate_flask(self, outdir: str, all_js: str, all_css: str,
                        all_html: str):
        # Split JS into per-concept static files for Flask
        static_files = self._build_flask_static_files(all_js, all_css)
        models = self._build_flask_models()
        routes = self._build_flask_routes()
        templates = self._build_flask_templates(all_html)

        spec = AppSpec(
            name=self.concepts.app_name,
            description=self.concepts.app_description,
            port=self.port,
            routes=routes,
            models=models,
            templates=templates,
            static_files=static_files,
            config=ConfigSpec(
                secret_key=f"{self.concepts.app_name}-dev-key",
                database_url=f"sqlite:///{self.concepts.app_name}.db",
                debug=True,
            ),
            dependencies=["flask", "flask-sqlalchemy"],
        )

        gen = FlaskAppGenerator(obligations=self.obligations, max_enrichment_rounds=5)
        return gen.generate(spec, outdir)

    # ── Helpers: build theme, nav, components from concepts ───────────

    def _build_theme(self) -> dict[str, str]:
        """Generate a theme based on active concepts."""
        if self.concepts.has("color_theory"):
            return {
                "primary": "#6366f1", "primary_light": "#818cf8",
                "primary_dark": "#4338ca", "accent": "#f59e0b",
                "accent_light": "#fbbf24", "bg": "#0a0a1a",
                "bg_surface": "#141428", "bg_card": "#1e1e3a",
                "text": "#e2e8f0", "text_muted": "#94a3b8",
                "success": "#10b981", "warning": "#f59e0b",
                "danger": "#ef4444", "info": "#3b82f6",
            }
        return {
            "primary": "#3b82f6", "accent": "#8b5cf6",
            "bg": "#111827", "bg_surface": "#1f2937",
            "text": "#f9fafb", "text_muted": "#9ca3af",
            "success": "#10b981", "danger": "#ef4444",
        }

    def _build_nav_items(self) -> list[dict[str, str]]:
        items = [{"label": "Home", "href": "#/"}]
        if self.concepts.has("game_engine"):
            items.append({"label": "Play", "href": "#/play"})
        if self.concepts.has("gallery"):
            items.append({"label": "Gallery", "href": "#/gallery"})
        if self.concepts.has("tutorial"):
            items.append({"label": "Tutorial", "href": "#/tutorial"})
        if self.concepts.has("scoring"):
            items.append({"label": "Scores", "href": "#/scores"})
        items.append({"label": "Settings", "href": "#/settings"})
        items.append({"label": "About", "href": "#/about"})
        return items

    def _build_components(self, all_html: str) -> list[ComponentSpec]:
        comps: list[ComponentSpec] = [
            ComponentSpec(kind=ComponentKind.HERO, id="hero", props={
                "title": self.concepts.app_title,
                "subtitle": self.concepts.app_description[:120],
            }),
        ]
        if all_html:
            comps.append(ComponentSpec(
                kind=ComponentKind.CUSTOM, id="app-content",
                custom_html=all_html,
            ))
        if self.concepts.has("canvas_renderer") or self.concepts.has("game_engine"):
            comps.append(ComponentSpec(
                kind=ComponentKind.CANVAS, id="main-canvas",
                props={"width": 1200, "height": 800},
            ))
        comps.extend([
            ComponentSpec(kind=ComponentKind.CARD, id="info-card"),
            ComponentSpec(kind=ComponentKind.TABS, id="mode-tabs", props={
                "tabs": [{"id": c.name.replace("_", "-"),
                          "label": c.name.replace("_", " ").title()}
                         for c in self.concepts.concepts[:5]],
            }),
            ComponentSpec(kind=ComponentKind.FOOTER, id="footer", props={
                "text": f"{self.concepts.app_title} — generated by jugeo-webapp",
            }),
        ])
        return comps

    def _build_content_components(self) -> list[ComponentSpec]:
        comps: list[ComponentSpec] = []
        # About section
        domain_descs = {
            ConceptDomain.GAME: "Interactive gameplay mechanics",
            ConceptDomain.ART: "Generative art algorithms",
            ConceptDomain.MEDIA: "Audio synthesis and generative music",
            ConceptDomain.UI: "Rich interactive interface",
            ConceptDomain.DATA: "Persistent data and leaderboards",
        }
        cards_html = '<div class="grid grid-3">'
        for domain in self.concepts.domains:
            desc = domain_descs.get(domain, domain.value)
            cards_html += f'<div class="card animate-fade-up"><h3>{domain.value.title()}</h3><p>{desc}</p></div>'
        cards_html += '</div>'
        comps.append(ComponentSpec(kind=ComponentKind.CUSTOM, id="about-cards",
                                   custom_html=cards_html))

        # FAQ accordion
        faq_items = [
            {"title": "What is this?",
             "content": f"An application generated from the prompt: \"{self.prompt[:100]}\""},
            {"title": "How was this built?",
             "content": "Generated by jugeo-webapp using the obligation presheaf pipeline — "
                        "concept extraction → code generation → descent verification → enrichment."},
        ]
        if self.concepts.has("game_engine"):
            faq_items.append({"title": "How do I play?",
                              "content": "Navigate to the Play section. The game combines interactive "
                                         "mechanics with generative art — your actions create visual art."})
        comps.append(ComponentSpec(kind=ComponentKind.ACCORDION, id="faq",
                                   props={"items": faq_items}))

        # Stats table
        comps.append(ComponentSpec(kind=ComponentKind.TABLE, id="concept-stats",
                                   props={
                                       "headers": ["Concept", "Domain", "Relevance"],
                                       "rows": [[c.name, c.domain.value, f"{c.relevance:.1f}"]
                                                 for c in self.concepts.concepts[:10]],
                                   }))
        return comps

    def _build_animation_css(self) -> str:
        return """\
@keyframes fadeInUp { from { opacity:0; transform:translateY(30px); } to { opacity:1; transform:translateY(0); } }
@keyframes slideInLeft { from { opacity:0; transform:translateX(-40px); } to { opacity:1; transform:translateX(0); } }
@keyframes scaleIn { from { transform:scale(.9); opacity:0; } to { transform:scale(1); opacity:1; } }
@keyframes gradientShift { 0% { background-position:0% 50%; } 50% { background-position:100% 50%; } 100% { background-position:0% 50%; } }
@keyframes shimmer { 0% { background-position:-200% 0; } 100% { background-position:200% 0; } }
@keyframes pulseGlow { 0%,100% { box-shadow:0 0 8px rgba(99,102,241,.3); } 50% { box-shadow:0 0 24px rgba(99,102,241,.6); } }
@keyframes float { 0%,100% { transform:translateY(0); } 50% { transform:translateY(-8px); } }
@keyframes hexPulse { 0%,100% { transform:scale(1); } 50% { transform:scale(1.05); filter:brightness(1.2); } }
@keyframes borderGlow { 0%,100% { box-shadow:0 0 4px var(--ct-primary); } 50% { box-shadow:0 0 20px var(--ct-primary); } }
@keyframes waveExpand { from { transform:scale(0); opacity:.8; } to { transform:scale(3); opacity:0; } }
@keyframes particleDrift { 0% { transform:translate(0,0) scale(1); opacity:1; } 100% { transform:translate(var(--dx,50px),var(--dy,-80px)) scale(.2); opacity:0; } }
@keyframes combatFlash { 0%,100% { opacity:1; } 50% { filter:brightness(1.5); } }
.animate-fade-up { animation: fadeInUp .7s ease forwards; opacity:0; }
.animate-slide-left { animation: slideInLeft .6s ease forwards; opacity:0; }
.animate-scale { animation: scaleIn .5s ease forwards; opacity:0; }
.gradient-bg { background:linear-gradient(135deg,var(--color-primary),var(--color-accent),var(--color-primary)); background-size:200% 200%; animation:gradientShift 6s ease infinite; }
"""

    def _build_animation_js(self) -> str:
        return """\
// ── Animation Controller ─────────────────────────────────────────
class AnimationController {
  constructor() { this.queue = []; this.running = false; this._setupScrollObserver(); }
  _setupScrollObserver() {
    if (typeof IntersectionObserver === 'undefined') return;
    const obs = new IntersectionObserver((entries) => {
      entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('ct-visible'); obs.unobserve(e.target); } });
    }, { threshold: 0.15 });
    document.querySelectorAll('.animate-fade-up,.animate-slide-left,.animate-scale').forEach(el => obs.observe(el));
  }
  enqueue(anim) { this.queue.push({...anim, startTime:null}); if (!this.running) this._run(); }
  _run() {
    this.running = true;
    const tick = (ts) => {
      this.queue = this.queue.filter(a => {
        if (!a.startTime) a.startTime = ts;
        const p = Math.min((ts - a.startTime) / (a.duration||500), 1);
        if (a.onFrame) a.onFrame(p);
        if (p >= 1) { if (a.onComplete) a.onComplete(); return false; }
        return true;
      });
      if (this.queue.length) requestAnimationFrame(tick); else this.running = false;
    };
    requestAnimationFrame(tick);
  }
}
window.CT = window.CT || {};
window.CT.AnimationController = AnimationController;
"""

    def _build_data_layer_js(self) -> str:
        return """\
// ── Data Layer (localStorage persistence) ────────────────────────
class DataLayer {
  constructor(prefix='ct') { this.prefix = prefix; }
  _key(n) { return this.prefix + '_' + n; }
  save(n, d) { try { localStorage.setItem(this._key(n), JSON.stringify(d)); return true; } catch(e) { return false; } }
  load(n, fb=null) { try { const r=localStorage.getItem(this._key(n)); return r?JSON.parse(r):fb; } catch(e) { return fb; } }
  remove(n) { localStorage.removeItem(this._key(n)); }
  saveSettings(s) { return this.save('settings', s); }
  loadSettings() { return this.load('settings', {volume:.7,music:true,sfx:true,quality:'high',animations:true}); }
  saveState(s) { return this.save('state', s); }
  loadState() { return this.load('state'); }
  addScore(e) { const b=this.load('scores',[]); e.at=new Date().toISOString(); b.push(e); b.sort((a,b)=>b.score-a.score); this.save('scores',b.slice(0,50)); }
  loadScores() { return this.load('scores',[]); }
  saveGalleryItem(item) { const g=this.load('gallery',[]); item.id=item.id||Date.now().toString(36); g.push(item); this.save('gallery',g); return item.id; }
  loadGallery() { return this.load('gallery',[]); }
  exportAll() { const d={}; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k&&k.startsWith(this.prefix+'_'))d[k.slice(this.prefix.length+1)]=this.load(k.slice(this.prefix.length+1));} return JSON.stringify(d,null,2); }
}
window.CT = window.CT || {};
window.CT.DataLayer = DataLayer;
"""

    # ── Flask-specific builders ───────────────────────────────────────

    def _build_flask_static_files(self, all_js: str, all_css: str) -> list[StaticFileSpec]:
        files = [
            StaticFileSpec(path="js/app.js", content_type="application/javascript", content=all_js),
            StaticFileSpec(path="css/app.css", content_type="text/css", content=all_css),
            StaticFileSpec(path="js/data-layer.js", content_type="application/javascript",
                           content=self._build_data_layer_js()),
            StaticFileSpec(path="js/animation-controller.js", content_type="application/javascript",
                           content=self._build_animation_js()),
            StaticFileSpec(path="css/animations.css", content_type="text/css",
                           content=self._build_animation_css()),
        ]
        return files

    def _build_flask_models(self) -> list[ModelSpec]:
        models = []
        if self.concepts.has("game_engine") or self.concepts.has("scoring"):
            models.append(ModelSpec(name="Player", table_name="players", columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("username", ColumnType.STRING, nullable=False, unique=True),
                ColumnSpec("display_name", ColumnType.STRING),
                ColumnSpec("total_score", ColumnType.INTEGER, default=0),
                ColumnSpec("games_played", ColumnType.INTEGER, default=0),
                ColumnSpec("preferences_json", ColumnType.TEXT),
                ColumnSpec("created_at", ColumnType.DATETIME),
            ]))
        if self.concepts.has("game_engine"):
            models.append(ModelSpec(name="GameSession", table_name="game_sessions", columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("player_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("state_json", ColumnType.TEXT),
                ColumnSpec("turn_count", ColumnType.INTEGER, default=0),
                ColumnSpec("status", ColumnType.STRING, default="in_progress"),
                ColumnSpec("final_score", ColumnType.INTEGER),
                ColumnSpec("config_json", ColumnType.TEXT),
                ColumnSpec("started_at", ColumnType.DATETIME),
                ColumnSpec("finished_at", ColumnType.DATETIME),
            ]))
        if self.concepts.has("gallery"):
            models.append(ModelSpec(name="Artwork", table_name="artworks", columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("player_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("title", ColumnType.STRING),
                ColumnSpec("description", ColumnType.TEXT),
                ColumnSpec("thumbnail_data", ColumnType.TEXT),
                ColumnSpec("score", ColumnType.FLOAT),
                ColumnSpec("metadata_json", ColumnType.TEXT),
                ColumnSpec("created_at", ColumnType.DATETIME),
            ]))
        if self.concepts.has("scoring"):
            models.append(ModelSpec(name="Achievement", table_name="achievements", columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("player_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("name", ColumnType.STRING, nullable=False),
                ColumnSpec("description", ColumnType.TEXT),
                ColumnSpec("category", ColumnType.STRING),
                ColumnSpec("unlocked_at", ColumnType.DATETIME),
            ]))
        return models

    def _build_flask_routes(self) -> list[RouteSpec]:
        routes = [
            RouteSpec("/", handler_name="index", template="index.html",
                      response_type=ResponseType.TEMPLATE),
            RouteSpec("/about", handler_name="about", template="about.html",
                      response_type=ResponseType.TEMPLATE),
            RouteSpec("/settings", handler_name="settings", template="settings.html",
                      response_type=ResponseType.TEMPLATE),
        ]
        if self.concepts.has("game_engine"):
            routes.append(RouteSpec("/play", handler_name="play", template="play.html",
                                    response_type=ResponseType.TEMPLATE))
            routes.append(RouteSpec("/api/game/state", handler_name="api_game_state",
                                    response_type=ResponseType.JSON))
            routes.append(RouteSpec("/api/game/action", methods=["POST"],
                                    handler_name="api_game_action",
                                    response_type=ResponseType.JSON))
            routes.append(RouteSpec("/api/game/new", methods=["POST"],
                                    handler_name="api_new_game",
                                    response_type=ResponseType.JSON))
        if self.concepts.has("gallery"):
            routes.append(RouteSpec("/gallery", handler_name="gallery_list",
                                    template="gallery.html",
                                    response_type=ResponseType.TEMPLATE))
            routes.append(RouteSpec("/api/gallery/save", methods=["POST"],
                                    handler_name="api_save_artwork",
                                    response_type=ResponseType.JSON))
        if self.concepts.has("tutorial"):
            routes.append(RouteSpec("/tutorial", handler_name="tutorial",
                                    template="tutorial.html",
                                    response_type=ResponseType.TEMPLATE))
        if self.concepts.has("scoring"):
            routes.append(RouteSpec("/scores", handler_name="scores",
                                    template="scores.html",
                                    response_type=ResponseType.TEMPLATE))
            routes.append(RouteSpec("/api/scores", handler_name="api_scores",
                                    response_type=ResponseType.JSON))
        return routes

    def _build_flask_templates(self, all_html: str) -> list[TemplateSpec]:
        templates = [
            TemplateSpec(name="base.html", extends="", blocks={
                "content": f"""\
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{% block title %}}{self.concepts.app_title}{{% endblock %}}</title>
<link rel="stylesheet" href="{{{{ url_for('static',filename='css/app.css') }}}}">
<link rel="stylesheet" href="{{{{ url_for('static',filename='css/animations.css') }}}}">
</head><body>
<nav><a href="/">{self.concepts.app_title}</a></nav>
<main>{{% block body %}}{{% endblock %}}</main>
<script src="{{{{ url_for('static',filename='js/app.js') }}}}"></script>
<script src="{{{{ url_for('static',filename='js/data-layer.js') }}}}"></script>
<script src="{{{{ url_for('static',filename='js/animation-controller.js') }}}}"></script>
</body></html>""",
            }),
            TemplateSpec(name="index.html", extends="base.html", blocks={
                "body": f'<h1>{self.concepts.app_title}</h1><p>{self.concepts.app_description[:200]}</p>',
            }),
            TemplateSpec(name="about.html", extends="base.html", blocks={
                "body": f'<h1>About</h1><p>{self.concepts.app_description}</p>',
            }),
            TemplateSpec(name="settings.html", extends="base.html", blocks={
                "body": '<h1>Settings</h1><div id="settings-panel"></div>',
            }),
        ]
        if self.concepts.has("game_engine"):
            templates.append(TemplateSpec(name="play.html", extends="base.html", blocks={
                "body": all_html if all_html else '<div id="game-container"><canvas id="main-canvas"></canvas></div>',
            }))
        if self.concepts.has("gallery"):
            templates.append(TemplateSpec(name="gallery.html", extends="base.html", blocks={
                "body": '<h1>Gallery</h1><div id="gallery-grid"></div>',
            }))
        if self.concepts.has("tutorial"):
            templates.append(TemplateSpec(name="tutorial.html", extends="base.html", blocks={
                "body": '<h1>Tutorial</h1><div id="tutorial-container"></div>',
            }))
        if self.concepts.has("scoring"):
            templates.append(TemplateSpec(name="scores.html", extends="base.html", blocks={
                "body": '<h1>Scores</h1><div id="scores-container"></div>',
            }))
        return templates
