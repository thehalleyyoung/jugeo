"""JavaScript UI/media layer constants for Chromatic Territories.

Canvas renderer, panel system, gallery, tutorial, audio synthesis,
generative music, and application initialization.  All classes are
attached to the window.CT namespace.
"""


# =================================================================
# 1. CANVAS RENDERER - Multi-layer hex grid renderer
# =================================================================

CANVAS_RENDERER_JS = """\
/* ================================================================
   Chromatic Territories — Canvas Renderer
   Multi-layer rendering engine for hex-grid game/art canvas.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  const SQRT3 = Math.sqrt(3);

  /* -------------------- Hex math helpers -------------------- */
  function hexCorner(cx, cy, size, i) {
    const angleDeg = 60 * i;
    const angleRad = (Math.PI / 180) * angleDeg;
    return { x: cx + size * Math.cos(angleRad), y: cy + size * Math.sin(angleRad) };
  }

  function hexToPixel(q, r, size) {
    const x = size * (3 / 2) * q;
    const y = size * (SQRT3 / 2 * q + SQRT3 * r);
    return { x, y };
  }

  function pixelToHex(px, py, size) {
    const q = (2 / 3 * px) / size;
    const r = (-1 / 3 * px + SQRT3 / 3 * py) / size;
    return cubeRound(q, -q - r, r);
  }

  function cubeRound(x, y, z) {
    let rx = Math.round(x);
    let ry = Math.round(y);
    let rz = Math.round(z);
    const xDiff = Math.abs(rx - x);
    const yDiff = Math.abs(ry - y);
    const zDiff = Math.abs(rz - z);
    if (xDiff > yDiff && xDiff > zDiff) { rx = -ry - rz; }
    else if (yDiff > zDiff)             { ry = -rx - rz; }
    else                                { rz = -rx - ry; }
    return { q: rx, r: rz };
  }

  /* -------------------- CanvasRenderer -------------------- */
  class CanvasRenderer {
    constructor(containerId) {
      this.container = document.getElementById(containerId);
      if (!this.container) {
        throw new Error(`CanvasRenderer: container "#${containerId}" not found`);
      }

      this.layers = {};
      this.layerOrder = ['terrain', 'territory', 'effects', 'ui'];
      this.layerOrder.forEach((name, idx) => {
        const canvas = document.createElement('canvas');
        canvas.id = `ct-canvas-${name}`;
        canvas.className = 'ct-canvas-layer';
        canvas.dataset.layer = name;
        canvas.style.position = 'absolute';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.zIndex = idx;
        this.container.appendChild(canvas);
        this.layers[name] = {
          canvas,
          ctx: canvas.getContext('2d'),
          dirty: true
        };
      });

      this.hexSize = 30;
      this.viewport = { x: 0, y: 0, zoom: 1, targetX: 0, targetY: 0, targetZoom: 1 };
      this.animations = [];
      this.dirtyRects = [];
      this._running = false;
      this._lastTime = 0;
      this._frameCount = 0;
      this._fpsTime = 0;
      this.fps = 0;

      this.handleResize = this.handleResize.bind(this);
      window.addEventListener('resize', this.handleResize);
      this.handleResize();

      this._setupInteraction();
    }

    /* ---- Layer management ---- */
    getLayer(name) { return this.layers[name]; }

    clearLayer(name) {
      const layer = this.layers[name];
      if (!layer) return;
      layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
      layer.dirty = false;
    }

    clearAll() {
      for (const name of this.layerOrder) { this.clearLayer(name); }
    }

    markDirty(name) {
      if (this.layers[name]) this.layers[name].dirty = true;
    }

    markAllDirty() {
      for (const name of this.layerOrder) { this.layers[name].dirty = true; }
    }

    needsRedraw() {
      return this.layerOrder.some(n => this.layers[n].dirty);
    }

    /* ---- HiDPI / Resize ---- */
    setupHiDPI(canvas) {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.container.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);
      return ctx;
    }

    handleResize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.container.getBoundingClientRect();
      for (const name of this.layerOrder) {
        const c = this.layers[name].canvas;
        c.width = rect.width * dpr;
        c.height = rect.height * dpr;
        const ctx = c.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.layers[name].ctx = ctx;
      }
      this.width = rect.width;
      this.height = rect.height;
      this.markAllDirty();
    }

    /* ---- Viewport transforms ---- */
    pan(dx, dy) {
      this.viewport.targetX += dx;
      this.viewport.targetY += dy;
      this.markAllDirty();
    }

    zoomTo(level, cx, cy) {
      const clamped = Math.max(0.3, Math.min(3, level));
      if (cx !== undefined && cy !== undefined) {
        const wx = (cx - this.viewport.x) / this.viewport.zoom;
        const wy = (cy - this.viewport.y) / this.viewport.zoom;
        this.viewport.targetX = cx - wx * clamped;
        this.viewport.targetY = cy - wy * clamped;
      }
      this.viewport.targetZoom = clamped;
      this.markAllDirty();
    }

    smoothTransition(dt) {
      const speed = 1 - Math.pow(0.001, dt);
      this.viewport.x += (this.viewport.targetX - this.viewport.x) * speed;
      this.viewport.y += (this.viewport.targetY - this.viewport.y) * speed;
      this.viewport.zoom += (this.viewport.targetZoom - this.viewport.zoom) * speed;
      const dx = Math.abs(this.viewport.targetX - this.viewport.x);
      const dy = Math.abs(this.viewport.targetY - this.viewport.y);
      const dz = Math.abs(this.viewport.targetZoom - this.viewport.zoom);
      if (dx > 0.1 || dy > 0.1 || dz > 0.001) { this.markAllDirty(); }
    }

    screenToWorld(sx, sy) {
      return {
        x: (sx - this.viewport.x) / this.viewport.zoom,
        y: (sy - this.viewport.y) / this.viewport.zoom
      };
    }

    worldToScreen(wx, wy) {
      return {
        x: wx * this.viewport.zoom + this.viewport.x,
        y: wy * this.viewport.zoom + this.viewport.y
      };
    }

    applyViewport(ctx) {
      ctx.save();
      ctx.translate(this.viewport.x, this.viewport.y);
      ctx.scale(this.viewport.zoom, this.viewport.zoom);
    }

    restoreViewport(ctx) { ctx.restore(); }

    /* ---- Hex drawing primitives ---- */
    drawHexPath(ctx, cx, cy, size) {
      ctx.beginPath();
      for (let i = 0; i < 6; i++) {
        const c = hexCorner(cx, cy, size, i);
        if (i === 0) ctx.moveTo(c.x, c.y);
        else ctx.lineTo(c.x, c.y);
      }
      ctx.closePath();
    }

    drawHexFill(ctx, q, r, color) {
      const { x, y } = hexToPixel(q, r, this.hexSize);
      this.drawHexPath(ctx, x, y, this.hexSize - 1);
      ctx.fillStyle = color;
      ctx.fill();
    }

    drawHexOutline(ctx, q, r, color, width) {
      const { x, y } = hexToPixel(q, r, this.hexSize);
      this.drawHexPath(ctx, x, y, this.hexSize - 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = width || 1;
      ctx.stroke();
    }

    drawHexBorder(ctx, q, r, color, thickness) {
      const { x, y } = hexToPixel(q, r, this.hexSize);
      this.drawHexPath(ctx, x, y, this.hexSize + 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = thickness || 3;
      ctx.stroke();
    }

    /* ---- Terrain rendering ---- */
    renderTerrain(grid) {
      const layer = this.layers.terrain;
      if (!layer.dirty) return;
      this.clearLayer('terrain');
      const ctx = layer.ctx;
      this.applyViewport(ctx);

      const noiseEngine = CT.NoiseEngine ? new CT.NoiseEngine() : null;

      for (const cell of grid.cells || []) {
        const { x, y } = hexToPixel(cell.q, cell.r, this.hexSize);
        let height = cell.height;
        if (height === undefined && noiseEngine) {
          height = noiseEngine.simplex2(cell.q * 0.08, cell.r * 0.08) * 0.5 + 0.5;
        } else if (height === undefined) {
          height = (Math.sin(cell.q * 0.3) * Math.cos(cell.r * 0.3) + 1) * 0.5;
        }
        const color = this._heightToColor(height);
        this.drawHexPath(ctx, x, y, this.hexSize - 1);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }
      this.restoreViewport(ctx);
      layer.dirty = false;
    }

    _heightToColor(h) {
      if (h < 0.25) return `rgb(${30 + h * 200 | 0}, ${60 + h * 160 | 0}, ${120 + h * 200 | 0})`;
      if (h < 0.45) return `rgb(${60 + (h - 0.25) * 400 | 0}, ${140 + (h - 0.25) * 300 | 0}, ${60})`;
      if (h < 0.65) return `rgb(${100 + (h - 0.45) * 350 | 0}, ${120 + (h - 0.45) * 200 | 0}, ${60 + (h - 0.45) * 200 | 0})`;
      if (h < 0.8)  return `rgb(${150 + (h - 0.65) * 300 | 0}, ${130 + (h - 0.65) * 300 | 0}, ${110 + (h - 0.65) * 300 | 0})`;
      return `rgb(${220 + (h - 0.8) * 175 | 0}, ${220 + (h - 0.8) * 175 | 0}, ${230 + (h - 0.8) * 125 | 0})`;
    }

    /* ---- Territory rendering ---- */
    renderTerritories(territories) {
      const layer = this.layers.territory;
      if (!layer.dirty) return;
      this.clearLayer('territory');
      const ctx = layer.ctx;
      this.applyViewport(ctx);

      if (!territories) { this.restoreViewport(ctx); layer.dirty = false; return; }

      for (const terr of territories) {
        const color = terr.color || '#6366f1';
        for (const cell of terr.cells || []) {
          this.drawHexFill(ctx, cell.q, cell.r, color);
        }

        for (const cell of terr.cells || []) {
          const { x, y } = hexToPixel(cell.q, cell.r, this.hexSize);
          const neighbors = this._getNeighbors(cell.q, cell.r);
          for (const nb of neighbors) {
            const nbTerr = this._findTerritory(territories, nb.q, nb.r);
            if (nbTerr && nbTerr !== terr) {
              const nbPos = hexToPixel(nb.q, nb.r, this.hexSize);
              const grad = ctx.createLinearGradient(x, y, nbPos.x, nbPos.y);
              grad.addColorStop(0, color);
              grad.addColorStop(1, nbTerr.color || '#6366f1');
              const mx = (x + nbPos.x) / 2;
              const my = (y + nbPos.y) / 2;
              ctx.beginPath();
              ctx.arc(mx, my, this.hexSize * 0.3, 0, Math.PI * 2);
              ctx.fillStyle = grad;
              ctx.globalAlpha = 0.3;
              ctx.fill();
              ctx.globalAlpha = 1;
            }
          }
        }
      }
      this.restoreViewport(ctx);
      layer.dirty = false;
    }

    _getNeighbors(q, r) {
      return [
        { q: q + 1, r: r },     { q: q - 1, r: r },
        { q: q, r: r + 1 },     { q: q, r: r - 1 },
        { q: q + 1, r: r - 1 }, { q: q - 1, r: r + 1 }
      ];
    }

    _findTerritory(territories, q, r) {
      for (const t of territories) {
        if (t.cells && t.cells.some(c => c.q === q && c.r === r)) return t;
      }
      return null;
    }

    /* ---- Particle effects ---- */
    renderParticles(particles) {
      const layer = this.layers.effects;
      this.clearLayer('effects');
      const ctx = layer.ctx;
      this.applyViewport(ctx);

      for (const p of particles || []) {
        const alpha = Math.max(0, 1 - p.elapsed / p.lifetime);
        ctx.globalAlpha = alpha;

        if (p.type === 'spark') {
          ctx.fillStyle = p.color || '#fbbf24';
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size || 2, 0, Math.PI * 2);
          ctx.fill();
        } else if (p.type === 'glow') {
          const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size || 10);
          grad.addColorStop(0, p.color || 'rgba(99,102,241,0.6)');
          grad.addColorStop(1, 'rgba(99,102,241,0)');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size || 10, 0, Math.PI * 2);
          ctx.fill();
        } else if (p.type === 'trail') {
          ctx.strokeStyle = p.color || '#6366f1';
          ctx.lineWidth = p.size || 1;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.x - p.vx * 3, p.y - p.vy * 3);
          ctx.stroke();
        } else {
          ctx.fillStyle = p.color || '#ffffff';
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size || 2, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
      this.restoreViewport(ctx);
    }

    /* ---- Post-processing ---- */
    applyBloom(layerName, radius, intensity) {
      const layer = this.layers[layerName];
      if (!layer) return;
      const ctx = layer.ctx;
      const w = layer.canvas.width;
      const h = layer.canvas.height;
      if (w === 0 || h === 0) return;

      try {
        const imageData = ctx.getImageData(0, 0, w, h);
        const data = imageData.data;
        const r2 = Math.ceil(radius || 4);
        const weights = [];
        let sum = 0;
        for (let i = -r2; i <= r2; i++) {
          const w = Math.exp(-(i * i) / (2 * r2 * r2));
          weights.push(w);
          sum += w;
        }
        for (let i = 0; i < weights.length; i++) weights[i] /= sum;

        const temp = new Uint8ClampedArray(data.length);
        const int = intensity || 0.5;
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            let rr = 0, gg = 0, bb = 0;
            for (let k = -r2; k <= r2; k++) {
              const sx = Math.min(w - 1, Math.max(0, x + k));
              const idx = (y * w + sx) * 4;
              const wt = weights[k + r2];
              rr += data[idx] * wt;
              gg += data[idx + 1] * wt;
              bb += data[idx + 2] * wt;
            }
            const idx = (y * w + x) * 4;
            temp[idx] = Math.min(255, data[idx] + rr * int);
            temp[idx + 1] = Math.min(255, data[idx + 1] + gg * int);
            temp[idx + 2] = Math.min(255, data[idx + 2] + bb * int);
            temp[idx + 3] = data[idx + 3];
          }
        }
        const result = new ImageData(temp, w, h);
        ctx.putImageData(result, 0, 0);
      } catch (e) { /* cross-origin or empty canvas */ }
    }

    applyVignette(layerName) {
      const layer = this.layers[layerName];
      if (!layer) return;
      const ctx = layer.ctx;
      const w = this.width;
      const h = this.height;
      const cx = w / 2;
      const cy = h / 2;
      const r = Math.max(cx, cy) * 1.2;
      const grad = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r);
      grad.addColorStop(0, 'rgba(0,0,0,0)');
      grad.addColorStop(1, 'rgba(0,0,0,0.5)');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }

    /* ---- Minimap ---- */
    renderMinimap(grid, territories) {
      const mmCanvas = document.getElementById('ct-minimap-canvas');
      if (!mmCanvas) return;
      const mmCtx = mmCanvas.getContext('2d');
      const mmW = 200;
      const mmH = 150;
      mmCanvas.width = mmW;
      mmCanvas.height = mmH;
      mmCtx.clearRect(0, 0, mmW, mmH);
      mmCtx.fillStyle = '#0d0d22';
      mmCtx.fillRect(0, 0, mmW, mmH);

      if (!grid || !grid.cells || grid.cells.length === 0) return;

      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const cell of grid.cells) {
        const p = hexToPixel(cell.q, cell.r, this.hexSize);
        if (p.x < minX) minX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.x > maxX) maxX = p.x;
        if (p.y > maxY) maxY = p.y;
      }
      const gridW = maxX - minX + this.hexSize * 2;
      const gridH = maxY - minY + this.hexSize * 2;
      const scale = Math.min(mmW / gridW, mmH / gridH) * 0.9;
      const offX = (mmW - gridW * scale) / 2 - minX * scale + this.hexSize * scale;
      const offY = (mmH - gridH * scale) / 2 - minY * scale + this.hexSize * scale;

      for (const cell of grid.cells) {
        const p = hexToPixel(cell.q, cell.r, this.hexSize);
        const sx = p.x * scale + offX;
        const sy = p.y * scale + offY;
        const sr = this.hexSize * scale * 0.7;

        let color = '#2a2a4a';
        if (territories) {
          for (const t of territories) {
            if (t.cells && t.cells.some(c => c.q === cell.q && c.r === cell.r)) {
              color = t.color || '#6366f1';
              break;
            }
          }
        }
        mmCtx.fillStyle = color;
        mmCtx.beginPath();
        mmCtx.arc(sx, sy, sr, 0, Math.PI * 2);
        mmCtx.fill();
      }

      const vpRect = document.getElementById('ct-minimap-viewport');
      if (vpRect) {
        const vpLeft = (-this.viewport.x / this.viewport.zoom) * scale + offX;
        const vpTop = (-this.viewport.y / this.viewport.zoom) * scale + offY;
        const vpW = (this.width / this.viewport.zoom) * scale;
        const vpH = (this.height / this.viewport.zoom) * scale;
        vpRect.style.left = vpLeft + 'px';
        vpRect.style.top = vpTop + 'px';
        vpRect.style.width = vpW + 'px';
        vpRect.style.height = vpH + 'px';
      }
    }

    /* ---- Animation queue ---- */
    queueAnimation(anim) {
      anim.elapsed = 0;
      this.animations.push(anim);
    }

    updateAnimations(dt) {
      for (let i = this.animations.length - 1; i >= 0; i--) {
        const a = this.animations[i];
        a.elapsed += dt;
        const t = Math.min(1, a.elapsed / (a.duration || 0.5));
        const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        if (a.type === 'colorTransition' && a.hex) {
          a.hex._animColor = this._lerpColor(a.from, a.to, eased);
          this.markDirty('territory');
        }
        if (t >= 1) {
          if (a.callback) a.callback();
          this.animations.splice(i, 1);
        }
      }
    }

    _lerpColor(c1, c2, t) {
      const r1 = parseInt(c1.slice(1, 3), 16);
      const g1 = parseInt(c1.slice(3, 5), 16);
      const b1 = parseInt(c1.slice(5, 7), 16);
      const r2 = parseInt(c2.slice(1, 3), 16);
      const g2 = parseInt(c2.slice(3, 5), 16);
      const b2 = parseInt(c2.slice(5, 7), 16);
      const r = Math.round(r1 + (r2 - r1) * t);
      const g = Math.round(g1 + (g2 - g1) * t);
      const b = Math.round(b1 + (b2 - b1) * t);
      return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }

    /* ---- Interaction ---- */
    _setupInteraction() {
      const uiCanvas = this.layers.ui.canvas;
      let dragging = false;
      let lastX = 0, lastY = 0;

      uiCanvas.addEventListener('mousedown', (e) => {
        dragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
      });

      window.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const dx = e.clientX - lastX;
        const dy = e.clientY - lastY;
        this.pan(dx, dy);
        lastX = e.clientX;
        lastY = e.clientY;
      });

      window.addEventListener('mouseup', () => { dragging = false; });

      uiCanvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        const rect = uiCanvas.getBoundingClientRect();
        const cx = e.clientX - rect.left;
        const cy = e.clientY - rect.top;
        this.zoomTo(this.viewport.targetZoom + delta, cx, cy);
      }, { passive: false });

      uiCanvas.addEventListener('click', (e) => {
        const rect = uiCanvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        const world = this.screenToWorld(sx, sy);
        const hex = pixelToHex(world.x, world.y, this.hexSize);
        const event = new CustomEvent('ct-hex-click', { detail: hex });
        document.dispatchEvent(event);
      });
    }

    /* ---- Render loop ---- */
    startRenderLoop() {
      this._running = true;
      this._lastTime = performance.now();
      this._fpsTime = this._lastTime;
      this._frameCount = 0;
      const loop = (now) => {
        if (!this._running) return;
        const dt = (now - this._lastTime) / 1000;
        this._lastTime = now;

        this._frameCount++;
        if (now - this._fpsTime >= 1000) {
          this.fps = this._frameCount;
          this._frameCount = 0;
          this._fpsTime = now;
        }

        this.smoothTransition(dt);
        this.updateAnimations(dt);

        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }

    stopRenderLoop() { this._running = false; }

    /* ---- Full render ---- */
    render(gameState) {
      if (!gameState) return;
      if (gameState.grid) this.renderTerrain(gameState.grid);
      if (gameState.territories) this.renderTerritories(gameState.territories);
      if (gameState.particles) this.renderParticles(gameState.particles);
      if (gameState.grid) this.renderMinimap(gameState.grid, gameState.territories);
    }

    /* ---- Pixel-to-hex public API ---- */
    screenToHex(sx, sy) {
      const world = this.screenToWorld(sx, sy);
      return pixelToHex(world.x, world.y, this.hexSize);
    }

    /* ---- Cleanup ---- */
    destroy() {
      this.stopRenderLoop();
      window.removeEventListener('resize', this.handleResize);
      for (const name of this.layerOrder) {
        const c = this.layers[name].canvas;
        if (c.parentNode) c.parentNode.removeChild(c);
      }
      this.layers = {};
    }
  }

  /* Expose hex helpers */
  CanvasRenderer.hexToPixel = hexToPixel;
  CanvasRenderer.pixelToHex = pixelToHex;

  CT.CanvasRenderer = CanvasRenderer;
})();
"""


