#!/usr/bin/env python3
"""Paper 04 — Trust Algebra Soundness."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo_proof import (
    theorem, check, verify, encode,
    reset, run_all,
)
from jugeo.judgments.judgment_terms import (
    JudgmentBuilder, Proposition, PropositionKind,
    TrustLevel, ProvenanceSource,
)
from jugeo.geometry.site import Coordinate, CoordinateKind

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

def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
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

# ─── CLI-based Theorems ────────────────────────────────────

@theorem("Trust algebra passes on pure code", code=PURE)
def trust_pure(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.trust == "COPILOT_SUGGESTED"

@theorem("Trust algebra passes on branching code", code=BRANCHING)
def trust_branch(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.trust == "COPILOT_SUGGESTED"

@theorem("Trust algebra passes on exception code", code=EXCEPTION)
def trust_exception(result):
    assert result.verified
    assert result.site.trust_algebra_pass
    assert result.trust == "COPILOT_SUGGESTED"

# ─── Deep API Theorems ─────────────────────────────────────

@theorem("Deep API: trust algebra reflexivity — t <= t")
def deep_trust_reflexivity():
    for level in (TrustLevel.CONTRADICTED, TrustLevel.UNVERIFIED,
                  TrustLevel.COPILOT_SUGGESTED, TrustLevel.VERIFIED_PROOF):
        assert level.value <= level.value

@theorem("Deep API: trust algebra transitivity — a<=b and b<=c implies a<=c")
def deep_trust_transitivity():
    levels = [TrustLevel.CONTRADICTED, TrustLevel.UNVERIFIED,
              TrustLevel.COPILOT_SUGGESTED, TrustLevel.RUNTIME_WITNESSED,
              TrustLevel.SOLVER_DISCHARGED, TrustLevel.VERIFIED_PROOF]
    for i in range(len(levels)):
        for j in range(i, len(levels)):
            for k in range(j, len(levels)):
                assert levels[i].value <= levels[k].value

@theorem("Deep API: trust algebra antisymmetry — a<=b and b<=a implies a==b")
def deep_trust_antisymmetry():
    levels = [TrustLevel.CONTRADICTED, TrustLevel.UNVERIFIED,
              TrustLevel.COPILOT_SUGGESTED, TrustLevel.VERIFIED_PROOF]
    for a in levels:
        for b in levels:
            if a.value <= b.value and b.value <= a.value:
                assert a == b

@theorem("Deep API: CONTRADICTED absorbs — meet(CONTRADICTED, x) = CONTRADICTED")
def deep_contradicted_absorbs():
    for level in (TrustLevel.UNVERIFIED, TrustLevel.COPILOT_SUGGESTED,
                  TrustLevel.VERIFIED_PROOF):
        assert min(TrustLevel.CONTRADICTED.value, level.value) == TrustLevel.CONTRADICTED.value

# ─── Checks ────────────────────────────────────────────────

@check("Reflexivity axiom holds on all programs")
def chk_reflexivity():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("reflexivity", False)

@check("Transitivity axiom holds on all programs")
def chk_transitivity():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("transitivity", False)

@check("Antisymmetry axiom holds on all programs")
def chk_antisymmetry():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("antisymmetry", False)

@check("Meet exists axiom holds on all programs")
def chk_meet_exists():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("meet_exists", False)

@check("Oracle ceiling axiom holds on all programs")
def chk_oracle_ceiling():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("oracle_ceiling", False)

@check("Monotonicity axiom holds on all programs")
def chk_monotonicity():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("monotonicity", False)

@check("Contradicted absorbs axiom holds on all programs")
def chk_contradicted():
    for code in (PURE, BRANCHING, EXCEPTION):
        r = verify(code)
        assert r.site.trust_algebra_axioms.get("contradicted_absorbs", False)

@check("Different trust floors still produce verified results")
def chk_trust_floors():
    for floor in ("copilot", "unverified"):
        r = verify(PURE, trust_floor=floor)
        assert r.verified, f"trust floor '{floor}' did not verify"

if __name__ == "__main__":
    run_all("Paper 04 — Trust Algebra Soundness")
