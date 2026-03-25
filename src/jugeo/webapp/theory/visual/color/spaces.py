"""Color spaces as coordinate systems on the perceptual color manifold.

Each ``ColorSpace`` names a coordinate chart. A ``Color`` is a point on the
manifold expressed in one chart. Conversion methods are chart transition maps —
diffeomorphisms where the spaces are related by smooth bijections (e.g. sRGB ↔
Linear RGB ↔ XYZ) or topological identifications (hue-wrapping in HSL/HSV).

``ColorBlindnessSimulator`` implements a *functor* from the full trichromatic
color category to a dichromatic sub-category: it maps objects (colors) to
objects and respects composition (convert then simulate == simulate then
convert), but it is **not faithful** because distinct colors in the domain can
collapse to the same image — the map is not injective on hom-sets. Information
(one cone channel) is permanently destroyed; there is no inverse functor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "ColorSpace",
    "Color",
    "ColorDistance",
    "ColorBlindnessSimulator",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _matmul3(m: tuple[tuple[float, ...], ...], v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Multiply a 3×3 matrix (row-major) by a 3-vector."""
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


# ---------------------------------------------------------------------------
# ColorSpace
# ---------------------------------------------------------------------------

class ColorSpace(str, Enum):
    """Named coordinate charts on the perceptual color manifold."""

    SRGB = "srgb"
    LINEAR_RGB = "linear_rgb"
    HSL = "hsl"
    HSV = "hsv"
    XYZ_D65 = "xyz_d65"
    LAB = "lab"
    LCH = "lch"
    OKLAB = "oklab"
    OKLCH = "oklch"
    DISPLAY_P3 = "display_p3"

    def is_perceptually_uniform(self) -> bool:
        """Return True iff this space approximates perceptual uniformity.

        LAB and OKLAB are designed so that equal Euclidean distances correspond
        (approximately) to equal perceived differences. LCH and OKLCH are
        cylindrical reparametrisations of LAB/OKLAB and inherit the property.
        """
        return self in (
            ColorSpace.LAB,
            ColorSpace.LCH,
            ColorSpace.OKLAB,
            ColorSpace.OKLCH,
        )


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------

