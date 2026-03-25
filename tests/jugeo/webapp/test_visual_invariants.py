"""Tests for the visual_invariants module."""
import math
import pytest

from jugeo.webapp.visual_invariants import (
    InvariantFamily,
    InvariantStatus,
    DeviceClass,
    STANDARD_DEVICE_CLASSES,
    InvariantResult,
    VisualInvariant,
    InvariantSuite,
    CrossDeviceDescentResult,
    ContainmentInvariant,
    ReadingOrderInvariant,
    VisualClusterInvariant,
    NonOcclusionInvariant,
    TopologicalChecker,
    ProportionalInvariant,
    UniformityInvariant,
    ProportionalChecker,
    ThresholdInvariant,
    ThresholdChecker,
    BehavioralInvariant,
    TriggerKind,
    BehavioralChecker,
    StructuralInvariant,
    StructuralChecker,
    ConditionalDeviceInvariant,
    DeviceCondition,
    ConditionalDeviceChecker,
    DeviceSite,
    CrossDeviceDescentChecker,
    DeviceSiteBuilder,
    VisualSite,
    ViewportRegion,
    TextRun,
    InteractiveZone,
    RenderingFunctor,
    VisualDescentChecker,
)


# =========================================================================
# models tests
# =========================================================================


def test_invariant_family_enum_values():
    assert InvariantFamily.TOPOLOGICAL == "topological"
    assert InvariantFamily.PROPORTIONAL == "proportional"
    assert InvariantFamily.THRESHOLD == "threshold"
    assert InvariantFamily.BEHAVIORAL == "behavioral"
    assert InvariantFamily.STRUCTURAL == "structural"
    assert InvariantFamily.CONDITIONAL_DEVICE == "conditional_device"


def test_invariant_status_enum_values():
    assert InvariantStatus.SATISFIED == "satisfied"
    assert InvariantStatus.VIOLATED == "violated"
    assert InvariantStatus.UNKNOWN == "unknown"
    assert InvariantStatus.NOT_APPLICABLE == "not_applicable"


def test_device_class_to_dict_roundtrip():
    dc = DeviceClass("test_device", (320, 480), "screen", 2.0, True)
    d = dc.to_dict()
    dc2 = DeviceClass.from_dict(d)
    assert dc2.name == dc.name
    assert dc2.width_range == dc.width_range
    assert dc2.media_type == dc.media_type
    assert dc2.pixel_ratio == dc.pixel_ratio
    assert dc2.is_touch == dc.is_touch


def test_standard_device_classes_exist():
    assert "mobile_portrait" in STANDARD_DEVICE_CLASSES
    assert "desktop" in STANDARD_DEVICE_CLASSES
    assert "print" in STANDARD_DEVICE_CLASSES
    assert "screen_reader" in STANDARD_DEVICE_CLASSES
    assert len(STANDARD_DEVICE_CLASSES) == 7


def test_standard_device_classes_mobile_portrait():
    mp = STANDARD_DEVICE_CLASSES["mobile_portrait"]
    assert mp.width_range == (320, 480)
    assert mp.is_touch is True
    assert mp.pixel_ratio == 2.0


def test_standard_device_classes_print():
    p = STANDARD_DEVICE_CLASSES["print"]
    assert p.media_type == "print"
    assert p.is_touch is False


def test_invariant_result_to_dict_roundtrip():
    ir = InvariantResult(
        invariant_id="test_inv",
        status=InvariantStatus.SATISFIED,
        evidence={"key": "value"},
        counterexample_device="mobile",
        message="All good",
    )
    d = ir.to_dict()
    ir2 = InvariantResult.from_dict(d)
    assert ir2.invariant_id == ir.invariant_id
    assert ir2.status == ir.status
    assert ir2.evidence == ir.evidence
    assert ir2.counterexample_device == ir.counterexample_device
    assert ir2.message == ir.message


def test_visual_invariant_to_dict_roundtrip():
    vi = VisualInvariant(
        id="vi_1",
        family=InvariantFamily.TOPOLOGICAL,
        description="Test invariant",
        subject_selector="#main",
        property_name="width",
        condition=">= 100",
        holds_on=["desktop", "tablet"],
    )
    d = vi.to_dict()
    vi2 = VisualInvariant.from_dict(d)
    assert vi2.id == vi.id
    assert vi2.family == vi.family
    assert vi2.holds_on == vi.holds_on


def test_invariant_suite_to_dict_roundtrip():
    vi = VisualInvariant(
        id="vi_1",
        family=InvariantFamily.THRESHOLD,
        description="Font size check",
        subject_selector="#text",
        property_name="font_size",
        condition=">= 12",
    )
    suite = InvariantSuite(id="suite_1", invariants=[vi], description="Test suite")
    d = suite.to_dict()
    suite2 = InvariantSuite.from_dict(d)
    assert suite2.id == suite.id
    assert len(suite2.invariants) == 1
    assert suite2.invariants[0].id == "vi_1"


