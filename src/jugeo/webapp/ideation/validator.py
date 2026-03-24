"""Validator for web-application idea proposals.

Stage 5 of the ideation pipeline: validates candidate ideas against an
existing portfolio, extracts demand signals, identifies obstacles, finds
partial solutions, and produces a confidence-scored recommendation.

Standalone module — Python stdlib only (no jugeo-internal imports beyond the
sibling models module).
"""
from __future__ import annotations

import math
import re
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    ExistingApp,
    IdeaPortfolio,
    IdeaProposal,
    ValidationResult,
    ValidationStatus,
)

AC = ApplicationCoordinate

# ---------------------------------------------------------------------------
# Demand signals by coordinate — evidence of real-world user interest
# ---------------------------------------------------------------------------
COORD_DEMAND_SIGNALS: dict[AC, list[str]] = {
    AC.SCHEDULING: [
        "Strong demand: Calendly achieved $3B valuation",
        "Scheduling apps consistently top Product Hunt launches",
        "Reddit r/productivity frequently requests better scheduling tools",
        "Google Calendar API has 500M+ monthly calls",
    ],
    AC.DATA_VISUALIZATION: [
        "Tableau IPO at $3B valuation",
        "Observable notebooks growing 40% YoY",
        "GitHub stars for D3.js, Chart.js, Plotly consistently high",
    ],
    AC.DATA_INGESTION: [
        "Fivetran valued at $5.6B for data-connector platform",
        "Airbyte open-source ingestion sees rapid adoption",
        "Enterprise ETL market projected $20B by 2026",
    ],
    AC.DATA_TRANSFORMATION: [
        "dbt Labs valued at $4.2B for transformation layer",
        "Pandas downloaded 100M+ times on PyPI",
        "Data-wrangling tools rank #1 unmet need in Kaggle surveys",
    ],
    AC.DATA_EXPORT: [
        "CSV/Excel export is the #1 requested feature in SaaS surveys",
        "Zapier export integrations used by 6M+ workflows",
    ],
    AC.COMPUTATION_ON_DEMAND: [
        "AWS Lambda processes 100T+ invocations/month",
        "Serverless computing market growing 25% CAGR",
        "Wolfram Alpha handles 1B+ queries annually",
    ],
    AC.BATCH_PROCESSING: [
        "Apache Spark ecosystem exceeds $4B market value",
        "Airflow has 30k+ GitHub stars for workflow orchestration",
    ],
    AC.COMPARISON: [
        "G2/Capterra comparison sites generate $500M+ revenue",
        "Consumer comparison shopping is a $6B market",
        "Product-comparison browser extensions see 10M+ installs",
    ],
    AC.AGGREGATION: [
        "News aggregator market valued at $8B",
        "RSS reader revival (Feedly 15M+ users)",
        "Data-aggregation APIs top the Rapid API marketplace",
    ],
    AC.FORM_WORKFLOW: [
        "Typeform and JotForm collectively serve 20M+ users",
        "Form-builder market projected $15B by 2027",
        "Low-code workflow tools growing 30% CAGR",
    ],
    AC.FILE_PROCESSING: [
        "PDF processing tools rank in top-10 utility apps",
        "Smallpdf serves 40M+ users monthly",
        "Image/video conversion tools are a $3B market",
    ],
    AC.REAL_TIME_FEEDBACK: [
        "Live-poll tools used in 80% of large conferences",
        "Real-time analytics dashboards a top-3 enterprise priority",
        "WebSocket adoption up 60% YoY in SaaS products",
    ],
    AC.COLLABORATIVE_EDITING: [
        "Google Docs has 1B+ monthly users",
        "Notion valued at $10B with collaborative-first approach",
        "Figma acquired for $20B driven by real-time collaboration",
        "Miro whiteboard valued at $17.5B",
    ],
    AC.INVENTORY: [
        "Inventory management software market at $3B",
        "Shopify inventory tools used by 4M+ merchants",
        "Supply-chain visibility a top CEO priority post-pandemic",
    ],
    AC.MATCHING: [
        "Job-matching platforms (LinkedIn, Indeed) serve 800M+ users",
        "Dating-app matching algorithms drive a $9B market",
        "Marketplace matching (Uber, Airbnb) are $100B+ businesses",
    ],
    AC.SIMULATION: [
        "Simulation software market projected $25B by 2028",
        "Monte Carlo tools increasingly used in fintech planning",
    ],
    AC.AUDIT_TRAIL: [
        "Compliance software market growing at 14% CAGR",
        "SOC-2 / GDPR audit requirements expanding globally",
        "Blockchain-based audit trails attracting VC funding",
    ],
    AC.CONSTRAINT_SATISFACTION: [
        "Operations research tools market at $2B",
        "Solver-based scheduling in high demand (nurse rostering, etc.)",
    ],
    AC.STATIC_REPORT: [
        "Business-intelligence reporting market exceeds $30B",
        "PDF report generation a core feature of 70% of SaaS tools",
    ],
    AC.INTERACTIVE_DASHBOARD: [
        "Looker acquired by Google for $2.6B",
        "Grafana open-source dashboards used by 800K+ orgs",
        "Dashboard fatigue is real — better dashboards still in demand",
        "Metabase community edition has 35k+ GitHub stars",
    ],
    AC.NOTIFICATION: [
        "Push-notification platforms (OneSignal, Pusher) serve 1M+ apps",
        "Notification overload drives demand for smart filtering",
        "Twilio (SMS/email notifications) valued at $10B+",
    ],
    AC.API_PROVISION: [
        "API-management market projected $10B by 2027",
        "Postman has 25M+ developers on its platform",
        "API-first design is the dominant SaaS architecture trend",
    ],
}

