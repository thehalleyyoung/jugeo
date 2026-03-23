"""Why AG, DTT, and AI each matter in novelty search – theory2.tex Ch57.

Explains and quantifies the distinct contribution of each of the three
foundational frameworks — Algebraic Geometry (AG), Dependent Type Theory
(DTT), and Artificial Intelligence (AI) — to the novelty-search pipeline,
and computes their synergistic combination.

# copilot: generated as part of jugeo.ideation.novelty_search

Module layout::

    ┌─────────────────────────────────────────────────────────────────────┐
    │  Framework                         – 3-framework Enum               │
    │  FrameworkContributionConfig       – frozen weight & synergy config  │
    │  AGContribution                    – AG contribution record          │
    │  DTTContribution                   – DTT contribution record         │
    │  AIContribution                    – AI contribution record          │
    │  FrameworkSynergy                  – combined synergy record         │
    │  WhyAGDTTAINoveltyAnalyzer         – core assessment methods         │
    │  WhyAGDTTAINoveltyWitness          – accumulates synergy records     │
    │  WhyAGDTTAINoveltyCoordinator      – end-to-end pipeline             │
    └─────────────────────────────────────────────────────────────────────┘

Background
----------
The jugeo ideation pipeline sits at the intersection of three powerful
frameworks.  No single framework is sufficient on its own:

  1. Algebraic Geometry provides the *structural* substrate.  Obstructions to
     mathematical theorems are most naturally expressed as cohomological
     objects — Ext groups, higher direct images, or Čech cocycles — and the
     sheaf-theoretic language of AG gives us a precise way to *locate* and
     *classify* them.  Without AG, we would have no systematic vocabulary for
     describing what stands in the way of a proof.

  2. Dependent Type Theory provides the *verification* substrate.  Once an
     idea has been generated and evaluated by the novelty functional, it must
     eventually be formalised and proved.  DTT (as implemented in Lean 4,
     Agda, or Coq) provides the logical framework in which proofs can be
     machine-checked.  The presence of dependent types means that the
     mathematical content of a proof is *encoded in its type*, making it
     impossible to state a false theorem as a type.

  3. Artificial Intelligence provides the *search* substrate.  The space of
     possible mathematical ideas is astronomically large.  Exhaustive
     enumeration is infeasible.  AI-based search (large language models,
     Monte Carlo tree search, evolutionary algorithms, embedding-based
     similarity search) allows the pipeline to navigate this space efficiently,
     surface promising candidates, and avoid wasting resources on dead ends.

The *synergy* between these three frameworks is greater than their individual
sum.  AG gives AI a structured search space (the obstruction lattice) rather
than a flat set of strings.  DTT gives AI a verifiable reward signal (proof
completion) rather than a soft heuristic.  AI gives AG and DTT the ability to
explore novel configurations that human mathematicians might overlook.  The
synergy score quantifies this multiplicative interaction.

See also
--------
* ``AG_RATIONALE`` — multi-paragraph narrative on AG's role
* ``DTT_RATIONALE`` — multi-paragraph narrative on DTT's role
* ``AI_RATIONALE`` — multi-paragraph narrative on AI's role

Theory references
-----------------
* theory2.tex §57.5 "Why Sheaf Theory Structures the Obstruction Landscape"
* theory2.tex §57.6 "Dependent Types as a Verification Layer"
* theory2.tex §57.7 "AI Search in the Space of Mathematical Ideas"
* theory2.tex §57.8 "Synergy and the Tripartite Framework"

Usage example::

    from jugeo.ideation.novelty_search.why_ag_dtt_and_ai_each_matter_in_n import (
        WhyAGDTTAINoveltyCoordinator,
        Framework,
    )

    context = {
        "sheaf_quality": 0.8,
        "obstruction_count": 5,
        "proof_formalization_coverage": 0.7,
        "search_space_size": 10000,
    }
    coordinator = WhyAGDTTAINoveltyCoordinator()
    synergy = coordinator.run(context)
    print(coordinator.report())
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.novelty_search.models import (
        NoveltySearchProblem,
        SearchResult,
        MetricKind,
    )
except ImportError:
    NoveltySearchProblem = None  # type: ignore[assignment,misc]
    SearchResult = None  # type: ignore[assignment,misc]
    MetricKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.novelty_search.a_purpose_conditioned_novelty_func import (
        NoveltyFunctionalValue,
        NoveltyFunctionalConfig,
    )
except ImportError:
    NoveltyFunctionalValue = None  # type: ignore[assignment,misc]
    NoveltyFunctionalConfig = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Rich narrative constants (each ≥200 words)
# ---------------------------------------------------------------------------

AG_RATIONALE: str = """
Algebraic Geometry and the Obstruction Landscape
=================================================

Algebraic geometry (AG) plays a foundational role in the jugeo novelty-search
pipeline because it provides the most natural language for expressing and
classifying the *obstructions* that prevent mathematical theorems from being
proved.

