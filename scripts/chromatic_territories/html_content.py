"""HTML content constants for Chromatic Territories UI.

Structured markup for all views, panels, and overlays.  Uses ct- prefix
for IDs and BEM-style class names throughout.
"""


# =================================================================
# 1. MAIN LAYOUT - Full application shell
# =================================================================

MAIN_LAYOUT_HTML = """\
<!-- Chromatic Territories - Main Application Shell -->
<div id="ct-app" class="ct-app">

  <!-- =================== HEADER =================== -->
  <header class="ct-header" role="banner">
    <div class="ct-header__brand">
      <span class="ct-header__logo" aria-hidden="true">&#9670;</span>
      <h1 class="ct-header__title">Chromatic Territories</h1>
    </div>

    <nav class="ct-header__nav" aria-label="Main navigation">
      <a href="#/" class="ct-header__link ct-header__link--active"
         data-route="/">Home</a>
      <a href="#/play" class="ct-header__link"
         data-route="/play">Play</a>
      <a href="#/gallery" class="ct-header__link"
         data-route="/gallery">Gallery</a>
      <a href="#/tutorial" class="ct-header__link"
         data-route="/tutorial">Tutorial</a>
      <a href="#/settings" class="ct-header__link"
         data-route="/settings">Settings</a>
      <a href="#/about" class="ct-header__link"
         data-route="/about">About</a>
    </nav>

    <div class="ct-header__actions">
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-audio-toggle"
              aria-label="Toggle audio" title="Toggle audio">
        <span class="ct-icon">&#128266;</span>
      </button>
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-fullscreen-toggle"
              aria-label="Toggle fullscreen" title="Toggle fullscreen">
        <span class="ct-icon">&#9974;</span>
      </button>
    </div>
  </header>

  <!-- =================== SIDEBAR =================== -->
  <aside class="ct-sidebar" id="ct-sidebar" role="complementary"
         aria-label="Game sidebar">
    <button class="ct-sidebar__toggle" id="ct-sidebar-toggle"
            aria-label="Toggle sidebar" aria-expanded="true">
      <span class="ct-sidebar__toggle-icon">&#9664;</span>
    </button>

    <div class="ct-sidebar__content">
      <!-- Player List -->
      <section class="ct-sidebar__section"
               aria-labelledby="ct-sidebar-players-heading">
        <h2 class="ct-sidebar__heading" id="ct-sidebar-players-heading">Players</h2>
        <ul class="ct-player-list" id="ct-player-list" role="list">
          <!-- Populated by JS: each li.ct-player-list__item -->
        </ul>
      </section>

      <!-- Game Info -->
      <section class="ct-sidebar__section"
               aria-labelledby="ct-sidebar-info-heading">
        <h2 class="ct-sidebar__heading" id="ct-sidebar-info-heading">Game Info</h2>
        <dl class="ct-info-list" id="ct-game-info">
          <div class="ct-info-list__item">
            <dt class="ct-info-list__label">Board Size</dt>
            <dd class="ct-info-list__value" id="ct-info-board-size">--</dd>
          </div>
          <div class="ct-info-list__item">
            <dt class="ct-info-list__label">Total Hexes</dt>
            <dd class="ct-info-list__value" id="ct-info-total-hexes">--</dd>
          </div>
          <div class="ct-info-list__item">
            <dt class="ct-info-list__label">Claimed</dt>
            <dd class="ct-info-list__value" id="ct-info-claimed">--</dd>
          </div>
          <div class="ct-info-list__item">
            <dt class="ct-info-list__label">Unclaimed</dt>
            <dd class="ct-info-list__value" id="ct-info-unclaimed">--</dd>
          </div>
        </dl>
      </section>

      <!-- Composition Scores -->
      <section class="ct-sidebar__section"
               aria-labelledby="ct-sidebar-scores-heading">
        <h2 class="ct-sidebar__heading" id="ct-sidebar-scores-heading">
          Composition Scores
        </h2>
        <div class="ct-score-list" id="ct-score-list">
          <!-- Populated by JS -->
        </div>
      </section>

      <!-- Quick Actions -->
      <section class="ct-sidebar__section"
               aria-labelledby="ct-sidebar-actions-heading">
        <h2 class="ct-sidebar__heading" id="ct-sidebar-actions-heading">
          Quick Actions
        </h2>
        <div class="ct-sidebar__actions">
          <button class="ct-btn ct-btn--ghost ct-btn--sm ct-sidebar__action-btn"
                  data-action="save-snapshot" title="Save artwork snapshot">
            &#128247; Snapshot
          </button>
          <button class="ct-btn ct-btn--ghost ct-btn--sm ct-sidebar__action-btn"
                  data-action="toggle-grid" title="Toggle grid overlay">
            &#9638; Grid
          </button>
          <button class="ct-btn ct-btn--ghost ct-btn--sm ct-sidebar__action-btn"
                  data-action="toggle-coords" title="Toggle coordinates">
            # Coords
          </button>
        </div>
      </section>
    </div>
  </aside>

  <!-- =================== MAIN CONTENT =================== -->
  <main class="ct-main" id="ct-main" role="main">

    <!-- Router Outlet -->
    <div id="ct-router-outlet" class="ct-router-outlet">
      <!-- Page content injected here by router -->
    </div>

    <!-- Canvas Container (shown on /play route) -->
    <section class="ct-canvas-container" id="ct-canvas-container"
             aria-label="Game canvas" style="display:none;">
      <canvas id="ct-canvas-terrain" class="ct-canvas-layer"
              data-layer="terrain" aria-hidden="true"></canvas>
      <canvas id="ct-canvas-territory" class="ct-canvas-layer"
              data-layer="territory" aria-hidden="true"></canvas>
      <canvas id="ct-canvas-effects" class="ct-canvas-layer"
              data-layer="effects" aria-hidden="true"></canvas>
      <canvas id="ct-canvas-ui" class="ct-canvas-layer"
              data-layer="ui" aria-hidden="true"></canvas>

      <!-- HTML overlay on top of canvases -->
      <div class="ct-canvas-overlay" id="ct-canvas-overlay"></div>
    </section>

    <!-- Floating Panels Container -->
    <div class="ct-panels" id="ct-panels" aria-live="polite"></div>
  </main>

  <!-- =================== FOOTER =================== -->
  <footer class="ct-footer" role="contentinfo">
    <div class="ct-footer__inner">
      <p class="ct-footer__credit">
        Chromatic Territories &mdash; Where Strategy Meets Art
      </p>
      <p class="ct-footer__version">
        v<span id="ct-app-version">1.0.0</span>
      </p>
      <nav class="ct-footer__links" aria-label="Footer links">
        <a href="#/about" class="ct-footer__link">About</a>
        <span class="ct-footer__sep" aria-hidden="true">&middot;</span>
        <a href="#/tutorial" class="ct-footer__link">Help</a>
        <span class="ct-footer__sep" aria-hidden="true">&middot;</span>
        <a href="https://github.com" class="ct-footer__link"
           target="_blank" rel="noopener noreferrer">Source</a>
      </nav>
    </div>
  </footer>

  <!-- =================== GLOBAL OVERLAYS =================== -->

  <!-- Toast Notification Container -->
  <div class="ct-toast-container" id="ct-toast-container"
       aria-live="assertive" aria-atomic="true" role="status"></div>

  <!-- Modal Container -->
  <div class="ct-modal-backdrop" id="ct-modal-backdrop"
       role="dialog" aria-modal="true" aria-hidden="true">
    <div class="ct-modal" id="ct-modal">
      <div class="ct-modal__header" id="ct-modal-header">
        <h2 class="ct-modal__title" id="ct-modal-title"></h2>
        <button class="ct-modal__close" id="ct-modal-close"
                aria-label="Close modal">&times;</button>
      </div>
      <div class="ct-modal__body" id="ct-modal-body"></div>
      <div class="ct-modal__footer" id="ct-modal-footer"></div>
    </div>
  </div>

  <!-- Tooltip -->
  <div class="ct-tooltip" id="ct-tooltip" role="tooltip" aria-hidden="true"></div>

  <!-- Context Menu -->
  <div class="ct-context-menu" id="ct-context-menu"
       role="menu" aria-hidden="true"></div>

  <!-- Loading Overlay -->
  <div class="ct-loading-overlay" id="ct-loading-overlay"
       aria-hidden="true" role="alert">
    <div class="ct-loading-overlay__content">
      <div class="ct-spinner ct-spinner--lg" aria-hidden="true"></div>
      <p class="ct-loading-overlay__text" id="ct-loading-text">Loading&hellip;</p>
    </div>
  </div>

  <!-- Error Overlay -->
  <div class="ct-error-overlay" id="ct-error-overlay"
       aria-hidden="true" role="alert">
    <div class="ct-error-overlay__content">
      <span class="ct-error-overlay__icon" aria-hidden="true">&#9888;</span>
      <h2 class="ct-error-overlay__title">Something went wrong</h2>
      <p class="ct-error-overlay__message" id="ct-error-message"></p>
      <pre class="ct-error-overlay__stack" id="ct-error-stack"></pre>
      <button class="ct-btn ct-btn--primary" id="ct-error-dismiss">Dismiss</button>
    </div>
  </div>

</div>

"""


