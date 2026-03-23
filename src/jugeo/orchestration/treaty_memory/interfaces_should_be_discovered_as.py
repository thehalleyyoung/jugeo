from __future__ import annotations

"""
theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"
Chapter 48 §1 — "Interfaces should be discovered as well as checked"

# copilot: This module implements the interface-discovery layer of the treaty-memory
# subsystem described in Chapter 48 of theory2.tex.  The central thesis of §1 is that
# runtime interfaces between negotiating agents cannot be known a-priori; they must be
# *discovered* via active probing (InterfaceProbe / InterfaceWitness) and then
# *checked* against the capability contract declared in the treaty record.  A pure
# static-checking approach leaves dynamic capability negotiation blind to emergent
# behaviour.  This module therefore pairs a lightweight coordinator that issues probes
# and accumulates witness observations with a post-hoc analyser that clusters the
# resulting InterfaceRecord set into capability groups, scores each record, and emits
# a compact AnalysisReport.

Design
------
The design follows the three-phase pattern from §1.3:

  Phase 1 – Probe issuance
      InterfaceDiscoveryCoordinator mints InterfaceProbe objects (immutable) and
      dispatches them to target agents.  Each probe carries a typed payload and a
      monotonic timestamp so that latency can be measured when the witness observation
      returns.

  Phase 2 – Witness observation
      When a target agent responds, the coordinator calls ``record_result``, which
      synthesises an InterfaceWitness and derives an InterfaceRecord.  The confidence
      score on the record is computed from both the witness success flag and the
      empirical Jaccard similarity between the observed capabilities and those declared
      in the treaty contract.

  Phase 3 – Analysis
      InterfaceDiscoveryAnalyzer.analyze() ingests the accumulated InterfaceRecord list,
      clusters records by shared capabilities, scores them with a configurable weight
      vector, detects capability overlaps between distinct interface pairs, and writes
      the summary into an AnalysisReport.

All public dataclasses are frozen (slots=True) to guarantee that records are treated as
values once created; the coordinator itself is mutable.

Threading note: InterfaceDiscoveryCoordinator is not thread-safe.  External callers are
expected to hold a lock around ``register_probe`` / ``record_result`` if multiple threads
share a single coordinator instance.
"""

import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── Optional jugeo imports ────────────────────────────────────────────────────

try:
    from jugeo.orchestration.treaty_memory.models import TreatyRecord  # type: ignore
except (ImportError, Exception):
    TreatyRecord: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.manifest import TreatyManifest  # type: ignore
except (ImportError, Exception):
    TreatyManifest: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.core.agent import AgentIdentity  # type: ignore
except (ImportError, Exception):
    AgentIdentity: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.core.capability import CapabilitySet  # type: ignore
except (ImportError, Exception):
    CapabilitySet: Any = None  # type: ignore[assignment,misc]

# ─── Module-level logger ───────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    # Dataclasses
    "InterfaceRecord",
    "InterfaceProbe",
    "InterfaceWitness",
    "AnalysisReport",
    # Core classes
    "InterfaceDiscoveryAnalyzer",
    "InterfaceDiscoveryCoordinator",
    # Helper functions
    "make_interface_record",
    "score_interface",
    "merge_interface_records",
    "interface_capability_jaccard",
    # Extra helpers
    "normalize_capabilities",
    "capability_coverage",
    "compute_probe_latency",
    "confidence_from_witness",
    "top_n_records",
    "group_records_by_party",
    "records_to_dict",
    # Constants (selected)
    "DEFAULT_CONFIDENCE_FLOOR",
    "DEFAULT_CONFIDENCE_CEILING",
    "PROBE_TYPE_PING",
    "PROBE_TYPE_CAPABILITY_QUERY",
    "PROBE_TYPE_CONTRACT_CHECK",
    "MAX_CAPABILITIES_PER_RECORD",
    "JACCARD_OVERLAP_THRESHOLD",
]

# ─── Constants ─────────────────────────────────────────────────────────────────

# Confidence bounds – any derived confidence is clamped to this range to avoid
# degenerate 0.0 or 1.0 values that would collapse scoring distributions.
DEFAULT_CONFIDENCE_FLOOR: float = 0.01
DEFAULT_CONFIDENCE_CEILING: float = 0.99

# Probe type tokens used as the ``probe_type`` field on InterfaceProbe.
PROBE_TYPE_PING: str = "ping"
PROBE_TYPE_CAPABILITY_QUERY: str = "capability_query"
PROBE_TYPE_CONTRACT_CHECK: str = "contract_check"
PROBE_TYPE_HEARTBEAT: str = "heartbeat"
PROBE_TYPE_ECHO: str = "echo"

# Hard cap on the number of capability tokens stored in an InterfaceRecord.
# Enforced by make_interface_record and merge_interface_records.
MAX_CAPABILITIES_PER_RECORD: int = 128

# Two records whose capability Jaccard similarity exceeds this threshold are
# considered "overlapping" by find_overlaps.
JACCARD_OVERLAP_THRESHOLD: float = 0.5

# Minimum number of records required before the analyzer will emit cluster data.
# Below this threshold the cluster_map in the AnalysisReport will be empty.
MIN_RECORDS_FOR_CLUSTERING: int = 2

# Weight keys used by score_interface.
WEIGHT_KEY_CONFIDENCE: str = "confidence"
WEIGHT_KEY_CAPABILITY_COUNT: str = "capability_count"
WEIGHT_KEY_RECENCY: str = "recency"
WEIGHT_KEY_PARTY_COUNT: str = "party_count"

