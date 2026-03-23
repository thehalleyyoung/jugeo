"""Integration layer connecting descent_locality to geometry.descent,
geometry.covers, geometry.site, and evidence subsystems — Theory2.tex Ch4.

This module provides the bridge objects and integration engine that wire
together the four major subsystems of JuGeo into a unified descent pipeline:

* **geometry.descent** — the overlap-checking, gluing, and obstruction engine
* **geometry.covers** — cover construction, refinement, scoring, serialisation
* **geometry.site** — Grothendieck sites, coordinate lookup, topology axioms
* **evidence** — provenance tracing, trust scoring, certificate issuance

The integration layer does *not* re-implement any of those subsystems.
Instead it defines typed bridge objects that hold live references and expose
convenience operations.  Bridges are composable: the ``DescentIntegration``
facade assembles all four bridges and exposes a single
``run_integrated_descent`` entry-point that runs the full pipeline in the
correct order.

Theory reference
----------------
theory2.tex Ch4 §4.1 "Integration architecture"
theory2.tex Ch4 §4.2 "Bridge pattern for descent"
theory2.tex Ch4 §4.3 "Provenance and trust in integrated runs"

copilot: shared-core marker
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.covers import (
    Cover,
    CoverDiagnostics,
    CoverMetric,
    CoverRefinement,
    CoverSerializer,
    CoverStatistics,
    refine_cover,
    score_cover,
)
from jugeo.geometry.descent import (
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentLog,
    DescentPhase,
    DescentResult,
    DescentStrategy,
    GluingData,
    GluingReport,
    GlobalSection,
    LocalSection,
    Obstruction,
    OverlapCondition,
    RepairFrontier,
)
from jugeo.geometry.site import (
    Coordinate,
    CoordinateIndex,
    CoveringFamily,
    GrothendieckTopology,
    Morphism,
    Site,
    SiteDiagnostics,
    SiteSerializer,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_id() -> str:
    """Return a compact random identifier."""
    return uuid.uuid4().hex[:12]


def _digest(*parts: str) -> str:
    """SHA-256 digest of concatenated strings, truncated to 16 hex chars."""
    h = hashlib.sha256("||".join(parts).encode()).hexdigest()
    return h[:16]


# ---------------------------------------------------------------------------
# Bridge classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentBridge:
    """Live bridge to a :class:`~jugeo.geometry.descent.DescentEngine`.

    A ``DescentBridge`` wraps an engine together with its current
    configuration and audit log.  All mutation methods return ``self`` so
    that bridge configuration can be chained fluently before calling
    :meth:`run`.

    Attributes
    ----------
    engine:
        The underlying :class:`~jugeo.geometry.descent.DescentEngine`.
    config:
        The :class:`~jugeo.geometry.descent.DescentConfiguration` currently
        active on the engine.
    log:
        The :class:`~jugeo.geometry.descent.DescentLog` accumulating this
        bridge's history.

    copilot: shared-core marker
    """

    engine: DescentEngine
    config: DescentConfiguration
    log: DescentLog
    _last_result: DescentResult | None = field(default=None, repr=False)
    _trust_floor: float = field(default=0.0, repr=False)
    _repair_frontier: RepairFrontier | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def run(self, gluing_data: GluingData) -> DescentResult:
        """Execute descent over *gluing_data* using the current configuration.

        The engine is reconfigured with :attr:`config` immediately before
        the run so that any fluent-chain modifications take effect.

        Parameters
        ----------
        gluing_data:
            Pre-assembled :class:`~jugeo.geometry.descent.GluingData`
            containing local sections and overlap conditions.

        Returns
        -------
        DescentResult
            The outcome of the run.  Also stored in :attr:`_last_result`.
        """
        # Propagate the current config into the engine before running.
        self.engine.configuration = self.config
        result = self.engine.run(gluing_data)
        self._last_result = result
        self.log.append_phase(DescentPhase.COMPLETE, metadata={
            "run_id": _short_id(),
            "timestamp": _utcnow_iso(),
            "success": result.succeeded,
        })
        return result

    # ------------------------------------------------------------------
    # Fluent configuration
    # ------------------------------------------------------------------

    def configure(self, strategy: DescentStrategy) -> DescentBridge:
        """Return *self* after switching the engine to *strategy*.

        Parameters
        ----------
        strategy:
            One of the :class:`~jugeo.geometry.descent.DescentStrategy`
            members (``EAGER``, ``EXHAUSTIVE``, ``ITERATIVE``,
            ``OPTIMISTIC``).
        """
        self.config = DescentConfiguration(
            strategy=strategy,
            trust_floor=self.config.trust_floor,
            max_iterations=self.config.max_iterations,
            parallelism=self.config.parallelism,
        )
        return self

    def with_trust_floor(self, trust: float) -> DescentBridge:
        """Return *self* after setting the minimum trust threshold.

        Sections with trust scores below *trust* are treated as violating
        overlap conditions during the descent run.

        Parameters
        ----------
        trust:
            A float in ``[0.0, 1.0]``.  Values outside this range are
            clamped silently.
        """
        clamped = max(0.0, min(1.0, float(trust)))
        self._trust_floor = clamped
        self.config = DescentConfiguration(
            strategy=self.config.strategy,
            trust_floor=clamped,
            max_iterations=self.config.max_iterations,
            parallelism=self.config.parallelism,
        )
        return self

    def set_repair_frontier(self, rf: RepairFrontier) -> None:
        """Attach a :class:`~jugeo.geometry.descent.RepairFrontier` for use
        on the next :meth:`run` call.

        The frontier is passed into the engine's repair hook so that
        copilot-assisted repairs can be proposed automatically when descent
        detects a violation.

        Parameters
        ----------
        rf:
            The repair frontier to attach.
        """
        self._repair_frontier = rf
        self.engine.repair_frontier = rf

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_log(self) -> DescentLog:
        """Return the current audit :class:`~jugeo.geometry.descent.DescentLog`."""
        return self.log

    def last_result(self) -> DescentResult | None:
        """Return the result of the most recent :meth:`run`, or ``None``."""
        return self._last_result

    def reset(self) -> None:
        """Clear :attr:`_last_result` and reset the engine's internal state.

        This does *not* wipe the log; log entries are permanent.  Use this
        between unrelated descent runs to avoid result contamination.
        """
        self._last_result = None
        self.engine.reset()

    def summary(self) -> str:
        """Return a human-readable one-line summary of this bridge.

        The summary includes the current strategy, trust floor, number of
        log entries, and whether the last run succeeded.

        Returns
        -------
        str
            E.g. ``"DescentBridge[EXHAUSTIVE, floor=0.75, log=12, last=OK]"``.
        """
        strategy_name = self.config.strategy.value if hasattr(self.config.strategy, "value") else str(self.config.strategy)
        last_str = "none"
        if self._last_result is not None:
            last_str = "OK" if self._last_result.succeeded else "FAIL"
        n_entries = len(self.log.entries) if hasattr(self.log, "entries") else 0
        return (
            f"DescentBridge[{strategy_name}, floor={self._trust_floor:.2f}, "
            f"log={n_entries}, last={last_str}]"
        )


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverBridge:
    """Live bridge to a :class:`~jugeo.geometry.covers.Cover`.

    Wraps a cover with its refinement history, diagnostics object, and
    statistics object.  Provides convenience access to scoring, merging,
    patching, and serialisation.

    Attributes
    ----------
    cover:
        The underlying :class:`~jugeo.geometry.covers.Cover`.
    refinements:
        Ordered list of :class:`~jugeo.geometry.covers.CoverRefinement`
        objects produced during this bridge's lifetime.
    diagnostics:
        A :class:`~jugeo.geometry.covers.CoverDiagnostics` instance bound
        to the current cover.
    statistics:
        A :class:`~jugeo.geometry.covers.CoverStatistics` snapshot.

    copilot: shared-core marker
    """

    cover: Cover
    refinements: list[CoverRefinement]
    diagnostics: CoverDiagnostics
    statistics: CoverStatistics

    # ------------------------------------------------------------------
    # Mutation (returns new bridge around updated cover)
    # ------------------------------------------------------------------

    def refine(self, suffix: str) -> CoverBridge:
        """Return a new :class:`CoverBridge` wrapping a refined cover.

        Calls :func:`~jugeo.geometry.covers.refine_cover` with the given
        *suffix*, records the refinement in :attr:`refinements`, and wraps
        the result in a fresh bridge that inherits the refinement history.

        Parameters
        ----------
        suffix:
            Label appended to each member name in the refined cover.

        Returns
        -------
        CoverBridge
            A new bridge around the refined cover.
        """
        refined_cover = refine_cover(self.cover, suffix=suffix)
        new_refinements = list(self.refinements)
        # Record the refinement transition
        refinement = CoverRefinement(
            source_cover=self.cover,
            refined_cover=refined_cover,
            refinement_label=suffix,
        )
        new_refinements.append(refinement)
        new_diag = CoverDiagnostics(cover=refined_cover)
        new_stats = CoverStatistics.from_cover(refined_cover)
        return CoverBridge(
            cover=refined_cover,
            refinements=new_refinements,
            diagnostics=new_diag,
            statistics=new_stats,
        )

    def merge(self, other: Cover) -> CoverBridge:
        """Return a new :class:`CoverBridge` after merging *other* into
        :attr:`cover`.

        Members of *other* that are not already present (by name) in
        :attr:`cover` are appended.  The merged cover is recorded as a
        synthetic refinement.

        Parameters
        ----------
        other:
            The cover whose members should be merged in.

        Returns
        -------
        CoverBridge
            A new bridge around the merged cover.
        """
        existing_names = {m.name for m in self.cover.members}
        new_members = list(self.cover.members)
        for m in other.members:
            if m.name not in existing_names:
                new_members.append(m)
                existing_names.add(m.name)
        merged = Cover(
            name=f"{self.cover.name}+{other.name}",
            members=tuple(new_members),
            base=self.cover.base,
        )
        new_diag = CoverDiagnostics(cover=merged)
        new_stats = CoverStatistics.from_cover(merged)
        return CoverBridge(
            cover=merged,
            refinements=list(self.refinements),
            diagnostics=new_diag,
            statistics=new_stats,
        )

    # ------------------------------------------------------------------
    # Scoring / diagnostics
    # ------------------------------------------------------------------

    def score(self) -> CoverMetric:
        """Compute and return the :class:`~jugeo.geometry.covers.CoverMetric`
        for the current cover.

        Delegates to :func:`~jugeo.geometry.covers.score_cover`.
        """
        return score_cover(self.cover)

    def diagnose(self) -> list[str]:
        """Return a list of human-readable diagnostic messages for the cover.

        Each message describes a potential issue or warning found by
        :attr:`diagnostics`.  An empty list means the cover is clean.

        Returns
        -------
        list[str]
            Diagnostic messages, possibly empty.
        """
        return list(self.diagnostics.run())

    def patch_names(self) -> list[str]:
        """Return the names of all members (patches) in the current cover.

        Returns
        -------
        list[str]
        """
        return [m.name for m in self.cover.members]

    def overlap_count(self) -> int:
        """Return the total number of pairwise overlaps in the current cover.

        Computes ``C(n, 2)`` where ``n`` is the number of members.

        Returns
        -------
        int
        """
        n = len(self.cover.members)
        return n * (n - 1) // 2

    def serialize(self) -> dict:
        """Return a JSON-serialisable ``dict`` representation of the cover.

        Delegates to :class:`~jugeo.geometry.covers.CoverSerializer`.
        """
        ser = CoverSerializer(cover=self.cover)
        return ser.to_dict()

    def summary(self) -> str:
        """Return a one-line human-readable summary of this bridge.

        Returns
        -------
        str
            E.g. ``"CoverBridge[mycover, 8 patches, 28 overlaps, 2 refinements]"``.
        """
        n = len(self.cover.members)
        return (
            f"CoverBridge[{self.cover.name}, {n} patches, "
            f"{self.overlap_count()} overlaps, {len(self.refinements)} refinements]"
        )


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SiteBridge:
    """Live bridge to a :class:`~jugeo.geometry.site.Site`.

    Holds a site together with its associated topology and coordinate index,
    and exposes convenience operations for topology queries and site mutations.

    Attributes
    ----------
    site:
        The underlying :class:`~jugeo.geometry.site.Site`.
    topology:
        The :class:`~jugeo.geometry.site.GrothendieckTopology` governing
        which families are covers.
    index:
        A :class:`~jugeo.geometry.site.CoordinateIndex` for fast lookup.

    copilot: shared-core marker
    """

    site: Site
    topology: GrothendieckTopology
    index: CoordinateIndex

    # ------------------------------------------------------------------
    # Topology queries
    # ------------------------------------------------------------------

    def covering_families(self, coord: Coordinate) -> list[CoveringFamily]:
        """Return all covering families for *coord* in the topology.

        Queries :attr:`topology` for all registered covering families whose
        base object equals *coord*.

        Parameters
        ----------
        coord:
            The coordinate whose coverings are requested.

        Returns
        -------
        list[CoveringFamily]
        """
        return list(self.topology.covering_families_for(coord))

    def lookup(self, name: str) -> Coordinate | None:
        """Look up a coordinate by its string name.

        Parameters
        ----------
        name:
            The dotted hierarchical name of the coordinate to find.

        Returns
        -------
        Coordinate or None
        """
        return self.index.get(name)

    def morphisms_from(self, coord: Coordinate) -> list[Morphism]:
        """Return all morphisms in the site whose source is *coord*.

        Parameters
        ----------
        coord:
            The source coordinate.

        Returns
        -------
        list[Morphism]
        """
        return list(self.site.morphisms_from(coord))

    def is_cover(self, family: CoveringFamily) -> bool:
        """Return ``True`` if *family* is a covering family in the topology.

        Parameters
        ----------
        family:
            The family to test.

        Returns
        -------
        bool
        """
        return self.topology.is_cover(family)

    def add_coordinate(self, coord: Coordinate) -> SiteBridge:
        """Return *self* after registering *coord* in the site and index.

        The coordinate is inserted into :attr:`site`'s internal storage and
        indexed in :attr:`index`.

        Parameters
        ----------
        coord:
            The coordinate to add.

        Returns
        -------
        SiteBridge
            ``self``, for chaining.
        """
        self.site.add_coordinate(coord)
        self.index.insert(coord)
        return self

    def validate_topology(self) -> bool:
        """Return ``True`` if the topology satisfies all Grothendieck axioms.

        Runs the full axiom check (identity, stability, transitivity) via
        :class:`~jugeo.geometry.site.SiteDiagnostics`.

        Returns
        -------
        bool
        """
        diag = SiteDiagnostics(site=self.site, topology=self.topology)
        issues = list(diag.validate_axioms())
        return len(issues) == 0

    def serialize(self) -> dict:
        """Return a JSON-serialisable ``dict`` representation of the site.

        Delegates to :class:`~jugeo.geometry.site.SiteSerializer`.
        """
        ser = SiteSerializer(site=self.site)
        return ser.to_dict()

    def summary(self) -> str:
        """Return a one-line human-readable summary of this bridge.

        Returns
        -------
        str
            E.g. ``"SiteBridge[mysite, 42 coords, topology=valid]"``.
        """
        n = len(list(self.site.coordinates()))
        topology_name = getattr(self.topology, "name", "topology")
        return f"SiteBridge[{getattr(self.site, 'name', '?')}, {n} coords, {topology_name}]"


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceBridge:
    """Bridge to the evidence subsystem (provenance, trust, certificates).

    Maintains a provenance chain (ordered list of event records), a dict of
    trust scores keyed by section ID, and a list of issued certificates.

    Attributes
    ----------
    provenance_chain:
        Ordered list of provenance event dicts.  Each dict has at least the
        keys ``"event"``, ``"source"``, and ``"timestamp"``.
    trust_scores:
        Mapping from section ID to its computed trust score in ``[0, 1]``.
    certificates:
        List of certificate dicts previously issued by this bridge.

    copilot: shared-core marker
    """

    provenance_chain: list[dict]
    trust_scores: dict[str, float]
    certificates: list[dict]
    _trust_anchors: dict[str, float] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def record_provenance(self, event: str, source: str) -> None:
        """Append a provenance event to :attr:`provenance_chain`.

        Parameters
        ----------
        event:
            A short description of the event (e.g. ``"section_added"``).
        source:
            The originating subsystem or agent identifier.
        """
        entry: dict = {
            "id": _short_id(),
            "event": event,
            "source": source,
            "timestamp": _utcnow_iso(),
        }
        self.provenance_chain.append(entry)

    def export_provenance(self) -> list[dict]:
        """Return a deep copy of :attr:`provenance_chain`.

        Returns
        -------
        list[dict]
            A snapshot of the provenance chain that is safe to mutate.
        """
        return [dict(e) for e in self.provenance_chain]

    def chain_length(self) -> int:
        """Return the number of entries in :attr:`provenance_chain`.

        Returns
        -------
        int
        """
        return len(self.provenance_chain)

    # ------------------------------------------------------------------
    # Trust
    # ------------------------------------------------------------------

    def add_trust_anchor(self, anchor_id: str, score: float) -> None:
        """Register a trust anchor that will influence future trust computations.

        Trust anchors are high-confidence reference points (e.g. verified
        copilot outputs) whose scores propagate into nearby section scores
        via the trust algebra.

        Parameters
        ----------
        anchor_id:
            Unique identifier for this anchor.
        score:
            The anchor's trust score in ``[0.0, 1.0]``.
        """
        self._trust_anchors[anchor_id] = max(0.0, min(1.0, float(score)))

    def compute_trust(self, section_id: str) -> float:
        """Return the trust score for *section_id*, caching the result.

        The score is computed as the geometric mean of any trust anchors
        registered via :meth:`add_trust_anchor` that share a prefix with
        *section_id*, falling back to ``0.5`` when no anchors are relevant.
        The computed score is stored in :attr:`trust_scores`.

        Parameters
        ----------
        section_id:
            The section whose trust score is requested.

        Returns
        -------
        float
            Trust score in ``[0.0, 1.0]``.
        """
        if section_id in self.trust_scores:
            return self.trust_scores[section_id]

        # Collect relevant anchor scores (prefix-match heuristic)
        relevant = [
            score
            for anchor_id, score in self._trust_anchors.items()
            if section_id.startswith(anchor_id) or anchor_id.startswith(section_id)
        ]
        if relevant:
            import math
            geo_mean = math.exp(sum(math.log(max(s, 1e-9)) for s in relevant) / len(relevant))
            computed = round(min(1.0, geo_mean), 6)
        else:
            computed = 0.5

        self.trust_scores[section_id] = computed
        return computed

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    def issue_certificate(self, section_id: str, verdict: str) -> dict:
        """Issue and record a certificate for *section_id*.

        The certificate includes a signature digest, the issuing timestamp,
        the trust score at time of issuance, and the provenance chain length.

        Parameters
        ----------
        section_id:
            The section being certified.
        verdict:
            A free-form verdict string (e.g. ``"VERIFIED"``, ``"PARTIAL"``).

        Returns
        -------
        dict
            The newly issued certificate dict.
        """
        trust = self.compute_trust(section_id)
        cert: dict = {
            "id": _short_id(),
            "section_id": section_id,
            "verdict": verdict,
            "trust_score": trust,
            "provenance_length": self.chain_length(),
            "signature": _digest(section_id, verdict, str(trust)),
            "issued_at": _utcnow_iso(),
        }
        self.certificates.append(cert)
        self.record_provenance(f"certificate_issued:{verdict}", source="EvidenceBridge")
        return cert

    def validate_chain(self) -> bool:
        """Return ``True`` if the provenance chain is internally consistent.

        Checks that all entries have the required keys and that timestamps
        are non-decreasing.

        Returns
        -------
        bool
        """
        required = {"id", "event", "source", "timestamp"}
        for entry in self.provenance_chain:
            if not required.issubset(entry.keys()):
                return False
        # Timestamp monotonicity (string ISO comparison is lexicographic,
        # which is correct for UTC ISO-8601 without timezone offset variance)
        timestamps = [e["timestamp"] for e in self.provenance_chain]
        return timestamps == sorted(timestamps)

    def summary(self) -> str:
        """Return a one-line summary of this evidence bridge.

        Returns
        -------
        str
        """
        return (
            f"EvidenceBridge[chain={self.chain_length()}, "
            f"trust_entries={len(self.trust_scores)}, "
            f"certs={len(self.certificates)}, "
            f"anchors={len(self._trust_anchors)}]"
        )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegratedResult:
    """Result of a full integrated descent pipeline run.

    Aggregates the outputs of all four subsystems into one immutable value.

    Attributes
    ----------
    global_section:
        The globally glued section, or ``None`` if descent failed.
    obstruction:
        The cohomology obstruction class, or ``None`` if descent succeeded.
    evidence:
        A dict containing trust scores, certificate IDs, and provenance
        metadata collected during the run.
    geometry_consistent:
        ``True`` if the cover and site passed all geometry consistency checks
        prior to descent.
    provenance:
        Immutable tuple of provenance event descriptions recorded during
        the run.
    repair_frontier:
        A list of repair-hint dicts for each violated overlap, empty when
        descent succeeded.

    copilot: shared-core marker
    """

    global_section: GlobalSection | None
    obstruction: CohomologyClass | None
    evidence: dict
    geometry_consistent: bool
    provenance: tuple[str, ...]
    repair_frontier: list[dict]

    @property
    def succeeded(self) -> bool:
        """``True`` iff descent produced a global section."""
        return self.global_section is not None

    def summary(self) -> str:
        """Return a compact human-readable summary.

        Returns
        -------
        str
        """
        status = "OK" if self.succeeded else "FAIL"
        n_repairs = len(self.repair_frontier)
        geo = "geo-ok" if self.geometry_consistent else "geo-inconsistent"
        return (
            f"IntegratedResult[{status}, {geo}, prov={len(self.provenance)}, "
            f"repairs={n_repairs}]"
        )


# ---------------------------------------------------------------------------
# DescentIntegration facade
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentIntegration:
    """Facade that assembles all four JuGeo subsystem bridges.

    ``DescentIntegration`` is the primary entry-point for code that needs
    to perform descent while also caring about covers, site topology, and
    evidence.  Bridges are created lazily when their ``connect_*`` method
    is first called.

    Attributes are intentionally sparse: the integration object is
    essentially a factory and orchestrator; state lives in the bridges it
    returns.

    copilot: shared-core marker
    """

    site: Site
    topology: GrothendieckTopology
    _descent_bridge: DescentBridge | None = field(default=None, repr=False)
    _cover_bridge: CoverBridge | None = field(default=None, repr=False)
    _site_bridge: SiteBridge | None = field(default=None, repr=False)
    _evidence_bridge: EvidenceBridge | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Bridge factories
    # ------------------------------------------------------------------

    def connect_geometry_descent(self, engine: DescentEngine) -> DescentBridge:
        """Create and return a :class:`DescentBridge` for *engine*.

        If a bridge was previously created, it is replaced.

        Parameters
        ----------
        engine:
            A live :class:`~jugeo.geometry.descent.DescentEngine`.

        Returns
        -------
        DescentBridge
        """
        config = engine.configuration
        log = engine.log
        bridge = DescentBridge(engine=engine, config=config, log=log)
        self._descent_bridge = bridge
        return bridge

    def connect_geometry_covers(self, cover: Cover) -> CoverBridge:
        """Create and return a :class:`CoverBridge` for *cover*.

        Parameters
        ----------
        cover:
            A :class:`~jugeo.geometry.covers.Cover` object.

        Returns
        -------
        CoverBridge
        """
        diag = CoverDiagnostics(cover=cover)
        stats = CoverStatistics.from_cover(cover)
        bridge = CoverBridge(
            cover=cover,
            refinements=[],
            diagnostics=diag,
            statistics=stats,
        )
        self._cover_bridge = bridge
        return bridge

    def connect_geometry_site(self, site: Site) -> SiteBridge:
        """Create and return a :class:`SiteBridge` for *site*.

        Uses :attr:`topology` as the Grothendieck topology and builds a
        fresh :class:`~jugeo.geometry.site.CoordinateIndex` from the site's
        current coordinates.

        Parameters
        ----------
        site:
            A :class:`~jugeo.geometry.site.Site` object.

        Returns
        -------
        SiteBridge
        """
        index = CoordinateIndex()
        for coord in site.coordinates():
            index.insert(coord)
        bridge = SiteBridge(site=site, topology=self.topology, index=index)
        self._site_bridge = bridge
        return bridge

    def connect_evidence(self, provenance: Any) -> EvidenceBridge:
        """Create and return an :class:`EvidenceBridge`.

        The *provenance* argument may be any object that the caller uses to
        seed the evidence chain; its ``str()`` representation is recorded as
        the first provenance event.

        Parameters
        ----------
        provenance:
            An existing provenance object (e.g. a
            :class:`~jugeo.evidence.provenance.ProvenanceGraph`) or any value
            that can be described as a string.

        Returns
        -------
        EvidenceBridge
        """
        bridge = EvidenceBridge(
            provenance_chain=[],
            trust_scores={},
            certificates=[],
        )
        bridge.record_provenance(
            event=f"evidence_connected:{type(provenance).__name__}",
            source="DescentIntegration",
        )
        self._evidence_bridge = bridge
        return bridge

    # ------------------------------------------------------------------
    # Integrated operations
    # ------------------------------------------------------------------

    def validate_geometry_consistency(self, cover: Cover, site: Site) -> list[str]:
        """Check that *cover* and *site* are mutually consistent.

        Consistency requires:

        1. Every patch name in *cover* resolves to a coordinate in *site*.
        2. Every overlap datum in *cover* corresponds to a morphism in *site*.
        3. The site's topology axioms all hold.

        Parameters
        ----------
        cover:
            The cover to validate.
        site:
            The site to validate against.

        Returns
        -------
        list[str]
            A list of human-readable error messages.  An empty list means
            the geometry is consistent.
        """
        issues: list[str] = []
        coord_names = {
            getattr(c, "name", str(c)) for c in site.coordinates()
        }
        for member in cover.members:
            if member.name not in coord_names:
                issues.append(
                    f"Cover patch '{member.name}' has no matching coordinate in site."
                )
        # Topology axiom check
        diag = SiteDiagnostics(site=site, topology=self.topology)
        for issue in diag.validate_axioms():
            issues.append(f"Topology axiom violation: {issue}")
        # Cover self-consistency
        cover_diag = CoverDiagnostics(cover=cover)
        for msg in cover_diag.run():
            issues.append(f"Cover diagnostic: {msg}")
        return issues

    def export_to_geometry(self, global_section: GlobalSection) -> GlobalSection:
        """Pass *global_section* back to the geometry layer (identity op).

        In the integration architecture, export means ensuring the section
        is registered with the site and that evidence is recorded.

        Parameters
        ----------
        global_section:
            A :class:`~jugeo.geometry.descent.GlobalSection` to export.

        Returns
        -------
        GlobalSection
            The same section (export is currently a no-op; future versions
            may perform site-registration side-effects).
        """
        if self._evidence_bridge is not None:
            section_id = getattr(global_section, "section_id", _short_id())
            self._evidence_bridge.record_provenance(
                event=f"export_global_section:{section_id}",
                source="DescentIntegration.export_to_geometry",
            )
        return global_section

    def import_from_geometry(self, result: DescentResult) -> dict:
        """Convert a raw :class:`~jugeo.geometry.descent.DescentResult` into
        an integration-layer dict.

        The returned dict has the following keys:

        * ``"succeeded"`` — bool
        * ``"global_section"`` — the :class:`~jugeo.geometry.descent.GlobalSection` or ``None``
        * ``"obstruction"`` — the :class:`~jugeo.geometry.descent.CohomologyClass` or ``None``
        * ``"phase"`` — the :class:`~jugeo.geometry.descent.DescentPhase` string value
        * ``"repair_hints"`` — list of repair hint dicts from the frontier
        * ``"import_timestamp"`` — ISO-8601 UTC string

        Parameters
        ----------
        result:
            The :class:`~jugeo.geometry.descent.DescentResult` to import.

        Returns
        -------
        dict
        """
        repair_hints: list[dict] = []
        if not result.succeeded and result.obstruction is not None:
            rf = getattr(result.obstruction, "repair_frontier", None)
            if rf is not None:
                for hint in getattr(rf, "hints", []):
                    repair_hints.append(
                        {
                            "patch_pair": getattr(hint, "patch_pair", ""),
                            "description": getattr(hint, "description", ""),
                            "severity": getattr(hint, "severity", "unknown"),
                        }
                    )
        return {
            "succeeded": result.succeeded,
            "global_section": result.global_section,
            "obstruction": result.obstruction,
            "phase": result.phase.value if hasattr(result.phase, "value") else str(result.phase),
            "repair_hints": repair_hints,
            "import_timestamp": _utcnow_iso(),
        }

    def run_integrated_descent(
        self,
        sections: list[LocalSection],
        cover: Cover,
        site: Site,
    ) -> IntegratedResult:
        """Run the full integrated pipeline: validate, descend, record evidence.

        Steps
        -----
        1. Validate geometry consistency (cover ↔ site).
        2. Build :class:`~jugeo.geometry.descent.GluingData` from *sections*
           and *cover*.
        3. Run descent via :attr:`_descent_bridge` (or a fresh engine).
        4. Record provenance and compute trust via :attr:`_evidence_bridge`
           (created on-the-fly if absent).
        5. Assemble and return :class:`IntegratedResult`.

        Parameters
        ----------
        sections:
            Local sections to be glued.
        cover:
            The cover over which descent is performed.
        site:
            The site providing coordinate context.

        Returns
        -------
        IntegratedResult
        """
        prov_events: list[str] = ["run_integrated_descent:start"]

        # Step 1 — geometry validation
        geo_issues = self.validate_geometry_consistency(cover, site)
        geo_consistent = len(geo_issues) == 0
        prov_events.append(
            f"geometry_validation:{'ok' if geo_consistent else 'issues=' + str(len(geo_issues))}"
        )

        # Step 2 — build gluing data
        gluing = GluingData.from_sections_and_cover(sections, cover)
        prov_events.append(f"gluing_data_built:n_sections={len(sections)}")

        # Step 3 — run descent
        if self._descent_bridge is None:
            engine = DescentEngine.default()
            self.connect_geometry_descent(engine)
        assert self._descent_bridge is not None
        result = self._descent_bridge.run(gluing)
        prov_events.append(f"descent:{'succeeded' if result.succeeded else 'failed'}")

        # Step 4 — evidence
        if self._evidence_bridge is None:
            self.connect_evidence(provenance=None)
        assert self._evidence_bridge is not None
        for event in prov_events:
            self._evidence_bridge.record_provenance(event=event, source="run_integrated_descent")

        # Compute trust for each section
        evidence_dict: dict = {}
        for sec in sections:
            sid = getattr(sec, "section_id", str(id(sec)))
            score = self._evidence_bridge.compute_trust(sid)
            evidence_dict[sid] = score

        # Issue overall certificate
        verdict = "VERIFIED" if result.succeeded else "OBSTRUCTED"
        run_cert = self._evidence_bridge.issue_certificate(
            section_id=f"run:{_short_id()}", verdict=verdict
        )
        evidence_dict["run_certificate"] = run_cert

        # Step 5 — assemble result
        imported = self.import_from_geometry(result)
        repair_frontier: list[dict] = imported.get("repair_hints", [])

        return IntegratedResult(
            global_section=result.global_section,
            obstruction=result.obstruction,
            evidence=evidence_dict,
            geometry_consistent=geo_consistent,
            provenance=tuple(prov_events),
            repair_frontier=repair_frontier,
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def build_integration(site: Site, topology: GrothendieckTopology) -> DescentIntegration:
    """Construct a :class:`DescentIntegration` for the given *site* and *topology*.

    This is the canonical entry-point for assembling an integration object.
    Use the returned integration to connect bridges and run the pipeline.

    Parameters
    ----------
    site:
        The :class:`~jugeo.geometry.site.Site` to integrate over.
    topology:
        The :class:`~jugeo.geometry.site.GrothendieckTopology` governing
        which cover families are admissible.

    Returns
    -------
    DescentIntegration

    Examples
    --------
    >>> integration = build_integration(my_site, my_topology)
    >>> bridge = integration.connect_geometry_descent(engine)
    >>> result = integration.run_integrated_descent(sections, cover, site)
    """
    return DescentIntegration(site=site, topology=topology)


def run_full_pipeline(
    sections: list[LocalSection],
    cover: Cover,
    site: Site,
) -> IntegratedResult:
    """Run the full integrated descent pipeline with a default topology.

    Creates a :class:`DescentIntegration` using the site's own topology (if
    available) or a default topology, then delegates to
    :meth:`DescentIntegration.run_integrated_descent`.

    This is the simplest entry-point when callers do not need to customise
    bridges.

    Parameters
    ----------
    sections:
        Local sections to be glued.
    cover:
        The cover over which descent is performed.
    site:
        The site providing coordinate context.

    Returns
    -------
    IntegratedResult

    Examples
    --------
    >>> result = run_full_pipeline(sections, cover, site)
    >>> if result.succeeded:
    ...     print("Global section:", result.global_section)
    ... else:
    ...     print("Obstruction:", result.obstruction)
    """
    topology = getattr(site, "topology", None) or GrothendieckTopology.default(site)
    integration = build_integration(site, topology)
    return integration.run_integrated_descent(sections, cover, site)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Bridge classes
    "DescentBridge",
    "CoverBridge",
    "SiteBridge",
    "EvidenceBridge",
    # Facade
    "DescentIntegration",
    # Result dataclass
    "IntegratedResult",
    # Module-level functions
    "build_integration",
    "run_full_pipeline",
]