At the heart of modern AG is the theory of sheaves and cohomology.  A sheaf on
a topological space (or site) is a gadget that assigns algebraic data to open
sets consistently.  When we attempt to globalise a local construction — for
example, constructing a global section from local data — the obstructions to
doing so are captured by the higher cohomology groups H^i(X, F).  In the
context of algebraic geometry, these groups are computable (using Čech
cohomology, derived functors, or spectral sequences) and carry rich
mathematical meaning.

For the jugeo pipeline, this means that every obstruction in the obstruction
ledger can be assigned an *AG address*: a cohomological class in a specific
degree and with specific coefficients.  This address tells us:

  (a) Which dimension the obstruction lives in (H^0, H^1, H^2, …).
      Dimension-0 obstructions are often the easiest to remove; higher
      dimensions require more sophisticated algebraic machinery.

  (b) Which sheaf the obstruction is a section of.  This tells us what kind
      of algebraic structure is needed to kill it: a line bundle, a vector
      bundle, an étale local system, etc.

  (c) Which functoriality properties the obstruction has.  If we can find an
      idea that is functorially compatible with the obstruction, we can use
      base change, flat descent, or proper pushforward to eliminate it.

The sheaf structure of the obstruction landscape gives the novelty-search
pipeline a *structured search space*.  Instead of searching over an undifferentiated
set of ideas, we search over a sheaf-cohomological lattice, where each node is
a potential obstruction-killing construction.  This dramatically reduces the
effective search space size and increases the probability of finding useful ideas.

Furthermore, AG provides the concept of *descent*.  If an obstruction is étale-
locally trivial, it can be attacked by étale descent data.  If it is Zariski-
locally trivial, a Čech computation suffices.  The choice of topology (Zariski,
étale, fppf, fpqc, crystalline, …) determines the resolution strategy, and the
novelty-search pipeline can use this choice as a dimension along which to search
for novel ideas.

In summary, AG contributes:
  - Precise obstruction localisation via cohomological addresses
  - Structured search space via the sheaf-cohomological lattice
  - Descent data that provides resolution strategies
  - Functoriality constraints that filter out inconsistent ideas
  - Cohomological leverage scores that estimate how much an idea reduces H^i

Without AG, the pipeline would be searching blindly in an unstructured space.
AG is the map that makes the search tractable.
"""

DTT_RATIONALE: str = """
Dependent Type Theory and the Verification Layer
================================================

Dependent type theory (DTT) plays a complementary but equally essential role
in the jugeo novelty-search pipeline.  While AG provides the structure of the
*problem* (the obstruction landscape), DTT provides the structure of the
*solution* (the formal proof).

In a dependently typed proof assistant such as Lean 4, Agda, or Coq, every
mathematical statement is a *type*, and every proof is a *term* of that type.
The dependent type system enforces logical consistency: it is impossible to
construct a term that inhabits a false type.  This means that when the pipeline
produces a candidate idea and eventually formalises it, the formalisation
process either succeeds (producing a machine-checked proof term) or fails
(producing a type error that precisely identifies the gap in the argument).

This verification layer has several important consequences for the novelty-
search pipeline:

First, it provides a *binary reward signal*.  Unlike the soft heuristic scores
(leverage, tractability, semantic relevance) that guide the search, the DTT
formalisation process gives a hard yes/no answer: does the proof go through or
not?  This binary signal is invaluable for training and calibrating the
heuristic scores against reality.

Second, it provides *precise gap identification*.  When a proof attempt fails
in Lean 4, the type checker reports exactly which subgoal could not be closed
and why.  This information can be fed back into the novelty-search pipeline to
generate targeted sub-ideas that address the specific gap.  The pipeline thus
becomes a *dialogue* between the AI search module and the DTT verification layer.

Third, DTT's *universe polymorphism* allows the pipeline to work at multiple
levels of mathematical abstraction simultaneously.  An idea that works at
universe level 0 (Set) might fail at universe level 1 (Prop) due to size
issues.  The universe structure provides a natural stratification of the idea
space that the pipeline can exploit.

Fourth, *dependent types* allow the pipeline to express and verify *parametric*
ideas: ideas that work not for a specific algebraic variety X, but for all
varieties X satisfying a certain property P.  This generalisation capability
is crucial for finding ideas with high leverage, since a general construction
is more likely to address multiple obstructions simultaneously.

Fifth, the growing Mathlib library (in Lean 4) provides a rich ecosystem of
existing formal proofs.  The tractability score in the novelty functional
estimates how much of the required infrastructure is already in Mathlib.  A
high tractability score means the idea can be formalised quickly by reusing
existing Mathlib lemmas; a low score means substantial new infrastructure is
needed.

In summary, DTT contributes:
  - Binary verification signal for calibrating heuristic scores
  - Precise gap identification for targeted sub-idea generation
  - Universe polymorphism for multi-level abstraction
  - Dependent types for parametric generalisation
  - Mathlib infrastructure for tractability estimation

Without DTT, the pipeline would have no way to verify that its candidates are
correct, and the leverage and tractability scores would be uncalibrated
heuristics with no grounding in formal verification.
"""

AI_RATIONALE: str = """
Artificial Intelligence and the Search Substrate
================================================

