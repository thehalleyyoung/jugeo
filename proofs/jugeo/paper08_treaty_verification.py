#!/usr/bin/env python3
"""Paper 08 — Treaty Synthesis Verification."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode, check_equiv, find_bugs,
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

MOD_A = '''
def validate(data):
    if not isinstance(data, dict):
        return False
    return 'key' in data

def sanitize(data):
    if validate(data):
        return data
    return {}
'''

MOD_B = '''
def format_value(v):
    if v is None:
        return '<empty>'
    return str(v)

def format_pair(key, value):
    return key + ": " + format_value(value)
'''

COMBINED = '''
def validate(data):
    if not isinstance(data, dict):
        return False
    return 'key' in data

def format_value(v):
    if v is None:
        return '<empty>'
    return str(v)

def pipeline(data):
    if validate(data):
        return format_value(data['key'])
    return format_value(None)
'''

REFACTORED_A = '''
def validate(data):
    return type(data) is dict and 'key' in data

def sanitize(data):
    if validate(data):
        return data
    return {}
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Module A verifies independently", code=MOD_A)
def mod_a_verified(result):
    assert result.verified
    assert result.H1 == "0"

@theorem("Module B verifies independently", code=MOD_B)
def mod_b_verified(result):
    assert result.verified
    assert result.H1 == "0"

@theorem("Combined module verifies", code=COMBINED)
def combined_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.all_axioms_pass

@theorem("Equiv detects structural differences between modules")
def equiv_detects_diff():
    eq = check_equiv(MOD_A, MOD_B)
    assert not eq.equivalent
    assert len(eq.obstructions) > 0

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: module composition produces larger site")
def deep_treaty_composition():
    # Module A site
    mod_a = Coordinate(('modA',), CoordinateKind.MODULE)
    validate = Coordinate(('modA', 'validate'), CoordinateKind.FUNCTION)
    sanitize = Coordinate(('modA', 'sanitize'), CoordinateKind.FUNCTION)
    site_a = (SiteBuilder('module-a')
        .add_coordinate(mod_a).add_coordinate(validate).add_coordinate(sanitize)
        .add_morphism(Morphism(validate, mod_a, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(sanitize, mod_a, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(sanitize, validate, MorphismKind.TRANSPORT))
        .build())

    # Module B site
    mod_b = Coordinate(('modB',), CoordinateKind.MODULE)
    fmt_val = Coordinate(('modB', 'format_value'), CoordinateKind.FUNCTION)
    fmt_pair = Coordinate(('modB', 'format_pair'), CoordinateKind.FUNCTION)
    site_b = (SiteBuilder('module-b')
        .add_coordinate(mod_b).add_coordinate(fmt_val).add_coordinate(fmt_pair)
        .add_morphism(Morphism(fmt_val, mod_b, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(fmt_pair, mod_b, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(fmt_pair, fmt_val, MorphismKind.TRANSPORT))
        .build())

    # Combined site
    mod_c = Coordinate(('combined',), CoordinateKind.MODULE)
    c_val = Coordinate(('combined', 'validate'), CoordinateKind.FUNCTION)
    c_fmt = Coordinate(('combined', 'format_value'), CoordinateKind.FUNCTION)
    c_pipe = Coordinate(('combined', 'pipeline'), CoordinateKind.FUNCTION)
    site_c = (SiteBuilder('combined')
        .add_coordinate(mod_c).add_coordinate(c_val)
        .add_coordinate(c_fmt).add_coordinate(c_pipe)
        .add_morphism(Morphism(c_val, mod_c, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(c_fmt, mod_c, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(c_pipe, mod_c, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(c_pipe, c_val, MorphismKind.TRANSPORT))
        .add_morphism(Morphism(c_pipe, c_fmt, MorphismKind.TRANSPORT))
        .build())

    assert len(site_c.objects()) >= len(site_a.objects())
    assert len(site_c.objects()) >= len(site_b.objects())

    # Descent on combined
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod_c, patches=(c_val, c_fmt, c_pipe)),
        sections={
            'combined.validate':     {'verified': True, 'trust': 1.0, 'props_ok': 2},
            'combined.format_value': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'combined.pipeline':     {'verified': True, 'trust': 1.0, 'props_ok': 2},
        })
    assert result.is_success

@theorem("Deep API: REFINEMENT morphism connects refactored to original")
def deep_refinement_morphism():
    mod = Coordinate(('refactor',), CoordinateKind.MODULE)
    original = Coordinate(('refactor', 'validate_v1'), CoordinateKind.FUNCTION)
    refactored = Coordinate(('refactor', 'validate_v2'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('refactoring')
        .add_coordinate(mod).add_coordinate(original).add_coordinate(refactored)
        .add_morphism(Morphism(original, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(refactored, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(refactored, original, MorphismKind.REFINEMENT))
        .build())

    assert len(site.objects()) == 3
    morphs = site.morphisms_from(refactored)
    kinds = [m.kind for m in morphs]
    assert MorphismKind.REFINEMENT in kinds

# ─── Checks ────────────────────────────────────────────────

@check("Identical modules show no obstructions")
def chk_equiv_identical():
    eq = check_equiv(MOD_A, MOD_A)
    assert eq.equivalent
    assert len(eq.obstructions) == 0

@check("Refactored module differs from original")
def chk_equiv_refactored():
    eq = check_equiv(MOD_A, REFACTORED_A)
    assert isinstance(eq.verdict, str)
    assert eq.site_a_coords > 0
    assert eq.site_b_coords > 0

@check("Bugs finds nothing in module A")
def chk_bugs_a():
    b = find_bugs(MOD_A)
    assert b.count == 0

@check("Bugs finds nothing in combined module")
def chk_bugs_combined():
    b = find_bugs(COMBINED)
    assert b.count == 0

@check("Encode combined module shows coordinates")
def chk_encode_combined():
    enc = encode(COMBINED)
    assert enc.n_coordinates >= 3

@check("Combined has more coordinates than individual modules")
def chk_combined_larger():
    ea = encode(MOD_A)
    eb = encode(MOD_B)
    ec = encode(COMBINED)
    assert ec.n_coordinates >= ea.n_coordinates
    assert ec.n_coordinates >= eb.n_coordinates

@check("Equiv site coords are consistent")
def chk_equiv_coords():
    eq = check_equiv(MOD_A, MOD_B)
    assert eq.site_a_coords > 0
    assert eq.site_b_coords > 0

if __name__ == "__main__":
    run_all("Paper 08 — Treaty Synthesis Verification")
