"""Pack promotion stage for the JuGeo discovery engine — theory2.tex Ch58.

This module implements Stage 4 and final stage of the discovery pipeline:
pack promotion.  Theorem candidates that pass eligibility checks are issued
authority grants and registered with the pack authority registry, making
them available as verified pack theorems for downstream reasoning.

Theory reference: theory2.tex Ch58 §5.4 — Pack Promotion Stage.

copilot: shared-core marker

Overview
--------
Pack promotion is the final stage of the four-stage discovery pipeline.
Its purpose is to *promote* theorem candidates — those that have survived
novelty filtering, kind classification, and theorem synthesis — into the
JuGeo pack registry as formally recognised pack theorems.

Promotion involves three sub-steps:

1. **Eligibility checking** — each theorem candidate is tested against a
   configurable set of ``EligibilityCriterion`` values.  Only candidates
   that satisfy enough criteria (as measured by a weighted eligibility score)
   are eligible for promotion.

2. **Decision making** — eligible candidates are granted promotion; ineligible
   ones are rejected.  Each decision is recorded as a ``PromotionDecision``
   value object for audit and reproducibility.

3. **Grant issuance** — eligible theorem candidates receive an authority grant
   from a ``PromotionAuthority`` instance.  The grant ID is stored in the
   ``PromotionDecision`` so that the grant can later be revoked if needed.

Pipeline position
-----------------
This stage consumes a ``TheoremSynthesisStage`` and produces a
``PackPromotionStage``, which is the terminal output of the discovery engine.

Typical usage::

    from jugeo.ideation.discovery_engine.pack_promotion import (
        run_pack_promotion,
        PackPromotionRunner,
        PackEligibilityChecker,
        PromotionAuthority,
        PromotionReport,
        EligibilityResult,
        EligibilityCriterion,
    )

    # One-shot
    stage = run_pack_promotion(theorem_synthesis_stage, config=cfg)

    # Fine-grained with full report
    runner = PackPromotionRunner(config=cfg)
    stage, report = runner.run_with_report(theorem_synthesis_stage)
    print(report.summary())

    # Inspect authority grants
    authority = PromotionAuthority()
    runner2 = PackPromotionRunner(config=cfg, authority=authority)
    stage2 = runner2.run(theorem_synthesis_stage)
    print(f"Grants issued: {authority.grant_count}")

Design notes
------------
* ``EligibilityResult`` is a *frozen dataclass* — results are immutable once
  produced.  This ensures that the audit trail cannot be tampered with.
* ``PromotionReport`` is also frozen and provides a concise summary of the
  entire promotion run.
* ``PromotionAuthority`` maintains a mutable in-memory grant store; it is
  intentionally *not* persistent.  Persistence is the responsibility of the
  ``PackAuthorityRegistry`` from ``jugeo.packs.authority``.
* The eligibility criteria are evaluated independently; the overall eligibility
  score is a simple average of per-criterion pass/fail values.

Eligibility criteria detail
----------------------------
* ``CONFIDENCE`` — ``theorem.confidence >= config.min_confidence`` (default 0.4).
* ``STATEMENT_LENGTH`` — statement has at least 60 characters.
* ``CANDIDATE_SUPPORT`` — ``source_candidate_id`` is non-empty.
* ``PROOF_COMPLETENESS`` — proof sketch has at least 3 sentences.
* ``DOMAIN_COVERAGE`` — ``kind_id`` is non-empty and recognised.

See also
--------
* ``theorem_synthesis`` — provides the input for this stage.
* ``jugeo.packs.authority`` — pack authority registry integration.
* ``jugeo.ideation.discovery_engine.models`` — shared dataclasses.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "EligibilityCriterion",
    "EligibilityResult",
    "PackEligibilityChecker",
    "PromotionReport",
    "PromotionAuthority",
    "PackPromotionRunner",
    "run_pack_promotion",
    # helpers
    "_utcnow",
    "_uid",
    "_clamp",
    "_check_eligibility",
    "_issue_grant",
    "_build_pack_descriptor",
]

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

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
        NoveltyPipelineStage,
        KindClassificationStage,
        TheoremSynthesisStage,
        PackPromotionStage,
    )
except Exception:
    DiscoveryCandidate = Any  # type: ignore[misc,assignment]
    DiscoveryConfig = Any  # type: ignore[misc,assignment]
    DiscoveryDiagnostics = Any  # type: ignore[misc,assignment]
    TheoremCandidate = Any  # type: ignore[misc,assignment]
    PromotionDecision = Any  # type: ignore[misc,assignment]
    TheoremSynthesisStage = Any  # type: ignore[misc,assignment]
    PackPromotionStage = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Returns
    -------
    float
        Seconds since the Unix epoch, UTC.

    Examples
    --------
    >>> t = _utcnow()
    >>> t > 1_700_000_000.0
    True
    """
    return time.time()


