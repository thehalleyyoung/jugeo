#!/usr/bin/env python3
"""Run the synthesis frontier: 48 fields → binary tournament → math paper → code.

Usage:
    python scripts/run_synthesis_frontier.py [OPTIONS]

Options:
    --rounds N          Run only N rounds (default: all 6 rounds to completion)
    --checkpoint DIR    Load from existing checkpoint directory
    --output DIR        Output directory (default: outputs/synthesis/)
    --model MODEL       LLM model to use (default: claude-sonnet-4.6)
    --strategy STRATEGY Pairing strategy: random|similarity|diversity|greedy (default: diversity)
    --no-llm            Use heuristic judge only (no LLM calls)
    --execute-code      Also orchestrate code generation from the paper
    --show-metaphors    Print discovered metaphors
    --show-propositions Print all propositions at each round
    --latex-output FILE Save LaTeX paper to FILE
    --verbose           Verbose output

Example:
    python scripts/run_synthesis_frontier.py --rounds 3 --show-metaphors
    python scripts/run_synthesis_frontier.py --execute-code --output outputs/my_synthesis/

# copilot: synthesis frontier main script — 48 fields → tournament → paper → code
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import argparse          # Command-line argument parsing
import dataclasses       # Dataclass decorator and field() helper
import datetime          # Datetime for timestamps and durations
import json              # JSON serialisation for checkpoints and state
import logging           # Structured logging throughout the pipeline
import math              # Mathematical helpers (ceil, log2, etc.)
import os                # Operating system path and environment utilities
import pathlib           # Object-oriented filesystem paths
import sys               # System-level helpers (argv, exit, stdout)
import time              # Timing helpers for progress tracking
import textwrap          # Text-wrapping for formatted output blocks
import shutil            # High-level file/directory operations
import uuid              # Universally unique identifiers for run IDs

# ---------------------------------------------------------------------------
# Optional synthesis_frontier package imports — graceful fallback stubs so
# the script can be run standalone (demo mode) even when the package is not
# yet installed in the current environment.
# ---------------------------------------------------------------------------
try:
    from synthesis_frontier.fields import FieldRegistry, Field  # type: ignore
    _HAVE_FIELDS = True
except ImportError:
    _HAVE_FIELDS = False
    # Minimal stub so type annotations resolve at runtime.
    class Field:  # type: ignore
        """Stub Field when synthesis_frontier is not installed."""
        def __init__(self, name: str, description: str = "", domain: str = ""):
            self.name = name
            self.description = description
            self.domain = domain
            self.propositions: list = []

    class FieldRegistry:  # type: ignore
        """Stub FieldRegistry."""
        def get_all(self):
            return []

try:
    from synthesis_frontier.pipeline import SynthesisPipeline, PipelineState  # type: ignore
    _HAVE_PIPELINE = True
except ImportError:
    _HAVE_PIPELINE = False

    class PipelineState:  # type: ignore
        """Stub PipelineState."""
        fields: list = []
        round_results: list = []
        metaphors: list = []
        propositions: list = []

    class SynthesisPipeline:  # type: ignore
        """Stub SynthesisPipeline."""
        def __init__(self, *args, **kwargs): pass
        def run_round(self, *args, **kwargs): return None

try:
    from synthesis_frontier.tournament import TournamentEngine  # type: ignore
    _HAVE_TOURNAMENT = True
except ImportError:
    _HAVE_TOURNAMENT = False

    class TournamentEngine:  # type: ignore
        """Stub TournamentEngine."""
        def __init__(self, *args, **kwargs): pass
        def pair_fields(self, fields, strategy="diversity"): return []

try:
    from synthesis_frontier.paper_generator import PaperGenerator, Paper  # type: ignore
    _HAVE_PAPER = True
except ImportError:
    _HAVE_PAPER = False

    class Paper:  # type: ignore
        """Stub Paper."""
        title: str = "Untitled Synthesis"
        abstract: str = ""
        sections: list = []
        theorems: list = []
        latex_source: str = ""

    class PaperGenerator:  # type: ignore
        """Stub PaperGenerator."""
        def generate(self, state, *args, **kwargs): return Paper()

try:
    from synthesis_frontier.code_orchestrator import CodeOrchestrator, CodePlan  # type: ignore
    _HAVE_CODE = True
except ImportError:
    _HAVE_CODE = False

    class CodePlan:  # type: ignore
        """Stub CodePlan."""
        modules: list = []
        entry_point: str = ""

    class CodeOrchestrator:  # type: ignore
        """Stub CodeOrchestrator."""
        def plan(self, paper): return CodePlan()
        def generate(self, plan): return {}

try:
    from synthesis_frontier.metaphor_finder import MetaphorFinder  # type: ignore
    _HAVE_METAPHOR = True
except ImportError:
    _HAVE_METAPHOR = False

    class MetaphorFinder:  # type: ignore
        """Stub MetaphorFinder."""
        def find(self, field_a, field_b): return []

try:
    from synthesis_frontier.llm_judge import LLMJudge  # type: ignore
    _HAVE_LLM_JUDGE = True
except ImportError:
    _HAVE_LLM_JUDGE = False

    class LLMJudge:  # type: ignore
        """Stub LLMJudge."""
        def score(self, field_a, field_b): return 0.5

# ---------------------------------------------------------------------------
# Module-level metadata
# ---------------------------------------------------------------------------
__version__ = "0.1.0"
__author__  = "JuGeo Synthesis System"

# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR  = "outputs/synthesis/"
DEFAULT_MODEL       = "claude-sonnet-4.6"
DEFAULT_STRATEGY    = "diversity"

# ---------------------------------------------------------------------------
# Human-readable description of the synthesis frontier system
# ---------------------------------------------------------------------------
SYNTHESIS_FRONTIER_DESCRIPTION = """\
The Synthesis Frontier is a multi-round binary tournament that progressively
merges 48 distinct mathematical and scientific fields into a single unified
theory.  At each round, pairs of fields are evaluated by an LLM judge that
scores the potential for deep structural integration.  The merger produces:

  • Bridging metaphors  — shared conceptual structures that span both fields.
  • Bridge theorems     — formal propositions that only become provable once
                          the vocabulary of both fields is available.
  • Cross-domain props  — corollaries and conjectures that emerge naturally
                          from the merged ontology.

After all 6 rounds (48 → 24 → 12 → 6 → 3 → 1) the winning unified field is
fed into a LaTeX paper generator that structures the collected theorems,
metaphors, and bridge proofs into a publication-ready mathematics paper.
Optionally, a code orchestrator transforms the paper's algorithmic content
into executable Python modules.

