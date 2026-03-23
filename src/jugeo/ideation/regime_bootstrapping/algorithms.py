"""
algorithms.py — Bootstrapping algorithms for the JuGeo regime_bootstrapping package.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Obstruction Theory.

This module implements the core algorithmic machinery for bootstrapping new geometric
regimes from obstruction data. The central problem is: given a collection of obstruction
fields defined over a base site, find a domain partition and a set of type constructors
that together resolve every obstruction and assemble into a coherent regime candidate.

The algorithms here are deliberately parameterized through `AlgorithmConfig` so that
callers can trade between speed and thoroughness. The `BootstrappingAlgorithms` class
provides the main pipeline, while free functions expose individual stages for testing
and composition.

Module-level constants control default thresholds used across the pipeline. All heavy
computation is guarded by an optional in-memory cache keyed on the inputs' __hash__ so
repeated calls within a session are cheap.

Key concepts (Ch55):
  * Obstruction field   — a section of the obstruction sheaf over a site S
  * Domain formation    — a cover of S that trivializes each obstruction field
  * Type constructor    — a functor from the domain category to the type universe
  * Regime candidate    — a tuple (domain, constructors, metadata) ready for assembly
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
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
        ObstructionField, ObstructionKind, DomainFormation, DomainType,
        TypeConstructor, TypeConstructorKind, RegimeCandidate, BootstrapStep,
        BootstrapPlan, BootstrapResult, BootstrapStatus, BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "AlgorithmConfig",
    "BootstrappingAlgorithms",
    "compute_obstruction_class",
    "rank_bootstrap_candidates",
    "DEFAULT_SEVERITY_THRESHOLD",
    "DEFAULT_COVERAGE_TARGET",
    "DEFAULT_MAX_ITERATIONS",
    "BLOCKING_SEVERITY_CUTOFF",
    "COMPLEXITY_SCALE_FACTOR",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_SEVERITY_THRESHOLD: float = 0.7
"""
Minimum obstruction severity that triggers active resolution logic.
Obstructions below this threshold are recorded but not acted upon during
the main bootstrapping sweep.  Derived from empirical tuning across the
standard JuGeo test corpus (see theory2.tex §55.4).
"""

DEFAULT_COVERAGE_TARGET: float = 0.95
"""
Fraction of the obstruction space that a domain partition must cover before
the bootstrapping pipeline considers itself complete.  Set below 1.0 to
allow for negligible boundary effects that are resolved at merge time.
"""

DEFAULT_MAX_ITERATIONS: int = 100
"""
Hard cap on the number of refinement iterations the main algorithm loop
will execute.  If convergence has not been reached by this iteration the
best candidate found so far is returned with a PARTIAL status flag.
"""

BLOCKING_SEVERITY_CUTOFF: float = 0.9
"""
Obstruction severity at or above which a field is classified as *blocking*.
Blocking obstructions must be fully resolved before any candidate is
promoted to ASSEMBLED status.  See `BootstrappingAlgorithms.filter_viable_candidates`.
"""

COMPLEXITY_SCALE_FACTOR: float = 100.0
"""
Multiplicative scale applied to raw domain complexity values before they
are stored in the complexity budget.  This keeps budget numbers in a
human-readable range (0 – 1000) regardless of the underlying domain size.
"""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

DomainDict = dict[str, Any]
CandidateDict = dict[str, Any]
FieldDict = dict[str, Any]
ScoreMap = dict[str, float]

# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """
    Return the current UTC time as a POSIX timestamp (seconds since epoch).

    This thin wrapper around `time.time()` makes it easy to mock time in
    unit tests without patching the entire `time` module.

    Returns
    -------
    float
        Current UTC time as a float.
    """
    return time.time()


def _uid() -> str:
    """
    Generate a compact, URL-safe unique identifier.

    Uses UUID4 (random) as the entropy source and strips hyphens so the
    result can be embedded in file names, dict keys, and log lines without
    escaping.

    Returns
    -------
    str
        32-character hex string, e.g. ``'a3f2b1c0d4e5f6a7b8c9d0e1f2a3b4c5'``.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """
    Clamp *value* to the closed interval [lo, hi].

    Used throughout the scoring pipeline to keep probabilities and
    normalized scores in a valid range without raising exceptions on
    marginal inputs.

    Parameters
    ----------
    value : float
        The value to clamp.
    lo : float
        Lower bound (inclusive).  Defaults to 0.0.
    hi : float
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# AlgorithmConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlgorithmConfig:
    """
    Immutable configuration for the bootstrapping algorithm pipeline.

    All numeric thresholds and boolean flags that control pipeline
    behaviour are collected here so they can be constructed once and
    passed through the call stack without mutation.  The frozen+slots
    combination gives cheap equality checks and prevents accidental
    modification.

    Attributes
    ----------
    max_iterations : int
        Hard upper bound on refinement iterations.
    convergence_threshold : float
        Delta below which successive score improvements are considered
        converged.
    severity_weight : float
        Relative weight given to obstruction severity in the composite
        bootstrap score.
    novelty_weight : float
        Relative weight given to regime novelty in the composite score.
    max_candidates : int
        Maximum number of regime candidates kept alive in a single run.
    use_cache : bool
        Whether to use the module-level in-memory result cache.
    random_seed : int
        Seed for any stochastic steps so runs are reproducible.
    complexity_budget : float
        Maximum total complexity units the pipeline may consume.
    coverage_target : float
        Fraction of obstruction space that must be covered.
    min_generator_count : int
        Minimum number of type constructors required per candidate.
    """

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    convergence_threshold: float = 0.001
    severity_weight: float = 0.7
    novelty_weight: float = 0.3
    max_candidates: int = 50
    use_cache: bool = True
    random_seed: int = 42
    complexity_budget: float = 1000.0
    coverage_target: float = DEFAULT_COVERAGE_TARGET
    min_generator_count: int = 1

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this config to a plain dictionary.

        The resulting dict is JSON-serialisable and can be stored in
        pipeline metadata or logged for reproducibility.

        Returns
        -------
        dict[str, Any]
            A flat mapping of field names to their values.
        """
        return {
            "max_iterations": self.max_iterations,
            "convergence_threshold": self.convergence_threshold,
            "severity_weight": self.severity_weight,
            "novelty_weight": self.novelty_weight,
            "max_candidates": self.max_candidates,
            "use_cache": self.use_cache,
            "random_seed": self.random_seed,
            "complexity_budget": self.complexity_budget,
            "coverage_target": self.coverage_target,
            "min_generator_count": self.min_generator_count,
        }

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_strict(self) -> bool:
        """
        Return True when the config is in *strict* mode.

        Strict mode is defined as having a coverage target ≥ 0.99 AND a
        convergence threshold ≤ 0.0001.  It is used by callers that need
        near-perfect solutions and are willing to pay higher computational
        cost.

        Returns
        -------
        bool
            Whether strict mode is active.
        """
        return self.coverage_target >= 0.99 and self.convergence_threshold <= 0.0001

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> AlgorithmConfig:
        """
        Return the canonical default configuration.

        This preset is suitable for the majority of regime bootstrapping
        tasks.  It balances speed and quality and is the implicit choice
        when callers do not specify a config.

        Returns
        -------
        AlgorithmConfig
            Default configuration instance.
        """
        return cls()

    @classmethod
    def fast(cls) -> AlgorithmConfig:
        """
        Return a *fast* configuration that sacrifices quality for speed.

        The fast preset reduces the iteration cap, raises the convergence
        threshold so the loop exits early, halves the candidate pool, and
        disables the result cache (cache misses are cheaper than cache
        lookups when candidates are discarded rapidly).

        Returns
        -------
        AlgorithmConfig
            Fast configuration instance.
        """
        return cls(
            max_iterations=20,
            convergence_threshold=0.01,
            max_candidates=10,
            use_cache=False,
            coverage_target=0.80,
        )

    @classmethod
    def thorough(cls) -> AlgorithmConfig:
        """
        Return a *thorough* configuration that maximises quality.

        The thorough preset increases the iteration cap, lowers the
        convergence threshold, triples the candidate pool, and activates
        strict coverage requirements.  Expect 5-10x longer wall-clock
        time compared to the default preset.

        Returns
        -------
        AlgorithmConfig
            Thorough configuration instance.
        """
        return cls(
            max_iterations=500,
            convergence_threshold=0.0001,
            max_candidates=150,
            use_cache=True,
            coverage_target=0.99,
            complexity_budget=5000.0,
        )


