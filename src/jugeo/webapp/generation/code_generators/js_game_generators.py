"""Code generators for game-related JavaScript modules.

Each generator produces a complete, working JS class (or set of classes)
registered on ``window.CT``.  Game logic references art modules through
``window.CT.ColorTheory`` and ``window.CT.CompositionAnalyzer``.
"""
from __future__ import annotations

from . import register


# ---------------------------------------------------------------------------
# 1. Territory  — hex-grid territory system
# ---------------------------------------------------------------------------

@register("territory")
def generate_territory(**kwargs) -> tuple[str, str, str]:
    js = """\
// ===================================================================
// TerritorySystem  –  hex-grid territory management
// ===================================================================
(function () {
  "use strict";

  var CT = window.CT = window.CT || {};

  // -----------------------------------------------------------------
  // HexCell
  // -----------------------------------------------------------------
  class HexCell {
    constructor(q, r) {
      this.q = q;
      this.r = r;
      this.owner = null;          // player index or null
      this.color = null;          // {h, s, l}
      this.compositionScore = 0;
      this.borderStrength = 0;
      this.claimedTurn = -1;
      this.fortified = false;
      this.visionBlocked = false;
    }

    get s() {
      return -this.q - this.r;
    }

    get key() {
      return this.q + "," + this.r;
    }

    clone() {
      var c = new HexCell(this.q, this.r);
      c.owner = this.owner;
      c.color = this.color ? { h: this.color.h, s: this.color.s, l: this.color.l } : null;
      c.compositionScore = this.compositionScore;
      c.borderStrength = this.borderStrength;
      c.claimedTurn = this.claimedTurn;
      c.fortified = this.fortified;
      c.visionBlocked = this.visionBlocked;
      return c;
    }

    toJSON() {
      return {
        q: this.q,
        r: this.r,
        owner: this.owner,
        color: this.color,
        compositionScore: this.compositionScore,
        borderStrength: this.borderStrength,
        claimedTurn: this.claimedTurn,
        fortified: this.fortified,
        visionBlocked: this.visionBlocked
      };
    }

    static fromJSON(data) {
      var c = new HexCell(data.q, data.r);
      c.owner = data.owner;
      c.color = data.color;
      c.compositionScore = data.compositionScore || 0;
      c.borderStrength = data.borderStrength || 0;
      c.claimedTurn = data.claimedTurn != null ? data.claimedTurn : -1;
      c.fortified = !!data.fortified;
      c.visionBlocked = !!data.visionBlocked;
      return c;
    }
  }

  // -----------------------------------------------------------------
  // Axial direction vectors (flat-top hex)
  // -----------------------------------------------------------------
  var DIRECTIONS = [
    { q: 1, r: 0 },
    { q: 1, r: -1 },
    { q: 0, r: -1 },
    { q: -1, r: 0 },
    { q: -1, r: 1 },
    { q: 0, r: 1 }
  ];

  // -----------------------------------------------------------------
  // HexGrid
  // -----------------------------------------------------------------
  class HexGrid {
    constructor(radius) {
      this.radius = radius;
      this._cells = new Map();
      this._initGrid();
    }

    _initGrid() {
      for (var q = -this.radius; q <= this.radius; q++) {
        var r1 = Math.max(-this.radius, -q - this.radius);
        var r2 = Math.min(this.radius, -q + this.radius);
        for (var r = r1; r <= r2; r++) {
          var cell = new HexCell(q, r);
          this._cells.set(cell.key, cell);
        }
      }
    }

    get(q, r) {
      return this._cells.get(q + "," + r) || null;
    }

    set(q, r, cell) {
      var key = q + "," + r;
      if (this._cells.has(key)) {
        this._cells.set(key, cell);
      }
    }

    has(q, r) {
      return this._cells.has(q + "," + r);
    }

    get size() {
      return this._cells.size;
    }

    forEach(fn) {
      this._cells.forEach(fn);
    }

    allCells() {
      return Array.from(this._cells.values());
    }

    neighbors(q, r) {
      var out = [];
      for (var i = 0; i < 6; i++) {
        var nq = q + DIRECTIONS[i].q;
        var nr = r + DIRECTIONS[i].r;
        if (this.has(nq, nr)) {
          out.push({ q: nq, r: nr });
        }
      }
      return out;
    }

    neighborCells(q, r) {
      return this.neighbors(q, r).map(function (n) {
        return this.get(n.q, n.r);
      }.bind(this)).filter(Boolean);
    }

    distance(q1, r1, q2, r2) {
      return (Math.abs(q1 - q2) + Math.abs(q1 + r1 - q2 - r2) + Math.abs(r1 - r2)) / 2;
    }

    ring(cq, cr, radius) {
      if (radius <= 0) return [{ q: cq, r: cr }];
      var results = [];
      var q = cq + DIRECTIONS[4].q * radius;
      var r = cr + DIRECTIONS[4].r * radius;
      for (var i = 0; i < 6; i++) {
        for (var j = 0; j < radius; j++) {
          if (this.has(q, r)) {
            results.push({ q: q, r: r });
          }
          q += DIRECTIONS[i].q;
          r += DIRECTIONS[i].r;
        }
      }
      return results;
    }

    spiral(cq, cr, radius) {
      var results = [{ q: cq, r: cr }];
      for (var k = 1; k <= radius; k++) {
        results = results.concat(this.ring(cq, cr, k));
      }
      return results;
    }

    line(q1, r1, q2, r2) {
      var N = this.distance(q1, r1, q2, r2);
      if (N === 0) return [{ q: q1, r: r1 }];
      var results = [];
      for (var i = 0; i <= N; i++) {
        var t = i / N;
        var fq = q1 + (q2 - q1) * t;
        var fr = r1 + (r2 - r1) * t;
        var rnd = HexGrid._axialRound(fq, fr);
        results.push(rnd);
      }
      return results;
    }

    static _axialRound(q, r) {
      var s = -q - r;
      var rq = Math.round(q);
      var rr = Math.round(r);
      var rs = Math.round(s);
      var dq = Math.abs(rq - q);
      var dr = Math.abs(rr - r);
      var ds = Math.abs(rs - s);
      if (dq > dr && dq > ds) {
        rq = -rr - rs;
      } else if (dr > ds) {
        rr = -rq - rs;
      }
      return { q: rq, r: rr };
    }

    hexToPixel(q, r) {
      var size = 30;
      var x = size * (3 / 2 * q);
      var y = size * (Math.sqrt(3) / 2 * q + Math.sqrt(3) * r);
      return { x: x, y: y };
    }

    pixelToHex(x, y) {
      var size = 30;
      var q = (2 / 3 * x) / size;
      var r = (-1 / 3 * x + Math.sqrt(3) / 3 * y) / size;
      return HexGrid._axialRound(q, r);
    }

    toJSON() {
      var cells = [];
      this._cells.forEach(function (c) { cells.push(c.toJSON()); });
      return { radius: this.radius, cells: cells };
    }

    static fromJSON(data) {
      var g = new HexGrid(0);
      g.radius = data.radius;
      g._cells = new Map();
      data.cells.forEach(function (cd) {
        var c = HexCell.fromJSON(cd);
        g._cells.set(c.key, c);
      });
      return g;
    }
  }

  // -----------------------------------------------------------------
  // TerritorySystem
  // -----------------------------------------------------------------
  class TerritorySystem {
    constructor(gridRadius) {
      gridRadius = gridRadius != null ? gridRadius : 8;
      this.grid = new HexGrid(gridRadius);
      this.turnCounter = 0;
    }

    claim(q, r, playerIdx, color) {
      var cell = this.grid.get(q, r);
      if (!cell) return false;
      if (cell.owner !== null) return false;
      cell.owner = playerIdx;
      cell.color = { h: color.h, s: color.s, l: color.l };
      cell.claimedTurn = this.turnCounter;
      this._updateNeighborBorders(q, r);
      this.calculateComposition(q, r);
      return true;
    }

    expand(fromQ, fromR, toQ, toR, playerIdx) {
      var src = this.grid.get(fromQ, fromR);
      var dst = this.grid.get(toQ, toR);
      if (!src || !dst) return false;
      if (src.owner !== playerIdx) return false;
      if (dst.owner === playerIdx) return false;
      if (this.grid.distance(fromQ, fromR, toQ, toR) !== 1) return false;
      var nbrs = this.grid.neighbors(toQ, toR);
      var adjacent = false;
      for (var i = 0; i < nbrs.length; i++) {
        var nc = this.grid.get(nbrs[i].q, nbrs[i].r);
        if (nc && nc.owner === playerIdx) { adjacent = true; break; }
      }
      if (!adjacent) return false;
      dst.owner = playerIdx;
      dst.color = { h: src.color.h, s: src.color.s, l: src.color.l };
      dst.claimedTurn = this.turnCounter;
      this._updateNeighborBorders(toQ, toR);
      this.calculateComposition(toQ, toR);
      return true;
    }

    getBorders() {
      var borders = [];
      var visited = new Set();
      this.grid.forEach(function (cell) {
        if (cell.owner === null) return;
        var nbrs = this.grid.neighbors(cell.q, cell.r);
        for (var i = 0; i < nbrs.length; i++) {
          var nc = this.grid.get(nbrs[i].q, nbrs[i].r);
          if (!nc) continue;
          if (nc.owner === cell.owner) continue;
          var edgeKey = [cell.key, nc.key].sort().join("|");
          if (visited.has(edgeKey)) continue;
          visited.add(edgeKey);
          borders.push({
            cell1: { q: cell.q, r: cell.r, owner: cell.owner },
            cell2: { q: nc.q, r: nc.r, owner: nc.owner },
            strength: this.getBorderStrength(cell.q, cell.r, nc.q, nc.r)
          });
        }
      }.bind(this));
      return borders;
    }

    getBorderStrength(q1, r1, q2, r2) {
      var c1 = this.grid.get(q1, r1);
      var c2 = this.grid.get(q2, r2);
      if (!c1 || !c2) return 0;
      if (!c1.color || !c2.color) return 0;
      var colorTheory = (window.CT && window.CT.ColorTheory) ? new window.CT.ColorTheory() : null;
      if (colorTheory && typeof colorTheory.contrast === "function") {
        return colorTheory.contrast(c1.color, c2.color);
      }
      var hueDiff = Math.abs(c1.color.h - c2.color.h);
      if (hueDiff > 180) hueDiff = 360 - hueDiff;
      var satFactor = (c1.color.s + c2.color.s) / 200;
      return (hueDiff / 180) * satFactor;
    }

    getTerritory(playerIdx) {
      var cells = [];
      this.grid.forEach(function (cell) {
        if (cell.owner === playerIdx) cells.push(cell);
      });
      return cells;
    }

    getConnectedRegions(playerIdx) {
      var owned = new Map();
      this.grid.forEach(function (cell) {
        if (cell.owner === playerIdx) owned.set(cell.key, cell);
      });
      var visited = new Set();
      var regions = [];
      owned.forEach(function (cell) {
        if (visited.has(cell.key)) return;
        var region = [];
        var stack = [cell];
        while (stack.length > 0) {
          var cur = stack.pop();
          if (visited.has(cur.key)) continue;
          visited.add(cur.key);
          region.push(cur);
          var nbrs = this.grid.neighbors(cur.q, cur.r);
          for (var i = 0; i < nbrs.length; i++) {
            var nk = nbrs[i].q + "," + nbrs[i].r;
            if (owned.has(nk) && !visited.has(nk)) {
              stack.push(owned.get(nk));
            }
          }
        }
        regions.push(region);
      }.bind(this));
      return regions;
    }

    calculateComposition(q, r) {
      var cell = this.grid.get(q, r);
      if (!cell || cell.owner === null) return 0;
      var nbrs = this.grid.neighborCells(q, r);
      if (nbrs.length === 0) { cell.compositionScore = 0.5; return 0.5; }
      var friendlyColors = [];
      var enemyColors = [];
      for (var i = 0; i < nbrs.length; i++) {
        if (nbrs[i].owner === cell.owner && nbrs[i].color) {
          friendlyColors.push(nbrs[i].color);
        } else if (nbrs[i].owner !== null && nbrs[i].color) {
          enemyColors.push(nbrs[i].color);
        }
      }
      var harmony = this._calcHarmony(cell.color, friendlyColors);
      var tension = this._calcTension(cell.color, enemyColors);
      var analyzer = (window.CT && window.CT.CompositionAnalyzer) ? window.CT.CompositionAnalyzer : null;
      var compositionBonus = 0;
      if (analyzer && typeof analyzer.evaluateLocal === "function") {
        compositionBonus = analyzer.evaluateLocal(cell, nbrs) * 0.2;
      }
      var score = Math.min(1, Math.max(0, harmony * 0.6 + tension * 0.2 + 0.2 + compositionBonus));
      cell.compositionScore = score;
      return score;
    }

    _calcHarmony(baseColor, friendlyColors) {
      if (!baseColor || friendlyColors.length === 0) return 0.5;
      var total = 0;
      for (var i = 0; i < friendlyColors.length; i++) {
        var hd = Math.abs(baseColor.h - friendlyColors[i].h);
        if (hd > 180) hd = 360 - hd;
        var analogous = hd <= 30 ? 1.0 : 0;
        var triadic = (hd >= 110 && hd <= 130) ? 0.85 : 0;
        var complementary = (hd >= 165 && hd <= 195) ? 0.7 : 0;
        var best = Math.max(analogous, triadic, complementary, 0.3);
        total += best;
      }
      return total / friendlyColors.length;
    }

    _calcTension(baseColor, enemyColors) {
      if (!baseColor || enemyColors.length === 0) return 0.5;
      var total = 0;
      for (var i = 0; i < enemyColors.length; i++) {
        var hd = Math.abs(baseColor.h - enemyColors[i].h);
        if (hd > 180) hd = 360 - hd;
        total += hd / 180;
      }
      return total / enemyColors.length;
    }

    generateChromaticity(playerIdx) {
      var cells = this.getTerritory(playerIdx);
      if (cells.length === 0) return 0;
      var totalQuality = 0;
      for (var i = 0; i < cells.length; i++) {
        totalQuality += cells[i].compositionScore;
      }
      var avgQuality = totalQuality / cells.length;
      var areaBonus = Math.log2(cells.length + 1);
      var regions = this.getConnectedRegions(playerIdx);
      var connectivityBonus = 1.0 / regions.length;
      return Math.round((avgQuality * 10 + areaBonus * 2) * connectivityBonus * 100) / 100;
    }

    fogOfWar(playerIdx, visionRadius) {
      visionRadius = visionRadius != null ? visionRadius : 3;
      var visible = new Set();
      this.grid.forEach(function (cell) {
        if (cell.owner !== playerIdx) return;
        var area = this.grid.spiral(cell.q, cell.r, visionRadius);
        for (var i = 0; i < area.length; i++) {
          visible.add(area[i].q + "," + area[i].r);
        }
      }.bind(this));
      return visible;
    }

    _updateNeighborBorders(q, r) {
      var cell = this.grid.get(q, r);
      if (!cell) return;
      var nbrs = this.grid.neighbors(q, r);
      var totalBorder = 0;
      var borderCount = 0;
      for (var i = 0; i < nbrs.length; i++) {
        var nc = this.grid.get(nbrs[i].q, nbrs[i].r);
        if (!nc || nc.owner === null) continue;
        if (nc.owner !== cell.owner) {
          var str = this.getBorderStrength(q, r, nbrs[i].q, nbrs[i].r);
          totalBorder += str;
          borderCount++;
        }
      }
      cell.borderStrength = borderCount > 0 ? totalBorder / borderCount : 0;
    }

    advanceTurn() {
      this.turnCounter++;
    }

    toJSON() {
      return {
        grid: this.grid.toJSON(),
        turnCounter: this.turnCounter
      };
    }

    static fromJSON(data) {
      var ts = new TerritorySystem(0);
      ts.grid = HexGrid.fromJSON(data.grid);
      ts.turnCounter = data.turnCounter || 0;
      return ts;
    }
  }

  // -----------------------------------------------------------------
  // Expose on window.CT
  // -----------------------------------------------------------------
  CT.HexCell = HexCell;
  CT.HexGrid = HexGrid;
  CT.TerritorySystem = TerritorySystem;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 2. Game Engine  — core loop and state management
# ---------------------------------------------------------------------------

@register("game_engine")
def generate_game_engine(**kwargs) -> tuple[str, str, str]:
    js = """\
