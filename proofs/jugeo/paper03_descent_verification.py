#!/usr/bin/env python3
"""Paper 03 — Descent & Obstructions."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, find_bugs, descend_code,
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

CORRECT = '''
def add(x, y):
    return x + y

def double(x):
    return add(x, x)
'''

MULTI_FUNC = '''
def process(data):
    result = []
    for item in data:
        if item > 0:
            result.append(item * 2)
        else:
            result.append(0)
    return result

def summarize(data):
    processed = process(data)
    return sum(processed)
'''

CLEAN_CLASS = '''
class Accumulator:
    def __init__(self):
        self.total = 0

    def add(self, value):
        self.total += value
        return self.total

    def reset(self):
        self.total = 0
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("H1=0 for correct program", code=CORRECT)
def h1_correct(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.descent.all_effective

@theorem("H1=0 for multi-function program", code=MULTI_FUNC)
def h1_multi(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.n_coordinates >= 2

@theorem("Descent verified for class program", code=CLEAN_CLASS)
def descent_class(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.site.grothendieck_axioms_pass

@theorem("All programs achieve effective descent")
def effective_descent_all():
    for code in (CORRECT, MULTI_FUNC, CLEAN_CLASS):
        r = verify(code)
        assert r.descent.all_effective, f"descent not effective"
        assert r.verified

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: descent with multiple patches succeeds")
def deep_descent_multi():
    mod = Coordinate(('prog',), CoordinateKind.MODULE)
    proc = Coordinate(('prog', 'process'), CoordinateKind.FUNCTION)
    summ = Coordinate(('prog', 'summarize'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('multi-func')
        .add_coordinate(mod).add_coordinate(proc).add_coordinate(summ)
        .add_morphism(Morphism(proc, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(summ, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(summ, proc, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(proc, summ)),
        sections={
            'prog.process':   {'verified': True, 'trust': 1.0, 'props_ok': 3},
            'prog.summarize': {'verified': True, 'trust': 1.0, 'props_ok': 2},
        })
    assert result.is_success
    gs = result.unwrap_section()
    assert gs.constituent_count == 2
    assert result.certificate.certificate_id

@theorem("Deep API: class site with temporal morphisms achieves descent")
def deep_descent_class():
    mod  = Coordinate(('acc',), CoordinateKind.MODULE)
    init = Coordinate(('acc', '__init__'), CoordinateKind.FUNCTION)
    add  = Coordinate(('acc', 'add'), CoordinateKind.FUNCTION)
    rst  = Coordinate(('acc', 'reset'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('accumulator')
        .add_coordinate(mod).add_coordinate(init)
        .add_coordinate(add).add_coordinate(rst)
        .add_morphism(Morphism(init, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(add, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(rst, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(add, init, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(rst, init, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(init, add, rst)),
        sections={
            'acc.__init__': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'acc.add':      {'verified': True, 'trust': 1.0, 'props_ok': 2},
            'acc.reset':    {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success

# ─── Checks ────────────────────────────────────────────────

@check("descend_code returns descent data for correct program")
def chk_descend_correct():
    d = descend_code(CORRECT)
    assert len(d) >= 1
    assert d[0].get("verdict") == "verified"

@check("descend_code shows local sections")
def chk_descend_sections():
    d = descend_code(MULTI_FUNC)
    assert len(d) >= 1
    assert d[0].get("local_sections", 0) > 0

@check("Obstruction field shows H1=0 on correct code")
def chk_obstruction_field():
    r = verify(CORRECT)
    assert r.H1 == "0"
    assert len(r.obstructions) == 0

@check("Bugs finds nothing in clean correct code")
def chk_bugs_clean():
    b = find_bugs(CORRECT)
    assert b.count == 0

@check("Deep API: descent summary is descriptive")
def chk_deep_summary():
    mod = Coordinate(('t',), CoordinateKind.MODULE)
    fn = Coordinate(('t', 'f'), CoordinateKind.FUNCTION)
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fn,)),
        sections={'t.f': {'verified': True, 'trust': 1.0, 'props_ok': 1}})
    summary = result.summary()
    assert 'success' in summary.lower() or 'GlobalSection' in summary

@check("Global section produced from descent")
def chk_global_section():
    d = descend_code(CORRECT)
    gs = d[0].get("global_section", {})
    assert gs.get("sections", 0) > 0

if __name__ == "__main__":
    run_all("Paper 03 — Descent & Obstructions")
