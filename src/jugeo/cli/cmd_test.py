"""CLI subcommand handler for ``jugeo test [suite]``.

Runs judgment-geometric benchmarks by constructing real Sites, Judgments,
DescentEngines, and TrustAlgebra objects and verifying their properties.

Test suites: site, descent, trust, judgment, encode, bugs, spec, equiv,
unit, all.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_log = logging.getLogger(__name__)

_VALID_SUITES = ("all", "site", "descent", "trust", "judgment", "encode",
                 "bugs", "spec", "equiv", "unit")

# -- geometry imports (all optional) -----------------------------------------
try:
    from jugeo.geometry.site import (
        Site, SiteBuilder, Coordinate, CoordinateKind,
        SiteSerializer, GrothendieckTopology,
    )
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False

try:
    from jugeo.geometry.covers import Cover, score_cover
    _HAS_COVERS = True
except Exception:
    _HAS_COVERS = False

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentConfiguration, DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

# -- judgment imports --------------------------------------------------------
try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentBuilder, TrustLevel, Proposition, PropositionKind,
        Carrier, TrustAnnotation, JudgmentStatus, ProvenanceSource,
        EvidenceItemKind, ResidualObligation, Obstruction,
    )
    _HAS_JUDGMENTS = True
except Exception:
    _HAS_JUDGMENTS = False

# -- trust algebra -----------------------------------------------------------
try:
    from jugeo.evidence.trust import TrustLevel as ETrustLevel, TrustAlgebra
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False


# ======================================================================
# Data structures
# ======================================================================

@dataclass
class _TestCase:
    name: str
    passed: bool = False
    error: str = ""
    elapsed_s: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

@dataclass
class _SuiteResult:
    name: str
    cases: list[_TestCase] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class _TestReport:
    suites: list[_SuiteResult] = field(default_factory=list)
    total_elapsed_s: float = 0.0
    overall_passed: bool = True


def _run_case(result: _SuiteResult, name: str,
              fn: Callable[[], dict[str, Any] | None]) -> None:
    """Run a single test case, updating *result* in place."""
    tc = _TestCase(name=name)
    t = time.monotonic()
    try:
        details = fn()
        tc.passed = True
        tc.details = details or {}
        result.passed += 1
    except Exception as exc:
        tc.error = str(exc)
        result.failed += 1
    tc.elapsed_s = time.monotonic() - t
    result.cases.append(tc)


# ======================================================================
# Test suite: site
# ======================================================================

def _run_site_suite(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="site")
    t0 = time.monotonic()
    if not _HAS_SITE:
        result.errors = 1
        result.details.append({"error": "geometry.site unavailable"})
        result.elapsed_s = time.monotonic() - t0
        return result

    def tc_build() -> dict[str, Any]:
        builder = SiteBuilder("test-site")
        for n, k in [("mod_a", CoordinateKind.MODULE),
                      ("fn_b", CoordinateKind.FUNCTION),
                      ("ifc_c", CoordinateKind.INTERFACE)]:
            builder.add_coordinate(Coordinate(n, kind=k))
        site = builder.build()
        assert site.coordinate_count() == 3
        return {"coordinates": site.coordinate_count(), "label": site.label}

    def tc_topology() -> dict[str, Any]:
        topo = GrothendieckTopology.canonical()
        b = SiteBuilder("topo-test").add_coordinate(
            Coordinate("x", kind=CoordinateKind.REGION))
        b.set_topology(topo)
        site = b.build()
        assert site.topology is not None and site.topology.name == "canonical"
        return {"topology": site.topology.name}

    def tc_roundtrip() -> dict[str, Any]:
        b = SiteBuilder("rt")
        b.add_coordinate(Coordinate("a", kind=CoordinateKind.FUNCTION))
        b.add_coordinate(Coordinate("b", kind=CoordinateKind.MODULE))
        site = b.build()
        data = SiteSerializer.site_to_json(site)
        restored = SiteSerializer.site_from_json(data)
        assert restored.coordinate_count() == site.coordinate_count()
        return {"json_keys": list(data.keys()) if isinstance(data, dict) else []}

    def tc_coord_props() -> dict[str, Any]:
        c = Coordinate("parent.child", kind=CoordinateKind.FUNCTION,
                        metadata={"lineno": 42})
        assert c.kind == CoordinateKind.FUNCTION
        s = c.serialize()
        assert s is not None
        return {"serialized_type": type(s).__name__}

    _run_case(result, "build_basic_site", tc_build)
    _run_case(result, "canonical_topology", tc_topology)
    _run_case(result, "serialization_roundtrip", tc_roundtrip)
    _run_case(result, "coordinate_properties", tc_coord_props)

    if _HAS_COVERS:
        def tc_cover() -> dict[str, Any]:
            cover = Cover(target=Coordinate("tgt", kind=CoordinateKind.MODULE))
            m = score_cover(cover)
            assert hasattr(m, "total_score")
            return {"total_score": m.total_score}
        _run_case(result, "cover_scoring", tc_cover)

    result.elapsed_s = time.monotonic() - t0
    return result


# ======================================================================
# Test suite: descent
# ======================================================================

def _run_descent_suite(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="descent")
    t0 = time.monotonic()
    if not (_HAS_DESCENT and _HAS_SITE):
        result.errors = 1
        result.details.append({"error": "descent/site unavailable"})
        result.elapsed_s = time.monotonic() - t0
        return result

    def tc_config() -> dict[str, Any]:
        cfg = DescentConfiguration(strategy=DescentStrategy.EXHAUSTIVE, depth_limit=5)
        assert cfg.strategy == DescentStrategy.EXHAUSTIVE and cfg.depth_limit == 5
        return {"summary": cfg.summary()}

    def tc_engine() -> dict[str, Any]:
        cfg = DescentConfiguration(strategy=DescentStrategy.ITERATIVE)
        eng = DescentEngine(configuration=cfg)
        assert eng.configuration.strategy == DescentStrategy.ITERATIVE
        return {}

    def tc_strategies() -> dict[str, Any]:
        s = list(DescentStrategy)
        assert len(s) >= 3
        return {"strategies": [x.value for x in s]}

    _run_case(result, "config_creation", tc_config)
    _run_case(result, "engine_instantiation", tc_engine)
    _run_case(result, "strategy_enumeration", tc_strategies)

    if _HAS_COVERS:
        def tc_descent_cover() -> dict[str, Any]:
            cover = Cover(target=Coordinate("m", kind=CoordinateKind.MODULE))
            eng = DescentEngine(configuration=DescentConfiguration(depth_limit=2))
            dr = eng.attempt_descent(cover, {})
            return {"is_success": dr.is_success}
        _run_case(result, "descent_with_cover", tc_descent_cover)

    result.elapsed_s = time.monotonic() - t0
    return result


# ======================================================================
# Test suite: trust
# ======================================================================

def _run_trust_suite(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="trust")
    t0 = time.monotonic()
    if not _HAS_TRUST:
        result.errors = 1
        result.details.append({"error": "evidence.trust unavailable"})
        result.elapsed_s = time.monotonic() - t0
        return result

    alg = TrustAlgebra()

    def tc_bounded() -> dict[str, Any]:
        assert alg.bottom() == ETrustLevel.CONTRADICTED
        assert alg.top() == ETrustLevel.MECHANICALLY_VERIFIED
        return {"bottom": alg.bottom().value, "top": alg.top().value}

    def tc_meet_glb() -> dict[str, Any]:
        m = alg.meet(ETrustLevel.SOLVER_DISCHARGED, ETrustLevel.RUNTIME_WITNESSED)
        assert m <= ETrustLevel.SOLVER_DISCHARGED and m <= ETrustLevel.RUNTIME_WITNESSED
        return {"meet": m.value}

    def tc_join_lub() -> dict[str, Any]:
        j = alg.join(ETrustLevel.COPILOT_SUGGESTED, ETrustLevel.RUNTIME_WITNESSED)
        assert j >= ETrustLevel.COPILOT_SUGGESTED and j >= ETrustLevel.RUNTIME_WITNESSED
        return {"join": j.value}

    def tc_idempotent() -> dict[str, Any]:
        for lvl in ETrustLevel:
            assert alg.meet(lvl, lvl) == lvl
        return {}

    def tc_commutative() -> dict[str, Any]:
        pairs = [(ETrustLevel.SOLVER_DISCHARGED, ETrustLevel.ORACLE_PROPOSED),
                 (ETrustLevel.RUNTIME_WITNESSED, ETrustLevel.COPILOT_SUGGESTED)]
        for a, b in pairs:
            assert alg.meet(a, b) == alg.meet(b, a)
        return {}

    def tc_compose() -> dict[str, Any]:
        c = alg.compose(ETrustLevel.SOLVER_DISCHARGED, ETrustLevel.ORACLE_PROPOSED)
        assert isinstance(c, ETrustLevel)
        return {"composed": c.value}

    def tc_bottom_absorbs() -> dict[str, Any]:
        bot = alg.bottom()
        for lvl in ETrustLevel:
            assert alg.meet(bot, lvl) == bot
        return {}

    def tc_ranking() -> dict[str, Any]:
        assert ETrustLevel.CONTRADICTED.rank_index() < ETrustLevel.UNVERIFIED.rank_index()
        assert ETrustLevel.UNVERIFIED.rank_index() < ETrustLevel.SOLVER_DISCHARGED.rank_index()
        return {}

    for name, fn in [("bounded_lattice", tc_bounded), ("meet_glb", tc_meet_glb),
                     ("join_lub", tc_join_lub), ("meet_idempotent", tc_idempotent),
                     ("meet_commutative", tc_commutative), ("compose", tc_compose),
                     ("bottom_absorbs_meet", tc_bottom_absorbs),
                     ("rank_ordering", tc_ranking)]:
        _run_case(result, name, fn)

    result.elapsed_s = time.monotonic() - t0
    return result


# ======================================================================
# Test suite: judgment
# ======================================================================

def _run_judgment_suite(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="judgment")
    t0 = time.monotonic()
    if not (_HAS_JUDGMENTS and _HAS_SITE):
        result.errors = 1
        result.details.append({"error": "judgments/site unavailable"})
        result.elapsed_s = time.monotonic() - t0
        return result

    def tc_proposition() -> dict[str, Any]:
        p = Proposition(kind=PropositionKind.STRUCTURAL, formula="f(x) > 0",
                        free_variables=("x",))
        assert not p.is_closed()
        sub = p.substitute({"x": "42"})
        assert sub is not None
        neg = p.negate()
        assert neg is not None
        return {"negation": neg.formula}

    def tc_builder() -> dict[str, Any]:
        j = (JudgmentBuilder()
             .at(Coordinate("fn", kind=CoordinateKind.FUNCTION))
             .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                                   formula="returns_int(fn)"))
             .of_type_named("Integer")
             .with_trust_level(TrustLevel.SOLVER_DISCHARGED)
             .from_source(ProvenanceSource.SOLVER)
             .with_status(JudgmentStatus.SETTLED)
             .build())
        assert j.status == JudgmentStatus.SETTLED
        return {"status": j.status.value}

    def tc_obligations() -> dict[str, Any]:
        obl = ResidualObligation(description="verify termination",
                                  required_evidence_kind=EvidenceItemKind.SOLVER_PROOF)
        obs = Obstruction(violated_condition="precondition",
                          description="may fail on negative input", severity=2)
        j = (JudgmentBuilder()
             .at(Coordinate("chk", kind=CoordinateKind.FUNCTION))
             .claiming(Proposition(kind=PropositionKind.BEHAVIORAL,
                                   formula="safe(chk)"))
             .of_type_named("Boolean")
             .with_obligation(obl).with_obstruction(obs)
             .with_trust_level(TrustLevel.COPILOT_SUGGESTED)
             .from_source(ProvenanceSource.ORACLE).build())
        assert j.has_residuals() and j.has_obstructions()
        return {"obligations": j.pending_obligation_count(),
                "obstructions": j.unresolved_obstruction_count()}

    def tc_serialization() -> dict[str, Any]:
        j = (JudgmentBuilder()
             .at(Coordinate("sf", kind=CoordinateKind.FUNCTION))
             .claiming(Proposition(kind=PropositionKind.STRUCTURAL,
                                   formula="typed(sf)"))
             .of_type_named("Any")
             .with_trust_level(TrustLevel.RUNTIME_WITNESSED).build())
        m = j.serialize()
        assert isinstance(m, dict)
        return {"keys": list(m.keys())[:5]}

    def tc_trust_annotation() -> dict[str, Any]:
        ta = TrustAnnotation(level=TrustLevel.COPILOT_SUGGESTED)
        up = ta.promote(reason="solver confirmed")
        dn = ta.demote(reason="evidence expired")
        assert up.level.value >= ta.level.value
        assert dn.level.value <= ta.level.value
        return {"original": ta.level.value, "promoted": up.level.value}

    def tc_carrier() -> dict[str, Any]:
        c = Carrier(name="Integer", parameters=("n",))
        r = c.refine("Positive")
        assert r.name != c.name or "Positive" in r.name
        return {}

    for name, fn in [("proposition_creation", tc_proposition),
                     ("judgment_builder", tc_builder),
                     ("obligations_obstructions", tc_obligations),
                     ("judgment_serialization", tc_serialization),
                     ("trust_annotation_ops", tc_trust_annotation),
                     ("carrier_ops", tc_carrier)]:
        _run_case(result, name, fn)

    result.elapsed_s = time.monotonic() - t0
    return result


# ======================================================================
# Test suite: encode
# ======================================================================

def _run_encode_suite(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="encode")
    t0 = time.monotonic()

    def tc_import() -> dict[str, Any]:
        from jugeo.cli.cmd_encode import run_encode  # noqa: F401
        return {}

    _run_case(result, "encode_import", tc_import)

    if _HAS_SITE:
        def tc_site_json() -> dict[str, Any]:
            b = SiteBuilder("enc")
            b.add_coordinate(Coordinate("f", kind=CoordinateKind.FUNCTION))
            site = b.build()
            data = SiteSerializer.site_to_json(site)
            s = json.dumps(data, default=str)
            assert len(s) > 0
            return {"json_size": len(s)}

        def tc_coord_serial() -> dict[str, Any]:
            c = Coordinate("t.c", kind=CoordinateKind.THEOREM,
                            metadata={"provenance": "test"})
            s = c.serialize()
            restored = Coordinate.parse(s)
            assert restored.kind == c.kind
            return {}

        _run_case(result, "site_json_encoding", tc_site_json)
        _run_case(result, "coordinate_serialization", tc_coord_serial)

    result.elapsed_s = time.monotonic() - t0
    return result


# ======================================================================
# Legacy benchmark / unit test fallbacks
# ======================================================================

def _run_benchmark_subprocess(name: str, verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name=name)
    t0 = time.monotonic()
    cmd = [sys.executable, "-m", "jugeo", name, "--format", "json"]
    if verbose:
        cmd.append("--verbose")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        result.elapsed_s = time.monotonic() - t0
        if proc.returncode == 0:
            result.passed = 1
        else:
            result.failed = 1
            if proc.stderr:
                result.details.append({"stderr": proc.stderr[:500]})
    except subprocess.TimeoutExpired:
        result.elapsed_s = time.monotonic() - t0
        result.errors, result.details = 1, [{"error": "timeout"}]
    except Exception as exc:
        result.elapsed_s = time.monotonic() - t0
        result.errors, result.details = 1, [{"error": str(exc)}]
    return result


def _run_unit_tests(verbose: bool) -> _SuiteResult:
    result = _SuiteResult(name="unit")
    t0 = time.monotonic()
    for runner in ([sys.executable, "-m", "pytest", "--tb=short", "-q"],
                   [sys.executable, "-m", "unittest", "discover", "-s", "tests"]):
        try:
            proc = subprocess.run(runner, capture_output=True, text=True, timeout=180)
            result.elapsed_s = time.monotonic() - t0
            result.passed = 1 if proc.returncode == 0 else 0
            result.failed = 0 if proc.returncode == 0 else 1
            return result
        except Exception:
            continue
    result.elapsed_s = time.monotonic() - t0
    result.errors = 1
    result.details.append({"error": "No test runner available"})
    return result


# ======================================================================
# Formatting
# ======================================================================

def _format_text(report: _TestReport) -> str:
    lines = ["JuGeo Test Report", "=" * 60]
    for s in report.suites:
        total = s.passed + s.failed + s.errors
        ok = s.failed == 0 and s.errors == 0
        tag = "\u2713 PASS" if ok else "\u2717 FAIL"
        lines.append(f"\n  [{tag}] {s.name}  "
                     f"({s.passed}/{total} passed, {s.elapsed_s:.2f}s)")
        for tc in s.cases:
            m = "\u2713" if tc.passed else "\u2717"
            line = f"      {m} {tc.name}  ({tc.elapsed_s:.3f}s)"
            if tc.error:
                line += f"  ERROR: {tc.error[:80]}"
            lines.append(line)
    lines.append(f"\nTotal: {report.total_elapsed_s:.2f}s")
    lines.append("\u2713 ALL PASSED" if report.overall_passed else "\u2717 FAILURES DETECTED")
    return "\n".join(lines)


def _format_json(report: _TestReport) -> str:
    return json.dumps({
        "overall_passed": report.overall_passed,
        "total_elapsed_s": report.total_elapsed_s,
        "suites": [{
            "name": s.name, "passed": s.passed, "failed": s.failed,
            "errors": s.errors, "elapsed_s": s.elapsed_s,
            "cases": [{"name": c.name, "passed": c.passed, "error": c.error,
                        "elapsed_s": c.elapsed_s, "details": c.details}
                       for c in s.cases],
        } for s in report.suites],
    }, indent=2, default=str)


# ======================================================================
# Registry
# ======================================================================


def _test_registry() -> dict[str, type]:
    """Return a dict of all public classes from benchmark subpackages."""
    registry: dict[str, type] = {}

    try:
        from jugeo.benchmarks.models import (  # type: ignore[import-untyped]
            InputPoint, EquivalenceCase, SpecCase, BugCase, Witness,
            ResidualObligation, MetricSummary, BenchmarkJudgment,
            BenchmarkReport, BenchmarkBundle, JudgmentBenchmarkCase,
            DescentBenchmarkCase, EncodingBenchmarkCase,
        )
        registry["InputPoint"] = InputPoint
        registry["EquivalenceCase"] = EquivalenceCase
        registry["SpecCase"] = SpecCase
        registry["BugCase"] = BugCase
        registry["Witness"] = Witness
        registry["ResidualObligation"] = ResidualObligation
        registry["MetricSummary"] = MetricSummary
        registry["BenchmarkJudgment"] = BenchmarkJudgment
        registry["BenchmarkReport"] = BenchmarkReport
        registry["BenchmarkBundle"] = BenchmarkBundle
        registry["JudgmentBenchmarkCase"] = JudgmentBenchmarkCase
        registry["DescentBenchmarkCase"] = DescentBenchmarkCase
        registry["EncodingBenchmarkCase"] = EncodingBenchmarkCase
    except Exception:
        pass

    try:
        from jugeo.benchmarks.semantics import (  # type: ignore[import-untyped]
            ExecutionOutcome, BugObservation, BugDetector,
        )
        registry["ExecutionOutcome"] = ExecutionOutcome
        registry["BugObservation"] = BugObservation
        registry["BugDetector"] = BugDetector
    except Exception:
        pass

    return registry


# ======================================================================
# Entry point
# ======================================================================

def run_test(args: argparse.Namespace) -> int:
    """Run benchmark suites and/or unit tests.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``suite``   -- which suite to run (see _VALID_SUITES)
        - ``format``  -- output format (``"text"`` or ``"json"``)
        - ``verbose`` -- enable debug logging

    Returns
    -------
    int
        0 if all suites pass, 1 if any fail.
    """
    suite: str = getattr(args, "suite", "all")
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _test_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    if suite not in _VALID_SUITES:
        print(f"error: unknown suite '{suite}' "
              f"(valid: {', '.join(_VALID_SUITES)})", file=sys.stderr)
        return 2

    report = _TestReport()
    t0 = time.monotonic()

    _SUITE_MAP: dict[str, Callable[[bool], _SuiteResult]] = {
        "site": _run_site_suite, "descent": _run_descent_suite,
        "trust": _run_trust_suite, "judgment": _run_judgment_suite,
        "encode": _run_encode_suite,
    }

    if suite == "all":
        for fn in _SUITE_MAP.values():
            report.suites.append(fn(verbose))
        for bm in ("bugs", "spec", "equiv"):
            report.suites.append(_run_benchmark_subprocess(bm, verbose))
        report.suites.append(_run_unit_tests(verbose))
    elif suite in _SUITE_MAP:
        report.suites.append(_SUITE_MAP[suite](verbose))
    elif suite in ("bugs", "spec", "equiv"):
        report.suites.append(_run_benchmark_subprocess(suite, verbose))
    elif suite == "unit":
        report.suites.append(_run_unit_tests(verbose))

    report.total_elapsed_s = time.monotonic() - t0
    report.overall_passed = all(
        sr.failed == 0 and sr.errors == 0 for sr in report.suites
    )

    print(_format_json(report) if out_format == "json" else _format_text(report))
    return 0 if report.overall_passed else 1