# =================================================================
# 2. UI SYSTEM - Panels, modals, tooltips, HUD
# =================================================================

UI_SYSTEM_JS = """\
/* ================================================================
   Chromatic Territories — UI System
   Panel manager, modals, tooltips, HUD, notifications.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  class UISystem {
    constructor() {
      this.panels = new Map();
      this.modalStack = [];
      this.tooltipEl = null;
      this.tooltipTimer = null;
      this.toasts = [];
      this.toastContainer = document.getElementById('ct-toast-container');
      this.contextMenuEl = document.getElementById('ct-context-menu');

      this._initTooltip();
      this._initContextMenu();
      this._initResponsive();
    }

    /* ==================== Panel Manager ==================== */
    createPanel({ id, title, x, y, width, height, content, closable, minimizable }) {
      if (this.panels.has(id)) { this.closePanel(id); }

      const panel = document.createElement('div');
      panel.id = `ct-panel-${id}`;
      panel.className = 'ct-panel';
      panel.style.left = (x || 100) + 'px';
      panel.style.top = (y || 100) + 'px';
      if (width) panel.style.width = width + 'px';
      if (height) panel.style.height = height + 'px';

      const header = document.createElement('div');
      header.className = 'ct-panel__header';

      const titleEl = document.createElement('h3');
      titleEl.className = 'ct-panel__title';
      titleEl.textContent = title || 'Panel';
      header.appendChild(titleEl);

      const controls = document.createElement('div');
      controls.className = 'ct-panel__controls';

      if (minimizable !== false) {
        const minBtn = document.createElement('button');
        minBtn.className = 'ct-panel__btn';
        minBtn.innerHTML = '&#8722;';
        minBtn.title = 'Minimize';
        minBtn.addEventListener('click', () => this.toggleMinimize(id));
        controls.appendChild(minBtn);
      }

      if (closable !== false) {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'ct-panel__btn';
        closeBtn.innerHTML = '&times;';
        closeBtn.title = 'Close';
        closeBtn.addEventListener('click', () => this.closePanel(id));
        controls.appendChild(closeBtn);
      }

      header.appendChild(controls);
      panel.appendChild(header);

      const body = document.createElement('div');
      body.className = 'ct-panel__body';
      if (typeof content === 'string') body.innerHTML = content;
      else if (content instanceof HTMLElement) body.appendChild(content);
      panel.appendChild(body);

      this._makeDraggable(panel, header);

      const container = document.getElementById('ct-panels') || document.body;
      container.appendChild(panel);
      this.panels.set(id, { el: panel, minimized: false });
      return panel;
    }

    closePanel(id) {
      const info = this.panels.get(id);
      if (!info) return;
      info.el.remove();
      this.panels.delete(id);
    }

    toggleMinimize(id) {
      const info = this.panels.get(id);
      if (!info) return;
      info.minimized = !info.minimized;
      info.el.classList.toggle('ct-panel--minimized', info.minimized);
    }

    togglePanel(id) {
      const info = this.panels.get(id);
      if (info) { this.closePanel(id); }
    }

    _makeDraggable(el, handle) {
      let dragging = false, startX = 0, startY = 0, origX = 0, origY = 0;

      handle.addEventListener('mousedown', (e) => {
        if (e.target.closest('.ct-panel__btn')) return;
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origX = el.offsetLeft;
        origY = el.offsetTop;
        e.preventDefault();
      });

      window.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        el.style.left = (origX + e.clientX - startX) + 'px';
        el.style.top = (origY + e.clientY - startY) + 'px';
      });

      window.addEventListener('mouseup', () => { dragging = false; });
    }

    /* ==================== Modal System ==================== */
    showModal({ title, content, buttons, onClose, size }) {
      const backdrop = document.getElementById('ct-modal-backdrop');
      const modal = document.getElementById('ct-modal');
      const titleEl = document.getElementById('ct-modal-title');
      const bodyEl = document.getElementById('ct-modal-body');
      const footerEl = document.getElementById('ct-modal-footer');

      if (!backdrop || !modal) return;

      titleEl.textContent = title || '';
      bodyEl.innerHTML = '';
      footerEl.innerHTML = '';

      if (typeof content === 'string') bodyEl.innerHTML = content;
      else if (content instanceof HTMLElement) bodyEl.appendChild(content);

      if (size) modal.className = `ct-modal ct-modal--${size}`;
      else modal.className = 'ct-modal';

      if (buttons && buttons.length) {
        for (const btn of buttons) {
          const b = document.createElement('button');
          b.className = `ct-btn ct-btn--${btn.type || 'ghost'}`;
          b.textContent = btn.label;
          b.addEventListener('click', () => {
            if (btn.action) btn.action();
            if (btn.close !== false) this.closeModal();
          });
          footerEl.appendChild(b);
        }
      }

      backdrop.classList.add('ct-modal-backdrop--active');
      backdrop.setAttribute('aria-hidden', 'false');

      this.modalStack.push({ onClose });

      const closeBtn = document.getElementById('ct-modal-close');
      closeBtn.onclick = () => this.closeModal();
      backdrop.onclick = (e) => {
        if (e.target === backdrop) this.closeModal();
      };
    }

    closeModal() {
      const backdrop = document.getElementById('ct-modal-backdrop');
      if (!backdrop) return;
      const info = this.modalStack.pop();
      if (info && info.onClose) info.onClose();
      if (this.modalStack.length === 0) {
        backdrop.classList.remove('ct-modal-backdrop--active');
        backdrop.setAttribute('aria-hidden', 'true');
      }
    }

    /* ==================== Tooltip System ==================== */
    _initTooltip() {
      this.tooltipEl = document.getElementById('ct-tooltip');
      if (!this.tooltipEl) {
        this.tooltipEl = document.createElement('div');
        this.tooltipEl.id = 'ct-tooltip';
        this.tooltipEl.className = 'ct-tooltip';
        this.tooltipEl.setAttribute('role', 'tooltip');
        document.body.appendChild(this.tooltipEl);
      }

      document.addEventListener('mouseover', (e) => {
        const target = e.target.closest('[data-tooltip]');
        if (!target) return;
        clearTimeout(this.tooltipTimer);
        this.tooltipTimer = setTimeout(() => {
          this.showTooltip(target.dataset.tooltip, e.clientX, e.clientY);
        }, 300);
      });

      document.addEventListener('mouseout', (e) => {
        const target = e.target.closest('[data-tooltip]');
        if (target) this.hideTooltip();
      });

      document.addEventListener('mousemove', (e) => {
        if (this.tooltipEl.classList.contains('ct-tooltip--visible')) {
          this._positionTooltip(e.clientX + 12, e.clientY + 12);
        }
      });
    }

    showTooltip(text, x, y) {
      this.tooltipEl.textContent = text;
      this.tooltipEl.classList.add('ct-tooltip--visible');
      this._positionTooltip(x + 12, y + 12);
    }

    hideTooltip() {
      clearTimeout(this.tooltipTimer);
      this.tooltipEl.classList.remove('ct-tooltip--visible');
    }

    _positionTooltip(x, y) {
      const rect = this.tooltipEl.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      if (x + rect.width > vw - 10) x = vw - rect.width - 10;
      if (y + rect.height > vh - 10) y = vh - rect.height - 10;
      if (x < 10) x = 10;
      if (y < 10) y = 10;
      this.tooltipEl.style.left = x + 'px';
      this.tooltipEl.style.top = y + 'px';
    }

    /* ==================== HUD ==================== */
    renderHUD(gameState) {
      if (!gameState) return;

      const turnEl = document.getElementById('ct-turn-number');
      if (turnEl) turnEl.textContent = gameState.turn || 1;

      const totalEl = document.getElementById('ct-turn-total');
      if (totalEl) totalEl.textContent = gameState.maxTurns || '--';

      const roundEl = document.getElementById('ct-round-number');
      if (roundEl) roundEl.textContent = gameState.round || 1;

      const player = gameState.currentPlayer;
      if (player) {
        const swatchEl = document.getElementById('ct-player-swatch');
        if (swatchEl) swatchEl.style.backgroundColor = player.color || '#6366f1';
        const nameEl = document.getElementById('ct-player-name');
        if (nameEl) nameEl.textContent = player.name || 'Player';
        const typeEl = document.getElementById('ct-player-type');
        if (typeEl) typeEl.textContent = player.isAI ? 'AI' : 'Human';
      }

      const chromEl = document.getElementById('ct-chromaticity-value');
      if (chromEl) chromEl.textContent = gameState.chromaticity || 0;
      const chromMaxEl = document.getElementById('ct-chromaticity-max');
      if (chromMaxEl) chromMaxEl.textContent = gameState.maxChromaticity || 100;

      this._updateActionButtons(gameState);
    }

    _updateActionButtons(gameState) {
      const actions = ['expand', 'fortify', 'disrupt', 'harmonize', 'evolve'];
      for (const action of actions) {
        const btn = document.getElementById(`ct-action-${action}`);
        if (!btn) continue;
        const cost = parseInt(btn.dataset.cost || '0', 10);
        const available = (gameState.chromaticity || 0) >= cost;
        btn.classList.toggle('ct-action-btn--disabled', !available);
        btn.disabled = !available;
      }
    }

    /* ==================== Palette Selector ==================== */
    renderPaletteSelector(palettes, onSelect) {
      const container = document.getElementById('ct-palette-slots');
      if (!container) return;

      const slots = container.querySelectorAll('.ct-palette-swatch');
      slots.forEach((slot, idx) => {
        const color = palettes && palettes[idx];
        if (color) {
          slot.style.backgroundColor = color;
          slot.classList.remove('ct-palette-swatch--empty');
          slot.querySelector('.ct-palette-swatch__add').style.display = 'none';
          slot.querySelector('.ct-palette-swatch__remove').style.display = 'block';
        } else {
          slot.style.backgroundColor = '';
          slot.classList.add('ct-palette-swatch--empty');
          slot.querySelector('.ct-palette-swatch__add').style.display = 'block';
          slot.querySelector('.ct-palette-swatch__remove').style.display = 'none';
        }

        slot.onclick = () => {
          slots.forEach(s => s.classList.remove('ct-palette-swatch--selected'));
          slot.classList.add('ct-palette-swatch--selected');
          if (onSelect) onSelect(idx, color);
        };
      });
    }

    /* ==================== Action Bar ==================== */
    renderActionBar(actions, onAction) {
      const btns = document.querySelectorAll('.ct-action-btn');
      btns.forEach(btn => {
        const action = btn.dataset.action;
        if (!action) return;
        btn.onclick = () => {
          if (btn.disabled) return;
          btns.forEach(b => b.classList.remove('ct-action-btn--active'));
          btn.classList.add('ct-action-btn--active');
          if (onAction) onAction(action);
        };
      });
    }

    /* ==================== Territory Info ==================== */
    renderTerritoryInfo(hex) {
      const panel = document.getElementById('ct-territory-info');
      if (!panel) return;

      if (!hex) {
        panel.style.display = 'none';
        return;
      }

      panel.style.display = 'block';

      const ownerColor = document.getElementById('ct-hex-owner-color');
      if (ownerColor) ownerColor.style.backgroundColor = hex.ownerColor || 'transparent';

      const ownerName = document.getElementById('ct-hex-owner-name');
      if (ownerName) ownerName.textContent = hex.ownerName || 'Unclaimed';

      const qEl = document.getElementById('ct-hex-q');
      if (qEl) qEl.textContent = hex.q;
      const rEl = document.getElementById('ct-hex-r');
      if (rEl) rEl.textContent = hex.r;

      const terrainEl = document.getElementById('ct-hex-terrain');
      if (terrainEl) terrainEl.textContent = hex.terrain || '--';

      const compEl = document.getElementById('ct-hex-composition');
      if (compEl) compEl.textContent = Math.round(hex.composition || 0);

      const scoreBar = document.getElementById('ct-hex-score-bar');
      if (scoreBar) {
        scoreBar.style.width = (hex.composition || 0) + '%';
        scoreBar.setAttribute('aria-valuenow', hex.composition || 0);
      }

      if (hex.borders) {
        const dirs = ['ne', 'e', 'se', 'sw', 'w', 'nw'];
        for (const dir of dirs) {
          const bar = document.querySelector(`.ct-progress__bar[data-dir="${dir}"]`);
          if (bar) bar.style.width = ((hex.borders[dir] || 0) * 100) + '%';
        }
      }
    }

    /* ==================== Notifications ==================== */
    notify(message, type, duration) {
      type = type || 'info';
      duration = duration || 3000;

      const toast = document.createElement('div');
      toast.className = `ct-toast ct-toast--${type}`;

      const icons = { info: 'ℹ', success: '✓', warning: '⚠', error: '✕' };
      toast.innerHTML = `
        <span class="ct-toast__icon">${icons[type] || 'ℹ'}</span>
        <p class="ct-toast__message">${message}</p>
        <button class="ct-toast__close">&times;</button>
      `;

      toast.querySelector('.ct-toast__close').onclick = () => this._removeToast(toast);

      if (this.toastContainer) this.toastContainer.appendChild(toast);
      this.toasts.push(toast);

      setTimeout(() => this._removeToast(toast), duration);
    }

    _removeToast(toast) {
      toast.classList.add('ct-toast--exit');
      setTimeout(() => {
        toast.remove();
        this.toasts = this.toasts.filter(t => t !== toast);
      }, 300);
    }

    /* ==================== Context Menu ==================== */
    _initContextMenu() {
      document.addEventListener('contextmenu', (e) => {
        if (e.target.closest('.ct-canvas-layer')) {
          e.preventDefault();
          this._pendingContextPos = { x: e.clientX, y: e.clientY };
          const event = new CustomEvent('ct-context-request', {
            detail: { x: e.clientX, y: e.clientY }
          });
          document.dispatchEvent(event);
        }
      });

      document.addEventListener('click', () => this.hideContextMenu());
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.hideContextMenu();
      });
    }

    showContextMenu(x, y, items) {
      if (!this.contextMenuEl) return;
      this.contextMenuEl.innerHTML = '';
      this.contextMenuEl.setAttribute('aria-hidden', 'false');

      for (const item of items) {
        if (item.separator) {
          const sep = document.createElement('div');
          sep.className = 'ct-context-menu__separator';
          this.contextMenuEl.appendChild(sep);
          continue;
        }
        const el = document.createElement('div');
        el.className = 'ct-context-menu__item';
        if (item.disabled) el.classList.add('ct-context-menu__item--disabled');
        el.innerHTML = `<span>${item.icon || ''}</span><span>${item.label}</span>`;
        if (!item.disabled) {
          el.addEventListener('click', () => {
            if (item.action) item.action();
            this.hideContextMenu();
          });
        }
        this.contextMenuEl.appendChild(el);
      }

      const vw = window.innerWidth;
      const vh = window.innerHeight;
      this.contextMenuEl.style.display = 'block';
      const rect = this.contextMenuEl.getBoundingClientRect();
      let posX = x;
      let posY = y;
      if (posX + rect.width > vw) posX = vw - rect.width - 5;
      if (posY + rect.height > vh) posY = vh - rect.height - 5;
      this.contextMenuEl.style.left = posX + 'px';
      this.contextMenuEl.style.top = posY + 'px';
    }

    hideContextMenu() {
      if (this.contextMenuEl) {
        this.contextMenuEl.innerHTML = '';
        this.contextMenuEl.setAttribute('aria-hidden', 'true');
        this.contextMenuEl.style.display = 'none';
      }
    }

    /* ==================== Settings Panel ==================== */
    renderSettings(settings, onChange) {
      const bindings = [
        { id: 'ct-setting-master-volume', key: 'masterVolume', type: 'range',
          display: 'ct-master-volume-display' },
        { id: 'ct-setting-music', key: 'music', type: 'checkbox' },
        { id: 'ct-setting-sfx', key: 'sfx', type: 'checkbox' },
        { id: 'ct-setting-ambient', key: 'ambient', type: 'checkbox' },
        { id: 'ct-setting-quality', key: 'quality', type: 'select' },
        { id: 'ct-setting-animations', key: 'animations', type: 'checkbox' },
        { id: 'ct-setting-particles', key: 'particles', type: 'checkbox' },
        { id: 'ct-setting-postfx', key: 'postfx', type: 'checkbox' },
        { id: 'ct-setting-gridlines', key: 'gridlines', type: 'checkbox' },
        { id: 'ct-setting-coords', key: 'coords', type: 'checkbox' },
        { id: 'ct-setting-colorblind', key: 'colorblind', type: 'checkbox' },
        { id: 'ct-setting-ui-scale', key: 'uiScale', type: 'range',
          display: 'ct-ui-scale-display' },
        { id: 'ct-setting-autosave', key: 'autosave', type: 'checkbox' },
        { id: 'ct-setting-hints', key: 'hints', type: 'checkbox' }
      ];

      for (const b of bindings) {
        const el = document.getElementById(b.id);
        if (!el) continue;

        if (settings && settings[b.key] !== undefined) {
          if (b.type === 'checkbox') el.checked = !!settings[b.key];
          else el.value = settings[b.key];
        }

        if (b.display) {
          const dEl = document.getElementById(b.display);
          if (dEl) dEl.textContent = el.value;
        }

        el.addEventListener('input', () => {
          const val = b.type === 'checkbox' ? el.checked :
                      b.type === 'range' ? parseFloat(el.value) : el.value;
          if (b.display) {
            const dEl = document.getElementById(b.display);
            if (dEl) dEl.textContent = el.value;
          }
          if (onChange) onChange(b.key, val);
        });
      }

      this._initSettingsTabs();
    }

    _initSettingsTabs() {
      const tabs = document.querySelectorAll('#ct-settings .ct-tab');
      const panels = document.querySelectorAll('#ct-settings .ct-settings__panel');

      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          tabs.forEach(t => {
            t.classList.remove('ct-tab--active');
            t.setAttribute('aria-selected', 'false');
          });
          tab.classList.add('ct-tab--active');
          tab.setAttribute('aria-selected', 'true');

          panels.forEach(p => p.hidden = true);
          const targetId = tab.getAttribute('aria-controls');
          const targetPanel = document.getElementById(targetId);
          if (targetPanel) targetPanel.hidden = false;
        });
      });
    }

    /* ==================== Responsive ==================== */
    _initResponsive() {
      this._checkBreakpoint();
      window.addEventListener('resize', () => this._checkBreakpoint());
    }

    _checkBreakpoint() {
      const w = window.innerWidth;
      const sidebar = document.getElementById('ct-sidebar');
      if (!sidebar) return;
      if (w < 768) {
        sidebar.classList.add('ct-sidebar--collapsed');
      }
    }

    /* ==================== Cleanup ==================== */
    destroy() {
      this.panels.forEach((_, id) => this.closePanel(id));
      this.toasts.forEach(t => t.remove());
      this.hideTooltip();
      this.hideContextMenu();
    }
  }

  CT.UISystem = UISystem;
})();
"""



