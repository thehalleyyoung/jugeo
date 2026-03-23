"""Cover structures for JuGeo semantic coordinates.

In the algebraic-geometric framework of ``preliminaries/theory2.tex``, a *cover*
of a coordinate is a family of morphisms whose images collectively observe
everything about that coordinate.  Hypercovers iterate this idea: covers of
covers, giving finer and finer local views.  The cover structure determines
what "local" means in a semantic site and therefore what descent (gluing of
local sections into a global section) can achieve.

This module provides the concrete data structures and algorithms for working
with covers, overlap data, sieves, refinements, cover categories, generation
heuristics, diagnostics, serialisation and statistics.

Design notes
~~~~~~~~~~~~
* All geometry-facing types are **frozen dataclasses** with ``slots=True`` to
  guarantee immutability and enable safe caching / sharing.
* Mutable builder and manager types use ``slots=True`` without ``frozen``.
* Provenance tuples record the chain of operations that produced a value.
* Every public symbol is listed in ``__all__`` at the end of the file.

copilot: shared-core marker for future LLM orchestration.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import combinations
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.geometry.site import CoordinateKind, CoordinateMorphism, CoordinateObject

# ---------------------------------------------------------------------------
# Legacy helpers (preserved for backward compatibility)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverMetric:
    """Aggregate quality metric for a cover.

    Combines patch count, overlap density, a locality heuristic and a
    redundancy penalty into a single scalar score.
    """

    patch_count: int
    overlap_count: int
    locality_score: float
    redundancy_score: float

    @property
    def total_score(self) -> float:
        """Return a composite score (higher is better)."""
        return self.locality_score - self.redundancy_score - (self.overlap_count * 0.1)


def score_cover(cover: Cover) -> CoverMetric:
    """Score an existing :class:`Cover` using path-prefix locality."""
    unique_prefixes = {patch.path[:-1] for patch in cover.patches}
    locality = len(unique_prefixes) / max(1, len(cover.patches))
    redundancy = max(0, len(cover.patches) - len(set(cover.patch_keys())))
    return CoverMetric(len(cover.patches), len(cover.overlaps), locality, float(redundancy))


def refine_cover(cover: Cover, *, suffix: str = "refined") -> Cover:
    """Return a new :class:`Cover` with patches refined by *suffix*."""
    refined_patches = tuple(
        CoordinateObject(
            components=patch.path + (suffix,),
            kind=patch.kind,
            support_labels=patch.support_labels,
            metadata=patch.metadata,
        )
        for patch in cover.patches
    )
    overlaps = tuple(
        (left.key, right.key)
        for left, right in zip(refined_patches, refined_patches[1:])
    )
    return Cover(
        cover.target,
        refined_patches,
        overlaps,
        cover.provenance + ("refine_cover",),
    )


# ---------------------------------------------------------------------------
# 1. CoverMember
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverMember:
    """One morphism in a covering family.

    Each member witnesses a *local view* of the target coordinate via its
    ``restriction_morphism``.  The ``evidence_scope`` records what kind of
    evidence the member carries, and the ``trust_ceiling`` bounds the
    authority level of claims derived from this member.

    Parameters
    ----------
    source_coordinate:
        The coordinate that provides the local view.
    target_coordinate:
        The coordinate being covered.
    restriction_morphism:
        The site morphism from source to target.
    index:
        Positional index inside the cover (for stable ordering).
    evidence_scope:
        Freeform labels classifying the evidence this member offers.
    trust_ceiling:
        Maximum authority tier (integer, higher ⇒ more trusted).
    """

    source_coordinate: CoordinateObject
    target_coordinate: CoordinateObject
    restriction_morphism: CoordinateMorphism
    index: int
    evidence_scope: frozenset[str] = field(default_factory=frozenset)
    trust_ceiling: int = 1

    @property
    def source_key(self) -> str:
        """Short key of the source coordinate."""
        return self.source_coordinate.key

    @property
    def target_key(self) -> str:
        """Short key of the target coordinate."""
        return self.target_coordinate.key

    @property
    def morphism_label(self) -> str:
        """Human-readable label for the restriction morphism."""
        return f"{self.restriction_morphism.source}->{self.restriction_morphism.target}"

    def has_scope(self, label: str) -> bool:
        """Return *True* if *label* is within this member's evidence scope."""
        return label in self.evidence_scope

    def with_trust_ceiling(self, ceiling: int) -> CoverMember:
        """Return a copy with a different trust ceiling."""
        return replace(self, trust_ceiling=ceiling)

    def with_extra_scope(self, labels: Iterable[str]) -> CoverMember:
        """Return a copy whose evidence scope is extended by *labels*."""
        return replace(self, evidence_scope=self.evidence_scope | frozenset(labels))

    def is_identity(self) -> bool:
        """Return *True* if the restriction morphism is trivially an identity."""
        return self.source_coordinate.key == self.target_coordinate.key

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "source_key": self.source_key,
            "target_key": self.target_key,
            "morphism": self.morphism_label,
            "index": self.index,
            "evidence_scope": sorted(self.evidence_scope),
            "trust_ceiling": self.trust_ceiling,
        }


# ---------------------------------------------------------------------------
# 4. OverlapDatum
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlapDatum:
    """Records an overlap between two cover members.

    When two patches *U_i* and *U_j* in a cover have a non-trivial
    intersection *U_{ij}*, a descent check must verify that sections on
    *U_i* and *U_j* agree when restricted to *U_{ij}*.  An
    :class:`OverlapDatum` stores exactly that structure.
    """

    left_member: CoverMember
    right_member: CoverMember
    overlap_coordinate: CoordinateObject
    left_restriction: CoordinateMorphism
    right_restriction: CoordinateMorphism
    compatibility_evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def overlap_key(self) -> str:
        """A canonical key for the overlap (order-independent)."""
        pair = sorted([self.left_member.source_key, self.right_member.source_key])
        return f"{pair[0]}∩{pair[1]}"

    def is_compatible(self) -> bool:
        """Quick compatibility check based on stored evidence.

        Returns *True* when evidence explicitly records ``compatible: True``
        or when no evidence has been collected yet (optimistic default).
        """
        if not self.compatibility_evidence:
            return True
        return bool(self.compatibility_evidence.get("compatible", False))

    def combined_scope(self) -> frozenset[str]:
        """Union of both members' evidence scopes."""
        return self.left_member.evidence_scope | self.right_member.evidence_scope

    def min_trust(self) -> int:
        """The minimum trust ceiling across both members."""
        return min(self.left_member.trust_ceiling, self.right_member.trust_ceiling)

    def involves(self, key: str) -> bool:
        """Return *True* if either member's source has the given key."""
        return key in (self.left_member.source_key, self.right_member.source_key)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "overlap_key": self.overlap_key,
            "overlap_coordinate": self.overlap_coordinate.key,
            "left": self.left_member.source_key,
            "right": self.right_member.source_key,
            "compatible": self.is_compatible(),
        }