# Default weight vector for score_interface.
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    WEIGHT_KEY_CONFIDENCE: 0.50,
    WEIGHT_KEY_CAPABILITY_COUNT: 0.20,
    WEIGHT_KEY_RECENCY: 0.20,
    WEIGHT_KEY_PARTY_COUNT: 0.10,
}

# Recency half-life in seconds.  Records older than this contribute less than
# half of their full recency score.
RECENCY_HALF_LIFE_SECONDS: float = 3600.0

# Sentinel string used when a probe result dict is missing an expected field.
MISSING_FIELD_SENTINEL: str = "__missing__"

# Maximum number of top records returned in AnalysisReport.top_interfaces.
TOP_INTERFACES_LIMIT: int = 10

# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InterfaceRecord:
    """An immutable snapshot of a discovered interface between two or more agents.

    InterfaceRecord is the core value type produced by interface discovery.  Each
    record captures:

    * **interface_id** – a universally unique identifier minted at record creation
      time (typically a UUID4 string).  Because the record is frozen, this ID is
      stable for the lifetime of the object and safe to use as a dict key or set
      member.

    * **name** – a human-readable label for the interface, usually derived from the
      probe payload or the treaty contract name.

    * **parties** – an ordered tuple of agent identifiers that participate in the
      interface.  The ordering is significant: ``parties[0]`` is conventionally the
      initiating agent and ``parties[1]`` (if present) is the responding agent.

    * **capabilities** – a tuple of capability token strings observed during
      discovery.  Tokens are normalised to lower-case stripped strings before
      storage.  The order is not semantically significant.

    * **discovered_at** – a POSIX monotonic timestamp (``time.monotonic()``) recorded
      when the record was first synthesised.  Used by the recency scoring component.

    * **confidence** – a float in ``[DEFAULT_CONFIDENCE_FLOOR,
      DEFAULT_CONFIDENCE_CEILING]`` expressing the discovery system's confidence that
      the observed capabilities are accurate.  Confidence is derived from the
      empirical Jaccard similarity between observed and declared capabilities, plus a
      witness success flag contribution.

    Equality and hashing are based on all fields (standard frozen-dataclass
    behaviour).  Two records with different ``interface_id`` values are never equal
    even if all other fields match; this is intentional — each discovery event is
    distinct.
    """

    interface_id: str
    name: str
    parties: tuple[str, ...]
    capabilities: tuple[str, ...]
    discovered_at: float
    confidence: float


@dataclass(frozen=True, slots=True)
class InterfaceProbe:
    """An immutable description of a capability-discovery probe sent to a target agent.

    Probes are created by InterfaceDiscoveryCoordinator and dispatched to agents.
    Because InterfaceProbe is frozen, it can be logged, cached, or placed in a set
    without risk of mutation.

    Fields
    ------
    probe_id : str
        A UUID4 string identifying this specific probe issuance.  Used to correlate
        the probe with its InterfaceWitness observations.

    target_agent : str
        The identifier of the agent to which the probe is directed.  Must be a
        non-empty string.

    probe_type : str
        One of the ``PROBE_TYPE_*`` constants defined in this module.  Determines the
        semantics of ``payload`` and the expected response shape.

    payload : dict
        Arbitrary JSON-serialisable payload delivered to the target.  The coordinator
        populates this with probe-type-specific fields (e.g., a list of capability
        tokens to check for ``PROBE_TYPE_CAPABILITY_QUERY``).  Because the dataclass
        is frozen, ``payload`` must not be mutated after construction; use a new probe
        instead.

    issued_at : float
        POSIX monotonic timestamp recorded when the probe was minted.  Subtracted from
        the witness timestamp to compute probe round-trip latency.
    """

    probe_id: str
    target_agent: str
    probe_type: str
    payload: dict
    issued_at: float


@dataclass(frozen=True, slots=True)
class InterfaceWitness:
    """An immutable record of what was actually observed when a probe returned.

    InterfaceWitness objects are ephemeral intermediates: the coordinator creates one
    per probe result and immediately derives an InterfaceRecord from it.  Witnesses
    are retained in the coordinator's internal list for post-hoc audit, but they do
    not appear in AnalysisReport.

    Fields
    ------
    witness_id : str
        UUID4 identifying this witness observation.

    probe_id : str
        The ``probe_id`` of the InterfaceProbe that triggered this observation.
        Foreign-key relationship to the coordinator's probe registry.

    observed_capabilities : tuple[str, ...]
        The capability tokens actually returned by the target agent.  These may differ
        from any declared capabilities in the treaty contract — the Jaccard similarity
        between the two sets drives the confidence score on the derived
        InterfaceRecord.

    success : bool
        ``True`` if the target agent acknowledged the probe without error; ``False``
        if a timeout, rejection, or error response was received.  A ``False`` witness
        contributes zero to the confidence calculation, effectively producing a
        near-floor confidence record.

    timestamp : float
        POSIX monotonic timestamp recorded when the probe response was received.
        Compared with ``InterfaceProbe.issued_at`` to compute latency.
    """

    witness_id: str
    probe_id: str
    observed_capabilities: tuple[str, ...]
    success: bool
    timestamp: float


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """A frozen summary produced by InterfaceDiscoveryAnalyzer.analyze().

    AnalysisReport captures the high-level findings of a single analysis pass over a
    list of InterfaceRecord objects.  It is designed to be serialisable (all fields
    are either primitives, tuples, or dicts containing primitives/tuples) for easy
    transmission over a message bus or storage in a treaty-memory archive.

    Fields
    ------
    report_id : str
        UUID4 uniquely identifying this analysis run.

    records_analyzed : int
        The total number of InterfaceRecord objects passed to ``analyze()``.

    top_interfaces : tuple
        Up to ``TOP_INTERFACES_LIMIT`` InterfaceRecord objects, sorted descending by
        their composite score.  The scoring uses ``DEFAULT_SCORE_WEIGHTS`` unless the
        caller configured a custom weight vector on the analyzer.

    cluster_map : dict
        Mapping from capability token → list of ``interface_id`` strings whose records
        contain that capability.  Empty if fewer than ``MIN_RECORDS_FOR_CLUSTERING``
        records were analysed.

    generated_at : float
        POSIX monotonic timestamp recorded at the end of the ``analyze()`` call.
    """

    report_id: str
    records_analyzed: int
    top_interfaces: tuple
    cluster_map: dict
    generated_at: float


