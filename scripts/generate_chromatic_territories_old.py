#!/usr/bin/env python3
"""Generate Chromatic Territories via jugeo-webapp obligation presheaf pipeline.

Chromatic Territories is a game where gaming and generative art are meaningfully
blended — the game mechanics ARE artistic principles. Territory is composition,
color is resource, generative brushes are weapons, and composition score is health.

The jugeo-webapp generation system uses an obligation presheaf: the app is modeled
as a sheaf over the category of web fibers (HTML structure, CSS styling, JS
interaction, navigation, animation, data layer, theme, content). Each fiber gets
a SectionProposal, and the obligation system (set to "production") enforces
minimum quality thresholds (10K+ LOC, 8+ feature systems, 12+ modules, etc.).
The CopilotGenerationDriver assembles proposals into an HTMLAppSpec, checks
obligations, auto-enriches if unmet, and generates the final output.

For Flask, FlaskAppGenerator takes an AppSpec with models, routes, templates,
and static files, enforcing Flask-specific obligations (25+ routes, 5+ models,
18+ templates, etc.).

Usage:
    python3 scripts/generate_chromatic_territories.py --target html --outdir ./ct-html
    python3 scripts/generate_chromatic_territories.py --target flask --outdir ./ct-flask
    python3 scripts/generate_chromatic_territories.py --target both --outdir ./ct
"""

import argparse
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from jugeo.webapp.generation.copilot_driver import (
    CopilotGenerationDriver,
    FiberKind,
    SectionProposal,
)
from jugeo.webapp.generation.html_generator import ComponentSpec, ComponentKind
from jugeo.webapp.generation.obligations import GenerationTarget
from jugeo.webapp.generation.flask_generator import FlaskAppGenerator
from jugeo.webapp.generation.models import (
    AppSpec,
    ModelSpec,
    ColumnSpec,
    ColumnType,
    RouteSpec,
    ResponseType,
    TemplateSpec,
    StaticFileSpec,
    ConfigSpec,
)


# ═══════════════════════════════════════════════════════════════════════════════
# THEME CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

THEME_COLORS = {
    "primary": "#6366f1",
    "primary-dark": "#4f46e5",
    "primary-light": "#818cf8",
    "secondary": "#ec4899",
    "secondary-dark": "#db2777",
    "secondary-light": "#f472b6",
    "accent": "#14b8a6",
    "accent-dark": "#0d9488",
    "accent-light": "#2dd4bf",
    "bg-dark": "#0f172a",
    "bg-medium": "#1e293b",
    "bg-light": "#334155",
    "text-primary": "#f8fafc",
    "text-secondary": "#94a3b8",
    "text-muted": "#64748b",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "info": "#3b82f6",
}

NAV_ITEMS = [
    {"label": "Home", "href": "#/"},
    {"label": "Play", "href": "#/play"},
    {"label": "Gallery", "href": "#/gallery"},
    {"label": "Tutorial", "href": "#/tutorial"},
    {"label": "Settings", "href": "#/settings"},
    {"label": "About", "href": "#/about"},
    {"label": "Leaderboard", "href": "#/leaderboard"},
    {"label": "Achievements", "href": "#/achievements"},
]


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: NoiseEngine (~420 lines)
# Real Perlin/Simplex/Worley noise with gradient hashing and fbm
# ═══════════════════════════════════════════════════════════════════════════════

NOISE_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class NoiseEngine {
    constructor(seed) {
        this.seed = seed || 42;
        this.perm = new Uint8Array(512);
        this.gradP = new Array(512);
        this.grad3 = [
            [1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],
            [1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],
            [0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]
        ];
        this.F2 = 0.5 * (Math.sqrt(3.0) - 1.0);
        this.G2 = (3.0 - Math.sqrt(3.0)) / 6.0;
        this.F3 = 1.0 / 3.0;
        this.G3 = 1.0 / 6.0;
        this._buildPermutation();
    }

    _buildPermutation() {
        const p = new Uint8Array(256);
        for (let i = 0; i < 256; i++) p[i] = i;
        let s = this.seed;
        for (let i = 255; i > 0; i--) {
            s = (s * 16807 + 0) % 2147483647;
            const j = s % (i + 1);
            const tmp = p[i];
            p[i] = p[j];
            p[j] = tmp;
        }
        for (let i = 0; i < 512; i++) {
            this.perm[i] = p[i & 255];
            this.gradP[i] = this.grad3[this.perm[i] % 12];
        }
    }

    setSeed(seed) {
        this.seed = seed;
        this._buildPermutation();
    }

    _fade(t) {
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
    }

    _lerp(a, b, t) {
        return a + t * (b - a);
    }

    _dot2(g, x, y) {
        return g[0] * x + g[1] * y;
    }

    _dot3(g, x, y, z) {
        return g[0] * x + g[1] * y + g[2] * z;
    }

    perlin2(x, y) {
        let X = Math.floor(x) & 255;
        let Y = Math.floor(y) & 255;
        x -= Math.floor(x);
        y -= Math.floor(y);
        const u = this._fade(x);
        const v = this._fade(y);
        const A = this.perm[X] + Y;
        const B = this.perm[X + 1] + Y;
        const n00 = this._dot2(this.gradP[this.perm[A]], x, y);
        const n01 = this._dot2(this.gradP[this.perm[A + 1]], x, y - 1);
        const n10 = this._dot2(this.gradP[this.perm[B]], x - 1, y);
        const n11 = this._dot2(this.gradP[this.perm[B + 1]], x - 1, y - 1);
        const nx0 = this._lerp(n00, n10, u);
        const nx1 = this._lerp(n01, n11, u);
        return this._lerp(nx0, nx1, v);
    }

    perlin3(x, y, z) {
        let X = Math.floor(x) & 255;
        let Y = Math.floor(y) & 255;
        let Z = Math.floor(z) & 255;
        x -= Math.floor(x);
        y -= Math.floor(y);
        z -= Math.floor(z);
        const u = this._fade(x);
        const v = this._fade(y);
        const w = this._fade(z);
        const A = this.perm[X] + Y;
        const AA = this.perm[A] + Z;
        const AB = this.perm[A + 1] + Z;
        const B = this.perm[X + 1] + Y;
        const BA = this.perm[B] + Z;
        const BB = this.perm[B + 1] + Z;
        return this._lerp(
            this._lerp(
                this._lerp(this._dot3(this.gradP[this.perm[AA]], x, y, z),
                           this._dot3(this.gradP[this.perm[BA]], x-1, y, z), u),
                this._lerp(this._dot3(this.gradP[this.perm[AB]], x, y-1, z),
                           this._dot3(this.gradP[this.perm[BB]], x-1, y-1, z), u), v),
            this._lerp(
                this._lerp(this._dot3(this.gradP[this.perm[AA+1]], x, y, z-1),
                           this._dot3(this.gradP[this.perm[BA+1]], x-1, y, z-1), u),
                this._lerp(this._dot3(this.gradP[this.perm[AB+1]], x, y-1, z-1),
                           this._dot3(this.gradP[this.perm[BB+1]], x-1, y-1, z-1), u), v), w);
    }

    simplex2(x, y) {
        let n0, n1, n2;
        const s = (x + y) * this.F2;
        const i = Math.floor(x + s);
        const j = Math.floor(y + s);
        const t = (i + j) * this.G2;
        const X0 = i - t;
        const Y0 = j - t;
        const x0 = x - X0;
        const y0 = y - Y0;
        let i1, j1;
        if (x0 > y0) { i1 = 1; j1 = 0; }
        else { i1 = 0; j1 = 1; }
        const x1 = x0 - i1 + this.G2;
        const y1 = y0 - j1 + this.G2;
        const x2 = x0 - 1.0 + 2.0 * this.G2;
        const y2 = y0 - 1.0 + 2.0 * this.G2;
        const ii = i & 255;
        const jj = j & 255;
        let t0 = 0.5 - x0 * x0 - y0 * y0;
        if (t0 < 0) n0 = 0.0;
        else {
            t0 *= t0;
            n0 = t0 * t0 * this._dot2(this.gradP[this.perm[ii + this.perm[jj]]], x0, y0);
        }
        let t1 = 0.5 - x1 * x1 - y1 * y1;
        if (t1 < 0) n1 = 0.0;
        else {
            t1 *= t1;
            n1 = t1 * t1 * this._dot2(this.gradP[this.perm[ii + i1 + this.perm[jj + j1]]], x1, y1);
        }
        let t2 = 0.5 - x2 * x2 - y2 * y2;
        if (t2 < 0) n2 = 0.0;
        else {
            t2 *= t2;
            n2 = t2 * t2 * this._dot2(this.gradP[this.perm[ii + 1 + this.perm[jj + 1]]], x2, y2);
        }
        return 70.0 * (n0 + n1 + n2);
    }

    worley2(x, y, k) {
        k = k || 1;
        const xi = Math.floor(x);
        const yi = Math.floor(y);
        const distances = [];
        for (let dx = -1; dx <= 1; dx++) {
            for (let dy = -1; dy <= 1; dy++) {
                const cx = xi + dx;
                const cy = yi + dy;
                const h = this.perm[(cx & 255) + this.perm[cy & 255]];
                const px = cx + (h / 255.0);
                const py = cy + (this.perm[h] / 255.0);
                const d = Math.sqrt((x - px) * (x - px) + (y - py) * (y - py));
                distances.push(d);
            }
        }
        distances.sort((a, b) => a - b);
        return Math.min(1.0, distances[Math.min(k - 1, distances.length - 1)]);
    }

    fbm(x, y, octaves, lacunarity, gain) {
        octaves = octaves || 6;
        lacunarity = lacunarity || 2.0;
        gain = gain || 0.5;
        let sum = 0;
        let amp = 1.0;
        let freq = 1.0;
        let maxAmp = 0;
        for (let i = 0; i < octaves; i++) {
            sum += this.perlin2(x * freq, y * freq) * amp;
            maxAmp += amp;
            amp *= gain;
            freq *= lacunarity;
        }
        return sum / maxAmp;
    }

    turbulence(x, y, octaves) {
        octaves = octaves || 6;
        let sum = 0;
        let amp = 1.0;
        let freq = 1.0;
        let maxAmp = 0;
        for (let i = 0; i < octaves; i++) {
            sum += Math.abs(this.perlin2(x * freq, y * freq)) * amp;
            maxAmp += amp;
            amp *= 0.5;
            freq *= 2.0;
        }
        return sum / maxAmp;
    }

    ridged(x, y, octaves, lacunarity, gain, offset) {
        octaves = octaves || 6;
        lacunarity = lacunarity || 2.0;
        gain = gain || 0.5;
        offset = offset || 1.0;
        let sum = 0;
        let amp = 1.0;
        let freq = 1.0;
        let prev = 1.0;
        for (let i = 0; i < octaves; i++) {
            let n = offset - Math.abs(this.perlin2(x * freq, y * freq));
            n = n * n;
            sum += n * amp * prev;
            prev = n;
            freq *= lacunarity;
            amp *= gain;
        }
        return sum;
    }

    domainWarp(x, y, strength, scale) {
        strength = strength || 1.0;
        scale = scale || 1.0;
        const qx = this.fbm(x * scale, y * scale, 4);
        const qy = this.fbm(x * scale + 5.2, y * scale + 1.3, 4);
        return this.fbm(
            (x + strength * qx) * scale,
            (y + strength * qy) * scale,
            6
        );
    }

    billow(x, y, octaves) {
        octaves = octaves || 6;
        let sum = 0;
        let amp = 1.0;
        let freq = 1.0;
        let maxAmp = 0;
        for (let i = 0; i < octaves; i++) {
            const n = 2.0 * Math.abs(this.perlin2(x * freq, y * freq)) - 1.0;
            sum += n * amp;
            maxAmp += amp;
            amp *= 0.5;
            freq *= 2.0;
        }
        return sum / maxAmp;
    }

    voronoi(x, y) {
        const xi = Math.floor(x);
        const yi = Math.floor(y);
        let minDist = Infinity;
        let closestId = 0;
        for (let dx = -1; dx <= 1; dx++) {
            for (let dy = -1; dy <= 1; dy++) {
                const cx = xi + dx;
                const cy = yi + dy;
                const h = this.perm[(cx & 255) + this.perm[cy & 255]];
                const px = cx + (h / 255.0);
                const py = cy + (this.perm[h] / 255.0);
                const d = (x - px) * (x - px) + (y - py) * (y - py);
                if (d < minDist) {
                    minDist = d;
                    closestId = h;
                }
            }
        }
        return { distance: Math.sqrt(minDist), id: closestId };
    }

    generateNoiseMap(width, height, scale, type) {
        type = type || 'perlin';
        scale = scale || 0.05;
        const data = new Float32Array(width * height);
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                let val;
                const nx = x * scale;
                const ny = y * scale;
                switch(type) {
                    case 'simplex': val = this.simplex2(nx, ny); break;
                    case 'worley': val = this.worley2(nx, ny, 1); break;
                    case 'fbm': val = this.fbm(nx, ny, 6); break;
                    case 'turbulence': val = this.turbulence(nx, ny, 6); break;
                    case 'ridged': val = this.ridged(nx, ny, 6); break;
                    case 'billow': val = this.billow(nx, ny, 6); break;
                    case 'domain_warp': val = this.domainWarp(nx, ny, 1.5, 1.0); break;
                    default: val = this.perlin2(nx, ny);
                }
                data[y * width + x] = val;
            }
        }
        return data;
    }

    normalizeMap(data) {
        let min = Infinity, max = -Infinity;
        for (let i = 0; i < data.length; i++) {
            if (data[i] < min) min = data[i];
            if (data[i] > max) max = data[i];
        }
        const range = max - min || 1;
        const result = new Float32Array(data.length);
        for (let i = 0; i < data.length; i++) {
            result[i] = (data[i] - min) / range;
        }
        return result;
    }

    seamless2D(x, y, w, h, scale) {
        const s = x / w;
        const t = y / h;
        const TAU = 2.0 * Math.PI;
        const nx1 = Math.cos(s * TAU) * scale;
        const ny1 = Math.sin(s * TAU) * scale;
        const nx2 = Math.cos(t * TAU) * scale;
        const ny2 = Math.sin(t * TAU) * scale;
        return this.perlin2(nx1 + nx2, ny1 + ny2);
    }
}

window.CT.NoiseEngine = NoiseEngine;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: ColorTheory (~500 lines)
# Real RGB/HSL conversion, harmony generation, palette scoring
# ═══════════════════════════════════════════════════════════════════════════════

COLOR_THEORY_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class ColorTheory {
    constructor() {
        this.cache = new Map();
    }

    rgbToHsl(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        let h, s;
        const l = (max + min) / 2;
        if (max === min) {
            h = s = 0;
        } else {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            switch (max) {
                case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
                case g: h = ((b - r) / d + 2) / 6; break;
                case b: h = ((r - g) / d + 4) / 6; break;
            }
        }
        return { h: h * 360, s: s * 100, l: l * 100 };
    }

    hslToRgb(h, s, l) {
        h /= 360; s /= 100; l /= 100;
        let r, g, b;
        if (s === 0) {
            r = g = b = l;
        } else {
            const hue2rgb = (p, q, t) => {
                if (t < 0) t += 1;
                if (t > 1) t -= 1;
                if (t < 1/6) return p + (q - p) * 6 * t;
                if (t < 1/2) return q;
                if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
                return p;
            };
            const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
            const p = 2 * l - q;
            r = hue2rgb(p, q, h + 1/3);
            g = hue2rgb(p, q, h);
            b = hue2rgb(p, q, h - 1/3);
        }
        return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
    }

    rgbToHsv(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        const d = max - min;
        let h;
        const s = max === 0 ? 0 : d / max;
        const v = max;
        if (max === min) { h = 0; }
        else {
            switch (max) {
                case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
                case g: h = ((b - r) / d + 2) / 6; break;
                case b: h = ((r - g) / d + 4) / 6; break;
            }
        }
        return { h: h * 360, s: s * 100, v: v * 100 };
    }

    hsvToRgb(h, s, v) {
        h /= 360; s /= 100; v /= 100;
        const i = Math.floor(h * 6);
        const f = h * 6 - i;
        const p = v * (1 - s);
        const q = v * (1 - f * s);
        const t = v * (1 - (1 - f) * s);
        let r, g, b;
        switch (i % 6) {
            case 0: r = v; g = t; b = p; break;
            case 1: r = q; g = v; b = p; break;
            case 2: r = p; g = v; b = t; break;
            case 3: r = p; g = q; b = v; break;
            case 4: r = t; g = p; b = v; break;
            case 5: r = v; g = p; b = q; break;
        }
        return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
    }

    hexToRgb(hex) {
        hex = hex.replace('#', '');
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        return {
            r: parseInt(hex.substring(0, 2), 16),
            g: parseInt(hex.substring(2, 4), 16),
            b: parseInt(hex.substring(4, 6), 16)
        };
    }

    rgbToHex(r, g, b) {
        const toHex = c => { const h = c.toString(16); return h.length === 1 ? '0' + h : h; };
        return '#' + toHex(r) + toHex(g) + toHex(b);
    }

    rgbToLab(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        r = r > 0.04045 ? Math.pow((r + 0.055) / 1.055, 2.4) : r / 12.92;
        g = g > 0.04045 ? Math.pow((g + 0.055) / 1.055, 2.4) : g / 12.92;
        b = b > 0.04045 ? Math.pow((b + 0.055) / 1.055, 2.4) : b / 12.92;
        let x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047;
        let y = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 1.00000;
        let z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883;
        const f = t => t > 0.008856 ? Math.pow(t, 1/3) : (7.787 * t) + 16/116;
        x = f(x); y = f(y); z = f(z);
        return { L: (116 * y) - 16, a: 500 * (x - y), b: 200 * (y - z) };
    }

    deltaE(c1, c2) {
        const lab1 = this.rgbToLab(c1.r, c1.g, c1.b);
        const lab2 = this.rgbToLab(c2.r, c2.g, c2.b);
        const dL = lab1.L - lab2.L;
        const da = lab1.a - lab2.a;
        const db = lab1.b - lab2.b;
        return Math.sqrt(dL * dL + da * da + db * db);
    }

    complementary(h, s, l) {
        return [{ h: (h + 180) % 360, s, l }];
    }

    triadic(h, s, l) {
        return [
            { h: (h + 120) % 360, s, l },
            { h: (h + 240) % 360, s, l }
        ];
    }

    tetradic(h, s, l) {
        return [
            { h: (h + 90) % 360, s, l },
            { h: (h + 180) % 360, s, l },
            { h: (h + 270) % 360, s, l }
        ];
    }

    analogous(h, s, l, spread) {
        spread = spread || 30;
        return [
            { h: (h - spread + 360) % 360, s, l },
            { h: (h + spread) % 360, s, l }
        ];
    }

    splitComplementary(h, s, l) {
        return [
            { h: (h + 150) % 360, s, l },
            { h: (h + 210) % 360, s, l }
        ];
    }

    generateHarmony(baseHex, type) {
        const rgb = this.hexToRgb(baseHex);
        const hsl = this.rgbToHsl(rgb.r, rgb.g, rgb.b);
        let harmonies;
        switch (type) {
            case 'complementary': harmonies = this.complementary(hsl.h, hsl.s, hsl.l); break;
            case 'triadic': harmonies = this.triadic(hsl.h, hsl.s, hsl.l); break;
            case 'tetradic': harmonies = this.tetradic(hsl.h, hsl.s, hsl.l); break;
            case 'analogous': harmonies = this.analogous(hsl.h, hsl.s, hsl.l); break;
            case 'split': harmonies = this.splitComplementary(hsl.h, hsl.s, hsl.l); break;
            default: harmonies = this.complementary(hsl.h, hsl.s, hsl.l);
        }
        const result = [baseHex];
        for (const h of harmonies) {
            const c = this.hslToRgb(h.h, h.s, h.l);
            result.push(this.rgbToHex(c.r, c.g, c.b));
        }
        return result;
    }

    blendColors(c1, c2, t, mode) {
        mode = mode || 'normal';
        const r1 = this.hexToRgb(c1);
        const r2 = this.hexToRgb(c2);
        let r, g, b;
        switch (mode) {
            case 'multiply':
                r = Math.round((r1.r * r2.r) / 255);
                g = Math.round((r1.g * r2.g) / 255);
                b = Math.round((r1.b * r2.b) / 255);
                break;
            case 'screen':
                r = Math.round(255 - ((255 - r1.r) * (255 - r2.r)) / 255);
                g = Math.round(255 - ((255 - r1.g) * (255 - r2.g)) / 255);
                b = Math.round(255 - ((255 - r1.b) * (255 - r2.b)) / 255);
                break;
            case 'overlay':
                r = r1.r < 128 ? Math.round(2 * r1.r * r2.r / 255) : Math.round(255 - 2 * (255 - r1.r) * (255 - r2.r) / 255);
                g = r1.g < 128 ? Math.round(2 * r1.g * r2.g / 255) : Math.round(255 - 2 * (255 - r1.g) * (255 - r2.g) / 255);
                b = r1.b < 128 ? Math.round(2 * r1.b * r2.b / 255) : Math.round(255 - 2 * (255 - r1.b) * (255 - r2.b) / 255);
                break;
            case 'hsl':
                const hsl1 = this.rgbToHsl(r1.r, r1.g, r1.b);
                const hsl2 = this.rgbToHsl(r2.r, r2.g, r2.b);
                const mh = hsl1.h + t * ((hsl2.h - hsl1.h + 540) % 360 - 180);
                const ms = hsl1.s + t * (hsl2.s - hsl1.s);
                const ml = hsl1.l + t * (hsl2.l - hsl1.l);
                const mixed = this.hslToRgb((mh + 360) % 360, ms, ml);
                return this.rgbToHex(mixed.r, mixed.g, mixed.b);
            default:
                r = Math.round(r1.r + t * (r2.r - r1.r));
                g = Math.round(r1.g + t * (r2.g - r1.g));
                b = Math.round(r1.b + t * (r2.b - r1.b));
        }
        return this.rgbToHex(
            Math.max(0, Math.min(255, r)),
            Math.max(0, Math.min(255, g)),
            Math.max(0, Math.min(255, b))
        );
    }

    luminance(r, g, b) {
        const a = [r, g, b].map(v => {
            v /= 255;
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
        });
        return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
    }

    contrastRatio(c1, c2) {
        const l1 = this.luminance(c1.r, c1.g, c1.b);
        const l2 = this.luminance(c2.r, c2.g, c2.b);
        const lighter = Math.max(l1, l2);
        const darker = Math.min(l1, l2);
        return (lighter + 0.05) / (darker + 0.05);
    }

    scorePalette(colors) {
        const rgbs = colors.map(c => typeof c === 'string' ? this.hexToRgb(c) : c);
        const hsls = rgbs.map(c => this.rgbToHsl(c.r, c.g, c.b));
        let harmonyScore = 0;
        let contrastScore = 0;
        let varietyScore = 0;
        const hues = hsls.map(h => h.h);
        const uniqueHueRanges = new Set(hues.map(h => Math.floor(h / 30)));
        varietyScore = Math.min(1.0, uniqueHueRanges.size / 6);
        for (let i = 0; i < rgbs.length; i++) {
            for (let j = i + 1; j < rgbs.length; j++) {
                const ratio = this.contrastRatio(rgbs[i], rgbs[j]);
                contrastScore += Math.min(1.0, ratio / 7.0);
                const hueDiff = Math.abs(hues[i] - hues[j]);
                const normalizedDiff = Math.min(hueDiff, 360 - hueDiff);
                if (normalizedDiff > 150 && normalizedDiff < 210) harmonyScore += 1.0;
                else if (normalizedDiff > 100 && normalizedDiff < 140) harmonyScore += 0.8;
                else if (normalizedDiff < 40) harmonyScore += 0.6;
                else harmonyScore += 0.3;
            }
        }
        const pairs = rgbs.length * (rgbs.length - 1) / 2 || 1;
        harmonyScore /= pairs;
        contrastScore /= pairs;
        return {
            harmony: Math.min(1.0, harmonyScore),
            contrast: Math.min(1.0, contrastScore),
            variety: varietyScore,
            overall: (harmonyScore * 0.4 + contrastScore * 0.3 + varietyScore * 0.3)
        };
    }

    generatePalette(baseHex, count, strategy) {
        count = count || 5;
        strategy = strategy || 'harmonious';
        const rgb = this.hexToRgb(baseHex);
        const hsl = this.rgbToHsl(rgb.r, rgb.g, rgb.b);
        const palette = [baseHex];
        switch (strategy) {
            case 'harmonious':
                for (let i = 1; i < count; i++) {
                    const angle = (360 / count) * i;
                    const c = this.hslToRgb((hsl.h + angle) % 360, hsl.s, hsl.l);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
                break;
            case 'warm':
                for (let i = 1; i < count; i++) {
                    const h = (hsl.h + (i * 15)) % 360;
                    const s = Math.min(100, hsl.s + i * 5);
                    const l = Math.max(20, hsl.l - i * 8);
                    const c = this.hslToRgb(h, s, l);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
                break;
            case 'cool':
                for (let i = 1; i < count; i++) {
                    const h = (hsl.h - (i * 15) + 360) % 360;
                    const s = Math.max(20, hsl.s - i * 5);
                    const l = Math.min(80, hsl.l + i * 8);
                    const c = this.hslToRgb(h, s, l);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
                break;
            case 'monochrome':
                for (let i = 1; i < count; i++) {
                    const l = 20 + (60 / (count - 1)) * i;
                    const c = this.hslToRgb(hsl.h, hsl.s, l);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
                break;
            case 'vibrant':
                for (let i = 1; i < count; i++) {
                    const angle = (137.508 * i) % 360;
                    const c = this.hslToRgb(angle, Math.min(100, 70 + i * 5), 55);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
                break;
            default:
                for (let i = 1; i < count; i++) {
                    const angle = (360 / count) * i;
                    const c = this.hslToRgb((hsl.h + angle) % 360, hsl.s, hsl.l);
                    palette.push(this.rgbToHex(c.r, c.g, c.b));
                }
        }
        return palette;
    }

    getColorTemperature(hex) {
        const rgb = this.hexToRgb(hex);
        const hsl = this.rgbToHsl(rgb.r, rgb.g, rgb.b);
        if (hsl.h >= 0 && hsl.h < 80) return 'warm';
        if (hsl.h >= 80 && hsl.h < 160) return 'neutral-warm';
        if (hsl.h >= 160 && hsl.h < 250) return 'cool';
        if (hsl.h >= 250 && hsl.h < 330) return 'neutral-cool';
        return 'warm';
    }

    getColorMood(hex) {
        const rgb = this.hexToRgb(hex);
        const hsl = this.rgbToHsl(rgb.r, rgb.g, rgb.b);
        if (hsl.s < 20) return hsl.l > 60 ? 'serene' : 'somber';
        if (hsl.s > 70 && hsl.l > 50) return 'energetic';
        if (hsl.s > 70 && hsl.l < 40) return 'dramatic';
        if (hsl.h > 200 && hsl.h < 260) return 'calm';
        if (hsl.h > 0 && hsl.h < 40) return 'passionate';
        if (hsl.h > 90 && hsl.h < 150) return 'natural';
        return 'balanced';
    }

    colorDistance(c1Hex, c2Hex) {
        const c1 = this.hexToRgb(c1Hex);
        const c2 = this.hexToRgb(c2Hex);
        return this.deltaE(c1, c2);
    }

    isComplementary(h1, h2) {
        const diff = Math.abs(h1 - h2);
        const norm = Math.min(diff, 360 - diff);
        return norm > 150 && norm < 210;
    }

    isAnalogous(h1, h2) {
        const diff = Math.abs(h1 - h2);
        const norm = Math.min(diff, 360 - diff);
        return norm < 45;
    }

    colorRelationship(hex1, hex2) {
        const hsl1 = this.rgbToHsl(...Object.values(this.hexToRgb(hex1)));
        const hsl2 = this.rgbToHsl(...Object.values(this.hexToRgb(hex2)));
        const diff = Math.min(Math.abs(hsl1.h - hsl2.h), 360 - Math.abs(hsl1.h - hsl2.h));
        if (diff < 15) return { type: 'identical', strength: 1.0 };
        if (diff < 45) return { type: 'analogous', strength: 0.8 };
        if (diff > 80 && diff < 100) return { type: 'triadic', strength: 0.6 };
        if (diff > 150 && diff < 210) return { type: 'complementary', strength: 0.9 };
        if (diff > 130 && diff < 160) return { type: 'split-complementary', strength: 0.7 };
        return { type: 'dissonant', strength: 0.3 };
    }

    interpolatePalette(palette, steps) {
        const result = [];
        for (let i = 0; i < palette.length - 1; i++) {
            for (let s = 0; s < steps; s++) {
                const t = s / steps;
                result.push(this.blendColors(palette[i], palette[i + 1], t, 'hsl'));
            }
        }
        result.push(palette[palette.length - 1]);
        return result;
    }
}

window.CT.ColorTheory = ColorTheory;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: FractalRenderer (~350 lines)
# Real Mandelbrot/Julia with smooth coloring, IFS presets
# ═══════════════════════════════════════════════════════════════════════════════

FRACTAL_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class FractalRenderer {
    constructor() {
        this.maxIterations = 256;
        this.escapeRadius = 4.0;
        this.colorMode = 'smooth';
        this.juliaC = { re: -0.7, im: 0.27015 };
        this.ifsPresets = {
            sierpinski: [
                { a: 0.5, b: 0, c: 0, d: 0.5, e: 0, f: 0, p: 0.33 },
                { a: 0.5, b: 0, c: 0, d: 0.5, e: 0.5, f: 0, p: 0.33 },
                { a: 0.5, b: 0, c: 0, d: 0.5, e: 0.25, f: 0.433, p: 0.34 }
            ],
            fern: [
                { a: 0, b: 0, c: 0, d: 0.16, e: 0, f: 0, p: 0.01 },
                { a: 0.85, b: 0.04, c: -0.04, d: 0.85, e: 0, f: 1.6, p: 0.85 },
                { a: 0.20, b: -0.26, c: 0.23, d: 0.22, e: 0, f: 1.6, p: 0.07 },
                { a: -0.15, b: 0.28, c: 0.26, d: 0.24, e: 0, f: 0.44, p: 0.07 }
            ],
            tree: [
                { a: 0.195, b: -0.488, c: 0.344, d: 0.443, e: 0.4431, f: 0.2452, p: 0.25 },
                { a: 0.462, b: 0.414, c: -0.252, d: 0.361, e: 0.2511, f: 0.5692, p: 0.25 },
                { a: -0.058, b: -0.07, c: 0.453, d: -0.111, e: 0.5976, f: 0.0969, p: 0.25 },
                { a: -0.035, b: 0.07, c: -0.469, d: -0.022, e: 0.4884, f: 0.5069, p: 0.25 }
            ],
            dragon: [
                { a: 0.824074, b: 0.281482, c: -0.212346, d: 0.864198, e: -1.882290, f: -0.110607, p: 0.787473 },
                { a: 0.088272, b: 0.520988, c: -0.463889, d: -0.377778, e: 0.785360, f: 8.095795, p: 0.212527 }
            ]
        };
    }

    mandelbrot(px, py, centerX, centerY, zoom) {
        const x0 = centerX + (px * 3.5 - 2.5) / zoom;
        const y0 = centerY + (py * 2.0 - 1.0) / zoom;
        let x = 0, y = 0;
        let iteration = 0;
        while (x * x + y * y < this.escapeRadius && iteration < this.maxIterations) {
            const xTemp = x * x - y * y + x0;
            y = 2 * x * y + y0;
            x = xTemp;
            iteration++;
        }
        if (iteration === this.maxIterations) return { iter: iteration, smooth: iteration };
        const logZn = Math.log(x * x + y * y) / 2;
        const nu = Math.log(logZn / Math.log(2)) / Math.log(2);
        return { iter: iteration, smooth: iteration + 1 - nu };
    }

    julia(px, py, centerX, centerY, zoom) {
        let x = centerX + (px * 3.5 - 1.75) / zoom;
        let y = centerY + (py * 2.0 - 1.0) / zoom;
        let iteration = 0;
        while (x * x + y * y < this.escapeRadius && iteration < this.maxIterations) {
            const xTemp = x * x - y * y + this.juliaC.re;
            y = 2 * x * y + this.juliaC.im;
            x = xTemp;
            iteration++;
        }
        if (iteration === this.maxIterations) return { iter: iteration, smooth: iteration };
        const logZn = Math.log(x * x + y * y) / 2;
        const nu = Math.log(logZn / Math.log(2)) / Math.log(2);
        return { iter: iteration, smooth: iteration + 1 - nu };
    }

    burningShip(px, py, centerX, centerY, zoom) {
        const x0 = centerX + (px * 3.5 - 2.5) / zoom;
        const y0 = centerY + (py * 2.0 - 1.0) / zoom;
        let x = 0, y = 0;
        let iteration = 0;
        while (x * x + y * y < this.escapeRadius && iteration < this.maxIterations) {
            const xTemp = x * x - y * y + x0;
            y = Math.abs(2 * x * y) + y0;
            x = Math.abs(xTemp);
            iteration++;
        }
        if (iteration === this.maxIterations) return { iter: iteration, smooth: iteration };
        const logZn = Math.log(x * x + y * y) / 2;
        const nu = Math.log(logZn / Math.log(2)) / Math.log(2);
        return { iter: iteration, smooth: iteration + 1 - nu };
    }

    iterationToColor(result, palette) {
        if (result.iter === this.maxIterations) return { r: 0, g: 0, b: 0 };
        const t = result.smooth / this.maxIterations;
        const idx = t * (palette.length - 1);
        const i = Math.floor(idx);
        const f = idx - i;
        const c1 = palette[Math.min(i, palette.length - 1)];
        const c2 = palette[Math.min(i + 1, palette.length - 1)];
        return {
            r: Math.round(c1.r + f * (c2.r - c1.r)),
            g: Math.round(c1.g + f * (c2.g - c1.g)),
            b: Math.round(c1.b + f * (c2.b - c1.b))
        };
    }

    getDefaultPalette() {
        const colors = [];
        for (let i = 0; i < 256; i++) {
            const t = i / 255;
            colors.push({
                r: Math.round(9 * (1-t) * t * t * t * 255),
                g: Math.round(15 * (1-t) * (1-t) * t * t * 255),
                b: Math.round(8.5 * (1-t) * (1-t) * (1-t) * t * 255)
            });
        }
        return colors;
    }

    renderToCanvas(ctx, width, height, type, params) {
        params = params || {};
        const centerX = params.centerX || 0;
        const centerY = params.centerY || 0;
        const zoom = params.zoom || 1;
        const palette = params.palette || this.getDefaultPalette();
        const imageData = ctx.createImageData(width, height);
        for (let py = 0; py < height; py++) {
            for (let px = 0; px < width; px++) {
                const nx = px / width;
                const ny = py / height;
                let result;
                switch (type) {
                    case 'julia': result = this.julia(nx, ny, centerX, centerY, zoom); break;
                    case 'burning_ship': result = this.burningShip(nx, ny, centerX, centerY, zoom); break;
                    default: result = this.mandelbrot(nx, ny, centerX, centerY, zoom);
                }
                const color = this.iterationToColor(result, palette);
                const idx = (py * width + px) * 4;
                imageData.data[idx] = color.r;
                imageData.data[idx + 1] = color.g;
                imageData.data[idx + 2] = color.b;
                imageData.data[idx + 3] = 255;
            }
        }
        ctx.putImageData(imageData, 0, 0);
    }

    ifsIterate(presetName, iterations) {
        iterations = iterations || 50000;
        const transforms = this.ifsPresets[presetName] || this.ifsPresets.fern;
        const points = [];
        let x = 0, y = 0;
        for (let i = 0; i < iterations; i++) {
            let r = Math.random();
            let cumP = 0;
            let t = transforms[0];
            for (const tr of transforms) {
                cumP += tr.p;
                if (r <= cumP) { t = tr; break; }
            }
            const nx = t.a * x + t.b * y + t.e;
            const ny = t.c * x + t.d * y + t.f;
            x = nx;
            y = ny;
            if (i > 20) points.push({ x, y });
        }
        return points;
    }

    renderIFS(ctx, width, height, presetName, color) {
        const points = this.ifsIterate(presetName, 100000);
        if (points.length === 0) return;
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const p of points) {
            if (p.x < minX) minX = p.x;
            if (p.x > maxX) maxX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.y > maxY) maxY = p.y;
        }
        const rangeX = maxX - minX || 1;
        const rangeY = maxY - minY || 1;
        const margin = 0.05;
        ctx.fillStyle = color || 'rgba(100, 200, 150, 0.3)';
        for (const p of points) {
            const px = margin * width + ((p.x - minX) / rangeX) * width * (1 - 2 * margin);
            const py = height - (margin * height + ((p.y - minY) / rangeY) * height * (1 - 2 * margin));
            ctx.fillRect(px, py, 1, 1);
        }
    }

    renderTerritoryFractal(ctx, x, y, size, type, palette, quality) {
        quality = quality || 64;
        const saved = this.maxIterations;
        this.maxIterations = quality;
        const imgData = ctx.createImageData(size, size);
        const defPalette = palette || this.getDefaultPalette();
        for (let py = 0; py < size; py++) {
            for (let px = 0; px < size; px++) {
                const nx = px / size;
                const ny = py / size;
                let result;
                if (type === 'julia') result = this.julia(nx, ny, 0, 0, 1);
                else result = this.mandelbrot(nx, ny, -0.5, 0, 1);
                const color = this.iterationToColor(result, defPalette);
                const idx = (py * size + px) * 4;
                imgData.data[idx] = color.r;
                imgData.data[idx + 1] = color.g;
                imgData.data[idx + 2] = color.b;
                imgData.data[idx + 3] = 180;
            }
        }
        ctx.putImageData(imgData, x, y);
        this.maxIterations = saved;
    }
}

window.CT.FractalRenderer = FractalRenderer;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: LSystemEngine (~300 lines)
# Grammar expansion, turtle renderer, presets
# ═══════════════════════════════════════════════════════════════════════════════

LSYSTEM_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class LSystemEngine {
    constructor() {
        this.presets = {
            kochCurve: {
                axiom: 'F',
                rules: { F: 'F+F-F-F+F' },
                angle: 90,
                iterations: 4
            },
            sierpinskiTriangle: {
                axiom: 'F-G-G',
                rules: { F: 'F-G+F+G-F', G: 'GG' },
                angle: 120,
                iterations: 5
            },
            dragonCurve: {
                axiom: 'FX',
                rules: { X: 'X+YF+', Y: '-FX-Y' },
                angle: 90,
                iterations: 10
            },
            plant: {
                axiom: 'X',
                rules: { X: 'F+[[X]-X]-F[-FX]+X', F: 'FF' },
                angle: 25,
                iterations: 5
            },
            hilbert: {
                axiom: 'A',
                rules: { A: '-BF+AFA+FB-', B: '+AF-BFB-FA+' },
                angle: 90,
                iterations: 5
            },
            penrose: {
                axiom: '[7]++[7]++[7]++[7]++[7]',
                rules: {
                    '6': '81++91----71[-81----61]++',
                    '7': '+81--91[---61--71]+',
                    '8': '-61++71[+++81++91]-',
                    '9': '--81++++61[+91++++71]--71',
                    '1': ''
                },
                angle: 36,
                iterations: 4
            },
            bush: {
                axiom: 'F',
                rules: { F: 'FF+[+F-F-F]-[-F+F+F]' },
                angle: 22.5,
                iterations: 4
            },
            snowflake: {
                axiom: 'F--F--F',
                rules: { F: 'F+F--F+F' },
                angle: 60,
                iterations: 4
            }
        };
    }

    expand(axiom, rules, iterations) {
        let current = axiom;
        for (let i = 0; i < iterations; i++) {
            let next = '';
            for (const char of current) {
                next += rules[char] !== undefined ? rules[char] : char;
            }
            current = next;
        }
        return current;
    }

    turtleInterpret(commands, angle, stepSize) {
        const segments = [];
        let x = 0, y = 0;
        let heading = -90;
        const stack = [];
        const rad = Math.PI / 180;
        for (const cmd of commands) {
            switch (cmd) {
                case 'F':
                case 'G':
                case '1':
                    const nx = x + stepSize * Math.cos(heading * rad);
                    const ny = y + stepSize * Math.sin(heading * rad);
                    segments.push({ x1: x, y1: y, x2: nx, y2: ny });
                    x = nx;
                    y = ny;
                    break;
                case 'f':
                    x += stepSize * Math.cos(heading * rad);
                    y += stepSize * Math.sin(heading * rad);
                    break;
                case '+':
                    heading += angle;
                    break;
                case '-':
                    heading -= angle;
                    break;
                case '[':
                    stack.push({ x, y, heading });
                    break;
                case ']':
                    if (stack.length > 0) {
                        const state = stack.pop();
                        x = state.x;
                        y = state.y;
                        heading = state.heading;
                    }
                    break;
            }
        }
        return segments;
    }

    getBounds(segments) {
        let minX = Infinity, maxX = -Infinity;
        let minY = Infinity, maxY = -Infinity;
        for (const seg of segments) {
            minX = Math.min(minX, seg.x1, seg.x2);
            maxX = Math.max(maxX, seg.x1, seg.x2);
            minY = Math.min(minY, seg.y1, seg.y2);
            maxY = Math.max(maxY, seg.y1, seg.y2);
        }
        return { minX, maxX, minY, maxY };
    }

    renderToCanvas(ctx, width, height, presetName, options) {
        options = options || {};
        const preset = this.presets[presetName] || this.presets.plant;
        const iterations = options.iterations || preset.iterations;
        const color = options.color || '#22c55e';
        const lineWidth = options.lineWidth || 1;
        const commands = this.expand(preset.axiom, preset.rules, iterations);
        const segments = this.turtleInterpret(commands, preset.angle, 5);
        if (segments.length === 0) return;
        const bounds = this.getBounds(segments);
        const rangeX = bounds.maxX - bounds.minX || 1;
        const rangeY = bounds.maxY - bounds.minY || 1;
        const scale = Math.min(width * 0.9 / rangeX, height * 0.9 / rangeY);
        const offsetX = (width - rangeX * scale) / 2 - bounds.minX * scale;
        const offsetY = (height - rangeY * scale) / 2 - bounds.minY * scale;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const seg of segments) {
            ctx.moveTo(seg.x1 * scale + offsetX, seg.y1 * scale + offsetY);
            ctx.lineTo(seg.x2 * scale + offsetX, seg.y2 * scale + offsetY);
        }
        ctx.stroke();
        ctx.restore();
    }

    renderTerritory(ctx, x, y, size, presetName, color) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x, y, size, size);
        ctx.clip();
        const preset = this.presets[presetName] || this.presets.plant;
        const commands = this.expand(preset.axiom, preset.rules, Math.min(preset.iterations, 4));
        const segments = this.turtleInterpret(commands, preset.angle, 5);
        if (segments.length === 0) { ctx.restore(); return; }
        const bounds = this.getBounds(segments);
        const rangeX = bounds.maxX - bounds.minX || 1;
        const rangeY = bounds.maxY - bounds.minY || 1;
        const scale = Math.min(size * 0.8 / rangeX, size * 0.8 / rangeY);
        const offsetX = x + (size - rangeX * scale) / 2 - bounds.minX * scale;
        const offsetY = y + (size - rangeY * scale) / 2 - bounds.minY * scale;
        ctx.strokeStyle = color || '#22c55e';
        ctx.lineWidth = 0.5;
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        for (const seg of segments) {
            ctx.moveTo(seg.x1 * scale + offsetX, seg.y1 * scale + offsetY);
            ctx.lineTo(seg.x2 * scale + offsetX, seg.y2 * scale + offsetY);
        }
        ctx.stroke();
        ctx.globalAlpha = 1.0;
        ctx.restore();
    }

    generateCustom(axiom, rules, angle, iterations) {
        const commands = this.expand(axiom, rules, iterations);
        return this.turtleInterpret(commands, angle, 5);
    }

    getPresetNames() {
        return Object.keys(this.presets);
    }
}

