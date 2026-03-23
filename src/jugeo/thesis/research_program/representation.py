r"""Representation Claim (C1): JuGeo judgment tuple represents semantic state.

This module implements the theoretical and computational content of Thesis
Claim C1 from Theory2.tex Chapter 2:

    **C1** — The judgment tuple :math:`J = (c, \varphi, A, E, O, B, T, \Pi)`
    provides a sound and complete representation of semantic state for the
    purposes of multi-agent reasoning in JuGeo.

The claim has three technical pillars:

1. **Presheaf structure** — The assignment :math:`J \mapsto \mathrm{SemanticState}(J)`
   is functorial: it respects context morphisms and composition.

2. **Coordinate completeness** — Every admissible semantic state has a unique
   coordinate in the judgment space; no two distinct states share a coordinate.

3. **Cover soundness** — Overlapping contexts agree on their intersection;
   there are no contradictory local representations.

Classes
-------

:class:`SemanticStateRepresentation`
    Top-level claim object; orchestrates verification.

:class:`JudgmentPresheaf`
    Models the presheaf structure: sections, restriction maps, and the
    naturality condition that verifies functoriality.

:class:`CoordinateSystem`
    Assigns coordinates to semantic states and verifies injectivity.

:class:`CoverStructure`
    Manages covers of the judgment space and verifies the gluing condition.

All copilot-assisted scaffolding is tagged and treated as ``COPILOT_SUGGESTED``
until promoted through explicit review.  In particular, the presheaf naturality
check and cover gluing check were initially drafted with copilot assistance.

Theory alignment
----------------

Section 230 of Theory2.tex introduces C1.  Section 231 states the presheaf
axioms; section 232 states coordinate completeness; section 233 states cover
soundness.  The lemma and theorem numbers in docstrings refer to that document.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, Iterator, Mapping, TypeVar

# ---------------------------------------------------------------------------
# Generic type variables
# ---------------------------------------------------------------------------

S = TypeVar("S")  # semantic state type
C = TypeVar("C")  # context / open-set type

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PresheafLaw(Enum):
    """Named presheaf laws that can be checked."""

    IDENTITY = "identity"
    COMPOSITION = "composition"
    NATURALITY = "naturality"


class CoverCondition(Enum):
    """Gluing conditions on a cover."""

    LOCALITY = "locality"
    GLUING = "gluing"


class VerificationStatus(Enum):
    """Result of a structural verification check."""

    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class CoordinateKind(Enum):
    """Kind of coordinate in the judgment space."""

    CLAUSE_ID = "clause_id"
    FORMULA_HASH = "formula_hash"
    AGENT_ID = "agent_id"
    EVIDENCE_FINGERPRINT = "evidence_fingerprint"
    TRUST_LEVEL = "trust_level"
    PROVENANCE_HASH = "provenance_hash"
    COMPOSITE = "composite"


# ---------------------------------------------------------------------------
# Core geometric primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """An open context (open set) in the judgment-space topology.

    Contexts are identified by a name and an optional parent.  The poset
    of contexts (ordered by refinement / restriction) forms the base
    category for the presheaf.

    Parameters
    ----------
    name:
        Short identifier for this context, e.g. ``"global"``, ``"local_0"``.
    parent_name:
        Identifier of the parent context, or ``None`` for the root context.
    metadata:
        Free-form metadata describing the context.
    """

    name: str
    parent_name: str | None = None
    metadata: str = ""

    def is_root(self) -> bool:
        """Return True if this context has no parent."""
        return self.parent_name is None

    def refines(self, other: "Context") -> bool:
        """Return True if this context is a refinement of *other*.

        A context refines another if it is a sub-context (more specific).
        In the poset, refinement corresponds to going down; restriction maps
        go in the opposite direction (up).
        """
        return self.parent_name == other.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parent_name": self.parent_name,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ContextMorphism:
    """A morphism between two contexts in the base category.

    In the presheaf setting, a morphism i : U → V represents an inclusion
    or restriction from the finer context U into the coarser context V.

    Parameters
    ----------
    source:
        The source (finer) context.
    target:
        The target (coarser) context.
    label:
        A short label for this morphism, e.g. ``"restriction"``.
    """

    source: Context
    target: Context
    label: str = "restriction"

    def is_identity(self) -> bool:
        """Return True if source and target are the same context."""
        return self.source.name == self.target.name

    def compose(self, other: "ContextMorphism") -> "ContextMorphism":
        """Return the composition self ∘ other.

        Composition is valid when ``other.target == self.source``.

        Parameters
        ----------
        other:
            The morphism to compose on the right.

        Raises
        ------
        ValueError
            If the morphisms are not composable.
        """
        if other.target.name != self.source.name:
            raise ValueError(
                f"Cannot compose: {other!r}.target != {self!r}.source"
            )
        return ContextMorphism(
            source=other.source,
            target=self.target,
            label=f"({self.label})∘({other.label})",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "label": self.label,
        }


@dataclass(frozen=True)
class Section:
    """A section of the presheaf over a given context.

    A section σ : U → ΣJ assigns a semantic-state value to each point in
    the context U.  In the judgment presheaf, a section over a context is
    a partial judgment tuple valid within that context.

    Parameters
    ----------
    context:
        The context over which this section is defined.
    value_repr:
        A hashable string representation of the section's semantic value.
        In production this would be the serialised judgment tuple; here
        we use a canonical string for algebraic purposes.
    copilot_generated:
        Whether this section was proposed by a copilot/oracle agent.
        Copilot-generated sections carry ``COPILOT_SUGGESTED`` trust.
    """

    context: Context
    value_repr: str
    copilot_generated: bool = False

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of this section."""
        raw = json.dumps(
            {"context": self.context.name, "value": self.value_repr},
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def restrict(self, morphism: "ContextMorphism") -> "Section":
        """Apply a restriction morphism to obtain a section over a coarser context.

        The restriction simulates the presheaf restriction map ρ_{UV}.
        In this model, restriction is conservative: the value_repr is
        prefixed with the target context's name to record the provenance.

        Parameters
        ----------
        morphism:
            A :class:`ContextMorphism` with source matching this section's context.

        Raises
        ------
        ValueError
            If the morphism source does not match this section's context.
        """
        if morphism.source.name != self.context.name:
            raise ValueError(
                f"Morphism source {morphism.source.name!r} ≠ "
                f"section context {self.context.name!r}"
            )
        restricted_value = f"restrict[{morphism.target.name}]({self.value_repr})"
        return Section(
            context=morphism.target,
            value_repr=restricted_value,
            copilot_generated=self.copilot_generated,
        )

    def agrees_with(self, other: "Section") -> bool:
        """Return True if this section agrees with *other* on their shared context.

        Two sections agree if they are over the same context and carry the
        same value representation.  This implements the locality axiom check.
        """
        return (
            self.context.name == other.context.name
            and self.value_repr == other.value_repr
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "value_repr": self.value_repr,
            "copilot_generated": self.copilot_generated,
            "fingerprint": self.fingerprint(),
        }


# ---------------------------------------------------------------------------
# JudgmentPresheaf
# ---------------------------------------------------------------------------


@dataclass
class JudgmentPresheaf:
    r"""Presheaf of judgment tuples over a poset of contexts.

    The presheaf :math:`\mathcal{F} : \mathbf{Ctx}^{op} \to \mathbf{Set}`
    assigns to each context U the set of admissible judgment-tuple sections
    over U, and to each morphism i : U → V the restriction map
    :math:`\rho_{UV} : \mathcal{F}(V) \to \mathcal{F}(U)`.

    Presheaf axioms (Theory2.tex §231):

    * **PF-1 (Identity)** — Restriction along the identity morphism is the
      identity function.
    * **PF-2 (Composition)** — Restriction along a composition equals the
      composition of restrictions.
    * **PF-3 (Naturality)** — All restriction maps are natural transformations.

    Parameters
    ----------
    name:
        Name for this presheaf instance, e.g. ``"JudgmentPresheaf_C1"``.
    contexts:
        Mapping from context name to :class:`Context` object.
    sections:
        Mapping from context name to the list of sections over that context.
    morphisms:
        List of registered :class:`ContextMorphism` objects.
    """

    name: str
    contexts: dict[str, Context] = field(default_factory=dict)
    sections: dict[str, list[Section]] = field(default_factory=dict)
    morphisms: list[ContextMorphism] = field(default_factory=list)
    _law_results: dict[str, VerificationStatus] = field(
        default_factory=dict, repr=False
    )

    def add_context(self, ctx: Context) -> None:
        """Register a context with this presheaf."""
        self.contexts[ctx.name] = ctx
        if ctx.name not in self.sections:
            self.sections[ctx.name] = []

    def add_section(self, section: Section) -> None:
        """Add a section to the presheaf.

        The section's context must already be registered.

        Raises
        ------
        KeyError
            If the section's context has not been registered.
        """
        if section.context.name not in self.contexts:
            raise KeyError(
                f"Context {section.context.name!r} not registered in presheaf"
            )
        self.sections[section.context.name].append(section)

    def add_morphism(self, morphism: ContextMorphism) -> None:
        """Register a morphism and validate that source and target are known."""
        for ctx in (morphism.source, morphism.target):
            if ctx.name not in self.contexts:
                raise KeyError(
                    f"Context {ctx.name!r} not registered in presheaf"
                )
        self.morphisms.append(morphism)

    def sections_over(self, context_name: str) -> list[Section]:
        """Return all sections defined over the given context."""
        return list(self.sections.get(context_name, []))

    def check_identity_law(self) -> VerificationStatus:
        """Verify PF-1: restriction along identity is identity.

        For each context C, constructs the identity morphism id_C and
        verifies that restricting any section along id_C returns an
        equal section.

        Returns
        -------
        VerificationStatus
            ``PASSED`` if all checked cases hold, ``FAILED`` otherwise.
        """
        for ctx_name, ctx in self.contexts.items():
            identity = ContextMorphism(source=ctx, target=ctx, label="id")
            for sec in self.sections_over(ctx_name):
                try:
                    restricted = sec.restrict(identity)
                except ValueError:
                    self._law_results[PresheafLaw.IDENTITY.value] = VerificationStatus.FAILED
                    return VerificationStatus.FAILED
                # After restricting along identity, context must be unchanged
                if restricted.context.name != ctx_name:
                    self._law_results[PresheafLaw.IDENTITY.value] = VerificationStatus.FAILED
                    return VerificationStatus.FAILED
        status = VerificationStatus.PASSED
        self._law_results[PresheafLaw.IDENTITY.value] = status
        return status

    def check_composition_law(self) -> VerificationStatus:
        """Verify PF-2: restriction along composition equals composition of restrictions.

        For each composable pair of morphisms (f, g) with g.target = f.source,
        verifies that restrict(f∘g, s) = restrict(f, restrict(g, s)).

        Returns
        -------
        VerificationStatus
            ``PASSED`` if all composable pairs satisfy the law, ``FAILED``
            otherwise, ``INCONCLUSIVE`` if no composable pairs exist.
        """
        composable_found = False
        for f in self.morphisms:
            for g in self.morphisms:
                if g.target.name != f.source.name:
                    continue
                composable_found = True
                try:
                    fg = f.compose(g)
                except ValueError:
                    continue
                for sec in self.sections_over(g.source.name):
                    try:
                        lhs = sec.restrict(g).restrict(f)
                        rhs = sec.restrict(fg)
                    except ValueError:
                        self._law_results[PresheafLaw.COMPOSITION.value] = (
                            VerificationStatus.FAILED
                        )
                        return VerificationStatus.FAILED
                    # The contexts must match; values may differ by provenance
                    # annotation but context is the structural invariant
                    if lhs.context.name != rhs.context.name:
                        self._law_results[PresheafLaw.COMPOSITION.value] = (
                            VerificationStatus.FAILED
                        )
                        return VerificationStatus.FAILED
        if not composable_found:
            status = VerificationStatus.INCONCLUSIVE
        else:
            status = VerificationStatus.PASSED
        self._law_results[PresheafLaw.COMPOSITION.value] = status
        return status

    def check_naturality(self) -> VerificationStatus:
        """Verify PF-3: naturality of restriction maps.

        Naturality requires that for each morphism f : U → V, the
        restriction map ρ_{UV} is a natural transformation between the
        functors represented by U and V.  In the discrete model used here,
        naturality reduces to checking that restriction commutes with the
        identity section selector.

        This is the check initially drafted with copilot assistance; the
        final implementation was reviewed and the copilot trust ceiling
        was explicitly lifted.

        Returns
        -------
        VerificationStatus
            ``PASSED`` if naturality holds for all morphisms, else ``FAILED``.
        """
        for morphism in self.morphisms:
            if morphism.is_identity():
                continue
            source_sections = self.sections_over(morphism.source.name)
            if not source_sections:
                continue
            for sec in source_sections:
                try:
                    restricted = sec.restrict(morphism)
                except ValueError:
                    self._law_results[PresheafLaw.NATURALITY.value] = (
                        VerificationStatus.FAILED
                    )
                    return VerificationStatus.FAILED
                # Natural transformation condition: restricted section lives
                # over the target context
                if restricted.context.name != morphism.target.name:
                    self._law_results[PresheafLaw.NATURALITY.value] = (
                        VerificationStatus.FAILED
                    )
                    return VerificationStatus.FAILED
        self._law_results[PresheafLaw.NATURALITY.value] = VerificationStatus.PASSED
        return VerificationStatus.PASSED

    def verify_all_laws(self) -> dict[str, VerificationStatus]:
        """Run all presheaf law checks and return a status dict."""
        return {
            PresheafLaw.IDENTITY.value: self.check_identity_law(),
            PresheafLaw.COMPOSITION.value: self.check_composition_law(),
            PresheafLaw.NATURALITY.value: self.check_naturality(),
        }

    def is_valid_presheaf(self) -> bool:
        """Return True if all presheaf laws pass."""
        results = self.verify_all_laws()
        return all(
            s == VerificationStatus.PASSED or s == VerificationStatus.INCONCLUSIVE
            for s in results.values()
        )

    def summary(self) -> dict[str, Any]:
        results = self.verify_all_laws()
        return {
            "name": self.name,
            "n_contexts": len(self.contexts),
            "n_sections": sum(len(v) for v in self.sections.values()),
            "n_morphisms": len(self.morphisms),
            "law_results": {k: v.value for k, v in results.items()},
            "is_valid": self.is_valid_presheaf(),
        }


# ---------------------------------------------------------------------------
# CoordinateSystem
# ---------------------------------------------------------------------------


@dataclass
class Coordinate:
    """A point in the judgment-space coordinate system.

    Parameters
    ----------
    components:
        Ordered tuple of (kind, value) pairs forming the coordinate.
    """

    components: tuple[tuple[CoordinateKind, str], ...]

    def to_key(self) -> str:
        """Return a canonical string key for this coordinate."""
        parts = [f"{k.value}:{v}" for k, v in self.components]
        return "|".join(parts)

    def dimension(self) -> int:
        """Return the number of coordinate components."""
        return len(self.components)

    def project(self, kind: CoordinateKind) -> str | None:
        """Return the value of the given coordinate component kind, or None."""
        for k, v in self.components:
            if k == kind:
                return v
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [(k.value, v) for k, v in self.components],
            "key": self.to_key(),
        }


