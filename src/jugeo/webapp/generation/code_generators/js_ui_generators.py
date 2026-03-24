"""UI and media code generators — renderer, UI system, gallery, tutorial, audio, music, app init."""
from __future__ import annotations

from . import register


# ---------------------------------------------------------------------------
# 1. Canvas Renderer
# ---------------------------------------------------------------------------


@register("canvas_renderer")
def generate_canvas_renderer(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var LAYERS = ['terrain', 'territory', 'effects', 'ui'];
  var HEX_ANGLES = [];
  for (var i = 0; i < 6; i++) {
    HEX_ANGLES.push((Math.PI / 3) * i - Math.PI / 6);
  }

  var TERRAIN_COLORS = {
    deepWater:   '#0a2463',
    water:       '#1e6091',
    shallowWater:'#168aad',
    sand:        '#e9c46a',
    lowland:     '#52b788',
    grassland:   '#40916c',
    highland:    '#6b4226',
    mountain:    '#8d6e63',
    peak:        '#d5c4a1',
    snow:        '#f0ead6'
  };

  function parseHexColor(hex) {
    hex = hex.replace('#', '');
    return {
      r: parseInt(hex.substring(0, 2), 16),
      g: parseInt(hex.substring(2, 4), 16),
      b: parseInt(hex.substring(4, 6), 16)
    };
  }

  function rgbToHex(r, g, b) {
    r = Math.max(0, Math.min(255, Math.round(r)));
    g = Math.max(0, Math.min(255, Math.round(g)));
    b = Math.max(0, Math.min(255, Math.round(b)));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }

  function lerpColor(a, b, t) {
    var ca = parseHexColor(a);
    var cb = parseHexColor(b);
    return rgbToHex(
      ca.r + (cb.r - ca.r) * t,
      ca.g + (cb.g - ca.g) * t,
      ca.b + (cb.b - ca.b) * t
    );
  }

  function clamp(val, min, max) {
    return Math.max(min, Math.min(max, val));
  }

  class CanvasRenderer {
    constructor(container) {
      if (typeof container === 'string') {
        this.container = document.querySelector(container);
      } else {
        this.container = container || document.body;
      }
      this.container.style.position = 'relative';
      this.container.style.overflow = 'hidden';

      this.layers = {};
      this.contexts = {};
      this.viewport = {
        x: 0, y: 0, zoom: 1,
        targetX: 0, targetY: 0, targetZoom: 1
      };
      this.animations = [];
      this.dpr = window.devicePixelRatio || 1;
      this.isDragging = false;
      this.lastMouse = { x: 0, y: 0 };
      this.pinchStartDist = 0;
      this.width = 0;
      this.height = 0;
      this.minimapSize = 180;
      this.minimapPadding = 12;
      this.showMinimap = true;
      this.lastFrameTime = 0;
      this.frameCount = 0;
      this.fps = 0;
      this._boundResize = this.resize.bind(this);
      this._boundWheel = this._onWheel.bind(this);
      this._boundMouseDown = this._onMouseDown.bind(this);
      this._boundMouseMove = this._onMouseMove.bind(this);
      this._boundMouseUp = this._onMouseUp.bind(this);
      this._boundTouchStart = this._onTouchStart.bind(this);
      this._boundTouchMove = this._onTouchMove.bind(this);
      this._boundTouchEnd = this._onTouchEnd.bind(this);

      this._initLayers();
      this._bindEvents();
    }

    _initLayers() {
      for (var li = 0; li < LAYERS.length; li++) {
        var name = LAYERS[li];
        var canvas = document.createElement('canvas');
        canvas.className = 'ct-layer ct-layer-' + name;
        canvas.style.position = 'absolute';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.pointerEvents = (name === 'ui') ? 'auto' : 'none';
        this.container.appendChild(canvas);
        this.layers[name] = canvas;
        this.contexts[name] = canvas.getContext('2d');
      }
      this.resize();
    }

    resize() {
      var rect = this.container.getBoundingClientRect();
      this.width = rect.width;
      this.height = rect.height;
      var dpr = this.dpr;
      for (var li = 0; li < LAYERS.length; li++) {
        var name = LAYERS[li];
        var canvas = this.layers[name];
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        var ctx = this.contexts[name];
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    }

    _hexPath(ctx, cx, cy, size) {
      ctx.beginPath();
      for (var i = 0; i < 6; i++) {
        var x = cx + size * Math.cos(HEX_ANGLES[i]);
        var y = cy + size * Math.sin(HEX_ANGLES[i]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
    }

    _hexToPixel(col, row, size) {
      var x = size * 1.5 * col;
      var y = size * Math.sqrt(3) * (row + 0.5 * (col & 1));
      return { x: x, y: y };
    }

    _pixelToHex(px, py, size) {
      var col = (px) / (size * 1.5);
      var row = (py / (size * Math.sqrt(3))) - 0.5 * (Math.round(col) & 1);
      return { col: Math.round(col), row: Math.round(row) };
    }

    drawHexGrid(grid, cellSize) {
      var ctx = this.contexts['terrain'];
      var canvas = this.layers['terrain'];
      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      this._applyViewport(ctx);

      var cols = grid.cols || 0;
      var rows = grid.rows || 0;
      var cells = grid.cells || [];

      ctx.strokeStyle = 'rgba(255,255,255,0.1)';
      ctx.lineWidth = 1;

      for (var c = 0; c < cols; c++) {
        for (var r = 0; r < rows; r++) {
          var pos = this._hexToPixel(c, r, cellSize);
          this._hexPath(ctx, pos.x, pos.y, cellSize);
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    drawTerritories(territorySystem, cellSize) {
      var ctx = this.contexts['territory'];
      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      this._applyViewport(ctx);

      var cells = territorySystem.cells || [];
      for (var i = 0; i < cells.length; i++) {
        var cell = cells[i];
        var col = cell.col !== undefined ? cell.col : (cell.x || 0);
        var row = cell.row !== undefined ? cell.row : (cell.y || 0);
        var ownerColor = null;
        if (typeof territorySystem.getOwnerColor === 'function') {
          ownerColor = territorySystem.getOwnerColor(cell);
        } else if (cell.ownerColor) {
          ownerColor = cell.ownerColor;
        }
        if (!ownerColor) continue;

        var pos = this._hexToPixel(col, row, cellSize);

        ctx.globalAlpha = 0.6;
        this._hexPath(ctx, pos.x, pos.y, cellSize);
        ctx.fillStyle = ownerColor;
        ctx.fill();

        var neighbors = cell.neighbors || [];
        for (var n = 0; n < neighbors.length; n++) {
          var nb = neighbors[n];
          var nbColor = null;
          if (typeof territorySystem.getOwnerColor === 'function') {
            nbColor = territorySystem.getOwnerColor(nb);
          } else if (nb.ownerColor) {
            nbColor = nb.ownerColor;
          }
          if (nbColor !== ownerColor) {
            ctx.globalAlpha = 1.0;
            ctx.strokeStyle = ownerColor;
            ctx.lineWidth = 3;
            this._hexPath(ctx, pos.x, pos.y, cellSize);
            ctx.stroke();
            break;
          }
        }
      }
      ctx.globalAlpha = 1.0;
      ctx.restore();
    }

    drawTerrain(noiseEngine, cellSize) {
      var ctx = this.contexts['terrain'];
      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      this._applyViewport(ctx);

      var cols = noiseEngine.cols || 40;
      var rows = noiseEngine.rows || 30;

      for (var c = 0; c < cols; c++) {
        for (var r = 0; r < rows; r++) {
          var h = 0.5;
          if (typeof noiseEngine.getValue === 'function') {
            h = noiseEngine.getValue(c, r);
          } else if (noiseEngine.grid && noiseEngine.grid[c]) {
            h = noiseEngine.grid[c][r] || 0.5;
          }
          h = clamp(h, 0, 1);
          var color;
          if (h < 0.15) {
            color = lerpColor(TERRAIN_COLORS.deepWater, TERRAIN_COLORS.water, h / 0.15);
          } else if (h < 0.3) {
            color = lerpColor(TERRAIN_COLORS.water, TERRAIN_COLORS.shallowWater, (h - 0.15) / 0.15);
          } else if (h < 0.35) {
            color = lerpColor(TERRAIN_COLORS.shallowWater, TERRAIN_COLORS.sand, (h - 0.3) / 0.05);
          } else if (h < 0.5) {
            color = lerpColor(TERRAIN_COLORS.sand, TERRAIN_COLORS.lowland, (h - 0.35) / 0.15);
          } else if (h < 0.6) {
            color = lerpColor(TERRAIN_COLORS.lowland, TERRAIN_COLORS.grassland, (h - 0.5) / 0.1);
          } else if (h < 0.7) {
            color = lerpColor(TERRAIN_COLORS.grassland, TERRAIN_COLORS.highland, (h - 0.6) / 0.1);
          } else if (h < 0.82) {
            color = lerpColor(TERRAIN_COLORS.highland, TERRAIN_COLORS.mountain, (h - 0.7) / 0.12);
          } else if (h < 0.92) {
            color = lerpColor(TERRAIN_COLORS.mountain, TERRAIN_COLORS.peak, (h - 0.82) / 0.1);
          } else {
            color = lerpColor(TERRAIN_COLORS.peak, TERRAIN_COLORS.snow, (h - 0.92) / 0.08);
          }

          var pos = this._hexToPixel(c, r, cellSize);
          this._hexPath(ctx, pos.x, pos.y, cellSize);
          ctx.fillStyle = color;
          ctx.fill();
          ctx.strokeStyle = 'rgba(0,0,0,0.08)';
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    _lerpColor(a, b, t) {
      return lerpColor(a, b, t);
    }

    drawParticles(particleSystem) {
      var ctx = this.contexts['effects'];
      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      this._applyViewport(ctx);

      var particles = particleSystem.particles || [];
      for (var i = 0; i < particles.length; i++) {
        var p = particles[i];
        var alpha = clamp(p.life !== undefined ? p.life : 1, 0, 1);
        var size = p.size || 3;
        var color = p.color || '#ffffff';

        ctx.beginPath();
        ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.globalAlpha = alpha;
        ctx.fill();

        if (p.trail && p.prevX !== undefined) {
          ctx.beginPath();
          ctx.moveTo(p.prevX, p.prevY);
          ctx.lineTo(p.x, p.y);
          ctx.strokeStyle = color;
          ctx.lineWidth = size * 0.5;
          ctx.globalAlpha = alpha * 0.4;
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1.0;
      ctx.restore();
    }

    drawMinimap(grid, territorySystem) {
      if (!this.showMinimap) return;
      var ctx = this.contexts['ui'];
      var mSize = this.minimapSize;
      var pad = this.minimapPadding;
      var mx = this.width - mSize - pad;
      var my = pad;

      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.strokeStyle = 'rgba(255,255,255,0.3)';
      ctx.lineWidth = 1;
      ctx.fillRect(mx, my, mSize, mSize);
      ctx.strokeRect(mx, my, mSize, mSize);

      var cols = grid.cols || 1;
      var rows = grid.rows || 1;
      var cw = mSize / cols;
      var ch = mSize / rows;
      var cells = (territorySystem && territorySystem.cells) ? territorySystem.cells : [];

      for (var i = 0; i < cells.length; i++) {
        var cell = cells[i];
        var col = cell.col !== undefined ? cell.col : (cell.x || 0);
        var row = cell.row !== undefined ? cell.row : (cell.y || 0);
        var color = null;
        if (typeof territorySystem.getOwnerColor === 'function') {
          color = territorySystem.getOwnerColor(cell);
        } else if (cell.ownerColor) {
          color = cell.ownerColor;
        }
        if (color) {
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.8;
          ctx.fillRect(mx + col * cw, my + row * ch, cw + 0.5, ch + 0.5);
        }
      }

      ctx.globalAlpha = 1.0;
      var gridW = cols * 30;
      var gridH = rows * 30;
      var vx = (this.viewport.x - this.width / 2 / this.viewport.zoom) / gridW * mSize;
      var vy = (this.viewport.y - this.height / 2 / this.viewport.zoom) / gridH * mSize;
      var vw = (this.width / this.viewport.zoom) / gridW * mSize;
      var vh = (this.height / this.viewport.zoom) / gridH * mSize;
      vx = clamp(vx + mx, mx, mx + mSize - 10);
      vy = clamp(vy + my, my, my + mSize - 10);
      vw = clamp(vw, 10, mSize);
      vh = clamp(vh, 10, mSize);

      ctx.strokeStyle = '#ffdd57';
      ctx.lineWidth = 2;
      ctx.strokeRect(vx, vy, vw, vh);
      ctx.restore();
    }

    pan(dx, dy) {
      this.viewport.x += dx;
      this.viewport.y += dy;
      this.viewport.targetX = this.viewport.x;
      this.viewport.targetY = this.viewport.y;
    }

    zoom(factor) {
      this.viewport.zoom = clamp(this.viewport.zoom * factor, 0.1, 5);
      this.viewport.targetZoom = this.viewport.zoom;
    }

    zoomAt(factor, sx, sy) {
      var worldBefore = this.screenToWorld(sx, sy);
      this.viewport.zoom = clamp(this.viewport.zoom * factor, 0.1, 5);
      this.viewport.targetZoom = this.viewport.zoom;
      var worldAfter = this.screenToWorld(sx, sy);
      this.viewport.x += worldBefore.x - worldAfter.x;
      this.viewport.y += worldBefore.y - worldAfter.y;
      this.viewport.targetX = this.viewport.x;
      this.viewport.targetY = this.viewport.y;
    }

    smoothPanTo(x, y) {
      this.viewport.targetX = x;
      this.viewport.targetY = y;
    }

    smoothZoomTo(z) {
      this.viewport.targetZoom = clamp(z, 0.1, 5);
    }

    screenToWorld(sx, sy) {
      return {
        x: (sx - this.width / 2) / this.viewport.zoom + this.viewport.x,
        y: (sy - this.height / 2) / this.viewport.zoom + this.viewport.y
      };
    }

    worldToScreen(wx, wy) {
      return {
        x: (wx - this.viewport.x) * this.viewport.zoom + this.width / 2,
        y: (wy - this.viewport.y) * this.viewport.zoom + this.height / 2
      };
    }

    queueAnimation(type, params) {
      this.animations.push({
        type: type,
        params: params || {},
        startTime: performance.now(),
        progress: 0
      });
    }

    processAnimations(dt) {
      var ctx = this.contexts['effects'];
      var now = performance.now();
      var remaining = [];

      for (var i = 0; i < this.animations.length; i++) {
        var anim = this.animations[i];
        var elapsed = (now - anim.startTime) / 1000;
        var duration = anim.params.duration || 1;
        anim.progress = clamp(elapsed / duration, 0, 1);

        ctx.save();
        ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
        this._applyViewport(ctx);

        var x = anim.params.x || this.width / 2;
        var y = anim.params.y || this.height / 2;
        var maxRadius = anim.params.radius || 100;
        var color = anim.params.color || '#ffffff';

        if (anim.type === 'expand') {
          var r = maxRadius * anim.progress;
          ctx.beginPath();
          ctx.arc(x, y, r, 0, Math.PI * 2);
          ctx.strokeStyle = color;
          ctx.lineWidth = 3 * (1 - anim.progress);
          ctx.globalAlpha = 1 - anim.progress;
          ctx.stroke();
        } else if (anim.type === 'fade') {
          ctx.beginPath();
          ctx.arc(x, y, maxRadius, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.globalAlpha = (1 - anim.progress) * 0.6;
          ctx.fill();
        } else if (anim.type === 'flash') {
          ctx.fillStyle = color;
          ctx.globalAlpha = (1 - anim.progress) * 0.3;
          ctx.fillRect(-10000, -10000, 20000, 20000);
        } else if (anim.type === 'ripple') {
          var rings = anim.params.rings || 3;
          for (var ri = 0; ri < rings; ri++) {
            var offset = ri / rings;
            var rp = (anim.progress + offset) % 1;
            var rr = maxRadius * rp;
            ctx.beginPath();
            ctx.arc(x, y, rr, 0, Math.PI * 2);
            ctx.strokeStyle = color;
            ctx.lineWidth = 2 * (1 - rp);
            ctx.globalAlpha = (1 - rp) * 0.7;
            ctx.stroke();
          }
        } else if (anim.type === 'trail') {
          var tx = anim.params.toX || x + 100;
          var ty = anim.params.toY || y;
          var cp = anim.progress;
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(x + (tx - x) * cp, y + (ty - y) * cp);
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.globalAlpha = 1 - cp * 0.5;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(x + (tx - x) * cp, y + (ty - y) * cp, 4, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
        }

        ctx.globalAlpha = 1;
        ctx.restore();

        if (anim.progress < 1) {
          remaining.push(anim);
        }
      }
      this.animations = remaining;
    }

    _applyViewport(ctx) {
      ctx.translate(this.width / 2, this.height / 2);
      ctx.scale(this.viewport.zoom, this.viewport.zoom);
      ctx.translate(-this.viewport.x, -this.viewport.y);
    }

    _updateViewport() {
      var ease = 0.1;
      this.viewport.x += (this.viewport.targetX - this.viewport.x) * ease;
      this.viewport.y += (this.viewport.targetY - this.viewport.y) * ease;
      var zoomDiff = this.viewport.targetZoom - this.viewport.zoom;
      if (Math.abs(zoomDiff) > 0.001) {
        this.viewport.zoom += zoomDiff * ease;
      }
    }

    render(dt) {
      this._updateViewport();
      if (this.animations.length > 0) {
        this.processAnimations(dt || 16);
      }
      this.frameCount++;
      var now = performance.now();
      if (now - this.lastFrameTime >= 1000) {
        this.fps = this.frameCount;
        this.frameCount = 0;
        this.lastFrameTime = now;
      }
    }

    _bindEvents() {
      var topCanvas = this.layers['ui'];
      topCanvas.addEventListener('mousedown', this._boundMouseDown);
      window.addEventListener('mousemove', this._boundMouseMove);
      window.addEventListener('mouseup', this._boundMouseUp);
      topCanvas.addEventListener('wheel', this._boundWheel, { passive: false });
      window.addEventListener('resize', this._boundResize);
      topCanvas.addEventListener('touchstart', this._boundTouchStart, { passive: false });
      topCanvas.addEventListener('touchmove', this._boundTouchMove, { passive: false });
      topCanvas.addEventListener('touchend', this._boundTouchEnd);
    }

    _onMouseDown(e) {
      this.isDragging = true;
      this.lastMouse.x = e.clientX;
      this.lastMouse.y = e.clientY;
    }

    _onMouseMove(e) {
      if (!this.isDragging) return;
      var dx = (e.clientX - this.lastMouse.x) / this.viewport.zoom;
      var dy = (e.clientY - this.lastMouse.y) / this.viewport.zoom;
      this.pan(-dx, -dy);
      this.lastMouse.x = e.clientX;
      this.lastMouse.y = e.clientY;
    }

    _onMouseUp() {
      this.isDragging = false;
    }

    _onWheel(e) {
      e.preventDefault();
      var factor = e.deltaY > 0 ? 0.9 : 1.1;
      var rect = this.container.getBoundingClientRect();
      this.zoomAt(factor, e.clientX - rect.left, e.clientY - rect.top);
    }

    _onTouchStart(e) {
      if (e.touches.length === 1) {
        e.preventDefault();
        this.isDragging = true;
        this.lastMouse.x = e.touches[0].clientX;
        this.lastMouse.y = e.touches[0].clientY;
      } else if (e.touches.length === 2) {
        e.preventDefault();
        this.isDragging = false;
        var dx = e.touches[0].clientX - e.touches[1].clientX;
        var dy = e.touches[0].clientY - e.touches[1].clientY;
        this.pinchStartDist = Math.sqrt(dx * dx + dy * dy);
      }
    }

    _onTouchMove(e) {
      if (e.touches.length === 1 && this.isDragging) {
        e.preventDefault();
        var dx = (e.touches[0].clientX - this.lastMouse.x) / this.viewport.zoom;
        var dy = (e.touches[0].clientY - this.lastMouse.y) / this.viewport.zoom;
        this.pan(-dx, -dy);
        this.lastMouse.x = e.touches[0].clientX;
        this.lastMouse.y = e.touches[0].clientY;
      } else if (e.touches.length === 2) {
        e.preventDefault();
        var tdx = e.touches[0].clientX - e.touches[1].clientX;
        var tdy = e.touches[0].clientY - e.touches[1].clientY;
        var dist = Math.sqrt(tdx * tdx + tdy * tdy);
        if (this.pinchStartDist > 0) {
          var factor = dist / this.pinchStartDist;
          this.zoom(factor);
          this.pinchStartDist = dist;
        }
      }
    }

    _onTouchEnd(e) {
      if (e.touches.length === 0) {
        this.isDragging = false;
      }
      this.pinchStartDist = 0;
    }

    clear() {
      for (var li = 0; li < LAYERS.length; li++) {
        var name = LAYERS[li];
        var ctx = this.contexts[name];
        ctx.save();
        ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
        ctx.clearRect(0, 0, this.width, this.height);
        ctx.restore();
      }
    }

    clearLayer(name) {
      var ctx = this.contexts[name];
      if (!ctx) return;
      ctx.save();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.restore();
    }

    destroy() {
      var topCanvas = this.layers['ui'];
      topCanvas.removeEventListener('mousedown', this._boundMouseDown);
      window.removeEventListener('mousemove', this._boundMouseMove);
      window.removeEventListener('mouseup', this._boundMouseUp);
      topCanvas.removeEventListener('wheel', this._boundWheel);
      window.removeEventListener('resize', this._boundResize);
      topCanvas.removeEventListener('touchstart', this._boundTouchStart);
      topCanvas.removeEventListener('touchmove', this._boundTouchMove);
      topCanvas.removeEventListener('touchend', this._boundTouchEnd);
      for (var li = 0; li < LAYERS.length; li++) {
        var canvas = this.layers[LAYERS[li]];
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      }
      this.layers = {};
      this.contexts = {};
      this.animations = [];
    }
  }

  window.CT = window.CT || {};
  window.CT.CanvasRenderer = CanvasRenderer;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 2. UI System
# ---------------------------------------------------------------------------


@register("ui_system")
def generate_ui_system(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var TOAST_COLORS = {
    info:    { bg: '#2196F3', icon: '\u2139' },
    success: { bg: '#4CAF50', icon: '\u2713' },
    warning: { bg: '#FF9800', icon: '\u26A0' },
    error:   { bg: '#F44336', icon: '\u2716' }
  };

  var Z_PANEL = 1000;
  var Z_MODAL = 5000;
  var Z_TOOLTIP = 6000;
  var Z_TOAST = 7000;
  var Z_CONTEXT = 4000;

  class UISystem {
    constructor() {
      this.panels = {};
      this.activeModal = null;
      this.tooltipEl = null;
      this.toastContainer = null;
      this.contextMenu = null;
      this._dragState = null;
      this._nextPanelZ = Z_PANEL;
      this._init();
    }

    _init() {
      this.tooltipEl = document.createElement('div');
      this.tooltipEl.className = 'ct-tooltip';
      this.tooltipEl.style.cssText = 'position:fixed;padding:6px 10px;background:rgba(0,0,0,0.85);' +
        'color:#fff;border-radius:4px;font-size:12px;pointer-events:none;z-index:' + Z_TOOLTIP +
        ';display:none;max-width:250px;word-wrap:break-word;transition:opacity 0.15s;';
      document.body.appendChild(this.tooltipEl);

      this.toastContainer = document.createElement('div');
      this.toastContainer.className = 'ct-toast-container';
      this.toastContainer.style.cssText = 'position:fixed;top:16px;right:16px;z-index:' + Z_TOAST +
        ';display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(this.toastContainer);

      this._initTooltips();
    }

    createPanel(id, title, content, options) {
      if (this.panels[id]) this.destroyPanel(id);
      options = options || {};
      var panel = document.createElement('div');
      panel.id = 'ct-panel-' + id;
      panel.className = 'ct-panel';
      this._nextPanelZ++;
      panel.style.cssText = 'position:fixed;background:#1a1a2e;border:1px solid rgba(255,255,255,0.12);' +
        'border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.5);overflow:hidden;z-index:' +
        this._nextPanelZ + ';color:#e0e0e0;font-family:sans-serif;' +
        'left:' + (options.x || 40) + 'px;top:' + (options.y || 40) + 'px;' +
        'width:' + (options.width || 320) + 'px;' +
        (options.height ? ('height:' + options.height + 'px;') : '');

      var header = document.createElement('div');
      header.className = 'ct-panel-header';
      header.style.cssText = 'padding:8px 12px;background:#16213e;display:flex;align-items:center;' +
        'justify-content:space-between;cursor:' + (options.draggable !== false ? 'move' : 'default') +
        ';user-select:none;border-bottom:1px solid rgba(255,255,255,0.08);';

      var titleEl = document.createElement('span');
      titleEl.className = 'ct-panel-title';
      titleEl.textContent = title;
      titleEl.style.cssText = 'font-weight:600;font-size:13px;';
      header.appendChild(titleEl);

      if (options.closable !== false) {
        var closeBtn = document.createElement('button');
        closeBtn.className = 'ct-panel-close';
        closeBtn.textContent = '\u00D7';
        closeBtn.style.cssText = 'background:none;border:none;color:#aaa;font-size:18px;cursor:pointer;' +
          'padding:0 4px;line-height:1;';
        closeBtn.addEventListener('mouseover', function() { closeBtn.style.color = '#fff'; });
        closeBtn.addEventListener('mouseout', function() { closeBtn.style.color = '#aaa'; });
        var self = this;
        closeBtn.addEventListener('click', function() { self.destroyPanel(id); });
        header.appendChild(closeBtn);
      }

      panel.appendChild(header);

      var body = document.createElement('div');
      body.className = 'ct-panel-body';
      body.style.cssText = 'padding:12px;overflow-y:auto;max-height:' +
        ((options.height || 400) - 40) + 'px;';
      if (typeof content === 'string') {
        body.innerHTML = content;
      } else if (content instanceof HTMLElement) {
        body.appendChild(content);
      }
      panel.appendChild(body);

      document.body.appendChild(panel);
      this.panels[id] = { el: panel, header: header, body: body, visible: true };

      if (options.draggable !== false) {
        this._initDrag(panel, header);
      }
      panel.addEventListener('mousedown', function() {
        self._nextPanelZ++;
        panel.style.zIndex = self._nextPanelZ;
      });

      return panel;
    }

    destroyPanel(id) {
      var p = this.panels[id];
      if (!p) return;
      if (p.el.parentNode) p.el.parentNode.removeChild(p.el);
      delete this.panels[id];
    }

    togglePanel(id) {
      var p = this.panels[id];
      if (!p) return;
      p.visible = !p.visible;
      p.el.style.display = p.visible ? '' : 'none';
    }

    _initDrag(panel, header) {
      var self = this;
      var startX, startY, origLeft, origTop;
      var moving = false;

      function onDown(e) {
        e.preventDefault();
        moving = true;
        var clientX = e.clientX !== undefined ? e.clientX : e.touches[0].clientX;
        var clientY = e.clientY !== undefined ? e.clientY : e.touches[0].clientY;
        startX = clientX;
        startY = clientY;
        origLeft = panel.offsetLeft;
        origTop = panel.offsetTop;
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        window.addEventListener('touchmove', onMove, { passive: false });
        window.addEventListener('touchend', onUp);
      }

      function onMove(e) {
        if (!moving) return;
        e.preventDefault();
        var clientX = e.clientX !== undefined ? e.clientX : e.touches[0].clientX;
        var clientY = e.clientY !== undefined ? e.clientY : e.touches[0].clientY;
        panel.style.left = (origLeft + clientX - startX) + 'px';
        panel.style.top = (origTop + clientY - startY) + 'px';
      }

      function onUp() {
        moving = false;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        window.removeEventListener('touchmove', onMove);
        window.removeEventListener('touchend', onUp);
      }

      header.addEventListener('mousedown', onDown);
      header.addEventListener('touchstart', onDown, { passive: false });
    }

    showModal(title, content) {
      this.closeModal();
      var backdrop = document.createElement('div');
      backdrop.className = 'ct-modal-backdrop';
      backdrop.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;' +
        'background:rgba(0,0,0,0.6);z-index:' + Z_MODAL + ';display:flex;align-items:center;' +
        'justify-content:center;opacity:0;transition:opacity 0.2s;';

      var modal = document.createElement('div');
      modal.className = 'ct-modal';
      modal.style.cssText = 'background:#1a1a2e;border-radius:12px;padding:0;max-width:560px;' +
        'width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 16px 48px rgba(0,0,0,0.6);' +
        'transform:scale(0.9);transition:transform 0.2s;';

      var mHeader = document.createElement('div');
      mHeader.style.cssText = 'padding:16px 20px;border-bottom:1px solid rgba(255,255,255,0.08);' +
        'display:flex;justify-content:space-between;align-items:center;';
      var mTitle = document.createElement('h3');
      mTitle.textContent = title;
      mTitle.style.cssText = 'margin:0;color:#fff;font-size:16px;';
      mHeader.appendChild(mTitle);

      var mClose = document.createElement('button');
      mClose.textContent = '\u00D7';
      mClose.style.cssText = 'background:none;border:none;color:#aaa;font-size:22px;cursor:pointer;';
      var self = this;
      mClose.addEventListener('click', function() { self.closeModal(); });
      mHeader.appendChild(mClose);

      var mBody = document.createElement('div');
      mBody.style.cssText = 'padding:20px;color:#ccc;font-size:14px;line-height:1.6;';
      if (typeof content === 'string') {
        mBody.innerHTML = content;
      } else if (content instanceof HTMLElement) {
        mBody.appendChild(content);
      }

      modal.appendChild(mHeader);
      modal.appendChild(mBody);
      backdrop.appendChild(modal);
      document.body.appendChild(backdrop);

      backdrop.addEventListener('click', function(e) {
        if (e.target === backdrop) self.closeModal();
      });

      this.activeModal = backdrop;
      requestAnimationFrame(function() {
        backdrop.style.opacity = '1';
        modal.style.transform = 'scale(1)';
      });
    }

    closeModal() {
      if (!this.activeModal) return;
      var el = this.activeModal;
      el.style.opacity = '0';
      this.activeModal = null;
      setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
    }

    _initTooltips() {
      var self = this;
      document.addEventListener('mouseover', function(e) {
        var target = e.target;
        var text = target.getAttribute ? target.getAttribute('data-tooltip') : null;
        if (!text) {
          self.tooltipEl.style.display = 'none';
          return;
        }
        self.tooltipEl.textContent = text;
        self.tooltipEl.style.display = 'block';
        var rect = target.getBoundingClientRect();
        var ttW = self.tooltipEl.offsetWidth;
        var left = rect.left + rect.width / 2 - ttW / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - ttW - 8));
        self.tooltipEl.style.left = left + 'px';
        self.tooltipEl.style.top = (rect.top - self.tooltipEl.offsetHeight - 6) + 'px';
      });
      document.addEventListener('mouseout', function(e) {
        if (e.target.getAttribute && e.target.getAttribute('data-tooltip')) {
          self.tooltipEl.style.display = 'none';
        }
      });
    }

    showToast(msg, type, duration) {
      type = type || 'info';
      duration = duration || 3500;
      var cfg = TOAST_COLORS[type] || TOAST_COLORS.info;

      var toast = document.createElement('div');
      toast.className = 'ct-toast ct-toast-' + type;
      toast.style.cssText = 'padding:10px 16px;border-radius:6px;color:#fff;font-size:13px;' +
        'display:flex;align-items:center;gap:8px;pointer-events:auto;cursor:pointer;' +
        'box-shadow:0 4px 12px rgba(0,0,0,0.4);opacity:0;transform:translateX(40px);' +
        'transition:opacity 0.3s,transform 0.3s;background:' + cfg.bg + ';';

      var icon = document.createElement('span');
      icon.textContent = cfg.icon;
      icon.style.fontSize = '16px';
      toast.appendChild(icon);

      var text = document.createElement('span');
      text.textContent = msg;
      toast.appendChild(text);

      this.toastContainer.appendChild(toast);

      requestAnimationFrame(function() {
        toast.style.opacity = '1';
        toast.style.transform = 'translateX(0)';
      });

      var self = this;
      var timer = setTimeout(function() { removeToast(); }, duration);
      toast.addEventListener('click', function() { clearTimeout(timer); removeToast(); });

      function removeToast() {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(40px)';
        setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
      }
    }

    updateHUD(data) {
      var fields = [
        { sel: '.ct-hud-turn', key: 'turn', prefix: 'Turn ' },
        { sel: '.ct-hud-player', key: 'player', prefix: '' },
        { sel: '.ct-hud-chromaticity', key: 'chromaticity', prefix: 'Chromaticity: ' },
        { sel: '.ct-hud-score', key: 'score', prefix: 'Score: ' },
        { sel: '.ct-hud-territories', key: 'territories', prefix: 'Territories: ' },
        { sel: '.ct-hud-phase', key: 'phase', prefix: '' }
      ];
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        if (data[f.key] !== undefined) {
          var el = document.querySelector(f.sel);
          if (el) el.textContent = f.prefix + data[f.key];
        }
      }
      var timerEl = document.querySelector('.ct-hud-timer');
      if (timerEl && data.timeLeft !== undefined) {
        var mins = Math.floor(data.timeLeft / 60);
        var secs = data.timeLeft % 60;
        timerEl.textContent = mins + ':' + (secs < 10 ? '0' : '') + secs;
      }
    }

    renderPaletteSelector(palettes, onSelect) {
      var container = document.querySelector('.ct-palette-selector');
      if (!container) return;
      container.innerHTML = '';
      container.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;padding:8px;';

      for (var pi = 0; pi < palettes.length; pi++) {
        (function(palette, idx) {
          var swatch = document.createElement('div');
          swatch.className = 'ct-palette-swatch';
          swatch.style.cssText = 'display:flex;border-radius:6px;overflow:hidden;cursor:pointer;' +
            'box-shadow:0 2px 8px rgba(0,0,0,0.3);transition:transform 0.15s;height:32px;';
          swatch.setAttribute('data-tooltip', palette.name || ('Palette ' + (idx + 1)));

          var colors = palette.colors || palette;
          for (var ci = 0; ci < colors.length; ci++) {
            var colorBlock = document.createElement('div');
            colorBlock.style.cssText = 'width:24px;height:100%;background:' + colors[ci] + ';';
            swatch.appendChild(colorBlock);
          }

          swatch.addEventListener('mouseover', function() { swatch.style.transform = 'scale(1.1)'; });
          swatch.addEventListener('mouseout', function() { swatch.style.transform = 'scale(1)'; });
          swatch.addEventListener('click', function() {
            var prev = container.querySelector('.ct-palette-active');
            if (prev) prev.style.outline = 'none';
            swatch.style.outline = '2px solid #fff';
            swatch.classList.add('ct-palette-active');
            if (onSelect) onSelect(palette, idx);
          });

          container.appendChild(swatch);
        })(palettes[pi], pi);
      }
    }

    showTerritoryInfo(cell, territory) {
      var panel = document.querySelector('.ct-territory-info');
      if (!panel) {
        panel = document.createElement('div');
        panel.className = 'ct-territory-info';
        panel.style.cssText = 'position:fixed;bottom:16px;left:16px;background:rgba(26,26,46,0.95);' +
          'border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:12px 16px;' +
          'color:#e0e0e0;font-size:12px;z-index:' + (Z_PANEL + 50) + ';min-width:200px;';
        document.body.appendChild(panel);
      }
      var html = '<div style="font-weight:600;font-size:14px;margin-bottom:8px;">' +
        (territory.name || 'Territory') + '</div>';
      html += '<div>Cell: (' + (cell.col || cell.x || 0) + ', ' + (cell.row || cell.y || 0) + ')</div>';
      if (territory.owner) html += '<div>Owner: ' + territory.owner + '</div>';
      if (territory.color) {
        html += '<div>Color: <span style="display:inline-block;width:12px;height:12px;' +
          'border-radius:2px;background:' + territory.color + ';vertical-align:middle;"></span> ' +
          territory.color + '</div>';
      }
      if (territory.strength !== undefined) html += '<div>Strength: ' + territory.strength + '</div>';
      if (territory.size !== undefined) html += '<div>Size: ' + territory.size + ' cells</div>';
      if (territory.chromaticity !== undefined) html += '<div>Chromaticity: ' + territory.chromaticity.toFixed(2) + '</div>';
      panel.innerHTML = html;
      panel.style.display = 'block';
    }

    showContextMenu(x, y, items) {
      this._closeContextMenu();
      var menu = document.createElement('div');
      menu.className = 'ct-context-menu';
      menu.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;background:#1a1a2e;' +
        'border:1px solid rgba(255,255,255,0.12);border-radius:6px;padding:4px 0;z-index:' +
        Z_CONTEXT + ';min-width:160px;box-shadow:0 8px 24px rgba(0,0,0,0.5);';

      var self = this;
      for (var i = 0; i < items.length; i++) {
        (function(item) {
          if (item.separator) {
            var sep = document.createElement('div');
            sep.style.cssText = 'height:1px;background:rgba(255,255,255,0.08);margin:4px 0;';
            menu.appendChild(sep);
            return;
          }
          var row = document.createElement('div');
          row.className = 'ct-context-item';
          row.textContent = item.label || '';
          var disabled = item.disabled || false;
          row.style.cssText = 'padding:6px 14px;font-size:13px;cursor:' +
            (disabled ? 'default' : 'pointer') + ';color:' +
            (disabled ? '#666' : '#ddd') + ';transition:background 0.1s;';
          if (!disabled) {
            row.addEventListener('mouseover', function() { row.style.background = 'rgba(255,255,255,0.07)'; });
            row.addEventListener('mouseout', function() { row.style.background = 'none'; });
            row.addEventListener('click', function() {
              self._closeContextMenu();
              if (item.action) item.action();
            });
          }
          menu.appendChild(row);
        })(items[i]);
      }

      document.body.appendChild(menu);
      this.contextMenu = menu;

      var closeOnClick = function(e) {
        if (!menu.contains(e.target)) {
          self._closeContextMenu();
          document.removeEventListener('click', closeOnClick);
        }
      };
      setTimeout(function() { document.addEventListener('click', closeOnClick); }, 0);

      var rect = menu.getBoundingClientRect();
      if (rect.right > window.innerWidth) menu.style.left = (x - rect.width) + 'px';
      if (rect.bottom > window.innerHeight) menu.style.top = (y - rect.height) + 'px';
    }

    _closeContextMenu() {
      if (this.contextMenu && this.contextMenu.parentNode) {
        this.contextMenu.parentNode.removeChild(this.contextMenu);
      }
      this.contextMenu = null;
    }

    renderSettings(settings, onChange) {
      var container = document.createElement('div');
      container.className = 'ct-settings';
      container.style.cssText = 'display:flex;flex-direction:column;gap:12px;';

      var keys = Object.keys(settings);
      for (var ki = 0; ki < keys.length; ki++) {
        (function(key) {
          var setting = settings[key];
          var row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;';

          var label = document.createElement('label');
          label.textContent = setting.label || key;
          label.style.cssText = 'font-size:13px;color:#ccc;min-width:120px;';
          row.appendChild(label);

          var input;
          if (setting.type === 'toggle' || setting.type === 'boolean') {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = !!setting.value;
            input.style.cssText = 'width:18px;height:18px;cursor:pointer;accent-color:#4fc3f7;';
            input.addEventListener('change', function() { if (onChange) onChange(key, input.checked); });
          } else if (setting.type === 'slider' || setting.type === 'range') {
            var wrapper = document.createElement('div');
            wrapper.style.cssText = 'display:flex;align-items:center;gap:8px;flex:1;';
            input = document.createElement('input');
            input.type = 'range';
            input.min = setting.min !== undefined ? setting.min : 0;
            input.max = setting.max !== undefined ? setting.max : 100;
            input.step = setting.step || 1;
            input.value = setting.value || 0;
            input.style.cssText = 'flex:1;accent-color:#4fc3f7;';
            var valDisplay = document.createElement('span');
            valDisplay.textContent = input.value;
            valDisplay.style.cssText = 'font-size:12px;color:#aaa;min-width:32px;text-align:right;';
            input.addEventListener('input', function() {
              valDisplay.textContent = input.value;
              if (onChange) onChange(key, parseFloat(input.value));
            });
            wrapper.appendChild(input);
            wrapper.appendChild(valDisplay);
            row.appendChild(label);
            row.appendChild(wrapper);
            container.appendChild(row);
            return;
          } else if (setting.type === 'select' || setting.type === 'dropdown') {
            input = document.createElement('select');
            input.style.cssText = 'background:#16213e;color:#ccc;border:1px solid rgba(255,255,255,0.15);' +
              'border-radius:4px;padding:4px 8px;font-size:13px;';
            var opts = setting.options || [];
            for (var oi = 0; oi < opts.length; oi++) {
              var opt = document.createElement('option');
              if (typeof opts[oi] === 'object') {
                opt.value = opts[oi].value;
                opt.textContent = opts[oi].label;
              } else {
                opt.value = opts[oi];
                opt.textContent = opts[oi];
              }
              if (opt.value === String(setting.value)) opt.selected = true;
              input.appendChild(opt);
            }
            input.addEventListener('change', function() { if (onChange) onChange(key, input.value); });
          } else {
            input = document.createElement('input');
            input.type = 'text';
            input.value = setting.value || '';
            input.style.cssText = 'background:#16213e;color:#ccc;border:1px solid rgba(255,255,255,0.15);' +
              'border-radius:4px;padding:4px 8px;font-size:13px;flex:1;';
            input.addEventListener('change', function() { if (onChange) onChange(key, input.value); });
          }

          row.appendChild(input);
          container.appendChild(row);
        })(keys[ki]);
      }

      return container;
    }
  }

  window.CT = window.CT || {};
  window.CT.UISystem = UISystem;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 3. Gallery
# ---------------------------------------------------------------------------


@register("gallery")
def generate_gallery(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var GALLERY_KEY = 'ct_gallery_items';

  class Gallery {
    constructor(dataLayer) {
      this.dataLayer = dataLayer || null;
      this.items = [];
      this.sortBy = 'date';
      this.filterQuery = '';
      this._nextId = 1;
    }

    capture(canvas) {
      if (!canvas || typeof canvas.toDataURL !== 'function') return null;
      try {
        return canvas.toDataURL('image/png');
      } catch (e) {
        return null;
      }
    }

    createThumbnail(dataUrl, size) {
      size = size || 160;
      return new Promise(function(resolve) {
        var img = new Image();
        img.onload = function() {
          var c = document.createElement('canvas');
          c.width = size;
          c.height = size;
          var ctx = c.getContext('2d');
          var aspect = img.width / img.height;
          var sw, sh, sx, sy;
          if (aspect > 1) {
            sh = img.height;
            sw = img.height;
            sx = (img.width - sw) / 2;
            sy = 0;
          } else {
            sw = img.width;
            sh = img.width;
            sx = 0;
            sy = (img.height - sh) / 2;
          }
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, size, size);
          resolve(c.toDataURL('image/jpeg', 0.8));
        };
        img.onerror = function() { resolve(null); };
        img.src = dataUrl;
      });
    }

    save(item) {
      var record = {
        id: item.id || ('gallery_' + Date.now() + '_' + this._nextId++),
        dataUrl: item.dataUrl || '',
        thumbnail: item.thumbnail || '',
        date: item.date || new Date().toISOString(),
        score: item.score || 0,
        title: item.title || 'Untitled',
        metadata: item.metadata || {}
      };
      this.items.push(record);
      this._persist();
      return record;
    }

    load() {
      if (this.dataLayer && typeof this.dataLayer.get === 'function') {
        var raw = this.dataLayer.get(GALLERY_KEY);
        this.items = raw ? JSON.parse(raw) : [];
      } else {
        try {
          var stored = localStorage.getItem(GALLERY_KEY);
          this.items = stored ? JSON.parse(stored) : [];
        } catch (e) {
          this.items = [];
        }
      }
      if (this.items.length > 0) {
        var maxId = 0;
        for (var i = 0; i < this.items.length; i++) {
          var numPart = parseInt((this.items[i].id || '').split('_').pop(), 10);
          if (numPart > maxId) maxId = numPart;
        }
        this._nextId = maxId + 1;
      }
      return this.items;
    }

    delete(id) {
      this.items = this.items.filter(function(it) { return it.id !== id; });
      this._persist();
    }

    _persist() {
      var data = JSON.stringify(this.items);
      if (this.dataLayer && typeof this.dataLayer.set === 'function') {
        this.dataLayer.set(GALLERY_KEY, data);
      } else {
        try { localStorage.setItem(GALLERY_KEY, data); } catch (e) { /* quota */ }
      }
    }

    renderGalleryGrid(container) {
      if (!container) return;
      container.innerHTML = '';
      container.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));' +
        'gap:12px;padding:12px;';

      var items = this._getFilteredSorted();
      if (items.length === 0) {
        var empty = document.createElement('div');
        empty.style.cssText = 'grid-column:1/-1;text-align:center;color:#888;padding:40px;font-size:14px;';
        empty.textContent = 'No gallery items yet. Capture your first composition!';
        container.appendChild(empty);
        return;
      }

      var self = this;
      for (var i = 0; i < items.length; i++) {
        (function(item) {
          var card = document.createElement('div');
          card.className = 'ct-gallery-card';
          card.style.cssText = 'background:#1a1a2e;border-radius:8px;overflow:hidden;cursor:pointer;' +
            'border:1px solid rgba(255,255,255,0.08);transition:transform 0.15s,box-shadow 0.15s;';

          var imgWrap = document.createElement('div');
          imgWrap.style.cssText = 'width:100%;aspect-ratio:1;overflow:hidden;background:#111;';
          var img = document.createElement('img');
          img.src = item.thumbnail || item.dataUrl;
          img.style.cssText = 'width:100%;height:100%;object-fit:cover;';
          img.alt = item.title;
          imgWrap.appendChild(img);
          card.appendChild(imgWrap);

          var info = document.createElement('div');
          info.style.cssText = 'padding:8px 10px;';

          var titleEl = document.createElement('div');
          titleEl.textContent = item.title;
          titleEl.style.cssText = 'font-size:13px;font-weight:600;color:#e0e0e0;white-space:nowrap;' +
            'overflow:hidden;text-overflow:ellipsis;';
          info.appendChild(titleEl);

          var meta = document.createElement('div');
          meta.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-top:4px;';
          var dateEl = document.createElement('span');
          dateEl.style.cssText = 'font-size:11px;color:#888;';
          var d = new Date(item.date);
          dateEl.textContent = d.toLocaleDateString();
          meta.appendChild(dateEl);

          if (item.score) {
            var badge = document.createElement('span');
            badge.style.cssText = 'font-size:11px;background:#4fc3f7;color:#000;padding:1px 6px;' +
              'border-radius:10px;font-weight:600;';
            badge.textContent = item.score;
            meta.appendChild(badge);
          }
          info.appendChild(meta);
          card.appendChild(info);

          card.addEventListener('mouseover', function() {
            card.style.transform = 'translateY(-2px)';
            card.style.boxShadow = '0 8px 24px rgba(0,0,0,0.4)';
          });
          card.addEventListener('mouseout', function() {
            card.style.transform = 'none';
            card.style.boxShadow = 'none';
          });
          card.addEventListener('click', function() { self.showDetail(item); });

          container.appendChild(card);
        })(items[i]);
      }
    }

    showDetail(item) {
      var CT = window.CT || {};
      var ui = CT.UISystem ? new CT.UISystem() : null;

      var content = document.createElement('div');
      content.style.cssText = 'text-align:center;';

      var img = document.createElement('img');
      img.src = item.dataUrl || item.thumbnail;
      img.style.cssText = 'max-width:100%;border-radius:6px;margin-bottom:12px;';
      img.alt = item.title;
      content.appendChild(img);

      var title = document.createElement('h3');
      title.textContent = item.title;
      title.style.cssText = 'margin:0 0 8px;color:#fff;';
      content.appendChild(title);

      var metaDiv = document.createElement('div');
      metaDiv.style.cssText = 'font-size:13px;color:#aaa;margin-bottom:12px;';
      metaDiv.textContent = 'Date: ' + new Date(item.date).toLocaleString();
      if (item.score) metaDiv.textContent += ' | Score: ' + item.score;
      content.appendChild(metaDiv);

      if (item.metadata && Object.keys(item.metadata).length > 0) {
        var metaList = document.createElement('div');
        metaList.style.cssText = 'text-align:left;font-size:12px;color:#888;margin-bottom:12px;';
        var mk = Object.keys(item.metadata);
        for (var mi = 0; mi < mk.length; mi++) {
          var mrow = document.createElement('div');
          mrow.textContent = mk[mi] + ': ' + item.metadata[mk[mi]];
          metaList.appendChild(mrow);
        }
        content.appendChild(metaList);
      }

      var btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;justify-content:center;';

      var exportBtn = document.createElement('button');
      exportBtn.textContent = 'Export PNG';
      exportBtn.style.cssText = 'padding:8px 16px;background:#4fc3f7;color:#000;border:none;' +
        'border-radius:4px;cursor:pointer;font-weight:600;';
      var self = this;
      exportBtn.addEventListener('click', function() {
        self.exportPNG(item.dataUrl, (item.title || 'gallery') + '.png');
      });
      btnRow.appendChild(exportBtn);

      var delBtn = document.createElement('button');
      delBtn.textContent = 'Delete';
      delBtn.style.cssText = 'padding:8px 16px;background:#F44336;color:#fff;border:none;' +
        'border-radius:4px;cursor:pointer;font-weight:600;';
      delBtn.addEventListener('click', function() {
        self.delete(item.id);
        if (ui) ui.closeModal();
      });
      btnRow.appendChild(delBtn);

      content.appendChild(btnRow);

      if (ui) {
        ui.showModal(item.title, content);
      } else {
        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);' +
          'display:flex;align-items:center;justify-content:center;z-index:9999;';
        var box = document.createElement('div');
        box.style.cssText = 'background:#1a1a2e;border-radius:12px;padding:20px;max-width:600px;width:90%;';
        box.appendChild(content);
        overlay.appendChild(box);
        overlay.addEventListener('click', function(e) {
          if (e.target === overlay) overlay.parentNode.removeChild(overlay);
        });
        document.body.appendChild(overlay);
      }
    }

    exportPNG(dataUrl, filename) {
      filename = filename || 'capture.png';
      var link = document.createElement('a');
      link.href = dataUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }

    sort(field) {
      this.sortBy = field || 'date';
    }

    filter(query) {
      this.filterQuery = (query || '').toLowerCase();
    }

    _getFilteredSorted() {
      var items = this.items.slice();
      var q = this.filterQuery;
      if (q) {
        items = items.filter(function(it) {
          return (it.title || '').toLowerCase().indexOf(q) !== -1;
        });
      }
      var sortBy = this.sortBy;
      items.sort(function(a, b) {
        if (sortBy === 'score') return (b.score || 0) - (a.score || 0);
        if (sortBy === 'title') return (a.title || '').localeCompare(b.title || '');
        return new Date(b.date) - new Date(a.date);
      });
      return items;
    }
  }

  window.CT = window.CT || {};
  window.CT.Gallery = Gallery;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 4. Tutorial System
# ---------------------------------------------------------------------------


@register("tutorial")
def generate_tutorial(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var TUTORIAL_KEY = 'ct_tutorial_progress';

  var DEFAULT_STEPS = [
    {
      id: 'welcome',
      target: null,
      message: 'Welcome to Chromaticity! This tutorial will guide you through the basics of territory painting and strategic composition.',
      position: 'center',
      action: null,
      highlight: false
    },
    {
      id: 'canvas_nav',
      target: '.ct-layer-ui',
      message: 'This is your canvas. Click and drag to pan around. Use the scroll wheel or pinch to zoom in and out.',
      position: 'bottom',
      action: null,
      highlight: true
    },
    {
      id: 'select_palette',
      target: '.ct-palette-selector',
      message: 'Choose a color palette for your territories. Each palette affects your chromaticity score differently.',
      position: 'top',
      action: 'click',
      highlight: true
    },
    {
      id: 'place_territory',
      target: '.ct-layer-ui',
      message: 'Click on any hex cell to place your first territory. Territories start small and grow over time.',
      position: 'bottom',
      action: 'click',
      highlight: true
    },
    {
      id: 'expand',
      target: '.ct-layer-ui',
      message: 'Click adjacent cells to expand your territory. Connected cells share color influence and increase your score.',
      position: 'bottom',
      action: 'click',
      highlight: true
    },
    {
      id: 'combat',
      target: '.ct-hud-phase',
      message: 'When territories of different owners share a border, combat occurs automatically. Stronger territories absorb weaker ones.',
      position: 'bottom',
      action: null,
      highlight: true
    },
    {
      id: 'chromaticity',
      target: '.ct-hud-chromaticity',
      message: 'Your Chromaticity score reflects the harmoniousness of your color composition. Complementary colors boost your score!',
      position: 'bottom',
      action: null,
      highlight: true
    },
    {
      id: 'gallery_intro',
      target: null,
      message: 'At any time, you can capture your current board state to the Gallery. Press "C" or use the capture button.',
      position: 'center',
      action: null,
      highlight: false
    },
    {
      id: 'minimap',
      target: null,
      message: 'The minimap in the corner shows an overview of the entire board. Click on it to quickly navigate.',
      position: 'left',
      action: null,
      highlight: false
    },
    {
      id: 'keyboard',
      target: null,
      message: 'Keyboard shortcuts: Space = end turn, 1-5 = select action, M = toggle minimap, G = gallery, Esc = cancel.',
      position: 'center',
      action: null,
      highlight: false
    },
    {
      id: 'settings',
      target: null,
      message: 'Open Settings to adjust audio, visual effects, grid size, and game difficulty.',
      position: 'center',
      action: null,
      highlight: false
    },
    {
      id: 'advanced',
      target: null,
      message: 'Advanced tip: Surround an enemy territory completely to capture it instantly. Use terrain height for defensive positions.',
      position: 'center',
      action: null,
      highlight: false
    },
    {
      id: 'complete',
      target: null,
      message: 'You are ready to play! Good luck creating beautiful, strategic compositions. The board is your canvas.',
      position: 'center',
      action: null,
      highlight: false
    }
  ];

  class TutorialSystem {
    constructor(dataLayer) {
      this.dataLayer = dataLayer || null;
      this.steps = DEFAULT_STEPS.slice();
      this.currentStep = 0;
      this.active = false;
      this.overlay = null;
      this.bubble = null;
      this.dotsEl = null;
      this._actionCleanup = null;
    }

    start() {
      this.active = true;
      var saved = this._loadProgress();
      this.currentStep = (saved >= 0 && saved < this.steps.length) ? saved : 0;
      this._renderOverlay();
      this._showStep(this.steps[this.currentStep]);
    }

    next() {
      this._cleanupAction();
      this.currentStep++;
      if (this.currentStep >= this.steps.length) {
        this.finish();
        return;
      }
      this._saveProgress();
      this._showStep(this.steps[this.currentStep]);
    }

    prev() {
      this._cleanupAction();
      if (this.currentStep > 0) {
        this.currentStep--;
        this._saveProgress();
        this._showStep(this.steps[this.currentStep]);
      }
    }

    skip() {
      this._cleanupAction();
      this.currentStep = this.steps.length;
      this._saveProgress();
      this.finish();
    }

    _renderOverlay() {
      if (this.overlay) return;
      this.overlay = document.createElement('div');
      this.overlay.className = 'ct-tutorial-overlay';
      this.overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:8000;' +
        'pointer-events:none;';
      document.body.appendChild(this.overlay);

      this.bubble = document.createElement('div');
      this.bubble.className = 'ct-tutorial-bubble';
      this.bubble.style.cssText = 'position:fixed;background:#1a1a2e;border:1px solid #4fc3f7;' +
        'border-radius:10px;padding:16px 20px;max-width:360px;color:#e0e0e0;font-size:14px;' +
        'line-height:1.5;z-index:8100;pointer-events:auto;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
      document.body.appendChild(this.bubble);
    }

    _highlightTarget(selector) {
      this.overlay.innerHTML = '';
      if (!selector) {
        this.overlay.style.background = 'rgba(0,0,0,0.5)';
        this.overlay.style.boxShadow = 'none';
        return null;
      }
      var target = document.querySelector(selector);
      if (!target) {
        this.overlay.style.background = 'rgba(0,0,0,0.5)';
        return null;
      }
      var rect = target.getBoundingClientRect();
      var pad = 6;
      this.overlay.style.background = 'none';

      var mask = document.createElement('div');
      mask.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;' +
        'box-shadow:0 0 0 9999px rgba(0,0,0,0.55);' +
        'clip-path:polygon(0% 0%, 0% 100%, ' +
        (rect.left - pad) + 'px 100%, ' +
        (rect.left - pad) + 'px ' + (rect.top - pad) + 'px, ' +
        (rect.right + pad) + 'px ' + (rect.top - pad) + 'px, ' +
        (rect.right + pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
        (rect.left - pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
        (rect.left - pad) + 'px 100%, 100% 100%, 100% 0%);';
      this.overlay.appendChild(mask);

      var highlight = document.createElement('div');
      highlight.style.cssText = 'position:absolute;left:' + (rect.left - pad) + 'px;' +
        'top:' + (rect.top - pad) + 'px;width:' + (rect.width + pad * 2) + 'px;' +
        'height:' + (rect.height + pad * 2) + 'px;border:2px solid #4fc3f7;border-radius:6px;' +
        'pointer-events:none;animation:ct-tut-pulse 1.5s infinite;';
      this.overlay.appendChild(highlight);

      return rect;
    }

    _showStep(step) {
      var targetRect = null;
      if (step.highlight && step.target) {
        targetRect = this._highlightTarget(step.target);
      } else {
        this._highlightTarget(null);
      }

      this.bubble.innerHTML = '';

      var msgEl = document.createElement('div');
      msgEl.textContent = step.message;
      msgEl.style.marginBottom = '12px';
      this.bubble.appendChild(msgEl);

      this.dotsEl = this._renderProgressDots();
      this.bubble.appendChild(this.dotsEl);

      var btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:8px;';

      var self = this;
      if (this.currentStep > 0) {
        var prevBtn = document.createElement('button');
        prevBtn.textContent = 'Back';
        prevBtn.style.cssText = 'padding:5px 12px;background:none;border:1px solid #4fc3f7;color:#4fc3f7;' +
          'border-radius:4px;cursor:pointer;font-size:12px;';
        prevBtn.addEventListener('click', function() { self.prev(); });
        btnRow.appendChild(prevBtn);
      }

      var skipBtn = document.createElement('button');
      skipBtn.textContent = 'Skip Tutorial';
      skipBtn.style.cssText = 'padding:5px 12px;background:none;border:1px solid #666;color:#888;' +
        'border-radius:4px;cursor:pointer;font-size:12px;';
      skipBtn.addEventListener('click', function() { self.skip(); });
      btnRow.appendChild(skipBtn);

      if (!step.action) {
        var nextBtn = document.createElement('button');
        nextBtn.textContent = this.currentStep === this.steps.length - 1 ? 'Finish' : 'Next';
        nextBtn.style.cssText = 'padding:5px 14px;background:#4fc3f7;border:none;color:#000;' +
          'border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;';
        nextBtn.addEventListener('click', function() { self.next(); });
        btnRow.appendChild(nextBtn);
      } else {
        var actionHint = document.createElement('span');
        actionHint.style.cssText = 'font-size:11px;color:#4fc3f7;align-self:center;';
        actionHint.textContent = '(' + step.action + ' to continue)';
        btnRow.appendChild(actionHint);
        this._waitForAction(step);
      }

      this.bubble.appendChild(btnRow);
      this._positionBubble(step.position, targetRect);
    }

    _positionBubble(position, targetRect) {
      var bw = 360;
      var bh = this.bubble.offsetHeight || 150;
      var margin = 16;
      var left, top;

      if (position === 'center' || !targetRect) {
        left = (window.innerWidth - bw) / 2;
        top = (window.innerHeight - bh) / 2;
      } else if (position === 'top') {
        left = targetRect.left + targetRect.width / 2 - bw / 2;
        top = targetRect.top - bh - margin;
      } else if (position === 'bottom') {
        left = targetRect.left + targetRect.width / 2 - bw / 2;
        top = targetRect.bottom + margin;
      } else if (position === 'left') {
        left = targetRect.left - bw - margin;
        top = targetRect.top + targetRect.height / 2 - bh / 2;
      } else if (position === 'right') {
        left = targetRect.right + margin;
        top = targetRect.top + targetRect.height / 2 - bh / 2;
      } else {
        left = (window.innerWidth - bw) / 2;
        top = (window.innerHeight - bh) / 2;
      }

      left = Math.max(margin, Math.min(left, window.innerWidth - bw - margin));
      top = Math.max(margin, Math.min(top, window.innerHeight - bh - margin));

      this.bubble.style.left = left + 'px';
      this.bubble.style.top = top + 'px';
    }

    _renderProgressDots() {
      var container = document.createElement('div');
      container.style.cssText = 'display:flex;gap:5px;justify-content:center;';
      for (var i = 0; i < this.steps.length; i++) {
        var dot = document.createElement('div');
        var isActive = i === this.currentStep;
        var isDone = i < this.currentStep;
        dot.style.cssText = 'width:8px;height:8px;border-radius:50%;transition:all 0.2s;' +
          'background:' + (isActive ? '#4fc3f7' : (isDone ? '#2196F3' : '#444')) + ';' +
          (isActive ? 'transform:scale(1.3);' : '');
        container.appendChild(dot);
      }
      return container;
    }

    _waitForAction(step) {
      var self = this;
      this._cleanupAction();

      if (step.action === 'click') {
        var target = step.target ? document.querySelector(step.target) : document;
        if (!target) target = document;
        var handler = function() {
          target.removeEventListener('click', handler, true);
          self._actionCleanup = null;
          self.next();
        };
        target.addEventListener('click', handler, true);
        this._actionCleanup = function() { target.removeEventListener('click', handler, true); };
      } else if (step.action === 'hover') {
        var hTarget = step.target ? document.querySelector(step.target) : null;
        if (hTarget) {
          var hHandler = function() {
            hTarget.removeEventListener('mouseenter', hHandler);
            self._actionCleanup = null;
            self.next();
          };
          hTarget.addEventListener('mouseenter', hHandler);
          this._actionCleanup = function() { hTarget.removeEventListener('mouseenter', hHandler); };
        }
      } else if (step.action === 'keypress') {
        var kHandler = function() {
          document.removeEventListener('keydown', kHandler);
          self._actionCleanup = null;
          self.next();
        };
        document.addEventListener('keydown', kHandler);
        this._actionCleanup = function() { document.removeEventListener('keydown', kHandler); };
      }
    }

    _cleanupAction() {
      if (this._actionCleanup) {
        this._actionCleanup();
        this._actionCleanup = null;
      }
    }

    _saveProgress() {
      var val = String(this.currentStep);
      if (this.dataLayer && typeof this.dataLayer.set === 'function') {
        this.dataLayer.set(TUTORIAL_KEY, val);
      } else {
        try { localStorage.setItem(TUTORIAL_KEY, val); } catch (e) { /* ignore */ }
      }
    }

    _loadProgress() {
      var raw;
      if (this.dataLayer && typeof this.dataLayer.get === 'function') {
        raw = this.dataLayer.get(TUTORIAL_KEY);
      } else {
        try { raw = localStorage.getItem(TUTORIAL_KEY); } catch (e) { raw = null; }
      }
      return raw !== null ? parseInt(raw, 10) : -1;
    }

    finish() {
      this._cleanupAction();
      this.active = false;
      this.currentStep = this.steps.length;
      this._saveProgress();
      if (this.overlay && this.overlay.parentNode) {
        this.overlay.parentNode.removeChild(this.overlay);
      }
      if (this.bubble && this.bubble.parentNode) {
        this.bubble.parentNode.removeChild(this.bubble);
      }
      this.overlay = null;
      this.bubble = null;
    }
  }

  window.CT = window.CT || {};
  window.CT.TutorialSystem = TutorialSystem;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 5. Audio Synthesizer
# ---------------------------------------------------------------------------


@register("audio_synth")
def generate_audio_synth(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var NOTE_FREQS = {
    'C': 16.35, 'C#': 17.32, 'D': 18.35, 'D#': 19.45, 'E': 20.60,
    'F': 21.83, 'F#': 23.12, 'G': 24.50, 'G#': 25.96, 'A': 27.50,
    'A#': 29.14, 'B': 30.87
  };

  var NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

  var SCALES = {
    major:       [0, 2, 4, 5, 7, 9, 11],
    minor:       [0, 2, 3, 5, 7, 8, 10],
    pentatonic:  [0, 2, 4, 7, 9],
    blues:       [0, 3, 5, 6, 7, 10],
    chromatic:   [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    dorian:      [0, 2, 3, 5, 7, 9, 10],
    mixolydian:  [0, 2, 4, 5, 7, 9, 10],
    harmonicMinor: [0, 2, 3, 5, 7, 8, 11],
    lydian:      [0, 2, 4, 6, 7, 9, 11],
    phrygian:    [0, 1, 3, 5, 7, 8, 10]
  };

  var CHORD_PATTERNS = {
    major:  [0, 4, 7],
    minor:  [0, 3, 7],
    dim:    [0, 3, 6],
    aug:    [0, 4, 8],
    dom7:   [0, 4, 7, 10],
    maj7:   [0, 4, 7, 11],
    min7:   [0, 3, 7, 10],
    sus2:   [0, 2, 7],
    sus4:   [0, 5, 7],
    add9:   [0, 4, 7, 14],
    dim7:   [0, 3, 6, 9],
    aug7:   [0, 4, 8, 10]
  };

  var DEFAULT_ENVELOPE = { attack: 0.02, decay: 0.1, sustain: 0.5, release: 0.3 };

  class AudioSynthesizer {
    constructor() {
      this.audioCtx = null;
      this.masterGain = null;
      this.muted = false;
      this.volume = 0.7;
      this._reverbNode = null;
      this._delayNode = null;
      this._compressor = null;
    }

    _ensureContext() {
      if (this.audioCtx) return;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this.audioCtx = new AC();

      this._compressor = this.audioCtx.createDynamicsCompressor();
      this._compressor.threshold.value = -24;
      this._compressor.knee.value = 30;
      this._compressor.ratio.value = 12;
      this._compressor.attack.value = 0.003;
      this._compressor.release.value = 0.25;
      this._compressor.connect(this.audioCtx.destination);

      this.masterGain = this.audioCtx.createGain();
      this.masterGain.gain.value = this.volume;
      this.masterGain.connect(this._compressor);
    }

    _resume() {
      if (this.audioCtx && this.audioCtx.state === 'suspended') {
        this.audioCtx.resume();
      }
    }

    noteToFreq(note, octave) {
      if (typeof note === 'number') {
        return 440 * Math.pow(2, (note - 69) / 12);
      }
      var idx = NOTE_NAMES.indexOf(note);
      if (idx === -1) return 440;
      octave = octave !== undefined ? octave : 4;
      var semitones = (octave - 0) * 12 + idx;
      var a4Semitones = 4 * 12 + 9;
      return 440 * Math.pow(2, (semitones - a4Semitones) / 12);
    }

    getScale(root, type, octave) {
      type = type || 'major';
      octave = octave !== undefined ? octave : 4;
      var pattern = SCALES[type] || SCALES.major;
      var rootIdx = NOTE_NAMES.indexOf(root);
      if (rootIdx === -1) rootIdx = 0;

      var freqs = [];
      for (var i = 0; i < pattern.length; i++) {
        var semitone = rootIdx + pattern[i];
        var noteOctave = octave + Math.floor(semitone / 12);
        var noteIdx = semitone % 12;
        freqs.push(this.noteToFreq(NOTE_NAMES[noteIdx], noteOctave));
      }
      return freqs;
    }

    playNote(freq, duration, type, envelope) {
      this._ensureContext();
      this._resume();
      if (!this.audioCtx || this.muted) return null;

      duration = duration || 0.5;
      type = type || 'sine';
      envelope = envelope || DEFAULT_ENVELOPE;

      var ctx = this.audioCtx;
      var now = ctx.currentTime;

      var osc = ctx.createOscillator();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, now);

      var gainNode = ctx.createGain();
      gainNode.gain.setValueAtTime(0, now);
      this._applyADSR(gainNode, envelope, now, duration);

      osc.connect(gainNode);
      gainNode.connect(this.masterGain);

      osc.start(now);
      osc.stop(now + duration + envelope.release + 0.05);

      return { oscillator: osc, gain: gainNode, startTime: now, duration: duration };
    }

    _applyADSR(gainNode, env, startTime, duration) {
      var peak = 1.0;
      var atk = Math.min(env.attack || 0.02, duration * 0.3);
      var dec = Math.min(env.decay || 0.1, duration * 0.3);
      var sus = env.sustain !== undefined ? env.sustain : 0.5;
      var rel = env.release || 0.3;

      gainNode.gain.setValueAtTime(0, startTime);
      gainNode.gain.linearRampToValueAtTime(peak, startTime + atk);
      gainNode.gain.linearRampToValueAtTime(peak * sus, startTime + atk + dec);
      gainNode.gain.setValueAtTime(peak * sus, startTime + duration);
      gainNode.gain.linearRampToValueAtTime(0, startTime + duration + rel);
    }

    playChord(freqs, duration, type) {
      if (!freqs || !freqs.length) return [];
      var results = [];
      for (var i = 0; i < freqs.length; i++) {
        var note = this.playNote(freqs[i], duration, type);
        if (note) results.push(note);
      }
      return results;
    }

    chordFromRoot(root, type, octave) {
      type = type || 'major';
      octave = octave !== undefined ? octave : 4;
      var pattern = CHORD_PATTERNS[type] || CHORD_PATTERNS.major;
      var rootIdx = NOTE_NAMES.indexOf(root);
      if (rootIdx === -1) rootIdx = 0;

      var freqs = [];
      for (var i = 0; i < pattern.length; i++) {
        var semitone = rootIdx + pattern[i];
        var noteOctave = octave + Math.floor(semitone / 12);
        var noteIdx = semitone % 12;
        freqs.push(this.noteToFreq(NOTE_NAMES[noteIdx], noteOctave));
      }
      return freqs;
    }

    createFilter(type, freq, q) {
      this._ensureContext();
      if (!this.audioCtx) return null;
      var filter = this.audioCtx.createBiquadFilter();
      filter.type = type || 'lowpass';
      filter.frequency.value = freq || 1000;
      filter.Q.value = q || 1;
      return filter;
    }

    _createReverb(duration, decay) {
      this._ensureContext();
      if (!this.audioCtx) return null;
      duration = duration || 2;
      decay = decay || 2;
      var ctx = this.audioCtx;
      var sampleRate = ctx.sampleRate;
      var length = sampleRate * duration;
      var impulse = ctx.createBuffer(2, length, sampleRate);

      for (var ch = 0; ch < 2; ch++) {
        var data = impulse.getChannelData(ch);
        for (var i = 0; i < length; i++) {
          data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay);
        }
      }

      var convolver = ctx.createConvolver();
      convolver.buffer = impulse;
      return convolver;
    }

    _createDelay(time, feedback) {
      this._ensureContext();
      if (!this.audioCtx) return null;
      time = time || 0.3;
      feedback = feedback || 0.4;
      var ctx = this.audioCtx;

      var delay = ctx.createDelay(5);
      delay.delayTime.value = time;

      var fbGain = ctx.createGain();
      fbGain.gain.value = feedback;

      var dryGain = ctx.createGain();
      dryGain.gain.value = 1;
      var wetGain = ctx.createGain();
      wetGain.gain.value = 0.5;

      delay.connect(fbGain);
      fbGain.connect(delay);

      return { delay: delay, feedback: fbGain, dry: dryGain, wet: wetGain };
    }

    playArpeggio(freqs, noteLength, pattern) {
      if (!freqs || !freqs.length) return;
      noteLength = noteLength || 0.15;
      pattern = pattern || 'up';

      var ordered;
      if (pattern === 'down') {
        ordered = freqs.slice().reverse();
      } else if (pattern === 'updown') {
        ordered = freqs.slice();
        for (var i = freqs.length - 2; i >= 0; i--) {
          ordered.push(freqs[i]);
        }
      } else if (pattern === 'random') {
        ordered = freqs.slice();
        for (var ri = ordered.length - 1; ri > 0; ri--) {
          var j = Math.floor(Math.random() * (ri + 1));
          var temp = ordered[ri];
          ordered[ri] = ordered[j];
          ordered[j] = temp;
        }
      } else {
        ordered = freqs.slice();
      }

      this._ensureContext();
      this._resume();
      if (!this.audioCtx || this.muted) return;

      var ctx = this.audioCtx;
      var now = ctx.currentTime;

      for (var ni = 0; ni < ordered.length; ni++) {
        var startAt = now + ni * noteLength;
        var osc = ctx.createOscillator();
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(ordered[ni], startAt);

        var g = ctx.createGain();
        g.gain.setValueAtTime(0, startAt);
        g.gain.linearRampToValueAtTime(0.6, startAt + 0.01);
        g.gain.linearRampToValueAtTime(0.3, startAt + noteLength * 0.6);
        g.gain.linearRampToValueAtTime(0, startAt + noteLength);

        osc.connect(g);
        g.connect(this.masterGain);
        osc.start(startAt);
        osc.stop(startAt + noteLength + 0.01);
      }
    }

    _playNoise(duration, filterFreq, filterQ, startTime) {
      this._ensureContext();
      if (!this.audioCtx) return;
      var ctx = this.audioCtx;
      startTime = startTime || ctx.currentTime;
      duration = duration || 0.1;

      var bufferSize = ctx.sampleRate * duration;
      var buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
      var data = buffer.getChannelData(0);
      for (var i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }

      var source = ctx.createBufferSource();
      source.buffer = buffer;

      var gain = ctx.createGain();
      gain.gain.setValueAtTime(0.5, startTime);
      gain.gain.linearRampToValueAtTime(0, startTime + duration);

      if (filterFreq) {
        var filter = ctx.createBiquadFilter();
        filter.type = 'bandpass';
        filter.frequency.value = filterFreq;
        filter.Q.value = filterQ || 2;
        source.connect(filter);
        filter.connect(gain);
      } else {
        source.connect(gain);
      }

      gain.connect(this.masterGain);
      source.start(startTime);
      source.stop(startTime + duration + 0.01);
    }

    setVolume(v) {
      this.volume = Math.max(0, Math.min(1, v));
      if (this.masterGain) {
        this.masterGain.gain.setValueAtTime(this.volume, this.audioCtx.currentTime);
      }
    }

    mute() {
      this.muted = true;
      if (this.masterGain) {
        this.masterGain.gain.setValueAtTime(0, this.audioCtx.currentTime);
      }
    }

    unmute() {
      this.muted = false;
      if (this.masterGain) {
        this.masterGain.gain.setValueAtTime(this.volume, this.audioCtx.currentTime);
      }
    }
  }

  window.CT = window.CT || {};
  window.CT.AudioSynthesizer = AudioSynthesizer;
  window.CT.SCALES = SCALES;
  window.CT.CHORD_PATTERNS = CHORD_PATTERNS;
  window.CT.NOTE_NAMES = NOTE_NAMES;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 6. Generative Music
# ---------------------------------------------------------------------------


@register("generative_music")
def generate_generative_music(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var KEY_MAP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

  var RHYTHM_PATTERNS = {
    steady:   [1, 0, 1, 0, 1, 0, 1, 0],
    driving:  [1, 0, 0, 1, 0, 0, 1, 0],
    sparse:   [1, 0, 0, 0, 1, 0, 0, 0],
    complex:  [1, 0, 1, 1, 0, 1, 0, 1],
    waltz:    [1, 0, 0, 1, 0, 0, 1, 0, 0],
    syncopated:[0, 1, 0, 1, 1, 0, 1, 0]
  };

  function hueToKey(hue) {
    return Math.round((hue / 360) * 12) % 12;
  }

  function hexToHSL(hex) {
    hex = hex.replace('#', '');
    var r = parseInt(hex.substring(0, 2), 16) / 255;
    var g = parseInt(hex.substring(2, 4), 16) / 255;
    var b = parseInt(hex.substring(4, 6), 16) / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b);
    var h = 0, s = 0, l = (max + min) / 2;
    if (max !== min) {
      var d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
      else if (max === g) h = ((b - r) / d + 2) / 6;
      else h = ((r - g) / d + 4) / 6;
    }
    return { h: h * 360, s: s, l: l };
  }

  function clamp01(v) { return Math.max(0, Math.min(1, v)); }

  class GenerativeMusic {
    constructor(synth) {
      this.synth = synth;
      this.state = null;
      this.isPlaying = false;
      this.ambientInterval = null;
      this.melodyTimeout = null;
      this._droneOscs = [];
      this._currentParams = null;
      this._markovHistory = [];
      this._rhythmStep = 0;
    }

    mapGameState(state) {
      if (!state) {
        return {
          key: 'C', scale: 'pentatonic', tempo: 90, intensity: 0.3,
          harmonyType: 'major', rhythmPattern: 'sparse', melodicComplexity: 2
        };
      }
      this.state = state;

      var colors = state.territoryColors || state.colors || [];
      var tension = state.borderTension !== undefined ? state.borderTension : 0.3;
      var score = state.compositionScore || state.score || 0;
      var phase = state.turnPhase || state.phase || 'action';
      var turn = state.turn || 1;

      var keyInfo = this._selectKey(colors);
      var scale = this._selectScale(tension);
      var intensity = clamp01(tension * 0.5 + (turn / 50) * 0.3 + (score / 1000) * 0.2);
      var tempo = 70 + intensity * 80;
      var complexity = Math.max(1, Math.min(5, Math.round(score / 200) + 1));

      var harmonyType;
      if (keyInfo.warm > keyInfo.cool) {
        harmonyType = tension > 0.5 ? 'dom7' : 'major';
      } else {
        harmonyType = tension > 0.5 ? 'dim' : 'minor';
      }

      var rhythmPattern;
      if (phase === 'setup') rhythmPattern = 'sparse';
      else if (phase === 'resolve') rhythmPattern = 'steady';
      else if (intensity > 0.7) rhythmPattern = 'complex';
      else if (intensity > 0.4) rhythmPattern = 'driving';
      else rhythmPattern = 'steady';

      this._currentParams = {
        key: keyInfo.key,
        scale: scale,
        tempo: tempo,
        intensity: intensity,
        harmonyType: harmonyType,
        rhythmPattern: rhythmPattern,
        melodicComplexity: complexity
      };
      return this._currentParams;
    }

    _selectKey(colors) {
      if (!colors || colors.length === 0) {
        return { key: 'C', warm: 0.5, cool: 0.5 };
      }
      var hueSum = 0;
      var warm = 0, cool = 0;
      for (var i = 0; i < colors.length; i++) {
        var hsl = hexToHSL(colors[i]);
        hueSum += hsl.h;
        if (hsl.h < 60 || hsl.h > 300) warm++;
        else if (hsl.h > 150 && hsl.h < 270) cool++;
        else { warm += 0.5; cool += 0.5; }
      }
      var avgHue = hueSum / colors.length;
      var keyIdx = hueToKey(avgHue);
      return { key: KEY_MAP[keyIdx], warm: warm, cool: cool };
    }

    _selectScale(tension) {
      if (tension < 0.2) return 'pentatonic';
      if (tension < 0.4) return 'major';
      if (tension < 0.6) return 'minor';
      if (tension < 0.8) return 'blues';
      return 'chromatic';
    }

    startAmbient() {
      if (this.isPlaying) this.stopAmbient();
      this.isPlaying = true;

      var params = this._currentParams || this.mapGameState(this.state);
      var synth = this.synth;
      if (!synth) return;

      synth._ensureContext();
      synth._resume();
      if (!synth.audioCtx) return;

      var scale = synth.getScale(params.key, params.scale, 3);
      var self = this;
      var chordIdx = 0;

      var beatMs = (60 / params.tempo) * 1000;

      this.ambientInterval = setInterval(function() {
        if (!self.isPlaying) return;

        var rootFreq = scale[chordIdx % scale.length];
        var chordFreqs = synth.chordFromRoot(
          params.key, params.harmonyType, 3
        );

        for (var ci = 0; ci < chordFreqs.length; ci++) {
          synth.playNote(chordFreqs[ci], beatMs * 4 / 1000, 'sine', {
            attack: 0.5, decay: 0.3, sustain: 0.6, release: 1.0
          });
        }

        chordIdx++;
        if (chordIdx % 4 === 0 && params.intensity > 0.3) {
          self._scheduleMelody(params, scale);
        }
      }, beatMs * 4);

      if (params.intensity > 0.4) {
        this._startRhythm(params);
      }
    }

    stopAmbient() {
      this.isPlaying = false;
      if (this.ambientInterval) {
        clearInterval(this.ambientInterval);
        this.ambientInterval = null;
      }
      if (this.melodyTimeout) {
        clearTimeout(this.melodyTimeout);
        this.melodyTimeout = null;
      }
      if (this._rhythmInterval) {
        clearInterval(this._rhythmInterval);
        this._rhythmInterval = null;
      }
    }

    _scheduleMelody(params, scale) {
      var self = this;
      var synth = this.synth;
      if (!synth || !this.isPlaying) return;

      var noteCount = 4 + params.melodicComplexity * 2;
      var beatMs = (60 / params.tempo) * 1000;
      var noteDuration = beatMs / 1000 * 0.8;
      var currentIdx = Math.floor(Math.random() * scale.length);

      for (var i = 0; i < noteCount; i++) {
        (function(idx, delay) {
          self.melodyTimeout = setTimeout(function() {
            if (!self.isPlaying) return;
            var nextIdx = self._markovNext(idx, scale, params.melodicComplexity);
            var freq = scale[nextIdx % scale.length];
            synth.playNote(freq * 2, noteDuration, 'triangle', {
              attack: 0.01, decay: 0.05, sustain: 0.4, release: 0.15
            });
          }, delay);
        })(currentIdx, i * beatMs);
        currentIdx = this._markovNext(currentIdx, scale, params.melodicComplexity);
      }
    }

    _markovNext(current, scale, complexity) {
      var len = scale.length;
      if (len === 0) return 0;

      var weights = [];
      var total = 0;
      for (var i = 0; i < len; i++) {
        var dist = Math.abs(i - current);
        if (dist > len / 2) dist = len - dist;
        var w;
        if (dist === 0) w = 0.5;
        else if (dist === 1) w = 3.0;
        else if (dist === 2) w = 2.0;
        else if (dist <= complexity) w = 1.0;
        else w = 0.3;
        weights.push(w);
        total += w;
      }

      var r = Math.random() * total;
      var accum = 0;
      for (var j = 0; j < weights.length; j++) {
        accum += weights[j];
        if (r <= accum) return j;
      }
      return 0;
    }

    playMelody(params) {
      params = params || this._currentParams || {};
      var synth = this.synth;
      if (!synth) return;
      var scale = synth.getScale(params.key || 'C', params.scale || 'pentatonic', 4);
      this._scheduleMelody(params, scale);
    }

    _startRhythm(params) {
      var synth = this.synth;
      if (!synth) return;
      var patternName = params.rhythmPattern || 'steady';
      var pattern = RHYTHM_PATTERNS[patternName] || RHYTHM_PATTERNS.steady;
      var beatMs = (60 / params.tempo) * 1000 / 2;
      var self = this;
      this._rhythmStep = 0;

      this._rhythmInterval = setInterval(function() {
        if (!self.isPlaying) return;
        var step = self._rhythmStep % pattern.length;
        if (pattern[step]) {
          if (step === 0) {
            self._playKick();
          } else if (step % 4 === 2) {
            self._playSnare();
          } else {
            self._playHat();
          }
        }
        self._rhythmStep++;
      }, beatMs);
    }

    _playKick() {
      var synth = this.synth;
      if (!synth) return;
      synth._ensureContext();
      var ctx = synth.audioCtx;
      if (!ctx) return;
      var now = ctx.currentTime;
      var osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(150, now);
      osc.frequency.exponentialRampToValueAtTime(40, now + 0.08);
      var g = ctx.createGain();
      g.gain.setValueAtTime(0.8, now);
      g.gain.exponentialRampToValueAtTime(0.01, now + 0.15);
      osc.connect(g);
      g.connect(synth.masterGain);
      osc.start(now);
      osc.stop(now + 0.2);
    }

    _playSnare() {
      var synth = this.synth;
      if (!synth) return;
      synth._playNoise(0.12, 3000, 1.5);
    }

    _playHat() {
      var synth = this.synth;
      if (!synth) return;
      synth._playNoise(0.04, 8000, 3);
    }

    playRhythm(pattern) {
      var params = this._currentParams || { tempo: 100, rhythmPattern: pattern || 'steady' };
      if (pattern) params.rhythmPattern = pattern;
      this._startRhythm(params);
    }

    playSFX(type) {
      var synth = this.synth;
      if (!synth) return;

      if (type === 'expand') {
        var scale = synth.getScale('C', 'major', 4);
        synth.playArpeggio(scale, 0.08, 'up');
      } else if (type === 'combat') {
        var clusterFreqs = [261, 277, 293, 311, 329];
        synth.playChord(clusterFreqs, 0.3, 'sawtooth');
        setTimeout(function() {
          var resolveFreqs = synth.chordFromRoot('C', 'major', 4);
          synth.playChord(resolveFreqs, 0.8, 'sine');
        }, 400);
      } else if (type === 'victory') {
        var fanfare = synth.chordFromRoot('C', 'major', 4);
        synth.playArpeggio(fanfare, 0.12, 'up');
        setTimeout(function() {
          synth.playChord(fanfare, 1.0, 'triangle');
        }, fanfare.length * 120 + 100);
      } else if (type === 'defeat') {
        var descScale = synth.getScale('A', 'minor', 4);
        synth.playArpeggio(descScale, 0.15, 'down');
      } else if (type === 'click') {
        synth.playNote(1200, 0.04, 'square', {
          attack: 0.001, decay: 0.02, sustain: 0, release: 0.01
        });
      } else if (type === 'notification') {
        synth.playNote(880, 0.1, 'sine', {
          attack: 0.005, decay: 0.05, sustain: 0.3, release: 0.1
        });
        setTimeout(function() {
          synth.playNote(1108.73, 0.2, 'sine', {
            attack: 0.005, decay: 0.05, sustain: 0.3, release: 0.15
          });
        }, 120);
      } else if (type === 'error') {
        synth.playNote(200, 0.3, 'sawtooth', {
          attack: 0.01, decay: 0.1, sustain: 0.3, release: 0.2
        });
      } else if (type === 'levelup') {
        var notes = [523.25, 659.25, 783.99, 1046.50];
        synth.playArpeggio(notes, 0.1, 'up');
      }
    }

    setVolume(v) {
      if (this.synth) this.synth.setVolume(v);
    }

    mute() {
      if (this.synth) this.synth.mute();
    }

    unmute() {
      if (this.synth) this.synth.unmute();
    }
  }

  window.CT = window.CT || {};
  window.CT.GenerativeMusic = GenerativeMusic;
})();
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 7. App Init
# ---------------------------------------------------------------------------


@register("app_init")
def generate_app_init(**kwargs) -> tuple[str, str, str]:
    js = """\
(function() {
  'use strict';

  var ROUTES = {
    '/':         'welcome',
    '/play':     'play',
    '/gallery':  'gallery',
    '/tutorial': 'tutorial',
    '/settings': 'settings',
    '/about':    'about'
  };

  var KEY_BINDINGS = {
    ' ':       'endTurn',
    'Escape':  'cancel',
    '1':       'action1',
    '2':       'action2',
    '3':       'action3',
    '4':       'action4',
    '5':       'action5',
    'm':       'toggleMinimap',
    'M':       'toggleMinimap',
    'g':       'gallery',
    'G':       'gallery',
    'c':       'capture',
    'C':       'capture',
    'ArrowUp':    'panUp',
    'ArrowDown':  'panDown',
    'ArrowLeft':  'panLeft',
    'ArrowRight': 'panRight',
    '+':       'zoomIn',
    '=':       'zoomIn',
    '-':       'zoomOut',
    'F3':      'debug',
    '`':       'debug'
  };

  class App {
    constructor() {
      this.renderer = null;
      this.ui = null;
      this.gallery = null;
      this.tutorial = null;
      this.synth = null;
      this.music = null;
      this.dataLayer = null;
      this.currentRoute = '/';
      this.gameState = null;
      this.debug = false;
      this._fpsFrames = 0;
      this._fpsTime = 0;
      this._fpsDisplay = 0;
      this._rafId = null;
      this._lastTimestamp = 0;
      this._resizeTimer = null;
      this._running = false;
    }

    _initSystems() {
      var CT = window.CT;

      if (CT.DataLayer) {
        this.dataLayer = new CT.DataLayer();
      } else {
        this.dataLayer = {
          _store: {},
          get: function(k) {
            try { return localStorage.getItem(k); } catch(e) { return this._store[k] || null; }
          },
          set: function(k, v) {
            this._store[k] = v;
            try { localStorage.setItem(k, v); } catch(e) { /* quota */ }
          },
          remove: function(k) {
            delete this._store[k];
            try { localStorage.removeItem(k); } catch(e) { /* ignore */ }
          }
        };
      }

      var canvasContainer = document.querySelector('.ct-canvas-container') ||
                            document.getElementById('ct-canvas') ||
                            document.body;
      if (CT.CanvasRenderer) {
        this.renderer = new CT.CanvasRenderer(canvasContainer);
      }

      if (CT.UISystem) {
        this.ui = new CT.UISystem();
      }

      if (CT.Gallery) {
        this.gallery = new CT.Gallery(this.dataLayer);
        this.gallery.load();
      }

      if (CT.TutorialSystem) {
        this.tutorial = new CT.TutorialSystem(this.dataLayer);
      }

      if (CT.AudioSynthesizer) {
        this.synth = new CT.AudioSynthesizer();
      }

      if (CT.GenerativeMusic) {
        this.music = new CT.GenerativeMusic(this.synth);
      }
    }

    _initRouter() {
      var self = this;
      window.addEventListener('hashchange', function() {
        var parsed = self._parseRoute(window.location.hash);
        self.currentRoute = parsed.route;
        self.renderPage(parsed.route, parsed.params);
      });
      var initial = this._parseRoute(window.location.hash);
      this.currentRoute = initial.route;
      this.renderPage(initial.route, initial.params);
    }

    _parseRoute(hash) {
      hash = hash || '#/';
      var path = hash.replace('#', '') || '/';
      var parts = path.split('?');
      var route = parts[0] || '/';
      var params = {};
      if (parts[1]) {
        var pairs = parts[1].split('&');
        for (var i = 0; i < pairs.length; i++) {
          var kv = pairs[i].split('=');
          params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || '');
        }
      }
      return { route: route, params: params };
    }

    renderPage(route, params) {
      var pageName = ROUTES[route] || 'welcome';
      var sections = document.querySelectorAll('[data-page]');
      for (var i = 0; i < sections.length; i++) {
        sections[i].style.display = 'none';
      }
      var active = document.querySelector('[data-page="' + pageName + '"]');
      if (active) active.style.display = '';

      var navLinks = document.querySelectorAll('.ct-nav-link');
      for (var ni = 0; ni < navLinks.length; ni++) {
        navLinks[ni].classList.remove('active');
        var href = navLinks[ni].getAttribute('href') || '';
        if (href === '#' + route) navLinks[ni].classList.add('active');
      }

      if (pageName === 'welcome') this._renderWelcome();
      else if (pageName === 'play') this._renderPlay(params);
      else if (pageName === 'gallery') this._renderGallery();
      else if (pageName === 'tutorial') this._renderTutorial();
      else if (pageName === 'settings') this._renderSettings();
      else if (pageName === 'about') this._renderAbout();
    }

    _renderWelcome() {
      var container = document.querySelector('[data-page="welcome"]');
      if (!container || container.dataset.rendered) return;
      container.dataset.rendered = 'true';
      container.innerHTML = '';
      container.style.cssText = 'display:flex;flex-direction:column;align-items:center;' +
        'justify-content:center;min-height:80vh;text-align:center;padding:40px;';

      var title = document.createElement('h1');
      title.textContent = 'Chromaticity';
      title.style.cssText = 'font-size:48px;color:#4fc3f7;margin-bottom:12px;font-weight:300;';
      container.appendChild(title);

      var subtitle = document.createElement('p');
      subtitle.textContent = 'A generative strategy game of color, territory, and musical composition.';
      subtitle.style.cssText = 'font-size:16px;color:#999;margin-bottom:40px;max-width:500px;';
      container.appendChild(subtitle);

      var btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:16px;';

      var newBtn = document.createElement('button');
      newBtn.textContent = 'New Game';
      newBtn.style.cssText = 'padding:12px 32px;background:#4fc3f7;color:#000;border:none;' +
        'border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;' +
        'transition:background 0.15s;';
      newBtn.addEventListener('mouseover', function() { newBtn.style.background = '#81d4fa'; });
      newBtn.addEventListener('mouseout', function() { newBtn.style.background = '#4fc3f7'; });
      var self = this;
      newBtn.addEventListener('click', function() { self.newGame({}); });
      btnRow.appendChild(newBtn);

      var contBtn = document.createElement('button');
      contBtn.textContent = 'Continue';
      contBtn.style.cssText = 'padding:12px 32px;background:transparent;color:#4fc3f7;' +
        'border:1px solid #4fc3f7;border-radius:6px;font-size:15px;cursor:pointer;' +
        'transition:background 0.15s;';
      contBtn.addEventListener('mouseover', function() { contBtn.style.background = 'rgba(79,195,247,0.1)'; });
      contBtn.addEventListener('mouseout', function() { contBtn.style.background = 'transparent'; });
      contBtn.addEventListener('click', function() { window.location.hash = '#/play'; });
      btnRow.appendChild(contBtn);

      container.appendChild(btnRow);

      var tutLink = document.createElement('a');
      tutLink.textContent = 'First time? Start the tutorial';
      tutLink.href = '#/tutorial';
      tutLink.style.cssText = 'margin-top:24px;color:#888;font-size:13px;text-decoration:underline;';
      container.appendChild(tutLink);
    }

    _renderPlay(params) {
      if (this.renderer && !this.gameState) {
        this.newGame(params || {});
      }
    }

    _renderGallery() {
      if (!this.gallery) return;
      var container = document.querySelector('[data-page="gallery"] .ct-gallery-grid') ||
                      document.querySelector('[data-page="gallery"]');
      if (!container) return;
      this.gallery.load();
      this.gallery.renderGalleryGrid(container);
    }

    _renderTutorial() {
      if (this.tutorial && !this.tutorial.active) {
        this.tutorial.start();
      }
    }

    _renderSettings() {
      if (!this.ui) return;
      var container = document.querySelector('[data-page="settings"]');
      if (!container || container.dataset.rendered) return;
      container.dataset.rendered = 'true';
      container.innerHTML = '';

      var title = document.createElement('h2');
      title.textContent = 'Settings';
      title.style.cssText = 'color:#fff;margin-bottom:20px;';
      container.appendChild(title);

      var settings = {
        musicVolume:  { label: 'Music Volume', type: 'slider', min: 0, max: 100, value: 70 },
        sfxVolume:    { label: 'SFX Volume', type: 'slider', min: 0, max: 100, value: 80 },
        showMinimap:  { label: 'Show Minimap', type: 'toggle', value: true },
        showFPS:      { label: 'Show FPS', type: 'toggle', value: false },
        gridSize:     { label: 'Grid Size', type: 'select', value: 'medium',
                        options: ['small', 'medium', 'large', 'huge'] },
        difficulty:   { label: 'Difficulty', type: 'select', value: 'normal',
                        options: ['easy', 'normal', 'hard', 'expert'] },
        particleEffects: { label: 'Particle Effects', type: 'toggle', value: true }
      };

      var self = this;
      var settingsEl = this.ui.renderSettings(settings, function(key, value) {
        if (key === 'musicVolume' && self.music) self.music.setVolume(value / 100);
        else if (key === 'sfxVolume' && self.synth) self.synth.setVolume(value / 100);
        else if (key === 'showMinimap' && self.renderer) self.renderer.showMinimap = value;
        else if (key === 'showFPS') self.debug = value;
      });
      container.appendChild(settingsEl);
    }

    _renderAbout() {
      var container = document.querySelector('[data-page="about"]');
      if (!container || container.dataset.rendered) return;
      container.dataset.rendered = 'true';
      container.innerHTML = '';
      container.style.cssText = 'padding:40px;max-width:700px;margin:0 auto;';

      var title = document.createElement('h2');
      title.textContent = 'About Chromaticity';
      title.style.cssText = 'color:#fff;margin-bottom:16px;';
      container.appendChild(title);

      var desc = document.createElement('p');
      desc.textContent = 'Chromaticity is a generative strategy game where color, territory, ' +
        'and music intertwine. Expand your territory, compose harmonious palettes, and listen ' +
        'as the game world creates a unique soundtrack for every match.';
      desc.style.cssText = 'color:#aaa;line-height:1.7;margin-bottom:24px;';
      container.appendChild(desc);

      var features = [
        { title: 'Hex Grid Strategy', desc: 'Claim and expand territories on a hex grid with terrain-based tactics.' },
        { title: 'Generative Music', desc: 'The soundtrack evolves with your gameplay \u2014 colors drive harmony.' },
        { title: 'Gallery', desc: 'Capture and share beautiful board states as art pieces.' },
        { title: 'Guided Tutorial', desc: 'Step-by-step guide to master the mechanics.' }
      ];

      var grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:16px;';
      for (var i = 0; i < features.length; i++) {
        var card = document.createElement('div');
        card.style.cssText = 'background:#16213e;border-radius:8px;padding:16px;';
        var ct = document.createElement('h4');
        ct.textContent = features[i].title;
        ct.style.cssText = 'color:#4fc3f7;margin:0 0 8px;';
        card.appendChild(ct);
        var cd = document.createElement('p');
        cd.textContent = features[i].desc;
        cd.style.cssText = 'color:#999;font-size:13px;margin:0;line-height:1.5;';
        card.appendChild(cd);
        grid.appendChild(card);
      }
      container.appendChild(grid);
    }

    newGame(options) {
      options = options || {};
      this.gameState = {
        turn: 1,
        phase: 'setup',
        players: options.players || [
          { id: 1, name: 'Player 1', color: '#4fc3f7' },
          { id: 2, name: 'AI', color: '#ff7043' }
        ],
        currentPlayer: 0,
        territories: [],
        score: 0,
        chromaticity: 0,
        gridCols: options.gridCols || 20,
        gridRows: options.gridRows || 15
      };
      window.location.hash = '#/play';

      if (this.music && this.synth) {
        this.music.mapGameState(this.gameState);
        this.music.playSFX('notification');
      }
    }

    _initKeyboard() {
      var self = this;
      document.addEventListener('keydown', function(e) {
        var tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

        var action = KEY_BINDINGS[e.key];
        if (!action) return;

        e.preventDefault();
        var panAmount = 30;
        var zoomFactor = 1.15;

        if (action === 'endTurn') {
          if (self.gameState) {
            self.gameState.turn++;
            if (self.ui) self.ui.updateHUD({ turn: self.gameState.turn });
            if (self.music) self.music.playSFX('click');
          }
        } else if (action === 'cancel') {
          if (self.ui) {
            self.ui.closeModal();
            self.ui._closeContextMenu();
          }
          if (self.tutorial && self.tutorial.active) {
            self.tutorial.skip();
          }
        } else if (action === 'toggleMinimap') {
          if (self.renderer) self.renderer.showMinimap = !self.renderer.showMinimap;
        } else if (action === 'gallery') {
          window.location.hash = '#/gallery';
        } else if (action === 'capture') {
          if (self.gallery && self.renderer) {
            var terrain = self.renderer.layers['terrain'];
            if (terrain) {
              var dataUrl = self.gallery.capture(terrain);
              if (dataUrl) {
                self.gallery.save({
                  dataUrl: dataUrl,
                  title: 'Turn ' + (self.gameState ? self.gameState.turn : 0),
                  score: self.gameState ? self.gameState.score : 0
                });
                if (self.ui) self.ui.showToast('Captured to gallery!', 'success');
                if (self.music) self.music.playSFX('notification');
              }
            }
          }
        } else if (action === 'panUp') {
          if (self.renderer) self.renderer.pan(0, -panAmount);
        } else if (action === 'panDown') {
          if (self.renderer) self.renderer.pan(0, panAmount);
        } else if (action === 'panLeft') {
          if (self.renderer) self.renderer.pan(-panAmount, 0);
        } else if (action === 'panRight') {
          if (self.renderer) self.renderer.pan(panAmount, 0);
        } else if (action === 'zoomIn') {
          if (self.renderer) self.renderer.zoom(zoomFactor);
        } else if (action === 'zoomOut') {
          if (self.renderer) self.renderer.zoom(1 / zoomFactor);
        } else if (action === 'debug') {
          self.debug = !self.debug;
        } else if (action.indexOf('action') === 0) {
          var num = parseInt(action.replace('action', ''), 10);
          if (self.music) self.music.playSFX('click');
        }
      });
    }

    _handleResize() {
      var self = this;
      window.addEventListener('resize', function() {
        if (self._resizeTimer) clearTimeout(self._resizeTimer);
        self._resizeTimer = setTimeout(function() {
          if (self.renderer) self.renderer.resize();
        }, 150);
      });
    }

    _initFPSCounter() {
      this._fpsFrames = 0;
      this._fpsTime = performance.now();
      this._fpsDisplay = 0;
    }

    _gameLoop(timestamp) {
      if (!this._running) return;
      var dt = timestamp - this._lastTimestamp;
      this._lastTimestamp = timestamp;

      if (dt > 100) dt = 16;

      if (this.renderer) {
        this.renderer.render(dt);
      }

      this._fpsFrames++;
      var elapsed = timestamp - this._fpsTime;
      if (elapsed >= 1000) {
        this._fpsDisplay = Math.round(this._fpsFrames * 1000 / elapsed);
        this._fpsFrames = 0;
        this._fpsTime = timestamp;
      }

      if (this.debug && this.renderer) {
        var ctx = this.renderer.contexts['ui'];
        if (ctx) {
          ctx.save();
          ctx.setTransform(this.renderer.dpr, 0, 0, this.renderer.dpr, 0, 0);
          ctx.fillStyle = '#0f0';
          ctx.font = '12px monospace';
          ctx.fillText('FPS: ' + this._fpsDisplay, 8, 16);
          ctx.fillText('Viewport: ' +
            Math.round(this.renderer.viewport.x) + ',' +
            Math.round(this.renderer.viewport.y) + ' z' +
            this.renderer.viewport.zoom.toFixed(2), 8, 30);
          if (this.gameState) {
            ctx.fillText('Turn: ' + this.gameState.turn + ' Phase: ' + this.gameState.phase, 8, 44);
          }
          ctx.restore();
        }
      }

      var self = this;
      this._rafId = requestAnimationFrame(function(ts) { self._gameLoop(ts); });
    }

    start() {
      this._initSystems();
      this._initRouter();
      this._initKeyboard();
      this._handleResize();
      this._initFPSCounter();

      this._running = true;
      this._lastTimestamp = performance.now();
      var self = this;
      this._rafId = requestAnimationFrame(function(ts) { self._gameLoop(ts); });
    }
  }

  window.CT = window.CT || {};
  window.CT.App = App;

  document.addEventListener('DOMContentLoaded', function() {
    var app = new CT.App();
    app.start();
  });
})();
"""
    return (js, "", "")