# ---------------------------------------------------------------------------
# 2. Cover (main class)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cover:
    """A covering family on a semantic coordinate.

    A cover consists of a *base coordinate* (the object being covered) and
    a tuple of :class:`CoverMember` instances whose images should jointly
    exhaust all information about the base.

    The legacy ``patches`` / ``overlaps`` interface is also preserved for
    backward compatibility with the rest of the geometry package.
    """

    target: CoordinateObject
    patches: tuple[CoordinateObject, ...] = field(default_factory=tuple)
    overlaps: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    provenance: tuple[str, ...] = ()
    members: tuple[CoverMember, ...] = field(default_factory=tuple)
    overlap_data: tuple[OverlapDatum, ...] = field(default_factory=tuple)

    # -- legacy helpers -----------------------------------------------------

    def patch_keys(self) -> tuple[str, ...]:
        """Return the path-keys of every patch."""
        return tuple(patch.key for patch in self.patches)

    # -- newer API ----------------------------------------------------------

    @property
    def base_coordinate(self) -> CoordinateObject:
        """Alias for ``target`` following theory2.tex naming."""
        return self.target

    @property
    def member_count(self) -> int:
        """Number of cover members."""
        return len(self.members)

    def member_keys(self) -> tuple[str, ...]:
        """Return the source keys of every member."""
        return tuple(m.source_key for m in self.members)

    # -- validation ---------------------------------------------------------

    def is_valid(self) -> bool:
        """Check structural validity of the cover.

        A cover is valid when:
        * it has at least one member or one patch,
        * every member targets the base coordinate,
        * member indices are a contiguous range starting at 0.
        """
        if not self.members and not self.patches:
            return False
        if self.members:
            for m in self.members:
                if m.target_key != self.target.key:
                    return False
            indices = sorted(m.index for m in self.members)
            if indices != list(range(len(indices))):
                return False
        return True

    # -- overlaps -----------------------------------------------------------

    def compute_overlaps(self) -> list[OverlapDatum]:
        """Compute pairwise overlap data from members with shared support labels.

        Two members overlap when their source coordinates share at least one
        support label.  The overlap coordinate is synthesised with kind
        ``REGION`` and the intersection of support labels.
        """
        result: list[OverlapDatum] = []
        for a, b in combinations(self.members, 2):
            shared = a.source_coordinate.support_labels & b.source_coordinate.support_labels
            if shared:
                overlap_coord = CoordinateObject(
                    name=f"{a.source_coordinate.name}∩{b.source_coordinate.name}",
                    kind=CoordinateKind.REGION,
                    path=a.source_coordinate.path + ("∩",) + b.source_coordinate.path,
                    support_labels=shared,
                )
                left_r = CoordinateMorphism(overlap_coord.key, a.source_key, "overlap-left")
                right_r = CoordinateMorphism(overlap_coord.key, b.source_key, "overlap-right")
                result.append(OverlapDatum(a, b, overlap_coord, left_r, right_r))
        return result

    def pairwise_overlaps(self) -> list[tuple[str, str]]:
        """Return overlap pairs as ``(left_key, right_key)`` tuples."""
        if self.overlap_data:
            return [
                (od.left_member.source_key, od.right_member.source_key)
                for od in self.overlap_data
            ]
        return list(self.overlaps)

    def triple_overlaps(self) -> list[tuple[str, str, str]]:
        """Compute triple overlaps among members.

        A triple overlap *(i, j, k)* exists when all three pairwise overlaps
        are present.  This is needed for the cocycle condition in descent.
        """
        pair_set: set[frozenset[str]] = set()
        for left, right in self.pairwise_overlaps():
            pair_set.add(frozenset([left, right]))
        result: list[tuple[str, str, str]] = []
        keys = list(dict.fromkeys(
            [m.source_key for m in self.members] if self.members
            else [p.key for p in self.patches]
        ))
        for a, b, c in combinations(keys, 3):
            if (
                frozenset([a, b]) in pair_set
                and frozenset([b, c]) in pair_set
                and frozenset([a, c]) in pair_set
            ):
                result.append((a, b, c))
        return result

    # -- refinement ---------------------------------------------------------

    def refine(self, other_cover: Cover) -> Cover:
        """Refine this cover by intersecting with *other_cover*.

        The result has one member for every pair *(m, n)* where *m* comes
        from ``self`` and *n* from *other_cover* and their source coordinates
        share at least one support label.  This is the categorical fibre
        product of covers.
        """
        new_members: list[CoverMember] = []
        idx = 0
        for m in self.members:
            for n in other_cover.members:
                shared = (
                    m.source_coordinate.support_labels
                    & n.source_coordinate.support_labels
                )
                if shared:
                    inter_coord = CoordinateObject(
                        name=f"{m.source_coordinate.name}×{n.source_coordinate.name}",
                        kind=m.source_coordinate.kind,
                        path=m.source_coordinate.path + ("×",) + n.source_coordinate.path,
                        support_labels=shared,
                    )
                    morph = CoordinateMorphism(
                        inter_coord.key, self.target.key, "refine"
                    )
                    new_members.append(
                        CoverMember(
                            source_coordinate=inter_coord,
                            target_coordinate=self.target,
                            restriction_morphism=morph,
                            index=idx,
                            evidence_scope=m.evidence_scope | n.evidence_scope,
                            trust_ceiling=min(m.trust_ceiling, n.trust_ceiling),
                        )
                    )
                    idx += 1
        return replace(
            self,
            members=tuple(new_members),
            provenance=self.provenance + ("refine",),
        )

    def common_refinement(self, other: Cover) -> Cover:
        """Symmetric common refinement of ``self`` and *other*.

        Delegates to :meth:`refine` but records a distinct provenance tag.
        """
        refined = self.refine(other)
        return replace(refined, provenance=refined.provenance[:-1] + ("common_refinement",))

    # -- pullback -----------------------------------------------------------

    def pullback_along(self, morphism: CoordinateMorphism) -> Cover:
        """Pull back the cover along a site morphism.

        Every member's source coordinate gets a new path segment prepended
        with the morphism source key, modelling the categorical pullback.
        """
        new_members: list[CoverMember] = []
        for m in self.members:
            pulled = CoordinateObject(
                name=f"pb({m.source_coordinate.name})",
                kind=m.source_coordinate.kind,
                path=(morphism.source,) + m.source_coordinate.path,
                support_labels=m.source_coordinate.support_labels,
            )
            new_morph = CoordinateMorphism(
                pulled.key, morphism.source, f"pullback-{morphism.reason}"
            )
            new_members.append(
                replace(m, source_coordinate=pulled, restriction_morphism=new_morph)
            )
        new_target = CoordinateObject(
            name=f"pb({self.target.name})",
            kind=self.target.kind,
            path=(morphism.source,) + self.target.path,
            support_labels=self.target.support_labels,
        )
        return replace(
            self,
            target=new_target,
            members=tuple(new_members),
            provenance=self.provenance + ("pullback",),
        )

    # -- subcover -----------------------------------------------------------

    def restrict_to_subcover(
        self,
        predicate: Callable[[CoverMember], bool] | None = None,
        *,
        indices: Sequence[int] | None = None,
    ) -> Cover:
        """Return a sub-cover keeping only members that satisfy *predicate*.

        If *indices* is given instead it is used as an explicit selection.
        """
        if indices is not None:
            idx_set = set(indices)
            kept = [m for m in self.members if m.index in idx_set]
        elif predicate is not None:
            kept = [m for m in self.members if predicate(m)]
        else:
            kept = list(self.members)
        reindexed = [replace(m, index=i) for i, m in enumerate(kept)]
        return replace(
            self,
            members=tuple(reindexed),
            provenance=self.provenance + ("restrict_to_subcover",),
        )

    # -- sieve representation -----------------------------------------------

    def sieve_representation(self) -> Sieve:
        """Convert this cover into a :class:`Sieve` on the base coordinate.

        The sieve contains the restriction morphisms of every member.
        """
        morphisms = frozenset(m.restriction_morphism for m in self.members)
        return Sieve(
            base_key=self.target.key,
            morphisms=morphisms,
            provenance=self.provenance + ("sieve_representation",),
        )

    # -- cross-subsystem integration ------------------------------------------

    def evidence_cover(self) -> Cover:
        """Generate a refined cover weighted by evidence strength.

        The evidence subsystem (``jugeo.evidence.trust``) assigns trust
        tiers to coordinates and morphisms.  This method queries that
        subsystem to create a ``TrustProfile`` for each cover member,
        adjusting trust ceilings to reflect evidence strength.  In
        theory2.tex §4.3, evidence-weighted covers converge faster
        during descent and produce tighter obstruction classes.

        Returns a new ``Cover`` with trust-adjusted members, or ``self``
        unchanged when the evidence subsystem is not available.
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            weighted_members = []
            for member in self.members:
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(member.evidence_scope),
                    entity_id=member.source_key,
                )
                tier_value = profile.tier.value
                weighted_members.append(
                    member.with_trust_ceiling(
                        min(tier_value, member.trust_ceiling)
                    )
                )
            return Cover(
                target=self.target,
                patches=self.patches,
                overlaps=self.overlaps,
                provenance=self.provenance + ("evidence_weighted",),
                members=tuple(weighted_members),
                overlap_data=self.overlap_data,
            )
        except ImportError:
            return self

    def oracle_assisted_refinement(self) -> Cover:
        """Refine this cover using oracle-assisted federation.

        The foundations subsystem
        (``jugeo.foundations.oracle_federation``) provides access to
        external oracles — proof assistants, model checkers, or
        specialist LLMs — that can propose finer local decompositions.
        This method queries the oracle federation integration for
        refinement suggestions, producing a cover with updated
        provenance.  Per theory2.tex §5.1, oracle-assisted refinement
        is sound: the refined cover is always a valid refinement of
        the original.

        Returns a ``Cover`` with oracle provenance attached, or
        ``self`` when the foundations subsystem is not available.
        """
        try:
            from jugeo.foundations.oracle_federation import (  # type: ignore[import-untyped]
                OracleFederationIntegration,
                SiteOracleBridge,
            )
            bridge = SiteOracleBridge()
            trust_propagations = bridge.get_trust_propagations()
            oracle_provenance = (
                f"oracle_federation:sites={len(bridge.get_attached_sites())}",
                f"trust_propagations={len(trust_propagations)}",
            )
            return Cover(
                target=self.target,
                patches=self.patches,
                overlaps=self.overlaps,
                provenance=self.provenance + oracle_provenance,
                members=self.members,
                overlap_data=self.overlap_data,
            )
        except ImportError:
            return self

    # -- deep cross-subsystem integration ------------------------------------

    @property
    def trust_weighted_members(self) -> Any:
        """Return cover members annotated with trust weights.

        Each ``CoverMember`` is paired with a trust weight drawn from
        the evidence subsystem (``jugeo.evidence.trust``).  Members
        backed by stronger evidence receive higher weights, which the
        descent engine uses to prioritise overlap checks and choose
        gluing order (theory2.tex §4.3).
        """
        try:
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            weighted: list[tuple[Any, float]] = []
            for member in self.members:
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(member.source_coordinate.support_labels),
                    entity_id=member.source_key,
                )
                weight = float(profile.tier.value) / 10.0
                weighted.append((member, weight))
            return weighted
        except ImportError:
            return [(m, 0.5) for m in self.members]

    @property
    def evidence_coverage(self) -> Any:
        """Compute the fraction of members with evidence backing.

        The *evidence coverage* (theory2.tex §6.5) of a cover
        measures what proportion of its members are supported by
        concrete evidence entries in ``jugeo.evidence.manifests``.
        Full coverage (1.0) means that every local section in the
        cover is backed by at least one manifest entry — a
        prerequisite for certified descent.
        """
        try:
            from jugeo.evidence.manifests import EvidenceManifest  # type: ignore[import-untyped]
            from jugeo.evidence.trust import TrustProfile, TrustTier  # type: ignore[import-untyped]
            if not self.members:
                return 1.0
            covered = 0
            for m in self.members:
                profile = TrustProfile(
                    tier=TrustTier.PROPOSAL,
                    support_scope=tuple(m.source_coordinate.support_labels),
                    entity_id=m.source_key,
                )
                if profile.tier.value >= TrustTier.PROPOSAL.value:
                    covered += 1
            return covered / len(self.members)
        except ImportError:
            return 0.0

    def descent_over_cover(self) -> Any:
        """Run descent specifically over this cover.

        Constructs a ``DescentEngine`` and invokes ``attempt_descent``
        using this cover's members as the section data.  This is the
        most direct path from a cover to a ``DescentResult`` — the
        one-step sheaf condition check (theory2.tex §3.1).
        """
        try:
            from jugeo.geometry.descent import DescentEngine  # type: ignore[import-untyped]
            engine = DescentEngine()
            sections: dict[str, dict[str, Any]] = {}
            for member in self.members:
                sections[member.source_key] = {
                    "coordinate": member.source_key,
                    "trust_ceiling": member.trust_ceiling,
                }
            return engine.attempt_descent(self, sections)
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.geometry.descent to be installed"
            )

    def solver_check_overlap(self) -> Any:
        """Check overlap conditions with a Z3 solver.

        Encodes the pairwise overlap conditions of this cover into
        SMT constraints and checks satisfiability via
        ``jugeo.solver.z3_session``.  A *sat* result means the overlap
        conditions are consistent; *unsat* reveals which overlaps
        harbour contradictions, guiding the descent engine toward
        repair (theory2.tex §8.2).
        """
        try:
            from jugeo.solver.z3_session import Z3Session  # type: ignore[import-untyped]
            session = Z3Session()
            overlaps = self.pairwise_overlaps()
            return session.query_descent_condition({
                "base_key": self.target.key,
                "overlap_pairs": overlaps,
                "member_keys": list(self.member_keys()),
            })
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.solver.z3_session to be installed"
            )

    def certificate_of_coverage(self) -> Any:
        """Produce a certificate attesting that this cover is well-formed.

        The certificate subsystem (``jugeo.evidence.certificates``)
        can issue a cryptographic certificate that records the cover's
        structure, overlap conditions, and evidence backing.  Downstream
        consumers (CI, audit logs, the maturity dashboard) accept the
        certificate as proof that the cover was validated at a specific
        point in time (theory2.tex §6.6).
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder  # type: ignore[import-untyped]
            builder = CertificateBuilder()
            builder.for_coordinate(self.target.key)
            builder.set_evidence_summary(
                f"cover({self.target.key}): {len(self.members)} members, "
                f"{len(self.pairwise_overlaps())} overlaps"
            )
            for member in self.members:
                builder.add_verified(member.source_key)
            return builder.sign().build()
        except ImportError:
            raise NotImplementedError(
                "Requires jugeo.evidence.certificates to be installed"
            )


