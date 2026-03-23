"""Bridge theorems, registry, discovery, verification, and federation support.

Theory (theory2.tex §4 — Domain packs, bridge theorems, and federation):

    A **domain pack** is a local semantic theory over a region of the judgment
    geometry.  Each pack exports its own kinds, laws, and evidence vocabulary.
    Packs are intentionally *partial*: they cover the coordinate sub-space they
    understand and remain silent elsewhere.

    A **bridge theorem** connects two packs by establishing an *overlap law*
    between their jurisdictions.  Formally, a bridge is a justified statement
    of the form

        ∀ x ∈ (Pack_A ∩ Pack_B).  φ_A(x) ⟺ ψ_B(x)

    where φ_A is stated in Pack_A's vocabulary and ψ_B in Pack_B's.

    Bridges enable **federation** — combining evidence from multiple packs to
    verify claims that span pack boundaries.  Trust does not propagate for
    free: each bridge carries a *trust ceiling*, and composing bridges along a
    path monotonically weakens the ceiling (theory2.tex §4.3, Lemma 4.7).

This module provides the full bridge lifecycle:

    * ``BridgeTheorem``         — the data record for a bridge.
    * ``BridgeRegistry``        — index and lookup of registered bridges.
    * ``BridgeDiscoverer``      — heuristic and copilot-assisted discovery.
    * ``BridgeVerifier``        — formal and semi-formal verification.
    * ``BridgeComposer``        — transitive composition of bridge chains.
    * ``BridgeApplication``     — evidence transport across a bridge.
    * ``BridgeMaintenance``     — staleness detection and refresh.
    * ``BridgePatternLibrary``  — reusable bridge archetypes.
    * ``BridgeStatistics``      — usage tracking and reliability metrics.
    * ``BridgeDiagnostics``     — human-readable and copilot-readable reports.
    * ``BridgeSerializer``      — JSON round-tripping.

Backward-compatible re-export:

    ``PackBridge`` remains available as a lightweight frozen record for code
    that only needs to name a bridge without the full lifecycle machinery.

copilot: bridge-lifecycle module — LLM agents may propose, verify, and
    compose bridges via the ``copilot_*`` helper methods on the discovery
    and verification classes.
"""

from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return the current UTC time, timezone-aware."""
    return datetime.now(timezone.utc)


def _short_id() -> str:
    """Return a short, unique hex identifier."""
    return uuid.uuid4().hex[:12]


def _clamp_trust(value: float) -> float:
    """Clamp a trust value to the canonical [0.0, 1.0] interval."""
    return max(0.0, min(1.0, value))


def _dedupe_strings(items: Iterable[str], *, label: str = "items") -> tuple[str, ...]:
    """Deduplicate while preserving first-occurrence order.

    Parameters
    ----------
    items:
        An iterable of strings to deduplicate.
    label:
        Diagnostic label for error messages.

    Returns
    -------
    tuple[str, ...]
        Deduplicated tuple in original encounter order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise ValueError(f"Empty or non-string entry in {label}: {item!r}")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


# ---------------------------------------------------------------------------
# Trust-level constants (inline to avoid circular imports with evidence.trust)
# ---------------------------------------------------------------------------

TRUST_MECHANICALLY_VERIFIED: float = 1.0
TRUST_SOLVER_DISCHARGED: float = 0.95
TRUST_RUNTIME_WITNESSED: float = 0.85
TRUST_HUMAN_ATTESTED: float = 0.75
TRUST_ORACLE_PROPOSED: float = 0.60
TRUST_COPILOT_SUGGESTED: float = 0.50
TRUST_UNVERIFIED: float = 0.20
TRUST_CONTRADICTED: float = 0.0

_TRUST_LABELS: dict[float, str] = {
    TRUST_MECHANICALLY_VERIFIED: "mechanically_verified",
    TRUST_SOLVER_DISCHARGED: "solver_discharged",
    TRUST_RUNTIME_WITNESSED: "runtime_witnessed",
    TRUST_HUMAN_ATTESTED: "human_attested",
    TRUST_ORACLE_PROPOSED: "oracle_proposed",
    TRUST_COPILOT_SUGGESTED: "copilot_suggested",
    TRUST_UNVERIFIED: "unverified",
    TRUST_CONTRADICTED: "contradicted",
}


def _trust_label(level: float) -> str:
    """Return a human-readable label for a numeric trust level."""
    closest = min(_TRUST_LABELS, key=lambda k: abs(k - level))
    if abs(closest - level) < 0.01:
        return _TRUST_LABELS[closest]
    return f"custom({level:.3f})"


# ---------------------------------------------------------------------------
# 0. PackBridge — lightweight backward-compatible record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PackBridge:
    """Lightweight, immutable bridge record for cross-pack transport.

    This is the original minimal type from the first iteration of the bridges
    module.  It is retained for backward compatibility; new code should prefer
    :class:`BridgeTheorem` for richer metadata.

    Attributes
    ----------
    source_pack:
        Name of the originating pack.
    target_pack:
        Name of the destination pack.
    theorem_name:
        Short symbolic name of the bridge theorem.
    transported_symbols:
        Tuple of symbol names that this bridge can transport.
    provenance:
        Ordered provenance chain (e.g. ``("theory2.tex §4", "review:alice")``).
    """

    source_pack: str
    target_pack: str
    theorem_name: str
    transported_symbols: tuple[str, ...] = field(default_factory=tuple)
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def connects(self, source: str, target: str) -> bool:
        """Return True when this bridge connects *source* → *target*."""
        return self.source_pack == source and self.target_pack == target


# ---------------------------------------------------------------------------
# 1. BridgeTheorem — full bridge data record
# ---------------------------------------------------------------------------

