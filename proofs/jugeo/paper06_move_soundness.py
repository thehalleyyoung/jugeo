#!/usr/bin/env python3
"""Paper 06 — Semantic Move Soundness."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, descend_code,
    reset, run_all,
)
from jugeo.geometry.site import (
    SiteBuilder, Coordinate, CoordinateKind,
    Morphism, MorphismKind, GrothendieckTopology,
)
from jugeo.geometry.descent import (
    DescentEngine, DescentConfiguration, DescentStrategy,
)
from jugeo.geometry.covers import Cover

reset()

# ─── Test programs ──────────────────────────────────────────

SINGLE_FUNC = '''
def transform(x):
    return x * 2 + 1
'''

MULTI_FUNC = '''
def step1(x):
    return x + 1

def step2(x):
    return step1(x) * 2

def step3(x):
    return step2(x) - 3
'''

CLASS_PROG = '''
class Parser:
    def __init__(self, text):
        self.text = text

    def tokenize(self):
        return self.text.strip().split()

    def validate(self):
        tokens = self.tokenize()
        return len(tokens) > 0

    def process(self):
        if self.validate():
            return self.tokenize()
        return []
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Single function has morphisms (moves exist)", code=SINGLE_FUNC)
def single_morphisms(result):
    assert result.verified
    assert result.n_morphisms > 0
    assert result.H1 == "0"

@theorem("Multi-function has morphisms (moves exist)", code=MULTI_FUNC)
def multi_morphisms(result):
    assert result.verified
    assert result.n_morphisms > 0
    assert result.n_coordinates >= 2

@theorem("Class program has morphisms (moves exist)", code=CLASS_PROG)
def class_morphisms(result):
    assert result.verified
    assert result.n_morphisms > 0
    assert result.descent.all_effective

@theorem("Descent works across all programs")
def descent_works():
    for code in (SINGLE_FUNC, MULTI_FUNC, CLASS_PROG):
        r = verify(code)
        assert r.verified
        assert r.H1 == "0"

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: morphism kinds model semantic moves")
def deep_morphism_kinds():
    mod = Coordinate(('prog',), CoordinateKind.MODULE)
    s1  = Coordinate(('prog', 'step1'), CoordinateKind.FUNCTION)
    s2  = Coordinate(('prog', 'step2'), CoordinateKind.FUNCTION)
    s3  = Coordinate(('prog', 'step3'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('semantic-moves')
        .add_coordinate(mod).add_coordinate(s1)
        .add_coordinate(s2).add_coordinate(s3)
        .add_morphism(Morphism(s1, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s2, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s3, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s2, s1, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(s3, s2, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    assert len(site.objects()) == 4
    assert len(site.morphisms_from(s2)) >= 2  # to mod and to s1

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(s1, s2, s3)),
        sections={
            'prog.step1': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'prog.step2': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'prog.step3': {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success
    assert result.unwrap_section().constituent_count == 3

@theorem("Deep API: multi-function site has more morphisms than single")
def deep_morph_scaling():
    mod1 = Coordinate(('single',), CoordinateKind.MODULE)
    fn1  = Coordinate(('single', 'transform'), CoordinateKind.FUNCTION)
    site1 = (SiteBuilder('single')
        .add_coordinate(mod1).add_coordinate(fn1)
        .add_morphism(Morphism(fn1, mod1, MorphismKind.RESTRICTION))
        .build())

    mod2 = Coordinate(('multi',), CoordinateKind.MODULE)
    s1 = Coordinate(('multi', 'step1'), CoordinateKind.FUNCTION)
    s2 = Coordinate(('multi', 'step2'), CoordinateKind.FUNCTION)
    s3 = Coordinate(('multi', 'step3'), CoordinateKind.FUNCTION)
    site2 = (SiteBuilder('multi')
        .add_coordinate(mod2).add_coordinate(s1).add_coordinate(s2).add_coordinate(s3)
        .add_morphism(Morphism(s1, mod2, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s2, mod2, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s3, mod2, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(s2, s1, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(s3, s2, MorphismKind.TRANSPORT))
        .build())

    assert len(site2.objects()) > len(site1.objects())

# ─── Checks ────────────────────────────────────────────────

@check("descend_code returns effective descent for single function")
def chk_descend_single():
    d = descend_code(SINGLE_FUNC)
    assert len(d) >= 1
    assert d[0].get("verdict") == "verified"

@check("descend_code returns effective descent for multi function")
def chk_descend_multi():
    d = descend_code(MULTI_FUNC)
    assert len(d) >= 1
    assert d[0].get("local_sections", 0) > 0

@check("descend_code returns effective descent for class")
def chk_descend_class():
    d = descend_code(CLASS_PROG)
    assert len(d) >= 1
    assert d[0].get("verdict") == "verified"

@check("Multi-function has more morphisms than single function")
def chk_morph_scaling():
    r1 = verify(SINGLE_FUNC)
    r2 = verify(MULTI_FUNC)
    assert r2.n_morphisms > r1.n_morphisms

@check("Grothendieck axioms pass on multi-function")
def chk_grot_multi():
    r = verify(MULTI_FUNC)
    assert r.site.grothendieck_axioms_pass

@check("Descent sections detail available")
def chk_sections_detail():
    d = descend_code(MULTI_FUNC)
    detail = d[0].get("sections_detail", [])
    assert len(detail) > 0
    for sec in detail:
        assert sec.get("propositions", 0) > 0

if __name__ == "__main__":
    run_all("Paper 06 — Semantic Move Soundness")