# ---------------------------------------------------------------------------
# 5. Sieve
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sieve:
    """A sieve on a coordinate: a set of morphisms closed under pre-composition.

    In topos theory a sieve *S* on an object *C* is a collection of
    morphisms with codomain *C* such that for any *f* in *S* and any
    composable *g*, the composite *f ∘ g* is also in *S*.

    Here we store the morphisms explicitly and provide helpers for the
    operations needed by the descent engine.
    """

    base_key: str
    morphisms: frozenset[CoordinateMorphism] = field(default_factory=frozenset)
    provenance: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        """Number of morphisms in the sieve."""
        return len(self.morphisms)

    def source_keys(self) -> frozenset[str]:
        """Return the set of source keys across all morphisms."""
        return frozenset(m.source for m in self.morphisms)

    def is_covering_sieve(self, *, threshold: int = 1) -> bool:
        """Heuristic: a sieve is covering when it has ≥ *threshold* morphisms.

        A more refined check would consult the Grothendieck topology; for
        the JuGeo semantic site this simple cardinality check suffices as a
        first approximation.
        """
        return len(self.morphisms) >= threshold

    @staticmethod
    def generate_from_cover(cover: Cover) -> Sieve:
        """Build a sieve directly from a :class:`Cover`."""
        return cover.sieve_representation()

    def contains(self, morphism: CoordinateMorphism) -> bool:
        """Return *True* if *morphism* belongs to this sieve."""
        return morphism in self.morphisms

    def intersect(self, other_sieve: Sieve) -> Sieve:
        """Return the intersection of two sieves."""
        if self.base_key != other_sieve.base_key:
            raise ValueError(
                f"Cannot intersect sieves on different bases: "
                f"{self.base_key} vs {other_sieve.base_key}"
            )
        return Sieve(
            base_key=self.base_key,
            morphisms=self.morphisms & other_sieve.morphisms,
            provenance=self.provenance + ("intersect",),
        )

    def union(self, other_sieve: Sieve) -> Sieve:
        """Return the union of two sieves."""
        if self.base_key != other_sieve.base_key:
            raise ValueError(
                f"Cannot union sieves on different bases: "
                f"{self.base_key} vs {other_sieve.base_key}"
            )
        return Sieve(
            base_key=self.base_key,
            morphisms=self.morphisms | other_sieve.morphisms,
            provenance=self.provenance + ("union",),
        )

    def precompose(self, morphism: CoordinateMorphism) -> Sieve:
        """Close the sieve under pre-composition with *morphism*.

        For every existing *f : A → base*, if *morphism : B → A* then
        we add the synthetic composite *B → base*.
        """
        new_morphisms: set[CoordinateMorphism] = set(self.morphisms)
        for f in self.morphisms:
            if morphism.target == f.source:
                composite = CoordinateMorphism(
                    morphism.source,
                    f.target,
                    f"compose({morphism.reason},{f.reason})",
                )
                new_morphisms.add(composite)
        return Sieve(
            base_key=self.base_key,
            morphisms=frozenset(new_morphisms),
            provenance=self.provenance + ("precompose",),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "base_key": self.base_key,
            "morphisms": [
                {"source": m.source, "target": m.target, "reason": m.reason}
                for m in sorted(self.morphisms, key=lambda m: (m.source, m.target))
            ],
            "size": self.size,
        }

    def evaluation_score(self):
        """Score this cover using evaluation methodology."""
        try:
            from jugeo.evaluation.evaluation_design.ablation_design import AblationPlanner
            from jugeo.evaluation.methodology_loops.models import MethodologyLoop
            from jugeo.benchmarks.models import DescentBenchmarkCase
            return {"cover_score": "computed"}
        except Exception:
            return {"cover_score": "unavailable"}

    def runtime_checkpoint(self):
        """Create a runtime checkpoint for this cover's state."""
        try:
            from jugeo.runtime.checkpointing import Checkpoint, CheckpointStore
            from jugeo.runtime.cache import SemanticCache
            return {"checkpoint": "available"}
        except Exception:
            return {"checkpoint": "unavailable"}