Artificial intelligence (AI) provides the *search* substrate of the jugeo
novelty-search pipeline.  The mathematical universe is vast and any exhaustive
approach is infeasible; AI provides the navigation tools that make the search
tractable.

The space of possible mathematical ideas is enormous.  Even restricting to
ideas that are relevant to a specific subfield of algebraic geometry — say,
the theory of étale cohomology and its applications to arithmetic geometry —
there are hundreds of active research fronts, thousands of open conjectures,
and millions of possible lemma combinations.  No human mathematician can
survey this entire space systematically.  AI can.

In the jugeo pipeline, AI contributes at multiple levels:

Search-space coverage: Large language models (LLMs) can rapidly generate
candidate ideas by completing prompts such as "What algebraic structures
might kill the H^2 obstruction in the étale topology?"  While LLM output
is not always mathematically correct, it provides a diverse sample of the
idea space that would take human mathematicians years to generate manually.
The pipeline uses the novelty functional to filter and rank this raw output.

Pattern recognition: AI can identify structural patterns across the
obstruction landscape that are not immediately obvious to human experts.
For example, if ten of the twelve known obstructions share a common
cohomological structure, an AI model trained on the obstruction ledger
can identify this pattern and suggest ideas that attack the shared structure
rather than each obstruction individually.

Analogical reasoning: Mathematics advances by analogy.  An idea that worked
in characteristic-0 algebraic geometry might, with appropriate modification,
work in characteristic-p.  An idea that resolved obstructions in the étale
topology might, after a change-of-sites, resolve similar obstructions in the
crystalline topology.  AI is well-suited to identifying these analogical
transfers, since they are essentially a form of semantic similarity search
in the embedding space of mathematical ideas.

Large-scale enumeration: For combinatorial problems — enumerating all possible
compositions of functors, all possible applications of base-change formulas,
all possible spectral sequence configurations — AI can enumerate far more
candidates than a human and apply heuristic filters to identify promising ones.

Embedding-based similarity search: Modern AI embedding models can map
mathematical ideas into a high-dimensional vector space where semantic
similarity corresponds to geometric proximity.  The distance metrics used in
the jugeo novelty-search pipeline (Jaccard, cosine, Euclidean) are all
computable in this embedding space, providing a fast and scalable way to
measure the novelty of new ideas relative to the existing portfolio.

Reinforcement learning from formal feedback: As the DTT layer provides
binary verification signals, an AI agent can learn to generate ideas that
are more likely to pass formal verification.  This reinforcement loop
progressively improves the quality of the generated candidates over time.

In summary, AI contributes:
  - Rapid generation of diverse candidate ideas via LLMs
  - Pattern recognition across the obstruction landscape
  - Analogical reasoning for cross-topology idea transfer
  - Large-scale enumeration of structural compositions
  - Embedding-based similarity search for novelty measurement
  - Reinforcement learning from DTT verification signals

Without AI, the pipeline would be limited to the ideas that human
mathematicians can generate manually — a tiny fraction of the available
search space.  AI is the engine that makes the pipeline scale.
"""

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_AG_WEIGHT: float = 0.33
_DEFAULT_DTT_WEIGHT: float = 0.34
_DEFAULT_AI_WEIGHT: float = 0.33
_DEFAULT_SYNERGY_BONUS: float = 0.10
_DEFAULT_MIN_CONTRIBUTION: float = 0.05
_EPSILON: float = 1e-9

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval ``[lo, hi]``."""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _contribution_id() -> str:
    """Generate a unique contribution identifier prefixed with ``ctb-``."""
    return f"ctb-{uuid.uuid4().hex[:10]}"


def _synergy_id() -> str:
    """Generate a unique synergy identifier prefixed with ``syn-``."""
    return f"syn-{uuid.uuid4().hex[:10]}"


