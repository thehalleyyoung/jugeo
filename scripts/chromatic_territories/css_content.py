"""CSS content constants for Chromatic Territories design system.

Complete styling: design tokens, layout, components, game UI,
and animations.  All game-specific classes use the .ct- prefix.
"""


# =================================================================
# 1. DESIGN SYSTEM - Tokens, reset, typography
# =================================================================

DESIGN_SYSTEM_CSS = """\
/* ================================================================
   CHROMATIC TERRITORIES - Design System
   ================================================================ */

/* -------------------- Design Tokens -------------------- */
:root {
  /* Core palette */
  --ct-bg:              #0a0a1a;
  --ct-bg-alt:          #0d0d22;
  --ct-surface:         #141428;
  --ct-surface-hover:   #1a1a35;
  --ct-surface-active:  #1e1e40;
  --ct-border:          #2a2a4a;
  --ct-border-light:    #3a3a5a;

  /* Primary */
  --ct-primary:         #6366f1;
  --ct-primary-hover:   #818cf8;
  --ct-primary-active:  #4f46e5;
  --ct-primary-ghost:   rgba(99, 102, 241, 0.1);

  /* Accent */
  --ct-accent:          #f59e0b;
  --ct-accent-hover:    #fbbf24;
  --ct-accent-active:   #d97706;
  --ct-accent-ghost:    rgba(245, 158, 11, 0.1);

  /* Semantic */
  --ct-success:         #10b981;
  --ct-success-hover:   #34d399;
  --ct-success-ghost:   rgba(16, 185, 129, 0.1);
  --ct-danger:          #ef4444;
  --ct-danger-hover:    #f87171;
  --ct-danger-ghost:    rgba(239, 68, 68, 0.1);
  --ct-warning:         #f59e0b;
  --ct-warning-ghost:   rgba(245, 158, 11, 0.1);
  --ct-info:            #3b82f6;
  --ct-info-ghost:      rgba(59, 130, 246, 0.1);

  /* Text */
  --ct-text:            #e2e8f0;
  --ct-text-muted:      #94a3b8;
  --ct-text-dim:        #64748b;
  --ct-text-inverse:    #0f172a;

  /* Spacing scale (4px base) */
  --ct-space-0:  0;
  --ct-space-1:  0.25rem;
  --ct-space-2:  0.5rem;
  --ct-space-3:  0.75rem;
  --ct-space-4:  1rem;
  --ct-space-5:  1.25rem;
  --ct-space-6:  1.5rem;
  --ct-space-8:  2rem;
  --ct-space-10: 2.5rem;
  --ct-space-12: 3rem;
  --ct-space-16: 4rem;
  --ct-space-20: 5rem;
  --ct-space-24: 6rem;

  /* Typography */
  --ct-font-sans:    'Inter', -apple-system, BlinkMacSystemFont,
                     'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
  --ct-font-mono:    'JetBrains Mono', 'Fira Code', 'Cascadia Code',
                     'Source Code Pro', monospace;
  --ct-font-display: 'Orbitron', var(--ct-font-sans);

  --ct-text-xs:   0.75rem;    /* 12px */
  --ct-text-sm:   0.875rem;   /* 14px */
  --ct-text-base: 1rem;       /* 16px */
  --ct-text-lg:   1.125rem;   /* 18px */
  --ct-text-xl:   1.25rem;    /* 20px */
  --ct-text-2xl:  1.5rem;     /* 24px */
  --ct-text-3xl:  1.875rem;   /* 30px */
  --ct-text-4xl:  2.25rem;    /* 36px */
  --ct-text-5xl:  3rem;       /* 48px */

  --ct-leading-tight:   1.25;
  --ct-leading-normal:  1.5;
  --ct-leading-relaxed: 1.75;

  --ct-weight-normal:   400;
  --ct-weight-medium:   500;
  --ct-weight-semibold: 600;
  --ct-weight-bold:     700;

  /* Borders & Radius */
  --ct-radius-sm:   0.25rem;
  --ct-radius:      0.5rem;
  --ct-radius-lg:   0.75rem;
  --ct-radius-xl:   1rem;
  --ct-radius-2xl:  1.5rem;
  --ct-radius-full: 9999px;

  /* Shadows */
  --ct-shadow-sm:   0 1px 2px rgba(0, 0, 0, 0.3);
  --ct-shadow:      0 4px 6px -1px rgba(0, 0, 0, 0.4);
  --ct-shadow-md:   0 6px 10px -2px rgba(0, 0, 0, 0.45);
  --ct-shadow-lg:   0 10px 15px -3px rgba(0, 0, 0, 0.5);
  --ct-shadow-xl:   0 20px 25px -5px rgba(0, 0, 0, 0.6);
  --ct-shadow-glow: 0 0 20px rgba(99, 102, 241, 0.3);
  --ct-shadow-accent-glow: 0 0 20px rgba(245, 158, 11, 0.3);

  /* Transitions */
  --ct-transition-fast: 150ms ease;
  --ct-transition:      250ms ease;
  --ct-transition-slow: 400ms ease;

  /* Z-index scale */
  --ct-z-base:     0;
  --ct-z-dropdown: 100;
  --ct-z-sticky:   200;
  --ct-z-overlay:  300;
  --ct-z-modal:    400;
  --ct-z-popover:  500;
  --ct-z-tooltip:  600;
  --ct-z-toast:    700;
}

/* -------------------- CSS Reset -------------------- */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  font-size: 16px;
  -webkit-text-size-adjust: 100%;
  -moz-text-size-adjust: 100%;
  text-size-adjust: 100%;
  scroll-behavior: smooth;
}

body {
  min-height: 100vh;
  background-color: var(--ct-bg);
  color: var(--ct-text);
  font-family: var(--ct-font-sans);
  font-size: var(--ct-text-base);
  line-height: var(--ct-leading-normal);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
  overflow: hidden;
}

img,
picture,
video,
canvas,
svg {
  display: block;
  max-width: 100%;
}

input,
button,
textarea,
select {
  font: inherit;
  color: inherit;
}

button {
  cursor: pointer;
  border: none;
  background: none;
}

a {
  color: var(--ct-primary);
  text-decoration: none;
  transition: color var(--ct-transition-fast);
}

a:hover {
  color: var(--ct-primary-hover);
  text-decoration: underline;
}

ul, ol {
  list-style: none;
}

table {
  border-collapse: collapse;
  border-spacing: 0;
}

/* -------------------- Typography -------------------- */
h1, h2, h3, h4, h5, h6 {
  font-family: var(--ct-font-display);
  font-weight: var(--ct-weight-bold);
  line-height: var(--ct-leading-tight);
  color: var(--ct-text);
  letter-spacing: 0.02em;
}

h1 {
  font-size: var(--ct-text-4xl);
  margin-bottom: var(--ct-space-6);
  letter-spacing: 0.04em;
}

h2 {
  font-size: var(--ct-text-3xl);
  margin-bottom: var(--ct-space-5);
}

h3 {
  font-size: var(--ct-text-2xl);
  margin-bottom: var(--ct-space-4);
}

h4 {
  font-size: var(--ct-text-xl);
  margin-bottom: var(--ct-space-3);
}

h5 {
  font-size: var(--ct-text-lg);
  margin-bottom: var(--ct-space-2);
}

h6 {
  font-size: var(--ct-text-base);
  margin-bottom: var(--ct-space-2);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

p {
  margin-bottom: var(--ct-space-4);
  color: var(--ct-text-muted);
}

strong, b { font-weight: var(--ct-weight-semibold); }
em, i     { font-style: italic; }

small {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-dim);
}

code, kbd, samp {
  font-family: var(--ct-font-mono);
  font-size: 0.9em;
  background: var(--ct-surface);
  padding: 0.15em 0.35em;
  border-radius: var(--ct-radius-sm);
  border: 1px solid var(--ct-border);
}

pre {
  font-family: var(--ct-font-mono);
  font-size: var(--ct-text-sm);
  background: var(--ct-surface);
  padding: var(--ct-space-4);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  overflow-x: auto;
  margin-bottom: var(--ct-space-4);
}

pre code {
  background: none;
  padding: 0;
  border: none;
}

kbd {
  background: var(--ct-surface-hover);
  border-bottom-width: 2px;
  font-size: var(--ct-text-xs);
  padding: 0.1em 0.4em;
}

/* -------------------- Selection -------------------- */
::selection {
  background-color: var(--ct-primary);
  color: white;
}

::-moz-selection {
  background-color: var(--ct-primary);
  color: white;
}

/* -------------------- Focus -------------------- */
:focus-visible {
  outline: 2px solid var(--ct-primary);
  outline-offset: 2px;
}

:focus:not(:focus-visible) {
  outline: none;
}

/* -------------------- Scrollbar -------------------- */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--ct-bg);
}

::-webkit-scrollbar-thumb {
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--ct-border-light);
}

/* Scrollbar for Firefox */
* {
  scrollbar-width: thin;
  scrollbar-color: var(--ct-border) var(--ct-bg);
}

/* -------------------- Utility text classes -------------------- */
.ct-text-muted   { color: var(--ct-text-muted) !important; }
.ct-text-dim     { color: var(--ct-text-dim) !important; }
.ct-text-primary { color: var(--ct-primary) !important; }
.ct-text-accent  { color: var(--ct-accent) !important; }
.ct-text-success { color: var(--ct-success) !important; }
.ct-text-danger  { color: var(--ct-danger) !important; }
.ct-text-warning { color: var(--ct-warning) !important; }
.ct-text-info    { color: var(--ct-info) !important; }

.ct-font-mono    { font-family: var(--ct-font-mono) !important; }
.ct-font-display { font-family: var(--ct-font-display) !important; }

.ct-text-center  { text-align: center !important; }
.ct-text-right   { text-align: right !important; }
.ct-text-left    { text-align: left !important; }

.ct-text-sm      { font-size: var(--ct-text-sm) !important; }
.ct-text-lg      { font-size: var(--ct-text-lg) !important; }
.ct-text-xl      { font-size: var(--ct-text-xl) !important; }

.ct-truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* -------------------- Screen reader only -------------------- */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border-width: 0;
}
"""




