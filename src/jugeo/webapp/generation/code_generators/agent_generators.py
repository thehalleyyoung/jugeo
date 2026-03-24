"""Agent-driven code generation — uses real AI agents to generate code.

Instead of hand-written template generators, this module dispatches to
the jugeo agent channel (copilot → claude → codex → sdk) to generate
JavaScript, CSS, and HTML for each concept.  From the JG perspective,
each agent call is an *evidence channel* producing local sections at
coordinates on the Code surface, carrying COPILOT_SUGGESTED trust.

The obligation presheaf still governs descent: if the AI output falls
short on line count, feature density, or interactivity, the enricher
makes additional agent calls to repair obstructions.

Usage (from prompt_driver.py)::

    from .code_generators.agent_generators import agent_generate_concept

    js, css, html = agent_generate_concept(
        concept_name="game_engine",
        app_prompt="a unique app mixing gaming and artistic generation",
        concept_params={"complexity": "high"},
        scale=5,
        working_dir="/tmp/my-app",
    )
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Optional

log = logging.getLogger(__name__)

try:
    from jugeo.research_orchestration import SurfaceKind
    from jugeo.directed_research._agent_channel import (
        agent_call,
        agent_file_write,
        available_agents,
        LLMSection,
    )
    HAS_AGENT_CHANNEL = True
except ImportError:
    HAS_AGENT_CHANNEL = False
    log.warning("Agent channel not available — agent generation disabled")


# ═══════════════════════════════════════════════════════════════════════
#  Concept → prompt mapping — the JG fiber decomposition
# ═══════════════════════════════════════════════════════════════════════

# Each concept maps to a description of what the AI should generate.
# The prompts are structured to produce COMPLETE, WORKING code — no stubs.
# Scale determines target line counts per concept.

CONCEPT_PROMPTS: dict[str, dict[str, str]] = {
    # ── Game concepts ─────────────────────────────────────────────
    "game_engine": {
        "js_desc": (
            "A complete game engine with: game loop (requestAnimationFrame), "
            "entity-component system, collision detection (AABB + spatial hash), "
            "state machine (menu/playing/paused/gameover), input handling "
            "(keyboard + mouse + touch), scene management, and a debug overlay. "
            "Must be fully self-contained in an IIFE."
        ),
        "target_per_scale": "300",
    },
    "territory": {
        "js_desc": (
            "A hex-grid territory system: hex coordinate math (cube coords, "
            "neighbors, distance, rings, spiral), territory ownership/claiming, "
            "influence propagation (BFS flood fill with decay), border detection, "
            "contested region computation, territory merging/splitting, minimap "
            "data generation, and territory serialization."
        ),
        "target_per_scale": "300",
    },
    "combat": {
        "js_desc": (
            "A turn-based combat system: unit stats (hp, attack, defense, speed), "
            "damage formulas with variance, initiative queue, ability system with "
            "cooldowns, status effects (poison, stun, buff, shield), XP/leveling, "
            "combat log, and AI opponent decision tree."
        ),
        "target_per_scale": "250",
    },
    "ai_opponent": {
        "js_desc": (
            "AI opponent with: minimax with alpha-beta pruning (depth 4+), "
            "board evaluation heuristics, opening book, endgame strategy, "
            "Monte Carlo tree search (MCTS) variant, difficulty levels, "
            "move history analysis, and a thinking indicator UI."
        ),
        "target_per_scale": "250",
    },
    "scoring": {
        "js_desc": (
            "Scoring and achievement system: combo multipliers, streak tracking, "
            "high score persistence (localStorage), leaderboard with sorting, "
            "achievement definitions with unlock conditions, progress bars, "
            "statistics tracking (games played, time, best combos), and "
            "score animation/popups."
        ),
        "target_per_scale": "200",
    },
    # ── Art concepts ──────────────────────────────────────────────
    "noise": {
        "js_desc": (
            "Procedural noise library: Perlin noise (2D/3D), simplex noise, "
            "fractal Brownian motion (fBm) with octaves, Worley/cellular noise, "
            "value noise, ridged multi-fractal, turbulence, domain warping, "
            "and noise-to-color mapping with customizable palettes."
        ),
        "target_per_scale": "300",
    },
    "color_theory": {
        "js_desc": (
            "Color theory engine: HSL/RGB/HSV/LAB/LCH color space conversions, "
            "complementary/triadic/tetradic/split-complementary harmonies, "
            "perceptual color difference (CIEDE2000), palette generation "
            "(analogous, monochromatic, warm/cool), gradient interpolation "
            "(linear, bezier, smooth step), and a color picker widget."
        ),
        "target_per_scale": "250",
    },
    "fractal": {
        "js_desc": (
            "Fractal renderer: Mandelbrot set with smooth coloring, Julia sets "
            "with parameter animation, Burning Ship fractal, Newton's method "
            "fractal, Barnsley fern (IFS), Sierpinski triangle/carpet, "
            "Koch snowflake, dragon curve, zoom controls with deep precision, "
            "and palette cycling animation."
        ),
        "target_per_scale": "300",
    },
    "lsystem": {
        "js_desc": (
            "L-system renderer: axiom/rule parser, turtle graphics interpreter "
            "with stack, stochastic L-systems, parametric L-systems, "
            "preset plants (fern, bush, tree, flower, seaweed), "
            "growth animation over time, branching angle/length controls, "
            "and SVG export."
        ),
        "target_per_scale": "250",
    },
    "particle": {
        "js_desc": (
            "Particle system: emitter with position/velocity/acceleration, "
            "forces (gravity, wind, attract/repel, turbulence), size/color "
            "curves over lifetime, blend modes, trail rendering, spawn shapes "
            "(point, circle, rect, ring), burst and continuous modes, "
            "sub-emitters, and particle pooling for performance."
        ),
        "target_per_scale": "300",
    },
    "cellular": {
        "js_desc": (
            "Cellular automata engine: Conway's Life, rule strings (B/S notation), "
            "Wolfram elementary automata (all 256 rules), Langton's ant, "
            "multiple cell states (Brian's Brain, Wireworld), wraparound and "
            "bounded grids, pattern library (gliders, guns, oscillators), "
            "step/run/pause controls, and grid resize."
        ),
        "target_per_scale": "250",
    },
    "composition": {
        "js_desc": (
            "Art composition engine: rule of thirds grid overlay, golden ratio "
            "spiral, visual weight distribution analysis, focal point detection, "
            "canvas layout manager (split, overlap, layer), blend modes "
            "(multiply, screen, overlay, soft light), mask system, "
            "save/export to PNG, and composition presets."
        ),
        "target_per_scale": "200",
    },
    # ── UI / infrastructure concepts ──────────────────────────────
    "canvas_renderer": {
        "js_desc": (
            "Multi-layer canvas rendering system: layer management (terrain, "
            "territory, effects, ui), camera with pan/zoom/smooth follow, "
            "hex grid rendering with flat-top/pointy-top, sprite batch renderer, "
            "text rendering with shadow, render loop with delta time, "
            "FPS counter, dirty rect optimization, and fullscreen toggle."
        ),
        "target_per_scale": "300",
    },
    "ui_system": {
        "js_desc": (
            "UI panel management system: draggable/resizable panels, modal "
            "dialogs with backdrop, toast notifications (info/success/warning/error), "
            "tabbed content switcher, context menu, tooltips, keyboard shortcuts "
            "manager, settings panel with range/toggle/select inputs, "
            "and responsive sidebar collapse."
        ),
        "target_per_scale": "250",
    },
    "gallery": {
        "js_desc": (
            "Art gallery system: canvas snapshot capture (toDataURL), thumbnail "
            "grid with masonry layout, lightbox viewer with zoom, filter by "
            "tag/date/type, sort (newest/oldest/name), search, selection mode "
            "with multi-select, batch export (zip via JSZip or sequential), "
            "empty state placeholder, and infinite scroll or pagination."
        ),
        "target_per_scale": "200",
    },
    "tutorial": {
        "js_desc": (
            "Interactive tutorial system: step-by-step walkthrough, spotlight "
            "overlay highlighting target elements, tooltip positioning (auto "
            "flip to stay on-screen), progress indicator, skip/back/next "
            "navigation, completion tracking in localStorage, tutorial "
            "definitions as JSON data, and restart capability."
        ),
        "target_per_scale": "200",
    },
    "audio_synth": {
        "js_desc": (
            "Web Audio API synthesizer: oscillator types (sine, square, "
            "sawtooth, triangle, custom), ADSR envelope, filter (low/high/band "
            "pass with resonance), delay effect, reverb (convolver), distortion, "
            "LFO modulation, note frequency table (MIDI to Hz), polyphony "
            "manager, and master volume with visualizer (analyser node → canvas)."
        ),
        "target_per_scale": "250",
    },
    "generative_music": {
        "js_desc": (
            "Procedural music generator: Markov chain melody generator, "
            "chord progression engine (I-IV-V-vi etc.), rhythm pattern sequencer, "
            "scale quantization (major, minor, pentatonic, blues, dorian), "
            "arpeggiator, drum pattern generator, song structure (intro/verse/"
            "chorus/bridge/outro), tempo/time-signature controls, and "
            "play/pause/next transport."
        ),
        "target_per_scale": "250",
    },
    "design_system": {
        "css_desc": (
            "Complete CSS design system: custom properties (colors, spacing, "
            "typography, shadows, borders, z-index), CSS reset/normalize, "
            "responsive grid (12-column), flex utilities, component styles "
            "(buttons with states, cards with hover, modals, forms, badges, "
            "progress bars, tabs, toggles, tooltips, dropdowns, toast, accordion, "
            "skeleton loading, avatars, chips), game-specific UI (canvas container, "
            "HUD overlay, action bar, palette panel, territory info, minimap, "
            "score display, resource bars, victory screen), and 20+ keyframe "
            "animations (fadeIn, slideUp, pulse, shake, glow, ripple, float, "
            "bounce, typing, gradient shift, confetti, flip, morph, orbit)."
        ),
        "target_per_scale": "200",
    },
}

# Scale multiplier: scale=1 → 1x, scale=3 → 2x, scale=5 → 3x
_SCALE_MULTIPLIERS = {1: 1.0, 2: 1.3, 3: 2.0, 4: 2.5, 5: 3.0}


def _target_lines(concept: str, scale: int) -> int:
    """Compute target line count for a concept at a given scale."""
    base = int(CONCEPT_PROMPTS.get(concept, {}).get("target_per_scale", "200"))
    mult = _SCALE_MULTIPLIERS.get(scale, 2.0)
    return int(base * mult)


def _extract_code_block(text: str, lang: str) -> str:
    """Extract a fenced code block for the given language from agent output."""
    # Try ```lang ... ```
    pattern = rf'```{lang}\s*\n(.*?)```'
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try bare ``` ... ```
    pattern = r'```\s*\n(.*?)```'
    m = re.search(pattern, text, re.DOTALL)
    if m:
        code = m.group(1).strip()
        if code:
            return code
    # If no fences, return the whole thing (agent just returned code)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════
#  Main entry point: generate code for one concept via agent
# ═══════════════════════════════════════════════════════════════════════

def agent_generate_concept(
    concept_name: str,
    app_prompt: str,
    concept_params: dict[str, Any] | None = None,
    scale: int = 3,
    working_dir: str | None = None,
) -> tuple[str, str, str]:
    """Generate JS/CSS/HTML for a concept by calling an AI agent.

    This is the agent-driven counterpart to generate_for_concept() in
    __init__.py. Instead of looking up a registered template generator,
    it dispatches to copilot/claude/codex to write real code.

    The concept's description from CONCEPT_PROMPTS is combined with the
    app prompt and scale requirements to produce a targeted agent prompt.

    Returns (js, css, html) — any component may be empty string.
    """
    if not HAS_AGENT_CHANNEL:
        log.warning("Agent channel unavailable for concept %s", concept_name)
        return ("", "", "")

    info = CONCEPT_PROMPTS.get(concept_name)
    if not info:
        log.info("No agent prompt defined for concept %s", concept_name)
        return ("", "", "")

    target = _target_lines(concept_name, scale)
    js, css, html = "", "", ""

    # Generate JS if concept has js_desc
    if "js_desc" in info:
        js = _agent_generate_js(concept_name, info["js_desc"], app_prompt,
                                target, scale, working_dir)

    # Generate CSS if concept has css_desc
    if "css_desc" in info:
        css = _agent_generate_css(concept_name, info["css_desc"], app_prompt,
                                  target, scale, working_dir)

    return (js, css, html)


def _agent_generate_js(
    concept: str,
    description: str,
    app_prompt: str,
    target_lines: int,
    scale: int,
    working_dir: str | None,
) -> str:
    """Have an AI agent generate JavaScript for a concept."""
    prompt = f"""\
