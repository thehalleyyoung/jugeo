"""CSS as a presheaf on the DOM selector site.

Rules are local sections.  The cascade is the descent mechanism — when
multiple rules apply to the same element, specificity determines which
section "wins."  Responsive design is gluing across the device site:
media queries are covering families, and the CSS must restrict
consistently to each viewport.  Obstructions are CSS bugs: missing
styles, broken layouts, specificity conflicts.

This module formalises CSS generation so that every component receives a
complete, responsive, accessible stylesheet derived from the design
token system (palette, typography, spacing, breakpoints) rather than
from ad-hoc string literals scattered across generators.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

__all__ = [
    "CSSPropertyDomain",
    "ComponentStyleKind",
    "CSSDescentObligation",
    "CSSReset",
    "CSSVariableSystem",
    "CSSLayoutGenerator",
    "CSSComponentGenerator",
    "CSSAnimationGenerator",
    "CSSResponsiveGenerator",
    "CSSTheoryGenerator",
    "CSSDescentChecker",
]


# ── Enumerations ──────────────────────────────────────────────────────


class CSSPropertyDomain(Enum):
    """Domains of CSS concern — fibres of the CSS presheaf."""

    LAYOUT = auto()
    BOX_MODEL = auto()
    TYPOGRAPHY = auto()
    COLOR = auto()
    BACKGROUND = auto()
    BORDER = auto()
    SHADOW = auto()
    TRANSFORM = auto()
    TRANSITION = auto()
    ANIMATION = auto()
    FILTER = auto()
    GRID = auto()
    FLEXBOX = auto()
    POSITION = auto()
    OVERFLOW = auto()
    VISIBILITY = auto()
    CURSOR = auto()
    POINTER_EVENTS = auto()
    USER_SELECT = auto()
    SCROLL = auto()
    PRINT = auto()


class ComponentStyleKind(Enum):
    """Component types that require styling — objects of the view site."""

    NAVBAR = auto()
    HERO = auto()
    CARD = auto()
    BUTTON = auto()
    BUTTON_PRIMARY = auto()
    BUTTON_OUTLINE = auto()
    BUTTON_GHOST = auto()
    MODAL = auto()
    TOAST = auto()
    TABS = auto()
    TAB_PANEL = auto()
    ACCORDION = auto()
    FORM_INPUT = auto()
    FORM_SELECT = auto()
    FORM_TEXTAREA = auto()
    FORM_CHECKBOX = auto()
    FORM_TOGGLE = auto()
    TABLE = auto()
    BADGE = auto()
    PROGRESS = auto()
    TOOLTIP = auto()
    DROPDOWN = auto()
    SIDEBAR = auto()
    HUD = auto()
    FOOTER = auto()
    SKELETON = auto()
    AVATAR = auto()
    CHIP = auto()
    BREADCRUMB = auto()
    PAGINATION = auto()
    ALERT = auto()
    SPINNER = auto()
    DIVIDER = auto()
    CODE_BLOCK = auto()
    BLOCKQUOTE = auto()
    LIST = auto()
    GRID_LAYOUT = auto()
    CANVAS_CONTAINER = auto()
    LOADING_SCREEN = auto()
    EMPTY_STATE = auto()
    ERROR_STATE = auto()


# ── Descent obligation ────────────────────────────────────────────────


@dataclass
class CSSDescentObligation:
    """What CSS must satisfy for a given component.

    Each obligation is a section of the CSS presheaf that must be
    defined over the corresponding selector open set.
    """

    component: ComponentStyleKind
    required_properties: list[str] = field(default_factory=list)
    responsive: bool = False
    interactive_states: list[str] = field(default_factory=list)
    dark_theme: bool = False
    reduced_motion: bool = False


# ── Default theme tokens ──────────────────────────────────────────────

_DEFAULT_PALETTE: dict[str, str] = {
    "primary": "#4f46e5",
    "primary_light": "#818cf8",
    "primary_dark": "#3730a3",
    "accent": "#f59e0b",
    "bg": "#0f0f23",
    "bg_card": "#1a1a2e",
    "bg_elevated": "#16213e",
    "text": "#e2e8f0",
    "text_muted": "#94a3b8",
    "border": "#2d3748",
    "success": "#10b981",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "info": "#3b82f6",
}

_DEFAULT_TYPOGRAPHY: dict[str, str] = {
    "font_body": "'Inter', 'Segoe UI', sans-serif",
    "font_mono": "'JetBrains Mono', 'Fira Code', monospace",
    "scale_xs": "0.75rem",
    "scale_sm": "0.875rem",
    "scale_base": "1rem",
    "scale_lg": "1.125rem",
    "scale_xl": "1.25rem",
    "scale_2xl": "1.5rem",
    "scale_3xl": "2rem",
    "scale_4xl": "2.5rem",
    "scale_5xl": "3.5rem",
    "line_height": "1.7",
    "heading_line_height": "1.2",
}

_DEFAULT_SPACING: dict[str, str] = {
    "unit": "0.25rem",
    "1": "0.25rem",
    "2": "0.5rem",
    "3": "0.75rem",
    "4": "1rem",
    "5": "1.25rem",
    "6": "1.5rem",
    "8": "2rem",
    "10": "2.5rem",
    "12": "3rem",
    "16": "4rem",
    "20": "5rem",
    "24": "6rem",
}

_DEFAULT_BREAKPOINTS: dict[str, int] = {
    "sm": 640,
    "md": 768,
    "lg": 1024,
    "xl": 1280,
    "2xl": 1440,
}


# ── CSS Reset ─────────────────────────────────────────────────────────


class CSSReset:
    """Modern CSS reset — the initial section of the CSS presheaf."""

    @staticmethod
    def generate() -> str:
        return textwrap.dedent("""\
        /* ═══ Reset ═══ */
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
        body {
          min-height: 100vh; overflow-x: hidden;
          -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
        }
        img, picture, video, canvas, svg { display: block; max-width: 100%; }
        input, button, textarea, select { font: inherit; }
        p, h1, h2, h3, h4, h5, h6 { overflow-wrap: break-word; }
        a { color: inherit; text-decoration: none; }
        ul, ol { list-style: none; }
        table { border-collapse: collapse; border-spacing: 0; }
        button { cursor: pointer; background: none; border: none; }
        [hidden] { display: none !important; }
        """)


# ── CSS Variable System ───────────────────────────────────────────────


class CSSVariableSystem:
    """Generate CSS custom properties — the global section of :root."""

    @staticmethod
    def from_palette(palette: dict[str, str] | None = None) -> str:
        p = {**_DEFAULT_PALETTE, **(palette or {})}
        lines = ["  /* Color tokens */"]
        for key, val in p.items():
            prop = key.replace("_", "-")
            lines.append(f"  --color-{prop}: {val};")
        return "\n".join(lines)

    @staticmethod
    def from_typography(scale: dict[str, str] | None = None) -> str:
        t = {**_DEFAULT_TYPOGRAPHY, **(scale or {})}
        lines = ["  /* Typography tokens */"]
        lines.append(f"  --font-body: {t['font_body']};")
        lines.append(f"  --font-mono: {t['font_mono']};")
        for key, val in t.items():
            if key.startswith("scale_"):
                name = key.replace("scale_", "")
                lines.append(f"  --font-size-{name}: {val};")
        lines.append(f"  --line-height: {t['line_height']};")
        lines.append(f"  --heading-line-height: {t['heading_line_height']};")
        return "\n".join(lines)

    @staticmethod
    def from_spacing(system: dict[str, str] | None = None) -> str:
        s = {**_DEFAULT_SPACING, **(system or {})}
        lines = ["  /* Spacing tokens */"]
        for key, val in s.items():
            lines.append(f"  --space-{key}: {val};")
        return "\n".join(lines)

    @staticmethod
    def generate_all(
        palette: dict[str, str] | None = None,
        typography: dict[str, str] | None = None,
        spacing: dict[str, str] | None = None,
        breakpoints: dict[str, int] | None = None,
    ) -> str:
        bp = {**_DEFAULT_BREAKPOINTS, **(breakpoints or {})}
        bp_lines = "  /* Breakpoint reference (used by media queries) */\n"
        for name, px in bp.items():
            bp_lines += f"  --bp-{name}: {px}px;\n"
        shape = textwrap.dedent("""\
          /* Shape tokens */
          --radius: 8px;
          --radius-sm: 4px;
          --radius-lg: 16px;
          --radius-full: 9999px;
          --shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
          --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.2);
          --shadow-lg: 0 12px 48px rgba(0, 0, 0, 0.4);
          --transition: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
          --transition-fast: 0.15s cubic-bezier(0.4, 0, 0.2, 1);
          --transition-slow: 0.4s cubic-bezier(0.4, 0, 0.2, 1);
          --nav-height: 64px;
          --max-width: 1280px;
          --z-dropdown: 100;
          --z-sticky: 200;
          --z-navbar: 1000;
          --z-modal: 2000;
          --z-toast: 3000;
          --z-loading: 9999;
        """)
        parts = [
            ":root {",
            CSSVariableSystem.from_palette(palette),
            CSSVariableSystem.from_typography(typography),
            CSSVariableSystem.from_spacing(spacing),
            bp_lines.rstrip(),
            shape.rstrip(),
            "}",
        ]
        return "\n".join(parts)


# ── Layout Generator ──────────────────────────────────────────────────


class CSSLayoutGenerator:
    """Layout utilities — the structural fibre of the CSS presheaf."""

    @staticmethod
    def generate_container() -> str:
        return textwrap.dedent("""\
        /* ═══ Container ═══ */
        .container { max-width: var(--max-width); margin: 0 auto; padding: 0 var(--space-6); }
        .container-sm { max-width: 640px; margin: 0 auto; padding: 0 var(--space-6); }
        .container-lg { max-width: 1440px; margin: 0 auto; padding: 0 var(--space-6); }
        .container-fluid { width: 100%; padding: 0 var(--space-6); }
        """)

    @staticmethod
    def generate_grid_system(columns: int = 12) -> str:
        lines = [
            "/* ═══ Grid system ═══ */",
            ".grid { display: grid; gap: var(--space-6); }",
        ]
        for i in range(1, columns + 1):
            lines.append(
                f".grid-cols-{i} {{ grid-template-columns: repeat({i}, 1fr); }}"
            )
        lines.append(
            ".grid-auto { display: grid; "
            "grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); "
            "gap: var(--space-6); }"
        )
        for i in range(1, columns + 1):
            lines.append(f".col-span-{i} {{ grid-column: span {i}; }}")
        return "\n".join(lines)

    @staticmethod
    def generate_flex_utilities() -> str:
        return textwrap.dedent("""\
        /* ═══ Flex utilities ═══ */
        .flex { display: flex; }
        .flex-col { flex-direction: column; }
        .flex-row { flex-direction: row; }
        .flex-wrap { flex-wrap: wrap; }
        .flex-nowrap { flex-wrap: nowrap; }
        .items-center { align-items: center; }
        .items-start { align-items: flex-start; }
        .items-end { align-items: flex-end; }
        .items-stretch { align-items: stretch; }
        .justify-center { justify-content: center; }
        .justify-between { justify-content: space-between; }
        .justify-around { justify-content: space-around; }
        .justify-start { justify-content: flex-start; }
        .justify-end { justify-content: flex-end; }
        .flex-1 { flex: 1 1 0%; }
        .flex-auto { flex: 1 1 auto; }
        .flex-none { flex: none; }
        .gap-1 { gap: var(--space-1); }
        .gap-2 { gap: var(--space-2); }
        .gap-3 { gap: var(--space-3); }
        .gap-4 { gap: var(--space-4); }
        .gap-6 { gap: var(--space-6); }
        .gap-8 { gap: var(--space-8); }
        """)

    @staticmethod
    def generate_spacing_utilities(base_unit: int = 4) -> str:
        steps = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24]
        lines = ["/* ═══ Spacing utilities ═══ */"]
        for s in steps:
            val = f"{s * base_unit / 16}rem" if s else "0"
            lines.append(f".m-{s} {{ margin: {val}; }}")
            lines.append(f".mt-{s} {{ margin-top: {val}; }}")
            lines.append(f".mb-{s} {{ margin-bottom: {val}; }}")
            lines.append(f".ml-{s} {{ margin-left: {val}; }}")
            lines.append(f".mr-{s} {{ margin-right: {val}; }}")
            lines.append(f".mx-{s} {{ margin-left: {val}; margin-right: {val}; }}")
            lines.append(f".my-{s} {{ margin-top: {val}; margin-bottom: {val}; }}")
            lines.append(f".p-{s} {{ padding: {val}; }}")
            lines.append(f".pt-{s} {{ padding-top: {val}; }}")
            lines.append(f".pb-{s} {{ padding-bottom: {val}; }}")
            lines.append(f".pl-{s} {{ padding-left: {val}; }}")
            lines.append(f".pr-{s} {{ padding-right: {val}; }}")
            lines.append(f".px-{s} {{ padding-left: {val}; padding-right: {val}; }}")
            lines.append(f".py-{s} {{ padding-top: {val}; padding-bottom: {val}; }}")
        return "\n".join(lines)

    @staticmethod
    def generate_position_utilities() -> str:
        return textwrap.dedent("""\
        /* ═══ Position utilities ═══ */
        .relative { position: relative; }
        .absolute { position: absolute; }
        .fixed { position: fixed; }
        .sticky { position: sticky; top: 0; }
        .inset-0 { inset: 0; }
        .top-0 { top: 0; }
        .right-0 { right: 0; }
        .bottom-0 { bottom: 0; }
        .left-0 { left: 0; }
        .z-0 { z-index: 0; }
        .z-10 { z-index: 10; }
        .z-20 { z-index: 20; }
        .z-50 { z-index: 50; }
        """)

    @staticmethod
    def generate_display_utilities() -> str:
        return textwrap.dedent("""\
        /* ═══ Display utilities ═══ */
        .block { display: block; }
        .inline-block { display: inline-block; }
        .inline { display: inline; }
        .hidden { display: none; }
        .sr-only {
          position: absolute; width: 1px; height: 1px;
          padding: 0; margin: -1px; overflow: hidden;
          clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
        }
        .overflow-hidden { overflow: hidden; }
        .overflow-auto { overflow: auto; }
        .overflow-x-auto { overflow-x: auto; }
        .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .text-center { text-align: center; }
        .text-left { text-align: left; }
        .text-right { text-align: right; }
        .text-muted { color: var(--color-text-muted); }
        .font-bold { font-weight: 700; }
        .font-semibold { font-weight: 600; }
        .font-normal { font-weight: 400; }
        .text-sm { font-size: var(--font-size-sm); }
        .text-lg { font-size: var(--font-size-lg); }
        .text-xl { font-size: var(--font-size-xl); }
        .w-full { width: 100%; }
        .h-full { height: 100%; }
        .min-h-screen { min-height: 100vh; }
        .cursor-pointer { cursor: pointer; }
        .select-none { user-select: none; }
        .pointer-events-none { pointer-events: none; }
        .opacity-0 { opacity: 0; }
        .opacity-50 { opacity: 0.5; }
        .opacity-100 { opacity: 1; }
        """)


# ── Component Generator ───────────────────────────────────────────────


class CSSComponentGenerator:
    """Generate component CSS — sections over each component selector."""

    @staticmethod
    def generate_navbar() -> str:
        return textwrap.dedent("""\
        /* ─── Navbar ─── */
        .navbar {
          position: fixed; top: 0; left: 0; right: 0;
          z-index: var(--z-navbar); height: var(--nav-height);
          background: rgba(15, 15, 35, 0.92);
          backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
          border-bottom: 1px solid var(--color-border);
          transition: background var(--transition);
        }
        .nav-container {
          max-width: var(--max-width); margin: 0 auto; height: 100%;
          display: flex; align-items: center; justify-content: space-between;
          padding: 0 var(--space-6);
        }
        .nav-brand {
          font-size: var(--font-size-xl); font-weight: 700;
          color: var(--color-text); text-decoration: none;
        }
        .nav-links { display: flex; gap: var(--space-6); align-items: center; }
        .nav-link {
          color: var(--color-text-muted); font-size: var(--font-size-sm);
          transition: color var(--transition); text-decoration: none;
        }
        .nav-link:hover { color: var(--color-primary-light); }
        .nav-link.active { color: var(--color-primary-light); font-weight: 600; }
        .nav-toggle {
          display: none; background: none; border: none;
          color: var(--color-text); font-size: 1.5rem; cursor: pointer;
          padding: var(--space-2);
        }
        """)

    @staticmethod
    def generate_hero() -> str:
        return textwrap.dedent("""\
        /* ─── Hero ─── */
        .hero {
          position: relative; min-height: 80vh;
          display: flex; align-items: center; justify-content: center;
          padding: calc(var(--nav-height) + var(--space-8)) var(--space-6) var(--space-16);
          overflow: hidden;
        }
        .hero-inner {
          position: relative; z-index: 1;
          text-align: center; max-width: 800px;
        }
        .hero-title {
          font-size: clamp(2.5rem, 6vw, 4.5rem); font-weight: 800;
          background: linear-gradient(135deg, var(--color-primary-light), var(--color-accent));
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
          background-clip: text; margin-bottom: var(--space-4);
        }
        .hero-subtitle {
          font-size: clamp(1rem, 2vw, 1.35rem);
          color: var(--color-text-muted); max-width: 600px;
          margin: 0 auto var(--space-8);
        }
        .hero-actions { display: flex; gap: var(--space-4); justify-content: center; flex-wrap: wrap; }
        .hero-bg-canvas {
          position: absolute; inset: 0; width: 100%; height: 100%;
          z-index: 0; opacity: 0.5;
        }
        """)

    @staticmethod
    def generate_card() -> str:
        return textwrap.dedent("""\
        /* ─── Card ─── */
        .card {
          background: var(--color-bg-card); border-radius: var(--radius-lg);
          padding: var(--space-8); border: 1px solid var(--color-border);
          transition: all var(--transition);
        }
        .card:hover {
          border-color: var(--color-primary);
          transform: translateY(-4px); box-shadow: var(--shadow-lg);
        }
        .card-icon { font-size: 2rem; margin-bottom: var(--space-4); }
        .card-title { font-size: var(--font-size-xl); margin-bottom: var(--space-3); font-weight: 600; }
        .card-body { color: var(--color-text-muted); line-height: var(--line-height); }
        .card-footer { margin-top: var(--space-6); padding-top: var(--space-4); border-top: 1px solid var(--color-border); }
        .card-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: var(--space-6);
        }
        .card-image {
          width: 100%; border-radius: var(--radius) var(--radius) 0 0;
          object-fit: cover; margin: calc(-1 * var(--space-8)) calc(-1 * var(--space-8)) var(--space-6);
          width: calc(100% + var(--space-8) * 2);
        }
        """)

    @staticmethod
    def generate_button() -> str:
        return textwrap.dedent("""\
        /* ─── Buttons ─── */
        .btn {
          display: inline-flex; align-items: center; gap: var(--space-2);
          padding: var(--space-3) var(--space-6); border-radius: var(--radius);
          font-weight: 600; font-size: var(--font-size-sm);
          cursor: pointer; border: none;
          transition: all var(--transition); text-decoration: none;
          line-height: 1; white-space: nowrap;
        }
        .btn:focus-visible {
          outline: 2px solid var(--color-primary-light);
          outline-offset: 2px;
        }
        .btn:disabled {
          opacity: 0.5; cursor: not-allowed;
          pointer-events: none;
        }
        .btn-primary { background: var(--color-primary); color: #fff; }
        .btn-primary:hover {
          background: var(--color-primary-dark);
          transform: translateY(-2px); box-shadow: var(--shadow);
        }
        .btn-outline {
          background: transparent;
          border: 2px solid var(--color-primary);
          color: var(--color-primary-light);
        }
        .btn-outline:hover { background: var(--color-primary); color: #fff; }
        .btn-ghost {
          background: transparent; color: var(--color-text-muted);
          padding: var(--space-2) var(--space-4);
        }
        .btn-ghost:hover { background: var(--color-bg-card); color: var(--color-text); }
        .btn-danger { background: var(--color-danger); color: #fff; }
        .btn-danger:hover { background: #dc2626; }
        .btn-success { background: var(--color-success); color: #fff; }
        .btn-success:hover { background: #059669; }
        .btn-sm { padding: var(--space-2) var(--space-4); font-size: var(--font-size-xs); }
        .btn-lg { padding: var(--space-4) var(--space-10); font-size: var(--font-size-lg); border-radius: var(--radius-lg); }
        .btn-icon {
          padding: var(--space-2); border-radius: var(--radius-full);
          width: 2.5rem; height: 2.5rem; justify-content: center;
        }
        .btn-group { display: inline-flex; }
        .btn-group .btn { border-radius: 0; }
        .btn-group .btn:first-child { border-radius: var(--radius) 0 0 var(--radius); }
        .btn-group .btn:last-child { border-radius: 0 var(--radius) var(--radius) 0; }
        """)

    @staticmethod
    def generate_modal() -> str:
        return textwrap.dedent("""\
        /* ─── Modal ─── */
        .modal-backdrop {
          position: fixed; inset: 0; z-index: var(--z-modal);
          background: rgba(0, 0, 0, 0.6);
          display: flex; align-items: center; justify-content: center;
          opacity: 0; visibility: hidden;
          transition: opacity var(--transition), visibility var(--transition);
        }
        .modal-backdrop.open { opacity: 1; visibility: visible; }
        .modal {
          background: var(--color-bg-card); border-radius: var(--radius-lg);
          padding: var(--space-8); max-width: 600px; width: 90%;
          max-height: 80vh; overflow-y: auto; box-shadow: var(--shadow-lg);
          transform: scale(0.95) translateY(10px);
          transition: transform var(--transition);
        }
        .modal-backdrop.open .modal { transform: scale(1) translateY(0); }
        .modal-header {
          display: flex; justify-content: space-between;
          align-items: center; margin-bottom: var(--space-4);
        }
        .modal-title { font-size: var(--font-size-xl); font-weight: 700; }
        .modal-close {
          background: none; border: none;
          color: var(--color-text-muted); font-size: 1.5rem;
          cursor: pointer; padding: var(--space-1);
          transition: color var(--transition);
        }
        .modal-close:hover { color: var(--color-text); }
        .modal-body { margin-bottom: var(--space-6); }
        .modal-footer { display: flex; gap: var(--space-4); justify-content: flex-end; }
        """)

    @staticmethod
    def generate_toast() -> str:
        return textwrap.dedent("""\
        /* ─── Toast ─── */
        .toast-container {
          position: fixed; bottom: var(--space-4); right: var(--space-4);
          z-index: var(--z-toast);
          display: flex; flex-direction: column; gap: var(--space-2);
        }
        .toast {
          padding: var(--space-3) var(--space-5); border-radius: var(--radius);
          background: var(--color-bg-card); border: 1px solid var(--color-border);
          box-shadow: var(--shadow); min-width: 280px;
          animation: slideInRight 0.3s ease;
          display: flex; align-items: center; gap: var(--space-3);
        }
        .toast-success { border-left: 4px solid var(--color-success); }
        .toast-warning { border-left: 4px solid var(--color-warning); }
        .toast-danger { border-left: 4px solid var(--color-danger); }
        .toast-info { border-left: 4px solid var(--color-info); }
        .toast-close {
          margin-left: auto; background: none; border: none;
          color: var(--color-text-muted); cursor: pointer;
        }
        """)

    @staticmethod
    def generate_tabs() -> str:
        return textwrap.dedent("""\
        /* ─── Tabs ─── */
        .tabs-container { margin: var(--space-4) 0; }
        .tab-bar {
          display: flex; gap: 0;
          border-bottom: 2px solid var(--color-border);
        }
        .tab-btn {
          padding: var(--space-3) var(--space-6); background: none;
          border: none; color: var(--color-text-muted);
          cursor: pointer; font-size: var(--font-size-sm);
          border-bottom: 2px solid transparent;
          margin-bottom: -2px; transition: all var(--transition);
          font-weight: 500;
        }
        .tab-btn:hover { color: var(--color-text); }
        .tab-btn.active {
          color: var(--color-primary-light);
          border-bottom-color: var(--color-primary);
        }
        .tab-panel { display: none; padding: var(--space-6) 0; }
        .tab-panel.active { display: block; }
        """)

    @staticmethod
    def generate_accordion() -> str:
        return textwrap.dedent("""\
        /* ─── Accordion ─── */
        .accordion { border: 1px solid var(--color-border); border-radius: var(--radius); overflow: hidden; }
        .accordion-item { border-bottom: 1px solid var(--color-border); }
        .accordion-item:last-child { border-bottom: none; }
        .accordion-trigger {
          width: 100%; text-align: left; padding: var(--space-4) var(--space-5);
          background: var(--color-bg-card); border: none;
          color: var(--color-text); cursor: pointer;
          font-size: var(--font-size-base); font-weight: 600;
          display: flex; justify-content: space-between; align-items: center;
          transition: background var(--transition);
        }
        .accordion-trigger:hover { background: var(--color-bg-elevated); }
        .accordion-trigger::after { content: '+'; font-size: 1.25rem; transition: transform var(--transition); }
        .accordion-trigger.open::after { content: '−'; }
        .accordion-content {
          max-height: 0; overflow: hidden;
          transition: max-height 0.3s ease, padding 0.3s ease;
          padding: 0 var(--space-5);
        }
        .accordion-content.open { max-height: 1000px; padding: var(--space-4) var(--space-5); }
        """)

    @staticmethod
    def generate_form_elements() -> str:
        return textwrap.dedent("""\
        /* ─── Forms ─── */
        .form { max-width: 600px; }
        .form-group { margin-bottom: var(--space-5); }
        .form-group label {
          display: block; margin-bottom: var(--space-2);
          font-weight: 500; font-size: var(--font-size-sm);
        }
        .form-input, .form-select, .form-textarea {
          width: 100%; padding: var(--space-3) var(--space-4);
          border-radius: var(--radius); border: 1px solid var(--color-border);
          background: var(--color-bg); color: var(--color-text);
          font-size: var(--font-size-sm);
          transition: border-color var(--transition), box-shadow var(--transition);
        }
        .form-input:focus, .form-select:focus, .form-textarea:focus {
          outline: none; border-color: var(--color-primary);
          box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.15);
        }
        .form-input:disabled, .form-select:disabled, .form-textarea:disabled {
          opacity: 0.5; cursor: not-allowed; background: var(--color-bg-card);
        }
        .form-input::placeholder { color: var(--color-text-muted); }
        .form-textarea { resize: vertical; min-height: 100px; }
        .form-select { appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 1rem center; padding-right: 2.5rem; }
        .form-checkbox, .form-toggle { display: inline-flex; align-items: center; gap: var(--space-2); cursor: pointer; }
        .form-checkbox input[type="checkbox"] {
          width: 1.125rem; height: 1.125rem; border-radius: var(--radius-sm);
          border: 2px solid var(--color-border); appearance: none;
          background: var(--color-bg); cursor: pointer;
          transition: all var(--transition);
        }
        .form-checkbox input[type="checkbox"]:checked {
          background: var(--color-primary); border-color: var(--color-primary);
        }
        .form-toggle input[type="checkbox"] {
          width: 2.5rem; height: 1.375rem; border-radius: var(--radius-full);
          border: 2px solid var(--color-border); appearance: none;
          background: var(--color-bg-card); cursor: pointer;
          transition: all var(--transition); position: relative;
        }
        .form-toggle input[type="checkbox"]::after {
          content: ''; position: absolute; top: 2px; left: 2px;
          width: 0.875rem; height: 0.875rem; border-radius: var(--radius-full);
          background: var(--color-text-muted); transition: transform var(--transition);
        }
        .form-toggle input[type="checkbox"]:checked {
          background: var(--color-primary); border-color: var(--color-primary);
        }
        .form-toggle input[type="checkbox"]:checked::after {
          transform: translateX(1.125rem); background: #fff;
        }
        .form-error { color: var(--color-danger); font-size: var(--font-size-xs); margin-top: var(--space-1); }
        .form-hint { color: var(--color-text-muted); font-size: var(--font-size-xs); margin-top: var(--space-1); }
        """)

    @staticmethod
    def generate_table() -> str:
        return textwrap.dedent("""\
        /* ─── Table ─── */
        .table-container {
          overflow-x: auto; border-radius: var(--radius);
          border: 1px solid var(--color-border);
        }
        .data-table { width: 100%; border-collapse: collapse; }
        .data-table th, .data-table td {
          padding: var(--space-3) var(--space-4); text-align: left;
          border-bottom: 1px solid var(--color-border);
        }
        .data-table th {
          background: var(--color-bg-elevated); font-weight: 600;
          color: var(--color-primary-light); font-size: var(--font-size-sm);
          text-transform: uppercase; letter-spacing: 0.05em;
        }
        .data-table tr:last-child td { border-bottom: none; }
        .data-table tr:hover td { background: rgba(79, 70, 229, 0.05); }
        .data-table td { font-size: var(--font-size-sm); }
        """)

    @staticmethod
    def generate_sidebar() -> str:
        return textwrap.dedent("""\
        /* ─── Sidebar ─── */
        .sidebar {
          position: sticky; top: calc(var(--nav-height) + var(--space-4));
          padding: var(--space-4);
          max-height: calc(100vh - var(--nav-height) - var(--space-8));
          overflow-y: auto; width: 260px; flex-shrink: 0;
        }
        .sidebar-section { margin-bottom: var(--space-6); }
        .sidebar-heading {
          font-size: var(--font-size-xs); font-weight: 700;
          text-transform: uppercase; letter-spacing: 0.05em;
          color: var(--color-text-muted); margin-bottom: var(--space-2);
          padding: 0 var(--space-3);
        }
        .sidebar-link {
          display: block; padding: var(--space-2) var(--space-3);
          color: var(--color-text-muted); border-radius: var(--radius-sm);
          font-size: var(--font-size-sm); transition: all var(--transition);
          text-decoration: none;
        }
        .sidebar-link:hover { background: var(--color-bg-card); color: var(--color-text); }
        .sidebar-link.active { background: rgba(79, 70, 229, 0.1); color: var(--color-primary-light); }
        """)

    @staticmethod
    def generate_footer() -> str:
        return textwrap.dedent("""\
        /* ─── Footer ─── */
        .footer {
          padding: var(--space-8) var(--space-6); text-align: center;
          color: var(--color-text-muted);
          border-top: 1px solid var(--color-border);
          margin-top: var(--space-16);
        }
        .footer-grid {
          display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: var(--space-8); text-align: left;
          max-width: var(--max-width); margin: 0 auto var(--space-8);
        }
        .footer-heading { font-weight: 600; margin-bottom: var(--space-4); color: var(--color-text); }
        .footer-link {
          display: block; color: var(--color-text-muted);
          padding: var(--space-1) 0; font-size: var(--font-size-sm);
          transition: color var(--transition); text-decoration: none;
        }
        .footer-link:hover { color: var(--color-primary-light); }
        .footer-bottom { font-size: var(--font-size-sm); }
        """)

    @staticmethod
    def generate_loading_screen() -> str:
        return textwrap.dedent("""\
        /* ─── Loading screen ─── */
        #loading-screen, .loading-screen {
          position: fixed; inset: 0; z-index: var(--z-loading);
          display: flex; align-items: center; justify-content: center;
          background: var(--color-bg); color: var(--color-text);
          transition: opacity 0.5s ease;
        }
        .loading-screen.done { opacity: 0; pointer-events: none; }
        .loading-screen__content { text-align: center; }
        .loading-screen__title { font-size: var(--font-size-3xl); font-weight: 700; margin: var(--space-4) 0 var(--space-2); }
        .loading-screen__subtitle { color: var(--color-text-muted); margin-bottom: var(--space-8); }
        .loading-screen__progress-track {
          width: 300px; height: 6px; background: var(--color-border);
          border-radius: 3px; overflow: hidden; margin: 0 auto var(--space-4);
        }
        .loading-screen__progress-fill {
          height: 100%; width: 0; background: var(--color-primary);
          border-radius: 3px; transition: width 0.3s ease;
        }
        .loading-screen__progress-label { font-size: var(--font-size-sm); color: var(--color-text-muted); }
        .loading-screen__steps { list-style: none; padding: 0; margin-top: var(--space-6); }
        .loading-screen__step {
          font-size: var(--font-size-sm); padding: var(--space-1) 0;
          color: var(--color-text-muted); transition: color 0.3s;
        }
        .loading-screen__step.done { color: var(--color-success); }
        .loading-screen__step.done::before { content: '✓ '; }
        """)

    @staticmethod
    def generate_badge() -> str:
        return textwrap.dedent("""\
        /* ─── Badge ─── */
        .badge {
          display: inline-flex; align-items: center; gap: var(--space-1);
          padding: var(--space-1) var(--space-3);
          font-size: var(--font-size-xs); font-weight: 600;
          border-radius: var(--radius-full);
          background: var(--color-bg-elevated); color: var(--color-text-muted);
        }
        .badge-primary { background: rgba(79, 70, 229, 0.15); color: var(--color-primary-light); }
        .badge-success { background: rgba(16, 185, 129, 0.15); color: var(--color-success); }
        .badge-warning { background: rgba(245, 158, 11, 0.15); color: var(--color-warning); }
        .badge-danger { background: rgba(239, 68, 68, 0.15); color: var(--color-danger); }
        .badge-info { background: rgba(59, 130, 246, 0.15); color: var(--color-info); }
        """)

    @staticmethod
    def generate_progress() -> str:
        return textwrap.dedent("""\
        /* ─── Progress ─── */
        .progress-track {
          width: 100%; height: 8px; background: var(--color-border);
          border-radius: var(--radius-full); overflow: hidden;
        }
        .progress-fill {
          height: 100%; background: var(--color-primary);
          border-radius: var(--radius-full);
          transition: width var(--transition);
        }
        .progress-fill-success { background: var(--color-success); }
        .progress-fill-warning { background: var(--color-warning); }
        .progress-fill-danger { background: var(--color-danger); }
        .progress-label { font-size: var(--font-size-sm); color: var(--color-text-muted); margin-top: var(--space-1); }
        """)

    @staticmethod
    def generate_tooltip() -> str:
        return textwrap.dedent("""\
        /* ─── Tooltip ─── */
        .tooltip-wrapper { position: relative; display: inline-block; }
        .tooltip {
          position: absolute; bottom: calc(100% + 8px); left: 50%;
          transform: translateX(-50%); padding: var(--space-2) var(--space-3);
          background: var(--color-bg-elevated); color: var(--color-text);
          font-size: var(--font-size-xs); border-radius: var(--radius-sm);
          white-space: nowrap; pointer-events: none;
          opacity: 0; transition: opacity var(--transition-fast);
          box-shadow: var(--shadow-sm);
        }
        .tooltip::after {
          content: ''; position: absolute; top: 100%; left: 50%;
          transform: translateX(-50%);
          border: 5px solid transparent; border-top-color: var(--color-bg-elevated);
        }
        .tooltip-wrapper:hover .tooltip { opacity: 1; }
        """)

    @staticmethod
    def generate_dropdown() -> str:
        return textwrap.dedent("""\
        /* ─── Dropdown ─── */
        .dropdown { position: relative; display: inline-block; }
        .dropdown-menu {
          position: absolute; top: calc(100% + 4px); left: 0;
          min-width: 200px; background: var(--color-bg-card);
          border: 1px solid var(--color-border); border-radius: var(--radius);
          box-shadow: var(--shadow-lg); z-index: var(--z-dropdown);
          opacity: 0; visibility: hidden;
          transform: translateY(-4px);
          transition: all var(--transition-fast);
          padding: var(--space-2) 0;
        }
        .dropdown.open .dropdown-menu { opacity: 1; visibility: visible; transform: translateY(0); }
        .dropdown-item {
          display: block; width: 100%; padding: var(--space-2) var(--space-4);
          color: var(--color-text-muted); font-size: var(--font-size-sm);
          background: none; border: none; text-align: left; cursor: pointer;
          transition: all var(--transition-fast);
        }
        .dropdown-item:hover { background: var(--color-bg-elevated); color: var(--color-text); }
        .dropdown-divider { height: 1px; background: var(--color-border); margin: var(--space-2) 0; }
        """)

    @staticmethod
    def generate_skeleton() -> str:
        return textwrap.dedent("""\
        /* ─── Skeleton ─── */
        .skeleton {
          background: linear-gradient(90deg, var(--color-bg-card) 25%, var(--color-bg-elevated) 50%, var(--color-bg-card) 75%);
          background-size: 200% 100%;
          animation: shimmer 1.5s ease infinite;
          border-radius: var(--radius-sm);
        }
        .skeleton-text { height: 1em; margin-bottom: var(--space-2); }
        .skeleton-text:last-child { width: 60%; }
        .skeleton-heading { height: 1.5em; width: 40%; margin-bottom: var(--space-4); }
        .skeleton-avatar { width: 48px; height: 48px; border-radius: var(--radius-full); }
        .skeleton-image { width: 100%; height: 200px; }
        .skeleton-card { padding: var(--space-6); border-radius: var(--radius-lg); height: 200px; }
        """)

    @staticmethod
    def generate_empty_state() -> str:
        return textwrap.dedent("""\
        /* ─── Empty state ─── */
        .empty-state {
          text-align: center; padding: var(--space-16) var(--space-6);
          color: var(--color-text-muted);
        }
        .empty-state-icon { font-size: 3rem; margin-bottom: var(--space-4); opacity: 0.5; }
        .empty-state-title { font-size: var(--font-size-xl); font-weight: 600; color: var(--color-text); margin-bottom: var(--space-2); }
        .empty-state-description { max-width: 400px; margin: 0 auto var(--space-6); }
        """)

    @staticmethod
    def generate_error_state() -> str:
        return textwrap.dedent("""\
        /* ─── Error state ─── */
        .error-state {
          text-align: center; padding: var(--space-16) var(--space-6);
        }
        .error-state-icon { font-size: 3rem; margin-bottom: var(--space-4); color: var(--color-danger); }
        .error-state-title { font-size: var(--font-size-xl); font-weight: 600; margin-bottom: var(--space-2); }
        .error-state-description { color: var(--color-text-muted); max-width: 500px; margin: 0 auto var(--space-6); }
        .error-state-code {
          display: inline-block; background: var(--color-bg-card);
          padding: var(--space-3) var(--space-6); border-radius: var(--radius);
          font-family: var(--font-mono); font-size: var(--font-size-sm);
          color: var(--color-danger); margin-bottom: var(--space-6);
        }
        """)

    @staticmethod
    def _generate_extra_components() -> str:
        """Additional component sections: avatar, chip, breadcrumb, etc."""
        return textwrap.dedent("""\
        /* ─── Avatar ─── */
        .avatar {
          display: inline-flex; align-items: center; justify-content: center;
          width: 2.5rem; height: 2.5rem; border-radius: var(--radius-full);
          background: var(--color-primary); color: #fff;
          font-weight: 600; font-size: var(--font-size-sm); overflow: hidden;
        }
        .avatar img { width: 100%; height: 100%; object-fit: cover; }
        .avatar-sm { width: 2rem; height: 2rem; font-size: var(--font-size-xs); }
        .avatar-lg { width: 3.5rem; height: 3.5rem; font-size: var(--font-size-lg); }
        .avatar-group { display: flex; }
        .avatar-group .avatar { margin-left: -0.5rem; border: 2px solid var(--color-bg); }
        .avatar-group .avatar:first-child { margin-left: 0; }

        /* ─── Chip ─── */
        .chip {
          display: inline-flex; align-items: center; gap: var(--space-1);
          padding: var(--space-1) var(--space-3);
          font-size: var(--font-size-xs); border-radius: var(--radius-full);
          border: 1px solid var(--color-border); background: var(--color-bg-card);
          color: var(--color-text-muted);
        }
        .chip-close {
          display: inline-flex; cursor: pointer; background: none; border: none;
          color: var(--color-text-muted); font-size: 0.875rem;
          transition: color var(--transition-fast);
        }
        .chip-close:hover { color: var(--color-danger); }

        /* ─── Breadcrumb ─── */
        .breadcrumb { display: flex; align-items: center; gap: var(--space-2); font-size: var(--font-size-sm); }
        .breadcrumb-item { color: var(--color-text-muted); }
        .breadcrumb-item a { color: var(--color-text-muted); text-decoration: none; transition: color var(--transition); }
        .breadcrumb-item a:hover { color: var(--color-primary-light); }
        .breadcrumb-separator { color: var(--color-border); }
        .breadcrumb-item.active { color: var(--color-text); font-weight: 500; }

        /* ─── Pagination ─── */
        .pagination { display: flex; align-items: center; gap: var(--space-1); }
        .pagination-btn {
          padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm);
          background: none; border: 1px solid var(--color-border);
          color: var(--color-text-muted); cursor: pointer;
          font-size: var(--font-size-sm); transition: all var(--transition);
          min-width: 2.25rem; text-align: center;
        }
        .pagination-btn:hover { border-color: var(--color-primary); color: var(--color-primary-light); }
        .pagination-btn.active { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
        .pagination-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .pagination-ellipsis { color: var(--color-text-muted); padding: 0 var(--space-1); }

        /* ─── Alert ─── */
        .alert {
          padding: var(--space-4) var(--space-5); border-radius: var(--radius);
          border: 1px solid var(--color-border); margin-bottom: var(--space-4);
          display: flex; align-items: flex-start; gap: var(--space-3);
        }
        .alert-success { border-color: var(--color-success); background: rgba(16, 185, 129, 0.08); }
        .alert-warning { border-color: var(--color-warning); background: rgba(245, 158, 11, 0.08); }
        .alert-danger { border-color: var(--color-danger); background: rgba(239, 68, 68, 0.08); }
        .alert-info { border-color: var(--color-info); background: rgba(59, 130, 246, 0.08); }

        /* ─── Spinner ─── */
        .spinner {
          display: inline-block; width: 1.5rem; height: 1.5rem;
          border: 2px solid var(--color-border);
          border-top-color: var(--color-primary);
          border-radius: var(--radius-full);
          animation: rotate 0.6s linear infinite;
        }
        .spinner-sm { width: 1rem; height: 1rem; }
        .spinner-lg { width: 2.5rem; height: 2.5rem; border-width: 3px; }

        /* ─── Divider ─── */
        .divider { height: 1px; background: var(--color-border); margin: var(--space-6) 0; }
        .divider-vertical { width: 1px; height: 100%; background: var(--color-border); margin: 0 var(--space-4); }

        /* ─── Code block ─── */
        .code-block { position: relative; }
        .code-block pre {
          background: var(--color-bg-card); padding: var(--space-5);
          border-radius: var(--radius); overflow-x: auto;
          border: 1px solid var(--color-border);
          font-family: var(--font-mono); font-size: var(--font-size-sm);
          line-height: 1.6;
        }
        .code-block::before {
          content: attr(data-language); position: absolute;
          top: var(--space-2); right: var(--space-3);
          font-size: var(--font-size-xs); color: var(--color-text-muted);
          text-transform: uppercase;
        }

        /* ─── Blockquote ─── */
        .blockquote {
          border-left: 4px solid var(--color-primary);
          padding: var(--space-4) var(--space-6);
          background: var(--color-bg-card); border-radius: 0 var(--radius) var(--radius) 0;
          color: var(--color-text-muted); font-style: italic;
        }
        .blockquote cite { display: block; margin-top: var(--space-2); font-size: var(--font-size-sm); font-style: normal; color: var(--color-text-muted); }

        /* ─── Section ─── */
        .section { padding: var(--space-16) var(--space-6); }
        .section-title { text-align: center; margin-bottom: var(--space-12); }
        .section-alt { background: var(--color-bg-card); }

        /* ─── Canvas container ─── */
        .canvas-container { position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; border-radius: var(--radius); }
        .canvas-container canvas { width: 100%; height: 100%; }

        /* ─── SPA view routing ─── */
        [data-view] { display: none; }
        [data-view].active { display: block; }
        """)

    def generate_all(self) -> str:
        """Combine all component sections into a single stylesheet fragment."""
        parts = [
            self.generate_navbar(),
            self.generate_hero(),
            self.generate_card(),
            self.generate_button(),
            self.generate_modal(),
            self.generate_toast(),
            self.generate_tabs(),
            self.generate_accordion(),
            self.generate_form_elements(),
            self.generate_table(),
            self.generate_sidebar(),
            self.generate_footer(),
            self.generate_loading_screen(),
            self.generate_badge(),
            self.generate_progress(),
            self.generate_tooltip(),
            self.generate_dropdown(),
            self.generate_skeleton(),
            self.generate_empty_state(),
            self.generate_error_state(),
            self._generate_extra_components(),
        ]
        return "\n".join(parts)


# ── Animation Generator ───────────────────────────────────────────────


class CSSAnimationGenerator:
    """Animation and transition sections of the CSS presheaf."""

    @staticmethod
    def generate_keyframes() -> str:
        return textwrap.dedent("""\
        /* ═══ Keyframes ═══ */
        @keyframes fadeIn {
          from { opacity: 0; } to { opacity: 1; }
        }
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeInDown {
          from { opacity: 0; transform: translateY(-20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes slideUp {
          from { transform: translateY(100%); } to { transform: translateY(0); }
        }
        @keyframes slideDown {
          from { transform: translateY(-100%); } to { transform: translateY(0); }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideInLeft {
          from { transform: translateX(-100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @keyframes scaleIn {
          from { transform: scale(0.9); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          10%, 30%, 50%, 70%, 90% { transform: translateX(-4px); }
          20%, 40%, 60%, 80% { transform: translateX(4px); }
        }
        @keyframes rotate {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes bounce {
          0%, 20%, 50%, 80%, 100% { transform: translateY(0); }
          40% { transform: translateY(-12px); }
          60% { transform: translateY(-6px); }
        }
        @keyframes glow {
          0%, 100% { box-shadow: 0 0 8px rgba(79, 70, 229, 0.3); }
          50% { box-shadow: 0 0 20px rgba(79, 70, 229, 0.6); }
        }
        @keyframes float {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-8px); }
        }
        """)

    @staticmethod
    def generate_transition_utilities() -> str:
        return textwrap.dedent("""\
        /* ═══ Transition utilities ═══ */
        .transition { transition: all var(--transition); }
        .transition-fast { transition: all var(--transition-fast); }
        .transition-slow { transition: all var(--transition-slow); }
        .transition-none { transition: none; }
        .fade-in { animation: fadeInUp 0.6s ease forwards; opacity: 0; }
        .fade-in-1 { animation-delay: 0.1s; }
        .fade-in-2 { animation-delay: 0.2s; }
        .fade-in-3 { animation-delay: 0.3s; }
        .fade-in-4 { animation-delay: 0.4s; }
        .fade-in-5 { animation-delay: 0.5s; }
        .animate-pulse { animation: pulse 2s ease infinite; }
        .animate-bounce { animation: bounce 1s ease infinite; }
        .animate-shake { animation: shake 0.5s ease; }
        .animate-float { animation: float 3s ease-in-out infinite; }
        .animate-glow { animation: glow 2s ease infinite; }
        .animate-rotate { animation: rotate 1s linear infinite; }

        /* ─── Glow effect ─── */
        .glow { position: relative; }
        .glow::after {
          content: ''; position: absolute; inset: -2px; border-radius: inherit;
          background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
          z-index: -1; opacity: 0; transition: opacity var(--transition);
          filter: blur(8px);
        }
        .glow:hover::after { opacity: 0.6; }
        """)

    @staticmethod
    def generate_scroll_animations() -> str:
        return textwrap.dedent("""\
        /* ═══ Scroll-driven reveal ═══ */
        .reveal {
          opacity: 0; transform: translateY(30px);
          transition: opacity 0.6s ease, transform 0.6s ease;
        }
        .reveal.visible { opacity: 1; transform: translateY(0); }
        .reveal-left {
          opacity: 0; transform: translateX(-30px);
          transition: opacity 0.6s ease, transform 0.6s ease;
        }
        .reveal-left.visible { opacity: 1; transform: translateX(0); }
        .reveal-right {
          opacity: 0; transform: translateX(30px);
          transition: opacity 0.6s ease, transform 0.6s ease;
        }
        .reveal-right.visible { opacity: 1; transform: translateX(0); }
        .reveal-scale {
          opacity: 0; transform: scale(0.9);
          transition: opacity 0.6s ease, transform 0.6s ease;
        }
        .reveal-scale.visible { opacity: 1; transform: scale(1); }
        """)

    @staticmethod
    def generate_reduced_motion() -> str:
        return textwrap.dedent("""\
        /* ═══ Reduced motion ═══ */
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
            scroll-behavior: auto !important;
          }
          .reveal, .reveal-left, .reveal-right, .reveal-scale {
            opacity: 1; transform: none;
          }
        }
        """)


# ── Responsive Generator ──────────────────────────────────────────────


class CSSResponsiveGenerator:
    """Responsive gluing — media queries as covering families."""

    def __init__(self, breakpoints: dict[str, int] | None = None) -> None:
        self.bp = {**_DEFAULT_BREAKPOINTS, **(breakpoints or {})}

    def generate_breakpoint_system(
        self, breakpoints: dict[str, int] | None = None,
    ) -> str:
        bp = breakpoints or self.bp
        lines = ["/* ═══ Responsive breakpoint system ═══ */"]
        for name, px in bp.items():
            lines.append(f"@media (min-width: {px}px) {{")
            lines.append(f"  .{name}\\:hidden {{ display: none; }}")
            lines.append(f"  .{name}\\:block {{ display: block; }}")
            lines.append(f"  .{name}\\:flex {{ display: flex; }}")
            lines.append(f"  .{name}\\:grid {{ display: grid; }}")
            lines.append(f"  .{name}\\:inline {{ display: inline; }}")
            for cols in (1, 2, 3, 4, 6, 12):
                lines.append(
                    f"  .{name}\\:grid-cols-{cols} "
                    f"{{ grid-template-columns: repeat({cols}, 1fr); }}"
                )
            lines.append("}")
        return "\n".join(lines)

    def generate_responsive_typography(self) -> str:
        return textwrap.dedent(f"""\
        /* ═══ Responsive typography ═══ */
        body {{
          font-family: var(--font-body); color: var(--color-text);
          background: var(--color-bg); line-height: var(--line-height);
        }}
        a {{ color: var(--color-primary-light); text-decoration: none; transition: color var(--transition); }}
        a:hover {{ color: var(--color-accent); }}
        h1, h2, h3, h4, h5, h6 {{ line-height: var(--heading-line-height); font-weight: 700; margin-bottom: 0.5em; }}
        h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); }}
        h2 {{ font-size: clamp(1.5rem, 3vw, 2.5rem); }}
        h3 {{ font-size: clamp(1.2rem, 2vw, 1.75rem); }}
        h4 {{ font-size: var(--font-size-xl); }}
        h5 {{ font-size: var(--font-size-lg); }}
        h6 {{ font-size: var(--font-size-base); }}
        code, pre {{ font-family: var(--font-mono); }}
        pre {{
          background: var(--color-bg-card); padding: var(--space-5);
          border-radius: var(--radius); overflow-x: auto;
          border: 1px solid var(--color-border);
        }}
        code {{
          background: var(--color-bg-card); padding: 0.15em 0.35em;
          border-radius: var(--radius-sm); font-size: 0.9em;
        }}
        pre code {{ background: none; padding: 0; }}
        img {{ max-width: 100%; height: auto; }}
        """)

    def generate_responsive_grid(self) -> str:
        md = self.bp.get("md", 768)
        return textwrap.dedent(f"""\
        /* ═══ Responsive grid collapse ═══ */
        @media (max-width: {md}px) {{
          .grid-cols-2, .grid-cols-3, .grid-cols-4 {{ grid-template-columns: 1fr; }}
          .card-grid {{ grid-template-columns: 1fr; }}
        }}
        """)

    def generate_responsive_navigation(self) -> str:
        md = self.bp.get("md", 768)
        return textwrap.dedent(f"""\
        /* ═══ Responsive navigation ═══ */
        @media (max-width: {md}px) {{
          .nav-toggle {{ display: block; }}
          .nav-links {{
            display: none; position: absolute;
            top: var(--nav-height); left: 0; right: 0;
            background: var(--color-bg-card);
            flex-direction: column; padding: var(--space-4);
            border-bottom: 1px solid var(--color-border);
            box-shadow: var(--shadow);
          }}
          .nav-links.open {{ display: flex; }}
          .nav-link {{ padding: var(--space-2) 0; }}
          .hero {{ min-height: 60vh; padding-top: calc(var(--nav-height) + var(--space-4)); }}
          .hero-title {{ font-size: clamp(1.75rem, 8vw, 2.5rem); }}
          .container {{ padding: 0 var(--space-4); }}
          .section {{ padding: var(--space-8) var(--space-4); }}
          .modal {{ width: 95%; padding: var(--space-5); }}
        }}
        """)

    def generate_responsive_sidebar(self) -> str:
        lg = self.bp.get("lg", 1024)
        return textwrap.dedent(f"""\
        /* ═══ Responsive sidebar ═══ */
        @media (max-width: {lg}px) {{
          .sidebar {{
            position: fixed; top: 0; left: 0; bottom: 0;
            z-index: var(--z-modal); width: 280px;
            background: var(--color-bg-card);
            transform: translateX(-100%);
            transition: transform var(--transition);
            box-shadow: var(--shadow-lg);
          }}
          .sidebar.open {{ transform: translateX(0); }}
          .sidebar-overlay {{
            position: fixed; inset: 0;
            background: rgba(0, 0, 0, 0.5);
            z-index: calc(var(--z-modal) - 1);
            opacity: 0; visibility: hidden;
            transition: all var(--transition);
          }}
          .sidebar-overlay.open {{ opacity: 1; visibility: visible; }}
        }}
        """)

    @staticmethod
    def generate_print_styles() -> str:
        return textwrap.dedent("""\
        /* ═══ Print styles ═══ */
        @media print {
          *, *::before, *::after { background: transparent !important; color: #000 !important; box-shadow: none !important; text-shadow: none !important; }
          body { font-size: 12pt; line-height: 1.5; }
          .navbar, .nav-toggle, .sidebar, .toast-container, .modal-backdrop,
          .loading-screen, #loading-screen, .footer, .btn { display: none !important; }
          a { text-decoration: underline; }
          a[href]::after { content: ' (' attr(href) ')'; font-size: 0.85em; }
          img { max-width: 100% !important; page-break-inside: avoid; }
          h1, h2, h3 { page-break-after: avoid; }
          .container { max-width: 100%; padding: 0; }
          table { border-collapse: collapse; }
          table th, table td { border: 1px solid #ccc; padding: 0.5em; }
          @page { margin: 2cm; }
        }
        """)


# ── Main Theory Generator ────────────────────────────────────────────


class CSSTheoryGenerator:
    """Main entry point — assemble the complete CSS presheaf.

    Orders the stylesheet as: reset → variables → typography → layout →
    components → animations → responsive → print → utilities.
    """

    def __init__(
        self,
        palette: dict[str, str] | None = None,
        typography: dict[str, str] | None = None,
        spacing: dict[str, str] | None = None,
        breakpoints: dict[str, int] | None = None,
    ) -> None:
        self.palette = palette
        self.typography = typography
        self.spacing = spacing
        self.breakpoints = breakpoints
        self._responsive = CSSResponsiveGenerator(breakpoints)

    def generate_complete_stylesheet(
        self,
        obligations: list[CSSDescentObligation] | None = None,
    ) -> str:
        """Generate a complete CSS stylesheet from theory.

        This replaces the hardcoded ``_generate_base_css()`` in
        *html_generator.py*.  The resulting stylesheet is ordered to
        respect the cascade: reset → variables → typography → layout →
        components → animations → responsive → print → utilities.
        """
        sections: list[str] = [
            "/* ═══ Generated by jugeo CSS Theory ═══ */",
            "",
            CSSReset.generate(),
            CSSVariableSystem.generate_all(
                self.palette, self.typography, self.spacing, self.breakpoints,
            ),
            "",
            self._responsive.generate_responsive_typography(),
            CSSLayoutGenerator.generate_container(),
            CSSComponentGenerator().generate_all(),
            CSSAnimationGenerator.generate_keyframes(),
            CSSAnimationGenerator.generate_transition_utilities(),
            CSSAnimationGenerator.generate_scroll_animations(),
            CSSAnimationGenerator.generate_reduced_motion(),
            self._responsive.generate_breakpoint_system(),
            self._responsive.generate_responsive_grid(),
            self._responsive.generate_responsive_navigation(),
            self._responsive.generate_responsive_sidebar(),
            CSSResponsiveGenerator.generate_print_styles(),
            CSSLayoutGenerator.generate_grid_system(),
            CSSLayoutGenerator.generate_flex_utilities(),
            CSSLayoutGenerator.generate_spacing_utilities(),
            CSSLayoutGenerator.generate_position_utilities(),
            CSSLayoutGenerator.generate_display_utilities(),
        ]
        return "\n".join(sections)


# ── Descent Checker ───────────────────────────────────────────────────


class CSSDescentChecker:
    """Check that CSS satisfies all descent obligations.

    Each obligation specifies a component and the properties / media
    queries it must declare.  The checker scans the stylesheet text and
    reports any missing sections.
    """

    _COMPONENT_SELECTORS: dict[ComponentStyleKind, list[str]] = {
        ComponentStyleKind.NAVBAR: [".navbar"],
        ComponentStyleKind.HERO: [".hero"],
        ComponentStyleKind.CARD: [".card"],
        ComponentStyleKind.BUTTON: [".btn"],
        ComponentStyleKind.BUTTON_PRIMARY: [".btn-primary"],
        ComponentStyleKind.BUTTON_OUTLINE: [".btn-outline"],
        ComponentStyleKind.BUTTON_GHOST: [".btn-ghost"],
        ComponentStyleKind.MODAL: [".modal"],
        ComponentStyleKind.TOAST: [".toast"],
        ComponentStyleKind.TABS: [".tab-btn", ".tab-panel"],
        ComponentStyleKind.TAB_PANEL: [".tab-panel"],
        ComponentStyleKind.ACCORDION: [".accordion-trigger", ".accordion-content"],
        ComponentStyleKind.FORM_INPUT: [".form-input"],
        ComponentStyleKind.FORM_SELECT: [".form-select"],
        ComponentStyleKind.FORM_TEXTAREA: [".form-textarea"],
        ComponentStyleKind.FORM_CHECKBOX: [".form-checkbox"],
        ComponentStyleKind.FORM_TOGGLE: [".form-toggle"],
        ComponentStyleKind.TABLE: [".data-table"],
        ComponentStyleKind.BADGE: [".badge"],
        ComponentStyleKind.PROGRESS: [".progress-track"],
        ComponentStyleKind.TOOLTIP: [".tooltip"],
        ComponentStyleKind.DROPDOWN: [".dropdown"],
        ComponentStyleKind.SIDEBAR: [".sidebar"],
        ComponentStyleKind.FOOTER: [".footer"],
        ComponentStyleKind.SKELETON: [".skeleton"],
        ComponentStyleKind.AVATAR: [".avatar"],
        ComponentStyleKind.CHIP: [".chip"],
        ComponentStyleKind.BREADCRUMB: [".breadcrumb"],
        ComponentStyleKind.PAGINATION: [".pagination"],
        ComponentStyleKind.ALERT: [".alert"],
        ComponentStyleKind.SPINNER: [".spinner"],
        ComponentStyleKind.DIVIDER: [".divider"],
        ComponentStyleKind.CODE_BLOCK: [".code-block"],
        ComponentStyleKind.BLOCKQUOTE: [".blockquote"],
        ComponentStyleKind.HUD: [".hud"],
        ComponentStyleKind.LIST: ["ul", "ol"],
        ComponentStyleKind.GRID_LAYOUT: [".grid"],
        ComponentStyleKind.CANVAS_CONTAINER: [".canvas-container"],
        ComponentStyleKind.LOADING_SCREEN: [".loading-screen"],
        ComponentStyleKind.EMPTY_STATE: [".empty-state"],
        ComponentStyleKind.ERROR_STATE: [".error-state"],
    }

    @classmethod
    def check(
        cls,
        css: str,
        obligations: list[CSSDescentObligation],
    ) -> list[str]:
        """Return a list of human-readable obstruction messages.

        An empty list means the CSS satisfies all obligations.
        """
        issues: list[str] = []
        for ob in obligations:
            selectors = cls._COMPONENT_SELECTORS.get(ob.component, [])
            for sel in selectors:
                escaped = re.escape(sel)
                if not re.search(escaped, css):
                    issues.append(
                        f"Missing selector {sel!r} for "
                        f"{ob.component.name}"
                    )

            for prop in ob.required_properties:
                escaped_prop = re.escape(prop)
                if not re.search(rf"{escaped_prop}\s*:", css):
                    issues.append(
                        f"Missing property {prop!r} for "
                        f"{ob.component.name}"
                    )

            if ob.responsive and "@media" not in css:
                issues.append(
                    f"No @media rules found but {ob.component.name} "
                    "requires responsive styles"
                )

            if ob.reduced_motion:
                if "prefers-reduced-motion" not in css:
                    issues.append(
                        f"Missing prefers-reduced-motion for "
                        f"{ob.component.name}"
                    )

            for state in ob.interactive_states:
                if state not in css:
                    issues.append(
                        f"Missing interactive state {state!r} for "
                        f"{ob.component.name}"
                    )

        return issues
