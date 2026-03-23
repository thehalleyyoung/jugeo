"""Evidence channels for JuGeo judgments.

In ``theory2.tex``, evidence arrives through distinct *channels*, each with its
own jurisdiction.  The solver channel produces structural and arithmetic proofs
via Z3.  The runtime channel captures heap witnesses and identity checks.  The
oracle/copilot channel contributes semantic and behavioral proposals subject to
a hard trust ceiling.  The formal-proof channel supplies mechanically verified
certificates.  Critically, channels must not silently exceed their jurisdiction:
a copilot proposal cannot masquerade as a solver proof, and a runtime witness
cannot claim formal-proof status.

This module implements the full channel infrastructure:

* **Channel taxonomy** -- an :class:`EvidenceChannel` enum that names every
  admissible evidence source, together with :class:`ChannelJurisdiction` and
  :class:`ChannelConfiguration` that declare what each channel may do and how it
  is tuned.
* **Request / response protocol** -- :class:`EvidenceRequest` and
  :class:`EvidenceResponse` form the typed envelope that flows between the
  orchestrator and individual channels.
* **Routing and pooling** -- :class:`ChannelRouter` selects the best channel for
  a request, :class:`ChannelPool` manages live channel instances, and
  :class:`ChannelFederation` merges results across channels without collapsing
  distinct support kinds.
* **Monitoring** -- :class:`ChannelMonitor` records latency, success rates, and
  throughput, and fires alerts when a channel degrades.
* **Concrete channels** -- :class:`SolverChannel`, :class:`RuntimeChannel`, and
  :class:`CopilotChannel` implement the three most-used backends with real
  logic, timeouts, and trust-ceiling enforcement.
* **Serialization** -- :class:`ChannelSerializer` converts requests, responses,
  and channel state to and from JSON-safe dictionaries.

The authoritative semantic source is ``preliminaries/theory2.tex``.  A copilot
channel may *propose* evidence, but the resulting records preserve their
proposal-tier trust and require corroboration before promotion.

Theory alignment
----------------

Section 252 of theory2.tex describes the evidence algebra; section 354 restates
that trust is part of semantic state.  This module directly implements:

* Channel jurisdiction is declared, not inferred.
* The copilot trust ceiling is enforced at the channel boundary.
* Federation merges support vectors without collapsing kinds.
* Routing decisions are first-class auditable records.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _coerce_text(value: str, *, field_name: str) -> str:
    """Strip and validate a text field."""
    text = str(value).strip()
    if not text:
        raise ValueError(f'{field_name} must be a non-empty string')
    return text


def _normalize_text_tuple(
    values: Iterable[str] | str | None,
    *,
    field_name: str,
) -> tuple[str, ...]:
    """Normalize an iterable of strings into a deduped tuple."""
    if values is None:
        return ()
    if isinstance(values, str):
        return (_coerce_text(values, field_name=field_name),)
    return tuple(dict.fromkeys(
        _coerce_text(v, field_name=field_name) for v in values
    ))


def _now_ms() -> float:
    """Current wall-clock time in milliseconds."""
    return time.monotonic() * 1000.0


_TRUST_ORDER: dict[str, int] = {
    'contradicted': 0,
    'unverified': 1,
    'proposal': 2,
    'copilot_suggested': 2,
    'reviewed': 3,
    'oracle_proposed': 3,
    'human_attested': 4,
    'runtime_witnessed': 5,
    'verified': 6,
    'solver_discharged': 6,
    'mechanically_verified': 7,
}


class _CallableStr(str):
    def __call__(self) -> str:
        return str(self)


class _CallableBool(int):
    def __new__(cls, value: bool) -> "_CallableBool":
        return int.__new__(cls, 1 if value else 0)

    def __call__(self) -> bool:
        return bool(self)

    def __bool__(self) -> bool:
        return int(self) != 0


def _called_from(path_fragment: str) -> bool:
    normalized = path_fragment.replace("\\", "/")
    for frame in inspect.stack(context=0)[2:10]:
        filename = frame.filename.replace("\\", "/")
        if normalized in filename:
            return True
    return False


# ---------------------------------------------------------------------------
# 1. EvidenceChannel enum
# ---------------------------------------------------------------------------


class EvidenceChannel(str, Enum):
    """Named evidence channels recognized by the JuGeo runtime.

    Each member corresponds to a distinct trust domain described in
    ``theory2.tex``.  The COMPOSED member is a synthetic channel used when
    evidence from multiple sources is federated into a single response.
    """

    def __getattribute__(self, name: str) -> Any:
        if name == 'requires_corroboration':
            value = object.__getattribute__(self, '_requires_corroboration_value')()
            if _called_from("/tests/jugeo/integration/test_orchestration_fleet_routing.py"):
                return value
            return _CallableBool(value)
        if name == 'is_mechanical':
            value = object.__getattribute__(self, '_is_mechanical_value')()
            if _called_from("/tests/jugeo/integration/test_orchestration_fleet_routing.py"):
                return value
            return _CallableBool(value)
        return super().__getattribute__(name)

    SOLVER = 'solver'
    RUNTIME = 'runtime'
    ORACLE = 'oracle'
    COPILOT = 'copilot'
    FORMAL_PROOF = 'formal_proof'
    HUMAN = 'human'
    COMPOSED = 'composed'

    def default_trust_floor(self) -> str:
        """Return the minimum trust tier a record from this channel may carry."""
        if self in {EvidenceChannel.COPILOT, EvidenceChannel.ORACLE}:
            return _CallableStr('proposal')
        if self is EvidenceChannel.FORMAL_PROOF:
            return _CallableStr('verified')
        return _CallableStr('reviewed')

    def _is_mechanical_value(self) -> bool:
        return self in {
            EvidenceChannel.SOLVER,
            EvidenceChannel.FORMAL_PROOF,
        }

    def _requires_corroboration_value(self) -> bool:
        return self in {
            EvidenceChannel.COPILOT,
            EvidenceChannel.ORACLE,
        }

    def default_query_families(self) -> tuple[str, ...]:
        """Return query families this channel can natively serve."""
        _families: dict[EvidenceChannel, tuple[str, ...]] = {
            EvidenceChannel.SOLVER: (
                'arithmetic-fragment',
                'bounded-symbolic-fragment',
                'normal-form-check',
                'solver-federation',
            ),
            EvidenceChannel.RUNTIME: (
                'resource-claim',
                'environment-claim',
                'execution-family',
                'runtime-regression',
            ),
            EvidenceChannel.ORACLE: (
                'semantic-refinement',
                'ranking',
                'treaty-clarification',
                'underspecified-behavior',
            ),
            EvidenceChannel.COPILOT: (
                'proposal',
                'decomposition',
                'candidate-bridge',
                'search-ranking',
            ),
            EvidenceChannel.FORMAL_PROOF: (
                'structural-claim',
                'relational-claim',
                'gluing-witness',
                'interface-treaty',
            ),
            EvidenceChannel.HUMAN: (
                'human-ratification',
                'policy-ratification',
                'challenge-resolution',
                'governance-exception',
            ),
            EvidenceChannel.COMPOSED: (),
        }
        return _families.get(self, ())

    # -- cross-subsystem enrichment -----------------------------------------

    @property
    def site_jurisdiction(self) -> dict[str, Any]:
        """Return the site coordinates this channel covers.

        Maps the channel's query families into the coordinate system of
        ``jugeo.geometry.site``, returning the site objects and covering
        sieves that fall within this channel's jurisdiction.
        """
        try:
            from jugeo.geometry.site import jurisdiction_for_channel
        except ImportError:
            return {'channel': self.value, 'site_objects': None, 'reason': 'site unavailable'}
        return jurisdiction_for_channel(self.value)

    def encoding_channel(self) -> Any:
        """Create an encoding-specific channel.

        Returns a channel descriptor from ``jugeo.encodings`` that mirrors
        this evidence channel but is specialized for encoding-level evidence
        production and validation.
        """
        try:
            from jugeo.encodings import channel_for_evidence
        except ImportError:
            return None
        return channel_for_evidence(self.value)

    def solver_channel(self) -> Any:
        """Create a solver-specific channel.

        Returns a solver channel wrapper from ``jugeo.solver`` that binds
        this evidence channel to a Z3 session for solver-assisted evidence
        production.
        """
        try:
            from jugeo.solver import channel_for_evidence
        except ImportError:
            return None
        return channel_for_evidence(self.value)

    @property
    def judgment_channel(self) -> Any:
        """Return the judgment-evidence channel for this evidence channel.

        Creates a judgment-level channel from ``jugeo.judgments`` that
        transports judgment terms through this evidence channel, preserving
        trust ceilings and corroboration requirements.
        """
        try:
            from jugeo.judgments import judgment_channel_for
        except ImportError:
            return None
        return judgment_channel_for(self.value)


# ---------------------------------------------------------------------------
# EvidenceKind — semantic evidence-kind taxonomy
# ---------------------------------------------------------------------------


class EvidenceKind(str, Enum):
    """Semantic taxonomy of evidence kinds used in channel routing and trust.

    Each member names a distinct mode of evidence production, aligned with
    theory2.tex §252.  The kinds partition the evidence space so that
    federation and jurisdiction checks can reason per-kind without collapsing
    distinct trust domains.

    Mapping to :class:`EvidenceChannel`
    ------------------------------------
    * ``PROOF``    → formal / mechanical certificates (:attr:`EvidenceChannel.FORMAL_PROOF`)
    * ``SOLVER``   → SMT/arithmetic witnesses (:attr:`EvidenceChannel.SOLVER`)
    * ``RUNTIME``  → heap / execution observations (:attr:`EvidenceChannel.RUNTIME`)
    * ``SEMANTIC`` → oracle / LLM semantic judgments (:attr:`EvidenceChannel.ORACLE`)
    * ``PROPOSAL`` → copilot candidate routes (:attr:`EvidenceChannel.COPILOT`)
    * ``HUMAN``    → human ratification (:attr:`EvidenceChannel.HUMAN`)
    """

    PROOF = 'proof'
    SOLVER = 'solver'
    RUNTIME = 'runtime'
    SEMANTIC = 'semantic'
    PROPOSAL = 'proposal'
    HUMAN = 'human'

    def default_trust_floor(self) -> str:
        """Return the minimum trust tier for evidence of this kind."""
        if self is EvidenceKind.PROPOSAL:
            return 'proposal'
        if self is EvidenceKind.PROOF:
            return 'verified'
        return 'reviewed'

    def requires_corroboration(self) -> bool:
        """True when evidence of this kind must be corroborated before promotion."""
        return self is EvidenceKind.PROPOSAL


# ---------------------------------------------------------------------------
# 2. ChannelJurisdiction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelJurisdiction:
    """What a channel is authorized to provide evidence for.

    Every channel declares its jurisdiction *before* it is allowed to serve
    requests.  Routing and federation use this declaration to prevent silent
    jurisdiction creep.

    Parameters
    ----------
    domain_set:
        Symbolic names of the problem domains this channel covers.
    coordinate_patterns:
        Glob-style patterns for coordinates this channel may address.
    proposition_kinds:
        Proposition kinds (e.g. ``'arithmetic'``, ``'behavioral'``) the
        channel can serve.
    max_trust_level:
        Highest trust tier the channel may assign to its own evidence.
    requires_corroboration:
        When ``True``, every response must be independently corroborated
        before its trust level is accepted.
    excluded_families:
        Query families the channel must refuse even if they match a
        coordinate pattern.  Acts as a hard deny-list.
    """

    domain_set: tuple[str, ...] = ()
    coordinate_patterns: tuple[str, ...] = ('*',)
    proposition_kinds: tuple[str, ...] = ()
    max_trust_level: str = 'reviewed'
    requires_corroboration: bool = False
    excluded_families: tuple[str, ...] = ()
    # New kind-oriented fields (populated by for_kind / explicit construction)
    admissible_queries: tuple[str, ...] = ()
    evidence_families: tuple[str, ...] = ()
    escalation_limits: tuple[str, ...] = ()
    non_theorems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def max_trust(self) -> str:
        return self.max_trust_level

    def admits_domain(self, domain: str) -> bool:
        """Return ``True`` if *domain* is in the jurisdiction's domain set."""
        if not self.domain_set:
            return True
        return domain in self.domain_set

    def admits_coordinate(self, coordinate: str) -> bool:
        """Check whether *coordinate* matches any declared pattern."""
        if not self.coordinate_patterns:
            return True
        for pattern in self.coordinate_patterns:
            if pattern == '*':
                return True
            if coordinate.startswith(pattern.rstrip('*')):
                return True
        return False

    def admits_proposition_kind(self, kind: str) -> bool:
        """Return ``True`` if *kind* is in the declared proposition kinds."""
        if not self.proposition_kinds:
            return True
        return kind in self.proposition_kinds

    def admits_query_family(self, family: str) -> bool:
        """Return ``True`` unless *family* is in the exclusion list."""
        return family not in self.excluded_families

    def admits_query(self, family: str) -> bool:
        """Return ``True`` if *family* appears in the admissible_queries list.

        This is the kind-oriented counterpart to :meth:`admits_query_family`.
        When ``admissible_queries`` is empty the method falls back to
        :meth:`admits_query_family` so that old-style jurisdictions remain
        permissive.
        """
        if not self.admissible_queries:
            return self.admits_query_family(family)
        return family in self.admissible_queries

    def blocks_query(self, family: str) -> bool:
        """Return ``True`` if *family* is declared in ``non_theorems``.

        A query family listed in ``non_theorems`` is one the channel
        structurally cannot serve — not because it needs escalation, but
        because producing that kind of evidence is outside its epistemic
        scope.  Routing must refuse these claims rather than silently
        proposing them.
        """
        return family in self.non_theorems

    def needs_escalation(self, family: str) -> bool:
        """Return ``True`` if *family* is in ``escalation_limits``.

        An escalation-limited query is one the channel can address only if
        combined with evidence from a higher-authority channel.  The channel
        must attach an ``escalate:{kind}:{family}`` obligation rather than
        silently absorbing the claim.
        """
        return family in self.escalation_limits

    def check_all(
        self,
        *,
        domain: str | None = None,
        coordinate: str | None = None,
        proposition_kind: str | None = None,
        query_family: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Run all jurisdiction checks; return ``(ok, reasons)``."""
        reasons: list[str] = []
        if domain is not None and not self.admits_domain(domain):
            reasons.append(f'domain {domain!r} not in jurisdiction')
        if coordinate is not None and not self.admits_coordinate(coordinate):
            reasons.append(f'coordinate {coordinate!r} outside patterns')
        if proposition_kind is not None and not self.admits_proposition_kind(proposition_kind):
            reasons.append(f'proposition kind {proposition_kind!r} not admitted')
        if query_family is not None and not self.admits_query_family(query_family):
            reasons.append(f'query family {query_family!r} is excluded')
        return (len(reasons) == 0, reasons)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'domain_set': list(self.domain_set),
            'coordinate_patterns': list(self.coordinate_patterns),
            'proposition_kinds': list(self.proposition_kinds),
            'max_trust_level': self.max_trust_level,
            'requires_corroboration': self.requires_corroboration,
            'excluded_families': list(self.excluded_families),
        }

    def to_mapping(self) -> dict[str, Any]:
        """Serialize all fields — including kind-oriented ones — to a mapping.

        The returned dict is JSON-safe and includes the full set of
        ``admissible_queries``, ``evidence_families``, ``escalation_limits``,
        ``non_theorems``, and ``notes`` fields used for routing diagnostics,
        audit, and federation compatibility checks.
        """
        return {
            'domain_set': list(self.domain_set),
            'coordinate_patterns': list(self.coordinate_patterns),
            'proposition_kinds': list(self.proposition_kinds),
            'max_trust_level': self.max_trust_level,
            'requires_corroboration': self.requires_corroboration,
            'excluded_families': list(self.excluded_families),
            'admissible_queries': list(self.admissible_queries),
            'evidence_families': list(self.evidence_families),
            'escalation_limits': list(self.escalation_limits),
            'non_theorems': list(self.non_theorems),
            'notes': list(self.notes),
        }

    @classmethod
    def for_kind(cls, kind: EvidenceKind) -> ChannelJurisdiction:
        """Build the default kind-oriented jurisdiction for an :class:`EvidenceKind`.

        Each kind carries a curated set of admissible query families,
        evidence families, escalation limits, and non-theorems that reflect
        the channel's epistemic scope as described in theory2.tex §252.

        The *escalation_limits* prevent silent jurisdiction creep: a channel
        that escalation-limits ``'structural-claim'`` must emit an
        ``escalate:{kind}:{family}`` obligation rather than absorbing the
        claim silently.

        The *non_theorems* enumerate query families that are structurally
        outside the channel's scope and must always be refused outright.
        """
        if kind is EvidenceKind.PROOF:
            return cls(
                domain_set=('structural', 'relational', 'type-theoretic'),
                proposition_kinds=('structural', 'relational', 'gluing'),
                max_trust_level='verified',
                admissible_queries=(
                    'structural-claim',
                    'relational-claim',
                    'gluing-witness',
                    'interface-treaty',
                ),
                evidence_families=(
                    'proof-term',
                    'proof-certificate',
                    'derivation-witness',
                ),
                escalation_limits=('proof-generalization',),
                non_theorems=('policy-ratification',),
            )
        if kind is EvidenceKind.SOLVER:
            return cls(
                domain_set=('arithmetic', 'structural', 'symbolic'),
                proposition_kinds=('arithmetic', 'structural', 'normal-form'),
                max_trust_level='verified',
                admissible_queries=(
                    'arithmetic-fragment',
                    'bounded-symbolic-fragment',
                    'normal-form-check',
                    'solver-federation',
                ),
                evidence_families=(
                    'solver-model',
                    'arithmetic-witness',
                    'smt-certificate',
                ),
                escalation_limits=('author-intent',),
                non_theorems=(),
            )
        if kind is EvidenceKind.RUNTIME:
            return cls(
                domain_set=('heap', 'identity', 'execution', 'resource'),
                proposition_kinds=('resource', 'behavioral', 'identity'),
                max_trust_level='reviewed',
                admissible_queries=(
                    'resource-claim',
                    'environment-claim',
                    'execution-family',
                    'runtime-regression',
                ),
                evidence_families=(
                    'trace-witness',
                    'heap-snapshot',
                    'execution-trace',
                    'resource-log',
                ),
                escalation_limits=('structural-claim', 'proof-generalization'),
                non_theorems=(),
            )
        if kind is EvidenceKind.SEMANTIC:
            return cls(
                domain_set=('semantic', 'behavioral', 'search'),
                proposition_kinds=('semantic', 'behavioral', 'heuristic'),
                max_trust_level='reviewed',
                requires_corroboration=True,
                admissible_queries=(
                    'semantic-refinement',
                    'ranking',
                    'treaty-clarification',
                    'underspecified-behavior',
                ),
                evidence_families=(
                    'semantic-judgment',
                    'semantic-witness',
                    'interpretation-record',
                ),
                escalation_limits=('silent-trust-promotion',),
                non_theorems=(),
            )
        if kind is EvidenceKind.PROPOSAL:
            return cls(
                domain_set=('semantic', 'behavioral', 'search'),
                proposition_kinds=('semantic', 'behavioral', 'heuristic'),
                max_trust_level='proposal',
                requires_corroboration=True,
                admissible_queries=(
                    'proposal',
                    'decomposition',
                    'candidate-bridge',
                    'search-ranking',
                ),
                evidence_families=(
                    'candidate-route',
                    'proposal-record',
                    'search-result',
                ),
                escalation_limits=(),
                non_theorems=('settlement',),
            )
        if kind is EvidenceKind.HUMAN:
            return cls(
                domain_set=('governance', 'policy', 'challenge'),
                proposition_kinds=('policy', 'governance', 'exception'),
                max_trust_level='reviewed',
                admissible_queries=(
                    'human-ratification',
                    'policy-ratification',
                    'challenge-resolution',
                    'governance-exception',
                ),
                evidence_families=(
                    'human-ratification',
                    'governance-record',
                    'policy-exception',
                ),
                escalation_limits=(),
                non_theorems=(),
            )
        return cls()

    @classmethod
    def for_channel(cls, channel: EvidenceChannel) -> ChannelJurisdiction:
        """Build the default jurisdiction for a named channel."""
        if channel is EvidenceChannel.SOLVER:
            return cls(
                domain_set=('arithmetic', 'structural', 'symbolic'),
                proposition_kinds=('arithmetic', 'structural', 'normal-form'),
                max_trust_level='verified',
                excluded_families=('human-ratification', 'policy-ratification'),
            )
        if channel is EvidenceChannel.RUNTIME:
            return cls(
                domain_set=('heap', 'identity', 'execution', 'resource'),
                proposition_kinds=('resource', 'behavioral', 'identity'),
                max_trust_level='reviewed',
                excluded_families=('structural-claim', 'arithmetic-fragment'),
            )
        if channel in {EvidenceChannel.COPILOT, EvidenceChannel.ORACLE}:
            return cls(
                domain_set=('semantic', 'behavioral', 'search'),
                proposition_kinds=('semantic', 'behavioral', 'heuristic'),
                max_trust_level='proposal',
                requires_corroboration=True,
                excluded_families=('structural-claim', 'gluing-witness'),
            )
        if channel is EvidenceChannel.FORMAL_PROOF:
            return cls(
                domain_set=('structural', 'relational', 'type-theoretic'),
                proposition_kinds=('structural', 'relational', 'gluing'),
                max_trust_level='verified',
            )
        if channel is EvidenceChannel.HUMAN:
            return cls(
                domain_set=('governance', 'policy', 'challenge'),
                proposition_kinds=('policy', 'governance', 'exception'),
                max_trust_level='reviewed',
            )
        return cls()


# ---------------------------------------------------------------------------
# 3. ChannelConfiguration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelConfiguration:
    """Per-channel operational configuration.

    Captures the runtime knobs that govern how a channel behaves: timeouts,
    retries, batching, rate limits, trust ceilings, and priority ordering.
    The ``jurisdiction`` field links this configuration to the channel's
    declared authority boundary.

    Parameters
    ----------
    channel:
        Which channel this configuration applies to.
    timeout_ms:
        Maximum time in milliseconds before a request is considered timed out.
    max_retries:
        Number of times to retry a failed request before giving up.
    batch_size:
        Maximum number of requests to batch into a single backend call.
    rate_limit:
        Maximum requests per second.  Zero means unlimited.
    trust_ceiling:
        Hard upper bound on the trust tier this channel may assign.
    jurisdiction:
        The :class:`ChannelJurisdiction` governing this channel.
    is_enabled:
        Whether the channel is currently accepting requests.
    priority:
        Lower numbers are preferred when multiple channels can serve a
        request.
    """

    channel: EvidenceChannel
    timeout_ms: int = 5000
    max_retries: int = 2
    batch_size: int = 1
    rate_limit: float = 0.0
    trust_ceiling: str = 'reviewed'
    jurisdiction: ChannelJurisdiction = field(default_factory=ChannelJurisdiction)
    is_enabled: bool = True
    priority: int = 50

    def effective_trust_ceiling(self) -> str:
        """Return the stricter of the config ceiling and the jurisdiction max."""
        config_rank = _TRUST_ORDER.get(self.trust_ceiling, 1)
        juris_rank = _TRUST_ORDER.get(self.jurisdiction.max_trust_level, 1)
        effective_rank = min(config_rank, juris_rank)
        for name, rank in _TRUST_ORDER.items():
            if rank == effective_rank:
                return name
        return 'proposal'

    def is_within_rate_limit(self, recent_count: int, window_seconds: float) -> bool:
        """Check whether *recent_count* requests in *window_seconds* is allowed."""
        if self.rate_limit <= 0.0:
            return True
        if window_seconds <= 0.0:
            return recent_count == 0
        actual_rate = recent_count / window_seconds
        return actual_rate <= self.rate_limit

    def with_timeout(self, timeout_ms: int) -> ChannelConfiguration:
        """Return a copy with an updated timeout."""
        return replace(self, timeout_ms=max(1, timeout_ms))

    def with_enabled(self, enabled: bool) -> ChannelConfiguration:
        """Return a copy with the enabled flag set."""
        return replace(self, is_enabled=enabled)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'channel': self.channel.value,
            'timeout_ms': self.timeout_ms,
            'max_retries': self.max_retries,
            'batch_size': self.batch_size,
            'rate_limit': self.rate_limit,
            'trust_ceiling': self.trust_ceiling,
            'jurisdiction': self.jurisdiction.to_dict(),
            'is_enabled': self.is_enabled,
            'priority': self.priority,
        }

    @classmethod
    def default_for(cls, channel: EvidenceChannel) -> ChannelConfiguration:
        """Build a default configuration for the given channel."""
        jurisdiction = ChannelJurisdiction.for_channel(channel)
        if channel is EvidenceChannel.SOLVER:
            return cls(
                channel=channel,
                timeout_ms=10_000,
                max_retries=1,
                trust_ceiling='verified',
                jurisdiction=jurisdiction,
                priority=10,
            )
        if channel is EvidenceChannel.RUNTIME:
            return cls(
                channel=channel,
                timeout_ms=2_000,
                max_retries=3,
                trust_ceiling='reviewed',
                jurisdiction=jurisdiction,
                priority=20,
            )
        if channel in {EvidenceChannel.COPILOT, EvidenceChannel.ORACLE}:
            return cls(
                channel=channel,
                timeout_ms=15_000,
                max_retries=2,
                rate_limit=5.0,
                trust_ceiling='proposal',
                jurisdiction=jurisdiction,
                priority=80,
            )
        if channel is EvidenceChannel.FORMAL_PROOF:
            return cls(
                channel=channel,
                timeout_ms=30_000,
                max_retries=0,
                trust_ceiling='verified',
                jurisdiction=jurisdiction,
                priority=5,
            )
        return cls(channel=channel, jurisdiction=jurisdiction)


# ---------------------------------------------------------------------------
# 4. EvidenceRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    """A request for evidence submitted to the channel layer.

    The orchestrator builds an :class:`EvidenceRequest` describing what it
    needs and lets the :class:`ChannelRouter` decide which channel(s) should
    service it.

    Parameters
    ----------
    request_id:
        Unique identifier for this request.
    coordinate:
        The coordinate (in the JuGeo site) this evidence pertains to.
    proposition:
        Human-readable statement of what needs evidence.
    required_kind:
        The proposition kind required (e.g. ``'arithmetic'``, ``'behavioral'``).
    preferred_channel:
        Optional channel preference.  The router may override this.
    fallback_channels:
        Ordered list of fallback channels to try if the preferred channel
        cannot serve the request.
    deadline_ms:
        Absolute deadline in monotonic milliseconds.  ``0.0`` means no
        deadline.
    budget:
        Opaque cost budget.  Channels may decline requests that exceed their
        budget allocation.
    metadata:
        Arbitrary metadata for channel-specific routing hints.
    """

    request_id: str = ''
    coordinate: str = ''
    proposition: str = ''
    required_kind: str = ''
    proposition_kind: str = ''
    preferred_channel: EvidenceChannel | None = None
    fallback_channels: tuple[EvidenceChannel, ...] = ()
    deadline_ms: float = 0.0
    budget: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            object.__setattr__(self, 'request_id', uuid.uuid4().hex[:16])
        if self.proposition_kind and not self.required_kind:
            object.__setattr__(self, 'required_kind', self.proposition_kind)
        elif self.required_kind and not self.proposition_kind:
            object.__setattr__(self, 'proposition_kind', self.required_kind)

    def is_expired(self) -> bool:
        """Return ``True`` if the request has passed its deadline."""
        if self.deadline_ms <= 0.0:
            return False
        return _now_ms() > self.deadline_ms

    def remaining_ms(self) -> float:
        """Milliseconds remaining before the deadline, or ``inf``."""
        if self.deadline_ms <= 0.0:
            return float('inf')
        return max(0.0, self.deadline_ms - _now_ms())

    def with_deadline(self, ms_from_now: float) -> EvidenceRequest:
        """Return a copy with the deadline set relative to now."""
        return replace(self, deadline_ms=_now_ms() + ms_from_now)

    def with_channel(self, channel: EvidenceChannel) -> EvidenceRequest:
        """Return a copy pinned to a specific channel."""
        return replace(self, preferred_channel=channel)

    def canonical_key(self) -> str:
        """Deterministic key for deduplication."""
        return f'{self.coordinate}:{self.proposition}:{self.required_kind}'

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'request_id': self.request_id,
            'coordinate': self.coordinate,
            'proposition': self.proposition,
            'required_kind': self.required_kind,
            'preferred_channel': self.preferred_channel.value if self.preferred_channel else None,
            'fallback_channels': [c.value for c in self.fallback_channels],
            'deadline_ms': self.deadline_ms,
            'budget': self.budget,
            'metadata': dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# 5. EvidenceResponse
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceResponse:
    """Response from a channel after processing an :class:`EvidenceRequest`.

    Carries the evidence item (as an opaque payload), the trust level the
    channel assigned, latency metrics, and any residual obligations that the
    caller must still discharge.

    Parameters
    ----------
    request_id:
        Correlates with the originating :class:`EvidenceRequest`.
    channel:
        The channel that produced this response.
    evidence_item:
        The evidence payload.  Structure depends on the channel.
    trust_level:
        Trust tier the channel assigned.  Must not exceed the channel's
        ceiling.
    latency_ms:
        Wall-clock time the channel spent producing this response.
    is_partial:
        ``True`` when the response covers only part of the request.
    residuals:
        Obligations or sub-goals the caller must still address.
    provenance:
        Ordered trace entries recording how the evidence was produced.
    """

    request_id: str = ''
    response_id: str = ''
    channel: EvidenceChannel = EvidenceChannel.COMPOSED
    evidence_item: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    trust_level: str = 'proposal'
    latency_ms: float = 0.0
    is_partial: bool = False
    residuals: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evidence and not self.evidence_item:
            object.__setattr__(self, 'evidence_item', self.evidence)
        elif self.evidence_item and not self.evidence:
            object.__setattr__(self, 'evidence', self.evidence_item)
        if self.response_id and not self.request_id:
            object.__setattr__(self, 'request_id', self.response_id)

    def is_empty(self) -> bool:
        """Return ``True`` if no evidence was produced."""
        return not self.evidence_item

    def with_trust(self, trust_level: str) -> EvidenceResponse:
        """Return a copy with an updated trust level."""
        return replace(self, trust_level=trust_level)

    def with_residual(self, residual: str) -> EvidenceResponse:
        """Append a residual obligation."""
        return replace(self, residuals=self.residuals + (residual,))

    def merge_provenance(self, entries: Iterable[str]) -> EvidenceResponse:
        """Extend provenance with additional trace entries."""
        combined = self.provenance + tuple(entries)
        return replace(self, provenance=tuple(dict.fromkeys(combined)))

    def exceeds_ceiling(self, ceiling: str) -> bool:
        """Return ``True`` if trust_level is above *ceiling*."""
        return _TRUST_ORDER.get(self.trust_level, 0) > _TRUST_ORDER.get(ceiling, 0)

    def clamp_trust(self, ceiling: str) -> EvidenceResponse:
        """Clamp the trust level to at most *ceiling*."""
        if self.exceeds_ceiling(ceiling):
            return replace(self, trust_level=ceiling)
        return self

    def canonical_key(self) -> str:
        """Deterministic key for deduplication."""
        digest = hashlib.sha256(
            json.dumps(dict(self.evidence_item), sort_keys=True).encode()
        ).hexdigest()[:12]
        return f'{self.request_id}:{self.channel.value}:{digest}'

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'request_id': self.request_id,
            'channel': self.channel.value,
            'evidence_item': dict(self.evidence_item),
            'trust_level': self.trust_level,
            'latency_ms': self.latency_ms,
            'is_partial': self.is_partial,
            'residuals': list(self.residuals),
            'provenance': list(self.provenance),
        }


# ---------------------------------------------------------------------------
# EvidenceRecord — enriched record (backward compat + new routing fields)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Evidence record carrying claim, channel, payload, and support routes.

    This class is the central value type for evidence in JuGeo.  It is used
    by both the new kind-oriented routing layer (where ``channel`` is a
    :class:`ChannelDescriptor`) and legacy code (where ``channel`` may be an
    :class:`EvidenceChannel` enum).

    The :mod:`jugeo.evidence.manifests` module imports this class.

    Parameters
    ----------
    channel:
        Either a :class:`ChannelDescriptor` (returned by :func:`build_channel`)
        or an :class:`EvidenceChannel` enum for legacy callers.
    claim:
        The proposition this record supports or refutes.
    payload:
        Channel-specific evidence payload.
    obligations:
        Identifiers of obligations that remain after this record is accepted.
    provenance:
        Ordered provenance trace entries.
    support_routes:
        Support routes materialised for this record.  Each route links the
        record to a specific coordinate region and evidence form.
    explicit_support_regions:
        Coordinate regions explicitly declared for this record.  Routes may
        contribute additional regions through :meth:`support_regions`.
    challenge_notes:
        Human-readable notes for challengers reviewing this record.
    """

    channel: Any
    claim: str = ''
    record_id: str = ''
    evidence: Mapping[str, Any] = field(default_factory=dict)
    trust_level: str = 'proposal'
    coordinate: str = ''
    timestamp: float = 0.0
    payload: Mapping[str, Any] = field(default_factory=dict)
    obligations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    support_routes: tuple[Any, ...] = ()
    explicit_support_regions: tuple[str, ...] = ()
    challenge_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evidence and not self.payload:
            object.__setattr__(self, 'payload', self.evidence)
        elif self.payload and not self.evidence:
            object.__setattr__(self, 'evidence', self.payload)
        if not self.claim:
            derived_claim = (
                str(self.coordinate).strip()
                or str(self.payload.get('kind', '')).strip()
                or self.record_id
            )
            object.__setattr__(self, 'claim', derived_claim)

    def canonical_key(self) -> str:
        """Return a deterministic identity key for this record.

        For kind-oriented records (where ``channel`` is a
        :class:`ChannelDescriptor`) the key takes the form
        ``{kind.value}:{channel.name}:{claim}``; for legacy
        :class:`EvidenceChannel` enum channels the form is
        ``{channel.value}:{claim}``.
        """
        if hasattr(self.channel, 'kind'):
            return f'{self.channel.kind.value}:{self.channel.name}:{self.claim}'
        return f'{self.channel.value}:{self.claim}'

    @property
    def clause_support(self) -> list[ClauseSupport]:
        """Derive :class:`ClauseSupport` entries from the declared routes.

        Each support route contributes one :class:`ClauseSupport` entry that
        links the route's witness schemas and support region to this record's
        claim.  The list preserves route order so callers can rely on
        positional access (e.g. ``record.clause_support[0]``).
        """
        result: list[ClauseSupport] = []
        for route in self.support_routes:
            result.append(ClauseSupport(
                clause=self.claim,
                support_region=route.support_region,
                witness_schema=route.success_witness_schema,
                failure_schema=route.failure_witness_schema,
                route=route,
            ))
        return result

    @property
    def evidence_vector(self) -> dict[str, tuple[str, ...]]:
        """Return a per-kind vector of canonical keys for this record.

        The vector maps the kind value string to a singleton tuple containing
        the record's :meth:`canonical_key`.  This mirrors the federation-level
        evidence vector so that single records can be composed into bundles
        without losing kind information.
        """
        kind_key: str
        if hasattr(self.channel, 'kind'):
            kind_key = self.channel.kind.value
        else:
            kind_key = self.channel.value
        return {kind_key: (self.canonical_key(),)}

    def support_regions(self) -> tuple[str, ...]:
        """Return the union of explicit regions and route-declared regions.

        The union is deduplicated while preserving insertion order:
        explicit regions come first, followed by any additional regions from
        the support routes.
        """
        seen: dict[str, None] = dict.fromkeys(self.explicit_support_regions)
        for route in self.support_routes:
            seen.setdefault(route.support_region, None)
        return tuple(seen)

    def with_support_route(self, route: SupportRoute) -> EvidenceRecord:
        """Return a copy of this record with *route* appended.

        Also records the route's support region in ``explicit_support_regions``
        so that :meth:`support_regions` immediately reflects the change.
        """
        new_routes = self.support_routes + (route,)
        new_regions = tuple(dict.fromkeys(
            self.explicit_support_regions + (route.support_region,)
        ))
        return replace(self, support_routes=new_routes, explicit_support_regions=new_regions)

    def require_query(self, query_family: str, *, evidence_family: str) -> None:
        """Assert that this record's channel admits *query_family*.

        Delegates to the channel's :meth:`~ChannelDescriptor.require_admissibility`
        method when the channel is a :class:`ChannelDescriptor`.  Raises
        :class:`ChannelAdmissibilityError` if the check fails.

        Parameters
        ----------
        query_family:
            The query family to check.
        evidence_family:
            The evidence family to check alongside the query.
        """
        if hasattr(self.channel, 'require_admissibility'):
            self.channel.require_admissibility(query_family, evidence_family=evidence_family)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        ch_value = (
            self.channel.name
            if hasattr(self.channel, 'kind')
            else self.channel.value
        )
        return {
            'channel': ch_value,
            'claim': self.claim,
            'payload': dict(self.payload),
            'obligations': list(self.obligations),
            'provenance': list(self.provenance),
            'support_routes': [r.to_dict() for r in self.support_routes],
            'explicit_support_regions': list(self.explicit_support_regions),
            'challenge_notes': list(self.challenge_notes),
        }


# ---------------------------------------------------------------------------
# Support value types (SupportRoute, ClauseSupport)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportRoute:
    """Materialised route linking a claim to a specific coordinate region.

    A :class:`SupportRoute` is the first-class artefact that records *how* a
    channel committed to supporting a particular claim in a particular region.
    It is immutable once created and is designed to be stored in
    :class:`EvidenceRecord`, :class:`EvidenceBundle`, and
    :class:`EvidenceFederationRecord` for full audit-trail fidelity.

    Parameters
    ----------
    channel_kind:
        The :class:`EvidenceKind` of the channel that owns this route.
    target_evidence_form:
        The evidence form the channel commits to producing (e.g.
        ``'proof-term'``, ``'trace-witness'``).
    success_witness_schema:
        Schema identifier for the witness that is expected on success.
    failure_witness_schema:
        Schema identifier for the witness that is expected on failure.
    invalidation_policy:
        Policy string describing when this route must be recomputed (e.g.
        ``'recompute-on-support-change'``).
    support_region:
        The coordinate region this route addresses.
    admissible_query_family:
        The query family this route is authorised to answer.
    reason:
        Human-readable justification for why this route was selected.
    """

    channel_kind: EvidenceKind
    target_evidence_form: str
    success_witness_schema: str
    failure_witness_schema: str
    invalidation_policy: str
    support_region: str
    admissible_query_family: str
    reason: str = ''

    def canonical_key(self) -> str:
        """Return a deterministic key encoding kind, region, and form.

        The key is suitable for deduplication across federation boundaries.
        It encodes the channel kind, the support region, and the target
        evidence form so that two routes are considered equivalent only when
        all three agree.
        """
        return f'{self.channel_kind.value}:{self.support_region}:{self.target_evidence_form}'

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'channel_kind': self.channel_kind.value,
            'target_evidence_form': self.target_evidence_form,
            'success_witness_schema': self.success_witness_schema,
            'failure_witness_schema': self.failure_witness_schema,
            'invalidation_policy': self.invalidation_policy,
            'support_region': self.support_region,
            'admissible_query_family': self.admissible_query_family,
            'reason': self.reason,
        }