# ---------------------------------------------------------------------------
# Known obstacles by coordinate — common implementation challenges
# ---------------------------------------------------------------------------
COORD_OBSTACLES: dict[AC, list[str]] = {
    AC.COLLABORATIVE_EDITING: [
        "Requires WebSocket infrastructure (Flask-SocketIO complexity)",
        "Conflict resolution is algorithmically hard (OT / CRDT needed)",
        "Operational transformation patents may restrict approaches",
        "Presence awareness adds significant state-management overhead",
    ],
    AC.REAL_TIME_FEEDBACK: [
        "WebSocket state management complexity",
        "Server-side event handling and back-pressure overhead",
        "Horizontal scaling of stateful connections is non-trivial",
    ],
    AC.SCHEDULING: [
        "Calendar integration requires OAuth with multiple providers",
        "Timezone handling is a persistent source of bugs",
        "Recurring-event logic is surprisingly complex (RFC 5545)",
    ],
    AC.DATA_INGESTION: [
        "Connector maintenance burden grows linearly with sources",
        "Schema evolution / drift handling is fragile",
        "Rate-limiting and retry logic differ per upstream API",
    ],
    AC.DATA_TRANSFORMATION: [
        "Correctness verification of arbitrary transforms is hard",
        "Large-dataset memory pressure requires streaming design",
    ],
    AC.MATCHING: [
        "Cold-start problem with new users / items",
        "Fairness and bias in matching algorithms under scrutiny",
        "Two-sided marketplace incentive alignment is subtle",
    ],
    AC.SIMULATION: [
        "Computational cost grows non-linearly with fidelity",
        "Validation of simulation accuracy is domain-specific",
        "Stochastic simulations need careful random-seed management",
    ],
    AC.CONSTRAINT_SATISFACTION: [
        "NP-hard in the general case — must scope carefully",
        "Solver integration (OR-Tools, MiniZinc) adds deployment weight",
        "User-facing constraint specification UX is difficult",
    ],
    AC.INVENTORY: [
        "Consistency across warehouses requires distributed transactions",
        "Barcode / SKU standardisation varies by industry",
    ],
    AC.AUDIT_TRAIL: [
        "Immutable-log storage costs can escalate quickly",
        "Regulatory requirements differ by jurisdiction",
        "Tamper-evidence mechanisms add complexity",
    ],
    AC.NOTIFICATION: [
        "Deliverability across email, SMS, push is provider-dependent",
        "Notification fatigue leads to user churn if poorly managed",
    ],
    AC.COMPUTATION_ON_DEMAND: [
        "Cold-start latency in serverless functions",
        "Execution sandboxing for untrusted code is security-critical",
    ],
    AC.INTERACTIVE_DASHBOARD: [
        "Query performance degrades with dashboard complexity",
        "Caching invalidation for live data is error-prone",
        "Responsive layout of complex dashboards is hard",
    ],
    AC.FILE_PROCESSING: [
        "Malicious-file detection is a security requirement",
        "Large-file upload / streaming needs chunked transfer",
    ],
    AC.API_PROVISION: [
        "Rate-limiting and quota enforcement add infra cost",
        "Versioning strategy must be decided early and is hard to change",
        "Authentication (API keys, OAuth) must be secure from day one",
    ],
}