def test_cross_device_descent_result_to_dict_roundtrip():
    cdr = CrossDeviceDescentResult(
        invariant_id="inv_1",
        per_device_results={
            "desktop": InvariantStatus.SATISFIED,
            "mobile_portrait": InvariantStatus.VIOLATED,
        },
        overlap_violations=["conflict at 480px"],
        globally_consistent=False,
    )
    d = cdr.to_dict()
    cdr2 = CrossDeviceDescentResult.from_dict(d)
    assert cdr2.invariant_id == cdr.invariant_id
    assert cdr2.per_device_results["desktop"] == InvariantStatus.SATISFIED
    assert cdr2.per_device_results["mobile_portrait"] == InvariantStatus.VIOLATED
    assert cdr2.globally_consistent is False


# =========================================================================
# topological tests
# =========================================================================


def _make_layout():
    return {
        "container": {"x": 0, "y": 0, "width": 200, "height": 200, "z_index": 0},
        "inside": {"x": 10, "y": 10, "width": 50, "height": 50, "z_index": 1},
        "outside": {"x": 250, "y": 250, "width": 50, "height": 50, "z_index": 0},
        "partial": {"x": 190, "y": 10, "width": 50, "height": 50, "z_index": 0},
        "a": {"x": 10, "y": 10, "width": 30, "height": 20, "z_index": 0},
        "b": {"x": 10, "y": 100, "width": 30, "height": 20, "z_index": 0},
        "c": {"x": 100, "y": 10, "width": 30, "height": 20, "z_index": 0},
        "overlay": {"x": 5, "y": 5, "width": 60, "height": 60, "z_index": 10},
        "far_away": {"x": 500, "y": 500, "width": 30, "height": 30, "z_index": 0},
    }


def test_containment_satisfied():
    checker = TopologicalChecker()
    result = checker.check_containment(_make_layout(), "inside", "container")
    assert result.status == InvariantStatus.SATISFIED


def test_containment_violated_outside():
    checker = TopologicalChecker()
    result = checker.check_containment(_make_layout(), "outside", "container")
    assert result.status == InvariantStatus.VIOLATED


def test_containment_missing_node_unknown():
    checker = TopologicalChecker()
    result = checker.check_containment(_make_layout(), "nonexistent", "container")
    assert result.status == InvariantStatus.UNKNOWN


def test_reading_order_first_above():
    checker = TopologicalChecker()
    result = checker.check_reading_order(_make_layout(), "a", "b")
    assert result.status == InvariantStatus.SATISFIED


def test_reading_order_violated():
    checker = TopologicalChecker()
    result = checker.check_reading_order(_make_layout(), "b", "a")
    assert result.status == InvariantStatus.VIOLATED


def test_reading_order_same_row_left_first():
    checker = TopologicalChecker()
    result = checker.check_reading_order(_make_layout(), "a", "c")
    assert result.status == InvariantStatus.SATISFIED


def test_visual_cluster_tight():
    checker = TopologicalChecker()
    layout = _make_layout()
    result = checker.check_visual_cluster(layout, ["a", "inside"], max_gap=100)
    assert result.status == InvariantStatus.SATISFIED


def test_visual_cluster_spread():
    checker = TopologicalChecker()
    layout = _make_layout()
    result = checker.check_visual_cluster(layout, ["a", "far_away"], max_gap=50)
    assert result.status == InvariantStatus.VIOLATED


def test_non_occlusion_clear():
    checker = TopologicalChecker()
    layout = _make_layout()
    result = checker.check_non_occlusion(layout, "outside")
    assert result.status == InvariantStatus.SATISFIED


def test_non_occlusion_occluded():
    checker = TopologicalChecker()
    layout = _make_layout()
    # "inside" has z_index=1 but "overlay" has z_index=10 and overlaps it
    result = checker.check_non_occlusion(layout, "inside")
    assert result.status == InvariantStatus.VIOLATED


def test_topological_checker_check_all():
    checker = TopologicalChecker()
    layout = _make_layout()
    invariants = [
        ContainmentInvariant(subject="inside", container="container"),
        ReadingOrderInvariant(first="a", second="b"),
        NonOcclusionInvariant(subject="outside"),
    ]
    results = checker.check_all(invariants, layout)
    assert len(results) == 3
    assert results[0].status == InvariantStatus.SATISFIED
    assert results[1].status == InvariantStatus.SATISFIED
    assert results[2].status == InvariantStatus.SATISFIED


def test_containment_invariant_to_dict_roundtrip():
    ci = ContainmentInvariant(subject="a", container="b", holds_on=["desktop"])
    d = ci.to_dict()
    ci2 = ContainmentInvariant.from_dict(d)
    assert ci2.subject == "a"
    assert ci2.container == "b"
    assert ci2.holds_on == ["desktop"]


# =========================================================================
# proportional tests
# =========================================================================


def _make_prop_layout():
    return {
        "a": {"x": 0, "y": 0, "width": 200, "height": 100, "z_index": 0},
        "b": {"x": 0, "y": 0, "width": 200, "height": 100, "z_index": 0},
        "c": {"x": 0, "y": 0, "width": 100, "height": 50, "z_index": 0},
        "d": {"x": 0, "y": 0, "width": 210, "height": 100, "z_index": 0},
    }