# =================================================================
# 3. GALLERY - Artwork capture, storage, display
# =================================================================

GALLERY_JS = """\
/* ================================================================
   Chromatic Territories — Gallery
   Capture, store, and display game-state artwork snapshots.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  const STORAGE_KEY = 'ct-gallery';
  const MAX_ITEMS = 50;

  const COLOR_NAMES = {
    red: [0, 30], orange: [30, 60], yellow: [60, 90],
    chartreuse: [90, 120], green: [120, 150], spring: [150, 180],
    cyan: [180, 210], azure: [210, 240], blue: [240, 270],
    violet: [270, 300], magenta: [300, 330], rose: [330, 360]
  };

  const ADJECTIVES = [
    'Luminous', 'Fractured', 'Radiant', 'Shimmering', 'Twilight',
    'Blazing', 'Prismatic', 'Serene', 'Chromatic', 'Ephemeral',
    'Converging', 'Divergent', 'Resonant', 'Vivid', 'Ethereal'
  ];

  const NOUNS = [
    'Territories', 'Convergence', 'Horizon', 'Archipelago',
    'Constellation', 'Mosaic', 'Tapestry', 'Dominion',
    'Expanse', 'Frontier', 'Spectrum', 'Landscape'
  ];

  function hueName(hue) {
    for (const [name, [lo, hi]] of Object.entries(COLOR_NAMES)) {
      if (hue >= lo && hue < hi) return name;
    }
    return 'red';
  }

  function autoTitle(gameState) {
    let dominantHue = 0;
    if (gameState && gameState.territories && gameState.territories.length) {
      const biggest = gameState.territories.reduce((a, b) =>
        (b.cells || []).length > (a.cells || []).length ? b : a
      );
      const c = biggest.color || '#6366f1';
      const r = parseInt(c.slice(1, 3), 16);
      const g = parseInt(c.slice(3, 5), 16);
      const b = parseInt(c.slice(5, 7), 16);
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      if (max === min) dominantHue = 0;
      else if (max === r) dominantHue = ((g - b) / (max - min) * 60 + 360) % 360;
      else if (max === g) dominantHue = ((b - r) / (max - min) * 60 + 120);
      else dominantHue = ((r - g) / (max - min) * 60 + 240);
    }
    const colorName = hueName(dominantHue);
    const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
    const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
    return `${adj} ${colorName.charAt(0).toUpperCase() + colorName.slice(1)} ${noun}`;
  }

  class Gallery {
    constructor() {
      this.items = this.loadGallery();
      this.currentDetailIdx = -1;
    }

    /* ---- Capture ---- */
    captureSnapshot(renderer) {
      if (!renderer || !renderer.layers) return null;
      const terrainCanvas = renderer.layers.terrain.canvas;
      const territoryCanvas = renderer.layers.territory.canvas;
      const w = terrainCanvas.width;
      const h = terrainCanvas.height;

      const composite = document.createElement('canvas');
      composite.width = w;
      composite.height = h;
      const ctx = composite.getContext('2d');
      ctx.drawImage(terrainCanvas, 0, 0);
      ctx.drawImage(territoryCanvas, 0, 0);
      if (renderer.layers.effects) {
        ctx.drawImage(renderer.layers.effects.canvas, 0, 0);
      }
      return composite.toDataURL('image/png');
    }

    generateThumbnail(dataUrl, maxWidth) {
      maxWidth = maxWidth || 200;
      return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
          const ratio = maxWidth / img.width;
          const canvas = document.createElement('canvas');
          canvas.width = maxWidth;
          canvas.height = img.height * ratio;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL('image/png', 0.8));
        };
        img.src = dataUrl;
      });
    }

    async createArtwork(renderer, gameState) {
      const fullImage = this.captureSnapshot(renderer);
      if (!fullImage) return null;
      const thumbnail = await this.generateThumbnail(fullImage);

      return {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        title: autoTitle(gameState),
        date: new Date().toISOString(),
        players: (gameState.players || []).map(p => p.name || 'Player'),
        compositionScore: gameState.compositionScore || 0,
        boardSize: gameState.grid ? gameState.grid.size : 'unknown',
        thumbnail,
        fullImage,
        colors: this._extractDominantColors(gameState)
      };
    }

    _extractDominantColors(gameState) {
      if (!gameState || !gameState.territories) return [];
      return gameState.territories.map(t => ({
        color: t.color || '#888',
        count: (t.cells || []).length
      }));
    }

    /* ---- Persistence ---- */
    saveToGallery(artwork) {
      this.items.unshift(artwork);
      if (this.items.length > MAX_ITEMS) {
        this.items = this.items.slice(0, MAX_ITEMS);
      }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.items));
      } catch (e) { console.warn('Gallery save failed:', e); }
    }

    loadGallery() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : [];
      } catch (e) { return []; }
    }

    deleteArtwork(id) {
      this.items = this.items.filter(a => a.id !== id);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.items));
      } catch (e) { /* noop */ }
    }

    /* ---- Sorting ---- */
    sortGallery(by) {
      if (by === 'date') this.items.sort((a, b) => new Date(b.date) - new Date(a.date));
      else if (by === 'score') this.items.sort((a, b) => (b.compositionScore || 0) - (a.compositionScore || 0));
      else if (by === 'size') this.items.sort((a, b) => {
        const sizeMap = { small: 1, medium: 2, large: 3 };
        return (sizeMap[b.boardSize] || 0) - (sizeMap[a.boardSize] || 0);
      });
    }

    /* ---- Grid View ---- */
    renderGalleryGrid(container) {
      if (!container) container = document.getElementById('ct-gallery-grid');
      if (!container) return;
      container.innerHTML = '';

      const emptyEl = document.getElementById('ct-gallery-empty');
      const countEl = document.getElementById('ct-gallery-count');
      if (countEl) countEl.textContent = `${this.items.length} artwork${this.items.length !== 1 ? 's' : ''}`;

      if (this.items.length === 0) {
        if (emptyEl) emptyEl.style.display = 'flex';
        return;
      }
      if (emptyEl) emptyEl.style.display = 'none';

      this.items.forEach((art, idx) => {
        const card = document.createElement('article');
        card.className = 'ct-artwork-card';
        card.setAttribute('role', 'listitem');
        card.dataset.id = art.id;

        const relDate = this._relativeDate(art.date);

        card.innerHTML = `
          <div class="ct-artwork-card__image">
            <img src="${art.thumbnail}" alt="${art.title}" loading="lazy">
            <div class="ct-artwork-card__overlay">
              <button class="ct-btn ct-btn--primary ct-btn--sm">View</button>
            </div>
          </div>
          <div class="ct-artwork-card__body">
            <h3 class="ct-artwork-card__title">${art.title}</h3>
            <time class="ct-artwork-card__date" datetime="${art.date}">${relDate}</time>
          </div>
          <div class="ct-artwork-card__footer">
            <div class="ct-artwork-card__players">
              ${(art.colors || []).map(c =>
                `<span class="ct-player-dot" style="background:${c.color}"></span>`
              ).join('')}
            </div>
            <span class="ct-badge ct-badge--accent">${Math.round(art.compositionScore || 0)}</span>
          </div>
        `;

        card.addEventListener('click', () => this.renderDetailView(idx));
        container.appendChild(card);
      });

      this._initSortButtons();
    }

    _relativeDate(iso) {
      const d = new Date(iso);
      const now = new Date();
      const diff = (now - d) / 1000;
      if (diff < 60) return 'just now';
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
      return d.toLocaleDateString();
    }

    _initSortButtons() {
      const btns = document.querySelectorAll('.ct-gallery__sort-btn');
      btns.forEach(btn => {
        btn.onclick = () => {
          btns.forEach(b => {
            b.classList.remove('ct-gallery__sort-btn--active');
            b.setAttribute('aria-pressed', 'false');
          });
          btn.classList.add('ct-gallery__sort-btn--active');
          btn.setAttribute('aria-pressed', 'true');
          this.sortGallery(btn.dataset.sort);
          this.renderGalleryGrid();
        };
      });
    }

    /* ---- Detail View ---- */
    renderDetailView(idx) {
      const art = this.items[idx];
      if (!art) return;
      this.currentDetailIdx = idx;

      const detail = document.getElementById('ct-gallery-detail');
      if (!detail) return;
      detail.style.display = 'block';

      const img = document.getElementById('ct-gallery-detail-img');
      if (img) img.src = art.fullImage || art.thumbnail;

      const titleEl = document.getElementById('ct-gallery-detail-title');
      if (titleEl) titleEl.textContent = art.title;

      const dateEl = document.getElementById('ct-gallery-detail-date');
      if (dateEl) dateEl.textContent = new Date(art.date).toLocaleString();

      const playersEl = document.getElementById('ct-gallery-detail-players');
      if (playersEl) playersEl.textContent = (art.players || []).join(', ');

      const boardEl = document.getElementById('ct-gallery-detail-board');
      if (boardEl) boardEl.textContent = art.boardSize || '--';

      const scoreEl = document.getElementById('ct-gallery-detail-score');
      if (scoreEl) scoreEl.textContent = Math.round(art.compositionScore || 0);

      const barsEl = document.getElementById('ct-gallery-detail-bars');
      if (barsEl) {
        barsEl.innerHTML = '';
        const total = (art.colors || []).reduce((s, c) => s + c.count, 0) || 1;
        for (const c of art.colors || []) {
          const pct = ((c.count / total) * 100).toFixed(1);
          const bar = document.createElement('div');
          bar.className = 'ct-progress';
          bar.innerHTML = `<div class="ct-progress__bar" style="width:${pct}%;background:${c.color};"></div>`;
          barsEl.appendChild(bar);
        }
      }

      this._bindDetailEvents(art);
    }

    _bindDetailEvents(art) {
      const closeBtn = document.getElementById('ct-gallery-detail-close');
      if (closeBtn) closeBtn.onclick = () => this._closeDetail();

      const prevBtn = document.getElementById('ct-gallery-prev');
      if (prevBtn) prevBtn.onclick = () => {
        if (this.currentDetailIdx > 0) this.renderDetailView(this.currentDetailIdx - 1);
      };

      const nextBtn = document.getElementById('ct-gallery-next');
      if (nextBtn) nextBtn.onclick = () => {
        if (this.currentDetailIdx < this.items.length - 1) this.renderDetailView(this.currentDetailIdx + 1);
      };

      const dlBtn = document.getElementById('ct-gallery-download');
      if (dlBtn) dlBtn.onclick = () => this.exportAsPNG(art);

      const copyBtn = document.getElementById('ct-gallery-copy');
      if (copyBtn) copyBtn.onclick = () => this.copyToClipboard(art);

      const shareBtn = document.getElementById('ct-gallery-share');
      if (shareBtn) shareBtn.onclick = () => this.renderShareModal(art);

      const delBtn = document.getElementById('ct-gallery-delete');
      if (delBtn) delBtn.onclick = () => {
        if (confirm('Delete this artwork?')) {
          this.deleteArtwork(art.id);
          this._closeDetail();
          this.renderGalleryGrid();
        }
      };
    }

    _closeDetail() {
      const detail = document.getElementById('ct-gallery-detail');
      if (detail) detail.style.display = 'none';
      this.currentDetailIdx = -1;
    }

    /* ---- Export ---- */
    exportAsPNG(art) {
      const link = document.createElement('a');
      link.download = `${art.title.replace(/[^a-z0-9]/gi, '_')}.png`;
      link.href = art.fullImage || art.thumbnail;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }

    async copyToClipboard(art) {
      try {
        const img = new Image();
        img.src = art.fullImage || art.thumbnail;
        await new Promise(r => { img.onload = r; });
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        canvas.getContext('2d').drawImage(img, 0, 0);
        const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
        await navigator.clipboard.write([
          new ClipboardItem({ 'image/png': blob })
        ]);
      } catch (e) {
        console.warn('Copy failed, falling back to data URL copy');
        try {
          await navigator.clipboard.writeText(art.fullImage || art.thumbnail);
        } catch (e2) { /* noop */ }
      }
    }

    renderShareModal(art) {
      const text = [
        `Chromatic Territories artwork "${art.title}"`,
        `Created: ${new Date(art.date).toLocaleDateString()}`,
        `Composition Score: ${Math.round(art.compositionScore || 0)}`,
        `Players: ${(art.players || []).join(', ')}`,
        `Board: ${art.boardSize}`
      ].join('\\n');

      if (CT.UISystem) {
        const ui = new CT.UISystem();
        ui.showModal({
          title: 'Share Artwork',
          content: `<textarea class="ct-input" rows="5" readonly style="resize:none;">${text}</textarea>`,
          buttons: [
            {
              label: 'Copy Text',
              type: 'primary',
              close: false,
              action: () => navigator.clipboard.writeText(text).catch(() => {})
            },
            { label: 'Close', type: 'ghost' }
          ]
        });
      }
    }
  }

  CT.Gallery = Gallery;
})();
"""



