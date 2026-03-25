"""Jinja2 Template Inheritance as a Grothendieck Site
=====================================================

Jinja2's template inheritance system is modelled as a *Grothendieck site*:

* **Objects (Coordinates)** — individual template files (base or child).
* **Blocks (Coordinates)** — named ``{% block %}`` regions are coordinates
  *within* a template, i.e. sub-coordinates of the template they live in.
* **Morphisms** — a ``{% extends %}`` directive is a :attr:`MorphismKind.REFINEMENT`
  morphism from child to parent: the child *refines* the parent's interface.
* **Covering families** — a ``{% include %}`` set forms a covering family
  where the included templates jointly cover the content region of the host.
* **Context sections** — the render-time context dict is a *fiber* transported
  along the inheritance chain; :class:`TemplateContextSection` models this
  fiber and :meth:`JinjaTemplateSite.context_descent_check` verifies that
  all referenced variables are present (no undefined-variable obstruction).
"""

from __future__ import annotations

__all__ = [
    "TemplateNodeKind",
    "JinjaBlock",
    "JinjaTemplate",
    "JinjaTemplateSite",
    "TemplateContextSection",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
)
from jugeo.geometry.descent import LocalSection, DescentResult


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TemplateNodeKind(str, Enum):
    """Kinds of node that appear in a Jinja2 template graph."""

    BASE = "base"
    CHILD = "child"
    MACRO = "macro"
    PARTIAL = "partial"
    BLOCK = "block"
    VARIABLE = "variable"
    CONTROL_IF = "control_if"
    CONTROL_FOR = "control_for"
    CONTROL_WITH = "control_with"
    INCLUDE = "include"
    IMPORT = "import"


# ---------------------------------------------------------------------------
# JinjaBlock — {% block %} as a coordinate
# ---------------------------------------------------------------------------


@dataclass
class JinjaBlock:
    """A ``{% block %}`` region — a named coordinate inside a template.

    Parameters
    ----------
    block_name:
        The identifier given to the block in the template source.
    template_name:
        The template file this block is *defined* in.
    default_content:
        The verbatim default content of the block, or ``None`` when the block
        is empty/abstract.
    is_required:
        When ``True`` the block has no default content and child templates
        *must* override it (analogous to an abstract method).
    scoped:
        When ``True`` the block was declared ``{% block foo scoped %}`` and
        the surrounding context variables are visible inside the override.
    """

    block_name: str
    template_name: str
    default_content: str | None = None
    is_required: bool = False
    scoped: bool = False

    def to_coordinate(self) -> Coordinate:
        """Return the site coordinate for this block.

        The coordinate name is ``"<template_name>.<block_name>"`` so that
        blocks from different templates never collide.
        """
        return Coordinate(
            components=(f"{self.template_name}.{self.block_name}",),
            kind=CoordinateKind.REGION,
            metadata={
                "template": self.template_name,
                "block": self.block_name,
                "is_required": self.is_required,
                "scoped": self.scoped,
            },
        )


# ---------------------------------------------------------------------------
# JinjaTemplate — a template file as a site object
# ---------------------------------------------------------------------------


@dataclass
class JinjaTemplate:
    """A Jinja2 template file modelled as a site coordinate.

    Parameters
    ----------
    template_name:
        The path/name used to identify the template (e.g. ``"base.html"``).
    extends:
        The parent template name when a ``{% extends %}`` directive is present,
        or ``None`` for base templates.
    blocks:
        All ``{% block %}`` definitions declared in this template.
    macros:
        Names of ``{% macro %}`` blocks defined here.
    includes:
        Template names referenced via ``{% include %}``.
    imports:
        ``(template_name, macro_name)`` pairs brought in via ``{% import %}``.
    context_vars:
        Variable names referenced in this template's render context.
    """

    template_name: str
    extends: str | None = None
    blocks: list[JinjaBlock] = field(default_factory=list)
    macros: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    imports: list[tuple[str, str]] = field(default_factory=list)
    context_vars: set[str] = field(default_factory=set)

    def to_coordinate(self) -> Coordinate:
        """Return the site coordinate for this template."""
        kind = CoordinateKind.MODULE if self.is_base() else CoordinateKind.REGION
        return Coordinate(
            components=(self.template_name,),
            kind=kind,
            metadata={
                "extends": self.extends,
                "block_count": len(self.blocks),
                "has_macros": bool(self.macros),
            },
        )

    def is_base(self) -> bool:
        """Return ``True`` when this template has no ``{% extends %}`` clause."""
        return self.extends is None

    def overridden_blocks(self) -> list[str]:
        """Return the names of blocks that override a parent block.

        A block *overrides* when the template is a child (has an ``extends``
        clause) and the block name matches a block that a parent template would
        expose.  We return all block names defined by a child — the caller is
        responsible for comparing against the parent's block list.
        """
        if self.is_base():
            return []
        return [b.block_name for b in self.blocks]