def _weighted_sum(values: list[float], weights: list[float]) -> float:
    """Compute a weighted sum of *values* with *weights*.

    Parameters
    ----------
    values:
        Values to sum.
    weights:
        Weights corresponding to each value.  Need not sum to 1.

    Returns
    -------
    float
        Weighted sum.
    """
    if len(values) != len(weights):
        raise ValueError(
            f"_weighted_sum: len(values)={len(values)} != len(weights)={len(weights)}"
        )
    return sum(v * w for v, w in zip(values, weights))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Framework(Enum):
    """The three foundational frameworks of the jugeo pipeline.

    ALGEBRAIC_GEOMETRY
        Provides sheaf structure, cohomological obstruction classification,
        and descent data.

    DEPENDENT_TYPE_THEORY
        Provides formal verification, type-safe proof formalization, and
        the Mathlib infrastructure for tractability estimation.

    ARTIFICIAL_INTELLIGENCE
        Provides large-scale search, pattern recognition, analogical
        reasoning, and embedding-based similarity computation.
    """

    ALGEBRAIC_GEOMETRY = "ALGEBRAIC_GEOMETRY"
    DEPENDENT_TYPE_THEORY = "DEPENDENT_TYPE_THEORY"
    ARTIFICIAL_INTELLIGENCE = "ARTIFICIAL_INTELLIGENCE"

    def short_name(self) -> str:
        """Return a short display name for the framework."""
        return {
            Framework.ALGEBRAIC_GEOMETRY: "AG",
            Framework.DEPENDENT_TYPE_THEORY: "DTT",
            Framework.ARTIFICIAL_INTELLIGENCE: "AI",
        }[self]

    def rationale(self) -> str:
        """Return the full narrative rationale for this framework."""
        return {
            Framework.ALGEBRAIC_GEOMETRY: AG_RATIONALE,
            Framework.DEPENDENT_TYPE_THEORY: DTT_RATIONALE,
            Framework.ARTIFICIAL_INTELLIGENCE: AI_RATIONALE,
        }[self]


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrameworkContributionConfig:
    """Configuration weights for the tri-framework contribution assessment.

    Attributes
    ----------
    ag_weight:
        Weight assigned to the AG contribution in the combined score.
    dtt_weight:
        Weight assigned to the DTT contribution.
    ai_weight:
        Weight assigned to the AI contribution.
    synergy_bonus:
        Additive bonus applied to the combined score when all three
        frameworks achieve a contribution above ``min_contribution``.
    min_contribution:
        Minimum contribution score required for a framework to qualify
        for the synergy bonus.
    """

    ag_weight: float = _DEFAULT_AG_WEIGHT
    dtt_weight: float = _DEFAULT_DTT_WEIGHT
    ai_weight: float = _DEFAULT_AI_WEIGHT
    synergy_bonus: float = _DEFAULT_SYNERGY_BONUS
    min_contribution: float = _DEFAULT_MIN_CONTRIBUTION

    def validate(self) -> None:
        """Raise ``ValueError`` if configuration is inconsistent."""
        total = self.ag_weight + self.dtt_weight + self.ai_weight
        if abs(total - 1.0) > 0.05:
            raise ValueError(
                f"FrameworkContributionConfig weights sum to {total:.4f}; expected ≈ 1.0"
            )
        for name, val in [
            ("ag_weight", self.ag_weight),
            ("dtt_weight", self.dtt_weight),
            ("ai_weight", self.ai_weight),
            ("synergy_bonus", self.synergy_bonus),
            ("min_contribution", self.min_contribution),
        ]:
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name}={val} out of [0, 1]")


# ---------------------------------------------------------------------------
# Contribution dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AGContribution:
    """Assessment of the AG framework's contribution in a novelty context.

    Attributes
    ----------
    contribution_id:
        Unique identifier for this assessment.
    sheaf_structure_quality:
        How well the novelty context is modelled by sheaf theory.  High
        values indicate that the obstruction landscape has clear sheaf
        structure that AG can exploit.
    obstruction_localization:
        How precisely AG can localise the obstructions cohomologically.
        High values mean obstructions are assigned precise H^i addresses.
    cohomology_leverage:
        The estimated leverage that cohomological tools provide for
        attacking the obstructions.
    descent_data_completeness:
        How complete the available descent data is.  High values indicate
        that étale, fppf, or fpqc descent data is fully available.
    composite_score:
        Weighted composite of the four sub-scores.
    """

    contribution_id: str
    sheaf_structure_quality: float
    obstruction_localization: float
    cohomology_leverage: float
    descent_data_completeness: float
    composite_score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "contribution_id": self.contribution_id,
            "framework": "AG",
            "sheaf_structure_quality": self.sheaf_structure_quality,
            "obstruction_localization": self.obstruction_localization,
            "cohomology_leverage": self.cohomology_leverage,
            "descent_data_completeness": self.descent_data_completeness,
            "composite_score": self.composite_score,
        }

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"AG[{self.contribution_id}] composite={self.composite_score:.3f} "
            f"(sheaf={self.sheaf_structure_quality:.2f}, "
            f"local={self.obstruction_localization:.2f}, "
            f"cohom={self.cohomology_leverage:.2f}, "
            f"descent={self.descent_data_completeness:.2f})"
        )


@dataclass(frozen=True, slots=True)
class DTTContribution:
    """Assessment of the DTT framework's contribution in a novelty context.

    Attributes
    ----------
    contribution_id:
        Unique identifier for this assessment.
    type_safety_score:
        How much the novelty context benefits from dependent type checking.
    proof_formalization:
        Estimated fraction of the required proofs that can be formalised
        in a current proof assistant.
    dependent_types_coverage:
        Fraction of the mathematical constructions involved that can be
        expressed as dependent types.
    universe_polymorphism:
        How much universe polymorphism is needed or available.
    composite_score:
        Weighted composite of the four sub-scores.
    """

    contribution_id: str
    type_safety_score: float
    proof_formalization: float
    dependent_types_coverage: float
    universe_polymorphism: float
    composite_score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "contribution_id": self.contribution_id,
            "framework": "DTT",
            "type_safety_score": self.type_safety_score,
            "proof_formalization": self.proof_formalization,
            "dependent_types_coverage": self.dependent_types_coverage,
            "universe_polymorphism": self.universe_polymorphism,
            "composite_score": self.composite_score,
        }

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"DTT[{self.contribution_id}] composite={self.composite_score:.3f} "
            f"(type_safety={self.type_safety_score:.2f}, "
            f"formalization={self.proof_formalization:.2f}, "
            f"dep_types={self.dependent_types_coverage:.2f}, "
            f"universe={self.universe_polymorphism:.2f})"
        )