# =================================================================
# 2. GAME HUD - Heads-up display overlay
# =================================================================

GAME_HUD_HTML = """\
<!-- Game HUD Overlay -->
<div class="ct-hud" id="ct-hud" aria-label="Game heads-up display">

  <!-- ========== TOP BAR ========== -->
  <div class="ct-hud-top">

    <!-- Turn Indicator -->
    <div class="ct-turn-indicator" id="ct-turn-indicator"
         aria-live="polite" aria-atomic="true">
      <span class="ct-turn-indicator__label">Turn</span>
      <span class="ct-turn-indicator__number" id="ct-turn-number">1</span>
      <span class="ct-turn-indicator__separator" aria-hidden="true">/</span>
      <span class="ct-turn-indicator__total" id="ct-turn-total">--</span>
    </div>

    <!-- Round Indicator -->
    <div class="ct-round-indicator" id="ct-round-indicator">
      <span class="ct-round-indicator__label">Round</span>
      <span class="ct-round-indicator__number" id="ct-round-number">1</span>
    </div>

    <!-- Current Player -->
    <div class="ct-player-display" id="ct-current-player">
      <div class="ct-player-display__swatch" id="ct-player-swatch"
           aria-hidden="true" style="background-color:#6366f1;"></div>
      <span class="ct-player-display__name" id="ct-player-name">Player 1</span>
      <span class="ct-player-display__badge ct-badge" id="ct-player-type">Human</span>
    </div>

    <!-- Chromaticity Counter -->
    <div class="ct-chromaticity" id="ct-chromaticity"
         aria-label="Chromaticity points">
      <span class="ct-chromaticity__icon" aria-hidden="true">&#9670;</span>
      <span class="ct-chromaticity__value" id="ct-chromaticity-value">0</span>
      <span class="ct-chromaticity__sep" aria-hidden="true">/</span>
      <span class="ct-chromaticity__max" id="ct-chromaticity-max">100</span>
    </div>

    <!-- Timer -->
    <div class="ct-timer" id="ct-timer" aria-live="off" style="display:none;">
      <span class="ct-timer__icon" aria-hidden="true">&#9203;</span>
      <span class="ct-timer__value" id="ct-timer-value">0:00</span>
    </div>
  </div>

  <!-- ========== ACTION BAR ========== -->
  <div class="ct-action-bar" id="ct-action-bar" role="toolbar"
       aria-label="Game actions">

    <button class="ct-action-btn" id="ct-action-expand"
            data-action="expand" data-cost="10"
            data-tooltip="Claim an adjacent unclaimed hex. Cost: 10 chromaticity."
            aria-label="Expand territory">
      <span class="ct-action-btn__icon" aria-hidden="true">&#11041;</span>
      <span class="ct-action-btn__label">Expand</span>
      <span class="ct-action-btn__cost">10</span>
    </button>

    <button class="ct-action-btn" id="ct-action-fortify"
            data-action="fortify" data-cost="15"
            data-tooltip="Strengthen border defenses. Cost: 15 chromaticity."
            aria-label="Fortify territory">
      <span class="ct-action-btn__icon" aria-hidden="true">&#128737;</span>
      <span class="ct-action-btn__label">Fortify</span>
      <span class="ct-action-btn__cost">15</span>
    </button>

    <button class="ct-action-btn" id="ct-action-disrupt"
            data-action="disrupt" data-cost="20"
            data-tooltip="Use color dissonance to weaken an opponent. Cost: 20."
            aria-label="Disrupt opponent">
      <span class="ct-action-btn__icon" aria-hidden="true">&#9889;</span>
      <span class="ct-action-btn__label">Disrupt</span>
      <span class="ct-action-btn__cost">20</span>
    </button>

    <button class="ct-action-btn" id="ct-action-harmonize"
            data-action="harmonize" data-cost="12"
            data-tooltip="Align hex colors with neighbors. Cost: 12."
            aria-label="Harmonize colors">
      <span class="ct-action-btn__icon" aria-hidden="true">&#127925;</span>
      <span class="ct-action-btn__label">Harmonize</span>
      <span class="ct-action-btn__cost">12</span>
    </button>

    <button class="ct-action-btn" id="ct-action-evolve"
            data-action="evolve" data-cost="18"
            data-tooltip="Transform a hex's color within your palette. Cost: 18."
            aria-label="Evolve hex color">
      <span class="ct-action-btn__icon" aria-hidden="true">&#127744;</span>
      <span class="ct-action-btn__label">Evolve</span>
      <span class="ct-action-btn__cost">18</span>
    </button>
  </div>

  <!-- ========== BOTTOM BAR ========== -->
  <div class="ct-hud-bottom">

    <button class="ct-btn ct-btn--accent ct-btn--lg" id="ct-end-turn"
            aria-label="End your turn">
      End Turn <kbd>Space</kbd>
    </button>

    <button class="ct-btn ct-btn--ghost" id="ct-undo"
            aria-label="Undo last action" disabled>
      &#8630; Undo
    </button>

    <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-minimap-toggle"
            aria-label="Toggle minimap" aria-pressed="true"
            title="Toggle minimap (M)">&#9635;</button>

    <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-hud-settings"
            aria-label="Open settings" title="Settings">&#9881;</button>

    <div class="ct-zoom-controls" role="group" aria-label="Zoom controls">
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-zoom-in"
              aria-label="Zoom in" title="Zoom in (+)">+</button>
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-zoom-reset"
              aria-label="Reset zoom" title="Reset zoom">&#8634;</button>
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-zoom-out"
              aria-label="Zoom out" title="Zoom out (-)">&#8722;</button>
    </div>
  </div>

  <!-- ========== SELECTED HEX INFO ========== -->
  <div class="ct-territory-info" id="ct-territory-info"
       aria-label="Selected hex information" style="display:none;">
    <div class="ct-territory-info__header">
      <h3 class="ct-territory-info__title">Selected Hex</h3>
      <button class="ct-territory-info__close" id="ct-territory-info-close"
              aria-label="Close info panel">&times;</button>
    </div>

    <div class="ct-territory-info__row">
      <span class="ct-territory-info__label">Owner</span>
      <span class="ct-territory-info__value ct-flex ct-gap-2">
        <span class="ct-territory-info__color-preview"
              id="ct-hex-owner-color"></span>
        <span id="ct-hex-owner-name">Unclaimed</span>
      </span>
    </div>

    <div class="ct-territory-info__row">
      <span class="ct-territory-info__label">Coordinates</span>
      <span class="ct-territory-info__value">
        q:<span id="ct-hex-q">-</span>
        r:<span id="ct-hex-r">-</span>
      </span>
    </div>

    <div class="ct-territory-info__row">
      <span class="ct-territory-info__label">Terrain</span>
      <span class="ct-territory-info__value" id="ct-hex-terrain">--</span>
    </div>

    <div class="ct-territory-info__row">
      <span class="ct-territory-info__label">Composition</span>
      <span class="ct-territory-info__value">
        <span id="ct-hex-composition">0</span>%
      </span>
    </div>
    <div class="ct-score-bar" aria-label="Composition score">
      <div class="ct-score-bar__fill" id="ct-hex-score-bar"
           style="width:0%;" role="progressbar"
           aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>
    </div>

    <!-- Border Strengths -->
    <div class="ct-territory-info__section">
      <h4 class="ct-territory-info__subtitle">Border Strengths</h4>
      <div class="ct-border-strengths" id="ct-border-strengths">
        <div class="ct-border-strength" data-direction="ne">
          <span class="ct-border-strength__label">NE</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="ne" style="width:0%;"></div>
          </div>
        </div>
        <div class="ct-border-strength" data-direction="e">
          <span class="ct-border-strength__label">E</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="e" style="width:0%;"></div>
          </div>
        </div>
        <div class="ct-border-strength" data-direction="se">
          <span class="ct-border-strength__label">SE</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="se" style="width:0%;"></div>
          </div>
        </div>
        <div class="ct-border-strength" data-direction="sw">
          <span class="ct-border-strength__label">SW</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="sw" style="width:0%;"></div>
          </div>
        </div>
        <div class="ct-border-strength" data-direction="w">
          <span class="ct-border-strength__label">W</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="w" style="width:0%;"></div>
          </div>
        </div>
        <div class="ct-border-strength" data-direction="nw">
          <span class="ct-border-strength__label">NW</span>
          <div class="ct-progress ct-progress--sm">
            <div class="ct-progress__bar" data-dir="nw" style="width:0%;"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Available Actions -->
    <div class="ct-territory-info__section">
      <h4 class="ct-territory-info__subtitle">Available Actions</h4>
      <div class="ct-territory-info__actions" id="ct-hex-actions"></div>
    </div>
  </div>

  <!-- AI Thinking Indicator -->
  <div class="ct-ai-thinking" id="ct-ai-thinking"
       aria-live="polite" style="display:none;">
    <span class="ct-ai-thinking__label">AI is thinking</span>
    <span class="ct-ai-thinking__dots" aria-hidden="true">
      <span>.</span><span>.</span><span>.</span>
    </span>
  </div>

  <!-- Minimap -->
  <div class="ct-minimap" id="ct-minimap" aria-label="Minimap overview">
    <canvas id="ct-minimap-canvas" aria-hidden="true"></canvas>
    <div class="ct-minimap__viewport" id="ct-minimap-viewport"></div>
  </div>
</div>

"""


