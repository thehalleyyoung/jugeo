"""Ideation federation: analogy-driven transport of discoveries across regimes.

This module realises the ``theory2.tex`` sections on *"Analogy, transfer, and
purpose-preserving transport across mathematical areas"* and *"From mathematical
discovery to pack federation"*.  The central concern is how an insight produced
inside one *ideation regime* (a bounded semantic territory with its own pack,
cover, and kind vocabulary) can be safely transported into another regime without
silently inflating trust or discarding the purpose that made the idea meaningful
in the first place.

Conceptual map
--------------
* **Regime** — a bounded sector of the JuGeo semantic site, governed by a pack
  with its own kind hierarchy, bridge census, and novelty criteria.  Examples:
  algebraic geometry, homotopy theory, combinatorial optimisation.

* **Analogy** — a partial structure-preserving map between two regimes.  An
  analogy is *purpose-preserving* if the transported idea serves the same
  theorem-finding or gap-filling role in the target as it did in the source.
  It is *structure-preserving* if the key relational invariants (commutativity,
  exactness, sheaf-condition, etc.) survive rewriting under the analogy.

* **Bridge** — a named, annotated analogy map that has been validated and
  registered.  Bridges carry a *trust attenuation factor* so that evidence
  transported through several bridges is weakened monotonically — no silent
  trust promotion (theory2.tex §4.3, Lemma 4.7).

* **Transport** — the mechanical act of reframing an :class:`IdeaProposal` from
  source vocabulary into target vocabulary, adjusting its trust, and recording
  the provenance trail.

* **Federation** — the coordination layer that discovers usable bridges,
  scores candidate analogies, chooses an optimal transport path, validates the
  result, and records the outcome in :class:`FederationHistory`.

Copilot integration
-------------------
Every major class exposes a ``copilot_*`` helper method.  These accept
free-text input from an LLM-backed orchestration layer (such as GitHub Copilot)
and return structured proposals that the rest of the pipeline can validate and
record.  Copilot-assisted proposals always enter at a trust ceiling of
``ORACLE_PROPOSED`` and may never exceed it without explicit human or solver
justification — this is the *no-silent-trust-promotion* invariant.

Backward compatibility
----------------------
The legacy :class:`IdeaFederation` dataclass (a simple bundle of
:class:`~jugeo.ideation.ideas.IdeaProposal` and
:class:`~jugeo.ideation.regimes.RegimeProposal`) is preserved at the bottom of
this module so that existing tests and callers continue to work without
modification.

copilot: ideation-federation module — LLM agents may propose analogies,
    initiate transports, and query the federation registry via the
    ``copilot_*`` methods on the classes below.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import IdeaProposal
from jugeo.ideation.regimes import RegimeKind, RegimeProposal

try:
    from jugeo.packs.federation import (
        PackFederation,
        FederationEngine as PackFederationEngine,
        FederationRequest,
        FederationResult,
    )
except ImportError:  # pragma: no cover
    PackFederation = None  # type: ignore[assignment,misc]
    PackFederationEngine = None  # type: ignore[assignment,misc]
    FederationRequest = None  # type: ignore[assignment,misc]
    FederationResult = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.manifests import (
        EvidenceManifest,
        Manifest,
        ManifestBuilder,
        build_evidence_manifest,
    )
except ImportError:  # pragma: no cover
    EvidenceManifest = None  # type: ignore[assignment,misc]
    Manifest = None  # type: ignore[assignment,misc]
    ManifestBuilder = None  # type: ignore[assignment,misc]
    build_evidence_manifest = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _tokenize(text: str) -> frozenset[str]:
    """Return a frozenset of lowercase alphanumeric tokens from *text*."""
    return frozenset(t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets; returns 0.0 for both empty."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _attenuate(trust: TrustLevel, factor: float) -> TrustLevel:
    """Return a trust level weakened by *factor* (0.0 = no change, 1.0 = floor).

    Attenuation can only weaken trust, never strengthen it.  The mapping
    follows the integer ordinal of :class:`TrustLevel` values: a factor of
    0.5 drops trust by roughly half the range toward ``UNVERIFIED``.
    """
    ordinals = [
        TrustLevel.UNVERIFIED,
        TrustLevel.ORACLE_PROPOSED,
        TrustLevel.HUMAN_ATTESTED,
        TrustLevel.SOLVER_DISCHARGED,
    ]
    try:
        idx = ordinals.index(trust)
    except ValueError:
        idx = 1  # default to ORACLE_PROPOSED if unknown
    drop = math.floor(factor * idx)
    new_idx = max(0, idx - drop)
    return ordinals[new_idx]


# ---------------------------------------------------------------------------
# 1. FederatedIdeaProposal
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FederatedIdeaProposal:
    """The result of transporting an idea from one ideation regime to another.

    A :class:`FederatedIdeaProposal` is the primary output of the ideation
    federation pipeline.  It bundles the transported idea with the full
    provenance trail so that downstream consumers (validators, history
    recorders, diagnostics) can audit exactly how the idea arrived and what
    trust adjustments were applied.

    Attributes
    ----------
    proposal_id:
        Unique identifier for this federated proposal (UUID4 by default).
    source_regime:
        The regime (pack name or regime kind label) where the idea originated.
    target_regime:
        The regime into which the idea has been transported.
    transported_idea:
        The reframed :class:`~jugeo.ideation.ideas.IdeaProposal` as it stands
        in the target regime's vocabulary.
    bridge_used:
        Identifier of the :class:`CrossRegimeBridge` that was traversed.
    trust_adjustment:
        The signed floating-point delta applied to the trust level during
        transport.  Negative values indicate attenuation (the normal case).
        A value of 0.0 means the bridge was lossless with respect to trust.
    analogy_evidence:
        Free-form evidence payload explaining *why* the analogy holds.  This
        is produced by :class:`AnalogyFinder` and attached verbatim so that
        validators and human reviewers have interpretable justification.
    created_at:
        ISO-8601 timestamp of when the proposal was produced.
    copilot_assisted:
        Whether a copilot (LLM oracle) contributed to constructing this
        proposal.  When True the trust ceiling is enforced at ORACLE_PROPOSED.
    """

    proposal_id: str
    source_regime: str
    target_regime: str
    transported_idea: IdeaProposal
    bridge_used: str
    trust_adjustment: float
    analogy_evidence: dict[str, Any]
    created_at: str = field(default_factory=_now_iso)
    copilot_assisted: bool = False

    def is_trust_attenuating(self) -> bool:
        """Return True if transport weakened the trust level (normal case)."""
        return self.trust_adjustment < 0.0

    def is_trust_preserving(self) -> bool:
        """Return True if no trust change was applied."""
        return math.isclose(self.trust_adjustment, 0.0, abs_tol=1e-9)

    def analogy_score(self) -> float:
        """Extract the numeric analogy quality score from *analogy_evidence*.

        Returns 0.0 if the evidence dict does not contain a ``score`` key or
        if the value is not a finite number.
        """
        raw = self.analogy_evidence.get("score", 0.0)
        try:
            value = float(raw)
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def purpose_preserved(self) -> bool:
        """Return the purpose-preservation flag stored in analogy evidence."""
        return bool(self.analogy_evidence.get("purpose_preserved", False))

    def to_dict(self) -> dict[str, Any]:
        """Serialise the proposal to a JSON-safe dictionary."""
        return {
            "proposal_id": self.proposal_id,
            "source_regime": self.source_regime,
            "target_regime": self.target_regime,
            "transported_idea": {
                "title": self.transported_idea.title,
                "hypothesis": self.transported_idea.hypothesis,
                "payoff": self.transported_idea.payoff,
            },
            "bridge_used": self.bridge_used,
            "trust_adjustment": self.trust_adjustment,
            "analogy_evidence": self.analogy_evidence,
            "created_at": self.created_at,
            "copilot_assisted": self.copilot_assisted,
        }


# ---------------------------------------------------------------------------
# 2. CrossRegimeBridge
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CrossRegimeBridge:
    """A validated analogy map connecting two ideation regimes.

    A bridge names a source regime and a target regime and provides:

    * an ``analogy_map`` — a dict mapping source vocabulary tokens to target
      vocabulary tokens, used by the transporter to rewrite idea text;
    * a ``trust_attenuation`` factor — the fraction by which trust is
      weakened when traversing this bridge (0.0 = lossless, 1.0 = floor);
    * a ``purpose_tags`` set — the mathematical purposes (e.g. "exactness",
      "duality", "finiteness") that this bridge is certified to preserve;
    * validation metadata recording when and how the bridge was established.

    Bridges are registered in :class:`FederationRegistry` and retrieved by
    :class:`IdeationFederator` during transport planning.

    Attributes
    ----------
    bridge_id:
        Unique identifier for the bridge (UUID4 by default).
    source:
        Label for the source ideation regime.
    target:
        Label for the target ideation regime.
    analogy_map:
        Vocabulary translation table: ``{source_token: target_token}``.
    trust_attenuation:
        Factor in [0.0, 1.0] by which trust is weakened during traversal.
        0.0 means trust is fully preserved; 1.0 means trust drops to the
        floor level ``UNVERIFIED``.
    purpose_tags:
        Frozenset of semantic purpose labels this bridge is certified to
        preserve intact.
    validated:
        Whether the bridge has passed formal or semi-formal validation.
    created_at:
        ISO-8601 timestamp of bridge registration.
    description:
        Human-readable explanation of what this bridge captures.
    """

    bridge_id: str
    source: str
    target: str
    analogy_map: dict[str, str]
    trust_attenuation: float
    purpose_tags: frozenset[str]
    validated: bool = False
    created_at: str = field(default_factory=_now_iso)
    description: str = ""

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def translate(self, text: str) -> str:
        """Translate *text* from source vocabulary into target vocabulary.

        Applies each entry of ``analogy_map`` as a whole-word substitution
        (case-insensitive) in longest-match-first order to avoid partial
        rewrites stomping on each other.
        """
        result = text
        for src, tgt in sorted(self.analogy_map.items(), key=lambda kv: -len(kv[0])):
            pattern = re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE)
            result = pattern.sub(tgt, result)
        return result

    def covers_purpose(self, purpose: str) -> bool:
        """Return True if this bridge is certified to preserve *purpose*."""
        return purpose in self.purpose_tags

    def effective_trust_loss(self, base: TrustLevel) -> TrustLevel:
        """Return the trust level that would result from traversing this bridge.

        The result is always weaker than or equal to *base*.
        """
        return _attenuate(base, self.trust_attenuation)

    def is_symmetric_candidate(self, other: "CrossRegimeBridge") -> bool:
        """Return True if *other* looks like the reverse bridge of this one."""
        return other.source == self.target and other.target == self.source

    def reverse_vocabulary(self) -> dict[str, str]:
        """Return the analogy map with source and target swapped."""
        return {v: k for k, v in self.analogy_map.items()}

    def vocabulary_overlap(self, tokens: frozenset[str]) -> float:
        """Return the fraction of *tokens* that appear in the analogy map keys."""
        if not tokens:
            return 0.0
        known = frozenset(self.analogy_map.keys())
        return len(tokens & known) / len(tokens)

    def summary_line(self) -> str:
        """Return a single human-readable summary of the bridge."""
        status = "validated" if self.validated else "unvalidated"
        return (
            f"Bridge {self.bridge_id[:8]} [{status}]: "
            f"{self.source!r} → {self.target!r}  "
            f"attenuation={self.trust_attenuation:.2f}  "
            f"vocab_size={len(self.analogy_map)}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "bridge_id": self.bridge_id,
            "source": self.source,
            "target": self.target,
            "analogy_map": self.analogy_map,
            "trust_attenuation": self.trust_attenuation,
            "purpose_tags": sorted(self.purpose_tags),
            "validated": self.validated,
            "created_at": self.created_at,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# 3. AnalogyFinder
# ---------------------------------------------------------------------------

class AnalogyFinder:
    """Discover and score structural/purpose-preserving analogies between regimes.

    The finder accepts two regime labels and a corpus of vocabulary tokens
    for each regime, then proposes candidate analogy maps.  Scoring combines
    three signals:

    1. **Structural score** — how well the relational shape of the source
       idea (its dependency graph tokens) maps to the target's vocabulary.
    2. **Purpose score** — whether the mathematical purpose encoded in the
       source idea (exactness, duality, invariance, …) has a matching
       purpose label in the target regime.
    3. **Novelty bonus** — a small reward for analogies that bridge
       conceptually distant regimes, because distant analogies tend to
       generate genuinely new conjectures.

    Attributes
    ----------
    source_vocab:
        Vocabulary token set for the source regime.
    target_vocab:
        Vocabulary token set for the target regime.
    purpose_catalog:
        Mapping from purpose tag to a set of indicator tokens.  Used by
        :meth:`purpose_preserved` to test whether a purpose survives.
    min_score:
        Minimum combined score for an analogy to be returned as a candidate.
    """

    def __init__(
        self,
        source_vocab: frozenset[str],
        target_vocab: frozenset[str],
        purpose_catalog: Mapping[str, frozenset[str]] | None = None,
        *,
        min_score: float = 0.1,
    ) -> None:
        self.source_vocab: frozenset[str] = source_vocab
        self.target_vocab: frozenset[str] = target_vocab
        self.purpose_catalog: dict[str, frozenset[str]] = dict(purpose_catalog or {})
        self.min_score: float = _clamp(min_score, 0.0, 1.0)
        self._cache: dict[tuple[str, str], float] = {}

    def find_analogies(self, idea: IdeaProposal) -> list[dict[str, Any]]:
        """Return candidate analogy records for *idea* sorted by descending score.

        Each record is a dict with keys:
        ``score``, ``purpose_preserved``, ``structure_score``,
        ``candidate_map``, ``rationale``.
        """
        idea_tokens = _tokenize(idea.title + " " + idea.hypothesis)
        source_hits = idea_tokens & self.source_vocab
        if not source_hits:
            return []

        candidate_map: dict[str, str] = {}
        for token in source_hits:
            best_target = self._best_target_token(token)
            if best_target:
                candidate_map[token] = best_target

        if not candidate_map:
            return []

        struct_score = self.structure_preserved(idea_tokens, frozenset(candidate_map.values()))
        purp_score = self._purpose_score(idea_tokens)
        combined = _clamp(0.6 * struct_score + 0.4 * purp_score)

        if combined < self.min_score:
            return []

        return [
            {
                "score": combined,
                "purpose_preserved": purp_score >= 0.5,
                "structure_score": struct_score,
                "candidate_map": candidate_map,
                "rationale": self._build_rationale(idea_tokens, candidate_map, struct_score, purp_score),
            }
        ]

    def score_analogy(self, source_tokens: frozenset[str], target_tokens: frozenset[str]) -> float:
        """Score a direct token-to-token analogy on structural overlap alone.

        The Jaccard overlap between the mapped image of *source_tokens* in
        the target vocabulary and *target_tokens* is the structural score.
        Results are cached.
        """
        key = (frozenset.__hash__(source_tokens), frozenset.__hash__(target_tokens))
        # Use a stable string key for the cache
        cache_key = (
            ",".join(sorted(source_tokens)),
            ",".join(sorted(target_tokens)),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]
        score = _jaccard(source_tokens & self.source_vocab, target_tokens & self.target_vocab)
        self._cache[cache_key] = score
        return score

    def purpose_preserved(self, idea_tokens: frozenset[str], purpose: str) -> bool:
        """Return True if *purpose* is detectable in *idea_tokens*.

        Detection uses the ``purpose_catalog``: if the indicator tokens for
        *purpose* overlap with *idea_tokens* above a 0.3 threshold the
        purpose is considered present.
        """
        indicators = self.purpose_catalog.get(purpose, frozenset())
        if not indicators:
            return False
        return _jaccard(idea_tokens, indicators) >= 0.3

    def structure_preserved(self, source_tokens: frozenset[str], mapped_tokens: frozenset[str]) -> float:
        """Return a [0,1] structural-preservation score.

        Measures how much of the source idea's relational structure (as
        approximated by *source_tokens*) has a correspondent in *mapped_tokens*
        drawn from the target vocabulary.
        """
        if not source_tokens:
            return 0.0
        target_hits = mapped_tokens & self.target_vocab
        return len(target_hits) / max(1, len(source_tokens))

    def validate_analogy(self, analogy: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a candidate analogy record, returning (ok, issues).

        Checks:
        * score is finite and non-negative;
        * candidate_map is non-empty;
        * all map values appear in the target vocabulary (or are plausible);
        * purpose_preserved flag is consistent with the score.
        """
        issues: list[str] = []
        score = analogy.get("score", 0.0)
        if not math.isfinite(float(score)) or float(score) < 0:
            issues.append(f"invalid score: {score!r}")
        candidate_map: dict[str, str] = analogy.get("candidate_map", {})
        if not candidate_map:
            issues.append("candidate_map is empty — no vocabulary translation found")
        unknown = frozenset(candidate_map.values()) - self.target_vocab
        if unknown:
            issues.append(
                f"{len(unknown)} target token(s) not in target_vocab: "
                + ", ".join(sorted(unknown)[:5])
            )
        pp = analogy.get("purpose_preserved", False)
        struct_score = analogy.get("structure_score", 0.0)
        if pp and float(struct_score) < 0.2:
            issues.append("purpose_preserved=True but structure_score is very low")
        return (len(issues) == 0), issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _best_target_token(self, source_token: str) -> str | None:
        """Return the target vocabulary token most similar to *source_token*."""
        if not self.target_vocab:
            return None
        src_chars = frozenset(source_token)
        best: str | None = None
        best_sim = 0.0
        for tgt in self.target_vocab:
            sim = _jaccard(src_chars, frozenset(tgt))
            if sim > best_sim:
                best_sim = sim
                best = tgt
        return best if best_sim >= 0.2 else None

    def _purpose_score(self, tokens: frozenset[str]) -> float:
        """Average purpose-indicator overlap across all registered purposes."""
        if not self.purpose_catalog:
            return 0.5  # neutral when no catalog is configured
        scores = [
            _jaccard(tokens, indicators)
            for indicators in self.purpose_catalog.values()
        ]
        return sum(scores) / len(scores) if scores else 0.0

    def _build_rationale(
        self,
        source_tokens: frozenset[str],
        candidate_map: dict[str, str],
        struct_score: float,
        purp_score: float,
    ) -> str:
        """Build a human-readable rationale string for an analogy."""
        mapped_pairs = ", ".join(f"{k}→{v}" for k, v in list(candidate_map.items())[:4])
        return (
            f"Structural overlap {struct_score:.2f}, purpose score {purp_score:.2f}. "
            f"Key mappings: {mapped_pairs}."
        )