@dataclass
class Color:
    """A color as a point in a named coordinate chart.

    Channels are intentionally generic (c1/c2/c3) because their meaning depends
    on the space:
        SRGB / LINEAR_RGB / DISPLAY_P3  →  (R, G, B)   ∈ [0, 1]³
        HSL                             →  (H°, S, L)  H ∈ [0°, 360°), S/L ∈ [0, 1]
        HSV                             →  (H°, S, V)
        XYZ_D65                         →  (X, Y, Z)   roughly [0, 1]
        LAB                             →  (L*, a*, b*) L ∈ [0, 100]
        LCH                             →  (L*, C*, H°)
        OKLAB                           →  (L, a, b)   L ∈ [0, 1]
        OKLCH                           →  (L, C, H°)
    """

    c1: float
    c2: float
    c3: float
    space: ColorSpace
    alpha: float = 1.0

    # ------------------------------------------------------------------
    # Internal sRGB ↔ Linear RGB
    # ------------------------------------------------------------------

    @staticmethod
    def _srgb_to_linear(c: float) -> float:
        if c <= 0.04045:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    @staticmethod
    def _linear_to_srgb(c: float) -> float:
        if c <= 0.0031308:
            return 12.92 * c
        return 1.055 * (c ** (1.0 / 2.4)) - 0.055

    # ------------------------------------------------------------------
    # to_linear_rgb
    # ------------------------------------------------------------------

    def to_linear_rgb(self) -> Color:
        """Convert to Linear RGB via sRGB gamma expansion.

        Applies the IEC 61966-2-1 piecewise function to each channel.
        """
        if self.space is ColorSpace.LINEAR_RGB:
            return Color(self.c1, self.c2, self.c3, ColorSpace.LINEAR_RGB, self.alpha)
        if self.space is ColorSpace.SRGB:
            return Color(
                self._srgb_to_linear(self.c1),
                self._srgb_to_linear(self.c2),
                self._srgb_to_linear(self.c3),
                ColorSpace.LINEAR_RGB,
                self.alpha,
            )
        # Route through sRGB first
        return self.to_srgb().to_linear_rgb()

    # ------------------------------------------------------------------
    # to_srgb
    # ------------------------------------------------------------------

    def to_srgb(self) -> Color:
        """Convert to sRGB, applying gamma compression from linear if needed."""
        if self.space is ColorSpace.SRGB:
            return Color(self.c1, self.c2, self.c3, ColorSpace.SRGB, self.alpha)
        if self.space is ColorSpace.LINEAR_RGB:
            return Color(
                self._linear_to_srgb(_clamp(self.c1)),
                self._linear_to_srgb(_clamp(self.c2)),
                self._linear_to_srgb(_clamp(self.c3)),
                ColorSpace.SRGB,
                self.alpha,
            )
        if self.space is ColorSpace.HSL:
            return self._hsl_to_srgb()
        if self.space is ColorSpace.HSV:
            return self._hsv_to_srgb()
        if self.space is ColorSpace.DISPLAY_P3:
            return self._display_p3_to_srgb()
        # General: go through XYZ → Linear RGB → sRGB
        return self.to_xyz().to_linear_rgb().to_srgb()

    # ------------------------------------------------------------------
    # to_hsl
    # ------------------------------------------------------------------

    def to_hsl(self) -> Color:
        """Convert to HSL from sRGB."""
        if self.space is ColorSpace.HSL:
            return Color(self.c1, self.c2, self.c3, ColorSpace.HSL, self.alpha)
        rgb = self.to_srgb()
        r, g, b = rgb.c1, rgb.c2, rgb.c3
        cmax = max(r, g, b)
        cmin = min(r, g, b)
        delta = cmax - cmin
        l = (cmax + cmin) / 2.0

        if delta == 0.0:
            h, s = 0.0, 0.0
        else:
            s = delta / (1.0 - abs(2.0 * l - 1.0))
            if cmax == r:
                h = 60.0 * (((g - b) / delta) % 6)
            elif cmax == g:
                h = 60.0 * ((b - r) / delta + 2)
            else:
                h = 60.0 * ((r - g) / delta + 4)

        return Color(h % 360.0, s, l, ColorSpace.HSL, self.alpha)

    def _hsl_to_srgb(self) -> Color:
        h, s, l = self.c1, self.c2, self.c3
        c = (1.0 - abs(2.0 * l - 1.0)) * s
        x = c * (1.0 - abs((h / 60.0) % 2 - 1.0))
        m = l - c / 2.0
        sector = int(h / 60) % 6
        pairs = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)]
        r1, g1, b1 = pairs[sector]
        return Color(r1 + m, g1 + m, b1 + m, ColorSpace.SRGB, self.alpha)

    def _hsv_to_srgb(self) -> Color:
        h, s, v = self.c1, self.c2, self.c3
        c = v * s
        x = c * (1.0 - abs((h / 60.0) % 2 - 1.0))
        m = v - c
        sector = int(h / 60) % 6
        pairs = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)]
        r1, g1, b1 = pairs[sector]
        return Color(r1 + m, g1 + m, b1 + m, ColorSpace.SRGB, self.alpha)

    # ------------------------------------------------------------------
    # to_xyz (XYZ D65)
    # ------------------------------------------------------------------

    # Standard IEC 61966-2-1 sRGB → XYZ D65 matrix
    _M_RGB_TO_XYZ = (
        (0.4124564, 0.3575761, 0.1804375),
        (0.2126729, 0.7151522, 0.0721750),
        (0.0193339, 0.1191920, 0.9503041),
    )

    def to_xyz(self) -> Color:
        """Convert to XYZ D65 via the standard sRGB primaries matrix."""
        if self.space is ColorSpace.XYZ_D65:
            return Color(self.c1, self.c2, self.c3, ColorSpace.XYZ_D65, self.alpha)
        lin = self.to_linear_rgb()
        x, y, z = _matmul3(self._M_RGB_TO_XYZ, (lin.c1, lin.c2, lin.c3))
        return Color(x, y, z, ColorSpace.XYZ_D65, self.alpha)

    # ------------------------------------------------------------------
    # to_lab (CIE L*a*b*)
    # ------------------------------------------------------------------

    # D65 white point
    _Xn, _Yn, _Zn = 0.95047, 1.00000, 1.08883

    @staticmethod
    def _f_lab(t: float) -> float:
        delta = 6.0 / 29.0
        if t > delta ** 3:
            return t ** (1.0 / 3.0)
        return t / (3.0 * delta ** 2) + 4.0 / 29.0

    def to_lab(self) -> Color:
        """Convert to CIE L*a*b* using the D65 white point."""
        if self.space is ColorSpace.LAB:
            return Color(self.c1, self.c2, self.c3, ColorSpace.LAB, self.alpha)
        xyz = self.to_xyz()
        fx = self._f_lab(xyz.c1 / self._Xn)
        fy = self._f_lab(xyz.c2 / self._Yn)
        fz = self._f_lab(xyz.c3 / self._Zn)
        L = 116.0 * fy - 16.0
        a = 500.0 * (fx - fy)
        b = 200.0 * (fy - fz)
        return Color(L, a, b, ColorSpace.LAB, self.alpha)

    def _lab_to_xyz(self) -> Color:
        L, a, b = self.c1, self.c2, self.c3
        fy = (L + 16.0) / 116.0
        fx = a / 500.0 + fy
        fz = fy - b / 200.0
        delta = 6.0 / 29.0

        def finv(t: float) -> float:
            return t ** 3 if t > delta else 3.0 * delta ** 2 * (t - 4.0 / 29.0)

        return Color(
            finv(fx) * self._Xn,
            finv(fy) * self._Yn,
            finv(fz) * self._Zn,
            ColorSpace.XYZ_D65,
            self.alpha,
        )

    # ------------------------------------------------------------------
    # to_lch
    # ------------------------------------------------------------------

    def to_lch(self) -> Color:
        """Convert Lab to LCH (cylindrical reparametrisation)."""
        if self.space is ColorSpace.LCH:
            return Color(self.c1, self.c2, self.c3, ColorSpace.LCH, self.alpha)
        lab = self.to_lab()
        L, a, b = lab.c1, lab.c2, lab.c3
        C = math.sqrt(a * a + b * b)
        H = math.degrees(math.atan2(b, a)) % 360.0
        return Color(L, C, H, ColorSpace.LCH, self.alpha)

    def _lch_to_lab(self) -> Color:
        L, C, H = self.c1, self.c2, self.c3
        h_rad = math.radians(H)
        return Color(L, C * math.cos(h_rad), C * math.sin(h_rad), ColorSpace.LAB, self.alpha)

    # ------------------------------------------------------------------
    # to_oklab (Björn Ottosson, 2020)
    # ------------------------------------------------------------------

    # XYZ D65 → LMS (approximate equal-energy adaption for OKLab)
    _M_XYZ_TO_LMS = (
        ( 0.8189330101,  0.3618667424, -0.1288597137),
        ( 0.0329845436,  0.9293118715,  0.0361456387),
        ( 0.0482003018,  0.2643662691,  0.6338517070),
    )
    # LMS' (cube-rooted) → OKLab
    _M_LMS_TO_OKLAB = (
        (0.2104542553, 0.7936177850, -0.0040720468),
        (1.9779984951, -2.4285922050,  0.4505937099),
        (0.0259040371,  0.7827717662, -0.8086757660),
    )

    def to_oklab(self) -> Color:
        """Convert to OKLab (Björn Ottosson 2020).

        Pipeline: sRGB → Linear RGB → XYZ D65 → LMS → LMS^(1/3) → OKLab.
        """
        if self.space is ColorSpace.OKLAB:
            return Color(self.c1, self.c2, self.c3, ColorSpace.OKLAB, self.alpha)
        xyz = self.to_xyz()
        lms = _matmul3(self._M_XYZ_TO_LMS, (xyz.c1, xyz.c2, xyz.c3))
        lms_g = (
            math.copysign(abs(lms[0]) ** (1.0 / 3.0), lms[0]),
            math.copysign(abs(lms[1]) ** (1.0 / 3.0), lms[1]),
            math.copysign(abs(lms[2]) ** (1.0 / 3.0), lms[2]),
        )
        L, a, b = _matmul3(self._M_LMS_TO_OKLAB, lms_g)
        return Color(L, a, b, ColorSpace.OKLAB, self.alpha)

    def _oklab_to_xyz(self) -> Color:
        # Inverse of _M_LMS_TO_OKLAB (Ottosson's published inverse)
        _M_OKLAB_TO_LMS = (
            (1.0000000000,  0.3963377774,  0.2158037573),
            (1.0000000000, -0.1055613458, -0.0638541728),
            (1.0000000000, -0.0894841775, -1.2914855480),
        )
        # Inverse of _M_XYZ_TO_LMS
        _M_LMS_TO_XYZ = (
            ( 1.2270138511035211, -0.5577999806518222,  0.2812561489664678),
            (-0.0405801784232806,  1.1122568696168302, -0.0716766786656012),
            (-0.0763812845057069, -0.4214819784180127,  1.5861632204407947),
        )
        lms_g = _matmul3(_M_OKLAB_TO_LMS, (self.c1, self.c2, self.c3))
        lms = (lms_g[0] ** 3, lms_g[1] ** 3, lms_g[2] ** 3)
        x, y, z = _matmul3(_M_LMS_TO_XYZ, lms)
        return Color(x, y, z, ColorSpace.XYZ_D65, self.alpha)

    def to_oklch(self) -> Color:
        """Convert to OKLCH (cylindrical OKLab)."""
        if self.space is ColorSpace.OKLCH:
            return Color(self.c1, self.c2, self.c3, ColorSpace.OKLCH, self.alpha)
        ok = self.to_oklab()
        L, a, b = ok.c1, ok.c2, ok.c3
        C = math.sqrt(a * a + b * b)
        H = math.degrees(math.atan2(b, a)) % 360.0
        return Color(L, C, H, ColorSpace.OKLCH, self.alpha)

    def _oklch_to_oklab(self) -> Color:
        L, C, H = self.c1, self.c2, self.c3
        h_rad = math.radians(H)
        return Color(L, C * math.cos(h_rad), C * math.sin(h_rad), ColorSpace.OKLAB, self.alpha)

    # ------------------------------------------------------------------
    # Display P3
    # ------------------------------------------------------------------

    # Display P3 uses the same gamma as sRGB but different primaries.
    # P3 → XYZ D65 matrix (from ICC / CSS Color 4 spec)
    _M_P3_TO_XYZ = (
        (0.4865709486482162, 0.26566769316909306, 0.1982172852343625),
        (0.2289745640697488, 0.6917385218376185,  0.0792869140926328),
        (0.0000000000000000, 0.0451133818589684,  1.0439443689955315),
    )

    def _display_p3_to_srgb(self) -> Color:
        # P3 uses the same transfer function as sRGB; linearise, convert primaries, reapply gamma
        lr = self._srgb_to_linear(self.c1)
        lg = self._srgb_to_linear(self.c2)
        lb = self._srgb_to_linear(self.c3)
        x, y, z = _matmul3(self._M_P3_TO_XYZ, (lr, lg, lb))
        # XYZ → linear sRGB (inverse of _M_RGB_TO_XYZ, IEC 61966-2-1)
        _M_XYZ_TO_LINEAR_RGB = (
            ( 3.2404542, -1.5371385, -0.4985314),
            (-0.9692660,  1.8760108,  0.0415560),
            ( 0.0556434, -0.2040259,  1.0572252),
        )
        lr2, lg2, lb2 = _matmul3(_M_XYZ_TO_LINEAR_RGB, (x, y, z))
        return Color(
            self._linear_to_srgb(_clamp(lr2)),
            self._linear_to_srgb(_clamp(lg2)),
            self._linear_to_srgb(_clamp(lb2)),
            ColorSpace.SRGB,
            self.alpha,
        )

    # ------------------------------------------------------------------
    # General conversion router
    # ------------------------------------------------------------------

    def to(self, space: ColorSpace) -> Color:
        """Convert to an arbitrary target space, routing through intermediates."""
        if self.space is space:
            return Color(self.c1, self.c2, self.c3, space, self.alpha)
        # Normalise: bring uncommon source spaces to a common pivot first
        if self.space is ColorSpace.LCH:
            return self._lch_to_lab().to(space)
        if self.space is ColorSpace.OKLCH:
            return self._oklch_to_oklab().to(space)
        if self.space is ColorSpace.OKLAB:
            if space is ColorSpace.OKLCH:
                return self.to_oklch()
            return self._oklab_to_xyz().to(space)
        if self.space is ColorSpace.LAB:
            if space is ColorSpace.LCH:
                return self.to_lch()
            return self._lab_to_xyz().to(space)
        if self.space is ColorSpace.XYZ_D65:
            if space is ColorSpace.LAB:
                return self.to_lab()
            if space is ColorSpace.OKLAB:
                return self.to_oklab()
            if space is ColorSpace.LCH:
                return self.to_lab().to_lch()
            if space is ColorSpace.OKLCH:
                return self.to_oklab().to_oklch()
            # XYZ → Linear RGB → sRGB/HSL/…
            _M_XYZ_TO_LINEAR_RGB = (
                ( 3.2404542, -1.5371385, -0.4985314),
                (-0.9692660,  1.8760108,  0.0415560),
                ( 0.0556434, -0.2040259,  1.0572252),
            )
            lr, lg, lb = _matmul3(_M_XYZ_TO_LINEAR_RGB, (self.c1, self.c2, self.c3))
            lin = Color(lr, lg, lb, ColorSpace.LINEAR_RGB, self.alpha)
            return lin.to(space)
        # Default pivot: go through sRGB
        dispatch = {
            ColorSpace.SRGB:        self.to_srgb,
            ColorSpace.LINEAR_RGB:  self.to_linear_rgb,
            ColorSpace.HSL:         self.to_hsl,
            ColorSpace.XYZ_D65:     self.to_xyz,
            ColorSpace.LAB:         self.to_lab,
            ColorSpace.LCH:         self.to_lch,
            ColorSpace.OKLAB:       self.to_oklab,
            ColorSpace.OKLCH:       self.to_oklch,
        }
        if space in dispatch:
            return dispatch[space]()
        # HSV not reachable from sRGB route above; add explicit path
        if space is ColorSpace.HSV:
            return self._to_hsv()
        if space is ColorSpace.DISPLAY_P3:
            raise NotImplementedError("Conversion to Display P3 not yet implemented")
        raise ValueError(f"Unknown target space: {space}")

    def _to_hsv(self) -> Color:
        rgb = self.to_srgb()
        r, g, b = rgb.c1, rgb.c2, rgb.c3
        cmax = max(r, g, b)
        cmin = min(r, g, b)
        delta = cmax - cmin
        v = cmax
        s = 0.0 if cmax == 0.0 else delta / cmax
        if delta == 0.0:
            h = 0.0
        elif cmax == r:
            h = 60.0 * (((g - b) / delta) % 6)
        elif cmax == g:
            h = 60.0 * ((b - r) / delta + 2)
        else:
            h = 60.0 * ((r - g) / delta + 4)
        return Color(h % 360.0, s, v, ColorSpace.HSV, self.alpha)