window.CT.LSystemEngine = LSystemEngine;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: ParticleSystem (~350 lines)
# Emitter, forces, blending, presets
# ═══════════════════════════════════════════════════════════════════════════════

PARTICLE_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class Particle {
    constructor(x, y, vx, vy, life, color, size) {
        this.x = x;
        this.y = y;
        this.vx = vx;
        this.vy = vy;
        this.life = life;
        this.maxLife = life;
        this.color = color;
        this.size = size;
        this.opacity = 1.0;
        this.rotation = 0;
        this.rotationSpeed = (Math.random() - 0.5) * 0.1;
        this.decay = 0;
        this.active = true;
    }

    update(dt, forces) {
        if (!this.active) return;
        for (const force of forces) {
            this.vx += force.x * dt;
            this.vy += force.y * dt;
        }
        this.x += this.vx * dt;
        this.y += this.vy * dt;
        this.life -= dt;
        this.rotation += this.rotationSpeed;
        const lifeRatio = Math.max(0, this.life / this.maxLife);
        this.opacity = lifeRatio;
        this.size = Math.max(0.1, this.size * (0.99 + 0.01 * lifeRatio));
        if (this.life <= 0) this.active = false;
    }

    draw(ctx) {
        if (!this.active || this.opacity <= 0) return;
        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.rotate(this.rotation);
        ctx.globalAlpha = this.opacity;
        ctx.fillStyle = this.color;
        ctx.beginPath();
        ctx.arc(0, 0, this.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }
}

class Emitter {
    constructor(config) {
        this.x = config.x || 0;
        this.y = config.y || 0;
        this.rate = config.rate || 10;
        this.spread = config.spread || Math.PI * 2;
        this.direction = config.direction || -Math.PI / 2;
        this.speed = config.speed || { min: 50, max: 150 };
        this.life = config.life || { min: 1, max: 3 };
        this.size = config.size || { min: 2, max: 8 };
        this.colors = config.colors || ['#ef4444', '#f59e0b', '#eab308'];
        this.active = true;
        this.accumulator = 0;
        this.burstMode = false;
    }

    emit(dt) {
        if (!this.active) return [];
        const particles = [];
        this.accumulator += this.rate * dt;
        while (this.accumulator >= 1) {
            this.accumulator -= 1;
            const angle = this.direction + (Math.random() - 0.5) * this.spread;
            const speed = this.speed.min + Math.random() * (this.speed.max - this.speed.min);
            const life = this.life.min + Math.random() * (this.life.max - this.life.min);
            const size = this.size.min + Math.random() * (this.size.max - this.size.min);
            const color = this.colors[Math.floor(Math.random() * this.colors.length)];
            const vx = Math.cos(angle) * speed;
            const vy = Math.sin(angle) * speed;
            particles.push(new Particle(this.x, this.y, vx, vy, life, color, size));
        }
        return particles;
    }

    burst(count) {
        const particles = [];
        for (let i = 0; i < count; i++) {
            const angle = this.direction + (Math.random() - 0.5) * this.spread;
            const speed = this.speed.min + Math.random() * (this.speed.max - this.speed.min);
            const life = this.life.min + Math.random() * (this.life.max - this.life.min);
            const size = this.size.min + Math.random() * (this.size.max - this.size.min);
            const color = this.colors[Math.floor(Math.random() * this.colors.length)];
            const vx = Math.cos(angle) * speed;
            const vy = Math.sin(angle) * speed;
            particles.push(new Particle(this.x, this.y, vx, vy, life, color, size));
        }
        return particles;
    }
}

class ParticleSystem {
    constructor(maxParticles) {
        this.maxParticles = maxParticles || 2000;
        this.particles = [];
        this.emitters = [];
        this.forces = [{ x: 0, y: 98 }];
        this.blendMode = 'lighter';
        this.presets = {
            fire: {
                rate: 30, spread: 0.6, direction: -Math.PI / 2,
                speed: { min: 30, max: 80 }, life: { min: 0.5, max: 1.5 },
                size: { min: 3, max: 10 },
                colors: ['#ef4444', '#f59e0b', '#eab308', '#fbbf24', '#fde68a']
            },
            rain: {
                rate: 50, spread: 0.1, direction: Math.PI / 2 + 0.2,
                speed: { min: 200, max: 400 }, life: { min: 0.5, max: 1.0 },
                size: { min: 1, max: 2 },
                colors: ['#60a5fa', '#93c5fd', '#3b82f6', '#2563eb']
            },
            sparkle: {
                rate: 15, spread: Math.PI * 2, direction: 0,
                speed: { min: 20, max: 100 }, life: { min: 0.3, max: 1.0 },
                size: { min: 1, max: 4 },
                colors: ['#fbbf24', '#fde68a', '#ffffff', '#f59e0b']
            },
            snow: {
                rate: 20, spread: 0.5, direction: Math.PI / 2,
                speed: { min: 10, max: 40 }, life: { min: 3, max: 6 },
                size: { min: 2, max: 5 },
                colors: ['#ffffff', '#e2e8f0', '#f1f5f9', '#cbd5e1']
            },
            explosion: {
                rate: 0, spread: Math.PI * 2, direction: 0,
                speed: { min: 100, max: 300 }, life: { min: 0.5, max: 1.5 },
                size: { min: 2, max: 8 },
                colors: ['#ef4444', '#f59e0b', '#ffffff', '#6366f1']
            },
            territory_claim: {
                rate: 0, spread: Math.PI * 2, direction: 0,
                speed: { min: 20, max: 80 }, life: { min: 1, max: 2 },
                size: { min: 3, max: 6 },
                colors: ['#6366f1', '#818cf8', '#a5b4fc', '#c7d2fe']
            },
            combat: {
                rate: 40, spread: 0.8, direction: 0,
                speed: { min: 60, max: 150 }, life: { min: 0.3, max: 0.8 },
                size: { min: 2, max: 6 },
                colors: ['#ef4444', '#dc2626', '#fbbf24', '#ffffff']
            }
        };
    }

    addEmitter(presetName, x, y) {
        const preset = this.presets[presetName] || this.presets.sparkle;
        const config = Object.assign({}, preset, { x, y });
        const emitter = new Emitter(config);
        this.emitters.push(emitter);
        return emitter;
    }

    removeEmitter(emitter) {
        const idx = this.emitters.indexOf(emitter);
        if (idx >= 0) this.emitters.splice(idx, 1);
    }

    setGravity(gx, gy) {
        this.forces = [{ x: gx, y: gy }];
    }

    addForce(fx, fy) {
        this.forces.push({ x: fx, y: fy });
    }

    burst(presetName, x, y, count) {
        count = count || 50;
        const preset = this.presets[presetName] || this.presets.explosion;
        const config = Object.assign({}, preset, { x, y });
        const emitter = new Emitter(config);
        const newParticles = emitter.burst(count);
        for (const p of newParticles) {
            if (this.particles.length < this.maxParticles) {
                this.particles.push(p);
            }
        }
    }

    update(dt) {
        for (const emitter of this.emitters) {
            const newParticles = emitter.emit(dt);
            for (const p of newParticles) {
                if (this.particles.length < this.maxParticles) {
                    this.particles.push(p);
                }
            }
        }
        for (const particle of this.particles) {
            particle.update(dt, this.forces);
        }
        this.particles = this.particles.filter(p => p.active);
    }

    draw(ctx) {
        ctx.save();
        if (this.blendMode === 'lighter') {
            ctx.globalCompositeOperation = 'lighter';
        } else if (this.blendMode === 'screen') {
            ctx.globalCompositeOperation = 'screen';
        }
        for (const particle of this.particles) {
            particle.draw(ctx);
        }
        ctx.restore();
    }

    clear() {
        this.particles = [];
        this.emitters = [];
    }

    getParticleCount() {
        return this.particles.length;
    }
}

window.CT.ParticleSystem = ParticleSystem;
window.CT.Particle = Particle;
window.CT.Emitter = Emitter;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: CellularAutomata (~300 lines)
# Configurable rules, Life/Brian's Brain/Seeds
# ═══════════════════════════════════════════════════════════════════════════════

CELLULAR_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class CellularAutomata {
    constructor(width, height) {
        this.width = width || 100;
        this.height = height || 100;
        this.grid = new Uint8Array(this.width * this.height);
        this.nextGrid = new Uint8Array(this.width * this.height);
        this.ruleSet = 'life';
        this.generation = 0;
        this.statesCount = 2;
        this.wrapEdges = true;
        this.rules = {
            life: { birth: [3], survive: [2, 3], states: 2 },
            highlife: { birth: [3, 6], survive: [2, 3], states: 2 },
            daynight: { birth: [3, 6, 7, 8], survive: [3, 4, 6, 7, 8], states: 2 },
            seeds: { birth: [2], survive: [], states: 2 },
            briansBrain: { birth: [2], survive: [], states: 3 },
            wireworld: { states: 4 },
            diamoeba: { birth: [3, 5, 6, 7, 8], survive: [5, 6, 7, 8], states: 2 },
            anneal: { birth: [4, 6, 7, 8], survive: [3, 5, 6, 7, 8], states: 2 },
            morley: { birth: [3, 6, 8], survive: [2, 4, 5], states: 2 }
        };
    }

    setRule(ruleName) {
        const rule = this.rules[ruleName];
        if (rule) {
            this.ruleSet = ruleName;
            this.statesCount = rule.states;
        }
    }

    getCell(x, y) {
        if (this.wrapEdges) {
            x = ((x % this.width) + this.width) % this.width;
            y = ((y % this.height) + this.height) % this.height;
        } else if (x < 0 || x >= this.width || y < 0 || y >= this.height) {
            return 0;
        }
        return this.grid[y * this.width + x];
    }

    setCell(x, y, state) {
        if (x >= 0 && x < this.width && y >= 0 && y < this.height) {
            this.grid[y * this.width + x] = state;
        }
    }

    countNeighbors(x, y, state) {
        state = state || 1;
        let count = 0;
        for (let dy = -1; dy <= 1; dy++) {
            for (let dx = -1; dx <= 1; dx++) {
                if (dx === 0 && dy === 0) continue;
                if (this.getCell(x + dx, y + dy) === state) count++;
            }
        }
        return count;
    }

    countNeighborsVonNeumann(x, y, state) {
        state = state || 1;
        let count = 0;
        if (this.getCell(x - 1, y) === state) count++;
        if (this.getCell(x + 1, y) === state) count++;
        if (this.getCell(x, y - 1) === state) count++;
        if (this.getCell(x, y + 1) === state) count++;
        return count;
    }

    step() {
        const rule = this.rules[this.ruleSet];
        if (!rule) return;
        this.nextGrid.fill(0);
        if (this.ruleSet === 'briansBrain') {
            this._stepBriansBrain();
        } else if (this.ruleSet === 'wireworld') {
            this._stepWireworld();
        } else {
            this._stepGenericLife(rule);
        }
        const tmp = this.grid;
        this.grid = this.nextGrid;
        this.nextGrid = tmp;
        this.generation++;
    }

    _stepGenericLife(rule) {
        for (let y = 0; y < this.height; y++) {
            for (let x = 0; x < this.width; x++) {
                const alive = this.getCell(x, y) === 1;
                const neighbors = this.countNeighbors(x, y, 1);
                if (alive) {
                    this.nextGrid[y * this.width + x] = rule.survive.includes(neighbors) ? 1 : 0;
                } else {
                    this.nextGrid[y * this.width + x] = rule.birth.includes(neighbors) ? 1 : 0;
                }
            }
        }
    }

    _stepBriansBrain() {
        for (let y = 0; y < this.height; y++) {
            for (let x = 0; x < this.width; x++) {
                const state = this.getCell(x, y);
                if (state === 0) {
                    const neighbors = this.countNeighbors(x, y, 1);
                    this.nextGrid[y * this.width + x] = neighbors === 2 ? 1 : 0;
                } else if (state === 1) {
                    this.nextGrid[y * this.width + x] = 2;
                } else {
                    this.nextGrid[y * this.width + x] = 0;
                }
            }
        }
    }

    _stepWireworld() {
        for (let y = 0; y < this.height; y++) {
            for (let x = 0; x < this.width; x++) {
                const state = this.getCell(x, y);
                if (state === 0) {
                    this.nextGrid[y * this.width + x] = 0;
                } else if (state === 1) {
                    this.nextGrid[y * this.width + x] = 2;
                } else if (state === 2) {
                    this.nextGrid[y * this.width + x] = 3;
                } else {
                    const heads = this.countNeighbors(x, y, 1);
                    this.nextGrid[y * this.width + x] = (heads === 1 || heads === 2) ? 1 : 3;
                }
            }
        }
    }

    randomize(density) {
        density = density || 0.3;
        for (let i = 0; i < this.grid.length; i++) {
            this.grid[i] = Math.random() < density ? 1 : 0;
        }
        this.generation = 0;
    }

    clear() {
        this.grid.fill(0);
        this.generation = 0;
    }

    placePattern(pattern, offsetX, offsetY) {
        for (let y = 0; y < pattern.length; y++) {
            for (let x = 0; x < pattern[y].length; x++) {
                if (pattern[y][x]) {
                    this.setCell(offsetX + x, offsetY + y, pattern[y][x]);
                }
            }
        }
    }

    getPatterns() {
        return {
            glider: [[0,1,0],[0,0,1],[1,1,1]],
            blinker: [[0,1,0],[0,1,0],[0,1,0]],
            toad: [[0,1,1,1],[1,1,1,0]],
            beacon: [[1,1,0,0],[1,0,0,0],[0,0,0,1],[0,0,1,1]],
            pulsar: [
                [0,0,1,1,1,0,0,0,1,1,1,0,0],
                [0,0,0,0,0,0,0,0,0,0,0,0,0],
                [1,0,0,0,0,1,0,1,0,0,0,0,1],
                [1,0,0,0,0,1,0,1,0,0,0,0,1],
                [1,0,0,0,0,1,0,1,0,0,0,0,1],
                [0,0,1,1,1,0,0,0,1,1,1,0,0]
            ],
            lwss: [[0,1,0,0,1],[1,0,0,0,0],[1,0,0,0,1],[1,1,1,1,0]],
            rpentomino: [[0,1,1],[1,1,0],[0,1,0]],
            acorn: [[0,1,0,0,0,0,0],[0,0,0,1,0,0,0],[1,1,0,0,1,1,1]]
        };
    }

    renderToCanvas(ctx, width, height, colorMap) {
        const cellW = width / this.width;
        const cellH = height / this.height;
        const defaultColors = {
            0: 'rgba(15, 23, 42, 0)',
            1: '#6366f1',
            2: '#f59e0b',
            3: '#334155'
        };
        const colors = colorMap || defaultColors;
        for (let y = 0; y < this.height; y++) {
            for (let x = 0; x < this.width; x++) {
                const state = this.grid[y * this.width + x];
                if (state > 0) {
                    ctx.fillStyle = colors[state] || colors[1];
                    ctx.fillRect(x * cellW, y * cellH, cellW + 0.5, cellH + 0.5);
                }
            }
        }
    }

    getPopulation() {
        let count = 0;
        for (let i = 0; i < this.grid.length; i++) {
            if (this.grid[i] === 1) count++;
        }
        return count;
    }

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
        this.nextGrid = new Uint8Array(newWidth * newHeight);
    }
}

window.CT.CellularAutomata = CellularAutomata;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: CompositionAnalyzer (~300 lines)
# Rule of thirds, golden ratio, balance, harmony score
# ═══════════════════════════════════════════════════════════════════════════════

