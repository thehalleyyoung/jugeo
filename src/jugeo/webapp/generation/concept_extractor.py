"""Concept extraction from natural-language prompts.

From the JG perspective, concept extraction is *restriction* of the
intent section (the user's prompt) to the concept fiber.  We identify
which feature domains the prompt activates — gaming, art generation,
audio, data, UI — and parameterize each with prompt-derived details.

The output is a ``ConceptMap`` that the ``PromptToApp`` driver uses to
select and parameterize code generators for each fiber.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConceptDomain(str, Enum):
    """High-level domains a prompt can activate."""
    GAME = "game"
    ART = "art"
    MEDIA = "media"
    UI = "ui"
    DATA = "data"
    SOCIAL = "social"
    EDUCATION = "education"
    VISUALIZATION = "visualization"


@dataclass
class Concept:
    """A single concept extracted from a prompt."""
    name: str
    domain: ConceptDomain
    relevance: float = 1.0  # 0..1
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConceptMap:
    """Full concept analysis of a prompt."""
    prompt: str
    concepts: list[Concept] = field(default_factory=list)
    app_name: str = ""
    app_title: str = ""
    app_description: str = ""

    @property
    def domains(self) -> set[ConceptDomain]:
        return {c.domain for c in self.concepts}

    def get(self, name: str) -> Concept | None:
        for c in self.concepts:
            if c.name == name:
                return c
        return None

    def by_domain(self, domain: ConceptDomain) -> list[Concept]:
        return [c for c in self.concepts if c.domain == domain]

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    @property
    def generator_names(self) -> list[str]:
        return [c.name for c in sorted(self.concepts, key=lambda c: -c.relevance)]


# ── Keyword → concept mapping ────────────────────────────────────────

_CONCEPT_KEYWORDS: dict[str, tuple[str, ConceptDomain, dict[str, Any]]] = {
    # ART domain
    "generative art": ("generative_art", ConceptDomain.ART, {}),
    "procedural": ("generative_art", ConceptDomain.ART, {}),
    "noise": ("noise", ConceptDomain.ART, {}),
    "perlin": ("noise", ConceptDomain.ART, {"algorithms": ["perlin", "simplex"]}),
    "fractal": ("fractal", ConceptDomain.ART, {}),
    "mandelbrot": ("fractal", ConceptDomain.ART, {"types": ["mandelbrot", "julia"]}),
    "l-system": ("lsystem", ConceptDomain.ART, {}),
    "particle": ("particle", ConceptDomain.ART, {}),
    "cellular automata": ("cellular", ConceptDomain.ART, {}),
    "color": ("color_theory", ConceptDomain.ART, {}),
    "palette": ("color_theory", ConceptDomain.ART, {}),
    "composition": ("composition", ConceptDomain.ART, {}),
    "artistic": ("generative_art", ConceptDomain.ART, {}),
    "creative": ("generative_art", ConceptDomain.ART, {}),
    "drawing": ("canvas_draw", ConceptDomain.ART, {}),
    "painting": ("canvas_draw", ConceptDomain.ART, {}),
    "visual": ("generative_art", ConceptDomain.ART, {}),

    # GAME domain
    "game": ("game_engine", ConceptDomain.GAME, {}),
    "gaming": ("game_engine", ConceptDomain.GAME, {}),
    "territory": ("territory", ConceptDomain.GAME, {}),
    "strategy": ("territory", ConceptDomain.GAME, {"type": "strategy"}),
    "combat": ("combat", ConceptDomain.GAME, {}),
    "battle": ("combat", ConceptDomain.GAME, {}),
    "fight": ("combat", ConceptDomain.GAME, {}),
    "score": ("scoring", ConceptDomain.GAME, {}),
    "points": ("scoring", ConceptDomain.GAME, {}),
    "achievement": ("scoring", ConceptDomain.GAME, {"achievements": True}),
    "ai": ("ai_opponent", ConceptDomain.GAME, {}),
    "opponent": ("ai_opponent", ConceptDomain.GAME, {}),
    "player": ("game_engine", ConceptDomain.GAME, {}),
    "turn": ("game_engine", ConceptDomain.GAME, {"turn_based": True}),
    "level": ("game_engine", ConceptDomain.GAME, {"levels": True}),
    "puzzle": ("game_engine", ConceptDomain.GAME, {"type": "puzzle"}),
    "rpg": ("game_engine", ConceptDomain.GAME, {"type": "rpg"}),

    # MEDIA domain
    "audio": ("audio_synth", ConceptDomain.MEDIA, {}),
    "music": ("generative_music", ConceptDomain.MEDIA, {}),
    "sound": ("audio_synth", ConceptDomain.MEDIA, {}),
    "synth": ("audio_synth", ConceptDomain.MEDIA, {}),

    # UI domain
    "canvas": ("canvas_renderer", ConceptDomain.UI, {}),
    "gallery": ("gallery", ConceptDomain.UI, {}),
    "tutorial": ("tutorial", ConceptDomain.UI, {}),
    "dashboard": ("dashboard", ConceptDomain.UI, {}),
    "editor": ("editor", ConceptDomain.UI, {}),

    # DATA domain
    "save": ("data_layer", ConceptDomain.DATA, {}),
    "persist": ("data_layer", ConceptDomain.DATA, {}),
    "leaderboard": ("data_layer", ConceptDomain.DATA, {"leaderboard": True}),
    "database": ("data_layer", ConceptDomain.DATA, {}),

    # VISUALIZATION domain
    "chart": ("chart", ConceptDomain.VISUALIZATION, {}),
    "graph": ("chart", ConceptDomain.VISUALIZATION, {}),
    "plot": ("chart", ConceptDomain.VISUALIZATION, {}),
    "data viz": ("chart", ConceptDomain.VISUALIZATION, {}),
    "visualization": ("chart", ConceptDomain.VISUALIZATION, {}),

    # SOCIAL domain
    "share": ("social", ConceptDomain.SOCIAL, {}),
    "community": ("social", ConceptDomain.SOCIAL, {}),
    "collaborate": ("social", ConceptDomain.SOCIAL, {}),
}

# ── Concept bundles: when two domains co-occur, activate bridge concepts ──

_CONCEPT_BRIDGES: dict[tuple[str, str], list[tuple[str, ConceptDomain, dict]]] = {
    ("game_engine", "generative_art"): [
        ("territory", ConceptDomain.GAME, {"type": "art_strategy"}),
        ("combat", ConceptDomain.GAME, {"style": "color_based"}),
        ("scoring", ConceptDomain.GAME, {"metric": "composition"}),
        ("ai_opponent", ConceptDomain.GAME, {"style": "artistic"}),
        ("composition", ConceptDomain.ART, {"game_integrated": True}),
        ("color_theory", ConceptDomain.ART, {"game_integrated": True}),
        ("noise", ConceptDomain.ART, {}),
        ("fractal", ConceptDomain.ART, {}),
        ("lsystem", ConceptDomain.ART, {}),
        ("particle", ConceptDomain.ART, {}),
        ("cellular", ConceptDomain.ART, {}),
        ("canvas_renderer", ConceptDomain.UI, {}),
        ("gallery", ConceptDomain.UI, {}),
        ("tutorial", ConceptDomain.UI, {}),
        ("audio_synth", ConceptDomain.MEDIA, {}),
        ("generative_music", ConceptDomain.MEDIA, {}),
        ("data_layer", ConceptDomain.DATA, {"leaderboard": True, "achievements": True}),
    ],
    ("game_engine", "audio_synth"): [
        ("generative_music", ConceptDomain.MEDIA, {"game_reactive": True}),
    ],
    ("generative_art", "gallery"): [
        ("data_layer", ConceptDomain.DATA, {"gallery_storage": True}),
    ],
}


def _slugify(text: str) -> str:
    """Convert prompt text to a snake_case app name."""
    words = re.sub(r'[^a-z0-9\s]', '', text.lower()).split()
    # Take first few meaningful words
    stop = {"a", "an", "the", "which", "that", "this", "in", "of", "for", "and",
            "but", "not", "with", "ways", "e.g.", "is", "are", "its", "more"}
    meaningful = [w for w in words if w not in stop][:4]
    return "_".join(meaningful) if meaningful else "generated_app"


def _titleize(slug: str) -> str:
    return slug.replace("_", " ").title()


def extract_concepts(prompt: str) -> ConceptMap:
    """Extract concepts from a natural-language prompt.

    Scans for keywords, activates concept domains, then applies
    bridge rules when multiple domains co-occur (e.g., game + art
    activates territory, combat, composition, color theory).
    """
    lower = prompt.lower()
    seen: dict[str, Concept] = {}

    # Phase 1: keyword scan
    for keyword, (name, domain, params) in _CONCEPT_KEYWORDS.items():
        if keyword in lower:
            if name not in seen:
                seen[name] = Concept(name=name, domain=domain, relevance=0.8, params=dict(params))
            else:
                # Increase relevance for repeated matches
                seen[name].relevance = min(1.0, seen[name].relevance + 0.1)
                seen[name].params.update(params)

    # Phase 2: bridge rules — when two concepts co-occur, activate bridges
    names = set(seen.keys())
    for (a, b), bridges in _CONCEPT_BRIDGES.items():
        if a in names and b in names:
            for bname, bdomain, bparams in bridges:
                if bname not in seen:
                    seen[bname] = Concept(name=bname, domain=bdomain,
                                          relevance=0.7, params=dict(bparams))

    # Phase 3: always include core infrastructure
    for name, domain in [("data_layer", ConceptDomain.DATA),
                         ("ui_system", ConceptDomain.UI),
                         ("canvas_renderer", ConceptDomain.UI),
                         ("design_system", ConceptDomain.UI),
                         ("app_init", ConceptDomain.UI)]:
        if name not in seen:
            seen[name] = Concept(name=name, domain=domain, relevance=0.5)

    slug = _slugify(prompt)
    title = _titleize(slug)
    # Derive a short, user-facing description — never use the raw prompt
    domains = {c.domain.value for c in seen.values()}
    if domains:
        domain_str = ", ".join(sorted(domains))
        description = f"An interactive {title.lower()} application featuring {domain_str}."
    else:
        description = f"An interactive {title.lower()} application."
    return ConceptMap(
        prompt=prompt,
        concepts=sorted(seen.values(), key=lambda c: -c.relevance),
        app_name=slug,
        app_title=title,
        app_description=description,
    )
