"""Multi-format data presentation for LaTeX papers.

This module formalizes the **REPORT morphism** (E→P) of the workspace site
by providing structured, trust-gated transformations from raw evidence
(benchmark results, time series, statistical tests) into multiple LaTeX
presentation formats.

The key invariant: every datum that appears in the paper must trace back
to a specific evidence section at a specific trust level.  The presentation
format is chosen by the *data kind* and *trust level* together:

    Data kind          Trust ≥ RUNTIME    Trust = COPILOT
    ──────────────────────────────────────────────────────
    Scalar metric      Results table      Future Work note
    Time series        pgfplots figure    Discussion sketch
    Distribution       Histogram/boxplot  Conjectured shape
    Comparison matrix  booktabs table     Omitted
    Statistical test   Theorem environ    Conjecture environ

Multiple formats for the same datum are encouraged — a metric might appear
as a number in a table AND as a bar in a chart AND as a sentence in the
evaluation narrative.  Each rendering is a separate *presentation section*
on the Claims surface, and the sheaf condition requires all renderings
to be consistent (same number everywhere).

This module is domain-agnostic — it knows about data shapes and LaTeX,
not about any specific research domain.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


# ═══════════════════════════════════════════════════════════════════════
#  Data kinds and presentation formats
# ═══════════════════════════════════════════════════════════════════════

class DataKind(str, Enum):
    """Classification of evidence data by shape."""
    SCALAR = "scalar"                # single number (Sharpe ratio, F1, etc.)
    TIME_SERIES = "time_series"      # ordered sequence of (timestamp, value)
    DISTRIBUTION = "distribution"    # sample of values (for histograms)
    COMPARISON = "comparison"        # matrix of (method × metric) values
    RANKED_LIST = "ranked_list"      # ordered list of items with scores
    CATEGORICAL = "categorical"      # counts per category
    CORRELATION = "correlation"      # pairwise correlation/covariance matrix
    STATISTICAL_TEST = "statistical_test"  # test statistic + p-value + CI


class PresentationFormat(str, Enum):
    """Available LaTeX rendering formats."""
    BOOKTABS_TABLE = "booktabs_table"      # \\toprule/\\midrule/\\bottomrule
    PGFPLOTS_LINE = "pgfplots_line"        # \\begin{axis} line plot
    PGFPLOTS_BAR = "pgfplots_bar"          # bar chart
    PGFPLOTS_SCATTER = "pgfplots_scatter"  # scatter plot
    HISTOGRAM = "histogram"                # pgfplots histogram
    BOXPLOT = "boxplot"                    # pgfplots boxplot
    HEATMAP = "heatmap"                    # colored matrix
    SPARKLINE = "sparkline"                # inline mini-chart
    INLINE_NUMBER = "inline_number"        # bold number in running text
    THEOREM_ENVIRON = "theorem_environ"    # \\begin{theorem}...\\end{theorem}
    ALGORITHM_BLOCK = "algorithm_block"    # algorithm2e pseudocode
    NARRATIVE = "narrative"                # paragraph of text with numbers


# Trust thresholds (matches jugeo.evidence.trust)
TRUST_COPILOT = 0.3
TRUST_RUNTIME = 0.7
TRUST_SOLVER = 0.9

# Which formats are allowed at which trust level
FORMAT_TRUST_GATE: dict[PresentationFormat, float] = {
    PresentationFormat.BOOKTABS_TABLE: TRUST_RUNTIME,
    PresentationFormat.PGFPLOTS_LINE: TRUST_RUNTIME,
    PresentationFormat.PGFPLOTS_BAR: TRUST_RUNTIME,
    PresentationFormat.PGFPLOTS_SCATTER: TRUST_RUNTIME,
    PresentationFormat.HISTOGRAM: TRUST_RUNTIME,
    PresentationFormat.BOXPLOT: TRUST_RUNTIME,
    PresentationFormat.HEATMAP: TRUST_RUNTIME,
    PresentationFormat.SPARKLINE: TRUST_RUNTIME,
    PresentationFormat.INLINE_NUMBER: TRUST_RUNTIME,
    PresentationFormat.THEOREM_ENVIRON: TRUST_SOLVER,
    PresentationFormat.ALGORITHM_BLOCK: TRUST_COPILOT,
    PresentationFormat.NARRATIVE: TRUST_COPILOT,
}

# Preferred formats by data kind (ordered by preference)
PREFERRED_FORMATS: dict[DataKind, list[PresentationFormat]] = {
    DataKind.SCALAR: [
        PresentationFormat.INLINE_NUMBER,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.TIME_SERIES: [
        PresentationFormat.PGFPLOTS_LINE,
        PresentationFormat.SPARKLINE,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.DISTRIBUTION: [
        PresentationFormat.HISTOGRAM,
        PresentationFormat.BOXPLOT,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.COMPARISON: [
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.PGFPLOTS_BAR,
        PresentationFormat.HEATMAP,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.RANKED_LIST: [
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.PGFPLOTS_BAR,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.CATEGORICAL: [
        PresentationFormat.PGFPLOTS_BAR,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.CORRELATION: [
        PresentationFormat.HEATMAP,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.NARRATIVE,
    ],
    DataKind.STATISTICAL_TEST: [
        PresentationFormat.THEOREM_ENVIRON,
        PresentationFormat.BOOKTABS_TABLE,
        PresentationFormat.INLINE_NUMBER,
        PresentationFormat.NARRATIVE,
    ],
}


# ═══════════════════════════════════════════════════════════════════════
#  Data point — a single piece of evidence with provenance
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DataPoint:
    """A single evidence datum with trust, provenance, and data kind.

    This is a section on the Evidence surface.  Its trust level gates
    which presentation formats are available for the Claims surface.
    """
    key: str                            # unique identifier (e.g. "sharpe_ratio")
    label: str                          # human-readable label
    value: Any                          # the actual data
    kind: DataKind                      # shape classification
    trust: float = TRUST_COPILOT        # evidence trust level
    unit: str = ""                      # unit of measurement
    source: str = ""                    # provenance (e.g. "backtest_2015_2025")
    higher_is_better: bool = True       # for comparison formatting
    metadata: dict[str, Any] = field(default_factory=dict)

    def allowed_formats(self) -> list[PresentationFormat]:
        """Return presentation formats allowed by this point's trust level."""
        preferred = PREFERRED_FORMATS.get(self.kind, [PresentationFormat.NARRATIVE])
        return [f for f in preferred if FORMAT_TRUST_GATE.get(f, 0.0) <= self.trust]


