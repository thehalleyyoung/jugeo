"""Trust Algebra — sheaf-theoretic verification for multi-agent LLM systems.

This module implements the *trust algebra* described in the JuGeo framework.
Every factual claim produced by an agent carries a trust level drawn from an
ordered lattice (:class:`TrustLevel`).  Trust may only *increase* through
explicit, auditable evidence channels — the **no-silent-promotion law** — and
the conservative join ensures that chaining two claims never silently inflates
confidence.

Key components
--------------
:class:`TrustAlgebra`
    Stateful engine that classifies, promotes, demotes, and audits claim
    trust across an entire multi-agent pipeline.
:class:`PromotionResult`
    Immutable record of an attempted trust promotion.
:class:`TrustAuditEntry`
    Timestamped audit-log row.
:class:`ModelTrustProfile`
    Registry mapping model identifiers to their baseline trust levels.
:class:`TrustCeilingEnforcer`
    Validator that checks claims against channel trust ceilings.
:class:`TrustBoundaryViolation`
    Report of a claim that exceeds its channel's declared ceiling.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from jugeo_agents.types import (
    CHANNEL_TRUST_CEILINGS,
    AgentOutput,
    EvidenceChannel,
    FactualClaim,
    TrustLevel,
    can_promote,
    conservative_join,
)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PromotionResult:
    """Outcome of an attempted trust promotion.

    Attributes
    ----------
    success:
        ``True`` when the promotion was applied.
    old_trust:
        Trust level *before* the attempt.
    new_trust:
        Trust level *after* the attempt (equals *old_trust* on failure).
    evidence:
        Human-readable evidence string that justified the promotion.
    channel:
        The evidence channel through which the promotion was attempted.
    reason:
        Explanation when the promotion is rejected (empty on success).
    """

    success: bool
    old_trust: TrustLevel
    new_trust: TrustLevel
    evidence: str
    channel: EvidenceChannel
    reason: str = ""


@dataclass(slots=True)
class TrustAuditEntry:
    """Single row in the trust audit log.

    Attributes
    ----------
    claim_id:
        Identifier of the affected :class:`FactualClaim`.
    action:
        One of ``"promote"``, ``"demote"``, or ``"classify"``.
    old_trust:
        Trust level before the action.
    new_trust:
        Trust level after the action.
    evidence:
        Evidence string (may be empty for classification actions).
    channel:
        Evidence channel used (``None`` for demotions / classifications that
        do not go through a channel).
    timestamp:
        Unix timestamp of the action.
    """

    claim_id: str
    action: str
    old_trust: TrustLevel
    new_trust: TrustLevel
    evidence: str = ""
    channel: EvidenceChannel | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class TrustBoundaryViolation:
    """Report of a claim whose trust exceeds the ceiling of its channel.

    Attributes
    ----------
    claim:
        The offending :class:`FactualClaim`.
    declared_trust:
        Trust level currently declared on the claim.
    ceiling:
        Maximum trust the channel permits.
    channel:
        The evidence channel whose ceiling was exceeded.
    """

    claim: FactualClaim
    declared_trust: TrustLevel
    ceiling: TrustLevel
    channel: EvidenceChannel


# ---------------------------------------------------------------------------
# ModelTrustProfile
# ---------------------------------------------------------------------------

# Frontier models that qualify for STRONG_MODEL_GENERATED.
_FRONTIER_PATTERNS: tuple[str, ...] = (
    "claude-opus",
    "claude-sonnet",
    "claude-3-opus",
    "claude-3-sonnet",
    "claude-3.5-sonnet",
    "claude-3.5-opus",
    "claude-4",
    "gpt-4",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4.5",
    "gpt-5",
    "o1",
    "o1-preview",
    "o1-mini",
    "o3",
    "o3-mini",
    "o4-mini",
    "gemini-pro",
    "gemini-ultra",
    "gemini-1.5-pro",
    "gemini-2.0",
    "gemini-2.5-pro",
    "command-r-plus",
    "mistral-large",
    "mistral-medium",
)

# Smaller / weaker models that get WEAK_MODEL_GENERATED.
_WEAK_PATTERNS: tuple[str, ...] = (
    "gpt-3.5",
    "gpt-3",
    "claude-instant",
    "claude-haiku",
    "claude-3-haiku",
    "gemini-flash",
    "gemini-nano",
    "command-r",
    "command-light",
    "mistral-small",
    "mistral-tiny",
    "mixtral",
    "llama",
    "phi-",
    "vicuna",
    "falcon",
    "qwen",
    "deepseek",
    "yi-",
    "codellama",
    "starcoder",
)


class ModelTrustProfile:
    """Registry mapping model identifiers to their baseline trust levels.

    Known frontier models (GPT-4 class, Claude Opus/Sonnet, Gemini Pro, etc.)
    are assigned :attr:`TrustLevel.STRONG_MODEL_GENERATED`.  Smaller or older
    models receive :attr:`TrustLevel.WEAK_MODEL_GENERATED`.  Unrecognised
    model strings conservatively default to
    :attr:`TrustLevel.UNGROUNDED_CLAIM`.

    The registry is case-insensitive and supports substring matching so that
    versioned names like ``"gpt-4-0125-preview"`` are handled automatically.

    Custom overrides can be registered via :meth:`register`.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, TrustLevel] = {}

    # -- public API ----------------------------------------------------------

    def base_trust(self, model: str) -> TrustLevel:
        """Return the baseline trust level for *model*.

        Resolution order:

        1. Exact match in the override registry (case-insensitive).
        2. Substring match against known frontier patterns.
        3. Substring match against known weak patterns.
        4. Fallback to ``UNGROUNDED_CLAIM``.
        """
        key = model.strip().lower()

        if key in self._overrides:
            return self._overrides[key]

        if any(pat in key for pat in _FRONTIER_PATTERNS):
            return TrustLevel.STRONG_MODEL_GENERATED

        if any(pat in key for pat in _WEAK_PATTERNS):
            return TrustLevel.WEAK_MODEL_GENERATED

        return TrustLevel.UNGROUNDED_CLAIM

    def register(self, model: str, level: TrustLevel) -> None:
        """Register a custom trust level for *model*.

        Overrides take precedence over the built-in pattern lists.
        """
        self._overrides[model.strip().lower()] = level

    def unregister(self, model: str) -> bool:
        """Remove a custom override.  Returns ``True`` if it existed."""
        return self._overrides.pop(model.strip().lower(), None) is not None

    def registered_models(self) -> dict[str, TrustLevel]:
        """Return a copy of all explicit overrides."""
        return dict(self._overrides)


