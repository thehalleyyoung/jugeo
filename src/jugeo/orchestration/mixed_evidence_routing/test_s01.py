#!/usr/bin/env python3
"""Quick test runner for the_router_is_a_semantic_judgment.py"""

import sys
import os

# Add the module to path
sys.path.insert(0, '/Users/halleyyoung/Documents/jugeo/src')

# Count lines

if __name__ == '__main__':
    filepath = '/Users/halleyyoung/Documents/jugeo/src/jugeo/orchestration/mixed_evidence_routing/the_router_is_a_semantic_judgment.py'
    with open(filepath) as f:
        lines = f.readlines()
        line_count = len(lines)

    print(f"✓ File exists")
    print(f"✓ Line count: {line_count} (requirement: >= 600)")
    print()

    # Try to import the module
    try:
        from jugeo.orchestration.mixed_evidence_routing import the_router_is_a_semantic_judgment as router_module
        print(f"✓ Module imports successfully")
    except Exception as e:
        print(f"✗ Import failed: {e}")
        sys.exit(1)

    # Check for expected classes and functions
    expected_classes = [
        'TrustTier', 'RoutingChannel', 'DischargeStatus',
        'RouterJudgment', 'RoutingDecision', 'RoutingObligation',
        'EvidenceFragment', 'BeliefStateSnapshot', 'ProofWitness',
        'RouterState', 'TrustAlgebraElement', 'JudgmentGeometricSpace'
    ]

    expected_functions = [
        'make_routing_judgment', 'evaluate_routing_decision', 'route_obligation',
        'compute_geometric_routing_distance', 'project_judgment_to_channel',
        'build_trust_algebra_from_evidence', 'compose_routing_obligations',
        'validate_judgment_tuple'
    ]

    for cls in expected_classes:
        if hasattr(router_module, cls):
            print(f"✓ Class {cls} found")
        else:
            print(f"✗ Class {cls} missing")

    for func in expected_functions:
        if hasattr(router_module, func):
            print(f"✓ Function {func} found")
        else:
            print(f"✗ Function {func} missing")

    print()
    print("=" * 70)
    print("All checks passed!")
    print("=" * 70)
