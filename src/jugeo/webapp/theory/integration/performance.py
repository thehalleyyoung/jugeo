"""
Web performance modelled as obligations over the render path site.

The critical render path is a chain of morphisms that must be minimized.
Core Web Vitals are descent conditions — each metric must descend below its
threshold for the page to be considered performant.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "RenderPhase",
    "ResourceKind",
    "WebResource",
    "CriticalRenderPath",
    "CoreWebVitals",
    "PerformanceBudget",
    "PerformanceChecker",
]


# ---------------------------------------------------------------------------
# RenderPhase
# ---------------------------------------------------------------------------

class RenderPhase(str, Enum):
    """Ordered phases of the browser's critical render path."""

    DNS_LOOKUP = "dns_lookup"
    TCP_CONNECT = "tcp_connect"
    TLS_HANDSHAKE = "tls_handshake"
    TTFB = "ttfb"
    HTML_PARSE = "html_parse"
    CSS_PARSE = "css_parse"
    JS_PARSE = "js_parse"
    STYLE_CALC = "style_calc"
    LAYOUT = "layout"
    PAINT = "paint"
    COMPOSITE = "composite"
    LOAD_COMPLETE = "load_complete"

    def is_blocking(self) -> bool:
        """Return True if this phase blocks rendering of subsequent content.

        CSS_PARSE blocks because the browser must construct the CSSOM before
        painting.  JS_PARSE (synchronous scripts) blocks because scripts can
        mutate the DOM/CSSOM and must therefore execute inline.
        """
        return self in {RenderPhase.CSS_PARSE, RenderPhase.JS_PARSE}


# ---------------------------------------------------------------------------
# ResourceKind
# ---------------------------------------------------------------------------

class ResourceKind(str, Enum):
    """Classification of web resources by MIME / function."""

    HTML = "html"
    CSS = "css"
    JAVASCRIPT = "javascript"
    IMAGE = "image"
    FONT = "font"
    VIDEO = "video"
    AUDIO = "audio"
    WASM = "wasm"
    FETCH = "fetch"
    WEBSOCKET = "websocket"


# ---------------------------------------------------------------------------
# WebResource
# ---------------------------------------------------------------------------

@dataclass
class WebResource:
    """A single resource fetched during page load.

    Attributes
    ----------
    resource_id:
        Stable identifier (e.g. a slug or URL hash).
    kind:
        Functional category of the resource.
    url:
        Absolute or relative URL.
    size_bytes:
        Uncompressed byte size (0 = unknown).
    is_render_blocking:
        True when the browser must wait for this resource before painting.
    is_lazy:
        True when loading is deferred (e.g. ``loading="lazy"``).
    priority:
        Fetch-priority hint: ``"high"``, ``"low"``, or ``"auto"``.
    preloaded:
        True when a ``<link rel="preload">`` hint exists for this resource.
    """

    resource_id: str
    kind: ResourceKind
    url: str
    size_bytes: int = 0
    is_render_blocking: bool = False
    is_lazy: bool = False
    priority: str = "auto"
    preloaded: bool = False

    def is_critical(self) -> bool:
        """Return True when the resource is on the critical render path.

        A resource is critical when it must be fetched and processed before
        the browser can produce the first meaningful paint:

        * HTML is always critical.
        * CSS in the ``<head>`` is always critical.
        * Synchronous (non-async, non-defer) JavaScript in the ``<head>``
          is critical because it blocks HTML parsing.
        """
        if self.kind == ResourceKind.HTML:
            return True
        if self.kind == ResourceKind.CSS:
            return True
        if self.kind == ResourceKind.JAVASCRIPT:
            return self.is_render_blocking and not self.is_lazy
        return False

    def loading_hint(self) -> str:
        """Return the recommended loading strategy for this resource.

        Returns one of: ``"preload"``, ``"defer"``, ``"async"``,
        ``"lazy"``, ``"eager"``.
        """
        if self.kind == ResourceKind.FONT:
            return "preload"
        if self.kind in {ResourceKind.CSS}:
            return "preload" if self.is_render_blocking else "defer"
        if self.kind == ResourceKind.JAVASCRIPT:
            if self.is_render_blocking:
                return "defer"
            return "async"
        if self.kind == ResourceKind.IMAGE:
            return "lazy" if self.is_lazy else "eager"
        if self.kind in {ResourceKind.VIDEO, ResourceKind.AUDIO}:
            return "lazy"
        return "eager"