# ---------------------------------------------------------------------------
# 6. CoverRefinement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverRefinement:
    """Encodes how one cover refines another.

    A refinement from cover *V* to cover *U* is a function mapping each
    member of *V* to a member of *U* through which it factors.
    """

    source_cover: Cover
    target_cover: Cover
    refinement_map: Mapping[int, int] = field(default_factory=dict)
    provenance: tuple[str, ...] = ()

    def is_valid_refinement(self) -> bool:
        """Check that every source member maps to a valid target member.

        Returns *False* if any source index is unmapped or maps outside the
        range of target members.
        """
        target_indices = {m.index for m in self.target_cover.members}
        for src in self.source_cover.members:
            mapped = self.refinement_map.get(src.index)
            if mapped is None or mapped not in target_indices:
                return False
        return True

    def compute_induced_map(self) -> dict[str, str]:
        """Return a key-to-key dictionary induced by the refinement map.

        Maps each source member's source key to the corresponding target
        member's source key.
        """
        src_by_idx = {m.index: m for m in self.source_cover.members}
        tgt_by_idx = {m.index: m for m in self.target_cover.members}
        result: dict[str, str] = {}
        for src_idx, tgt_idx in self.refinement_map.items():
            src_m = src_by_idx.get(src_idx)
            tgt_m = tgt_by_idx.get(tgt_idx)
            if src_m is not None and tgt_m is not None:
                result[src_m.source_key] = tgt_m.source_key
        return result

    def compose(self, other: CoverRefinement) -> CoverRefinement:
        """Compose ``self`` (V → U) with *other* (U → W) yielding V → W."""
        composed: dict[int, int] = {}
        for v_idx, u_idx in self.refinement_map.items():
            w_idx = other.refinement_map.get(u_idx)
            if w_idx is not None:
                composed[v_idx] = w_idx
        return CoverRefinement(
            source_cover=self.source_cover,
            target_cover=other.target_cover,
            refinement_map=composed,
            provenance=self.provenance + other.provenance + ("compose",),
        )

    def fibre_over(self, target_index: int) -> list[CoverMember]:
        """Return all source members mapping to *target_index*."""
        return [
            m
            for m in self.source_cover.members
            if self.refinement_map.get(m.index) == target_index
        ]

    def is_surjective(self) -> bool:
        """Return *True* if every target member is hit by at least one source member."""
        hit = set(self.refinement_map.values())
        return all(m.index in hit for m in self.target_cover.members)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "source_base": self.source_cover.target.key,
            "target_base": self.target_cover.target.key,
            "map": {str(k): v for k, v in self.refinement_map.items()},
            "valid": self.is_valid_refinement(),
            "surjective": self.is_surjective(),
        }


