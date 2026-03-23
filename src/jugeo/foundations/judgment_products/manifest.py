"""Package manifest for jugeo.foundations.judgment_products.

Theory2.tex Chapter 5: Judgments, Sections, and Semantic Products.

This module declares the complete component inventory for the
``judgment_products`` package — the layer that treats verification
output not as boolean pass/fail flags but as structured, composable
*semantic products*: algebraic values carrying full provenance,
residual obligations, and section data that can be compared,
projected, refined, and glued.

Design Principles
-----------------
1. **Judgment as product**: A verified judgment produces a rich semantic
   value — not merely ``True``.  The product records what was shown,
   how it was shown, what remains open, and how the evidence relates
   to the overall sheaf.

2. **Residuals are alive**: Unresolved sub-goals survive as first-class
   ``ResidualObligation`` objects, carried forward in the product, not
   discarded.

3. **Sections as witnesses**: The canonical output of a successful
   gluing is a *global section* — not an approval status.

4. **Comparisons are morphisms**: Refinement and equivalence between
   judgments are structure-preserving maps with explicit witnesses, not
   opaque boolean results.

Exports
-------
See ``__all__`` for the authoritative list of public names.  All names
are re-exported from the package ``__init__`` for convenient top-level
access.

References
----------
theory2.tex §5.1 – §5.4 (Semantic Products, Residual Systems, Section
Products, Comparison Maps).

# copilot: package manifest for judgment_products — Theory2.tex Ch5.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Mapping, Sequence

# ---------------------------------------------------------------------------
# Version and identity
# ---------------------------------------------------------------------------

PACKAGE_NAME: Final[str] = "jugeo.foundations.judgment_products"
PACKAGE_VERSION: Final[str] = "0.5.0"
THEORY_CHAPTER: Final[str] = "Theory2.tex §5"
PACKAGE_DESCRIPTION: Final[str] = (
    "Semantic products of judgment verification: algebraic composition, "
    "residual obligation tracking, section witnesses, and comparison morphisms."
)

# Minimum Python version required by this package.
MINIMUM_PYTHON: Final[tuple[int, int]] = (3, 11)


def _check_python_version() -> None:
    """Raise ``RuntimeError`` if the running interpreter is too old."""
    if sys.version_info[:2] < MINIMUM_PYTHON:
        raise RuntimeError(
            f"{PACKAGE_NAME} requires Python >= {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}, "
            f"got {sys.version_info.major}.{sys.version_info.minor}."
        )


_check_python_version()


# ---------------------------------------------------------------------------
# Component categories
# ---------------------------------------------------------------------------


class ComponentKind(str, Enum):
    """High-level category of a registered component.

    Members
    -------
    MODEL
        A data model or domain object (dataclass, frozen dataclass, etc.).
    ALGORITHM
        A pure-function or static-method algorithm operating on models.
    INTEGRATION
        An adapter that connects this package to another jugeo sub-package.
    THEOREM
        A formal property statement or proof obligation.
    UTILITY
        Internal helper (not part of the public API).
    """

    MODEL = "model"
    ALGORITHM = "algorithm"
    INTEGRATION = "integration"
    THEOREM = "theorem"
    UTILITY = "utility"


class Stability(str, Enum):
    """API stability contract for a registered component.

    Members
    -------
    STABLE
        Backwards-incompatible changes follow semver MAJOR bumps.
    EXPERIMENTAL
        May change in any minor release; opt-in by explicit import.
    INTERNAL
        Not part of the public API; subject to change without notice.
    DEPRECATED
        Scheduled for removal; replacement documented in ``notes``.
    """

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    INTERNAL = "internal"
    DEPRECATED = "deprecated"


# ---------------------------------------------------------------------------
# Component descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentDescriptor:
    """A self-describing entry in the package component registry.

    Parameters
    ----------
    name:
        Fully-qualified Python name (``module.ClassName`` or
        ``module.function_name``).
    kind:
        Category classification.
    module:
        Dotted module path within this package (e.g. ``models``).
    stability:
        API stability contract.
    summary:
        One-sentence description for tooling / documentation.
    theory_ref:
        Citation in theory2.tex (e.g. ``§5.2 Def 3``).
    depends_on:
        Names of other components this one depends on, in topological
        order (used for import-order validation and dependency graphing).
    notes:
        Free-text notes — e.g. deprecation notice, migration guide.
    tags:
        Arbitrary string tags for filtering/search.
    """

    name: str
    kind: ComponentKind
    module: str
    stability: Stability = Stability.STABLE
    summary: str = ""
    theory_ref: str = ""
    depends_on: tuple[str, ...] = ()
    notes: str = ""
    tags: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def qualified_module(self) -> str:
        """Return the fully-qualified module path.

        Returns
        -------
        str
            E.g. ``jugeo.foundations.judgment_products.models``.
        """
        return f"{PACKAGE_NAME}.{self.module}"

    def is_public(self) -> bool:
        """Return ``True`` iff the component is part of the public API.

        Returns
        -------
        bool
            ``True`` for STABLE and EXPERIMENTAL; ``False`` for INTERNAL
            and DEPRECATED.
        """
        return self.stability in (Stability.STABLE, Stability.EXPERIMENTAL)

    def dependency_names(self) -> tuple[str, ...]:
        """Return the tuple of dependency names unchanged.

        Returns
        -------
        tuple[str, ...]
            Same as ``self.depends_on``.
        """
        return self.depends_on

    def with_note(self, extra: str) -> "ComponentDescriptor":
        """Return a copy with ``extra`` appended to ``notes``.

        Parameters
        ----------
        extra:
            Additional note text to append.

        Returns
        -------
        ComponentDescriptor
            A new frozen descriptor with the extended notes field.
        """
        sep = "\n" if self.notes else ""
        from dataclasses import replace
        return replace(self, notes=self.notes + sep + extra)

    def matches_tag(self, tag: str) -> bool:
        """Return ``True`` if *tag* is present in ``self.tags``.

        Parameters
        ----------
        tag:
            Tag string to search for.

        Returns
        -------
        bool
        """
        return tag in self.tags

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for JSON export.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "module": self.qualified_module(),
            "stability": self.stability.value,
            "summary": self.summary,
            "theory_ref": self.theory_ref,
            "depends_on": list(self.depends_on),
            "notes": self.notes,
            "tags": list(self.tags),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ComponentRegistry:
    """Immutable registry of all components in the judgment_products package.

    Construction
    ------------
    Call :meth:`from_descriptors` to build a registry from a sequence of
    ``ComponentDescriptor`` objects.  The class is otherwise not directly
    instantiated.

    Lookup
    ------
    Use :meth:`get`, :meth:`by_kind`, :meth:`by_module`, or :meth:`by_tag`
    to query the registry.  All methods return copies / filtered views;
    the underlying data is immutable.

    Parameters
    ----------
    _entries:
        Ordered tuple of ``ComponentDescriptor`` instances.  Populated
        exclusively by :meth:`from_descriptors`.

    Notes
    -----
    Duplicate names raise ``ValueError`` at construction time so that
    mis-configuration is caught eagerly rather than silently shadowed.
    """

    def __init__(self, entries: Sequence[ComponentDescriptor]) -> None:
        seen: set[str] = set()
        validated: list[ComponentDescriptor] = []
        for entry in entries:
            if entry.name in seen:
                raise ValueError(
                    f"Duplicate component name in registry: {entry.name!r}"
                )
            seen.add(entry.name)
            validated.append(entry)
        self._entries: tuple[ComponentDescriptor, ...] = tuple(validated)
        self._by_name: dict[str, ComponentDescriptor] = {
            e.name: e for e in self._entries
        }

    @classmethod
    def from_descriptors(
        cls, descriptors: Sequence[ComponentDescriptor]
    ) -> "ComponentRegistry":
        """Construct a registry from a sequence of descriptors.

        Parameters
        ----------
        descriptors:
            Ordered sequence of ``ComponentDescriptor`` objects.

        Returns
        -------
        ComponentRegistry

        Raises
        ------
        ValueError
            If any name appears more than once.
        """
        return cls(descriptors)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> ComponentDescriptor | None:
        """Look up a component by name.

        Parameters
        ----------
        name:
            The ``ComponentDescriptor.name`` to look up.

        Returns
        -------
        ComponentDescriptor | None
            The descriptor, or ``None`` if not found.
        """
        return self._by_name.get(name)

    def require(self, name: str) -> ComponentDescriptor:
        """Look up a component by name, raising if absent.

        Parameters
        ----------
        name:
            The component name.

        Returns
        -------
        ComponentDescriptor

        Raises
        ------
        KeyError
            If the name is not registered.
        """
        entry = self._by_name.get(name)
        if entry is None:
            raise KeyError(f"Component not found in registry: {name!r}")
        return entry

    def by_kind(self, kind: ComponentKind) -> tuple[ComponentDescriptor, ...]:
        """Return all descriptors with the given *kind*.

        Parameters
        ----------
        kind:
            The ``ComponentKind`` to filter by.

        Returns
        -------
        tuple[ComponentDescriptor, ...]
        """
        return tuple(e for e in self._entries if e.kind == kind)

    def by_module(self, module: str) -> tuple[ComponentDescriptor, ...]:
        """Return all descriptors defined in *module*.

        Parameters
        ----------
        module:
            The short module name (e.g. ``"models"``).

        Returns
        -------
        tuple[ComponentDescriptor, ...]
        """
        return tuple(e for e in self._entries if e.module == module)

    def by_tag(self, tag: str) -> tuple[ComponentDescriptor, ...]:
        """Return all descriptors carrying *tag*.

        Parameters
        ----------
        tag:
            Tag string to match.

        Returns
        -------
        tuple[ComponentDescriptor, ...]
        """
        return tuple(e for e in self._entries if e.matches_tag(tag))

    def public_components(self) -> tuple[ComponentDescriptor, ...]:
        """Return all descriptors with public stability contracts.

        Returns
        -------
        tuple[ComponentDescriptor, ...]
        """
        return tuple(e for e in self._entries if e.is_public())

    def all_names(self) -> tuple[str, ...]:
        """Return the names of every registered component.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(self._by_name)

    def dependency_order(self) -> list[str]:
        """Return component names in a dependency-stable topological order.

        A simple iterative Kahn's algorithm is used.  Cycles are broken
        by keeping the first occurrence of each repeated name.

        Returns
        -------
        list[str]
            Component names ordered so each appears after all of its
            ``depends_on`` predecessors.

        Raises
        ------
        ValueError
            If a dependency cycle is detected.
        """
        in_degree: dict[str, int] = {e.name: 0 for e in self._entries}
        dependents: dict[str, list[str]] = {e.name: [] for e in self._entries}
        for entry in self._entries:
            for dep in entry.depends_on:
                if dep in dependents:
                    dependents[dep].append(entry.name)
                    in_degree[entry.name] += 1
        queue = [n for n, d in in_degree.items() if d == 0]
        result: list[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for dep_name in dependents[node]:
                in_degree[dep_name] -= 1
                if in_degree[dep_name] == 0:
                    queue.append(dep_name)
        if len(result) != len(self._entries):
            raise ValueError("Dependency cycle detected in component registry.")
        return result

    def to_manifest_dict(self) -> dict[str, Any]:
        """Serialise the full registry to a dictionary for JSON/YAML export.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "package": PACKAGE_NAME,
            "version": PACKAGE_VERSION,
            "theory": THEORY_CHAPTER,
            "components": [e.to_mapping() for e in self._entries],
        }

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return (
            f"ComponentRegistry(package={PACKAGE_NAME!r}, "
            f"components={len(self._entries)})"
        )


# ---------------------------------------------------------------------------
# Component declarations
# ---------------------------------------------------------------------------

_COMPONENTS: list[ComponentDescriptor] = [
    # models.py
    ComponentDescriptor(
        name="models.JudgmentProduct",
        kind=ComponentKind.MODEL,
        module="models",
        summary="The semantic product of composing multiple judgments.",
        theory_ref="§5.1 Def 1",
        tags=("core", "product"),
    ),
    ComponentDescriptor(
        name="models.SemanticProduct",
        kind=ComponentKind.MODEL,
        module="models",
        summary="A typed container for semantic verification output.",
        theory_ref="§5.1 Def 2",
        depends_on=("models.JudgmentProduct",),
        tags=("core", "product"),
    ),
    ComponentDescriptor(
        name="models.LocalJudgmentSection",
        kind=ComponentKind.MODEL,
        module="models",
        summary="Section model local to the judgment_products package.",
        theory_ref="§5.3",
        depends_on=("models.JudgmentProduct",),
        tags=("section",),
    ),
    ComponentDescriptor(
        name="models.ComparisonMap",
        kind=ComponentKind.MODEL,
        module="models",
        summary="Structure-preserving map between two judgment products.",
        theory_ref="§5.4 Def 1",
        depends_on=("models.JudgmentProduct",),
        tags=("comparison",),
    ),
    ComponentDescriptor(
        name="models.ExplanationProjection",
        kind=ComponentKind.MODEL,
        module="models",
        summary="Projection from a judgment product to a human-readable explanation.",
        theory_ref="§5.4 Def 2",
        depends_on=("models.JudgmentProduct", "models.ComparisonMap"),
        tags=("explanation",),
    ),
    # s01
    ComponentDescriptor(
        name="s01.JudgmentAsObject",
        kind=ComponentKind.MODEL,
        module="judgments_are_not_boolean_facts",
        summary="Treats a Judgment as a first-class semantic object, not a boolean.",
        theory_ref="§5.1",
        tags=("core", "algebra"),
    ),
    ComponentDescriptor(
        name="s01.NonBooleanJudgment",
        kind=ComponentKind.MODEL,
        module="judgments_are_not_boolean_facts",
        summary="Judgment carrying structured truth values beyond True/False.",
        theory_ref="§5.1 Prop 1",
        depends_on=("s01.JudgmentAsObject",),
        tags=("core", "algebra"),
    ),
    ComponentDescriptor(
        name="s01.StructuredJudgment",
        kind=ComponentKind.MODEL,
        module="judgments_are_not_boolean_facts",
        summary="A judgment with explicit structural decomposition.",
        theory_ref="§5.1 Prop 2",
        depends_on=("s01.NonBooleanJudgment",),
        tags=("core", "structure"),
    ),
    ComponentDescriptor(
        name="s01.JudgmentComparison",
        kind=ComponentKind.MODEL,
        module="judgments_are_not_boolean_facts",
        summary="Pairwise comparison result for two JudgmentAsObject instances.",
        theory_ref="§5.1 §5.4",
        depends_on=("s01.JudgmentAsObject",),
        tags=("comparison",),
    ),
    ComponentDescriptor(
        name="s01.JudgmentProductAlgebra",
        kind=ComponentKind.ALGORITHM,
        module="judgments_are_not_boolean_facts",
        summary="Algebraic operations treating judgments as non-boolean objects.",
        theory_ref="§5.1 Thm 1",
        depends_on=("s01.JudgmentAsObject", "s01.StructuredJudgment"),
        tags=("core", "algebra"),
    ),
    # s02
    ComponentDescriptor(
        name="s02.ResidualObligation",
        kind=ComponentKind.MODEL,
        module="residual_obligations_are_the_livin",
        summary="A live, unresolved verification obligation in the semantic system.",
        theory_ref="§5.2 Def 1",
        tags=("residual",),
    ),
    ComponentDescriptor(
        name="s02.ObligationTracker",
        kind=ComponentKind.MODEL,
        module="residual_obligations_are_the_livin",
        summary="Tracks the lifecycle of residual obligations across judgment products.",
        theory_ref="§5.2",
        depends_on=("s02.ResidualObligation",),
        tags=("residual",),
    ),
    ComponentDescriptor(
        name="s02.ResidualDischarger",
        kind=ComponentKind.ALGORITHM,
        module="residual_obligations_are_the_livin",
        summary="Attempts to discharge residual obligations using available evidence.",
        theory_ref="§5.2 Prop 3",
        depends_on=("s02.ResidualObligation", "s02.ObligationTracker"),
        tags=("residual", "algorithm"),
    ),
    ComponentDescriptor(
        name="s02.ResidualPropagator",
        kind=ComponentKind.ALGORITHM,
        module="residual_obligations_are_the_livin",
        summary="Propagates residuals through composition and restriction maps.",
        theory_ref="§5.2 Prop 4",
        depends_on=("s02.ResidualDischarger",),
        tags=("residual", "algorithm"),
    ),
    # s03
    ComponentDescriptor(
        name="s03.SectionProduct",
        kind=ComponentKind.MODEL,
        module="sections_are_the_real_products_of",
        summary="A verification section produced by a successfully glued family.",
        theory_ref="§5.3 Def 1",
        tags=("section", "product"),
    ),
    ComponentDescriptor(
        name="s03.GlobalSection",
        kind=ComponentKind.MODEL,
        module="sections_are_the_real_products_of",
        summary="A section that extends consistently to the entire base site.",
        theory_ref="§5.3 Def 2",
        depends_on=("s03.SectionProduct",),
        tags=("section", "global"),
    ),
    ComponentDescriptor(
        name="s03.SectionFunctor",
        kind=ComponentKind.ALGORITHM,
        module="sections_are_the_real_products_of",
        summary="Functorial mapping on section products along coordinate morphisms.",
        theory_ref="§5.3 §2.4",
        depends_on=("s03.SectionProduct", "s03.GlobalSection"),
        tags=("section", "functor"),
    ),
    ComponentDescriptor(
        name="s03.SectionComparison",
        kind=ComponentKind.MODEL,
        module="sections_are_the_real_products_of",
        summary="Comparison morphism between two section products.",
        theory_ref="§5.3 §5.4",
        depends_on=("s03.SectionProduct",),
        tags=("section", "comparison"),
    ),
    # s04
    ComponentDescriptor(
        name="s04.ComparisonMap",
        kind=ComponentKind.MODEL,
        module="comparison_maps_and_explanation_pr",
        summary="Explicit structure-preserving map between judgment products.",
        theory_ref="§5.4 Def 1",
        tags=("comparison", "map"),
    ),
    ComponentDescriptor(
        name="s04.ExplanationProjection",
        kind=ComponentKind.MODEL,
        module="comparison_maps_and_explanation_pr",
        summary="Projects a judgment product to a structured natural-language explanation.",
        theory_ref="§5.4 Def 2",
        depends_on=("s04.ComparisonMap",),
        tags=("explanation", "projection"),
    ),
    ComponentDescriptor(
        name="s04.RefinementWitness",
        kind=ComponentKind.MODEL,
        module="comparison_maps_and_explanation_pr",
        summary="Explicit witness certifying one judgment refines another.",
        theory_ref="§5.4 Prop 1",
        depends_on=("s04.ComparisonMap",),
        tags=("refinement", "witness"),
    ),
    ComponentDescriptor(
        name="s04.EquivalenceCertificate",
        kind=ComponentKind.MODEL,
        module="comparison_maps_and_explanation_pr",
        summary="Certificate of semantic equivalence between two judgment products.",
        theory_ref="§5.4 Prop 2",
        depends_on=("s04.RefinementWitness",),
        tags=("equivalence", "certificate"),
    ),
    # algorithms.py
    ComponentDescriptor(
        name="algorithms.JudgmentAlgorithms",
        kind=ComponentKind.ALGORITHM,
        module="algorithms",
        summary="Collection of core algorithms for judgment product computation.",
        theory_ref="§5 (all)",
        depends_on=(
            "models.JudgmentProduct",
            "s02.ResidualDischarger",
            "s04.ComparisonMap",
            "s04.ExplanationProjection",
        ),
        tags=("algorithm", "core"),
    ),
    # integration.py
    ComponentDescriptor(
        name="integration.JudgmentIntegration",
        kind=ComponentKind.INTEGRATION,
        module="integration",
        summary="Connects judgment_products to the jugeo.judgments package.",
        theory_ref="§5",
        depends_on=("algorithms.JudgmentAlgorithms",),
        tags=("integration",),
    ),
    # theorems.py
    ComponentDescriptor(
        name="theorems.JudgmentTheorems",
        kind=ComponentKind.THEOREM,
        module="theorems",
        summary="Formal properties and proof obligations for judgment products.",
        theory_ref="§5 Thm 1–7",
        depends_on=(
            "models.JudgmentProduct",
            "s03.GlobalSection",
            "s04.EquivalenceCertificate",
        ),
        tags=("theorem", "formal"),
    ),
]

#: The authoritative package component registry.
REGISTRY: Final[ComponentRegistry] = ComponentRegistry.from_descriptors(_COMPONENTS)


# ---------------------------------------------------------------------------
# Dependency declarations for upstream package imports
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UpstreamDependency:
    """A declared dependency on a jugeo sub-package or external library.

    Parameters
    ----------
    package:
        Dotted import path of the dependency (e.g.
        ``jugeo.judgments.judgment_terms``).
    required_names:
        Names imported from ``package``.
    optional:
        If ``True``, the dependency is wrapped in a try/except and
        stubs are provided on import failure.
    notes:
        Free-text notes about how the dependency is used.
    """

    package: str
    required_names: tuple[str, ...]
    optional: bool = False
    notes: str = ""

    def import_statement(self) -> str:
        """Generate the ``from … import …`` string for this dependency.

        Returns
        -------
        str
        """
        names = ", ".join(self.required_names)
        return f"from {self.package} import {names}"


UPSTREAM_DEPENDENCIES: Final[tuple[UpstreamDependency, ...]] = (
    UpstreamDependency(
        package="jugeo.judgments.judgment_terms",
        required_names=(
            "Judgment",
            "LocalJudgment",
            "JudgmentAlgebra",
            "ResidualObligation",
            "Obstruction",
            "TrustAnnotation",
            "Provenance",
            "EvidenceBundle",
            "Proposition",
            "Carrier",
            "JudgmentStatus",
            "TrustLevel",
            "ProvenanceSource",
            "PropositionKind",
            "JudgmentBuilder",
            "JudgmentClause",
        ),
        notes="Core term algebra — the 8-component judgment tuple.",
    ),
    UpstreamDependency(
        package="jugeo.judgments.sections",
        required_names=(
            "Section",
            "JudgmentSection",
            "SectionFamily",
            "SectionGluing",
            "SectionComparator",
            "GluingStatus",
            "SectionBuilder",
            "SectionCache",
        ),
        notes="Sheaf section layer — restriction, gluing, transport.",
    ),
    UpstreamDependency(
        package="jugeo.judgments.comparisons",
        required_names=(
            "ComparisonMode",
            "ComparisonResult",
            "compare_sections",
        ),
        notes="Comparison helpers for judgment sections.",
    ),
)


# ---------------------------------------------------------------------------
# Public __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Identity
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "THEORY_CHAPTER",
    "PACKAGE_DESCRIPTION",
    "MINIMUM_PYTHON",
    # Enumerations
    "ComponentKind",
    "Stability",
    # Data model
    "ComponentDescriptor",
    # Registry
    "ComponentRegistry",
    "REGISTRY",
    # Dependency declarations
    "UpstreamDependency",
    "UPSTREAM_DEPENDENCIES",
]

# copilot: package manifest for judgment_products — Theory2.tex Ch5.