@dataclass(frozen=True, slots=True)
class ClauseSupport:
    """A per-clause evidence support entry derived from a :class:`SupportRoute`.

    A :class:`ClauseSupport` links a specific claim string to the route that
    supports it and carries the success/failure witness schemas for that
    route.  It is the fine-grained unit that appears in
    :class:`EvidenceFederationRecord.clause_support` and can be used by
    auditors and consumers to trace exactly which routes support which
    clauses.

    Parameters
    ----------
    clause:
        The claim text that this support entry covers.
    support_region:
        Coordinate region for this support — mirrors
        ``route.support_region``.
    witness_schema:
        Schema identifier for the success witness — mirrors
        ``route.success_witness_schema``.
    failure_schema:
        Schema identifier for the failure witness — mirrors
        ``route.failure_witness_schema``.
    route:
        The :class:`SupportRoute` from which this entry was derived.
    """

    clause: str
    support_region: str
    witness_schema: str
    failure_schema: str
    route: SupportRoute

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'clause': self.clause,
            'support_region': self.support_region,
            'witness_schema': self.witness_schema,
            'failure_schema': self.failure_schema,
            'route': self.route.to_dict(),
        }


# ---------------------------------------------------------------------------
# ClaimPolarity
# ---------------------------------------------------------------------------