Generate a COMPLETE, self-contained JavaScript module for a web application.

APPLICATION CONTEXT: {app_prompt}

CONCEPT: {concept}
WHAT TO BUILD: {description}

REQUIREMENTS:
- Write {target_lines}+ lines of real, working JavaScript
- Wrap everything in an IIFE: (function() {{ 'use strict'; ... }})();
- Export classes/functions to window.CT namespace: window.CT = window.CT || {{}};
- NO external dependencies — pure vanilla JS
- NO placeholders, NO "TODO", NO stubs — every function must have a real implementation
- Include detailed JSDoc comments on all classes and public methods
- Include error handling (try/catch where appropriate)
- Code must be immediately runnable in a browser

Write ONLY the JavaScript code. No explanations, no markdown fences."""

    section = agent_call(
        prompt,
        surface=SurfaceKind.CODE,
        coordinate=f"webapp.js.{concept}",
        working_dir=working_dir,
    )

    js = _extract_code_block(section.content, "javascript")
    if not js or len(js) < 50:
        js = _extract_code_block(section.content, "js")
    if not js or len(js) < 50:
        js = section.content.strip()

    # Ensure IIFE wrapper
    if js and not js.startswith("(function"):
        # Strip any leading non-code
        lines = js.split("\n")
        code_start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(("(function", "var ", "const ", "let ", "class ", "function ", "'use strict'")):
                code_start = i
                break
        js = "\n".join(lines[code_start:])
        if not js.startswith("(function"):
            js = "(function() {\n  'use strict';\n" + js + "\n})();"

    lines = js.count("\n") + 1
    log.info("Agent generated %d JS lines for %s (target: %d)", lines, concept, target_lines)
    return js


def _agent_generate_css(
    concept: str,
    description: str,
    app_prompt: str,
    target_lines: int,
    scale: int,
    working_dir: str | None,
) -> str:
    """Have an AI agent generate CSS for a concept."""
    prompt = f"""\
