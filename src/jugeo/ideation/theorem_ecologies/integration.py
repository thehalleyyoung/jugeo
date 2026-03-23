"""Integration of theorem ecologies with other jugeo packages (theory2.tex Ch61 §6).

Module layout::

    IdeaEcologyLinker         – links ideas with theorem ecologies
    TrustEcologyFilter        – filters ecologies by trust levels
    NoveltyEcologyScorer      – scores ecologies by novelty
    EcologyIdeaGenerator      – generates ideas from ecology analysis
    IntegratedEcologyPipeline – end-to-end pipeline
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from jugeo.ideation.ideas import (
        Idea, IdeaPortfolio, GainProfile, ValidationPath, TrustStatus,
        LifecycleStatus, EvaluationResult, IdeaGenerator, IdeaEvaluator,
        IdeaDependencyGraph, IdeaHistory, IdeaSerializer, IdeaDiagnostics,
        HistoryEntry, IdeaRefiner, IdeaLifecycle, IdeaProposal,
    )
except Exception:
    pass

try:
    from jugeo.ideation.novelty import (
        NoveltyScore, NoveltyOptimizer, TheoremPortfolio,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.models import (
        TheoremEcology, LemmaPortfolio, CompoundingEffect,
        EcologicalDynamic, PortfolioOptimization, EcologyHealth, DynamicType,
    )
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.ecology_modeling import (
        EcologyConfig, EcologyModeler,
    )
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.lemma_portfolios import (
        PortfolioConfig, LemmaPortfolioManager,
    )
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.compounding import (
        CompoundingConfig, CompoundingEngine,
    )
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.algorithms import (
        EcologyManager, PortfolioOptimizer, EcologicalDynamicsSimulator,
        EcologyDiagnostics,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> list[str]:
    """Tokenize *text* into lowercase alphanumeric words of length ≥ 3."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity between two token collections."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    inter = sa & sb
    return len(inter) / len(union)


def _idea_to_ecology_id(idea: "Idea") -> str:
    """Derive a stable ecology ID from an idea."""
    return f"ecology_{idea.idea_id}"


def _trust_to_health(trust: "TrustLevel") -> float:
    """Map TrustLevel to ecology health score in [0, 1]."""
    try:
        return trust.as_float()
    except AttributeError:
        mapping = {
            "CONTRADICTED": 0.0,
            "UNVERIFIED": 0.1,
            "COPILOT_SUGGESTED": 0.3,
            "ORACLE_PROPOSED": 0.4,
            "HUMAN_ATTESTED": 0.6,
            "RUNTIME_WITNESSED": 0.75,
            "SOLVER_DISCHARGED": 0.9,
            "MECHANICALLY_VERIFIED": 1.0,
        }
        return mapping.get(getattr(trust, "name", ""), 0.5)


def _ecology_to_novelty_tags(ecology: "TheoremEcology") -> list[str]:
    """Extract tag-like strings from an ecology for novelty analysis."""
    tags: list[str] = []
    for th in getattr(ecology, "theorems", []):
        tags.extend(re.findall(r"[a-z0-9]+", th.lower()))
    for lm in getattr(ecology, "lemmas", []):
        tags.extend(re.findall(r"[a-z0-9]+", lm.lower()))
    return list({t for t in tags if len(t) > 2})


def _trust_status_weight(trust_status: "TrustStatus") -> float:
    """Map a TrustStatus string enum to a numeric weight in [0, 1]."""
    weights = {
        "SPECULATIVE": 0.1,
        "PROVISIONAL": 0.35,
        "GROUNDED": 0.6,
        "VALIDATED": 0.85,
        "RETIRED": 0.0,
    }
    name = getattr(trust_status, "value", str(trust_status))
    return weights.get(name, 0.5)


def _gain_from_ecology(ecology: "TheoremEcology") -> "GainProfile":
    """Build a GainProfile whose parameters are derived from *ecology*."""
    health = _clamp(getattr(ecology, "health_score", 0.5))
    diversity = _clamp(getattr(ecology, "diversity_index", 0.3))
    size = max(1, len(getattr(ecology, "theorems", [])) + len(getattr(ecology, "lemmas", [])))
    cost = _clamp(1.0 / math.sqrt(size), 0.05, 0.95)
    uncertainty = _clamp(1.0 - health, 0.05, 0.95)
    return GainProfile(
        theorem_yield=health,
        bridge_impact=diversity,
        cost=cost,
        uncertainty=uncertainty,
    )


def _validation_from_ecology(ecology: "TheoremEcology") -> "ValidationPath":
    """Build a ValidationPath whose steps are derived from *ecology* theorems."""
    theorems = list(getattr(ecology, "theorems", []))[:5]
    steps = tuple(theorems) if theorems else ("verify_ecology",)
    evidence = [f"evidence_{t}" for t in steps]
    criteria = f"All {len(steps)} core theorems must hold with health ≥ 0.5"
    return ValidationPath(
        steps=steps,
        required_evidence=evidence,
        success_criteria=criteria,
    )


# ---------------------------------------------------------------------------
# IdeaEcologyLinker
# ---------------------------------------------------------------------------

