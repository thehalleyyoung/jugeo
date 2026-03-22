#!/usr/bin/env python3
"""Paper 00 — End-to-End Pipeline Soundness."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode, find_bugs, carry_proof,
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

ARITHMETIC = '''
def add(x, y):
    return x + y

def multiply(a, b):
    return a * b

def negate(x):
    return -x
'''

STRING_OPS = '''
def greet(name):
    return "Hello, " + name

def shout(msg):
    return msg.upper() + "!"

def length(s):
    return len(s)
'''

EXCEPTION = '''
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

STATEFUL = '''
class Counter:
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1
        return self.count

    def reset(self):
        self.count = 0
'''

RECURSIVE = '''
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Arithmetic verifies end-to-end", code=ARITHMETIC)
def arith_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.site.grothendieck_axioms_pass
    assert result.all_axioms_pass

@theorem("String ops verify end-to-end", code=STRING_OPS)
def strings_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total

@theorem("Exception handler verifies end-to-end", code=EXCEPTION)
def exception_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.n_coordinates >= 2

@theorem("Stateful class verifies end-to-end", code=STATEFUL)
def stateful_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.site.category_axioms_pass

@theorem("Recursive code verifies end-to-end", code=RECURSIVE)
def recursive_verified(result):
    assert result.verified
    assert result.H1 == "0"
    assert result.descent.all_effective

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: arithmetic site construction and descent")
def deep_arithmetic():
    mod = Coordinate(('arith',), CoordinateKind.MODULE)
    add = Coordinate(('arith', 'add'), CoordinateKind.FUNCTION)
    mul = Coordinate(('arith', 'multiply'), CoordinateKind.FUNCTION)
    neg = Coordinate(('arith', 'negate'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('arithmetic')
        .add_coordinate(mod).add_coordinate(add)
        .add_coordinate(mul).add_coordinate(neg)
        .add_morphism(Morphism(add, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(mul, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(neg, mod, MorphismKind.RESTRICTION))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    assert len(site.objects()) == 4

    for coord in (add, mul, neg):
        j = (JudgmentBuilder().at(coord)
            .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                                  formula=f'type_correct({coord.name})'))
            .of_type_named('PureFunction')
            .with_trust_level(TrustLevel.VERIFIED_PROOF)
            .from_source(ProvenanceSource.SOLVER)
            .build())
        assert not j.has_obstructions()

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(add, mul, neg)),
        sections={
            'arith.add':      {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'arith.multiply': {'verified': True, 'trust': 1.0, 'props_ok': 1},
            'arith.negate':   {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success
    assert result.unwrap_section().constituent_count == 3

@theorem("Deep API: recursive site with transport morphisms")
def deep_recursive():
    mod = Coordinate(('rec',), CoordinateKind.MODULE)
    fact = Coordinate(('rec', 'factorial'), CoordinateKind.FUNCTION)
    fib = Coordinate(('rec', 'fibonacci'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('recursive')
        .add_coordinate(mod).add_coordinate(fact).add_coordinate(fib)
        .add_morphism(Morphism(fact, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(fib, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(fact, fib, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    assert len(site.objects()) == 3
    assert len(site.morphisms_from(fact)) >= 2

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fact, fib)),
        sections={
            'rec.factorial': {'verified': True, 'trust': 1.0, 'props_ok': 2},
            'rec.fibonacci': {'verified': True, 'trust': 1.0, 'props_ok': 2},
        })
    assert result.is_success

# ─── Checks ────────────────────────────────────────────────

@check("Proof-carrying code produces valid certificate")
def chk_carry():
    source, cert = carry_proof(ARITHMETIC)
    assert cert.verdict == "verified"
    assert cert.reverify(source)

@check("All strategies agree on arithmetic")
def chk_strategies():
    for strat in ("eager", "exhaustive", "iterative"):
        r = verify(ARITHMETIC, strategy=strat)
        assert r.verified, f"strategy {strat} failed"

@check("Deep API: all DescentStrategy values produce success")
def chk_deep_strategies():
    mod = Coordinate(('s',), CoordinateKind.MODULE)
    fn = Coordinate(('s', 'f'), CoordinateKind.FUNCTION)
    for strategy in (DescentStrategy.EAGER, DescentStrategy.EXHAUSTIVE, DescentStrategy.ITERATIVE):
        engine = DescentEngine(configuration=DescentConfiguration(strategy=strategy))
        result = engine.attempt_descent(
            cover=Cover(target=mod, patches=(fn,)),
            sections={'s.f': {'verified': True, 'trust': 1.0, 'props_ok': 1}})
        assert result.is_success, f"Strategy {strategy} failed"

@check("Bugs finds nothing in clean arithmetic code")
def chk_no_bugs():
    b = find_bugs(ARITHMETIC)
    assert b.count == 0

@check("Encode reveals coordinates for string ops")
def chk_encode():
    enc = encode(STRING_OPS)
    assert enc.n_coordinates >= 2
    assert enc.n_declarations > 0

@check("All five programs produce certificate hashes")
def chk_hashes():
    for prog in (ARITHMETIC, STRING_OPS, EXCEPTION, STATEFUL, RECURSIVE):
        r = verify(prog)
        assert r.certificate_hash, "missing certificate hash"

@check("Propositions ok equals total for clean code")
def chk_props():
    r = verify(ARITHMETIC)
    assert r.propositions_ok == r.propositions_total
    assert r.propositions_total > 0

if __name__ == "__main__":
    run_all("Paper 00 — End-to-End Pipeline Soundness")