# =================================================================
# 4. TUTORIAL - Interactive step-by-step tutorial
# =================================================================

TUTORIAL_JS = """\
/* ================================================================
   Chromatic Territories — Tutorial System
   Step-by-step interactive tutorial with spotlight and progress.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  const STEPS = [
    {
      id: 'welcome',
      title: 'Welcome',
      message: 'Welcome to Chromatic Territories! This game blends strategic territory control with generative art. Every match you play creates a unique artwork.',
      target: null,
      position: 'center',
      interactive: false
    },
    {
      id: 'canvas-intro',
      title: 'Your Canvas',
      message: 'This is your canvas — the game board and artwork combined. Use mouse drag to pan around and scroll wheel to zoom in and out.',
      target: '#ct-canvas-container',
      position: 'bottom',
      interactive: false
    },
    {
      id: 'hex-grid',
      title: 'The Hex Grid',
      message: 'The world is made of hexagonal cells. Each hex can be claimed by a player and becomes part of their territory — and part of the artwork.',
      target: '#ct-canvas-container',
      position: 'right',
      interactive: false
    },
    {
      id: 'palette',
      title: 'Choose Your Palette',
      message: 'Select a color palette for your territories. Your palette defines the colors you can use — these drive both combat effectiveness and artistic composition.',
      target: '#ct-palette-slots',
      position: 'right',
      interactive: false
    },
    {
      id: 'color-theory',
      title: 'Color Theory',
      message: 'Colors have relationships: complementary colors (opposite on the wheel) are strong against each other in combat. Analogous colors (neighbors) reinforce each other for better composition.',
      target: '#ct-color-wheel',
      position: 'right',
      interactive: false
    },
    {
      id: 'first-claim',
      title: 'Claim Territory',
      message: 'Click on an unclaimed hex to select it, then click the EXPAND button to claim it for your own. Try it now!',
      target: '#ct-action-expand',
      position: 'top',
      interactive: true,
      action: 'expand'
    },
    {
      id: 'expansion',
      title: 'Expand Your Domain',
      message: 'Expand from your existing territories into adjacent hexes. Each expansion costs chromaticity points. Build connected regions for better composition scores.',
      target: '#ct-action-expand',
      position: 'top',
      interactive: false
    },
    {
      id: 'composition',
      title: 'Composition Score',
      message: 'Your composition score measures the aesthetic quality of your territory arrangement. Connected regions with harmonious colors score higher. Check the score in the sidebar.',
      target: '#ct-score-list',
      position: 'left',
      interactive: false
    },
    {
      id: 'fortify',
      title: 'Fortify Borders',
      message: 'FORTIFY strengthens a hex\'s border defenses, making it harder for opponents to capture. Fortified borders show as thicker lines.',
      target: '#ct-action-fortify',
      position: 'top',
      interactive: false
    },
    {
      id: 'disrupt',
      title: 'Disrupt Opponents',
      message: 'DISRUPT uses color dissonance to weaken an opponent\'s adjacent hex. The attack is stronger when your color is complementary to theirs.',
      target: '#ct-action-disrupt',
      position: 'top',
      interactive: false
    },
    {
      id: 'harmonize',
      title: 'Harmonize Colors',
      message: 'HARMONIZE improves your composition score by optimizing color relationships between a hex and its neighbors. Strategic harmony wins games.',
      target: '#ct-action-harmonize',
      position: 'top',
      interactive: false
    },
    {
      id: 'evolve',
      title: 'Evolve Hex Color',
      message: 'EVOLVE changes a hex\'s color within your palette. Use this to adapt your strategy or improve composition in specific areas.',
      target: '#ct-action-evolve',
      position: 'top',
      interactive: false
    },
    {
      id: 'ai-opponents',
      title: 'AI Opponents',
      message: 'AI players use different strategies — some aggressive, some artistic. Watch how they build their territories and adapt your approach.',
      target: '#ct-ai-thinking',
      position: 'bottom',
      interactive: false
    },
    {
      id: 'generative-art',
      title: 'Generative Art',
      message: 'As you play, the board becomes a living generative artwork. Noise patterns create terrain, particles react to combat, and the music follows the game state.',
      target: '#ct-canvas-container',
      position: 'center',
      interactive: false
    },
    {
      id: 'victory',
      title: 'Victory Conditions',
      message: 'Win by achieving the highest composition score when all hexes are claimed — or when the turn limit is reached. The final board is saved as artwork. Good luck and create something beautiful!',
      target: null,
      position: 'center',
      interactive: false
    }
  ];

  class TutorialSystem {
    constructor() {
      this.steps = STEPS;
      this.currentStep = 0;
      this.overlay = document.getElementById('ct-tutorial');
      this.welcomeEl = document.getElementById('ct-tutorial-welcome');
      this.cardEl = document.getElementById('ct-tutorial-card');
      this.spotlightEl = document.getElementById('ct-tutorial-spotlight');
      this.progressEl = document.getElementById('ct-tutorial-progress');
      this._interactionResolver = null;

      this._bindEvents();
    }

    _bindEvents() {
      const prevBtn = document.getElementById('ct-tutorial-prev');
      const nextBtn = document.getElementById('ct-tutorial-next');
      const skipBtn = document.getElementById('ct-tutorial-skip');
      const restartBtn = document.getElementById('ct-tutorial-restart');
      const beginBtn = document.getElementById('ct-tutorial-begin');
      const skipWelcomeBtn = document.getElementById('ct-tutorial-skip-welcome');

      if (prevBtn) prevBtn.onclick = () => this.prev();
      if (nextBtn) nextBtn.onclick = () => this.next();
      if (skipBtn) skipBtn.onclick = () => this.skip();
      if (restartBtn) restartBtn.onclick = () => this.restart();
      if (beginBtn) beginBtn.onclick = () => this._hideWelcome();
      if (skipWelcomeBtn) skipWelcomeBtn.onclick = () => this.skip();
    }

    /* ---- Lifecycle ---- */
    start() {
      if (!this.overlay) return;
      this.currentStep = 0;
      this.overlay.style.display = 'block';
      if (this.welcomeEl) this.welcomeEl.style.display = 'flex';
      if (this.cardEl) this.cardEl.style.display = 'none';
    }

    _hideWelcome() {
      if (this.welcomeEl) this.welcomeEl.style.display = 'none';
      if (this.cardEl) this.cardEl.style.display = 'block';
      this.renderStep(this.steps[0]);
    }

    next() {
      if (this._interactionResolver) return;
      if (this.currentStep < this.steps.length - 1) {
        this.currentStep++;
        this.renderStep(this.steps[this.currentStep]);
      } else {
        this.complete();
      }
    }

    prev() {
      if (this.currentStep > 0) {
        this.currentStep--;
        this.renderStep(this.steps[this.currentStep]);
      }
    }

    complete() {
      try { localStorage.setItem('ct-tutorial-done', 'true'); } catch (e) {}
      this._hide();
      document.dispatchEvent(new CustomEvent('ct-tutorial-complete'));
    }

    skip() {
      try { localStorage.setItem('ct-tutorial-done', 'true'); } catch (e) {}
      this._hide();
    }

    restart() {
      try { localStorage.removeItem('ct-tutorial-done'); } catch (e) {}
      this.start();
    }

    _hide() {
      if (this.overlay) this.overlay.style.display = 'none';
    }

    /* ---- Step rendering ---- */
    renderStep(step) {
      if (!step) return;

      const titleEl = document.getElementById('ct-tutorial-step-title');
      if (titleEl) titleEl.textContent = step.title;

      const msgEl = document.getElementById('ct-tutorial-step-message');
      if (msgEl) msgEl.textContent = step.message;

      const badgeEl = document.getElementById('ct-tutorial-step-badge');
      if (badgeEl) badgeEl.textContent = `Step ${this.currentStep + 1}`;

      const counterEl = document.getElementById('ct-tutorial-counter');
      if (counterEl) counterEl.textContent = `Step ${this.currentStep + 1} of ${this.steps.length}`;

      const prevBtn = document.getElementById('ct-tutorial-prev');
      if (prevBtn) prevBtn.disabled = this.currentStep === 0;

      const nextBtn = document.getElementById('ct-tutorial-next');
      if (nextBtn) {
        if (this.currentStep === this.steps.length - 1) {
          nextBtn.textContent = 'Finish';
        } else if (step.interactive) {
          nextBtn.textContent = 'Waiting...';
          nextBtn.disabled = true;
        } else {
          nextBtn.innerHTML = 'Next &rarr;';
          nextBtn.disabled = false;
        }
      }

      this._highlightTarget(step);
      this._renderProgress();

      if (step.interactive) {
        this._waitForInteraction(step).then(() => {
          if (nextBtn) {
            nextBtn.innerHTML = 'Next &rarr;';
            nextBtn.disabled = false;
          }
        });
      }
    }

    _highlightTarget(step) {
      if (!step.target) {
        if (this.spotlightEl) this.spotlightEl.style.display = 'none';
        this._positionCard('center');
        return;
      }

      const el = document.querySelector(step.target);
      if (!el) {
        if (this.spotlightEl) this.spotlightEl.style.display = 'none';
        this._positionCard('center');
        return;
      }

      const rect = el.getBoundingClientRect();
      const pad = 8;
      if (this.spotlightEl) {
        this.spotlightEl.style.display = 'block';
        this.spotlightEl.style.left = (rect.left - pad) + 'px';
        this.spotlightEl.style.top = (rect.top - pad) + 'px';
        this.spotlightEl.style.width = (rect.width + pad * 2) + 'px';
        this.spotlightEl.style.height = (rect.height + pad * 2) + 'px';
      }

      this._positionCard(step.position, rect);
    }

    _positionCard(position, targetRect) {
      if (!this.cardEl) return;
      const card = this.cardEl;
      const cw = 380;
      const ch = card.offsetHeight || 250;
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      if (position === 'center' || !targetRect) {
        card.style.left = ((vw - cw) / 2) + 'px';
        card.style.top = ((vh - ch) / 2) + 'px';
        return;
      }

      let x = 0, y = 0;
      switch (position) {
        case 'top':
          x = targetRect.left + targetRect.width / 2 - cw / 2;
          y = targetRect.top - ch - 20;
          break;
        case 'bottom':
          x = targetRect.left + targetRect.width / 2 - cw / 2;
          y = targetRect.bottom + 20;
          break;
        case 'left':
          x = targetRect.left - cw - 20;
          y = targetRect.top + targetRect.height / 2 - ch / 2;
          break;
        case 'right':
          x = targetRect.right + 20;
          y = targetRect.top + targetRect.height / 2 - ch / 2;
          break;
      }

      x = Math.max(10, Math.min(vw - cw - 10, x));
      y = Math.max(10, Math.min(vh - ch - 10, y));
      card.style.left = x + 'px';
      card.style.top = y + 'px';
    }

    _renderProgress() {
      if (!this.progressEl) return;
      const dots = this.progressEl.querySelectorAll('.ct-tutorial__dot');
      dots.forEach((dot, idx) => {
        dot.classList.remove('ct-tutorial__dot--completed', 'ct-tutorial__dot--current');
        if (idx < this.currentStep) dot.classList.add('ct-tutorial__dot--completed');
        else if (idx === this.currentStep) dot.classList.add('ct-tutorial__dot--current');
      });

      if (this.progressEl.setAttribute) {
        this.progressEl.setAttribute('aria-valuenow', this.currentStep + 1);
      }
    }

    _waitForInteraction(step) {
      return new Promise((resolve) => {
        this._interactionResolver = resolve;
        const handler = (e) => {
          if (step.action && e.detail && e.detail.action === step.action) {
            document.removeEventListener('ct-action', handler);
            this._interactionResolver = null;
            resolve();
          }
        };
        document.addEventListener('ct-action', handler);

        setTimeout(() => {
          if (this._interactionResolver === resolve) {
            document.removeEventListener('ct-action', handler);
            this._interactionResolver = null;
            resolve();
          }
        }, 15000);
      });
    }

    /* ---- Auto-trigger ---- */
    checkFirstVisit() {
      try {
        if (!localStorage.getItem('ct-visited')) {
          localStorage.setItem('ct-visited', 'true');
          this.start();
          return true;
        }
      } catch (e) {}
      return false;
    }

    /* ---- Cleanup ---- */
    destroy() {
      this._hide();
      this._interactionResolver = null;
    }
  }

  CT.TutorialSystem = TutorialSystem;
})();
"""



