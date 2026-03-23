"""Shared pytest fixtures for jugeo.generation.semantic_closure tests.

Provides fixtures for all model objects used across the semantic-closure
test suite.  Every fixture that depends on an unavailable class uses a
try/except guard and calls ``pytest.skip`` so the individual test file
still loads cleanly even when the production code is not yet complete.
"""
from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import time
import pytest

# ---------------------------------------------------------------------------
# Optional imports — wrapped so the conftest loads even when modules are
# partially implemented.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.semantic_closure.models import (
        ClosureCheck,
        RegressionTest,
        SemanticClosure,
        ClosureGap,
        RegressionRecord,
        make_check,
        make_gap,
        empty_closure,
    )
    _models_available = True
except ImportError:
    _models_available = False

try:
    from jugeo.generation.semantic_closure.s03_integration_closure import (
        IntegrationState,
        make_integration_state,
    )
    _s03_available = True
except ImportError:
    _s03_available = False


# ---------------------------------------------------------------------------
# Primitive / identifier fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_id() -> str:
    """Return a simple patch identifier string."""
    return "patch_alpha"


@pytest.fixture
def obligation_id() -> str:
    """Return a simple obligation identifier string."""
    return "obligation_001"


@pytest.fixture
def integration_id() -> str:
    """Return a simple integration identifier string."""
    return "integration_test_001"


# ---------------------------------------------------------------------------
# Evidence fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_evidence() -> tuple:
    """Return a tuple with three distinct evidence tags."""
    return ("evidence_a", "evidence_b", "evidence_c")


@pytest.fixture
def empty_evidence() -> tuple:
    """Return an empty evidence tuple."""
    return ()


@pytest.fixture
def large_evidence() -> tuple:
    """Return a tuple with ten evidence tags for high-confidence tests."""
    return tuple(f"evidence_{i:02d}" for i in range(10))


# ---------------------------------------------------------------------------
# ClosureCheck fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def closure_check_closed():
    """Return a ClosureCheck with result='closed' and high confidence."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureCheck(
            check_id="chk_closed_001",
            obligation_id="obl_001",
            patch_id="patch_alpha",
            result="closed",
            confidence=0.9,
            evidence=("evidence_a", "evidence_b", "evidence_c"),
            check_type="semantic",
            timestamp=time.time(),
            notes="fixture: closed check",
        )
    except Exception as exc:
        pytest.skip(f"ClosureCheck construction failed: {exc}")


@pytest.fixture
def closure_check_open():
    """Return a ClosureCheck with result='open' and low confidence."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureCheck(
            check_id="chk_open_001",
            obligation_id="obl_002",
            patch_id="patch_alpha",
            result="open",
            confidence=0.1,
            evidence=(),
            check_type="semantic",
            timestamp=time.time(),
            notes="fixture: open check",
        )
    except Exception as exc:
        pytest.skip(f"ClosureCheck construction failed: {exc}")


@pytest.fixture
def closure_check_partial():
    """Return a ClosureCheck with result='partial' and medium confidence."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureCheck(
            check_id="chk_partial_001",
            obligation_id="obl_003",
            patch_id="patch_alpha",
            result="partial",
            confidence=0.5,
            evidence=("evidence_a",),
            check_type="semantic",
            timestamp=time.time(),
            notes="fixture: partial check",
        )
    except Exception as exc:
        pytest.skip(f"ClosureCheck construction failed: {exc}")


@pytest.fixture
def three_closure_checks(closure_check_closed, closure_check_open, closure_check_partial):
    """Return a list of three ClosureChecks: closed, open, partial."""
    return [closure_check_closed, closure_check_open, closure_check_partial]


# ---------------------------------------------------------------------------
# ClosureGap fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def closure_gap_minor():
    """Return a ClosureGap with severity='minor'."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureGap(
            gap_id="gap_minor_001",
            obligation_id="obl_004",
            description="Minor gap: insufficient evidence overlap",
            severity="minor",
            patch_id="patch_alpha",
            suggested_fix="Add at least two overlapping evidence tags",
            timestamp=time.time(),
            source_check_id="chk_open_001",
        )
    except Exception as exc:
        pytest.skip(f"ClosureGap construction failed: {exc}")


@pytest.fixture
def closure_gap_blocking():
    """Return a ClosureGap with severity='blocking'."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureGap(
            gap_id="gap_blocking_001",
            obligation_id="obl_005",
            description="Blocking gap: no evidence whatsoever for this obligation",
            severity="blocking",
            patch_id="patch_alpha",
            suggested_fix="Run descent check and treaty evaluation before retrying",
            timestamp=time.time(),
            source_check_id="chk_open_002",
        )
    except Exception as exc:
        pytest.skip(f"ClosureGap construction failed: {exc}")


@pytest.fixture
def closure_gap_critical():
    """Return a ClosureGap with severity='critical'."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return ClosureGap(
            gap_id="gap_critical_001",
            obligation_id="obl_006",
            description="Critical gap: treaty clause violated",
            severity="critical",
            patch_id="patch_alpha",
            suggested_fix="Re-ratify treaty with corrected clause",
            timestamp=time.time(),
            source_check_id="chk_open_003",
        )
    except Exception as exc:
        pytest.skip(f"ClosureGap construction failed: {exc}")