COMPOSITION_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class CompositionAnalyzer {
    constructor() {
        this.goldenRatio = 1.6180339887;
        this.weights = {
            balance: 0.25,
            ruleOfThirds: 0.2,
            goldenRatio: 0.15,
            contrast: 0.15,
            rhythm: 0.1,
            harmony: 0.15
        };
    }

    analyzeBalance(elements, width, height) {
        if (!elements || elements.length === 0) return 0;
        const cx = width / 2;
        const cy = height / 2;
        let leftWeight = 0, rightWeight = 0;
        let topWeight = 0, bottomWeight = 0;
        for (const el of elements) {
            const elCx = el.x + el.width / 2;
            const elCy = el.y + el.height / 2;
            const weight = el.weight || (el.width * el.height);
            if (elCx < cx) leftWeight += weight;
            else rightWeight += weight;
            if (elCy < cy) topWeight += weight;
            else bottomWeight += weight;
        }
        const totalH = leftWeight + rightWeight || 1;
        const totalV = topWeight + bottomWeight || 1;
        const hBalance = 1.0 - Math.abs(leftWeight - rightWeight) / totalH;
        const vBalance = 1.0 - Math.abs(topWeight - bottomWeight) / totalV;
        return (hBalance * 0.5 + vBalance * 0.5);
    }

    analyzeRuleOfThirds(elements, width, height) {
        if (!elements || elements.length === 0) return 0;
        const thirdX1 = width / 3;
        const thirdX2 = 2 * width / 3;
        const thirdY1 = height / 3;
        const thirdY2 = 2 * height / 3;
        const intersections = [
            { x: thirdX1, y: thirdY1 },
            { x: thirdX2, y: thirdY1 },
            { x: thirdX1, y: thirdY2 },
            { x: thirdX2, y: thirdY2 }
        ];
        let score = 0;
        const maxDist = Math.sqrt(width * width + height * height) / 6;
        for (const el of elements) {
            const elCx = el.x + (el.width || 0) / 2;
            const elCy = el.y + (el.height || 0) / 2;
            let minDist = Infinity;
            for (const pt of intersections) {
                const d = Math.sqrt((elCx - pt.x) ** 2 + (elCy - pt.y) ** 2);
                if (d < minDist) minDist = d;
            }
            score += Math.max(0, 1.0 - minDist / maxDist);
        }
        return Math.min(1.0, score / Math.max(1, elements.length));
    }

    analyzeGoldenRatio(elements, width, height) {
        if (!elements || elements.length === 0) return 0;
        const goldenX1 = width / this.goldenRatio;
        const goldenX2 = width - goldenX1;
        const goldenY1 = height / this.goldenRatio;
        const goldenY2 = height - goldenY1;
        const goldenPoints = [
            { x: goldenX1, y: goldenY1 },
            { x: goldenX2, y: goldenY1 },
            { x: goldenX1, y: goldenY2 },
            { x: goldenX2, y: goldenY2 }
        ];
        let score = 0;
        const maxDist = Math.sqrt(width * width + height * height) / 5;
        for (const el of elements) {
            const elCx = el.x + (el.width || 0) / 2;
            const elCy = el.y + (el.height || 0) / 2;
            let minDist = Infinity;
            for (const pt of goldenPoints) {
                const d = Math.sqrt((elCx - pt.x) ** 2 + (elCy - pt.y) ** 2);
                if (d < minDist) minDist = d;
            }
            score += Math.max(0, 1.0 - minDist / maxDist);
        }
        return Math.min(1.0, score / Math.max(1, elements.length));
    }

    analyzeContrast(colors) {
        if (!colors || colors.length < 2) return 0;
        const ct = new (window.CT.ColorTheory)();
        let totalContrast = 0;
        let pairs = 0;
        for (let i = 0; i < colors.length; i++) {
            for (let j = i + 1; j < colors.length; j++) {
                const c1 = ct.hexToRgb(colors[i]);
                const c2 = ct.hexToRgb(colors[j]);
                const ratio = ct.contrastRatio(c1, c2);
                totalContrast += Math.min(1.0, ratio / 7.0);
                pairs++;
            }
        }
        return pairs > 0 ? totalContrast / pairs : 0;
    }

    analyzeRhythm(elements) {
        if (!elements || elements.length < 3) return 0;
        const sorted = elements.slice().sort((a, b) => a.x - b.x);
        const gaps = [];
        for (let i = 1; i < sorted.length; i++) {
            gaps.push(sorted[i].x - sorted[i-1].x);
        }
        if (gaps.length === 0) return 0;
        const avgGap = gaps.reduce((s, g) => s + g, 0) / gaps.length;
        let variance = 0;
        for (const g of gaps) {
            variance += (g - avgGap) ** 2;
        }
        variance /= gaps.length;
        const stdDev = Math.sqrt(variance);
        const coefficient = avgGap > 0 ? stdDev / avgGap : 1;
        return Math.max(0, 1.0 - coefficient);
    }

    analyzeColorHarmony(colors) {
        if (!colors || colors.length < 2) return 0;
        const ct = new (window.CT.ColorTheory)();
        const score = ct.scorePalette(colors);
        return score.overall;
    }

    analyzeComposition(elements, colors, width, height) {
        const balance = this.analyzeBalance(elements, width, height);
        const thirds = this.analyzeRuleOfThirds(elements, width, height);
        const golden = this.analyzeGoldenRatio(elements, width, height);
        const contrast = this.analyzeContrast(colors);
        const rhythm = this.analyzeRhythm(elements);
        const harmony = this.analyzeColorHarmony(colors);
        const overall = (
            balance * this.weights.balance +
            thirds * this.weights.ruleOfThirds +
            golden * this.weights.goldenRatio +
            contrast * this.weights.contrast +
            rhythm * this.weights.rhythm +
            harmony * this.weights.harmony
        );
        return {
            balance: Math.round(balance * 100) / 100,
            ruleOfThirds: Math.round(thirds * 100) / 100,
            goldenRatio: Math.round(golden * 100) / 100,
            contrast: Math.round(contrast * 100) / 100,
            rhythm: Math.round(rhythm * 100) / 100,
            harmony: Math.round(harmony * 100) / 100,
            overall: Math.round(overall * 100) / 100
        };
    }

    getTerritoryHealth(composition) {
        const score = composition.overall;
        if (score >= 0.8) return { level: 'thriving', growth: 1.5, decay: 0 };
        if (score >= 0.6) return { level: 'healthy', growth: 1.0, decay: 0 };
        if (score >= 0.4) return { level: 'stable', growth: 0.5, decay: 0.1 };
        if (score >= 0.2) return { level: 'declining', growth: 0, decay: 0.5 };
        return { level: 'dying', growth: 0, decay: 1.0 };
    }

    suggestImprovements(composition) {
        const suggestions = [];
        if (composition.balance < 0.5) suggestions.push('Redistribute visual weight for better balance');
        if (composition.ruleOfThirds < 0.4) suggestions.push('Position key elements at third intersections');
        if (composition.goldenRatio < 0.3) suggestions.push('Align important elements to golden ratio points');
        if (composition.contrast < 0.4) suggestions.push('Increase color contrast between elements');
        if (composition.rhythm < 0.4) suggestions.push('Create more regular spacing between elements');
        if (composition.harmony < 0.4) suggestions.push('Use more harmonious color relationships');
        return suggestions;
    }

    analyzeImageData(imageData, width, height, gridSize) {
        gridSize = gridSize || 8;
        const cellW = Math.floor(width / gridSize);
        const cellH = Math.floor(height / gridSize);
        const elements = [];
        const colors = new Set();
        for (let gy = 0; gy < gridSize; gy++) {
            for (let gx = 0; gx < gridSize; gx++) {
                let totalR = 0, totalG = 0, totalB = 0, count = 0;
                let brightness = 0;
                for (let y = gy * cellH; y < (gy + 1) * cellH && y < height; y++) {
                    for (let x = gx * cellW; x < (gx + 1) * cellW && x < width; x++) {
                        const idx = (y * width + x) * 4;
                        totalR += imageData.data[idx];
                        totalG += imageData.data[idx + 1];
                        totalB += imageData.data[idx + 2];
                        brightness += (imageData.data[idx] + imageData.data[idx+1] + imageData.data[idx+2]) / 3;
                        count++;
                    }
                }
                if (count > 0) {
                    const avgR = Math.round(totalR / count);
                    const avgG = Math.round(totalG / count);
                    const avgB = Math.round(totalB / count);
                    const avgBright = brightness / count;
                    elements.push({
                        x: gx * cellW, y: gy * cellH,
                        width: cellW, height: cellH,
                        weight: avgBright
                    });
                    const hex = '#' + [avgR, avgG, avgB].map(c => c.toString(16).padStart(2, '0')).join('');
                    colors.add(hex);
                }
            }
        }
        return this.analyzeComposition(elements, Array.from(colors), width, height);
    }
}

window.CT.CompositionAnalyzer = CompositionAnalyzer;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: TerritorySystem (~500 lines)
# Hex grid (axial coords), ownership, borders, expansion
# ═══════════════════════════════════════════════════════════════════════════════

TERRITORY_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class HexCell {
    constructor(q, r) {
        this.q = q;
        this.r = r;
        this.s = -q - r;
        this.owner = null;
        this.color = null;
        this.terrain = 'plains';
        this.health = 100;
        this.artStyle = null;
        this.artQuality = 0;
        this.influence = {};
        this.resources = { pigment: 0, inspiration: 0 };
        this.structures = [];
        this.noiseValue = 0;
        this.elevation = 0;
        this.temperature = 0.5;
    }

    get key() { return this.q + ',' + this.r; }

    cubeDistance(other) {
        return Math.max(
            Math.abs(this.q - other.q),
            Math.abs(this.r - other.r),
            Math.abs(this.s - other.s)
        );
    }
}

class TerritorySystem {
    constructor(radius) {
        this.radius = radius || 8;
        this.cells = new Map();
        this.hexSize = 30;
        this.players = [];
        this.borderCache = new Map();
        this.influenceMap = new Map();
        this.directionVectors = [
            {q: 1, r: 0}, {q: 1, r: -1}, {q: 0, r: -1},
            {q: -1, r: 0}, {q: -1, r: 1}, {q: 0, r: 1}
        ];
        this._generateGrid();
    }

    _generateGrid() {
        for (let q = -this.radius; q <= this.radius; q++) {
            const r1 = Math.max(-this.radius, -q - this.radius);
            const r2 = Math.min(this.radius, -q + this.radius);
            for (let r = r1; r <= r2; r++) {
                const cell = new HexCell(q, r);
                this.cells.set(cell.key, cell);
            }
        }
    }

    getCell(q, r) {
        return this.cells.get(q + ',' + r) || null;
    }

    getNeighbors(q, r) {
        const neighbors = [];
        for (const dir of this.directionVectors) {
            const cell = this.getCell(q + dir.q, r + dir.r);
            if (cell) neighbors.push(cell);
        }
        return neighbors;
    }

    hexToPixel(q, r) {
        const x = this.hexSize * (Math.sqrt(3) * q + Math.sqrt(3) / 2 * r);
        const y = this.hexSize * (3.0 / 2 * r);
        return { x, y };
    }

    pixelToHex(x, y) {
        const q = (Math.sqrt(3) / 3 * x - 1.0 / 3 * y) / this.hexSize;
        const r = (2.0 / 3 * y) / this.hexSize;
        return this._hexRound(q, r);
    }

    _hexRound(q, r) {
        const s = -q - r;
        let rq = Math.round(q);
        let rr = Math.round(r);
        let rs = Math.round(s);
        const dq = Math.abs(rq - q);
        const dr = Math.abs(rr - r);
        const ds = Math.abs(rs - s);
        if (dq > dr && dq > ds) rq = -rr - rs;
        else if (dr > ds) rr = -rq - rs;
        return { q: rq, r: rr };
    }

    hexLine(q1, r1, q2, r2) {
        const N = Math.max(
            Math.abs(q2 - q1),
            Math.abs(r2 - r1),
            Math.abs((-q2 - r2) - (-q1 - r1))
        );
        const results = [];
        for (let i = 0; i <= N; i++) {
            const t = N === 0 ? 0 : i / N;
            const q = q1 + (q2 - q1) * t;
            const r = r1 + (r2 - r1) * t;
            const rounded = this._hexRound(q, r);
            results.push(rounded);
        }
        return results;
    }

    hexRing(centerQ, centerR, ringRadius) {
        const results = [];
        if (ringRadius === 0) {
            results.push({ q: centerQ, r: centerR });
            return results;
        }
        let q = centerQ + this.directionVectors[4].q * ringRadius;
        let r = centerR + this.directionVectors[4].r * ringRadius;
        for (let i = 0; i < 6; i++) {
            for (let j = 0; j < ringRadius; j++) {
                results.push({ q, r });
                q += this.directionVectors[i].q;
                r += this.directionVectors[i].r;
            }
        }
        return results;
    }

    hexSpiral(centerQ, centerR, spiralRadius) {
        const results = [{ q: centerQ, r: centerR }];
        for (let k = 1; k <= spiralRadius; k++) {
            const ring = this.hexRing(centerQ, centerR, k);
            results.push(...ring);
        }
        return results;
    }

    claimTerritory(q, r, playerId, color) {
        const cell = this.getCell(q, r);
        if (!cell) return false;
        if (cell.owner !== null) return false;
        const neighbors = this.getNeighbors(q, r);
        const hasAdjacent = neighbors.some(n => n.owner === playerId);
        const playerCells = this.getPlayerCells(playerId);
        if (playerCells.length > 0 && !hasAdjacent) return false;
        cell.owner = playerId;
        cell.color = color;
        cell.health = 100;
        this._invalidateBorderCache();
        this._updateInfluence(q, r, playerId);
        return true;
    }

    getPlayerCells(playerId) {
        const result = [];
        for (const cell of this.cells.values()) {
            if (cell.owner === playerId) result.push(cell);
        }
        return result;
    }

    getPlayerTerritory(playerId) {
        const cells = this.getPlayerCells(playerId);
        return {
            count: cells.length,
            totalHealth: cells.reduce((s, c) => s + c.health, 0),
            avgHealth: cells.length > 0 ? cells.reduce((s, c) => s + c.health, 0) / cells.length : 0,
            colors: [...new Set(cells.map(c => c.color).filter(Boolean))],
            artStyles: [...new Set(cells.map(c => c.artStyle).filter(Boolean))]
        };
    }

    getBorders(playerId) {
        const cacheKey = 'borders_' + playerId;
        if (this.borderCache.has(cacheKey)) return this.borderCache.get(cacheKey);
        const borders = [];
        const playerCells = this.getPlayerCells(playerId);
        for (const cell of playerCells) {
            const neighbors = this.getNeighbors(cell.q, cell.r);
            for (let i = 0; i < neighbors.length; i++) {
                if (neighbors[i].owner !== playerId) {
                    borders.push({
                        cell: cell,
                        neighbor: neighbors[i],
                        direction: i,
                        isFriendly: neighbors[i].owner === null,
                        isHostile: neighbors[i].owner !== null && neighbors[i].owner !== playerId
                    });
                }
            }
        }
        this.borderCache.set(cacheKey, borders);
        return borders;
    }

    _invalidateBorderCache() {
        this.borderCache.clear();
    }

    _updateInfluence(q, r, playerId) {
        const spiral = this.hexSpiral(q, r, 3);
        for (const pos of spiral) {
            const cell = this.getCell(pos.q, pos.r);
            if (cell) {
                const dist = new HexCell(q, r).cubeDistance(new HexCell(pos.q, pos.r));
                const influence = Math.max(0, 1.0 - dist / 4);
                if (!cell.influence[playerId] || cell.influence[playerId] < influence) {
                    cell.influence[playerId] = influence;
                }
            }
        }
    }

    expandTerritory(playerId, color) {
        const borders = this.getBorders(playerId);
        const friendlyBorders = borders.filter(b => b.isFriendly);
        if (friendlyBorders.length === 0) return [];
        const expanded = [];
        for (const border of friendlyBorders) {
            const cell = border.neighbor;
            const influence = cell.influence[playerId] || 0;
            if (influence > 0.5 && Math.random() < influence * 0.3) {
                cell.owner = playerId;
                cell.color = color;
                cell.health = 80;
                expanded.push(cell);
            }
        }
        if (expanded.length > 0) this._invalidateBorderCache();
        return expanded;
    }

    contractTerritory(playerId) {
        const borders = this.getBorders(playerId);
        const hostileBorders = borders.filter(b => b.isHostile);
        const contracted = [];
        for (const border of hostileBorders) {
            const cell = border.cell;
            if (cell.health <= 0) {
                cell.owner = null;
                cell.color = null;
                contracted.push(cell);
            }
        }
        if (contracted.length > 0) this._invalidateBorderCache();
        return contracted;
    }

    applyDecay() {
        for (const cell of this.cells.values()) {
            if (cell.owner !== null) {
                const composition = cell.artQuality || 0.5;
                const decayRate = Math.max(0, 0.5 - composition * 0.5);
                cell.health = Math.max(0, cell.health - decayRate);
            }
        }
    }

    applyGrowth() {
        for (const cell of this.cells.values()) {
            if (cell.owner !== null && cell.artQuality > 0.6) {
                const growthRate = (cell.artQuality - 0.6) * 5;
                cell.health = Math.min(100, cell.health + growthRate);
            }
        }
    }

    getHexCorners(centerX, centerY) {
        const corners = [];
        for (let i = 0; i < 6; i++) {
            const angleDeg = 60 * i - 30;
            const angleRad = Math.PI / 180 * angleDeg;
            corners.push({
                x: centerX + this.hexSize * Math.cos(angleRad),
                y: centerY + this.hexSize * Math.sin(angleRad)
            });
        }
        return corners;
    }

    generateTerrain(noise) {
        for (const cell of this.cells.values()) {
            const scale = 0.15;
            cell.elevation = noise.fbm(cell.q * scale, cell.r * scale, 4);
            cell.temperature = noise.simplex2(cell.q * 0.1 + 100, cell.r * 0.1 + 100) * 0.5 + 0.5;
            cell.noiseValue = noise.perlin2(cell.q * 0.2, cell.r * 0.2);
            if (cell.elevation > 0.5) cell.terrain = 'mountain';
            else if (cell.elevation > 0.2) cell.terrain = 'hills';
            else if (cell.elevation > -0.1) cell.terrain = 'plains';
            else if (cell.elevation > -0.3) cell.terrain = 'marsh';
            else cell.terrain = 'water';
            cell.resources.pigment = Math.max(0, Math.floor((cell.noiseValue + 1) * 5));
            cell.resources.inspiration = Math.max(0, Math.floor(cell.temperature * 10));
        }
    }

    getStats() {
        const stats = { total: 0, claimed: 0, unclaimed: 0, byPlayer: {} };
        for (const cell of this.cells.values()) {
            stats.total++;
            if (cell.owner !== null) {
                stats.claimed++;
                if (!stats.byPlayer[cell.owner]) stats.byPlayer[cell.owner] = 0;
                stats.byPlayer[cell.owner]++;
            } else {
                stats.unclaimed++;
            }
        }
        return stats;
    }

    serialize() {
        const data = [];
        for (const cell of this.cells.values()) {
            data.push({
                q: cell.q, r: cell.r, owner: cell.owner, color: cell.color,
                terrain: cell.terrain, health: cell.health, artStyle: cell.artStyle,
                artQuality: cell.artQuality, resources: cell.resources
            });
        }
        return data;
    }

    deserialize(data) {
        for (const d of data) {
            const cell = this.getCell(d.q, d.r);
            if (cell) {
                cell.owner = d.owner;
                cell.color = d.color;
                cell.terrain = d.terrain;
                cell.health = d.health;
                cell.artStyle = d.artStyle;
                cell.artQuality = d.artQuality;
                cell.resources = d.resources || { pigment: 0, inspiration: 0 };
            }
        }
        this._invalidateBorderCache();
    }
}

window.CT.TerritorySystem = TerritorySystem;
window.CT.HexCell = HexCell;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: GameEngine (~400 lines)
# States, turn phases, players, actions, event system, game loop
# ═══════════════════════════════════════════════════════════════════════════════

GAME_ENGINE_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class EventBus {
    constructor() {
        this.listeners = {};
    }
    on(event, callback) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push(callback);
        return () => this.off(event, callback);
    }
    off(event, callback) {
        if (!this.listeners[event]) return;
        this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
    }
    emit(event, data) {
        if (!this.listeners[event]) return;
        for (const cb of this.listeners[event]) {
            try { cb(data); } catch(e) { console.error('Event handler error:', e); }
        }
    }
    once(event, callback) {
        const wrapper = (data) => { callback(data); this.off(event, wrapper); };
        this.on(event, wrapper);
    }
}

class Player {
    constructor(id, name, color, isAI) {
        this.id = id;
        this.name = name;
        this.color = color;
        this.isAI = isAI || false;
        this.palette = [];
        this.score = 0;
        this.resources = { pigment: 50, inspiration: 30, energy: 100 };
        this.artStyle = 'impressionist';
        this.achievements = [];
        this.turnOrder = id;
        this.eliminated = false;
        this.stats = {
            territoriesClaimed: 0,
            combatsWon: 0,
            combatsLost: 0,
            artworksCreated: 0,
            totalArtQuality: 0
        };
    }

    canAfford(cost) {
        for (const [resource, amount] of Object.entries(cost)) {
            if ((this.resources[resource] || 0) < amount) return false;
        }
        return true;
    }

    spend(cost) {
        for (const [resource, amount] of Object.entries(cost)) {
            this.resources[resource] = (this.resources[resource] || 0) - amount;
        }
    }

    earn(rewards) {
        for (const [resource, amount] of Object.entries(rewards)) {
            this.resources[resource] = (this.resources[resource] || 0) + amount;
        }
    }
}

class GameEngine {
    constructor() {
        this.events = new EventBus();
        this.state = 'menu';
        this.phase = 'waiting';
        this.players = [];
        this.currentPlayerIndex = 0;
        this.turnNumber = 0;
        this.maxTurns = 50;
        this.territory = null;
        this.actionQueue = [];
        this.history = [];
        this.config = {
            gridRadius: 8,
            startingTerritories: 3,
            resourcesPerTurn: { pigment: 10, inspiration: 5, energy: 20 },
            maxPlayers: 4,
            turnTimeLimit: 60,
            autoSave: true
        };
        this.turnTimer = 0;
        this.gameTime = 0;
        this.paused = false;
        this.winner = null;
        this.phases = ['select', 'act', 'resolve', 'grow', 'end_turn'];
        this.phaseIndex = 0;
    }

    initialize(playerConfigs) {
        this.territory = new window.CT.TerritorySystem(this.config.gridRadius);
        const noise = new window.CT.NoiseEngine(Date.now());
        this.territory.generateTerrain(noise);
        this.players = [];
        for (const config of playerConfigs) {
            const player = new Player(
                this.players.length, config.name, config.color, config.isAI
            );
            player.palette = config.palette || [];
            player.artStyle = config.artStyle || 'impressionist';
            this.players.push(player);
        }
        this._assignStartingPositions();
        this.state = 'playing';
        this.phase = 'select';
        this.phaseIndex = 0;
        this.turnNumber = 1;
        this.currentPlayerIndex = 0;
        this.events.emit('game:start', { players: this.players, turn: 1 });
    }

    _assignStartingPositions() {
        const positions = this._getStartingPositions(this.players.length);
        for (let i = 0; i < this.players.length; i++) {
            const player = this.players[i];
            const pos = positions[i];
            this.territory.claimTerritory(pos.q, pos.r, player.id, player.color);
            const neighbors = this.territory.getNeighbors(pos.q, pos.r);
            let claimed = 1;
            for (const n of neighbors) {
                if (claimed >= this.config.startingTerritories) break;
                if (n.owner === null && n.terrain !== 'water') {
                    this.territory.claimTerritory(n.q, n.r, player.id, player.color);
                    claimed++;
                }
            }
        }
    }

    _getStartingPositions(count) {
        const r = Math.floor(this.config.gridRadius * 0.6);
        const positions = [];
        for (let i = 0; i < count; i++) {
            const angle = (2 * Math.PI / count) * i;
            const q = Math.round(r * Math.cos(angle));
            const rr = Math.round(r * Math.sin(angle));
            const cell = this.territory.getCell(q, rr);
            if (cell && cell.terrain !== 'water') {
                positions.push({ q, r: rr });
            } else {
                positions.push({ q: Math.round(q * 0.8), r: Math.round(rr * 0.8) });
            }
        }
        return positions;
    }

    getCurrentPlayer() {
        return this.players[this.currentPlayerIndex] || null;
    }

    submitAction(action) {
        if (this.state !== 'playing') return false;
        const player = this.getCurrentPlayer();
        if (!player || player.eliminated) return false;
        action.playerId = player.id;
        action.turn = this.turnNumber;
        action.phase = this.phase;
        this.actionQueue.push(action);
        this.events.emit('action:submitted', action);
        return true;
    }

    advancePhase() {
        if (this.state !== 'playing') return;
        this._processActions();
        this.phaseIndex++;
        if (this.phaseIndex >= this.phases.length) {
            this._endTurn();
        } else {
            this.phase = this.phases[this.phaseIndex];
            this.events.emit('phase:change', { phase: this.phase, turn: this.turnNumber });
            if (this.phase === 'grow') {
                this._processGrowth();
                this.advancePhase();
            } else if (this.phase === 'resolve') {
                this._processResolve();
                this.advancePhase();
            }
        }
    }

    _processActions() {
        for (const action of this.actionQueue) {
            this.history.push(action);
        }
        this.actionQueue = [];
    }

    _processGrowth() {
        this.territory.applyGrowth();
        this.territory.applyDecay();
        for (const player of this.players) {
            if (player.eliminated) continue;
            const cells = this.territory.getPlayerCells(player.id);
            for (const cell of cells) {
                player.resources.pigment += cell.resources.pigment;
                player.resources.inspiration += cell.resources.inspiration;
            }
            player.earn(this.config.resourcesPerTurn);
            const expanded = this.territory.expandTerritory(player.id, player.color);
            if (expanded.length > 0) {
                this.events.emit('territory:expanded', { playerId: player.id, cells: expanded });
            }
        }
        const contracted = [];
        for (const player of this.players) {
            const lost = this.territory.contractTerritory(player.id);
            contracted.push(...lost);
        }
        this.events.emit('growth:complete', { turn: this.turnNumber });
    }

    _processResolve() {
        this.events.emit('resolve:complete', { turn: this.turnNumber });
    }

    _endTurn() {
        this.currentPlayerIndex++;
        if (this.currentPlayerIndex >= this.players.length) {
            this.currentPlayerIndex = 0;
            this.turnNumber++;
            this.events.emit('round:end', { turn: this.turnNumber - 1 });
        }
        while (this.currentPlayerIndex < this.players.length &&
               this.players[this.currentPlayerIndex].eliminated) {
            this.currentPlayerIndex++;
        }
        if (this.currentPlayerIndex >= this.players.length) {
            this.currentPlayerIndex = 0;
            this.turnNumber++;
        }
        if (this._checkGameEnd()) {
            this._endGame();
            return;
        }
        this.phaseIndex = 0;
        this.phase = this.phases[0];
        this.turnTimer = this.config.turnTimeLimit;
        this.events.emit('turn:start', {
            player: this.getCurrentPlayer(),
            turn: this.turnNumber,
            phase: this.phase
        });
    }

    _checkGameEnd() {
        if (this.turnNumber > this.maxTurns) return true;
        const activePlayers = this.players.filter(p => !p.eliminated);
        if (activePlayers.length <= 1) return true;
        const stats = this.territory.getStats();
        if (stats.unclaimed === 0) return true;
        return false;
    }

    _endGame() {
        this.state = 'gameover';
        this._calculateFinalScores();
        const sorted = this.players.slice().sort((a, b) => b.score - a.score);
        this.winner = sorted[0];
        this.events.emit('game:end', { winner: this.winner, players: sorted });
    }

    _calculateFinalScores() {
        for (const player of this.players) {
            const territory = this.territory.getPlayerTerritory(player.id);
            player.score = territory.count * 10;
            player.score += territory.avgHealth;
            player.score += player.stats.artworksCreated * 5;
            player.score += player.stats.combatsWon * 15;
        }
    }

    pause() { this.paused = true; this.events.emit('game:pause', {}); }
    resume() { this.paused = false; this.events.emit('game:resume', {}); }

    update(dt) {
        if (this.state !== 'playing' || this.paused) return;
        this.gameTime += dt;
        this.turnTimer -= dt;
        if (this.turnTimer <= 0) {
            this.events.emit('turn:timeout', { player: this.getCurrentPlayer() });
            this.advancePhase();
        }
    }

    getState() {
        return {
            state: this.state,
            phase: this.phase,
            turnNumber: this.turnNumber,
            currentPlayer: this.getCurrentPlayer(),
            players: this.players,
            territoryStats: this.territory ? this.territory.getStats() : null,
            gameTime: this.gameTime
        };
    }

    serialize() {
        return {
            state: this.state, phase: this.phase, turnNumber: this.turnNumber,
            currentPlayerIndex: this.currentPlayerIndex, maxTurns: this.maxTurns,
            players: this.players.map(p => ({
                id: p.id, name: p.name, color: p.color, isAI: p.isAI,
                score: p.score, resources: p.resources, palette: p.palette,
                artStyle: p.artStyle, eliminated: p.eliminated, stats: p.stats
            })),
            territory: this.territory ? this.territory.serialize() : [],
            history: this.history.slice(-100),
            config: this.config
        };
    }

    deserialize(data) {
        this.state = data.state;
        this.phase = data.phase;
        this.turnNumber = data.turnNumber;
        this.currentPlayerIndex = data.currentPlayerIndex;
        this.maxTurns = data.maxTurns;
        this.config = data.config;
        this.players = data.players.map(p => {
            const player = new Player(p.id, p.name, p.color, p.isAI);
            Object.assign(player, p);
            return player;
        });
        this.territory = new window.CT.TerritorySystem(this.config.gridRadius);
        this.territory.deserialize(data.territory);
        this.history = data.history || [];
    }
}

window.CT.GameEngine = GameEngine;
window.CT.EventBus = EventBus;
window.CT.Player = Player;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: CombatResolver (~300 lines)
# Color-based combat, border dynamics, art attacks
# ═══════════════════════════════════════════════════════════════════════════════

COMBAT_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class CombatResolver {
    constructor() {
        this.colorTheory = new window.CT.ColorTheory();
        this.attackTypes = {
            noise_burst: { cost: { pigment: 15, energy: 20 }, basePower: 30, artBonus: 1.2 },
            fractal_wave: { cost: { pigment: 25, energy: 30 }, basePower: 45, artBonus: 1.5 },
            lsystem_growth: { cost: { pigment: 20, energy: 15 }, basePower: 25, artBonus: 1.8 },
            particle_storm: { cost: { pigment: 10, energy: 35 }, basePower: 35, artBonus: 1.1 },
            cellular_invasion: { cost: { pigment: 20, energy: 25 }, basePower: 40, artBonus: 1.3 },
            color_flood: { cost: { pigment: 30, energy: 20 }, basePower: 50, artBonus: 1.0 },
            harmony_strike: { cost: { pigment: 35, energy: 40 }, basePower: 60, artBonus: 2.0 },
            composition_blast: { cost: { pigment: 40, energy: 45 }, basePower: 70, artBonus: 1.6 }
        };
    }

    calculateBorderStrength(attackerColor, defenderColor) {
        const rel = this.colorTheory.colorRelationship(attackerColor, defenderColor);
        switch (rel.type) {
            case 'complementary': return 1.5;
            case 'split-complementary': return 1.3;
            case 'triadic': return 1.1;
            case 'analogous': return 0.7;
            case 'identical': return 0.5;
            default: return 1.0;
        }
    }

    resolveAttack(attacker, defender, attackType, attackerCell, defenderCell) {
        const config = this.attackTypes[attackType];
        if (!config) return { success: false, reason: 'unknown_attack_type' };
        if (!attacker.canAfford(config.cost)) {
            return { success: false, reason: 'insufficient_resources' };
        }
        const borderMod = this.calculateBorderStrength(
            attacker.color, defender.color
        );
        const artQualityMod = (attackerCell.artQuality || 0.5) * config.artBonus;
        const compositionMod = (attackerCell.health || 50) / 100;
        const attackPower = config.basePower * borderMod * artQualityMod * compositionMod;
        const defenseBase = (defenderCell.health || 50) * 0.5;
        const defenseArt = (defenderCell.artQuality || 0.5) * 25;
        const defenderTerritory = window.CT.App && window.CT.App.game && window.CT.App.game.territory;
        let defenseNeighborBonus = 0;
        if (defenderTerritory) {
            const neighbors = defenderTerritory.getNeighbors(defenderCell.q, defenderCell.r);
            defenseNeighborBonus = neighbors.filter(n => n.owner === defender.id).length * 5;
        }
        const defensePower = defenseBase + defenseArt + defenseNeighborBonus;
        attacker.spend(config.cost);
        const success = attackPower > defensePower;
        const damage = Math.max(0, attackPower - defensePower * 0.5);
        const result = {
            success,
            attackPower: Math.round(attackPower),
            defensePower: Math.round(defensePower),
            damage: Math.round(damage),
            borderStrength: borderMod,
            artQualityBonus: artQualityMod,
            attackType
        };
        if (success) {
            defenderCell.health -= damage;
            if (defenderCell.health <= 0) {
                result.captured = true;
                defenderCell.owner = attacker.id;
                defenderCell.color = attacker.color;
                defenderCell.health = 50;
                defenderCell.artQuality = 0;
                attacker.stats.territoriesClaimed++;
                attacker.stats.combatsWon++;
                defender.stats.combatsLost++;
            } else {
                result.captured = false;
                attacker.stats.combatsWon++;
            }
        } else {
            defender.stats.combatsWon++;
            attacker.stats.combatsLost++;
            const recoil = Math.max(0, defensePower - attackPower) * 0.3;
            attackerCell.health -= recoil;
            result.recoilDamage = Math.round(recoil);
        }
        return result;
    }

    getAvailableAttacks(player) {
        const available = [];
        for (const [name, config] of Object.entries(this.attackTypes)) {
            available.push({
                name,
                ...config,
                affordable: player.canAfford(config.cost),
                description: this._getAttackDescription(name)
            });
        }
        return available;
    }

    _getAttackDescription(type) {
        const descriptions = {
            noise_burst: 'Unleash Perlin noise patterns to disrupt enemy territory',
            fractal_wave: 'Send fractal waves that erode defenses with mathematical beauty',
            lsystem_growth: 'Grow L-system patterns that overwhelm neighboring cells',
            particle_storm: 'Storm of generative particles that weaken borders',
            cellular_invasion: 'Deploy cellular automata to consume enemy territory',
            color_flood: 'Flood territory with your palette to claim by color dominance',
            harmony_strike: 'Use perfect color harmony to destabilize dissonant territories',
            composition_blast: 'Channel composition mastery into a devastating artistic attack'
        };
        return descriptions[type] || 'Unknown attack';
    }

    simulateCombat(attacker, defender, attackType) {
        const config = this.attackTypes[attackType];
        if (!config) return null;
        const borderMod = this.calculateBorderStrength(attacker.color, defender.color);
        const power = config.basePower * borderMod;
        return {
            estimatedPower: Math.round(power),
            cost: config.cost,
            borderAdvantage: borderMod > 1.0,
            colorRelationship: this.colorTheory.colorRelationship(attacker.color, defender.color)
        };
    }

    getAttackTypeForStyle(artStyle) {
        const styleMap = {
            impressionist: 'noise_burst',
            geometric: 'fractal_wave',
            organic: 'lsystem_growth',
            abstract: 'particle_storm',
            minimalist: 'cellular_invasion',
            expressionist: 'color_flood',
            harmonic: 'harmony_strike',
            structured: 'composition_blast'
        };
        return styleMap[artStyle] || 'noise_burst';
    }
}

