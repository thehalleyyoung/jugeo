"""HTML component content for various concepts."""

HTML_COMPONENTS: dict[str, str] = {
    "game_engine": """<div class="ct-game-container" id="ct-game" role="application" aria-label="Chromatic Territories game" data-state="idle">
  <!-- Canvas stack: layered canvases for rendering -->
  <div class="ct-canvas-container" id="ct-canvas-container" role="img" aria-label="Game board canvas" tabindex="0">
    <canvas class="ct-layer ct-layer-terrain" id="ct-layer-terrain" aria-hidden="true" data-layer="terrain"></canvas>
    <canvas class="ct-layer ct-layer-territory" id="ct-layer-territory" aria-hidden="true" data-layer="territory"></canvas>
    <canvas class="ct-layer ct-layer-effects" id="ct-layer-effects" aria-hidden="true" data-layer="effects"></canvas>
    <canvas class="ct-layer ct-layer-ui" id="ct-layer-ui" aria-hidden="true" data-layer="ui"></canvas>
    <div class="ct-cell-highlight" id="ct-cell-highlight" style="display: none;" aria-hidden="true"></div>
    <div class="ct-selection-ring" id="ct-selection-ring" style="display: none;" aria-hidden="true"></div>
    <div class="ct-hover-tooltip" id="ct-hover-tooltip" style="display: none;" role="tooltip" aria-hidden="true">
      <span class="ct-hover-tooltip__text" id="ct-hover-tooltip-text"></span>
    </div>
  </div>

  <!-- HUD Top: turn indicator, player info, chromaticity, score -->
  <div class="ct-hud-top" id="ct-hud-top" role="status" aria-label="Game status bar">
    <div class="ct-turn-indicator" id="ct-turn-indicator" aria-label="Turn information">
      <span class="ct-turn-indicator__label">Turn</span>
      <span class="ct-turn-indicator__number" id="ct-turn-number" aria-live="polite">1</span>
      <span class="ct-turn-indicator__separator">/</span>
      <span class="ct-turn-indicator__max" id="ct-turn-max">30</span>
      <span class="ct-turn-indicator__phase" id="ct-turn-phase" aria-live="polite">Setup</span>
    </div>
    <div class="ct-player-info" id="ct-player-info" aria-label="Current player">
      <span class="ct-player-info__color" id="ct-player-color" aria-hidden="true"></span>
      <span class="ct-player-info__name" id="ct-player-name" aria-live="polite">Player 1</span>
      <span class="ct-player-info__badge" id="ct-player-badge" data-type="human">Human</span>
      <span class="ct-player-info__territories" id="ct-player-territories" aria-label="Territory count">0 hexes</span>
    </div>
    <div class="ct-chromaticity" id="ct-chromaticity" aria-label="Chromaticity meter">
      <span class="ct-chromaticity__label">Chromaticity</span>
      <div class="ct-chromaticity__bar" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">
        <div class="ct-chromaticity__fill" id="ct-chromaticity-fill" style="width: 0%"></div>
        <div class="ct-chromaticity__glow" id="ct-chromaticity-glow" aria-hidden="true"></div>
      </div>
      <span class="ct-chromaticity__value" id="ct-chromaticity-value">0</span>
    </div>
    <div class="ct-score-display" id="ct-score-display" aria-label="Score display">
      <span class="ct-score-display__label">Score</span>
      <span class="ct-score-display__value" id="ct-score-value" aria-live="polite">0</span>
      <span class="ct-score-display__delta" id="ct-score-delta" style="display: none;" aria-hidden="true"></span>
    </div>
    <div class="ct-timer-display" id="ct-timer-display" aria-label="Turn timer" style="display: none;">
      <span class="ct-timer-display__icon" aria-hidden="true">⏱️</span>
      <span class="ct-timer-display__value" id="ct-timer-value">0:00</span>
    </div>
  </div>

  <!-- Resource bar: below HUD top -->
  <div class="ct-resource-bar" id="ct-resource-bar" role="status" aria-label="Resources">
    <div class="ct-resource" data-resource="actions" aria-label="Actions remaining">
      <span class="ct-resource__icon" aria-hidden="true">⚡</span>
      <span class="ct-resource__label">Actions</span>
      <span class="ct-resource__value" id="ct-resource-actions">3</span>
      <span class="ct-resource__max">/3</span>
    </div>
    <div class="ct-resource" data-resource="palette-tokens" aria-label="Palette tokens">
      <span class="ct-resource__icon" aria-hidden="true">🎨</span>
      <span class="ct-resource__label">Palette</span>
      <span class="ct-resource__value" id="ct-resource-palette">5</span>
    </div>
    <div class="ct-resource" data-resource="harmony-points" aria-label="Harmony points">
      <span class="ct-resource__icon" aria-hidden="true">🎵</span>
      <span class="ct-resource__label">Harmony</span>
      <span class="ct-resource__value" id="ct-resource-harmony">0</span>
    </div>
  </div>

  <!-- HUD Bottom: action bar -->
  <div class="ct-hud-bottom" id="ct-hud-bottom" role="toolbar" aria-label="Game actions">
    <div class="ct-action-bar" id="ct-action-bar">
      <button class="ct-action-btn" data-action="place" data-tooltip="Place Territory" data-cost="1" aria-label="Place Territory (hotkey 1)" aria-keyshortcuts="1">
        <span class="ct-action-btn__hotkey" aria-hidden="true">1</span>
        <span class="ct-action-btn__icon" aria-hidden="true">🎨</span>
        <span class="ct-action-btn__label">Place</span>
        <span class="ct-action-btn__cost" aria-label="Cost: 1 action">1⚡</span>
      </button>
      <button class="ct-action-btn" data-action="expand" data-tooltip="Expand Territory" data-cost="1" aria-label="Expand Territory (hotkey 2)" aria-keyshortcuts="2">
        <span class="ct-action-btn__hotkey" aria-hidden="true">2</span>
        <span class="ct-action-btn__icon" aria-hidden="true">⬡</span>
        <span class="ct-action-btn__label">Expand</span>
        <span class="ct-action-btn__cost" aria-label="Cost: 1 action">1⚡</span>
      </button>
      <button class="ct-action-btn" data-action="fortify" data-tooltip="Fortify Border" data-cost="1" aria-label="Fortify Border (hotkey 3)" aria-keyshortcuts="3">
        <span class="ct-action-btn__hotkey" aria-hidden="true">3</span>
        <span class="ct-action-btn__icon" aria-hidden="true">🛡️</span>
        <span class="ct-action-btn__label">Fortify</span>
        <span class="ct-action-btn__cost" aria-label="Cost: 1 action">1⚡</span>
      </button>
      <button class="ct-action-btn" data-action="blend" data-tooltip="Blend Colors" data-cost="2" aria-label="Blend Colors (hotkey 4)" aria-keyshortcuts="4">
        <span class="ct-action-btn__hotkey" aria-hidden="true">4</span>
        <span class="ct-action-btn__icon" aria-hidden="true">🔀</span>
        <span class="ct-action-btn__label">Blend</span>
        <span class="ct-action-btn__cost" aria-label="Cost: 2 actions">2⚡</span>
      </button>
      <button class="ct-action-btn" data-action="harmonize" data-tooltip="Harmonize Region" data-cost="3" aria-label="Harmonize Region (hotkey 5)" aria-keyshortcuts="5">
        <span class="ct-action-btn__hotkey" aria-hidden="true">5</span>
        <span class="ct-action-btn__icon" aria-hidden="true">✨</span>
        <span class="ct-action-btn__label">Harmonize</span>
        <span class="ct-action-btn__cost" aria-label="Cost: 3 actions">3⚡</span>
      </button>
      <div class="ct-action-bar__divider" role="separator" aria-hidden="true"></div>
      <button class="ct-action-btn ct-action-btn--undo" data-action="undo" data-tooltip="Undo (Ctrl+Z)" aria-label="Undo last action" aria-keyshortcuts="Control+z" disabled>
        <span class="ct-action-btn__icon" aria-hidden="true">↩️</span>
        <span class="ct-action-btn__label">Undo</span>
      </button>
      <button class="ct-action-btn ct-action-btn--end-turn" data-action="endturn" data-tooltip="End Turn (Space)" aria-label="End Turn" aria-keyshortcuts="Space">
        <span class="ct-action-btn__icon" aria-hidden="true">⏭️</span>
        <span class="ct-action-btn__label">End Turn</span>
      </button>
    </div>
  </div>

  <!-- Side panel: territory info -->
  <div class="ct-sidebar-panel ct-territory-info" id="ct-territory-info" style="display: none;" role="complementary" aria-label="Territory information">
    <div class="ct-territory-info__header">
      <h3 class="ct-territory-info__title">Territory Info</h3>
      <button class="btn btn--ghost btn--sm ct-panel-close" data-panel="ct-territory-info" aria-label="Close territory info panel">&times;</button>
    </div>
    <div class="ct-territory-info__body">
      <div class="ct-territory-info__stat" data-field="owner">
        <span class="ct-territory-info__stat-label">Owner</span>
        <span class="ct-territory-info__stat-value" id="ct-info-owner">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="color">
        <span class="ct-territory-info__stat-label">Color</span>
        <span class="ct-territory-info__color" id="ct-info-color" aria-label="Territory color swatch"></span>
        <span class="ct-territory-info__color-name" id="ct-info-color-name">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="strength">
        <span class="ct-territory-info__stat-label">Strength</span>
        <span class="ct-territory-info__stat-value" id="ct-info-strength">—</span>
        <div class="ct-territory-info__strength-bar" aria-hidden="true">
          <div class="ct-territory-info__strength-fill" id="ct-info-strength-bar" style="width: 0%"></div>
        </div>
      </div>
      <div class="ct-territory-info__stat" data-field="terrain">
        <span class="ct-territory-info__stat-label">Terrain</span>
        <span class="ct-territory-info__stat-value" id="ct-info-terrain">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="harmony">
        <span class="ct-territory-info__stat-label">Harmony</span>
        <span class="ct-territory-info__stat-value" id="ct-info-harmony">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="neighbors">
        <span class="ct-territory-info__stat-label">Neighbors</span>
        <span class="ct-territory-info__stat-value" id="ct-info-neighbors">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="coords">
        <span class="ct-territory-info__stat-label">Coordinates</span>
        <span class="ct-territory-info__stat-value" id="ct-info-coords">—</span>
      </div>
      <div class="ct-territory-info__stat" data-field="fortified">
        <span class="ct-territory-info__stat-label">Fortified</span>
        <span class="ct-territory-info__stat-value" id="ct-info-fortified">No</span>
      </div>
    </div>
    <div class="ct-territory-info__actions">
      <button class="btn btn--sm btn--primary" id="ct-info-action-expand" data-action="expand-here" aria-label="Expand into this territory">Expand Here</button>
      <button class="btn btn--sm btn--ghost" id="ct-info-action-fortify" data-action="fortify-here" aria-label="Fortify this territory">Fortify</button>
      <button class="btn btn--sm btn--ghost" id="ct-info-action-blend" data-action="blend-here" aria-label="Blend colors in this territory">Blend</button>
    </div>
  </div>

  <!-- Palette selector panel -->
  <div class="ct-palette-panel" id="ct-palette-panel" style="display: none;" role="complementary" aria-label="Color palette selector">
    <div class="ct-palette-panel__header">
      <h4 class="ct-palette-panel__title">Color Palette</h4>
      <button class="btn btn--ghost btn--sm ct-panel-close" data-panel="ct-palette-panel" aria-label="Close palette panel">&times;</button>
    </div>
    <div class="ct-palette-selector" id="ct-palette-selector" role="listbox" aria-label="Available colors">
      <!-- Swatches populated by JS -->
    </div>
    <div class="ct-palette-panel__current" aria-label="Currently selected color">
      <span class="ct-palette-panel__current-label">Selected:</span>
      <span class="ct-palette-panel__current-swatch" id="ct-palette-current-swatch" aria-hidden="true"></span>
      <span class="ct-palette-panel__current-name" id="ct-palette-current-name">None</span>
      <span class="ct-palette-panel__current-hex" id="ct-palette-current-hex" aria-label="Hex color code"></span>
    </div>
    <div class="ct-harmony-preview" id="ct-harmony-preview" aria-label="Harmony preview">
      <span class="text-xs text-muted">Harmony Preview</span>
      <div class="ct-harmony-preview__colors" id="ct-harmony-colors" role="list" aria-label="Harmonious colors"></div>
      <span class="ct-harmony-preview__type" id="ct-harmony-type" aria-live="polite">Complementary</span>
    </div>
  </div>

  <!-- Minimap -->
  <div class="ct-minimap" id="ct-minimap" aria-label="Minimap overview" data-tooltip="Minimap (M)">
    <canvas id="ct-minimap-canvas" aria-hidden="true"></canvas>
    <div class="ct-minimap__viewport" id="ct-minimap-viewport" aria-hidden="true" role="slider" aria-label="Visible area"></div>
    <button class="btn btn--ghost btn--sm ct-minimap__toggle" id="ct-minimap-toggle" aria-label="Toggle minimap visibility">◿</button>
  </div>

  <!-- Scoreboard overlay (end of game) -->
  <div class="ct-scoreboard-overlay" id="ct-scoreboard-overlay" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-scoreboard-title">
    <div class="ct-scoreboard card">
      <div class="card__header">
        <h2 id="ct-scoreboard-title">🏆 Game Over</h2>
      </div>
      <div class="card__body">
        <div class="ct-scoreboard__winner" id="ct-scoreboard-winner">
          <span class="ct-scoreboard__winner-color" id="ct-scoreboard-winner-color" aria-hidden="true"></span>
          <span class="ct-scoreboard__winner-name" id="ct-scoreboard-winner-name">Player 1</span>
          <span class="ct-scoreboard__winner-label">Winner!</span>
        </div>
        <table class="ct-scoreboard__table" id="ct-scoreboard-table" aria-label="Final scores">
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Player</th>
              <th scope="col">Territories</th>
              <th scope="col">Harmony</th>
              <th scope="col">Chromaticity</th>
              <th scope="col">Total Score</th>
            </tr>
          </thead>
          <tbody id="ct-scoreboard-body">
            <!-- Rows populated by JS -->
          </tbody>
        </table>
      </div>
      <div class="card__footer">
        <button class="btn btn--ghost" id="ct-scoreboard-gallery" aria-label="Save to gallery">📸 Save to Gallery</button>
        <button class="btn btn--ghost" id="ct-scoreboard-replay" aria-label="Watch replay">🔄 Replay</button>
        <button class="btn btn--primary" id="ct-scoreboard-new-game" aria-label="Start new game">New Game</button>
      </div>
    </div>
  </div>

  <!-- New Game Setup Form -->
  <div class="ct-game-setup" id="ct-game-setup" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-setup-title">
    <div class="card">
      <div class="card__header">
        <h2 id="ct-setup-title">New Game</h2>
      </div>
      <div class="card__body">
        <div class="form-group">
          <label class="form-label" for="ct-setup-palette">Color Palette</label>
          <select class="form-select" id="ct-setup-palette" aria-describedby="ct-setup-palette-help">
            <option value="warm">Warm (Reds, Oranges, Yellows)</option>
            <option value="cool">Cool (Blues, Greens, Purples)</option>
            <option value="earth">Earth Tones</option>
            <option value="neon">Neon</option>
            <option value="pastel">Pastel</option>
            <option value="monochrome">Monochrome</option>
            <option value="random">Random</option>
          </select>
          <span class="form-help" id="ct-setup-palette-help">Choose the color scheme for this game</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-setup-size">Board Size</label>
          <select class="form-select" id="ct-setup-size" aria-describedby="ct-setup-size-help">
            <option value="small">Small (12×10)</option>
            <option value="medium" selected>Medium (20×16)</option>
            <option value="large">Large (30×24)</option>
            <option value="huge">Huge (40×32)</option>
          </select>
          <span class="form-help" id="ct-setup-size-help">Larger boards create longer, more strategic games</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-setup-players">Players</label>
          <select class="form-select" id="ct-setup-players" aria-describedby="ct-setup-players-help">
            <option value="2">2 Players</option>
            <option value="3">3 Players</option>
            <option value="4" selected>4 Players</option>
            <option value="6">6 Players</option>
          </select>
          <span class="form-help" id="ct-setup-players-help">Total number of players including yourself</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-setup-ai">AI Difficulty</label>
          <select class="form-select" id="ct-setup-ai" aria-describedby="ct-setup-ai-help">
            <option value="easy">Easy</option>
            <option value="medium" selected>Medium</option>
            <option value="hard">Hard</option>
            <option value="expert">Expert</option>
          </select>
          <span class="form-help" id="ct-setup-ai-help">Difficulty level for AI-controlled players</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-setup-turns">Turn Limit</label>
          <select class="form-select" id="ct-setup-turns" aria-describedby="ct-setup-turns-help">
            <option value="20">20 Turns (Quick)</option>
            <option value="30" selected>30 Turns (Standard)</option>
            <option value="50">50 Turns (Extended)</option>
            <option value="0">Unlimited</option>
          </select>
          <span class="form-help" id="ct-setup-turns-help">Game ends after this many turns</span>
        </div>
        <fieldset class="form-group">
          <legend class="form-label">AI Players</legend>
          <div class="flex gap-2 flex-wrap">
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-ai-1" checked aria-label="Player 2 controlled by AI">
              <span>Player 2 (AI)</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-ai-2" checked aria-label="Player 3 controlled by AI">
              <span>Player 3 (AI)</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-ai-3" checked aria-label="Player 4 controlled by AI">
              <span>Player 4 (AI)</span>
            </label>
          </div>
        </fieldset>
        <fieldset class="form-group">
          <legend class="form-label">Game Options</legend>
          <div class="flex flex-col gap-2">
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-terrain" checked>
              <span>Enable terrain effects</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-fog" checked>
              <span>Fog of war</span>
            </label>
            <label class="form-checkbox">
              <input type="checkbox" id="ct-setup-music" checked>
              <span>Generative music</span>
            </label>
          </div>
        </fieldset>
        <div class="ct-game-setup__preview" id="ct-setup-preview" aria-label="Game setup preview">
          <span class="text-muted">Preview will appear here</span>
        </div>
      </div>
      <div class="card__footer">
        <button class="btn btn--ghost" id="ct-setup-cancel" aria-label="Cancel game setup">Cancel</button>
        <button class="btn btn--primary" id="ct-setup-start" aria-label="Start the game">Start Game</button>
      </div>
    </div>
  </div>

  <!-- Loading overlay -->
  <div class="ct-loading-overlay" id="ct-loading-overlay" style="display: none;" role="alert" aria-live="assertive" aria-label="Loading">
    <div class="ct-loading-overlay__spinner" aria-hidden="true"></div>
    <span class="ct-loading-overlay__text" id="ct-loading-text">Loading...</span>
  </div>

  <!-- Confirmation dialog -->
  <div class="ct-confirm-dialog" id="ct-confirm-dialog" style="display: none;" role="alertdialog" aria-modal="true" aria-labelledby="ct-confirm-title" aria-describedby="ct-confirm-message">
    <div class="card ct-confirm-dialog__card">
      <div class="card__header">
        <h3 id="ct-confirm-title">Confirm Action</h3>
      </div>
      <div class="card__body">
        <p id="ct-confirm-message">Are you sure?</p>
      </div>
      <div class="card__footer">
        <button class="btn btn--ghost" id="ct-confirm-cancel">Cancel</button>
        <button class="btn btn--primary" id="ct-confirm-ok">Confirm</button>
      </div>
    </div>
  </div>
</div>""",

    "gallery": """<div class="ct-page ct-gallery-page" id="ct-gallery-page" role="main" aria-label="Artwork gallery">
  <div class="container">
    <div class="flex flex-between items-center mb-6">
      <div class="ct-gallery-header">
        <h1 class="ct-gallery-header__title">Gallery</h1>
        <span class="ct-gallery-header__count text-muted" id="ct-gallery-count" aria-live="polite">0 artworks</span>
      </div>
      <div class="flex gap-2 items-center flex-wrap">
        <!-- Filter controls -->
        <select class="form-select" id="ct-gallery-filter-palette" aria-label="Filter by palette">
          <option value="">All Palettes</option>
          <option value="warm">Warm</option>
          <option value="cool">Cool</option>
          <option value="earth">Earth Tones</option>
          <option value="neon">Neon</option>
          <option value="pastel">Pastel</option>
          <option value="monochrome">Monochrome</option>
        </select>
        <!-- Sort controls -->
        <select class="form-select" id="ct-gallery-sort" aria-label="Sort gallery">
          <option value="date-desc">Newest First</option>
          <option value="date-asc">Oldest First</option>
          <option value="score-desc">Highest Score</option>
          <option value="score-asc">Lowest Score</option>
          <option value="name-asc">Name A-Z</option>
          <option value="name-desc">Name Z-A</option>
        </select>
        <!-- View mode -->
        <div class="ct-gallery-view-toggle" role="radiogroup" aria-label="View mode">
          <button class="btn btn--ghost btn--icon ct-gallery-view-btn" data-view="grid" aria-label="Grid view" aria-pressed="true" data-tooltip="Grid View">⊞</button>
          <button class="btn btn--ghost btn--icon ct-gallery-view-btn" data-view="list" aria-label="List view" aria-pressed="false" data-tooltip="List View">☰</button>
        </div>
        <!-- Search -->
        <div class="ct-gallery-search" role="search">
          <input type="text" class="form-input" id="ct-gallery-search" placeholder="Search artworks..." aria-label="Search gallery" autocomplete="off">
          <button class="btn btn--ghost btn--icon ct-gallery-search__clear" id="ct-gallery-search-clear" style="display: none;" aria-label="Clear search">&times;</button>
        </div>
        <!-- Capture button -->
        <button class="btn btn--primary" id="ct-gallery-capture" data-tooltip="Capture current canvas" aria-label="Capture current canvas as artwork">
          <span aria-hidden="true">📸</span> Capture
        </button>
      </div>
    </div>

    <!-- Gallery grid -->
    <div class="ct-gallery-grid" id="ct-gallery-grid" role="list" aria-label="Artwork grid">
      <!-- Cards populated by JS; template below for reference -->
    </div>

    <!-- Artwork card template (hidden, cloned by JS) -->
    <template id="ct-gallery-card-template">
      <div class="ct-gallery-card" role="listitem" tabindex="0" data-artwork-id="">
        <div class="ct-gallery-card__image-wrap">
          <img class="ct-gallery-card__image" alt="" loading="lazy" src="">
          <div class="ct-gallery-card__overlay">
            <button class="btn btn--ghost btn--icon ct-gallery-card__action" data-action="view" aria-label="View artwork">🔍</button>
            <button class="btn btn--ghost btn--icon ct-gallery-card__action" data-action="export" aria-label="Export artwork">📥</button>
            <button class="btn btn--ghost btn--icon ct-gallery-card__action" data-action="delete" aria-label="Delete artwork">🗑️</button>
          </div>
        </div>
        <div class="ct-gallery-card__body">
          <span class="ct-gallery-card__title"></span>
          <span class="ct-gallery-card__date text-muted text-xs"></span>
          <div class="ct-gallery-card__meta">
            <span class="ct-gallery-card__score badge badge--primary"></span>
            <span class="ct-gallery-card__palette badge badge--ghost"></span>
          </div>
        </div>
      </div>
    </template>

    <!-- Empty state -->
    <div class="ct-gallery-empty" id="ct-gallery-empty" role="status" aria-label="Gallery empty">
      <div class="ct-gallery-empty__icon" aria-hidden="true">🎨</div>
      <p class="ct-gallery-empty__text">No artworks yet</p>
      <p class="text-muted mt-2">Start a game and capture your creations!</p>
      <button class="btn btn--primary mt-4" id="ct-gallery-start-playing" aria-label="Navigate to game">Start Playing</button>
    </div>

    <!-- No search results state -->
    <div class="ct-gallery-no-results" id="ct-gallery-no-results" style="display: none;" role="status" aria-label="No search results">
      <div class="ct-gallery-no-results__icon" aria-hidden="true">🔍</div>
      <p class="ct-gallery-no-results__text">No artworks match your search</p>
      <button class="btn btn--ghost mt-2" id="ct-gallery-clear-filters" aria-label="Clear all filters">Clear Filters</button>
    </div>

    <!-- Pagination -->
    <nav class="flex flex-center mt-6 gap-2" id="ct-gallery-pagination" role="navigation" aria-label="Gallery pagination">
      <button class="btn btn--ghost btn--sm" id="ct-gallery-prev" disabled aria-label="Previous page">← Previous</button>
      <span class="text-muted" id="ct-gallery-page-info" aria-live="polite" aria-atomic="true">Page 1 of 1</span>
      <button class="btn btn--ghost btn--sm" id="ct-gallery-next" disabled aria-label="Next page">Next →</button>
    </nav>
  </div>

  <!-- Detail modal -->
  <div class="modal-backdrop" id="ct-artwork-modal" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-artwork-title">
    <div class="modal modal--lg">
      <div class="modal__header">
        <h2 id="ct-artwork-title">Artwork</h2>
        <button class="btn btn--ghost btn--icon" id="ct-artwork-close" aria-label="Close artwork detail">&times;</button>
      </div>
      <div class="modal__body">
        <div class="ct-artwork-detail">
          <div class="ct-artwork-detail__image-container">
            <img class="ct-artwork-detail__image" id="ct-artwork-image" alt="Artwork preview" src="">
            <div class="ct-artwork-detail__zoom-controls">
              <button class="btn btn--ghost btn--sm" id="ct-artwork-zoom-in" aria-label="Zoom in">+</button>
              <button class="btn btn--ghost btn--sm" id="ct-artwork-zoom-out" aria-label="Zoom out">−</button>
              <button class="btn btn--ghost btn--sm" id="ct-artwork-zoom-fit" aria-label="Fit to view">⊙</button>
            </div>
          </div>
          <div class="ct-artwork-detail__meta">
            <div class="ct-artwork-detail__name-edit">
              <input type="text" class="form-input" id="ct-artwork-name-input" aria-label="Artwork name" style="display: none;">
              <button class="btn btn--ghost btn--sm" id="ct-artwork-name-edit" aria-label="Edit artwork name">✏️</button>
            </div>
            <div class="ct-territory-info__stat" data-field="created">
              <span>Created</span>
              <span id="ct-artwork-date">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="score">
              <span>Score</span>
              <span id="ct-artwork-score">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="palette">
              <span>Palette</span>
              <span id="ct-artwork-palette">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="board-size">
              <span>Board Size</span>
              <span id="ct-artwork-size">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="players">
              <span>Players</span>
              <span id="ct-artwork-players">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="turns">
              <span>Turns</span>
              <span id="ct-artwork-turns">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="harmony">
              <span>Harmony Score</span>
              <span id="ct-artwork-harmony">—</span>
            </div>
            <div class="ct-territory-info__stat" data-field="chromaticity">
              <span>Chromaticity</span>
              <span id="ct-artwork-chromaticity">—</span>
            </div>
          </div>
          <div class="ct-artwork-detail__color-swatches" id="ct-artwork-swatches" aria-label="Colors used in artwork">
            <!-- Color swatches populated by JS -->
          </div>
          <div class="ct-artwork-detail__actions">
            <button class="btn btn--primary" id="ct-artwork-export" aria-label="Export as PNG">
              <span aria-hidden="true">📥</span> Export PNG
            </button>
            <button class="btn btn--ghost" id="ct-artwork-export-svg" aria-label="Export as SVG">
              <span aria-hidden="true">📐</span> Export SVG
            </button>
            <button class="btn btn--ghost" id="ct-artwork-share" aria-label="Share artwork link">
              <span aria-hidden="true">🔗</span> Share
            </button>
            <button class="btn btn--danger btn--ghost" id="ct-artwork-delete" aria-label="Delete artwork permanently">
              <span aria-hidden="true">🗑️</span> Delete
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Delete confirmation dialog -->
  <div class="modal-backdrop" id="ct-gallery-delete-confirm" style="display: none;" role="alertdialog" aria-modal="true" aria-labelledby="ct-gallery-delete-title" aria-describedby="ct-gallery-delete-message">
    <div class="modal modal--sm">
      <div class="modal__header">
        <h3 id="ct-gallery-delete-title">Delete Artwork</h3>
      </div>
      <div class="modal__body">
        <p id="ct-gallery-delete-message">Are you sure you want to delete this artwork? This action cannot be undone.</p>
      </div>
      <div class="modal__footer">
        <button class="btn btn--ghost" id="ct-gallery-delete-cancel" aria-label="Cancel deletion">Cancel</button>
        <button class="btn btn--danger" id="ct-gallery-delete-ok" aria-label="Confirm deletion">Delete</button>
      </div>
    </div>
  </div>
</div>""",

    "tutorial": """<div class="ct-tutorial-overlay" id="ct-tutorial-overlay" style="display: none;" role="dialog" aria-modal="true" aria-label="Interactive tutorial" data-step="0" data-total-steps="12">
  <!-- Dim backdrop behind the highlighted element -->
  <div class="ct-tutorial-backdrop" id="ct-tutorial-backdrop" aria-hidden="true"></div>

  <!-- Highlight cutout element - positioned over the target element -->
  <div class="ct-tutorial-highlight" id="ct-tutorial-highlight" aria-hidden="true">
    <div class="ct-tutorial-highlight__pulse" aria-hidden="true"></div>
    <div class="ct-tutorial-highlight__ring" aria-hidden="true"></div>
  </div>

  <!-- Pointer arrow from step box to highlighted element -->
  <div class="ct-tutorial-pointer" id="ct-tutorial-pointer" aria-hidden="true">
    <svg class="ct-tutorial-pointer__arrow" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <path d="M12 2 L22 12 L12 22" fill="none" stroke="currentColor" stroke-width="2"/>
    </svg>
  </div>

  <!-- Step container: the main floating instruction card -->
  <div class="ct-tutorial-step" id="ct-tutorial-step" role="alertdialog" aria-labelledby="ct-tutorial-step-title" aria-describedby="ct-tutorial-message" data-position="bottom">
    <div class="ct-tutorial-step__header">
      <span class="badge badge--primary" id="ct-tutorial-step-badge" aria-label="Current tutorial step">Step 1</span>
      <span class="ct-tutorial-step__title" id="ct-tutorial-step-title">Getting Started</span>
      <button class="btn btn--ghost btn--sm" id="ct-tutorial-skip" aria-label="Skip tutorial entirely">Skip</button>
    </div>
    <div class="ct-tutorial-step__body">
      <div class="ct-tutorial-step__message" id="ct-tutorial-message" aria-live="polite">
        Welcome to Chromatic Territories!
      </div>
      <div class="ct-tutorial-step__detail" id="ct-tutorial-detail" style="display: none;">
        <p class="text-sm text-secondary" id="ct-tutorial-detail-text"></p>
      </div>
      <div class="ct-tutorial-step__hint" id="ct-tutorial-hint" style="display: none;" role="note">
        <span class="ct-tutorial-step__hint-icon text-xs text-muted" aria-hidden="true">💡</span>
        <span class="text-xs text-muted" id="ct-tutorial-hint-text"></span>
      </div>
      <div class="ct-tutorial-step__image" id="ct-tutorial-image" style="display: none;" aria-hidden="true">
        <img id="ct-tutorial-image-src" alt="Tutorial illustration" src="">
      </div>
    </div>
    <div class="ct-tutorial-step__actions">
      <button class="btn btn--ghost btn--sm" id="ct-tutorial-prev" disabled aria-label="Go to previous step">← Back</button>
      <div class="ct-progress-dots" id="ct-tutorial-dots" role="tablist" aria-label="Tutorial progress">
        <!-- Dots populated by JS; each dot is a clickable indicator -->
      </div>
      <button class="btn btn--primary btn--sm" id="ct-tutorial-next" aria-label="Go to next step">Next →</button>
    </div>
    <div class="ct-tutorial-step__counter" aria-label="Step progress">
      <span id="ct-tutorial-current">1</span> / <span id="ct-tutorial-total">12</span>
    </div>
    <div class="ct-tutorial-step__progress-bar" aria-hidden="true">
      <div class="ct-tutorial-step__progress-fill" id="ct-tutorial-progress-fill" style="width: 0%"></div>
    </div>
  </div>

  <!-- Welcome screen (first time visitor) -->
  <div class="ct-tutorial-welcome" id="ct-tutorial-welcome" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-tutorial-welcome-title">
    <div class="card ct-tutorial-welcome__card" style="max-width: 500px; margin: auto;">
      <div class="card__body" style="text-align: center;">
        <div class="ct-tutorial-welcome__logo" aria-hidden="true">
          <span style="font-size: 3rem;">🎨</span>
        </div>
        <h2 class="mb-4" id="ct-tutorial-welcome-title">Welcome to Chromatic Territories!</h2>
        <p class="text-secondary mb-2">
          Learn how to play in a quick interactive tutorial. You'll discover how to
          claim territory, blend colors, and create beautiful compositions.
        </p>
        <p class="text-muted text-sm mb-4">
          The tutorial takes about 3 minutes and covers all the basics.
        </p>
        <div class="ct-tutorial-welcome__topics" aria-label="Topics covered">
          <div class="ct-tutorial-welcome__topic">
            <span aria-hidden="true">🎯</span>
            <span class="text-sm">Claiming territory</span>
          </div>
          <div class="ct-tutorial-welcome__topic">
            <span aria-hidden="true">🔀</span>
            <span class="text-sm">Blending colors</span>
          </div>
          <div class="ct-tutorial-welcome__topic">
            <span aria-hidden="true">🛡️</span>
            <span class="text-sm">Fortifying borders</span>
          </div>
          <div class="ct-tutorial-welcome__topic">
            <span aria-hidden="true">✨</span>
            <span class="text-sm">Harmonizing regions</span>
          </div>
          <div class="ct-tutorial-welcome__topic">
            <span aria-hidden="true">🏆</span>
            <span class="text-sm">Scoring points</span>
          </div>
        </div>
        <div class="flex flex-col gap-2 mt-4">
          <button class="btn btn--primary btn--lg" id="ct-tutorial-start-btn" aria-label="Start the interactive tutorial">Start Tutorial</button>
          <button class="btn btn--ghost" id="ct-tutorial-skip-welcome" aria-label="Skip tutorial and start playing">I already know how to play</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Completion screen -->
  <div class="ct-tutorial-complete" id="ct-tutorial-complete" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-tutorial-complete-title">
    <div class="card ct-tutorial-complete__card" style="max-width: 500px; margin: auto;">
      <div class="card__body" style="text-align: center;">
        <div class="ct-tutorial-complete__celebration" aria-hidden="true" style="font-size: 3rem; margin-bottom: 1rem;">🎉</div>
        <h2 class="mb-2" id="ct-tutorial-complete-title">Tutorial Complete!</h2>
        <p class="text-secondary mb-2">
          You're ready to create your own chromatic masterpiece.
        </p>
        <div class="ct-tutorial-complete__summary" aria-label="Tutorial summary">
          <p class="text-sm text-muted mb-4">You learned how to:</p>
          <ul class="ct-tutorial-complete__list">
            <li class="ct-tutorial-complete__list-item">
              <span aria-hidden="true">✅</span> Place and expand territories
            </li>
            <li class="ct-tutorial-complete__list-item">
              <span aria-hidden="true">✅</span> Blend and harmonize colors
            </li>
            <li class="ct-tutorial-complete__list-item">
              <span aria-hidden="true">✅</span> Fortify your borders
            </li>
            <li class="ct-tutorial-complete__list-item">
              <span aria-hidden="true">✅</span> Maximize your composition score
            </li>
          </ul>
        </div>
        <div class="flex flex-col gap-2 mt-4">
          <button class="btn btn--primary btn--lg" id="ct-tutorial-play" aria-label="Start playing a new game">Start Playing</button>
          <button class="btn btn--ghost" id="ct-tutorial-replay" aria-label="Replay the tutorial from the beginning">Replay Tutorial</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Interactive task overlay (for tutorial steps that require user action) -->
  <div class="ct-tutorial-task" id="ct-tutorial-task" style="display: none;" role="status" aria-live="polite">
    <div class="ct-tutorial-task__prompt">
      <span class="ct-tutorial-task__icon" aria-hidden="true">👆</span>
      <span class="ct-tutorial-task__text" id="ct-tutorial-task-text">Click on a hex to place territory</span>
    </div>
    <div class="ct-tutorial-task__progress" aria-label="Task progress">
      <div class="ct-tutorial-task__progress-fill" id="ct-tutorial-task-progress" style="width: 0%"></div>
    </div>
  </div>
</div>""",

    "ui_system": """<!-- Settings Modal -->
<div class="modal-backdrop" id="ct-settings-modal" style="display: none;" role="dialog" aria-modal="true" aria-labelledby="ct-settings-title">
  <div class="modal">
    <div class="modal__header">
      <h2 id="ct-settings-title">⚙️ Settings</h2>
      <button class="btn btn--ghost btn--icon" id="ct-settings-close" aria-label="Close settings">&times;</button>
    </div>
    <div class="modal__body">
      <div class="tabs" role="tablist" aria-label="Settings categories">
        <button class="tab tab-active" data-tab="audio" role="tab" aria-selected="true" aria-controls="ct-settings-audio" id="ct-tab-audio">🔊 Audio</button>
        <button class="tab" data-tab="visual" role="tab" aria-selected="false" aria-controls="ct-settings-visual" id="ct-tab-visual">🎨 Visual</button>
        <button class="tab" data-tab="game" role="tab" aria-selected="false" aria-controls="ct-settings-game" id="ct-tab-game">🎮 Game</button>
        <button class="tab" data-tab="controls" role="tab" aria-selected="false" aria-controls="ct-settings-controls" id="ct-tab-controls">⌨️ Controls</button>
      </div>

      <!-- Audio Settings -->
      <div class="ct-settings-section" id="ct-settings-audio" data-section="audio" role="tabpanel" aria-labelledby="ct-tab-audio">
        <div class="form-group">
          <label class="form-label" for="ct-vol-master">Master Volume</label>
          <input type="range" class="slider" id="ct-vol-master" min="0" max="100" value="70" aria-valuemin="0" aria-valuemax="100" aria-valuenow="70">
          <span class="form-help" id="ct-vol-master-val" aria-live="polite">70%</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-vol-music">Music Volume</label>
          <input type="range" class="slider" id="ct-vol-music" min="0" max="100" value="50" aria-valuemin="0" aria-valuemax="100" aria-valuenow="50">
          <span class="form-help" id="ct-vol-music-val" aria-live="polite">50%</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-vol-sfx">SFX Volume</label>
          <input type="range" class="slider" id="ct-vol-sfx" min="0" max="100" value="60" aria-valuemin="0" aria-valuemax="100" aria-valuenow="60">
          <span class="form-help" id="ct-vol-sfx-val" aria-live="polite">60%</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-music">
            Generative Music
            <span class="toggle-switch active" id="ct-toggle-music" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle generative music"></span>
          </label>
          <span class="form-help">Enable dynamic music that responds to gameplay</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-sfx">
            Sound Effects
            <span class="toggle-switch active" id="ct-toggle-sfx" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle sound effects"></span>
          </label>
          <span class="form-help">Play sounds for actions, captures, and notifications</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-ambient">
            Ambient Sounds
            <span class="toggle-switch active" id="ct-toggle-ambient" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle ambient sounds"></span>
          </label>
          <span class="form-help">Background atmospheric sounds based on board state</span>
        </div>
      </div>

      <!-- Visual Settings -->
      <div class="ct-settings-section" id="ct-settings-visual" data-section="visual" style="display: none;" role="tabpanel" aria-labelledby="ct-tab-visual">
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-animations">
            Animations
            <span class="toggle-switch active" id="ct-toggle-animations" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle animations"></span>
          </label>
          <span class="form-help">Smooth transitions for territory changes and effects</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-particles">
            Particle Effects
            <span class="toggle-switch active" id="ct-toggle-particles" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle particle effects"></span>
          </label>
          <span class="form-help">Visual particles for harmonize, blend, and capture events</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-grid-opacity">Grid Opacity</label>
          <input type="range" class="slider" id="ct-grid-opacity" min="0" max="100" value="30" aria-valuemin="0" aria-valuemax="100" aria-valuenow="30">
          <span class="form-help" id="ct-grid-opacity-val" aria-live="polite">30%</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-minimap">
            Show Minimap
            <span class="toggle-switch active" id="ct-toggle-minimap" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle minimap visibility"></span>
          </label>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-zoom-sensitivity">Zoom Sensitivity</label>
          <input type="range" class="slider" id="ct-zoom-sensitivity" min="1" max="10" value="5" aria-valuemin="1" aria-valuemax="10" aria-valuenow="5">
          <span class="form-help" id="ct-zoom-sensitivity-val" aria-live="polite">5</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-hidpi">
            High DPI Rendering
            <span class="toggle-switch active" id="ct-toggle-hidpi" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle high DPI rendering"></span>
          </label>
          <span class="form-help">Uses more GPU power for sharper rendering on retina displays</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-borders">
            Territory Borders
            <span class="toggle-switch active" id="ct-toggle-borders" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle territory borders"></span>
          </label>
          <span class="form-help">Show colored borders around owned territories</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-labels">
            Hex Labels
            <span class="toggle-switch" id="ct-toggle-labels" role="switch" aria-checked="false" tabindex="0" aria-label="Toggle hex coordinate labels"></span>
          </label>
          <span class="form-help">Display coordinate labels on each hex cell</span>
        </div>
      </div>

      <!-- Game Settings -->
      <div class="ct-settings-section" id="ct-settings-game" data-section="game" style="display: none;" role="tabpanel" aria-labelledby="ct-tab-game">
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-auto-end">
            Auto-End Turn
            <span class="toggle-switch" id="ct-toggle-auto-end" role="switch" aria-checked="false" tabindex="0" aria-label="Toggle auto-end turn"></span>
          </label>
          <span class="form-help">Automatically end your turn when all actions are spent</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-confirm">
            Confirm Actions
            <span class="toggle-switch active" id="ct-toggle-confirm" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle action confirmation"></span>
          </label>
          <span class="form-help">Show confirmation dialog before irreversible actions</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-coords">
            Show Coordinates
            <span class="toggle-switch" id="ct-toggle-coords" role="switch" aria-checked="false" tabindex="0" aria-label="Toggle coordinate display"></span>
          </label>
          <span class="form-help">Display hex coordinates on the game board</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-ai-think-time">AI Think Time</label>
          <input type="range" class="slider" id="ct-ai-think-time" min="0" max="5000" step="250" value="1000" aria-valuemin="0" aria-valuemax="5000" aria-valuenow="1000">
          <span class="form-help" id="ct-ai-think-time-val" aria-live="polite">1.0s</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="ct-undo-limit">Undo Limit</label>
          <select class="form-select" id="ct-undo-limit" aria-label="Maximum number of undo steps">
            <option value="0">Disabled</option>
            <option value="1">1 Step</option>
            <option value="3" selected>3 Steps</option>
            <option value="5">5 Steps</option>
            <option value="10">10 Steps</option>
            <option value="-1">Unlimited</option>
          </select>
          <span class="form-help">Maximum number of actions you can undo per turn</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-autosave">
            Autosave
            <span class="toggle-switch active" id="ct-toggle-autosave" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle autosave"></span>
          </label>
          <span class="form-help">Automatically save game state each turn</span>
        </div>
        <div class="form-group">
          <label class="form-label flex flex-between items-center" for="ct-toggle-notifications">
            Notifications
            <span class="toggle-switch active" id="ct-toggle-notifications" role="switch" aria-checked="true" tabindex="0" aria-label="Toggle in-game notifications"></span>
          </label>
          <span class="form-help">Show toast notifications for game events</span>
        </div>
      </div>

      <!-- Controls / Keyboard Shortcuts -->
      <div class="ct-settings-section" id="ct-settings-controls" data-section="controls" style="display: none;" role="tabpanel" aria-labelledby="ct-tab-controls">
        <table class="ct-shortcuts-table" aria-label="Keyboard shortcuts reference">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Shortcut</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Place Territory</td>
              <td><kbd>1</kbd></td>
            </tr>
            <tr>
              <td>Expand Territory</td>
              <td><kbd>2</kbd></td>
            </tr>
            <tr>
              <td>Fortify Border</td>
              <td><kbd>3</kbd></td>
            </tr>
            <tr>
              <td>Blend Colors</td>
              <td><kbd>4</kbd></td>
            </tr>
            <tr>
              <td>Harmonize Region</td>
              <td><kbd>5</kbd></td>
            </tr>
            <tr>
              <td>End Turn</td>
              <td><kbd>Space</kbd></td>
            </tr>
            <tr>
              <td>Undo</td>
              <td><kbd>Ctrl</kbd> + <kbd>Z</kbd></td>
            </tr>
            <tr>
              <td>Zoom In</td>
              <td><kbd>+</kbd> / <kbd>=</kbd></td>
            </tr>
            <tr>
              <td>Zoom Out</td>
              <td><kbd>-</kbd></td>
            </tr>
            <tr>
              <td>Reset View</td>
              <td><kbd>0</kbd></td>
            </tr>
            <tr>
              <td>Toggle Grid</td>
              <td><kbd>G</kbd></td>
            </tr>
            <tr>
              <td>Toggle Minimap</td>
              <td><kbd>M</kbd></td>
            </tr>
            <tr>
              <td>Open Settings</td>
              <td><kbd>Esc</kbd></td>
            </tr>
            <tr>
              <td>Toggle Palette</td>
              <td><kbd>P</kbd></td>
            </tr>
            <tr>
              <td>Quick Save</td>
              <td><kbd>Ctrl</kbd> + <kbd>S</kbd></td>
            </tr>
            <tr>
              <td>Capture to Gallery</td>
              <td><kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd></td>
            </tr>
            <tr>
              <td>Pan View</td>
              <td><kbd>Arrow Keys</kbd> / Middle Mouse Drag</td>
            </tr>
          </tbody>
        </table>
        <p class="text-muted text-sm mt-4">Keyboard shortcuts can be customized in a future update.</p>
      </div>
    </div>
    <div class="modal__footer">
      <button class="btn btn--ghost" id="ct-settings-reset" aria-label="Reset all settings to default values">Reset to Defaults</button>
      <button class="btn btn--primary" id="ct-settings-save" aria-label="Save settings">Save</button>
    </div>
  </div>
</div>

<!-- About page content -->
<div class="ct-page ct-about-page" id="ct-about-page" role="main" aria-label="About Chromatic Territories">
  <div class="container">
    <div class="ct-about-hero" style="text-align: center; padding: 3rem 0;">
      <div class="ct-about-hero__logo" aria-hidden="true" style="font-size: 4rem; margin-bottom: 1rem;">🎨</div>
      <h1 class="mb-2">Chromatic Territories</h1>
      <p class="text-secondary text-lg mb-2">A generative art strategy game</p>
      <p class="text-muted text-sm mb-6">Where color theory meets territorial conquest</p>
    </div>

    <div class="grid grid-3 gap-4 mb-8" role="list" aria-label="Game features">
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">🎨</div>
          <h3 class="mb-2">Color Theory</h3>
          <p class="text-muted text-sm">Explore harmonies, complementary colors, and chromatic relationships to dominate the board</p>
        </div>
      </div>
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">⬡</div>
          <h3 class="mb-2">Territory Control</h3>
          <p class="text-muted text-sm">Claim hexes, expand borders, and fortify your domain on a dynamic hex grid</p>
        </div>
      </div>
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">🎵</div>
          <h3 class="mb-2">Generative Music</h3>
          <p class="text-muted text-sm">Dynamic soundscapes that respond to your artistic choices and board state</p>
        </div>
      </div>
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">🖼️</div>
          <h3 class="mb-2">Gallery</h3>
          <p class="text-muted text-sm">Capture, save, and export your generative artworks as high-resolution images</p>
        </div>
      </div>
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">🤖</div>
          <h3 class="mb-2">AI Opponents</h3>
          <p class="text-muted text-sm">Challenge intelligent AI players with unique strategies and difficulty levels</p>
        </div>
      </div>
      <div class="card hover-lift" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div style="font-size: 2rem; margin-bottom: 0.5rem;" aria-hidden="true">📊</div>
          <h3 class="mb-2">Composition Score</h3>
          <p class="text-muted text-sm">Earn points for creating beautiful color compositions and harmonic arrangements</p>
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card__header">
        <h3>Keyboard Shortcuts</h3>
      </div>
      <div class="card__body">
        <table class="ct-shortcuts-table" aria-label="Quick reference keyboard shortcuts">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Shortcut</th>
              <th scope="col">Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Actions 1–5</td>
              <td><kbd>1</kbd>–<kbd>5</kbd></td>
              <td>Select the corresponding action from the toolbar</td>
            </tr>
            <tr>
              <td>End Turn</td>
              <td><kbd>Space</kbd></td>
              <td>Finish your turn and pass to the next player</td>
            </tr>
            <tr>
              <td>Undo</td>
              <td><kbd>Ctrl+Z</kbd></td>
              <td>Undo your last action (up to the undo limit)</td>
            </tr>
            <tr>
              <td>Zoom</td>
              <td><kbd>+</kbd> / <kbd>-</kbd></td>
              <td>Zoom in or out on the board</td>
            </tr>
            <tr>
              <td>Toggle Grid</td>
              <td><kbd>G</kbd></td>
              <td>Show or hide the hex grid overlay</td>
            </tr>
            <tr>
              <td>Minimap</td>
              <td><kbd>M</kbd></td>
              <td>Show or hide the minimap</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card__header">
        <h3>How to Play</h3>
      </div>
      <div class="card__body">
        <ol class="ct-about-steps" aria-label="Game instructions">
          <li class="ct-about-steps__step">
            <strong>Setup:</strong> Choose your color palette and board size, then start a new game.
          </li>
          <li class="ct-about-steps__step">
            <strong>Place:</strong> On your turn, place territory on unclaimed hexes using your palette colors.
          </li>
          <li class="ct-about-steps__step">
            <strong>Expand:</strong> Grow your territory by expanding into adjacent hexes.
          </li>
          <li class="ct-about-steps__step">
            <strong>Blend:</strong> Combine colors on adjacent territories to create new hues and increase chromaticity.
          </li>
          <li class="ct-about-steps__step">
            <strong>Harmonize:</strong> Create color harmonies across regions for bonus points.
          </li>
          <li class="ct-about-steps__step">
            <strong>Score:</strong> Points are awarded for territory size, color harmony, and chromaticity at game end.
          </li>
        </ol>
      </div>
    </div>

    <div class="ct-about-footer" style="text-align: center; padding: 2rem 0;">
      <p class="text-muted text-sm">Version 1.0.0 · Built with ❤️ and JG theory</p>
      <p class="text-muted text-xs mt-1">Chromatic Territories — a Judgment Geometry project</p>
    </div>
  </div>
</div>

<!-- Notification container -->
<div class="ct-notification-container" id="ct-notification-container" aria-live="polite" aria-relevant="additions" role="log">
  <!-- Notification template (hidden, cloned by JS) -->
  <template id="ct-notification-template">
    <div class="ct-notification" role="alert" aria-atomic="true" data-type="info" data-duration="4000">
      <span class="ct-notification__icon" aria-hidden="true"></span>
      <div class="ct-notification__content">
        <span class="ct-notification__title"></span>
        <span class="ct-notification__message"></span>
      </div>
      <button class="ct-notification__close btn btn--ghost btn--sm" aria-label="Dismiss notification">&times;</button>
      <div class="ct-notification__timer" aria-hidden="true">
        <div class="ct-notification__timer-fill"></div>
      </div>
    </div>
  </template>
</div>

<!-- Welcome / Landing page -->
<div class="ct-page ct-welcome-page" id="ct-welcome-page" role="main" aria-label="Welcome to Chromatic Territories">
  <div class="container">
    <!-- Hero section -->
    <div class="ct-welcome-hero" style="text-align: center; padding: 4rem 0 2rem;">
      <div class="ct-welcome-hero__logo" aria-hidden="true" style="font-size: 5rem; margin-bottom: 1rem;">🎨</div>
      <h1 class="ct-welcome-hero__title mb-2" style="font-size: 2.5rem;">Chromatic Territories</h1>
      <p class="ct-welcome-hero__subtitle text-secondary text-lg mb-6">Where color theory becomes strategy</p>
      <div class="ct-welcome-hero__actions flex flex-center gap-4">
        <button class="btn btn--primary btn--lg" id="ct-welcome-new-game" aria-label="Start a new game">
          <span aria-hidden="true">🎮</span> New Game
        </button>
        <button class="btn btn--ghost btn--lg" id="ct-welcome-continue" style="display: none;" aria-label="Continue saved game">
          <span aria-hidden="true">▶️</span> Continue
        </button>
        <button class="btn btn--ghost btn--lg" id="ct-welcome-tutorial" aria-label="Start the tutorial">
          <span aria-hidden="true">📖</span> Tutorial
        </button>
      </div>
    </div>

    <!-- Feature highlights -->
    <div class="ct-welcome-features grid grid-3 gap-4 mb-8" role="list" aria-label="Key features">
      <div class="card hover-lift ct-welcome-feature" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div class="ct-welcome-feature__icon" aria-hidden="true" style="font-size: 2.5rem; margin-bottom: 0.75rem;">🎨</div>
          <h3 class="ct-welcome-feature__title mb-2">Create Art</h3>
          <p class="ct-welcome-feature__desc text-muted text-sm">Every game generates a unique piece of art through your strategic choices</p>
        </div>
      </div>
      <div class="card hover-lift ct-welcome-feature" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div class="ct-welcome-feature__icon" aria-hidden="true" style="font-size: 2.5rem; margin-bottom: 0.75rem;">⬡</div>
          <h3 class="ct-welcome-feature__title mb-2">Conquer Territory</h3>
          <p class="ct-welcome-feature__desc text-muted text-sm">Claim hexes, expand your borders, and outmaneuver your opponents</p>
        </div>
      </div>
      <div class="card hover-lift ct-welcome-feature" role="listitem">
        <div class="card__body" style="text-align: center;">
          <div class="ct-welcome-feature__icon" aria-hidden="true" style="font-size: 2.5rem; margin-bottom: 0.75rem;">🎵</div>
          <h3 class="ct-welcome-feature__title mb-2">Listen</h3>
          <p class="ct-welcome-feature__desc text-muted text-sm">Generative music evolves with your gameplay for a unique audio experience</p>
        </div>
      </div>
    </div>

    <!-- Quick stats / last game -->
    <div class="ct-welcome-stats" id="ct-welcome-stats" style="display: none;" aria-label="Your statistics">
      <div class="card">
        <div class="card__header">
          <h3>Your Stats</h3>
        </div>
        <div class="card__body">
          <div class="grid grid-4 gap-4">
            <div class="ct-welcome-stat" style="text-align: center;">
              <span class="ct-welcome-stat__value text-lg" id="ct-welcome-stat-games">0</span>
              <span class="ct-welcome-stat__label text-muted text-sm">Games Played</span>
            </div>
            <div class="ct-welcome-stat" style="text-align: center;">
              <span class="ct-welcome-stat__value text-lg" id="ct-welcome-stat-wins">0</span>
              <span class="ct-welcome-stat__label text-muted text-sm">Victories</span>
            </div>
            <div class="ct-welcome-stat" style="text-align: center;">
              <span class="ct-welcome-stat__value text-lg" id="ct-welcome-stat-score">0</span>
              <span class="ct-welcome-stat__label text-muted text-sm">Best Score</span>
            </div>
            <div class="ct-welcome-stat" style="text-align: center;">
              <span class="ct-welcome-stat__value text-lg" id="ct-welcome-stat-artworks">0</span>
              <span class="ct-welcome-stat__label text-muted text-sm">Artworks</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div class="ct-welcome-footer" style="text-align: center; padding: 2rem 0;">
      <nav class="ct-welcome-footer__links flex flex-center gap-4" aria-label="Footer navigation">
        <a href="#/about" class="ct-welcome-footer__link text-muted text-sm" aria-label="About the game">About</a>
        <a href="#/gallery" class="ct-welcome-footer__link text-muted text-sm" aria-label="View gallery">Gallery</a>
        <a href="#/settings" class="ct-welcome-footer__link text-muted text-sm" aria-label="Open settings">Settings</a>
      </nav>
      <p class="text-muted text-xs mt-2">v1.0.0 · A Judgment Geometry project</p>
    </div>
  </div>
</div>""",

    "canvas_renderer": """<div class="ct-canvas-stack" id="ct-canvas-stack" role="application" aria-label="Canvas rendering stack" data-renderer="2d">
  <!-- Main canvas container: holds layered canvases for composited rendering -->
  <div class="ct-canvas-container" id="ct-canvas-main" role="img" aria-label="Game board" tabindex="0" data-zoom="1.0" data-pan-x="0" data-pan-y="0">
    <canvas class="ct-layer ct-layer-terrain" id="ct-layer-terrain" aria-hidden="true" data-layer="terrain" data-priority="0"></canvas>
    <canvas class="ct-layer ct-layer-territory" id="ct-layer-territory" aria-hidden="true" data-layer="territory" data-priority="1"></canvas>
    <canvas class="ct-layer ct-layer-grid" id="ct-layer-grid" aria-hidden="true" data-layer="grid" data-priority="2"></canvas>
    <canvas class="ct-layer ct-layer-effects" id="ct-layer-effects" aria-hidden="true" data-layer="effects" data-priority="3"></canvas>
    <canvas class="ct-layer ct-layer-ui" id="ct-layer-ui" aria-hidden="true" data-layer="ui" data-priority="4"></canvas>
    <!-- Overlay elements positioned above canvases -->
    <div class="ct-cell-highlight" id="ct-cell-highlight" style="display: none;" aria-hidden="true" data-hex-q="" data-hex-r=""></div>
    <div class="ct-selection-indicator" id="ct-selection-indicator" style="display: none;" aria-hidden="true">
      <div class="ct-selection-indicator__ring" aria-hidden="true"></div>
      <div class="ct-selection-indicator__pulse" aria-hidden="true"></div>
    </div>
    <div class="ct-drag-indicator" id="ct-drag-indicator" style="display: none;" aria-hidden="true"></div>
    <!-- Screen reader announcement for board changes -->
    <div class="sr-only" id="ct-canvas-announce" role="status" aria-live="polite" aria-atomic="true"></div>
  </div>

  <!-- Canvas zoom and view controls -->
  <div class="ct-canvas-controls" id="ct-canvas-controls" role="toolbar" aria-label="Canvas view controls">
    <button class="btn btn--ghost btn--icon" id="ct-zoom-in" data-tooltip="Zoom In (+)" aria-label="Zoom in" aria-keyshortcuts="+">+</button>
    <span class="ct-canvas-controls__zoom-level" id="ct-canvas-zoom-level" aria-live="polite">100%</span>
    <button class="btn btn--ghost btn--icon" id="ct-zoom-out" data-tooltip="Zoom Out (−)" aria-label="Zoom out" aria-keyshortcuts="-">−</button>
    <div class="ct-canvas-controls__divider" role="separator" aria-hidden="true"></div>
    <button class="btn btn--ghost btn--icon" id="ct-zoom-reset" data-tooltip="Reset View (0)" aria-label="Reset zoom and pan" aria-keyshortcuts="0">⊙</button>
    <button class="btn btn--ghost btn--icon" id="ct-toggle-grid" data-tooltip="Toggle Grid (G)" aria-label="Toggle hex grid overlay" aria-keyshortcuts="g" aria-pressed="true">⊞</button>
    <button class="btn btn--ghost btn--icon" id="ct-toggle-coords-btn" data-tooltip="Toggle Coordinates" aria-label="Toggle coordinate display" aria-pressed="false">#</button>
    <button class="btn btn--ghost btn--icon" id="ct-center-view" data-tooltip="Center View" aria-label="Center view on board">⊕</button>
  </div>

  <!-- Minimap for overview navigation -->
  <div class="ct-minimap" id="ct-minimap-container" data-tooltip="Minimap (M)" role="navigation" aria-label="Board minimap">
    <canvas id="ct-minimap-canvas" aria-hidden="true" data-minimap="true"></canvas>
    <div class="ct-minimap__viewport" id="ct-minimap-viewport" aria-hidden="true" role="slider" aria-label="Visible area indicator" aria-valuemin="0" aria-valuemax="100"></div>
    <button class="btn btn--ghost btn--sm ct-minimap__toggle" id="ct-minimap-toggle" aria-label="Toggle minimap visibility" aria-keyshortcuts="m" aria-expanded="true">◿</button>
    <div class="ct-minimap__legend" id="ct-minimap-legend" style="display: none;" aria-label="Minimap player colors">
      <!-- Legend items populated by JS -->
    </div>
  </div>

  <!-- Canvas info bar: coordinates, zoom, FPS -->
  <div class="ct-canvas-info" id="ct-canvas-info" role="status" aria-label="Canvas information" aria-live="polite">
    <span class="ct-canvas-info__item text-xs text-muted" id="ct-canvas-coords" aria-label="Cursor coordinates" data-field="coords">0, 0</span>
    <span class="ct-canvas-info__separator text-xs text-muted" aria-hidden="true">|</span>
    <span class="ct-canvas-info__item text-xs text-muted" id="ct-canvas-zoom" aria-label="Current zoom level" data-field="zoom">100%</span>
    <span class="ct-canvas-info__separator text-xs text-muted" aria-hidden="true">|</span>
    <span class="ct-canvas-info__item text-xs text-muted" id="ct-canvas-dimensions" aria-label="Board dimensions" data-field="dimensions">20×16</span>
    <span class="ct-canvas-info__separator text-xs text-muted" aria-hidden="true" id="ct-fps-separator" style="display: none;">|</span>
    <span class="ct-canvas-info__item text-xs text-muted" id="ct-canvas-fps" aria-label="Frames per second" data-field="fps" style="display: none;">60 FPS</span>
  </div>

  <!-- Performance warning overlay -->
  <div class="ct-canvas-perf-warning" id="ct-canvas-perf-warning" style="display: none;" role="alert" aria-live="assertive">
    <span class="ct-canvas-perf-warning__icon" aria-hidden="true">⚠️</span>
    <span class="ct-canvas-perf-warning__text">Low framerate detected. Consider reducing visual effects in Settings.</span>
    <button class="btn btn--ghost btn--sm" id="ct-canvas-perf-dismiss" aria-label="Dismiss performance warning">Dismiss</button>
  </div>

  <!-- Render stats overlay (debug mode) -->
  <div class="ct-canvas-debug" id="ct-canvas-debug" style="display: none;" aria-hidden="true" data-debug="true">
    <div class="ct-canvas-debug__stat">
      <span class="ct-canvas-debug__label">Draw calls:</span>
      <span class="ct-canvas-debug__value" id="ct-debug-draw-calls">0</span>
    </div>
    <div class="ct-canvas-debug__stat">
      <span class="ct-canvas-debug__label">Visible hexes:</span>
      <span class="ct-canvas-debug__value" id="ct-debug-visible-hexes">0</span>
    </div>
    <div class="ct-canvas-debug__stat">
      <span class="ct-canvas-debug__label">Particles:</span>
      <span class="ct-canvas-debug__value" id="ct-debug-particles">0</span>
    </div>
    <div class="ct-canvas-debug__stat">
      <span class="ct-canvas-debug__label">Frame time:</span>
      <span class="ct-canvas-debug__value" id="ct-debug-frame-time">0ms</span>
    </div>
  </div>
</div>""",
}


def get_html_for_concept(name: str) -> str:
    """Return the HTML component for a given concept name, or empty string."""
    return HTML_COMPONENTS.get(name, "")