class IdeaEcologyLinker:
    """Links ideas from :mod:`jugeo.ideation.ideas` with theorem ecologies.

    This class maintains a bidirectional mapping between *Idea* objects and
    *TheoremEcology* objects, enabling cross-module analysis such as:

    * Enriching an idea's :class:`GainProfile` with ecology health data.
    * Deriving a :class:`ValidationPath` from an ecology's theorem set.
    * Building a :class:`IdeaDependencyGraph` that reflects the dependency
      structure of the underlying ecologies.

    The linker delegates lifecycle tracking to :class:`IdeaHistory` and
    :class:`IdeaLifecycle` so that all state changes are auditable.
    """

    def __init__(
        self,
        ecology_manager: "EcologyManager",
        portfolio_manager: "LemmaPortfolioManager",
    ) -> None:
        self._ecology_manager = ecology_manager
        self._portfolio_manager = portfolio_manager
        self._links: dict[str, str] = {}           # idea_id -> ecology_id
        self._reverse: dict[str, list[str]] = defaultdict(list)  # ecology_id -> [idea_id]
        self._idea_history = IdeaHistory()
        self._lifecycle = IdeaLifecycle()

    # ------------------------------------------------------------------
    # Link management
    # ------------------------------------------------------------------

    def link_idea_to_ecology(self, idea: "Idea", ecology: "TheoremEcology") -> None:
        """Create a bidirectional link between *idea* and *ecology*.

        Side-effects:
        * Updates ``_links`` and ``_reverse`` dictionaries.
        * Records a ``"linked_to_ecology"`` event in :class:`IdeaHistory`.
        """
        ecology_id = getattr(ecology, "ecology_id", _idea_to_ecology_id(idea))
        self._links[idea.idea_id] = ecology_id
        if idea.idea_id not in self._reverse[ecology_id]:
            self._reverse[ecology_id].append(idea.idea_id)
        self._idea_history.record(
            idea_id=idea.idea_id,
            event="linked_to_ecology",
            status=idea.trust_status,
            notes=f"Linked to ecology {ecology_id} at {_now_iso()}",
        )

    def unlink_idea(self, idea_id: str) -> bool:
        """Remove the link for *idea_id*.  Returns *True* if a link existed."""
        ecology_id = self._links.pop(idea_id, None)
        if ecology_id is None:
            return False
        id_list = self._reverse.get(ecology_id, [])
        if idea_id in id_list:
            id_list.remove(idea_id)
        self._idea_history.record(
            idea_id=idea_id,
            event="unlinked_from_ecology",
            status=TrustStatus.PROVISIONAL,
            notes=f"Unlinked from ecology {ecology_id} at {_now_iso()}",
        )
        return True

    def get_ecology_for_idea(self, idea: "Idea") -> "TheoremEcology | None":
        """Return the ecology linked to *idea*, or *None* if no link exists."""
        ecology_id = self._links.get(idea.idea_id)
        if ecology_id is None:
            return None
        try:
            return self._ecology_manager.get(ecology_id)
        except Exception:
            return None

    def get_ideas_for_ecology(
        self, ecology_id: str, portfolio: "IdeaPortfolio"
    ) -> "list[Idea]":
        """Return all ideas in *portfolio* that are linked to *ecology_id*."""
        linked_ids = set(self._reverse.get(ecology_id, []))
        result: list[Idea] = []
        for idea in portfolio.rank():
            if idea.idea_id in linked_ids:
                result.append(idea)
        return result

    # ------------------------------------------------------------------
    # Idea ↔ Ecology conversion
    # ------------------------------------------------------------------

    def create_ecology_from_idea(self, idea: "Idea") -> "TheoremEcology":
        """Build a :class:`TheoremEcology` from *idea*'s fields.

        * Theorems are derived from *idea.hypothesis* tokens.
        * Lemmas are derived from *idea.validation_plan.steps*.
        * The health score is computed from *idea.trust_status*.
        """
        hypothesis_tokens = _tokenize(idea.hypothesis)
        theorem_ids = [
            f"thm_{tok}_{idea.idea_id[:6]}" for tok in hypothesis_tokens[:8]
        ]
        lemma_ids = [
            f"lm_{step[:20].replace(' ', '_')}_{idea.idea_id[:4]}"
            for step in list(idea.validation_plan.steps)[:6]
        ]
        health = _trust_status_weight(idea.trust_status)
        diversity = _clamp(idea.novelty_score)
        ecology_id = _idea_to_ecology_id(idea)
        try:
            return self._ecology_manager.create(
                ecology_id=ecology_id,
                name=f"Ecology for '{idea.title}'",
                theorems=theorem_ids,
                lemmas=lemma_ids,
                health_score=health,
                diversity_index=diversity,
            )
        except Exception:
            return TheoremEcology(
                ecology_id=ecology_id,
                name=f"Ecology for '{idea.title}'",
                theorems=theorem_ids,
                lemmas=lemma_ids,
                health_score=health,
                diversity_index=diversity,
            )

    def create_idea_from_ecology(
        self, ecology: "TheoremEcology", purpose: str = "analysis"
    ) -> "Idea":
        """Generate an :class:`Idea` that represents the ecology's potential.

        The idea's :class:`GainProfile` is seeded with the ecology's
        ``health_score`` (as *theorem_yield*) and ``diversity_index``
        (as *bridge_impact*).  The :class:`ValidationPath` uses the
        ecology's core theorems as validation steps.
        """
        gain = _gain_from_ecology(ecology)
        validation = _validation_from_ecology(ecology)
        health = _clamp(getattr(ecology, "health_score", 0.5))
        if health >= 0.85:
            trust = TrustStatus.VALIDATED
        elif health >= 0.6:
            trust = TrustStatus.GROUNDED
        elif health >= 0.35:
            trust = TrustStatus.PROVISIONAL
        else:
            trust = TrustStatus.SPECULATIVE

        ecology_id = getattr(ecology, "ecology_id", str(uuid.uuid4()))
        title = getattr(ecology, "name", ecology_id)
        hypothesis = (
            f"The theorem ecology '{title}' yields compounding insights "
            f"with health {health:.2f} and diversity "
            f"{getattr(ecology, 'diversity_index', 0.0):.2f}."
        )
        return Idea(
            idea_id=f"idea_{ecology_id}",
            title=f"Insights from {title}",
            purpose=purpose,
            target_area=getattr(ecology, "domain", "theorem_ecology"),
            hypothesis=hypothesis,
            predicted_gain=gain,
            novelty_score=_clamp(getattr(ecology, "diversity_index", 0.3)),
            validation_plan=validation,
            trust_status=trust,
        )

    def enrich_idea_with_ecology(
        self, idea: "Idea", ecology: "TheoremEcology"
    ) -> "Idea":
        """Return a new :class:`Idea` enriched by ecology data.

        Uses :meth:`Idea.with_adjusted_gain` and
        :meth:`Idea.with_validation_step` to attach ecology-derived
        compounding information.
        """
        health = _clamp(getattr(ecology, "health_score", 0.5))
        diversity = _clamp(getattr(ecology, "diversity_index", 0.3))

        # Build an adjusted gain profile: boost theorem_yield and bridge_impact.
        old_gain = idea.predicted_gain
        new_gain = GainProfile(
            theorem_yield=_clamp(old_gain.theorem_yield * (1.0 + 0.2 * health)),
            bridge_impact=_clamp(old_gain.bridge_impact * (1.0 + 0.15 * diversity)),
            cost=_clamp(old_gain.cost * (1.0 - 0.1 * health)),
            uncertainty=_clamp(old_gain.uncertainty * (1.0 - 0.1 * health)),
        )
        enriched = idea.with_adjusted_gain(new_gain)

        # Append one validation step derived from the ecology's top theorem.
        top_theorem = next(iter(getattr(ecology, "theorems", [])), None)
        if top_theorem:
            enriched = enriched.with_validation_step(
                f"verify_{top_theorem[:30]}",
                f"Confirm theorem {top_theorem} holds in context.",
            )
        self._idea_history.record(
            idea_id=idea.idea_id,
            event="enriched_by_ecology",
            status=idea.trust_status,
            notes=f"Gain boosted by ecology health={health:.2f}",
        )
        return enriched

    # ------------------------------------------------------------------
    # Graph and portfolio utilities
    # ------------------------------------------------------------------

    def build_dependency_graph(
        self,
        portfolio: "IdeaPortfolio",
        ecologies: "list[TheoremEcology]",
    ) -> "IdeaDependencyGraph":
        """Build an :class:`IdeaDependencyGraph` reflecting ecology dependencies.

        Two ideas are linked (parent → child) when their ecologies share at
        least one theorem in common, with the higher-health ecology being the
        parent.
        """
        graph = IdeaDependencyGraph()
        ideas = portfolio.rank()

        # Build idea → ecology mapping for this batch.
        idea_ecology: dict[str, TheoremEcology] = {}
        for idea in ideas:
            eid = self._links.get(idea.idea_id)
            for eco in ecologies:
                if getattr(eco, "ecology_id", None) == eid:
                    idea_ecology[idea.idea_id] = eco
                    break

        # Link ideas that share theorems between their ecologies.
        for i, idea_a in enumerate(ideas):
            eco_a = idea_ecology.get(idea_a.idea_id)
            if eco_a is None:
                continue
            th_a = set(getattr(eco_a, "theorems", []))
            h_a = getattr(eco_a, "health_score", 0.0)
            for idea_b in ideas[i + 1:]:
                eco_b = idea_ecology.get(idea_b.idea_id)
                if eco_b is None:
                    continue
                th_b = set(getattr(eco_b, "theorems", []))
                if th_a & th_b:
                    h_b = getattr(eco_b, "health_score", 0.0)
                    parent, child = (
                        (idea_a, idea_b) if h_a >= h_b else (idea_b, idea_a)
                    )
                    try:
                        graph.add_dependency(parent.idea_id, child.idea_id)
                    except Exception:
                        pass
        return graph

    def portfolio_ecology_alignment(
        self, portfolio: "IdeaPortfolio"
    ) -> "dict[str, float]":
        """Return a mapping of idea_id → ecology health score for linked ideas."""
        result: dict[str, float] = {}
        for idea in portfolio.rank():
            ecology = self.get_ecology_for_idea(idea)
            if ecology is not None:
                result[idea.idea_id] = _clamp(
                    getattr(ecology, "health_score", 0.5)
                )
            else:
                result[idea.idea_id] = 0.0
        return result

    def evaluate_ideas_in_ecology_context(
        self,
        portfolio: "IdeaPortfolio",
        ecology: "TheoremEcology",
        evaluator: "IdeaEvaluator",
    ) -> "list[EvaluationResult]":
        """Evaluate each idea in *portfolio* in the context of *ecology*.

        The ecology's health and compounding potential boost the evaluator's
        *compounding* weight so that ideas in healthy ecologies score higher.
        """
        health = _clamp(getattr(ecology, "health_score", 0.5))
        compounding_boost = _clamp(
            getattr(ecology, "compounding_potential", health * 0.8)
        )

        # Build a temporary dependency graph for context.
        deps = self.build_dependency_graph(portfolio, [ecology])
        purpose = getattr(ecology, "domain", "ecology_evaluation")

        results: list[EvaluationResult] = []
        for idea in portfolio.rank():
            try:
                base: EvaluationResult = evaluator.evaluate(
                    idea, portfolio, deps, purpose, idea.target_area
                )
                # Boost compounding score by ecology health.
                boosted_compounding = _clamp(
                    base.compounding + compounding_boost * 0.3
                )
                new_total = (
                    base.total_score
                    + (boosted_compounding - base.compounding) * getattr(evaluator, "compounding", 0.25)
                )
                result = EvaluationResult(
                    novelty=base.novelty,
                    feasibility=base.feasibility,
                    compounding=boosted_compounding,
                    alignment=base.alignment,
                    total_score=_clamp(new_total),
                    recommendation=base.recommendation,
                )
            except Exception:
                result = EvaluationResult(
                    novelty=idea.novelty_score,
                    feasibility=1.0 - idea.predicted_gain.uncertainty,
                    compounding=compounding_boost,
                    alignment=health,
                    total_score=_clamp(
                        (idea.novelty_score + compounding_boost + health) / 3
                    ),
                    recommendation="consider",
                )
            results.append(result)
        return results

    def lifecycle_update_from_ecology(
        self, idea: "Idea", ecology: "TheoremEcology"
    ) -> "LifecycleStatus":
        """Determine lifecycle status for *idea* based on *ecology* health.

        High-health ecologies move ideas toward *ACCEPTED*; low-health ones
        defer or retire them.
        """
        health = _clamp(getattr(ecology, "health_score", 0.5))
        if health >= 0.8:
            status = LifecycleStatus.ACCEPTED
            try:
                self._lifecycle.accept(idea.idea_id)
            except Exception:
                pass
        elif health >= 0.5:
            status = LifecycleStatus.PROPOSED
        elif health >= 0.2:
            status = LifecycleStatus.DEFERRED
            try:
                self._lifecycle.defer(idea.idea_id)
            except Exception:
                pass
        else:
            status = LifecycleStatus.REJECTED
            try:
                self._lifecycle.reject(idea.idea_id)
            except Exception:
                pass

        self._idea_history.record(
            idea_id=idea.idea_id,
            event="lifecycle_updated",
            status=idea.trust_status,
            notes=f"Ecology health {health:.2f} → lifecycle {status}",
        )
        return status

    def suggest_refinements(
        self,
        idea: "Idea",
        ecology: "TheoremEcology",
        refiner: "IdeaRefiner",
        evaluator: "IdeaEvaluator",
        portfolio: "IdeaPortfolio",
    ) -> "list[Idea]":
        """Use :class:`IdeaRefiner` to generate variants aligned with *ecology*.

        Steps:
        1. Narrow or widen scope depending on ecology size.
        2. Strengthen validation to match ecology depth.
        3. Reduce cost for large, healthy ecologies.
        4. Filter variants whose Jaccard similarity to ecology tags > 0.1.
        """
        eco_tags = set(_ecology_to_novelty_tags(ecology))
        idea_tokens = set(_tokenize(idea.hypothesis) + _tokenize(idea.title))
        size = len(getattr(ecology, "theorems", [])) + len(getattr(ecology, "lemmas", []))
        health = _clamp(getattr(ecology, "health_score", 0.5))

        variants: list[Idea] = []
        try:
            if size > 10:
                variants.append(refiner.narrow_scope(idea))
            else:
                variants.append(refiner.widen_scope(idea))
            variants.append(refiner.strengthen_validation(idea))
            if health > 0.6:
                variants.append(refiner.reduce_cost(idea))
            refined = refiner.refine(idea, portfolio, evaluator)
            variants.append(refined)
        except Exception:
            pass

        # Filter by ecology tag alignment.
        aligned: list[Idea] = []
        for v in variants:
            v_tokens = set(_tokenize(v.hypothesis) + _tokenize(v.title))
            sim = _jaccard(v_tokens, eco_tags)
            if sim > 0.05 or not eco_tags:
                aligned.append(v)

        return aligned


