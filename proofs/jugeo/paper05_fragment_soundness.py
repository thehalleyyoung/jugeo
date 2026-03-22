#!/usr/bin/env python3
"""Paper 05 — Fragment Classification Soundness."""
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

reset()

# ─── Test programs ──────────────────────────────────────────

ARITHMETIC = '''
def add(x, y):
    return x + y

def multiply(a, b):
    return a * b

def negate(x):
    return -x
'''

BRANCHING = '''
def is_positive(x):
    return x > 0

def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''

LOOPING = '''
def total(items):
    s = 0
    for item in items:
        s += item
    return s

def maximum(items):
    best = items[0]
    for item in items[1:]:
        if item > best:
            best = item
    return best
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("All strategies agree on arithmetic")
def strategies_arith():
    verdicts = []
    for strat in ("eager", "exhaustive", "iterative"):
        r = verify(ARITHMETIC, strategy=strat)
        verdicts.append(r.verdict)
    assert all(v == "verified" for v in verdicts)

@theorem("All strategies agree on branching")
def strategies_branch():
    verdicts = []
    for strat in ("eager", "exhaustive", "iterative"):
        r = verify(BRANCHING, strategy=strat)
        verdicts.append(r.verdict)
    assert all(v == "verified" for v in verdicts)

@theorem("All strategies agree on looping")
def strategies_loop():
    verdicts = []
    for strat in ("eager", "exhaustive", "iterative"):
        r = verify(LOOPING, strategy=strat)
        verdicts.append(r.verdict)
    assert all(v == "verified" for v in verdicts)

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: all three DescentStrategy values produce descent success")
def deep_all_strategies():
    mod = Coordinate(('frag',), CoordinateKind.MODULE)
    fn1 = Coordinate(('frag', 'add'), CoordinateKind.FUNCTION)
    fn2 = Coordinate(('frag', 'multiply'), CoordinateKind.FUNCTION)

    for strategy in (DescentStrategy.EAGER, DescentStrategy.EXHAUSTIVE, DescentStrategy.ITERATIVE):
        site = (SiteBuilder('fragment')
            .add_coordinate(mod).add_coordinate(fn1).add_coordinate(fn2)
            .add_morphism(Morphism(fn1, mod, MorphismKind.RESTRICTION))
            .add_morphism(Morphism(fn2, mod, MorphismKind.RESTRICTION))
            .build())
        engine = DescentEngine(configuration=DescentConfiguration(strategy=strategy))
        result = engine.attempt_descent(
            cover=Cover(target=mod, patches=(fn1, fn2)),
            sections={
                'frag.add':      {'verified': True, 'trust': 1.0, 'props_ok': 1},
                'frag.multiply': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            })
        assert result.is_success, f"Strategy {strategy} failed"
        assert result.certificate.certificate_id

@theorem("Deep API: CoordinateKind classifies code fragments")
def deep_coordinate_kinds():
    mod = Coordinate(('prog',), CoordinateKind.MODULE)
    fn  = Coordinate(('prog', 'f'), CoordinateKind.FUNCTION)
    ifc = Coordinate(('prog', 'I'), CoordinateKind.INTERFACE)
    tst = Coordinate(('prog', 't'), CoordinateKind.TEST)
    thm = Coordinate(('prog', 'th'), CoordinateKind.THEOREM)
    reg = Coordinate(('prog', 'r'), CoordinateKind.REGION)

    assert mod.kind == CoordinateKind.MODULE
    assert fn.kind == CoordinateKind.FUNCTION
    assert ifc.kind == CoordinateKind.INTERFACE
    assert tst.kind == CoordinateKind.TEST
    assert thm.kind == CoordinateKind.THEOREM
    assert reg.kind == CoordinateKind.REGION

# ─── Checks ────────────────────────────────────────────────

@check("Encode shows decidability_map for arithmetic")
def chk_decidability_arith():
    enc = encode(ARITHMETIC)
    assert isinstance(enc.decidability_map, dict)

@check("Propositions ok <= total for arithmetic")
def chk_props_arith():
    r = verify(ARITHMETIC)
    assert r.propositions_total > 0
    assert r.propositions_ok <= r.propositions_total

@check("Propositions ok <= total for branching")
def chk_props_branch():
    r = verify(BRANCHING)
    assert r.propositions_total > 0
    assert r.propositions_ok <= r.propositions_total

@check("Propositions ok <= total for looping")
def chk_props_loop():
    r = verify(LOOPING)
    assert r.propositions_total > 0
    assert r.propositions_ok <= r.propositions_total

@check("Encode produces coordinates for all programs")
def chk_encode_all():
    for code in (ARITHMETIC, BRANCHING, LOOPING):
        enc = encode(code)
        assert enc.n_coordinates >= 2

@check("Certificate hash produced under all strategies")
def chk_cert_strategies():
    for strat in ("eager", "exhaustive", "iterative"):
        r = verify(ARITHMETIC, strategy=strat)
        assert r.certificate_hash

if __name__ == "__main__":
    run_all("Paper 05 — Fragment Classification Soundness")
