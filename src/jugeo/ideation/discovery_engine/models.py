"""Core data models for the JuGeo discovery engine — theory2.tex Ch58.

This module defines the canonical data structures used throughout the
discovery pipeline: status enumerations, pipeline-stage enumerations,
candidate representations, configuration objects, results, diagnostics,
kind signatures, theorem candidates, promotion decisions, and per-stage
dataclasses.

Theory reference: theory2.tex Ch58 §3 — Discovery Pipeline Data Model.

copilot: shared-core marker

Module Layout
=============

The module is organised into five logical sections, in reading order:

1. **Helper utilities** — ``_utcnow()``, ``_uid()``, ``_clamp()``.
2. **Enumerations** — ``DiscoveryStatus``, ``PipelineStage``.
3. **Core value objects** — ``DiscoveryCandidate``, ``KindSignature``,
   ``TheoremCandidate``, ``PromotionDecision``.
4. **Configuration and diagnostics** — ``DiscoveryConfig``,
   ``DiscoveryDiagnostics``, ``DiscoveryResult``.
5. **Stage dataclasses** — ``NoveltyPipelineStage``,
   ``KindClassificationStage``, ``TheoremSynthesisStage``,
   ``PackPromotionStage``.

Dataclass Conventions
=====================

* Mutable dataclasses use ``@dataclasses.dataclass(slots=True)``.
* Immutable value objects use ``@dataclasses.dataclass(frozen=True, slots=True)``.
* Fields with mutable defaults use ``dataclasses.field(default_factory=…)``.
* ``__post_init__`` methods perform lightweight validation and normalisation.
* All dataclasses expose ``to_dict()`` for serialisation and ``from_dict()``
  classmethods for deserialisation where applicable.

Design Notes
============

``DiscoveryCandidate`` intentionally carries a ``trust_tier`` string rather
than importing the ``TrustTier`` enum from ``jugeo.evidence.trust`` directly.
This keeps the models module free of circular imports while still capturing
trust information.

``KindSignature.distance_to`` uses a symmetric Jaccard distance over the
union of characteristic-class label sets.  The label sets are converted to
``frozenset`` before comparison so that order does not affect the result.

``DiscoveryDiagnostics`` is mutable (not frozen) because stages accumulate
errors and timing information incrementally during execution.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import time
import uuid
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Guarded cross-module imports
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

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Helper utilities
    "_utcnow",
    "_uid",
    "_clamp",
    # Enumerations
    "DiscoveryStatus",
    "PipelineStage",
    # Core value objects
    "DiscoveryCandidate",
    "KindSignature",
    "TheoremCandidate",
    "PromotionDecision",
    # Configuration and diagnostics
    "DiscoveryConfig",
    "DiscoveryDiagnostics",
    "DiscoveryResult",
    # Stage dataclasses
    "NoveltyPipelineStage",
    "KindClassificationStage",
    "TheoremSynthesisStage",
    "PackPromotionStage",
]

# ---------------------------------------------------------------------------
# §1 Helper utilities
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return current UTC timestamp as a float (seconds since epoch).

    Uses :func:`time.time` which returns seconds since the Unix epoch in UTC.
    Prefer this helper over ``datetime.utcnow()`` to keep the models module
    free of ``datetime`` import overhead.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC).

    Example
    -------
    ::

        t = _utcnow()
        assert isinstance(t, float)
        assert t > 0
    """
    return time.time()


