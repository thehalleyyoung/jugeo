from __future__ import annotations

"""
Integration bridge for treaty_memory.

References:
    theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"

# copilot: This module was generated as part of the treaty_memory sub-package.
    It acts as the primary integration bridge between the in-memory episode/
    candidate stores, the negotiation module, the controller, and the evidence-
    trust subsystem.  All outward-facing surfaces use plain ``dict`` / JSON-
    serialisable types so that the bridge can be used in both in-process and
    cross-process deployment configurations.

Design
------
The module is structured in eight logical sections:

1. Imports & fallback stubs
2. Module-level constants
3. Data-transfer objects (frozen + mutable dataclasses)
4. Format-conversion helpers
5. TreatyMemoryBridge  – runtime connection manager
6. TreatyMemoryImporter – inbound bundle handling
7. TreatyMemoryExporter – outbound bundle production
8. TreatyMemoryHealthMonitor – self-diagnostics
9. Module smoke-test (``__main__``)

All classes that hold mutable state use ``@dataclass(slots=True)``.
All value objects use ``@dataclass(frozen=True, slots=True)`` so they are
hashable and safe to store in sets / as dict keys.
"""

import json
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── Jugeo Internal Imports (with stub fallbacks) ───────────────────────────

try:
    from jugeo.orchestration.treaty_memory.index import TreatyIndex  # type: ignore
except ImportError:
    TreatyIndex: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.archive import TreatyArchive  # type: ignore
except ImportError:
    TreatyArchive: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.candidates import CandidateStore  # type: ignore
except ImportError:
    CandidateStore: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.negotiation.module import NegotiationModule  # type: ignore
except ImportError:
    NegotiationModule: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.control.controller import Controller  # type: ignore
except ImportError:
    Controller: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.trust.evidence import EvidenceTrust  # type: ignore
except ImportError:
    EvidenceTrust: Any = None  # type: ignore[assignment,misc]

# ─── Public API ─────────────────────────────────────────────────────────────

__all__ = [
    # DTOs
    "ExportBundle",
    "HealthReport",
    # Bridge / manager
    "TreatyMemoryBridge",
    # Import / export
    "TreatyMemoryImporter",
    "TreatyMemoryExporter",
    # Health
    "TreatyMemoryHealthMonitor",
    # Helpers
    "bridge_episode_to_negotiation_format",
    "bridge_law_to_controller_format",
    "extract_trust_signals",
    "format_for_export",
    "merge_episode_batches",
    "score_candidate_relevance",
    "summarise_archive_stats",
    "validate_episode_dict",
    "validate_candidate_dict",
    "coerce_timestamp",
    "diff_bundles",
]

# ─── Constants ───────────────────────────────────────────────────────────────

#: Semantic version for the exchange bundle format.  Bump the minor component
#: for backwards-compatible additions; bump the major component for breaking
#: changes.
FORMAT_VERSION: str = "1.3.0"

#: Maximum number of episodes that will be included in a single export bundle.
#: Larger collections should be chunked at the call site.
MAX_EXPORT_EPISODES: int = 10_000

#: Maximum number of law-candidate entries allowed in one bundle.
MAX_EXPORT_CANDIDATES: int = 5_000

#: Maximum number of archive entries per bundle.
MAX_EXPORT_ARCHIVE_ENTRIES: int = 50_000

#: Minimum trust-signal score required before an episode is forwarded to the
#: negotiation module.  Episodes below this threshold are quarantined.
MIN_TRUST_SIGNAL_SCORE: float = 0.35

#: Weight given to recency when scoring candidate relevance.
RECENCY_WEIGHT: float = 0.4

#: Weight given to frequency (citation count) when scoring candidate relevance.
FREQUENCY_WEIGHT: float = 0.35

#: Weight given to evidence-trust rating when scoring candidate relevance.
TRUST_WEIGHT: float = 0.25

#: Number of seconds after which a cached health report is considered stale.
HEALTH_REPORT_TTL_SECONDS: float = 60.0

#: Status strings used in HealthReport.
STATUS_OK: str = "ok"
STATUS_DEGRADED: str = "degraded"
STATUS_CRITICAL: str = "critical"

#: Key that carries the treaty-episode payload inside an export bundle.
BUNDLE_KEY_EPISODES: str = "episodes"

#: Key that carries the candidate-law payload inside an export bundle.
BUNDLE_KEY_CANDIDATES: str = "candidates"

#: Key that carries the archive entries inside an export bundle.
BUNDLE_KEY_ARCHIVE: str = "archive"

#: Key that carries bundle-level metadata inside an export bundle.
BUNDLE_KEY_META: str = "meta"

#: Negotiation module expects episodes wrapped under this top-level key.
NEGOTIATION_EPISODE_KEY: str = "treaty_episode"

#: Controller expects law candidates wrapped under this top-level key.
CONTROLLER_LAW_KEY: str = "candidate_law"

#: Minimum required fields in a raw episode dict.
EPISODE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"episode_id", "treaty_id", "timestamp", "outcome"}
)

#: Minimum required fields in a raw candidate dict.
CANDIDATE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"candidate_id", "law_text", "confidence"}
)

#: Precision used when rounding floating-point scores for serialisation.
SCORE_DECIMAL_PLACES: int = 6