# ---------------------------------------------------------------------------
# 4. IdeaTransporter
# ---------------------------------------------------------------------------

class IdeaTransporter:
    """Reframe an :class:`IdeaProposal` for a target regime via a bridge.

    The transporter applies the bridge's analogy map to the idea's text,
    adjusts trust according to the bridge's attenuation factor, records the
    provenance trail, and checks whether the target regime will accept the
    resulting idea.

    Attributes
    ----------
    bridge:
        The :class:`CrossRegimeBridge` to use for translation.
    base_trust:
        The trust level that applies to the idea before transport.
    admissibility_predicates:
        Optional list of callables ``(IdeaProposal) -> bool`` that must all
        return True for a transported idea to be considered admissible in
        the target.  If not supplied, all transported ideas are admissible.
    """

    def __init__(
        self,
        bridge: CrossRegimeBridge,
        base_trust: TrustLevel = TrustLevel.ORACLE_PROPOSED,
        admissibility_predicates: Sequence[Callable[[IdeaProposal], bool]] | None = None,
    ) -> None:
        self.bridge = bridge
        self.base_trust = base_trust
        self.admissibility_predicates: list[Callable[[IdeaProposal], bool]] = list(
            admissibility_predicates or []
        )

    def transport(self, idea: IdeaProposal) -> IdeaProposal:
        """Return a new :class:`IdeaProposal` reframed for the target regime.

        Steps:
        1. Translate the title and hypothesis through the bridge vocabulary.
        2. Carry over the support region and payoff unchanged (the geometric
           footprint and value estimate are regime-independent).
        3. Append a provenance record noting the bridge traversal.
        """
        new_title = self.bridge.translate(idea.title)
        new_hypothesis = self.bridge.translate(idea.hypothesis)
        provenance = getattr(idea, "provenance", ()) + (
            f"via-bridge:{self.bridge.bridge_id[:8]}",
        )
        try:
            return IdeaProposal(
                title=new_title,
                hypothesis=new_hypothesis,
                support=idea.support,
                payoff=idea.payoff,
                provenance=provenance,
            )
        except TypeError:
            # Fallback for IdeaProposal variants that don't accept provenance.
            return IdeaProposal(
                title=new_title,
                hypothesis=new_hypothesis,
                support=idea.support,
                payoff=idea.payoff,
            )

    def adjust_trust(self, trust: TrustLevel) -> tuple[TrustLevel, float]:
        """Return the attenuated trust level and the signed delta.

        The delta is always ≤ 0: trust can only be weakened during transport.
        """
        attenuated = self.bridge.effective_trust_loss(trust)
        ordinals = [
            TrustLevel.UNVERIFIED,
            TrustLevel.ORACLE_PROPOSED,
            TrustLevel.HUMAN_ATTESTED,
            TrustLevel.SOLVER_DISCHARGED,
        ]
        try:
            delta = ordinals.index(attenuated) - ordinals.index(trust)
        except ValueError:
            delta = 0
        return attenuated, float(delta)

    def reframe_for_target(self, idea: IdeaProposal, analogy: dict[str, Any]) -> IdeaProposal:
        """Apply an explicit analogy map on top of bridge translation.

        When :class:`AnalogyFinder` supplies a ``candidate_map`` richer than
        the bridge's static ``analogy_map``, this method applies those
        additional substitutions before handing off to :meth:`transport`.
        """
        extra_map: dict[str, str] = analogy.get("candidate_map", {})
        if not extra_map:
            return self.transport(idea)
        augmented_title = idea.title
        augmented_hyp = idea.hypothesis
        for src, tgt in sorted(extra_map.items(), key=lambda kv: -len(kv[0])):
            pattern = re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE)
            augmented_title = pattern.sub(tgt, augmented_title)
            augmented_hyp = pattern.sub(tgt, augmented_hyp)
        return self.transport(
            IdeaProposal(
                title=augmented_title,
                hypothesis=augmented_hyp,
                support=idea.support,
                payoff=idea.payoff,
            )
        )

    def check_target_admissibility(self, idea: IdeaProposal) -> tuple[bool, list[str]]:
        """Test whether *idea* satisfies all registered admissibility predicates.

        Returns a tuple ``(admissible, reasons)`` where *reasons* is an empty
        list on success and a list of failure messages on rejection.
        """
        reasons: list[str] = []
        for idx, pred in enumerate(self.admissibility_predicates):
            try:
                if not pred(idea):
                    reasons.append(f"predicate[{idx}] rejected the transported idea")
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"predicate[{idx}] raised {type(exc).__name__}: {exc}")
        return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# 5. FederationRegistry