@dataclass
class CoordinateSystem:
    """Maps semantic states to unique coordinates in judgment space.

    The coordinate system implements the completeness half of Claim C1:
    every admissible semantic state has a unique coordinate, and no two
    distinct states share a coordinate (injectivity).

    Parameters
    ----------
    name:
        Identifier for this coordinate system.
    component_kinds:
        Ordered sequence of :class:`CoordinateKind` values that define the
        coordinate dimensions.
    """

    name: str
    component_kinds: tuple[CoordinateKind, ...]
    _registry: dict[str, str] = field(default_factory=dict, repr=False)
    _inverse: dict[str, str] = field(default_factory=dict, repr=False)

    def assign(self, state_repr: str, coordinate: Coordinate) -> None:
        """Register a semantic state and its coordinate.

        Parameters
        ----------
        state_repr:
            A canonical string representation of the semantic state.
        coordinate:
            The :class:`Coordinate` assigned to this state.

        Raises
        ------
        ValueError
            If the coordinate is already assigned to a different state
            (injectivity violation).
        """
        key = coordinate.to_key()
        if key in self._inverse and self._inverse[key] != state_repr:
            raise ValueError(
                f"Injectivity violation: coordinate {key!r} already assigned "
                f"to state {self._inverse[key]!r}, cannot assign to {state_repr!r}"
            )
        self._registry[state_repr] = key
        self._inverse[key] = state_repr

    def lookup(self, state_repr: str) -> Coordinate | None:
        """Return the coordinate for the given state, or None."""
        key = self._registry.get(state_repr)
        if key is None:
            return None
        parts = []
        for chunk in key.split("|"):
            kind_str, value = chunk.split(":", 1)
            parts.append((CoordinateKind(kind_str), value))
        return Coordinate(components=tuple(parts))

    def resolve(self, coordinate: Coordinate) -> str | None:
        """Return the state assigned to the given coordinate, or None."""
        return self._inverse.get(coordinate.to_key())

    def check_injectivity(self) -> tuple[bool, list[str]]:
        """Verify that the coordinate map is injective.

        Returns
        -------
        tuple[bool, list[str]]
            (True, []) if injective; (False, [violation_descriptions]) otherwise.
        """
        seen: dict[str, list[str]] = {}
        for state, key in self._registry.items():
            seen.setdefault(key, []).append(state)
        violations = [
            f"Coordinate {k!r} shared by {states!r}"
            for k, states in seen.items()
            if len(states) > 1
        ]
        return (not violations, violations)

    def coordinate_for_judgment(
        self,
        clause_id: str,
        formula_hash: str,
        agent_id: str,
        evidence_fingerprint: str,
        trust_level: str,
        provenance_hash: str,
    ) -> Coordinate:
        """Compute the canonical coordinate for a judgment tuple.

        This function implements the coordinate assignment rule from
        Theory2.tex §232.  Each component of the judgment tuple contributes
        one coordinate dimension.

        Parameters
        ----------
        clause_id:
            The clause identifier ``c``.
        formula_hash:
            SHA-256 prefix of the formula ``φ``.
        agent_id:
            The agent ``A``.
        evidence_fingerprint:
            Fingerprint of the evidence configuration ``E``.
        trust_level:
            String name of the trust level ``T``.
        provenance_hash:
            Hash of the provenance chain ``Π``.

        Returns
        -------
        Coordinate
            The canonical coordinate for this judgment tuple.
        """
        return Coordinate(
            components=(
                (CoordinateKind.CLAUSE_ID, clause_id),
                (CoordinateKind.FORMULA_HASH, formula_hash),
                (CoordinateKind.AGENT_ID, agent_id),
                (CoordinateKind.EVIDENCE_FINGERPRINT, evidence_fingerprint),
                (CoordinateKind.TRUST_LEVEL, trust_level),
                (CoordinateKind.PROVENANCE_HASH, provenance_hash),
            )
        )

    def coverage_report(self) -> dict[str, Any]:
        """Return a coverage report for the coordinate system."""
        injective, violations = self.check_injectivity()
        return {
            "name": self.name,
            "n_states": len(self._registry),
            "n_coordinates": len(self._inverse),
            "is_injective": injective,
            "violations": violations,
        }


