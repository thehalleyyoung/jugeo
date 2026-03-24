"""Binary tournament over domain pairings (§4)."""

from __future__ import annotations

from jugeo.directed_research._types import BridgeProposition


def tournament_select(
    propositions: list[BridgeProposition],
) -> BridgeProposition:
    """Select the best proposition via tournament (highest UNS wins)."""
    if not propositions:
        raise ValueError("No propositions to select from")
    return max(propositions, key=lambda bp: bp.useful_novelty_score)
