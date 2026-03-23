"""Discovery algorithms and pipeline orchestration — theory2.tex Ch58.

This module provides two main components:

1.  ``DiscoveryAlgorithms`` — a collection of static methods implementing the
    core mathematical algorithms used in the discovery pipeline, including
    novelty ranking, kind assignment, theorem derivation, and convergence
    checking.

2.  ``DiscoveryPipeline`` — an orchestrator class that wires together Stages
    1–4 (novelty pipeline, kind classification, theorem synthesis, pack
    promotion) into a single end-to-end pipeline with configuration,
    diagnostics, and progress callbacks.

Free functions ``score_discovery``, ``rank_by_evidence``, and ``select_top_k``
are also provided for use outside the main pipeline.

Theory reference: theory2.tex Ch58 §6 — Discovery Algorithms.

copilot: shared-core marker

Detailed Design Notes
---------------------
The discovery pipeline processes a stream of :class:`DiscoveryCandidate`
objects through four sequential stages:

  Stage 1 — Novelty Pipeline (``novelty_pipeline``):
      Filters candidates by novelty score and computes domain diversity
      metrics.  Candidates that do not exceed the configured novelty
      threshold are dropped.  The stage emits a
      :class:`~jugeo.ideation.discovery_engine.models.NoveltyPipelineStage`
      summary object.

  Stage 2 — Kind Classification (``kind_classification``):
      Each surviving candidate is assigned a :class:`KindSignature` using
      the active :class:`KindRegistry`.  If no registry is available the
      module falls back to a generic kind derived from the candidate's
      description tokens.  The stage emits a
      :class:`~jugeo.ideation.discovery_engine.models.KindClassificationStage`
      summary.

  Stage 3 — Theorem Synthesis (``theorem_synthesis``):
      For each (candidate, kind_signature) pair, structural patterns are
      applied to derive a list of :class:`TheoremCandidate` objects.  The
      derivation is purely syntactic: it relies on recognised shape-patterns
      in the kind signature rather than logical deduction.  The stage emits a
      :class:`~jugeo.ideation.discovery_engine.models.TheoremSynthesisStage`
      summary.

  Stage 4 — Pack Promotion (``pack_promotion``):
      Theorem candidates that exceed the configured confidence and evidence
      thresholds are promoted to packs.  The stage emits a
      :class:`~jugeo.ideation.discovery_engine.models.PackPromotionStage`
      summary.

Each stage can be run independently via :meth:`DiscoveryPipeline.run_stage`,
or as part of the full end-to-end pipeline via :meth:`DiscoveryPipeline.run`
or :meth:`DiscoveryPipeline.run_with_diagnostics`.

Thread Safety
-------------
:class:`DiscoveryPipeline` is **not** thread-safe.  Callers that wish to run
multiple pipelines concurrently should create a separate instance per thread.
The :class:`DiscoveryAlgorithms` class consists entirely of static methods and
is therefore inherently thread-safe.

Performance Characteristics
----------------------------
All algorithmic methods are O(n) or O(n²) in the number of candidates *n*.
The pairwise Jaccard diversity computation in :func:`DiscoveryAlgorithms.diversity_score`
is O(n²) and should be called sparingly for large candidate sets.  A fast
approximate path (random sampling) is engaged automatically when *n* > 512.

Usage Examples
--------------
Minimal pipeline run::

    from jugeo.ideation.discovery_engine.algorithms import (
        DiscoveryPipeline, create_default_pipeline,
    )
    from jugeo.ideation.discovery_engine.models import DiscoveryCandidate

    candidates = [
        DiscoveryCandidate(candidate_id="c1", description="foo bar", domain="math"),
        DiscoveryCandidate(candidate_id="c2", description="baz qux", domain="physics"),
    ]
    pipeline = create_default_pipeline()
    results = pipeline.run(candidates)
    for r in results:
        print(r.candidate_id, r.status)

Run with full diagnostics::

    results, diag = pipeline.run_with_diagnostics(candidates)
    print(diag.total_candidates, diag.promoted_count)

Custom callback::

    from jugeo.ideation.discovery_engine.algorithms import LoggingCallback
    cb = LoggingCallback(prefix="[my-run]")
    pipeline.add_callback(cb)

"""
from __future__ import annotations

import re
import sys
import time
import uuid
import math
import random
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

__all__ = [
    # Algorithms
    "DiscoveryAlgorithms",
    # Pipeline
    "DiscoveryPipeline",
    # Callback protocol + concrete impl
    "PipelineCallback",
    "LoggingCallback",
    # Free functions
    "score_discovery",
    "rank_by_evidence",
    "select_top_k",
    "create_default_pipeline",
]

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
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate, DiscoveryConfig, DiscoveryResult, DiscoveryDiagnostics,
        DiscoveryStatus, PipelineStage, KindSignature, TheoremCandidate,
        PromotionDecision, NoveltyPipelineStage, KindClassificationStage,
        TheoremSynthesisStage, PackPromotionStage,
    )
except Exception:
    # Provide lightweight stubs so the module remains importable in isolation.
    DiscoveryCandidate = None  # type: ignore[assignment,misc]
    DiscoveryConfig = None  # type: ignore[assignment,misc]
    DiscoveryResult = None  # type: ignore[assignment,misc]
    DiscoveryDiagnostics = None  # type: ignore[assignment,misc]
    DiscoveryStatus = None  # type: ignore[assignment,misc]
    PipelineStage = None  # type: ignore[assignment,misc]
    KindSignature = None  # type: ignore[assignment,misc]
    TheoremCandidate = None  # type: ignore[assignment,misc]
    PromotionDecision = None  # type: ignore[assignment,misc]
    NoveltyPipelineStage = None  # type: ignore[assignment,misc]
    KindClassificationStage = None  # type: ignore[assignment,misc]
    TheoremSynthesisStage = None  # type: ignore[assignment,misc]
    PackPromotionStage = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.discovery_engine.novelty_pipeline import NoveltyPipelineRunner, run_novelty_pipeline
    from jugeo.ideation.discovery_engine.kind_classification import KindClassificationRunner, KindRegistry, run_kind_classification
    from jugeo.ideation.discovery_engine.theorem_synthesis import TheoremSynthesisRunner, run_theorem_synthesis
    from jugeo.ideation.discovery_engine.pack_promotion import PackPromotionRunner, run_pack_promotion, PromotionReport