# ---------------------------------------------------------------------------
# CriticalRenderPath
# ---------------------------------------------------------------------------

@dataclass
class CriticalRenderPath:
    """The ordered chain of resources that must load before first paint.

    Modelled as a sequence of morphisms in the render pipeline; minimising
    the chain length and total blocking payload reduces time-to-paint.
    """

    resources: list[WebResource] = field(default_factory=list)

    def blocking_resources(self) -> list[WebResource]:
        """Return resources that actively block rendering.

        A resource blocks rendering when:
        * ``is_render_blocking`` is True, *and*
        * it has not been pre-fetched (``preloaded=False``), *and*
        * it is not deferred / async / lazy.
        """
        result = []
        for r in self.resources:
            if r.is_render_blocking and not r.preloaded and not r.is_lazy:
                result.append(r)
        return result

    def critical_chain_length(self) -> int:
        """Count sequential blocking resources in the critical chain."""
        return len(self.blocking_resources())

    def total_blocking_size_bytes(self) -> int:
        """Sum the byte sizes of all blocking resources."""
        return sum(r.size_bytes for r in self.blocking_resources())

    def optimize(self) -> list[str]:
        """Return a prioritised list of optimisation recommendations."""
        recommendations: list[str] = []

        blocking = self.blocking_resources()

        # Recommendation: defer/async non-critical scripts
        blocking_js = [r for r in blocking if r.kind == ResourceKind.JAVASCRIPT]
        if blocking_js:
            recommendations.append("Add defer/async to non-critical scripts")

        # Recommendation: preload critical fonts
        fonts = [
            r for r in self.resources
            if r.kind == ResourceKind.FONT and not r.preloaded
        ]
        if fonts:
            recommendations.append("Preload critical fonts")

        # Recommendation: inline critical CSS
        large_blocking_css = [
            r for r in blocking
            if r.kind == ResourceKind.CSS and r.size_bytes > 10_000
        ]
        if large_blocking_css:
            recommendations.append("Inline critical CSS")

        # Recommendation: resource hints for third-party origins
        third_party = [
            r for r in self.resources
            if r.url.startswith("http") and not r.preloaded
        ]
        if third_party:
            recommendations.append(
                "Use resource hints (preconnect, dns-prefetch)"
            )

        return recommendations


# ---------------------------------------------------------------------------
# CoreWebVitals
# ---------------------------------------------------------------------------

