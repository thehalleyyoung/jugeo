"""Level definitions and heuristics for the Hierarchical Site module.

Provides utilities for inferring levels from names or AST node types,
navigating the level hierarchy, and looking up per-level policy defaults.
"""

from __future__ import annotations

from typing import Any, Optional

from jugeo.scaling.hierarchical.models import SiteLevel

# ---------------------------------------------------------------------------
# LevelHeuristic
# ---------------------------------------------------------------------------

# AST node type → SiteLevel mapping
_AST_KIND_MAP: dict[str, SiteLevel] = {
    # Module-level constructs
    "module": SiteLevel.MODULE,
    "interactive": SiteLevel.MODULE,
    "expression": SiteLevel.EXPRESSION,
    "suite": SiteLevel.MODULE,
    # Class and function declarations
    "classdef": SiteLevel.CLASS,
    "asyncfunctiondef": SiteLevel.FUNCTION,
    "functiondef": SiteLevel.FUNCTION,
    "lambda": SiteLevel.FUNCTION,
    # Control flow → branch level
    "if": SiteLevel.BRANCH,
    "for": SiteLevel.BRANCH,
    "while": SiteLevel.BRANCH,
    "try": SiteLevel.BRANCH,
    "with": SiteLevel.BRANCH,
    "match": SiteLevel.BRANCH,
    "case": SiteLevel.BRANCH,
    "excepthandler": SiteLevel.BRANCH,
    # Fine-grained expressions
    "call": SiteLevel.EXPRESSION,
    "binop": SiteLevel.EXPRESSION,
    "compare": SiteLevel.EXPRESSION,
    "boolop": SiteLevel.EXPRESSION,
    "unaryop": SiteLevel.EXPRESSION,
    "subscript": SiteLevel.EXPRESSION,
    "attribute": SiteLevel.EXPRESSION,
    "name": SiteLevel.EXPRESSION,
    "constant": SiteLevel.EXPRESSION,
    "assign": SiteLevel.EXPRESSION,
    "augassign": SiteLevel.EXPRESSION,
    "annassign": SiteLevel.EXPRESSION,
    "return": SiteLevel.EXPRESSION,
    "yield": SiteLevel.EXPRESSION,
    "yieldfrom": SiteLevel.EXPRESSION,
    "await": SiteLevel.EXPRESSION,
    "raise": SiteLevel.EXPRESSION,
    "delete": SiteLevel.EXPRESSION,
    "assert": SiteLevel.EXPRESSION,
    "import": SiteLevel.MODULE,
    "importfrom": SiteLevel.MODULE,
}

# Standard depths for each level (mirrors how many dotted-name components
# are typically present when a coordinate's name is fully qualified).
_LEVEL_DEPTH: dict[SiteLevel, int] = {
    SiteLevel.PROJECT: 0,
    SiteLevel.PACKAGE: 1,
    SiteLevel.MODULE: 2,
    SiteLevel.CLASS: 3,
    SiteLevel.FUNCTION: 4,
    SiteLevel.BRANCH: 5,
    SiteLevel.EXPRESSION: 6,
}


class LevelHeuristic:
    """Static helpers for level inference and navigation."""

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @staticmethod
    def infer_level_from_name(name: str) -> SiteLevel:
        """Infer level by counting components in a dotted qualified name.

        Examples::

            "mypackage"                          → PACKAGE
            "mypackage.mymodule"                 → MODULE
            "mypackage.mymodule.MyClass"         → CLASS
            "mypackage.mymodule.MyClass.method"  → FUNCTION
        """
        if not name or name.strip() == "":
            return SiteLevel.PROJECT

        # Strip any leading/trailing dots and split
        parts = [p for p in name.strip().split(".") if p]
        depth = len(parts)

        if depth == 0:
            return SiteLevel.PROJECT
        elif depth == 1:
            # A single bare name — could be a package or a project root
            # Heuristic: if it starts with uppercase it is a class
            token = parts[0]
            if token[0].isupper():
                return SiteLevel.CLASS
            return SiteLevel.PACKAGE
        elif depth == 2:
            # pkg.mod  OR  pkg.ClassName
            token = parts[-1]
            if token[0].isupper():
                return SiteLevel.CLASS
            return SiteLevel.MODULE
        elif depth == 3:
            # pkg.mod.ClassName  OR  pkg.mod.function
            token = parts[-1]
            if token[0].isupper():
                return SiteLevel.CLASS
            return SiteLevel.FUNCTION
        elif depth == 4:
            # pkg.mod.Class.method  OR  deep function
            token = parts[-1]
            if token.startswith("_") or token[0].islower():
                return SiteLevel.FUNCTION
            return SiteLevel.CLASS
        elif depth == 5:
            return SiteLevel.BRANCH
        else:
            return SiteLevel.EXPRESSION

    @staticmethod
    def infer_level_from_ast_kind(kind: str) -> SiteLevel:
        """Map an AST node type string to a SiteLevel.

        Falls back to EXPRESSION for unknown node types.
        """
        return _AST_KIND_MAP.get(kind.lower(), SiteLevel.EXPRESSION)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @staticmethod
    def level_depth(level: SiteLevel) -> int:
        """Return the standard tree depth for a level."""
        return _LEVEL_DEPTH[level]

    @staticmethod
    def parent_level(level: SiteLevel) -> Optional[SiteLevel]:
        """Return the level immediately above the given level, or None for PROJECT."""
        if level == SiteLevel.PROJECT:
            return None
        return SiteLevel(level.value - 1)

    @staticmethod
    def child_level(level: SiteLevel) -> Optional[SiteLevel]:
        """Return the level immediately below, or None for EXPRESSION."""
        if level == SiteLevel.EXPRESSION:
            return None
        return SiteLevel(level.value + 1)

    @staticmethod
    def levels_between(high: SiteLevel, low: SiteLevel) -> list[SiteLevel]:
        """Return all levels from high down to low (inclusive), ordered coarse-to-fine.

        Raises ValueError if high is finer than low.
        """
        if high.value > low.value:
            raise ValueError(
                f"high level {high!r} must be coarser than (or equal to) low level {low!r}"
            )
        return [SiteLevel(v) for v in range(high.value, low.value + 1)]

    @staticmethod
    def all_levels() -> list[SiteLevel]:
        """Return all levels from PROJECT to EXPRESSION."""
        return list(SiteLevel)

    @staticmethod
    def all_levels_fine_to_coarse() -> list[SiteLevel]:
        """Return levels ordered finest-first (EXPRESSION → PROJECT)."""
        return list(reversed(list(SiteLevel)))


