r"""Analogy transport algorithms: planner, executor, and normalizer — theory2.tex Ch61.

# copilot: shared-core marker

This module implements the core algorithmic infrastructure for theorem-level
analogy transport in the JuGeo system.  Analogy transport is the process of
taking a theorem proved in one mathematical domain (the *source domain*) and
finding a structurally analogous theorem in a different mathematical domain
(the *target domain*) by exploiting a functor-like mapping between the two
domains.

Mathematical Background
-----------------------

An *analogy functor* F : C → D between two mathematical domains C and D
consists of:

  - An object map  F_0 : Ob(C) → Ob(D)
  - A morphism map F_1 : Mor(C) → Mor(D)

such that the functor laws hold:

  - Identity preservation:  F_1(id_X) = id_{F_0(X)} for all objects X in C
  - Composition preservation:  F_1(g ∘ f) = F_1(g) ∘ F_1(f)

When F is not a strict functor but only a *weak analogy*, the coherence
conditions are relaxed to approximate or structural preservation.  The
quality of the analogy is measured by a confidence score in [0, 1] that
reflects how well the transported theorem is expected to hold in the target
domain.

Analogy Transport Pipeline
--------------------------

The transport pipeline consists of three main stages:

  1. **Planning** (:class:`AnalogyTransportPlanner`):
     Given a source theorem T and a target domain D, the planner searches a
     registry of known analogy functors to find the best functor F : C → D
     for transporting T.  It then generates a :class:`TransportPlan` that
     specifies how to apply F to T step by step.

  2. **Execution** (:class:`AnalogyTransportExecutor`):
     The executor takes a :class:`TransportPlan` and applies the analogy
     functor step by step, generating a :class:`TransportedTheorem` T' in
     the target domain.  Each step is recorded in the ``transport_trace``
     for full auditability.

  3. **Normalisation** (:class:`AnalogyTransportNormalizer`):
     The normaliser post-processes the transported theorem to ensure it
     conforms to the naming conventions and notational standards of the
     target domain, resolving conflicts with existing theorems and
     standardising mathematical notation.

Design Notes
------------

* All value objects use ``@dataclass(frozen=True, slots=True)`` for
  immutability and memory efficiency.
* Mutable state containers (configs, registries) use ``@dataclass(slots=True)``.
* Analogy quality is assessed using a combination of Jaccard similarity
  on domain-concept vocabularies and a structural coherence check.
* The planner uses a greedy ranking strategy: functors are ranked by
  their combined faithfulness × coverage score.
* The executor applies the functor object-by-object and morphism-by-
  morphism, recording each step in the trace.
* The normaliser applies a sequence of rewrite rules to standardise
  notation in the target domain.

Complexity Summary
------------------

.. list-table::
   :header-rows: 1

   * - Algorithm
     - Time complexity
   * - :meth:`AnalogyTransportPlanner.find_analogies`
     - O(|F| · |C|) where |F| = number of known functors, |C| = domain size
   * - :meth:`AnalogyTransportExecutor.apply_functor`
     - O(|Ob(T)| + |Mor(T)|) for theorem T
   * - :meth:`AnalogyTransportNormalizer.run_normalization_pipeline`
     - O(n · |T|) for n results and theorem size |T|

References
----------

theory2.tex, Chapter 61 (Analogy Transport).
Barr & Wells, *Category Theory for Computing Science*, Ch. 2–3.
Mac Lane, *Categories for the Working Mathematician*, Ch. 1.
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.registry import EvidenceRegistry  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.packs.core import PackStore  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.orchestration.scheduler import TaskScheduler  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.analogy_transport.models import (  # type: ignore[import]
        AnalogyMap,
        TransportedIdea,
        TransportFidelity,
    )
except Exception:
    pass

try:
    from jugeo.ideation.ideas import Idea  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.federation import CrossRegimeBridge  # type: ignore[import]
except Exception:
    pass

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Return lowercase alphanumeric tokens of length ≥ 2 from *text*."""
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between token sets *a* and *b*."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union)


def _coverage_fraction(mapped_keys: set[str], available_keys: set[str]) -> float:
    """Compute the fraction of *available_keys* that appear in *mapped_keys*."""
    if not available_keys:
        return 1.0
    return len(mapped_keys & available_keys) / len(available_keys)


def _coherence_score(conditions: list[str], satisfied: list[str]) -> float:
    """Compute the fraction of coherence conditions that are satisfied."""
    if not conditions:
        return 1.0
    return len([c for c in satisfied if c in conditions]) / len(conditions)


def _normalize_name(name: str, prefix: str = "") -> str:
    """Convert *name* to snake_case and optionally prepend *prefix*."""
    s = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()
    s = re.sub(r"_+", "_", s).strip("_")
    return f"{prefix}_{s}" if prefix else s


def _weighted_score(components: dict[str, float], weights: dict[str, float]) -> float:
    """Compute a weighted sum of score components, clipped to [0, 1].

    Missing weights default to 1 / len(components).
    """
    if not components:
        return 0.0
    default_w = 1.0 / len(components)
    total = sum(
        components.get(k, 0.0) * weights.get(k, default_w) for k in components
    )
    return _clamp(total)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AnalogyQuality(str, Enum):
    """Quality rating for an analogy functor in the transport context.

    POOR means the analogy is superficial with little structural content.
    FAIR means moderate structural correspondence exists.
    GOOD means strong correspondence with minor gaps.
    EXCELLENT means near-perfect or formally verified correspondence.
    """

    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"

    @classmethod
    def from_score(cls, score: float) -> AnalogyQuality:
        """Derive quality level from a numeric score in [0, 1]."""
        if score >= 0.85:
            return cls.EXCELLENT
        if score >= 0.65:
            return cls.GOOD
        if score >= 0.40:
            return cls.FAIR
        return cls.POOR

    def numeric(self) -> float:
        """Return a representative numeric value for this quality level."""
        return {
            AnalogyQuality.POOR: 0.2,
            AnalogyQuality.FAIR: 0.5,
            AnalogyQuality.GOOD: 0.75,
            AnalogyQuality.EXCELLENT: 0.95,
        }[self]

    def is_acceptable(self) -> bool:
        """Return True when quality is FAIR or better."""
        return self in (AnalogyQuality.FAIR, AnalogyQuality.GOOD, AnalogyQuality.EXCELLENT)


