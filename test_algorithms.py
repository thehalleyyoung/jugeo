#!/usr/bin/env python3
"""Simple test runner for algorithms_new.py"""
import sys
import os
sys.path.insert(0, '/Users/halleyyoung/Documents/jugeo')

from src.jugeo.generation.hypercover_treaties.algorithms_new import (
    synthesize_treaty, detect_treaty_conflict, resolve_treaty_conflict,
    run_treaty_algorithm, TreatyAlgorithms, ResolutionStrategy
)

print("✓ Imports successful")

iface_a = {
    "name": "PatchA",
    "version": "1.0.0",
    "exports": ["Alpha", "Beta", "Gamma"],
    "imports": ["Delta", "Epsilon"],
}
iface_b = {
    "name": "PatchB",
    "version": "1.0.1",
    "exports": ["Gamma", "Zeta"],
    "imports": ["Alpha", "Eta"],
}

print("=== Test 1: Treaty Synthesis ===")
treaty = synthesize_treaty(iface_a, iface_b)
assert "treaty_id" in treaty
print(f"✓ Treaty synthesized: {treaty['treaty_id']}")

print("\n=== Test 2: Full Pipeline ===")
result = run_treaty_algorithm(iface_a, iface_b)
assert result['status'] in ['trivial', 'resolved', 'unresolved']
print(f"✓ Pipeline completed with status: {result['status']}")

print("\n=== Test 3: TreatyAlgorithms Facade ===")
facade_result = TreatyAlgorithms.run(iface_a, iface_b)
assert 'treaty_id' in facade_result
print(f"✓ Facade run successful: {facade_result['status']}")

print("\n✓✓✓ All tests passed! ✓✓✓")
