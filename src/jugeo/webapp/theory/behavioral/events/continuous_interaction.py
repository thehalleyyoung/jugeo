"""
Continuous interaction model for drag, scroll, pinch/zoom, pan, and related gestures.

These interactions are NOT discrete state transitions — they are continuous paths
parameterized by pointer position, time, or scroll position in 2D/1D coordinate spaces.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "InteractionKind",
    "PointerState",
    "ContinuousPath",
    "DragInteraction",
    "ScrollState",
    "PinchZoom",
]


# ---------------------------------------------------------------------------
# 1. InteractionKind
# ---------------------------------------------------------------------------

class InteractionKind(str, Enum):
    """Classifies a continuous interaction by its semantic gesture type."""
    DRAG = "drag"
    SCROLL = "scroll"
    PINCH_ZOOM = "pinch_zoom"
    PAN = "pan"
    SWIPE = "swipe"
    ROTATE = "rotate"
    HOVER = "hover"
    LONG_PRESS = "long_press"
    POINTER_LOCK = "pointer_lock"


# ---------------------------------------------------------------------------
# 2. PointerState — a single sample on a continuous pointer path
# ---------------------------------------------------------------------------

@dataclass
class PointerState:
    """
    One sample of a pointer (mouse, touch, pen) at a moment in time.

    Coordinates are in viewport pixels; timestamp is in milliseconds.
    """
    pointer_id: int
    x: float
    y: float
    pressure: float = 0.5
    timestamp_ms: float = 0.0
    pointer_type: str = "mouse"

    def distance_to(self, other: PointerState) -> float:
        """Euclidean distance in pixels to *other*."""
        return math.hypot(other.x - self.x, other.y - self.y)

    def velocity_to(self, other: PointerState) -> tuple[float, float]:
        """
        Instantaneous velocity toward *other* in px/ms.

        Returns (0.0, 0.0) when the time delta is zero to avoid division by zero.
        """
        dt = other.timestamp_ms - self.timestamp_ms
        if dt == 0.0:
            return (0.0, 0.0)
        return ((other.x - self.x) / dt, (other.y - self.y) / dt)

    def speed_to(self, other: PointerState) -> float:
        """Scalar speed (magnitude of velocity) toward *other* in px/ms."""
        vx, vy = self.velocity_to(other)
        return math.hypot(vx, vy)


# ---------------------------------------------------------------------------
# 3. ContinuousPath — ordered sequence of pointer samples
# ---------------------------------------------------------------------------

@dataclass
class ContinuousPath:
    """
    A continuous path through 2D viewport space formed by an ordered sequence
    of PointerState samples captured during a single gesture.
    """
    interaction_kind: InteractionKind
    samples: list[PointerState]
    start_target: str  # element coord name at pointer pickup

    # Threshold for classifying end speed as "has inertia potential" (px/ms)
    _INERTIA_SPEED_THRESHOLD: float = field(default=0.3, init=False, repr=False)

    def start(self) -> Optional[PointerState]:
        """First sample in the path, or None if the path is empty."""
        return self.samples[0] if self.samples else None

    def end(self) -> Optional[PointerState]:
        """Last sample in the path, or None if the path is empty."""
        return self.samples[-1] if self.samples else None

    def total_distance(self) -> float:
        """Arc length: sum of Euclidean distances between consecutive samples."""
        return sum(
            self.samples[i].distance_to(self.samples[i + 1])
            for i in range(len(self.samples) - 1)
        )

    def displacement(self) -> tuple[float, float]:
        """
        Net (dx, dy) displacement from the first sample to the last.

        Returns (0.0, 0.0) when the path has fewer than two samples.
        """
        s, e = self.start(), self.end()
        if s is None or e is s:
            return (0.0, 0.0)
        return (e.x - s.x, e.y - s.y)

    def direction_deg(self) -> Optional[float]:
        """
        Angle of the net displacement vector in degrees.

        Convention: 0° = right (+x), 90° = down (+y), measured clockwise
        in screen-space (where y increases downward).

        Returns None when displacement is (0, 0) or the path is too short.
        """
        dx, dy = self.displacement()
        if dx == 0.0 and dy == 0.0:
            return None
        # atan2(dy, dx) gives CCW angle from +x axis; negate dy for screen coords
        angle = math.degrees(math.atan2(dy, dx))
        # Normalise to [0, 360)
        return angle % 360.0

    def is_mostly_horizontal(self, tolerance_deg: float = 30.0) -> bool:
        """
        True when the displacement angle is within *tolerance_deg* of the
        horizontal axis (0° or 180°).
        """
        angle = self.direction_deg()
        if angle is None:
            return False
        # Distance to nearest horizontal axis
        dist = min(angle % 180.0, 180.0 - angle % 180.0)
        return dist <= tolerance_deg

    def is_mostly_vertical(self, tolerance_deg: float = 30.0) -> bool:
        """
        True when the displacement angle is within *tolerance_deg* of the
        vertical axis (90° or 270°).
        """
        angle = self.direction_deg()
        if angle is None:
            return False
        # Distance to nearest vertical axis (90° or 270°)
        dist = min(abs((angle % 180.0) - 90.0), 90.0 - abs((angle % 180.0) - 90.0))
        return dist <= tolerance_deg

    def peak_speed(self) -> float:
        """Maximum scalar speed (px/ms) between any two consecutive samples."""
        if len(self.samples) < 2:
            return 0.0
        return max(
            self.samples[i].speed_to(self.samples[i + 1])
            for i in range(len(self.samples) - 1)
        )

    def duration_ms(self) -> float:
        """
        Time elapsed from the first sample to the last in milliseconds.

        Returns 0.0 for paths with fewer than two samples.
        """
        s, e = self.start(), self.end()
        if s is None or e is s:
            return 0.0
        return e.timestamp_ms - s.timestamp_ms

    def average_speed(self) -> float:
        """
        Mean speed over the whole path in px/ms (arc length / duration).

        Returns 0.0 when duration is zero.
        """
        t = self.duration_ms()
        if t == 0.0:
            return 0.0
        return self.total_distance() / t

    def has_inertia_potential(self) -> bool:
        """
        True when the speed at the end of the path exceeds the inertia threshold,
        indicating the gesture could trigger inertial (momentum-based) scrolling.
        """
        if len(self.samples) < 2:
            return False
        end_speed = self.samples[-2].speed_to(self.samples[-1])
        return end_speed > self._INERTIA_SPEED_THRESHOLD


# ---------------------------------------------------------------------------
# 4. DragInteraction — drag semantics on top of a continuous path
# ---------------------------------------------------------------------------

@dataclass
class DragInteraction:
    """
    Models a drag gesture: pickup, live translation, and optional drop.

    ``pickup_offset`` is the point within the dragged element where the pointer
    landed (so the element can be positioned relative to the pointer correctly).
    """
    path: ContinuousPath
    pickup_offset: tuple[float, float]
    drop_target: Optional[str]  # element coord name at release, or None
    current_position: tuple[float, float]  # live viewport position during drag

    def is_threshold_exceeded(self, threshold_px: float = 4.0) -> bool:
        """
        True when the net displacement from the pickup point exceeds
        *threshold_px*, distinguishing an intentional drag from an accidental
        micro-movement.
        """
        dx, dy = self.path.displacement()
        return math.hypot(dx, dy) > threshold_px

    def constrain_to_axis(self) -> Optional[str]:
        """
        Returns ``"x"`` or ``"y"`` when the path is mostly horizontal or
        mostly vertical (respectively), enabling axis-locked drag behaviour.

        Returns ``None`` when the path is diagonal or too short to determine.
        """
        if self.path.is_mostly_horizontal():
            return "x"
        if self.path.is_mostly_vertical():
            return "y"
        return None


# ---------------------------------------------------------------------------
# 5. ScrollState — scroll as a continuous 1D/2D coordinate
# ---------------------------------------------------------------------------

@dataclass
class ScrollState:
    """
    Models the current scroll position of a scrollable element as a continuous
    coordinate, together with velocity information for inertial scrolling and
    optional snap-point support.
    """
    scroll_x: float
    scroll_y: float
    max_scroll_x: float
    max_scroll_y: float
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    snap_points_y: list[float] = field(default_factory=list)

    def scroll_fraction_y(self) -> float:
        """
        Vertical scroll position as a fraction in [0, 1].

        Returns 0.0 when max_scroll_y is zero (no overflow).
        """
        if self.max_scroll_y == 0.0:
            return 0.0
        return max(0.0, min(1.0, self.scroll_y / self.max_scroll_y))

    def nearest_snap_point(self, direction: float) -> Optional[float]:
        """
        Return the nearest snap point *ahead* in *direction* (positive = down,
        negative = up).  Returns ``None`` when there are no snap points.

        When ``direction`` is zero, the closest snap point regardless of
        direction is returned.
        """
        if not self.snap_points_y:
            return None

        if direction == 0.0:
            return min(self.snap_points_y, key=lambda p: abs(p - self.scroll_y))

        candidates = (
            [p for p in self.snap_points_y if p > self.scroll_y]
            if direction > 0
            else [p for p in self.snap_points_y if p < self.scroll_y]
        )
        if not candidates:
            return None

        return min(candidates, key=lambda p: abs(p - self.scroll_y))

    def inertial_position_at(
        self, t_ms: float, friction: float = 0.95
    ) -> tuple[float, float]:
        """
        Predict scroll position after *t_ms* milliseconds of inertial scrolling.

        The velocity is multiplied by *friction* once per 16 ms frame.  The
        position is computed by integrating the decaying velocity analytically:

            frames  = t_ms / 16
            pos(t)  = pos(0) + v * (1 - friction^frames) / (1 - friction)

        Clamped to [0, max_scroll].
        """
        if t_ms <= 0.0:
            return (self.scroll_x, self.scroll_y)

        frames = t_ms / 16.0

        if abs(friction - 1.0) < 1e-9:
            # No friction: linear motion
            factor_x = factor_y = frames
        else:
            # Geometric series sum: (1 - r^n) / (1 - r)
            r = friction
            factor = (1.0 - r ** frames) / (1.0 - r)
            factor_x = factor_y = factor

        x = self.scroll_x + self.velocity_x * factor_x
        y = self.scroll_y + self.velocity_y * factor_y

        x = max(0.0, min(self.max_scroll_x, x))
        y = max(0.0, min(self.max_scroll_y, y))

        return (x, y)


# ---------------------------------------------------------------------------
# 6. PinchZoom — two-finger pinch/zoom and rotation
# ---------------------------------------------------------------------------

@dataclass
class PinchZoom:
    """
    Models a two-finger pinch-zoom gesture.

    ``initial_distance`` is the span between the fingers at gesture start;
    ``current_distance`` is the live span used to compute the scale factor.
    """
    touch_a: PointerState
    touch_b: PointerState
    initial_distance: float
    current_distance: float

    @property
    def scale_factor(self) -> float:
        """
        Multiplicative scale factor relative to the gesture start.

        Returns 1.0 when the initial distance is zero to avoid division by zero.
        """
        if self.initial_distance == 0.0:
            return 1.0
        return self.current_distance / self.initial_distance

    @property
    def center(self) -> tuple[float, float]:
        """Midpoint between the two active touch points in viewport coordinates."""
        return (
            (self.touch_a.x + self.touch_b.x) / 2.0,
            (self.touch_a.y + self.touch_b.y) / 2.0,
        )

    def rotation_deg(
        self, prev_a: PointerState, prev_b: PointerState
    ) -> float:
        """
        Rotation in degrees since the previous frame, computed from the change
        in angle of the vector connecting the two touch points.

        Positive values indicate clockwise rotation in screen-space.
        Returns 0.0 when either span has zero length.
        """
        prev_angle = math.degrees(
            math.atan2(prev_b.y - prev_a.y, prev_b.x - prev_a.x)
        )
        curr_angle = math.degrees(
            math.atan2(self.touch_b.y - self.touch_a.y, self.touch_b.x - self.touch_a.x)
        )
        delta = curr_angle - prev_angle
        # Normalise to (-180, 180]
        if delta > 180.0:
            delta -= 360.0
        elif delta <= -180.0:
            delta += 360.0
        return delta
