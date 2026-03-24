"""Web Trust Bundle — Judgment Fiber Bundle for web application verification.

Models the full-stack web application as a fiber bundle where:

- **Base space** *B* = request lifecycle stages
  (browser, route, handler, database, response, render).
- **Fibers** *F_s* = judgment spaces at each stage – concrete claims about
  correctness together with their evidence and trust level.
- **Connection** ∇ = trust transport across web-stack boundaries
  (client → server, server → DB, template → browser, …).
- **Curvature** *F* = trust inconsistency at layer boundaries.

The key insight: web-application bugs live in the *overlaps* between
layers.  The curvature of the trust connection precisely measures these
overlaps, surfacing structural problems that no single-layer analysis
can detect.

Lifecycle loop
--------------
A full request lifecycle follows:

    browser → route → handler → database → response → render → browser

The **holonomy** around this loop measures the net trust shift: a non-
trivial holonomy indicates structural trust defects that cannot be fixed
by adjusting individual layers.

First Chern class
-----------------
The average curvature over all stage triples gives a global topological
invariant *c₁* of the web stack.  A flat bundle (*c₁* = 0) means trust
is transported consistently across every boundary.

Usage::

    bundle = WebTrustBundle()
    bundle.add_judgment(WebJudgment(stage="handler", language="python",
                                     claim="input validated", trust="SERVER_VALIDATED"))
    bundle.add_judgment(WebJudgment(stage="browser", language="javascript",
                                     claim="input validated", trust="CLIENT_VALIDATED"))
    bundle.build_connection()

    print(bundle.lifecycle_holonomy())
    print(bundle.summary_text())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Sequence

from .models import TRUST_ORDER, trust_index, TrustBoundary, TrustTransport, TrustReport


# ═══════════════════════════════════════════════════════════════════════
#  Lifecycle stages
# ═══════════════════════════════════════════════════════════════════════


class LifecycleStage:
    """Named constants for stages in the web request lifecycle.

    The canonical ordering mirrors the flow of a single HTTP request
    through a typical web application.
    """

    BROWSER = "browser"
    ROUTE = "route"
    HANDLER = "handler"
    DATABASE = "database"
    RESPONSE = "response"
    RENDER = "render"

    ALL: list[str] = [BROWSER, ROUTE, HANDLER, DATABASE, RESPONSE, RENDER]

    # The full request cycle closes back at the browser.
    REQUEST_CYCLE: list[str] = [
        BROWSER, ROUTE, HANDLER, DATABASE, RESPONSE, RENDER, BROWSER,
    ]


# ═══════════════════════════════════════════════════════════════════════
#  Language-layer mapping
# ═══════════════════════════════════════════════════════════════════════

#: Languages that execute at each lifecycle stage.
STAGE_LANGUAGES: dict[str, list[str]] = {
    "browser": ["javascript", "html", "css"],
    "route": ["python"],
    "handler": ["python"],
    "database": ["sql"],
    "response": ["python", "html"],
    "render": ["javascript", "html", "css"],
}

#: Maximum achievable trust level at each stage.  Judgments that
#: exceed the ceiling are flagged as violations.
STAGE_TRUST_CEILING: dict[str, str] = {
    "browser": "CLIENT_VALIDATED",
    "route": "SERVER_VALIDATED",
    "handler": "SERVER_VALIDATED",
    "database": "DB_CONSTRAINT_ENFORCED",
    "response": "TEMPLATE_TYPE_CHECKED",
    "render": "JS_TYPE_CHECKED",
}

# Stop-words stripped when testing claim relatedness.
_STOP_WORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
     "to", "for", "of", "and", "or", "but", "not", "with", "by"}
)


# ═══════════════════════════════════════════════════════════════════════
#  Core data carriers
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WebJudgment:
    """A single judgment at a specific stage of the web stack.

    Attributes
    ----------
    stage : str
        Lifecycle stage (one of :class:`LifecycleStage` constants).
    language : str
        Programming language of the artifact (python, javascript, …).
    claim : str
        Human-readable claim about correctness.
    evidence : tuple[str, ...]
        Evidence supporting the claim (test names, proof paths, …).
    trust : str
        Current trust level from ``TRUST_ORDER``.
    coordinate : str
        Specific code coordinate (e.g. ``"app.py:42"``).
    source : str
        Identifier of who made the judgment (linter, human reviewer, …).
    """

    stage: str
    language: str
    claim: str
    evidence: tuple[str, ...] = ()
    trust: str = "USER_INPUT"
    coordinate: str = ""
    source: str = ""


@dataclass
class WebFiber:
    """Fiber of judgments over a single lifecycle stage.

    Collects every :class:`WebJudgment` that belongs to a given stage
    and provides aggregate statistics.
    """

    stage: str
    judgments: list[WebJudgment] = field(default_factory=list)

    # -- properties --------------------------------------------------------

    @property
    def languages(self) -> set[str]:
        """Distinct languages represented by the contained judgments."""
        return {j.language for j in self.judgments}

    @property
    def average_trust_index(self) -> float:
        """Mean numeric trust index across contained judgments."""
        if not self.judgments:
            return 0.0
        return sum(trust_index(j.trust) for j in self.judgments) / len(self.judgments)

    @property
    def min_trust(self) -> str:
        """Lowest trust level in this fiber."""
        if not self.judgments:
            return TRUST_ORDER[0]
        return min(self.judgments, key=lambda j: trust_index(j.trust)).trust

    @property
    def max_trust(self) -> str:
        """Highest trust level in this fiber."""
        if not self.judgments:
            return TRUST_ORDER[0]
        return max(self.judgments, key=lambda j: trust_index(j.trust)).trust

    @property
    def trust_ceiling(self) -> str:
        """The maximum trust that can be *legitimately* achieved at this stage."""
        return STAGE_TRUST_CEILING.get(self.stage, "MECHANICALLY_VERIFIED")

    @property
    def trust_spread(self) -> int:
        """Numeric spread between min and max trust in the fiber."""
        if not self.judgments:
            return 0
        idxs = [trust_index(j.trust) for j in self.judgments]
        return max(idxs) - min(idxs)

    # -- queries -----------------------------------------------------------

    def ceiling_violations(self) -> list[WebJudgment]:
        """Return judgments that exceed the stage's trust ceiling."""
        ceiling_idx = trust_index(self.trust_ceiling)
        return [j for j in self.judgments if trust_index(j.trust) > ceiling_idx]

    def judgments_at_trust(self, level: str) -> list[WebJudgment]:
        """Return judgments exactly at *level*."""
        return [j for j in self.judgments if j.trust == level]

    def judgments_for_language(self, language: str) -> list[WebJudgment]:
        """Return judgments in *language*."""
        return [j for j in self.judgments if j.language == language]