window.CT.CombatResolver = CombatResolver;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: AIOpponent (~400 lines)
# Difficulty levels, artistic styles, evaluation, minimax-lite
# ═══════════════════════════════════════════════════════════════════════════════

AI_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class AIOpponent {
    constructor(playerId, difficulty) {
        this.playerId = playerId;
        this.difficulty = difficulty || 'medium';
        this.style = 'balanced';
        this.searchDepth = difficulty === 'hard' ? 3 : difficulty === 'medium' ? 2 : 1;
        this.explorationFactor = 0.3;
        this.styleWeights = {
            aggressive: { expand: 0.3, attack: 0.5, defend: 0.1, art: 0.1 },
            defensive: { expand: 0.2, attack: 0.1, defend: 0.5, art: 0.2 },
            artistic: { expand: 0.15, attack: 0.1, defend: 0.15, art: 0.6 },
            balanced: { expand: 0.25, attack: 0.25, defend: 0.25, art: 0.25 },
            expansionist: { expand: 0.5, attack: 0.2, defend: 0.15, art: 0.15 }
        };
        this.memory = { lastActions: [], opponentStrategies: {}, favoriteAttacks: {} };
    }

    setStyle(style) {
        if (this.styleWeights[style]) this.style = style;
    }

    evaluateBoard(game) {
        const player = game.players[this.playerId];
        if (!player || player.eliminated) return -Infinity;
        const territory = game.territory.getPlayerTerritory(this.playerId);
        const stats = game.territory.getStats();
        let score = 0;
        score += territory.count * 10;
        score += territory.avgHealth * 0.5;
        score += player.resources.pigment * 0.3;
        score += player.resources.inspiration * 0.5;
        score += player.resources.energy * 0.2;
        score += player.stats.combatsWon * 8;
        score -= player.stats.combatsLost * 5;
        score += player.stats.artworksCreated * 12;
        const cells = game.territory.getPlayerCells(this.playerId);
        let totalArt = 0;
        for (const cell of cells) {
            totalArt += cell.artQuality || 0;
        }
        score += totalArt * 20;
        const borders = game.territory.getBorders(this.playerId);
        const hostileBorders = borders.filter(b => b.isHostile).length;
        const friendlyBorders = borders.filter(b => b.isFriendly).length;
        score -= hostileBorders * 2;
        score += friendlyBorders * 1;
        const totalCells = stats.total;
        const controlPct = territory.count / totalCells;
        if (controlPct > 0.5) score += 100;
        if (controlPct > 0.7) score += 200;
        return score;
    }

    generateMoves(game) {
        const moves = [];
        const player = game.players[this.playerId];
        if (!player || player.eliminated) return moves;
        const borders = game.territory.getBorders(this.playerId);
        const friendlyBorders = borders.filter(b => b.isFriendly);
        for (const border of friendlyBorders) {
            const cell = border.neighbor;
            if (cell.terrain !== 'water') {
                moves.push({
                    type: 'claim',
                    target: { q: cell.q, r: cell.r },
                    cost: { pigment: 10, energy: 15 },
                    priority: this._evaluateClaimTarget(cell, game)
                });
            }
        }
        const hostileBorders = borders.filter(b => b.isHostile);
        const combat = new window.CT.CombatResolver();
        for (const border of hostileBorders) {
            const attackTypes = combat.getAvailableAttacks(player);
            for (const attack of attackTypes) {
                if (attack.affordable) {
                    moves.push({
                        type: 'attack',
                        target: { q: border.neighbor.q, r: border.neighbor.r },
                        attackType: attack.name,
                        cost: attack.cost,
                        priority: this._evaluateAttackTarget(border, attack, game)
                    });
                }
            }
        }
        const cells = game.territory.getPlayerCells(this.playerId);
        for (const cell of cells) {
            if ((cell.artQuality || 0) < 0.8) {
                moves.push({
                    type: 'create_art',
                    target: { q: cell.q, r: cell.r },
                    cost: { pigment: 5, inspiration: 10 },
                    priority: this._evaluateArtTarget(cell, game)
                });
            }
        }
        moves.push({ type: 'fortify', cost: { energy: 10 }, priority: 20 });
        moves.push({ type: 'pass', cost: {}, priority: 5 });
        return moves;
    }

    _evaluateClaimTarget(cell, game) {
        let score = 50;
        if (cell.terrain === 'plains') score += 10;
        if (cell.terrain === 'hills') score += 5;
        score += cell.resources.pigment * 2;
        score += cell.resources.inspiration * 3;
        const neighbors = game.territory.getNeighbors(cell.q, cell.r);
        const friendlyCount = neighbors.filter(n => n.owner === this.playerId).length;
        score += friendlyCount * 15;
        const enemyCount = neighbors.filter(n => n.owner !== null && n.owner !== this.playerId).length;
        if (this.style === 'aggressive') score += enemyCount * 5;
        else score -= enemyCount * 5;
        return score;
    }

    _evaluateAttackTarget(border, attack, game) {
        let score = 30;
        const ct = new window.CT.ColorTheory();
        const player = game.players[this.playerId];
        const rel = ct.colorRelationship(player.color, border.neighbor.color);
        if (rel.type === 'complementary') score += 30;
        if (rel.type === 'analogous') score -= 20;
        score += (100 - (border.neighbor.health || 50)) * 0.3;
        if (border.neighbor.artQuality < 0.3) score += 20;
        const weights = this.styleWeights[this.style];
        score *= (weights.attack * 4);
        return score;
    }

    _evaluateArtTarget(cell, game) {
        let score = 40;
        const borders = game.territory.getBorders(this.playerId);
        const isBorder = borders.some(b => b.cell.q === cell.q && b.cell.r === cell.r);
        if (isBorder) score += 25;
        score += (1 - (cell.artQuality || 0)) * 30;
        const weights = this.styleWeights[this.style];
        score *= (weights.art * 4);
        return score;
    }

    selectMove(game) {
        const moves = this.generateMoves(game);
        if (moves.length === 0) return { type: 'pass' };
        if (this.difficulty === 'easy') {
            return this._selectEasy(moves);
        } else if (this.difficulty === 'hard') {
            return this._selectHard(moves, game);
        } else {
            return this._selectMedium(moves);
        }
    }

    _selectEasy(moves) {
        const viable = moves.filter(m => m.priority > 10);
        if (viable.length === 0) return moves[moves.length - 1];
        const idx = Math.floor(Math.random() * viable.length);
        return viable[idx];
    }

    _selectMedium(moves) {
        moves.sort((a, b) => b.priority - a.priority);
        const topN = Math.min(3, moves.length);
        if (Math.random() < this.explorationFactor) {
            return moves[Math.floor(Math.random() * topN)];
        }
        return moves[0];
    }

    _selectHard(moves, game) {
        moves.sort((a, b) => b.priority - a.priority);
        const topMoves = moves.slice(0, 5);
        let bestMove = topMoves[0];
        let bestScore = -Infinity;
        for (const move of topMoves) {
            const score = this._minimaxEval(move, game, this.searchDepth, false);
            if (score > bestScore) {
                bestScore = score;
                bestMove = move;
            }
        }
        return bestMove;
    }

    _minimaxEval(move, game, depth, isMaximizing) {
        if (depth === 0) return move.priority + this.evaluateBoard(game) * 0.1;
        let score = move.priority;
        if (isMaximizing) {
            score += this.evaluateBoard(game) * 0.2;
        } else {
            score -= 10;
        }
        return score;
    }

    adaptStrategy(game) {
        const territory = game.territory.getPlayerTerritory(this.playerId);
        const stats = game.territory.getStats();
        const controlPct = territory.count / stats.total;
        if (controlPct < 0.15) {
            this.style = 'defensive';
        } else if (controlPct > 0.4) {
            this.style = 'aggressive';
        } else if (territory.avgHealth < 40) {
            this.style = 'artistic';
        } else if (game.turnNumber > game.maxTurns * 0.7) {
            this.style = 'aggressive';
        } else {
            this.style = 'balanced';
        }
    }

    recordAction(action) {
        this.memory.lastActions.push(action);
        if (this.memory.lastActions.length > 20) {
            this.memory.lastActions.shift();
        }
    }
}

window.CT.AIOpponent = AIOpponent;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: ScoringSystem (~250 lines)
# Composition scoring, achievements, leaderboard
# ═══════════════════════════════════════════════════════════════════════════════

SCORING_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class ScoringSystem {
    constructor() {
        this.leaderboard = [];
        this.maxLeaderboardSize = 20;
        this.achievements = {
            first_claim: { name: 'First Steps', description: 'Claim your first territory', icon: 'flag', unlocked: false },
            territory_5: { name: 'Growing Empire', description: 'Control 5 territories', icon: 'map', unlocked: false },
            territory_20: { name: 'Vast Domain', description: 'Control 20 territories', icon: 'globe', unlocked: false },
            art_master: { name: 'Art Master', description: 'Create 10 artworks with quality > 0.7', icon: 'palette', unlocked: false },
            combat_victor: { name: 'Battle Artist', description: 'Win 5 combats', icon: 'sword', unlocked: false },
            harmony_king: { name: 'Color Harmonist', description: 'Achieve palette score > 0.8', icon: 'rainbow', unlocked: false },
            composition_ace: { name: 'Composition Ace', description: 'Territory composition score > 0.9', icon: 'star', unlocked: false },
            speed_painter: { name: 'Speed Painter', description: 'Create 5 artworks in one turn', icon: 'clock', unlocked: false },
            strategist: { name: 'Grand Strategist', description: 'Win a game with all territories healthy', icon: 'crown', unlocked: false },
            generative_genius: { name: 'Generative Genius', description: 'Use all attack types', icon: 'brain', unlocked: false },
            color_theory: { name: 'Color Theorist', description: 'Win using complementary color advantage', icon: 'eyedropper', unlocked: false },
            peaceful: { name: 'Peaceful Expansion', description: 'Win without attacking', icon: 'dove', unlocked: false }
        };
    }

    calculateTerritoryScore(territory, game) {
        const cells = game.territory.getPlayerCells(territory.owner);
        let score = {
            territory: cells.length * 10,
            health: 0,
            artQuality: 0,
            resourceEfficiency: 0,
            colorHarmony: 0,
            composition: 0,
            combat: 0,
            total: 0
        };
        let totalHealth = 0;
        let totalArt = 0;
        const colors = [];
        for (const cell of cells) {
            totalHealth += cell.health;
            totalArt += (cell.artQuality || 0);
            if (cell.color) colors.push(cell.color);
        }
        score.health = cells.length > 0 ? Math.round(totalHealth / cells.length) : 0;
        score.artQuality = cells.length > 0 ? Math.round((totalArt / cells.length) * 100) : 0;
        if (colors.length > 1) {
            const ct = new window.CT.ColorTheory();
            const paletteScore = ct.scorePalette(colors);
            score.colorHarmony = Math.round(paletteScore.overall * 100);
        }
        const player = game.players[territory.owner];
        if (player) {
            score.combat = player.stats.combatsWon * 15 - player.stats.combatsLost * 5;
            score.resourceEfficiency = Math.round(
                (player.stats.artworksCreated * 5) /
                Math.max(1, game.turnNumber) * 10
            );
        }
        score.total = score.territory + score.health + score.artQuality +
                       score.colorHarmony + score.combat + score.resourceEfficiency;
        return score;
    }

    checkAchievements(player, game) {
        const unlocked = [];
        const cells = game.territory.getPlayerCells(player.id);
        if (cells.length >= 1 && !this.achievements.first_claim.unlocked) {
            this.achievements.first_claim.unlocked = true;
            unlocked.push('first_claim');
        }
        if (cells.length >= 5 && !this.achievements.territory_5.unlocked) {
            this.achievements.territory_5.unlocked = true;
            unlocked.push('territory_5');
        }
        if (cells.length >= 20 && !this.achievements.territory_20.unlocked) {
            this.achievements.territory_20.unlocked = true;
            unlocked.push('territory_20');
        }
        if (player.stats.combatsWon >= 5 && !this.achievements.combat_victor.unlocked) {
            this.achievements.combat_victor.unlocked = true;
            unlocked.push('combat_victor');
        }
        const highQualityArt = cells.filter(c => (c.artQuality || 0) > 0.7).length;
        if (highQualityArt >= 10 && !this.achievements.art_master.unlocked) {
            this.achievements.art_master.unlocked = true;
            unlocked.push('art_master');
        }
        return unlocked;
    }

    addToLeaderboard(entry) {
        this.leaderboard.push({
            name: entry.name,
            score: entry.score,
            territories: entry.territories,
            artScore: entry.artScore,
            date: new Date().toISOString(),
            turns: entry.turns
        });
        this.leaderboard.sort((a, b) => b.score - a.score);
        if (this.leaderboard.length > this.maxLeaderboardSize) {
            this.leaderboard = this.leaderboard.slice(0, this.maxLeaderboardSize);
        }
    }

    getLeaderboard() {
        return this.leaderboard.map((entry, idx) => ({
            rank: idx + 1,
            ...entry
        }));
    }

    getAchievements() {
        return Object.entries(this.achievements).map(([id, data]) => ({
            id, ...data
        }));
    }

    getUnlockedCount() {
        return Object.values(this.achievements).filter(a => a.unlocked).length;
    }

    getTotalCount() {
        return Object.keys(this.achievements).length;
    }

    getScoreBreakdown(player, game) {
        const territory = game.territory.getPlayerTerritory(player.id);
        return {
            territoryControl: territory.count * 10,
            averageHealth: Math.round(territory.avgHealth),
            combatProwess: player.stats.combatsWon * 15,
            artisanship: player.stats.artworksCreated * 5,
            achievementBonus: this.getUnlockedCount() * 25,
            total: territory.count * 10 + Math.round(territory.avgHealth) +
                   player.stats.combatsWon * 15 + player.stats.artworksCreated * 5 +
                   this.getUnlockedCount() * 25
        };
    }

    serialize() {
        return {
            leaderboard: this.leaderboard,
            achievements: Object.fromEntries(
                Object.entries(this.achievements).map(([k, v]) => [k, { unlocked: v.unlocked }])
            )
        };
    }

    deserialize(data) {
        if (data.leaderboard) this.leaderboard = data.leaderboard;
        if (data.achievements) {
            for (const [id, state] of Object.entries(data.achievements)) {
                if (this.achievements[id]) {
                    this.achievements[id].unlocked = state.unlocked;
                }
            }
        }
    }
}

window.CT.ScoringSystem = ScoringSystem;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: CanvasRenderer (~500 lines)
# Layers, hex rendering, terrain, particles, minimap
# ═══════════════════════════════════════════════════════════════════════════════

CANVAS_RENDERER_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class Camera {
    constructor(canvasWidth, canvasHeight) {
        this.x = 0;
        this.y = 0;
        this.zoom = 1.0;
        this.minZoom = 0.3;
        this.maxZoom = 3.0;
        this.canvasWidth = canvasWidth;
        this.canvasHeight = canvasHeight;
        this.targetX = 0;
        this.targetY = 0;
        this.targetZoom = 1.0;
        this.smoothing = 0.1;
        this.dragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
    }

    worldToScreen(wx, wy) {
        return {
            x: (wx - this.x) * this.zoom + this.canvasWidth / 2,
            y: (wy - this.y) * this.zoom + this.canvasHeight / 2
        };
    }

    screenToWorld(sx, sy) {
        return {
            x: (sx - this.canvasWidth / 2) / this.zoom + this.x,
            y: (sy - this.canvasHeight / 2) / this.zoom + this.y
        };
    }

    panTo(x, y) { this.targetX = x; this.targetY = y; }
    zoomTo(z) { this.targetZoom = Math.max(this.minZoom, Math.min(this.maxZoom, z)); }
    zoomBy(delta) { this.zoomTo(this.targetZoom + delta); }

    update(dt) {
        this.x += (this.targetX - this.x) * this.smoothing;
        this.y += (this.targetY - this.y) * this.smoothing;
        this.zoom += (this.targetZoom - this.zoom) * this.smoothing;
    }

    startDrag(sx, sy) {
        this.dragging = true;
        this.dragStartX = sx;
        this.dragStartY = sy;
    }

    drag(sx, sy) {
        if (!this.dragging) return;
        const dx = (sx - this.dragStartX) / this.zoom;
        const dy = (sy - this.dragStartY) / this.zoom;
        this.targetX -= dx;
        this.targetY -= dy;
        this.dragStartX = sx;
        this.dragStartY = sy;
    }

    endDrag() { this.dragging = false; }

    resize(w, h) { this.canvasWidth = w; this.canvasHeight = h; }
}

class CanvasRenderer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
        this.width = this.canvas ? this.canvas.width : 1200;
        this.height = this.canvas ? this.canvas.height : 800;
        this.camera = new Camera(this.width, this.height);
        this.layers = {
            terrain: null,
            territories: null,
            borders: null,
            art: null,
            particles: null,
            ui: null
        };
        this.hoveredCell = null;
        this.selectedCell = null;
        this.showGrid = true;
        this.showMinimap = true;
        this.minimapSize = 180;
        this.minimapPadding = 10;
        this.terrainColors = {
            water: '#1e3a5f',
            marsh: '#2d5a27',
            plains: '#3a5a3a',
            hills: '#5a4a3a',
            mountain: '#6b6b6b'
        };
        this.animationTime = 0;
        this._createOffscreenLayers();
    }

    _createOffscreenLayers() {
        for (const name of Object.keys(this.layers)) {
            if (typeof OffscreenCanvas !== 'undefined') {
                const c = new OffscreenCanvas(this.width, this.height);
                this.layers[name] = { canvas: c, ctx: c.getContext('2d'), dirty: true };
            } else {
                const c = document.createElement('canvas');
                c.width = this.width;
                c.height = this.height;
                this.layers[name] = { canvas: c, ctx: c.getContext('2d'), dirty: true };
            }
        }
    }

    resize(w, h) {
        this.width = w;
        this.height = h;
        if (this.canvas) { this.canvas.width = w; this.canvas.height = h; }
        this.camera.resize(w, h);
        for (const layer of Object.values(this.layers)) {
            layer.canvas.width = w;
            layer.canvas.height = h;
            layer.dirty = true;
        }
    }

    render(game, particles, dt) {
        if (!this.ctx) return;
        this.animationTime += dt;
        this.camera.update(dt);
        this.ctx.clearRect(0, 0, this.width, this.height);
        this.ctx.fillStyle = '#0a0e1a';
        this.ctx.fillRect(0, 0, this.width, this.height);
        if (game && game.territory) {
            this._renderTerrain(game.territory);
            this._renderTerritories(game.territory);
            this._renderBorders(game.territory, game.players);
            if (this.showGrid) this._renderGrid(game.territory);
            this._renderArt(game.territory);
        }
        if (particles) this._renderParticles(particles);
        this._renderHoverHighlight(game);
        this._renderSelectionHighlight(game);
        if (this.showMinimap && game && game.territory) {
            this._renderMinimap(game.territory, game.players);
        }
    }

    _renderTerrain(territory) {
        const layer = this.layers.terrain;
        if (!layer.dirty) {
            this.ctx.drawImage(layer.canvas, 0, 0);
            return;
        }
        layer.ctx.clearRect(0, 0, this.width, this.height);
        for (const cell of territory.cells.values()) {
            const pixel = territory.hexToPixel(cell.q, cell.r);
            const screen = this.camera.worldToScreen(pixel.x, pixel.y);
            if (screen.x < -50 || screen.x > this.width + 50 ||
                screen.y < -50 || screen.y > this.height + 50) continue;
            const corners = territory.getHexCorners(screen.x, screen.y);
            const scaledCorners = corners.map(c => ({
                x: screen.x + (c.x - screen.x) * this.camera.zoom,
                y: screen.y + (c.y - screen.y) * this.camera.zoom
            }));
            layer.ctx.beginPath();
            layer.ctx.moveTo(scaledCorners[0].x, scaledCorners[0].y);
            for (let i = 1; i < 6; i++) {
                layer.ctx.lineTo(scaledCorners[i].x, scaledCorners[i].y);
            }
            layer.ctx.closePath();
            const baseColor = this.terrainColors[cell.terrain] || this.terrainColors.plains;
            const elevation = (cell.elevation + 1) / 2;
            layer.ctx.fillStyle = baseColor;
            layer.ctx.globalAlpha = 0.3 + elevation * 0.4;
            layer.ctx.fill();
            layer.ctx.globalAlpha = 1.0;
        }
        layer.dirty = false;
        this.ctx.drawImage(layer.canvas, 0, 0);
    }

    _renderTerritories(territory) {
        for (const cell of territory.cells.values()) {
            if (cell.owner === null) continue;
            const pixel = territory.hexToPixel(cell.q, cell.r);
            const screen = this.camera.worldToScreen(pixel.x, pixel.y);
            if (screen.x < -50 || screen.x > this.width + 50 ||
                screen.y < -50 || screen.y > this.height + 50) continue;
            const corners = territory.getHexCorners(screen.x, screen.y);
            const sc = corners.map(c => ({
                x: screen.x + (c.x - screen.x) * this.camera.zoom,
                y: screen.y + (c.y - screen.y) * this.camera.zoom
            }));
            this.ctx.beginPath();
            this.ctx.moveTo(sc[0].x, sc[0].y);
            for (let i = 1; i < 6; i++) this.ctx.lineTo(sc[i].x, sc[i].y);
            this.ctx.closePath();
            const healthAlpha = 0.3 + (cell.health / 100) * 0.5;
            this.ctx.fillStyle = cell.color || '#6366f1';
            this.ctx.globalAlpha = healthAlpha;
            this.ctx.fill();
            this.ctx.globalAlpha = 1.0;
        }
    }

    _renderBorders(territory, players) {
        for (const player of players) {
            if (player.eliminated) continue;
            const borders = territory.getBorders(player.id);
            this.ctx.strokeStyle = player.color;
            this.ctx.lineWidth = 2 * this.camera.zoom;
            this.ctx.globalAlpha = 0.8;
            for (const border of borders) {
                if (!border.isHostile) continue;
                const pixel = territory.hexToPixel(border.cell.q, border.cell.r);
                const screen = this.camera.worldToScreen(pixel.x, pixel.y);
                const corners = territory.getHexCorners(screen.x, screen.y);
                const sc = corners.map(c => ({
                    x: screen.x + (c.x - screen.x) * this.camera.zoom,
                    y: screen.y + (c.y - screen.y) * this.camera.zoom
                }));
                const dir = border.direction;
                const i1 = dir;
                const i2 = (dir + 1) % 6;
                this.ctx.beginPath();
                this.ctx.moveTo(sc[i1].x, sc[i1].y);
                this.ctx.lineTo(sc[i2].x, sc[i2].y);
                this.ctx.stroke();
            }
            this.ctx.globalAlpha = 1.0;
        }
    }

    _renderGrid(territory) {
        this.ctx.strokeStyle = 'rgba(100, 116, 139, 0.15)';
        this.ctx.lineWidth = 0.5;
        for (const cell of territory.cells.values()) {
            const pixel = territory.hexToPixel(cell.q, cell.r);
            const screen = this.camera.worldToScreen(pixel.x, pixel.y);
            if (screen.x < -50 || screen.x > this.width + 50 ||
                screen.y < -50 || screen.y > this.height + 50) continue;
            const corners = territory.getHexCorners(screen.x, screen.y);
            const sc = corners.map(c => ({
                x: screen.x + (c.x - screen.x) * this.camera.zoom,
                y: screen.y + (c.y - screen.y) * this.camera.zoom
            }));
            this.ctx.beginPath();
            this.ctx.moveTo(sc[0].x, sc[0].y);
            for (let i = 1; i < 6; i++) this.ctx.lineTo(sc[i].x, sc[i].y);
            this.ctx.closePath();
            this.ctx.stroke();
        }
    }

    _renderArt(territory) {
        for (const cell of territory.cells.values()) {
            if (!cell.artStyle || cell.artQuality <= 0) continue;
            const pixel = territory.hexToPixel(cell.q, cell.r);
            const screen = this.camera.worldToScreen(pixel.x, pixel.y);
            if (screen.x < -80 || screen.x > this.width + 80 ||
                screen.y < -80 || screen.y > this.height + 80) continue;
            const size = territory.hexSize * this.camera.zoom * 0.8;
            this.ctx.save();
            this.ctx.globalAlpha = cell.artQuality * 0.6;
            this.ctx.fillStyle = cell.color || '#818cf8';
            this.ctx.beginPath();
            this.ctx.arc(screen.x, screen.y, size * 0.3, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.restore();
        }
    }

    _renderParticles(particleSystem) {
        this.ctx.save();
        particleSystem.draw(this.ctx);
        this.ctx.restore();
    }

    _renderHoverHighlight(game) {
        if (!this.hoveredCell || !game || !game.territory) return;
        const cell = game.territory.getCell(this.hoveredCell.q, this.hoveredCell.r);
        if (!cell) return;
        const pixel = game.territory.hexToPixel(cell.q, cell.r);
        const screen = this.camera.worldToScreen(pixel.x, pixel.y);
        const corners = game.territory.getHexCorners(screen.x, screen.y);
        const sc = corners.map(c => ({
            x: screen.x + (c.x - screen.x) * this.camera.zoom,
            y: screen.y + (c.y - screen.y) * this.camera.zoom
        }));
        this.ctx.strokeStyle = '#fbbf24';
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.ctx.moveTo(sc[0].x, sc[0].y);
        for (let i = 1; i < 6; i++) this.ctx.lineTo(sc[i].x, sc[i].y);
        this.ctx.closePath();
        this.ctx.stroke();
    }

    _renderSelectionHighlight(game) {
        if (!this.selectedCell || !game || !game.territory) return;
        const cell = game.territory.getCell(this.selectedCell.q, this.selectedCell.r);
        if (!cell) return;
        const pixel = game.territory.hexToPixel(cell.q, cell.r);
        const screen = this.camera.worldToScreen(pixel.x, pixel.y);
        const corners = game.territory.getHexCorners(screen.x, screen.y);
        const sc = corners.map(c => ({
            x: screen.x + (c.x - screen.x) * this.camera.zoom,
            y: screen.y + (c.y - screen.y) * this.camera.zoom
        }));
        const pulse = 0.5 + Math.sin(this.animationTime * 4) * 0.3;
        this.ctx.strokeStyle = '#22c55e';
        this.ctx.lineWidth = 3;
        this.ctx.globalAlpha = pulse;
        this.ctx.beginPath();
        this.ctx.moveTo(sc[0].x, sc[0].y);
        for (let i = 1; i < 6; i++) this.ctx.lineTo(sc[i].x, sc[i].y);
        this.ctx.closePath();
        this.ctx.stroke();
        this.ctx.globalAlpha = 1.0;
    }

    _renderMinimap(territory, players) {
        const mx = this.width - this.minimapSize - this.minimapPadding;
        const my = this.minimapPadding;
        this.ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
        this.ctx.fillRect(mx, my, this.minimapSize, this.minimapSize);
        this.ctx.strokeStyle = 'rgba(100, 116, 139, 0.5)';
        this.ctx.lineWidth = 1;
        this.ctx.strokeRect(mx, my, this.minimapSize, this.minimapSize);
        const radius = territory.radius;
        const scale = this.minimapSize / (radius * 4 + 2);
        const centerX = mx + this.minimapSize / 2;
        const centerY = my + this.minimapSize / 2;
        for (const cell of territory.cells.values()) {
            const px = cell.q * scale * 1.5 + centerX;
            const py = (cell.r + cell.q * 0.5) * scale * 1.73 + centerY;
            if (cell.owner !== null) {
                this.ctx.fillStyle = cell.color || '#6366f1';
                this.ctx.globalAlpha = 0.8;
            } else {
                this.ctx.fillStyle = this.terrainColors[cell.terrain] || '#334155';
                this.ctx.globalAlpha = 0.3;
            }
            this.ctx.fillRect(px - scale * 0.4, py - scale * 0.4, scale * 0.8, scale * 0.8);
        }
        this.ctx.globalAlpha = 1.0;
    }

    handleMouseMove(e, territory) {
        if (!territory) return;
        const rect = this.canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        if (this.camera.dragging) {
            this.camera.drag(sx, sy);
            this.layers.terrain.dirty = true;
            return;
        }
        const world = this.camera.screenToWorld(sx, sy);
        const hex = territory.pixelToHex(world.x, world.y);
        this.hoveredCell = hex;
    }

    handleClick(e, territory) {
        if (!territory) return null;
        const rect = this.canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        const world = this.camera.screenToWorld(sx, sy);
        const hex = territory.pixelToHex(world.x, world.y);
        this.selectedCell = hex;
        return hex;
    }

    handleWheel(e) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        this.camera.zoomBy(delta);
        this.layers.terrain.dirty = true;
    }

    invalidateAll() {
        for (const layer of Object.values(this.layers)) {
            layer.dirty = true;
        }
    }
}

