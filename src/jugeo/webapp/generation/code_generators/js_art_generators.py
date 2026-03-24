"""Art engine code generators — noise, color theory, fractals, L-systems, particles, cellular automata, composition."""
from __future__ import annotations

from . import register


# ---------------------------------------------------------------------------
# 1. Noise Engine
# ---------------------------------------------------------------------------
@register("noise")
def generate_noise(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Noise Engine ─────────────────────────────────────────────────
class NoiseEngine {
  constructor(seed) {
    this.seed = seed || 0;
    this._perm = new Uint8Array(512);
    this._grad2 = [
      [1, 0], [-1, 0], [0, 1], [0, -1],
      [1, 1], [-1, 1], [1, -1], [-1, -1],
      [0.7071, 0.7071], [-0.7071, 0.7071],
      [0.7071, -0.7071], [-0.7071, -0.7071]
    ];
    this._buildPermTable(seed);
  }

  /* Build a repeatable permutation table from a seed using Fisher-Yates. */
  _buildPermTable(seed) {
    const p = new Uint8Array(256);
    for (let i = 0; i < 256; i++) p[i] = i;
    let s = seed | 0;
    for (let i = 255; i > 0; i--) {
      s = (s * 1664525 + 1013904223) & 0xffffffff;
      const j = ((s >>> 0) % (i + 1));
      const tmp = p[i];
      p[i] = p[j];
      p[j] = tmp;
    }
    for (let i = 0; i < 512; i++) {
      this._perm[i] = p[i & 255];
    }
  }

  /* 6t^5 − 15t^4 + 10t^3 */
  _fade(t) {
    return t * t * t * (t * (t * 6 - 15) + 10);
  }

  /* Linear interpolation */
  _lerp(a, b, t) {
    return a + t * (b - a);
  }

  /* Dot product of gradient vector and distance vector */
  _gradDot2(hash, x, y) {
    const g = this._grad2[hash % 12];
    return g[0] * x + g[1] * y;
  }

  /**
   * Classic 2-D Perlin noise.
   * Returns a value in approximately [-1, 1].
   */
  perlin2d(x, y) {
    const xi = Math.floor(x) & 255;
    const yi = Math.floor(y) & 255;
    const xf = x - Math.floor(x);
    const yf = y - Math.floor(y);

    const u = this._fade(xf);
    const v = this._fade(yf);

    const p = this._perm;
    const aa = p[p[xi] + yi];
    const ab = p[p[xi] + yi + 1];
    const ba = p[p[xi + 1] + yi];
    const bb = p[p[xi + 1] + yi + 1];

    const x1 = this._lerp(this._gradDot2(aa, xf, yf), this._gradDot2(ba, xf - 1, yf), u);
    const x2 = this._lerp(this._gradDot2(ab, xf, yf - 1), this._gradDot2(bb, xf - 1, yf - 1), u);

    return this._lerp(x1, x2, v);
  }

  /**
   * 2-D simplex noise.
   * Skew factors: F2 = (√3 − 1)/2, G2 = (3 − √3)/6.
   */
  simplex2d(x, y) {
    const F2 = 0.5 * (Math.sqrt(3.0) - 1.0);
    const G2 = (3.0 - Math.sqrt(3.0)) / 6.0;

    const s = (x + y) * F2;
    const i = Math.floor(x + s);
    const j = Math.floor(y + s);
    const t = (i + j) * G2;

    const X0 = i - t;
    const Y0 = j - t;
    const x0 = x - X0;
    const y0 = y - Y0;

    let i1, j1;
    if (x0 > y0) { i1 = 1; j1 = 0; }
    else { i1 = 0; j1 = 1; }

    const x1 = x0 - i1 + G2;
    const y1 = y0 - j1 + G2;
    const x2 = x0 - 1.0 + 2.0 * G2;
    const y2 = y0 - 1.0 + 2.0 * G2;

    const ii = i & 255;
    const jj = j & 255;
    const p = this._perm;

    const gi0 = p[ii + p[jj]] % 12;
    const gi1 = p[ii + i1 + p[jj + j1]] % 12;
    const gi2 = p[ii + 1 + p[jj + 1]] % 12;

    let n0 = 0, n1 = 0, n2 = 0;

    let t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 >= 0) {
      t0 *= t0;
      n0 = t0 * t0 * this._gradDot2(gi0, x0, y0);
    }

    let t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 >= 0) {
      t1 *= t1;
      n1 = t1 * t1 * this._gradDot2(gi1, x1, y1);
    }

    let t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 >= 0) {
      t2 *= t2;
      n2 = t2 * t2 * this._gradDot2(gi2, x2, y2);
    }

    return 70.0 * (n0 + n1 + n2);
  }

  /**
   * Fractional Brownian motion — layered Perlin noise.
   * @param {number} octaves  — number of layers (default 6)
   * @param {number} lacunarity — frequency multiplier per octave (default 2.0)
   * @param {number} gain — amplitude multiplier per octave (default 0.5)
   */
  fbm(x, y, octaves = 6, lacunarity = 2.0, gain = 0.5) {
    let value = 0;
    let amplitude = 1.0;
    let frequency = 1.0;
    let maxAmplitude = 0;

    for (let i = 0; i < octaves; i++) {
      value += amplitude * this.perlin2d(x * frequency, y * frequency);
      maxAmplitude += amplitude;
      amplitude *= gain;
      frequency *= lacunarity;
    }

    return value / maxAmplitude;
  }

  /**
   * Domain warping — feed noise-offset coordinates back into noise.
   * @param {number} strength — warp magnitude (default 1.0)
   */
  domainWarp(x, y, strength = 1.0) {
    const offsetX = this.fbm(x, y, 4) * strength;
    const offsetY = this.fbm(x + 5.2, y + 1.3, 4) * strength;
    return this.fbm(x + offsetX, y + offsetY, 6);
  }

  /**
   * Worley (cellular) noise — returns the distance to the nearest
   * feature point in a grid of random points.
   * @param {number} numPoints — grid density proxy (default 20)
   */
  worley(x, y, numPoints = 20) {
    let minDist = Infinity;
    const seed = this.seed;

    for (let i = 0; i < numPoints; i++) {
      let sx = seed + i * 127.1;
      let sy = seed + i * 311.7;
      sx = (Math.sin(sx) * 43758.5453) % 1;
      sy = (Math.sin(sy) * 43758.5453) % 1;
      if (sx < 0) sx += 1;
      if (sy < 0) sy += 1;

      const dx = x - sx * numPoints;
      const dy = y - sy * numPoints;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < minDist) minDist = dist;
    }

    return minDist;
  }

  /**
   * Turbulence — sum of absolute values of Perlin noise octaves.
   */
  turbulence(x, y, octaves = 4) {
    let value = 0;
    let amplitude = 1.0;
    let frequency = 1.0;
    let maxAmplitude = 0;

    for (let i = 0; i < octaves; i++) {
      value += amplitude * Math.abs(this.perlin2d(x * frequency, y * frequency));
      maxAmplitude += amplitude;
      amplitude *= 0.5;
      frequency *= 2.0;
    }

    return value / maxAmplitude;
  }

  /**
   * Ridged multifractal noise.
   * Each octave is: (1 − |noise|)^2 weighted by the previous octave's
   * contribution for "sharpness".
   */
  ridged(x, y, octaves = 6) {
    let value = 0;
    let amplitude = 1.0;
    let frequency = 1.0;
    let weight = 1.0;
    const gain = 2.0;
    const lacunarity = 2.0;
    const offset = 1.0;

    for (let i = 0; i < octaves; i++) {
      let signal = this.perlin2d(x * frequency, y * frequency);
      signal = offset - Math.abs(signal);
      signal *= signal;
      signal *= weight;
      weight = signal * gain;
      if (weight > 1.0) weight = 1.0;
      if (weight < 0.0) weight = 0.0;
      value += signal * amplitude;
      amplitude *= 0.5;
      frequency *= lacunarity;
    }

    return value;
  }

  /**
   * Value noise — interpolated random values on a lattice.
   * Smoother and cheaper than Perlin but with more visible grid bias.
   */
  value2d(x, y) {
    const xi = Math.floor(x);
    const yi = Math.floor(y);
    const xf = x - xi;
    const yf = y - yi;

    const u = this._fade(xf);
    const v = this._fade(yf);

    const p = this._perm;
    const v00 = p[(p[(xi & 255)] + (yi & 255)) & 511] / 255;
    const v10 = p[(p[((xi + 1) & 255)] + (yi & 255)) & 511] / 255;
    const v01 = p[(p[(xi & 255)] + ((yi + 1) & 255)) & 511] / 255;
    const v11 = p[(p[((xi + 1) & 255)] + ((yi + 1) & 255)) & 511] / 255;

    const x1 = this._lerp(v00, v10, u);
    const x2 = this._lerp(v01, v11, u);
    return this._lerp(x1, x2, v) * 2 - 1;
  }

  /**
   * Curl noise — divergence-free 2D vector field derived from Perlin.
   * Useful for fluid-like particle advection.
   * Returns {x, y} velocity vector.
   */
  curl2d(x, y, epsilon = 0.0001) {
    const dNdy = (this.perlin2d(x, y + epsilon) - this.perlin2d(x, y - epsilon)) / (2 * epsilon);
    const dNdx = (this.perlin2d(x + epsilon, y) - this.perlin2d(x - epsilon, y)) / (2 * epsilon);
    return { x: dNdy, y: -dNdx };
  }

  /**
   * Billow noise — absolute-value Perlin with doubled range, recentred.
   */
  billow(x, y, octaves = 6, lacunarity = 2.0, gain = 0.5) {
    let value = 0;
    let amplitude = 1.0;
    let frequency = 1.0;
    let maxAmplitude = 0;

    for (let i = 0; i < octaves; i++) {
      const n = Math.abs(this.perlin2d(x * frequency, y * frequency)) * 2 - 1;
      value += amplitude * n;
      maxAmplitude += amplitude;
      amplitude *= gain;
      frequency *= lacunarity;
    }

    return value / maxAmplitude;
  }

  /**
   * Swiss noise — warped ridged noise that creates eroded mountain-like terrain.
   */
  swiss(x, y, octaves = 6, lacunarity = 2.0, gain = 0.5, warp = 0.15) {
    let sum = 0;
    let amp = 1.0;
    let freq = 1.0;
    let maxAmp = 0;
    let dx = 0, dy = 0;

    for (let i = 0; i < octaves; i++) {
      const n = (1.0 - Math.abs(this.perlin2d((x + dx) * freq, (y + dy) * freq))) * 2 - 1;
      sum += amp * n;
      maxAmp += amp;
      const dxn = (this.perlin2d((x + dx + 0.01) * freq, (y + dy) * freq) - n);
      const dyn = (this.perlin2d((x + dx) * freq, (y + dy + 0.01) * freq) - n);
      dx += dxn * warp * amp;
      dy += dyn * warp * amp;
      freq *= lacunarity;
      amp *= gain * Math.max(0, Math.min(1, sum));
    }

    return sum / maxAmp;
  }

  /**
   * Voronoi tessellation — returns {dist1, dist2, cellId} for the
   * nearest and second-nearest feature points.
   * More flexible than worley — allows F2-F1, cell colouring, etc.
   */
  voronoi(x, y, scale = 8) {
    const ix = Math.floor(x / scale);
    const iy = Math.floor(y / scale);
    let d1 = Infinity, d2 = Infinity;
    let cellId = 0;

    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const cx = ix + dx;
        const cy = iy + dy;

        // Deterministic random point in cell using permutation table
        const p = this._perm;
        const hash = p[(p[(cx & 255)] + (cy & 255)) & 511];
        const px = (cx + (hash / 255)) * scale;
        const hash2 = p[(hash + 1) & 511];
        const py = (cy + (hash2 / 255)) * scale;

        const ddx = x - px;
        const ddy = y - py;
        const dist = Math.sqrt(ddx * ddx + ddy * ddy);

        if (dist < d1) {
          d2 = d1;
          d1 = dist;
          cellId = hash;
        } else if (dist < d2) {
          d2 = dist;
        }
      }
    }

    return { dist1: d1, dist2: d2, cellId };
  }

  /**
   * Generate a 2D noise field as a Float32Array (row-major).
   * @param {number} w — width
   * @param {number} h — height
   * @param {number} scaleX — coordinate scale in x
   * @param {number} scaleY — coordinate scale in y
   * @param {string} type — 'perlin' | 'simplex' | 'fbm' | 'ridged' | 'turbulence' | 'worley'
   * @returns {Float32Array}
   */
  generateField(w, h, scaleX = 0.02, scaleY = 0.02, type = 'fbm') {
    const field = new Float32Array(w * h);
    const noiseFn = {
      perlin:     (x, y) => this.perlin2d(x, y),
      simplex:    (x, y) => this.simplex2d(x, y),
      fbm:        (x, y) => this.fbm(x, y),
      ridged:     (x, y) => this.ridged(x, y),
      turbulence: (x, y) => this.turbulence(x, y),
      worley:     (x, y) => this.worley(x, y, 15),
      billow:     (x, y) => this.billow(x, y),
      value:      (x, y) => this.value2d(x, y)
    }[type] || ((x, y) => this.fbm(x, y));

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        field[y * w + x] = noiseFn(x * scaleX, y * scaleY);
      }
    }
    return field;
  }

  /**
   * Render a noise field to a canvas using a colour gradient.
   * @param {HTMLCanvasElement} canvas
   * @param {Float32Array} field
   * @param {Array<{t:number,r:number,g:number,b:number}>} gradient — colour stops
   */
  renderField(canvas, field, gradient) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const img = ctx.createImageData(w, h);

    if (!gradient) {
      gradient = [
        { t: 0.0, r: 0,   g: 0,   b: 80  },
        { t: 0.3, r: 0,   g: 50,  b: 200 },
        { t: 0.5, r: 200, g: 200, b: 100 },
        { t: 0.6, r: 50,  g: 150, b: 50  },
        { t: 0.8, r: 100, g: 80,  b: 60  },
        { t: 1.0, r: 255, g: 255, b: 255 }
      ];
    }

    // Normalise field to 0..1
    let minV = Infinity, maxV = -Infinity;
    for (let i = 0; i < field.length; i++) {
      if (field[i] < minV) minV = field[i];
      if (field[i] > maxV) maxV = field[i];
    }
    const range = maxV - minV || 1;

    for (let i = 0; i < field.length; i++) {
      const t = (field[i] - minV) / range;

      // Find gradient segment
      let lo = gradient[0], hi = gradient[gradient.length - 1];
      for (let g = 0; g < gradient.length - 1; g++) {
        if (t >= gradient[g].t && t <= gradient[g + 1].t) {
          lo = gradient[g];
          hi = gradient[g + 1];
          break;
        }
      }
      const segT = (hi.t - lo.t) > 0 ? (t - lo.t) / (hi.t - lo.t) : 0;
      const idx = i * 4;
      img.data[idx]     = Math.round(lo.r + (hi.r - lo.r) * segT);
      img.data[idx + 1] = Math.round(lo.g + (hi.g - lo.g) * segT);
      img.data[idx + 2] = Math.round(lo.b + (hi.b - lo.b) * segT);
      img.data[idx + 3] = 255;
    }

    ctx.putImageData(img, 0, 0);
  }

  /**
   * Multi-scale domain warping — cascaded warp for more complex distortions.
   * @param {number} x
   * @param {number} y
   * @param {number} iterations — number of warp passes (default 2)
   * @param {number} strength — warp magnitude per pass
   */
  multiDomainWarp(x, y, iterations = 2, strength = 1.0) {
    let px = x, py = y;
    for (let i = 0; i < iterations; i++) {
      const ox = this.fbm(px + i * 1.7, py + i * 9.2, 4) * strength;
      const oy = this.fbm(px + i * 8.3, py + i * 2.8, 4) * strength;
      px += ox;
      py += oy;
    }
    return this.fbm(px, py, 6);
  }

  /**
   * Create a seeded random number generator for auxiliary uses.
   * @param {number} seed
   * @returns {Function} () → number in [0, 1)
   */
  static seededRandom(seed) {
    let s = seed | 0;
    return function() {
      s = (s * 1664525 + 1013904223) & 0xffffffff;
      return (s >>> 0) / 4294967296;
    };
  }
}

