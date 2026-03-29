from __future__ import annotations
"""Syntactic analysis sub-package."""

from gofai_chat.strata.syn.analyzer import SynAnalyzer, SynSection

try:
    from gofai_chat.strata.syn.spacy_bridge import SpacyBridge
    _HAS_SPACY_BRIDGE = True
except ImportError:
    SpacyBridge = None
    _HAS_SPACY_BRIDGE = False

try:
    from gofai_chat.strata.syn.dependency import (
        DepRel, DepArc, DepTree, DependencyGrammar,
    )
    __all__ = ["SynAnalyzer", "SynSection", "SpacyBridge",
               "DepRel", "DepArc", "DepTree", "DependencyGrammar"]
except ImportError:
    __all__ = ["SynAnalyzer", "SynSection", "SpacyBridge"]

try:
    from gofai_chat.strata.syn.constituency import PSGrammar
    _HAS_CONSTITUENCY = True
except (ImportError, SyntaxError):
    _HAS_CONSTITUENCY = False

try:
    from gofai_chat.strata.syn.transformations import SyntacticTransform
    _HAS_TRANSFORMATIONS = True
except (ImportError, SyntaxError):
    _HAS_TRANSFORMATIONS = False
