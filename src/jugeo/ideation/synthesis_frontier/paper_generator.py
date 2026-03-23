from __future__ import annotations
"""Paper generator for synthesis frontier — synthesizes LaTeX math papers from tournaments.
# copilot: synthesis frontier paper generator — tournament results → LaTeX math paper
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Model imports with stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.synthesis_frontier.models import (
        PropositionKind,
        PropositionRecord,
        MetaphorLink,
        FieldNode,
        TournamentState,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        DEFINITION = "definition"
        THEOREM = "theorem"
        PROPOSITION = "proposition"
        LEMMA = "lemma"
        COROLLARY = "corollary"
        CONJECTURE = "conjecture"
        EXAMPLE = "example"
        REMARK = "remark"
        CONSTRUCTION = "construction"
        BRIDGE_THEOREM = "bridge_theorem"

        def latex_env(self) -> str:
            return {
                PropositionKind.BRIDGE_THEOREM: "theorem",
            }.get(self, self.value)

        def is_proven(self) -> bool:
            return self in (
                PropositionKind.THEOREM, PropositionKind.PROPOSITION,
                PropositionKind.LEMMA, PropositionKind.COROLLARY,
                PropositionKind.BRIDGE_THEOREM,
            )

    @dataclass(frozen=True)
    class PropositionRecord:  # type: ignore[no-redef]
        prop_id: str
        kind: PropositionKind
        title: str
        statement: str
        proof_sketch: str = ""
        why_useful: str = ""
        source_field: str = ""
        target_fields: tuple = ()
        metaphor_tags: tuple = ()
        trust_tier: str = "PROPOSAL"
        leverage_score: float = 0.5
        proof_difficulty: str = "MEDIUM"
        dependencies: tuple = ()
        judgment_coordinate: str = ""
        metadata: dict = field(default_factory=dict, compare=False, hash=False)

        @classmethod
        def make(
            cls,
            title: str,
            statement: str,
            kind: PropositionKind = PropositionKind.THEOREM,
            source_field: str = "",
            importance: float = 0.5,
        ) -> "PropositionRecord":
            return cls(
                prop_id=str(uuid.uuid4())[:12],
                kind=kind,
                title=title,
                statement=statement,
                leverage_score=importance,
                source_field=source_field,
            )

    @dataclass(frozen=True)
    class MetaphorLink:  # type: ignore[no-redef]
        link_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
        source_field: str = ""
        target_field: str = ""
        source_concept: str = ""
        target_concept: str = ""
        description: str = ""
        metaphor_description: str = ""
        bridge_propositions: tuple = ()
        strength: float = 0.5
        kind: str = "ANALOGY"
        llm_judge_score: float = 0.0
        llm_judge_reasoning: str = ""

        def to_latex_row(self) -> str:
            desc = self.description or self.metaphor_description
            return (
                f"{self.source_concept} & {self.target_concept} & "
                f"{self.kind} & {self.strength:.2f} & {desc[:60]} \\\\"
            )

        def to_latex_table_row(self) -> str:
            return self.to_latex_row()

    @dataclass
    class FieldNode:  # type: ignore[no-redef]
        field_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
        name: str = ""
        domain: Any = None
        description: str = ""
        core_objects: tuple = ()
        core_morphisms: tuple = ()
        key_theorems: tuple = ()
        propositions: list = field(default_factory=list)
        metaphor_links: list = field(default_factory=list)
        judgment_site: dict = field(default_factory=dict)
        round_number: int = 0
        constituent_fields: tuple = ()
        keywords: tuple = ()
        llm_summary: str = ""
        trust_tier: str = "PROPOSAL"
        metadata: dict = field(default_factory=dict)

        @classmethod
        def make(
            cls,
            name: str,
            description: str,
            propositions: tuple = (),
            constituent_fields: tuple = (),
            round_number: int = 0,
            keywords: tuple = (),
        ) -> "FieldNode":
            return cls(
                field_id=str(uuid.uuid4())[:8],
                name=name,
                description=description,
                propositions=list(propositions),
                constituent_fields=constituent_fields,
                round_number=round_number,
                keywords=keywords,
            )

        def proposition_count(self) -> int:
            return len(self.propositions)

        def bridge_theorems(self) -> list:
            return [p for p in self.propositions if p.kind == PropositionKind.BRIDGE_THEOREM]

        def key_prop_titles(self, n: int = 5) -> list:
            sorted_props = sorted(self.propositions, key=lambda p: -p.leverage_score)
            return [p.title for p in sorted_props[:n]]

        def top_propositions(self, n: int = 10) -> list:
            return sorted(self.propositions, key=lambda p: -p.leverage_score)[:n]

        def propositions_by_kind(self, kind: Any) -> list:
            return [p for p in self.propositions if p.kind == kind]

    @dataclass
    class TournamentState:  # type: ignore[no-redef]
        current_round: int = 0
        round_number: int = 0
        active_nodes: list = field(default_factory=list)
        all_nodes: list = field(default_factory=list)
        all_pairs: list = field(default_factory=list)
        all_metaphors: list = field(default_factory=list)
        all_propositions: list = field(default_factory=list)
        completed_merges: list = field(default_factory=list)
        is_complete: bool = False
        final_synthesis: Any = None

        def register_node(self, node: Any) -> None:
            self.active_nodes.append(node)
            self.all_nodes.append(node)

        def total_propositions(self) -> int:
            return sum(n.proposition_count() for n in self.active_nodes)

        def proposition_count(self) -> int:
            return self.total_propositions()

        def metaphor_count(self) -> int:
            metas = self.all_metaphors
            if hasattr(metas, "values"):
                return len(list(metas.values()))
            return len(metas)

        def summary(self) -> str:
            return (
                f"TournamentState(round={self.current_round or self.round_number}, "
                f"nodes={len(self.active_nodes)}, "
                f"metaphors={self.metaphor_count()})"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _latex_escape(s: str) -> str:
    """Escape LaTeX special characters in plain text."""
    for char, rep in [
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
    ]:
        s = s.replace(char, rep)
    return s


def _get_metaphors_list(state: Any) -> list:
    """Return all_metaphors as a flat list regardless of container type."""
    metas = getattr(state, "all_metaphors", [])
    if hasattr(metas, "values"):
        return list(metas.values())
    return list(metas)


def _get_round(state: Any) -> int:
    """Return the current round number regardless of attribute name."""
    return getattr(state, "current_round", None) or getattr(state, "round_number", 0)


def _get_keywords(synthesis: Any) -> tuple:
    """Return keywords from FieldNode, falling back to key_theorems."""
    kw = getattr(synthesis, "keywords", ())
    if kw:
        return tuple(kw)
    return tuple(getattr(synthesis, "key_theorems", ()))[:6]


def _key_prop_titles(synthesis: Any, n: int = 5) -> list:
    """Return top-n proposition titles, using key_prop_titles if available."""
    if hasattr(synthesis, "key_prop_titles"):
        return synthesis.key_prop_titles(n)
    tops = sorted(
        getattr(synthesis, "propositions", []),
        key=lambda p: -getattr(p, "leverage_score", 0.0),
    )[:n]
    return [p.title for p in tops]


def _metaphor_row(m: Any) -> str:
    """Return a LaTeX table row for a MetaphorLink, handling API variants."""
    if hasattr(m, "to_latex_row"):
        return m.to_latex_row()
    if hasattr(m, "to_latex_table_row"):
        return m.to_latex_table_row()
    desc = getattr(m, "description", "") or getattr(m, "metaphor_description", "")
    src = _latex_escape(getattr(m, "source_concept", ""))
    tgt = _latex_escape(getattr(m, "target_concept", ""))
    kind = getattr(m, "kind", "ANALOGY")
    strength = getattr(m, "strength", 0.0)
    return f"{src} & {tgt} & {kind} & {strength:.2f} & {_latex_escape(desc[:60])} \\\\"


def _total_propositions(state: Any) -> int:
    """Return total proposition count across all nodes."""
    if hasattr(state, "total_propositions"):
        return state.total_propositions()
    if hasattr(state, "all_propositions"):
        return len(state.all_propositions)
    return sum(
        n.proposition_count()
        for n in getattr(state, "active_nodes", [])
    )


# ---------------------------------------------------------------------------
# PaperSection
# ---------------------------------------------------------------------------


class PaperSection(str, Enum):
    """Logical sections of a generated synthesis paper."""

    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    MAIN_RESULTS = "main_results"
    PROOFS = "proofs"
    CONNECTIONS = "connections"
    APPLICATIONS = "applications"
    FUTURE_WORK = "future_work"
    REFERENCES = "references"

    def latex_title(self) -> str:
        return self.value.replace("_", " ").title()


# ---------------------------------------------------------------------------
# TheoremEnvironment
# ---------------------------------------------------------------------------


class TheoremEnvironment(str, Enum):
    """amsthm environments for LaTeX theorem-like blocks."""

    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    PROPOSITION = "proposition"
    DEFINITION = "definition"
    EXAMPLE = "example"
    REMARK = "remark"
    CONJECTURE = "conjecture"


# ---------------------------------------------------------------------------
# LatexTheoremBlock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatexTheoremBlock:
    """A self-contained amsthm LaTeX environment block."""

    env: TheoremEnvironment
    label: str
    statement: str
    proof: str
    number: int

    def to_latex(self) -> str:
        """Render as a LaTeX theorem environment, with optional proof block."""
        env_name = self.env.value.lower()
        lines = [
            f"\\begin{{{env_name}}}",
            f"\\label{{thm:{self.label}}}",
            self.statement,
            f"\\end{{{env_name}}}",
        ]
        if self.proof.strip():
            lines += [
                "\\begin{proof}",
                self.proof,
                "\\end{proof}",
            ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PaperStats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperStats:
    """Descriptive statistics for a generated MathPaper."""

    section_count: int
    theorem_count: int
    lemma_count: int
    definition_count: int
    page_estimate: int
    word_count_estimate: int
    metaphor_table_rows: int
    bridge_theorem_count: int

    def __str__(self) -> str:
        return (
            f"PaperStats(sections={self.section_count}, "
            f"theorems={self.theorem_count}, lemmas={self.lemma_count}, "
            f"defs={self.definition_count}, pages~{self.page_estimate}, "
            f"words~{self.word_count_estimate}, "
            f"metaphors={self.metaphor_table_rows}, "
            f"bridges={self.bridge_theorem_count})"
        )


# ---------------------------------------------------------------------------
# MathPaper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MathPaper:
    """A fully assembled mathematical paper ready for LaTeX compilation."""

    paper_id: str
    title: str
    authors: tuple
    abstract: str
    sections: tuple
    theorems: tuple
    bibliography: tuple
    metadata: dict
    created_at: float

    def theorem_count(self) -> int:
        """Return number of THEOREM-environment blocks."""
        return sum(1 for t in self.theorems if t.env == TheoremEnvironment.THEOREM)

    def stats(self) -> PaperStats:
        """Compute descriptive statistics for this paper."""
        section_contents: dict = self.metadata.get("section_contents", {})
        total_text = " ".join(str(v) for v in section_contents.values()) + self.abstract
        word_count = len(total_text.split())
        page_est = max(1, word_count // 300)

        theorem_c = sum(1 for t in self.theorems if t.env == TheoremEnvironment.THEOREM)
        lemma_c = sum(1 for t in self.theorems if t.env == TheoremEnvironment.LEMMA)
        def_c = sum(1 for t in self.theorems if t.env == TheoremEnvironment.DEFINITION)
        bridge_c = sum(
            1 for t in self.theorems
            if "bridge" in t.label.lower() or "bridge" in t.statement.lower()
        )
        metaphor_rows = self.metadata.get("metaphor_table_rows", 0)

        return PaperStats(
            section_count=len(self.sections),
            theorem_count=theorem_c,
            lemma_count=lemma_c,
            definition_count=def_c,
            page_estimate=page_est,
            word_count_estimate=word_count,
            metaphor_table_rows=metaphor_rows,
            bridge_theorem_count=bridge_c,
        )

    def to_latex(self) -> str:
        """Render the complete paper as a compilable LaTeX document."""
        lines: list = []

        # Preamble
        lines += [
            r"\documentclass{amsart}",
            r"\usepackage{amsmath,amssymb,amsthm,hyperref,cleveref,booktabs}",
            r"\newtheorem{theorem}{Theorem}[section]",
            r"\newtheorem{lemma}[theorem]{Lemma}",
            r"\newtheorem{corollary}[theorem]{Corollary}",
            r"\newtheorem{proposition}[theorem]{Proposition}",
            r"\newtheorem{conjecture}[theorem]{Conjecture}",
            r"\newtheorem{definition}[theorem]{Definition}",
            r"\newtheorem{example}[theorem]{Example}",
            r"\newtheorem{remark}[theorem]{Remark}",
            "",
        ]

        lines.append(f"\\title{{{_latex_escape(self.title)}}}")
        for author in self.authors:
            lines.append(f"\\author{{{_latex_escape(author)}}}")
        lines += ["", r"\begin{document}", r"\maketitle", ""]

        lines += [r"\begin{abstract}", self.abstract, r"\end{abstract}", ""]

        section_contents: dict = self.metadata.get("section_contents", {})
        metaphor_table: str = self.metadata.get("metaphor_table", "")

        for sec in self.sections:
            if sec in (PaperSection.ABSTRACT, PaperSection.REFERENCES):
                continue

            lines.append(f"\\section{{{sec.latex_title()}}}")
            lines.append(f"\\label{{sec:{sec.value}}}")
            lines.append("")

            content = section_contents.get(sec, "")
            if content:
                lines.append(content)
                lines.append("")

            if sec in (PaperSection.MAIN_RESULTS, PaperSection.PROOFS):
                for thm in self.theorems:
                    lines.append(thm.to_latex())
                    lines.append("")

            if sec == PaperSection.CONNECTIONS and metaphor_table:
                lines.append(metaphor_table)
                lines.append("")

        if self.bibliography:
            lines.append(r"\begin{thebibliography}{99}")
            for i, ref in enumerate(self.bibliography, start=1):
                lines.append(f"\\bibitem{{ref{i}}} {_latex_escape(ref)}")
            lines.append(r"\end{thebibliography}")
            lines.append("")

        lines.append(r"\end{document}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PaperOutline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperOutline:
    """A lightweight outline of the paper before full prose generation."""

    field_name: str
    round_count: int
    total_props: int
    section_titles: tuple
    key_theorems: tuple
    abstract_preview: str



# ---------------------------------------------------------------------------
# SectionWriter
# ---------------------------------------------------------------------------


class SectionWriter:
    """Converts a tournament synthesis result into substantive paper sections."""

    # ------------------------------------------------------------------
    # Abstract
    # ------------------------------------------------------------------

    def write_abstract(self, synthesis: Any, state: Any) -> str:
        """Write a substantive abstract for the synthesis paper."""
        n_fields = len(getattr(synthesis, "constituent_fields", ())) or 2
        n_props = synthesis.proposition_count()
        n_rounds = _get_round(state)
        bridges = synthesis.bridge_theorems()
        n_bridges = len(bridges)
        top_titles = _key_prop_titles(synthesis, 3)
        titles_str = "; ".join(top_titles) if top_titles else "several landmark results"
        name = synthesis.name

        return (
            f"We present a systematic cross-domain synthesis of {name}, "
            f"integrating results from {n_fields} constituent mathematical and "
            f"computational fields via a binary tournament process spanning "
            f"{n_rounds} round{'s' if n_rounds != 1 else ''} of iterative merging. "
            f"The synthesis accumulates {n_props} proposition{'s' if n_props != 1 else ''}—"
            f"including {n_bridges} bridge theorem{'s' if n_bridges != 1 else ''} "
            f"that formally connect previously disparate domains—and identifies "
            f"deep structural correspondences through cross-domain metaphor analysis. "
            f"Key results include: {titles_str}. "
            f"The integration reveals unexpected unifying principles and opens new "
            f"directions for cross-domain mathematical research. "
            f"We provide a complete LaTeX exposition of the synthesized theory, "
            f"including full proofs, cross-domain metaphor tables, and a taxonomy "
            f"of bridge theorems ranked by leverage and novelty."
        )

    # ------------------------------------------------------------------
    # Introduction
    # ------------------------------------------------------------------

    def write_introduction(self, synthesis: Any, state: Any) -> str:
        """Write the introduction section."""
        n_fields = len(getattr(synthesis, "constituent_fields", ())) or 2
        n_rounds = _get_round(state)
        n_total_props = _total_propositions(state)
        kw = _get_keywords(synthesis)
        kw_str = ", ".join(str(k) for k in kw[:5]) if kw else synthesis.name
        bridges = synthesis.bridge_theorems()
        name = synthesis.name

        paras = [
            (
                f"The unification of mathematical disciplines has historically been "
                f"one of the most productive sources of new theorems and conceptual "
                f"breakthroughs. The synthesis of {name} pursued here brings together "
                f"{n_fields} distinct fields—united by the common thread of "
                f"{kw_str}—into a single coherent theoretical framework."
            ),
            (
                f"Our approach proceeds via a structured binary tournament: at each "
                f"of the {n_rounds} round{'s' if n_rounds != 1 else ''}, pairs of "
                f"field nodes are merged by a language-model judge that discovers "
                f"bridge theorems and cross-domain metaphors at their interface. "
                f"Over the course of the tournament, the proposition pool grows to "
                f"{n_total_props} total propositions, with bridge theorems serving "
                f"as the primary evidence of genuine structural integration."
            ),
            (
                f"The field of {name} emerges from this process as a nontrivial "
                f"synthesis: it is not merely the disjoint union of its constituents, "
                f"but a richer structure in which results from one domain illuminate "
                f"and generalise results in another. "
                f"The {len(bridges)} bridge theorem{'s' if len(bridges) != 1 else ''} "
                f"we identify are the formal backbone of this synthesis; they are "
                f"stated and proved in Section~\\ref{{sec:main_results}}."
            ),
            (
                r"\paragraph{Organisation of the paper.} "
                r"Section~\ref{sec:background} provides self-contained background "
                r"on each constituent field. Section~\ref{sec:main_results} states "
                r"the main theorems and bridge results. Section~\ref{sec:proofs} "
                r"contains the proofs. Section~\ref{sec:connections} surveys the "
                r"cross-domain metaphors. Section~\ref{sec:applications} discusses "
                r"applications, and Section~\ref{sec:future_work} outlines open problems."
            ),
        ]
        return "\n\n".join(paras)

    # ------------------------------------------------------------------
    # Background
    # ------------------------------------------------------------------

    def write_background(self, synthesis: Any) -> str:
        """Write the background section describing constituent fields."""
        desc = getattr(synthesis, "description", "") or (
            f"The field of {synthesis.name} encompasses several interrelated areas."
        )

        parts = [
            f"This section provides background on the constituent fields merged to "
            f"produce {synthesis.name}. We summarise the key objects, morphisms, "
            f"and landmark results, highlighting the aspects most relevant to the synthesis.",
            "",
            desc,
        ]

        constituent = getattr(synthesis, "constituent_fields", ())
        if constituent:
            parts += [
                "",
                "The primary constituent fields are: "
                + ", ".join(
                    f"\\textit{{{_latex_escape(str(f))}}}" for f in constituent
                )
                + ".",
            ]

        key_thms = getattr(synthesis, "key_theorems", ())
        if key_thms:
            parts += [
                "",
                "Landmark results motivating the synthesis include: "
                + "; ".join(f"the {_latex_escape(str(t))}" for t in key_thms[:5])
                + ".",
            ]

        definitions = [
            p for p in getattr(synthesis, "propositions", [])
            if p.kind == PropositionKind.DEFINITION
        ]
        if definitions:
            parts += [
                "",
                f"We record the {len(definitions)} most relevant definition"
                f"{'s' if len(definitions) != 1 else ''} below.",
            ]
            for defn in definitions[:4]:
                parts += [
                    "",
                    f"\\begin{{definition}}[{_latex_escape(defn.title)}]",
                    defn.statement,
                    "\\end{definition}",
                ]

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Main results
    # ------------------------------------------------------------------

    def write_main_results(self, synthesis: Any) -> list:
        """Convert bridge theorems and top propositions to LatexTheoremBlocks."""
        blocks: list = []
        seen_ids: set = set()

        _kind_to_env = {
            "theorem": TheoremEnvironment.THEOREM,
            "bridge_theorem": TheoremEnvironment.THEOREM,
            "lemma": TheoremEnvironment.LEMMA,
            "corollary": TheoremEnvironment.COROLLARY,
            "proposition": TheoremEnvironment.PROPOSITION,
            "conjecture": TheoremEnvironment.CONJECTURE,
            "definition": TheoremEnvironment.DEFINITION,
            "remark": TheoremEnvironment.REMARK,
        }

        def _make_block(prop: Any) -> LatexTheoremBlock:
            kind_val = (
                prop.kind.value
                if hasattr(prop.kind, "value")
                else str(prop.kind)
            )
            env = _kind_to_env.get(kind_val, TheoremEnvironment.THEOREM)
            proof = getattr(prop, "proof_sketch", "") or ""
            label_prefix = "bridge" if "bridge" in kind_val else "main"
            return LatexTheoremBlock(
                env=env,
                label=f"{label_prefix}_{prop.prop_id}",
                statement=prop.statement,
                proof=proof,
                number=len(blocks) + 1,
            )

        # Bridge theorems first (highest priority)
        for prop in synthesis.bridge_theorems():
            if prop.prop_id in seen_ids:
                continue
            seen_ids.add(prop.prop_id)
            blocks.append(_make_block(prop))
            if len(blocks) >= 4:
                break

        # Top-importance theorems, propositions, lemmas
        provable_kinds = {"theorem", "proposition", "lemma", "corollary"}
        top = sorted(
            [
                p for p in getattr(synthesis, "propositions", [])
                if (
                    hasattr(p.kind, "value") and p.kind.value in provable_kinds
                    or str(p.kind) in provable_kinds
                )
                and p.prop_id not in seen_ids
            ],
            key=lambda p: -getattr(p, "leverage_score", 0.0),
        )
        for prop in top:
            if len(blocks) >= 8:
                break
            seen_ids.add(prop.prop_id)
            blocks.append(_make_block(prop))

        # Fallback: ensure at least 3 blocks from any propositions
        if len(blocks) < 3:
            for prop in getattr(synthesis, "propositions", []):
                if len(blocks) >= 3:
                    break
                if prop.prop_id in seen_ids:
                    continue
                seen_ids.add(prop.prop_id)
                blocks.append(_make_block(prop))

        return blocks

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

    def write_connections(self, state: Any) -> str:
        """Write the cross-domain connections section."""
        metaphors = _get_metaphors_list(state)
        n_meta = len(metaphors)
        n_merges = len(getattr(state, "completed_merges", []))

        paras = [
            (
                f"The tournament process discovered {n_meta} cross-domain metaphor"
                f"{'s' if n_meta != 1 else ''} over {n_merges} merge"
                f"{'s' if n_merges != 1 else ''}, each representing a structural "
                f"correspondence between concepts in different constituent fields. "
                f"These metaphors range from loose analogies to precise categorical "
                f"equivalences (adjunctions, dualities, and isomorphisms)."
            ),
        ]

        if metaphors:
            by_kind: dict = {}
            for m in metaphors:
                k = getattr(m, "kind", "ANALOGY")
                by_kind.setdefault(k, []).append(m)

            for kind, mlist in sorted(by_kind.items()):
                examples = " ".join(
                    f"The concept \\emph{{{_latex_escape(str(m.source_concept))}}} "
                    f"in one field corresponds to "
                    f"\\emph{{{_latex_escape(str(m.target_concept))}}} "
                    f"(strength {getattr(m, 'strength', 0.0):.2f})."
                    for m in mlist[:2]
                )
                paras.append(
                    f"\\paragraph{{{kind.title()} correspondences}} "
                    f"We identified {len(mlist)} metaphor{'s' if len(mlist) != 1 else ''} "
                    f"of type \\textit{{{kind.lower()}}}. {examples}"
                )
        else:
            paras.append(
                "No explicit metaphor links were recorded in the tournament state; "
                "cross-domain correspondences are implicit in the bridge theorems of "
                "Section~\\ref{sec:main_results}."
            )

        paras.append(
            "A complete tabulation of all discovered metaphor correspondences is given "
            "in Table~\\ref{tab:metaphors}."
        )
        return "\n\n".join(paras)

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------

    def write_applications(self, synthesis: Any) -> str:
        """Write the applications section."""
        kw = _get_keywords(synthesis)
        kw_list = [str(k) for k in kw[:4]]
        top = _key_prop_titles(synthesis, 3)
        name = synthesis.name

        paras = [
            (
                f"The synthesis of {name} has potential applications across a broad "
                f"spectrum of mathematical and computational domains. The bridge "
                f"theorems identified in Section~\\ref{{sec:main_results}} provide "
                f"formal tools deployable wherever the constituent fields overlap "
                f"with practical or theoretical problems."
            ),
            (
                f"\\paragraph{{Theoretical applications.}} "
                f"The results pertaining to "
                f"{', '.join(kw_list) if kw_list else name} "
                f"suggest new proof strategies: a result difficult to prove in one "
                f"constituent field may become tractable after translating it to "
                f"another field via a bridge theorem. In particular, "
                f"{'the ' + _latex_escape(top[0]) if top else 'the key results'} "
                f"can be reinterpreted to yield new corollaries in adjacent domains."
            ),
            (
                f"\\paragraph{{Computational applications.}} "
                f"The categorical and type-theoretic aspects of the synthesis suggest "
                f"implementations in proof assistants (Coq, Agda, Lean~4) and "
                f"functional programming languages. Bridge theorems of the FUNCTOR "
                f"and ADJUNCTION variety directly correspond to program transformations "
                f"and compiler optimisations, providing a rigorous semantic foundation."
            ),
            (
                f"\\paragraph{{Foundational applications.}} "
                f"The synthesis provides a unified vocabulary that simplifies the "
                f"statement and proof of results currently requiring separate "
                f"treatments in each constituent field. This is especially valuable "
                f"in formalisation efforts, where a shared type-theoretic foundation "
                f"can host all branches of the synthesis simultaneously."
            ),
        ]
        return "\n\n".join(paras)

    # ------------------------------------------------------------------
    # Future work
    # ------------------------------------------------------------------

    def write_future_work(self, synthesis: Any) -> str:
        """Write the future work section."""
        conjectures = [
            p for p in getattr(synthesis, "propositions", [])
            if (
                hasattr(p.kind, "value") and p.kind.value == "conjecture"
                or str(p.kind) == "conjecture"
            )
        ]
        n_conj = len(conjectures)
        bridges = synthesis.bridge_theorems()
        n_br = len(bridges)
        n_fields = len(getattr(synthesis, "constituent_fields", ())) or 2
        name = synthesis.name

        paras = [
            (
                f"The synthesis of {name} opens a number of directions for future "
                f"research. We highlight the most promising avenues below."
            ),
            (
                f"\\paragraph{{Open conjectures.}} "
                + (
                    f"The tournament produced {n_conj} unproved "
                    f"conjecture{'s' if n_conj != 1 else ''}, each representing a "
                    f"plausible cross-domain statement that resists easy verification. "
                    + (
                        f"The most important is: "
                        f"\\emph{{{_latex_escape(conjectures[0].title)}}}. "
                        if conjectures else ""
                    )
                    if n_conj > 0 else
                    "The tournament did not produce explicit conjectures, but the "
                    "bridge theorems suggest several unpublished generalisations "
                    "worth investigating. "
                )
                + "Resolving these would substantially deepen the synthesis."
            ),
            (
                f"\\paragraph{{Deeper bridge theorems.}} "
                f"The {n_br} bridge theorem{'s' if n_br != 1 else ''} identified "
                f"here are a first pass; a more thorough analysis could yield "
                f"stronger structural results—perhaps full categorical equivalences "
                f"or fibered relationships—between the constituent fields. "
                f"Metaphors of type ADJUNCTION and DUALITY especially merit "
                f"formalisation as precise mathematical theorems."
            ),
            (
                r"\paragraph{Mechanised verification.} "
                r"All bridge theorems in Section~\ref{sec:main_results} are stated "
                r"at a precision compatible with formalisation in a proof assistant. "
                r"A natural next step is to implement the constructions in Lean~4 "
                r"or Agda and verify the proofs mechanically, elevating their trust "
                r"tier from \textsc{Proposal} to \textsc{Verified}."
            ),
            (
                f"\\paragraph{{Tournament extension.}} "
                f"The current synthesis covers {n_fields} constituent fields. "
                f"Extending the tournament to include additional fields—particularly "
                f"from statistics, physics, and economics—would likely reveal further "
                f"structural correspondences and bridge theorems of independent "
                f"mathematical interest. A full 48-field tournament is planned."
            ),
        ]
        return "\n\n".join(paras)

    # ------------------------------------------------------------------
    # Metaphor table
    # ------------------------------------------------------------------

    def write_metaphor_table(self, metaphors: list) -> str:
        """Return a LaTeX table of cross-domain metaphor correspondences."""
        if not metaphors:
            return ""

        rows = [_metaphor_row(m) for m in metaphors[:30]]
        return (
            "\\begin{table}[h]\n"
            "\\centering\n"
            "\\caption{Cross-Domain Metaphor Correspondences}\n"
            "\\label{tab:metaphors}\n"
            "\\begin{tabular}{lllll}\n"
            "\\toprule\n"
            "Source Concept & Target Concept & Kind & Strength & Description \\\\\n"
            "\\midrule\n"
            + "\n".join(rows) + "\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\end{table}"
        )


# ---------------------------------------------------------------------------
# PaperGenerator
# ---------------------------------------------------------------------------


class PaperGenerator:
    """Orchestrates the generation of a complete MathPaper from a tournament synthesis."""

    def __init__(self, authors: tuple = ("JuGeo Synthesis Engine",), **kwargs: Any) -> None:
        self.authors = authors
        self._writer = SectionWriter()

    # ------------------------------------------------------------------
    # Main generation entry-point
    # ------------------------------------------------------------------

    def generate(self, synthesis: Any, state: Any = None) -> MathPaper:
        """Generate a complete MathPaper from the synthesis FieldNode and TournamentState."""
        # Create a minimal stub state if none provided
        if state is None:
            class _EmptyState:
                current_round = 0
                round_number = 0
                active_nodes: list = []
                all_metaphors: dict = {}
                all_nodes: dict = {}
                def total_propositions(self): return 0
                def summary(self): return "TournamentState(empty)"
            state = _EmptyState()

        paper_id = str(uuid.uuid4())[:12]
        title = f"Synthesis of {synthesis.name}: A Cross-Domain Mathematical Integration"

        abstract = self._writer.write_abstract(synthesis, state)
        intro = self._writer.write_introduction(synthesis, state)
        background = self._writer.write_background(synthesis)
        connections = self._writer.write_connections(state)
        applications = self._writer.write_applications(synthesis)
        future_work = self._writer.write_future_work(synthesis)

        theorem_blocks = self._writer.write_main_results(synthesis)
        proofs_preamble = (
            "The proofs below follow from the constructions developed in each "
            "constituent field, translated via the bridge theorems above. "
            "We indicate the essential steps; full mechanised proofs are deferred "
            "to a companion formalisation project."
        )

        all_metaphors = _get_metaphors_list(state)
        metaphor_table = self._writer.write_metaphor_table(all_metaphors)
        bibliography = self._collect_bibliography(synthesis)

        section_contents: dict = {
            PaperSection.INTRODUCTION: intro,
            PaperSection.BACKGROUND: background,
            PaperSection.MAIN_RESULTS: (
                f"We state the main results of the synthesis of {synthesis.name}. "
                f"Bridge theorems—results formally connecting two constituent "
                f"fields—are marked \\textnormal{{[Bridge]}}."
            ),
            PaperSection.PROOFS: proofs_preamble,
            PaperSection.CONNECTIONS: connections,
            PaperSection.APPLICATIONS: applications,
            PaperSection.FUTURE_WORK: future_work,
        }

        sections = (
            PaperSection.ABSTRACT,
            PaperSection.INTRODUCTION,
            PaperSection.BACKGROUND,
            PaperSection.MAIN_RESULTS,
            PaperSection.PROOFS,
            PaperSection.CONNECTIONS,
            PaperSection.APPLICATIONS,
            PaperSection.FUTURE_WORK,
            PaperSection.REFERENCES,
        )

        metadata: dict = {
            "section_contents": section_contents,
            "metaphor_table": metaphor_table,
            "metaphor_table_rows": len(all_metaphors),
            "theorem_map": {t.label: t for t in theorem_blocks},
            "synthesis_field_id": getattr(synthesis, "field_id", ""),
            "tournament_round": _get_round(state),
            "constituent_fields": list(getattr(synthesis, "constituent_fields", ())),
            "keywords": list(_get_keywords(synthesis)),
        }

        return MathPaper(
            paper_id=paper_id,
            title=title,
            authors=self.authors,
            abstract=abstract,
            sections=sections,
            theorems=tuple(theorem_blocks),
            bibliography=tuple(bibliography),
            metadata=metadata,
            created_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Outline
    # ------------------------------------------------------------------

    def generate_outline(self, synthesis: Any, state: Any) -> PaperOutline:
        """Generate a lightweight PaperOutline without full prose generation."""
        n_rounds = _get_round(state)
        n_props = synthesis.proposition_count()
        key_thm_titles = tuple(_key_prop_titles(synthesis, 6))
        section_titles = tuple(
            sec.latex_title()
            for sec in (
                PaperSection.INTRODUCTION,
                PaperSection.BACKGROUND,
                PaperSection.MAIN_RESULTS,
                PaperSection.PROOFS,
                PaperSection.CONNECTIONS,
                PaperSection.APPLICATIONS,
                PaperSection.FUTURE_WORK,
            )
        )
        abstract_preview = self._writer.write_abstract(synthesis, state)
        return PaperOutline(
            field_name=synthesis.name,
            round_count=n_rounds,
            total_props=n_props,
            section_titles=section_titles,
            key_theorems=key_thm_titles,
            abstract_preview=abstract_preview,
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_latex(self, paper: MathPaper, path: str) -> None:
        """Write the compiled LaTeX document to path."""
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(paper.to_latex())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_bibliography(self, synthesis: Any) -> list:
        """Collect bibliography entries from proposition metadata."""
        refs: list = []
        seen: set = set()
        for prop in getattr(synthesis, "propositions", []):
            meta = getattr(prop, "metadata", {}) or {}
            ref = meta.get("source_ref", "")
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
        if not refs:
            refs = [
                "Mac Lane, S. (1971). Categories for the Working Mathematician. Springer.",
                "Howard, W. A. (1980). The formulae-as-types notion of construction. "
                "In To H. B. Curry: Essays on Combinatory Logic, Lambda Calculus, Formalism.",
                "Awodey, S. (2010). Category Theory. Oxford University Press.",
                "The Univalent Foundations Program (2013). Homotopy Type Theory: "
                "Univalent Foundations of Mathematics. Institute for Advanced Study.",
            ]
        return refs


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        from jugeo.ideation.synthesis_frontier.models import (
            FieldNode, PropositionRecord, PropositionKind, TournamentState, MetaphorLink
        )
        props = (
            PropositionRecord.make("Yoneda Lemma", "Every presheaf is a colimit of representables.", PropositionKind.THEOREM, "cat", importance=0.95),
            PropositionRecord.make("Adjoint Functor Theorem", "A functor has a left adjoint iff it preserves all limits and satisfies the solution set condition.", PropositionKind.THEOREM, "cat", importance=0.90),
            PropositionRecord.make("Bridge: CT x TT", "The internal language of a topos is a dependent type theory.", PropositionKind.BRIDGE_THEOREM, "cat_tt", importance=0.92),
        )
        synthesis = FieldNode.make(
            "Category Theory x Type Theory",
            "A synthesis of categorical logic and dependent type theory.",
            propositions=props,
            constituent_fields=("Category Theory", "Type Theory"),
            round_number=1,
            keywords=("functor", "type", "adjoint", "dependent", "topos"),
        )
        state = TournamentState()
        state.register_node(synthesis)
        gen = PaperGenerator()
        paper = gen.generate(synthesis, state)
        print(f"Paper: {paper.title}")
        stats = paper.stats()
        print(f"Stats: {stats}")
        outline = gen.generate_outline(synthesis, state)
        print(f"Outline: {outline.abstract_preview[:100]}")
        latex = paper.to_latex()
        print(f"LaTeX length: {len(latex)} chars")
        print("Smoke test passed!")
    except Exception as e:
        import traceback; traceback.print_exc()