def _make_prop_styles():
    return {
        "a": {"font_size": 16.0},
        "b": {"font_size": 16.0},
        "c": {"font_size": 14.0},
        "d": {"font_size": 20.0},
    }


def test_proportion_equal():
    checker = ProportionalChecker()
    result = checker.check_proportion(
        _make_prop_layout(), _make_prop_styles(),
        "a", "width", "eq", "b", "width",
    )
    assert result.status == InvariantStatus.SATISFIED


def test_proportion_gt_satisfied():
    checker = ProportionalChecker()
    result = checker.check_proportion(
        _make_prop_layout(), _make_prop_styles(),
        "a", "width", "gt", "c", "width",
    )
    assert result.status == InvariantStatus.SATISFIED


def test_proportion_gt_violated():
    checker = ProportionalChecker()
    result = checker.check_proportion(
        _make_prop_layout(), _make_prop_styles(),
        "c", "width", "gt", "a", "width",
    )
    assert result.status == InvariantStatus.VIOLATED


def test_proportion_approx_satisfied():
    checker = ProportionalChecker()
    # a.width=200, d.width=210 -> factor=1.0 -> target=210
    # deviation = |200-210|/210 ≈ 0.0476 < 0.05
    result = checker.check_proportion(
        _make_prop_layout(), _make_prop_styles(),
        "a", "width", "approx", "d", "width", factor=1.0, tolerance=0.05,
    )
    assert result.status == InvariantStatus.SATISFIED


def test_proportion_approx_violated():
    checker = ProportionalChecker()
    # a.width=200, c.width=100 -> target=100 -> deviation=100/100=1.0
    result = checker.check_proportion(
        _make_prop_layout(), _make_prop_styles(),
        "a", "width", "approx", "c", "width", factor=1.0, tolerance=0.05,
    )
    assert result.status == InvariantStatus.VIOLATED


def test_uniformity_satisfied():
    checker = ProportionalChecker()
    result = checker.check_uniformity(
        _make_prop_layout(), _make_prop_styles(),
        ["a", "b"], "font_size", tolerance=0.05,
    )
    assert result.status == InvariantStatus.SATISFIED


def test_uniformity_violated():
    checker = ProportionalChecker()
    result = checker.check_uniformity(
        _make_prop_layout(), _make_prop_styles(),
        ["a", "d"], "font_size", tolerance=0.05,
    )
    assert result.status == InvariantStatus.VIOLATED


def test_proportional_checker_check_all():
    checker = ProportionalChecker()
    invariants = [
        ProportionalInvariant(
            subject="a", property="width", relation="eq",
            reference="b", reference_property="width",
        ),
        UniformityInvariant(
            subjects_selector=["a", "b"], property="font_size",
        ),
    ]
    results = checker.check_all(invariants, _make_prop_layout(), _make_prop_styles())
    assert len(results) == 2
    assert all(r.status == InvariantStatus.SATISFIED for r in results)


def test_proportional_invariant_to_dict_roundtrip():
    pi = ProportionalInvariant(
        subject="a", property="width", relation="approx",
        reference="b", reference_property="width",
        factor=2.0, tolerance=0.1, holds_on=["desktop"],
    )
    d = pi.to_dict()
    pi2 = ProportionalInvariant.from_dict(d)
    assert pi2.subject == "a"
    assert pi2.factor == 2.0
    assert pi2.tolerance == 0.1


# =========================================================================
# threshold tests
# =========================================================================


def test_font_size_satisfied():
    checker = ThresholdChecker()
    styles = {"text": {"font_size": 16.0}}
    result = checker.check_font_size(styles, "text", min_px=12.0)
    assert result.status == InvariantStatus.SATISFIED


def test_font_size_violated():
    checker = ThresholdChecker()
    styles = {"text": {"font_size": 10.0}}
    result = checker.check_font_size(styles, "text", min_px=12.0)
    assert result.status == InvariantStatus.VIOLATED


def test_touch_target_satisfied():
    checker = ThresholdChecker()
    layout = {"btn": {"x": 0, "y": 0, "width": 48, "height": 48}}
    result = checker.check_touch_target(layout, "btn", min_px=44.0)
    assert result.status == InvariantStatus.SATISFIED


def test_touch_target_violated_too_small():
    checker = ThresholdChecker()
    layout = {"btn": {"x": 0, "y": 0, "width": 30, "height": 30}}
    result = checker.check_touch_target(layout, "btn", min_px=44.0)
    assert result.status == InvariantStatus.VIOLATED


def test_no_horizontal_scroll_ok():
    checker = ThresholdChecker()
    layout = {
        "a": {"x": 0, "y": 0, "width": 300, "height": 100},
        "b": {"x": 100, "y": 0, "width": 200, "height": 100},
    }
    result = checker.check_no_horizontal_scroll(layout, viewport_width=400)
    assert result.status == InvariantStatus.SATISFIED


def test_no_horizontal_scroll_violated():
    checker = ThresholdChecker()
    layout = {
        "a": {"x": 0, "y": 0, "width": 300, "height": 100},
        "b": {"x": 300, "y": 0, "width": 200, "height": 100},
    }
    result = checker.check_no_horizontal_scroll(layout, viewport_width=400)
    assert result.status == InvariantStatus.VIOLATED


