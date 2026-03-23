"""Integration of kind discovery with other jugeo packages (theory2.tex Ch 56).

Bridges the kind-discovery pipeline with trust algebra, ideation, federation,
and novelty scoring so that discovered kinds are first-class members of the
broader jugeo ecosystem.

Module layout::

    TrustAwareDiscovery        – integrates TrustLevel from jugeo.evidence.trust
    IdeaKindLinker             – links discovered kinds with Idea objects
    FederationKindBridge       – bridges kind discovery with federation
    NoveltyKindScorer          – scores kinds by novelty metrics
    IntegratedDiscoveryPipeline – end-to-end integrated pipeline
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

from jugeo.ideation.kind_discovery.models import (
    KindCandidate, ObstructionField, KindPattern,
    KindBootstrapPlan, NewKind, KindStatus, ObstructionType,
)

# ---------------------------------------------------------------------------
# Optional heavy imports – each is guarded so the module remains importable
# even when sibling packages have not yet been installed or built.
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.kind_discovery.algorithms import (
        KindDiscoveryEngine, KindValidator, KindRanker, DiscoveryAlgorithm,
    )
except ImportError:
    KindDiscoveryEngine = None   # type: ignore[assignment,misc]
    KindValidator = None         # type: ignore[assignment,misc]
    KindRanker = None            # type: ignore[assignment,misc]
    DiscoveryAlgorithm = None    # type: ignore[assignment,misc]

try:
    from jugeo.ideation.ideas import (
        Idea, IdeaPortfolio, GainProfile, ValidationPath, TrustStatus,
        IdeaGenerator, IdeaEvaluator, IdeaRefiner, IdeaHistory, IdeaDiagnostics,
    )
except ImportError:
    Idea = None                  # type: ignore[assignment,misc]
    IdeaPortfolio = None         # type: ignore[assignment,misc]
    GainProfile = None           # type: ignore[assignment,misc]
    ValidationPath = None        # type: ignore[assignment,misc]
    TrustStatus = None           # type: ignore[assignment,misc]
    IdeaGenerator = None         # type: ignore[assignment,misc]
    IdeaEvaluator = None         # type: ignore[assignment,misc]
    IdeaRefiner = None           # type: ignore[assignment,misc]
    IdeaHistory = None           # type: ignore[assignment,misc]
    IdeaDiagnostics = None       # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import (
        TrustLevel, TrustAlgebra, TrustPolicy, TrustAuditEntry, TrustAuditLog,
    )
except ImportError:
    TrustLevel = None            # type: ignore[assignment,misc]
    TrustAlgebra = None          # type: ignore[assignment,misc]
    TrustPolicy = None           # type: ignore[assignment,misc]
    TrustAuditEntry = None       # type: ignore[assignment,misc]
    TrustAuditLog = None         # type: ignore[assignment,misc]

try:
    from jugeo.ideation.federation import (
        CrossRegimeBridge, AnalogyFinder, IdeationFederator, FederationRegistry,
    )
except ImportError:
    CrossRegimeBridge = None     # type: ignore[assignment,misc]
    AnalogyFinder = None         # type: ignore[assignment,misc]
    IdeationFederator = None     # type: ignore[assignment,misc]
    FederationRegistry = None    # type: ignore[assignment,misc]

try:
    from jugeo.ideation.novelty import (
        NoveltyScore, NoveltyMetric, TheoremPortfolio, NoveltySearcher,
    )
except ImportError:
    NoveltyScore = None          # type: ignore[assignment,misc]
    NoveltyMetric = None         # type: ignore[assignment,misc]
    TheoremPortfolio = None      # type: ignore[assignment,misc]
    NoveltySearcher = None       # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, float(v)))


def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on whitespace/punctuation, and drop tokens shorter than 3 chars.

    Produces a set of cleaned tokens suitable for Jaccard similarity or
    frequency-bucket hashing.  Punctuation is replaced with spaces before
    splitting so compound identifiers like ``quasi-coherent`` yield the
    individual words ``quasi`` and ``coherent``.
    """
    if not text:
        return set()
    # Replace common punctuation / separators with spaces.
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return {tok for tok in normalized.split() if len(tok) >= 3}


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Compute Jaccard similarity between two token collections.

    Returns a value in [0, 1].  Empty inputs yield 0.0 rather than NaN.
    The denominator is the size of the *union*, so identical sets give 1.0
    and disjoint sets give 0.0.
    """
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _trust_to_confidence(trust_level: Any) -> float:
    """Map a TrustLevel enum value (or string) to a float confidence in [0, 1].

    Handles the case where TrustLevel is None (i.e. the trust package could
    not be imported) by inspecting the string representation of the value.
    Unknown / None inputs default to a conservative 0.1.
    """
    if trust_level is None:
        return 0.1
    # When TrustLevel enum is available, compare directly.
    name = getattr(trust_level, "name", str(trust_level)).upper()
    mapping = {
        "UNVERIFIED": 0.1,
        "ORACLE_PROPOSED": 0.45,
        "ORACLE_REVIEWED": 0.70,
        "ORACLE_VERIFIED": 0.90,
        "ESTABLISHED": 1.0,
        # Fallbacks for variant naming conventions.
        "LOW": 0.15,
        "MEDIUM": 0.50,
        "HIGH": 0.80,
        "VERIFIED": 0.90,
        "PROPOSED": 0.45,
        "REVIEWED": 0.70,
    }
    return mapping.get(name, 0.1)


def _kind_to_idea_payload(kind: NewKind) -> dict:
    """Build an idea-creation payload dict from a *NewKind* instance.

    The dict uses the field names expected by the jugeo.ideation.ideas module
    so it can be passed directly to IdeaGenerator or used as a stand-alone
    idea record when that module is unavailable.
    """
    return {
        "idea_id": str(uuid.uuid4()),
        "title": f"Introduce kind: {kind.name}",
        "purpose": (
            f"Establish {kind.name} as a first-class mathematical kind by "
            f"resolving its obstruction fields and constructing canonical examples."
        ),
        "target_area": getattr(kind, "domain", "mathematics"),
        "hypothesis": (
            f"If {kind.name} constitutes a valid kind, then its formal definition "
            f"— {kind.formal_definition[:120].rstrip()} — "
            f"yields at least {kind.example_count} concrete examples and "
            f"{kind.theorem_count} provable theorems."
        ),
        "confidence": kind.confidence,
        "tags": list(kind.tags),
        "source_kind_id": kind.kind_id,
        "created_at": _now_iso(),
    }


def _novelty_hash_vec(kind: NewKind, *, n_buckets: int = 64) -> list[float]:
    """Compute a numeric frequency-bucket vector from a kind's tokens.

    Each dimension corresponds to a hash bucket, and its value is the
    fraction of tokens that hash into that bucket.  The resulting vector
    captures the 'semantic fingerprint' of the kind's name and description
    for approximate cosine-similarity comparisons.

    Parameters
    ----------
    kind:
        The :class:`NewKind` whose textual content is vectorised.
    n_buckets:
        Number of hash buckets (vector dimensionality).  Defaults to 64.
    """
    combined = " ".join([kind.name, kind.formal_definition] + list(kind.tags))
    tokens = _tokenize(combined)
    vec = [0.0] * n_buckets
    if not tokens:
        return vec
    for tok in tokens:
        # Use Python's built-in hash, masked to positive bucket index.
        bucket = hash(tok) % n_buckets
        vec[bucket] += 1.0
    # Normalise by total token count so the vector represents relative density.
    total = sum(vec)
    if total > 0:
        vec = [x / total for x in vec]
    return vec


# ---------------------------------------------------------------------------
# Class 1: TrustAwareDiscovery
# ---------------------------------------------------------------------------

@dataclass
class TrustAwareDiscovery:
    """Integrates TrustLevel assignment into the kind discovery pipeline.

    Uses TrustAlgebra to assign and promote trust levels for discovered kinds
    based on their confidence scores, obstruction evidence, and domain context.
    All trust assignments are recorded in an audit log.

    When the jugeo.evidence.trust package is unavailable the class degrades
    gracefully: trust levels become plain strings and the audit log becomes
    an in-memory list of dicts.
    """

    trust_algebra: Any = None   # TrustAlgebra instance
    trust_policy: Any = None    # TrustPolicy instance
    audit_log: Any = None       # TrustAuditLog instance
    _engine: Any = field(default_factory=lambda: KindDiscoveryEngine() if KindDiscoveryEngine else None)
    _validator: Any = field(default_factory=lambda: KindValidator() if KindValidator else None)
    _ranker: Any = field(default_factory=lambda: KindRanker() if KindRanker else None)
    _decisions: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise trust infrastructure from available classes."""
        if self.trust_algebra is None and TrustAlgebra is not None:
            try:
                self.trust_algebra = TrustAlgebra()
            except Exception:
                self.trust_algebra = None
        if self.audit_log is None and TrustAuditLog is not None:
            try:
                self.audit_log = TrustAuditLog()
            except Exception:
                # Fall back to an in-memory list used as audit log.
                self.audit_log = []

    def discover(
        self,
        texts: list[str],
        *,
        domain: str = "",
        base_trust: Any = None,
    ) -> list[tuple[NewKind, Any]]:
        """Run the full trust-aware discovery pipeline on *texts*.

        Steps
        -----
        1. Use the discovery engine to extract ``NewKind`` instances from *texts*.
        2. Filter results through the validator.
        3. Assign a TrustLevel to each kind via :meth:`bulk_assign_trust`.
        4. Record decisions for auditing.

        Returns
        -------
        list[tuple[NewKind, TrustLevel]]
            Pairs of (discovered kind, assigned trust level).
        """
        # Produce raw kinds – use engine when available, otherwise synthesise
        # minimal NewKind objects directly from the input texts.
        if self._engine is not None and hasattr(self._engine, "discover"):
            try:
                raw_kinds = self._engine.discover(texts, domain=domain)
            except Exception:
                raw_kinds = self._synthesise_kinds_from_texts(texts, domain)
        else:
            raw_kinds = self._synthesise_kinds_from_texts(texts, domain)

        # Validate – remove implausible candidates.
        if self._validator is not None and hasattr(self._validator, "filter_valid"):
            try:
                raw_kinds = self._validator.filter_valid(raw_kinds)
            except Exception:
                # Fallback: keep only high-confidence kinds.
                raw_kinds = [k for k in raw_kinds if k.confidence >= 0.2]

        # Rank if ranker is available.
        if self._ranker is not None and hasattr(self._ranker, "rank"):
            try:
                raw_kinds = self._ranker.rank(raw_kinds)
            except Exception:
                raw_kinds = sorted(raw_kinds, key=lambda k: k.confidence, reverse=True)
        else:
            raw_kinds = sorted(raw_kinds, key=lambda k: k.confidence, reverse=True)

        kinds_with_trust = self.bulk_assign_trust(raw_kinds)
        self.record_trust_decisions(kinds_with_trust)
        return kinds_with_trust

    def _synthesise_kinds_from_texts(self, texts: list[str], domain: str) -> list[NewKind]:
        """Create minimal NewKind objects from raw texts when engine is absent.

        Each non-empty text produces one kind whose name is derived from its
        first few meaningful tokens.  Confidence is estimated from text length
        as a very rough proxy.
        """
        kinds: list[NewKind] = []
        for text in texts:
            text = text.strip()
            if not text:
                continue
            tokens = list(_tokenize(text))[:4]
            name = " ".join(tokens) if tokens else f"kind_{uuid.uuid4().hex[:6]}"
            # Longer texts suggest richer evidence → higher confidence proxy.
            confidence = _clamp(len(text) / 500.0, 0.05, 0.85)
            kind = NewKind(
                kind_id=str(uuid.uuid4()),
                name=name,
                formal_definition=text[:200],
                examples=(),
                theorems=(),
                discovery_path=(f"synthesised from text in domain '{domain}'",),
                confidence=confidence,
                tags=frozenset([domain] if domain else []),
            )
            kinds.append(kind)
        return kinds

    def assign_trust(self, kind: NewKind) -> Any:
        """Compute and return a trust level for *kind*.

        The level is determined by confidence, domain richness, and the number
        of available obstruction fields/tags that corroborate the discovery.
        A higher tag count is treated as a lightweight proxy for obstruction
        evidence (more structural knowledge about the kind).

        Returns a TrustLevel enum value when available, else a string.
        """
        base_trust = self._trust_from_confidence(kind.confidence)

        # Domain penalty: if domain is empty the kind has no contextual anchor.
        has_domain = bool(kind.tags) or bool(kind.discovery_path)
        if not has_domain:
            # Attenuate one level down when no domain context exists.
            base_trust = self._attenuate_trust(base_trust)

        # Obstruction bonus: more tags/theorems → stronger evidence base.
        evidence_count = kind.theorem_count + len(kind.tags) + kind.example_count
        if evidence_count >= 5 and self._can_promote(base_trust):
            base_trust = self._promote_trust(base_trust)

        return base_trust

    def _trust_from_confidence(self, confidence: float) -> Any:
        """Map *confidence* in [0, 1] to a TrustLevel (or string fallback).

        Thresholds:
        - ``< 0.30``  → UNVERIFIED
        - ``0.30–0.60`` → ORACLE_PROPOSED
        - ``0.60–0.80`` → ORACLE_REVIEWED
        - ``>= 0.80``   → ORACLE_VERIFIED
        """
        if TrustLevel is not None:
            try:
                if confidence < 0.30:
                    return TrustLevel.UNVERIFIED
                elif confidence < 0.60:
                    return TrustLevel.ORACLE_PROPOSED
                elif confidence < 0.80:
                    return TrustLevel.ORACLE_REVIEWED
                else:
                    return TrustLevel.ORACLE_VERIFIED
            except AttributeError:
                pass
        # Fallback string representation.
        if confidence < 0.30:
            return "UNVERIFIED"
        elif confidence < 0.60:
            return "ORACLE_PROPOSED"
        elif confidence < 0.80:
            return "ORACLE_REVIEWED"
        else:
            return "ORACLE_VERIFIED"

    def _attenuate_trust(self, trust: Any) -> Any:
        """Return a trust level one step below *trust* (domain-penalty helper)."""
        order = ["UNVERIFIED", "ORACLE_PROPOSED", "ORACLE_REVIEWED", "ORACLE_VERIFIED"]
        name = getattr(trust, "name", str(trust)).upper()
        idx = order.index(name) if name in order else 1
        lowered_name = order[max(0, idx - 1)]
        if TrustLevel is not None:
            try:
                return TrustLevel[lowered_name]
            except (KeyError, AttributeError):
                pass
        return lowered_name

    def _can_promote(self, trust: Any) -> bool:
        """Return True if *trust* is below ORACLE_VERIFIED (can be promoted)."""
        name = getattr(trust, "name", str(trust)).upper()
        return name not in ("ORACLE_VERIFIED", "ESTABLISHED")

    def _promote_trust(self, trust: Any) -> Any:
        """Return a trust level one step above *trust* (evidence-bonus helper)."""
        order = ["UNVERIFIED", "ORACLE_PROPOSED", "ORACLE_REVIEWED", "ORACLE_VERIFIED"]
        name = getattr(trust, "name", str(trust)).upper()
        idx = order.index(name) if name in order else 0
        promoted_name = order[min(len(order) - 1, idx + 1)]
        if TrustLevel is not None:
            try:
                return TrustLevel[promoted_name]
            except (KeyError, AttributeError):
                pass
        return promoted_name

    def promote_kind(
        self,
        kind: NewKind,
        new_trust: Any,
        *,
        explicit: bool = True,
    ) -> tuple[NewKind, Any]:
        """Promote *kind* to *new_trust*, optionally upgrading its :class:`KindStatus`.

        The ``explicit`` flag must be ``True`` to guard against accidental
        promotions in automated pipelines.  When the new trust level is
        ``ORACLE_VERIFIED`` or higher the kind's status is upgraded to
        ``KindStatus.VALIDATED`` (if that is a valid transition).

        Returns
        -------
        tuple[NewKind, Any]
            The (possibly updated) kind and the new trust level.
        """
        if not explicit:
            raise ValueError(
                "promote_kind requires explicit=True to prevent accidental trust promotions."
            )

        trust_name = getattr(new_trust, "name", str(new_trust)).upper()
        updated_kind = kind

        # Upgrade status when trust is sufficiently high.
        if trust_name in ("ORACLE_VERIFIED", "ESTABLISHED"):
            try:
                if kind.status.can_transition_to(KindStatus.VALIDATED):
                    updated_kind = replace(kind, status=KindStatus.VALIDATED)
            except (AttributeError, ValueError):
                # KindStatus.VALIDATED may not exist in all versions.
                pass

        # Record the promotion decision.
        self._decisions.append({
            "kind_id": kind.kind_id,
            "action": "promote",
            "new_trust": trust_name,
            "previous_status": kind.status,
            "new_status": updated_kind.status,
            "timestamp": _now_iso(),
        })

        return updated_kind, new_trust

    def audit_kind_trust(self, kind: NewKind, trust_level: Any) -> Any:
        """Create and record a trust audit entry for *kind* at *trust_level*.

        Returns a ``TrustAuditEntry`` when available, else a plain dict.
        The entry is appended to the audit log if one has been initialised.
        """
        entry_data = {
            "subject_id": kind.kind_id,
            "subject_type": "NewKind",
            "trust_level": getattr(trust_level, "name", str(trust_level)),
            "confidence": kind.confidence,
            "name": kind.name,
            "timestamp": _now_iso(),
            "tags": list(kind.tags),
        }

        entry: Any = entry_data
        if TrustAuditEntry is not None:
            try:
                entry = TrustAuditEntry(**entry_data)
            except Exception:
                entry = entry_data

        # Append to audit log (list or object with .append / .record).
        if self.audit_log is not None:
            if isinstance(self.audit_log, list):
                self.audit_log.append(entry)
            elif hasattr(self.audit_log, "record"):
                try:
                    self.audit_log.record(entry)
                except Exception:
                    pass
            elif hasattr(self.audit_log, "append"):
                try:
                    self.audit_log.append(entry)
                except Exception:
                    pass

        return entry

    def bulk_assign_trust(
        self, kinds: list[NewKind]
    ) -> list[tuple[NewKind, Any]]:
        """Assign trust levels to all kinds in *kinds* and audit each assignment.

        Returns a list of ``(kind, trust_level)`` pairs in the same order as
        the input list.
        """
        results: list[tuple[NewKind, Any]] = []
        for kind in kinds:
            trust = self.assign_trust(kind)
            self.audit_kind_trust(kind, trust)
            results.append((kind, trust))
        return results

    def trust_summary(self, kinds_with_trust: list[tuple[NewKind, Any]]) -> dict:
        """Return a summary dict aggregating trust levels and mean confidence.

        The ``distribution`` sub-dict maps each trust level name to the count
        of kinds assigned that level.  ``mean_confidence`` is computed over all
        kinds regardless of level.
        """
        distribution: dict[str, int] = Counter()
        confidences: list[float] = []
        for kind, trust in kinds_with_trust:
            level_name = getattr(trust, "name", str(trust))
            distribution[level_name] += 1
            confidences.append(kind.confidence)

        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return {
            "total_kinds": len(kinds_with_trust),
            "distribution": dict(distribution),
            "mean_confidence": round(mean_conf, 4),
            "audit_entries": len(self.audit_log) if isinstance(self.audit_log, list) else "n/a",
        }

    def record_trust_decisions(self, kinds_with_trust: list[tuple[NewKind, Any]]) -> None:
        """Append each (kind, trust) assignment to the internal decisions list."""
        for kind, trust in kinds_with_trust:
            self._decisions.append({
                "kind_id": kind.kind_id,
                "kind_name": kind.name,
                "trust_level": getattr(trust, "name", str(trust)),
                "confidence": kind.confidence,
                "timestamp": _now_iso(),
                "action": "assign",
            })


