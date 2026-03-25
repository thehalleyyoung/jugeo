"""
CSS/JS animation modelled as paths in visual state space.

Continuous animations are NOT state machines — they are solutions to ODEs.
This module implements the actual mathematics:

  • CubicBezier  — CSS timing-function as a reparameterisation of [0,1]
  • SpringSystem — damped harmonic oscillator (analytical ODE solution)
  • KeyframeAnimation — piecewise path in CSS property space
  • ScrollLinkedAnimation — scroll position as the time parameter
"""

from __future__ import annotations

__all__ = [
    "CubicBezier",
    "SpringSystem",
    "KeyframeAnimation",
    "ScrollLinkedAnimation",
]

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 1. CubicBezier
# ---------------------------------------------------------------------------

@dataclass
class CubicBezier:
    """CSS cubic-bezier easing function.

    The curve is defined by four points:
        P0 = (0, 0)   — fixed start
        P1 = (p1x, p1y) — first control point
        P2 = (p2x, p2y) — second control point
        P3 = (1, 1)   — fixed end

    The CSS progress x ∈ [0,1] lives on the *x*-axis of the bezier.
    We invert x → t with Newton-Raphson, then read off y(t).
    """

    p1x: float
    p1y: float
    p2x: float
    p2y: float

    # ------------------------------------------------------------------
    # Bezier primitives
    # ------------------------------------------------------------------

    def sample_x(self, t: float) -> float:
        """Cubic bezier x-coordinate at parameter t ∈ [0,1]."""
        mt = 1.0 - t
        return 3.0 * self.p1x * mt * mt * t + 3.0 * self.p2x * mt * t * t + t * t * t

    def sample_y(self, t: float) -> float:
        """Cubic bezier y-coordinate at parameter t ∈ [0,1]."""
        mt = 1.0 - t
        return 3.0 * self.p1y * mt * mt * t + 3.0 * self.p2y * mt * t * t + t * t * t

    def _sample_x_derivative(self, t: float) -> float:
        """dx/dt at parameter t."""
        mt = 1.0 - t
        return (
            3.0 * self.p1x * mt * mt
            - 6.0 * self.p1x * mt * t
            + 3.0 * self.p2x * (mt * mt - 2.0 * mt * t)  # chain rule factored
            + 3.0 * t * t
        )
        # Cleaner factored form:
        # d/dt [ 3*p1x*(1-t)^2*t + 3*p2x*(1-t)*t^2 + t^3 ]
        # = 3*p1x*(1-3t^2+2t^3)' ... easier via polynomial form:

    def _dx_dt(self, t: float) -> float:
        """dx/dt — used by Newton-Raphson."""
        mt = 1.0 - t
        # Derivative of: 3*p1x*(1-t)^2*t + 3*p2x*(1-t)*t^2 + t^3
        return (
            3.0 * self.p1x * (mt * mt - 2.0 * mt * t)
            + 3.0 * self.p2x * (2.0 * mt * t - t * t)
            + 3.0 * t * t
        )

    def solve(self, x: float, tolerance: float = 1e-6) -> float:
        """Given CSS progress *x* ∈ [0,1], return the eased value y ∈ [0,1].

        Implements the same Newton-Raphson inversion that browsers use:
        find t such that bezier_x(t) = x, then return bezier_y(t).
        Falls back to bisection when the derivative is too small.
        """
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0

        # Initial guess: t ≈ x  (exact for linear easing)
        t = x

        for _ in range(8):
            fx = self.sample_x(t) - x
            if abs(fx) < tolerance:
                return self.sample_y(t)
            dfx = self._dx_dt(t)
            if abs(dfx) < 1e-12:
                break
            t -= fx / dfx
            t = max(0.0, min(1.0, t))

        # Bisection fallback (guaranteed convergence)
        lo, hi = 0.0, 1.0
        t = x
        while hi - lo > tolerance:
            fx = self.sample_x(t) - x
            if abs(fx) < tolerance:
                break
            if fx < 0.0:
                lo = t
            else:
                hi = t
            t = 0.5 * (lo + hi)

        return self.sample_y(t)

    # ------------------------------------------------------------------
    # Named CSS presets
    # ------------------------------------------------------------------

    @classmethod
    def ease(cls) -> CubicBezier:
        """CSS ease — (0.25, 0.1, 0.25, 1.0)."""
        return cls(0.25, 0.1, 0.25, 1.0)

    @classmethod
    def ease_in(cls) -> CubicBezier:
        """CSS ease-in — (0.42, 0.0, 1.0, 1.0)."""
        return cls(0.42, 0.0, 1.0, 1.0)

    @classmethod
    def ease_out(cls) -> CubicBezier:
        """CSS ease-out — (0.0, 0.0, 0.58, 1.0)."""
        return cls(0.0, 0.0, 0.58, 1.0)

    @classmethod
    def ease_in_out(cls) -> CubicBezier:
        """CSS ease-in-out — (0.42, 0.0, 0.58, 1.0)."""
        return cls(0.42, 0.0, 0.58, 1.0)

    @classmethod
    def linear(cls) -> CubicBezier:
        """Linear (no easing) — (0.0, 0.0, 1.0, 1.0)."""
        return cls(0.0, 0.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# 2. SpringSystem  —  damped harmonic oscillator
# ---------------------------------------------------------------------------

@dataclass
class SpringSystem:
    """Damped harmonic oscillator: m*x'' + c*x' + k*x = 0.

    The ODE has an *analytical* solution for all three damping regimes.
    No numerical integration — exact formulae only.

    State: displacement x(t) from the equilibrium (target) position.
    At t=0: x(0) = x0  (initial displacement),  x'(0) = v0.

    Parameters
    ----------
    mass       : float  — kg, inertia of the animated element (default 1)
    stiffness  : float  — N/m, spring constant k
    damping    : float  — N·s/m, damping coefficient c
    """

    mass: float = 1.0
    stiffness: float = 100.0
    damping: float = 10.0

    # ------------------------------------------------------------------
    # Derived physical quantities
    # ------------------------------------------------------------------

    def natural_frequency(self) -> float:
        """ω₀ = √(k/m)  [rad/s]."""
        return math.sqrt(self.stiffness / self.mass)

    def damping_ratio(self) -> float:
        """ζ = c / (2√(km))  — dimensionless."""
        return self.damping / (2.0 * math.sqrt(self.stiffness * self.mass))

    def is_underdamped(self) -> bool:
        """ζ < 1 — system oscillates around equilibrium."""
        return self.damping_ratio() < 1.0

    def is_critically_damped(self) -> bool:
        """ζ = 1 — fastest return without oscillation."""
        return math.isclose(self.damping_ratio(), 1.0, rel_tol=1e-9)

    def is_overdamped(self) -> bool:
        """ζ > 1 — slow monotonic return."""
        return self.damping_ratio() > 1.0

    # ------------------------------------------------------------------
    # Analytical ODE solutions
    # ------------------------------------------------------------------

    def _coefficients(
        self, x0: float, v0: float
    ) -> tuple[float, float, float, float, float]:
        """Return (omega_0, zeta, omega_d, A, B) for the chosen regime."""
        omega_0 = self.natural_frequency()
        zeta = self.damping_ratio()

        if zeta < 1.0:
            # Underdamped: ω_d = ω₀√(1 - ζ²)
            omega_d = omega_0 * math.sqrt(1.0 - zeta * zeta)
            A = x0
            B = (v0 + zeta * omega_0 * x0) / omega_d
        elif math.isclose(zeta, 1.0, rel_tol=1e-9):
            # Critically damped
            omega_d = 0.0
            A = x0
            B = v0 + omega_0 * x0
        else:
            # Overdamped: two real roots r1, r2
            sqrt_term = math.sqrt(zeta * zeta - 1.0)
            r1 = omega_0 * (-zeta + sqrt_term)
            r2 = omega_0 * (-zeta - sqrt_term)
            # x(t) = A*e^(r1*t) + B*e^(r2*t)
            # x(0) = A + B = x0
            # x'(0) = A*r1 + B*r2 = v0
            A = (v0 - r2 * x0) / (r1 - r2)
            B = x0 - A
            # Re-use omega_d slot for r1, and store r2 separately
            # We'll handle overdamped specially in position_at
            return omega_0, zeta, r1, A, B  # type: ignore[return-value]

        return omega_0, zeta, omega_d, A, B

    def position_at(self, t: float, x0: float = 1.0, v0: float = 0.0) -> float:
        """Displacement x(t) — exact analytical solution.

        Parameters
        ----------
        t  : time in seconds (t ≥ 0)
        x0 : initial displacement from equilibrium (default 1.0)
        v0 : initial velocity (default 0.0)
        """
        if t < 0.0:
            return x0

        omega_0 = self.natural_frequency()
        zeta = self.damping_ratio()

        if zeta < 1.0:
            # Underdamped
            omega_d = omega_0 * math.sqrt(1.0 - zeta * zeta)
            A = x0
            B = (v0 + zeta * omega_0 * x0) / omega_d
            decay = math.exp(-zeta * omega_0 * t)
            return decay * (A * math.cos(omega_d * t) + B * math.sin(omega_d * t))

        elif math.isclose(zeta, 1.0, rel_tol=1e-9):
            # Critically damped
            A = x0
            B = v0 + omega_0 * x0
            return (A + B * t) * math.exp(-omega_0 * t)

        else:
            # Overdamped
            sqrt_term = math.sqrt(zeta * zeta - 1.0)
            r1 = omega_0 * (-zeta + sqrt_term)
            r2 = omega_0 * (-zeta - sqrt_term)
            A = (v0 - r2 * x0) / (r1 - r2)
            B = x0 - A
            return A * math.exp(r1 * t) + B * math.exp(r2 * t)

    def velocity_at(self, t: float, x0: float = 1.0, v0: float = 0.0) -> float:
        """Velocity x'(t) — exact analytical derivative.

        Parameters
        ----------
        t  : time in seconds (t ≥ 0)
        x0 : initial displacement from equilibrium (default 1.0)
        v0 : initial velocity (default 0.0)
        """
        if t < 0.0:
            return v0

        omega_0 = self.natural_frequency()
        zeta = self.damping_ratio()

        if zeta < 1.0:
            omega_d = omega_0 * math.sqrt(1.0 - zeta * zeta)
            A = x0
            B = (v0 + zeta * omega_0 * x0) / omega_d
            alpha = zeta * omega_0
            decay = math.exp(-alpha * t)
            cos_t = math.cos(omega_d * t)
            sin_t = math.sin(omega_d * t)
            # d/dt [ e^(-α t) (A cos ω_d t + B sin ω_d t) ]
            return decay * (
                (-alpha * A + omega_d * B) * cos_t
                + (-alpha * B - omega_d * A) * sin_t
            )

        elif math.isclose(zeta, 1.0, rel_tol=1e-9):
            A = x0
            B = v0 + omega_0 * x0
            # d/dt [ (A + Bt) e^(-ω₀ t) ] = (B - ω₀(A + Bt)) e^(-ω₀ t)
            return (B - omega_0 * (A + B * t)) * math.exp(-omega_0 * t)

        else:
            sqrt_term = math.sqrt(zeta * zeta - 1.0)
            r1 = omega_0 * (-zeta + sqrt_term)
            r2 = omega_0 * (-zeta - sqrt_term)
            A = (v0 - r2 * x0) / (r1 - r2)
            B = x0 - A
            return A * r1 * math.exp(r1 * t) + B * r2 * math.exp(r2 * t)

    def settle_time(self, threshold: float = 0.001, x0: float = 1.0) -> float:
        """Time until |x(t)| < threshold (seconds), found by binary search.

        We first probe exponentially to bracket the settle time, then refine
        with bisection.  Returns 0.0 if already settled at t=0.
        """
        if abs(self.position_at(0.0, x0)) < threshold:
            return 0.0

        # Exponential probe to find an upper bound
        t_hi = 0.1
        for _ in range(40):
            if abs(self.position_at(t_hi, x0)) < threshold:
                break
            t_hi *= 2.0
        else:
            # Could not settle within reasonable time
            return t_hi

        # Bisection to refine
        t_lo = 0.0
        for _ in range(60):
            t_mid = 0.5 * (t_lo + t_hi)
            if abs(self.position_at(t_mid, x0)) < threshold:
                t_hi = t_mid
            else:
                t_lo = t_mid
            if t_hi - t_lo < 1e-9:
                break

        return t_hi


# ---------------------------------------------------------------------------
# 3. KeyframeAnimation
# ---------------------------------------------------------------------------

@dataclass
class KeyframeAnimation:
    """Piecewise CSS @keyframes path in property space.

    A keyframe animation is parameterised by *progress* ∈ [0,1] which maps
    to a CSS property value.  The easing function is applied per-segment
    (between consecutive keyframes) to reparameterise local progress.

    Parameters
    ----------
    property_name : str
        CSS property being animated (e.g. 'opacity', 'transform').
    keyframes     : list[tuple[float, float]]
        Sorted list of (progress, value) pairs.  progress ∈ [0,1].
    easing        : CubicBezier
        Timing function applied within each keyframe segment.
    duration_ms   : float
        Total animation duration in milliseconds.
    delay_ms      : float
        Delay before the animation starts (ms).  Default 0.
    fill_mode     : str
        CSS fill-mode: "none" | "forwards" | "backwards" | "both".
    """

    property_name: str
    keyframes: list[tuple[float, float]]
    easing: CubicBezier
    duration_ms: float
    delay_ms: float = 0.0
    fill_mode: str = "none"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """True iff keyframes are sorted and span the full [0,1] range."""
        if len(self.keyframes) < 2:
            return False
        progresses = [kf[0] for kf in self.keyframes]
        if not math.isclose(progresses[0], 0.0, abs_tol=1e-9):
            return False
        if not math.isclose(progresses[-1], 1.0, abs_tol=1e-9):
            return False
        return all(a <= b for a, b in zip(progresses, progresses[1:]))

    # ------------------------------------------------------------------
    # Interpolation
    # ------------------------------------------------------------------

    def value_at(self, time_ms: float) -> float:
        """CSS property value at *time_ms* milliseconds from time-origin.

        Handles fill_mode semantics:
          - "none"      : outside [delay, delay+duration] → value undefined
                          (returns boundary value as a sane default)
          - "forwards"  : after the animation, hold the final value
          - "backwards" : before the animation, apply the first keyframe
          - "both"      : forwards + backwards combined
        """
        active_start = self.delay_ms
        active_end = self.delay_ms + self.duration_ms

        # Determine progress in [0,1] along the animation timeline
        if time_ms < active_start:
            if self.fill_mode in ("backwards", "both"):
                progress = 0.0
            else:
                return self.keyframes[0][1]
        elif time_ms > active_end:
            if self.fill_mode in ("forwards", "both"):
                progress = 1.0
            else:
                return self.keyframes[-1][1]
        else:
            elapsed = time_ms - active_start
            progress = elapsed / self.duration_ms if self.duration_ms > 0.0 else 1.0
            progress = max(0.0, min(1.0, progress))

        return self._interpolate(progress)

    def _interpolate(self, progress: float) -> float:
        """Find the keyframe segment containing *progress* and interpolate."""
        kfs = self.keyframes

        # Clamp to boundary values
        if progress <= kfs[0][0]:
            return kfs[0][1]
        if progress >= kfs[-1][0]:
            return kfs[-1][1]

        # Binary search for the enclosing segment
        lo, hi = 0, len(kfs) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if kfs[mid][0] <= progress:
                lo = mid
            else:
                hi = mid

        p0, v0 = kfs[lo]
        p1, v1 = kfs[hi]

        segment_span = p1 - p0
        if segment_span < 1e-12:
            return v1

        # Local progress within this segment, then apply easing
        local_t = (progress - p0) / segment_span
        eased_t = self.easing.solve(local_t)

        return v0 + eased_t * (v1 - v0)


# ---------------------------------------------------------------------------
# 4. ScrollLinkedAnimation
# ---------------------------------------------------------------------------

@dataclass
class ScrollLinkedAnimation:
    """CSS scroll-driven animation: scroll position *is* the time parameter.

    Unlike time-based animations this is *not* an ODE — scroll position
    directly drives the progress variable.  The easing function still acts
    as a reparameterisation of the [0,1] progress interval.

    Parameters
    ----------
    property_name : str
        CSS property being animated.
    scroll_start  : float
        Scroll position (px from top) at which the animation begins.
    scroll_end    : float
        Scroll position (px from top) at which the animation ends.
    value_start   : float
        Property value at scroll_start.
    value_end     : float
        Property value at scroll_end.
    easing        : CubicBezier
        Timing function applied to the scroll progress.
    """

    property_name: str
    scroll_start: float
    scroll_end: float
    value_start: float
    value_end: float
    easing: CubicBezier

    def value_at_scroll(self, scroll_px: float) -> float:
        """CSS property value at *scroll_px* pixels from the top.

        progress = clamp((scroll_px − scroll_start) / (scroll_end − scroll_start), 0, 1)
        eased    = easing.solve(progress)
        value    = lerp(value_start, value_end, eased)
        """
        span = self.scroll_end - self.scroll_start
        if span == 0.0:
            return self.value_end if scroll_px >= self.scroll_end else self.value_start

        raw_progress = (scroll_px - self.scroll_start) / span
        progress = max(0.0, min(1.0, raw_progress))
        eased = self.easing.solve(progress)
        return self.value_start + eased * (self.value_end - self.value_start)
