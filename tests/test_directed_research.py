"""Tests for the directed research module (rewritten for radical geometry-first API)."""

import json
import os
import tempfile

import pytest

from jugeo.directed_research import (
    DirectedResearch,
    DirectedResearchResult,
    LLMSection,
    MoveKind,
    MoveResult,
    ResearchStatus,
    TRUST_COPILOT,
    TRUST_RUNTIME,
)
from jugeo.research_orchestration import SurfaceKind


@pytest.fixture
def output_dir():
    return tempfile.mkdtemp(prefix="jugeo_test_dr_")


@pytest.fixture
def basic(output_dir):
    return DirectedResearch(
        prompt="Build a tool using advanced math for supply chains",
        max_iterations=5, max_pivots=2, output_dir=output_dir, no_llm=True)


@pytest.fixture
def finance(output_dir):
    return DirectedResearch(
        prompt="Build a killer app in finance",
        max_iterations=3, max_pivots=1, output_dir=output_dir, no_llm=True)


class TestConstruction:
    def test_creates_output_dir(self, basic):
        assert os.path.isdir(basic.output_dir)

    def test_initial_state(self, basic):
        assert basic.sections == []
        assert basic.moves == []
        assert basic.code_files == []
        assert basic.pivots == 0

    def test_prompt_stored(self, basic):
        assert "supply chains" in basic.prompt


class TestLLMSection:
    def test_section_fields(self):
        s = LLMSection(surface=SurfaceKind.THEORY, coordinate="theory.test",
                       content="hello", trust=TRUST_COPILOT)
        assert s.trust == TRUST_COPILOT
        assert s.surface == SurfaceKind.THEORY

    def test_upgrade_trust(self):
        s = LLMSection(surface=SurfaceKind.EVIDENCE, coordinate="ev.bench",
                       content="result=0.8", trust=TRUST_COPILOT)
        upgraded = s.upgrade_trust(TRUST_RUNTIME, "benchmark-confirmed")
        assert upgraded.trust == TRUST_RUNTIME
        assert "benchmark-confirmed" in upgraded.provenance

    def test_upgrade_never_demotes(self):
        s = LLMSection(surface=SurfaceKind.CODE, coordinate="code.core",
                       content="x", trust=TRUST_RUNTIME)
        upgraded = s.upgrade_trust(TRUST_COPILOT, "weaker")
        assert upgraded.trust == TRUST_RUNTIME  # didn't demote


class TestFullRun:
    def test_completes(self, basic):
        result = basic.run()
        assert isinstance(result, DirectedResearchResult)
        assert len(result.moves) > 0

    def test_produces_code_files(self, basic):
        result = basic.run()
        py_files = [f for f in result.code_files if f.endswith(".py")]
        assert len(py_files) >= 2

    def test_produces_approach(self, basic):
        result = basic.run()
        assert result.approach != ""

    def test_produces_sections(self, basic):
        result = basic.run()
        assert len(result.sections) > 0

    def test_produces_readme(self, basic):
        result = basic.run()
        assert any("README" in f for f in result.code_files)

    def test_produces_paper(self, basic):
        result = basic.run()
        assert any(f.endswith(".tex") for f in result.code_files)

    def test_produces_pyproject(self, basic):
        result = basic.run()
        assert any(f.endswith(".toml") for f in result.code_files)

    def test_saves_metadata(self, basic):
        result = basic.run()
        meta_path = os.path.join(result.output_dir, "research_metadata.json")
        assert os.path.exists(meta_path)
        meta = json.loads(open(meta_path).read())
        assert "status" in meta
        assert "trust_profile" in meta

    def test_different_prompts(self, output_dir):
        for prompt in ["quantum tool", "database optimizer", "ML framework"]:
            dr = DirectedResearch(prompt=prompt, max_iterations=3,
                                 output_dir=tempfile.mkdtemp(dir=output_dir), no_llm=True)
            result = dr.run()
            assert len(result.code_files) > 0

    def test_repr(self, basic):
        result = basic.run()
        s = repr(result)
        assert "DirectedResearchResult" in s
        assert "sections:" in s


class TestMoves:
    def test_analyze_domain(self, basic):
        basic._move_analyze_domain()
        assert basic.approach != ""

    def test_elaborate_theory(self, basic):
        basic._move_analyze_domain()
        basic._move_elaborate_theory()
        assert basic.theory_text != ""
        assert os.path.exists(basic.output_dir / "context.md")

    def test_design_architecture(self, basic):
        basic._move_analyze_domain()
        basic._move_elaborate_theory()
        basic._move_design_architecture()
        assert "modules" in basic.architecture

    def test_generate_module(self, basic):
        basic._move_analyze_domain()
        basic._move_elaborate_theory()
        basic._move_design_architecture()
        mod = basic.architecture["modules"][0]
        basic._move_generate_module(mod)
        assert len(basic.code_files) >= 1

    def test_syntax_check(self, basic):
        basic._move_analyze_domain()
        basic._move_elaborate_theory()
        basic._move_design_architecture()
        for m in basic.architecture["modules"]:
            basic._move_generate_module(m)
        basic._move_syntax_check()
        assert "syntax_check" in basic.benchmark_results


class TestPivot:
    def test_pivot_increments(self, basic):
        basic._move_analyze_domain()
        basic._move_elaborate_theory()
        basic._move_pivot_theory()
        assert basic.pivots == 1
        basic._move_pivot_theory()
        assert basic.pivots == 2


class TestEdgeCases:
    def test_zero_iterations(self, output_dir):
        dr = DirectedResearch(prompt="test", max_iterations=0,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.status in (ResearchStatus.BUDGET_EXHAUSTED, ResearchStatus.PARTIAL,
                                ResearchStatus.CONVERGED)

    def test_zero_pivots(self, output_dir):
        dr = DirectedResearch(prompt="test", max_pivots=0, max_iterations=3,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert result.pivots == 0

    def test_success_property(self, output_dir):
        dr = DirectedResearch(prompt="test", max_iterations=5,
                             output_dir=output_dir, no_llm=True)
        result = dr.run()
        assert isinstance(result.success, bool)

    def test_move_result_fields(self):
        mr = MoveResult(move=MoveKind.ANALYZE_DOMAIN, surface=SurfaceKind.THEORY,
                       section=None, trust_achieved=0.3, success=True,
                       description="test", elapsed=1.0)
        assert mr.success

    def test_research_status_values(self):
        assert ResearchStatus.CONVERGED.value == "converged"
        assert ResearchStatus.BUDGET_EXHAUSTED.value == "budget_exhausted"
