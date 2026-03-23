"""Code orchestrator for synthesis frontier — derives code targets from math papers.
# copilot: synthesis frontier code orchestrator — math paper → code generation targets
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Conditional imports with graceful fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.synthesis_frontier.paper_generator import (
        MathPaper,
        LatexTheoremBlock,
        TheoremEnvironment,
        PaperSection,
    )
except ImportError:
    try:
        from .paper_generator import (
            MathPaper,
            LatexTheoremBlock,
            TheoremEnvironment,
            PaperSection,
        )
    except ImportError:
        # Minimal stubs for standalone execution

        class TheoremEnvironment(str, Enum):  # type: ignore[no-redef]
            THEOREM = "theorem"
            DEFINITION = "definition"
            LEMMA = "lemma"
            COROLLARY = "corollary"
            CONJECTURE = "conjecture"
            EXAMPLE = "example"
            BRIDGE_THEOREM = "bridge_theorem"
            REMARK = "remark"
            PROPOSITION = "proposition"

        @dataclass
        class LatexTheoremBlock:  # type: ignore[no-redef]
            env: Any = TheoremEnvironment.THEOREM
            label: str = ""
            statement: str = ""
            proof: str = ""
            number: int = 0

        @dataclass
        class PaperSection:  # type: ignore[no-redef]
            section_id: str = ""
            title: str = ""
            content: str = ""
            propositions: tuple = ()
            level: int = 1

            ABSTRACT: "PaperSection" = None  # filled in below

        PaperSection.ABSTRACT = PaperSection(section_id="abstract", title="Abstract", content="", propositions=())

        @dataclass
        class MathPaper:  # type: ignore[no-redef]
            paper_id: str = ""
            title: str = ""
            authors: tuple = ()
            abstract: str = ""
            sections: Any = ()
            theorems: tuple = ()
            bibliography: Any = ()
            metadata: dict = None
            created_at: float = 0.0

            def __post_init__(self):
                if self.metadata is None:
                    self.metadata = {}


# ---------------------------------------------------------------------------
# CodeTarget
# ---------------------------------------------------------------------------


class CodeTarget(str, Enum):
    """The kind of code artifact a spec describes."""

    TYPE_CHECKER = "type_checker"
    PROOF_ASSISTANT = "proof_assistant"
    ALGORITHM = "algorithm"
    DATA_STRUCTURE = "data_structure"
    VERIFICATION_CONDITION = "verification_condition"
    ABSTRACTION = "abstraction"
    TEST_SUITE = "test_suite"


# ---------------------------------------------------------------------------
# CodeLanguage
# ---------------------------------------------------------------------------


class CodeLanguage(str, Enum):
    """Target programming language for a CodeSpec."""

    PYTHON = "python"
    LEAN4 = "lean4"
    AGDA = "agda"
    COQ = "coq"
    HASKELL = "haskell"
    TYPESCRIPT = "typescript"
    RUST = "rust"


# ---------------------------------------------------------------------------
# CodeSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeSpec:
    """A single code generation target derived from a theorem or definition.

    Parameters
    ----------
    spec_id:
        Unique identifier for this spec.
    target_type:
        The kind of code artifact (type checker, proof, data structure, etc.).
    language:
        Target programming language.
    title:
        Short human-readable title.
    description:
        Detailed description of what this spec should implement.
    source_theorem_ids:
        Tuple of theorem labels from which this spec was derived.
    priority:
        Float in [0, 1] — higher means more important.
    estimated_lines:
        Rough estimate of lines of code needed.
    dependencies:
        Spec IDs that must be generated before this one.
    """

    spec_id: str
    target_type: CodeTarget
    language: CodeLanguage
    title: str
    description: str
    source_theorem_ids: tuple[str, ...]
    priority: float
    estimated_lines: int
    dependencies: tuple[str, ...]

    def summary(self) -> str:
        """Return a one-line summary of this spec."""
        return (
            f"[{self.language.value}/{self.target_type.value}] "
            f"{self.title!r} (priority={self.priority:.2f}, ~{self.estimated_lines} lines)"
        )


# ---------------------------------------------------------------------------
# CodePlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodePlan:
    """A complete code generation plan derived from a MathPaper.

    Parameters
    ----------
    plan_id:
        Unique identifier for this plan.
    paper_id:
        ID of the source MathPaper.
    specs:
        Tuple of CodeSpec objects.
    total_specs:
        Total number of specs (= len(specs)).
    estimated_total_lines:
        Sum of estimated_lines across all specs.
    languages_required:
        Tuple of unique language values required.
    created_at:
        Unix timestamp of plan creation.
    """

    plan_id: str
    paper_id: str
    specs: tuple[CodeSpec, ...]
    total_specs: int
    estimated_total_lines: int
    languages_required: tuple[str, ...]
    created_at: float

    def specs_by_priority(self) -> list[CodeSpec]:
        """Return specs sorted by priority descending."""
        return sorted(self.specs, key=lambda s: -s.priority)

    def specs_for_language(self, lang: CodeLanguage) -> list[CodeSpec]:
        """Return specs targeting the given language."""
        return [s for s in self.specs if s.language == lang]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "plan_id": self.plan_id,
            "paper_id": self.paper_id,
            "total_specs": self.total_specs,
            "estimated_total_lines": self.estimated_total_lines,
            "languages_required": list(self.languages_required),
            "created_at": self.created_at,
            "specs": [
                {
                    "spec_id": s.spec_id,
                    "target_type": s.target_type.value,
                    "language": s.language.value,
                    "title": s.title,
                    "priority": s.priority,
                    "estimated_lines": s.estimated_lines,
                }
                for s in self.specs
            ],
        }


# ---------------------------------------------------------------------------
# TheoremToCodeMapper
# ---------------------------------------------------------------------------


class TheoremToCodeMapper:
    """Maps a single LatexTheoremBlock to one or more CodeSpec objects.

    The mapping rules are:
    - THEOREM / BRIDGE_THEOREM → TYPE_CHECKER in Lean4 + TEST_SUITE in Python
    - DEFINITION              → DATA_STRUCTURE in Haskell + ABSTRACTION in Python
    - LEMMA                   → VERIFICATION_CONDITION in Coq
    - CONJECTURE              → PROOF_ASSISTANT in Agda
    - EXAMPLE                 → TEST_SUITE in Python
    All other environments    → TEST_SUITE in Python (fallback)
    """

    # Estimated lines of code per environment type
    _LINES: dict[str, int] = {
        "theorem": 80,
        "bridge_theorem": 80,
        "definition": 50,
        "lemma": 40,
        "conjecture": 100,
        "example": 30,
        "corollary": 35,
        "proposition": 45,
        "remark": 20,
    }

    # Priority weights per environment type
    _PRIORITY: dict[str, float] = {
        "theorem": 0.8,
        "bridge_theorem": 0.9,
        "definition": 0.7,
        "lemma": 0.6,
        "conjecture": 0.5,
        "corollary": 0.55,
        "proposition": 0.65,
        "example": 0.4,
        "remark": 0.3,
    }

    def _env_str(self, theorem: "LatexTheoremBlock") -> str:
        env = theorem.env
        if hasattr(env, "value"):
            return str(env.value).lower()
        return str(env).lower()

    def _lines(self, env: str) -> int:
        return self._LINES.get(env, 30)

    def _priority(self, env: str) -> float:
        return self._PRIORITY.get(env, 0.4)

    def _make_spec(
        self,
        theorem: "LatexTheoremBlock",
        paper: "MathPaper",
        target_type: CodeTarget,
        language: CodeLanguage,
        suffix: str,
        lines: int,
        priority: float,
        deps: tuple[str, ...] = (),
    ) -> CodeSpec:
        label = getattr(theorem, "label", "") or f"thm{getattr(theorem, 'number', 0)}"
        paper_id = getattr(paper, "paper_id", "unknown")
        spec_id = str(uuid.uuid4())
        title = f"{label}.{suffix}"
        description = (
            f"Implement {target_type.value} for theorem '{label}' "
            f"from paper '{getattr(paper, 'title', '')}'. "
            f"Statement: {getattr(theorem, 'statement', '')[:120]}"
        )
        return CodeSpec(
            spec_id=spec_id,
            target_type=target_type,
            language=language,
            title=title,
            description=description,
            source_theorem_ids=(label,),
            priority=priority,
            estimated_lines=lines,
            dependencies=deps,
        )

    def map_theorem(
        self,
        theorem: "LatexTheoremBlock",
        paper: "MathPaper",
    ) -> list[CodeSpec]:
        """Map a single theorem block to a list of CodeSpec objects."""
        env = self._env_str(theorem)
        lines = self._lines(env)
        priority = self._priority(env)
        specs: list[CodeSpec] = []

        if env in ("theorem", "bridge_theorem"):
            # TYPE_CHECKER in Lean4
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.TYPE_CHECKER, CodeLanguage.LEAN4,
                "lean4_type_check", lines, priority,
            )
            # TEST_SUITE in Python
            s2 = self._make_spec(
                theorem, paper,
                CodeTarget.TEST_SUITE, CodeLanguage.PYTHON,
                "python_tests", max(lines // 2, 20), priority - 0.05,
                deps=(s1.spec_id,),
            )
            specs.extend([s1, s2])

        elif env == "definition":
            # DATA_STRUCTURE in Haskell
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.DATA_STRUCTURE, CodeLanguage.HASKELL,
                "haskell_data", lines, priority,
            )
            # ABSTRACTION in Python
            s2 = self._make_spec(
                theorem, paper,
                CodeTarget.ABSTRACTION, CodeLanguage.PYTHON,
                "python_abstract", max(lines // 2, 20), priority - 0.05,
                deps=(s1.spec_id,),
            )
            specs.extend([s1, s2])

        elif env == "lemma":
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.VERIFICATION_CONDITION, CodeLanguage.COQ,
                "coq_verification", lines, priority,
            )
            specs.append(s1)

        elif env == "conjecture":
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.PROOF_ASSISTANT, CodeLanguage.AGDA,
                "agda_proof_attempt", lines, priority,
            )
            specs.append(s1)

        elif env == "example":
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.TEST_SUITE, CodeLanguage.PYTHON,
                "python_example_tests", lines, priority,
            )
            specs.append(s1)

        else:
            # Fallback: produce a Python TEST_SUITE
            s1 = self._make_spec(
                theorem, paper,
                CodeTarget.TEST_SUITE, CodeLanguage.PYTHON,
                "python_tests", lines, priority,
            )
            specs.append(s1)

        return specs


# ---------------------------------------------------------------------------
# CodeOrchestrator
# ---------------------------------------------------------------------------


class CodeOrchestrator:
    """Orchestrates derivation of code targets from a MathPaper.

    Parameters
    ----------
    target_languages:
        Restrict generated specs to these languages.  Defaults to
        [LEAN4, PYTHON, HASKELL].
    """

    _DEFAULT_LANGUAGES: list[CodeLanguage] = [
        CodeLanguage.LEAN4,
        CodeLanguage.PYTHON,
        CodeLanguage.HASKELL,
    ]

    def __init__(
        self,
        target_languages: list[CodeLanguage] | None = None,
    ) -> None:
        self.target_languages: list[CodeLanguage] = (
            target_languages if target_languages is not None else list(self._DEFAULT_LANGUAGES)
        )
        self._mapper = TheoremToCodeMapper()

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    def plan(self, paper: "MathPaper") -> CodePlan:
        """Derive a CodePlan from a MathPaper.

        Maps every theorem/definition/etc. in the paper to CodeSpec objects,
        filters to target_languages, then assembles the plan.

        Parameters
        ----------
        paper:
            The source MathPaper.

        Returns
        -------
        CodePlan
            Complete plan with all specs, line estimates, and language set.
        """
        paper_id = getattr(paper, "paper_id", str(uuid.uuid4()))
        theorems = getattr(paper, "theorems", ())

        all_specs: list[CodeSpec] = []
        for theorem in theorems:
            try:
                specs = self._mapper.map_theorem(theorem, paper)
                for s in specs:
                    if s.language in self.target_languages:
                        all_specs.append(s)
            except Exception:
                pass

        langs_required = tuple(sorted({s.language.value for s in all_specs}))
        total_lines = sum(s.estimated_lines for s in all_specs)

        return CodePlan(
            plan_id=str(uuid.uuid4()),
            paper_id=paper_id,
            specs=tuple(all_specs),
            total_specs=len(all_specs),
            estimated_total_lines=total_lines,
            languages_required=langs_required,
            created_at=time.time(),
        )

    # ------------------------------------------------------------------
    # describe_plan
    # ------------------------------------------------------------------

    def describe_plan(self, plan: CodePlan) -> str:
        """Return a human-readable description of a CodePlan.

        Includes:
        - Total specs and estimated lines
        - Breakdown by language
        - Top specs by priority
        """
        lines: list[str] = []
        lines.append(f"CodePlan {plan.plan_id[:8]}...")
        lines.append(f"  Source paper  : {plan.paper_id}")
        lines.append(f"  Total specs   : {plan.total_specs}")
        lines.append(f"  Est. lines    : {plan.estimated_total_lines}")
        lines.append(f"  Languages     : {', '.join(plan.languages_required) or '(none)'}")
        lines.append("")

        # Count by language
        lang_counts: dict[str, int] = {}
        lang_lines: dict[str, int] = {}
        for s in plan.specs:
            k = s.language.value
            lang_counts[k] = lang_counts.get(k, 0) + 1
            lang_lines[k] = lang_lines.get(k, 0) + s.estimated_lines
        lines.append("  Specs by language:")
        for lang in sorted(lang_counts):
            lines.append(f"    {lang:20s} {lang_counts[lang]:3d} specs, ~{lang_lines[lang]} lines")
        lines.append("")

        # Count by target type
        type_counts: dict[str, int] = {}
        for s in plan.specs:
            k = s.target_type.value
            type_counts[k] = type_counts.get(k, 0) + 1
        lines.append("  Specs by type:")
        for t in sorted(type_counts):
            lines.append(f"    {t:30s} {type_counts[t]:3d}")
        lines.append("")

        # Top specs by priority
        top = plan.specs_by_priority()[:5]
        if top:
            lines.append("  Top specs by priority:")
            for s in top:
                lines.append(f"    [{s.priority:.2f}] {s.title} ({s.language.value})")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # execute_spec
    # ------------------------------------------------------------------

    def execute_spec(self, spec: CodeSpec) -> dict[str, Any]:
        """Generate a stub implementation for a CodeSpec.

        Returns a dict with keys: spec_id, language, code_stub, status.
        The stub contains type signatures and TODO comments — not real
        implementations.

        Parameters
        ----------
        spec:
            The spec to generate a stub for.

        Returns
        -------
        dict
            With keys spec_id, language, code_stub, status.
        """
        lang = spec.language
        name = spec.title.replace(".", "_").replace(" ", "_").replace("-", "_")
        stub = self._generate_stub(spec, lang, name)
        return {
            "spec_id": spec.spec_id,
            "language": lang.value,
            "code_stub": stub,
            "status": "stub_generated",
            "target_type": spec.target_type.value,
            "title": spec.title,
            "estimated_lines": spec.estimated_lines,
        }

    def _generate_stub(
        self, spec: CodeSpec, lang: CodeLanguage, name: str
    ) -> str:
        """Generate language-appropriate stub code."""
        desc_line = spec.description[:100].replace("\n", " ")

        if lang == CodeLanguage.LEAN4:
            return (
                f"-- {spec.title}\n"
                f"-- {desc_line}\n"
                f"-- Source: {', '.join(spec.source_theorem_ids)}\n"
                f"-- TODO: prove\n\n"
                f"theorem {name} : Type := by\n"
                f"  sorry\n"
            )

        elif lang == CodeLanguage.PYTHON:
            args = ""
            if spec.target_type == CodeTarget.TEST_SUITE:
                return (
                    f'"""Tests for {spec.title}.\n\n'
                    f"{desc_line}\n"
                    f'"""\n\n'
                    f"import pytest\n\n\n"
                    f"class Test_{name}:\n"
                    f'    """Auto-generated test suite — TODO: implement."""\n\n'
                    f"    def test_placeholder(self) -> None:\n"
                    f"        # TODO: implement test for {', '.join(spec.source_theorem_ids)}\n"
                    f"        pass\n"
                )
            return (
                f'def {name}({args}) -> None:\n'
                f'    """{desc_line}\n\n'
                f"    Source theorems: {', '.join(spec.source_theorem_ids)}\n"
                f'    TODO: implement\n'
                f'    """\n'
                f"    pass  # TODO\n"
            )

        elif lang == CodeLanguage.HASKELL:
            return (
                f"-- {spec.title}\n"
                f"-- {desc_line}\n"
                f"-- Source: {', '.join(spec.source_theorem_ids)}\n"
                f"-- TODO\n\n"
                f"module {name.title().replace('_', '')} where\n\n"
                f"-- | TODO: implement data structure\n"
                f"data {name.title().replace('_', '')} = {name.title().replace('_', '')} -- TODO\n\n"
                f"{name} :: {name.title().replace('_', '')}\n"
                f"{name} = undefined\n"
            )

        elif lang == CodeLanguage.COQ:
            return (
                f"(* {spec.title} *)\n"
                f"(* {desc_line} *)\n"
                f"(* Source: {', '.join(spec.source_theorem_ids)} *)\n\n"
                f"Require Import Coq.Init.Prelude.\n\n"
                f"(* TODO: state and prove verification condition *)\n"
                f"Theorem {name} : True.\n"
                f"Proof.\n"
                f"  trivial.\n"
                f"  (* TODO: real proof *)\n"
                f"Qed.\n"
            )

        elif lang == CodeLanguage.AGDA:
            return (
                f"-- {spec.title}\n"
                f"-- {desc_line}\n"
                f"-- Source: {', '.join(spec.source_theorem_ids)}\n\n"
                f"module {name} where\n\n"
                f"open import Agda.Builtin.Nat\n\n"
                f"-- TODO: prove conjecture\n"
                f"postulate\n"
                f"  {name}-conjecture : Set  -- TODO: fill in type\n"
            )

        elif lang == CodeLanguage.TYPESCRIPT:
            return (
                f"// {spec.title}\n"
                f"// {desc_line}\n"
                f"// Source: {', '.join(spec.source_theorem_ids)}\n\n"
                f"// TODO: implement\n"
                f"export function {name}(): void {{\n"
                f"  // TODO\n"
                f"  throw new Error('Not implemented');\n"
                f"}}\n"
            )

        elif lang == CodeLanguage.RUST:
            return (
                f"// {spec.title}\n"
                f"// {desc_line}\n"
                f"// Source: {', '.join(spec.source_theorem_ids)}\n\n"
                f"// TODO: implement\n"
                f"pub fn {name}() {{\n"
                f"    todo!(\"Implement {spec.title}\")\n"
                f"}}\n"
            )

        else:
            return f"// TODO: stub for {spec.title} in {lang.value}\n"

    # ------------------------------------------------------------------
    # execute_plan
    # ------------------------------------------------------------------

    def execute_plan(
        self,
        plan: CodePlan,
        max_specs: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute all specs in a plan in priority order.

        Parameters
        ----------
        plan:
            The plan to execute.
        max_specs:
            If given, only execute the top-N specs by priority.

        Returns
        -------
        list[dict]
            List of execution results, one per spec.
        """
        ordered = plan.specs_by_priority()
        if max_specs is not None:
            ordered = ordered[:max_specs]
        results: list[dict[str, Any]] = []
        for spec in ordered:
            try:
                result = self.execute_spec(spec)
                results.append(result)
            except Exception as exc:
                results.append({
                    "spec_id": spec.spec_id,
                    "language": spec.language.value,
                    "code_stub": "",
                    "status": f"error: {exc}",
                    "title": spec.title,
                })
        return results


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    try:
        from jugeo.ideation.synthesis_frontier.paper_generator import (
            MathPaper, LatexTheoremBlock, TheoremEnvironment, PaperSection
        )
        import time as _time, uuid as _uuid
        theorems = (
            LatexTheoremBlock(env=TheoremEnvironment.THEOREM, label="yoneda",
                             statement="Every presheaf is a colimit of representables.", proof="By the Yoneda embedding.", number=1),
            LatexTheoremBlock(env=TheoremEnvironment.DEFINITION, label="adjunction",
                             statement="An adjunction F ⊣ G consists of natural bijections Hom(FA, B) ≅ Hom(A, GB).", proof="", number=2),
        )
        paper = MathPaper(
            paper_id=str(_uuid.uuid4()), title="Test", authors=("Test",),
            abstract="Test abstract", sections=(PaperSection.ABSTRACT,),
            theorems=theorems, bibliography=(), metadata={}, created_at=_time.time()
        )
        orch = CodeOrchestrator()
        plan = orch.plan(paper)
        print(f"Plan: {plan.total_specs} specs, {plan.estimated_total_lines} lines")
        print(orch.describe_plan(plan))
        results = orch.execute_plan(plan, max_specs=3)
        for r in results:
            print(f"  {r['spec_id'][:8]}... [{r['language']}] — {r['status']}")
    except Exception as e:
        import traceback; traceback.print_exc()
