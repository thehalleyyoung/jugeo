"""
canvas_audio.py — Continuous media modelled as presheaves over time and space.

Canvas 2D drawing commands are sections over image regions; WebGL scene nodes
form a tree site; Web Audio API nodes form a signal-flow site; VideoPlayback
tracks playback as a continuous time coordinate.
"""
from __future__ import annotations

__all__ = [
    "Canvas2DCommand",
    "Canvas2DSection",
    "SceneGraphNode",
    "AudioNodeKind",
    "AudioNode",
    "AudioGraph",
    "VideoPlayback",
]

import math
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 1. Canvas 2D commands
# ---------------------------------------------------------------------------

class Canvas2DCommand(str, Enum):
    FILL_RECT = "fillRect"
    STROKE_RECT = "strokeRect"
    CLEAR_RECT = "clearRect"
    FILL_TEXT = "fillText"
    STROKE_TEXT = "strokeText"
    BEGIN_PATH = "beginPath"
    MOVE_TO = "moveTo"
    LINE_TO = "lineTo"
    ARC = "arc"
    BEZIER_CURVE_TO = "bezierCurveTo"
    QUADRATIC_CURVE_TO = "quadraticCurveTo"
    CLOSE_PATH = "closePath"
    FILL = "fill"
    STROKE = "stroke"
    DRAW_IMAGE = "drawImage"
    SAVE = "save"
    RESTORE = "restore"
    TRANSFORM = "transform"
    CLIP = "clip"


# ---------------------------------------------------------------------------
# 2. Canvas 2D section — a sequence of draw commands over an image region
# ---------------------------------------------------------------------------

@dataclass
class Canvas2DSection:
    """A section of the canvas presheaf: draw commands restricted to *region*.

    The region is given as (x, y, w, h) in canvas-pixel coordinates.
    Sections at higher z_order are painted on top of lower ones.
    """
    region: tuple[float, float, float, float]  # (x, y, w, h)
    commands: list[tuple[str, tuple]] = field(default_factory=list)
    z_order: int = 0

    # -- mutation ----------------------------------------------------------

    def add_command(self, cmd: Canvas2DCommand, *args: object) -> None:
        """Append *(cmd.value, args)* to the command list."""
        self.commands.append((cmd.value, args))

    # -- geometric predicates ---------------------------------------------

    def overlaps(self, other: Canvas2DSection) -> bool:
        """Return True if *self.region* and *other.region* overlap (AABB test)."""
        x1, y1, w1, h1 = self.region
        x2, y2, w2, h2 = other.region
        return (
            x1 < x2 + w2
            and x1 + w1 > x2
            and y1 < y2 + h2
            and y1 + h1 > y2
        )

    # -- class-level coverage check ----------------------------------------

    @staticmethod
    def sections_cover_canvas(
        sections: list[Canvas2DSection],
        width: float,
        height: float,
    ) -> bool:
        """Simplified coverage check via a pixel-strip sweep along each axis.

        Subdivides the canvas into unit columns and rows and verifies that
        every column-strip and every row-strip is covered by at least one
        section.  This is a conservative approximation: it may return *True*
        when there are small uncovered gaps that are narrower than the sweep
        step, but it is fast (O(w+h) per section) and avoids external deps.
        """
        if not sections:
            return width == 0 and height == 0

        # Collect all x-breakpoints and y-breakpoints.
        xs: list[float] = [0.0, width]
        ys: list[float] = [0.0, height]
        for s in sections:
            x, y, w, h = s.region
            xs.extend([x, x + w])
            ys.extend([y, y + h])

        xs = sorted(set(xs))
        ys = sorted(set(ys))

        # Check every cell defined by the breakpoint grid.
        for i in range(len(xs) - 1):
            for j in range(len(ys) - 1):
                cx = (xs[i] + xs[i + 1]) / 2
                cy = (ys[j] + ys[j + 1]) / 2
                # Only check cells that lie within the canvas.
                if not (0 <= cx <= width and 0 <= cy <= height):
                    continue
                covered = any(
                    s.region[0] <= cx <= s.region[0] + s.region[2]
                    and s.region[1] <= cy <= s.region[1] + s.region[3]
                    for s in sections
                )
                if not covered:
                    return False
        return True


# ---------------------------------------------------------------------------
# 3. Scene graph node — WebGL/3D scene tree site
# ---------------------------------------------------------------------------

_IDENTITY_4X4: tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

_INVISIBLE_KINDS = frozenset({"camera", "light"})


