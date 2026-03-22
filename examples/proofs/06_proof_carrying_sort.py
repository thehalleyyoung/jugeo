#!/usr/bin/env python3
"""Example 6: Proof-Carrying Merge Sort -- Complete Certificate Lifecycle.

End-to-end proof-carrying code demonstration using the REAL JuGeo pipeline:

  1. ``carry_proof(source)`` runs ``jugeo prove``, bundles a ProofCertificate
  2. Certificate includes: code hash, verdict, trust, site metrics, H1, axioms
  3. Serialize to JSON, deserialize, re-verify -- O(1) hash check
  4. Tampering detection: modifying the code invalidates the certificate
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'proofs', 'jugeo'))

from jugeo_proof import (
    theorem, check, verify, carry_proof, encode,
    ProofCertificate,
    reset, run_all,
)

reset()

# =====================================================================
#  Program under verification -- merge sort with 3 specs
# =====================================================================

MERGE_SORT_CODE = '''\
from collections import Counter

# -- Specifications --

def spec_is_sorted(result):
    """Spec 1: output is in non-decreasing order."""
    return all(result[i] <= result[i + 1] for i in range(len(result) - 1))

def spec_is_permutation(result, original):
    """Spec 2: output is a rearrangement of the input (same multiset)."""
    return Counter(result) == Counter(original)

def spec_length_preserved(result, original):
    """Spec 3: output length matches input length."""
    return len(result) == len(original)

# -- Implementation --

def merge(left, right):
    """Merge two sorted lists, preserving stability (<=)."""
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
    """Stable merge sort -- returns a new sorted list.

    Properties to prove:
      1. spec_is_sorted(merge_sort(arr))           -- output is sorted
      2. spec_is_permutation(merge_sort(arr), arr)  -- no elements lost/gained
      3. spec_length_preserved(merge_sort(arr), arr) -- length unchanged
    """
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)
'''

# Tampered version -- deliberately introduces a bug
TAMPERED_CODE = MERGE_SORT_CODE.replace(
    "if left[i] <= right[j]:",
    "if left[i] < right[j]:",  # breaks stability
)


# =====================================================================
#  Theorems -- proved by the REAL JuGeo pipeline
# =====================================================================

@theorem("Merge sort satisfies all 3 specs", code=MERGE_SORT_CODE)
def merge_sort_proof(result):
    """Verifies sorted + permutation + length via real pipeline."""
    assert result.verified, f"Expected verified, got {result.verdict}"
    assert result.H1 == "0", f"Descent failed: H1={result.H1}"
    assert result.propositions_ok == result.propositions_total
    assert result.site.grothendieck_axioms_pass
    assert result.site.trust_algebra_pass


@theorem("Proof-carrying merge sort produces valid certificate")
def carry_proof_lifecycle():
    """Full lifecycle: prove -> certify -> serialize -> deserialize -> reverify."""
    source, cert = carry_proof(MERGE_SORT_CODE)

    # Certificate contains real pipeline results
    assert cert.verdict == "verified"
    assert cert.H1 == "0"
    assert cert.n_coordinates >= 3, \
        f"Expected >=3 coords, got {cert.n_coordinates}"
    assert cert.propositions_ok == cert.propositions_total
    assert cert.grothendieck_ok
    assert cert.trust_algebra_ok

    # Serialize to JSON and back
    json_str = cert.to_json()
    restored = ProofCertificate.from_json(json_str)
    assert restored.verdict == "verified"
    assert restored.H1 == "0"
    assert restored.n_coordinates == cert.n_coordinates
    assert restored.propositions_ok == cert.propositions_ok

    # O(1) re-verification: check code hash + verdict
    assert restored.reverify(source), \
        "Certificate should reverify against original source"


# =====================================================================
#  Empirical checks
# =====================================================================

@check("Tampering detection: modified code fails reverify")
def tamper_detection():
    source, cert = carry_proof(MERGE_SORT_CODE)
    assert not cert.reverify(TAMPERED_CODE), \
        "Certificate should NOT reverify against tampered code"


@check("Certificate JSON roundtrip preserves all fields")
def cert_json_roundtrip():
    _, cert = carry_proof(MERGE_SORT_CODE)
    json_str = cert.to_json()
    restored = ProofCertificate.from_json(json_str)
    assert restored.code_hash == cert.code_hash
    assert restored.verdict == cert.verdict
    assert restored.trust == cert.trust
    assert restored.n_coordinates == cert.n_coordinates
    assert restored.n_morphisms == cert.n_morphisms
    assert restored.propositions_ok == cert.propositions_ok
    assert restored.propositions_total == cert.propositions_total
    assert restored.H1 == cert.H1
    assert restored.grothendieck_ok == cert.grothendieck_ok
    assert restored.trust_algebra_ok == cert.trust_algebra_ok


@check("Site encodes merge sort with meaningful coordinates")
def encode_merge_sort():
    enc = encode(MERGE_SORT_CODE)
    assert enc.n_coordinates >= 3, \
        f"Expected >=3 coordinates, got {enc.n_coordinates}"
    coord_names = list(enc.coordinates.keys())
    assert any("merge" in c.lower() or "sort" in c.lower()
               for c in coord_names), \
        f"Expected merge/sort in coords: {coord_names}"


@check("Certificate has positive timestamp")
def cert_has_timestamp():
    _, cert = carry_proof(MERGE_SORT_CODE)
    assert cert.timestamp > 0, "Certificate should have a positive timestamp"


if __name__ == "__main__":
    run_all("Example 6 -- Proof-Carrying Sort (Real Pipeline)")