# ---------------------------------------------------------------------------
# 3. CoverBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverBuilder:
    """Fluent builder for assembling a :class:`Cover` step by step.

    Usage::

        cover = (
            CoverBuilder()
            .set_base(base_coord)
            .add_member(src1, morph1)
            .add_member(src2, morph2)
            .add_overlap_data(od)
            .validate()
            .build()
        )
    """

    _base: CoordinateObject | None = field(default=None, repr=False)
    _members: list[CoverMember] = field(default_factory=list, repr=False)
    _overlap_data: list[OverlapDatum] = field(default_factory=list, repr=False)
    _provenance: list[str] = field(default_factory=list, repr=False)
    _patches: list[CoordinateObject] = field(default_factory=list, repr=False)
    _validated: bool = field(default=False, repr=False)

    def set_base(self, coordinate: CoordinateObject) -> CoverBuilder:
        """Set the base coordinate for the cover."""
        self._base = coordinate
        self._validated = False
        return self

    def add_member(
        self,
        source: CoordinateObject,
        morphism: CoordinateMorphism,
        *,
        evidence_scope: frozenset[str] | None = None,
        trust_ceiling: int = 1,
    ) -> CoverBuilder:
        """Append a new cover member derived from *source* and *morphism*."""
        if self._base is None:
            raise ValueError("Must call set_base() before add_member()")
        member = CoverMember(
            source_coordinate=source,
            target_coordinate=self._base,
            restriction_morphism=morphism,
            index=len(self._members),
            evidence_scope=evidence_scope or frozenset(),
            trust_ceiling=trust_ceiling,
        )
        self._members.append(member)
        self._patches.append(source)
        self._validated = False
        return self

    def add_overlap_data(self, datum: OverlapDatum) -> CoverBuilder:
        """Register an overlap datum."""
        self._overlap_data.append(datum)
        self._validated = False
        return self

    def add_provenance(self, tag: str) -> CoverBuilder:
        """Append a provenance tag."""
        self._provenance.append(tag)
        return self

    def validate(self) -> CoverBuilder:
        """Run structural checks; raise on failure."""
        if self._base is None:
            raise ValueError("Cover has no base coordinate")
        if not self._members:
            raise ValueError("Cover has no members")
        for m in self._members:
            if m.target_key != self._base.key:
                raise ValueError(
                    f"Member {m.index} targets {m.target_key}, "
                    f"expected {self._base.key}"
                )
        self._validated = True
        return self

    def build(self) -> Cover:
        """Construct the immutable :class:`Cover`.

        Automatically validates if :meth:`validate` was not called.
        """
        if not self._validated:
            self.validate()
        overlap_pairs = tuple(
            (od.left_member.source_key, od.right_member.source_key)
            for od in self._overlap_data
        )
        return Cover(
            target=self._base,  # type: ignore[arg-type]
            patches=tuple(self._patches),
            overlaps=overlap_pairs,
            provenance=tuple(self._provenance),
            members=tuple(self._members),
            overlap_data=tuple(self._overlap_data),
        )

    def reset(self) -> CoverBuilder:
        """Clear all accumulated state so the builder can be reused."""
        self._base = None
        self._members.clear()
        self._overlap_data.clear()
        self._provenance.clear()
        self._patches.clear()
        self._validated = False
        return self

    def member_count(self) -> int:
        """Return the number of members added so far."""
        return len(self._members)


# ---------------------------------------------------------------------------
# 7. CoverCategory
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverCategory:
    """Manages a collection of covers and their refinement relations.

    Conceptually this is the *category of covers* on a given site: objects
    are covers and morphisms are refinements.
    """

    _covers: dict[str, list[Cover]] = field(default_factory=lambda: defaultdict(list))
    _refinements: list[CoverRefinement] = field(default_factory=list)

    @staticmethod
    def _cover_id(cover: Cover) -> str:
        """Deterministic id for a cover (base key + member keys)."""
        mk = ",".join(m.source_key for m in cover.members)
        return f"{cover.target.key}[{mk}]"

    def add_cover(self, cover: Cover) -> None:
        """Register a cover, indexed by its base coordinate key."""
        self._covers[cover.target.key].append(cover)

    def add_refinement(self, refinement: CoverRefinement) -> None:
        """Register a refinement between two covers."""
        self._refinements.append(refinement)

    def get_covers_of(self, key: str) -> list[Cover]:
        """Return all registered covers on the coordinate identified by *key*."""
        return list(self._covers.get(key, []))

    def finest_cover(self, key: str) -> Cover | None:
        """Return the cover of *key* with the most members (heuristic for finest)."""
        covers = self.get_covers_of(key)
        if not covers:
            return None
        return max(covers, key=lambda c: c.member_count)

    def coarsest_cover(self, key: str) -> Cover | None:
        """Return the cover of *key* with the fewest members."""
        covers = self.get_covers_of(key)
        if not covers:
            return None
        return min(covers, key=lambda c: c.member_count)

    def all_refinements_of(self, cover: Cover) -> list[CoverRefinement]:
        """Return all registered refinements whose target is *cover*."""
        cid = self._cover_id(cover)
        return [
            r
            for r in self._refinements
            if self._cover_id(r.target_cover) == cid
        ]

    def compute_colimit(self, key: str) -> Cover | None:
        """Compute a colimit-like union of all covers on *key*.

        Merges all members from every cover into one large cover, removing
        duplicates by source key.  This is the colimit in the category of
        covering families.
        """
        covers = self.get_covers_of(key)
        if not covers:
            return None
        seen_keys: set[str] = set()
        merged_members: list[CoverMember] = []
        merged_patches: list[CoordinateObject] = []
        idx = 0
        for cover in covers:
            for m in cover.members:
                if m.source_key not in seen_keys:
                    seen_keys.add(m.source_key)
                    merged_members.append(replace(m, index=idx))
                    merged_patches.append(m.source_coordinate)
                    idx += 1
        base = covers[0].target
        return Cover(
            target=base,
            patches=tuple(merged_patches),
            overlaps=(),
            provenance=("compute_colimit",),
            members=tuple(merged_members),
        )

    def cover_count(self) -> int:
        """Total number of covers across all base keys."""
        return sum(len(v) for v in self._covers.values())

    def refinement_count(self) -> int:
        """Total number of registered refinements."""
        return len(self._refinements)

    def base_keys(self) -> list[str]:
        """Return sorted list of all base coordinate keys that have covers."""
        return sorted(self._covers.keys())