# ---------------------------------------------------------------------------
# LevelPolicy
# ---------------------------------------------------------------------------

# Default policy tables — can be overridden per project
_TRUST_REQUIREMENTS: dict[SiteLevel, str] = {
    SiteLevel.PROJECT: "project_trust",
    SiteLevel.PACKAGE: "package_trust",
    SiteLevel.MODULE: "module_trust",
    SiteLevel.CLASS: "class_trust",
    SiteLevel.FUNCTION: "function_trust",
    SiteLevel.BRANCH: "branch_trust",
    SiteLevel.EXPRESSION: "expression_trust",
}

_COVERAGE_TARGETS: dict[SiteLevel, float] = {
    SiteLevel.PROJECT: 0.95,
    SiteLevel.PACKAGE: 0.90,
    SiteLevel.MODULE: 0.85,
    SiteLevel.CLASS: 0.80,
    SiteLevel.FUNCTION: 0.75,
    SiteLevel.BRANCH: 0.70,
    SiteLevel.EXPRESSION: 0.60,
}

_MAX_COVER_SIZES: dict[SiteLevel, int] = {
    SiteLevel.PROJECT: 4,
    SiteLevel.PACKAGE: 8,
    SiteLevel.MODULE: 16,
    SiteLevel.CLASS: 32,
    SiteLevel.FUNCTION: 64,
    SiteLevel.BRANCH: 128,
    SiteLevel.EXPRESSION: 256,
}


class LevelPolicy:
    """Per-level default policy values used by the verification engine."""

    @staticmethod
    def trust_requirement_for_level(level: SiteLevel) -> str:
        """Return the default trust requirement identifier for a level."""
        return _TRUST_REQUIREMENTS[level]

    @staticmethod
    def coverage_target_for_level(level: SiteLevel) -> float:
        """Return the default coverage target (0–1) for a level."""
        return _COVERAGE_TARGETS[level]

    @staticmethod
    def max_cover_size_for_level(level: SiteLevel) -> int:
        """Return the maximum number of members in a cover at a level."""
        return _MAX_COVER_SIZES[level]

    @staticmethod
    def default_policies() -> dict[SiteLevel, dict[str, Any]]:
        """Return a dict mapping every SiteLevel to its default policy bundle."""
        return {
            level: {
                "trust_requirement": _TRUST_REQUIREMENTS[level],
                "coverage_target": _COVERAGE_TARGETS[level],
                "max_cover_size": _MAX_COVER_SIZES[level],
                "level": level.to_dict(),
            }
            for level in SiteLevel
        }

    @staticmethod
    def policy_for_level(level: SiteLevel) -> dict[str, Any]:
        """Return the policy bundle for a single level."""
        return {
            "trust_requirement": _TRUST_REQUIREMENTS[level],
            "coverage_target": _COVERAGE_TARGETS[level],
            "max_cover_size": _MAX_COVER_SIZES[level],
            "level": level.to_dict(),
        }

    @staticmethod
    def override(
        level: SiteLevel,
        trust_requirement: Optional[str] = None,
        coverage_target: Optional[float] = None,
        max_cover_size: Optional[int] = None,
    ) -> dict[str, Any]:
        """Build a policy dict with selective overrides over the defaults."""
        base = LevelPolicy.policy_for_level(level)
        if trust_requirement is not None:
            base["trust_requirement"] = trust_requirement
        if coverage_target is not None:
            base["coverage_target"] = coverage_target
        if max_cover_size is not None:
            base["max_cover_size"] = max_cover_size
        return base


__all__ = [
    "LevelHeuristic",
    "LevelPolicy",
]