# =================================================================
# 5. AUDIO SYNTHESIZER - Web Audio API synthesis engine
# =================================================================

AUDIO_SYNTH_JS = """\
/* ================================================================
   Chromatic Territories — Audio Synthesizer
   Web Audio API based synthesis: oscillators, envelopes, filters,
   effects, scales, chords, and arpeggiator.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  /* ---- Scale definitions (semitone intervals from root) ---- */
  const SCALES = {
    MAJOR:       [0, 2, 4, 5, 7, 9, 11],
    MINOR:       [0, 2, 3, 5, 7, 8, 10],
    PENTATONIC:  [0, 2, 4, 7, 9],
    BLUES:       [0, 3, 5, 6, 7, 10],
    WHOLE_TONE:  [0, 2, 4, 6, 8, 10],
    CHROMATIC:   [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    DORIAN:      [0, 2, 3, 5, 7, 9, 10],
    MIXOLYDIAN:  [0, 2, 4, 5, 7, 9, 10]
  };

  /* ---- Chord intervals ---- */
  const CHORDS = {
    major:      [0, 4, 7],
    minor:      [0, 3, 7],
    diminished: [0, 3, 6],
    augmented:  [0, 4, 8],
    dominant7:  [0, 4, 7, 10],
    major7:     [0, 4, 7, 11],
    minor7:     [0, 3, 7, 10],
    sus2:       [0, 2, 7],
    sus4:       [0, 5, 7],
    add9:       [0, 4, 7, 14]
  };

  class AudioSynthesizer {
    constructor() {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 0.5;
      this.masterGain.connect(this.ctx.destination);
      this.channels = {};
      this.isResumed = false;
      this._activeNodes = [];
    }

    /* ---- Context management ---- */
    async resume() {
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume();
      }
      this.isResumed = true;
    }

    ensureContext() {
      if (!this.isResumed) this.resume();
    }

    get currentTime() { return this.ctx.currentTime; }

    /* ---- Frequency helpers ---- */
    static noteToFreq(note, octave) {
      return 440 * Math.pow(2, (note - 9 + (octave - 4) * 12) / 12);
    }

    static midiToFreq(midi) {
      return 440 * Math.pow(2, (midi - 69) / 12);
    }

    static freqToMidi(freq) {
      return 69 + 12 * Math.log2(freq / 440);
    }

    static getScale(name) {
      return SCALES[name] || SCALES.MAJOR;
    }

    static getChordIntervals(name) {
      return CHORDS[name] || CHORDS.major;
    }

    /* ---- Envelope ---- */
    createEnvelope(gainNode, { attack, decay, sustain, release }, startTime) {
      attack = attack || 0.01;
      decay = decay || 0.1;
      sustain = Math.max(0.001, sustain !== undefined ? sustain : 0.7);
      release = release || 0.3;
      const t = startTime || this.ctx.currentTime;
      const g = gainNode.gain;

      g.cancelScheduledValues(t);
      g.setValueAtTime(0.001, t);
      g.exponentialRampToValueAtTime(1.0, t + attack);
      g.exponentialRampToValueAtTime(sustain, t + attack + decay);

      return { releaseTime: release, sustainLevel: sustain, attackEnd: t + attack + decay };
    }

    releaseEnvelope(gainNode, release, time) {
      release = release || 0.3;
      const t = time || this.ctx.currentTime;
      const g = gainNode.gain;
      g.cancelScheduledValues(t);
      g.setValueAtTime(g.value || 0.001, t);
      g.exponentialRampToValueAtTime(0.001, t + release);
    }

    /* ---- Oscillator creation ---- */
    createOscillator(type, freq, detune) {
      const osc = this.ctx.createOscillator();
      osc.type = type || 'sine';
      osc.frequency.value = freq || 440;
      if (detune) osc.detune.value = detune;
      return osc;
    }

    createCustomWave(harmonics) {
      const n = harmonics.length;
      const real = new Float32Array(n + 1);
      const imag = new Float32Array(n + 1);
      real[0] = 0;
      imag[0] = 0;
      for (let i = 0; i < n; i++) {
        real[i + 1] = harmonics[i].real || 0;
        imag[i + 1] = harmonics[i].imag || harmonics[i] || 0;
      }
      return this.ctx.createPeriodicWave(real, imag, { disableNormalization: false });
    }

    /* ---- Filter ---- */
    createFilter(type, freq, Q) {
      const filter = this.ctx.createBiquadFilter();
      filter.type = type || 'lowpass';
      filter.frequency.value = freq || 1000;
      filter.Q.value = Q || 1;
      return filter;
    }

    /* ---- Effects ---- */
    createReverb(duration, decay) {
      duration = duration || 2;
      decay = decay || 2;
      const sampleRate = this.ctx.sampleRate;
      const length = sampleRate * duration;
      const buffer = this.ctx.createBuffer(2, length, sampleRate);
      for (let ch = 0; ch < 2; ch++) {
        const data = buffer.getChannelData(ch);
        for (let i = 0; i < length; i++) {
          data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay);
        }
      }
      const convolver = this.ctx.createConvolver();
      convolver.buffer = buffer;
      return convolver;
    }

    createDelay(time, feedback) {
      time = time || 0.3;
      feedback = feedback || 0.4;
      const delay = this.ctx.createDelay(5);
      delay.delayTime.value = time;
      const fbGain = this.ctx.createGain();
      fbGain.gain.value = feedback;
      delay.connect(fbGain);
      fbGain.connect(delay);
      return { delay, feedback: fbGain };
    }

    createChorus(rate, depth, mix) {
      rate = rate || 1.5;
      depth = depth || 0.002;
      mix = mix || 0.5;

      const delay = this.ctx.createDelay();
      delay.delayTime.value = 0.03;
      const lfo = this.ctx.createOscillator();
      lfo.type = 'sine';
      lfo.frequency.value = rate;
      const lfoGain = this.ctx.createGain();
      lfoGain.gain.value = depth;
      lfo.connect(lfoGain);
      lfoGain.connect(delay.delayTime);
      lfo.start();

      const wet = this.ctx.createGain();
      wet.gain.value = mix;
      const dry = this.ctx.createGain();
      dry.gain.value = 1 - mix;

      const input = this.ctx.createGain();
      input.connect(dry);
      input.connect(delay);
      delay.connect(wet);

      const output = this.ctx.createGain();
      dry.connect(output);
      wet.connect(output);

      return { input, output, lfo };
    }

    /* ---- Channel management ---- */
    createChannel(name, gain) {
      const g = this.ctx.createGain();
      g.gain.value = gain !== undefined ? gain : 1.0;
      g.connect(this.masterGain);
      this.channels[name] = g;
      return g;
    }

    getChannel(name) {
      if (!this.channels[name]) this.createChannel(name, 1.0);
      return this.channels[name];
    }

    setChannelGain(name, val) {
      if (this.channels[name]) {
        this.channels[name].gain.setTargetAtTime(val, this.ctx.currentTime, 0.05);
      }
    }

    setMasterGain(val) {
      this.masterGain.gain.setTargetAtTime(val, this.ctx.currentTime, 0.05);
    }

    /* ---- Play a single note ---- */
    playNote(freq, duration, options) {
      this.ensureContext();
      options = options || {};
      const type = options.type || 'sine';
      const envelope = options.envelope || { attack: 0.02, decay: 0.1, sustain: 0.6, release: 0.2 };
      const channel = options.channel ? this.getChannel(options.channel) : this.masterGain;
      const detune = options.detune || 0;
      const now = this.ctx.currentTime;

      const osc = this.createOscillator(type, freq, detune);
      const envGain = this.ctx.createGain();
      envGain.gain.value = 0.001;

      let lastNode = osc;
      if (options.filter) {
        const f = this.createFilter(options.filter.type, options.filter.freq, options.filter.Q);
        lastNode.connect(f);
        lastNode = f;
      }
      lastNode.connect(envGain);
      envGain.connect(channel);

      const env = this.createEnvelope(envGain, envelope, now);
      osc.start(now);

      const endTime = now + (duration || 0.5);
      this.releaseEnvelope(envGain, envelope.release || 0.2, endTime);
      osc.stop(endTime + (envelope.release || 0.2) + 0.05);

      this._activeNodes.push(osc);
      osc.onended = () => {
        this._activeNodes = this._activeNodes.filter(n => n !== osc);
        try { envGain.disconnect(); } catch (e) {}
      };

      return {
        osc,
        stop: (t) => {
          const st = t || this.ctx.currentTime;
          this.releaseEnvelope(envGain, envelope.release || 0.2, st);
          osc.stop(st + (envelope.release || 0.2) + 0.05);
        }
      };
    }

    /* ---- Play a chord ---- */
    playChord(frequencies, duration, options) {
      return frequencies.map(f => this.playNote(f, duration, options));
    }

    generateChord(root, octave, chordType) {
      const intervals = AudioSynthesizer.getChordIntervals(chordType);
      return intervals.map(interval =>
        AudioSynthesizer.noteToFreq((root + interval) % 12, octave + Math.floor((root + interval) / 12))
      );
    }

    /* ---- Arpeggiator ---- */
    createArpeggiator({ notes, pattern, speed, loop }) {
      speed = speed || 120;
      pattern = pattern || 'up';
      loop = loop !== false;
      let running = false;
      let idx = 0;
      let direction = 1;
      let timer = null;

      const getOrderedNotes = () => {
        const sorted = [...notes].sort((a, b) => a - b);
        switch (pattern) {
          case 'down': return sorted.reverse();
          case 'updown': {
            const up = [...sorted];
            const down = [...sorted].reverse().slice(1, -1);
            return [...up, ...down];
          }
          case 'random': return sorted.sort(() => Math.random() - 0.5);
          default: return sorted;
        }
      };

      const tick = () => {
        const ordered = getOrderedNotes();
        if (ordered.length === 0) return;
        const noteFreq = ordered[idx % ordered.length];
        this.playNote(noteFreq, 60 / speed * 0.8, {
          type: 'triangle',
          envelope: { attack: 0.01, decay: 0.05, sustain: 0.5, release: 0.15 },
          channel: 'melody'
        });
        idx++;
        if (!loop && idx >= ordered.length) {
          this.stopArpeggiator(arpObj);
        }
      };

      const arpObj = {
        start: () => {
          if (running) return;
          running = true;
          idx = 0;
          const interval = 60000 / speed;
          tick();
          timer = setInterval(tick, interval);
        },
        stop: () => {
          running = false;
          if (timer) { clearInterval(timer); timer = null; }
        },
        setSpeed: (bpm) => {
          speed = bpm;
          if (running) {
            arpObj.stop();
            arpObj.start();
          }
        },
        setPattern: (p) => { pattern = p; }
      };
      return arpObj;
    }

    /* ---- Play a scale ---- */
    playScale(root, octave, scaleType, noteDuration) {
      const intervals = AudioSynthesizer.getScale(scaleType);
      noteDuration = noteDuration || 0.3;
      const controls = [];
      intervals.forEach((interval, idx) => {
        const freq = AudioSynthesizer.noteToFreq(root + interval, octave);
        setTimeout(() => {
          controls.push(this.playNote(freq, noteDuration, {
            type: 'triangle',
            channel: 'melody'
          }));
        }, idx * noteDuration * 1000);
      });
      return controls;
    }

    /* ---- Noise burst (percussion) ---- */
    generateNoiseBurst(duration, options) {
      this.ensureContext();
      options = options || {};
      duration = duration || 0.1;
      const bufferSize = this.ctx.sampleRate * duration;
      const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;

      const envGain = this.ctx.createGain();
      envGain.gain.value = 1.0;
      envGain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration);

      const channel = options.channel ? this.getChannel(options.channel) : this.masterGain;
      let lastNode = source;

      if (options.filter) {
        const f = this.createFilter(options.filter.type || 'bandpass',
                                     options.filter.freq || 1000,
                                     options.filter.Q || 2);
        lastNode.connect(f);
        lastNode = f;
      }

      lastNode.connect(envGain);
      envGain.connect(channel);

      source.start();
      source.stop(this.ctx.currentTime + duration + 0.05);
      return source;
    }

    /* ---- Cleanup ---- */
    dispose() {
      for (const node of this._activeNodes) {
        try { node.stop(); } catch (e) {}
      }
      this._activeNodes = [];
      if (this.ctx.state !== 'closed') {
        this.ctx.close().catch(() => {});
      }
    }
  }

  /* Expose constants */
  AudioSynthesizer.SCALES = SCALES;
  AudioSynthesizer.CHORDS = CHORDS;

  CT.AudioSynthesizer = AudioSynthesizer;
})();
"""



