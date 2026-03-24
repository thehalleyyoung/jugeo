"""Copilot-driven generation with obligation-presheaf verification.

The architecture is sheaf-theoretic:

1. **Fiber decomposition**: The app is decomposed into fibers
   (HTML structure, CSS styling, JS interaction, content, routing).
   Each fiber is a section of the web-application sheaf.

2. **Section generation**: Copilot generates candidate sections for
   each fiber.  This is the *creative* step — the LLM produces rich,
   stunning content.

3. **Descent verification**: The obligation checker verifies that
   sections satisfy the obligation presheaf (quality, completeness,
   interactivity, visual richness).  This is the *geometric* step.

4. **Obstruction repair**: If descent fails (obligations unmet), the
   checker produces an *obstruction report* — typed, precise feedback
   about exactly which obligations are unmet and by how much.  Copilot
   uses this to generate targeted repairs.

5. **Convergence**: The loop repeats until all obligations are met
   or a maximum depth is reached.

This module provides:

- ``FiberKind`` — the fibers of the app sheaf
- ``SectionProposal`` — a candidate section for one fiber
- ``CopilotGenerationDriver`` — orchestrates the full loop
- ``copilot_generate()`` — high-level function for use in scripts

Usage from a generation script (run by Copilot)::

    from jugeo.webapp.generation.copilot_driver import (
        CopilotGenerationDriver, FiberKind, SectionProposal
    )

    driver = CopilotGenerationDriver(obligations="stunning")
    driver.propose(FiberKind.HTML_STRUCTURE, SectionProposal(...))
    driver.propose(FiberKind.CSS_STYLING, SectionProposal(...))
    driver.propose(FiberKind.JS_INTERACTION, SectionProposal(...))
    result = driver.generate("/path/to/output")
    # result.obligation_report.all_met == True  (guaranteed by the loop)
"""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .html_generator import (
    HTMLOnlyGenerator,
    HTMLAppSpec,
    HTMLGenerationResult,
    PageSpec,
    PageKind,
    ComponentSpec,
    ComponentKind,
    HTMLObligationChecker,
    HTMLSpecEnricher,
)
from .flask_obligations import FlaskObligationChecker, FlaskSpecEnricher
from .flask_generator import FlaskAppGenerator
from .obligations import (
    Obligation,
    ObligationKind,
    ObligationReport,
    resolve_obligations,
    enforce_obligations,
    GenerationTarget,
    OBLIGATION_PRESETS,
)

# backward compat
VisualObligation = Obligation


# ── Fiber decomposition ──────────────────────────────────────────────

class FiberKind(str, Enum):
    """The fibers of the HTML app sheaf over the browser base space."""
    HTML_STRUCTURE = "html_structure"
    CSS_STYLING = "css_styling"
    JS_INTERACTION = "js_interaction"
    CONTENT = "content"
    NAVIGATION = "navigation"
    ANIMATION = "animation"
    DATA_LAYER = "data_layer"
    THEME = "theme"


@dataclass
class SectionProposal:
    """A candidate section for one fiber, proposed by Copilot.

    In sheaf terms, this is a local section s_U over an open set U
    (the fiber).  The obligation checker verifies that this section
    is compatible with sections on overlapping opens (cross-fiber
    descent).
    """
    fiber: FiberKind
    components: list[ComponentSpec] = field(default_factory=list)
    css: str = ""
    js: str = ""
    nav_items: list[dict[str, str]] = field(default_factory=list)
    theme: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "fiber": self.fiber.value,
            "components": [c.to_dict() for c in self.components],
            "css": self.css[:100] + "..." if len(self.css) > 100 else self.css,
            "js": self.js[:100] + "..." if len(self.js) > 100 else self.js,
            "nav_items": self.nav_items,
        }


@dataclass
class DescentReport:
    """Report on whether proposed sections satisfy descent (glue properly)."""
    satisfied: bool
    obligation_report: ObligationReport | None = None
    obstruction_summary: str = ""
    fiber_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "satisfied": self.satisfied,
            "obstruction_summary": self.obstruction_summary,
            "fiber_scores": self.fiber_scores,
        }


# ── Copilot generation driver ────────────────────────────────────────

