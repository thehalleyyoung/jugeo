"""Code generators for jugeo-webapp prompt-to-app pipeline.

Each generator is a Python function that produces a complete, working
JavaScript/CSS/HTML module as a string.  Generators are parameterized —
the concept extractor determines WHICH generators to call and with what
parameters based on the user's prompt.

From the JG perspective, each generator constructs a *local section* of
the application sheaf over one fiber (JS interaction, CSS styling, etc.).
The obligation presheaf then verifies that the assembled global section
satisfies descent (quality, completeness, scale).

Generators are registered in GENERATOR_REGISTRY and looked up by concept
name from the ConceptMap.  Each generator receives a ``scale`` parameter
(1–5) that controls how much code it produces: 1 = minimal, 3 = standard,
5 = production (deep algorithms, extensive edge-case handling, rich
comments and documentation).
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

# Registry: concept_name → generator function
# Each generator returns (js: str, css: str, html: str) — any may be empty.
GeneratorFn = Callable[..., tuple[str, str, str]]
GENERATOR_REGISTRY: dict[str, GeneratorFn] = {}


def register(name: str):
    """Decorator to register a code generator by concept name."""
    def decorator(fn: GeneratorFn) -> GeneratorFn:
        GENERATOR_REGISTRY[name] = fn
        return fn
    return decorator


def generate_for_concept(name: str, params: dict[str, Any] | None = None,
                         scale: int = 3) -> tuple[str, str, str]:
    """Look up and invoke a generator by concept name.

    Parameters
    ----------
    name : str
        Concept name (must match a registered generator).
    params : dict
        Concept-specific parameters from the concept extractor.
    scale : int
        Code scale factor (1=minimal, 3=standard, 5=production).
        Generators that support it produce proportionally more code
        at higher scale: deeper algorithms, more helper methods,
        richer comments, additional edge-case handling.

    Returns (js, css, html) — any component may be empty string.
    """
    fn = GENERATOR_REGISTRY.get(name)
    if fn is None:
        return ("", "", "")
    merged = dict(params or {})
    merged.setdefault("scale", scale)
    try:
        return fn(**merged)
    except TypeError:
        # Generator doesn't accept scale — call without it
        merged.pop("scale", None)
        return fn(**merged)


def available_generators() -> list[str]:
    return sorted(GENERATOR_REGISTRY.keys())


def scale_for_obligations(obligation_preset: str) -> int:
    """Map an obligation preset name to a generator scale factor."""
    return {"minimal": 1, "standard": 2, "stunning": 3, "production": 5}.get(
        obligation_preset, 3
    )


# ── Auto-import generator modules (resilient to missing files) ────────

_GENERATOR_MODULES = [
    "js_art_generators",
    "js_game_generators",
    "js_ui_generators",
    "css_generators",
    "html_generators",
]

for _mod_name in _GENERATOR_MODULES:
    try:
        importlib.import_module(f".{_mod_name}", __name__)
    except (ImportError, ModuleNotFoundError) as exc:
        log.debug("Generator module %s not available: %s", _mod_name, exc)