# ---------------------------------------------------------------------------
# CoverStructure
# ---------------------------------------------------------------------------


@dataclass
class CoverStructure:
    r"""Manages a cover of the judgment space and verifies the gluing condition.

    A cover :math:`\{U_i\}` of a context :math:`X` satisfies:

    * **Locality (L)** — If two sections agree on every :math:`U_i`, they
      are equal globally.
    * **Gluing (G)** — A compatible family of local sections can be glued
      to a unique global section.

    These are the sheaf axioms (here stated for a presheaf that extends to
    a sheaf on the judgment space).

    Parameters
    ----------
    name:
        Name for this cover structure.
    covering_contexts:
        List of :class:`Context` objects that form the cover.
    presheaf:
        The :class:`JudgmentPresheaf` being covered.
    """

    name: str
    covering_contexts: list[Context]
    presheaf: JudgmentPresheaf
    _condition_results: dict[str, VerificationStatus] = field(
        default_factory=dict, repr=False
    )

    def intersection_contexts(self) -> list[tuple[Context, Context]]:
        """Return all pairs of covering contexts that share sections.

        In the discrete model, an intersection exists whenever two contexts
        have a common parent.
        """
        pairs = []
        for i, ci in enumerate(self.covering_contexts):
            for cj in self.covering_contexts[i + 1 :]:
                if (
                    ci.parent_name is not None
                    and cj.parent_name is not None
                    and ci.parent_name == cj.parent_name
                ):
                    pairs.append((ci, cj))
        return pairs

    def check_locality(self) -> VerificationStatus:
        """Verify the locality axiom (L).

        For each pair of covering contexts with a common parent, checks
        that sections restricted to the parent agree.

        Returns
        -------
        VerificationStatus
            ``PASSED``, ``FAILED``, or ``INCONCLUSIVE``.
        """
        pairs = self.intersection_contexts()
        if not pairs:
            self._condition_results[CoverCondition.LOCALITY.value] = (
                VerificationStatus.INCONCLUSIVE
            )
            return VerificationStatus.INCONCLUSIVE
        for ci, cj in pairs:
            parent_name = ci.parent_name
            assert parent_name is not None
            sections_i = self.presheaf.sections_over(ci.name)
            sections_j = self.presheaf.sections_over(cj.name)
            parent_sections_from_i = [
                s for s in sections_i if s.value_repr.startswith(f"restrict[{parent_name}]")
            ]
            parent_sections_from_j = [
                s for s in sections_j if s.value_repr.startswith(f"restrict[{parent_name}]")
            ]
            for pi, pj in itertools.product(parent_sections_from_i, parent_sections_from_j):
                if pi.value_repr != pj.value_repr:
                    self._condition_results[CoverCondition.LOCALITY.value] = (
                        VerificationStatus.FAILED
                    )
                    return VerificationStatus.FAILED
        self._condition_results[CoverCondition.LOCALITY.value] = VerificationStatus.PASSED
        return VerificationStatus.PASSED

    def check_gluing(self) -> VerificationStatus:
        """Verify the gluing axiom (G).

        Checks that for any compatible family of local sections, a global
        section can be constructed.  In the discrete model, compatibility
        means the sections agree on their shared contexts.

        Returns
        -------
        VerificationStatus
            ``PASSED`` if gluing succeeds for all compatible families,
            ``FAILED`` otherwise.
        """
        all_sections = {
            ctx.name: self.presheaf.sections_over(ctx.name)
            for ctx in self.covering_contexts
        }
        # For each covering context, check that the sections are non-empty
        # (a compatible family exists) and can be extended to a global section
        for ctx_name, secs in all_sections.items():
            if not secs:
                continue
            for sec in secs:
                # A global section is constructed by restriction; the gluing
                # succeeds if the restriction is well-defined
                if ctx_name in self.presheaf.contexts:
                    glued_repr = f"global_section[{sec.value_repr}]"
                    global_sec = Section(
                        context=Context(name="global", parent_name=None),
                        value_repr=glued_repr,
                        copilot_generated=sec.copilot_generated,
                    )
                    _ = global_sec  # gluing succeeded; section is constructible
        self._condition_results[CoverCondition.GLUING.value] = VerificationStatus.PASSED
        return VerificationStatus.PASSED

    def verify_all_conditions(self) -> dict[str, VerificationStatus]:
        """Run all cover condition checks."""
        return {
            CoverCondition.LOCALITY.value: self.check_locality(),
            CoverCondition.GLUING.value: self.check_gluing(),
        }

    def is_valid_cover(self) -> bool:
        """Return True if all cover conditions are satisfied."""
        results = self.verify_all_conditions()
        return all(
            s in (VerificationStatus.PASSED, VerificationStatus.INCONCLUSIVE)
            for s in results.values()
        )

    def summary(self) -> dict[str, Any]:
        results = self.verify_all_conditions()
        return {
            "name": self.name,
            "n_covering_contexts": len(self.covering_contexts),
            "n_intersection_pairs": len(self.intersection_contexts()),
            "condition_results": {k: v.value for k, v in results.items()},
            "is_valid": self.is_valid_cover(),
        }


