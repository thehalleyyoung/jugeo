#!/usr/bin/env python3
"""Paper 02 — Judgment Algebra (8-tuple)."""
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

PURE = '''
def add(x, y):
    return x + y

def sub(x, y):
    return x - y
'''

BRANCHING = '''
def classify(x):
    if x > 0:
        return 'positive'
    elif x < 0:
        return 'negative'
    return 'zero'

def absolute(x):
    if x < 0:
        return -x
    return x
'''

LOOPING = '''
def total(xs):
    s = 0
    for x in xs:
        s += x
    return s

def count_positives(xs):
    c = 0
    for x in xs:
        if x > 0:
            c += 1
    return c
'''

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Trust algebra axioms pass on pure code", code=PURE)
def trust_pure(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.propositions_total > 0
    assert result.propositions_ok == result.propositions_total

@theorem("Trust algebra axioms pass on branching code", code=BRANCHING)
def trust_branch(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.n_coordinates >= 2

@theorem("Trust algebra axioms pass on looping code", code=LOOPING)
def trust_loop(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.H1 == "0"

@theorem("Propositions generated per coordinate across code types")
def props_per_coord():
    for code in (PURE, BRANCHING, LOOPING):
        r = verify(code)
        assert r.propositions_total > 0
        assert r.n_coordinates > 0
        assert r.propositions_total >= r.n_coordinates

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: judgment 8-tuple construction and trust levels")
def deep_judgment_tuple():
    fn = Coordinate(('mod', 'add'), CoordinateKind.FUNCTION)

    j = (JudgmentBuilder().at(fn)
        .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                              formula='type_correct(add)'))
        .of_type_named('PureFunction')
        .with_trust_level(TrustLevel.VERIFIED_PROOF)
        .from_source(ProvenanceSource.SOLVER)
        .build())

    assert j.coordinate.name == 'mod.add'
    assert j.proposition.formula == 'type_correct(add)'
    assert j.trust.level == TrustLevel.VERIFIED_PROOF
    assert not j.has_obstructions()

@theorem("Deep API: trust level ordering is a total order")
def deep_trust_ordering():
    levels = [
        TrustLevel.CONTRADICTED,
        TrustLevel.UNVERIFIED,
        TrustLevel.COPILOT_SUGGESTED,
        TrustLevel.RUNTIME_WITNESSED,
        TrustLevel.SOLVER_DISCHARGED,
        TrustLevel.VERIFIED_PROOF,
    ]
    for i in range(len(levels) - 1):
        assert levels[i].value < levels[i + 1].value, \
            f"{levels[i]} should be < {levels[i+1]}"

@theorem("Deep API: judgments at different coordinates are independent")
def deep_judgment_independence():
    fn_add = Coordinate(('mod', 'add'), CoordinateKind.FUNCTION)
    fn_sub = Coordinate(('mod', 'sub'), CoordinateKind.FUNCTION)

    j_add = (JudgmentBuilder().at(fn_add)
        .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                              formula='type_correct(add)'))
        .of_type_named('PureFunction')
        .with_trust_level(TrustLevel.VERIFIED_PROOF)
        .from_source(ProvenanceSource.SOLVER)
        .build())

    j_sub = (JudgmentBuilder().at(fn_sub)
        .claiming(Proposition(kind=PropositionKind.BEHAVIORAL,
                              formula='sub(x,y) == x - y'))
        .of_type_named('PureFunction')
        .with_trust_level(TrustLevel.RUNTIME_WITNESSED)
        .from_source(ProvenanceSource.RUNTIME)
        .build())

    assert j_add.coordinate.name != j_sub.coordinate.name
    assert j_add.proposition.kind != j_sub.proposition.kind
    assert j_add.trust.level != j_sub.trust.level

# ─── Checks ────────────────────────────────────────────────

@check("All 7 trust algebra axioms verified on pure code")
def chk_all_axioms_pure():
    r = verify(PURE)
    axioms = r.site.trust_algebra_axioms
    for name in ("reflexivity", "transitivity", "antisymmetry",
                 "meet_exists", "oracle_ceiling", "monotonicity",
                 "contradicted_absorbs"):
        assert axioms.get(name, False), f"axiom {name} failed"

@check("All 7 trust algebra axioms verified on branching code")
def chk_all_axioms_branch():
    r = verify(BRANCHING)
    axioms = r.site.trust_algebra_axioms
    for name in ("reflexivity", "transitivity", "antisymmetry",
                 "meet_exists", "oracle_ceiling", "monotonicity",
                 "contradicted_absorbs"):
        assert axioms.get(name, False), f"axiom {name} failed"

@check("All 7 trust algebra axioms verified on looping code")
def chk_all_axioms_loop():
    r = verify(LOOPING)
    axioms = r.site.trust_algebra_axioms
    for name in ("reflexivity", "transitivity", "antisymmetry",
                 "meet_exists", "oracle_ceiling", "monotonicity",
                 "contradicted_absorbs"):
        assert axioms.get(name, False), f"axiom {name} failed"

@check("Deep API: PropositionKind has STRUCTURAL and BEHAVIORAL")
def chk_deep_prop_kinds():
    assert PropositionKind.STRUCTURAL.value == 'structural'
    assert PropositionKind.BEHAVIORAL.value == 'behavioral'

@check("Encode reveals judgment structure for branching code")
def chk_encode_branch():
    enc = encode(BRANCHING)
    assert enc.n_coordinates >= 2
    assert enc.n_declarations > 0

@check("Morphisms present in all code types")
def chk_morphisms():
    for code in (PURE, BRANCHING, LOOPING):
        r = verify(code)
        assert r.n_morphisms > 0

if __name__ == "__main__":
    run_all("Paper 02 — Judgment Algebra (8-tuple)")