@dataclass
class SceneGraphNode:
    """A node in a WebGL scene-graph site.

    The scene graph is a presheaf: each node's world transform is the
    composition of all ancestor transforms.  We store only local transforms
    and compose on demand.
    """
    node_id: str
    node_kind: str  # mesh | light | camera | group | material | geometry
    transform: tuple[float, ...] = field(default_factory=tuple)  # 16-elem or ()
    children: list[str] = field(default_factory=list)
    material_id: str | None = None

    # -- transform composition --------------------------------------------

    @staticmethod
    def _mat4_mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
        """Multiply two row-major 4×4 matrices."""
        result: list[float] = [0.0] * 16
        for row in range(4):
            for col in range(4):
                s = 0.0
                for k in range(4):
                    s += a[row * 4 + k] * b[k * 4 + col]
                result[row * 4 + col] = s
        return tuple(result)

    def world_transform(self, parent_transforms: list[tuple[float, ...]]) -> tuple[float, ...]:
        """Return the world-space transform for this node.

        *parent_transforms* is the ordered list of ancestor local transforms
        (outermost first).  We compose them left-to-right and then multiply
        by this node's own transform.

        Simplified rule: if a transform entry is empty, treat it as identity.
        """
        accumulated = _IDENTITY_4X4
        for t in parent_transforms:
            if len(t) == 16:
                accumulated = self._mat4_mul(accumulated, t)
        local = self.transform if len(self.transform) == 16 else _IDENTITY_4X4
        return self._mat4_mul(accumulated, local)

    # -- visibility -------------------------------------------------------

    def is_visible(self) -> bool:
        """Return True for renderable nodes (not cameras or lights)."""
        return self.node_kind not in _INVISIBLE_KINDS


# ---------------------------------------------------------------------------
# 4. Audio node kind
# ---------------------------------------------------------------------------

class AudioNodeKind(str, Enum):
    SOURCE_BUFFER = "AudioBufferSourceNode"
    OSCILLATOR = "OscillatorNode"
    MEDIA_ELEMENT = "MediaElementAudioSourceNode"
    GAIN = "GainNode"
    BIQUAD_FILTER = "BiquadFilterNode"
    CONVOLVER = "ConvolverNode"
    DELAY = "DelayNode"
    DYNAMICS_COMPRESSOR = "DynamicsCompressorNode"
    PANNER = "PannerNode"
    STEREO_PANNER = "StereoPannerNode"
    ANALYSER = "AnalyserNode"
    WAVE_SHAPER = "WaveShaperNode"
    DESTINATION = "AudioDestinationNode"
    CHANNEL_MERGER = "ChannelMergerNode"
    CHANNEL_SPLITTER = "ChannelSplitterNode"


_SOURCE_KINDS = frozenset({
    AudioNodeKind.SOURCE_BUFFER,
    AudioNodeKind.OSCILLATOR,
    AudioNodeKind.MEDIA_ELEMENT,
})


# ---------------------------------------------------------------------------
# 5. Audio node — a node in the Web Audio API signal graph
# ---------------------------------------------------------------------------

@dataclass
class AudioNode:
    """A single processing node in the Web Audio signal-flow presheaf.

    The presheaf structure: each node restricts its output signal to the
    connected downstream nodes, and the signal at any point is the
    composition of upstream gains and transformations.
    """
    node_id: str
    kind: AudioNodeKind
    params: dict[str, float] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)

    def is_source(self) -> bool:
        """Return True if this node generates signal (no audio inputs)."""
        return self.kind in _SOURCE_KINDS

    def is_sink(self) -> bool:
        """Return True if this is the final audio destination."""
        return self.kind == AudioNodeKind.DESTINATION

    def gain_db(self) -> float | None:
        """For GAIN nodes, convert the linear *gain* param to dB.

        Returns *None* for non-GAIN nodes or if the *gain* param is absent.
        Returns *-inf* (represented as a large negative float) when gain == 0.
        """
        if self.kind is not AudioNodeKind.GAIN:
            return None
        gain = self.params.get("gain")
        if gain is None:
            return None
        if gain <= 0.0:
            return -math.inf
        return 20.0 * math.log10(gain)


# ---------------------------------------------------------------------------
# 6. Audio graph — the full signal-flow site
# ---------------------------------------------------------------------------