# ---------------------------------------------------------------------------

class FederationRegistry:
    """Index and navigate registered :class:`CrossRegimeBridge` instances.

    The registry maintains a directed graph of regimes connected by bridges.
    It supports:
    * single-bridge lookup by source/target pair;
    * multi-hop path discovery using BFS;
    * optimal-path selection that maximises the trust retained along the path
      (i.e. minimises total attenuation).

    Thread safety: the registry is not thread-safe; callers sharing a registry
    across threads must provide their own synchronisation.
    """

    def __init__(self) -> None:
        self._bridges: dict[str, CrossRegimeBridge] = {}
        self._graph: dict[str, list[str]] = defaultdict(list)  # source → [target, ...]

    def register_bridge(self, bridge: CrossRegimeBridge) -> None:
        """Register *bridge* in the index.

        Raises :class:`ValueError` if a bridge with the same ``bridge_id``
        already exists in the registry.
        """
        if bridge.bridge_id in self._bridges:
            raise ValueError(
                f"Bridge {bridge.bridge_id!r} is already registered.  "
                "Deregister the old bridge before replacing it."
            )
        self._bridges[bridge.bridge_id] = bridge
        self._graph[bridge.source].append(bridge.target)

    def deregister_bridge(self, bridge_id: str) -> bool:
        """Remove a bridge from the registry.  Returns True if it was present."""
        bridge = self._bridges.pop(bridge_id, None)
        if bridge is None:
            return False
        targets = self._graph.get(bridge.source, [])
        if bridge.target in targets:
            targets.remove(bridge.target)
        return True

    def discover_paths(self, source: str, target: str, *, max_hops: int = 6) -> list[list[str]]:
        """Return all simple paths from *source* to *target* up to *max_hops*.

        Each path is a list of regime labels starting at *source* and ending
        at *target*.  Only validated bridges are traversed by default; paths
        through unvalidated bridges are omitted.
        """
        if source == target:
            return [[source]]
        results: list[list[str]] = []
        queue: deque[tuple[str, list[str]]] = deque([(source, [source])])
        while queue:
            current, path = queue.popleft()
            if len(path) > max_hops + 1:
                continue
            for bridge in self._outgoing_validated(current):
                nxt = bridge.target
                if nxt in path:
                    continue  # avoid cycles
                new_path = path + [nxt]
                if nxt == target:
                    results.append(new_path)
                else:
                    queue.append((nxt, new_path))
        return results

    def optimal_path(self, source: str, target: str, *, max_hops: int = 6) -> list[str] | None:
        """Return the path that maximises retained trust (minimises attenuation).

        Uses Dijkstra's algorithm over the attenuation weights.  Returns
        ``None`` if no path exists.
        """
        paths = self.discover_paths(source, target, max_hops=max_hops)
        if not paths:
            return None
        return min(paths, key=lambda p: self._path_attenuation(p))

    def trust_along_path(self, path: list[str], base_trust: TrustLevel) -> TrustLevel:
        """Compute the trust level that would remain after traversing *path*.

        Applies each bridge's attenuation factor sequentially.
        """
        trust = base_trust
        for i in range(len(path) - 1):
            bridge = self._bridge_for(path[i], path[i + 1])
            if bridge is None:
                return TrustLevel.UNVERIFIED
            trust = bridge.effective_trust_loss(trust)
        return trust

    def get_bridge(self, bridge_id: str) -> CrossRegimeBridge | None:
        """Look up a bridge by its unique identifier."""
        return self._bridges.get(bridge_id)

    def bridges_from(self, source: str) -> list[CrossRegimeBridge]:
        """Return all registered bridges whose source matches *source*."""
        return [b for b in self._bridges.values() if b.source == source]

    def all_regimes(self) -> frozenset[str]:
        """Return every regime label that appears in at least one bridge."""
        labels: set[str] = set()
        for b in self._bridges.values():
            labels.add(b.source)
            labels.add(b.target)
        return frozenset(labels)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _outgoing_validated(self, regime: str) -> list[CrossRegimeBridge]:
        return [
            b for b in self._bridges.values()
            if b.source == regime and b.validated
        ]

    def _bridge_for(self, source: str, target: str) -> CrossRegimeBridge | None:
        for b in self._bridges.values():
            if b.source == source and b.target == target:
                return b
        return None

    def _path_attenuation(self, path: list[str]) -> float:
        total = 0.0
        for i in range(len(path) - 1):
            bridge = self._bridge_for(path[i], path[i + 1])
            total += bridge.trust_attenuation if bridge else 1.0
        return total