# ---------------------------------------------------------------------------
# ColorDistance
# ---------------------------------------------------------------------------

class ColorDistance:
    """Distance functionals on the color manifold."""

    @staticmethod
    def euclidean(c1: Color, c2: Color) -> float:
        """Euclidean distance in the shared coordinate chart.

        Meaningful for perceptually uniform spaces (LAB, OKLAB, LCH, OKLCH);
        use with care in non-uniform spaces like sRGB.
        """
        if c1.space is not c2.space:
            raise ValueError(
                f"Cannot compute Euclidean distance between different spaces "
                f"({c1.space} and {c2.space}); convert first."
            )
        return math.sqrt(
            (c1.c1 - c2.c1) ** 2
            + (c1.c2 - c2.c2) ** 2
            + (c1.c3 - c2.c3) ** 2
        )

    @staticmethod
    def ciede2000(lab1: Color, lab2: Color) -> float:
        """CIEDE2000 color difference (Sharma et al., 2005).

        Full formula with kL = kC = kH = 1.  Includes the RT rotation term that
        corrects blue-region hue–chroma interaction.  Both inputs must be in the
        LAB space (or will be converted).

        References: G. Sharma, W. Wu, E. N. Dalal, Color Research & Application,
        30(1):21-30, 2005.
        """
        if lab1.space is not ColorSpace.LAB:
            lab1 = lab1.to_lab()
        if lab2.space is not ColorSpace.LAB:
            lab2 = lab2.to_lab()

        L1, a1, b1 = lab1.c1, lab1.c2, lab1.c3
        L2, a2, b2 = lab2.c1, lab2.c2, lab2.c3

        # Step 1: C'ab and h'ab
        C1 = math.sqrt(a1 ** 2 + b1 ** 2)
        C2 = math.sqrt(a2 ** 2 + b2 ** 2)
        C_avg7 = ((C1 + C2) / 2.0) ** 7
        G = 0.5 * (1.0 - math.sqrt(C_avg7 / (C_avg7 + 25.0 ** 7)))
        a1p = a1 * (1.0 + G)
        a2p = a2 * (1.0 + G)
        C1p = math.sqrt(a1p ** 2 + b1 ** 2)
        C2p = math.sqrt(a2p ** 2 + b2 ** 2)

        h1p = math.degrees(math.atan2(b1, a1p)) % 360.0
        h2p = math.degrees(math.atan2(b2, a2p)) % 360.0

        # Step 2: deltas
        dLp = L2 - L1
        dCp = C2p - C1p

        if C1p * C2p == 0.0:
            dhp = 0.0
        elif abs(h2p - h1p) <= 180.0:
            dhp = h2p - h1p
        elif h2p - h1p > 180.0:
            dhp = h2p - h1p - 360.0
        else:
            dhp = h2p - h1p + 360.0

        dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))

        # Step 3: CIEDE2000
        Lp_avg = (L1 + L2) / 2.0
        Cp_avg = (C1p + C2p) / 2.0

        if C1p * C2p == 0.0:
            Hp_avg = h1p + h2p
        elif abs(h1p - h2p) <= 180.0:
            Hp_avg = (h1p + h2p) / 2.0
        elif h1p + h2p < 360.0:
            Hp_avg = (h1p + h2p + 360.0) / 2.0
        else:
            Hp_avg = (h1p + h2p - 360.0) / 2.0

        T = (
            1.0
            - 0.17 * math.cos(math.radians(Hp_avg - 30.0))
            + 0.24 * math.cos(math.radians(2.0 * Hp_avg))
            + 0.32 * math.cos(math.radians(3.0 * Hp_avg + 6.0))
            - 0.20 * math.cos(math.radians(4.0 * Hp_avg - 63.0))
        )

        SL = 1.0 + 0.015 * (Lp_avg - 50.0) ** 2 / math.sqrt(20.0 + (Lp_avg - 50.0) ** 2)
        SC = 1.0 + 0.045 * Cp_avg
        SH = 1.0 + 0.015 * Cp_avg * T

        Cp_avg7 = Cp_avg ** 7
        RC = 2.0 * math.sqrt(Cp_avg7 / (Cp_avg7 + 25.0 ** 7))
        d_theta = 30.0 * math.exp(-((Hp_avg - 275.0) / 25.0) ** 2)
        RT = -math.sin(math.radians(2.0 * d_theta)) * RC

        return math.sqrt(
            (dLp / SL) ** 2
            + (dCp / SC) ** 2
            + (dHp / SH) ** 2
            + RT * (dCp / SC) * (dHp / SH)
        )

    @staticmethod
    def wcag_contrast(c1: Color, c2: Color) -> float:
        """WCAG 2.1 relative luminance contrast ratio.

        Returns (L_lighter + 0.05) / (L_darker + 0.05) where relative luminance
        L = 0.2126 R + 0.7152 G + 0.0722 B in linear (scene-referred) RGB.
        The ratio is always ≥ 1.0; 21.0 is the maximum (black vs. white).
        """
        def relative_luminance(c: Color) -> float:
            lin = c.to_linear_rgb()
            return 0.2126 * lin.c1 + 0.7152 * lin.c2 + 0.0722 * lin.c3

        l1 = relative_luminance(c1)
        l2 = relative_luminance(c2)
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# ColorBlindnessSimulator
# ---------------------------------------------------------------------------