# ─── Logging ─────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ─── Data-Transfer Objects ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """
    Immutable, hashable snapshot of treaty-memory state ready for export.

    Attributes
    ----------
    bundle_id:
        Unique identifier (UUID4 string) assigned at creation time.
    episodes_count:
        Number of episode records captured in :attr:`payload`.
    candidates_count:
        Number of candidate-law records captured in :attr:`payload`.
    archive_entries_count:
        Number of archive entries captured in :attr:`payload`.
    exported_at:
        Unix timestamp (float) at which the bundle was assembled.
    format_version:
        Semantic version string of the bundle schema (e.g. ``"1.3.0"``).
    payload:
        Dictionary containing the full exported data under the keys
        ``"episodes"``, ``"candidates"``, ``"archive"``, and ``"meta"``.
    """

    bundle_id: str
    episodes_count: int
    candidates_count: int
    archive_entries_count: int
    exported_at: float
    format_version: str
    payload: dict  # type: ignore[type-arg]

    def age_seconds(self) -> float:
        """Return the number of seconds elapsed since this bundle was created."""
        return time.time() - self.exported_at

    def is_empty(self) -> bool:
        """Return ``True`` if the bundle carries no data records."""
        return (
            self.episodes_count == 0
            and self.candidates_count == 0
            and self.archive_entries_count == 0
        )

    def total_records(self) -> int:
        """Return the aggregate count of all records in the bundle."""
        return self.episodes_count + self.candidates_count + self.archive_entries_count

    def meta(self) -> dict:  # type: ignore[type-arg]
        """Return the ``"meta"`` sub-dictionary from the payload, or ``{}``."""
        return self.payload.get(BUNDLE_KEY_META, {})


@dataclass(frozen=True, slots=True)
class HealthReport:
    """
    Immutable diagnostic report produced by :class:`TreatyMemoryHealthMonitor`.

    Attributes
    ----------
    report_id:
        Unique identifier (UUID4 string) for this report.
    status:
        One of ``"ok"``, ``"degraded"``, or ``"critical"``.
    issues:
        Tuple of human-readable issue descriptions (empty when status is
        ``"ok"``).
    checked_at:
        Unix timestamp at which the check was performed.
    component:
        Name of the component that was checked (e.g. ``"index"``,
        ``"archive"``, or ``"full"``).
    """

    report_id: str
    status: str
    issues: tuple[str, ...]
    checked_at: float
    component: str

    def is_healthy(self) -> bool:
        """Return ``True`` when no issues were detected."""
        return self.status == STATUS_OK

    def age_seconds(self) -> float:
        """Return the number of seconds elapsed since this report was produced."""
        return time.time() - self.checked_at

    def is_stale(self) -> bool:
        """Return ``True`` when the report has exceeded :data:`HEALTH_REPORT_TTL_SECONDS`."""
        return self.age_seconds() > HEALTH_REPORT_TTL_SECONDS

    def summary_line(self) -> str:
        """Return a single-line human-readable summary of the report."""
        issue_count = len(self.issues)
        return (
            f"[{self.component}] status={self.status} issues={issue_count} "
            f"checked_at={self.checked_at:.3f}"
        )


# ─── Format-Conversion Helpers ───────────────────────────────────────────────


def validate_episode_dict(episode: dict) -> list[str]:  # type: ignore[type-arg]
    """
    Validate the structure of a raw episode dictionary.

    Parameters
    ----------
    episode:
        The dictionary to validate.

    Returns
    -------
    list[str]
        A (possibly empty) list of validation-error messages.
    """
    errors: list[str] = []
    for key in EPISODE_REQUIRED_FIELDS:
        if key not in episode:
            errors.append(f"Missing required episode field: '{key}'")
    if "timestamp" in episode:
        ts = episode["timestamp"]
        if not isinstance(ts, (int, float)):
            errors.append(
                f"Episode field 'timestamp' must be numeric, got {type(ts).__name__}"
            )
        elif ts < 0:
            errors.append("Episode field 'timestamp' must be non-negative")
    if "outcome" in episode and not isinstance(episode["outcome"], str):
        errors.append(
            f"Episode field 'outcome' must be a string, got {type(episode['outcome']).__name__}"
        )
    return errors


def validate_candidate_dict(candidate: dict) -> list[str]:  # type: ignore[type-arg]
    """
    Validate the structure of a raw candidate-law dictionary.

    Parameters
    ----------
    candidate:
        The dictionary to validate.

    Returns
    -------
    list[str]
        A (possibly empty) list of validation-error messages.
    """
    errors: list[str] = []
    for key in CANDIDATE_REQUIRED_FIELDS:
        if key not in candidate:
            errors.append(f"Missing required candidate field: '{key}'")
    if "confidence" in candidate:
        conf = candidate["confidence"]
        if not isinstance(conf, (int, float)):
            errors.append(
                f"Candidate field 'confidence' must be numeric, got {type(conf).__name__}"
            )
        elif not (0.0 <= conf <= 1.0):
            errors.append(
                f"Candidate field 'confidence' must be in [0, 1], got {conf}"
            )
    return errors


def coerce_timestamp(value: Any) -> float:
    """
    Attempt to coerce *value* into a Unix timestamp float.

    Accepts floats, ints, and ISO-8601 strings (date-only or datetime).
    Falls back to ``time.time()`` and logs a warning on failure.

    Parameters
    ----------
    value:
        The value to coerce.

    Returns
    -------
    float
        A valid Unix timestamp.
    """
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        import datetime  # local import to avoid top-level cost

        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(value, fmt)
                return dt.timestamp()
            except ValueError:
                continue
    logger.warning(
        "coerce_timestamp: could not coerce %r to float; using current time", value
    )
    return time.time()


def bridge_episode_to_negotiation_format(episode: dict) -> dict:  # type: ignore[type-arg]
    """
    Convert an internal episode dict into the payload format expected by the
    negotiation module.

    The negotiation module expects a top-level ``"treaty_episode"`` key whose
    value is a dict containing at least ``episode_id``, ``treaty_id``,
    ``outcome``, and ``timestamp_ms`` (milliseconds).

    Parameters
    ----------
    episode:
        Raw internal episode dictionary.

    Returns
    -------
    dict
        Negotiation-module-compatible payload.
    """
    errors = validate_episode_dict(episode)
    if errors:
        logger.warning(
            "bridge_episode_to_negotiation_format: episode has %d validation errors",
            len(errors),
        )

    timestamp_raw = episode.get("timestamp", time.time())
    timestamp_ms = int(coerce_timestamp(timestamp_raw) * 1000)

    negotiation_payload: dict = {  # type: ignore[type-arg]
        NEGOTIATION_EPISODE_KEY: {
            "episode_id": episode.get("episode_id", str(uuid.uuid4())),
            "treaty_id": episode.get("treaty_id", ""),
            "outcome": episode.get("outcome", "unknown"),
            "timestamp_ms": timestamp_ms,
            "metadata": episode.get("metadata", {}),
            "parties": episode.get("parties", []),
            "clauses": episode.get("clauses", []),
            "trust_score": round(
                float(episode.get("trust_score", 0.0)), SCORE_DECIMAL_PLACES
            ),
        }
    }
    return negotiation_payload


def bridge_law_to_controller_format(candidate: dict) -> dict:  # type: ignore[type-arg]
    """
    Convert an internal candidate-law dict into the payload format expected
    by the controller.

    The controller expects a top-level ``"candidate_law"`` key whose value is
    a dict containing at least ``candidate_id``, ``law_text``,
    ``confidence``, and ``provenance``.

    Parameters
    ----------
    candidate:
        Raw internal candidate-law dictionary.

    Returns
    -------
    dict
        Controller-compatible payload.
    """
    errors = validate_candidate_dict(candidate)
    if errors:
        logger.warning(
            "bridge_law_to_controller_format: candidate has %d validation errors",
            len(errors),
        )

    controller_payload: dict = {  # type: ignore[type-arg]
        CONTROLLER_LAW_KEY: {
            "candidate_id": candidate.get("candidate_id", str(uuid.uuid4())),
            "law_text": candidate.get("law_text", ""),
            "confidence": round(
                float(candidate.get("confidence", 0.0)), SCORE_DECIMAL_PLACES
            ),
            "provenance": candidate.get("provenance", {}),
            "treaty_ids": candidate.get("treaty_ids", []),
            "episode_support_count": int(candidate.get("episode_support_count", 0)),
            "version": candidate.get("version", "1.0"),
        }
    }
    return controller_payload


def extract_trust_signals(episodes: list) -> dict:  # type: ignore[type-arg]
    """
    Aggregate trust-related metrics from a collection of episode dicts.

    Computes mean, standard deviation, minimum, maximum, and the fraction of
    episodes that exceed :data:`MIN_TRUST_SIGNAL_SCORE`.

    Parameters
    ----------
    episodes:
        List of raw internal episode dicts.

    Returns
    -------
    dict
        A mapping with keys ``mean``, ``stdev``, ``min``, ``max``,
        ``above_threshold_fraction``, ``episode_count``.
    """
    if not episodes:
        return {
            "mean": 0.0,
            "stdev": 0.0,
            "min": 0.0,
            "max": 0.0,
            "above_threshold_fraction": 0.0,
            "episode_count": 0,
        }

    scores: list[float] = [
        float(ep.get("trust_score", 0.0)) for ep in episodes
    ]

    mean_score = statistics.mean(scores)
    stdev_score = statistics.pstdev(scores)  # population stdev (never raises for n=1)
    min_score = min(scores)
    max_score = max(scores)
    above = sum(1 for s in scores if s >= MIN_TRUST_SIGNAL_SCORE)
    fraction_above = above / len(scores)

    return {
        "mean": round(mean_score, SCORE_DECIMAL_PLACES),
        "stdev": round(stdev_score, SCORE_DECIMAL_PLACES),
        "min": round(min_score, SCORE_DECIMAL_PLACES),
        "max": round(max_score, SCORE_DECIMAL_PLACES),
        "above_threshold_fraction": round(fraction_above, SCORE_DECIMAL_PLACES),
        "episode_count": len(scores),
    }


def format_for_export(data: dict, version: str) -> dict:  # type: ignore[type-arg]
    """
    Wrap *data* in a standardised export envelope.

    The envelope adds ``schema_version``, ``exported_at``, and
    ``export_id`` fields under a ``"meta"`` key while placing the
    original *data* under ``"body"``.

    Parameters
    ----------
    data:
        The data to wrap.
    version:
        Schema-version string to embed in the envelope.

    Returns
    -------
    dict
        An export-envelope dict.
    """
    return {
        BUNDLE_KEY_META: {
            "schema_version": version,
            "exported_at": time.time(),
            "export_id": str(uuid.uuid4()),
        },
        "body": data,
    }


def merge_episode_batches(batches: list[list[dict]]) -> list[dict]:  # type: ignore[type-arg]
    """
    Merge multiple lists of episode dicts into a single deduplicated list.

    Deduplication is performed on ``episode_id``.  In case of collision the
    record with the higher ``trust_score`` is retained.

    Parameters
    ----------
    batches:
        List of episode-dict lists to merge.

    Returns
    -------
    list[dict]
        Merged, deduplicated list sorted by ``timestamp`` ascending.
    """
    merged: dict[str, dict] = {}  # type: ignore[type-arg]
    for batch in batches:
        for ep in batch:
            eid = ep.get("episode_id")
            if eid is None:
                # Episodes without IDs are assigned one deterministically.
                eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, json.dumps(ep, sort_keys=True)))
                ep = dict(ep)
                ep["episode_id"] = eid
            if eid not in merged:
                merged[eid] = ep
            else:
                existing_score = float(merged[eid].get("trust_score", 0.0))
                incoming_score = float(ep.get("trust_score", 0.0))
                if incoming_score > existing_score:
                    merged[eid] = ep

    result = list(merged.values())
    result.sort(key=lambda e: coerce_timestamp(e.get("timestamp", 0.0)))
    return result


def score_candidate_relevance(
    candidate: dict,  # type: ignore[type-arg]
    recency_ts: float | None = None,
) -> float:
    """
    Compute a scalar relevance score for a candidate-law entry.

    The score is a weighted combination of recency, citation frequency, and
    evidence-trust rating (weights defined by :data:`RECENCY_WEIGHT`,
    :data:`FREQUENCY_WEIGHT`, :data:`TRUST_WEIGHT`).

    Parameters
    ----------
    candidate:
        Raw internal candidate-law dictionary.
    recency_ts:
        Reference timestamp for recency computation.  Defaults to
        ``time.time()``.

    Returns
    -------
    float
        Relevance score in [0, 1].
    """
    now = recency_ts if recency_ts is not None else time.time()

    # Recency: exponential decay with half-life of 30 days.
    last_seen_raw = candidate.get("last_seen_at", now)
    last_seen = coerce_timestamp(last_seen_raw)
    age_days = max(0.0, (now - last_seen) / 86_400.0)
    half_life_days = 30.0
    recency_score = math.exp(-math.log(2) * age_days / half_life_days)

    # Frequency: normalised citation count (asymptotic towards 1 via tanh).
    citation_count = max(0, int(candidate.get("episode_support_count", 0)))
    frequency_score = math.tanh(citation_count / 20.0)

    # Trust: direct confidence value clamped to [0, 1].
    trust_score = max(0.0, min(1.0, float(candidate.get("confidence", 0.0))))

    composite = (
        RECENCY_WEIGHT * recency_score
        + FREQUENCY_WEIGHT * frequency_score
        + TRUST_WEIGHT * trust_score
    )
    return round(min(1.0, max(0.0, composite)), SCORE_DECIMAL_PLACES)


def summarise_archive_stats(archive_entries: list[dict]) -> dict:  # type: ignore[type-arg]
    """
    Produce a statistical summary of a collection of archive entries.

    Parameters
    ----------
    archive_entries:
        List of archive-entry dicts.  Each entry is expected to contain at
        least a ``"treaty_id"`` string and a numeric ``"episode_count"``
        value.

    Returns
    -------
    dict
        Summary containing ``total_entries``, ``total_episodes``,
        ``unique_treaties``, ``mean_episodes_per_treaty``,
        ``stdev_episodes_per_treaty``.
    """
    if not archive_entries:
        return {
            "total_entries": 0,
            "total_episodes": 0,
            "unique_treaties": 0,
            "mean_episodes_per_treaty": 0.0,
            "stdev_episodes_per_treaty": 0.0,
        }

    treaties: dict[str, int] = {}
    for entry in archive_entries:
        tid = entry.get("treaty_id", "_unknown_")
        count = int(entry.get("episode_count", 0))
        treaties[tid] = treaties.get(tid, 0) + count

    counts = list(treaties.values())
    return {
        "total_entries": len(archive_entries),
        "total_episodes": sum(counts),
        "unique_treaties": len(treaties),
        "mean_episodes_per_treaty": round(statistics.mean(counts), SCORE_DECIMAL_PLACES),
        "stdev_episodes_per_treaty": round(
            statistics.pstdev(counts), SCORE_DECIMAL_PLACES
        ),
    }


def diff_bundles(
    old: ExportBundle,
    new: ExportBundle,
) -> dict:  # type: ignore[type-arg]
    """
    Produce a high-level diff between two :class:`ExportBundle` instances.

    The diff captures changes in record counts and format version, and flags
    whether the new bundle is newer than the old one.

    Parameters
    ----------
    old:
        The earlier bundle.
    new:
        The later (or candidate-replacement) bundle.

    Returns
    -------
    dict
        Diff summary with keys ``episode_delta``, ``candidate_delta``,
        ``archive_delta``, ``version_changed``, ``new_is_newer``.
    """
    return {
        "episode_delta": new.episodes_count - old.episodes_count,
        "candidate_delta": new.candidates_count - old.candidates_count,
        "archive_delta": new.archive_entries_count - old.archive_entries_count,
        "version_changed": old.format_version != new.format_version,
        "new_is_newer": new.exported_at > old.exported_at,
        "old_bundle_id": old.bundle_id,
        "new_bundle_id": new.bundle_id,
    }


# ─── TreatyMemoryBridge ──────────────────────────────────────────────────────


@dataclass(slots=True)
class TreatyMemoryBridge:
    """
    Runtime connection manager that wires the treaty-memory subsystem to
    external jugeo modules.

    The bridge maintains optional references to a :class:`NegotiationModule`,
    a :class:`Controller`, and an :class:`EvidenceTrust` instance.  All three
    can be supplied after construction via the ``connect_*`` family of methods,
    allowing the bridge to be constructed before all subsystems are ready (a
    common pattern in jugeo's lazy-initialisation boot sequence).

    Attributes
    ----------
    _negotiation_module:
        Connected negotiation module (or ``None`` if not yet connected).
    _controller:
        Connected controller (or ``None`` if not yet connected).
    _evidence_trust:
        Connected evidence-trust instance (or ``None`` if not yet connected).
    _episode_buffer:
        In-memory buffer of episode dicts awaiting dispatch.
    _law_buffer:
        In-memory buffer of candidate-law dicts awaiting sync.
    """

    _negotiation_module: Any = field(default=None, repr=False)
    _controller: Any = field(default=None, repr=False)
    _evidence_trust: Any = field(default=None, repr=False)
    _episode_buffer: list = field(default_factory=list, repr=False)
    _law_buffer: list = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Connection API
    # ------------------------------------------------------------------

    def connect_negotiation_module(self, module: Any) -> None:
        """
        Attach a negotiation module to this bridge.

        Parameters
        ----------
        module:
            Any object that exposes an ``ingest_episode(payload: dict)`` method.
            Passing ``None`` disconnects the current module.
        """
        self._negotiation_module = module
        logger.info(
            "TreatyMemoryBridge: negotiation module %s",
            "connected" if module is not None else "disconnected",
        )

    def connect_controller(self, controller: Any) -> None:
        """
        Attach a controller to this bridge.

        Parameters
        ----------
        controller:
            Any object that exposes a ``receive_law(payload: dict)`` method.
            Passing ``None`` disconnects the current controller.
        """
        self._controller = controller
        logger.info(
            "TreatyMemoryBridge: controller %s",
            "connected" if controller is not None else "disconnected",
        )

    def connect_evidence_trust(self, trust: Any) -> None:
        """
        Attach an evidence-trust instance to this bridge.

        Parameters
        ----------
        trust:
            Any object that exposes a ``score(episode: dict) -> float`` method.
            Passing ``None`` disconnects the current instance.
        """
        self._evidence_trust = trust
        logger.info(
            "TreatyMemoryBridge: evidence trust %s",
            "connected" if trust is not None else "disconnected",
        )

    # ------------------------------------------------------------------
    # Episode API
    # ------------------------------------------------------------------

    def push_episode(self, episode_dict: dict) -> None:  # type: ignore[type-arg]
        """
        Push a single episode into the bridge.

        If a negotiation module is connected and the episode's trust score
        meets or exceeds :data:`MIN_TRUST_SIGNAL_SCORE`, the episode is
        forwarded immediately.  Otherwise it is appended to the internal
        buffer for later dispatch.

        Parameters
        ----------
        episode_dict:
            Raw internal episode dictionary.  Must satisfy
            :data:`EPISODE_REQUIRED_FIELDS`.
        """
        errors = validate_episode_dict(episode_dict)
        if errors:
            logger.warning(
                "push_episode: rejecting episode due to %d validation error(s): %s",
                len(errors),
                errors,
            )
            return

        # Optionally enrich trust score via the connected evidence-trust module.
        if self._evidence_trust is not None:
            try:
                score = float(self._evidence_trust.score(episode_dict))
                episode_dict = dict(episode_dict)
                episode_dict["trust_score"] = round(score, SCORE_DECIMAL_PLACES)
            except Exception:  # noqa: BLE001
                logger.exception("push_episode: evidence_trust.score() raised")

        trust_score = float(episode_dict.get("trust_score", 0.0))
        if trust_score < MIN_TRUST_SIGNAL_SCORE:
            logger.debug(
                "push_episode: buffering episode %s (trust_score=%.4f < threshold=%.4f)",
                episode_dict.get("episode_id"),
                trust_score,
                MIN_TRUST_SIGNAL_SCORE,
            )
            self._episode_buffer.append(episode_dict)
            return

        self._dispatch_episode(episode_dict)

    def pull_episodes(self, query: dict) -> list:  # type: ignore[type-arg]
        """
        Pull episodes from the internal buffer that match *query*.

        Matching is performed by simple key-value equality on the fields
        present in *query*.  An empty *query* returns all buffered episodes.

        Parameters
        ----------
        query:
            Dict of field-value pairs to match against.

        Returns
        -------
        list
            List of matching episode dicts.
        """
        if not query:
            return list(self._episode_buffer)

        result = []
        for ep in self._episode_buffer:
            if all(ep.get(k) == v for k, v in query.items()):
                result.append(ep)
        return result

    def _dispatch_episode(self, episode_dict: dict) -> None:  # type: ignore[type-arg]
        """Forward a validated, above-threshold episode to the negotiation module."""
        if self._negotiation_module is None:
            self._episode_buffer.append(episode_dict)
            logger.debug(
                "_dispatch_episode: no negotiation module; buffering episode %s",
                episode_dict.get("episode_id"),
            )
            return
        payload = bridge_episode_to_negotiation_format(episode_dict)
        try:
            self._negotiation_module.ingest_episode(payload)
            logger.debug(
                "_dispatch_episode: forwarded episode %s to negotiation module",
                episode_dict.get("episode_id"),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "_dispatch_episode: negotiation_module.ingest_episode() raised; "
                "buffering episode"
            )
            self._episode_buffer.append(episode_dict)

    # ------------------------------------------------------------------
    # Law-sync API
    # ------------------------------------------------------------------

    def sync_laws(self, candidate_dicts: list) -> None:  # type: ignore[type-arg]
        """
        Synchronise a batch of candidate-law dicts to the connected controller.

        Validation errors cause the offending candidate to be skipped (with a
        warning) rather than aborting the entire batch.  Candidates that pass
        validation are forwarded to the controller when one is connected;
        otherwise they are appended to the internal law buffer.

        Parameters
        ----------
        candidate_dicts:
            List of raw internal candidate-law dicts.
        """
        forwarded = 0
        buffered = 0
        skipped = 0

        for candidate in candidate_dicts:
            errors = validate_candidate_dict(candidate)
            if errors:
                logger.warning(
                    "sync_laws: skipping candidate %s due to %d error(s): %s",
                    candidate.get("candidate_id", "<unknown>"),
                    len(errors),
                    errors,
                )
                skipped += 1
                continue

            if self._controller is None:
                self._law_buffer.append(candidate)
                buffered += 1
                continue

            payload = bridge_law_to_controller_format(candidate)
            try:
                self._controller.receive_law(payload)
                forwarded += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "sync_laws: controller.receive_law() raised; buffering candidate"
                )
                self._law_buffer.append(candidate)
                buffered += 1

        logger.info(
            "sync_laws: forwarded=%d buffered=%d skipped=%d",
            forwarded,
            buffered,
            skipped,
        )


# ─── TreatyMemoryImporter ────────────────────────────────────────────────────


@dataclass(slots=True)
class TreatyMemoryImporter:
    """
    Handles inbound :class:`ExportBundle` data, including JSON deserialisation
    and structural validation.

    This class is stateless in normal usage — all methods can be called on a
    freshly-constructed instance.  An instance is nonetheless used (rather
    than free functions) so that subclasses can override individual steps.
    """

    def from_bundle(self, bundle: ExportBundle) -> dict:  # type: ignore[type-arg]
        """
        Extract the payload data from an :class:`ExportBundle` into a plain
        nested dict suitable for consumption by other jugeo subsystems.

        Parameters
        ----------
        bundle:
            Source bundle.

        Returns
        -------
        dict
            Dict with keys ``"episodes"``, ``"candidates"``, ``"archive"``,
            and ``"meta"``.
        """
        issues = self.validate_bundle(bundle)
        if issues:
            logger.warning(
                "from_bundle: bundle %s has %d issue(s): %s",
                bundle.bundle_id,
                len(issues),
                issues,
            )

        episodes = bundle.payload.get(BUNDLE_KEY_EPISODES, [])
        candidates = bundle.payload.get(BUNDLE_KEY_CANDIDATES, [])
        archive = bundle.payload.get(BUNDLE_KEY_ARCHIVE, [])
        meta = bundle.payload.get(BUNDLE_KEY_META, {})

        return {
            "episodes": episodes,
            "candidates": candidates,
            "archive": archive,
            "meta": meta,
            "import_issues": issues,
        }

    def from_json(self, json_str: str) -> ExportBundle:
        """
        Deserialise an :class:`ExportBundle` from a JSON string.

        Parameters
        ----------
        json_str:
            JSON string previously produced by
            :meth:`TreatyMemoryExporter.to_json`.

        Returns
        -------
        ExportBundle
            Reconstructed bundle.

        Raises
        ------
        ValueError
            If the JSON is malformed or missing required top-level keys.
        """
        try:
            raw = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"from_json: invalid JSON: {exc}") from exc

        required = {
            "bundle_id",
            "episodes_count",
            "candidates_count",
            "archive_entries_count",
            "exported_at",
            "format_version",
            "payload",
        }
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"from_json: missing keys in JSON: {sorted(missing)}")

        return ExportBundle(
            bundle_id=str(raw["bundle_id"]),
            episodes_count=int(raw["episodes_count"]),
            candidates_count=int(raw["candidates_count"]),
            archive_entries_count=int(raw["archive_entries_count"]),
            exported_at=float(raw["exported_at"]),
            format_version=str(raw["format_version"]),
            payload=dict(raw["payload"]),
        )

    def validate_bundle(self, bundle: ExportBundle) -> list[str]:
        """
        Perform a structural validation of an :class:`ExportBundle`.

        Returns a (possibly empty) list of human-readable issue strings.
        An empty list means the bundle passed all checks.

        Parameters
        ----------
        bundle:
            The bundle to validate.

        Returns
        -------
        list[str]
            Validation issues found.
        """
        issues: list[str] = []

        if not bundle.bundle_id:
            issues.append("bundle_id is empty")

        if bundle.episodes_count < 0:
            issues.append(f"episodes_count is negative: {bundle.episodes_count}")

        if bundle.candidates_count < 0:
            issues.append(f"candidates_count is negative: {bundle.candidates_count}")

        if bundle.archive_entries_count < 0:
            issues.append(
                f"archive_entries_count is negative: {bundle.archive_entries_count}"
            )

        if bundle.exported_at <= 0:
            issues.append(f"exported_at is not a positive timestamp: {bundle.exported_at}")

        if not bundle.format_version:
            issues.append("format_version is empty")

        actual_episodes = len(bundle.payload.get(BUNDLE_KEY_EPISODES, []))
        if actual_episodes != bundle.episodes_count:
            issues.append(
                f"episodes_count mismatch: header says {bundle.episodes_count}, "
                f"payload contains {actual_episodes}"
            )

        actual_candidates = len(bundle.payload.get(BUNDLE_KEY_CANDIDATES, []))
        if actual_candidates != bundle.candidates_count:
            issues.append(
                f"candidates_count mismatch: header says {bundle.candidates_count}, "
                f"payload contains {actual_candidates}"
            )

        actual_archive = len(bundle.payload.get(BUNDLE_KEY_ARCHIVE, []))
        if actual_archive != bundle.archive_entries_count:
            issues.append(
                f"archive_entries_count mismatch: header says "
                f"{bundle.archive_entries_count}, payload contains {actual_archive}"
            )

        if bundle.episodes_count > MAX_EXPORT_EPISODES:
            issues.append(
                f"episodes_count {bundle.episodes_count} exceeds maximum "
                f"{MAX_EXPORT_EPISODES}"
            )

        if bundle.candidates_count > MAX_EXPORT_CANDIDATES:
            issues.append(
                f"candidates_count {bundle.candidates_count} exceeds maximum "
                f"{MAX_EXPORT_CANDIDATES}"
            )

        return issues


# ─── TreatyMemoryExporter ────────────────────────────────────────────────────


@dataclass(slots=True)
class TreatyMemoryExporter:
    """
    Produces :class:`ExportBundle` instances from live treaty-memory data
    structures and serialises them to JSON or compact summary form.

    All methods accept the raw data structures (index, candidates store,
    archive) as duck-typed parameters so that the exporter works against both
    real jugeo implementations and the stub ``None`` values used when those
    packages are absent.
    """

    def to_bundle(
        self,
        index: Any,
        candidates: Any,
        archive: Any,
    ) -> ExportBundle:
        """
        Snapshot the provided data structures into an :class:`ExportBundle`.

        When a data structure is ``None`` (e.g. the backing package is not
        installed) the corresponding section of the bundle will be empty.

        Parameters
        ----------
        index:
            A :class:`TreatyIndex`-compatible object with an
            ``all_episodes() -> list[dict]`` method, or ``None``.
        candidates:
            A :class:`CandidateStore`-compatible object with an
            ``all_candidates() -> list[dict]`` method, or ``None``.
        archive:
            A :class:`TreatyArchive`-compatible object with an
            ``all_entries() -> list[dict]`` method, or ``None``.

        Returns
        -------
        ExportBundle
            A fully-populated, immutable bundle.
        """
        episodes: list[dict] = []  # type: ignore[type-arg]
        candidate_list: list[dict] = []  # type: ignore[type-arg]
        archive_entries: list[dict] = []  # type: ignore[type-arg]

        if index is not None:
            try:
                episodes = list(index.all_episodes())
            except Exception:  # noqa: BLE001
                logger.exception("to_bundle: index.all_episodes() raised")

        if candidates is not None:
            try:
                candidate_list = list(candidates.all_candidates())
            except Exception:  # noqa: BLE001
                logger.exception("to_bundle: candidates.all_candidates() raised")

        if archive is not None:
            try:
                archive_entries = list(archive.all_entries())
            except Exception:  # noqa: BLE001
                logger.exception("to_bundle: archive.all_entries() raised")

        # Enforce export limits.
        if len(episodes) > MAX_EXPORT_EPISODES:
            logger.warning(
                "to_bundle: truncating episodes from %d to %d",
                len(episodes),
                MAX_EXPORT_EPISODES,
            )
            episodes = episodes[:MAX_EXPORT_EPISODES]

        if len(candidate_list) > MAX_EXPORT_CANDIDATES:
            logger.warning(
                "to_bundle: truncating candidates from %d to %d",
                len(candidate_list),
                MAX_EXPORT_CANDIDATES,
            )
            candidate_list = candidate_list[:MAX_EXPORT_CANDIDATES]

        if len(archive_entries) > MAX_EXPORT_ARCHIVE_ENTRIES:
            logger.warning(
                "to_bundle: truncating archive entries from %d to %d",
                len(archive_entries),
                MAX_EXPORT_ARCHIVE_ENTRIES,
            )
            archive_entries = archive_entries[:MAX_EXPORT_ARCHIVE_ENTRIES]

        now = time.time()
        payload = {
            BUNDLE_KEY_EPISODES: episodes,
            BUNDLE_KEY_CANDIDATES: candidate_list,
            BUNDLE_KEY_ARCHIVE: archive_entries,
            BUNDLE_KEY_META: {
                "exported_at": now,
                "format_version": FORMAT_VERSION,
            },
        }

        return ExportBundle(
            bundle_id=str(uuid.uuid4()),
            episodes_count=len(episodes),
            candidates_count=len(candidate_list),
            archive_entries_count=len(archive_entries),
            exported_at=now,
            format_version=FORMAT_VERSION,
            payload=payload,
        )

    def to_json(self, bundle: ExportBundle) -> str:
        """
        Serialise an :class:`ExportBundle` to a JSON string.

        Parameters
        ----------
        bundle:
            The bundle to serialise.

        Returns
        -------
        str
            JSON representation of the bundle.
        """
        data = {
            "bundle_id": bundle.bundle_id,
            "episodes_count": bundle.episodes_count,
            "candidates_count": bundle.candidates_count,
            "archive_entries_count": bundle.archive_entries_count,
            "exported_at": bundle.exported_at,
            "format_version": bundle.format_version,
            "payload": bundle.payload,
        }
        return json.dumps(data, separators=(",", ":"), default=str)

    def to_summary(self, bundle: ExportBundle) -> dict:  # type: ignore[type-arg]
        """
        Produce a compact, human-readable summary of an :class:`ExportBundle`.

        The summary omits the full payload in favour of aggregate statistics
        computed from it.

        Parameters
        ----------
        bundle:
            The bundle to summarise.

        Returns
        -------
        dict
            Summary dict containing ``bundle_id``, ``format_version``,
            ``exported_at``, ``records``, ``trust_signals``,
            ``archive_stats``, and ``age_seconds``.
        """
        episodes = bundle.payload.get(BUNDLE_KEY_EPISODES, [])
        candidates = bundle.payload.get(BUNDLE_KEY_CANDIDATES, [])
        archive = bundle.payload.get(BUNDLE_KEY_ARCHIVE, [])

        trust_signals = extract_trust_signals(episodes)
        archive_stats = summarise_archive_stats(archive)

        candidate_scores = [
            score_candidate_relevance(c) for c in candidates
        ]
        mean_relevance = (
            round(statistics.mean(candidate_scores), SCORE_DECIMAL_PLACES)
            if candidate_scores
            else 0.0
        )

        return {
            "bundle_id": bundle.bundle_id,
            "format_version": bundle.format_version,
            "exported_at": bundle.exported_at,
            "age_seconds": round(bundle.age_seconds(), 2),
            "records": {
                "episodes": bundle.episodes_count,
                "candidates": bundle.candidates_count,
                "archive_entries": bundle.archive_entries_count,
                "total": bundle.total_records(),
            },
            "trust_signals": trust_signals,
            "archive_stats": archive_stats,
            "mean_candidate_relevance": mean_relevance,
        }


# ─── TreatyMemoryHealthMonitor ───────────────────────────────────────────────


@dataclass(slots=True)
class TreatyMemoryHealthMonitor:
    """
    Performs integrity checks on treaty-memory components and produces
    :class:`HealthReport` instances.

    The monitor is intentionally lightweight: it does not hold strong
    references to the components it checks.  Callers pass data structures
    in at check time.

    Attributes
    ----------
    _last_index_report:
        Most recent index health report, used for fast ``is_stale`` checks.
    _last_archive_report:
        Most recent archive health report.
    """

    _last_index_report: HealthReport | None = field(default=None, repr=False)
    _last_archive_report: HealthReport | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Index checks
    # ------------------------------------------------------------------

    def check_index_integrity(self, index: Any) -> HealthReport:
        """
        Validate the integrity of a treaty index.

        Checks performed:

        * The index object is not ``None``.
        * ``all_episodes()`` can be called without raising.
        * No episode in the index is missing a required field.
        * No two episodes share the same ``episode_id``.

        Parameters
        ----------
        index:
            A :class:`TreatyIndex`-compatible object, or ``None``.

        Returns
        -------
        HealthReport
            Diagnostic report for the index component.
        """
        issues: list[str] = []
        now = time.time()

        if index is None:
            issues.append("Index is None (package not available)")
            report = HealthReport(
                report_id=str(uuid.uuid4()),
                status=STATUS_DEGRADED,
                issues=tuple(issues),
                checked_at=now,
                component="index",
            )
            self._last_index_report = report
            return report

        # Attempt to call all_episodes().
        try:
            episodes: list[dict] = list(index.all_episodes())  # type: ignore[type-arg]
        except Exception as exc:  # noqa: BLE001
            issues.append(f"index.all_episodes() raised: {exc}")
            report = HealthReport(
                report_id=str(uuid.uuid4()),
                status=STATUS_CRITICAL,
                issues=tuple(issues),
                checked_at=now,
                component="index",
            )
            self._last_index_report = report
            return report

        # Field completeness.
        for i, ep in enumerate(episodes):
            ep_errors = validate_episode_dict(ep)
            for err in ep_errors:
                issues.append(f"Episode[{i}]: {err}")

        # Duplicate IDs.
        seen_ids: set[str] = set()
        for ep in episodes:
            eid = ep.get("episode_id", "")
            if eid in seen_ids:
                issues.append(f"Duplicate episode_id detected: '{eid}'")
            else:
                seen_ids.add(eid)

        status = STATUS_OK if not issues else STATUS_DEGRADED
        report = HealthReport(
            report_id=str(uuid.uuid4()),
            status=status,
            issues=tuple(issues),
            checked_at=now,
            component="index",
        )
        self._last_index_report = report
        return report

    # ------------------------------------------------------------------
    # Archive checks
    # ------------------------------------------------------------------

    def check_archive_integrity(self, archive: Any) -> HealthReport:
        """
        Validate the integrity of a treaty archive.

        Checks performed:

        * The archive object is not ``None``.
        * ``all_entries()`` can be called without raising.
        * No entry has a negative ``episode_count``.
        * No two entries share the same ``entry_id`` (if present).

        Parameters
        ----------
        archive:
            A :class:`TreatyArchive`-compatible object, or ``None``.

        Returns
        -------
        HealthReport
            Diagnostic report for the archive component.
        """
        issues: list[str] = []
        now = time.time()

        if archive is None:
            issues.append("Archive is None (package not available)")
            report = HealthReport(
                report_id=str(uuid.uuid4()),
                status=STATUS_DEGRADED,
                issues=tuple(issues),
                checked_at=now,
                component="archive",
            )
            self._last_archive_report = report
            return report

        try:
            entries: list[dict] = list(archive.all_entries())  # type: ignore[type-arg]
        except Exception as exc:  # noqa: BLE001
            issues.append(f"archive.all_entries() raised: {exc}")
            report = HealthReport(
                report_id=str(uuid.uuid4()),
                status=STATUS_CRITICAL,
                issues=tuple(issues),
                checked_at=now,
                component="archive",
            )
            self._last_archive_report = report
            return report

        seen_entry_ids: set[str] = set()
        for i, entry in enumerate(entries):
            count = entry.get("episode_count")
            if count is not None and int(count) < 0:
                issues.append(
                    f"Archive entry[{i}] has negative episode_count: {count}"
                )
            eid = entry.get("entry_id")
            if eid is not None:
                if eid in seen_entry_ids:
                    issues.append(f"Duplicate entry_id detected: '{eid}'")
                else:
                    seen_entry_ids.add(str(eid))

        status = STATUS_OK if not issues else STATUS_DEGRADED
        report = HealthReport(
            report_id=str(uuid.uuid4()),
            status=status,
            issues=tuple(issues),
            checked_at=now,
            component="archive",
        )
        self._last_archive_report = report
        return report

    # ------------------------------------------------------------------
    # Full check
    # ------------------------------------------------------------------

    def full_health_check(
        self,
        index: Any = None,
        candidates: Any = None,
        archive: Any = None,
    ) -> HealthReport:
        """
        Run all individual integrity checks and combine them into a single
        aggregate :class:`HealthReport`.

        The aggregate status is the worst status among all component reports:
        ``"critical"`` > ``"degraded"`` > ``"ok"``.

        Parameters
        ----------
        index:
            Treaty index to check, or ``None``.
        candidates:
            Candidate store to check (basic ``None`` check only), or ``None``.
        archive:
            Treaty archive to check, or ``None``.

        Returns
        -------
        HealthReport
            Aggregate diagnostic report with component ``"full"``.
        """
        all_issues: list[str] = []
        worst_status = STATUS_OK

        def _escalate(status: str) -> str:
            order = {STATUS_OK: 0, STATUS_DEGRADED: 1, STATUS_CRITICAL: 2}
            if order.get(status, 0) > order.get(worst_status, 0):
                return status
            return worst_status

        # Index check.
        index_report = self.check_index_integrity(index)
        for issue in index_report.issues:
            all_issues.append(f"[index] {issue}")
        worst_status = _escalate(index_report.status)

        # Archive check.
        archive_report = self.check_archive_integrity(archive)
        for issue in archive_report.issues:
            all_issues.append(f"[archive] {issue}")
        worst_status = _escalate(archive_report.status)

        # Candidates: basic None check only.
        if candidates is None:
            all_issues.append("[candidates] CandidateStore is None (package not available)")
            worst_status = _escalate(STATUS_DEGRADED)

        now = time.time()
        return HealthReport(
            report_id=str(uuid.uuid4()),
            status=worst_status,
            issues=tuple(all_issues),
            checked_at=now,
            component="full",
        )


# ─── Smoke Test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    print(f"[smoke] {__file__}")

    # ------------------------------------------------------------------
    # 1. DTO construction and basic properties
    # ------------------------------------------------------------------
    now_ts = time.time()
    bundle = ExportBundle(
        bundle_id=str(uuid.uuid4()),
        episodes_count=2,
        candidates_count=1,
        archive_entries_count=3,
        exported_at=now_ts,
        format_version=FORMAT_VERSION,
        payload={
            BUNDLE_KEY_EPISODES: [
                {
                    "episode_id": "ep-001",
                    "treaty_id": "t-001",
                    "timestamp": now_ts - 100,
                    "outcome": "agreed",
                    "trust_score": 0.9,
                },
                {
                    "episode_id": "ep-002",
                    "treaty_id": "t-001",
                    "timestamp": now_ts - 50,
                    "outcome": "rejected",
                    "trust_score": 0.2,
                },
            ],
            BUNDLE_KEY_CANDIDATES: [
                {
                    "candidate_id": "cand-001",
                    "law_text": "All parties must notify within 30 days.",
                    "confidence": 0.85,
                    "episode_support_count": 12,
                    "last_seen_at": now_ts - 200,
                }
            ],
            BUNDLE_KEY_ARCHIVE: [
                {"treaty_id": "t-001", "episode_count": 5, "entry_id": "arc-001"},
                {"treaty_id": "t-002", "episode_count": 3, "entry_id": "arc-002"},
                {"treaty_id": "t-003", "episode_count": 7, "entry_id": "arc-003"},
            ],
            BUNDLE_KEY_META: {"format_version": FORMAT_VERSION},
        },
    )
    assert bundle.total_records() == 6, "total_records mismatch"
    assert not bundle.is_empty(), "bundle should not be empty"

    # ------------------------------------------------------------------
    # 2. Importer / exporter round-trip
    # ------------------------------------------------------------------
    exporter = TreatyMemoryExporter()
    importer = TreatyMemoryImporter()

    json_str = exporter.to_json(bundle)
    recovered = importer.from_json(json_str)
    assert recovered.bundle_id == bundle.bundle_id, "bundle_id mismatch after round-trip"
    assert recovered.episodes_count == bundle.episodes_count, "episodes_count mismatch"

    issues = importer.validate_bundle(recovered)
    assert not issues, f"validate_bundle should pass: {issues}"

    summary = exporter.to_summary(bundle)
    assert summary["records"]["total"] == 6, "summary total_records mismatch"
    assert "trust_signals" in summary, "summary missing trust_signals"

    # ------------------------------------------------------------------
    # 3. Helper functions
    # ------------------------------------------------------------------
    episodes_raw = bundle.payload[BUNDLE_KEY_EPISODES]
    trust = extract_trust_signals(episodes_raw)
    assert 0.0 <= trust["mean"] <= 1.0, "trust mean out of range"

    neg_payload = bridge_episode_to_negotiation_format(episodes_raw[0])
    assert NEGOTIATION_EPISODE_KEY in neg_payload, "missing negotiation episode key"

    cand_raw = bundle.payload[BUNDLE_KEY_CANDIDATES][0]
    ctrl_payload = bridge_law_to_controller_format(cand_raw)
    assert CONTROLLER_LAW_KEY in ctrl_payload, "missing controller law key"

    relevance = score_candidate_relevance(cand_raw)
    assert 0.0 <= relevance <= 1.0, f"relevance out of range: {relevance}"

    archive_stats = summarise_archive_stats(bundle.payload[BUNDLE_KEY_ARCHIVE])
    assert archive_stats["unique_treaties"] == 3, "unexpected unique_treaties count"

    merged = merge_episode_batches([episodes_raw, episodes_raw])
    assert len(merged) == 2, f"deduplication failed: {len(merged)} != 2"

    # ------------------------------------------------------------------
    # 4. Bridge
    # ------------------------------------------------------------------
    bridge = TreatyMemoryBridge()
    bridge.push_episode(episodes_raw[0])   # trust_score=0.9 → above threshold, buffered (no module)
    bridge.push_episode(episodes_raw[1])   # trust_score=0.2 → below threshold, buffered

    all_buffered = bridge.pull_episodes({})
    # Both end up in the buffer since there is no negotiation module connected.
    assert len(all_buffered) == 2, f"expected 2 buffered episodes, got {len(all_buffered)}"

    bridge.sync_laws(bundle.payload[BUNDLE_KEY_CANDIDATES])

    # ------------------------------------------------------------------
    # 5. Health monitor
    # ------------------------------------------------------------------
    monitor = TreatyMemoryHealthMonitor()
    full_report = monitor.full_health_check(index=None, candidates=None, archive=None)
    assert full_report.status in (STATUS_OK, STATUS_DEGRADED, STATUS_CRITICAL)
    assert not full_report.is_stale(), "brand-new report should not be stale"

    # ------------------------------------------------------------------
    # 6. diff_bundles
    # ------------------------------------------------------------------
    bundle2 = ExportBundle(
        bundle_id=str(uuid.uuid4()),
        episodes_count=5,
        candidates_count=2,
        archive_entries_count=3,
        exported_at=now_ts + 10,
        format_version=FORMAT_VERSION,
        payload={
            BUNDLE_KEY_EPISODES: [{}] * 5,
            BUNDLE_KEY_CANDIDATES: [{}, {}],
            BUNDLE_KEY_ARCHIVE: [{}, {}, {}],
            BUNDLE_KEY_META: {},
        },
    )
    diff = diff_bundles(bundle, bundle2)
    assert diff["episode_delta"] == 3, f"episode_delta wrong: {diff['episode_delta']}"
    assert diff["new_is_newer"] is True

    # ------------------------------------------------------------------
    # 7. coerce_timestamp edge cases
    # ------------------------------------------------------------------
    assert isinstance(coerce_timestamp(1_700_000_000), float)
    assert isinstance(coerce_timestamp("2024-01-15T10:30:00"), float)
    assert isinstance(coerce_timestamp("2024-01-15"), float)
    assert isinstance(coerce_timestamp(None), float)  # fallback path

    # ------------------------------------------------------------------
    # 8. format_for_export
    # ------------------------------------------------------------------
    envelope = format_for_export({"key": "value"}, FORMAT_VERSION)
    assert BUNDLE_KEY_META in envelope, "envelope missing meta"
    assert envelope[BUNDLE_KEY_META]["schema_version"] == FORMAT_VERSION

    print("[smoke] PASS")
    sys.exit(0)
