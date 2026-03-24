"""Provenance tracking for directed research.

Every section, move, and decision is tracked for full reproducibility.
Wraps jugeo.evidence.provenance when available.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from jugeo.directed_research._types import LLMSection, MoveResult, HAS_PROVENANCE

if HAS_PROVENANCE:
    from jugeo.evidence.provenance import ProvenanceTrace, ProvenanceStep


@dataclass
class ResearchProvenance:
    """Full provenance trace for a directed research run."""
    prompt: str = ""
    start_time: float = field(default_factory=time.time)
    moves: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    pivots: list[dict[str, Any]] = field(default_factory=list)

    def record_move(self, move: MoveResult) -> None:
        self.moves.append({
            "move": move.move.value,
            "surface": move.surface.value,
            "trust": move.trust_achieved,
            "success": move.success,
            "elapsed": move.elapsed,
            "timestamp": time.time(),
        })

    def record_section(self, section: LLMSection) -> None:
        self.sections.append({
            "surface": section.surface.value,
            "coordinate": section.coordinate,
            "trust": section.trust,
            "agent": section.agent_backend.value,
            "elapsed": section.elapsed,
            "timestamp": time.time(),
        })

    def save(self, output_dir: str) -> str:
        path = os.path.join(output_dir, "provenance.json")
        with open(path, "w") as f:
            json.dump({
                "prompt": self.prompt,
                "start_time": self.start_time,
                "moves": self.moves,
                "sections": self.sections,
                "pivots": self.pivots,
            }, f, indent=2, default=str)
        return path