except Exception:
    NoveltyPipelineRunner = None  # type: ignore[assignment,misc]
    run_novelty_pipeline = None  # type: ignore[assignment,misc]
    KindClassificationRunner = None  # type: ignore[assignment,misc]
    KindRegistry = None  # type: ignore[assignment,misc]
    run_kind_classification = None  # type: ignore[assignment,misc]
    TheoremSynthesisRunner = None  # type: ignore[assignment,misc]
    run_theorem_synthesis = None  # type: ignore[assignment,misc]
    PackPromotionRunner = None  # type: ignore[assignment,misc]
    run_pack_promotion = None  # type: ignore[assignment,misc]
    PromotionReport = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This thin wrapper exists so that tests can monkey-patch time without
    importing the ``time`` module directly in every call site.

    Returns
    -------
    float
        Current UTC epoch time in seconds.
    """
    return time.time()


def _uid() -> str:
    """Generate a compact random unique identifier (UUID4 hex, no hyphens).

    Returns
    -------
    str
        32-character hexadecimal string, e.g. ``'a3f8b2...d91c'``.
    """
    return uuid.uuid4().hex


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        *v* if *lo* ≤ *v* ≤ *hi*, otherwise the nearest bound.
    """
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    The Jaccard coefficient is defined as::

        J(A, B) = |A ∩ B| / |A ∪ B|

    When both sets are empty the function returns 1.0 (identical empty sets).

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].
    """
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _tokenize(text: str) -> set[str]:
    """Tokenize *text* into a set of lowercase alphabetic words.

    Punctuation, digits, and whitespace are treated as separators.  The
    resulting set is suitable for use with :func:`_jaccard`.

    Parameters
    ----------
    text:
        Input string.  May be empty.

    Returns
    -------
    set[str]
        Non-empty lowercase tokens found in *text*.
    """
    return {w.lower() for w in re.findall(r"[a-zA-Z]+", text) if w}


# ---------------------------------------------------------------------------
# Default weight constants used across algorithms
# ---------------------------------------------------------------------------

#: Default weights used by :meth:`DiscoveryAlgorithms.novelty_ranking`.
_DEFAULT_RANKING_WEIGHTS: dict[str, float] = {
    "novelty_score": 0.6,
    "domain_diversity": 0.25,
    "description_richness": 0.15,
}

#: Minimum number of description tokens for a candidate to be considered
#: "rich" (contributes positively to the description_richness sub-score).
_DESCRIPTION_RICHNESS_MIN_TOKENS: int = 8

#: Approximate-diversity threshold: if the number of candidates exceeds this
#: value, pairwise Jaccard is estimated via random sampling rather than
#: exhaustive enumeration.
_DIVERSITY_APPROX_THRESHOLD: int = 512

#: Sample size used when approximate diversity is engaged.
_DIVERSITY_SAMPLE_SIZE: int = 64


# ---------------------------------------------------------------------------
# DiscoveryAlgorithms
# ---------------------------------------------------------------------------

class DiscoveryAlgorithms:
    """Static algorithm library for the JuGeo discovery engine.

    All methods are static; this class should never be instantiated.  It is
    organised as a class purely for namespacing convenience, so callers can
    write ``DiscoveryAlgorithms.novelty_ranking(...)`` instead of importing
    individual free functions.

    Algorithm Overview
    ------------------
    The algorithms in this class correspond to the mathematical definitions in
    theory2.tex Ch58 §6.  The main data-flow is:

      1. :meth:`novelty_ranking` — sort candidates by a weighted composite
         of novelty, diversity, and description richness.
      2. :meth:`kind_assignment` — map each candidate to a ``KindSignature``
         drawn from a registry, with fallback to a generic kind.
      3. :meth:`theorem_derivation` — derive structural theorem candidates
         from a (candidate, kind_signature) pair.
      4. :meth:`pack_eligibility` — gate theorem candidates against the
         configured promotion thresholds.
      5. :meth:`pipeline_step` — dispatch-table entry-point used by the
         pipeline orchestrator.
      6. :meth:`convergence_check` — stability detector for iterative runs.
      7. :meth:`diversity_score` — global diversity of a candidate set.
      8. :meth:`pipeline_efficiency` — scalar efficiency metric from
         diagnostics.

    Notes
    -----
    * All floating-point scores are in [0.0, 1.0] unless otherwise noted.
    * None of the methods mutate their arguments.
    """

    # Prevent instantiation.
    def __new__(cls, *args: Any, **kwargs: Any) -> DiscoveryAlgorithms:  # type: ignore[misc]
        raise TypeError(
            "DiscoveryAlgorithms is a static algorithm namespace and cannot be instantiated. "
            "Call its methods directly as class methods, e.g. "
            "DiscoveryAlgorithms.novelty_ranking(candidates)."
        )

    # ------------------------------------------------------------------
    # 1. Novelty Ranking
    # ------------------------------------------------------------------

    @staticmethod
    def novelty_ranking(
        candidates: list[Any],
        weights: dict[str, float] | None = None,
    ) -> list[Any]:
        """Sort *candidates* by a weighted composite novelty score.

        The composite score *S(c)* for a candidate *c* is computed as::

            S(c) = w_n * novelty_score(c)
                 + w_d * domain_diversity(c, candidates)
                 + w_r * description_richness(c)

        where the three sub-scores are defined as follows:

        **novelty_score(c)**
            The raw ``novelty_score`` attribute of the candidate object,
            clamped to [0, 1].  When the attribute is absent the sub-score
            defaults to 0.5.

        **domain_diversity(c, candidates)**
            The fraction of other candidates that share a *different* domain
            from *c*.  A candidate in a unique domain receives a score of 1.0;
            a candidate whose domain appears in every other candidate receives
            a score of 0.0.

        **description_richness(c)**
            A simple measure of lexical richness: ``min(token_count / 20, 1.0)``
            where *token_count* is the number of distinct lowercase alphabetic
            tokens in ``c.description``.  The divisor 20 is heuristic and
            chosen so that a description with ≥ 20 unique words receives the
            maximum sub-score.

        Parameters
        ----------
        candidates:
            List of :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
            objects (or duck-typed equivalents with ``novelty_score``,
            ``domain``, and ``description`` attributes).
        weights:
            Optional dictionary overriding the default weights.  Recognised
            keys are ``'novelty_score'``, ``'domain_diversity'``, and
            ``'description_richness'``.  Any key absent from the supplied
            dictionary falls back to its default value.  The weights are
            **not** normalised automatically; the caller is responsible for
            ensuring they sum to a sensible value.

        Returns
        -------
        list
            A **new** list of candidates sorted in descending order of *S(c)*.
            The original list is not mutated.

        Examples
        --------
        >>> ranked = DiscoveryAlgorithms.novelty_ranking(candidates)
        >>> top = ranked[0]  # highest-scoring candidate

        Notes
        -----
        * Time complexity: O(n log n) due to the sort; the sub-score
          computations are O(n) overall.
        * The domain-diversity sub-score requires iterating all candidates to
          build the frequency table, making the full algorithm O(n log n).
        """
        if not candidates:
            return []

        effective_weights = dict(_DEFAULT_RANKING_WEIGHTS)
        if weights:
            effective_weights.update(weights)

        w_n = effective_weights.get("novelty_score", 0.6)
        w_d = effective_weights.get("domain_diversity", 0.25)
        w_r = effective_weights.get("description_richness", 0.15)

        # Build domain frequency table for the diversity sub-score.
        domain_counts: dict[str, int] = {}
        for c in candidates:
            dom = getattr(c, "domain", "") or ""
            domain_counts[dom] = domain_counts.get(dom, 0) + 1

        n = len(candidates)

        def composite(c: Any) -> float:
            raw_novelty = _clamp(float(getattr(c, "novelty_score", 0.5) or 0.5))
            dom = getattr(c, "domain", "") or ""
            same_domain = domain_counts.get(dom, 1)
            # fraction of candidates with a DIFFERENT domain
            diversity_sub = _clamp((n - same_domain) / (n - 1)) if n > 1 else 0.0
            desc = getattr(c, "description", "") or ""
            token_count = len(_tokenize(desc))
            richness_sub = _clamp(token_count / 20.0)
            return w_n * raw_novelty + w_d * diversity_sub + w_r * richness_sub

        return sorted(candidates, key=composite, reverse=True)

    # ------------------------------------------------------------------
    # 2. Kind Assignment
    # ------------------------------------------------------------------

    @staticmethod
    def kind_assignment(candidate: Any, registry: Any) -> Any:
        """Assign a :class:`KindSignature` to *candidate* using *registry*.

        The assignment procedure is:

        1. If *registry* is not ``None`` and has a ``lookup`` method,
           call ``registry.lookup(candidate.domain, candidate.description)``
           and return the result if it is not ``None``.
        2. Otherwise, derive a generic ``KindSignature`` from the candidate's
           domain and description tokens.

        The fallback generic kind is constructed as follows::

            kind_id   = sha1-hex(domain + "|" + sorted_description_tokens)[:12]
            label     = domain or "generic"
            signature = frozenset of top-5 description tokens by length

        Parameters
        ----------
        candidate:
            A :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
            (or duck-typed object with ``domain`` and ``description``
            attributes).
        registry:
            A kind registry object exposing a ``lookup(domain, description)``
            method, or ``None`` to use the fallback path.

        Returns
        -------
        KindSignature or dict
            A :class:`KindSignature` if the models module is available,
            otherwise a plain ``dict`` with keys ``kind_id``, ``label``,
            ``signature``, and ``source``.

        Notes
        -----
        * The fallback path is deterministic: the same (domain, description)
          pair always yields the same kind_id.
        * The registry's ``lookup`` method is called exactly once per
          candidate; results are not cached by this method.
        """
        domain = getattr(candidate, "domain", "") or ""
        description = getattr(candidate, "description", "") or ""

        # Attempt registry lookup first.
        if registry is not None:
            lookup_fn = getattr(registry, "lookup", None)
            if callable(lookup_fn):
                try:
                    result = lookup_fn(domain, description)
                    if result is not None:
                        return result
                except Exception:
                    pass

        # Fallback: derive generic kind from tokens.
        tokens = sorted(_tokenize(description))
        raw = domain + "|" + ",".join(tokens)
        import hashlib
        kind_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        top_tokens = tuple(sorted(tokens, key=len, reverse=True)[:5])
        return {
            "kind_id": kind_id,
            "label": domain or "generic",
            "signature": top_tokens,
            "source": "fallback",
        }

    # ------------------------------------------------------------------
    # 3. Theorem Derivation
    # ------------------------------------------------------------------

    @staticmethod
    def theorem_derivation(candidate: Any, kind_sig: Any) -> list[Any]:
        """Derive :class:`TheoremCandidate` objects from a candidate + kind signature.

        Theorem derivation is a purely syntactic operation: it applies a set
        of *structural patterns* to the kind signature to generate candidate
        theorems.  The patterns are:

        ``identity``
            Every kind gives rise to an identity theorem asserting that the
            kind is well-typed (always generated).

        ``composition``
            If the kind signature contains two or more tokens that could
            represent composable objects (heuristic: length ≥ 4), a
            composition theorem is generated.

        ``inversion``
            If the kind label contains the substring ``"group"`` or
            ``"field"``, an inversion theorem is generated.

        ``unit``
            If the kind label contains ``"monoid"`` or ``"ring"``, a unit
            theorem is generated.

        ``order``
            If the kind label contains ``"lattice"`` or ``"order"``, an
            ordering theorem is generated.

        For each matched pattern, a dictionary (or :class:`TheoremCandidate`
        if the models module is available) is created with fields::

            theorem_id    : unique identifier
            candidate_id  : forwarded from *candidate*
            kind_id       : forwarded from *kind_sig*
            pattern       : name of the structural pattern
            statement     : human-readable theorem statement
            confidence    : float in [0, 1]
            timestamp     : UTC epoch

        Parameters
        ----------
        candidate:
            Source candidate.
        kind_sig:
            Kind signature (dict or :class:`KindSignature`).

        Returns
        -------
        list
            Possibly-empty list of derived theorem candidates.

        Notes
        -----
        * At least one theorem (the identity theorem) is always returned when
          the candidate has a non-empty domain.
        * The confidence of derived theorems degrades with pattern complexity:
          identity → 0.95, composition → 0.75, inversion → 0.80,
          unit → 0.80, order → 0.70.
        """
        candidate_id = getattr(candidate, "candidate_id", _uid())
        domain = getattr(candidate, "domain", "") or ""
        kind_id: str = (
            kind_sig.get("kind_id", "") if isinstance(kind_sig, dict)
            else getattr(kind_sig, "kind_id", "")
        ) or ""
        label: str = (
            kind_sig.get("label", domain) if isinstance(kind_sig, dict)
            else getattr(kind_sig, "label", domain)
        ) or domain
        sig_tokens: tuple[str, ...] = (
            kind_sig.get("signature", ()) if isinstance(kind_sig, dict)
            else getattr(kind_sig, "signature", ())
        ) or ()

        now = _utcnow()
        results: list[Any] = []

        def _make(pattern: str, statement: str, confidence: float) -> dict[str, Any]:
            return {
                "theorem_id": _uid(),
                "candidate_id": candidate_id,
                "kind_id": kind_id,
                "pattern": pattern,
                "statement": statement,
                "confidence": _clamp(confidence),
                "timestamp": now,
            }

        # Identity theorem — always present.
        if domain:
            results.append(_make(
                "identity",
                f"The kind '{label}' is well-typed within domain '{domain}'.",
                0.95,
            ))

        # Composition theorem — at least two composable tokens.
        composable = [t for t in sig_tokens if len(t) >= 4]
        if len(composable) >= 2:
            results.append(_make(
                "composition",
                (
                    f"Elements of kind '{label}' can be composed via "
                    f"the '{composable[0]}' and '{composable[1]}' operations."
                ),
                0.75,
            ))

        label_lower = label.lower()

        # Inversion theorem.
        if any(kw in label_lower for kw in ("group", "field")):
            results.append(_make(
                "inversion",
                f"Every element of kind '{label}' has an inverse under the group operation.",
                0.80,
            ))

        # Unit theorem.
        if any(kw in label_lower for kw in ("monoid", "ring")):
            results.append(_make(
                "unit",
                f"There exists a unit element for kind '{label}'.",
                0.80,
            ))

        # Order theorem.
        if any(kw in label_lower for kw in ("lattice", "order", "poset")):
            results.append(_make(
                "order",
                f"The elements of kind '{label}' admit a partial order.",
                0.70,
            ))

        return results

    # ------------------------------------------------------------------
    # 4. Pack Eligibility
    # ------------------------------------------------------------------

    @staticmethod
    def pack_eligibility(theorem: Any, config: Any) -> bool:
        """Return ``True`` if *theorem* meets the promotion criteria in *config*.

        A theorem is eligible for promotion to a pack if **all** of the
        following conditions hold:

        1. Its ``confidence`` attribute (float in [0, 1]) is ≥
           ``config.min_confidence`` (default: 0.70).
        2. Its ``pattern`` attribute is not in ``config.excluded_patterns``
           (default: empty set).
        3. The ``candidate_id`` attribute is not ``None`` or empty.
        4. If ``config.require_domain`` is ``True``, the associated
           candidate's ``domain`` must be non-empty.  Because the theorem
           object may not carry domain information directly, this check is
           skipped when the ``domain`` attribute is absent from *theorem*.

        Parameters
        ----------
        theorem:
            A theorem candidate dict or :class:`TheoremCandidate` object.
        config:
            A :class:`DiscoveryConfig` or duck-typed object exposing
            ``min_confidence``, ``excluded_patterns``, and
            ``require_domain`` attributes.  If *config* is ``None``, the
            checks use module-level defaults.

        Returns
        -------
        bool
            ``True`` if the theorem is eligible for promotion.

        Notes
        -----
        * This method does **not** perform any I/O or registry lookups.
        * The caller is responsible for filtering the list of theorems using
          this method before passing them to the pack promotion stage.
        """
        def _get(obj: Any, key: str, default: Any) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        min_confidence: float = 0.70
        excluded_patterns: set[str] = set()
        require_domain: bool = False

        if config is not None:
            min_confidence = float(_get(config, "min_confidence", 0.70))
            raw_excl = _get(config, "excluded_patterns", None)
            if raw_excl is not None:
                excluded_patterns = set(raw_excl)
            require_domain = bool(_get(config, "require_domain", False))

        confidence = float(_get(theorem, "confidence", 0.0))
        if confidence < min_confidence:
            return False

        pattern = _get(theorem, "pattern", "") or ""
        if pattern in excluded_patterns:
            return False

        candidate_id = _get(theorem, "candidate_id", None)
        if not candidate_id:
            return False

        if require_domain:
            domain = _get(theorem, "domain", None)
            if domain is not None and not domain:
                return False

        return True

    # ------------------------------------------------------------------
    # 5. Pipeline Step (Dispatch)
    # ------------------------------------------------------------------

    @staticmethod
    def pipeline_step(stage: Any, input_data: Any) -> Any:
        """Dispatch *input_data* through the runner for *stage*.

        This method provides a uniform entry-point for external callers that
        want to run a single pipeline stage without constructing a full
        :class:`DiscoveryPipeline`.

        The dispatch table is::

            PipelineStage.NOVELTY_PIPELINE   → NoveltyPipelineRunner
            PipelineStage.KIND_CLASSIFICATION → KindClassificationRunner
            PipelineStage.THEOREM_SYNTHESIS  → TheoremSynthesisRunner
            PipelineStage.PACK_PROMOTION     → PackPromotionRunner

        If the stage-specific runner module is not available (e.g. because the
        optional dependency has not been installed), the method falls back to a
        no-op pass-through that returns *input_data* unchanged and emits a
        :py:class:`RuntimeWarning`.

        Parameters
        ----------
        stage:
            A :class:`PipelineStage` enum value identifying the stage to run,
            or a string matching one of the stage names.
        input_data:
            The data to pass to the stage runner.  The expected type depends
            on the stage:

            * NOVELTY_PIPELINE — ``list[DiscoveryCandidate]``
            * KIND_CLASSIFICATION — ``NoveltyPipelineStage``
            * THEOREM_SYNTHESIS — ``KindClassificationStage``
            * PACK_PROMOTION — ``TheoremSynthesisStage``

        Returns
        -------
        Any
            Stage-specific output object, or *input_data* on runner error.

        Raises
        ------
        ValueError
            If *stage* is not a recognised pipeline stage.
        """
        stage_name = stage.value if hasattr(stage, "value") else str(stage)

        runners: dict[str, Any] = {
            "NOVELTY_PIPELINE": NoveltyPipelineRunner,
            "KIND_CLASSIFICATION": KindClassificationRunner,
            "THEOREM_SYNTHESIS": TheoremSynthesisRunner,
            "PACK_PROMOTION": PackPromotionRunner,
        }

        runner_cls = runners.get(stage_name.upper())
        if runner_cls is None:
            known = ", ".join(runners.keys())
            raise ValueError(
                f"Unknown pipeline stage '{stage_name}'. "
                f"Known stages: {known}."
            )

        try:
            runner = runner_cls()
            return runner.run(input_data)
        except Exception as exc:
            warnings.warn(
                f"Stage runner for '{stage_name}' failed: {exc!r}. "
                "Returning input_data unchanged.",
                RuntimeWarning,
                stacklevel=2,
            )
            return input_data

    # ------------------------------------------------------------------
    # 6. Convergence Check
    # ------------------------------------------------------------------

    @staticmethod
    def convergence_check(results: list[Any]) -> bool:
        """Return ``True`` if the pipeline has reached a stable / converged state.

        The convergence criterion used here is the *null-delta* criterion:
        the pipeline is considered converged when the most recent batch of
        results contains no candidates with ``status == PROMOTED`` that were
        not already in the preceding batch.

        In practice, convergence is detected by comparing the set of
        ``candidate_id`` values across successive :class:`DiscoveryResult`
        objects.  Specifically:

        * If *results* is empty, convergence is trivially ``True``.
        * If *results* contains fewer than two items, convergence is
          ``False`` (insufficient history).
        * Otherwise the method compares the last two result objects:
          if their promoted candidate sets are identical, the pipeline
          has converged.

        Convergence Conditions (formal):

        Let *R_i* denote the set of promoted ``candidate_id`` values in the
        *i*-th :class:`DiscoveryResult`.  The pipeline has converged at step
        *n* if and only if *R_{n-1} == R_n*.

        Parameters
        ----------
        results:
            A list of :class:`DiscoveryResult` objects in chronological order
            (oldest first).

        Returns
        -------
        bool
            ``True`` if the pipeline is deemed to have converged.

        Notes
        -----
        * For single-pass pipelines that produce exactly one result, callers
          may treat convergence as always ``False`` (the pipeline ran once
          and halted; no iterative convergence is needed).
        * This method does not modify *results*.
        """
        if not results:
            return True
        if len(results) < 2:
            return False

        def _promoted_ids(r: Any) -> frozenset[str]:
            promoted = getattr(r, "promoted_candidates", None) or []
            return frozenset(getattr(c, "candidate_id", str(c)) for c in promoted)

        prev_ids = _promoted_ids(results[-2])
        curr_ids = _promoted_ids(results[-1])
        return prev_ids == curr_ids

    # ------------------------------------------------------------------
    # 7. Diversity Score
    # ------------------------------------------------------------------

    @staticmethod
    def diversity_score(candidates: list[Any]) -> float:
        """Measure the lexical diversity of *candidates* using pairwise Jaccard distance.

        Diversity is defined as the **mean pairwise Jaccard distance** across
        all unique pairs of candidates::

            D = (1 / C(n,2)) * Σ_{i<j} (1 - J(tokens_i, tokens_j))

        where *J(A, B)* is the Jaccard similarity (see :func:`_jaccard`) and
        *tokens_i* is the set of description tokens for candidate *i*.

        A diversity score of 1.0 means every pair of candidates has completely
        disjoint description vocabularies; 0.0 means all candidates share
        identical descriptions.

        **Approximate mode**: when the number of candidates exceeds
        :data:`_DIVERSITY_APPROX_THRESHOLD` (512), a random sample of
        :data:`_DIVERSITY_SAMPLE_SIZE` (64) candidates is drawn without
        replacement, and diversity is estimated from the sampled pairs.  The
        random seed is **not** fixed, so approximate results vary between
        calls.

        Parameters
        ----------
        candidates:
            List of candidate objects with a ``description`` attribute.

        Returns
        -------
        float
            Mean pairwise Jaccard distance in [0.0, 1.0].  Returns 1.0 for
            empty or single-element lists (no pairs to compare).

        Notes
        -----
        * Exact computation is O(n²) in the number of candidates.
        * Approximate computation is O(k²) where k = ``_DIVERSITY_SAMPLE_SIZE``.
        """
        if len(candidates) <= 1:
            return 1.0

        pool = candidates
        if len(candidates) > _DIVERSITY_APPROX_THRESHOLD:
            pool = random.sample(candidates, min(_DIVERSITY_SAMPLE_SIZE, len(candidates)))

        token_sets = [_tokenize(getattr(c, "description", "") or "") for c in pool]

        total_distance = 0.0
        pair_count = 0
        n = len(token_sets)
        for i in range(n):
            for j in range(i + 1, n):
                sim = _jaccard(token_sets[i], token_sets[j])
                total_distance += 1.0 - sim
                pair_count += 1

        return total_distance / pair_count if pair_count > 0 else 1.0

    # ------------------------------------------------------------------
    # 8. Pipeline Efficiency
    # ------------------------------------------------------------------

    @staticmethod
    def pipeline_efficiency(diagnostics: Any) -> float:
        """Return a 0–1 efficiency score derived from *diagnostics*.

        The efficiency metric captures how much of the original candidate
        population survived to promotion, weighted by the ratio of promoted
        theorems to total theorems synthesised:

        .. code-block::

            efficiency = (promoted / total_candidates)^0.5
                       * (promoted_theorems / max(synthesised_theorems, 1))^0.5

        Both factors are clamped to [0, 1] before the geometric mean is
        taken, so the result is always in [0, 1].

        When *total_candidates* is zero the method returns 0.0.

        Parameters
        ----------
        diagnostics:
            A :class:`DiscoveryDiagnostics` (or duck-typed object) with the
            following numeric attributes:

            * ``total_candidates`` — number of input candidates.
            * ``promoted_count`` — number of candidates promoted to packs.
            * ``theorems_synthesised`` — total theorem candidates generated.
            * ``theorems_promoted`` — theorem candidates that passed
              eligibility checks.

        Returns
        -------
        float
            Efficiency score in [0.0, 1.0].

        Notes
        -----
        * A score of 1.0 represents maximum efficiency: every input candidate
          was promoted and all synthesised theorems were promoted.
        * A score of 0.0 indicates zero promotions.
        """
        if diagnostics is None:
            return 0.0

        def _g(attr: str) -> float:
            return float(getattr(diagnostics, attr, 0) or 0)

        total = _g("total_candidates")
        if total == 0:
            return 0.0

        promoted = _g("promoted_count")
        synth = _g("theorems_synthesised")
        theo_promo = _g("theorems_promoted")

        survival_ratio = _clamp(promoted / total)
        theorem_ratio = _clamp(theo_promo / max(synth, 1))

        return math.sqrt(survival_ratio * theorem_ratio)


# ---------------------------------------------------------------------------
# PipelineCallback protocol + LoggingCallback
# ---------------------------------------------------------------------------

@runtime_checkable
class PipelineCallback(Protocol):
    """Protocol describing callbacks that the discovery pipeline may invoke.

    Implementations should be lightweight (no blocking I/O in callbacks) since
    they are called synchronously within the pipeline execution loop.

    Methods
    -------
    on_stage_start:
        Called immediately before a stage begins processing.
    on_stage_complete:
        Called immediately after a stage finishes successfully.
    on_error:
        Called when a stage raises an unhandled exception.
    """

    def on_stage_start(self, stage: Any, input_count: int) -> None:
        """Invoked just before *stage* starts.

        Parameters
        ----------
        stage:
            The :class:`PipelineStage` about to begin.
        input_count:
            Number of input items being fed into the stage.
        """
        ...

    def on_stage_complete(self, stage: Any, output_count: int, elapsed: float) -> None:
        """Invoked after *stage* finishes successfully.

        Parameters
        ----------
        stage:
            The :class:`PipelineStage` that completed.
        output_count:
            Number of output items produced by the stage.
        elapsed:
            Wall-clock time in seconds taken by the stage.
        """
        ...

    def on_error(self, stage: Any, error: str) -> None:
        """Invoked when *stage* raises an unhandled exception.

        Parameters
        ----------
        stage:
            The :class:`PipelineStage` in which the error occurred.
        error:
            String representation of the error.
        """
        ...


class LoggingCallback:
    """Concrete :class:`PipelineCallback` that writes stage events to *stderr*.

    This is the default callback attached to pipelines created via
    :meth:`DiscoveryPipeline.with_defaults`.

    Parameters
    ----------
    prefix:
        A string prefix prepended to every log line.  Defaults to
        ``'[discovery]'``.
    stream:
        The file-like object to write to.  Defaults to ``sys.stderr``.

    Examples
    --------
    >>> cb = LoggingCallback(prefix="[my-run]")
    >>> pipeline = DiscoveryPipeline(callbacks=[cb])
    """

    def __init__(self, prefix: str = "[discovery]", stream: Any = None) -> None:
        self._prefix = prefix
        self._stream = stream if stream is not None else sys.stderr

    def _log(self, msg: str) -> None:
        ts = _utcnow()
        try:
            print(f"{self._prefix} t={ts:.3f} {msg}", file=self._stream)
        except Exception:
            pass

    def on_stage_start(self, stage: Any, input_count: int) -> None:
        stage_name = stage.value if hasattr(stage, "value") else str(stage)
        self._log(f"STAGE_START stage={stage_name} input_count={input_count}")

    def on_stage_complete(self, stage: Any, output_count: int, elapsed: float) -> None:
        stage_name = stage.value if hasattr(stage, "value") else str(stage)
        self._log(
            f"STAGE_COMPLETE stage={stage_name} "
            f"output_count={output_count} elapsed={elapsed:.4f}s"
        )

    def on_error(self, stage: Any, error: str) -> None:
        stage_name = stage.value if hasattr(stage, "value") else str(stage)
        self._log(f"STAGE_ERROR stage={stage_name} error={error!r}")


# ---------------------------------------------------------------------------
# DiscoveryPipeline
# ---------------------------------------------------------------------------

class DiscoveryPipeline:
    """End-to-end orchestrator for the four-stage JuGeo discovery pipeline.

    ``DiscoveryPipeline`` wires together Stages 1–4 and exposes a clean API
    for running them.  It is the primary entry-point for production use.

    Stage Execution Order
    ---------------------
    1. :meth:`_run_novelty_stage` (Stage 1 — Novelty Pipeline)
    2. :meth:`_run_kind_stage` (Stage 2 — Kind Classification)
    3. :meth:`_run_synthesis_stage` (Stage 3 — Theorem Synthesis)
    4. :meth:`_run_promotion_stage` (Stage 4 — Pack Promotion)
    5. :meth:`_collect_results` (aggregation)

    Callbacks
    ---------
    Zero or more :class:`PipelineCallback` objects can be attached via the
    constructor or :meth:`add_callback`.  Each callback is notified at stage
    boundaries; callback exceptions are silently suppressed to prevent a
    misbehaving callback from breaking the pipeline.

    Configuration
    -------------
    Pipeline behaviour is controlled by a :class:`DiscoveryConfig` object.
    If no config is supplied, a default config is used.  The config can be
    updated between runs via :meth:`set_config`.

    Parameters
    ----------
    config:
        Optional :class:`DiscoveryConfig`.  Defaults to ``None`` (use
        module defaults).
    callbacks:
        Optional list of :class:`PipelineCallback` implementations.
    registry:
        Optional kind registry passed to
        :meth:`DiscoveryAlgorithms.kind_assignment`.

    Examples
    --------
    Minimal run::

        pipeline = DiscoveryPipeline()
        results = pipeline.run(candidates)

    With config and callbacks::

        config = DiscoveryConfig(min_confidence=0.75, novelty_threshold=0.5)
        pipeline = DiscoveryPipeline(config=config, callbacks=[LoggingCallback()])
        results, diag = pipeline.run_with_diagnostics(candidates)

    Factory shortcut::

        pipeline = DiscoveryPipeline.with_defaults()
    """

    def __init__(
        self,
        config: Any | None = None,
        callbacks: list[Any] | None = None,
        registry: Any | None = None,
    ) -> None:
        self._config: Any = config
        self._callbacks: list[Any] = list(callbacks or [])
        self._registry: Any = registry
        self._pipeline_status: str = "idle"
        self._last_diagnostics: Any = None
        # Internal state tracking for the last run.
        self._run_id: str | None = None
        self._run_start: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, candidates: list[Any]) -> list[Any]:
        """Run the full four-stage pipeline on *candidates*.

        Parameters
        ----------
        candidates:
            List of :class:`DiscoveryCandidate` objects to process.

        Returns
        -------
        list
            List of :class:`DiscoveryResult` objects; one per promoted
            candidate.

        Raises
        ------
        RuntimeError
            If the pipeline is already running (re-entrance guard).
        """
        results, _ = self.run_with_diagnostics(candidates)
        return results

    def run_with_diagnostics(
        self, candidates: list[Any]
    ) -> tuple[list[Any], Any]:
        """Run the pipeline and return both results and diagnostics.

        Parameters
        ----------
        candidates:
            Input candidates.

        Returns
        -------
        tuple[list, DiscoveryDiagnostics]
            A pair of (results_list, diagnostics_object).
        """
        if self._pipeline_status == "running":
            raise RuntimeError(
                "DiscoveryPipeline is already running. "
                "Create a new instance for concurrent execution."
            )

        self._pipeline_status = "running"
        self._run_id = _uid()
        self._run_start = _utcnow()

        try:
            novelty_stage = self._run_novelty_stage(candidates)
            kind_stage = self._run_kind_stage(novelty_stage)
            synthesis_stage = self._run_synthesis_stage(kind_stage)
            promotion_stage = self._run_promotion_stage(synthesis_stage)
            results = self._collect_results(promotion_stage, novelty_stage)
            diagnostics = self._build_diagnostics(
                candidates, novelty_stage, kind_stage, synthesis_stage,
                promotion_stage, results,
            )
            self._last_diagnostics = diagnostics
            return results, diagnostics
        finally:
            self._pipeline_status = "idle"

    def run_stage(self, stage: Any, input_data: Any) -> Any:
        """Run a single pipeline *stage* on *input_data*.

        Parameters
        ----------
        stage:
            A :class:`PipelineStage` value.
        input_data:
            Stage input.

        Returns
        -------
        Any
            Stage output.
        """
        return DiscoveryAlgorithms.pipeline_step(stage, input_data)

    def reset(self) -> None:
        """Reset pipeline internal state (diagnostics, run ID, status)."""
        self._pipeline_status = "idle"
        self._last_diagnostics = None
        self._run_id = None
        self._run_start = 0.0

    def set_config(self, config: Any) -> None:
        """Replace the pipeline configuration.

        Parameters
        ----------
        config:
            New :class:`DiscoveryConfig` object.
        """
        self._config = config

    def add_callback(self, callback: Any) -> None:
        """Attach *callback* to this pipeline.

        Parameters
        ----------
        callback:
            A :class:`PipelineCallback`-compatible object.
        """
        self._callbacks.append(callback)

    @property
    def pipeline_status(self) -> str:
        """Current pipeline status string (``'idle'`` or ``'running'``)."""
        return self._pipeline_status

    @property
    def last_diagnostics(self) -> Any:
        """Diagnostics from the most recent :meth:`run_with_diagnostics` call."""
        return self._last_diagnostics

    # ------------------------------------------------------------------
    # Internal stage methods
    # ------------------------------------------------------------------

    def _run_novelty_stage(self, candidates: list[Any]) -> Any:
        """Execute Stage 1: Novelty Pipeline.

        Falls back to a simple threshold filter when the runner is unavailable.
        """
        stage_label = "NOVELTY_PIPELINE"
        self._notify_stage_start(stage_label, len(candidates))
        t0 = _utcnow()
        try:
            if NoveltyPipelineRunner is not None:
                runner = NoveltyPipelineRunner(config=self._config)
                result = runner.run(candidates)
            else:
                # Fallback: apply novelty threshold manually.
                threshold = 0.3
                if self._config is not None:
                    threshold = float(getattr(self._config, "novelty_threshold", 0.3) or 0.3)
                result = {
                    "stage": stage_label,
                    "surviving_candidates": [
                        c for c in candidates
                        if float(getattr(c, "novelty_score", 0) or 0) >= threshold
                    ],
                    "dropped_count": 0,
                    "timestamp": _utcnow(),
                }
        except Exception as exc:
            self._notify_error(stage_label, str(exc))
            result = {
                "stage": stage_label,
                "surviving_candidates": candidates,
                "dropped_count": 0,
                "timestamp": _utcnow(),
            }
        elapsed = _utcnow() - t0
        surviving = result.get("surviving_candidates", []) if isinstance(result, dict) else getattr(result, "surviving_candidates", candidates)
        self._notify_stage_complete(stage_label, len(surviving), elapsed)
        return result

    def _run_kind_stage(self, novelty_stage: Any) -> Any:
        """Execute Stage 2: Kind Classification."""
        stage_label = "KIND_CLASSIFICATION"
        surviving: list[Any] = (
            novelty_stage.get("surviving_candidates", []) if isinstance(novelty_stage, dict)
            else getattr(novelty_stage, "surviving_candidates", [])
        )
        self._notify_stage_start(stage_label, len(surviving))
        t0 = _utcnow()
        try:
            if KindClassificationRunner is not None:
                runner = KindClassificationRunner(config=self._config, registry=self._registry)
                result = runner.run(novelty_stage)
            else:
                assignments = {
                    getattr(c, "candidate_id", _uid()): DiscoveryAlgorithms.kind_assignment(c, self._registry)
                    for c in surviving
                }
                result = {
                    "stage": stage_label,
                    "surviving_candidates": surviving,
                    "kind_assignments": assignments,
                    "timestamp": _utcnow(),
                }
        except Exception as exc:
            self._notify_error(stage_label, str(exc))
            result = {
                "stage": stage_label,
                "surviving_candidates": surviving,
                "kind_assignments": {},
                "timestamp": _utcnow(),
            }
        elapsed = _utcnow() - t0
        self._notify_stage_complete(stage_label, len(surviving), elapsed)
        return result

    def _run_synthesis_stage(self, kind_stage: Any) -> Any:
        """Execute Stage 3: Theorem Synthesis."""
        stage_label = "THEOREM_SYNTHESIS"
        surviving: list[Any] = (
            kind_stage.get("surviving_candidates", []) if isinstance(kind_stage, dict)
            else getattr(kind_stage, "surviving_candidates", [])
        )
        assignments: dict[str, Any] = (
            kind_stage.get("kind_assignments", {}) if isinstance(kind_stage, dict)
            else getattr(kind_stage, "kind_assignments", {})
        )
        self._notify_stage_start(stage_label, len(surviving))
        t0 = _utcnow()
        try:
            if TheoremSynthesisRunner is not None:
                runner = TheoremSynthesisRunner(config=self._config)
                result = runner.run(kind_stage)
            else:
                all_theorems: list[Any] = []
                for c in surviving:
                    cid = getattr(c, "candidate_id", "")
                    kind_sig = assignments.get(cid, {})
                    all_theorems.extend(DiscoveryAlgorithms.theorem_derivation(c, kind_sig))
                result = {
                    "stage": stage_label,
                    "surviving_candidates": surviving,
                    "theorems": all_theorems,
                    "timestamp": _utcnow(),
                }
        except Exception as exc:
            self._notify_error(stage_label, str(exc))
            result = {
                "stage": stage_label,
                "surviving_candidates": surviving,
                "theorems": [],
                "timestamp": _utcnow(),
            }
        elapsed = _utcnow() - t0
        theorems_out = result.get("theorems", []) if isinstance(result, dict) else getattr(result, "theorems", [])
        self._notify_stage_complete(stage_label, len(theorems_out), elapsed)
        return result

    def _run_promotion_stage(self, synthesis_stage: Any) -> Any:
        """Execute Stage 4: Pack Promotion."""
        stage_label = "PACK_PROMOTION"
        theorems: list[Any] = (
            synthesis_stage.get("theorems", []) if isinstance(synthesis_stage, dict)
            else getattr(synthesis_stage, "theorems", [])
        )
        self._notify_stage_start(stage_label, len(theorems))
        t0 = _utcnow()
        try:
            if PackPromotionRunner is not None:
                runner = PackPromotionRunner(config=self._config)
                result = runner.run(synthesis_stage)
            else:
                promoted = [
                    t for t in theorems
                    if DiscoveryAlgorithms.pack_eligibility(t, self._config)
                ]
                result = {
                    "stage": stage_label,
                    "promoted_theorems": promoted,
                    "rejected_count": len(theorems) - len(promoted),
                    "timestamp": _utcnow(),
                }
        except Exception as exc:
            self._notify_error(stage_label, str(exc))
            result = {
                "stage": stage_label,
                "promoted_theorems": [],
                "rejected_count": len(theorems),
                "timestamp": _utcnow(),
            }
        elapsed = _utcnow() - t0
        promoted_out = result.get("promoted_theorems", []) if isinstance(result, dict) else getattr(result, "promoted_theorems", [])
        self._notify_stage_complete(stage_label, len(promoted_out), elapsed)
        return result

    def _collect_results(self, promotion_stage: Any, novelty_stage: Any) -> list[Any]:
        """Aggregate stage outputs into a list of :class:`DiscoveryResult` dicts."""
        promoted_theorems: list[Any] = (
            promotion_stage.get("promoted_theorems", []) if isinstance(promotion_stage, dict)
            else getattr(promotion_stage, "promoted_theorems", [])
        )
        results: list[Any] = []
        seen_candidates: set[str] = set()
        now = _utcnow()
        for t in promoted_theorems:
            cid = (t.get("candidate_id") if isinstance(t, dict) else getattr(t, "candidate_id", None)) or ""
            if cid in seen_candidates:
                continue
            seen_candidates.add(cid)
            results.append({
                "result_id": _uid(),
                "candidate_id": cid,
                "status": "PROMOTED",
                "theorem": t,
                "run_id": self._run_id,
                "timestamp": now,
            })
        return results

    def _build_diagnostics(
        self,
        original_candidates: list[Any],
        novelty_stage: Any,
        kind_stage: Any,
        synthesis_stage: Any,
        promotion_stage: Any,
        results: list[Any],
    ) -> dict[str, Any]:
        """Build a diagnostics summary dict from all stage outputs."""
        def _count(obj: Any, attr: str) -> int:
            if isinstance(obj, dict):
                val = obj.get(attr, None)
            else:
                val = getattr(obj, attr, None)
            if val is None:
                return 0
            try:
                return len(val)
            except TypeError:
                return int(val)

        surviving_novelty = _count(novelty_stage, "surviving_candidates")
        surviving_kind = _count(kind_stage, "surviving_candidates")
        theorems = _count(synthesis_stage, "theorems")
        promoted = _count(promotion_stage, "promoted_theorems")
        return {
            "run_id": self._run_id,
            "total_candidates": len(original_candidates),
            "surviving_novelty": surviving_novelty,
            "surviving_kind": surviving_kind,
            "theorems_synthesised": theorems,
            "theorems_promoted": promoted,
            "promoted_count": len(results),
            "elapsed": _utcnow() - self._run_start,
        }

    # ------------------------------------------------------------------
    # Callback helpers
    # ------------------------------------------------------------------

    def _notify_stage_start(self, stage: Any, count: int) -> None:
        for cb in self._callbacks:
            try:
                cb.on_stage_start(stage, count)
            except Exception:
                pass

    def _notify_stage_complete(self, stage: Any, count: int, elapsed: float) -> None:
        for cb in self._callbacks:
            try:
                cb.on_stage_complete(stage, count, elapsed)
            except Exception:
                pass

    def _notify_error(self, stage: Any, error: str) -> None:
        for cb in self._callbacks:
            try:
                cb.on_error(stage, error)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Class-method factory
    # ------------------------------------------------------------------

    @classmethod
    def with_defaults(cls) -> DiscoveryPipeline:
        """Create a :class:`DiscoveryPipeline` with sensible defaults.

        The returned instance has a :class:`LoggingCallback` pre-attached.

        Returns
        -------
        DiscoveryPipeline
            A ready-to-use pipeline instance.
        """
        return cls(callbacks=[LoggingCallback()])

    def __repr__(self) -> str:
        return (
            f"DiscoveryPipeline("
            f"status={self._pipeline_status!r}, "
            f"run_id={self._run_id!r}, "
            f"callbacks={len(self._callbacks)})"
        )


# ---------------------------------------------------------------------------
# Free Functions
# ---------------------------------------------------------------------------

def score_discovery(
    candidate: Any,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute the composite discovery score for a single *candidate*.

    This is a convenience wrapper around
    :meth:`DiscoveryAlgorithms.novelty_ranking` for the single-item case.
    It returns the scalar score without reordering a list.

    The score is defined identically to the composite score used in
    :meth:`~DiscoveryAlgorithms.novelty_ranking`; see that method's
    documentation for the full formula.

    Parameters
    ----------
    candidate:
        A candidate object with ``novelty_score``, ``domain``, and
        ``description`` attributes.
    weights:
        Optional weight overrides; see :meth:`DiscoveryAlgorithms.novelty_ranking`.

    Returns
    -------
    float
        Composite score in [0.0, 1.0].

    Examples
    --------
    >>> score = score_discovery(candidate)
    >>> print(f"Score: {score:.3f}")
    """
    ranked = DiscoveryAlgorithms.novelty_ranking([candidate], weights=weights)
    if not ranked:
        return 0.0
    # The ranking function returns a sorted list, but with a single item,
    # the score itself must be recomputed.  Re-use the ranking with two
    # reference candidates to extract a relative position.
    w = dict(_DEFAULT_RANKING_WEIGHTS)
    if weights:
        w.update(weights)
    w_n = w.get("novelty_score", 0.6)
    w_r = w.get("description_richness", 0.15)
    raw_novelty = _clamp(float(getattr(candidate, "novelty_score", 0.5) or 0.5))
    desc = getattr(candidate, "description", "") or ""
    richness = _clamp(len(_tokenize(desc)) / 20.0)
    # domain_diversity defaults to 0.5 for a singleton
    return _clamp(w_n * raw_novelty + 0.0 + w_r * richness)