# =================================================================
# 3. CONTROLS - Game setup controls
# =================================================================

CONTROLS_HTML = """\
<!-- Game Setup Controls -->
<div class="ct-controls" id="ct-controls">

  <!-- ========== PALETTE SELECTION ========== -->
  <section class="ct-controls__section" aria-labelledby="ct-palette-heading">
    <h2 class="ct-controls__heading" id="ct-palette-heading">
      &#127912; Choose Your Palette
    </h2>

    <div class="ct-color-wheel" id="ct-color-wheel"
         aria-label="Color wheel preview">
      <canvas id="ct-color-wheel-canvas" width="200" height="200"
              aria-hidden="true"></canvas>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-harmony-type">Harmony Type</label>
      <select class="ct-select" id="ct-harmony-type" name="harmony-type">
        <option value="complementary">Complementary</option>
        <option value="analogous" selected>Analogous</option>
        <option value="triadic">Triadic</option>
        <option value="split-complementary">Split-Complementary</option>
        <option value="tetradic">Tetradic</option>
      </select>
    </div>

    <button class="ct-btn ct-btn--primary ct-btn--sm" id="ct-generate-palette">
      &#9733; Generate Palette
    </button>

    <div class="ct-palette-selector" id="ct-palette-slots"
         role="group" aria-label="Selected palette colors">
      <div class="ct-palette-swatch ct-palette-swatch--empty" data-slot="0"
           aria-label="Color slot 1 (empty)">
        <span class="ct-palette-swatch__add" aria-hidden="true">+</span>
        <button class="ct-palette-swatch__remove" aria-label="Remove color"
                style="display:none;">&times;</button>
      </div>
      <div class="ct-palette-swatch ct-palette-swatch--empty" data-slot="1"
           aria-label="Color slot 2 (empty)">
        <span class="ct-palette-swatch__add" aria-hidden="true">+</span>
        <button class="ct-palette-swatch__remove" aria-label="Remove color"
                style="display:none;">&times;</button>
      </div>
      <div class="ct-palette-swatch ct-palette-swatch--empty" data-slot="2"
           aria-label="Color slot 3 (empty)">
        <span class="ct-palette-swatch__add" aria-hidden="true">+</span>
        <button class="ct-palette-swatch__remove" aria-label="Remove color"
                style="display:none;">&times;</button>
      </div>
      <div class="ct-palette-swatch ct-palette-swatch--empty" data-slot="3"
           aria-label="Color slot 4 (empty)">
        <span class="ct-palette-swatch__add" aria-hidden="true">+</span>
        <button class="ct-palette-swatch__remove" aria-label="Remove color"
                style="display:none;">&times;</button>
      </div>
      <div class="ct-palette-swatch ct-palette-swatch--empty" data-slot="4"
           aria-label="Color slot 5 (empty)">
        <span class="ct-palette-swatch__add" aria-hidden="true">+</span>
        <button class="ct-palette-swatch__remove" aria-label="Remove color"
                style="display:none;">&times;</button>
      </div>
    </div>

    <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-randomize-palette">
      &#127922; Randomize
    </button>
  </section>

  <!-- ========== BOARD SIZE ========== -->
  <section class="ct-controls__section" aria-labelledby="ct-board-heading">
    <h2 class="ct-controls__heading" id="ct-board-heading">
      &#11041; Board Size
    </h2>

    <fieldset class="ct-radio-group" id="ct-board-size-group">
      <legend class="sr-only">Select board size</legend>

      <label class="ct-radio-card" for="ct-board-small">
        <input type="radio" class="ct-radio-card__input" id="ct-board-small"
               name="board-size" value="small">
        <div class="ct-radio-card__content">
          <span class="ct-radio-card__title">Small</span>
          <span class="ct-radio-card__subtitle">7 &times; 7 &mdash; 37 hexes</span>
          <span class="ct-radio-card__desc">Quick match, ~10 minutes</span>
        </div>
      </label>

      <label class="ct-radio-card" for="ct-board-medium">
        <input type="radio" class="ct-radio-card__input" id="ct-board-medium"
               name="board-size" value="medium" checked>
        <div class="ct-radio-card__content">
          <span class="ct-radio-card__title">Medium</span>
          <span class="ct-radio-card__subtitle">11 &times; 11 &mdash; 91 hexes</span>
          <span class="ct-radio-card__desc">Standard game, ~25 minutes</span>
        </div>
      </label>

      <label class="ct-radio-card" for="ct-board-large">
        <input type="radio" class="ct-radio-card__input" id="ct-board-large"
               name="board-size" value="large">
        <div class="ct-radio-card__content">
          <span class="ct-radio-card__title">Large</span>
          <span class="ct-radio-card__subtitle">15 &times; 15 &mdash; 169 hexes</span>
          <span class="ct-radio-card__desc">Epic match, ~45 minutes</span>
        </div>
      </label>
    </fieldset>
  </section>

  <!-- ========== AI SETTINGS ========== -->
  <section class="ct-controls__section" aria-labelledby="ct-ai-heading">
    <h2 class="ct-controls__heading" id="ct-ai-heading">
      &#129302; Opponents
    </h2>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-opponent-count">
        Number of AI Opponents:
        <span class="ct-label__value" id="ct-opponent-count-display">2</span>
      </label>
      <input type="range" class="ct-slider" id="ct-opponent-count"
             name="opponent-count" min="1" max="4" value="2" step="1">
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-ai-difficulty">Difficulty</label>
      <select class="ct-select" id="ct-ai-difficulty" name="ai-difficulty">
        <option value="easy">Easy &mdash; Casual exploration</option>
        <option value="medium" selected>Medium &mdash; Balanced challenge</option>
        <option value="hard">Hard &mdash; Strategic opponent</option>
        <option value="expert">Expert &mdash; Ruthless tactician</option>
      </select>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-ai-personality">AI Personality</label>
      <select class="ct-select" id="ct-ai-personality" name="ai-personality">
        <option value="aggressive">Aggressive &mdash; Expansion focus</option>
        <option value="balanced" selected>Balanced &mdash; Adaptive strategy</option>
        <option value="defensive">Defensive &mdash; Fortification focus</option>
        <option value="artistic">Artistic &mdash; Maximizes composition</option>
      </select>
    </div>

    <label class="ct-checkbox-label">
      <input type="checkbox" class="ct-checkbox" id="ct-show-ai-thinking"
             name="show-ai-thinking" checked>
      <span class="ct-checkbox-label__text">Show AI thinking process</span>
    </label>
  </section>

  <!-- ========== START GAME ========== -->
  <section class="ct-controls__section ct-controls__section--start">
    <div class="ct-controls__summary" id="ct-game-summary" aria-live="polite">
      <p class="ct-controls__summary-text">
        <strong>Medium</strong> board &bull;
        <strong>2</strong> AI opponents &bull;
        <strong>Medium</strong> difficulty
      </p>
    </div>

    <button class="ct-btn ct-btn--accent ct-btn--lg ct-controls__start-btn"
            id="ct-start-game">
      &#9654; Start Game
    </button>

    <a href="#/" class="ct-controls__back-link">&larr; Back to Menu</a>
  </section>
</div>

"""


