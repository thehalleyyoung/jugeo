"""CSS code generators — design system, layout, components, game UI, animations."""
from . import register


@register("generative_art")
def generate_css():
    css = """
/* ===== Design System & Variables ===== */
:root {
  /* Color palette */
  --ct-bg: #0a0a1a;
  --ct-bg-alt: #0f0f24;
  --ct-surface: #141428;
  --ct-surface-hover: #1a1a3e;
  --ct-surface-active: #202050;
  --ct-border: #2a2a4a;
  --ct-border-light: #3a3a5a;

  /* Brand colors */
  --ct-primary: #6366f1;
  --ct-primary-hover: #818cf8;
  --ct-primary-active: #4f46e5;
  --ct-primary-glow: rgba(99, 102, 241, 0.3);
  --ct-accent: #f59e0b;
  --ct-accent-hover: #fbbf24;
  --ct-accent-active: #d97706;
  --ct-accent-glow: rgba(245, 158, 11, 0.3);

  /* Semantic colors */
  --ct-success: #10b981;
  --ct-success-hover: #34d399;
  --ct-success-bg: rgba(16, 185, 129, 0.1);
  --ct-warning: #f59e0b;
  --ct-warning-bg: rgba(245, 158, 11, 0.1);
  --ct-danger: #ef4444;
  --ct-danger-hover: #f87171;
  --ct-danger-bg: rgba(239, 68, 68, 0.1);
  --ct-info: #3b82f6;
  --ct-info-bg: rgba(59, 130, 246, 0.1);

  /* Text */
  --ct-text: #e2e8f0;
  --ct-text-secondary: #cbd5e1;
  --ct-text-muted: #94a3b8;
  --ct-text-dim: #64748b;
  --ct-text-inverse: #0a0a1a;

  /* Typography */
  --ct-font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --ct-font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace;
  --ct-font-size-xs: 0.75rem;
  --ct-font-size-sm: 0.875rem;
  --ct-font-size-base: 1rem;
  --ct-font-size-lg: 1.125rem;
  --ct-font-size-xl: 1.25rem;
  --ct-font-size-2xl: 1.5rem;
  --ct-font-size-3xl: 1.875rem;
  --ct-font-size-4xl: 2.25rem;

  /* Spacing */
  --ct-space-1: 0.25rem;
  --ct-space-2: 0.5rem;
  --ct-space-3: 0.75rem;
  --ct-space-4: 1rem;
  --ct-space-5: 1.25rem;
  --ct-space-6: 1.5rem;
  --ct-space-8: 2rem;
  --ct-space-10: 2.5rem;
  --ct-space-12: 3rem;
  --ct-space-16: 4rem;

  /* Borders & Radius */
  --ct-radius-sm: 4px;
  --ct-radius: 8px;
  --ct-radius-lg: 12px;
  --ct-radius-xl: 16px;
  --ct-radius-full: 9999px;

  /* Shadows */
  --ct-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
  --ct-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -2px rgba(0, 0, 0, 0.3);
  --ct-shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -4px rgba(0, 0, 0, 0.4);
  --ct-shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.4);
  --ct-shadow-glow: 0 0 20px var(--ct-primary-glow);

  /* Transitions */
  --ct-transition-fast: 150ms ease;
  --ct-transition: 250ms ease;
  --ct-transition-slow: 500ms ease;

  /* Z-index layers */
  --ct-z-base: 1;
  --ct-z-dropdown: 100;
  --ct-z-sticky: 200;
  --ct-z-overlay: 300;
  --ct-z-modal: 400;
  --ct-z-toast: 500;
  --ct-z-tooltip: 600;
  --ct-z-max: 9999;

  /* Sidebar */
  --ct-sidebar-width: 280px;
  --ct-sidebar-collapsed: 60px;
  --ct-header-height: 56px;
}

/* CSS Reset */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  scroll-behavior: smooth;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-size-adjust: 100%;
  -webkit-text-size-adjust: 100%;
  font-size: 16px;
  line-height: 1.5;
}

body {
  font-family: var(--ct-font);
  font-size: var(--ct-font-size-base);
  line-height: 1.6;
  color: var(--ct-text);
  background-color: var(--ct-bg);
  min-height: 100vh;
  overflow-x: hidden;
  -webkit-tap-highlight-color: transparent;
}

img,
video,
svg {
  display: block;
  max-width: 100%;
  height: auto;
}

img {
  border-style: none;
}

button,
input,
select,
textarea {
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
  color: inherit;
}

a {
  text-decoration: none;
  color: var(--ct-primary);
}

ul,
ol {
  list-style: none;
}

table {
  border-collapse: collapse;
  border-spacing: 0;
}

/* Typography */
h1 {
  font-size: var(--ct-font-size-4xl);
  font-weight: 800;
  line-height: 1.1;
  margin-bottom: var(--ct-space-6);
  color: var(--ct-text);
  letter-spacing: -0.02em;
}

h2 {
  font-size: var(--ct-font-size-3xl);
  font-weight: 700;
  line-height: 1.2;
  margin-bottom: var(--ct-space-5);
  color: var(--ct-text);
  letter-spacing: -0.01em;
}

h3 {
  font-size: var(--ct-font-size-2xl);
  font-weight: 700;
  line-height: 1.25;
  margin-bottom: var(--ct-space-4);
  color: var(--ct-text);
}

h4 {
  font-size: var(--ct-font-size-xl);
  font-weight: 600;
  line-height: 1.3;
  margin-bottom: var(--ct-space-3);
  color: var(--ct-text);
}

h5 {
  font-size: var(--ct-font-size-lg);
  font-weight: 600;
  line-height: 1.4;
  margin-bottom: var(--ct-space-3);
  color: var(--ct-text);
}

h6 {
  font-size: var(--ct-font-size-base);
  font-weight: 600;
  line-height: 1.4;
  margin-bottom: var(--ct-space-2);
  color: var(--ct-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

p {
  margin-bottom: var(--ct-space-4);
  color: var(--ct-text-secondary);
  line-height: 1.7;
}

a:hover {
  color: var(--ct-primary-hover);
  text-decoration: underline;
}

strong {
  font-weight: 700;
  color: var(--ct-text);
}

em {
  font-style: italic;
}

small {
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text-muted);
}

code {
  font-family: var(--ct-font-mono);
  font-size: 0.875em;
  padding: 0.15em 0.4em;
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  color: var(--ct-primary-hover);
}

pre code {
  display: block;
  padding: var(--ct-space-4);
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  overflow-x: auto;
  line-height: 1.6;
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text);
}

blockquote {
  padding-left: var(--ct-space-4);
  border-left: 3px solid var(--ct-primary);
  color: var(--ct-text-secondary);
  font-style: italic;
  margin-bottom: var(--ct-space-4);
}

mark {
  background-color: rgba(245, 158, 11, 0.25);
  color: var(--ct-text);
  padding: 0.1em 0.3em;
  border-radius: var(--ct-radius-sm);
}

::selection {
  background-color: rgba(99, 102, 241, 0.35);
  color: var(--ct-text);
}

/* Scrollbar Styling */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--ct-bg);
  border-radius: var(--ct-radius-full);
}

::-webkit-scrollbar-thumb {
  background: var(--ct-border);
  border-radius: var(--ct-radius-full);
  border: 2px solid var(--ct-bg);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--ct-border-light);
}

::-webkit-scrollbar-corner {
  background: var(--ct-bg);
}

/* Focus Styles */
:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--ct-bg), 0 0 0 4px var(--ct-primary);
  border-radius: var(--ct-radius-sm);
}

/* Utility Text Classes */
.text-xs { font-size: var(--ct-font-size-xs); }
.text-sm { font-size: var(--ct-font-size-sm); }
.text-base { font-size: var(--ct-font-size-base); }
.text-lg { font-size: var(--ct-font-size-lg); }
.text-xl { font-size: var(--ct-font-size-xl); }
.text-2xl { font-size: var(--ct-font-size-2xl); }
.text-3xl { font-size: var(--ct-font-size-3xl); }
.text-4xl { font-size: var(--ct-font-size-4xl); }
.text-muted { color: var(--ct-text-muted); }
.text-primary { color: var(--ct-primary); }
.text-accent { color: var(--ct-accent); }
.text-success { color: var(--ct-success); }
.text-danger { color: var(--ct-danger); }
.text-secondary { color: var(--ct-text-secondary); }
.text-center { text-align: center; }
.text-right { text-align: right; }
.text-left { text-align: left; }
.font-mono { font-family: var(--ct-font-mono); }
.font-bold { font-weight: 700; }
.font-semibold { font-weight: 600; }
.font-normal { font-weight: 400; }
.uppercase { text-transform: uppercase; letter-spacing: 0.05em; }
.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ===== Layout ===== */

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 var(--ct-space-6);
  width: 100%;
}

.container-sm {
  max-width: 768px;
  margin: 0 auto;
  padding: 0 var(--ct-space-6);
  width: 100%;
}

.container-lg {
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 var(--ct-space-6);
  width: 100%;
}

/* Grid System */
.grid {
  display: grid;
  gap: var(--ct-space-4);
}

.grid-2 {
  grid-template-columns: repeat(2, 1fr);
}

.grid-3 {
  grid-template-columns: repeat(3, 1fr);
}

.grid-4 {
  grid-template-columns: repeat(4, 1fr);
}

.grid-auto {
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
}

/* Flexbox */
.flex {
  display: flex;
}

.flex-col {
  display: flex;
  flex-direction: column;
}

.flex-wrap {
  flex-wrap: wrap;
}

.flex-center {
  display: flex;
  align-items: center;
  justify-content: center;
}

.flex-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.flex-end {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.items-center {
  align-items: center;
}

.items-start {
  align-items: flex-start;
}

.items-end {
  align-items: flex-end;
}

.justify-center {
  justify-content: center;
}

.justify-between {
  justify-content: space-between;
}

.flex-1 {
  flex: 1;
}

.flex-shrink-0 {
  flex-shrink: 0;
}

.gap-1 { gap: var(--ct-space-1); }
.gap-2 { gap: var(--ct-space-2); }
.gap-3 { gap: var(--ct-space-3); }
.gap-4 { gap: var(--ct-space-4); }
.gap-5 { gap: var(--ct-space-5); }
.gap-6 { gap: var(--ct-space-6); }
.gap-8 { gap: var(--ct-space-8); }

/* App Layout */
.ct-app {
  display: grid;
  grid-template-columns: var(--ct-sidebar-width) 1fr;
  grid-template-rows: var(--ct-header-height) 1fr;
  min-height: 100vh;
  width: 100%;
  overflow: hidden;
}

/* Header */
.ct-header {
  position: sticky;
  top: 0;
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--ct-space-6);
  background-color: rgba(10, 10, 26, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--ct-border);
  z-index: var(--ct-z-sticky);
  height: var(--ct-header-height);
}

.ct-header__logo {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  font-weight: 700;
  font-size: var(--ct-font-size-lg);
  color: var(--ct-text);
}

.ct-header__logo img {
  width: 28px;
  height: 28px;
}

.ct-header__nav {
  display: flex;
  align-items: center;
  gap: var(--ct-space-4);
}

/* Sidebar */
.ct-sidebar {
  position: fixed;
  top: var(--ct-header-height);
  left: 0;
  bottom: 0;
  width: var(--ct-sidebar-width);
  background-color: var(--ct-surface);
  border-right: 1px solid var(--ct-border);
  overflow-y: auto;
  overflow-x: hidden;
  transition: width var(--ct-transition);
  z-index: var(--ct-z-sticky);
  display: flex;
  flex-direction: column;
}

.ct-sidebar.collapsed {
  width: var(--ct-sidebar-collapsed);
}

.ct-sidebar.collapsed .ct-sidebar-nav__label {
  display: none;
}

.ct-sidebar.collapsed .ct-sidebar-nav__item {
  justify-content: center;
  padding: var(--ct-space-3);
}

/* Sidebar Navigation */
.ct-sidebar-nav {
  display: flex;
  flex-direction: column;
  padding: var(--ct-space-4) var(--ct-space-3);
  gap: var(--ct-space-1);
  flex: 1;
}

.ct-sidebar-nav__section {
  margin-top: var(--ct-space-4);
  margin-bottom: var(--ct-space-2);
  padding: 0 var(--ct-space-3);
  font-size: var(--ct-font-size-xs);
  font-weight: 600;
  color: var(--ct-text-dim);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.ct-sidebar-nav__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  color: var(--ct-text-muted);
  cursor: pointer;
  transition: all var(--ct-transition-fast);
  font-size: var(--ct-font-size-sm);
  font-weight: 500;
  white-space: nowrap;
}

.ct-sidebar-nav__item:hover {
  background-color: var(--ct-surface-hover);
  color: var(--ct-text);
}

.ct-sidebar-nav__item.active {
  background-color: rgba(99, 102, 241, 0.15);
  color: var(--ct-primary);
  font-weight: 600;
}

.ct-sidebar-nav__item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background-color: var(--ct-primary);
  border-radius: 0 var(--ct-radius-sm) var(--ct-radius-sm) 0;
}

.ct-sidebar-nav__icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}

.ct-sidebar-nav__label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Main Content */
.ct-main {
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--ct-space-6);
  position: relative;
  grid-column: 2;
  grid-row: 2;
  margin-left: var(--ct-sidebar-width);
}

/* Page Tabs */
.ct-page {
  display: none;
}

.ct-page.active {
  display: block;
  animation: fadeIn var(--ct-transition) ease;
}

/* Responsive */
@media (max-width: 768px) {
  .ct-app {
    grid-template-columns: 1fr;
    grid-template-rows: var(--ct-header-height) 1fr auto;
  }

  .ct-sidebar {
    position: fixed;
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100%;
    height: auto;
    flex-direction: row;
    border-right: none;
    border-top: 1px solid var(--ct-border);
    z-index: var(--ct-z-sticky);
    overflow-x: auto;
    overflow-y: hidden;
  }

  .ct-sidebar-nav {
    flex-direction: row;
    padding: var(--ct-space-2);
    gap: var(--ct-space-1);
    overflow-x: auto;
  }

  .ct-sidebar-nav__section {
    display: none;
  }

  .ct-sidebar-nav__item {
    flex-direction: column;
    font-size: var(--ct-font-size-xs);
    padding: var(--ct-space-2);
    gap: var(--ct-space-1);
    min-width: 60px;
    text-align: center;
  }

  .ct-sidebar-nav__label {
    font-size: 10px;
  }

  .ct-main {
    margin-left: 0;
    padding: var(--ct-space-4);
    padding-bottom: calc(var(--ct-space-16) + 20px);
  }

  .grid-2 {
    grid-template-columns: 1fr;
  }

  .grid-3 {
    grid-template-columns: 1fr;
  }

  .grid-4 {
    grid-template-columns: repeat(2, 1fr);
  }

  .container {
    padding: 0 var(--ct-space-4);
  }

  h1 { font-size: var(--ct-font-size-2xl); }
  h2 { font-size: var(--ct-font-size-xl); }
  h3 { font-size: var(--ct-font-size-lg); }

  .flex-between {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--ct-space-3);
  }
}

@media (max-width: 1024px) {
  .ct-sidebar {
    width: var(--ct-sidebar-collapsed);
  }

  .ct-sidebar .ct-sidebar-nav__label {
    display: none;
  }

  .ct-sidebar .ct-sidebar-nav__item {
    justify-content: center;
    padding: var(--ct-space-3);
  }

  .ct-main {
    margin-left: var(--ct-sidebar-collapsed);
  }

  .grid-4 {
    grid-template-columns: repeat(2, 1fr);
  }

  h1 { font-size: var(--ct-font-size-3xl); }
  h2 { font-size: var(--ct-font-size-2xl); }
}

@media (min-width: 1440px) {
  .container {
    max-width: 1400px;
  }

  .container-lg {
    max-width: 1600px;
  }

  :root {
    --ct-space-6: 1.75rem;
    --ct-space-8: 2.5rem;
  }
}

/* Spacing Utilities */
.mt-1 { margin-top: var(--ct-space-1); }
.mt-2 { margin-top: var(--ct-space-2); }
.mt-3 { margin-top: var(--ct-space-3); }
.mt-4 { margin-top: var(--ct-space-4); }
.mt-5 { margin-top: var(--ct-space-5); }
.mt-6 { margin-top: var(--ct-space-6); }
.mt-8 { margin-top: var(--ct-space-8); }

.mb-1 { margin-bottom: var(--ct-space-1); }
.mb-2 { margin-bottom: var(--ct-space-2); }
.mb-3 { margin-bottom: var(--ct-space-3); }
.mb-4 { margin-bottom: var(--ct-space-4); }
.mb-5 { margin-bottom: var(--ct-space-5); }
.mb-6 { margin-bottom: var(--ct-space-6); }
.mb-8 { margin-bottom: var(--ct-space-8); }

.ml-1 { margin-left: var(--ct-space-1); }
.ml-2 { margin-left: var(--ct-space-2); }
.ml-3 { margin-left: var(--ct-space-3); }
.ml-4 { margin-left: var(--ct-space-4); }
.ml-5 { margin-left: var(--ct-space-5); }
.ml-6 { margin-left: var(--ct-space-6); }
.ml-8 { margin-left: var(--ct-space-8); }

.mr-1 { margin-right: var(--ct-space-1); }
.mr-2 { margin-right: var(--ct-space-2); }
.mr-3 { margin-right: var(--ct-space-3); }
.mr-4 { margin-right: var(--ct-space-4); }
.mr-5 { margin-right: var(--ct-space-5); }
.mr-6 { margin-right: var(--ct-space-6); }
.mr-8 { margin-right: var(--ct-space-8); }

.mx-auto { margin-left: auto; margin-right: auto; }

.p-1 { padding: var(--ct-space-1); }
.p-2 { padding: var(--ct-space-2); }
.p-3 { padding: var(--ct-space-3); }
.p-4 { padding: var(--ct-space-4); }
.p-5 { padding: var(--ct-space-5); }
.p-6 { padding: var(--ct-space-6); }
.p-8 { padding: var(--ct-space-8); }

.px-1 { padding-left: var(--ct-space-1); padding-right: var(--ct-space-1); }
.px-2 { padding-left: var(--ct-space-2); padding-right: var(--ct-space-2); }
.px-3 { padding-left: var(--ct-space-3); padding-right: var(--ct-space-3); }
.px-4 { padding-left: var(--ct-space-4); padding-right: var(--ct-space-4); }

.py-1 { padding-top: var(--ct-space-1); padding-bottom: var(--ct-space-1); }
.py-2 { padding-top: var(--ct-space-2); padding-bottom: var(--ct-space-2); }
.py-3 { padding-top: var(--ct-space-3); padding-bottom: var(--ct-space-3); }
.py-4 { padding-top: var(--ct-space-4); padding-bottom: var(--ct-space-4); }

/* Width/Height Utilities */
.w-full { width: 100%; }
.h-full { height: 100%; }
.w-screen { width: 100vw; }
.h-screen { height: 100vh; }
.min-h-screen { min-height: 100vh; }

/* Overflow */
.overflow-hidden { overflow: hidden; }
.overflow-auto { overflow: auto; }
.overflow-scroll { overflow: scroll; }

/* Position */
.relative { position: relative; }
.absolute { position: absolute; }
.fixed { position: fixed; }
.sticky { position: sticky; top: 0; }

/* Display */
.hidden { display: none; }
.block { display: block; }
.inline-block { display: inline-block; }
.inline-flex { display: inline-flex; }

/* ===== Components ===== */

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) var(--ct-space-4);
  border: none;
  border-radius: var(--ct-radius);
  font-family: var(--ct-font);
  font-size: var(--ct-font-size-sm);
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  transition: all var(--ct-transition-fast);
  user-select: none;
  white-space: nowrap;
  text-decoration: none;
  position: relative;
  overflow: hidden;
}

.btn--primary {
  background-color: var(--ct-primary);
  color: #ffffff;
}

.btn--primary:hover {
  background-color: var(--ct-primary-hover);
  box-shadow: var(--ct-shadow-glow);
}

.btn--primary:active {
  background-color: var(--ct-primary-active);
  transform: translateY(1px);
}

.btn--primary:focus-visible {
  box-shadow: 0 0 0 2px var(--ct-bg), 0 0 0 4px var(--ct-primary);
}

.btn--accent {
  background-color: var(--ct-accent);
  color: var(--ct-text-inverse);
}

.btn--accent:hover {
  background-color: var(--ct-accent-hover);
  box-shadow: 0 0 20px var(--ct-accent-glow);
}

.btn--accent:active {
  background-color: var(--ct-accent-active);
  transform: translateY(1px);
}

.btn--success {
  background-color: var(--ct-success);
  color: #ffffff;
}

.btn--success:hover {
  background-color: var(--ct-success-hover);
  box-shadow: 0 0 20px rgba(16, 185, 129, 0.3);
}

.btn--success:active {
  transform: translateY(1px);
}

.btn--danger {
  background-color: var(--ct-danger);
  color: #ffffff;
}

.btn--danger:hover {
  background-color: var(--ct-danger-hover);
  box-shadow: 0 0 20px rgba(239, 68, 68, 0.3);
}

.btn--danger:active {
  transform: translateY(1px);
}

.btn--ghost {
  background-color: transparent;
  border: 1px solid var(--ct-border);
  color: var(--ct-text);
}

.btn--ghost:hover {
  background-color: var(--ct-surface);
  border-color: var(--ct-border-light);
}

.btn--ghost:active {
  background-color: var(--ct-surface-active);
}

.btn--outline {
  background-color: transparent;
  border: 1px solid var(--ct-primary);
  color: var(--ct-primary);
}

.btn--outline:hover {
  background-color: var(--ct-primary);
  color: #ffffff;
}

.btn--outline:active {
  background-color: var(--ct-primary-active);
  color: #ffffff;
}

.btn--sm {
  padding: var(--ct-space-1) var(--ct-space-3);
  font-size: var(--ct-font-size-xs);
  border-radius: var(--ct-radius-sm);
}

.btn--lg {
  padding: var(--ct-space-3) var(--ct-space-6);
  font-size: var(--ct-font-size-base);
  border-radius: var(--ct-radius-lg);
}

.btn--icon {
  width: 36px;
  height: 36px;
  padding: 0;
  border-radius: var(--ct-radius-full);
  justify-content: center;
}

.btn--icon.btn--sm {
  width: 28px;
  height: 28px;
}

.btn--icon.btn--lg {
  width: 44px;
  height: 44px;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

.btn-group {
  display: flex;
}

.btn-group .btn {
  border-radius: 0;
  margin-left: -1px;
}

.btn-group .btn:first-child {
  border-radius: var(--ct-radius) 0 0 var(--ct-radius);
  margin-left: 0;
}

.btn-group .btn:last-child {
  border-radius: 0 var(--ct-radius) var(--ct-radius) 0;
}

/* Cards */
.card {
  background-color: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-5);
  box-shadow: var(--ct-shadow);
  transition: all var(--ct-transition);
}

.card:hover {
  transform: translateY(-2px);
  box-shadow: var(--ct-shadow-lg);
}

.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--ct-space-4);
  border-bottom: 1px solid var(--ct-border);
  margin-bottom: var(--ct-space-4);
}

.card__body {
  padding: var(--ct-space-2) 0;
}

.card__footer {
  padding-top: var(--ct-space-4);
  border-top: 1px solid var(--ct-border);
  display: flex;
  justify-content: flex-end;
  gap: var(--ct-space-3);
  margin-top: var(--ct-space-4);
}

.card--interactive {
  cursor: pointer;
}

.card--interactive:hover {
  border-color: var(--ct-primary);
  box-shadow: var(--ct-shadow-glow);
}

.card--flush {
  padding: 0;
}

/* Modal */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: var(--ct-z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn var(--ct-transition-fast) ease;
}

.modal {
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius-xl);
  box-shadow: var(--ct-shadow-xl);
  max-width: 560px;
  width: 90%;
  max-height: 80vh;
  overflow: hidden;
  animation: slideUp var(--ct-transition) ease;
  border: 1px solid var(--ct-border);
}

.modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-4) var(--ct-space-5);
  border-bottom: 1px solid var(--ct-border);
}

.modal__header h3 {
  margin-bottom: 0;
}

.modal__body {
  padding: var(--ct-space-5);
  overflow-y: auto;
  max-height: calc(80vh - 130px);
}

.modal__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--ct-space-3);
  padding: var(--ct-space-4) var(--ct-space-5);
  border-top: 1px solid var(--ct-border);
}

.modal--lg {
  max-width: 800px;
}

.modal--fullscreen {
  width: 100vw;
  height: 100vh;
  max-width: none;
  max-height: none;
  border-radius: 0;
}

/* Tooltip */
.tooltip {
  position: absolute;
  background-color: var(--ct-bg-alt);
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  padding: var(--ct-space-2) var(--ct-space-3);
  border-radius: var(--ct-radius);
  box-shadow: var(--ct-shadow-lg);
  z-index: var(--ct-z-tooltip);
  pointer-events: none;
  max-width: 240px;
  line-height: 1.4;
  white-space: normal;
  border: 1px solid var(--ct-border);
}

.tooltip::after {
  content: '';
  position: absolute;
  width: 8px;
  height: 8px;
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  transform: rotate(45deg);
}

.tooltip--top {
  margin-bottom: var(--ct-space-2);
}

.tooltip--top::after {
  bottom: -5px;
  left: 50%;
  margin-left: -4px;
  border-top: none;
  border-left: none;
}

.tooltip--bottom {
  margin-top: var(--ct-space-2);
}

.tooltip--bottom::after {
  top: -5px;
  left: 50%;
  margin-left: -4px;
  border-bottom: none;
  border-right: none;
}

.tooltip--left {
  margin-right: var(--ct-space-2);
}

.tooltip--left::after {
  right: -5px;
  top: 50%;
  margin-top: -4px;
  border-bottom: none;
  border-left: none;
}

.tooltip--right {
  margin-left: var(--ct-space-2);
}

.tooltip--right::after {
  left: -5px;
  top: 50%;
  margin-top: -4px;
  border-top: none;
  border-right: none;
}

/* Form Elements */
.form-group {
  margin-bottom: var(--ct-space-5);
  position: relative;
}

.form-label {
  display: block;
  font-size: var(--ct-font-size-sm);
  font-weight: 500;
  margin-bottom: var(--ct-space-2);
  color: var(--ct-text-secondary);
}

.form-input {
  width: 100%;
  padding: var(--ct-space-2) var(--ct-space-3);
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  transition: all var(--ct-transition-fast);
  outline: none;
  line-height: 1.5;
}

.form-input:focus {
  border-color: var(--ct-primary);
  box-shadow: 0 0 0 3px var(--ct-primary-glow);
}

.form-input::placeholder {
  color: var(--ct-text-dim);
}

.form-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.form-select {
  width: 100%;
  padding: var(--ct-space-2) var(--ct-space-3);
  padding-right: var(--ct-space-8);
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  transition: all var(--ct-transition-fast);
  outline: none;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  background-size: 12px;
}

.form-select:focus {
  border-color: var(--ct-primary);
  box-shadow: 0 0 0 3px var(--ct-primary-glow);
}

.form-textarea {
  width: 100%;
  padding: var(--ct-space-3);
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  transition: all var(--ct-transition-fast);
  outline: none;
  min-height: 100px;
  resize: vertical;
  line-height: 1.6;
}

.form-textarea:focus {
  border-color: var(--ct-primary);
  box-shadow: 0 0 0 3px var(--ct-primary-glow);
}

.form-textarea::placeholder {
  color: var(--ct-text-dim);
}

.form-checkbox {
  appearance: none;
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border: 2px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  background-color: var(--ct-bg-alt);
  cursor: pointer;
  transition: all var(--ct-transition-fast);
  position: relative;
  flex-shrink: 0;
}

.form-checkbox:checked {
  background-color: var(--ct-primary);
  border-color: var(--ct-primary);
}

.form-checkbox:checked::after {
  content: '';
  position: absolute;
  left: 5px;
  top: 2px;
  width: 5px;
  height: 9px;
  border: solid #ffffff;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.form-checkbox:focus-visible {
  box-shadow: 0 0 0 3px var(--ct-primary-glow);
}

.form-radio {
  appearance: none;
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border: 2px solid var(--ct-border);
  border-radius: var(--ct-radius-full);
  background-color: var(--ct-bg-alt);
  cursor: pointer;
  transition: all var(--ct-transition-fast);
  position: relative;
  flex-shrink: 0;
}

.form-radio:checked {
  border-color: var(--ct-primary);
}

.form-radio:checked::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 8px;
  height: 8px;
  border-radius: var(--ct-radius-full);
  background-color: var(--ct-primary);
}

.form-radio:focus-visible {
  box-shadow: 0 0 0 3px var(--ct-primary-glow);
}

.form-help {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  margin-top: var(--ct-space-1);
}

.form-error {
  color: var(--ct-danger);
  font-size: var(--ct-font-size-xs);
  margin-top: var(--ct-space-1);
}

.form-error + .form-input,
.form-input.error {
  border-color: var(--ct-danger);
}

/* Badge */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.15em 0.6em;
  font-size: var(--ct-font-size-xs);
  font-weight: 600;
  border-radius: var(--ct-radius-full);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  line-height: 1.5;
  white-space: nowrap;
}

.badge--primary {
  background-color: rgba(99, 102, 241, 0.15);
  color: var(--ct-primary-hover);
}

.badge--accent {
  background-color: rgba(245, 158, 11, 0.15);
  color: var(--ct-accent-hover);
}

.badge--success {
  background-color: var(--ct-success-bg);
  color: var(--ct-success-hover);
}

.badge--danger {
  background-color: var(--ct-danger-bg);
  color: var(--ct-danger-hover);
}

.badge--info {
  background-color: var(--ct-info-bg);
  color: var(--ct-info);
}

/* Progress Bar */
.progress-bar {
  height: 8px;
  background-color: var(--ct-bg-alt);
  border-radius: var(--ct-radius-full);
  overflow: hidden;
  width: 100%;
}

.progress-bar__fill {
  height: 100%;
  border-radius: var(--ct-radius-full);
  transition: width var(--ct-transition-slow);
  background: linear-gradient(90deg, var(--ct-primary), var(--ct-primary-hover));
}

.progress-bar__fill--accent {
  background: linear-gradient(90deg, var(--ct-accent), var(--ct-accent-hover));
}

.progress-bar__fill--success {
  background: linear-gradient(90deg, var(--ct-success), var(--ct-success-hover));
}

/* Tabs */
.tabs {
  display: flex;
  border-bottom: 1px solid var(--ct-border);
  gap: var(--ct-space-1);
  overflow-x: auto;
}

.tab {
  padding: var(--ct-space-3) var(--ct-space-4);
  color: var(--ct-text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all var(--ct-transition-fast);
  font-size: var(--ct-font-size-sm);
  font-weight: 500;
  white-space: nowrap;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
}

.tab:hover {
  color: var(--ct-text);
  background-color: var(--ct-surface-hover);
}

.tab.tab-active {
  color: var(--ct-primary);
  border-bottom-color: var(--ct-primary);
  font-weight: 600;
}

/* Toggle Switch */
.toggle-switch {
  position: relative;
  width: 44px;
  height: 24px;
  background-color: var(--ct-bg-alt);
  border-radius: var(--ct-radius-full);
  cursor: pointer;
  transition: background-color var(--ct-transition-fast);
  border: 1px solid var(--ct-border);
  flex-shrink: 0;
}

.toggle-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  background-color: var(--ct-text-muted);
  border-radius: var(--ct-radius-full);
  transition: all var(--ct-transition-fast);
}

.toggle-switch.active {
  background-color: var(--ct-primary);
  border-color: var(--ct-primary);
}

.toggle-switch.active::after {
  transform: translateX(20px);
  background-color: #ffffff;
}

/* Dropdown */
.dropdown {
  position: relative;
  display: inline-block;
}

.dropdown__menu {
  position: absolute;
  top: 100%;
  left: 0;
  background-color: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  box-shadow: var(--ct-shadow-lg);
  z-index: var(--ct-z-dropdown);
  min-width: 180px;
  opacity: 0;
  transform: translateY(-8px);
  transition: all var(--ct-transition-fast);
  pointer-events: none;
  padding: var(--ct-space-1) 0;
  margin-top: var(--ct-space-1);
}

.dropdown.open .dropdown__menu {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.dropdown__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) var(--ct-space-3);
  cursor: pointer;
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  transition: background-color var(--ct-transition-fast);
  white-space: nowrap;
}

.dropdown__item:hover {
  background-color: var(--ct-surface-hover);
}

.dropdown__item.active {
  color: var(--ct-primary);
}

.dropdown__divider {
  border-top: 1px solid var(--ct-border);
  margin: var(--ct-space-1) 0;
}

/* Slider / Range Input */
input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  background: var(--ct-bg-alt);
  border-radius: var(--ct-radius-full);
  outline: none;
  cursor: pointer;
}

input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: var(--ct-radius-full);
  background: var(--ct-primary);
  cursor: pointer;
  border: 2px solid var(--ct-bg);
  box-shadow: var(--ct-shadow-sm);
  transition: all var(--ct-transition-fast);
}

input[type="range"]::-webkit-slider-thumb:hover {
  transform: scale(1.15);
  box-shadow: var(--ct-shadow-glow);
}

input[type="range"]::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: var(--ct-radius-full);
  background: var(--ct-primary);
  cursor: pointer;
  border: 2px solid var(--ct-bg);
  box-shadow: var(--ct-shadow-sm);
}

input[type="range"]::-moz-range-track {
  height: 6px;
  background: var(--ct-bg-alt);
  border-radius: var(--ct-radius-full);
}

/* Divider */
.divider {
  border: none;
  border-top: 1px solid var(--ct-border);
  margin: var(--ct-space-4) 0;
}

.divider--lg {
  margin: var(--ct-space-8) 0;
}

/* Avatar */
.avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--ct-radius-full);
  object-fit: cover;
  border: 2px solid var(--ct-border);
  flex-shrink: 0;
}

.avatar--sm {
  width: 28px;
  height: 28px;
}

.avatar--lg {
  width: 56px;
  height: 56px;
}

.avatar--xl {
  width: 72px;
  height: 72px;
}

/* Skeleton Loading */
.skeleton {
  background-color: var(--ct-bg-alt);
  border-radius: var(--ct-radius);
  overflow: hidden;
  position: relative;
}

.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.04) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.skeleton--text {
  height: 1em;
  width: 80%;
  margin-bottom: var(--ct-space-2);
}

.skeleton--title {
  height: 1.5em;
  width: 60%;
  margin-bottom: var(--ct-space-3);
}

.skeleton--avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--ct-radius-full);
}

.skeleton--image {
  width: 100%;
  aspect-ratio: 16 / 9;
}

/* Alert */
.alert {
  padding: var(--ct-space-3) var(--ct-space-4);
  border-radius: var(--ct-radius);
  border-left: 4px solid var(--ct-border);
  margin-bottom: var(--ct-space-4);
  font-size: var(--ct-font-size-sm);
  line-height: 1.5;
}

.alert--info {
  background-color: var(--ct-info-bg);
  border-left-color: var(--ct-info);
  color: var(--ct-text);
}

.alert--success {
  background-color: var(--ct-success-bg);
  border-left-color: var(--ct-success);
  color: var(--ct-text);
}

.alert--warning {
  background-color: var(--ct-warning-bg);
  border-left-color: var(--ct-warning);
  color: var(--ct-text);
}

.alert--danger {
  background-color: var(--ct-danger-bg);
  border-left-color: var(--ct-danger);
  color: var(--ct-text);
}

/* ===== Game UI ===== */

/* Canvas Container */
.ct-canvas-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background-color: var(--ct-bg);
  cursor: grab;
  user-select: none;
  -webkit-user-select: none;
}

.ct-canvas-container:active {
  cursor: grabbing;
}

.ct-canvas-container canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: block;
}

/* HUD Top */
.ct-hud-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  padding: var(--ct-space-4);
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: var(--ct-z-sticky);
  pointer-events: none;
  background: linear-gradient(to bottom, rgba(10, 10, 26, 0.9) 0%, rgba(10, 10, 26, 0.5) 60%, transparent 100%);
}

.ct-hud-top > * {
  pointer-events: auto;
}

/* HUD Bottom */
.ct-hud-bottom {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: var(--ct-space-4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--ct-z-sticky);
  pointer-events: none;
  background: linear-gradient(to top, rgba(10, 10, 26, 0.9) 0%, rgba(10, 10, 26, 0.5) 60%, transparent 100%);
}

.ct-hud-bottom > * {
  pointer-events: auto;
}

/* Action Bar */
.ct-action-bar {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-3);
  background-color: rgba(20, 20, 40, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: var(--ct-radius-xl);
  border: 1px solid var(--ct-border);
  box-shadow: var(--ct-shadow-lg);
}

.ct-action-btn {
  width: 64px;
  height: 64px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background-color: var(--ct-bg-alt);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  cursor: pointer;
  transition: all var(--ct-transition-fast);
  position: relative;
  color: var(--ct-text);
}

.ct-action-btn:hover {
  background-color: var(--ct-surface-hover);
  border-color: var(--ct-primary);
  transform: translateY(-2px);
  box-shadow: var(--ct-shadow-glow);
}

.ct-action-btn.active {
  background-color: rgba(99, 102, 241, 0.2);
  border-color: var(--ct-primary);
  animation: pulse 2s ease-in-out infinite;
}

.ct-action-btn.disabled {
  opacity: 0.4;
  pointer-events: none;
  cursor: default;
}

.ct-action-btn__icon {
  font-size: 24px;
  margin-bottom: var(--ct-space-1);
  line-height: 1;
}

.ct-action-btn__label {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  line-height: 1;
  white-space: nowrap;
}

.ct-action-btn__hotkey {
  position: absolute;
  top: -4px;
  right: -4px;
  background-color: var(--ct-primary);
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
  width: 18px;
  height: 18px;
  border-radius: var(--ct-radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  font-family: var(--ct-font-mono);
}

/* Palette Selector */
.ct-palette-selector {
  display: flex;
  gap: var(--ct-space-2);
  padding: var(--ct-space-3);
  flex-wrap: wrap;
  align-items: center;
}

.ct-swatch {
  width: 36px;
  height: 36px;
  border-radius: var(--ct-radius-full);
  cursor: pointer;
  border: 2px solid transparent;
  transition: all var(--ct-transition-fast);
  position: relative;
  flex-shrink: 0;
}

.ct-swatch:hover {
  transform: scale(1.15);
  box-shadow: var(--ct-shadow);
}

.ct-swatch.selected {
  border-color: #ffffff;
  box-shadow: var(--ct-shadow-glow);
}

.ct-swatch.selected::after {
  content: '';
  position: absolute;
  inset: -4px;
  border: 2px solid var(--ct-primary);
  border-radius: var(--ct-radius-full);
  animation: pulse 2s ease-in-out infinite;
}

.ct-harmony-preview {
  display: flex;
  gap: var(--ct-space-1);
  padding: var(--ct-space-2) var(--ct-space-3);
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius);
  border: 1px solid var(--ct-border);
  align-items: center;
}

/* Territory Info */
.ct-territory-info {
  background-color: rgba(20, 20, 40, 0.9);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-4);
  border: 1px solid var(--ct-border);
  min-width: 200px;
  box-shadow: var(--ct-shadow-lg);
}

.ct-territory-info__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: var(--ct-font-size-sm);
  margin-bottom: var(--ct-space-3);
  color: var(--ct-text);
}

.ct-territory-info__stat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--ct-space-2) 0;
  border-bottom: 1px solid rgba(42, 42, 74, 0.5);
  color: var(--ct-text-secondary);
  font-size: var(--ct-font-size-sm);
}

.ct-territory-info__stat:last-child {
  border-bottom: none;
}

.ct-territory-info__color {
  width: 16px;
  height: 16px;
  border-radius: var(--ct-radius-full);
  border: 1px solid rgba(255, 255, 255, 0.2);
  flex-shrink: 0;
}

/* Minimap */
.ct-minimap {
  position: absolute;
  bottom: 16px;
  right: 16px;
  width: 180px;
  height: 180px;
  background-color: rgba(15, 15, 36, 0.85);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  overflow: hidden;
  z-index: var(--ct-z-sticky);
  pointer-events: auto;
  opacity: 0.8;
  transition: opacity var(--ct-transition-fast);
  box-shadow: var(--ct-shadow-lg);
}

.ct-minimap canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.ct-minimap__viewport {
  position: absolute;
  border: 1px solid var(--ct-accent);
  pointer-events: none;
  box-shadow: 0 0 4px var(--ct-accent-glow);
}

.ct-minimap:hover {
  opacity: 1;
}

/* Turn Indicator */
.ct-turn-indicator {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  padding: var(--ct-space-2) var(--ct-space-4);
  background-color: rgba(20, 20, 40, 0.85);
  border-radius: var(--ct-radius-full);
  border: 1px solid var(--ct-border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.ct-turn-indicator__number {
  font-family: var(--ct-font-mono);
  font-size: var(--ct-font-size-xl);
  font-weight: 700;
  color: var(--ct-accent);
  line-height: 1;
}

.ct-turn-indicator__label {
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text-muted);
  line-height: 1.2;
}

.ct-turn-indicator__phase {
  font-size: var(--ct-font-size-xs);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ct-primary);
  font-weight: 600;
}

/* Score Display */
.ct-score-display {
  font-family: var(--ct-font-mono);
  font-size: var(--ct-font-size-2xl);
  font-weight: 700;
  letter-spacing: 0.02em;
  text-align: center;
}

.ct-score-display__value {
  color: var(--ct-accent);
  font-variant-numeric: tabular-nums;
}

.ct-score-display__label {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 600;
  display: block;
  margin-top: var(--ct-space-1);
}

/* Chromaticity */
.ct-chromaticity {
  display: flex;
  align-items: center;
  gap: var(--ct-space-3);
  padding: var(--ct-space-2) 0;
}

.ct-chromaticity__bar {
  height: 6px;
  flex: 1;
  background-color: var(--ct-bg-alt);
  border-radius: var(--ct-radius-full);
  overflow: hidden;
}

.ct-chromaticity__fill {
  height: 100%;
  border-radius: var(--ct-radius-full);
  background: linear-gradient(90deg, #ef4444, #f59e0b, #10b981, #3b82f6, #8b5cf6, #ec4899);
  transition: width var(--ct-transition);
}

.ct-chromaticity__value {
  font-family: var(--ct-font-mono);
  color: var(--ct-accent);
  font-weight: 600;
  font-size: var(--ct-font-size-sm);
  min-width: 40px;
  text-align: right;
}

/* Player Info */
.ct-player-info {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
}

.ct-player-info__color {
  width: 12px;
  height: 12px;
  border-radius: var(--ct-radius-full);
  border: 1px solid rgba(255, 255, 255, 0.2);
  flex-shrink: 0;
}

.ct-player-info__name {
  font-weight: 600;
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text);
}

.ct-player-info__badge {
  font-size: var(--ct-font-size-xs);
  padding: 0.1em 0.5em;
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius-sm);
  color: var(--ct-text-muted);
  border: 1px solid var(--ct-border);
}

/* Victory Overlay */
.ct-victory-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--ct-z-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  text-align: center;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  background: radial-gradient(
    ellipse at center,
    rgba(99, 102, 241, 0.2) 0%,
    rgba(10, 10, 26, 0.85) 70%
  );
  animation: fadeIn var(--ct-transition-slow) ease;
}

.ct-victory-overlay__title {
  font-size: var(--ct-font-size-4xl);
  font-weight: 800;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
  background: linear-gradient(135deg, var(--ct-primary-hover), var(--ct-accent), var(--ct-primary-hover));
  background-size: 200% 200%;
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: gradientShift 3s ease infinite;
  margin-bottom: var(--ct-space-4);
  line-height: 1.1;
}

.ct-victory-overlay__subtitle {
  font-size: var(--ct-font-size-xl);
  color: var(--ct-text-secondary);
  margin-bottom: var(--ct-space-6);
  max-width: 480px;
}

.ct-victory-overlay__actions {
  display: flex;
  gap: var(--ct-space-4);
  margin-top: var(--ct-space-4);
}

/* Cell Highlight */
.ct-cell-highlight {
  position: absolute;
  pointer-events: none;
  border-radius: var(--ct-radius-full);
  border: 2px solid rgba(99, 102, 241, 0.6);
  animation: expandRing 1.5s ease-out infinite;
}

/* Game Setup */
.ct-game-setup {
  max-width: 600px;
  margin: 0 auto;
  padding: var(--ct-space-6);
}

.ct-game-setup__option {
  margin-bottom: var(--ct-space-4);
  padding: var(--ct-space-4);
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius-lg);
  border: 1px solid var(--ct-border);
}

.ct-game-setup__option:hover {
  border-color: var(--ct-border-light);
}

.ct-game-setup__preview {
  height: 200px;
  background-color: var(--ct-bg-alt);
  border-radius: var(--ct-radius);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1px solid var(--ct-border);
  margin-top: var(--ct-space-4);
}

/* Notification Container */
.ct-notification-container {
  position: fixed;
  top: var(--ct-space-4);
  right: var(--ct-space-4);
  z-index: var(--ct-z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--ct-space-3);
  max-width: 360px;
  pointer-events: none;
}

.ct-notification-container > * {
  pointer-events: auto;
}

/* Toast */
.ct-toast {
  padding: var(--ct-space-3) var(--ct-space-4);
  background-color: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-lg);
  box-shadow: var(--ct-shadow-lg);
  animation: slideLeft var(--ct-transition) ease;
  display: flex;
  gap: var(--ct-space-3);
  align-items: flex-start;
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text);
  max-width: 100%;
}

.ct-toast--success {
  border-left: 3px solid var(--ct-success);
}

.ct-toast--error {
  border-left: 3px solid var(--ct-danger);
}

.ct-toast--warning {
  border-left: 3px solid var(--ct-warning);
}

.ct-toast--info {
  border-left: 3px solid var(--ct-info);
}

.ct-toast__message {
  flex: 1;
  line-height: 1.5;
}

.ct-toast__close {
  background: none;
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  color: var(--ct-text-muted);
  cursor: pointer;
  padding: var(--ct-space-1);
  font-size: var(--ct-font-size-xs);
  margin-left: auto;
  transition: all var(--ct-transition-fast);
  line-height: 1;
}

.ct-toast__close:hover {
  background-color: var(--ct-surface-hover);
  color: var(--ct-text);
}

/* Context Menu */
.ct-context-menu {
  position: fixed;
  background-color: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius);
  box-shadow: var(--ct-shadow-xl);
  z-index: var(--ct-z-dropdown);
  min-width: 180px;
  padding: var(--ct-space-1) 0;
  animation: fadeIn 100ms ease;
}

.ct-context-menu__item {
  display: flex;
  align-items: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-2) var(--ct-space-3);
  cursor: pointer;
  color: var(--ct-text);
  font-size: var(--ct-font-size-sm);
  transition: background-color var(--ct-transition-fast);
  white-space: nowrap;
}

.ct-context-menu__item:hover {
  background-color: var(--ct-surface-hover);
}

.ct-context-menu__item.disabled {
  opacity: 0.5;
  pointer-events: none;
  cursor: default;
}

.ct-context-menu__item__shortcut {
  margin-left: auto;
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-dim);
  font-family: var(--ct-font-mono);
}

.ct-context-menu__divider {
  border-top: 1px solid var(--ct-border);
  margin: var(--ct-space-1) 0;
}

/* ===== Animations ===== */

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes fadeOut {
  from { opacity: 1; }
  to { opacity: 0; }
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slideDown {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slideLeft {
  from { opacity: 0; transform: translateX(20px); }
  to { opacity: 1; transform: translateX(0); }
}

@keyframes slideRight {
  from { opacity: 0; transform: translateX(-20px); }
  to { opacity: 1; transform: translateX(0); }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

@keyframes glow {
  0%, 100% { box-shadow: 0 0 5px var(--ct-primary-glow); }
  50% { box-shadow: 0 0 20px var(--ct-primary-glow); }
}

@keyframes shake {
  0%, 100% { transform: translateX(0); }
  10%, 30%, 50%, 70%, 90% { transform: translateX(-4px); }
  20%, 40%, 60%, 80% { transform: translateX(4px); }
}

@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@keyframes gradientShift {
  0% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@keyframes bounce {
  0%, 20%, 53%, 100% {
    animation-timing-function: cubic-bezier(0.215, 0.61, 0.355, 1);
    transform: translateY(0);
  }
  40%, 43% {
    animation-timing-function: cubic-bezier(0.755, 0.05, 0.855, 0.06);
    transform: translateY(-20px);
  }
  70% {
    animation-timing-function: cubic-bezier(0.755, 0.05, 0.855, 0.06);
    transform: translateY(-10px);
  }
  80% { transform: translateY(0); }
  90% { transform: translateY(-4px); }
}

@keyframes expandRing {
  from { transform: scale(0.8); opacity: 1; }
  to { transform: scale(2); opacity: 0; }
}

@keyframes ripple {
  0% { transform: scale(1); opacity: 0.4; }
  100% { transform: scale(4); opacity: 0; }
}

@keyframes scaleIn {
  from { transform: scale(0.9); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

@keyframes rotateIn {
  from { transform: rotate(-10deg) scale(0.9); opacity: 0; }
  to { transform: rotate(0) scale(1); opacity: 1; }
}

@keyframes typewriter {
  from { width: 0; }
  to { width: 100%; }
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

@keyframes colorCycle {
  0% { filter: hue-rotate(0deg); }
  100% { filter: hue-rotate(360deg); }
}

/* Utility Animation Classes */
.animate-fadeIn {
  animation: fadeIn var(--ct-transition) ease;
}

.animate-slideUp {
  animation: slideUp var(--ct-transition) ease;
}

.animate-slideDown {
  animation: slideDown var(--ct-transition) ease;
}

.animate-slideLeft {
  animation: slideLeft var(--ct-transition) ease;
}

.animate-pulse {
  animation: pulse 2s ease-in-out infinite;
}

.animate-glow {
  animation: glow 2s ease-in-out infinite;
}

.animate-float {
  animation: float 3s ease-in-out infinite;
}

.animate-spin {
  animation: spin 1s linear infinite;
}

.animate-bounce {
  animation: bounce 1s ease;
}

.animate-shake {
  animation: shake 0.5s ease;
}

.animate-scaleIn {
  animation: scaleIn var(--ct-transition) ease;
}

.animate-rotateIn {
  animation: rotateIn var(--ct-transition) ease;
}

.transition-fast {
  transition: all 150ms ease;
}

.transition-normal {
  transition: all 250ms ease;
}

.transition-slow {
  transition: all 500ms ease;
}

.hover-lift:hover {
  transform: translateY(-2px);
  box-shadow: var(--ct-shadow-lg);
}

.hover-glow:hover {
  box-shadow: var(--ct-shadow-glow);
}

.hover-scale:hover {
  transform: scale(1.05);
}

.hover-brightness:hover {
  filter: brightness(1.1);
}

.delay-100 { animation-delay: 100ms; }
.delay-200 { animation-delay: 200ms; }
.delay-300 { animation-delay: 300ms; }
.delay-400 { animation-delay: 400ms; }
.delay-500 { animation-delay: 500ms; }

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

/* ===== Gallery & Tutorial ===== */

/* Gallery Grid */
.ct-gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--ct-space-4);
  padding: var(--ct-space-4) 0;
}

.ct-gallery-card {
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius-lg);
  overflow: hidden;
  border: 1px solid var(--ct-border);
  transition: all var(--ct-transition);
  cursor: pointer;
  position: relative;
}

.ct-gallery-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--ct-shadow-lg);
  border-color: var(--ct-primary);
}

.ct-gallery-card__image {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  background-color: var(--ct-bg-alt);
  display: block;
}

.ct-gallery-card__overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.7) 0%, transparent 50%);
  opacity: 0;
  transition: opacity var(--ct-transition);
  display: flex;
  align-items: flex-end;
  padding: var(--ct-space-4);
}

.ct-gallery-card:hover .ct-gallery-card__overlay {
  opacity: 1;
}

.ct-gallery-card__info {
  padding: var(--ct-space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ct-gallery-card__title {
  font-weight: 600;
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.ct-gallery-card__date {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  flex-shrink: 0;
  margin-left: var(--ct-space-2);
}

.ct-gallery-card__score {
  position: absolute;
  top: 8px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  padding: 0.15em 0.6em;
  font-size: var(--ct-font-size-xs);
  font-weight: 700;
  border-radius: var(--ct-radius-full);
  background-color: rgba(99, 102, 241, 0.85);
  color: #ffffff;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: 1;
}

/* Artwork Detail */
.ct-artwork-detail {
  max-width: 900px;
  margin: 0 auto;
}

.ct-artwork-detail__image {
  width: 100%;
  border-radius: var(--ct-radius-lg);
  background-color: var(--ct-bg-alt);
  display: block;
  border: 1px solid var(--ct-border);
}

.ct-artwork-detail__meta {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--ct-space-4);
  padding: var(--ct-space-5) 0;
}

.ct-artwork-detail__meta dt {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--ct-space-1);
}

.ct-artwork-detail__meta dd {
  font-size: var(--ct-font-size-sm);
  color: var(--ct-text);
  font-weight: 500;
}

.ct-artwork-detail__actions {
  display: flex;
  gap: var(--ct-space-3);
  padding-top: var(--ct-space-4);
  border-top: 1px solid var(--ct-border);
}

/* Gallery Empty State */
.ct-gallery-empty {
  text-align: center;
  padding: var(--ct-space-16) var(--ct-space-4);
  color: var(--ct-text-muted);
}

.ct-gallery-empty__icon {
  font-size: var(--ct-font-size-4xl);
  margin-bottom: var(--ct-space-4);
  opacity: 0.5;
  line-height: 1;
}

.ct-gallery-empty__text {
  font-size: var(--ct-font-size-lg);
  max-width: 400px;
  margin: 0 auto;
  line-height: 1.6;
}

/* Tutorial Overlay */
.ct-tutorial-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--ct-z-overlay);
  pointer-events: auto;
  background-color: rgba(0, 0, 0, 0.7);
  display: none;
}

.ct-tutorial-overlay.active {
  display: block;
}

/* Tutorial Highlight */
.ct-tutorial-highlight {
  position: absolute;
  z-index: 301;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.7);
  border-radius: var(--ct-radius);
  transition: all 300ms ease;
  pointer-events: none;
}

/* Tutorial Step */
.ct-tutorial-step {
  position: absolute;
  z-index: 302;
  max-width: 360px;
  background-color: var(--ct-surface);
  border-radius: var(--ct-radius-lg);
  padding: var(--ct-space-5);
  box-shadow: var(--ct-shadow-xl);
  border: 1px solid var(--ct-border);
  animation: scaleIn var(--ct-transition) ease;
}

.ct-tutorial-step__title {
  font-weight: 700;
  font-size: var(--ct-font-size-lg);
  margin-bottom: var(--ct-space-2);
  color: var(--ct-text);
}

.ct-tutorial-step__message {
  font-size: var(--ct-font-size-base);
  line-height: 1.6;
  color: var(--ct-text-secondary);
  margin-bottom: var(--ct-space-4);
}

.ct-tutorial-step__actions {
  display: flex;
  justify-content: space-between;
  gap: var(--ct-space-3);
  align-items: center;
}

.ct-tutorial-step__counter {
  font-size: var(--ct-font-size-xs);
  color: var(--ct-text-muted);
  text-align: center;
  margin-top: var(--ct-space-3);
}

/* Tutorial Step Positions */
.ct-tutorial-step--top {
  margin-bottom: var(--ct-space-4);
}

.ct-tutorial-step--top::after {
  content: '';
  position: absolute;
  bottom: -8px;
  left: 50%;
  margin-left: -8px;
  width: 0;
  height: 0;
  border-left: 8px solid transparent;
  border-right: 8px solid transparent;
  border-top: 8px solid var(--ct-surface);
}

.ct-tutorial-step--bottom {
  margin-top: var(--ct-space-4);
}

.ct-tutorial-step--bottom::after {
  content: '';
  position: absolute;
  top: -8px;
  left: 50%;
  margin-left: -8px;
  width: 0;
  height: 0;
  border-left: 8px solid transparent;
  border-right: 8px solid transparent;
  border-bottom: 8px solid var(--ct-surface);
}

.ct-tutorial-step--left {
  margin-right: var(--ct-space-4);
}

.ct-tutorial-step--left::after {
  content: '';
  position: absolute;
  right: -8px;
  top: 50%;
  margin-top: -8px;
  width: 0;
  height: 0;
  border-top: 8px solid transparent;
  border-bottom: 8px solid transparent;
  border-left: 8px solid var(--ct-surface);
}

.ct-tutorial-step--right {
  margin-left: var(--ct-space-4);
}

.ct-tutorial-step--right::after {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  margin-top: -8px;
  width: 0;
  height: 0;
  border-top: 8px solid transparent;
  border-bottom: 8px solid transparent;
  border-right: 8px solid var(--ct-surface);
}

/* Progress Dots */
.ct-progress-dots {
  display: flex;
  justify-content: center;
  gap: var(--ct-space-2);
  padding: var(--ct-space-3) 0;
}

.ct-progress-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--ct-radius-full);
  background-color: var(--ct-text-dim);
  transition: all var(--ct-transition-fast);
  cursor: pointer;
}

.ct-progress-dot:hover {
  background-color: var(--ct-text-muted);
}

.ct-progress-dot.active {
  background-color: var(--ct-primary);
  transform: scale(1.25);
}

.ct-progress-dot.completed {
  background-color: var(--ct-success);
}
"""
    return ("", css, "")
