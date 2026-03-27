from __future__ import annotations
"""Inference package for gofai_chat: default reasoning, belief revision, analogy, temporal, force dynamics, counterfactual, abduction, spatial, scalar, narrative."""

from gofai_chat.inference.defaults import DefaultRule, DefaultReasoner, DEFAULT_RULES
from gofai_chat.inference.retraction import Belief, BeliefBase, RetractionEngine
from gofai_chat.inference.analogy import StructuralMapping, StructureMappingEngine, BlendSpace, ConceptualBlender
from gofai_chat.inference.temporal import AllenRelation, TemporalInterval, AllenReasoningEngine, TemporalAnaphoraResolver
from gofai_chat.inference.force_dynamics import ForceTendency, ForceStrength, ForceDynamic, ForceDynamicAnalyzer
from gofai_chat.inference.counterfactual import CounterfactualReasoner
from gofai_chat.inference.abduction import Hypothesis, AbductiveReasoner
from gofai_chat.inference.spatial import RCC8Relation, SpatialReasoner
from gofai_chat.inference.scalar import ScaleType, DegreeModifier, ScalarSemantics, DEGREE_MODIFIERS
from gofai_chat.inference.narrative import NarrativeElement, CharacterRole, NarrativeAnalyzer
from gofai_chat.inference.fuzzy import (
    MembershipFunction,
    FuzzySet,
    LinguisticVariable,
    FuzzyRule,
    FuzzyInferenceSystem,
    GradeFuzzyBridge,
    FuzzySetArithmetic,
    GradeThresholdClassifier,
)
from gofai_chat.inference.inductive import (
    Example,
    Hypothesis as InductiveHypothesis,
    InductiveLearner,
    FormalConcept,
    ConceptLattice,
    GradeWeightedID3,
    VersionSpace,
    GradeHypothesisRanker,
)

__all__ = [
    "DefaultRule", "DefaultReasoner", "DEFAULT_RULES",
    "Belief", "BeliefBase", "RetractionEngine",
    "StructuralMapping", "StructureMappingEngine", "BlendSpace", "ConceptualBlender",
    "AllenRelation", "TemporalInterval", "AllenReasoningEngine", "TemporalAnaphoraResolver",
    "ForceTendency", "ForceStrength", "ForceDynamic", "ForceDynamicAnalyzer",
    "CounterfactualReasoner",
    "Hypothesis", "AbductiveReasoner",
    "RCC8Relation", "SpatialReasoner",
    "ScaleType", "DegreeModifier", "ScalarSemantics", "DEGREE_MODIFIERS",
    "NarrativeElement", "CharacterRole", "NarrativeAnalyzer",
    # fuzzy
    "MembershipFunction", "FuzzySet", "LinguisticVariable", "FuzzyRule",
    "FuzzyInferenceSystem", "GradeFuzzyBridge", "FuzzySetArithmetic",
    "GradeThresholdClassifier",
    # inductive
    "Example", "InductiveHypothesis", "InductiveLearner", "FormalConcept",
    "ConceptLattice", "GradeWeightedID3", "VersionSpace", "GradeHypothesisRanker",
]