# Similarity threshold for the "already exists" duplicate check
EXISTS_SIMILARITY_THRESHOLD = 0.75

# Coordinates considered "sticky" (high user retention)
_STICKY_COORDS: frozenset[AC] = frozenset({
    AC.AUDIT_TRAIL,
    AC.INVENTORY,
    AC.COLLABORATIVE_EDITING,
    AC.SCHEDULING,
    AC.FORM_WORKFLOW,
    AC.INTERACTIVE_DASHBOARD,
})

# Coordinates associated with low retention / one-off usage
_LOW_RETENTION_COORDS: frozenset[AC] = frozenset({
    AC.COMPUTATION_ON_DEMAND,
    AC.COMPARISON,
    AC.STATIC_REPORT,
    AC.DATA_EXPORT,
    AC.FILE_PROCESSING,
})

# Severity keywords used to classify obstacle severity
_SEVERE_OBSTACLE_KEYWORDS: tuple[str, ...] = (
    "hard",
    "NP-hard",
    "non-trivial",
    "security-critical",
    "complex",
    "complexity",
    "patents",
)


# ===================================================================
# AppIdeaValidator
# ===================================================================
class AppIdeaValidator:
    """Stage 5: validates idea candidates against an existing portfolio.

    Lifecycle of a single validation::

        1. Check if already exists in portfolio  → ALREADY_EXISTS
        2. Early-reject if feasibility is too low → INFEASIBLE
        3. Extract demand signals
        4. Extract obstacles
        5. Find partial solutions
        6. Compute confidence
        7. Classify status and generate recommendation
    """

    # ---- public API -------------------------------------------------------

    def validate(
        self,
        candidate: IdeaProposal,
        portfolio: IdeaPortfolio,
    ) -> ValidationResult:
        """Validate a single candidate idea against *portfolio*."""

        # 1. Duplicate check
        exists, similarity = self._check_already_exists(candidate, portfolio)
        if exists:
            return ValidationResult(
                status=ValidationStatus.ALREADY_EXISTS,
                confidence=similarity,
                demand_signals=[],
                known_obstacles=[],
                partial_solutions=[],
                recommendation=(
                    f"'{candidate.title}' already exists in the portfolio "
                    f"(similarity {similarity:.0%}). Consider differentiating "
                    "on a unique coordinate or targeting a distinct user segment."
                ),
            )

        # 2. Feasibility gate
        if candidate.feasibility_score < 0.2:
            return ValidationResult(
                status=ValidationStatus.INFEASIBLE,
                confidence=0.9,
                demand_signals=[],
                known_obstacles=["Feasibility score below threshold (< 0.2)"],
                partial_solutions=[],
                recommendation=(
                    f"'{candidate.title}' has a feasibility score of "
                    f"{candidate.feasibility_score:.2f}, which is below the "
                    "minimum threshold. Revisit technical assumptions before "
                    "proceeding."
                ),
            )

        # 3-5. Signals, obstacles, partials
        demand = self._extract_demand_signals(candidate)
        obstacles = self._extract_obstacles(candidate)
        partials = self._find_partial_solutions(candidate, portfolio)

        # 6. Confidence
        confidence = self._compute_confidence(demand, obstacles, partials)

        # 7. Status classification
        severe_count = self._count_severe_obstacles(obstacles)
        if severe_count >= 3:
            status = ValidationStatus.OBSTACLE_FOUND
        elif confidence > 0.6:
            status = ValidationStatus.VALIDATED
        else:
            status = ValidationStatus.UNCERTAIN

        recommendation = self._generate_recommendation(
            status, candidate, demand, obstacles,
        )

        return ValidationResult(
            status=status,
            confidence=confidence,
            demand_signals=demand,
            known_obstacles=obstacles,
            partial_solutions=partials,
            recommendation=recommendation,
        )

    def batch_validate(
        self,
        candidates: list[IdeaProposal],
        portfolio: IdeaPortfolio,
    ) -> list[tuple[IdeaProposal, ValidationResult]]:
        """Validate every candidate and return *(candidate, result)* pairs.

        Results are returned in the same order as *candidates*, with
        ``VALIDATED`` items sorted to the front within each feasibility tier.
        """
        raw: list[tuple[IdeaProposal, ValidationResult]] = [
            (c, self.validate(c, portfolio)) for c in candidates
        ]
        # Stable sort: VALIDATED first, then UNCERTAIN, then the rest
        _STATUS_ORDER = {
            ValidationStatus.VALIDATED: 0,
            ValidationStatus.UNCERTAIN: 1,
            ValidationStatus.OBSTACLE_FOUND: 2,
            ValidationStatus.ALREADY_EXISTS: 3,
            ValidationStatus.INFEASIBLE: 4,
        }
        raw.sort(key=lambda pair: _STATUS_ORDER.get(pair[1].status, 9))
        return raw

    # ---- internal helpers -------------------------------------------------

    def _check_already_exists(
        self,
        candidate: IdeaProposal,
        portfolio: IdeaPortfolio,
    ) -> tuple[bool, float]:
        """Return *(exists, max_similarity)*.

        Similarity is the weighted combination of Jaccard coordinate overlap
        (70 %) and title-word overlap (30 %).
        """
        if not portfolio.ideas:
            return False, 0.0

        max_sim = 0.0
        cand_coords = set(candidate.coordinates)
        cand_words = _normalise_words(candidate.title)

        for app in portfolio.ideas:
            app_coords = set(app.coordinates)
            coord_sim = _jaccard(cand_coords, app_coords)
            word_sim = _jaccard(cand_words, _normalise_words(app.name))
            combined = 0.70 * coord_sim + 0.30 * word_sim
            if combined > max_sim:
                max_sim = combined

        return (max_sim > EXISTS_SIMILARITY_THRESHOLD, max_sim)

    def _extract_demand_signals(
        self, candidate: IdeaProposal,
    ) -> list[str]:
        """Collect unique demand signals for *candidate*'s coordinates."""
        seen: set[str] = set()
        signals: list[str] = []
        for coord in candidate.coordinates:
            for sig in COORD_DEMAND_SIGNALS.get(coord, []):
                if sig not in seen:
                    seen.add(sig)
                    signals.append(sig)

        # Generic signals derived from target_area keywords
        area = (candidate.target_area or "").lower()
        generic = _generic_demand_signals(area)
        for sig in generic:
            if sig not in seen:
                seen.add(sig)
                signals.append(sig)

        return signals

    def _extract_obstacles(self, candidate: IdeaProposal) -> list[str]:
        """Collect unique obstacles for *candidate*'s coordinates."""
        seen: set[str] = set()
        obstacles: list[str] = []
        for coord in candidate.coordinates:
            for obs in COORD_OBSTACLES.get(coord, []):
                if obs not in seen:
                    seen.add(obs)
                    obstacles.append(obs)

        # Complexity penalty for multi-coordinate ideas
        n = len(candidate.coordinates)
        if n >= 5:
            msg = (
                f"Integration complexity: idea spans {n} coordinates; "
                "cross-cutting concerns multiply testing effort"
            )
            if msg not in seen:
                obstacles.append(msg)
        elif n >= 3:
            msg = (
                f"Moderate integration scope: {n} coordinates require "
                "careful interface design"
            )
            if msg not in seen:
                obstacles.append(msg)

        return obstacles

    def _find_partial_solutions(
        self,
        candidate: IdeaProposal,
        portfolio: IdeaPortfolio,
    ) -> list[str]:
        """Find portfolio apps that partially cover the candidate's space."""
        cand_coords = set(candidate.coordinates)
        if not cand_coords:
            return []

        partials: list[tuple[int, str]] = []
        for app in portfolio.ideas:
            app_coords = set(app.coordinates)
            overlap = cand_coords & app_coords
            if 1 <= len(overlap) < len(cand_coords):
                overlap_names = sorted(c.value for c in overlap)
                partials.append((
                    len(overlap),
                    f"{app.name} covers {', '.join(overlap_names)}",
                ))

        # Sort descending by overlap size, keep top 5
        partials.sort(key=lambda t: t[0], reverse=True)
        return [text for _, text in partials[:5]]

    def _compute_confidence(
        self,
        demand: list[str],
        obstacles: list[str],
        partials: list[str],
    ) -> float:
        """Return a confidence score in [0.05, 0.95]."""
        conf = 0.5
        conf += min(len(demand) * 0.05, 0.3)
        severe = self._count_severe_obstacles(obstacles)
        conf -= min(severe * 0.10, 0.3)
        conf += min(len(partials) * 0.02, 0.10)
        return max(0.05, min(0.95, conf))

    @staticmethod
    def _count_severe_obstacles(obstacles: list[str]) -> int:
        """Count obstacles that contain severity-indicating keywords."""
        count = 0
        for obs in obstacles:
            lower = obs.lower()
            if any(kw.lower() in lower for kw in _SEVERE_OBSTACLE_KEYWORDS):
                count += 1
        return count

    def _generate_recommendation(
        self,
        status: ValidationStatus,
        candidate: IdeaProposal,
        demand: list[str],
        obstacles: list[str],
    ) -> str:
        """Produce a human-readable recommendation paragraph."""
        title = candidate.title

        if status is ValidationStatus.VALIDATED:
            top_signals = "; ".join(demand[:2]) if demand else "general market interest"
            return (
                f"'{title}' is validated with strong demand signals "
                f"({top_signals}). Recommended next step: build a minimal "
                "prototype targeting the primary coordinate and validate with "
                "five early users."
            )

        if status is ValidationStatus.OBSTACLE_FOUND:
            top_obstacles = "; ".join(obstacles[:2]) if obstacles else "multiple concerns"
            return (
                f"'{title}' faces significant obstacles ({top_obstacles}). "
                "Consider de-scoping to fewer coordinates or finding an "
                "existing open-source component that addresses the hardest "
                "technical challenge before committing resources."
            )

        # UNCERTAIN
        return (
            f"'{title}' shows mixed signals. There are "
            f"{len(demand)} demand indicator(s) but also "
            f"{len(obstacles)} obstacle(s). Recommend conducting "
            "five user-discovery interviews and a focused technical "
            "spike before deciding to proceed."
        )


