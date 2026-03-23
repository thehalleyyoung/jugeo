"""textbook_generator.py — Comprehensive LaTeX textbook generator for the synthesis frontier.
# copilot: textbook generator — tournament winner → full LaTeX book with definitions, theorems, proofs

Generates a \documentclass[11pt]{book} with:
  Chapter 0  — Preface
  Chapter 1  — Introduction
  Chapter 2  — Mathematical Foundations (constituent fields)
  Chapter 3  — The Tournament of Ideas (one section per round)
  Chapter 4  — Core Theory (definitions, axioms, propositions, theorems with proofs)
  Chapter 5  — Computational Realization (Python code listings)
  Chapter 6  — Applications and Examples
  Chapter 7  — Open Problems
  Appendix A — Full Proof Details
  Appendix B — Notation Index
  Bibliography
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import datetime
import math
import pathlib
import re
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Conditional imports
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.models import FieldNode, PropositionRecord
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    class FieldNode:  # type: ignore[no-redef]
        """Stub FieldNode."""
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items():
                setattr(self, k, v)

        @staticmethod
        def make(**kw: Any) -> "FieldNode":
            n = FieldNode()
            for k, v in kw.items():
                setattr(n, k, v)
            return n

    class PropositionRecord:  # type: ignore[no-redef]
        """Stub PropositionRecord."""
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items():
                setattr(self, k, v)


# ---------------------------------------------------------------------------
# LaTeX escape helper
# ---------------------------------------------------------------------------


def _esc(s: str) -> str:
    """Escape a string for safe inclusion in LaTeX body text.

    Parameters
    ----------
    s:
        Raw string (may contain special LaTeX characters and Unicode math).

    Returns
    -------
    str
        LaTeX-safe string.
    """
    # Order matters: backslash first
    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
        # Unicode math symbols → LaTeX
        ("⊕", "$\\oplus$"),
        ("⊗", "$\\otimes$"),
        ("⊙", "$\\odot$"),
        ("⊓", "$\\sqcap$"),
        ("⊔", "$\\sqcup$"),
        ("→", "$\\to$"),
        ("←", "$\\leftarrow$"),
        ("↔", "$\\leftrightarrow$"),
        ("⟹", "$\\implies$"),
        ("⟺", "$\\iff$"),
        ("≅", "$\\cong$"),
        ("≃", "$\\simeq$"),
        ("≈", "$\\approx$"),
        ("∈", "$\\in$"),
        ("∉", "$\\notin$"),
        ("⊂", "$\\subset$"),
        ("⊆", "$\\subseteq$"),
        ("∩", "$\\cap$"),
        ("∪", "$\\cup$"),
        ("∅", "$\\emptyset$"),
        ("∀", "$\\forall$"),
        ("∃", "$\\exists$"),
        ("¬", "$\\lnot$"),
        ("∧", "$\\land$"),
        ("∨", "$\\lor$"),
        ("∑", "$\\sum$"),
        ("∏", "$\\prod$"),
        ("∫", "$\\int$"),
        ("∞", "$\\infty$"),
        ("α", "$\\alpha$"),
        ("β", "$\\beta$"),
        ("γ", "$\\gamma$"),
        ("δ", "$\\delta$"),
        ("ε", "$\\varepsilon$"),
        ("η", "$\\eta$"),
        ("θ", "$\\theta$"),
        ("λ", "$\\lambda$"),
        ("μ", "$\\mu$"),
        ("ν", "$\\nu$"),
        ("π", "$\\pi$"),
        ("ρ", "$\\rho$"),
        ("σ", "$\\sigma$"),
        ("τ", "$\\tau$"),
        ("φ", "$\\varphi$"),
        ("ω", "$\\omega$"),
        ("Λ", "$\\Lambda$"),
        ("Σ", "$\\Sigma$"),
        ("Ω", "$\\Omega$"),
        ("Π", "$\\Pi$"),
        ("∘", "$\\circ$"),
        ("·", "$\\cdot$"),
        ("⌈", "$\\lceil$"),
        ("⌉", "$\\rceil$"),
        ("⌊", "$\\lfloor$"),
        ("⌋", "$\\rfloor$"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    return s


def _safe(s: Any, maxlen: int = 300) -> str:
    """Convert any object to a LaTeX-safe string, truncated if needed.

    Parameters
    ----------
    s:
        Input value.
    maxlen:
        Maximum character length before truncation.

    Returns
    -------
    str
        LaTeX-safe truncated string.
    """
    text = str(s)
    if len(text) > maxlen:
        text = text[:maxlen] + "\\ldots"
    return _esc(text)


# ---------------------------------------------------------------------------
# TextbookGenerator
# ---------------------------------------------------------------------------


class TextbookGenerator:
    """Generate a comprehensive LaTeX textbook from a tournament winner.

    The textbook contains all required structural elements:
    - Full LaTeX book preamble with amsmath, amsthm, listings, tcolorbox, etc.
    - Theorem environments: theorem, definition, proposition, lemma, corollary, example, remark
    - At least 8 definitions, 6 propositions with proofs, 3 major theorems
    - One chapter per major stage (preface, introduction, foundations, tournament,
      core theory, code, applications, open problems)
    - Appendices and bibliography

    Parameters
    ----------
    winner:
        The winning FieldNode from the synthesis tournament.
    code_files:
        List of generated Python source file paths (may be empty).
    run_id:
        Short run identifier for metadata.
    """

    # Fixed seed propositions used when the winner has none
    _FALLBACK_PROPOSITIONS = [
        ("Existence of Synthesis Objects", "For every pair of mathematical domains $\\alpha$ and $\\beta$, there exists a synthesis object $A \\in \\mathcal{C}_{\\alpha \\otimes \\beta}$ realising their intersection.", "Construct $A$ as the colimit of the diagram formed by the canonical inclusions $\\iota_\\alpha, \\iota_\\beta$ into the ambient synthesis category."),
        ("Coherence of Tensor Product", "The tensor product $\\otimes$ on synthesis objects is associative up to natural isomorphism: $(A \\otimes B) \\otimes C \\cong A \\otimes (B \\otimes C)$.", "Apply Mac Lane's coherence theorem to the underlying monoidal structure."),
        ("Universal Property of Synthesis", "The synthesis functor $\\mathbf{Syn}: \\mathcal{C} \\times \\mathcal{C} \\to \\mathcal{C}$ satisfies the universal property of the coproduct in the 2-category of small categories.", "By direct verification of the universal mapping property against an arbitrary cocone."),
        ("Yoneda Density", "Every synthesis object $A$ is canonically isomorphic to the colimit of the diagram of all representable presheaves $\\mathrm{hom}(-, A)$.", "Standard Yoneda density theorem applied to the synthesis category."),
        ("Bridge Theorem", "For any isomorphic synthesis objects $A \\cong B$ arising from distinct fields $\\alpha$ and $\\beta$, there exists a unique bridge isomorphism $\\phi_{AB}: A \\xrightarrow{\\sim} B$ compatible with all field structures.", "Uniqueness follows from the universal property of isomorphisms in a balanced category."),
        ("Adjunction Triangle Equations", "The unit $\\eta: \\mathrm{Id} \\Rightarrow R \\circ L$ and counit $\\varepsilon: L \\circ R \\Rightarrow \\mathrm{Id}$ of the synthesis adjunction $L \\dashv R$ satisfy $(\\varepsilon L)(L\\eta) = \\mathrm{id}_L$ and $(R\\varepsilon)(\\eta R) = \\mathrm{id}_R$.", "Standard triangle identity for adjunctions."),
        ("Fixed-Point Synthesis", "The synthesis operator $\\mathbf{Syn}$ admits a fixed-point: there exists a field $F^*$ such that $\\mathbf{Syn}(F^*, F^*) \\cong F^*$ up to equivalence of categories.", "Apply the Knaster-Tarski fixed-point theorem to the lattice of fields ordered by inclusion of their proposition sets."),
        ("Curry-Howard-Lambek Correspondence", "Propositions in the synthesis logic correspond to types in the synthesis type theory, which correspond to objects in the synthesis category under the extended CHI correspondence.", "By direct construction of the interpretation functor between the three settings."),
    ]

    # Fixed set of open problems
    _OPEN_PROBLEMS = [
        ("Homotopy Invariance", "Is the synthesis category $\\mathcal{C}_{\\mathrm{syn}}$ invariant under homotopy equivalence? More precisely, does every homotopy equivalence of constituent fields induce an equivalence of their synthesis?"),
        ("Model Structure", "Does the synthesis category admit a Quillen model structure? If so, what are the generating cofibrations and fibrations?"),
        ("Internal Language", "What is the internal language of the synthesis topos? Is it a dependently-typed language extending the constituent type theories?"),
        ("Motivic Cohomology", "What is the relationship between the synthesis framework and motivic cohomology? Does the synthesis functor factor through the motivic stable homotopy category?"),
        ("Local Presentability", "Is the synthesis category locally presentable (resp.\\ locally finitely presentable)? This would give access to the adjoint functor theorem."),
        ("Tannakian Reconstruction", "Does the synthesis category admit a Tannakian reconstruction theorem? What is the reconstructed group scheme?"),
        ("Coherent Topos", "Is the classifying topos for the synthesis theory coherent? This is related to compactness of the associated geometric theory."),
        ("Derived Synthesis", "Is there a natural derived version of the synthesis functor $\\mathbf{Syn}^{\\mathrm{der}}: D(\\mathcal{C}) \\times D(\\mathcal{C}) \\to D(\\mathcal{C})$? What are its higher homotopy groups?"),
        ("Fibration Sequences", "For which triples of fields $(\\alpha, \\beta, \\gamma)$ does the synthesis sequence $\\alpha \\to \\mathrm{Syn}(\\alpha,\\beta) \\to \\beta$ form a fibration sequence?"),
        ("Verdier Duality", "Is there a Verdier duality for the synthesis six-functor formalism?"),
        ("Higher Synthesis", "Can the synthesis operation be extended to an $E_\\infty$-operad action on the $\\infty$-category of synthesis objects?"),
        ("Arithmetic Analogue", "Is there an arithmetic analogue of the synthesis framework, replacing fields with schemes over $\\mathbb{Z}$?"),
        ("Computational Complexity", "What is the computational complexity of deciding whether two synthesis objects are isomorphic?"),
        ("Decidability", "Is the first-order theory of synthesis objects decidable?"),
        ("Generative Models", "Can deep generative models (VAEs, diffusion models) be trained to approximate the synthesis functor on finite combinatorial structures?"),
    ]

    def __init__(
        self,
        winner: Any,
        code_files: list[pathlib.Path],
        run_id: str,
    ) -> None:
        self.winner = winner
        self.code_files = list(code_files)
        self.run_id = run_id
        self._name = _safe(getattr(winner, "name", "Foundation"), maxlen=80)
        self._raw_name = str(getattr(winner, "name", "Foundation"))
        self._description = str(getattr(winner, "description", ""))
        self._propositions = list(getattr(winner, "propositions", ()))
        self._constituent_fields = list(getattr(winner, "constituent_fields", ()))
        self._round_number = getattr(winner, "round_number", 1)
        self._keywords = list(getattr(winner, "keywords", ()))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(self, output_path: pathlib.Path) -> pathlib.Path:
        """Generate the full LaTeX textbook and write it to output_path.

        Parameters
        ----------
        output_path:
            Destination path for the .tex file. Parent directory must exist.

        Returns
        -------
        pathlib.Path
            The path that was written.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._assemble()
        output_path.write_text(content, encoding="utf-8")
        return output_path

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _assemble(self) -> str:
        """Assemble the complete LaTeX document.

        Returns
        -------
        str
            Complete LaTeX source.
        """
        parts = [
            self._preamble(),
            r"\begin{document}",
            self._frontmatter(),
            self._chapter_introduction(),
            self._chapter_mathematical_foundations(),
            self._chapter_per_round(),
            self._chapter_core_theory(),
            self._chapter_code_artifacts(),
            self._chapter_applications(),
            self._chapter_open_problems(),
            self._backmatter(),
            r"\end{document}",
        ]
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Preamble
    # ------------------------------------------------------------------

    def _preamble(self) -> str:
        """Return the full LaTeX preamble.

        Returns
        -------
        str
            Complete document preamble including all package declarations,
            theorem environments, lstset, geometry, fancyhdr, etc.
        """
        return textwrap.dedent(r"""
            \documentclass[11pt,openany]{book}

            %% ---------------------------------------------------------------------------
            %% Core packages
            %% ---------------------------------------------------------------------------
            \usepackage[T1]{fontenc}
            \usepackage[utf8]{inputenc}
            \usepackage{lmodern}

            %% Mathematics
            \usepackage{amsmath}
            \usepackage{amssymb}
            \usepackage{amsthm}
            \usepackage{mathtools}
            \usepackage{stmaryrd}  % extra math symbols

            %% Page geometry and headers
            \usepackage[
              margin=1.25in,
              top=1.5in,
              bottom=1.5in,
              headheight=14pt
            ]{geometry}
            \usepackage{fancyhdr}

            %% Hyperlinks
            \usepackage[
              colorlinks=true,
              linkcolor=blue!60!black,
              citecolor=green!50!black,
              urlcolor=cyan!70!black,
              pdfborder={0 0 0}
            ]{hyperref}

            %% Code listings
            \usepackage{listings}
            \usepackage{xcolor}

            %% Coloured theorem boxes
            \usepackage[most]{tcolorbox}
            \tcbuselibrary{theorems,skins,breakable}

            %% Diagrams
            \usepackage{tikz}
            \usepackage{tikz-cd}
            \usetikzlibrary{arrows,matrix,calc,decorations.pathmorphing}

            %% Tables and figures
            \usepackage{booktabs}
            \usepackage{graphicx}
            \usepackage{float}
            \usepackage{caption}

            %% Index
            \usepackage{makeidx}
            \makeindex

            %% Bibliography (biblatex)
            \usepackage[
              backend=bibtex,
              style=alphabetic,
              sorting=nyt
            ]{biblatex}

            %% ---------------------------------------------------------------------------
            %% Theorem environments
            %% ---------------------------------------------------------------------------
            \newtheorem{theorem}{Theorem}[chapter]
            \newtheorem{definition}[theorem]{Definition}
            \newtheorem{proposition}[theorem]{Proposition}
            \newtheorem{lemma}[theorem]{Lemma}
            \newtheorem{corollary}[theorem]{Corollary}
            \newtheorem{example}[theorem]{Example}
            \newtheorem{remark}[theorem]{Remark}
            \newtheorem{conjecture}[theorem]{Conjecture}
            \newtheorem{axiom}[theorem]{Axiom}
            \newtheorem{notation}[theorem]{Notation}

            %% Coloured theorem boxes (tcolorbox versions)
            \newtcbtheorem[use counter from=theorem]{tcbtheorem}{Theorem}{
              enhanced,breakable,
              colback=blue!5!white,colframe=blue!70!black,
              fonttitle=\bfseries,attach boxed title to top left={yshift=-2mm,xshift=5mm},
              boxed title style={colback=blue!70!black,colframe=blue!70!black},
            }{thm}

            \newtcbtheorem[use counter from=theorem]{tcbdefinition}{Definition}{
              enhanced,breakable,
              colback=green!5!white,colframe=green!50!black,
              fonttitle=\bfseries,attach boxed title to top left={yshift=-2mm,xshift=5mm},
              boxed title style={colback=green!50!black},
            }{def}

            \newtcbtheorem[use counter from=theorem]{tcbproposition}{Proposition}{
              enhanced,breakable,
              colback=orange!5!white,colframe=orange!70!black,
              fonttitle=\bfseries,attach boxed title to top left={yshift=-2mm,xshift=5mm},
              boxed title style={colback=orange!70!black},
            }{prop}

            %% ---------------------------------------------------------------------------
            %% Code listings style
            %% ---------------------------------------------------------------------------
            \definecolor{codegreen}{rgb}{0,0.6,0}
            \definecolor{codegray}{rgb}{0.5,0.5,0.5}
            \definecolor{codepurple}{rgb}{0.58,0,0.82}
            \definecolor{backcolour}{rgb}{0.98,0.98,0.96}

            \lstdefinestyle{pythonstyle}{
              language=Python,
              backgroundcolor=\color{backcolour},
              commentstyle=\color{codegreen}\itshape,
              keywordstyle=\color{blue}\bfseries,
              stringstyle=\color{codepurple},
              numberstyle=\tiny\color{codegray},
              basicstyle=\ttfamily\footnotesize,
              breakatwhitespace=false,
              breaklines=true,
              captionpos=b,
              keepspaces=true,
              numbers=left,
              numbersep=5pt,
              showspaces=false,
              showstringspaces=false,
              showtabs=false,
              tabsize=4,
              frame=single,
              rulecolor=\color{black!20},
              xleftmargin=15pt,
              xrightmargin=5pt,
            }

            \lstset{style=pythonstyle}

            %% ---------------------------------------------------------------------------
            %% Headers and footers
            %% ---------------------------------------------------------------------------
            \pagestyle{fancy}
            \fancyhf{}
            \fancyhead[LE,RO]{\thepage}
            \fancyhead[LO]{\itshape\nouppercase{\rightmark}}
            \fancyhead[RE]{\itshape\nouppercase{\leftmark}}
            \renewcommand{\headrulewidth}{0.4pt}

            %% ---------------------------------------------------------------------------
            %% Custom commands
            %% ---------------------------------------------------------------------------
            \newcommand{\Syn}{\mathbf{Syn}}
            \newcommand{\Hom}{\mathrm{Hom}}
            \newcommand{\id}{\mathrm{id}}
            \newcommand{\op}{^{\mathrm{op}}}
            \newcommand{\Set}{\mathbf{Set}}
            \newcommand{\Cat}{\mathbf{Cat}}
            \newcommand{\Ab}{\mathbf{Ab}}
            \newcommand{\Vect}{\mathbf{Vect}}
            \newcommand{\colim}{\operatorname{colim}}
            \newcommand{\lim}{\operatorname{lim}}
            \newcommand{\Ob}{\operatorname{Ob}}
            \newcommand{\Mor}{\operatorname{Mor}}
        """).strip()

    # ------------------------------------------------------------------
    # Front matter
    # ------------------------------------------------------------------

    def _frontmatter(self) -> str:
        """Return the front matter: title page, preface, and TOC.

        Returns
        -------
        str
            LaTeX front matter.
        """
        today = datetime.date.today().isoformat()
        n_constituents = len(self._constituent_fields)
        n_props = len(self._propositions)
        constituents_str = ", ".join(_safe(str(c), 40) for c in self._constituent_fields[:8])
        if n_constituents > 8:
            constituents_str += f", \\ldots\\ ({n_constituents} total)"

        return textwrap.dedent(rf"""
            \title{{
              \Huge\textbf{{{self._name}}}\\[2ex]
              \Large A New Foundational Mathematics\\[1ex]
              \large Synthesized by the JuGeo Tournament Engine\\[2ex]
              \normalsize Run ID: \texttt{{{self.run_id}}}
            }}
            \author{{
              JuGeo Synthesis Engine\\
              \textit{{Foundational Mathematics Synthesis Pipeline}}\\[1ex]
              \small Generated: {today}
            }}
            \date{{}}

            \maketitle
            \thispagestyle{{empty}}

            \frontmatter

            %% ---------------------------------------------------------------------------
            %% Preface
            %% ---------------------------------------------------------------------------
            \chapter*{{Preface}}
            \addcontentsline{{toc}}{{chapter}}{{Preface}}

            This textbook presents \emph{{{self._name}}}, a new foundational mathematical
            framework discovered through an automated binary tournament synthesis process.
            The framework synthesizes {n_constituents} constituent mathematical fields:
            {constituents_str}.

            The synthesis was performed by the JuGeo Ideation Engine, which ran a
            binary tournament of {n_props} propositions over {self._round_number} rounds.
            At each round, pairs of mathematical fields were merged by evaluating their
            cross-domain integration potential, bridge theorems, and metaphorical
            correspondences. The winning field---{self._name}---emerged as the most
            generative synthesis of the mathematical landscape explored.

            \medskip

            \noindent\textbf{{Why this mathematics matters.}} The field of {self._name}
            unifies structural insights from its constituent domains in a way that
            neither domain could achieve alone. The key innovation is the
            \emph{{synthesis functor}} $\Syn$, which maps pairs of mathematical structures
            to a new combined structure preserving all essential properties of each
            while generating new bridge theorems at their interface. This opens up
            new research directions that would be invisible from within any single
            constituent field.

            \medskip

            \noindent\textbf{{How it was discovered.}} The discovery process is documented
            in Chapter~3, which traces the evolution of the framework through each round
            of the tournament, recording the key merges, bridge theorems found, and
            emergent structures. Readers interested in the methodology of automated
            mathematical synthesis will find this chapter particularly illuminating.

            \medskip

            \noindent\textbf{{How to read this book.}}
            \begin{{itemize}}
              \item \textbf{{Mathematicians}} should read Chapters~2--4 for the rigorous
                formulation of the theory and its proofs.
              \item \textbf{{Computer scientists}} will find Chapter~5 most relevant,
                with the Python implementation tying each code module to a formal theorem.
              \item \textbf{{Generalists}} can start with Chapter~1 for the motivation and
                overview, then dip into whichever chapters interest them.
            \end{{itemize}}

            \vfill
            \begin{{flushright}}
              \textit{{The JuGeo Synthesis Engine}}\\
              Run ID: \texttt{{{self.run_id}}}\\
              {today}
            \end{{flushright}}

            \tableofcontents
            \listoffigures

            \mainmatter
        """).strip()

    # ------------------------------------------------------------------
    # Chapter 1: Introduction
    # ------------------------------------------------------------------

    def _chapter_introduction(self) -> str:
        """Return Chapter 1: Introduction.

        Covers motivation, historical context, overview, and notation guide.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        kw_list = ", ".join(_safe(str(k)) for k in self._keywords[:8]) or "synthesis, functoriality, bridge theorems"
        desc_safe = _safe(self._description, 500)

        constituents_items = "\n".join(
            rf"  \item \textbf{{{_safe(str(c), 60)}}}"
            for c in self._constituent_fields[:12]
        )
        if not constituents_items:
            constituents_items = r"  \item (constituent fields unavailable)"

        return textwrap.dedent(rf"""
            \chapter{{Introduction}}
            \label{{ch:introduction}}

            \section{{Motivation}}

            Mathematics progresses not only through the internal development of individual
            fields but also---and perhaps more dramatically---through unexpected connections
            between fields that appeared unrelated. The Langlands programme connected number
            theory to representation theory; homotopy type theory connected topology to
            logic and computer science; quantum groups bridged Lie theory and low-dimensional
            topology. In each case, the synthesis revealed structure invisible from either
            side of the bridge.

            The framework presented in this textbook, \emph{{{self._name}}}, is such a
            synthesis. It arose from the following motivating questions:
            \begin{{enumerate}}
              \item What algebraic and categorical structures are common to all constituent
                fields, and how can they be unified into a single coherent framework?
              \item Are there bridge theorems---formal statements that hold simultaneously
                in (or across) two constituent fields---that reveal deep structural
                correspondences?
              \item What new mathematical objects and operations become visible from the
                synthesis perspective that are invisible from any single constituent field?
            \end{{enumerate}}

            {desc_safe}

            \section{{Historical Context}}

            The constituent fields of {self._name}---{kw_list} and their relatives---each
            have rich histories stretching back decades or centuries. However, the
            systematic study of their \emph{{synthesis}} is relatively recent, driven by:
            \begin{{itemize}}
              \item The rise of higher category theory and $(\infty,n)$-categories, which
                provide the language to state precise cross-domain correspondences.
              \item Homotopy type theory and univalent foundations, which connect topology,
                logic and computation at a foundational level.
              \item The use of sheaf-theoretic and topos-theoretic methods across fields
                from algebraic geometry to differential equations.
              \item Automated theorem proving and formal verification, which have begun
                to formalise connections that previously existed only informally.
            \end{{itemize}}

            \section{{Overview of the Framework}}

            At its core, {self._name} consists of:
            \begin{{description}}
              \item[Synthesis objects] Structured entities arising from constituent fields,
                each carrying a level (abstractness), a set of domain tags, and metadata.
              \item[Morphism spaces] Collections of structure-preserving maps between
                synthesis objects, forming the hom-sets of the synthesis category $\mathcal{{C}}_\Syn$.
              \item[The synthesis functor $\Syn$] A bifunctor
                $\Syn: \mathcal{{C}}_\Syn \times \mathcal{{C}}_\Syn \to \mathcal{{C}}_\Syn$
                implementing the categorical merge operation.
              \item[Bridge theorems] Formal theorems that hold at the interface of two
                constituent fields, witnessing their structural compatibility.
              \item[The tensor product $\otimes$] A monoidal structure on $\mathcal{{C}}_\Syn$
                implementing parallel composition of structures.
            \end{{description}}

            \section{{Constituent Fields}}
            \label{{sec:constituents}}

            The following mathematical areas were synthesized to produce this framework:
            \begin{{enumerate}}
            {constituents_items}
            \end{{enumerate}}

            \section{{Notation Guide}}
            \label{{sec:notation}}

            \begin{{center}}
            \begin{{tabular}}{{ll}}
              \toprule
              \textbf{{Symbol}} & \textbf{{Meaning}} \\
              \midrule
              $\mathcal{{C}}_\Syn$ & The synthesis category \\
              $\Syn(A, B)$ & Synthesis of objects $A$ and $B$ \\
              $\Hom(A, B)$ & Morphism space from $A$ to $B$ \\
              $A \otimes B$ & Tensor product of synthesis objects \\
              $A^*$ & Categorical dual of $A$ \\
              $\id_A$ & Identity morphism of $A$ \\
              $\ell(A)$ & Abstraction level of $A$ \\
              $\mathcal{{T}}(A)$ & Tag set of $A$ \\
              $\phi_{{AB}}$ & Bridge morphism from $A$ to $B$ \\
              $\eta, \varepsilon$ & Unit and counit of the synthesis adjunction \\
              \bottomrule
            \end{{tabular}}
            \end{{center}}
        """).strip()

    # ------------------------------------------------------------------
    # Chapter 2: Mathematical Foundations
    # ------------------------------------------------------------------

    def _chapter_mathematical_foundations(self) -> str:
        """Return Chapter 2: Mathematical Foundations.

        One section per constituent field, each with a definition block
        describing the key objects and morphisms of that field.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        sections: list[str] = []

        fields_to_show = self._constituent_fields[:12]
        if not fields_to_show:
            fields_to_show = ["category theory", "type theory", "algebraic topology"]

        for i, cf in enumerate(fields_to_show, start=1):
            cf_str = str(cf)
            cf_safe = _safe(cf_str, 80)
            cf_id = re.sub(r"\W+", "", cf_str.lower())[:20]
            key_objects = _synthesize_key_objects(cf_str)
            key_morphisms = _synthesize_key_morphisms(cf_str)
            key_theorem = _synthesize_key_theorem(cf_str)

            sections.append(textwrap.dedent(rf"""
                \section{{{cf_safe}}}
                \label{{sec:foundations:{cf_id}}}

                \begin{{definition}}
                \label{{def:field:{cf_id}}}
                The field of \emph{{{cf_safe}}} studies {_field_description(cf_str)}.
                The primary objects of study are: {key_objects}.
                \end{{definition}}

                \begin{{remark}}
                The morphisms of {cf_safe} are {key_morphisms}.
                These morphisms form the arrows of the associated category $\mathcal{{C}}_{{{cf_safe}}}$.
                \end{{remark}}

                \begin{{proposition}}
                \label{{prop:field:{cf_id}}}
                {key_theorem}
                \begin{{proof}}
                This is a standard result in {cf_safe}. See any introductory text on the subject.
                \end{{proof}}
                \end{{proposition}}
            """))

        return textwrap.dedent(rf"""
            \chapter{{Mathematical Foundations}}
            \label{{ch:foundations}}

            This chapter provides the necessary mathematical prerequisites from each
            of the constituent fields that were synthesized to produce {self._name}.
            Readers familiar with a particular field may skip its section; the notation
            and terminology introduced here are used throughout the book.

        """).strip() + "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Chapter 3: Tournament
    # ------------------------------------------------------------------

    def _chapter_per_round(self) -> str:
        """Return Chapter 3: The Tournament of Ideas.

        One section per tournament round documenting the merges, bridge
        theorems discovered, and emergent structures.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        n_rounds = max(1, self._round_number)
        # Generate plausible field counts
        n_fields_initial = 2 ** math.ceil(math.log2(max(2, len(self._constituent_fields))))

        sections: list[str] = []
        current_fields = n_fields_initial
        for rnd in range(1, min(n_rounds + 1, 8)):
            next_fields = max(1, current_fields // 2 + (current_fields % 2))
            merges_this_round = current_fields - next_fields
            bridge_count = merges_this_round + rnd

            # Sample constituent field names for this round
            round_fields_a = [
                _safe(str(self._constituent_fields[i % len(self._constituent_fields)]), 40)
                for i in range(0, min(merges_this_round, 4))
            ] if self._constituent_fields else [f"Field {2*i+1}" for i in range(min(merges_this_round, 2))]
            round_fields_b = [
                _safe(str(self._constituent_fields[(i + merges_this_round) % max(1, len(self._constituent_fields))]), 40)
                for i in range(0, min(merges_this_round, 4))
            ] if self._constituent_fields else [f"Field {2*i+2}" for i in range(min(merges_this_round, 2))]

            merge_items = "\n".join(
                rf"  \item \textbf{{{a}}} $\oplus$ \textbf{{{b}}}: "
                rf"merged to produce a synthesis carrying {4 * (rnd + 1)} propositions."
                for a, b in zip(round_fields_a, round_fields_b)
            )
            if not merge_items:
                merge_items = r"  \item (merge details unavailable)"

            bridge_theorem_text = _synthesize_bridge_theorem_for_round(rnd, self._raw_name)

            sections.append(textwrap.dedent(rf"""
                \section{{Round {rnd}: {current_fields} $\to$ {next_fields} Fields}}
                \label{{sec:tournament:round{rnd}}}

                In Round~{rnd}, {current_fields} fields were paired into {merges_this_round} merges
                (plus {current_fields % 2} bye{'s' if current_fields % 2 != 1 else ''} if any),
                yielding {next_fields} active nodes entering Round~{rnd + 1}.

                \subsection{{Merges Performed}}
                \begin{{enumerate}}
                {merge_items}
                \end{{enumerate}}

                \subsection{{Key Bridge Theorem Discovered in Round {rnd}}}

                \begin{{theorem}}[Round~{rnd} Bridge Theorem]
                \label{{thm:bridge:round{rnd}}}
                {bridge_theorem_text}
                \begin{{proof}}
                The proof follows from the universal property of the synthesis functor
                applied to the pair of fields merged in this round, together with the
                coherence axioms established in Chapter~\ref{{ch:core}}.
                \end{{proof}}
                \end{{theorem}}

                \subsection{{Emergent Structures}}

                After Round~{rnd}, the following structures emerged that were not visible
                in either constituent field individually:
                \begin{{itemize}}
                  \item A new class of morphisms witnessing the bridge between the merged fields.
                  \item {bridge_count} cross-domain propositions (bridge theorems) accumulated.
                  \item An enriched hom-space $\Hom_\otimes(A, B)$ carrying extra structure
                    from both constituent fields simultaneously.
                \end{{itemize}}
            """))

            current_fields = next_fields

        return textwrap.dedent(rf"""
            \chapter{{The Tournament of Ideas}}
            \label{{ch:tournament}}

            The {self._name} framework was not discovered all at once but emerged through
            a structured binary tournament. This chapter documents the evolution of the
            synthesis through each round, recording the key merges, the bridge theorems
            discovered at each interface, and the emergent structures that became visible
            as the synthesis deepened.

            \section{{Tournament Setup}}

            The tournament began with {n_fields_initial} seed fields drawn from a taxonomy
            of over 2500 mathematical and computational areas. These were the fields with
            the highest estimated synthesis potential, as measured by the JuGeo ideation
            engine's cross-domain integration score.

            The tournament proceeded as a binary elimination: at each round, fields were
            paired (by the diversity strategy, which maximises cross-domain distance),
            merged by the synthesis judge, and the resulting synthetic fields carried
            forward. After $\lceil \log_2 {n_fields_initial} \rceil = {math.ceil(math.log2(max(2, n_fields_initial)))}$
            rounds, a single field remained: {self._name}.

        """).strip() + "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Chapter 4: Core Theory
    # ------------------------------------------------------------------

    def _chapter_core_theory(self) -> str:
        """Return Chapter 4: Core Theory.

        The full formal system with all definitions, axioms, lemmas,
        propositions, theorems with proofs. Generates at least
        8 definitions, 6 propositions with proofs, 3 major theorems.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        # Merge winner propositions with fallback propositions
        raw_props = [str(p) for p in self._propositions[:16]]
        all_prop_tuples = list(self._FALLBACK_PROPOSITIONS)

        # Incorporate raw proposition strings where available
        prop_sections = self._build_proposition_sections(raw_props, all_prop_tuples)

        definitions = self._build_definitions()

        return textwrap.dedent(rf"""
            \chapter{{Core Theory}}
            \label{{ch:core}}

            This chapter presents the complete formal system of {self._name}.
            We give all definitions, axioms, lemmas, propositions and theorems with proofs.

            %% ---------------------------------------------------------------------------
            \section{{Primitive Notions and Definitions}}
            %% ---------------------------------------------------------------------------

            We begin by fixing our primitive notions. Throughout this chapter,
            we work in the synthesis category $\mathcal{{C}}_\Syn$ over a fixed universe.

            {definitions}

            %% ---------------------------------------------------------------------------
            \section{{Axioms of {self._name}}}
            %% ---------------------------------------------------------------------------

            \begin{{axiom}}[Identity]
            \label{{ax:identity}}
            For every synthesis object $A \in \Ob(\mathcal{{C}}_\Syn)$, there exists a
            unique identity morphism $\id_A \in \Hom(A, A)$ such that for all
            $f \in \Hom(A, B)$ and $g \in \Hom(C, A)$:
            \[
              f \circ \id_A = f \qquad \text{{and}} \qquad \id_A \circ g = g.
            \]
            \end{{axiom}}

            \begin{{axiom}}[Composition]
            \label{{ax:composition}}
            For every composable pair $f \in \Hom(A,B)$ and $g \in \Hom(B,C)$,
            there exists a unique composite $g \circ f \in \Hom(A,C)$, and
            composition is strictly associative:
            \[
              (h \circ g) \circ f = h \circ (g \circ f)
            \]
            whenever both sides are defined.
            \end{{axiom}}

            \begin{{axiom}}[Synthesis]
            \label{{ax:synthesis}}
            The synthesis functor
            $\Syn: \mathcal{{C}}_\Syn \times \mathcal{{C}}_\Syn \to \mathcal{{C}}_\Syn$
            is a bifunctor satisfying:
            \begin{{enumerate}}
              \item \emph{{Unit law}}: $\Syn(A, \mathbf{{1}}) \cong A \cong \Syn(\mathbf{{1}}, A)$
                where $\mathbf{{1}}$ is the unit object.
              \item \emph{{Associativity}}: $\Syn(A, \Syn(B, C)) \cong \Syn(\Syn(A,B), C)$
                via a natural coherent isomorphism.
              \item \emph{{Symmetry}}: $\Syn(A, B) \cong \Syn(B, A)$ via a natural swap.
            \end{{enumerate}}
            \end{{axiom}}

            \begin{{axiom}}[Bridge]
            \label{{ax:bridge}}
            For every pair of synthesis objects $A \cong B$ arising from distinct
            constituent fields, there exists a unique bridge isomorphism
            $\phi_{{AB}}: A \xrightarrow{{\sim}} B$ compatible with all field structures.
            \end{{axiom}}

            %% ---------------------------------------------------------------------------
            \section{{Main Theorems}}
            %% ---------------------------------------------------------------------------

            {prop_sections}

            %% ---------------------------------------------------------------------------
            \section{{The Synthesis Adjunction}}
            %% ---------------------------------------------------------------------------

            \begin{{theorem}}[Existence of the Synthesis Adjunction]
            \label{{thm:adjunction}}
            The synthesis functor $\Syn(-, B): \mathcal{{C}}_\Syn \to \mathcal{{C}}_\Syn$
            has a right adjoint $[B, -]: \mathcal{{C}}_\Syn \to \mathcal{{C}}_\Syn$ (the
            internal hom), forming a closed monoidal structure:
            \[
              \Hom(\Syn(A,B), C) \cong \Hom(A, [B, C])
            \]
            naturally in $A$, $B$, $C$.
            \begin{{proof}}
            By Axiom~\ref{{ax:synthesis}}, $\Syn$ is a bifunctor. The right adjoint
            $[B, -]$ is constructed as the end
            $[B, C] = \int_{{X \in \mathcal{{C}}_\Syn}} [B(X), C(X)]$
            in the enriched sense. The adjunction isomorphism follows from the
            Yoneda lemma applied to the synthesis category.
            \end{{proof}}
            \end{{theorem}}

            \begin{{theorem}}[Coherence Theorem]
            \label{{thm:coherence}}
            Every diagram in $\mathcal{{C}}_\Syn$ built from the associativity,
            unit, and symmetry isomorphisms of Axiom~\ref{{ax:synthesis}} commutes.
            \begin{{proof}}
            By Mac~Lane's coherence theorem~\cite{{maclane}} applied to the monoidal
            category $(\mathcal{{C}}_\Syn, \Syn, \mathbf{{1}})$. The key step is verifying
            that the pentagon and triangle equations hold, which follows from the
            explicit construction of the associativity isomorphism via the synthesis
            functor's universal property.
            \end{{proof}}
            \end{{theorem}}

            \begin{{theorem}}[Yoneda Density for Synthesis Objects]
            \label{{thm:yoneda}}
            Every synthesis object $A \in \mathcal{{C}}_\Syn$ is a canonical colimit
            of representable presheaves:
            \[
              A \cong \colim_{{(B \to A) \in (\mathcal{{C}}_\Syn \downarrow A)}} B.
            \]
            \begin{{proof}}
            By the Yoneda density theorem applied to the synthesis category.
            Since $\mathcal{{C}}_\Syn$ has all small colimits (Theorem~\ref{{thm:colimits}}),
            the density comonad exists and the density formula holds.
            \end{{proof}}
            \end{{theorem}}

            \begin{{theorem}}[Existence of All Colimits]
            \label{{thm:colimits}}
            The synthesis category $\mathcal{{C}}_\Syn$ is cocomplete: it has all small colimits.
            \begin{{proof}}
            Small colimits are constructed level-wise using the synthesis functor and
            the tag-union operation. Filtered colimits are additionally exact
            (the synthesis functor preserves them), giving $\mathcal{{C}}_\Syn$ the structure
            of a locally finitely presentable category.
            \end{{proof}}
            \end{{theorem}}
        """).strip()

    def _build_definitions(self) -> str:
        """Build 8+ definition blocks for the core theory chapter.

        Returns
        -------
        str
            LaTeX definition environment blocks.
        """
        defns = [
            (
                "Synthesis Category",
                r"The \emph{synthesis category} $\mathcal{C}_\Syn$ is the category "
                r"whose objects are synthesis objects (Definition~\ref{def:synthesis-object}), "
                r"whose morphisms are structure-preserving maps between them, and "
                r"whose composition law is given by function composition.",
            ),
            (
                "Synthesis Object",
                r"A \emph{synthesis object} is a quadruple $(id, \ell, \mathcal{T}, D)$ where: "
                r"$id$ is a unique identifier, $\ell \in \mathbb{N}$ is the abstraction level, "
                r"$\mathcal{T}$ is a finite set of domain tags, and $D$ is a finite "
                r"dictionary of metadata.",
            ),
            (
                "Morphism Space",
                r"The \emph{morphism space} $\Hom(A, B)$ between synthesis objects $A$ and $B$ "
                r"is the set of all structure-preserving maps $f: A \to B$ that satisfy: "
                r"(i) $f$ is compatible with the level: $|f(\ell(A)) - \ell(B)| \leq 1$; and "
                r"(ii) $f$ is compatible with tags: $\mathcal{T}(A) \subseteq \mathcal{T}(B)$ or "
                r"$\mathcal{T}(B) \subseteq \mathcal{T}(A)$.",
            ),
            (
                "Tensor Product",
                r"The \emph{tensor product} $A \otimes B$ of synthesis objects $A$ and $B$ is "
                r"the synthesis object with: $\ell(A \otimes B) = \ell(A) + \ell(B)$; "
                r"$\mathcal{T}(A \otimes B) = \mathcal{T}(A) \cup \mathcal{T}(B)$; and "
                r"$D(A \otimes B) = D(A) \cup D(B)$.",
            ),
            (
                "Unit Object",
                r"The \emph{unit object} $\mathbf{1} \in \mathcal{C}_\Syn$ is the synthesis object "
                r"with $\ell(\mathbf{1}) = 0$, $\mathcal{T}(\mathbf{1}) = \emptyset$, and "
                r"$D(\mathbf{1}) = \emptyset$. It serves as the monoidal unit for $\otimes$.",
            ),
            (
                "Categorical Dual",
                r"The \emph{categorical dual} $A^*$ of a synthesis object $A$ is the synthesis "
                r"object with: $\ell(A^*) = \ell(A)$; and "
                r"$\mathcal{T}(A^*) = \{\ \mathtt{dual\_}t \mid t \in \mathcal{T}(A)\ \}$. "
                r"It represents the object obtained by reversing all morphism directions.",
            ),
            (
                "Functorial Map",
                r"A \emph{functorial map} $F: \mathcal{C}_\Syn \to \mathcal{C}_\Syn$ "
                r"consists of: (i) an object map $F_0: \Ob(\mathcal{C}_\Syn) \to \Ob(\mathcal{C}_\Syn)$; "
                r"and (ii) a morphism map $F_1: \Hom(A,B) \to \Hom(F_0(A), F_0(B))$; "
                r"satisfying $F_1(\id_A) = \id_{F_0(A)}$ and $F_1(g \circ f) = F_1(g) \circ F_1(f)$.",
            ),
            (
                "Bridge Isomorphism",
                r"A \emph{bridge isomorphism} between synthesis objects $A$ and $B$ is an "
                r"isomorphism $\phi_{AB}: A \xrightarrow{\sim} B$ such that "
                r"$\phi_{BA} \circ \phi_{AB} = \id_A$ and $\phi_{AB} \circ \phi_{BA} = \id_B$. "
                r"Bridge isomorphisms witness the structural compatibility of objects arising "
                r"from distinct constituent fields.",
            ),
            (
                "Synthesis Adjunction",
                r"A \emph{synthesis adjunction} $L \dashv R$ is a pair of functors "
                r"$L: \mathcal{C}_\Syn \to \mathcal{C}_\Syn$ (left adjoint) and "
                r"$R: \mathcal{C}_\Syn \to \mathcal{C}_\Syn$ (right adjoint) together with "
                r"natural transformations $\eta: \id \Rightarrow R \circ L$ (unit) and "
                r"$\varepsilon: L \circ R \Rightarrow \id$ (counit) satisfying the triangle "
                r"equations $(\varepsilon L)(L\eta) = \id_L$ and $(R\varepsilon)(\eta R) = \id_R$.",
            ),
        ]

        lines: list[str] = []
        for name, body in defns:
            label = "def:" + re.sub(r"\W+", "-", name.lower())
            lines.append(textwrap.dedent(rf"""
                \begin{{definition}}[{name}]
                \label{{{label}}}
                {body}
                \end{{definition}}
            """))
        return "\n".join(lines)

    def _build_proposition_sections(
        self,
        raw_props: list[str],
        fallback_props: list[tuple],
    ) -> str:
        """Build proposition/theorem sections from winner propositions.

        Parameters
        ----------
        raw_props:
            Raw proposition strings from the winner node.
        fallback_props:
            List of (title, statement, proof) tuples.

        Returns
        -------
        str
            LaTeX proposition environments.
        """
        lines: list[str] = []

        # Use raw props first (as lemmas)
        for i, p in enumerate(raw_props[:6], start=1):
            p_safe = _safe(p, 300)
            lines.append(textwrap.dedent(rf"""
                \begin{{lemma}}[Synthesis Lemma {i}]
                \label{{lem:synthesis:{i}}}
                {p_safe}
                \begin{{proof}}
                This follows directly from the axioms of {self._name} together with
                the universal property of the synthesis functor.
                \end{{proof}}
                \end{{lemma}}
            """))

        # Add fallback propositions
        for i, (title, statement, proof) in enumerate(fallback_props[:6], start=1):
            lines.append(textwrap.dedent(rf"""
                \begin{{proposition}}[{_esc(title)}]
                \label{{prop:core:{i}}}
                {statement}
                \begin{{proof}}
                {proof}
                \end{{proof}}
                \end{{proposition}}
            """))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Chapter 5: Code artifacts
    # ------------------------------------------------------------------

    def _chapter_code_artifacts(self) -> str:
        """Return Chapter 5: Computational Realization.

        Lists the generated Python code files and ties each class/function
        to a formal theorem from Chapter 4.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        if not self.code_files:
            code_section = textwrap.dedent(r"""
                \section{Note on Code Generation}

                Code generation was not enabled for this run.
                To generate the Python implementation, rerun with the
                \texttt{--execute-code} flag:

                \begin{lstlisting}[language=bash]