The system is designed to be run incrementally: each round writes a JSON
checkpoint so that long runs can be resumed without re-doing expensive LLM
calls.
"""

# ---------------------------------------------------------------------------
# Canonical list of the 48 seed fields used in the synthesis tournament.
# Each entry is a dict with three keys:
#   name        — short identifier used in the tournament bracket
#   description — one-sentence summary of what the field studies
#   domain      — broad domain label (mathematics / physics / cs / biology /
#                 philosophy / linguistics)
# ---------------------------------------------------------------------------
FORTY_EIGHT_FIELDS: list[dict] = [
    # ── Mathematics ──────────────────────────────────────────────────────────
    {
        "name": "type_theory",
        "description": (
            "Studies formal systems in which every expression has a type; "
            "underpins both functional programming and constructive mathematics."
        ),
        "domain": "mathematics",
    },
    {
        "name": "category_theory",
        "description": (
            "Abstract framework of objects and morphisms that unifies diverse "
            "mathematical structures through functors and natural transformations."
        ),
        "domain": "mathematics",
    },
    {
        "name": "homotopy_type_theory",
        "description": (
            "Synthesis of type theory and homotopy theory, interpreting types "
            "as topological spaces and proofs as paths."
        ),
        "domain": "mathematics",
    },
    {
        "name": "algebraic_topology",
        "description": (
            "Uses algebraic invariants such as homology and homotopy groups to "
            "classify topological spaces up to continuous deformation."
        ),
        "domain": "mathematics",
    },
    {
        "name": "differential_geometry",
        "description": (
            "Studies smooth manifolds and the geometric structures defined on "
            "them — curvature, connections, geodesics."
        ),
        "domain": "mathematics",
    },
    {
        "name": "algebraic_geometry",
        "description": (
            "Investigates solution sets of polynomial equations (varieties) "
            "using both commutative algebra and geometric intuition."
        ),
        "domain": "mathematics",
    },
    {
        "name": "number_theory",
        "description": (
            "Properties of integers, prime numbers, Diophantine equations, and "
            "deep connections to complex analysis via L-functions."
        ),
        "domain": "mathematics",
    },
    {
        "name": "representation_theory",
        "description": (
            "Realises abstract algebraic structures (groups, algebras) as "
            "linear transformations on vector spaces."
        ),
        "domain": "mathematics",
    },
    {
        "name": "homological_algebra",
        "description": (
            "Provides tools — exact sequences, derived functors, spectral "
            "sequences — to measure the failure of exactness."
        ),
        "domain": "mathematics",
    },
    {
        "name": "model_theory",
        "description": (
            "Studies the relationship between formal logical theories and "
            "their mathematical models (structures that satisfy those theories)."
        ),
        "domain": "mathematics",
    },
    {
        "name": "proof_theory",
        "description": (
            "Analyses formal proofs as mathematical objects; investigates "
            "consistency, cut-elimination, and ordinal proof-theoretic strength."
        ),
        "domain": "mathematics",
    },
    {
        "name": "set_theory",
        "description": (
            "Foundation of mathematics based on sets; explores large cardinals, "
            "forcing, inner models, and independence phenomena."
        ),
        "domain": "mathematics",
    },
    # ── Physics ───────────────────────────────────────────────────────────────
    {
        "name": "quantum_field_theory",
        "description": (
            "Combines quantum mechanics and special relativity; the language of "
            "the Standard Model of particle physics."
        ),
        "domain": "physics",
    },
    {
        "name": "general_relativity",
        "description": (
            "Einstein's geometric theory of gravitation; spacetime curvature "
            "encodes gravity and is sourced by the stress-energy tensor."
        ),
        "domain": "physics",
    },
    {
        "name": "statistical_mechanics",
        "description": (
            "Derives macroscopic thermodynamic properties from the statistical "
            "behaviour of large ensembles of microscopic particles."
        ),
        "domain": "physics",
    },
    {
        "name": "condensed_matter_physics",
        "description": (
            "Studies emergent phenomena in large collections of interacting "
            "particles — superconductivity, topological phases, etc."
        ),
        "domain": "physics",
    },
    {
        "name": "string_theory",
        "description": (
            "Theoretical framework replacing point particles with one-dimensional "
            "strings, aiming to unify all fundamental forces."
        ),
        "domain": "physics",
    },
    {
        "name": "topological_quantum_field_theory",
        "description": (
            "Quantum field theories whose observables depend only on the topology "
            "of spacetime; bridges physics and low-dimensional topology."
        ),
        "domain": "physics",
    },
    # ── Computer Science ──────────────────────────────────────────────────────
    {
        "name": "computability_theory",
        "description": (
            "Characterises which problems are solvable by algorithms; studies "
            "Turing machines, halting, reducibility, and degree theory."
        ),
        "domain": "cs",
    },
    {
        "name": "complexity_theory",
        "description": (
            "Classifies computational problems by the resources (time, space) "
            "required; P vs NP is the central open question."
        ),
        "domain": "cs",
    },
    {
        "name": "information_theory",
        "description": (
            "Quantifies information, redundancy, and channel capacity; "
            "Shannon entropy is the central measure."
        ),
        "domain": "cs",
    },
    {
        "name": "formal_language_theory",
        "description": (
            "Studies grammars, automata, and the Chomsky hierarchy; underlies "
            "compiler design and natural language processing."
        ),
        "domain": "cs",
    },
    {
        "name": "lambda_calculus",
        "description": (
            "Formal system for expressing computation via function abstraction "
            "and application; the core of functional programming."
        ),
        "domain": "cs",
    },
    {
        "name": "game_theory",
        "description": (
            "Mathematical modelling of strategic interaction among rational "
            "agents; equilibria, mechanism design, cooperative games."
        ),
        "domain": "cs",
    },
    {
        "name": "probabilistic_graphical_models",
        "description": (
            "Represent joint probability distributions over large sets of "
            "variables using graphs (Bayesian networks, Markov random fields)."
        ),
        "domain": "cs",
    },
    {
        "name": "deep_learning_theory",
        "description": (
            "Mathematical underpinnings of deep neural networks — expressiveness, "
            "optimisation landscape, implicit regularisation."
        ),
        "domain": "cs",
    },
    # ── Probability & Stochastic Processes ────────────────────────────────────
    {
        "name": "stochastic_calculus",
        "description": (
            "Calculus for functions of stochastic processes; Itô integral, "
            "stochastic differential equations, Girsanov theorem."
        ),
        "domain": "mathematics",
    },
    {
        "name": "ergodic_theory",
        "description": (
            "Studies measure-preserving dynamical systems and their long-run "
            "statistical behaviour; connects probability and dynamics."
        ),
        "domain": "mathematics",
    },
    {
        "name": "random_matrix_theory",
        "description": (
            "Properties of matrices with random entries; eigenvalue statistics "
            "appear in nuclear physics, number theory, and machine learning."
        ),
        "domain": "mathematics",
    },
    # ── Geometry & Topology ───────────────────────────────────────────────────
    {
        "name": "symplectic_geometry",
        "description": (
            "Studies manifolds equipped with a closed non-degenerate 2-form; "
            "the natural setting for classical Hamiltonian mechanics."
        ),
        "domain": "mathematics",
    },
    {
        "name": "knot_theory",
        "description": (
            "Classifies embeddings of circles in 3-space; knot invariants connect "
            "to quantum groups and 3-manifold topology."
        ),
        "domain": "mathematics",
    },
    {
        "name": "geometric_group_theory",
        "description": (
            "Studies groups via the geometry of the spaces they act on; "
            "word metrics, Cayley graphs, hyperbolic groups."
        ),
        "domain": "mathematics",
    },
    # ── Logic & Foundations ───────────────────────────────────────────────────
    {
        "name": "modal_logic",
        "description": (
            "Extends classical logic with operators for necessity and possibility; "
            "semantics via possible-world Kripke frames."
        ),
        "domain": "philosophy",
    },
    {
        "name": "intuitionistic_logic",
        "description": (
            "Constructive logic rejecting the law of excluded middle; "
            "the Curry-Howard correspondence links it to type theory."
        ),
        "domain": "mathematics",
    },
    # ── Biology & Neuroscience ─────────────────────────────────────────────────
    {
        "name": "evolutionary_game_theory",
        "description": (
            "Applies game-theoretic equilibrium concepts to biological evolution; "
            "evolutionarily stable strategies, replicator dynamics."
        ),
        "domain": "biology",
    },
    {
        "name": "computational_neuroscience",
        "description": (
            "Mathematical modelling of neural computation; spiking networks, "
            "Hodgkin-Huxley, predictive coding, neural field theories."
        ),
        "domain": "biology",
    },
    {
        "name": "systems_biology",
        "description": (
            "Network-level modelling of biological systems; gene regulatory "
            "networks, metabolic flux analysis, robustness."
        ),
        "domain": "biology",
    },
    # ── Linguistics & Cognitive Science ───────────────────────────────────────
    {
        "name": "formal_semantics",
        "description": (
            "Models natural language meaning using tools from logic and "
            "model theory; truth conditions, compositionality."
        ),
        "domain": "linguistics",
    },
    {
        "name": "cognitive_linguistics",
        "description": (
            "Studies language as a window into cognitive structure; "
            "conceptual metaphor, embodied cognition, construction grammar."
        ),
        "domain": "linguistics",
    },
    # ── Economics & Social Science ────────────────────────────────────────────
    {
        "name": "mechanism_design",
        "description": (
            "Reverse game theory: designs rules of a game to achieve desired "
            "social outcomes; auctions, voting, market design."
        ),
        "domain": "cs",
    },
    {
        "name": "social_choice_theory",
        "description": (
            "Studies collective decision-making; Arrow's impossibility theorem, "
            "voting rules, preference aggregation."
        ),
        "domain": "philosophy",
    },
    # ── Control Theory & Dynamical Systems ────────────────────────────────────
    {
        "name": "control_theory",
        "description": (
            "Design of feedback systems; stability (Lyapunov), controllability, "
            "optimal control (Pontryagin, Hamilton-Jacobi-Bellman)."
        ),
        "domain": "cs",
    },
    {
        "name": "dynamical_systems",
        "description": (
            "Studies the long-term behaviour of evolving systems; attractors, "
            "bifurcations, chaos, structural stability."
        ),
        "domain": "mathematics",
    },
    # ── Applied Mathematics ────────────────────────────────────────────────────
    {
        "name": "convex_optimisation",
        "description": (
            "Theory and algorithms for minimising convex functions over convex "
            "sets; duality, interior-point methods, ADMM."
        ),
        "domain": "mathematics",
    },
    {
        "name": "tropical_geometry",
        "description": (
            "Algebraic geometry over the tropical semiring (min, +); piecewise-"
            "linear shadows of classical varieties."
        ),
        "domain": "mathematics",
    },
    {
        "name": "noncommutative_geometry",
        "description": (
            "Alain Connes's programme extending geometric concepts to "
            "noncommutative C*-algebras; spectral triples, cyclic cohomology."
        ),
        "domain": "mathematics",
    },
    {
        "name": "topos_theory",
        "description": (
            "Categorical generalisation of set-theoretic topology; every topos "
            "has an internal logic, bridging geometry and logic."
        ),
        "domain": "mathematics",
    },
]

# ---------------------------------------------------------------------------
# Mapping from field name → broad domain label.  This dict is used to colour-
# code terminal output and to group fields in the printed table.
# ---------------------------------------------------------------------------
FIELD_DOMAINS: dict[str, str] = {f["name"]: f["domain"] for f in FORTY_EIGHT_FIELDS}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RoundResult:
    """Holds all data produced by a single tournament round.

    Attributes
    ----------
    round_num:
        1-based round number (round 1 merges 48→24, round 6 merges 2→1).
    fields_before:
        Number of fields entering this round.
    fields_after:
        Number of fields remaining after this round's mergers.
    pairs_merged:
        List of 2-tuples (name_a, name_b) of fields that were merged.
    integration_scores:
        Mapping from (name_a, name_b) pair to float integration score [0, 1].
    new_metaphors:
        All metaphors discovered during this round.
    new_bridge_theorems:
        All bridge theorems created during this round.
    total_propositions:
        Total proposition count after this round completes.
    props_delta:
        Net increase in propositions this round.
    elapsed_seconds:
        Wall-clock time for this round in seconds.
    timestamp:
        ISO-8601 UTC timestamp when the round completed.
    """

    round_num: int
    fields_before: int
    fields_after: int
    pairs_merged: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    integration_scores: dict[str, float] = dataclasses.field(default_factory=dict)
    new_metaphors: list[str] = dataclasses.field(default_factory=list)
    new_bridge_theorems: list[str] = dataclasses.field(default_factory=list)
    total_propositions: int = 0
    props_delta: int = 0
    elapsed_seconds: float = 0.0
    timestamp: str = dataclasses.field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z"
    )


@dataclasses.dataclass
class OutputConfig:
    """Holds all user-configurable output settings for a synthesis run.

    Attributes
    ----------
    output_dir:
        Filesystem path where all run artefacts are written.
    model:
        Identifier of the LLM model used for scoring and generation.
    strategy:
        Pairing strategy for tournament bracket construction.
    use_llm:
        Whether to call the real LLM judge (False → heuristic fallback).
    execute_code:
        Whether to run the code orchestrator after paper generation.
    show_metaphors:
        Print the top metaphors after each round.
    show_propositions:
        Print all propositions at the end of each round.
    latex_output:
        If not None, path where the LaTeX paper is written.
    verbose:
        Enable verbose logging to stderr.
    max_rounds:
        Maximum number of rounds to run (None = run to completion).
    checkpoint_dir:
        If not None, load state from this checkpoint directory before running.
    run_id:
        Unique identifier for this run (auto-generated UUID).
    """

    output_dir: str = DEFAULT_OUTPUT_DIR
    model: str = DEFAULT_MODEL
    strategy: str = DEFAULT_STRATEGY
    use_llm: bool = True
    execute_code: bool = False
    show_metaphors: bool = False
    show_propositions: bool = False
    latex_output: str | None = None
    verbose: bool = False
    max_rounds: int | None = None
    checkpoint_dir: str | None = None
    run_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4())[:8])


# ---------------------------------------------------------------------------
# ASCII / Unicode banner
# ---------------------------------------------------------------------------

def print_ascii_banner(version: str = __version__) -> None:
    """Print a decorative box-drawing banner for the synthesis frontier.

    The banner uses Unicode box-drawing characters (═ ╔ ╗ ╚ ╝ ║) to create
    a professional-looking title block that is printed once at startup.

    Parameters
    ----------
    version:
        Version string to embed in the banner (defaults to module __version__).
    """
    # Width of the inner content area (between the left and right borders).
    width = 60

    # Top border
    print(f"\n╔{'═' * width}╗")

    # Blank padding row
    print(f"║{' ' * width}║")

    # Title line — centred within the box
    title = "SYNTHESIS FRONTIER"
    print(f"║{title.center(width)}║")

    # Subtitle line
    subtitle = "48 Fields → Binary Tournament → Math Paper → Code"
    print(f"║{subtitle.center(width)}║")

    # Blank row
    print(f"║{' ' * width}║")

    # Divider
    print(f"╠{'═' * width}╣")

    # Version and author information
    ver_line = f"Version {version}  ·  {__author__}"
    print(f"║{ver_line.center(width)}║")

    # Timestamp of the current run
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"║{ts.center(width)}║")

    # Blank padding row
    print(f"║{' ' * width}║")

    # Bottom border
    print(f"╚{'═' * width}╝\n")


# ---------------------------------------------------------------------------
# TournamentPrinter
# ---------------------------------------------------------------------------

class TournamentPrinter:
    """Rich terminal output for every stage of the synthesis tournament.

    All formatting decisions are centralised here so that the rest of the
    script can focus on logic.  The printer uses Unicode box-drawing characters,
    ANSI-style star ratings, and progress bars to give the user a clear sense
    of tournament progress at a glance.

    Methods
    -------
    print_banner()
        Delegates to :func:`print_ascii_banner`.
    print_fields_table(fields)
        Tabular overview of all seed fields grouped by domain.
    print_round_header(round_num, before, after)
        Section divider shown before each round's mergers begin.
    print_merge_result(...)
        Per-merge summary including score, metaphors, bridge theorem count.
    print_round_summary(result)
        Aggregate statistics for a completed round.
    print_metaphors(metaphors, top_n)
        Numbered list of the top N metaphors discovered so far.
    print_propositions(field)
        Full list of propositions attached to a merged field.
    print_tournament_summary(state)
        Final box-drawing summary after the last round completes.
    print_paper_stats(paper)
        Word count, theorem count, and section breakdown of a generated paper.
    print_progress_bar(current, total, width)
        Inline ASCII progress bar.
    stars(score)
        Convert a float score in [0, 1] to a ⭐ string.
    print_code_plan(plan)
        Tree-style summary of the generated code plan.
    """

    # Number of columns available for output (fall back to 80 if undetectable).
    _TERM_WIDTH: int = shutil.get_terminal_size(fallback=(80, 24)).columns

    def print_banner(self) -> None:
        """Delegate to the module-level banner function."""
        print_ascii_banner()

    def print_fields_table(self, fields: list) -> None:
        """Print a two-column table of all seed fields, grouped by domain.

        Parameters
        ----------
        fields:
            List of Field objects or dicts with at least 'name' and 'domain'
            keys.  If dicts are passed, this method handles both styles.
        """
        print("┌─────────────────────────────── Seed Fields ───────────────────────────────┐")
        print(f"│ {'#':<4} {'Field Name':<28} {'Domain':<14} {'Description':<28}│")
        print("├────────────────────────────────────────────────────────────────────────────┤")

        for idx, f in enumerate(fields, start=1):
            # Support both dict and Field-object inputs.
            if isinstance(f, dict):
                name   = f.get("name", "unknown")
                domain = f.get("domain", "—")
                desc   = f.get("description", "")
            else:
                name   = getattr(f, "name", "unknown")
                domain = getattr(f, "domain", "—")
                desc   = getattr(f, "description", "")

            # Truncate long names and descriptions for the fixed-width table.
            name_col = name[:26]
            desc_col = desc[:26]
            print(f"│ {idx:<4} {name_col:<28} {domain:<14} {desc_col:<28}│")

        print("└────────────────────────────────────────────────────────────────────────────┘")
        print(f"  Total: {len(fields)} fields across "
              f"{len(set(FIELD_DOMAINS.get(f['name'] if isinstance(f, dict) else f.name, '?') for f in fields))} domains\n")

    def print_round_header(self, round_num: int, before: int, after: int) -> None:
        """Print the section divider that precedes each tournament round.

        Parameters
        ----------
        round_num:
            1-based round index.
        before:
            Number of fields at the start of this round.
        after:
            Expected number of fields after this round completes.
        """
        # Decorative double-line divider, full terminal width.
        bar = "═" * min(self._TERM_WIDTH, 60)
        print(f"\n{bar}")
        print(f"  Round {round_num}: {before} fields → {after} merged fields")
        print(f"{bar}")

    def print_merge_result(
        self,
        field_a_name: str,
        field_b_name: str,
        score: float,
        metaphors: list,
        bridges: list,
        props_before: int,
        props_after: int,
    ) -> None:
        """Print the result of a single pairwise merge operation.

        This is the most detailed output in the tournament — it shows exactly
        what happened when two fields were fused together.

        Parameters
        ----------
        field_a_name:
            Name of the first (left) field in the merge.
        field_b_name:
            Name of the second (right) field in the merge.
        score:
            Integration score in [0, 1] from the LLM judge.
        metaphors:
            List of metaphor strings found by the MetaphorFinder.
        bridges:
            List of bridge theorem strings created during this merge.
        props_before:
            Total proposition count before this merge.
        props_after:
            Total proposition count after this merge.
        """
        # Compute derived statistics.
        props_new = props_after - props_before
        star_str  = self.stars(score)

        print(f"\n  Pairing: {field_a_name} × {field_b_name}")
        print(f"    Integration score : {score:.2f}  {star_str}")
        print(f"    Metaphors found   : {len(metaphors)}")
        print(f"    Bridge theorems   : {len(bridges)}")
        print(f"    Propositions      : {props_before}+{props_before} → {props_after} "
              f"({props_new} new bridge theorems)")

    def print_round_summary(self, result: RoundResult) -> None:
        """Print aggregate statistics after all merges in a round complete.

        Parameters
        ----------
        result:
            The :class:`RoundResult` produced by this round.
        """
        print(f"\n  ── Round {result.round_num} summary ──────────────────────────")
        print(f"     Fields remaining : {result.fields_after}")
        print(f"     Merges performed : {len(result.pairs_merged)}")
        print(f"     New metaphors    : {len(result.new_metaphors)}")
        print(f"     New bridge thms  : {len(result.new_bridge_theorems)}")
        print(f"     Total props      : {result.total_propositions}  "
              f"(+{result.props_delta} this round)")
        print(f"     Elapsed          : {format_duration(result.elapsed_seconds)}")

    def print_metaphors(self, metaphors: list, top_n: int = 3) -> None:
        """Print the top N metaphors discovered so far in the tournament.

        Parameters
        ----------
        metaphors:
            Full list of metaphor strings (or dicts with a 'text' key).
        top_n:
            How many metaphors to display (default: 3).
        """
        if not metaphors:
            print("  (no metaphors discovered yet)")
            return

        print(f"\n  Top {min(top_n, len(metaphors))} metaphors:")
        for i, m in enumerate(metaphors[:top_n], start=1):
            # Handle both plain strings and dict-style metaphors.
            text = m["text"] if isinstance(m, dict) else str(m)
            wrapped = textwrap.fill(text, width=72, initial_indent=f"    {i}. ",
                                    subsequent_indent="       ")
            print(wrapped)

    def print_propositions(self, field) -> None:
        """Print every proposition attached to a field after a merge.

        Parameters
        ----------
        field:
            A Field object (or dict) whose propositions are to be printed.
        """
        # Extract propositions — support both attribute and dict access.
        if isinstance(field, dict):
            props = field.get("propositions", [])
            name  = field.get("name", "unknown")
        else:
            props = getattr(field, "propositions", [])
            name  = getattr(field, "name", "unknown")

        print(f"\n  Propositions for '{name}' ({len(props)} total):")
        if not props:
            print("    (none)")
            return
        for i, p in enumerate(props, start=1):
            text = p["statement"] if isinstance(p, dict) else str(p)
            print(f"    {i:>3}. {text}")

    def print_tournament_summary(self, state) -> None:
        """Print the final box-drawing summary block after the last round.

        Parameters
        ----------
        state:
            The :class:`PipelineState` (or stub equivalent) after all rounds.
        """
        # Extract totals — be defensive about attribute existence.
        total_rounds    = len(getattr(state, "round_results", []))
        total_metaphors = len(getattr(state, "metaphors", []))
        total_props     = len(getattr(state, "propositions", []))
        final_fields    = getattr(state, "fields", [])

        width = 56
        print(f"\n╔{'═' * width}╗")
        print(f"║{'TOURNAMENT COMPLETE'.center(width)}║")
        print(f"╠{'═' * width}╣")
        print(f"║  {'Rounds completed:':<30} {total_rounds:>22}  ║")
        print(f"║  {'Total metaphors:':<30} {total_metaphors:>22}  ║")
        print(f"║  {'Total propositions:':<30} {total_props:>22}  ║")
        print(f"║  {'Fields remaining:':<30} {len(final_fields):>22}  ║")
        print(f"╚{'═' * width}╝\n")

    def print_paper_stats(self, paper) -> None:
        """Print word count, theorem count, and section list for a paper.

        Parameters
        ----------
        paper:
            A :class:`Paper` object (or stub).
        """
        # Safely extract attributes.
        title    = getattr(paper, "title", "Untitled")
        sections = getattr(paper, "sections", [])
        theorems = getattr(paper, "theorems", [])
        latex    = getattr(paper, "latex_source", "")

        # Approximate word count from LaTeX source.
        word_count = len(latex.split()) if latex else 0

        print("  ── Generated Paper ─────────────────────────────")
        print(f"     Title     : {title}")
        print(f"     Sections  : {len(sections)}")
        print(f"     Theorems  : {len(theorems)}")
        print(f"     ~Words    : {word_count}")

    def print_progress_bar(self, current: int, total: int, width: int = 40) -> None:
        """Print an inline ASCII progress bar.

        Parameters
        ----------
        current:
            Number of completed steps.
        total:
            Total number of steps.
        width:
            Character width of the filled bar portion (default 40).
        """
        if total <= 0:
            # Avoid ZeroDivisionError.
            print(f"  [{'?' * width}] 0/0")
            return

        # Compute how many characters should be filled.
        filled = int(width * current / total)
        bar    = "█" * filled + "░" * (width - filled)
        pct    = 100 * current / total
        print(f"  [{bar}] {current}/{total} ({pct:.0f}%)")

    @staticmethod
    def stars(score: float) -> str:
        """Convert a float integration score in [0, 1] to a star rating string.

        The mapping is:
            0.00–0.19  →  (no stars)
            0.20–0.39  →  ⭐
            0.40–0.59  →  ⭐⭐
            0.60–0.74  →  ⭐⭐⭐
            0.75–0.89  →  ⭐⭐⭐⭐
            0.90–1.00  →  ⭐⭐⭐⭐⭐

        Parameters
        ----------
        score:
            A float in the range [0.0, 1.0].

        Returns
        -------
        str
            A string of zero to five ⭐ characters.
        """
        if score >= 0.90:
            return "⭐⭐⭐⭐⭐"
        elif score >= 0.75:
            return "⭐⭐⭐⭐"
        elif score >= 0.60:
            return "⭐⭐⭐"
        elif score >= 0.40:
            return "⭐⭐"
        elif score >= 0.20:
            return "⭐"
        else:
            return "(no stars)"

    def print_code_plan(self, plan) -> None:
        """Print a tree-style summary of the generated code plan.

        Parameters
        ----------
        plan:
            A :class:`CodePlan` object (or stub) with a ``modules`` list and
            an ``entry_point`` attribute.
        """
        modules     = getattr(plan, "modules", [])
        entry_point = getattr(plan, "entry_point", "main.py")

        print("\n  ── Code Generation Plan ────────────────────────")
        print(f"     Entry point : {entry_point}")
        print(f"     Modules     : {len(modules)}")
        for i, mod in enumerate(modules):
            # Each module may be a string name or a dict.
            name = mod["name"] if isinstance(mod, dict) else str(mod)
            connector = "├──" if i < len(modules) - 1 else "└──"
            print(f"       {connector} {name}")


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Tracks wall-clock timing across tournament rounds.

    This class records when each round started and ended, then uses that
    history to produce an estimated time remaining and a human-readable
    summary string.

    Parameters
    ----------
    total_rounds:
        Total number of rounds to be run in this tournament.

    Attributes
    ----------
    total_rounds:
        As supplied.
    _round_start_times:
        Dict mapping round_num → start timestamp (time.monotonic()).
    _round_durations:
        Dict mapping round_num → elapsed seconds for completed rounds.
    _round_props:
        Dict mapping round_num → proposition count at end of round.
    _run_start:
        time.monotonic() value when the tracker was created.
    """

    def __init__(self, total_rounds: int) -> None:
        """Initialise the tracker.

        Parameters
        ----------
        total_rounds:
            Number of rounds planned for this run.
        """
        # Store the total so we can compute progress fraction.
        self.total_rounds: int = total_rounds

        # Per-round timing data.
        self._round_start_times: dict[int, float] = {}
        self._round_durations: dict[int, float]   = {}
        self._round_props: dict[int, int]          = {}

        # Record the absolute start of the entire run.
        self._run_start: float = time.monotonic()

    def start_round(self, round_num: int) -> None:
        """Record the start time for a round.

        Parameters
        ----------
        round_num:
            1-based round identifier.
        """
        self._round_start_times[round_num] = time.monotonic()

    def end_round(self, round_num: int, fields_remaining: int, props_count: int) -> None:
        """Record the completion of a round and store its timing.

        Parameters
        ----------
        round_num:
            1-based round identifier (must have been started first).
        fields_remaining:
            Number of fields left after this round (unused internally, but
            stored for future use in adaptive scheduling).
        props_count:
            Total proposition count at the end of this round.
        """
        start = self._round_start_times.get(round_num, self._run_start)
        elapsed = time.monotonic() - start
        self._round_durations[round_num] = elapsed
        self._round_props[round_num]     = props_count

    def estimated_remaining_seconds(self) -> float:
        """Return an estimate of how many seconds remain in the tournament.

        Uses the mean duration of completed rounds to extrapolate.

        Returns
        -------
        float
            Estimated seconds remaining, or 0.0 if the run has already
            finished or no rounds have completed yet.
        """
        completed = len(self._round_durations)
        if completed == 0:
            # No data yet — cannot estimate.
            return 0.0

        # Mean duration of completed rounds.
        mean_duration = sum(self._round_durations.values()) / completed

        # Rounds still to run.
        rounds_left = max(0, self.total_rounds - completed)
        return mean_duration * rounds_left

    def elapsed_total(self) -> float:
        """Return total elapsed seconds since the tracker was created.

        Returns
        -------
        float
            Wall-clock seconds since ``__init__`` was called.
        """
        return time.monotonic() - self._run_start

    def summary(self) -> str:
        """Return a multi-line human-readable progress summary.

        Returns
        -------
        str
            A string showing completed/total rounds, elapsed time, and an
            estimated time remaining.
        """
        completed = len(self._round_durations)
        elapsed   = self.elapsed_total()
        eta       = self.estimated_remaining_seconds()

        lines = [
            f"  Progress : {completed}/{self.total_rounds} rounds complete",
            f"  Elapsed  : {format_duration(elapsed)}",
            f"  ETA      : {format_duration(eta)}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ResultFormatter
# ---------------------------------------------------------------------------

class ResultFormatter:
    """Formats final synthesis results into human-readable strings.

    This class is separate from :class:`TournamentPrinter` because it is
    designed to produce *storable* string outputs (written to files) rather
    than live terminal output.  It consumes the same state objects but
    returns strings rather than printing directly.

    Methods
    -------
    format_synthesis_summary(state) -> str
        A compact textual summary of the full tournament state.
    format_evidence_injections(records) -> str
        Formatted list of evidence injection records (used in paper gen).
    format_paper_summary(paper) -> str
        Multi-line paper summary suitable for saving to a README.
    """

    def format_synthesis_summary(self, state) -> str:
        """Build a multi-line textual summary of the finished tournament.

        Parameters
        ----------
        state:
            The :class:`PipelineState` (or stub) after tournament completion.

        Returns
        -------
        str
            A human-readable summary block.
        """
        # Safely extract attributes with sensible defaults.
        round_results   = getattr(state, "round_results", [])
        metaphors       = getattr(state, "metaphors", [])
        propositions    = getattr(state, "propositions", [])
        fields          = getattr(state, "fields", [])

        lines = [
            "=" * 60,
            "SYNTHESIS RUN SUMMARY",
            "=" * 60,
            f"Rounds completed     : {len(round_results)}",
            f"Final fields         : {len(fields)}",
            f"Total metaphors      : {len(metaphors)}",
            f"Total propositions   : {len(propositions)}",
            "",
        ]

        # Per-round breakdown.
        lines.append("Per-round statistics:")
        for rr in round_results:
            if isinstance(rr, RoundResult):
                lines.append(
                    f"  Round {rr.round_num}: "
                    f"{rr.fields_before}→{rr.fields_after} fields, "
                    f"+{rr.props_delta} props, "
                    f"{format_duration(rr.elapsed_seconds)}"
                )
        lines.append("=" * 60)
        return "\n".join(lines)

    def format_evidence_injections(self, records: list) -> str:
        """Format a list of evidence injection records for display.

        Evidence injections are external data points (papers, datasets, etc.)
        that were injected into the tournament to guide the LLM judge.

        Parameters
        ----------
        records:
            List of dicts with at least 'source', 'field', and 'excerpt' keys.

        Returns
        -------
        str
            A numbered list of evidence injections.
        """
        if not records:
            return "(no evidence injections)"

        lines = [f"Evidence Injections ({len(records)} total):"]
        for i, rec in enumerate(records, start=1):
            source  = rec.get("source", "unknown") if isinstance(rec, dict) else str(rec)
            field   = rec.get("field", "—")         if isinstance(rec, dict) else "—"
            excerpt = rec.get("excerpt", "")         if isinstance(rec, dict) else ""
            lines.append(f"  {i:>3}. [{field}] {source}")
            if excerpt:
                # Indent the excerpt under the source line.
                wrapped = textwrap.fill(excerpt, width=68,
                                        initial_indent="        ",
                                        subsequent_indent="        ")
                lines.append(wrapped)
        return "\n".join(lines)

    def format_paper_summary(self, paper) -> str:
        """Build a README-style summary of a generated paper.

        Parameters
        ----------
        paper:
            A :class:`Paper` object (or stub).

        Returns
        -------
        str
            Multi-line summary suitable for writing to outputs/summary.txt.
        """
        title    = getattr(paper, "title", "Untitled Synthesis")
        abstract = getattr(paper, "abstract", "")
        sections = getattr(paper, "sections", [])
        theorems = getattr(paper, "theorems", [])
        latex    = getattr(paper, "latex_source", "")

        word_count = len(latex.split()) if latex else 0

        lines = [
            "GENERATED PAPER",
            "=" * 60,
            f"Title    : {title}",
            f"Sections : {len(sections)}",
            f"Theorems : {len(theorems)}",
            f"~Words   : {word_count}",
            "",
            "Abstract:",
        ]
        if abstract:
            lines.append(textwrap.fill(abstract, width=72,
                                       initial_indent="  ",
                                       subsequent_indent="  "))
        else:
            lines.append("  (abstract not yet generated)")

        lines.append("")
        lines.append("Sections:")
        for i, sec in enumerate(sections, start=1):
            sec_title = sec.get("title", f"Section {i}") if isinstance(sec, dict) else str(sec)
            lines.append(f"  {i}. {sec_title}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone helper functions
# ---------------------------------------------------------------------------

def setup_output_dir(path: str) -> pathlib.Path:
    """Create the output directory (and any missing parents) and return it.

    Parameters
    ----------
    path:
        Desired output directory path (may be relative or absolute).

    Returns
    -------
    pathlib.Path
        The resolved, existing output directory.

    Raises
    ------
    OSError
        If the directory cannot be created due to permissions.
    """
    out = pathlib.Path(path).resolve()
    # Create the full directory tree; ignore if it already exists.
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_checkpoint(state, output_dir: pathlib.Path) -> pathlib.Path:
    """Serialise the current pipeline state to a JSON checkpoint file.

    The checkpoint is written to ``output_dir/checkpoint.json``.  If a
    previous checkpoint exists it is overwritten atomically (write to a
    temp file then rename).

    Parameters
    ----------
    state:
        The :class:`PipelineState` to serialise.  Must be JSON-serialisable
        or have a ``to_dict()`` method.
    output_dir:
        Directory where the checkpoint will be written.

    Returns
    -------
    pathlib.Path
        Path of the written checkpoint file.
    """
    checkpoint_path = output_dir / "checkpoint.json"
    tmp_path        = output_dir / "checkpoint.json.tmp"

    # Convert state to a dict — try .to_dict() first, fall back to __dict__.
    if hasattr(state, "to_dict"):
        payload = state.to_dict()
    elif hasattr(state, "__dict__"):
        payload = state.__dict__
    else:
        payload = {"state": str(state)}

    # Add a timestamp so we know when the checkpoint was written.
    payload["_checkpoint_ts"] = datetime.datetime.utcnow().isoformat() + "Z"

    # Write to temp path first, then atomically rename.
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, default=str))
        tmp_path.rename(checkpoint_path)
    except Exception as exc:
        # If the rename fails (e.g., cross-device move), fall back to direct write.
        checkpoint_path.write_text(json.dumps(payload, indent=2, default=str))
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        logging.getLogger(__name__).warning(
            "Checkpoint atomic rename failed (%s); wrote directly.", exc
        )

    return checkpoint_path