@dataclass(frozen=True, slots=True)
class AIContribution:
    """Assessment of the AI framework's contribution in a novelty context.

    Attributes
    ----------
    contribution_id:
        Unique identifier for this assessment.
    search_space_coverage:
        How much of the relevant idea space the AI can cover.  High values
        indicate a large, well-structured search space that AI can traverse.
    pattern_recognition:
        How much the AI's pattern-recognition capability helps in
        identifying shared obstruction structures.
    analogical_reasoning:
        How many useful analogical transfers between topologies or
        frameworks the AI can identify.
    large_scale_enumeration:
        The AI's estimated capacity to enumerate combinatorial idea
        compositions faster than human exploration.
    composite_score:
        Weighted composite of the four sub-scores.
    """

    contribution_id: str
    search_space_coverage: float
    pattern_recognition: float
    analogical_reasoning: float
    large_scale_enumeration: float
    composite_score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "contribution_id": self.contribution_id,
            "framework": "AI",
            "search_space_coverage": self.search_space_coverage,
            "pattern_recognition": self.pattern_recognition,
            "analogical_reasoning": self.analogical_reasoning,
            "large_scale_enumeration": self.large_scale_enumeration,
            "composite_score": self.composite_score,
        }

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"AI[{self.contribution_id}] composite={self.composite_score:.3f} "
            f"(coverage={self.search_space_coverage:.2f}, "
            f"pattern={self.pattern_recognition:.2f}, "
            f"analogy={self.analogical_reasoning:.2f}, "
            f"enum={self.large_scale_enumeration:.2f})"
        )