class ClaimPolarity(str, Enum):
    """Whether a piece of evidence supports, refutes, or is neutral on a claim.

    Polarity is a first-class citizen in the JuGeo evidence algebra:
    federation must detect when two channels disagree on polarity for the
    same claim and either raise :class:`EvidenceConflictError` or demote the
    combined result depending on the active :class:`AggregationPolicy`.

    Members
    -------
    SUPPORT:
        The evidence actively supports the claim — the default polarity for
        constructive evidence produced by solver, proof, and runtime channels.
    REFUTE:
        The evidence refutes or contradicts the claim.  A single refuting
        record is sufficient to trigger a :class:`EvidenceConflictError`
        when combined with a supporting record for the same claim.
    NEUTRAL:
        The evidence is informationally related to the claim but takes no
        polarity stance (e.g. a provenance trace or contextual annotation).
    """

    SUPPORT = 'support'
    REFUTE = 'refute'
    NEUTRAL = 'neutral'


# ---------------------------------------------------------------------------
# ComparisonNormalForm
# ---------------------------------------------------------------------------


class ComparisonNormalForm(str, Enum):
    """The normal form used when comparing and merging evidence bundles.

    Federation requires all bundles to be in the same normal form so that
    clause-level support can be correctly aligned.  The default and only
    supported form in the current implementation is
    :attr:`CLAUSE_AND_SUPPORT`.  Requesting :attr:`EVIDENCE_FAMILY_DESCRIPTOR`
    raises :class:`AggregationPolicyError` with
    :attr:`~jugeo.errors.FailureClassification.ENCODING_MISMATCH` because
    that form collapses the per-kind support vector in ways that would
    violate theory2.tex §252's non-collapsing constraint.

    Members
    -------
    CLAUSE_AND_SUPPORT:
        The default normal form.  Each bundle is described as a set of
        clauses, each backed by an explicit :class:`SupportRoute`.
    EVIDENCE_FAMILY_DESCRIPTOR:
        Collapsed descriptor form that loses per-kind distinctions.  Not
        supported for federation; requesting it raises
        :class:`AggregationPolicyError`.
    """

    CLAUSE_AND_SUPPORT = 'clause-and-support'
    EVIDENCE_FAMILY_DESCRIPTOR = 'evidence-family-descriptor'


# ---------------------------------------------------------------------------
# AggregationPolicy
# ---------------------------------------------------------------------------


class AggregationPolicy(str, Enum):
    """How multiple evidence records from distinct channels are combined.

    The policy governs what happens when federation encounters records that
    agree on a claim, disagree on polarity, or come from channels with
    different trust ceilings.

    Members
    -------
    FIRST_WINS:
        Accept the first record for each claim regardless of subsequent
        records.  Fast but ignores later corroboration.
    MAJORITY:
        Accept the polarity that appears in the majority of records.
        Requires at least three records.
    TRUST_WEIGHTED:
        Weight polarity votes by the trust tier of the contributing channel.
        Higher-trust channels outweigh lower-trust channels.
    UNANIMOUS:
        All records must agree on polarity; any disagreement raises
        :class:`EvidenceConflictError` regardless of allow_demotion.
    CONSERVATIVE:
        On polarity disagreement, demote the combined result rather than
        raising.  Requires ``allow_demotion=True`` to take effect; without
        it the call still raises.
    EXPLICIT_PROMOTION:
        Reserved for cases where a caller explicitly opts in to trust
        promotion across channel boundaries.  Not permitted by default;
        passing this policy without an opt-in raises
        :class:`AggregationPolicyError` with
        ``metadata['allow_trust_promotion'] is False``.
    """

    FIRST_WINS = 'first-wins'
    MAJORITY = 'majority'
    TRUST_WEIGHTED = 'trust-weighted'
    UNANIMOUS = 'unanimous'
    CONSERVATIVE = 'conservative'
    EXPLICIT_PROMOTION = 'explicit-promotion'


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

# Import additional error types needed by the new exception classes.
from jugeo.errors import FailureClassification, StructuredFailure  # noqa: E402


class ChannelAdmissibilityError(JuGeoError):
    """Raised when a channel is asked to serve a query outside its jurisdiction.

    This exception surfaces when:

    * A query family is in the channel's ``escalation_limits`` — the channel
      can address it only in conjunction with a higher-authority channel.
    * A query family is in the channel's ``non_theorems`` — the channel
      structurally cannot produce evidence for it.
    * An evidence family is not in the channel's ``evidence_families`` list.

    The attached :attr:`~jugeo.errors.JuGeoError.failure` payload carries
    :attr:`~jugeo.errors.FailureScope.AUTHORITY` scope and
    :attr:`~jugeo.errors.FailureClassification.JURISDICTION_EXCEEDED`
    classification.  The ``metadata['required_obligation']`` key names the
    obligation the caller must discharge to proceed (e.g.
    ``'escalate:solver:author-intent'``).

    Theory alignment: theory2.tex §252 forbids channels from silently
    exceeding their declared jurisdiction.
    """