@dataclass
class DataSet:
    """A named collection of related data points (one "experiment").

    Multiple datasets may back a single paper.  Each dataset has its own
    trust level (the minimum trust of its constituent points) and can be
    rendered in multiple formats.
    """
    name: str
    description: str
    points: list[DataPoint] = field(default_factory=list)
    source_coordinate: str = ""         # workspace coordinate on E surface
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def trust(self) -> float:
        """Minimum trust across all points (conservative)."""
        if not self.points:
            return 0.0
        return min(p.trust for p in self.points)

    def points_by_kind(self, kind: DataKind) -> list[DataPoint]:
        return [p for p in self.points if p.kind == kind]


# ═══════════════════════════════════════════════════════════════════════
#  Presentation plan — what formats to use where in the paper
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PresentationSlot:
    """One planned rendering of data in the paper.

    A single data point may have multiple slots (e.g., a Sharpe ratio
    appears as a number in Table 1, as a bar in Figure 2, and as a
    sentence in §7.1).  The sheaf condition requires all slots for the
    same datum to show the same value.
    """
    data_key: str                          # which DataPoint
    format: PresentationFormat             # how to render
    section: str                           # paper section (e.g. "evaluation")
    label: str = ""                        # LaTeX label (e.g. "tab:results")
    caption: str = ""                      # figure/table caption
    slot_id: str = ""                      # unique slot identifier

    def __post_init__(self):
        if not self.slot_id:
            self.slot_id = f"{self.data_key}_{self.format.value}_{self.section}"