# ═══════════════════════════════════════════════════════════════════════
#  Transport observation
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class WebTransportObservation:
    """A single trust-transport observation at a stage boundary.

    Attributes
    ----------
    source_stage, target_stage : str
        The two adjacent lifecycle stages.
    source_trust, target_trust : str
        Trust levels on either side of the boundary.
    trust_delta : int
        Signed difference (target − source) in the trust lattice.
    boundary : str
        Human-readable name of the boundary crossed.
    language_crossing : bool
        ``True`` when the transport crosses a language boundary
        (e.g. Python → SQL).
    """

    source_stage: str
    target_stage: str
    source_trust: str
    target_trust: str
    trust_delta: int
    boundary: str = ""
    language_crossing: bool = False


# ═══════════════════════════════════════════════════════════════════════
#  Trust connection
# ═══════════════════════════════════════════════════════════════════════


class WebTrustConnection:
    """Trust connection on the web-stack bundle.

    Records how trust transforms at each boundary in the request lifecycle.
    The connection captures both same-language transport (e.g. Python handler
    to Python response) and cross-language transport (e.g. Python handler to
    SQL database), which is typically more expensive or lossy.
    """

    def __init__(self) -> None:
        self._observations: dict[tuple[str, str], list[WebTransportObservation]] = {}

    # -- recording ---------------------------------------------------------

    def observe(
        self,
        source_stage: str,
        target_stage: str,
        source_trust: str,
        target_trust: str,
        boundary: str = "",
        language_crossing: bool = False,
    ) -> None:
        """Record a trust-transport observation between two stages."""
        key = (source_stage, target_stage)
        obs = WebTransportObservation(
            source_stage=source_stage,
            target_stage=target_stage,
            source_trust=source_trust,
            target_trust=target_trust,
            trust_delta=trust_index(target_trust) - trust_index(source_trust),
            boundary=boundary,
            language_crossing=language_crossing,
        )
        self._observations.setdefault(key, []).append(obs)

    # -- queries -----------------------------------------------------------

    def observations(self, source: str, target: str) -> list[WebTransportObservation]:
        """Return all observations for the (*source*, *target*) edge."""
        return list(self._observations.get((source, target), []))

    def average_delta(self, source: str, target: str) -> float:
        """Mean trust delta for the (*source*, *target*) edge."""
        obs = self._observations.get((source, target), [])
        if not obs:
            return 0.0
        return sum(o.trust_delta for o in obs) / len(obs)

    def max_delta(self, source: str, target: str) -> int:
        """Largest observed trust delta for the given edge."""
        obs = self._observations.get((source, target), [])
        if not obs:
            return 0
        return max(o.trust_delta for o in obs)

    def min_delta(self, source: str, target: str) -> int:
        """Smallest observed trust delta for the given edge."""
        obs = self._observations.get((source, target), [])
        if not obs:
            return 0
        return min(o.trust_delta for o in obs)

    def cross_language_penalty(self, source: str, target: str) -> float:
        """Average additional trust loss due to language-boundary crossing.

        Returns zero when there are not both crossing and non-crossing
        observations available for comparison.
        """
        obs = self._observations.get((source, target), [])
        if not obs:
            return 0.0
        crossing = [o for o in obs if o.language_crossing]
        non_crossing = [o for o in obs if not o.language_crossing]
        if not crossing or not non_crossing:
            return 0.0
        avg_cross = sum(o.trust_delta for o in crossing) / len(crossing)
        avg_non = sum(o.trust_delta for o in non_crossing) / len(non_crossing)
        return avg_cross - avg_non

    def edge_count(self) -> int:
        """Number of distinct (source, target) edges with observations."""
        return len(self._observations)

    def total_observations(self) -> int:
        """Total number of individual transport observations."""
        return sum(len(v) for v in self._observations.values())