# =================================================================
# 6. GENERATIVE MUSIC - Game-state-driven procedural music
# =================================================================

GENERATIVE_MUSIC_JS = """\
/* ================================================================
   Chromatic Territories — Generative Music
   Maps game state to musical parameters for procedural soundtrack.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  class GenerativeMusic {
    constructor(synth) {
      if (!synth) throw new Error('GenerativeMusic requires an AudioSynthesizer instance');
      this.synth = synth;
      this.isPlaying = false;
      this.isMuted = false;
      this.tempo = 120;
      this.currentKey = 0;
      this.currentScale = 'MAJOR';
      this.complexity = 0.5;

      this.synth.createChannel('drone', 0.12);
      this.synth.createChannel('melody', 0.3);
      this.synth.createChannel('rhythm', 0.2);
      this.synth.createChannel('sfx', 0.5);

      this._droneNodes = [];
      this._melodyTimer = null;
      this._rhythmTimer = null;
      this._melodyIdx = 0;

      /* Markov chain: transition probabilities between scale degrees */
      this.markovChain = [
        [0.1, 0.2, 0.15, 0.1, 0.2, 0.15, 0.1],   // from degree 0
        [0.25, 0.05, 0.2, 0.1, 0.15, 0.15, 0.1],  // from degree 1
        [0.15, 0.2, 0.05, 0.2, 0.15, 0.15, 0.1],  // from degree 2
        [0.2, 0.1, 0.15, 0.05, 0.25, 0.15, 0.1],  // from degree 3
        [0.25, 0.15, 0.1, 0.15, 0.05, 0.2, 0.1],  // from degree 4
        [0.15, 0.15, 0.15, 0.2, 0.15, 0.05, 0.15], // from degree 5
        [0.3, 0.1, 0.1, 0.15, 0.15, 0.1, 0.1]     // from degree 6
      ];
    }

    /* ---- Color-to-music mapping ---- */
    colorToNote(color) {
      const hue = this._colorToHue(color);
      return Math.round(hue / 30) % 12;
    }

    colorToChordType(color) {
      const hue = this._colorToHue(color);
      if ((hue >= 0 && hue < 60) || hue >= 300) return 'major';
      if (hue >= 120 && hue < 240) return 'minor';
      if (hue >= 60 && hue < 120) return 'sus4';
      return 'diminished';
    }

    territoryToVolume(territory) {
      const count = territory.cells ? territory.cells.length : 0;
      return Math.min(0.8, 0.1 + count * 0.03);
    }

    tensionToDissonance(borderTension) {
      if (borderTension > 0.7) return 'dominant7';
      if (borderTension > 0.4) return 'minor7';
      return 'major';
    }

    scoreToComplexity(compositionScore) {
      return Math.min(1, (compositionScore || 0) / 100);
    }

    turnPhaseToRhythm(phase) {
      const patterns = {
        planning:   [1, 0, 0, 0, 1, 0, 0, 0],
        action:     [1, 0, 1, 0, 1, 0, 1, 0],
        resolution: [1, 0, 0, 1, 0, 0, 1, 0]
      };
      return patterns[phase] || patterns.action;
    }

    _colorToHue(color) {
      if (!color || color.length < 7) return 0;
      const r = parseInt(color.slice(1, 3), 16) / 255;
      const g = parseInt(color.slice(3, 5), 16) / 255;
      const b = parseInt(color.slice(5, 7), 16) / 255;
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      if (max === min) return 0;
      let h;
      const d = max - min;
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
      else if (max === g) h = ((b - r) / d + 2) * 60;
      else h = ((r - g) / d + 4) * 60;
      return h % 360;
    }

    /* ---- Update from game state ---- */
    updateFromGameState(gameState) {
      if (!gameState) return;

      const territories = gameState.territories || [];
      if (territories.length > 0) {
        const biggest = territories.reduce((a, b) =>
          (b.cells || []).length > (a.cells || []).length ? b : a
        );
        this.currentKey = this.colorToNote(biggest.color);

        const hue = this._colorToHue(biggest.color);
        this.currentScale = (hue >= 0 && hue < 60) || hue >= 300 ? 'MAJOR' : 'MINOR';
      }

      const totalClaimed = territories.reduce((s, t) => s + (t.cells || []).length, 0);
      const totalHexes = gameState.grid ? (gameState.grid.cells || []).length : 100;
      const progress = totalClaimed / Math.max(1, totalHexes);
      this.tempo = Math.round(80 + progress * 80);

      this.complexity = this.scoreToComplexity(gameState.compositionScore);

      if (this.isPlaying) {
        this._updateDrone();
      }
    }

    /* ---- Ambient Drone ---- */
    startDrone() {
      this.synth.ensureContext();
      this._stopDroneNodes();
      const rootFreq = CT.AudioSynthesizer.noteToFreq(this.currentKey, 2);

      const configs = [
        { type: 'sawtooth', freq: rootFreq, detune: -5, gain: 0.08 },
        { type: 'sine', freq: rootFreq * 1.5, detune: 3, gain: 0.05 },
        { type: 'sine', freq: rootFreq * 2, detune: -2, gain: 0.03 }
      ];

      for (const cfg of configs) {
        const osc = this.synth.createOscillator(cfg.type, cfg.freq, cfg.detune);
        const gain = this.synth.ctx.createGain();
        gain.gain.value = 0;
        gain.gain.setTargetAtTime(cfg.gain, this.synth.ctx.currentTime, 2.0);

        const filter = this.synth.createFilter('lowpass', 400, 0.5);
        const lfo = this.synth.ctx.createOscillator();
        lfo.type = 'sine';
        lfo.frequency.value = 0.1;
        const lfoGain = this.synth.ctx.createGain();
        lfoGain.gain.value = 200;
        lfo.connect(lfoGain);
        lfoGain.connect(filter.frequency);
        lfo.start();

        osc.connect(filter);
        filter.connect(gain);
        gain.connect(this.synth.getChannel('drone'));
        osc.start();

        this._droneNodes.push({ osc, gain, filter, lfo });
      }
    }

    _updateDrone() {
      const rootFreq = CT.AudioSynthesizer.noteToFreq(this.currentKey, 2);
      const freqs = [rootFreq, rootFreq * 1.5, rootFreq * 2];
      this._droneNodes.forEach((node, i) => {
        if (node.osc && freqs[i]) {
          node.osc.frequency.setTargetAtTime(freqs[i], this.synth.ctx.currentTime, 1.0);
        }
      });
    }

    stopDrone() { this._stopDroneNodes(); }

    _stopDroneNodes() {
      for (const node of this._droneNodes) {
        try {
          if (node.gain) node.gain.gain.setTargetAtTime(0, this.synth.ctx.currentTime, 0.5);
          setTimeout(() => {
            try { node.osc.stop(); } catch (e) {}
            try { node.lfo.stop(); } catch (e) {}
          }, 2000);
        } catch (e) {}
      }
      setTimeout(() => { this._droneNodes = []; }, 2500);
    }

    /* ---- Melodic Layer ---- */
    _markovNext(currentDegree) {
      const probs = this.markovChain[currentDegree] || this.markovChain[0];
      let r = Math.random();
      for (let i = 0; i < probs.length; i++) {
        r -= probs[i];
        if (r <= 0) return i;
      }
      return 0;
    }

    startMelody() {
      this._melodyIdx = 0;
      let currentDegree = 0;
      const scale = CT.AudioSynthesizer.getScale(this.currentScale);

      const playNext = () => {
        if (!this.isPlaying) return;
        currentDegree = this._markovNext(currentDegree);
        const interval = scale[currentDegree % scale.length];
        const octave = 4 + Math.floor(currentDegree / scale.length);
        const freq = CT.AudioSynthesizer.noteToFreq(this.currentKey + interval, octave);

        const durations = [0.25, 0.5, 0.375, 0.125];
        const dur = durations[Math.floor(Math.random() * durations.length)] * (60 / this.tempo);

        if (Math.random() < 0.8) {
          this.synth.playNote(freq, dur * 0.9, {
            type: this.complexity > 0.6 ? 'sawtooth' : 'triangle',
            envelope: { attack: 0.02, decay: 0.08, sustain: 0.5, release: 0.15 },
            channel: 'melody',
            filter: { type: 'lowpass', freq: 2000 + this.complexity * 3000, Q: 1 }
          });
        }

        this._melodyTimer = setTimeout(playNext, dur * 1000);
      };

      playNext();
    }

    stopMelody() {
      if (this._melodyTimer) { clearTimeout(this._melodyTimer); this._melodyTimer = null; }
    }

    /* ---- Rhythmic Layer ---- */
    startDrumPattern() {
      let beat = 0;
      const pattern = this.turnPhaseToRhythm('action');

      const playBeat = () => {
        if (!this.isPlaying) return;
        const beatIdx = beat % 8;
        const beatDur = 60 / this.tempo / 2;

        if (beatIdx % 4 === 0) {
          this.synth.generateNoiseBurst(0.1, {
            channel: 'rhythm',
            filter: { type: 'lowpass', freq: 200, Q: 5 }
          });
        }

        if (beatIdx % 4 === 2) {
          this.synth.generateNoiseBurst(0.08, {
            channel: 'rhythm',
            filter: { type: 'bandpass', freq: 1500, Q: 3 }
          });
        }

        if (pattern[beatIdx]) {
          this.synth.generateNoiseBurst(0.03, {
            channel: 'rhythm',
            filter: { type: 'highpass', freq: 6000, Q: 2 }
          });
        }

        if (Math.random() < 0.15) {
          this.synth.generateNoiseBurst(0.05, {
            channel: 'rhythm',
            filter: { type: 'bandpass', freq: 800 + Math.random() * 2000, Q: 2 }
          });
        }

        beat++;
        this._rhythmTimer = setTimeout(playBeat, beatDur * 1000);
      };

      playBeat();
    }

    stopDrumPattern() {
      if (this._rhythmTimer) { clearTimeout(this._rhythmTimer); this._rhythmTimer = null; }
    }

    /* ---- Transition Music ---- */
    playTransition(type) {
      const scale = CT.AudioSynthesizer.getScale(this.currentScale);
      const baseOctave = 4;

      if (type === 'capture') {
        scale.slice(0, 5).forEach((interval, i) => {
          setTimeout(() => {
            this.synth.playNote(
              CT.AudioSynthesizer.noteToFreq(this.currentKey + interval, baseOctave + Math.floor(i / 4)),
              0.15, { type: 'square', channel: 'sfx',
                      envelope: { attack: 0.01, decay: 0.05, sustain: 0.4, release: 0.1 } }
            );
          }, i * 80);
        });
      } else if (type === 'loss') {
        const minor = CT.AudioSynthesizer.getScale('MINOR');
        minor.slice(0, 4).reverse().forEach((interval, i) => {
          setTimeout(() => {
            this.synth.playNote(
              CT.AudioSynthesizer.noteToFreq(this.currentKey + interval, baseOctave),
              0.2, { type: 'sawtooth', channel: 'sfx',
                      envelope: { attack: 0.02, decay: 0.08, sustain: 0.3, release: 0.2 } }
            );
          }, i * 120);
        });
      } else if (type === 'fortify') {
        const chord = this.synth.generateChord(this.currentKey, baseOctave, 'major');
        chord.forEach((freq, i) => {
          setTimeout(() => {
            this.synth.playNote(freq, 0.8, {
              type: 'sine', channel: 'sfx',
              envelope: { attack: 0.3, decay: 0.1, sustain: 0.7, release: 0.3 }
            });
          }, i * 100);
        });
      }
    }

    /* ---- SFX ---- */
    playExpandSound() {
      const freq = CT.AudioSynthesizer.noteToFreq(this.currentKey, 5);
      this.synth.playNote(freq, 0.2, {
        type: 'sine', channel: 'sfx',
        envelope: { attack: 0.01, decay: 0.05, sustain: 0.5, release: 0.15 }
      });
      this.synth.playNote(freq * 1.5, 0.15, {
        type: 'triangle', channel: 'sfx',
        envelope: { attack: 0.02, decay: 0.05, sustain: 0.3, release: 0.1 }
      });
    }

    playCombatSound() {
      const root = CT.AudioSynthesizer.noteToFreq(this.currentKey, 3);
      const tritone = root * Math.pow(2, 6 / 12);
      this.synth.playNote(root, 0.3, {
        type: 'sawtooth', channel: 'sfx',
        envelope: { attack: 0.01, decay: 0.05, sustain: 0.6, release: 0.2 }
      });
      this.synth.playNote(tritone, 0.25, {
        type: 'square', channel: 'sfx',
        envelope: { attack: 0.01, decay: 0.05, sustain: 0.4, release: 0.15 }
      });
      setTimeout(() => {
        this.synth.playNote(root * 2, 0.4, {
          type: 'sine', channel: 'sfx',
          envelope: { attack: 0.05, decay: 0.1, sustain: 0.5, release: 0.3 }
        });
      }, 200);
    }

    playVictoryFanfare() {
      const chord = this.synth.generateChord(this.currentKey, 4, 'major');
      chord.forEach((freq, i) => {
        setTimeout(() => {
          this.synth.playNote(freq, 1.5, {
            type: 'triangle', channel: 'sfx',
            envelope: { attack: 0.05, decay: 0.1, sustain: 0.8, release: 0.5 }
          });
        }, i * 150);
      });
      setTimeout(() => {
        this.synth.playNote(
          CT.AudioSynthesizer.noteToFreq(this.currentKey, 5), 2.0,
          { type: 'sine', channel: 'sfx',
            envelope: { attack: 0.1, decay: 0.2, sustain: 0.9, release: 0.8 } }
        );
      }, 600);
    }

    playDefeatSting() {
      const minor = this.synth.generateChord(this.currentKey, 3, 'minor');
      minor.reverse().forEach((freq, i) => {
        setTimeout(() => {
          this.synth.playNote(freq, 0.6, {
            type: 'sawtooth', channel: 'sfx',
            envelope: { attack: 0.05, decay: 0.1, sustain: 0.4, release: 0.3 }
          });
        }, i * 200);
      });
      this.synth.generateNoiseBurst(0.5, {
        channel: 'sfx',
        filter: { type: 'lowpass', freq: 200, Q: 3 }
      });
    }

    /* ---- Controls ---- */
    setVolume(v) { this.synth.setMasterGain(v); }

    toggleMute() {
      this.isMuted = !this.isMuted;
      this.synth.setMasterGain(this.isMuted ? 0 : 0.5);
    }

    setTempo(bpm) { this.tempo = Math.max(60, Math.min(200, bpm)); }

    start() {
      this.isPlaying = true;
      this.startDrone();
      this.startMelody();
      this.startDrumPattern();
    }

    stop() {
      this.isPlaying = false;
      this.stopDrone();
      this.stopMelody();
      this.stopDrumPattern();
    }

    pause()   { this.stop(); }
    unpause() { this.start(); }

    dispose() {
      this.stop();
    }
  }

  CT.GenerativeMusic = GenerativeMusic;
})();
"""



