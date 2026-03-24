"""Portfolio builder for the ideation pipeline.

Standalone module – no jugeo imports, Python stdlib only.
Constructs portfolios of existing applications in a domain,
either via keyword-based coordinate extraction (offline) or
from predefined builtin portfolios for common domains.
"""

from __future__ import annotations

import re
from typing import Any

from .models import ApplicationCoordinate, AppIdeationPurpose, ExistingApp, IdeaPortfolio

AC = ApplicationCoordinate

# ---------------------------------------------------------------------------
# Keyword → coordinate mapping
# ---------------------------------------------------------------------------

_KEYWORD_MAP: dict[ApplicationCoordinate, list[str]] = {
    AC.DATA_INGESTION: [
        "import", "upload", "ingest", "collect", "input", "sync",
        "fetch", "pull data", "scrape", "crawl",
    ],
    AC.DATA_TRANSFORMATION: [
        "transform", "convert", "normalize", "process", "parse",
        "reformat", "clean", "wrangle", "etl", "pipeline",
    ],
    AC.DATA_VISUALIZATION: [
        "chart", "graph", "plot", "visualize", "visual", "map",
        "diagram", "heatmap", "treemap", "sparkline",
    ],
    AC.DATA_EXPORT: [
        "export", "download", "generate pdf", "csv", "output file",
        "spreadsheet", "print report",
    ],
    AC.COMPUTATION_ON_DEMAND: [
        "calculate", "compute", "solver", "calculator", "estimate",
        "formula", "equation", "evaluate",
    ],
    AC.BATCH_PROCESSING: [
        "batch", "bulk", "queue", "pipeline", "automate",
        "mass operation", "cron", "scheduled job",
    ],
    AC.COMPARISON: [
        "compare", "diff", "versus", "side-by-side", "contrast",
        "benchmark", "head to head",
    ],
    AC.AGGREGATION: [
        "aggregate", "summarize", "total", "combine", "merge",
        "roll up", "consolidate", "group by",
    ],
    AC.FORM_WORKFLOW: [
        "form", "wizard", "multi-step", "survey", "questionnaire",
        "workflow", "onboarding", "intake",
    ],
    AC.FILE_PROCESSING: [
        "file", "document", "pdf", "image processing", "attachment",
        "file convert", "ocr",
    ],
    AC.REAL_TIME_FEEDBACK: [
        "real-time", "live", "instant", "preview", "interactive",
        "streaming", "live update", "reactive",
    ],
    AC.COLLABORATIVE_EDITING: [
        "collaborate", "share", "team", "multi-user", "concurrent",
        "co-edit", "multiplayer", "workspace",
    ],
    AC.SCHEDULING: [
        "schedule", "calendar", "appointment", "booking", "time slot",
        "planner", "recurring", "availability",
    ],
    AC.INVENTORY: [
        "inventory", "stock", "track items", "catalog", "assets",
        "warehouse", "sku", "ledger",
    ],
    AC.MATCHING: [
        "match", "connect", "pair", "recommend", "find similar",
        "suggestion", "discovery",
    ],
    AC.SIMULATION: [
        "simulate", "model", "scenario", "forecast", "what-if",
        "monte carlo", "projection", "predict",
    ],
    AC.AUDIT_TRAIL: [
        "audit", "history", "log", "track changes", "versioning",
        "changelog", "revision",
    ],
    AC.CONSTRAINT_SATISFACTION: [
        "constraint", "optimization", "rules", "solver", "validation",
        "feasibility", "linear program",
    ],
    AC.STATIC_REPORT: [
        "report", "generate report", "static", "print", "template",
        "pdf report", "summary document",
    ],
    AC.INTERACTIVE_DASHBOARD: [
        "dashboard", "analytics", "metrics", "kpi", "monitor",
        "drill down", "overview panel",
    ],
    AC.NOTIFICATION: [
        "notify", "alert", "reminder", "email notification", "webhook",
        "push notification", "sms",
    ],
    AC.API_PROVISION: [
        "api", "endpoint", "rest", "integration", "graphql",
        "developer api", "programmatic access",
    ],
}


# ---------------------------------------------------------------------------
# ApplicationPortfolioBuilder
# ---------------------------------------------------------------------------


