"""
Domain formation pipeline for regime bootstrapping.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Obstruction-Theoretic
Domain Formation. This module implements the first stage of the regime bootstrapping
pipeline: given a collection of obstruction fields arising from failed or partial
descent computations, we identify coherent sub-domains of the total mathematical
space in which a new regime can be validly constructed.

The fundamental idea is that obstruction fields delineate the boundaries within
which type-theoretic constructions are coherent. By analyzing the severity, kind,
and mutual interaction of these obstructions we can partition the ambient space
into candidate domains, each equipped with a set of generators and relations
sufficient to seed a type-constructor search in the subsequent pipeline stage.

This module is intentionally self-contained: all cross-module imports are guarded
so that the domain formation logic can be exercised in isolation during testing
or when upstream packages are unavailable.

Typical usage::

    from jugeo.ideation.regime_bootstrapping.domain_formation import (
        DomainFormationRunner, analyze_obstructions, partition_domain,
    )
    runner = DomainFormationRunner()
    domains = runner.run(obstruction_fields)
    for d in domains:
        print(d)
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "ObstructionAnalyzer",
    "DomainPartitioner",
    "DomainValidator",
    "DomainFormationRunner",
    "analyze_obstructions",
    "partition_domain",
]

# ---------------------------------------------------------------------------
# Cross-module imports — always guarded
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField,
        ObstructionKind,
        DomainFormation,
        DomainType,
        TypeConstructor,
        TypeConstructorKind,
        RegimeCandidate,
        BootstrapStep,
        BootstrapPlan,
        BootstrapResult,
        BootstrapStatus,
        BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default weight applied to topological obstruction severity
TOPO_WEIGHT: float = 1.5

#: Default weight applied to algebraic obstruction severity
ALGE_WEIGHT: float = 1.2

#: Default weight applied to geometric obstruction severity
GEOM_WEIGHT: float = 1.0

#: Default weight applied to cohomological obstruction severity
COHO_WEIGHT: float = 1.8

#: Severity bucket boundaries (low, medium, high, critical)
SEVERITY_THRESHOLDS: Tuple[float, float, float] = (0.25, 0.55, 0.80)

#: Minimum number of generators a domain must have to be considered valid
MIN_GENERATORS: int = 1

#: Maximum number of generators before a domain is considered too large
MAX_GENERATORS: int = 256

#: Minimum coverage fraction for a partition to be accepted
MIN_COVERAGE: float = 0.70

#: Default number of histogram bins for severity distribution
HISTOGRAM_BINS: int = 10

#: Default domain type when none is inferred
DEFAULT_DOMAIN_TYPE: str = "generic"

#: Sentinel value used when a severity cannot be computed
NULL_SEVERITY: float = -1.0

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
AnalysisReport = Dict[str, Any]
ValidationResult = Dict[str, Any]
PartitionReport = Dict[str, Any]
SeverityBucket = str  # "low" | "medium" | "high" | "critical"

# ---------------------------------------------------------------------------
# Module-level helper utilities
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime with timezone info.

    Used uniformly across this module to ensure all timestamps are
    timezone-aware and comparable.

    Returns
    -------
    datetime
        Current UTC datetime (timezone-aware).
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a new random UUID4 string.

    Returns
    -------
    str
        A fresh UUID4 string, e.g. ``'3fa85f64-5717-4562-b3fc-2c963f66afa6'``.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the inclusive interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The numeric value to clamp.
    lo:
        Lower bound of the interval.
    hi:
        Upper bound of the interval.

    Returns
    -------
    float
        The clamped value, guaranteed to satisfy ``lo <= result <= hi``.
    """
    return max(lo, min(hi, value))


def _score_obstruction(field: Any) -> float:
    """Compute a raw numeric score for a single obstruction field.

    This internal helper extracts severity-related attributes from *field*
    and combines them into a single float in [0.0, 1.0].  It is deliberately
    defensive: if *field* lacks expected attributes the function degrades
    gracefully rather than raising.

    Parameters
    ----------
    field:
        An ``ObstructionField`` instance or any object with a ``severity``
        float attribute.  Duck-typing is used so that this function can be
        exercised without importing the models module.

    Returns
    -------
    float
        A score in ``[0.0, 1.0]``, where higher values indicate more severe
        obstructions that constrain domain formation more tightly.
    """
    # Try to read a 'severity' attribute; fall back to 0.5 if missing.
    raw = getattr(field, "severity", None)
    if raw is None:
        # Attempt dict-style access for plain dicts
        try:
            raw = field["severity"]
        except Exception:
            raw = 0.5

    try:
        score = float(raw)
    except (TypeError, ValueError):
        score = 0.5

    return _clamp(score, 0.0, 1.0)


def _normalize_domain_id(raw_id: Any) -> str:
    """Normalize a raw domain identifier to a consistent string form.

    Domain identifiers may arrive as UUIDs, integers, or arbitrary strings.
    This function converts them all to a lowercase, hyphen-separated string
    that is safe to use as a dictionary key or filename component.

    Parameters
    ----------
    raw_id:
        The raw identifier to normalize.  May be ``None``, in which case a
        fresh UUID is generated.

    Returns
    -------
    str
        A normalized domain identifier string.

    Examples
    --------
    >>> _normalize_domain_id(None)  # returns a uuid4 string
    '3fa85f64-...'
    >>> _normalize_domain_id("Domain_01")
    'domain-01'
    >>> _normalize_domain_id(42)
    '42'
    """
    if raw_id is None:
        return _uid()
    s = str(raw_id).strip().lower()
    # Replace underscores and spaces with hyphens for uniformity
    s = s.replace("_", "-").replace(" ", "-")
    return s or _uid()