# ===================================================================
# DemandSignalAnalyzer
# ===================================================================
class DemandSignalAnalyzer:
    """Analyzes demand signals for coordinate combinations.

    Provides three estimates:

    * **demand** — normalised [0, 1] fraction of maximum possible demand.
    * **user_base** — estimated monthly active users (integer).
    * **retention** — expected 30-day retention rate [0, 1].
    """

    # Base demand estimates (thousands of monthly active users)
    COORD_DEMAND_ESTIMATES: dict[AC, int] = {
        AC.SCHEDULING: 500,
        AC.DATA_VISUALIZATION: 800,
        AC.FORM_WORKFLOW: 600,
        AC.DATA_INGESTION: 400,
        AC.INTERACTIVE_DASHBOARD: 700,
        AC.MATCHING: 450,
        AC.COMPUTATION_ON_DEMAND: 350,
        AC.INVENTORY: 300,
        AC.AUDIT_TRAIL: 250,
        AC.NOTIFICATION: 400,
        AC.BATCH_PROCESSING: 200,
        AC.COMPARISON: 300,
        AC.AGGREGATION: 350,
        AC.DATA_TRANSFORMATION: 300,
        AC.DATA_EXPORT: 250,
        AC.FILE_PROCESSING: 350,
        AC.REAL_TIME_FEEDBACK: 400,
        AC.COLLABORATIVE_EDITING: 600,
        AC.SIMULATION: 150,
        AC.CONSTRAINT_SATISFACTION: 120,
        AC.STATIC_REPORT: 200,
        AC.API_PROVISION: 300,
    }

    # Retention scores per coordinate (higher → stickier)
    _RETENTION_SCORES: dict[AC, float] = {
        AC.AUDIT_TRAIL: 0.85,
        AC.INVENTORY: 0.82,
        AC.COLLABORATIVE_EDITING: 0.80,
        AC.SCHEDULING: 0.78,
        AC.FORM_WORKFLOW: 0.75,
        AC.INTERACTIVE_DASHBOARD: 0.74,
        AC.DATA_INGESTION: 0.70,
        AC.NOTIFICATION: 0.68,
        AC.MATCHING: 0.65,
        AC.REAL_TIME_FEEDBACK: 0.62,
        AC.DATA_TRANSFORMATION: 0.60,
        AC.AGGREGATION: 0.58,
        AC.BATCH_PROCESSING: 0.55,
        AC.API_PROVISION: 0.55,
        AC.SIMULATION: 0.50,
        AC.CONSTRAINT_SATISFACTION: 0.48,
        AC.DATA_VISUALIZATION: 0.45,
        AC.DATA_EXPORT: 0.40,
        AC.FILE_PROCESSING: 0.38,
        AC.COMPUTATION_ON_DEMAND: 0.35,
        AC.COMPARISON: 0.32,
        AC.STATIC_REPORT: 0.30,
    }

    # Purpose population-size multipliers
    _POPULATION_MULTIPLIERS: dict[str, float] = {
        "consumer": 10.0,
        "prosumer": 5.0,
        "smb": 2.0,
        "enterprise": 0.5,
        "developer": 1.5,
        "internal": 0.3,
    }

    # Domain relevance keywords mapped to coordinates
    _DOMAIN_KEYWORDS: dict[str, set[AC]] = {
        "finance": {AC.AUDIT_TRAIL, AC.COMPUTATION_ON_DEMAND, AC.STATIC_REPORT},
        "healthcare": {AC.SCHEDULING, AC.FORM_WORKFLOW, AC.AUDIT_TRAIL},
        "education": {AC.COLLABORATIVE_EDITING, AC.SCHEDULING, AC.REAL_TIME_FEEDBACK},
        "ecommerce": {AC.INVENTORY, AC.MATCHING, AC.NOTIFICATION},
        "logistics": {AC.SCHEDULING, AC.CONSTRAINT_SATISFACTION, AC.INVENTORY},
        "analytics": {AC.DATA_VISUALIZATION, AC.INTERACTIVE_DASHBOARD, AC.AGGREGATION},
        "marketing": {AC.DATA_VISUALIZATION, AC.NOTIFICATION, AC.FORM_WORKFLOW},
        "devtools": {AC.API_PROVISION, AC.DATA_TRANSFORMATION, AC.BATCH_PROCESSING},
    }

    def estimate_demand(
        self,
        coordinates: set[AC],
        purpose: AppIdeationPurpose,
    ) -> float:
        """Estimate normalised demand in [0, 1].

        Uses a bottleneck model: the intersection demand is limited by the
        lowest individual coordinate demand, then normalised by the maximum
        demand estimate and adjusted by domain relevance.
        """
        if not coordinates:
            return 0.0

        estimates = [
            self.COORD_DEMAND_ESTIMATES.get(c, 100) for c in coordinates
        ]
        max_possible = max(self.COORD_DEMAND_ESTIMATES.values())
        bottleneck = min(estimates)
        raw = bottleneck / max_possible  # [0, 1]

        relevance = self._domain_relevance(coordinates, purpose)
        return max(0.0, min(1.0, raw * relevance))

    def estimate_user_base(
        self,
        coordinates: set[AC],
        purpose: AppIdeationPurpose,
    ) -> int:
        """Estimate monthly active users for a coordinate combination.

        Geometric mean of individual demand estimates (in thousands),
        multiplied by population-type multiplier, returned as an integer.
        """
        if not coordinates:
            return 0

        estimates = [
            self.COORD_DEMAND_ESTIMATES.get(c, 100) for c in coordinates
        ]
        log_sum = sum(math.log(e) for e in estimates)
        geo_mean = math.exp(log_sum / len(estimates))  # in thousands

        pop = (purpose.user_population or "").lower().strip()
        multiplier = self._POPULATION_MULTIPLIERS.get(pop, 1.0)

        return int(geo_mean * multiplier * 1000)

    def estimate_retention(self, coordinates: set[AC]) -> float:
        """Estimate 30-day retention as mean stickiness score in [0, 1]."""
        if not coordinates:
            return 0.0

        scores = [
            self._RETENTION_SCORES.get(c, 0.50) for c in coordinates
        ]
        return sum(scores) / len(scores)

    # ---- helpers ----------------------------------------------------------

    def _domain_relevance(
        self,
        coordinates: set[AC],
        purpose: AppIdeationPurpose,
    ) -> float:
        """Return a multiplier in [0.5, 1.5] based on domain-keyword overlap.

        If the purpose domain matches known keyword families that align with
        the candidate coordinates, boost relevance; otherwise neutral.
        """
        domain_lower = (purpose.domain or "").lower()
        if not domain_lower:
            return 1.0

        best_overlap = 0.0
        for keyword, coords in self._DOMAIN_KEYWORDS.items():
            if keyword in domain_lower:
                overlap = len(coordinates & coords)
                total = len(coordinates)
                if total > 0:
                    best_overlap = max(best_overlap, overlap / total)

        # Map [0, 1] overlap → [0.5, 1.5] multiplier
        return 0.5 + best_overlap * 1.0 if best_overlap > 0 else 1.0


