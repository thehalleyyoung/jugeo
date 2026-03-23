"""Tests for the directed research module.

Tests the full prompt-driven research synthesis pipeline:
- DirectedResearch construction
- Phase execution (ideate, design, implement, benchmark, evaluate)
- Refinement and pivot mechanics
- README and paper generation
- Consistency checking via workspace site
- Edge cases
"""

import json
import os
import tempfile

import pytest

from jugeo.directed_research import (
    BenchmarkSuite,
    DirectedResearch,
    DirectedResearchResult,
    IterationKind,
    IterationRecord,
    ResearchStatus,
    SoftwarePlan,
)
from jugeo.research_orchestration import SurfaceKind


# ═══════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def output_dir():
    d = tempfile.mkdtemp(prefix="jugeo_test_research_")
    yield d
    # Cleanup happens via OS temp dir cleanup


@pytest.fixture
def basic_research(output_dir):
    """A DirectedResearch instance with LLM disabled for fast testing."""
    return DirectedResearch(
        prompt="Build a tool that uses tropical geometry to optimize supply chains",
        max_iterations=5,
        max_pivots=2,
        output_dir=output_dir,
        no_llm=True,
        seed=42,
    )


@pytest.fixture
def finance_research(output_dir):
    return DirectedResearch(
        prompt="Build a killer app in finance using advanced math",
        max_iterations=3,
        max_pivots=1,
        output_dir=output_dir,
        no_llm=True,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Construction
# ═══════════════════════════════════════════════════════════════════════

class TestConstruction:
    def test_creates_output_dir(self, basic_research):
        assert os.path.isdir(basic_research.output_dir)

    def test_initial_state_empty(self, basic_research):
        assert basic_research.theory == ""
        assert basic_research.approach == ""
        assert basic_research.code_files == []
        assert basic_research.iterations == []
        assert basic_research.pivots_taken == 0

    def test_prompt_stored(self, basic_research):
        assert "tropical geometry" in basic_research.prompt

    def test_max_settings(self, basic_research):
        assert basic_research.max_iterations == 5
        assert basic_research.max_pivots == 2


# ═══════════════════════════════════════════════════════════════════════
#  Full run (no LLM)
# ═══════════════════════════════════════════════════════════════════════

class TestFullRun:
    def test_run_completes(self, basic_research):
        result = basic_research.run()
        assert isinstance(result, DirectedResearchResult)
        assert result.total_iterations > 0

    def test_run_produces_code_files(self, basic_research):
        result = basic_research.run()
        assert len(result.code_files) > 0
        # Should have at least __init__.py and module files
        py_files = [f for f in result.code_files if f.endswith(".py")]
        assert len(py_files) >= 2

    def test_run_produces_theory(self, basic_research):
        result = basic_research.run()
        assert result.theory != ""

    def test_run_produces_approach(self, basic_research):
        result = basic_research.run()
        assert result.approach != ""

    def test_run_produces_plan(self, basic_research):
        result = basic_research.run()
        assert result.plan is not None
        assert len(result.plan.modules) >= 2

    def test_run_produces_benchmarks(self, basic_research):
        result = basic_research.run()
        assert result.benchmarks is not None

    def test_run_produces_consistency_report(self, basic_research):
        result = basic_research.run()
        assert result.consistency is not None

    def test_run_produces_readme(self, basic_research):
        result = basic_research.run()
        readme_files = [f for f in result.code_files if "README" in f]
        assert len(readme_files) >= 1
        assert os.path.exists(readme_files[0])

    def test_run_produces_paper(self, basic_research):
        result = basic_research.run()
        tex_files = [f for f in result.code_files if f.endswith(".tex")]
        assert len(tex_files) >= 1
        assert os.path.exists(tex_files[0])
        content = open(tex_files[0]).read()
        assert "\\documentclass" in content

    def test_run_produces_pyproject(self, basic_research):
        result = basic_research.run()
        toml_files = [f for f in result.code_files if f.endswith(".toml")]
        assert len(toml_files) >= 1

    def test_run_saves_metadata(self, basic_research):
        result = basic_research.run()
        meta_path = os.path.join(result.output_dir, "research_metadata.json")
        assert os.path.exists(meta_path)
        meta = json.loads(open(meta_path).read())
        assert "status" in meta
        assert "prompt" in meta
        assert "approach" in meta

    def test_run_different_prompts(self, output_dir):
        prompts = [
            "Build a tool for quantum error correction",
            "Make a database that uses homological algebra",
            "Create an ML framework based on category theory",
        ]
        for prompt in prompts:
            dr = DirectedResearch(
                prompt=prompt, max_iterations=3, max_pivots=1,
                output_dir=tempfile.mkdtemp(dir=output_dir),
                no_llm=True,
            )
            result = dr.run()
            assert result.total_iterations > 0
            assert len(result.code_files) > 0

    def test_repr(self, basic_research):
        result = basic_research.run()
        s = repr(result)
        assert "DirectedResearchResult" in s
        assert "status=" in s


# ═══════════════════════════════════════════════════════════════════════
#  Individual phases
# ═══════════════════════════════════════════════════════════════════════

class TestPhases:
    def test_ideate_populates_theory(self, basic_research):
        basic_research._ideate()
        assert basic_research.theory != ""
        assert len(basic_research.iterations) == 1
        assert basic_research.iterations[0].kind == IterationKind.IDEATE

    def test_design_produces_plan(self, basic_research):
        basic_research._ideate()
        basic_research._design()
        assert basic_research.plan is not None
        assert len(basic_research.plan.modules) >= 2
        assert basic_research.plan.package_name != ""

    def test_implement_creates_files(self, basic_research):
        basic_research._ideate()
        basic_research._design()
        basic_research._implement()
        assert len(basic_research.code_files) >= 3  # __init__ + modules + toml

    def test_benchmark_produces_suite(self, basic_research):
        basic_research._ideate()
        basic_research._design()
        basic_research._implement()
        basic_research._benchmark()
        assert basic_research.benchmarks is not None

    def test_evaluate_returns_report(self, basic_research):
        basic_research._ideate()
        basic_research._design()
        basic_research._implement()
        basic_research._benchmark()
        report = basic_research._evaluate()
        assert report is not None
        assert hasattr(report, 'consistent')
        assert hasattr(report, 'obstructions')


# ═══════════════════════════════════════════════════════════════════════
#  Pivot mechanics
# ═══════════════════════════════════════════════════════════════════════

class TestPivot:
    def test_pivot_increments_counter(self, basic_research):
        basic_research._ideate()
        assert basic_research.pivots_taken == 0
        basic_research._pivot()
        assert basic_research.pivots_taken == 1
        basic_research._pivot()
        assert basic_research.pivots_taken == 2

    def test_pivot_modifies_theory(self, basic_research):
        basic_research._ideate()
        old_theory = basic_research.theory
        basic_research._pivot()
        assert basic_research.theory != old_theory


# ═══════════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════════

class TestDataStructures:
    def test_software_plan_module_names(self):
        plan = SoftwarePlan(
            modules=[{"name": "core"}, {"name": "algo"}],
            package_name="test",
        )
        assert plan.module_names == ["core", "algo"]

    def test_benchmark_suite_competitive(self):
        suite = BenchmarkSuite(
            comparison={"speed": "better", "accuracy": "better", "memory": "worse"}
        )
        assert suite.competitive  # 2/3 wins

    def test_benchmark_suite_not_competitive(self):
        suite = BenchmarkSuite(
            comparison={"speed": "worse", "accuracy": "worse", "memory": "better"}
        )
        assert not suite.competitive  # 1/3 wins

    def test_benchmark_suite_empty(self):
        suite = BenchmarkSuite()
        assert not suite.competitive

    def test_iteration_record(self):
        rec = IterationRecord(
            iteration=0, kind=IterationKind.IDEATE,
            description="test", surface_modified=SurfaceKind.THEORY,
            trust_before=0.0, trust_after=0.3,
            obstructions_before=0, obstructions_after=0,
            elapsed=1.0, success=True,
        )
        assert rec.kind == IterationKind.IDEATE

    def test_research_status_values(self):
        assert ResearchStatus.CONVERGED.value == "converged"
        assert ResearchStatus.BUDGET_EXHAUSTED.value == "budget_exhausted"
        assert ResearchStatus.PIVOT_LIMIT.value == "pivot_limit"


# ═══════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_iterations(self, output_dir):
        dr = DirectedResearch(
            prompt="test", max_iterations=0, output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.status in (ResearchStatus.BUDGET_EXHAUSTED, ResearchStatus.PARTIAL,
                                ResearchStatus.CONVERGED)

    def test_zero_pivots(self, output_dir):
        dr = DirectedResearch(
            prompt="test", max_pivots=0, max_iterations=3,
            output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.pivots_taken == 0

    def test_short_prompt(self, output_dir):
        dr = DirectedResearch(prompt="math tool", max_iterations=3,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.total_iterations > 0

    def test_long_prompt(self, output_dir):
        prompt = "Build a comprehensive " + "very " * 100 + "advanced tool"
        dr = DirectedResearch(prompt=prompt, max_iterations=3,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.total_iterations > 0

    def test_result_success_property(self, output_dir):
        dr = DirectedResearch(prompt="test", max_iterations=5,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        # success property should be boolean
        assert isinstance(result.success, bool)