window.CT = window.CT || {};
window.CT.NoiseEngine = NoiseEngine;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 2. Color Theory
# ---------------------------------------------------------------------------
@register("color_theory")
def generate_color_theory(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Color Theory ─────────────────────────────────────────────────
class ColorTheory {
  constructor() {
    this.NAMED_COLORS = {
      cerulean: '#007BA7', sienna: '#A0522D', ochre: '#CC7722',
      vermilion: '#E34234', cadmiumYellow: '#FFF600', cobaltBlue: '#0047AB',
      alizarinCrimson: '#E32636', burntUmber: '#8A3324', rawSienna: '#C97145',
      titaniumWhite: '#FCFFF0', ivoryBlack: '#231F20', phthaloBlue: '#000F89',
      phthaloGreen: '#123524', viridian: '#40826D', ultramarine: '#3F00FF',
      prussianBlue: '#003153', crimsonLake: '#771122', indianRed: '#CD5C5C',
      yellowOchre: '#CB9D06', naplesYellow: '#FADA5E', venetianRed: '#C80815',
      terracotta: '#E2725B', mauve: '#E0B0FF', lavender: '#E6E6FA',
      salmon: '#FA8072', coral: '#FF7F50', teal: '#008080',
      turquoise: '#40E0D0', indigo: '#4B0082', magenta: '#FF00FF',
      chartreuse: '#7FFF00', slate: '#708090', charcoal: '#36454F',
      sepia: '#704214', amber: '#FFBF00', jade: '#00A86B',
      ruby: '#E0115F', sapphire: '#0F52BA', topaz: '#FFC87C',
      peridot: '#E6E200', amethyst: '#9966CC', garnet: '#733635',
      malachite: '#0BDA51', lapis: '#26619C'
    };
  }

  /**
   * HSL → RGB.
   * @param {number} h hue 0–360
   * @param {number} s saturation 0–1
   * @param {number} l lightness 0–1
   * @returns {{r:number,g:number,b:number}} each 0–255
   */
  hslToRgb(h, s, l) {
    h = ((h % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = l - c / 2;
    let r1, g1, b1;

    if (h < 60)       { r1 = c; g1 = x; b1 = 0; }
    else if (h < 120) { r1 = x; g1 = c; b1 = 0; }
    else if (h < 180) { r1 = 0; g1 = c; b1 = x; }
    else if (h < 240) { r1 = 0; g1 = x; b1 = c; }
    else if (h < 300) { r1 = x; g1 = 0; b1 = c; }
    else              { r1 = c; g1 = 0; b1 = x; }

    return {
      r: Math.round((r1 + m) * 255),
      g: Math.round((g1 + m) * 255),
      b: Math.round((b1 + m) * 255)
    };
  }

  /**
   * RGB → HSL.
   * @param {number} r 0–255
   * @param {number} g 0–255
   * @param {number} b 0–255
   * @returns {{h:number,s:number,l:number}} h 0–360, s/l 0–1
   */
  rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    let h = 0, s = 0;

    if (max !== min) {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);

      switch (max) {
        case r: h = ((g - b) / d + (g < b ? 6 : 0)) * 60; break;
        case g: h = ((b - r) / d + 2) * 60; break;
        case b: h = ((r - g) / d + 4) * 60; break;
      }
    }

    return { h, s, l };
  }

  /**
   * HSV → RGB.
   * @param {number} h 0–360
   * @param {number} s 0–1
   * @param {number} v 0–1
   * @returns {{r:number,g:number,b:number}} each 0–255
   */
  hsvToRgb(h, s, v) {
    h = ((h % 360) + 360) % 360;
    const c = v * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = v - c;
    let r1, g1, b1;

    if (h < 60)       { r1 = c; g1 = x; b1 = 0; }
    else if (h < 120) { r1 = x; g1 = c; b1 = 0; }
    else if (h < 180) { r1 = 0; g1 = c; b1 = x; }
    else if (h < 240) { r1 = 0; g1 = x; b1 = c; }
    else if (h < 300) { r1 = x; g1 = 0; b1 = c; }
    else              { r1 = c; g1 = 0; b1 = x; }

    return {
      r: Math.round((r1 + m) * 255),
      g: Math.round((g1 + m) * 255),
      b: Math.round((b1 + m) * 255)
    };
  }

  /**
   * RGB → HSV.
   * @returns {{h:number,s:number,v:number}} h 0–360, s/v 0–1
   */
  rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const d = max - min;
    let h = 0;
    const s = max === 0 ? 0 : d / max;
    const v = max;

    if (d !== 0) {
      switch (max) {
        case r: h = ((g - b) / d + (g < b ? 6 : 0)) * 60; break;
        case g: h = ((b - r) / d + 2) * 60; break;
        case b: h = ((r - g) / d + 4) * 60; break;
      }
    }

    return { h, s, v };
  }

  /**
   * RGB → hex string.
   */
  rgbToHex(r, g, b) {
    const toHex = (c) => {
      const h = Math.max(0, Math.min(255, Math.round(c))).toString(16);
      return h.length === 1 ? '0' + h : h;
    };
    return '#' + toHex(r) + toHex(g) + toHex(b);
  }

  /**
   * Hex string → RGB object.
   */
  hexToRgb(hex) {
    hex = hex.replace(/^#/, '');
    if (hex.length === 3) {
      hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    }
    const n = parseInt(hex, 16);
    return {
      r: (n >> 16) & 255,
      g: (n >> 8) & 255,
      b: n & 255
    };
  }

  /* ── Harmony generators ────────────────────────────── */

  _wrapHue(h) {
    return ((h % 360) + 360) % 360;
  }

  /**
   * Complementary: base + 180°.
   * @param {{h,s,l}} hsl
   * @returns {Array<{h,s,l}>} two colors
   */
  complementary(hsl) {
    return [
      { h: hsl.h, s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 180), s: hsl.s, l: hsl.l }
    ];
  }

  /**
   * Triadic: three colors 120° apart.
   */
  triadic(hsl) {
    return [
      { h: hsl.h, s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 120), s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 240), s: hsl.s, l: hsl.l }
    ];
  }

  /**
   * Tetradic (rectangle): four colors.
   */
  tetradic(hsl) {
    return [
      { h: hsl.h, s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 90), s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 180), s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 270), s: hsl.s, l: hsl.l }
    ];
  }

  /**
   * Analogous: five colors evenly spread around a center hue.
   * @param {number} spread — total angular spread (default 30)
   */
  analogous(hsl, spread = 30) {
    const colors = [];
    const step = spread / 2;
    for (let i = -2; i <= 2; i++) {
      colors.push({
        h: this._wrapHue(hsl.h + i * step),
        s: hsl.s,
        l: hsl.l
      });
    }
    return colors;
  }

  /**
   * Split complementary: base + complement ±30°.
   */
  splitComplementary(hsl) {
    return [
      { h: hsl.h, s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 150), s: hsl.s, l: hsl.l },
      { h: this._wrapHue(hsl.h + 210), s: hsl.s, l: hsl.l }
    ];
  }

  /**
   * Generate a palette of `count` colors using the specified harmony.
   * @param {number} baseHue 0–360
   * @param {number} count   number of colours (default 5)
   * @param {string} harmony 'analogous'|'triadic'|'tetradic'|'complementary'|'splitComplementary'
   */
  generatePalette(baseHue, count = 5, harmony = 'analogous') {
    const base = { h: baseHue, s: 0.65, l: 0.55 };
    let raw;

    switch (harmony) {
      case 'triadic': raw = this.triadic(base); break;
      case 'tetradic': raw = this.tetradic(base); break;
      case 'complementary': raw = this.complementary(base); break;
      case 'splitComplementary': raw = this.splitComplementary(base); break;
      case 'analogous':
      default:
        raw = this.analogous(base, 60); break;
    }

    const palette = [];
    for (let i = 0; i < count; i++) {
      const src = raw[i % raw.length];
      const lightnessShift = (i - Math.floor(count / 2)) * 0.07;
      palette.push({
        h: src.h,
        s: Math.max(0, Math.min(1, src.s + (i % 2 === 0 ? 0.05 : -0.05))),
        l: Math.max(0.1, Math.min(0.9, src.l + lightnessShift))
      });
    }
    return palette;
  }

  /* ── Perception helpers ────────────────────────────── */

  /**
   * Approximate sRGB → linear.
   */
  _linearize(c) {
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }

  /**
   * Relative luminance (WCAG 2.1).
   */
  _relativeLuminance(r, g, b) {
    return 0.2126 * this._linearize(r) +
           0.7152 * this._linearize(g) +
           0.0722 * this._linearize(b);
  }

  /**
   * Approximate CIE76 colour distance via a simple sRGB → Lab path.
   * Uses the simplified D65 conversion.
   */
  colorDistance(rgb1, rgb2) {
    const toLab = (r, g, b) => {
      let rr = this._linearize(r);
      let gg = this._linearize(g);
      let bb = this._linearize(b);

      let x = (rr * 0.4124564 + gg * 0.3575761 + bb * 0.1804375) / 0.95047;
      let y = (rr * 0.2126729 + gg * 0.7151522 + bb * 0.0721750) / 1.00000;
      let z = (rr * 0.0193339 + gg * 0.1191920 + bb * 0.9503041) / 1.08883;

      const f = (t) => t > 0.008856 ? Math.cbrt(t) : (7.787 * t + 16 / 116);
      x = f(x); y = f(y); z = f(z);

      return { L: 116 * y - 16, a: 500 * (x - y), b: 200 * (y - z) };
    };

    const lab1 = toLab(rgb1.r, rgb1.g, rgb1.b);
    const lab2 = toLab(rgb2.r, rgb2.g, rgb2.b);

    const dL = lab1.L - lab2.L;
    const da = lab1.a - lab2.a;
    const db = lab1.b - lab2.b;
    return Math.sqrt(dL * dL + da * da + db * db);
  }

  /**
   * WCAG contrast ratio between two RGB colours.
   */
  contrastRatio(rgb1, rgb2) {
    const l1 = this._relativeLuminance(rgb1.r, rgb1.g, rgb1.b);
    const l2 = this._relativeLuminance(rgb2.r, rgb2.g, rgb2.b);
    const lighter = Math.max(l1, l2);
    const darker = Math.min(l1, l2);
    return (lighter + 0.05) / (darker + 0.05);
  }

  /**
   * Warm/cool score from −1 (cool) to +1 (warm).
   * Warm hues cluster around 0–60° and 300–360°; cool around 150–270°.
   */
  colorTemperature(hsl) {
    const h = this._wrapHue(hsl.h);
    const rad = (h * Math.PI) / 180;
    return Math.cos(rad - (30 * Math.PI) / 180);
  }

  /**
   * Harmony score for a set of HSL colors (0..1).
   * Measures how well the hue intervals match known harmonic templates.
   */
  harmonyScore(colors) {
    if (colors.length < 2) return 1;

    const templates = [
      [0],
      [0, 180],
      [0, 120, 240],
      [0, 90, 180, 270],
      [0, 150, 210]
    ];

    const hues = colors.map(c => this._wrapHue(c.h));
    const baseHue = hues[0];
    const offsets = hues.map(h => this._wrapHue(h - baseHue)).sort((a, b) => a - b);

    let bestScore = 0;
    for (const tmpl of templates) {
      if (tmpl.length !== offsets.length) continue;
      let totalError = 0;
      for (let i = 0; i < tmpl.length; i++) {
        let diff = Math.abs(tmpl[i] - offsets[i]);
        if (diff > 180) diff = 360 - diff;
        totalError += diff;
      }
      const avgError = totalError / tmpl.length;
      const score = Math.max(0, 1 - avgError / 60);
      if (score > bestScore) bestScore = score;
    }

    if (bestScore === 0) {
      let sumDiff = 0;
      for (let i = 1; i < offsets.length; i++) {
        sumDiff += offsets[i] - offsets[i - 1];
      }
      const avgSpacing = sumDiff / (offsets.length - 1);
      let variance = 0;
      for (let i = 1; i < offsets.length; i++) {
        const d = (offsets[i] - offsets[i - 1]) - avgSpacing;
        variance += d * d;
      }
      variance /= (offsets.length - 1);
      bestScore = Math.max(0, 1 - Math.sqrt(variance) / 60);
    }

    return bestScore;
  }

  /**
   * Interpolate between two HSL colors, taking the shortest hue path.
   * @param {number} t 0–1
   */
  interpolateHSL(hsl1, hsl2, t) {
    let h1 = hsl1.h;
    let h2 = hsl2.h;
    let dh = h2 - h1;
    if (dh > 180) dh -= 360;
    if (dh < -180) dh += 360;

    return {
      h: this._wrapHue(h1 + dh * t),
      s: hsl1.s + (hsl2.s - hsl1.s) * t,
      l: hsl1.l + (hsl2.l - hsl1.l) * t
    };
  }

  /**
   * Interpolate between two RGB colors.
   * @param {{r,g,b}} rgb1
   * @param {{r,g,b}} rgb2
   * @param {number} t 0–1
   * @returns {{r,g,b}}
   */
  interpolateRGB(rgb1, rgb2, t) {
    return {
      r: Math.round(rgb1.r + (rgb2.r - rgb1.r) * t),
      g: Math.round(rgb1.g + (rgb2.g - rgb1.g) * t),
      b: Math.round(rgb1.b + (rgb2.b - rgb1.b) * t)
    };
  }

  /**
   * Generate a multi-stop gradient as an array of RGB values.
   * @param {Array<{color:{r,g,b}, pos:number}>} stops — sorted by pos (0..1)
   * @param {number} steps — number of output colours
   * @returns {Array<{r,g,b}>}
   */
  gradient(stops, steps = 256) {
    if (!stops || stops.length === 0) return [];
    if (stops.length === 1) {
      return new Array(steps).fill(stops[0].color);
    }
    stops.sort((a, b) => a.pos - b.pos);

    const result = [];
    for (let i = 0; i < steps; i++) {
      const t = i / (steps - 1);
      let lo = stops[0], hi = stops[stops.length - 1];
      for (let s = 0; s < stops.length - 1; s++) {
        if (t >= stops[s].pos && t <= stops[s + 1].pos) {
          lo = stops[s];
          hi = stops[s + 1];
          break;
        }
      }
      const segLen = hi.pos - lo.pos;
      const segT = segLen > 0 ? (t - lo.pos) / segLen : 0;
      result.push(this.interpolateRGB(lo.color, hi.color, segT));
    }
    return result;
  }

  /**
   * Adjust saturation of an HSL colour.
   * @param {{h,s,l}} hsl
   * @param {number} amount — additive change (-1..1)
   * @returns {{h,s,l}}
   */
  adjustSaturation(hsl, amount) {
    return {
      h: hsl.h,
      s: Math.max(0, Math.min(1, hsl.s + amount)),
      l: hsl.l
    };
  }

  /**
   * Adjust lightness of an HSL colour.
   * @param {{h,s,l}} hsl
   * @param {number} amount — additive change (-1..1)
   * @returns {{h,s,l}}
   */
  adjustLightness(hsl, amount) {
    return {
      h: hsl.h,
      s: hsl.s,
      l: Math.max(0, Math.min(1, hsl.l + amount))
    };
  }

  /**
   * Desaturate a colour toward grey.
   * @param {{h,s,l}} hsl
   * @param {number} amount — 0..1 (1 = fully grey)
   */
  desaturate(hsl, amount = 1) {
    return {
      h: hsl.h,
      s: hsl.s * (1 - amount),
      l: hsl.l
    };
  }

  /**
   * Mix two RGB colours at a given ratio.
   * @param {{r,g,b}} c1
   * @param {{r,g,b}} c2
   * @param {number} ratio — 0 = all c1, 1 = all c2
   */
  mix(c1, c2, ratio = 0.5) {
    return this.interpolateRGB(c1, c2, ratio);
  }

  /**
   * Tint (lighten by mixing with white).
   * @param {{r,g,b}} rgb
   * @param {number} amount 0..1
   */
  tint(rgb, amount = 0.5) {
    return this.mix(rgb, { r: 255, g: 255, b: 255 }, amount);
  }

  /**
   * Shade (darken by mixing with black).
   * @param {{r,g,b}} rgb
   * @param {number} amount 0..1
   */
  shade(rgb, amount = 0.5) {
    return this.mix(rgb, { r: 0, g: 0, b: 0 }, amount);
  }

  /**
   * Tone (grey-shift by mixing with 50% grey).
   * @param {{r,g,b}} rgb
   * @param {number} amount 0..1
   */
  tone(rgb, amount = 0.5) {
    return this.mix(rgb, { r: 128, g: 128, b: 128 }, amount);
  }

  /**
   * WCAG accessibility rating from contrast ratio.
   * @returns {'AAA'|'AA'|'AA-large'|'fail'}
   */
  wcagRating(rgb1, rgb2) {
    const ratio = this.contrastRatio(rgb1, rgb2);
    if (ratio >= 7) return 'AAA';
    if (ratio >= 4.5) return 'AA';
    if (ratio >= 3) return 'AA-large';
    return 'fail';
  }

  /**
   * Find the most readable text colour (black or white) for a background.
   * @param {{r,g,b}} bg — background colour
   * @returns {{r,g,b}} — black or white, whichever has higher contrast
   */
  readableTextColor(bg) {
    const white = { r: 255, g: 255, b: 255 };
    const black = { r: 0, g: 0, b: 0 };
    const cw = this.contrastRatio(bg, white);
    const cb = this.contrastRatio(bg, black);
    return cw > cb ? white : black;
  }

  /**
   * Blend two colours using various blending modes.
   * @param {{r,g,b}} base
   * @param {{r,g,b}} blend
   * @param {string} mode — 'multiply'|'screen'|'overlay'|'softLight'
   * @returns {{r,g,b}}
   */
  blendMode(base, blend, mode = 'multiply') {
    const b = { r: base.r / 255, g: base.g / 255, b: base.b / 255 };
    const l = { r: blend.r / 255, g: blend.g / 255, b: blend.b / 255 };

    const calc = (bc, lc) => {
      switch (mode) {
        case 'multiply': return bc * lc;
        case 'screen':   return 1 - (1 - bc) * (1 - lc);
        case 'overlay':
          return bc < 0.5 ? 2 * bc * lc : 1 - 2 * (1 - bc) * (1 - lc);
        case 'softLight':
          return lc < 0.5
            ? bc - (1 - 2 * lc) * bc * (1 - bc)
            : bc + (2 * lc - 1) * (Math.sqrt(bc) - bc);
        default: return bc * lc;
      }
    };

    return {
      r: Math.round(calc(b.r, l.r) * 255),
      g: Math.round(calc(b.g, l.g) * 255),
      b: Math.round(calc(b.b, l.b) * 255)
    };
  }

  /**
   * Extract a dominant colour palette from an array of RGB pixels
   * using a simplified median-cut algorithm.
   * @param {Array<{r,g,b}>} pixels
   * @param {number} paletteSize
   * @returns {Array<{r,g,b}>}
   */
  extractPalette(pixels, paletteSize = 5) {
    if (pixels.length === 0) return [];

    const buckets = [pixels.slice()];

    while (buckets.length < paletteSize) {
      // Find the bucket with the largest range
      let bestIdx = 0;
      let bestRange = 0;
      for (let i = 0; i < buckets.length; i++) {
        const b = buckets[i];
        if (b.length < 2) continue;
        let minR = 255, maxR = 0, minG = 255, maxG = 0, minB = 255, maxB = 0;
        for (const p of b) {
          if (p.r < minR) minR = p.r; if (p.r > maxR) maxR = p.r;
          if (p.g < minG) minG = p.g; if (p.g > maxG) maxG = p.g;
          if (p.b < minB) minB = p.b; if (p.b > maxB) maxB = p.b;
        }
        const range = Math.max(maxR - minR, maxG - minG, maxB - minB);
        if (range > bestRange) {
          bestRange = range;
          bestIdx = i;
        }
      }

      const bucket = buckets[bestIdx];
      if (bucket.length < 2) break;

      // Determine which channel has the widest range
      let minR = 255, maxR = 0, minG = 255, maxG = 0, minB = 255, maxB = 0;
      for (const p of bucket) {
        if (p.r < minR) minR = p.r; if (p.r > maxR) maxR = p.r;
        if (p.g < minG) minG = p.g; if (p.g > maxG) maxG = p.g;
        if (p.b < minB) minB = p.b; if (p.b > maxB) maxB = p.b;
      }
      const rRange = maxR - minR;
      const gRange = maxG - minG;
      const bRange = maxB - minB;
      let sortKey;
      if (rRange >= gRange && rRange >= bRange) sortKey = 'r';
      else if (gRange >= bRange) sortKey = 'g';
      else sortKey = 'b';

      bucket.sort((a, b) => a[sortKey] - b[sortKey]);
      const mid = Math.floor(bucket.length / 2);
      buckets.splice(bestIdx, 1, bucket.slice(0, mid), bucket.slice(mid));
    }

    return buckets.map(b => {
      let sr = 0, sg = 0, sb = 0;
      for (const p of b) { sr += p.r; sg += p.g; sb += p.b; }
      return {
        r: Math.round(sr / b.length),
        g: Math.round(sg / b.length),
        b: Math.round(sb / b.length)
      };
    });
  }

  /**
   * Convert an HSL colour to a CSS string.
   */
  hslToCSS(hsl) {
    return 'hsl(' + Math.round(hsl.h) + ', ' +
           Math.round(hsl.s * 100) + '%, ' +
           Math.round(hsl.l * 100) + '%)';
  }

  /**
   * Convert an RGB colour to a CSS string.
   */
  rgbToCSS(rgb) {
    return 'rgb(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ')';
  }
}