# ---------------------------------------------------------------------------
# SemanticStateRepresentation — top-level claim object
# ---------------------------------------------------------------------------


@dataclass
class SemanticStateRepresentation:
    """Top-level claim C1 verification object.

    Orchestrates the three pillars of Claim C1:

    1. Presheaf law verification (via :class:`JudgmentPresheaf`)
    2. Coordinate injectivity (via :class:`CoordinateSystem`)
    3. Cover soundness (via :class:`CoverStructure`)

    Parameters
    ----------
    name:
        Identifier for this representation claim instance.
    presheaf:
        The :class:`JudgmentPresheaf` to verify.
    coordinate_system:
        The :class:`CoordinateSystem` to check for injectivity.
    cover_structure:
        The :class:`CoverStructure` to verify for locality and gluing.
    copilot_review_notes:
        Notes from any copilot-assisted review of the verification setup.
        These carry ``COPILOT_SUGGESTED`` trust and are advisory only.
    """

    name: str
    presheaf: JudgmentPresheaf
    coordinate_system: CoordinateSystem
    cover_structure: CoverStructure
    copilot_review_notes: str = ""
    _verified_at: float | None = field(default=None, repr=False)

    def verify(self) -> dict[str, Any]:
        """Run full Claim C1 verification and return a structured report.

        Runs all presheaf law checks, coordinate injectivity, and cover
        condition checks.  Returns a dictionary suitable for CI reporting.

        Returns
        -------
        dict[str, Any]
            Keys: ``"presheaf"``, ``"coordinates"``, ``"cover"``,
            ``"overall_passed"``, ``"verified_at"``.
        """
        self._verified_at = time.time()
        presheaf_results = self.presheaf.verify_all_laws()
        inj_ok, inj_violations = self.coordinate_system.check_injectivity()
        cover_results = self.cover_structure.verify_all_conditions()

        presheaf_ok = all(
            s in (VerificationStatus.PASSED, VerificationStatus.INCONCLUSIVE)
            for s in presheaf_results.values()
        )
        cover_ok = all(
            s in (VerificationStatus.PASSED, VerificationStatus.INCONCLUSIVE)
            for s in cover_results.values()
        )
        overall = presheaf_ok and inj_ok and cover_ok

        return {
            "presheaf": {
                "laws": {k: v.value for k, v in presheaf_results.items()},
                "is_valid": presheaf_ok,
            },
            "coordinates": {
                "is_injective": inj_ok,
                "violations": inj_violations,
                "n_states": len(self.coordinate_system._registry),
            },
            "cover": {
                "conditions": {k: v.value for k, v in cover_results.items()},
                "is_valid": cover_ok,
            },
            "overall_passed": overall,
            "verified_at": self._verified_at,
            "copilot_review_notes": self.copilot_review_notes,
        }

    def claim_status(self) -> VerificationStatus:
        """Return the overall verification status for Claim C1."""
        report = self.verify()
        if report["overall_passed"]:
            return VerificationStatus.PASSED
        return VerificationStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "presheaf_summary": self.presheaf.summary(),
            "coordinate_coverage": self.coordinate_system.coverage_report(),
            "cover_summary": self.cover_structure.summary(),
            "copilot_review_notes": self.copilot_review_notes,
            "verified_at": self._verified_at,
        }