window.CT.CanvasRenderer = CanvasRenderer;
window.CT.Camera = Camera;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: UISystem (~400 lines)
# Panels, modals, tooltips, HUD, palette selector
# ═══════════════════════════════════════════════════════════════════════════════

UI_SYSTEM_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class UISystem {
    constructor() {
        this.panels = {};
        this.modals = [];
        this.tooltipEl = null;
        this.toastQueue = [];
        this.activePanel = 'game';
        this.hudVisible = true;
        this.initialized = false;
    }

    init() {
        this._createTooltip();
        this._createToastContainer();
        this._bindGlobalEvents();
        this.initialized = true;
    }

    _createTooltip() {
        this.tooltipEl = document.createElement('div');
        this.tooltipEl.className = 'ct-tooltip';
        this.tooltipEl.style.cssText = 'display:none;position:fixed;z-index:10000;' +
            'background:rgba(15,23,42,0.95);color:#f8fafc;padding:8px 12px;' +
            'border-radius:6px;font-size:13px;pointer-events:none;max-width:250px;' +
            'border:1px solid rgba(99,102,241,0.3);box-shadow:0 4px 12px rgba(0,0,0,0.4)';
        document.body.appendChild(this.tooltipEl);
    }

    _createToastContainer() {
        const container = document.createElement('div');
        container.id = 'ct-toast-container';
        container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;' +
            'display:flex;flex-direction:column;gap:8px;pointer-events:none';
        document.body.appendChild(container);
    }

    _bindGlobalEvents() {
        document.addEventListener('mousemove', (e) => {
            if (this.tooltipEl && this.tooltipEl.style.display !== 'none') {
                this.tooltipEl.style.left = (e.clientX + 12) + 'px';
                this.tooltipEl.style.top = (e.clientY + 12) + 'px';
            }
        });
    }

    showTooltip(text, x, y) {
        if (!this.tooltipEl) return;
        this.tooltipEl.textContent = text;
        this.tooltipEl.style.display = 'block';
        this.tooltipEl.style.left = (x + 12) + 'px';
        this.tooltipEl.style.top = (y + 12) + 'px';
    }

    hideTooltip() {
        if (this.tooltipEl) this.tooltipEl.style.display = 'none';
    }

    showToast(message, type, duration) {
        type = type || 'info';
        duration = duration || 3000;
        const container = document.getElementById('ct-toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = 'ct-toast ct-toast-' + type;
        const colors = { info: '#3b82f6', success: '#22c55e', warning: '#f59e0b', danger: '#ef4444' };
        toast.style.cssText = 'padding:12px 20px;border-radius:8px;color:#f8fafc;' +
            'font-size:14px;pointer-events:auto;cursor:pointer;' +
            'background:' + (colors[type] || colors.info) + ';' +
            'box-shadow:0 4px 12px rgba(0,0,0,0.3);' +
            'animation:ct-slide-in 0.3s ease-out;max-width:350px';
        toast.textContent = message;
        toast.addEventListener('click', () => toast.remove());
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.animation = 'ct-fade-out 0.3s ease-out';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }

    showModal(config) {
        const overlay = document.createElement('div');
        overlay.className = 'ct-modal-overlay';
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;' +
            'background:rgba(0,0,0,0.7);z-index:8000;display:flex;align-items:center;' +
            'justify-content:center;animation:ct-fade-in 0.2s ease-out';
        const modal = document.createElement('div');
        modal.className = 'ct-modal';
        modal.style.cssText = 'background:#1e293b;border-radius:12px;padding:24px;' +
            'max-width:' + (config.width || '500px') + ';width:90%;max-height:80vh;' +
            'overflow-y:auto;border:1px solid rgba(99,102,241,0.2);' +
            'box-shadow:0 20px 60px rgba(0,0,0,0.5);animation:ct-scale-in 0.3s ease-out';
        let html = '';
        if (config.title) html += '<h2 style="color:#f8fafc;margin:0 0 16px;font-size:20px">' + config.title + '</h2>';
        if (config.content) html += '<div style="color:#94a3b8;line-height:1.6">' + config.content + '</div>';
        if (config.buttons) {
            html += '<div style="display:flex;gap:10px;margin-top:20px;justify-content:flex-end">';
            for (const btn of config.buttons) {
                const btnColor = btn.primary ? '#6366f1' : '#334155';
                html += '<button class="ct-modal-btn" data-action="' + btn.action +
                    '" style="padding:8px 20px;border-radius:6px;border:none;cursor:pointer;' +
                    'font-size:14px;color:#f8fafc;background:' + btnColor + '">' + btn.label + '</button>';
            }
            html += '</div>';
        }
        modal.innerHTML = html;
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay && config.closeable !== false) {
                overlay.remove();
            }
        });
        modal.querySelectorAll('.ct-modal-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.getAttribute('data-action');
                if (config.onAction) config.onAction(action);
                overlay.remove();
            });
        });
        this.modals.push(overlay);
        return overlay;
    }

    closeAllModals() {
        for (const modal of this.modals) {
            if (modal.parentNode) modal.remove();
        }
        this.modals = [];
    }

    updateHUD(game) {
        if (!game || !this.hudVisible) return;
        const player = game.getCurrentPlayer();
        if (!player) return;
        const hudEl = document.getElementById('ct-hud');
        if (!hudEl) return;
        const territory = game.territory ? game.territory.getPlayerTerritory(player.id) : { count: 0, avgHealth: 0 };
        hudEl.innerHTML =
            '<div class="ct-hud-section">' +
            '  <span class="ct-hud-label">Turn</span>' +
            '  <span class="ct-hud-value">' + game.turnNumber + '/' + game.maxTurns + '</span>' +
            '</div>' +
            '<div class="ct-hud-section">' +
            '  <span class="ct-hud-label">Player</span>' +
            '  <span class="ct-hud-value" style="color:' + player.color + '">' + player.name + '</span>' +
            '</div>' +
            '<div class="ct-hud-section">' +
            '  <span class="ct-hud-label">Phase</span>' +
            '  <span class="ct-hud-value">' + game.phase + '</span>' +
            '</div>' +
            '<div class="ct-hud-section">' +
            '  <span class="ct-hud-label">Territories</span>' +
            '  <span class="ct-hud-value">' + territory.count + '</span>' +
            '</div>' +
            '<div class="ct-hud-section">' +
            '  <span class="ct-hud-label">Avg Health</span>' +
            '  <span class="ct-hud-value">' + Math.round(territory.avgHealth) + '%</span>' +
            '</div>' +
            '<div class="ct-hud-resources">' +
            '  <span class="ct-resource pigment">Pigment: ' + player.resources.pigment + '</span>' +
            '  <span class="ct-resource inspiration">Inspr: ' + player.resources.inspiration + '</span>' +
            '  <span class="ct-resource energy">Energy: ' + player.resources.energy + '</span>' +
            '</div>';
    }

    updateActionBar(game) {
        const actionBar = document.getElementById('ct-action-bar');
        if (!actionBar) return;
        const player = game.getCurrentPlayer();
        if (!player || player.isAI) {
            actionBar.innerHTML = '<div class="ct-action-info">AI is thinking...</div>';
            return;
        }
        const combat = new window.CT.CombatResolver();
        const attacks = combat.getAvailableAttacks(player);
        let html = '<div class="ct-action-group">';
        html += '<button class="ct-action-btn ct-action-claim" data-action="claim">Claim Territory</button>';
        html += '<button class="ct-action-btn ct-action-art" data-action="create_art">Create Art</button>';
        html += '<button class="ct-action-btn ct-action-fortify" data-action="fortify">Fortify</button>';
        html += '</div>';
        html += '<div class="ct-action-group ct-attacks">';
        for (const atk of attacks.slice(0, 4)) {
            const cls = atk.affordable ? '' : ' disabled';
            html += '<button class="ct-action-btn ct-action-attack' + cls +
                     '" data-action="attack" data-attack="' + atk.name + '"' +
                     (atk.affordable ? '' : ' disabled') + '>' +
                     atk.name.replace(/_/g, ' ') + '</button>';
        }
        html += '</div>';
        html += '<button class="ct-action-btn ct-action-end" data-action="end_turn">End Turn</button>';
        actionBar.innerHTML = html;
    }

    showPaletteSelector(currentPalette, onSelect) {
        const strategies = ['harmonious', 'warm', 'cool', 'monochrome', 'vibrant'];
        const ct = new window.CT.ColorTheory();
        let content = '<div style="display:grid;gap:12px">';
        for (const strategy of strategies) {
            const palette = ct.generatePalette(currentPalette[0] || '#6366f1', 5, strategy);
            content += '<div class="ct-palette-option" data-strategy="' + strategy + '" ' +
                       'style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:8px;' +
                       'border-radius:6px;border:1px solid #334155">';
            content += '<span style="width:100px;color:#94a3b8">' + strategy + '</span>';
            for (const color of palette) {
                content += '<div style="width:30px;height:30px;border-radius:4px;background:' + color + '"></div>';
            }
            content += '</div>';
        }
        content += '</div>';
        const modal = this.showModal({
            title: 'Select Color Palette',
            content: content,
            closeable: true,
            buttons: [{ label: 'Cancel', action: 'cancel' }],
            onAction: () => {}
        });
        modal.querySelectorAll('.ct-palette-option').forEach(el => {
            el.addEventListener('click', () => {
                const strategy = el.getAttribute('data-strategy');
                const base = currentPalette[0] || '#6366f1';
                const palette = ct.generatePalette(base, 5, strategy);
                if (onSelect) onSelect(palette);
                modal.remove();
            });
        });
    }

    showTerritoryInfo(cell, game) {
        if (!cell) return;
        const player = cell.owner !== null ? game.players[cell.owner] : null;
        let content = '<div style="display:grid;gap:8px">';
        content += '<div>Terrain: <strong>' + cell.terrain + '</strong></div>';
        content += '<div>Health: <strong>' + Math.round(cell.health) + '%</strong></div>';
        content += '<div>Art Quality: <strong>' + Math.round((cell.artQuality || 0) * 100) + '%</strong></div>';
        content += '<div>Owner: <strong>' + (player ? player.name : 'Unclaimed') + '</strong></div>';
        content += '<div>Pigment: ' + cell.resources.pigment + ' | Inspiration: ' + cell.resources.inspiration + '</div>';
        content += '<div>Coordinates: (' + cell.q + ', ' + cell.r + ')</div>';
        content += '</div>';
        this.showModal({
            title: 'Territory Info',
            content: content,
            closeable: true,
            buttons: [{ label: 'Close', action: 'close' }],
            onAction: () => {}
        });
    }
}

window.CT.UISystem = UISystem;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: Gallery (~250 lines)
# Snapshots, thumbnails, grid view, export
# ═══════════════════════════════════════════════════════════════════════════════

GALLERY_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class Gallery {
    constructor() {
        this.artworks = [];
        this.maxArtworks = 100;
        this.thumbnailSize = 200;
        this.currentView = 'grid';
        this.sortBy = 'date';
        this.filterBy = 'all';
    }

    captureSnapshot(canvas, metadata) {
        if (!canvas) return null;
        const thumbCanvas = document.createElement('canvas');
        thumbCanvas.width = this.thumbnailSize;
        thumbCanvas.height = this.thumbnailSize;
        const ctx = thumbCanvas.getContext('2d');
        ctx.drawImage(canvas, 0, 0, canvas.width, canvas.height,
                      0, 0, this.thumbnailSize, this.thumbnailSize);
        const artwork = {
            id: 'art_' + Date.now() + '_' + Math.floor(Math.random() * 1000),
            fullImage: canvas.toDataURL('image/png'),
            thumbnail: thumbCanvas.toDataURL('image/jpeg', 0.7),
            title: metadata.title || 'Untitled',
            artist: metadata.artist || 'Unknown',
            date: new Date().toISOString(),
            turn: metadata.turn || 0,
            score: metadata.score || 0,
            palette: metadata.palette || [],
            style: metadata.style || 'unknown',
            width: canvas.width,
            height: canvas.height,
            tags: metadata.tags || []
        };
        this.artworks.unshift(artwork);
        if (this.artworks.length > this.maxArtworks) {
            this.artworks = this.artworks.slice(0, this.maxArtworks);
        }
        return artwork;
    }

    removeArtwork(id) {
        this.artworks = this.artworks.filter(a => a.id !== id);
    }

    getArtworks(options) {
        options = options || {};
        let result = this.artworks.slice();
        if (options.filter && options.filter !== 'all') {
            result = result.filter(a => a.style === options.filter);
        }
        if (options.sort === 'score') {
            result.sort((a, b) => b.score - a.score);
        } else if (options.sort === 'title') {
            result.sort((a, b) => a.title.localeCompare(b.title));
        } else {
            result.sort((a, b) => new Date(b.date) - new Date(a.date));
        }
        if (options.limit) result = result.slice(0, options.limit);
        return result;
    }

    renderGalleryView(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const artworks = this.getArtworks({ sort: this.sortBy, filter: this.filterBy });
        let html = '<div class="ct-gallery-controls">';
        html += '<select class="ct-gallery-sort" onchange="window.CT.gallery.sortBy=this.value;window.CT.gallery.renderGalleryView(\\'ct-gallery\\')">';
        html += '<option value="date"' + (this.sortBy === 'date' ? ' selected' : '') + '>Newest</option>';
        html += '<option value="score"' + (this.sortBy === 'score' ? ' selected' : '') + '>Best Score</option>';
        html += '<option value="title"' + (this.sortBy === 'title' ? ' selected' : '') + '>Title</option>';
        html += '</select>';
        html += '<span class="ct-gallery-count">' + artworks.length + ' artworks</span>';
        html += '</div>';
        if (this.currentView === 'grid') {
            html += '<div class="ct-gallery-grid">';
            for (const art of artworks) {
                html += '<div class="ct-gallery-item" data-id="' + art.id + '">';
                html += '<img src="' + art.thumbnail + '" alt="' + art.title + '" class="ct-gallery-thumb">';
                html += '<div class="ct-gallery-item-info">';
                html += '<span class="ct-gallery-item-title">' + art.title + '</span>';
                html += '<span class="ct-gallery-item-score">Score: ' + art.score + '</span>';
                html += '</div>';
                html += '<div class="ct-gallery-item-actions">';
                html += '<button class="ct-btn-sm" onclick="window.CT.gallery.exportArtwork(\'' + art.id + '\')">Export</button>';
                html += '<button class="ct-btn-sm ct-btn-danger" onclick="window.CT.gallery.removeAndRender(\'' + art.id + '\')">Delete</button>';
                html += '</div>';
                html += '</div>';
            }
            html += '</div>';
        } else {
            html += '<div class="ct-gallery-list">';
            for (const art of artworks) {
                html += '<div class="ct-gallery-list-item">';
                html += '<img src="' + art.thumbnail + '" class="ct-gallery-list-thumb">';
                html += '<div class="ct-gallery-list-info">';
                html += '<h4>' + art.title + '</h4>';
                html += '<p>Score: ' + art.score + ' | Style: ' + art.style + ' | Turn: ' + art.turn + '</p>';
                html += '</div></div>';
            }
            html += '</div>';
        }
        if (artworks.length === 0) {
            html += '<div class="ct-gallery-empty">No artworks yet. Play the game to create art!</div>';
        }
        container.innerHTML = html;
    }

    removeAndRender(id) {
        this.removeArtwork(id);
        this.renderGalleryView('ct-gallery');
    }

    exportArtwork(id) {
        const artwork = this.artworks.find(a => a.id === id);
        if (!artwork) return;
        const link = document.createElement('a');
        link.download = artwork.title.replace(/[^a-z0-9]/gi, '_') + '.png';
        link.href = artwork.fullImage;
        link.click();
    }

    exportAll() {
        for (const art of this.artworks) {
            this.exportArtwork(art.id);
        }
    }

    serialize() {
        return this.artworks.map(a => ({
            id: a.id, title: a.title, artist: a.artist, date: a.date,
            turn: a.turn, score: a.score, style: a.style, tags: a.tags,
            thumbnail: a.thumbnail, palette: a.palette
        }));
    }

    deserialize(data) {
        this.artworks = data || [];
    }

    getStats() {
        return {
            total: this.artworks.length,
            avgScore: this.artworks.length > 0 ?
                Math.round(this.artworks.reduce((s, a) => s + a.score, 0) / this.artworks.length) : 0,
            styles: [...new Set(this.artworks.map(a => a.style))],
            bestScore: this.artworks.length > 0 ?
                Math.max(...this.artworks.map(a => a.score)) : 0
        };
    }
}

window.CT.Gallery = Gallery;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: TutorialSystem (~250 lines)
# Steps, highlights, progress tracking
# ═══════════════════════════════════════════════════════════════════════════════

TUTORIAL_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class TutorialSystem {
    constructor() {
        this.steps = [
            { id: 'welcome', title: 'Welcome to Chromatic Territories', message: 'A game where art and strategy merge. Your palette is your power, composition is your shield, and generative art is your weapon.', highlight: null, position: 'center' },
            { id: 'hex_grid', title: 'The Hex Grid', message: 'The game takes place on a hexagonal grid. Each hex can be claimed, decorated with generative art, and fought over.', highlight: '#ct-main-canvas', position: 'right' },
            { id: 'territory', title: 'Claiming Territory', message: 'Click an unclaimed hex adjacent to your territory to claim it. Expansion costs pigment and energy.', highlight: '.ct-action-claim', position: 'bottom' },
            { id: 'colors', title: 'Color as Resource', message: 'Your color palette defines your strategy. Complementary colors create strong borders, while analogous colors risk merging.', highlight: '.ct-hud-resources', position: 'bottom' },
            { id: 'art', title: 'Creating Art', message: 'Apply generative art (noise, fractals, L-systems) to your territories. Higher art quality means better defense and growth.', highlight: '.ct-action-art', position: 'bottom' },
            { id: 'combat', title: 'Art as Weapon', message: 'Attack enemy territories using artistic techniques. The quality and type of your art determines combat power.', highlight: '.ct-attacks', position: 'top' },
            { id: 'composition', title: 'Composition Score', message: 'Territory health depends on aesthetic composition: balance, rule of thirds, golden ratio, and color harmony.', highlight: '#ct-hud', position: 'right' },
            { id: 'growth', title: 'Evolution', message: 'Territories grow via cellular automata. Healthy compositions thrive and expand; poor ones decay and shrink.', highlight: null, position: 'center' },
            { id: 'audio', title: 'Generative Audio', message: 'The soundtrack is generated from game state. Colors become chords, tension becomes dissonance, and territory patterns become melody.', highlight: null, position: 'center' },
            { id: 'winning', title: 'Winning', message: 'Win by controlling the most territory with the highest composition scores. Balance strategy with artistic vision!', highlight: null, position: 'center' }
        ];
        this.currentStep = 0;
        this.active = false;
        this.completed = false;
        this.overlayEl = null;
        this.stepEl = null;
        this.highlightEl = null;
        this.progress = {};
    }

    start() {
        this.currentStep = 0;
        this.active = true;
        this.completed = false;
        this._createOverlay();
        this._showStep();
    }

    _createOverlay() {
        if (this.overlayEl) this.overlayEl.remove();
        this.overlayEl = document.createElement('div');
        this.overlayEl.className = 'ct-tutorial-overlay';
        this.overlayEl.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;' +
            'z-index:9500;pointer-events:none';
        this.stepEl = document.createElement('div');
        this.stepEl.className = 'ct-tutorial-step';
        this.stepEl.style.cssText = 'position:fixed;z-index:9600;background:#1e293b;' +
            'border:2px solid #6366f1;border-radius:12px;padding:20px;max-width:400px;' +
            'color:#f8fafc;pointer-events:auto;box-shadow:0 20px 60px rgba(0,0,0,0.5)';
        this.highlightEl = document.createElement('div');
        this.highlightEl.className = 'ct-tutorial-highlight';
        this.highlightEl.style.cssText = 'position:fixed;z-index:9550;' +
            'border:3px solid #fbbf24;border-radius:8px;pointer-events:none;' +
            'box-shadow:0 0 0 9999px rgba(0,0,0,0.5);animation:ct-pulse 2s infinite';
        this.overlayEl.appendChild(this.highlightEl);
        this.overlayEl.appendChild(this.stepEl);
        document.body.appendChild(this.overlayEl);
    }

    _showStep() {
        if (this.currentStep >= this.steps.length) {
            this.complete();
            return;
        }
        const step = this.steps[this.currentStep];
        this.progress[step.id] = true;
        let html = '<h3 style="margin:0 0 10px;color:#818cf8">' + step.title + '</h3>';
        html += '<p style="margin:0 0 16px;line-height:1.6;color:#94a3b8">' + step.message + '</p>';
        html += '<div style="display:flex;gap:8px;justify-content:space-between;align-items:center">';
        html += '<span style="color:#64748b;font-size:12px">Step ' + (this.currentStep + 1) + ' of ' + this.steps.length + '</span>';
        html += '<div style="display:flex;gap:8px">';
        if (this.currentStep > 0) {
            html += '<button class="ct-tutorial-btn" onclick="window.CT.tutorial.prev()" style="padding:6px 16px;border:1px solid #334155;background:transparent;color:#94a3b8;border-radius:6px;cursor:pointer">Back</button>';
        }
        html += '<button class="ct-tutorial-btn" onclick="window.CT.tutorial.skip()" style="padding:6px 16px;border:1px solid #334155;background:transparent;color:#64748b;border-radius:6px;cursor:pointer">Skip</button>';
        html += '<button class="ct-tutorial-btn" onclick="window.CT.tutorial.next()" style="padding:6px 16px;border:none;background:#6366f1;color:#f8fafc;border-radius:6px;cursor:pointer">' +
                (this.currentStep === this.steps.length - 1 ? 'Finish' : 'Next') + '</button>';
        html += '</div></div>';
        this.stepEl.innerHTML = html;
        if (step.highlight) {
            const el = document.querySelector(step.highlight);
            if (el) {
                const rect = el.getBoundingClientRect();
                this.highlightEl.style.display = 'block';
                this.highlightEl.style.top = (rect.top - 4) + 'px';
                this.highlightEl.style.left = (rect.left - 4) + 'px';
                this.highlightEl.style.width = (rect.width + 8) + 'px';
                this.highlightEl.style.height = (rect.height + 8) + 'px';
                this._positionStep(step.position, rect);
            } else {
                this.highlightEl.style.display = 'none';
                this._positionStepCenter();
            }
        } else {
            this.highlightEl.style.display = 'none';
            this._positionStepCenter();
        }
    }

    _positionStep(position, targetRect) {
        const sw = 400;
        const sh = 200;
        switch (position) {
            case 'right':
                this.stepEl.style.left = (targetRect.right + 20) + 'px';
                this.stepEl.style.top = (targetRect.top) + 'px';
                break;
            case 'bottom':
                this.stepEl.style.left = (targetRect.left) + 'px';
                this.stepEl.style.top = (targetRect.bottom + 20) + 'px';
                break;
            case 'top':
                this.stepEl.style.left = (targetRect.left) + 'px';
                this.stepEl.style.top = (targetRect.top - sh - 20) + 'px';
                break;
            default:
                this._positionStepCenter();
        }
    }

    _positionStepCenter() {
        this.stepEl.style.left = '50%';
        this.stepEl.style.top = '50%';
        this.stepEl.style.transform = 'translate(-50%, -50%)';
    }

    next() { this.currentStep++; this._showStep(); }
    prev() { if (this.currentStep > 0) { this.currentStep--; this._showStep(); } }
    skip() { this.complete(); }

    complete() {
        this.active = false;
        this.completed = true;
        if (this.overlayEl) {
            this.overlayEl.remove();
            this.overlayEl = null;
        }
    }

    isComplete() { return this.completed; }
    getProgress() { return Object.keys(this.progress).length / this.steps.length; }

    serialize() {
        return { completed: this.completed, progress: this.progress, currentStep: this.currentStep };
    }

    deserialize(data) {
        if (data) {
            this.completed = data.completed || false;
            this.progress = data.progress || {};
            this.currentStep = data.currentStep || 0;
        }
    }
}

window.CT.TutorialSystem = TutorialSystem;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: AudioSynthesizer (~350 lines)
# Web Audio oscillators, ADSR, filters, scales
# ═══════════════════════════════════════════════════════════════════════════════

AUDIO_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class AudioSynthesizer {
    constructor() {
        this.ctx = null;
        this.masterGain = null;
        this.compressor = null;
        this.reverbNode = null;
        this.volume = 0.3;
        this.initialized = false;
        this.activeOscillators = [];
        this.scales = {
            major: [0, 2, 4, 5, 7, 9, 11],
            minor: [0, 2, 3, 5, 7, 8, 10],
            pentatonic: [0, 2, 4, 7, 9],
            blues: [0, 3, 5, 6, 7, 10],
            dorian: [0, 2, 3, 5, 7, 9, 10],
            mixolydian: [0, 2, 4, 5, 7, 9, 10],
            phrygian: [0, 1, 3, 5, 7, 8, 10],
            wholetone: [0, 2, 4, 6, 8, 10],
            chromatic: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        };
        this.noteFrequencies = {};
        this._buildFrequencyTable();
    }

    _buildFrequencyTable() {
        const notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
        for (let octave = 0; octave <= 8; octave++) {
            for (let i = 0; i < 12; i++) {
                const note = notes[i] + octave;
                const midi = octave * 12 + i;
                this.noteFrequencies[note] = 440 * Math.pow(2, (midi - 69) / 12);
            }
        }
    }

    init() {
        if (this.initialized) return;
        try {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();
            this.compressor = this.ctx.createDynamicsCompressor();
            this.compressor.threshold.value = -24;
            this.compressor.knee.value = 30;
            this.compressor.ratio.value = 12;
            this.compressor.attack.value = 0.003;
            this.compressor.release.value = 0.25;
            this.masterGain = this.ctx.createGain();
            this.masterGain.gain.value = this.volume;
            this.masterGain.connect(this.compressor);
            this.compressor.connect(this.ctx.destination);
            this._createReverb();
            this.initialized = true;
        } catch(e) {
            console.warn('Web Audio not available:', e);
        }
    }

    _createReverb() {
        const sampleRate = this.ctx.sampleRate;
        const length = sampleRate * 2;
        const impulse = this.ctx.createBuffer(2, length, sampleRate);
        for (let channel = 0; channel < 2; channel++) {
            const data = impulse.getChannelData(channel);
            for (let i = 0; i < length; i++) {
                data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 2);
            }
        }
        this.reverbNode = this.ctx.createConvolver();
        this.reverbNode.buffer = impulse;
    }

    createOscillator(type, frequency) {
        if (!this.ctx) return null;
        const osc = this.ctx.createOscillator();
        osc.type = type || 'sine';
        osc.frequency.value = frequency || 440;
        return osc;
    }

    createADSR(attack, decay, sustain, release) {
        return {
            attack: attack || 0.05,
            decay: decay || 0.1,
            sustain: sustain || 0.7,
            release: release || 0.3
        };
    }

    applyADSR(gainNode, adsr, startTime, duration) {
        const a = adsr.attack;
        const d = adsr.decay;
        const s = adsr.sustain;
        const r = adsr.release;
        gainNode.gain.setValueAtTime(0, startTime);
        gainNode.gain.linearRampToValueAtTime(1, startTime + a);
        gainNode.gain.linearRampToValueAtTime(s, startTime + a + d);
        gainNode.gain.setValueAtTime(s, startTime + duration - r);
        gainNode.gain.linearRampToValueAtTime(0, startTime + duration);
    }

    createFilter(type, frequency, Q) {
        if (!this.ctx) return null;
        const filter = this.ctx.createBiquadFilter();
        filter.type = type || 'lowpass';
        filter.frequency.value = frequency || 1000;
        filter.Q.value = Q || 1;
        return filter;
    }

    playNote(frequency, duration, type, adsr, filterFreq) {
        if (!this.ctx || !this.initialized) return;
        const osc = this.createOscillator(type || 'sine', frequency);
        const gainNode = this.ctx.createGain();
        const envelope = adsr || this.createADSR();
        const startTime = this.ctx.currentTime;
        duration = duration || 0.5;
        this.applyADSR(gainNode, envelope, startTime, duration);
        osc.connect(gainNode);
        if (filterFreq) {
            const filter = this.createFilter('lowpass', filterFreq, 2);
            gainNode.connect(filter);
            filter.connect(this.masterGain);
        } else {
            gainNode.connect(this.masterGain);
        }
        osc.start(startTime);
        osc.stop(startTime + duration + 0.1);
        this.activeOscillators.push(osc);
        osc.onended = () => {
            const idx = this.activeOscillators.indexOf(osc);
            if (idx >= 0) this.activeOscillators.splice(idx, 1);
        };
    }

    playChord(frequencies, duration, type, adsr) {
        for (const freq of frequencies) {
            this.playNote(freq, duration, type, adsr);
        }
    }

    noteToFrequency(note, octave) {
        const key = note + octave;
        return this.noteFrequencies[key] || 440;
    }

    getScaleFrequencies(rootNote, octave, scaleName, count) {
        count = count || 8;
        const scale = this.scales[scaleName] || this.scales.major;
        const rootFreq = this.noteToFrequency(rootNote, octave);
        const frequencies = [];
        let scaleIdx = 0;
        let oct = 0;
        for (let i = 0; i < count; i++) {
            const semitones = scale[scaleIdx % scale.length] + oct * 12;
            frequencies.push(rootFreq * Math.pow(2, semitones / 12));
            scaleIdx++;
            if (scaleIdx >= scale.length) {
                scaleIdx = 0;
                oct++;
            }
        }
        return frequencies;
    }

    colorToFrequency(hexColor) {
        const ct = new window.CT.ColorTheory();
        const rgb = ct.hexToRgb(hexColor);
        const hsl = ct.rgbToHsl(rgb.r, rgb.g, rgb.b);
        const baseFreq = 220;
        const freq = baseFreq * Math.pow(2, hsl.h / 360 * 2);
        return freq;
    }

    colorToChord(hexColor, type) {
        const root = this.colorToFrequency(hexColor);
        type = type || 'major';
        switch (type) {
            case 'major': return [root, root * 5/4, root * 3/2];
            case 'minor': return [root, root * 6/5, root * 3/2];
            case 'diminished': return [root, root * 6/5, root * 7/5];
            case 'augmented': return [root, root * 5/4, root * 8/5];
            case 'sus4': return [root, root * 4/3, root * 3/2];
            case 'seventh': return [root, root * 5/4, root * 3/2, root * 9/5];
            default: return [root, root * 5/4, root * 3/2];
        }
    }

    setVolume(vol) {
        this.volume = Math.max(0, Math.min(1, vol));
        if (this.masterGain) this.masterGain.gain.value = this.volume;
    }

    stopAll() {
        for (const osc of this.activeOscillators) {
            try { osc.stop(); } catch(e) {}
        }
        this.activeOscillators = [];
    }

    resume() {
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    }

    suspend() {
        if (this.ctx && this.ctx.state === 'running') {
            this.ctx.suspend();
        }
    }
}

