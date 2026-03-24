"""Stage 3B – Analogy Transport idea generation.

Generates web-application ideas by transporting tools, workflows, and
mechanics from non-web domains (desktop software, CLI tools, physical
workflows, spreadsheets, scientific instruments, board games, paper forms)
into the Flask/web-app design space.

Standalone module — Python stdlib only.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .models import (
    ApplicationCoordinate, AppIdeationPurpose, ExistingApp,
    GainProfile, IdeaPortfolio, IdeaProposal, IdeaSource,
)

AC = ApplicationCoordinate

SOURCE_DOMAINS: List[str] = [
    "desktop_software", "cli_tools", "physical_workflows",
    "spreadsheet_models", "scientific_instruments", "board_games",
    "paper_forms",
]

# Keyword associations: domain → {source_concept: web_concept}
_DOMAIN_WEB_CONCEPTS: Dict[str, Dict[str, str]] = {
    "desktop_software": {
        "local file": "uploaded asset / cloud storage",
        "menu bar": "navbar / sidebar",
        "plugin": "Flask extension / blueprint",
        "undo stack": "version history endpoint",
        "GPU rendering": "server-side rendering / WebGL",
    },
    "cli_tools": {
        "stdin/stdout": "request/response body",
        "pipe": "chained API endpoints",
        "flag": "query parameter",
        "man page": "OpenAPI docs",
        "script": "saved preset / cron job",
    },
    "physical_workflows": {
        "sticky note": "card component",
        "whiteboard": "collaborative canvas",
        "filing cabinet": "database table",
        "binder": "PDF export bundle",
        "rubber stamp": "automated approval step",
    },
    "spreadsheet_models": {
        "spreadsheet cell": "database row field",
        "formula": "computed column / server-side calc",
        "chart": "Chart.js / D3 visualization",
        "pivot table": "aggregation query + dynamic table",
        "conditional formatting": "colour-coded status badges",
    },
    "scientific_instruments": {
        "sensor reading": "real-time data ingest",
        "calibration curve": "normalization pipeline",
        "oscilloscope trace": "time-series chart",
        "spectrum": "frequency-domain dashboard",
        "lab notebook entry": "audit-trail record",
    },
    "board_games": {
        "score track": "leaderboard widget",
        "turn order": "round-robin scheduler",
        "resource token": "virtual currency / quota",
        "game board": "interactive grid UI",
        "victory condition": "goal-completion badge",
    },
    "paper_forms": {
        "checkbox": "boolean toggle",
        "signature line": "e-signature widget",
        "carbon copy": "email notification",
        "filing date stamp": "server-generated timestamp",
        "section header": "multi-step form wizard",
    },
}

_FIDELITY_PENALTIES: Dict[str, float] = {
    "physical interaction": 0.25, "hardware requirement": 0.30,
    "os-level access": 0.20, "real-time latency": 0.15,
    "tactile feedback": 0.20, "analogue signal": 0.15,
}
_FIDELITY_BONUSES: Dict[str, float] = {
    "data manipulation": 0.15, "visualization": 0.15,
    "workflow": 0.10, "text processing": 0.10,
    "scheduling": 0.10, "form handling": 0.10, "collaboration": 0.10,
}

# ── Data classes ─────────────────────────────────────────────────

@dataclass
class DomainTool:
    """A tool / artefact from a non-web source domain."""
    name: str
    domain: str
    core_function: str
    description: str
    user_base_estimate: int
    coordinate_coverage: List[ApplicationCoordinate]
    penalty_tags: List[str] = field(default_factory=list)
    bonus_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "domain": self.domain,
            "core_function": self.core_function,
            "description": self.description,
            "user_base_estimate": self.user_base_estimate,
            "coordinate_coverage": [c.value for c in self.coordinate_coverage],
            "penalty_tags": list(self.penalty_tags),
            "bonus_tags": list(self.bonus_tags),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> DomainTool:
        return cls(
            name=d["name"], domain=d["domain"],
            core_function=d["core_function"],
            description=d["description"],
            user_base_estimate=d["user_base_estimate"],
            coordinate_coverage=[AC(v) for v in d["coordinate_coverage"]],
            penalty_tags=d.get("penalty_tags", []),
            bonus_tags=d.get("bonus_tags", []),
        )


@dataclass
class AnalogyMap:
    """Maps concepts from a source tool to their web-app equivalents."""
    source_tool: DomainTool
    target_description: str
    correspondences: Dict[str, str]
    faithfulness: float          # 0–1
    quality: str                 # "high" | "medium" | "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_tool": self.source_tool.to_dict(),
            "target_description": self.target_description,
            "correspondences": dict(self.correspondences),
            "faithfulness": self.faithfulness,
            "quality": self.quality,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> AnalogyMap:
        return cls(
            source_tool=DomainTool.from_dict(d["source_tool"]),
            target_description=d["target_description"],
            correspondences=d["correspondences"],
            faithfulness=d["faithfulness"],
            quality=d["quality"],
        )


# ── Main transporter ─────────────────────────────────────────────

class AppAnalogyTransporter:
    """Stage 3B: Generates Flask app ideas via analogy transport."""

    def generate_candidates(
        self, purpose: AppIdeationPurpose, portfolio: IdeaPortfolio,
    ) -> List[IdeaProposal]:
        """Generate ideas by transporting tools from other domains.

        For each source domain: get tools → filter already-covered →
        build analogy map → convert to IdeaProposal.
        Returns proposals sorted by analogy_fidelity descending.
        """
        proposals: List[IdeaProposal] = []
        for domain in SOURCE_DOMAINS:
            for tool in self._get_source_tools(domain, purpose):
                if self._has_web_equivalent(tool, portfolio):
                    continue
                analogy = self._build_analogy(tool, purpose)
                fidelity = self._assess_fidelity(analogy)
                analogy = AnalogyMap(
                    source_tool=analogy.source_tool,
                    target_description=analogy.target_description,
                    correspondences=analogy.correspondences,
                    faithfulness=fidelity, quality=analogy.quality,
                )
                proposals.append(self._analogy_to_idea(tool, analogy, purpose))
        proposals.sort(key=lambda p: p.analogy_fidelity, reverse=True)
        return proposals

    # ── internal helpers ──────────────────────────────────────────

    def _get_source_tools(
        self, domain: str, purpose: AppIdeationPurpose,
    ) -> List[DomainTool]:
        """Get tools from *domain*, sorted by relevance to *purpose*."""
        all_tools = BuiltinSourceTools.get_domain_tools(domain)
        domain_kw = _tokenize(purpose.domain)

        def _relevance(t: DomainTool) -> float:
            tkw = _tokenize(t.core_function) | _tokenize(t.description)
            if not domain_kw:
                return 0.5
            return len(domain_kw & tkw) / max(len(domain_kw), 1)

        all_tools.sort(key=_relevance, reverse=True)
        return all_tools

    def _has_web_equivalent(
        self, tool: DomainTool, portfolio: IdeaPortfolio,
    ) -> bool:
        """True when a web version of *tool* is already in *portfolio*.

        Checks name-keyword overlap (≥ 0.5) and coordinate-coverage
        overlap (> 0.7).
        """
        tkw = _tokenize(tool.name)
        tcoords = set(tool.coordinate_coverage)
        for app in portfolio.ideas:
            akw = _tokenize(app.name)
            if len(tkw & akw) / max(len(tkw), 1) >= 0.5:
                return True
            if tcoords and app.coordinates:
                if len(tcoords & app.coordinates) / len(tcoords) > 0.7:
                    return True
        return False

    def _build_analogy(
        self, tool: DomainTool, purpose: AppIdeationPurpose,
    ) -> AnalogyMap:
        """Construct analogy map from *tool* to a Flask web app.

        Correspondences dict maps source concepts to web equivalents.
        Faithfulness derived from web-friendly coordinate ratio plus
        penalty/bonus tag adjustments.
        Quality: "high" if > 0.7, "medium" if > 0.4, else "low".
        """
        domain_concepts = _DOMAIN_WEB_CONCEPTS.get(tool.domain, {})
        correspondences: Dict[str, str] = {}
        tool_text = f"{tool.core_function} {tool.description}".lower()
        for src, web in domain_concepts.items():
            if set(src.lower().split()) & set(tool_text.split()):
                correspondences[src] = web
        correspondences[tool.core_function] = (
            f"Flask endpoint providing {tool.core_function} as a service"
        )
        # faithfulness from web-friendly coordinate ratio
        web_friendly = {
            AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.DATA_VISUALIZATION,
            AC.DATA_EXPORT, AC.FORM_WORKFLOW, AC.AGGREGATION,
            AC.INTERACTIVE_DASHBOARD, AC.STATIC_REPORT, AC.NOTIFICATION,
            AC.API_PROVISION, AC.SCHEDULING,
        }
        tc = set(tool.coordinate_coverage)
        ratio = len(tc & web_friendly) / len(tc) if tc else 0.3
        faith = 0.3 + 0.5 * ratio
        for tag in tool.penalty_tags:
            faith -= _FIDELITY_PENALTIES.get(tag, 0.05)
        for tag in tool.bonus_tags:
            faith += _FIDELITY_BONUSES.get(tag, 0.05)
        faith = max(0.0, min(1.0, faith))
        quality = "high" if faith > 0.7 else ("medium" if faith > 0.4 else "low")
        desc = (
            f"A Flask web app replicating {tool.name} "
            f"({tool.domain.replace('_', ' ')}) — {tool.core_function} "
            f"— accessible through a browser."
        )
        return AnalogyMap(tool, desc, correspondences, faith, quality)

    def _assess_fidelity(self, analogy: AnalogyMap) -> float:
        """Re-assess faithfulness including correspondence richness.

        High fidelity: core function maps cleanly to web capabilities.
        Low fidelity: requires hardware, real-time OS, etc.
        Penalties for physical interaction, hardware, OS-level access.
        Bonuses for data manipulation, visualization, workflow.
        """
        base = analogy.faithfulness
        tool = analogy.source_tool
        for tag in tool.penalty_tags:
            base -= _FIDELITY_PENALTIES.get(tag, 0.05)
        for tag in tool.bonus_tags:
            base += _FIDELITY_BONUSES.get(tag, 0.05)
        base += min(len(analogy.correspondences) * 0.02, 0.10)
        return max(0.0, min(1.0, base))

    def _analogy_to_idea(
        self, tool: DomainTool, analogy: AnalogyMap,
        purpose: AppIdeationPurpose,
    ) -> IdeaProposal:
        """Convert an analogy map into a scored IdeaProposal.

        Title: "{Tool.name} for the Web"
        Hypothesis: explain what the web version would do.
        Feasibility from fidelity penalised by hard coordinates.
        Novelty inversely related to fidelity (novel = less explored).
        """
        label = tool.domain.replace("_", " ").title()
        title = f"{tool.name} for the Web"
        hypothesis = (
            f"Transporting {tool.name} from {label} to a Flask web app "
            f"would let users {tool.core_function.lower()} via browser, "
            f"eliminating the need for {label.lower()} infrastructure."
        )
        hard = {AC.REAL_TIME_FEEDBACK, AC.COLLABORATIVE_EDITING,
                AC.SIMULATION, AC.CONSTRAINT_SATISFACTION}
        tc = set(tool.coordinate_coverage)
        hf = len(tc & hard) / max(len(tc), 1)
        feasibility = max(0.05, min(0.99, analogy.faithfulness * (1 - 0.4 * hf)))
        novelty = max(0.1, min(0.99, 0.5 + 0.3 * (1 - analogy.faithfulness)))
        gain = GainProfile(
            theorem_yield=analogy.faithfulness * 0.6,
            bridge_impact=analogy.faithfulness * 0.8,
            cost=1.0 - feasibility,
            uncertainty=1.0 - analogy.faithfulness,
        )
        return IdeaProposal.create(
            title=title, hypothesis=hypothesis,
            target_area=purpose.domain, coordinates=tc, gain=gain,
            source=IdeaSource.ANALOGY_TRANSPORT,
            analogy_source=tool.name,
            analogy_fidelity=analogy.faithfulness,
            feasibility_score=feasibility, novelty_score=novelty,
        )


# ── Built-in source-tool catalogue ───────────────────────────────

def _dt(name: str, domain: str, func: str, desc: str, users: int,
        coords: List[AC], *, pen: Optional[List[str]] = None,
        bon: Optional[List[str]] = None) -> DomainTool:
    """Shorthand factory for DomainTool construction."""
    return DomainTool(name, domain, func, desc, users, coords,
                      pen or [], bon or [])


class BuiltinSourceTools:
    """Pre-built DomainTool catalogues for each source domain."""

    @staticmethod
    def get_domain_tools(domain: str) -> List[DomainTool]:
        dispatch: Dict[str, Any] = {
            "desktop_software": BuiltinSourceTools.desktop_software,
            "cli_tools": BuiltinSourceTools.cli_tools,
            "physical_workflows": BuiltinSourceTools.physical_workflows,
            "spreadsheet_models": BuiltinSourceTools.spreadsheet_models,
            "scientific_instruments": BuiltinSourceTools.scientific_instruments,
            "board_games": BuiltinSourceTools.board_games,
            "paper_forms": BuiltinSourceTools.paper_forms,
        }
        return dispatch.get(domain, lambda: [])()

    # -- 1. Desktop software -------------------------------------------

    @staticmethod
    def desktop_software() -> List[DomainTool]:
        D = "desktop_software"
        return [
            _dt("Photoshop", D, "raster image editing and compositing",
                "Bitmap editor with layers, filters, and colour management.",
                30_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.DATA_EXPORT,
                 AC.FILE_PROCESSING, AC.REAL_TIME_FEEDBACK],
                pen=["real-time latency"],
                bon=["data manipulation", "visualization"]),
            _dt("Excel", D, "tabular data analysis with formulas and charts",
                "Spreadsheet with formulas, pivot tables, macros, and charts.",
                800_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                 AC.DATA_VISUALIZATION, AC.DATA_EXPORT,
                 AC.COMPUTATION_ON_DEMAND, AC.AGGREGATION],
                bon=["data manipulation", "visualization"]),
            _dt("AutoCAD", D, "2D/3D computer-aided drafting",
                "Precision drafting for architecture and engineering.",
                4_000_000,
                [AC.DATA_INGESTION, AC.DATA_EXPORT,
                 AC.CONSTRAINT_SATISFACTION, AC.SIMULATION],
                pen=["hardware requirement", "real-time latency"],
                bon=["visualization"]),
            _dt("OBS Studio", D, "live video recording and streaming",
                "Screen recording and live streaming with scene composition.",
                10_000_000,
                [AC.DATA_INGESTION, AC.REAL_TIME_FEEDBACK,
                 AC.FILE_PROCESSING, AC.DATA_EXPORT],
                pen=["hardware requirement", "real-time latency"]),
            _dt("Audacity", D, "audio recording and waveform editing",
                "Multi-track audio editor with effects and format conversion.",
                15_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.DATA_EXPORT,
                 AC.FILE_PROCESSING, AC.REAL_TIME_FEEDBACK],
                pen=["real-time latency"],
                bon=["data manipulation"]),
            _dt("Outlook Calendar", D,
                "personal and group scheduling with reminders",
                "Calendar client with meeting invitations and recurring events.",
                400_000_000,
                [AC.SCHEDULING, AC.NOTIFICATION,
                 AC.COLLABORATIVE_EDITING, AC.DATA_EXPORT],
                bon=["scheduling", "workflow", "collaboration"]),
        ]

    # -- 2. CLI tools --------------------------------------------------

    @staticmethod
    def cli_tools() -> List[DomainTool]:
        D = "cli_tools"
        return [
            _dt("ffmpeg", D, "audio/video transcoding and stream processing",
                "Universal media converter with filter graphs for pipelines.",
                5_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.DATA_EXPORT,
                 AC.FILE_PROCESSING, AC.BATCH_PROCESSING],
                bon=["data manipulation"]),
            _dt("ImageMagick", D, "batch image conversion and manipulation",
                "CLI image processor supporting 200+ formats.",
                3_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.DATA_EXPORT,
                 AC.FILE_PROCESSING, AC.BATCH_PROCESSING],
                bon=["data manipulation"]),
            _dt("Pandoc", D, "universal document format conversion",
                "Converts between Markdown, LaTeX, HTML, DOCX, PDF, and more.",
                2_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                 AC.DATA_EXPORT, AC.FILE_PROCESSING],
                bon=["text processing", "data manipulation"]),
            _dt("jq", D, "JSON querying and transformation",
                "Lightweight JSON processor for filtering and reshaping data.",
                1_500_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                 AC.DATA_EXPORT, AC.COMPUTATION_ON_DEMAND],
                bon=["data manipulation", "text processing"]),
            _dt("curl", D, "HTTP request construction and execution",
                "Transfer data via URLs with auth, headers, and cookies.",
                10_000_000,
                [AC.DATA_INGESTION, AC.DATA_EXPORT, AC.API_PROVISION],
                bon=["workflow"]),
            _dt("rsync", D, "incremental file synchronisation",
                "Efficient remote file sync via delta encoding for backups.",
                4_000_000,
                [AC.DATA_INGESTION, AC.DATA_EXPORT,
                 AC.FILE_PROCESSING, AC.BATCH_PROCESSING],
                pen=["os-level access"], bon=["workflow"]),
            _dt("awk", D, "pattern-directed text scanning and processing",
                "Line-oriented language for column extraction and statistics.",
                2_000_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                 AC.COMPUTATION_ON_DEMAND, AC.AGGREGATION],
                bon=["text processing", "data manipulation"]),
        ]

    # -- 3. Physical workflows -----------------------------------------

    @staticmethod
    def physical_workflows() -> List[DomainTool]:
        D = "physical_workflows"
        return [
            _dt("Kanban Board", D, "visual task tracking with WIP limits",
                "Board with columns and sticky notes representing work items.",
                5_000_000,
                [AC.DATA_VISUALIZATION, AC.SCHEDULING,
                 AC.COLLABORATIVE_EDITING, AC.INVENTORY],
                bon=["workflow", "visualization", "collaboration"]),
            _dt("Filing Cabinet", D,
                "hierarchical document storage and retrieval",
                "Labelled folders for archiving paper documents by category.",
                50_000_000,
                [AC.DATA_INGESTION, AC.DATA_EXPORT,
                 AC.INVENTORY, AC.AUDIT_TRAIL],
                pen=["physical interaction"], bon=["workflow"]),
            _dt("Lab Notebook", D,
                "chronological experiment logging with attestation",
                "Bound notebook recording procedures, observations, results.",
                2_000_000,
                [AC.DATA_INGESTION, AC.AUDIT_TRAIL, AC.STATIC_REPORT],
                pen=["physical interaction"], bon=["workflow"]),
            _dt("Whiteboard Session", D,
                "collaborative real-time diagramming",
                "Erasable board for brainstorming and group problem-solving.",
                20_000_000,
                [AC.DATA_VISUALIZATION, AC.COLLABORATIVE_EDITING,
                 AC.REAL_TIME_FEEDBACK],
                pen=["physical interaction", "tactile feedback"],
                bon=["visualization", "collaboration"]),
            _dt("Mail Room Sorting", D,
                "incoming item classification and routing",
                "Manual sorting of mail into department pigeon-holes.",
                1_000_000,
                [AC.DATA_INGESTION, AC.MATCHING,
                 AC.NOTIFICATION, AC.BATCH_PROCESSING],
                pen=["physical interaction"], bon=["workflow"]),
            _dt("Inventory Clipboard Count", D,
                "physical stock counting and reconciliation",
                "Walking a warehouse counting stock against expected quantities.",
                3_000_000,
                [AC.INVENTORY, AC.COMPARISON,
                 AC.AGGREGATION, AC.AUDIT_TRAIL],
                pen=["physical interaction"], bon=["data manipulation"]),
        ]

    # -- 4. Spreadsheet models -----------------------------------------

    @staticmethod
    def spreadsheet_models() -> List[DomainTool]:
        D = "spreadsheet_models"
        return [
            _dt("Financial Forecast Model", D,
                "revenue/expense projection with scenarios",
                "Multi-tab workbook modelling cash-flow under best/worst/base.",
                10_000_000,
                [AC.COMPUTATION_ON_DEMAND, AC.DATA_VISUALIZATION,
                 AC.AGGREGATION, AC.SIMULATION, AC.STATIC_REPORT],
                bon=["data manipulation", "visualization"]),
            _dt("Grade Book", D,
                "student grade recording and weighted averaging",
                "Tracks assignments, exams, weights, and final grades.",
                5_000_000,
                [AC.DATA_INGESTION, AC.COMPUTATION_ON_DEMAND,
                 AC.AGGREGATION, AC.STATIC_REPORT, AC.DATA_EXPORT],
                bon=["data manipulation"]),
            _dt("Inventory Tracker Sheet", D,
                "stock level monitoring with reorder alerts",
                "Lists SKUs, quantities, reorder points, low-stock warnings.",
                8_000_000,
                [AC.INVENTORY, AC.DATA_INGESTION, AC.NOTIFICATION,
                 AC.COMPARISON, AC.DATA_EXPORT],
                bon=["data manipulation", "workflow"]),
            _dt("Project Timeline Gantt", D,
                "task dependency scheduling via bar chart",
                "Bar chart showing task durations, dependencies, milestones.",
                6_000_000,
                [AC.SCHEDULING, AC.DATA_VISUALIZATION,
                 AC.CONSTRAINT_SATISFACTION, AC.DATA_EXPORT],
                bon=["visualization", "scheduling"]),
            _dt("Survey Tally Sheet", D,
                "response aggregation and cross-tabulation",
                "Collects responses, computes frequencies, generates charts.",
                4_000_000,
                [AC.DATA_INGESTION, AC.AGGREGATION,
                 AC.DATA_VISUALIZATION, AC.STATIC_REPORT],
                bon=["data manipulation", "visualization"]),
            _dt("Budget Tracker", D,
                "income vs expense categorisation and tracking",
                "Monthly columns, category roll-ups, and variance analysis.",
                12_000_000,
                [AC.DATA_INGESTION, AC.COMPUTATION_ON_DEMAND,
                 AC.AGGREGATION, AC.DATA_VISUALIZATION, AC.STATIC_REPORT],
                bon=["data manipulation", "visualization"]),
        ]

    # -- 5. Scientific instruments -------------------------------------

    @staticmethod
    def scientific_instruments() -> List[DomainTool]:
        D = "scientific_instruments"
        return [
            _dt("Oscilloscope", D,
                "real-time voltage waveform display and measurement",
                "Displays signal amplitude over time with triggering/cursors.",
                2_000_000,
                [AC.DATA_INGESTION, AC.DATA_VISUALIZATION,
                 AC.REAL_TIME_FEEDBACK, AC.COMPUTATION_ON_DEMAND],
                pen=["hardware requirement", "analogue signal"],
                bon=["visualization"]),
            _dt("Spectrum Analyzer", D,
                "frequency-domain signal decomposition",
                "Signal power across frequency bands via FFT.",
                800_000,
                [AC.DATA_INGESTION, AC.DATA_TRANSFORMATION,
                 AC.DATA_VISUALIZATION, AC.COMPUTATION_ON_DEMAND],
                pen=["hardware requirement", "analogue signal"],
                bon=["visualization", "data manipulation"]),
            _dt("Chromatograph", D,
                "chemical mixture separation and identification",
                "Separates compounds; detector output is concentration-vs-time.",
                500_000,
                [AC.DATA_INGESTION, AC.DATA_VISUALIZATION,
                 AC.COMPARISON, AC.STATIC_REPORT],
                pen=["hardware requirement", "physical interaction"],
                bon=["visualization"]),
            _dt("Multimeter Logger", D,
                "multi-channel electrical measurement logging",
                "Records V/I/R readings over time with min/max/average stats.",
                3_000_000,
                [AC.DATA_INGESTION, AC.AGGREGATION,
                 AC.DATA_EXPORT, AC.STATIC_REPORT],
                pen=["hardware requirement"],
                bon=["data manipulation"]),
            _dt("pH Meter Dashboard", D,
                "continuous pH monitoring with threshold alerts",
                "Real-time pH readout with calibration and high/low alarms.",
                1_000_000,
                [AC.DATA_INGESTION, AC.REAL_TIME_FEEDBACK,
                 AC.NOTIFICATION, AC.AUDIT_TRAIL],
                pen=["hardware requirement"], bon=["workflow"]),
            _dt("Thermal Imager", D,
                "infrared heat-map capture and analysis",
                "False-colour thermal image with spot temp and isotherms.",
                600_000,
                [AC.DATA_INGESTION, AC.DATA_VISUALIZATION,
                 AC.COMPARISON, AC.DATA_EXPORT],
                pen=["hardware requirement", "analogue signal"],
                bon=["visualization"]),
        ]

    # -- 6. Board games ------------------------------------------------

    @staticmethod
    def board_games() -> List[DomainTool]:
        D = "board_games"
        return [
            _dt("Scoring System", D,
                "multi-category point tracking with bonuses",
                "Tracks scores across categories with end-game bonus calc.",
                20_000_000,
                [AC.AGGREGATION, AC.COMPUTATION_ON_DEMAND,
                 AC.DATA_VISUALIZATION, AC.INTERACTIVE_DASHBOARD],
                bon=["data manipulation", "visualization"]),
            _dt("Tournament Bracket", D,
                "elimination bracket generation and progression",
                "Seeding, match results, and automatic round advancement.",
                10_000_000,
                [AC.MATCHING, AC.SCHEDULING,
                 AC.DATA_VISUALIZATION, AC.STATIC_REPORT],
                bon=["workflow", "visualization", "scheduling"]),
            _dt("Strategy Tracker", D,
                "move-by-move decision log with outcome analysis",
                "Records decisions per turn, links to outcomes, shows win-rate.",
                2_000_000,
                [AC.DATA_INGESTION, AC.AUDIT_TRAIL,
                 AC.AGGREGATION, AC.DATA_VISUALIZATION],
                bon=["data manipulation", "visualization"]),
            _dt("Resource Market", D,
                "supply-demand pricing with trade mechanics",
                "Dynamic marketplace with shifting prices and trade tracking.",
                5_000_000,
                [AC.INVENTORY, AC.COMPUTATION_ON_DEMAND,
                 AC.SIMULATION, AC.INTERACTIVE_DASHBOARD],
                bon=["data manipulation"]),
            _dt("Tile Placement Engine", D,
                "grid-based placement with adjacency constraints",
                "Validates placements on hex/square grid with scoring rules.",
                3_000_000,
                [AC.CONSTRAINT_SATISFACTION, AC.DATA_VISUALIZATION,
                 AC.REAL_TIME_FEEDBACK, AC.SIMULATION],
                pen=["real-time latency"], bon=["visualization"]),
            _dt("Draft Pick System", D,
                "sequential selection from a shared card pool",
                "Turn-based picking from a common display with draft history.",
                4_000_000,
                [AC.MATCHING, AC.SCHEDULING,
                 AC.INVENTORY, AC.COLLABORATIVE_EDITING],
                bon=["workflow", "collaboration"]),
        ]

    # -- 7. Paper forms ------------------------------------------------

    @staticmethod
    def paper_forms() -> List[DomainTool]:
        D = "paper_forms"
        return [
            _dt("Tax Return Form", D,
                "income/deduction data collection with calculation",
                "Multi-page form collecting income, expenses, computing tax.",
                150_000_000,
                [AC.FORM_WORKFLOW, AC.COMPUTATION_ON_DEMAND, AC.DATA_EXPORT,
                 AC.AUDIT_TRAIL, AC.CONSTRAINT_SATISFACTION],
                bon=["form handling", "data manipulation", "workflow"]),
            _dt("Medical Intake Form", D,
                "patient history and symptom data capture",
                "Questionnaire: demographics, history, meds, allergies, consent.",
                50_000_000,
                [AC.FORM_WORKFLOW, AC.DATA_INGESTION,
                 AC.AUDIT_TRAIL, AC.DATA_EXPORT],
                bon=["form handling", "workflow"]),
            _dt("Building Permit Application", D,
                "construction plan submission with compliance checks",
                "Site plans, contractor details, zoning compliance, fees.",
                5_000_000,
                [AC.FORM_WORKFLOW, AC.CONSTRAINT_SATISFACTION,
                 AC.AUDIT_TRAIL, AC.FILE_PROCESSING, AC.NOTIFICATION],
                bon=["form handling", "workflow"]),
            _dt("Expense Report", D,
                "receipt attachment and reimbursement request",
                "Expenses with dates, categories, receipts, manager approval.",
                30_000_000,
                [AC.FORM_WORKFLOW, AC.DATA_INGESTION, AC.AGGREGATION,
                 AC.DATA_EXPORT, AC.AUDIT_TRAIL],
                bon=["form handling", "data manipulation", "workflow"]),
            _dt("Inspection Checklist", D,
                "pass/fail item verification with notes",
                "Safety/quality checklist with pass/fail per item and notes.",
                10_000_000,
                [AC.FORM_WORKFLOW, AC.AUDIT_TRAIL,
                 AC.STATIC_REPORT, AC.COMPARISON],
                bon=["form handling", "workflow"]),
            _dt("Event Registration Form", D,
                "attendee sign-up with session selection",
                "Contact info, session preferences, dietary needs, payment.",
                20_000_000,
                [AC.FORM_WORKFLOW, AC.DATA_INGESTION, AC.SCHEDULING,
                 AC.NOTIFICATION, AC.DATA_EXPORT],
                bon=["form handling", "scheduling", "workflow"]),
        ]


# ── Utility helpers ──────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "is", "it", "by", "with", "from", "as",
})


def _tokenize(text: str) -> Set[str]:
    """Lowercase keyword tokens, stripped of stop words."""
    raw = text.lower().replace("-", " ").replace("/", " ").replace("_", " ")
    return {w for w in raw.split() if w.isalpha() and w not in _STOP_WORDS}