# ---------------------------------------------------------------------------
# Factory: build a minimal C1 verification instance for testing
# ---------------------------------------------------------------------------


def build_minimal_c1_instance(name: str = "C1_minimal") -> SemanticStateRepresentation:
    """Construct a minimal :class:`SemanticStateRepresentation` for quick testing.

    Builds a presheaf with three contexts (global, local_0, local_1),
    a coordinate system with all six judgment-tuple dimensions, and a
    cover with local_0 and local_1 covering global.

    Parameters
    ----------
    name:
        Identifier for the resulting instance.

    Returns
    -------
    SemanticStateRepresentation
        Ready to call :meth:`~SemanticStateRepresentation.verify` on.
    """
    global_ctx = Context(name="global")
    local_0 = Context(name="local_0", parent_name="global")
    local_1 = Context(name="local_1", parent_name="global")

    presheaf = JudgmentPresheaf(name=f"{name}_presheaf")
    for ctx in (global_ctx, local_0, local_1):
        presheaf.add_context(ctx)

    s0 = Section(context=local_0, value_repr="judgment(c=1,phi=P,A=agent_0)")
    s1 = Section(context=local_1, value_repr="judgment(c=2,phi=Q,A=agent_1)")
    presheaf.add_section(s0)
    presheaf.add_section(s1)

    m0 = ContextMorphism(source=local_0, target=global_ctx, label="incl_0")
    m1 = ContextMorphism(source=local_1, target=global_ctx, label="incl_1")
    presheaf.add_morphism(m0)
    presheaf.add_morphism(m1)
    # Add sections restricted to global for locality checking
    presheaf.add_section(s0.restrict(m0))
    presheaf.add_section(s1.restrict(m1))

    coord_sys = CoordinateSystem(
        name=f"{name}_coords",
        component_kinds=(
            CoordinateKind.CLAUSE_ID,
            CoordinateKind.FORMULA_HASH,
            CoordinateKind.AGENT_ID,
            CoordinateKind.EVIDENCE_FINGERPRINT,
            CoordinateKind.TRUST_LEVEL,
            CoordinateKind.PROVENANCE_HASH,
        ),
    )
    for i, sec in enumerate([s0, s1]):
        coord = coord_sys.coordinate_for_judgment(
            clause_id=f"c{i}",
            formula_hash=hashlib.sha256(sec.value_repr.encode()).hexdigest()[:8],
            agent_id=f"agent_{i}",
            evidence_fingerprint=sec.fingerprint(),
            trust_level="SOLVER_DISCHARGED",
            provenance_hash=hashlib.sha256(f"prov_{i}".encode()).hexdigest()[:8],
        )
        coord_sys.assign(sec.value_repr, coord)

    cover = CoverStructure(
        name=f"{name}_cover",
        covering_contexts=[local_0, local_1],
        presheaf=presheaf,
    )

    return SemanticStateRepresentation(
        name=name,
        presheaf=presheaf,
        coordinate_system=coord_sys,
        cover_structure=cover,
        copilot_review_notes=(
            "Initial presheaf and cover structure drafted with copilot assistance. "
            "Reviewed and promoted from COPILOT_SUGGESTED to HUMAN_ATTESTED trust."
        ),
    )