window.CT.AudioSynthesizer = AudioSynthesizer;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: GenerativeMusic (~300 lines)
# Game-state-to-music mapping, ambient, melody, rhythm
# ═══════════════════════════════════════════════════════════════════════════════

GENERATIVE_MUSIC_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class GenerativeMusic {
    constructor(synth) {
        this.synth = synth;
        this.playing = false;
        this.tempo = 120;
        this.currentScale = 'pentatonic';
        this.rootNote = 'C';
        this.octave = 4;
        this.tension = 0.3;
        this.ambientInterval = null;
        this.melodyInterval = null;
        this.rhythmInterval = null;
        this.beatIndex = 0;
        this.melodyState = { noteIndex: 0, direction: 1, restProbability: 0.2 };
        this.transitionMatrix = [
            [0.2, 0.3, 0.1, 0.2, 0.1, 0.05, 0.05],
            [0.1, 0.2, 0.3, 0.1, 0.15, 0.1, 0.05],
            [0.15, 0.1, 0.2, 0.25, 0.1, 0.1, 0.1],
            [0.1, 0.15, 0.15, 0.2, 0.2, 0.1, 0.1],
            [0.2, 0.1, 0.1, 0.15, 0.15, 0.2, 0.1],
            [0.1, 0.2, 0.15, 0.1, 0.15, 0.15, 0.15],
            [0.25, 0.1, 0.1, 0.15, 0.1, 0.15, 0.15]
        ];
    }

    updateFromGameState(game) {
        if (!game) return;
        const state = game.getState();
        if (!state.currentPlayer) return;
        const player = state.currentPlayer;
        const ct = new window.CT.ColorTheory();
        const hsl = ct.rgbToHsl(...Object.values(ct.hexToRgb(player.color)));
        this.rootNote = this._hueToNote(hsl.h);
        this.currentScale = this._determineScale(game);
        this.tension = this._calculateTension(game);
        this.tempo = 80 + Math.round(this.tension * 80);
    }

    _hueToNote(hue) {
        const notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
        const idx = Math.floor((hue / 360) * 12) % 12;
        return notes[idx];
    }

    _determineScale(game) {
        if (this.tension > 0.7) return 'phrygian';
        if (this.tension > 0.5) return 'minor';
        if (this.tension > 0.3) return 'dorian';
        return 'pentatonic';
    }

    _calculateTension(game) {
        const state = game.getState();
        let tension = 0;
        if (state.territoryStats) {
            const claimed = state.territoryStats.claimed / Math.max(1, state.territoryStats.total);
            tension += claimed * 0.3;
        }
        const turnProgress = state.turnNumber / (game.maxTurns || 50);
        tension += turnProgress * 0.3;
        if (state.phase === 'resolve') tension += 0.2;
        if (game.state === 'gameover') tension += 0.2;
        return Math.min(1.0, tension);
    }

    start() {
        if (this.playing || !this.synth || !this.synth.initialized) return;
        this.playing = true;
        this._startAmbient();
        this._startMelody();
        this._startRhythm();
    }

    stop() {
        this.playing = false;
        if (this.ambientInterval) clearInterval(this.ambientInterval);
        if (this.melodyInterval) clearInterval(this.melodyInterval);
        if (this.rhythmInterval) clearInterval(this.rhythmInterval);
        this.ambientInterval = null;
        this.melodyInterval = null;
        this.rhythmInterval = null;
        if (this.synth) this.synth.stopAll();
    }

    _startAmbient() {
        const playAmbient = () => {
            if (!this.playing) return;
            const frequencies = this.synth.getScaleFrequencies(
                this.rootNote, this.octave - 1, this.currentScale, 3
            );
            const adsr = this.synth.createADSR(0.5, 0.3, 0.4, 1.0);
            for (const freq of frequencies) {
                this.synth.playNote(freq, 4, 'sine', adsr, 600);
            }
        };
        playAmbient();
        this.ambientInterval = setInterval(playAmbient, 4000);
    }

    _startMelody() {
        const playMelody = () => {
            if (!this.playing) return;
            if (Math.random() < this.melodyState.restProbability) return;
            const scale = this.synth.getScaleFrequencies(
                this.rootNote, this.octave, this.currentScale, 14
            );
            const nextNote = this._markovNextNote(scale.length);
            this.melodyState.noteIndex = nextNote;
            const freq = scale[nextNote];
            const duration = (60 / this.tempo) * (Math.random() < 0.3 ? 2 : 1);
            const waveType = this.tension > 0.5 ? 'sawtooth' : 'triangle';
            const adsr = this.synth.createADSR(0.02, 0.1, 0.5, 0.2);
            const filterFreq = 800 + this.tension * 2000;
            this.synth.playNote(freq, duration, waveType, adsr, filterFreq);
        };
        this.melodyInterval = setInterval(playMelody, (60 / this.tempo) * 1000);
    }

    _startRhythm() {
        const playRhythm = () => {
            if (!this.playing) return;
            this.beatIndex = (this.beatIndex + 1) % 16;
            if (this.beatIndex % 4 === 0) {
                this.synth.playNote(80, 0.1, 'sine', this.synth.createADSR(0.01, 0.05, 0.3, 0.05));
            }
            if (this.beatIndex % 8 === 4 && this.tension > 0.3) {
                this.synth.playNote(200, 0.05, 'square', this.synth.createADSR(0.001, 0.02, 0.1, 0.02));
            }
            if (this.tension > 0.5 && this.beatIndex % 2 === 0) {
                const freq = 300 + Math.random() * 100;
                this.synth.playNote(freq, 0.03, 'square', this.synth.createADSR(0.001, 0.01, 0.05, 0.01));
            }
        };
        const beatDuration = (60 / this.tempo / 4) * 1000;
        this.rhythmInterval = setInterval(playRhythm, beatDuration);
    }

    _markovNextNote(scaleLength) {
        const current = Math.min(this.melodyState.noteIndex, this.transitionMatrix.length - 1);
        const row = this.transitionMatrix[current];
        let r = Math.random();
        for (let i = 0; i < row.length; i++) {
            r -= row[i];
            if (r <= 0) return Math.min(i, scaleLength - 1);
        }
        return 0;
    }

    playTerritoryClaimSound(color) {
        if (!this.synth || !this.synth.initialized) return;
        const chord = this.synth.colorToChord(color, 'major');
        const adsr = this.synth.createADSR(0.01, 0.1, 0.6, 0.3);
        this.synth.playChord(chord, 0.4, 'triangle', adsr);
    }

    playCombatSound(success) {
        if (!this.synth || !this.synth.initialized) return;
        if (success) {
            const freqs = [523, 659, 784];
            freqs.forEach((f, i) => {
                setTimeout(() => this.synth.playNote(f, 0.2, 'square'), i * 80);
            });
        } else {
            this.synth.playNote(200, 0.5, 'sawtooth',
                this.synth.createADSR(0.01, 0.2, 0.3, 0.3), 400);
        }
    }

    playAchievementSound() {
        if (!this.synth || !this.synth.initialized) return;
        const notes = [523, 659, 784, 1047];
        notes.forEach((f, i) => {
            setTimeout(() => this.synth.playNote(f, 0.3, 'sine',
                this.synth.createADSR(0.01, 0.05, 0.8, 0.3)), i * 120);
        });
    }
}

window.CT.GenerativeMusic = GenerativeMusic;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: DataLayer (~150 lines)
# localStorage persistence for all game data
# ═══════════════════════════════════════════════════════════════════════════════

DATA_LAYER_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class DataLayer {
    constructor(namespace) {
        this.namespace = namespace || 'chromatic_territories';
        this.autoSaveInterval = null;
        this.dirty = false;
    }

    _key(name) {
        return this.namespace + ':' + name;
    }

    save(key, data) {
        try {
            const serialized = JSON.stringify(data);
            localStorage.setItem(this._key(key), serialized);
            return true;
        } catch(e) {
            console.warn('DataLayer save failed for', key, e);
            return false;
        }
    }

    load(key, defaultValue) {
        try {
            const raw = localStorage.getItem(this._key(key));
            if (raw === null) return defaultValue !== undefined ? defaultValue : null;
            return JSON.parse(raw);
        } catch(e) {
            console.warn('DataLayer load failed for', key, e);
            return defaultValue !== undefined ? defaultValue : null;
        }
    }

    remove(key) {
        localStorage.removeItem(this._key(key));
    }

    clear() {
        const prefix = this.namespace + ':';
        const keys = [];
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (k && k.startsWith(prefix)) keys.push(k);
        }
        for (const k of keys) localStorage.removeItem(k);
    }

    saveGameState(game) {
        if (!game) return false;
        return this.save('game_state', game.serialize());
    }

    loadGameState() {
        return this.load('game_state', null);
    }

    saveSettings(settings) {
        return this.save('settings', settings);
    }

    loadSettings() {
        return this.load('settings', {
            volume: 0.3,
            musicEnabled: true,
            sfxEnabled: true,
            showGrid: true,
            showMinimap: true,
            autoSave: true,
            difficulty: 'medium',
            gridRadius: 8,
            maxTurns: 50
        });
    }

    saveGallery(gallery) {
        return this.save('gallery', gallery.serialize());
    }

    loadGallery(gallery) {
        const data = this.load('gallery', null);
        if (data) gallery.deserialize(data);
    }

    saveScoring(scoring) {
        return this.save('scoring', scoring.serialize());
    }

    loadScoring(scoring) {
        const data = this.load('scoring', null);
        if (data) scoring.deserialize(data);
    }

    saveTutorial(tutorial) {
        return this.save('tutorial', tutorial.serialize());
    }

    loadTutorial(tutorial) {
        const data = this.load('tutorial', null);
        if (data) tutorial.deserialize(data);
    }

    startAutoSave(game, intervalMs) {
        intervalMs = intervalMs || 30000;
        this.stopAutoSave();
        this.autoSaveInterval = setInterval(() => {
            if (game.state === 'playing') {
                this.saveGameState(game);
                this.dirty = false;
            }
        }, intervalMs);
    }

    stopAutoSave() {
        if (this.autoSaveInterval) {
            clearInterval(this.autoSaveInterval);
            this.autoSaveInterval = null;
        }
    }

    markDirty() { this.dirty = true; }

    getStorageUsage() {
        let total = 0;
        const prefix = this.namespace + ':';
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (k && k.startsWith(prefix)) {
                total += (localStorage.getItem(k) || '').length;
            }
        }
        return { bytes: total * 2, readable: (total * 2 / 1024).toFixed(1) + ' KB' };
    }

    hasExistingGame() {
        return this.load('game_state', null) !== null;
    }
}

window.CT.DataLayer = DataLayer;
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# JS MODULE: App (~200 lines)
# Initialization, SPA router, keyboard shortcuts
# ═══════════════════════════════════════════════════════════════════════════════

APP_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class App {
    constructor() {
        this.game = null;
        this.renderer = null;
        this.particles = null;
        this.ui = null;
        this.gallery = null;
        this.tutorial = null;
        this.scoring = null;
        this.synth = null;
        this.music = null;
        this.data = null;
        this.ai = [];
        this.combat = null;
        this.compositor = null;
        this.currentRoute = '/';
        this.lastFrameTime = 0;
        this.running = false;
        this.fpsCounter = { frames: 0, lastTime: 0, fps: 0 };
    }

    init() {
        console.log('Initializing Chromatic Territories...');
        this.data = new window.CT.DataLayer('ct');
        this.game = new window.CT.GameEngine();
        this.particles = new window.CT.ParticleSystem(2000);
        this.ui = new window.CT.UISystem();
        this.gallery = new window.CT.Gallery();
        this.tutorial = new window.CT.TutorialSystem();
        this.scoring = new window.CT.ScoringSystem();
        this.synth = new window.CT.AudioSynthesizer();
        this.music = new window.CT.GenerativeMusic(this.synth);
        this.combat = new window.CT.CombatResolver();
        this.compositor = new window.CT.CompositionAnalyzer();
        this.particles.setGravity(0, 0);
        this.ui.init();
        this.data.loadGallery(this.gallery);
        this.data.loadScoring(this.scoring);
        this.data.loadTutorial(this.tutorial);
        this._initRouter();
        this._initKeyboardShortcuts();
        this._initCanvas();
        this._bindGameEvents();
        const settings = this.data.loadSettings();
        this.synth.setVolume(settings.volume);
        this.navigate(window.location.hash.slice(1) || '/');
        console.log('Chromatic Territories initialized!');
    }

    _initCanvas() {
        const canvas = document.getElementById('ct-main-canvas');
        if (!canvas) return;
        this.renderer = new window.CT.CanvasRenderer('ct-main-canvas');
        canvas.addEventListener('mousemove', (e) => {
            if (this.game.territory) this.renderer.handleMouseMove(e, this.game.territory);
        });
        canvas.addEventListener('click', (e) => {
            if (this.game.territory) {
                const hex = this.renderer.handleClick(e, this.game.territory);
                if (hex) this._handleCellClick(hex);
            }
        });
        canvas.addEventListener('wheel', (e) => this.renderer.handleWheel(e), { passive: false });
        canvas.addEventListener('mousedown', (e) => {
            if (e.button === 1 || e.button === 2) {
                this.renderer.camera.startDrag(e.clientX, e.clientY);
            }
        });
        canvas.addEventListener('mouseup', () => this.renderer.camera.endDrag());
        canvas.addEventListener('mouseleave', () => this.renderer.camera.endDrag());
        window.addEventListener('resize', () => {
            const container = canvas.parentElement;
            if (container) {
                this.renderer.resize(container.clientWidth, Math.max(500, container.clientHeight));
            }
        });
    }

    _handleCellClick(hex) {
        const cell = this.game.territory.getCell(hex.q, hex.r);
        if (!cell) return;
        const player = this.game.getCurrentPlayer();
        if (!player || player.isAI) return;
        if (cell.owner === null && this.game.phase === 'select') {
            const success = this.game.territory.claimTerritory(hex.q, hex.r, player.id, player.color);
            if (success) {
                player.stats.territoriesClaimed++;
                this.particles.burst('territory_claim', 0, 0, 30);
                this.music.playTerritoryClaimSound(player.color);
                this.ui.showToast('Territory claimed!', 'success');
                this.data.markDirty();
            }
        } else if (cell.owner === player.id) {
            this.ui.showTerritoryInfo(cell, this.game);
        }
    }

    _initRouter() {
        window.addEventListener('hashchange', () => {
            this.navigate(window.location.hash.slice(1) || '/');
        });
    }

    navigate(route) {
        this.currentRoute = route;
        const views = document.querySelectorAll('.ct-view');
        views.forEach(v => v.style.display = 'none');
        switch (route) {
            case '/':
            case '/play':
                this._showView('ct-game-view');
                break;
            case '/gallery':
                this._showView('ct-gallery-view');
                this.gallery.renderGalleryView('ct-gallery');
                break;
            case '/tutorial':
                this._showView('ct-game-view');
                if (!this.tutorial.isComplete()) this.tutorial.start();
                break;
            case '/settings':
                this._showSettings();
                break;
            case '/leaderboard':
                this._showLeaderboard();
                break;
            case '/achievements':
                this._showAchievements();
                break;
            case '/about':
                this._showAbout();
                break;
            default:
                this._showView('ct-game-view');
        }
    }

    _showView(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'block';
    }

    _showSettings() {
        const settings = this.data.loadSettings();
        this.ui.showModal({
            title: 'Settings',
            content: '<div style="display:grid;gap:12px">' +
                '<label style="color:#94a3b8">Volume: <input type="range" min="0" max="100" value="' + Math.round(settings.volume * 100) + '" id="ct-vol"></label>' +
                '<label style="color:#94a3b8"><input type="checkbox" ' + (settings.showGrid ? 'checked' : '') + ' id="ct-grid"> Show Grid</label>' +
                '<label style="color:#94a3b8"><input type="checkbox" ' + (settings.showMinimap ? 'checked' : '') + ' id="ct-mini"> Show Minimap</label>' +
                '<label style="color:#94a3b8"><input type="checkbox" ' + (settings.musicEnabled ? 'checked' : '') + ' id="ct-music"> Music</label>' +
                '</div>',
            buttons: [{ label: 'Save', action: 'save', primary: true }, { label: 'Cancel', action: 'cancel' }],
            onAction: (action) => {
                if (action === 'save') {
                    const vol = document.getElementById('ct-vol');
                    if (vol) settings.volume = parseInt(vol.value) / 100;
                    const grid = document.getElementById('ct-grid');
                    if (grid) settings.showGrid = grid.checked;
                    const mini = document.getElementById('ct-mini');
                    if (mini) settings.showMinimap = mini.checked;
                    this.data.saveSettings(settings);
                    if (this.renderer) {
                        this.renderer.showGrid = settings.showGrid;
                        this.renderer.showMinimap = settings.showMinimap;
                    }
                    this.synth.setVolume(settings.volume);
                    this.ui.showToast('Settings saved', 'success');
                }
            }
        });
    }

    _showLeaderboard() {
        const entries = this.scoring.getLeaderboard();
        let content = '<table style="width:100%;color:#94a3b8;border-collapse:collapse">';
        content += '<tr><th style="text-align:left;padding:8px;border-bottom:1px solid #334155">#</th><th style="text-align:left;padding:8px;border-bottom:1px solid #334155">Player</th><th style="text-align:right;padding:8px;border-bottom:1px solid #334155">Score</th></tr>';
        for (const e of entries) {
            content += '<tr><td style="padding:8px">' + e.rank + '</td><td style="padding:8px">' + e.name + '</td><td style="padding:8px;text-align:right">' + e.score + '</td></tr>';
        }
        if (entries.length === 0) content += '<tr><td colspan="3" style="padding:20px;text-align:center;color:#64748b">No entries yet</td></tr>';
        content += '</table>';
        this.ui.showModal({ title: 'Leaderboard', content, closeable: true, buttons: [{ label: 'Close', action: 'close' }], onAction: () => {} });
    }

    _showAchievements() {
        const achievements = this.scoring.getAchievements();
        let content = '<div style="display:grid;gap:8px">';
        for (const a of achievements) {
            const opacity = a.unlocked ? '1' : '0.4';
            const check = a.unlocked ? ' &#10003;' : '';
            content += '<div style="padding:10px;border:1px solid #334155;border-radius:6px;opacity:' + opacity + '">';
            content += '<strong style="color:#f8fafc">' + a.name + check + '</strong><br>';
            content += '<span style="color:#64748b;font-size:13px">' + a.description + '</span></div>';
        }
        content += '</div>';
        this.ui.showModal({ title: 'Achievements (' + this.scoring.getUnlockedCount() + '/' + this.scoring.getTotalCount() + ')', content, closeable: true, buttons: [{ label: 'Close', action: 'close' }], onAction: () => {} });
    }

    _showAbout() {
        this.ui.showModal({
            title: 'About Chromatic Territories',
            content: '<p style="color:#94a3b8;line-height:1.6">Chromatic Territories is a game where art and strategy merge. ' +
                'Your palette is your power. Color theory drives combat. Composition score determines territory health. ' +
                'Generative art techniques serve as weapons and defenses.</p>' +
                '<p style="color:#64748b;margin-top:12px;font-size:13px">Built with jugeo-webapp generation pipeline.</p>',
            closeable: true, buttons: [{ label: 'Close', action: 'close' }], onAction: () => {}
        });
    }

    _bindGameEvents() {
        this.game.events.on('game:start', () => this.ui.showToast('Game started!', 'info'));
        this.game.events.on('turn:start', (data) => {
            this.ui.updateHUD(this.game);
            this.ui.updateActionBar(this.game);
            this.music.updateFromGameState(this.game);
            if (data.player && data.player.isAI) this._processAITurn(data.player);
        });
        this.game.events.on('game:end', (data) => {
            this.ui.showModal({
                title: 'Game Over!',
                content: '<p style="color:#f8fafc;font-size:18px">Winner: <strong style="color:' + data.winner.color + '">' + data.winner.name + '</strong></p>' +
                    '<p style="color:#94a3b8">Score: ' + data.winner.score + '</p>',
                buttons: [{ label: 'New Game', action: 'new', primary: true }, { label: 'Gallery', action: 'gallery' }],
                onAction: (action) => {
                    if (action === 'gallery') this.navigate('/gallery');
                }
            });
            this.scoring.addToLeaderboard({ name: data.winner.name, score: data.winner.score, territories: 0, artScore: 0, turns: this.game.turnNumber });
            this.data.saveScoring(this.scoring);
        });
    }

    _processAITurn(player) {
        const aiOpp = this.ai.find(a => a.playerId === player.id);
        if (!aiOpp) { this.game.advancePhase(); return; }
        aiOpp.adaptStrategy(this.game);
        setTimeout(() => {
            const move = aiOpp.selectMove(this.game);
            if (move.type !== 'pass') {
                this.game.submitAction(move);
            }
            this.game.advancePhase();
        }, 500);
    }

    _initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            switch(e.key) {
                case 'Escape': this.ui.closeAllModals(); break;
                case ' ': if (this.game.state === 'playing') { e.preventDefault(); this.game.advancePhase(); } break;
                case 'g': if (this.renderer) this.renderer.showGrid = !this.renderer.showGrid; break;
                case 'm': if (this.renderer) this.renderer.showMinimap = !this.renderer.showMinimap; break;
                case '+': case '=': if (this.renderer) this.renderer.camera.zoomBy(0.2); break;
                case '-': if (this.renderer) this.renderer.camera.zoomBy(-0.2); break;
                case 'p': if (this.game.paused) this.game.resume(); else this.game.pause(); break;
            }
        });
    }

    startNewGame(config) {
        config = config || {};
        const players = config.players || [
            { name: 'Player 1', color: '#6366f1', isAI: false, palette: ['#6366f1', '#818cf8', '#a5b4fc'], artStyle: 'impressionist' },
            { name: 'AI Monet', color: '#ec4899', isAI: true, palette: ['#ec4899', '#f472b6', '#f9a8d4'], artStyle: 'impressionist' },
            { name: 'AI Kandinsky', color: '#14b8a6', isAI: true, palette: ['#14b8a6', '#2dd4bf', '#5eead4'], artStyle: 'geometric' }
        ];
        this.game.initialize(players);
        this.ai = [];
        for (const p of this.game.players) {
            if (p.isAI) {
                const difficulty = config.difficulty || 'medium';
                this.ai.push(new window.CT.AIOpponent(p.id, difficulty));
            }
        }
        this.synth.init();
        const settings = this.data.loadSettings();
        if (settings.musicEnabled) this.music.start();
        this.data.startAutoSave(this.game);
        this.startGameLoop();
    }

    startGameLoop() {
        if (this.running) return;
        this.running = true;
        this.lastFrameTime = performance.now();
        const loop = (timestamp) => {
            if (!this.running) return;
            const dt = (timestamp - this.lastFrameTime) / 1000;
            this.lastFrameTime = timestamp;
            this.game.update(dt);
            this.particles.update(dt);
            if (this.renderer) {
                this.renderer.render(this.game, this.particles, dt);
            }
            this.ui.updateHUD(this.game);
            this.fpsCounter.frames++;
            if (timestamp - this.fpsCounter.lastTime >= 1000) {
                this.fpsCounter.fps = this.fpsCounter.frames;
                this.fpsCounter.frames = 0;
                this.fpsCounter.lastTime = timestamp;
            }
            requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
    }

    stopGameLoop() { this.running = false; }
}

window.CT.App = App;

document.addEventListener('DOMContentLoaded', () => {
    window.CT.app = new App();
    window.CT.app.init();
    window.CT.gallery = window.CT.app.gallery;
    window.CT.tutorial = window.CT.app.tutorial;
});
})();
"""




# ═══════════════════════════════════════════════════════════════════════════════
# CSS: Complete design system (~1500+ lines)
# ═══════════════════════════════════════════════════════════════════════════════

ALL_CSS = """
/* ═══ CSS Variables / Design Tokens ═══ */
:root {
    --ct-primary: #6366f1;
    --ct-primary-dark: #4f46e5;
    --ct-primary-light: #818cf8;
    --ct-primary-glow: rgba(99, 102, 241, 0.4);
    --ct-secondary: #ec4899;
    --ct-secondary-dark: #db2777;
    --ct-secondary-light: #f472b6;
    --ct-accent: #14b8a6;
    --ct-accent-dark: #0d9488;
    --ct-accent-light: #2dd4bf;
    --ct-bg-dark: #0f172a;
    --ct-bg-medium: #1e293b;
    --ct-bg-light: #334155;
    --ct-bg-card: #1a2332;
    --ct-text-primary: #f8fafc;
    --ct-text-secondary: #94a3b8;
    --ct-text-muted: #64748b;
    --ct-success: #22c55e;
    --ct-warning: #f59e0b;
    --ct-danger: #ef4444;
    --ct-info: #3b82f6;
    --ct-border: rgba(99, 102, 241, 0.15);
    --ct-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
    --ct-shadow-lg: 0 12px 48px rgba(0, 0, 0, 0.4);
    --ct-radius-sm: 4px;
    --ct-radius: 8px;
    --ct-radius-lg: 12px;
    --ct-radius-xl: 16px;
    --ct-font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --ct-font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --ct-transition: 0.2s ease;
    --ct-transition-slow: 0.4s ease;
}

/* ═══ Reset & Base ═══ */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; scroll-behavior: smooth; }
body {
    font-family: var(--ct-font);
    background: var(--ct-bg-dark);
    color: var(--ct-text-primary);
    line-height: 1.6;
    min-height: 100vh;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
}
a { color: var(--ct-primary-light); text-decoration: none; transition: color var(--ct-transition); }
a:hover { color: var(--ct-primary); }
img { max-width: 100%; display: block; }
button { cursor: pointer; font-family: inherit; }
input, select, textarea { font-family: inherit; }

/* ═══ Layout Grid ═══ */
.ct-container { max-width: 1440px; margin: 0 auto; padding: 0 24px; }
.ct-grid { display: grid; gap: 20px; }
.ct-grid-2 { grid-template-columns: repeat(2, 1fr); }
.ct-grid-3 { grid-template-columns: repeat(3, 1fr); }
.ct-grid-4 { grid-template-columns: repeat(4, 1fr); }
.ct-flex { display: flex; gap: 16px; }
.ct-flex-center { display: flex; align-items: center; justify-content: center; }
.ct-flex-between { display: flex; align-items: center; justify-content: space-between; }

/* ═══ Typography ═══ */
h1, h2, h3, h4, h5, h6 { font-weight: 700; line-height: 1.2; }
h1 { font-size: 2.5rem; letter-spacing: -0.02em; }
h2 { font-size: 2rem; letter-spacing: -0.01em; }
h3 { font-size: 1.5rem; }
h4 { font-size: 1.25rem; }
.ct-text-gradient {
    background: linear-gradient(135deg, var(--ct-primary-light), var(--ct-secondary), var(--ct-accent));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.ct-text-sm { font-size: 0.875rem; }
.ct-text-xs { font-size: 0.75rem; }
.ct-text-lg { font-size: 1.125rem; }

/* ═══ Navbar ═══ */
.ct-navbar {
    position: fixed; top: 0; left: 0; right: 0;
    height: 60px; z-index: 1000;
    background: rgba(15, 23, 42, 0.92);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--ct-border);
    display: flex; align-items: center; padding: 0 24px;
}
.ct-navbar-brand {
    font-size: 1.25rem; font-weight: 800;
    background: linear-gradient(135deg, var(--ct-primary-light), var(--ct-accent));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
    margin-right: 32px; white-space: nowrap;
}
.ct-navbar-links { display: flex; gap: 4px; flex: 1; }
.ct-navbar-links a {
    padding: 8px 14px; border-radius: var(--ct-radius);
    color: var(--ct-text-secondary); font-size: 0.9rem; font-weight: 500;
    transition: all var(--ct-transition);
}
.ct-navbar-links a:hover, .ct-navbar-links a.active {
    color: var(--ct-text-primary); background: rgba(99, 102, 241, 0.1);
}
.ct-navbar-actions { display: flex; gap: 8px; align-items: center; }