// ===================================================================
// GameEngine  –  core game loop and state management
// ===================================================================
(function () {
  "use strict";

  var CT = window.CT = window.CT || {};

  // -----------------------------------------------------------------
  // Enums
  // -----------------------------------------------------------------
  var GameState = Object.freeze({
    MENU: "MENU",
    SETUP: "SETUP",
    PLAYING: "PLAYING",
    GAME_OVER: "GAME_OVER"
  });

  var TurnPhase = Object.freeze({
    SELECT_ACTION: "SELECT_ACTION",
    CHOOSE_TARGET: "CHOOSE_TARGET",
    APPLY_EFFECT: "APPLY_EFFECT",
    EVALUATE: "EVALUATE",
    AI_TURN: "AI_TURN"
  });

  var ActionType = Object.freeze({
    EXPAND: "EXPAND",
    FORTIFY: "FORTIFY",
    DISRUPT: "DISRUPT",
    HARMONIZE: "HARMONIZE",
    EVOLVE: "EVOLVE"
  });

  // -----------------------------------------------------------------
  // Player
  // -----------------------------------------------------------------
  class Player {
    constructor(id, name, opts) {
      opts = opts || {};
      this.id = id;
      this.name = name;
      this.palette = opts.palette || [
        { h: 0, s: 70, l: 50 },
        { h: 30, s: 70, l: 50 },
        { h: 60, s: 70, l: 50 }
      ];
      this.chromaticity = 0;
      this.territories = 0;
      this.style = opts.style || "BALANCED";
      this.isAI = !!opts.isAI;
      this.score = 0;
      this.achievements = [];
      this.actionsThisTurn = 0;
      this.maxActionsPerTurn = opts.maxActionsPerTurn || 2;
    }

    get primaryColor() {
      return this.palette[0];
    }

    canAct() {
      return this.actionsThisTurn < this.maxActionsPerTurn;
    }

    resetTurn() {
      this.actionsThisTurn = 0;
    }

    toJSON() {
      return {
        id: this.id,
        name: this.name,
        palette: this.palette,
        chromaticity: this.chromaticity,
        territories: this.territories,
        style: this.style,
        isAI: this.isAI,
        score: this.score,
        achievements: this.achievements.slice(),
        actionsThisTurn: this.actionsThisTurn,
        maxActionsPerTurn: this.maxActionsPerTurn
      };
    }

    static fromJSON(data) {
      var p = new Player(data.id, data.name, {
        palette: data.palette,
        style: data.style,
        isAI: data.isAI,
        maxActionsPerTurn: data.maxActionsPerTurn
      });
      p.chromaticity = data.chromaticity || 0;
      p.territories = data.territories || 0;
      p.score = data.score || 0;
      p.achievements = data.achievements || [];
      p.actionsThisTurn = data.actionsThisTurn || 0;
      return p;
    }
  }

  // -----------------------------------------------------------------
  // GameEngine
  // -----------------------------------------------------------------
  class GameEngine {
    constructor(options) {
      options = options || {};
      this.boardSize = options.boardSize || 8;
      this.playerCount = options.playerCount || 2;
      this.aiCount = options.aiCount != null ? options.aiCount : 1;
      this.actionsPerTurn = options.actionsPerTurn || 2;
      this.victoryTerritoryPct = options.victoryTerritoryPct || 0.6;
      this.victoryCompositionThreshold = options.victoryCompositionThreshold || 0.85;
      this.maxTurns = options.maxTurns || 200;

      this.state = GameState.MENU;
      this.phase = TurnPhase.SELECT_ACTION;
      this.currentPlayerIdx = 0;
      this.turnNumber = 1;
      this.players = [];
      this.territory = null;
      this.actionLog = [];
      this._listeners = {};
      this._animationFrameId = null;
      this._updateHook = null;
      this._renderHook = null;
      this._lastTimestamp = 0;
    }

    // -- Event system -------------------------------------------------

    on(event, callback) {
      if (!this._listeners[event]) this._listeners[event] = [];
      this._listeners[event].push(callback);
      return this;
    }

    off(event, callback) {
      var cbs = this._listeners[event];
      if (!cbs) return;
      this._listeners[event] = cbs.filter(function (cb) { return cb !== callback; });
    }

    emit(event, data) {
      var cbs = this._listeners[event];
      if (!cbs) return;
      for (var i = 0; i < cbs.length; i++) {
        try { cbs[i](data); } catch (e) { console.error("Event handler error:", e); }
      }
    }

    // -- Setup --------------------------------------------------------

    start() {
      this.territory = new CT.TerritorySystem(this.boardSize);
      this.players = [];
      var hueStep = 360 / this.playerCount;
      for (var i = 0; i < this.playerCount; i++) {
        var baseHue = Math.round(i * hueStep);
        var isAI = i >= (this.playerCount - this.aiCount);
        var palette = this._generatePalette(baseHue);
        var p = new Player(i, isAI ? "AI " + (i + 1) : "Player " + (i + 1), {
          palette: palette,
          isAI: isAI,
          maxActionsPerTurn: this.actionsPerTurn
        });
        this.players.push(p);
      }
      this._assignStartingPositions();
      this.state = GameState.PLAYING;
      this.phase = TurnPhase.SELECT_ACTION;
      this.currentPlayerIdx = 0;
      this.turnNumber = 1;
      this.emit("gameStart", { players: this.players });
      this.emit("turnStart", { playerIdx: 0, turnNumber: 1 });
      if (this.currentPlayer.isAI) {
        this.phase = TurnPhase.AI_TURN;
        this.emit("aiTurnStart", { playerIdx: 0 });
      }
    }

    _generatePalette(baseHue) {
      return [
        { h: baseHue % 360, s: 70, l: 50 },
        { h: (baseHue + 30) % 360, s: 65, l: 55 },
        { h: (baseHue + 60) % 360, s: 60, l: 60 },
        { h: (baseHue + 330) % 360, s: 65, l: 45 },
        { h: (baseHue + 15) % 360, s: 75, l: 48 }
      ];
    }

    _assignStartingPositions() {
      var grid = this.territory.grid;
      var radius = grid.radius;
      var angleStep = (2 * Math.PI) / this.playerCount;
      for (var i = 0; i < this.playerCount; i++) {
        var angle = i * angleStep - Math.PI / 2;
        var dist = Math.floor(radius * 0.6);
        var approxQ = Math.round(dist * Math.cos(angle));
        var approxR = Math.round(dist * Math.sin(angle));
        var cell = grid.get(approxQ, approxR);
        if (!cell) {
          var ring = grid.ring(0, 0, dist);
          if (ring.length > 0) {
            var idx = Math.floor(i * ring.length / this.playerCount);
            approxQ = ring[idx].q;
            approxR = ring[idx].r;
          }
        }
        var player = this.players[i];
        this.territory.claim(approxQ, approxR, i, player.primaryColor);
        var nbrs = grid.neighbors(approxQ, approxR);
        for (var j = 0; j < Math.min(nbrs.length, 2); j++) {
          this.territory.claim(nbrs[j].q, nbrs[j].r, i, player.palette[j + 1] || player.primaryColor);
        }
        player.territories = this.territory.getTerritory(i).length;
      }
    }

    get currentPlayer() {
      return this.players[this.currentPlayerIdx];
    }

    // -- Actions ------------------------------------------------------

    submitAction(action) {
      if (this.state !== GameState.PLAYING) return { success: false, reason: "Game not in progress" };
      var player = this.currentPlayer;
      if (!player.canAct()) return { success: false, reason: "No actions remaining" };
      if (action.playerIdx !== this.currentPlayerIdx) return { success: false, reason: "Not your turn" };

      this.phase = TurnPhase.APPLY_EFFECT;
      var result = this._executeAction(action);
      if (!result.success) {
        this.phase = TurnPhase.SELECT_ACTION;
        return result;
      }

      player.actionsThisTurn++;
      this.actionLog.push({ turn: this.turnNumber, player: this.currentPlayerIdx, action: action, result: result });
      this.emit("actionApplied", { action: action, result: result });

      this.phase = TurnPhase.EVALUATE;
      this._evaluateBoardState();

      var victory = this.checkVictory();
      if (victory) {
        this.state = GameState.GAME_OVER;
        this.emit("victory", victory);
        return result;
      }

      if (!player.canAct()) {
        this.endTurn();
      } else {
        this.phase = player.isAI ? TurnPhase.AI_TURN : TurnPhase.SELECT_ACTION;
      }
      return result;
    }

    _executeAction(action) {
      switch (action.type) {
        case ActionType.EXPAND:
          return this._executeExpand(action);
        case ActionType.FORTIFY:
          return this._executeFortify(action);
        case ActionType.DISRUPT:
          return this._executeDisrupt(action);
        case ActionType.HARMONIZE:
          return this._executeHarmonize(action);
        case ActionType.EVOLVE:
          return this._executeEvolve(action);
        default:
          return { success: false, reason: "Unknown action type" };
      }
    }

    _executeExpand(action) {
      var ok = this.territory.expand(action.fromQ, action.fromR, action.toQ, action.toR, action.playerIdx);
      if (!ok) return { success: false, reason: "Cannot expand there" };
      this.players[action.playerIdx].territories = this.territory.getTerritory(action.playerIdx).length;
      return { success: true, type: ActionType.EXPAND };
    }

    _executeFortify(action) {
      var cell = this.territory.grid.get(action.q, action.r);
      if (!cell || cell.owner !== action.playerIdx) return { success: false, reason: "Not your cell" };
      cell.fortified = true;
      cell.borderStrength = Math.min(1, cell.borderStrength + 0.3);
      cell.compositionScore = Math.min(1, cell.compositionScore + 0.1);
      return { success: true, type: ActionType.FORTIFY };
    }

    _executeDisrupt(action) {
      var cell = this.territory.grid.get(action.q, action.r);
      if (!cell) return { success: false, reason: "Invalid cell" };
      if (cell.owner === action.playerIdx) return { success: false, reason: "Cannot disrupt own cell" };
      if (cell.owner === null) return { success: false, reason: "Cell is unowned" };
      cell.compositionScore = Math.max(0, cell.compositionScore - 0.25);
      cell.borderStrength = Math.max(0, cell.borderStrength - 0.15);
      if (cell.compositionScore <= 0.1 && !cell.fortified) {
        cell.owner = null;
        cell.color = null;
        cell.fortified = false;
      }
      return { success: true, type: ActionType.DISRUPT };
    }

    _executeHarmonize(action) {
      var cells = this.territory.getTerritory(action.playerIdx);
      if (cells.length === 0) return { success: false, reason: "No territory" };
      var palette = this.players[action.playerIdx].palette;
      for (var i = 0; i < cells.length; i++) {
        var best = palette[0];
        var bestDist = Infinity;
        for (var j = 0; j < palette.length; j++) {
          var hd = Math.abs(cells[i].color.h - palette[j].h);
          if (hd > 180) hd = 360 - hd;
          if (hd < bestDist) { bestDist = hd; best = palette[j]; }
        }
        cells[i].color.h += (best.h - cells[i].color.h) * 0.3;
        cells[i].color.s += (best.s - cells[i].color.s) * 0.2;
        this.territory.calculateComposition(cells[i].q, cells[i].r);
      }
      return { success: true, type: ActionType.HARMONIZE };
    }

    _executeEvolve(action) {
      var cell = this.territory.grid.get(action.q, action.r);
      if (!cell || cell.owner !== action.playerIdx) return { success: false, reason: "Not your cell" };
      cell.color.h = (cell.color.h + action.hueShift) % 360;
      if (cell.color.h < 0) cell.color.h += 360;
      cell.color.s = Math.min(100, Math.max(0, cell.color.s + (action.satShift || 0)));
      cell.color.l = Math.min(90, Math.max(10, cell.color.l + (action.lightShift || 0)));
      this.territory.calculateComposition(cell.q, cell.r);
      return { success: true, type: ActionType.EVOLVE };
    }

    _evaluateBoardState() {
      for (var i = 0; i < this.players.length; i++) {
        var cells = this.territory.getTerritory(i);
        this.players[i].territories = cells.length;
        this.players[i].chromaticity = this.territory.generateChromaticity(i);
      }
    }

    endTurn() {
      this.emit("turnEnd", { playerIdx: this.currentPlayerIdx, turnNumber: this.turnNumber });
      this.currentPlayer.resetTurn();
      this.currentPlayerIdx = (this.currentPlayerIdx + 1) % this.playerCount;
      if (this.currentPlayerIdx === 0) {
        this.turnNumber++;
        this.territory.advanceTurn();
      }
      this.emit("turnStart", { playerIdx: this.currentPlayerIdx, turnNumber: this.turnNumber });
      var cp = this.currentPlayer;
      if (cp.isAI) {
        this.phase = TurnPhase.AI_TURN;
        this.emit("aiTurnStart", { playerIdx: this.currentPlayerIdx });
      } else {
        this.phase = TurnPhase.SELECT_ACTION;
      }
    }

    checkVictory() {
      var totalCells = this.territory.grid.size;
      for (var i = 0; i < this.players.length; i++) {
        var pct = this.players[i].territories / totalCells;
        if (pct >= this.victoryTerritoryPct) {
          return { type: "territory", winner: i, pct: pct };
        }
      }
      for (var i = 0; i < this.players.length; i++) {
        var cells = this.territory.getTerritory(i);
        if (cells.length === 0) continue;
        var avgComp = 0;
        for (var j = 0; j < cells.length; j++) avgComp += cells[j].compositionScore;
        avgComp /= cells.length;
        if (avgComp >= this.victoryCompositionThreshold && cells.length >= totalCells * 0.2) {
          return { type: "composition", winner: i, avgComposition: avgComp };
        }
      }
      if (this.turnNumber >= this.maxTurns) {
        var best = -1, bestScore = -Infinity;
        for (var i = 0; i < this.players.length; i++) {
          var s = this.players[i].territories * 2 + this.players[i].chromaticity;
          if (s > bestScore) { bestScore = s; best = i; }
        }
        return { type: "turns", winner: best, score: bestScore };
      }
      var anyMoves = false;
      for (var i = 0; i < this.players.length; i++) {
        if (this.getValidActions(i).length > 0) { anyMoves = true; break; }
      }
      if (!anyMoves) {
        var best = -1, bestScore = -Infinity;
        for (var i = 0; i < this.players.length; i++) {
          var s = this.players[i].territories * 2 + this.players[i].chromaticity;
          if (s > bestScore) { bestScore = s; best = i; }
        }
        return { type: "stalemate", winner: best, score: bestScore };
      }
      return null;
    }

    getValidActions(playerIdxOverride) {
      var pIdx = playerIdxOverride != null ? playerIdxOverride : this.currentPlayerIdx;
      var actions = [];
      var cells = this.territory.getTerritory(pIdx);
      for (var i = 0; i < cells.length; i++) {
        var nbrs = this.territory.grid.neighbors(cells[i].q, cells[i].r);
        for (var j = 0; j < nbrs.length; j++) {
          var nc = this.territory.grid.get(nbrs[j].q, nbrs[j].r);
          if (nc && nc.owner !== pIdx) {
            actions.push({
              type: nc.owner === null ? ActionType.EXPAND : ActionType.DISRUPT,
              playerIdx: pIdx,
              fromQ: cells[i].q,
              fromR: cells[i].r,
              toQ: nbrs[j].q,
              toR: nbrs[j].r,
              q: nbrs[j].q,
              r: nbrs[j].r
            });
          }
        }
        actions.push({ type: ActionType.FORTIFY, playerIdx: pIdx, q: cells[i].q, r: cells[i].r });
        actions.push({ type: ActionType.EVOLVE, playerIdx: pIdx, q: cells[i].q, r: cells[i].r, hueShift: 15, satShift: 0, lightShift: 0 });
        actions.push({ type: ActionType.EVOLVE, playerIdx: pIdx, q: cells[i].q, r: cells[i].r, hueShift: -15, satShift: 0, lightShift: 0 });
      }
      if (cells.length > 0) {
        actions.push({ type: ActionType.HARMONIZE, playerIdx: pIdx });
      }
      return actions;
    }

    // -- Game loop -----------------------------------------------------

    gameLoop(timestamp) {
      if (this.state !== GameState.PLAYING && this.state !== GameState.GAME_OVER) return;
      var dt = timestamp - this._lastTimestamp;
      this._lastTimestamp = timestamp;
      if (this._updateHook) this._updateHook(dt, this);
      if (this._renderHook) this._renderHook(dt, this);
      if (this.state === GameState.PLAYING) {
        this._animationFrameId = requestAnimationFrame(this.gameLoop.bind(this));
      }
    }

    startLoop(updateHook, renderHook) {
      this._updateHook = updateHook || null;
      this._renderHook = renderHook || null;
      this._lastTimestamp = performance.now();
      this._animationFrameId = requestAnimationFrame(this.gameLoop.bind(this));
    }

    stopLoop() {
      if (this._animationFrameId != null) {
        cancelAnimationFrame(this._animationFrameId);
        this._animationFrameId = null;
      }
    }

    // -- Serialization ------------------------------------------------

    toJSON() {
      return {
        boardSize: this.boardSize,
        playerCount: this.playerCount,
        state: this.state,
        phase: this.phase,
        currentPlayerIdx: this.currentPlayerIdx,
        turnNumber: this.turnNumber,
        players: this.players.map(function (p) { return p.toJSON(); }),
        territory: this.territory ? this.territory.toJSON() : null,
        actionLog: this.actionLog
      };
    }

    static fromJSON(data) {
      var ge = new GameEngine({
        boardSize: data.boardSize,
        playerCount: data.playerCount
      });
      ge.state = data.state;
      ge.phase = data.phase;
      ge.currentPlayerIdx = data.currentPlayerIdx;
      ge.turnNumber = data.turnNumber;
      ge.players = data.players.map(function (pd) { return Player.fromJSON(pd); });
      ge.territory = data.territory ? CT.TerritorySystem.fromJSON(data.territory) : null;
      ge.actionLog = data.actionLog || [];
      return ge;
    }
  }

  // -----------------------------------------------------------------
  // Expose on window.CT
  // -----------------------------------------------------------------
  CT.GameState = GameState;
  CT.TurnPhase = TurnPhase;
  CT.ActionType = ActionType;
  CT.Player = Player;
  CT.GameEngine = GameEngine;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 3. Combat  — color-theory-based combat resolution
# ---------------------------------------------------------------------------

@register("combat")
def generate_combat(**kwargs) -> tuple[str, str, str]:
    js = """\
// ===================================================================
// CombatResolver  –  color-theory-based combat resolution
// ===================================================================
(function () {
  "use strict";

  var CT = window.CT = window.CT || {};

  var AttackType = Object.freeze({
    NOISE_ASSAULT: "NOISE_ASSAULT",
    FRACTAL_SIEGE: "FRACTAL_SIEGE",
    PARTICLE_STORM: "PARTICLE_STORM",
    HARMONIC_WAVE: "HARMONIC_WAVE"
  });

  var Relationship = Object.freeze({
    COMPLEMENTARY: "COMPLEMENTARY",
    ANALOGOUS: "ANALOGOUS",
    TRIADIC: "TRIADIC",
    SPLIT_COMPLEMENTARY: "SPLIT_COMPLEMENTARY",
    CLASH: "CLASH",
    NEUTRAL: "NEUTRAL"
  });

  // -----------------------------------------------------------------
  // CombatResolver
  // -----------------------------------------------------------------
  class CombatResolver {
    constructor(colorTheory, compositionAnalyzer) {
      this.colorTheory = colorTheory || null;
      this.compositionAnalyzer = compositionAnalyzer || null;
      this._rng = Math.random;
    }

    resolve(attacker, defender, attackType, effectQuality) {
      effectQuality = effectQuality != null ? effectQuality : 0.5;
      var atkPower = this.calculateAttackPower(effectQuality, attacker.palette || []);
      var defPower = this.calculateDefense(defender.borderStrength || 0, defender.compositionScore || 0);

      var roll = this._rng() * 0.3 - 0.15;
      var advantage = atkPower - defPower + roll;

      var typeBonus = this._attackTypeBonus(attackType, attacker, defender);
      advantage += typeBonus;

      var success = advantage > 0;
      var newOwner = success ? attacker.owner : defender.owner;
      var newScore = success
        ? Math.max(0.1, effectQuality * 0.6 + defender.compositionScore * 0.2)
        : Math.max(0.05, defender.compositionScore - atkPower * 0.1);

      return {
        success: success,
        newOwner: newOwner,
        newScore: newScore,
        advantage: advantage,
        attackPower: atkPower,
        defensePower: defPower,
        attackType: attackType,
        animation: this.animationData(attackType, attacker, defender)
      };
    }

    calculateAttackPower(effectQuality, attackerPalette) {
      var basePower = effectQuality;
      if (!attackerPalette || attackerPalette.length === 0) return basePower;

      var coherence = this._paletteCoherence(attackerPalette);
      return basePower * (0.7 + coherence * 0.6);
    }

    _paletteCoherence(palette) {
      if (palette.length <= 1) return 1;
      var totalHarmony = 0;
      var pairs = 0;
      for (var i = 0; i < palette.length; i++) {
        for (var j = i + 1; j < palette.length; j++) {
          var rel = this.calculateColorRelationship(palette[i], palette[j]);
          switch (rel) {
            case Relationship.ANALOGOUS: totalHarmony += 1.0; break;
            case Relationship.COMPLEMENTARY: totalHarmony += 0.8; break;
            case Relationship.TRIADIC: totalHarmony += 0.7; break;
            case Relationship.SPLIT_COMPLEMENTARY: totalHarmony += 0.6; break;
            case Relationship.CLASH: totalHarmony += 0.2; break;
            default: totalHarmony += 0.4; break;
          }
          pairs++;
        }
      }
      return pairs > 0 ? totalHarmony / pairs : 0.5;
    }

    calculateDefense(borderStrength, defenderComposition) {
      var borderFactor = borderStrength * 0.5;
      var compFactor = defenderComposition * 0.5;
      return borderFactor + compFactor;
    }

    borderDynamics(cell1, cell2) {
      var c1 = cell1.color;
      var c2 = cell2.color;
      if (!c1 || !c2) return { tension: 0, mergeRisk: 0, chromaticityYield: 0 };

      var rel = this.calculateColorRelationship(c1, c2);

      var tension = 0;
      var mergeRisk = 0;
      var chromaticityYield = 0;

      switch (rel) {
        case Relationship.COMPLEMENTARY:
          tension = 0.9;
          mergeRisk = 0.1;
          chromaticityYield = 0.8;
          break;
        case Relationship.ANALOGOUS:
          tension = 0.2;
          mergeRisk = 0.7;
          chromaticityYield = 0.3;
          break;
        case Relationship.TRIADIC:
          tension = 0.6;
          mergeRisk = 0.3;
          chromaticityYield = 0.6;
          break;
        case Relationship.SPLIT_COMPLEMENTARY:
          tension = 0.7;
          mergeRisk = 0.2;
          chromaticityYield = 0.7;
          break;
        case Relationship.CLASH:
          tension = 1.0;
          mergeRisk = 0.0;
          chromaticityYield = 0.4;
          break;
        default:
          tension = 0.4;
          mergeRisk = 0.4;
          chromaticityYield = 0.5;
          break;
      }

      var satFactor = ((c1.s + c2.s) / 200);
      tension *= satFactor;
      chromaticityYield *= satFactor;

      return {
        tension: Math.round(tension * 1000) / 1000,
        mergeRisk: Math.round(mergeRisk * 1000) / 1000,
        chromaticityYield: Math.round(chromaticityYield * 1000) / 1000
      };
    }

    // -- Attack types -------------------------------------------------

    noiseAssault(targetCell) {
      if (!targetCell || !targetCell.color) return { disruption: 0 };
      var noiseMagnitude = 0.4 + this._rng() * 0.4;
      var hueNoise = (this._rng() - 0.5) * 60 * noiseMagnitude;
      var satNoise = (this._rng() - 0.5) * 30 * noiseMagnitude;
      var lightNoise = (this._rng() - 0.5) * 20 * noiseMagnitude;

      var originalH = targetCell.color.h;
      var originalS = targetCell.color.s;
      var originalL = targetCell.color.l;

      targetCell.color.h = (targetCell.color.h + hueNoise + 360) % 360;
      targetCell.color.s = Math.min(100, Math.max(0, targetCell.color.s + satNoise));
      targetCell.color.l = Math.min(90, Math.max(10, targetCell.color.l + lightNoise));

      var hueDelta = Math.abs(targetCell.color.h - originalH);
      if (hueDelta > 180) hueDelta = 360 - hueDelta;
      var satDelta = Math.abs(targetCell.color.s - originalS);
      var lightDelta = Math.abs(targetCell.color.l - originalL);
      var disruption = (hueDelta / 180 + satDelta / 100 + lightDelta / 80) / 3;

      targetCell.compositionScore = Math.max(0, targetCell.compositionScore - disruption * 0.3);
      return {
        disruption: Math.round(disruption * 1000) / 1000,
        hueShift: hueNoise,
        satShift: satNoise,
        lightShift: lightNoise
      };
    }

    fractalSiege(targetCell) {
      if (!targetCell || !targetCell.color) return { quality: 0 };
      var depth = 3 + Math.floor(this._rng() * 3);
      var scale = 0.5 + this._rng() * 0.5;
      var fractalQuality = this._simulateFractal(depth, scale, targetCell.color);

      var overlayStrength = fractalQuality * 0.5;
      targetCell.compositionScore = Math.max(0,
        targetCell.compositionScore * (1 - overlayStrength) + fractalQuality * overlayStrength * 0.3
      );

      return {
        quality: Math.round(fractalQuality * 1000) / 1000,
        depth: depth,
        scale: scale
      };
    }

    _simulateFractal(depth, scale, color) {
      var quality = 0;
      var weight = 1;
      for (var d = 0; d < depth; d++) {
        var levelQuality = (Math.sin(color.h * 0.017 * (d + 1)) + 1) / 2;
        levelQuality *= (color.s / 100);
        quality += levelQuality * weight;
        weight *= scale;
      }
      var totalWeight = (1 - Math.pow(scale, depth)) / (1 - scale);
      return Math.min(1, quality / totalWeight);
    }

    particleStorm(targetCell) {
      if (!targetCell || !targetCell.color) return { decay: 0, particleCount: 0 };
      var particleCount = 20 + Math.floor(this._rng() * 30);
      var totalDecay = 0;

      for (var i = 0; i < particleCount; i++) {
        var angle = this._rng() * Math.PI * 2;
        var velocity = 0.3 + this._rng() * 0.7;
        var lifetime = 0.5 + this._rng() * 0.5;
        var impact = velocity * lifetime * 0.01;
        totalDecay += impact;
      }

      var decayFactor = Math.min(0.5, totalDecay);
      targetCell.compositionScore = Math.max(0, targetCell.compositionScore - decayFactor);
      targetCell.color.s = Math.max(0, targetCell.color.s - decayFactor * 20);

      return {
        decay: Math.round(decayFactor * 1000) / 1000,
        particleCount: particleCount
      };
    }

    harmonicWave(sourceCell, targetCell) {
      if (!sourceCell || !targetCell) return { resonance: 0 };
      if (!sourceCell.color || !targetCell.color) return { resonance: 0 };

      var rel = this.calculateColorRelationship(sourceCell.color, targetCell.color);
      var baseResonance = 0;
      switch (rel) {
        case Relationship.ANALOGOUS: baseResonance = 0.9; break;
        case Relationship.COMPLEMENTARY: baseResonance = 0.4; break;
        case Relationship.TRIADIC: baseResonance = 0.6; break;
        case Relationship.SPLIT_COMPLEMENTARY: baseResonance = 0.5; break;
        case Relationship.CLASH: baseResonance = 0.1; break;
        default: baseResonance = 0.3; break;
      }

      var satBoost = (sourceCell.color.s / 100) * 0.2;
      var resonance = Math.min(1, baseResonance + satBoost);

      if (sourceCell.owner === targetCell.owner) {
        targetCell.compositionScore = Math.min(1, targetCell.compositionScore + resonance * 0.2);
        targetCell.color.h += (sourceCell.color.h - targetCell.color.h) * resonance * 0.15;
        if (targetCell.color.h < 0) targetCell.color.h += 360;
        targetCell.color.h %= 360;
      } else {
        if (resonance > 0.6) {
          targetCell.compositionScore = Math.max(0, targetCell.compositionScore - resonance * 0.15);
        }
      }

      return {
        resonance: Math.round(resonance * 1000) / 1000,
        relationship: rel
      };
    }

    // -- Color relationship -------------------------------------------

    calculateColorRelationship(hsl1, hsl2) {
      if (!hsl1 || !hsl2) return Relationship.NEUTRAL;
      var hueDiff = Math.abs(hsl1.h - hsl2.h);
      if (hueDiff > 180) hueDiff = 360 - hueDiff;

      if (this.colorTheory && typeof this.colorTheory.relationship === "function") {
        return this.colorTheory.relationship(hsl1, hsl2);
      }

      if (hueDiff <= 30) return Relationship.ANALOGOUS;
      if (hueDiff >= 150 && hueDiff <= 210) return Relationship.COMPLEMENTARY;
      if (hueDiff >= 110 && hueDiff <= 130) return Relationship.TRIADIC;
      if ((hueDiff >= 135 && hueDiff < 150) || (hueDiff > 210 && hueDiff <= 225)) {
        return Relationship.SPLIT_COMPLEMENTARY;
      }
      if (hueDiff >= 60 && hueDiff <= 90) return Relationship.CLASH;
      return Relationship.NEUTRAL;
    }

    // -- Attack type bonus --------------------------------------------

    _attackTypeBonus(attackType, attacker, defender) {
      if (!attacker.color || !defender.color) return 0;
      var rel = this.calculateColorRelationship(attacker.color, defender.color);
      switch (attackType) {
        case AttackType.NOISE_ASSAULT:
          return rel === Relationship.CLASH ? 0.15 : 0;
        case AttackType.FRACTAL_SIEGE:
          return rel === Relationship.COMPLEMENTARY ? 0.15 : 0;
        case AttackType.PARTICLE_STORM:
          return rel === Relationship.TRIADIC ? 0.1 : 0;
        case AttackType.HARMONIC_WAVE:
          return rel === Relationship.ANALOGOUS ? 0.2 : -0.05;
        default:
          return 0;
      }
    }

    // -- Animation data -----------------------------------------------

    animationData(attackType, source, target) {
      var sp = { q: source.q || 0, r: source.r || 0 };
      var tp = { q: target.q || 0, r: target.r || 0 };

      var base = {
        sourceQ: sp.q,
        sourceR: sp.r,
        targetQ: tp.q,
        targetR: tp.r,
        duration: 600,
        easing: "ease-out"
      };

      switch (attackType) {
        case AttackType.NOISE_ASSAULT:
          base.type = "noise";
          base.particles = 30;
          base.turbulence = 0.7;
          base.colorSpread = 60;
          base.duration = 800;
          break;
        case AttackType.FRACTAL_SIEGE:
          base.type = "fractal";
          base.iterations = 5;
          base.scaleDecay = 0.65;
          base.rotationSpeed = 0.5;
          base.duration = 1200;
          base.easing = "ease-in-out";
          break;
        case AttackType.PARTICLE_STORM:
          base.type = "particles";
          base.count = 40;
          base.velocity = 0.8;
          base.gravity = 0.02;
          base.fadeRate = 0.015;
          base.duration = 1000;
          break;
        case AttackType.HARMONIC_WAVE:
          base.type = "wave";
          base.amplitude = 0.6;
          base.frequency = 3;
          base.wavelength = 2;
          base.duration = 900;
          base.easing = "ease-in-out";
          break;
        default:
          base.type = "generic";
          base.particles = 10;
          break;
      }
      return base;
    }
  }

  // -----------------------------------------------------------------
  // Expose on window.CT
  // -----------------------------------------------------------------
  CT.AttackType = AttackType;
  CT.Relationship = Relationship;
  CT.CombatResolver = CombatResolver;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 4. AI Opponent  — AI with artistic personality
# ---------------------------------------------------------------------------

@register("ai_opponent")
def generate_ai_opponent(**kwargs) -> tuple[str, str, str]:
    js = """\
// ===================================================================
// AIOpponent  –  AI with artistic personality
// ===================================================================
(function () {
  "use strict";

  var CT = window.CT = window.CT || {};

  var Difficulty = Object.freeze({
    EASY: "EASY",
    MEDIUM: "MEDIUM",
    HARD: "HARD",
    MASTER: "MASTER"
  });

  var AIStyle = Object.freeze({
    AGGRESSIVE: "AGGRESSIVE",
    DEFENSIVE: "DEFENSIVE",
    CHAOTIC: "CHAOTIC",
    AESTHETIC: "AESTHETIC"
  });

  var ActionType = CT.ActionType || Object.freeze({
    EXPAND: "EXPAND",
    FORTIFY: "FORTIFY",
    DISRUPT: "DISRUPT",
    HARMONIZE: "HARMONIZE",
    EVOLVE: "EVOLVE"
  });

  // -----------------------------------------------------------------
  // MCTSNode (for MASTER difficulty)
  // -----------------------------------------------------------------
  class MCTSNode {
    constructor(state, action, parent) {
      this.state = state;
      this.action = action;
      this.parent = parent || null;
      this.children = [];
      this.visits = 0;
      this.totalScore = 0;
      this.untriedActions = null;
    }

    get avgScore() {
      return this.visits > 0 ? this.totalScore / this.visits : 0;
    }

    ucb1(explorationWeight) {
      if (this.visits === 0) return Infinity;
      var parentVisits = this.parent ? this.parent.visits : this.visits;
      return this.avgScore + explorationWeight * Math.sqrt(Math.log(parentVisits) / this.visits);
    }

    bestChild(explorationWeight) {
      explorationWeight = explorationWeight != null ? explorationWeight : 1.41;
      var best = null;
      var bestVal = -Infinity;
      for (var i = 0; i < this.children.length; i++) {
        var val = this.children[i].ucb1(explorationWeight);
        if (val > bestVal) { bestVal = val; best = this.children[i]; }
      }
      return best;
    }
  }

  // -----------------------------------------------------------------
  // AIOpponent
  // -----------------------------------------------------------------
  class AIOpponent {
    constructor(difficulty, style, playerIdx) {
      this.difficulty = difficulty || Difficulty.MEDIUM;
      this.style = style || AIStyle.AGGRESSIVE;
      this.playerIdx = playerIdx;
      this._rng = Math.random;
      this._turnHistory = [];
      this._strategicGoal = null;
      this._adaptiveStyle = this.style;
    }

    // -- Palette selection --------------------------------------------

    choosePalette(availableHues) {
      var palette = [];
      switch (this.style) {
        case AIStyle.AGGRESSIVE:
          palette = this._warmPalette(availableHues);
          break;
        case AIStyle.DEFENSIVE:
          palette = this._coolPalette(availableHues);
          break;
        case AIStyle.CHAOTIC:
          palette = this._chaoticPalette(availableHues);
          break;
        case AIStyle.AESTHETIC:
          palette = this._aestheticPalette(availableHues);
          break;
        default:
          palette = this._warmPalette(availableHues);
          break;
      }
      return palette;
    }

    _warmPalette(hues) {
      var warmHues = (hues || []).filter(function (h) {
        return (h >= 0 && h <= 60) || (h >= 300 && h <= 360);
      });
      if (warmHues.length < 3) {
        warmHues = [0, 15, 30, 45, 345];
      }
      return this._buildPalette(warmHues, 75, 55);
    }

    _coolPalette(hues) {
      var coolHues = (hues || []).filter(function (h) {
        return h >= 180 && h <= 300;
      });
      if (coolHues.length < 3) {
        coolHues = [200, 220, 240, 260, 190];
      }
      return this._buildPalette(coolHues, 60, 50);
    }

    _chaoticPalette(hues) {
      var selected = [];
      for (var i = 0; i < 5; i++) {
        selected.push(Math.floor(this._rng() * 360));
      }
      return this._buildPalette(selected, 80, 50);
    }

    _aestheticPalette(hues) {
      var base = hues && hues.length > 0 ? hues[0] : Math.floor(this._rng() * 360);
      var selected = [
        base,
        (base + 30) % 360,
        (base + 60) % 360,
        (base + 330) % 360,
        (base + 15) % 360
      ];
      return this._buildPalette(selected, 65, 55);
    }

    _buildPalette(hueList, sat, light) {
      var palette = [];
      for (var i = 0; i < Math.min(5, hueList.length); i++) {
        palette.push({
          h: hueList[i] % 360,
          s: sat + Math.floor((this._rng() - 0.5) * 10),
          l: light + Math.floor((this._rng() - 0.5) * 10)
        });
      }
      return palette;
    }

    // -- Action selection ---------------------------------------------

    chooseAction(gameState) {
      this.adaptStrategy(gameState);

      var actions = gameState.getValidActions
        ? gameState.getValidActions(this.playerIdx)
        : (gameState.validActions || []);

      if (actions.length === 0) return null;

      switch (this.difficulty) {
        case Difficulty.EASY:
          return this._chooseEasy(actions, gameState);
        case Difficulty.MEDIUM:
          return this._chooseMedium(actions, gameState);
        case Difficulty.HARD:
          return this._chooseHard(actions, gameState);
        case Difficulty.MASTER:
          return this._chooseMaster(actions, gameState);
        default:
          return actions[Math.floor(this._rng() * actions.length)];
      }
    }

    _chooseEasy(actions, gameState) {
      var priorities = this.getActionPriorities();
      var weighted = [];
      for (var i = 0; i < actions.length; i++) {
        var w = priorities[actions[i].type] || 1;
        w += this._rng() * 3;
        weighted.push({ action: actions[i], weight: w });
      }
      weighted.sort(function (a, b) { return b.weight - a.weight; });
      var top = Math.min(5, weighted.length);
      return weighted[Math.floor(this._rng() * top)].action;
    }

    _chooseMedium(actions, gameState) {
      var bestAction = null;
      var bestScore = -Infinity;
      var sampleSize = Math.min(actions.length, 20);
      var sampled = this._sampleActions(actions, sampleSize);
      for (var i = 0; i < sampled.length; i++) {
        var score = this.evaluateAction(sampled[i], gameState);
        if (score > bestScore) {
          bestScore = score;
          bestAction = sampled[i];
        }
      }
      return bestAction;
    }

    _chooseHard(actions, gameState) {
      var bestAction = null;
      var bestScore = -Infinity;
      for (var i = 0; i < actions.length; i++) {
        var score = this.evaluateAction(actions[i], gameState);
        score += this.minimax(gameState, 2, -Infinity, Infinity, true) * 0.1;
        if (score > bestScore) {
          bestScore = score;
          bestAction = actions[i];
        }
      }
      return bestAction;
    }

    _chooseMaster(actions, gameState) {
      return this.mcts(gameState, 100);
    }

    _sampleActions(actions, count) {
      if (actions.length <= count) return actions.slice();
      var copy = actions.slice();
      var sampled = [];
      for (var i = 0; i < count; i++) {
        var idx = Math.floor(this._rng() * copy.length);
        sampled.push(copy[idx]);
        copy.splice(idx, 1);
      }
      return sampled;
    }

    // -- State evaluation ---------------------------------------------

    evaluateState(state, playerIdx) {
      playerIdx = playerIdx != null ? playerIdx : this.playerIdx;
      var territory, players;

      if (state.territory) {
        territory = state.territory;
        players = state.players || [];
      } else {
        return 0;
      }

      var cells = territory.getTerritory(playerIdx);
      var area = cells.length;

      var avgComposition = 0;
      for (var i = 0; i < cells.length; i++) {
        avgComposition += cells[i].compositionScore;
      }
      avgComposition = cells.length > 0 ? avgComposition / cells.length : 0;

      var chromaticity = territory.generateChromaticity(playerIdx);
      var borders = territory.getBorders();
      var borderSum = 0;
      var borderCount = 0;
      for (var j = 0; j < borders.length; j++) {
        if (borders[j].cell1.owner === playerIdx || borders[j].cell2.owner === playerIdx) {
          borderSum += borders[j].strength;
          borderCount++;
        }
      }
      var avgBorder = borderCount > 0 ? borderSum / borderCount : 0;

      var regions = territory.getConnectedRegions(playerIdx);
      var connectedness = regions.length > 0 ? 1 / regions.length : 0;

      var score = area * 2 + avgComposition * 3 + chromaticity + avgBorder + connectedness * 2;

      if (this._adaptiveStyle === AIStyle.AESTHETIC) {
        score += avgComposition * 5;
      } else if (this._adaptiveStyle === AIStyle.AGGRESSIVE) {
        score += area * 2;
      } else if (this._adaptiveStyle === AIStyle.DEFENSIVE) {
        score += avgBorder * 3 + connectedness * 3;
      }

      return score;
    }

    evaluateAction(action, state) {
      var currentScore = this.evaluateState(state, this.playerIdx);
      var simState = this._simulateAction(action, state);
      if (!simState) return currentScore;
      var newScore = this.evaluateState(simState, this.playerIdx);
      var priorities = this.getActionPriorities();
      var typeWeight = priorities[action.type] || 1;
      return (newScore - currentScore) * typeWeight;
    }

    _simulateAction(action, state) {
      if (!state || !state.toJSON) return null;
      try {
        var json = state.toJSON();
        var clone = CT.GameEngine.fromJSON(json);
        clone.submitAction(action);
        return clone;
      } catch (e) {
        return null;
      }
    }

    // -- Minimax (HARD+) ----------------------------------------------

    minimax(state, depth, alpha, beta, maximizing) {
      if (depth <= 0) return this.evaluateState(state, this.playerIdx);

      var actions = state.getValidActions
        ? state.getValidActions(maximizing ? this.playerIdx : this._opponentIdx(state))
        : [];
      if (actions.length === 0) return this.evaluateState(state, this.playerIdx);

      var sampledActions = this._sampleActions(actions, Math.min(actions.length, 8));

      if (maximizing) {
        var maxEval = -Infinity;
        for (var i = 0; i < sampledActions.length; i++) {
          var simState = this._simulateAction(sampledActions[i], state);
          if (!simState) continue;
          var evalScore = this.minimax(simState, depth - 1, alpha, beta, false);
          maxEval = Math.max(maxEval, evalScore);
          alpha = Math.max(alpha, evalScore);
          if (beta <= alpha) break;
        }
        return maxEval === -Infinity ? this.evaluateState(state, this.playerIdx) : maxEval;
      } else {
        var minEval = Infinity;
        for (var i = 0; i < sampledActions.length; i++) {
          var simState = this._simulateAction(sampledActions[i], state);
          if (!simState) continue;
          var evalScore = this.minimax(simState, depth - 1, alpha, beta, true);
          minEval = Math.min(minEval, evalScore);
          beta = Math.min(beta, evalScore);
          if (beta <= alpha) break;
        }
        return minEval === Infinity ? this.evaluateState(state, this.playerIdx) : minEval;
      }
    }

    _opponentIdx(state) {
      var players = state.players || [];
      for (var i = 0; i < players.length; i++) {
        if (i !== this.playerIdx) return i;
      }
      return (this.playerIdx + 1) % Math.max(2, players.length);
    }

    // -- MCTS (MASTER) ------------------------------------------------

    mcts(state, iterations) {
      iterations = iterations != null ? iterations : 100;

      var root = new MCTSNode(state, null, null);
      root.untriedActions = this._getActionsForNode(state);

      for (var iter = 0; iter < iterations; iter++) {
        var node = root;

        // Selection
        while (node.untriedActions && node.untriedActions.length === 0 && node.children.length > 0) {
          node = node.bestChild(1.41);
        }

        // Expansion
        if (node.untriedActions && node.untriedActions.length > 0) {
          var actionIdx = Math.floor(this._rng() * node.untriedActions.length);
          var action = node.untriedActions.splice(actionIdx, 1)[0];
          var newState = this._simulateAction(action, node.state);
          if (newState) {
            var child = new MCTSNode(newState, action, node);
            child.untriedActions = this._getActionsForNode(newState);
            node.children.push(child);
            node = child;
          }
        }

        // Simulation (random playout)
        var rolloutScore = this._rollout(node.state, 6);

        // Back-propagation
        while (node) {
          node.visits++;
          node.totalScore += rolloutScore;
          node = node.parent;
        }
      }

      // Select best child of root (most visits)
      if (root.children.length === 0) {
        var fallback = root.untriedActions || this._getActionsForNode(state);
        return fallback.length > 0 ? fallback[0] : null;
      }
      var bestChild = null;
      var mostVisits = -1;
      for (var i = 0; i < root.children.length; i++) {
        if (root.children[i].visits > mostVisits) {
          mostVisits = root.children[i].visits;
          bestChild = root.children[i];
        }
      }
      return bestChild ? bestChild.action : null;
    }

    _getActionsForNode(state) {
      if (!state) return [];
      if (state.getValidActions) {
        return this._sampleActions(state.getValidActions(this.playerIdx), 15);
      }
      return [];
    }

    _rollout(state, maxDepth) {
      var current = state;
      for (var d = 0; d < maxDepth; d++) {
        var actions = current && current.getValidActions
          ? current.getValidActions(this.playerIdx)
          : [];
        if (actions.length === 0) break;
        var action = actions[Math.floor(this._rng() * actions.length)];
        var next = this._simulateAction(action, current);
        if (!next) break;
        current = next;
      }
      return this.evaluateState(current, this.playerIdx);
    }

    // -- Strategy adaptation ------------------------------------------

    adaptStrategy(gameState) {
      if (!gameState || !gameState.territory) return;

      var myCells = gameState.territory.getTerritory(this.playerIdx);
      var totalCells = gameState.territory.grid.size;
      var myPct = totalCells > 0 ? myCells.length / totalCells : 0;

      var avgComp = 0;
      for (var i = 0; i < myCells.length; i++) {
        avgComp += myCells[i].compositionScore;
      }
      avgComp = myCells.length > 0 ? avgComp / myCells.length : 0;

      var regions = gameState.territory.getConnectedRegions(this.playerIdx);
      var fragmented = regions.length > 2;

      if (myPct < 0.15) {
        this._adaptiveStyle = AIStyle.AGGRESSIVE;
      } else if (myPct > 0.4 && avgComp < 0.5) {
        this._adaptiveStyle = AIStyle.AESTHETIC;
      } else if (fragmented) {
        this._adaptiveStyle = AIStyle.DEFENSIVE;
      } else if (myPct > 0.3) {
        this._adaptiveStyle = this.style;
      } else {
        this._adaptiveStyle = this.style;
      }

      this._turnHistory.push({
        turn: gameState.turnNumber || 0,
        style: this._adaptiveStyle,
        territory: myPct,
        composition: avgComp
      });
    }

    artisticIntent(state) {
      if (!state || !state.territory) return { goal: "expand", targetHue: 0 };

      var myCells = state.territory.getTerritory(this.playerIdx);
      if (myCells.length === 0) return { goal: "expand", targetHue: 0 };

      var hueHist = new Array(12).fill(0);
      for (var i = 0; i < myCells.length; i++) {
        if (myCells[i].color) {
          var bucket = Math.floor(myCells[i].color.h / 30) % 12;
          hueHist[bucket]++;
        }
      }

      var dominantBucket = 0;
      for (var i = 1; i < 12; i++) {
        if (hueHist[i] > hueHist[dominantBucket]) dominantBucket = i;
      }
      var dominantHue = dominantBucket * 30 + 15;

      var avgComp = 0;
      for (var i = 0; i < myCells.length; i++) avgComp += myCells[i].compositionScore;
      avgComp /= myCells.length;

      if (avgComp < 0.4) {
        return { goal: "harmonize", targetHue: dominantHue, reason: "low composition" };
      }
      if (this._adaptiveStyle === AIStyle.AESTHETIC) {
        var complementHue = (dominantHue + 180) % 360;
        return { goal: "compose", targetHue: complementHue, reason: "aesthetic balance" };
      }
      return { goal: "expand", targetHue: dominantHue, reason: "territorial growth" };
    }

    getActionPriorities() {
      var style = this._adaptiveStyle || this.style;
      switch (style) {
        case AIStyle.AGGRESSIVE:
          return {
            EXPAND: 3.0,
            DISRUPT: 2.5,
            FORTIFY: 0.5,
            HARMONIZE: 0.8,
            EVOLVE: 1.0
          };
        case AIStyle.DEFENSIVE:
          return {
            EXPAND: 1.0,
            DISRUPT: 0.5,
            FORTIFY: 3.0,
            HARMONIZE: 2.5,
            EVOLVE: 1.5
          };
        case AIStyle.CHAOTIC:
          return {
            EXPAND: 1.5 + this._rng(),
            DISRUPT: 1.5 + this._rng(),
            FORTIFY: 1.5 + this._rng(),
            HARMONIZE: 1.5 + this._rng(),
            EVOLVE: 1.5 + this._rng()
          };
        case AIStyle.AESTHETIC:
          return {
            EXPAND: 1.2,
            DISRUPT: 0.3,
            FORTIFY: 1.5,
            HARMONIZE: 3.0,
            EVOLVE: 2.5
          };
        default:
          return {
            EXPAND: 1.5,
            DISRUPT: 1.5,
            FORTIFY: 1.5,
            HARMONIZE: 1.5,
            EVOLVE: 1.5
          };
      }
    }
  }

  // -----------------------------------------------------------------
  // Expose on window.CT
  // -----------------------------------------------------------------
  CT.Difficulty = Difficulty;
  CT.AIStyle = AIStyle;
  CT.AIOpponent = AIOpponent;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 5. Scoring  — composition-based scoring with achievements
# ---------------------------------------------------------------------------

@register("scoring")
def generate_scoring(**kwargs) -> tuple[str, str, str]:
    js = """\
// ===================================================================
// ScoringSystem  –  composition-based scoring with achievements
// ===================================================================
(function () {
  "use strict";

  var CT = window.CT = window.CT || {};

  // -----------------------------------------------------------------
  // Achievement definitions
  // -----------------------------------------------------------------
  var ACHIEVEMENTS = [
    {
      id: "harmonist",
      name: "Harmonist",
      description: "Achieve average composition score above 0.8 across your territory.",
      icon: "🎵",
      check: function (player, state) {
        var cells = state.territory.getTerritory(player.id);
        if (cells.length < 5) return false;
        var avg = 0;
        for (var i = 0; i < cells.length; i++) avg += cells[i].compositionScore;
        avg /= cells.length;
        return avg >= 0.8;
      }
    },
    {
      id: "disruptor",
      name: "Disruptor",
      description: "Successfully disrupt 10 enemy cells in a single game.",
      icon: "💥",
      check: function (player, state) {
        var log = state.actionLog || [];
        var count = 0;
        for (var i = 0; i < log.length; i++) {
          if (log[i].player === player.id && log[i].action.type === "DISRUPT" && log[i].result.success) {
            count++;
          }
        }
        return count >= 10;
      }
    },
    {
      id: "expansionist",
      name: "Expansionist",
      description: "Control 40% or more of the board.",
      icon: "🌍",
      check: function (player, state) {
        var total = state.territory.grid.size;
        var owned = state.territory.getTerritory(player.id).length;
        return total > 0 && (owned / total) >= 0.4;
      }
    },
    {
      id: "alchemist",
      name: "Alchemist",
      description: "Evolve a cell's hue by more than 120 degrees from its original color.",
      icon: "⚗️",
      check: function (player, state) {
        var cells = state.territory.getTerritory(player.id);
        for (var i = 0; i < cells.length; i++) {
          if (cells[i].color && cells[i].claimedTurn >= 0) {
            var palette = player.palette || [];
            for (var j = 0; j < palette.length; j++) {
              var hd = Math.abs(cells[i].color.h - palette[j].h);
              if (hd > 180) hd = 360 - hd;
              if (hd > 120) return true;
            }
          }
        }
        return false;
      }
    },
    {
      id: "goldilocks",
      name: "Goldilocks",
      description: "Have all owned cells with composition scores between 0.45 and 0.55.",
      icon: "🐻",
      check: function (player, state) {
        var cells = state.territory.getTerritory(player.id);
        if (cells.length < 3) return false;
        for (var i = 0; i < cells.length; i++) {
          if (cells[i].compositionScore < 0.45 || cells[i].compositionScore > 0.55) return false;
        }
        return true;
      }
    },
    {
      id: "chromatic_master",
      name: "Chromatic Master",
      description: "Generate 50 or more chromaticity in a single turn.",
      icon: "🌈",
      check: function (player, state) {
        return (player.chromaticity || 0) >= 50;
      }
    },
    {
      id: "first_blood",
      name: "First Blood",
      description: "Be the first player to capture an enemy cell.",
      icon: "⚔️",
      check: function (player, state) {
        var log = state.actionLog || [];
        for (var i = 0; i < log.length; i++) {
          if (log[i].action.type === "DISRUPT" && log[i].result.success) {
            return log[i].player === player.id;
          }
        }
        return false;
      }
    },
    {
      id: "pacifist",
      name: "Pacifist",
      description: "Win the game without ever using the DISRUPT action.",
      icon: "☮️",
      check: function (player, state) {
        if (state.state !== "GAME_OVER") return false;
        var log = state.actionLog || [];
        for (var i = 0; i < log.length; i++) {
          if (log[i].player === player.id && log[i].action.type === "DISRUPT") return false;
        }
        return true;
      }
    },
    {
      id: "speed_runner",
      name: "Speed Runner",
      description: "Win the game in 15 turns or fewer.",
      icon: "⚡",
      check: function (player, state) {
        if (state.state !== "GAME_OVER") return false;
        return (state.turnNumber || Infinity) <= 15;
      }
    },
    {
      id: "perfectionist",
      name: "Perfectionist",
      description: "Have 20+ cells all with composition score above 0.9.",
      icon: "💎",
      check: function (player, state) {
        var cells = state.territory.getTerritory(player.id);
        if (cells.length < 20) return false;
        for (var i = 0; i < cells.length; i++) {
          if (cells[i].compositionScore < 0.9) return false;
        }
        return true;
      }
    },
    {
      id: "connected",
      name: "United Front",
      description: "Have all your territory in a single connected region of 15+ cells.",
      icon: "🔗",
      check: function (player, state) {
        var regions = state.territory.getConnectedRegions(player.id);
        return regions.length === 1 && regions[0].length >= 15;
      }
    },
    {
      id: "rainbow",
      name: "Rainbow Warrior",
      description: "Own cells spanning at least 5 distinct 60-degree hue sectors.",
      icon: "🏳️‍🌈",
      check: function (player, state) {
        var cells = state.territory.getTerritory(player.id);
        var sectors = new Set();
        for (var i = 0; i < cells.length; i++) {
          if (cells[i].color) {
            sectors.add(Math.floor(cells[i].color.h / 60));
          }
        }
        return sectors.size >= 5;
      }
    }
  ];

  // -----------------------------------------------------------------
  // ScoringSystem
  // -----------------------------------------------------------------
  class ScoringSystem {
    constructor() {
      this.achievements = ACHIEVEMENTS.slice();
      this._playerAchievements = {};
    }

    calculateTerritoryHealth(territory, cells) {
      if (!cells || cells.length === 0) return 0;

      var totalComp = 0;
      var totalBorder = 0;
      var fortifiedCount = 0;
      for (var i = 0; i < cells.length; i++) {
        totalComp += cells[i].compositionScore;
        totalBorder += cells[i].borderStrength;
        if (cells[i].fortified) fortifiedCount++;
      }
      var avgComp = totalComp / cells.length;
      var avgBorder = totalBorder / cells.length;
      var fortifiedRatio = fortifiedCount / cells.length;

      var area = cells.length;
      var areaWeight = Math.min(1, area / 30);

      var regions = territory.getConnectedRegions(cells[0].owner);
      var connectivity = regions.length > 0 ? 1 / regions.length : 0;

      var health = avgComp * 0.4 + avgBorder * 0.15 + fortifiedRatio * 0.1 +
                   areaWeight * 0.2 + connectivity * 0.15;

      return Math.round(health * 1000) / 1000;
    }

    calculateChromaticityIncome(territory, playerIdx) {
      var cells = territory.getTerritory(playerIdx);
      if (cells.length === 0) return 0;

      var totalComp = 0;
      for (var i = 0; i < cells.length; i++) {
        totalComp += cells[i].compositionScore;
      }
      var avgComp = totalComp / cells.length;

      var area = cells.length;
      var areaFactor = Math.log2(area + 1);

      var borders = territory.getBorders();
      var borderValue = 0;
      for (var j = 0; j < borders.length; j++) {
        if (borders[j].cell1.owner === playerIdx || borders[j].cell2.owner === playerIdx) {
          borderValue += borders[j].strength * 0.5;
        }
      }

      var regions = territory.getConnectedRegions(playerIdx);
      var connectBonus = regions.length === 1 ? 1.5 : 1.0 / regions.length;

      var income = (avgComp * 5 + areaFactor * 2 + borderValue) * connectBonus;
      return Math.round(income * 100) / 100;
    }

    calculatePlayerScore(player, territory) {
      var cells = territory.getTerritory(player.id);
      var area = cells.length;

      var avgComp = 0;
      for (var i = 0; i < cells.length; i++) avgComp += cells[i].compositionScore;
      avgComp = cells.length > 0 ? avgComp / cells.length : 0;

      var chromaticity = player.chromaticity || 0;
      var health = this.calculateTerritoryHealth(territory, cells);

      var achievementBonus = 0;
      var pa = this._playerAchievements[player.id] || [];
      achievementBonus = pa.length * 50;

      var score = area * 10 + avgComp * 200 + chromaticity * 5 + health * 100 + achievementBonus;
      return Math.round(score);
    }

    checkAchievements(player, gameState) {
      if (!this._playerAchievements[player.id]) {
        this._playerAchievements[player.id] = [];
      }
      var earned = this._playerAchievements[player.id];
      var earnedIds = new Set(earned.map(function (a) { return a.id; }));
      var newlyUnlocked = [];

      for (var i = 0; i < this.achievements.length; i++) {
        var ach = this.achievements[i];
        if (earnedIds.has(ach.id)) continue;
        try {
          if (ach.check(player, gameState)) {
            var record = {
              id: ach.id,
              name: ach.name,
              description: ach.description,
              icon: ach.icon,
              unlockedAt: Date.now(),
              turn: gameState.turnNumber || 0
            };
            earned.push(record);
            newlyUnlocked.push(record);
          }
        } catch (e) {
          // achievement check failed — skip
        }
      }
      return newlyUnlocked;
    }

    getPlayerAchievements(playerId) {
      return (this._playerAchievements[playerId] || []).slice();
    }

    formatScore(score) {
      if (score == null || isNaN(score)) return "0";
      var str = Math.round(score).toString();
      var parts = [];
      while (str.length > 3) {
        parts.unshift(str.slice(-3));
        str = str.slice(0, -3);
      }
      parts.unshift(str);
      return parts.join(",");
    }

    getLeaderboard(dataLayer) {
      var entries = [];
      if (dataLayer && typeof dataLayer.load === "function") {
        try {
          entries = dataLayer.load("leaderboard") || [];
        } catch (e) {
          entries = [];
        }
      } else if (typeof localStorage !== "undefined") {
        try {
          entries = JSON.parse(localStorage.getItem("ct_leaderboard") || "[]");
        } catch (e) {
          entries = [];
        }
      }
      entries.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
      return entries;
    }

    addToLeaderboard(entry, dataLayer) {
      var entries = this.getLeaderboard(dataLayer);
      entry.timestamp = entry.timestamp || Date.now();
      entries.push(entry);
      entries.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
      entries = entries.slice(0, 100);

      if (dataLayer && typeof dataLayer.save === "function") {
        try {
          dataLayer.save("leaderboard", entries);
        } catch (e) { /* storage error */ }
      } else if (typeof localStorage !== "undefined") {
        try {
          localStorage.setItem("ct_leaderboard", JSON.stringify(entries));
        } catch (e) { /* storage error */ }
      }
      return entries;
    }

    animateScoreChange(el, oldVal, newVal, duration) {
      duration = duration || 800;
      if (!el) return;
      var startTime = null;
      var diff = newVal - oldVal;

      function step(timestamp) {
        if (!startTime) startTime = timestamp;
        var progress = Math.min(1, (timestamp - startTime) / duration);
        var eased = 1 - Math.pow(1 - progress, 3);
        var current = Math.round(oldVal + diff * eased);
        el.textContent = ScoringSystem.prototype.formatScore.call(null, current);

        if (diff > 0) {
          el.style.color = "hsl(120, 70%, " + (40 + progress * 20) + "%)";
        } else if (diff < 0) {
          el.style.color = "hsl(0, 70%, " + (40 + progress * 20) + "%)";
        }

        if (progress < 1) {
          requestAnimationFrame(step);
        } else {
          el.textContent = ScoringSystem.prototype.formatScore.call(null, newVal);
          setTimeout(function () { el.style.color = ""; }, 300);
        }
      }

      requestAnimationFrame(step);
    }

    generateEndGameSummary(players, territory, actionLog) {
      var summaries = [];
      for (var i = 0; i < players.length; i++) {
        var p = players[i];
        var cells = territory.getTerritory(p.id);
        var avgComp = 0;
        for (var j = 0; j < cells.length; j++) avgComp += cells[j].compositionScore;
        avgComp = cells.length > 0 ? avgComp / cells.length : 0;

        var actionsUsed = {};
        for (var k = 0; k < (actionLog || []).length; k++) {
          if (actionLog[k].player === p.id) {
            var t = actionLog[k].action.type;
            actionsUsed[t] = (actionsUsed[t] || 0) + 1;
          }
        }

        var score = this.calculatePlayerScore(p, territory);
        var achievs = this.getPlayerAchievements(p.id);

        summaries.push({
          playerId: p.id,
          playerName: p.name,
          score: score,
          formattedScore: this.formatScore(score),
          territories: cells.length,
          avgComposition: Math.round(avgComp * 1000) / 1000,
          chromaticity: p.chromaticity || 0,
          achievements: achievs,
          actionsUsed: actionsUsed,
          regions: territory.getConnectedRegions(p.id).length
        });
      }
      summaries.sort(function (a, b) { return b.score - a.score; });
      return summaries;
    }
  }

  // -----------------------------------------------------------------
  // Expose on window.CT
  // -----------------------------------------------------------------
  CT.ScoringSystem = ScoringSystem;
})();
"""
    return (js, "", "")