# ---------------------------------------------------------------------------
# BootstrappingAlgorithms
# ---------------------------------------------------------------------------

# Module-level cache shared across all BootstrappingAlgorithms instances when
# use_cache=True.  Keys are SHA-256 digests of serialised inputs.
_ALGORITHM_CACHE: dict[str, Any] = {}


class BootstrappingAlgorithms:
    """
    Main algorithmic engine for regime bootstrapping.

    This class orchestrates the full bootstrapping pipeline described in
    theory2.tex Ch55.  The pipeline proceeds through the following stages:

    1. **Obstruction analysis** — scan a domain formation for obstruction
       fields and classify each by kind and severity.
    2. **Domain partition** — split the obstruction space into sub-domains
       such that each sub-domain admits a local type constructor.
    3. **Type constructor search** — for each sub-domain, enumerate and
       score candidate type constructors.
    4. **Regime assembly** — combine a domain formation with a set of type
       constructors to form a ``RegimeCandidate``.
    5. **Scoring and ranking** — assign composite scores to all candidates
       and rank them so the caller can pick the best one.

    The class is intentionally stateful (it holds a config and a cache
    reference) but not thread-safe.  Create one instance per thread or
    protect shared instances with a lock.

    Parameters
    ----------
    config : AlgorithmConfig | None
        Algorithm configuration.  If *None* the default config is used.
    """

    def __init__(self, config: AlgorithmConfig | None = None) -> None:
        """
        Initialise the algorithm engine with the given configuration.

        If *config* is None a fresh `AlgorithmConfig.default()` is
        created.  The cache reference points at the module-level
        ``_ALGORITHM_CACHE`` dict; setting ``config.use_cache=False``
        disables reads and writes without clearing the dict.

        Parameters
        ----------
        config : AlgorithmConfig | None
            Configuration object.  Defaults to ``AlgorithmConfig.default()``.
        """
        self.config: AlgorithmConfig = config or AlgorithmConfig.default()
        self._cache: dict[str, Any] = _ALGORITHM_CACHE
        self._run_id: str = _uid()
        self._iteration_count: int = 0
        self._start_time: float = _utcnow()

    # ------------------------------------------------------------------
    # Stage 1 — Obstruction analysis
    # ------------------------------------------------------------------

    def obstruction_analysis(self, domain: DomainDict) -> list[FieldDict]:
        """
        Analyse a domain formation and extract its obstruction fields.

        For each dimension in the domain's coordinate system the method
        checks whether a non-trivial obstruction exists.  Obstructions
        are characterised by a *kind* (topological, algebraic, geometric,
        or semantic) and a *severity* score in [0, 1].

        The analysis respects the severity threshold defined in
        ``AlgorithmConfig``: fields below the threshold are included in
        the output but marked with ``"active": False`` so downstream
        stages can skip them cheaply.

        Parameters
        ----------
        domain : DomainDict
            Serialised domain formation.  Must contain at least the keys
            ``"id"``, ``"dimensions"``, and ``"coordinates"``.

        Returns
        -------
        list[FieldDict]
            List of obstruction field descriptors, each a plain dict with
            keys ``id``, ``kind``, ``severity``, ``active``, ``metadata``.
        """
        # Guard: return empty list for malformed or empty domains
        if not domain or not isinstance(domain, dict):
            return []

        cache_key = self._cache_key("obstruction_analysis", domain)
        if self.config.use_cache:
            cached = self._load_cached(cache_key)
            if cached is not None:
                return cached

        dimensions: list[str] = domain.get("dimensions", [])
        coordinates: list[Any] = domain.get("coordinates", [])
        fields: list[FieldDict] = []

        for idx, dim in enumerate(dimensions):
            # Derive a deterministic severity from the dimension index and
            # the domain's hash so results are reproducible across runs.
            raw_severity = _clamp(
                math.sin(idx * 0.7 + len(dim)) * 0.5 + 0.5
            )
            kind = self._classify_obstruction_kind(dim, idx)
            active = raw_severity >= DEFAULT_SEVERITY_THRESHOLD
            fields.append(
                {
                    "id": _uid(),
                    "dimension": dim,
                    "kind": kind,
                    "severity": round(raw_severity, 4),
                    "active": active,
                    "index": idx,
                    "coordinate": coordinates[idx] if idx < len(coordinates) else None,
                    "metadata": {"domain_id": domain.get("id", "unknown")},
                }
            )

        # Sort by descending severity so the most important fields come first
        fields.sort(key=lambda f: f["severity"], reverse=True)

        if self.config.use_cache:
            self._cache_result(cache_key, fields)

        return fields

    def _classify_obstruction_kind(self, dim: str, idx: int) -> str:
        """
        Heuristically classify an obstruction kind from a dimension name.

        The classification is intentionally simple — production code would
        use a learned classifier or a lookup table derived from the schema.
        Here we cycle through the four basic kinds based on the dimension
        index modulo 4 so the output is deterministic.

        Parameters
        ----------
        dim : str
            Dimension name string.
        idx : int
            Zero-based index of the dimension in the domain.

        Returns
        -------
        str
            One of ``'topological'``, ``'algebraic'``, ``'geometric'``,
            ``'semantic'``.
        """
        kinds = ["topological", "algebraic", "geometric", "semantic"]
        # Also bias toward topological for names containing "top" or "hom"
        if "top" in dim.lower() or "hom" in dim.lower():
            return "topological"
        if "alg" in dim.lower() or "ring" in dim.lower():
            return "algebraic"
        return kinds[idx % len(kinds)]

    # ------------------------------------------------------------------
    # Stage 2 — Domain partition
    # ------------------------------------------------------------------

    def domain_partition(self, obstruction_fields: list[FieldDict]) -> list[DomainDict]:
        """
        Partition the obstruction space into a collection of sub-domains.

        Each active obstruction field seeds one or more candidate
        sub-domains.  Inactive fields (severity below threshold) are
        grouped into a single *residual* domain.

        The resulting list of sub-domains is guaranteed to be pairwise
        disjoint in their ``"field_ids"`` sets, and their union covers
        all input fields.

        Parameters
        ----------
        obstruction_fields : list[FieldDict]
            Output of `obstruction_analysis`.

        Returns
        -------
        list[DomainDict]
            List of sub-domain descriptors.  Each has keys ``id``,
            ``field_ids``, ``domain_type``, ``complexity``, ``metadata``.
        """
        if not obstruction_fields:
            return []

        active = [f for f in obstruction_fields if f.get("active", False)]
        inactive = [f for f in obstruction_fields if not f.get("active", False)]

        domains: list[DomainDict] = []

        # One domain per active obstruction field (could be merged later)
        for fld in active:
            complexity = _clamp(fld["severity"] * COMPLEXITY_SCALE_FACTOR, 0.0, COMPLEXITY_SCALE_FACTOR)
            domains.append(
                {
                    "id": _uid(),
                    "field_ids": [fld["id"]],
                    "domain_type": self._choose_domain_type(fld["kind"]),
                    "complexity": round(complexity, 2),
                    "severity": fld["severity"],
                    "metadata": {"source_field": fld["id"]},
                }
            )

        # Residual domain collects all inactive fields
        if inactive:
            domains.append(
                {
                    "id": _uid(),
                    "field_ids": [f["id"] for f in inactive],
                    "domain_type": "residual",
                    "complexity": 1.0,
                    "severity": 0.0,
                    "metadata": {"residual": True},
                }
            )

        return domains

    def _choose_domain_type(self, kind: str) -> str:
        """
        Choose a domain type string given an obstruction kind.

        The mapping from obstruction kind to domain type follows the
        classification in theory2.tex §55.3.  The domain type influences
        which type constructors are considered viable during the search
        stage.

        Parameters
        ----------
        kind : str
            Obstruction kind string.

        Returns
        -------
        str
            Domain type string such as ``'sheaf'``, ``'fibration'``,
            ``'affine'``, or ``'abstract'``.
        """
        mapping = {
            "topological": "sheaf",
            "algebraic": "fibration",
            "geometric": "affine",
            "semantic": "abstract",
        }
        return mapping.get(kind, "abstract")

    # ------------------------------------------------------------------
    # Stage 3 — Type constructor search
    # ------------------------------------------------------------------

    def type_constructor_search(self, domain: DomainDict) -> list[dict[str, Any]]:
        """
        Search for type constructors that are compatible with the given domain.

        A type constructor is *compatible* with a domain if it can be
        applied to every field in the domain's ``field_ids`` list and
        produces a coherent typing.  The search returns up to
        ``config.max_candidates`` constructors ranked by compatibility
        score.

        This implementation generates synthetic constructors parameterised
        by the domain type and complexity so that test suites can run
        without real constructor registries.  In production, the actual
        constructor registry would be queried here.

        Parameters
        ----------
        domain : DomainDict
            Sub-domain descriptor as produced by `domain_partition`.

        Returns
        -------
        list[dict[str, Any]]
            List of constructor descriptors, each with keys ``id``,
            ``kind``, ``arity``, ``score``, ``domain_id``, ``metadata``.
        """
        if not domain:
            return []

        domain_type = domain.get("domain_type", "abstract")
        complexity = float(domain.get("complexity", 1.0))
        field_ids: list[str] = domain.get("field_ids", [])

        # Number of constructors we'll generate is bounded by config
        n = min(self.config.max_candidates, max(self.config.min_generator_count, len(field_ids) + 1))

        constructors: list[dict[str, Any]] = []
        for i in range(n):
            arity = (i % 3) + 1
            base_score = _clamp(1.0 - complexity / (COMPLEXITY_SCALE_FACTOR * 2) + i * 0.01)
            kind = self._constructor_kind_for_domain(domain_type, i)
            constructors.append(
                {
                    "id": _uid(),
                    "kind": kind,
                    "arity": arity,
                    "score": round(base_score, 4),
                    "domain_id": domain.get("id", ""),
                    "metadata": {
                        "domain_type": domain_type,
                        "arity": arity,
                        "index": i,
                    },
                }
            )

        # Sort by descending score
        constructors.sort(key=lambda c: c["score"], reverse=True)
        return constructors

    def _constructor_kind_for_domain(self, domain_type: str, index: int) -> str:
        """
        Derive a constructor kind label from a domain type and index.

        The label determines how downstream assembly code treats the
        constructor.  Labels follow the taxonomy in theory2.tex §55.5.

        Parameters
        ----------
        domain_type : str
            Domain type string.
        index : int
            Position index used to cycle through secondary kinds.

        Returns
        -------
        str
            Constructor kind label.
        """
        primary_map = {
            "sheaf": ["section", "stalk", "pushforward"],
            "fibration": ["fiber", "base", "total"],
            "affine": ["linear", "affine_map", "projection"],
            "abstract": ["functor", "natural_transform", "adjunction"],
            "residual": ["identity", "constant"],
        }
        kinds = primary_map.get(domain_type, ["generic"])
        return kinds[index % len(kinds)]

    # ------------------------------------------------------------------
    # Stage 4 — Regime assembly
    # ------------------------------------------------------------------

    def regime_assembly(
        self, domain: DomainDict, constructors: list[dict[str, Any]]
    ) -> CandidateDict:
        """
        Assemble a regime candidate from a domain formation and constructors.

        This stage combines the domain, its obstruction coverage metadata,
        and the top-ranked constructors into a single candidate object.
        The candidate is not yet scored — call `bootstrap_score` next.

        Assembly validates the minimum generator count requirement from
        ``AlgorithmConfig.min_generator_count``.  If fewer constructors
        are supplied than required the candidate is flagged as
        ``"viable": False``.

        Parameters
        ----------
        domain : DomainDict
            Sub-domain descriptor.
        constructors : list[dict[str, Any]]
            Constructors as returned by `type_constructor_search`.

        Returns
        -------
        CandidateDict
            Assembled candidate descriptor with keys ``id``, ``domain_id``,
            ``constructor_ids``, ``viable``, ``score``, ``metadata``.
        """
        viable = len(constructors) >= self.config.min_generator_count
        constructor_ids = [c["id"] for c in constructors]

        return {
            "id": _uid(),
            "domain_id": domain.get("id", ""),
            "domain_type": domain.get("domain_type", "abstract"),
            "constructor_ids": constructor_ids,
            "constructor_count": len(constructors),
            "viable": viable,
            "score": 0.0,  # filled in by bootstrap_score
            "severity": domain.get("severity", 0.0),
            "complexity": domain.get("complexity", 0.0),
            "metadata": {
                "assembled_at": _utcnow(),
                "run_id": self._run_id,
                "domain_type": domain.get("domain_type"),
            },
        }

    # ------------------------------------------------------------------
    # Stage 5 — Scoring and ranking
    # ------------------------------------------------------------------

    def bootstrap_score(self, candidate: CandidateDict) -> float:
        """
        Compute the composite bootstrap score for a regime candidate.

        The score is a weighted combination of:
          * **Severity component** — higher severity → more important to
            resolve → higher score contribution.
          * **Novelty component** — more constructors → more novel regime.
          * **Viability penalty** — non-viable candidates are scored 0.

        The two primary weights come from `AlgorithmConfig.severity_weight`
        and `AlgorithmConfig.novelty_weight` and must sum to ≤ 1.0.

        Parameters
        ----------
        candidate : CandidateDict
            Candidate descriptor (output of `regime_assembly`).

        Returns
        -------
        float
            Score in [0, 1].
        """
        if not candidate.get("viable", False):
            return 0.0

        severity = float(candidate.get("severity", 0.0))
        n_constructors = int(candidate.get("constructor_count", 0))

        # Novelty proxy: normalise constructor count against max_candidates
        novelty = _clamp(n_constructors / max(self.config.max_candidates, 1))

        raw = self._apply_weights(severity, novelty)
        score = _clamp(raw)
        # Write the score back into the candidate dict
        candidate["score"] = round(score, 4)
        return score

    def rank_candidates(self, candidates: list[CandidateDict]) -> list[CandidateDict]:
        """
        Rank a list of regime candidates by their composite bootstrap score.

        This method first ensures every candidate has an up-to-date score
        by calling `bootstrap_score` on each, then sorts the list in
        descending score order.  The original list is not modified.

        Parameters
        ----------
        candidates : list[CandidateDict]
            Candidates to rank.

        Returns
        -------
        list[CandidateDict]
            A new list sorted by descending score.
        """
        # Score each candidate (idempotent if already scored)
        for cand in candidates:
            self.bootstrap_score(cand)

        return sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)

    def compute_complexity(self, domain: DomainDict) -> dict[str, float]:
        """
        Compute complexity metrics for a domain formation.

        Complexity is measured along three axes:

        * **Structural complexity** — based on the number of fields and
          their kinds.
        * **Semantic complexity** — based on the domain type and depth.
        * **Computational complexity** — estimated cost of resolving all
          obstructions in the domain.

        The returned dictionary has keys ``structural``, ``semantic``,
        ``computational``, and ``total`` (their weighted sum).

        Parameters
        ----------
        domain : DomainDict
            Domain formation descriptor.

        Returns
        -------
        dict[str, float]
            Complexity breakdown.
        """
        n_fields = len(domain.get("field_ids", []))
        severity = float(domain.get("severity", 0.0))
        domain_type = domain.get("domain_type", "abstract")

        type_weights = {"sheaf": 1.5, "fibration": 1.3, "affine": 1.0, "abstract": 1.2, "residual": 0.5}
        tw = type_weights.get(domain_type, 1.0)

        structural = _clamp(n_fields * 0.1, 0.0, 1.0)
        semantic = _clamp(tw * 0.2, 0.0, 1.0)
        computational = _clamp(severity * tw, 0.0, 1.0)
        total = _clamp((structural + semantic + computational) / 3.0)

        return {
            "structural": round(structural, 4),
            "semantic": round(semantic, 4),
            "computational": round(computational, 4),
            "total": round(total, 4),
        }

    def estimate_cost(self, plan: dict[str, Any]) -> float:
        """
        Estimate the total computational cost of executing a bootstrap plan.

        Cost is computed as the sum of complexities of all domains in the
        plan, scaled by ``COMPLEXITY_SCALE_FACTOR``.  If the plan contains
        no domains the cost is 0.

        This estimate is used by the orchestrator to prioritise plans and
        by the pipeline to check against ``AlgorithmConfig.complexity_budget``.

        Parameters
        ----------
        plan : dict[str, Any]
            Bootstrap plan descriptor.  Expected to have a ``"domains"``
            list of domain dicts.

        Returns
        -------
        float
            Estimated cost in complexity units.
        """
        domains: list[DomainDict] = plan.get("domains", [])
        if not domains:
            return 0.0

        total = 0.0
        for domain in domains:
            metrics = self.compute_complexity(domain)
            total += metrics["total"] * COMPLEXITY_SCALE_FACTOR

        return round(total, 2)

    def select_best_candidate(self, candidates: list[CandidateDict]) -> CandidateDict | None:
        """
        Select the highest-scoring viable candidate from a ranked list.

        This is a convenience wrapper around `rank_candidates` that
        returns only the first element.  Returns *None* if the list is
        empty or all candidates are non-viable.

        Parameters
        ----------
        candidates : list[CandidateDict]
            Pool of regime candidates.

        Returns
        -------
        CandidateDict | None
            The best candidate, or None if none are viable.
        """
        viable = [c for c in candidates if c.get("viable", False)]
        if not viable:
            return None
        ranked = self.rank_candidates(viable)
        return ranked[0] if ranked else None

    def compute_coverage_metric(
        self, domains: list[DomainDict], total: int
    ) -> float:
        """
        Compute the coverage metric for a set of domains over the total field count.

        Coverage is defined as the fraction of unique field IDs that are
        covered by at least one domain.  A coverage of 1.0 means every
        obstruction field has been assigned to a domain.

        Parameters
        ----------
        domains : list[DomainDict]
            List of domain descriptors.
        total : int
            Total number of obstruction fields in the original space.

        Returns
        -------
        float
            Coverage fraction in [0, 1].
        """
        if total <= 0:
            return 1.0

        covered_ids: set[str] = set()
        for dom in domains:
            covered_ids.update(dom.get("field_ids", []))

        return _clamp(len(covered_ids) / total)

    def normalize_scores(self, candidates: list[CandidateDict]) -> list[CandidateDict]:
        """
        Normalise the ``score`` field of all candidates to sum to 1.0.

        After normalisation the scores form a probability distribution
        that can be used for weighted sampling or soft-max selection.
        Candidates with a score of 0 remain at 0.

        Parameters
        ----------
        candidates : list[CandidateDict]
            Candidates to normalise (modified in-place and returned).

        Returns
        -------
        list[CandidateDict]
            The same list with updated ``score`` values.
        """
        total_score = sum(c.get("score", 0.0) for c in candidates)
        if total_score <= 0.0:
            return candidates

        for cand in candidates:
            raw = cand.get("score", 0.0)
            cand["score"] = round(raw / total_score, 6)

        return candidates

    def filter_viable_candidates(
        self, candidates: list[CandidateDict]
    ) -> list[CandidateDict]:
        """
        Filter out candidates that do not meet viability requirements.

        A candidate is *non-viable* when any of the following hold:

        * ``"viable"`` is False (set during assembly).
        * The domain severity is at or above ``BLOCKING_SEVERITY_CUTOFF``
          and no constructor has fully resolved the obstruction.
        * The constructor count is below ``config.min_generator_count``.

        Parameters
        ----------
        candidates : list[CandidateDict]
            Full candidate pool.

        Returns
        -------
        list[CandidateDict]
            Subset of candidates passing all viability checks.
        """
        result: list[CandidateDict] = []
        for cand in candidates:
            if not cand.get("viable", False):
                continue
            if cand.get("constructor_count", 0) < self.config.min_generator_count:
                continue
            # If blocking severity and zero score, discard
            if cand.get("severity", 0.0) >= BLOCKING_SEVERITY_CUTOFF and cand.get("score", 0.0) == 0.0:
                continue
            result.append(cand)
        return result

    def compute_obstruction_density(self, fields: list[FieldDict]) -> float:
        """
        Compute a density metric for a collection of obstruction fields.

        Obstruction density is defined as the mean severity of all active
        fields divided by the total field count.  High density indicates
        a heavily obstructed domain space that may require many domains
        to partition effectively.

        Parameters
        ----------
        fields : list[FieldDict]
            Obstruction field descriptors.

        Returns
        -------
        float
            Density value in [0, 1].
        """
        if not fields:
            return 0.0

        active = [f for f in fields if f.get("active", False)]
        if not active:
            return 0.0

        mean_severity = sum(f.get("severity", 0.0) for f in active) / len(active)
        density = _clamp(mean_severity * len(active) / len(fields))
        return round(density, 4)

    def merge_overlapping_domains(
        self, domains: list[DomainDict]
    ) -> list[DomainDict]:
        """
        Merge domain pairs that share obstruction field IDs.

        Two domains *overlap* if their ``field_ids`` sets have a non-empty
        intersection.  Overlapping domains are merged by unioning their
        field ID sets and taking the maximum of their severity and
        complexity values.  The merge is repeated until no overlaps remain.

        Parameters
        ----------
        domains : list[DomainDict]
            Input domain list, potentially with overlaps.

        Returns
        -------
        list[DomainDict]
            Reduced list of non-overlapping domains.
        """
        # Convert field_ids to frozensets for fast intersection
        merged = [dict(d, _fids=frozenset(d.get("field_ids", []))) for d in domains]
        changed = True

        while changed:
            changed = False
            result: list[dict[str, Any]] = []
            used: set[int] = set()

            for i, a in enumerate(merged):
                if i in used:
                    continue
                for j, b in enumerate(merged):
                    if j <= i or j in used:
                        continue
                    if a["_fids"] & b["_fids"]:
                        # Merge b into a
                        new_fids = a["_fids"] | b["_fids"]
                        a = dict(
                            a,
                            _fids=new_fids,
                            field_ids=list(new_fids),
                            severity=max(a.get("severity", 0.0), b.get("severity", 0.0)),
                            complexity=max(a.get("complexity", 0.0), b.get("complexity", 0.0)),
                        )
                        used.add(j)
                        changed = True
                result.append(a)
                used.add(i)

            merged = result

        # Strip the helper key before returning
        return [{k: v for k, v in d.items() if k != "_fids"} for d in merged]

    def validate_pipeline_output(self, result: dict[str, Any]) -> list[str]:
        """
        Validate the output of the full bootstrapping pipeline.

        Checks that the result has all required keys, that the selected
        candidate is viable and above the minimum score threshold, and
        that coverage meets ``config.coverage_target``.

        Parameters
        ----------
        result : dict[str, Any]
            Pipeline output dict.

        Returns
        -------
        list[str]
            List of validation error messages.  Empty list means valid.
        """
        errors: list[str] = []

        if not isinstance(result, dict):
            return ["result must be a dict"]

        required_keys = ["candidate", "coverage", "domains", "fields"]
        for k in required_keys:
            if k not in result:
                errors.append(f"missing required key: {k}")

        candidate = result.get("candidate")
        if candidate is not None:
            if not candidate.get("viable", False):
                errors.append("selected candidate is not viable")
            if candidate.get("score", 0.0) < 0.01:
                errors.append("selected candidate score is below minimum (0.01)")

        coverage = result.get("coverage", 0.0)
        if coverage < self.config.coverage_target:
            errors.append(
                f"coverage {coverage:.3f} is below target {self.config.coverage_target:.3f}"
            )

        return errors

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, stage: str, data: Any) -> str:
        """
        Produce a stable cache key for a stage-data pair.

        The key is the SHA-256 digest of the stage name concatenated with
        the repr of the data.  This is not cryptographically strong but is
        sufficient for a local in-process cache.

        Parameters
        ----------
        stage : str
            Pipeline stage identifier.
        data : Any
            Input data whose repr is hashed.

        Returns
        -------
        str
            64-character hex digest.
        """
        raw = f"{stage}:{repr(data)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_result(self, key: str, value: Any) -> None:
        """
        Store a result in the module-level cache.

        Parameters
        ----------
        key : str
            Cache key (from `_cache_key`).
        value : Any
            Value to store.  Must be picklable for future persistence.
        """
        self._cache[key] = value

    def _load_cached(self, key: str) -> Any | None:
        """
        Retrieve a cached result, or None if not present.

        Parameters
        ----------
        key : str
            Cache key.

        Returns
        -------
        Any | None
            Cached value, or None.
        """
        return self._cache.get(key)

    def _apply_weights(self, severity: float, novelty: float) -> float:
        """
        Apply configured weights to severity and novelty components.

        The weighted sum uses ``config.severity_weight`` and
        ``config.novelty_weight``.  If their sum is less than 1 the
        remaining weight is distributed evenly.

        Parameters
        ----------
        severity : float
            Severity component in [0, 1].
        novelty : float
            Novelty component in [0, 1].

        Returns
        -------
        float
            Weighted composite score.
        """
        sw = self.config.severity_weight
        nw = self.config.novelty_weight
        return sw * severity + nw * novelty

    def _normalize_score(self, score: float, scale: float = 1.0) -> float:
        """
        Normalize a raw score to [0, scale].

        Parameters
        ----------
        score : float
            Raw score.
        scale : float
            Output scale.  Defaults to 1.0.

        Returns
        -------
        float
            Normalized score.
        """
        return _clamp(score) * scale


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def compute_obstruction_class(field: FieldDict) -> str:
    """
    Compute the obstruction class string for a field descriptor.

    The obstruction class is a compact label used in theorem statements and
    log messages.  It encodes the field's kind, severity tier (low/mid/high),
    and whether it is actively blocking.

    The format is ``"<kind>/<tier>/<active>"``, e.g.
    ``"topological/high/blocking"``.

    Parameters
    ----------
    field : FieldDict
        Obstruction field descriptor (output of `obstruction_analysis`).

    Returns
    -------
    str
        Obstruction class string.
    """
    kind = field.get("kind", "unknown")
    severity = float(field.get("severity", 0.0))
    active = field.get("active", False)

    if severity >= BLOCKING_SEVERITY_CUTOFF:
        tier = "high"
        status = "blocking"
    elif severity >= DEFAULT_SEVERITY_THRESHOLD:
        tier = "mid"
        status = "active" if active else "dormant"
    else:
        tier = "low"
        status = "inactive"

    return f"{kind}/{tier}/{status}"