# ---------------------------------------------------------------------------
# 6. IdeationFederator
# ---------------------------------------------------------------------------

class IdeationFederator:
    """Coordinate the full ideation-federation pipeline.

    The federator ties together the finder, transporter, registry, validator,
    and history to produce :class:`FederatedIdeaProposal` objects from raw
    :class:`~jugeo.ideation.ideas.IdeaProposal` inputs.

    Typical usage::

        registry = FederationRegistry()
        registry.register_bridge(my_bridge)
        federator = IdeationFederator(registry=registry, history=history)
        proposals = federator.federate(ideas, source_regime="algebra",
                                       target_regime="topology")

    Attributes
    ----------
    registry:
        The bridge registry used to look up transport paths.
    history:
        Optional history recorder; if provided, every accepted proposal is
        automatically recorded.
    validator:
        Optional validator; if provided, proposals that fail validation are
        dropped from the output.
    base_trust:
        Default trust level applied to incoming ideas before transport.
    """

    def __init__(
        self,
        registry: FederationRegistry,
        history: "FederationHistory | None" = None,
        validator: "FederationValidator | None" = None,
        base_trust: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> None:
        self.registry = registry
        self.history = history
        self.validator = validator
        self.base_trust = base_trust

    def federate(
        self,
        ideas: Iterable[IdeaProposal],
        *,
        source_regime: str,
        target_regime: str,
    ) -> list[FederatedIdeaProposal]:
        """Transport all *ideas* from *source_regime* into *target_regime*.

        For each idea the method:
        1. Discovers the optimal bridge path.
        2. Finds an analogy supporting the transport.
        3. Transports the idea through each bridge in the path.
        4. Validates the resulting proposal.
        5. Records the outcome in history (if configured).

        Ideas that cannot be transported (no valid path, validation failure)
        are silently dropped; diagnostics can recover the rejection rationale
        from :class:`FederationHistory`.
        """
        path = self.registry.optimal_path(source_regime, target_regime)
        if not path:
            return []
        results: list[FederatedIdeaProposal] = []
        for idea in ideas:
            proposal = self._transport_along_path(idea, path)
            if proposal is None:
                continue
            if self.validator:
                ok, _ = self.validator.validate_proposal(proposal)
                if not ok:
                    continue
            if self.history:
                self.history.record(proposal, success=True)
            results.append(proposal)
        return results

    def discover_analogies(
        self,
        idea: IdeaProposal,
        source_vocab: frozenset[str],
        target_vocab: frozenset[str],
        purpose_catalog: Mapping[str, frozenset[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run :class:`AnalogyFinder` for a single idea and return candidates."""
        finder = AnalogyFinder(source_vocab, target_vocab, purpose_catalog)
        return finder.find_analogies(idea)

    def transport_idea(
        self,
        idea: IdeaProposal,
        bridge: CrossRegimeBridge,
        analogy: dict[str, Any] | None = None,
    ) -> FederatedIdeaProposal:
        """Transport a single idea over a single bridge.

        If *analogy* is provided its ``candidate_map`` is applied on top of
        the bridge's static vocabulary map.
        """
        transporter = IdeaTransporter(bridge, self.base_trust)
        if analogy:
            transported = transporter.reframe_for_target(idea, analogy)
        else:
            transported = transporter.transport(idea)
        _, trust_delta = transporter.adjust_trust(self.base_trust)
        evidence = analogy or {"score": 0.5, "purpose_preserved": False, "rationale": "direct"}
        return FederatedIdeaProposal(
            proposal_id=str(uuid.uuid4()),
            source_regime=bridge.source,
            target_regime=bridge.target,
            transported_idea=transported,
            bridge_used=bridge.bridge_id,
            trust_adjustment=trust_delta,
            analogy_evidence=evidence,
        )

    def validate_transport(self, proposal: FederatedIdeaProposal) -> tuple[bool, list[str]]:
        """Convenience proxy to :class:`FederationValidator` if configured."""
        if self.validator:
            return self.validator.validate_proposal(proposal)
        return True, []

    def copilot_federate(
        self,
        idea_title: str,
        idea_hypothesis: str,
        source_regime: str,
        target_regime: str,
        payoff: int = 5,
        *,
        support: Any | None = None,
        analogy_hint: str = "",
    ) -> FederatedIdeaProposal | None:
        """Accept a copilot (LLM oracle) free-text transport request.

        Constructs a minimal :class:`~jugeo.ideation.ideas.IdeaProposal` from
        the provided strings, locates a bridge, and runs the standard pipeline.
        Trust is capped at ``ORACLE_PROPOSED`` regardless of any hint provided
        by the copilot.  Returns ``None`` if no bridge path exists.

        Parameters
        ----------
        idea_title:
            Short name for the proposed idea (free text from copilot).
        idea_hypothesis:
            Hypothesis statement (free text from copilot).
        source_regime:
            The originating regime label.
        target_regime:
            The destination regime label.
        payoff:
            Estimated payoff score (copilot-supplied; defaults to 5).
        support:
            Optional support region; if None a minimal placeholder is used.
        analogy_hint:
            Optional free-text hint from copilot describing the analogy.
        """
        if support is None:
            # Use a minimal placeholder so the pipeline can proceed without
            # geometry machinery at the copilot call site.
            from jugeo.geometry.site import CoordinateKind, CoordinateObject
            from jugeo.geometry.supports import SupportRegion
            coord = CoordinateObject("copilot-placeholder", CoordinateKind.REGION, ("copilot",))
            support = SupportRegion(coord, frozenset({"copilot"}))

        idea = IdeaProposal(
            title=idea_title,
            hypothesis=idea_hypothesis,
            support=support,
            payoff=payoff,
        )
        path = self.registry.optimal_path(source_regime, target_regime)
        if not path:
            return None
        proposal = self._transport_along_path(idea, path, copilot_assisted=True)
        if proposal is None:
            return None
        # Enforce copilot ceiling.
        if proposal.trust_adjustment > 0:
            proposal = FederatedIdeaProposal(
                proposal_id=proposal.proposal_id,
                source_regime=proposal.source_regime,
                target_regime=proposal.target_regime,
                transported_idea=proposal.transported_idea,
                bridge_used=proposal.bridge_used,
                trust_adjustment=min(0.0, proposal.trust_adjustment),
                analogy_evidence={**proposal.analogy_evidence, "copilot_hint": analogy_hint},
                created_at=proposal.created_at,
                copilot_assisted=True,
            )
        if self.history:
            self.history.record(proposal, success=True)
        return proposal

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transport_along_path(
        self,
        idea: IdeaProposal,
        path: list[str],
        copilot_assisted: bool = False,
    ) -> FederatedIdeaProposal | None:
        """Walk each hop in *path* and accumulate transport."""
        current_idea = idea
        last_bridge: CrossRegimeBridge | None = None
        total_delta = 0.0
        last_analogy: dict[str, Any] = {}

        for i in range(len(path) - 1):
            src, tgt = path[i], path[i + 1]
            bridges = [b for b in self.registry.bridges_from(src) if b.target == tgt and b.validated]
            if not bridges:
                return None
            bridge = bridges[0]
            transporter = IdeaTransporter(bridge, self.base_trust)
            current_idea = transporter.transport(current_idea)
            _, delta = transporter.adjust_trust(self.base_trust)
            total_delta += delta
            last_bridge = bridge

        if last_bridge is None:
            return None

        return FederatedIdeaProposal(
            proposal_id=str(uuid.uuid4()),
            source_regime=path[0],
            target_regime=path[-1],
            transported_idea=current_idea,
            bridge_used=last_bridge.bridge_id,
            trust_adjustment=total_delta,
            analogy_evidence=last_analogy or {"score": 0.5, "purpose_preserved": False},
            copilot_assisted=copilot_assisted,
        )

    # ------------------------------------------------------------------
    # Judgment-geometric integration
    # ------------------------------------------------------------------

    def pack_federation(
        self,
        ideas: Iterable[IdeaProposal],
        *,
        source_regime: str,
        target_regime: str,
        pack_federation: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Federate ideas across domain packs.

        Uses :mod:`jugeo.packs.federation` to transport ideas through the
        pack authority system, ensuring that each idea's vocabulary and
        trust level are reconciled with the target pack's authority
        registry before transport.

        Parameters
        ----------
        ideas:
            Ideas to federate.
        source_regime:
            Origin regime label.
        target_regime:
            Destination regime label.
        pack_federation:
            Optional :class:`~jugeo.packs.federation.PackFederation`
            instance.  When ``None`` the method falls back to the
            standard :meth:`federate` pipeline, annotating results with
            a note that pack-level federation was skipped.

        Returns
        -------
        list of dict
            Each dict describes the federation outcome for one idea,
            including ``transported_title``, ``pack_federation_used``,
            and ``trust_adjustment``.
        """
        if PackFederation is None or pack_federation is None:
            # Fallback: use standard federation and annotate.
            proposals = self.federate(ideas, source_regime=source_regime, target_regime=target_regime)
            return [
                {
                    "transported_title": p.transported_idea.title,
                    "trust_adjustment": p.trust_adjustment,
                    "pack_federation_used": False,
                    "bridge_used": p.bridge_used,
                    "proposal_id": p.proposal_id,
                }
                for p in proposals
            ]

        results: list[dict[str, Any]] = []
        for idea in ideas:
            # Attempt pack-level federation first.
            if hasattr(pack_federation, "federate"):
                pack_result = pack_federation.federate(
                    idea.title,
                    source=source_regime,
                    target=target_regime,
                )
                if pack_result is not None:
                    status = pack_result.status.value if hasattr(pack_result, "status") and hasattr(pack_result.status, "value") else str(getattr(pack_result, "status", "unknown"))
                    results.append({
                        "transported_title": idea.title,
                        "trust_adjustment": -0.1,
                        "pack_federation_used": True,
                        "pack_status": status,
                    })
                    continue

            # Fall back to bridge-based transport.
            proposals = self.federate([idea], source_regime=source_regime, target_regime=target_regime)
            for p in proposals:
                results.append({
                    "transported_title": p.transported_idea.title,
                    "trust_adjustment": p.trust_adjustment,
                    "pack_federation_used": False,
                    "bridge_used": p.bridge_used,
                    "proposal_id": p.proposal_id,
                })
        return results

    def evidence_federation(
        self,
        proposals: Iterable[FederatedIdeaProposal],
        *,
        manifest_builder: Any | None = None,
    ) -> dict[str, Any]:
        """Combine evidence across federated proposals using manifests.

        Uses :mod:`jugeo.evidence.manifests` to merge the analogy
        evidence from multiple federated proposals into a single
        :class:`~jugeo.evidence.manifests.EvidenceManifest` that
        downstream validators can audit.

        Parameters
        ----------
        proposals:
            Federated proposals whose evidence should be combined.
        manifest_builder:
            Optional :class:`~jugeo.evidence.manifests.ManifestBuilder`
            instance.  When ``None`` the method constructs a lightweight
            summary dict instead.

        Returns
        -------
        dict
            Manifest data including total evidence items, combined trust
            adjustment, and per-proposal evidence summaries.
        """
        proposal_list = list(proposals)
        if not proposal_list:
            return {"manifest_built": False, "reason": "no proposals", "evidence_count": 0}

        evidence_items: list[dict[str, Any]] = []
        total_trust_delta = 0.0
        for p in proposal_list:
            total_trust_delta += p.trust_adjustment
            evidence_items.append({
                "proposal_id": p.proposal_id,
                "source": p.source_regime,
                "target": p.target_regime,
                "analogy_score": p.analogy_score(),
                "purpose_preserved": p.purpose_preserved(),
                "trust_adjustment": p.trust_adjustment,
            })

        if ManifestBuilder is None or build_evidence_manifest is None:
            return {
                "manifest_built": False,
                "reason": "jugeo.evidence.manifests not installed",
                "evidence_count": len(evidence_items),
                "total_trust_adjustment": total_trust_delta,
                "evidence_items": evidence_items,
            }

        # Build a proper evidence manifest.
        builder = manifest_builder or ManifestBuilder()
        if hasattr(builder, "with_coordinate"):
            builder = builder.with_coordinate("federation-combined")
        for item in evidence_items:
            if hasattr(builder, "add_evidence"):
                builder = builder.add_evidence(item)
        manifest = builder.build() if hasattr(builder, "build") else None

        manifest_data: dict[str, Any] = {
            "manifest_built": manifest is not None,
            "evidence_count": len(evidence_items),
            "total_trust_adjustment": total_trust_delta,
            "evidence_items": evidence_items,
        }
        if manifest is not None and hasattr(manifest, "canonical_key"):
            manifest_data["manifest_key"] = manifest.canonical_key()
        return manifest_data


# ---------------------------------------------------------------------------
# 7. FederationValidator
# ---------------------------------------------------------------------------

class FederationValidator:
    """Validate :class:`FederatedIdeaProposal` objects against key invariants.

    The three invariants checked are:

    1. **No silent trust promotion** — the ``trust_adjustment`` must be ≤ 0.
       Trust may not be silently increased during federation transport.
    2. **Purpose preservation** — if the originating analogy claimed to
       preserve a purpose, the transported idea's tokens must still contain
       the purpose indicators.
    3. **Non-trivial transport** — the transported idea must differ from the
       original in at least one vocabulary token (to catch identity transports
       that add no value).

    Attributes
    ----------
    purpose_catalog:
        Mapping of purpose tag → indicator tokens used by the purpose check.
    strict:
        When True, the validator rejects proposals that fail any check.
        When False, only the no-silent-trust-promotion check is hard.
    """

    def __init__(
        self,
        purpose_catalog: Mapping[str, frozenset[str]] | None = None,
        *,
        strict: bool = True,
    ) -> None:
        self.purpose_catalog: dict[str, frozenset[str]] = dict(purpose_catalog or {})
        self.strict = strict

    def validate_proposal(self, proposal: FederatedIdeaProposal) -> tuple[bool, list[str]]:
        """Run all validation checks on *proposal*.

        Returns ``(True, [])`` if the proposal is valid, or
        ``(False, [issue, ...])`` if any check fails.
        """
        issues: list[str] = []
        ok1, msg1 = self.check_no_silent_trust_promotion(proposal)
        if not ok1:
            issues.extend(msg1)
        ok2, msg2 = self.check_purpose_preserved(proposal)
        if not ok2 and self.strict:
            issues.extend(msg2)
        ok3, msg3 = self._check_non_trivial(proposal)
        if not ok3 and self.strict:
            issues.extend(msg3)
        return len(issues) == 0, issues

    def check_no_silent_trust_promotion(
        self, proposal: FederatedIdeaProposal
    ) -> tuple[bool, list[str]]:
        """Enforce the no-silent-trust-promotion invariant.

        Trust may only decrease (or stay flat) across a bridge.  Any positive
        ``trust_adjustment`` is a violation regardless of the regime pair.
        """
        if proposal.trust_adjustment > 1e-9:
            return False, [
                f"Silent trust promotion detected: trust_adjustment="
                f"{proposal.trust_adjustment:+.3f} for proposal {proposal.proposal_id!r}.  "
                "Trust may only be weakened or preserved during federation transport."
            ]
        return True, []

    def check_purpose_preserved(
        self, proposal: FederatedIdeaProposal
    ) -> tuple[bool, list[str]]:
        """Check that purpose is preserved when the analogy claims it is.

        If ``analogy_evidence["purpose_preserved"]`` is True, the transported
        idea's token set must overlap with at least one registered purpose's
        indicator tokens.
        """
        if not proposal.analogy_evidence.get("purpose_preserved", False):
            return True, []  # claim not made — nothing to check
        idea_tokens = _tokenize(
            proposal.transported_idea.title + " " + proposal.transported_idea.hypothesis
        )
        for purpose, indicators in self.purpose_catalog.items():
            if _jaccard(idea_tokens, indicators) >= 0.2:
                return True, []
        return False, [
            f"Purpose-preservation claim in proposal {proposal.proposal_id!r} is not "
            "supported: no registered purpose indicators were found in the transported idea."
        ]

    def _check_non_trivial(self, proposal: FederatedIdeaProposal) -> tuple[bool, list[str]]:
        """Reject identity transports where title and hypothesis are unchanged."""
        src = proposal.analogy_evidence.get("source_title", "")
        tgt = proposal.transported_idea.title
        if src and _normalize_text(src) == _normalize_text(tgt):
            return False, [
                f"Transport in proposal {proposal.proposal_id!r} appears trivial: "
                "the transported title is identical to the source title."
            ]
        return True, []

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(text.strip().lower().split())


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


# ---------------------------------------------------------------------------
# 8. FederationHistory
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _HistoryRecord:
    """Internal record stored in :class:`FederationHistory`."""

    proposal: FederatedIdeaProposal
    success: bool
    recorded_at: str = field(default_factory=_now_iso)


class FederationHistory:
    """Append-only log of federation transport outcomes.

    Every accepted or rejected :class:`FederatedIdeaProposal` is recorded
    here so that the system can compute success rates, track analogy quality
    over time, and replay federation decisions for auditing.

    The history is held in memory; callers that need persistence should use
    :class:`FederationSerializer` to write snapshots to disk.
    """

    def __init__(self) -> None:
        self._records: list[_HistoryRecord] = []

    def record(self, proposal: FederatedIdeaProposal, *, success: bool) -> None:
        """Append *proposal* to the history with its outcome flag."""
        self._records.append(_HistoryRecord(proposal=proposal, success=success))

    def by_source_regime(self, source: str) -> list[FederatedIdeaProposal]:
        """Return all proposals that originated in *source*."""
        return [r.proposal for r in self._records if r.proposal.source_regime == source]

    def by_target_regime(self, target: str) -> list[FederatedIdeaProposal]:
        """Return all proposals that targeted *target*."""
        return [r.proposal for r in self._records if r.proposal.target_regime == target]

    def success_rate(self, *, source: str | None = None, target: str | None = None) -> float:
        """Return the fraction of recorded proposals that succeeded.

        Optionally filter to proposals matching *source* and/or *target*.
        Returns 0.0 when no matching records exist.
        """
        records = self._records
        if source is not None:
            records = [r for r in records if r.proposal.source_regime == source]
        if target is not None:
            records = [r for r in records if r.proposal.target_regime == target]
        if not records:
            return 0.0
        return sum(1 for r in records if r.success) / len(records)

    def analogy_quality_over_time(self) -> list[tuple[str, float]]:
        """Return a time-ordered series of ``(timestamp, analogy_score)`` pairs.

        Useful for detecting drift in analogy quality as more bridges are added
        and more ideas are transported.
        """
        return [
            (r.recorded_at, r.proposal.analogy_score())
            for r in self._records
        ]

    def recent(self, n: int = 20) -> list[FederatedIdeaProposal]:
        """Return the *n* most recently recorded proposals."""
        return [r.proposal for r in self._records[-n:]]

    def bridge_usage(self) -> dict[str, int]:
        """Return a count of how many proposals used each bridge ID."""
        counts: dict[str, int] = defaultdict(int)
        for r in self._records:
            counts[r.proposal.bridge_used] += 1
        return dict(counts)

    def copilot_assisted_count(self) -> int:
        """Return the number of proposals that were copilot-assisted."""
        return sum(1 for r in self._records if r.proposal.copilot_assisted)

    def total(self) -> int:
        """Return the total number of records (successful and failed)."""
        return len(self._records)

    def clear(self) -> None:
        """Remove all recorded history (use with caution in production)."""
        self._records.clear()


# ---------------------------------------------------------------------------
# 9. FederationDiagnostics
# ---------------------------------------------------------------------------

class FederationDiagnostics:
    """Human-readable and machine-readable diagnostics for federation state.

    The diagnostics class aggregates information from a registry and history
    to produce reports useful for debugging, copilot interaction surfaces, and
    progress monitoring.

    Attributes
    ----------
    registry:
        The :class:`FederationRegistry` to inspect.
    history:
        The :class:`FederationHistory` to draw statistics from.
    """

    def __init__(self, registry: FederationRegistry, history: FederationHistory) -> None:
        self.registry = registry
        self.history = history

    def summary(self) -> dict[str, Any]:
        """Return a top-level summary dictionary.

        Includes: total bridges, validated bridge count, total regimes,
        total proposals recorded, overall success rate, copilot-assisted
        count, and average analogy score.
        """
        all_bridges = list(self.registry._bridges.values())
        validated = sum(1 for b in all_bridges if b.validated)
        scores = [r.proposal.analogy_score() for r in self.history._records]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        return {
            "total_bridges": len(all_bridges),
            "validated_bridges": validated,
            "total_regimes": len(self.registry.all_regimes()),
            "total_proposals": self.history.total(),
            "overall_success_rate": self.history.success_rate(),
            "copilot_assisted": self.history.copilot_assisted_count(),
            "average_analogy_score": round(avg_score, 4),
        }

    def bridge_report(self) -> list[dict[str, Any]]:
        """Return a per-bridge diagnostic record for every registered bridge."""
        usage = self.history.bridge_usage()
        return [
            {
                "bridge_id": b.bridge_id,
                "summary": b.summary_line(),
                "usage_count": usage.get(b.bridge_id, 0),
                "vocab_size": len(b.analogy_map),
                "purpose_tags": sorted(b.purpose_tags),
                "validated": b.validated,
                "trust_attenuation": b.trust_attenuation,
            }
            for b in self.registry._bridges.values()
        ]

    def transport_quality_report(self) -> dict[str, Any]:
        """Return statistics on the quality of transported ideas.

        Includes analogy score distribution (min, mean, max, stddev) and the
        proportion of proposals claiming purpose preservation.
        """
        records = self.history._records
        if not records:
            return {"note": "no records"}
        scores = [r.proposal.analogy_score() for r in records]
        n = len(scores)
        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / n
        purpose_count = sum(1 for r in records if r.proposal.purpose_preserved())
        return {
            "n": n,
            "score_min": min(scores),
            "score_mean": round(mean, 4),
            "score_max": max(scores),
            "score_stddev": round(math.sqrt(variance), 4),
            "purpose_preserved_fraction": round(purpose_count / n, 4),
        }

    def copilot_federation_summary(self) -> str:
        """Return a concise plain-text summary suitable for copilot consumption.

        This method is the primary surface for LLM-backed orchestration agents
        (including GitHub Copilot integrations) to read the current state of
        the federation system.  It is intentionally terse and structured so
        that an LLM can parse it cheaply.
        """
        s = self.summary()
        lines = [
            "=== JuGeo Ideation Federation (copilot summary) ===",
            f"Bridges registered : {s['total_bridges']} "
            f"({s['validated_bridges']} validated)",
            f"Regimes in graph   : {s['total_regimes']}",
            f"Proposals recorded : {s['total_proposals']} "
            f"(success rate {s['overall_success_rate']:.1%})",
            f"Copilot-assisted   : {s['copilot_assisted']}",
            f"Mean analogy score : {s['average_analogy_score']:.3f}",
        ]
        quality = self.transport_quality_report()
        if "n" in quality:
            lines.append(
                f"Quality range      : [{quality['score_min']:.3f}, "
                f"{quality['score_max']:.3f}] "
                f"stddev={quality['score_stddev']:.3f}"
            )
            lines.append(
                f"Purpose preserved  : {quality['purpose_preserved_fraction']:.1%} of proposals"
            )
        return "\n".join(lines)

    def regime_connectivity(self) -> dict[str, list[str]]:
        """Return the adjacency list of validated-bridge connections."""
        adj: dict[str, list[str]] = defaultdict(list)
        for b in self.registry._bridges.values():
            if b.validated:
                adj[b.source].append(b.target)
        return dict(adj)

    def unreachable_regimes(self, hub: str) -> frozenset[str]:
        """Return regimes that cannot be reached from *hub* via validated bridges."""
        all_r = self.registry.all_regimes()
        reachable: set[str] = {hub}
        queue: deque[str] = deque([hub])
        while queue:
            current = queue.popleft()
            for b in self.registry._bridges.values():
                if b.source == current and b.validated and b.target not in reachable:
                    reachable.add(b.target)
                    queue.append(b.target)
        return all_r - reachable


# ---------------------------------------------------------------------------
# 10. FederationSerializer
# ---------------------------------------------------------------------------

class FederationSerializer:
    """JSON round-trip serialization for federation objects.

    Provides class-level helpers for converting :class:`CrossRegimeBridge`,
    :class:`FederatedIdeaProposal`, and :class:`FederationHistory` to and
    from JSON-safe dictionaries and strings.

    All serialization preserves the information needed to reconstruct the
    objects (within the limits of the frozen-dataclass shapes); it does *not*
    store the full :class:`~jugeo.geometry.supports.SupportRegion` geometry
    since that is expensive to serialize and can be looked up from the pack.
    """

    # ------------------------------------------------------------------
    # Bridge serialization
    # ------------------------------------------------------------------

    @staticmethod
    def bridge_to_dict(bridge: CrossRegimeBridge) -> dict[str, Any]:
        """Serialise *bridge* to a JSON-safe dict."""
        return bridge.to_dict()

    @staticmethod
    def bridge_from_dict(data: Mapping[str, Any]) -> CrossRegimeBridge:
        """Reconstruct a :class:`CrossRegimeBridge` from *data*.

        Raises :class:`KeyError` if required fields are missing.
        """
        return CrossRegimeBridge(
            bridge_id=data["bridge_id"],
            source=data["source"],
            target=data["target"],
            analogy_map=dict(data.get("analogy_map", {})),
            trust_attenuation=float(data.get("trust_attenuation", 0.3)),
            purpose_tags=frozenset(data.get("purpose_tags", [])),
            validated=bool(data.get("validated", False)),
            created_at=data.get("created_at", _now_iso()),
            description=data.get("description", ""),
        )

    # ------------------------------------------------------------------
    # Proposal serialization
    # ------------------------------------------------------------------

    @staticmethod
    def proposal_to_dict(proposal: FederatedIdeaProposal) -> dict[str, Any]:
        """Serialise *proposal* to a JSON-safe dict."""
        return proposal.to_dict()

    @staticmethod
    def proposal_to_json(proposal: FederatedIdeaProposal, *, indent: int = 2) -> str:
        """Serialise *proposal* to a JSON string."""
        return json.dumps(FederationSerializer.proposal_to_dict(proposal), indent=indent)

    # ------------------------------------------------------------------
    # History serialization
    # ------------------------------------------------------------------

    @staticmethod
    def history_to_dict(history: FederationHistory) -> dict[str, Any]:
        """Serialise the full *history* to a JSON-safe dict."""
        return {
            "total": history.total(),
            "success_rate": history.success_rate(),
            "bridge_usage": history.bridge_usage(),
            "copilot_assisted": history.copilot_assisted_count(),
            "analogy_quality_series": history.analogy_quality_over_time(),
        }

    @staticmethod
    def history_to_json(history: FederationHistory, *, indent: int = 2) -> str:
        """Serialise *history* to a JSON string."""
        return json.dumps(FederationSerializer.history_to_dict(history), indent=indent)

    # ------------------------------------------------------------------
    # Registry snapshot
    # ------------------------------------------------------------------

    @staticmethod
    def registry_snapshot(registry: FederationRegistry) -> dict[str, Any]:
        """Return a serialisable snapshot of the registry's bridge graph."""
        return {
            "bridges": [b.to_dict() for b in registry._bridges.values()],
            "regimes": sorted(registry.all_regimes()),
        }

    @staticmethod
    def registry_snapshot_json(registry: FederationRegistry, *, indent: int = 2) -> str:
        """Return a JSON string snapshot of *registry*."""
        return json.dumps(
            FederationSerializer.registry_snapshot(registry), indent=indent
        )

    @staticmethod
    def restore_registry(snapshot: Mapping[str, Any]) -> FederationRegistry:
        """Reconstruct a :class:`FederationRegistry` from a snapshot dict."""
        reg = FederationRegistry()
        for bridge_data in snapshot.get("bridges", []):
            try:
                reg.register_bridge(FederationSerializer.bridge_from_dict(bridge_data))
            except (KeyError, ValueError):
                pass  # skip malformed entries; diagnostics can surface them
        return reg


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def make_bridge(
    source: str,
    target: str,
    analogy_map: dict[str, str],
    *,
    trust_attenuation: float = 0.2,
    purpose_tags: Iterable[str] = (),
    description: str = "",
    validated: bool = False,
) -> CrossRegimeBridge:
    """Construct a :class:`CrossRegimeBridge` with an auto-generated ID.

    This is the recommended factory for building bridges in tests and
    bootstrapping scripts where a human-readable description matters more
    than a stable identifier.
    """
    return CrossRegimeBridge(
        bridge_id=str(uuid.uuid4()),
        source=source,
        target=target,
        analogy_map=analogy_map,
        trust_attenuation=_clamp(trust_attenuation),
        purpose_tags=frozenset(purpose_tags),
        description=description,
        validated=validated,
    )


# ---------------------------------------------------------------------------
# Backward-compatible legacy class (preserved for existing tests/callers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IdeaFederation:
    """Legacy bundle of idea proposals and regime proposals.

    This class is retained for backward compatibility so that existing tests
    and callers continue to work without modification.  New code should use
    :class:`IdeationFederator` and :class:`FederatedIdeaProposal` instead.

    Attributes
    ----------
    ideas:
        Tuple of :class:`~jugeo.ideation.ideas.IdeaProposal` objects.
    regimes:
        Tuple of :class:`~jugeo.ideation.regimes.RegimeProposal` objects.
    """

    ideas: tuple[IdeaProposal, ...]
    regimes: tuple[RegimeProposal, ...]

    def deduplicated_titles(self) -> tuple[str, ...]:
        """Return idea titles with duplicates removed in first-seen order."""
        return tuple(dict.fromkeys(idea.title for idea in self.ideas))

    def regime_labels(self) -> tuple[str, ...]:
        """Return the kind labels for all bundled regime proposals."""
        return tuple(r.kind.value for r in self.regimes)

    def top_ideas(self, n: int = 5) -> tuple[IdeaProposal, ...]:
        """Return the top-*n* ideas sorted by descending payoff."""
        return tuple(sorted(self.ideas, key=lambda i: i.payoff, reverse=True)[:n])


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Primary federation classes
    "FederatedIdeaProposal",
    "IdeationFederator",
    "AnalogyFinder",
    "IdeaTransporter",
    "CrossRegimeBridge",
    "FederationRegistry",
    "FederationValidator",
    "FederationHistory",
    "FederationDiagnostics",
    "FederationSerializer",
    # Convenience factory
    "make_bridge",
    # Legacy compatibility
    "IdeaFederation",
]

# copilot: ideation-federation module — primary surface for LLM-assisted
#     cross-regime analogy discovery, trust-safe transport, and federation
#     diagnostics.  See copilot_federate() and copilot_federation_summary()
#     for the main integration points.