def _uid() -> str:
    """Generate a short unique identifier string.

    Wraps :func:`uuid.uuid4` to produce a RFC-4122 UUID string.  The string
    is hyphen-separated (e.g. ``"550e8400-e29b-41d4-a716-446655440000"``).

    Returns
    -------
    str
        UUID4 string.

    Example
    -------
    ::

        uid1 = _uid()
        uid2 = _uid()
        assert uid1 != uid2
        assert len(uid1) == 36
    """
    return str(uuid.uuid4())


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp a numeric value to a closed interval.

    Parameters
    ----------
    value:
        The value to clamp.
    lower:
        Lower bound of the interval (inclusive).  Defaults to ``0.0``.
    upper:
        Upper bound of the interval (inclusive).  Defaults to ``1.0``.

    Returns
    -------
    float
        ``value`` clamped to ``[lower, upper]``.

    Raises
    ------
    ValueError
        If ``lower > upper``.

    Example
    -------
    ::

        assert _clamp(1.5) == 1.0
        assert _clamp(-0.1) == 0.0
        assert _clamp(0.5) == 0.5
        assert _clamp(3.0, 0.0, 5.0) == 3.0
    """
    if lower > upper:
        raise ValueError(f"lower ({lower}) must not exceed upper ({upper})")
    return max(lower, min(upper, float(value)))


# ---------------------------------------------------------------------------
# §2 Enumerations
# ---------------------------------------------------------------------------


class DiscoveryStatus(str, Enum):
    """Lifecycle status of a discovery pipeline run or individual stage.

    Transitions
    -----------
    A pipeline run starts in :attr:`PENDING` and transitions to
    :attr:`RUNNING` when execution begins.  From :attr:`RUNNING` it may
    reach :attr:`COMPLETE` (success), :attr:`FAILED` (unrecoverable error),
    or :attr:`CANCELLED` (external interruption).

    Valid transition table::

        PENDING   -> RUNNING
        RUNNING   -> COMPLETE
        RUNNING   -> FAILED
        RUNNING   -> CANCELLED
        COMPLETE  -> (terminal)
        FAILED    -> (terminal)
        CANCELLED -> (terminal)

    A stage that has not yet been reached in a pipeline run retains
    :attr:`PENDING` status.  Once the pipeline reaches that stage,
    its status becomes :attr:`RUNNING`.

    Notes
    -----
    The enum inherits from ``str`` so that status values can be stored
    directly as JSON strings without an extra serialisation step.
    """

    PENDING = "PENDING"
    """The run or stage has not yet started."""

    RUNNING = "RUNNING"
    """The run or stage is currently executing."""

    COMPLETE = "COMPLETE"
    """The run or stage finished successfully."""

    FAILED = "FAILED"
    """The run or stage encountered an unrecoverable error."""

    CANCELLED = "CANCELLED"
    """The run or stage was externally interrupted before completion."""

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if this status represents a terminal state.

        Terminal states are :attr:`COMPLETE`, :attr:`FAILED`, and
        :attr:`CANCELLED`.  Once a run reaches a terminal state it will
        not transition further.

        Returns
        -------
        bool
            ``True`` for terminal statuses.
        """
        return self in (
            DiscoveryStatus.COMPLETE,
            DiscoveryStatus.FAILED,
            DiscoveryStatus.CANCELLED,
        )

    @property
    def is_active(self) -> bool:
        """Return ``True`` if this status represents an active (non-terminal) state.

        Active states are :attr:`PENDING` and :attr:`RUNNING`.

        Returns
        -------
        bool
            ``True`` for active statuses.
        """
        return not self.is_terminal

    def can_transition_to(self, other: DiscoveryStatus) -> bool:
        """Return whether a transition from this status to *other* is valid.

        Parameters
        ----------
        other:
            Target status.

        Returns
        -------
        bool
            ``True`` if the transition is permitted by the transition table.

        Example
        -------
        ::

            assert DiscoveryStatus.PENDING.can_transition_to(DiscoveryStatus.RUNNING)
            assert not DiscoveryStatus.COMPLETE.can_transition_to(DiscoveryStatus.RUNNING)
        """
        _allowed: dict[DiscoveryStatus, set[DiscoveryStatus]] = {
            DiscoveryStatus.PENDING: {DiscoveryStatus.RUNNING},
            DiscoveryStatus.RUNNING: {
                DiscoveryStatus.COMPLETE,
                DiscoveryStatus.FAILED,
                DiscoveryStatus.CANCELLED,
            },
            DiscoveryStatus.COMPLETE: set(),
            DiscoveryStatus.FAILED: set(),
            DiscoveryStatus.CANCELLED: set(),
        }
        return other in _allowed.get(self, set())


class PipelineStage(str, Enum):
    """Enumeration of the four pipeline stages in execution order.

    The stages are executed in the following fixed order:

    1. :attr:`NOVELTY` — filter and rank candidates by novelty score.
    2. :attr:`KIND_CLASSIFICATION` — assign a :class:`KindSignature` to each
       surviving candidate.
    3. :attr:`THEOREM_SYNTHESIS` — derive theorem candidates from classified
       kinds using bridge patterns.
    4. :attr:`PACK_PROMOTION` — promote high-confidence theorem candidates
       into the pack authority registry.

    Notes
    -----
    Inherits from ``str`` for direct JSON serialisation.
    """

    NOVELTY = "NOVELTY"
    """First stage: novelty filtering and ranking."""

    KIND_CLASSIFICATION = "KIND_CLASSIFICATION"
    """Second stage: kind-signature assignment."""

    THEOREM_SYNTHESIS = "THEOREM_SYNTHESIS"
    """Third stage: theorem candidate generation."""

    PACK_PROMOTION = "PACK_PROMOTION"
    """Fourth and final stage: promotion to pack registry."""

    # ------------------------------------------------------------------
    # Properties / helpers
    # ------------------------------------------------------------------

    @property
    def stage_order(self) -> int:
        """Return the zero-based execution order index of this stage.

        Returns
        -------
        int
            0 for :attr:`NOVELTY`, 1 for :attr:`KIND_CLASSIFICATION`,
            2 for :attr:`THEOREM_SYNTHESIS`, 3 for :attr:`PACK_PROMOTION`.
        """
        _order = {
            PipelineStage.NOVELTY: 0,
            PipelineStage.KIND_CLASSIFICATION: 1,
            PipelineStage.THEOREM_SYNTHESIS: 2,
            PipelineStage.PACK_PROMOTION: 3,
        }
        return _order[self]

    @property
    def is_final(self) -> bool:
        """Return ``True`` if this is the last stage in the pipeline.

        Returns
        -------
        bool
            ``True`` only for :attr:`PACK_PROMOTION`.
        """
        return self is PipelineStage.PACK_PROMOTION

    def next_stage(self) -> PipelineStage | None:
        """Return the next stage after this one, or ``None`` if final.

        Returns
        -------
        PipelineStage | None
            The successor stage, or ``None`` for :attr:`PACK_PROMOTION`.

        Example
        -------
        ::

            s = PipelineStage.NOVELTY.next_stage()
            assert s is PipelineStage.KIND_CLASSIFICATION
            assert PipelineStage.PACK_PROMOTION.next_stage() is None
        """
        _next: dict[PipelineStage, PipelineStage | None] = {
            PipelineStage.NOVELTY: PipelineStage.KIND_CLASSIFICATION,
            PipelineStage.KIND_CLASSIFICATION: PipelineStage.THEOREM_SYNTHESIS,
            PipelineStage.THEOREM_SYNTHESIS: PipelineStage.PACK_PROMOTION,
            PipelineStage.PACK_PROMOTION: None,
        }
        return _next[self]