# ---------------------------------------------------------------------------
# JinjaTemplateSite — the Grothendieck site
# ---------------------------------------------------------------------------


class JinjaTemplateSite:
    """Grothendieck site whose objects are Jinja2 templates.

    The site captures the full template-inheritance graph:

    * Each :class:`JinjaTemplate` becomes a :class:`Coordinate` in the
      underlying :class:`Site`.
    * Each ``{% extends %}`` relationship becomes a
      :attr:`MorphismKind.REFINEMENT` morphism from child to parent.
    * Each ``{% include %}`` set becomes a :class:`CoveringFamily`.
    * Each :class:`JinjaBlock` gets its own ``REGION`` coordinate linked to
      its template via an :attr:`MorphismKind.INCLUSION` morphism.
    """

    def __init__(self) -> None:
        self._site: Site = Site(label="jinja-template-site")
        self._templates: dict[str, JinjaTemplate] = {}

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_template(self, template: JinjaTemplate) -> None:
        """Register *template* in the site, adding its coordinate and blocks."""
        self._templates[template.template_name] = template
        tmpl_coord = template.to_coordinate()
        self._site.add_coordinate(tmpl_coord)

        for block in template.blocks:
            block_coord = block.to_coordinate()
            self._site.add_coordinate(block_coord)
            # Block is contained in its template — model as INCLUSION.
            self._site.add_morphism(
                Morphism(
                    source=block_coord,
                    target=tmpl_coord,
                    kind=MorphismKind.INCLUSION,
                    label=f"block:{block.block_name}@{template.template_name}",
                )
            )

    def inheritance_morphism(self, child: str, parent: str) -> Morphism:
        """Register and return the ``{% extends %}`` morphism child → parent.

        The ``{% extends %}`` directive is a *refinement*: the child template
        specialises the parent's interface, overriding blocks.  A
        :attr:`MorphismKind.REFINEMENT` morphism from the child coordinate to
        the parent coordinate captures this relationship.

        Raises
        ------
        KeyError
            If either *child* or *parent* has not been added to the site.
        """
        child_coord = self._templates[child].to_coordinate()
        parent_coord = self._templates[parent].to_coordinate()
        morph = Morphism(
            source=child_coord,
            target=parent_coord,
            kind=MorphismKind.REFINEMENT,
            label=f"extends:{child}->{parent}",
        )
        self._site.add_morphism(morph)
        return morph

    def include_covering(
        self, host: str, included: list[str]
    ) -> CoveringFamily:
        """Build and register a covering family for a ``{% include %}`` set.

        The *included* templates together cover the content region of the
        *host* template.  Each included template contributes an
        :attr:`MorphismKind.INCLUSION` morphism into the host coordinate.

        Raises
        ------
        KeyError
            If *host* or any name in *included* has not been added to the site.
        """
        host_coord = self._templates[host].to_coordinate()
        members: list[Morphism] = []
        for name in included:
            inc_coord = self._templates[name].to_coordinate()
            morph = Morphism(
                source=inc_coord,
                target=host_coord,
                kind=MorphismKind.INCLUSION,
                label=f"include:{name}->{host}",
            )
            self._site.add_morphism(morph)
            members.append(morph)

        family = CoveringFamily(
            base=host_coord,
            members=members,
            label=f"include-cover:{host}",
        )
        self._site.add_covering_family(family)
        return family

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def inheritance_chain(self, child: str) -> list[str]:
        """Return the MRO from *child* up to the root base template.

        The list starts with *child* and ends with the base template that has
        no ``{% extends %}`` clause.  Stops early if a template is not
        registered (graceful degradation).

        Raises
        ------
        ValueError
            If a circular inheritance chain is detected.
        """
        chain: list[str] = []
        visited: set[str] = set()
        current: str | None = child
        while current is not None:
            if current in visited:
                raise ValueError(
                    f"Circular inheritance detected at '{current}' "
                    f"while building chain for '{child}'"
                )
            chain.append(current)
            visited.add(current)
            tmpl = self._templates.get(current)
            current = tmpl.extends if tmpl is not None else None
        return chain

    def block_resolution(self, block_name: str, child: str) -> str:
        """Return the name of the template whose block content takes effect.

        Walks the inheritance chain from *child* toward the base template and
        returns the first template in that chain that *defines* a block with
        the given *block_name*.  The child's own definition wins; if none is
        found, the name of the base template is returned as a fallback.
        """
        chain = self.inheritance_chain(child)
        for tmpl_name in chain:
            tmpl = self._templates.get(tmpl_name)
            if tmpl is None:
                continue
            if any(b.block_name == block_name for b in tmpl.blocks):
                return tmpl_name
        # No template in chain defines the block — return base as fallback.
        return chain[-1] if chain else child

    def context_descent_check(
        self, template: str, provided_vars: set[str]
    ) -> DescentResult:
        """Check that *provided_vars* satisfies the context needs of *template*.

        Walks the full inheritance chain and collects every ``context_var``
        referenced by any template in the chain.  If all required variables
        are present in *provided_vars*, descent succeeds.  Otherwise a
        :class:`DescentObstruction` is returned whose ``partial_section``
        records the missing names.

        Returns a :class:`DescentResult` success when all variables are
        provided, or failure with an obstruction identifying the gaps.
        """
        from jugeo.geometry.descent import GlobalSection, DescentObstruction

        chain = self.inheritance_chain(template)
        required: set[str] = set()
        for tmpl_name in chain:
            tmpl = self._templates.get(tmpl_name)
            if tmpl is not None:
                required.update(tmpl.context_vars)

        missing = required - provided_vars

        if not missing:
            gs = GlobalSection(
                coordinate=template,
                merged_judgment={v: "provided" for v in provided_vars},
                trust_floor=1.0,
            )
            return DescentResult(_global_section=gs)

        obstruction = DescentObstruction(
            coordinate=template,
            partial_section={
                "missing_vars": sorted(missing),
                "provided_vars": sorted(provided_vars),
            },
        )
        return DescentResult(_obstruction=obstruction)

    def detect_circular_inheritance(self) -> list[list[str]]:
        """Return a list of cycles in the ``{% extends %}`` graph.

        Each element of the returned list is a cycle expressed as a sequence
        of template names.  Returns an empty list when no cycles exist.
        """
        cycles: list[list[str]] = []
        visited_global: set[str] = set()

        for start in self._templates:
            if start in visited_global:
                continue
            path: list[str] = []
            path_set: set[str] = set()
            current: str | None = start

            while current is not None:
                if current in path_set:
                    # Found a cycle — extract the loop portion.
                    cycle_start = path.index(current)
                    cycles.append(path[cycle_start:])
                    break
                if current in visited_global:
                    break
                path.append(current)
                path_set.add(current)
                tmpl = self._templates.get(current)
                current = tmpl.extends if tmpl is not None else None

            visited_global.update(path)

        return cycles


