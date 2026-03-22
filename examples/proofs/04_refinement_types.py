#!/usr/bin/env python3
"""Example 4: Refinement Types -- Verified Constraints via JuGeo.

Verifies programs with type-like constraints (assert statements, isinstance
checks, range bounds) using the REAL JuGeo pipeline.  ``jugeo prove``
extracts propositions from assertions and type guards in the code, then
checks that all constraints are satisfiable via descent.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'proofs', 'jugeo'))

from jugeo_proof import (
    theorem, check, verify, encode,
    reset, run_all,
)

reset()

# =====================================================================
#  Programs with refinement-type constraints
# =====================================================================

SAFE_DIVIDE = '''\
def spec_no_division_by_zero(b, result):
    """Division never raises ZeroDivisionError when b > 0."""
    return isinstance(result, float)

def spec_result_sign(a, b, result):
    """If a >= 0 and b > 0, result >= 0."""
    if a >= 0 and b > 0:
        return result >= 0
    return True

def safe_divide(a, b):
    """Division with precondition: b must be positive.

    Refinement type: b : {x : int | x > 0}

    Properties:
      1. spec_no_division_by_zero -- always returns a float
      2. spec_result_sign -- sign is correct for non-negative inputs
    """
    assert isinstance(a, (int, float)), "a must be numeric"
    assert isinstance(b, (int, float)), "b must be numeric"
    assert b > 0, "divisor must be positive"
    return float(a) / float(b)
'''

BOUNDED_BUFFER = '''\
def spec_size_invariant(buf):
    """Buffer never exceeds its capacity."""
    return len(buf["items"]) <= buf["capacity"]

def spec_push_grows(buf_before_len, buf_after_len, capacity):
    """Push increases size by 1 when not full."""
    if buf_before_len < capacity:
        return buf_after_len == buf_before_len + 1
    return buf_after_len == buf_before_len

def make_buffer(capacity):
    """Create a bounded buffer with explicit capacity constraint.

    Refinement type: capacity : {n : int | n > 0}
    """
    assert isinstance(capacity, int) and capacity > 0
    return {"items": [], "capacity": capacity}

def push(buf, item):
    """Push an item, respecting the capacity bound.

    Pre:  len(buf["items"]) < buf["capacity"]
    Post: len(buf["items"]) <= buf["capacity"]
    """
    assert len(buf["items"]) < buf["capacity"], "buffer is full"
    buf["items"].append(item)
    assert spec_size_invariant(buf), "invariant violated after push"
    return buf

def pop(buf):
    """Pop an item, requiring non-empty buffer.

    Refinement type: buf : {b : dict | len(b["items"]) > 0}
    """
    assert len(buf["items"]) > 0, "buffer is empty"
    return buf["items"].pop()
'''

PERCENTAGE_CLAMP = '''\
def spec_in_range(result):
    """Output is always in [0, 100]."""
    return 0 <= result <= 100

def spec_idempotent(value, result):
    """If value is already in range, clamping is identity."""
    if 0 <= value <= 100:
        return result == value
    return True

def clamp_percentage(value):
    """Clamp a numeric value to the percentage range [0, 100].

    Post-condition refinement: result : {x : float | 0 <= x <= 100}
    """
    assert isinstance(value, (int, float)), "value must be numeric"
    result = max(0.0, min(100.0, float(value)))
    assert 0 <= result <= 100, "post-condition: result in [0, 100]"
    return result
'''

SORTED_INSERT = '''\
def spec_output_sorted(result):
    """Output list is sorted."""
    return all(result[i] <= result[i + 1] for i in range(len(result) - 1))

def spec_contains_element(result, elem):
    """Output contains the inserted element."""
    return elem in result

def spec_length_grows(original, result):
    """Output has exactly one more element."""
    return len(result) == len(original) + 1

def sorted_insert(sorted_list, elem):
    """Insert into sorted list, maintaining sort order.

    Pre:  sorted_list is sorted
    Post: result is sorted and contains elem
    """
    assert all(sorted_list[i] <= sorted_list[i+1]
               for i in range(len(sorted_list) - 1)), "input must be sorted"
    result = list(sorted_list)
    for i, v in enumerate(result):
        if elem <= v:
            result.insert(i, elem)
            return result
    result.append(elem)
    return result
'''


# =====================================================================
#  Theorems -- proved by the REAL JuGeo pipeline
# =====================================================================

@theorem("Safe divide respects preconditions", code=SAFE_DIVIDE)
def safe_divide_proof(result):
    """JuGeo extracts propositions from asserts and specs, verifies all."""
    assert result.verified, f"Expected verified, got {result.verdict}"
    assert result.H1 == "0", f"Descent failed: H1={result.H1}"
    assert result.propositions_ok == result.propositions_total


@theorem("Bounded buffer maintains invariants", code=BOUNDED_BUFFER)
def bounded_buffer_proof(result):
    """Verifies capacity-bound invariant across push/pop operations."""
    assert result.verified
    assert result.H1 == "0"
    assert result.n_coordinates >= 3, \
        f"Expected >=3 coords (make_buffer, push, pop), got {result.n_coordinates}"


@theorem("Percentage clamp satisfies post-conditions", code=PERCENTAGE_CLAMP)
def percentage_clamp_proof(result):
    """Verifies output is always in [0, 100]."""
    assert result.verified
    assert result.H1 == "0"
    assert result.all_axioms_pass


@theorem("Sorted insert preserves order", code=SORTED_INSERT)
def sorted_insert_proof(result):
    """Verifies insertion into sorted list maintains sort invariant."""
    assert result.verified
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total


# =====================================================================
#  Empirical checks
# =====================================================================

@check("Propositions capture assertion-based constraints")
def propositions_capture_asserts():
    r = verify(SAFE_DIVIDE)
    assert r.propositions_total >= 2, \
        f"Expected >=2 propositions from asserts, got {r.propositions_total}"


@check("Complex code generates more coordinates than simple code")
def complexity_scaling():
    r_buf = verify(BOUNDED_BUFFER)
    r_clamp = verify(PERCENTAGE_CLAMP)
    assert r_buf.n_coordinates >= r_clamp.n_coordinates, \
        f"Buffer ({r_buf.n_coordinates}) should have >= coords than clamp ({r_clamp.n_coordinates})"


@check("Encode reveals refinement structure")
def encode_reveals_refinements():
    enc = encode(SORTED_INSERT)
    assert enc.n_coordinates >= 2, \
        f"Expected >=2 coordinates, got {enc.n_coordinates}"
    assert enc.n_assertions >= 1, \
        f"Expected >=1 assertions in site, got {enc.n_assertions}"


if __name__ == "__main__":
    run_all("Example 4 -- Refinement Types (Real Pipeline)")