window.CT = window.CT || {};
window.CT.ColorTheory = ColorTheory;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 3. Fractal Renderer
# ---------------------------------------------------------------------------
@register("fractal")
def generate_fractal(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Fractal Renderer ─────────────────────────────────────────────
class FractalRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas ? canvas.getContext('2d') : null;
    this.PRESETS = {
      barnsleyFern: [
        { a: 0.00, b: 0.00, c: 0.00, d: 0.16, e: 0.00, f: 0.00, p: 0.01 },
        { a: 0.85, b: 0.04, c:-0.04, d: 0.85, e: 0.00, f: 1.60, p: 0.85 },
        { a: 0.20, b:-0.26, c: 0.23, d: 0.22, e: 0.00, f: 1.60, p: 0.07 },
        { a:-0.15, b: 0.28, c: 0.26, d: 0.24, e: 0.00, f: 0.44, p: 0.07 }
      ],
      sierpinski: [
        { a: 0.5, b: 0.0, c: 0.0, d: 0.5, e: 0.0,   f: 0.0,   p: 0.333 },
        { a: 0.5, b: 0.0, c: 0.0, d: 0.5, e: 0.5,   f: 0.0,   p: 0.333 },
        { a: 0.5, b: 0.0, c: 0.0, d: 0.5, e: 0.25,  f: 0.433, p: 0.334 }
      ],
      dragonCurve: [
        { a: 0.5, b:-0.5, c: 0.5,  d: 0.5, e: 0.0, f: 0.0, p: 0.5 },
        { a:-0.5, b:-0.5, c: 0.5, d:-0.5, e: 1.0, f: 0.0, p: 0.5 }
      ]
    };
  }

  /**
   * Mandelbrot iteration with smooth colouring.
   * Returns fractional iteration count for smooth colour gradients.
   */
  mandelbrot(cx, cy, maxIter = 200) {
    let zx = 0, zy = 0;
    let i = 0;
    for (; i < maxIter; i++) {
      const zx2 = zx * zx;
      const zy2 = zy * zy;
      if (zx2 + zy2 > 4) break;
      zy = 2 * zx * zy + cy;
      zx = zx2 - zy2 + cx;
    }
    if (i === maxIter) return maxIter;
    // Smooth colouring: add fractional escape
    const log2 = Math.log(2);
    const modulus = Math.sqrt(zx * zx + zy * zy);
    return i + 1 - Math.log(Math.log(modulus)) / log2;
  }

  /**
   * Julia set iteration.
   * @param {number} zx  — initial real part (pixel x)
   * @param {number} zy  — initial imag part (pixel y)
   * @param {number} cx  — Julia constant real
   * @param {number} cy  — Julia constant imag
   */
  julia(zx, zy, cx, cy, maxIter = 200) {
    let i = 0;
    for (; i < maxIter; i++) {
      const zx2 = zx * zx;
      const zy2 = zy * zy;
      if (zx2 + zy2 > 4) break;
      const newZx = zx2 - zy2 + cx;
      zy = 2 * zx * zy + cy;
      zx = newZx;
    }
    if (i === maxIter) return maxIter;
    const modulus = Math.sqrt(zx * zx + zy * zy);
    return i + 1 - Math.log(Math.log(modulus)) / Math.log(2);
  }

  /**
   * Map a smooth iteration count to an RGB colour via a palette array.
   * Palette is [{r,g,b}, …]; the iteration is interpolated across it.
   */
  colorFromIteration(iter, maxIter, palette) {
    if (iter >= maxIter) return { r: 0, g: 0, b: 0 };
    const t = iter / maxIter;
    const idx = t * (palette.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.min(lo + 1, palette.length - 1);
    const frac = idx - lo;
    return {
      r: Math.round(palette[lo].r + (palette[hi].r - palette[lo].r) * frac),
      g: Math.round(palette[lo].g + (palette[hi].g - palette[lo].g) * frac),
      b: Math.round(palette[lo].b + (palette[hi].b - palette[lo].b) * frac)
    };
  }

  /**
   * Default colour palette (blue → white → orange → black).
   */
  _defaultPalette() {
    return [
      { r: 0,   g: 7,   b: 100 },
      { r: 32,  g: 107, b: 203 },
      { r: 237, g: 255, b: 255 },
      { r: 255, g: 170, b: 0   },
      { r: 0,   g: 2,   b: 0   }
    ];
  }

  /**
   * Render the Mandelbrot set onto the stored canvas.
   * @param {number} centerX — real-axis center
   * @param {number} centerY — imaginary-axis center
   * @param {number} zoom — 1/half-width in complex plane
   * @param {number} maxIter — max iterations
   * @param {Array}  palette — colour palette [{r,g,b},…]
   */
  renderMandelbrot(centerX = -0.5, centerY = 0, zoom = 1, maxIter = 200, palette) {
    if (!this.ctx) return;
    palette = palette || this._defaultPalette();
    const w = this.canvas.width;
    const h = this.canvas.height;
    const img = this.ctx.createImageData(w, h);
    const halfW = w / 2;
    const halfH = h / 2;
    const aspect = w / h;
    const rangeX = (1 / zoom) * aspect;
    const rangeY = 1 / zoom;

    for (let py = 0; py < h; py++) {
      for (let px = 0; px < w; px++) {
        const cx = centerX + (px - halfW) / halfW * rangeX;
        const cy = centerY + (py - halfH) / halfH * rangeY;
        const iter = this.mandelbrot(cx, cy, maxIter);
        const col = this.colorFromIteration(iter, maxIter, palette);
        const idx = (py * w + px) * 4;
        img.data[idx]     = col.r;
        img.data[idx + 1] = col.g;
        img.data[idx + 2] = col.b;
        img.data[idx + 3] = 255;
      }
    }
    this.ctx.putImageData(img, 0, 0);
  }

  /**
   * Render a Julia set onto the stored canvas.
   * @param {number} cx — Julia constant real
   * @param {number} cy — Julia constant imag
   * @param {number} zoom — 1/half-width in complex plane
   * @param {number} maxIter — max iterations
   * @param {Array}  palette — colour palette
   */
  renderJulia(cx = -0.7, cy = 0.27015, zoom = 1, maxIter = 200, palette) {
    if (!this.ctx) return;
    palette = palette || this._defaultPalette();
    const w = this.canvas.width;
    const h = this.canvas.height;
    const img = this.ctx.createImageData(w, h);
    const halfW = w / 2;
    const halfH = h / 2;
    const aspect = w / h;
    const rangeX = (1 / zoom) * aspect;
    const rangeY = 1 / zoom;

    for (let py = 0; py < h; py++) {
      for (let px = 0; px < w; px++) {
        const zx = (px - halfW) / halfW * rangeX;
        const zy = (py - halfH) / halfH * rangeY;
        const iter = this.julia(zx, zy, cx, cy, maxIter);
        const col = this.colorFromIteration(iter, maxIter, palette);
        const idx = (py * w + px) * 4;
        img.data[idx]     = col.r;
        img.data[idx + 1] = col.g;
        img.data[idx + 2] = col.b;
        img.data[idx + 3] = 255;
      }
    }
    this.ctx.putImageData(img, 0, 0);
  }

  /**
   * Iterated Function System renderer.
   * @param {Array} transforms — array of {a,b,c,d,e,f,p} affine transforms
   * @param {number} iterations — number of points to plot
   * @param {HTMLCanvasElement} canvas — optional override canvas
   */
  ifs(transforms, iterations = 50000, canvas) {
    const cvs = canvas || this.canvas;
    if (!cvs) return;
    const ctx = cvs.getContext('2d');
    const w = cvs.width;
    const h = cvs.height;

    // Normalise probabilities
    let totalP = 0;
    for (const t of transforms) totalP += t.p;
    const cumP = [];
    let running = 0;
    for (const t of transforms) {
      running += t.p / totalP;
      cumP.push(running);
    }

    // Track bounding box for the first pass
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;
    let x = 0, y = 0;

    const points = new Float32Array(iterations * 2);

    for (let i = 0; i < iterations; i++) {
      const r = Math.random();
      let ti = 0;
      while (ti < cumP.length - 1 && r > cumP[ti]) ti++;
      const t = transforms[ti];
      const nx = t.a * x + t.b * y + t.e;
      const ny = t.c * x + t.d * y + t.f;
      x = nx;
      y = ny;
      points[i * 2] = x;
      points[i * 2 + 1] = y;
      if (i > 20) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }

    // Render with fit-to-canvas mapping
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;
    const scaleX = (w - 20) / rangeX;
    const scaleY = (h - 20) / rangeY;
    const scale = Math.min(scaleX, scaleY);

    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);

    const img = ctx.createImageData(w, h);
    for (let i = 20; i < iterations; i++) {
      const px = Math.round((points[i * 2] - minX) * scale + 10);
      const py = Math.round(h - ((points[i * 2 + 1] - minY) * scale + 10));
      if (px >= 0 && px < w && py >= 0 && py < h) {
        const idx = (py * w + px) * 4;
        const depth = (i / iterations);
        img.data[idx]     = Math.min(255, img.data[idx] + Math.round(50 + 200 * depth));
        img.data[idx + 1] = Math.min(255, img.data[idx + 1] + Math.round(200 * (1 - depth)));
        img.data[idx + 2] = Math.min(255, img.data[idx + 2] + 80);
        img.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  /**
   * Burning Ship fractal — variant of Mandelbrot using abs(z).
   */
  burningShip(cx, cy, maxIter = 200) {
    let zx = 0, zy = 0;
    let i = 0;
    for (; i < maxIter; i++) {
      const zx2 = zx * zx;
      const zy2 = zy * zy;
      if (zx2 + zy2 > 4) break;
      zy = Math.abs(2 * zx * zy) + cy;
      zx = zx2 - zy2 + cx;
    }
    if (i === maxIter) return maxIter;
    const modulus = Math.sqrt(zx * zx + zy * zy);
    return i + 1 - Math.log(Math.log(modulus)) / Math.log(2);
  }

  /**
   * Render the Burning Ship fractal.
   */
  renderBurningShip(centerX = -0.4, centerY = -0.6, zoom = 1, maxIter = 200, palette) {
    if (!this.ctx) return;
    palette = palette || this._defaultPalette();
    const w = this.canvas.width;
    const h = this.canvas.height;
    const img = this.ctx.createImageData(w, h);
    const halfW = w / 2;
    const halfH = h / 2;
    const aspect = w / h;
    const rangeX = (1 / zoom) * aspect;
    const rangeY = 1 / zoom;

    for (let py = 0; py < h; py++) {
      for (let px = 0; px < w; px++) {
        const cx = centerX + (px - halfW) / halfW * rangeX;
        const cy = centerY + (py - halfH) / halfH * rangeY;
        const iter = this.burningShip(cx, cy, maxIter);
        const col = this.colorFromIteration(iter, maxIter, palette);
        const idx = (py * w + px) * 4;
        img.data[idx]     = col.r;
        img.data[idx + 1] = col.g;
        img.data[idx + 2] = col.b;
        img.data[idx + 3] = 255;
      }
    }
    this.ctx.putImageData(img, 0, 0);
  }

  /**
   * Newton fractal — roots of z^3 - 1 using Newton's method.
   * Colours based on which root the iteration converges to.
   */
  newton(zx, zy, maxIter = 50, tolerance = 1e-6) {
    // Roots of z^3 - 1
    const roots = [
      { x: 1, y: 0 },
      { x: -0.5, y: Math.sqrt(3) / 2 },
      { x: -0.5, y: -Math.sqrt(3) / 2 }
    ];

    for (let i = 0; i < maxIter; i++) {
      // f(z) = z^3 - 1, f'(z) = 3z^2
      // z_{n+1} = z_n - f(z_n)/f'(z_n)
      const zx2 = zx * zx;
      const zy2 = zy * zy;

      // z^2 = (zx + zy*i)^2 = zx^2 - zy^2 + 2*zx*zy*i
      const z2x = zx2 - zy2;
      const z2y = 2 * zx * zy;

      // z^3 = z^2 * z
      const z3x = z2x * zx - z2y * zy;
      const z3y = z2x * zy + z2y * zx;

      // f(z) = z^3 - 1
      const fx = z3x - 1;
      const fy = z3y;

      // f'(z) = 3*z^2
      const fpx = 3 * z2x;
      const fpy = 3 * z2y;

      // Division: (fx + fy*i) / (fpx + fpy*i)
      const denom = fpx * fpx + fpy * fpy;
      if (denom < 1e-12) break;
      const divX = (fx * fpx + fy * fpy) / denom;
      const divY = (fy * fpx - fx * fpy) / denom;

      zx -= divX;
      zy -= divY;

      // Check convergence to a root
      for (let r = 0; r < roots.length; r++) {
        const dx = zx - roots[r].x;
        const dy = zy - roots[r].y;
        if (dx * dx + dy * dy < tolerance) {
          return { root: r, iterations: i };
        }
      }
    }

    return { root: -1, iterations: maxIter };
  }

  /**
   * Render a Newton fractal.
   */
  renderNewton(centerX = 0, centerY = 0, zoom = 1, maxIter = 50) {
    if (!this.ctx) return;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const img = this.ctx.createImageData(w, h);
    const halfW = w / 2;
    const halfH = h / 2;
    const aspect = w / h;
    const rangeX = (2 / zoom) * aspect;
    const rangeY = 2 / zoom;

    const rootColors = [
      { r: 200, g: 50, b: 50 },
      { r: 50, g: 200, b: 50 },
      { r: 50, g: 50, b: 200 }
    ];

    for (let py = 0; py < h; py++) {
      for (let px = 0; px < w; px++) {
        const zx = centerX + (px - halfW) / halfW * rangeX;
        const zy = centerY + (py - halfH) / halfH * rangeY;
        const result = this.newton(zx, zy, maxIter);
        const idx = (py * w + px) * 4;

        if (result.root >= 0) {
          const c = rootColors[result.root];
          const bright = 1 - result.iterations / maxIter;
          img.data[idx]     = Math.round(c.r * bright);
          img.data[idx + 1] = Math.round(c.g * bright);
          img.data[idx + 2] = Math.round(c.b * bright);
        } else {
          img.data[idx] = img.data[idx + 1] = img.data[idx + 2] = 0;
        }
        img.data[idx + 3] = 255;
      }
    }
    this.ctx.putImageData(img, 0, 0);
  }

  /**
   * Orbit trap colouring for Mandelbrot — colour based on minimum
   * distance to a geometric shape (circle, cross, etc.) during iteration.
   */
  orbitTrap(cx, cy, maxIter = 200, trapType = 'circle', trapParam = 0.5) {
    let zx = 0, zy = 0;
    let minDist = Infinity;

    for (let i = 0; i < maxIter; i++) {
      const zx2 = zx * zx;
      const zy2 = zy * zy;
      if (zx2 + zy2 > 100) break;

      let dist;
      switch (trapType) {
        case 'circle':
          dist = Math.abs(Math.sqrt(zx2 + zy2) - trapParam);
          break;
        case 'cross':
          dist = Math.min(Math.abs(zx), Math.abs(zy));
          break;
        case 'point':
          dist = Math.sqrt(zx2 + zy2);
          break;
        case 'line':
          dist = Math.abs(zy - trapParam * zx);
          break;
        default:
          dist = Math.sqrt(zx2 + zy2);
      }

      if (dist < minDist) minDist = dist;

      zy = 2 * zx * zy + cy;
      zx = zx2 - zy2 + cx;
    }

    return minDist;
  }
}

window.CT = window.CT || {};
window.CT.FractalRenderer = FractalRenderer;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 4. L-System Engine
# ---------------------------------------------------------------------------
@register("lsystem")
def generate_lsystem(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── L-System Engine ──────────────────────────────────────────────
class LSystemEngine {
  constructor() {
    this.grammars = {};
    this._initBuiltins();
  }

  /**
   * Define a grammar.
   * @param {string} name
   * @param {string} axiom
   * @param {Object} rules — { symbol: replacement_string, … }
   * @param {number} angle — turning angle in degrees
   * @param {number} stepLength — turtle step size in pixels
   */
  define(name, axiom, rules, angle, stepLength) {
    this.grammars[name] = { axiom, rules, angle, stepLength };
  }

  /**
   * Expand a grammar to the given depth.
   * @param {string} name
   * @param {number} depth
   * @returns {string} expanded string
   */
  iterate(name, depth) {
    const g = this.grammars[name];
    if (!g) throw new Error('Unknown grammar: ' + name);
    let current = g.axiom;

    for (let d = 0; d < depth; d++) {
      let next = '';
      for (let i = 0; i < current.length; i++) {
        const ch = current[i];
        if (g.rules[ch] !== undefined) {
          const rule = g.rules[ch];
          if (Array.isArray(rule)) {
            // Stochastic: pick one at random
            const pick = Math.random();
            let cumulative = 0;
            let chosen = rule[0].replacement;
            for (const option of rule) {
              cumulative += option.probability;
              if (pick <= cumulative) { chosen = option.replacement; break; }
            }
            next += chosen;
          } else {
            next += rule;
          }
        } else {
          next += ch;
        }
      }
      current = next;
    }
    return current;
  }

  /**
   * Render an L-system string onto a canvas using turtle graphics.
   * Supported commands:
   *   F — move forward and draw
   *   f — move forward without drawing
   *   + — turn right
   *   - — turn left
   *   [ — push state
   *   ] — pop state
   *
   * @param {HTMLCanvasElement} canvas
   * @param {string} name — grammar name
   * @param {number} depth — iteration depth
   * @param {Object} options — { startX, startY, startAngle, color, lineWidth, fitToCanvas }
   */
  render(canvas, name, depth, options = {}) {
    const g = this.grammars[name];
    if (!g) throw new Error('Unknown grammar: ' + name);

    const str = this.iterate(name, depth);
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;

    const angleRad = (g.angle * Math.PI) / 180;
    let step = g.stepLength;

    // First pass: compute bounding box
    let tx = 0, ty = 0, ta = options.startAngle != null ? (options.startAngle * Math.PI / 180) : -Math.PI / 2;
    let minX = 0, maxX = 0, minY = 0, maxY = 0;
    const stk = [];

    for (let i = 0; i < str.length; i++) {
      const ch = str[i];
      switch (ch) {
        case 'F': case 'f':
          tx += Math.cos(ta) * step;
          ty += Math.sin(ta) * step;
          if (tx < minX) minX = tx;
          if (tx > maxX) maxX = tx;
          if (ty < minY) minY = ty;
          if (ty > maxY) maxY = ty;
          break;
        case '+': ta += angleRad; break;
        case '-': ta -= angleRad; break;
        case '[': stk.push({ x: tx, y: ty, a: ta }); break;
        case ']':
          if (stk.length) {
            const s = stk.pop();
            tx = s.x; ty = s.y; ta = s.a;
          }
          break;
      }
    }

    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;
    const padding = 20;
    const fitScale = options.fitToCanvas !== false
      ? Math.min((w - padding * 2) / rangeX, (h - padding * 2) / rangeY)
      : 1;

    const offsetX = options.startX != null ? options.startX : padding - minX * fitScale;
    const offsetY = options.startY != null ? options.startY : padding - minY * fitScale;

    // Second pass: actual rendering
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, w, h);

    tx = 0; ty = 0;
    ta = options.startAngle != null ? (options.startAngle * Math.PI / 180) : -Math.PI / 2;
    stk.length = 0;
    let currentDepth = 0;

    const baseColor = options.color || [120, 200, 80];
    ctx.lineWidth = options.lineWidth || 1;
    ctx.lineCap = 'round';

    ctx.beginPath();
    ctx.moveTo(tx * fitScale + offsetX, ty * fitScale + offsetY);

    for (let i = 0; i < str.length; i++) {
      const ch = str[i];
      switch (ch) {
        case 'F': {
          const nx = tx + Math.cos(ta) * step;
          const ny = ty + Math.sin(ta) * step;
          const t = Math.min(1, currentDepth / 8);
          const r = Math.round(baseColor[0] * (1 - t * 0.5));
          const g2 = Math.round(baseColor[1] * (1 - t * 0.3));
          const b = Math.round(baseColor[2] + (255 - baseColor[2]) * t * 0.3);
          ctx.strokeStyle = 'rgb(' + r + ',' + g2 + ',' + b + ')';
          ctx.beginPath();
          ctx.moveTo(tx * fitScale + offsetX, ty * fitScale + offsetY);
          ctx.lineTo(nx * fitScale + offsetX, ny * fitScale + offsetY);
          ctx.stroke();
          tx = nx; ty = ny;
          break;
        }
        case 'f':
          tx += Math.cos(ta) * step;
          ty += Math.sin(ta) * step;
          break;
        case '+': ta += angleRad; break;
        case '-': ta -= angleRad; break;
        case '[':
          stk.push({ x: tx, y: ty, a: ta, d: currentDepth });
          currentDepth++;
          break;
        case ']':
          if (stk.length) {
            const s = stk.pop();
            tx = s.x; ty = s.y; ta = s.a; currentDepth = s.d;
          }
          break;
      }
    }
  }

  /**
   * Animate an L-system from depth 1 to maxDepth with a delay between steps.
   * @param {HTMLCanvasElement} canvas
   * @param {string} name
   * @param {number} maxDepth
   * @param {number} delay — ms between frames (default 500)
   * @returns {Promise<void>}
   */
  animateGrowth(canvas, name, maxDepth, delay = 500) {
    return new Promise((resolve) => {
      let d = 1;
      const step = () => {
        this.render(canvas, name, d);
        d++;
        if (d <= maxDepth) {
          setTimeout(step, delay);
        } else {
          resolve();
        }
      };
      step();
    });
  }

  /* ── Built-in grammars ─────────────────────────────── */

  _initBuiltins() {
    this.define('koch', 'F', { F: 'F+F--F+F' }, 60, 4);

    this.define('dragon', 'FX', { X: 'X+YF+', Y: '-FX-Y' }, 90, 5);

    this.define('sierpinski', 'F-G-G', {
      F: 'F-G+F+G-F',
      G: 'GG'
    }, 120, 6);

    this.define('plant', 'X', {
      X: 'F+[[X]-X]-F[-FX]+X',
      F: 'FF'
    }, 25, 4);

    this.define('tree', 'F', {
      F: [
        { replacement: 'FF+[+F-F-F]-[-F+F+F]', probability: 0.5 },
        { replacement: 'FF-[-F+F+F]+[+F-F-F]', probability: 0.3 },
        { replacement: 'F[+F]F[-F]+F', probability: 0.2 }
      ]
    }, 22, 5);

    this.define('penroseSnowflake', 'F++F++F++F++F', {
      F: 'F++F++F|F-F++F'
    }, 36, 5);

    this.define('hilbert', 'A', {
      A: '-BF+AFA+FB-',
      B: '+AF-BFB-FA+'
    }, 90, 6);

    this.define('gosper', 'A', {
      A: 'A-B--B+A++AA+B-',
      B: '+A-BB--B-A++A+B'
    }, 60, 4);

    this.define('penroseP3', '[7]++[7]++[7]++[7]++[7]', {
      '6': '81++91----71[-81----61]++',
      '7': '+81--91[---61--71]+',
      '8': '-61++71[+++81++91]-',
      '9': '--81++++61[+91++++71]--71',
      '1': ''
    }, 36, 6);

    this.define('bushA', 'Y', {
      X: 'X[-FFF][+FFF]FX',
      Y: 'YFX[+Y][-Y]'
    }, 25.7, 3);

    this.define('bushB', 'F', {
      F: 'FF+[+F-F-F]-[-F+F+F]'
    }, 22.5, 4);
  }

  /**
   * Get a list of all defined grammar names.
   * @returns {string[]}
   */
  listGrammars() {
    return Object.keys(this.grammars);
  }

  /**
   * Get the string length after iteration without actually computing it.
   * Useful for estimating rendering cost.
   * @param {string} name
   * @param {number} depth
   * @returns {number}
   */
  estimateLength(name, depth) {
    const g = this.grammars[name];
    if (!g) return 0;
    let lengths = {};
    for (const ch of g.axiom) {
      lengths[ch] = (lengths[ch] || 0) + 1;
    }
    for (let d = 0; d < depth; d++) {
      const next = {};
      for (const [ch, count] of Object.entries(lengths)) {
        const rule = g.rules[ch];
        if (rule !== undefined) {
          const replacement = Array.isArray(rule) ? rule[0].replacement : rule;
          for (const rc of replacement) {
            next[rc] = (next[rc] || 0) + count;
          }
        } else {
          next[ch] = (next[ch] || 0) + count;
        }
      }
      lengths = next;
    }
    return Object.values(lengths).reduce((a, b) => a + b, 0);
  }
}

window.CT = window.CT || {};
window.CT.LSystemEngine = LSystemEngine;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 5. Particle System
# ---------------------------------------------------------------------------
@register("particle")
def generate_particle(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Particle System ──────────────────────────────────────────────
class Particle {
  constructor(x, y, vx, vy, life, color, size) {
    this.x = x;
    this.y = y;
    this.vx = vx;
    this.vy = vy;
    this.life = life;
    this.maxLife = life;
    this.color = color;         // {r,g,b}
    this.size = size;
    this.alpha = 1.0;
    this.prevX = x;
    this.prevY = y;
  }

  get alive() {
    return this.life > 0;
  }

  get progress() {
    return 1 - this.life / this.maxLife;
  }
}

class Emitter {
  /**
   * @param {number} x
   * @param {number} y
   * @param {number} rate — particles per second
   * @param {number} spread — emission cone half-angle in radians
   * @param {Array}  speedRange — [min, max]
   * @param {Array}  lifeRange — [min, max] in seconds
   * @param {Function} colorFn — () => {r,g,b}
   */
  constructor(x, y, rate, spread, speedRange, lifeRange, colorFn) {
    this.x = x;
    this.y = y;
    this.rate = rate;
    this.spread = spread;
    this.speedRange = speedRange || [50, 150];
    this.lifeRange = lifeRange || [0.5, 2.0];
    this.colorFn = colorFn || (() => ({ r: 255, g: 200, b: 50 }));
    this.angle = -Math.PI / 2;  // default: emit upward
    this.active = true;
    this._accumulator = 0;
  }

  emit(dt) {
    if (!this.active) return [];
    this._accumulator += dt * this.rate;
    const particles = [];
    while (this._accumulator >= 1) {
      this._accumulator -= 1;
      const a = this.angle + (Math.random() - 0.5) * 2 * this.spread;
      const speed = this.speedRange[0] +
        Math.random() * (this.speedRange[1] - this.speedRange[0]);
      const life = this.lifeRange[0] +
        Math.random() * (this.lifeRange[1] - this.lifeRange[0]);
      const size = 2 + Math.random() * 4;
      particles.push(new Particle(
        this.x, this.y,
        Math.cos(a) * speed, Math.sin(a) * speed,
        life, this.colorFn(), size
      ));
    }
    return particles;
  }
}

class ParticleSystem {
  constructor() {
    this.particles = [];
    this.emitters = [];
    this.forces = [];
    this.blending = 'lighter';   // additive blending by default
  }

  /**
   * Add an emitter.
   * @returns {Emitter}
   */
  addEmitter(x, y, rate = 60, spread = 0.3, speedRange, lifeRange, colorFn) {
    const em = new Emitter(x, y, rate, spread, speedRange, lifeRange, colorFn);
    this.emitters.push(em);
    return em;
  }

  /**
   * Add a global force.
   * @param {string} type — 'gravity' | 'wind' | 'attract' | 'repel' | 'orbit' | 'turbulence'
   * @param {Object} params — type-specific parameters
   */
  addForce(type, params = {}) {
    this.forces.push({ type, ...params });
  }

  /**
   * One-shot burst of particles.
   * @param {number} x
   * @param {number} y
   * @param {number} count
   * @param {Object} options — { speed, life, color, size }
   */
  burst(x, y, count, options = {}) {
    const speed = options.speed || 200;
    const life = options.life || 1.0;
    const color = options.color || { r: 255, g: 120, b: 30 };
    const size = options.size || 3;

    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const s = speed * (0.3 + Math.random() * 0.7);
      this.particles.push(new Particle(
        x, y,
        Math.cos(angle) * s, Math.sin(angle) * s,
        life * (0.5 + Math.random() * 0.5),
        { r: color.r, g: color.g, b: color.b },
        size * (0.5 + Math.random())
      ));
    }
  }

  /**
   * Apply all forces to a particle and return the net acceleration.
   */
  _computeAcceleration(p) {
    let ax = 0, ay = 0;

    for (const f of this.forces) {
      switch (f.type) {
        case 'gravity':
          ay += (f.strength || 200);
          break;

        case 'wind':
          ax += (f.x || 0);
          ay += (f.y || 0);
          break;

        case 'attract': {
          const dx = (f.x || 0) - p.x;
          const dy = (f.y || 0) - p.y;
          const dist = Math.sqrt(dx * dx + dy * dy) + 1;
          const str = (f.strength || 5000) / (dist * dist);
          ax += (dx / dist) * str;
          ay += (dy / dist) * str;
          break;
        }

        case 'repel': {
          const dx = p.x - (f.x || 0);
          const dy = p.y - (f.y || 0);
          const dist = Math.sqrt(dx * dx + dy * dy) + 1;
          const str = (f.strength || 5000) / (dist * dist);
          ax += (dx / dist) * str;
          ay += (dy / dist) * str;
          break;
        }

        case 'orbit': {
          const dx = (f.x || 0) - p.x;
          const dy = (f.y || 0) - p.y;
          const dist = Math.sqrt(dx * dx + dy * dy) + 1;
          const str = (f.strength || 100) / dist;
          // Tangential force (perpendicular to radial)
          ax += (-dy / dist) * str;
          ay += (dx / dist) * str;
          break;
        }

        case 'turbulence': {
          const freq = f.frequency || 0.01;
          const str = f.strength || 100;
          // Simple pseudo-turbulence using sin/cos
          ax += Math.sin(p.y * freq + p.x * freq * 0.7) * str;
          ay += Math.cos(p.x * freq + p.y * freq * 0.7) * str;
          break;
        }
      }
    }

    return { ax, ay };
  }

  /**
   * Update all particles (Euler integration).
   * @param {number} dt — delta time in seconds
   */
  update(dt) {
    // Spawn from emitters
    for (const em of this.emitters) {
      const newParticles = em.emit(dt);
      for (const p of newParticles) this.particles.push(p);
    }

    // Update existing particles
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.life -= dt;
      if (p.life <= 0) {
        this.particles.splice(i, 1);
        continue;
      }

      const { ax, ay } = this._computeAcceleration(p);
      p.prevX = p.x;
      p.prevY = p.y;
      p.vx += ax * dt;
      p.vy += ay * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;

      p.alpha = Math.max(0, p.life / p.maxLife);
      p.size = Math.max(0.5, p.size * (1 - dt * 0.5));
    }
  }

  /**
   * Render all particles.
   * @param {CanvasRenderingContext2D} ctx
   */
  render(ctx) {
    const prevComposite = ctx.globalCompositeOperation;
    ctx.globalCompositeOperation = this.blending;

    for (const p of this.particles) {
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);

      if (speed > 100) {
        // Trail line for fast particles
        ctx.beginPath();
        ctx.moveTo(p.prevX, p.prevY);
        ctx.lineTo(p.x, p.y);
        ctx.strokeStyle = 'rgba(' + p.color.r + ',' + p.color.g + ',' +
                          p.color.b + ',' + (p.alpha * 0.6) + ')';
        ctx.lineWidth = p.size * 0.5;
        ctx.stroke();
      }

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(' + p.color.r + ',' + p.color.g + ',' +
                      p.color.b + ',' + p.alpha + ')';
      ctx.fill();
    }

    ctx.globalCompositeOperation = prevComposite;
  }

  /**
   * Confine all particles within a rectangular boundary.
   * @param {number} x
   * @param {number} y
   * @param {number} w
   * @param {number} h
   * @param {number} bounciness — 0..1 (coefficient of restitution)
   */
  setBounds(x, y, w, h, bounciness = 0.6) {
    this._bounds = { x, y, w, h, bounciness };
  }

  /**
   * Apply boundary constraints to a particle.
   */
  _applyBounds(p) {
    if (!this._bounds) return;
    const b = this._bounds;
    if (p.x < b.x) { p.x = b.x; p.vx *= -b.bounciness; }
    if (p.x > b.x + b.w) { p.x = b.x + b.w; p.vx *= -b.bounciness; }
    if (p.y < b.y) { p.y = b.y; p.vy *= -b.bounciness; }
    if (p.y > b.y + b.h) { p.y = b.y + b.h; p.vy *= -b.bounciness; }
  }

  /**
   * Set a velocity damping factor (friction).
   * @param {number} damping — 0..1 (0 = no damping, 1 = full stop)
   */
  setDamping(damping = 0.01) {
    this._damping = 1 - damping;
  }

  /**
   * Get the number of currently live particles.
   */
  get count() {
    return this.particles.length;
  }

  /**
   * Clear all particles and emitters.
   */
  clear() {
    this.particles.length = 0;
    this.emitters.length = 0;
    this.forces.length = 0;
  }

  /**
   * Apply a color-over-life function.
   * @param {Function} fn — (progress:number) => {r,g,b} where progress is 0..1
   */
  setColorOverLife(fn) {
    this._colorOverLife = fn;
  }

  /**
   * Apply size-over-life function.
   * @param {Function} fn — (progress:number) => number
   */
  setSizeOverLife(fn) {
    this._sizeOverLife = fn;
  }

  /* ── Presets ────────────────────────────────────────── */

  static presetFire(x, y) {
    const sys = new ParticleSystem();
    sys.addEmitter(x, y, 80, 0.3, [40, 120], [0.3, 1.5], () => {
      const t = Math.random();
      return {
        r: 255,
        g: Math.round(50 + 200 * (1 - t)),
        b: Math.round(10 * (1 - t))
      };
    });
    sys.addForce('gravity', { strength: -150 });
    sys.addForce('turbulence', { frequency: 0.02, strength: 40 });
    return sys;
  }

  static presetWater(x, y) {
    const sys = new ParticleSystem();
    sys.addEmitter(x, y, 50, 0.5, [30, 100], [0.5, 2.0], () => ({
      r: 30 + Math.round(Math.random() * 50),
      g: 100 + Math.round(Math.random() * 80),
      b: 200 + Math.round(Math.random() * 55)
    }));
    sys.addForce('gravity', { strength: 100 });
    return sys;
  }

  static presetSpark(x, y) {
    const sys = new ParticleSystem();
    sys.burst(x, y, 100, { speed: 300, life: 0.8, color: { r: 255, g: 230, b: 80 } });
    sys.addForce('gravity', { strength: 300 });
    return sys;
  }

  static presetExplosion(x, y) {
    const sys = new ParticleSystem();
    sys.burst(x, y, 200, { speed: 400, life: 1.2, color: { r: 255, g: 100, b: 20 }, size: 5 });
    sys.burst(x, y, 80, { speed: 200, life: 0.6, color: { r: 255, g: 255, b: 200 }, size: 3 });
    sys.addForce('gravity', { strength: 150 });
    return sys;
  }

  static presetFlow(x, y) {
    const sys = new ParticleSystem();
    sys.addEmitter(x, y, 40, 0.1, [80, 200], [2.0, 4.0], () => ({
      r: 100 + Math.round(Math.random() * 100),
      g: 150 + Math.round(Math.random() * 100),
      b: 200 + Math.round(Math.random() * 55)
    }));
    sys.addForce('wind', { x: 50, y: 0 });
    sys.addForce('turbulence', { frequency: 0.005, strength: 60 });
    return sys;
  }
}

window.CT = window.CT || {};
window.CT.Particle = Particle;
window.CT.Emitter = Emitter;
window.CT.ParticleSystem = ParticleSystem;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 6. Cellular Automata
# ---------------------------------------------------------------------------
@register("cellular")
def generate_cellular(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Cellular Automata ────────────────────────────────────────────
class CellularAutomata {
  /**
   * @param {number} width — grid width in cells
   * @param {number} height — grid height in cells
   * @param {boolean} wrap — toroidal wrapping (default true)
   */
  constructor(width, height, wrap = true) {
    this.width = width;
    this.height = height;
    this.wrap = wrap;
    this.numStates = 2;
    this.grid = new Uint8Array(width * height);
    this._next = new Uint8Array(width * height);

    // Default rule: Conway's Game of Life
    this.birth = [3];
    this.survive = [2, 3];
    this.neighborhood = 'moore'; // 'moore' | 'vonNeumann'

    this.RULESETS = {
      life:        { birth: [3], survive: [2, 3], states: 2 },
      highLife:    { birth: [3, 6], survive: [2, 3], states: 2 },
      dayAndNight: { birth: [3, 6, 7, 8], survive: [3, 4, 6, 7, 8], states: 2 },
      seeds:       { birth: [2], survive: [], states: 2 },
      briansBrain: { birth: [2], survive: [], states: 3 }
    };

    this.PATTERNS = {
      glider: [
        [0, 1, 0],
        [0, 0, 1],
        [1, 1, 1]
      ],
      blinker: [
        [1, 1, 1]
      ],
      block: [
        [1, 1],
        [1, 1]
      ],
      beacon: [
        [1, 1, 0, 0],
        [1, 1, 0, 0],
        [0, 0, 1, 1],
        [0, 0, 1, 1]
      ],
      gliderGun: [
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,0,0,0,0,0,1,1],
        [0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,0,1,1,0,0,0,0,0,0,0,0,0,0,0,0,1,1],
        [1,1,0,0,0,0,0,0,0,0,1,0,0,0,0,0,1,0,0,0,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [1,1,0,0,0,0,0,0,0,0,1,0,0,0,1,0,1,1,0,0,0,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
      ],
      pulsar: [
        [0,0,1,1,1,0,0,0,1,1,1,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [0,0,1,1,1,0,0,0,1,1,1,0,0],
        [0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,1,1,1,0,0,0,1,1,1,0,0],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [1,0,0,0,0,1,0,1,0,0,0,0,1],
        [0,0,0,0,0,0,0,0,0,0,0,0,0],
        [0,0,1,1,1,0,0,0,1,1,1,0,0]
      ]
    };
  }

  /**
   * Set the automaton rule.
   * @param {number[]} birth — neighbour counts that cause birth
   * @param {number[]} survive — neighbour counts that keep a cell alive
   */
  setRule(birth, survive) {
    this.birth = birth;
    this.survive = survive;
  }

  /**
   * Load a named ruleset.
   * @param {string} name — one of 'life', 'highLife', 'dayAndNight', 'seeds', 'briansBrain'
   */
  loadRuleset(name) {
    const rs = this.RULESETS[name];
    if (!rs) throw new Error('Unknown ruleset: ' + name);
    this.birth = rs.birth;
    this.survive = rs.survive;
    this.numStates = rs.states;
  }

  /**
   * Randomise the grid.
   * @param {number} density — fraction of live cells (0..1, default 0.3)
   */
  randomize(density = 0.3) {
    for (let i = 0; i < this.grid.length; i++) {
      this.grid[i] = Math.random() < density ? 1 : 0;
    }
  }

  /** Clear the grid. */
  clear() {
    this.grid.fill(0);
  }

  /**
   * Get cell value with optional wrapping.
   */
  getCell(x, y) {
    if (this.wrap) {
      x = ((x % this.width) + this.width) % this.width;
      y = ((y % this.height) + this.height) % this.height;
    } else if (x < 0 || x >= this.width || y < 0 || y >= this.height) {
      return 0;
    }
    return this.grid[y * this.width + x];
  }

  /**
   * Set cell value.
   */
  setCell(x, y, state) {
    if (x < 0 || x >= this.width || y < 0 || y >= this.height) return;
    this.grid[y * this.width + x] = state;
  }

  /**
   * Count live neighbours.
   */
  _countNeighbors(x, y) {
    let count = 0;
    if (this.neighborhood === 'vonNeumann') {
      count += (this.getCell(x - 1, y) > 0 ? 1 : 0);
      count += (this.getCell(x + 1, y) > 0 ? 1 : 0);
      count += (this.getCell(x, y - 1) > 0 ? 1 : 0);
      count += (this.getCell(x, y + 1) > 0 ? 1 : 0);
    } else {
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (dx === 0 && dy === 0) continue;
          if (this.getCell(x + dx, y + dy) > 0) count++;
        }
      }
    }
    return count;
  }

  /**
   * Advance one generation.
   */
  step() {
    const w = this.width;
    const h = this.height;

    if (this.numStates === 3) {
      // Brian's Brain: 0=dead, 1=alive, 2=dying
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const idx = y * w + x;
          const state = this.grid[idx];
          if (state === 0) {
            const n = this._countNeighbors(x, y);
            this._next[idx] = this.birth.includes(n) ? 1 : 0;
          } else if (state === 1) {
            this._next[idx] = 2; // alive → dying
          } else {
            this._next[idx] = 0; // dying → dead
          }
        }
      }
    } else {
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const idx = y * w + x;
          const alive = this.grid[idx] > 0;
          const n = this._countNeighbors(x, y);
          if (alive) {
            this._next[idx] = this.survive.includes(n) ? 1 : 0;
          } else {
            this._next[idx] = this.birth.includes(n) ? 1 : 0;
          }
        }
      }
    }

    // Swap buffers
    const tmp = this.grid;
    this.grid = this._next;
    this._next = tmp;
  }

  /**
   * Insert a predefined pattern at (px, py).
   * @param {number} px — top-left x
   * @param {number} py — top-left y
   * @param {Array<Array<number>>|string} pattern — 2D array or name
   */
  insertPattern(px, py, pattern) {
    if (typeof pattern === 'string') {
      pattern = this.PATTERNS[pattern];
      if (!pattern) throw new Error('Unknown pattern: ' + pattern);
    }
    for (let y = 0; y < pattern.length; y++) {
      for (let x = 0; x < pattern[y].length; x++) {
        if (pattern[y][x]) {
          this.setCell(px + x, py + y, pattern[y][x]);
        }
      }
    }
  }

  /**
   * Render the grid onto a canvas.
   * @param {CanvasRenderingContext2D} ctx
   * @param {number} cellSize
   * @param {Object} colorMap — state → CSS colour string
   */
  render(ctx, cellSize = 4, colorMap) {
    const defaultColors = {
      0: '#111111',
      1: '#00ff88',
      2: '#005533'
    };
    const colors = colorMap || defaultColors;

    const w = this.width;
    const h = this.height;

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const state = this.grid[y * w + x];
        ctx.fillStyle = colors[state] || colors[0];
        ctx.fillRect(x * cellSize, y * cellSize, cellSize, cellSize);
      }
    }
  }

  /**
   * Fraction of live cells.
   */
  density() {
    let live = 0;
    for (let i = 0; i < this.grid.length; i++) {
      if (this.grid[i] > 0) live++;
    }
    return live / this.grid.length;
  }

  /**
   * Shannon entropy of the state distribution.
   */
  entropy() {
    const counts = new Array(this.numStates).fill(0);
    const total = this.grid.length;
    for (let i = 0; i < total; i++) {
      counts[this.grid[i]]++;
    }
    let h = 0;
    for (let i = 0; i < this.numStates; i++) {
      if (counts[i] === 0) continue;
      const p = counts[i] / total;
      h -= p * Math.log2(p);
    }
    return h;
  }

  /**
   * Count the population of each state.
   * @returns {Object} map of state → count
   */
  stateCounts() {
    const counts = {};
    for (let i = 0; i < this.numStates; i++) counts[i] = 0;
    for (let i = 0; i < this.grid.length; i++) {
      counts[this.grid[i]]++;
    }
    return counts;
  }

  /**
   * Run multiple steps at once and collect density statistics.
   * @param {number} steps
   * @returns {Array<number>} density per step
   */
  runSteps(steps) {
    const densities = [];
    for (let i = 0; i < steps; i++) {
      this.step();
      densities.push(this.density());
    }
    return densities;
  }

  /**
   * Check whether the automaton has reached a static state (no change).
   * @returns {boolean}
   */
  isStatic() {
    const w = this.width;
    const h = this.height;

    if (this.numStates === 3) {
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const idx = y * w + x;
          const state = this.grid[idx];
          if (state === 0) {
            const n = this._countNeighbors(x, y);
            if (this.birth.includes(n)) return false;
          } else if (state === 1) {
            return false; // alive cells always transition in Brian's Brain
          } else {
            // dying → dead is always a change if there are dying cells
            return false;
          }
        }
      }
      return true;
    }

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const idx = y * w + x;
        const alive = this.grid[idx] > 0;
        const n = this._countNeighbors(x, y);
        const nextAlive = alive ? this.survive.includes(n) : this.birth.includes(n);
        if ((nextAlive ? 1 : 0) !== this.grid[idx]) return false;
      }
    }
    return true;
  }

  /**
   * Export the current grid as a compact string (run-length encoded).
   * @returns {string}
   */
  exportRLE() {
    const w = this.width;
    const h = this.height;
    let rle = '';
    for (let y = 0; y < h; y++) {
      let count = 1;
      let prev = this.grid[y * w];
      for (let x = 1; x < w; x++) {
        const v = this.grid[y * w + x];
        if (v === prev) {
          count++;
        } else {
          rle += (count > 1 ? count : '') + (prev ? 'o' : 'b');
          prev = v;
          count = 1;
        }
      }
      rle += (count > 1 ? count : '') + (prev ? 'o' : 'b');
      rle += (y < h - 1) ? '$' : '!';
    }
    return rle;
  }

  /**
   * Import a grid from an RLE string.
   * @param {string} rle
   */
  importRLE(rle) {
    this.clear();
    let x = 0, y = 0;
    let numStr = '';
    for (let i = 0; i < rle.length; i++) {
      const ch = rle[i];
      if (ch >= '0' && ch <= '9') {
        numStr += ch;
      } else {
        const count = numStr ? parseInt(numStr) : 1;
        numStr = '';
        switch (ch) {
          case 'b':
            x += count;
            break;
          case 'o':
            for (let c = 0; c < count; c++) {
              this.setCell(x, y, 1);
              x++;
            }
            break;
          case '$':
            y += count;
            x = 0;
            break;
          case '!':
            return;
        }
      }
    }
  }

  /**
   * Resize the grid, preserving existing cells where possible.
   * @param {number} newWidth
   * @param {number} newHeight
   */
  resize(newWidth, newHeight) {
    const newGrid = new Uint8Array(newWidth * newHeight);
    const copyW = Math.min(this.width, newWidth);
    const copyH = Math.min(this.height, newHeight);
    for (let y = 0; y < copyH; y++) {
      for (let x = 0; x < copyW; x++) {
        newGrid[y * newWidth + x] = this.grid[y * this.width + x];
      }
    }
    this.width = newWidth;
    this.height = newHeight;
    this.grid = newGrid;
    this._next = new Uint8Array(newWidth * newHeight);
  }
}