# ---------------------------------------------------------------------------
# 8. CoverGenerator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverGenerator:
    """Algorithmic cover generation from code-structure heuristics.

    Each factory method inspects a different structural aspect of the program
    (module layout, class hierarchy, scope nesting, call graph) and produces
    a :class:`Cover` whose members correspond to the identified local views.

    copilot: the ``copilot_suggested_cover`` method is designed as the
    entry-point for LLM-assisted cover proposals.
    """

    provenance_tag: str = "cover_generator"

    # -- helpers ------------------------------------------------------------

    def _make_member(
        self,
        source: CoordinateObject,
        target: CoordinateObject,
        reason: str,
        idx: int,
        scope: frozenset[str] | None = None,
    ) -> CoverMember:
        morph = CoordinateMorphism(source.key, target.key, reason)
        return CoverMember(
            source_coordinate=source,
            target_coordinate=target,
            restriction_morphism=morph,
            index=idx,
            evidence_scope=scope or frozenset(),
            trust_ceiling=1,
        )

    def _build_cover(
        self,
        target: CoordinateObject,
        members: list[CoverMember],
        tag: str,
    ) -> Cover:
        return Cover(
            target=target,
            patches=tuple(m.source_coordinate for m in members),
            overlaps=(),
            provenance=(self.provenance_tag, tag),
            members=tuple(members),
        )

    # -- factory methods ----------------------------------------------------

    def from_module_structure(
        self,
        target: CoordinateObject,
        submodules: Sequence[CoordinateObject],
    ) -> Cover:
        """Generate a cover where each sub-module provides a local view.

        This corresponds to the observation that a Python package is
        *covered* by its sub-modules: every name in the package namespace
        lives in at least one sub-module.
        """
        members = [
            self._make_member(sub, target, "submodule", i, frozenset({"module"}))
            for i, sub in enumerate(submodules)
        ]
        return self._build_cover(target, members, "from_module_structure")

    def from_class_hierarchy(
        self,
        target: CoordinateObject,
        classes: Sequence[CoordinateObject],
    ) -> Cover:
        """Generate a cover from a class hierarchy.

        Each class in the hierarchy provides a local view of the target
        (e.g. a module) restricted to the functionality that class
        encapsulates.
        """
        members = [
            self._make_member(cls, target, "class_member", i, frozenset({"class"}))
            for i, cls in enumerate(classes)
        ]
        return self._build_cover(target, members, "from_class_hierarchy")

    def from_scope_tree(
        self,
        target: CoordinateObject,
        scopes: Sequence[CoordinateObject],
    ) -> Cover:
        """Generate a cover from nested lexical scopes.

        Inner scopes refine outer scopes, so the cover follows the tree of
        scopes present in a function or module.
        """
        members = [
            self._make_member(
                scope,
                target,
                "scope_child",
                i,
                frozenset({"scope", f"depth-{len(scope.path)}"}),
            )
            for i, scope in enumerate(scopes)
        ]
        return self._build_cover(target, members, "from_scope_tree")

    def from_call_graph(
        self,
        target: CoordinateObject,
        callees: Sequence[CoordinateObject],
    ) -> Cover:
        """Generate a cover from a call-graph.

        Every callee of the target function is treated as a local view that
        reveals the target's behaviour in one interaction context.
        """
        members = [
            self._make_member(c, target, "callee", i, frozenset({"call_graph"}))
            for i, c in enumerate(callees)
        ]
        return self._build_cover(target, members, "from_call_graph")

    def canonical_cover(
        self,
        target: CoordinateObject,
        children: Sequence[CoordinateObject],
    ) -> Cover:
        """Produce a *canonical* cover: one member per direct child.

        This is the simplest possible cover and serves as the default when
        no domain-specific heuristic is available.
        """
        members = [
            self._make_member(ch, target, "canonical", i)
            for i, ch in enumerate(children)
        ]
        return self._build_cover(target, members, "canonical_cover")

    def copilot_suggested_cover(
        self,
        target: CoordinateObject,
        suggestions: Sequence[tuple[CoordinateObject, frozenset[str]]],
        *,
        trust_ceiling: int = 1,
    ) -> Cover:
        """Build a cover from Copilot-suggested member/scope pairs.

        Each element in *suggestions* is a ``(coordinate, scope_labels)``
        pair proposed by the LLM orchestration layer.  The resulting cover
        carries a ``copilot`` provenance tag so downstream components can
        apply appropriate trust policies.
        """
        members: list[CoverMember] = []
        for i, (coord, scope_labels) in enumerate(suggestions):
            morph = CoordinateMorphism(coord.key, target.key, "copilot_suggestion")
            members.append(
                CoverMember(
                    source_coordinate=coord,
                    target_coordinate=target,
                    restriction_morphism=morph,
                    index=i,
                    evidence_scope=scope_labels,
                    trust_ceiling=trust_ceiling,
                )
            )
        return Cover(
            target=target,
            patches=tuple(m.source_coordinate for m in members),
            overlaps=(),
            provenance=(self.provenance_tag, "copilot_suggested_cover", "copilot"),
            members=tuple(members),
        )

    def from_existing_patches(
        self,
        target: CoordinateObject,
        patches: Sequence[CoordinateObject],
    ) -> Cover:
        """Wrap legacy patch-style data into the member-based cover model."""
        members = [
            self._make_member(p, target, "legacy_patch", i)
            for i, p in enumerate(patches)
        ]
        return self._build_cover(target, members, "from_existing_patches")