# ---------------------------------------------------------------------------
# TemplateContextSection — render-time context as a fiber
# ---------------------------------------------------------------------------


@dataclass
class TemplateContextSection:
    """The render-time context dictionary modelled as a fiber over a template.

    In the Grothendieck-site picture the context dict is a *local section*
    over the template coordinate: it assigns data to each variable reference
    coordinate.  :meth:`to_local_section` converts to the geometry layer's
    :class:`LocalSection` so that standard descent machinery can be applied.

    Parameters
    ----------
    template_name:
        The template this context is paired with.
    variables:
        The concrete variable bindings passed to the renderer.
    """

    template_name: str
    variables: dict[str, Any] = field(default_factory=dict)

    def to_local_section(self) -> LocalSection:
        """Return a :class:`LocalSection` representing this context fiber."""
        return LocalSection(
            coordinate=self.template_name,
            judgment_data=dict(self.variables),
            trust_level=1.0,
            provenance=("render-context",),
        )

    def satisfies_template(
        self, template: JinjaTemplate
    ) -> tuple[bool, list[str]]:
        """Check whether this context provides all variables the template needs.

        Returns
        -------
        ok:
            ``True`` when every ``context_var`` in *template* is present.
        missing_vars:
            The list of variable names absent from :attr:`variables`.
        """
        missing = [v for v in template.context_vars if v not in self.variables]
        return (len(missing) == 0, missing)