@dataclass(slots=True, init=False)
class BridgeTheorem:
    """Complete record for a bridge theorem connecting two domain packs.

    A bridge theorem (theory2.tex §4.1) establishes an overlap law between
    the jurisdictions of two packs.  This record carries the statement, its
    proof sketch, the evidence basis that justifies it, and operational
    metadata needed for federation.

    Attributes
    ----------
    bridge_id:
        Unique identifier.  Auto-generated if not supplied.
    source_pack:
        The pack whose vocabulary appears on the left of the bridge.
    target_pack:
        The pack whose vocabulary appears on the right of the bridge.
    theorem_statement:
        Human-readable (or formal) statement of the bridge law.
    proof_sketch:
        Informal or structured proof sketch justifying the bridge.
    evidence_basis:
        References to evidence artifacts that support the bridge.
    trust_level:
        Numeric trust in [0, 1].  Composed bridges weaken monotonically.
    support_scope:
        Coordinate prefixes in which this bridge is valid.
    preconditions:
        Conditions that must hold before the bridge may be applied.
    is_verified:
        Whether the bridge has been formally or semi-formally verified.
    verification_evidence:
        References to verification certificates or logs.
    created_at:
        Timestamp of creation.
    metadata:
        Extensible key/value metadata.
    """

    source_pack: str
    target_pack: str
    theorem_statement: str
    bridge_id: str = field(default_factory=_short_id)
    proof_sketch: str = ""
    evidence_basis: tuple[str, ...] = field(default_factory=tuple)
    trust_level: float = TRUST_UNVERIFIED
    support_scope: tuple[str, ...] = field(default_factory=tuple)
    preconditions: list[str] = field(default_factory=list)
    is_verified: bool = False
    verification_evidence: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        source_pack: str,
        target_pack: str,
        theorem_statement: str,
        bridge_id: str | None = None,
        proof_sketch: str = "",
        evidence_basis: Sequence[str] = (),
        trust_level: float = TRUST_UNVERIFIED,
        support_scope: Sequence[str] = (),
        preconditions: Sequence[str] = (),
        is_verified: bool = False,
        verification_evidence: Sequence[str] = (),
        created_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        *,
        trust_ceiling: float | None = None,
    ) -> None:
        object.__setattr__(self, "source_pack", source_pack)
        object.__setattr__(self, "target_pack", target_pack)
        object.__setattr__(self, "theorem_statement", theorem_statement)
        object.__setattr__(self, "bridge_id", bridge_id or _short_id())
        object.__setattr__(self, "proof_sketch", proof_sketch)
        object.__setattr__(self, "evidence_basis", tuple(evidence_basis))
        object.__setattr__(self, "trust_level", trust_level if trust_ceiling is None else trust_ceiling)
        object.__setattr__(self, "support_scope", tuple(support_scope))
        object.__setattr__(self, "preconditions", list(preconditions))
        object.__setattr__(self, "is_verified", is_verified)
        object.__setattr__(self, "verification_evidence", tuple(verification_evidence))
        object.__setattr__(self, "created_at", created_at or _utcnow())
        object.__setattr__(self, "metadata", dict(metadata or {}))
        self.__post_init__()

    # -- post-init normalization ------------------------------------------------

    def __post_init__(self) -> None:
        """Normalize and validate fields after construction."""
        self.trust_level = _clamp_trust(self.trust_level)
        if self.evidence_basis:
            object.__setattr__(
                self, "evidence_basis",
                _dedupe_strings(self.evidence_basis, label="evidence_basis"),
            )
        if self.support_scope:
            object.__setattr__(
                self, "support_scope",
                _dedupe_strings(self.support_scope, label="support_scope"),
            )
        if not self.source_pack or not self.target_pack:
            raise ValueError("source_pack and target_pack must be non-empty strings")

    # -- query helpers ----------------------------------------------------------

    def connects(self, source: str, target: str) -> bool:
        """Return True when this bridge connects *source* → *target*."""
        return self.source_pack == source and self.target_pack == target

    def involves(self, pack_name: str) -> bool:
        """Return True if *pack_name* is either the source or target."""
        return pack_name in (self.source_pack, self.target_pack)

    def covers_coordinate(self, coordinate: str) -> bool:
        """Return True if *coordinate* falls inside the support scope.

        When ``support_scope`` is empty every coordinate is accepted (the
        bridge is unrestricted).  Otherwise the coordinate must have at
        least one scope entry as a prefix.
        """
        if not self.support_scope:
            return True
        return any(coordinate.startswith(prefix) for prefix in self.support_scope)

    def meets_trust_floor(self, floor: float) -> bool:
        """Return True if the bridge's trust level meets or exceeds *floor*."""
        return self.trust_level >= floor

    @property
    def trust_ceiling(self) -> float:
        return self.trust_level

    def mark_verified(self, evidence: Iterable[str] | None = None) -> None:
        """Flag this bridge as verified, optionally recording evidence."""
        self.is_verified = True
        if evidence:
            self.verification_evidence = _dedupe_strings(
                (*self.verification_evidence, *evidence),
                label="verification_evidence",
            )

    def to_pack_bridge(self) -> PackBridge:
        """Downcast to the lightweight :class:`PackBridge` record."""
        return PackBridge(
            source_pack=self.source_pack,
            target_pack=self.target_pack,
            theorem_name=self.theorem_statement[:60],
            transported_symbols=(),
            provenance=self.evidence_basis,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dictionary."""
        return {
            "bridge_id": self.bridge_id,
            "source_pack": self.source_pack,
            "target_pack": self.target_pack,
            "theorem_statement": self.theorem_statement,
            "proof_sketch": self.proof_sketch,
            "evidence_basis": list(self.evidence_basis),
            "trust_level": self.trust_level,
            "support_scope": list(self.support_scope),
            "preconditions": list(self.preconditions),
            "is_verified": self.is_verified,
            "verification_evidence": list(self.verification_evidence),
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> BridgeTheorem:
        """Reconstruct a :class:`BridgeTheorem` from a dictionary."""
        created = data.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        return cls(
            bridge_id=data.get("bridge_id", _short_id()),
            source_pack=data["source_pack"],
            target_pack=data["target_pack"],
            theorem_statement=data["theorem_statement"],
            proof_sketch=data.get("proof_sketch", ""),
            evidence_basis=tuple(data.get("evidence_basis", ())),
            trust_level=float(data.get("trust_level", TRUST_UNVERIFIED)),
            support_scope=tuple(data.get("support_scope", ())),
            preconditions=list(data.get("preconditions", ())),
            is_verified=bool(data.get("is_verified", False)),
            verification_evidence=tuple(data.get("verification_evidence", ())),
            created_at=created or _utcnow(),
            metadata=dict(data.get("metadata", {})),
        )

    def describe(self) -> str:
        """Return a one-line human-readable summary."""
        status = "✓ verified" if self.is_verified else "○ unverified"
        return (
            f"[{self.bridge_id}] {self.source_pack} → {self.target_pack}: "
            f"{self.theorem_statement!r}  ({status}, trust={self.trust_level:.2f})"
        )


# ---------------------------------------------------------------------------
# 2. BridgeRegistry — index and lookup
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeRegistry:
    """Central registry for bridge theorems.

    The registry indexes bridges by id, source pack, and target pack so that
    look-ups are O(1) on average.  It enforces uniqueness of bridge ids and
    provides graph-level queries (e.g. ``bridges_between``, transitive path
    availability) needed by the federation layer.

    Attributes
    ----------
    _by_id:
        Primary index: bridge_id → BridgeTheorem.
    _by_source:
        Secondary index: source_pack → set of bridge_ids.
    _by_target:
        Secondary index: target_pack → set of bridge_ids.
    """

    _by_id: dict[str, BridgeTheorem] = field(default_factory=dict)
    _by_source: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _by_target: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # -- mutators ---------------------------------------------------------------

    def register(self, bridge: BridgeTheorem) -> None:
        """Register a bridge theorem.

        Raises
        ------
        ValueError
            If a bridge with the same *bridge_id* is already registered.
        """
        if bridge.bridge_id in self._by_id:
            raise ValueError(
                f"Duplicate bridge id {bridge.bridge_id!r} — "
                "use a unique id or remove the existing bridge first."
            )
        self._by_id[bridge.bridge_id] = bridge
        self._by_source[bridge.source_pack].add(bridge.bridge_id)
        self._by_target[bridge.target_pack].add(bridge.bridge_id)

    def remove(self, bridge_id: str) -> BridgeTheorem:
        """Remove and return a bridge by its id.

        Raises
        ------
        KeyError
            If no bridge with *bridge_id* exists.
        """
        bridge = self._by_id.pop(bridge_id)
        self._by_source[bridge.source_pack].discard(bridge_id)
        self._by_target[bridge.target_pack].discard(bridge_id)
        return bridge

    # -- lookups ----------------------------------------------------------------

    def lookup(self, bridge_id: str) -> BridgeTheorem | None:
        """Return the bridge with *bridge_id*, or ``None``."""
        return self._by_id.get(bridge_id)

    def bridges_between(
        self, source: str, target: str
    ) -> list[BridgeTheorem]:
        """Return all bridges from *source* to *target*."""
        source_ids = self._by_source.get(source, set())
        return [
            self._by_id[bid]
            for bid in source_ids
            if self._by_id[bid].target_pack == target
        ]

    def bridges_from(self, source: str) -> list[BridgeTheorem]:
        """Return every bridge originating in *source*."""
        return [self._by_id[bid] for bid in self._by_source.get(source, set())]

    def bridges_to(self, target: str) -> list[BridgeTheorem]:
        """Return every bridge landing in *target*."""
        return [self._by_id[bid] for bid in self._by_target.get(target, set())]

    def all_bridges(self) -> list[BridgeTheorem]:
        """Return a snapshot of every registered bridge."""
        return list(self._by_id.values())

    def find(
        self,
        *,
        source: str | None = None,
        target: str | None = None,
    ) -> list[BridgeTheorem]:
        if source is not None and target is not None:
            return self.bridges_between(source, target)
        if source is not None:
            return self.bridges_from(source)
        if target is not None:
            return self.bridges_to(target)
        return self.all_bridges()

    def all(self) -> list[BridgeTheorem]:
        return self.all_bridges()

    # -- validation -------------------------------------------------------------

    def validate_bridge(self, bridge_id: str) -> list[str]:
        """Run lightweight validation checks on a registered bridge.

        Returns a list of diagnostic strings; an empty list means the bridge
        passed all checks.
        """
        bridge = self._by_id.get(bridge_id)
        if bridge is None:
            return [f"Bridge {bridge_id!r} not found in registry."]
        issues: list[str] = []
        if not bridge.theorem_statement.strip():
            issues.append("Empty theorem statement.")
        if bridge.trust_level <= TRUST_CONTRADICTED:
            issues.append("Trust level is at or below CONTRADICTED.")
        if bridge.source_pack == bridge.target_pack:
            issues.append("Source and target pack are identical (trivial bridge).")
        if not bridge.is_verified and bridge.trust_level > TRUST_COPILOT_SUGGESTED:
            issues.append(
                "Trust exceeds COPILOT_SUGGESTED but bridge is not verified."
            )
        return issues

    def invalidate_bridge(self, bridge_id: str, *, reason: str = "") -> None:
        """Mark a bridge as unverified and lower its trust to CONTRADICTED.

        Parameters
        ----------
        bridge_id:
            The id of the bridge to invalidate.
        reason:
            Optional reason string appended to metadata.
        """
        bridge = self._by_id.get(bridge_id)
        if bridge is None:
            raise KeyError(f"Bridge {bridge_id!r} not found.")
        bridge.is_verified = False
        bridge.trust_level = TRUST_CONTRADICTED
        if reason:
            bridge.metadata["invalidation_reason"] = reason
            bridge.metadata["invalidated_at"] = _utcnow().isoformat()

    # -- graph queries ----------------------------------------------------------

    def is_transitive_path_available(
        self,
        source: str,
        target: str,
        *,
        trust_floor: float = TRUST_CONTRADICTED,
    ) -> bool:
        """Return True if there is a directed path from *source* to *target*.

        Only bridges whose trust meets *trust_floor* are traversed.
        Uses breadth-first search over the bridge graph.
        """
        if source == target:
            return True
        visited: set[str] = set()
        queue: deque[str] = deque([source])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for bridge in self.bridges_from(current):
                if bridge.trust_level >= trust_floor:
                    if bridge.target_pack == target:
                        return True
                    queue.append(bridge.target_pack)
        return False

    def pack_names(self) -> set[str]:
        """Return the set of all pack names that appear as source or target."""
        names: set[str] = set()
        for bridge in self._by_id.values():
            names.add(bridge.source_pack)
            names.add(bridge.target_pack)
        return names

    def __len__(self) -> int:
        return len(self._by_id)


# ---------------------------------------------------------------------------
# 3. BridgeDiscoverer — heuristic and copilot-assisted discovery
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeDiscoverer:
    """Discovers potential bridges between packs.

    Bridge discovery is the process of identifying candidate overlap laws
    between pack jurisdictions.  Discovery can be driven by structural
    analysis (shared symbol names, compatible type signatures) or by
    copilot-assisted heuristic suggestion (theory2.tex §4.4).

    Attributes
    ----------
    registry:
        The bridge registry to consult for existing bridges.
    correlation_threshold:
        Minimum correlation score to propose a bridge candidate.
    """

    registry: BridgeRegistry
    correlation_threshold: float = 0.6

    def discover(
        self,
        source_symbols: Mapping[str, str],
        target_symbols: Mapping[str, str],
        source_pack: str,
        target_pack: str,
    ) -> list[BridgeTheorem]:
        """Discover candidate bridges by comparing exported symbols.

        Parameters
        ----------
        source_symbols:
            Mapping of symbol name → kind in the source pack.
        target_symbols:
            Mapping of symbol name → kind in the target pack.
        source_pack / target_pack:
            Pack identifiers.

        Returns
        -------
        list[BridgeTheorem]
            Candidate bridges ranked by estimated utility.
        """
        correlations = self.mine_correlations(source_symbols, target_symbols)
        candidates: list[BridgeTheorem] = []
        for sym, score in correlations:
            if score >= self.correlation_threshold:
                candidate = self.propose_bridge(
                    source_pack=source_pack,
                    target_pack=target_pack,
                    shared_symbol=sym,
                    correlation_score=score,
                )
                if self.validate_candidate(candidate):
                    candidates.append(candidate)
        return self.rank_by_utility(candidates)

    def mine_correlations(
        self,
        source_symbols: Mapping[str, str],
        target_symbols: Mapping[str, str],
    ) -> list[tuple[str, float]]:
        """Find correlated symbols between two packs.

        Correlation is based on exact name matches (score 1.0), shared
        prefixes (score proportional to prefix length), and kind
        compatibility (bonus 0.2 for matching kinds).

        Returns
        -------
        list[tuple[str, float]]
            List of (symbol_name, score) pairs, sorted by descending score.
        """
        results: list[tuple[str, float]] = []
        for src_sym, src_kind in source_symbols.items():
            for tgt_sym, tgt_kind in target_symbols.items():
                score = 0.0
                if src_sym == tgt_sym:
                    score = 1.0
                else:
                    prefix_len = len(
                        next(
                            (src_sym[:i] for i in range(min(len(src_sym), len(tgt_sym)), 0, -1)
                             if src_sym[:i] == tgt_sym[:i]),
                            "",
                        )
                    )
                    if prefix_len > 2:
                        score = prefix_len / max(len(src_sym), len(tgt_sym))
                if src_kind == tgt_kind and score > 0:
                    score = min(1.0, score + 0.2)
                if score > 0:
                    results.append((src_sym, score))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def propose_bridge(
        self,
        source_pack: str,
        target_pack: str,
        shared_symbol: str,
        correlation_score: float,
    ) -> BridgeTheorem:
        """Create a candidate bridge theorem from a discovered correlation.

        The candidate starts at COPILOT_SUGGESTED trust because it is
        machine-proposed and must be reviewed before reaching higher levels.
        """
        return BridgeTheorem(
            source_pack=source_pack,
            target_pack=target_pack,
            theorem_statement=(
                f"Symbol '{shared_symbol}' overlaps between "
                f"{source_pack} and {target_pack} "
                f"(correlation={correlation_score:.2f})"
            ),
            proof_sketch="Auto-discovered via symbol correlation mining.",
            trust_level=TRUST_COPILOT_SUGGESTED,
            metadata={
                "discovery_method": "symbol_correlation",
                "shared_symbol": shared_symbol,
                "correlation_score": correlation_score,
            },
        )

    def validate_candidate(self, candidate: BridgeTheorem) -> bool:
        """Apply lightweight heuristic checks to a candidate bridge.

        Returns True if the candidate passes basic sanity checks:
        * Source and target are distinct.
        * No existing bridge with the same statement already registered.
        * Theorem statement is non-empty.
        """
        if candidate.source_pack == candidate.target_pack:
            return False
        if not candidate.theorem_statement.strip():
            return False
        existing = self.registry.bridges_between(
            candidate.source_pack, candidate.target_pack
        )
        for b in existing:
            if b.theorem_statement == candidate.theorem_statement:
                return False
        return True

    def copilot_suggest_bridge(
        self,
        source_pack: str,
        target_pack: str,
        context_hint: str = "",
    ) -> BridgeTheorem:
        """Generate a copilot-suggested bridge from context hints.

        This method simulates the pathway by which a copilot agent proposes
        a bridge theorem based on a natural-language context hint.  The
        resulting bridge carries COPILOT_SUGGESTED trust and must be verified
        before federation can rely on it.

        Parameters
        ----------
        source_pack / target_pack:
            The packs to bridge.
        context_hint:
            A natural-language description of the expected overlap.

        Returns
        -------
        BridgeTheorem
            A candidate bridge at COPILOT_SUGGESTED trust.
        """
        statement = context_hint if context_hint else (
            f"Copilot-proposed overlap between {source_pack} and {target_pack}."
        )
        return BridgeTheorem(
            source_pack=source_pack,
            target_pack=target_pack,
            theorem_statement=statement,
            proof_sketch="Copilot-generated; requires human or solver review.",
            trust_level=TRUST_COPILOT_SUGGESTED,
            metadata={
                "discovery_method": "copilot_suggestion",
                "context_hint": context_hint,
            },
        )

    def rank_by_utility(
        self, candidates: list[BridgeTheorem]
    ) -> list[BridgeTheorem]:
        """Sort candidate bridges by estimated utility (descending).

        Utility heuristic:
            utility = trust_level × (1 + len(support_scope) × 0.1)

        Bridges that cover more of the coordinate space and carry higher
        trust are preferred.
        """
        def utility(b: BridgeTheorem) -> float:
            scope_bonus = 1.0 + len(b.support_scope) * 0.1
            return b.trust_level * scope_bonus

        return sorted(candidates, key=utility, reverse=True)


# ---------------------------------------------------------------------------
# 4. BridgeVerifier — formal and semi-formal verification
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeVerifier:
    """Verifies bridge theorems against their preconditions and evidence.

    Verification proceeds in stages (theory2.tex §4.2):

        1. **Precondition check** — are the bridge's stated preconditions
           satisfied in the current context?
        2. **Consistency check** — does the bridge introduce a contradiction
           when both pack theories are loaded simultaneously?
        3. **Support-scope check** — is the coordinate region still active?
        4. **Certificate generation** — on success, produce a verification
           certificate with an expiry window.

    Attributes
    ----------
    registry:
        Backing registry for resolving bridge ids.
    verification_log:
        Append-only log of verification outcomes.
    """

    registry: BridgeRegistry
    verification_log: list[dict[str, Any]] = field(default_factory=list)

    def verify(self, bridge_id: str) -> bool:
        """Run the full verification pipeline on a bridge.

        Returns True when the bridge passes all checks and is marked
        as verified in the registry.
        """
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            self._log(bridge_id, "not_found", success=False)
            return False
        if not self.check_preconditions(bridge):
            self._log(bridge_id, "precondition_failure", success=False)
            return False
        if not self.check_consistency(bridge):
            self._log(bridge_id, "consistency_failure", success=False)
            return False
        if not self.check_support_scope(bridge):
            self._log(bridge_id, "scope_failure", success=False)
            return False
        cert = self.generate_certificate(bridge)
        bridge.mark_verified(evidence=(cert["certificate_id"],))
        bridge.trust_level = max(bridge.trust_level, TRUST_HUMAN_ATTESTED)
        self._log(bridge_id, "verified", success=True, certificate=cert)
        return True

    def check_preconditions(self, bridge: BridgeTheorem) -> bool:
        """Return True if all stated preconditions are satisfiable.

        Currently performs a structural check: every precondition string
        must be non-empty and must not be the literal ``"false"``.
        """
        for precondition in bridge.preconditions:
            stripped = precondition.strip().lower()
            if not stripped or stripped == "false":
                return False
        return True

    def check_consistency(self, bridge: BridgeTheorem) -> bool:
        """Return True if the bridge does not self-contradict.

        A bridge is *inconsistent* if its trust is CONTRADICTED or if its
        source and target packs are the same (trivial, possibly circular).
        """
        if bridge.trust_level <= TRUST_CONTRADICTED:
            return False
        if bridge.source_pack == bridge.target_pack:
            return False
        return True

    def check_support_scope(self, bridge: BridgeTheorem) -> bool:
        """Return True if the bridge's support scope is well-formed.

        Each scope entry must be a non-empty string that looks like a
        coordinate prefix (no whitespace-only entries).
        """
        for scope in bridge.support_scope:
            if not scope.strip():
                return False
        return True

    def generate_certificate(self, bridge: BridgeTheorem) -> dict[str, Any]:
        """Create a verification certificate for a bridge that passed checks.

        Parameters
        ----------
        bridge:
            The bridge theorem that has been verified.

        Returns
        -------
        dict[str, Any]
            A certificate record containing the bridge id, timestamp,
            trust level at verification time, and a unique certificate id.
        """
        return {
            "certificate_id": f"cert-{_short_id()}",
            "bridge_id": bridge.bridge_id,
            "source_pack": bridge.source_pack,
            "target_pack": bridge.target_pack,
            "trust_at_verification": bridge.trust_level,
            "verified_at": _utcnow().isoformat(),
            "expires_at": None,
        }

    def copilot_assist_verification(
        self, bridge_id: str
    ) -> dict[str, Any]:
        """Produce a copilot-friendly verification report.

        The report includes each check result with a short explanation,
        suitable for display in a copilot tooltip or inline suggestion.
        """
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            return {"status": "error", "message": f"Bridge {bridge_id!r} not found."}

        results: dict[str, Any] = {
            "bridge_id": bridge_id,
            "checks": {},
        }
        results["checks"]["preconditions"] = {
            "passed": self.check_preconditions(bridge),
            "detail": f"{len(bridge.preconditions)} precondition(s) evaluated.",
        }
        results["checks"]["consistency"] = {
            "passed": self.check_consistency(bridge),
            "detail": (
                f"trust={bridge.trust_level:.2f}, "
                f"self_loop={bridge.source_pack == bridge.target_pack}"
            ),
        }
        results["checks"]["support_scope"] = {
            "passed": self.check_support_scope(bridge),
            "detail": f"{len(bridge.support_scope)} scope prefix(es).",
        }
        all_passed = all(
            check["passed"] for check in results["checks"].values()
        )
        results["overall"] = "pass" if all_passed else "fail"
        results["copilot_hint"] = (
            "All checks passed — bridge is ready for verification."
            if all_passed
            else "Some checks failed — review the 'checks' map for details."
        )
        return results

    # -- internal ---------------------------------------------------------------

    def _log(
        self,
        bridge_id: str,
        event: str,
        *,
        success: bool,
        certificate: dict[str, Any] | None = None,
    ) -> None:
        """Append an entry to the verification log."""
        entry: dict[str, Any] = {
            "bridge_id": bridge_id,
            "event": event,
            "success": success,
            "timestamp": _utcnow().isoformat(),
        }
        if certificate:
            entry["certificate"] = certificate
        self.verification_log.append(entry)


# ---------------------------------------------------------------------------
# 5. BridgeComposer — transitive composition of bridge chains
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeComposer:
    """Composes bridge theorems transitively along directed paths.

    When no single bridge connects pack A to pack C, a *bridge chain*
    A → B → C may exist.  Composition produces a synthetic bridge whose
    trust is the minimum of all constituent bridges (theory2.tex §4.3,
    Lemma 4.7 — monotonic weakening).

    Attributes
    ----------
    registry:
        The registry of all available bridges.
    """

    registry: BridgeRegistry = field(default_factory=BridgeRegistry)

    def register(self, bridge: BridgeTheorem) -> None:
        self.registry.register(bridge)

    def compose(
        self, bridge_a: BridgeTheorem | Sequence[BridgeTheorem], bridge_b: BridgeTheorem | None = None
    ) -> BridgeTheorem | None:
        """Compose two adjacent bridges into a single synthetic bridge.

        Returns None if the bridges are not composable (i.e. the target
        of *bridge_a* does not equal the source of *bridge_b*).
        """
        if bridge_b is None:
            if isinstance(bridge_a, Sequence):
                return self.compose_chain(list(bridge_a))
            return bridge_a
        if not self.is_composable(bridge_a, bridge_b):
            return None
        composed_trust = min(bridge_a.trust_level, bridge_b.trust_level)
        return BridgeTheorem(
            source_pack=bridge_a.source_pack,
            target_pack=bridge_b.target_pack,
            theorem_statement=(
                f"Composed: ({bridge_a.theorem_statement}) "
                f"∘ ({bridge_b.theorem_statement})"
            ),
            proof_sketch=(
                f"Transitive composition via "
                f"{bridge_a.bridge_id} → {bridge_b.bridge_id}."
            ),
            evidence_basis=(
                *bridge_a.evidence_basis,
                *bridge_b.evidence_basis,
            ),
            trust_level=composed_trust,
            support_scope=tuple(
                set(bridge_a.support_scope) & set(bridge_b.support_scope)
            ) if bridge_a.support_scope and bridge_b.support_scope else (),
            preconditions=[
                *bridge_a.preconditions,
                *bridge_b.preconditions,
            ],
            is_verified=bridge_a.is_verified and bridge_b.is_verified,
            metadata={
                "composition_chain": [bridge_a.bridge_id, bridge_b.bridge_id],
                "composition_method": "pairwise",
            },
        )

    def compose_chain(
        self, bridges: Sequence[BridgeTheorem]
    ) -> BridgeTheorem | None:
        """Compose a chain of bridges left-to-right.

        Returns None if any adjacent pair is not composable.
        """
        if not bridges:
            return None
        result = bridges[0]
        for i in range(1, len(bridges)):
            composed = self.compose(result, bridges[i])
            if composed is None:
                return None
            result = composed
        if len(bridges) > 2:
            result.metadata["composition_method"] = "chain"
            result.metadata["composition_chain"] = [b.bridge_id for b in bridges]
        return result

    def find_path(
        self,
        source: str,
        target: str,
        *,
        trust_floor: float = TRUST_CONTRADICTED,
    ) -> list[BridgeTheorem] | None:
        """Find any directed path from *source* to *target* (BFS).

        Returns
        -------
        list[BridgeTheorem] | None
            A list of bridges forming the path, or None if unreachable.
        """
        if source == target:
            return []
        visited: set[str] = set()
        parent: dict[str, tuple[str, BridgeTheorem]] = {}
        queue: deque[str] = deque([source])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for bridge in self.registry.bridges_from(current):
                if bridge.trust_level < trust_floor:
                    continue
                nxt = bridge.target_pack
                if nxt not in visited and nxt not in parent:
                    parent[nxt] = (current, bridge)
                    if nxt == target:
                        return self._reconstruct_path(source, target, parent)
                    queue.append(nxt)
        return None

    def shortest_path(
        self,
        source: str,
        target: str,
        *,
        trust_floor: float = TRUST_CONTRADICTED,
    ) -> list[BridgeTheorem] | None:
        """Find the shortest (fewest hops) path from *source* to *target*.

        BFS guarantees the shortest unweighted path.
        """
        return self.find_path(source, target, trust_floor=trust_floor)

    def trust_along_path(self, path: Sequence[BridgeTheorem]) -> float:
        """Compute the effective trust along a bridge path.

        Per theory2.tex §4.3 Lemma 4.7 the composed trust is the minimum
        trust of any constituent bridge — composition can only weaken.
        """
        if not path:
            return TRUST_MECHANICALLY_VERIFIED
        return min(b.trust_level for b in path)

    def is_composable(
        self, bridge_a: BridgeTheorem, bridge_b: BridgeTheorem
    ) -> bool:
        """Return True when *bridge_a* and *bridge_b* can be composed.

        Composition requires adjacency: bridge_a.target_pack must equal
        bridge_b.source_pack, and neither bridge may have CONTRADICTED trust.
        """
        if bridge_a.target_pack != bridge_b.source_pack:
            return False
        if bridge_a.trust_level <= TRUST_CONTRADICTED:
            return False
        if bridge_b.trust_level <= TRUST_CONTRADICTED:
            return False
        return True

    # -- internal ---------------------------------------------------------------

    @staticmethod
    def _reconstruct_path(
        source: str,
        target: str,
        parent: dict[str, tuple[str, BridgeTheorem]],
    ) -> list[BridgeTheorem]:
        """Walk backward through *parent* to reconstruct the BFS path."""
        path: list[BridgeTheorem] = []
        current = target
        while current != source:
            prev, bridge = parent[current]
            path.append(bridge)
            current = prev
        path.reverse()
        return path


# ---------------------------------------------------------------------------
# 6. BridgeApplication — evidence transport across a bridge
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeApplication:
    """Applies a bridge theorem to transport evidence across pack boundaries.

    When a judgment in pack A needs evidence from pack B, a bridge
    application *transports* that evidence, adjusting its trust to reflect
    the bridge's own trust ceiling.

    Attributes
    ----------
    registry:
        Backing registry.
    application_log:
        Append-only log of every application for audit purposes.
    """

    registry: BridgeRegistry
    application_log: list[dict[str, Any]] = field(default_factory=list)

    def __init__(self, registry: BridgeRegistry | BridgeTheorem) -> None:
        if isinstance(registry, BridgeTheorem):
            bridge_registry = BridgeRegistry()
            bridge_registry.register(registry)
            self.registry = bridge_registry
        else:
            self.registry = registry
        self.application_log = []

    def transport(self, evidence: dict[str, Any]) -> dict[str, Any] | None:
        bridges = self.registry.all_bridges()
        if not bridges:
            return None
        return self.transport_evidence(bridges[0], evidence)

    def apply(
        self,
        bridge_id: str,
        evidence: dict[str, Any],
        *,
        coordinate: str = "",
    ) -> dict[str, Any] | None:
        """Apply a bridge to transport *evidence* across pack boundaries.

        Returns the transformed evidence dict, or None if the bridge
        cannot be applied (not found, jurisdiction mismatch, etc.).
        """
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            return None
        if coordinate and not self.check_jurisdiction(bridge, coordinate):
            return None
        transported = self.transport_evidence(bridge, evidence)
        adjusted = self.adjust_trust(bridge, transported)
        provenance = self.record_provenance(bridge, adjusted)
        self._log_application(bridge, coordinate, success=True)
        return provenance

    def transport_evidence(
        self,
        bridge: BridgeTheorem,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Rewrite evidence vocabulary from source pack to target pack.

        The transported evidence receives a ``_transported_via`` annotation
        and its ``pack`` field (if present) is rewritten to the target pack.
        """
        transported = dict(evidence)
        transported["_transported_via"] = bridge.bridge_id
        transported["_transport_timestamp"] = _utcnow().isoformat()
        if "pack" in transported:
            transported["_original_pack"] = transported["pack"]
            transported["pack"] = bridge.target_pack
        return transported

    def transform_judgment(
        self,
        bridge: BridgeTheorem,
        judgment: dict[str, Any],
    ) -> dict[str, Any]:
        """Transform a judgment's coordinate and trust through the bridge.

        The judgment is re-situated in the target pack's coordinate space
        and its trust is capped by the bridge's trust level.
        """
        transformed = dict(judgment)
        if "trust" in transformed:
            original_trust = float(transformed["trust"])
            transformed["trust"] = min(original_trust, bridge.trust_level)
        transformed["pack"] = bridge.target_pack
        transformed["_bridge_id"] = bridge.bridge_id
        return transformed

    def adjust_trust(
        self,
        bridge: BridgeTheorem,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Cap evidence trust at the bridge's trust level.

        Per theory2.tex §4.3: transported evidence cannot exceed the
        bridge's own trust.  This implements the monotonic-weakening
        invariant.
        """
        adjusted = dict(evidence)
        if "trust" in adjusted:
            original = float(adjusted["trust"])
            adjusted["trust"] = min(original, bridge.trust_level)
            adjusted["_trust_adjustment"] = {
                "original": original,
                "bridge_ceiling": bridge.trust_level,
                "effective": adjusted["trust"],
            }
        return adjusted

    def record_provenance(
        self,
        bridge: BridgeTheorem,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach provenance metadata to transported evidence.

        Provenance records how the evidence arrived in the target pack,
        enabling audit trails and trust debugging.
        """
        augmented = dict(evidence)
        provenance_entry = {
            "bridge_id": bridge.bridge_id,
            "source_pack": bridge.source_pack,
            "target_pack": bridge.target_pack,
            "trust_at_transport": bridge.trust_level,
            "timestamp": _utcnow().isoformat(),
        }
        existing = augmented.get("_provenance_chain", [])
        augmented["_provenance_chain"] = [*existing, provenance_entry]
        return augmented

    def check_jurisdiction(
        self, bridge: BridgeTheorem, coordinate: str
    ) -> bool:
        """Return True if the bridge's support scope covers *coordinate*.

        Delegates to :meth:`BridgeTheorem.covers_coordinate`.
        """
        return bridge.covers_coordinate(coordinate)

    # -- internal ---------------------------------------------------------------

    def _log_application(
        self,
        bridge: BridgeTheorem,
        coordinate: str,
        *,
        success: bool,
    ) -> None:
        """Append an entry to the application log."""
        self.application_log.append({
            "bridge_id": bridge.bridge_id,
            "source_pack": bridge.source_pack,
            "target_pack": bridge.target_pack,
            "coordinate": coordinate,
            "success": success,
            "timestamp": _utcnow().isoformat(),
        })


# ---------------------------------------------------------------------------
# 7. BridgeMaintenance — staleness detection and refresh
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeMaintenance:
    """Maintains the health and currency of bridge theorems over time.

    Bridges are not eternal: the packs they connect may evolve, their
    evidence bases may be invalidated, or their trust may erode.  This
    class provides the lifecycle hooks for detecting and reacting to
    staleness (theory2.tex §4.5).

    Attributes
    ----------
    registry:
        Backing registry.
    staleness_threshold_days:
        Number of days after which a bridge is considered stale.
    archive:
        Retired bridges kept for historical reference.
    """

    registry: BridgeRegistry
    staleness_threshold_days: int = 90
    archive: list[BridgeTheorem] = field(default_factory=list)

    def check_validity(self, bridge_id: str) -> dict[str, Any]:
        """Check current validity of a bridge and return a status report.

        Returns
        -------
        dict[str, Any]
            A report with keys ``valid``, ``stale``, ``age_days``, and
            ``issues``.
        """
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            return {"valid": False, "stale": True, "age_days": -1,
                    "issues": ["Bridge not found."]}
        age = (_utcnow() - bridge.created_at).days
        issues: list[str] = []
        stale = age > self.staleness_threshold_days
        if stale:
            issues.append(
                f"Bridge is {age} days old "
                f"(threshold={self.staleness_threshold_days})."
            )
        if bridge.trust_level <= TRUST_CONTRADICTED:
            issues.append("Trust is at CONTRADICTED level.")
        if not bridge.is_verified:
            issues.append("Bridge is not verified.")
        return {
            "valid": len(issues) == 0,
            "stale": stale,
            "age_days": age,
            "issues": issues,
        }

    def detect_stale(self) -> list[BridgeTheorem]:
        """Return all bridges that exceed the staleness threshold."""
        now = _utcnow()
        return [
            b for b in self.registry.all_bridges()
            if (now - b.created_at).days > self.staleness_threshold_days
        ]

    def refresh(self, bridge_id: str) -> bool:
        """Reset the creation timestamp of a bridge to now.

        Typically called after re-verification.  Returns False if the
        bridge is not found.
        """
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            return False
        bridge.created_at = _utcnow()
        bridge.metadata["last_refreshed"] = _utcnow().isoformat()
        return True

    def retire(self, bridge_id: str, *, reason: str = "") -> bool:
        """Retire a bridge: remove from the registry and archive it.

        Returns False if the bridge is not found.
        """
        try:
            bridge = self.registry.remove(bridge_id)
        except KeyError:
            return False
        bridge.metadata["retired_at"] = _utcnow().isoformat()
        if reason:
            bridge.metadata["retirement_reason"] = reason
        self.archive.append(bridge)
        return True

    def archive_all_stale(self) -> int:
        """Retire all stale bridges.  Returns the count of retired bridges."""
        stale = self.detect_stale()
        count = 0
        for bridge in stale:
            if self.retire(bridge.bridge_id, reason="staleness"):
                count += 1
        return count

    def revalidate_after_change(
        self,
        changed_pack: str,
        verifier: BridgeVerifier,
    ) -> dict[str, bool]:
        """Re-verify every bridge that touches *changed_pack*.

        Returns a mapping of bridge_id → verification result.
        """
        results: dict[str, bool] = {}
        affected = [
            *self.registry.bridges_from(changed_pack),
            *self.registry.bridges_to(changed_pack),
        ]
        seen: set[str] = set()
        for bridge in affected:
            if bridge.bridge_id in seen:
                continue
            seen.add(bridge.bridge_id)
            results[bridge.bridge_id] = verifier.verify(bridge.bridge_id)
        return results

    def health_summary(self) -> dict[str, Any]:
        """Return an overall health summary of the bridge population."""
        all_bridges = self.registry.all_bridges()
        stale = self.detect_stale()
        verified = [b for b in all_bridges if b.is_verified]
        avg_trust = (
            sum(b.trust_level for b in all_bridges) / len(all_bridges)
            if all_bridges else 0.0
        )
        return {
            "total": len(all_bridges),
            "verified": len(verified),
            "stale": len(stale),
            "archived": len(self.archive),
            "average_trust": round(avg_trust, 3),
        }


# ---------------------------------------------------------------------------
# 8. BridgePatternLibrary — reusable bridge archetypes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgePatternLibrary:
    """Library of common bridge patterns.

    Many bridges follow recognizable structural patterns.  This library
    provides factory methods for the most common archetypes, reducing
    boilerplate and ensuring consistent metadata.

    Attributes
    ----------
    _patterns:
        Internal cache of named patterns.
    """

    _patterns: dict[str, BridgeTheorem] = field(default_factory=dict)

    def identity_bridge(self, pack_name: str) -> BridgeTheorem:
        """Create the identity bridge on a pack (A → A).

        The identity bridge is the unit of composition: composing any
        bridge with the identity yields the same bridge.
        """
        bridge = BridgeTheorem(
            source_pack=pack_name,
            target_pack=pack_name,
            theorem_statement=f"Identity bridge on {pack_name}.",
            proof_sketch="Reflexivity of overlap.",
            trust_level=TRUST_MECHANICALLY_VERIFIED,
            is_verified=True,
            metadata={"pattern": "identity"},
        )
        self._patterns[f"identity:{pack_name}"] = bridge
        return bridge

    def restriction_bridge(
        self,
        source_pack: str,
        target_pack: str,
        restricted_scope: Sequence[str],
    ) -> BridgeTheorem:
        """Create a restriction bridge that narrows the support scope.

        A restriction bridge is valid only within the given coordinate
        prefixes.  It is the standard way to express a bridge that holds
        *locally* but not globally.
        """
        bridge = BridgeTheorem(
            source_pack=source_pack,
            target_pack=target_pack,
            theorem_statement=(
                f"Restriction bridge: {source_pack} → {target_pack} "
                f"within {restricted_scope}."
            ),
            proof_sketch="Restriction of a global bridge to a sub-region.",
            trust_level=TRUST_HUMAN_ATTESTED,
            support_scope=tuple(restricted_scope),
            metadata={"pattern": "restriction"},
        )
        key = f"restriction:{source_pack}:{target_pack}"
        self._patterns[key] = bridge
        return bridge

    def composition_bridge(
        self,
        bridges: Sequence[BridgeTheorem],
        composer: BridgeComposer,
    ) -> BridgeTheorem | None:
        """Create a composition bridge from a chain using the composer.

        Delegates to :meth:`BridgeComposer.compose_chain` and tags the
        result with the ``composition`` pattern.
        """
        result = composer.compose_chain(bridges)
        if result is not None:
            result.metadata["pattern"] = "composition"
            key = f"composition:{result.source_pack}:{result.target_pack}"
            self._patterns[key] = result
        return result

    def adjunction_bridge(
        self,
        left_pack: str,
        right_pack: str,
        *,
        left_to_right_statement: str,
        right_to_left_statement: str,
    ) -> tuple[BridgeTheorem, BridgeTheorem]:
        """Create an adjunction pair (left ⇄ right) of bridges.

        An adjunction expresses a Galois-like connection between two packs:
        the left-to-right and right-to-left bridges are mutual inverses
        up to a trust penalty.
        """
        forward = BridgeTheorem(
            source_pack=left_pack,
            target_pack=right_pack,
            theorem_statement=left_to_right_statement,
            proof_sketch="Left adjoint direction.",
            trust_level=TRUST_HUMAN_ATTESTED,
            metadata={"pattern": "adjunction", "direction": "forward"},
        )
        backward = BridgeTheorem(
            source_pack=right_pack,
            target_pack=left_pack,
            theorem_statement=right_to_left_statement,
            proof_sketch="Right adjoint direction.",
            trust_level=TRUST_HUMAN_ATTESTED,
            metadata={"pattern": "adjunction", "direction": "backward"},
        )
        self._patterns[f"adjunction:{left_pack}:{right_pack}:fwd"] = forward
        self._patterns[f"adjunction:{left_pack}:{right_pack}:bwd"] = backward
        return forward, backward

    def equivalence_bridge(
        self,
        pack_a: str,
        pack_b: str,
        statement: str,
    ) -> tuple[BridgeTheorem, BridgeTheorem]:
        """Create an equivalence pair (A ⇄ B) with matched trust.

        An equivalence is a special adjunction where both directions carry
        the same trust and the round-trip is the identity.
        """
        forward = BridgeTheorem(
            source_pack=pack_a,
            target_pack=pack_b,
            theorem_statement=f"{statement} (forward: {pack_a} → {pack_b})",
            proof_sketch="Equivalence — forward direction.",
            trust_level=TRUST_SOLVER_DISCHARGED,
            is_verified=True,
            metadata={"pattern": "equivalence", "direction": "forward"},
        )
        backward = BridgeTheorem(
            source_pack=pack_b,
            target_pack=pack_a,
            theorem_statement=f"{statement} (backward: {pack_b} → {pack_a})",
            proof_sketch="Equivalence — backward direction.",
            trust_level=TRUST_SOLVER_DISCHARGED,
            is_verified=True,
            metadata={"pattern": "equivalence", "direction": "backward"},
        )
        self._patterns[f"equivalence:{pack_a}:{pack_b}:fwd"] = forward
        self._patterns[f"equivalence:{pack_a}:{pack_b}:bwd"] = backward
        return forward, backward

    def lookup_pattern(self, key: str) -> BridgeTheorem | None:
        """Look up a previously created pattern by its key."""
        return self._patterns.get(key)

    def all_patterns(self) -> dict[str, BridgeTheorem]:
        """Return a snapshot of all cached patterns."""
        return dict(self._patterns)


# ---------------------------------------------------------------------------
# 9. BridgeStatistics — usage tracking and reliability metrics
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeStatistics:
    """Tracks bridge usage, success rates, and reliability metrics.

    Every :meth:`BridgeApplication.apply` call should feed into this
    tracker so that the federation layer can prefer reliable bridges and
    retire unreliable ones.

    Attributes
    ----------
    _usage_counts:
        bridge_id → total application count.
    _success_counts:
        bridge_id → successful application count.
    _trust_losses:
        bridge_id → list of trust-loss amounts per application.
    """

    _usage_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _success_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _trust_losses: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def record_application(
        self,
        bridge_id: str,
        *,
        success: bool,
        trust_loss: float = 0.0,
    ) -> None:
        """Record one bridge application event.

        Parameters
        ----------
        bridge_id:
            The bridge that was applied.
        success:
            Whether the application succeeded.
        trust_loss:
            The trust difference between input and output evidence.
        """
        self._usage_counts[bridge_id] += 1
        if success:
            self._success_counts[bridge_id] += 1
        self._trust_losses[bridge_id].append(trust_loss)

    def usage_count(self, bridge_id: str) -> int:
        """Return total application count for *bridge_id*."""
        return self._usage_counts.get(bridge_id, 0)

    def success_rate(self, bridge_id: str) -> float:
        """Return the success rate in [0, 1] for *bridge_id*.

        Returns 0.0 if the bridge has never been applied.
        """
        total = self._usage_counts.get(bridge_id, 0)
        if total == 0:
            return 0.0
        return self._success_counts.get(bridge_id, 0) / total

    def average_trust_loss(self, bridge_id: str) -> float:
        """Return the mean trust loss for *bridge_id*.

        Returns 0.0 if the bridge has never been applied.
        """
        losses = self._trust_losses.get(bridge_id, [])
        if not losses:
            return 0.0
        return sum(losses) / len(losses)

    def most_used(self, n: int = 5) -> list[tuple[str, int]]:
        """Return the *n* most-used bridges by application count.

        Returns
        -------
        list[tuple[str, int]]
            List of (bridge_id, count) sorted descending.
        """
        items = sorted(
            self._usage_counts.items(), key=lambda kv: kv[1], reverse=True
        )
        return items[:n]

    def least_reliable(self, n: int = 5) -> list[tuple[str, float]]:
        """Return the *n* bridges with the lowest success rate.

        Only bridges that have been applied at least once are considered.

        Returns
        -------
        list[tuple[str, float]]
            List of (bridge_id, success_rate) sorted ascending.
        """
        rates: list[tuple[str, float]] = []
        for bridge_id in self._usage_counts:
            rates.append((bridge_id, self.success_rate(bridge_id)))
        rates.sort(key=lambda kv: kv[1])
        return rates[:n]

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics across all tracked bridges."""
        total_apps = sum(self._usage_counts.values())
        total_successes = sum(self._success_counts.values())
        all_losses = [
            loss
            for losses in self._trust_losses.values()
            for loss in losses
        ]
        return {
            "tracked_bridges": len(self._usage_counts),
            "total_applications": total_apps,
            "total_successes": total_successes,
            "overall_success_rate": (
                total_successes / total_apps if total_apps else 0.0
            ),
            "mean_trust_loss": (
                sum(all_losses) / len(all_losses) if all_losses else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# 10. BridgeDiagnostics — human-readable and copilot-readable reports
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeDiagnostics:
    """Diagnostic reporting for the bridge subsystem.

    Produces human-readable summaries and structured reports suitable
    for copilot agents to consume when reasoning about federation state.

    Attributes
    ----------
    registry:
        Backing bridge registry.
    statistics:
        Optional statistics tracker for usage-aware reports.
    """

    registry: BridgeRegistry
    statistics: BridgeStatistics | None = None

    def bridge_summary(self, bridge_id: str) -> str:
        """Return a multi-line human-readable summary of a single bridge."""
        bridge = self.registry.lookup(bridge_id)
        if bridge is None:
            return f"Bridge {bridge_id!r}: not found."
        lines = [
            f"Bridge: {bridge.bridge_id}",
            f"  Source:    {bridge.source_pack}",
            f"  Target:    {bridge.target_pack}",
            f"  Statement: {bridge.theorem_statement}",
            f"  Trust:     {bridge.trust_level:.2f} ({_trust_label(bridge.trust_level)})",
            f"  Verified:  {'yes' if bridge.is_verified else 'no'}",
            f"  Scope:     {', '.join(bridge.support_scope) or '(global)'}",
            f"  Age:       {(_utcnow() - bridge.created_at).days} days",
        ]
        if self.statistics:
            lines.append(
                f"  Usage:     {self.statistics.usage_count(bridge_id)} applications, "
                f"success rate {self.statistics.success_rate(bridge_id):.1%}"
            )
        return "\n".join(lines)

    def connectivity_report(self) -> str:
        """Return a text report of pack-to-pack connectivity.

        Lists every pack and the packs reachable from it in one hop.
        """
        packs = sorted(self.registry.pack_names())
        lines = ["=== Bridge Connectivity Report ==="]
        for pack in packs:
            targets = sorted(
                {b.target_pack for b in self.registry.bridges_from(pack)}
            )
            lines.append(f"  {pack} → {', '.join(targets) or '(none)'}")
        lines.append(f"  Total bridges: {len(self.registry)}")
        return "\n".join(lines)

    def trust_analysis(self) -> str:
        """Return a trust distribution analysis across all bridges."""
        bridges = self.registry.all_bridges()
        if not bridges:
            return "No bridges registered."
        trusts = [b.trust_level for b in bridges]
        avg = sum(trusts) / len(trusts)
        min_t = min(trusts)
        max_t = max(trusts)
        verified = sum(1 for b in bridges if b.is_verified)

        # Compute standard deviation inline to avoid numpy dependency.
        variance = sum((t - avg) ** 2 for t in trusts) / len(trusts)
        std_dev = math.sqrt(variance)

        lines = [
            "=== Trust Analysis ===",
            f"  Bridges:    {len(bridges)}",
            f"  Verified:   {verified} ({verified / len(bridges):.0%})",
            f"  Trust min:  {min_t:.3f}",
            f"  Trust max:  {max_t:.3f}",
            f"  Trust mean: {avg:.3f}",
            f"  Trust σ:    {std_dev:.3f}",
        ]
        return "\n".join(lines)

    def staleness_report(self, threshold_days: int = 90) -> str:
        """Return a report of bridges older than *threshold_days*."""
        now = _utcnow()
        stale = [
            b for b in self.registry.all_bridges()
            if (now - b.created_at).days > threshold_days
        ]
        if not stale:
            return f"No bridges older than {threshold_days} days."
        lines = [f"=== Stale Bridges (>{threshold_days} days) ==="]
        for b in sorted(stale, key=lambda x: x.created_at):
            age = (now - b.created_at).days
            lines.append(
                f"  [{b.bridge_id}] {b.source_pack} → {b.target_pack}  "
                f"({age} days, trust={b.trust_level:.2f})"
            )
        return "\n".join(lines)

    def copilot_bridge_summary(self) -> dict[str, Any]:
        """Produce a structured summary optimized for copilot consumption.

        Returns a dictionary that a copilot agent can parse to understand
        the current bridge landscape without reading prose reports.
        """
        bridges = self.registry.all_bridges()
        packs = sorted(self.registry.pack_names())
        edges: list[dict[str, Any]] = []
        for b in bridges:
            edge: dict[str, Any] = {
                "id": b.bridge_id,
                "source": b.source_pack,
                "target": b.target_pack,
                "trust": b.trust_level,
                "verified": b.is_verified,
            }
            if self.statistics:
                edge["usage"] = self.statistics.usage_count(b.bridge_id)
                edge["success_rate"] = self.statistics.success_rate(b.bridge_id)
            edges.append(edge)
        return {
            "packs": packs,
            "bridge_count": len(bridges),
            "edges": edges,
            "copilot_note": (
                "Use 'edges' to reason about reachability and trust. "
                "Prefer verified bridges with high success rates."
            ),
        }


# ---------------------------------------------------------------------------
# 11. BridgeSerializer — JSON round-tripping
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BridgeSerializer:
    """Serializes and deserializes bridges, registries, and paths to JSON.

    All serialization goes through :meth:`BridgeTheorem.to_dict` and
    :meth:`BridgeTheorem.from_mapping` so the format is always consistent.
    """

    indent: int = 2

    def serialize_bridge(self, bridge: BridgeTheorem) -> str:
        """Serialize a single bridge to a JSON string."""
        return json.dumps(bridge.to_dict(), indent=self.indent, default=str)

    def deserialize_bridge(self, data: str) -> BridgeTheorem:
        """Deserialize a bridge from a JSON string."""
        return BridgeTheorem.from_mapping(json.loads(data))

    def serialize_registry(self, registry: BridgeRegistry) -> str:
        """Serialize all bridges in a registry to a JSON array string."""
        payload = [b.to_dict() for b in registry.all_bridges()]
        return json.dumps(payload, indent=self.indent, default=str)

    def deserialize_registry(self, data: str) -> BridgeRegistry:
        """Deserialize a JSON array into a populated :class:`BridgeRegistry`."""
        items = json.loads(data)
        registry = BridgeRegistry()
        for item in items:
            registry.register(BridgeTheorem.from_mapping(item))
        return registry

    def serialize_path(self, path: Sequence[BridgeTheorem]) -> str:
        """Serialize a bridge path (list of bridges) to a JSON string."""
        payload = {
            "path_length": len(path),
            "bridges": [b.to_dict() for b in path],
            "source": path[0].source_pack if path else None,
            "target": path[-1].target_pack if path else None,
            "effective_trust": (
                min(b.trust_level for b in path) if path else None
            ),
        }
        return json.dumps(payload, indent=self.indent, default=str)

    def deserialize_path(self, data: str) -> list[BridgeTheorem]:
        """Deserialize a JSON path string into a list of bridges."""
        payload = json.loads(data)
        return [
            BridgeTheorem.from_mapping(item)
            for item in payload.get("bridges", [])
        ]

    def serialize_statistics(self, stats: BridgeStatistics) -> str:
        """Serialize bridge statistics to a JSON string."""
        payload = {
            "summary": stats.summary(),
            "per_bridge": {
                bid: {
                    "usage_count": stats.usage_count(bid),
                    "success_rate": stats.success_rate(bid),
                    "average_trust_loss": stats.average_trust_loss(bid),
                }
                for bid in stats._usage_counts
            },
        }
        return json.dumps(payload, indent=self.indent, default=str)

    def serialize_diagnostics(self, diagnostics: BridgeDiagnostics) -> str:
        """Serialize a full diagnostic snapshot to JSON.

        Includes the copilot summary, trust analysis text, and
        connectivity report in a single document.
        """
        payload = {
            "copilot_summary": diagnostics.copilot_bridge_summary(),
            "trust_analysis": diagnostics.trust_analysis(),
            "connectivity": diagnostics.connectivity_report(),
        }
        return json.dumps(payload, indent=self.indent, default=str)

    def round_trip_bridge(self, bridge: BridgeTheorem) -> BridgeTheorem:
        """Serialize then deserialize a bridge as a consistency check."""
        return self.deserialize_bridge(self.serialize_bridge(bridge))

    def round_trip_registry(self, registry: BridgeRegistry) -> BridgeRegistry:
        """Serialize then deserialize a registry as a consistency check."""
        return self.deserialize_registry(self.serialize_registry(registry))


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "PackBridge",
    "BridgeTheorem",
    "BridgeRegistry",
    "BridgeDiscoverer",
    "BridgeVerifier",
    "BridgeComposer",
    "BridgeApplication",
    "BridgeMaintenance",
    "BridgePatternLibrary",
    "BridgeStatistics",
    "BridgeDiagnostics",
    "BridgeSerializer",
    # Trust-level constants (useful for callers that configure trust floors).
    "TRUST_MECHANICALLY_VERIFIED",
    "TRUST_SOLVER_DISCHARGED",
    "TRUST_RUNTIME_WITNESSED",
    "TRUST_HUMAN_ATTESTED",
    "TRUST_ORACLE_PROPOSED",
    "TRUST_COPILOT_SUGGESTED",
    "TRUST_UNVERIFIED",
    "TRUST_CONTRADICTED",
]

# copilot: bridge-lifecycle module — LLM orchestration entry-points are the
#   copilot_suggest_bridge (BridgeDiscoverer), copilot_assist_verification
#   (BridgeVerifier), and copilot_bridge_summary (BridgeDiagnostics) methods.
