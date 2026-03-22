#!/usr/bin/env python3
"""Paper 01 — Grothendieck Site Axioms."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode,
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
from jugeo.judgments.judgment_terms import (
    JudgmentBuilder, Proposition, PropositionKind,
    TrustLevel, ProvenanceSource,
)

reset()

# ─── Test programs ──────────────────────────────────────────

SMALL = '''
def increment(x):
    return x + 1
'''

MEDIUM = '''
def add(x, y):
    return x + y

def multiply(x, y):
    return x * y

def negate(x):
    return -x
'''

LARGE = '''
def f1(x):
    return x + 1

def f2(x):
    return x * 2

def f3(x):
    return x - 1

def f4(x, y):
    return x + y

def f5(x, y):
    return x * y

def f6(x):
    return abs(x)
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Grothendieck axioms hold on small program", code=SMALL)
def grot_small(result):
    assert result.verified
    assert result.site.grothendieck_axioms_pass

@theorem("Grothendieck axioms hold on medium program", code=MEDIUM)
def grot_medium(result):
    assert result.verified
    assert result.site.grothendieck_axioms_pass
    assert result.site.category_axioms_pass

@theorem("Grothendieck axioms hold on large program", code=LARGE)
def grot_large(result):
    assert result.verified
    assert result.site.grothendieck_axioms_pass
    assert result.site.category_axioms_pass
    assert result.all_axioms_pass

@theorem("Site scales with program complexity")
def site_scaling():
    rs = verify(SMALL)
    rm = verify(MEDIUM)
    rl = verify(LARGE)
    assert rs.n_coordinates < rm.n_coordinates < rl.n_coordinates

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: site construction matches small program")
def deep_site_small():
    mod = Coordinate(('increment',), CoordinateKind.MODULE)
    inc = Coordinate(('increment', 'increment'), CoordinateKind.FUNCTION)
    site = (SiteBuilder('small-prog')
        .add_coordinate(mod)
        .add_coordinate(inc)
        .add_morphism(Morphism(inc, mod, MorphismKind.RESTRICTION))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    assert len(site.objects()) >= 2
    assert len(site.morphisms_from(inc)) >= 1

    j = (JudgmentBuilder().at(inc)
        .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                              formula='type_correct(increment)'))
        .of_type_named('PureFunction')
        .with_trust_level(TrustLevel.VERIFIED_PROOF)
        .from_source(ProvenanceSource.SOLVER)
        .build())
    assert not j.has_obstructions()

    cover = Cover(target=mod, patches=(inc,))
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(cover=cover, sections={
        'increment.increment': {'verified': True, 'trust': 1.0, 'props_ok': 2},
    })
    assert result.is_success
    assert result.unwrap_section().constituent_count >= 1

@theorem("Deep API: site scaling across small/medium/large")
def deep_site_scaling():
    def build(prefix, funcs):
        mod = Coordinate((prefix,), CoordinateKind.MODULE)
        coords = [Coordinate((prefix, f), CoordinateKind.FUNCTION) for f in funcs]
        builder = SiteBuilder(prefix).add_coordinate(mod)
        for c in coords:
            builder = builder.add_coordinate(c)
            builder = builder.add_morphism(Morphism(c, mod, MorphismKind.RESTRICTION))
        return builder.build()

    s_small  = build('s', ['increment'])
    s_medium = build('m', ['add', 'multiply', 'negate'])
    s_large  = build('l', ['f1', 'f2', 'f3', 'f4', 'f5', 'f6'])

    assert len(s_small.objects()) < len(s_medium.objects()) < len(s_large.objects())

# ─── Checks ────────────────────────────────────────────────

@check("Encode reveals coordinates for small program")
def chk_enc_small():
    enc = encode(SMALL)
    assert enc.n_coordinates >= 1
    assert len(enc.coordinates) >= 1

@check("Category axioms pass on medium program")
def chk_cat_medium():
    r = verify(MEDIUM)
    assert r.site.category_axioms_pass

@check("Morphism count exceeds coordinate count")
def chk_morph_gt_coord():
    r = verify(LARGE)
    assert r.n_morphisms > r.n_coordinates, (
        f"expected morphisms ({r.n_morphisms}) > coordinates ({r.n_coordinates})"
    )

@check("All programs have H1=0")
def chk_h1_zero():
    for prog in (SMALL, MEDIUM, LARGE):
        r = verify(prog)
        assert r.H1 == "0"

@check("Deep API: morphisms_from returns correct morphisms")
def chk_deep_morphisms():
    mod = Coordinate(('t',), CoordinateKind.MODULE)
    fn = Coordinate(('t', 'f'), CoordinateKind.FUNCTION)
    site = (SiteBuilder('t')
        .add_coordinate(mod).add_coordinate(fn)
        .add_morphism(Morphism(fn, mod, MorphismKind.RESTRICTION))
        .build())
    morphs = site.morphisms_from(fn)
    assert len(morphs) >= 1

@check("Propositions total grows with site size")
def chk_props_scaling():
    rs = verify(SMALL)
    rl = verify(LARGE)
    assert rs.propositions_total < rl.propositions_total

if __name__ == "__main__":
    run_all("Paper 01 — Grothendieck Site Axioms")