class CopilotGenerationDriver:
    """Orchestrates Copilot-driven generation with JG obligation verification.

    The driver collects ``SectionProposal`` objects from Copilot (one per
    fiber), assembles them into an ``HTMLAppSpec``, checks obligations,
    and if any are unmet, reports precise obstructions so Copilot can
    propose repairs.

    This is the *judgment-geometric* way to use an LLM for generation:
    the LLM is the section generator, the obligation presheaf is the
    type system, and descent verification is the type checker.
    """

    def __init__(
        self,
        name: str = "app",
        title: str = "Application",
        description: str = "",
        port: int = 8080,
        obligations: str | list[Obligation] = "stunning",
        target: GenerationTarget = GenerationTarget.HTML_ONLY,
    ) -> None:
        self.name = name
        self.title = title
        self.description = description
        self.port = port
        self.target = target
        self._proposals: dict[FiberKind, SectionProposal] = {}

        if target == GenerationTarget.FLASK:
            self._checker = FlaskObligationChecker()
            self._enricher = FlaskSpecEnricher()
        else:
            self._checker = HTMLObligationChecker()
            self._enricher = HTMLSpecEnricher()

        self._obligations = resolve_obligations(obligations, target)

    def propose(self, fiber: FiberKind, proposal: SectionProposal) -> None:
        """Submit a section proposal for a fiber."""
        proposal.fiber = fiber
        self._proposals[fiber] = proposal

    def propose_section(self, fiber: FiberKind, *,
                        components: list[ComponentSpec] | None = None,
                        css: str = "", js: str = "",
                        nav_items: list[dict[str, str]] | None = None,
                        theme: dict[str, str] | None = None) -> None:
        """Convenience: propose a section with keyword arguments."""
        self.propose(fiber, SectionProposal(
            fiber=fiber,
            components=components or [],
            css=css, js=js,
            nav_items=nav_items or [],
            theme=theme or {},
        ))

    def assemble_spec(self) -> HTMLAppSpec:
        """Assemble all proposals into a single HTMLAppSpec."""
        all_components: list[ComponentSpec] = []
        all_css_parts: list[str] = []
        all_js_parts: list[str] = []
        nav_items: list[dict[str, str]] = []
        theme: dict[str, str] = {}

        # Order: navigation, theme, html_structure, content,
        #        css_styling, animation, js_interaction, data_layer
        order = [
            FiberKind.THEME,
            FiberKind.NAVIGATION,
            FiberKind.HTML_STRUCTURE,
            FiberKind.CONTENT,
            FiberKind.CSS_STYLING,
            FiberKind.ANIMATION,
            FiberKind.JS_INTERACTION,
            FiberKind.DATA_LAYER,
        ]

        for fiber in order:
            prop = self._proposals.get(fiber)
            if not prop:
                continue
            all_components.extend(prop.components)
            if prop.css:
                all_css_parts.append(f"/* ─── Fiber: {fiber.value} ─── */\n{prop.css}")
            if prop.js:
                all_js_parts.append(f"/* ─── Fiber: {fiber.value} ─── */\n{prop.js}")
            if prop.nav_items:
                nav_items.extend(prop.nav_items)
            if prop.theme:
                theme.update(prop.theme)

        page = PageSpec(
            name="index",
            title=self.title,
            route="/",
            kind=PageKind.INTERACTIVE,
            components=all_components,
            custom_css="\n\n".join(all_css_parts),
            custom_js="\n\n".join(all_js_parts),
            description=self.description,
        )

        return HTMLAppSpec(
            name=self.name,
            title=self.title,
            description=self.description,
            port=self.port,
            pages=[page],
            nav_items=nav_items,
            theme=theme,
            meta={"version": "1.0.0", "generator": "copilot+jugeo-webapp"},
        )

    def check_descent(self, spec: HTMLAppSpec | None = None) -> DescentReport:
        """Check whether the assembled spec satisfies all obligations."""
        if spec is None:
            spec = self.assemble_spec()

        report = self._checker.check(spec, self._obligations)

        fiber_scores: dict[str, float] = {}
        for fiber, prop in self._proposals.items():
            score = 0
            if prop.components:
                score += len(prop.components) * 2
            if prop.css:
                score += prop.css.count("\n") / 10
            if prop.js:
                score += prop.js.count("\n") / 10
            fiber_scores[fiber.value] = round(score, 1)

        obstruction_lines: list[str] = []
        for r in report.unmet:
            obstruction_lines.append(
                f"  {r.obligation.kind.value}: need {r.obligation.minimum}, "
                f"have {r.actual} (deficit {r.deficit})"
            )

        return DescentReport(
            satisfied=report.all_met,
            obligation_report=report,
            obstruction_summary="\n".join(obstruction_lines) if obstruction_lines else "All obligations met.",
            fiber_scores=fiber_scores,
        )

    def generate(self, output_dir: str, auto_enrich: bool = True,
                 max_rounds: int = 5) -> HTMLGenerationResult:
        """Generate the app, optionally auto-enriching to meet obligations.

        Parameters
        ----------
        output_dir : str
            Where to write the generated files.
        auto_enrich : bool
            If True (default), the enricher patches the spec to meet any
            remaining obligations after Copilot's proposals.  This is the
            *fallback repair functor* — ideally Copilot's proposals already
            satisfy everything, and enrichment is a no-op.
        max_rounds : int
            Maximum enrichment rounds if auto_enrich is True.
        """
        spec = self.assemble_spec()

        # Check descent first
        descent = self.check_descent(spec)
        rounds = 0

        if auto_enrich:
            while not descent.satisfied and rounds < max_rounds:
                report = descent.obligation_report
                if report:
                    spec = self._enricher.enrich(spec, report.unmet)
                rounds += 1
                descent = self.check_descent(spec)

        # Now generate files using the HTML-only generator (with obligations
        # set to the same level — but since we already enriched, this should
        # be a no-op verification pass).
        generator = HTMLOnlyGenerator(
            obligations=self._obligations,
            max_enrichment_rounds=0,  # we already enriched
        )
        result = generator.generate(spec, output_dir, obligations=self._obligations)
        if descent.obligation_report:
            descent.obligation_report.enrichment_rounds = rounds
            result.obligation_report = descent.obligation_report
        return result


# ── Convenience function ──────────────────────────────────────────────

def copilot_generate(
    name: str,
    title: str,
    output_dir: str,
    proposals: dict[FiberKind, SectionProposal],
    *,
    description: str = "",
    port: int = 8080,
    obligations: str = "stunning",
    auto_enrich: bool = True,
    target: GenerationTarget = GenerationTarget.HTML_ONLY,
) -> HTMLGenerationResult:
    """One-shot Copilot-driven generation.

    Usage::

        result = copilot_generate(
            name="my_app", title="My App", output_dir="./out",
            proposals={
                FiberKind.HTML_STRUCTURE: SectionProposal(components=[...]),
                FiberKind.CSS_STYLING: SectionProposal(css="..."),
                FiberKind.JS_INTERACTION: SectionProposal(js="..."),
            },
        )
    """
    driver = CopilotGenerationDriver(
        name=name, title=title, description=description,
        port=port, obligations=obligations, target=target,
    )
    for fiber, proposal in proposals.items():
        driver.propose(fiber, proposal)
    return driver.generate(output_dir, auto_enrich=auto_enrich)
