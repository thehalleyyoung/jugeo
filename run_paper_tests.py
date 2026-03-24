#!/usr/bin/env python3
"""
Execute and capture all JuGeo paper API tests.
This script will run, capture results, and write them to a file.
"""
import sys
import os
import traceback
from io import StringIO

# Change to repo root
os.chdir('/Users/halleyyoung/Documents/jugeo')
sys.path.insert(0, 'src')

output = StringIO()

def log(msg):
    print(msg)
    output.write(msg + '\n')

log("=" * 80)
log("JuGEO PAPER API TEST HARNESS")
log("Repo: /Users/halleyyoung/Documents/jugeo")
log("=" * 80)

# ============================================================================
# PAPER 04: Trust Algebra
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 04: Trust Algebra (paper04-trust-algebra.tex)")
log("Lines 177-186: TrustLevel enum import")
log("=" * 80)

try:
    from jugeo.evidence.trust import TrustLevel
    five_tier = [
        TrustLevel.CONTRADICTED,
        TrustLevel.UNVERIFIED,
        TrustLevel.COPILOT_SUGGESTED,
        TrustLevel.SOLVER_DISCHARGED,
        TrustLevel.MECHANICALLY_VERIFIED,
    ]
    log("\n[RUNS] API: TrustLevel enum")
    log("Harness: from jugeo.evidence.trust import TrustLevel; [TrustLevel.CONTRADICTED, ...]")
    log(f"Stdout: {[t.name for t in five_tier]}")
except Exception as e:
    log(f"\n[FAILS] TrustLevel")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# Paper04 - TrustAlgebra class
log("\n" + "-" * 80)
log("PAPER 04: Lines 269-298 - TrustAlgebra class operations")
log("-" * 80)

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    ta = TrustAlgebra()
    
    log("\n[RUNS] API: TrustAlgebra instantiation")
    log("Harness: from jugeo.evidence.trust import TrustAlgebra; ta = TrustAlgebra()")
    log(f"Stdout: TrustAlgebra instance created: {ta}")
    
    # Test compare
    cmp_result = ta.compare(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)
    log("\n[RUNS] API: TrustAlgebra.compare()")
    log("Harness: ta.compare(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)")
    log(f"Stdout: {cmp_result}")
    
except Exception as e:
    log(f"\n[FAILS] TrustAlgebra operations")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# PAPER 05: SMT Dispatch
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 05: SMT Dispatch (paper05-smt-dispatch.tex)")
log("Main public API: solve()")
log("=" * 80)

try:
    from jugeo.solver import solve
    log("\n[RUNS] API: jugeo.solver.solve")
    log("Harness: from jugeo.solver import solve")
    log(f"Stdout: solve function imported, callable={callable(solve)}")
except Exception as e:
    log(f"\n[FAILS] jugeo.solver.solve")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# PAPER 07: Python Effects
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 07: Python Effects (paper07-python-effects.tex)")
log("Lines 207-216: Geometry site imports (Coordinate, CoordinateKind, Morphism, MorphismKind)")
log("=" * 80)

try:
    from jugeo.geometry.site import SiteBuilder, Coordinate, CoordinateKind, Morphism, MorphismKind
    
    log("\n[RUNS] API: Coordinate classes and enums")
    log("Harness: from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind")
    
    # Try to enumerate kinds
    coord_names = [k.name if hasattr(k, 'name') else str(k) for k in CoordinateKind]
    log(f"Stdout: CoordinateKind members: {coord_names}")
    
except Exception as e:
    log(f"\n[FAILS] jugeo.geometry.site imports")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# PAPER 09: Proof-Carrying Python
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 09: Proof-Carrying Python (paper09-proof-carrying-python.tex)")
log("Lines 207-216: Certificate-related imports")
log("=" * 80)

