#!/usr/bin/env python3
"""Example 7: Task Router Integration -- All Three Problem Modes.

Demonstrates all three JuGeo problem modes through the real pipeline:
  1. Bug detection  -- ``find_bugs()`` on buggy vs clean code
  2. Equivalence    -- ``check_equiv()`` on two implementations
  3. Spec adherence -- ``verify()`` on an implementation with specs

Each mode runs ``jugeo`` under the hood -- no local fallbacks.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'proofs', 'jugeo'))

from jugeo_proof import (
    theorem, check, verify, find_bugs, check_equiv,
    reset, run_all,
)

reset()

# =====================================================================
#  1. Bug detection -- buggy vs clean code
# =====================================================================

BUGGY_SOURCE = '''\
def append_to(element, target=[]):
    """Mutable default argument -- a classic Python bug."""
    target.append(element)
    return target
'''

CLEAN_SOURCE = '''\
def spec_append_returns_list(result):
    """append_to always returns a list."""
    return isinstance(result, list)

def append_to(element, target=None):
    """Fixed: uses None sentinel instead of mutable default.

    Property: spec_append_returns_list
    """
    if target is None:
        target = []
    target.append(element)
    return target
'''

# =====================================================================
#  2. Equivalence -- iterative vs recursive factorial
# =====================================================================

ITERATIVE_FACTORIAL = '''\
def spec_factorial_positive(n, result):
    """Factorial of non-negative int is always positive."""
    return result >= 1 if n >= 0 else True

def factorial(n):
    """Iterative factorial.

    Property: spec_factorial_positive
    """
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
'''

RECURSIVE_FACTORIAL = '''\
def spec_factorial_positive(n, result):
    """Factorial of non-negative int is always positive."""
    return result >= 1 if n >= 0 else True

def factorial(n):
    """Recursive factorial.

    Property: spec_factorial_positive
    """
    if n <= 1:
        return 1
    return n * factorial(n - 1)
'''

# =====================================================================
#  3. Spec adherence -- Fibonacci with properties
# =====================================================================

FIBONACCI = '''\
def spec_fib_base_0(result_0):
    """fibonacci(0) == 0."""
    return result_0 == 0

def spec_fib_base_1(result_1):
    """fibonacci(1) == 1."""
    return result_1 == 1

def spec_fib_nonneg(n, result):
    """fibonacci(n) is non-negative for n >= 0."""
    return result >= 0 if n >= 0 else True

def fibonacci(n):
    """Iterative Fibonacci.

    Properties:
      1. spec_fib_base_0 -- fib(0) = 0
      2. spec_fib_base_1 -- fib(1) = 1
      3. spec_fib_nonneg -- fib(n) >= 0
    """
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''


# =====================================================================
#  Theorems and checks -- all via the REAL JuGeo pipeline
# =====================================================================

@theorem("Fibonacci satisfies its specification", code=FIBONACCI)
def fibonacci_proof(result):
    """Verifies Fibonacci base cases and non-negativity."""
    assert result.verified, f"Expected verified, got {result.verdict}"
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total


@theorem("Clean source passes verification", code=CLEAN_SOURCE)
def clean_source_proof(result):
    """Verifies the fixed append_to."""
    assert result.verified
    assert result.H1 == "0"


@theorem("Both factorial implementations verify independently")
def factorial_verify():
    """Verify both factorial variants independently via ``jugeo prove``."""
    r_iter = verify(ITERATIVE_FACTORIAL)
    r_rec = verify(RECURSIVE_FACTORIAL)
    assert r_iter.verified, f"Iterative: {r_iter.verdict}"
    assert r_rec.verified, f"Recursive: {r_rec.verdict}"
    assert r_iter.H1 == "0"
    assert r_rec.H1 == "0"


@check("Buggy source has bugs detected by pipeline")
def buggy_has_bugs():
    bugs = find_bugs(BUGGY_SOURCE)
    assert bugs.count > 0 or bugs.obstruction_count > 0, \
        "Expected bugs in buggy source"


@check("Clean source has zero bugs")
def clean_no_bugs():
    bugs = find_bugs(CLEAN_SOURCE)
    assert bugs.count == 0, \
        f"Expected 0 bugs in clean source, got {bugs.count}"


@check("All three modes produce real pipeline results")
def all_modes_work():
    # Bug detection
    bugs = find_bugs(BUGGY_SOURCE)
    assert isinstance(bugs.raw, list), "find_bugs should return raw pipeline data"

    # Equivalence
    eq = check_equiv(ITERATIVE_FACTORIAL, RECURSIVE_FACTORIAL)
    assert isinstance(eq.raw, list), "check_equiv should return raw pipeline data"

    # Verification
    r = verify(FIBONACCI)
    assert isinstance(r.raw, list), "verify should return raw pipeline data"


if __name__ == "__main__":
    run_all("Example 7 -- Task Router Integration (Real Pipeline)")
