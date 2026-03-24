"""HTML component generators for Chromatic Territories.

Generates complete HTML document and component markup for the web app.
Uses ``HTML_COMPONENTS`` dict and ``get_html_for_concept()`` for individual
lookups — **no** ``@register()`` decorator (avoids name collisions with
JS generators that register the same concept names).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _indent(text: str, level: int = 1, width: int = 2) -> str:
    """Indent every non-blank line of *text*."""
    pad = " " * (level * width)
    return "\n".join(
        (pad + ln) if ln.strip() else "" for ln in text.splitlines()
    )


def _aria(scale: int, **attrs: str) -> str:
    """Emit ARIA / a11y attributes only when *scale* >= 4."""
    if scale < 4:
        return ""
    parts = []
    for key, val in attrs.items():
        parts.append(f'{key.replace("_", "-")}="{val}"')
    return (" " + " ".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# Inline SVG icons  (scale >= 5)
# ---------------------------------------------------------------------------

_SVG_ICONS: dict[str, str] = {
    "menu": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="3" y1="6" x2="21" y2="6"/>'
        '<line x1="3" y1="12" x2="21" y2="12"/>'
        '<line x1="3" y1="18" x2="21" y2="18"/>'
        "</svg>"
    ),
    "close": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="18" y1="6" x2="6" y2="18"/>'
        '<line x1="6" y1="6" x2="18" y2="18"/>'
        "</svg>"
    ),
    "chevron-left": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="15 18 9 12 15 6"/>'
        "</svg>"
    ),
    "chevron-right": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="9 18 15 12 9 6"/>'
        "</svg>"
    ),
    "settings": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83'
        "l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21"
        'a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0'
        "-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0"
        ' .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65'
        ' 0 0 0 1.51-1.08 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1'
        " 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0"
        ' 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1.08 1.51 1.65'
        " 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65"
        ' 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1.08H21a2 2 0 0 1'
        ' 0 4h-.09a1.65 1.65 0 0 0-1.51 1.08z"/>'
        "</svg>"
    ),
    "brush": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 19l7-7 3 3-7 7-3-3z"/>'
        '<path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/>'
        '<path d="M2 2l7.586 7.586"/>'
        '<circle cx="11" cy="11" r="2"/>'
        "</svg>"
    ),
    "eraser": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M20 20H7L3 16l9-9 8 8-4 4z"/>'
        '<path d="M6.5 13.5l5-5"/>'
        "</svg>"
    ),
    "move": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="5 9 2 12 5 15"/>'
        '<polyline points="9 5 12 2 15 5"/>'
        '<polyline points="15 19 12 22 9 19"/>'
        '<polyline points="19 9 22 12 19 15"/>'
        '<line x1="2" y1="12" x2="22" y2="12"/>'
        '<line x1="12" y1="2" x2="12" y2="22"/>'
        "</svg>"
    ),
    "eye": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
        '<circle cx="12" cy="12" r="3"/>'
        "</svg>"
    ),
    "zap": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'
        "</svg>"
    ),
    "shield": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
        "</svg>"
    ),
    "flag": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>'
        '<line x1="4" y1="22" x2="4" y2="15"/>'
        "</svg>"
    ),
    "grid": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="3" width="7" height="7"/>'
        '<rect x="14" y="3" width="7" height="7"/>'
        '<rect x="3" y="14" width="7" height="7"/>'
        '<rect x="14" y="14" width="7" height="7"/>'
        "</svg>"
    ),
    "info": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="12" y1="16" x2="12" y2="12"/>'
        '<line x1="12" y1="8" x2="12.01" y2="8"/>'
        "</svg>"
    ),
    "help": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
        "</svg>"
    ),
    "trophy": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M6 9H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h2"/>'
        '<path d="M18 9h2a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-2"/>'
        '<path d="M6 3h12v6a6 6 0 0 1-12 0V3z"/>'
        '<path d="M9 18h6"/>'
        '<path d="M10 22h4"/>'
        '<path d="M12 15v3"/>'
        "</svg>"
    ),
    "volume": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>'
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>'
        "</svg>"
    ),
    "download": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/>'
        '<line x1="12" y1="15" x2="12" y2="3"/>'
        "</svg>"
    ),
    "search": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="8"/>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"/>'
        "</svg>"
    ),
    "undo": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="1 4 1 10 7 10"/>'
        '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'
        "</svg>"
    ),
    "redo": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="23 4 23 10 17 10"/>'
        '<path d="M20.49 15a9 9 0 1 1-2.13-9.36L23 10"/>'
        "</svg>"
    ),
    "maximize": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="15 3 21 3 21 9"/>'
        '<polyline points="9 21 3 21 3 15"/>'
        '<line x1="21" y1="3" x2="14" y2="10"/>'
        '<line x1="3" y1="21" x2="10" y2="14"/>'
        "</svg>"
    ),
    "play": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="5 3 19 12 5 21 5 3"/>'
        "</svg>"
    ),
    "pause": (
        '<svg class="ct-icon" viewBox="0 0 24 24" width="24" height="24"'
        ' fill="none" stroke="currentColor" stroke-width="2"'
        ' stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="6" y="4" width="4" height="16"/>'
        '<rect x="14" y="4" width="4" height="16"/>'
        "</svg>"
    ),
}


def _icon(name: str, scale: int) -> str:
    """Return inline SVG for *name* when scale >= 5, else empty."""
    if scale < 5:
        return ""
    return _SVG_ICONS.get(name, "")


def _icon_or_span(name: str, label: str, scale: int) -> str:
    """SVG icon at scale>=5, otherwise a text span."""
    svg = _icon(name, scale)
    if svg:
        return svg
    return f'<span class="ct-icon-text">{label}</span>'


# ═══════════════════════════════════════════════════════════════════════════
# Component generators
# ═══════════════════════════════════════════════════════════════════════════
# Each returns an HTML fragment string.  *scale* (1-5) controls verbosity.


def _gen_app_shell_open(title: str, scale: int, description: str = "") -> str:
    """Opening tags: doctype through <div id="app">."""
    desc = description or f"{title} — a chromatic territory strategy game"
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"  <title>{title}</title>",
        f'  <meta name="description" content="{desc}">',
        '  <link rel="icon" href="favicon.ico" type="image/x-icon">',
    ]
    if scale >= 4:
        lines += [
            '  <meta name="theme-color" content="#6c5ce7">',
            '  <meta name="color-scheme" content="dark light">',
            '  <meta name="application-name" content="Chromatic Territories">',
        ]
    if scale >= 5:
        lines += [
            '  <meta property="og:title" content="' + title + '">',
            '  <meta property="og:description" content="' + desc + '">',
            '  <meta property="og:type" content="website">',
            '  <meta property="og:image" content="og-image.png">',
            '  <link rel="apple-touch-icon" href="apple-touch-icon.png">',
            '  <link rel="manifest" href="manifest.json">',
        ]
    lines += [
        "  <!-- Google Fonts -->",
        '  <link rel="preconnect" href="https://fonts.googleapis.com">',
        '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">',
        "  <!-- Application styles -->",
        '  <link rel="stylesheet" href="styles.css">',
        "</head>",
        "<body>",
    ]
    if scale >= 5:
        lines += [
            "  <!-- Skip links for accessibility -->",
            '  <a class="ct-skip-link" href="#main-content">Skip to main content</a>',
            '  <a class="ct-skip-link" href="#ct-canvas-area">Skip to game canvas</a>',
        ]
    lines += [
        '  <div id="app" class="ct-app">',
    ]
    return "\n".join(lines)


def _gen_app_shell_close(scale: int) -> str:
    """Closing tags for the shell."""
    lines = [
        "  </div><!-- /.ct-app -->",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines)


# ── Navigation ─────────────────────────────────────────────────────────────

def _gen_navigation(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(f'    <header class="ct-header"{a(scale, role="banner")}>')
    lines.append(f'      <nav class="ct-nav"{a(scale, aria_label="Main navigation")}>')
    lines.append('        <div class="ct-nav__brand">')
    if scale >= 5:
        lines.append(
            '          <svg class="ct-nav__logo" viewBox="0 0 32 32" width="32" height="32"'
            ' fill="none" stroke="currentColor" stroke-width="2">'
        )
        lines.append('            <polygon points="16 2 30 28 2 28" stroke="#6c5ce7" fill="rgba(108,92,231,0.15)"/>')
        lines.append('            <circle cx="16" cy="18" r="5" stroke="#00cec9" fill="rgba(0,206,201,0.15)"/>')
        lines.append("          </svg>")
    lines.append('          <span class="ct-nav__title">Chromatic Territories</span>')
    lines.append("        </div>")
    lines.append("")
    lines.append(f'        <ul class="ct-nav__links"{a(scale, role="menubar")}>')
    nav_items = [
        ("territory", "Territory", "#territory"),
        ("gallery", "Gallery", "#gallery"),
        ("tutorial", "Tutorial", "#tutorial"),
        ("settings", "Settings", "#settings"),
    ]
    for data_id, label, href in nav_items:
        if scale >= 5:
            lines.append(
                f'          <li class="ct-nav__item" role="none">'
                f'<a class="ct-nav__link" href="{href}" data-nav="{data_id}"'
                f' role="menuitem">{label}</a></li>'
            )
        elif scale >= 4:
            lines.append(
                f'          <li class="ct-nav__item">'
                f'<a class="ct-nav__link" href="{href}" data-nav="{data_id}"'
                f' role="menuitem">{label}</a></li>'
            )
        else:
            lines.append(
                f'          <li class="ct-nav__item">'
                f'<a class="ct-nav__link" href="{href}" data-nav="{data_id}">'
                f"{label}</a></li>"
            )
    lines.append("        </ul>")
    lines.append("")
    if scale >= 4:
        lines.append('        <div class="ct-nav__actions">')
        lines.append(
            f'          <button class="ct-btn ct-btn--icon ct-nav__help-btn" data-action="open-help"'
            f'{a(scale, aria_label="Open help")}>{_icon_or_span("help", "?", scale)}</button>'
        )
        lines.append("        </div>")
        lines.append("")
    hamburger_aria = a(scale, aria_label="Toggle menu", aria_expanded="false", aria_controls="ct-mobile-menu")
    lines.append(f'        <button class="ct-nav__hamburger" data-action="toggle-menu"{hamburger_aria}>')
    if scale >= 5:
        lines.append("          " + _SVG_ICONS["menu"])
    else:
        lines.append('          <span class="ct-nav__hamburger-bar"></span>')
        lines.append('          <span class="ct-nav__hamburger-bar"></span>')
        lines.append('          <span class="ct-nav__hamburger-bar"></span>')
    lines.append("        </button>")
    lines.append("      </nav>")
    if scale >= 3:
        lines.append("")
        lines.append(f'      <div id="ct-mobile-menu" class="ct-mobile-menu" hidden{a(scale, role="menu")}>')
        lines.append('        <ul class="ct-mobile-menu__list">')
        for data_id, label, href in nav_items:
            lines.append(
                f'          <li class="ct-mobile-menu__item">'
                f'<a class="ct-mobile-menu__link" href="{href}" data-nav="{data_id}">'
                f"{label}</a></li>"
            )
        lines.append("        </ul>")
        lines.append("      </div>")
    lines.append("    </header>")
    return "\n".join(lines)


# ── Hero ───────────────────────────────────────────────────────────────────

def _gen_hero(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(f'    <section class="ct-hero" id="hero" data-panel="hero"{a(scale, aria_label="Welcome hero section")}>')
    lines.append('      <div class="ct-hero__bg">')
    lines.append('        <div class="ct-hero__gradient"></div>')
    if scale >= 4:
        lines.append('        <div class="ct-hero__particles" data-effect="hero-particles"></div>')
    if scale >= 5:
        lines.append('        <div class="ct-hero__grid-overlay" aria-hidden="true">')
        lines.append(
            '          <svg class="ct-hero__grid-svg" viewBox="0 0 800 400" preserveAspectRatio="none"'
            ' xmlns="http://www.w3.org/2000/svg">'
        )
        lines.append('            <defs>')
        lines.append('              <pattern id="hero-grid" width="40" height="40" patternUnits="userSpaceOnUse">')
        lines.append('                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(108,92,231,0.1)" stroke-width="0.5"/>')
        lines.append("              </pattern>")
        lines.append("            </defs>")
        lines.append('            <rect width="100%" height="100%" fill="url(#hero-grid)"/>')
        lines.append("          </svg>")
        lines.append("        </div>")
    lines.append("      </div>")
    lines.append("")
    lines.append('      <div class="ct-hero__content">')
    lines.append('        <h1 class="ct-hero__title" data-animate="fade-up">')
    lines.append("          <span class=\"ct-hero__title-line\">Claim Your</span>")
    lines.append('          <span class="ct-hero__title-line ct-hero__title-line--accent">Chromatic Territory</span>')
    lines.append("        </h1>")
    lines.append('        <p class="ct-hero__subtitle" data-animate="fade-up" data-delay="200">')
    lines.append("          Conquer the grid with colour, strategy, and generative art.")
    lines.append("        </p>")
    lines.append("")
    lines.append('        <div class="ct-hero__cta" data-animate="fade-up" data-delay="400">')
    lines.append(
        f'          <button class="ct-btn ct-btn--primary ct-btn--lg ct-hero__cta-play"'
        f' data-action="start-game"{a(scale, aria_label="Start a new game")}>'
    )
    if scale >= 5:
        lines.append("            " + _SVG_ICONS["play"])
    lines.append("            <span>Play Now</span>")
    lines.append("          </button>")
    lines.append(
        f'          <button class="ct-btn ct-btn--outline ct-btn--lg ct-hero__cta-tutorial"'
        f' data-action="open-tutorial"{a(scale, aria_label="Open the tutorial")}>'
    )
    lines.append("            <span>How to Play</span>")
    lines.append("          </button>")
    lines.append("        </div>")
    if scale >= 4:
        lines.append("")
        lines.append('        <div class="ct-hero__stats" data-animate="fade-up" data-delay="600">')
        lines.append('          <div class="ct-hero__stat">')
        lines.append('            <span class="ct-hero__stat-value" data-counter="territories">1,024</span>')
        lines.append('            <span class="ct-hero__stat-label">Territories Claimed</span>')
        lines.append("          </div>")
        lines.append('          <div class="ct-hero__stat">')
        lines.append('            <span class="ct-hero__stat-value" data-counter="players">256</span>')
        lines.append('            <span class="ct-hero__stat-label">Active Players</span>')
        lines.append("          </div>")
        lines.append('          <div class="ct-hero__stat">')
        lines.append('            <span class="ct-hero__stat-value" data-counter="matches">4,096</span>')
        lines.append('            <span class="ct-hero__stat-label">Matches Played</span>')
        lines.append("          </div>")
        lines.append("        </div>")
    lines.append("      </div>")
    if scale >= 5:
        lines.append("")
        lines.append('      <div class="ct-hero__features" data-animate="fade-up" data-delay="800">')
        features = [
            ("brush", "Generative Art", "Every territory is a unique piece of generative artwork, shaped by colour theory and fractal algorithms."),
            ("zap", "Strategic Combat", "Attack and defend territories in a colour-based combat system. Complementary colours gain bonuses."),
            ("grid", "Dynamic Grid", "Play on procedurally generated maps with varied terrain types, resources, and strategic chokepoints."),
            ("trophy", "Competitive Play", "Climb the leaderboard, earn achievements, and unlock new colour palettes as you master the game."),
        ]
        for icon_name, title, desc in features:
            lines.append(f'        <div class="ct-hero__feature">')
            lines.append(f'          <div class="ct-hero__feature-icon" aria-hidden="true">')
            lines.append(f"            {_SVG_ICONS.get(icon_name, '')}")
            lines.append(f"          </div>")
            lines.append(f'          <h3 class="ct-hero__feature-title">{title}</h3>')
            lines.append(f'          <p class="ct-hero__feature-desc">{desc}</p>')
            lines.append(f"        </div>")
        lines.append("      </div>")
    lines.append("")
    # Floating decorative elements
    lines.append('      <div class="ct-hero__decor" aria-hidden="true">')
    lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--1"></div>')
    lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--2"></div>')
    lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--3"></div>')
    if scale >= 4:
        lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--4"></div>')
        lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--5"></div>')
    if scale >= 5:
        lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--6"></div>')
        lines.append('        <div class="ct-hero__decor-shape ct-hero__decor-shape--7"></div>')
        lines.append('        <div class="ct-hero__decor-orbit ct-hero__decor-orbit--1">')
        lines.append('          <div class="ct-hero__decor-orbit-dot"></div>')
        lines.append("        </div>")
        lines.append('        <div class="ct-hero__decor-orbit ct-hero__decor-orbit--2">')
        lines.append('          <div class="ct-hero__decor-orbit-dot"></div>')
        lines.append("        </div>")
    lines.append("      </div>")
    lines.append("    </section>")
    return "\n".join(lines)


# ── Canvas ─────────────────────────────────────────────────────────────────

def _gen_canvas(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <main class="ct-main" id="main-content"{a(scale, role="main")}>'
    )
    lines.append(
        f'      <section class="ct-canvas-area" id="ct-canvas-area"'
        f' data-panel="canvas"{a(scale, aria_label="Game canvas area")}>'
    )
    lines.append("")
    # Layered canvases
    lines.append('        <div class="ct-canvas-stack">')
    canvas_layers = [
        ("terrain", "Terrain layer"),
        ("units", "Units layer"),
        ("effects", "Visual effects layer"),
        ("ui", "UI overlay layer"),
    ]
    for layer_id, label in canvas_layers:
        aria_lbl = a(scale, aria_label=label)
        lines.append(
            f'          <canvas id="ct-canvas-{layer_id}" class="ct-canvas ct-canvas--{layer_id}"'
            f' data-layer="{layer_id}"{aria_lbl}></canvas>'
        )
    if scale >= 5:
        lines.append(
            '          <canvas id="ct-canvas-debug" class="ct-canvas ct-canvas--debug"'
            ' data-layer="debug" hidden aria-hidden="true"></canvas>'
        )
    lines.append("        </div>")
    lines.append("")

    # HUD overlay
    lines.append(f'        <div class="ct-hud"{a(scale, role="status", aria_live="polite")}>')
    lines.append("")
    # Turn indicator
    lines.append('          <div class="ct-hud__turn" data-hud="turn">')
    lines.append('            <span class="ct-hud__turn-label">Turn</span>')
    lines.append('            <span class="ct-hud__turn-value" data-bind="turn-number">1</span>')
    lines.append("          </div>")
    lines.append("")
    # Player info
    lines.append('          <div class="ct-hud__player" data-hud="player">')
    lines.append('            <div class="ct-hud__player-avatar">')
    lines.append('              <div class="ct-hud__player-color" data-bind="player-color"></div>')
    lines.append("            </div>")
    lines.append('            <div class="ct-hud__player-details">')
    lines.append('              <span class="ct-hud__player-name" data-bind="player-name">Player 1</span>')
    lines.append('              <span class="ct-hud__player-score" data-bind="player-score">0</span>')
    lines.append("            </div>")
    lines.append("          </div>")
    lines.append("")
    # Resource bars
    lines.append('          <div class="ct-hud__resources" data-hud="resources">')
    resources = [
        ("energy", "Energy", "100", "#00cec9"),
        ("influence", "Influence", "50", "#6c5ce7"),
        ("territory", "Territory", "0%", "#fdcb6e"),
    ]
    for res_id, label, default, color in resources:
        lines.append(f'            <div class="ct-hud__resource ct-hud__resource--{res_id}">')
        lines.append(f'              <span class="ct-hud__resource-label">{label}</span>')
        if scale >= 4:
            lines.append(
                f'              <div class="ct-hud__resource-bar" role="progressbar"'
                f' aria-valuenow="{default.replace("%","")}" aria-valuemin="0" aria-valuemax="100"'
                f' aria-label="{label} level">'
            )
        else:
            lines.append(f'              <div class="ct-hud__resource-bar">')
        lines.append(
            f'                <div class="ct-hud__resource-fill" data-bind="resource-{res_id}"'
            f' style="width:{default};background:{color}"></div>'
        )
        lines.append("              </div>")
        lines.append(f'              <span class="ct-hud__resource-value" data-bind="resource-{res_id}-text">{default}</span>')
        lines.append("            </div>")
    lines.append("          </div>")
    if scale >= 4:
        lines.append("")
        lines.append('          <div class="ct-hud__timer" data-hud="timer">')
        lines.append('            <span class="ct-hud__timer-icon">⏱</span>')
        lines.append('            <span class="ct-hud__timer-value" data-bind="timer">0:00</span>')
        lines.append("          </div>")
    if scale >= 5:
        lines.append("")
        lines.append('          <div class="ct-hud__notifications" data-hud="notifications" aria-live="assertive">')
        lines.append('            <div class="ct-hud__notification-slot" data-bind="notification"></div>')
        lines.append("          </div>")
    lines.append("        </div>")
    lines.append("")

    # Action bar
    lines.append(f'        <div class="ct-action-bar"{a(scale, role="toolbar", aria_label="Game tools")}>')
    tools = [
        ("brush", "Brush", "Paint territory"),
        ("eraser", "Eraser", "Erase territory"),
        ("move", "Move", "Pan the map"),
        ("eye", "Inspect", "Inspect tile"),
        ("zap", "Attack", "Attack territory"),
        ("shield", "Defend", "Defend territory"),
        ("flag", "Claim", "Claim territory"),
    ]
    for tool_icon, label, tooltip in tools:
        btn_aria = a(scale, aria_label=tooltip)
        lines.append(
            f'          <button class="ct-action-bar__btn" data-action="tool-{tool_icon}"'
            f' data-tooltip="{tooltip}"{btn_aria}>'
        )
        lines.append(f"            {_icon_or_span(tool_icon, label, scale)}")
        if scale >= 3:
            lines.append(f'            <span class="ct-action-bar__label">{label}</span>')
        lines.append("          </button>")
    lines.append("")
    # Separator
    lines.append('          <div class="ct-action-bar__separator"></div>')
    lines.append("")
    # Utility buttons
    util_btns = [
        ("undo", "Undo", "Undo last action"),
        ("redo", "Redo", "Redo last action"),
    ]
    for btn_icon, label, tooltip in util_btns:
        btn_aria = a(scale, aria_label=tooltip)
        lines.append(
            f'          <button class="ct-action-bar__btn ct-action-bar__btn--util"'
            f' data-action="{btn_icon}" data-tooltip="{tooltip}"{btn_aria}>'
        )
        lines.append(f"            {_icon_or_span(btn_icon, label, scale)}")
        lines.append("          </button>")
    if scale >= 4:
        lines.append("")
        lines.append('          <div class="ct-action-bar__separator"></div>')
        lines.append("")
        extra_btns = [
            ("grid", "Grid", "Toggle grid overlay"),
            ("maximize", "Fullscreen", "Toggle fullscreen"),
        ]
        for btn_icon, label, tooltip in extra_btns:
            btn_aria = a(scale, aria_label=tooltip)
            lines.append(
                f'          <button class="ct-action-bar__btn ct-action-bar__btn--util"'
                f' data-action="{btn_icon}" data-tooltip="{tooltip}"{btn_aria}>'
            )
            lines.append(f"            {_icon_or_span(btn_icon, label, scale)}")
            lines.append("          </button>")
    lines.append("        </div>")

    if scale >= 5:
        lines.append("")
        lines.append("        <!-- Context menu (shown on right-click) -->")
        lines.append('        <div class="ct-context-menu" id="ct-context-menu" hidden aria-hidden="true">')
        lines.append('          <ul class="ct-context-menu__list" role="menu">')
        ctx_items = [
            ("inspect-tile", "Inspect Tile"),
            ("attack-tile", "Attack"),
            ("defend-tile", "Defend"),
            ("claim-tile", "Claim"),
            ("build-structure", "Build Structure"),
            ("cancel", "Cancel"),
        ]
        for action, label in ctx_items:
            lines.append(
                f'            <li class="ct-context-menu__item" role="menuitem"'
                f' data-action="{action}">{label}</li>'
            )
        lines.append("          </ul>")
        lines.append("        </div>")

    if scale >= 5:
        lines.append("")
        lines.append("        <!-- Coordinate display -->")
        lines.append(
            '        <div class="ct-coords" data-hud="coords" aria-label="Cursor coordinates">'
        )
        lines.append('          <span class="ct-coords__x" data-bind="coord-x">0</span>')
        lines.append('          <span class="ct-coords__sep">,</span>')
        lines.append('          <span class="ct-coords__y" data-bind="coord-y">0</span>')
        lines.append("        </div>")

    if scale >= 5:
        lines.append("")
        lines.append("        <!-- Layer visibility panel -->")
        lines.append('        <div class="ct-layers-panel" data-panel="layers" aria-label="Layer visibility">')
        lines.append('          <h4 class="ct-layers-panel__title">Layers</h4>')
        layers = [
            ("terrain", "Terrain", True),
            ("units", "Units", True),
            ("effects", "Effects", True),
            ("ui", "UI Overlay", True),
            ("grid", "Grid", False),
            ("debug", "Debug", False),
        ]
        for layer_id, label, checked in layers:
            chk = " checked" if checked else ""
            lines.append(f'          <div class="ct-layers-panel__item">')
            lines.append(
                f'            <label class="ct-layers-panel__label">'
                f'<input class="ct-layers-panel__checkbox" type="checkbox"'
                f' data-layer-toggle="{layer_id}"{chk}>'
                f' {label}</label>'
            )
            lines.append(
                f'            <input class="ct-layers-panel__opacity" type="range"'
                f' min="0" max="100" value="100" data-layer-opacity="{layer_id}"'
                f' aria-label="{label} opacity">'
            )
            lines.append(f"          </div>")
        lines.append("        </div>")
        lines.append("")
        lines.append("        <!-- Secondary toolbar: brush settings -->")
        lines.append('        <div class="ct-brush-settings" data-panel="brush-settings" aria-label="Brush settings">')
        lines.append('          <h4 class="ct-brush-settings__title">Brush Settings</h4>')
        lines.append('          <div class="ct-brush-settings__control">')
        lines.append('            <label class="ct-brush-settings__label" for="ct-brush-size">Size</label>')
        lines.append(
            '            <input class="ct-range" type="range" id="ct-brush-size" min="1" max="10" value="1"'
            ' data-setting="brush-size" aria-label="Brush size">'
        )
        lines.append('            <span class="ct-brush-settings__value" data-bind="brush-size">1</span>')
        lines.append("          </div>")
        lines.append('          <div class="ct-brush-settings__control">')
        lines.append('            <label class="ct-brush-settings__label" for="ct-brush-shape">Shape</label>')
        lines.append('            <select class="ct-select ct-select--sm" id="ct-brush-shape" data-setting="brush-shape">')
        for shape in ["Circle", "Square", "Diamond", "Cross"]:
            lines.append(f'              <option value="{shape.lower()}">{shape}</option>')
        lines.append("            </select>")
        lines.append("          </div>")
        lines.append('          <div class="ct-brush-settings__control">')
        lines.append('            <label class="ct-brush-settings__label" for="ct-brush-strength">Strength</label>')
        lines.append(
            '            <input class="ct-range" type="range" id="ct-brush-strength" min="1" max="100" value="50"'
            ' data-setting="brush-strength" aria-label="Brush strength">'
        )
        lines.append('            <span class="ct-brush-settings__value" data-bind="brush-strength">50%</span>')
        lines.append("          </div>")
        lines.append('          <div class="ct-brush-settings__control ct-brush-settings__control--toggle">')
        lines.append('            <label class="ct-brush-settings__label" for="ct-brush-pattern">Pattern Fill</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-brush-pattern" data-setting="brush-pattern">'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
        lines.append("        </div>")
        lines.append("")
        lines.append("        <!-- Zoom controls -->")
        lines.append('        <div class="ct-zoom-controls" data-panel="zoom" aria-label="Zoom controls">')
        lines.append('          <button class="ct-btn ct-btn--sm" data-action="zoom-in" aria-label="Zoom in">+</button>')
        lines.append('          <span class="ct-zoom-controls__level" data-bind="zoom-level">100%</span>')
        lines.append('          <button class="ct-btn ct-btn--sm" data-action="zoom-out" aria-label="Zoom out">−</button>')
        lines.append('          <button class="ct-btn ct-btn--sm" data-action="zoom-fit" aria-label="Fit to screen">⊡</button>')
        lines.append("        </div>")

    lines.append("      </section>")
    lines.append("    </main>")
    return "\n".join(lines)


# ── Sidebar ────────────────────────────────────────────────────────────────

def _gen_sidebar(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    toggle_aria = a(scale, aria_label="Toggle sidebar", aria_expanded="true", aria_controls="ct-sidebar-inner")
    lines.append(
        f'    <aside class="ct-sidebar" id="ct-sidebar" data-panel="sidebar"'
        f'{a(scale, role="complementary", aria_label="Game sidebar")}>'
    )
    lines.append(f'      <button class="ct-sidebar__toggle" data-action="toggle-sidebar"{toggle_aria}>')
    lines.append(f"        {_icon_or_span('chevron-left', '◂', scale)}")
    lines.append("      </button>")
    lines.append("")
    lines.append('      <div class="ct-sidebar__inner" id="ct-sidebar-inner">')
    lines.append("")

    # Palette selector
    lines.append(f'        <div class="ct-palette" data-panel="palette"{a(scale, role="group", aria_label="Color palette")}>')
    lines.append('          <h3 class="ct-sidebar__heading">Palette</h3>')
    lines.append('          <div class="ct-palette__swatches">')
    colors = [
        ("#6c5ce7", "Ultraviolet"),
        ("#00cec9", "Cyan"),
        ("#fdcb6e", "Gold"),
        ("#e17055", "Coral"),
        ("#00b894", "Emerald"),
        ("#d63031", "Crimson"),
        ("#0984e3", "Cerulean"),
        ("#e84393", "Fuchsia"),
    ]
    if scale >= 4:
        colors += [
            ("#2d3436", "Charcoal"),
            ("#dfe6e9", "Cloud"),
            ("#fd79a8", "Rose"),
            ("#55efc4", "Mint"),
        ]
    if scale >= 5:
        colors += [
            ("#a29bfe", "Lavender"),
            ("#ffeaa7", "Vanilla"),
            ("#fab1a0", "Salmon"),
            ("#74b9ff", "Sky"),
        ]
    for hex_val, name in colors:
        swatch_aria = a(scale, aria_label=f"Select {name} ({hex_val})")
        lines.append(
            f'            <button class="ct-palette__swatch" data-color="{hex_val}"'
            f' style="background:{hex_val}" data-tooltip="{name}"{swatch_aria}></button>'
        )
    lines.append("          </div>")
    if scale >= 4:
        lines.append('          <div class="ct-palette__custom">')
        lines.append(
            '            <label class="ct-palette__custom-label" for="ct-custom-color">Custom</label>'
        )
        lines.append(
            '            <input class="ct-palette__custom-input" type="color" id="ct-custom-color"'
            ' value="#6c5ce7" data-action="custom-color">'
        )
        lines.append("          </div>")
    if scale >= 5:
        lines.append('          <div class="ct-palette__presets">')
        lines.append('            <span class="ct-palette__presets-label">Presets:</span>')
        presets = ["Sunset", "Ocean", "Forest", "Neon", "Pastel"]
        for preset in presets:
            lines.append(
                f'            <button class="ct-palette__preset-btn" data-action="load-preset"'
                f' data-preset="{preset.lower()}">{preset}</button>'
            )
        lines.append("          </div>")
    lines.append("        </div>")
    lines.append("")

    # Territory info panel
    lines.append(f'        <div class="ct-territory-info" data-panel="territory-info"{a(scale, aria_label="Territory information")}>')
    lines.append('          <h3 class="ct-sidebar__heading">Territory Info</h3>')
    lines.append('          <div class="ct-territory-info__content">')
    lines.append('            <div class="ct-territory-info__row">')
    lines.append('              <span class="ct-territory-info__label">Owner</span>')
    lines.append('              <span class="ct-territory-info__value" data-bind="territory-owner">—</span>')
    lines.append("            </div>")
    lines.append('            <div class="ct-territory-info__row">')
    lines.append('              <span class="ct-territory-info__label">Strength</span>')
    lines.append('              <span class="ct-territory-info__value" data-bind="territory-strength">—</span>')
    lines.append("            </div>")
    lines.append('            <div class="ct-territory-info__row">')
    lines.append('              <span class="ct-territory-info__label">Type</span>')
    lines.append('              <span class="ct-territory-info__value" data-bind="territory-type">—</span>')
    lines.append("            </div>")
    lines.append('            <div class="ct-territory-info__row">')
    lines.append('              <span class="ct-territory-info__label">Coordinates</span>')
    lines.append('              <span class="ct-territory-info__value" data-bind="territory-coords">—</span>')
    lines.append("            </div>")
    if scale >= 4:
        lines.append('            <div class="ct-territory-info__row">')
        lines.append('              <span class="ct-territory-info__label">Resources</span>')
        lines.append('              <span class="ct-territory-info__value" data-bind="territory-resources">—</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-territory-info__row">')
        lines.append('              <span class="ct-territory-info__label">Adjacent</span>')
        lines.append('              <span class="ct-territory-info__value" data-bind="territory-adjacent">—</span>')
        lines.append("            </div>")
    if scale >= 5:
        lines.append('            <div class="ct-territory-info__row">')
        lines.append('              <span class="ct-territory-info__label">Terrain</span>')
        lines.append('              <span class="ct-territory-info__value" data-bind="territory-terrain">—</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-territory-info__row">')
        lines.append('              <span class="ct-territory-info__label">Last Action</span>')
        lines.append('              <span class="ct-territory-info__value" data-bind="territory-last-action">—</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-territory-info__history">')
        lines.append('              <h4 class="ct-territory-info__subheading">History</h4>')
        lines.append('              <ul class="ct-territory-info__history-list" data-bind="territory-history">')
        lines.append('                <li class="ct-territory-info__history-item">No history yet</li>')
        lines.append("              </ul>")
        lines.append("            </div>")
    lines.append("          </div>")
    lines.append("        </div>")
    lines.append("")

    # Minimap
    lines.append(f'        <div class="ct-minimap" data-panel="minimap"{a(scale, aria_label="Minimap")}>')
    lines.append('          <h3 class="ct-sidebar__heading">Minimap</h3>')
    lines.append('          <div class="ct-minimap__container">')
    lines.append('            <canvas id="ct-minimap-canvas" class="ct-minimap__canvas" width="200" height="200"></canvas>')
    lines.append('            <div class="ct-minimap__viewport" data-bind="minimap-viewport"></div>')
    lines.append("          </div>")
    if scale >= 4:
        lines.append('          <div class="ct-minimap__controls">')
        lines.append(
            '            <button class="ct-btn ct-btn--sm" data-action="minimap-zoom-in"'
            f'{a(scale, aria_label="Zoom in")}>+</button>'
        )
        lines.append(
            '            <button class="ct-btn ct-btn--sm" data-action="minimap-zoom-out"'
            f'{a(scale, aria_label="Zoom out")}>−</button>'
        )
        lines.append(
            '            <button class="ct-btn ct-btn--sm" data-action="minimap-center"'
            f'{a(scale, aria_label="Center view")}>⊙</button>'
        )
        lines.append("          </div>")
    lines.append("        </div>")

    if scale >= 5:
        lines.append("")
        lines.append('        <div class="ct-scoreboard" data-panel="scoreboard" aria-label="Scoreboard">')
        lines.append('          <h3 class="ct-sidebar__heading">Scoreboard</h3>')
        lines.append('          <table class="ct-scoreboard__table">')
        lines.append("            <thead>")
        lines.append("              <tr>")
        lines.append('                <th scope="col">Player</th>')
        lines.append('                <th scope="col">Score</th>')
        lines.append('                <th scope="col">Land</th>')
        lines.append("              </tr>")
        lines.append("            </thead>")
        lines.append('            <tbody data-bind="scoreboard-body">')
        lines.append("              <tr>")
        lines.append('                <td><span class="ct-scoreboard__color" style="background:#6c5ce7"></span> Player 1</td>')
        lines.append("                <td>0</td>")
        lines.append("                <td>0%</td>")
        lines.append("              </tr>")
        lines.append("              <tr>")
        lines.append('                <td><span class="ct-scoreboard__color" style="background:#00cec9"></span> Player 2</td>')
        lines.append("                <td>0</td>")
        lines.append("                <td>0%</td>")
        lines.append("              </tr>")
        lines.append("            </tbody>")
        lines.append("          </table>")
        lines.append("        </div>")
        lines.append("")
        lines.append('        <div class="ct-game-log" data-panel="game-log" aria-label="Game event log">')
        lines.append('          <h3 class="ct-sidebar__heading">Game Log</h3>')
        lines.append('          <div class="ct-game-log__filters">')
        for log_type in ["All", "Combat", "Capture", "Resource", "System"]:
            active = " ct-game-log__filter--active" if log_type == "All" else ""
            lines.append(
                f'            <button class="ct-game-log__filter{active}"'
                f' data-log-filter="{log_type.lower()}">{log_type}</button>'
            )
        lines.append("          </div>")
        lines.append('          <ul class="ct-game-log__list" data-bind="game-log" role="log" aria-live="polite">')
        lines.append('            <li class="ct-game-log__entry ct-game-log__entry--system">')
        lines.append('              <span class="ct-game-log__time">0:00</span>')
        lines.append('              <span class="ct-game-log__text">Game started. Good luck!</span>')
        lines.append("            </li>")
        lines.append("          </ul>")
        lines.append("        </div>")
        lines.append("")
        lines.append('        <div class="ct-quick-actions" data-panel="quick-actions" aria-label="Quick actions">')
        lines.append('          <h3 class="ct-sidebar__heading">Quick Actions</h3>')
        lines.append('          <div class="ct-quick-actions__grid">')
        quick_actions = [
            ("end-turn", "End Turn", "play"),
            ("save-game", "Save", "download"),
            ("open-settings", "Settings", "settings"),
            ("open-help", "Help", "help"),
        ]
        for action, label, icon_name in quick_actions:
            lines.append(f'            <button class="ct-quick-actions__btn" data-action="{action}" aria-label="{label}">')
            lines.append(f"              {_SVG_ICONS.get(icon_name, '')}")
            lines.append(f'              <span>{label}</span>')
            lines.append(f"            </button>")
        lines.append("          </div>")
        lines.append("        </div>")


# ── Modals ─────────────────────────────────────────────────────────────────

def _gen_modals(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append("    <!-- ═══ Modals ═══ -->")
    lines.append(f'    <div class="ct-modal-backdrop" id="ct-modal-backdrop" hidden{a(scale, aria_hidden="true")}></div>')
    lines.append("")

    # ── Settings modal ──
    lines.append(
        f'    <div class="ct-modal" id="ct-modal-settings" hidden data-modal="settings"'
        f'{a(scale, role="dialog", aria_modal="true", aria_labelledby="ct-modal-settings-title")}>'
    )
    lines.append('      <div class="ct-modal__header">')
    lines.append('        <h2 class="ct-modal__title" id="ct-modal-settings-title">Settings</h2>')
    close_aria = a(scale, aria_label="Close settings")
    lines.append(
        f'        <button class="ct-modal__close" data-action="close-modal"{close_aria}>'
        f'{_icon_or_span("close", "✕", scale)}</button>'
    )
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__body">')
    lines.append('        <p>Configure game settings here.</p>')
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__footer">')
    lines.append('        <button class="ct-btn ct-btn--secondary" data-action="close-modal">Cancel</button>')
    lines.append('        <button class="ct-btn ct-btn--primary" data-action="save-settings">Save</button>')
    lines.append("      </div>")
    lines.append("    </div>")
    lines.append("")

    # ── Gallery preview modal ──
    lines.append(
        f'    <div class="ct-modal ct-modal--lg" id="ct-modal-gallery" hidden data-modal="gallery-preview"'
        f'{a(scale, role="dialog", aria_modal="true", aria_labelledby="ct-modal-gallery-title")}>'
    )
    lines.append('      <div class="ct-modal__header">')
    lines.append('        <h2 class="ct-modal__title" id="ct-modal-gallery-title">Gallery Preview</h2>')
    lines.append(
        f'        <button class="ct-modal__close" data-action="close-modal"'
        f'{a(scale, aria_label="Close preview")}>{_icon_or_span("close", "✕", scale)}</button>'
    )
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__body">')
    lines.append('        <div class="ct-gallery-preview">')
    lines.append('          <div class="ct-gallery-preview__image">')
    lines.append('            <canvas id="ct-gallery-preview-canvas" class="ct-gallery-preview__canvas"></canvas>')
    lines.append("          </div>")
    lines.append('          <div class="ct-gallery-preview__info">')
    lines.append('            <h3 class="ct-gallery-preview__name" data-bind="preview-name">—</h3>')
    lines.append('            <p class="ct-gallery-preview__description" data-bind="preview-desc">—</p>')
    lines.append('            <div class="ct-gallery-preview__meta">')
    lines.append('              <span data-bind="preview-author">—</span>')
    lines.append('              <span data-bind="preview-date">—</span>')
    lines.append("            </div>")
    if scale >= 4:
        lines.append('            <div class="ct-gallery-preview__tags" data-bind="preview-tags"></div>')
    lines.append("          </div>")
    lines.append("        </div>")
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__footer">')
    if scale >= 5:
        lines.append(
            '        <button class="ct-btn ct-btn--icon" data-action="preview-prev"'
            f' aria-label="Previous item">{_SVG_ICONS["chevron-left"]}</button>'
        )
    lines.append('        <button class="ct-btn ct-btn--secondary" data-action="close-modal">Close</button>')
    lines.append(
        f'        <button class="ct-btn ct-btn--primary" data-action="download-artwork"'
        f'{a(scale, aria_label="Download artwork")}>'
        f'{_icon_or_span("download", "↓", scale)} Download</button>'
    )
    if scale >= 5:
        lines.append(
            '        <button class="ct-btn ct-btn--icon" data-action="preview-next"'
            f' aria-label="Next item">{_SVG_ICONS["chevron-right"]}</button>'
        )
    lines.append("      </div>")
    lines.append("    </div>")
    lines.append("")

    # ── Game over modal ──
    lines.append(
        f'    <div class="ct-modal ct-modal--centered" id="ct-modal-gameover" hidden data-modal="gameover"'
        f'{a(scale, role="alertdialog", aria_modal="true", aria_labelledby="ct-modal-gameover-title")}>'
    )
    lines.append('      <div class="ct-modal__header">')
    lines.append('        <h2 class="ct-modal__title" id="ct-modal-gameover-title">Game Over</h2>')
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__body">')
    lines.append('        <div class="ct-gameover">')
    if scale >= 5:
        lines.append("          " + _SVG_ICONS["trophy"])
    lines.append('          <p class="ct-gameover__message" data-bind="gameover-message">Victory!</p>')
    lines.append('          <div class="ct-gameover__scores">')
    lines.append('            <div class="ct-gameover__score">')
    lines.append('              <span class="ct-gameover__score-label">Final Score</span>')
    lines.append('              <span class="ct-gameover__score-value" data-bind="gameover-score">0</span>')
    lines.append("            </div>")
    lines.append('            <div class="ct-gameover__score">')
    lines.append('              <span class="ct-gameover__score-label">Territory</span>')
    lines.append('              <span class="ct-gameover__score-value" data-bind="gameover-territory">0%</span>')
    lines.append("            </div>")
    lines.append('            <div class="ct-gameover__score">')
    lines.append('              <span class="ct-gameover__score-label">Turns</span>')
    lines.append('              <span class="ct-gameover__score-value" data-bind="gameover-turns">0</span>')
    lines.append("            </div>")
    lines.append("          </div>")
    if scale >= 4:
        lines.append('          <div class="ct-gameover__stats">')
        lines.append('            <h4>Match Statistics</h4>')
        lines.append('            <div class="ct-gameover__stat-row">')
        lines.append('              <span>Tiles Captured</span>')
        lines.append('              <span data-bind="gameover-tiles-captured">0</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-gameover__stat-row">')
        lines.append('              <span>Tiles Lost</span>')
        lines.append('              <span data-bind="gameover-tiles-lost">0</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-gameover__stat-row">')
        lines.append('              <span>Attacks Made</span>')
        lines.append('              <span data-bind="gameover-attacks">0</span>')
        lines.append("            </div>")
        lines.append('            <div class="ct-gameover__stat-row">')
        lines.append('              <span>Defenses Held</span>')
        lines.append('              <span data-bind="gameover-defenses">0</span>')
        lines.append("            </div>")
        lines.append("          </div>")
    lines.append("        </div>")
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__footer">')
    lines.append('        <button class="ct-btn ct-btn--secondary" data-action="return-menu">Main Menu</button>')
    lines.append('        <button class="ct-btn ct-btn--primary" data-action="play-again">Play Again</button>')
    lines.append("      </div>")
    lines.append("    </div>")
    lines.append("")

    # ── Help / tutorial modal ──
    lines.append(
        f'    <div class="ct-modal ct-modal--lg" id="ct-modal-help" hidden data-modal="help"'
        f'{a(scale, role="dialog", aria_modal="true", aria_labelledby="ct-modal-help-title")}>'
    )
    lines.append('      <div class="ct-modal__header">')
    lines.append('        <h2 class="ct-modal__title" id="ct-modal-help-title">How to Play</h2>')
    lines.append(
        f'        <button class="ct-modal__close" data-action="close-modal"'
        f'{a(scale, aria_label="Close help")}>{_icon_or_span("close", "✕", scale)}</button>'
    )
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__body">')
    lines.append('        <div class="ct-help">')
    help_sections = [
        ("Getting Started", "Select a colour from the palette, then click tiles on the grid to claim territory."),
        ("Combat", "Attack adjacent enemy tiles to capture them. Strength determines the outcome."),
        ("Scoring", "Points are awarded for territory size, contiguous regions, and strategic positions."),
    ]
    if scale >= 4:
        help_sections += [
            ("Resources", "Energy is spent on actions. Influence grows with territory. Manage both wisely."),
            ("Advanced Tactics", "Chain captures for combo bonuses. Defend chokepoints to protect large regions."),
        ]
    if scale >= 5:
        help_sections += [
            ("Keyboard Shortcuts", "B = Brush, E = Eraser, M = Move, Space = End turn, Esc = Menu"),
            ("Colour Theory", "Adjacent complementary colours gain a strength bonus. Plan your palette!"),
        ]
    for heading, text in help_sections:
        lines.append(f'          <div class="ct-help__section">')
        lines.append(f'            <h3 class="ct-help__heading">{heading}</h3>')
        lines.append(f'            <p class="ct-help__text">{text}</p>')
        lines.append("          </div>")
    lines.append("        </div>")
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__footer">')
    lines.append('        <button class="ct-btn ct-btn--primary" data-action="close-modal">Got it</button>')
    lines.append("      </div>")
    lines.append("    </div>")
    lines.append("")

    # ── Confirmation dialog ──
    lines.append(
        f'    <div class="ct-modal ct-modal--sm" id="ct-modal-confirm" hidden data-modal="confirm"'
        f'{a(scale, role="alertdialog", aria_modal="true", aria_labelledby="ct-modal-confirm-title", aria_describedby="ct-modal-confirm-desc")}>'
    )
    lines.append('      <div class="ct-modal__header">')
    lines.append('        <h2 class="ct-modal__title" id="ct-modal-confirm-title">Confirm</h2>')
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__body">')
    lines.append('        <p id="ct-modal-confirm-desc" data-bind="confirm-message">Are you sure?</p>')
    lines.append("      </div>")
    lines.append('      <div class="ct-modal__footer">')
    lines.append('        <button class="ct-btn ct-btn--secondary" data-action="confirm-cancel">Cancel</button>')
    lines.append('        <button class="ct-btn ct-btn--danger" data-action="confirm-ok">Confirm</button>')
    lines.append("      </div>")
    lines.append("    </div>")

    if scale >= 5:
        lines.append("")
        # ── New game modal ──
        lines.append(
            '    <div class="ct-modal" id="ct-modal-newgame" hidden data-modal="newgame"'
            ' role="dialog" aria-modal="true" aria-labelledby="ct-modal-newgame-title">'
        )
        lines.append('      <div class="ct-modal__header">')
        lines.append('        <h2 class="ct-modal__title" id="ct-modal-newgame-title">New Game</h2>')
        lines.append(
            f'        <button class="ct-modal__close" data-action="close-modal"'
            f' aria-label="Close">{_SVG_ICONS["close"]}</button>'
        )
        lines.append("      </div>")
        lines.append('      <div class="ct-modal__body">')
        lines.append('        <div class="ct-newgame">')
        lines.append('          <div class="ct-newgame__field">')
        lines.append('            <label class="ct-label" for="ct-newgame-grid">Grid Size</label>')
        lines.append('            <select class="ct-select" id="ct-newgame-grid" data-setting="grid-size">')
        for size in ["8×8", "12×12", "16×16", "24×24"]:
            lines.append(f'              <option value="{size}">{size}</option>')
        lines.append("            </select>")
        lines.append("          </div>")
        lines.append('          <div class="ct-newgame__field">')
        lines.append('            <label class="ct-label" for="ct-newgame-players">Players</label>')
        lines.append('            <select class="ct-select" id="ct-newgame-players" data-setting="players">')
        for n in range(2, 5):
            lines.append(f'              <option value="{n}">{n} Players</option>')
        lines.append("            </select>")
        lines.append("          </div>")
        lines.append('          <div class="ct-newgame__field">')
        lines.append('            <label class="ct-label" for="ct-newgame-difficulty">AI Difficulty</label>')
        lines.append('            <select class="ct-select" id="ct-newgame-difficulty" data-setting="difficulty">')
        for diff in ["Easy", "Normal", "Hard", "Expert"]:
            lines.append(f'              <option value="{diff.lower()}">{diff}</option>')
        lines.append("            </select>")
        lines.append("          </div>")
        lines.append("        </div>")
        lines.append("      </div>")
        lines.append('      <div class="ct-modal__footer">')
        lines.append('        <button class="ct-btn ct-btn--secondary" data-action="close-modal">Cancel</button>')
        lines.append('        <button class="ct-btn ct-btn--primary" data-action="start-new-game">Start Game</button>')
        lines.append("      </div>")
        lines.append("    </div>")

    return "\n".join(lines)


# ── Gallery ────────────────────────────────────────────────────────────────

def _gen_gallery(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <section class="ct-gallery" id="gallery" data-panel="gallery"'
        f'{a(scale, aria_label="Artwork gallery")}>'
    )
    lines.append('      <div class="ct-gallery__header">')
    lines.append('        <h2 class="ct-gallery__title">Gallery</h2>')
    if scale >= 4:
        lines.append('        <div class="ct-gallery__search">')
        lines.append(
            f'          <label class="ct-sr-only" for="ct-gallery-search">Search gallery</label>'
            if scale >= 5 else ""
        )
        lines.append(
            f'          {_icon("search", scale)}'
            f'<input class="ct-input ct-gallery__search-input" type="search"'
            f' id="ct-gallery-search" placeholder="Search…" data-action="gallery-search"'
            f'{a(scale, aria_label="Search gallery")}>'
        )
        lines.append("        </div>")
    lines.append("      </div>")
    lines.append("")

    # Filter bar
    lines.append(f'      <div class="ct-gallery__filters"{a(scale, role="toolbar", aria_label="Gallery filters")}>')
    filters = ["All", "Landscapes", "Abstract", "Portraits", "Fractals", "Cellular"]
    for f_name in filters:
        active = ' ct-gallery__filter--active' if f_name == "All" else ""
        lines.append(
            f'        <button class="ct-gallery__filter{active}" data-filter="{f_name.lower()}">{f_name}</button>'
        )
    if scale >= 5:
        lines.append('        <div class="ct-gallery__sort">')
        lines.append('          <label class="ct-sr-only" for="ct-gallery-sort">Sort by</label>')
        lines.append('          <select class="ct-select ct-select--sm" id="ct-gallery-sort" data-action="gallery-sort">')
        for opt in ["Newest", "Popular", "Name A-Z", "Name Z-A"]:
            lines.append(f'            <option value="{opt.lower().replace(" ", "-")}">{opt}</option>')
        lines.append("          </select>")
        lines.append("        </div>")
    lines.append("      </div>")
    lines.append("")

    # Grid of cards
    lines.append('      <div class="ct-gallery__grid" data-bind="gallery-grid">')
    card_count = 4 if scale <= 3 else (6 if scale == 4 else 8)
    for i in range(1, card_count + 1):
        lines.append(f'        <article class="ct-gallery__card" data-gallery-id="artwork-{i}">')
        lines.append('          <div class="ct-gallery__card-image">')
        lines.append(f'            <canvas class="ct-gallery__card-canvas" data-artwork="artwork-{i}"></canvas>')
        if scale >= 4:
            lines.append('            <div class="ct-gallery__card-overlay">')
            lines.append(
                f'              <button class="ct-btn ct-btn--icon ct-gallery__card-preview"'
                f' data-action="preview-artwork" data-artwork-id="artwork-{i}"'
                f'{a(scale, aria_label=f"Preview artwork {i}")}>'
                f'{_icon_or_span("eye", "👁", scale)}</button>'
            )
            lines.append("            </div>")
        lines.append("          </div>")
        lines.append('          <div class="ct-gallery__card-body">')
        lines.append(f'            <h3 class="ct-gallery__card-title">Artwork {i}</h3>')
        lines.append(f'            <p class="ct-gallery__card-meta">Generated · 2024</p>')
        if scale >= 5:
            lines.append('            <div class="ct-gallery__card-tags">')
            lines.append('              <span class="ct-tag">generative</span>')
            lines.append('              <span class="ct-tag">abstract</span>')
            lines.append("            </div>")
        lines.append("          </div>")
        if scale >= 5:
            lines.append('          <div class="ct-gallery__card-actions">')
            lines.append(
                f'            <button class="ct-btn ct-btn--sm ct-btn--icon" data-action="like-artwork"'
                f' data-artwork-id="artwork-{i}" aria-label="Like artwork {i}">♥</button>'
            )
            lines.append(
                f'            <button class="ct-btn ct-btn--sm ct-btn--icon" data-action="download-artwork"'
                f' data-artwork-id="artwork-{i}" aria-label="Download artwork {i}">'
                f'{_SVG_ICONS["download"]}</button>'
            )
            lines.append("          </div>")
        lines.append("        </article>")
    lines.append("      </div>")
    lines.append("")

    # Pagination
    lines.append(f'      <nav class="ct-gallery__pagination"{a(scale, aria_label="Gallery pagination")}>')
    lines.append(
        f'        <button class="ct-gallery__page-btn ct-gallery__page-btn--prev" data-action="gallery-prev"'
        f' disabled{a(scale, aria_label="Previous page")}>'
        f'{_icon_or_span("chevron-left", "‹", scale)}</button>'
    )
    lines.append('        <span class="ct-gallery__page-info">')
    lines.append('          Page <span data-bind="gallery-page">1</span> of <span data-bind="gallery-total">1</span>')
    lines.append("        </span>")
    lines.append(
        f'        <button class="ct-gallery__page-btn ct-gallery__page-btn--next" data-action="gallery-next"'
        f'{a(scale, aria_label="Next page")}>'
        f'{_icon_or_span("chevron-right", "›", scale)}</button>'
    )
    lines.append("      </nav>")
    lines.append("    </section>")
    return "\n".join(lines)


# ── Tutorial ───────────────────────────────────────────────────────────────

def _gen_tutorial(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <div class="ct-tutorial-overlay" id="ct-tutorial" hidden data-panel="tutorial"'
        f'{a(scale, role="dialog", aria_modal="true", aria_label="Tutorial overlay")}>'
    )
    lines.append("")
    # Spotlight
    lines.append('      <div class="ct-tutorial__spotlight" data-tutorial="spotlight"></div>')
    lines.append("")
    # Step card
    lines.append('      <div class="ct-tutorial__card" data-tutorial="card">')
    lines.append('        <div class="ct-tutorial__card-header">')
    lines.append('          <span class="ct-tutorial__step-badge" data-bind="tutorial-step">1 / 5</span>')
    lines.append('          <h3 class="ct-tutorial__card-title" data-bind="tutorial-title">Welcome</h3>')
    lines.append("        </div>")
    lines.append('        <div class="ct-tutorial__card-body">')
    lines.append(
        '          <p class="ct-tutorial__card-text" data-bind="tutorial-text">'
        "Welcome to Chromatic Territories! Let's learn the basics.</p>"
    )
    if scale >= 5:
        lines.append('          <div class="ct-tutorial__card-image" data-bind="tutorial-image"></div>')
    lines.append("        </div>")
    lines.append('        <div class="ct-tutorial__card-footer">')
    lines.append(
        f'          <button class="ct-btn ct-btn--sm ct-btn--ghost" data-action="tutorial-skip"'
        f'{a(scale, aria_label="Skip tutorial")}>Skip</button>'
    )
    lines.append('          <div class="ct-tutorial__card-nav">')
    lines.append(
        f'            <button class="ct-btn ct-btn--sm" data-action="tutorial-prev" disabled'
        f'{a(scale, aria_label="Previous step")}>'
        f'{_icon_or_span("chevron-left", "← Back", scale)}</button>'
    )
    lines.append(
        f'            <button class="ct-btn ct-btn--sm ct-btn--primary" data-action="tutorial-next"'
        f'{a(scale, aria_label="Next step")}>'
        f'{_icon_or_span("chevron-right", "Next →", scale)}</button>'
    )
    lines.append("          </div>")
    lines.append("        </div>")
    lines.append("      </div>")
    lines.append("")

    # Dot indicators
    step_count = 5 if scale < 5 else 8
    lines.append(f'      <div class="ct-tutorial__dots"{a(scale, role="tablist", aria_label="Tutorial steps")}>')
    for i in range(1, step_count + 1):
        active = " ct-tutorial__dot--active" if i == 1 else ""
        dot_aria = a(scale, role="tab", aria_selected="true" if i == 1 else "false", aria_label=f"Step {i}")
        lines.append(
            f'        <button class="ct-tutorial__dot{active}" data-step="{i}"{dot_aria}></button>'
        )
    lines.append("      </div>")

    if scale >= 5:
        lines.append("")
        lines.append("      <!-- Tutorial step content (hidden, loaded by JS) -->")
        steps = [
            ("Welcome", "Welcome to Chromatic Territories! This tutorial will guide you through the basics of gameplay."),
            ("Select a Colour", "Choose a colour from the palette on the left. This will be your territory colour."),
            ("Claim Territory", "Click on empty tiles to claim them. Each claim costs energy."),
            ("Attack", "Click on enemy tiles adjacent to your territory to attack. Higher strength wins."),
            ("Defend", "Fortify your tiles to increase their defense strength against attacks."),
            ("Resources", "Manage your Energy and Influence carefully. Energy recharges each turn."),
            ("Scoring", "Score points for territory size and contiguous regions. Biggest region wins bonus!"),
            ("Victory", "The game ends when the board is full. Highest score wins. Good luck!"),
        ]
        lines.append('      <div class="ct-tutorial__steps-data" hidden aria-hidden="true">')
        for i, (title, text) in enumerate(steps, 1):
            lines.append(f'        <div class="ct-tutorial__step-data" data-step="{i}">')
            lines.append(f'          <span data-field="title">{title}</span>')
            lines.append(f'          <span data-field="text">{text}</span>')
            lines.append("        </div>")
        lines.append("      </div>")

    lines.append("    </div>")
    return "\n".join(lines)


# ── Settings panel ─────────────────────────────────────────────────────────

def _gen_settings(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <section class="ct-settings" id="settings" data-panel="settings"'
        f'{a(scale, aria_label="Game settings")}>'
    )
    lines.append('      <div class="ct-settings__header">')
    lines.append('        <h2 class="ct-settings__title">Settings</h2>')
    lines.append("      </div>")
    lines.append('      <div class="ct-settings__body">')
    lines.append("")

    # ── Audio section ──
    lines.append(
        f'        <fieldset class="ct-settings__section" data-settings-section="audio"'
        f'{a(scale, role="group", aria_labelledby="ct-settings-audio-legend")}>'
    )
    if scale >= 5:
        lines.append(f'          <legend class="ct-settings__legend" id="ct-settings-audio-legend">{_SVG_ICONS["volume"]} Audio</legend>')
    else:
        lines.append('          <legend class="ct-settings__legend" id="ct-settings-audio-legend">Audio</legend>')
    # Master volume
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-master-vol">Master Volume</label>')
    lines.append(
        '            <input class="ct-range" type="range" id="ct-setting-master-vol" min="0" max="100" value="80"'
        f' data-setting="master-volume"{a(scale, aria_valuemin="0", aria_valuemax="100", aria_valuenow="80")}>'
    )
    lines.append('            <span class="ct-settings__value" data-bind="master-volume">80%</span>')
    lines.append("          </div>")
    # Music volume
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-music-vol">Music Volume</label>')
    lines.append(
        '            <input class="ct-range" type="range" id="ct-setting-music-vol" min="0" max="100" value="60"'
        f' data-setting="music-volume">'
    )
    lines.append('            <span class="ct-settings__value" data-bind="music-volume">60%</span>')
    lines.append("          </div>")
    # SFX volume
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-sfx-vol">SFX Volume</label>')
    lines.append(
        '            <input class="ct-range" type="range" id="ct-setting-sfx-vol" min="0" max="100" value="70"'
        f' data-setting="sfx-volume">'
    )
    lines.append('            <span class="ct-settings__value" data-bind="sfx-volume">70%</span>')
    lines.append("          </div>")
    # Mute toggle
    lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-mute">Mute All</label>')
    lines.append(
        '            <label class="ct-toggle">'
        '<input class="ct-toggle__input" type="checkbox" id="ct-setting-mute" data-setting="mute">'
        '<span class="ct-toggle__slider"></span>'
        "</label>"
    )
    lines.append("          </div>")
    lines.append("        </fieldset>")
    lines.append("")

    # ── Visual section ──
    lines.append(
        f'        <fieldset class="ct-settings__section" data-settings-section="visual"'
        f'{a(scale, role="group", aria_labelledby="ct-settings-visual-legend")}>'
    )
    lines.append('          <legend class="ct-settings__legend" id="ct-settings-visual-legend">Visual</legend>')
    # Theme
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-theme">Theme</label>')
    lines.append('            <select class="ct-select" id="ct-setting-theme" data-setting="theme">')
    lines.append('              <option value="dark">Dark</option>')
    lines.append('              <option value="light">Light</option>')
    lines.append('              <option value="system">System</option>')
    if scale >= 5:
        lines.append('              <option value="high-contrast">High Contrast</option>')
    lines.append("            </select>")
    lines.append("          </div>")
    # Particle effects
    lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-particles">Particle Effects</label>')
    lines.append(
        '            <label class="ct-toggle">'
        '<input class="ct-toggle__input" type="checkbox" id="ct-setting-particles" data-setting="particles" checked>'
        '<span class="ct-toggle__slider"></span>'
        "</label>"
    )
    lines.append("          </div>")
    # Animations
    lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-animations">Animations</label>')
    lines.append(
        '            <label class="ct-toggle">'
        '<input class="ct-toggle__input" type="checkbox" id="ct-setting-animations" data-setting="animations" checked>'
        '<span class="ct-toggle__slider"></span>'
        "</label>"
    )
    lines.append("          </div>")
    # Grid opacity
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-grid-opacity">Grid Opacity</label>')
    lines.append(
        '            <input class="ct-range" type="range" id="ct-setting-grid-opacity" min="0" max="100" value="40"'
        ' data-setting="grid-opacity">'
    )
    lines.append('            <span class="ct-settings__value" data-bind="grid-opacity">40%</span>')
    lines.append("          </div>")
    if scale >= 4:
        lines.append('          <div class="ct-settings__control">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-quality">Render Quality</label>')
        lines.append('            <select class="ct-select" id="ct-setting-quality" data-setting="quality">')
        lines.append('              <option value="low">Low</option>')
        lines.append('              <option value="medium" selected>Medium</option>')
        lines.append('              <option value="high">High</option>')
        lines.append('              <option value="ultra">Ultra</option>')
        lines.append("            </select>")
        lines.append("          </div>")
    if scale >= 5:
        lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-antialiasing">Anti-Aliasing</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-setting-antialiasing" data-setting="antialiasing" checked>'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
        lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-fps-counter">Show FPS Counter</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-setting-fps-counter" data-setting="fps-counter">'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
    lines.append("        </fieldset>")
    lines.append("")

    # ── Gameplay section ──
    lines.append(
        f'        <fieldset class="ct-settings__section" data-settings-section="gameplay"'
        f'{a(scale, role="group", aria_labelledby="ct-settings-gameplay-legend")}>'
    )
    lines.append('          <legend class="ct-settings__legend" id="ct-settings-gameplay-legend">Gameplay</legend>')
    # Difficulty
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-difficulty">AI Difficulty</label>')
    lines.append('            <select class="ct-select" id="ct-setting-difficulty" data-setting="difficulty">')
    lines.append('              <option value="easy">Easy</option>')
    lines.append('              <option value="normal" selected>Normal</option>')
    lines.append('              <option value="hard">Hard</option>')
    if scale >= 4:
        lines.append('              <option value="expert">Expert</option>')
    lines.append("            </select>")
    lines.append("          </div>")
    # Turn timer
    lines.append('          <div class="ct-settings__control">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-turn-timer">Turn Timer (s)</label>')
    lines.append(
        '            <input class="ct-range" type="range" id="ct-setting-turn-timer" min="10" max="120" value="30"'
        ' step="5" data-setting="turn-timer">'
    )
    lines.append('            <span class="ct-settings__value" data-bind="turn-timer">30s</span>')
    lines.append("          </div>")
    # Auto end turn
    lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-auto-end">Auto End Turn</label>')
    lines.append(
        '            <label class="ct-toggle">'
        '<input class="ct-toggle__input" type="checkbox" id="ct-setting-auto-end" data-setting="auto-end-turn">'
        '<span class="ct-toggle__slider"></span>'
        "</label>"
    )
    lines.append("          </div>")
    # Confirm actions
    lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
    lines.append('            <label class="ct-settings__label" for="ct-setting-confirm-actions">Confirm Actions</label>')
    lines.append(
        '            <label class="ct-toggle">'
        '<input class="ct-toggle__input" type="checkbox" id="ct-setting-confirm-actions" data-setting="confirm-actions" checked>'
        '<span class="ct-toggle__slider"></span>'
        "</label>"
    )
    lines.append("          </div>")
    if scale >= 5:
        # Show hints
        lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-hints">Show Hints</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-setting-hints" data-setting="hints" checked>'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
        # Color blind mode
        lines.append('          <div class="ct-settings__control">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-colorblind">Color Blind Mode</label>')
        lines.append('            <select class="ct-select" id="ct-setting-colorblind" data-setting="colorblind-mode">')
        lines.append('              <option value="none">None</option>')
        lines.append('              <option value="protanopia">Protanopia</option>')
        lines.append('              <option value="deuteranopia">Deuteranopia</option>')
        lines.append('              <option value="tritanopia">Tritanopia</option>')
        lines.append("            </select>")
        lines.append("          </div>")
    lines.append("        </fieldset>")

    if scale >= 5:
        lines.append("")
        # ── Accessibility section ──
        lines.append(
            '        <fieldset class="ct-settings__section" data-settings-section="accessibility"'
            ' role="group" aria-labelledby="ct-settings-a11y-legend">'
        )
        lines.append('          <legend class="ct-settings__legend" id="ct-settings-a11y-legend">Accessibility</legend>')
        lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-reduced-motion">Reduced Motion</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-setting-reduced-motion" data-setting="reduced-motion">'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
        lines.append('          <div class="ct-settings__control ct-settings__control--toggle">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-screen-reader">Screen Reader Announcements</label>')
        lines.append(
            '            <label class="ct-toggle">'
            '<input class="ct-toggle__input" type="checkbox" id="ct-setting-screen-reader" data-setting="screen-reader" checked>'
            '<span class="ct-toggle__slider"></span>'
            "</label>"
        )
        lines.append("          </div>")
        lines.append('          <div class="ct-settings__control">')
        lines.append('            <label class="ct-settings__label" for="ct-setting-font-size">Font Size</label>')
        lines.append(
            '            <input class="ct-range" type="range" id="ct-setting-font-size" min="12" max="24" value="16"'
            ' data-setting="font-size">'
        )
        lines.append('            <span class="ct-settings__value" data-bind="font-size">16px</span>')
        lines.append("          </div>")
        lines.append("        </fieldset>")

    lines.append("")
    lines.append("      </div><!-- /.ct-settings__body -->")
    lines.append("")
    lines.append('      <div class="ct-settings__footer">')
    lines.append('        <button class="ct-btn ct-btn--ghost" data-action="reset-settings">Reset Defaults</button>')
    lines.append('        <button class="ct-btn ct-btn--primary" data-action="save-settings">Save Settings</button>')
    lines.append("      </div>")
    lines.append("    </section>")
    return "\n".join(lines)


# ── Toasts ─────────────────────────────────────────────────────────────────

def _gen_toasts(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <div class="ct-toasts" id="ct-toasts" data-panel="toasts"'
        f'{a(scale, role="log", aria_live="polite", aria_label="Notifications")}>'
    )
    if scale >= 5:
        lines.append("      <!-- Toast template (cloned by JS) -->")
        lines.append('      <template id="ct-toast-template">')
        lines.append('        <div class="ct-toast" role="alert" data-toast="">')
        lines.append('          <div class="ct-toast__icon" data-bind="toast-icon"></div>')
        lines.append('          <div class="ct-toast__content">')
        lines.append('            <p class="ct-toast__message" data-bind="toast-message"></p>')
        lines.append("          </div>")
        lines.append(
            f'          <button class="ct-toast__close" data-action="dismiss-toast"'
            f' aria-label="Dismiss notification">{_SVG_ICONS["close"]}</button>'
        )
        lines.append('          <div class="ct-toast__progress" data-bind="toast-progress"></div>')
        lines.append("        </div>")
        lines.append("      </template>")
    elif scale >= 4:
        lines.append("      <!-- Toasts are injected here by JS -->")
    lines.append("    </div>")
    return "\n".join(lines)


# ── Loading screen ─────────────────────────────────────────────────────────

def _gen_loading(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(
        f'    <div class="ct-loading" id="ct-loading" data-panel="loading"'
        f'{a(scale, role="progressbar", aria_label="Loading game", aria_valuemin="0", aria_valuemax="100", aria_valuenow="0")}>'
    )
    lines.append('      <div class="ct-loading__backdrop"></div>')
    lines.append('      <div class="ct-loading__content">')
    if scale >= 5:
        lines.append('        <div class="ct-loading__logo" aria-hidden="true">')
        lines.append(
            '          <svg class="ct-loading__logo-svg" viewBox="0 0 120 120" width="120" height="120">'
        )
        lines.append('            <defs>')
        lines.append('              <linearGradient id="loading-gradient" x1="0%" y1="0%" x2="100%" y2="100%">')
        lines.append('                <stop offset="0%" stop-color="#6c5ce7"/>')
        lines.append('                <stop offset="50%" stop-color="#00cec9"/>')
        lines.append('                <stop offset="100%" stop-color="#fdcb6e"/>')
        lines.append("              </linearGradient>")
        lines.append("            </defs>")
        lines.append(
            '            <polygon points="60 10 110 95 10 95" fill="none" stroke="url(#loading-gradient)"'
            ' stroke-width="3" class="ct-loading__logo-shape ct-loading__logo-shape--triangle"/>'
        )
        lines.append(
            '            <circle cx="60" cy="65" r="25" fill="none" stroke="url(#loading-gradient)"'
            ' stroke-width="2" class="ct-loading__logo-shape ct-loading__logo-shape--circle"/>'
        )
        lines.append(
            '            <rect x="38" y="43" width="44" height="44" fill="none" stroke="url(#loading-gradient)"'
            ' stroke-width="1.5" class="ct-loading__logo-shape ct-loading__logo-shape--square"'
            ' transform="rotate(15 60 65)"/>'
        )
        lines.append("          </svg>")
        lines.append("        </div>")
    lines.append('        <div class="ct-loading__spinner">')
    lines.append('          <div class="ct-loading__spinner-ring"></div>')
    if scale >= 4:
        lines.append('          <div class="ct-loading__spinner-ring ct-loading__spinner-ring--inner"></div>')
    lines.append("        </div>")
    lines.append('        <p class="ct-loading__text" data-bind="loading-text">Loading…</p>')
    lines.append('        <div class="ct-loading__progress">')
    lines.append('          <div class="ct-loading__progress-track">')
    lines.append('            <div class="ct-loading__progress-fill" data-bind="loading-progress"></div>')
    lines.append("          </div>")
    lines.append('          <span class="ct-loading__progress-value" data-bind="loading-percent">0%</span>')
    lines.append("        </div>")
    if scale >= 5:
        lines.append('        <ul class="ct-loading__steps" aria-label="Loading steps">')
        loading_steps = [
            "Initializing engine…",
            "Loading terrain data…",
            "Generating colour palettes…",
            "Preparing UI components…",
            "Connecting to game server…",
            "Ready!",
        ]
        for step in loading_steps:
            lines.append(f'          <li class="ct-loading__step" data-bind="loading-step">{step}</li>')
        lines.append("        </ul>")
    lines.append("      </div>")
    lines.append("    </div>")
    return "\n".join(lines)


# ── Footer ─────────────────────────────────────────────────────────────────

def _gen_footer(scale: int) -> str:
    a = _aria
    lines: list[str] = []
    lines.append(f'    <footer class="ct-footer"{a(scale, role="contentinfo")}>')
    lines.append('      <div class="ct-footer__inner">')
    lines.append('        <div class="ct-footer__credits">')
    lines.append('          <p class="ct-footer__text">Chromatic Territories</p>')
    lines.append('          <p class="ct-footer__version">v0.1.0</p>')
    lines.append("        </div>")
    if scale >= 3:
        lines.append(f'        <nav class="ct-footer__links"{a(scale, aria_label="Footer navigation")}>')
        footer_links = [
            ("#about", "About"),
            ("#privacy", "Privacy"),
            ("#terms", "Terms"),
            ("#credits", "Credits"),
        ]
        if scale >= 5:
            footer_links += [
                ("#changelog", "Changelog"),
                ("#source", "Source Code"),
                ("#feedback", "Feedback"),
            ]
        for href, label in footer_links:
            lines.append(f'          <a class="ct-footer__link" href="{href}">{label}</a>')
        lines.append("        </nav>")
    if scale >= 4:
        lines.append('        <div class="ct-footer__social">')
        social = [("github", "GitHub"), ("twitter", "Twitter"), ("discord", "Discord")]
        for handle, label in social:
            lines.append(
                f'          <a class="ct-footer__social-link" href="#{handle}"'
                f'{a(scale, aria_label=label)}>{label}</a>'
            )
        lines.append("        </div>")
    lines.append('        <p class="ct-footer__copyright">')
    lines.append("          &copy; 2024 Chromatic Territories. Built with generative algorithms.")
    lines.append("        </p>")
    if scale >= 5:
        lines.append('        <p class="ct-footer__a11y">')
        lines.append(
            '          <a class="ct-footer__link" href="#accessibility">Accessibility Statement</a>'
        )
        lines.append("        </p>")
    lines.append("      </div>")
    lines.append("    </footer>")
    return "\n".join(lines)


# ── Scripts ────────────────────────────────────────────────────────────────

def _gen_scripts(scale: int) -> str:
    lines: list[str] = []
    lines.append("    <!-- ═══ Scripts ═══ -->")
    js_modules = [
        "js/vendor/polyfills.js",
        "js/core/utils.js",
        "js/core/event-bus.js",
        "js/engine/noise.js",
        "js/engine/color-theory.js",
        "js/engine/fractal.js",
        "js/engine/lsystem.js",
        "js/engine/particle.js",
        "js/engine/cellular.js",
        "js/engine/composition.js",
        "js/game/territory.js",
        "js/game/game-engine.js",
        "js/game/combat.js",
        "js/game/ai-opponent.js",
        "js/game/scoring.js",
        "js/ui/renderer.js",
        "js/ui/hud.js",
        "js/ui/modals.js",
        "js/ui/sidebar.js",
        "js/ui/toast.js",
        "js/app.js",
    ]
    if scale >= 4:
        js_modules.insert(-1, "js/ui/tutorial.js")
        js_modules.insert(-1, "js/ui/gallery.js")
        js_modules.insert(-1, "js/ui/settings.js")
        js_modules.insert(-1, "js/ui/keyboard.js")
    if scale >= 5:
        js_modules.insert(-1, "js/ui/context-menu.js")
        js_modules.insert(-1, "js/ui/accessibility.js")
        js_modules.insert(-1, "js/debug/fps-counter.js")
        js_modules.insert(-1, "js/debug/inspector.js")
        js_modules.insert(-1, "js/analytics.js")
    for mod in js_modules:
        if scale >= 5:
            lines.append(f'    <script src="{mod}" defer></script>')
        else:
            lines.append(f'    <script src="{mod}"></script>')
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Component registry & public API
# ═══════════════════════════════════════════════════════════════════════════

# Maps concept name → generator function(scale) -> str
_COMPONENT_GENERATORS: dict[str, object] = {
    "app_shell_open": _gen_app_shell_open,
    "app_shell_close": _gen_app_shell_close,
    "navigation": _gen_navigation,
    "hero": _gen_hero,
    "canvas": _gen_canvas,
    "sidebar": _gen_sidebar,
    "modals": _gen_modals,
    "gallery": _gen_gallery,
    "tutorial": _gen_tutorial,
    "settings": _gen_settings,
    "toasts": _gen_toasts,
    "loading": _gen_loading,
    "footer": _gen_footer,
    "scripts": _gen_scripts,
}

# Pre-populated at default scale for get_html_for_concept() lookups.
HTML_COMPONENTS: dict[str, str] = {}

_DEFAULT_SCALE = 3


def _populate(scale: int = _DEFAULT_SCALE, title: str = "Chromatic Territories") -> None:
    """Populate ``HTML_COMPONENTS`` at the requested *scale*."""
    HTML_COMPONENTS.clear()
    for name, gen in _COMPONENT_GENERATORS.items():
        if name == "app_shell_open":
            HTML_COMPONENTS[name] = gen(title, scale)
        else:
            HTML_COMPONENTS[name] = gen(scale)
    # Alias: "app_shell" returns shell_open (for backward compat / simple lookup)
    HTML_COMPONENTS["app_shell"] = HTML_COMPONENTS["app_shell_open"]


# Eagerly populate at import time so get_html_for_concept works immediately.
_populate()


def get_html_for_concept(name: str) -> str:
    """Return pre-generated HTML for *name*, or empty string."""
    return HTML_COMPONENTS.get(name, "")


# Which components to include at each minimum scale level
_SCALE_COMPONENTS: dict[int, list[str]] = {
    1: ["navigation", "canvas", "scripts"],
    2: ["sidebar", "modals", "footer"],
    3: ["hero", "gallery", "tutorial", "settings"],
    4: ["loading", "toasts"],
    5: [],  # scale 5 adds detail inside components, not new sections
}


def generate_app_html(
    title: str = "Chromatic Territories",
    concepts: list[str] | None = None,
    scale: int = 3,
    **kwargs: object,
) -> str:
    """Generate a complete HTML document from *concepts* at the given *scale*.

    Parameters
    ----------
    title:
        Page ``<title>`` value.
    concepts:
        Explicit list of component names to include.  When *None* the set
        is determined automatically from *scale*.
    scale:
        Detail level 1-5.  Higher values include more components and richer
        markup (ARIA attrs at 4+, inline SVG icons at 5).
    **kwargs:
        Forwarded to individual generators where applicable (e.g.
        ``description`` for the meta tag).
    """
    scale = max(1, min(5, scale))

    # Rebuild components at the requested scale & title.
    _populate(scale=scale, title=title)

    # Determine which sections to include.
    if concepts is not None:
        sections = list(concepts)
    else:
        sections = []
        for lvl in range(1, scale + 1):
            sections.extend(_SCALE_COMPONENTS.get(lvl, []))

    description = kwargs.get("description", "")
    parts: list[str] = []

    # Opening shell
    parts.append(_gen_app_shell_open(title, scale, description=str(description)))

    # Body sections in correct order
    ordered = [
        "navigation",
        "hero",
        "canvas",
        "sidebar",
        "gallery",
        "tutorial",
        "settings",
        "modals",
        "toasts",
        "loading",
        "footer",
        "scripts",
    ]
    for name in ordered:
        if name in sections:
            parts.append("")
            parts.append(get_html_for_concept(name))

    # Closing shell
    parts.append("")
    parts.append(_gen_app_shell_close(scale))

    return "\n".join(parts)