# =================================================================
# 7. APP INIT - Application bootstrap and router
# =================================================================

APP_INIT_JS = """\
/* ================================================================
   Chromatic Territories — Application Initializer
   Bootstrap all systems, SPA router, keyboard shortcuts.
   ================================================================ */
(function () {
  'use strict';
  const CT = window.CT = window.CT || {};

  class App {
    constructor(containerId) {
      this.containerId = containerId || 'ct-app';
      this.container = document.getElementById(this.containerId);
      this.systems = {};
      this.currentRoute = null;
      this.gameState = null;
      this.settings = this._loadSettings();

      this.fps = 0;
      this._frameCount = 0;
      this._lastFpsUpdate = 0;
      this._resizeTimer = null;
    }

    /* ---- Initialization ---- */
    async init() {
      try {
        /* 1. Canvas Renderer */
        if (CT.CanvasRenderer) {
          this.systems.renderer = new CT.CanvasRenderer('ct-canvas-container');
        }

        /* 2. UI System */
        if (CT.UISystem) {
          this.systems.ui = new CT.UISystem();
        }

        /* 3. Audio Synthesizer */
        if (CT.AudioSynthesizer) {
          this.systems.synth = new CT.AudioSynthesizer();
        }

        /* 4. Generative Music */
        if (CT.GenerativeMusic && this.systems.synth) {
          this.systems.music = new CT.GenerativeMusic(this.systems.synth);
        }

        /* 5. Gallery */
        if (CT.Gallery) {
          this.systems.gallery = new CT.Gallery();
        }

        /* 6. Tutorial */
        if (CT.TutorialSystem) {
          this.systems.tutorial = new CT.TutorialSystem();
        }

        this._setupRouter();
        this._setupKeyboard();
        this._setupResize();
        this._setupErrorBoundary();
        this._setupAudioToggle();
        this._setupSidebarToggle();

        const hash = window.location.hash.slice(1) || '/';
        this.navigate(hash);

        if (this.systems.tutorial) {
          this.systems.tutorial.checkFirstVisit();
        }

        console.log('[CT] Application initialized');
      } catch (err) {
        console.error('[CT] Init error:', err);
        this._showError(err);
      }
    }

    /* ---- Router ---- */
    _setupRouter() {
      window.addEventListener('hashchange', () => {
        const route = window.location.hash.slice(1) || '/';
        this.renderRoute(route);
      });

      document.querySelectorAll('.ct-header__link').forEach(link => {
        link.addEventListener('click', () => {
          document.querySelectorAll('.ct-header__link').forEach(l =>
            l.classList.remove('ct-header__link--active'));
          link.classList.add('ct-header__link--active');
        });
      });
    }

    navigate(route) {
      window.location.hash = route;
    }

    renderRoute(route) {
      this.currentRoute = route;
      const outlet = document.getElementById('ct-router-outlet');
      const canvasContainer = document.getElementById('ct-canvas-container');
      const hud = document.getElementById('ct-hud');

      if (outlet) outlet.innerHTML = '';
      if (canvasContainer) canvasContainer.style.display = 'none';
      if (hud) hud.style.display = 'none';

      switch (route) {
        case '/':       this._renderWelcome(outlet); break;
        case '/play':   this._renderPlay(canvasContainer, hud); break;
        case '/gallery': this._renderGallery(outlet); break;
        case '/tutorial': this._renderTutorial(); break;
        case '/settings': this._renderSettings(outlet); break;
        case '/about':   this._renderAbout(outlet); break;
        default:         this._renderWelcome(outlet); break;
      }
    }

    _renderWelcome(outlet) {
      if (!outlet) return;
      outlet.innerHTML = `
        <div class="ct-flex-center ct-h-full">
          <div class="ct-text-center" style="max-width:500px;">
            <div class="ct-animate-float ct-mb-6" style="font-size:5rem;color:var(--ct-accent);">
              &#9670;
            </div>
            <h1 class="ct-mb-4">Chromatic Territories</h1>
            <p class="ct-mb-8 ct-text-lg">
              A strategy game where every match creates a unique work of
              generative art. Color theory drives combat, composition
              determines territory health, and the game world <em>is</em>
              the artwork.
            </p>
            <div class="ct-flex-center ct-flex-col ct-gap-3">
              <button class="ct-btn ct-btn--accent ct-btn--lg" id="ct-welcome-new-game">
                &#9654; New Game
              </button>
              <button class="ct-btn ct-btn--ghost" id="ct-welcome-gallery">
                &#127912; Gallery
              </button>
              <button class="ct-btn ct-btn--ghost" id="ct-welcome-tutorial">
                &#10067; Tutorial
              </button>
            </div>
          </div>
        </div>
      `;

      const ngBtn = document.getElementById('ct-welcome-new-game');
      if (ngBtn) ngBtn.onclick = () => this.startNewGame();

      const galBtn = document.getElementById('ct-welcome-gallery');
      if (galBtn) galBtn.onclick = () => this.navigate('/gallery');

      const tutBtn = document.getElementById('ct-welcome-tutorial');
      if (tutBtn) tutBtn.onclick = () => this.navigate('/tutorial');
    }

    _renderPlay(canvasContainer, hud) {
      if (canvasContainer) canvasContainer.style.display = 'block';
      if (hud) hud.style.display = 'flex';

      if (this.systems.renderer) {
        this.systems.renderer.handleResize();
        this.systems.renderer.startRenderLoop();
      }

      if (this.gameState) {
        if (this.systems.renderer) this.systems.renderer.render(this.gameState);
        if (this.systems.ui) this.systems.ui.renderHUD(this.gameState);
      }

      if (this.systems.ui) {
        this.systems.ui.renderActionBar(null, (action) => {
          document.dispatchEvent(new CustomEvent('ct-action', { detail: { action } }));
        });
      }
    }

    _renderGallery(outlet) {
      if (!outlet) return;
      if (CT.Gallery) {
        const gallery = this.systems.gallery || new CT.Gallery();
        const el = document.createElement('div');
        el.innerHTML = CT.GALLERY_HTML || '';
        outlet.appendChild(el);
        setTimeout(() => gallery.renderGalleryGrid(), 0);
      } else {
        outlet.innerHTML = '<div class="ct-p-6"><h2>Gallery</h2><p>Gallery module not loaded.</p></div>';
      }
    }

    _renderTutorial() {
      if (this.systems.tutorial) {
        this.systems.tutorial.start();
      }
    }

    _renderSettings(outlet) {
      if (!outlet) return;
      outlet.innerHTML = '<div class="ct-flex-center ct-h-full"><div class="ct-settings" id="ct-settings-inline"></div></div>';
      if (this.systems.ui) {
        this.systems.ui.renderSettings(this.settings, (key, val) => {
          this.settings[key] = val;
          this._saveSettings();
          this._applySettings();
        });
      }
    }

    _renderAbout(outlet) {
      if (!outlet) return;
      outlet.innerHTML = `
        <div class="ct-container ct-container--sm ct-py-4">
          <h1 class="ct-mb-4">About Chromatic Territories</h1>
          <p>Chromatic Territories is a strategy game that meaningfully blends
             gaming with generative art. The game world <em>is</em> the artwork:
             color theory drives combat, composition rules determine territory
             health, and every match produces a unique piece of generative art.</p>
          <h3 class="ct-mt-6">How It Works</h3>
          <p>Players claim hexagonal cells on a procedurally-generated terrain.
             Territory colors are drawn from chosen palettes. Combat effectiveness
             depends on color relationships &mdash; complementary colors are strong
             in attack, analogous colors strengthen defense and composition.</p>
          <h3 class="ct-mt-6">Technology</h3>
          <p>Built with vanilla JavaScript, Canvas 2D, and Web Audio API.
             No frameworks, no dependencies.</p>
          <h3 class="ct-mt-6">Credits</h3>
          <p>Part of the Judgment Geometry project.</p>
        </div>
      `;
    }

    /* ---- New Game Flow ---- */
    startNewGame() {
      this.navigate('/play');
      /* In a full implementation this would show palette selection,
         board size, and AI modals in sequence. For now we create
         a default game state. */
      this.gameState = {
        turn: 1,
        maxTurns: 50,
        round: 1,
        currentPlayer: { name: 'Player 1', color: '#6366f1', isAI: false },
        chromaticity: 50,
        maxChromaticity: 100,
        compositionScore: 0,
        players: [
          { name: 'Player 1', color: '#6366f1', isAI: false },
          { name: 'AI Alpha', color: '#ef4444', isAI: true },
          { name: 'AI Beta', color: '#10b981', isAI: true }
        ],
        grid: { size: 'medium', cells: this._generateHexGrid(5) },
        territories: [],
        particles: []
      };

      if (this.systems.renderer) {
        this.systems.renderer.markAllDirty();
        this.systems.renderer.render(this.gameState);
      }
      if (this.systems.ui) this.systems.ui.renderHUD(this.gameState);

      if (this.systems.music && this.settings.music) {
        this.systems.synth.resume().then(() => this.systems.music.start());
      }
    }

    _generateHexGrid(radius) {
      const cells = [];
      for (let q = -radius; q <= radius; q++) {
        const r1 = Math.max(-radius, -q - radius);
        const r2 = Math.min(radius, -q + radius);
        for (let r = r1; r <= r2; r++) {
          cells.push({
            q, r,
            height: undefined,
            terrain: 'plains',
            owner: null
          });
        }
      }
      return cells;
    }

    /* ---- Keyboard Shortcuts ---- */
    _setupKeyboard() {
      document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' ||
            e.target.tagName === 'SELECT' || e.target.isContentEditable) return;

        switch (e.key) {
          case ' ':
            e.preventDefault();
            if (this.currentRoute === '/play') {
              const endBtn = document.getElementById('ct-end-turn');
              if (endBtn) endBtn.click();
            }
            break;
          case 'Escape':
            if (this.systems.ui) {
              this.systems.ui.closeModal();
              this.systems.ui.hideContextMenu();
            }
            break;
          case '1': this._triggerAction('expand'); break;
          case '2': this._triggerAction('fortify'); break;
          case '3': this._triggerAction('disrupt'); break;
          case '4': this._triggerAction('harmonize'); break;
          case '5': this._triggerAction('evolve'); break;
          case 'm': case 'M':
            const mm = document.getElementById('ct-minimap');
            if (mm) mm.style.display = mm.style.display === 'none' ? 'block' : 'none';
            break;
          case 'g': case 'G':
            this.navigate('/gallery');
            break;
          case 'f': case 'F':
            if (!document.fullscreenElement) {
              document.documentElement.requestFullscreen().catch(() => {});
            } else {
              document.exitFullscreen().catch(() => {});
            }
            break;
          case '+': case '=':
            if (this.systems.renderer) this.systems.renderer.zoomTo(this.systems.renderer.viewport.targetZoom + 0.2);
            break;
          case '-':
            if (this.systems.renderer) this.systems.renderer.zoomTo(this.systems.renderer.viewport.targetZoom - 0.2);
            break;
          case 'ArrowUp':    if (this.systems.renderer) this.systems.renderer.pan(0, 30); break;
          case 'ArrowDown':  if (this.systems.renderer) this.systems.renderer.pan(0, -30); break;
          case 'ArrowLeft':  if (this.systems.renderer) this.systems.renderer.pan(30, 0); break;
          case 'ArrowRight': if (this.systems.renderer) this.systems.renderer.pan(-30, 0); break;
        }
      });
    }

    _triggerAction(name) {
      const btn = document.getElementById(`ct-action-${name}`);
      if (btn && !btn.disabled) btn.click();
    }

    /* ---- Resize ---- */
    _setupResize() {
      window.addEventListener('resize', () => {
        clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => {
          if (this.systems.renderer) this.systems.renderer.handleResize();
        }, 250);
      });
    }

    /* ---- Error Boundary ---- */
    _setupErrorBoundary() {
      window.onerror = (msg, src, line, col, err) => {
        this._showError(err || new Error(msg));
        return true;
      };
      window.onunhandledrejection = (e) => {
        this._showError(e.reason || new Error('Unhandled rejection'));
      };
    }

    _showError(err) {
      const overlay = document.getElementById('ct-error-overlay');
      if (!overlay) { console.error(err); return; }
      overlay.setAttribute('aria-hidden', 'false');
      overlay.style.display = 'flex';

      const msgEl = document.getElementById('ct-error-message');
      if (msgEl) msgEl.textContent = err.message || String(err);

      const stackEl = document.getElementById('ct-error-stack');
      if (stackEl) stackEl.textContent = err.stack || '';

      const dismissBtn = document.getElementById('ct-error-dismiss');
      if (dismissBtn) {
        dismissBtn.onclick = () => {
          overlay.setAttribute('aria-hidden', 'true');
          overlay.style.display = 'none';
        };
      }
    }

    /* ---- Audio Toggle ---- */
    _setupAudioToggle() {
      const btn = document.getElementById('ct-audio-toggle');
      if (btn) {
        btn.onclick = () => {
          if (this.systems.music) {
            this.systems.music.toggleMute();
            btn.querySelector('.ct-icon').textContent =
              this.systems.music.isMuted ? '\\u{1F507}' : '\\u{1F50A}';
          }
        };
      }
    }

    /* ---- Sidebar Toggle ---- */
    _setupSidebarToggle() {
      const btn = document.getElementById('ct-sidebar-toggle');
      const sidebar = document.getElementById('ct-sidebar');
      if (btn && sidebar) {
        btn.onclick = () => {
          sidebar.classList.toggle('ct-sidebar--collapsed');
          const expanded = !sidebar.classList.contains('ct-sidebar--collapsed');
          btn.setAttribute('aria-expanded', String(expanded));
        };
      }
    }

    /* ---- Settings Persistence ---- */
    _loadSettings() {
      try {
        const raw = localStorage.getItem('ct-settings');
        return raw ? JSON.parse(raw) : this._defaultSettings();
      } catch (e) { return this._defaultSettings(); }
    }

    _saveSettings() {
      try { localStorage.setItem('ct-settings', JSON.stringify(this.settings)); } catch (e) {}
    }

    _defaultSettings() {
      return {
        masterVolume: 80, music: true, sfx: true, ambient: true,
        quality: 'medium', animations: true, particles: true, postfx: true,
        gridlines: true, coords: false, colorblind: false, uiScale: 100,
        autosave: true, hints: true
      };
    }

    _applySettings() {
      if (this.systems.synth) {
        this.systems.synth.setMasterGain(this.settings.masterVolume / 100);
      }
      document.documentElement.style.fontSize = (this.settings.uiScale / 100 * 16) + 'px';
    }

    /* ---- Cleanup ---- */
    destroy() {
      if (this.systems.renderer) this.systems.renderer.destroy();
      if (this.systems.ui) this.systems.ui.destroy();
      if (this.systems.music) this.systems.music.dispose();
      if (this.systems.synth) this.systems.synth.dispose();
      if (this.systems.tutorial) this.systems.tutorial.destroy();
      this.systems = {};
    }
  }

  CT.App = App;

  /* Auto-init on DOMContentLoaded if ct-app element exists */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      if (document.getElementById('ct-app')) {
        const app = new CT.App('ct-app');
        app.init();
        CT.app = app;
      }
    });
  } else {
    if (document.getElementById('ct-app')) {
      const app = new CT.App('ct-app');
      app.init();
      CT.app = app;
    }
  }
})();
"""