# ─── Mutable coordinator state ─────────────────────────────────────────────────


@dataclass(slots=True)
class _CoordinatorState:
    """Internal mutable state bag for InterfaceDiscoveryCoordinator.

    Separating mutable state into its own (non-frozen) dataclass keeps the
    coordinator class clean while making the state shape explicit and
    introspectable.

    Attributes
    ----------
    probes : dict[str, InterfaceProbe]
        Registry of all probes issued, keyed by probe_id.

    witnesses : list[InterfaceWitness]
        Chronologically ordered list of all witness observations received.

    records : list[InterfaceRecord]
        Derived interface records, one per successful witness correlation.

    pending_probe_ids : list[str]
        Probe IDs that have been issued but not yet resolved.

    round_count : int
        Number of completed discovery rounds (incremented by run_discovery_round).
    """

    probes: dict = field(default_factory=dict)
    witnesses: list = field(default_factory=list)
    records: list = field(default_factory=list)
    pending_probe_ids: list = field(default_factory=list)
    round_count: int = 0


# ─── Core classes ──────────────────────────────────────────────────────────────


class InterfaceDiscoveryAnalyzer:
    """Post-hoc analyser that turns a bag of InterfaceRecord objects into insight.

    InterfaceDiscoveryAnalyzer is a stateless service object: all persistent
    configuration is held in ``__init__`` parameters and never mutated after
    construction.  This makes it safe to share across threads (or coroutines) as
    long as callers do not mutate the input record lists during analysis.

    The three main concerns addressed by this class are:

    1. **Scoring** – ``score_record`` maps an InterfaceRecord onto a scalar in [0, 1]
       using a weighted combination of confidence, capability breadth, recency, and
       party count.  The weight vector is configurable.

    2. **Clustering** – ``cluster_by_capability`` builds an inverted index from
       capability token → records, enabling downstream consumers to find all
       interfaces that expose a given capability without a linear scan.

    3. **Overlap detection** – ``find_overlaps`` identifies pairs of records whose
       capability Jaccard similarity exceeds ``JACCARD_OVERLAP_THRESHOLD``, which
       often indicates redundant or conflicting interface declarations that a treaty
       negotiator should resolve.

    Parameters
    ----------
    weights : dict[str, float] | None
        Score weights.  If None, ``DEFAULT_SCORE_WEIGHTS`` is used.  Keys must be a
        subset of ``{WEIGHT_KEY_CONFIDENCE, WEIGHT_KEY_CAPABILITY_COUNT,
        WEIGHT_KEY_RECENCY, WEIGHT_KEY_PARTY_COUNT}``.  Missing keys default to 0.0.

    overlap_threshold : float
        Jaccard similarity threshold above which two records are considered
        overlapping.  Defaults to ``JACCARD_OVERLAP_THRESHOLD``.

    top_n : int
        Maximum number of top-scoring records to include in the AnalysisReport.
        Defaults to ``TOP_INTERFACES_LIMIT``.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        overlap_threshold: float = JACCARD_OVERLAP_THRESHOLD,
        top_n: int = TOP_INTERFACES_LIMIT,
    ) -> None:
        self._weights: dict[str, float] = weights if weights is not None else dict(DEFAULT_SCORE_WEIGHTS)
        self._overlap_threshold: float = overlap_threshold
        self._top_n: int = top_n
        log.debug(
            "InterfaceDiscoveryAnalyzer initialised: weights=%s overlap_threshold=%s top_n=%s",
            self._weights,
            self._overlap_threshold,
            self._top_n,
        )

    # ------------------------------------------------------------------
    def analyze(self, records: list[InterfaceRecord]) -> AnalysisReport:
        """Run a full analysis pass over a list of InterfaceRecord objects.

        Steps
        -----
        1. Score every record using ``score_record``.
        2. Sort records descending by score and take the top-n.
        3. Build capability cluster map via ``cluster_by_capability``.
        4. Detect overlapping pairs via ``find_overlaps`` (logged at DEBUG level
           but not surfaced in the report to keep the report compact).
        5. Assemble and return an AnalysisReport.

        Parameters
        ----------
        records : list[InterfaceRecord]
            The records to analyse.  May be empty, in which case an empty report is
            returned.

        Returns
        -------
        AnalysisReport
            A frozen report object.  The ``top_interfaces`` tuple contains at most
            ``self._top_n`` records.
        """
        log.info("Analyzing %d interface records", len(records))

        scored = sorted(records, key=self.score_record, reverse=True)
        top_interfaces = tuple(scored[: self._top_n])

        cluster_map: dict[str, list[str]] = {}
        if len(records) >= MIN_RECORDS_FOR_CLUSTERING:
            raw_clusters = self.cluster_by_capability(records)
            cluster_map = {cap: [r.interface_id for r in recs] for cap, recs in raw_clusters.items()}

        overlaps = self.find_overlaps(records)
        if overlaps:
            log.debug("Found %d overlapping interface pairs", len(overlaps))

        report = AnalysisReport(
            report_id=str(uuid.uuid4()),
            records_analyzed=len(records),
            top_interfaces=top_interfaces,
            cluster_map=cluster_map,
            generated_at=time.monotonic(),
        )
        log.info("AnalysisReport generated: report_id=%s", report.report_id)
        return report

    # ------------------------------------------------------------------
    def score_record(self, record: InterfaceRecord) -> float:
        """Compute a scalar quality score for a single InterfaceRecord.

        The score is a weighted sum of four normalised sub-scores:

        * **confidence** – raw confidence value (already in [0, 1]).
        * **capability_count** – sigmoid-normalised count of capabilities.  The
          sigmoid is centred at 8 capabilities (considered "average breadth") and
          scaled so that 0 capabilities gives ~0 and 32+ gives ~1.
        * **recency** – exponential decay based on ``record.discovered_at`` relative
          to ``time.monotonic()``.  Uses ``RECENCY_HALF_LIFE_SECONDS``.
        * **party_count** – normalised party count, capped at 4.

        The final score is clamped to [0.0, 1.0].

        Parameters
        ----------
        record : InterfaceRecord
            The record to score.

        Returns
        -------
        float
            Score in [0.0, 1.0].
        """
        w = self._weights

        conf_score = record.confidence

        cap_count = len(record.capabilities)
        # Sigmoid centred at 8: score ≈ 0.5 at 8 capabilities, approaches 1 at 32+.
        cap_score = 1.0 / (1.0 + math.exp(-(cap_count - 8) / 4.0))

        age_seconds = max(0.0, time.monotonic() - record.discovered_at)
        recency_score = math.exp(-age_seconds * math.log(2.0) / RECENCY_HALF_LIFE_SECONDS)

        party_score = min(len(record.parties) / 4.0, 1.0)

        raw = (
            w.get(WEIGHT_KEY_CONFIDENCE, 0.0) * conf_score
            + w.get(WEIGHT_KEY_CAPABILITY_COUNT, 0.0) * cap_score
            + w.get(WEIGHT_KEY_RECENCY, 0.0) * recency_score
            + w.get(WEIGHT_KEY_PARTY_COUNT, 0.0) * party_score
        )
        return max(0.0, min(1.0, raw))

    # ------------------------------------------------------------------
    def cluster_by_capability(
        self, records: list[InterfaceRecord]
    ) -> dict[str, list[InterfaceRecord]]:
        """Build an inverted index from capability token to records.

        Each entry in the returned dict maps a capability token string to the list of
        InterfaceRecord objects that declare that capability.  Records with zero
        capabilities do not appear in the index.

        Parameters
        ----------
        records : list[InterfaceRecord]

        Returns
        -------
        dict[str, list[InterfaceRecord]]
            Capability → records mapping.  The lists preserve insertion order, which
            corresponds to the order of ``records``.
        """
        index: dict[str, list[InterfaceRecord]] = {}
        for rec in records:
            for cap in rec.capabilities:
                index.setdefault(cap, []).append(rec)
        return index

    # ------------------------------------------------------------------
    def find_overlaps(
        self, records: list[InterfaceRecord]
    ) -> list[tuple[InterfaceRecord, InterfaceRecord, float]]:
        """Find all pairs of records with Jaccard similarity above the threshold.

        This is an O(n²) operation.  For large record sets consider sampling or
        pre-filtering by party set before calling.

        Parameters
        ----------
        records : list[InterfaceRecord]

        Returns
        -------
        list[tuple[InterfaceRecord, InterfaceRecord, float]]
            Each element is ``(record_a, record_b, jaccard)`` where ``jaccard >
            self._overlap_threshold``.  Pairs are unique (each unordered pair appears
            exactly once).
        """
        overlaps: list[tuple[InterfaceRecord, InterfaceRecord, float]] = []
        for i, a in enumerate(records):
            for b in records[i + 1 :]:
                j = interface_capability_jaccard(a, b)
                if j > self._overlap_threshold:
                    overlaps.append((a, b, j))
        return overlaps


# ─── Coordinator ───────────────────────────────────────────────────────────────


class InterfaceDiscoveryCoordinator:
    """Stateful coordinator that issues probes and accumulates interface records.

    The coordinator is the operational heart of the discovery system.  It manages
    the full probe lifecycle:

    1. A caller registers a new probe via ``register_probe``.
    2. The probe is dispatched (externally, outside this module) to the target agent.
    3. When the agent responds, the caller invokes ``record_result`` with the probe ID
       and a result dict.  The coordinator mints an InterfaceWitness, derives an
       InterfaceRecord, and stores both.
    4. ``run_discovery_round`` synthesises all pending probes into a consolidated list
       of new records and increments the round counter.
    5. ``report`` returns a summary dict suitable for logging or forwarding to the
       treaty-memory archive.

    The coordinator deliberately does *not* perform network I/O; it is a pure
    bookkeeping object.  Callers are responsible for the actual probe dispatch loop.

    Parameters
    ----------
    coordinator_id : str | None
        Optional stable identifier for this coordinator instance.  Defaults to a
        fresh UUID4.

    default_parties : tuple[str, ...] | None
        If provided, these parties are prepended to the ``parties`` list on every
        derived InterfaceRecord.  Useful when the coordinator always runs on behalf
        of a fixed initiating agent.
    """

    def __init__(
        self,
        coordinator_id: str | None = None,
        default_parties: tuple[str, ...] | None = None,
    ) -> None:
        self.coordinator_id: str = coordinator_id or str(uuid.uuid4())
        self.default_parties: tuple[str, ...] = default_parties or ()
        self._state = _CoordinatorState()
        log.debug("InterfaceDiscoveryCoordinator created: id=%s", self.coordinator_id)

    # ------------------------------------------------------------------
    def register_probe(self, probe: InterfaceProbe) -> None:
        """Register a new probe and mark it as pending.

        Parameters
        ----------
        probe : InterfaceProbe
            The probe to register.  Must have a unique ``probe_id``.

        Raises
        ------
        ValueError
            If a probe with the same ``probe_id`` is already registered.
        """
        if probe.probe_id in self._state.probes:
            raise ValueError(f"Probe {probe.probe_id!r} is already registered")
        self._state.probes[probe.probe_id] = probe
        self._state.pending_probe_ids.append(probe.probe_id)
        log.debug("Registered probe %s → %s", probe.probe_id, probe.target_agent)

    # ------------------------------------------------------------------
    def record_result(self, probe_id: str, result_dict: dict) -> InterfaceRecord:
        """Resolve a probe result and derive an InterfaceRecord.

        This method:

        1. Looks up the original InterfaceProbe by ``probe_id``.
        2. Extracts ``observed_capabilities`` and ``success`` from ``result_dict``.
        3. Mints an InterfaceWitness and stores it.
        4. Computes confidence from the witness.
        5. Synthesises and stores an InterfaceRecord.
        6. Removes the probe from ``pending_probe_ids``.

        Parameters
        ----------
        probe_id : str
            The probe to resolve.
        result_dict : dict
            Expected keys:

            * ``"capabilities"`` – list[str] of observed capability tokens.
            * ``"success"`` – bool indicating probe success.
            * ``"name"`` – optional str naming the interface.
            * ``"declared_capabilities"`` – optional list[str] of declared
              capabilities to compare against (used for Jaccard confidence).

        Returns
        -------
        InterfaceRecord
            The newly synthesised record.

        Raises
        ------
        KeyError
            If ``probe_id`` is not found in the probe registry.
        """
        probe = self._state.probes[probe_id]

        raw_caps: list[str] = result_dict.get("capabilities", [])
        success: bool = bool(result_dict.get("success", False))
        name: str = result_dict.get("name", f"interface-{probe.target_agent}")
        declared: list[str] = result_dict.get("declared_capabilities", [])

        observed_caps = tuple(normalize_capabilities(raw_caps))

        witness = InterfaceWitness(
            witness_id=str(uuid.uuid4()),
            probe_id=probe_id,
            observed_capabilities=observed_caps,
            success=success,
            timestamp=time.monotonic(),
        )
        self._state.witnesses.append(witness)

        conf = confidence_from_witness(witness, declared_capabilities=declared)
        parties = self.default_parties + (probe.target_agent,)

        record = InterfaceRecord(
            interface_id=str(uuid.uuid4()),
            name=name,
            parties=parties,
            capabilities=observed_caps[:MAX_CAPABILITIES_PER_RECORD],
            discovered_at=witness.timestamp,
            confidence=conf,
        )
        self._state.records.append(record)

        if probe_id in self._state.pending_probe_ids:
            self._state.pending_probe_ids.remove(probe_id)

        log.debug(
            "Resolved probe %s → record %s (conf=%.3f, caps=%d)",
            probe_id,
            record.interface_id,
            conf,
            len(record.capabilities),
        )
        return record

    # ------------------------------------------------------------------
    def run_discovery_round(self) -> list[InterfaceRecord]:
        """Mark the end of a discovery round and return this round's new records.

        All records accumulated since the previous call to this method are
        considered part of this round.  The round counter is incremented and the
        pending-probe list is cleared (probes that never received a result are
        dropped with a warning).

        Returns
        -------
        list[InterfaceRecord]
            Newly accumulated records.  May be empty if no probes were resolved.
        """
        if self._state.pending_probe_ids:
            log.warning(
                "Round %d ending with %d unresolved probes: %s",
                self._state.round_count,
                len(self._state.pending_probe_ids),
                self._state.pending_probe_ids,
            )
            self._state.pending_probe_ids.clear()

        round_records = list(self._state.records)
        self._state.round_count += 1
        log.info(
            "Discovery round %d complete: %d records total",
            self._state.round_count,
            len(round_records),
        )
        return round_records

    # ------------------------------------------------------------------
    def report(self) -> dict:
        """Return a plain-dict summary of coordinator state.

        The dict is intended for logging or JSON serialisation.  It does not contain
        full InterfaceRecord objects (use the AnalysisReport for that); instead it
        surfaces aggregate statistics.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``round_count``, ``total_probes``,
            ``total_witnesses``, ``total_records``, ``pending_probes``,
            ``mean_confidence`` (or None if no records), ``capability_counts``.
        """
        confidences = [r.confidence for r in self._state.records]
        mean_conf = statistics.mean(confidences) if confidences else None
        cap_counts = [len(r.capabilities) for r in self._state.records]
        return {
            "coordinator_id": self.coordinator_id,
            "round_count": self._state.round_count,
            "total_probes": len(self._state.probes),
            "total_witnesses": len(self._state.witnesses),
            "total_records": len(self._state.records),
            "pending_probes": list(self._state.pending_probe_ids),
            "mean_confidence": mean_conf,
            "capability_counts": cap_counts,
        }


# ─── Helper functions ──────────────────────────────────────────────────────────


def make_interface_record(
    name: str,
    parties: tuple[str, ...] | list[str],
    capabilities: tuple[str, ...] | list[str],
    confidence: float,
) -> InterfaceRecord:
    """Construct an InterfaceRecord with auto-generated ID and timestamp.

    This is the preferred factory for tests and quick one-off construction; it
    handles normalisation, clamping, and ID generation automatically.

    Parameters
    ----------
    name : str
        Human-readable interface name.
    parties : tuple[str, ...] | list[str]
        Participating agent identifiers.
    capabilities : tuple[str, ...] | list[str]
        Capability token strings.  Will be normalised and truncated to
        ``MAX_CAPABILITIES_PER_RECORD``.
    confidence : float
        Raw confidence value.  Will be clamped to
        ``[DEFAULT_CONFIDENCE_FLOOR, DEFAULT_CONFIDENCE_CEILING]``.

    Returns
    -------
    InterfaceRecord
    """
    norm_caps = tuple(normalize_capabilities(list(capabilities)))[:MAX_CAPABILITIES_PER_RECORD]
    clamped_conf = max(DEFAULT_CONFIDENCE_FLOOR, min(DEFAULT_CONFIDENCE_CEILING, confidence))
    return InterfaceRecord(
        interface_id=str(uuid.uuid4()),
        name=name,
        parties=tuple(parties),
        capabilities=norm_caps,
        discovered_at=time.monotonic(),
        confidence=clamped_conf,
    )


def score_interface(record: InterfaceRecord, weights: dict[str, float]) -> float:
    """Score an InterfaceRecord using an explicit weight dict.

    Convenience wrapper around InterfaceDiscoveryAnalyzer.score_record that avoids
    constructing a full analyser instance.

    Parameters
    ----------
    record : InterfaceRecord
    weights : dict[str, float]
        Weight dict.  See ``DEFAULT_SCORE_WEIGHTS`` for valid keys.

    Returns
    -------
    float
        Score in [0.0, 1.0].
    """
    analyzer = InterfaceDiscoveryAnalyzer(weights=weights)
    return analyzer.score_record(record)


def merge_interface_records(
    a: InterfaceRecord, b: InterfaceRecord
) -> InterfaceRecord:
    """Merge two InterfaceRecord objects into a single record.

    The merge strategy is:

    * **interface_id** – new UUID4 (the merged record is a distinct entity).
    * **name** – ``"{a.name}+{b.name}"``.
    * **parties** – union of both party tuples, preserving order and de-duplicating.
    * **capabilities** – union of both capability sets, normalised, truncated to
      ``MAX_CAPABILITIES_PER_RECORD``.
    * **discovered_at** – minimum of both timestamps (the older origin is used).
    * **confidence** – geometric mean of both confidence values.

    Parameters
    ----------
    a : InterfaceRecord
    b : InterfaceRecord

    Returns
    -------
    InterfaceRecord
        A new record representing the merged interface.
    """
    merged_parties: list[str] = list(a.parties)
    for p in b.parties:
        if p not in merged_parties:
            merged_parties.append(p)

    merged_caps_set: list[str] = list(a.capabilities)
    for c in b.capabilities:
        if c not in merged_caps_set:
            merged_caps_set.append(c)
    merged_caps = tuple(merged_caps_set[:MAX_CAPABILITIES_PER_RECORD])

    geo_conf = math.sqrt(a.confidence * b.confidence)
    clamped_conf = max(DEFAULT_CONFIDENCE_FLOOR, min(DEFAULT_CONFIDENCE_CEILING, geo_conf))

    return InterfaceRecord(
        interface_id=str(uuid.uuid4()),
        name=f"{a.name}+{b.name}",
        parties=tuple(merged_parties),
        capabilities=merged_caps,
        discovered_at=min(a.discovered_at, b.discovered_at),
        confidence=clamped_conf,
    )


def interface_capability_jaccard(
    a: InterfaceRecord, b: InterfaceRecord
) -> float:
    """Compute the Jaccard similarity between the capability sets of two records.

    Jaccard similarity = |A ∩ B| / |A ∪ B|.

    Returns 0.0 if both records have empty capability sets (undefined Jaccard by
    convention returns 0 rather than NaN).

    Parameters
    ----------
    a : InterfaceRecord
    b : InterfaceRecord

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].
    """
    set_a = set(a.capabilities)
    set_b = set(b.capabilities)
    union = set_a | set_b
    if not union:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / len(union)


# ─── Extra helpers ─────────────────────────────────────────────────────────────


def normalize_capabilities(caps: list[str]) -> list[str]:
    """Normalise a list of capability token strings.

    Normalisation steps applied to each token:

    1. Strip leading/trailing whitespace.
    2. Lower-case.
    3. Drop empty strings.
    4. De-duplicate while preserving first-occurrence order.

    Parameters
    ----------
    caps : list[str]

    Returns
    -------
    list[str]
        Normalised, de-duplicated list in first-occurrence order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for c in caps:
        normalised = c.strip().lower()
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