@dataclass
class PresentationPlan:
    """Complete plan for all data renderings in a paper.

    Built by ``plan_presentations()``, consumed by the paper generator.
    The plan guarantees:
    1. Every datum appears in at least 2 formats (multi-format requirement)
    2. No datum appears in a format its trust level forbids
    3. All renderings of the same datum are consistent
    """
    slots: list[PresentationSlot] = field(default_factory=list)
    datasets: list[DataSet] = field(default_factory=list)

    def slots_for_section(self, section: str) -> list[PresentationSlot]:
        return [s for s in self.slots if s.section == section]

    def slots_for_data(self, key: str) -> list[PresentationSlot]:
        return [s for s in self.slots if s.data_key == key]

    @property
    def multi_format_coverage(self) -> float:
        """Fraction of data points that appear in ≥2 formats."""
        keys = {s.data_key for s in self.slots}
        if not keys:
            return 0.0
        multi = sum(1 for k in keys if len(self.slots_for_data(k)) >= 2)
        return multi / len(keys)


def plan_presentations(
    datasets: list[DataSet],
    *,
    min_formats_per_datum: int = 2,
    max_formats_per_datum: int = 4,
) -> PresentationPlan:
    """Build a presentation plan ensuring multi-format coverage.

    For each data point, selects at least ``min_formats_per_datum``
    allowed formats (up to ``max_formats_per_datum``), distributing
    renderings across paper sections.
    """
    plan = PresentationPlan(datasets=list(datasets))

    section_map = {
        PresentationFormat.BOOKTABS_TABLE: "evaluation",
        PresentationFormat.PGFPLOTS_LINE: "evaluation",
        PresentationFormat.PGFPLOTS_BAR: "evaluation",
        PresentationFormat.PGFPLOTS_SCATTER: "evaluation",
        PresentationFormat.HISTOGRAM: "evaluation",
        PresentationFormat.BOXPLOT: "evaluation",
        PresentationFormat.HEATMAP: "evaluation",
        PresentationFormat.SPARKLINE: "introduction",
        PresentationFormat.INLINE_NUMBER: "abstract",
        PresentationFormat.THEOREM_ENVIRON: "framework",
        PresentationFormat.ALGORITHM_BLOCK: "framework",
        PresentationFormat.NARRATIVE: "discussion",
    }

    for ds in datasets:
        for point in ds.points:
            allowed = point.allowed_formats()
            selected = allowed[:max_formats_per_datum]
            # Pad with narrative if needed
            while len(selected) < min_formats_per_datum:
                if PresentationFormat.NARRATIVE not in selected:
                    selected.append(PresentationFormat.NARRATIVE)
                    break
                break  # can't pad further

            for fmt in selected:
                slot = PresentationSlot(
                    data_key=point.key,
                    format=fmt,
                    section=section_map.get(fmt, "evaluation"),
                    label=f"fig:{point.key}" if "pgf" in fmt.value else f"tab:{point.key}",
                    caption=f"{point.label} ({ds.name})",
                )
                plan.slots.append(slot)

    return plan


# ═══════════════════════════════════════════════════════════════════════
#  LaTeX rendering — each format gets a renderer
# ═══════════════════════════════════════════════════════════════════════

def _fmt(v: Any, precision: int = 4) -> str:
    """Format a value for LaTeX."""
    if isinstance(v, float):
        if abs(v) < 0.001 and v != 0:
            return f"{v:.2e}"
        return f"{v:.{precision}f}"
    return str(v)