def test_contrast_ratio_black_white():
    checker = ThresholdChecker()
    styles = {"text": {"color": "black", "background_color": "white"}}
    result = checker.check_contrast_ratio(styles, "text", min_ratio=4.5)
    assert result.status == InvariantStatus.SATISFIED
    assert result.evidence["contrast_ratio"] == pytest.approx(21.0, abs=0.1)


def test_contrast_ratio_too_low():
    checker = ThresholdChecker()
    styles = {"text": {"color": "#777777", "background_color": "#999999"}}
    result = checker.check_contrast_ratio(styles, "text", min_ratio=4.5)
    assert result.status == InvariantStatus.VIOLATED


def test_contrast_ratio_satisfied():
    checker = ThresholdChecker()
    styles = {"text": {"color": "#000000", "background_color": "#ffffff"}}
    result = checker.check_contrast_ratio(styles, "text", min_ratio=4.5)
    assert result.status == InvariantStatus.SATISFIED


def test_parse_color_hex6():
    checker = ThresholdChecker()
    assert checker._parse_color("#ff0000") == (255, 0, 0)


def test_parse_color_hex3():
    checker = ThresholdChecker()
    assert checker._parse_color("#f00") == (255, 0, 0)


def test_parse_color_rgb():
    checker = ThresholdChecker()
    assert checker._parse_color("rgb(128, 64, 32)") == (128, 64, 32)


def test_parse_color_named_white():
    checker = ThresholdChecker()
    assert checker._parse_color("white") == (255, 255, 255)


def test_parse_color_named_black():
    checker = ThresholdChecker()
    assert checker._parse_color("black") == (0, 0, 0)


def test_relative_luminance_white():
    checker = ThresholdChecker()
    assert checker._relative_luminance(255, 255, 255) == pytest.approx(1.0, abs=0.01)


def test_relative_luminance_black():
    checker = ThresholdChecker()
    assert checker._relative_luminance(0, 0, 0) == pytest.approx(0.0, abs=0.001)


def test_contrast_ratio_formula():
    checker = ThresholdChecker()
    ratio = checker._contrast_ratio(1.0, 0.0)
    assert ratio == pytest.approx(21.0, abs=0.01)


def test_threshold_checker_check_all():
    checker = ThresholdChecker()
    layout = {"btn": {"x": 0, "y": 0, "width": 50, "height": 50}}
    styles = {"text": {"font_size": 14.0, "color": "black", "background_color": "white"}}
    invariants = [
        ThresholdInvariant(subject="text", property="font_size", relation="gte", threshold=12.0),
        ThresholdInvariant(subject="btn", property="touch_target", relation="gte", threshold=44.0),
        ThresholdInvariant(subject="text", property="contrast_ratio", relation="gte", threshold=4.5),
    ]
    results = checker.check_all(invariants, layout, styles)
    assert len(results) == 3
    assert all(r.status == InvariantStatus.SATISFIED for r in results)


# =========================================================================
# behavioral tests
# =========================================================================


def _make_dom():
    return {
        "tag": "html",
        "id": "root",
        "children": [
            {
                "tag": "button",
                "id": "btn1",
                "attrs": {"type": "button"},
                "events": ["click", "focus"],
                "children": [],
            },
            {
                "tag": "div",
                "id": "menu",
                "attrs": {},
                "events": ["click"],
                "children": [
                    {
                        "tag": "ul",
                        "id": "dropdown1",
                        "attrs": {"class": "dropdown"},
                        "children": [],
                    },
                ],
            },
        ],
    }


def test_hover_distinction_different():
    checker = BehavioralChecker()
    default = {"btn1": {"color": "black", "background_color": "white"}}
    hover = {"btn1": {"color": "blue", "background_color": "white"}}
    result = checker.check_hover_distinction(default, hover, "btn1")
    assert result.status == InvariantStatus.SATISFIED


def test_hover_distinction_same_state_violated():
    checker = BehavioralChecker()
    default = {"btn1": {"color": "black"}}
    hover = {"btn1": {"color": "black"}}
    result = checker.check_hover_distinction(default, hover, "btn1")
    assert result.status == InvariantStatus.VIOLATED


def test_focus_visible_with_outline():
    checker = BehavioralChecker()
    default = {"btn1": {"outline": "none"}}
    focus = {"btn1": {"outline": "2px solid blue"}}
    result = checker.check_focus_visible(default, focus, "btn1")
    assert result.status == InvariantStatus.SATISFIED


def test_focus_visible_no_change_violated():
    checker = BehavioralChecker()
    default = {"btn1": {"outline": "none"}}
    focus = {"btn1": {"outline": "none"}}
    result = checker.check_focus_visible(default, focus, "btn1")
    assert result.status == InvariantStatus.VIOLATED


def test_error_visibility_in_viewport():
    checker = BehavioralChecker()
    layout = {"error_msg": {"x": 10, "y": 100, "width": 200, "height": 30}}
    result = checker.check_error_visibility(layout, "error_msg", viewport_height=800.0)
    assert result.status == InvariantStatus.SATISFIED