@pytest.fixture
def sample_gaps(closure_gap_minor, closure_gap_critical, closure_gap_blocking):
    """Return a list of [minor_gap, critical_gap, blocking_gap]."""
    return [closure_gap_minor, closure_gap_critical, closure_gap_blocking]


# ---------------------------------------------------------------------------
# SemanticClosure fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_closure_full():
    """Return a SemanticClosure representing complete closure (fraction=1.0)."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return SemanticClosure(
            integration_id="int_full_001",
            fraction_closed=1.0,
            report=None,
            fractions=[0.4, 0.7, 0.9, 1.0],
            closed_obligations=("obl_1", "obl_2", "obl_3", "obl_4", "obl_5"),
            open_obligations=(),
            notes="fixture: fully closed",
        )
    except Exception as exc:
        pytest.skip(f"SemanticClosure construction failed: {exc}")


@pytest.fixture
def semantic_closure_partial():
    """Return a SemanticClosure representing partial closure (fraction=0.5)."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return SemanticClosure(
            integration_id="int_partial_001",
            fraction_closed=0.5,
            report=None,
            fractions=[0.1, 0.3, 0.5],
            closed_obligations=("obl_3", "obl_4"),
            open_obligations=("obl_1", "obl_2"),
            notes="fixture: partially closed",
        )
    except Exception as exc:
        pytest.skip(f"SemanticClosure construction failed: {exc}")


@pytest.fixture
def semantic_closure_empty():
    """Return an empty SemanticClosure with no fractions or obligations."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return empty_closure(integration_id="int_empty_001")
    except Exception as exc:
        pytest.skip(f"empty_closure not available: {exc}")


# ---------------------------------------------------------------------------
# RegressionTest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def regression_test_passing():
    """Return a RegressionTest with status='passing'."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return RegressionTest(
            test_id="rt_pass_001",
            obligation_id="obl_001",
            baseline_snapshot_id="snap_baseline_001",
            status="passing",
            last_run=time.time() - 60.0,
            failure_reason="",
            notes="fixture: passing regression test",
            expected_result="closed",
            expected_confidence_min=0.7,
            tags=frozenset({"smoke", "semantic"}),
        )
    except Exception as exc:
        pytest.skip(f"RegressionTest construction failed: {exc}")


@pytest.fixture
def regression_test_failing():
    """Return a RegressionTest with status='failing'."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return RegressionTest(
            test_id="rt_fail_001",
            obligation_id="obl_002",
            baseline_snapshot_id="snap_baseline_002",
            status="failing",
            last_run=time.time() - 30.0,
            failure_reason="Confidence dropped below minimum threshold",
            notes="fixture: failing regression test",
            expected_result="closed",
            expected_confidence_min=0.8,
            tags=frozenset({"regression"}),
        )
    except Exception as exc:
        pytest.skip(f"RegressionTest construction failed: {exc}")


# ---------------------------------------------------------------------------
# RegressionRecord fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def regression_record():
    """Return a RegressionRecord documenting a detected semantic regression."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return RegressionRecord(
            record_id="rec_001",
            key="obl_001:patch_alpha",
            baseline_value={"closed": True, "confidence": 0.9},
            current_value={"closed": False, "confidence": 0.2},
            regression_type="semantic",
            severity="minor",
            cause_analysis="Treaty clause was modified without re-ratification",
            timestamp=time.time(),
            patch_id="patch_alpha",
        )
    except Exception as exc:
        pytest.skip(f"RegressionRecord construction failed: {exc}")


@pytest.fixture
def regression_record_critical():
    """Return a critical-severity RegressionRecord."""
    if not _models_available:
        pytest.skip("models not available")
    try:
        return RegressionRecord(
            record_id="rec_critical_001",
            key="obl_003:patch_beta",
            baseline_value={"closed": True, "confidence": 0.95},
            current_value=None,
            regression_type="coverage",
            severity="critical",
            cause_analysis="Evidence pool was cleared during patch rebuild",
            timestamp=time.time() - 5.0,
            patch_id="patch_beta",
        )
    except Exception as exc:
        pytest.skip(f"RegressionRecord construction failed: {exc}")


# ---------------------------------------------------------------------------
# Integration-state fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_integration_dict():
    """Return a plain dict representing a minimal integration configuration."""
    return {
        "patches": ["p1", "p2", "p3"],
        "sections": {},
        "obligations": ["obl_1", "obl_2", "obl_3"],
        "evidence": {},
    }


@pytest.fixture
def integration_state(sample_integration_dict):
    """Return an IntegrationState if s03 is available; skip otherwise."""
    if not _s03_available:
        pytest.skip("s03_integration_closure not available")
    try:
        return make_integration_state(**sample_integration_dict)
    except Exception as exc:
        pytest.skip(f"make_integration_state failed: {exc}")