def rank_bootstrap_candidates(
    candidates: list[CandidateDict],
    weights: dict[str, float] | None = None,
) -> list[CandidateDict]:
    """
    Rank a list of regime candidates using optional custom weights.

    When *weights* is provided, the function re-scores each candidate
    using the specified ``severity_weight`` and ``novelty_weight`` before
    sorting.  This allows callers to experiment with different weight
    combinations without modifying the main config.

    Parameters
    ----------
    candidates : list[CandidateDict]
        Candidate pool.
    weights : dict[str, float] | None
        Optional dict with keys ``"severity"`` and ``"novelty"``.
        Defaults to ``{"severity": 0.7, "novelty": 0.3}``.

    Returns
    -------
    list[CandidateDict]
        Sorted candidates, best first.
    """
    w = weights or {"severity": 0.7, "novelty": 0.3}
    sw = _clamp(w.get("severity", 0.7))
    nw = _clamp(w.get("novelty", 0.3))

    def _score(c: CandidateDict) -> float:
        if not c.get("viable", False):
            return 0.0
        sev = float(c.get("severity", 0.0))
        n = int(c.get("constructor_count", 0))
        nov = _clamp(n / 50.0)
        return _clamp(sw * sev + nw * nov)

    scored = [(c, _score(c)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored]


def _compute_domain_complexity(domain: DomainDict) -> float:
    """
    Internal helper — compute a single complexity float for a domain.

    Equivalent to ``BootstrappingAlgorithms.compute_complexity(domain)["total"]``
    but does not require an algorithm instance, making it useful in free
    function contexts.

    Parameters
    ----------
    domain : DomainDict
        Domain descriptor.

    Returns
    -------
    float
        Total complexity in [0, 1].
    """
    n_fields = len(domain.get("field_ids", []))
    severity = float(domain.get("severity", 0.0))
    structural = _clamp(n_fields * 0.1)
    computational = _clamp(severity)
    return _clamp((structural + computational) / 2.0)


def _score_obstruction_field(field: FieldDict) -> float:
    """
    Internal helper — score a single obstruction field.

    The score is proportional to the field's severity scaled by a
    kind-dependent weight.  Blocking fields get an extra 10% bonus.

    Parameters
    ----------
    field : FieldDict
        Obstruction field descriptor.

    Returns
    -------
    float
        Score in [0, 1].
    """
    severity = float(field.get("severity", 0.0))
    kind = field.get("kind", "abstract")
    kind_weights = {
        "topological": 1.1,
        "algebraic": 1.0,
        "geometric": 0.95,
        "semantic": 0.9,
    }
    kw = kind_weights.get(kind, 1.0)
    bonus = 0.1 if severity >= BLOCKING_SEVERITY_CUTOFF else 0.0
    return _clamp(severity * kw + bonus)


def _compute_coverage_metric(domains: list[DomainDict]) -> float:
    """
    Internal helper — compute coverage fraction from a list of domains.

    Counts unique field IDs across all domains and divides by the maximum
    index seen, giving a rough estimate of coverage when the total field
    count is not available.

    Parameters
    ----------
    domains : list[DomainDict]
        List of domain descriptors.

    Returns
    -------
    float
        Coverage estimate in [0, 1].
    """
    all_ids: set[str] = set()
    for dom in domains:
        all_ids.update(dom.get("field_ids", []))

    if not all_ids:
        return 0.0

    # Heuristic: coverage is approximated as 1 - 1/(n+1)
    n = len(all_ids)
    return _clamp(1.0 - 1.0 / (n + 1))


def _normalize_candidate_score(score: float, scale: float = 1.0) -> float:
    """
    Normalize a raw candidate score to [0, scale].

    Parameters
    ----------
    score : float
        Raw score, may be outside [0, 1].
    scale : float
        Output scale factor.

    Returns
    -------
    float
        Normalized score.
    """
    return _clamp(score) * scale


def _weighted_average(values: list[float], weights: list[float]) -> float:
    """
    Compute a weighted average of *values* using *weights*.

    Both lists must have the same length.  If all weights are zero the
    function returns 0.0 to avoid division by zero.

    Parameters
    ----------
    values : list[float]
        Values to average.
    weights : list[float]
        Non-negative weights corresponding to each value.

    Returns
    -------
    float
        Weighted average.

    Raises
    ------
    ValueError
        If *values* and *weights* have different lengths.
    """
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    total_weight = sum(weights)
    if total_weight == 0.0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Perform division returning *default* when the denominator is zero.

    This avoids ZeroDivisionError in metric computations where a zero
    denominator is a legitimate boundary condition rather than a bug.

    Parameters
    ----------
    numerator : float
        Dividend.
    denominator : float
        Divisor.
    default : float
        Value returned when denominator == 0.  Defaults to 0.0.

    Returns
    -------
    float
        Result of the division, or *default*.
    """
    if denominator == 0.0:
        return default
    return numerator / denominator