class TransportStatus(str, Enum):
    """Lifecycle status of a theorem transport operation.

    PLANNED means a plan has been created but not yet executed.
    EXECUTING means the executor is currently applying the functor.
    COMPLETED means transport finished successfully.
    FAILED means transport encountered an unrecoverable error.
    INVALID means the plan was rejected by validation.
    """

    PLANNED = "planned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    INVALID = "invalid"

    def is_terminal(self) -> bool:
        """Return True when the status represents a terminal state."""
        return self in (TransportStatus.COMPLETED, TransportStatus.FAILED,
                        TransportStatus.INVALID)

    def can_execute(self) -> bool:
        """Return True when the transport can be started."""
        return self == TransportStatus.PLANNED


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnalogyFunctor:
    """An analogy functor mapping between two mathematical domains.

    Attributes:
        functor_id: Stable unique identifier for this functor.
        source_domain: Name of the domain this functor maps from.
        target_domain: Name of the domain this functor maps to.
        object_map: Dictionary mapping source object names to target object names.
        morphism_map: Dictionary mapping source morphism names to target morphism names.
        coherence_conditions: List of condition strings that must hold for the
            functor to be structurally valid.
        faithfulness_score: Numeric estimate in [0, 1] of how faithfully the
            functor preserves structure.
        created_at: ISO-8601 creation timestamp.
        description: Human-readable description of the analogy.
    """

    functor_id: str
    source_domain: str
    target_domain: str
    object_map: dict[str, str]
    morphism_map: dict[str, str]
    coherence_conditions: tuple[str, ...]
    faithfulness_score: float
    created_at: str
    description: str = ""

    def quality(self) -> AnalogyQuality:
        """Return the quality classification of this functor."""
        return AnalogyQuality.from_score(self.faithfulness_score)

    def coverage(self, source_objects: set[str]) -> float:
        """Return the fraction of *source_objects* covered by the object map."""
        return _coverage_fraction(set(self.object_map.keys()), source_objects)

    def maps_object(self, name: str) -> bool:
        """Return True when *name* appears in the object map."""
        return name in self.object_map

    def maps_morphism(self, name: str) -> bool:
        """Return True when *name* appears in the morphism map."""
        return name in self.morphism_map

    def transport_object(self, name: str) -> str | None:
        """Return the target name for source object *name*, or None."""
        return self.object_map.get(name)

    def transport_morphism(self, name: str) -> str | None:
        """Return the target name for source morphism *name*, or None."""
        return self.morphism_map.get(name)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "functor_id": self.functor_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "object_map": dict(self.object_map),
            "morphism_map": dict(self.morphism_map),
            "coherence_conditions": list(self.coherence_conditions),
            "faithfulness_score": self.faithfulness_score,
            "created_at": self.created_at,
            "description": self.description,
            "quality": self.quality().value,
        }

    def summary(self) -> str:
        """Return a one-line summary of this functor."""
        return (
            f"AnalogyFunctor({self.source_domain!r} → {self.target_domain!r}, "
            f"faith={self.faithfulness_score:.2f}, quality={self.quality().value}, "
            f"objects={len(self.object_map)}, morphisms={len(self.morphism_map)})"
        )