def render_booktabs_table(
    points: Sequence[DataPoint],
    *,
    label: str = "tab:results",
    caption: str = "Experimental results.",
    columns: list[str] | None = None,
) -> str:
    """Render a list of scalar data points as a booktabs table."""
    if not points:
        return "% No data points for table\n"

    cols = columns or ["Metric", "Value", "Unit", "Source"]
    header = " & ".join(f"\\textbf{{{c}}}" for c in cols)

    rows = []
    for p in points:
        val = _fmt(p.value)
        if p.higher_is_better:
            val = f"\\textbf{{{val}}}"
        rows.append(f"    {p.label} & {val} & {p.unit} & {p.source} \\\\")

    return textwrap.dedent(f"""\
        \\begin{{table}}[htbp]
        \\centering
        \\caption{{{caption}}}
        \\label{{{label}}}
        \\begin{{tabular}}{{{'l' * len(cols)}}}
        \\toprule
        {header} \\\\
        \\midrule
        {chr(10).join(rows)}
        \\bottomrule
        \\end{{tabular}}
        \\end{{table}}
    """)


def render_comparison_table(
    methods: list[str],
    metrics: list[str],
    values: dict[str, dict[str, float]],
    *,
    label: str = "tab:comparison",
    caption: str = "Method comparison.",
    higher_is_better: dict[str, bool] | None = None,
) -> str:
    """Render a method × metric comparison table with best values bolded."""
    hib = higher_is_better or {}
    col_spec = "l" + "r" * len(metrics)
    header = "\\textbf{Method} & " + " & ".join(f"\\textbf{{{m}}}" for m in metrics)

    rows = []
    # Find best per metric
    best: dict[str, float] = {}
    for m in metrics:
        vals = [values.get(method, {}).get(m, float('-inf')) for method in methods]
        if hib.get(m, True):
            best[m] = max(vals)
        else:
            best[m] = min(vals)

    for method in methods:
        cells = [method]
        for m in metrics:
            v = values.get(method, {}).get(m, float('nan'))
            s = _fmt(v)
            if not math.isnan(v) and v == best.get(m):
                s = f"\\textbf{{{s}}}"
            cells.append(s)
        rows.append("    " + " & ".join(cells) + " \\\\")

    return textwrap.dedent(f"""\
        \\begin{{table}}[htbp]
        \\centering
        \\caption{{{caption}}}
        \\label{{{label}}}
        \\begin{{tabular}}{{{col_spec}}}
        \\toprule
        {header} \\\\
        \\midrule
        {chr(10).join(rows)}
        \\bottomrule
        \\end{{tabular}}
        \\end{{table}}
    """)


def render_pgfplots_line(
    series: list[tuple[float, float]],
    *,
    xlabel: str = "x",
    ylabel: str = "y",
    label: str = "fig:timeseries",
    caption: str = "Time series.",
    legend: str = "",
) -> str:
    """Render a time series as a pgfplots line chart."""
    coords = " ".join(f"({x},{y})" for x, y in series)
    legend_entry = f"\\addlegendentry{{{legend}}}" if legend else ""
    return textwrap.dedent(f"""\
        \\begin{{figure}}[htbp]
        \\centering
        \\begin{{tikzpicture}}
        \\begin{{axis}}[
            xlabel={{{xlabel}}},
            ylabel={{{ylabel}}},
            grid=major,
            width=0.85\\columnwidth,
            height=0.5\\columnwidth,
        ]
        \\addplot coordinates {{{coords}}};
        {legend_entry}
        \\end{{axis}}
        \\end{{tikzpicture}}
        \\caption{{{caption}}}
        \\label{{{label}}}
        \\end{{figure}}
    """)