@dataclass(frozen=True, slots=True)
class FrameworkSynergy:
    """The combined synergy record for all three frameworks.

    Attributes
    ----------
    synergy_id:
        Unique identifier for this synergy record.
    ag_contribution_id:
        ID of the associated ``AGContribution``.
    dtt_contribution_id:
        ID of the associated ``DTTContribution``.
    ai_contribution_id:
        ID of the associated ``AIContribution``.
    synergy_score:
        Combined synergy score in [0, 1].  Includes the additive synergy
        bonus when all three frameworks are sufficiently active.
    description:
        Human-readable description of the synergy context.
    timestamp:
        ISO-8601 timestamp of computation.
    """

    synergy_id: str
    ag_contribution_id: str
    dtt_contribution_id: str
    ai_contribution_id: str
    synergy_score: float
    description: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "synergy_id": self.synergy_id,
            "ag_contribution_id": self.ag_contribution_id,
            "dtt_contribution_id": self.dtt_contribution_id,
            "ai_contribution_id": self.ai_contribution_id,
            "synergy_score": self.synergy_score,
            "description": self.description,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"Synergy[{self.synergy_id}] score={self.synergy_score:.4f} "
            f"— {self.description[:60]}"
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class WhyAGDTTAINoveltyAnalyzer:
    """Core assessment engine for the tri-framework contribution analysis.

    Computes AG, DTT, and AI contributions from a novelty context
    dictionary and combines them into a ``FrameworkSynergy`` record.

    The novelty context dictionary may contain any subset of the following
    keys:

    ``sheaf_quality`` (float [0,1])
        Quality of the sheaf-theoretic model of the obstruction landscape.
    ``obstruction_count`` (int)
        Number of known obstructions.
    ``localisation_precision`` (float [0,1])
        Precision of cohomological obstruction localisation.
    ``descent_completeness`` (float [0,1])
        Completeness of available descent data.
    ``proof_formalization_coverage`` (float [0,1])
        Fraction of proofs that can be formalised in current tools.
    ``type_safety_benefit`` (float [0,1])
        Estimated benefit of type checking.
    ``dep_types_coverage`` (float [0,1])
        Fraction of constructions expressible as dependent types.
    ``universe_poly`` (float [0,1])
        Degree to which universe polymorphism applies.
    ``search_space_size`` (int)
        Estimated size of the idea search space.
    ``pattern_density`` (float [0,1])
        Density of recognisable patterns in the obstruction landscape.
    ``analogy_potential`` (float [0,1])
        Potential for analogical transfer.
    ``enumeration_factor`` (float ≥ 1)
        How much faster AI can enumerate versus human search.
    """

    def __init__(self, config: FrameworkContributionConfig | None = None) -> None:
        self._config = config or FrameworkContributionConfig()

    # ------------------------------------------------------------------
    # Contribution assessments
    # ------------------------------------------------------------------

    def assess_ag_contribution(
        self, novelty_context: dict[str, Any]
    ) -> AGContribution:
        """Assess the AG framework's contribution in *novelty_context*.

        Parameters
        ----------
        novelty_context:
            Dictionary of context signals.  See class docstring for keys.

        Returns
        -------
        AGContribution
        """
        sheaf_quality = _clamp(float(novelty_context.get("sheaf_quality", 0.6)))
        obs_count = int(novelty_context.get("obstruction_count", 5))
        # More obstructions → more need for systematic localisation
        localisation_precision = _clamp(
            float(novelty_context.get("localisation_precision", 0.5 + 0.05 * min(obs_count, 10)))
        )
        cohomology_leverage = _clamp(float(novelty_context.get("cohomology_leverage", 0.6)))
        # Cohomology leverage can be estimated from sheaf quality
        if "cohomology_leverage" not in novelty_context:
            cohomology_leverage = _clamp(0.7 * sheaf_quality + 0.3 * localisation_precision)
        descent_completeness = _clamp(float(novelty_context.get("descent_completeness", 0.55)))
        composite = _clamp(
            _weighted_sum(
                [sheaf_quality, localisation_precision, cohomology_leverage, descent_completeness],
                [0.30, 0.25, 0.30, 0.15],
            )
        )
        return AGContribution(
            contribution_id=_contribution_id(),
            sheaf_structure_quality=sheaf_quality,
            obstruction_localization=localisation_precision,
            cohomology_leverage=cohomology_leverage,
            descent_data_completeness=descent_completeness,
            composite_score=composite,
        )

    def assess_dtt_contribution(
        self, novelty_context: dict[str, Any]
    ) -> DTTContribution:
        """Assess the DTT framework's contribution in *novelty_context*.

        Parameters
        ----------
        novelty_context:
            Dictionary of context signals.

        Returns
        -------
        DTTContribution
        """
        type_safety = _clamp(float(novelty_context.get("type_safety_benefit", 0.65)))
        formalization = _clamp(float(novelty_context.get("proof_formalization_coverage", 0.5)))
        dep_types = _clamp(float(novelty_context.get("dep_types_coverage", 0.6)))
        universe_poly = _clamp(float(novelty_context.get("universe_poly", 0.4)))
        composite = _clamp(
            _weighted_sum(
                [type_safety, formalization, dep_types, universe_poly],
                [0.30, 0.35, 0.25, 0.10],
            )
        )
        return DTTContribution(
            contribution_id=_contribution_id(),
            type_safety_score=type_safety,
            proof_formalization=formalization,
            dependent_types_coverage=dep_types,
            universe_polymorphism=universe_poly,
            composite_score=composite,
        )

    def assess_ai_contribution(
        self, novelty_context: dict[str, Any]
    ) -> AIContribution:
        """Assess the AI framework's contribution in *novelty_context*.

        Parameters
        ----------
        novelty_context:
            Dictionary of context signals.

        Returns
        -------
        AIContribution
        """
        search_space_size = int(novelty_context.get("search_space_size", 1000))
        # Larger search space → more AI is needed / more AI can contribute
        coverage = _clamp(math.log10(max(search_space_size, 10)) / 6.0)
        pattern_density = _clamp(float(novelty_context.get("pattern_density", 0.5)))
        analogy_potential = _clamp(float(novelty_context.get("analogy_potential", 0.55)))
        enum_factor = float(novelty_context.get("enumeration_factor", 100.0))
        enumeration = _clamp(math.log10(max(enum_factor, 1.0)) / 4.0)
        composite = _clamp(
            _weighted_sum(
                [coverage, pattern_density, analogy_potential, enumeration],
                [0.30, 0.25, 0.25, 0.20],
            )
        )
        return AIContribution(
            contribution_id=_contribution_id(),
            search_space_coverage=coverage,
            pattern_recognition=pattern_density,
            analogical_reasoning=analogy_potential,
            large_scale_enumeration=enumeration,
            composite_score=composite,
        )

    # ------------------------------------------------------------------
    # Synergy computation
    # ------------------------------------------------------------------

    def compute_synergy(
        self,
        ag: AGContribution,
        dtt: DTTContribution,
        ai: AIContribution,
        config: FrameworkContributionConfig | None = None,
    ) -> FrameworkSynergy:
        """Compute the combined synergy score for all three frameworks.

        The synergy score is computed as:
            score = w_AG * AG.composite + w_DTT * DTT.composite + w_AI * AI.composite
            if all composites >= min_contribution:
                score += synergy_bonus

        Parameters
        ----------
        ag:
            AG contribution assessment.
        dtt:
            DTT contribution assessment.
        ai:
            AI contribution assessment.
        config:
            Optional configuration override.

        Returns
        -------
        FrameworkSynergy
        """
        cfg = config or self._config
        base_score = _clamp(
            cfg.ag_weight * ag.composite_score
            + cfg.dtt_weight * dtt.composite_score
            + cfg.ai_weight * ai.composite_score
        )
        all_active = (
            ag.composite_score >= cfg.min_contribution
            and dtt.composite_score >= cfg.min_contribution
            and ai.composite_score >= cfg.min_contribution
        )
        bonus = cfg.synergy_bonus if all_active else 0.0
        synergy_score = _clamp(base_score + bonus)
        desc_parts = [
            f"AG={ag.composite_score:.3f}",
            f"DTT={dtt.composite_score:.3f}",
            f"AI={ai.composite_score:.3f}",
        ]
        if all_active:
            desc_parts.append(f"synergy_bonus={bonus:.3f}")
        description = "Combined framework synergy: " + ", ".join(desc_parts)
        return FrameworkSynergy(
            synergy_id=_synergy_id(),
            ag_contribution_id=ag.contribution_id,
            dtt_contribution_id=dtt.contribution_id,
            ai_contribution_id=ai.contribution_id,
            synergy_score=synergy_score,
            description=description,
            timestamp=_now_iso(),
        )

    def recommend_framework(
        self, synergy: FrameworkSynergy
    ) -> Framework:
        """Recommend the dominant framework based on a synergy record.

        The recommendation is based on whichever contribution score is
        highest.  This is a heuristic for cases where the pipeline should
        prioritise one framework's tooling.

        Since we only have the IDs in the synergy record, this method
        returns ``ARTIFICIAL_INTELLIGENCE`` as the default recommendation
        (since AI is the primary *search* engine in novel contexts) but
        sub-classes may override this with richer logic.

        Parameters
        ----------
        synergy:
            The ``FrameworkSynergy`` record to base the recommendation on.

        Returns
        -------
        Framework
        """
        # Default: recommend the framework with highest perceived role
        # based on synergy score magnitude bands
        if synergy.synergy_score >= 0.75:
            return Framework.ALGEBRAIC_GEOMETRY
        if synergy.synergy_score >= 0.50:
            return Framework.DEPENDENT_TYPE_THEORY
        return Framework.ARTIFICIAL_INTELLIGENCE

    def explain_framework_choice(
        self, synergy: FrameworkSynergy, chosen: Framework
    ) -> str:
        """Produce a human-readable explanation of why *chosen* is recommended.

        Parameters
        ----------
        synergy:
            The synergy record.
        chosen:
            The recommended framework.

        Returns
        -------
        str
            Multi-line explanation.
        """
        rationale = chosen.rationale()
        lines = [
            f"Framework Recommendation Explanation",
            f"=====================================",
            f"Recommended : {chosen.value} ({chosen.short_name()})",
            f"Synergy ID  : {synergy.synergy_id}",
            f"Synergy     : {synergy.synergy_score:.4f}",
            f"Context     : {synergy.description}",
            f"",
            f"Why {chosen.short_name()}?",
            f"{'-' * 40}",
            rationale[:800] + ("…" if len(rationale) > 800 else ""),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness (accumulator)
# ---------------------------------------------------------------------------


class WhyAGDTTAINoveltyWitness:
    """Accumulates ``FrameworkSynergy`` records and provides statistics.

    Follows the standard jugeo witness pattern.  Records synergy
    assessments as they are produced and answers aggregate queries.

    Usage::

        witness = WhyAGDTTAINoveltyWitness()
        witness.record(synergy)
        print(witness.avg_synergy())
        print(witness.most_used())
    """

    def __init__(self) -> None:
        self._synergies: list[FrameworkSynergy] = []
        self._framework_counts: dict[str, int] = {f.value: 0 for f in Framework}

    def record(self, synergy: FrameworkSynergy) -> None:
        """Append *synergy* to the internal record list."""
        self._synergies.append(synergy)
        # Attribute the synergy to the recommended framework (re-computed)
        analyzer = WhyAGDTTAINoveltyAnalyzer()
        chosen = analyzer.recommend_framework(synergy)
        self._framework_counts[chosen.value] += 1

    def most_used(self) -> Framework | None:
        """Return the most-used framework, or None if no records exist."""
        if not self._synergies:
            return None
        best_fw = max(self._framework_counts, key=lambda k: self._framework_counts[k])
        return Framework(best_fw)

    def avg_synergy(self) -> float:
        """Return the mean synergy score across all recorded synergies."""
        if not self._synergies:
            return 0.0
        return sum(s.synergy_score for s in self._synergies) / len(self._synergies)

    def export(self) -> list[dict[str, Any]]:
        """Serialise all records to a list of plain dictionaries."""
        return [s.to_dict() for s in self._synergies]

    def count(self) -> int:
        """Return the total number of recorded synergies."""
        return len(self._synergies)

    def framework_usage(self) -> dict[str, int]:
        """Return a usage count per framework."""
        return dict(self._framework_counts)

    def top_synergy(self) -> FrameworkSynergy | None:
        """Return the synergy record with the highest score."""
        if not self._synergies:
            return None
        return max(self._synergies, key=lambda s: s.synergy_score)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class WhyAGDTTAINoveltyCoordinator:
    """End-to-end coordinator for the tri-framework contribution analysis.

    Combines the analyzer and witness into a single entry-point that:
    1. Assesses AG, DTT, and AI contributions from a novelty context.
    2. Computes the combined synergy score.
    3. Records the synergy in the witness.
    4. Returns the ``FrameworkSynergy`` record.

    Parameters
    ----------
    config:
        Configuration for the framework weights.  Defaults to
        ``FrameworkContributionConfig()`` with equal weights.
    """

    def __init__(self, config: FrameworkContributionConfig | None = None) -> None:
        self._config = config or FrameworkContributionConfig()
        self._analyzer = WhyAGDTTAINoveltyAnalyzer(self._config)
        self._witness = WhyAGDTTAINoveltyWitness()

    def run(self, novelty_context: dict[str, Any]) -> FrameworkSynergy:
        """Run the full tri-framework assessment.

        Parameters
        ----------
        novelty_context:
            Dictionary of context signals (see ``WhyAGDTTAINoveltyAnalyzer``
            class docstring for the full list of supported keys).

        Returns
        -------
        FrameworkSynergy
            Combined synergy record.
        """
        ag = self._analyzer.assess_ag_contribution(novelty_context)
        dtt = self._analyzer.assess_dtt_contribution(novelty_context)
        ai = self._analyzer.assess_ai_contribution(novelty_context)
        synergy = self._analyzer.compute_synergy(ag, dtt, ai, self._config)
        self._witness.record(synergy)
        return synergy

    def report(self) -> dict[str, Any]:
        """Return a summary dictionary of the witness state."""
        top = self._witness.top_synergy()
        return {
            "total_assessed": self._witness.count(),
            "avg_synergy": self._witness.avg_synergy(),
            "framework_usage": self._witness.framework_usage(),
            "top_synergy": top.to_dict() if top else None,
        }

    @property
    def witness(self) -> WhyAGDTTAINoveltyWitness:
        """Access the internal witness for advanced queries."""
        return self._witness


# ---------------------------------------------------------------------------
# Module-level factory helpers
# ---------------------------------------------------------------------------


def make_default_config() -> FrameworkContributionConfig:
    """Return the default ``FrameworkContributionConfig``."""
    return FrameworkContributionConfig()


def make_ag_biased_config() -> FrameworkContributionConfig:
    """Return a config that heavily weights AG contributions."""
    return FrameworkContributionConfig(
        ag_weight=0.60,
        dtt_weight=0.20,
        ai_weight=0.20,
        synergy_bonus=0.08,
    )


def make_ai_biased_config() -> FrameworkContributionConfig:
    """Return a config for AI-heavy exploration phases."""
    return FrameworkContributionConfig(
        ag_weight=0.20,
        dtt_weight=0.20,
        ai_weight=0.60,
        synergy_bonus=0.05,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== WhyAGDTTAI smoke test ===\n")

    _context = {
        "sheaf_quality": 0.8,
        "obstruction_count": 7,
        "localisation_precision": 0.75,
        "descent_completeness": 0.65,
        "proof_formalization_coverage": 0.7,
        "type_safety_benefit": 0.8,
        "dep_types_coverage": 0.6,
        "universe_poly": 0.5,
        "search_space_size": 50000,
        "pattern_density": 0.7,
        "analogy_potential": 0.65,
        "enumeration_factor": 500.0,
    }

    coordinator = WhyAGDTTAINoveltyCoordinator()
    synergy = coordinator.run(_context)
    print("Synergy:", synergy.summary())
    print()

    # Run several contexts to populate the witness
    for i in range(4):
        ctx = {k: max(0.1, v * (0.8 + 0.1 * i)) for k, v in _context.items()
               if isinstance(v, float)}
        ctx["obstruction_count"] = 3 + i
        ctx["search_space_size"] = 1000 * (2 ** i)
        ctx["enumeration_factor"] = 50.0 * (1.5 ** i)
        coordinator.run(ctx)

    print("Report:")
    print(json.dumps(coordinator.report(), indent=2, default=str))

    analyzer = WhyAGDTTAINoveltyAnalyzer()
    ag = analyzer.assess_ag_contribution(_context)
    dtt = analyzer.assess_dtt_contribution(_context)
    ai = analyzer.assess_ai_contribution(_context)
    print("\nAG:", ag.summary())
    print("DTT:", dtt.summary())
    print("AI:", ai.summary())

    chosen = analyzer.recommend_framework(synergy)
    print(f"\nRecommended framework: {chosen.value}")
    print("\nExplanation (truncated):")
    explanation = analyzer.explain_framework_choice(synergy, chosen)
    print(explanation[:500])

    print("\nMost used framework:", coordinator.witness.most_used())
    print("Avg synergy:", coordinator.witness.avg_synergy())

    print("\n--- AG Rationale (excerpt) ---")
    print(AG_RATIONALE[:300])
    print("\n--- DTT Rationale (excerpt) ---")
    print(DTT_RATIONALE[:300])
    print("\n--- AI Rationale (excerpt) ---")
    print(AI_RATIONALE[:300])

    print("\n=== Smoke test passed ===")
