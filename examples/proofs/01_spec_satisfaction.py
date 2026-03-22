#!/usr/bin/env python3
"""Example 1: Specification Satisfaction -- Merge Sort Correctness.

Proves that a merge sort implementation satisfies its specification using the
REAL JuGeo pipeline: ``jugeo prove`` constructs a Grothendieck site, generates
propositions at each coordinate (one per function), checks them locally, runs
descent, and issues a certificate when H1 = 0.

This is the JuGeo analogue of:
  - LEAN: ``theorem merge_sort_correct`` with ~80 lines of tactic proofs
  - F*:   ``val merge_sort : ... -> Lemma (ensures sorted)``

The difference: you write Python, JuGeo proves it, the proof ships with code.
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
#  Program under verification -- natural Python, no scaffolding
# =====================================================================

MERGE_SORT_CODE = '''\
# -- Specifications --

def spec_is_sorted(result):
    """Output is in non-decreasing order."""
    return all(result[i] <= result[i + 1] for i in range(len(result) - 1))

def spec_is_permutation(result, original):
    """Output is a rearrangement of the input (same multiset)."""
    return sorted(result) == sorted(original)

def spec_length_preserved(result, original):
    """Output has the same length as the input."""
    return len(result) == len(original)

# -- Implementation --

def merge(left, right):
    """Merge two sorted lists into one sorted list."""
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result

def merge_sort(arr):
    """Merge sort: stable, O(n log n), returns a new sorted list.

    Properties to prove:
      1. spec_is_sorted(merge_sort(arr))         -- output is sorted
      2. spec_is_permutation(merge_sort(arr), arr) -- no elements lost/gained
      3. spec_length_preserved(merge_sort(arr), arr) -- length is unchanged
    """
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)
'''

BINARY_SEARCH_CODE = '''\
# -- Specification --

def spec_finds_target(result, arr, target):
    """If target is in arr, result is a valid index pointing to it."""
    if target in arr:
        return result >= 0 and arr[result] == target
    return result == -1

def spec_result_in_bounds(result, arr):
    """Result is either -1 or a valid index."""
    return result == -1 or (0 <= result < len(arr))

# -- Implementation --

def binary_search(arr, target):
    """Binary search on a sorted array.

    Properties to prove:
      1. spec_finds_target -- returns correct index or -1
      2. spec_result_in_bounds -- never returns out-of-range index
    """
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
'''


# =====================================================================
#  Theorems -- proved by the REAL JuGeo pipeline
# =====================================================================

@theorem("Merge sort is correct", code=MERGE_SORT_CODE)
def merge_sort_proof(result):
    """Proves 3 specs about merge sort via real site construction + descent.

    The site has one coordinate per function (merge, merge_sort, specs).
    Each spec becomes a proposition at its coordinate.  Descent glues them.
    H1 = 0 means all specs hold simultaneously.
    """
    assert result.verified, f"Expected verified, got {result.verdict}"
    assert result.H1 == "0", f"Descent failed: H1={result.H1}"
    assert result.propositions_ok == result.propositions_total, \
        f"Props: {result.propositions_ok}/{result.propositions_total}"
    assert result.site.grothendieck_axioms_pass
    assert result.site.trust_algebra_pass


@theorem("Binary search is correct", code=BINARY_SEARCH_CODE)
def binary_search_proof(result):
    """Proves binary search returns the right index or -1."""
    assert result.verified
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total
    assert result.all_axioms_pass


# =====================================================================
#  Empirical checks
# =====================================================================

@check("Merge sort site has coordinates for each function")
def merge_sort_site_structure():
    enc = encode(MERGE_SORT_CODE)
    assert enc.n_coordinates >= 3, \
        f"Expected >=3 coordinates (merge, merge_sort, specs), got {enc.n_coordinates}"


@check("Binary search site is simpler than merge sort site")
def complexity_comparison():
    r_merge = verify(MERGE_SORT_CODE)
    r_search = verify(BINARY_SEARCH_CODE)
    assert r_merge.n_coordinates >= r_search.n_coordinates, \
        f"Merge sort ({r_merge.n_coordinates}) should have >= coords than binary search ({r_search.n_coordinates})"


if __name__ == "__main__":
    run_all("Example 1 -- Specification Satisfaction (Real Pipeline)")
