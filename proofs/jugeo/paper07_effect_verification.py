#!/usr/bin/env python3
"""Paper 07 — Python Effect Verification."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode, find_bugs,
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

PURE_FN = '''
def add(x, y):
    return x + y

def compose(f_val, g_val):
    return f_val + g_val
'''

EXCEPTION_FN = '''
def safe_divide(x, y):
    try:
        return x / y
    except ZeroDivisionError:
        return 0

def safe_index(lst, i):
    try:
        return lst[i]
    except IndexError:
        return None
'''

CLASS_STATE = '''
class Accumulator:
    def __init__(self):
        self.total = 0
        self.count = 0

    def add(self, value):
        self.total += value
        self.count += 1
        return self.total

    def average(self):
        if self.count == 0:
            return 0
        return self.total / self.count
'''

IO_LIKE = '''
def format_output(name, value):
    header = "Result for " + name
    body = str(value)
    return header + ": " + body

def build_report(items):
    lines = []
    for name, val in items:
        lines.append(format_output(name, val))
    return "\\n".join(lines)
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Pure function verifies completely", code=PURE_FN)
def pure_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total

@theorem("Exception handler verifies", code=EXCEPTION_FN)
def exception_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.all_axioms_pass

@theorem("Stateful class verifies", code=CLASS_STATE)
def class_verified(result):
    assert result.verified
    assert result.H1 == "0"

@theorem("Pure function has fewer coordinates than effectful class")
def pure_fewer_coords():
    r_pure = verify(PURE_FN)
    r_class = verify(CLASS_STATE)
    assert r_pure.n_coordinates < r_class.n_coordinates

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: pure vs effectful site complexity")
def deep_effect_complexity():
    # Pure: 3 coordinates
    pure_mod = Coordinate(('pure',), CoordinateKind.MODULE)
    pure_add = Coordinate(('pure', 'add'), CoordinateKind.FUNCTION)
    pure_comp = Coordinate(('pure', 'compose'), CoordinateKind.FUNCTION)
    pure_site = (SiteBuilder('pure')
        .add_coordinate(pure_mod).add_coordinate(pure_add).add_coordinate(pure_comp)
        .add_morphism(Morphism(pure_add, pure_mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(pure_comp, pure_mod, MorphismKind.RESTRICTION))
        .build())

    # Effectful: 5 coordinates (class with methods)
    eff_mod = Coordinate(('eff',), CoordinateKind.MODULE)
    eff_init = Coordinate(('eff', '__init__'), CoordinateKind.FUNCTION)
    eff_add = Coordinate(('eff', 'add'), CoordinateKind.FUNCTION)
    eff_avg = Coordinate(('eff', 'average'), CoordinateKind.FUNCTION)
    eff_state = Coordinate(('eff', 'state'), CoordinateKind.REGION)
    eff_site = (SiteBuilder('effectful')
        .add_coordinate(eff_mod).add_coordinate(eff_init)
        .add_coordinate(eff_add).add_coordinate(eff_avg).add_coordinate(eff_state)
        .add_morphism(Morphism(eff_init, eff_mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(eff_add, eff_mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(eff_avg, eff_mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(eff_add, eff_init, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(eff_state, eff_init, MorphismKind.INCLUSION))
        .build())

    assert len(eff_site.objects()) > len(pure_site.objects()), \
        "Effectful code should have more coordinates"

@theorem("Deep API: exception handler modeled with REGION forks")
def deep_exception_regions():
    mod = Coordinate(('exc',), CoordinateKind.MODULE)
    fn  = Coordinate(('exc', 'safe_divide'), CoordinateKind.FUNCTION)
    ok_path  = Coordinate(('exc', 'safe_divide', 'ok'), CoordinateKind.REGION)
    err_path = Coordinate(('exc', 'safe_divide', 'err'), CoordinateKind.REGION)

    site = (SiteBuilder('exception-fork')
        .add_coordinate(mod).add_coordinate(fn)
        .add_coordinate(ok_path).add_coordinate(err_path)
        .add_morphism(Morphism(fn, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(ok_path, fn, MorphismKind.INCLUSION))
        .add_morphism(Morphism(err_path, fn, MorphismKind.INCLUSION))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    assert len(site.objects()) == 4
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fn, ok_path, err_path)),
        sections={
            'exc.safe_divide':     {'verified': True, 'trust': 1.0, 'props_ok': 2},
            'exc.safe_divide.ok':  {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'exc.safe_divide.err': {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success

# ─── Checks ────────────────────────────────────────────────

@check("All programs verify")
def chk_all_verify():
    for code in (PURE_FN, EXCEPTION_FN, CLASS_STATE, IO_LIKE):
        r = verify(code)
        assert r.verified

@check("Bugs finds nothing in clean pure code")
def chk_bugs_pure():
    b = find_bugs(PURE_FN)
    assert b.count == 0

@check("Encode shows coordinates per effect region")
def chk_encode_regions():
    enc_pure = encode(PURE_FN)
    enc_class = encode(CLASS_STATE)
    assert enc_class.n_coordinates > enc_pure.n_coordinates

@check("IO-like program has multiple coordinates")
def chk_io_coords():
    enc = encode(IO_LIKE)
    assert enc.n_coordinates >= 2

@check("Propositions scale with program complexity")
def chk_props_scale():
    r_pure = verify(PURE_FN)
    r_class = verify(CLASS_STATE)
    assert r_class.propositions_total > r_pure.propositions_total

@check("All programs produce certificate hashes")
def chk_certs():
    for code in (PURE_FN, EXCEPTION_FN, CLASS_STATE, IO_LIKE):
        r = verify(code)
        assert r.certificate_hash

if __name__ == "__main__":
    run_all("Paper 07 — Python Effect Verification")
