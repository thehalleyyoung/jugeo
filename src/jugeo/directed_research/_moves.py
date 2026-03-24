"""Semantic move dispatch — routes MoveKind to the appropriate executor.

Each move is a typed operation on the workspace site. This module provides
the dispatch table that maps move kinds to their implementations.
"""

from __future__ import annotations

from jugeo.directed_research._types import MoveKind, MOVE_SURFACE, SurfaceKind


def move_to_surface(move: MoveKind) -> SurfaceKind:
    """Get the surface a move primarily affects."""
    return MOVE_SURFACE.get(move, SurfaceKind.CLAIMS)


def is_ideation_move(move: MoveKind) -> bool:
    """Check if a move belongs to the ideation phase."""
    return move.value.startswith("ideate_")


def is_verification_move(move: MoveKind) -> bool:
    """Check if a move is a jg verification step."""
    return move.value.startswith("run_jg_") or move.value.startswith("verify_")