def capability_coverage(
    record: InterfaceRecord, required: tuple[str, ...] | list[str]
) -> float:
    """Compute the fraction of required capabilities present in a record.

    Returns 1.0 if ``required`` is empty (vacuously true).

    Parameters
    ----------
    record : InterfaceRecord
    required : tuple[str, ...] | list[str]
        The set of capabilities that must be covered.

    Returns
    -------
    float
        Coverage in [0.0, 1.0].
    """
    if not required:
        return 1.0
    rec_set = set(record.capabilities)
    req_set = set(normalize_capabilities(list(required)))
    return len(rec_set & req_set) / len(req_set)


def compute_probe_latency(probe: InterfaceProbe, witness: InterfaceWitness) -> float:
    """Return the probe round-trip latency in seconds.

    Parameters
    ----------
    probe : InterfaceProbe
    witness : InterfaceWitness

    Returns
    -------
    float
        Latency in seconds.  May be negative if clocks are skewed (caller should
        clamp to 0.0 if needed).
    """
    return witness.timestamp - probe.issued_at


def confidence_from_witness(
    witness: InterfaceWitness,
    declared_capabilities: list[str] | None = None,
) -> float:
    """Derive a confidence score from an InterfaceWitness observation.

    Algorithm
    ---------
    * If ``witness.success`` is False, return ``DEFAULT_CONFIDENCE_FLOOR``.
    * If ``declared_capabilities`` is provided and non-empty, confidence =
      0.4 + 0.6 * Jaccard(observed, declared).  This ensures that even a perfect
      match stays below ``DEFAULT_CONFIDENCE_CEILING`` while a zero-match still
      yields a non-trivial base confidence.
    * If no declared capabilities are provided, confidence = 0.7 (moderate
      confidence: the probe succeeded but we cannot validate against a contract).

    The result is always clamped to ``[DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_CONFIDENCE_CEILING]``.

    Parameters
    ----------
    witness : InterfaceWitness
    declared_capabilities : list[str] | None

    Returns
    -------
    float
    """
    if not witness.success:
        return DEFAULT_CONFIDENCE_FLOOR

    if declared_capabilities:
        norm_declared = set(normalize_capabilities(declared_capabilities))
        norm_observed = set(witness.observed_capabilities)
        union = norm_declared | norm_observed
        if union:
            jaccard = len(norm_declared & norm_observed) / len(union)
        else:
            jaccard = 0.0
        raw = 0.4 + 0.6 * jaccard
    else:
        raw = 0.7

    return max(DEFAULT_CONFIDENCE_FLOOR, min(DEFAULT_CONFIDENCE_CEILING, raw))