class ApplicationPortfolioBuilder:
    """Stage 1: Constructs a portfolio of existing apps (without live web search).

    In production use, :meth:`build_portfolio` would call web search APIs.
    Here it uses keyword-based coordinate extraction for offline use.
    """

    # -- public API ----------------------------------------------------------

    def build_portfolio(self, purpose: AppIdeationPurpose) -> IdeaPortfolio:
        """Build a portfolio by searching/extracting existing apps for the domain.

        In production this would hit a search engine.  The offline
        implementation returns an empty portfolio that callers can
        populate via :class:`BuiltinPortfolios` instead.
        """
        queries = self._generate_category_queries(purpose)
        # In production: would call web search. Here, return empty portfolio.
        return IdeaPortfolio(
            ideas=[], domain=purpose.domain, construction_method="builder"
        )

    def build_from_search_results(
        self, purpose: AppIdeationPurpose, search_results: list[dict[str, str]]
    ) -> IdeaPortfolio:
        """Build a portfolio from pre-fetched search-result dicts."""
        apps = self._extract_app_descriptions(search_results)
        apps = self._deduplicate(apps)
        for app in apps:
            if not app.coordinates:
                app.coordinates = self._extract_coordinates(app)
        return IdeaPortfolio(
            ideas=apps, domain=purpose.domain, construction_method="search"
        )

    def enrich_portfolio(self, portfolio: IdeaPortfolio) -> IdeaPortfolio:
        """Re-tag coordinates and fill gaps in an existing portfolio."""
        for app in portfolio.ideas:
            extra = self._extract_coordinates(app)
            app.coordinates = app.coordinates | extra
        supplementary = self._gap_aware_supplementary(portfolio)
        if supplementary:
            portfolio.ideas.extend(supplementary)
        return portfolio

    # -- query generation ----------------------------------------------------

    def _generate_category_queries(
        self, purpose: AppIdeationPurpose
    ) -> list[str]:
        """Generate 5-8 search queries tailored to the domain."""
        domain = purpose.domain
        population = purpose.user_population
        queries = [
            f"best {domain} web apps",
            f"open source {domain} tools",
            f"{domain} software for {population}",
            f"{domain} flask python projects github",
            f"self-hosted {domain} tools",
            f"{domain} alternatives comparison",
            f"{domain} online tools free",
        ]
        for tag in purpose.constraint_tags[:3]:
            queries.append(f"{domain} {tag} tool")
        return queries

    # -- extraction / parsing ------------------------------------------------

    def _extract_app_descriptions(
        self, search_results: list[dict[str, str]]
    ) -> list[ExistingApp]:
        """Parse search-result dicts into :class:`ExistingApp` objects.

        Each dict is expected to carry ``name``, ``url``, and
        ``description`` keys.
        """
        apps: list[ExistingApp] = []
        for r in search_results:
            name = r.get("name", "Unknown")
            url = r.get("url", "")
            desc = r.get("description", "")
            coords = self._extract_coordinates_from_text(desc)
            quality = r.get("quality_tier", "medium")
            users = int(r.get("user_base_estimate", 0))
            apps.append(
                ExistingApp(
                    name=name,
                    url=url,
                    description=desc,
                    coordinates=coords,
                    quality_tier=quality,
                    user_base_estimate=users,
                )
            )
        return apps

    def _deduplicate(self, apps: list[ExistingApp]) -> list[ExistingApp]:
        """Remove duplicate apps by name (case-insensitive)."""
        seen: set[str] = set()
        result: list[ExistingApp] = []
        for app in apps:
            key = app.name.lower().strip()
            if key not in seen:
                seen.add(key)
                result.append(app)
        return result

    # -- coordinate extraction -----------------------------------------------

    def _extract_coordinates(self, app: ExistingApp) -> set[ApplicationCoordinate]:
        """Classify an app by coordinates using keyword matching."""
        text = f"{app.name} {app.description}"
        return self._extract_coordinates_from_text(text)

    def _extract_coordinates_from_text(
        self, text: str
    ) -> set[ApplicationCoordinate]:
        """Keyword-based coordinate extraction from free text."""
        lower = text.lower()
        found: set[ApplicationCoordinate] = set()
        for coord, keywords in _KEYWORD_MAP.items():
            for kw in keywords:
                if kw in lower:
                    found.add(coord)
                    break
        return found

    # -- gap-aware supplementation -------------------------------------------

    def _gap_aware_supplementary(
        self, portfolio: IdeaPortfolio
    ) -> list[ExistingApp]:
        """Identify uncovered coordinate regions.

        In production this would issue targeted searches for the missing
        coordinates.  The offline version returns an empty list.
        """
        covered: set[ApplicationCoordinate] = set()
        for app in portfolio.ideas:
            covered.update(app.coordinates)
        uncovered = [c for c in ApplicationCoordinate if c not in covered]
        # In production: web search for each uncovered coordinate
        return []

    def coverage_report(
        self, portfolio: IdeaPortfolio
    ) -> dict[str, Any]:
        """Return a summary of coordinate coverage for the portfolio."""
        coord_counts: dict[ApplicationCoordinate, int] = {
            c: 0 for c in ApplicationCoordinate
        }
        for app in portfolio.ideas:
            for c in app.coordinates:
                coord_counts[c] += 1
        covered = {c for c, n in coord_counts.items() if n > 0}
        uncovered = set(ApplicationCoordinate) - covered
        return {
            "total_apps": len(portfolio.ideas),
            "covered": sorted(c.value for c in covered),
            "uncovered": sorted(c.value for c in uncovered),
            "coverage_ratio": len(covered) / len(ApplicationCoordinate),
            "counts": {c.value: n for c, n in coord_counts.items()},
        }


# ---------------------------------------------------------------------------
# Helper for concise app construction
# ---------------------------------------------------------------------------

def _app(
    name: str,
    url: str,
    desc: str,
    coords: set[ApplicationCoordinate],
    tier: str = "medium",
    users: int = 0,
) -> ExistingApp:
    return ExistingApp(
        name=name,
        url=url,
        description=desc,
        coordinates=coords,
        quality_tier=tier,
        user_base_estimate=users,
    )


# ---------------------------------------------------------------------------
# BuiltinPortfolios
# ---------------------------------------------------------------------------