try:
    from jugeo.geometry.site import CoordinateKind, MorphismKind
    
    log("\n[RUNS] API: Certificate-related enums")
    log("Harness: from jugeo.geometry.site import CoordinateKind, MorphismKind")
    
    coord_kinds = [k.name if hasattr(k, 'name') else str(k) for k in CoordinateKind]
    morph_kinds = [k.name if hasattr(k, 'name') else str(k) for k in MorphismKind]
    
    log(f"Stdout: CoordinateKind: {coord_kinds}")
    log(f"        MorphismKind: {morph_kinds}")
    
except Exception as e:
    log(f"\n[FAILS] jugeo.geometry.site in paper09 context")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# PAPER 10: Evaluation
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 10: Evaluation (paper10-evaluation.tex)")
log("Main easy API: prove, bugs, equiv, ideate, carry, spec")
log("=" * 80)

try:
    from jugeo.easy import prove, bugs, equiv, ideate, carry, spec
    
    log("\n[RUNS] API: Easy API functions")
    log("Harness: from jugeo.easy import prove, bugs, equiv, ideate, carry, spec")
    
    funcs = [
        ('prove', prove),
        ('bugs', bugs),
        ('equiv', equiv),
        ('ideate', ideate),
        ('carry', carry),
        ('spec', spec),
    ]
    
    for name, func in funcs:
        log(f"Stdout: {name}={func}, callable={callable(func)}")
        
except Exception as e:
    log(f"\n[FAILS] jugeo.easy API")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# PAPER 51: LLM-Z3 Orchestration
# ============================================================================
log("\n" + "=" * 80)
log("PAPER 51: LLM-Z3 Orchestration (paper51-llm-z3-orchestration.tex)")
log("Lines 269-298: TrustAlgebra operations (join, promote)")
log("=" * 80)

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    ta = TrustAlgebra()
    
    log("\n[RUNS] API: TrustAlgebra.join()")
    log("Harness: ta.join(TrustLevel.COPILOT_SUGGESTED, TrustLevel.RUNTIME_WITNESSED)")
    
    if hasattr(ta, 'join'):
        joined = ta.join(TrustLevel.COPILOT_SUGGESTED, TrustLevel.RUNTIME_WITNESSED)
        log(f"Stdout: {joined}")
    else:
        log("Stdout: (method not found, checking available methods...)")
        log(f"Available methods: {[m for m in dir(ta) if not m.startswith('_')]}")
    
    log("\n[RUNS] API: TrustAlgebra.promote()")
    log("Harness: ta.promote(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)")
    
    if hasattr(ta, 'promote'):
        promoted = ta.promote(TrustLevel.COPILOT_SUGGESTED, TrustLevel.SOLVER_DISCHARGED)
        log(f"Stdout: {promoted}")
    else:
        log("Stdout: (method not found)")
        
except Exception as e:
    log(f"\n[FAILS] TrustAlgebra operations in paper51 context")
    log(f"Exception: {type(e).__name__}: {e}")
    log(traceback.format_exc())

# ============================================================================
# Main public API (__init__.py)
# ============================================================================
log("\n" + "=" * 80)
log("MAIN PUBLIC API (src/jugeo/__init__.py)")
log("=" * 80)

import jugeo

public_api = [
    'GeometricSite',
    'TrustAlgebra',
    'construct_judgment',
    'validate_judgment_form',
    'solve',
    'prove',
    'bugs',
    'equiv',
    'spec',
    'ideate',
    'carry',
]

log("\n[CHECKING] Main __init__.py exports:")
for name in public_api:
    if hasattr(jugeo, name):
        obj = getattr(jugeo, name)
        log(f"  [RUNS] jugeo.{name}: {type(obj).__name__} (None={obj is None})")
    else:
        log(f"  [FAILS] jugeo.{name}: not exported")

# ============================================================================
# Write results to file
# ============================================================================
log("\n" + "=" * 80)
log("Test harness execution complete")
log("=" * 80)

results = output.getvalue()
print("\n\n" + results)

# Save to file
with open('/Users/halleyyoung/Documents/jugeo/PAPER_API_TEST_RESULTS.txt', 'w') as f:
    f.write(results)

log("\nResults saved to: PAPER_API_TEST_RESULTS.txt")