def top_n_records(
    records: list[InterfaceRecord],
    n: int,
    weights: dict[str, float] | None = None,
) -> list[InterfaceRecord]:
    """Return the top-n records sorted descending by composite score.

    Parameters
    ----------
    records : list[InterfaceRecord]
    n : int
        Maximum number of records to return.
    weights : dict[str, float] | None
        Weight dict for scoring.  Defaults to ``DEFAULT_SCORE_WEIGHTS``.

    Returns
    -------
    list[InterfaceRecord]
    """
    w = weights or DEFAULT_SCORE_WEIGHTS
    analyzer = InterfaceDiscoveryAnalyzer(weights=w, top_n=n)
    scored = sorted(records, key=analyzer.score_record, reverse=True)
    return scored[:n]


def group_records_by_party(
    records: list[InterfaceRecord],
) -> dict[str, list[InterfaceRecord]]:
    """Build a party → records index from a list of InterfaceRecord objects.

    Each record appears once per party it declares, so a record with three parties
    will appear in three groups.

    Parameters
    ----------
    records : list[InterfaceRecord]

    Returns
    -------
    dict[str, list[InterfaceRecord]]
    """
    index: dict[str, list[InterfaceRecord]] = {}
    for rec in records:
        for party in rec.parties:
            index.setdefault(party, []).append(rec)
    return index