Generate a COMPLETE CSS design system/stylesheet for a web application.

APPLICATION CONTEXT: {app_prompt}

CONCEPT: {concept}
WHAT TO BUILD: {description}

REQUIREMENTS:
- Write {target_lines}+ lines of real, working CSS
- Use CSS custom properties (--var-name) for theming
- Include a CSS reset/normalize section
- Include responsive breakpoints (@media queries for 480px, 768px, 1024px, 1440px)
- NO CSS-in-JS, NO preprocessor syntax — pure CSS
- NO placeholders, NO stubs — every rule must have real properties
- Include detailed comments for each major section
- Dark theme by default with a polished, professional look
- Include @keyframes animations (at least 15 different animations)

Write ONLY the CSS code. No explanations, no markdown fences."""

    section = agent_call(
        prompt,
        surface=SurfaceKind.CODE,
        coordinate=f"webapp.css.{concept}",
        working_dir=working_dir,
    )

    css = _extract_code_block(section.content, "css")
    if not css or len(css) < 50:
        css = section.content.strip()

    lines = css.count("\n") + 1
    log.info("Agent generated %d CSS lines for %s (target: %d)", lines, concept, target_lines)
    return css


# ═══════════════════════════════════════════════════════════════════════
#  HTML generation via agent
# ═══════════════════════════════════════════════════════════════════════

def agent_generate_html(
    app_title: str,
    app_prompt: str,
    concepts: list[str],
    scale: int = 3,
    working_dir: str | None = None,
) -> str:
    """Have an AI agent generate the full HTML document shell."""
    if not HAS_AGENT_CHANNEL:
        return ""

    target = 300 * _SCALE_MULTIPLIERS.get(scale, 2.0)
    concept_list = ", ".join(concepts)

    prompt = f"""\
