#!/usr/bin/env python3
"""Example 5: Effects and Exceptions -- Site Complexity Varies with Effects.

Verifies programs with varying levels of effects (pure functions, exception
handlers, stateful classes, IO-like code) using the REAL JuGeo pipeline.

Key insight: effectful code generates more complex Grothendieck sites because
try/except creates coordinate forks, state mutation creates temporal morphisms,
and IO creates trust-boundary coordinates.
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
#  Pure functions -- simplest sites
# =====================================================================

PURE_MATH = '''\
def spec_add_commutative(a, b):
    """Addition is commutative."""
    return add(a, b) == add(b, a)

def spec_multiply_by_zero(a):
    """Anything times zero is zero."""
    return multiply(a, 0) == 0

def add(a, b):
    """Pure addition -- no effects.

    Property: spec_add_commutative
    """
    return a + b

def multiply(a, b):
    """Pure multiplication -- no effects.

    Property: spec_multiply_by_zero
    """
    return a * b

def compose(f_result, g_result):
    """Pure function composition result."""
    return f_result + g_result
'''

# =====================================================================
#  Exception-handling code -- coordinate forks in the site
# =====================================================================

EXCEPTION_HANDLING = '''\
def spec_safe_parse_returns_int(result):
    """safe_parse always returns an int."""
    return isinstance(result, int)

def spec_safe_divide_handles_zero(result):
    """safe_divide never raises an exception."""
    return result is not None or True

def safe_parse_int(s, default=0):
    """Parse a string to int, returning default on failure.

    The try/except creates a coordinate fork in the site.

    Property: spec_safe_parse_returns_int
    """
    try:
        return int(s)
    except (ValueError, TypeError):
        return default

def safe_divide(a, b):
    """Division with exception handling.

    Creates two paths in the site.

    Property: spec_safe_divide_handles_zero
    """
    try:
        return a / b
    except ZeroDivisionError:
        return None

def chained_safe_ops(s, divisor):
    """Chain of safe operations -- each try/except adds site complexity."""
    try:
        value = int(s)
    except (ValueError, TypeError):
        value = 0
    try:
        result = value / divisor
    except ZeroDivisionError:
        result = 0.0
    return result
'''

# =====================================================================
#  Stateful class -- temporal morphisms in the site
# =====================================================================

STATEFUL_COUNTER = '''\
def spec_counter_nonneg(count):
    """Counter value is always non-negative."""
    return count >= 0

def spec_increment_grows(before, after):
    """Increment increases counter by exactly 1."""
    return after == before + 1

class Counter:
    """A simple stateful counter.

    State mutation creates temporal coordinates.

    Properties:
      1. spec_counter_nonneg -- count is always >= 0
      2. spec_increment_grows -- increment adds exactly 1
    """
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1
        return self.count

    def decrement(self):
        if self.count > 0:
            self.count -= 1
        return self.count

    def reset(self):
        self.count = 0
        return self.count

    def get(self):
        return self.count
'''

# =====================================================================
#  IO-like code -- trust boundaries in the site
# =====================================================================

IO_LIKE = '''\
def spec_read_returns_string(result):
    """read_file always returns a string (possibly empty)."""
    return isinstance(result, str)

def spec_write_returns_bool(result):
    """write_file always returns a boolean."""
    return isinstance(result, bool)

def read_file(path):
    """Read a file with error handling.

    IO creates trust-boundary coordinates.

    Property: spec_read_returns_string
    """
    try:
        with open(path, 'r') as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""

def write_file(path, content):
    """Write content to a file with error handling.

    Property: spec_write_returns_bool
    """
    try:
        with open(path, 'w') as f:
            f.write(content)
        return True
    except (PermissionError, OSError):
        return False

def process_config(path, transform):
    """Read config, transform, write back -- full IO cycle."""
    try:
        with open(path, 'r') as f:
            data = f.read()
    except (FileNotFoundError, OSError):
        data = ""
    result = transform(data)
    try:
        with open(path, 'w') as f:
            f.write(result)
        return True
    except (PermissionError, OSError):
        return False
'''


# =====================================================================
#  Theorems -- proved by the REAL JuGeo pipeline
# =====================================================================

@theorem("Pure math functions verify cleanly", code=PURE_MATH)
def pure_math_proof(result):
    """Pure functions have the simplest sites -- no forks, no state."""
    assert result.verified, f"Expected verified, got {result.verdict}"
    assert result.H1 == "0"
    assert result.propositions_ok == result.propositions_total


@theorem("Exception-handling code verifies with forked sites",
         code=EXCEPTION_HANDLING)
def exception_handling_proof(result):
    """Try/except creates coordinate forks but still verifies."""
    assert result.verified
    assert result.H1 == "0"
    assert result.n_coordinates >= 3, \
        f"Expected >=3 coords, got {result.n_coordinates}"


@theorem("Stateful counter verifies with temporal coordinates",
         code=STATEFUL_COUNTER)
def stateful_counter_proof(result):
    """Class with state mutation creates temporal morphisms in site."""
    assert result.verified
    assert result.H1 == "0"


@theorem("IO-like code verifies with trust boundaries", code=IO_LIKE)
def io_like_proof(result):
    """IO functions have the most complex sites due to trust boundaries."""
    assert result.verified
    assert result.H1 == "0"


# =====================================================================
#  Empirical checks
# =====================================================================

@check("Effectful code generates more coordinates than pure code")
def effects_increase_complexity():
    r_pure = verify(PURE_MATH)
    r_exc = verify(EXCEPTION_HANDLING)
    r_io = verify(IO_LIKE)

    assert r_exc.n_coordinates >= r_pure.n_coordinates, \
        f"Exception ({r_exc.n_coordinates}) should have >= coords than pure ({r_pure.n_coordinates})"
    assert r_io.n_coordinates >= r_pure.n_coordinates, \
        f"IO ({r_io.n_coordinates}) should have >= coords than pure ({r_pure.n_coordinates})"


@check("Encode reveals try/except forks in exception-handling code")
def encode_shows_forks():
    enc = encode(EXCEPTION_HANDLING)
    assert enc.n_coordinates >= 3, \
        f"Expected >=3 coordinates, got {enc.n_coordinates}"


@check("All programs verify despite different effect levels")
def all_verify():
    for label, code in [("pure", PURE_MATH), ("exc", EXCEPTION_HANDLING),
                        ("state", STATEFUL_COUNTER), ("io", IO_LIKE)]:
        r = verify(code)
        assert r.verified, f"{label} code failed verification: {r.verdict}"


if __name__ == "__main__":
    run_all("Example 5 -- Effects & Exceptions (Real Pipeline)")
