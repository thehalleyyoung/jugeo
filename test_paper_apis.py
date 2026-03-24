#!/usr/bin/env python3
"""
Minimal test harnesses for JuGeo APIs mentioned in papers 04, 05, 07, 09, 10, 51.
Execute from /Users/halleyyoung/Documents/jugeo repo root.
"""
import sys
sys.path.insert(0, 'src')

def test_paper04_trust():
    """Paper04: Trust Algebra - TrustLevel and TrustAlgebra"""
    print("\n" + "=" * 70)
    print("PAPER 04: Trust Algebra (paper04-trust-algebra.tex)")
    print("=" * 70)
    
    # API 1: TrustLevel enum
    print("\n[TEST] TrustLevel import (lines 177-186)")
    try:
        from jugeo.evidence.trust import TrustLevel
        five_tier = [
            TrustLevel.CONTRADICTED,
            TrustLevel.UNVERIFIED,
            TrustLevel.COPILOT_SUGGESTED,
            TrustLevel.SOLVER_DISCHARGED,
            TrustLevel.MECHANICALLY_VERIFIED,
        ]
        print(f"[RUNS] Harness: from jugeo.evidence.trust import TrustLevel; five_tier={[t.name for t in five_tier]}")
        print(f"       Result: {[t.name for t in five_tier]}")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")
    
    # API 2: TrustAlgebra operations
    print("\n[TEST] TrustAlgebra instantiation (lines 269-298)")
    try:
        from jugeo.evidence.trust import TrustAlgebra, TrustLevel
        ta = TrustAlgebra()
        result = ta.compare(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)
        print(f"[RUNS] Harness: ta = TrustAlgebra(); ta.compare(COPILOT_SUGGESTED, SOLVER_DISCHARGED)")
        print(f"       Result: {result} (negative means <)")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_paper05_smt():
    """Paper05: SMT Dispatch - Fragment classification and routing"""
    print("\n" + "=" * 70)
    print("PAPER 05: SMT Dispatch (paper05-smt-dispatch.tex)")
    print("=" * 70)
    
    # Note: Paper05 is mostly theory; search for FragmentClassifier, SolverRouter
    print("\n[TEST] solver.solve import (public API)")
    try:
        from jugeo.solver import solve
        print(f"[RUNS] Harness: from jugeo.solver import solve")
        print(f"       Result: solve={solve} (callable={callable(solve)})")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_paper07_effects():
    """Paper07: Python Effects - Effect encoding in JuGeo"""
    print("\n" + "=" * 70)
    print("PAPER 07: Python Effects (paper07-python-effects.tex)")
    print("=" * 70)
    
    # API: Coordinate and CoordinateKind from geometry.site
    print("\n[TEST] Geometry site imports (lines 207-216)")
    try:
        from jugeo.geometry.site import SiteBuilder, Coordinate, CoordinateKind, Morphism, MorphismKind
        print(f"[RUNS] Harness: from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind")
        print(f"       Result: Coordinate={Coordinate}, CoordinateKind={CoordinateKind}")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_paper09_pcp():
    """Paper09: Proof-Carrying Python - Certificate chains"""
    print("\n" + "=" * 70)
    print("PAPER 09: Proof-Carrying Python (paper09-proof-carrying-python.tex)")
    print("=" * 70)
    
    # Check geometry.site imports again (used in paper09)
    print("\n[TEST] Certificate-related imports (lines 207-216)")
    try:
        from jugeo.geometry.site import CoordinateKind, MorphismKind
        print(f"[RUNS] Harness: from jugeo.geometry.site import CoordinateKind, MorphismKind")
        coord_kinds = [k.name for k in CoordinateKind]
        morph_kinds = [k.name for k in MorphismKind]
        print(f"       CoordinateKind: {coord_kinds}")
        print(f"       MorphismKind: {morph_kinds}")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_paper10_evaluation():
    """Paper10: Evaluation - prove() easy API"""
    print("\n" + "=" * 70)
    print("PAPER 10: Evaluation (paper10-evaluation.tex)")
    print("=" * 70)
    
    # API: Easy API functions
    print("\n[TEST] Easy API imports (prove, bugs, equiv, ideate, carry, spec)")
    try:
        from jugeo.easy import prove, bugs, equiv, ideate, carry, spec
        apis = [prove, bugs, equiv, ideate, carry, spec]
        print(f"[RUNS] Harness: from jugeo.easy import prove, bugs, equiv, ideate, carry, spec")
        print(f"       Result: prove={prove}, bugs={bugs}, equiv={equiv}, ideate={ideate}, carry={carry}, spec={spec}")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_paper51_llmz3():
    """Paper51: LLM-Z3 Orchestration - Trust upgrade pipeline"""
    print("\n" + "=" * 70)
    print("PAPER 51: LLM-Z3 Orchestration (paper51-llm-z3-orchestration.tex)")
    print("=" * 70)
    
    # API: TrustAlgebra operations (used in section 3.6)
    print("\n[TEST] TrustAlgebra.join and promote (lines 269-298)")
    try:
        from jugeo.evidence.trust import TrustAlgebra, TrustLevel
        ta = TrustAlgebra()
        
        # join operation
        joined = ta.join(TrustLevel.COPILOT_SUGGESTED, TrustLevel.RUNTIME_WITNESSED)
        print(f"[RUNS] Harness: ta.join(COPILOT_SUGGESTED, RUNTIME_WITNESSED)")
        print(f"       Result: {joined}")
        
        # promote operation  
        promoted = ta.promote(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)
        print(f"[RUNS] Harness: ta.promote(COPILOT_SUGGESTED, SOLVER_DISCHARGED)")
        print(f"       Result: {promoted}")
    except Exception as e:
        print(f"[FAILS] {type(e).__name__}: {e}")


def test_top_level_api():
    """Test main __init__.py exports"""
    print("\n" + "=" * 70)
    print("MAIN PUBLIC API (__init__.py)")
    print("=" * 70)
    
    imports = [
        ('jugeo', 'GeometricSite'),
        ('jugeo', 'TrustAlgebra'),
        ('jugeo', 'construct_judgment'),
        ('jugeo', 'validate_judgment_form'),
        ('jugeo', 'solve'),
        ('jugeo', 'prove'),
        ('jugeo', 'bugs'),
        ('jugeo', 'equiv'),
        ('jugeo', 'spec'),
        ('jugeo', 'ideate'),
        ('jugeo', 'carry'),
    ]
    
    for module, name in imports:
        try:
            mod = __import__(module)
            obj = getattr(mod, name)
            print(f"[RUNS] {module}.{name}: {type(obj).__name__}")
        except Exception as e:
            print(f"[FAILS] {module}.{name}: {type(e).__name__}: {e}")


if __name__ == '__main__':
    test_paper04_trust()
    test_paper05_smt()
    test_paper07_effects()
    test_paper09_pcp()
    test_paper10_evaluation()
    test_paper51_llmz3()
    test_top_level_api()
    print("\n" + "=" * 70)
    print("Test harness complete.")
    print("=" * 70)
