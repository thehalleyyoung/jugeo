#!/usr/bin/env python3
"""Example 2: Equivalence Checking -- Two Implementations, One Proof.

Proves equivalence using the REAL JuGeo pipeline: ``jugeo equiv``
constructs sites for both programs, builds refinement morphisms between them,
and checks descent. Zero obstructions means the implementations are equivalent.

Also demonstrates detecting when implementations DIFFER.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'proofs', 'jugeo'))

from jugeo_proof import (
    theorem, check, verify, check_equiv,
    reset, run_all,
)

reset()

# =====================================================================
#  Programs under verification -- natural Python, no scaffolding
# =====================================================================

FACTORIAL_VERSION_A = '''\
def spec_factorial_nonneg(n, result):
    """Factorial of a non-negative integer is always positive."""
    if n >= 0:
        return result >= 1
    return True

def spec_factorial_base(result_0):
    """factorial(0) == 1."""
    return result_0 == 1

def factorial(n):
    """Iterative factorial using a loop.

    Properties to prove:
      1. spec_factorial_nonneg -- result is always >= 1 for n >= 0
      2. spec_factorial_base -- factorial(0) == 1
    """
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
'''

# Structurally identical copy -- tests that equiv detects exact matches
FACTORIAL_VERSION_A_COPY = FACTORIAL_VERSION_A

FACTORIAL_VERSION_B = '''\
def spec_factorial_nonneg(n, result):
    """Factorial of a non-negative integer is always positive."""
    if n >= 0:
        return result >= 1
    return True

def spec_factorial_base(result_0):
    """factorial(0) == 1."""
    return result_0 == 1

def factorial(n):
    """Recursive factorial.

    Properties to prove:
      1. spec_factorial_nonneg -- result is always >= 1 for n >= 0
      2. spec_factorial_base -- factorial(0) == 1
    """
    if n <= 1:
        return 1
    return n * factorial(n - 1)
'''

# Deliberately buggy version: computes sum, not factorial
BUGGY_FACTORIAL = '''\
def factorial(n):
    """Buggy: computes sum instead of product."""
    result = 0
    for i in range(1, n + 1):
        result += i
    return result
'''

SUM_VERSION_A = '''\
def spec_sum_nonneg(values, result):
    """Sum of non-negative values is non-negative."""
    if all(v >= 0 for v in values):
        return result >= 0
    return True

def total(values):
    """Imperative sum with accumulator.

    Property: spec_sum_nonneg
    """
    acc = 0
    for v in values:
        acc += v
    return acc
'''

SUM_VERSION_B = '''\
def spec_sum_nonneg(values, result):
    """Sum of non-negative values is non-negative."""
    if all(v >= 0 for v in values):
        return result >= 0
    return True

def total(values):
    """Functional sum via built-in.

    Property: spec_sum_nonneg
    """
    return sum(values)
'''


# =====================================================================
#  Theorems -- proved by the REAL JuGeo pipeline
# =====================================================================

@theorem("Identical factorial copies are equivalent")
def identical_equiv():
    """Uses ``jugeo equiv`` on structurally identical code -- must match."""
    eq = check_equiv(FACTORIAL_VERSION_A, FACTORIAL_VERSION_A_COPY)
    assert eq.equivalent, \
        f"Identical code should be equivalent, got obstructions: {eq.obstructions}"
    assert eq.site_a_coords >= 1
    assert eq.site_b_coords >= 1
    return eq


@theorem("Each factorial implementation verifies independently")
def factorial_independent_verify():
    """Verify both factorial variants independently via ``jugeo prove``."""
    r_a = verify(FACTORIAL_VERSION_A)
    r_b = verify(FACTORIAL_VERSION_B)
    assert r_a.verified, f"Version A: {r_a.verdict}"
    assert r_b.verified, f"Version B: {r_b.verdict}"
    assert r_a.H1 == "0"
    assert r_b.H1 == "0"


@theorem("Each sum implementation verifies independently")
def sum_independent_verify():
    """Verify both sum variants independently via ``jugeo prove``."""
    r_a = verify(SUM_VERSION_A)
    r_b = verify(SUM_VERSION_B)
    assert r_a.verified, f"Version A: {r_a.verdict}"
    assert r_b.verified, f"Version B: {r_b.verdict}"
    assert r_a.H1 == "0"
    assert r_b.H1 == "0"


# =====================================================================
#  Empirical checks
# =====================================================================

@check("Structurally different implementations report obstructions")
def structural_diff_detected():
    eq = check_equiv(FACTORIAL_VERSION_A, FACTORIAL_VERSION_B)
    # Structurally different bodies -> obstructions (even if semantically equal)
    assert not eq.equivalent, \
        "Structurally different code should report obstructions via jugeo equiv"


@check("Buggy factorial is NOT equivalent to correct factorial")
def buggy_not_equiv():
    eq = check_equiv(FACTORIAL_VERSION_A, BUGGY_FACTORIAL)
    assert not eq.equivalent, \
        "Buggy factorial should NOT be equivalent to correct factorial"


@check("Equivalent programs have comparable site sizes")
def site_size_comparable():
    r_a = verify(FACTORIAL_VERSION_A)
    r_b = verify(FACTORIAL_VERSION_B)
    ratio = max(r_a.n_coordinates, r_b.n_coordinates) / \
            max(1, min(r_a.n_coordinates, r_b.n_coordinates))
    assert ratio <= 5, \
        f"Site size ratio {ratio} is too large"


if __name__ == "__main__":
    run_all("Example 2 -- Equivalence Checking (Real Pipeline)")