def test_error_visibility_out_of_viewport():
    checker = BehavioralChecker()
    layout = {"error_msg": {"x": 10, "y": 900, "width": 200, "height": 30}}
    result = checker.check_error_visibility(layout, "error_msg", viewport_height=800.0)
    assert result.status == InvariantStatus.VIOLATED


def test_toggle_visibility_display_none_to_block():
    checker = BehavioralChecker()
    before = {"panel": {"display": "none"}}
    after = {"panel": {"display": "block"}}
    result = checker.check_toggle_visibility(before, after, "panel")
    assert result.status == InvariantStatus.SATISFIED


def test_toggle_visibility_no_change():
    checker = BehavioralChecker()
    before = {"panel": {"display": "block"}}
    after = {"panel": {"display": "block"}}
    result = checker.check_toggle_visibility(before, after, "panel")
    assert result.status == InvariantStatus.VIOLATED


def test_simulate_trigger_hover():
    checker = BehavioralChecker()
    dom = _make_dom()
    styles = {"btn1": {"color": "black"}}
    new_styles = checker.simulate_trigger(dom, styles, TriggerKind.HOVER, "btn1")
    assert new_styles["btn1"].get("outline") == "2px solid blue"
    # Original unchanged
    assert "outline" not in styles.get("btn1", {})


def test_simulate_trigger_focus():
    checker = BehavioralChecker()
    dom = _make_dom()
    styles = {"btn1": {"color": "black"}}
    new_styles = checker.simulate_trigger(dom, styles, TriggerKind.FOCUS, "btn1")
    assert new_styles["btn1"].get("outline") == "2px solid orange"


def test_behavioral_checker_check_all():
    checker = BehavioralChecker()
    dom = _make_dom()
    styles = {"btn1": {"color": "black"}}
    layout = {"btn1": {"x": 0, "y": 0, "width": 100, "height": 40}}
    invariants = [
        BehavioralInvariant(
            subject="btn1", trigger=TriggerKind.HOVER,
            property="outline", expected_value="2px solid blue",
        ),
    ]
    results = checker.check_all(invariants, dom, styles, layout)
    assert len(results) == 1
    assert results[0].status == InvariantStatus.SATISFIED


def test_behavioral_invariant_to_dict_roundtrip():
    bi = BehavioralInvariant(
        subject="btn1", trigger=TriggerKind.FOCUS,
        property="outline", expected_value="2px solid blue",
        holds_on=["desktop"],
    )
    d = bi.to_dict()
    bi2 = BehavioralInvariant.from_dict(d)
    assert bi2.subject == "btn1"
    assert bi2.trigger == TriggerKind.FOCUS


# =========================================================================
# structural tests
# =========================================================================


def _make_full_dom():
    return {
        "tag": "html",
        "id": "root",
        "attrs": {"lang": "en"},
        "children": [
            {"tag": "head", "id": "head", "children": []},
            {
                "tag": "body",
                "id": "body",
                "children": [
                    {"tag": "h1", "id": "h1", "children": [], "attrs": {}},
                    {"tag": "h2", "id": "h2", "children": [], "attrs": {}},
                    {"tag": "h3", "id": "h3", "children": [], "attrs": {}},
                    {
                        "tag": "form",
                        "id": "login_form",
                        "children": [
                            {
                                "tag": "label",
                                "id": "lbl_email",
                                "attrs": {"for": "email"},
                                "children": [],
                            },
                            {
                                "tag": "input",
                                "id": "email",
                                "attrs": {"type": "text"},
                                "children": [],
                            },
                            {
                                "tag": "button",
                                "id": "submit_btn",
                                "attrs": {"type": "submit"},
                                "children": [],
                            },
                        ],
                        "attrs": {},
                    },
                    {
                        "tag": "img",
                        "id": "logo",
                        "attrs": {"alt": "Logo image", "src": "logo.png"},
                        "children": [],
                    },
                    {
                        "tag": "a",
                        "id": "link1",
                        "attrs": {"href": "/home", "role": "link"},
                        "children": [],
                    },
                ],
                "attrs": {},
            },
        ],
        "attrs": {"lang": "en"},
    }


def test_form_submit_button_present():
    checker = StructuralChecker()
    result = checker.check_form_submit(_make_full_dom(), "login_form")
    assert result.status == InvariantStatus.SATISFIED


def test_form_submit_input_present():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {
                "tag": "form", "id": "f1", "children": [
                    {"tag": "input", "id": "s1", "attrs": {"type": "submit"}, "children": []},
                ],
                "attrs": {},
            },
        ],
        "attrs": {},
    }
    result = checker.check_form_submit(dom, "f1")
    assert result.status == InvariantStatus.SATISFIED


def test_form_submit_missing():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {
                "tag": "form", "id": "f1", "children": [
                    {"tag": "input", "id": "i1", "attrs": {"type": "text"}, "children": []},
                ],
                "attrs": {},
            },
        ],
        "attrs": {},
    }
    result = checker.check_form_submit(dom, "f1")
    assert result.status == InvariantStatus.VIOLATED