# =================================================================
# 4. GALLERY - Artwork gallery view
# =================================================================

GALLERY_HTML = """\
<!-- Gallery View -->
<div class="ct-gallery" id="ct-gallery">

  <!-- Gallery Header -->
  <div class="ct-gallery__header">
    <div class="ct-gallery__title-row">
      <h2 class="ct-gallery__title">Gallery</h2>
      <span class="ct-badge ct-badge--primary" id="ct-gallery-count">0 artworks</span>
    </div>

    <div class="ct-gallery__controls">
      <div class="ct-gallery__sort" role="group" aria-label="Sort artworks">
        <button class="ct-btn ct-btn--ghost ct-btn--sm ct-gallery__sort-btn
                       ct-gallery__sort-btn--active"
                data-sort="date" aria-pressed="true">Newest</button>
        <button class="ct-btn ct-btn--ghost ct-btn--sm ct-gallery__sort-btn"
                data-sort="score" aria-pressed="false">Top Score</button>
        <button class="ct-btn ct-btn--ghost ct-btn--sm ct-gallery__sort-btn"
                data-sort="size" aria-pressed="false">Largest</button>
      </div>

      <div class="ct-gallery__search">
        <input type="search" class="ct-input ct-input--sm"
               id="ct-gallery-search" placeholder="Search artworks..."
               aria-label="Search artworks">
      </div>
    </div>
  </div>

  <!-- Gallery Grid -->
  <div class="ct-gallery__grid" id="ct-gallery-grid" role="list">
    <!--
      Cards injected dynamically. Template:
      <article class="ct-artwork-card" role="listitem" data-id="{id}">
        <div class="ct-artwork-card__image">
          <img src="{thumbnail}" alt="{title}" loading="lazy">
          <div class="ct-artwork-card__overlay">
            <button class="ct-btn ct-btn--primary ct-btn--sm">View</button>
          </div>
        </div>
        <div class="ct-artwork-card__body">
          <h3 class="ct-artwork-card__title">{title}</h3>
          <time class="ct-artwork-card__date" datetime="{iso}">{relative}</time>
        </div>
        <div class="ct-artwork-card__footer">
          <div class="ct-artwork-card__players">
            <span class="ct-player-dot" style="background:{color}"></span>
          </div>
          <span class="ct-badge ct-badge--accent">{score}</span>
        </div>
      </article>
    -->
  </div>

  <!-- Detail Modal -->
  <div class="ct-gallery-detail" id="ct-gallery-detail" style="display:none;"
       role="dialog" aria-label="Artwork detail view">
    <div class="ct-gallery-detail__backdrop"></div>
    <div class="ct-gallery-detail__content">

      <button class="ct-gallery-detail__nav ct-gallery-detail__nav--prev"
              id="ct-gallery-prev" aria-label="Previous artwork">&lsaquo;</button>
      <button class="ct-gallery-detail__nav ct-gallery-detail__nav--next"
              id="ct-gallery-next" aria-label="Next artwork">&rsaquo;</button>

      <div class="ct-gallery-detail__image">
        <img id="ct-gallery-detail-img" src="" alt="Full-size artwork">
      </div>

      <div class="ct-gallery-detail__meta">
        <h3 class="ct-gallery-detail__title" id="ct-gallery-detail-title"
            contenteditable="true" aria-label="Artwork title (editable)"></h3>

        <table class="ct-gallery-detail__table">
          <tr><th>Date</th><td id="ct-gallery-detail-date"></td></tr>
          <tr><th>Players</th><td id="ct-gallery-detail-players"></td></tr>
          <tr><th>Board Size</th><td id="ct-gallery-detail-board"></td></tr>
          <tr><th>Composition</th><td id="ct-gallery-detail-score"></td></tr>
        </table>

        <div class="ct-gallery-detail__breakdown" aria-label="Color composition">
          <h4>Color Composition</h4>
          <div class="ct-gallery-detail__bars" id="ct-gallery-detail-bars"></div>
        </div>

        <div class="ct-gallery-detail__actions">
          <button class="ct-btn ct-btn--primary ct-btn--sm" id="ct-gallery-download">
            &#11015; Download PNG
          </button>
          <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-gallery-copy">
            &#128203; Copy to Clipboard
          </button>
          <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-gallery-share">
            &#128279; Share
          </button>
        </div>

        <button class="ct-btn ct-btn--ghost ct-btn--sm ct-text-danger"
                id="ct-gallery-delete" aria-label="Delete artwork">
          &#128465; Delete
        </button>
      </div>

      <button class="ct-gallery-detail__close" id="ct-gallery-detail-close"
              aria-label="Close detail view">&times;</button>
    </div>
  </div>

  <!-- Empty State -->
  <div class="ct-gallery__empty" id="ct-gallery-empty" style="display:none;">
    <span class="ct-gallery__empty-icon" aria-hidden="true">&#127912;</span>
    <h3 class="ct-gallery__empty-title">No artworks yet</h3>
    <p class="ct-gallery__empty-desc">
      Play a game to create your first masterpiece.
      Every match becomes a unique work of generative art.
    </p>
    <a href="#/play" class="ct-btn ct-btn--primary">
      &#9654; Start Playing
    </a>
  </div>
</div>

"""