Generate a COMPLETE HTML5 document for a single-page web application.

APPLICATION: {app_title}
DESCRIPTION: {app_prompt}
FEATURES/CONCEPTS: {concept_list}

REQUIREMENTS:
- Write {int(target)}+ lines of semantic HTML5
- Include: navigation bar, hero section, main canvas area (with 4 layered canvases),
  game HUD overlay, action bar, sidebar panels, gallery section, tutorial section,
  settings section (with toggles/sliders), scores/leaderboard section, about section,
  footer, modal dialogs, toast container, loading screen
- Use ARIA attributes for accessibility
- Include data-* attributes for JS hooks
- Structure with meaningful class names matching the CSS design system
- NO inline styles, NO inline scripts — those come from separate files
- Include meta viewport, charset, and Open Graph tags
- Link to app.css and app.js (external files)

Write ONLY the HTML code. No explanations, no markdown fences."""

    section = agent_call(
        prompt,
        surface=SurfaceKind.CODE,
        coordinate="webapp.html.shell",
        working_dir=working_dir,
    )

    html = _extract_code_block(section.content, "html")
    if not html or len(html) < 50:
        html = section.content.strip()

    lines = html.count("\n") + 1
    log.info("Agent generated %d HTML lines for shell (target: %d)", lines, int(target))
    return html


# ═══════════════════════════════════════════════════════════════════════
#  Obligation-driven enrichment via agent
# ═══════════════════════════════════════════════════════════════════════

def agent_enrich_js(
    existing_js: str,
    gap_lines: int,
    app_prompt: str,
    unmet_obligations: list[str] | None = None,
    working_dir: str | None = None,
) -> str:
    """Have an AI agent generate additional JS to close obligation gaps.

    This is called when the obligation presheaf detects that the
    generated JS falls short of the target line count or feature density.
    The agent is told what's missing and asked to fill the gap.
    """
    if not HAS_AGENT_CHANNEL or gap_lines < 100:
        return existing_js

    obligations_text = ""
    if unmet_obligations:
        obligations_text = f"\nUNMET OBLIGATIONS: {', '.join(unmet_obligations)}"

    existing_summary = f"EXISTING CODE: {existing_js.count(chr(10))+1} lines with these systems: "
    # Extract what's already defined
    existing_classes = re.findall(r'class\s+(\w+)', existing_js)
    existing_summary += ", ".join(existing_classes[:20])

    prompt = f"""\