@dataclass
class CoreWebVitals:
    """Measured or estimated Google Core Web Vitals.

    Thresholds follow the official 2024 guidance:

    =========  ======  =================  ======
    Metric     Good    Needs Improvement  Poor
    =========  ======  =================  ======
    LCP        <2500   2500–4000          >4000 ms
    FID        <100    100–300            >300 ms
    CLS        <0.1    0.1–0.25           >0.25
    FCP        <1800   1800–3000          >3000 ms
    TTFB       <800    800–1800           >1800 ms
    INP        <200    200–500            >500 ms
    =========  ======  =================  ======
    """

    lcp_ms: float | None = None
    fid_ms: float | None = None
    cls_score: float | None = None
    fcp_ms: float | None = None
    ttfb_ms: float | None = None
    inp_ms: float | None = None

    # ------------------------------------------------------------------
    # Rating helpers
    # ------------------------------------------------------------------

    def lcp_rating(self) -> str:
        """Rate Largest Contentful Paint."""
        if self.lcp_ms is None:
            return "unknown"
        if self.lcp_ms < 2500:
            return "good"
        if self.lcp_ms <= 4000:
            return "needs_improvement"
        return "poor"

    def fid_rating(self) -> str:
        """Rate First Input Delay."""
        if self.fid_ms is None:
            return "unknown"
        if self.fid_ms < 100:
            return "good"
        if self.fid_ms <= 300:
            return "needs_improvement"
        return "poor"

    def cls_rating(self) -> str:
        """Rate Cumulative Layout Shift."""
        if self.cls_score is None:
            return "unknown"
        if self.cls_score < 0.1:
            return "good"
        if self.cls_score <= 0.25:
            return "needs_improvement"
        return "poor"

    def fcp_rating(self) -> str:
        """Rate First Contentful Paint."""
        if self.fcp_ms is None:
            return "unknown"
        if self.fcp_ms < 1800:
            return "good"
        if self.fcp_ms <= 3000:
            return "needs_improvement"
        return "poor"

    def ttfb_rating(self) -> str:
        """Rate Time to First Byte."""
        if self.ttfb_ms is None:
            return "unknown"
        if self.ttfb_ms < 800:
            return "good"
        if self.ttfb_ms <= 1800:
            return "needs_improvement"
        return "poor"

    def inp_rating(self) -> str:
        """Rate Interaction to Next Paint."""
        if self.inp_ms is None:
            return "unknown"
        if self.inp_ms < 200:
            return "good"
        if self.inp_ms <= 500:
            return "needs_improvement"
        return "poor"

    def overall_rating(self) -> str:
        """Return the worst rating across the three Core Web Vitals (LCP, FID, CLS).

        The ordering from worst to best is: ``poor`` > ``needs_improvement``
        > ``good`` > ``unknown``.
        """
        order = {"poor": 3, "needs_improvement": 2, "good": 1, "unknown": 0}
        ratings = [self.lcp_rating(), self.fid_rating(), self.cls_rating()]
        return max(ratings, key=lambda r: order[r])

    # ------------------------------------------------------------------
    # Estimation
    # ------------------------------------------------------------------

    @classmethod
    def estimate_from_path(cls, path: CriticalRenderPath) -> CoreWebVitals:
        """Produce rough synthetic estimates from a :class:`CriticalRenderPath`.

        Estimation model
        ----------------
        * **FCP** ≈ 200 ms + 50 ms × (number of blocking resources)
        * **LCP** ≈ FCP + 300 ms
        * **CLS** = 0.05 when no layout-shifting images are present;
          0.15 when images without explicit dimensions exist.
        * **TTFB** ≈ 200 ms (constant baseline — not observable from
          client-side resource lists alone).
        * FID and INP are interaction-driven and cannot be estimated
          from the resource list; they are left as ``None``.
        """
        blocking_count = path.critical_chain_length()
        fcp = 200.0 + 50.0 * blocking_count
        lcp = fcp + 300.0

        # CLS heuristic: images without explicit sizes cause layout shifts.
        layout_shifting_images = [
            r for r in path.resources
            if r.kind == ResourceKind.IMAGE and not r.is_lazy
        ]
        cls_score = 0.15 if layout_shifting_images else 0.05

        return cls(
            lcp_ms=math.ceil(lcp),
            fcp_ms=math.ceil(fcp),
            cls_score=cls_score,
            ttfb_ms=200.0,
        )


# ---------------------------------------------------------------------------
# PerformanceBudget
# ---------------------------------------------------------------------------