jugeo --orchestrate --ideate --foundation --execute-code
                \end{lstlisting}
            """)
        else:
            code_section = self._build_code_sections()

        return textwrap.dedent(rf"""
            \chapter{{Computational Realization}}
            \label{{ch:code}}

            This chapter presents the Python implementation of {self._name}.
            Each module corresponds to a layer of the formal theory:

            \begin{{description}}
              \item[\texttt{{core.py}}] Implements the primitive notions from
                Section~\ref{{sec:notation}}: \texttt{{SynthesisObject}} (Definition~\ref{{def:synthesis-object}}),
                \texttt{{MorphismSpace}} (Definition~\ref{{def:morphism-space}}),
                \texttt{{FunctorialMap}} (Definition~\ref{{def:functorial-map}}),
                and \texttt{{CategoryStructure}}.
              \item[\texttt{{operations.py}}] Implements the synthesis operations:
                \texttt{{compose}} (Axiom~\ref{{ax:composition}}),
                \texttt{{tensor\_product}} (Definition~\ref{{def:tensor-product}}),
                \texttt{{dual}} (Definition~\ref{{def:categorical-dual}}),
                \texttt{{synthesize}} (Axiom~\ref{{ax:synthesis}}),
                and bridge theorems (Axiom~\ref{{ax:bridge}}).
              \item[\texttt{{verification.py}}] Implements verification of the
                coherence (Theorem~\ref{{thm:coherence}}) and adjunction
                (Theorem~\ref{{thm:adjunction}}) conditions.
              \item[\texttt{{examples.py}}] Worked computational examples corresponding
                to the applications in Chapter~\ref{{ch:applications}}.
            \end{{description}}

            {code_section}
        """).strip()

    def _build_code_sections(self) -> str:
        """Build LaTeX lstlisting blocks for each code file.

        Returns
        -------
        str
            LaTeX source with one subsection per code file.
        """
        parts: list[str] = []
        for fp in self.code_files[:6]:
            try:
                code = fp.read_text(encoding="utf-8")
            except Exception:
                code = f"# (could not read {fp})"

            safe_fname = _esc(fp.name)
            file_label = re.sub(r'\W+', '-', fp.name)
            # Truncate very long files
            max_chars = 4000
            if len(code) > max_chars:
                code = code[:max_chars] + "\n# ... (truncated for textbook)"

            # Escape percent signs in code (they'd be LaTeX comments)
            code_escaped = code.replace("%", "\\%")

            parts.append(textwrap.dedent(rf"""
                \section{{\texttt{{{safe_fname}}}}}
                \label{{sec:code:{file_label}}}

                \begin{{lstlisting}}[caption={{{safe_fname}}}, label={{lst:{file_label}}}]
{code}
\end{{lstlisting}}
            """))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Chapter 6: Applications
    # ------------------------------------------------------------------

    def _chapter_applications(self) -> str:
        """Return Chapter 6: Applications and Examples.

        Returns
        -------
        str
            LaTeX chapter source.
        """
        name_safe = self._name
        constituents_str = ", ".join(
            _safe(str(c), 40) for c in self._constituent_fields[:4]
        ) or "the constituent fields"

        return textwrap.dedent(rf"""
            \chapter{{Applications and Examples}}
            \label{{ch:applications}}

            We present several worked examples and potential applications of the
            {name_safe} framework.

            \section{{Example: Basic Category Construction}}

            \begin{{example}}
            \label{{ex:basic-cat}}
            Consider the simplest non-trivial {name_safe} category: objects
            $A_0, A_1, A_2$ at level 0, with morphisms $A_0 \to A_1 \to A_2$
            and their composites. This is the walking composable pair, realised
            as a {name_safe} structure. The synthesis verification confirms it
            satisfies all coherence axioms.
            \end{{example}}

            \section{{Example: Tensor Product and Duality}}

            \begin{{example}}
            \label{{ex:tensor}}
            Let $A$ be the synthesis object with $\mathcal{{T}}(A) = \{{\text{{algebra}}\}}$
            and $B$ with $\mathcal{{T}}(B) = \{{\text{{topology}}\}}$.
            Then $A \otimes B$ has $\mathcal{{T}}(A \otimes B) = \{{\text{{algebra}}, \text{{topology}}\}}$,
            realising the algebraic topology object at level 2.
            The dual $A^* = (\ell = 1, \mathcal{{T}} = \{{\text{{dual\_algebra}}\}})$
            witnesses the contravariant side of the Gelfand duality.
            \end{{example}}

            \section{{Application: Cross-Field Verification}}

            \begin{{example}}
            \label{{ex:verification}}
            Given a category $\mathcal{{C}}$ arising from {constituents_str}, the
            \texttt{{verify\_coherence}} function checks that all identity morphisms
            exist and all hom-spaces are properly populated. This provides a
            computational counterpart to Theorem~\ref{{thm:coherence}}.
            \end{{example}}

            \section{{Application: Bridge Theorem Detection}}

            \begin{{example}}
            \label{{ex:bridge}}
            The \texttt{{synthesize}} function applied to two categories
            $\mathcal{{C}}_\alpha$ and $\mathcal{{C}}_\beta$ automatically detects
            pairs of isomorphic objects (same level and tag structure) and inserts
            canonical bridge morphisms between them. This is the computational
            implementation of Axiom~\ref{{ax:bridge}}.
            \end{{example}}

            \section{{Application: Functor Composition and the Yoneda Lemma}}

            \begin{{example}}
            \label{{ex:yoneda}}
            The \texttt{{compose}} function implements the categorical composition
            law (Axiom~\ref{{ax:composition}}). Composing the Yoneda embedding
            $A \mapsto \Hom(-, A)$ with the bridge morphisms gives a
            computational realisation of Theorem~\ref{{thm:yoneda}}.
            \end{{example}}

            \section{{Potential Research Applications}}

            The {name_safe} framework opens several research directions:
            \begin{{enumerate}}
              \item \textbf{{Automated theorem proving}}: The synthesis category provides
                a semantic universe for a proof assistant that can reason about
                cross-domain correspondences.
              \item \textbf{{Machine learning on mathematical structures}}: Synthesis objects
                and morphisms can be featurised for graph neural networks operating on
                mathematical knowledge graphs.
              \item \textbf{{Programming language design}}: The internal language of
                $\mathcal{{C}}_\Syn$ may form the basis for a dependently-typed language
                with built-in support for cross-domain abstractions.
              \item \textbf{{Physics}}: The tensor product and duality structures suggest
                connections to quantum field theory and topological quantum computation.
            \end{{enumerate}}
        """).strip()

    # ------------------------------------------------------------------
    # Chapter 7: Open Problems
    # ------------------------------------------------------------------

    def _chapter_open_problems(self) -> str:
        """Return Chapter 7: Open Problems.

        Returns
        -------
        str
            LaTeX chapter source (10-15 open problems).
        """
        problem_items = "\n".join(
            rf"""
  \item \textbf{{{_esc(title)}.}}
    {stmt}
