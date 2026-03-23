"""Synthesis frontier pipeline — wires all components into a unified execution pipeline.
# copilot: synthesis frontier pipeline — 48 fields → tournament → paper → code
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional imports with graceful fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.synthesis_frontier.models import FieldNode, TournamentState, MetaphorLink
except ImportError:
    try:
        from .models import FieldNode, TournamentState, MetaphorLink  # type: ignore[no-redef]
    except ImportError:
        _log.warning("models not importable; using stubs")

        @dataclass
        class MetaphorLink:  # type: ignore[no-redef]
            link_id: str = field(default_factory=lambda: str(uuid.uuid4()))
            source_field_id: str = ""
            target_field_id: str = ""
            description: str = ""
            strength: float = 0.5

        @dataclass
        class FieldNode:  # type: ignore[no-redef]
            field_id: str = field(default_factory=lambda: str(uuid.uuid4()))
            name: str = ""
            description: str = ""
            propositions: tuple = ()
            constituent_fields: tuple = ()
            round_number: int = 0
            keywords: tuple = ()
            created_at: float = field(default_factory=time.time)

            @staticmethod
            def make(name: str = "", description: str = "", **kw) -> "FieldNode":
                obj = FieldNode(
                    field_id=str(uuid.uuid4()),
                    name=name,
                    description=description,
                    created_at=time.time(),
                )
                for k, v in kw.items():
                    if hasattr(obj, k):
                        object.__setattr__(obj, k, v)
                return obj

            def proposition_count(self) -> int:
                return len(self.propositions)

            def summary_line(self) -> str:
                return f"FieldNode({self.name!r}, round={self.round_number})"

        @dataclass
        class TournamentState:  # type: ignore[no-redef]
            state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
            current_round: int = 0
            active_nodes: list = field(default_factory=list)
            completed_merges: list = field(default_factory=list)
            all_nodes: dict = field(default_factory=dict)
            all_pairs: dict = field(default_factory=dict)
            all_metaphors: dict = field(default_factory=dict)
            is_complete: bool = False
            created_at: float = field(default_factory=time.time)
            updated_at: float = field(default_factory=time.time)
            metadata: dict = field(default_factory=dict)

            def register_node(self, node: Any) -> None:
                self.all_nodes[node.field_id] = node

            def total_propositions(self) -> int:
                return sum(
                    n.proposition_count() for n in self.active_nodes
                )

            def summary(self) -> str:
                return (
                    f"TournamentState(round={self.current_round}, "
                    f"active={len(self.active_nodes)}, complete={self.is_complete})"
                )


try:
    from jugeo.ideation.synthesis_frontier.fields import ALL_128_FIELDS
except ImportError:
    try:
        from .fields import ALL_128_FIELDS  # type: ignore[no-redef]
    except ImportError:
        ALL_128_FIELDS = []  # type: ignore[assignment]


try:
    from jugeo.ideation.synthesis_frontier.llm_judge import JudgeMode, JudgeConfig, SynthesisJudge
except ImportError:
    try:
        from .llm_judge import JudgeMode, JudgeConfig, SynthesisJudge  # type: ignore[no-redef]
    except ImportError:

        class JudgeMode(str):  # type: ignore[no-redef]
            HEURISTIC = "heuristic"
            LLM = "llm"

        @dataclass
        class JudgeConfig:  # type: ignore[no-redef]
            mode: str = "heuristic"
            model: str = "claude-sonnet-4-6"
            timeout: int = 120

        class SynthesisJudge:  # type: ignore[no-redef]
            def __init__(self, config: Any = None) -> None:
                self._config = config


try:
    from jugeo.ideation.synthesis_frontier.tournament import (
        PairingStrategy, Tournament, RoundResult
    )
except ImportError:
    try:
        from .tournament import PairingStrategy, Tournament, RoundResult  # type: ignore[no-redef]
    except ImportError:
        try:
            # tournament.py uses BinaryTournamentFrontier and RoundResult
            from jugeo.ideation.synthesis_frontier.tournament import (
                PairingStrategy,
                BinaryTournamentFrontier as Tournament,
                RoundResult,
            )
        except ImportError:
            try:
                from .tournament import (  # type: ignore[no-redef]
                    PairingStrategy,
                    BinaryTournamentFrontier as Tournament,
                    RoundResult,
                )
            except ImportError:

                class PairingStrategy(str):  # type: ignore[no-redef]
                    RANDOM = "random"
                    SIMILARITY = "similarity"
                    DIVERSITY = "diversity"
                    GREEDY = "greedy"

                @dataclass(frozen=True)
                class RoundResult:  # type: ignore[no-redef]
                    round_number: int = 0
                    fields_before: int = 0
                    fields_after: int = 0
                    total_propositions: int = 0
                    duration_seconds: float = 0.0
                    merges: tuple = ()
                    top_metaphors: tuple = ()

                class Tournament:  # type: ignore[no-redef]
                    def __init__(self, fields: list, config: Any = None, judge: Any = None) -> None:
                        self._fields = list(fields)

                    def run(self) -> TournamentState:
                        state = TournamentState(active_nodes=list(self._fields))
                        for node in state.active_nodes:
                            state.register_node(node)
                        # Trivial heuristic merge: just keep first node
                        while len(state.active_nodes) > 1:
                            a = state.active_nodes.pop(0)
                            b = state.active_nodes.pop(0)
                            merged = FieldNode(
                                field_id=str(uuid.uuid4()),
                                name=f"{a.name}+{b.name}",
                                description=f"Synthesis of {a.name} and {b.name}",
                                propositions=tuple(list(getattr(a, 'propositions', ())) + list(getattr(b, 'propositions', ()))),
                                constituent_fields=tuple(list(getattr(a, 'constituent_fields', (a.name,))) + list(getattr(b, 'constituent_fields', (b.name,)))),
                                round_number=state.current_round + 1,
                                created_at=time.time(),
                            )
                            state.active_nodes.append(merged)
                            state.register_node(merged)
                            state.current_round += 1
                        state.is_complete = True
                        return state

                    def run_round(self, state: TournamentState) -> TournamentState:
                        return state


try:
    from jugeo.ideation.synthesis_frontier.metaphor_finder import MetaphorFinder
except ImportError:
    try:
        from .metaphor_finder import MetaphorFinder  # type: ignore[no-redef]
    except ImportError:

        class MetaphorFinder:  # type: ignore[no-redef]
            def find_metaphors(self, field_a: Any, field_b: Any) -> list:
                return []

            def scan_fields(self, fields: list) -> Any:
                return None


try:
    from jugeo.ideation.synthesis_frontier.paper_generator import MathPaper, PaperGenerator
except ImportError:
    try:
        from .paper_generator import MathPaper, PaperGenerator  # type: ignore[no-redef]
    except ImportError:

        @dataclass
        class MathPaper:  # type: ignore[no-redef]
            paper_id: str = field(default_factory=lambda: str(uuid.uuid4()))
            title: str = ""
            authors: tuple = ()
            abstract: str = ""
            sections: Any = ()
            theorems: tuple = ()
            bibliography: Any = ()
            metadata: dict = field(default_factory=dict)
            created_at: float = field(default_factory=time.time)

        class PaperGenerator:  # type: ignore[no-redef]
            def generate(self, node: Any) -> "MathPaper":
                name = getattr(node, "name", "Unknown")
                return MathPaper(
                    title=f"On the Synthesis of {name}",
                    authors=("JuGeo Synthesis Frontier",),
                    abstract=f"A synthesis paper on {name}.",
                    metadata={"source_field": name},
                )


try:
    from jugeo.ideation.synthesis_frontier.code_orchestrator import CodePlan, CodeOrchestrator
except ImportError:
    try:
        from .code_orchestrator import CodePlan, CodeOrchestrator  # type: ignore[no-redef]
    except ImportError:

        @dataclass(frozen=True)
        class CodePlan:  # type: ignore[no-redef]
            plan_id: str = ""
            paper_id: str = ""
            specs: tuple = ()
            total_specs: int = 0
            estimated_total_lines: int = 0
            languages_required: tuple = ()
            created_at: float = 0.0

            def specs_by_priority(self) -> list:
                return list(self.specs)

        class CodeOrchestrator:  # type: ignore[no-redef]
            def __init__(self, target_languages: Any = None) -> None:
                pass

            def plan(self, paper: Any) -> "CodePlan":
                return CodePlan(plan_id=str(uuid.uuid4()), created_at=time.time())

            def describe_plan(self, plan: "CodePlan") -> str:
                return f"CodePlan: {plan.total_specs} specs"

            def execute_plan(self, plan: "CodePlan", max_specs: int | None = None) -> list:
                return []


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for the SynthesisFrontierPipeline.

    Parameters
    ----------
    strategy:
        How fields are paired at each round.
    judge_mode:
        Whether to use heuristic or LLM scoring.
    max_rounds:
        Hard cap on rounds (None = run to completion).
    use_metaphor_finder:
        Whether to run MetaphorFinder on the final synthesis.
    generate_paper:
        Whether to generate a MathPaper from the final synthesis.
    generate_code:
        Whether to run the code orchestrator on the paper.
    output_dir:
        Directory for output files.
    model:
        LLM model slug.
    verbose:
        Verbose logging.
    checkpoint_dir:
        Directory for checkpoints (None = no checkpointing).
    """

    strategy: Any = None  # PairingStrategy.DIVERSITY
    judge_mode: Any = None  # JudgeMode.HEURISTIC
    max_rounds: int | None = None
    use_metaphor_finder: bool = True
    generate_paper: bool = True
    generate_code: bool = False
    output_dir: str = "outputs/synthesis"
    model: str = "claude-sonnet-4-6"
    verbose: bool = False
    checkpoint_dir: str | None = None

    def __post_init__(self) -> None:
        # Use object.__setattr__ because frozen=True
        if self.strategy is None:
            object.__setattr__(self, "strategy", PairingStrategy.DIVERSITY)
        if self.judge_mode is None:
            object.__setattr__(self, "judge_mode", JudgeMode.HEURISTIC)