# =================================================================
# 5. TUTORIAL - Interactive tutorial overlay
# =================================================================

TUTORIAL_HTML = """\
<!-- Tutorial Overlay -->
<div class="ct-tutorial" id="ct-tutorial" aria-label="Interactive tutorial"
     role="dialog" aria-modal="true" style="display:none;">

  <!-- Dark Backdrop with Spotlight Cutout -->
  <div class="ct-tutorial__backdrop" id="ct-tutorial-backdrop">
    <div class="ct-tutorial__spotlight" id="ct-tutorial-spotlight"></div>
  </div>

  <!-- Step Content Card -->
  <div class="ct-tutorial__card" id="ct-tutorial-card">

    <div class="ct-tutorial__arrow" id="ct-tutorial-arrow"
         aria-hidden="true"></div>

    <div class="ct-tutorial__card-header">
      <span class="ct-tutorial__step-badge" id="ct-tutorial-step-badge">
        Step 1
      </span>
      <h3 class="ct-tutorial__step-title" id="ct-tutorial-step-title">
        Welcome
      </h3>
    </div>

    <div class="ct-tutorial__card-body">
      <p class="ct-tutorial__step-message" id="ct-tutorial-step-message">
        Welcome to Chromatic Territories!
      </p>
      <div class="ct-tutorial__illustration" id="ct-tutorial-illustration"
           aria-hidden="true"></div>
    </div>

    <div class="ct-tutorial__card-footer">
      <button class="ct-btn ct-btn--ghost ct-btn--sm" id="ct-tutorial-prev"
              aria-label="Previous step" disabled>
        &larr; Previous
      </button>
      <span class="ct-tutorial__counter" id="ct-tutorial-counter"
            aria-live="polite">Step 1 of 15</span>
      <button class="ct-btn ct-btn--primary ct-btn--sm" id="ct-tutorial-next"
              aria-label="Next step">
        Next &rarr;
      </button>
    </div>

    <div class="ct-tutorial__skip-row">
      <button class="ct-btn ct-btn--ghost ct-btn--sm ct-text-muted"
              id="ct-tutorial-skip">Skip tutorial</button>
      <button class="ct-btn ct-btn--ghost ct-btn--sm ct-text-muted"
              id="ct-tutorial-restart" style="display:none;">
        Restart tutorial
      </button>
    </div>
  </div>

  <!-- Progress Dots -->
  <div class="ct-tutorial__progress" id="ct-tutorial-progress"
       role="progressbar" aria-valuemin="1" aria-valuemax="15"
       aria-valuenow="1">
    <span class="ct-tutorial__dot ct-tutorial__dot--current"
          data-step="0"></span>
    <span class="ct-tutorial__dot" data-step="1"></span>
    <span class="ct-tutorial__dot" data-step="2"></span>
    <span class="ct-tutorial__dot" data-step="3"></span>
    <span class="ct-tutorial__dot" data-step="4"></span>
    <span class="ct-tutorial__dot" data-step="5"></span>
    <span class="ct-tutorial__dot" data-step="6"></span>
    <span class="ct-tutorial__dot" data-step="7"></span>
    <span class="ct-tutorial__dot" data-step="8"></span>
    <span class="ct-tutorial__dot" data-step="9"></span>
    <span class="ct-tutorial__dot" data-step="10"></span>
    <span class="ct-tutorial__dot" data-step="11"></span>
    <span class="ct-tutorial__dot" data-step="12"></span>
    <span class="ct-tutorial__dot" data-step="13"></span>
    <span class="ct-tutorial__dot" data-step="14"></span>
  </div>

  <!-- Welcome Step -->
  <div class="ct-tutorial__welcome" id="ct-tutorial-welcome">
    <div class="ct-tutorial__welcome-content">
      <div class="ct-tutorial__welcome-logo" aria-hidden="true">
        <span class="ct-tutorial__welcome-diamond">&#9670;</span>
      </div>
      <h2 class="ct-tutorial__welcome-title">
        Welcome to<br>Chromatic Territories
      </h2>
      <p class="ct-tutorial__welcome-desc">
        A strategy game where every match creates a unique work of
        generative art. Color theory drives combat, composition
        determines territory health, and the game world <em>is</em>
        the artwork.
      </p>
      <div class="ct-tutorial__welcome-actions">
        <button class="ct-btn ct-btn--accent ct-btn--lg"
                id="ct-tutorial-begin">Begin Tutorial</button>
        <button class="ct-btn ct-btn--ghost"
                id="ct-tutorial-skip-welcome">I know how to play</button>
      </div>
    </div>
  </div>
</div>

"""


