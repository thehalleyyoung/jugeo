#!/usr/bin/env python3
"""Generate Chromatic Territories — a game/art fusion app.

This script contains ONLY the prompt and the jugeo API call.
All code synthesis is performed by the jugeo-webapp framework:

  1. Concept extraction restricts the prompt to the concept fiber
  2. Agent channel (copilot → claude → codex) generates JS/CSS/HTML
     for each concept — each agent call is a local section on the
     Code surface with COPILOT_SUGGESTED trust
  3. Obligation presheaf enforces descent (20K+ LOC, 12+ modules,
     8+ feature systems, etc.)
  4. Enricher makes additional agent calls to repair obstructions
  5. CopilotGenerationDriver assembles and renders final files

Usage:
    python3 scripts/generate_chromatic_territories.py
    python3 scripts/generate_chromatic_territories.py --target flask
    python3 scripts/generate_chromatic_territories.py --outdir /tmp/ct
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jugeo.webapp.generation.prompt_driver import PromptToApp


PROMPT = (
    "A unique app which mixes gaming and artistic generation, not in "
    "superficial ways (e.g., getting points for creating things) but in a "
    "more meaningful blend. Territory is composition, color is resource, "
    "generative brushes are weapons, and composition score is health. "
    "Features: hex-grid territory control, procedural art generation "
    "(noise, fractals, L-systems, particles, cellular automata), "
    "AI opponents, combat system, scoring/achievements, audio synthesis, "
    "gallery, tutorial system, and a polished dark-theme UI."
)


def main():
    parser = argparse.ArgumentParser(description="Generate Chromatic Territories")
    parser.add_argument("--target", default="html", choices=["html", "flask", "both"])
    parser.add_argument("--outdir", default="/tmp/chromatic-territories")
    parser.add_argument("--obligations", default="production",
                        choices=["minimal", "standard", "stunning", "production"])
    args = parser.parse_args()

    result = PromptToApp(PROMPT, obligations=args.obligations).generate(
        args.outdir, target=args.target,
    )

    print(result.summary())


if __name__ == "__main__":
    main()