@dataclass
class PerformanceBudget:
    """Declarative performance budget for a page or route.

    Attributes
    ----------
    max_lcp_ms:
        Maximum acceptable Largest Contentful Paint in milliseconds.
    max_blocking_resources:
        Maximum number of render-blocking resources allowed.
    max_total_js_kb:
        Maximum total JavaScript payload in kilobytes.
    max_total_css_kb:
        Maximum total CSS payload in kilobytes.
    max_image_kb:
        Maximum total image payload in kilobytes.
    """

    max_lcp_ms: float = 2500.0
    max_blocking_resources: int = 2
    max_total_js_kb: float = 200.0
    max_total_css_kb: float = 50.0
    max_image_kb: float = 500.0

    def check(self, resources: list[WebResource]) -> list[str]:
        """Return a list of budget violation descriptions.

        An empty list means all budgets are satisfied.
        """
        violations: list[str] = []

        path = CriticalRenderPath(resources)
        blocking_count = path.critical_chain_length()
        if blocking_count > self.max_blocking_resources:
            violations.append(
                f"Blocking resources: {blocking_count} "
                f"(budget: {self.max_blocking_resources})"
            )

        total_js_kb = sum(
            r.size_bytes for r in resources if r.kind == ResourceKind.JAVASCRIPT
        ) / 1024
        if total_js_kb > self.max_total_js_kb:
            violations.append(
                f"Total JS: {total_js_kb:.1f} KB "
                f"(budget: {self.max_total_js_kb:.1f} KB)"
            )

        total_css_kb = sum(
            r.size_bytes for r in resources if r.kind == ResourceKind.CSS
        ) / 1024
        if total_css_kb > self.max_total_css_kb:
            violations.append(
                f"Total CSS: {total_css_kb:.1f} KB "
                f"(budget: {self.max_total_css_kb:.1f} KB)"
            )

        total_image_kb = sum(
            r.size_bytes for r in resources if r.kind == ResourceKind.IMAGE
        ) / 1024
        if total_image_kb > self.max_image_kb:
            violations.append(
                f"Total images: {total_image_kb:.1f} KB "
                f"(budget: {self.max_image_kb:.1f} KB)"
            )

        vitals = CoreWebVitals.estimate_from_path(path)
        if vitals.lcp_ms is not None and vitals.lcp_ms > self.max_lcp_ms:
            violations.append(
                f"Estimated LCP: {vitals.lcp_ms:.0f} ms "
                f"(budget: {self.max_lcp_ms:.0f} ms)"
            )

        return violations


# ---------------------------------------------------------------------------
# PerformanceChecker
# ---------------------------------------------------------------------------