Generate ADDITIONAL JavaScript code to add to an existing web application.

APPLICATION: {app_prompt}
{existing_summary}
{obligations_text}

I need {gap_lines}+ MORE lines of JavaScript. Generate NEW systems that don't
duplicate what's already there. Ideas for what to add:
- Utility framework (event bus, logger, config manager, performance monitor)
- Math utilities (Vec2, matrix, easing functions, random distributions)
- Data structures (priority queue, spatial hash, ring buffer, object pool)
- Animation framework (tweens, springs, screen shake, particle effects)
- State persistence (save/load, undo/redo, autosave)
- Input system (gesture recognition, gamepad support, key rebinding)
- Audio manager (sound effects pool, volume control, spatial audio)
- Network simulation (fake multiplayer, replay recording, sync protocol)
- Debug tools (console overlay, performance graphs, state inspector)

REQUIREMENTS:
- Wrap in an IIFE with 'use strict'
- Export to window.CT namespace
- {gap_lines}+ lines of REAL, working code — no stubs
- Full JSDoc comments
- Error handling throughout

Write ONLY JavaScript code. No explanations, no markdown."""

    section = agent_call(
        prompt,
        surface=SurfaceKind.CODE,
        coordinate="webapp.js.enrichment",
        working_dir=working_dir,
    )

    additional_js = _extract_code_block(section.content, "javascript")
    if not additional_js or len(additional_js) < 50:
        additional_js = _extract_code_block(section.content, "js")
    if not additional_js or len(additional_js) < 50:
        additional_js = section.content.strip()

    if additional_js and len(additional_js) > 100:
        return existing_js + "\n\n// ═══ Agent enrichment ═══\n" + additional_js

    return existing_js


def agent_enrich_css(
    existing_css: str,
    gap_lines: int,
    app_prompt: str,
    working_dir: str | None = None,
) -> str:
    """Have an AI agent generate additional CSS to close obligation gaps."""
    if not HAS_AGENT_CHANNEL or gap_lines < 100:
        return existing_css

    prompt = f"""\
Generate ADDITIONAL CSS to add to an existing web application stylesheet.

APPLICATION: {app_prompt}
EXISTING CSS: {existing_css.count(chr(10))+1} lines already written.

I need {gap_lines}+ MORE lines of CSS. Generate NEW styles that complement
what's already there. Include:
- Additional component styles (cards, badges, tooltips, dropdowns, etc.)
- More @keyframes animations (at least 10 new ones)
- Dark/light theme variants
- Print styles
- Additional responsive breakpoints
- Game-specific UI (health bars, cooldown indicators, damage numbers, etc.)
- Micro-interactions (hover effects, focus styles, transitions)
- Utility classes (spacing, typography, display, flexbox helpers)

REQUIREMENTS:
- Pure CSS, no preprocessor syntax
- Use CSS custom properties for theming
- {gap_lines}+ lines of real CSS — no stubs
- Detailed section comments

Write ONLY CSS code. No explanations, no markdown."""

    section = agent_call(
        prompt,
        surface=SurfaceKind.CODE,
        coordinate="webapp.css.enrichment",
        working_dir=working_dir,
    )

    additional_css = _extract_code_block(section.content, "css")
    if not additional_css or len(additional_css) < 50:
        additional_css = section.content.strip()

    if additional_css and len(additional_css) > 100:
        return existing_css + "\n\n/* ═══ Agent enrichment ═══ */\n" + additional_css

    return existing_css