# =================================================================
# 2. LAYOUT - Grid, flexbox, responsive structure
# =================================================================

LAYOUT_CSS = """\
/* ================================================================
   LAYOUT SYSTEM
   ================================================================ */

/* -------------------- App Shell -------------------- */
.ct-app {
  display: grid;
  grid-template-areas:
    "header  header"
    "sidebar main"
    "footer  footer";
  grid-template-columns: auto 1fr;
  grid-template-rows: auto 1fr auto;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: var(--ct-bg);
}

.ct-header {
  grid-area: header;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-4);
  height: 60px;
  padding: 0 var(--ct-space-4);
  background: var(--ct-surface);
  border-bottom: 1px solid var(--ct-border);
  z-index: var(--ct-z-sticky);
}

.ct-header__brand {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
}

.ct-header__logo {
  font-size: var(--ct-text-2xl);
  color: var(--ct-accent);
  animation: ct-pulse 3s ease-in-out infinite;
}

.ct-header__title {
  font-family: var(--ct-font-display);
  font-size: var(--ct-text-lg);
  font-weight: var(--ct-weight-bold);
  letter-spacing: 0.05em;
  margin: 0;
  white-space: nowrap;
}

.ct-header__nav {
  display: flex;
  align-items: center;
  gap: var(--ct-space-1);
}

.ct-header__link {
  padding: var(--ct-space-2) var(--ct-space-3);
  color: var(--ct-text-muted);
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
  border-radius: var(--ct-radius);
  transition: all var(--ct-transition-fast);
  text-decoration: none;
}

.ct-header__link:hover {
  color: var(--ct-text);
  background: var(--ct-surface-hover);
  text-decoration: none;
}

.ct-header__link--active {
  color: var(--ct-primary);
  background: var(--ct-primary-ghost);
}

.ct-header__actions {
  display: flex;
  align-items: center;
  gap: var(--ct-space-1);
}

/* -------------------- Sidebar -------------------- */
.ct-sidebar {
  grid-area: sidebar;
  width: 280px;
  background: var(--ct-surface);
  border-right: 1px solid var(--ct-border);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  transition: width var(--ct-transition-slow);
  position: relative;
}

.ct-sidebar--collapsed {
  width: 0;
  overflow: hidden;
  border-right: none;
}

.ct-sidebar__toggle {
  position: absolute;
  top: var(--ct-space-2);
  right: calc(-1 * var(--ct-space-8));
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  color: var(--ct-text-muted);
  font-size: var(--ct-text-sm);
  cursor: pointer;
  z-index: 10;
  transition: all var(--ct-transition-fast);
}

.ct-sidebar__toggle:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-sidebar--collapsed .ct-sidebar__toggle-icon {
  transform: rotate(180deg);
}

.ct-sidebar__content {
  padding: var(--ct-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-4);
}

.ct-sidebar__section {
  border-bottom: 1px solid var(--ct-border);
  padding-bottom: var(--ct-space-4);
}

.ct-sidebar__section:last-child {
  border-bottom: none;
}

.ct-sidebar__heading {
  font-family: var(--ct-font-sans);
  font-size: var(--ct-text-xs);
  font-weight: var(--ct-weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--ct-text-dim);
  margin-bottom: var(--ct-space-3);
}

.ct-sidebar__actions {
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-2);
}

.ct-sidebar__action-btn {
  justify-content: flex-start;
  text-align: left;
}

/* -------------------- Main Content -------------------- */
.ct-main {
  grid-area: main;
  position: relative;
  overflow: hidden;
  background: var(--ct-bg);
}

.ct-router-outlet {
  width: 100%;
  height: 100%;
  overflow-y: auto;
  padding: var(--ct-space-6);
}

/* -------------------- Canvas Container -------------------- */
.ct-canvas-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--ct-bg-alt);
}

.ct-canvas-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.ct-canvas-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 10;
}

/* -------------------- Panels -------------------- */
.ct-panels {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: var(--ct-z-dropdown);
}

.ct-panel {
  position: absolute;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  box-shadow: var(--ct-shadow-xl);
  display: flex;
  flex-direction: column;
  min-width: 240px;
  max-width: 90vw;
  max-height: 80vh;
  pointer-events: auto;
  z-index: var(--ct-z-dropdown);
}

.ct-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-3) var(--ct-space-4);
  border-bottom: 1px solid var(--ct-border);
  cursor: move;
  user-select: none;
  flex-shrink: 0;
}

.ct-panel__title {
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-semibold);
  margin: 0;
}

.ct-panel__controls {
  display: flex;
  gap: var(--ct-space-1);
}

.ct-panel__btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--ct-radius-sm);
  color: var(--ct-text-dim);
  font-size: var(--ct-text-sm);
  transition: all var(--ct-transition-fast);
}

.ct-panel__btn:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-panel__body {
  flex: 1;
  padding: var(--ct-space-4);
  overflow-y: auto;
}

.ct-panel__footer {
  padding: var(--ct-space-3) var(--ct-space-4);
  border-top: 1px solid var(--ct-border);
  flex-shrink: 0;
}

.ct-panel--floating {
  position: fixed;
}

.ct-panel--minimized .ct-panel__body,
.ct-panel--minimized .ct-panel__footer {
  display: none;
}

/* -------------------- Footer -------------------- */
.ct-footer {
  grid-area: footer;
  background: var(--ct-surface);
  border-top: 1px solid var(--ct-border);
  padding: var(--ct-space-2) var(--ct-space-4);
}

.ct-footer__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-4);
}

.ct-footer__credit {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
  margin: 0;
}

.ct-footer__version {
  font-family: var(--ct-font-mono);
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
  margin: 0;
}

.ct-footer__links {
  display: flex;
  gap: var(--ct-space-2);
  align-items: center;
}

.ct-footer__link {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-footer__link:hover {
  color: var(--ct-text-muted);
}

.ct-footer__sep {
  color: var(--ct-border);
}

/* -------------------- Grid Utilities -------------------- */
.ct-grid     { display: grid; gap: var(--ct-space-4); }
.ct-grid-2   { grid-template-columns: repeat(2, 1fr); }
.ct-grid-3   { grid-template-columns: repeat(3, 1fr); }
.ct-grid-4   { grid-template-columns: repeat(4, 1fr); }
.ct-grid-auto { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }

/* -------------------- Flex Utilities -------------------- */
.ct-flex         { display: flex; }
.ct-flex-col     { display: flex; flex-direction: column; }
.ct-flex-center  { display: flex; align-items: center; justify-content: center; }
.ct-flex-between { display: flex; align-items: center; justify-content: space-between; }
.ct-flex-end     { display: flex; align-items: center; justify-content: flex-end; }
.ct-flex-wrap    { flex-wrap: wrap; }
.ct-flex-1       { flex: 1; }
.ct-flex-shrink-0 { flex-shrink: 0; }
.ct-items-start  { align-items: flex-start; }
.ct-items-end    { align-items: flex-end; }

.ct-gap-1 { gap: var(--ct-space-1); }
.ct-gap-2 { gap: var(--ct-space-2); }
.ct-gap-3 { gap: var(--ct-space-3); }
.ct-gap-4 { gap: var(--ct-space-4); }
.ct-gap-6 { gap: var(--ct-space-6); }
.ct-gap-8 { gap: var(--ct-space-8); }

/* -------------------- Spacing Utilities -------------------- */
.ct-mt-0  { margin-top: 0 !important; }
.ct-mt-1  { margin-top: var(--ct-space-1) !important; }
.ct-mt-2  { margin-top: var(--ct-space-2) !important; }
.ct-mt-3  { margin-top: var(--ct-space-3) !important; }
.ct-mt-4  { margin-top: var(--ct-space-4) !important; }
.ct-mt-6  { margin-top: var(--ct-space-6) !important; }
.ct-mt-8  { margin-top: var(--ct-space-8) !important; }

.ct-mb-0  { margin-bottom: 0 !important; }
.ct-mb-1  { margin-bottom: var(--ct-space-1) !important; }
.ct-mb-2  { margin-bottom: var(--ct-space-2) !important; }
.ct-mb-3  { margin-bottom: var(--ct-space-3) !important; }
.ct-mb-4  { margin-bottom: var(--ct-space-4) !important; }
.ct-mb-6  { margin-bottom: var(--ct-space-6) !important; }
.ct-mb-8  { margin-bottom: var(--ct-space-8) !important; }

.ct-ml-0  { margin-left: 0 !important; }
.ct-ml-1  { margin-left: var(--ct-space-1) !important; }
.ct-ml-2  { margin-left: var(--ct-space-2) !important; }
.ct-ml-4  { margin-left: var(--ct-space-4) !important; }

.ct-mr-0  { margin-right: 0 !important; }
.ct-mr-1  { margin-right: var(--ct-space-1) !important; }
.ct-mr-2  { margin-right: var(--ct-space-2) !important; }
.ct-mr-4  { margin-right: var(--ct-space-4) !important; }

.ct-mx-auto { margin-left: auto !important; margin-right: auto !important; }
.ct-my-2    { margin-top: var(--ct-space-2) !important; margin-bottom: var(--ct-space-2) !important; }
.ct-my-4    { margin-top: var(--ct-space-4) !important; margin-bottom: var(--ct-space-4) !important; }

.ct-p-0  { padding: 0 !important; }
.ct-p-1  { padding: var(--ct-space-1) !important; }
.ct-p-2  { padding: var(--ct-space-2) !important; }
.ct-p-3  { padding: var(--ct-space-3) !important; }
.ct-p-4  { padding: var(--ct-space-4) !important; }
.ct-p-6  { padding: var(--ct-space-6) !important; }
.ct-p-8  { padding: var(--ct-space-8) !important; }

.ct-px-2 { padding-left: var(--ct-space-2) !important; padding-right: var(--ct-space-2) !important; }
.ct-px-4 { padding-left: var(--ct-space-4) !important; padding-right: var(--ct-space-4) !important; }
.ct-py-2 { padding-top: var(--ct-space-2) !important; padding-bottom: var(--ct-space-2) !important; }
.ct-py-4 { padding-top: var(--ct-space-4) !important; padding-bottom: var(--ct-space-4) !important; }

/* -------------------- Container -------------------- */
.ct-container {
  width: 100%;
  max-width: 1200px;
  margin-left: auto;
  margin-right: auto;
  padding-left: var(--ct-space-4);
  padding-right: var(--ct-space-4);
}

.ct-container--sm { max-width: 640px; }
.ct-container--md { max-width: 768px; }
.ct-container--lg { max-width: 1024px; }
.ct-container--xl { max-width: 1440px; }

/* -------------------- Position Utilities -------------------- */
.ct-relative { position: relative; }
.ct-absolute { position: absolute; }
.ct-fixed    { position: fixed; }
.ct-sticky   { position: sticky; top: 0; }

/* -------------------- Size Utilities -------------------- */
.ct-w-full   { width: 100%; }
.ct-h-full   { height: 100%; }
.ct-h-screen { height: 100vh; }
.ct-w-screen { width: 100vw; }

/* -------------------- Overflow -------------------- */
.ct-overflow-hidden { overflow: hidden; }
.ct-overflow-auto   { overflow: auto; }
.ct-overflow-x-auto { overflow-x: auto; overflow-y: hidden; }

/* -------------------- Visibility -------------------- */
.ct-hidden  { display: none !important; }
.ct-visible { display: block !important; }
.ct-invisible { visibility: hidden; }

/* -------------------- Responsive -------------------- */
@media (max-width: 768px) {
  .ct-app {
    grid-template-areas:
      "header"
      "main"
      "footer";
    grid-template-columns: 1fr;
  }

  .ct-sidebar {
    position: fixed;
    top: 60px;
    left: 0;
    bottom: 0;
    z-index: var(--ct-z-overlay);
    transform: translateX(-100%);
    transition: transform var(--ct-transition-slow);
  }

  .ct-sidebar--open {
    transform: translateX(0);
  }

  .ct-header__nav {
    display: none;
  }

  .ct-grid-2,
  .ct-grid-3,
  .ct-grid-4 {
    grid-template-columns: 1fr;
  }

  .ct-hidden-mobile { display: none !important; }
  .ct-visible-mobile { display: block !important; }

  .ct-panel {
    position: fixed !important;
    left: var(--ct-space-2) !important;
    right: var(--ct-space-2) !important;
    width: auto !important;
    max-width: none !important;
  }
}

@media (max-width: 1024px) {
  .ct-sidebar { width: 240px; }

  .ct-grid-4 { grid-template-columns: repeat(2, 1fr); }
  .ct-grid-3 { grid-template-columns: repeat(2, 1fr); }

  .ct-hidden-tablet { display: none !important; }
}

@media (min-width: 1440px) {
  .ct-sidebar { width: 320px; }

  .ct-container { max-width: 1400px; }

  .ct-hidden-desktop { display: none !important; }
  .ct-visible-desktop { display: block !important; }
}

@media (min-width: 1920px) {
  :root {
    font-size: 18px;
  }
}
"""