class PerformanceChecker:
    """Static HTML/CSS analysis for performance anti-patterns."""

    # Patterns for resource extraction
    _RE_SCRIPT = re.compile(
        r'<script\b([^>]*)src=["\']([^"\']+)["\']([^>]*)>',
        re.IGNORECASE,
    )
    _RE_LINK_STYLESHEET = re.compile(
        r'<link\b([^>]*)rel=["\']stylesheet["\']([^>]*)>',
        re.IGNORECASE,
    )
    _RE_LINK_PRELOAD = re.compile(
        r'<link\b([^>]*)rel=["\']preload["\']([^>]*)href=["\']([^"\']+)["\']([^>]*)>',
        re.IGNORECASE,
    )
    _RE_IMG = re.compile(
        r'<img\b([^>]*)>',
        re.IGNORECASE,
    )
    _RE_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    _RE_SRC = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
    _RE_FONT_FACE = re.compile(r'@font-face\s*\{([^}]*)\}', re.IGNORECASE | re.DOTALL)
    _RE_FONT_DISPLAY = re.compile(r'font-display\s*:\s*(\w+)', re.IGNORECASE)

    def analyze_html_resources(self, html_content: str) -> list[WebResource]:
        """Parse *html_content* and return a :class:`WebResource` per asset.

        Recognises:
        * ``<script src="…">`` — with ``defer`` / ``async`` detection
        * ``<link rel="stylesheet" href="…">``
        * ``<img src="…">`` — with ``loading="lazy"`` detection
        * ``<link rel="preload" href="…">``
        """
        resources: list[WebResource] = []
        preloaded_urls: set[str] = set()

        # Collect preloaded URLs first so we can annotate other resources.
        for m in self._RE_LINK_PRELOAD.finditer(html_content):
            attrs_before, attrs_after = m.group(1), m.group(4)
            url = m.group(3)
            preloaded_urls.add(url)
            as_match = re.search(
                r'\bas=["\'](\w+)["\']', attrs_before + attrs_after, re.IGNORECASE
            )
            kind_str = as_match.group(1).lower() if as_match else "fetch"
            kind = self._as_to_resource_kind(kind_str)
            resources.append(
                WebResource(
                    resource_id=url,
                    kind=kind,
                    url=url,
                    preloaded=True,
                    priority="high",
                )
            )

        # Scripts
        for m in self._RE_SCRIPT.finditer(html_content):
            attrs = m.group(1) + m.group(3)
            url = m.group(2)
            has_defer = bool(re.search(r'\bdefer\b', attrs, re.IGNORECASE))
            has_async = bool(re.search(r'\basync\b', attrs, re.IGNORECASE))
            is_lazy = has_defer or has_async
            is_blocking = not is_lazy
            resources.append(
                WebResource(
                    resource_id=url,
                    kind=ResourceKind.JAVASCRIPT,
                    url=url,
                    is_render_blocking=is_blocking,
                    is_lazy=is_lazy,
                    preloaded=url in preloaded_urls,
                )
            )

        # Stylesheets
        for m in self._RE_LINK_STYLESHEET.finditer(html_content):
            attrs = m.group(1) + m.group(2)
            href_m = self._RE_HREF.search(attrs)
            if not href_m:
                continue
            url = href_m.group(1)
            resources.append(
                WebResource(
                    resource_id=url,
                    kind=ResourceKind.CSS,
                    url=url,
                    is_render_blocking=True,
                    preloaded=url in preloaded_urls,
                )
            )

        # Images
        for m in self._RE_IMG.finditer(html_content):
            attrs = m.group(1)
            src_m = self._RE_SRC.search(attrs)
            if not src_m:
                continue
            url = src_m.group(1)
            is_lazy = bool(
                re.search(r'loading=["\']lazy["\']', attrs, re.IGNORECASE)
            )
            resources.append(
                WebResource(
                    resource_id=url,
                    kind=ResourceKind.IMAGE,
                    url=url,
                    is_lazy=is_lazy,
                    preloaded=url in preloaded_urls,
                )
            )

        return resources

    def check_image_dimensions(self, html_content: str) -> list[str]:
        """Return a warning for every ``<img>`` missing ``width`` and ``height``.

        Omitting explicit dimensions causes the browser to reserve no space
        for the image until it loads, producing a Cumulative Layout Shift.
        """
        warnings: list[str] = []
        for m in self._RE_IMG.finditer(html_content):
            attrs = m.group(1)
            src_m = self._RE_SRC.search(attrs)
            url = src_m.group(1) if src_m else "<unknown>"
            has_width = bool(re.search(r'\bwidth\s*=', attrs, re.IGNORECASE))
            has_height = bool(re.search(r'\bheight\s*=', attrs, re.IGNORECASE))
            if not (has_width and has_height):
                warnings.append(
                    f"<img src=\"{url}\"> is missing explicit width/height "
                    "(CLS impact: layout shift on image load)"
                )
        return warnings

    def check_font_loading(self, css_content: str) -> list[str]:
        """Return a warning for every ``@font-face`` without a safe ``font-display``.

        Without ``font-display: swap`` or ``font-display: optional``, the
        browser may render invisible text (FOIT) or show a flash of unstyled
        text (FOUT) without recovery, degrading Largest Contentful Paint.
        """
        warnings: list[str] = []
        for m in self._RE_FONT_FACE.finditer(css_content):
            block = m.group(1)
            fd_match = self._RE_FONT_DISPLAY.search(block)
            if fd_match:
                value = fd_match.group(1).lower()
                if value in {"swap", "optional"}:
                    continue
                warnings.append(
                    f"@font-face uses font-display: {value} — "
                    "prefer 'swap' or 'optional' to avoid FOIT/FOUT"
                )
            else:
                warnings.append(
                    "@font-face missing font-display property — "
                    "add 'font-display: swap' or 'font-display: optional' "
                    "to avoid FOIT/FOUT"
                )
        return warnings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_to_resource_kind(as_value: str) -> ResourceKind:
        """Map a ``<link as="…">`` value to a :class:`ResourceKind`."""
        mapping: dict[str, ResourceKind] = {
            "script": ResourceKind.JAVASCRIPT,
            "style": ResourceKind.CSS,
            "image": ResourceKind.IMAGE,
            "font": ResourceKind.FONT,
            "video": ResourceKind.VIDEO,
            "audio": ResourceKind.AUDIO,
            "fetch": ResourceKind.FETCH,
        }
        return mapping.get(as_value, ResourceKind.FETCH)