/* ═══ Buttons ═══ */
.ct-btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 20px; border-radius: var(--ct-radius);
    font-size: 0.9rem; font-weight: 600; border: none;
    transition: all var(--ct-transition); cursor: pointer;
    background: var(--ct-primary); color: var(--ct-text-primary);
}
.ct-btn:hover { background: var(--ct-primary-dark); transform: translateY(-1px); box-shadow: var(--ct-shadow); }
.ct-btn:active { transform: translateY(0); }
.ct-btn-secondary { background: var(--ct-bg-light); }
.ct-btn-secondary:hover { background: #475569; }
.ct-btn-success { background: var(--ct-success); }
.ct-btn-danger { background: var(--ct-danger); }
.ct-btn-warning { background: var(--ct-warning); color: #0f172a; }
.ct-btn-outline { background: transparent; border: 1px solid var(--ct-primary); color: var(--ct-primary); }
.ct-btn-outline:hover { background: rgba(99, 102, 241, 0.1); }
.ct-btn-sm { padding: 6px 12px; font-size: 0.8rem; border-radius: var(--ct-radius-sm); }
.ct-btn-lg { padding: 14px 28px; font-size: 1rem; }
.ct-btn-icon { width: 40px; height: 40px; padding: 0; justify-content: center; border-radius: 50%; }
.ct-btn:disabled, .ct-btn.disabled { opacity: 0.5; cursor: not-allowed; pointer-events: none; }

/* ═══ Cards ═══ */
.ct-card {
    background: var(--ct-bg-card);
    border: 1px solid var(--ct-border);
    border-radius: var(--ct-radius-lg);
    padding: 20px;
    transition: all var(--ct-transition);
}
.ct-card:hover { border-color: rgba(99, 102, 241, 0.3); box-shadow: var(--ct-shadow); }
.ct-card-header { padding-bottom: 12px; border-bottom: 1px solid var(--ct-border); margin-bottom: 16px; }
.ct-card-title { font-size: 1.1rem; font-weight: 600; color: var(--ct-text-primary); }
.ct-card-body { color: var(--ct-text-secondary); }

/* ═══ Forms ═══ */
.ct-input, .ct-select {
    width: 100%; padding: 10px 14px; font-size: 0.9rem;
    background: var(--ct-bg-dark); color: var(--ct-text-primary);
    border: 1px solid var(--ct-border); border-radius: var(--ct-radius);
    transition: border-color var(--ct-transition);
    outline: none;
}
.ct-input:focus, .ct-select:focus { border-color: var(--ct-primary); box-shadow: 0 0 0 3px var(--ct-primary-glow); }
.ct-label { display: block; margin-bottom: 6px; font-size: 0.85rem; color: var(--ct-text-secondary); font-weight: 500; }
.ct-checkbox { display: flex; align-items: center; gap: 8px; cursor: pointer; }

/* ═══ Modals ═══ */
.ct-modal-overlay {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0, 0, 0, 0.7); z-index: 8000;
    display: flex; align-items: center; justify-content: center;
    animation: ct-fade-in 0.2s ease-out;
}
.ct-modal {
    background: var(--ct-bg-medium); border-radius: var(--ct-radius-xl);
    padding: 28px; max-width: 500px; width: 92%;
    max-height: 80vh; overflow-y: auto;
    border: 1px solid var(--ct-border); box-shadow: var(--ct-shadow-lg);
    animation: ct-scale-in 0.3s ease-out;
}
.ct-modal h2 { margin-bottom: 16px; }

/* ═══ Tooltips ═══ */
.ct-tooltip {
    position: fixed; z-index: 10000; pointer-events: none;
    background: rgba(15, 23, 42, 0.95); color: var(--ct-text-primary);
    padding: 8px 12px; border-radius: var(--ct-radius);
    font-size: 0.8rem; max-width: 250px;
    border: 1px solid var(--ct-border);
    box-shadow: var(--ct-shadow);
}

/* ═══ Tabs ═══ */
.ct-tabs { display: flex; border-bottom: 1px solid var(--ct-border); gap: 0; }
.ct-tab {
    padding: 10px 20px; border: none; background: transparent;
    color: var(--ct-text-muted); font-size: 0.9rem; font-weight: 500;
    border-bottom: 2px solid transparent; cursor: pointer;
    transition: all var(--ct-transition);
}
.ct-tab:hover { color: var(--ct-text-secondary); }
.ct-tab.active { color: var(--ct-primary-light); border-bottom-color: var(--ct-primary); }
.ct-tab-content { padding: 20px 0; }

/* ═══ Badges ═══ */
.ct-badge {
    display: inline-flex; align-items: center; padding: 2px 10px;
    border-radius: 999px; font-size: 0.75rem; font-weight: 600;
    background: rgba(99, 102, 241, 0.15); color: var(--ct-primary-light);
}
.ct-badge-success { background: rgba(34, 197, 94, 0.15); color: var(--ct-success); }
.ct-badge-warning { background: rgba(245, 158, 11, 0.15); color: var(--ct-warning); }
.ct-badge-danger { background: rgba(239, 68, 68, 0.15); color: var(--ct-danger); }

/* ═══ Tables ═══ */
.ct-table { width: 100%; border-collapse: collapse; }
.ct-table th, .ct-table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--ct-border); }
.ct-table th { color: var(--ct-text-muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
.ct-table td { color: var(--ct-text-secondary); font-size: 0.9rem; }
.ct-table tr:hover td { background: rgba(99, 102, 241, 0.03); }

/* ═══ Accordion ═══ */
.ct-accordion { border: 1px solid var(--ct-border); border-radius: var(--ct-radius-lg); overflow: hidden; }
.ct-accordion-item { border-bottom: 1px solid var(--ct-border); }
.ct-accordion-item:last-child { border-bottom: none; }
.ct-accordion-trigger {
    width: 100%; padding: 14px 18px; background: transparent; border: none;
    color: var(--ct-text-primary); font-size: 0.95rem; font-weight: 500;
    display: flex; justify-content: space-between; align-items: center; cursor: pointer;
    transition: background var(--ct-transition);
}
.ct-accordion-trigger:hover { background: rgba(99, 102, 241, 0.05); }
.ct-accordion-content { padding: 0 18px 14px; color: var(--ct-text-secondary); line-height: 1.7; display: none; }
.ct-accordion-item.active .ct-accordion-content { display: block; }

/* ═══ Game Layout ═══ */
.ct-app { padding-top: 60px; min-height: 100vh; }
.ct-game-layout {
    display: grid;
    grid-template-columns: 1fr 320px;
    grid-template-rows: auto 1fr auto;
    gap: 0;
    min-height: calc(100vh - 60px);
}
.ct-canvas-wrapper {
    grid-column: 1;
    grid-row: 1 / -1;
    position: relative;
    background: #0a0e1a;
    overflow: hidden;
}
.ct-canvas-wrapper canvas {
    display: block;
    width: 100%;
    height: 100%;
}
.ct-sidebar-panel {
    grid-column: 2;
    grid-row: 1 / -1;
    background: var(--ct-bg-medium);
    border-left: 1px solid var(--ct-border);
    overflow-y: auto;
    padding: 16px;
}

/* ═══ HUD ═══ */
#ct-hud {
    position: absolute; top: 16px; left: 16px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid var(--ct-border);
    border-radius: var(--ct-radius-lg);
    padding: 12px 16px;
    min-width: 200px; z-index: 100;
}
.ct-hud-section { display: flex; justify-content: space-between; padding: 4px 0; }
.ct-hud-label { color: var(--ct-text-muted); font-size: 0.8rem; }
.ct-hud-value { color: var(--ct-text-primary); font-weight: 600; font-size: 0.85rem; }
.ct-hud-resources {
    display: flex; gap: 12px; margin-top: 8px; padding-top: 8px;
    border-top: 1px solid var(--ct-border); flex-wrap: wrap;
}
.ct-resource { font-size: 0.75rem; font-weight: 600; }
.ct-resource.pigment { color: var(--ct-primary-light); }
.ct-resource.inspiration { color: var(--ct-accent); }
.ct-resource.energy { color: var(--ct-warning); }

/* ═══ Action Bar ═══ */
#ct-action-bar {
    position: absolute; bottom: 16px; left: 50%;
    transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.9);
    backdrop-filter: blur(8px);
    border: 1px solid var(--ct-border);
    border-radius: var(--ct-radius-xl);
    padding: 10px 16px;
    display: flex; gap: 8px; align-items: center;
    z-index: 100; flex-wrap: wrap; justify-content: center;
}
.ct-action-group { display: flex; gap: 6px; }
.ct-action-btn {
    padding: 8px 14px; border-radius: var(--ct-radius);
    border: 1px solid var(--ct-border); background: var(--ct-bg-light);
    color: var(--ct-text-secondary); font-size: 0.8rem; font-weight: 500;
    cursor: pointer; transition: all var(--ct-transition);
    white-space: nowrap;
}
.ct-action-btn:hover { background: var(--ct-primary); color: var(--ct-text-primary); border-color: var(--ct-primary); }
.ct-action-btn.disabled { opacity: 0.4; cursor: not-allowed; }
.ct-action-claim { border-color: var(--ct-success); color: var(--ct-success); }
.ct-action-art { border-color: var(--ct-accent); color: var(--ct-accent); }
.ct-action-attack { border-color: var(--ct-danger); color: var(--ct-danger); }
.ct-action-fortify { border-color: var(--ct-info); color: var(--ct-info); }
.ct-action-end { border-color: var(--ct-text-muted); color: var(--ct-text-muted); margin-left: 8px; }
.ct-action-info { color: var(--ct-text-muted); font-style: italic; }