# ---------------------------------------------------------------------------
# Class 2: IdeaKindLinker
# ---------------------------------------------------------------------------

@dataclass
class IdeaKindLinker:
    """Links discovered kinds with Idea objects in the ideation system.

    Generates Idea objects for each NewKind, evaluates them, and maintains
    a mapping between kind IDs and associated ideas.  When the ideas module
    is unavailable, plain dicts with the same structure are used throughout.
    """

    generator: Any = None   # IdeaGenerator
    evaluator: Any = None   # IdeaEvaluator
    refiner: Any = None     # IdeaRefiner
    _kind_idea_map: dict = field(default_factory=dict)
    _portfolio_entries: list = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise generator, evaluator and refiner from available classes."""
        if self.generator is None and IdeaGenerator is not None:
            try:
                self.generator = IdeaGenerator()
            except Exception:
                self.generator = None
        if self.evaluator is None and IdeaEvaluator is not None:
            try:
                self.evaluator = IdeaEvaluator()
            except Exception:
                self.evaluator = None
        if self.refiner is None and IdeaRefiner is not None:
            try:
                self.refiner = IdeaRefiner()
            except Exception:
                self.refiner = None

    def link(self, kind: NewKind) -> list:
        """Generate and evaluate ideas for *kind*, storing them in the internal map.

        Returns a list of Idea objects (or dicts) associated with *kind*.
        """
        ideas = self.generate_ideas_for_kind(kind)

        # Evaluate if evaluator is available.
        if self.evaluator is not None:
            scored = self.evaluate_kind_ideas(kind, ideas)
            # Keep only the idea objects from scored pairs, in ranked order.
            ideas = [idea for idea, _score in scored]

        self._kind_idea_map[kind.kind_id] = ideas
        return ideas

    def link_batch(self, kinds: list[NewKind]) -> dict[str, list]:
        """Link all kinds in *kinds* and return a kind_id → ideas mapping."""
        mapping: dict[str, list] = {}
        for kind in kinds:
            mapping[kind.kind_id] = self.link(kind)
        return mapping

    def generate_ideas_for_kind(self, kind: NewKind) -> list:
        """Build 2–3 Idea-like objects derived from *kind*'s attributes.

        Idea 1 – Core formalisation idea: motivates developing the formal
                 definition further.
        Idea 2 – Example-construction idea: motivates finding more examples.
        Idea 3 (optional) – Theorem-development idea: only created when the
                 kind already has at least one associated theorem to extend.

        When the Idea class is not available, plain dicts with the same keys
        are returned instead.
        """
        payload_base = _kind_to_idea_payload(kind)
        gain_profile = self._kind_to_gain_profile(kind)
        validation_path = self._kind_to_validation_path(kind)
        hypothesis = self._kind_hypothesis(kind)

        ideas = []

        # --- Idea 1: Formal definition development ---
        idea1_payload = {
            **payload_base,
            "idea_id": str(uuid.uuid4()),
            "title": f"Formalise the definition of {kind.name}",
            "purpose": (
                f"Develop a rigorous formal definition for {kind.name} that "
                f"distinguishes it from related kinds and supports theorem proving."
            ),
            "hypothesis": hypothesis,
            "gain_profile": gain_profile,
            "validation_path": validation_path,
            "priority": "high" if kind.confidence >= 0.6 else "medium",
        }
        if Idea is not None:
            try:
                ideas.append(Idea(**{k: v for k, v in idea1_payload.items()
                                     if k in Idea.__dataclass_fields__}))
            except Exception:
                ideas.append(idea1_payload)
        else:
            ideas.append(idea1_payload)

        # --- Idea 2: Example construction ---
        example_hypothesis = (
            f"Constructing {max(3, kind.example_count + 2)} examples of {kind.name} "
            f"will validate the formal definition and reveal edge-case behaviour."
        )
        idea2_payload = {
            **payload_base,
            "idea_id": str(uuid.uuid4()),
            "title": f"Construct canonical examples for {kind.name}",
            "purpose": (
                f"Build a library of concrete examples of {kind.name} covering "
                f"degenerate, minimal, and maximal instances."
            ),
            "hypothesis": example_hypothesis,
            "gain_profile": gain_profile,
            "validation_path": validation_path,
            "priority": "medium",
        }
        if Idea is not None:
            try:
                ideas.append(Idea(**{k: v for k, v in idea2_payload.items()
                                     if k in Idea.__dataclass_fields__}))
            except Exception:
                ideas.append(idea2_payload)
        else:
            ideas.append(idea2_payload)

        # --- Idea 3: Theorem development (only when seeds exist) ---
        if kind.theorem_count > 0 or kind.confidence >= 0.5:
            thm_hypothesis = (
                f"The structural properties of {kind.name} imply at least one "
                f"non-trivial theorem that generalises {kind.theorem_count} "
                f"known result(s)."
            )
            idea3_payload = {
                **payload_base,
                "idea_id": str(uuid.uuid4()),
                "title": f"Develop theorems for {kind.name}",
                "purpose": (
                    f"Identify and prove theorems that characterise {kind.name}, "
                    f"establishing its place in the broader mathematical landscape."
                ),
                "hypothesis": thm_hypothesis,
                "gain_profile": gain_profile,
                "validation_path": validation_path,
                "priority": "low" if kind.confidence < 0.5 else "medium",
            }
            if Idea is not None:
                try:
                    ideas.append(Idea(**{k: v for k, v in idea3_payload.items()
                                         if k in Idea.__dataclass_fields__}))
                except Exception:
                    ideas.append(idea3_payload)
            else:
                ideas.append(idea3_payload)

        return ideas

    def evaluate_kind_ideas(self, kind: NewKind, ideas: list) -> list[tuple[Any, float]]:
        """Score and rank ideas for *kind*, returning (idea, score) pairs.

        Uses the injected evaluator when available; otherwise computes a
        heuristic score from the idea dict's ``confidence`` and ``priority``
        fields.  Results are sorted by score in descending order.
        """
        scored: list[tuple[Any, float]] = []
        for idea in ideas:
            score: float
            if self.evaluator is not None and hasattr(self.evaluator, "evaluate"):
                try:
                    score = float(self.evaluator.evaluate(idea))
                except Exception:
                    score = self._heuristic_idea_score(idea, kind)
            else:
                score = self._heuristic_idea_score(idea, kind)
            scored.append((idea, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def _heuristic_idea_score(self, idea: Any, kind: NewKind) -> float:
        """Compute a heuristic quality score for *idea* derived from *kind*.

        The score blends:
        - ``kind.confidence`` (primary quality signal)
        - A priority weight (high=1.0, medium=0.7, low=0.4)
        - A slight bonus for ideas that mention theorem development (higher ROI)
        """
        priority_weight = {"high": 1.0, "medium": 0.7, "low": 0.4}
        priority = "medium"
        if isinstance(idea, dict):
            priority = idea.get("priority", "medium")
            title = idea.get("title", "")
        else:
            priority = getattr(idea, "priority", "medium")
            title = getattr(idea, "title", "")

        weight = priority_weight.get(str(priority).lower(), 0.7)
        theorem_bonus = 0.1 if "theorem" in str(title).lower() else 0.0
        return _clamp(kind.confidence * weight + theorem_bonus)

    def add_to_portfolio(self, kind: NewKind, portfolio: Any) -> int:
        """Add all ideas linked to *kind* to *portfolio*.

        Returns the number of ideas successfully added.
        """
        ideas = self._kind_idea_map.get(kind.kind_id, [])
        added = 0
        for idea in ideas:
            try:
                if hasattr(portfolio, "add"):
                    portfolio.add(idea)
                    added += 1
                elif isinstance(portfolio, list):
                    portfolio.append(idea)
                    added += 1
                self._portfolio_entries.append(idea)
            except Exception:
                continue
        return added

    def _kind_to_gain_profile(self, kind: NewKind) -> Any:
        """Build a GainProfile (or dict) representing the expected yield from *kind*."""
        theorem_yield = max(1, kind.theorem_count + int(kind.confidence * 5))
        profile_data = {
            "theorem_yield": theorem_yield,
            "example_yield": max(1, kind.example_count + 2),
            "confidence": kind.confidence,
            "estimated_effort_days": max(1, int((1.0 - kind.confidence) * 30)),
        }
        if GainProfile is not None:
            try:
                return GainProfile(**{k: v for k, v in profile_data.items()
                                      if k in GainProfile.__dataclass_fields__})
            except Exception:
                pass
        return profile_data

    def _kind_to_validation_path(self, kind: NewKind) -> Any:
        """Build a ValidationPath (or dict) from *kind*'s discovery path."""
        steps = list(kind.discovery_path)
        if not steps:
            steps = [
                f"Define {kind.name} formally",
                f"Construct at least 2 examples",
                f"Prove at least 1 theorem",
                f"Establish relationship to adjacent kinds",
            ]
        path_data = {
            "steps": steps,
            "kind_id": kind.kind_id,
            "confidence_target": _clamp(kind.confidence + 0.15),
        }
        if ValidationPath is not None:
            try:
                return ValidationPath(**{k: v for k, v in path_data.items()
                                         if k in ValidationPath.__dataclass_fields__})
            except Exception:
                pass
        return path_data

    def _kind_hypothesis(self, kind: NewKind) -> str:
        """Generate a hypothesis string from *kind*'s structural data."""
        obstruction_count = len(kind.tags)  # tags serve as obstruction proxies here
        domain_clause = (
            f"in the domain of {next(iter(kind.tags))}"
            if kind.tags else "across the relevant domain"
        )
        return (
            f"If {kind.name} constitutes a valid kind {domain_clause}, "
            f"then {obstruction_count} obstruction field(s) can be resolved, "
            f"yielding confidence {kind.confidence:.2f} and at least "
            f"{max(1, kind.example_count)} concrete example(s)."
        )

    def cross_link(self, kinds: list[NewKind]) -> Any:
        """Link all *kinds* and assemble them into an IdeaPortfolio (or dict).

        All generated ideas are collected into the portfolio; the portfolio
        itself is returned so callers can perform further operations on it.
        """
        idea_map = self.link_batch(kinds)
        all_ideas = [idea for ideas in idea_map.values() for idea in ideas]

        if IdeaPortfolio is not None:
            try:
                portfolio = IdeaPortfolio()
                for idea in all_ideas:
                    try:
                        portfolio.add(idea)
                    except Exception:
                        pass
                return portfolio
            except Exception:
                pass

        # Fallback: return a structured dict.
        return {
            "portfolio_type": "kind_ideas",
            "kind_count": len(kinds),
            "idea_count": len(all_ideas),
            "ideas": all_ideas,
            "created_at": _now_iso(),
        }

    def kind_portfolio_summary(self, portfolio: Any) -> str:
        """Return a human-readable summary of how many ideas and kinds are linked."""
        linked_kinds = len(self._kind_idea_map)
        total_ideas = sum(len(v) for v in self._kind_idea_map.values())

        portfolio_size: Any = "unknown"
        if isinstance(portfolio, dict):
            portfolio_size = portfolio.get("idea_count", total_ideas)
        elif hasattr(portfolio, "__len__"):
            try:
                portfolio_size = len(portfolio)
            except Exception:
                pass

        return (
            f"IdeaKindLinker: {linked_kinds} kind(s) linked → "
            f"{total_ideas} idea(s) generated, "
            f"{portfolio_size} idea(s) in portfolio."
        )