# ===================================================================
# Module-level helper utilities
# ===================================================================

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on",
    "with", "by", "is", "it", "at", "as", "from", "app", "tool",
})


def _normalise_words(text: str) -> set[str]:
    """Lowercase, strip punctuation, remove stopwords → word set."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _generic_demand_signals(area: str) -> list[str]:
    """Generate area-based demand signals for common target-area keywords."""
    signals: list[str] = []
    if not area:
        return signals

    _AREA_SIGNALS: dict[str, str] = {
        "productivity": "Productivity software market exceeds $100B globally",
        "collaboration": "Remote-work tools saw 300% growth 2020-2023",
        "analytics": "Self-serve analytics demand growing 20% CAGR",
        "automation": "Workflow automation market projected $30B by 2026",
        "education": "EdTech market valued at $340B worldwide",
        "health": "Digital-health apps downloaded 3.5B times in 2023",
        "finance": "Fintech app usage growing 25% YoY",
        "ecommerce": "Global e-commerce projected $8T by 2027",
        "developer": "Developer-tools market growing at 22% CAGR",
        "social": "Social platforms collectively have 4.9B users",
        "communication": "Business-communication tools market at $50B",
        "security": "Cybersecurity market projected $300B by 2027",
        "logistics": "Supply-chain software market at $20B",
        "marketing": "Marketing-technology landscape exceeds 11,000 products",
    }

    for keyword, signal in _AREA_SIGNALS.items():
        if keyword in area:
            signals.append(signal)

    return signals
