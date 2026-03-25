"""Tests for jugeo.webapp.dom — DOM sheaf-theoretic model.

90+ tests covering models, parsing, selectors, specificity, cascade,
inheritance, media queries, layout, algorithms, and theorems.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.webapp.dom.models import (
    CSSPropertyKind,
    CSSRule,
    CSSValue,
    CSSValueType,
    CascadeObstruction,
    CascadeObstructionKind,
    ComputedStyle,
    DOMNode,
    DOMNodeKind,
    DOMSection,
    ObstructionSeverity,
    Specificity,
)
from jugeo.webapp.dom.dom_site import (
    CombinatorKind,
    DOMSite,
    SelectorChain,
    SelectorParser,
    SelectorPart,
)
from jugeo.webapp.dom.specificity import (
    SpecificityConflictDetector,
    compare_specificity,
    compute_specificity,
    specificity_sort,
)
from jugeo.webapp.dom.css_cascade import (
    CSSCascadeEngine,
    CascadeDescentChecker,
    INHERITABLE_PROPERTIES,
    INITIAL_VALUES,
)
from jugeo.webapp.dom.media_queries import (
    BreakpointAnalyzer,
    MediaQuery,
    MediaQueryOverlapAnalyzer,
    MediaQueryParser,
    MediaType,
)
from jugeo.webapp.dom.layout_model import (
    BoxModel,
    ContainmentChecker,
    LayoutBox,
    LayoutEngine,
    LayoutKind,
    OverlapDetector,
)
from jugeo.webapp.dom.inheritance import (
    INHERITABLE,
    NON_INHERITABLE,
    InheritanceModel,
    InitialValueRegistry,
)
from jugeo.webapp.dom.algorithms import (
    AccessibilityChecker,
    DOMChange,
    DOMChangeKind,
    DOMDiffEngine,
    SelectorCoverageAnalyzer,
)
from jugeo.webapp.dom.theorems import (
    CascadeDescentTheorem,
    ContainmentPreservationTheorem,
    InheritanceCompletionTheorem,
    MediaQueryGluingTheorem,
    SelectorCoverageTheorem,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def simple_html():
    return '<div id="main" class="container"><p class="text">Hello</p><span>World</span></div>'


@pytest.fixture
def simple_dom(simple_html):
    return DOMSite.from_html(simple_html)


@pytest.fixture
def nested_html():
    return (
        '<html lang="en"><head></head><body>'
        '<div id="nav" class="menu">'
        '<ul><li class="item active">Home</li><li class="item">About</li></ul>'
        '</div>'
        '<main id="content"><h1>Title</h1><p>Text</p></main>'
        '</body></html>'
    )


@pytest.fixture
def nested_dom(nested_html):
    return DOMSite.from_html(nested_html)


@pytest.fixture
def basic_rules():
    return [
        CSSRule(
            selector="div",
            properties={"color": CSSValue(raw="red")},
            specificity=Specificity(0, 0, 1),
            source_order=0,
        ),
        CSSRule(
            selector=".container",
            properties={"color": CSSValue(raw="blue"), "font-size": CSSValue(raw="14px")},
            specificity=Specificity(0, 1, 0),
            source_order=1,
        ),
        CSSRule(
            selector="#main",
            properties={"color": CSSValue(raw="green")},
            specificity=Specificity(1, 0, 0),
            source_order=2,
        ),
    ]


# =====================================================================
# 1. Models (10 tests)
# =====================================================================

class TestModels:
    def test_specificity_ordering_element_lt_class(self):
        assert Specificity(0, 0, 1) < Specificity(0, 1, 0)

    def test_specificity_ordering_class_lt_id(self):
        assert Specificity(0, 1, 0) < Specificity(1, 0, 0)

    def test_specificity_ordering_full(self):
        assert Specificity(0, 0, 1) < Specificity(0, 1, 0) < Specificity(1, 0, 0)

    def test_specificity_addition(self):
        s = Specificity(1, 2, 3) + Specificity(0, 1, 2)
        assert s == Specificity(1, 3, 5)

    def test_specificity_repr(self):
        assert repr(Specificity(1, 2, 3)) == "(1,2,3)"

    def test_css_value_roundtrip(self):
        v = CSSValue(raw="16px", value_type=CSSValueType.LENGTH, computed="16px")
        d = v.to_dict()
        v2 = CSSValue.from_dict(d)
        assert v2.raw == "16px"
        assert v2.value_type == CSSValueType.LENGTH
        assert v2.computed == "16px"

    def test_css_rule_roundtrip(self):
        r = CSSRule(
            selector=".foo",
            properties={"color": CSSValue(raw="red")},
            specificity=Specificity(0, 1, 0),
            source_order=5,
        )
        d = r.to_dict()
        r2 = CSSRule.from_dict(d)
        assert r2.selector == ".foo"
        assert r2.properties["color"].raw == "red"
        assert r2.specificity == Specificity(0, 1, 0)

    def test_dom_node_roundtrip(self):
        n = DOMNode(
            node_id="n1", tag="div", classes=["a", "b"],
            attributes={"data-x": "1"}, children=["n2"],
        )
        d = n.to_dict()
        n2 = DOMNode.from_dict(d)
        assert n2.node_id == "n1"
        assert n2.classes == ["a", "b"]
        assert n2.attributes["data-x"] == "1"

    def test_cascade_obstruction_roundtrip(self):
        o = CascadeObstruction(
            kind=CascadeObstructionKind.SPECIFICITY_CONFLICT,
            selector1=".a",
            selector2=".b",
            property_name="color",
            severity=ObstructionSeverity.HIGH,
            node_ids=["n1"],
        )
        d = o.to_dict()
        o2 = CascadeObstruction.from_dict(d)
        assert o2.kind == CascadeObstructionKind.SPECIFICITY_CONFLICT
        assert o2.severity == ObstructionSeverity.HIGH
        assert o2.node_ids == ["n1"]

    def test_computed_style_get(self):
        cs = ComputedStyle(node_id="n1", properties={"color": CSSValue(raw="red")})
        assert cs.get("color").raw == "red"
        assert cs.get("font-size") is None


# =====================================================================
# 2. DOM Parsing (12 tests)
# =====================================================================

class TestDOMParsing:
    def test_parse_simple_html_root(self, simple_dom):
        assert simple_dom.root_id
        root = simple_dom.nodes[simple_dom.root_id]
        assert root.tag == "div"

    def test_parse_nested_parent_child(self, simple_dom):
        root = simple_dom.nodes[simple_dom.root_id]
        children = simple_dom.children_of(root.node_id)
        element_children = [c for c in children if c.node_kind == DOMNodeKind.ELEMENT]
        assert len(element_children) == 2  # p and span

    def test_parse_id_and_classes(self, simple_dom):
        root = simple_dom.nodes[simple_dom.root_id]
        assert root.id_attr == "main"
        assert "container" in root.classes

    def test_parse_attributes(self):
        dom = DOMSite.from_html('<a href="http://example.com" target="_blank">Link</a>')
        root = dom.nodes[dom.root_id]
        assert root.attributes["href"] == "http://example.com"
        assert root.attributes["target"] == "_blank"

    def test_parse_text_nodes(self, simple_dom):
        text_nodes = [n for n in simple_dom.nodes.values() if n.node_kind == DOMNodeKind.TEXT]
        texts = [n.text_content for n in text_nodes]
        assert "Hello" in texts
        assert "World" in texts

    def test_parse_void_elements(self):
        dom = DOMSite.from_html('<div><img src="a.png"><br><input type="text"></div>')
        root = dom.nodes[dom.root_id]
        children = dom.children_of(root.node_id)
        tags = [c.tag for c in children if c.node_kind == DOMNodeKind.ELEMENT]
        assert "img" in tags
        assert "br" in tags
        assert "input" in tags

    def test_children_of(self, simple_dom):
        root = simple_dom.nodes[simple_dom.root_id]
        children = simple_dom.children_of(root.node_id)
        assert len(children) > 0

    def test_ancestors_of(self, simple_dom):
        # Find the <p> node
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert len(p_nodes) == 1
        ancestors = simple_dom.ancestors_of(p_nodes[0].node_id)
        assert len(ancestors) == 1  # div parent
        assert ancestors[0].tag == "div"

    def test_depth_of(self, simple_dom):
        root = simple_dom.nodes[simple_dom.root_id]
        assert simple_dom.depth_of(root.node_id) == 0
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert simple_dom.depth_of(p_nodes[0].node_id) == 1

    def test_subtree(self, simple_dom):
        root = simple_dom.nodes[simple_dom.root_id]
        subtree = simple_dom.subtree(root.node_id)
        assert len(subtree) == len(simple_dom.nodes)

    def test_serialize_parse_roundtrip(self, simple_dom):
        data = simple_dom.serialize()
        dom2 = DOMSite.parse(data)
        assert dom2.root_id == simple_dom.root_id
        assert set(dom2.nodes) == set(simple_dom.nodes)

    def test_all_element_nodes(self, simple_dom):
        elems = simple_dom.all_element_nodes()
        for e in elems:
            assert e.node_kind == DOMNodeKind.ELEMENT


# =====================================================================
# 3. Selector Matching (20 tests)
# =====================================================================

class TestSelectorMatching:
    def test_match_by_tag(self, simple_dom):
        matched = simple_dom.nodes_matching("div")
        assert any(n.tag == "div" for n in matched)

    def test_match_by_class(self, simple_dom):
        matched = simple_dom.nodes_matching(".container")
        assert len(matched) == 1
        assert "container" in matched[0].classes

    def test_match_by_id(self, simple_dom):
        matched = simple_dom.nodes_matching("#main")
        assert len(matched) == 1
        assert matched[0].id_attr == "main"

    def test_match_by_attribute(self):
        dom = DOMSite.from_html('<a href="x">link</a>')
        matched = dom.nodes_matching("[href]")
        assert len(matched) == 1

    def test_match_compound_tag_class(self, simple_dom):
        matched = simple_dom.nodes_matching("div.container")
        assert len(matched) == 1
        assert matched[0].tag == "div"

    def test_child_combinator(self, simple_dom):
        matched = simple_dom.nodes_matching("div > p")
        assert len(matched) == 1
        assert matched[0].tag == "p"

    def test_descendant_combinator(self, nested_dom):
        matched = nested_dom.nodes_matching("div li")
        assert len(matched) == 2

    def test_adjacent_sibling(self):
        dom = DOMSite.from_html("<div><h1>T</h1><p>Text</p></div>")
        matched = dom.nodes_matching("h1 + p")
        assert len(matched) == 1
        assert matched[0].tag == "p"

    def test_general_sibling(self):
        dom = DOMSite.from_html("<div><h1>T</h1><span>S</span><p>Text</p></div>")
        matched = dom.nodes_matching("h1 ~ p")
        assert len(matched) == 1
        assert matched[0].tag == "p"

    def test_comma_separated(self, simple_dom):
        matched = simple_dom.nodes_matching("p, span")
        tags = {n.tag for n in matched}
        assert "p" in tags
        assert "span" in tags

    def test_compound_child(self):
        dom = DOMSite.from_html('<div><p class="highlight">Hi</p><p>No</p></div>')
        matched = dom.nodes_matching("div > p.highlight")
        assert len(matched) == 1
        assert "highlight" in matched[0].classes

    def test_negative_tag(self, simple_dom):
        matched = simple_dom.nodes_matching("span")
        assert all(n.tag == "span" for n in matched)
        # div should not be in the match
        assert not any(n.tag == "div" for n in matched)

    def test_negative_class(self, simple_dom):
        matched = simple_dom.nodes_matching(".nonexistent")
        assert len(matched) == 0

    def test_universal_selector(self, simple_dom):
        matched = simple_dom.nodes_matching("*")
        assert len(matched) == len(simple_dom.all_element_nodes())

    def test_id_and_class(self):
        dom = DOMSite.from_html('<div id="x" class="y">Hi</div>')
        matched = dom.nodes_matching("#x.y")
        assert len(matched) == 1

    def test_is_covering_full(self, simple_dom):
        assert simple_dom.is_covering(["*"])

    def test_is_covering_partial(self, simple_dom):
        assert not simple_dom.is_covering(["p"])

    def test_selector_overlap(self, simple_dom):
        overlap = simple_dom.selector_overlap("div", "#main")
        assert len(overlap) == 1
        assert overlap[0].tag == "div"

    def test_attribute_value_selector(self):
        dom = DOMSite.from_html('<input type="text"><input type="email">')
        matched = dom.nodes_matching('[type="text"]')
        assert len(matched) == 1

    def test_multiple_classes(self):
        dom = DOMSite.from_html('<div class="a b c">X</div>')
        matched = dom.nodes_matching(".a.b")
        assert len(matched) == 1


# =====================================================================
# 4. Specificity (12 tests)
# =====================================================================

class TestSpecificity:
    def test_universal(self):
        assert compute_specificity("*") == Specificity(0, 0, 0)

    def test_element(self):
        assert compute_specificity("div") == Specificity(0, 0, 1)

    def test_class(self):
        assert compute_specificity(".foo") == Specificity(0, 1, 0)

    def test_id(self):
        assert compute_specificity("#id") == Specificity(1, 0, 0)

    def test_element_class(self):
        assert compute_specificity("div.foo") == Specificity(0, 1, 1)

    def test_id_class(self):
        assert compute_specificity("#id .foo") == Specificity(1, 1, 0)

    def test_child_combinator(self):
        assert compute_specificity("div > p") == Specificity(0, 0, 2)

    def test_hover(self):
        assert compute_specificity(":hover") == Specificity(0, 1, 0)

    def test_pseudo_element(self):
        assert compute_specificity("div::before") == Specificity(0, 0, 2)

    def test_not(self):
        assert compute_specificity(":not(.foo)") == Specificity(0, 1, 0)

    def test_where(self):
        assert compute_specificity(":where(.foo)") == Specificity(0, 0, 0)

    def test_specificity_sort(self):
        rules = [
            CSSRule(selector="#id", specificity=Specificity(1, 0, 0), source_order=0),
            CSSRule(selector="div", specificity=Specificity(0, 0, 1), source_order=1),
            CSSRule(selector=".cls", specificity=Specificity(0, 1, 0), source_order=2),
        ]
        sorted_r = specificity_sort(rules)
        assert sorted_r[0].selector == "div"
        assert sorted_r[1].selector == ".cls"
        assert sorted_r[2].selector == "#id"


# =====================================================================
# 5. CSS Cascade (15 tests)
# =====================================================================

class TestCSSCascade:
    def test_single_rule_applies(self, simple_dom, basic_rules):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, [basic_rules[0]])  # div rule
        root = simple_dom.nodes[simple_dom.root_id]
        assert styles[root.node_id].get("color") is not None

    def test_higher_specificity_wins(self, simple_dom, basic_rules):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, basic_rules)
        root = simple_dom.nodes[simple_dom.root_id]
        # #main (specificity 1,0,0) should win for color
        assert styles[root.node_id].get("color").raw == "green"

    def test_source_order_breaks_ties(self, simple_dom):
        rules = [
            CSSRule(selector="div", properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 1), source_order=0),
            CSSRule(selector="div", properties={"color": CSSValue(raw="blue")},
                    specificity=Specificity(0, 0, 1), source_order=1),
        ]
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, rules)
        root = simple_dom.nodes[simple_dom.root_id]
        assert styles[root.node_id].get("color").raw == "blue"

    def test_inheritance_from_parent(self, simple_dom):
        rules = [
            CSSRule(selector="div", properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 1), source_order=0),
        ]
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, rules)
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert len(p_nodes) == 1
        # color should inherit
        assert styles[p_nodes[0].node_id].get("color").raw == "red"

    def test_non_inheritable_does_not_inherit(self, simple_dom):
        rules = [
            CSSRule(selector="div",
                    properties={"margin-top": CSSValue(raw="10px")},
                    specificity=Specificity(0, 0, 1), source_order=0),
        ]
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, rules)
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        # margin-top should be initial "0", not "10px"
        val = styles[p_nodes[0].node_id].get("margin-top")
        assert val.raw == "0"

    def test_initial_value_applied(self, simple_dom):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, [])
        root = simple_dom.nodes[simple_dom.root_id]
        # display should have initial value
        assert styles[root.node_id].get("display").raw == "inline"

    def test_resolve_returns_all_nodes(self, simple_dom, basic_rules):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, basic_rules)
        for nid in simple_dom.nodes:
            assert nid in styles

    def test_multiple_rules_last_highest_spec_wins(self, simple_dom, basic_rules):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, basic_rules)
        root = simple_dom.nodes[simple_dom.root_id]
        assert styles[root.node_id].get("color").raw == "green"

    def test_check_cascade_specificity_conflict(self, simple_dom):
        rules = [
            CSSRule(selector=".container",
                    properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 1, 0), source_order=0),
            CSSRule(selector=".container",
                    properties={"color": CSSValue(raw="blue")},
                    specificity=Specificity(0, 1, 0), source_order=1),
        ]
        checker = CascadeDescentChecker()
        obs = checker.check_cascade(simple_dom, rules)
        spec_obs = [o for o in obs if o.kind == CascadeObstructionKind.SPECIFICITY_CONFLICT]
        assert len(spec_obs) >= 1

    def test_check_cascade_zindex_confusion(self):
        dom = DOMSite.from_html('<div><span>A</span><span>B</span></div>')
        span_nodes = [n for n in dom.all_element_nodes() if n.tag == "span"]
        rules = [
            CSSRule(selector="span",
                    properties={
                        "position": CSSValue(raw="absolute"),
                        "z-index": CSSValue(raw="5"),
                    },
                    specificity=Specificity(0, 0, 1), source_order=0),
        ]
        checker = CascadeDescentChecker()
        obs = checker.check_cascade(dom, rules)
        z_obs = [o for o in obs if o.kind == CascadeObstructionKind.ZINDEX_CONFUSION]
        assert len(z_obs) >= 1

    def test_check_cascade_leak(self):
        dom = DOMSite.from_html('<div><p>A</p><p>B</p><span>C</span></div>')
        rules = [
            CSSRule(selector="*",
                    properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 0), source_order=0),
        ]
        checker = CascadeDescentChecker()
        obs = checker.check_cascade(dom, rules)
        leak_obs = [o for o in obs if o.kind == CascadeObstructionKind.CASCADE_LEAK]
        assert len(leak_obs) >= 1

    def test_obstruction_has_correct_kind(self):
        o = CascadeObstruction(
            kind=CascadeObstructionKind.SPECIFICITY_CONFLICT,
            message="test",
        )
        assert o.kind == CascadeObstructionKind.SPECIFICITY_CONFLICT

    def test_font_size_inherits(self, simple_dom):
        rules = [
            CSSRule(selector="div",
                    properties={"font-size": CSSValue(raw="20px")},
                    specificity=Specificity(0, 0, 1), source_order=0),
        ]
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, rules)
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert styles[p_nodes[0].node_id].get("font-size").raw == "20px"

    def test_explicit_inherit_value(self, simple_dom):
        rules = [
            CSSRule(selector="div",
                    properties={"color": CSSValue(raw="purple")},
                    specificity=Specificity(0, 0, 1), source_order=0),
            CSSRule(selector="p",
                    properties={"color": CSSValue(raw="inherit")},
                    specificity=Specificity(0, 0, 1), source_order=1),
        ]
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, rules)
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert styles[p_nodes[0].node_id].get("color").raw == "purple"


# =====================================================================
# 6. Inheritance (8 tests)
# =====================================================================

class TestInheritance:
    def test_is_inheritable_color(self):
        model = InheritanceModel()
        assert model.is_inheritable("color") is True

    def test_is_not_inheritable_margin(self):
        model = InheritanceModel()
        assert model.is_inheritable("margin") is False

    def test_resolve_inheritance_chain(self, simple_dom):
        model = InheritanceModel()
        root = simple_dom.nodes[simple_dom.root_id]
        styles = {root.node_id: ComputedStyle(
            node_id=root.node_id,
            properties={"color": CSSValue(raw="red")},
        )}
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        styles[p_nodes[0].node_id] = ComputedStyle(node_id=p_nodes[0].node_id)
        val = model.resolve_inheritance_chain(p_nodes[0].node_id, "color", styles, simple_dom)
        assert val is not None
        assert val.raw == "red"

    def test_resolve_inheritance_chain_none(self, simple_dom):
        model = InheritanceModel()
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        styles = {p_nodes[0].node_id: ComputedStyle(node_id=p_nodes[0].node_id)}
        # Add root with no color
        root = simple_dom.nodes[simple_dom.root_id]
        styles[root.node_id] = ComputedStyle(node_id=root.node_id)
        val = model.resolve_inheritance_chain(p_nodes[0].node_id, "color", styles, simple_dom)
        assert val is None

    def test_apply_inheritance(self, simple_dom):
        model = InheritanceModel()
        root = simple_dom.nodes[simple_dom.root_id]
        styles: dict[str, ComputedStyle] = {}
        for nid in simple_dom.nodes:
            styles[nid] = ComputedStyle(node_id=nid)
        styles[root.node_id].properties["color"] = CSSValue(raw="red")
        model.apply_inheritance(simple_dom, styles)
        p_nodes = [n for n in simple_dom.nodes.values() if n.tag == "p"]
        assert styles[p_nodes[0].node_id].get("color").raw == "red"

    def test_detect_inheritance_gaps(self, simple_dom):
        model = InheritanceModel()
        styles: dict[str, ComputedStyle] = {}
        for nid, node in simple_dom.nodes.items():
            styles[nid] = ComputedStyle(node_id=nid)
        gaps = model.detect_inheritance_gaps(simple_dom, styles)
        assert len(gaps) > 0  # no property set anywhere

    def test_initial_value_registry(self):
        reg = InitialValueRegistry()
        val = reg.get_initial("color")
        assert val.raw == "black"

    def test_inheritable_is_frozenset(self):
        model = InheritanceModel()
        assert isinstance(model.INHERITABLE, frozenset)


# =====================================================================
# 7. Media Queries (10 tests)
# =====================================================================

class TestMediaQueries:
    def test_parse_min_width(self):
        mq = MediaQueryParser.parse_media_query("(min-width: 768px)")
        assert mq.min_width == 768

    def test_parse_max_width(self):
        mq = MediaQueryParser.parse_media_query("(max-width: 1024px)")
        assert mq.max_width == 1024

    def test_parse_screen_and(self):
        mq = MediaQueryParser.parse_media_query("screen and (min-width: 480px)")
        assert mq.media_type == MediaType.SCREEN
        assert mq.min_width == 480

    def test_applies_at_width(self):
        mq = MediaQuery(min_width=768, max_width=1024)
        assert mq.applies_at_width(800) is True
        assert mq.applies_at_width(500) is False
        assert mq.applies_at_width(1100) is False

    def test_find_overlaps(self):
        q1 = MediaQuery(min_width=0, max_width=768)
        q2 = MediaQuery(min_width=600, max_width=1024)
        analyzer = MediaQueryOverlapAnalyzer()
        overlaps = analyzer.find_overlaps([q1, q2])
        assert len(overlaps) == 1
        _, _, rng = overlaps[0]
        assert rng == (600, 768)

    def test_find_gaps(self):
        q1 = MediaQuery(min_width=0, max_width=600)
        q2 = MediaQuery(min_width=800, max_width=1200)
        analyzer = MediaQueryOverlapAnalyzer()
        gaps = analyzer.find_gaps([q1, q2], (0, 1200))
        assert len(gaps) >= 1
        # There should be a gap between 601 and 799
        assert any(lo > 600 and hi < 800 for lo, hi in gaps)

    def test_extract_breakpoints(self):
        q1 = MediaQuery(min_width=768)
        q2 = MediaQuery(max_width=1024)
        q3 = MediaQuery(min_width=480, max_width=768)
        bp = BreakpointAnalyzer().extract_breakpoints([q1, q2, q3])
        assert bp == [480, 768, 1024]

    def test_validate_breakpoint_continuity(self):
        q1 = MediaQuery(min_width=0, max_width=600)
        q2 = MediaQuery(min_width=800, max_width=1200)
        warnings = BreakpointAnalyzer().validate_breakpoint_continuity([q1, q2])
        assert len(warnings) >= 1

    def test_find_contradictions(self):
        analyzer = MediaQueryOverlapAnalyzer()
        rules_by_query = {
            "(min-width: 0px) and (max-width: 800px)": [
                CSSRule(selector=".foo", properties={"color": CSSValue(raw="red")},
                        specificity=Specificity(0, 1, 0)),
            ],
            "(min-width: 600px) and (max-width: 1200px)": [
                CSSRule(selector=".foo", properties={"color": CSSValue(raw="blue")},
                        specificity=Specificity(0, 1, 0)),
            ],
        }
        contradictions = analyzer.find_contradictions(rules_by_query)
        assert len(contradictions) >= 1

    def test_media_query_roundtrip(self):
        mq = MediaQuery(
            condition="screen and (min-width: 768px)",
            media_type=MediaType.SCREEN,
            min_width=768,
        )
        d = mq.to_dict()
        mq2 = MediaQuery.from_dict(d)
        assert mq2.min_width == 768
        assert mq2.media_type == MediaType.SCREEN


# =====================================================================
# 8. Layout (8 tests)
# =====================================================================

class TestLayout:
    def test_box_model_total_width(self):
        box = BoxModel(
            margin_left=10, padding_left=5, content_width=100,
            padding_right=5, margin_right=10,
        )
        assert box.total_width() == 130

    def test_layout_box_bounds(self):
        box = BoxModel(content_width=100, content_height=50)
        lb = LayoutBox(node_id="n1", box=box, x=10, y=20)
        x1, y1, x2, y2 = lb.bounds()
        assert x1 == 10
        assert y1 == 20
        assert x2 == 110
        assert y2 == 70

    def test_overlap_detector_finds_overlap(self):
        b1 = LayoutBox(node_id="a", box=BoxModel(content_width=100, content_height=100),
                       x=0, y=0, is_positioned=True)
        b2 = LayoutBox(node_id="b", box=BoxModel(content_width=100, content_height=100),
                       x=50, y=50, is_positioned=True)
        det = OverlapDetector()
        overlaps = det.detect_visual_overlaps([b1, b2])
        assert ("a", "b") in overlaps

    def test_overlap_detector_finds_overflow(self):
        b = LayoutBox(node_id="a", box=BoxModel(content_width=2000, content_height=100),
                      x=0, y=0)
        det = OverlapDetector()
        overflow = det.detect_overflow([b], 1280, 800)
        assert "a" in overflow

    def test_containment_true(self):
        parent = LayoutBox(node_id="p", box=BoxModel(content_width=200, content_height=200),
                          x=0, y=0)
        child = LayoutBox(node_id="c", box=BoxModel(content_width=50, content_height=50),
                         x=10, y=10)
        checker = ContainmentChecker()
        assert checker.check_containment(parent, child) is True

    def test_containment_false(self):
        parent = LayoutBox(node_id="p", box=BoxModel(content_width=100, content_height=100),
                          x=0, y=0)
        child = LayoutBox(node_id="c", box=BoxModel(content_width=200, content_height=200),
                         x=0, y=0)
        checker = ContainmentChecker()
        assert checker.check_containment(parent, child) is False

    def test_layout_engine_returns_all_elements(self, simple_dom):
        engine = CSSCascadeEngine()
        styles = engine.resolve(simple_dom, [])
        layout_engine = LayoutEngine()
        boxes = layout_engine.compute_layout(simple_dom, styles)
        elem_ids = {n.node_id for n in simple_dom.all_element_nodes()}
        for eid in elem_ids:
            assert eid in boxes

    def test_boxes_no_overlap_separate(self):
        b1 = LayoutBox(node_id="a", box=BoxModel(content_width=50, content_height=50),
                       x=0, y=0, is_positioned=True)
        b2 = LayoutBox(node_id="b", box=BoxModel(content_width=50, content_height=50),
                       x=200, y=200, is_positioned=True)
        det = OverlapDetector()
        assert det._boxes_overlap(b1, b2) is False


# =====================================================================
# 9. Algorithms (8 tests)
# =====================================================================

class TestAlgorithms:
    def test_diff_node_added(self):
        dom1 = DOMSite.from_html("<div>Hello</div>")
        dom2 = DOMSite.from_html("<div><p>Hello</p></div>")
        engine = DOMDiffEngine()
        changes = engine.diff(dom1, dom2)
        added = [c for c in changes if c.kind == DOMChangeKind.NODE_ADDED]
        assert len(added) > 0

    def test_diff_node_removed(self):
        dom1 = DOMSite.from_html("<div><p>Hello</p></div>")
        dom2 = DOMSite.from_html("<div>Hello</div>")
        engine = DOMDiffEngine()
        changes = engine.diff(dom1, dom2)
        removed = [c for c in changes if c.kind == DOMChangeKind.NODE_REMOVED]
        assert len(removed) > 0

    def test_diff_attribute_changed(self):
        dom1 = DOMSite.from_html('<div class="a">X</div>')
        dom2_nodes = dict(dom1.nodes)
        # Modify the root node's attributes
        root = DOMNode.from_dict(dom1.nodes[dom1.root_id].to_dict())
        root.attributes = {"class": "b"}
        root.classes = ["b"]
        dom2_nodes[dom1.root_id] = root
        dom2 = DOMSite(nodes=dom2_nodes, root_id=dom1.root_id)
        engine = DOMDiffEngine()
        changes = engine.diff(dom1, dom2)
        attr_changes = [c for c in changes if c.kind in (
            DOMChangeKind.ATTRIBUTE_CHANGED, DOMChangeKind.CLASS_CHANGED)]
        assert len(attr_changes) > 0

    def test_diff_identical(self, simple_dom):
        engine = DOMDiffEngine()
        changes = engine.diff(simple_dom, simple_dom)
        assert len(changes) == 0

    def test_uncovered_nodes(self, simple_dom):
        rules = [
            CSSRule(selector="div", properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 1)),
        ]
        analyzer = SelectorCoverageAnalyzer()
        uncovered = analyzer.uncovered_nodes(simple_dom, rules)
        # p and span should be uncovered
        tags = {n.tag for n in uncovered}
        assert "p" in tags or "span" in tags

    def test_over_targeted_nodes(self):
        dom = DOMSite.from_html('<div class="a b c d e f">X</div>')
        rules = [
            CSSRule(selector=".a", properties={"x": CSSValue(raw="1")}, specificity=Specificity(0, 1, 0)),
            CSSRule(selector=".b", properties={"y": CSSValue(raw="2")}, specificity=Specificity(0, 1, 0)),
            CSSRule(selector=".c", properties={"z": CSSValue(raw="3")}, specificity=Specificity(0, 1, 0)),
            CSSRule(selector=".d", properties={"w": CSSValue(raw="4")}, specificity=Specificity(0, 1, 0)),
            CSSRule(selector=".e", properties={"v": CSSValue(raw="5")}, specificity=Specificity(0, 1, 0)),
            CSSRule(selector=".f", properties={"u": CSSValue(raw="6")}, specificity=Specificity(0, 1, 0)),
        ]
        analyzer = SelectorCoverageAnalyzer()
        over = analyzer.over_targeted_nodes(dom, rules)
        assert len(over) >= 1
        assert over[0][1] == 6

    def test_check_alt_text(self):
        dom = DOMSite.from_html('<div><img src="a.png"><img src="b.png" alt="B"></div>')
        checker = AccessibilityChecker()
        issues = checker.check_alt_text(dom)
        assert len(issues) == 1  # first img has no alt

    def test_heading_hierarchy(self):
        dom = DOMSite.from_html("<div><h1>A</h1><h3>B</h3></div>")
        checker = AccessibilityChecker()
        warnings = checker.check_heading_hierarchy(dom)
        assert len(warnings) >= 1
        assert "h2" in warnings[0]


# =====================================================================
# 10. Theorems (5 tests)
# =====================================================================

class TestTheorems:
    def test_cascade_descent_holds(self, simple_dom):
        rules = [
            CSSRule(selector="div",
                    properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 1), source_order=0),
            CSSRule(selector=".container",
                    properties={"color": CSSValue(raw="blue")},
                    specificity=Specificity(0, 1, 0), source_order=1),
        ]
        result = CascadeDescentTheorem().check(simple_dom, rules)
        assert result["holds"] is True

    def test_cascade_descent_fails(self, simple_dom):
        rules = [
            CSSRule(selector=".container",
                    properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 1, 0), source_order=0),
            CSSRule(selector=".container",
                    properties={"color": CSSValue(raw="blue")},
                    specificity=Specificity(0, 1, 0), source_order=1),
        ]
        result = CascadeDescentTheorem().check(simple_dom, rules)
        assert result["holds"] is False
        assert result["counterexample"] is not None

    def test_inheritance_completion_holds(self, simple_dom):
        engine = CSSCascadeEngine()
        # Give root all inheritable properties
        root_props = {p: CSSValue(raw="test") for p in INHERITABLE}
        rules = [
            CSSRule(selector="div",
                    properties=root_props,
                    specificity=Specificity(0, 0, 1), source_order=0),
        ]
        styles = engine.resolve(simple_dom, rules)
        result = InheritanceCompletionTheorem().check(simple_dom, styles)
        assert result["holds"] is True

    def test_selector_coverage_holds(self, simple_dom):
        rules = [
            CSSRule(selector="*", properties={"color": CSSValue(raw="red")},
                    specificity=Specificity(0, 0, 0)),
        ]
        result = SelectorCoverageTheorem().check(simple_dom, rules)
        assert result["holds"] is True

    def test_media_query_gluing_holds(self):
        queries = [
            MediaQuery(min_width=0, max_width=767),
            MediaQuery(min_width=768, max_width=1920),
        ]
        rules_by_query = {
            "(min-width: 0px) and (max-width: 767px)": [
                CSSRule(selector=".x", properties={"color": CSSValue(raw="red")},
                        specificity=Specificity(0, 1, 0)),
            ],
            "(min-width: 768px) and (max-width: 1920px)": [
                CSSRule(selector=".x", properties={"color": CSSValue(raw="blue")},
                        specificity=Specificity(0, 1, 0)),
            ],
        }
        result = MediaQueryGluingTheorem().check(queries, rules_by_query, (0, 1920))
        assert result["holds"] is True