# ---------------------------------------------------------------------------
# Class 3: FederationKindBridge
# ---------------------------------------------------------------------------

@dataclass
class FederationKindBridge:
    """Bridges kind discovery with the ideation federation system.

    Enables discovered kinds to cross regime boundaries through analogy
    transport, finding analogous kinds in other regimes and federating
    discoveries across the ideation network.

    When the federation module is unavailable, the bridge produces heuristic
    analogues and records operations in an internal transport log.
    """

    registry: Any = None        # FederationRegistry
    federator: Any = None       # IdeationFederator
    _bridge_cache: dict = field(default_factory=dict)
    _transport_log: list = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise registry and federator from available classes."""
        if self.registry is None and FederationRegistry is not None:
            try:
                self.registry = FederationRegistry()
            except Exception:
                self.registry = None
        if self.federator is None and IdeationFederator is not None:
            try:
                self.federator = IdeationFederator()
            except Exception:
                self.federator = None

    def bridge(
        self,
        kind: NewKind,
        *,
        source_regime: str = "kind_discovery",
        target_regime: str = "general",
    ) -> list[NewKind]:
        """Transport *kind* from *source_regime* to *target_regime*.

        Steps
        -----
        1. Create a regime bridge object for *kind*.
        2. Transport the kind across regime boundaries.
        3. Log the operation.

        Returns
        -------
        list[NewKind]
            New kind variants adapted for the target regime.
        """
        bridge_obj = self.create_bridge_for_kind(kind, target_regime)
        transported = self.transport_kind_as_idea(kind, bridge_obj)

        self._transport_log.append({
            "kind_id": kind.kind_id,
            "kind_name": kind.name,
            "source_regime": source_regime,
            "target_regime": target_regime,
            "transported_count": len(transported),
            "timestamp": _now_iso(),
        })

        return transported

    def create_bridge_for_kind(self, kind: NewKind, target_regime: str) -> Any:
        """Create and cache a CrossRegimeBridge for *kind* targeting *target_regime*.

        The cache key is ``"{kind_id}:{target_regime}"``.  If a bridge already
        exists for this pair it is returned from cache without reconstruction.
        """
        cache_key = f"{kind.kind_id}:{target_regime}"
        if cache_key in self._bridge_cache:
            return self._bridge_cache[cache_key]

        bridge_data = {
            "source_kind_id": kind.kind_id,
            "source_kind_name": kind.name,
            "target_regime": target_regime,
            "confidence": kind.confidence,
            "created_at": _now_iso(),
        }

        bridge_obj: Any = bridge_data
        if CrossRegimeBridge is not None:
            try:
                bridge_obj = CrossRegimeBridge(**{
                    k: v for k, v in bridge_data.items()
                    if k in CrossRegimeBridge.__dataclass_fields__
                })
            except Exception:
                bridge_obj = bridge_data

        self._bridge_cache[cache_key] = bridge_obj
        return bridge_obj

    def transport_kind_as_idea(self, kind: NewKind, bridge: Any) -> list[NewKind]:
        """Return 1–2 variant NewKind objects adapted for the target regime.

        Trust attenuation of 0.10 is applied to confidence on crossing a
        regime boundary to reflect the additional uncertainty of the transport.
        Tags are augmented with the target regime name so downstream components
        can identify the origin of transported kinds.
        """
        target_regime = (
            bridge.get("target_regime", "general")
            if isinstance(bridge, dict)
            else getattr(bridge, "target_regime", "general")
        )

        attenuated_confidence = _clamp(kind.confidence - 0.10)
        transported: list[NewKind] = []

        # Variant 1: direct analogue in target regime.
        variant1 = replace(
            kind,
            kind_id=str(uuid.uuid4()),
            name=f"{kind.name} [{target_regime}]",
            confidence=attenuated_confidence,
            tags=kind.tags | frozenset([target_regime, "transported"]),
            discovery_path=kind.discovery_path + (f"transported to regime: {target_regime}",),
        )
        transported.append(variant1)

        # Variant 2: generalised analogue (only if confidence is high enough).
        if kind.confidence >= 0.5:
            generalised_name = f"generalised {kind.name}"
            variant2 = replace(
                kind,
                kind_id=str(uuid.uuid4()),
                name=generalised_name,
                formal_definition=(
                    f"Generalisation of '{kind.formal_definition[:100]}' "
                    f"in regime '{target_regime}'."
                ),
                confidence=_clamp(attenuated_confidence - 0.05),
                tags=kind.tags | frozenset([target_regime, "generalised", "transported"]),
                discovery_path=kind.discovery_path + (
                    f"generalised during transport to regime: {target_regime}",
                ),
            )
            transported.append(variant2)

        return transported

    def find_analogous_kinds(self, kind: NewKind, regime: str) -> list[NewKind]:
        """Find kinds analogous to *kind* within *regime*.

        Uses AnalogyFinder when available; otherwise creates a heuristic
        analogue by flipping domain tags and reducing confidence slightly.
        """
        analogues: list[NewKind] = []

        if AnalogyFinder is not None:
            try:
                finder = AnalogyFinder()
                raw_analogues = finder.find(kind, regime=regime)
                for raw in raw_analogues:
                    if isinstance(raw, NewKind):
                        analogues.append(raw)
            except Exception:
                pass

        # Always produce at least one heuristic analogue.
        heuristic = replace(
            kind,
            kind_id=str(uuid.uuid4()),
            name=f"analogue of {kind.name} in {regime}",
            formal_definition=(
                f"Analogue of '{kind.name}' transposed into regime '{regime}': "
                f"{kind.formal_definition[:120]}"
            ),
            confidence=_clamp(kind.confidence - 0.15),
            tags=kind.tags | frozenset([regime, "analogue"]),
            discovery_path=kind.discovery_path + (f"analogised into regime: {regime}",),
        )
        analogues.append(heuristic)
        return analogues

    def federation_summary(self, kinds: list[NewKind]) -> dict:
        """Return aggregate statistics for federation operations on *kinds*."""
        bridges_created = sum(
            1 for key in self._bridge_cache
            if any(key.startswith(k.kind_id) for k in kinds)
        )
        transports_done = sum(
            1 for entry in self._transport_log
            if entry["kind_id"] in {k.kind_id for k in kinds}
        )
        return {
            "total_kinds": len(kinds),
            "bridges_created": bridges_created,
            "transports_done": transports_done,
            "bridge_cache_size": len(self._bridge_cache),
            "transport_log_entries": len(self._transport_log),
        }

    def _kind_to_proposal(self, kind: NewKind) -> Any:
        """Convert *kind* to a federated idea proposal dict (or object)."""
        proposal_data = {
            "proposal_id": str(uuid.uuid4()),
            "kind_id": kind.kind_id,
            "name": kind.name,
            "formal_definition": kind.formal_definition,
            "confidence": kind.confidence,
            "tags": list(kind.tags),
            "proposed_at": _now_iso(),
        }
        # If a FederatedIdeaProposal class exists, try to use it.
        FederatedIdeaProposal = None
        try:
            from jugeo.ideation.federation import FederatedIdeaProposal  # type: ignore[no-redef]
        except ImportError:
            pass
        if FederatedIdeaProposal is not None:
            try:
                return FederatedIdeaProposal(**{
                    k: v for k, v in proposal_data.items()
                    if k in FederatedIdeaProposal.__dataclass_fields__
                })
            except Exception:
                pass
        return proposal_data

    def _proposal_to_kind(self, proposal: Any, template: NewKind) -> NewKind:
        """Convert a federation *proposal* back to a NewKind using *template* as base."""
        if isinstance(proposal, dict):
            name = proposal.get("name", template.name)
            confidence = float(proposal.get("confidence", template.confidence))
            tags = frozenset(proposal.get("tags", list(template.tags)))
        else:
            name = getattr(proposal, "name", template.name)
            confidence = float(getattr(proposal, "confidence", template.confidence))
            tags = frozenset(getattr(proposal, "tags", list(template.tags)))

        return replace(
            template,
            kind_id=str(uuid.uuid4()),
            name=name,
            confidence=_clamp(confidence),
            tags=tags,
            discovery_path=template.discovery_path + ("reconstructed from federation proposal",),
        )

    def bridge_report(self, kinds: list[NewKind], target_regime: str) -> str:
        """Format a human-readable bridge report for *kinds* targeting *target_regime*."""
        lines = [
            f"FederationKindBridge Report — target regime: {target_regime}",
            f"Generated at: {_now_iso()}",
            f"Kinds bridged: {len(kinds)}",
            "",
        ]
        for kind in kinds:
            cache_key = f"{kind.kind_id}:{target_regime}"
            cached = self._bridge_cache.get(cache_key)
            status = "bridge cached" if cached else "bridge not yet created"
            lines.append(
                f"  [{kind.status}] {kind.name!r}  "
                f"conf={kind.confidence:.3f}  tags={set(kind.tags)}  → {status}"
            )

        lines += [
            "",
            f"Total transport log entries: {len(self._transport_log)}",
            f"Total bridge cache entries:  {len(self._bridge_cache)}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Class 4: NoveltyKindScorer
# ---------------------------------------------------------------------------

@dataclass
class NoveltyKindScorer:
    """Scores discovered kinds by novelty relative to a theorem portfolio.

    Computes semantic distances, filters by novelty threshold, and ranks
    kinds by their contribution to the overall research portfolio.  All
    computations are cached to avoid redundant vector operations.
    """

    portfolio: Any = None   # TheoremPortfolio
    metric: Any = None      # NoveltyMetric
    _score_cache: dict = field(default_factory=dict)
    _dim: int = 64

    def __post_init__(self) -> None:
        """Initialise portfolio and metric from available classes."""
        if self.portfolio is None and TheoremPortfolio is not None:
            try:
                self.portfolio = TheoremPortfolio()
            except Exception:
                self.portfolio = None
        if self.metric is None and NoveltyMetric is not None:
            try:
                self.metric = NoveltyMetric()
            except Exception:
                self.metric = None

    def score(self, kind: NewKind) -> Any:
        """Compute and cache the novelty score for *kind*.

        The score combines:
        - ``semantic_distance``: distance from the kind to its nearest
          portfolio neighbour in hash-vector space.
        - ``purpose_alignment``: how well the kind's tags align with known
          research goals (approximated by tag count / 10).
        - ``feasibility``: the kind's own confidence score.
        - ``combined``: weighted blend of the above.

        Returns a ``NoveltyScore`` when available, else a dict.
        """
        if kind.kind_id in self._score_cache:
            return self._score_cache[kind.kind_id]

        semantic_distance = self.score_vs_portfolio(kind, self.portfolio)
        purpose_alignment = _clamp(len(kind.tags) / 10.0)
        feasibility = kind.confidence

        # Combined score: novelty is weighted most heavily, then feasibility.
        combined = _clamp(
            0.50 * semantic_distance
            + 0.30 * feasibility
            + 0.20 * purpose_alignment
        )

        score_data = {
            "idea_id": kind.kind_id,
            "semantic_distance": round(semantic_distance, 4),
            "purpose_alignment": round(purpose_alignment, 4),
            "feasibility": round(feasibility, 4),
            "combined": round(combined, 4),
        }

        result: Any = score_data
        if NoveltyScore is not None:
            try:
                result = NoveltyScore(**{
                    k: v for k, v in score_data.items()
                    if k in NoveltyScore.__dataclass_fields__
                })
            except Exception:
                result = score_data

        self._score_cache[kind.kind_id] = result
        return result

    def score_batch(self, kinds: list[NewKind]) -> list[Any]:
        """Return a list of novelty scores for each kind in *kinds*."""
        return [self.score(kind) for kind in kinds]

    def score_vs_portfolio(self, kind: NewKind, theorem_portfolio: Any) -> float:
        """Compute the minimum semantic distance from *kind* to portfolio entries.

        If the portfolio is empty or unavailable, returns the kind's own
        confidence as a conservative novelty proxy (higher confidence kinds
        are assumed to be more novel).
        """
        kind_vec = self._kind_to_vector(kind)

        portfolio_entries: list[Any] = []
        if theorem_portfolio is not None:
            if hasattr(theorem_portfolio, "entries"):
                try:
                    portfolio_entries = list(theorem_portfolio.entries)
                except Exception:
                    pass
            elif hasattr(theorem_portfolio, "__iter__"):
                try:
                    portfolio_entries = list(theorem_portfolio)
                except Exception:
                    pass

        if not portfolio_entries:
            # No portfolio to compare against – treat as maximally novel.
            return kind.confidence

        min_distance = 1.0
        for entry in portfolio_entries:
            # Extract a comparable kind or text from the portfolio entry.
            if isinstance(entry, NewKind):
                other_vec = self._kind_to_vector(entry)
            else:
                entry_text = str(getattr(entry, "text", entry))
                other_kind = replace(
                    kind,
                    kind_id=str(uuid.uuid4()),
                    name=entry_text[:60],
                    formal_definition=entry_text[:200],
                    tags=frozenset(),
                )
                other_vec = self._kind_to_vector(other_kind)

            dist = 1.0 - _cosine_similarity(kind_vec, other_vec)
            if dist < min_distance:
                min_distance = dist

        return _clamp(min_distance)

    def _kind_to_vector(self, kind: NewKind) -> list[float]:
        """Compute and L2-normalise the hash-bucket vector for *kind*."""
        vec = _novelty_hash_vec(kind, n_buckets=self._dim)
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def compute_semantic_distance(self, kind_a: NewKind, kind_b: NewKind) -> float:
        """Return the semantic distance between *kind_a* and *kind_b*.

        Distance is defined as ``1 - cosine_similarity(vec_a, vec_b)`` where
        both vectors are L2-normalised hash-bucket representations.
        """
        vec_a = self._kind_to_vector(kind_a)
        vec_b = self._kind_to_vector(kind_b)
        return _clamp(1.0 - _cosine_similarity(vec_a, vec_b))

    def filter_novel(self, kinds: list[NewKind], *, threshold: float = 0.3) -> list[NewKind]:
        """Return only those kinds whose semantic distance is >= *threshold*."""
        novel: list[NewKind] = []
        for kind in kinds:
            score = self.score(kind)
            dist = (
                score.get("semantic_distance", 0.0)
                if isinstance(score, dict)
                else getattr(score, "semantic_distance", 0.0)
            )
            if dist >= threshold:
                novel.append(kind)
        return novel

    def rank_by_novelty(self, kinds: list[NewKind]) -> list[tuple[NewKind, Any]]:
        """Return (kind, score) pairs sorted by semantic_distance descending."""
        scored = [(kind, self.score(kind)) for kind in kinds]
        scored.sort(
            key=lambda pair: (
                pair[1].get("semantic_distance", 0.0)
                if isinstance(pair[1], dict)
                else getattr(pair[1], "semantic_distance", 0.0)
            ),
            reverse=True,
        )
        return scored

    def novelty_summary(self, kinds: list[NewKind]) -> dict:
        """Return aggregated novelty statistics for *kinds*."""
        if not kinds:
            return {
                "total": 0,
                "novel_count": 0,
                "non_novel_count": 0,
                "mean_semantic_distance": 0.0,
                "max_semantic_distance": 0.0,
            }

        scores = self.score_batch(kinds)
        distances = [
            s.get("semantic_distance", 0.0) if isinstance(s, dict)
            else getattr(s, "semantic_distance", 0.0)
            for s in scores
        ]
        novel_count = sum(1 for d in distances if d >= 0.3)
        return {
            "total": len(kinds),
            "novel_count": novel_count,
            "non_novel_count": len(kinds) - novel_count,
            "mean_semantic_distance": round(sum(distances) / len(distances), 4),
            "max_semantic_distance": round(max(distances), 4),
        }


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two equal-length float vectors.

    Returns 0.0 when either vector is the zero vector to avoid division
    by zero.
    """
    if len(vec_a) != len(vec_b):
        raise ValueError("_cosine_similarity: vectors must have equal length.")
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return _clamp(dot / (norm_a * norm_b), -1.0, 1.0)


