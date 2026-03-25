"""Tests for src/jugeo/webapp/rendering/ — visual site and rendering functor."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.webapp.rendering.models import (
    AnimationFrame,
    InteractiveZone,
    RenderChange,
    RenderChangeKind,
    RenderDiff,
    TextRun,
    ViewportRegion,
    VisualElement,
    VisualPage,
)
from jugeo.webapp.rendering.visual_site import VisualSite
from jugeo.webapp.rendering.rendering_functor import RenderingFunctor, RenderingDescentChecker
from jugeo.webapp.rendering.viewport_model import Viewport, ViewportPresets, ViewportSimulator
from jugeo.webapp.rendering.interaction_model import (
    InteractionAccessibilityChecker,
    InteractionEvent,
    InteractionKind,
    InteractionSimulator,
    InteractionTrace,
)


# ═══════════════════════════════════════════════════════════════════════════
# RenderChangeKind
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderChangeKind:
    def test_values_count(self):
        assert len(RenderChangeKind) == 7

    def test_position_changed(self):
        assert RenderChangeKind.POSITION_CHANGED == "position_changed"

    def test_added(self):
        assert RenderChangeKind.ADDED == "added"

    def test_removed(self):
        assert RenderChangeKind.REMOVED == "removed"


# ═══════════════════════════════════════════════════════════════════════════
# ViewportRegion
# ═══════════════════════════════════════════════════════════════════════════


class TestViewportRegion:
    def test_creation(self):
        r = ViewportRegion(x=10, y=20, width=100, height=50)
        assert r.x == 10
        assert r.y == 20
        assert r.width == 100
        assert r.height == 50

    def test_to_dict_from_dict(self):
        r = ViewportRegion(x=1, y=2, width=3, height=4, node_id="n1", z_index=5)
        d = r.to_dict()
        r2 = ViewportRegion.from_dict(d)
        assert r2.x == r.x
        assert r2.node_id == "n1"
        assert r2.z_index == 5

    def test_fields(self):
        r = ViewportRegion(x=0, y=0, width=10, height=10)
        assert r.node_id == ""
        assert r.z_index == 0


# ═══════════════════════════════════════════════════════════════════════════
# TextRun
# ═══════════════════════════════════════════════════════════════════════════


class TestTextRun:
    def test_creation(self):
        t = TextRun(content="hello")
        assert t.content == "hello"
        assert t.font_family == "sans-serif"

    def test_to_dict_from_dict(self):
        t = TextRun(content="abc", font_size=20.0, color="#ff0000", node_id="t1")
        d = t.to_dict()
        t2 = TextRun.from_dict(d)
        assert t2.content == "abc"
        assert t2.font_size == 20.0


# ═══════════════════════════════════════════════════════════════════════════
# InteractiveZone
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractiveZone:
    def test_creation(self):
        z = InteractiveZone(node_id="btn1")
        assert z.node_id == "btn1"

    def test_to_dict_from_dict(self):
        z = InteractiveZone(
            node_id="btn1",
            event_types=["click"],
            bbox=(10, 20, 100, 50),
        )
        d = z.to_dict()
        z2 = InteractiveZone.from_dict(d)
        assert z2.node_id == "btn1"
        assert z2.bbox == (10, 20, 100, 50)


# ═══════════════════════════════════════════════════════════════════════════
# AnimationFrame
# ═══════════════════════════════════════════════════════════════════════════


class TestAnimationFrame:
    def test_creation(self):
        f = AnimationFrame(time_ms=100, node_id="a1")
        assert f.time_ms == 100

    def test_to_dict_from_dict(self):
        f = AnimationFrame(time_ms=50, node_id="x", properties_changed={"opacity": 0.5})
        d = f.to_dict()
        f2 = AnimationFrame.from_dict(d)
        assert f2.node_id == "x"
        assert f2.properties_changed["opacity"] == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# VisualElement
# ═══════════════════════════════════════════════════════════════════════════


class TestVisualElement:
    def test_creation(self):
        r = ViewportRegion(x=0, y=0, width=100, height=100, node_id="el1")
        el = VisualElement(region=r)
        assert el.region.node_id == "el1"

    def test_to_dict_from_dict(self):
        r = ViewportRegion(x=0, y=0, width=100, height=100, node_id="el1")
        t = TextRun(content="hi", node_id="el1")
        el = VisualElement(region=r, text_runs=[t])
        d = el.to_dict()
        el2 = VisualElement.from_dict(d)
        assert el2.region.node_id == "el1"
        assert len(el2.text_runs) == 1


# ═══════════════════════════════════════════════════════════════════════════
# VisualPage
# ═══════════════════════════════════════════════════════════════════════════


class TestVisualPage:
    def test_creation(self):
        page = VisualPage(viewport_width=1920, viewport_height=1080)
        assert page.viewport_width == 1920

    def test_to_dict_from_dict(self):
        page = VisualPage(viewport_width=800, viewport_height=600, device_class="mobile")
        d = page.to_dict()
        page2 = VisualPage.from_dict(d)
        assert page2.device_class == "mobile"


# ═══════════════════════════════════════════════════════════════════════════
# RenderChange / RenderDiff
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderChange:
    def test_creation(self):
        c = RenderChange(kind=RenderChangeKind.ADDED, node_id="n1")
        assert c.kind == RenderChangeKind.ADDED

    def test_to_dict_from_dict(self):
        c = RenderChange(kind=RenderChangeKind.SIZE_CHANGED, node_id="n2",
                         old_value=(10, 10), new_value=(20, 20))
        d = c.to_dict()
        c2 = RenderChange.from_dict(d)
        assert c2.kind == RenderChangeKind.SIZE_CHANGED
        assert c2.node_id == "n2"


class TestRenderDiff:
    def test_has_changes_true(self):
        diff = RenderDiff(changes=[RenderChange(kind=RenderChangeKind.ADDED, node_id="x")])
        assert diff.has_changes is True

    def test_has_changes_false(self):
        diff = RenderDiff()
        assert diff.has_changes is False

    def test_to_dict_from_dict(self):
        diff = RenderDiff(changes=[RenderChange(kind=RenderChangeKind.REMOVED, node_id="y")])
        d = diff.to_dict()
        diff2 = RenderDiff.from_dict(d)
        assert len(diff2.changes) == 1


# ═══════════════════════════════════════════════════════════════════════════
# VisualSite
# ═══════════════════════════════════════════════════════════════════════════


def _make_element(nid: str, x: float, y: float, w: float, h: float, z: int = 0,
                  text: str = "", interactive: bool = False) -> VisualElement:
    """Helper to build a VisualElement."""
    region = ViewportRegion(x=x, y=y, width=w, height=h, node_id=nid, z_index=z)
    text_runs = [TextRun(content=text, node_id=nid)] if text else []
    zones = [InteractiveZone(node_id=nid, event_types=["click"],
                             bbox=(x, y, w, h), z_index=z)] if interactive else []
    return VisualElement(region=region, text_runs=text_runs, interactive_zones=zones)


class TestVisualSite:
    def test_construction(self):
        el = _make_element("a", 0, 0, 100, 100)
        vs = VisualSite([el])
        assert len(vs.regions()) == 1

    def test_from_layout_empty(self):
        vs = VisualSite.from_layout([], {}, {})
        assert len(vs.regions()) == 0

    def test_from_layout_with_boxes(self):
        boxes = [{"id": "b1", "x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0}]
        vs = VisualSite.from_layout(boxes, {}, {"children": []})
        assert len(vs.regions()) == 1

    def test_regions(self):
        el = _make_element("a", 0, 0, 50, 50)
        vs = VisualSite([el])
        assert isinstance(vs.regions()[0], ViewportRegion)

    def test_text_runs(self):
        el = _make_element("a", 0, 0, 50, 50, text="hello")
        vs = VisualSite([el])
        assert len(vs.text_runs()) == 1

    def test_interactive_zones(self):
        el = _make_element("a", 0, 0, 50, 50, interactive=True)
        vs = VisualSite([el])
        assert len(vs.interactive_zones()) == 1

    def test_spatial_morphisms_with_children(self):
        parent = _make_element("p", 0, 0, 200, 200)
        child = _make_element("c", 10, 10, 50, 50)
        parent.children = [child]
        vs = VisualSite([parent])
        morphisms = vs.spatial_morphisms()
        assert len(morphisms) >= 1
        assert morphisms[0]["parent"] == "p"
        assert morphisms[0]["child"] == "c"

    def test_temporal_morphisms(self):
        a = _make_element("a", 0, 0, 50, 50)
        b = _make_element("b", 0, 60, 50, 50)
        vs = VisualSite([a, b])
        tm = vs.temporal_morphisms()
        assert len(tm) >= 1

    def test_interaction_morphisms(self):
        el = _make_element("btn", 0, 0, 100, 40, interactive=True)
        vs = VisualSite([el])
        im = vs.interaction_morphisms()
        assert len(im) >= 1
        assert im[0]["source"] == "btn"

    def test_elements_at_point_hit(self):
        el = _make_element("a", 0, 0, 100, 100)
        vs = VisualSite([el])
        hits = vs.elements_at_point(50, 50)
        assert len(hits) == 1

    def test_elements_at_point_miss(self):
        el = _make_element("a", 0, 0, 100, 100)
        vs = VisualSite([el])
        hits = vs.elements_at_point(200, 200)
        assert len(hits) == 0

    def test_elements_in_region(self):
        el = _make_element("a", 10, 10, 50, 50)
        vs = VisualSite([el])
        hits = vs.elements_in_region(0, 0, 100, 100)
        assert len(hits) == 1

    def test_reading_order(self):
        a = _make_element("a", 100, 0, 50, 50)
        b = _make_element("b", 0, 0, 50, 50)
        c = _make_element("c", 0, 100, 50, 50)
        vs = VisualSite([a, b, c])
        order = vs.reading_order()
        assert order[0] == "b"  # top-left first
        assert order[-1] == "c"  # bottom

    def test_z_order(self):
        a = _make_element("a", 0, 0, 50, 50, z=1)
        b = _make_element("b", 0, 0, 50, 50, z=10)
        vs = VisualSite([a, b])
        order = vs.z_order()
        assert order[0] == "b"  # higher z first

    def test_to_visual_page(self):
        el = _make_element("a", 0, 0, 50, 50)
        vs = VisualSite([el])
        page = vs.to_visual_page()
        assert isinstance(page, VisualPage)

    def test_to_dict_from_dict_roundtrip(self):
        el = _make_element("a", 10, 20, 30, 40)
        vs = VisualSite([el], viewport_width=1024, viewport_height=768)
        d = vs.to_dict()
        vs2 = VisualSite.from_dict(d)
        assert len(vs2.regions()) == 1
        assert vs2._viewport_width == 1024


# ═══════════════════════════════════════════════════════════════════════════
# RenderingFunctor
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderingFunctor:
    def test_instantiation(self):
        f = RenderingFunctor()
        assert f is not None

    def test_map_layout_to_regions_empty(self):
        f = RenderingFunctor()
        assert f._map_layout_to_regions([]) == []

    def test_map_layout_to_regions_with_boxes(self):
        f = RenderingFunctor()
        boxes = [{"id": "b1", "x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0}]
        regions = f._map_layout_to_regions(boxes)
        assert len(regions) == 1
        assert regions[0].node_id == "b1"

    def test_map_text_nodes_empty(self):
        f = RenderingFunctor()
        assert f._map_text_nodes({}, {}) == []

    def test_map_text_nodes_with_text(self):
        f = RenderingFunctor()
        dom = {"children": [{"id": "t1", "type": "text", "content": "hello"}]}
        texts = f._map_text_nodes(dom, {})
        assert len(texts) == 1
        assert texts[0].content == "hello"

    def test_map_interactive_elements_with_buttons(self):
        f = RenderingFunctor()
        dom = {"children": [{"id": "btn1", "tag": "button"}]}
        zones = f._map_interactive_elements(dom)
        assert len(zones) == 1
        assert zones[0].node_id == "btn1"

    def test_compose_visual_elements(self):
        f = RenderingFunctor()
        r = ViewportRegion(x=0, y=0, width=100, height=100, node_id="n1")
        elements = f._compose_visual_elements([r], [], [])
        assert len(elements) == 1

    def test_apply_returns_visual_site(self):
        f = RenderingFunctor()
        dom = {"children": []}
        vs = f.apply(dom, {}, [])
        assert isinstance(vs, VisualSite)

    def test_apply_with_empty_inputs(self):
        f = RenderingFunctor()
        vs = f.apply({}, {}, [])
        assert len(vs.regions()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# RenderingDescentChecker
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderingDescentChecker:
    def test_no_overlap(self):
        a = _make_element("a", 0, 0, 50, 50)
        b = _make_element("b", 100, 100, 50, 50)
        vs = VisualSite([a, b])
        checker = RenderingDescentChecker()
        assert checker.check_visual_overlap(vs) == []

    def test_overlap_detected(self):
        a = _make_element("a", 0, 0, 100, 100)
        b = _make_element("b", 50, 50, 100, 100)
        vs = VisualSite([a, b])
        checker = RenderingDescentChecker()
        issues = checker.check_visual_overlap(vs)
        assert len(issues) >= 1

    def test_interaction_dead_zones_clear(self):
        el = _make_element("btn", 0, 0, 100, 50, interactive=True)
        vs = VisualSite([el])
        checker = RenderingDescentChecker()
        assert checker.check_interaction_dead_zones(vs) == []

    def test_layout_thrashing_stable(self):
        page = VisualPage(viewport_width=1280, viewport_height=800)
        checker = RenderingDescentChecker()
        assert checker.check_layout_thrashing([page]) == []

    def test_text_legibility_normal(self):
        el = _make_element("a", 0, 0, 100, 100, text="normal")
        vs = VisualSite([el])
        checker = RenderingDescentChecker()
        assert checker.check_text_legibility(vs) == []

    def test_text_legibility_tiny(self):
        region = ViewportRegion(x=0, y=0, width=100, height=100, node_id="tiny")
        tiny = TextRun(content="tiny", font_size=6.0, node_id="tiny")
        el = VisualElement(region=region, text_runs=[tiny])
        vs = VisualSite([el])
        checker = RenderingDescentChecker()
        issues = checker.check_text_legibility(vs)
        assert len(issues) >= 1

    def test_scroll_dependency(self):
        el = _make_element("below", 0, 2000, 100, 100)
        vs = VisualSite([el], viewport_height=800)
        checker = RenderingDescentChecker()
        issues = checker.check_scroll_dependency(vs)
        assert len(issues) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Viewport
# ═══════════════════════════════════════════════════════════════════════════


class TestViewport:
    def test_creation(self):
        vp = Viewport(width=1920, height=1080)
        assert vp.width == 1920

    def test_device_class_mobile(self):
        vp = Viewport(width=375, height=667)
        assert vp.device_class == "mobile"

    def test_device_class_tablet(self):
        vp = Viewport(width=810, height=1080)
        assert vp.device_class == "tablet"

    def test_device_class_desktop(self):
        vp = Viewport(width=1920, height=1080)
        assert vp.device_class == "desktop"

    def test_to_dict_from_dict(self):
        vp = Viewport(width=1280, height=800, pixel_ratio=2.0, is_touch=True)
        d = vp.to_dict()
        vp2 = Viewport.from_dict(d)
        assert vp2.width == 1280
        assert vp2.is_touch is True


# ═══════════════════════════════════════════════════════════════════════════
# ViewportPresets
# ═══════════════════════════════════════════════════════════════════════════


class TestViewportPresets:
    def test_iphone_se(self):
        vp = ViewportPresets.iphone_se()
        assert vp.width == 375
        assert vp.height == 667

    def test_iphone_14(self):
        vp = ViewportPresets.iphone_14()
        assert vp.width == 390

    def test_ipad_touch(self):
        vp = ViewportPresets.ipad()
        assert vp.is_touch is True

    def test_laptop_13(self):
        vp = ViewportPresets.laptop_13()
        assert vp.width == 1280

    def test_desktop_1080p(self):
        vp = ViewportPresets.desktop_1080p()
        assert vp.width == 1920

    def test_ultrawide(self):
        vp = ViewportPresets.ultrawide()
        assert vp.width == 3440

    def test_print_a4(self):
        vp = ViewportPresets.print_a4()
        assert vp.media_type == "print"

    def test_all_presets_count(self):
        presets = ViewportPresets.all_presets()
        assert len(presets) == 7


# ═══════════════════════════════════════════════════════════════════════════
# ViewportSimulator
# ═══════════════════════════════════════════════════════════════════════════


class TestViewportSimulator:
    def test_simulate_returns_visual_page(self):
        sim = ViewportSimulator()
        page = sim.simulate({"children": [{"id": "a"}]}, {}, Viewport(width=1280, height=800))
        assert isinstance(page, VisualPage)

    def test_simulate_all_presets(self):
        sim = ViewportSimulator()
        pages = sim.simulate_all_presets({"children": [{"id": "a"}]}, {})
        assert len(pages) == 7

    def test_compare_viewports_same(self):
        sim = ViewportSimulator()
        page = VisualPage(viewport_width=1280, viewport_height=800)
        diff = sim.compare_viewports(page, page)
        assert not diff.has_changes


# ═══════════════════════════════════════════════════════════════════════════
# InteractionKind
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionKind:
    def test_count(self):
        assert len(InteractionKind) == 8

    def test_click(self):
        assert InteractionKind.CLICK == "click"


# ═══════════════════════════════════════════════════════════════════════════
# InteractionEvent
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionEvent:
    def test_creation(self):
        ev = InteractionEvent(kind=InteractionKind.CLICK, target_node_id="btn1")
        assert ev.target_node_id == "btn1"

    def test_to_dict_from_dict(self):
        ev = InteractionEvent(
            kind=InteractionKind.HOVER,
            target_node_id="x",
            position=(10, 20),
            timestamp=50.0,
        )
        d = ev.to_dict()
        ev2 = InteractionEvent.from_dict(d)
        assert ev2.kind == InteractionKind.HOVER
        assert ev2.position == (10, 20)


# ═══════════════════════════════════════════════════════════════════════════
# InteractionTrace
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionTrace:
    def test_creation(self):
        tr = InteractionTrace()
        assert tr.duration_ms == 0.0

    def test_duration_ms(self):
        e1 = InteractionEvent(kind=InteractionKind.CLICK, target_node_id="a", timestamp=100)
        e2 = InteractionEvent(kind=InteractionKind.CLICK, target_node_id="b", timestamp=300)
        tr = InteractionTrace(events=[e1, e2])
        assert tr.duration_ms == 200.0


# ═══════════════════════════════════════════════════════════════════════════
# InteractionSimulator
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionSimulator:
    def _site(self) -> VisualSite:
        el = _make_element("btn", 0, 0, 100, 100, interactive=True)
        return VisualSite([el])

    def test_simulate_click(self):
        sim = InteractionSimulator()
        result = sim.simulate_click(self._site(), 50, 50)
        assert "targets" in result
        assert len(result["targets"]) >= 1

    def test_simulate_hover(self):
        sim = InteractionSimulator()
        result = sim.simulate_hover(self._site(), "btn")
        assert result["event"] == "hover"

    def test_simulate_focus(self):
        sim = InteractionSimulator()
        result = sim.simulate_focus(self._site(), "btn")
        assert result["event"] == "focus"

    def test_simulate_form_submit(self):
        sim = InteractionSimulator()
        result = sim.simulate_form_submit(self._site(), "form1", {"field": "val"})
        assert result["event"] == "form_submit"

    def test_trace_user_flow(self):
        sim = InteractionSimulator()
        events = [
            {"kind": "click", "target_node_id": "btn", "position": (50, 50)},
        ]
        trace = sim.trace_user_flow(self._site(), events)
        assert isinstance(trace, InteractionTrace)


# ═══════════════════════════════════════════════════════════════════════════
# InteractionAccessibilityChecker
# ═══════════════════════════════════════════════════════════════════════════


class TestInteractionAccessibilityChecker:
    def test_keyboard_navigable(self):
        el = _make_element("btn", 0, 0, 100, 100, interactive=True)
        vs = VisualSite([el])
        checker = InteractionAccessibilityChecker()
        issues = checker.check_keyboard_navigable(vs)
        assert isinstance(issues, list)

    def test_focus_visible(self):
        el = _make_element("btn", 0, 0, 100, 100, interactive=True)
        vs = VisualSite([el])
        checker = InteractionAccessibilityChecker()
        issues = checker.check_focus_visible(vs)
        assert isinstance(issues, list)

    def test_touch_targets_small(self):
        region = ViewportRegion(x=0, y=0, width=20, height=20, node_id="small")
        zone = InteractiveZone(node_id="small", event_types=["click"], bbox=(0, 0, 20, 20))
        el = VisualElement(region=region, interactive_zones=[zone])
        vs = VisualSite([el])
        checker = InteractionAccessibilityChecker()
        issues = checker.check_touch_targets(vs)
        assert len(issues) >= 1

    def test_touch_targets_large(self):
        region = ViewportRegion(x=0, y=0, width=100, height=100, node_id="large")
        zone = InteractiveZone(node_id="large", event_types=["click"], bbox=(0, 0, 100, 100))
        el = VisualElement(region=region, interactive_zones=[zone])
        vs = VisualSite([el])
        checker = InteractionAccessibilityChecker()
        issues = checker.check_touch_targets(vs)
        assert len(issues) == 0