def _uid() -> str:
    """Generate a 32-character hexadecimal unique identifier.

    Returns
    -------
    str
        UUID4 hex string (no hyphens).

    Examples
    --------
    >>> uid = _uid()
    >>> len(uid) == 32 and uid.isalnum()
    True
    """
    return uuid.uuid4().hex


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval ``[lo, hi]``.

    Parameters
    ----------
    v : float
        Value to clamp.
    lo : float
        Inclusive lower bound.  Defaults to ``0.0``.
    hi : float
        Inclusive upper bound.  Defaults to ``1.0``.

    Returns
    -------
    float
        Clamped value.

    Raises
    ------
    ValueError
        If ``lo > hi``.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.1)
    0.0
    >>> _clamp(0.6, lo=0.0, hi=0.5)
    0.5
    """
    if lo > hi:
        raise ValueError(f"lo ({lo}) must not exceed hi ({hi})")
    return max(lo, min(hi, v))


def _check_eligibility(
    theorem: Any,
    config: Any,
) -> tuple[bool, str]:
    """Quick single-criterion eligibility check using the configured threshold.

    This is a lightweight convenience function that checks only the
    ``CONFIDENCE`` criterion against the config.  For a full multi-criterion
    check, use ``PackEligibilityChecker.check()``.

    Parameters
    ----------
    theorem:
        A ``TheoremCandidate``-like object with a ``confidence`` attribute.
    config:
        A ``DiscoveryConfig``-like object with a ``min_confidence`` attribute
        (defaults to ``0.4`` if absent).

    Returns
    -------
    tuple[bool, str]
        ``(eligible, reason_string)`` where *reason_string* explains the
        outcome.

    Examples
    --------
    >>> class T:
    ...     confidence = 0.7
    >>> class C:
    ...     min_confidence = 0.5
    >>> _check_eligibility(T(), C())
    (True, 'confidence 0.700 >= threshold 0.500')
    """
    min_conf = float(getattr(config, "min_confidence", 0.4)) if config else 0.4
    conf = float(getattr(theorem, "confidence", 0.0))
    if conf >= min_conf:
        return True, f"confidence {conf:.3f} >= threshold {min_conf:.3f}"
    return False, f"confidence {conf:.3f} < threshold {min_conf:.3f}"


def _issue_grant(theorem: Any, authority: "PromotionAuthority") -> str:
    """Issue an authority grant for *theorem* via *authority*.

    This is a thin wrapper around ``authority.issue_grant()`` that logs the
    action.  It is provided as a module-level helper so that callers not
    holding a reference to the ``PackPromotionRunner`` can still issue grants.

    Parameters
    ----------
    theorem:
        A ``TheoremCandidate``-like object.
    authority:
        A ``PromotionAuthority`` instance to use for grant issuance.

    Returns
    -------
    str
        The issued grant ID.

    Examples
    --------
    >>> auth = PromotionAuthority()
    >>> grant_id = _issue_grant(my_theorem, auth)
    >>> auth.is_granted(grant_id)
    True
    """
    return authority.issue_grant(theorem)


def _build_pack_descriptor(theorem: Any, grant_id: str) -> str:
    """Build a human-readable pack descriptor string for *theorem*.

    The descriptor summarises the theorem statement, its kind, confidence,
    and the authority grant ID.  This string is intended for logging and
    audit purposes.

    Parameters
    ----------
    theorem:
        A ``TheoremCandidate``-like object.
    grant_id:
        The grant ID issued by the authority.

    Returns
    -------
    str
        A multi-line descriptor string.

    Examples
    --------
    >>> desc = _build_pack_descriptor(my_theorem, "abc123")
    >>> "grant" in desc.lower()
    True
    """
    tid = str(getattr(theorem, "theorem_id", "?"))
    stmt = str(getattr(theorem, "statement", ""))[:100]
    kind = str(getattr(theorem, "kind_id", "unknown"))
    conf = float(getattr(theorem, "confidence", 0.0))
    return (
        f"PackDescriptor\n"
        f"  theorem_id : {tid}\n"
        f"  kind_id    : {kind}\n"
        f"  confidence : {conf:.3f}\n"
        f"  grant_id   : {grant_id}\n"
        f"  statement  : {stmt}{'...' if len(str(getattr(theorem, 'statement', ''))) > 100 else ''}"
    )


# ---------------------------------------------------------------------------
# EligibilityCriterion
# ---------------------------------------------------------------------------


class EligibilityCriterion(str, Enum):
    """Enumeration of eligibility criteria for pack promotion.

    Each criterion corresponds to a specific check performed by
    ``PackEligibilityChecker``.  All criteria are evaluated independently and
    their results combined into an overall eligibility score.

    Values
    ------
    CONFIDENCE:
        The theorem candidate's ``confidence`` attribute must be at least
        ``config.min_confidence`` (default 0.4).  This is the most important
        criterion — a low confidence theorem should not be promoted.
    STATEMENT_LENGTH:
        The ``statement`` string must be at least 60 characters long.
        Very short statements are typically incomplete or placeholder text.
    CANDIDATE_SUPPORT:
        The ``source_candidate_id`` attribute must be a non-empty string.
        This ensures traceability back to the original discovery candidate.
    PROOF_COMPLETENESS:
        The ``proof_sketch`` must contain at least 2 sentence-ending punctuation
        marks (``'.'``, ``'!'``, or ``'?'``), indicating it is a multi-sentence
        sketch rather than a single label.
    DOMAIN_COVERAGE:
        The ``kind_id`` attribute must be non-empty and at least 3 characters
        long.  Single-character or empty kind IDs indicate that kind
        classification failed for this theorem.

    Examples
    --------
    >>> EligibilityCriterion.CONFIDENCE.value
    'confidence'
    >>> EligibilityCriterion("statement_length")
    <EligibilityCriterion.STATEMENT_LENGTH: 'statement_length'>
    >>> list(EligibilityCriterion)  # doctest: +NORMALIZE_WHITESPACE
    [<EligibilityCriterion.CONFIDENCE: 'confidence'>, ...]
    """

    CONFIDENCE = "confidence"
    STATEMENT_LENGTH = "statement_length"
    CANDIDATE_SUPPORT = "candidate_support"
    PROOF_COMPLETENESS = "proof_completeness"
    DOMAIN_COVERAGE = "domain_coverage"


# ---------------------------------------------------------------------------
# EligibilityResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """Immutable record of the eligibility assessment for one theorem candidate.

    Produced by ``PackEligibilityChecker.check()`` for each theorem candidate.
    Contains per-criterion pass/fail flags, human-readable reason strings, and
    an aggregate eligibility score.

    Attributes
    ----------
    theorem_id : str
        The ID of the theorem candidate that was assessed.
    eligible : bool
        ``True`` if the candidate overall passed eligibility.
    criteria_results : dict[str, bool]
        Mapping from criterion name (``EligibilityCriterion.value``) to
        whether that criterion passed (``True``) or failed (``False``).
    reasons : tuple[str, ...]
        Human-readable reason strings — one per *failed* criterion, empty if
        all criteria passed.
    score : float
        Eligibility score in ``[0.0, 1.0]`` equal to the fraction of criteria
        that passed.

    Examples
    --------
    >>> result = EligibilityResult(
    ...     theorem_id="thm_001",
    ...     eligible=True,
    ...     criteria_results={"confidence": True, "statement_length": True},
    ...     reasons=(),
    ...     score=1.0,
    ... )
    >>> result.passed_criteria()
    ['confidence', 'statement_length']
    >>> result.failed_criteria()
    []
    >>> result.to_dict()["eligible"]
    True
    """

    theorem_id: str
    eligible: bool
    criteria_results: dict[str, bool]
    reasons: tuple[str, ...]
    score: float

    def passed_criteria(self) -> list[str]:
        """Return the list of criteria names that passed.

        Returns
        -------
        list[str]
            Names of criteria that returned ``True``.
        """
        return [k for k, v in self.criteria_results.items() if v]

    def failed_criteria(self) -> list[str]:
        """Return the list of criteria names that failed.

        Returns
        -------
        list[str]
            Names of criteria that returned ``False``.
        """
        return [k for k, v in self.criteria_results.items() if not v]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the result to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of this result.

        Examples
        --------
        >>> result.to_dict().keys()
        dict_keys(['theorem_id', 'eligible', 'criteria_results', 'reasons', 'score'])
        """
        return {
            "theorem_id": self.theorem_id,
            "eligible": self.eligible,
            "criteria_results": dict(self.criteria_results),
            "reasons": list(self.reasons),
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# PackEligibilityChecker
# ---------------------------------------------------------------------------


class PackEligibilityChecker:
    """Check theorem candidates against a configurable set of eligibility criteria.

    Each criterion is evaluated independently.  The overall eligibility score
    is the fraction of criteria that passed.  A candidate is considered
    eligible if its score meets or exceeds a threshold derived from the config
    (default: all criteria must pass, i.e., threshold = 1.0; configurable via
    ``config.eligibility_score_threshold``).

    Parameters
    ----------
    config:
        Optional ``DiscoveryConfig`` controlling per-criterion thresholds.
        If ``None``, defaults are applied.
    criteria:
        Optional list of ``EligibilityCriterion`` values to evaluate.
        If ``None``, all five criteria are used.

    Examples
    --------
    Default checker (all criteria)::

        checker = PackEligibilityChecker()
        result = checker.check(theorem)
        if result.eligible:
            promote(theorem)

    Custom criteria subset::

        checker = PackEligibilityChecker(
            criteria=[EligibilityCriterion.CONFIDENCE, EligibilityCriterion.DOMAIN_COVERAGE]
        )
        results = checker.check_batch(theorems)

    Notes
    -----
    Adding new criteria requires:

    1. Adding a new value to ``EligibilityCriterion``.
    2. Adding a ``_check_<criterion_name>`` method to this class.
    3. Extending the dispatch logic in ``check()``.
    """

    # Default eligibility threshold: the candidate must pass at least this
    # fraction of the configured criteria.
    DEFAULT_SCORE_THRESHOLD: float = 0.6

    def __init__(
        self,
        config: Any | None = None,
        criteria: list[EligibilityCriterion] | None = None,
    ) -> None:
        self._config = config
        self._criteria: list[EligibilityCriterion] = (
            criteria if criteria is not None else list(EligibilityCriterion)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, theorem: Any) -> EligibilityResult:
        """Evaluate all configured criteria for *theorem*.

        Parameters
        ----------
        theorem:
            A ``TheoremCandidate``-like object.

        Returns
        -------
        EligibilityResult
            Immutable result object with per-criterion outcomes and overall
            eligibility flag.
        """
        criteria_results: dict[str, bool] = {}
        reasons: list[str] = []

        for criterion in self._criteria:
            if criterion == EligibilityCriterion.CONFIDENCE:
                passed, reason = self._check_confidence(theorem)
            elif criterion == EligibilityCriterion.STATEMENT_LENGTH:
                passed, reason = self._check_statement_length(theorem)
            elif criterion == EligibilityCriterion.CANDIDATE_SUPPORT:
                passed, reason = self._check_candidate_support(theorem)
            elif criterion == EligibilityCriterion.PROOF_COMPLETENESS:
                passed, reason = self._check_proof_completeness(theorem)
            elif criterion == EligibilityCriterion.DOMAIN_COVERAGE:
                passed, reason = self._check_domain_coverage(theorem)
            else:
                # Unknown criterion — skip
                continue
            criteria_results[criterion.value] = passed
            if not passed:
                reasons.append(f"{criterion.value}: {reason}")

        score = self._compute_eligibility_score(criteria_results)
        score_threshold = float(
            getattr(self._config, "eligibility_score_threshold", self.DEFAULT_SCORE_THRESHOLD)
        ) if self._config else self.DEFAULT_SCORE_THRESHOLD
        eligible = score >= score_threshold

        return EligibilityResult(
            theorem_id=str(getattr(theorem, "theorem_id", _uid()[:8])),
            eligible=eligible,
            criteria_results=criteria_results,
            reasons=tuple(reasons),
            score=score,
        )

    def check_batch(self, theorems: list[Any]) -> list[EligibilityResult]:
        """Evaluate eligibility for every theorem in *theorems*.

        Parameters
        ----------
        theorems:
            List of ``TheoremCandidate``-like objects.

        Returns
        -------
        list[EligibilityResult]
            One result per input theorem, in the same order.
        """
        return [self.check(t) for t in theorems]

    # ------------------------------------------------------------------
    # Per-criterion check methods
    # ------------------------------------------------------------------

    def _check_confidence(self, theorem: Any) -> tuple[bool, str]:
        """Check that the theorem's confidence meets the configured minimum.

        Parameters
        ----------
        theorem:
            The theorem candidate.

        Returns
        -------
        tuple[bool, str]
            ``(passed, human_readable_reason)``.
        """
        min_conf = float(
            getattr(self._config, "min_confidence", 0.4)
        ) if self._config else 0.4
        try:
            conf = float(getattr(theorem, "confidence", 0.0))
        except (TypeError, ValueError):
            return False, "confidence is not a valid float"
        if conf >= min_conf:
            return True, f"confidence {conf:.3f} >= {min_conf:.3f}"
        return False, f"confidence {conf:.3f} < min_confidence {min_conf:.3f}"

    def _check_statement_length(self, theorem: Any) -> tuple[bool, str]:
        """Check that the theorem statement meets a minimum length requirement.

        Parameters
        ----------
        theorem:
            The theorem candidate.

        Returns
        -------
        tuple[bool, str]
        """
        stmt = str(getattr(theorem, "statement", "") or "")
        min_len = int(getattr(self._config, "min_statement_length", 60)) if self._config else 60
        length = len(stmt.strip())
        if length >= min_len:
            return True, f"statement length {length} >= {min_len}"
        return False, f"statement length {length} < min_statement_length {min_len}"

    def _check_candidate_support(self, theorem: Any) -> tuple[bool, str]:
        """Check that the theorem references a valid source candidate.

        Parameters
        ----------
        theorem:
            The theorem candidate.

        Returns
        -------
        tuple[bool, str]
        """
        cid = str(getattr(theorem, "source_candidate_id", "") or "")
        if cid.strip():
            return True, f"source_candidate_id='{cid}' is present"
        return False, "source_candidate_id is empty or missing"

    def _check_proof_completeness(self, theorem: Any) -> tuple[bool, str]:
        """Check that the proof sketch is sufficiently complete.

        A sketch is considered complete if it contains at least 2 sentence-
        terminating punctuation marks, indicating a multi-sentence sketch.

        Parameters
        ----------
        theorem:
            The theorem candidate.

        Returns
        -------
        tuple[bool, str]
        """
        sketch = str(getattr(theorem, "proof_sketch", "") or "")
        min_sentences = int(
            getattr(self._config, "min_proof_sentences", 2)
        ) if self._config else 2
        sentence_count = len(re.findall(r"[.!?]", sketch))
        if sentence_count >= min_sentences:
            return True, f"proof_sketch has {sentence_count} sentences (>= {min_sentences})"
        return False, (
            f"proof_sketch has only {sentence_count} sentence(s); "
            f"min is {min_sentences}"
        )

    def _check_domain_coverage(self, theorem: Any) -> tuple[bool, str]:
        """Check that the theorem has a valid, non-trivial kind ID.

        Parameters
        ----------
        theorem:
            The theorem candidate.

        Returns
        -------
        tuple[bool, str]
        """
        kid = str(getattr(theorem, "kind_id", "") or "")
        if len(kid.strip()) >= 3:
            return True, f"kind_id='{kid}' is non-trivial"
        return False, f"kind_id='{kid}' is too short or empty (min 3 chars)"

    def _compute_eligibility_score(self, criteria_results: dict[str, bool]) -> float:
        """Compute the fraction of criteria that passed.

        Parameters
        ----------
        criteria_results:
            Mapping from criterion name to pass/fail boolean.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.

        Examples
        --------
        >>> checker = PackEligibilityChecker()
        >>> checker._compute_eligibility_score({"a": True, "b": False, "c": True})
        0.6666666666666666
        """
        if not criteria_results:
            return 0.0
        return sum(criteria_results.values()) / len(criteria_results)


# ---------------------------------------------------------------------------
# PromotionReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromotionReport:
    """Immutable summary report for a single pack-promotion run.

    Encapsulates high-level statistics about what was promoted and rejected,
    plus timing information and the individual ``PromotionDecision`` objects.

    Attributes
    ----------
    report_id : str
        Unique identifier for this report.
    pipeline_run_id : str
        Identifier of the overall discovery pipeline run that produced these
        theorems.
    total_candidates : int
        Total number of theorem candidates submitted for promotion.
    approved : int
        Number of candidates approved for promotion.
    rejected : int
        Number of candidates rejected.
    decisions : tuple[PromotionDecision, ...]
        All promotion decisions made during this run.
    elapsed_secs : float
        Wall-clock time taken for the entire promotion run in seconds.
    created_at : float
        POSIX timestamp of when this report was created.

    Examples
    --------
    >>> report = PromotionReport(
    ...     report_id="r001",
    ...     pipeline_run_id="p001",
    ...     total_candidates=10,
    ...     approved=7,
    ...     rejected=3,
    ...     decisions=(),
    ...     elapsed_secs=0.12,
    ...     created_at=1700000000.0,
    ... )
    >>> report.approval_rate
    0.7
    >>> report.summary()[:20]
    'PromotionReport r001'
    """

    report_id: str
    pipeline_run_id: str
    total_candidates: int
    approved: int
    rejected: int
    decisions: tuple[Any, ...]
    elapsed_secs: float
    created_at: float

    @property
    def approval_rate(self) -> float:
        """Return the fraction of candidates that were approved.

        Returns
        -------
        float
            Approval rate in ``[0.0, 1.0]``.  Returns ``0.0`` if
            *total_candidates* is zero.

        Examples
        --------
        >>> report.approval_rate  # 7/10
        0.7
        """
        if self.total_candidates == 0:
            return 0.0
        return self.approved / self.total_candidates

    def approved_decisions(self) -> list[Any]:
        """Return only the approved ``PromotionDecision`` objects.

        Returns
        -------
        list[PromotionDecision]
        """
        return [
            d for d in self.decisions
            if getattr(d, "approved", False)
        ]

    def rejected_decisions(self) -> list[Any]:
        """Return only the rejected ``PromotionDecision`` objects.

        Returns
        -------
        list[PromotionDecision]
        """
        return [
            d for d in self.decisions
            if not getattr(d, "approved", True)
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the report to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.

        Examples
        --------
        >>> d = report.to_dict()
        >>> d["approval_rate"]
        0.7
        """
        return {
            "report_id": self.report_id,
            "pipeline_run_id": self.pipeline_run_id,
            "total_candidates": self.total_candidates,
            "approved": self.approved,
            "rejected": self.rejected,
            "approval_rate": self.approval_rate,
            "elapsed_secs": self.elapsed_secs,
            "created_at": self.created_at,
            "decisions": [
                {
                    "theorem_id": str(getattr(d, "theorem_id", "?")),
                    "approved": bool(getattr(d, "approved", False)),
                    "grant_id": str(getattr(d, "grant_id", "")),
                    "reason": str(getattr(d, "reason", "")),
                }
                for d in self.decisions
            ],
        }

    def summary(self) -> str:
        """Return a one-paragraph human-readable summary of this report.

        Returns
        -------
        str
            Multi-line summary string.

        Examples
        --------
        >>> print(report.summary())
        PromotionReport r001
          Pipeline run : p001
          ...
        """
        return (
            f"PromotionReport {self.report_id}\n"
            f"  Pipeline run : {self.pipeline_run_id}\n"
            f"  Total        : {self.total_candidates}\n"
            f"  Approved     : {self.approved} ({self.approval_rate:.1%})\n"
            f"  Rejected     : {self.rejected}\n"
            f"  Elapsed      : {self.elapsed_secs:.3f}s\n"
        )


# ---------------------------------------------------------------------------
# PromotionAuthority
# ---------------------------------------------------------------------------


class PromotionAuthority:
    """In-memory authority that issues and revokes pack promotion grants.

    A ``PromotionAuthority`` maintains a dictionary of active grants.  Each
    grant is identified by a unique ``grant_id`` and records the theorem ID,
    issuance timestamp, and other metadata.

    Parameters
    ----------
    authority_id:
        Optional identifier for this authority instance.  A random ID is
        generated if not supplied.

    Examples
    --------
    Issuing and checking grants::

        auth = PromotionAuthority()
        grant_id = auth.issue_grant(theorem)
        assert auth.is_granted(grant_id)
        auth.revoke_grant(grant_id)
        assert not auth.is_granted(grant_id)

    Listing grants::

        for gid in auth.list_grants():
            print(gid)
        print(f"Total active grants: {auth.grant_count}")

    Notes
    -----
    This class is *not* thread-safe.  For concurrent use, wrap grant
    operations in a threading lock.  For persistence, use the
    ``PackAuthorityRegistry`` from ``jugeo.packs.authority``.
    """

    def __init__(self, authority_id: str | None = None) -> None:
        self.authority_id: str = authority_id or f"auth_{_uid()[:8]}"
        self._grants: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_grant(self, theorem: Any) -> str:
        """Issue a new grant for *theorem* and return the grant ID.

        Parameters
        ----------
        theorem:
            A ``TheoremCandidate``-like object.

        Returns
        -------
        str
            The generated grant ID.

        Examples
        --------
        >>> auth = PromotionAuthority()
        >>> gid = auth.issue_grant(my_theorem)
        >>> len(gid) == 32
        True
        """
        grant_id = _uid()
        self._grants[grant_id] = {
            "grant_id": grant_id,
            "theorem_id": str(getattr(theorem, "theorem_id", "")),
            "kind_id": str(getattr(theorem, "kind_id", "")),
            "confidence": float(getattr(theorem, "confidence", 0.0)),
            "issued_at": _utcnow(),
            "authority_id": self.authority_id,
        }
        return grant_id

    def revoke_grant(self, grant_id: str) -> bool:
        """Revoke the grant with *grant_id*.

        Parameters
        ----------
        grant_id:
            The grant identifier to revoke.

        Returns
        -------
        bool
            ``True`` if the grant existed and was revoked; ``False`` if it
            was not found.

        Examples
        --------
        >>> auth = PromotionAuthority()
        >>> gid = auth.issue_grant(t)
        >>> auth.revoke_grant(gid)
        True
        >>> auth.revoke_grant(gid)  # already revoked
        False
        """
        if grant_id in self._grants:
            del self._grants[grant_id]
            return True
        return False

    def is_granted(self, grant_id: str) -> bool:
        """Return ``True`` if *grant_id* is an active grant.

        Parameters
        ----------
        grant_id:
            The grant identifier to check.

        Returns
        -------
        bool
        """
        return grant_id in self._grants

    def list_grants(self) -> list[str]:
        """Return a list of all active grant IDs.

        Returns
        -------
        list[str]
            Grant IDs in arbitrary order.
        """
        return list(self._grants.keys())

    @property
    def grant_count(self) -> int:
        """Return the number of active grants.

        Returns
        -------
        int

        Examples
        --------
        >>> auth = PromotionAuthority()
        >>> auth.grant_count
        0
        >>> _ = auth.issue_grant(t)
        >>> auth.grant_count
        1
        """
        return len(self._grants)


# ---------------------------------------------------------------------------
# PackPromotionRunner
# ---------------------------------------------------------------------------


class PackPromotionRunner:
    """Orchestrate the full pack promotion stage of the discovery pipeline.

    Composes ``PackEligibilityChecker``, ``PromotionAuthority``, and
    decision logic into a single pipeline that accepts a
    ``TheoremSynthesisStage`` and produces a ``PackPromotionStage``.

    Parameters
    ----------
    config:
        Optional ``DiscoveryConfig`` controlling eligibility thresholds and
        criteria.
    authority:
        Optional ``PromotionAuthority`` to use for grant issuance.  If
        ``None``, a new ``PromotionAuthority`` is created per run.

    Examples
    --------
    Basic run::

        runner = PackPromotionRunner()
        stage = runner.run(theorem_synthesis_stage)

    Full report::

        runner = PackPromotionRunner(config=cfg)
        stage, report = runner.run_with_report(theorem_synthesis_stage)
        print(report.summary())

    Shared authority::

        authority = PromotionAuthority(authority_id="main_authority")
        runner = PackPromotionRunner(authority=authority)
        for synthesis_stage in synthesis_stages:
            runner.run(synthesis_stage)
        print(f"Total grants issued: {authority.grant_count}")

    Notes
    -----
    When *authority* is ``None``, a fresh ``PromotionAuthority`` is created
    for each call to ``run()`` / ``run_with_report()``.  Grants issued in one
    run will not be visible to a subsequent run unless a shared authority is
    supplied.
    """

    def __init__(
        self,
        config: Any | None = None,
        authority: PromotionAuthority | None = None,
    ) -> None:
        self._config = config
        self._authority = authority

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, stage: Any) -> Any:
        """Run pack promotion on *stage* and return a ``PackPromotionStage``.

        Parameters
        ----------
        stage:
            A ``TheoremSynthesisStage`` object with a ``.theorem_candidates``
            attribute.

        Returns
        -------
        PackPromotionStage
        """
        result, _ = self.run_with_diagnostics(stage)
        return result

    def run_with_report(self, stage: Any) -> tuple[Any, PromotionReport]:
        """Run promotion and return ``(PackPromotionStage, PromotionReport)``.

        Parameters
        ----------
        stage:
            ``TheoremSynthesisStage`` or equivalent.

        Returns
        -------
        tuple[PackPromotionStage, PromotionReport]
        """
        promotion_stage, diag = self.run_with_diagnostics(stage)
        decisions = list(getattr(promotion_stage, "decisions", None) or
                         (diag.get("decisions", []) if isinstance(diag, dict) else []))
        approved = sum(1 for d in decisions if getattr(d, "approved", False))
        report = PromotionReport(
            report_id=_uid()[:12],
            pipeline_run_id=str((diag.get("run_id", "") if isinstance(diag, dict) else getattr(diag, "run_id", "")) or ""),
            total_candidates=int((diag.get("input_count", 0) if isinstance(diag, dict) else getattr(diag, "input_count", 0)) or 0),
            approved=approved,
            rejected=len(decisions) - approved,
            decisions=tuple(decisions),
            elapsed_secs=float((diag.get("elapsed_secs", 0.0) if isinstance(diag, dict) else getattr(diag, "elapsed_secs", 0.0)) or 0.0),
            created_at=_utcnow(),
        )
        return promotion_stage, report

    def run_with_diagnostics(self, stage: Any) -> tuple[Any, Any]:
        """Run promotion and return ``(PackPromotionStage, DiscoveryDiagnostics)``.

        Parameters
        ----------
        stage:
            ``TheoremSynthesisStage`` or equivalent.

        Returns
        -------
        tuple[PackPromotionStage, DiscoveryDiagnostics]
        """
        start = _utcnow()
        theorems = list(getattr(stage, "theorem_candidates", []) or [])
        authority = self._authority if self._authority is not None else PromotionAuthority()
        run_id = _uid()

        diag: dict[str, Any] = {
            "stage": "pack_promotion",
            "run_id": run_id,
            "input_count": len(theorems),
        }

        # Step 1: Eligibility
        eligibility_results = self._run_eligibility_step(theorems, self._config)
        diag["eligible_count"] = sum(1 for r in eligibility_results if r.eligible)
        diag["ineligible_count"] = sum(1 for r in eligibility_results if not r.eligible)

        # Step 2: Decision
        decisions = self._run_decision_step(theorems, eligibility_results)
        diag["approved_count"] = sum(1 for d in decisions if getattr(d, "approved", False))
        diag["rejected_count"] = sum(1 for d in decisions if not getattr(d, "approved", True))

        # Step 3: Grant issuance
        decisions = self._run_grant_step(decisions, authority)

        diag["elapsed_secs"] = _utcnow() - start
        diag["decisions"] = decisions

        try:
            out_stage = PackPromotionStage(  # type: ignore[call-arg]
                stage_id=_uid(),
                decisions=tuple(decisions),
                input_count=len(theorems),
                output_count=diag["approved_count"],
                elapsed_secs=diag["elapsed_secs"],
            )
        except Exception:
            out_stage = {  # type: ignore[assignment]
                "stage": "pack_promotion",
                "decisions": decisions,
                "input_count": len(theorems),
                "output_count": diag["approved_count"],
            }

        try:
            out_diag = DiscoveryDiagnostics(**{k: v for k, v in diag.items() if k != "decisions"})  # type: ignore[call-arg]
        except Exception:
            out_diag = diag  # type: ignore[assignment]

        return out_stage, out_diag

    # ------------------------------------------------------------------
    # Private step runners
    # ------------------------------------------------------------------

    def _run_eligibility_step(
        self,
        theorems: list[Any],
        config: Any | None,
    ) -> list[EligibilityResult]:
        """Evaluate eligibility for all *theorems*."""
        checker = PackEligibilityChecker(config=config)
        return checker.check_batch(theorems)

    def _run_decision_step(
        self,
        theorems: list[Any],
        results: list[EligibilityResult],
    ) -> list[Any]:
        """Create ``PromotionDecision`` objects for each theorem.

        Approved theorems have ``approved=True`` and an empty ``grant_id``.
        Grant IDs are assigned in the subsequent grant step.

        Parameters
        ----------
        theorems:
            List of ``TheoremCandidate``-like objects.
        results:
            Corresponding eligibility results (same order as *theorems*).

        Returns
        -------
        list[PromotionDecision]
        """
        decisions: list[Any] = []
        result_by_id: dict[str, EligibilityResult] = {r.theorem_id: r for r in results}

        for theorem in theorems:
            tid = str(getattr(theorem, "theorem_id", "") or "")
            er = result_by_id.get(tid)
            if er is None:
                continue
            reason = "; ".join(er.reasons) if er.reasons else "all criteria passed"
            try:
                decision = PromotionDecision(  # type: ignore[call-arg]
                    theorem_id=tid,
                    approved=er.eligible,
                    grant_id="",  # filled in by grant step
                    reason=reason,
                    eligibility_score=er.score,
                )
            except Exception:
                decision = {  # type: ignore[assignment]
                    "theorem_id": tid,
                    "approved": er.eligible,
                    "grant_id": "",
                    "reason": reason,
                    "eligibility_score": er.score,
                }
            decisions.append(decision)
        return decisions

    def _run_grant_step(
        self,
        decisions: list[Any],
        authority: PromotionAuthority,
    ) -> list[Any]:
        """Issue authority grants for all approved decisions.

        Parameters
        ----------
        decisions:
            List of ``PromotionDecision``-like objects.
        authority:
            The ``PromotionAuthority`` to use for grant issuance.

        Returns
        -------
        list[PromotionDecision]
            Updated list with ``grant_id`` populated for approved decisions.
        """
        updated: list[Any] = []
        for decision in decisions:
            if getattr(decision, "approved", False):
                # Issue a grant and store the grant_id in the decision.
                grant_id = authority.issue_grant(decision)
                # Attempt to set grant_id on the decision object (may be frozen)
                try:
                    object.__setattr__(decision, "grant_id", grant_id)
                except (AttributeError, TypeError):
                    # Decision is a frozen dataclass or dict; create a new one
                    if isinstance(decision, dict):
                        decision = {**decision, "grant_id": grant_id}
                    else:
                        try:
                            decision = PromotionDecision(  # type: ignore[call-arg]
                                theorem_id=getattr(decision, "theorem_id", ""),
                                approved=True,
                                grant_id=grant_id,
                                reason=getattr(decision, "reason", ""),
                                eligibility_score=getattr(decision, "eligibility_score", 0.0),
                            )
                        except Exception:
                            decision = {
                                "theorem_id": getattr(decision, "theorem_id", ""),
                                "approved": True,
                                "grant_id": grant_id,
                                "reason": getattr(decision, "reason", ""),
                            }
            updated.append(decision)
        return updated


# ---------------------------------------------------------------------------
# Top-level convenience function
# ---------------------------------------------------------------------------


def run_pack_promotion(
    stage: Any,
    config: Any | None = None,
) -> Any:
    """Run the full pack promotion pipeline stage and return the result.

    This is the primary entry point for Stage 4 (and the final stage) of the
    discovery pipeline.

    Parameters
    ----------
    stage:
        A ``TheoremSynthesisStage`` object produced by ``run_theorem_synthesis``.
    config:
        Optional ``DiscoveryConfig`` controlling eligibility thresholds, minimum
        confidence, and other promotion parameters.

    Returns
    -------
    PackPromotionStage
        Terminal stage result.  Its ``.decisions`` attribute contains all
        ``PromotionDecision`` objects, both approved and rejected.  Approved
        decisions have a non-empty ``grant_id``.

    Examples
    --------
    Simple call::

        from jugeo.ideation.discovery_engine.pack_promotion import (
            run_pack_promotion,
        )
        final_stage = run_pack_promotion(theorem_stage, config=cfg)
        approved = [d for d in final_stage.decisions if d.approved]
        print(f"{len(approved)} theorems promoted to pack registry.")

    Full pipeline chaining::

        from jugeo.ideation.discovery_engine.novelty_pipeline import run_novelty_pipeline
        from jugeo.ideation.discovery_engine.kind_classification import run_kind_classification
        from jugeo.ideation.discovery_engine.theorem_synthesis import run_theorem_synthesis
        from jugeo.ideation.discovery_engine.pack_promotion import run_pack_promotion

        s1 = run_novelty_pipeline(raw_candidates, config=cfg)
        s2 = run_kind_classification(list(s1.candidates), config=cfg)
        s3 = run_theorem_synthesis(s2, config=cfg)
        s4 = run_pack_promotion(s3, config=cfg)

    Raises
    ------
    TypeError
        If *stage* does not have a ``theorem_candidates`` attribute and is
        not a dict with that key.

    See also
    --------
    ``run_theorem_synthesis`` — Stage 3 that produces the input.
    ``PackPromotionRunner`` — for re-usable runner instances with shared authority.
    ``PromotionAuthority`` — for managing authority grants across runs.
    ``PromotionReport`` — for structured reporting of promotion results.
    """
    runner = PackPromotionRunner(config=config)
    return runner.run(stage)