# ═══════════════════════════════════════════════════════════════════════
#  Curvature
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class WebCurvature:
    """Curvature at a triple of lifecycle stages.

    The curvature is the signed sum of average trust deltas around the
    triangle (s1 → s2 → s3 → s1).  Zero curvature ("flat") means trust
    is transported consistently around the triangle.

    Positive curvature indicates trust *inflation* (over-trusting at
    a boundary); negative curvature indicates trust *deflation*
    (excessive re-validation or unnecessary trust loss).
    """

    stages: tuple[str, str, str]
    value: float
    edge_deltas: tuple[float, float, float]
    involves_language_crossing: bool = False

    @property
    def is_flat(self) -> bool:
        """``True`` when the curvature is effectively zero."""
        return abs(self.value) < 1e-9

    @property
    def interpretation(self) -> str:
        """Human-readable summary of the curvature."""
        if self.is_flat:
            return "flat"
        prefix = "cross-language " if self.involves_language_crossing else ""
        if self.value > 0:
            return f"{prefix}trust inflation (over-trusting at boundary)"
        return f"{prefix}trust deflation (excessive re-validation)"


# ═══════════════════════════════════════════════════════════════════════
#  Holonomy
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class LifecycleHolonomy:
    """Holonomy around the request lifecycle loop.

    The total trust shift when following a request through the full
    lifecycle::

        browser → route → handler → DB → response → render → browser

    Non-trivial holonomy means the web stack has structural trust
    defects that cannot be fixed by adjusting individual layers.
    """

    loop: tuple[str, ...]
    total_shift: float
    edge_shifts: list[float]

    @property
    def is_trivial(self) -> bool:
        """``True`` when the total shift is effectively zero."""
        return abs(self.total_shift) < 1e-9

    @property
    def interpretation(self) -> str:
        """Human-readable summary of the holonomy."""
        if self.is_trivial:
            return "trivial holonomy: request lifecycle is trust-consistent"
        if self.total_shift > 0:
            return (
                f"positive holonomy ({self.total_shift:+.1f}): "
                "trust gained through lifecycle (potential false confidence)"
            )
        return (
            f"negative holonomy ({self.total_shift:+.1f}): "
            "trust lost through lifecycle (overly conservative or broken chain)"
        )