window.CT = window.CT || {};
window.CT.CellularAutomata = CellularAutomata;
"""
    return (js, "", "")


# ---------------------------------------------------------------------------
# 7. Composition Analyzer
# ---------------------------------------------------------------------------
@register("composition")
def generate_composition(**kwargs) -> tuple[str, str, str]:
    js = """\
// ── Composition Analyzer ─────────────────────────────────────────
class CompositionAnalyzer {
  constructor() {}

  /**
   * Full composition analysis returning a scores object.
   * @param {HTMLCanvasElement} canvas
   * @returns {Object} scores — { thirds, golden, balance, contrast, colorHarmony, rhythm, overall }
   */
  analyzeCanvas(canvas) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const imageData = ctx.getImageData(0, 0, w, h);

    const thirds   = this.ruleOfThirds(imageData, w, h);
    const golden   = this.goldenRatio(imageData, w, h);
    const balance  = this.visualBalance(imageData, w, h);
    const contrast = this.contrastScore(imageData);
    const colorHarmony = this.colorHarmonyScore(imageData, 1000);
    const rhythm   = this.rhythmScore(imageData, w);

    const scores = { thirds, golden, balance, contrast, colorHarmony, rhythm };
    scores.overall = this.overallScore(scores);
    return scores;
  }

  /* ── Helper: luminance of a pixel ──────────────────── */

  _luminance(r, g, b) {
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  /* ── Helper: sample random pixels ──────────────────── */

  _samplePixels(imageData, count) {
    const data = imageData.data;
    const total = data.length / 4;
    const samples = [];
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(Math.random() * total) * 4;
      samples.push({
        r: data[idx],
        g: data[idx + 1],
        b: data[idx + 2]
      });
    }
    return samples;
  }

  /**
   * Rule of Thirds: measure how much visual weight sits near the
   * four intersection points of a 3×3 grid.
   * Returns score 0..1 (1 = most weight at intersections).
   */
  ruleOfThirds(imageData, width, height) {
    const data = imageData.data;
    const intersections = [
      { x: width / 3,     y: height / 3     },
      { x: 2 * width / 3, y: height / 3     },
      { x: width / 3,     y: 2 * height / 3 },
      { x: 2 * width / 3, y: 2 * height / 3 }
    ];
    const radius = Math.min(width, height) / 8;
    const radiusSq = radius * radius;

    let weightAtIntersections = 0;
    let totalWeight = 0;

    for (let y = 0; y < height; y += 2) {
      for (let x = 0; x < width; x += 2) {
        const idx = (y * width + x) * 4;
        const lum = this._luminance(data[idx], data[idx + 1], data[idx + 2]);
        const edgeMag = this._sobelAt(data, x, y, width, height);
        const weight = lum * 0.3 + edgeMag * 0.7;
        totalWeight += weight;

        for (const pt of intersections) {
          const dx = x - pt.x;
          const dy = y - pt.y;
          if (dx * dx + dy * dy < radiusSq) {
            weightAtIntersections += weight;
            break;
          }
        }
      }
    }

    return totalWeight > 0 ? Math.min(1, (weightAtIntersections / totalWeight) * 4) : 0;
  }

  /**
   * Simplified Sobel edge magnitude at a point.
   */
  _sobelAt(data, x, y, width, height) {
    if (x <= 0 || x >= width - 1 || y <= 0 || y >= height - 1) return 0;
    const idx = (row, col) => ((row * width + col) * 4);

    const tl = this._luminance(data[idx(y-1,x-1)], data[idx(y-1,x-1)+1], data[idx(y-1,x-1)+2]);
    const tc = this._luminance(data[idx(y-1,x)],   data[idx(y-1,x)+1],   data[idx(y-1,x)+2]);
    const tr = this._luminance(data[idx(y-1,x+1)], data[idx(y-1,x+1)+1], data[idx(y-1,x+1)+2]);
    const ml = this._luminance(data[idx(y,x-1)],   data[idx(y,x-1)+1],   data[idx(y,x-1)+2]);
    const mr = this._luminance(data[idx(y,x+1)],   data[idx(y,x+1)+1],   data[idx(y,x+1)+2]);
    const bl = this._luminance(data[idx(y+1,x-1)], data[idx(y+1,x-1)+1], data[idx(y+1,x-1)+2]);
    const bc = this._luminance(data[idx(y+1,x)],   data[idx(y+1,x)+1],   data[idx(y+1,x)+2]);
    const br = this._luminance(data[idx(y+1,x+1)], data[idx(y+1,x+1)+1], data[idx(y+1,x+1)+2]);

    const gx = -tl - 2 * ml - bl + tr + 2 * mr + br;
    const gy = -tl - 2 * tc - tr + bl + 2 * bc + br;

    return Math.sqrt(gx * gx + gy * gy) / 1442; // Normalize to ~0..1
  }

  /**
   * Golden Ratio: measure content near the golden-section lines.
   * phi ≈ 0.618
   */
  goldenRatio(imageData, width, height) {
    const data = imageData.data;
    const phi = 0.6180339887;
    const lines = [
      { axis: 'v', pos: width * phi },
      { axis: 'v', pos: width * (1 - phi) },
      { axis: 'h', pos: height * phi },
      { axis: 'h', pos: height * (1 - phi) }
    ];
    const tolerance = Math.min(width, height) / 12;

    let nearWeight = 0;
    let totalWeight = 0;

    for (let y = 0; y < height; y += 2) {
      for (let x = 0; x < width; x += 2) {
        const idx = (y * width + x) * 4;
        const lum = this._luminance(data[idx], data[idx + 1], data[idx + 2]);
        const edge = this._sobelAt(data, x, y, width, height);
        const weight = lum * 0.3 + edge * 0.7;
        totalWeight += weight;

        for (const line of lines) {
          const dist = line.axis === 'v' ? Math.abs(x - line.pos) : Math.abs(y - line.pos);
          if (dist < tolerance) {
            nearWeight += weight * (1 - dist / tolerance);
            break;
          }
        }
      }
    }

    return totalWeight > 0 ? Math.min(1, (nearWeight / totalWeight) * 3) : 0;
  }

  /**
   * Visual balance: compare luminance-weighted centre of mass to
   * canvas centre.  Returns 0..1 (1 = perfect balance).
   */
  visualBalance(imageData, width, height) {
    const data = imageData.data;
    let sumX = 0, sumY = 0, totalLum = 0;

    for (let y = 0; y < height; y += 3) {
      for (let x = 0; x < width; x += 3) {
        const idx = (y * width + x) * 4;
        const lum = this._luminance(data[idx], data[idx + 1], data[idx + 2]);
        sumX += x * lum;
        sumY += y * lum;
        totalLum += lum;
      }
    }

    if (totalLum === 0) return 1;

    const comX = sumX / totalLum;
    const comY = sumY / totalLum;
    const cx = width / 2;
    const cy = height / 2;
    const maxDist = Math.sqrt(cx * cx + cy * cy);
    const dist = Math.sqrt((comX - cx) * (comX - cx) + (comY - cy) * (comY - cy));

    return Math.max(0, 1 - dist / maxDist);
  }

  /**
   * Contrast score based on luminance range and variance.
   * Returns 0..1.
   */
  contrastScore(imageData) {
    const data = imageData.data;
    const total = data.length / 4;
    let minLum = 255, maxLum = 0;
    let sumLum = 0;
    const sampleStep = Math.max(1, Math.floor(total / 5000));
    let count = 0;
    const lumValues = [];

    for (let i = 0; i < total; i += sampleStep) {
      const idx = i * 4;
      const lum = this._luminance(data[idx], data[idx + 1], data[idx + 2]);
      if (lum < minLum) minLum = lum;
      if (lum > maxLum) maxLum = lum;
      sumLum += lum;
      lumValues.push(lum);
      count++;
    }

    const mean = sumLum / count;
    let variance = 0;
    for (let i = 0; i < lumValues.length; i++) {
      const d = lumValues[i] - mean;
      variance += d * d;
    }
    variance /= count;

    const rangeScore = (maxLum - minLum) / 255;
    const varianceScore = Math.min(1, Math.sqrt(variance) / 80);

    return rangeScore * 0.5 + varianceScore * 0.5;
  }

  /**
   * Color harmony score: sample pixels and assess their hue distribution.
   * Returns 0..1.
   */
  colorHarmonyScore(imageData, sampleCount = 1000) {
    const samples = this._samplePixels(imageData, sampleCount);
    const hues = [];

    for (const s of samples) {
      const max = Math.max(s.r, s.g, s.b);
      const min = Math.min(s.r, s.g, s.b);
      if (max - min < 10) continue; // skip grays

      let h = 0;
      const d = max - min;
      if (max === s.r)      h = ((s.g - s.b) / d + (s.g < s.b ? 6 : 0)) * 60;
      else if (max === s.g) h = ((s.b - s.r) / d + 2) * 60;
      else                  h = ((s.r - s.g) / d + 4) * 60;

      hues.push(h);
    }

    if (hues.length < 10) return 0.5;

    // Build a 12-bin hue histogram (30° bins)
    const bins = new Array(12).fill(0);
    for (const h of hues) {
      bins[Math.floor(h / 30) % 12]++;
    }

    // Harmony: how few bins dominate?
    const total = hues.length;
    const sorted = bins.slice().sort((a, b) => b - a);
    const top3 = (sorted[0] + sorted[1] + sorted[2]) / total;

    // Higher top3 concentration = more harmonic
    return Math.min(1, top3);
  }

  /**
   * Rhythm score: how regular is the vertical column luminance pattern?
   * Returns 0..1.
   */
  rhythmScore(imageData, width) {
    const data = imageData.data;
    const height = data.length / (4 * width);
    const colLum = new Float64Array(width);

    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let y = 0; y < height; y += 2) {
        const idx = (y * width + x) * 4;
        sum += this._luminance(data[idx], data[idx + 1], data[idx + 2]);
      }
      colLum[x] = sum / (height / 2);
    }

    // Compute autocorrelation at several lags to detect periodicity
    const maxLag = Math.min(width / 2, 100);
    let bestCorr = 0;

    const mean = colLum.reduce((a, b) => a + b, 0) / width;
    let variance = 0;
    for (let i = 0; i < width; i++) {
      const d = colLum[i] - mean;
      variance += d * d;
    }

    if (variance < 0.001) return 0.5; // flat image

    for (let lag = 2; lag < maxLag; lag++) {
      let corr = 0;
      for (let i = 0; i < width - lag; i++) {
        corr += (colLum[i] - mean) * (colLum[i + lag] - mean);
      }
      corr /= variance;
      if (corr > bestCorr) bestCorr = corr;
    }

    return Math.max(0, Math.min(1, bestCorr));
  }

  /**
   * Weighted combination of all sub-scores.
   * @param {Object} scores — { thirds, golden, balance, contrast, colorHarmony, rhythm }
   * @returns {number} 0..1
   */
  overallScore(scores) {
    const weights = {
      thirds: 0.20,
      golden: 0.15,
      balance: 0.20,
      contrast: 0.15,
      colorHarmony: 0.15,
      rhythm: 0.15
    };

    let total = 0;
    let weightSum = 0;
    for (const [key, w] of Object.entries(weights)) {
      if (scores[key] != null) {
        total += scores[key] * w;
        weightSum += w;
      }
    }

    return weightSum > 0 ? total / weightSum : 0;
  }

  /**
   * Analyse a rectangular sub-region of an image.
   * @param {ImageData} imageData
   * @param {number} rx — region x
   * @param {number} ry — region y
   * @param {number} rw — region width
   * @param {number} rh — region height
   */
  analyzeRegion(imageData, rx, ry, rw, rh) {
    const srcW = imageData.width;
    const data = imageData.data;

    // Extract sub-region into a new ImageData-like structure
    const regionData = new Uint8ClampedArray(rw * rh * 4);
    for (let y = 0; y < rh; y++) {
      for (let x = 0; x < rw; x++) {
        const srcIdx = ((ry + y) * srcW + (rx + x)) * 4;
        const dstIdx = (y * rw + x) * 4;
        regionData[dstIdx]     = data[srcIdx];
        regionData[dstIdx + 1] = data[srcIdx + 1];
        regionData[dstIdx + 2] = data[srcIdx + 2];
        regionData[dstIdx + 3] = data[srcIdx + 3];
      }
    }

    const regionImg = { data: regionData, width: rw, height: rh };

    const contrast = this.contrastScore(regionImg);
    const balance  = this.visualBalance(regionImg, rw, rh);
    const colorHarmony = this.colorHarmonyScore(regionImg, 500);
    const rhythm   = this.rhythmScore(regionImg, rw);

    return { contrast, balance, colorHarmony, rhythm };
  }

  /**
   * Edge density: fraction of strong edges in the image.
   * High edge density suggests complexity / texture.
   * @param {ImageData} imageData
   * @param {number} width
   * @param {number} height
   * @param {number} threshold — Sobel magnitude threshold (0..1, default 0.15)
   * @returns {number} 0..1
   */
  edgeDensity(imageData, width, height, threshold = 0.15) {
    const data = imageData.data;
    let edgeCount = 0;
    let totalCount = 0;

    for (let y = 1; y < height - 1; y += 2) {
      for (let x = 1; x < width - 1; x += 2) {
        const mag = this._sobelAt(data, x, y, width, height);
        if (mag > threshold) edgeCount++;
        totalCount++;
      }
    }

    return totalCount > 0 ? edgeCount / totalCount : 0;
  }

  /**
   * Symmetry score: how visually symmetric the image is (left ↔ right).
   * Returns 0..1 (1 = perfectly symmetric).
   */
  symmetryScore(imageData, width, height) {
    const data = imageData.data;
    let totalDiff = 0;
    let count = 0;
    const halfW = Math.floor(width / 2);

    for (let y = 0; y < height; y += 3) {
      for (let x = 0; x < halfW; x += 3) {
        const mirrorX = width - 1 - x;
        const idx1 = (y * width + x) * 4;
        const idx2 = (y * width + mirrorX) * 4;
        const dR = Math.abs(data[idx1] - data[idx2]);
        const dG = Math.abs(data[idx1 + 1] - data[idx2 + 1]);
        const dB = Math.abs(data[idx1 + 2] - data[idx2 + 2]);
        totalDiff += (dR + dG + dB) / (3 * 255);
        count++;
      }
    }

    return count > 0 ? Math.max(0, 1 - totalDiff / count) : 0;
  }

  /**
   * Focal point detection: find the (x, y) of maximum visual interest.
   * Uses a combination of luminance gradient and colour saturation.
   * @param {ImageData} imageData
   * @param {number} width
   * @param {number} height
   * @returns {{x:number, y:number, strength:number}}
   */
  findFocalPoint(imageData, width, height) {
    const data = imageData.data;
    const blockSize = 16;
    let maxInterest = 0;
    let focalX = width / 2;
    let focalY = height / 2;

    for (let by = 0; by < height; by += blockSize) {
      for (let bx = 0; bx < width; bx += blockSize) {
        let interest = 0;
        let count = 0;

        for (let y = by; y < Math.min(by + blockSize, height); y += 2) {
          for (let x = bx; x < Math.min(bx + blockSize, width); x += 2) {
            const idx = (y * width + x) * 4;
            const r = data[idx], g = data[idx + 1], b = data[idx + 2];

            const max = Math.max(r, g, b);
            const min = Math.min(r, g, b);
            const sat = max > 0 ? (max - min) / max : 0;
            const edge = this._sobelAt(data, x, y, width, height);

            interest += sat * 0.4 + edge * 0.6;
            count++;
          }
        }

        if (count > 0) {
          interest /= count;
          if (interest > maxInterest) {
            maxInterest = interest;
            focalX = bx + blockSize / 2;
            focalY = by + blockSize / 2;
          }
        }
      }
    }

    return { x: focalX, y: focalY, strength: maxInterest };
  }

  /**
   * Leading lines score: how many strong edges point toward the
   * centre of interest.
   * @param {ImageData} imageData
   * @param {number} width
   * @param {number} height
   * @returns {number} 0..1
   */
  leadingLinesScore(imageData, width, height) {
    const data = imageData.data;
    const cx = width / 2;
    const cy = height / 2;
    let alignedCount = 0;
    let edgeCount = 0;

    for (let y = 2; y < height - 2; y += 4) {
      for (let x = 2; x < width - 2; x += 4) {
        const mag = this._sobelAt(data, x, y, width, height);
        if (mag < 0.1) continue;

        edgeCount++;

        // Compute gradient direction
        const idx = (row, col) => ((row * width + col) * 4);
        const lumAt = (r, c) => this._luminance(
          data[idx(r, c)], data[idx(r, c) + 1], data[idx(r, c) + 2]
        );

        const gx = lumAt(y, x + 1) - lumAt(y, x - 1);
        const gy = lumAt(y + 1, x) - lumAt(y - 1, x);

        // Edge direction is perpendicular to gradient
        const edgeAngle = Math.atan2(-gx, gy);

        // Angle from this point toward centre
        const toCenterAngle = Math.atan2(cy - y, cx - x);

        // How aligned is the edge direction with the line to centre?
        let angleDiff = Math.abs(edgeAngle - toCenterAngle);
        if (angleDiff > Math.PI) angleDiff = 2 * Math.PI - angleDiff;

        // Consider aligned if within 30° of pointing toward centre
        if (angleDiff < Math.PI / 6 || angleDiff > 5 * Math.PI / 6) {
          alignedCount++;
        }
      }
    }

    return edgeCount > 0 ? alignedCount / edgeCount : 0;
  }

  /**
   * Depth score — rough estimate of apparent depth using luminance
   * distribution (lighter top → darker bottom suggests sky/ground).
   * @param {ImageData} imageData
   * @param {number} width
   * @param {number} height
   * @returns {number} 0..1
   */
  depthScore(imageData, width, height) {
    const data = imageData.data;
    const thirds = Math.floor(height / 3);
    let topLum = 0, midLum = 0, botLum = 0;
    let topCount = 0, midCount = 0, botCount = 0;

    for (let y = 0; y < height; y += 2) {
      for (let x = 0; x < width; x += 2) {
        const idx = (y * width + x) * 4;
        const lum = this._luminance(data[idx], data[idx + 1], data[idx + 2]);
        if (y < thirds) { topLum += lum; topCount++; }
        else if (y < 2 * thirds) { midLum += lum; midCount++; }
        else { botLum += lum; botCount++; }
      }
    }

    topLum = topCount > 0 ? topLum / topCount : 0;
    midLum = midCount > 0 ? midLum / midCount : 0;
    botLum = botCount > 0 ? botLum / botCount : 0;

    // A gradient from light to dark (or dark to light) suggests depth
    const gradient = Math.abs(topLum - botLum) / 255;
    // Variation between zones also helps
    const variation = (Math.abs(topLum - midLum) + Math.abs(midLum - botLum)) / (2 * 255);

    return Math.min(1, gradient * 0.6 + variation * 0.4);
  }

  /**
   * Full extended analysis including all available metrics.
   * @param {HTMLCanvasElement} canvas
   * @returns {Object}
   */
  analyzeCanvasExtended(canvas) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const imageData = ctx.getImageData(0, 0, w, h);

    const basic = {
      thirds:       this.ruleOfThirds(imageData, w, h),
      golden:       this.goldenRatio(imageData, w, h),
      balance:      this.visualBalance(imageData, w, h),
      contrast:     this.contrastScore(imageData),
      colorHarmony: this.colorHarmonyScore(imageData, 1000),
      rhythm:       this.rhythmScore(imageData, w)
    };

    const extended = {
      edgeDensity:  this.edgeDensity(imageData, w, h),
      symmetry:     this.symmetryScore(imageData, w, h),
      focalPoint:   this.findFocalPoint(imageData, w, h),
      leadingLines: this.leadingLinesScore(imageData, w, h),
      depth:        this.depthScore(imageData, w, h)
    };

    const scores = { ...basic, ...extended };
    scores.overall = this.overallScore(basic);
    return scores;
  }
}

window.CT = window.CT || {};
window.CT.CompositionAnalyzer = CompositionAnalyzer;
"""
    return (js, "", "")
