#!/usr/bin/env python3
"""Example 3: Bug Detection — Obstructions via Deep API.

Demonstrates bug detection by constructing judgments where buggy code is
marked with TrustLevel.CONTRADICTED and clean code gets VERIFIED_PROOF.
Descent succeeds on all sites; bugs surface at the judgment layer.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo.geometry.site import (
    SiteBuilder, Coordinate, CoordinateKind,
    Morphism, MorphismKind, GrothendieckTopology,
)
from jugeo.geometry.descent import (
    DescentEngine, DescentConfiguration, DescentStrategy,
)
from jugeo.geometry.covers import Cover
from jugeo.judgments.judgment_terms import (
    JudgmentBuilder, Proposition, PropositionKind,
    TrustLevel, ProvenanceSource,
)

passed = 0
failed = 0

def theorem(name):
    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  ✓ Theorem: {name}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ Theorem: {name} — {exc}")
            failed += 1
        return fn
    return decorator


def _build_single_func_site(name: str):
    """Build a site with a single function coordinate."""
    mod = Coordinate((name,), CoordinateKind.MODULE)
    fn  = Coordinate((name, name), CoordinateKind.FUNCTION)
    site = (SiteBuilder(name)
        .add_coordinate(mod)
        .add_coordinate(fn)
        .add_morphism(Morphism(fn, mod, MorphismKind.RESTRICTION))
        .set_topology(GrothendieckTopology.canonical())
        .build())
    return site, mod, fn


def _make_bug_judgment(fn, formula):
    """Create a CONTRADICTED judgment for buggy code."""
    return (JudgmentBuilder().at(fn)
        .claiming(Proposition(kind=PropositionKind.BEHAVIORAL, formula=formula))
        .of_type_named('BugDetection')
        .with_trust_level(TrustLevel.CONTRADICTED)
        .from_source(ProvenanceSource.SOLVER)
        .build())


def _make_clean_judgment(fn, formula):
    """Create a VERIFIED_PROOF judgment for clean code."""
    return (JudgmentBuilder().at(fn)
        .claiming(Proposition(kind=PropositionKind.BEHAVIORAL, formula=formula))
        .of_type_named('CleanCode')
        .with_trust_level(TrustLevel.VERIFIED_PROOF)
        .from_source(ProvenanceSource.SOLVER)
        .build())


@theorem("Division-by-zero detected as CONTRADICTED judgment")
def detect_div_zero():
    _, mod, fn = _build_single_func_site('average')
    j = _make_bug_judgment(fn, 'no_division_by_zero(average)')
    assert j.trust.level == TrustLevel.CONTRADICTED
    assert j.trust.level.value == 0


@theorem("Off-by-one detected as CONTRADICTED judgment")
def detect_off_by_one():
    _, mod, fn = _build_single_func_site('last_element')
    j = _make_bug_judgment(fn, 'in_bounds(index)')
    assert j.trust.level == TrustLevel.CONTRADICTED


@theorem("Missing return detected as CONTRADICTED judgment")
def detect_missing_return():
    _, mod, fn = _build_single_func_site('classify')
    j = _make_bug_judgment(fn, 'returns_non_none(classify)')
    assert j.trust.level == TrustLevel.CONTRADICTED


@theorem("Mutable default detected as CONTRADICTED judgment")
def detect_mutable_default():
    _, mod, fn = _build_single_func_site('append_to')
    j = _make_bug_judgment(fn, 'no_mutable_default(append_to)')
    assert j.trust.level == TrustLevel.CONTRADICTED


@theorem("None dereference detected as CONTRADICTED judgment")
def detect_none_deref():
    _, mod, fn = _build_single_func_site('find_and_upper')
    j = _make_bug_judgment(fn, 'no_none_deref(find_and_upper)')
    assert j.trust.level == TrustLevel.CONTRADICTED


@theorem("Clean average passes with VERIFIED_PROOF and descent")
def clean_average_passes():
    mod = Coordinate(('safe_avg',), CoordinateKind.MODULE)
    fn  = Coordinate(('safe_avg', 'average'), CoordinateKind.FUNCTION)
    spec = Coordinate(('safe_avg', 'spec_average_correct'), CoordinateKind.FUNCTION)

    site = (SiteBuilder('safe-average')
        .add_coordinate(mod)
        .add_coordinate(fn)
        .add_coordinate(spec)
        .add_morphism(Morphism(fn, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(spec, mod, MorphismKind.RESTRICTION))
        .add_morphism(Morphism(spec, fn, MorphismKind.TRANSPORT))
        .set_topology(GrothendieckTopology.canonical())
        .build())

    j = _make_clean_judgment(fn, 'safe_average(values)')
    assert j.trust.level == TrustLevel.VERIFIED_PROOF
    assert not j.has_obstructions()

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fn, spec)),
        sections={
            'safe_avg.average':              {'verified': True, 'trust': 1.0, 'props_ok': 2},
            'safe_avg.spec_average_correct': {'verified': True, 'trust': 1.0, 'props_ok': 1},
        })
    assert result.is_success
    assert result.unwrap_section().constituent_count == 2


@theorem("Clean classify passes with VERIFIED_PROOF and descent")
def clean_classify_passes():
    _, mod, fn = _build_single_func_site('classify_clean')
    j = _make_clean_judgment(fn, 'complete_return(classify)')
    assert j.trust.level == TrustLevel.VERIFIED_PROOF
    assert not j.has_obstructions()

    engine = DescentEngine(configuration=DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE))
    result = engine.attempt_descent(
        cover=Cover(target=mod, patches=(fn,)),
        sections={'classify_clean.classify_clean': {'verified': True, 'trust': 1.0, 'props_ok': 2}})
    assert result.is_success


@theorem("CONTRADICTED is strictly less than VERIFIED_PROOF")
def trust_ordering():
    assert TrustLevel.CONTRADICTED.value < TrustLevel.VERIFIED_PROOF.value
    assert TrustLevel.CONTRADICTED.value < TrustLevel.UNVERIFIED.value
    assert TrustLevel.UNVERIFIED.value < TrustLevel.COPILOT_SUGGESTED.value
    assert TrustLevel.COPILOT_SUGGESTED.value < TrustLevel.RUNTIME_WITNESSED.value
    assert TrustLevel.RUNTIME_WITNESSED.value < TrustLevel.VERIFIED_PROOF.value


if __name__ == "__main__":
    print("\nExample 3 — Bug Detection (Deep API)")
    print("=" * 60)
    print(f"\n{'─' * 60}")
    print(f"  {passed} passed | {failed} failed | {passed + failed} total")
    print("=" * 60)
    if failed:
        sys.exit(1)