class ColorBlindnessSimulator:
    """Dichromacy simulation as an information-losing functor.

    **Category-theoretic perspective**: The full trichromatic color category has
    objects (colors) and morphisms (perceptual indistinguishability relations,
    color transformations, etc.).  Each simulation method is a *functor*
    F : Trichromat → Dichromat that

    * maps every color C to a color F(C) in a reduced gamut;
    * preserves composition: F(g ∘ f) = F(g) ∘ F(f);

    but is **not faithful** (hence not an equivalence of categories): distinct
    colors C₁ ≠ C₂ in the trichromat world may satisfy F(C₁) = F(C₂) because
    an entire *confusion line* collapses to a single point on the *copunctal
    point*.  The functor loses the dimension corresponding to the missing cone,
    so the hom-sets are not injectively preserved.  There is no right-adjoint
    (no "inverse functor") because the information is irreversibly destroyed.

    Implementation follows Brettel, Viénot & Mollon (1997), projecting LMS
    coordinates onto the confusion-line plane through the copunctal point for
    each dichromacy type.
    """

    # LMS → sRGB and sRGB → LMS (Hunt–Pointer–Estévez, D65, from Viénot 1999)
    _M_RGB_TO_LMS = (
        (0.31399022, 0.63951294, 0.04649755),
        (0.15537241, 0.75789446, 0.08670142),
        (0.01775239, 0.10944209, 0.87256922),
    )
    _M_LMS_TO_RGB = (
        ( 5.47221206, -4.64196010,  0.16963708),
        (-1.12524190,  2.29317094, -0.16789520),
        ( 0.02980165, -0.19318073,  1.16364789),
    )

    def _srgb_to_lms(self, c: Color) -> tuple[float, float, float]:
        lin = c.to_linear_rgb()
        return _matmul3(self._M_RGB_TO_LMS, (lin.c1, lin.c2, lin.c3))

    def _lms_to_color(self, L: float, M: float, S: float, alpha: float) -> Color:
        r, g, b = _matmul3(self._M_LMS_TO_RGB, (L, M, S))
        lin = Color(_clamp(r), _clamp(g), _clamp(b), ColorSpace.LINEAR_RGB, alpha)
        return lin.to_srgb()

    def simulate_protanopia(self, color: Color) -> Color:
        """Simulate protanopia (missing L / red cone).

        The L cone is absent; the confusion locus collapses all L-variation onto
        the (M, S) plane through the tritanopic copunctal point in LMS space.
        Brettel 1997 projection: L = (M * 2.02344 - 2.52581 * S) for the
        primary plane; simplified single-plane version used here.
        """
        L, M, S = self._srgb_to_lms(color)
        # Project L onto the M-S plane (Brettel/Viénot single-plane simplification)
        Lp = 2.02344 * M - 2.52581 * S
        return self._lms_to_color(Lp, M, S, color.alpha)

    def simulate_deuteranopia(self, color: Color) -> Color:
        """Simulate deuteranopia (missing M / green cone).

        The M cone is absent; confusion lines run through the deuteropic
        copunctal point.  Projection: M = 0.494207 * L + 1.24827 * S.
        """
        L, M, S = self._srgb_to_lms(color)
        Mp = 0.494207 * L + 1.24827 * S
        return self._lms_to_color(L, Mp, S, color.alpha)

    def simulate_tritanopia(self, color: Color) -> Color:
        """Simulate tritanopia (missing S / blue cone).

        The S cone is absent; confusion lines run through the tritanopic
        copunctal point (near yellow).  Projection: S = -0.395913 * L + 0.801109 * M.
        """
        L, M, S = self._srgb_to_lms(color)
        Sp = -0.395913 * L + 0.801109 * M
        return self._lms_to_color(L, M, Sp, color.alpha)