def records_to_dict(records: list[InterfaceRecord]) -> list[dict]:
    """Serialise a list of InterfaceRecord objects to plain dicts.

    Useful for JSON serialisation or logging.  Fields ``parties`` and
    ``capabilities`` are converted to lists.

    Parameters
    ----------
    records : list[InterfaceRecord]

    Returns
    -------
    list[dict]
    """
    return [
        {
            "interface_id": r.interface_id,
            "name": r.name,
            "parties": list(r.parties),
            "capabilities": list(r.capabilities),
            "discovered_at": r.discovered_at,
            "confidence": r.confidence,
        }
        for r in records
    ]


def _make_probe(
    target_agent: str,
    probe_type: str = PROBE_TYPE_CAPABILITY_QUERY,
    payload: dict | None = None,
) -> InterfaceProbe:
    """Internal factory for InterfaceProbe objects.

    Parameters
    ----------
    target_agent : str
    probe_type : str
    payload : dict | None

    Returns
    -------
    InterfaceProbe
    """
    return InterfaceProbe(
        probe_id=str(uuid.uuid4()),
        target_agent=target_agent,
        probe_type=probe_type,
        payload=payload or {},
        issued_at=time.monotonic(),
    )


def _simulate_probe_result(
    probe: InterfaceProbe,
    capabilities: list[str],
    success: bool = True,
    name: str | None = None,
) -> dict:
    """Build a synthetic result dict as if a target agent had responded.

    For use in tests and smoke testing only.

    Parameters
    ----------
    probe : InterfaceProbe
    capabilities : list[str]
    success : bool
    name : str | None

    Returns
    -------
    dict
    """
    return {
        "capabilities": capabilities,
        "success": success,
        "name": name or f"iface-{probe.target_agent}",
        "declared_capabilities": capabilities,
    }