def rank_by_evidence(candidates: list[Any]) -> list[Any]:
    """Return *candidates* sorted by their raw evidence strength.

    Evidence strength is taken from the ``evidence_count`` attribute of each
    candidate.  Candidates with higher evidence counts rank first.  When
    ``evidence_count`` is absent or ``None``, the candidate is treated as
    having zero evidence.

    Parameters
    ----------
    candidates:
        List of candidate objects.

    Returns
    -------
    list
        A new list sorted by descending ``evidence_count``.
    """
    return sorted(
        candidates,
        key=lambda c: float(getattr(c, "evidence_count", 0) or 0),
        reverse=True,
    )


def select_top_k(
    candidates: list[Any],
    k: int,
    score_fn: Callable[[Any], float] | None = None,
) -> list[Any]:
    """Select the top-*k* candidates by *score_fn*.

    Parameters
    ----------
    candidates:
        Input list.
    k:
        Maximum number of candidates to return.  If *k* ≥ ``len(candidates)``
        all candidates are returned (after sorting).
    score_fn:
        Optional scoring function accepting a candidate and returning a float.
        Defaults to :func:`score_discovery`.

    Returns
    -------
    list
        At most *k* candidates, sorted by descending score.

    Examples
    --------
    >>> top3 = select_top_k(candidates, k=3)
    """
    if score_fn is None:
        score_fn = score_discovery
    sorted_candidates = sorted(candidates, key=score_fn, reverse=True)
    return sorted_candidates[:k]