# ═══════════════════════════════════════════════════════════════════════
#  Web Trust Bundle
# ═══════════════════════════════════════════════════════════════════════


def _languages_overlap(stage_a: str, stage_b: str) -> bool:
    """Return ``True`` when two stages share at least one language."""
    langs_a = set(STAGE_LANGUAGES.get(stage_a, []))
    langs_b = set(STAGE_LANGUAGES.get(stage_b, []))
    return bool(langs_a & langs_b)


class WebTrustBundle:
    """Judgment Fiber Bundle for full-stack web application verification.

    The central diagnostic tool for web apps: models the entire request
    lifecycle as a fiber bundle with trust as the connection.

    **Workflow**

    1. Add :class:`WebJudgment` instances via :meth:`add_judgment`.
    2. Optionally ingest existing transport chains via
       :meth:`add_transport_chain`.
    3. Call :meth:`build_connection` (or let it be built lazily).
    4. Query :meth:`curvature`, :meth:`lifecycle_holonomy`,
       :meth:`ceiling_violations`, etc.
    5. Call :meth:`diagnose` for a complete diagnostic dict, or
       :meth:`summary_text` for a human-readable report.

    Example::

        bundle = WebTrustBundle()
        bundle.add_judgment(WebJudgment(
            stage="handler", language="python",
            claim="input validated", trust="SERVER_VALIDATED",
        ))
        bundle.add_judgment(WebJudgment(
            stage="browser", language="javascript",
            claim="input validated", trust="CLIENT_VALIDATED",
        ))
        bundle.build_connection()

        hol = bundle.lifecycle_holonomy()
        print(hol.interpretation)
        print(bundle.summary_text())
    """

    def __init__(self) -> None:
        self._fibers: dict[str, WebFiber] = {}
        self._connection = WebTrustConnection()
        self._connection_built = False

    # -- fiber access ------------------------------------------------------

    @property
    def stages(self) -> list[str]:
        """Lifecycle stages that currently have at least one judgment."""
        return sorted(self._fibers.keys())

    @property
    def total_judgments(self) -> int:
        """Total number of judgments across all fibers."""
        return sum(len(f.judgments) for f in self._fibers.values())

    def fiber(self, stage: str) -> WebFiber | None:
        """Return the fiber at *stage*, or ``None`` if no judgments exist."""
        return self._fibers.get(stage)

    # -- ingestion ---------------------------------------------------------

    def add_judgment(self, judgment: WebJudgment) -> None:
        """Add a single judgment to the appropriate fiber.

        Invalidates the cached connection so the next query will
        re-build it automatically.
        """
        if judgment.stage not in self._fibers:
            self._fibers[judgment.stage] = WebFiber(stage=judgment.stage)
        self._fibers[judgment.stage].judgments.append(judgment)
        self._connection_built = False

    def add_judgments(self, judgments: Sequence[WebJudgment]) -> None:
        """Convenience: add multiple judgments at once."""
        for j in judgments:
            self.add_judgment(j)

    def add_transport_chain(
        self,
        transports: list[TrustTransport],
        stages: list[str],
    ) -> None:
        """Ingest a chain of :class:`TrustTransport` objects.

        Each transport corresponds to the edge between consecutive
        stages in the *stages* list.  If the transport chain has *n*
        transports the *stages* list must have *n + 1* entries.
        """
        if len(stages) < 2:
            return
        for transport, (src, tgt) in zip(transports, zip(stages, stages[1:])):
            is_cross = not _languages_overlap(src, tgt)
            self._connection.observe(
                src, tgt,
                transport.source_trust, transport.target_trust,
                language_crossing=is_cross,
            )
        self._connection_built = True

    # -- connection building -----------------------------------------------

    def build_connection(self) -> WebTrustConnection:
        """Build the trust connection from judgment pairs at adjacent stages.

        For every pair of adjacent stages in the lifecycle, pairs of
        judgments that make *related* claims are matched and their trust
        differential is recorded as a transport observation.

        Returns the computed :class:`WebTrustConnection`.
        """
        all_stages = LifecycleStage.ALL
        for i, s1 in enumerate(all_stages):
            s2 = all_stages[(i + 1) % len(all_stages)]
            f1 = self._fibers.get(s1)
            f2 = self._fibers.get(s2)
            if f1 and f2:
                is_cross = not _languages_overlap(s1, s2)
                for j1 in f1.judgments:
                    for j2 in f2.judgments:
                        if self._claims_related(j1.claim, j2.claim):
                            self._connection.observe(
                                s1, s2, j1.trust, j2.trust,
                                language_crossing=is_cross,
                            )
        self._connection_built = True
        return self._connection

    def _ensure_connection(self) -> None:
        """Lazily build the connection if it has not been built yet."""
        if not self._connection_built:
            self.build_connection()

    @staticmethod
    def _claims_related(c1: str, c2: str) -> bool:
        """Heuristic: two claims are related when they share enough words.

        Strips common stop-words and requires > 40 % overlap between
        the remaining word sets (relative to the smaller set).
        """
        w1 = set(c1.lower().split()) - _STOP_WORDS
        w2 = set(c2.lower().split()) - _STOP_WORDS
        if not w1 or not w2:
            return False
        return len(w1 & w2) / min(len(w1), len(w2)) > 0.4

    # -- curvature ---------------------------------------------------------

    def curvature(self, s1: str, s2: str, s3: str) -> WebCurvature:
        """Compute the curvature at the stage triple (*s1*, *s2*, *s3*).

        The curvature is the signed sum of average trust deltas around
        the triangle:  Δ(s1→s2) + Δ(s2→s3) + Δ(s3→s1).
        """
        self._ensure_connection()
        d12 = self._connection.average_delta(s1, s2)
        d23 = self._connection.average_delta(s2, s3)
        d31 = self._connection.average_delta(s3, s1)
        pairs = [(s1, s2), (s2, s3), (s3, s1)]
        cross = any(not _languages_overlap(a, b) for a, b in pairs)
        return WebCurvature(
            stages=(s1, s2, s3),
            value=d12 + d23 + d31,
            edge_deltas=(d12, d23, d31),
            involves_language_crossing=cross,
        )

    def all_curvatures(self) -> list[WebCurvature]:
        """Curvatures over every triple of stages that have judgments."""
        self._ensure_connection()
        active = [s for s in LifecycleStage.ALL if s in self._fibers]
        return [self.curvature(*triple) for triple in combinations(active, 3)]

    def cross_language_curvature(self) -> list[WebCurvature]:
        """Non-flat curvatures involving at least one language crossing."""
        return [
            c for c in self.all_curvatures()
            if c.involves_language_crossing and not c.is_flat
        ]

    # -- holonomy ----------------------------------------------------------

    def lifecycle_holonomy(self) -> LifecycleHolonomy:
        """Compute holonomy around the full request lifecycle.

        Returns a :class:`LifecycleHolonomy` recording the shift at
        each edge and the total accumulated shift.
        """
        self._ensure_connection()
        cycle = LifecycleStage.REQUEST_CYCLE
        shifts: list[float] = []
        for i in range(len(cycle) - 1):
            shifts.append(self._connection.average_delta(cycle[i], cycle[i + 1]))
        return LifecycleHolonomy(
            loop=tuple(cycle),
            total_shift=sum(shifts),
            edge_shifts=shifts,
        )

    # -- ceiling violations ------------------------------------------------

    def ceiling_violations(self) -> dict[str, list[WebJudgment]]:
        """Find judgments that exceed their stage's trust ceiling.

        Returns a dict mapping stage names to lists of offending
        judgments.  Stages with no violations are omitted.
        """
        violations: dict[str, list[WebJudgment]] = {}
        for stage, fiber in self._fibers.items():
            v = fiber.ceiling_violations()
            if v:
                violations[stage] = v
        return violations

    # -- first Chern class -------------------------------------------------

    def first_chern_class(self) -> float:
        """Average curvature over all stage triples (topological invariant).

        *c₁* = 0 indicates a flat bundle (trust is consistently
        transported); non-zero values indicate structural inconsistency.
        """
        curvatures = self.all_curvatures()
        if not curvatures:
            return 0.0
        return sum(c.value for c in curvatures) / len(curvatures)

    # -- full diagnostics --------------------------------------------------

    def diagnose(self) -> dict[str, Any]:
        """Produce a comprehensive diagnostic dictionary.

        Includes stage listing, holonomy, curvatures, ceiling
        violations, and per-fiber statistics.  Useful for serialisation
        or dashboard display.
        """
        self._ensure_connection()
        active_stages = self.stages
        hol = self.lifecycle_holonomy()
        curvatures = self.all_curvatures()
        non_flat = [c for c in curvatures if not c.is_flat]
        cross_lang = [c for c in non_flat if c.involves_language_crossing]
        c1 = self.first_chern_class()
        violations = self.ceiling_violations()

        return {
            "stages": active_stages,
            "total_judgments": self.total_judgments,
            "first_chern_class": c1,
            "bundle_is_flat": all(c.is_flat for c in curvatures) and hol.is_trivial,
            "lifecycle_holonomy": {
                "total_shift": hol.total_shift,
                "trivial": hol.is_trivial,
                "interpretation": hol.interpretation,
            },
            "curvatures_total": len(curvatures),
            "curvatures_non_flat": len(non_flat),
            "cross_language_curvatures": len(cross_lang),
            "non_flat_details": [
                {
                    "stages": c.stages,
                    "value": c.value,
                    "interpretation": c.interpretation,
                }
                for c in non_flat[:10]
            ],
            "ceiling_violations": {
                stage: [
                    {
                        "claim": j.claim,
                        "trust": j.trust,
                        "ceiling": STAGE_TRUST_CEILING.get(stage, "?"),
                    }
                    for j in violations_list
                ]
                for stage, violations_list in violations.items()
            },
            "fiber_stats": {
                stage: {
                    "judgments": len(f.judgments),
                    "languages": sorted(f.languages),
                    "avg_trust": f.average_trust_index,
                    "trust_spread": f.trust_spread,
                }
                for stage, f in self._fibers.items()
            },
            "connection_stats": {
                "edges": self._connection.edge_count(),
                "observations": self._connection.total_observations(),
            },
        }

    # -- human-readable summary --------------------------------------------

    def summary_text(self) -> str:
        """Return a human-readable diagnostic summary.

        Suitable for CLI output or logging.  Calls :meth:`diagnose`
        internally.
        """
        d = self.diagnose()
        lines = [
            "═══ Web Trust Bundle Diagnostic ═══",
            f"  Stages: {', '.join(d['stages'])}",
            f"  Total judgments: {d['total_judgments']}",
            f"  Bundle is flat: {'Yes ✓' if d['bundle_is_flat'] else 'No ✗'}",
            "",
            "  Request Lifecycle Holonomy:",
            f"    {d['lifecycle_holonomy']['interpretation']}",
            f"    Total shift: {d['lifecycle_holonomy']['total_shift']:.1f}",
            "",
            f"  First Chern class c₁ = {d['first_chern_class']:+.4f}",
            f"  Non-flat curvatures: {d['curvatures_non_flat']} / {d['curvatures_total']}",
            f"  Cross-language curvatures: {d['cross_language_curvatures']}",
        ]

        if d["non_flat_details"]:
            lines.append("")
            lines.append("  Non-flat details:")
            for c in d["non_flat_details"][:5]:
                lines.append(
                    f"    {c['stages']}: {c['value']:+.2f} — {c['interpretation']}"
                )

        if d["ceiling_violations"]:
            lines.append("")
            lines.append("  ⚠ Trust ceiling violations:")
            for stage, vs in d["ceiling_violations"].items():
                for v in vs:
                    lines.append(
                        f"    {stage}: '{v['claim']}' at {v['trust']} "
                        f"exceeds ceiling {v['ceiling']}"
                    )

        return "\n".join(lines)

    # -- reset -------------------------------------------------------------

    def reset(self) -> None:
        """Clear all fibers, observations, and cached state."""
        self._fibers.clear()
        self._connection = WebTrustConnection()
        self._connection_built = False