# =================================================================
# 3. COMPONENTS - Buttons, cards, modals, forms, etc.
# =================================================================

COMPONENTS_CSS = """\
/* ================================================================
   COMPONENT LIBRARY
   ================================================================ */

/* ==================== Buttons ==================== */
.ct-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--ct-space-2);
  padding: 0.5rem 1rem;
  border-radius: var(--ct-radius);
  font-family: var(--ct-font-sans);
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
  line-height: 1.5;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  border: 1px solid transparent;
  transition: all var(--ct-transition);
  text-decoration: none;
}

.ct-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--ct-shadow);
}

.ct-btn:active {
  transform: translateY(0);
  box-shadow: none;
}

.ct-btn:disabled,
.ct-btn[disabled] {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none !important;
  box-shadow: none !important;
}

/* Primary */
.ct-btn--primary {
  background: var(--ct-primary);
  color: white;
}
.ct-btn--primary:hover {
  background: var(--ct-primary-hover);
}
.ct-btn--primary:active {
  background: var(--ct-primary-active);
}

/* Accent */
.ct-btn--accent {
  background: var(--ct-accent);
  color: var(--ct-text-inverse);
}
.ct-btn--accent:hover {
  background: var(--ct-accent-hover);
}
.ct-btn--accent:active {
  background: var(--ct-accent-active);
}

/* Success */
.ct-btn--success {
  background: var(--ct-success);
  color: white;
}
.ct-btn--success:hover {
  background: var(--ct-success-hover);
}

/* Danger */
.ct-btn--danger {
  background: var(--ct-danger);
  color: white;
}
.ct-btn--danger:hover {
  background: var(--ct-danger-hover);
}

/* Ghost */
.ct-btn--ghost {
  background: transparent;
  color: var(--ct-text);
  border-color: var(--ct-border);
}
.ct-btn--ghost:hover {
  background: var(--ct-surface-hover);
  border-color: var(--ct-border-light);
}

/* Outline */
.ct-btn--outline {
  background: transparent;
  color: var(--ct-primary);
  border-color: var(--ct-primary);
}
.ct-btn--outline:hover {
  background: var(--ct-primary-ghost);
}

/* Sizes */
.ct-btn--sm {
  padding: 0.25rem 0.5rem;
  font-size: var(--ct-text-xs);
  border-radius: var(--ct-radius-sm);
}
.ct-btn--lg {
  padding: 0.75rem 1.5rem;
  font-size: var(--ct-text-base);
  border-radius: var(--ct-radius-lg);
}
.ct-btn--xl {
  padding: 1rem 2rem;
  font-size: var(--ct-text-lg);
}

/* Icon-only */
.ct-btn--icon {
  padding: 0.5rem;
  aspect-ratio: 1;
}
.ct-btn--icon.ct-btn--sm { padding: 0.25rem; }
.ct-btn--icon.ct-btn--lg { padding: 0.75rem; }

/* Button group */
.ct-btn-group {
  display: inline-flex;
  border-radius: var(--ct-radius);
  overflow: hidden;
}
.ct-btn-group .ct-btn {
  border-radius: 0;
  border-right-width: 0;
}
.ct-btn-group .ct-btn:first-child { border-radius: var(--ct-radius) 0 0 var(--ct-radius); }
.ct-btn-group .ct-btn:last-child  { border-radius: 0 var(--ct-radius) var(--ct-radius) 0; border-right-width: 1px; }

/* ==================== Cards ==================== */
.ct-card {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  box-shadow: var(--ct-shadow);
  overflow: hidden;
  transition: transform var(--ct-transition), box-shadow var(--ct-transition);
}

.ct-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--ct-shadow-lg);
}

.ct-card__header {
  padding: var(--ct-space-4);
  border-bottom: 1px solid var(--ct-border);
}

.ct-card__body {
  padding: var(--ct-space-4);
}

.ct-card__footer {
  padding: var(--ct-space-4);
  border-top: 1px solid var(--ct-border);
  background: var(--ct-surface-hover);
}

.ct-card__image {
  width: 100%;
  height: auto;
  display: block;
}

.ct-card--flat {
  box-shadow: none;
  border: none;
}

.ct-card--flat:hover {
  transform: none;
  box-shadow: none;
  background: var(--ct-surface-hover);
}

/* ==================== Modals ==================== */
.ct-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: var(--ct-z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--ct-space-4);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--ct-transition);
}

.ct-modal-backdrop--active {
  opacity: 1;
  pointer-events: auto;
}

.ct-modal {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-xl);
  box-shadow: var(--ct-shadow-xl);
  max-width: 500px;
  width: 90%;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  transform: scale(0.9);
  opacity: 0;
  transition: transform var(--ct-transition), opacity var(--ct-transition);
}

.ct-modal-backdrop--active .ct-modal {
  transform: scale(1);
  opacity: 1;
}

.ct-modal--lg { max-width: 720px; }
.ct-modal--xl { max-width: 960px; }
.ct-modal--full { max-width: 95vw; max-height: 95vh; }

.ct-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-4) var(--ct-space-6);
  border-bottom: 1px solid var(--ct-border);
  flex-shrink: 0;
}

.ct-modal__title {
  font-size: var(--ct-text-xl);
  font-weight: var(--ct-weight-semibold);
  margin: 0;
}

.ct-modal__close {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--ct-text-xl);
  color: var(--ct-text-dim);
  border-radius: var(--ct-radius);
  transition: all var(--ct-transition-fast);
}

.ct-modal__close:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-modal__body {
  padding: var(--ct-space-6);
  overflow-y: auto;
  flex: 1;
}

.ct-modal__footer {
  padding: var(--ct-space-4) var(--ct-space-6);
  border-top: 1px solid var(--ct-border);
  display: flex;
  justify-content: flex-end;
  gap: var(--ct-space-2);
  flex-shrink: 0;
}

/* ==================== Tooltips ==================== */
.ct-tooltip {
  position: fixed;
  background: #1e293b;
  color: var(--ct-text);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  font-size: var(--ct-text-sm);
  line-height: var(--ct-leading-normal);
  max-width: 250px;
  white-space: normal;
  pointer-events: none;
  z-index: var(--ct-z-tooltip);
  opacity: 0;
  transition: opacity var(--ct-transition-fast);
  box-shadow: var(--ct-shadow-lg);
}

.ct-tooltip--visible {
  opacity: 1;
}

.ct-tooltip::after {
  content: "";
  position: absolute;
  border: 5px solid transparent;
}

.ct-tooltip--top::after {
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-top-color: #1e293b;
}

.ct-tooltip--bottom::after {
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-bottom-color: #1e293b;
}

.ct-tooltip--left::after {
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  border-left-color: #1e293b;
}

.ct-tooltip--right::after {
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  border-right-color: #1e293b;
}

/* ==================== Forms ==================== */
.ct-form-group {
  margin-bottom: var(--ct-space-4);
}

.ct-label {
  display: block;
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
  color: var(--ct-text-muted);
  margin-bottom: var(--ct-space-1);
}

.ct-label__value {
  font-weight: var(--ct-weight-bold);
  color: var(--ct-text);
}

.ct-input {
  width: 100%;
  background: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  padding: var(--ct-space-2) var(--ct-space-3);
  color: var(--ct-text);
  font-size: var(--ct-text-base);
  line-height: var(--ct-leading-normal);
  transition: border-color var(--ct-transition-fast),
              box-shadow var(--ct-transition-fast);
  outline: none;
}

.ct-input:focus {
  border-color: var(--ct-primary);
  box-shadow: 0 0 0 3px var(--ct-primary-ghost);
}

.ct-input::placeholder {
  color: var(--ct-text-dim);
}

.ct-input--sm {
  padding: var(--ct-space-1) var(--ct-space-2);
  font-size: var(--ct-text-sm);
}

.ct-input--error {
  border-color: var(--ct-danger);
}
.ct-input--error:focus {
  box-shadow: 0 0 0 3px var(--ct-danger-ghost);
}

.ct-select {
  width: 100%;
  background: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  padding: var(--ct-space-2) var(--ct-space-3);
  padding-right: var(--ct-space-8);
  color: var(--ct-text);
  font-size: var(--ct-text-base);
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2394a3b8'%3E%3Cpath d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.75rem center;
  cursor: pointer;
  outline: none;
  transition: border-color var(--ct-transition-fast),
              box-shadow var(--ct-transition-fast);
}

.ct-select:focus {
  border-color: var(--ct-primary);
  box-shadow: 0 0 0 3px var(--ct-primary-ghost);
}

/* Slider / Range */
.ct-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
  outline: none;
  cursor: pointer;
}

.ct-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  background: var(--ct-primary);
  border: 2px solid var(--ct-surface);
  border-radius: var(--ct-radius-full);
  cursor: pointer;
  transition: transform var(--ct-transition-fast),
              box-shadow var(--ct-transition-fast);
}

.ct-slider::-webkit-slider-thumb:hover {
  transform: scale(1.2);
  box-shadow: 0 0 0 4px var(--ct-primary-ghost);
}

.ct-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  background: var(--ct-primary);
  border: 2px solid var(--ct-surface);
  border-radius: var(--ct-radius-full);
  cursor: pointer;
}

/* Custom Checkbox */
.ct-checkbox-label {
  display: flex;
  align-items: flex-start;
  gap: var(--ct-space-2);
  cursor: pointer;
  font-size: var(--ct-text-sm);
}

.ct-checkbox {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  min-width: 18px;
  background: var(--ct-bg-alt);
  border: 2px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  cursor: pointer;
  position: relative;
  transition: all var(--ct-transition-fast);
  margin-top: 2px;
}

.ct-checkbox:checked {
  background: var(--ct-primary);
  border-color: var(--ct-primary);
}

.ct-checkbox:checked::after {
  content: "";
  position: absolute;
  top: 1px;
  left: 5px;
  width: 5px;
  height: 9px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.ct-checkbox:focus-visible {
  box-shadow: 0 0 0 3px var(--ct-primary-ghost);
}

.ct-checkbox-label__text {
  color: var(--ct-text);
}

.ct-checkbox-label__desc {
  display: block;
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
  margin-top: 2px;
}

/* Toggle Switch */
.ct-toggle-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-3);
  cursor: pointer;
}

.ct-toggle-label__text {
  font-size: var(--ct-text-sm);
  color: var(--ct-text);
}

.ct-toggle-label__desc {
  display: block;
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-toggle {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
}

.ct-toggle input {
  opacity: 0;
  width: 0;
  height: 0;
  position: absolute;
}

.ct-toggle__slider {
  position: absolute;
  inset: 0;
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
  transition: background var(--ct-transition-fast);
  cursor: pointer;
}

.ct-toggle__slider::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  background: white;
  border-radius: var(--ct-radius-full);
  transition: transform var(--ct-transition-fast);
}

.ct-toggle input:checked + .ct-toggle__slider {
  background: var(--ct-primary);
}

.ct-toggle input:checked + .ct-toggle__slider::after {
  transform: translateX(20px);
}

.ct-toggle input:focus-visible + .ct-toggle__slider {
  box-shadow: 0 0 0 3px var(--ct-primary-ghost);
}

/* ==================== Badges ==================== */
.ct-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.125rem 0.5rem;
  border-radius: var(--ct-radius-full);
  font-size: var(--ct-text-xs);
  font-weight: var(--ct-weight-semibold);
  line-height: 1.5;
  white-space: nowrap;
}

.ct-badge--primary {
  background: var(--ct-primary-ghost);
  color: var(--ct-primary);
}

.ct-badge--accent {
  background: var(--ct-accent-ghost);
  color: var(--ct-accent);
}

.ct-badge--success {
  background: var(--ct-success-ghost);
  color: var(--ct-success);
}

.ct-badge--danger {
  background: var(--ct-danger-ghost);
  color: var(--ct-danger);
}

.ct-badge--warning {
  background: var(--ct-warning-ghost);
  color: var(--ct-warning);
}

.ct-badge--info {
  background: var(--ct-info-ghost);
  color: var(--ct-info);
}

/* ==================== Progress Bar ==================== */
.ct-progress {
  height: 8px;
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
  overflow: hidden;
}

.ct-progress--sm { height: 4px; }
.ct-progress--lg { height: 12px; }

.ct-progress__bar {
  height: 100%;
  background: var(--ct-primary);
  border-radius: var(--ct-radius-full);
  transition: width 0.5s ease;
}

.ct-progress__bar--accent  { background: var(--ct-accent); }
.ct-progress__bar--success { background: var(--ct-success); }
.ct-progress__bar--danger  { background: var(--ct-danger); }

/* ==================== Dropdown ==================== */
.ct-dropdown {
  position: relative;
  display: inline-block;
}

.ct-dropdown__menu {
  position: absolute;
  top: 100%;
  left: 0;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  box-shadow: var(--ct-shadow-lg);
  z-index: var(--ct-z-dropdown);
  min-width: 180px;
  padding: var(--ct-space-1) 0;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-8px);
  transition: all var(--ct-transition-fast);
}

.ct-dropdown--open .ct-dropdown__menu {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

.ct-dropdown__menu--right {
  left: auto;
  right: 0;
}

.ct-dropdown__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) var(--ct-space-4);
  font-size: var(--ct-text-sm);
  color: var(--ct-text);
  cursor: pointer;
  transition: background var(--ct-transition-fast);
  white-space: nowrap;
}

.ct-dropdown__item:hover {
  background: var(--ct-surface-hover);
}

.ct-dropdown__item--disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ct-dropdown__item--danger {
  color: var(--ct-danger);
}

.ct-dropdown__separator {
  height: 1px;
  background: var(--ct-border);
  margin: var(--ct-space-1) 0;
}

/* ==================== Tabs ==================== */
.ct-tab-bar {
  display: flex;
  gap: var(--ct-space-1);
  border-bottom: 1px solid var(--ct-border);
  padding: 0 var(--ct-space-4);
}

.ct-tab {
  padding: var(--ct-space-2) var(--ct-space-4);
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
  color: var(--ct-text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: all var(--ct-transition-fast);
  background: none;
}

.ct-tab:hover {
  color: var(--ct-text);
}

.ct-tab--active {
  color: var(--ct-primary);
  border-bottom-color: var(--ct-primary);
}

/* ==================== Radio Cards ==================== */
.ct-radio-group {
  border: none;
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-2);
}

.ct-radio-card {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  padding: var(--ct-space-3) var(--ct-space-4);
  background: var(--ct-surface);
  border: 2px solid var(--ct-border);
  border-radius: var(--ct-radius);
  cursor: pointer;
  transition: all var(--ct-transition-fast);
}

.ct-radio-card:hover {
  border-color: var(--ct-border-light);
  background: var(--ct-surface-hover);
}

.ct-radio-card__input {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  min-width: 20px;
  border: 2px solid var(--ct-border-light);
  border-radius: var(--ct-radius-full);
  cursor: pointer;
  position: relative;
  transition: all var(--ct-transition-fast);
}

.ct-radio-card__input:checked {
  border-color: var(--ct-primary);
  background: var(--ct-primary);
}

.ct-radio-card__input:checked::after {
  content: "";
  position: absolute;
  top: 4px;
  left: 4px;
  width: 8px;
  height: 8px;
  background: white;
  border-radius: var(--ct-radius-full);
}

.ct-radio-card:has(.ct-radio-card__input:checked) {
  border-color: var(--ct-primary);
  background: var(--ct-primary-ghost);
}

.ct-radio-card__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ct-radio-card__title {
  font-weight: var(--ct-weight-semibold);
  font-size: var(--ct-text-sm);
}

.ct-radio-card__subtitle {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-muted);
}

.ct-radio-card__desc {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

/* ==================== Info List (definition list) ==================== */
.ct-info-list__item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--ct-space-1) 0;
  border-bottom: 1px solid var(--ct-border);
}

.ct-info-list__item:last-child { border-bottom: none; }

.ct-info-list__label {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-info-list__value {
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
}

/* ==================== Shortcuts Table ==================== */
.ct-shortcuts-table {
  width: 100%;
  font-size: var(--ct-text-sm);
}

.ct-shortcuts-table th {
  text-align: left;
  padding: var(--ct-space-2);
  color: var(--ct-text-dim);
  font-weight: var(--ct-weight-medium);
  border-bottom: 1px solid var(--ct-border);
}

.ct-shortcuts-table td {
  padding: var(--ct-space-2);
  border-bottom: 1px solid var(--ct-border);
}

.ct-shortcuts-table tr:last-child td { border-bottom: none; }
"""