# ---------------------------------------------------------------------------
# Class 5: IntegratedDiscoveryPipeline
# ---------------------------------------------------------------------------

@dataclass
class IntegratedDiscoveryPipeline:
    """End-to-end pipeline integrating all kind discovery subsystems.

    Orchestrates discovery, trust assignment, idea linking, novelty scoring,
    and federation into a single coherent workflow with comprehensive reporting.

    The pipeline is designed to be robust: each phase catches exceptions from
    its sub-component and logs them in the run report rather than aborting the
    entire pipeline.  This ensures that missing optional packages degrade
    gracefully without preventing partial results.

    Attributes
    ----------
    algorithm:
        Optional DiscoveryAlgorithm enum value to pass to the discovery engine.
    trust_policy:
        Optional TrustPolicy that governs trust threshold decisions.
    federation_registry:
        Optional FederationRegistry for cross-regime kind transport.
    theorem_portfolio:
        Optional TheoremPortfolio used as reference corpus for novelty scoring.
    """

    algorithm: Any = None               # DiscoveryAlgorithm enum value
    trust_policy: Any = None            # TrustPolicy instance
    federation_registry: Any = None     # FederationRegistry
    theorem_portfolio: Any = None       # TheoremPortfolio
    _trust_discovery: TrustAwareDiscovery = field(
        default_factory=TrustAwareDiscovery
    )
    _idea_linker: IdeaKindLinker = field(default_factory=IdeaKindLinker)
    _federation_bridge: FederationKindBridge = field(
        default_factory=FederationKindBridge
    )
    _novelty_scorer: NoveltyKindScorer = field(default_factory=NoveltyKindScorer)
    _run_history: list[dict] = field(default_factory=list)
    _start_time: float = field(default_factory=time.time)

    def run(self, texts: list[str], *, domain: str = "") -> dict:
        """Execute the full five-phase pipeline on *texts*.

        Phase 1 – Discovery:  extract NewKind objects from texts.
        Phase 2 – Trust:      assign TrustLevel to each kind.
        Phase 3 – Ideas:      generate and link Idea objects.
        Phase 4 – Novelty:    score and rank by semantic novelty.
        Phase 5 – Federation: bridge novel kinds across regimes.

        Returns
        -------
        dict
            A comprehensive run report (see :meth:`_compile_report`).
        """
        run_start = time.time()
        errors: list[str] = []

        # Phase 1 – Discovery
        try:
            kinds = self._discover_phase(texts, domain)
        except Exception as exc:
            errors.append(f"discovery_phase: {exc}")
            kinds = []

        # Phase 2 – Trust
        try:
            trust_map = self._trust_phase(kinds)
        except Exception as exc:
            errors.append(f"trust_phase: {exc}")
            trust_map = [(k, "UNKNOWN") for k in kinds]

        # Phase 3 – Ideas
        try:
            idea_map = self._idea_phase(kinds)
        except Exception as exc:
            errors.append(f"idea_phase: {exc}")
            idea_map = {}

        # Phase 4 – Novelty
        try:
            novelty_scores = self._novelty_phase(kinds)
        except Exception as exc:
            errors.append(f"novelty_phase: {exc}")
            novelty_scores = [(k, {}) for k in kinds]

        # Phase 5 – Federation (optional; only run when module is present)
        federation_result: dict = {}
        if FederationRegistry is not None or self._federation_bridge.registry is not None:
            try:
                federation_result = self._federation_phase(kinds, ["general", "applied"])
            except Exception as exc:
                errors.append(f"federation_phase: {exc}")

        report = self._compile_report(kinds, trust_map, idea_map, novelty_scores)
        report["errors"] = errors
        report["elapsed_seconds"] = round(time.time() - run_start, 3)
        if federation_result:
            report["federation"] = {
                regime: len(ks) for regime, ks in federation_result.items()
            }

        self._run_history.append(report)
        return report

    def run_from_ideas(self, ideas: Any) -> dict:
        """Extract text descriptions from *ideas* and run the discovery pipeline.

        Accepts an IdeaPortfolio, a list of Idea objects, or a list of dicts.
        """
        texts: list[str] = []

        def _extract_text(idea: Any) -> str:
            for attr in ("description", "hypothesis", "purpose", "title"):
                val = idea.get(attr) if isinstance(idea, dict) else getattr(idea, attr, None)
                if val:
                    return str(val)
            return str(idea)

        if hasattr(ideas, "__iter__"):
            for idea in ideas:
                text = _extract_text(idea)
                if text:
                    texts.append(text)
        else:
            text = _extract_text(ideas)
            if text:
                texts.append(text)

        if not texts:
            return {
                "run_id": str(uuid.uuid4()),
                "error": "No text could be extracted from the provided ideas.",
                "kinds": [],
            }

        return self.run(texts, domain="ideas")

    def _discover_phase(self, texts: list[str], domain: str) -> list[NewKind]:
        """Phase 1: use TrustAwareDiscovery engine to extract NewKind instances.

        Returns validated and ranked NewKind objects, or an empty list when
        no kinds can be extracted.
        """
        pairs = self._trust_discovery.discover(texts, domain=domain)
        # discover() returns (kind, trust) pairs; extract just the kinds.
        return [kind for kind, _trust in pairs]

    def _trust_phase(self, kinds: list[NewKind]) -> list[tuple[NewKind, Any]]:
        """Phase 2: assign and record trust levels for all kinds in *kinds*."""
        return self._trust_discovery.bulk_assign_trust(kinds)

    def _idea_phase(self, kinds: list[NewKind]) -> dict[str, list]:
        """Phase 3: generate Idea objects for each kind and return the mapping."""
        return self._idea_linker.link_batch(kinds)

    def _novelty_phase(self, kinds: list[NewKind]) -> list[tuple[NewKind, Any]]:
        """Phase 4: score all kinds by novelty and return ranked (kind, score) pairs."""
        if self.theorem_portfolio is not None:
            self._novelty_scorer.portfolio = self.theorem_portfolio
        return self._novelty_scorer.rank_by_novelty(kinds)

    def _federation_phase(
        self, kinds: list[NewKind], target_regimes: list[str]
    ) -> dict:
        """Phase 5: bridge each kind into each target regime.

        Returns a dict mapping regime → list of transported kinds.
        """
        result: dict[str, list[NewKind]] = defaultdict(list)
        for regime in target_regimes:
            for kind in kinds:
                transported = self._federation_bridge.bridge(
                    kind, source_regime="kind_discovery", target_regime=regime
                )
                result[regime].extend(transported)
        return dict(result)

    def _compile_report(
        self,
        kinds: list[NewKind],
        trust_map: list[tuple[NewKind, Any]],
        idea_map: dict[str, list],
        novelty_scores: list[tuple[NewKind, Any]],
    ) -> dict:
        """Assemble a comprehensive run report from phase outputs.

        The report contains:
        - ``run_id``, ``timestamp``, ``domain``
        - ``total_kinds``
        - ``trust_distribution`` (count per trust level string)
        - ``idea_count`` (total ideas generated across all kinds)
        - ``mean_novelty_score`` (mean combined novelty score)
        - ``top_kinds`` (top 5 kinds sorted by confidence)
        - ``kind_summaries`` (per-kind metadata dicts)
        - ``pipeline_metadata`` (component availability flags)
        """
        # Trust distribution.
        trust_distribution: dict[str, int] = Counter()
        for _kind, trust in trust_map:
            level_name = getattr(trust, "name", str(trust))
            trust_distribution[level_name] += 1

        # Total idea count.
        idea_count = sum(len(ideas) for ideas in idea_map.values())

        # Mean novelty score (combined dimension).
        novelty_values: list[float] = []
        for _kind, score in novelty_scores:
            val = (
                score.get("combined", 0.0) if isinstance(score, dict)
                else getattr(score, "combined", 0.0)
            )
            novelty_values.append(float(val))
        mean_novelty = (
            sum(novelty_values) / len(novelty_values) if novelty_values else 0.0
        )

        # Top 5 kinds by confidence.
        top_kinds = sorted(kinds, key=lambda k: k.confidence, reverse=True)[:5]

        # Per-kind summary dicts.
        trust_lookup = {k.kind_id: t for k, t in trust_map}
        novelty_lookup = {k.kind_id: s for k, s in novelty_scores}
        kind_summaries = []
        for kind in kinds:
            trust = trust_lookup.get(kind.kind_id, "UNKNOWN")
            novelty = novelty_lookup.get(kind.kind_id, {})
            novelty_dist = (
                novelty.get("semantic_distance", 0.0) if isinstance(novelty, dict)
                else getattr(novelty, "semantic_distance", 0.0)
            )
            kind_summaries.append({
                "kind_id": kind.kind_id,
                "name": kind.name,
                "status": str(kind.status),
                "confidence": kind.confidence,
                "trust_level": getattr(trust, "name", str(trust)),
                "ideas_generated": len(idea_map.get(kind.kind_id, [])),
                "semantic_distance": round(float(novelty_dist), 4),
                "tags": list(kind.tags),
                "example_count": kind.example_count,
                "theorem_count": kind.theorem_count,
            })

        return {
            "run_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "total_kinds": len(kinds),
            "trust_distribution": dict(trust_distribution),
            "idea_count": idea_count,
            "mean_novelty_score": round(mean_novelty, 4),
            "top_kinds": [
                {"kind_id": k.kind_id, "name": k.name, "confidence": k.confidence}
                for k in top_kinds
            ],
            "kind_summaries": kind_summaries,
            "pipeline_metadata": {
                "trust_module_available": TrustLevel is not None,
                "ideas_module_available": Idea is not None,
                "federation_module_available": CrossRegimeBridge is not None,
                "novelty_module_available": NoveltyScore is not None,
                "algorithms_module_available": KindDiscoveryEngine is not None,
            },
        }

    def pipeline_health(self) -> dict:
        """Return a health dict reporting the status of each sub-component.

        Each component is queried for a basic attribute to verify it is
        properly initialised.  The result can be used for monitoring or
        diagnostic dashboards.
        """
        def _check(component: Any, check_attr: str) -> str:
            if component is None:
                return "unavailable"
            try:
                getattr(component, check_attr)
                return "ok"
            except AttributeError:
                return "degraded"
            except Exception as exc:
                return f"error: {exc}"

        return {
            "trust_discovery": _check(self._trust_discovery, "_decisions"),
            "idea_linker": _check(self._idea_linker, "_kind_idea_map"),
            "federation_bridge": _check(self._federation_bridge, "_bridge_cache"),
            "novelty_scorer": _check(self._novelty_scorer, "_score_cache"),
            "trust_module": "ok" if TrustLevel is not None else "not_installed",
            "ideas_module": "ok" if Idea is not None else "not_installed",
            "federation_module": "ok" if CrossRegimeBridge is not None else "not_installed",
            "novelty_module": "ok" if NoveltyScore is not None else "not_installed",
            "algorithms_module": "ok" if KindDiscoveryEngine is not None else "not_installed",
        }

    def diagnostics(self) -> str:
        """Return a multi-line diagnostic string summarising the pipeline state."""
        health = self.pipeline_health()
        run_count = len(self._run_history)
        last_run_info = "no runs yet"
        if self._run_history:
            last = self._run_history[-1]
            last_run_info = (
                f"run_id={last.get('run_id', 'n/a')}, "
                f"kinds={last.get('total_kinds', 0)}, "
                f"ideas={last.get('idea_count', 0)}, "
                f"novelty={last.get('mean_novelty_score', 0.0):.3f}, "
                f"elapsed={last.get('elapsed_seconds', 0.0):.2f}s"
            )

        uptime_seconds = round(time.time() - self._start_time, 1)
        lines = [
            "=" * 60,
            "IntegratedDiscoveryPipeline Diagnostics",
            "=" * 60,
            f"Uptime:        {uptime_seconds}s",
            f"Runs completed: {run_count}",
            f"Last run:       {last_run_info}",
            "",
            "Component health:",
        ]
        for component, status in health.items():
            lines.append(f"  {component:<28} {status}")

        score_cache_size = len(self._novelty_scorer._score_cache)
        bridge_cache_size = len(self._federation_bridge._bridge_cache)
        lines += [
            "",
            f"Novelty score cache entries:   {score_cache_size}",
            f"Federation bridge cache entries: {bridge_cache_size}",
            "=" * 60,
        ]
        return "\n".join(lines)
