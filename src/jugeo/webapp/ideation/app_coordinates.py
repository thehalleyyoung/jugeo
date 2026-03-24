"""Application Coordinate Space for web application ideation.

Provides the 22-dimensional coordinate system that characterises what a
web application *does*.  Each coordinate represents a fundamental capability;
real applications occupy a subset of these coordinates, and the geometry of
that subset drives novelty and feasibility analysis.

This module is standalone — it uses only the Python standard library plus
the ``ApplicationCoordinate`` enum defined in ``.models``.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from .models import ApplicationCoordinate


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------

_DESCRIPTIONS: dict[ApplicationCoordinate, str] = {
    ApplicationCoordinate.DATA_INGESTION: (
        "Accepts data from users or external sources — CSV upload, API pull, "
        "form input, clipboard paste, or sensor streams"
    ),
    ApplicationCoordinate.DATA_TRANSFORMATION: (
        "Transforms data between representations — format conversion, "
        "normalization, enrichment, cleaning, or schema mapping"
    ),
    ApplicationCoordinate.DATA_VISUALIZATION: (
        "Renders data visually — charts, graphs, maps, heatmaps, "
        "tree-maps, or other graphical representations"
    ),
    ApplicationCoordinate.DATA_EXPORT: (
        "Exports data to external formats or destinations — PDF download, "
        "CSV export, API push, email attachment, or webhook delivery"
    ),
    ApplicationCoordinate.COMPUTATION_ON_DEMAND: (
        "Performs non-trivial computation triggered by a user action — "
        "statistical analysis, route planning, pricing, or ML inference"
    ),
    ApplicationCoordinate.BATCH_PROCESSING: (
        "Processes large volumes of items in bulk — mass email send, "
        "bulk image resize, nightly ETL, or report generation queues"
    ),
    ApplicationCoordinate.COMPARISON: (
        "Compares two or more entities side-by-side — diff views, "
        "product comparison tables, A/B test dashboards, or plan tiers"
    ),
    ApplicationCoordinate.AGGREGATION: (
        "Aggregates many records into summaries — totals, averages, "
        "leaderboards, roll-ups, or grouped statistics"
    ),
    ApplicationCoordinate.FORM_WORKFLOW: (
        "Guides users through structured multi-step input — wizards, "
        "onboarding flows, checkout processes, or application forms"
    ),
    ApplicationCoordinate.FILE_PROCESSING: (
        "Operates on uploaded files as first-class objects — image editing, "
        "document parsing, video transcoding, or archive extraction"
    ),
    ApplicationCoordinate.REAL_TIME_FEEDBACK: (
        "Provides immediate, continuous feedback as the user acts — "
        "live preview, syntax highlighting, spell-check, or auto-save"
    ),
    ApplicationCoordinate.COLLABORATIVE_EDITING: (
        "Allows multiple users to view or edit shared state concurrently — "
        "co-authoring documents, shared whiteboards, or pair programming"
    ),
    ApplicationCoordinate.SCHEDULING: (
        "Manages time-based coordination — booking appointments, "
        "assigning shifts, setting reminders, or finding mutual availability"
    ),
    ApplicationCoordinate.INVENTORY: (
        "Tracks a collection of discrete items and their status — "
        "stock levels, asset registers, library catalogues, or ticket pools"
    ),
    ApplicationCoordinate.MATCHING: (
        "Pairs or groups entities by compatibility criteria — job matching, "
        "roommate pairing, donor matching, or recommendation engines"
    ),
    ApplicationCoordinate.SIMULATION: (
        "Models hypothetical scenarios over time — financial projections, "
        "physics simulations, traffic models, or Monte Carlo analysis"
    ),
    ApplicationCoordinate.AUDIT_TRAIL: (
        "Records an immutable history of actions or changes — edit logs, "
        "compliance records, version history, or access logs"
    ),
    ApplicationCoordinate.CONSTRAINT_SATISFACTION: (
        "Finds solutions that satisfy a set of hard or soft constraints — "
        "timetable generation, seating plans, resource allocation, or diet planning"
    ),
    ApplicationCoordinate.STATIC_REPORT: (
        "Produces fixed-layout reports intended for print or archival — "
        "invoices, certificates, lab results, or regulatory filings"
    ),
    ApplicationCoordinate.INTERACTIVE_DASHBOARD: (
        "Presents a live, filterable overview of key metrics — KPI boards, "
        "ops dashboards, analytics consoles, or monitoring panels"
    ),
    ApplicationCoordinate.NOTIFICATION: (
        "Delivers alerts or updates through push, email, SMS, or in-app "
        "messages — reminders, status changes, threshold warnings, or digests"
    ),
    ApplicationCoordinate.API_PROVISION: (
        "Exposes programmatic endpoints for third-party consumption — "
        "REST/GraphQL APIs, webhooks, SDKs, or embeddable widgets"
    ),
}


# ---------------------------------------------------------------------------
# Examples (2–4 real-world apps per coordinate)
# ---------------------------------------------------------------------------

_EXAMPLES: dict[ApplicationCoordinate, list[str]] = {
    ApplicationCoordinate.DATA_INGESTION: [
        "GitHub (code import)",
        "YNAB (transaction import)",
        "Zapier (data connectors)",
        "Fivetran (warehouse ingestion)",
    ],
    ApplicationCoordinate.DATA_TRANSFORMATION: [
        "CloudConvert (file format conversion)",
        "dbt (SQL-based data transformation)",
        "OpenRefine (data cleaning)",
    ],
    ApplicationCoordinate.DATA_VISUALIZATION: [
        "Tableau Public (interactive charts)",
        "Observable (notebook visualizations)",
        "Google Earth (geospatial rendering)",
    ],
    ApplicationCoordinate.DATA_EXPORT: [
        "Google Takeout (data portability)",
        "Airtable (CSV / JSON export)",
        "QuickBooks (report PDF export)",
    ],
    ApplicationCoordinate.COMPUTATION_ON_DEMAND: [
        "Wolfram Alpha (symbolic computation)",
        "Google Maps (route calculation)",
        "TurboTax (tax computation)",
        "Desmos (graphing calculator)",
    ],
    ApplicationCoordinate.BATCH_PROCESSING: [
        "Mailchimp (bulk email campaigns)",
        "TinyPNG (bulk image compression)",
        "AWS Batch (cloud batch jobs)",
    ],
    ApplicationCoordinate.COMPARISON: [
        "GitHub (pull request diffs)",
        "Google Shopping (product comparison)",
        "Diffchecker (text comparison)",
        "PCPartPicker (component comparison)",
    ],
    ApplicationCoordinate.AGGREGATION: [
        "Google Analytics (traffic roll-ups)",
        "Mint (spending summaries)",
        "Stack Overflow (reputation scores)",
    ],
    ApplicationCoordinate.FORM_WORKFLOW: [
        "Typeform (multi-step surveys)",
        "TurboTax (guided tax filing)",
        "Shopify Checkout (purchase flow)",
        "Common App (college applications)",
    ],
    ApplicationCoordinate.FILE_PROCESSING: [
        "Canva (image editing)",
        "HandBrake (video transcoding)",
        "ILovePDF (PDF manipulation)",
        "Cloudinary (image CDN pipeline)",
    ],
    ApplicationCoordinate.REAL_TIME_FEEDBACK: [
        "Grammarly (live writing feedback)",
        "CodePen (live preview)",
        "Figma (instant design preview)",
    ],
    ApplicationCoordinate.COLLABORATIVE_EDITING: [
        "Google Docs (co-authoring)",
        "Figma (multiplayer design)",
        "Miro (shared whiteboards)",
        "Replit (collaborative coding)",
    ],
    ApplicationCoordinate.SCHEDULING: [
        "Calendly (appointment booking)",
        "Doodle (group polls)",
        "Acuity Scheduling (service booking)",
        "When2meet (availability finder)",
    ],
    ApplicationCoordinate.INVENTORY: [
        "Shopify (product inventory)",
        "Snipe-IT (asset management)",
        "LibraryThing (book cataloguing)",
    ],
    ApplicationCoordinate.MATCHING: [
        "LinkedIn (job-candidate matching)",
        "Tinder (people matching)",
        "Upwork (freelancer-project matching)",
        "DonorsChoose (donor-project matching)",
    ],
    ApplicationCoordinate.SIMULATION: [
        "Monte Carlo simulators (financial risk)",
        "PhET (physics simulations)",
        "SimCity / city-builder games",
        "Retool (workflow simulation)",
    ],
    ApplicationCoordinate.AUDIT_TRAIL: [
        "GitHub (commit history)",
        "Notion (page version history)",
        "Salesforce (field audit trail)",
    ],
    ApplicationCoordinate.CONSTRAINT_SATISFACTION: [
        "Google OR-Tools demos (scheduling solvers)",
        "Optaplanner (shift rostering)",
        "EatThisMuch (diet constraint planning)",
        "UniTime (university timetabling)",
    ],
    ApplicationCoordinate.STATIC_REPORT: [
        "Stripe (invoice PDFs)",
        "Jaspersoft (enterprise reports)",
        "Canva (certificate generation)",
    ],
    ApplicationCoordinate.INTERACTIVE_DASHBOARD: [
        "Grafana (ops monitoring)",
        "Datadog (infrastructure metrics)",
        "Mixpanel (product analytics)",
        "Metabase (business intelligence)",
    ],
    ApplicationCoordinate.NOTIFICATION: [
        "Slack (team notifications)",
        "PagerDuty (incident alerts)",
        "Twilio (SMS notifications)",
    ],
    ApplicationCoordinate.API_PROVISION: [
        "Stripe (payments API)",
        "Twilio (communications API)",
        "OpenAI (inference API)",
        "Mapbox (mapping API)",
    ],
}


# ---------------------------------------------------------------------------
# Semantic neighbours
# ---------------------------------------------------------------------------

_RELATED: dict[ApplicationCoordinate, list[ApplicationCoordinate]] = {
    ApplicationCoordinate.DATA_INGESTION: [
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.FILE_PROCESSING,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.API_PROVISION,
    ],
    ApplicationCoordinate.DATA_TRANSFORMATION: [
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
    ],
    ApplicationCoordinate.DATA_VISUALIZATION: [
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.STATIC_REPORT,
        ApplicationCoordinate.AGGREGATION,
    ],
    ApplicationCoordinate.DATA_EXPORT: [
        ApplicationCoordinate.STATIC_REPORT,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.API_PROVISION,
    ],
    ApplicationCoordinate.COMPUTATION_ON_DEMAND: [
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.SIMULATION,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    ],
    ApplicationCoordinate.BATCH_PROCESSING: [
        ApplicationCoordinate.DATA_TRANSFORMATION,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.FILE_PROCESSING,
    ],
    ApplicationCoordinate.COMPARISON: [
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.MATCHING,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
    ],
    ApplicationCoordinate.AGGREGATION: [
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.INTERACTIVE_DASHBOARD,
        ApplicationCoordinate.STATIC_REPORT,
        ApplicationCoordinate.COMPARISON,
    ],
    ApplicationCoordinate.FORM_WORKFLOW: [
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.NOTIFICATION,
    ],
    ApplicationCoordinate.FILE_PROCESSING: [
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.DATA_TRANSFORMATION,
    ],
    ApplicationCoordinate.REAL_TIME_FEEDBACK: [
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.DATA_VISUALIZATION,
    ],
    ApplicationCoordinate.COLLABORATIVE_EDITING: [
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.SCHEDULING,
    ],
    ApplicationCoordinate.SCHEDULING: [
        ApplicationCoordinate.NOTIFICATION,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.INVENTORY,
    ],
    ApplicationCoordinate.INVENTORY: [
        ApplicationCoordinate.AUDIT_TRAIL,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.DATA_EXPORT,
    ],
    ApplicationCoordinate.MATCHING: [
        ApplicationCoordinate.COMPARISON,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.NOTIFICATION,
    ],
    ApplicationCoordinate.SIMULATION: [
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.CONSTRAINT_SATISFACTION,
    ],
    ApplicationCoordinate.AUDIT_TRAIL: [
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.INVENTORY,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
        ApplicationCoordinate.STATIC_REPORT,
    ],
    ApplicationCoordinate.CONSTRAINT_SATISFACTION: [
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.MATCHING,
        ApplicationCoordinate.SIMULATION,
    ],
    ApplicationCoordinate.STATIC_REPORT: [
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.AUDIT_TRAIL,
    ],
    ApplicationCoordinate.INTERACTIVE_DASHBOARD: [
        ApplicationCoordinate.DATA_VISUALIZATION,
        ApplicationCoordinate.AGGREGATION,
        ApplicationCoordinate.REAL_TIME_FEEDBACK,
        ApplicationCoordinate.COMPARISON,
    ],
    ApplicationCoordinate.NOTIFICATION: [
        ApplicationCoordinate.SCHEDULING,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.FORM_WORKFLOW,
        ApplicationCoordinate.COLLABORATIVE_EDITING,
    ],
    ApplicationCoordinate.API_PROVISION: [
        ApplicationCoordinate.DATA_INGESTION,
        ApplicationCoordinate.DATA_EXPORT,
        ApplicationCoordinate.BATCH_PROCESSING,
        ApplicationCoordinate.COMPUTATION_ON_DEMAND,
    ],
}


# ---------------------------------------------------------------------------
# Commonly co-occurring coordinate pairs
# ---------------------------------------------------------------------------

_COMMONLY_COMBINED: list[tuple[ApplicationCoordinate, ApplicationCoordinate]] = [
    (ApplicationCoordinate.DATA_INGESTION, ApplicationCoordinate.DATA_TRANSFORMATION),
    (ApplicationCoordinate.DATA_INGESTION, ApplicationCoordinate.DATA_EXPORT),
    (ApplicationCoordinate.DATA_TRANSFORMATION, ApplicationCoordinate.DATA_VISUALIZATION),
    (ApplicationCoordinate.DATA_VISUALIZATION, ApplicationCoordinate.INTERACTIVE_DASHBOARD),
    (ApplicationCoordinate.DATA_VISUALIZATION, ApplicationCoordinate.STATIC_REPORT),
    (ApplicationCoordinate.AGGREGATION, ApplicationCoordinate.INTERACTIVE_DASHBOARD),
    (ApplicationCoordinate.AGGREGATION, ApplicationCoordinate.DATA_VISUALIZATION),
    (ApplicationCoordinate.FORM_WORKFLOW, ApplicationCoordinate.AUDIT_TRAIL),
    (ApplicationCoordinate.FORM_WORKFLOW, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.SCHEDULING, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.SCHEDULING, ApplicationCoordinate.COLLABORATIVE_EDITING),
    (ApplicationCoordinate.INVENTORY, ApplicationCoordinate.AUDIT_TRAIL),
    (ApplicationCoordinate.COLLABORATIVE_EDITING, ApplicationCoordinate.REAL_TIME_FEEDBACK),
    (ApplicationCoordinate.BATCH_PROCESSING, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.COMPUTATION_ON_DEMAND, ApplicationCoordinate.REAL_TIME_FEEDBACK),
    (ApplicationCoordinate.MATCHING, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.API_PROVISION, ApplicationCoordinate.DATA_INGESTION),
    (ApplicationCoordinate.CONSTRAINT_SATISFACTION, ApplicationCoordinate.SCHEDULING),
    (ApplicationCoordinate.DATA_EXPORT, ApplicationCoordinate.STATIC_REPORT),
]


# ---------------------------------------------------------------------------
# Rarely co-occurring coordinate pairs — high novelty potential
# ---------------------------------------------------------------------------

_RARELY_COMBINED: list[tuple[ApplicationCoordinate, ApplicationCoordinate]] = [
    (ApplicationCoordinate.SIMULATION, ApplicationCoordinate.AUDIT_TRAIL),
    (ApplicationCoordinate.CONSTRAINT_SATISFACTION, ApplicationCoordinate.COLLABORATIVE_EDITING),
    (ApplicationCoordinate.MATCHING, ApplicationCoordinate.REAL_TIME_FEEDBACK),
    (ApplicationCoordinate.SIMULATION, ApplicationCoordinate.FORM_WORKFLOW),
    (ApplicationCoordinate.BATCH_PROCESSING, ApplicationCoordinate.COLLABORATIVE_EDITING),
    (ApplicationCoordinate.INVENTORY, ApplicationCoordinate.SIMULATION),
    (ApplicationCoordinate.FILE_PROCESSING, ApplicationCoordinate.SCHEDULING),
    (ApplicationCoordinate.AUDIT_TRAIL, ApplicationCoordinate.DATA_VISUALIZATION),
    (ApplicationCoordinate.CONSTRAINT_SATISFACTION, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.STATIC_REPORT, ApplicationCoordinate.REAL_TIME_FEEDBACK),
    (ApplicationCoordinate.API_PROVISION, ApplicationCoordinate.COLLABORATIVE_EDITING),
    (ApplicationCoordinate.COMPARISON, ApplicationCoordinate.SCHEDULING),
    (ApplicationCoordinate.MATCHING, ApplicationCoordinate.FILE_PROCESSING),
    (ApplicationCoordinate.SIMULATION, ApplicationCoordinate.NOTIFICATION),
    (ApplicationCoordinate.AGGREGATION, ApplicationCoordinate.CONSTRAINT_SATISFACTION),
    (ApplicationCoordinate.BATCH_PROCESSING, ApplicationCoordinate.COMPARISON),
    (ApplicationCoordinate.DATA_EXPORT, ApplicationCoordinate.MATCHING),
    (ApplicationCoordinate.INVENTORY, ApplicationCoordinate.REAL_TIME_FEEDBACK),
]


# =========================================================================
# Main class
# =========================================================================


class ApplicationCoordinateSpace:
    """Manages the 22-dimensional application coordinate space.

    Every web application can be described by the subset of
    ``ApplicationCoordinate`` values it occupies.  This class exposes
    metadata about each coordinate and geometric operations over the
    space (distance, combinations, neighbourhood queries).
    """

    # ----- catalogue queries -------------------------------------------

    def all_coordinates(self) -> list[ApplicationCoordinate]:
        """Return all 22 ``ApplicationCoordinate`` values."""
        return list(ApplicationCoordinate)

    @property
    def dimension(self) -> int:
        """Dimensionality of the space (number of coordinates)."""
        return len(ApplicationCoordinate)

    def coordinate_description(self, coord: ApplicationCoordinate) -> str:
        """Human-readable description for *coord*.

        Raises ``KeyError`` if *coord* is not a valid coordinate.
        """
        return _DESCRIPTIONS[coord]

    def coordinate_examples(self, coord: ApplicationCoordinate) -> list[str]:
        """Return 2–4 real-world application examples for *coord*."""
        return list(_EXAMPLES[coord])

    def related_coordinates(
        self, coord: ApplicationCoordinate
    ) -> list[ApplicationCoordinate]:
        """Semantically related coordinates for *coord*.

        Returns a list of 3–4 coordinates that naturally appear alongside
        *coord* in typical application designs.
        """
        return list(_RELATED[coord])

    def describe_all(self) -> dict[ApplicationCoordinate, dict[str, Any]]:
        """Return a mapping of every coordinate to its full metadata.

        Each value is a dict with keys ``"description"``, ``"examples"``,
        and ``"related"``.
        """
        return {
            coord: {
                "description": self.coordinate_description(coord),
                "examples": self.coordinate_examples(coord),
                "related": self.related_coordinates(coord),
            }
            for coord in ApplicationCoordinate
        }

    # ----- geometry ----------------------------------------------------

    @staticmethod
    def distance(c1: set[ApplicationCoordinate], c2: set[ApplicationCoordinate]) -> float:
        """Jaccard distance between two coordinate sets.

        .. math::

            d(c_1, c_2) = 1 - \\frac{|c_1 \\cap c_2|}{|c_1 \\cup c_2|}

        Returns ``1.0`` when both sets are empty (maximally uninformative).
        """
        if not c1 and not c2:
            return 1.0
        intersection = len(c1 & c2)
        union = len(c1 | c2)
        return 1.0 - intersection / union

    @staticmethod
    def overlap(c1: set[ApplicationCoordinate], c2: set[ApplicationCoordinate]) -> float:
        """Overlap coefficient (Szymkiewicz–Simpson).

        .. math::

            \\text{overlap}(c_1, c_2) = \\frac{|c_1 \\cap c_2|}{\\min(|c_1|, |c_2|)}

        Returns ``0.0`` when either set is empty.
        """
        if not c1 or not c2:
            return 0.0
        return len(c1 & c2) / min(len(c1), len(c2))

    @staticmethod
    def hamming(c1: set[ApplicationCoordinate], c2: set[ApplicationCoordinate]) -> int:
        """Hamming distance in the binary-vector view of the space.

        Counts the number of coordinates where the two sets differ
        (present in one but not the other).
        """
        return len(c1.symmetric_difference(c2))

    # ----- combinatorics -----------------------------------------------

    def pairwise_combinations(self, k: int) -> list[tuple[ApplicationCoordinate, ...]]:
        """All *k*-element subsets of the 22 coordinates.

        Parameters
        ----------
        k : int
            Size of each combination.  Must satisfy ``0 <= k <= 22``.

        Returns
        -------
        list[tuple[ApplicationCoordinate, ...]]
            Sorted list of tuples, one per combination.

        Raises
        ------
        ValueError
            If *k* is negative or larger than the number of coordinates.
        """
        n = len(ApplicationCoordinate)
        if k < 0 or k > n:
            raise ValueError(
                f"k must be in [0, {n}], got {k}"
            )
        return list(combinations(ApplicationCoordinate, k))

    def commonly_combined(
        self,
    ) -> list[tuple[ApplicationCoordinate, ApplicationCoordinate]]:
        """Coordinate pairs frequently found together in real-world apps.

        These represent well-trodden design patterns (e.g. ingestion +
        transformation, scheduling + notification).  Returns 15-20 pairs.
        """
        return list(_COMMONLY_COMBINED)

    def rarely_combined(
        self,
    ) -> list[tuple[ApplicationCoordinate, ApplicationCoordinate]]:
        """Coordinate pairs that *could* combine but seldom do.

        These pairings represent high-novelty opportunity: each one is
        technically feasible but under-explored in existing products.
        Returns 15-20 pairs.
        """
        return list(_RARELY_COMBINED)

    # ----- neighbourhood / similarity ----------------------------------

    def neighbours(
        self,
        coords: set[ApplicationCoordinate],
        *,
        max_distance: float = 0.5,
    ) -> list[tuple[set[ApplicationCoordinate], float]]:
        """Find commonly-combined pairs whose union is close to *coords*.

        Iterates over ``commonly_combined`` pairs, forms the 2-element
        set for each, and keeps those within *max_distance* (Jaccard) of
        the given *coords* set.

        Returns a list of ``(pair_set, distance)`` sorted by distance.
        """
        results: list[tuple[set[ApplicationCoordinate], float]] = []
        for a, b in _COMMONLY_COMBINED:
            pair = {a, b}
            d = self.distance(coords, pair)
            if d <= max_distance:
                results.append((pair, d))
        results.sort(key=lambda t: t[1])
        return results

    def novelty_score(self, coords: set[ApplicationCoordinate]) -> float:
        """Estimate how novel a coordinate set is.

        The score is the fraction of the application's coordinate pairs
        that appear in the ``rarely_combined`` list, normalised to [0, 1].

        A higher score means the application's coordinate mix is more
        unusual relative to existing products.  Returns ``0.0`` for sets
        with fewer than two coordinates.
        """
        if len(coords) < 2:
            return 0.0
        rare = set(_RARELY_COMBINED)
        app_pairs = list(combinations(coords, 2))
        hits = sum(
            1
            for pair in app_pairs
            if pair in rare or (pair[1], pair[0]) in rare
        )
        return hits / len(app_pairs)

    def feasibility_score(self, coords: set[ApplicationCoordinate]) -> float:
        """Estimate how feasible a coordinate set is.

        The score is the fraction of the application's coordinate pairs
        that appear in the ``commonly_combined`` list, normalised to [0, 1].

        A higher score means the combination is well-precedented.
        Returns ``0.0`` for sets with fewer than two coordinates.
        """
        if len(coords) < 2:
            return 0.0
        common = set(_COMMONLY_COMBINED)
        app_pairs = list(combinations(coords, 2))
        hits = sum(
            1
            for pair in app_pairs
            if pair in common or (pair[1], pair[0]) in common
        )
        return hits / len(app_pairs)

    # ----- text helpers ------------------------------------------------

    def summarise(self, coords: set[ApplicationCoordinate]) -> str:
        """One-line summary of a coordinate set for display.

        Example: ``"3-coord app: DATA_INGESTION, MATCHING, NOTIFICATION"``
        """
        names = sorted(c.name for c in coords)
        return f"{len(names)}-coord app: {', '.join(names)}"

    def detail(self, coords: set[ApplicationCoordinate]) -> str:
        """Multi-line detail string for a coordinate set.

        Includes each coordinate's description and novelty / feasibility
        scores for the combination.
        """
        lines: list[str] = [self.summarise(coords), ""]
        for coord in sorted(coords, key=lambda c: c.name):
            lines.append(f"  {coord.name}")
            lines.append(f"    {self.coordinate_description(coord)}")
        lines.append("")
        lines.append(f"  novelty:     {self.novelty_score(coords):.2f}")
        lines.append(f"  feasibility: {self.feasibility_score(coords):.2f}")
        return "\n".join(lines)

    # ----- dunder ------------------------------------------------------

    def __repr__(self) -> str:
        return f"ApplicationCoordinateSpace(dim={self.dimension})"


# =========================================================================
# Module-level singleton
# =========================================================================

COORD_SPACE = ApplicationCoordinateSpace()