# =================================================================
# 4. GAME UI - HUD, action bar, minimap, overlays
# =================================================================

GAME_UI_CSS = """\
/* ================================================================
   GAME UI STYLES
   ================================================================ */

/* ==================== Canvas ==================== */
.ct-game-canvas {
  position: relative;
  cursor: crosshair;
}

.ct-canvas-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 10;
}

/* ==================== HUD ==================== */
.ct-hud {
  position: absolute;
  inset: 0;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  z-index: 50;
  padding: var(--ct-space-3);
}

.ct-hud > * {
  pointer-events: auto;
}

/* Top bar */
.ct-hud-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-3);
  flex-wrap: wrap;
}

/* Bottom bar */
.ct-hud-bottom {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--ct-space-2);
  flex-wrap: wrap;
}

/* ==================== Turn Indicator ==================== */
.ct-turn-indicator {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-4);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  font-family: var(--ct-font-display);
  letter-spacing: 0.05em;
  animation: ct-pulse 3s ease-in-out infinite;
}

.ct-turn-indicator__label {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
  text-transform: uppercase;
}

.ct-turn-indicator__number {
  font-size: var(--ct-text-xl);
  font-weight: var(--ct-weight-bold);
  color: var(--ct-accent);
}

.ct-turn-indicator__separator {
  color: var(--ct-text-dim);
}

.ct-turn-indicator__total {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-muted);
}

/* Round indicator */
.ct-round-indicator {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  font-size: var(--ct-text-sm);
}

.ct-round-indicator__label {
  color: var(--ct-text-dim);
  font-size: var(--ct-text-xs);
  text-transform: uppercase;
}

.ct-round-indicator__number {
  font-weight: var(--ct-weight-bold);
  color: var(--ct-text);
}

/* ==================== Player Display ==================== */
.ct-player-display {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
}

.ct-player-display__swatch {
  width: 24px;
  height: 24px;
  border-radius: var(--ct-radius-full);
  border: 2px solid rgba(255, 255, 255, 0.3);
  flex-shrink: 0;
}

.ct-player-display__name {
  font-weight: var(--ct-weight-semibold);
  font-size: var(--ct-text-sm);
}

/* ==================== Chromaticity Counter ==================== */
.ct-chromaticity {
  display: flex;
  align-items: center;
  gap: var(--ct-space-1);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  font-family: var(--ct-font-mono);
  font-size: var(--ct-text-lg);
}

.ct-chromaticity__icon {
  color: var(--ct-accent);
  font-size: var(--ct-text-xl);
}

.ct-chromaticity__value {
  font-weight: var(--ct-weight-bold);
  color: var(--ct-accent);
  min-width: 2ch;
  text-align: right;
}

.ct-chromaticity__sep {
  color: var(--ct-text-dim);
  font-size: var(--ct-text-sm);
}

.ct-chromaticity__max {
  color: var(--ct-text-dim);
  font-size: var(--ct-text-sm);
}

/* Timer */
.ct-timer {
  display: flex;
  align-items: center;
  gap: var(--ct-space-1);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  font-family: var(--ct-font-mono);
}

.ct-timer__icon { color: var(--ct-text-dim); }
.ct-timer__value { font-weight: var(--ct-weight-bold); }

/* ==================== Action Bar ==================== */
.ct-action-bar {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  background: rgba(20, 20, 40, 0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  padding: var(--ct-space-3);
  border-radius: var(--ct-radius-xl);
  border: 1px solid var(--ct-border);
  box-shadow: var(--ct-shadow-xl);
}

.ct-action-btn {
  width: 64px;
  height: 64px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  background: transparent;
  border: 2px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  cursor: pointer;
  transition: all var(--ct-transition);
  position: relative;
  color: var(--ct-text);
}

.ct-action-btn:hover {
  border-color: var(--ct-primary);
  box-shadow: 0 0 15px rgba(99, 102, 241, 0.3);
  transform: translateY(-2px);
  background: var(--ct-primary-ghost);
}

.ct-action-btn:active {
  transform: translateY(0);
}

.ct-action-btn--active {
  border-color: var(--ct-accent);
  background: var(--ct-accent-ghost);
  animation: ct-action-pulse 1.5s ease-in-out infinite;
}

.ct-action-btn--disabled {
  opacity: 0.4;
  cursor: not-allowed;
  pointer-events: none;
}

.ct-action-btn__icon {
  font-size: 1.5rem;
  line-height: 1;
}

.ct-action-btn__label {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-muted);
  font-weight: var(--ct-weight-medium);
}

.ct-action-btn__cost {
  position: absolute;
  top: -8px;
  right: -8px;
  background: var(--ct-danger);
  color: white;
  font-size: 10px;
  font-weight: var(--ct-weight-bold);
  padding: 1px 5px;
  border-radius: var(--ct-radius-full);
  line-height: 1.4;
}

/* ==================== Palette Selector ==================== */
.ct-palette-selector {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--ct-space-2);
  padding: var(--ct-space-3);
}

.ct-palette-swatch {
  aspect-ratio: 1;
  border-radius: var(--ct-radius);
  cursor: pointer;
  border: 3px solid transparent;
  transition: all var(--ct-transition);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ct-palette-swatch--empty {
  border: 3px dashed var(--ct-border);
  background: var(--ct-surface);
}

.ct-palette-swatch--selected {
  border-color: white;
  box-shadow: 0 0 10px currentColor;
  transform: scale(1.1);
}

.ct-palette-swatch:hover {
  transform: scale(1.05);
  box-shadow: var(--ct-shadow);
}

.ct-palette-swatch__add {
  font-size: var(--ct-text-xl);
  color: var(--ct-text-dim);
}

.ct-palette-swatch__remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  background: var(--ct-danger);
  color: white;
  border-radius: var(--ct-radius-full);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
  transition: transform var(--ct-transition-fast);
}

.ct-palette-swatch__remove:hover {
  transform: scale(1.2);
}

/* Color wheel */
.ct-color-wheel {
  display: flex;
  justify-content: center;
  margin-bottom: var(--ct-space-3);
}

.ct-color-wheel canvas {
  border-radius: var(--ct-radius-full);
  border: 2px solid var(--ct-border);
}

/* ==================== Territory Info Panel ==================== */
.ct-territory-info {
  position: absolute;
  right: var(--ct-space-4);
  top: 50%;
  transform: translateY(-50%);
  background: rgba(20, 20, 40, 0.95);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-4);
  min-width: 220px;
  box-shadow: var(--ct-shadow-xl);
  pointer-events: auto;
}

.ct-territory-info__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--ct-space-3);
}

.ct-territory-info__title {
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-semibold);
  margin: 0;
}

.ct-territory-info__close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ct-text-dim);
  border-radius: var(--ct-radius-sm);
  font-size: var(--ct-text-lg);
  transition: all var(--ct-transition-fast);
}

.ct-territory-info__close:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-territory-info__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-1) 0;
  border-bottom: 1px solid rgba(42, 42, 74, 0.5);
}

.ct-territory-info__row:last-child { border-bottom: none; }

.ct-territory-info__label {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-territory-info__value {
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-medium);
}

.ct-territory-info__color-preview {
  width: 16px;
  height: 16px;
  border-radius: var(--ct-radius-sm);
  border: 1px solid rgba(255, 255, 255, 0.2);
  flex-shrink: 0;
}

.ct-territory-info__section {
  margin-top: var(--ct-space-3);
  padding-top: var(--ct-space-3);
  border-top: 1px solid var(--ct-border);
}

.ct-territory-info__subtitle {
  font-size: var(--ct-text-xs);
  font-weight: var(--ct-weight-semibold);
  color: var(--ct-text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--ct-space-2);
}

.ct-territory-info__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ct-space-1);
}

/* Border strengths */
.ct-border-strengths {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--ct-space-2);
}

.ct-border-strength {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
}

.ct-border-strength__label {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
  font-weight: var(--ct-weight-medium);
  min-width: 20px;
}

/* ==================== Score Bar ==================== */
.ct-score-bar {
  height: 12px;
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
  overflow: hidden;
  position: relative;
  margin-top: var(--ct-space-1);
}

.ct-score-bar__fill {
  height: 100%;
  border-radius: var(--ct-radius-full);
  transition: width 1s ease;
  background: linear-gradient(90deg, var(--ct-primary), var(--ct-accent));
}

.ct-score-bar__label {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--ct-text-xs);
  font-weight: var(--ct-weight-bold);
  color: white;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
}

/* ==================== Minimap ==================== */
.ct-minimap {
  position: fixed;
  bottom: var(--ct-space-4);
  right: var(--ct-space-4);
  width: 200px;
  height: 150px;
  background: rgba(20, 20, 40, 0.92);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  overflow: hidden;
  box-shadow: var(--ct-shadow-lg);
  z-index: var(--ct-z-sticky);
  cursor: pointer;
  pointer-events: auto;
}

.ct-minimap canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.ct-minimap__viewport {
  position: absolute;
  border: 2px solid var(--ct-accent);
  pointer-events: none;
  transition: all 0.1s linear;
}

/* ==================== Game Overlays ==================== */
.ct-victory-overlay,
.ct-defeat-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: var(--ct-space-6);
  z-index: var(--ct-z-overlay);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.ct-victory-overlay {
  background: rgba(16, 185, 129, 0.15);
}

.ct-defeat-overlay {
  background: rgba(239, 68, 68, 0.15);
}

.ct-victory-text,
.ct-defeat-text {
  font-family: var(--ct-font-display);
  font-size: var(--ct-text-5xl);
  font-weight: var(--ct-weight-bold);
  text-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
  animation: ct-scaleIn 0.5s ease;
}

.ct-victory-text { color: var(--ct-success); }
.ct-defeat-text  { color: var(--ct-danger); }

/* ==================== Notifications / Toasts ==================== */
.ct-toast-container {
  position: fixed;
  top: var(--ct-space-4);
  right: var(--ct-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-2);
  z-index: var(--ct-z-toast);
  pointer-events: none;
  max-width: 360px;
}

.ct-toast {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  padding: var(--ct-space-3) var(--ct-space-4);
  box-shadow: var(--ct-shadow-lg);
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  min-width: 280px;
  animation: ct-slideInRight 0.3s ease;
}

.ct-toast--info    { border-left: 4px solid var(--ct-info); }
.ct-toast--success { border-left: 4px solid var(--ct-success); }
.ct-toast--warning { border-left: 4px solid var(--ct-warning); }
.ct-toast--error   { border-left: 4px solid var(--ct-danger); }

.ct-toast--exit {
  animation: ct-slideOutRight 0.3s ease forwards;
}

.ct-toast__icon {
  font-size: var(--ct-text-lg);
  flex-shrink: 0;
}

.ct-toast__message {
  font-size: var(--ct-text-sm);
  flex: 1;
  margin: 0;
}

.ct-toast__close {
  color: var(--ct-text-dim);
  font-size: var(--ct-text-sm);
  padding: var(--ct-space-1);
  border-radius: var(--ct-radius-sm);
  transition: all var(--ct-transition-fast);
  flex-shrink: 0;
}

.ct-toast__close:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

/* ==================== Context Menu ==================== */
.ct-context-menu {
  position: fixed;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  box-shadow: var(--ct-shadow-xl);
  z-index: var(--ct-z-popover);
  min-width: 160px;
  padding: var(--ct-space-1) 0;
  animation: ct-scaleIn 0.15s ease;
}

.ct-context-menu__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) var(--ct-space-4);
  font-size: var(--ct-text-sm);
  color: var(--ct-text);
  cursor: pointer;
  transition: background var(--ct-transition-fast);
}

.ct-context-menu__item:hover {
  background: var(--ct-surface-hover);
}

.ct-context-menu__item--disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ct-context-menu__separator {
  height: 1px;
  background: var(--ct-border);
  margin: var(--ct-space-1) 0;
}

/* ==================== Cell Highlight ==================== */
.ct-cell-highlight {
  position: absolute;
  pointer-events: none;
  border: 3px solid var(--ct-accent);
  border-radius: var(--ct-radius-full);
  animation: ct-expandRing 1.5s ease-out infinite;
}

/* ==================== AI Thinking Indicator ==================== */
.ct-ai-thinking {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  font-size: var(--ct-text-sm);
  color: var(--ct-text-muted);
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  padding: var(--ct-space-2) var(--ct-space-4);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  pointer-events: auto;
}

.ct-ai-thinking__dots span {
  display: inline-block;
  width: 8px;
  height: 8px;
  background: var(--ct-primary);
  border-radius: var(--ct-radius-full);
  animation: ct-bounce 1.4s ease-in-out infinite;
}

.ct-ai-thinking__dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.ct-ai-thinking__dots span:nth-child(3) {
  animation-delay: 0.4s;
}

/* ==================== Zoom Controls ==================== */
.ct-zoom-controls {
  display: flex;
  background: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  overflow: hidden;
}

.ct-zoom-controls .ct-btn {
  border-radius: 0;
  border: none;
  border-right: 1px solid var(--ct-border);
}

.ct-zoom-controls .ct-btn:last-child {
  border-right: none;
}

/* ==================== Controls page ==================== */
.ct-controls {
  max-width: 600px;
  margin: 0 auto;
  padding: var(--ct-space-6);
}

.ct-controls__section {
  margin-bottom: var(--ct-space-8);
  padding-bottom: var(--ct-space-6);
  border-bottom: 1px solid var(--ct-border);
}

.ct-controls__section:last-child {
  border-bottom: none;
}

.ct-controls__heading {
  font-size: var(--ct-text-xl);
  margin-bottom: var(--ct-space-4);
}

.ct-controls__heading-icon {
  margin-right: var(--ct-space-2);
}

.ct-controls__summary {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  padding: var(--ct-space-3) var(--ct-space-4);
  margin-bottom: var(--ct-space-4);
}

.ct-controls__summary-text {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-muted);
  margin: 0;
}

.ct-controls__start-btn {
  width: 100%;
  font-size: var(--ct-text-lg);
  padding: var(--ct-space-4);
  background: linear-gradient(135deg, var(--ct-accent), var(--ct-primary));
  border: none;
  font-weight: var(--ct-weight-bold);
  letter-spacing: 0.05em;
}

.ct-controls__start-btn:hover {
  box-shadow: var(--ct-shadow-accent-glow);
}

.ct-controls__back-link {
  display: block;
  text-align: center;
  margin-top: var(--ct-space-3);
  font-size: var(--ct-text-sm);
  color: var(--ct-text-dim);
}

/* ==================== Settings ==================== */
.ct-settings {
  background: var(--ct-surface);
  border-radius: var(--ct-radius-xl);
  max-width: 600px;
  width: 90%;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.ct-settings__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-4) var(--ct-space-6);
  border-bottom: 1px solid var(--ct-border);
}

.ct-settings__title {
  font-size: var(--ct-text-xl);
  margin: 0;
}

.ct-settings__close {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ct-text-dim);
  font-size: var(--ct-text-xl);
  border-radius: var(--ct-radius);
  transition: all var(--ct-transition-fast);
}

.ct-settings__close:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-settings__panel {
  padding: var(--ct-space-6);
  overflow-y: auto;
  flex: 1;
}

.ct-settings__section-title {
  font-size: var(--ct-text-base);
  margin-bottom: var(--ct-space-4);
}

.ct-settings__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--ct-space-2);
  padding: var(--ct-space-4) var(--ct-space-6);
  border-top: 1px solid var(--ct-border);
}

/* ==================== Player List ==================== */
.ct-player-list__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) 0;
}

.ct-player-dot {
  width: 12px;
  height: 12px;
  border-radius: var(--ct-radius-full);
  flex-shrink: 0;
  border: 1px solid rgba(255, 255, 255, 0.2);
}

/* Score list */
.ct-score-list__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) 0;
}

.ct-score-list__bar {
  flex: 1;
}

.ct-score-list__value {
  font-family: var(--ct-font-mono);
  font-size: var(--ct-text-xs);
  min-width: 3ch;
  text-align: right;
}

/* ==================== Gallery ==================== */
.ct-gallery {
  padding: var(--ct-space-6);
}

.ct-gallery__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-4);
  margin-bottom: var(--ct-space-6);
  flex-wrap: wrap;
}

.ct-gallery__title-row {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
}

.ct-gallery__title {
  margin: 0;
}

.ct-gallery__controls {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
}

.ct-gallery__sort {
  display: flex;
  gap: var(--ct-space-1);
}

.ct-gallery__sort-btn--active {
  background: var(--ct-primary-ghost);
  color: var(--ct-primary);
  border-color: var(--ct-primary);
}

.ct-gallery__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--ct-space-4);
}

/* Artwork card */
.ct-artwork-card {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  overflow: hidden;
  transition: all var(--ct-transition);
}

.ct-artwork-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--ct-shadow-lg);
}

.ct-artwork-card__image {
  position: relative;
  aspect-ratio: 4/3;
  overflow: hidden;
}

.ct-artwork-card__image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ct-artwork-card__overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity var(--ct-transition-fast);
}

.ct-artwork-card:hover .ct-artwork-card__overlay {
  opacity: 1;
}

.ct-artwork-card__body {
  padding: var(--ct-space-3);
}

.ct-artwork-card__title {
  font-size: var(--ct-text-sm);
  font-weight: var(--ct-weight-semibold);
  margin: 0 0 var(--ct-space-1);
}

.ct-artwork-card__date {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-artwork-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-2) var(--ct-space-3);
  border-top: 1px solid var(--ct-border);
}

.ct-artwork-card__players {
  display: flex;
  gap: var(--ct-space-1);
}

/* Gallery detail */
.ct-gallery-detail {
  position: fixed;
  inset: 0;
  z-index: var(--ct-z-modal);
}

.ct-gallery-detail__backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.ct-gallery-detail__content {
  position: relative;
  display: flex;
  height: 100%;
  padding: var(--ct-space-6);
  gap: var(--ct-space-6);
  z-index: 1;
}

.ct-gallery-detail__image {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ct-gallery-detail__image img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: var(--ct-radius);
}

.ct-gallery-detail__meta {
  width: 320px;
  background: var(--ct-surface);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-6);
  overflow-y: auto;
  flex-shrink: 0;
}

.ct-gallery-detail__title {
  font-size: var(--ct-text-xl);
  margin-bottom: var(--ct-space-4);
  outline: none;
  border-bottom: 1px dashed var(--ct-border);
  padding-bottom: var(--ct-space-1);
}

.ct-gallery-detail__table {
  width: 100%;
  margin-bottom: var(--ct-space-4);
}

.ct-gallery-detail__table th,
.ct-gallery-detail__table td {
  padding: var(--ct-space-2);
  font-size: var(--ct-text-sm);
  text-align: left;
  border-bottom: 1px solid var(--ct-border);
}

.ct-gallery-detail__table th {
  color: var(--ct-text-dim);
  font-weight: var(--ct-weight-medium);
  width: 40%;
}

.ct-gallery-detail__breakdown {
  margin-bottom: var(--ct-space-4);
}

.ct-gallery-detail__breakdown h4 {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-dim);
  margin-bottom: var(--ct-space-2);
}

.ct-gallery-detail__bars {
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-1);
}

.ct-gallery-detail__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ct-space-2);
  margin-bottom: var(--ct-space-4);
}

.ct-gallery-detail__close {
  position: absolute;
  top: var(--ct-space-4);
  right: var(--ct-space-4);
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--ct-text-2xl);
  color: var(--ct-text-muted);
  background: var(--ct-surface);
  border-radius: var(--ct-radius-full);
  z-index: 10;
  transition: all var(--ct-transition-fast);
}

.ct-gallery-detail__close:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-gallery-detail__nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--ct-text-3xl);
  color: var(--ct-text-muted);
  background: var(--ct-surface);
  border-radius: var(--ct-radius-full);
  z-index: 10;
  transition: all var(--ct-transition-fast);
}

.ct-gallery-detail__nav:hover {
  background: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-gallery-detail__nav--prev { left: var(--ct-space-2); }
.ct-gallery-detail__nav--next { right: calc(320px + var(--ct-space-8)); }

/* Gallery empty state */
.ct-gallery__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--ct-space-16);
  text-align: center;
}

.ct-gallery__empty-icon {
  font-size: 4rem;
  margin-bottom: var(--ct-space-4);
  opacity: 0.4;
}

.ct-gallery__empty-title {
  font-size: var(--ct-text-2xl);
  margin-bottom: var(--ct-space-2);
}

.ct-gallery__empty-desc {
  color: var(--ct-text-dim);
  max-width: 300px;
  margin-bottom: var(--ct-space-6);
}

/* ==================== Tutorial ==================== */
.ct-tutorial {
  position: fixed;
  inset: 0;
  z-index: var(--ct-z-overlay);
}

.ct-tutorial__backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
}

.ct-tutorial__spotlight {
  position: absolute;
  border-radius: var(--ct-radius);
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.75);
  transition: all 0.4s ease;
  pointer-events: none;
}

.ct-tutorial__card {
  position: absolute;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-6);
  max-width: 380px;
  box-shadow: var(--ct-shadow-xl);
  z-index: 10;
  animation: ct-scaleIn 0.3s ease;
}

.ct-tutorial__arrow {
  position: absolute;
  width: 0;
  height: 0;
  border: 8px solid transparent;
}

.ct-tutorial__card-header {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  margin-bottom: var(--ct-space-3);
}

.ct-tutorial__step-badge {
  background: var(--ct-primary-ghost);
  color: var(--ct-primary);
  font-size: var(--ct-text-xs);
  font-weight: var(--ct-weight-semibold);
  padding: 0.125rem 0.5rem;
  border-radius: var(--ct-radius-full);
}

.ct-tutorial__step-title {
  font-size: var(--ct-text-lg);
  margin: 0;
}

.ct-tutorial__step-message {
  font-size: var(--ct-text-sm);
  color: var(--ct-text-muted);
  line-height: var(--ct-leading-relaxed);
  margin-bottom: var(--ct-space-4);
}

.ct-tutorial__illustration {
  min-height: 60px;
  margin-bottom: var(--ct-space-3);
}

.ct-tutorial__card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ct-space-2);
}

.ct-tutorial__counter {
  font-size: var(--ct-text-xs);
  color: var(--ct-text-dim);
}

.ct-tutorial__skip-row {
  display: flex;
  justify-content: center;
  gap: var(--ct-space-3);
  margin-top: var(--ct-space-3);
  padding-top: var(--ct-space-3);
  border-top: 1px solid var(--ct-border);
}

/* Tutorial progress dots */
.ct-tutorial__progress {
  position: fixed;
  bottom: var(--ct-space-6);
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: var(--ct-space-2);
  z-index: var(--ct-z-overlay);
}

.ct-tutorial__dot {
  width: 10px;
  height: 10px;
  border-radius: var(--ct-radius-full);
  background: var(--ct-border);
  transition: all var(--ct-transition-fast);
}

.ct-tutorial__dot--completed {
  background: var(--ct-primary);
}

.ct-tutorial__dot--current {
  background: var(--ct-accent);
  transform: scale(1.3);
  animation: ct-pulse 2s ease-in-out infinite;
}

/* Tutorial welcome */
.ct-tutorial__welcome {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}

.ct-tutorial__welcome-content {
  text-align: center;
  max-width: 480px;
  animation: ct-fadeIn 0.5s ease;
}

.ct-tutorial__welcome-logo {
  margin-bottom: var(--ct-space-6);
}

.ct-tutorial__welcome-diamond {
  font-size: 5rem;
  color: var(--ct-accent);
  animation: ct-glow 2s ease-in-out infinite;
  display: inline-block;
}

.ct-tutorial__welcome-title {
  font-size: var(--ct-text-4xl);
  line-height: var(--ct-leading-tight);
  margin-bottom: var(--ct-space-4);
}

.ct-tutorial__welcome-desc {
  font-size: var(--ct-text-base);
  color: var(--ct-text-muted);
  line-height: var(--ct-leading-relaxed);
  margin-bottom: var(--ct-space-8);
}

.ct-tutorial__welcome-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--ct-space-3);
}

/* ==================== Loading / Error overlays ==================== */
.ct-loading-overlay {
  position: fixed;
  inset: 0;
  background: rgba(10, 10, 26, 0.85);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--ct-z-overlay);
}

.ct-loading-overlay__content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--ct-space-4);
}

.ct-loading-overlay__text {
  color: var(--ct-text-muted);
  margin: 0;
}

.ct-error-overlay {
  position: fixed;
  inset: 0;
  background: rgba(10, 10, 26, 0.9);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--ct-z-overlay);
}

.ct-error-overlay__content {
  text-align: center;
  max-width: 500px;
  padding: var(--ct-space-8);
}

.ct-error-overlay__icon {
  font-size: 3rem;
  color: var(--ct-danger);
  display: block;
  margin-bottom: var(--ct-space-4);
}

.ct-error-overlay__title {
  color: var(--ct-danger);
  margin-bottom: var(--ct-space-2);
}

.ct-error-overlay__message {
  color: var(--ct-text-muted);
  margin-bottom: var(--ct-space-4);
}

.ct-error-overlay__stack {
  text-align: left;
  font-size: var(--ct-text-xs);
  max-height: 200px;
  overflow: auto;
  margin-bottom: var(--ct-space-4);
}
"""


