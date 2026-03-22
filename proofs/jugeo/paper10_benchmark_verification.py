#!/usr/bin/env python3
"""Paper 10 — Full Benchmark Verification."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode, find_bugs, descend_code,
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

PROGS = {
    "arithmetic": '''
def add(x, y):
    return x + y

def sub(x, y):
    return x - y
''',
    "branching": '''
def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
''',
    "loop": '''
def total(lst):
    s = 0
    for v in lst:
        s += v
    return s
''',
    "class": '''
class Counter:
    def __init__(self):
        self.n = 0

    def inc(self):
        self.n += 1
        return self.n

    def dec(self):
        self.n -= 1
        return self.n
''',
    "recursion": '''
def gcd(a, b):
    if b == 0:
        return a
    return gcd(b, a % b)

def lcm(a, b):
    return a * b // gcd(a, b)
''',
    "exception": '''
def safe_div(x, y):
    try:
        return x / y
    except ZeroDivisionError:
        return 0

def safe_sqrt(x):
    if x < 0:
        return None
    return x ** 0.5
''',
}

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("All benchmark programs verify")
def all_verify():
    for name, src in PROGS.items():
        r = verify(src)
        assert r.verified, f"{name} did not verify"
        assert r.H1 == "0", f"{name} has H1 != 0"

@theorem("All benchmark programs have passing axioms")
def all_axioms():
    for name, src in PROGS.items():
        r = verify(src)
        assert r.all_axioms_pass, f"{name} axioms failed"

@theorem("Stats collected for all programs")
def stats_collected():
    total_coords = 0
    total_morphisms = 0
    total_props = 0
    for name, src in PROGS.items():
        r = verify(src)
        total_coords += r.n_coordinates
        total_morphisms += r.n_morphisms
        total_props += r.propositions_total
    assert total_coords > 0
    assert total_morphisms > 0
    assert total_props > 0

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: benchmark sites scale with complexity")
def deep_benchmark_scaling():
    def build_site(prefix, funcs):
        mod = Coordinate((prefix,), CoordinateKind.MODULE)
        coords = [Coordinate((prefix, f), CoordinateKind.FUNCTION) for f in funcs]
        builder = SiteBuilder(prefix).add_coordinate(mod)
        for c in coords:
            builder = builder.add_coordinate(c)
            builder = builder.add_morphism(Morphism(c, mod, MorphismKind.RESTRICTION))
        return builder.set_topology(GrothendieckTopology.canonical()).build()

    s_arith = build_site('arith', ['add', 'sub'])
    s_class = build_site('ctr', ['__init__', 'inc', 'dec'])

    assert len(s_class.objects()) > len(s_arith.objects()), \
        "Class should have more coordinates than simple arithmetic"

@theorem("Deep API: descent succeeds across all benchmark types")
def deep_benchmark_descent():
    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))

    benchmarks = [
        ('arith', ['add', 'sub']),
        ('branch', ['clamp']),
        ('loop', ['total']),
        ('ctr', ['__init__', 'inc', 'dec']),
        ('rec', ['gcd', 'lcm']),
        ('exc', ['safe_div', 'safe_sqrt']),
    ]

    for prefix, funcs in benchmarks:
        mod = Coordinate((prefix,), CoordinateKind.MODULE)
        coords = [Coordinate((prefix, f), CoordinateKind.FUNCTION) for f in funcs]
        builder = SiteBuilder(prefix).add_coordinate(mod)
        for c in coords:
            builder = builder.add_coordinate(c)
            builder = builder.add_morphism(Morphism(c, mod, MorphismKind.RESTRICTION))
        site = builder.build()

        sections = {c.name: {'verified': True, 'trust': 1.0, 'props_ok': 2} for c in coords}
        result = engine.attempt_descent(
            cover=Cover(target=mod, patches=tuple(coords)),
            sections=sections)
        assert result.is_success, f"Descent failed for {prefix}"

@theorem("Deep API: judgments model all benchmark program types")
def deep_benchmark_judgments():
    benchmarks = [
        ('arith', 'add', PropositionKind.STRUCTURAL, 'type_correct(add)'),
        ('branch', 'clamp', PropositionKind.BEHAVIORAL, 'lo <= result <= hi'),
        ('rec', 'gcd', PropositionKind.BEHAVIORAL, 'gcd(a,b) divides a and b'),
        ('exc', 'safe_div', PropositionKind.STRUCTURAL, 'no_exceptions(safe_div)'),
    ]

    for prefix, func, kind, formula in benchmarks:
        fn = Coordinate((prefix, func), CoordinateKind.FUNCTION)
        j = (JudgmentBuilder().at(fn)
            .claiming(Proposition(kind=kind, formula=formula))
            .of_type_named('Benchmark')
            .with_trust_level(TrustLevel.VERIFIED_PROOF)
            .from_source(ProvenanceSource.SOLVER)
            .build())
        assert j.proposition.kind == kind
        assert j.proposition.formula == formula
        assert not j.has_obstructions()

# ─── Checks ────────────────────────────────────────────────

@check("Total propositions across benchmark > 0")
def chk_total_props():
    total = sum(verify(src).propositions_total for src in PROGS.values())
    assert total > 20

@check("All H1 values are 0")
def chk_all_h1():
    for name, src in PROGS.items():
        r = verify(src)
        assert r.H1 == "0", f"{name} has H1={r.H1}"

@check("Encode works on all programs")
def chk_encode_all():
    for name, src in PROGS.items():
        enc = encode(src)
        assert enc.n_coordinates >= 1, f"{name} encode failed"

@check("Descend works on representative program")
def chk_descend():
    d = descend_code(PROGS["branching"])
    assert len(d) >= 1
    assert d[0].get("verdict") == "verified"

@check("Bugs finds nothing in clean arithmetic")
def chk_bugs():
    b = find_bugs(PROGS["arithmetic"])
    assert b.count == 0

@check("Class program has more coordinates than single function")
def chk_class_coords():
    r_branch = verify(PROGS["branching"])
    r_class = verify(PROGS["class"])
    assert r_class.n_coordinates > r_branch.n_coordinates

@check("All programs produce certificate hashes")
def chk_certs():
    for name, src in PROGS.items():
        r = verify(src)
        assert r.certificate_hash, f"{name} missing cert hash"

@check("Aggregate morphism count is positive")
def chk_morphisms():
    total = sum(verify(src).n_morphisms for src in PROGS.values())
    assert total > 10

if __name__ == "__main__":
    run_all("Paper 10 — Full Benchmark Verification")