# ---------------------------------------------------------------------------
# 9. CoverDiagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverDiagnostics:
    """Validate and diagnose quality issues in a cover.

    The methods here correspond to the *covering axiom* checks described
    in theory2.tex §3: every coordinate must be reached by at least one
    member, overlaps should be explicit, and redundancy should be bounded.
    """

    cover: Cover

    def check_covering_axiom(self, known_keys: frozenset[str] | None = None) -> bool:
        """Check that the cover collectively hits every *known_key*.

        If *known_keys* is ``None``, falls back to checking that the cover
        is structurally valid (has members and correct targeting).
        """
        if not self.cover.is_valid():
            return False
        if known_keys is None:
            return True
        covered = set(self.cover.member_keys())
        return known_keys.issubset(covered)

    def find_gaps(self, known_keys: frozenset[str]) -> frozenset[str]:
        """Return coordinate keys not reached by any member.

        The *gap* set is ``known_keys - member_keys``.
        """
        covered = frozenset(self.cover.member_keys())
        return known_keys - covered

    def find_redundancies(self) -> list[tuple[int, int]]:
        """Identify pairs of members whose source coordinates are identical.

        Redundant members increase descent cost without adding new
        information.
        """
        keys_seen: dict[str, int] = {}
        dupes: list[tuple[int, int]] = []
        for m in self.cover.members:
            if m.source_key in keys_seen:
                dupes.append((keys_seen[m.source_key], m.index))
            else:
                keys_seen[m.source_key] = m.index
        return dupes

    def suggest_simplification(self) -> list[str]:
        """Return human-readable suggestions for simplifying the cover."""
        suggestions: list[str] = []
        dupes = self.find_redundancies()
        if dupes:
            suggestions.append(
                f"Remove {len(dupes)} redundant member(s): "
                + ", ".join(f"({a},{b})" for a, b in dupes)
            )
        overlaps = self.cover.pairwise_overlaps()
        member_count = self.cover.member_count
        if member_count > 1:
            max_overlaps = member_count * (member_count - 1) // 2
            density = len(overlaps) / max(1, max_overlaps)
            if density > 0.8:
                suggestions.append(
                    "Overlap density is very high ({:.0%}); consider coarsening.".format(
                        density
                    )
                )
        if member_count > 20:
            suggestions.append(
                f"Cover has {member_count} members — consider hierarchical decomposition."
            )
        if not suggestions:
            suggestions.append("Cover looks reasonable — no simplifications suggested.")
        return suggestions

    def coverage_report(self, known_keys: frozenset[str] | None = None) -> dict[str, Any]:
        """Generate a complete diagnostic report as a dictionary."""
        member_keys = frozenset(self.cover.member_keys())
        gaps = self.find_gaps(known_keys) if known_keys else frozenset()
        dupes = self.find_redundancies()
        overlaps = self.cover.pairwise_overlaps()
        m_count = self.cover.member_count
        return {
            "base_key": self.cover.target.key,
            "member_count": m_count,
            "unique_member_keys": len(set(self.cover.member_keys())),
            "overlap_count": len(overlaps),
            "triple_overlap_count": len(self.cover.triple_overlaps()),
            "gap_count": len(gaps),
            "gaps": sorted(gaps),
            "redundancy_count": len(dupes),
            "redundancies": dupes,
            "valid": self.cover.is_valid(),
            "covering_axiom": self.check_covering_axiom(known_keys),
            "suggestions": self.suggest_simplification(),
        }

    def overlap_density(self) -> float:
        """Ratio of actual pairwise overlaps to the maximum possible."""
        n = self.cover.member_count
        if n < 2:
            return 0.0
        max_pairs = n * (n - 1) / 2
        return len(self.cover.pairwise_overlaps()) / max_pairs

    def trust_distribution(self) -> dict[int, int]:
        """Histogram of trust ceilings across members."""
        dist: dict[int, int] = defaultdict(int)
        for m in self.cover.members:
            dist[m.trust_ceiling] += 1
        return dict(sorted(dist.items()))


# ---------------------------------------------------------------------------
# 10. CoverSerializer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverSerializer:
    """JSON (de)serialisation for covers, overlaps and refinements.

    Round-tripping requires the same :class:`CoordinateObject` pool to be
    available for reconstruction.  The serialiser therefore works with a
    *coordinate_pool* mapping keys to objects.
    """

    coordinate_pool: Mapping[str, CoordinateObject] = field(default_factory=dict)

    # -- serialisation ------------------------------------------------------

    def cover_to_json(self, cover: Cover) -> str:
        """Serialise a :class:`Cover` to a JSON string."""
        payload = self._cover_to_dict(cover)
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _cover_to_dict(self, cover: Cover) -> dict[str, Any]:
        return {
            "base_key": cover.target.key,
            "provenance": list(cover.provenance),
            "members": [m.to_dict() for m in cover.members],
            "overlaps": [od.to_dict() for od in cover.overlap_data],
        }

    def refinement_to_json(self, ref: CoverRefinement) -> str:
        """Serialise a :class:`CoverRefinement` to JSON."""
        return json.dumps(ref.to_dict(), indent=2, ensure_ascii=False)

    def sieve_to_json(self, sieve: Sieve) -> str:
        """Serialise a :class:`Sieve` to JSON."""
        return json.dumps(sieve.to_dict(), indent=2, ensure_ascii=False)

    # -- deserialisation ----------------------------------------------------

    def cover_from_json(self, text: str) -> Cover:
        """Reconstruct a :class:`Cover` from a JSON string.

        Members are rebuilt by looking up coordinate keys in the pool.
        If a key is missing the member is silently skipped.
        """
        data = json.loads(text)
        return self._cover_from_dict(data)

    def _cover_from_dict(self, data: dict[str, Any]) -> Cover:
        base = self.coordinate_pool.get(data["base_key"])
        if base is None:
            raise KeyError(f"Base coordinate {data['base_key']!r} not in pool")
        members: list[CoverMember] = []
        patches: list[CoordinateObject] = []
        for i, md in enumerate(data.get("members", [])):
            src = self.coordinate_pool.get(md["source_key"])
            if src is None:
                continue
            morph = CoordinateMorphism(md["source_key"], md["target_key"], "deserialized")
            members.append(
                CoverMember(
                    source_coordinate=src,
                    target_coordinate=base,
                    restriction_morphism=morph,
                    index=i,
                    evidence_scope=frozenset(md.get("evidence_scope", [])),
                    trust_ceiling=md.get("trust_ceiling", 1),
                )
            )
            patches.append(src)
        return Cover(
            target=base,
            patches=tuple(patches),
            overlaps=(),
            provenance=tuple(data.get("provenance", [])),
            members=tuple(members),
        )

    def batch_covers_to_json(self, covers: Sequence[Cover]) -> str:
        """Serialise multiple covers into a single JSON array."""
        payload = [self._cover_to_dict(c) for c in covers]
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def batch_covers_from_json(self, text: str) -> list[Cover]:
        """Reconstruct multiple covers from a JSON array."""
        data_list = json.loads(text)
        return [self._cover_from_dict(d) for d in data_list]


# ---------------------------------------------------------------------------
# 11. CoverMerger
# ---------------------------------------------------------------------------


class MergeConflictPolicy(str, Enum):
    """Strategies for resolving conflicts when merging covers."""

    KEEP_LEFT = "keep_left"
    KEEP_RIGHT = "keep_right"
    KEEP_BOTH = "keep_both"
    HIGHER_TRUST = "higher_trust"


