from __future__ import annotations
"""Grammar analysis package for gofai_chat.

Provides Grade-weighted syntactic analysis grounded in JuGeo theory:

  - cfg:                Context-Free Grammar with Earley/CYK parsing (Grade-weighted rules)
  - categorial:         CCG parser with lambda-calculus semantics
  - type_logical:       Lambek calculus / type-logical grammar with proof nets
  - construction_bank:  Construction Grammar inventory (200+ constructions)
  - gradient_grammar:   Gradient grammaticality via Grade semiring
  - transformations:    Syntactic transformations (passivization, topicalization, etc.)
  - dependency_grammar: Dependency grammar with valency frames and projectivity checking

JuGeo theory connections
-------------------------
Each parse tree is a GluingData: SynSection carries the syntactic structure,
SemSection the semantic content.  Agreement violations and valency failures are
ObstructionType.SEMANTIC_GAP.  Transformations preserve SemSection while mutating
SynSection; the overall grade of a derivation is the product of each step's Grade.

Proof nets (type_logical) are global sections of the type-logical sheaf.
Failed proofs are H¹ obstructions — grammatical gaps.
Constructions (construction_bank) are partial GluingData seeds.
Gradient judgments (gradient_grammar) are the surface form of harmony scores.
"""

from gofai_chat.grammar.cfg import (
    CFGRule,
    CFGrammar,
    EarleyParser,
    ParseForest,
    ParseTree,
)
try:
    from gofai_chat.grammar.transformations import (
        Transformation,
        TransformationEngine,
        TransformationType,
    )
except ImportError:
    Transformation = TransformationEngine = TransformationType = None
from gofai_chat.grammar.dependency_grammar import (
    DependencyArc,
    DependencyGrammar,
    DependencyRelation,
    DependencyRule,
    DependencyTree,
    ProjectivityChecker,
)
from gofai_chat.grammar.categorial import (
    Category,
    Atomic,
    Slash,
    CCGParser,
    CCGDerivation,
    CCGParseResult,
    SupertagBank,
)
from gofai_chat.grammar.type_logical import (
    LambdaType,
    AtomicLType,
    SlashType,
    BackslashType,
    LambekSequent,
    ProofNet,
    TypeLogicalParser,
    TLLexicon,
    LambekSystem,
)
from gofai_chat.grammar.construction_bank import (
    Construction,
    ConstructionFamily,
    ConstructionBank,
    ConstructionMatcher,
    ConstructionMatch,
    get_default_bank,
    get_default_matcher,
)
from gofai_chat.grammar.gradient_grammar import (
    GradientJudgment,
    GradientGrammar,
    GradientPhenomenon,
    ViolationType,
    ViolationInstance,
    grade_sentence,
    judge,
    get_default_grammar,
)

# New modules: typed features, Distributed Morphology, Minimalist syntax, LFG
try:
    from gofai_chat.grammar.features import (
        Feature,
        FeatureBundle,
        PhiFeatures,
        CaseFeature,
        TenseFeature,
        make_verb_features,
        make_noun_features,
        make_adj_features,
    )
except ImportError:
    Feature = FeatureBundle = PhiFeatures = CaseFeature = TenseFeature = None
    make_verb_features = make_noun_features = make_adj_features = None

try:
    from gofai_chat.grammar.distributed_morphology import (
        VocabularyItem,
        MorphologicalWord,
        DMEngine,
        ENGLISH_VIS,
    )
except ImportError:
    VocabularyItem = MorphologicalWord = DMEngine = ENGLISH_VIS = None

try:
    from gofai_chat.grammar.minimalism import (
        SyntacticObject,
        MergeResult,
        Merge,
        Agree,
        PhaseEngine,
        build_clause,
    )
except ImportError:
    SyntacticObject = MergeResult = Merge = Agree = PhaseEngine = build_clause = None

try:
    from gofai_chat.grammar.lfg import (
        FStructure,
        PhiCorrespondence,
        GrammaticalFunction,
    )
except ImportError:
    FStructure = PhiCorrespondence = GrammaticalFunction = None

__all__ = [
    # cfg
    "CFGRule",
    "CFGrammar",
    "EarleyParser",
    "ParseForest",
    "ParseTree",
    # transformations
    "Transformation",
    "TransformationEngine",
    "TransformationType",
    # dependency_grammar
    "DependencyArc",
    "DependencyGrammar",
    "DependencyRelation",
    "DependencyRule",
    "DependencyTree",
    "ProjectivityChecker",
    # categorial
    "Category",
    "Atomic",
    "Slash",
    "CCGParser",
    "CCGDerivation",
    "CCGParseResult",
    "SupertagBank",
    # type_logical
    "LambdaType",
    "AtomicLType",
    "SlashType",
    "BackslashType",
    "LambekSequent",
    "ProofNet",
    "TypeLogicalParser",
    "TLLexicon",
    "LambekSystem",
    # construction_bank
    "Construction",
    "ConstructionFamily",
    "ConstructionBank",
    "ConstructionMatcher",
    "ConstructionMatch",
    "get_default_bank",
    "get_default_matcher",
    # gradient_grammar
    "GradientJudgment",
    "GradientGrammar",
    "GradientPhenomenon",
    "ViolationType",
    "ViolationInstance",
    "grade_sentence",
    "judge",
    "get_default_grammar",
    # features (new)
    "Feature",
    "FeatureBundle",
    "PhiFeatures",
    "CaseFeature",
    "TenseFeature",
    "make_verb_features",
    "make_noun_features",
    "make_adj_features",
    # distributed_morphology (new)
    "VocabularyItem",
    "MorphologicalWord",
    "DMEngine",
    "ENGLISH_VIS",
    # minimalism (new)
    "SyntacticObject",
    "MergeResult",
    "Merge",
    "Agree",
    "PhaseEngine",
    "build_clause",
    # lfg (new)
    "FStructure",
    "PhiCorrespondence",
    "GrammaticalFunction",
]