def test_alt_text_all_present():
    checker = StructuralChecker()
    result = checker.check_alt_text(_make_full_dom())
    assert result.status == InvariantStatus.SATISFIED


def test_alt_text_missing():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {"tag": "img", "id": "bad_img", "attrs": {}, "children": []},
        ],
        "attrs": {},
    }
    result = checker.check_alt_text(dom)
    assert result.status == InvariantStatus.VIOLATED


def test_heading_hierarchy_valid():
    checker = StructuralChecker()
    result = checker.check_heading_hierarchy(_make_full_dom())
    assert result.status == InvariantStatus.SATISFIED


def test_heading_hierarchy_invalid_skip():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {"tag": "h1", "id": "h1", "children": [], "attrs": {}},
            {"tag": "h3", "id": "h3", "children": [], "attrs": {}},
        ],
        "attrs": {},
    }
    result = checker.check_heading_hierarchy(dom)
    assert result.status == InvariantStatus.VIOLATED


def test_label_association_for_attribute():
    checker = StructuralChecker()
    result = checker.check_label_association(_make_full_dom())
    assert result.status == InvariantStatus.SATISFIED


def test_label_association_nested():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {
                "tag": "label", "id": "lbl", "attrs": {}, "children": [
                    {"tag": "input", "id": "inp1", "attrs": {"type": "text"}, "children": []},
                ],
            },
        ],
        "attrs": {},
    }
    result = checker.check_label_association(dom)
    assert result.status == InvariantStatus.SATISFIED


def test_label_association_missing():
    checker = StructuralChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {"tag": "input", "id": "orphan", "attrs": {"type": "text"}, "children": []},
        ],
        "attrs": {},
    }
    result = checker.check_label_association(dom)
    assert result.status == InvariantStatus.VIOLATED


def test_lang_attribute_present():
    checker = StructuralChecker()
    result = checker.check_lang_attribute(_make_full_dom())
    assert result.status == InvariantStatus.SATISFIED


def test_lang_attribute_missing():
    checker = StructuralChecker()
    dom = {"tag": "html", "id": "root", "attrs": {}, "children": []}
    result = checker.check_lang_attribute(dom)
    assert result.status == InvariantStatus.VIOLATED


def test_aria_roles_satisfied():
    checker = StructuralChecker()
    result = checker.check_aria_roles(_make_full_dom())
    assert result.status == InvariantStatus.SATISFIED


def test_structural_checker_check_all():
    checker = StructuralChecker()
    dom = _make_full_dom()
    invariants = [
        StructuralInvariant(subject="login_form", property_check="form_submit"),
        StructuralInvariant(subject="", property_check="alt_text"),
        StructuralInvariant(subject="", property_check="heading_hierarchy"),
        StructuralInvariant(subject="", property_check="lang_attribute"),
    ]
    results = checker.check_all(invariants, dom)
    assert len(results) == 4
    assert all(r.status == InvariantStatus.SATISFIED for r in results)


def test_structural_invariant_to_dict_roundtrip():
    si = StructuralInvariant(
        subject="form1", property_check="form_submit",
        expected_value="true", holds_on=["desktop"],
    )
    d = si.to_dict()
    si2 = StructuralInvariant.from_dict(d)
    assert si2.subject == "form1"
    assert si2.property_check == "form_submit"


# =========================================================================
# conditional device tests
# =========================================================================


def test_device_condition_matches():
    cond = DeviceCondition(device_class_name="mobile_portrait", media_type="screen")
    device = STANDARD_DEVICE_CLASSES["mobile_portrait"]
    assert cond.matches(device) is True


def test_device_condition_no_match():
    cond = DeviceCondition(device_class_name="mobile_portrait", media_type="screen")
    device = STANDARD_DEVICE_CLASSES["desktop"]
    assert cond.matches(device) is False


def test_check_mobile_collapse_hidden():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "mobile_portrait": {"nav": {"display": "none"}},
    }
    result = checker.check_mobile_collapse(styles_per_device, "nav")
    assert result.status == InvariantStatus.SATISFIED


def test_check_mobile_collapse_visible_violated():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "mobile_portrait": {"nav": {"display": "flex"}},
    }
    result = checker.check_mobile_collapse(styles_per_device, "nav")
    assert result.status == InvariantStatus.VIOLATED


def test_check_desktop_sidebar_visible():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "desktop": {"sidebar": {"display": "block"}},
    }
    result = checker.check_desktop_sidebar(styles_per_device, "sidebar")
    assert result.status == InvariantStatus.SATISFIED


def test_check_desktop_sidebar_hidden_violated():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "desktop": {"sidebar": {"display": "none"}},
    }
    result = checker.check_desktop_sidebar(styles_per_device, "sidebar")
    assert result.status == InvariantStatus.VIOLATED


def test_check_print_hide_hidden():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "print": {"ad_banner": {"display": "none"}, "nav": {"display": "none"}},
    }
    result = checker.check_print_hide(styles_per_device, ["ad_banner", "nav"])
    assert result.status == InvariantStatus.SATISFIED