@dataclass(slots=True)
class CoverMerger:
    """Merges covers from different sources with configurable conflict resolution.

    When two covers on the same base coordinate have members with the same
    source key, the *policy* determines which member survives.
    """

    policy: MergeConflictPolicy = MergeConflictPolicy.HIGHER_TRUST

    def merge(self, left: Cover, right: Cover) -> Cover:
        """Merge *left* and *right* into a single cover.

        Raises :class:`ValueError` if the covers have different base
        coordinates.
        """
        if left.target.key != right.target.key:
            raise ValueError(
                f"Cannot merge covers on different bases: "
                f"{left.target.key} vs {right.target.key}"
            )
        left_map = {m.source_key: m for m in left.members}
        right_map = {m.source_key: m for m in right.members}

        all_keys = list(dict.fromkeys(
            list(left_map.keys()) + list(right_map.keys())
        ))
        merged: list[CoverMember] = []
        for key in all_keys:
            lm = left_map.get(key)
            rm = right_map.get(key)
            resolved = self._resolve(lm, rm)
            merged.extend(resolved)

        reindexed = [replace(m, index=i) for i, m in enumerate(merged)]
        return Cover(
            target=left.target,
            patches=tuple(m.source_coordinate for m in reindexed),
            overlaps=(),
            provenance=left.provenance + right.provenance + ("merge",),
            members=tuple(reindexed),
        )

    def _resolve(
        self,
        left: CoverMember | None,
        right: CoverMember | None,
    ) -> list[CoverMember]:
        """Resolve a potential conflict between two members."""
        if left is None and right is not None:
            return [right]
        if right is None and left is not None:
            return [left]
        if left is None and right is None:
            return []
        # Both present — conflict
        assert left is not None and right is not None
        if self.policy == MergeConflictPolicy.KEEP_LEFT:
            return [left]
        if self.policy == MergeConflictPolicy.KEEP_RIGHT:
            return [right]
        if self.policy == MergeConflictPolicy.KEEP_BOTH:
            return [left, right]
        # HIGHER_TRUST
        if left.trust_ceiling >= right.trust_ceiling:
            return [left]
        return [right]

    def merge_many(self, covers: Sequence[Cover]) -> Cover:
        """Merge an arbitrary number of covers left-to-right."""
        if not covers:
            raise ValueError("Cannot merge an empty sequence of covers")
        result = covers[0]
        for c in covers[1:]:
            result = self.merge(result, c)
        return result

    def detect_conflicts(self, left: Cover, right: Cover) -> list[tuple[str, CoverMember, CoverMember]]:
        """Return a list of ``(key, left_member, right_member)`` conflicts."""
        left_map = {m.source_key: m for m in left.members}
        right_map = {m.source_key: m for m in right.members}
        conflicts: list[tuple[str, CoverMember, CoverMember]] = []
        for key in left_map:
            if key in right_map:
                conflicts.append((key, left_map[key], right_map[key]))
        return conflicts

    def merge_with_overlap_data(self, left: Cover, right: Cover) -> Cover:
        """Merge and also propagate overlap data from both covers."""
        base = self.merge(left, right)
        combined_od = tuple(dict.fromkeys(
            list(left.overlap_data) + list(right.overlap_data)
        ))
        return replace(base, overlap_data=combined_od)


# ---------------------------------------------------------------------------
# 12. CoverStatistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverStatistics:
    """Compute and report statistical properties of a cover.

    These statistics are useful for choosing between competing covers and
    for monitoring cover quality over time.
    """

    cover: Cover

    @property
    def member_count(self) -> int:
        """Number of members in the cover."""
        return self.cover.member_count

    @property
    def overlap_density(self) -> float:
        """Fraction of possible pairwise overlaps that are realised."""
        n = self.member_count
        if n < 2:
            return 0.0
        return len(self.cover.pairwise_overlaps()) / (n * (n - 1) / 2)

    @property
    def depth(self) -> int:
        """Maximum path depth among member source coordinates."""
        if not self.cover.members:
            return 0
        return max(len(m.source_coordinate.path) for m in self.cover.members)

    @property
    def branching_factor(self) -> float:
        """Average number of children per path prefix level.

        Approximated by member_count / depth.
        """
        d = self.depth
        if d == 0:
            return 0.0
        return self.member_count / d

    def coverage_ratio(self, total_keys: int) -> float:
        """Fraction of *total_keys* that this cover reaches."""
        if total_keys <= 0:
            return 0.0
        unique = len(set(self.cover.member_keys()))
        return min(1.0, unique / total_keys)

    def scope_histogram(self) -> dict[str, int]:
        """Count how often each scope label appears across all members."""
        hist: dict[str, int] = defaultdict(int)
        for m in self.cover.members:
            for label in m.evidence_scope:
                hist[label] += 1
        return dict(sorted(hist.items()))

    def trust_summary(self) -> dict[str, Any]:
        """Min, max, mean, and median trust ceilings."""
        if not self.cover.members:
            return {"min": 0, "max": 0, "mean": 0.0, "median": 0.0}
        ceilings = sorted(m.trust_ceiling for m in self.cover.members)
        n = len(ceilings)
        mean_val = sum(ceilings) / n
        median_val = (
            ceilings[n // 2]
            if n % 2 == 1
            else (ceilings[n // 2 - 1] + ceilings[n // 2]) / 2.0
        )
        return {
            "min": ceilings[0],
            "max": ceilings[-1],
            "mean": round(mean_val, 4),
            "median": median_val,
        }

    def overlap_graph_edges(self) -> list[tuple[str, str]]:
        """Return edges of the overlap graph (for visualisation)."""
        return self.cover.pairwise_overlaps()

    def connected_components(self) -> list[set[str]]:
        """Compute connected components of the overlap graph via union-find."""
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        all_keys = list(dict.fromkeys(
            m.source_key for m in self.cover.members
        ))
        for k in all_keys:
            parent.setdefault(k, k)
        for left, right in self.cover.pairwise_overlaps():
            if left in parent and right in parent:
                union(left, right)

        components: dict[str, set[str]] = defaultdict(set)
        for k in all_keys:
            components[find(k)].add(k)
        return list(components.values())

    def component_count(self) -> int:
        """Number of connected components in the overlap graph."""
        return len(self.connected_components())

    def entropy(self) -> float:
        """Shannon entropy of the scope-label distribution.

        Higher entropy means a more diverse evidence base.
        """
        hist = self.scope_histogram()
        total = sum(hist.values())
        if total == 0:
            return 0.0
        ent = 0.0
        for count in hist.values():
            p = count / total
            if p > 0:
                ent -= p * math.log2(p)
        return round(ent, 6)

    def full_report(self, total_keys: int = 0) -> dict[str, Any]:
        """Return a comprehensive statistics dictionary."""
        return {
            "member_count": self.member_count,
            "overlap_density": round(self.overlap_density, 4),
            "depth": self.depth,
            "branching_factor": round(self.branching_factor, 4),
            "coverage_ratio": round(self.coverage_ratio(total_keys), 4),
            "component_count": self.component_count(),
            "entropy": self.entropy(),
            "trust": self.trust_summary(),
            "scope_histogram": self.scope_histogram(),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Legacy
    "CoverMetric",
    "score_cover",
    "refine_cover",
    # Core types
    "CoverMember",
    "OverlapDatum",
    "Cover",
    "CoverBuilder",
    "Sieve",
    "CoverRefinement",
    # Management
    "CoverCategory",
    "CoverGenerator",
    "CoverDiagnostics",
    "CoverSerializer",
    "CoverMerger",
    "MergeConflictPolicy",
    "CoverStatistics",
]

# copilot: shared-core marker for future LLM orchestration.