# ---------------------------------------------------------------------------
# TrustEcologyFilter
# ---------------------------------------------------------------------------

class TrustEcologyFilter:
    """Filters ecologies by trust levels from :mod:`jugeo.evidence.trust`.

    The filter assigns each ecology a :class:`TrustLevel` based on its health
    score, connectivity, and size.  Ecologies below *min_trust* are excluded
    from analysis pipelines so that low-confidence results do not pollute
    downstream computations.
    """

    # Mapping from TrustStatus (ideas module) to TrustLevel (evidence module)
    _TRUST_STATUS_MAP: dict[str, str] = {
        "SPECULATIVE": "COPILOT_SUGGESTED",
        "PROVISIONAL": "ORACLE_PROPOSED",
        "GROUNDED": "HUMAN_ATTESTED",
        "VALIDATED": "SOLVER_DISCHARGED",
        "RETIRED": "CONTRADICTED",
    }

    def __init__(
        self,
        min_trust: "TrustLevel | None" = None,
        trust_algebra: "TrustAlgebra | None" = None,
    ) -> None:
        try:
            self.min_trust = min_trust if min_trust is not None else TrustLevel.UNVERIFIED
        except Exception:
            self.min_trust = None
        try:
            self.trust_algebra = trust_algebra if trust_algebra is not None else TrustAlgebra()
        except Exception:
            self.trust_algebra = None

    # ------------------------------------------------------------------
    # Core filtering
    # ------------------------------------------------------------------

    def filter(self, ecologies: "list[TheoremEcology]") -> "list[TheoremEcology]":
        """Keep ecologies whose assigned trust ≥ *min_trust*."""
        result: list[TheoremEcology] = []
        for eco in ecologies:
            level = self.assign_trust(eco)
            try:
                if level.is_at_least(self.min_trust):
                    result.append(eco)
            except Exception:
                result.append(eco)
        return result

    def filter_by_level(
        self, ecologies: "list[TheoremEcology]", level: "TrustLevel"
    ) -> "list[TheoremEcology]":
        """Keep ecologies whose assigned trust ≥ *level*."""
        result: list[TheoremEcology] = []
        for eco in ecologies:
            assigned = self.assign_trust(eco)
            try:
                if assigned.is_at_least(level):
                    result.append(eco)
            except Exception:
                result.append(eco)
        return result

    # ------------------------------------------------------------------
    # Trust assignment and algebra
    # ------------------------------------------------------------------

    def assign_trust(self, ecology: "TheoremEcology") -> "TrustLevel":
        """Compute a :class:`TrustLevel` for *ecology*.

        The assignment uses health score as the primary signal, with
        connectivity and size as secondary modifiers.
        """
        health = _clamp(getattr(ecology, "health_score", 0.5))
        n_theorems = len(getattr(ecology, "theorems", []))
        n_lemmas = len(getattr(ecology, "lemmas", []))
        size_bonus = _clamp((n_theorems + n_lemmas) / 40.0) * 0.1
        score = _clamp(health + size_bonus)

        try:
            if score >= 0.95:
                return TrustLevel.MECHANICALLY_VERIFIED
            elif score >= 0.85:
                return TrustLevel.SOLVER_DISCHARGED
            elif score >= 0.72:
                return TrustLevel.RUNTIME_WITNESSED
            elif score >= 0.58:
                return TrustLevel.HUMAN_ATTESTED
            elif score >= 0.38:
                return TrustLevel.ORACLE_PROPOSED
            elif score >= 0.25:
                return TrustLevel.COPILOT_SUGGESTED
            elif score >= 0.05:
                return TrustLevel.UNVERIFIED
            else:
                return TrustLevel.CONTRADICTED
        except Exception:
            return TrustLevel.UNVERIFIED  # type: ignore[return-value]

    def compose_trust(
        self,
        ecology_a: "TheoremEcology",
        ecology_b: "TheoremEcology",
    ) -> "TrustLevel":
        """Use :meth:`TrustAlgebra.compose` to combine trust levels of two ecologies."""
        level_a = self.assign_trust(ecology_a)
        level_b = self.assign_trust(ecology_b)
        if self.trust_algebra is None:
            try:
                return level_a.min_with(level_b)
            except Exception:
                return level_a
        try:
            return self.trust_algebra.compose(level_a, level_b)
        except Exception:
            try:
                return level_a.min_with(level_b)
            except Exception:
                return level_a

    def attenuate_by_dependency_depth(
        self, ecology: "TheoremEcology", base_level: "TrustLevel"
    ) -> "TrustLevel":
        """Attenuate *base_level* by the dependency depth of *ecology*.

        Each additional level of dependency depth weakens trust by one step,
        reflecting that deep dependency chains introduce more failure points.
        """
        depth = len(getattr(ecology, "dependency_chain", [])) or max(
            1,
            len(getattr(ecology, "theorems", [])) // 4,
        )
        current = base_level
        for _ in range(min(depth, 4)):
            try:
                current = current.weakened_by(1)
            except Exception:
                if self.trust_algebra is not None:
                    try:
                        current = self.trust_algebra.demote(current)
                    except Exception:
                        pass
                break
        return current

    def trust_certificate(self, ecology: "TheoremEcology") -> "dict[str, Any]":
        """Return a dictionary certificate describing the ecology's trust level."""
        level = self.assign_trust(ecology)
        health = _clamp(getattr(ecology, "health_score", 0.5))
        return {
            "ecology_id": getattr(ecology, "ecology_id", "unknown"),
            "trust_level": getattr(level, "name", str(level)),
            "trust_float": _trust_to_health(level),
            "health_score": health,
            "rationale": (
                f"Health {health:.2f} maps to trust level "
                f"{getattr(level, 'name', str(level))}."
            ),
            "conditions": [
                "All component theorems must be verified.",
                f"Health must remain ≥ {health:.2f}.",
            ],
            "issued_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # Idea portfolio integration
    # ------------------------------------------------------------------

    def filter_portfolio_ideas(
        self,
        portfolio: "IdeaPortfolio",
        min_trust_status: "TrustStatus",
    ) -> "IdeaPortfolio":
        """Filter *portfolio* to ideas whose :class:`TrustStatus` ≥ *min_trust_status*.

        Returns a new :class:`IdeaPortfolio` containing only ideas that pass
        the trust threshold.  Uses :meth:`Idea.is_trusted` where available.
        """
        new_portfolio = IdeaPortfolio()
        for idea in portfolio.rank():
            try:
                if idea.is_trusted(min_trust_status):
                    new_portfolio.add(idea)
            except Exception:
                weight = _trust_status_weight(idea.trust_status)
                threshold = _trust_status_weight(min_trust_status)
                if weight >= threshold:
                    new_portfolio.add(idea)
        return new_portfolio

    def idea_trust_to_ecology_trust(
        self, trust_status: "TrustStatus"
    ) -> "TrustLevel":
        """Map a :class:`TrustStatus` (ideas module) to a :class:`TrustLevel` (evidence module)."""
        name = getattr(trust_status, "value", str(trust_status))
        level_name = self._TRUST_STATUS_MAP.get(name, "UNVERIFIED")
        try:
            return TrustLevel[level_name]
        except Exception:
            try:
                return TrustLevel.UNVERIFIED
            except Exception:
                return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# NoveltyEcologyScorer
# ---------------------------------------------------------------------------

class NoveltyEcologyScorer:
    """Scores ecologies by novelty using :mod:`jugeo.ideation.novelty`.

    Wraps a :class:`TheoremPortfolio` to track which theorems are already
    known and quantify how much new knowledge each candidate ecology
    contributes.
    """

    def __init__(
        self, theorem_portfolio: "TheoremPortfolio | None" = None
    ) -> None:
        try:
            self.theorem_portfolio = (
                theorem_portfolio
                if theorem_portfolio is not None
                else TheoremPortfolio()
            )
        except Exception:
            self.theorem_portfolio = None
        try:
            self._optimizer = NoveltyOptimizer()
        except Exception:
            self._optimizer = None

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_ecology(self, ecology: "TheoremEcology") -> "NoveltyScore":
        """Score *ecology*'s novelty relative to the existing theorem portfolio.

        Novelty is measured as Jaccard distance between the ecology's tag set
        and the known portfolio, weighted by ecology health and diversity.
        """
        tags = _ecology_to_novelty_tags(ecology)
        health = _clamp(getattr(ecology, "health_score", 0.5))
        diversity = _clamp(getattr(ecology, "diversity_index", 0.3))
        ecology_id = getattr(ecology, "ecology_id", str(uuid.uuid4()))

        # Compute semantic distance vs. known portfolio.
        if self.theorem_portfolio is not None and tags:
            try:
                known_ids = list(self.theorem_portfolio.ids())
                known_tags: list[str] = []
                for kid in known_ids[:50]:
                    try:
                        vecs = self.theorem_portfolio.vectors()
                        known_tags.extend(vecs.get(kid, []))
                    except Exception:
                        pass
                semantic_distance = 1.0 - _jaccard(tags, known_tags) if known_tags else 0.8
            except Exception:
                semantic_distance = 0.7
        else:
            semantic_distance = 0.7 + 0.2 * diversity

        feasibility = _clamp(health)
        purpose_alignment = _clamp(diversity * 0.5 + health * 0.5)
        composite = _clamp(
            0.4 * semantic_distance
            + 0.3 * purpose_alignment
            + 0.3 * feasibility
        )
        explanation = (
            f"Ecology {ecology_id}: semantic_dist={semantic_distance:.2f}, "
            f"alignment={purpose_alignment:.2f}, feasibility={feasibility:.2f}."
        )
        try:
            return NoveltyScore(
                idea_id=ecology_id,
                semantic_distance=semantic_distance,
                purpose_alignment=purpose_alignment,
                feasibility=feasibility,
                composite=composite,
                explanation=explanation,
                title=getattr(ecology, "name", ecology_id),
                timestamp=_now_iso(),
            )
        except Exception as exc:
            return NoveltyScore(  # type: ignore[call-arg]
                idea_id=ecology_id,
                semantic_distance=semantic_distance,
                purpose_alignment=purpose_alignment,
                feasibility=feasibility,
                composite=composite,
                explanation=explanation,
            )

    def score_batch(
        self, ecologies: "list[TheoremEcology]"
    ) -> "list[NoveltyScore]":
        """Score all ecologies in *ecologies* and return the list of scores."""
        return [self.score_ecology(eco) for eco in ecologies]

    def update_portfolio_from_ecology(self, ecology: "TheoremEcology") -> None:
        """Add *ecology*'s theorems to the internal :class:`TheoremPortfolio`."""
        if self.theorem_portfolio is None:
            return
        ecology_id = getattr(ecology, "ecology_id", str(uuid.uuid4()))
        for theorem in getattr(ecology, "theorems", []):
            try:
                self.theorem_portfolio.add(
                    theorem_id=theorem,
                    title=theorem,
                    statement=f"Theorem {theorem} from ecology {ecology_id}.",
                    tags=_ecology_to_novelty_tags(ecology),
                    proved=getattr(ecology, "health_score", 0.0) > 0.7,
                )
            except Exception:
                pass

    def most_novel(
        self, ecologies: "list[TheoremEcology]", k: int = 5
    ) -> "list[TheoremEcology]":
        """Return the *k* most novel ecologies by composite novelty score."""
        scored = sorted(
            ecologies,
            key=lambda e: self.score_ecology(e).composite,
            reverse=True,
        )
        return scored[:k]

    def portfolio_gap_analysis(
        self, ecologies: "list[TheoremEcology]"
    ) -> "list[str]":
        """Identify knowledge gaps in the combined theorem portfolio.

        Collects all tags from *ecologies* and delegates gap detection to
        :meth:`TheoremPortfolio.gaps`.
        """
        all_tags: list[str] = []
        for eco in ecologies:
            all_tags.extend(_ecology_to_novelty_tags(eco))
        if self.theorem_portfolio is None:
            return all_tags[:10]
        try:
            return self.theorem_portfolio.gaps(set(all_tags))
        except Exception:
            return list(set(all_tags))[:10]

    def novelty_diversity_score(
        self, ecologies: "list[TheoremEcology]"
    ) -> float:
        """Compute overall novelty diversity across a set of ecologies.

        Diversity = mean composite novelty score, penalised for duplicate
        tag overlap between ecology pairs.
        """
        if not ecologies:
            return 0.0
        scores = self.score_batch(ecologies)
        mean_composite = sum(s.composite for s in scores) / len(scores)

        # Penalise pair-wise overlap.
        tag_sets = [set(_ecology_to_novelty_tags(e)) for e in ecologies]
        overlap_penalty = 0.0
        count = 0
        for i in range(len(tag_sets)):
            for j in range(i + 1, len(tag_sets)):
                sim = _jaccard(tag_sets[i], tag_sets[j])
                overlap_penalty += sim
                count += 1
        avg_overlap = overlap_penalty / max(count, 1)
        return _clamp(mean_composite * (1.0 - 0.5 * avg_overlap))

    def rank_by_novelty(
        self, ecologies: "list[TheoremEcology]"
    ) -> "list[tuple[TheoremEcology, NoveltyScore]]":
        """Return *ecologies* sorted by descending composite novelty score."""
        pairs = [(eco, self.score_ecology(eco)) for eco in ecologies]
        return sorted(pairs, key=lambda p: p[1].composite, reverse=True)


# ---------------------------------------------------------------------------
# EcologyIdeaGenerator
# ---------------------------------------------------------------------------

class EcologyIdeaGenerator:
    """Generates :class:`Idea` objects from theorem ecology analysis.

    Bridges :mod:`jugeo.ideation.theorem_ecologies` and
    :mod:`jugeo.ideation.ideas` by translating ecological structure
    (theorem sets, compounding effects, health metrics) into actionable ideas.
    """

    def __init__(
        self,
        ecology_manager: "EcologyManager",
        generator: "IdeaGenerator | None" = None,
        evaluator: "IdeaEvaluator | None" = None,
    ) -> None:
        self._ecology_manager = ecology_manager
        try:
            self._generator = generator if generator is not None else IdeaGenerator()
        except Exception:
            self._generator = None
        try:
            self._evaluator = evaluator if evaluator is not None else IdeaEvaluator()
        except Exception:
            self._evaluator = None
        self._lifecycle = IdeaLifecycle()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_from_ecology(
        self,
        ecology: "TheoremEcology",
        purpose: str,
        count: int = 5,
    ) -> "list[Idea]":
        """Use :meth:`IdeaGenerator.generate` with ecology-derived observations.

        Observations are built from the ecology's theorems, lemmas, health
        score, and diversity index, giving the generator rich context.
        """
        health = _clamp(getattr(ecology, "health_score", 0.5))
        diversity = _clamp(getattr(ecology, "diversity_index", 0.3))
        name = getattr(ecology, "name", getattr(ecology, "ecology_id", "ecology"))
        theorems = list(getattr(ecology, "theorems", []))[:6]
        lemmas = list(getattr(ecology, "lemmas", []))[:4]

        observations: list[str] = [
            f"Ecology '{name}' has health_score={health:.2f}.",
            f"Diversity index is {diversity:.2f}.",
        ]
        for t in theorems:
            observations.append(f"Theorem '{t}' is part of the ecology.")
        for lm in lemmas:
            observations.append(f"Lemma '{lm}' supports the ecology.")

        target_area = getattr(ecology, "domain", "theorem_ecology")
        if self._generator is not None:
            try:
                return self._generator.generate(
                    purpose=purpose,
                    target_area=target_area,
                    observations=observations,
                    count=count,
                )
            except Exception:
                pass

        # Fallback: synthesise ideas directly.
        ideas: list[Idea] = []
        for i in range(count):
            gain = GainProfile(
                theorem_yield=_clamp(health + 0.05 * i),
                bridge_impact=diversity,
                cost=_clamp(0.4 - 0.02 * i),
                uncertainty=_clamp(0.5 - 0.05 * health),
            )
            validation = ValidationPath(
                steps=tuple(theorems[:3]) or ("verify_ecology",),
                required_evidence=[f"evidence_{t}" for t in theorems[:3]] or ["ecology_evidence"],
                success_criteria=f"Ecology health ≥ {health:.2f} maintained.",
            )
            idea = Idea(
                idea_id=f"idea_{name}_{i}_{uuid.uuid4().hex[:6]}",
                title=f"Ecology Insight #{i + 1} from {name}",
                purpose=purpose,
                target_area=target_area,
                hypothesis=(
                    f"Observation #{i + 1}: ecology '{name}' with health "
                    f"{health:.2f} can yield insight #{i + 1}."
                ),
                predicted_gain=gain,
                novelty_score=_clamp(diversity + 0.05 * i),
                validation_plan=validation,
                trust_status=(
                    TrustStatus.GROUNDED if health > 0.6 else TrustStatus.PROVISIONAL
                ),
            )
            ideas.append(idea)
        return ideas

    def generate_from_compounding(
        self,
        effects: "list[CompoundingEffect]",
        purpose: str,
        area: str,
    ) -> "list[Idea]":
        """Generate ideas from each :class:`CompoundingEffect`.

        Each effect's ``compound_result`` and ``amplification`` are translated
        into a :class:`GainProfile` and hypothesis.
        """
        ideas: list[Idea] = []
        for effect in effects:
            result_text = getattr(effect, "compound_result", "compounding_effect")
            amplification = _clamp(getattr(effect, "amplification", 0.5))
            base_value = _clamp(getattr(effect, "base_value", 0.4))
            gain = GainProfile(
                theorem_yield=_clamp(base_value * amplification),
                bridge_impact=_clamp(amplification * 0.8),
                cost=_clamp(0.5 - amplification * 0.2),
                uncertainty=_clamp(0.6 - base_value * 0.3),
            )
            steps = (
                f"verify_{result_text[:20]}",
                f"measure_amplification_{amplification:.2f}",
            )
            validation = ValidationPath(
                steps=steps,
                required_evidence=list(steps),
                success_criteria=(
                    f"Amplification ≥ {amplification:.2f} confirmed."
                ),
            )
            idea = Idea(
                idea_id=f"idea_compound_{uuid.uuid4().hex[:8]}",
                title=f"Compounding: {result_text[:40]}",
                purpose=purpose,
                target_area=area,
                hypothesis=(
                    f"Compounding effect '{result_text}' with amplification "
                    f"{amplification:.2f} can drive new insights in {area}."
                ),
                predicted_gain=gain,
                novelty_score=_clamp(amplification * 0.7),
                validation_plan=validation,
                trust_status=(
                    TrustStatus.GROUNDED if amplification > 0.6 else TrustStatus.PROVISIONAL
                ),
            )
            ideas.append(idea)
        return ideas

    def generate_portfolio_from_ecologies(
        self,
        ecologies: "list[TheoremEcology]",
        purpose: str,
        target_area: str,
    ) -> "IdeaPortfolio":
        """Generate many ideas from *ecologies* and return them as a ranked portfolio."""
        portfolio = IdeaPortfolio()
        for eco in ecologies:
            ideas = self.generate_from_ecology(eco, purpose, count=3)
            for idea in ideas:
                try:
                    portfolio.add(idea)
                except Exception:
                    pass
        return portfolio

    def mutate_idea_with_ecology(
        self, idea: "Idea", ecology: "TheoremEcology"
    ) -> "list[Idea]":
        """Use :meth:`IdeaGenerator.mutate` with ecology-based emphases."""
        eco_tags = _ecology_to_novelty_tags(ecology)
        emphases = eco_tags[:4] if eco_tags else ["theorem", "ecology", "compound"]

        if self._generator is None:
            return [idea]
        try:
            return [self._generator.mutate(idea, emphasis=e) for e in emphases]
        except Exception:
            return [idea]

    def analogize_across_ecologies(
        self,
        idea: "Idea",
        source_ecology: "TheoremEcology",
        target_ecology: "TheoremEcology",
    ) -> "Idea":
        """Use :meth:`IdeaGenerator.analogize` to map an idea between ecologies."""
        source_area = getattr(source_ecology, "domain", "source_ecology")
        if self._generator is not None:
            try:
                return self._generator.analogize(idea, source_area=source_area)
            except Exception:
                pass

        # Fallback: adapt the idea's target area and hypothesis.
        target_area = getattr(target_ecology, "domain", "target_ecology")
        new_hypothesis = (
            f"[Analogized from '{source_area}' to '{target_area}'] "
            + idea.hypothesis
        )
        new_gain = _gain_from_ecology(target_ecology)
        return Idea(
            idea_id=f"analogy_{idea.idea_id}_{uuid.uuid4().hex[:6]}",
            title=f"[Analogy] {idea.title}",
            purpose=idea.purpose,
            target_area=target_area,
            hypothesis=new_hypothesis,
            predicted_gain=new_gain,
            novelty_score=_clamp(idea.novelty_score * 1.1),
            validation_plan=_validation_from_ecology(target_ecology),
            trust_status=TrustStatus.PROVISIONAL,
        )

    def evaluate_ecology_ideas(
        self,
        ideas: "list[Idea]",
        ecology: "TheoremEcology",
        portfolio: "IdeaPortfolio",
    ) -> "list[EvaluationResult]":
        """Evaluate *ideas*, boosting compounding score for healthy ecologies."""
        health = _clamp(getattr(ecology, "health_score", 0.5))
        results: list[EvaluationResult] = []
        for idea in ideas:
            if self._evaluator is not None:
                try:
                    deps = IdeaDependencyGraph()
                    base = self._evaluator.evaluate(
                        idea, portfolio, deps, idea.purpose, idea.target_area
                    )
                    boosted = _clamp(base.compounding + health * 0.2)
                    results.append(
                        EvaluationResult(
                            novelty=base.novelty,
                            feasibility=base.feasibility,
                            compounding=boosted,
                            alignment=base.alignment,
                            total_score=_clamp(base.total_score + 0.05 * health),
                            recommendation=base.recommendation,
                        )
                    )
                    continue
                except Exception:
                    pass
            # Fallback evaluation.
            results.append(
                EvaluationResult(
                    novelty=idea.novelty_score,
                    feasibility=1.0 - idea.predicted_gain.uncertainty,
                    compounding=_clamp(health * 0.9),
                    alignment=health,
                    total_score=_clamp(
                        (idea.novelty_score + health + (1.0 - idea.predicted_gain.uncertainty)) / 3.0
                    ),
                    recommendation="accept" if health > 0.6 else "review",
                )
            )
        return results

    def lifecycle_manage(
        self, ideas: "list[Idea]", ecology: "TheoremEcology"
    ) -> "dict[str, LifecycleStatus]":
        """Use :class:`IdeaLifecycle` to manage idea statuses by ecology health."""
        health = _clamp(getattr(ecology, "health_score", 0.5))
        statuses: dict[str, LifecycleStatus] = {}
        for idea in ideas:
            if health >= 0.75:
                try:
                    self._lifecycle.accept(idea.idea_id)
                except Exception:
                    pass
                statuses[idea.idea_id] = LifecycleStatus.ACCEPTED
            elif health >= 0.45:
                try:
                    self._lifecycle.propose(idea)
                except Exception:
                    pass
                statuses[idea.idea_id] = LifecycleStatus.PROPOSED
            elif health >= 0.2:
                try:
                    self._lifecycle.defer(idea.idea_id)
                except Exception:
                    pass
                statuses[idea.idea_id] = LifecycleStatus.DEFERRED
            else:
                try:
                    self._lifecycle.reject(idea.idea_id)
                except Exception:
                    pass
                statuses[idea.idea_id] = LifecycleStatus.REJECTED
        return statuses


# ---------------------------------------------------------------------------
# IntegratedEcologyPipeline
# ---------------------------------------------------------------------------

class IntegratedEcologyPipeline:
    """End-to-end pipeline connecting theorem ecologies with idea generation.

    Wires together:
    * :class:`EcologyManager` / :class:`EcologyModeler` for ecology construction.
    * :class:`LemmaPortfolioManager` for portfolio management.
    * :class:`CompoundingEngine` for compounding effect analysis.
    * :class:`PortfolioOptimizer` for portfolio optimisation.
    * :class:`IdeaEcologyLinker`, :class:`TrustEcologyFilter`,
      :class:`NoveltyEcologyScorer`, :class:`EcologyIdeaGenerator` for
      cross-module integration.
    * :class:`EcologyDiagnostics` for health reporting.

    The primary entry point is :meth:`run`, which executes the full pipeline
    from raw theorem/lemma lists to an enriched idea portfolio.
    """

    def __init__(
        self,
        ecology_config: "EcologyConfig | None" = None,
        portfolio_config: "PortfolioConfig | None" = None,
        compounding_config: "CompoundingConfig | None" = None,
    ) -> None:
        try:
            self._ecology_config = ecology_config or EcologyConfig()
        except Exception:
            self._ecology_config = None
        try:
            self._portfolio_config = portfolio_config or PortfolioConfig()
        except Exception:
            self._portfolio_config = None
        try:
            self._compounding_config = compounding_config or CompoundingConfig()
        except Exception:
            self._compounding_config = None

        try:
            self._ecology_manager = EcologyManager(self._ecology_config)
        except Exception:
            self._ecology_manager = EcologyManager()  # type: ignore[call-arg]
        try:
            self._portfolio_manager = LemmaPortfolioManager(self._portfolio_config)
        except Exception:
            self._portfolio_manager = LemmaPortfolioManager()  # type: ignore[call-arg]
        try:
            self._compounding_engine = CompoundingEngine(self._compounding_config)
        except Exception:
            self._compounding_engine = CompoundingEngine()  # type: ignore[call-arg]
        try:
            self._portfolio_optimizer = PortfolioOptimizer()
        except Exception:
            self._portfolio_optimizer = None
        try:
            self._diagnostics = EcologyDiagnostics()
        except Exception:
            self._diagnostics = None

        self._linker = IdeaEcologyLinker(self._ecology_manager, self._portfolio_manager)
        self._trust_filter = TrustEcologyFilter()
        self._novelty_scorer = NoveltyEcologyScorer()
        self._idea_generator = EcologyIdeaGenerator(self._ecology_manager)
        self._idea_history = IdeaHistory()

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        theorems: "list[str]",
        lemmas: "list[str]",
        dependencies: "dict[str, list[str]]",
        purpose: str,
        target_area: str,
        name: str = "pipeline",
    ) -> "dict[str, Any]":
        """Execute the full ecology → portfolio → ideas pipeline.

        Steps
        -----
        1. Build a :class:`TheoremEcology` from *theorems* and *lemmas*.
        2. Create a :class:`LemmaPortfolio` and optimise it.
        3. Compute compounding effects.
        4. Generate ideas from the ecology.
        5. Score novelty and filter by trust.
        6. Return a rich result dictionary.
        """
        t_start = time.monotonic()

        # Step 1 – build ecology.
        ecology: TheoremEcology
        try:
            ecology = self._ecology_manager.create(
                ecology_id=f"{name}_{uuid.uuid4().hex[:8]}",
                name=name,
                theorems=theorems,
                lemmas=lemmas,
                dependencies=dependencies,
            )
        except Exception:
            health = _clamp(len(theorems) / max(len(theorems) + len(lemmas), 1))
            ecology = TheoremEcology(
                ecology_id=f"{name}_{uuid.uuid4().hex[:8]}",
                name=name,
                theorems=theorems,
                lemmas=lemmas,
                health_score=health,
                diversity_index=_clamp(len(set(lemmas)) / max(len(lemmas), 1)),
            )

        # Step 2 – lemma portfolio.
        lm_portfolio: LemmaPortfolio | None = None
        try:
            lm_portfolio = self._portfolio_manager.create_portfolio(
                portfolio_id=f"lp_{name}",
                lemmas=lemmas,
                dependencies=dependencies,
            )
            if self._portfolio_optimizer is not None:
                lm_portfolio = self._portfolio_optimizer.optimize(lm_portfolio)
        except Exception:
            pass

        # Step 3 – compounding effects.
        compounding_effects: list[CompoundingEffect] = []
        try:
            compounding_effects = self._compounding_engine.compute(ecology)
        except Exception:
            pass

        # Step 4 – generate ideas.
        raw_ideas = self._idea_generator.generate_from_ecology(
            ecology, purpose=purpose, count=8
        )
        compound_ideas = self._idea_generator.generate_from_compounding(
            compounding_effects, purpose=purpose, area=target_area
        )
        all_ideas = raw_ideas + compound_ideas

        # Step 5 – novelty scoring.
        novelty_scores = self._novelty_scorer.score_batch([ecology])
        self._novelty_scorer.update_portfolio_from_ecology(ecology)

        # Step 6 – trust filtering.
        filtered = self._trust_filter.filter([ecology])
        trust_cert = self._trust_filter.trust_certificate(ecology)

        # Build output portfolio.
        out_portfolio = IdeaPortfolio()
        for idea in all_ideas:
            self._linker.link_idea_to_ecology(idea, ecology)
            self._idea_history.record(
                idea_id=idea.idea_id,
                event="pipeline_generated",
                status=idea.trust_status,
                notes=f"Generated in pipeline '{name}'.",
            )
            try:
                out_portfolio.add(idea)
            except Exception:
                pass

        elapsed = time.monotonic() - t_start
        return {
            "ecology": ecology,
            "lemma_portfolio": lm_portfolio,
            "ideas": all_ideas,
            "idea_portfolio": out_portfolio,
            "compounding_effects": compounding_effects,
            "novelty_scores": [s.to_dict() if hasattr(s, "to_dict") else vars(s) for s in novelty_scores],
            "trust_certificate": trust_cert,
            "filtered_ecologies": filtered,
            "elapsed_seconds": elapsed,
            "pipeline_name": name,
            "purpose": purpose,
            "target_area": target_area,
        }

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_idea_portfolio(
        self, portfolio: "IdeaPortfolio"
    ) -> "list[TheoremEcology]":
        """Convert each idea in *portfolio* into a :class:`TheoremEcology`."""
        ecologies: list[TheoremEcology] = []
        for idea in portfolio.rank():
            try:
                eco = self._linker.create_ecology_from_idea(idea)
                self._linker.link_idea_to_ecology(idea, eco)
                ecologies.append(eco)
            except Exception:
                pass
        return ecologies

    def generate_idea_pipeline(
        self,
        ecology: "TheoremEcology",
        purpose: str,
        count: int = 10,
    ) -> "IdeaPortfolio":
        """Generate ideas from *ecology*, evaluate them, filter by trust.

        Returns a ranked :class:`IdeaPortfolio`.
        """
        ideas = self._idea_generator.generate_from_ecology(ecology, purpose, count)
        portfolio = IdeaPortfolio()
        for idea in ideas:
            enriched = self._linker.enrich_idea_with_ecology(idea, ecology)
            try:
                portfolio.add(enriched)
            except Exception:
                pass

        # Filter by trust via TrustEcologyFilter.
        min_trust_status = TrustStatus.PROVISIONAL
        filtered_portfolio = self._trust_filter.filter_portfolio_ideas(
            portfolio, min_trust_status
        )
        return filtered_portfolio

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def trust_filtered_analysis(
        self,
        ecologies: "list[TheoremEcology]",
        min_trust: "TrustLevel",
    ) -> "dict[str, Any]":
        """Filter *ecologies* by *min_trust* and analyse the survivors."""
        self._trust_filter.min_trust = min_trust
        passing = self._trust_filter.filter(ecologies)
        novelty_scores = self._novelty_scorer.score_batch(passing)
        certs = [self._trust_filter.trust_certificate(e) for e in passing]
        gaps = self._novelty_scorer.portfolio_gap_analysis(passing)
        return {
            "total_input": len(ecologies),
            "passing": len(passing),
            "filtered_ecologies": passing,
            "novelty_scores": novelty_scores,
            "trust_certificates": certs,
            "knowledge_gaps": gaps,
            "min_trust": getattr(min_trust, "name", str(min_trust)),
        }

    def novelty_driven_exploration(
        self,
        seed_ecologies: "list[TheoremEcology]",
        expansion_budget: int = 5,
    ) -> "list[TheoremEcology]":
        """Use novelty scores to guide exploration by expanding the most novel ecologies.

        For each of the top *expansion_budget* novel ecologies, generates child
        ecologies by mutating their theorem sets.
        """
        ranked = self._novelty_scorer.rank_by_novelty(seed_ecologies)
        top = [eco for eco, _ in ranked[:expansion_budget]]
        new_ecologies: list[TheoremEcology] = list(seed_ecologies)

        for eco in top:
            theorems = list(getattr(eco, "theorems", []))
            for extra_tag in _ecology_to_novelty_tags(eco)[:3]:
                child_id = f"child_{getattr(eco, 'ecology_id', 'eco')}_{extra_tag}"
                child_theorems = theorems + [f"thm_{extra_tag}_{child_id[:8]}"]
                try:
                    child = self._ecology_manager.create(
                        ecology_id=child_id,
                        name=f"Child of {getattr(eco, 'name', eco)} ({extra_tag})",
                        theorems=child_theorems,
                        lemmas=list(getattr(eco, "lemmas", [])),
                    )
                except Exception:
                    child = TheoremEcology(
                        ecology_id=child_id,
                        name=f"Child of {getattr(eco, 'name', str(eco))} ({extra_tag})",
                        theorems=child_theorems,
                        lemmas=list(getattr(eco, "lemmas", [])),
                        health_score=_clamp(getattr(eco, "health_score", 0.5) * 0.9),
                        diversity_index=_clamp(
                            getattr(eco, "diversity_index", 0.3) + 0.05
                        ),
                    )
                new_ecologies.append(child)
                self._novelty_scorer.update_portfolio_from_ecology(child)

        return new_ecologies

    def portfolio_idea_coevolution(
        self,
        portfolio: "IdeaPortfolio",
        lemma_portfolio: "LemmaPortfolio",
        iterations: int = 5,
    ) -> "dict[str, Any]":
        """Iteratively co-evolve an idea portfolio and a lemma portfolio.

        Each iteration:
        1. Convert ideas → ecologies.
        2. Compute compounding effects for each ecology.
        3. Generate new ideas from compounding effects.
        4. Add new ideas to the portfolio.
        5. Update lemma utilities from idea gain profiles.

        Returns a history of portfolio sizes and final state.
        """
        history: list[dict[str, Any]] = []
        current_portfolio = portfolio

        for iteration in range(iterations):
            ecologies = self.ingest_idea_portfolio(current_portfolio)

            # Compute compounding and generate ideas.
            new_ideas: list[Idea] = []
            for eco in ecologies[:5]:
                try:
                    effects = self._compounding_engine.compute(eco)
                    ideas = self._idea_generator.generate_from_compounding(
                        effects, purpose="coevolution", area="theorem_ecology"
                    )
                    new_ideas.extend(ideas)
                except Exception:
                    new_ideas.extend(
                        self._idea_generator.generate_from_ecology(
                            eco, purpose="coevolution", count=2
                        )
                    )

            # Add new ideas to portfolio.
            for idea in new_ideas:
                try:
                    current_portfolio.add(idea)
                except Exception:
                    pass

            # Update lemma utilities from idea gain profiles.
            for idea in current_portfolio.rank()[:10]:
                gain = idea.predicted_gain
                try:
                    self._portfolio_manager.update_utility(
                        lemma_portfolio,
                        idea.idea_id,
                        gain.composite_value(),
                    )
                except Exception:
                    pass

            history.append(
                {
                    "iteration": iteration,
                    "portfolio_size": len(current_portfolio.rank()),
                    "new_ideas": len(new_ideas),
                    "ecologies": len(ecologies),
                }
            )

        return {
            "final_portfolio": current_portfolio,
            "lemma_portfolio": lemma_portfolio,
            "history": history,
            "iterations": iterations,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def full_report(
        self, ecology_id: str, portfolio_id: "str | None" = None
    ) -> str:
        """Return a multi-line text report for *ecology_id*.

        Combines ecology health, portfolio statistics, compounding summary,
        and idea generation results into a human-readable report.
        """
        lines: list[str] = [
            "=" * 72,
            f"Integrated Ecology Report",
            f"  Ecology ID  : {ecology_id}",
            f"  Portfolio ID: {portfolio_id or 'N/A'}",
            f"  Generated   : {_now_iso()}",
            "=" * 72,
        ]

        # Ecology section.
        try:
            eco = self._ecology_manager.get(ecology_id)
            health = getattr(eco, "health_score", 0.0)
            diversity = getattr(eco, "diversity_index", 0.0)
            n_th = len(getattr(eco, "theorems", []))
            n_lm = len(getattr(eco, "lemmas", []))
            lines += [
                "",
                "── Ecology ─────────────────────────────────────────────────────",
                f"  Name        : {getattr(eco, 'name', ecology_id)}",
                f"  Health      : {health:.3f}",
                f"  Diversity   : {diversity:.3f}",
                f"  Theorems    : {n_th}",
                f"  Lemmas      : {n_lm}",
            ]
            trust_level = self._trust_filter.assign_trust(eco)
            cert = self._trust_filter.trust_certificate(eco)
            lines += [
                f"  Trust Level : {getattr(trust_level, 'name', str(trust_level))}",
                f"  Rationale   : {cert.get('rationale', 'N/A')}",
            ]
        except Exception as exc:
            lines.append(f"  [Ecology not found: {exc}]")

        # Novelty section.
        try:
            eco = self._ecology_manager.get(ecology_id)
            ns = self._novelty_scorer.score_ecology(eco)
            lines += [
                "",
                "── Novelty ──────────────────────────────────────────────────────",
                f"  Composite Score    : {ns.composite:.3f}",
                f"  Semantic Distance  : {ns.semantic_distance:.3f}",
                f"  Purpose Alignment  : {ns.purpose_alignment:.3f}",
                f"  Feasibility        : {ns.feasibility:.3f}",
                f"  Explanation        : {ns.explanation}",
            ]
        except Exception:
            lines.append("  [Novelty data unavailable]")

        # Diagnostics.
        if self._diagnostics is not None:
            try:
                diag = self._diagnostics.summary(ecology_id)
                lines += [
                    "",
                    "── Diagnostics ──────────────────────────────────────────────",
                    f"  {diag}",
                ]
            except Exception:
                pass

        lines += ["", "=" * 72]
        return "\n".join(lines)

    def diagnostics(self) -> "dict[str, Any]":
        """Return overall system diagnostics as a dictionary."""
        diag: dict[str, Any] = {
            "timestamp": _now_iso(),
            "components": {
                "ecology_manager": type(self._ecology_manager).__name__,
                "portfolio_manager": type(self._portfolio_manager).__name__,
                "compounding_engine": type(self._compounding_engine).__name__,
                "portfolio_optimizer": (
                    type(self._portfolio_optimizer).__name__
                    if self._portfolio_optimizer is not None
                    else "N/A"
                ),
                "linker": type(self._linker).__name__,
                "trust_filter": type(self._trust_filter).__name__,
                "novelty_scorer": type(self._novelty_scorer).__name__,
                "idea_generator": type(self._idea_generator).__name__,
                "diagnostics": (
                    type(self._diagnostics).__name__
                    if self._diagnostics is not None
                    else "N/A"
                ),
            },
        }
        if self._diagnostics is not None:
            try:
                diag["ecology_diagnostics"] = self._diagnostics.summary("pipeline")
            except Exception:
                pass
        return diag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Helpers
    "_clamp",
    "_now_iso",
    "_tokenize",
    "_jaccard",
    "_idea_to_ecology_id",
    "_trust_to_health",
    "_ecology_to_novelty_tags",
    "_trust_status_weight",
    "_gain_from_ecology",
    "_validation_from_ecology",
    # Classes
    "IdeaEcologyLinker",
    "TrustEcologyFilter",
    "NoveltyEcologyScorer",
    "EcologyIdeaGenerator",
    "IntegratedEcologyPipeline",
]