def test_check_print_hide_visible_violated():
    checker = ConditionalDeviceChecker()
    styles_per_device = {
        "print": {"ad_banner": {"display": "block"}, "nav": {"display": "none"}},
    }
    result = checker.check_print_hide(styles_per_device, ["ad_banner", "nav"])
    assert result.status == InvariantStatus.VIOLATED


def test_check_high_dpi_images_has_srcset():
    checker = ConditionalDeviceChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {"tag": "img", "id": "img1", "attrs": {"srcset": "img@2x.png 2x"}, "children": []},
        ],
        "attrs": {},
    }
    device = DeviceClass("retina", (320, 480), "screen", 2.0, True)
    result = checker.check_high_dpi_images(dom, device)
    assert result.status == InvariantStatus.SATISFIED


def test_check_high_dpi_images_no_srcset():
    checker = ConditionalDeviceChecker()
    dom = {
        "tag": "html", "id": "root", "children": [
            {"tag": "img", "id": "img1", "attrs": {"src": "img.png"}, "children": []},
        ],
        "attrs": {},
    }
    device = DeviceClass("retina", (320, 480), "screen", 2.0, True)
    result = checker.check_high_dpi_images(dom, device)
    assert result.status == InvariantStatus.VIOLATED


def test_conditional_device_checker_check_all():
    checker = ConditionalDeviceChecker()
    cond = DeviceCondition(device_class_name="mobile_portrait", media_type="screen")
    inv = ConditionalDeviceInvariant(
        condition=cond, subject="nav", property="display", expected_value="none",
    )
    styles_per_device = {
        "mobile_portrait": {"nav": {"display": "none"}},
        "desktop": {"nav": {"display": "flex"}},
    }
    results = checker.check_all([inv], styles_per_device)
    assert len(results) == 1
    assert results[0].status == InvariantStatus.SATISFIED


def test_conditional_device_invariant_to_dict_roundtrip():
    cond = DeviceCondition(device_class_name="desktop", media_type="screen")
    inv = ConditionalDeviceInvariant(
        condition=cond, subject="sidebar", property="display", expected_value="block",
    )
    d = inv.to_dict()
    inv2 = ConditionalDeviceInvariant.from_dict(d)
    assert inv2.subject == "sidebar"
    assert inv2.condition.device_class_name == "desktop"


# =========================================================================
# device site tests
# =========================================================================


def test_device_site_overlapping_pairs():
    site = DeviceSiteBuilder().add_standard_devices().build()
    pairs = site.overlapping_pairs()
    # mobile_portrait(320-480) and mobile_landscape(480-768) share boundary at 480
    # but overlap requires lo < hi, so only true overlaps
    overlap_names = {(p[0].name, p[1].name) for p in pairs}
    # At minimum, print and screen_reader overlap with everything due to (0, 9999) range
    assert len(pairs) > 0


def test_device_site_restriction_morphisms():
    site = DeviceSite(
        device_classes=[
            DeviceClass("big", (0, 1920), "screen"),
            DeviceClass("small", (320, 480), "screen"),
        ]
    )
    morphisms = site.restriction_morphisms()
    assert ("big", "small") in morphisms


def test_device_site_builder_fluent():
    site = (
        DeviceSiteBuilder()
        .add_device("phone", (320, 480), is_touch=True)
        .add_device("desktop", (1024, 1920))
        .build()
    )
    assert len(site.device_classes) == 2
    assert site.get_device("phone") is not None
    assert site.get_device("desktop") is not None


def test_cross_device_descent_checker_globally_consistent():
    site = DeviceSite(
        device_classes=[
            DeviceClass("mobile", (320, 480), "screen"),
            DeviceClass("desktop", (1024, 1920), "screen"),
        ]
    )
    checker = CrossDeviceDescentChecker(site)
    inv = VisualInvariant(
        id="font_min",
        family=InvariantFamily.THRESHOLD,
        description="Font size >= 12",
        subject_selector="text",
        property_name="font_size",
        condition=">= 12",
    )
    suite = InvariantSuite(id="suite1", invariants=[inv])
    styles_per_device = {
        "mobile": {"text": {"font_size": 14}},
        "desktop": {"text": {"font_size": 16}},
    }
    layout_per_device = {
        "mobile": {},
        "desktop": {},
    }
    results = checker.check_descent(suite, styles_per_device, layout_per_device)
    assert len(results) == 1
    assert results[0].globally_consistent is True


def test_cross_device_descent_checker_violation():
    site = DeviceSite(
        device_classes=[
            DeviceClass("mobile", (320, 480), "screen"),
            DeviceClass("desktop", (1024, 1920), "screen"),
        ]
    )
    checker = CrossDeviceDescentChecker(site)
    inv = VisualInvariant(
        id="font_min",
        family=InvariantFamily.THRESHOLD,
        description="Font size >= 12",
        subject_selector="text",
        property_name="font_size",
        condition=">= 12",
    )
    suite = InvariantSuite(id="suite1", invariants=[inv])
    styles_per_device = {
        "mobile": {"text": {"font_size": 10}},  # VIOLATED
        "desktop": {"text": {"font_size": 16}},
    }
    layout_per_device = {
        "mobile": {},
        "desktop": {},
    }
    results = checker.check_descent(suite, styles_per_device, layout_per_device)
    assert len(results) == 1
    assert results[0].globally_consistent is False