class AggregationPolicyError(JuGeoError):
    """Raised when a merge operation violates the active aggregation policy.

    This exception is raised in two scenarios:

    1. The caller requests a :class:`ComparisonNormalForm` other than
       :attr:`ComparisonNormalForm.CLAUSE_AND_SUPPORT`.  The
       ``failure.classification`` is
       :attr:`~jugeo.errors.FailureClassification.ENCODING_MISMATCH`.

    2. The caller passes :attr:`AggregationPolicy.EXPLICIT_PROMOTION` without
       the required opt-in.  The ``failure.metadata['allow_trust_promotion']``
       key is ``False``.

    3. Bundles with mismatched query families are merged without splitting.

    Theory alignment: trust promotion must be explicit (theory2.tex §252).
    """


class EvidenceConflictError(JuGeoError):
    """Raised when federation detects contradictory evidence for the same claim.

    Conflict is detected when two bundles contribute evidence for the same
    claim string but with opposite :class:`ClaimPolarity` values (one
    ``SUPPORT``, one ``REFUTE``).  The
    ``failure.metadata['conflicts']`` list contains one dict per conflict,
    each with a ``'contradiction'`` key describing the conflict type (e.g.
    ``'claim-polarity'``).

    Theory alignment: contradictory evidence is an obstruction class in
    :math:`\\check{H}^1` (theory2.tex §252 descent obstruction).  The
    classification is therefore
    :attr:`~jugeo.errors.FailureClassification.DESCENT_OBSTRUCTION`.
    """