def load_checkpoint(checkpoint_dir: str):
    """Load a previously saved pipeline state from a checkpoint directory.

    Parameters
    ----------
    checkpoint_dir:
        Path to the directory containing ``checkpoint.json``.

    Returns
    -------
    dict
        The deserialised checkpoint payload, or an empty dict if the file
        cannot be found or parsed.
    """
    path = pathlib.Path(checkpoint_dir) / "checkpoint.json"
    if not path.exists():
        logging.getLogger(__name__).warning(
            "Checkpoint file not found: %s", path
        )
        return {}

    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logging.getLogger(__name__).error(
            "Failed to parse checkpoint JSON (%s): %s", path, exc
        )
        return {}


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string.

    Examples
    --------
    >>> format_duration(45.3)
    '45s'
    >>> format_duration(125.0)
    '2m 5s'
    >>> format_duration(3725.0)
    '1h 2m 5s'

    Parameters
    ----------
    seconds:
        Duration in seconds (non-negative float).

    Returns
    -------
    str
        Human-readable duration string.
    """
    # Clamp to zero to avoid negative display values.
    seconds = max(0.0, seconds)
    total   = int(seconds)

    hours   = total // 3600
    minutes = (total % 3600) // 60
    secs    = total % 60

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        # Show one decimal place for sub-minute durations.
        return f"{seconds:.1f}s"


def format_field_table_row(field, max_name: int = 30) -> str:
    """Return a single formatted table row string for a field.

    Parameters
    ----------
    field:
        A dict or Field object describing the field.
    max_name:
        Maximum characters for the name column before truncation.

    Returns
    -------
    str
        A formatted string suitable for printing in a table.
    """
    if isinstance(field, dict):
        name   = field.get("name", "unknown")[:max_name]
        domain = field.get("domain", "—")[:12]
    else:
        name   = getattr(field, "name", "unknown")[:max_name]
        domain = getattr(field, "domain", "—")[:12]

    return f"  {name:<{max_name}}  {domain:<14}"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the command-line argument parser.

    All arguments documented in the module docstring are registered here.

    Returns
    -------
    argparse.ArgumentParser
        Fully configured parser ready to call ``.parse_args()``.
    """
    parser = argparse.ArgumentParser(
        prog="run_synthesis_frontier.py",
        description=(
            "Run the synthesis frontier: 48 fields → binary tournament "
            "→ math paper → code."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python scripts/run_synthesis_frontier.py --rounds 3 --show-metaphors
              python scripts/run_synthesis_frontier.py --execute-code --output outputs/run1/
              python scripts/run_synthesis_frontier.py --no-llm --strategy random
        """),
    )

    # ── Tournament control ─────────────────────────────────────────────────
    parser.add_argument(
        "--rounds", type=int, default=None, metavar="N",
        help="Run only N rounds (default: all 6 rounds to completion).",
    )
    parser.add_argument(
        "--checkpoint", type=str, default=None, metavar="DIR",
        help="Load from an existing checkpoint directory before running.",
    )
    parser.add_argument(
        "--strategy",
        choices=["random", "similarity", "diversity", "greedy"],
        default=DEFAULT_STRATEGY,
        help=f"Pairing strategy (default: {DEFAULT_STRATEGY}).",
    )

    # ── Output control ─────────────────────────────────────────────────────
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--latex-output", type=str, default=None, metavar="FILE",
        help="Save generated LaTeX paper to FILE.",
    )

    # ── Model configuration ────────────────────────────────────────────────
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        help=f"LLM model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--no-llm", action="store_true", default=False,
        help="Use heuristic judge only (no LLM calls).",
    )

    # ── Execution flags ────────────────────────────────────────────────────
    parser.add_argument(
        "--execute-code", action="store_true", default=False,
        help="Orchestrate code generation from the paper after synthesis.",
    )

    # ── Display flags ──────────────────────────────────────────────────────
    parser.add_argument(
        "--show-metaphors", action="store_true", default=False,
        help="Print discovered metaphors after each round.",
    )
    parser.add_argument(
        "--show-propositions", action="store_true", default=False,
        help="Print all propositions at each round.",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Enable verbose logging to stderr.",
    )

    # ── Demo flag ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--demo", action="store_true", default=False,
        help="Run in demo mode with synthetic data (no LLM calls, no package needed).",
    )

    return parser


def setup_logging(verbose: bool) -> logging.Logger:
    """Configure the root logger and return the module-level logger.

    Parameters
    ----------
    verbose:
        If True, set log level to DEBUG; otherwise INFO.

    Returns
    -------
    logging.Logger
        The logger for this module (``__name__``).
    """
    level = logging.DEBUG if verbose else logging.INFO

    # Configure the root logger so all library loggers inherit the level.
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    logger = logging.getLogger(__name__)
    logger.debug("Logging initialised at level %s.", logging.getLevelName(level))
    return logger


def _print_welcome_message(args: argparse.Namespace) -> None:
    """Print a concise welcome/configuration summary before the run starts.

    Parameters
    ----------
    args:
        Parsed command-line arguments namespace.
    """
    print(f"  Model    : {args.model}")
    print(f"  Strategy : {args.strategy}")
    print(f"  Output   : {args.output}")
    if args.rounds:
        print(f"  Rounds   : {args.rounds} (capped)")
    if args.no_llm:
        print("  LLM      : disabled (heuristic judge)")
    if args.checkpoint:
        print(f"  Resume   : {args.checkpoint}")
    print()


def _demo_mode() -> None:
    """Run a fully self-contained demo showing what the script output looks like.

    This function uses synthetic (hard-coded) data — no LLM calls, no external
    package dependencies.  It walks through a simulated 3-round tournament with
    a subset of the 48 fields to demonstrate the output format.
    """
    import random  # local import — only needed for demo mode

    # Seed for reproducibility.
    random.seed(42)

    printer = TournamentPrinter()
    tracker = ProgressTracker(total_rounds=3)
    fmt     = ResultFormatter()

    # Use the first 8 fields from FORTY_EIGHT_FIELDS as demo data.
    demo_fields = list(FORTY_EIGHT_FIELDS[:8])

    printer.print_banner()
    print("  [DEMO MODE — synthetic data, no LLM calls]\n")
    printer.print_fields_table(demo_fields)

    # Simulate three tournament rounds: 8 → 4 → 2 → 1.
    current_fields = demo_fields
    cumulative_props = 0
    all_metaphors: list = []
    round_results: list[RoundResult] = []

    for round_num in range(1, 4):
        before = len(current_fields)
        after  = max(1, before // 2)

        tracker.start_round(round_num)
        printer.print_round_header(round_num, before, after)

        pairs: list[tuple[str, str]] = []
        new_metaphors: list[str]     = []
        new_bridges: list[str]       = []
        round_props_delta = 0

        # Pair up the current fields.
        for i in range(0, len(current_fields) - 1, 2):
            fa = current_fields[i]
            fb = current_fields[i + 1]
            name_a = fa["name"]
            name_b = fb["name"]

            # Synthetic score: random float in [0.6, 1.0].
            score = round(random.uniform(0.6, 1.0), 2)

            # Generate fake metaphors.
            n_metaphors = random.randint(2, 8)
            merge_metaphors = [
                f"The {name_a} notion of 'structure' mirrors the {name_b} "
                f"concept of 'invariant' under transformation #{j}."
                for j in range(n_metaphors)
            ]
            new_metaphors.extend(merge_metaphors)

            # Generate fake bridge theorems.
            n_bridges = random.randint(2, 6)
            merge_bridges = [
                f"Bridge Theorem {round_num}.{i // 2 + 1}.{k}: The {name_a}–"
                f"{name_b} functor preserves property P_{k}."
                for k in range(n_bridges)
            ]
            new_bridges.extend(merge_bridges)

            # Simulate proposition count growth.
            props_before = cumulative_props
            props_added  = random.randint(5, 20)
            cumulative_props += props_added
            round_props_delta += props_added

            printer.print_merge_result(
                field_a_name=name_a,
                field_b_name=name_b,
                score=score,
                metaphors=merge_metaphors,
                bridges=merge_bridges,
                props_before=props_before,
                props_after=cumulative_props,
            )
            pairs.append((name_a, name_b))

        # Progress bar showing overall field reduction.
        all_metaphors.extend(new_metaphors)
        printer.print_progress_bar(
            current=len(FORTY_EIGHT_FIELDS) - len(current_fields) + (before - after),
            total=len(FORTY_EIGHT_FIELDS) - 1,
        )

        # Build a RoundResult and store it.
        time.sleep(0.05)  # Tiny sleep so timing is non-zero.
        tracker.end_round(round_num, after, cumulative_props)
        rr = RoundResult(
            round_num=round_num,
            fields_before=before,
            fields_after=after,
            pairs_merged=pairs,
            new_metaphors=new_metaphors,
            new_bridge_theorems=new_bridges,
            total_propositions=cumulative_props,
            props_delta=round_props_delta,
            elapsed_seconds=tracker._round_durations.get(round_num, 0.0),
        )
        round_results.append(rr)
        printer.print_round_summary(rr)

        # Show top metaphors if demo is verbose.
        printer.print_metaphors(all_metaphors, top_n=3)

        # Reduce field list for next round (just keep every other field).
        current_fields = current_fields[::2][:after]

        print(tracker.summary())

    # ── Final summary ──────────────────────────────────────────────────────

    # Build a minimal stub state object for the summary printer.
    class _State:
        fields         = current_fields
        round_results  = round_results
        metaphors      = all_metaphors
        propositions   = list(range(cumulative_props))  # fake — just need count

    state = _State()
    printer.print_tournament_summary(state)

    # ── Fake paper generation ──────────────────────────────────────────────

    class _FakePaper:
        title        = "On the Unified Theory of Synthesised Mathematical Fields"
        abstract     = (
            "We present a unification of type theory, category theory, "
            "algebraic topology, and differential geometry obtained via a "
            "six-round binary synthesis tournament.  The resulting framework "
            "yields 47 novel bridge theorems and 12 cross-domain metaphors."
        )
        sections     = [
            {"title": "Introduction"},
            {"title": "Preliminaries"},
            {"title": "Round-by-Round Synthesis"},
            {"title": "Bridge Theorems"},
            {"title": "Unified Framework"},
            {"title": "Conclusion"},
        ]
        theorems     = [f"Theorem {i}" for i in range(1, 48)]
        latex_source = r"\documentclass{amsart}" + " " * 5000  # fake bulk

    paper = _FakePaper()
    printer.print_paper_stats(paper)

    # ── Fake code plan ─────────────────────────────────────────────────────

    class _FakePlan:
        entry_point = "synthesis_main.py"
        modules     = [
            {"name": "field_registry.py"},
            {"name": "tournament_engine.py"},
            {"name": "metaphor_finder.py"},
            {"name": "bridge_theorem_prover.py"},
            {"name": "paper_generator.py"},
            {"name": "latex_renderer.py"},
        ]

    printer.print_code_plan(_FakePlan())

    # ── Final result formatter output ──────────────────────────────────────
    print("\n" + fmt.format_synthesis_summary(state))
    print("\n" + fmt.format_paper_summary(paper))

    print(f"\n  Demo complete in {format_duration(tracker.elapsed_total())}.")
    print(f"  Total metaphors  : {len(all_metaphors)}")
    print(f"  Total propositions: {cumulative_props}")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the synthesis frontier pipeline.

    This function:

    1. Parses command-line arguments.
    2. Sets up structured logging.
    3. Prints the ASCII banner and a welcome message.
    4. Loads or initialises the 48 seed fields.
    5. Iterates through tournament rounds, printing detailed output at each
       step (merge results, metaphors, progress bars).
    6. After all rounds complete, prints the tournament summary.
    7. Generates a LaTeX paper from the final merged field.
    8. Optionally generates code from the paper.
    9. Writes all artefacts to the output directory.

    If the synthesis_frontier package is not installed and ``--demo`` is not
    passed, the function warns the user and falls back to demo mode.
    """
    # ── Parse arguments ────────────────────────────────────────────────────
    parser = build_arg_parser()
    args   = parser.parse_args()

    # ── Logging ────────────────────────────────────────────────────────────
    logger = setup_logging(args.verbose)

    # ── Demo shortcut ──────────────────────────────────────────────────────
    if args.demo or not _HAVE_PIPELINE:
        if not args.demo and not _HAVE_PIPELINE:
            print(
                "\n  [WARNING] synthesis_frontier package not found. "
                "Running in demo mode.\n"
                "  Install the package with: pip install synthesis_frontier\n"
            )
        _demo_mode()
        return

    # ── Build OutputConfig ─────────────────────────────────────────────────
    cfg = OutputConfig(
        output_dir      = args.output,
        model           = args.model,
        strategy        = args.strategy,
        use_llm         = not args.no_llm,
        execute_code    = args.execute_code,
        show_metaphors  = args.show_metaphors,
        show_propositions = args.show_propositions,
        latex_output    = args.latex_output,
        verbose         = args.verbose,
        max_rounds      = args.rounds,
        checkpoint_dir  = args.checkpoint,
    )

    logger.debug("OutputConfig: %s", dataclasses.asdict(cfg))

    # ── Printer / tracker / formatter ──────────────────────────────────────
    printer = TournamentPrinter()
    fmt     = ResultFormatter()

    # Total rounds = log2(48) rounded up = 6.
    total_rounds = math.ceil(math.log2(len(FORTY_EIGHT_FIELDS)))
    if cfg.max_rounds is not None:
        total_rounds = min(total_rounds, cfg.max_rounds)

    tracker = ProgressTracker(total_rounds=total_rounds)

    # ── Banner + welcome ───────────────────────────────────────────────────
    printer.print_banner()
    _print_welcome_message(args)

    # ── Setup output directory ─────────────────────────────────────────────
    try:
        out_dir = setup_output_dir(cfg.output_dir)
        logger.info("Output directory: %s", out_dir)
    except OSError as exc:
        logger.error("Cannot create output directory: %s", exc)
        sys.exit(1)

    # ── Load checkpoint or initialise fresh state ──────────────────────────
    if cfg.checkpoint_dir:
        logger.info("Loading checkpoint from %s", cfg.checkpoint_dir)
        ckpt_data = load_checkpoint(cfg.checkpoint_dir)
        if not ckpt_data:
            logger.warning("Checkpoint empty — starting fresh.")
    else:
        ckpt_data = {}

    # ── Initialise the synthesis pipeline ─────────────────────────────────
    try:
        pipeline = SynthesisPipeline(
            fields        = FORTY_EIGHT_FIELDS,
            model         = cfg.model,
            strategy      = cfg.strategy,
            use_llm       = cfg.use_llm,
            checkpoint    = ckpt_data or None,
        )
        state = getattr(pipeline, "state", PipelineState())
    except Exception as exc:
        logger.error("Failed to initialise pipeline: %s", exc, exc_info=cfg.verbose)
        sys.exit(1)

    # Print the initial fields table.
    printer.print_fields_table(FORTY_EIGHT_FIELDS)

    # ── Tournament rounds ──────────────────────────────────────────────────
    for round_num in range(1, total_rounds + 1):
        fields_before = len(getattr(state, "fields", FORTY_EIGHT_FIELDS))
        fields_after  = max(1, fields_before // 2)

        tracker.start_round(round_num)
        printer.print_round_header(round_num, fields_before, fields_after)

        try:
            # Run one round of the tournament.
            round_result = pipeline.run_round(round_num=round_num)
        except Exception as exc:
            logger.error("Round %d failed: %s", round_num, exc, exc_info=cfg.verbose)
            # Save a checkpoint even on failure so the user can resume.
            save_checkpoint(state, out_dir)
            logger.info("Checkpoint saved to %s", out_dir / "checkpoint.json")
            sys.exit(1)

        # Wrap raw result in RoundResult if the pipeline returns a plain dict.
        if isinstance(round_result, dict):
            rr = RoundResult(
                round_num          = round_num,
                fields_before      = fields_before,
                fields_after       = fields_after,
                pairs_merged       = round_result.get("pairs", []),
                integration_scores = round_result.get("scores", {}),
                new_metaphors      = round_result.get("metaphors", []),
                new_bridge_theorems= round_result.get("bridges", []),
                total_propositions = round_result.get("total_props", 0),
                props_delta        = round_result.get("props_delta", 0),
            )
        elif isinstance(round_result, RoundResult):
            rr = round_result
        else:
            # Unknown type — create a minimal placeholder.
            rr = RoundResult(
                round_num     = round_num,
                fields_before = fields_before,
                fields_after  = fields_after,
            )

        # ── Print per-merge results ────────────────────────────────────────
        for pair_key, score in rr.integration_scores.items():
            if isinstance(pair_key, str) and "×" in pair_key:
                name_a, name_b = [s.strip() for s in pair_key.split("×", 1)]
            elif isinstance(pair_key, (tuple, list)) and len(pair_key) == 2:
                name_a, name_b = pair_key
            else:
                name_a = name_b = str(pair_key)

            printer.print_merge_result(
                field_a_name = name_a,
                field_b_name = name_b,
                score        = score,
                metaphors    = rr.new_metaphors,
                bridges      = rr.new_bridge_theorems,
                props_before = rr.total_propositions - rr.props_delta,
                props_after  = rr.total_propositions,
            )

        # ── Progress bar ───────────────────────────────────────────────────
        printer.print_progress_bar(current=round_num, total=total_rounds)

        # ── Optional metaphor display ──────────────────────────────────────
        if cfg.show_metaphors:
            all_metaphors = getattr(state, "metaphors", []) + rr.new_metaphors
            printer.print_metaphors(all_metaphors, top_n=3)

        # ── Optional propositions display ──────────────────────────────────
        if cfg.show_propositions:
            for field in getattr(state, "fields", []):
                printer.print_propositions(field)

        # ── Round summary ──────────────────────────────────────────────────
        tracker.end_round(round_num, fields_after, rr.total_propositions)
        rr.elapsed_seconds = tracker._round_durations.get(round_num, 0.0)
        printer.print_round_summary(rr)

        # ── Progress tracker summary ───────────────────────────────────────
        print(tracker.summary())

        # ── Checkpoint after each round ────────────────────────────────────
        ckpt_path = save_checkpoint(state, out_dir)
        logger.info("Checkpoint saved: %s", ckpt_path)

    # ── Tournament complete ────────────────────────────────────────────────
    printer.print_tournament_summary(state)

    # ── Paper generation ───────────────────────────────────────────────────
    if _HAVE_PAPER:
        try:
            gen   = PaperGenerator(model=cfg.model)
            paper = gen.generate(state)
        except Exception as exc:
            logger.error("Paper generation failed: %s", exc, exc_info=cfg.verbose)
            paper = None
    else:
        logger.warning("PaperGenerator not available — skipping paper generation.")
        paper = None

    if paper is not None:
        printer.print_paper_stats(paper)

        # Save LaTeX to the configured path (or a default location).
        latex_src = getattr(paper, "latex_source", "")
        if latex_src:
            latex_path = pathlib.Path(cfg.latex_output) if cfg.latex_output else (
                out_dir / "paper.tex"
            )
            latex_path.write_text(latex_src)
            logger.info("LaTeX paper saved: %s", latex_path)

        # Save the formatted paper summary.
        summary_txt = fmt.format_paper_summary(paper)
        (out_dir / "paper_summary.txt").write_text(summary_txt)

    # ── Code generation ────────────────────────────────────────────────────
    if cfg.execute_code and paper is not None:
        if _HAVE_CODE:
            try:
                orch  = CodeOrchestrator(model=cfg.model)
                plan  = orch.plan(paper)
                printer.print_code_plan(plan)
                code  = orch.generate(plan)
                # Write each generated module to the output directory.
                code_dir = out_dir / "generated_code"
                code_dir.mkdir(exist_ok=True)
                for filename, source in code.items():
                    (code_dir / filename).write_text(source)
                logger.info("Generated code written to %s", code_dir)
            except Exception as exc:
                logger.error("Code generation failed: %s", exc, exc_info=cfg.verbose)
        else:
            logger.warning("CodeOrchestrator not available — skipping code generation.")

    # ── Save final synthesis summary ───────────────────────────────────────
    synthesis_summary = fmt.format_synthesis_summary(state)
    (out_dir / "synthesis_summary.txt").write_text(synthesis_summary)
    print("\n" + synthesis_summary)

    logger.info(
        "Run complete in %s.  All artefacts in %s",
        format_duration(tracker.elapsed_total()),
        out_dir,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