# =========================================================================
# rendering functor tests
# =========================================================================


def test_viewport_region_contains():
    outer = ViewportRegion(0, 0, 200, 200)
    inner = ViewportRegion(10, 10, 50, 50)
    assert outer.contains(inner) is True
    assert inner.contains(outer) is False


def test_viewport_region_overlaps():
    a = ViewportRegion(0, 0, 100, 100)
    b = ViewportRegion(50, 50, 100, 100)
    c = ViewportRegion(200, 200, 50, 50)
    assert a.overlaps(b) is True
    assert a.overlaps(c) is False


def test_viewport_region_to_dict_roundtrip():
    vr = ViewportRegion(10, 20, 30, 40, "node1")
    d = vr.to_dict()
    vr2 = ViewportRegion.from_dict(d)
    assert vr2.x == 10
    assert vr2.node_id == "node1"


def test_rendering_functor_apply_basic():
    functor = RenderingFunctor()
    dom = {
        "tag": "div", "id": "main", "children": [],
        "attrs": {},
    }
    styles = {"main": {"font_size": 16, "color": "black"}}
    layout = {"main": {"x": 0, "y": 0, "width": 200, "height": 100}}
    vs = functor.apply(dom, styles, layout)
    assert isinstance(vs, VisualSite)


def test_rendering_functor_produces_regions():
    functor = RenderingFunctor()
    dom = {
        "tag": "div", "id": "main", "children": [
            {"tag": "p", "id": "para", "children": [], "attrs": {}},
        ],
        "attrs": {},
    }
    styles = {}
    layout = {
        "main": {"x": 0, "y": 0, "width": 200, "height": 100},
        "para": {"x": 10, "y": 10, "width": 180, "height": 40},
    }
    vs = functor.apply(dom, styles, layout)
    assert len(vs.regions) == 2
    node_ids = {r.node_id for r in vs.regions}
    assert "main" in node_ids
    assert "para" in node_ids


def test_rendering_functor_produces_text_runs():
    functor = RenderingFunctor()
    dom = {
        "tag": "p", "id": "text1", "text": "Hello world",
        "children": [], "attrs": {},
    }
    styles = {"text1": {"font_size": 14, "color": "black", "font_family": "Arial"}}
    layout = {"text1": {"x": 0, "y": 0, "width": 100, "height": 20}}
    vs = functor.apply(dom, styles, layout)
    assert len(vs.text_runs) == 1
    assert vs.text_runs[0].content == "Hello world"
    assert vs.text_runs[0].font == "Arial"


def test_rendering_functor_produces_interactive_zones():
    functor = RenderingFunctor()
    dom = {
        "tag": "button", "id": "btn", "events": ["click", "focus"],
        "children": [], "attrs": {},
    }
    styles = {}
    layout = {"btn": {"x": 10, "y": 10, "width": 80, "height": 40}}
    vs = functor.apply(dom, styles, layout)
    assert len(vs.interactive_zones) == 1
    assert vs.interactive_zones[0].node_id == "btn"
    assert "click" in vs.interactive_zones[0].event_types
    assert vs.interactive_zones[0].bbox is not None


def test_visual_descent_checker_overlap():
    checker = VisualDescentChecker()
    regions = [
        ViewportRegion(0, 0, 200, 200, "outer"),
        ViewportRegion(10, 10, 50, 50, "inner"),
    ]
    violations = checker.check_visual_overlap(regions)
    assert len(violations) == 1
    assert "outer" in violations[0]
    assert "inner" in violations[0]


def test_visual_descent_checker_dead_zones():
    checker = VisualDescentChecker()
    zones = [
        InteractiveZone("btn1", ["click"], bbox=ViewportRegion(0, 0, 50, 50, "btn1")),
        InteractiveZone("btn2", ["click"], bbox=None),
    ]
    regions = [ViewportRegion(0, 0, 50, 50, "btn1")]
    violations = checker.check_interaction_dead_zones(zones, regions)
    assert len(violations) == 1
    assert "btn2" in violations[0]


def test_visual_descent_checker_text_legibility():
    checker = VisualDescentChecker()
    runs = [
        TextRun("Big text", "Arial", 16.0, "black", "p1"),
        TextRun("Tiny text", "Arial", 8.0, "gray", "p2"),
    ]
    violations = checker.check_text_legibility(runs)
    assert len(violations) == 1
    assert "p2" in violations[0]


def test_visual_site_to_dict_roundtrip():
    vs = VisualSite(
        regions=[ViewportRegion(0, 0, 100, 50, "r1")],
        text_runs=[TextRun("hello", "Arial", 14, "black", "t1")],
        interactive_zones=[InteractiveZone("z1", ["click"])],
        animation_frames=[],
    )
    d = vs.to_dict()
    vs2 = VisualSite.from_dict(d)
    assert len(vs2.regions) == 1
    assert vs2.regions[0].node_id == "r1"
    assert len(vs2.text_runs) == 1
    assert vs2.text_runs[0].content == "hello"
    assert len(vs2.interactive_zones) == 1