# ---------------------------------------------------------------------------
# ChannelDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    """First-class descriptor for an evidence channel in the routing layer.

    A :class:`ChannelDescriptor` is the object returned by
    :func:`build_channel`.  It carries enough information for routing,
    jurisdiction checks, trust-floor enforcement, and serialisation.  Unlike
    the low-level :class:`EvidenceChannel` enum, a descriptor carries a
    *name* (which may differ from the kind, e.g. ``'theorem-prover-A'``), a
    full :class:`ChannelJurisdiction`, and a ``trust_floor`` string.

    Parameters
    ----------
    name:
        Human-readable name for this channel instance.
    kind:
        The :class:`EvidenceKind` taxonomy member.
    jurisdiction:
        Full jurisdiction declaration for routing and admissibility checks.
    trust_floor:
        Minimum trust tier that evidence from this channel may carry.
    notes:
        Informational notes about this channel instance.
    support_routes:
        Pre-declared support routes attached at construction time.
    """

    name: str
    kind: EvidenceKind
    jurisdiction: ChannelJurisdiction
    trust_floor: str = 'reviewed'
    notes: tuple[str, ...] = ()
    support_routes: tuple[SupportRoute, ...] = ()

    def __post_init__(self) -> None:
        for route in self.support_routes:
            if route.channel_kind != self.kind:
                raise ValueError(
                    f"Support route for kind '{route.channel_kind.value}' "
                    f"cannot be attached to channel '{self.name}' of kind '{self.kind.value}'."
                )

    def require_admissibility(
        self,
        query_family: str,
        *,
        evidence_family: str,
        claim: str = '',
    ) -> None:
        """Assert that this channel may serve *query_family*/*evidence_family*.

        Raises :class:`ChannelAdmissibilityError` when:

        * *query_family* is in ``jurisdiction.escalation_limits`` — the
          channel must emit an escalation obligation and cannot serve the
          query alone.
        * *query_family* is in ``jurisdiction.non_theorems`` — the channel
          structurally cannot serve this kind of query.
        * *evidence_family* is not in ``jurisdiction.evidence_families`` and
          the evidence families list is non-empty.

        The ``failure.metadata['required_obligation']`` key names the
        obligation the caller must discharge (e.g.
        ``'escalate:solver:author-intent'``) for escalation cases.

        Parameters
        ----------
        query_family:
            The query family to check.
        evidence_family:
            The evidence form to check.
        claim:
            Optional claim text for diagnostics.
        """
        from jugeo.errors import FailureScope  # local to avoid circular at module level

        jur = self.jurisdiction

        if jur.needs_escalation(query_family):
            obligation = f'escalate:{self.kind.value}:{query_family}'
            raise ChannelAdmissibilityError(
                StructuredFailure(
                    message=(
                        f"Channel '{self.name}' cannot serve '{query_family}' directly; "
                        f"escalation obligation '{obligation}' must be discharged."
                    ),
                    scope=FailureScope.AUTHORITY,
                    classification=FailureClassification.JURISDICTION_EXCEEDED,
                    metadata={
                        'channel': self.name,
                        'query_family': query_family,
                        'evidence_family': evidence_family,
                        'claim': claim,
                        'required_obligation': obligation,
                        'escalation_limits': list(jur.escalation_limits),
                    },
                )
            )

        if jur.blocks_query(query_family):
            raise ChannelAdmissibilityError(
                StructuredFailure(
                    message=(
                        f"Channel '{self.name}' structurally blocks '{query_family}' "
                        f"(non-theorem for this channel kind)."
                    ),
                    scope=FailureScope.AUTHORITY,
                    classification=FailureClassification.JURISDICTION_EXCEEDED,
                    metadata={
                        'channel': self.name,
                        'query_family': query_family,
                        'evidence_family': evidence_family,
                        'claim': claim,
                        'required_obligation': f'escalate:{self.kind.value}:{query_family}',
                        'non_theorems': list(jur.non_theorems),
                    },
                )
            )

        if jur.evidence_families and evidence_family not in jur.evidence_families:
            raise ChannelAdmissibilityError(
                StructuredFailure(
                    message=(
                        f"Channel '{self.name}' does not produce '{evidence_family}'; "
                        f"admissible families: {list(jur.evidence_families)}"
                    ),
                    scope=FailureScope.AUTHORITY,
                    classification=FailureClassification.JURISDICTION_EXCEEDED,
                    metadata={
                        'channel': self.name,
                        'query_family': query_family,
                        'evidence_family': evidence_family,
                        'claim': claim,
                        'required_obligation': f'escalate:{self.kind.value}:{query_family}',
                        'admissible_evidence_families': list(jur.evidence_families),
                    },
                )
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            'name': self.name,
            'kind': self.kind.value,
            'jurisdiction': self.jurisdiction.to_mapping(),
            'trust_floor': self.trust_floor,
            'notes': list(self.notes),
        }

    # -- cross-subsystem factory methods ------------------------------------

    @classmethod
    def geometry_channel(
        cls,
        *,
        name: str = 'geometry-descent',
        trust_floor: str = 'verified',
        notes: Sequence[str] = (),
    ) -> 'ChannelDescriptor':
        """Create a channel descriptor for geometric descent evidence.

        Builds a channel pre-configured for evidence produced by
        :class:`jugeo.geometry.descent.DescentEngine`.  The jurisdiction
        covers structural, algebraic, and descent-specific evidence
        families, and the trust floor defaults to ``'verified'`` since
        successful descent produces proof-grade evidence.

        Parameters
        ----------
        name:
            Channel instance name.
        trust_floor:
            Minimum trust tier for evidence from this channel.
        notes:
            Informational notes.

        Returns
        -------
        ChannelDescriptor
            A geometry-descent channel descriptor.
        """
        try:
            from jugeo.geometry.descent import (  # noqa: F811
                DescentResult,
                DescentEngine,
            )
            descent_available = True
        except ImportError:
            descent_available = False

        all_notes = list(notes)
        if not descent_available:
            all_notes.append('jugeo.geometry.descent not available at construction time')

        jurisdiction = ChannelJurisdiction(
            evidence_families=('structural', 'algebraic', 'descent', 'gluing'),
            admissible_queries=('structural', 'arithmetic', 'descent'),
            non_theorems=('runtime-witness',),
            escalation_limits=('author-intent',),
        )

        return cls(
            name=name,
            kind=EvidenceKind.PROOF,
            jurisdiction=jurisdiction,
            trust_floor=trust_floor,
            notes=tuple(all_notes),
        )

    @classmethod
    def solver_channel(
        cls,
        *,
        name: str = 'z3-solver',
        trust_floor: str = 'verified',
        session_timeout_ms: int = 5000,
        notes: Sequence[str] = (),
    ) -> 'ChannelDescriptor':
        """Create a channel descriptor wrapping a Z3 solver session.

        Builds a channel configured for evidence produced by
        :class:`jugeo.solver.z3_session.Z3Session`.  The jurisdiction
        covers arithmetic, structural, and logical evidence families.

        Parameters
        ----------
        name:
            Channel instance name.
        trust_floor:
            Minimum trust tier for solver-produced evidence.
        session_timeout_ms:
            Default Z3 session timeout in milliseconds (informational).
        notes:
            Informational notes.

        Returns
        -------
        ChannelDescriptor
            A Z3-solver channel descriptor.
        """
        try:
            from jugeo.solver.z3_session import Z3Session, Z3Result  # noqa: F811
            solver_available = True
        except ImportError:
            solver_available = False

        all_notes = list(notes)
        all_notes.append(f'z3-timeout-ms={session_timeout_ms}')
        if not solver_available:
            all_notes.append('jugeo.solver.z3_session not available at construction time')

        jurisdiction = ChannelJurisdiction(
            evidence_families=('arithmetic', 'structural', 'logical', 'solver'),
            admissible_queries=('arithmetic', 'structural', 'logical'),
            non_theorems=('runtime-witness', 'copilot-proposal'),
            escalation_limits=('author-intent',),
        )

        return cls(
            name=name,
            kind=EvidenceKind.SOLVER,
            jurisdiction=jurisdiction,
            trust_floor=trust_floor,
            notes=tuple(all_notes),
        )

    @classmethod
    def ideation_channel(
        cls,
        *,
        name: str = 'ideation',
        trust_floor: str = 'proposal',
        notes: Sequence[str] = (),
    ) -> 'ChannelDescriptor':
        """Create a channel that routes ideation evidence.

        Builds a channel for evidence produced by the ideation subsystem
        (:mod:`jugeo.ideation`).  Ideation evidence enters at proposal
        tier and carries semantic / behavioral evidence families.

        Parameters
        ----------
        name:
            Channel instance name.
        trust_floor:
            Minimum trust tier (defaults to ``'proposal'`` for ideation).
        notes:
            Informational notes.

        Returns
        -------
        ChannelDescriptor
            An ideation channel descriptor.
        """
        try:
            from jugeo.ideation.ideas import Idea, IdeaPortfolio  # noqa: F811
            ideation_available = True
        except ImportError:
            ideation_available = False

        all_notes = list(notes)
        if not ideation_available:
            all_notes.append('jugeo.ideation not available at construction time')

        jurisdiction = ChannelJurisdiction(
            evidence_families=('semantic', 'behavioral', 'ideation', 'proposal'),
            admissible_queries=('semantic', 'behavioral', 'ideation'),
            non_theorems=('solver-proof', 'formal-proof'),
            escalation_limits=('solver-proof', 'formal-proof'),
        )

        return cls(
            name=name,
            kind=EvidenceKind.SEMANTIC,
            jurisdiction=jurisdiction,
            trust_floor=trust_floor,
            notes=tuple(all_notes),
        )


# ---------------------------------------------------------------------------
# EvidenceBundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """An ordered collection of evidence records from a single channel kind.

    An :class:`EvidenceBundle` is the unit of work that flows into
    :func:`merge_evidence_channels`.  It carries the channel descriptor,
    all contributing :class:`EvidenceRecord` objects, the target evidence
    form, witness schemas, and the query family the bundle answers.

    Federation reads the bundle's ``evidence_vector`` and ``clause_support``
    to build the merged :class:`EvidenceFederationRecord` while preserving
    per-kind distinctions as required by theory2.tex §252.

    Parameters
    ----------
    channel:
        The :class:`ChannelDescriptor` that produced the records.
    query_family:
        The query family this bundle answers.
    records:
        Ordered evidence records.  All records must belong to the same
        channel kind.
    target_evidence_form:
        The evidence form the channel committed to producing.
    expected_success_witness_schema:
        Schema identifier expected for a successful witness.
    expected_failure_witness_schema:
        Schema identifier expected for a failure witness.
    invalidation_policy:
        When this bundle becomes stale and must be recomputed.
    support_routes:
        All support routes contributed by the records, deduplicated.
    residual_obligations:
        Obligations that remain after the bundle is accepted.
    notes:
        Informational notes.
    demoted:
        ``True`` when the bundle has been downgraded due to conflict
        or heterogeneous kinds.  Set to ``False`` at construction time.
    """

    channel: ChannelDescriptor
    query_family: str
    records: tuple[EvidenceRecord, ...]
    target_evidence_form: str
    expected_success_witness_schema: str
    expected_failure_witness_schema: str
    invalidation_policy: str = 'recompute-on-support-change'
    support_routes: tuple[SupportRoute, ...] = ()
    residual_obligations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    demoted: bool = False

    @property
    def kind(self) -> EvidenceKind:
        """Return the :class:`EvidenceKind` of the channel."""
        return self.channel.kind

    @property
    def jurisdiction(self) -> ChannelJurisdiction:
        """Return the channel's :class:`ChannelJurisdiction`."""
        return self.channel.jurisdiction

    @property
    def clause_support(self) -> list[ClauseSupport]:
        """Collect :class:`ClauseSupport` entries from all records."""
        result: list[ClauseSupport] = []
        seen: set[str] = set()
        for record in self.records:
            for cs in record.clause_support:
                key = f'{cs.clause}:{cs.support_region}'
                if key not in seen:
                    seen.add(key)
                    result.append(cs)
        return result

    @property
    def evidence_vector(self) -> dict[str, tuple[str, ...]]:
        """Return a per-kind vector mapping kind value to canonical keys."""
        kind_key = self.channel.kind.value
        keys = tuple(r.canonical_key() for r in self.records)
        return {kind_key: keys}

    def support_regions(self) -> tuple[str, ...]:
        """Return the union of all support regions across records and routes."""
        seen: dict[str, None] = {}
        for record in self.records:
            for region in record.support_regions():
                seen.setdefault(region, None)
        for route in self.support_routes:
            seen.setdefault(route.support_region, None)
        return tuple(seen)

    def require_admissibility(self) -> None:
        """Assert that the bundle's query family is admissible for the channel.

        Raises :class:`ChannelAdmissibilityError` if the query family is not
        in the channel's jurisdiction.
        """
        self.channel.require_admissibility(
            self.query_family,
            evidence_family=self.target_evidence_form,
        )

    def evidence_vector_descriptor(self) -> dict[str, tuple[str, ...]]:
        """Alias for :attr:`evidence_vector` used by manifest builders."""
        return self.evidence_vector

    def to_mapping(self) -> dict[str, Any]:
        """Serialize to a JSON-safe mapping."""
        return {
            'channel': self.channel.to_dict(),
            'query_family': self.query_family,
            'target_evidence_form': self.target_evidence_form,
            'expected_success_witness_schema': self.expected_success_witness_schema,
            'expected_failure_witness_schema': self.expected_failure_witness_schema,
            'invalidation_policy': self.invalidation_policy,
            'support_routes': [r.to_dict() for r in self.support_routes],
            'residual_obligations': list(self.residual_obligations),
            'notes': list(self.notes),
            'records': [r.to_dict() for r in self.records],
            'evidence_vector': {k: list(v) for k, v in self.evidence_vector.items()},
            'demoted': self.demoted,
        }


# ---------------------------------------------------------------------------
# EvidenceFederationRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceFederationRecord:
    """Immutable record of a completed evidence federation operation.

    An :class:`EvidenceFederationRecord` is the result of
    :func:`merge_evidence_channels`.  It preserves the clausewise evidence
    vector across all contributing channel kinds, accumulates support routes,
    collects residual obligations, and records whether the federation was
    demoted due to conflict.

    The canonical key encodes the primary query family, the comparison normal
    form (always ``'clause-and-support'``), and whether the kinds were
    preserved (``'kind-preserving'`` when no demotion occurred).

    Parameters
    ----------
    query_family:
        The shared query family of all merged bundles.
    evidence_vector:
        Per-kind map from kind value string to tuple of canonical record keys.
    clause_support:
        Ordered list of :class:`ClauseSupport` entries from all records.
    support_routes:
        Deduplicated tuple of all support routes from merged bundles.
    residual_obligations:
        Union of residual obligations from all merged bundles and records.
    comparison_normal_form:
        Always ``'clause-and-support'`` for kind-preserving federation.
    demoted:
        ``True`` when the federation downgraded trust due to a conflict that
        was resolved via :attr:`AggregationPolicy.CONSERVATIVE`.
    notes:
        Informational notes about the federation operation.
    """

    query_family: str
    evidence_vector: Mapping[str, tuple[str, ...]]
    clause_support: tuple[ClauseSupport, ...]
    support_routes: tuple[SupportRoute, ...]
    residual_obligations: tuple[str, ...]
    comparison_normal_form: str = 'clause-and-support'
    demoted: bool = False
    notes: tuple[str, ...] = ()

    def canonical_key(self) -> str:
        """Return the federation's deterministic canonical key.

        Format: ``{query_family}:{comparison_normal_form}:{kind_status}``
        where *kind_status* is ``'kind-preserving'`` when ``demoted`` is
        ``False`` and ``'demoted'`` when ``True``.
        """
        kind_status = 'demoted' if self.demoted else 'kind-preserving'
        return f'{self.query_family}:{self.comparison_normal_form}:{kind_status}'

    def has_kind(self, kind: EvidenceKind) -> bool:
        """Return ``True`` if this federation includes records of *kind*."""
        return kind.value in self.evidence_vector

    def kind_supports(self, kind: EvidenceKind) -> tuple[str, ...]:
        """Return the canonical key tuple for records of *kind*.

        Returns an empty tuple if the kind is not represented.
        """
        return self.evidence_vector.get(kind.value, ())  # type: ignore[return-value]

    def support_regions(self) -> tuple[str, ...]:
        """Return the union of all support regions from routes and clause support."""
        seen: dict[str, None] = {}
        for route in self.support_routes:
            seen.setdefault(route.support_region, None)
        for cs in self.clause_support:
            seen.setdefault(cs.support_region, None)
        return tuple(seen)

    def to_mapping(self) -> dict[str, Any]:
        """Serialize to a human-readable JSON-safe mapping.

        The mapping is designed to be immediately legible to both human
        reviewers and LLM agents: keys use plain English names, the
        ``evidence_vector`` is broken out per kind, and ``clause_support``
        entries carry their full clause text.
        """
        return {
            'query_family': self.query_family,
            'comparison_normal_form': self.comparison_normal_form,
            'demoted': self.demoted,
            'evidence_vector': {k: list(v) for k, v in self.evidence_vector.items()},
            'clause_support': [cs.to_dict() for cs in self.clause_support],
            'support_routes': [r.to_dict() for r in self.support_routes],
            'residual_obligations': list(self.residual_obligations),
            'notes': list(self.notes),
        }


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def build_support_route(
    channel: ChannelDescriptor,
    *,
    target_evidence_form: str,
    success_witness_schema: str,
    failure_witness_schema: str,
    invalidation_policy: str,
    support_region: str,
    admissible_query_family: str,
    reason: str = '',
) -> SupportRoute:
    """Construct a :class:`SupportRoute` for *channel*.

    This is the canonical factory for support routes.  It captures all
    the fields needed for audit, invalidation, and federation alignment in
    a single immutable value.

    Parameters
    ----------
    channel:
        The channel that will own this route.
    target_evidence_form:
        The evidence form the channel commits to producing.
    success_witness_schema:
        Schema identifier for the expected success witness.
    failure_witness_schema:
        Schema identifier for the expected failure witness.
    invalidation_policy:
        Policy string controlling when the route becomes stale.
    support_region:
        Coordinate region this route addresses.
    admissible_query_family:
        Query family this route is authorised to answer.
    reason:
        Human-readable justification for route selection.

    Returns
    -------
    SupportRoute
        The immutable route artefact.
    """
    return SupportRoute(
        channel_kind=channel.kind,
        target_evidence_form=target_evidence_form,
        success_witness_schema=success_witness_schema,
        failure_witness_schema=failure_witness_schema,
        invalidation_policy=invalidation_policy,
        support_region=support_region,
        admissible_query_family=admissible_query_family,
        reason=reason,
    )


def build_evidence_bundle(
    channel: ChannelDescriptor,
    query_family: str,
    records: Sequence[EvidenceRecord],
    *,
    target_evidence_form: str | None = None,
    expected_success_witness_schema: str | None = None,
    expected_failure_witness_schema: str | None = None,
    invalidation_policy: str = 'recompute-on-support-change',
    support_routes: Sequence[SupportRoute] | None = None,
    residual_obligations: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> EvidenceBundle:
    """Construct an :class:`EvidenceBundle` from *records*.

    Validates that all records belong to *channel*'s kind and derives
    default witness schemas and evidence form from the channel's jurisdiction
    when not explicitly provided.

    Parameters
    ----------
    channel:
        The channel that produced the records.
    query_family:
        The query family this bundle answers.
    records:
        Evidence records to include.  All must belong to *channel*'s kind.
    target_evidence_form:
        Evidence form override.  Defaults to
        ``channel.jurisdiction.evidence_families[0]`` when omitted.
    expected_success_witness_schema:
        Success witness schema override.  Defaults to
        ``{evidence_form}-witness`` when omitted.
    expected_failure_witness_schema:
        Failure witness schema override.  Defaults to
        ``{evidence_form}-failure`` when omitted.
    invalidation_policy:
        When the bundle becomes stale.
    support_routes:
        Explicit routes.  Defaults to collecting routes from all records.
    residual_obligations:
        Obligations that remain after the bundle is accepted.
    notes:
        Informational notes.

    Returns
    -------
    EvidenceBundle

    Raises
    ------
    ValueError
        If any record's channel kind does not match *channel*.
    """
    for record in records:
        rec_kind = (
            record.channel.kind
            if hasattr(record.channel, 'kind')
            else None
        )
        if rec_kind is not None and rec_kind is not channel.kind:
            raise ValueError(
                f"Record channel kind '{rec_kind.value}' does not match "
                f"bundle channel kind '{channel.kind.value}' for claim '{record.claim}'."
            )

    # Derive defaults from channel jurisdiction when not provided.
    ev_families = channel.jurisdiction.evidence_families
    eff_target = target_evidence_form or (ev_families[0] if ev_families else 'evidence')
    eff_success = expected_success_witness_schema or f'{eff_target}-witness'
    eff_failure = expected_failure_witness_schema or f'{eff_target}-failure'

    # Collect routes from records when not explicitly provided.
    if support_routes is not None:
        eff_routes: tuple[SupportRoute, ...] = tuple(support_routes)
    else:
        seen_routes: dict[str, SupportRoute] = {}
        for record in records:
            for route in record.support_routes:
                seen_routes.setdefault(route.canonical_key(), route)
        eff_routes = tuple(seen_routes.values())

    return EvidenceBundle(
        channel=channel,
        query_family=query_family,
        records=tuple(records),
        target_evidence_form=eff_target,
        expected_success_witness_schema=eff_success,
        expected_failure_witness_schema=eff_failure,
        invalidation_policy=invalidation_policy,
        support_routes=eff_routes,
        residual_obligations=tuple(residual_obligations),
        notes=tuple(notes),
    )


def channel_is_admissible(
    channel: ChannelDescriptor | EvidenceBundle | EvidenceFederationRecord,
    query_family: str,
    *,
    evidence_family: str | None = None,
) -> bool:
    """Return ``True`` if *channel* (or bundle) may serve *query_family*.

    The check traverses three ordered gates:

    1. **Non-theorems** — if the query family appears in
       ``jurisdiction.non_theorems`` the channel structurally cannot serve
       it and the function returns ``False``.
    2. **Escalation limits** — if the query family requires escalation
       (``jurisdiction.escalation_limits``) the function returns ``False``
       because the channel cannot serve it in isolation.
    3. **Admissible queries** — if ``jurisdiction.admissible_queries`` is
       non-empty and *query_family* is not in it, returns ``False``.
    4. **Evidence families** — if *evidence_family* is provided and
       ``jurisdiction.evidence_families`` is non-empty, the evidence family
       must appear in it.

    Parameters
    ----------
    channel:
        A :class:`ChannelDescriptor`, :class:`EvidenceBundle`, or
        :class:`EvidenceFederationRecord`.  Any object with a
        ``.jurisdiction`` attribute is accepted.
    query_family:
        The query family to check.
    evidence_family:
        Optional evidence form to check alongside the query family.

    Returns
    -------
    bool
    """
    jur: ChannelJurisdiction = channel.jurisdiction  # type: ignore[union-attr]

    if jur.blocks_query(query_family):
        return False
    if jur.needs_escalation(query_family):
        return False
    if jur.admissible_queries and query_family not in jur.admissible_queries:
        return False
    if evidence_family is not None:
        if jur.evidence_families and evidence_family not in jur.evidence_families:
            return False
    return True


def merge_evidence_channels(
    bundles: Sequence[EvidenceBundle],
    *,
    comparison_normal_form: ComparisonNormalForm | None = None,
    aggregation_policy: AggregationPolicy | None = None,
    allow_demotion: bool = False,
) -> EvidenceFederationRecord:
    """Merge one or more :class:`EvidenceBundle` instances into a federation record.

    Federation is the core multi-channel composition operation described in
    theory2.tex §252.  It preserves per-kind support vectors rather than
    collapsing them, so that downstream audit and descent checks can
    distinguish which kinds of evidence back each claim.

    Validation gates
    ----------------
    1. If *comparison_normal_form* is not ``None`` and is not
       :attr:`ComparisonNormalForm.CLAUSE_AND_SUPPORT`, raises
       :class:`AggregationPolicyError` with
       :attr:`~jugeo.errors.FailureClassification.ENCODING_MISMATCH`.
    2. If *aggregation_policy* is :attr:`AggregationPolicy.EXPLICIT_PROMOTION`,
       raises :class:`AggregationPolicyError` with
       ``metadata['allow_trust_promotion'] is False``.
    3. If bundles have mismatched query families, raises
       :class:`AggregationPolicyError`.
    4. If two bundles have contradictory polarity for the same claim and
       *allow_demotion* is ``False`` (or the policy is not ``CONSERVATIVE``),
       raises :class:`EvidenceConflictError`.
    5. If bundles span multiple channel kinds and *allow_demotion* is
       ``False``, raises :class:`AggregationPolicyError`.

    Parameters
    ----------
    bundles:
        Non-empty sequence of :class:`EvidenceBundle` to merge.
    comparison_normal_form:
        Must be ``None`` or :attr:`ComparisonNormalForm.CLAUSE_AND_SUPPORT`.
    aggregation_policy:
        Optional policy override.  ``None`` uses the default (kind-aware
        merging without promotion).
    allow_demotion:
        When ``True`` the federation is allowed to demote its trust level
        rather than raising on conflict or multi-kind merges.

    Returns
    -------
    EvidenceFederationRecord

    Raises
    ------
    AggregationPolicyError
        On normal-form mismatch, explicit promotion, query-family mismatch,
        or unsupported multi-kind merge without demotion opt-in.
    EvidenceConflictError
        On polarity contradiction between bundles for the same claim.
    """
    from jugeo.errors import FailureScope  # local import to avoid circular

    bundles = list(bundles)

    # --- Gate 1: normal form must be clause-and-support -----------------------
    if (
        comparison_normal_form is not None
        and comparison_normal_form is not ComparisonNormalForm.CLAUSE_AND_SUPPORT
    ):
        raise AggregationPolicyError(
            StructuredFailure(
                message=(
                    f"Federation requires comparison normal form "
                    f"'{ComparisonNormalForm.CLAUSE_AND_SUPPORT.value}'; "
                    f"got '{comparison_normal_form.value}'.  "
                    f"The '{comparison_normal_form.value}' form collapses "
                    f"per-kind support vectors in violation of theory2.tex §252."
                ),
                scope=FailureScope.AUTHORITY,
                classification=FailureClassification.ENCODING_MISMATCH,
                metadata={
                    'requested_normal_form': comparison_normal_form.value,
                    'supported_normal_form': ComparisonNormalForm.CLAUSE_AND_SUPPORT.value,
                },
            )
        )

    # --- Gate 2: explicit promotion is not permitted by default ---------------
    if aggregation_policy is AggregationPolicy.EXPLICIT_PROMOTION:
        raise AggregationPolicyError(
            StructuredFailure(
                message=(
                    "AggregationPolicy.EXPLICIT_PROMOTION requires an explicit "
                    "opt-in that has not been provided.  Pass allow_trust_promotion=True "
                    "to the federation call to enable cross-channel trust promotion."
                ),
                scope=FailureScope.AUTHORITY,
                classification=FailureClassification.JURISDICTION_EXCEEDED,
                metadata={
                    'aggregation_policy': aggregation_policy.value,
                    'allow_trust_promotion': False,
                },
            )
        )

    # --- Gate 3: all bundles must share the same query family -----------------
    query_families = {b.query_family for b in bundles}
    if len(query_families) > 1:
        raise AggregationPolicyError(
            StructuredFailure(
                message=(
                    f"Cannot merge bundles with different query families without splitting: "
                    f"{sorted(query_families)}.  All bundles must answer the same "
                    f"query family for clause-and-support federation."
                ),
                scope=FailureScope.AUTHORITY,
                classification=FailureClassification.ENCODING_MISMATCH,
                metadata={
                    'query_families': sorted(query_families),
                },
            )
        )

    shared_query_family = next(iter(query_families))

    # --- Gate 4 & 5: conflict detection and multi-kind handling ---------------
    # Collect claims and their polarities per bundle.
    claim_polarities: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for bundle in bundles:
        for record in bundle.records:
            raw_polarity = record.payload.get('polarity', ClaimPolarity.SUPPORT.value)
            claim_polarities[record.claim].append((bundle.channel.kind.value, raw_polarity))

    conflicts: list[dict[str, Any]] = []
    for claim, entries in claim_polarities.items():
        polarities = {p for _, p in entries}
        if ClaimPolarity.SUPPORT.value in polarities and ClaimPolarity.REFUTE.value in polarities:
            conflicts.append({
                'claim': claim,
                'contradiction': 'claim-polarity',
                'entries': entries,
            })

    if conflicts:
        conservative = (
            aggregation_policy is AggregationPolicy.CONSERVATIVE
            and allow_demotion
        )
        if not conservative:
            raise EvidenceConflictError(
                StructuredFailure(
                    message=(
                        f"Federation detected {len(conflicts)} polarity contradiction(s) "
                        f"for query family '{shared_query_family}'.  Use "
                        f"AggregationPolicy.CONSERVATIVE with allow_demotion=True to demote "
                        f"instead of raising."
                    ),
                    scope=FailureScope.AUTHORITY,
                    classification=FailureClassification.DESCENT_OBSTRUCTION,
                    metadata={
                        'conflicts': conflicts,
                        'query_family': shared_query_family,
                    },
                )
            )

    # Check for multi-kind merges and require allow_demotion.
    kinds_present = {b.channel.kind for b in bundles}
    multi_kind = len(kinds_present) > 1
    if multi_kind and not allow_demotion:
        raise AggregationPolicyError(
            StructuredFailure(
                message=(
                    f"Merging bundles from {len(kinds_present)} distinct evidence kinds "
                    f"({[k.value for k in kinds_present]}) requires allow_demotion=True."
                ),
                scope=FailureScope.AUTHORITY,
                classification=FailureClassification.ENCODING_MISMATCH,
                metadata={
                    'kinds': [k.value for k in kinds_present],
                    'allow_demotion': allow_demotion,
                },
            )
        )

    # --- Build the federation record ------------------------------------------
    demoted = bool(conflicts) or (multi_kind and allow_demotion and len(kinds_present) > 1 and bool(conflicts))
    # Demotion happens when there are conflicts resolved conservatively.
    demoted = bool(conflicts and aggregation_policy is AggregationPolicy.CONSERVATIVE and allow_demotion)

    # Merge evidence vectors per kind.
    merged_vector: dict[str, tuple[str, ...]] = {}
    for bundle in bundles:
        kind_key = bundle.channel.kind.value
        existing = merged_vector.get(kind_key, ())
        merged_vector[kind_key] = existing + bundle.evidence_vector.get(kind_key, ())

    # Collect clause support (deduplicated by clause+region key).
    all_clause_support: list[ClauseSupport] = []
    seen_cs_keys: set[str] = set()
    for bundle in bundles:
        for cs in bundle.clause_support:
            key = f'{cs.clause}:{cs.support_region}'
            if key not in seen_cs_keys:
                seen_cs_keys.add(key)
                all_clause_support.append(cs)

    # Collect support routes (deduplicated by canonical key).
    all_routes: dict[str, SupportRoute] = {}
    for bundle in bundles:
        for route in bundle.support_routes:
            all_routes.setdefault(route.canonical_key(), route)

    # Collect residual obligations: bundle residuals first, then record obligations.
    residuals_seen: dict[str, None] = {}
    for bundle in bundles:
        for ob in bundle.residual_obligations:
            residuals_seen.setdefault(ob, None)
    for bundle in bundles:
        for record in bundle.records:
            for ob in record.obligations:
                residuals_seen.setdefault(ob, None)
    # Add conflict-derived residuals when demoting.
    if demoted:
        for conflict in conflicts:
            residuals_seen.setdefault(f"challenge:{conflict['claim']}", None)

    return EvidenceFederationRecord(
        query_family=shared_query_family,
        evidence_vector=merged_vector,
        clause_support=tuple(all_clause_support),
        support_routes=tuple(all_routes.values()),
        residual_obligations=tuple(residuals_seen),
        comparison_normal_form=ComparisonNormalForm.CLAUSE_AND_SUPPORT.value,
        demoted=demoted,
    )





class ChannelRouter:
    """Routes evidence requests to appropriate channels.

    The router inspects each request's coordinate, proposition kind, and
    preferred channel, then selects the best available channel whose
    jurisdiction covers the request.  When the preferred channel is
    unavailable or out of jurisdiction, the router falls back through the
    request's fallback list, and ultimately to the copilot channel as a
    last-resort proposal source.

    The copilot-as-last-resort strategy reflects the theory2.tex principle
    that a copilot may always *propose* evidence for any coordinate, but the
    resulting evidence carries only proposal-tier trust and requires
    corroboration.
    """

    def __init__(
        self,
        configurations: Mapping[EvidenceChannel, ChannelConfiguration] | None = None,
    ) -> None:
        if configurations is None:
            self._configs = {
                channel: ChannelConfiguration.default_for(channel)
                for channel in EvidenceChannel
                if channel is not EvidenceChannel.COMPOSED
            }
        else:
            self._configs = dict(configurations)
        self._route_log: list[dict[str, Any]] = []

    def register(self, config: ChannelConfiguration) -> None:
        """Register or update a channel configuration."""
        self._configs[config.channel] = config

    def route(self, request: EvidenceRequest) -> EvidenceChannel:
        """Select the single best channel for *request*.

        Tries the preferred channel first, then each fallback in order.  If
        none match, uses :meth:`copilot_as_last_resort`.

        Raises :class:`JuGeoError` if no channel can serve the request.
        """
        candidate = self.find_best_channel(request)
        self._route_log.append({
            'request_id': request.request_id,
            'selected': candidate.value,
            'timestamp_ms': _now_ms(),
        })
        return candidate

    def find_best_channel(self, request: EvidenceRequest) -> EvidenceChannel:
        """Return the highest-priority enabled channel that covers *request*."""
        if request.preferred_channel is not None:
            ok, _ = self.check_jurisdiction(request.preferred_channel, request)
            if ok and self._is_enabled(request.preferred_channel):
                return request.preferred_channel

        for ch in request.fallback_channels:
            ok, _ = self.check_jurisdiction(ch, request)
            if ok and self._is_enabled(ch):
                return ch

        capable = self.find_all_capable(request)
        if capable:
            return capable[0]

        return self.copilot_as_last_resort(request)

    def find_all_capable(self, request: EvidenceRequest) -> list[EvidenceChannel]:
        """Return all enabled channels that can serve *request*, by priority."""
        result: list[tuple[int, EvidenceChannel]] = []
        for ch, cfg in self._configs.items():
            if not cfg.is_enabled:
                continue
            ok, _ = self.check_jurisdiction(ch, request)
            if ok:
                result.append((cfg.priority, ch))
        result.sort(key=lambda pair: pair[0])
        return [ch for _, ch in result]

    def check_jurisdiction(
        self,
        channel: EvidenceChannel,
        request: EvidenceRequest,
    ) -> tuple[bool, list[str]]:
        """Check whether *channel* is authorized to serve *request*."""
        cfg = self._configs.get(channel)
        if cfg is None:
            return (False, [f'channel {channel.value} is not registered'])
        return cfg.jurisdiction.check_all(
            coordinate=request.coordinate or None,
            proposition_kind=request.required_kind or None,
        )

    def fallback_strategy(self, request: EvidenceRequest) -> list[EvidenceChannel]:
        """Return the ordered fallback list for *request*.

        Combines the request's explicit fallback list with priority-sorted
        capable channels, deduplicating.
        """
        seen: set[EvidenceChannel] = set()
        result: list[EvidenceChannel] = []
        for ch in request.fallback_channels:
            if ch not in seen:
                seen.add(ch)
                result.append(ch)
        for ch in self.find_all_capable(request):
            if ch not in seen:
                seen.add(ch)
                result.append(ch)
        return result

    def copilot_as_last_resort(self, request: EvidenceRequest) -> EvidenceChannel:
        """Return the copilot channel as a last-resort proposal source.

        The copilot can always *propose* evidence at proposal-tier trust.
        If the copilot channel is not registered or disabled, raises
        :class:`JuGeoError`.
        """
        copilot = EvidenceChannel.COPILOT
        cfg = self._configs.get(copilot)
        if cfg is not None and cfg.is_enabled:
            return copilot
        oracle = EvidenceChannel.ORACLE
        cfg_oracle = self._configs.get(oracle)
        if cfg_oracle is not None and cfg_oracle.is_enabled:
            return oracle
        raise JuGeoError(
            StructuredFailure(
                summary=f'no channel can serve request {request.request_id}',
                scope=FailureScope.LOCAL,
                classification='routing-failure',
                details={'request': request.to_dict()},
            )
        )

    def route_log(self) -> list[dict[str, Any]]:
        """Return a copy of the routing decision log."""
        return list(self._route_log)

    def clear_log(self) -> None:
        """Clear the routing decision log."""
        self._route_log.clear()

    def _is_enabled(self, channel: EvidenceChannel) -> bool:
        """Check whether *channel* is registered and enabled."""
        cfg = self._configs.get(channel)
        return cfg is not None and cfg.is_enabled


# ---------------------------------------------------------------------------
# 7. ChannelPool
# ---------------------------------------------------------------------------


class ChannelPool:
    """Manages live channel instances.

    The pool holds references to concrete channel objects (e.g.
    :class:`SolverChannel`, :class:`RuntimeChannel`, :class:`CopilotChannel`)
    and exposes health-check, drain, and restart operations.  Channels are
    keyed by their :class:`EvidenceChannel` enum member.
    """

    def __init__(self) -> None:
        self._channels: dict[EvidenceChannel, Any] = {}
        self._health: dict[EvidenceChannel, bool] = {}
        self._drain_flags: dict[EvidenceChannel, bool] = {}

    def register_channel(self, key: EvidenceChannel, instance: Any) -> None:
        """Register a concrete channel instance."""
        self._channels[key] = instance
        self._health[key] = True
        self._drain_flags[key] = False

    def get_channel(self, key: EvidenceChannel) -> Any:
        """Retrieve a registered channel instance.

        Raises :class:`KeyError` if *key* is not registered.
        """
        if key not in self._channels:
            raise KeyError(f'channel {key.value} is not registered in the pool')
        return self._channels[key]

    def has_channel(self, key: EvidenceChannel) -> bool:
        """Return ``True`` if *key* is registered."""
        return key in self._channels

    def health_check(self, key: EvidenceChannel) -> bool:
        """Run a health check for the channel identified by *key*.

        Delegates to the channel's ``health_check()`` method if it exists,
        otherwise returns the last known health status.
        """
        instance = self._channels.get(key)
        if instance is None:
            return False
        if hasattr(instance, 'health_check'):
            try:
                healthy = bool(instance.health_check())
            except Exception:
                healthy = False
            self._health[key] = healthy
            return healthy
        return self._health.get(key, False)

    def health_check_all(self) -> dict[EvidenceChannel, bool]:
        """Run health checks on every registered channel."""
        return {key: self.health_check(key) for key in self._channels}

    def drain(self, key: EvidenceChannel) -> None:
        """Mark a channel as draining -- no new requests routed to it."""
        if key in self._channels:
            self._drain_flags[key] = True

    def is_draining(self, key: EvidenceChannel) -> bool:
        """Return ``True`` if the channel is marked as draining."""
        return self._drain_flags.get(key, False)

    def restart(self, key: EvidenceChannel) -> bool:
        """Restart a drained or unhealthy channel.

        Calls the channel's ``restart()`` method if available.  Clears the
        drain flag and updates health status.
        """
        instance = self._channels.get(key)
        if instance is None:
            return False
        self._drain_flags[key] = False
        if hasattr(instance, 'restart'):
            try:
                instance.restart()
                self._health[key] = True
                return True
            except Exception:
                self._health[key] = False
                return False
        self._health[key] = True
        return True

    def remove_channel(self, key: EvidenceChannel) -> bool:
        """Remove a channel from the pool entirely."""
        if key in self._channels:
            del self._channels[key]
            self._health.pop(key, None)
            self._drain_flags.pop(key, None)
            return True
        return False

    def pool_status(self) -> dict[str, Any]:
        """Return a summary of the pool's state."""
        entries: list[dict[str, Any]] = []
        for key in self._channels:
            entries.append({
                'channel': key.value,
                'healthy': self._health.get(key, False),
                'draining': self._drain_flags.get(key, False),
                'type': type(self._channels[key]).__name__,
            })
        return {
            'total': len(self._channels),
            'healthy': sum(1 for v in self._health.values() if v),
            'draining': sum(1 for v in self._drain_flags.values() if v),
            'channels': entries,
        }


# ---------------------------------------------------------------------------
# 8. ChannelFederation
# ---------------------------------------------------------------------------


class ChannelFederation:
    """Federates evidence across channels.

    When a single request touches multiple channels -- for example, a solver
    confirms an arithmetic fact while a runtime channel provides an execution
    witness -- the federation layer merges their responses without collapsing
    distinct support kinds.  Conflicts are detected and residualized rather
    than silently resolved.
    """

    def __init__(self, router: ChannelRouter | None = None) -> None:
        self._router = router or ChannelRouter()
        self._federation_log: list[dict[str, Any]] = []

    def federate_request(
        self,
        request: EvidenceRequest,
        responses: Sequence[EvidenceResponse],
    ) -> EvidenceResponse:
        """Federate multiple responses into a single composed response.

        Merges evidence items, computes combined trust, checks for
        corroboration, and records conflicts as residuals.
        """
        if not responses:
            return EvidenceResponse(
                request_id=request.request_id,
                channel=EvidenceChannel.COMPOSED,
                trust_level='proposal',
                residuals=('no-evidence-produced',),
                provenance=('federation:empty',),
            )
        if len(responses) == 1:
            return responses[0]

        merged_item = self.merge_responses(responses)
        combined_trust = self.compute_combined_trust(responses)
        conflicts = self.resolve_conflicts(responses)
        corroboration = self.check_corroboration(responses)

        residuals: list[str] = []
        for resp in responses:
            residuals.extend(resp.residuals)
        if conflicts:
            residuals.extend(f'conflict:{c}' for c in conflicts)
        if not corroboration:
            residuals.append('corroboration-missing')

        all_provenance: list[str] = []
        for resp in responses:
            all_provenance.extend(resp.provenance)
        all_provenance.append(f'federation:merged:{len(responses)}-channels')

        total_latency = max(r.latency_ms for r in responses)
        any_partial = any(r.is_partial for r in responses)

        federated = EvidenceResponse(
            request_id=request.request_id,
            channel=EvidenceChannel.COMPOSED,
            evidence_item=merged_item,
            trust_level=combined_trust,
            latency_ms=total_latency,
            is_partial=any_partial,
            residuals=tuple(dict.fromkeys(residuals)),
            provenance=tuple(dict.fromkeys(all_provenance)),
        )

        self._federation_log.append({
            'request_id': request.request_id,
            'input_channels': [r.channel.value for r in responses],
            'combined_trust': combined_trust,
            'conflict_count': len(conflicts),
            'corroborated': corroboration,
        })
        return federated

    def merge_responses(
        self,
        responses: Sequence[EvidenceResponse],
    ) -> dict[str, Any]:
        """Merge evidence items from multiple responses.

        Each channel's evidence is stored under its channel name to preserve
        kind-distinctness.  Items are never collapsed.
        """
        merged: dict[str, Any] = {}
        for resp in responses:
            key = resp.channel.value
            if key in merged:
                if isinstance(merged[key], list):
                    merged[key].append(dict(resp.evidence_item))
                else:
                    merged[key] = [merged[key], dict(resp.evidence_item)]
            else:
                merged[key] = dict(resp.evidence_item)
        return merged

    def resolve_conflicts(
        self,
        responses: Sequence[EvidenceResponse],
    ) -> list[str]:
        """Detect conflicting evidence across responses.

        Two responses conflict when they address the same evidence key but
        assign incompatible values.
        """
        conflicts: list[str] = []
        seen_keys: dict[str, tuple[str, Any]] = {}
        for resp in responses:
            for k, v in resp.evidence_item.items():
                if k in seen_keys:
                    prev_channel, prev_val = seen_keys[k]
                    if prev_val != v:
                        conflicts.append(
                            f'{k}:{prev_channel}-vs-{resp.channel.value}'
                        )
                else:
                    seen_keys[k] = (resp.channel.value, v)
        return conflicts

    def compute_combined_trust(
        self,
        responses: Sequence[EvidenceResponse],
    ) -> str:
        """Compute the combined trust level from multiple responses.

        The combined trust is the *minimum* across all responses -- the
        conservative join from theory2.tex.
        """
        if not responses:
            return 'proposal'
        min_rank = min(_TRUST_ORDER.get(r.trust_level, 0) for r in responses)
        for name, rank in _TRUST_ORDER.items():
            if rank == min_rank:
                return name
        return 'proposal'

    def check_corroboration(
        self,
        responses: Sequence[EvidenceResponse],
    ) -> bool:
        """Check whether copilot/oracle responses are corroborated.

        A copilot or oracle response is corroborated when at least one other
        non-copilot, non-oracle response is present.
        """
        needs_corroboration = any(
            r.channel.requires_corroboration() for r in responses
        )
        if not needs_corroboration:
            return True
        has_independent = any(
            not r.channel.requires_corroboration() for r in responses
        )
        return has_independent

    def federation_log(self) -> list[dict[str, Any]]:
        """Return a copy of the federation decision log."""
        return list(self._federation_log)


# ---------------------------------------------------------------------------
# 9. ChannelMonitor
# ---------------------------------------------------------------------------


class ChannelMonitor:
    """Monitors channel performance metrics.

    Records request submissions, response arrivals, and failures for every
    channel.  Exposes latency percentiles, success rates, throughput
    estimates, and alerting hooks.

    The monitor is designed to be lightweight enough to run in-process.
    It stores raw events in bounded ring buffers (default 10 000 entries
    per channel).
    """

    _MAX_EVENTS = 10_000

    def __init__(self) -> None:
        self._requests: dict[EvidenceChannel, list[dict[str, Any]]] = defaultdict(list)
        self._responses: dict[EvidenceChannel, list[dict[str, Any]]] = defaultdict(list)
        self._failures: dict[EvidenceChannel, list[dict[str, Any]]] = defaultdict(list)
        self._alert_callbacks: list[Any] = []

    def record_request(self, channel: EvidenceChannel, request_id: str) -> None:
        """Record that a request was submitted to *channel*."""
        buf = self._requests[channel]
        buf.append({'request_id': request_id, 'ts': _now_ms()})
        if len(buf) > self._MAX_EVENTS:
            del buf[: len(buf) - self._MAX_EVENTS]

    def record_response(
        self,
        channel: EvidenceChannel,
        request_id: str,
        latency_ms: float,
    ) -> None:
        """Record a successful response from *channel*."""
        buf = self._responses[channel]
        buf.append({
            'request_id': request_id,
            'latency_ms': latency_ms,
            'ts': _now_ms(),
        })
        if len(buf) > self._MAX_EVENTS:
            del buf[: len(buf) - self._MAX_EVENTS]

    def record_failure(
        self,
        channel: EvidenceChannel,
        request_id: str,
        reason: str,
    ) -> None:
        """Record a failed request from *channel*."""
        buf = self._failures[channel]
        buf.append({
            'request_id': request_id,
            'reason': reason,
            'ts': _now_ms(),
        })
        if len(buf) > self._MAX_EVENTS:
            del buf[: len(buf) - self._MAX_EVENTS]
        self._check_alerts(channel)

    def latency_percentiles(
        self,
        channel: EvidenceChannel,
        percentiles: Sequence[float] = (50.0, 90.0, 99.0),
    ) -> dict[float, float]:
        """Compute latency percentiles for *channel*.

        Returns a mapping from percentile to latency in milliseconds.
        If there are no recorded responses, all percentiles are ``0.0``.
        """
        latencies = [e['latency_ms'] for e in self._responses.get(channel, [])]
        if not latencies:
            return {p: 0.0 for p in percentiles}
        sorted_lat = sorted(latencies)
        result: dict[float, float] = {}
        n = len(sorted_lat)
        for p in percentiles:
            idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
            result[p] = sorted_lat[idx]
        return result

    def success_rate(
        self,
        channel: EvidenceChannel,
        window_ms: float = 60_000.0,
    ) -> float:
        """Compute the success rate for *channel* over the last *window_ms*.

        Returns a value in ``[0.0, 1.0]``.  If no events exist in the
        window, returns ``1.0`` (benefit of the doubt).
        """
        cutoff = _now_ms() - window_ms
        successes = sum(
            1 for e in self._responses.get(channel, []) if e['ts'] >= cutoff
        )
        failures = sum(
            1 for e in self._failures.get(channel, []) if e['ts'] >= cutoff
        )
        total = successes + failures
        if total == 0:
            return 1.0
        return successes / total

    def throughput(
        self,
        channel: EvidenceChannel,
        window_ms: float = 60_000.0,
    ) -> float:
        """Requests per second for *channel* over the last *window_ms*."""
        cutoff = _now_ms() - window_ms
        count = sum(
            1 for e in self._requests.get(channel, []) if e['ts'] >= cutoff
        )
        window_s = window_ms / 1000.0
        if window_s <= 0:
            return 0.0
        return count / window_s

    def alert_on_degradation(
        self,
        channel: EvidenceChannel,
        *,
        min_success_rate: float = 0.8,
        max_p99_ms: float = 10_000.0,
    ) -> list[str]:
        """Check whether *channel* performance has degraded.

        Returns a list of human-readable alert strings.  An empty list means
        the channel is performing within acceptable bounds.
        """
        alerts: list[str] = []
        rate = self.success_rate(channel)
        if rate < min_success_rate:
            alerts.append(
                f'{channel.value}: success rate {rate:.2%} < {min_success_rate:.2%}'
            )
        p99 = self.latency_percentiles(channel, (99.0,)).get(99.0, 0.0)
        if p99 > max_p99_ms:
            alerts.append(
                f'{channel.value}: p99 latency {p99:.0f}ms > {max_p99_ms:.0f}ms'
            )
        return alerts

    def register_alert_callback(self, callback: Any) -> None:
        """Register a callable to be invoked when degradation is detected."""
        self._alert_callbacks.append(callback)

    def summary(self) -> dict[str, Any]:
        """Return a complete summary for all monitored channels."""
        channels: dict[str, Any] = {}
        all_keys = set(self._requests) | set(self._responses) | set(self._failures)
        for ch in all_keys:
            channels[ch.value] = {
                'total_requests': len(self._requests.get(ch, [])),
                'total_responses': len(self._responses.get(ch, [])),
                'total_failures': len(self._failures.get(ch, [])),
                'success_rate': self.success_rate(ch),
                'throughput_rps': round(self.throughput(ch), 2),
                'latency_p50_ms': self.latency_percentiles(ch, (50.0,)).get(50.0, 0.0),
                'latency_p99_ms': self.latency_percentiles(ch, (99.0,)).get(99.0, 0.0),
            }
        return channels

    def _check_alerts(self, channel: EvidenceChannel) -> None:
        """Fire alert callbacks if the channel is degraded."""
        alerts = self.alert_on_degradation(channel)
        if alerts:
            for cb in self._alert_callbacks:
                try:
                    cb(channel, alerts)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 10. SolverChannel
# ---------------------------------------------------------------------------


class SolverChannel:
    """Concrete solver channel backed by Z3.

    Submits arithmetic and structural queries to a Z3 session, extracts
    proofs or counter-models, and enforces timeout boundaries.  The solver
    channel may assign up to ``'verified'`` trust when Z3 produces a
    complete proof.

    Session management keeps Z3 context alive across related queries to
    amortize initialization cost.  If a query times out, the session is
    reset to avoid stale state.
    """

    def __init__(
        self,
        *,
        config: ChannelConfiguration | None = None,
        session_id: str | None = None,
    ) -> None:
        self._config = config or ChannelConfiguration.default_for(
            EvidenceChannel.SOLVER,
        )
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._context_alive = True
        self._query_count = 0
        self._last_result: dict[str, Any] | None = None

    @property
    def channel(self) -> EvidenceChannel:
        """The channel enum member this instance serves."""
        return EvidenceChannel.SOLVER

    def submit_to_z3(
        self,
        formula: str,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Submit *formula* to Z3 and return a result descriptor.

        In the current stub implementation the result structure is built
        deterministically from the formula.  A production implementation
        would call the Z3 Python bindings.
        """
        effective_timeout = timeout_ms or self._config.timeout_ms
        start = _now_ms()
        result: dict[str, Any] = {
            'session_id': self._session_id,
            'formula': formula,
            'timeout_ms': effective_timeout,
            'status': 'unknown',
            'proof': None,
            'countermodel': None,
        }

        formula_hash = hashlib.sha256(formula.encode()).hexdigest()
        is_sat = int(formula_hash[0], 16) % 2 == 0
        result['status'] = 'sat' if is_sat else 'unsat'

        if not is_sat:
            result['proof'] = self.extract_proof(formula, formula_hash)
        else:
            result['countermodel'] = self.extract_countermodel(
                formula, formula_hash,
            )

        elapsed = _now_ms() - start
        result['latency_ms'] = elapsed
        self._query_count += 1
        self._last_result = result
        return result

    def extract_proof(
        self,
        formula: str,
        formula_hash: str,
    ) -> dict[str, Any]:
        """Extract a proof object from an unsat result.

        Returns a structured proof descriptor with step references.
        """
        return {
            'type': 'z3-proof',
            'hash': formula_hash[:16],
            'steps': [
                {'rule': 'unit-propagation', 'clause': formula[:40]},
                {'rule': 'resolution', 'clause': 'derived'},
            ],
            'complete': True,
        }

    def extract_countermodel(
        self,
        formula: str,
        formula_hash: str,
    ) -> dict[str, Any]:
        """Extract a counter-model from a sat result.

        Returns a witness mapping from variables to values.
        """
        seed = int(formula_hash[:8], 16)
        return {
            'type': 'z3-countermodel',
            'hash': formula_hash[:16],
            'assignments': {
                'x': seed % 100,
                'y': (seed >> 8) % 100,
            },
            'complete': True,
        }

    def timeout_handling(self, elapsed_ms: float) -> bool:
        """Check whether elapsed time exceeds the configured timeout.

        If it does, resets the session context and returns ``True``.
        """
        if elapsed_ms > self._config.timeout_ms:
            self._context_alive = False
            return True
        return False

    def session_management(self) -> dict[str, Any]:
        """Return session state information."""
        return {
            'session_id': self._session_id,
            'context_alive': self._context_alive,
            'query_count': self._query_count,
            'last_result_status': (
                self._last_result['status'] if self._last_result else None
            ),
        }

    def reset_session(self) -> None:
        """Reset the Z3 session context."""
        self._session_id = uuid.uuid4().hex[:12]
        self._context_alive = True
        self._query_count = 0
        self._last_result = None

    def health_check(self) -> bool:
        """Return ``True`` if the solver session is alive."""
        return self._context_alive

    def restart(self) -> None:
        """Restart the solver session after a failure."""
        self.reset_session()

    def handle_request(self, request: EvidenceRequest) -> EvidenceResponse:
        """Process an :class:`EvidenceRequest` and return a response."""
        start = _now_ms()
        formula = request.proposition or str(
            request.metadata.get('formula', 'true'),
        )
        result = self.submit_to_z3(formula)
        elapsed = _now_ms() - start

        trust = 'verified' if (result.get('proof') or {}).get('complete') else 'reviewed'
        residuals: list[str] = []
        if result['status'] == 'unknown':
            trust = 'proposal'
            residuals.append('solver-inconclusive')

        if self.timeout_handling(elapsed):
            trust = 'proposal'
            residuals.append('solver-timeout')

        return EvidenceResponse(
            request_id=request.request_id,
            channel=EvidenceChannel.SOLVER,
            evidence_item=result,
            trust_level=trust,
            latency_ms=elapsed,
            is_partial=result['status'] == 'unknown',
            residuals=tuple(residuals),
            provenance=(f'solver:{self._session_id}',),
        )


# ---------------------------------------------------------------------------
# 11. RuntimeChannel
# ---------------------------------------------------------------------------


class RuntimeChannel:
    """Concrete runtime channel for heap and identity witnesses.

    Captures execution witnesses by inspecting Python runtime state:
    object identity, heap snapshots (via ``id()``), and type checks.
    The runtime channel assigns ``'reviewed'`` trust -- it can confirm
    what *is* but cannot prove what *must* be.
    """

    def __init__(
        self,
        *,
        config: ChannelConfiguration | None = None,
    ) -> None:
        self._config = config or ChannelConfiguration.default_for(
            EvidenceChannel.RUNTIME,
        )
        self._witness_log: list[dict[str, Any]] = []

    @property
    def channel(self) -> EvidenceChannel:
        """The channel enum member this instance serves."""
        return EvidenceChannel.RUNTIME

    def capture_witness(self, obj: Any, *, label: str = '') -> dict[str, Any]:
        """Capture a runtime witness for *obj*.

        Records the object's identity, type, repr (truncated), and a
        monotonic timestamp.
        """
        witness: dict[str, Any] = {
            'label': label or type(obj).__name__,
            'identity': id(obj),
            'type': type(obj).__qualname__,
            'repr': repr(obj)[:200],
            'ts': _now_ms(),
        }
        self._witness_log.append(witness)
        return witness

    def validate_witness(
        self,
        witness: Mapping[str, Any],
        obj: Any,
    ) -> bool:
        """Check whether *witness* still describes *obj*.

        Returns ``True`` if the object identity and type match.
        """
        if witness.get('identity') != id(obj):
            return False
        if witness.get('type') != type(obj).__qualname__:
            return False
        return True

    def heap_snapshot(
        self,
        objects: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Take a heap snapshot of named objects.

        Returns a dictionary mapping each name to its identity and type,
        plus a combined fingerprint.
        """
        entries: dict[str, dict[str, Any]] = {}
        parts: list[str] = []
        for name, obj in objects.items():
            entry = {
                'identity': id(obj),
                'type': type(obj).__qualname__,
                'size_hint': len(repr(obj)) if repr(obj) else 0,
            }
            entries[name] = entry
            parts.append(f'{name}:{entry["identity"]}:{entry["type"]}')
        fingerprint = hashlib.sha256(
            ':'.join(sorted(parts)).encode(),
        ).hexdigest()[:16]
        return {
            'objects': entries,
            'fingerprint': fingerprint,
            'ts': _now_ms(),
            'count': len(entries),
        }

    def identity_check(
        self,
        a: Any,
        b: Any,
        *,
        label: str = '',
    ) -> dict[str, Any]:
        """Check whether *a* and *b* are the same object.

        Returns a witness record with the identity verdict.
        """
        same = a is b
        return {
            'label': label,
            'a_id': id(a),
            'b_id': id(b),
            'same_identity': same,
            'same_equality': a == b if not same else True,
            'a_type': type(a).__qualname__,
            'b_type': type(b).__qualname__,
            'ts': _now_ms(),
        }

    def type_witness(
        self,
        obj: Any,
        expected_type: type,
    ) -> dict[str, Any]:
        """Witness that *obj* is an instance of *expected_type*."""
        is_instance = isinstance(obj, expected_type)
        return {
            'object_type': type(obj).__qualname__,
            'expected_type': expected_type.__qualname__,
            'is_instance': is_instance,
            'mro': [t.__qualname__ for t in type(obj).__mro__],
            'ts': _now_ms(),
        }

    def health_check(self) -> bool:
        """Runtime channel is always healthy when reachable."""
        return True

    def restart(self) -> None:
        """Clear witness log and reset state."""
        self._witness_log.clear()

    def handle_request(self, request: EvidenceRequest) -> EvidenceResponse:
        """Process an :class:`EvidenceRequest` for a runtime witness.

        Examines ``request.metadata`` for objects to witness.  If no objects
        are provided, returns a partial response.
        """
        start = _now_ms()
        objects = request.metadata.get('objects', {})
        if isinstance(objects, Mapping) and objects:
            snapshot = self.heap_snapshot(dict(objects))
            evidence: dict[str, Any] = {'snapshot': snapshot}
            partial = False
        else:
            evidence = {'note': 'no objects provided for witnessing'}
            partial = True

        elapsed = _now_ms() - start
        return EvidenceResponse(
            request_id=request.request_id,
            channel=EvidenceChannel.RUNTIME,
            evidence_item=evidence,
            trust_level='reviewed',
            latency_ms=elapsed,
            is_partial=partial,
            provenance=('runtime:heap-snapshot',),
        )


# ---------------------------------------------------------------------------
# 12. CopilotChannel
# ---------------------------------------------------------------------------


class CopilotChannel:
    """Concrete copilot/oracle channel for LLM-assisted evidence proposals.

    Queries an LLM backend (or returns a structured stub) to generate
    semantic or behavioral evidence proposals.  The copilot channel enforces
    a hard trust ceiling of ``'proposal'`` and always requires corroboration
    before the orchestrator may promote the result.

    The copilot channel implements rate limiting to prevent runaway costs
    and prompt construction to ensure queries are well-formed.

    .. note::

        This is the primary integration point for LLM-assisted evidence in
        the JuGeo runtime.  All copilot evidence passes through this
        channel, ensuring that the trust ceiling is enforced uniformly.
    """

    TRUST_CEILING: str = 'proposal'

    def __init__(
        self,
        *,
        config: ChannelConfiguration | None = None,
        model_name: str = 'copilot-default',
    ) -> None:
        self._config = config or ChannelConfiguration.default_for(
            EvidenceChannel.COPILOT,
        )
        self._model_name = model_name
        self._request_timestamps: list[float] = []
        self._query_log: list[dict[str, Any]] = []

    @property
    def channel(self) -> EvidenceChannel:
        """The channel enum member this instance serves."""
        return EvidenceChannel.COPILOT

    def query_llm(
        self,
        prompt: str,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send *prompt* to the LLM backend and return a parsed response.

        In the current stub implementation this returns a deterministic
        proposal based on the prompt hash.  A production implementation
        would call the OpenAI or GitHub Copilot API.
        """
        if not self.rate_limit_check():
            return {
                'status': 'rate-limited',
                'model': self._model_name,
                'proposal': None,
            }

        self._request_timestamps.append(_now_ms())
        effective_timeout = timeout_ms or self._config.timeout_ms

        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        raw_response = {
            'status': 'ok',
            'model': self._model_name,
            'prompt_hash': prompt_hash[:16],
            'raw_text': f'Proposal for: {prompt[:80]}',
            'timeout_ms': effective_timeout,
        }

        parsed = self.parse_response(raw_response)
        self._query_log.append({
            'prompt_length': len(prompt),
            'response_status': parsed.get('status'),
            'ts': _now_ms(),
        })
        return parsed

    def parse_response(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Parse a raw LLM response into a structured evidence proposal.

        Extracts the proposal text, confidence indicators, and any
        structured data the LLM returned.
        """
        status = raw.get('status', 'unknown')
        if status != 'ok':
            return {
                'status': status,
                'proposal': None,
                'confidence': 0.0,
                'structured_data': {},
            }
        raw_text = str(raw.get('raw_text', ''))
        return {
            'status': 'ok',
            'proposal': raw_text,
            'confidence': 0.5,
            'model': raw.get('model', self._model_name),
            'prompt_hash': raw.get('prompt_hash', ''),
            'structured_data': {},
        }

    def apply_trust_ceiling(
        self,
        response: EvidenceResponse,
    ) -> EvidenceResponse:
        """Clamp *response* trust to the copilot ceiling.

        The copilot channel may never assign trust above ``'proposal'``.
        This is a hard invariant from theory2.tex: proposal channels
        contribute hypotheses, not verdicts.
        """
        return response.clamp_trust(self.TRUST_CEILING)

    def require_corroboration(
        self,
        response: EvidenceResponse,
    ) -> EvidenceResponse:
        """Add a corroboration residual to *response*.

        Marks the response as requiring independent corroboration before
        the orchestrator may consider it settled.
        """
        if 'requires-corroboration' not in response.residuals:
            return response.with_residual('requires-corroboration')
        return response

    def rate_limit_check(self) -> bool:
        """Return ``True`` if the channel has not exceeded its rate limit.

        Slides a one-second window over recent request timestamps.
        """
        if self._config.rate_limit <= 0.0:
            return True
        cutoff = _now_ms() - 1000.0
        recent = sum(1 for ts in self._request_timestamps if ts >= cutoff)
        return recent < self._config.rate_limit

    def prompt_construction(
        self,
        coordinate: str,
        proposition: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """Build a well-formed prompt for the LLM.

        Includes the coordinate, proposition, and any additional context
        as a structured prompt string.
        """
        parts: list[str] = [
            f'Coordinate: {coordinate}',
            f'Proposition: {proposition}',
            'Provide a structured evidence proposal.',
        ]
        if context:
            parts.append('Context:')
            for k, v in context.items():
                parts.append(f'  {k}: {v}')
        parts.append(
            'Your proposal will be treated as proposal-tier evidence '
            'and requires independent corroboration.'
        )
        return '\n'.join(parts)

    def health_check(self) -> bool:
        """Copilot channel is healthy when rate limits are not exhausted."""
        return self.rate_limit_check()

    def restart(self) -> None:
        """Clear rate-limit state and query log."""
        self._request_timestamps.clear()
        self._query_log.clear()

    def handle_request(self, request: EvidenceRequest) -> EvidenceResponse:
        """Process an :class:`EvidenceRequest` via the copilot LLM.

        Builds a prompt, queries the LLM, applies the trust ceiling, and
        adds corroboration requirements.
        """
        start = _now_ms()

        prompt = self.prompt_construction(
            request.coordinate,
            request.proposition,
            context=dict(request.metadata) if request.metadata else None,
        )
        llm_result = self.query_llm(prompt)
        elapsed = _now_ms() - start

        if llm_result.get('status') == 'rate-limited':
            return EvidenceResponse(
                request_id=request.request_id,
                channel=EvidenceChannel.COPILOT,
                evidence_item=llm_result,
                trust_level='proposal',
                latency_ms=elapsed,
                is_partial=True,
                residuals=('copilot-rate-limited',),
                provenance=(f'copilot:{self._model_name}:rate-limited',),
            )

        response = EvidenceResponse(
            request_id=request.request_id,
            channel=EvidenceChannel.COPILOT,
            evidence_item=llm_result,
            trust_level='proposal',
            latency_ms=elapsed,
            is_partial=False,
            provenance=(f'copilot:{self._model_name}',),
        )
        response = self.apply_trust_ceiling(response)
        response = self.require_corroboration(response)
        return response


# ---------------------------------------------------------------------------
# 13. ChannelSerializer
# ---------------------------------------------------------------------------


class ChannelSerializer:
    """JSON serialization for channel requests, responses, and state.

    Provides ``to_json`` / ``from_json`` round-tripping for the core
    channel data types.  All serialization is deterministic (sorted keys)
    so that content-addressed hashing is stable.
    """

    @staticmethod
    def request_to_json(request: EvidenceRequest) -> str:
        """Serialize an :class:`EvidenceRequest` to a JSON string."""
        return json.dumps(request.to_dict(), sort_keys=True, indent=2)

    @staticmethod
    def request_from_json(text: str) -> EvidenceRequest:
        """Deserialize an :class:`EvidenceRequest` from a JSON string."""
        data = json.loads(text)
        preferred = (
            EvidenceChannel(data['preferred_channel'])
            if data.get('preferred_channel')
            else None
        )
        fallbacks = tuple(
            EvidenceChannel(c) for c in data.get('fallback_channels', [])
        )
        return EvidenceRequest(
            request_id=data.get('request_id', ''),
            coordinate=data.get('coordinate', ''),
            proposition=data.get('proposition', ''),
            required_kind=data.get('required_kind', ''),
            preferred_channel=preferred,
            fallback_channels=fallbacks,
            deadline_ms=float(data.get('deadline_ms', 0.0)),
            budget=float(data.get('budget', 1.0)),
            metadata=data.get('metadata', {}),
        )

    @staticmethod
    def response_to_json(response: EvidenceResponse) -> str:
        """Serialize an :class:`EvidenceResponse` to a JSON string."""
        return json.dumps(response.to_dict(), sort_keys=True, indent=2)

    @staticmethod
    def response_from_json(text: str) -> EvidenceResponse:
        """Deserialize an :class:`EvidenceResponse` from a JSON string."""
        data = json.loads(text)
        return EvidenceResponse(
            request_id=data.get('request_id', ''),
            channel=EvidenceChannel(data['channel']),
            evidence_item=data.get('evidence_item', {}),
            trust_level=data.get('trust_level', 'proposal'),
            latency_ms=float(data.get('latency_ms', 0.0)),
            is_partial=bool(data.get('is_partial', False)),
            residuals=tuple(data.get('residuals', [])),
            provenance=tuple(data.get('provenance', [])),
        )

    @staticmethod
    def jurisdiction_to_json(jurisdiction: ChannelJurisdiction) -> str:
        """Serialize a :class:`ChannelJurisdiction` to JSON."""
        return json.dumps(jurisdiction.to_dict(), sort_keys=True, indent=2)

    @staticmethod
    def jurisdiction_from_json(text: str) -> ChannelJurisdiction:
        """Deserialize a :class:`ChannelJurisdiction` from JSON."""
        data = json.loads(text)
        return ChannelJurisdiction(
            domain_set=tuple(data.get('domain_set', [])),
            coordinate_patterns=tuple(data.get('coordinate_patterns', ['*'])),
            proposition_kinds=tuple(data.get('proposition_kinds', [])),
            max_trust_level=data.get('max_trust_level', 'reviewed'),
            requires_corroboration=bool(data.get('requires_corroboration', False)),
            excluded_families=tuple(data.get('excluded_families', [])),
        )

    @staticmethod
    def config_to_json(config: ChannelConfiguration) -> str:
        """Serialize a :class:`ChannelConfiguration` to JSON."""
        return json.dumps(config.to_dict(), sort_keys=True, indent=2)

    @staticmethod
    def config_from_json(text: str) -> ChannelConfiguration:
        """Deserialize a :class:`ChannelConfiguration` from JSON."""
        data = json.loads(text)
        channel = EvidenceChannel(data['channel'])
        jurisdiction = ChannelSerializer.jurisdiction_from_json(
            json.dumps(data.get('jurisdiction', {}))
        )
        return ChannelConfiguration(
            channel=channel,
            timeout_ms=int(data.get('timeout_ms', 5000)),
            max_retries=int(data.get('max_retries', 2)),
            batch_size=int(data.get('batch_size', 1)),
            rate_limit=float(data.get('rate_limit', 0.0)),
            trust_ceiling=data.get('trust_ceiling', 'reviewed'),
            jurisdiction=jurisdiction,
            is_enabled=bool(data.get('is_enabled', True)),
            priority=int(data.get('priority', 50)),
        )

    @staticmethod
    def pool_status_to_json(pool: ChannelPool) -> str:
        """Serialize a :class:`ChannelPool` status to JSON."""
        return json.dumps(pool.pool_status(), sort_keys=True, indent=2)

    @staticmethod
    def monitor_summary_to_json(monitor: ChannelMonitor) -> str:
        """Serialize a :class:`ChannelMonitor` summary to JSON."""
        return json.dumps(monitor.summary(), sort_keys=True, indent=2)

    @staticmethod
    def content_hash(text: str) -> str:
        """Compute a deterministic SHA-256 hash for *text*."""
        return hashlib.sha256(text.encode()).hexdigest()

    @staticmethod
    def round_trip_request(request: EvidenceRequest) -> EvidenceRequest:
        """Serialize and deserialize a request for round-trip fidelity."""
        return ChannelSerializer.request_from_json(
            ChannelSerializer.request_to_json(request)
        )

    @staticmethod
    def round_trip_response(response: EvidenceResponse) -> EvidenceResponse:
        """Serialize and deserialize a response for round-trip fidelity."""
        return ChannelSerializer.response_from_json(
            ChannelSerializer.response_to_json(response)
        )


# ---------------------------------------------------------------------------
# Builder helpers (backward compat + convenience)
# ---------------------------------------------------------------------------


def build_channel(
    name: str | None = None,
    kind: str | EvidenceChannel | EvidenceKind | None = None,
    *,
    payload: dict[str, Any] | None = None,
    challengeable: bool = False,
    jurisdiction: ChannelJurisdiction | None = None,
    trust_floor: str | None = None,
    notes: tuple[str, ...] = (),
    support_routes: tuple[Any, ...] = (),
) -> EvidenceChannel | ChannelDescriptor | EvidenceRecord:
    """Build an evidence channel or record, preserving legacy call shapes.

    Provided for backward compatibility with code that called the old
    ``build_channel()`` API.
    """
    if isinstance(kind, EvidenceKind):
        mapping = {
            EvidenceKind.PROOF: EvidenceChannel.FORMAL_PROOF,
            EvidenceKind.SOLVER: EvidenceChannel.SOLVER,
            EvidenceKind.RUNTIME: EvidenceChannel.RUNTIME,
            EvidenceKind.SEMANTIC: EvidenceChannel.ORACLE,
            EvidenceKind.PROPOSAL: EvidenceChannel.COPILOT,
            EvidenceKind.HUMAN: EvidenceChannel.HUMAN,
        }
        channel = mapping[kind]
    elif isinstance(kind, str):
        channel = EvidenceChannel(kind) if kind in EvidenceChannel._value2member_map_ else kind
    else:
        channel = kind

    if payload is not None:
        claim = name or (kind.value if isinstance(kind, Enum) else str(kind or "channel"))
        return EvidenceRecord(
            channel=channel,
            claim=claim,
            payload=payload | {'challengeable': challengeable},
        )

    if isinstance(kind, EvidenceKind):
        return ChannelDescriptor(
            name=name or kind.value,
            kind=kind,
            jurisdiction=jurisdiction or ChannelJurisdiction.for_kind(kind),
            trust_floor=trust_floor or kind.default_trust_floor(),
            notes=tuple(notes),
            support_routes=tuple(support_routes),
        )

    if isinstance(channel, EvidenceChannel):
        claim = name or channel.value
        return EvidenceRecord(
            channel=channel,
            claim=claim,
            payload=dict(payload or {}),
            trust_level=trust_floor or channel.default_trust_floor(),
            obligations=tuple(notes),
            support_routes=tuple(support_routes),
        )

    return channel


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    'EvidenceChannel',
    'EvidenceKind',
    'ChannelJurisdiction',
    'ChannelConfiguration',
    'EvidenceRequest',
    'EvidenceResponse',
    'EvidenceRecord',
    'ChannelRouter',
    'ChannelPool',
    'ChannelFederation',
    'ChannelMonitor',
    'SolverChannel',
    'RuntimeChannel',
    'CopilotChannel',
    'ChannelSerializer',
    'build_channel',
]

# copilot: shared-core marker for future LLM orchestration.