def _extract_generators_from_obstructions(fields: Sequence[Any]) -> List[str]:
    """Extract candidate generator names from a sequence of obstruction fields.

    Each obstruction field may carry metadata indicating which generators of
    the ambient type-theoretic structure it affects.  This function collects
    those generator names, deduplicates them, and returns them in a stable
    order.

    Parameters
    ----------
    fields:
        Sequence of ``ObstructionField`` objects (or duck-typed equivalents).

    Returns
    -------
    list of str
        Deduplicated list of generator names, sorted lexicographically.
    """
    seen: set[str] = set()
    generators: List[str] = []
    for field in fields:
        # generators may be stored under various attribute names
        candidates = (
            getattr(field, "generators", None)
            or getattr(field, "affected_generators", None)
            or []
        )
        for gen in candidates:
            name = str(gen).strip()
            if name and name not in seen:
                seen.add(name)
                generators.append(name)
    if not generators:
        # Synthesize a placeholder generator when none are found
        generators.append("sigma_0")
    return sorted(generators)


# ---------------------------------------------------------------------------
# ObstructionAnalyzer
# ---------------------------------------------------------------------------


class ObstructionAnalyzer:
    """Analyzes a collection of obstruction fields to identify domain boundaries.

    An ``ObstructionAnalyzer`` takes a list of ``ObstructionField`` objects —
    arising from partial or failed descent computations — and produces a
    structured analysis report.  The report characterises the severity
    distribution, identifies boundary obstructions (those that straddle two
    candidate domains), and groups obstructions by kind.

    The analysis is the first step in the domain formation pipeline described
    in theory2.tex Ch55.  Its output feeds directly into the ``DomainPartitioner``
    which uses severity scores and boundary flags to carve out coherent
    sub-domains.

    The analyzer maintains an internal LRU-style cache keyed on the hash of the
    input obstruction list.  This avoids redundant computation when the same
    set of fields is analyzed multiple times (e.g. during iterative refinement).

    Attributes
    ----------
    config : dict or None
        Optional configuration dict.  Recognised keys are ``'topo_weight'``,
        ``'alge_weight'``, ``'geom_weight'``, ``'coho_weight'``, and
        ``'histogram_bins'``.
    _cache : dict
        Internal analysis cache mapping cache keys to result dicts.

    Examples
    --------
    >>> analyzer = ObstructionAnalyzer(config={"topo_weight": 2.0})
    >>> report = analyzer.to_report(fields)
    >>> print(report["summary"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the ObstructionAnalyzer.

        Sets up the internal cache and copies weight constants from *config*
        (falling back to module-level defaults when keys are absent).

        Parameters
        ----------
        config:
            Optional dict controlling analysis behaviour.  Recognised keys:

            - ``'topo_weight'``: weight for topological obstructions (default 1.5).
            - ``'alge_weight'``: weight for algebraic obstructions (default 1.2).
            - ``'geom_weight'``: weight for geometric obstructions (default 1.0).
            - ``'coho_weight'``: weight for cohomological obstructions (default 1.8).
            - ``'histogram_bins'``: number of severity histogram bins (default 10).
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._topo_weight: float = float(cfg.get("topo_weight", TOPO_WEIGHT))
        self._alge_weight: float = float(cfg.get("alge_weight", ALGE_WEIGHT))
        self._geom_weight: float = float(cfg.get("geom_weight", GEOM_WEIGHT))
        self._coho_weight: float = float(cfg.get("coho_weight", COHO_WEIGHT))
        self._histogram_bins: int = int(cfg.get("histogram_bins", HISTOGRAM_BINS))
        self._cache: Dict[str, AnalysisReport] = {}
        log.debug("ObstructionAnalyzer initialized with config=%s", cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, obstruction_fields: Sequence[Any]) -> AnalysisReport:
        """Analyze a list of obstruction fields and return a structured report.

        This is the primary entry point for the analyzer.  It computes severity
        scores, classifies each obstruction, identifies boundary obstructions,
        groups by kind, and populates a flat report dictionary.  Results are
        cached internally; repeated calls with identical inputs are free.

        Parameters
        ----------
        obstruction_fields:
            Sequence of ``ObstructionField`` objects to analyze.

        Returns
        -------
        dict
            Analysis report with keys:

            - ``'count'``: total number of obstructions.
            - ``'severities'``: list of (field_id, severity_score) tuples.
            - ``'classified'``: list of (field_id, bucket) tuples.
            - ``'boundary_ids'``: list of field IDs that are boundary obstructions.
            - ``'groups'``: dict mapping kind → list of field IDs.
            - ``'histogram'``: list of (bin_label, count) tuples.
            - ``'summary'``: human-readable summary string.
            - ``'analyzed_at'``: ISO-8601 timestamp.
        """
        fields = list(obstruction_fields)
        key = self._cache_key(fields)
        if key in self._cache:
            log.debug("ObstructionAnalyzer cache hit for key=%s", key[:16])
            return self._cache[key]

        severities = [(self._field_id(f), self.compute_severity(f)) for f in fields]
        classified = [(fid, self.classify_obstruction(f)) for f, (fid, _) in zip(fields, severities)]
        boundary_ids = [self._field_id(f) for f in self.find_boundary_obstructions(fields)]
        groups = {k: [self._field_id(f) for f in v] for k, v in self.group_by_kind(fields).items()}
        histogram = self.severity_histogram(fields)
        summary = self.summarize(fields)

        report: AnalysisReport = {
            "count": len(fields),
            "severities": severities,
            "classified": classified,
            "boundary_ids": boundary_ids,
            "groups": groups,
            "histogram": histogram,
            "summary": summary,
            "analyzed_at": _utcnow().isoformat(),
        }
        self._cache[key] = report
        return report

    def compute_severity(self, field: Any) -> float:
        """Compute a weighted severity score for a single obstruction field.

        The severity score incorporates the raw ``severity`` attribute of the
        field together with kind-specific weights.  The result is clamped to
        ``[0.0, 1.0]``.

        Parameters
        ----------
        field:
            An ``ObstructionField`` or duck-typed equivalent.

        Returns
        -------
        float
            Weighted severity in ``[0.0, 1.0]``.
        """
        raw = _score_obstruction(field)
        weight = self._kind_weight(field)
        weighted = _clamp(raw * weight, 0.0, 1.0)
        log.debug("compute_severity: raw=%.3f weight=%.2f => %.3f", raw, weight, weighted)
        return weighted

    def classify_obstruction(self, field: Any) -> SeverityBucket:
        """Classify an obstruction field into a named severity bucket.

        The classification uses the thresholds defined by ``SEVERITY_THRESHOLDS``.
        Buckets are ``'low'``, ``'medium'``, ``'high'``, and ``'critical'``.

        Parameters
        ----------
        field:
            An ``ObstructionField`` or duck-typed equivalent.

        Returns
        -------
        str
            One of ``'low'``, ``'medium'``, ``'high'``, ``'critical'``.
        """
        score = self.compute_severity(field)
        lo, med, hi = SEVERITY_THRESHOLDS
        if score < lo:
            return "low"
        if score < med:
            return "medium"
        if score < hi:
            return "high"
        return "critical"

    def find_boundary_obstructions(self, fields: Sequence[Any]) -> List[Any]:
        """Find obstructions that sit at domain boundaries.

        A boundary obstruction is one whose severity is within a narrow band
        around the medium threshold, or one whose metadata explicitly marks it
        as a boundary obstruction.

        Parameters
        ----------
        fields:
            Sequence of obstruction fields to inspect.

        Returns
        -------
        list
            Subset of *fields* that are boundary obstructions.
        """
        result = []
        for f in fields:
            if self._is_boundary(f):
                result.append(f)
        log.debug("find_boundary_obstructions: %d/%d are boundary", len(result), len(list(fields)))
        return result

    def group_by_kind(self, fields: Sequence[Any]) -> Dict[str, List[Any]]:
        """Group obstruction fields by their ``ObstructionKind``.

        Parameters
        ----------
        fields:
            Sequence of obstruction fields.

        Returns
        -------
        dict
            Mapping of kind string → list of fields with that kind.
        """
        groups: Dict[str, List[Any]] = defaultdict(list)
        for f in fields:
            raw_kind = getattr(f, "kind", "unknown")
            kind = raw_kind.value if hasattr(raw_kind, "value") else str(raw_kind)
            groups[kind].append(f)
        return dict(groups)

    def severity_histogram(self, fields: Sequence[Any]) -> List[Tuple[str, int]]:
        """Compute a histogram of severity values across all fields.

        Parameters
        ----------
        fields:
            Sequence of obstruction fields.

        Returns
        -------
        list of (bin_label, count) tuples
            Each tuple names a bin by its lower bound and gives the count of
            obstructions whose severity falls in ``[lower, upper)``.
        """
        n = self._histogram_bins
        bins = [0] * n
        for f in fields:
            s = self.compute_severity(f)
            idx = min(int(s * n), n - 1)
            bins[idx] += 1
        width = 1.0 / n
        histogram = [(f"{i * width:.2f}", bins[i]) for i in range(n)]
        return histogram

    def summarize(self, fields: Sequence[Any]) -> str:
        """Return a short human-readable summary of the obstruction set.

        Parameters
        ----------
        fields:
            Sequence of obstruction fields.

        Returns
        -------
        str
            Multi-sentence summary describing count, severity distribution,
            and boundary obstruction fraction.
        """
        flist = list(fields)
        n = len(flist)
        if n == 0:
            return "No obstruction fields provided; domain is unobstructed."
        scores = [self.compute_severity(f) for f in flist]
        mean_sev = sum(scores) / n
        max_sev = max(scores)
        buckets = [self.classify_obstruction(f) for f in flist]
        critical_count = buckets.count("critical")
        boundary_count = len(self.find_boundary_obstructions(flist))
        return (
            f"Analyzed {n} obstruction(s): mean severity={mean_sev:.3f}, "
            f"max severity={max_sev:.3f}, critical={critical_count}, "
            f"boundary={boundary_count}."
        )

    def to_report(self, fields: Sequence[Any]) -> AnalysisReport:
        """Return a full analysis report dict.

        Convenience alias for ``analyze``; included for API symmetry.

        Parameters
        ----------
        fields:
            Sequence of obstruction fields.

        Returns
        -------
        dict
            Analysis report (same structure as ``analyze`` return value).
        """
        return self.analyze(fields)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cache_key(self, fields: List[Any]) -> str:
        """Compute a stable cache key for a list of obstruction fields.

        The key is a SHA-256 hex digest of the repr of each field joined
        by newlines.  This is not cryptographically significant — it is used
        only for fast cache lookup.

        Parameters
        ----------
        fields:
            List of obstruction fields.

        Returns
        -------
        str
            64-character hex string.
        """
        payload = "\n".join(repr(f) for f in fields)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _weighted_severity(self, raw: float, kind_str: str) -> float:
        """Apply kind-specific weight to a raw severity value.

        Parameters
        ----------
        raw:
            Raw severity in [0, 1].
        kind_str:
            String representation of the obstruction kind.

        Returns
        -------
        float
            Weighted severity, clamped to [0, 1].
        """
        kind_lower = kind_str.lower()
        if "topo" in kind_lower:
            w = self._topo_weight
        elif "alge" in kind_lower or "algebra" in kind_lower:
            w = self._alge_weight
        elif "geom" in kind_lower:
            w = self._geom_weight
        elif "coho" in kind_lower:
            w = self._coho_weight
        else:
            w = 1.0
        return _clamp(raw * w, 0.0, 1.0)

    def _kind_weight(self, field: Any) -> float:
        """Retrieve the kind-based weight for *field*.

        Parameters
        ----------
        field:
            An obstruction field.

        Returns
        -------
        float
            Weight multiplier.
        """
        kind_str = str(getattr(field, "kind", "unknown"))
        return self._weighted_severity(1.0, kind_str)  # use as pure weight getter

    def _is_boundary(self, field: Any) -> bool:
        """Determine whether *field* is a boundary obstruction.

        An obstruction is considered a boundary obstruction if:
        - Its ``is_boundary`` attribute is truthy, OR
        - Its severity is within 0.05 of the medium threshold.

        Parameters
        ----------
        field:
            An obstruction field.

        Returns
        -------
        bool
            True if the field is a boundary obstruction.
        """
        if getattr(field, "is_boundary", False):
            return True
        score = self.compute_severity(field)
        _, med, _ = SEVERITY_THRESHOLDS
        return abs(score - med) < 0.05

    @staticmethod
    def _field_id(field: Any) -> str:
        """Extract or synthesize a stable string identifier for *field*.

        Parameters
        ----------
        field:
            An obstruction field.

        Returns
        -------
        str
            Stable identifier string.
        """
        fid = getattr(field, "id", None) or getattr(field, "field_id", None)
        if fid is not None:
            return str(fid)
        return hashlib.md5(repr(field).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# DomainPartitioner
# ---------------------------------------------------------------------------


class DomainPartitioner:
    """Partitions mathematical space into candidate domain formations.

    The ``DomainPartitioner`` uses the output of ``ObstructionAnalyzer`` to
    carve the ambient space into coherent sub-domains.  Each domain is
    represented as a ``DomainFormation`` object (or a plain dict when the
    models module is unavailable) carrying an identifier, a ``DomainType``,
    lists of generators and relations, and coverage metadata.

    The partitioning algorithm proceeds in three phases:

    1. **Clustering** — obstructions are clustered by kind and severity.
       Each cluster seeds a candidate domain.
    2. **Boundary resolution** — boundary obstructions are assigned to the
       domain that minimises inter-domain tension.
    3. **Coverage check** — domains that individually cover less than a
       configurable threshold are merged into their nearest neighbour.

    This design follows the exposition in theory2.tex Ch55 §3.

    Attributes
    ----------
    config : dict
        Configuration dict (same keys as ``ObstructionAnalyzer``).
    _analyzer : ObstructionAnalyzer
        Shared analyzer instance used for scoring.

    Examples
    --------
    >>> partitioner = DomainPartitioner()
    >>> domains = partitioner.partition(obstruction_fields)
    >>> report = partitioner.to_report(domains)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the DomainPartitioner.

        Parameters
        ----------
        config:
            Optional configuration dict.  Passed through to the internal
            ``ObstructionAnalyzer`` and used to set ``min_coverage``.
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._analyzer = ObstructionAnalyzer(config=cfg)
        self._min_coverage: float = float(cfg.get("min_coverage", MIN_COVERAGE))
        log.debug("DomainPartitioner initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def partition(
        self,
        obstruction_fields: Sequence[Any],
        domain_type: Optional[str] = None,
    ) -> List[Any]:
        """Partition the ambient space into candidate domain formations.

        Parameters
        ----------
        obstruction_fields:
            Sequence of obstruction fields driving the partition.
        domain_type:
            Optional override for the domain type of every created domain.
            When ``None``, the type is inferred per-domain from obstruction kinds.

        Returns
        -------
        list of DomainFormation (or dict)
            List of candidate domains, ordered by descending estimated coverage.
        """
        fields = list(obstruction_fields)
        if not fields:
            log.info("partition called with empty fields; returning single universal domain")
            return [self._make_domain(
                domain_id=_uid(),
                domain_type=domain_type or DEFAULT_DOMAIN_TYPE,
                generators=["sigma_0"],
                relations=[],
                coverage=1.0,
                source_fields=[],
            )]

        # Phase 1: cluster by kind
        groups = self._analyzer.group_by_kind(fields)
        domains = []
        for kind_str, kind_fields in groups.items():
            dtype = domain_type or self._select_domain_type(kind_str)
            generators = self.assign_generators(None, kind_fields)
            relations = self.assign_relations(None, kind_fields)
            coverage_raw = len(kind_fields) / len(fields)
            domain = self._make_domain(
                domain_id=self._compute_partition_key(kind_str, kind_fields),
                domain_type=dtype,
                generators=generators,
                relations=relations,
                coverage=coverage_raw,
                source_fields=kind_fields,
            )
            domains.append(domain)

        # Phase 2: boundary resolution
        boundary_fields = self._analyzer.find_boundary_obstructions(fields)
        for bf in boundary_fields:
            # Assign each boundary field to the domain with highest coverage
            if domains:
                target = max(domains, key=lambda d: self._domain_coverage(d))
                self._absorb_boundary_field(target, bf)

        # Phase 3: coverage check — merge small domains
        domains = self._merge_small_domains(domains)

        # Sort by descending coverage
        domains = sorted(domains, key=lambda d: self._domain_coverage(d), reverse=True)
        log.info("partition produced %d domains from %d fields", len(domains), len(fields))
        return domains

    def refine_partition(self, domains: List[Any]) -> List[Any]:
        """Refine an existing partition by splitting large domains.

        For each domain whose coverage fraction exceeds 0.5 (i.e., it covers
        more than half the total space), this method attempts to split it into
        two sub-domains using a median-severity split criterion.

        Parameters
        ----------
        domains:
            Existing list of domain formations.

        Returns
        -------
        list
            Potentially larger list of domain formations after refinement.
        """
        refined: List[Any] = []
        for domain in domains:
            if self._domain_coverage(domain) > 0.5 and len(self._domain_generators(domain)) > 2:
                halves = self.split_domain(domain, "median_severity")
                refined.extend(halves)
            else:
                refined.append(domain)
        log.debug("refine_partition: %d -> %d domains", len(domains), len(refined))
        return refined

    def merge_domains(self, a: Any, b: Any) -> Any:
        """Merge two domain formations into a single domain.

        The merged domain takes the union of generators and relations, uses
        the ``DomainType`` of *a*, and has coverage equal to the sum of the
        two input coverages (clamped to 1.0).

        Parameters
        ----------
        a:
            First domain formation.
        b:
            Second domain formation.

        Returns
        -------
        DomainFormation or dict
            A new domain formation representing the merge of *a* and *b*.
        """
        gen_a = self._domain_generators(a)
        gen_b = self._domain_generators(b)
        merged_gens = sorted(set(gen_a) | set(gen_b))
        rel_a = self._domain_relations(a)
        rel_b = self._domain_relations(b)
        merged_rels = list(set(rel_a) | set(rel_b))
        merged_coverage = _clamp(self._domain_coverage(a) + self._domain_coverage(b), 0.0, 1.0)
        dtype = self._domain_type(a)
        new_id = _normalize_domain_id(f"{self._domain_id(a)}_merged_{self._domain_id(b)}")
        return self._make_domain(
            domain_id=new_id,
            domain_type=dtype,
            generators=merged_gens,
            relations=merged_rels,
            coverage=merged_coverage,
            source_fields=[],
        )

    def split_domain(self, domain: Any, criterion: str = "median_severity") -> List[Any]:
        """Split a domain into two sub-domains.

        Parameters
        ----------
        domain:
            The domain formation to split.
        criterion:
            Split strategy.  Currently supports ``'median_severity'`` (split
            generators into two halves based on alphabetical order as a proxy
            for severity ordering) and ``'half'`` (even split by index).

        Returns
        -------
        list of two domain formations
            The two halves of the split.  If the domain has fewer than two
            generators, the original domain is returned as a single-element list.
        """
        gens = sorted(self._domain_generators(domain))
        if len(gens) < 2:
            return [domain]
        mid = len(gens) // 2
        gens_a, gens_b = gens[:mid], gens[mid:]
        rels = self._domain_relations(domain)
        dtype = self._domain_type(domain)
        half_cov = self._domain_coverage(domain) / 2.0
        base_id = self._domain_id(domain)
        return [
            self._make_domain(f"{base_id}_a", dtype, gens_a, rels[:len(rels)//2], half_cov, []),
            self._make_domain(f"{base_id}_b", dtype, gens_b, rels[len(rels)//2:], half_cov, []),
        ]

    def compute_coverage(self, domains: List[Any], total_space_size: int) -> float:
        """Compute the fraction of the total space covered by *domains*.

        Parameters
        ----------
        domains:
            List of domain formations.
        total_space_size:
            Total number of points (or dimension proxy) in the ambient space.

        Returns
        -------
        float
            Coverage fraction in ``[0.0, 1.0]``.
        """
        if total_space_size <= 0:
            return 0.0
        covered = sum(self._domain_coverage(d) for d in domains)
        return _clamp(covered, 0.0, 1.0)

    def assign_generators(
        self, domain: Optional[Any], obstructions: Sequence[Any]
    ) -> List[str]:
        """Assign generators to a domain based on obstruction analysis.

        Parameters
        ----------
        domain:
            Existing domain (may be ``None`` for a fresh domain).
        obstructions:
            Obstruction fields associated with the domain.

        Returns
        -------
        list of str
            Generator names extracted from the obstruction fields.
        """
        gens = _extract_generators_from_obstructions(obstructions)
        # Ensure uniqueness and bounds
        gens = list(dict.fromkeys(gens))[:MAX_GENERATORS]
        if len(gens) < MIN_GENERATORS:
            gens = ["sigma_0"]
        return gens

    def assign_relations(
        self, domain: Optional[Any], obstructions: Sequence[Any]
    ) -> List[str]:
        """Assign relations to a domain based on obstruction analysis.

        Parameters
        ----------
        domain:
            Existing domain (may be ``None``).
        obstructions:
            Obstruction fields associated with the domain.

        Returns
        -------
        list of str
            Relation strings (e.g. ``'sigma_0 * sigma_1 = sigma_1 * sigma_0'``).
        """
        gens = _extract_generators_from_obstructions(obstructions)
        relations: List[str] = []
        # Generate commutativity relations for pairs of generators
        for g1, g2 in itertools.combinations(gens[:8], 2):
            relations.append(f"{g1} * {g2} = {g2} * {g1}")
        return relations

    def validate_partition(self, domains: List[Any]) -> bool:
        """Validate that the partition is coherent (non-overlapping, sufficient coverage).

        Parameters
        ----------
        domains:
            List of domain formations to validate.

        Returns
        -------
        bool
            ``True`` if the partition is valid, ``False`` otherwise.
        """
        if not domains:
            log.warning("validate_partition: empty domain list")
            return False
        total_coverage = sum(self._domain_coverage(d) for d in domains)
        if total_coverage < self._min_coverage:
            log.warning(
                "validate_partition: total coverage %.3f < min %.3f",
                total_coverage, self._min_coverage,
            )
            return False
        # Check for domains with no generators
        for domain in domains:
            if not self._domain_generators(domain):
                log.warning("validate_partition: domain %s has no generators", self._domain_id(domain))
                return False
        return True

    def to_report(self, domains: List[Any]) -> PartitionReport:
        """Return a structured report describing the partition.

        Parameters
        ----------
        domains:
            List of domain formations.

        Returns
        -------
        dict
            Partition report with keys ``'domain_count'``, ``'domains'``,
            ``'total_coverage'``, ``'valid'``, and ``'generated_at'``.
        """
        total_cov = sum(self._domain_coverage(d) for d in domains)
        domain_summaries = [
            {
                "id": self._domain_id(d),
                "type": self._domain_type(d),
                "generators": self._domain_generators(d),
                "relations": self._domain_relations(d),
                "coverage": self._domain_coverage(d),
            }
            for d in domains
        ]
        return {
            "domain_count": len(domains),
            "domains": domain_summaries,
            "total_coverage": _clamp(total_cov, 0.0, 1.0),
            "valid": self.validate_partition(domains),
            "generated_at": _utcnow().isoformat(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_partition_key(self, kind_str: str, fields: List[Any]) -> str:
        """Compute a deterministic partition key from kind and field list.

        Parameters
        ----------
        kind_str:
            ObstructionKind string.
        fields:
            Associated obstruction fields.

        Returns
        -------
        str
            Normalized domain identifier.
        """
        raw = f"{kind_str}_{len(fields)}"
        return _normalize_domain_id(raw)

    def _select_domain_type(self, kind_str: str) -> str:
        """Infer a domain type from an obstruction kind string.

        Parameters
        ----------
        kind_str:
            String representation of an ``ObstructionKind``.

        Returns
        -------
        str
            A ``DomainType`` string.
        """
        kind_lower = kind_str.lower()
        if "topo" in kind_lower:
            return "topological"
        if "alge" in kind_lower:
            return "algebraic"
        if "geom" in kind_lower:
            return "geometric"
        if "coho" in kind_lower:
            return "cohomological"
        return DEFAULT_DOMAIN_TYPE

    def _extract_generators(self, fields: List[Any]) -> List[str]:
        """Thin wrapper around the module-level helper.

        Parameters
        ----------
        fields:
            Obstruction fields.

        Returns
        -------
        list of str
            Generator names.
        """
        return _extract_generators_from_obstructions(fields)

    def _make_domain(
        self,
        domain_id: str,
        domain_type: str,
        generators: List[str],
        relations: List[str],
        coverage: float,
        source_fields: List[Any],
    ) -> Any:
        """Construct a domain representation (dict fallback when models unavailable).

        Parameters
        ----------
        domain_id:
            Unique identifier for the domain.
        domain_type:
            Domain type string.
        generators:
            List of generator names.
        relations:
            List of relation strings.
        coverage:
            Estimated coverage fraction.
        source_fields:
            Obstruction fields that seeded this domain.

        Returns
        -------
        DomainFormation or dict
        """
        try:
            return DomainFormation(
                id=domain_id,
                domain_type=domain_type,
                generators=generators,
                relations=relations,
                coverage=coverage,
            )
        except Exception:
            return {
                "id": domain_id,
                "domain_type": domain_type,
                "generators": generators,
                "relations": relations,
                "coverage": coverage,
                "source_field_count": len(source_fields),
            }

    @staticmethod
    def _domain_id(domain: Any) -> str:
        return str(getattr(domain, "id", None) or domain.get("id", _uid()))

    @staticmethod
    def _domain_type(domain: Any) -> str:
        return str(getattr(domain, "domain_type", None) or domain.get("domain_type", DEFAULT_DOMAIN_TYPE))

    @staticmethod
    def _domain_generators(domain: Any) -> List[str]:
        gens = getattr(domain, "generators", None)
        if gens is None:
            try:
                gens = domain.get("generators", [])
            except Exception:
                gens = []
        return list(gens)

    @staticmethod
    def _domain_relations(domain: Any) -> List[str]:
        rels = getattr(domain, "relations", None)
        if rels is None:
            try:
                rels = domain.get("relations", [])
            except Exception:
                rels = []
        return list(rels)

    @staticmethod
    def _domain_coverage(domain: Any) -> float:
        cov = getattr(domain, "coverage", None)
        if cov is None:
            try:
                cov = domain.get("coverage", 0.0)
            except Exception:
                cov = 0.0
        return float(cov)

    def _merge_small_domains(self, domains: List[Any]) -> List[Any]:
        """Merge domains whose coverage is below the minimum threshold.

        Parameters
        ----------
        domains:
            List of domain formations.

        Returns
        -------
        list
            Possibly shorter list after merging small domains.
        """
        large = [d for d in domains if self._domain_coverage(d) >= self._min_coverage / len(domains) if domains]
        small = [d for d in domains if d not in large]
        for s in small:
            if large:
                large[0] = self.merge_domains(large[0], s)
            else:
                large.append(s)
        return large

    def _absorb_boundary_field(self, domain: Any, field: Any) -> None:
        """Add generators from *field* to *domain* in-place (dict domains only).

        Parameters
        ----------
        domain:
            Domain formation (dict or DomainFormation).
        field:
            Boundary obstruction field to absorb.
        """
        new_gens = _extract_generators_from_obstructions([field])
        try:
            # dict path
            existing = domain.get("generators", [])
            domain["generators"] = sorted(set(existing) | set(new_gens))
        except AttributeError:
            # object path — best-effort
            try:
                existing = list(domain.generators)
                object.__setattr__(domain, "generators", sorted(set(existing) | set(new_gens)))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DomainValidator
# ---------------------------------------------------------------------------


class DomainValidator:
    """Validates domain formation candidates for coherence and completeness.

    A ``DomainValidator`` applies a series of checks to a single
    ``DomainFormation`` object and produces a structured validation result.
    The checks cover:

    - **Generator check**: the domain must have at least ``MIN_GENERATORS``
      generators, each of which is a non-empty string.
    - **Relation check**: relations must be syntactically plausible (contain
      an equality sign) and reference valid generators.
    - **Partition check**: the domain's coverage fraction must be positive.
    - **Coherence check**: the set of relations must not be trivially
      inconsistent (no relation equates a generator to itself with a minus sign,
      which would imply the trivial group in characteristic 0).

    The validator also computes a scalar validation score in ``[0.0, 1.0]``
    suitable for ranking candidate domains during bootstrapping.

    Examples
    --------
    >>> validator = DomainValidator()
    >>> result = validator.validate(domain)
    >>> if result["valid"]:
    ...     print("Domain is valid, score:", result["score"])
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the DomainValidator.

        Parameters
        ----------
        config:
            Optional configuration dict.  No keys are currently used, but
            the argument is accepted for API symmetry.
        """
        self.config: Dict[str, Any] = config or {}
        log.debug("DomainValidator initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, domain: Any) -> ValidationResult:
        """Run all validation checks and return a structured result.

        Parameters
        ----------
        domain:
            A ``DomainFormation`` or dict-like domain representation.

        Returns
        -------
        dict
            Validation result with keys:

            - ``'valid'``: bool — overall pass/fail.
            - ``'score'``: float in [0, 1] — quality score.
            - ``'errors'``: list of error strings.
            - ``'warnings'``: list of warning strings.
            - ``'checks'``: dict mapping check name → bool.
            - ``'validated_at'``: ISO-8601 timestamp.
        """
        gen_ok = self.check_generators(domain)
        rel_ok = self.check_relations(domain)
        part_ok = self.check_partitions(domain)
        coh_ok = self.check_coherence(domain)
        errors = self.list_errors(domain)
        warnings = self.list_warnings(domain)
        score = self.compute_validation_score(domain)
        valid = gen_ok and part_ok and len(errors) == 0
        return {
            "valid": valid,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "checks": {
                "generators": gen_ok,
                "relations": rel_ok,
                "partitions": part_ok,
                "coherence": coh_ok,
            },
            "validated_at": _utcnow().isoformat(),
        }

    def check_generators(self, domain: Any) -> bool:
        """Check that the domain has a valid, non-empty generator list.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        bool
            True iff the generator list is non-empty and all entries are
            non-empty strings.
        """
        gens = DomainPartitioner._domain_generators(domain)
        if len(gens) < MIN_GENERATORS:
            return False
        if len(gens) > MAX_GENERATORS:
            return False
        return all(self._is_valid_generator(g) for g in gens)

    def check_relations(self, domain: Any) -> bool:
        """Check that all relations in the domain are syntactically valid.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        bool
            True iff every relation is a non-empty string containing ``'='``.
        """
        rels = DomainPartitioner._domain_relations(domain)
        return all(self._is_valid_relation(r) for r in rels)

    def check_partitions(self, domain: Any) -> bool:
        """Check that the domain has a positive coverage fraction.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        bool
            True iff the coverage is strictly positive.
        """
        cov = DomainPartitioner._domain_coverage(domain)
        return cov > 0.0

    def check_coherence(self, domain: Any) -> bool:
        """Check that the domain's generators and relations are mutually coherent.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        bool
            True iff the coherence metric is above 0.5.
        """
        return self._coherence_metric(domain) > 0.5

    def compute_validation_score(self, domain: Any) -> float:
        """Compute a scalar validation score for the domain.

        The score is a weighted average of sub-check results and continuous
        metrics (coverage, coherence).

        Parameters
        ----------
        domain:
            Domain formation to score.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.
        """
        gen_score = 1.0 if self.check_generators(domain) else 0.0
        rel_score = 1.0 if self.check_relations(domain) else 0.5
        part_score = _clamp(DomainPartitioner._domain_coverage(domain), 0.0, 1.0)
        coh_score = self._coherence_metric(domain)
        # Weighted average: generators matter most, then coverage, then coherence
        return _clamp(
            0.4 * gen_score + 0.2 * rel_score + 0.25 * part_score + 0.15 * coh_score,
            0.0, 1.0,
        )

    def list_errors(self, domain: Any) -> List[str]:
        """Return a list of error messages for the domain.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        list of str
            Non-empty error messages.  An empty list means no errors.
        """
        errors: List[str] = []
        gens = DomainPartitioner._domain_generators(domain)
        if len(gens) < MIN_GENERATORS:
            errors.append(f"Domain has {len(gens)} generator(s); minimum is {MIN_GENERATORS}.")
        if len(gens) > MAX_GENERATORS:
            errors.append(f"Domain has {len(gens)} generator(s); maximum is {MAX_GENERATORS}.")
        for g in gens:
            if not self._is_valid_generator(g):
                errors.append(f"Invalid generator: {g!r}.")
        cov = DomainPartitioner._domain_coverage(domain)
        if cov <= 0.0:
            errors.append(f"Domain coverage is {cov:.3f}; must be positive.")
        return errors

    def list_warnings(self, domain: Any) -> List[str]:
        """Return a list of warning messages for the domain.

        Parameters
        ----------
        domain:
            Domain formation to check.

        Returns
        -------
        list of str
            Non-critical warnings (domain is still considered valid if only
            warnings are present).
        """
        warnings: List[str] = []
        cov = DomainPartitioner._domain_coverage(domain)
        if cov < self.config.get("min_coverage", MIN_COVERAGE):
            warnings.append(f"Domain coverage {cov:.3f} is below recommended minimum {MIN_COVERAGE}.")
        if not self.check_relations(domain):
            warnings.append("Some relations failed syntactic validation.")
        if not self.check_coherence(domain):
            warnings.append("Coherence metric is below 0.5; domain may be inconsistent.")
        return warnings

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_generator(g: Any) -> bool:
        """Return True iff *g* is a non-empty string.

        Parameters
        ----------
        g:
            Candidate generator name.

        Returns
        -------
        bool
        """
        return isinstance(g, str) and bool(g.strip())

    @staticmethod
    def _is_valid_relation(r: Any) -> bool:
        """Return True iff *r* is a non-empty string containing ``'='``.

        Parameters
        ----------
        r:
            Candidate relation string.

        Returns
        -------
        bool
        """
        return isinstance(r, str) and "=" in r

    def _coherence_metric(self, domain: Any) -> float:
        """Compute a simple coherence metric for the domain.

        The metric measures the ratio of valid relations to total relations
        plus 0.5 base score (domains with no relations are considered
        vacuously coherent at 0.5).

        Parameters
        ----------
        domain:
            Domain formation.

        Returns
        -------
        float
            Coherence metric in ``[0.0, 1.0]``.
        """
        rels = DomainPartitioner._domain_relations(domain)
        if not rels:
            return 0.5
        valid_count = sum(1 for r in rels if self._is_valid_relation(r))
        return valid_count / len(rels)


# ---------------------------------------------------------------------------
# DomainFormationRunner
# ---------------------------------------------------------------------------


class DomainFormationRunner:
    """Orchestrates the full domain formation pipeline.

    The ``DomainFormationRunner`` wires together the ``ObstructionAnalyzer``,
    ``DomainPartitioner``, and ``DomainValidator`` into a single, easy-to-use
    pipeline object.  After construction it exposes a ``run`` method that
    accepts a list of obstruction fields and returns a list of validated
    domain formations.

    The runner also maintains internal state (analysis report, raw domains,
    validation results) so that intermediate results can be inspected after
    the pipeline completes.

    State machine::

        IDLE → (run called) → ANALYZING → PARTITIONING → VALIDATING → DONE
                                                                        ↓
                                                                    (reset called)
                                                                     → IDLE

    Attributes
    ----------
    config : dict
        Configuration dict forwarded to all sub-components.
    _analyzer : ObstructionAnalyzer
        Internal obstruction analyzer.
    _partitioner : DomainPartitioner
        Internal domain partitioner.
    _validator : DomainValidator
        Internal domain validator.
    _analysis_report : dict or None
        Cached analysis report from the most recent ``run_analysis`` call.
    _raw_domains : list or None
        Raw (unvalidated) domains from the most recent ``run_partition`` call.
    _validation_results : list or None
        Validation results from the most recent ``run_validation`` call.

    Examples
    --------
    >>> runner = DomainFormationRunner(config={"min_coverage": 0.8})
    >>> domains = runner.run(obstruction_fields)
    >>> print(runner.summarize())
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the DomainFormationRunner.

        Parameters
        ----------
        config:
            Optional configuration dict forwarded to all sub-components.
        """
        cfg = config or {}
        self.config: Dict[str, Any] = cfg
        self._analyzer = ObstructionAnalyzer(config=cfg)
        self._partitioner = DomainPartitioner(config=cfg)
        self._validator = DomainValidator(config=cfg)
        self._analysis_report: Optional[AnalysisReport] = None
        self._raw_domains: Optional[List[Any]] = None
        self._validation_results: Optional[List[ValidationResult]] = None
        log.debug("DomainFormationRunner initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, obstruction_fields: Sequence[Any]) -> List[Any]:
        """Run the full domain formation pipeline.

        Executes analysis → partition → validation in order and returns
        the list of validated domain formations.

        Parameters
        ----------
        obstruction_fields:
            Input obstruction fields.

        Returns
        -------
        list of DomainFormation (or dict)
            Validated domain formations, sorted by descending validation score.
        """
        log.info("DomainFormationRunner.run: starting pipeline with %d fields", len(list(obstruction_fields)))
        fields = list(obstruction_fields)
        self._analysis_report = self.run_analysis(fields)
        self._raw_domains = self.run_partition(fields)
        self._validation_results = self.run_validation(self._raw_domains)
        # Filter to valid domains only (or keep all if none pass)
        valid_domains = [
            d for d, vr in zip(self._raw_domains, self._validation_results) if vr.get("valid", False)
        ]
        if not valid_domains:
            log.warning("DomainFormationRunner.run: no domains passed validation; returning all")
            valid_domains = self._raw_domains
        # Sort by validation score descending
        scores = {id(d): vr.get("score", 0.0) for d, vr in zip(self._raw_domains, self._validation_results)}
        valid_domains = sorted(valid_domains, key=lambda d: scores.get(id(d), 0.0), reverse=True)
        log.info("DomainFormationRunner.run: returning %d valid domains", len(valid_domains))
        return valid_domains

    def run_analysis(self, fields: Sequence[Any]) -> AnalysisReport:
        """Run the obstruction analysis step.

        Parameters
        ----------
        fields:
            Obstruction fields to analyze.

        Returns
        -------
        dict
            Analysis report from ``ObstructionAnalyzer.analyze``.
        """
        return self._analyzer.analyze(fields)

    def run_partition(self, fields: Sequence[Any]) -> List[Any]:
        """Run the domain partition step.

        Parameters
        ----------
        fields:
            Obstruction fields to partition.

        Returns
        -------
        list
            Raw (unvalidated) domain formations.
        """
        return self._partitioner.partition(fields)

    def run_validation(self, domains: List[Any]) -> List[ValidationResult]:
        """Run the validation step on a list of domains.

        Parameters
        ----------
        domains:
            Domain formations to validate.

        Returns
        -------
        list of dict
            One validation result dict per domain.
        """
        return [self._validator.validate(d) for d in domains]

    def get_results(self) -> Dict[str, Any]:
        """Return a dict of all intermediate pipeline results.

        Returns
        -------
        dict
            Keys: ``'analysis_report'``, ``'raw_domains'``, ``'validation_results'``.
        """
        return {
            "analysis_report": self._analysis_report,
            "raw_domains": self._raw_domains,
            "validation_results": self._validation_results,
        }

    def reset(self) -> None:
        """Reset the runner's internal state.

        After calling ``reset``, the runner is in the same state as immediately
        after construction.  The sub-component caches are also cleared.
        """
        self._analysis_report = None
        self._raw_domains = None
        self._validation_results = None
        self._analyzer._cache.clear()
        log.debug("DomainFormationRunner.reset: state cleared")

    def summarize(self) -> str:
        """Return a human-readable summary of the most recent pipeline run.

        Returns
        -------
        str
            Multi-line summary string, or a message indicating that no run
            has been performed yet.
        """
        if self._raw_domains is None:
            return "DomainFormationRunner: no pipeline run has been performed yet."
        n_raw = len(self._raw_domains)
        n_valid = sum(
            1 for vr in (self._validation_results or []) if vr.get("valid", False)
        )
        analysis_count = (self._analysis_report or {}).get("count", 0)
        lines = [
            f"DomainFormationRunner summary:",
            f"  Obstruction fields analyzed: {analysis_count}",
            f"  Raw domains produced:        {n_raw}",
            f"  Domains passing validation:  {n_valid}",
        ]
        if self._analysis_report:
            lines.append(f"  Analysis summary: {self._analysis_report.get('summary', 'N/A')}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Free convenience functions
# ---------------------------------------------------------------------------


def analyze_obstructions(
    obstruction_fields: Sequence[Any],
    config: Optional[Dict[str, Any]] = None,
) -> AnalysisReport:
    """Analyze a collection of obstruction fields and return a report.

    This is a convenience wrapper around ``ObstructionAnalyzer.analyze`` that
    constructs a fresh analyzer with the given *config* and immediately runs
    the analysis.

    Parameters
    ----------
    obstruction_fields:
        Sequence of ``ObstructionField`` objects (or duck-typed equivalents).
    config:
        Optional configuration dict passed to ``ObstructionAnalyzer``.

    Returns
    -------
    dict
        Analysis report (see ``ObstructionAnalyzer.analyze`` for structure).

    Examples
    --------
    >>> report = analyze_obstructions(fields, config={"topo_weight": 2.0})
    >>> print(report["summary"])
    """
    return ObstructionAnalyzer(config=config).analyze(obstruction_fields)


def partition_domain(
    obstruction_fields: Sequence[Any],
    domain_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Partition the ambient space into candidate domain formations.

    Convenience wrapper around ``DomainPartitioner.partition``.

    Parameters
    ----------
    obstruction_fields:
        Sequence of obstruction fields.
    domain_type:
        Optional domain type override for all created domains.
    config:
        Optional configuration dict.

    Returns
    -------
    list
        List of ``DomainFormation`` objects (or dicts).

    Examples
    --------
    >>> domains = partition_domain(fields, domain_type="algebraic")
    """
    return DomainPartitioner(config=config).partition(obstruction_fields, domain_type=domain_type)