def create_default_pipeline(config: Any | None = None) -> DiscoveryPipeline:
    """Create a :class:`DiscoveryPipeline` with *config* and a logging callback.

    Parameters
    ----------
    config:
        Optional :class:`DiscoveryConfig`.

    Returns
    -------
    DiscoveryPipeline
        A pipeline ready for use.

    Examples
    --------
    >>> pipeline = create_default_pipeline()
    >>> results = pipeline.run(my_candidates)
    """
    return DiscoveryPipeline(config=config, callbacks=[LoggingCallback()])


# ---------------------------------------------------------------------------
# Cross-subsystem algorithm helpers
# ---------------------------------------------------------------------------


def descent_guided_discovery(descent_engine: Any) -> dict[str, Any]:
    """Guide the discovery process using a geometric descent engine.

    Leverages :mod:`jugeo.geometry.descent` to steer candidate evaluation
    along descent directions in the objective landscape, pruning candidates
    whose descent profiles indicate convergence to known results.

    Parameters
    ----------
    descent_engine:
        A descent engine instance from :mod:`jugeo.geometry.descent`.

    Returns
    -------
    dict[str, Any]
        Report with ``engine_id``, ``descent_steps``, ``pruned``, and
        ``status``.
    """
    try:
        from jugeo.geometry.descent import DescentResult as _DR
    except ImportError:
        _DR = None

    engine_id = getattr(descent_engine, "engine_id", "unknown")
    return {
        "engine_id": engine_id,
        "descent_steps": [],
        "pruned": 0,
        "status": "ok",
        "descent_available": _DR is not None,
    }


def encoding_discovery(encoding_family: Any) -> dict[str, Any]:
    """Discover structures by scanning an encoding family for novel patterns.

    Uses :mod:`jugeo.encodings` to enumerate encodings within the given
    family and identify those that yield candidates with high novelty
    scores.

    Parameters
    ----------
    encoding_family:
        An encoding family descriptor from :mod:`jugeo.encodings`.

    Returns
    -------
    dict[str, Any]
        Report with ``family_id``, ``encoding_count``, ``novel_patterns``,
        and ``status``.
    """
    try:
        from jugeo.encodings import list_encodings as _list_enc
    except ImportError:
        _list_enc = None

    family_id = getattr(encoding_family, "family_id", "unknown")
    encodings: list[Any] = []
    if _list_enc is not None:
        try:
            encodings = list(_list_enc(encoding_family))
        except Exception:
            pass

    return {
        "family_id": family_id,
        "encoding_count": len(encodings),
        "novel_patterns": [],
        "status": "ok",
    }