# ─── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    print(f"[smoke] {__file__}")

    # ── 1. Factory and basic record creation ──────────────────────────────────
    rec_a = make_interface_record(
        name="alpha",
        parties=["agent-001", "agent-002"],
        capabilities=["read", "write", "subscribe"],
        confidence=0.85,
    )
    rec_b = make_interface_record(
        name="beta",
        parties=["agent-002", "agent-003"],
        capabilities=["write", "publish", "notify"],
        confidence=0.60,
    )
    rec_c = make_interface_record(
        name="gamma",
        parties=["agent-001"],
        capabilities=["read", "audit"],
        confidence=0.45,
    )
    assert rec_a.confidence == 0.85, "confidence not preserved"
    assert "read" in rec_a.capabilities
    print(f"  [1] Records: {rec_a.name}, {rec_b.name}, {rec_c.name} — OK")

    # ── 2. Jaccard similarity ─────────────────────────────────────────────────
    j_ab = interface_capability_jaccard(rec_a, rec_b)
    j_ac = interface_capability_jaccard(rec_a, rec_c)
    assert 0.0 <= j_ab <= 1.0
    assert 0.0 <= j_ac <= 1.0
    print(f"  [2] Jaccard a↔b={j_ab:.3f}  a↔c={j_ac:.3f} — OK")

    # ── 3. Merge ──────────────────────────────────────────────────────────────
    merged = merge_interface_records(rec_a, rec_b)
    assert "agent-001" in merged.parties and "agent-003" in merged.parties
    assert "read" in merged.capabilities and "publish" in merged.capabilities
    print(f"  [3] Merged: {merged.name}  parties={merged.parties} — OK")

    # ── 4. Coordinator probe/result round-trip ────────────────────────────────
    coord = InterfaceDiscoveryCoordinator(
        coordinator_id="smoke-coord",
        default_parties=("orchestrator",),
    )
    probe1 = _make_probe("agent-007", PROBE_TYPE_CAPABILITY_QUERY)
    probe2 = _make_probe("agent-008", PROBE_TYPE_CONTRACT_CHECK)
    coord.register_probe(probe1)
    coord.register_probe(probe2)

    result1 = _simulate_probe_result(probe1, ["stream", "batch", "query"])
    result2 = _simulate_probe_result(probe2, ["validate", "certify"], success=True)

    derived1 = coord.record_result(probe1.probe_id, result1)
    derived2 = coord.record_result(probe2.probe_id, result2)
    assert derived1.confidence > DEFAULT_CONFIDENCE_FLOOR
    assert "orchestrator" in derived1.parties and "agent-007" in derived1.parties

    round_records = coord.run_discovery_round()
    assert len(round_records) == 2
    summary = coord.report()
    assert summary["total_records"] == 2
    assert summary["round_count"] == 1
    print(f"  [4] Coordinator round: {len(round_records)} records — OK")

    # ── 5. Analyzer ───────────────────────────────────────────────────────────
    all_records = [rec_a, rec_b, rec_c, derived1, derived2]
    analyzer = InterfaceDiscoveryAnalyzer()
    report = analyzer.analyze(all_records)
    assert report.records_analyzed == 5
    assert len(report.top_interfaces) <= TOP_INTERFACES_LIMIT
    assert isinstance(report.cluster_map, dict)
    print(f"  [5] AnalysisReport: {report.report_id[:8]}… top={len(report.top_interfaces)} — OK")

    # ── 6. Extra helpers ──────────────────────────────────────────────────────
    cov = capability_coverage(rec_a, ["read", "execute"])
    assert 0.0 <= cov <= 1.0
    lat = compute_probe_latency(probe1, coord._state.witnesses[0])
    assert lat >= 0.0
    grouped = group_records_by_party(all_records)
    assert "agent-001" in grouped
    serialised = records_to_dict([rec_a, rec_b])
    assert serialised[0]["name"] == "alpha"
    top3 = top_n_records(all_records, 3)
    assert len(top3) <= 3

    # ── 7. score_interface helper ─────────────────────────────────────────────
    s = score_interface(rec_a, DEFAULT_SCORE_WEIGHTS)
    assert 0.0 <= s <= 1.0
    print(f"  [6-7] Helpers and scoring — OK")

    # ── 8. Edge cases ─────────────────────────────────────────────────────────
    empty_rec = make_interface_record("empty", [], [], 0.5)
    j_empty = interface_capability_jaccard(empty_rec, rec_a)
    assert j_empty == 0.0
    cov_empty = capability_coverage(empty_rec, [])
    assert cov_empty == 1.0
    norm = normalize_capabilities(["  READ ", "Write", "read", "", "  "])
    assert norm == ["read", "write"]
    print("  [8] Edge cases — OK")

    print("[smoke] PASS")