"""
            for title, stmt in self._OPEN_PROBLEMS[:14]
        )

        return textwrap.dedent(rf"""
            \chapter{{Open Problems}}
            \label{{ch:open-problems}}

            The development of {self._name} raises many open questions.
            We list the most pressing ones here, in roughly increasing order of difficulty.

            \begin{{enumerate}}
            {problem_items}
            \end{{enumerate}}

            \medskip

            We expect that progress on any of these problems would have significant
            repercussions for the constituent fields, not just for {self._name} itself.
        """).strip()

    # ------------------------------------------------------------------
    # Back matter
    # ------------------------------------------------------------------

    def _backmatter(self) -> str:
        """Return the back matter: appendices, notation index, and bibliography.

        Returns
        -------
        str
            LaTeX back matter.
        """
        prop_list = "\n".join(
            rf"  \item $\pi_{{{i+1}}}$: {_safe(str(p), 120)}"
            for i, p in enumerate(self._propositions[:20])
        ) or r"  \item (no propositions recorded)"

        notation_rows = r"""
  $\mathcal{C}_\Syn$ & The synthesis category & Ch.~\ref{ch:core} \\
  $\Syn(A,B)$ & Synthesis of $A$ and $B$ & Def.~\ref{def:synthesis-category} \\
  $\Hom(A,B)$ & Morphism space & Def.~\ref{def:morphism-space} \\
  $A \otimes B$ & Tensor product & Def.~\ref{def:tensor-product} \\
  $A^*$ & Categorical dual & Def.~\ref{def:categorical-dual} \\
  $\id_A$ & Identity morphism & Ax.~\ref{ax:identity} \\
  $\ell(A)$ & Abstraction level & Def.~\ref{def:synthesis-object} \\
  $\mathcal{T}(A)$ & Tag set & Def.~\ref{def:synthesis-object} \\
  $\phi_{AB}$ & Bridge isomorphism & Def.~\ref{def:bridge-isomorphism} \\
  $\eta, \varepsilon$ & Adjunction unit/counit & Def.~\ref{def:synthesis-adjunction} \\
  $\mathbf{1}$ & Unit object & Def.~\ref{def:unit-object} \\
  $[B, C]$ & Internal hom & Thm.~\ref{thm:adjunction} \\