@dataclass
class AudioGraph:
    """The complete Web Audio API graph, modelled as a site over signal paths.

    Morphisms are directed signal connections; the presheaf assigns processed
    audio buffers to each node and restricts them along connection edges.
    """
    nodes: dict[str, AudioNode]
    sample_rate: float = 44100.0

    # -- mutation ----------------------------------------------------------

    def add_node(self, node: AudioNode) -> None:
        """Register *node* in the graph."""
        self.nodes[node.node_id] = node

    def connect(self, from_id: str, to_id: str) -> None:
        """Create a directed signal edge from *from_id* → *to_id*.

        Mutates both node's adjacency lists.  Silently ignores attempts to
        connect nodes that are not (yet) registered.
        """
        src = self.nodes.get(from_id)
        dst = self.nodes.get(to_id)
        if src is not None and to_id not in src.outputs:
            src.outputs.append(to_id)
        if dst is not None and from_id not in dst.inputs:
            dst.inputs.append(from_id)

    # -- traversal ---------------------------------------------------------

    def signal_paths(self) -> list[list[str]]:
        """Return all simple paths from source nodes to the destination.

        Uses iterative DFS to avoid recursion depth issues on deep graphs.
        """
        destinations = [
            nid for nid, n in self.nodes.items() if n.is_sink()
        ]
        sources = [
            nid for nid, n in self.nodes.items() if n.is_source()
        ]

        all_paths: list[list[str]] = []

        for src_id in sources:
            # Stack entries: (current_node_id, path_so_far)
            stack: list[tuple[str, list[str]]] = [(src_id, [src_id])]
            while stack:
                current, path = stack.pop()
                node = self.nodes.get(current)
                if node is None:
                    continue
                if current in destinations:
                    all_paths.append(path)
                    continue
                for out_id in node.outputs:
                    if out_id not in path:  # prevent cycles in DFS
                        stack.append((out_id, path + [out_id]))

        return all_paths

    def has_feedback_loop(self) -> bool:
        """Return True if the graph contains a directed cycle (feedback loop).

        Uses iterative DFS with colouring: white (0) → grey (1) → black (2).
        """
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[str, int] = {nid: WHITE for nid in self.nodes}

        def _dfs_has_cycle(start: str) -> bool:
            # Iterative DFS with explicit recursion stack.
            # Stack entries: (node_id, iterator_over_children, entered)
            dfs_stack: list[tuple[str, bool]] = [(start, True)]
            while dfs_stack:
                nid, entering = dfs_stack.pop()
                if entering:
                    if color[nid] == GREY:
                        return True
                    if color[nid] == BLACK:
                        continue
                    color[nid] = GREY
                    # Push "exit" marker first, then children.
                    dfs_stack.append((nid, False))
                    node = self.nodes.get(nid)
                    if node:
                        for child_id in node.outputs:
                            if child_id in color:
                                dfs_stack.append((child_id, True))
                else:
                    color[nid] = BLACK
            return False

        for nid in list(self.nodes):
            if color[nid] == WHITE:
                if _dfs_has_cycle(nid):
                    return True
        return False

    def total_gain_on_path(self, path: list[str]) -> float:
        """Return the product of all linear gain values along *path*.

        GAIN nodes contribute their *params['gain']* value (defaulting to 1.0
        if absent).  All other node kinds contribute 1.0.  The overall product
        represents the cumulative amplitude scaling from source to sink.
        """
        product = 1.0
        for nid in path:
            node = self.nodes.get(nid)
            if node is not None and node.kind is AudioNodeKind.GAIN:
                product *= node.params.get("gain", 1.0)
        return product


# ---------------------------------------------------------------------------
# 7. Video playback — continuous time coordinate
# ---------------------------------------------------------------------------

@dataclass
class VideoPlayback:
    """Video playback state modelled as a section over the time presheaf.

    *current_time_s* is the distinguished coordinate in the time site;
    *buffered_ranges* are the open sub-intervals where the presheaf has
    locally-defined data (downloaded frames).
    """
    media_id: str
    duration_s: float
    current_time_s: float = 0.0
    playback_rate: float = 1.0
    paused: bool = True
    loop: bool = False
    buffered_ranges: list[tuple[float, float]] = field(default_factory=list)

    def progress(self) -> float:
        """Return a normalised playhead position in [0, 1].

        Returns 0.0 for zero-duration media to avoid division by zero.
        """
        if self.duration_s == 0.0:
            return 0.0
        return max(0.0, min(1.0, self.current_time_s / self.duration_s))

    def is_buffered_at(self, time_s: float) -> bool:
        """Return True if *time_s* falls within any buffered range."""
        return any(start <= time_s <= end for start, end in self.buffered_ranges)

    def seek(self, time_s: float) -> None:
        """Move the playhead to *time_s*, clamped to [0, duration_s]."""
        self.current_time_s = max(0.0, min(self.duration_s, time_s))