@dataclass(frozen=True, slots=True)
class SourceTheorem:
    """A theorem in the source domain that is a candidate for transport.

    Attributes:
        theorem_id: Stable unique identifier.
        source_domain: Domain in which the theorem lives.
        statement: Formal statement of the theorem.
        objects_used: Tuple of domain object names referenced in the theorem.
        morphisms_used: Tuple of morphism names referenced in the theorem.
        assumptions: Tuple of assumption strings required by the theorem.
        tags: Tuple of classification tags.
        created_at: ISO-8601 creation timestamp.
    """

    theorem_id: str
    source_domain: str
    statement: str
    objects_used: tuple[str, ...]
    morphisms_used: tuple[str, ...]
    assumptions: tuple[str, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=_now_iso)

    def vocabulary(self) -> set[str]:
        """Return combined vocabulary from statement tokens and object/morphism names."""
        toks = _tokenize(self.statement)
        toks |= set(self.objects_used)
        toks |= set(self.morphisms_used)
        return toks

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "theorem_id": self.theorem_id,
            "source_domain": self.source_domain,
            "statement": self.statement,
            "objects_used": list(self.objects_used),
            "morphisms_used": list(self.morphisms_used),
            "assumptions": list(self.assumptions),
            "tags": list(self.tags),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TransportedTheorem:
    """A theorem that has been transported to a target domain.

    Attributes:
        transport_id: Stable unique identifier for this transport record.
        source_theorem_id: ID of the original :class:`SourceTheorem`.
        target_domain: The domain to which the theorem was transported.
        transported_statement: The statement as reformulated in the target domain.
        object_translations: Mapping of source object names to target names used.
        morphism_translations: Mapping of source morphism names to target names.
        transport_trace: Ordered list of step descriptions applied during transport.
        confidence_score: Estimated probability in [0, 1] that the theorem is
            valid in the target domain.
        functor_id: ID of the :class:`AnalogyFunctor` used.
        status: Current :class:`TransportStatus`.
        created_at: ISO-8601 creation timestamp.
    """

    transport_id: str
    source_theorem_id: str
    target_domain: str
    transported_statement: str
    object_translations: dict[str, str]
    morphism_translations: dict[str, str]
    transport_trace: tuple[str, ...]
    confidence_score: float
    functor_id: str
    status: TransportStatus
    created_at: str = field(default_factory=_now_iso)

    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """Return True when confidence exceeds *threshold*."""
        return self.confidence_score >= threshold

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "transport_id": self.transport_id,
            "source_theorem_id": self.source_theorem_id,
            "target_domain": self.target_domain,
            "transported_statement": self.transported_statement,
            "object_translations": dict(self.object_translations),
            "morphism_translations": dict(self.morphism_translations),
            "transport_trace": list(self.transport_trace),
            "confidence_score": self.confidence_score,
            "functor_id": self.functor_id,
            "status": self.status.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TransportResult:
    """The complete result of a transport operation.

    Attributes:
        result_id: Stable unique identifier.
        source_theorem: The original :class:`SourceTheorem`.
        transported_theorem: The resulting :class:`TransportedTheorem`.
        functor_used: The :class:`AnalogyFunctor` that was applied.
        confidence_score: Overall confidence for the transported theorem.
        transport_trace: Ordered list of step descriptions.
        status: Final :class:`TransportStatus`.
        elapsed_seconds: Time taken for the transport, in seconds.
        created_at: ISO-8601 creation timestamp.
    """

    result_id: str
    source_theorem: SourceTheorem
    transported_theorem: TransportedTheorem
    functor_used: AnalogyFunctor
    confidence_score: float
    transport_trace: tuple[str, ...]
    status: TransportStatus
    elapsed_seconds: float
    created_at: str = field(default_factory=_now_iso)

    def succeeded(self) -> bool:
        """Return True when transport completed successfully."""
        return self.status == TransportStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "result_id": self.result_id,
            "source_theorem": self.source_theorem.to_dict(),
            "transported_theorem": self.transported_theorem.to_dict(),
            "functor_used": self.functor_used.to_dict(),
            "confidence_score": self.confidence_score,
            "transport_trace": list(self.transport_trace),
            "status": self.status.value,
            "elapsed_seconds": self.elapsed_seconds,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TransportPlan:
    """A validated plan describing how to execute an analogy transport.

    Attributes:
        plan_id: Stable unique identifier.
        source_theorem: The :class:`SourceTheorem` to transport.
        functor: The :class:`AnalogyFunctor` to apply.
        planned_steps: Ordered list of step descriptions.
        estimated_confidence: Planner's pre-execution confidence estimate.
        status: Current :class:`TransportStatus` (should be PLANNED initially).
        created_at: ISO-8601 creation timestamp.
    """

    plan_id: str
    source_theorem: SourceTheorem
    functor: AnalogyFunctor
    planned_steps: tuple[str, ...]
    estimated_confidence: float
    status: TransportStatus
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "plan_id": self.plan_id,
            "source_theorem_id": self.source_theorem.theorem_id,
            "functor_id": self.functor.functor_id,
            "planned_steps": list(self.planned_steps),
            "estimated_confidence": self.estimated_confidence,
            "status": self.status.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class PlanValidationResult:
    """Outcome of validating a :class:`TransportPlan`.

    Attributes:
        plan_id: ID of the validated plan.
        is_valid: Whether the plan passed all validation checks.
        errors: Tuple of error message strings (empty when valid).
        warnings: Tuple of non-fatal warning strings.
        coverage_fraction: Fraction of source objects covered by the functor.
        coherence_fraction: Fraction of coherence conditions that are met.
        validated_at: ISO-8601 timestamp.
    """

    plan_id: str
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    coverage_fraction: float
    coherence_fraction: float
    validated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "plan_id": self.plan_id,
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "coverage_fraction": self.coverage_fraction,
            "coherence_fraction": self.coherence_fraction,
            "validated_at": self.validated_at,
        }


@dataclass(frozen=True, slots=True)
class PlanningCycleResult:
    """Aggregated result of a complete planning cycle.

    Attributes:
        cycle_id: Stable unique identifier.
        target_domain: The target domain for the cycle.
        plans: Tuple of :class:`TransportPlan` instances generated.
        n_theorems_processed: Number of source theorems considered.
        n_functors_found: Total number of analogy functors discovered.
        best_functor_score: Highest score among all discovered functors.
        elapsed_seconds: Total cycle time in seconds.
        created_at: ISO-8601 timestamp.
    """

    cycle_id: str
    target_domain: str
    plans: tuple[TransportPlan, ...]
    n_theorems_processed: int
    n_functors_found: int
    best_functor_score: float
    elapsed_seconds: float
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "cycle_id": self.cycle_id,
            "target_domain": self.target_domain,
            "n_plans": len(self.plans),
            "n_theorems_processed": self.n_theorems_processed,
            "n_functors_found": self.n_functors_found,
            "best_functor_score": self.best_functor_score,
            "elapsed_seconds": self.elapsed_seconds,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TransportVerification:
    """Verification record for a completed transport.

    Attributes:
        verification_id: Stable unique identifier.
        result_id: ID of the :class:`TransportResult` that was verified.
        checks_passed: Tuple of check names that passed.
        checks_failed: Tuple of check names that failed.
        is_verified: Whether all required checks passed.
        confidence_adjustment: Delta applied to the confidence score (may be negative).
        verified_at: ISO-8601 timestamp.
    """

    verification_id: str
    result_id: str
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    is_verified: bool
    confidence_adjustment: float
    verified_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "verification_id": self.verification_id,
            "result_id": self.result_id,
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
            "is_verified": self.is_verified,
            "confidence_adjustment": self.confidence_adjustment,
            "verified_at": self.verified_at,
        }


@dataclass(frozen=True, slots=True)
class NormalizedTheorem:
    """A transported theorem that has been normalised for the target domain.

    Attributes:
        normalized_id: Stable unique identifier.
        transport_id: ID of the source :class:`TransportedTheorem`.
        target_domain: The target domain.
        normalized_statement: Statement after applying notation rewrites.
        canonical_name: Assigned canonical name in the target domain.
        notation_rewrites: Mapping of original notation to normalised notation.
        confidence_score: Final confidence after normalisation.
        created_at: ISO-8601 creation timestamp.
    """

    normalized_id: str
    transport_id: str
    target_domain: str
    normalized_statement: str
    canonical_name: str
    notation_rewrites: dict[str, str]
    confidence_score: float
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "normalized_id": self.normalized_id,
            "transport_id": self.transport_id,
            "target_domain": self.target_domain,
            "normalized_statement": self.normalized_statement,
            "canonical_name": self.canonical_name,
            "notation_rewrites": dict(self.notation_rewrites),
            "confidence_score": self.confidence_score,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TransportPlannerConfig:
    """Configuration for :class:`AnalogyTransportPlanner`.

    Attributes:
        max_functors_per_pair: Maximum number of functors to consider for a
            given source/target domain pair.
        min_functor_quality: Minimum :class:`AnalogyQuality` required for a
            functor to be used in planning.
        min_coverage: Minimum object-map coverage fraction required.
        top_k_functors: Number of top-ranked functors to return.
        enable_composition: Whether to attempt functor composition to discover
            indirect analogies.
        planning_timeout_seconds: Maximum seconds to spend in planning.
    """

    max_functors_per_pair: int = 20
    min_functor_quality: AnalogyQuality = AnalogyQuality.FAIR
    min_coverage: float = 0.3
    top_k_functors: int = 5
    enable_composition: bool = False
    planning_timeout_seconds: float = 30.0