# =================================================================
# 6. SETTINGS - Settings modal / panel
# =================================================================

SETTINGS_HTML = """\
<!-- Settings Modal -->
<div class="ct-settings" id="ct-settings" role="dialog"
     aria-label="Settings" aria-modal="true">

  <div class="ct-settings__header">
    <h2 class="ct-settings__title">Settings</h2>
    <button class="ct-settings__close" id="ct-settings-close"
            aria-label="Close settings">&times;</button>
  </div>

  <!-- Tab Bar -->
  <div class="ct-tab-bar" role="tablist" aria-label="Settings categories">
    <button class="ct-tab ct-tab--active" role="tab"
            id="ct-settings-tab-audio" aria-selected="true"
            aria-controls="ct-settings-panel-audio">Audio</button>
    <button class="ct-tab" role="tab" id="ct-settings-tab-visual"
            aria-selected="false"
            aria-controls="ct-settings-panel-visual">Visual</button>
    <button class="ct-tab" role="tab" id="ct-settings-tab-game"
            aria-selected="false"
            aria-controls="ct-settings-panel-game">Game</button>
    <button class="ct-tab" role="tab" id="ct-settings-tab-controls"
            aria-selected="false"
            aria-controls="ct-settings-panel-controls">Controls</button>
  </div>

  <!-- Audio Tab -->
  <div class="ct-settings__panel" id="ct-settings-panel-audio"
       role="tabpanel" aria-labelledby="ct-settings-tab-audio">

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-master-volume">
        Master Volume: <span id="ct-master-volume-display">80</span>%
      </label>
      <input type="range" class="ct-slider" id="ct-setting-master-volume"
             name="master-volume" min="0" max="100" value="80">
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Music</span>
        <span class="ct-toggle-label__desc">Generative music reacts to gameplay</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-music" name="music" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Sound Effects</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-sfx" name="sfx" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Ambient Sounds</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-ambient" name="ambient" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-audio-quality">Audio Quality</label>
      <select class="ct-select" id="ct-setting-audio-quality" name="audio-quality">
        <option value="low">Low (22 kHz)</option>
        <option value="standard" selected>Standard (44.1 kHz)</option>
        <option value="high">High (48 kHz)</option>
      </select>
    </div>
  </div>

  <!-- Visual Tab -->
  <div class="ct-settings__panel" id="ct-settings-panel-visual"
       role="tabpanel" aria-labelledby="ct-settings-tab-visual" hidden>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-quality">Quality Preset</label>
      <select class="ct-select" id="ct-setting-quality" name="quality">
        <option value="low">Low &mdash; Best performance</option>
        <option value="medium" selected>Medium &mdash; Balanced</option>
        <option value="high">High &mdash; Full effects</option>
        <option value="ultra">Ultra &mdash; Maximum fidelity</option>
      </select>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Animations</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-animations"
                 name="animations" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Particle Effects</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-particles"
                 name="particles" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Post-processing (bloom, vignette)</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-postfx"
                 name="postfx" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Show Grid Lines</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-gridlines"
                 name="gridlines" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Show Coordinates</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-coords" name="coords">
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-checkbox-label">
        <input type="checkbox" class="ct-checkbox"
               id="ct-setting-colorblind" name="colorblind">
        <span class="ct-checkbox-label__text">Color-blind mode</span>
        <span class="ct-checkbox-label__desc">
          Adds patterns and icons to distinguish territories
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-ui-scale">
        UI Scale: <span id="ct-ui-scale-display">100</span>%
      </label>
      <input type="range" class="ct-slider" id="ct-setting-ui-scale"
             name="ui-scale" min="75" max="150" value="100" step="5">
    </div>
  </div>

  <!-- Game Tab -->
  <div class="ct-settings__panel" id="ct-settings-panel-game"
       role="tabpanel" aria-labelledby="ct-settings-tab-game" hidden>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Auto-save</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-autosave"
                 name="autosave" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Confirm End Turn</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-confirm-turn"
                 name="confirm-turn">
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-toggle-label">
        <span class="ct-toggle-label__text">Show Move Hints</span>
        <span class="ct-toggle">
          <input type="checkbox" id="ct-setting-hints" name="hints" checked>
          <span class="ct-toggle__slider"></span>
        </span>
      </label>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-turn-timer">Turn Timer</label>
      <select class="ct-select" id="ct-setting-turn-timer" name="turn-timer">
        <option value="0" selected>Off</option>
        <option value="30">30 seconds</option>
        <option value="60">60 seconds</option>
        <option value="120">120 seconds</option>
      </select>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-default-board">
        Default Board Size
      </label>
      <select class="ct-select" id="ct-setting-default-board"
              name="default-board">
        <option value="small">Small (7&times;7)</option>
        <option value="medium" selected>Medium (11&times;11)</option>
        <option value="large">Large (15&times;15)</option>
      </select>
    </div>

    <div class="ct-form-group">
      <label class="ct-label" for="ct-setting-default-difficulty">
        Default Difficulty
      </label>
      <select class="ct-select" id="ct-setting-default-difficulty"
              name="default-difficulty">
        <option value="easy">Easy</option>
        <option value="medium" selected>Medium</option>
        <option value="hard">Hard</option>
        <option value="expert">Expert</option>
      </select>
    </div>
  </div>

  <!-- Controls Tab -->
  <div class="ct-settings__panel" id="ct-settings-panel-controls"
       role="tabpanel" aria-labelledby="ct-settings-tab-controls" hidden>

    <h3 class="ct-settings__section-title">Keyboard Shortcuts</h3>

    <table class="ct-shortcuts-table" aria-label="Keyboard shortcuts">
      <thead>
        <tr><th>Key</th><th>Action</th></tr>
      </thead>
      <tbody>
        <tr><td><kbd>Space</kbd></td><td>End turn</td></tr>
        <tr><td><kbd>Escape</kbd></td><td>Cancel / Close</td></tr>
        <tr><td><kbd>1</kbd></td><td>Expand</td></tr>
        <tr><td><kbd>2</kbd></td><td>Fortify</td></tr>
        <tr><td><kbd>3</kbd></td><td>Disrupt</td></tr>
        <tr><td><kbd>4</kbd></td><td>Harmonize</td></tr>
        <tr><td><kbd>5</kbd></td><td>Evolve</td></tr>
        <tr><td><kbd>M</kbd></td><td>Toggle minimap</td></tr>
        <tr><td><kbd>G</kbd></td><td>Open gallery</td></tr>
        <tr><td><kbd>F</kbd></td><td>Toggle fullscreen</td></tr>
        <tr><td><kbd>+</kbd> / <kbd>-</kbd></td><td>Zoom in/out</td></tr>
        <tr>
          <td><kbd>&uarr;</kbd><kbd>&darr;</kbd><kbd>&larr;</kbd><kbd>&rarr;</kbd></td>
          <td>Pan camera</td>
        </tr>
      </tbody>
    </table>

    <button class="ct-btn ct-btn--ghost ct-btn--sm ct-mt-4"
            id="ct-reset-shortcuts">Reset to Defaults</button>
  </div>

  <!-- Footer -->
  <div class="ct-settings__footer">
    <button class="ct-btn ct-btn--primary" id="ct-settings-save">
      Save Settings
    </button>
    <button class="ct-btn ct-btn--ghost" id="ct-settings-cancel">
      Cancel
    </button>
    <button class="ct-btn ct-btn--ghost ct-text-danger"
            id="ct-settings-reset-all">Reset All</button>
  </div>
</div>

"""