# Module-level singleton for convenience.
DEFAULT_MODEL_PROFILE = ModelTrustProfile()


# ---------------------------------------------------------------------------
# TrustCeilingEnforcer
# ---------------------------------------------------------------------------


class TrustCeilingEnforcer:
    """Validates that no claim exceeds its evidence channel's trust ceiling.

    Each :class:`EvidenceChannel` has a maximum trust level defined in
    :data:`CHANNEL_TRUST_CEILINGS`.  A claim whose declared trust exceeds
    the ceiling of the channel that produced it is a *trust boundary
    violation* — an indication that trust was silently inflated somewhere
    in the pipeline.

    Usage::

        enforcer = TrustCeilingEnforcer()
        violations = enforcer.check(claims, channel=EvidenceChannel.RAG_RETRIEVAL)
    """

    def __init__(
        self,
        ceilings: dict[EvidenceChannel, TrustLevel] | None = None,
    ) -> None:
        self._ceilings = ceilings if ceilings is not None else dict(CHANNEL_TRUST_CEILINGS)

    # -- public API ----------------------------------------------------------

    @property
    def ceilings(self) -> dict[EvidenceChannel, TrustLevel]:
        """Return the active ceiling map (read-only copy)."""
        return dict(self._ceilings)

    def ceiling_for(self, channel: EvidenceChannel) -> TrustLevel:
        """Return the ceiling for *channel*.

        Falls back to ``UNGROUNDED_CLAIM`` if the channel has no entry.
        """
        return self._ceilings.get(channel, TrustLevel.UNGROUNDED_CLAIM)

    def check(
        self,
        claims: list[FactualClaim],
        channel: EvidenceChannel | None = None,
    ) -> list[TrustBoundaryViolation]:
        """Check *claims* against their channel ceilings.

        Parameters
        ----------
        claims:
            The claims to validate.
        channel:
            If provided, every claim is checked against this single channel's
            ceiling.  If ``None``, the method inspects each claim's
            ``metadata["channel"]`` to determine the channel per claim; claims
            without channel metadata are skipped.

        Returns
        -------
        list[TrustBoundaryViolation]
            One entry per claim that exceeds its ceiling (empty when all
            claims are within bounds).
        """
        violations: list[TrustBoundaryViolation] = []

        for claim in claims:
            resolved_channel = channel
            if resolved_channel is None:
                raw = claim.metadata.get("channel")
                if raw is None:
                    continue
                if isinstance(raw, EvidenceChannel):
                    resolved_channel = raw
                elif isinstance(raw, str):
                    try:
                        resolved_channel = EvidenceChannel(raw)
                    except ValueError:
                        continue
                else:
                    continue

            ceiling = self.ceiling_for(resolved_channel)
            if claim.trust > ceiling:
                violations.append(
                    TrustBoundaryViolation(
                        claim=claim,
                        declared_trust=claim.trust,
                        ceiling=ceiling,
                        channel=resolved_channel,
                    )
                )

        return violations