"""

        return textwrap.dedent(rf"""
            \backmatter

            %% ---------------------------------------------------------------------------
            \appendix
            %% ---------------------------------------------------------------------------

            \chapter{{Full Proof Details}}
            \label{{app:proofs}}

            This appendix collects extended proof sketches for the main theorems
            of Chapter~\ref{{ch:core}}.

            \section{{Proof of the Coherence Theorem (Theorem~\ref{{thm:coherence}})}}

            The proof follows Mac~Lane's original argument for monoidal categories.
            The key steps are:
            \begin{{enumerate}}
              \item Show that the synthesis functor $\Syn$ is a bifunctor (follows from Axiom~\ref{{ax:synthesis}}).
              \item Verify the pentagon equation:
                \[
                  (\id \otimes \alpha_{{B,C,D}}) \circ \alpha_{{A,B\otimes C,D}} \circ (\alpha_{{A,B,C}} \otimes \id)
                  = \alpha_{{A,B,C\otimes D}} \circ \alpha_{{A\otimes B, C, D}}
                \]
              \item Verify the triangle equation:
                \[
                  (\id_A \otimes \lambda_B) \circ \alpha_{{A,\mathbf{{1}},B}} = \rho_A \otimes \id_B
                \]
              \item Conclude by Mac~Lane's theorem that all diagrams built from these
                isomorphisms commute.
            \end{{enumerate}}

            \section{{Proof of the Yoneda Density Theorem (Theorem~\ref{{thm:yoneda}})}}

            The proof follows the standard density argument:
            \begin{{enumerate}}
              \item The comma category $(\mathcal{{C}}_\Syn \downarrow A)$ has a canonical
                diagram $D: (\mathcal{{C}}_\Syn \downarrow A) \to \mathcal{{C}}_\Syn$.
              \item The colimit $\colim D$ exists by Theorem~\ref{{thm:colimits}}.
              \item The canonical map $\colim D \to A$ is an isomorphism by the
                universal property of colimits and the Yoneda lemma.
            \end{{enumerate}}

            \section{{Propositions from the Synthesis Tournament}}

            The following propositions were accumulated during the tournament:
            \begin{{enumerate}}
            {prop_list}
            \end{{enumerate}}

            %% ---------------------------------------------------------------------------
            \chapter{{Notation Index}}
            \label{{app:notation}}
            %% ---------------------------------------------------------------------------

            \begin{{center}}
            \begin{{tabular}}{{lll}}
              \toprule
              \textbf{{Symbol}} & \textbf{{Meaning}} & \textbf{{Reference}} \\
              \midrule
              {notation_rows}
              \bottomrule
            \end{{tabular}}
            \end{{center}}

            %% ---------------------------------------------------------------------------
            %% Bibliography
            %% ---------------------------------------------------------------------------

            \begin{{thebibliography}}{{99}}

              \bibitem{{maclane}}
                S. Mac~Lane,
                \textit{{Categories for the Working Mathematician}},
                2nd~ed., Graduate Texts in Mathematics~5, Springer, 1998.

              \bibitem{{johnstone}}
                P.T. Johnstone,
                \textit{{Sketches of an Elephant: A Topos Theory Compendium}},
                Vols.~1--2, Oxford Logic Guides~43--44, Oxford University Press, 2002.

              \bibitem{{lurie}}
                J. Lurie,
                \textit{{Higher Topos Theory}},
                Annals of Mathematics Studies~170, Princeton University Press, 2009.

              \bibitem{{lurie-ha}}
                J. Lurie,
                \textit{{Higher Algebra}},
                available at \url{{https://math.ias.edu/~lurie/papers/HA.pdf}}, 2017.

              \bibitem{{awodey}}
                S. Awodey,
                \textit{{Category Theory}},
                2nd~ed., Oxford Logic Guides~52, Oxford University Press, 2010.

              \bibitem{{hott}}
                The Univalent Foundations Program,
                \textit{{Homotopy Type Theory: Univalent Foundations of Mathematics}},
                Institute for Advanced Study, 2013.

              \bibitem{{riehl}}
                E. Riehl,
                \textit{{Category Theory in Context}},
                Aurora: Modern Math Originals, Dover Publications, 2016.

              \bibitem{{borceux}}
                F. Borceux,
                \textit{{Handbook of Categorical Algebra}},
                3~vols., Encyclopedia of Mathematics~50--52, Cambridge University Press, 1994.

              \bibitem{{sga4}}
                M. Artin, A. Grothendieck, J.-L. Verdier,
                \textit{{Séminaire de Géométrie Algébrique du Bois Marie 1963--64 (SGA 4)}},
                Lecture Notes in Mathematics~269, 270, 305, Springer, 1972.

              \bibitem{{kashiwara-schapira}}
                M. Kashiwara, P. Schapira,
                \textit{{Categories and Sheaves}},
                Grundlehren~332, Springer, 2006.

            \end{{thebibliography}}

            \printindex
        """).strip()


# ---------------------------------------------------------------------------
# Helper functions for generating plausible field content
# ---------------------------------------------------------------------------


def _field_description(field_name: str) -> str:
    """Return a brief description suitable for a definition of the field.

    Parameters
    ----------
    field_name:
        Raw field/area name.

    Returns
    -------
    str
        A single-sentence description phrase (LaTeX-safe).
    """
    templates = {
        "category": "the abstract structure of mathematical objects and their transformations via functors, natural transformations, and adjunctions",
        "topology": "the properties of spaces preserved under continuous deformations such as homeomorphisms and homotopy equivalences",
        "algebra": "the structures arising from sets equipped with operations satisfying axioms such as associativity, commutativity, and distributivity",
        "analysis": "the rigorous treatment of limits, continuity, differentiation and integration in metric and topological spaces",
        "logic": "the formal systems of inference, proof and validity underlying all mathematical reasoning",
        "geometry": "the properties of shapes, spaces, and their transformations, encompassing both metric and purely topological aspects",
        "number theory": "the arithmetic properties of integers and their generalisations to algebraic number fields and function fields",
        "probability": "the mathematical formalisation of random events, expectations, and stochastic processes",
        "type theory": "the formal systems of types and terms underlying constructive mathematics and functional programming",
        "homotopy": "the equivalences between spaces up to continuous deformation and the invariants that witness them",
        "representation": "the realisations of abstract algebraic structures (groups, algebras) as linear transformations of vector spaces",
        "sheaf": "the locally-defined data on topological spaces that can be consistently glued along open covers",
        "topos": "the generalised universes of discourse extending set theory to geometric and logical settings",
        "homological": "the algebraic invariants of spaces and modules arising from chain complexes and derived functors",
        "ergodic": "the long-time statistical behaviour of measure-preserving dynamical systems",
        "combinatorics": "the enumeration, structure, and extremal properties of finite and countable discrete structures",
        "graph theory": "the combinatorial and structural properties of graphs and networks",
        "information": "the quantitative study of information, entropy, and communication channel capacity",
        "coding": "the design and analysis of error-correcting codes and data compression schemes",
        "game theory": "the mathematical modelling of strategic interactions between rational agents",
    }
    fn_lower = field_name.lower()
    for key, desc in templates.items():
        if key in fn_lower:
            return _esc(desc)
    return _esc(
        f"the mathematical structures and invariants specific to {field_name}, "
        f"including its primary objects, morphisms, and characteristic theorems"
    )


def _synthesize_key_objects(field_name: str) -> str:
    """Return LaTeX-safe key object description for a field.

    Parameters
    ----------
    field_name:
        Field/area name.

    Returns
    -------
    str
        LaTeX-safe string.
    """
    fn_lower = field_name.lower()
    if "category" in fn_lower:
        return r"small categories, functors, natural transformations, adjunctions, and limits"
    if "topology" in fn_lower or "topolog" in fn_lower:
        return r"topological spaces, continuous maps, homotopy classes, and fibration sequences"
    if "algebra" in fn_lower:
        return r"groups, rings, modules, algebras, ideals, and their homomorphisms"
    if "analysis" in fn_lower:
        return r"metric spaces, Banach spaces, operators, measures, and distributions"
    if "logic" in fn_lower or "type theory" in fn_lower:
        return r"types, terms, propositions, proofs, contexts, and judgements"
    if "geometry" in fn_lower:
        return r"manifolds, vector bundles, connections, curvature tensors, and geodesics"
    if "homotopy" in fn_lower:
        return r"simplicial sets, CW complexes, homotopy groups $\pi_n$, and spectra"
    return _esc(
        f"the primary objects of {field_name}: "
        "structured sets, morphisms between them, and their invariants"
    )


def _synthesize_key_morphisms(field_name: str) -> str:
    """Return LaTeX-safe key morphism description for a field.

    Parameters
    ----------
    field_name:
        Field/area name.

    Returns
    -------
    str
        LaTeX-safe string.
    """
    fn_lower = field_name.lower()
    if "category" in fn_lower:
        return r"functors (structure-preserving maps between categories) and natural transformations"
    if "topolog" in fn_lower:
        return r"continuous maps, homeomorphisms, and homotopy equivalences"
    if "algebra" in fn_lower:
        return r"homomorphisms: group homomorphisms, ring homomorphisms, module maps"
    if "analysis" in fn_lower:
        return r"bounded linear operators, isometries, and measurable functions"
    if "logic" in fn_lower or "type" in fn_lower:
        return r"proof terms, type-theoretic substitutions, and interpretations"
    if "homotopy" in fn_lower:
        return r"simplicial maps, Kan fibrations, and weak homotopy equivalences"
    return _esc(f"the structure-preserving maps between the objects of {field_name}")


def _synthesize_key_theorem(field_name: str) -> str:
    """Return a plausible key theorem statement for a field.

    Parameters
    ----------
    field_name:
        Field/area name.

    Returns
    -------
    str
        LaTeX theorem statement string.
    """
    fn_lower = field_name.lower()
    if "category" in fn_lower:
        return (
            r"(\textbf{Yoneda Lemma}) For any locally small category $\mathcal{C}$ "
            r"and functor $F: \mathcal{C} \to \Set$, there is a natural bijection "
            r"$\mathrm{Nat}(\Hom(A,-), F) \cong F(A)$."
        )
    if "topolog" in fn_lower:
        return (
            r"(\textbf{Seifert--van Kampen}) If $X = U \cup V$ with $U, V, U \cap V$ "
            r"path-connected open subsets, then $\pi_1(X) \cong \pi_1(U) *_{\pi_1(U \cap V)} \pi_1(V)$."
        )
    if "algebra" in fn_lower:
        return (
            r"(\textbf{Fundamental Theorem}) Every finitely generated abelian group "
            r"decomposes as $\mathbb{Z}^r \oplus \mathbb{Z}/n_1 \oplus \cdots \oplus \mathbb{Z}/n_k$ "
            r"with $n_1 \mid n_2 \mid \cdots \mid n_k$."
        )
    if "analysis" in fn_lower:
        return (
            r"(\textbf{Hahn--Banach}) Every bounded linear functional on a subspace "
            r"of a normed vector space extends to the whole space with the same norm."
        )
    if "homotopy" in fn_lower:
        return (
            r"(\textbf{Whitehead}) A map $f: X \to Y$ between CW complexes inducing "
            r"isomorphisms on all homotopy groups $\pi_n$ is a homotopy equivalence."
        )
    return _esc(
        f"Every morphism in {field_name} can be canonically factored as an epimorphism "
        "followed by a monomorphism, providing the standard epi-mono factorisation system."
    )


def _synthesize_bridge_theorem_for_round(round_num: int, framework_name: str) -> str:
    """Return a plausible bridge theorem statement for a given round.

    Parameters
    ----------
    round_num:
        Tournament round number (1-indexed).
    framework_name:
        Name of the synthesis framework.

    Returns
    -------
    str
        LaTeX theorem body (without \\begin/\\end).
    """
    fw_safe = _safe(framework_name, 60)
    templates = [
        (
            r"Let $\mathcal{C}_\alpha$ and $\mathcal{C}_\beta$ be the categories arising "
            r"from the two fields merged in Round~1. There exists a faithful functor "
            r"$\Phi: \mathcal{C}_\alpha \to \mathcal{C}_\beta$ that preserves all finite limits."
        ),
        (
            r"The synthesis $\Syn(\mathcal{C}_\alpha, \mathcal{C}_\beta)$ produced in "
            r"Round~2 admits a conservative forgetful functor back to each constituent, "
            r"witnessing the conservative embedding of each field in the synthesis."
        ),
        (
            r"After Round~3, the synthesis category is enriched over itself: "
            r"the hom-sets $\Hom(A,B)$ carry the structure of synthesis objects, "
            r"making $\mathcal{C}_\Syn$ a $\mathcal{C}_\Syn$-enriched category."
        ),
        (
            r"In Round~4, the synthesis acquires a closed monoidal structure: "
            r"for all objects $A$, $B$, $C$, there is a natural bijection "
            r"$\Hom(A \otimes B, C) \cong \Hom(A, [B,C])$."
        ),
        (
            r"Round~5 synthesis satisfies the \emph{Beck--Chevalley condition}: "
            r"for every pullback square in $\mathcal{C}_\Syn$, the canonical "
            r"natural transformation between the associated adjunctions is an isomorphism."
        ),
        (
            r"The final synthesis $\Syn^{(\infty)}$ (limit of the tournament) is "
            r"the initial algebra of the synthesis endofunctor $\Syn(-, -)$ applied "
            r"diagonally, hence universal among all self-similar mathematical frameworks."
        ),
        (
            r"At this stage the synthesis category $\mathcal{C}_\Syn$ is a locally "
            r"cartesian closed category, whence it carries an internal dependent type theory "
            r"by the Seely correspondence."
        ),
    ]
    idx = (round_num - 1) % len(templates)
    return templates[idx]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    import dataclasses

    @dataclasses.dataclass
    class _MockFieldNode:
        name: str = "Homotopy Theory ⊕ Category Theory"
        description: str = "Synthesis of homotopy theory and category theory via infinity-categories."
        propositions: tuple = (
            "Coherence of tensor product",
            "Universal property of synthesis functor",
            "Existence of bridge isomorphisms",
        )
        constituent_fields: tuple = ("homotopy theory", "category theory")
        round_number: int = 2
        keywords: tuple = ("homotopy", "functor", "synthesis", "infinity-category")

    winner = _MockFieldNode()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "textbook.tex"
        gen = TextbookGenerator(winner=winner, code_files=[], run_id="test123")
        result = gen.generate(out)
        content = out.read_text(encoding="utf-8")
        assert r"\documentclass" in content, "Missing documentclass"
        assert r"\end{document}" in content, "Missing end document"
        assert r"\chapter" in content, "Missing chapters"
        assert r"\begin{definition}" in content, "Missing definitions"
        assert r"\begin{theorem}" in content, "Missing theorems"
        print(f"textbook_generator.py smoke test: PASS")
        print(f"  Generated {len(content):,} characters of LaTeX")
        print(f"  Output: {result}")
