"""
jugeo.webapp.dom — Sheaf-theoretic model of the DOM and CSS cascade.

The DOM is a presheaf on the category of CSS selectors. The CSS cascade is
a descent procedure. Obstructions to descent correspond to CSS bugs.
"""
from .models import (
    DOMNodeKind, CSSPropertyKind, CSSValueType,
    Specificity, CSSValue, CSSRule, DOMNode, ComputedStyle,
    DOMSection, CascadeObstructionKind, ObstructionSeverity, CascadeObstruction,
)
from .dom_site import DOMSite, SelectorParser, SelectorChain, SelectorPart, CombinatorKind
from .specificity import compute_specificity, compare_specificity, specificity_sort, SpecificityConflictDetector
from .css_cascade import CSSCascadeEngine, CascadeDescentChecker, INHERITABLE_PROPERTIES, INITIAL_VALUES
from .media_queries import MediaQuery, MediaQueryParser, MediaQueryOverlapAnalyzer, BreakpointAnalyzer, MediaType
from .layout_model import BoxModel, LayoutBox, LayoutKind, LayoutEngine, OverlapDetector, ContainmentChecker
from .inheritance import InheritanceModel, InitialValueRegistry, INHERITABLE, NON_INHERITABLE
from .algorithms import DOMDiffEngine, SelectorCoverageAnalyzer, AccessibilityChecker, DOMChange, DOMChangeKind
from .theorems import (
    CascadeDescentTheorem, InheritanceCompletionTheorem, SelectorCoverageTheorem,
    MediaQueryGluingTheorem, ContainmentPreservationTheorem,
)

__all__ = [
    # models
    "DOMNodeKind", "CSSPropertyKind", "CSSValueType",
    "Specificity", "CSSValue", "CSSRule", "DOMNode", "ComputedStyle",
    "DOMSection", "CascadeObstructionKind", "ObstructionSeverity", "CascadeObstruction",
    # dom_site
    "DOMSite", "SelectorParser", "SelectorChain", "SelectorPart", "CombinatorKind",
    # specificity
    "compute_specificity", "compare_specificity", "specificity_sort", "SpecificityConflictDetector",
    # css_cascade
    "CSSCascadeEngine", "CascadeDescentChecker", "INHERITABLE_PROPERTIES", "INITIAL_VALUES",
    # media_queries
    "MediaQuery", "MediaQueryParser", "MediaQueryOverlapAnalyzer", "BreakpointAnalyzer", "MediaType",
    # layout_model
    "BoxModel", "LayoutBox", "LayoutKind", "LayoutEngine", "OverlapDetector", "ContainmentChecker",
    # inheritance
    "InheritanceModel", "InitialValueRegistry", "INHERITABLE", "NON_INHERITABLE",
    # algorithms
    "DOMDiffEngine", "SelectorCoverageAnalyzer", "AccessibilityChecker", "DOMChange", "DOMChangeKind",
    # theorems
    "CascadeDescentTheorem", "InheritanceCompletionTheorem", "SelectorCoverageTheorem",
    "MediaQueryGluingTheorem", "ContainmentPreservationTheorem",
]