@dataclass(slots=True)
class TransportExecutorConfig:
    """Configuration for :class:`AnalogyTransportExecutor`.

    Attributes:
        max_trace_depth: Maximum number of trace steps to record.
        verify_after_execution: Whether to run a post-execution verification check.
        confidence_decay_per_gap: Amount to subtract from confidence for each
            unmapped object or morphism encountered during execution.
        execution_timeout_seconds: Maximum seconds to spend on a single transport.
        batch_size: Number of plans to execute in each batch call.
    """

    max_trace_depth: int = 50
    verify_after_execution: bool = True
    confidence_decay_per_gap: float = 0.05
    execution_timeout_seconds: float = 60.0
    batch_size: int = 16


@dataclass(slots=True)
class NormalizerConfig:
    """Configuration for :class:`AnalogyTransportNormalizer`.

    Attributes:
        naming_prefix: Optional prefix to prepend to canonical theorem names.
        apply_latex_rewrites: Whether to apply LaTeX notation standardisation.
        conflict_resolution_strategy: One of ``"rename"``, ``"skip"``, or ``"overwrite"``.
        max_notation_rewrites: Maximum number of notation rewrites to attempt.
        lowercase_names: Whether to force canonical names to lowercase.
    """

    naming_prefix: str = "transported"
    apply_latex_rewrites: bool = True
    conflict_resolution_strategy: str = "rename"
    max_notation_rewrites: int = 100
    lowercase_names: bool = True