# ---------------------------------------------------------------------------
# §3 Core value objects
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """An immutable representation of a single discovery candidate.

    A discovery candidate is a mathematical object — a conjecture, an
    observed pattern, a geometric fact, or a computational result — that
    has been nominated for automated discovery processing.

    Each candidate carries a :attr:`novelty_score` in ``[0, 1]`` that
    quantifies how different the candidate is from the existing corpus
    (higher = more novel).  It also carries a :attr:`trust_tier` string
    that records the provenance trust level at submission time.

    Parameters
    ----------
    id:
        Unique identifier for this candidate.  Should be a UUID string.
    domain:
        Mathematical domain label, e.g. ``"topology"``, ``"algebra"``.
    description:
        Human-readable description of the candidate.
    novelty_score:
        Novelty score in ``[0, 1]``.  Values below the configured threshold
        are filtered out during the novelty stage.
    trust_tier:
        Trust tier string, e.g. ``"provisional"``, ``"established"``.
    metadata:
        Arbitrary key-value metadata attached at submission.

    Raises
    ------
    ValueError
        If ``novelty_score`` is outside ``[0, 1]``.

    Example
    -------
    ::

        c = DiscoveryCandidate(
            id="abc-123",
            domain="topology",
            description="Compact surface with genus 2",
            novelty_score=0.75,
            trust_tier="provisional",
        )
        assert c.novelty_score == 0.75
        assert "topology" in c.token_set()
    """

    id: str
    domain: str
    description: str
    novelty_score: float
    trust_tier: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate that *novelty_score* lies in ``[0, 1]``."""
        if not (0.0 <= self.novelty_score <= 1.0):
            raise ValueError(
                f"novelty_score must be in [0, 1]; got {self.novelty_score!r}"
            )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def token_set(self) -> frozenset[str]:
        """Return a frozenset of normalised tokens from domain and description.

        Tokens are produced by splitting :attr:`domain` and :attr:`description`
        on whitespace, stripping punctuation, and lower-casing each token.
        This token set is used for Jaccard-based similarity computation.

        Returns
        -------
        frozenset[str]
            Token set.
        """
        import re
        tokens: set[str] = set()
        for text in (self.domain, self.description):
            for tok in re.split(r"\W+", text.lower()):
                if tok:
                    tokens.add(tok)
        return frozenset(tokens)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def similarity_to(self, other: DiscoveryCandidate) -> float:
        """Compute Jaccard similarity between this candidate and *other*.

        The similarity is computed over the :attr:`token_set` of each
        candidate::

            similarity = |A ∩ B| / |A ∪ B|

        Returns ``0.0`` if both token sets are empty.

        Parameters
        ----------
        other:
            The other candidate.

        Returns
        -------
        float
            Jaccard similarity in ``[0, 1]``.
        """
        a = self.token_set
        b = other.token_set
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    def with_novelty_score(self, score: float) -> DiscoveryCandidate:
        """Return a new candidate identical to this one but with *score*.

        Because :class:`DiscoveryCandidate` is frozen, this method creates
        and returns a new instance via :func:`dataclasses.replace`.

        Parameters
        ----------
        score:
            Replacement novelty score in ``[0, 1]``.

        Returns
        -------
        DiscoveryCandidate
            New candidate with updated novelty score.
        """
        return dataclasses.replace(self, novelty_score=_clamp(score))

    def to_dict(self) -> dict[str, Any]:
        """Serialise this candidate to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys matching field names.
        """
        return {
            "id": self.id,
            "domain": self.domain,
            "description": self.description,
            "novelty_score": self.novelty_score,
            "trust_tier": self.trust_tier,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryCandidate:
        """Deserialise a candidate from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        DiscoveryCandidate
            Reconstructed candidate.

        Raises
        ------
        KeyError
            If a required field is missing from *data*.
        """
        return cls(
            id=data["id"],
            domain=data["domain"],
            description=data["description"],
            novelty_score=float(data["novelty_score"]),
            trust_tier=data["trust_tier"],
            metadata=dict(data.get("metadata", {})),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class KindSignature:
    """Immutable signature describing the abstract mathematical kind of a candidate.

    A kind signature captures two orthogonal pieces of information:

    * **Dimension labels** — named dimensions of the mathematical object
      (e.g. ``("cohomological_degree", "filtration_index")``).
    * **Characteristic classes** — characteristic-class identifiers
      (e.g. ``("chern_1", "pontryagin_2")``).

    Kind signatures are used in the :class:`KindClassificationStage` to
    group related candidates and in the :class:`TheoremSynthesisStage` to
    select applicable bridge patterns.

    The :meth:`distance_to` method computes a symmetric Jaccard distance
    over the union of characteristic-class sets.  Two signatures with
    identical characteristic-class sets have distance ``0.0``; disjoint sets
    have distance ``1.0``.

    Parameters
    ----------
    kind_id:
        Unique string identifier for this kind, e.g. ``"oriented_surface_g2"``.
    dimension_labels:
        Tuple of dimension label strings.  May be empty.
    characteristic_classes:
        Tuple of characteristic-class identifier strings.  May be empty.

    Example
    -------
    ::

        ks = KindSignature(
            kind_id="surface",
            dimension_labels=("genus",),
            characteristic_classes=("euler_class",),
        )
        assert ks.arity == 1
    """

    kind_id: str
    dimension_labels: tuple[str, ...] = ()
    characteristic_classes: tuple[str, ...] = ()

    @property
    def arity(self) -> int:
        """Return the number of dimension labels.

        Returns
        -------
        int
            ``len(self.dimension_labels)``.
        """
        return len(self.dimension_labels)

    def matches(self, other: KindSignature) -> bool:
        """Return ``True`` if this signature is compatible with *other*.

        Two signatures match if their characteristic-class sets intersect
        (i.e. they share at least one characteristic class), OR if both
        have empty characteristic-class sets.

        Parameters
        ----------
        other:
            The signature to compare against.

        Returns
        -------
        bool
            ``True`` if the signatures are compatible.
        """
        self_cc = frozenset(self.characteristic_classes)
        other_cc = frozenset(other.characteristic_classes)
        if not self_cc and not other_cc:
            return True
        return bool(self_cc & other_cc)

    def distance_to(self, other: KindSignature) -> float:
        """Compute symmetric Jaccard distance between characteristic-class sets.

        The Jaccard distance is::

            d(A, B) = 1 - |A ∩ B| / |A ∪ B|

        Returns ``0.0`` if both sets are empty (identical empty signatures).

        Parameters
        ----------
        other:
            The other kind signature.

        Returns
        -------
        float
            Jaccard distance in ``[0, 1]``.
        """
        a = frozenset(self.characteristic_classes)
        b = frozenset(other.characteristic_classes)
        union = a | b
        if not union:
            return 0.0
        intersection = a & b
        return 1.0 - len(intersection) / len(union)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised representation.
        """
        return {
            "kind_id": self.kind_id,
            "dimension_labels": list(self.dimension_labels),
            "characteristic_classes": list(self.characteristic_classes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KindSignature:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        KindSignature
            Reconstructed kind signature.
        """
        return cls(
            kind_id=data["kind_id"],
            dimension_labels=tuple(data.get("dimension_labels", [])),
            characteristic_classes=tuple(data.get("characteristic_classes", [])),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class TheoremCandidate:
    """An immutable theorem candidate produced by the synthesis stage.

    A theorem candidate encodes a conjectured mathematical statement together
    with a proof sketch and a confidence score.  It is produced by the
    :class:`TheoremSynthesisStage` and consumed by the
    :class:`PackPromotionStage`.

    Only candidates whose :attr:`confidence` meets or exceeds
    ``DiscoveryConfig.promotion_threshold`` are promoted to the pack registry.

    Parameters
    ----------
    id:
        Unique identifier.
    statement:
        The theorem statement in human-readable form.
    proof_sketch:
        A brief outline of the intended proof strategy.
    confidence:
        Confidence score in ``[0, 1]``.
    supporting_candidates:
        Tuple of :class:`DiscoveryCandidate` IDs that support this theorem.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0, 1]``.

    Example
    -------
    ::

        tc = TheoremCandidate(
            id="thm-001",
            statement="Every compact surface admits a CW-structure.",
            proof_sketch="Triangulate and collapse.",
            confidence=0.92,
        )
        assert tc.is_high_confidence
    """

    id: str
    statement: str
    proof_sketch: str
    confidence: float
    supporting_candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate that *confidence* lies in ``[0, 1]``."""
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1]; got {self.confidence!r}"
            )

    @property
    def is_high_confidence(self) -> bool:
        """Return ``True`` if ``confidence >= 0.8``.

        Returns
        -------
        bool
            High-confidence flag.
        """
        return self.confidence >= 0.8

    @property
    def token_set(self) -> frozenset[str]:
        """Return a frozenset of tokens from statement and proof sketch.

        Returns
        -------
        frozenset[str]
            Normalised token set.
        """
        import re
        tokens: set[str] = set()
        for text in (self.statement, self.proof_sketch):
            for tok in re.split(r"\W+", text.lower()):
                if tok:
                    tokens.add(tok)
        return frozenset(tokens)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised representation.
        """
        return {
            "id": self.id,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "confidence": self.confidence,
            "supporting_candidates": list(self.supporting_candidates),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Immutable record of a pack-promotion decision for one theorem candidate.

    A :class:`PromotionDecision` is produced by the :class:`PackPromotionStage`
    for each :class:`TheoremCandidate` evaluated.  It records whether the
    candidate was approved for promotion, the reason for the decision, and
    (if approved) the authority grant token and pack descriptor reference.

    Parameters
    ----------
    candidate_id:
        ID of the :class:`TheoremCandidate` this decision applies to.
    approved:
        Whether the candidate is approved for promotion.
    reason:
        Human-readable rationale.
    authority_grant:
        Opaque authority grant token issued by the pack authority, or
        ``None`` if not approved.
    pack_descriptor:
        Pack descriptor identifier, or ``None`` if not approved.

    Example
    -------
    ::

        d = PromotionDecision(
            candidate_id="thm-001",
            approved=True,
            reason="Confidence 0.92 >= threshold 0.7",
            authority_grant="grant-xyz",
            pack_descriptor="pd-abc",
        )
        assert d.is_approved
    """

    candidate_id: str
    approved: bool
    reason: str
    authority_grant: str | None = None
    pack_descriptor: str | None = None

    @property
    def is_approved(self) -> bool:
        """Return ``True`` if this decision approves promotion.

        Returns
        -------
        bool
            Value of :attr:`approved`.
        """
        return self.approved

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised representation.
        """
        return {
            "candidate_id": self.candidate_id,
            "approved": self.approved,
            "reason": self.reason,
            "authority_grant": self.authority_grant,
            "pack_descriptor": self.pack_descriptor,
        }


# ---------------------------------------------------------------------------
# §4 Configuration and diagnostics
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class DiscoveryConfig:
    """Mutable configuration object for a discovery pipeline run.

    All pipeline stages read their parameters from a single
    :class:`DiscoveryConfig` instance.  The config object is mutable so
    that it can be progressively constructed before being passed to the
    pipeline.

    Parameters
    ----------
    max_candidates:
        Maximum number of candidates accepted at pipeline entry.
    novelty_threshold:
        Minimum novelty score for a candidate to survive the novelty stage.
    synthesis_budget:
        Maximum number of theorem candidates to generate per run.
    promotion_threshold:
        Minimum confidence for a theorem candidate to be promoted.
    pipeline_timeout_secs:
        Wall-clock timeout for the entire pipeline in seconds.
    enable_deduplication:
        Whether to deduplicate candidates by similarity before classification.
    min_trust_tier:
        Minimum trust tier accepted at pipeline entry.

    Example
    -------
    ::

        cfg = DiscoveryConfig(novelty_threshold=0.5, synthesis_budget=20)
        errors = cfg.validate()
        assert errors == []
    """

    max_candidates: int = 100
    novelty_threshold: float = 0.3
    synthesis_budget: int = 50
    promotion_threshold: float = 0.7
    pipeline_timeout_secs: float = 300.0
    enable_deduplication: bool = True
    min_trust_tier: str = "provisional"

    def validate(self) -> list[str]:
        """Validate field values and return a list of error messages.

        An empty list means the configuration is valid and may be used
        to drive a pipeline run.

        Returns
        -------
        list[str]
            Validation errors.  Empty if the configuration is valid.
        """
        errors: list[str] = []
        if self.max_candidates < 1:
            errors.append(f"max_candidates must be >= 1; got {self.max_candidates}")
        if not (0.0 <= self.novelty_threshold <= 1.0):
            errors.append(
                f"novelty_threshold must be in [0, 1]; got {self.novelty_threshold}"
            )
        if self.synthesis_budget < 0:
            errors.append(
                f"synthesis_budget must be >= 0; got {self.synthesis_budget}"
            )
        if not (0.0 <= self.promotion_threshold <= 1.0):
            errors.append(
                f"promotion_threshold must be in [0, 1]; got {self.promotion_threshold}"
            )
        if self.pipeline_timeout_secs <= 0.0:
            errors.append(
                f"pipeline_timeout_secs must be > 0; got {self.pipeline_timeout_secs}"
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised configuration.
        """
        return {
            "max_candidates": self.max_candidates,
            "novelty_threshold": self.novelty_threshold,
            "synthesis_budget": self.synthesis_budget,
            "promotion_threshold": self.promotion_threshold,
            "pipeline_timeout_secs": self.pipeline_timeout_secs,
            "enable_deduplication": self.enable_deduplication,
            "min_trust_tier": self.min_trust_tier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryConfig:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        DiscoveryConfig
            Reconstructed configuration.
        """
        return cls(
            max_candidates=int(data.get("max_candidates", 100)),
            novelty_threshold=float(data.get("novelty_threshold", 0.3)),
            synthesis_budget=int(data.get("synthesis_budget", 50)),
            promotion_threshold=float(data.get("promotion_threshold", 0.7)),
            pipeline_timeout_secs=float(data.get("pipeline_timeout_secs", 300.0)),
            enable_deduplication=bool(data.get("enable_deduplication", True)),
            min_trust_tier=str(data.get("min_trust_tier", "provisional")),
        )

    def with_novelty_threshold(self, t: float) -> DiscoveryConfig:
        """Return a new config with *novelty_threshold* set to *t*.

        This method does **not** mutate ``self``; it creates and returns a
        new :class:`DiscoveryConfig` instance.

        Parameters
        ----------
        t:
            New novelty threshold in ``[0, 1]``.

        Returns
        -------
        DiscoveryConfig
            New config with updated threshold.
        """
        d = self.to_dict()
        d["novelty_threshold"] = _clamp(t)
        return DiscoveryConfig.from_dict(d)


@dataclasses.dataclass(slots=True)
class DiscoveryDiagnostics:
    """Mutable container for diagnostics accumulated during a pipeline run.

    Stages append errors, warnings, and timing information to a shared
    :class:`DiscoveryDiagnostics` instance.  After the run completes, the
    diagnostics object can be inspected or forwarded to monitoring
    infrastructure.

    Parameters
    ----------
    stage_times:
        Mapping from stage name to elapsed wall-clock time in seconds.
    errors:
        List of error messages accumulated during the run.
    warnings:
        List of warning messages accumulated during the run.
    telemetry:
        Arbitrary telemetry key-value pairs.

    Example
    -------
    ::

        diag = DiscoveryDiagnostics()
        diag.record_stage_time("NOVELTY", 0.12)
        diag.add_warning("Low novelty candidate count after filtering")
        assert diag.total_elapsed == 0.12
        assert not diag.has_errors
    """

    stage_times: dict[str, float] = dataclasses.field(default_factory=dict)
    errors: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    telemetry: dict[str, Any] = dataclasses.field(default_factory=dict)

    def record_stage_time(self, stage: str, elapsed: float) -> None:
        """Record the elapsed time for *stage*.

        Parameters
        ----------
        stage:
            Stage name string (e.g. ``"NOVELTY"``).
        elapsed:
            Elapsed wall-clock time in seconds.
        """
        self.stage_times[stage] = float(elapsed)

    def add_error(self, msg: str) -> None:
        """Append an error message.

        Parameters
        ----------
        msg:
            Error message string.
        """
        self.errors.append(str(msg))

    def add_warning(self, msg: str) -> None:
        """Append a warning message.

        Parameters
        ----------
        msg:
            Warning message string.
        """
        self.warnings.append(str(msg))

    @property
    def total_elapsed(self) -> float:
        """Return the sum of all recorded stage times.

        Returns
        -------
        float
            Total elapsed time in seconds.
        """
        return sum(self.stage_times.values())

    @property
    def has_errors(self) -> bool:
        """Return ``True`` if any errors have been recorded.

        Returns
        -------
        bool
            Error-present flag.
        """
        return len(self.errors) > 0

    def summary(self) -> str:
        """Return a human-readable summary of the diagnostics.

        Returns
        -------
        str
            Multi-line summary string.
        """
        lines = [
            f"DiscoveryDiagnostics summary:",
            f"  total_elapsed : {self.total_elapsed:.3f}s",
            f"  errors        : {len(self.errors)}",
            f"  warnings      : {len(self.warnings)}",
        ]
        for stage, t in self.stage_times.items():
            lines.append(f"    {stage}: {t:.3f}s")
        if self.errors:
            lines.append("  Error details:")
            for e in self.errors:
                lines.append(f"    - {e}")
        if self.warnings:
            lines.append("  Warning details:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Immutable result of processing a single discovery candidate through the pipeline.

    :class:`DiscoveryResult` is the top-level output produced for each
    candidate that survives to the end of the pipeline.  It summarises
    how many theorems were synthesised and promoted, and captures timing
    and diagnostics information.

    Parameters
    ----------
    candidate:
        The :class:`DiscoveryCandidate` that was processed.
    status:
        Final :class:`DiscoveryStatus` for this candidate.
    theorems_synthesized:
        Number of theorem candidates generated for this candidate.
    pack_promotions:
        Number of those theorem candidates successfully promoted.
    elapsed_secs:
        Wall-clock time consumed processing this candidate.
    diagnostics:
        Optional :class:`DiscoveryDiagnostics` for this candidate.

    Example
    -------
    ::

        result = DiscoveryResult(
            candidate=cand,
            status=DiscoveryStatus.COMPLETE,
            theorems_synthesized=3,
            pack_promotions=2,
        )
        assert result.is_successful
    """

    candidate: DiscoveryCandidate
    status: DiscoveryStatus
    theorems_synthesized: int = 0
    pack_promotions: int = 0
    elapsed_secs: float = 0.0
    diagnostics: DiscoveryDiagnostics | None = None

    @property
    def is_successful(self) -> bool:
        """Return ``True`` if ``status is COMPLETE``.

        Returns
        -------
        bool
            Success flag.
        """
        return self.status is DiscoveryStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised result.
        """
        return {
            "candidate": self.candidate.to_dict(),
            "status": self.status.value,
            "theorems_synthesized": self.theorems_synthesized,
            "pack_promotions": self.pack_promotions,
            "elapsed_secs": self.elapsed_secs,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"DiscoveryResult(id={self.candidate.id!r},"
            f" status={self.status.value},"
            f" theorems={self.theorems_synthesized},"
            f" promotions={self.pack_promotions},"
            f" elapsed={self.elapsed_secs:.3f}s)"
        )


# ---------------------------------------------------------------------------
# §5 Stage dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class NoveltyPipelineStage:
    """Mutable state container for the novelty-filtering stage.

    The novelty stage receives a list of raw :class:`DiscoveryCandidate`
    objects, filters out those whose :attr:`~DiscoveryCandidate.novelty_score`
    is below :attr:`threshold_used`, and ranks survivors in descending order
    of novelty score.

    Parameters
    ----------
    input_candidates:
        All candidates submitted to this stage.
    filtered_candidates:
        Candidates surviving the threshold filter.
    ranked_candidates:
        Filtered candidates sorted by descending novelty score.
    threshold_used:
        The novelty threshold that was applied.
    status:
        Execution status of this stage.

    Example
    -------
    ::

        stage = NoveltyPipelineStage(input_candidates=candidates)
        result = stage.run(config)
        print(result.candidate_count)
    """

    input_candidates: list[DiscoveryCandidate] = dataclasses.field(
        default_factory=list
    )
    filtered_candidates: list[DiscoveryCandidate] = dataclasses.field(
        default_factory=list
    )
    ranked_candidates: list[DiscoveryCandidate] = dataclasses.field(
        default_factory=list
    )
    threshold_used: float = 0.3
    status: DiscoveryStatus = DiscoveryStatus.PENDING

    @property
    def candidate_count(self) -> int:
        """Return the number of candidates that survived filtering.

        Returns
        -------
        int
            ``len(self.filtered_candidates)``.
        """
        return len(self.filtered_candidates)

    @property
    def filter_ratio(self) -> float:
        """Return the fraction of input candidates that survived filtering.

        Returns ``0.0`` if no input candidates are present.

        Returns
        -------
        float
            Survival ratio in ``[0, 1]``.
        """
        if not self.input_candidates:
            return 0.0
        return len(self.filtered_candidates) / len(self.input_candidates)

    def run(self, config: DiscoveryConfig) -> NoveltyPipelineStage:
        """Execute the novelty stage using *config*.

        Filters :attr:`input_candidates` by
        ``config.novelty_threshold`` and ranks survivors by descending
        novelty score.  Mutates ``self`` in place and returns ``self``.

        Parameters
        ----------
        config:
            Active pipeline configuration.

        Returns
        -------
        NoveltyPipelineStage
            ``self`` after execution.
        """
        self.status = DiscoveryStatus.RUNNING
        self.threshold_used = config.novelty_threshold
        try:
            self.filtered_candidates = [
                c for c in self.input_candidates
                if c.novelty_score >= config.novelty_threshold
            ]
            self.ranked_candidates = sorted(
                self.filtered_candidates,
                key=lambda c: c.novelty_score,
                reverse=True,
            )[: config.max_candidates]
            self.status = DiscoveryStatus.COMPLETE
        except Exception as exc:  # noqa: BLE001
            self.status = DiscoveryStatus.FAILED
            raise RuntimeError(f"NoveltyPipelineStage failed: {exc}") from exc
        return self


@dataclasses.dataclass(slots=True)
class KindClassificationStage:
    """Mutable state container for the kind-classification stage.

    Each candidate surviving the novelty stage is assigned a
    :class:`KindSignature` based on its domain and description tokens.

    Parameters
    ----------
    candidates:
        Candidates passed from the novelty stage.
    kind_assignments:
        Mapping from candidate ID to its assigned :class:`KindSignature`.
    unclassified:
        List of candidate IDs that could not be classified.
    status:
        Execution status of this stage.

    Example
    -------
    ::

        stage = KindClassificationStage(candidates=ranked)
        print(stage.classification_rate)
    """

    candidates: list[DiscoveryCandidate]
    kind_assignments: dict[str, KindSignature] = dataclasses.field(
        default_factory=dict
    )
    unclassified: list[str] = dataclasses.field(default_factory=list)
    status: DiscoveryStatus = DiscoveryStatus.PENDING

    @property
    def classification_rate(self) -> float:
        """Return the fraction of candidates that were successfully classified.

        Returns ``0.0`` if no candidates are present.

        Returns
        -------
        float
            Classification rate in ``[0, 1]``.
        """
        if not self.candidates:
            return 0.0
        return len(self.kind_assignments) / len(self.candidates)

    def assigned_kinds(self) -> set[str]:
        """Return the set of unique kind IDs assigned so far.

        Returns
        -------
        set[str]
            Set of :attr:`KindSignature.kind_id` strings.
        """
        return {sig.kind_id for sig in self.kind_assignments.values()}


@dataclasses.dataclass(slots=True)
class TheoremSynthesisStage:
    """Mutable state container for the theorem-synthesis stage.

    The synthesis stage generates :class:`TheoremCandidate` objects by
    applying bridge patterns to classified candidates.

    Parameters
    ----------
    classified_candidates:
        Candidates from the classification stage.
    kind_assignments:
        Kind assignments from the classification stage.
    theorem_candidates:
        Theorem candidates generated by this stage.
    status:
        Execution status of this stage.

    Example
    -------
    ::

        stage = TheoremSynthesisStage(
            classified_candidates=classified,
            kind_assignments=assignments,
        )
        high = stage.high_confidence_theorems()
    """

    classified_candidates: list[DiscoveryCandidate]
    kind_assignments: dict[str, KindSignature]
    theorem_candidates: list[TheoremCandidate] = dataclasses.field(
        default_factory=list
    )
    status: DiscoveryStatus = DiscoveryStatus.PENDING

    @property
    def theorems_per_candidate(self) -> float:
        """Return average theorem candidates generated per classified candidate.

        Returns ``0.0`` if no classified candidates are present.

        Returns
        -------
        float
            Average theorem count.
        """
        if not self.classified_candidates:
            return 0.0
        return len(self.theorem_candidates) / len(self.classified_candidates)

    def high_confidence_theorems(self) -> list[TheoremCandidate]:
        """Return only high-confidence theorem candidates (confidence >= 0.8).

        Returns
        -------
        list[TheoremCandidate]
            Filtered list.
        """
        return [tc for tc in self.theorem_candidates if tc.is_high_confidence]


@dataclasses.dataclass(slots=True)
class PackPromotionStage:
    """Mutable state container for the pack-promotion stage.

    The promotion stage evaluates each :class:`TheoremCandidate` against
    the configured ``promotion_threshold`` and produces a
    :class:`PromotionDecision` for each.

    Parameters
    ----------
    theorem_candidates:
        Theorem candidates from the synthesis stage.
    decisions:
        Promotion decisions produced by this stage.
    status:
        Execution status of this stage.

    Example
    -------
    ::

        stage = PackPromotionStage(theorem_candidates=theorems)
        print(stage.approved_count)
    """

    theorem_candidates: list[TheoremCandidate]
    decisions: list[PromotionDecision] = dataclasses.field(default_factory=list)
    status: DiscoveryStatus = DiscoveryStatus.PENDING

    @property
    def approved_count(self) -> int:
        """Return the number of approved promotion decisions.

        Returns
        -------
        int
            Count of approved decisions.
        """
        return sum(1 for d in self.decisions if d.is_approved)

    @property
    def promotion_rate(self) -> float:
        """Return fraction of theorem candidates that were approved.

        Returns ``0.0`` if no theorem candidates are present.

        Returns
        -------
        float
            Promotion rate in ``[0, 1]``.
        """
        if not self.theorem_candidates:
            return 0.0
        return self.approved_count / len(self.theorem_candidates)

    def approved_decisions(self) -> list[PromotionDecision]:
        """Return only approved :class:`PromotionDecision` objects.

        Returns
        -------
        list[PromotionDecision]
            Approved decisions.
        """
        return [d for d in self.decisions if d.is_approved]