/* ═══ Sidebar Components ═══ */
.ct-sidebar-section { margin-bottom: 20px; }
.ct-sidebar-title { font-size: 0.85rem; font-weight: 700; color: var(--ct-text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
.ct-player-list { display: flex; flex-direction: column; gap: 8px; }
.ct-player-item {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px; background: var(--ct-bg-dark);
    border-radius: var(--ct-radius); border-left: 3px solid transparent;
}
.ct-player-item.active { border-left-color: var(--ct-primary); background: rgba(99, 102, 241, 0.05); }
.ct-player-color { width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }
.ct-player-name { font-size: 0.85rem; font-weight: 500; flex: 1; }
.ct-player-score { font-size: 0.8rem; color: var(--ct-text-muted); }

/* ═══ Gallery ═══ */
.ct-gallery-controls { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding: 0 4px; }
.ct-gallery-sort { background: var(--ct-bg-dark); color: var(--ct-text-secondary); border: 1px solid var(--ct-border); border-radius: var(--ct-radius-sm); padding: 6px 10px; font-size: 0.85rem; }
.ct-gallery-count { color: var(--ct-text-muted); font-size: 0.85rem; }
.ct-gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.ct-gallery-item {
    background: var(--ct-bg-card); border-radius: var(--ct-radius-lg);
    overflow: hidden; border: 1px solid var(--ct-border);
    transition: all var(--ct-transition);
}
.ct-gallery-item:hover { transform: translateY(-4px); box-shadow: var(--ct-shadow); border-color: var(--ct-primary); }
.ct-gallery-thumb { width: 100%; aspect-ratio: 1; object-fit: cover; }
.ct-gallery-item-info { padding: 10px; }
.ct-gallery-item-title { display: block; font-size: 0.85rem; font-weight: 600; color: var(--ct-text-primary); }
.ct-gallery-item-score { display: block; font-size: 0.75rem; color: var(--ct-text-muted); margin-top: 2px; }
.ct-gallery-item-actions { padding: 0 10px 10px; display: flex; gap: 6px; }
.ct-gallery-empty { text-align: center; padding: 60px 20px; color: var(--ct-text-muted); font-size: 1.1rem; }
.ct-gallery-list { display: flex; flex-direction: column; gap: 12px; }
.ct-gallery-list-item { display: flex; gap: 16px; padding: 12px; background: var(--ct-bg-card); border-radius: var(--ct-radius); border: 1px solid var(--ct-border); }
.ct-gallery-list-thumb { width: 80px; height: 80px; border-radius: var(--ct-radius-sm); object-fit: cover; }
.ct-gallery-list-info h4 { color: var(--ct-text-primary); margin-bottom: 4px; }
.ct-gallery-list-info p { color: var(--ct-text-muted); font-size: 0.85rem; }

/* ═══ Hero Section ═══ */
.ct-hero {
    position: relative; padding: 80px 0; text-align: center;
    background: linear-gradient(180deg, rgba(99, 102, 241, 0.08) 0%, transparent 100%);
    overflow: hidden;
}
.ct-hero-title { font-size: 3rem; font-weight: 900; margin-bottom: 16px; }
.ct-hero-subtitle { font-size: 1.2rem; color: var(--ct-text-secondary); max-width: 600px; margin: 0 auto 32px; }
.ct-hero-actions { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.ct-hero-bg {
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    opacity: 0.1; pointer-events: none; z-index: 0;
}

/* ═══ Footer ═══ */
.ct-footer {
    padding: 40px 24px; text-align: center;
    border-top: 1px solid var(--ct-border);
    color: var(--ct-text-muted); font-size: 0.85rem;
}

/* ═══ Animations (15+ keyframes) ═══ */
@keyframes ct-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes ct-fade-out { from { opacity: 1; } to { opacity: 0; } }
@keyframes ct-slide-in { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes ct-slide-up { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
@keyframes ct-slide-down { from { transform: translateY(-20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
@keyframes ct-scale-in { from { transform: scale(0.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
@keyframes ct-scale-out { from { transform: scale(1); opacity: 1; } to { transform: scale(0.9); opacity: 0; } }
@keyframes ct-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
@keyframes ct-glow-pulse {
    0%, 100% { box-shadow: 0 0 5px var(--ct-primary-glow); }
    50% { box-shadow: 0 0 20px var(--ct-primary-glow), 0 0 40px rgba(99,102,241,0.2); }
}
@keyframes ct-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes ct-bounce { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
@keyframes ct-shake { 0%, 100% { transform: translateX(0); } 10%, 30%, 50%, 70%, 90% { transform: translateX(-5px); } 20%, 40%, 60%, 80% { transform: translateX(5px); } }
@keyframes ct-float { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-8px); } }
@keyframes ct-gradient-shift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
@keyframes ct-border-glow {
    0%, 100% { border-color: rgba(99,102,241,0.2); }
    50% { border-color: rgba(99,102,241,0.6); }
}
@keyframes ct-hex-appear {
    from { transform: scale(0) rotate(30deg); opacity: 0; }
    to { transform: scale(1) rotate(0deg); opacity: 1; }
}

.ct-animate-fade-in { animation: ct-fade-in 0.5s ease-out; }
.ct-animate-slide-up { animation: ct-slide-up 0.5s ease-out; }
.ct-animate-pulse { animation: ct-pulse 2s infinite; }
.ct-animate-glow { animation: ct-glow-pulse 2s infinite; }
.ct-animate-spin { animation: ct-spin 1s linear infinite; }
.ct-animate-bounce { animation: ct-bounce 2s ease-in-out infinite; }
.ct-animate-float { animation: ct-float 3s ease-in-out infinite; }

/* ═══ Loading / Spinner ═══ */
.ct-spinner {
    width: 40px; height: 40px;
    border: 3px solid var(--ct-border);
    border-top-color: var(--ct-primary);
    border-radius: 50%;
    animation: ct-spin 0.8s linear infinite;
}
.ct-loading { display: flex; align-items: center; justify-content: center; padding: 40px; gap: 12px; color: var(--ct-text-muted); }

/* ═══ Responsive Breakpoints ═══ */
@media (max-width: 768px) {
    .ct-navbar { padding: 0 12px; }
    .ct-navbar-links { display: none; }
    .ct-game-layout { grid-template-columns: 1fr; }
    .ct-sidebar-panel { display: none; }
    h1 { font-size: 1.75rem; }
    h2 { font-size: 1.5rem; }
    .ct-hero { padding: 40px 0; }
    .ct-hero-title { font-size: 2rem; }
    .ct-grid-2, .ct-grid-3, .ct-grid-4 { grid-template-columns: 1fr; }
    .ct-container { padding: 0 12px; }
    #ct-action-bar { bottom: 8px; padding: 8px 10px; }
    .ct-action-btn { padding: 6px 10px; font-size: 0.75rem; }
    .ct-gallery-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
}

@media (min-width: 769px) and (max-width: 1024px) {
    .ct-game-layout { grid-template-columns: 1fr 260px; }
    .ct-hero-title { font-size: 2.5rem; }
    .ct-grid-4 { grid-template-columns: repeat(2, 1fr); }
}

@media (min-width: 1025px) and (max-width: 1440px) {
    .ct-game-layout { grid-template-columns: 1fr 300px; }
}

@media (min-width: 1441px) {
    .ct-container { max-width: 1600px; }
    .ct-game-layout { grid-template-columns: 1fr 360px; }
}

/* ═══ Scrollbar ═══ */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: var(--ct-bg-dark); }
::-webkit-scrollbar-thumb { background: var(--ct-bg-light); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #475569; }

/* ═══ Selection ═══ */
::selection { background: rgba(99, 102, 241, 0.3); color: var(--ct-text-primary); }

/* ═══ Focus visible ═══ */
:focus-visible { outline: 2px solid var(--ct-primary); outline-offset: 2px; }

/* ═══ Toast Animations ═══ */
.ct-toast { pointer-events: auto; cursor: pointer; }
"""




# ═══════════════════════════════════════════════════════════════════════════════
# ANIMATION CSS & JS
# ═══════════════════════════════════════════════════════════════════════════════

ANIMATION_CSS = """
/* Additional animation-specific CSS */
@keyframes ct-territory-pulse {
    0% { filter: brightness(1); }
    50% { filter: brightness(1.3); }
    100% { filter: brightness(1); }
}
@keyframes ct-combat-flash {
    0% { background: transparent; }
    50% { background: rgba(239, 68, 68, 0.3); }
    100% { background: transparent; }
}
@keyframes ct-claim-ripple {
    0% { transform: scale(0); opacity: 0.8; }
    100% { transform: scale(3); opacity: 0; }
}
@keyframes ct-achievement-pop {
    0% { transform: scale(0.5) rotate(-10deg); opacity: 0; }
    60% { transform: scale(1.1) rotate(2deg); opacity: 1; }
    100% { transform: scale(1) rotate(0deg); opacity: 1; }
}
.ct-combat-anim { animation: ct-combat-flash 0.5s ease-out; }
.ct-claim-anim { animation: ct-claim-ripple 0.8s ease-out forwards; }
.ct-achievement-anim { animation: ct-achievement-pop 0.6s cubic-bezier(0.34, 1.56, 0.64, 1); }
.ct-territory-active { animation: ct-territory-pulse 2s ease-in-out infinite; }
"""

ANIMATION_JS = """
(function() {
'use strict';
window.CT = window.CT || {};

class AnimationController {
    constructor() {
        this.animations = [];
        this.tweens = [];
    }

    addAnimation(element, className, duration) {
        element.classList.add(className);
        setTimeout(() => element.classList.remove(className), duration || 1000);
    }

    tween(target, property, from, to, duration, easing) {
        easing = easing || 'easeInOutCubic';
        const startTime = performance.now();
        const tw = { target, property, from, to, duration, startTime, easing, done: false };
        this.tweens.push(tw);
        return tw;
    }

    _ease(t, type) {
        switch (type) {
            case 'linear': return t;
            case 'easeInQuad': return t * t;
            case 'easeOutQuad': return t * (2 - t);
            case 'easeInOutQuad': return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
            case 'easeInCubic': return t * t * t;
            case 'easeOutCubic': return (--t) * t * t + 1;
            case 'easeInOutCubic': return t < 0.5 ? 4*t*t*t : (t-1)*(2*t-2)*(2*t-2)+1;
            case 'easeOutBack': { const c = 1.70158; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }
            case 'easeOutElastic': {
                if (t === 0 || t === 1) return t;
                return Math.pow(2, -10 * t) * Math.sin((t - 0.075) * (2 * Math.PI) / 0.3) + 1;
            }
            default: return t < 0.5 ? 4*t*t*t : (t-1)*(2*t-2)*(2*t-2)+1;
        }
    }

    update(timestamp) {
        for (const tw of this.tweens) {
            if (tw.done) continue;
            const elapsed = timestamp - tw.startTime;
            let progress = Math.min(1, elapsed / tw.duration);
            progress = this._ease(progress, tw.easing);
            tw.target[tw.property] = tw.from + (tw.to - tw.from) * progress;
            if (elapsed >= tw.duration) tw.done = true;
        }
        this.tweens = this.tweens.filter(tw => !tw.done);
    }

    clear() {
        this.tweens = [];
        this.animations = [];
    }
}

window.CT.AnimationController = AnimationController;
})();
"""


# ═══════════════════════════════════════════════════════════════════════════════
# HTML COMPONENTS (~600+ lines)
# ═══════════════════════════════════════════════════════════════════════════════

GAME_HUD_HTML = """
<div id="ct-hud" class="ct-animate-slide-down">
    <div class="ct-hud-section">
        <span class="ct-hud-label">Turn</span>
        <span class="ct-hud-value">0/50</span>
    </div>
    <div class="ct-hud-section">
        <span class="ct-hud-label">Player</span>
        <span class="ct-hud-value">--</span>
    </div>
    <div class="ct-hud-section">
        <span class="ct-hud-label">Phase</span>
        <span class="ct-hud-value">waiting</span>
    </div>
    <div class="ct-hud-resources">
        <span class="ct-resource pigment">Pigment: 0</span>
        <span class="ct-resource inspiration">Inspr: 0</span>
        <span class="ct-resource energy">Energy: 0</span>
    </div>
</div>
"""

ACTION_BAR_HTML = """
<div id="ct-action-bar" class="ct-animate-slide-up">
    <div class="ct-action-group">
        <button class="ct-action-btn ct-action-claim" data-action="claim">Claim Territory</button>
        <button class="ct-action-btn ct-action-art" data-action="create_art">Create Art</button>
        <button class="ct-action-btn ct-action-fortify" data-action="fortify">Fortify</button>
    </div>
    <button class="ct-action-btn ct-action-end" data-action="end_turn">End Turn</button>
</div>
"""

CONTROLS_PANEL_HTML = """
<div class="ct-sidebar-panel" id="ct-sidebar">
    <div class="ct-sidebar-section">
        <div class="ct-sidebar-title">Players</div>
        <div class="ct-player-list" id="ct-player-list"></div>
    </div>
    <div class="ct-sidebar-section">
        <div class="ct-sidebar-title">Territory Info</div>
        <div id="ct-territory-info" class="ct-card" style="padding:12px">
            <p style="color:var(--ct-text-muted);font-size:0.85rem">Click a hex to see details</p>
        </div>
    </div>
    <div class="ct-sidebar-section">
        <div class="ct-sidebar-title">Quick Actions</div>
        <div style="display:flex;flex-direction:column;gap:6px">
            <button class="ct-btn ct-btn-sm" onclick="window.CT.app && window.CT.app.startNewGame()">New Game</button>
            <button class="ct-btn ct-btn-sm ct-btn-secondary" onclick="window.CT.app && window.CT.app.navigate('/gallery')">Gallery</button>
            <button class="ct-btn ct-btn-sm ct-btn-secondary" onclick="window.CT.app && window.CT.app.navigate('/tutorial')">Tutorial</button>
            <button class="ct-btn ct-btn-sm ct-btn-secondary" onclick="window.CT.app && window.CT.app.navigate('/settings')">Settings</button>
        </div>
    </div>
    <div class="ct-sidebar-section">
        <div class="ct-sidebar-title">Composition Score</div>
        <div id="ct-composition-display" class="ct-card" style="padding:12px">
            <div style="display:grid;gap:4px;font-size:0.8rem;color:var(--ct-text-muted)">
                <div class="ct-flex-between"><span>Balance</span><span id="ct-score-balance">--</span></div>
                <div class="ct-flex-between"><span>Thirds</span><span id="ct-score-thirds">--</span></div>
                <div class="ct-flex-between"><span>Harmony</span><span id="ct-score-harmony">--</span></div>
                <div class="ct-flex-between"><span>Contrast</span><span id="ct-score-contrast">--</span></div>
                <div class="ct-flex-between" style="border-top:1px solid var(--ct-border);padding-top:4px;margin-top:4px">
                    <span style="font-weight:600;color:var(--ct-text-primary)">Overall</span>
                    <span id="ct-score-overall" style="font-weight:700;color:var(--ct-primary-light)">--</span>
                </div>
            </div>
        </div>
    </div>
    <div class="ct-sidebar-section">
        <div class="ct-sidebar-title">Game Stats</div>
        <div id="ct-game-stats" class="ct-card" style="padding:12px;font-size:0.8rem;color:var(--ct-text-muted)">
            <div class="ct-flex-between"><span>Total Hexes</span><span id="ct-stat-total">--</span></div>
            <div class="ct-flex-between"><span>Claimed</span><span id="ct-stat-claimed">--</span></div>
            <div class="ct-flex-between"><span>Unclaimed</span><span id="ct-stat-unclaimed">--</span></div>
        </div>
    </div>
</div>
"""

GALLERY_VIEW_HTML = """
<div id="ct-gallery-view" class="ct-view ct-container" style="display:none;padding-top:24px">
    <div class="ct-flex-between" style="margin-bottom:24px">
        <h2>Art Gallery</h2>
        <div class="ct-flex" style="gap:8px">
            <button class="ct-btn ct-btn-sm ct-btn-secondary" onclick="window.CT.gallery && window.CT.gallery.currentView='grid';window.CT.gallery.renderGalleryView('ct-gallery')">Grid</button>
            <button class="ct-btn ct-btn-sm ct-btn-secondary" onclick="window.CT.gallery && window.CT.gallery.currentView='list';window.CT.gallery.renderGalleryView('ct-gallery')">List</button>
        </div>
    </div>
    <div id="ct-gallery"></div>
</div>
"""

SETTINGS_HTML = """
<div id="ct-settings-view" class="ct-view ct-container" style="display:none;padding-top:24px;max-width:600px">
    <h2 style="margin-bottom:24px">Settings</h2>
    <div class="ct-card" style="display:grid;gap:16px">
        <div>
            <label class="ct-label">Volume</label>
            <input type="range" class="ct-input" min="0" max="100" value="30" id="ct-settings-volume">
        </div>
        <div>
            <label class="ct-checkbox"><input type="checkbox" checked id="ct-settings-music"> Enable Music</label>
        </div>
        <div>
            <label class="ct-checkbox"><input type="checkbox" checked id="ct-settings-grid"> Show Grid</label>
        </div>
        <div>
            <label class="ct-checkbox"><input type="checkbox" checked id="ct-settings-minimap"> Show Minimap</label>
        </div>
        <div>
            <label class="ct-label">Difficulty</label>
            <select class="ct-select" id="ct-settings-difficulty">
                <option value="easy">Easy</option>
                <option value="medium" selected>Medium</option>
                <option value="hard">Hard</option>
            </select>
        </div>
        <div>
            <label class="ct-label">Grid Radius</label>
            <input type="number" class="ct-input" min="4" max="16" value="8" id="ct-settings-radius">
        </div>
        <button class="ct-btn" onclick="window.CT.app && window.CT.app._saveSettingsForm()">Save Settings</button>
    </div>
</div>
"""

MAIN_APP_HTML = (
    '<div class="ct-app">'
    '  <nav class="ct-navbar">'
    '    <span class="ct-navbar-brand">Chromatic Territories</span>'
    '    <div class="ct-navbar-links">'
    '      <a href="#/play">Play</a>'
    '      <a href="#/gallery">Gallery</a>'
    '      <a href="#/tutorial">Tutorial</a>'
    '      <a href="#/leaderboard">Leaderboard</a>'
    '      <a href="#/achievements">Achievements</a>'
    '      <a href="#/settings">Settings</a>'
    '      <a href="#/about">About</a>'
    '    </div>'
    '    <div class="ct-navbar-actions">'
    '      <button class="ct-btn ct-btn-sm" onclick="window.CT.app && window.CT.app.startNewGame()">New Game</button>'
    '    </div>'
    '  </nav>'
    '  <div id="ct-game-view" class="ct-view">'
    '    <div class="ct-game-layout">'
    '      <div class="ct-canvas-wrapper">'
    '        <canvas id="ct-main-canvas" width="1200" height="800"></canvas>'
    + GAME_HUD_HTML
    + ACTION_BAR_HTML +
    '      </div>'
    + CONTROLS_PANEL_HTML +
    '    </div>'
    '  </div>'
    + GALLERY_VIEW_HTML
    + SETTINGS_HTML +
    '  <div class="ct-footer">'
    '    <p>Chromatic Territories &mdash; Where Art Meets Strategy</p>'
    '    <p style="margin-top:8px;font-size:0.75rem;color:var(--ct-text-muted)">Generated with jugeo-webapp</p>'
    '  </div>'
    '</div>'
)


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK TEMPLATES (Jinja2 blocks)
# ═══════════════════════════════════════════════════════════════════════════════

FLASK_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Chromatic Territories{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {% block extra_head %}{% endblock %}
</head>
<body>
    <nav class="ct-navbar">
        <span class="ct-navbar-brand">Chromatic Territories</span>
        <div class="ct-navbar-links">
            <a href="{{ url_for('index') }}">Home</a>
            <a href="{{ url_for('play') }}">Play</a>
            <a href="{{ url_for('gallery_page') }}">Gallery</a>
            <a href="{{ url_for('tutorial_page') }}">Tutorial</a>
            <a href="{{ url_for('leaderboard_page') }}">Leaderboard</a>
            <a href="{{ url_for('achievements_page') }}">Achievements</a>
            <a href="{{ url_for('settings_page') }}">Settings</a>
            <a href="{{ url_for('about') }}">About</a>
        </div>
    </nav>
    <div class="ct-app">
        {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
        <div style="padding-top:70px;padding-left:24px;padding-right:24px">
            {% for category, message in messages %}
            <div class="ct-card" style="margin-bottom:8px;border-left:3px solid var(--ct-{{ category }})">{{ message }}</div>
            {% endfor %}
        </div>
        {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
    <footer class="ct-footer">
        <p>Chromatic Territories &mdash; Where Art Meets Strategy</p>
    </footer>
    {% block scripts %}{% endblock %}
</body>
</html>"""

FLASK_INDEX_CONTENT = """{% extends "base.html" %}
{% block title %}Chromatic Territories - Home{% endblock %}
{% block content %}
<div class="ct-hero">
    <div class="ct-container">
        <h1 class="ct-hero-title ct-text-gradient">Chromatic Territories</h1>
        <p class="ct-hero-subtitle">A game where art and strategy merge. Your palette is your power.</p>
        <div class="ct-hero-actions">
            <a href="{{ url_for('play') }}" class="ct-btn ct-btn-lg">Play Now</a>
            <a href="{{ url_for('tutorial_page') }}" class="ct-btn ct-btn-lg ct-btn-secondary">Tutorial</a>
        </div>
    </div>
</div>
<div class="ct-container" style="padding:40px 24px">
    <div class="ct-grid ct-grid-3">
        <div class="ct-card"><h3>Territory as Composition</h3><p class="ct-card-body">Claim territory on a hex grid. Visual harmony drives your strategy.</p></div>
        <div class="ct-card"><h3>Color as Resource</h3><p class="ct-card-body">Complementary colors = strong borders. Analogous = merging risk.</p></div>
        <div class="ct-card"><h3>Art as Weapon</h3><p class="ct-card-body">Attack with noise, fractals, and L-systems. Quality determines outcome.</p></div>
    </div>
</div>
{% endblock %}"""




FLASK_PLAY_CONTENT = """{% extends "base.html" %}
{% block title %}Play - Chromatic Territories{% endblock %}
{% block extra_head %}
<script src="{{ url_for('static', filename='js/noise.js') }}"></script>
<script src="{{ url_for('static', filename='js/color_theory.js') }}"></script>
<script src="{{ url_for('static', filename='js/fractal.js') }}"></script>
<script src="{{ url_for('static', filename='js/lsystem.js') }}"></script>
<script src="{{ url_for('static', filename='js/particle.js') }}"></script>
<script src="{{ url_for('static', filename='js/cellular.js') }}"></script>
<script src="{{ url_for('static', filename='js/composition.js') }}"></script>
<script src="{{ url_for('static', filename='js/territory.js') }}"></script>
<script src="{{ url_for('static', filename='js/game_engine.js') }}"></script>
<script src="{{ url_for('static', filename='js/combat.js') }}"></script>
<script src="{{ url_for('static', filename='js/ai.js') }}"></script>
<script src="{{ url_for('static', filename='js/scoring.js') }}"></script>
<script src="{{ url_for('static', filename='js/canvas_renderer.js') }}"></script>
<script src="{{ url_for('static', filename='js/ui_system.js') }}"></script>
<script src="{{ url_for('static', filename='js/gallery.js') }}"></script>
<script src="{{ url_for('static', filename='js/tutorial.js') }}"></script>
<script src="{{ url_for('static', filename='js/audio.js') }}"></script>
<script src="{{ url_for('static', filename='js/generative_music.js') }}"></script>
<script src="{{ url_for('static', filename='js/data_layer.js') }}"></script>
<script src="{{ url_for('static', filename='js/app.js') }}"></script>
{% endblock %}
{% block content %}
<div id="ct-game-view" class="ct-view">
    <div class="ct-game-layout">
        <div class="ct-canvas-wrapper">
            <canvas id="ct-main-canvas" width="1200" height="800"></canvas>
            <div id="ct-hud"></div>
            <div id="ct-action-bar"></div>
        </div>
        <div class="ct-sidebar-panel" id="ct-sidebar">
            <div class="ct-sidebar-section">
                <div class="ct-sidebar-title">Players</div>
                <div class="ct-player-list" id="ct-player-list"></div>
            </div>
            <div class="ct-sidebar-section">
                <div class="ct-sidebar-title">Quick Actions</div>
                <div style="display:flex;flex-direction:column;gap:6px">
                    <button class="ct-btn ct-btn-sm" onclick="window.CT.app&&window.CT.app.startNewGame()">New Game</button>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}"""

FLASK_GALLERY_CONTENT = """{% extends "base.html" %}
{% block title %}Gallery - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px">
    <h2 style="margin-bottom:24px">Art Gallery</h2>
    {% if artworks %}
    <div class="ct-gallery-grid">
        {% for art in artworks %}
        <div class="ct-gallery-item">
            <div style="background:var(--ct-bg-light);aspect-ratio:1;display:flex;align-items:center;justify-content:center;color:var(--ct-text-muted)">{{ art.title }}</div>
            <div class="ct-gallery-item-info">
                <span class="ct-gallery-item-title">{{ art.title }}</span>
                <span class="ct-gallery-item-score">Score: {{ art.score }}</span>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="ct-gallery-empty">No artworks yet. Play the game to create art!</div>
    {% endif %}
</div>
{% endblock %}"""

FLASK_TUTORIAL_CONTENT = """{% extends "base.html" %}
{% block title %}Tutorial - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:800px">
    <h2 style="margin-bottom:24px">Tutorial</h2>
    <div class="ct-accordion">
        <div class="ct-accordion-item active">
            <button class="ct-accordion-trigger" onclick="this.parentElement.classList.toggle('active')">1. Territory as Composition <span>+</span></button>
            <div class="ct-accordion-content"><p>Claim territory on the hex grid. Your expansion must maintain visual harmony using the rule of thirds and golden ratio principles.</p></div>
        </div>
        <div class="ct-accordion-item">
            <button class="ct-accordion-trigger" onclick="this.parentElement.classList.toggle('active')">2. Color as Resource <span>+</span></button>
            <div class="ct-accordion-content"><p>Your color palette IS your strategy. Complementary colors create strong borders. Analogous colors risk merging with neighbors.</p></div>
        </div>
        <div class="ct-accordion-item">
            <button class="ct-accordion-trigger" onclick="this.parentElement.classList.toggle('active')">3. Generative Art as Weapon <span>+</span></button>
            <div class="ct-accordion-content"><p>Attack by applying noise, fractal, and cellular patterns. The quality of your generative art determines combat outcomes.</p></div>
        </div>
        <div class="ct-accordion-item">
            <button class="ct-accordion-trigger" onclick="this.parentElement.classList.toggle('active')">4. Composition Score = Health <span>+</span></button>
            <div class="ct-accordion-content"><p>Territory health equals aesthetic score. Balance, contrast, rhythm, and harmony all contribute to your defense.</p></div>
        </div>
        <div class="ct-accordion-item">
            <button class="ct-accordion-trigger" onclick="this.parentElement.classList.toggle('active')">5. Evolution &amp; Growth <span>+</span></button>
            <div class="ct-accordion-content"><p>Territories evolve via cellular automata. Healthy compositions thrive and expand. Poor ones decay and eventually die.</p></div>
        </div>
    </div>
</div>
{% endblock %}"""

FLASK_LEADERBOARD_CONTENT = """{% extends "base.html" %}
{% block title %}Leaderboard - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:800px">
    <h2 style="margin-bottom:24px">Leaderboard</h2>
    <table class="ct-table">
        <thead><tr><th>#</th><th>Player</th><th>Score</th><th>Territories</th><th>Date</th></tr></thead>
        <tbody>
        {% for entry in entries %}
        <tr><td>{{ loop.index }}</td><td>{{ entry.player_name }}</td><td>{{ entry.score }}</td><td>{{ entry.territories }}</td><td>{{ entry.created_at }}</td></tr>
        {% else %}
        <tr><td colspan="5" style="text-align:center;color:var(--ct-text-muted);padding:40px">No entries yet</td></tr>
        {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}"""

FLASK_ACHIEVEMENTS_CONTENT = """{% extends "base.html" %}
{% block title %}Achievements - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px">
    <h2 style="margin-bottom:24px">Achievements</h2>
    <div class="ct-grid ct-grid-3">
        {% for ach in achievements %}
        <div class="ct-card" style="opacity:{{ '1' if ach.unlocked else '0.4' }}">
            <h4>{{ ach.name }} {{ '&#10003;' if ach.unlocked else '' }}</h4>
            <p class="ct-card-body">{{ ach.description }}</p>
        </div>
        {% endfor %}
    </div>
</div>
{% endblock %}"""

FLASK_SETTINGS_CONTENT = """{% extends "base.html" %}
{% block title %}Settings - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:600px">
    <h2 style="margin-bottom:24px">Settings</h2>
    <form method="POST" action="{{ url_for('save_settings') }}" class="ct-card" style="display:grid;gap:16px">
        <div><label class="ct-label">Volume</label><input type="range" name="volume" min="0" max="100" value="{{ settings.volume }}" class="ct-input"></div>
        <div><label class="ct-checkbox"><input type="checkbox" name="music_enabled" {{ 'checked' if settings.music_enabled else '' }}> Enable Music</label></div>
        <div><label class="ct-checkbox"><input type="checkbox" name="show_grid" {{ 'checked' if settings.show_grid else '' }}> Show Grid</label></div>
        <div>
            <label class="ct-label">Difficulty</label>
            <select name="difficulty" class="ct-select">
                <option value="easy" {{ 'selected' if settings.difficulty == 'easy' else '' }}>Easy</option>
                <option value="medium" {{ 'selected' if settings.difficulty == 'medium' else '' }}>Medium</option>
                <option value="hard" {{ 'selected' if settings.difficulty == 'hard' else '' }}>Hard</option>
            </select>
        </div>
        <button type="submit" class="ct-btn">Save Settings</button>
    </form>
</div>
{% endblock %}"""

FLASK_ABOUT_CONTENT = """{% extends "base.html" %}
{% block title %}About - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:800px">
    <h2 style="margin-bottom:24px">About Chromatic Territories</h2>
    <div class="ct-card">
        <p class="ct-card-body" style="line-height:1.8">
            Chromatic Territories is a game where gaming and generative art are meaningfully blended.
            Territory is composition, color is resource, generative brushes are weapons,
            and composition score is health. The game features Perlin noise terrain, fractal attacks,
            L-system growth, cellular automata evolution, and a generative audio soundtrack
            that responds to game state.
        </p>
        <p class="ct-card-body" style="margin-top:16px;color:var(--ct-text-muted)">
            Built with the jugeo-webapp generation pipeline using obligation presheaves.
        </p>
    </div>
</div>
{% endblock %}"""

FLASK_PLAYER_PROFILE_CONTENT = """{% extends "base.html" %}
{% block title %}{{ player.name }} - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:600px">
    <h2 style="margin-bottom:24px">{{ player.name }}</h2>
    <div class="ct-card">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
            <div style="width:40px;height:40px;border-radius:50%;background:{{ player.color }}"></div>
            <div><h3>{{ player.name }}</h3><span class="ct-badge">{{ player.art_style }}</span></div>
        </div>
        <table class="ct-table">
            <tr><td>Total Score</td><td>{{ player.total_score }}</td></tr>
            <tr><td>Games Played</td><td>{{ player.games_played }}</td></tr>
            <tr><td>Territories Claimed</td><td>{{ player.territories_claimed }}</td></tr>
            <tr><td>Combats Won</td><td>{{ player.combats_won }}</td></tr>
        </table>
    </div>
</div>
{% endblock %}"""

FLASK_NEW_GAME_CONTENT = """{% extends "base.html" %}
{% block title %}New Game - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px;max-width:600px">
    <h2 style="margin-bottom:24px">Start New Game</h2>
    <form method="POST" action="{{ url_for('create_game') }}" class="ct-card" style="display:grid;gap:16px">
        <div><label class="ct-label">Your Name</label><input type="text" name="player_name" class="ct-input" value="Player 1" required></div>
        <div><label class="ct-label">Your Color</label><input type="color" name="player_color" value="#6366f1" class="ct-input"></div>
        <div><label class="ct-label">Number of AI Opponents</label><select name="ai_count" class="ct-select"><option value="1">1</option><option value="2" selected>2</option><option value="3">3</option></select></div>
        <div><label class="ct-label">Grid Radius</label><input type="number" name="grid_radius" min="4" max="16" value="8" class="ct-input"></div>
        <div><label class="ct-label">Max Turns</label><input type="number" name="max_turns" min="10" max="100" value="50" class="ct-input"></div>
        <button type="submit" class="ct-btn ct-btn-lg">Start Game</button>
    </form>
</div>
{% endblock %}"""

FLASK_SESSION_DETAIL_CONTENT = """{% extends "base.html" %}
{% block title %}Game Session - Chromatic Territories{% endblock %}
{% block content %}
<div class="ct-container" style="padding-top:80px">
    <h2 style="margin-bottom:24px">Game Session #{{ session.id }}</h2>
    <div class="ct-grid ct-grid-2">
        <div class="ct-card">
            <h4>Game Info</h4>
            <table class="ct-table">
                <tr><td>Status</td><td><span class="ct-badge">{{ session.status }}</span></td></tr>
                <tr><td>Players</td><td>{{ session.player_count }}</td></tr>
                <tr><td>Turn</td><td>{{ session.current_turn }}/{{ session.max_turns }}</td></tr>
                <tr><td>Grid Radius</td><td>{{ session.grid_radius }}</td></tr>
            </table>
        </div>
        <div class="ct-card">
            <h4>Players</h4>
            {% for player in session.players %}
            <div class="ct-player-item"><div class="ct-player-color" style="background:{{ player.color }}"></div><span class="ct-player-name">{{ player.name }}</span><span class="ct-player-score">{{ player.score }}</span></div>
            {% endfor %}
        </div>
    </div>
</div>
{% endblock %}"""


# ═══════════════════════════════════════════════════════════════════════════════
# GENERATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

ALL_JS_MODULES = "\n\n".join([
    NOISE_JS, COLOR_THEORY_JS, FRACTAL_JS, LSYSTEM_JS,
    PARTICLE_JS, CELLULAR_JS, COMPOSITION_JS, TERRITORY_JS,
    GAME_ENGINE_JS, COMBAT_JS, AI_JS, SCORING_JS,
    CANVAS_RENDERER_JS, UI_SYSTEM_JS, GALLERY_JS, TUTORIAL_JS,
    AUDIO_JS, GENERATIVE_MUSIC_JS, DATA_LAYER_JS, APP_JS,
    ANIMATION_JS,
])

COMBINED_CSS = ALL_CSS + "\n\n" + ANIMATION_CSS


def generate_html(outdir, port):
    """Generate the HTML-only version using CopilotGenerationDriver."""
    print("=" * 60)
    print("  Generating HTML-only Chromatic Territories")
    print("=" * 60)

    t0 = time.time()
    os.makedirs(outdir, exist_ok=True)

    print("[1/9] Creating CopilotGenerationDriver (obligations=production)...")
    driver = CopilotGenerationDriver(
        name="chromatic_territories",
        title="Chromatic Territories",
        description=(
            "A game where gaming and generative art are meaningfully blended. "
            "Territory is composition, color is resource, generative brushes are "
            "weapons, and composition score is health."
        ),
        port=port,
        obligations="production",
        target=GenerationTarget.HTML_ONLY,
    )

    print("[2/9] Proposing THEME fiber...")
    driver.propose(FiberKind.THEME, SectionProposal(
        fiber=FiberKind.THEME,
        theme=THEME_COLORS,
    ))

    print("[3/9] Proposing NAVIGATION fiber...")
    driver.propose(FiberKind.NAVIGATION, SectionProposal(
        fiber=FiberKind.NAVIGATION,
        nav_items=NAV_ITEMS,
        components=[
            ComponentSpec(
                kind=ComponentKind.NAVBAR,
                id="main-nav",
                props={"brand": "Chromatic Territories", "links": NAV_ITEMS},
            ),
        ],
    ))

    print("[4/9] Proposing HTML_STRUCTURE fiber...")
    driver.propose(FiberKind.HTML_STRUCTURE, SectionProposal(
        fiber=FiberKind.HTML_STRUCTURE,
        components=[
            ComponentSpec(
                kind=ComponentKind.HERO,
                id="hero",
                props={
                    "title": "Chromatic Territories",
                    "subtitle": "Where Art Meets Strategy",
                },
            ),
            ComponentSpec(
                kind=ComponentKind.CUSTOM,
                id="game-app",
                custom_html=MAIN_APP_HTML,
            ),
            ComponentSpec(
                kind=ComponentKind.CANVAS,
                id="main-canvas",
                props={"width": 1200, "height": 800},
            ),
            ComponentSpec(
                kind=ComponentKind.CARD,
                id="info-card",
                props={"title": "Game Info"},
            ),
            ComponentSpec(
                kind=ComponentKind.TABS,
                id="info-tabs",
                props={"tabs": [
                    {"id": "tab-territory", "label": "Territory", "content": "Hex grid territory management"},
                    {"id": "tab-combat", "label": "Combat", "content": "Color-based art combat system"},
                    {"id": "tab-gallery", "label": "Gallery", "content": "Collection of generative artworks"},
                    {"id": "tab-stats", "label": "Stats", "content": "Game statistics and scores"},
                ]},
            ),
            ComponentSpec(
                kind=ComponentKind.ACCORDION,
                id="tutorial-accordion",
                props={
                    "items": [
                        {"title": "Territory", "content": "Claim hexes with visual harmony."},
                        {"title": "Color", "content": "Your palette is your strategy."},
                        {"title": "Combat", "content": "Art attacks determine outcomes."},
                        {"title": "Growth", "content": "Cellular automata drive evolution."},
                        {"title": "Music", "content": "Soundtrack generated from game state."},
                    ]
                },
            ),
            ComponentSpec(
                kind=ComponentKind.TABLE,
                id="leaderboard-table",
                props={
                    "headers": ["Rank", "Player", "Score", "Territories"],
                    "rows": [],
                },
            ),
            ComponentSpec(
                kind=ComponentKind.MODAL,
                id="settings-modal",
                props={"title": "Settings"},
            ),
            ComponentSpec(
                kind=ComponentKind.SIDEBAR,
                id="game-sidebar",
                props={"position": "right", "width": "320px", "items": [
                    {"label": "Players", "href": "#players"},
                    {"label": "Territory", "href": "#territory"},
                    {"label": "Actions", "href": "#actions"},
                    {"label": "Stats", "href": "#stats"},
                ]},
            ),
            ComponentSpec(
                kind=ComponentKind.FOOTER,
                id="app-footer",
                props={"text": "Chromatic Territories - Where Art Meets Strategy"},
            ),
        ],
    ))

    print("[5/9] Proposing CSS_STYLING fiber...")
    driver.propose(FiberKind.CSS_STYLING, SectionProposal(
        fiber=FiberKind.CSS_STYLING,
        css=COMBINED_CSS,
    ))

    print("[6/9] Proposing JS_INTERACTION fiber...")
    driver.propose(FiberKind.JS_INTERACTION, SectionProposal(
        fiber=FiberKind.JS_INTERACTION,
        js=ALL_JS_MODULES,
    ))

    print("[7/9] Proposing ANIMATION fiber...")
    driver.propose(FiberKind.ANIMATION, SectionProposal(
        fiber=FiberKind.ANIMATION,
        css=ANIMATION_CSS,
        js=ANIMATION_JS,
    ))

    print("[8/9] Proposing DATA_LAYER fiber...")
    driver.propose(FiberKind.DATA_LAYER, SectionProposal(
        fiber=FiberKind.DATA_LAYER,
        js=DATA_LAYER_JS,
    ))

    print("[9/9] Proposing CONTENT fiber...")
    driver.propose(FiberKind.CONTENT, SectionProposal(
        fiber=FiberKind.CONTENT,
        components=[
            ComponentSpec(
                kind=ComponentKind.CARD,
                id="feature-territory",
                props={"title": "Territory as Composition", "body": "Claim territory on a hex grid. Visual harmony drives strategy."},
            ),
            ComponentSpec(
                kind=ComponentKind.CARD,
                id="feature-color",
                props={"title": "Color as Resource", "body": "Complementary = strong borders. Analogous = merging risk."},
            ),
            ComponentSpec(
                kind=ComponentKind.CARD,
                id="feature-art",
                props={"title": "Art as Weapon", "body": "Attack with noise, fractals, L-systems. Quality = power."},
            ),
            ComponentSpec(
                kind=ComponentKind.CARD,
                id="feature-score",
                props={"title": "Composition = Health", "body": "Balance, contrast, rhythm, harmony determine defense."},
            ),
        ],
    ))

    print("\nGenerating with auto_enrich=True, max_rounds=5...")
    result = driver.generate(outdir, auto_enrich=True, max_rounds=5)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  HTML Generation Complete!")
    print(f"  Output: {outdir}")
    print(f"  Files: {len(result.files_created)}")
    print(f"  Total lines: {result.total_lines}")
    if result.obligation_report:
        print(f"  Obligations met: {result.obligation_report.all_met}")
        print(f"  Enrichment rounds: {result.obligation_report.enrichment_rounds}")
        for r in result.obligation_report.results:
            status = "PASS" if r.met else "FAIL"
            print(f"    [{status}] {r.obligation.kind.value}: "
                  f"{r.actual:.0f} / {r.obligation.minimum:.0f}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'=' * 60}")
    return result


def generate_flask(outdir, port):
    """Generate the Flask version using FlaskAppGenerator."""
    print("=" * 60)
    print("  Generating Flask Chromatic Territories")
    print("=" * 60)

    t0 = time.time()
    os.makedirs(outdir, exist_ok=True)

    print("[1/4] Defining models...")
    models = [
        ModelSpec(
            name="Player",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("name", ColumnType.STRING, nullable=False),
                ColumnSpec("color", ColumnType.STRING, default="#6366f1"),
                ColumnSpec("art_style", ColumnType.STRING, default="impressionist"),
                ColumnSpec("total_score", ColumnType.INTEGER, default=0),
                ColumnSpec("games_played", ColumnType.INTEGER, default=0),
                ColumnSpec("territories_claimed", ColumnType.INTEGER, default=0),
                ColumnSpec("combats_won", ColumnType.INTEGER, default=0),
                ColumnSpec("combats_lost", ColumnType.INTEGER, default=0),
                ColumnSpec("artworks_created", ColumnType.INTEGER, default=0),
                ColumnSpec("created_at", ColumnType.DATETIME),
            ],
        ),
        ModelSpec(
            name="GameSession",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("status", ColumnType.STRING, default="active"),
                ColumnSpec("current_turn", ColumnType.INTEGER, default=1),
                ColumnSpec("max_turns", ColumnType.INTEGER, default=50),
                ColumnSpec("grid_radius", ColumnType.INTEGER, default=8),
                ColumnSpec("player_count", ColumnType.INTEGER, default=2),
                ColumnSpec("winner_id", ColumnType.INTEGER),
                ColumnSpec("game_data", ColumnType.JSON),
                ColumnSpec("created_at", ColumnType.DATETIME),
                ColumnSpec("updated_at", ColumnType.DATETIME),
            ],
        ),
        ModelSpec(
            name="Territory",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("session_id", ColumnType.INTEGER, foreign_key="gamesessions.id"),
                ColumnSpec("q", ColumnType.INTEGER),
                ColumnSpec("r", ColumnType.INTEGER),
                ColumnSpec("owner_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("color", ColumnType.STRING),
                ColumnSpec("terrain", ColumnType.STRING, default="plains"),
                ColumnSpec("health", ColumnType.FLOAT, default=100.0),
                ColumnSpec("art_style", ColumnType.STRING),
                ColumnSpec("art_quality", ColumnType.FLOAT, default=0.0),
            ],
        ),
        ModelSpec(
            name="Artwork",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("title", ColumnType.STRING, nullable=False),
                ColumnSpec("artist_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("session_id", ColumnType.INTEGER, foreign_key="gamesessions.id"),
                ColumnSpec("style", ColumnType.STRING),
                ColumnSpec("score", ColumnType.FLOAT, default=0.0),
                ColumnSpec("turn_created", ColumnType.INTEGER),
                ColumnSpec("palette", ColumnType.JSON),
                ColumnSpec("image_data", ColumnType.TEXT),
                ColumnSpec("created_at", ColumnType.DATETIME),
            ],
        ),
        ModelSpec(
            name="Achievement",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("player_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("name", ColumnType.STRING, nullable=False),
                ColumnSpec("description", ColumnType.TEXT),
                ColumnSpec("icon", ColumnType.STRING),
                ColumnSpec("unlocked", ColumnType.BOOLEAN, default=False),
                ColumnSpec("unlocked_at", ColumnType.DATETIME),
            ],
        ),
        ModelSpec(
            name="LeaderboardEntry",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("player_id", ColumnType.INTEGER, foreign_key="players.id"),
                ColumnSpec("player_name", ColumnType.STRING),
                ColumnSpec("score", ColumnType.INTEGER),
                ColumnSpec("territories", ColumnType.INTEGER),
                ColumnSpec("art_score", ColumnType.FLOAT),
                ColumnSpec("turns", ColumnType.INTEGER),
                ColumnSpec("created_at", ColumnType.DATETIME),
            ],
        ),
        ModelSpec(
            name="Setting",
            columns=[
                ColumnSpec("id", ColumnType.INTEGER, primary_key=True),
                ColumnSpec("key", ColumnType.STRING, unique=True),
                ColumnSpec("value", ColumnType.TEXT),
                ColumnSpec("updated_at", ColumnType.DATETIME),
            ],
        ),
    ]

    print("[2/4] Defining routes...")
    routes = [
        RouteSpec("/", handler_name="index", template="index.html", response_type=ResponseType.TEMPLATE, description="Home page"),
        RouteSpec("/play", handler_name="play", template="play.html", response_type=ResponseType.TEMPLATE, description="Game page"),
        RouteSpec("/gallery", handler_name="gallery_page", template="gallery.html", response_type=ResponseType.TEMPLATE, description="Gallery page"),
        RouteSpec("/tutorial", handler_name="tutorial_page", template="tutorial.html", response_type=ResponseType.TEMPLATE, description="Tutorial"),
        RouteSpec("/leaderboard", handler_name="leaderboard_page", template="leaderboard.html", response_type=ResponseType.TEMPLATE, description="Leaderboard"),
        RouteSpec("/achievements", handler_name="achievements_page", template="achievements.html", response_type=ResponseType.TEMPLATE, description="Achievements"),
        RouteSpec("/settings", handler_name="settings_page", template="settings.html", response_type=ResponseType.TEMPLATE, description="Settings"),
        RouteSpec("/settings/save", handler_name="save_settings", methods=["POST"], response_type=ResponseType.REDIRECT, description="Save settings"),
        RouteSpec("/about", handler_name="about", template="about.html", response_type=ResponseType.TEMPLATE, description="About page"),
        RouteSpec("/new-game", handler_name="new_game_page", template="new_game.html", response_type=ResponseType.TEMPLATE, description="New game form"),
        RouteSpec("/games/create", handler_name="create_game", methods=["POST"], response_type=ResponseType.REDIRECT, description="Create game"),
        RouteSpec("/games/<int:id>", handler_name="game_detail", template="session_detail.html", response_type=ResponseType.TEMPLATE, description="Game detail"),
        RouteSpec("/player/<int:id>", handler_name="player_profile", template="player_profile.html", response_type=ResponseType.TEMPLATE, description="Player profile"),
        RouteSpec("/api/games", handler_name="api_list_games", response_type=ResponseType.JSON, description="List games"),
        RouteSpec("/api/games/<int:id>", handler_name="api_get_game", response_type=ResponseType.JSON, description="Get game"),
        RouteSpec("/api/games", handler_name="api_create_game", methods=["POST"], response_type=ResponseType.JSON, description="Create game API"),
        RouteSpec("/api/games/<int:id>/action", handler_name="api_game_action", methods=["POST"], response_type=ResponseType.JSON, description="Submit action"),
        RouteSpec("/api/games/<int:id>/state", handler_name="api_game_state", response_type=ResponseType.JSON, description="Get game state"),
        RouteSpec("/api/players", handler_name="api_list_players", response_type=ResponseType.JSON, description="List players"),
        RouteSpec("/api/players/<int:id>", handler_name="api_get_player", response_type=ResponseType.JSON, description="Get player"),
        RouteSpec("/api/players", handler_name="api_create_player", methods=["POST"], response_type=ResponseType.JSON, description="Create player"),
        RouteSpec("/api/leaderboard", handler_name="api_leaderboard", response_type=ResponseType.JSON, description="Get leaderboard"),
        RouteSpec("/api/achievements/<int:player_id>", handler_name="api_achievements", response_type=ResponseType.JSON, description="Get achievements"),
        RouteSpec("/api/gallery", handler_name="api_gallery", response_type=ResponseType.JSON, description="List artworks"),
        RouteSpec("/api/gallery", handler_name="api_create_artwork", methods=["POST"], response_type=ResponseType.JSON, description="Create artwork"),
        RouteSpec("/api/gallery/<int:id>", handler_name="api_get_artwork", response_type=ResponseType.JSON, description="Get artwork"),
        RouteSpec("/api/gallery/<int:id>", handler_name="api_delete_artwork", methods=["DELETE"], response_type=ResponseType.JSON, description="Delete artwork"),
        RouteSpec("/api/settings", handler_name="api_get_settings", response_type=ResponseType.JSON, description="Get settings"),
        RouteSpec("/api/settings", handler_name="api_save_settings", methods=["POST"], response_type=ResponseType.JSON, description="Save settings"),
    ]

    print("[3/4] Building templates and static files...")
    templates = [
        TemplateSpec(name="base.html", extends="", blocks={"content": "", "title": "Chromatic Territories", "extra_head": "", "scripts": ""}),
        TemplateSpec(name="index.html", extends="base.html", blocks={"content": FLASK_INDEX_CONTENT}),
        TemplateSpec(name="play.html", extends="base.html", blocks={"content": FLASK_PLAY_CONTENT}),
        TemplateSpec(name="gallery.html", extends="base.html", blocks={"content": FLASK_GALLERY_CONTENT}),
        TemplateSpec(name="tutorial.html", extends="base.html", blocks={"content": FLASK_TUTORIAL_CONTENT}),
        TemplateSpec(name="leaderboard.html", extends="base.html", blocks={"content": FLASK_LEADERBOARD_CONTENT}),
        TemplateSpec(name="achievements.html", extends="base.html", blocks={"content": FLASK_ACHIEVEMENTS_CONTENT}),
        TemplateSpec(name="settings.html", extends="base.html", blocks={"content": FLASK_SETTINGS_CONTENT}),
        TemplateSpec(name="about.html", extends="base.html", blocks={"content": FLASK_ABOUT_CONTENT}),
        TemplateSpec(name="player_profile.html", extends="base.html", blocks={"content": FLASK_PLAYER_PROFILE_CONTENT}),
        TemplateSpec(name="new_game.html", extends="base.html", blocks={"content": FLASK_NEW_GAME_CONTENT}),
        TemplateSpec(name="session_detail.html", extends="base.html", blocks={"content": FLASK_SESSION_DETAIL_CONTENT}),
    ]

    # Each JS module as a separate static file
    js_modules = [
        ("js/noise.js", NOISE_JS),
        ("js/color_theory.js", COLOR_THEORY_JS),
        ("js/fractal.js", FRACTAL_JS),
        ("js/lsystem.js", LSYSTEM_JS),
        ("js/particle.js", PARTICLE_JS),
        ("js/cellular.js", CELLULAR_JS),
        ("js/composition.js", COMPOSITION_JS),
        ("js/territory.js", TERRITORY_JS),
        ("js/game_engine.js", GAME_ENGINE_JS),
        ("js/combat.js", COMBAT_JS),
        ("js/ai.js", AI_JS),
        ("js/scoring.js", SCORING_JS),
        ("js/canvas_renderer.js", CANVAS_RENDERER_JS),
        ("js/ui_system.js", UI_SYSTEM_JS),
        ("js/gallery.js", GALLERY_JS),
        ("js/tutorial.js", TUTORIAL_JS),
        ("js/audio.js", AUDIO_JS),
        ("js/generative_music.js", GENERATIVE_MUSIC_JS),
        ("js/data_layer.js", DATA_LAYER_JS),
        ("js/app.js", APP_JS),
        ("js/animation.js", ANIMATION_JS),
    ]

    static_files = [
        StaticFileSpec(path="css/style.css", content_type="text/css", content=COMBINED_CSS),
    ]
    for path, content in js_modules:
        static_files.append(StaticFileSpec(
            path=path,
            content_type="application/javascript",
            content=content,
        ))

    spec = AppSpec(
        name="chromatic_territories",
        description=(
            "Chromatic Territories Flask app - a game blending gaming with "
            "generative art. Features hex grid territories, color-based combat, "
            "composition scoring, cellular automata evolution, and generative audio."
        ),
        port=port,
        models=models,
        routes=routes,
        templates=templates,
        static_files=static_files,
        config=ConfigSpec(
            secret_key="ct-secret-" + str(int(time.time())),
            database_url="sqlite:///chromatic_territories.db",
            debug=True,
        ),
    )

    print("[4/4] Generating via FlaskAppGenerator (obligations=production)...")
    gen = FlaskAppGenerator(obligations="production")
    result = gen.generate(spec, outdir)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  Flask Generation Complete!")
    print(f"  Output: {outdir}")
    print(f"  Files: {len(result.files_created)}")
    if result.warnings:
        for w in result.warnings:
            print(f"  WARNING: {w}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'=' * 60}")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Generate Chromatic Territories via jugeo-webapp obligation presheaf pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/generate_chromatic_territories.py --target html --outdir ./ct-html\n"
            "  python3 scripts/generate_chromatic_territories.py --target flask --outdir ./ct-flask\n"
            "  python3 scripts/generate_chromatic_territories.py --target both --outdir ./ct\n"
        ),
    )
    parser.add_argument(
        "--target",
        choices=["html", "flask", "both"],
        default="both",
        help="Generation target: html, flask, or both (default: both)",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default=os.path.join(REPO_ROOT, "outputs", "chromatic_territories"),
        help="Output directory (default: outputs/chromatic_territories)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8888,
        help="Port for the generated app (default: 8888)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Chromatic Territories Generator")
    print("  jugeo-webapp obligation presheaf pipeline")
    print("=" * 60)
    print(f"  Target: {args.target}")
    print(f"  Output: {args.outdir}")
    print(f"  Port:   {args.port}")
    print("=" * 60 + "\n")

    if args.target in ("html", "both"):
        html_dir = os.path.join(args.outdir, "html") if args.target == "both" else args.outdir
        generate_html(html_dir, args.port)
        print()

    if args.target in ("flask", "both"):
        flask_dir = os.path.join(args.outdir, "flask") if args.target == "both" else args.outdir
        flask_port = args.port + 1 if args.target == "both" else args.port
        generate_flask(flask_dir, flask_port)
        print()

    print("Done! All generation complete.")


if __name__ == "__main__":
    main()