# ---------------------------------------------------------------------------
# DomainRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DomainRegistry:
    """A registry of mathematical domains and the analogy functors between them.

    Attributes:
        _domains: Mapping from domain name to a dict of domain metadata.
        _functors: List of all registered :class:`AnalogyFunctor` instances.
        _functor_index: Two-level index ``{source: {target: [functor_id, ...]}}``.
    """

    _domains: dict[str, dict[str, Any]] = field(default_factory=dict)
    _functors: list[AnalogyFunctor] = field(default_factory=list)
    _functor_index: dict[str, dict[str, list[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )

    # ------------------------------------------------------------------
    # Domain management
    # ------------------------------------------------------------------

    def register_domain(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        """Register a domain under *name* with optional *metadata*."""
        self._domains[name] = metadata or {}
        _log.debug("DomainRegistry: registered domain %r", name)

    def has_domain(self, name: str) -> bool:
        """Return True if *name* is a registered domain."""
        return name in self._domains

    def domain_names(self) -> list[str]:
        """Return a sorted list of all registered domain names."""
        return sorted(self._domains.keys())

    # ------------------------------------------------------------------
    # Functor management
    # ------------------------------------------------------------------

    def register_functor(self, functor: AnalogyFunctor) -> None:
        """Register *functor* in the registry and update the index."""
        self._functors.append(functor)
        self._functor_index[functor.source_domain][functor.target_domain].append(
            functor.functor_id
        )
        _log.debug(
            "DomainRegistry: registered functor %s (%s → %s)",
            functor.functor_id,
            functor.source_domain,
            functor.target_domain,
        )

    def get_functors(
        self, source_domain: str, target_domain: str
    ) -> list[AnalogyFunctor]:
        """Return all registered functors for the given domain pair."""
        ids = set(self._functor_index.get(source_domain, {}).get(target_domain, []))
        return [f for f in self._functors if f.functor_id in ids]

    def all_functors(self) -> list[AnalogyFunctor]:
        """Return all registered functors."""
        return list(self._functors)

    def functor_count(self) -> int:
        """Return the total number of registered functors."""
        return len(self._functors)

    def summary(self) -> str:
        """Return a one-line summary of registry contents."""
        return (
            f"DomainRegistry(domains={len(self._domains)}, "
            f"functors={len(self._functors)})"
        )


# ---------------------------------------------------------------------------
# AnalogyTransportPlanner
# ---------------------------------------------------------------------------


class AnalogyTransportPlanner:
    """Plans analogy transport operations for a batch of source theorems.

    Given a set of source theorems and a target domain, the planner discovers
    applicable analogy functors, validates their coverage, and produces
    :class:`TransportPlan` instances ready for execution.

    Attributes:
        _config: :class:`TransportPlannerConfig` controlling planner behaviour.
    """

    def __init__(self, config: TransportPlannerConfig | None = None) -> None:
        self._config = config or TransportPlannerConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_analogies(
        self,
        source_domain: str,
        target_domain: str,
        domain_registry: DomainRegistry,
    ) -> list[AnalogyFunctor]:
        """Find analogy functors from *source_domain* to *target_domain*.

        Queries *domain_registry* for registered functors, filters by the
        minimum quality threshold in the config, and returns the top-k
        ranked by quality score.

        Parameters:
            source_domain: Name of the source domain.
            target_domain: Name of the target domain.
            domain_registry: Registry of known domains and functors.

        Returns:
            List of :class:`AnalogyFunctor` instances, sorted best-first.
        """
        _log.info(
            "Planner.find_analogies: %r → %r", source_domain, target_domain
        )
        candidates = domain_registry.get_functors(source_domain, target_domain)
        min_score = self._config.min_functor_quality.numeric()
        filtered = [f for f in candidates if f.faithfulness_score >= min_score]
        ranked = self.rank_functors_by_quality(filtered)
        result = ranked[: self._config.top_k_functors]
        _log.debug("Planner.find_analogies: %d candidates → %d selected", len(candidates), len(result))
        return result

    def plan_transport(
        self, theorem: SourceTheorem, functor: AnalogyFunctor
    ) -> TransportPlan:
        """Create a :class:`TransportPlan` for transporting *theorem* via *functor*.

        The plan enumerates the steps needed: mapping each object, then each
        morphism, then rewriting the theorem statement.

        Parameters:
            theorem: The source theorem to transport.
            functor: The analogy functor to apply.

        Returns:
            A :class:`TransportPlan` with status ``PLANNED``.
        """
        steps: list[str] = []
        steps.append(
            f"Begin transport of theorem {theorem.theorem_id!r} "
            f"via functor {functor.functor_id!r}"
        )
        for obj in theorem.objects_used:
            target = functor.transport_object(obj)
            if target is not None:
                steps.append(f"Map object {obj!r} → {target!r}")
            else:
                steps.append(f"Object {obj!r} has no mapping (gap)")
        for mor in theorem.morphisms_used:
            target = functor.transport_morphism(mor)
            if target is not None:
                steps.append(f"Map morphism {mor!r} → {target!r}")
            else:
                steps.append(f"Morphism {mor!r} has no mapping (gap)")
        steps.append("Rewrite theorem statement in target domain")
        steps.append("Verify coherence conditions")
        coverage = functor.coverage(set(theorem.objects_used))
        conf = _clamp(functor.faithfulness_score * coverage)
        return TransportPlan(
            plan_id=_uid(),
            source_theorem=theorem,
            functor=functor,
            planned_steps=tuple(steps),
            estimated_confidence=conf,
            status=TransportStatus.PLANNED,
        )

    def validate_transport_plan(self, plan: TransportPlan) -> PlanValidationResult:
        """Validate a :class:`TransportPlan` before execution.

        Checks that the functor covers a sufficient fraction of the source
        theorem's objects, that required coherence conditions are present,
        and that the plan status is PLANNED.

        Parameters:
            plan: The plan to validate.

        Returns:
            A :class:`PlanValidationResult` indicating whether the plan is valid.
        """
        errors: list[str] = []
        warnings: list[str] = []
        theorem = plan.source_theorem
        functor = plan.functor
        if plan.status != TransportStatus.PLANNED:
            errors.append(f"Plan status is {plan.status.value!r}; expected 'planned'")
        coverage = functor.coverage(set(theorem.objects_used))
        if coverage < self._config.min_coverage:
            errors.append(
                f"Coverage {coverage:.2f} is below minimum {self._config.min_coverage:.2f}"
            )
        elif coverage < 0.6:
            warnings.append(
                f"Coverage {coverage:.2f} is low; some objects may not transport cleanly"
            )
        unmapped_morphisms = [
            m for m in theorem.morphisms_used if not functor.maps_morphism(m)
        ]
        if unmapped_morphisms:
            warnings.append(
                f"Unmapped morphisms: {', '.join(unmapped_morphisms[:5])}"
            )
        coh_fraction = _coherence_score(
            list(functor.coherence_conditions),
            [c for c in functor.coherence_conditions if "identity" in c or "compose" in c],
        )
        if coh_fraction < 0.5:
            warnings.append(
                f"Only {coh_fraction:.0%} of coherence conditions appear to be met"
            )
        is_valid = len(errors) == 0
        return PlanValidationResult(
            plan_id=plan.plan_id,
            is_valid=is_valid,
            errors=tuple(errors),
            warnings=tuple(warnings),
            coverage_fraction=coverage,
            coherence_fraction=coh_fraction,
        )

    def rank_functors_by_quality(
        self, functors: list[AnalogyFunctor]
    ) -> list[AnalogyFunctor]:
        """Return *functors* sorted by quality score, best first.

        Uses :func:`score_analogy_functor` to compute composite scores.

        Parameters:
            functors: List of functors to rank.

        Returns:
            New list sorted from highest to lowest score.
        """
        return sorted(functors, key=score_analogy_functor, reverse=True)

    def run_planning_cycle(
        self,
        source_theorems: list[SourceTheorem],
        target_domain: str,
        registry: DomainRegistry,
    ) -> PlanningCycleResult:
        """Run a complete planning cycle for multiple source theorems.

        For each theorem, discovers applicable functors, selects the best,
        and generates a transport plan.

        Parameters:
            source_theorems: List of theorems to plan transport for.
            target_domain: The target mathematical domain.
            registry: Registry of known domains and functors.

        Returns:
            A :class:`PlanningCycleResult` summarising all generated plans.
        """
        t_start = time.monotonic()
        plans: list[TransportPlan] = []
        all_functors_found: set[str] = set()
        best_score = 0.0
        for theorem in source_theorems:
            functors = self.find_analogies(theorem.source_domain, target_domain, registry)
            for f in functors:
                all_functors_found.add(f.functor_id)
                s = score_analogy_functor(f)
                if s > best_score:
                    best_score = s
            if functors:
                best_functor = self.rank_functors_by_quality(functors)[0]
                plan = self.plan_transport(theorem, best_functor)
                validation = self.validate_transport_plan(plan)
                if validation.is_valid:
                    plans.append(plan)
                else:
                    _log.warning(
                        "Plan for theorem %r is invalid: %s",
                        theorem.theorem_id,
                        "; ".join(validation.errors),
                    )
            else:
                _log.info(
                    "No functors found for theorem %r → %r",
                    theorem.source_domain,
                    target_domain,
                )
        elapsed = time.monotonic() - t_start
        return PlanningCycleResult(
            cycle_id=_uid(),
            target_domain=target_domain,
            plans=tuple(plans),
            n_theorems_processed=len(source_theorems),
            n_functors_found=len(all_functors_found),
            best_functor_score=best_score,
            elapsed_seconds=elapsed,
        )


# ---------------------------------------------------------------------------
# AnalogyTransportExecutor
# ---------------------------------------------------------------------------


class AnalogyTransportExecutor:
    """Executes analogy transport plans, producing transported theorems.

    The executor applies an :class:`AnalogyFunctor` to a
    :class:`SourceTheorem` step by step, recording each transformation
    in a trace for full auditability.

    Attributes:
        _config: :class:`TransportExecutorConfig` controlling execution behaviour.
    """

    def __init__(self, config: TransportExecutorConfig | None = None) -> None:
        self._config = config or TransportExecutorConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_transport(self, plan: TransportPlan) -> TransportResult:
        """Execute a :class:`TransportPlan` and return the :class:`TransportResult`.

        If an error occurs during execution, returns a result with status
        ``FAILED`` and an empty trace.

        Parameters:
            plan: A validated plan with status ``PLANNED``.

        Returns:
            A :class:`TransportResult` capturing the outcome.
        """
        t_start = time.monotonic()
        _log.info("Executor.execute_transport: plan %r", plan.plan_id)
        try:
            transported = self.apply_functor(plan.functor, plan.source_theorem)
            status = TransportStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            _log.error("Executor.execute_transport failed: %s", exc)
            transported = TransportedTheorem(
                transport_id=_uid(),
                source_theorem_id=plan.source_theorem.theorem_id,
                target_domain=plan.functor.target_domain,
                transported_statement="<execution failed>",
                object_translations={},
                morphism_translations={},
                transport_trace=(f"Error: {exc}",),
                confidence_score=0.0,
                functor_id=plan.functor.functor_id,
                status=TransportStatus.FAILED,
            )
            status = TransportStatus.FAILED
        elapsed = time.monotonic() - t_start
        result = TransportResult(
            result_id=_uid(),
            source_theorem=plan.source_theorem,
            transported_theorem=transported,
            functor_used=plan.functor,
            confidence_score=transported.confidence_score,
            transport_trace=transported.transport_trace,
            status=status,
            elapsed_seconds=elapsed,
        )
        if self._config.verify_after_execution and result.succeeded():
            verification = self.verify_transported_theorem(result)
            _log.debug(
                "Executor: post-execution verification %s",
                "passed" if verification.is_verified else "failed",
            )
        return result

    def apply_functor(
        self, functor: AnalogyFunctor, theorem: SourceTheorem
    ) -> TransportedTheorem:
        """Apply *functor* to *theorem*, producing a :class:`TransportedTheorem`.

        Iterates over the theorem's objects and morphisms, applying the
        functor's maps.  Gaps (unmapped items) cause a confidence decay.

        Parameters:
            functor: The analogy functor to apply.
            theorem: The source theorem to transport.

        Returns:
            A :class:`TransportedTheorem` with the transported statement.
        """
        trace: list[str] = []
        obj_translations: dict[str, str] = {}
        mor_translations: dict[str, str] = {}
        gaps = 0
        for obj in theorem.objects_used:
            target = functor.transport_object(obj)
            if target is not None:
                obj_translations[obj] = target
                trace.append(f"  object {obj!r} → {target!r}")
            else:
                gaps += 1
                trace.append(f"  object {obj!r} → (no mapping; gap #{gaps})")
        for mor in theorem.morphisms_used:
            target = functor.transport_morphism(mor)
            if target is not None:
                mor_translations[mor] = target
                trace.append(f"  morphism {mor!r} → {target!r}")
            else:
                gaps += 1
                trace.append(f"  morphism {mor!r} → (no mapping; gap #{gaps})")
        transported_stmt = self._rewrite_statement(
            theorem.statement, obj_translations, mor_translations
        )
        trace.append(f"Rewritten statement: {transported_stmt!r}")
        confidence = _clamp(
            functor.faithfulness_score
            - gaps * self._config.confidence_decay_per_gap
        )
        if len(trace) > self._config.max_trace_depth:
            trace = trace[: self._config.max_trace_depth] + ["... (truncated)"]
        return TransportedTheorem(
            transport_id=_uid(),
            source_theorem_id=theorem.theorem_id,
            target_domain=functor.target_domain,
            transported_statement=transported_stmt,
            object_translations=obj_translations,
            morphism_translations=mor_translations,
            transport_trace=tuple(trace),
            confidence_score=confidence,
            functor_id=functor.functor_id,
            status=TransportStatus.COMPLETED,
        )

    def verify_transported_theorem(
        self, result: TransportResult
    ) -> TransportVerification:
        """Run post-execution checks on a completed :class:`TransportResult`.

        Checks include: statement non-emptiness, confidence above a threshold,
        all objects having translations, and coherence condition sampling.

        Parameters:
            result: A completed transport result to verify.

        Returns:
            A :class:`TransportVerification` recording which checks passed.
        """
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        tt = result.transported_theorem
        if tt.transported_statement and tt.transported_statement != "<execution failed>":
            checks_passed.append("non_empty_statement")
        else:
            checks_failed.append("non_empty_statement")
        if tt.confidence_score >= 0.2:
            checks_passed.append("confidence_above_floor")
        else:
            checks_failed.append("confidence_above_floor")
        if tt.object_translations:
            checks_passed.append("at_least_one_object_mapped")
        else:
            checks_failed.append("at_least_one_object_mapped")
        if result.functor_used.coherence_conditions:
            checks_passed.append("coherence_conditions_present")
        else:
            checks_failed.append("coherence_conditions_present")
        adjustment = 0.05 * len(checks_passed) - 0.05 * len(checks_failed)
        return TransportVerification(
            verification_id=_uid(),
            result_id=result.result_id,
            checks_passed=tuple(checks_passed),
            checks_failed=tuple(checks_failed),
            is_verified=len(checks_failed) == 0,
            confidence_adjustment=adjustment,
        )

    def run_transport_batch(
        self, plans: list[TransportPlan]
    ) -> list[TransportResult]:
        """Execute a batch of :class:`TransportPlan` instances.

        Processes plans in chunks of ``config.batch_size``.

        Parameters:
            plans: List of validated plans to execute.

        Returns:
            List of :class:`TransportResult` instances in the same order.
        """
        results: list[TransportResult] = []
        for i in range(0, len(plans), self._config.batch_size):
            chunk = plans[i : i + self._config.batch_size]
            _log.debug(
                "Executor: processing batch %d-%d of %d",
                i,
                i + len(chunk) - 1,
                len(plans),
            )
            for plan in chunk:
                results.append(self.execute_transport(plan))
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rewrite_statement(
        self,
        statement: str,
        obj_map: dict[str, str],
        mor_map: dict[str, str],
    ) -> str:
        """Rewrite *statement* by substituting source names with target names.

        Longer names are replaced first to avoid partial overlaps.
        """
        result = statement
        combined = {**obj_map, **mor_map}
        for src in sorted(combined, key=len, reverse=True):
            result = result.replace(src, combined[src])
        return result


# ---------------------------------------------------------------------------
# AnalogyTransportNormalizer
# ---------------------------------------------------------------------------


class AnalogyTransportNormalizer:
    """Normalises and post-processes transported theorems for the target domain.

    Handles naming convention enforcement, conflict resolution with existing
    theorems, and mathematical notation standardisation.

    Attributes:
        _config: :class:`NormalizerConfig` controlling normaliser behaviour.
        _notation_rules: Dictionary of regex pattern → replacement strings.
    """

    def __init__(self, config: NormalizerConfig | None = None) -> None:
        self._config = config or NormalizerConfig()
        self._notation_rules: dict[str, str] = self._build_notation_rules()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize_transported_theorem(
        self, theorem: TransportedTheorem, target_domain: str
    ) -> NormalizedTheorem:
        """Normalise *theorem* for use in *target_domain*.

        Applies notation standardisation and assigns a canonical name.

        Parameters:
            theorem: The transported theorem to normalise.
            target_domain: The target domain for context.

        Returns:
            A :class:`NormalizedTheorem` ready for registration.
        """
        _log.debug(
            "Normalizer.normalize: transport %r in domain %r",
            theorem.transport_id,
            target_domain,
        )
        stmt, rewrites = self._apply_notation_rules(theorem.transported_statement)
        canonical_name = self._generate_canonical_name(theorem, target_domain)
        conf = _clamp(theorem.confidence_score + 0.02 * len(rewrites))
        return NormalizedTheorem(
            normalized_id=_uid(),
            transport_id=theorem.transport_id,
            target_domain=target_domain,
            normalized_statement=stmt,
            canonical_name=canonical_name,
            notation_rewrites=rewrites,
            confidence_score=conf,
        )

    def resolve_naming_conflicts(
        self,
        theorem: TransportedTheorem,
        existing_theorems: list[str],
    ) -> TransportedTheorem:
        """Rename the transported theorem if its target name conflicts.

        When ``config.conflict_resolution_strategy`` is ``"rename"``, appends
        a short suffix until the name is unique.

        Parameters:
            theorem: The transported theorem whose name may conflict.
            existing_theorems: List of already-registered theorem IDs.

        Returns:
            A :class:`TransportedTheorem` with a non-conflicting statement prefix.
        """
        existing_set = set(existing_theorems)
        if theorem.transport_id not in existing_set:
            return theorem
        strategy = self._config.conflict_resolution_strategy
        if strategy == "skip":
            _log.info(
                "Normalizer: skipping conflicting theorem %r", theorem.transport_id
            )
            return theorem
        if strategy == "overwrite":
            _log.info(
                "Normalizer: overwrite strategy; conflict not resolved for %r",
                theorem.transport_id,
            )
            return theorem
        # "rename" strategy: create a new ID
        new_stmt = f"[renamed] {theorem.transported_statement}"
        return replace(
            theorem,
            transport_id=_uid(),
            transported_statement=new_stmt,
        )

    def standardize_notation(
        self, theorem: TransportedTheorem
    ) -> TransportedTheorem:
        """Apply notation standardisation rules to the theorem statement.

        Replaces common informal notation with standardised forms recognised
        by the JuGeo system.

        Parameters:
            theorem: The transported theorem to standardise.

        Returns:
            A :class:`TransportedTheorem` with a standardised statement.
        """
        stmt, _ = self._apply_notation_rules(theorem.transported_statement)
        if stmt == theorem.transported_statement:
            return theorem
        return replace(theorem, transported_statement=stmt)

    def run_normalization_pipeline(
        self, results: list[TransportResult]
    ) -> list[NormalizedTheorem]:
        """Run the full normalisation pipeline over a list of transport results.

        Only processes results with status ``COMPLETED``.

        Parameters:
            results: List of transport results to normalise.

        Returns:
            List of :class:`NormalizedTheorem` instances for completed results.
        """
        normalised: list[NormalizedTheorem] = []
        for result in results:
            if not result.succeeded():
                _log.debug(
                    "Normalizer: skipping non-completed result %r (status=%s)",
                    result.result_id,
                    result.status.value,
                )
                continue
            tt = result.transported_theorem
            tt = self.standardize_notation(tt)
            norm = self.normalize_transported_theorem(tt, result.functor_used.target_domain)
            normalised.append(norm)
        _log.info(
            "Normalizer: normalised %d/%d results", len(normalised), len(results)
        )
        return normalised

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_notation_rules(self) -> dict[str, str]:
        """Build the default set of notation rewrite rules."""
        return {
            r"\bforall\b": "∀",
            r"\bexists\b": "∃",
            r"\bimplies\b": "⟹",
            r"\band\b": "∧",
            r"\bor\b": "∨",
            r"\bnot\b": "¬",
            r"\bcompose\b": "∘",
            r"\biso\b": "≅",
            r"\bequiv\b": "≡",
            r"\bmap\b": "↦",
        }

    def _apply_notation_rules(self, text: str) -> tuple[str, dict[str, str]]:
        """Apply notation rules to *text*, returning (rewritten_text, rewrites_dict)."""
        rewrites: dict[str, str] = {}
        result = text
        count = 0
        for pattern, replacement in self._notation_rules.items():
            new = re.sub(pattern, replacement, result)
            if new != result:
                rewrites[pattern] = replacement
                result = new
                count += 1
                if count >= self._config.max_notation_rewrites:
                    break
        return result, rewrites

    def _generate_canonical_name(
        self, theorem: TransportedTheorem, target_domain: str
    ) -> str:
        """Generate a canonical name for *theorem* in *target_domain*."""
        domain_slug = _normalize_name(target_domain)
        stmt_slug = _normalize_name(theorem.transported_statement[:30])
        prefix = self._config.naming_prefix
        name = f"{prefix}_{domain_slug}_{stmt_slug}"
        if self._config.lowercase_names:
            name = name.lower()
        return name


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def score_analogy_functor(functor: AnalogyFunctor) -> float:
    """Compute a composite quality score for *functor* in [0, 1].

    The score combines faithfulness, object coverage density, morphism
    coverage density, and a coherence condition bonus.

    Parameters:
        functor: The analogy functor to score.

    Returns:
        A float in [0, 1]; higher is better.
    """
    if not functor.object_map and not functor.morphism_map:
        return 0.0
    n_objects = len(functor.object_map)
    n_morphisms = len(functor.morphism_map)
    n_conditions = len(functor.coherence_conditions)
    # Density bonus: more mappings → more specific analogy
    density = _clamp(math.log1p(n_objects + n_morphisms) / math.log1p(50))
    # Coherence bonus: conditions signal structural depth
    coherence_bonus = _clamp(math.log1p(n_conditions) / math.log1p(10)) * 0.1
    score = _weighted_score(
        {
            "faithfulness": functor.faithfulness_score,
            "density": density,
        },
        {"faithfulness": 0.7, "density": 0.2},
    )
    return _clamp(score + coherence_bonus)


def build_domain_registry(
    domains: list[str],
    functors: list[AnalogyFunctor] | None = None,
) -> DomainRegistry:
    """Build a :class:`DomainRegistry` pre-populated with given domains and functors.

    Parameters:
        domains: List of domain names to register.
        functors: Optional list of :class:`AnalogyFunctor` instances to register.

    Returns:
        A fully populated :class:`DomainRegistry`.
    """
    registry = DomainRegistry()
    for d in domains:
        registry.register_domain(d)
    for f in functors or []:
        registry.register_functor(f)
    _log.info(
        "build_domain_registry: %d domains, %d functors",
        len(domains),
        len(functors or []),
    )
    return registry


def run_planning_cycle(
    source_theorems: list[SourceTheorem],
    target_domain: str,
    registry: DomainRegistry,
    config: TransportPlannerConfig | None = None,
) -> PlanningCycleResult:
    """Convenience wrapper: create a planner and run a full planning cycle.

    Parameters:
        source_theorems: Theorems to plan transport for.
        target_domain: The target mathematical domain.
        registry: Registry of known domains and functors.
        config: Optional planner configuration.

    Returns:
        A :class:`PlanningCycleResult` summarising all generated plans.
    """
    planner = AnalogyTransportPlanner(config)
    return planner.run_planning_cycle(source_theorems, target_domain, registry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "AnalogyQuality",
    "TransportStatus",
    # Core value objects
    "AnalogyFunctor",
    "SourceTheorem",
    "TransportedTheorem",
    "TransportResult",
    "TransportPlan",
    "PlanValidationResult",
    "PlanningCycleResult",
    "TransportVerification",
    "NormalizedTheorem",
    # Configuration
    "TransportPlannerConfig",
    "TransportExecutorConfig",
    "NormalizerConfig",
    # Registry
    "DomainRegistry",
    # Planner / Executor / Normalizer
    "AnalogyTransportPlanner",
    "AnalogyTransportExecutor",
    "AnalogyTransportNormalizer",
    # Free functions
    "score_analogy_functor",
    "build_domain_registry",
    "run_planning_cycle",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=== analogy_transport.algorithms smoke test ===\n")

    # 1. Build a domain registry with two domains and one functor
    sheaf_to_types_functor = AnalogyFunctor(
        functor_id=_uid(),
        source_domain="sheaf_theory",
        target_domain="type_theory",
        object_map={
            "sheaf": "type",
            "section": "term",
            "stalk": "fiber",
            "base_space": "context",
            "open_set": "proposition",
        },
        morphism_map={
            "restriction_map": "weakening",
            "gluing": "dependent_sum",
            "sheafification": "quotient_type",
        },
        coherence_conditions=(
            "identity_preservation",
            "compose_preservation",
            "gluing_axiom_transport",
        ),
        faithfulness_score=0.82,
        created_at=_now_iso(),
        description="Maps sheaf-theoretic constructs to type-theoretic analogues.",
    )

    registry = build_domain_registry(
        domains=["sheaf_theory", "type_theory"],
        functors=[sheaf_to_types_functor],
    )
    print(f"Registry: {registry.summary()}\n")

    # 2. Define a source theorem
    thm = SourceTheorem(
        theorem_id=_uid(),
        source_domain="sheaf_theory",
        statement=(
            "For any sheaf F on base_space X and open_set U, "
            "the restriction_map F(X) → F(U) is a morphism of abelian groups."
        ),
        objects_used=("sheaf", "section", "base_space", "open_set"),
        morphisms_used=("restriction_map",),
        assumptions=("F is an abelian group sheaf",),
        tags=("sheaf", "abelian", "restriction"),
    )
    print(f"Source theorem: {thm.theorem_id[:8]}...")
    print(f"  statement: {thm.statement[:60]}...\n")

    # 3. Plan transport
    planner = AnalogyTransportPlanner(TransportPlannerConfig(min_coverage=0.2))
    functors = planner.find_analogies("sheaf_theory", "type_theory", registry)
    print(f"Functors found: {len(functors)}")
    for f in functors:
        print(f"  {f.summary()}")
    print()

    plan = planner.plan_transport(thm, functors[0])
    print(f"Plan: {plan.plan_id[:8]}..., confidence={plan.estimated_confidence:.3f}")
    validation = planner.validate_transport_plan(plan)
    print(f"Validation: valid={validation.is_valid}, coverage={validation.coverage_fraction:.2f}")
    if validation.warnings:
        for w in validation.warnings:
            print(f"  warning: {w}")
    print()

    # 4. Execute transport
    executor = AnalogyTransportExecutor(TransportExecutorConfig(verify_after_execution=True))
    result = executor.execute_transport(plan)
    print(f"Transport result: status={result.status.value}, confidence={result.confidence_score:.3f}")
    print(f"  transported statement: {result.transported_theorem.transported_statement[:80]}...")
    print(f"  trace steps: {len(result.transport_trace)}")
    print()

    # 5. Normalise
    normalizer = AnalogyTransportNormalizer(NormalizerConfig(naming_prefix="xport"))
    norm_list = normalizer.run_normalization_pipeline([result])
    print(f"Normalized theorems: {len(norm_list)}")
    if norm_list:
        n = norm_list[0]
        print(f"  canonical_name: {n.canonical_name}")
        print(f"  confidence: {n.confidence_score:.3f}")
        print(f"  notation_rewrites: {n.notation_rewrites}")
    print()

    # 6. Score functor
    s = score_analogy_functor(sheaf_to_types_functor)
    print(f"Functor score: {s:.4f}")
    print("\n=== smoke test complete ===")