class BuiltinPortfolios:
    """Predefined portfolios for common domains.

    Each static method returns an :class:`IdeaPortfolio` pre-populated
    with 15-25 realistic :class:`ExistingApp` entries tagged with
    :class:`ApplicationCoordinate` values.
    """

    # ------------------------------------------------------------------ #
    # Personal Finance
    # ------------------------------------------------------------------ #

    @staticmethod
    def personal_finance() -> IdeaPortfolio:
        """Portfolio of personal-finance web applications."""
        apps = [
            _app(
                "YNAB", "https://www.ynab.com",
                "Zero-based budgeting app with real-time bank sync and goal tracking.",
                {AC.DATA_INGESTION, AC.REAL_TIME_FEEDBACK, AC.FORM_WORKFLOW,
                 AC.INTERACTIVE_DASHBOARD, AC.NOTIFICATION},
                "high", 2_000_000,
            ),
            _app(
                "Mint", "https://mint.intuit.com",
                "Free personal finance dashboard that aggregates bank accounts and tracks spending.",
                {AC.DATA_INGESTION, AC.AGGREGATION, AC.INTERACTIVE_DASHBOARD,
                 AC.NOTIFICATION, AC.DATA_VISUALIZATION},
                "high", 20_000_000,
            ),
            _app(
                "Empower Personal Dashboard", "https://www.empower.com",
                "Net-worth tracker with investment analytics and retirement planner.",
                {AC.DATA_INGESTION, AC.AGGREGATION, AC.SIMULATION,
                 AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION},
                "high", 3_000_000,
            ),
            _app(
                "Copilot Money", "https://copilot.money",
                "iOS finance tracker with real-time sync, budgets, and spending insights.",
                {AC.DATA_INGESTION, AC.REAL_TIME_FEEDBACK,
                 AC.INTERACTIVE_DASHBOARD, AC.NOTIFICATION},
                "high", 500_000,
            ),
            _app(
                "Tiller Money", "https://www.tillerhq.com",
                "Automated spreadsheet-based finance tracking with bank feeds.",
                {AC.DATA_INGESTION, AC.DATA_EXPORT, AC.DATA_TRANSFORMATION,
                 AC.STATIC_REPORT},
                "medium", 200_000,
            ),
            _app(
                "Lunch Money", "https://lunchmoney.app",
                "Developer-friendly budgeting app with API, multi-currency, and CSV import.",
                {AC.DATA_INGESTION, AC.API_PROVISION, AC.DATA_EXPORT,
                 AC.INTERACTIVE_DASHBOARD, AC.DATA_TRANSFORMATION},
                "medium", 50_000,
            ),
            _app(
                "Firefly III", "https://www.firefly-iii.org",
                "Self-hosted open-source finance manager with budgets, rules, and reports.",
                {AC.DATA_INGESTION, AC.DATA_TRANSFORMATION, AC.STATIC_REPORT,
                 AC.AUDIT_TRAIL, AC.CONSTRAINT_SATISFACTION, AC.API_PROVISION},
                "medium", 100_000,
            ),
            _app(
                "GnuCash", "https://www.gnucash.org",
                "Double-entry accounting for personal and small-business finance.",
                {AC.DATA_INGESTION, AC.DATA_EXPORT, AC.STATIC_REPORT,
                 AC.AUDIT_TRAIL, AC.COMPUTATION_ON_DEMAND},
                "medium", 500_000,
            ),
            _app(
                "Quicken", "https://www.quicken.com",
                "Desktop finance suite with investment tracking, bill pay, and tax reports.",
                {AC.DATA_INGESTION, AC.SCHEDULING, AC.STATIC_REPORT,
                 AC.SIMULATION, AC.INTERACTIVE_DASHBOARD, AC.NOTIFICATION},
                "high", 10_000_000,
            ),
            _app(
                "Wave", "https://www.waveapps.com",
                "Free invoicing and accounting with receipt scanning and reporting.",
                {AC.DATA_INGESTION, AC.FILE_PROCESSING, AC.STATIC_REPORT,
                 AC.FORM_WORKFLOW, AC.DATA_EXPORT},
                "medium", 3_000_000,
            ),
            _app(
                "Money Manager EX", "https://www.moneymanagerex.org",
                "Open-source cross-platform money management with budgets and reports.",
                {AC.DATA_INGESTION, AC.STATIC_REPORT, AC.DATA_EXPORT,
                 AC.INTERACTIVE_DASHBOARD},
                "low", 80_000,
            ),
            _app(
                "PocketSmith", "https://www.pocketsmith.com",
                "Calendar-based budgeting with what-if scenarios and bank feeds.",
                {AC.DATA_INGESTION, AC.SCHEDULING, AC.SIMULATION,
                 AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION},
                "medium", 150_000,
            ),
            _app(
                "Toshl Finance", "https://toshl.com",
                "Expense tracker with bank connections, tags, budgets, and export.",
                {AC.DATA_INGESTION, AC.DATA_EXPORT, AC.INTERACTIVE_DASHBOARD,
                 AC.NOTIFICATION},
                "medium", 300_000,
            ),
            _app(
                "Emma", "https://emma-app.com",
                "Subscription tracker and budgeting app with smart notifications.",
                {AC.DATA_INGESTION, AC.NOTIFICATION, AC.AGGREGATION,
                 AC.MATCHING, AC.INTERACTIVE_DASHBOARD},
                "medium", 1_000_000,
            ),
            _app(
                "MoneyDance", "https://moneydance.com",
                "Java-based personal finance with investment tracking and reports.",
                {AC.DATA_INGESTION, AC.COMPUTATION_ON_DEMAND,
                 AC.STATIC_REPORT, AC.DATA_EXPORT},
                "medium", 100_000,
            ),
            _app(
                "HomeBank", "https://homebank.free.fr",
                "Open-source personal accounting with charts, budgets, and CSV import.",
                {AC.DATA_INGESTION, AC.DATA_VISUALIZATION,
                 AC.STATIC_REPORT, AC.DATA_EXPORT},
                "low", 60_000,
            ),
            _app(
                "Actual Budget", "https://actualbudget.com",
                "Open-source envelope-budgeting with local-first sync and rule engine.",
                {AC.DATA_INGESTION, AC.CONSTRAINT_SATISFACTION,
                 AC.REAL_TIME_FEEDBACK, AC.DATA_EXPORT, AC.API_PROVISION},
                "medium", 40_000,
            ),
        ]
        return IdeaPortfolio(
            ideas=apps, domain="personal_finance", construction_method="builtin"
        )

    # ------------------------------------------------------------------ #
    # Education
    # ------------------------------------------------------------------ #

    @staticmethod
    def education() -> IdeaPortfolio:
        """Portfolio of education and learning web applications."""
        apps = [
            _app(
                "Anki", "https://apps.ankiweb.net",
                "Spaced-repetition flashcard app with scheduling algorithm and add-ons.",
                {AC.SCHEDULING, AC.COMPUTATION_ON_DEMAND, AC.MATCHING,
                 AC.DATA_INGESTION, AC.DATA_EXPORT},
                "high", 10_000_000,
            ),
            _app(
                "Quizlet", "https://quizlet.com",
                "Flashcard platform with collaborative sets, games, and progress tracking.",
                {AC.COLLABORATIVE_EDITING, AC.MATCHING,
                 AC.INTERACTIVE_DASHBOARD, AC.REAL_TIME_FEEDBACK},
                "high", 60_000_000,
            ),
            _app(
                "Khan Academy", "https://www.khanacademy.org",
                "Free online courses with exercises, progress dashboard, and mastery goals.",
                {AC.INTERACTIVE_DASHBOARD, AC.REAL_TIME_FEEDBACK,
                 AC.DATA_VISUALIZATION, AC.COMPUTATION_ON_DEMAND},
                "high", 120_000_000,
            ),
            _app(
                "Duolingo", "https://www.duolingo.com",
                "Gamified language learning with spaced repetition and streaks.",
                {AC.SCHEDULING, AC.REAL_TIME_FEEDBACK, AC.NOTIFICATION,
                 AC.MATCHING, AC.INTERACTIVE_DASHBOARD},
                "high", 80_000_000,
            ),
            _app(
                "Moodle", "https://moodle.org",
                "Open-source learning management system with quizzes, forums, and grading.",
                {AC.FORM_WORKFLOW, AC.COLLABORATIVE_EDITING, AC.AUDIT_TRAIL,
                 AC.STATIC_REPORT, AC.NOTIFICATION, AC.SCHEDULING},
                "high", 300_000_000,
            ),
            _app(
                "Canvas LMS", "https://www.instructure.com/canvas",
                "Cloud LMS with assignments, rubrics, peer review, and analytics.",
                {AC.FORM_WORKFLOW, AC.COLLABORATIVE_EDITING,
                 AC.INTERACTIVE_DASHBOARD, AC.NOTIFICATION,
                 AC.STATIC_REPORT, AC.API_PROVISION},
                "high", 30_000_000,
            ),
            _app(
                "Notion", "https://www.notion.so",
                "All-in-one workspace with notes, databases, wikis, and collaboration.",
                {AC.COLLABORATIVE_EDITING, AC.DATA_INGESTION,
                 AC.FORM_WORKFLOW, AC.DATA_EXPORT, AC.API_PROVISION},
                "high", 30_000_000,
            ),
            _app(
                "Obsidian", "https://obsidian.md",
                "Local-first markdown knowledge base with graph view and plugins.",
                {AC.DATA_VISUALIZATION, AC.DATA_EXPORT, AC.MATCHING,
                 AC.FILE_PROCESSING},
                "high", 4_000_000,
            ),
            _app(
                "RemNote", "https://www.remnote.com",
                "Note-taking with built-in spaced repetition and knowledge graph.",
                {AC.SCHEDULING, AC.MATCHING, AC.DATA_VISUALIZATION,
                 AC.REAL_TIME_FEEDBACK},
                "medium", 500_000,
            ),
            _app(
                "Orbit", "https://withorbit.com",
                "Experimental spaced-repetition tool embedded in web articles.",
                {AC.SCHEDULING, AC.MATCHING, AC.DATA_INGESTION},
                "low", 20_000,
            ),
            _app(
                "Coursera", "https://www.coursera.org",
                "Online course platform with certificates, peer grading, and forums.",
                {AC.FORM_WORKFLOW, AC.COLLABORATIVE_EDITING,
                 AC.STATIC_REPORT, AC.SCHEDULING},
                "high", 100_000_000,
            ),
            _app(
                "edX", "https://www.edx.org",
                "Massive open online courses from universities with proctored exams.",
                {AC.FORM_WORKFLOW, AC.AUDIT_TRAIL, AC.STATIC_REPORT,
                 AC.SCHEDULING},
                "high", 40_000_000,
            ),
            _app(
                "Brilliant", "https://brilliant.org",
                "Interactive STEM courses with problem-solving and visual explanations.",
                {AC.REAL_TIME_FEEDBACK, AC.COMPUTATION_ON_DEMAND,
                 AC.SIMULATION, AC.INTERACTIVE_DASHBOARD},
                "high", 10_000_000,
            ),
            _app(
                "Socratic by Google", "https://socratic.org",
                "AI homework helper that explains step-by-step solutions from photos.",
                {AC.FILE_PROCESSING, AC.COMPUTATION_ON_DEMAND,
                 AC.REAL_TIME_FEEDBACK, AC.MATCHING},
                "medium", 5_000_000,
            ),
            _app(
                "Roam Research", "https://roamresearch.com",
                "Networked thought tool with bidirectional links and daily notes.",
                {AC.COLLABORATIVE_EDITING, AC.DATA_VISUALIZATION,
                 AC.MATCHING, AC.AUDIT_TRAIL},
                "medium", 200_000,
            ),
            _app(
                "Gradescope", "https://www.gradescope.com",
                "AI-assisted grading for handwritten and code assignments.",
                {AC.FILE_PROCESSING, AC.BATCH_PROCESSING,
                 AC.STATIC_REPORT, AC.AGGREGATION},
                "high", 2_000_000,
            ),
            _app(
                "Kahoot!", "https://kahoot.com",
                "Live quiz game platform for classrooms with real-time leaderboards.",
                {AC.REAL_TIME_FEEDBACK, AC.COLLABORATIVE_EDITING,
                 AC.COMPARISON, AC.INTERACTIVE_DASHBOARD},
                "high", 9_000_000,
            ),
            _app(
                "Excalidraw", "https://excalidraw.com",
                "Collaborative virtual whiteboard for sketching diagrams and ideas.",
                {AC.COLLABORATIVE_EDITING, AC.REAL_TIME_FEEDBACK,
                 AC.DATA_VISUALIZATION, AC.DATA_EXPORT},
                "medium", 3_000_000,
            ),
        ]
        return IdeaPortfolio(
            ideas=apps, domain="education", construction_method="builtin"
        )

    # ------------------------------------------------------------------ #
    # Developer Tools
    # ------------------------------------------------------------------ #

    @staticmethod
    def developer_tools() -> IdeaPortfolio:
        """Portfolio of developer tools and platforms."""
        apps = [
            _app(
                "GitHub", "https://github.com",
                "Code hosting with pull requests, actions CI/CD, issues, and API.",
                {AC.COLLABORATIVE_EDITING, AC.AUDIT_TRAIL, AC.API_PROVISION,
                 AC.BATCH_PROCESSING, AC.NOTIFICATION},
                "high", 100_000_000,
            ),
            _app(
                "GitLab", "https://gitlab.com",
                "DevOps platform with CI/CD pipelines, issue boards, and container registry.",
                {AC.COLLABORATIVE_EDITING, AC.AUDIT_TRAIL, AC.BATCH_PROCESSING,
                 AC.API_PROVISION, AC.INTERACTIVE_DASHBOARD},
                "high", 30_000_000,
            ),
            _app(
                "Jira", "https://www.atlassian.com/software/jira",
                "Issue tracker with sprint boards, backlog management, and workflow automation.",
                {AC.FORM_WORKFLOW, AC.SCHEDULING, AC.INTERACTIVE_DASHBOARD,
                 AC.NOTIFICATION, AC.AUDIT_TRAIL},
                "high", 10_000_000,
            ),
            _app(
                "Linear", "https://linear.app",
                "Fast project tracker with cycles, triage, and keyboard-first design.",
                {AC.FORM_WORKFLOW, AC.SCHEDULING, AC.NOTIFICATION,
                 AC.INTERACTIVE_DASHBOARD, AC.API_PROVISION},
                "high", 2_000_000,
            ),
            _app(
                "Postman", "https://www.postman.com",
                "API testing platform with collections, environments, and automated tests.",
                {AC.API_PROVISION, AC.BATCH_PROCESSING, AC.COMPARISON,
                 AC.COLLABORATIVE_EDITING, AC.DATA_EXPORT},
                "high", 25_000_000,
            ),
            _app(
                "Insomnia", "https://insomnia.rest",
                "Open-source API client with GraphQL support and environment variables.",
                {AC.API_PROVISION, AC.COMPARISON, AC.DATA_EXPORT},
                "medium", 1_000_000,
            ),
            _app(
                "TablePlus", "https://tableplus.com",
                "Native database GUI for Postgres, MySQL, SQLite, and more.",
                {AC.DATA_INGESTION, AC.DATA_VISUALIZATION, AC.DATA_EXPORT,
                 AC.FORM_WORKFLOW},
                "medium", 500_000,
            ),
            _app(
                "DBeaver", "https://dbeaver.io",
                "Universal database tool with SQL editor, ER diagrams, and data export.",
                {AC.DATA_INGESTION, AC.DATA_VISUALIZATION, AC.DATA_EXPORT,
                 AC.DATA_TRANSFORMATION, AC.COMPARISON},
                "medium", 5_000_000,
            ),
            _app(
                "Sentry", "https://sentry.io",
                "Error monitoring with stack traces, performance tracing, and alerts.",
                {AC.AUDIT_TRAIL, AC.NOTIFICATION, AC.INTERACTIVE_DASHBOARD,
                 AC.AGGREGATION, AC.REAL_TIME_FEEDBACK},
                "high", 4_000_000,
            ),
            _app(
                "Datadog", "https://www.datadoghq.com",
                "Monitoring platform with metrics, traces, logs, and dashboards.",
                {AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION,
                 AC.AGGREGATION, AC.NOTIFICATION, AC.REAL_TIME_FEEDBACK,
                 AC.API_PROVISION},
                "high", 2_500_000,
            ),
            _app(
                "Grafana", "https://grafana.com",
                "Open-source dashboarding for metrics with alerting and plugin ecosystem.",
                {AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION,
                 AC.NOTIFICATION, AC.DATA_INGESTION, AC.API_PROVISION},
                "high", 10_000_000,
            ),
            _app(
                "Prometheus", "https://prometheus.io",
                "Time-series monitoring with PromQL, alerting rules, and scrape targets.",
                {AC.DATA_INGESTION, AC.AGGREGATION, AC.NOTIFICATION,
                 AC.COMPUTATION_ON_DEMAND, AC.API_PROVISION},
                "high", 8_000_000,
            ),
            _app(
                "PagerDuty", "https://www.pagerduty.com",
                "Incident management with on-call scheduling, escalation, and alerting.",
                {AC.NOTIFICATION, AC.SCHEDULING, AC.AUDIT_TRAIL,
                 AC.FORM_WORKFLOW},
                "high", 1_500_000,
            ),
            _app(
                "Codecov", "https://about.codecov.io",
                "Code coverage reporting integrated with CI/CD and pull requests.",
                {AC.STATIC_REPORT, AC.COMPARISON, AC.INTERACTIVE_DASHBOARD,
                 AC.BATCH_PROCESSING},
                "medium", 1_000_000,
            ),
            _app(
                "Vercel", "https://vercel.com",
                "Frontend deployment platform with preview deploys and edge functions.",
                {AC.BATCH_PROCESSING, AC.REAL_TIME_FEEDBACK,
                 AC.API_PROVISION, AC.NOTIFICATION},
                "high", 3_000_000,
            ),
            _app(
                "Railway", "https://railway.app",
                "Cloud deployment with one-click databases, environments, and logs.",
                {AC.BATCH_PROCESSING, AC.AUDIT_TRAIL,
                 AC.INTERACTIVE_DASHBOARD, AC.DATA_INGESTION},
                "medium", 500_000,
            ),
            _app(
                "Swagger UI", "https://swagger.io",
                "Interactive API documentation generated from OpenAPI specifications.",
                {AC.API_PROVISION, AC.STATIC_REPORT,
                 AC.REAL_TIME_FEEDBACK, AC.DATA_VISUALIZATION},
                "high", 15_000_000,
            ),
        ]
        return IdeaPortfolio(
            ideas=apps, domain="developer_tools", construction_method="builtin"
        )

    # ------------------------------------------------------------------ #
    # Data Science
    # ------------------------------------------------------------------ #

    @staticmethod
    def data_science() -> IdeaPortfolio:
        """Portfolio of data science and ML tools."""
        apps = [
            _app(
                "Jupyter", "https://jupyter.org",
                "Interactive notebooks for code, visualization, and narrative text.",
                {AC.COMPUTATION_ON_DEMAND, AC.DATA_VISUALIZATION,
                 AC.REAL_TIME_FEEDBACK, AC.DATA_EXPORT, AC.COLLABORATIVE_EDITING},
                "high", 15_000_000,
            ),
            _app(
                "RStudio", "https://posit.co",
                "IDE for R with plots, packages, and publishing to Shiny or Quarto.",
                {AC.COMPUTATION_ON_DEMAND, AC.DATA_VISUALIZATION,
                 AC.STATIC_REPORT, AC.DATA_EXPORT},
                "high", 5_000_000,
            ),
            _app(
                "Tableau", "https://www.tableau.com",
                "Drag-and-drop visual analytics with dashboards and data connectors.",
                {AC.DATA_VISUALIZATION, AC.INTERACTIVE_DASHBOARD,
                 AC.DATA_INGESTION, AC.AGGREGATION, AC.DATA_EXPORT},
                "high", 2_000_000,
            ),
            _app(
                "Metabase", "https://www.metabase.com",
                "Open-source BI tool with SQL and no-code query builder plus dashboards.",
                {AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION,
                 AC.AGGREGATION, AC.DATA_EXPORT, AC.API_PROVISION},
                "high", 500_000,
            ),
            _app(
                "Apache Superset", "https://superset.apache.org",
                "Open-source data exploration with rich visualizations and SQL lab.",
                {AC.DATA_VISUALIZATION, AC.INTERACTIVE_DASHBOARD,
                 AC.DATA_INGESTION, AC.AGGREGATION, AC.COMPUTATION_ON_DEMAND},
                "high", 300_000,
            ),
            _app(
                "Redash", "https://redash.io",
                "Connect to any data source, query, visualize, and share dashboards.",
                {AC.DATA_INGESTION, AC.DATA_VISUALIZATION,
                 AC.INTERACTIVE_DASHBOARD, AC.COLLABORATIVE_EDITING,
                 AC.DATA_EXPORT},
                "medium", 200_000,
            ),
            _app(
                "Observable", "https://observablehq.com",
                "Reactive JavaScript notebooks for data visualization and exploration.",
                {AC.DATA_VISUALIZATION, AC.REAL_TIME_FEEDBACK,
                 AC.COLLABORATIVE_EDITING, AC.COMPUTATION_ON_DEMAND},
                "medium", 150_000,
            ),
            _app(
                "Streamlit", "https://streamlit.io",
                "Python framework for building data apps with interactive widgets.",
                {AC.REAL_TIME_FEEDBACK, AC.DATA_VISUALIZATION,
                 AC.FORM_WORKFLOW, AC.COMPUTATION_ON_DEMAND, AC.DATA_EXPORT},
                "high", 1_000_000,
            ),
            _app(
                "Gradio", "https://www.gradio.app",
                "Build ML demo interfaces with a few lines of Python code.",
                {AC.REAL_TIME_FEEDBACK, AC.FORM_WORKFLOW,
                 AC.API_PROVISION, AC.FILE_PROCESSING},
                "high", 800_000,
            ),
            _app(
                "Weights & Biases", "https://wandb.ai",
                "ML experiment tracking with dashboards, sweeps, and model registry.",
                {AC.AUDIT_TRAIL, AC.INTERACTIVE_DASHBOARD,
                 AC.DATA_VISUALIZATION, AC.COMPARISON, AC.COLLABORATIVE_EDITING},
                "high", 500_000,
            ),
            _app(
                "MLflow", "https://mlflow.org",
                "Open-source ML lifecycle platform: tracking, models, registry, and serving.",
                {AC.AUDIT_TRAIL, AC.COMPARISON, AC.DATA_EXPORT,
                 AC.API_PROVISION, AC.BATCH_PROCESSING},
                "high", 600_000,
            ),
            _app(
                "DVC", "https://dvc.org",
                "Data version control for ML projects with pipeline management.",
                {AC.AUDIT_TRAIL, AC.BATCH_PROCESSING,
                 AC.DATA_INGESTION, AC.DATA_TRANSFORMATION},
                "medium", 200_000,
            ),
            _app(
                "Hugging Face", "https://huggingface.co",
                "Model hub with datasets, spaces for demos, and inference API.",
                {AC.API_PROVISION, AC.MATCHING, AC.DATA_INGESTION,
                 AC.COLLABORATIVE_EDITING, AC.FILE_PROCESSING},
                "high", 3_000_000,
            ),
            _app(
                "Kaggle", "https://www.kaggle.com",
                "Data science competitions with notebooks, datasets, and leaderboards.",
                {AC.COMPUTATION_ON_DEMAND, AC.COLLABORATIVE_EDITING,
                 AC.COMPARISON, AC.DATA_INGESTION, AC.INTERACTIVE_DASHBOARD},
                "high", 15_000_000,
            ),
            _app(
                "Deepnote", "https://deepnote.com",
                "Collaborative data science notebooks with SQL, Python, and scheduling.",
                {AC.COLLABORATIVE_EDITING, AC.COMPUTATION_ON_DEMAND,
                 AC.SCHEDULING, AC.DATA_VISUALIZATION, AC.REAL_TIME_FEEDBACK},
                "medium", 200_000,
            ),
            _app(
                "Hex", "https://hex.tech",
                "Collaborative data workspace with SQL, Python, and shareable apps.",
                {AC.COLLABORATIVE_EDITING, AC.COMPUTATION_ON_DEMAND,
                 AC.DATA_VISUALIZATION, AC.INTERACTIVE_DASHBOARD,
                 AC.DATA_EXPORT},
                "medium", 100_000,
            ),
            _app(
                "Label Studio", "https://labelstud.io",
                "Open-source data labeling tool for text, image, audio, and video.",
                {AC.FILE_PROCESSING, AC.FORM_WORKFLOW,
                 AC.COLLABORATIVE_EDITING, AC.DATA_EXPORT,
                 AC.BATCH_PROCESSING},
                "medium", 150_000,
            ),
            _app(
                "Great Expectations", "https://greatexpectations.io",
                "Data quality validation framework with profiling and docs generation.",
                {AC.CONSTRAINT_SATISFACTION, AC.STATIC_REPORT,
                 AC.DATA_TRANSFORMATION, AC.BATCH_PROCESSING,
                 AC.AUDIT_TRAIL},
                "medium", 100_000,
            ),
        ]
        return IdeaPortfolio(
            ideas=apps, domain="data_science", construction_method="builtin"
        )

    # ------------------------------------------------------------------ #
    # Small Business
    # ------------------------------------------------------------------ #

    @staticmethod
    def small_business() -> IdeaPortfolio:
        """Portfolio of small-business web applications."""
        apps = [
            _app(
                "QuickBooks Online", "https://quickbooks.intuit.com",
                "Cloud accounting with invoicing, expense tracking, and tax reports.",
                {AC.DATA_INGESTION, AC.STATIC_REPORT, AC.FORM_WORKFLOW,
                 AC.DATA_EXPORT, AC.NOTIFICATION, AC.AUDIT_TRAIL},
                "high", 7_000_000,
            ),
            _app(
                "FreshBooks", "https://www.freshbooks.com",
                "Invoicing and time-tracking software for freelancers and small teams.",
                {AC.FORM_WORKFLOW, AC.SCHEDULING, AC.STATIC_REPORT,
                 AC.NOTIFICATION, AC.DATA_EXPORT},
                "high", 3_000_000,
            ),
            _app(
                "Wave Financial", "https://www.waveapps.com",
                "Free accounting, invoicing, and receipt scanning for small businesses.",
                {AC.DATA_INGESTION, AC.FILE_PROCESSING, AC.STATIC_REPORT,
                 AC.FORM_WORKFLOW, AC.DATA_EXPORT},
                "medium", 3_000_000,
            ),
            _app(
                "Square", "https://squareup.com",
                "POS system with payments, inventory management, and sales analytics.",
                {AC.INVENTORY, AC.INTERACTIVE_DASHBOARD, AC.DATA_INGESTION,
                 AC.REAL_TIME_FEEDBACK, AC.API_PROVISION},
                "high", 4_000_000,
            ),
            _app(
                "Shopify", "https://www.shopify.com",
                "E-commerce platform with storefront, inventory, shipping, and analytics.",
                {AC.INVENTORY, AC.INTERACTIVE_DASHBOARD, AC.FORM_WORKFLOW,
                 AC.DATA_EXPORT, AC.API_PROVISION, AC.NOTIFICATION},
                "high", 4_000_000,
            ),
            _app(
                "WooCommerce", "https://woocommerce.com",
                "Open-source WordPress e-commerce plugin with extensions.",
                {AC.INVENTORY, AC.FORM_WORKFLOW, AC.DATA_EXPORT,
                 AC.API_PROVISION},
                "high", 5_000_000,
            ),
            _app(
                "Airtable", "https://www.airtable.com",
                "Spreadsheet-database hybrid with views, automations, and integrations.",
                {AC.DATA_INGESTION, AC.FORM_WORKFLOW, AC.COLLABORATIVE_EDITING,
                 AC.API_PROVISION, AC.INTERACTIVE_DASHBOARD,
                 AC.BATCH_PROCESSING},
                "high", 5_000_000,
            ),
            _app(
                "Trello", "https://trello.com",
                "Kanban board for project management with power-ups and automation.",
                {AC.FORM_WORKFLOW, AC.COLLABORATIVE_EDITING, AC.NOTIFICATION,
                 AC.SCHEDULING},
                "high", 10_000_000,
            ),
            _app(
                "Asana", "https://asana.com",
                "Work management with tasks, timelines, goals, and team dashboards.",
                {AC.SCHEDULING, AC.COLLABORATIVE_EDITING,
                 AC.INTERACTIVE_DASHBOARD, AC.NOTIFICATION, AC.FORM_WORKFLOW},
                "high", 3_000_000,
            ),
            _app(
                "HubSpot CRM", "https://www.hubspot.com",
                "Free CRM with contact management, deal pipeline, and email tracking.",
                {AC.MATCHING, AC.FORM_WORKFLOW, AC.NOTIFICATION,
                 AC.INTERACTIVE_DASHBOARD, AC.AUDIT_TRAIL, AC.DATA_EXPORT},
                "high", 6_000_000,
            ),
            _app(
                "Mailchimp", "https://mailchimp.com",
                "Email marketing with campaigns, automation, audience segments, and analytics.",
                {AC.NOTIFICATION, AC.BATCH_PROCESSING, AC.INTERACTIVE_DASHBOARD,
                 AC.FORM_WORKFLOW, AC.DATA_VISUALIZATION, AC.AGGREGATION},
                "high", 13_000_000,
            ),
            _app(
                "Calendly", "https://calendly.com",
                "Scheduling tool with availability rules, team pages, and integrations.",
                {AC.SCHEDULING, AC.FORM_WORKFLOW, AC.NOTIFICATION,
                 AC.API_PROVISION},
                "high", 10_000_000,
            ),
            _app(
                "Stripe", "https://stripe.com",
                "Payment processing API with subscriptions, invoicing, and dashboards.",
                {AC.API_PROVISION, AC.INTERACTIVE_DASHBOARD,
                 AC.NOTIFICATION, AC.AUDIT_TRAIL, AC.DATA_EXPORT},
                "high", 3_000_000,
            ),
            _app(
                "PayPal Business", "https://www.paypal.com/business",
                "Online payments with invoicing, checkout buttons, and reporting.",
                {AC.API_PROVISION, AC.FORM_WORKFLOW, AC.STATIC_REPORT,
                 AC.NOTIFICATION, AC.DATA_EXPORT},
                "high", 30_000_000,
            ),
            _app(
                "Gusto", "https://gusto.com",
                "Payroll, benefits, and HR platform for small businesses.",
                {AC.SCHEDULING, AC.FORM_WORKFLOW, AC.STATIC_REPORT,
                 AC.NOTIFICATION, AC.CONSTRAINT_SATISFACTION, AC.AUDIT_TRAIL},
                "high", 300_000,
            ),
            _app(
                "Xero", "https://www.xero.com",
                "Cloud accounting with bank reconciliation, invoicing, and reporting.",
                {AC.DATA_INGESTION, AC.STATIC_REPORT, AC.AGGREGATION,
                 AC.FORM_WORKFLOW, AC.DATA_EXPORT, AC.AUDIT_TRAIL},
                "high", 3_500_000,
            ),
            _app(
                "Zoho CRM", "https://www.zoho.com/crm",
                "CRM suite with sales automation, analytics, and multichannel communication.",
                {AC.MATCHING, AC.NOTIFICATION, AC.INTERACTIVE_DASHBOARD,
                 AC.BATCH_PROCESSING, AC.API_PROVISION, AC.FORM_WORKFLOW},
                "high", 2_000_000,
            ),
            _app(
                "Inventory Lab", "https://www.inventorylab.com",
                "Amazon seller inventory management with profit analytics.",
                {AC.INVENTORY, AC.INTERACTIVE_DASHBOARD, AC.DATA_EXPORT,
                 AC.COMPUTATION_ON_DEMAND},
                "medium", 100_000,
            ),
        ]
        return IdeaPortfolio(
            ideas=apps, domain="small_business", construction_method="builtin"
        )