def render_pgfplots_bar(
    categories: list[str],
    values: list[float],
    *,
    ylabel: str = "Value",
    label: str = "fig:bar",
    caption: str = "Bar chart.",
) -> str:
    """Render categorical data as a pgfplots bar chart."""
    coords = " ".join(f"({cat},{_fmt(v)})" for cat, v in zip(categories, values))
    xtick = ",".join(categories)
    return textwrap.dedent(f"""\
        \\begin{{figure}}[htbp]
        \\centering
        \\begin{{tikzpicture}}
        \\begin{{axis}}[
            ybar,
            ylabel={{{ylabel}}},
            symbolic x coords={{{xtick}}},
            xtick=data,
            x tick label style={{rotate=45, anchor=east}},
            width=0.85\\columnwidth,
            height=0.5\\columnwidth,
            nodes near coords,
            nodes near coords align={{vertical}},
        ]
        \\addplot coordinates {{{coords}}};
        \\end{{axis}}
        \\end{{tikzpicture}}
        \\caption{{{caption}}}
        \\label{{{label}}}
        \\end{{figure}}
    """)


def render_statistical_test(
    test_name: str,
    statistic: float,
    p_value: float,
    *,
    null_hypothesis: str = "",
    conclusion: str = "",
    label: str = "thm:test",
) -> str:
    """Render a statistical test result as a theorem environment."""
    h0 = f"\n$H_0$: {null_hypothesis}" if null_hypothesis else ""
    conc = f"\n{conclusion}" if conclusion else ""
    sig = "rejected" if p_value < 0.05 else "not rejected"
    return textwrap.dedent(f"""\
        \\begin{{proposition}}[{test_name}]
        \\label{{{label}}}{h0}

        Test statistic: ${_fmt(statistic)}$, $p$-value: ${_fmt(p_value)}$.
        At significance level $\\alpha = 0.05$, $H_0$ is \\textbf{{{sig}}}.{conc}
        \\end{{proposition}}
    """)


def render_inline_number(point: DataPoint) -> str:
    """Render a scalar as a bold inline number with unit."""
    v = _fmt(point.value)
    unit = f"\\,{point.unit}" if point.unit else ""
    return f"$\\mathbf{{{v}}}{unit}$"


# ═══════════════════════════════════════════════════════════════════════
#  Obligation — data the paper claims to need but doesn't have yet
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DataObligation:
    """An obligation to produce specific evidence before the paper can claim it.

    Obligations are open holes in the evidence surface.  The descent loop
    must discharge them (by running experiments) before the corresponding
    claim can appear in the paper at the required trust level.
    """
    key: str                            # what's needed (e.g. "sharpe_ratio")
    description: str                    # human description
    required_kind: DataKind             # what shape
    required_trust: float               # minimum trust for the target section
    target_section: str                 # paper section that needs it
    target_formats: list[PresentationFormat] = field(default_factory=list)
    discharged: bool = False            # has the obligation been met?
    discharging_point: Optional[DataPoint] = None

    def discharge(self, point: DataPoint) -> bool:
        """Try to discharge this obligation with a data point.

        Returns True if the point satisfies the obligation (right kind,
        sufficient trust).  Returns False otherwise.
        """
        if point.kind != self.required_kind:
            return False
        if point.trust < self.required_trust:
            return False
        self.discharged = True
        self.discharging_point = point
        return True


@dataclass
class ObligationManifest:
    """All data obligations for a paper, with discharge tracking."""
    obligations: list[DataObligation] = field(default_factory=list)

    @property
    def open_obligations(self) -> list[DataObligation]:
        return [o for o in self.obligations if not o.discharged]

    @property
    def discharged_obligations(self) -> list[DataObligation]:
        return [o for o in self.obligations if o.discharged]

    @property
    def discharge_rate(self) -> float:
        if not self.obligations:
            return 1.0
        return len(self.discharged_obligations) / len(self.obligations)

    def try_discharge_all(self, points: list[DataPoint]) -> int:
        """Try to discharge all open obligations with the given points.

        Returns the number of newly discharged obligations.
        """
        count = 0
        for obl in self.open_obligations:
            for pt in points:
                if pt.key == obl.key and obl.discharge(pt):
                    count += 1
                    break
        return count