# =================================================================
# 5. ANIMATIONS - Keyframes, transitions, effects
# =================================================================

ANIMATIONS_CSS = """\
/* ================================================================
   ANIMATIONS & TRANSITIONS
   ================================================================ */

/* ==================== Keyframes ==================== */

@keyframes ct-fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes ct-fadeOut {
  from { opacity: 1; }
  to   { opacity: 0; }
}

@keyframes ct-slideInLeft {
  from { transform: translateX(-20px); opacity: 0; }
  to   { transform: translateX(0);     opacity: 1; }
}

@keyframes ct-slideInRight {
  from { transform: translateX(20px); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
}

@keyframes ct-slideOutRight {
  from { transform: translateX(0);    opacity: 1; }
  to   { transform: translateX(100%); opacity: 0; }
}

@keyframes ct-slideInUp {
  from { transform: translateY(20px); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

@keyframes ct-slideInDown {
  from { transform: translateY(-20px); opacity: 0; }
  to   { transform: translateY(0);     opacity: 1; }
}

@keyframes ct-slideOutUp {
  from { transform: translateY(0);     opacity: 1; }
  to   { transform: translateY(-20px); opacity: 0; }
}

@keyframes ct-slideOutDown {
  from { transform: translateY(0);    opacity: 1; }
  to   { transform: translateY(20px); opacity: 0; }
}

@keyframes ct-scaleIn {
  from { transform: scale(0.8); opacity: 0; }
  to   { transform: scale(1);   opacity: 1; }
}

@keyframes ct-scaleOut {
  from { transform: scale(1);   opacity: 1; }
  to   { transform: scale(0.8); opacity: 0; }
}

@keyframes ct-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.7; }
}

@keyframes ct-glow {
  0%, 100% {
    box-shadow: 0 0 5px var(--ct-primary);
  }
  50% {
    box-shadow: 0 0 20px var(--ct-primary),
                0 0 40px rgba(99, 102, 241, 0.2);
  }
}

@keyframes ct-shake {
  0%, 100% { transform: translateX(0); }
  10%      { transform: translateX(-5px); }
  20%      { transform: translateX(5px); }
  30%      { transform: translateX(-5px); }
  40%      { transform: translateX(5px); }
  50%      { transform: translateX(-3px); }
  60%      { transform: translateX(3px); }
  70%      { transform: translateX(-2px); }
  80%      { transform: translateX(2px); }
  90%      { transform: translateX(-1px); }
}

@keyframes ct-shimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}

@keyframes ct-gradientShift {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

@keyframes ct-float {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-10px); }
}

@keyframes ct-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

@keyframes ct-bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40%           { transform: translateY(-10px); }
}

@keyframes ct-expandRing {
  0%   { transform: scale(0.8); opacity: 1; }
  100% { transform: scale(1.5); opacity: 0; }
}

@keyframes ct-ripple {
  0%   { transform: scale(0); opacity: 0.5; }
  100% { transform: scale(4); opacity: 0; }
}

@keyframes ct-typewriter {
  from { width: 0; }
  to   { width: 100%; }
}

@keyframes ct-action-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
  50%      { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
}

@keyframes ct-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.05); }
}

@keyframes ct-flash {
  0%, 50%, 100% { opacity: 1; }
  25%, 75%      { opacity: 0; }
}

@keyframes ct-colorCycle {
  0%   { filter: hue-rotate(0deg); }
  100% { filter: hue-rotate(360deg); }
}

@keyframes ct-slideRotate {
  from { transform: translateX(-100%) rotate(-5deg); opacity: 0; }
  to   { transform: translateX(0) rotate(0deg);       opacity: 1; }
}

@keyframes ct-countUp {
  from { transform: translateY(100%); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}

/* ==================== Animation Utility Classes ==================== */

.ct-animate-fadeIn      { animation: ct-fadeIn 0.3s ease both; }
.ct-animate-fadeOut     { animation: ct-fadeOut 0.3s ease both; }
.ct-animate-slideInLeft  { animation: ct-slideInLeft 0.3s ease both; }
.ct-animate-slideInRight { animation: ct-slideInRight 0.3s ease both; }
.ct-animate-slideInUp    { animation: ct-slideInUp 0.3s ease both; }
.ct-animate-slideInDown  { animation: ct-slideInDown 0.3s ease both; }
.ct-animate-scaleIn      { animation: ct-scaleIn 0.3s ease both; }
.ct-animate-pulse        { animation: ct-pulse 2s ease-in-out infinite; }
.ct-animate-glow         { animation: ct-glow 2s ease-in-out infinite; }
.ct-animate-shake        { animation: ct-shake 0.5s ease; }
.ct-animate-float        { animation: ct-float 3s ease-in-out infinite; }
.ct-animate-spin         { animation: ct-spin 1s linear infinite; }
.ct-animate-bounce       { animation: ct-bounce 1.4s ease-in-out infinite; }
.ct-animate-breathe      { animation: ct-breathe 4s ease-in-out infinite; }

/* Delay modifiers */
.ct-animate-delay-100 { animation-delay: 100ms; }
.ct-animate-delay-200 { animation-delay: 200ms; }
.ct-animate-delay-300 { animation-delay: 300ms; }
.ct-animate-delay-500 { animation-delay: 500ms; }
.ct-animate-delay-1000 { animation-delay: 1000ms; }

/* Duration modifiers */
.ct-animate-fast { animation-duration: 150ms; }
.ct-animate-slow { animation-duration: 600ms; }

/* ==================== Transition Utilities ==================== */

.ct-transition-none { transition: none !important; }
.ct-transition-fast { transition: all 150ms ease; }
.ct-transition      { transition: all 250ms ease; }
.ct-transition-slow { transition: all 400ms ease; }
.ct-transition-transform { transition: transform 250ms ease; }
.ct-transition-opacity   { transition: opacity 250ms ease; }
.ct-transition-colors    { transition: color 250ms ease, background-color 250ms ease, border-color 250ms ease; }

/* ==================== Hover Effects ==================== */

.ct-hover-lift:hover {
  transform: translateY(-3px);
  box-shadow: var(--ct-shadow-lg);
}

.ct-hover-glow:hover {
  box-shadow: var(--ct-shadow-glow);
}

.ct-hover-scale:hover {
  transform: scale(1.05);
}

.ct-hover-bright:hover {
  filter: brightness(1.1);
}

.ct-hover-dim:hover {
  opacity: 0.8;
}

/* ==================== Scroll-triggered Animations ==================== */

.ct-animate-on-scroll {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}

.ct-animate-on-scroll--visible {
  opacity: 1;
  transform: translateY(0);
}

.ct-animate-on-scroll--left {
  opacity: 0;
  transform: translateX(-30px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}

.ct-animate-on-scroll--left.ct-animate-on-scroll--visible {
  opacity: 1;
  transform: translateX(0);
}

.ct-animate-on-scroll--right {
  opacity: 0;
  transform: translateX(30px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}

.ct-animate-on-scroll--right.ct-animate-on-scroll--visible {
  opacity: 1;
  transform: translateX(0);
}

.ct-animate-on-scroll--scale {
  opacity: 0;
  transform: scale(0.9);
  transition: opacity 0.6s ease, transform 0.6s ease;
}

.ct-animate-on-scroll--scale.ct-animate-on-scroll--visible {
  opacity: 1;
  transform: scale(1);
}

/* Stagger children */
.ct-stagger > *:nth-child(1) { transition-delay: 0ms; }
.ct-stagger > *:nth-child(2) { transition-delay: 50ms; }
.ct-stagger > *:nth-child(3) { transition-delay: 100ms; }
.ct-stagger > *:nth-child(4) { transition-delay: 150ms; }
.ct-stagger > *:nth-child(5) { transition-delay: 200ms; }
.ct-stagger > *:nth-child(6) { transition-delay: 250ms; }
.ct-stagger > *:nth-child(7) { transition-delay: 300ms; }
.ct-stagger > *:nth-child(8) { transition-delay: 350ms; }

/* ==================== Spinner ==================== */

.ct-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid var(--ct-border);
  border-top-color: var(--ct-primary);
  border-radius: var(--ct-radius-full);
  animation: ct-spin 0.8s linear infinite;
}

.ct-spinner--sm { width: 16px; height: 16px; border-width: 2px; }
.ct-spinner--lg { width: 48px; height: 48px; border-width: 4px; }
.ct-spinner--xl { width: 64px; height: 64px; border-width: 5px; }

.ct-spinner--accent {
  border-top-color: var(--ct-accent);
}

/* ==================== Skeleton Loading ==================== */

.ct-skeleton {
  background: var(--ct-border);
  border-radius: var(--ct-radius);
  overflow: hidden;
  position: relative;
}

.ct-skeleton::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(255, 255, 255, 0.05),
    transparent
  );
  background-size: 200% 100%;
  animation: ct-shimmer 1.5s ease-in-out infinite;
}

.ct-skeleton--text {
  height: 1rem;
  width: 80%;
  margin-bottom: var(--ct-space-2);
}

.ct-skeleton--text:last-child {
  width: 60%;
}

.ct-skeleton--title {
  height: 1.5rem;
  width: 50%;
  margin-bottom: var(--ct-space-3);
}

.ct-skeleton--circle {
  width: 48px;
  height: 48px;
  border-radius: var(--ct-radius-full);
}

.ct-skeleton--card {
  height: 200px;
  width: 100%;
}

.ct-skeleton--avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--ct-radius-full);
}

.ct-skeleton--btn {
  height: 36px;
  width: 100px;
  border-radius: var(--ct-radius);
}

/* ==================== Page Transitions ==================== */

.ct-page-enter {
  opacity: 0;
  transform: translateY(10px);
}

.ct-page-enter-active {
  opacity: 1;
  transform: translateY(0);
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.ct-page-exit {
  opacity: 1;
  transform: translateY(0);
}

.ct-page-exit-active {
  opacity: 0;
  transform: translateY(-10px);
  transition: opacity 0.2s ease, transform 0.2s ease;
}

/* Cross-fade variant */
.ct-page-crossfade-enter {
  opacity: 0;
}

.ct-page-crossfade-enter-active {
  opacity: 1;
  transition: opacity 0.3s ease;
}

.ct-page-crossfade-exit {
  opacity: 1;
}

.ct-page-crossfade-exit-active {
  opacity: 0;
  transition: opacity 0.2s ease;
}

/* ==================== Reduced Motion ==================== */

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }

  .ct-animate-on-scroll {
    opacity: 1;
    transform: none;
  }

  .ct-spinner {
    animation: ct-spin 2s linear infinite;
  }
}
"""