# ---------------------------------------------------------------------------
# TrustAlgebra — main engine
# ---------------------------------------------------------------------------


class TrustAlgebra:
    """Stateful trust-algebra engine for a multi-agent verification pipeline.

    The ``TrustAlgebra`` is the single authority that classifies, promotes,
    demotes, and audits trust across every claim in the pipeline.  All
    mutations to trust are recorded in an append-only audit log so that the
    full provenance of every trust transition is available for inspection.

    Parameters
    ----------
    model_profile:
        :class:`ModelTrustProfile` to use for model-based trust baselines.
        Defaults to the module-level ``DEFAULT_MODEL_PROFILE`` singleton.
    ceiling_enforcer:
        Optional pre-configured :class:`TrustCeilingEnforcer`.  A default
        instance is created if omitted.

    Examples
    --------
    >>> from jugeo_agents.types import AgentOutput, TrustLevel
    >>> algebra = TrustAlgebra()
    >>> output = AgentOutput(agent_id="a1", output_text="Paris is the capital.", tools_used=["web_search"])
    >>> algebra.classify_output(output)
    <TrustLevel.TOOL_EXECUTED: 70>
    """

    def __init__(
        self,
        model_profile: ModelTrustProfile | None = None,
        ceiling_enforcer: TrustCeilingEnforcer | None = None,
    ) -> None:
        self._model_profile = model_profile or DEFAULT_MODEL_PROFILE
        self._ceiling_enforcer = ceiling_enforcer or TrustCeilingEnforcer()
        self._log: list[TrustAuditEntry] = []

    # -- audit log -----------------------------------------------------------

    @property
    def audit_log(self) -> list[TrustAuditEntry]:
        """Return the full, ordered list of audit entries.

        The returned list is a shallow copy — callers cannot mutate the
        internal log.
        """
        return list(self._log)

    # -- helpers (private) ---------------------------------------------------

    def _record(
        self,
        claim_id: str,
        action: str,
        old_trust: TrustLevel,
        new_trust: TrustLevel,
        evidence: str = "",
        channel: EvidenceChannel | None = None,
    ) -> TrustAuditEntry:
        """Append an entry to the audit log and return it."""
        entry = TrustAuditEntry(
            claim_id=claim_id,
            action=action,
            old_trust=old_trust,
            new_trust=new_trust,
            evidence=evidence,
            channel=channel,
        )
        self._log.append(entry)
        return entry

    def _base_trust_for_output(self, output: AgentOutput) -> TrustLevel:
        """Determine the *base* trust for an output using evidence signals.

        Evaluation order (highest ceiling first):

        1. ``tools_used`` non-empty → ``TOOL_EXECUTED``
        2. ``rag_sources`` non-empty → ``RAG_GROUNDED``
        3. ``citations`` non-empty → ``CITATION_BACKED``
        4. Model name lookup via :class:`ModelTrustProfile`
        """
        if output.tools_used:
            return TrustLevel.TOOL_EXECUTED

        if output.rag_sources:
            return TrustLevel.RAG_GROUNDED

        if output.citations:
            return TrustLevel.CITATION_BACKED

        if output.model:
            return self._model_profile.base_trust(output.model)

        return TrustLevel.UNGROUNDED_CLAIM

    # -- classification ------------------------------------------------------

    def classify_output(self, output: AgentOutput) -> TrustLevel:
        """Classify the overall trust level of *output*.

        The classification inspects the output's evidence signals (tools
        used, RAG sources, citations, model identity) and assigns the
        highest *justified* trust level.  The result is also written back
        to ``output.trust`` and recorded in the audit log.

        Parameters
        ----------
        output:
            The agent output to classify.

        Returns
        -------
        TrustLevel
            The newly assigned trust level.
        """
        old_trust = output.trust
        new_trust = self._base_trust_for_output(output)

        # Write back onto the mutable output.
        output.trust = new_trust

        self._record(
            claim_id=output.output_id,
            action="classify",
            old_trust=old_trust,
            new_trust=new_trust,
            evidence=self._classification_evidence(output),
        )

        return new_trust

    def classify_claim(self, claim: FactualClaim, output: AgentOutput) -> TrustLevel:
        """Classify a single *claim* within the context of *output*.

        The claim inherits the output's base trust level (determined by the
        evidence signals on the output) but is capped at the claim's own
        current trust if that is already higher — classification never
        silently demotes.

        Parameters
        ----------
        claim:
            The factual claim to classify.
        output:
            The agent output that contains the claim.

        Returns
        -------
        TrustLevel
            The newly assigned trust level.
        """
        old_trust = claim.trust
        output_trust = self._base_trust_for_output(output)

        # If the claim already has higher trust (e.g. previously promoted),
        # classification does not lower it — use :meth:`demote` explicitly.
        new_trust = max(output_trust, old_trust)

        claim.trust = new_trust

        self._record(
            claim_id=claim.claim_id,
            action="classify",
            old_trust=old_trust,
            new_trust=new_trust,
            evidence=self._classification_evidence(output),
        )

        return new_trust

    @staticmethod
    def _classification_evidence(output: AgentOutput) -> str:
        """Build a human-readable evidence string for a classification."""
        parts: list[str] = []
        if output.tools_used:
            parts.append(f"tools={output.tools_used}")
        if output.rag_sources:
            parts.append(f"rag_sources={len(output.rag_sources)}")
        if output.citations:
            parts.append(f"citations={len(output.citations)}")
        if output.model:
            parts.append(f"model={output.model}")
        return "; ".join(parts) if parts else "no evidence signals"

    # -- algebraic operations ------------------------------------------------

    @staticmethod
    def compose(a: TrustLevel, b: TrustLevel) -> TrustLevel:
        """Conservative join of two trust levels.

        Equivalent to :func:`conservative_join` — returns the weaker of the
        two inputs.  This is the fundamental JuGeo invariant: composing
        claims can never silently inflate trust.

        Parameters
        ----------
        a, b:
            Trust levels to compose.

        Returns
        -------
        TrustLevel
            ``min(a, b)``
        """
        return conservative_join(a, b)

    # -- promotion -----------------------------------------------------------

    def promote(
        self,
        claim: FactualClaim,
        target: TrustLevel,
        evidence: str,
        channel: EvidenceChannel,
    ) -> PromotionResult:
        """Attempt to promote *claim* to *target* via *channel*.

        **No-silent-promotion law**: promotion requires a non-empty
        *evidence* string **and** the channel's trust ceiling must be ≥
        *target*.  If either condition fails the promotion is rejected and
        the claim's trust is unchanged.

        Parameters
        ----------
        claim:
            Claim to promote.
        target:
            Desired new trust level.
        evidence:
            Human-readable justification.
        channel:
            Evidence channel through which the evidence was obtained.

        Returns
        -------
        PromotionResult
            Full record of the outcome (success or failure with reason).
        """
        old_trust = claim.trust

        # Gate 1: basic admissibility (non-empty evidence, target > current).
        if not can_promote(old_trust, target, evidence):
            reason = self._promotion_rejection_reason(old_trust, target, evidence)
            self._record(
                claim_id=claim.claim_id,
                action="promote",
                old_trust=old_trust,
                new_trust=old_trust,
                evidence=evidence,
                channel=channel,
            )
            return PromotionResult(
                success=False,
                old_trust=old_trust,
                new_trust=old_trust,
                evidence=evidence,
                channel=channel,
                reason=reason,
            )

        # Gate 2: channel ceiling enforcement.
        ceiling = self._ceiling_enforcer.ceiling_for(channel)
        if target > ceiling:
            reason = (
                f"Target {target.name} (value={target.value}) exceeds "
                f"{channel.value} ceiling {ceiling.name} (value={ceiling.value})"
            )
            self._record(
                claim_id=claim.claim_id,
                action="promote",
                old_trust=old_trust,
                new_trust=old_trust,
                evidence=evidence,
                channel=channel,
            )
            return PromotionResult(
                success=False,
                old_trust=old_trust,
                new_trust=old_trust,
                evidence=evidence,
                channel=channel,
                reason=reason,
            )

        # All gates passed — apply promotion.
        claim.trust = target

        self._record(
            claim_id=claim.claim_id,
            action="promote",
            old_trust=old_trust,
            new_trust=target,
            evidence=evidence,
            channel=channel,
        )

        return PromotionResult(
            success=True,
            old_trust=old_trust,
            new_trust=target,
            evidence=evidence,
            channel=channel,
        )

    @staticmethod
    def _promotion_rejection_reason(
        old: TrustLevel, target: TrustLevel, evidence: str
    ) -> str:
        """Build a human-readable rejection reason."""
        if not evidence:
            return "No evidence provided (no-silent-promotion law)"
        if target <= old:
            return (
                f"Target {target.name} (value={target.value}) is not higher "
                f"than current {old.name} (value={old.value})"
            )
        return "Unknown rejection reason"

    # -- demotion ------------------------------------------------------------

    def demote(self, claim: FactualClaim, reason: str) -> TrustLevel:
        """Demote *claim* by one trust tier.

        Demotion drops the claim to the next lower :class:`TrustLevel`
        member.  If the claim is already at the absolute minimum
        (``SELF_CONTRADICTED``), it remains there.

        Parameters
        ----------
        claim:
            Claim to demote.
        reason:
            Mandatory justification (e.g. ``"failed cross-agent challenge"``).

        Returns
        -------
        TrustLevel
            The new (lower) trust level.
        """
        old_trust = claim.trust
        ordered = sorted(TrustLevel, key=lambda t: t.value)
        idx = next(i for i, t in enumerate(ordered) if t == old_trust)
        new_trust = ordered[max(0, idx - 1)]

        claim.trust = new_trust

        self._record(
            claim_id=claim.claim_id,
            action="demote",
            old_trust=old_trust,
            new_trust=new_trust,
            evidence=reason,
        )

        return new_trust

    # -- cross-agent confirmation --------------------------------------------

    def cross_agent_confirm(
        self,
        claims: list[FactualClaim],
        threshold: int = 2,
    ) -> list[FactualClaim]:
        """Promote claims that are confirmed by multiple independent agents.

        Claims are grouped by ``(subject, predicate, value)`` — the
        *semantic key*.  When at least *threshold* distinct ``source_agent``
        values agree on a key, every matching claim is promoted to
        :attr:`TrustLevel.CROSS_AGENT_CONFIRMED` (unless it already holds
        a higher trust level).

        Parameters
        ----------
        claims:
            Pool of claims from all agents.
        threshold:
            Minimum number of distinct agents required for confirmation.

        Returns
        -------
        list[FactualClaim]
            The subset of claims that were promoted.
        """
        # Group by semantic key.
        groups: dict[tuple[str, str, str], list[FactualClaim]] = {}
        for claim in claims:
            key = (claim.subject.strip().lower(),
                   claim.predicate.strip().lower(),
                   claim.value.strip().lower())
            groups.setdefault(key, []).append(claim)

        promoted: list[FactualClaim] = []

        for _key, group in groups.items():
            distinct_agents = {c.source_agent for c in group if c.source_agent}
            if len(distinct_agents) < threshold:
                continue

            agent_list = ", ".join(sorted(distinct_agents))
            evidence = (
                f"Cross-agent confirmation: {len(distinct_agents)} agents "
                f"({agent_list}) agree"
            )

            for claim in group:
                if claim.trust >= TrustLevel.CROSS_AGENT_CONFIRMED:
                    # Already at or above the target — no action needed.
                    continue

                old_trust = claim.trust
                claim.trust = TrustLevel.CROSS_AGENT_CONFIRMED

                self._record(
                    claim_id=claim.claim_id,
                    action="promote",
                    old_trust=old_trust,
                    new_trust=TrustLevel.CROSS_AGENT_CONFIRMED,
                    evidence=evidence,
                    channel=EvidenceChannel.LLM_VERIFICATION,
                )
                promoted.append(claim)

        return promoted

    # -- summary -------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a summary of the trust audit log.

        The returned dictionary contains:

        - ``"total_actions"``: total number of audit entries.
        - ``"promotes"``: count of successful promotions.
        - ``"demotes"``: count of demotions.
        - ``"classifies"``: count of classifications.
        - ``"trust_distribution"``: mapping of trust-level name → count of
          claims currently at that level (derived from the *latest* entry
          per claim).
        - ``"channels_used"``: mapping of channel name → usage count.
        - ``"rejected_promotions"``: count of promotion attempts whose
          ``new_trust == old_trust`` (i.e. no change).

        Returns
        -------
        dict[str, Any]
        """
        promotes = 0
        demotes = 0
        classifies = 0
        rejected_promotions = 0
        channel_counts: Counter[str] = Counter()

        # Track the latest trust per claim_id for distribution.
        latest_trust: dict[str, TrustLevel] = {}

        for entry in self._log:
            latest_trust[entry.claim_id] = entry.new_trust

            if entry.action == "promote":
                if entry.new_trust > entry.old_trust:
                    promotes += 1
                else:
                    rejected_promotions += 1
            elif entry.action == "demote":
                demotes += 1
            elif entry.action == "classify":
                classifies += 1

            if entry.channel is not None:
                channel_counts[entry.channel.value] += 1

        trust_distribution: Counter[str] = Counter()
        for trust in latest_trust.values():
            trust_distribution[trust.name] += 1

        return {
            "total_actions": len(self._log),
            "promotes": promotes,
            "demotes": demotes,
            "classifies": classifies,
            "rejected_promotions": rejected_promotions,
            "trust_distribution": dict(trust_distribution),
            "channels_used": dict(channel_counts),
        }
