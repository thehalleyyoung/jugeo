"""Task decomposition coverage checker.

Analyses whether a set of subtask assignments collectively *cover* every
semantic dimension of a high-level task.  The pipeline is:

    task description
        → TaskDimensionExtractor  (keyword/pattern taxonomy)
        → SubtaskDimensionMapper  (subtask scope → dimensions)
        → CoverageScorer          (gap / redundancy / score)
        → DependencyValidator     (DAG integrity)
        → CoverageReport          (final verdict)

No LLM calls — everything is deterministic keyword matching against a
hand-curated taxonomy of 40+ task dimensions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from jugeo_agents.types import CoverageReport

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class DimensionCategory(str, Enum):
    """Top-level categories that group related task dimensions."""

    CONTENT = "content"
    QUALITY = "quality"
    FORMAT = "format"
    PROCESS = "process"
    ETHICS = "ethics"
    TECHNICAL = "technical"


@dataclass(frozen=True, slots=True)
class TaskDimension:
    """A single semantic dimension of a task.

    Parameters
    ----------
    name:
        Machine-readable identifier (e.g. ``"factual_accuracy"``).
    category:
        One of the :class:`DimensionCategory` values.
    importance:
        How critical this dimension is, in [0, 1].
    description:
        Human-readable explanation.
    """

    name: str
    category: str
    importance: float
    description: str


@dataclass(slots=True)
class SubtaskAssignment:
    """A single unit of delegated work.

    Parameters
    ----------
    name:
        Short label for the subtask.
    agent_id:
        Identifier of the agent that will execute it.
    scope:
        Free-text description of what the subtask entails.
    depends_on:
        Names of other subtasks that must complete first.
    estimated_dimensions:
        Dimension names this subtask is *expected* to cover.
    """

    name: str
    agent_id: str
    scope: str
    depends_on: list[str] = field(default_factory=list)
    estimated_dimensions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dimension taxonomy — keyword patterns
# ---------------------------------------------------------------------------

# Each entry: (name, category, importance, description, keywords)
# ``keywords`` is a list of regex patterns (case-insensitive) that, when
# matched against a task description, signal that this dimension is relevant.

_TAXONOMY: list[tuple[str, str, float, str, list[str]]] = [
    # ── Content ───────────────────────────────────────────────────────────
    (
        "factual_accuracy",
        "content",
        0.95,
        "Claims must be verifiably correct",
        [r"accura\w+", r"fact\w*", r"correct\w*", r"verif\w+", r"truth\w*"],
    ),
    (
        "source_quality",
        "content",
        0.85,
        "Sources must be authoritative and current",
        [r"source\w*", r"reference\w*", r"cit\w+", r"bibliograph\w*", r"authorit\w+"],
    ),
    (
        "depth_of_analysis",
        "content",
        0.80,
        "Analysis goes beyond surface-level description",
        [r"analy\w+", r"deep\b", r"depth", r"in.depth", r"thorough\w*", r"detail\w*"],
    ),
    (
        "data_completeness",
        "content",
        0.75,
        "All relevant data points are included",
        [r"data\b", r"complet\w+", r"comprehensive\w*", r"all\b.*\bdata", r"dataset\w*"],
    ),
    (
        "argument_strength",
        "content",
        0.80,
        "Conclusions follow logically from evidence",
        [r"argu\w+", r"logic\w+", r"reason\w+", r"evidence\w*", r"conclu\w+"],
    ),
    (
        "originality",
        "content",
        0.65,
        "Content offers novel insights or perspectives",
        [r"original\w*", r"novel\w*", r"innovat\w*", r"unique\w*", r"creative\w*"],
    ),
    (
        "relevance",
        "content",
        0.85,
        "Content is directly relevant to the stated objective",
        [r"relevan\w+", r"pertinen\w*", r"on.topic", r"scope\b", r"focus\w*"],
    ),
    (
        "coverage_breadth",
        "content",
        0.70,
        "All major facets of the topic are addressed",
        [r"breadth", r"broad\w*", r"comprehensiv\w*", r"all\b.*\baspects", r"holistic\w*"],
    ),
    # ── Quality ───────────────────────────────────────────────────────────
    (
        "writing_quality",
        "quality",
        0.80,
        "Prose is clear, precise, and professional",
        [r"writ\w+", r"prose\b", r"style\b", r"clarity\b", r"clear\w*"],
    ),
    (
        "coherence",
        "quality",
        0.85,
        "Ideas flow logically and sections connect",
        [r"coheren\w+", r"flow\b", r"connect\w*", r"transit\w+", r"unified\w*"],
    ),
    (
        "readability",
        "quality",
        0.70,
        "Text is accessible to the target audience",
        [r"readab\w+", r"accessib\w+", r"plain\b.*\blanguage", r"jargon\b", r"audienc\w+"],
    ),
    (
        "grammar",
        "quality",
        0.60,
        "Text is free of grammatical and spelling errors",
        [r"grammar\w*", r"spell\w+", r"proofread\w*", r"error.free", r"typo\w*"],
    ),
    (
        "tone",
        "quality",
        0.65,
        "Tone is appropriate for context and audience",
        [r"tone\b", r"voice\b", r"formal\w*", r"informal\w*", r"profession\w+"],
    ),
    (
        "consistency",
        "quality",
        0.75,
        "Terminology and style are used consistently",
        [r"consisten\w+", r"uniform\w*", r"standardiz\w*", r"terminolog\w+"],
    ),
    (
        "conciseness",
        "quality",
        0.60,
        "Content is free of unnecessary verbosity",
        [r"concis\w+", r"brief\w*", r"succinct\w*", r"terse\b", r"verbose\w*"],
    ),
    # ── Format ────────────────────────────────────────────────────────────
    (
        "structure",
        "format",
        0.80,
        "Document has a clear organizational structure",
        [r"structur\w+", r"organiz\w+", r"outline\w*", r"section\w*", r"framework\w*"],
    ),
    (
        "headings",
        "format",
        0.55,
        "Headings and sub-headings guide the reader",
        [r"heading\w*", r"sub.?heading\w*", r"title\w*", r"header\w*"],
    ),
    (
        "citations",
        "format",
        0.75,
        "Sources are properly cited in-line and in bibliography",
        [r"citat\w+", r"cite\w*", r"footnot\w+", r"endnot\w+", r"APA\b", r"MLA\b"],
    ),
    (
        "executive_summary",
        "format",
        0.65,
        "An executive summary or abstract is included",
        [r"executive\b.*\bsummar\w+", r"abstract\b", r"synopsis\w*", r"overview\b"],
    ),
    (
        "visual_elements",
        "format",
        0.55,
        "Charts, tables, or diagrams support the text",
        [r"visual\w*", r"chart\w*", r"table\w*", r"diagram\w*", r"graph\w*", r"figur\w+"],
    ),
    (
        "length_requirements",
        "format",
        0.50,
        "Output meets specified length or page constraints",
        [r"length\b", r"word.?count", r"page\w*", r"\d+\s*words?", r"short\b", r"long\b"],
    ),
    (
        "formatting_standards",
        "format",
        0.50,
        "Follows specified formatting standards (fonts, margins, etc.)",
        [r"format\w+", r"font\b", r"margin\w*", r"spacing\b", r"indent\w*", r"template\w*"],
    ),
    (
        "appendices",
        "format",
        0.40,
        "Supplementary material is provided in appendices",
        [r"appendi\w+", r"supplement\w+", r"additional\b.*\bmaterial", r"annex\w*"],
    ),
    # ── Process ───────────────────────────────────────────────────────────
    (
        "source_diversity",
        "process",
        0.75,
        "Multiple independent sources are consulted",
        [r"divers\w+", r"multiple\b.*\bsource", r"independent\w*", r"cross.?referenc\w*"],
    ),
    (
        "peer_review",
        "process",
        0.80,
        "Output is reviewed by at least one other agent or human",
        [r"peer\b.*\breview\w*", r"review\w+", r"feedback\w*", r"critiqu\w+"],
    ),
    (
        "fact_checking",
        "process",
        0.90,
        "Claims are independently verified against primary sources",
        [r"fact.?check\w*", r"verif\w+", r"validat\w+", r"confirm\w*", r"double.?check"],
    ),
    (
        "revision",
        "process",
        0.65,
        "At least one revision pass is performed",
        [r"revis\w+", r"edit\w*", r"draft\w*", r"iteration\w*", r"refin\w+", r"polish\w*"],
    ),
    (
        "stakeholder_input",
        "process",
        0.70,
        "Relevant stakeholders are consulted during the process",
        [r"stakeholder\w*", r"consult\w+", r"input\b", r"collaborat\w+", r"participat\w+"],
    ),
    (
        "timeline_adherence",
        "process",
        0.60,
        "Work is completed within stated deadlines",
        [r"timeline\w*", r"deadline\w*", r"schedul\w+", r"mileston\w+", r"due\b.*\bdate"],
    ),
    (
        "version_control",
        "process",
        0.45,
        "Changes are tracked and reversible",
        [r"version\w*", r"track\w+", r"changelog\w*", r"history\b", r"audit\b.*\btrail"],
    ),
    (
        "quality_assurance",
        "process",
        0.80,
        "A dedicated QA step ensures output meets standards",
        [r"quality\b.*\bassur\w+", r"\bQA\b", r"test\w+", r"check\w*", r"standard\w*"],
    ),
    # ── Ethics ────────────────────────────────────────────────────────────
    (
        "bias_mitigation",
        "ethics",
        0.85,
        "Content is checked for unfair bias or stereotyping",
        [r"bias\w*", r"fair\w+", r"stereotyp\w*", r"neutral\w*", r"impartial\w*"],
    ),
    (
        "privacy_compliance",
        "ethics",
        0.90,
        "Personal data is handled in compliance with regulations",
        [r"privacy\w*", r"GDPR\b", r"PII\b", r"personal\b.*\bdata", r"anonymi\w+"],
    ),
    (
        "attribution",
        "ethics",
        0.75,
        "Ideas and content are properly attributed to creators",
        [r"attribut\w+", r"credit\w*", r"acknowledg\w+", r"plagiar\w+", r"intellectual\b.*\bproperty"],
    ),
    (
        "transparency",
        "ethics",
        0.70,
        "Limitations, assumptions, and AI involvement are disclosed",
        [r"transparen\w+", r"disclos\w+", r"limitation\w*", r"assumption\w*", r"caveat\w*"],
    ),
    # ── Technical ─────────────────────────────────────────────────────────
    (
        "reproducibility",
        "technical",
        0.80,
        "Results can be reproduced given the same inputs",
        [r"reproduc\w+", r"replicat\w+", r"deterministic\w*", r"repeatabl\w+"],
    ),
    (
        "scalability",
        "technical",
        0.60,
        "Approach scales to larger inputs or requirements",
        [r"scal\w+", r"perform\w+", r"efficien\w+", r"large.?scale", r"throughput\w*"],
    ),
    (
        "error_handling",
        "technical",
        0.75,
        "Edge cases and failure modes are addressed",
        [r"error\w*", r"exception\w*", r"edge.?case\w*", r"fail\w+", r"robust\w*", r"fault\b"],
    ),
    (
        "security",
        "technical",
        0.90,
        "Output does not expose sensitive information or vulnerabilities",
        [r"secur\w+", r"vulnerab\w+", r"exploit\w*", r"inject\w+", r"sanitiz\w+"],
    ),
    (
        "interoperability",
        "technical",
        0.55,
        "Output works with expected downstream systems",
        [r"interop\w+", r"compat\w+", r"integrat\w+", r"API\b", r"interface\w*"],
    ),
    (
        "documentation",
        "technical",
        0.65,
        "Process and decisions are documented for future reference",
        [r"document\w+", r"readme\w*", r"comment\w*", r"explain\w*", r"instruct\w+"],
    ),
]

# Pre-compile regex patterns once at import time.
_COMPILED_TAXONOMY: list[tuple[TaskDimension, list[re.Pattern[str]]]] = [
    (
        TaskDimension(name=name, category=cat, importance=imp, description=desc),
        [re.compile(p, re.IGNORECASE) for p in kws],
    )
    for name, cat, imp, desc, kws in _TAXONOMY
]


# ---------------------------------------------------------------------------
# TaskDimensionExtractor
# ---------------------------------------------------------------------------


class TaskDimensionExtractor:
    """Extract relevant :class:`TaskDimension` instances from a task
    description by matching against a pre-built keyword taxonomy.

    The extractor does **not** call an LLM — it is fully deterministic.

    Parameters
    ----------
    min_keyword_matches:
        Minimum number of distinct keyword patterns that must match for a
        dimension to be considered relevant.  Defaults to ``1``.
    extra_dimensions:
        Optional additional dimensions (with keywords) that supplement the
        built-in taxonomy.
    """

    def __init__(
        self,
        *,
        min_keyword_matches: int = 1,
        extra_dimensions: Sequence[tuple[str, str, float, str, list[str]]] | None = None,
    ) -> None:
        self._min_matches = max(1, min_keyword_matches)

        # Build the lookup from both built-in and caller-supplied dimensions.
        self._taxonomy: list[tuple[TaskDimension, list[re.Pattern[str]]]] = list(
            _COMPILED_TAXONOMY
        )

        if extra_dimensions:
            for name, cat, imp, desc, kws in extra_dimensions:
                dim = TaskDimension(name=name, category=cat, importance=imp, description=desc)
                patterns = [re.compile(p, re.IGNORECASE) for p in kws]
                self._taxonomy.append((dim, patterns))

    # ------------------------------------------------------------------

    def extract(self, task_description: str) -> list[TaskDimension]:
        """Return dimensions whose keyword patterns appear in *task_description*.

        Dimensions are returned sorted by importance (descending) so that
        the most critical requirements appear first.
        """
        matched: list[TaskDimension] = []

        for dim, patterns in self._taxonomy:
            hits = sum(1 for p in patterns if p.search(task_description))
            if hits >= self._min_matches:
                matched.append(dim)

        matched.sort(key=lambda d: d.importance, reverse=True)
        return matched

    def extract_by_category(
        self, task_description: str
    ) -> dict[str, list[TaskDimension]]:
        """Extract dimensions grouped by their category."""
        dims = self.extract(task_description)
        groups: dict[str, list[TaskDimension]] = defaultdict(list)
        for d in dims:
            groups[d.category].append(d)
        return dict(groups)

    @property
    def all_dimensions(self) -> list[TaskDimension]:
        """Return every dimension known to this extractor."""
        return [dim for dim, _ in self._taxonomy]


# ---------------------------------------------------------------------------
# SubtaskDimensionMapper
# ---------------------------------------------------------------------------


class SubtaskDimensionMapper:
    """Map each :class:`SubtaskAssignment` to the set of dimension names
    it is likely to cover, based on keyword overlap between the subtask's
    *scope* text and each dimension's keyword patterns.

    If the subtask already carries ``estimated_dimensions``, those are
    included unconditionally.
    """

    def __init__(
        self,
        *,
        min_keyword_matches: int = 1,
        taxonomy: Sequence[tuple[TaskDimension, list[re.Pattern[str]]]] | None = None,
    ) -> None:
        self._min_matches = max(1, min_keyword_matches)
        self._taxonomy = list(taxonomy or _COMPILED_TAXONOMY)

    # ------------------------------------------------------------------

    def map_subtask(
        self,
        subtask: SubtaskAssignment,
        all_dimensions: list[TaskDimension],
    ) -> list[str]:
        """Return the names of dimensions covered by *subtask*.

        Coverage is determined by:

        1. Any dimensions listed in ``subtask.estimated_dimensions`` that
           also appear in *all_dimensions*.
        2. Keyword-pattern matching of the subtask's *scope* against each
           dimension's patterns.
        """
        dim_names = {d.name for d in all_dimensions}
        covered: set[str] = set()

        # (1) Honour explicit estimates that are among the known dimensions.
        for est in subtask.estimated_dimensions:
            if est in dim_names:
                covered.add(est)

        # (2) Keyword matching against scope text.
        text = f"{subtask.name} {subtask.scope}"
        for dim, patterns in self._taxonomy:
            if dim.name not in dim_names:
                continue
            hits = sum(1 for p in patterns if p.search(text))
            if hits >= self._min_matches:
                covered.add(dim.name)

        return sorted(covered)

    def map_all(
        self,
        subtasks: Sequence[SubtaskAssignment],
        all_dimensions: list[TaskDimension],
    ) -> dict[str, list[str]]:
        """Return ``{subtask.name: [dim_name, …]}`` for every subtask."""
        return {st.name: self.map_subtask(st, all_dimensions) for st in subtasks}


# ---------------------------------------------------------------------------
# CoverageScorer
# ---------------------------------------------------------------------------


class CoverageScorer:
    """Compute coverage metrics and produce a :class:`CoverageReport`.

    The scorer considers both *unweighted* coverage (fraction of dimensions
    hit) and *importance-weighted* coverage.  The final
    ``coverage_score`` is the weighted variant.
    """

    def __init__(self, *, completeness_threshold: float = 1.0) -> None:
        self._threshold = completeness_threshold

    # ------------------------------------------------------------------

    def score(
        self,
        dimensions: list[TaskDimension],
        assignments: list[SubtaskAssignment],
        mapping: dict[str, list[str]],
    ) -> CoverageReport:
        """Build a :class:`CoverageReport` from extracted dimensions,
        subtask assignments, and the per-subtask dimension mapping.
        """
        if not dimensions:
            return CoverageReport(
                is_complete=True,
                coverage_score=1.0,
                covered_dimensions=set(),
                gaps=set(),
                redundancies={},
                suggestions=[],
                dimension_assignments={},
            )

        all_dim_names = {d.name for d in dimensions}
        importance_map: dict[str, float] = {d.name: d.importance for d in dimensions}

        # Which dimensions are covered and by how many subtasks.
        dim_coverage_count: dict[str, int] = defaultdict(int)
        # Reverse map: dimension → list of subtask names.
        dim_to_subtasks: dict[str, list[str]] = defaultdict(list)

        for subtask_name, dim_names in mapping.items():
            for dn in dim_names:
                if dn in all_dim_names:
                    dim_coverage_count[dn] += 1
                    dim_to_subtasks[dn].append(subtask_name)

        covered: set[str] = set(dim_coverage_count.keys())
        gaps: set[str] = all_dim_names - covered

        # Redundancies: dimensions covered by >1 subtask.
        redundancies: dict[str, int] = {
            dn: cnt for dn, cnt in dim_coverage_count.items() if cnt > 1
        }

        # Importance-weighted coverage score.
        total_weight = sum(importance_map[d] for d in all_dim_names)
        covered_weight = sum(importance_map[d] for d in covered)
        coverage_score = covered_weight / total_weight if total_weight > 0 else 1.0

        # Actionable suggestions.
        suggestions = self._generate_suggestions(
            dimensions, gaps, redundancies, mapping, assignments,
        )

        is_complete = coverage_score >= self._threshold and len(gaps) == 0

        dimension_assignments: dict[str, list[str]] = {
            dn: sorted(subs) for dn, subs in dim_to_subtasks.items()
        }

        return CoverageReport(
            is_complete=is_complete,
            coverage_score=round(coverage_score, 4),
            covered_dimensions=covered,
            gaps=gaps,
            redundancies=redundancies,
            suggestions=suggestions,
            dimension_assignments=dimension_assignments,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _generate_suggestions(
        dimensions: list[TaskDimension],
        gaps: set[str],
        redundancies: dict[str, int],
        mapping: dict[str, list[str]],
        assignments: list[SubtaskAssignment],
    ) -> list[str]:
        """Build a prioritised list of human-readable suggestions."""
        dim_lookup: dict[str, TaskDimension] = {d.name: d for d in dimensions}
        suggestions: list[str] = []

        # --- Gap suggestions (sorted by importance descending) ---
        sorted_gaps = sorted(gaps, key=lambda g: dim_lookup[g].importance, reverse=True)
        for gap_name in sorted_gaps:
            dim = dim_lookup[gap_name]
            suggestions.append(
                f"[GAP] Add a subtask covering '{gap_name}' "
                f"({dim.category}, importance={dim.importance:.2f}): "
                f"{dim.description}"
            )

        # --- Redundancy suggestions ---
        for dim_name, count in sorted(
            redundancies.items(), key=lambda kv: kv[1], reverse=True
        ):
            dim = dim_lookup.get(dim_name)
            if dim and dim.importance < 0.6:
                subtasks_str = ", ".join(
                    st.name for st in assignments
                    if dim_name in mapping.get(st.name, [])
                )
                suggestions.append(
                    f"[REDUNDANCY] '{dim_name}' is covered by {count} subtasks "
                    f"({subtasks_str}); consider consolidating since importance "
                    f"is only {dim.importance:.2f}."
                )

        # --- Under-utilised agent suggestions ---
        subtask_dim_counts: dict[str, int] = {
            st_name: len(dims) for st_name, dims in mapping.items()
        }
        for st in assignments:
            n_dims = subtask_dim_counts.get(st.name, 0)
            if n_dims == 0:
                suggestions.append(
                    f"[IDLE] Subtask '{st.name}' (agent={st.agent_id}) does "
                    f"not cover any extracted dimensions — review its scope or "
                    f"remove it."
                )

        # --- Single-point-of-failure suggestions ---
        for dim_name, subs in mapping.items():
            dim = dim_lookup.get(dim_name)
            if dim and dim.importance >= 0.85 and len(subs) == 1:
                suggestions.append(
                    f"[RISK] High-importance dimension '{dim_name}' "
                    f"(importance={dim.importance:.2f}) is only covered by "
                    f"'{subs[0]}'. Consider adding a verification subtask."
                )

        return suggestions


# ---------------------------------------------------------------------------
# DependencyValidator
# ---------------------------------------------------------------------------


class DependencyValidator:
    """Validate the dependency DAG formed by subtask ``depends_on`` edges.

    Checks performed
    ----------------
    * **Missing dependency targets** — a subtask depends on a name that does
      not correspond to any known subtask.
    * **Cycle detection** — uses iterative DFS with a three-colour scheme
      (WHITE / GREY / BLACK) to detect back-edges.
    * **Unreachable subtasks** — subtasks that are neither root nodes (no
      dependencies) nor reachable from any root via forward edges.
    * **Self-dependencies** — a subtask that lists itself in ``depends_on``.
    """

    _WHITE = 0  # Not visited
    _GREY = 1   # In current DFS path
    _BLACK = 2  # Fully explored

    def validate(self, assignments: Sequence[SubtaskAssignment]) -> list[str]:
        """Return a list of error/warning messages.  An empty list means
        the dependency graph is valid.
        """
        errors: list[str] = []
        name_set = {a.name for a in assignments}

        # Adjacency list (forward edges: dependency → dependant).
        forward: dict[str, list[str]] = defaultdict(list)
        # Adjacency list (dependant → dependencies, for cycle detection).
        backward: dict[str, list[str]] = {}

        for a in assignments:
            backward[a.name] = list(a.depends_on)
            for dep in a.depends_on:
                forward[dep].append(a.name)

        # --- Self-dependencies ---
        for a in assignments:
            if a.name in a.depends_on:
                errors.append(
                    f"Subtask '{a.name}' lists itself as a dependency."
                )

        # --- Missing dependency targets ---
        for a in assignments:
            for dep in a.depends_on:
                if dep not in name_set:
                    errors.append(
                        f"Subtask '{a.name}' depends on '{dep}', which "
                        f"does not exist."
                    )

        # --- Cycle detection (iterative DFS, three-colour) ---
        colour: dict[str, int] = {a.name: self._WHITE for a in assignments}
        cycle_errors = self._detect_cycles(backward, colour, name_set)
        errors.extend(cycle_errors)

        # --- Unreachable subtask detection ---
        roots = {a.name for a in assignments if not a.depends_on}
        if roots:
            reachable = self._bfs_reachable(forward, roots)
            for a in assignments:
                if a.name not in reachable:
                    errors.append(
                        f"Subtask '{a.name}' is unreachable from any root "
                        f"subtask (has unresolvable dependencies)."
                    )

        # --- Duplicate dependency entries ---
        for a in assignments:
            seen: set[str] = set()
            for dep in a.depends_on:
                if dep in seen:
                    errors.append(
                        f"Subtask '{a.name}' lists '{dep}' as a dependency "
                        f"more than once."
                    )
                seen.add(dep)

        return errors

    # ------------------------------------------------------------------

    def _detect_cycles(
        self,
        backward: dict[str, list[str]],
        colour: dict[str, int],
        all_names: set[str],
    ) -> list[str]:
        """Iterative DFS cycle detection with explicit stack.

        Returns one error message per detected cycle, including the cycle
        path for debugging.
        """
        errors: list[str] = []

        for start in sorted(all_names):
            if colour.get(start, self._WHITE) != self._WHITE:
                continue

            # Stack entries: (node, iterator_over_dependencies, path)
            stack: list[tuple[str, int]] = [(start, 0)]
            path: list[str] = [start]
            colour[start] = self._GREY

            while stack:
                node, dep_idx = stack[-1]
                deps = backward.get(node, [])

                if dep_idx < len(deps):
                    # Advance the iterator for this stack frame.
                    stack[-1] = (node, dep_idx + 1)
                    neighbour = deps[dep_idx]

                    if neighbour not in colour:
                        # Unknown node — already flagged as missing dep.
                        continue

                    if colour[neighbour] == self._GREY:
                        # Back-edge → cycle found.
                        cycle_start = path.index(neighbour)
                        cycle_path = path[cycle_start:] + [neighbour]
                        errors.append(
                            f"Dependency cycle detected: "
                            f"{' → '.join(cycle_path)}"
                        )
                    elif colour[neighbour] == self._WHITE:
                        colour[neighbour] = self._GREY
                        stack.append((neighbour, 0))
                        path.append(neighbour)
                else:
                    # All neighbours explored.
                    colour[node] = self._BLACK
                    stack.pop()
                    path.pop()

        return errors

    @staticmethod
    def _bfs_reachable(
        forward: dict[str, list[str]],
        roots: set[str],
    ) -> set[str]:
        """BFS from *roots* along forward edges.  Returns all reachable
        node names (including the roots themselves).
        """
        visited: set[str] = set(roots)
        frontier = list(roots)
        while frontier:
            next_frontier: list[str] = []
            for node in frontier:
                for child in forward.get(node, []):
                    if child not in visited:
                        visited.add(child)
                        next_frontier.append(child)
            frontier = next_frontier
        return visited


# ---------------------------------------------------------------------------
# CoverageChecker — top-level façade
# ---------------------------------------------------------------------------


class CoverageChecker:
    """One-call façade that orchestrates dimension extraction, subtask
    mapping, coverage scoring, and dependency validation.

    Usage::

        checker = CoverageChecker()
        report = checker.check(
            task_description="Write a research report on climate change …",
            subtasks=[
                {"name": "find_sources", "agent_id": "researcher",
                 "scope": "Find diverse, authoritative sources"},
                {"name": "draft",        "agent_id": "writer",
                 "scope": "Write a clear, structured draft with citations",
                 "depends_on": ["find_sources"]},
                {"name": "review",       "agent_id": "reviewer",
                 "scope": "Peer-review the draft for factual accuracy",
                 "depends_on": ["draft"]},
            ],
        )
    """

    def __init__(
        self,
        *,
        min_keyword_matches: int = 1,
        completeness_threshold: float = 1.0,
        extra_dimensions: Sequence[tuple[str, str, float, str, list[str]]] | None = None,
    ) -> None:
        self._extractor = TaskDimensionExtractor(
            min_keyword_matches=min_keyword_matches,
            extra_dimensions=extra_dimensions,
        )
        self._mapper = SubtaskDimensionMapper(min_keyword_matches=min_keyword_matches)
        self._scorer = CoverageScorer(completeness_threshold=completeness_threshold)
        self._dep_validator = DependencyValidator()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def extractor(self) -> TaskDimensionExtractor:
        """Access the underlying dimension extractor."""
        return self._extractor

    @property
    def mapper(self) -> SubtaskDimensionMapper:
        """Access the underlying subtask-dimension mapper."""
        return self._mapper

    @property
    def scorer(self) -> CoverageScorer:
        """Access the underlying coverage scorer."""
        return self._scorer

    @property
    def dependency_validator(self) -> DependencyValidator:
        """Access the underlying dependency validator."""
        return self._dep_validator

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def check(
        self,
        task_description: str,
        subtasks: Sequence[dict[str, object] | SubtaskAssignment],
    ) -> CoverageReport:
        """Run the full coverage-checking pipeline.

        Parameters
        ----------
        task_description:
            Natural-language description of the high-level task.
        subtasks:
            A list of subtask specifications.  Each entry may be either a
            :class:`SubtaskAssignment` instance or a dict with keys
            ``name``, ``agent_id``, ``scope``, and optionally
            ``depends_on`` and ``estimated_dimensions``.

        Returns
        -------
        CoverageReport
            A fully-populated report including gaps, redundancies,
            dependency errors (appended to ``suggestions``), and the
            coverage score.
        """
        # 1. Parse subtask dicts into SubtaskAssignment objects.
        parsed = self._parse_subtasks(subtasks)

        # 2. Extract task dimensions.
        dimensions = self._extractor.extract(task_description)

        # 3. Map subtasks → dimensions.
        mapping = self._mapper.map_all(parsed, dimensions)

        # 4. Score coverage.
        report = self._scorer.score(dimensions, parsed, mapping)

        # 5. Validate dependency graph and append any errors.
        dep_errors = self._dep_validator.validate(parsed)
        for err in dep_errors:
            report.suggestions.append(f"[DEPENDENCY] {err}")

        # If there are dependency errors the decomposition is not complete.
        if dep_errors:
            report.is_complete = False

        return report

    # ------------------------------------------------------------------

    def check_with_details(
        self,
        task_description: str,
        subtasks: Sequence[dict[str, object] | SubtaskAssignment],
    ) -> tuple[
        CoverageReport,
        list[TaskDimension],
        list[SubtaskAssignment],
        dict[str, list[str]],
        list[str],
    ]:
        """Like :meth:`check` but also returns intermediate artefacts.

        Returns
        -------
        tuple
            ``(report, dimensions, assignments, mapping, dep_errors)``
        """
        parsed = self._parse_subtasks(subtasks)
        dimensions = self._extractor.extract(task_description)
        mapping = self._mapper.map_all(parsed, dimensions)
        report = self._scorer.score(dimensions, parsed, mapping)
        dep_errors = self._dep_validator.validate(parsed)
        for err in dep_errors:
            report.suggestions.append(f"[DEPENDENCY] {err}")
        if dep_errors:
            report.is_complete = False
        return report, dimensions, parsed, mapping, dep_errors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_subtasks(
        raw: Sequence[dict[str, object] | SubtaskAssignment],
    ) -> list[SubtaskAssignment]:
        """Normalise a mixed sequence of dicts / dataclass instances into
        a uniform list of :class:`SubtaskAssignment`.
        """
        result: list[SubtaskAssignment] = []
        for item in raw:
            if isinstance(item, SubtaskAssignment):
                result.append(item)
            elif isinstance(item, Mapping):
                result.append(
                    SubtaskAssignment(
                        name=str(item.get("name", "")),
                        agent_id=str(item.get("agent_id", "")),
                        scope=str(item.get("scope", "")),
                        depends_on=list(item.get("depends_on", [])),  # type: ignore[arg-type]
                        estimated_dimensions=list(
                            item.get("estimated_dimensions", [])  # type: ignore[arg-type]
                        ),
                    )
                )
            else:
                raise TypeError(
                    f"Expected SubtaskAssignment or dict, got {type(item).__name__}"
                )
        return result


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def check_coverage(
    task_description: str,
    subtasks: Sequence[dict[str, object] | SubtaskAssignment],
    *,
    min_keyword_matches: int = 1,
    completeness_threshold: float = 1.0,
) -> CoverageReport:
    """Convenience function — create a :class:`CoverageChecker` and run it.

    >>> report = check_coverage(
    ...     "Write an accurate, well-cited research report",
    ...     [{"name": "research", "agent_id": "a1",
    ...       "scope": "Find accurate sources and verify facts"}],
    ... )
    >>> report.coverage_score > 0
    True
    """
    checker = CoverageChecker(
        min_keyword_matches=min_keyword_matches,
        completeness_threshold=completeness_threshold,
    )
    return checker.check(task_description, subtasks)


__all__ = [
    "DimensionCategory",
    "TaskDimension",
    "SubtaskAssignment",
    "TaskDimensionExtractor",
    "SubtaskDimensionMapper",
    "CoverageScorer",
    "DependencyValidator",
    "CoverageChecker",
    "check_coverage",
]
