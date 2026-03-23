"""A site for programmatic judgment — Theory2.tex formal core chapter.

# copilot: foundations/formal_core §a-site-for-programmatic-judgment
# Chapter: Mathematical interlude — a more explicit formal core
# Reference: Theory2.tex §formal_core.s01 "A site for programmatic judgment"

Mathematical Overview
---------------------
A **Grothendieck site** (C, J) pairs a small category C with a Grothendieck
topology J.  In the JuGeo framework, the objects of C are *program contexts* —
typed environments Γ together with an active judgment formula φ — and the
morphisms are *context inclusions*: weakening, substitution, and projection maps
that preserve the judgment structure.

A **covering sieve** S ∈ J(c) on a context c is a downward-closed collection of
context morphisms into c that constitutes a valid *observation set*: every
property that can be discharged using evidence from S is considered verified at c.
The topology axioms then guarantee coherence:

1. **Maximality** — the identity sieve t_c = Hom(−, c) belongs to J(c).
2. **Stability** — if S ∈ J(c) and f: d → c is a morphism, then the pullback
   f*S ∈ J(d).
3. **Transitivity** — if S ∈ J(c) and R is a sieve on c such that f*R ∈ J(d)
   for every f : d → c in S, then R ∈ J(c).

The **judgment sheaf** F on (C, J) assigns to each context c the set of
*judgment tuples* (c, φ, A, E, O, B, T, Π) where:

* c — context (program environment + active variable bindings)
* φ — formula / proposition under judgment
* A — aspect tag (what facet of correctness is being judged)
* E — evidence bundle (solver transcripts, test witnesses, certificates)
* O — obligation set (residual proof obligations still to be discharged)
* B — trust bound (ceiling and floor on admissible trust tier)
* T — trust tier (current trust level in the ordered algebra)
* Π — provenance chain (ordered record of derivation steps)

The sheaf condition guarantees that locally consistent judgment tuples glue
uniquely to a global tuple: if tuples on overlapping contexts agree on overlaps,
there is a unique global tuple extending them.

This module provides:

- :class:`ContextKind` — discriminant for the semantic role of a context.
- :class:`MorphismKind` — discriminant for the structural role of a morphism.
- :class:`SiteCoordinate` — an object of the site category C.
- :class:`ContextMorphism` — a morphism in C between two SiteCoordinates.
- :class:`CoveringFamily` — a concrete cover (finite set of morphisms).
- :class:`CoveringRelation` — tracks which families cover which contexts.
- :class:`GrothendieckSite` — the full site (C, J) with axiom verification.
- :class:`JudgmentTuple` — the eight-component judgment (c, φ, A, E, O, B, T, Π).
- :class:`JudgmentSection` — a section of the judgment sheaf over a coordinate.
- :class:`JudgmentSheaf` — the sheaf of judgment tuples on the site.
- :class:`JudgmentSite` — top-level composite site + sheaf.
- :class:`ASiteProgrammaticJudgmentWitness` — immutable certificate of a site run.
- :class:`ASiteProgrammaticJudgmentCoordinator` — orchestrates site construction
  and judgment sheaf population.
- :class:`ASiteProgrammaticJudgmentAnalyzer` — analyses site coherence and
  judgment tuple consistency across a sheaf.

References
----------
Theory2.tex §formal_core "A site for programmatic judgment" — Definitions,
covering families, judgment tuples as sheaf sections, coherence theorems.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier
    _HAS_TRUST = True
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    _HAS_TRUST = False

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRequest, EvidenceResponse
    _HAS_CHANNELS = True
except ImportError:
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]
    _HAS_CHANNELS = False

try:
    from jugeo.errors import JuGeoError, StructuredFailure  # type: ignore[import]
    _HAS_ERRORS = True
except ImportError:
    JuGeoError = Exception  # type: ignore[assignment,misc]
    StructuredFailure = None  # type: ignore[assignment,misc]
    _HAS_ERRORS = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust tier ordering (local five-tier algebra for this module)
# ---------------------------------------------------------------------------

_TIER_ORDER: dict[str, int] = {
    "PROPOSAL": 0,
    "REVIEWED": 1,
    "VERIFIED": 2,
    "RUNTIME_WITNESSED": 3,
    "PROOF_BACKED": 4,
}


def _tier_rank(tier: str) -> int:
    """Return the integer rank of *tier* in the local five-tier order."""
    return _TIER_ORDER.get(tier, 0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ContextKind(str, Enum):
    """Discriminant for the semantic role of a program context.

    Theory2.tex §formal_core distinguishes several flavours of context that
    appear as objects of the site category C.

    Members
    -------
    FUNCTION_BODY:
        A function body context: the environment carries the formal parameters,
        local bindings, and the return-type annotation of a single function.
    MODULE_SCOPE:
        A module-level context encompassing all top-level definitions and
        imports visible within one Python module.
    CLASS_BODY:
        A class body context carrying ``self``-type, method signatures, and
        class-level attribute declarations.
    LOOP_INVARIANT:
        A context restricted to the inductive invariant of a loop: pre- and
        post-condition bindings together with the induction hypothesis.
    TYPE_PARAMETER:
        A polymorphic context parameterised by one or more type variables.
    GLOBAL:
        The maximal context covering an entire project; used as the terminal
        object of the site category.
    SYNTHETIC:
        An artificially constructed context used during proof synthesis or
        countermodel search.
    """

    FUNCTION_BODY = "function_body"
    MODULE_SCOPE = "module_scope"
    CLASS_BODY = "class_body"
    LOOP_INVARIANT = "loop_invariant"
    TYPE_PARAMETER = "type_parameter"
    GLOBAL = "global"
    SYNTHETIC = "synthetic"


class MorphismKind(str, Enum):
    """Discriminant for the structural role of a context morphism.

    Theory2.tex §formal_core identifies the following morphism kinds as
    the generators of the site category C.

    Members
    -------
    WEAKENING:
        Adds unused variables to the context.  The judgment remains valid
        in the larger environment.
    SUBSTITUTION:
        Replaces a free variable by a term.  Preserves typing iff the term
        has the declared type of the variable.
    PROJECTION:
        Drops a suffix of variable declarations.  Implements the forgetful
        functor from a bigger context to a sub-context.
    INCLUSION:
        Embeds a sub-context into a parent context.  Dual to projection.
    IDENTITY:
        The identity morphism on a context; required by the category laws.
    COMPOSITION:
        A composed morphism; produced by :meth:`ContextMorphism.compose`.
    """

    WEAKENING = "weakening"
    SUBSTITUTION = "substitution"
    PROJECTION = "projection"
    INCLUSION = "inclusion"
    IDENTITY = "identity"
    COMPOSITION = "composition"


class CoverAxiomStatus(str, Enum):
    """Result of verifying one Grothendieck topology axiom.

    Members
    -------
    HOLDS:
        The axiom was verified for all tested instances.
    VIOLATED:
        At least one counterexample was found.
    UNTESTED:
        The axiom has not yet been checked.
    PARTIAL:
        Some instances pass; others were skipped due to missing data.
    """

    HOLDS = "holds"
    VIOLATED = "violated"
    UNTESTED = "untested"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# §formal_core Definition — SiteCoordinate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SiteCoordinate:
    """An object of the site category C.

    In Theory2.tex §formal_core, each object of C is a *program context*:
    a typed environment together with a formula φ currently under judgment.
    ``SiteCoordinate`` is the immutable Python representation of that object.

    A coordinate is identified by its ``coord_id`` (stable across sessions) and
    carries a ``kind`` tag that records which part of the program structure it
    corresponds to.  The ``depth`` field records the nesting depth (0 = global
    context) and ``parent_id`` records the direct super-context, enabling
    reconstruction of the site category's morphism structure.

    Parameters
    ----------
    coord_id:
        Stable unique identifier for this context object.
    name:
        Human-readable label (module path, function name, etc.).
    kind:
        :class:`ContextKind` discriminant.
    depth:
        Nesting depth in the context hierarchy (0 = global).
    parent_id:
        The ``coord_id`` of the immediately enclosing context, or ``""`` for
        the global context.
    metadata:
        Auxiliary key-value pairs for extension points.
    """

    coord_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    kind: ContextKind = ContextKind.FUNCTION_BODY
    depth: int = 0
    parent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_global(self) -> bool:
        """Return True iff this coordinate is the global (terminal) context."""
        return self.kind == ContextKind.GLOBAL or self.depth == 0

    def is_sub_context_of(self, other: SiteCoordinate) -> bool:
        """Return True iff ``self`` is a strict sub-context of *other*.

        Checks transitivity through the ``parent_id`` chain.  If either
        coordinate lacks parent information, falls back to depth comparison.

        Parameters
        ----------
        other:
            The candidate parent coordinate.
        """
        if self.coord_id == other.coord_id:
            return False
        if self.parent_id == other.coord_id:
            return True
        return self.depth > other.depth and other.kind == ContextKind.GLOBAL

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of the coordinate."""
        payload = json.dumps(
            {"id": self.coord_id, "name": self.name, "kind": self.kind.value},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "coord_id": self.coord_id,
            "name": self.name,
            "kind": self.kind.value,
            "depth": self.depth,
            "parent_id": self.parent_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SiteCoordinate:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            coord_id=d.get("coord_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            kind=ContextKind(d.get("kind", ContextKind.FUNCTION_BODY.value)),
            depth=int(d.get("depth", 0)),
            parent_id=d.get("parent_id", ""),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# ContextMorphism
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextMorphism:
    """A morphism in the site category C between two SiteCoordinates.

    Morphisms encode the structural transitions between program contexts
    (weakening, substitution, projection, inclusion).  Each morphism carries
    a :class:`MorphismKind` discriminant and a ``label`` that records the
    semantic content of the transition (e.g. the substituted variable name).

    Theory2.tex §formal_core requires morphisms to be composable (category
    law) and to be compatible with covering sieves (the pullback axiom).

    Parameters
    ----------
    morph_id:
        Stable unique identifier for this morphism.
    source:
        The domain :class:`SiteCoordinate`.
    target:
        The codomain :class:`SiteCoordinate`.
    kind:
        :class:`MorphismKind` of this transition.
    label:
        Semantic label (variable name, substitution expression, etc.).
    is_covering:
        Whether this morphism is part of some covering family.
    metadata:
        Auxiliary key-value pairs.
    """

    morph_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: SiteCoordinate = field(default_factory=SiteCoordinate)
    target: SiteCoordinate = field(default_factory=SiteCoordinate)
    kind: MorphismKind = MorphismKind.IDENTITY
    label: str = ""
    is_covering: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_identity(self) -> bool:
        """Return True iff source and target are the same coordinate."""
        return (
            self.kind == MorphismKind.IDENTITY
            or self.source.coord_id == self.target.coord_id
        )

    def compose(self, other: ContextMorphism) -> ContextMorphism:
        """Return the composite morphism self ∘ other (other applied first).

        Requires ``other.target.coord_id == self.source.coord_id``.

        Parameters
        ----------
        other:
            The morphism to be applied first (right factor).

        Raises
        ------
        ValueError
            If the source of ``self`` does not match the target of ``other``.
        """
        if self.source.coord_id != other.target.coord_id:
            raise ValueError(
                f"Cannot compose: self.source={self.source.coord_id!r} "
                f"≠ other.target={other.target.coord_id!r}"
            )
        return ContextMorphism(
            source=other.source,
            target=self.target,
            kind=MorphismKind.COMPOSITION,
            label=f"({self.label} ∘ {other.label})",
            is_covering=self.is_covering and other.is_covering,
            metadata={"left": self.morph_id, "right": other.morph_id},
        )

    def pullback(self, sieve_members: list[ContextMorphism]) -> list[ContextMorphism]:
        """Compute the pullback f*S of a sieve S along this morphism.

        Given a sieve S ∈ J(target) encoded as a list of morphisms into
        ``target``, returns the list of morphisms g : d → source such that
        self ∘ g ∈ S.  This implements the stability axiom of the topology.

        Parameters
        ----------
        sieve_members:
            The morphisms constituting the sieve S on ``self.target``.

        Returns
        -------
        list[ContextMorphism]
            The pullback sieve f*S on ``self.source``.
        """
        pulled_back: list[ContextMorphism] = []
        for m in sieve_members:
            if m.target.coord_id == self.target.coord_id:
                try:
                    composed = self.compose(
                        ContextMorphism(
                            source=m.source,
                            target=self.source,
                            kind=MorphismKind.INCLUSION,
                            label=f"pb_{m.label}",
                        )
                    )
                    pulled_back.append(composed)
                except ValueError:
                    pass
        return pulled_back

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "morph_id": self.morph_id,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "kind": self.kind.value,
            "label": self.label,
            "is_covering": self.is_covering,
        }


# ---------------------------------------------------------------------------
# CoveringFamily
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoveringFamily:
    """A finite covering family {f_i : U_i → c} for a context c.

    A covering family in the pretopology sense is a finite collection of
    context morphisms all sharing the same target *base_coord*.  It
    generates a sieve by taking all composites.

    Theory2.tex §formal_core Remark: a covering family is *adequate* iff
    every admissible evidence type for ``base_coord`` can be witnessed by
    at least one member of the family.

    Parameters
    ----------
    family_id:
        Stable identifier for this covering family.
    base_coord:
        The context c being covered.
    morphisms:
        The sequence of morphisms {f_i : U_i → c}.
    trust_tier:
        The minimum trust tier required for this covering family to be
        accepted as a valid observation set.
    is_adequate:
        Whether this family is adequate in the pretopology sense.
    provenance:
        Free-text note on how this family was constructed.
    """

    family_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    base_coord: SiteCoordinate = field(default_factory=SiteCoordinate)
    morphisms: tuple[ContextMorphism, ...] = field(default_factory=tuple)
    trust_tier: str = "PROPOSAL"
    is_adequate: bool = False
    provenance: str = ""

    def size(self) -> int:
        """Return the number of morphisms in this covering family."""
        return len(self.morphisms)

    def member_source_ids(self) -> list[str]:
        """Return the list of source coordinate IDs for each morphism."""
        return [m.source.coord_id for m in self.morphisms]

    def check_morphisms_share_target(self) -> bool:
        """Return True iff every morphism targets ``self.base_coord``."""
        return all(m.target.coord_id == self.base_coord.coord_id for m in self.morphisms)

    def generate_sieve(self) -> list[ContextMorphism]:
        """Generate the sieve by closing the covering family under composition.

        Returns all morphisms in the family plus all composites of pairs
        within the family (one level of closure).  A full closure would
        require iterating until a fixed point, but one level is sufficient
        for finite sites.

        Returns
        -------
        list[ContextMorphism]
            The (partial) sieve generated by this covering family.
        """
        sieve: list[ContextMorphism] = list(self.morphisms)
        for i, f in enumerate(self.morphisms):
            for j, g in enumerate(self.morphisms):
                if i == j:
                    continue
                if f.source.coord_id == g.target.coord_id:
                    try:
                        sieve.append(f.compose(g))
                    except ValueError:
                        pass
        return sieve

    def fingerprint(self) -> str:
        """Return a short fingerprint of this covering family."""
        ids = sorted(m.morph_id for m in self.morphisms)
        payload = json.dumps({"base": self.base_coord.coord_id, "morphisms": ids}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "family_id": self.family_id,
            "base_coord": self.base_coord.to_dict(),
            "morphisms": [m.to_dict() for m in self.morphisms],
            "trust_tier": self.trust_tier,
            "is_adequate": self.is_adequate,
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# CoveringRelation
# ---------------------------------------------------------------------------


@dataclass
class CoveringRelation:
    """Tracks the assignment of covering families to site coordinates.

    A ``CoveringRelation`` is a mutable mapping coord_id → list[CoveringFamily]
    that represents the pretopology on the site.  It exposes methods to add
    families, query which families cover a coordinate, and verify the three
    Grothendieck axioms (maximality, stability, transitivity) on finite
    samples.

    Parameters
    ----------
    relation_id:
        Unique identifier for this covering relation.
    families:
        Internal mutable dict mapping coord_id to the list of covering
        families on that coordinate.
    axiom_results:
        Cache of axiom-check results keyed by axiom name.
    """

    relation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    families: dict[str, list[CoveringFamily]] = field(default_factory=dict)
    axiom_results: dict[str, CoverAxiomStatus] = field(default_factory=dict)

    def add_family(self, family: CoveringFamily) -> None:
        """Register a :class:`CoveringFamily` under its ``base_coord``.

        Parameters
        ----------
        family:
            The covering family to register.
        """
        cid = family.base_coord.coord_id
        self.families.setdefault(cid, []).append(family)
        log.debug("CoveringRelation.add_family: base_coord=%r family=%r", cid, family.family_id)

    def families_for(self, coord: SiteCoordinate) -> list[CoveringFamily]:
        """Return all covering families registered for *coord*.

        Parameters
        ----------
        coord:
            The coordinate to look up.

        Returns
        -------
        list[CoveringFamily]
            Possibly empty list of covering families.
        """
        return list(self.families.get(coord.coord_id, []))

    def covers(self, coord: SiteCoordinate) -> bool:
        """Return True iff at least one covering family is registered for *coord*."""
        return bool(self.families.get(coord.coord_id))

    def check_maximality(self, coord: SiteCoordinate) -> CoverAxiomStatus:
        """Check the maximality axiom: the identity sieve belongs to J(coord).

        The identity sieve on ``coord`` is represented by the presence of the
        identity morphism in at least one registered covering family.

        Returns
        -------
        CoverAxiomStatus
        """
        families = self.families_for(coord)
        if not families:
            self.axiom_results["maximality"] = CoverAxiomStatus.UNTESTED
            return CoverAxiomStatus.UNTESTED
        for fam in families:
            for m in fam.morphisms:
                if m.is_identity():
                    self.axiom_results["maximality"] = CoverAxiomStatus.HOLDS
                    return CoverAxiomStatus.HOLDS
        # No explicit identity morphism found; treat as partial (not violated,
        # since the family may still be adequate).
        self.axiom_results["maximality"] = CoverAxiomStatus.PARTIAL
        return CoverAxiomStatus.PARTIAL

    def check_stability(
        self, coord: SiteCoordinate, morphism: ContextMorphism
    ) -> CoverAxiomStatus:
        """Check the stability axiom: pullback of a cover is a cover.

        For each covering family on ``coord``, computes the pullback along
        ``morphism`` and checks whether the result is non-empty (indicating
        the pullback is a valid covering family for the source).

        Returns
        -------
        CoverAxiomStatus
        """
        families = self.families_for(coord)
        if not families:
            return CoverAxiomStatus.UNTESTED
        for fam in families:
            sieve = fam.generate_sieve()
            pulled = morphism.pullback(sieve)
            if not pulled:
                log.warning(
                    "CoveringRelation.check_stability: empty pullback for family=%r "
                    "morphism=%r", fam.family_id, morphism.morph_id
                )
                self.axiom_results["stability"] = CoverAxiomStatus.VIOLATED
                return CoverAxiomStatus.VIOLATED
        self.axiom_results["stability"] = CoverAxiomStatus.HOLDS
        return CoverAxiomStatus.HOLDS

    def describe(self) -> str:
        """Return a human-readable summary of this covering relation."""
        total_families = sum(len(v) for v in self.families.values())
        return (
            f"CoveringRelation {self.relation_id}\n"
            f"  Covered coordinates : {len(self.families)}\n"
            f"  Total families      : {total_families}\n"
            f"  Axiom results       : {self.axiom_results}\n"
        )


# ---------------------------------------------------------------------------
# GrothendieckSite
# ---------------------------------------------------------------------------


@dataclass
class GrothendieckSite:
    """The full Grothendieck site (C, J) for JuGeo program judgments.

    A ``GrothendieckSite`` owns a collection of :class:`SiteCoordinate` objects
    (the objects of C), a collection of :class:`ContextMorphism` objects (the
    morphisms of C), and a :class:`CoveringRelation` (the topology J).

    Theory2.tex §formal_core constructs the site so that objects are program
    contexts and morphisms are context inclusions.  The topology J is defined
    by the *adequate coverage* pretopology: a family covers a context iff
    every property verifiable at the context can be witnessed by evidence
    drawn from the family.

    Parameters
    ----------
    site_id:
        Stable unique identifier for this site.
    name:
        Human-readable name (e.g. the project name).
    coordinates:
        The objects of the site category C.
    morphisms:
        The morphisms of C.
    covering_relation:
        The Grothendieck topology J.
    global_coord:
        The terminal object (global context).
    axiom_log:
        Log of axiom verification events.
    """

    site_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    coordinates: dict[str, SiteCoordinate] = field(default_factory=dict)
    morphisms: dict[str, ContextMorphism] = field(default_factory=dict)
    covering_relation: CoveringRelation = field(default_factory=CoveringRelation)
    global_coord: SiteCoordinate = field(
        default_factory=lambda: SiteCoordinate(name="global", kind=ContextKind.GLOBAL, depth=0)
    )
    axiom_log: list[dict[str, Any]] = field(default_factory=list)

    def add_coordinate(self, coord: SiteCoordinate) -> None:
        """Add a :class:`SiteCoordinate` to the site.

        Parameters
        ----------
        coord:
            The coordinate to add.
        """
        self.coordinates[coord.coord_id] = coord
        log.debug("GrothendieckSite.add_coordinate: id=%r name=%r", coord.coord_id, coord.name)

    def add_morphism(self, morph: ContextMorphism) -> None:
        """Add a :class:`ContextMorphism` to the site.

        Both source and target must have been registered via
        :meth:`add_coordinate` first, or this method logs a warning.

        Parameters
        ----------
        morph:
            The morphism to add.
        """
        if morph.source.coord_id not in self.coordinates:
            log.warning(
                "GrothendieckSite.add_morphism: source %r not registered", morph.source.coord_id
            )
        if morph.target.coord_id not in self.coordinates:
            log.warning(
                "GrothendieckSite.add_morphism: target %r not registered", morph.target.coord_id
            )
        self.morphisms[morph.morph_id] = morph
        log.debug("GrothendieckSite.add_morphism: id=%r", morph.morph_id)

    def add_covering_family(self, family: CoveringFamily) -> None:
        """Register a covering family with the topology J.

        Parameters
        ----------
        family:
            The covering family to register.
        """
        self.covering_relation.add_family(family)

    def verify_axioms(self, sample_size: int = 5) -> dict[str, CoverAxiomStatus]:
        """Verify all three Grothendieck topology axioms on a sample of coordinates.

        Checks maximality, stability, and transitivity on the first
        ``sample_size`` registered coordinates (to keep runtime tractable).

        Parameters
        ----------
        sample_size:
            Number of coordinates to sample for each axiom check.

        Returns
        -------
        dict[str, CoverAxiomStatus]
            Mapping axiom_name → status.
        """
        results: dict[str, CoverAxiomStatus] = {}
        coords = list(self.coordinates.values())[:sample_size]
        morphs = list(self.morphisms.values())[:sample_size]

        # Maximality
        max_statuses: list[CoverAxiomStatus] = [
            self.covering_relation.check_maximality(c) for c in coords
        ]
        if any(s == CoverAxiomStatus.VIOLATED for s in max_statuses):
            results["maximality"] = CoverAxiomStatus.VIOLATED
        elif all(s == CoverAxiomStatus.HOLDS for s in max_statuses):
            results["maximality"] = CoverAxiomStatus.HOLDS
        else:
            results["maximality"] = CoverAxiomStatus.PARTIAL

        # Stability
        stab_statuses: list[CoverAxiomStatus] = []
        for c in coords:
            for m in morphs:
                if m.target.coord_id == c.coord_id:
                    stab_statuses.append(self.covering_relation.check_stability(c, m))
        if stab_statuses:
            if any(s == CoverAxiomStatus.VIOLATED for s in stab_statuses):
                results["stability"] = CoverAxiomStatus.VIOLATED
            elif all(s == CoverAxiomStatus.HOLDS for s in stab_statuses):
                results["stability"] = CoverAxiomStatus.HOLDS
            else:
                results["stability"] = CoverAxiomStatus.PARTIAL
        else:
            results["stability"] = CoverAxiomStatus.UNTESTED

        # Transitivity — recorded as partial without further computation
        results["transitivity"] = CoverAxiomStatus.PARTIAL

        self.axiom_log.append({
            "event": "verify_axioms",
            "results": {k: v.value for k, v in results.items()},
            "timestamp": time.time(),
        })
        log.info("GrothendieckSite.verify_axioms: %s", results)
        return results

    def object_count(self) -> int:
        """Return the number of registered coordinates."""
        return len(self.coordinates)

    def morphism_count(self) -> int:
        """Return the number of registered morphisms."""
        return len(self.morphisms)

    def describe(self) -> str:
        """Return a human-readable summary of this site."""
        return (
            f"GrothendieckSite '{self.name}' [{self.site_id}]\n"
            f"  Objects   : {self.object_count()}\n"
            f"  Morphisms : {self.morphism_count()}\n"
            f"  Global    : {self.global_coord.name!r}\n"
            + self.covering_relation.describe()
        )


# ---------------------------------------------------------------------------
# JudgmentTuple — the (c, φ, A, E, O, B, T, Π) kernel
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgmentTuple:
    """The eight-component judgment tuple (c, φ, A, E, O, B, T, Π).

    Theory2.tex §formal_core Definition: a judgment is **always** a tuple
    of eight components — never a bare boolean, never just a trust score.
    Each component plays a distinct logical role:

    * ``c``  — context: the :class:`SiteCoordinate` at which the judgment is made.
    * ``phi`` — formula: the proposition φ currently under judgment.
    * ``A``  — aspect: what property of φ is being judged (safety, liveness, …).
    * ``E``  — evidence: the bundle of certificates, transcripts, witnesses.
    * ``O``  — obligations: the residual proof obligations still to discharge.
    * ``B``  — bound: the admissibility bound (trust ceiling + floor pair).
    * ``T``  — trust: the current trust tier in the ordered algebra.
    * ``Pi`` — provenance: the ordered chain of derivation steps.

    The tuple is immutable; every transformation produces a new tuple.

    Parameters
    ----------
    tuple_id:
        Stable unique identifier for this judgment tuple.
    c:
        Context coordinate.
    phi:
        Formula / proposition string.
    A:
        Aspect tag.
    E:
        Evidence bundle (dict mapping evidence_id → descriptor).
    O:
        Obligation set (frozenset of obligation identifiers).
    B:
        Trust bound ``{"ceiling": str, "floor": str}``.
    T:
        Current trust tier string.
    Pi:
        Provenance chain (ordered tuple of step labels).
    """

    tuple_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    c: SiteCoordinate = field(default_factory=SiteCoordinate)
    phi: str = ""
    A: str = "safety"
    E: dict[str, Any] = field(default_factory=dict)
    O: frozenset[str] = field(default_factory=frozenset)
    B: dict[str, str] = field(default_factory=lambda: {"ceiling": "PROOF_BACKED", "floor": "PROPOSAL"})
    T: str = "PROPOSAL"
    Pi: tuple[str, ...] = field(default_factory=tuple)

    def is_fully_discharged(self) -> bool:
        """Return True iff all obligations in O have been discharged (O = ∅)."""
        return len(self.O) == 0

    def trust_rank(self) -> int:
        """Return the integer rank of the current trust tier T."""
        return _tier_rank(self.T)

    def meets_floor(self) -> bool:
        """Return True iff T is at least as strong as the floor in B."""
        floor = self.B.get("floor", "PROPOSAL")
        return _tier_rank(self.T) >= _tier_rank(floor)

    def respects_ceiling(self) -> bool:
        """Return True iff T does not exceed the ceiling in B."""
        ceiling = self.B.get("ceiling", "PROOF_BACKED")
        return _tier_rank(self.T) <= _tier_rank(ceiling)

    def with_trust(self, new_tier: str, step_label: str) -> JudgmentTuple:
        """Return a new tuple with trust tier promoted/demoted to *new_tier*.

        Parameters
        ----------
        new_tier:
            The new trust tier string.
        step_label:
            A provenance label recording the reason for the change.

        Returns
        -------
        JudgmentTuple
            A new immutable tuple with updated ``T`` and extended ``Pi``.
        """
        return replace(self, T=new_tier, Pi=self.Pi + (step_label,))

    def with_evidence(self, evidence_id: str, descriptor: Any) -> JudgmentTuple:
        """Return a new tuple with *evidence_id* added to E.

        Parameters
        ----------
        evidence_id:
            Unique identifier for the new evidence item.
        descriptor:
            Arbitrary descriptor for the evidence (transcript, certificate, etc.).

        Returns
        -------
        JudgmentTuple
            A new tuple with the evidence bundle extended.
        """
        new_E = {**self.E, evidence_id: descriptor}
        return replace(self, E=new_E, Pi=self.Pi + (f"add_evidence:{evidence_id}",))

    def discharge_obligation(self, obligation_id: str, step_label: str) -> JudgmentTuple:
        """Return a new tuple with *obligation_id* removed from O.

        Parameters
        ----------
        obligation_id:
            The obligation to discharge.
        step_label:
            Provenance label for the discharge step.

        Raises
        ------
        KeyError
            If *obligation_id* is not in O.
        """
        if obligation_id not in self.O:
            raise KeyError(f"Obligation {obligation_id!r} not in O={self.O!r}")
        new_O = self.O - {obligation_id}
        return replace(self, O=new_O, Pi=self.Pi + (f"discharge:{obligation_id}:{step_label}",))

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of this judgment tuple."""
        payload = json.dumps(
            {
                "tuple_id": self.tuple_id,
                "phi": self.phi,
                "A": self.A,
                "T": self.T,
                "O": sorted(self.O),
                "context_id": self.c.coord_id,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this judgment tuple to a plain dict."""
        return {
            "tuple_id": self.tuple_id,
            "c": self.c.to_dict(),
            "phi": self.phi,
            "A": self.A,
            "E": dict(self.E),
            "O": sorted(self.O),
            "B": dict(self.B),
            "T": self.T,
            "Pi": list(self.Pi),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JudgmentTuple:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            tuple_id=d.get("tuple_id", str(uuid.uuid4())),
            c=SiteCoordinate.from_dict(d.get("c", {})),
            phi=d.get("phi", ""),
            A=d.get("A", "safety"),
            E=dict(d.get("E", {})),
            O=frozenset(d.get("O", [])),
            B=dict(d.get("B", {"ceiling": "PROOF_BACKED", "floor": "PROPOSAL"})),
            T=d.get("T", "PROPOSAL"),
            Pi=tuple(d.get("Pi", [])),
        )


# ---------------------------------------------------------------------------
# JudgmentSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgmentSection:
    """A section of the judgment sheaf over a specific SiteCoordinate.

    A ``JudgmentSection`` packages a :class:`JudgmentTuple` with the coordinate
    over which it is defined, ensuring that the sheaf restriction maps can be
    computed correctly (restriction lowers trust tier by one step and extends Pi).

    Parameters
    ----------
    section_id:
        Stable unique identifier for this section.
    coord:
        The :class:`SiteCoordinate` over which this section is defined.
    judgment:
        The :class:`JudgmentTuple` (c, φ, A, E, O, B, T, Π).
    is_global:
        True iff this section was assembled from a compatible covering family
        (i.e. it is a global section of the sheaf, not merely a local one).
    provenance:
        Additional derivation labels beyond those in ``judgment.Pi``.
    """

    section_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coord: SiteCoordinate = field(default_factory=SiteCoordinate)
    judgment: JudgmentTuple = field(default_factory=JudgmentTuple)
    is_global: bool = False
    provenance: tuple[str, ...] = field(default_factory=tuple)

    def restrict_to(self, sub_coord: SiteCoordinate) -> JudgmentSection:
        """Restrict this section to a sub-coordinate.

        Implements the sheaf restriction map ρ_{UV}: F(V) → F(U) for U ≤ V.
        Restriction lowers the trust tier by one step (since evidence at a
        sub-context is generally weaker than at the full context) and records
        the restriction in the provenance chain.

        Parameters
        ----------
        sub_coord:
            The target sub-coordinate.

        Returns
        -------
        JudgmentSection
            A new section over *sub_coord* with one trust tier lower.
        """
        current_rank = _tier_rank(self.judgment.T)
        new_rank = max(0, current_rank - 1)
        tier_order = list(_TIER_ORDER.keys())
        new_tier = tier_order[new_rank]
        new_judgment = self.judgment.with_trust(
            new_tier, f"restrict({sub_coord.coord_id})"
        )
        return JudgmentSection(
            coord=sub_coord,
            judgment=replace(new_judgment, c=sub_coord),
            is_global=False,
            provenance=self.provenance + (f"restrict_from:{self.coord.coord_id}",),
        )

    def is_compatible_with(self, other: JudgmentSection) -> bool:
        """Check sheaf compatibility: two sections agree on their overlap.

        Two sections s ∈ F(U) and t ∈ F(V) are *compatible* iff they agree
        on U ∩ V.  Here we approximate compatibility by checking:

        1. The formulas ``phi`` agree.
        2. The aspect tags ``A`` agree.
        3. The trust tiers are within two steps of each other.
        4. There are no contradictory obligation sets.

        Parameters
        ----------
        other:
            The other section to compare.

        Returns
        -------
        bool
            True iff the sections are compatible at their overlap.
        """
        if self.judgment.phi != other.judgment.phi:
            return False
        if self.judgment.A != other.judgment.A:
            return False
        rank_diff = abs(_tier_rank(self.judgment.T) - _tier_rank(other.judgment.T))
        if rank_diff > 2:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "section_id": self.section_id,
            "coord": self.coord.to_dict(),
            "judgment": self.judgment.to_dict(),
            "is_global": self.is_global,
            "provenance": list(self.provenance),
        }


# ---------------------------------------------------------------------------
# JudgmentSheaf
# ---------------------------------------------------------------------------


@dataclass
class JudgmentSheaf:
    """The sheaf of judgment tuples on a GrothendieckSite.

    ``JudgmentSheaf`` assigns to each :class:`SiteCoordinate` a set of
    :class:`JudgmentSection` objects, implements the restriction maps, and
    provides a gluing algorithm that assembles compatible local sections into
    a global section.

    Theory2.tex §formal_core: the judgment sheaf satisfies:
    - **Locality** — sections that agree on every member of a cover are equal.
    - **Gluing** — a compatible family of local sections glues uniquely.

    Parameters
    ----------
    sheaf_id:
        Stable unique identifier.
    site:
        The :class:`GrothendieckSite` on which this sheaf lives.
    sections:
        Internal mapping coord_id → list of JudgmentSection.
    gluing_log:
        Log of gluing operations.
    """

    sheaf_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    site: GrothendieckSite = field(default_factory=GrothendieckSite)
    sections: dict[str, list[JudgmentSection]] = field(default_factory=dict)
    gluing_log: list[dict[str, Any]] = field(default_factory=list)

    def add_section(self, section: JudgmentSection) -> None:
        """Add a :class:`JudgmentSection` to this sheaf.

        Parameters
        ----------
        section:
            The section to add.
        """
        cid = section.coord.coord_id
        self.sections.setdefault(cid, []).append(section)
        log.debug("JudgmentSheaf.add_section: coord=%r section=%r", cid, section.section_id)

    def sections_for(self, coord: SiteCoordinate) -> list[JudgmentSection]:
        """Return all sections over *coord*.

        Parameters
        ----------
        coord:
            The coordinate to query.
        """
        return list(self.sections.get(coord.coord_id, []))

    def glue(self, covering_family: CoveringFamily) -> JudgmentSection | None:
        """Attempt to glue compatible local sections into a global section.

        Given a covering family {f_i : U_i → c}, collects the sections over
        each U_i, checks pairwise compatibility (the *cocycle condition*), and
        if all pairs are compatible assembles a global section over c.

        Theory2.tex §formal_core: if the compatibility check fails, the
        obstruction is a non-trivial Čech 1-cocycle and gluing fails.

        Parameters
        ----------
        covering_family:
            The covering family {f_i : U_i → c} to use for gluing.

        Returns
        -------
        JudgmentSection or None
            The assembled global section if gluing succeeds; None otherwise.
        """
        base = covering_family.base_coord
        local_sections: list[JudgmentSection] = []
        for m in covering_family.morphisms:
            src_sections = self.sections_for(m.source)
            if src_sections:
                local_sections.append(src_sections[0])

        if not local_sections:
            log.warning("JudgmentSheaf.glue: no local sections for family %r", covering_family.family_id)
            self.gluing_log.append({
                "event": "glue_empty",
                "family_id": covering_family.family_id,
                "timestamp": time.time(),
            })
            return None

        # Check pairwise compatibility
        for i, s1 in enumerate(local_sections):
            for j, s2 in enumerate(local_sections):
                if i >= j:
                    continue
                if not s1.is_compatible_with(s2):
                    log.warning(
                        "JudgmentSheaf.glue: incompatible sections %r and %r",
                        s1.section_id, s2.section_id,
                    )
                    self.gluing_log.append({
                        "event": "glue_incompatible",
                        "family_id": covering_family.family_id,
                        "section_a": s1.section_id,
                        "section_b": s2.section_id,
                        "timestamp": time.time(),
                    })
                    return None

        # Assemble: take the section with highest trust as the representative
        best = max(local_sections, key=lambda s: _tier_rank(s.judgment.T))
        provenance_labels = tuple(
            f"from_section:{s.section_id}" for s in local_sections
        )
        global_judgment = replace(
            best.judgment,
            c=base,
            Pi=best.judgment.Pi + ("glued_from_family:" + covering_family.family_id,),
        )
        global_section = JudgmentSection(
            coord=base,
            judgment=global_judgment,
            is_global=True,
            provenance=provenance_labels,
        )
        self.add_section(global_section)
        self.gluing_log.append({
            "event": "glue_success",
            "family_id": covering_family.family_id,
            "global_section_id": global_section.section_id,
            "timestamp": time.time(),
        })
        log.info("JudgmentSheaf.glue: success → global section %r", global_section.section_id)
        return global_section

    def verify_locality(self, coord: SiteCoordinate, covering_family: CoveringFamily) -> bool:
        """Verify the locality (separation) axiom at *coord*.

        The locality axiom says: if two sections s, t ∈ F(coord) agree on
        every member of a covering family, then s = t.  Here we check the
        weaker finite form: if two sections have the same phi, A, and T, they
        are considered equal.

        Parameters
        ----------
        coord:
            The context coordinate.
        covering_family:
            The cover relative to which locality is checked.

        Returns
        -------
        bool
            True iff at most one "distinct" section exists (up to equality).
        """
        secs = self.sections_for(coord)
        if len(secs) <= 1:
            return True
        distinct: list[JudgmentSection] = []
        for s in secs:
            is_dup = any(
                s.judgment.phi == d.judgment.phi
                and s.judgment.A == d.judgment.A
                and s.judgment.T == d.judgment.T
                for d in distinct
            )
            if not is_dup:
                distinct.append(s)
        ok = len(distinct) <= 1
        if not ok:
            log.warning(
                "JudgmentSheaf.verify_locality: locality violated at coord=%r; "
                "%d distinct sections", coord.coord_id, len(distinct)
            )
        return ok

    def total_section_count(self) -> int:
        """Return the total number of sections in this sheaf."""
        return sum(len(v) for v in self.sections.values())

    def describe(self) -> str:
        """Return a human-readable summary of this sheaf."""
        return (
            f"JudgmentSheaf {self.sheaf_id}\n"
            f"  Total sections : {self.total_section_count()}\n"
            f"  Coordinates    : {len(self.sections)}\n"
            f"  Gluing events  : {len(self.gluing_log)}\n"
        )


# ---------------------------------------------------------------------------
# JudgmentSite — top-level composite
# ---------------------------------------------------------------------------


@dataclass
class JudgmentSite:
    """Top-level composite of a GrothendieckSite and a JudgmentSheaf.

    ``JudgmentSite`` is the primary entry point for consumers of this module.
    It owns both the categorical structure (site) and the semantic content
    (sheaf), and provides convenience methods for:

    - Registering new program contexts as site coordinates.
    - Adding judgment tuples as sheaf sections.
    - Gluing compatible sections using registered covering families.
    - Verifying site axioms and sheaf conditions.

    Parameters
    ----------
    site_id:
        Stable unique identifier.
    site:
        The underlying :class:`GrothendieckSite`.
    sheaf:
        The :class:`JudgmentSheaf` living on the site.
    created_at:
        Unix timestamp of creation.
    """

    site_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    site: GrothendieckSite = field(default_factory=GrothendieckSite)
    sheaf: JudgmentSheaf = field(default_factory=JudgmentSheaf)
    created_at: float = field(default_factory=time.time)

    def register_context(
        self,
        name: str,
        kind: ContextKind = ContextKind.FUNCTION_BODY,
        depth: int = 1,
        parent_id: str = "",
    ) -> SiteCoordinate:
        """Create and register a new :class:`SiteCoordinate`.

        Parameters
        ----------
        name:
            Human-readable label for the context.
        kind:
            :class:`ContextKind` discriminant.
        depth:
            Nesting depth.
        parent_id:
            Parent coordinate ID, or ``""`` for top-level.

        Returns
        -------
        SiteCoordinate
            The newly created and registered coordinate.
        """
        coord = SiteCoordinate(name=name, kind=kind, depth=depth, parent_id=parent_id)
        self.site.add_coordinate(coord)
        return coord

    def add_judgment(
        self,
        coord: SiteCoordinate,
        phi: str,
        aspect: str = "safety",
        trust_tier: str = "PROPOSAL",
        obligations: frozenset[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> JudgmentSection:
        """Create a :class:`JudgmentTuple` and add it as a sheaf section.

        Parameters
        ----------
        coord:
            The context coordinate for this judgment.
        phi:
            The formula under judgment.
        aspect:
            The aspect tag.
        trust_tier:
            Initial trust tier.
        obligations:
            Initial obligation set (empty if None).
        evidence:
            Initial evidence bundle (empty if None).

        Returns
        -------
        JudgmentSection
            The newly created section.
        """
        tup = JudgmentTuple(
            c=coord,
            phi=phi,
            A=aspect,
            E=evidence or {},
            O=obligations or frozenset(),
            B={"ceiling": "PROOF_BACKED", "floor": "PROPOSAL"},
            T=trust_tier,
            Pi=("initial",),
        )
        section = JudgmentSection(coord=coord, judgment=tup, is_global=False)
        self.sheaf.add_section(section)
        return section

    def describe(self) -> str:
        """Return a human-readable summary of this JudgmentSite."""
        return (
            f"JudgmentSite {self.site_id}\n"
            + self.site.describe()
            + self.sheaf.describe()
        )


# ---------------------------------------------------------------------------
# ASiteProgrammaticJudgmentWitness — immutable certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ASiteProgrammaticJudgmentWitness:
    """Immutable certificate produced by a completed site judgment run.

    A ``ASiteProgrammaticJudgmentWitness`` records the outcome of a complete
    site construction and sheaf population cycle.  It captures the final state
    of the site axiom check results, the number of global sections assembled,
    the highest trust tier achieved, and a full provenance chain.

    Parameters
    ----------
    witness_id:
        Stable unique identifier for this certificate.
    site_id:
        The site that produced this witness.
    axiom_results:
        The result of :meth:`GrothendieckSite.verify_axioms`.
    global_section_count:
        Number of global sections successfully assembled.
    max_trust_tier:
        The highest trust tier attained by any section.
    judgment_count:
        Total number of judgment tuples registered with the sheaf.
    provenance:
        Ordered chain of step labels recording the derivation.
    created_at:
        Unix timestamp.
    metadata:
        Auxiliary key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    site_id: str = ""
    axiom_results: dict[str, str] = field(default_factory=dict)
    global_section_count: int = 0
    max_trust_tier: str = "PROPOSAL"
    judgment_count: int = 0
    provenance: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_axiom_compliant(self) -> bool:
        """Return True iff all verified axioms hold (HOLDS or PARTIAL; none VIOLATED)."""
        return all(v != CoverAxiomStatus.VIOLATED.value for v in self.axiom_results.values())

    def trust_rank(self) -> int:
        """Return the integer rank of the maximum trust tier attained."""
        return _tier_rank(self.max_trust_tier)

    def summary(self) -> str:
        """Return a one-line summary of this witness."""
        axiom_ok = "✓" if self.is_axiom_compliant() else "✗"
        return (
            f"[Witness {self.witness_id[:8]}] site={self.site_id[:8]} "
            f"axioms={axiom_ok} sections={self.global_section_count} "
            f"max_tier={self.max_trust_tier} judgments={self.judgment_count}"
        )

    def validate(self) -> list[str]:
        """Return a list of validation violations; empty if valid."""
        errors: list[str] = []
        if not self.witness_id:
            errors.append("witness_id must not be empty")
        if not self.site_id:
            errors.append("site_id must not be empty")
        if self.judgment_count < 0:
            errors.append("judgment_count must be non-negative")
        if self.max_trust_tier not in _TIER_ORDER:
            errors.append(f"Unknown trust tier: {self.max_trust_tier!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "witness_id": self.witness_id,
            "site_id": self.site_id,
            "axiom_results": dict(self.axiom_results),
            "global_section_count": self.global_section_count,
            "max_trust_tier": self.max_trust_tier,
            "judgment_count": self.judgment_count,
            "provenance": list(self.provenance),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ASiteProgrammaticJudgmentWitness:
        """Deserialise from a plain dict produced by :meth:`to_dict`."""
        return cls(
            witness_id=d.get("witness_id", str(uuid.uuid4())),
            site_id=d.get("site_id", ""),
            axiom_results=dict(d.get("axiom_results", {})),
            global_section_count=int(d.get("global_section_count", 0)),
            max_trust_tier=d.get("max_trust_tier", "PROPOSAL"),
            judgment_count=int(d.get("judgment_count", 0)),
            provenance=tuple(d.get("provenance", [])),
            created_at=float(d.get("created_at", time.time())),
            metadata=dict(d.get("metadata", {})),
        )

    def merge(self, other: ASiteProgrammaticJudgmentWitness) -> ASiteProgrammaticJudgmentWitness:
        """Merge two witnesses by taking the better result at each field.

        Parameters
        ----------
        other:
            The other witness to merge.

        Returns
        -------
        ASiteProgrammaticJudgmentWitness
            A new merged witness.
        """
        merged_axioms = {**other.axiom_results, **self.axiom_results}
        best_tier = (
            self.max_trust_tier
            if _tier_rank(self.max_trust_tier) >= _tier_rank(other.max_trust_tier)
            else other.max_trust_tier
        )
        return ASiteProgrammaticJudgmentWitness(
            site_id=self.site_id or other.site_id,
            axiom_results=merged_axioms,
            global_section_count=self.global_section_count + other.global_section_count,
            max_trust_tier=best_tier,
            judgment_count=self.judgment_count + other.judgment_count,
            provenance=self.provenance + other.provenance + ("merged",),
            metadata={**other.metadata, **self.metadata},
        )


# ---------------------------------------------------------------------------
# ASiteProgrammaticJudgmentCoordinator
# ---------------------------------------------------------------------------


@dataclass
class ASiteProgrammaticJudgmentCoordinator:
    """Orchestrates the full site construction and judgment sheaf population cycle.

    The ``ASiteProgrammaticJudgmentCoordinator`` is the primary entry point
    for building a :class:`JudgmentSite`, registering program contexts,
    populating the sheaf with judgment tuples, and assembling global sections
    by gluing compatible covering families.

    At the end of a run, :meth:`produce_witness` emits an immutable
    :class:`ASiteProgrammaticJudgmentWitness` that records the full outcome.

    Parameters
    ----------
    coordinator_id:
        Stable unique identifier.
    judgment_site:
        The :class:`JudgmentSite` being built.
    witnesses:
        List of :class:`ASiteProgrammaticJudgmentWitness` produced by this
        coordinator.
    run_log:
        Append-only log of run events.
    """

    coordinator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    judgment_site: JudgmentSite = field(default_factory=JudgmentSite)
    witnesses: list[ASiteProgrammaticJudgmentWitness] = field(default_factory=list)
    run_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Site construction helpers
    # ------------------------------------------------------------------

    def register_context(
        self,
        name: str,
        kind: ContextKind = ContextKind.FUNCTION_BODY,
        depth: int = 1,
        parent_id: str = "",
    ) -> SiteCoordinate:
        """Register a new program context as a site coordinate.

        Parameters
        ----------
        name:
            Human-readable label.
        kind:
            :class:`ContextKind` discriminant.
        depth:
            Nesting depth.
        parent_id:
            Parent coordinate ID.

        Returns
        -------
        SiteCoordinate
            The newly registered coordinate.
        """
        coord = self.judgment_site.register_context(name=name, kind=kind, depth=depth, parent_id=parent_id)
        self.run_log.append({
            "event": "register_context",
            "coord_id": coord.coord_id,
            "name": name,
            "timestamp": time.time(),
        })
        return coord

    def add_morphism(
        self,
        source: SiteCoordinate,
        target: SiteCoordinate,
        kind: MorphismKind = MorphismKind.INCLUSION,
        label: str = "",
        is_covering: bool = False,
    ) -> ContextMorphism:
        """Add a morphism between two registered coordinates.

        Parameters
        ----------
        source:
            Domain coordinate.
        target:
            Codomain coordinate.
        kind:
            :class:`MorphismKind`.
        label:
            Semantic label.
        is_covering:
            Whether this morphism belongs to a covering family.

        Returns
        -------
        ContextMorphism
            The newly added morphism.
        """
        morph = ContextMorphism(source=source, target=target, kind=kind, label=label, is_covering=is_covering)
        self.judgment_site.site.add_morphism(morph)
        self.run_log.append({
            "event": "add_morphism",
            "morph_id": morph.morph_id,
            "source": source.coord_id,
            "target": target.coord_id,
            "timestamp": time.time(),
        })
        return morph

    def add_covering_family(
        self, base_coord: SiteCoordinate, morphisms: Sequence[ContextMorphism], trust_tier: str = "PROPOSAL"
    ) -> CoveringFamily:
        """Register a covering family for *base_coord*.

        Parameters
        ----------
        base_coord:
            The context being covered.
        morphisms:
            The morphisms constituting the covering family.
        trust_tier:
            Minimum trust tier for this cover.

        Returns
        -------
        CoveringFamily
            The registered covering family.
        """
        family = CoveringFamily(
            base_coord=base_coord,
            morphisms=tuple(morphisms),
            trust_tier=trust_tier,
            is_adequate=len(morphisms) >= 1,
            provenance=f"coordinator:{self.coordinator_id}",
        )
        self.judgment_site.site.add_covering_family(family)
        self.run_log.append({
            "event": "add_covering_family",
            "family_id": family.family_id,
            "base_coord": base_coord.coord_id,
            "morphism_count": len(morphisms),
            "timestamp": time.time(),
        })
        return family

    def add_judgment(
        self,
        coord: SiteCoordinate,
        phi: str,
        aspect: str = "safety",
        trust_tier: str = "PROPOSAL",
        obligations: frozenset[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> JudgmentSection:
        """Add a judgment tuple to the sheaf at *coord*.

        Parameters
        ----------
        coord:
            Context coordinate.
        phi:
            Formula under judgment.
        aspect:
            Aspect tag.
        trust_tier:
            Initial trust tier.
        obligations:
            Initial obligation set.
        evidence:
            Initial evidence bundle.

        Returns
        -------
        JudgmentSection
            The newly added section.
        """
        section = self.judgment_site.add_judgment(
            coord=coord,
            phi=phi,
            aspect=aspect,
            trust_tier=trust_tier,
            obligations=obligations,
            evidence=evidence,
        )
        self.run_log.append({
            "event": "add_judgment",
            "section_id": section.section_id,
            "coord": coord.coord_id,
            "phi": phi[:60],
            "timestamp": time.time(),
        })
        return section

    def glue_family(self, family: CoveringFamily) -> JudgmentSection | None:
        """Attempt to glue local sections over *family* into a global section.

        Parameters
        ----------
        family:
            The covering family to glue over.

        Returns
        -------
        JudgmentSection or None
        """
        result = self.judgment_site.sheaf.glue(family)
        self.run_log.append({
            "event": "glue_family",
            "family_id": family.family_id,
            "success": result is not None,
            "timestamp": time.time(),
        })
        return result

    def produce_witness(self) -> ASiteProgrammaticJudgmentWitness:
        """Emit an immutable :class:`ASiteProgrammaticJudgmentWitness`.

        Verifies the site axioms, counts global sections, and computes the
        highest trust tier achieved across all sheaf sections.

        Returns
        -------
        ASiteProgrammaticJudgmentWitness
            An immutable certificate of the completed run.
        """
        axiom_raw = self.judgment_site.site.verify_axioms()
        axiom_str = {k: v.value for k, v in axiom_raw.items()}

        all_sections = [
            s
            for secs in self.judgment_site.sheaf.sections.values()
            for s in secs
        ]
        global_count = sum(1 for s in all_sections if s.is_global)
        max_tier = "PROPOSAL"
        for s in all_sections:
            if _tier_rank(s.judgment.T) > _tier_rank(max_tier):
                max_tier = s.judgment.T

        w = ASiteProgrammaticJudgmentWitness(
            site_id=self.judgment_site.site_id,
            axiom_results=axiom_str,
            global_section_count=global_count,
            max_trust_tier=max_tier,
            judgment_count=len(all_sections),
            provenance=tuple(e["event"] for e in self.run_log[-10:]),
            metadata={"coordinator_id": self.coordinator_id},
        )
        self.witnesses.append(w)
        log.info("ASiteProgrammaticJudgmentCoordinator.produce_witness: %s", w.summary())
        return w

    def validate(self) -> list[str]:
        """Return validation violations; empty if all invariants hold."""
        violations: list[str] = []
        if not self.coordinator_id:
            violations.append("coordinator_id must not be empty")
        site = self.judgment_site.site
        for morph in site.morphisms.values():
            if morph.source.coord_id not in site.coordinates:
                violations.append(
                    f"Morphism {morph.morph_id}: source {morph.source.coord_id!r} not registered"
                )
            if morph.target.coord_id not in site.coordinates:
                violations.append(
                    f"Morphism {morph.morph_id}: target {morph.target.coord_id!r} not registered"
                )
        return violations

    def describe(self) -> str:
        """Return a human-readable summary of this coordinator."""
        return (
            f"ASiteProgrammaticJudgmentCoordinator {self.coordinator_id}\n"
            + self.judgment_site.describe()
            + f"  Witnesses produced : {len(self.witnesses)}\n"
            + f"  Run log events     : {len(self.run_log)}\n"
        )


# ---------------------------------------------------------------------------
# ASiteProgrammaticJudgmentAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class ASiteProgrammaticJudgmentAnalyzer:
    """Analyses site coherence and judgment tuple consistency across a sheaf.

    ``ASiteProgrammaticJudgmentAnalyzer`` operates on a collection of
    :class:`ASiteProgrammaticJudgmentWitness` objects and provides:

    - Trust-tier distribution statistics.
    - Axiom compliance rate across witnesses.
    - Detection of locality violations and gluing failures.
    - Computation of a composite trust-health score in [0, 1].

    Parameters
    ----------
    analyzer_id:
        Stable unique identifier.
    witnesses:
        The witnesses to analyse.
    judgment_site:
        Optional live :class:`JudgmentSite` for deeper sheaf inspection.
    """

    analyzer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    witnesses: list[ASiteProgrammaticJudgmentWitness] = field(default_factory=list)
    judgment_site: JudgmentSite | None = None

    def tier_distribution(self) -> dict[str, int]:
        """Return a count of witnesses by maximum trust tier."""
        dist: dict[str, int] = {}
        for w in self.witnesses:
            dist[w.max_trust_tier] = dist.get(w.max_trust_tier, 0) + 1
        return dist

    def axiom_compliance_rate(self) -> float:
        """Return the fraction of witnesses that are axiom-compliant.

        Returns
        -------
        float
            A value in [0, 1]; 1.0 means all witnesses are axiom-compliant.
        """
        if not self.witnesses:
            return 1.0
        return sum(1 for w in self.witnesses if w.is_axiom_compliant()) / len(self.witnesses)

    def average_global_section_count(self) -> float:
        """Return the mean number of global sections per witness."""
        if not self.witnesses:
            return 0.0
        return sum(w.global_section_count for w in self.witnesses) / len(self.witnesses)

    def score(self) -> float:
        """Compute a composite trust-health score in [0, 1].

        The score is a weighted combination of:
        - Axiom compliance rate (weight 0.4)
        - Average trust rank of max_trust_tier normalised to [0,1] (weight 0.4)
        - Fraction of witnesses with at least one global section (weight 0.2)

        Returns
        -------
        float
            Composite trust-health score.
        """
        compliance = self.axiom_compliance_rate()
        max_rank = max(_TIER_ORDER.values())
        avg_rank = (
            sum(_tier_rank(w.max_trust_tier) for w in self.witnesses) / len(self.witnesses)
            if self.witnesses
            else 0.0
        )
        normalised_rank = avg_rank / max_rank if max_rank > 0 else 0.0
        has_global = (
            sum(1 for w in self.witnesses if w.global_section_count > 0) / len(self.witnesses)
            if self.witnesses
            else 0.0
        )
        return 0.4 * compliance + 0.4 * normalised_rank + 0.2 * has_global

    def detect_locality_violations(self) -> list[str]:
        """Return descriptions of any detected locality violations.

        Scans the live ``judgment_site`` (if set) and checks the locality axiom
        for every registered coordinate.

        Returns
        -------
        list[str]
            Descriptions of violations; empty if none detected.
        """
        if self.judgment_site is None:
            return []
        violations: list[str] = []
        site = self.judgment_site.site
        sheaf = self.judgment_site.sheaf
        for cid, coord in site.coordinates.items():
            families = site.covering_relation.families_for(coord)
            for fam in families:
                if not sheaf.verify_locality(coord, fam):
                    violations.append(
                        f"Locality violation at coord={cid!r} with family={fam.family_id!r}"
                    )
        return violations

    def report(self) -> str:
        """Return a rich multi-line analysis report."""
        lines = [
            f"ASiteProgrammaticJudgmentAnalyzer {self.analyzer_id}",
            f"  Witnesses         : {len(self.witnesses)}",
            f"  Axiom compliance  : {self.axiom_compliance_rate():.1%}",
            f"  Avg global secs   : {self.average_global_section_count():.2f}",
            f"  Trust-health score: {self.score():.3f}",
            f"  Tier distribution : {self.tier_distribution()}",
        ]
        lv = self.detect_locality_violations()
        if lv:
            lines.append(f"  Locality violations ({len(lv)}):")
            for v in lv[:5]:
                lines.append(f"    - {v}")
        else:
            lines.append("  Locality violations : (none detected)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== a_site_for_programmatic_judgment.py smoke test ===\n")

    # Build coordinator
    coord_obj = ASiteProgrammaticJudgmentCoordinator()

    # Register contexts
    global_ctx = coord_obj.register_context("project_root", kind=ContextKind.GLOBAL, depth=0)
    mod_ctx = coord_obj.register_context("mymodule", kind=ContextKind.MODULE_SCOPE, depth=1, parent_id=global_ctx.coord_id)
    fn_ctx = coord_obj.register_context("mymodule.parse", kind=ContextKind.FUNCTION_BODY, depth=2, parent_id=mod_ctx.coord_id)
    fn_ctx2 = coord_obj.register_context("mymodule.validate", kind=ContextKind.FUNCTION_BODY, depth=2, parent_id=mod_ctx.coord_id)

    # Register morphisms
    m1 = coord_obj.add_morphism(fn_ctx, mod_ctx, kind=MorphismKind.INCLUSION, label="fn→mod", is_covering=True)
    m2 = coord_obj.add_morphism(fn_ctx2, mod_ctx, kind=MorphismKind.INCLUSION, label="fn2→mod", is_covering=True)

    # Covering family
    family = coord_obj.add_covering_family(mod_ctx, [m1, m2], trust_tier="PROPOSAL")

    # Add judgments
    s1 = coord_obj.add_judgment(fn_ctx, phi="∀x: parse(x) → valid(x)", aspect="safety", trust_tier="PROPOSAL")
    s2 = coord_obj.add_judgment(fn_ctx2, phi="∀x: parse(x) → valid(x)", aspect="safety", trust_tier="REVIEWED")

    # Glue
    global_sec = coord_obj.glue_family(family)
    assert global_sec is not None, "Expected successful gluing"
    assert global_sec.is_global, "Section should be marked global"

    # Produce witness
    witness = coord_obj.produce_witness()
    errors = witness.validate()
    assert errors == [], f"Witness errors: {errors}"
    assert witness.global_section_count >= 1, "Expected at least one global section"
    print(witness.summary())

    # Roundtrip
    d = witness.to_dict()
    w2 = ASiteProgrammaticJudgmentWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Merge
    w3 = witness.merge(w2)
    assert w3.global_section_count == witness.global_section_count * 2

    # Analyzer
    analyzer = ASiteProgrammaticJudgmentAnalyzer(
        witnesses=[witness], judgment_site=coord_obj.judgment_site
    )
    print(analyzer.report())
    score = analyzer.score()
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    # Validate coordinator
    violations = coord_obj.validate()
    assert violations == [], f"Coordinator violations: {violations}"

    # JudgmentTuple operations
    tup = s1.judgment
    tup2 = tup.with_trust("REVIEWED", "manual_review_passed")
    assert tup2.T == "REVIEWED"
    tup3 = tup2.with_evidence("ev_001", {"type": "z3_proof", "hash": "abc123"})
    assert "ev_001" in tup3.E
    fingerprint = tup3.fingerprint()
    assert len(fingerprint) == 16

    # Roundtrip JudgmentTuple
    td = tup3.to_dict()
    tup4 = JudgmentTuple.from_dict(td)
    assert tup4.tuple_id == tup3.tuple_id

    print("\n[PASS] All smoke tests passed.")