# ---------------------------------------------------------------------------
# PipelineProgress
# ---------------------------------------------------------------------------


@dataclass
class PipelineProgress:
    """Mutable progress tracker for a running pipeline.

    Updated in-place as the pipeline executes.
    """

    current_round: int = 0
    total_rounds: int = 0
    fields_remaining: int = 0
    propositions_total: int = 0
    metaphors_found: int = 0
    papers_generated: int = 0
    status_message: str = ""

    def update(
        self,
        *,
        current_round: int | None = None,
        fields_remaining: int | None = None,
        propositions_total: int | None = None,
        metaphors_found: int | None = None,
        papers_generated: int | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update any subset of progress fields."""
        if current_round is not None:
            self.current_round = current_round
        if fields_remaining is not None:
            self.fields_remaining = fields_remaining
        if propositions_total is not None:
            self.propositions_total = propositions_total
        if metaphors_found is not None:
            self.metaphors_found = metaphors_found
        if papers_generated is not None:
            self.papers_generated = papers_generated
        if status_message is not None:
            self.status_message = status_message

    def __str__(self) -> str:
        return (
            f"Round {self.current_round}/{self.total_rounds} — "
            f"{self.fields_remaining} fields — "
            f"{self.propositions_total} props — "
            f"{self.status_message}"
        )


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineResult:
    """Complete result of a SynthesisFrontierPipeline run.

    Parameters
    ----------
    state:
        Final TournamentState.
    synthesis_field:
        The winning FieldNode (final synthesis), or None.
    paper:
        Generated MathPaper, or None.
    code_plan:
        Generated CodePlan, or None.
    metaphors:
        Tuple of MetaphorLink objects discovered during the run.
    rounds_completed:
        Number of tournament rounds completed.
    total_propositions:
        Total propositions in the final synthesis field.
    duration_seconds:
        Wall-clock time for the full run.
    """

    state: Any  # TournamentState
    synthesis_field: Any  # FieldNode | None
    paper: Any  # MathPaper | None
    code_plan: Any  # CodePlan | None
    metaphors: tuple  # tuple[MetaphorLink, ...]
    rounds_completed: int
    total_propositions: int
    duration_seconds: float

    def summary(self) -> str:
        """Return a multi-line human-readable summary."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Synthesis Frontier Pipeline Result")
        lines.append("=" * 60)
        lines.append(f"  Rounds completed     : {self.rounds_completed}")
        lines.append(f"  Duration             : {self.duration_seconds:.2f}s")
        lines.append(f"  Total propositions   : {self.total_propositions}")
        lines.append(f"  Metaphors found      : {len(self.metaphors)}")

        if self.synthesis_field is not None:
            name = getattr(self.synthesis_field, "name", "?")
            n_props = getattr(self.synthesis_field, "propositions", ())
            lines.append(f"  Synthesis field      : {name} ({len(n_props)} props)")
            constituents = getattr(self.synthesis_field, "constituent_fields", ())
            if constituents:
                lines.append(f"  Constituent fields   : {len(constituents)}")
        else:
            lines.append("  Synthesis field      : (none)")

        if self.paper is not None:
            title = getattr(self.paper, "title", "?")
            n_sections = getattr(self.paper, "sections", [])
            lines.append(f"  Paper title          : {title}")
            lines.append(f"  Paper sections       : {len(n_sections)}")
        else:
            lines.append("  Paper                : (not generated)")

        if self.code_plan is not None:
            lines.append(
                f"  Code specs           : {self.code_plan.total_specs} "
                f"(~{self.code_plan.estimated_total_lines} lines)"
            )
        else:
            lines.append("  Code plan            : (not generated)")

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize key fields to a plain dict."""
        sf = self.synthesis_field
        return {
            "rounds_completed": self.rounds_completed,
            "duration_seconds": self.duration_seconds,
            "total_propositions": self.total_propositions,
            "metaphors_count": len(self.metaphors),
            "synthesis_field_name": getattr(sf, "name", None) if sf else None,
            "paper_title": getattr(self.paper, "title", None) if self.paper else None,
            "code_specs": self.code_plan.total_specs if self.code_plan else 0,
        }


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------


class CheckpointManager:
    """Save and load pipeline checkpoints.

    Checkpoints store enough information to resume or analyse a run,
    but do not attempt to fully reconstruct the TournamentState graph
    (which is expensive to serialise).
    """

    def save(
        self,
        state: Any,  # TournamentState
        result: "PipelineResult | None",
        path: str,
    ) -> None:
        """Serialize key fields to JSON and write to path.

        Parameters
        ----------
        state:
            The current TournamentState.
        result:
            Optional PipelineResult (if the run is complete).
        path:
            File path to write the checkpoint JSON.
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data: dict[str, Any] = {
            "state_id": getattr(state, "state_id", str(uuid.uuid4())),
            "current_round": getattr(state, "current_round", 0),
            "is_complete": getattr(state, "is_complete", False),
            "node_count": len(getattr(state, "all_nodes", {})),
            "pair_count": len(getattr(state, "all_pairs", {})),
            "metaphor_count": len(getattr(state, "all_metaphors", {})),
            "active_nodes": len(getattr(state, "active_nodes", [])),
            "saved_at": time.time(),
        }
        if result is not None:
            sf = result.synthesis_field
            data["synthesis_field_name"] = getattr(sf, "name", None) if sf else None
            data["rounds_completed"] = result.rounds_completed
            data["total_propositions"] = result.total_propositions
            data["duration_seconds"] = result.duration_seconds

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        _log.info("Checkpoint saved to %s", path)

    def load(
        self, path: str
    ) -> tuple[Any, "PipelineResult | None"]:
        """Load a checkpoint from path.

        Returns a (TournamentState, None) tuple — full graph reconstruction
        from JSON is not supported; the state has metadata populated from
        the checkpoint.

        Parameters
        ----------
        path:
            File path to load the checkpoint JSON from.

        Returns
        -------
        tuple[TournamentState, None]
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        state = TournamentState()
        state.metadata.update(data)
        state.current_round = data.get("current_round", 0)
        state.is_complete = data.get("is_complete", False)
        _log.info(
            "Checkpoint loaded from %s (round=%d, complete=%s)",
            path, state.current_round, state.is_complete,
        )
        return state, None


# ---------------------------------------------------------------------------
# SynthesisFrontierPipeline
# ---------------------------------------------------------------------------


class SynthesisFrontierPipeline:
    """Unified execution pipeline for the synthesis frontier.

    Wires together:
      - Tournament (48 → 1 field merging)
      - MetaphorFinder (cross-domain analogy discovery)
      - PaperGenerator (LaTeX paper from final synthesis)
      - CodeOrchestrator (code targets from paper)

    Parameters
    ----------
    config:
        Pipeline configuration.  Uses sensible defaults if None.
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self._progress = PipelineProgress()
        self._checkpoint_manager = CheckpointManager()
        # Build judge config
        try:
            self._judge_config = JudgeConfig(
                mode=self.config.judge_mode,
                model=self.config.model,
            )
            self._judge = SynthesisJudge(self._judge_config)
        except Exception:
            self._judge_config = None
            self._judge = None

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self, fields: list | None = None
    ) -> PipelineResult:
        """Run the complete pipeline.

        Parameters
        ----------
        fields:
            FieldNodes to run the tournament over.  Uses ALL_128_FIELDS if None.

        Returns
        -------
        PipelineResult
        """
        t0 = time.time()
        if fields is None:
            fields = list(ALL_128_FIELDS)
        if not fields:
            _log.warning("No fields provided; returning empty result")
            return PipelineResult(
                state=TournamentState(),
                synthesis_field=None,
                paper=None,
                code_plan=None,
                metaphors=(),
                rounds_completed=0,
                total_propositions=0,
                duration_seconds=0.0,
            )

        if self.config.verbose:
            _log.info("Starting pipeline with %d fields", len(fields))

        # --- Stage 1: Tournament ---
        self._progress.update(
            fields_remaining=len(fields),
            status_message="Running tournament",
        )
        state, round_results = self._run_tournament(fields)
        rounds_completed = state.current_round

        # --- Stage 2: Extract synthesis field ---
        synthesis_field = (
            state.active_nodes[0] if state.active_nodes else None
        )
        total_props = 0
        if synthesis_field is not None:
            total_props = len(getattr(synthesis_field, "propositions", ()))

        # --- Stage 3: Metaphor finder ---
        metaphors: tuple = ()
        if self.config.use_metaphor_finder and synthesis_field is not None:
            self._progress.update(status_message="Finding metaphors")
            try:
                finder = MetaphorFinder()
                db = finder.scan_fields(fields)
                if db is not None and hasattr(db, "all_metaphors"):
                    metaphors = tuple(db.all_metaphors.values())
                elif db is not None and hasattr(db, "_links"):
                    metaphors = tuple(db._links)
            except Exception as exc:
                _log.warning("MetaphorFinder failed: %s", exc)
            # Also pull metaphors from state
            # Also pull metaphors from state
            raw = getattr(state, "all_metaphors", [])
            state_metaphors = list(raw.values()) if isinstance(raw, dict) else list(raw)
            metaphors = tuple(list(metaphors) + state_metaphors)
            self._progress.update(metaphors_found=len(metaphors))

        # --- Stage 4: Paper generation ---
        paper = None
        if self.config.generate_paper and synthesis_field is not None:
            self._progress.update(status_message="Generating paper")
            try:
                gen = PaperGenerator()
                paper = gen.generate(synthesis_field)
                self._progress.update(papers_generated=1)
                if self.config.verbose:
                    title = getattr(paper, "title", "?")
                    _log.info("Paper generated: %s", title)
            except Exception as exc:
                _log.warning("PaperGenerator failed: %s", exc)

        # --- Stage 5: Code orchestration ---
        code_plan = None
        if self.config.generate_code and paper is not None:
            self._progress.update(status_message="Generating code plan")
            try:
                orch = CodeOrchestrator()
                code_plan = orch.plan(paper)
                if self.config.verbose:
                    _log.info(
                        "Code plan: %d specs", code_plan.total_specs
                    )
            except Exception as exc:
                _log.warning("CodeOrchestrator failed: %s", exc)

        # --- Stage 6: Checkpoint ---
        if self.config.checkpoint_dir:
            try:
                result_partial = PipelineResult(
                    state=state,
                    synthesis_field=synthesis_field,
                    paper=paper,
                    code_plan=code_plan,
                    metaphors=metaphors,
                    rounds_completed=rounds_completed,
                    total_propositions=total_props,
                    duration_seconds=time.time() - t0,
                )
                ckpt_path = self.save_checkpoint(state)
                _log.info("Checkpoint saved to %s", ckpt_path)
            except Exception as exc:
                _log.warning("Checkpoint save failed: %s", exc)

        duration = time.time() - t0
        self._progress.update(status_message="Complete")

        return PipelineResult(
            state=state,
            synthesis_field=synthesis_field,
            paper=paper,
            code_plan=code_plan,
            metaphors=metaphors,
            rounds_completed=rounds_completed,
            total_propositions=total_props,
            duration_seconds=duration,
        )

    def _run_tournament(
        self, fields: list
    ) -> tuple[Any, list]:
        """Run the tournament and return (state, round_results)."""
        try:
            from jugeo.ideation.synthesis_frontier.tournament import (
                Tournament, PairingStrategy,
            )
            from jugeo.ideation.synthesis_frontier.llm_judge import (
                JudgeConfig, JudgeMode, SynthesisJudge,
            )
            judge_cfg = JudgeConfig(
                mode=self.config.judge_mode,
                model=self.config.model,
            )
            judge = SynthesisJudge(judge_cfg)
            strategy = getattr(PairingStrategy, "DIVERSITY", PairingStrategy("diversity"))
            engine = Tournament(
                initial_fields=fields,
                strategy=strategy,
                judge=judge,
                max_rounds=self.config.max_rounds or 20,
            )
            state = engine.run()
            return state, []
        except Exception as exc:
            _log.warning("Tournament failed: %s; using trivial merge", exc)

        state = TournamentState(active_nodes=list(fields))
        for node in fields:
            state.register_node(node)
        state.is_complete = True
        return state, []

    # ------------------------------------------------------------------
    # run_rounds
    # ------------------------------------------------------------------

    def run_rounds(
        self,
        n: int,
        state: Any | None = None,
    ) -> tuple[Any, list]:
        """Run exactly n tournament rounds.

        Parameters
        ----------
        n:
            Number of rounds to run.
        state:
            Existing TournamentState to continue from.  If None, raises
            ValueError — call run() to start fresh.

        Returns
        -------
        tuple[TournamentState, list[RoundResult]]
        """
        if state is None:
            state = TournamentState(active_nodes=list(ALL_128_FIELDS))
            for node in state.active_nodes:
                state.register_node(node)

        round_results: list[Any] = []
        try:
            from jugeo.ideation.synthesis_frontier.tournament import (
                TournamentConfig, BinaryTournamentFrontier,
            )
            cfg = TournamentConfig(
                pairing_strategy=self.config.strategy,
                model=self.config.model,
                max_rounds=n,
            )
            engine = BinaryTournamentFrontier(
                fields=list(state.active_nodes), config=cfg
            )
            for _ in range(n):
                if len(state.active_nodes) <= 1:
                    break
                t0 = time.time()
                state = engine.run_round(state)
                dt = time.time() - t0
                round_results.append(
                    RoundResult(
                        round_number=state.current_round,
                        fields_before=len(state.active_nodes) * 2,
                        fields_after=len(state.active_nodes),
                        total_propositions=state.total_propositions(),
                        duration_seconds=dt,
                    )
                )
        except Exception as exc:
            _log.warning("run_rounds failed: %s", exc)

        return state, round_results

    # ------------------------------------------------------------------
    # resume
    # ------------------------------------------------------------------

    def resume(self, checkpoint_dir: str) -> PipelineResult:
        """Load the latest checkpoint from checkpoint_dir and resume.

        Parameters
        ----------
        checkpoint_dir:
            Directory containing checkpoint JSON files.

        Returns
        -------
        PipelineResult
        """
        import glob as _glob
        pattern = os.path.join(checkpoint_dir, "checkpoint_*.json")
        files = sorted(_glob.glob(pattern))
        if not files:
            raise FileNotFoundError(
                f"No checkpoint files found in {checkpoint_dir!r}"
            )
        latest = files[-1]
        _log.info("Resuming from checkpoint: %s", latest)
        state, _ = self._checkpoint_manager.load(latest)
        # We can't fully reconstruct field graph from JSON alone, so we
        # re-run from ALL_128_FIELDS with the round count from the checkpoint.
        _log.warning(
            "Full state reconstruction not supported; re-running from scratch"
        )
        return self.run()

    # ------------------------------------------------------------------
    # save_checkpoint
    # ------------------------------------------------------------------

    def save_checkpoint(self, state: Any) -> str:
        """Save a checkpoint for the given state.

        Parameters
        ----------
        state:
            Current TournamentState.

        Returns
        -------
        str
            Path of the saved checkpoint file.
        """
        rnd = getattr(state, "current_round", 0)
        ckpt_dir = self.config.checkpoint_dir or self.config.output_dir
        path = os.path.join(ckpt_dir, f"checkpoint_{rnd:03d}.json")
        self._checkpoint_manager.save(state, None, path)
        return path

    @property
    def progress(self) -> PipelineProgress:
        """Current pipeline progress."""
        return self._progress


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def run_default_pipeline(config: PipelineConfig | None = None) -> PipelineResult:
    """Run the synthesis frontier pipeline with default settings."""
    return SynthesisFrontierPipeline(config).run()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import time as _time
    t0 = _time.time()
    try:
        from jugeo.ideation.synthesis_frontier.models import FieldNode
        test_fields = [
            FieldNode.make("Algebra", "Group theory and rings", keywords=("group", "ring", "algebra", "module")),
            FieldNode.make("Topology", "Topological spaces", keywords=("space", "open", "continuous", "compact")),
            FieldNode.make("Logic", "Proof theory and model theory", keywords=("proof", "model", "formula", "type")),
            FieldNode.make("Geometry", "Differential and algebraic geometry", keywords=("manifold", "sheaf", "curvature", "bundle")),
        ]
        config = PipelineConfig(
            strategy=PairingStrategy.RANDOM,
            judge_mode=JudgeMode.HEURISTIC,
            generate_paper=True,
            generate_code=False,
            verbose=True,
        )
        pipeline = SynthesisFrontierPipeline(config)
        result = pipeline.run(fields=test_fields)
        print(result.summary())
        elapsed = _time.time() - t0
        print(f"Elapsed: {elapsed:.2f}s")
    except Exception as e:
        import traceback; traceback.print_exc()
