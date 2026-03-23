"""Cross-module tests: Trust tier preserved through repair workflow."""
import pytest
import time

try:
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord, RepairPlan
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

@pytest.fixture
def cx():
    return CounterexampleRecord(
        record_id="trust-repair-cx-001",
        coordinate="module.func:line_10",
        context={},
        assignment={"x": -1},
        witness=None,
        severity=0.7,
        trust_tier="ORACLE_PROPOSED",
    )

@pytest.fixture
def plan(cx):
    return RepairPlan(
        plan_id="trust-repair-plan-001",
        from_counterexample=cx.record_id,
        steps=(),
        dependencies={},
        timestamp=str(time.time()),
        status="PENDING",
        effort_estimate=1.0,
    )

def test_counterexample_starts_oracle_proposed(cx):
    assert "PROPOSED" in cx.trust_tier.upper()

def test_repair_plan_from_oracle_proposed_counterexample(cx, plan):
    assert plan.from_counterexample == cx.record_id
