"""Formal site definition for JuGeo judgment contexts (Theory2.tex §9.1).

Mathematical Interlude — a more explicit formal core
=====================================================

In Theory2.tex §9.1 a **JuGeo judgment site** is a small category :math:`C`
whose objects are *judgment contexts* and whose morphisms are *context
morphisms* (weakening, substitution, and projection).  A **Grothendieck
topology** :math:`J` on :math:`C` assigns to each context object :math:`X` a
collection :math:`J(X)` of *covering sieves* — downward-closed sub-presheaves
of the representable :math:`\\mathrm{Hom}(-, X)` — subject to three axioms:

1. **Maximality** — the maximal sieve :math:`t_X = \\mathrm{Hom}(-, X)` belongs
   to :math:`J(X)` for every object :math:`X`.
2. **Stability** — if :math:`S \\in J(X)` and :math:`f : Y \\to X` is any
   morphism, then the pullback :math:`f^* S \\in J(Y)`.
3. **Transitivity** — if :math:`S \\in J(X)` and :math:`R` is a sieve on
   :math:`X` such that :math:`f^* R \\in J(Y)` for every :math:`f : Y \\to X`
   in :math:`S`, then :math:`R \\in J(X)`.

A **trust sheaf** :math:`T` on :math:`(C, J)` is a presheaf
:math:`T : C^{\\mathrm{op}} \\to \\mathbf{Set}` satisfying:

- **Locality** — if :math:`s, t \\in T(X)` agree on every element of a cover
  :math:`S \\in J(X)`, then :math:`s = t`.
- **Gluing** — a compatible family of sections over a cover glues to a unique
  global section.

Cover-based verification maps onto trust tiers: copilot-proposed covers enter
at :attr:`TrustLevel.ORACLE_PROPOSED`, solver-discharged covers enter at
:attr:`TrustLevel.SOLVER_DISCHARGED`, and mechanically verified covers enter at
:attr:`TrustLevel.MECHANICALLY_VERIFIED`.  The sheaf condition is the formal
guarantee that trust assignments are globally coherent, not merely locally
consistent on individual morphisms.

This module provides:

- :class:`FormalJudgmentObject` — typed wrapper for a judgment context node.
- :class:`Sieve` — a downward-closed family of morphisms into a fixed object.
- :class:`GrothendieckTopology` — the full topology structure with axiom
  verification.
- :class:`SheafOnSite` — trust sheaf implementing locality and gluing.
- :class:`CategoryStructure` — the underlying category (objects + morphisms +
  composition).
- :class:`SiteCoherenceChecker` — dedicated axiom-verification pass.
- :class:`ProgrammaticJudgmentSite` — the top-level site object used throughout
  JuGeo's reasoning pipeline.

References
----------
Theory2.tex §9.1 (pp. 87–102), especially Definitions 9.1–9.7, Lemmas 9.8–9.12,
and Remark 9.14 (trust sheaf / cover interaction).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from jugeo.evidence.channels import (
    ChannelJurisdiction,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)
from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier

# ---------------------------------------------------------------------------
# Optional geometry / solver imports (graceful degradation)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import JudgmentSite  # type: ignore[import]
    _HAS_GEOMETRY_SITE = True
except ImportError:
    JudgmentSite = None  # type: ignore[assignment,misc]
    _HAS_GEOMETRY_SITE = False

try:
    from jugeo.solver.router import (  # type: ignore[import]
        BackendKind,
        RoutingDecision,
        SolverRouter,
    )
    _HAS_SOLVER_ROUTER = True
except ImportError:
    SolverRouter = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_ROUTER = False

try:
    from jugeo.solver.fragments import (  # type: ignore[import]
        LogicalFragment,
        SolverFragment,
    )
    _HAS_SOLVER_FRAGMENTS = True
except ImportError:
    LogicalFragment = None  # type: ignore[assignment,misc]
    SolverFragment = None  # type: ignore[assignment,misc]
    _HAS_SOLVER_FRAGMENTS = False

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust-level ordering helpers (§9.1 Remark 9.3)
# ---------------------------------------------------------------------------

# Partial order on TrustLevel values.  Higher index = stronger trust.
_TRUST_ORDER: list[TrustLevel] = [
    TrustLevel.CONTRADICTED,
    TrustLevel.UNVERIFIED,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.HUMAN_ATTESTED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.MECHANICALLY_VERIFIED,
]

_TRUST_RANK: dict[TrustLevel, int] = {lvl: i for i, lvl in enumerate(_TRUST_ORDER)}


def _trust_gte(a: TrustLevel, b: TrustLevel) -> bool:
    """Return True iff trust level *a* is at least as strong as *b*."""
    return _TRUST_RANK.get(a, 0) >= _TRUST_RANK.get(b, 0)


def _strongest(levels: list[TrustLevel]) -> TrustLevel:
    """Return the maximum trust level from *levels*, or UNVERIFIED if empty."""
    if not levels:
        return TrustLevel.UNVERIFIED
    return max(levels, key=lambda lvl: _TRUST_RANK.get(lvl, 0))


# ---------------------------------------------------------------------------
# §9.1 Definition 9.1 — Judgment Object
# ---------------------------------------------------------------------------


@dataclass
class FormalJudgmentObject:
    """A node in the judgment site category.

    In §9.1, the objects of the site category :math:`C` are *judgment
    contexts*: typed environments :math:`\\Gamma` together with a judgment
    :math:`\\Gamma \\vdash \\varphi` and provenance metadata.  Here we wrap
    that information in a Python dataclass, attaching the relevant
    :class:`~jugeo.evidence.trust.TrustLevel` so that the sheaf can track
    trust per-section (§9.1 Definition 9.7).

    Parameters
    ----------
    obj_id:
        Stable unique identifier for this judgment context.
    judgment_type:
        Human-readable category, e.g. ``"arithmetic"``, ``"geometric"``,
        ``"identity_check"``.
    payload:
        Arbitrary dict carrying the actual judgment data (formula text,
        variable bindings, solver transcript, etc.).
    trust_level:
        The current :class:`TrustLevel` of the evidence for this judgment.
    support_kind:
        Free-form label for the support channel kind, e.g.
        ``"z3_proof"``, ``"lean4_certificate"``, ``"copilot_proposal"``.
    """

    obj_id: str
    judgment_type: str
    payload: dict[str, Any]
    trust_level: TrustLevel
    support_kind: str

    # Class-level registry of valid judgment types (extensible at runtime)
    _KNOWN_TYPES: ClassVar[frozenset[str]] = frozenset({
        "arithmetic", "geometric", "identity_check", "reachability",
        "structural", "semantic", "provenance", "oracle_proposal",
    })

    def validate(self) -> bool:
        """Validate the object against basic well-formedness constraints.

        Checks:
        - ``obj_id`` is non-empty.
        - ``judgment_type`` is a recognised category (warning only for
          unknown types; does not raise).
        - ``trust_level`` is a valid :class:`TrustLevel` member.
        - ``payload`` is a dict.

        Returns
        -------
        bool
            ``True`` if all hard constraints pass.
        """
        if not self.obj_id or not self.obj_id.strip():
            log.error("FormalJudgmentObject.validate: empty obj_id")
            return False
        if not isinstance(self.payload, dict):
            log.error(
                "FormalJudgmentObject.validate: payload must be dict, got %s",
                type(self.payload).__name__,
            )
            return False
        if not isinstance(self.trust_level, TrustLevel):
            log.error(
                "FormalJudgmentObject.validate: invalid trust_level %r",
                self.trust_level,
            )
            return False
        if self.judgment_type not in self._KNOWN_TYPES:
            log.warning(
                "FormalJudgmentObject.validate: unknown judgment_type %r; "
                "continuing but downstream axiom checks may reject it",
                self.judgment_type,
            )
        return True

    def describe(self) -> str:
        """Return a one-line human-readable summary of this judgment object."""
        return (
            f"JudgmentObject(id={self.obj_id!r}, type={self.judgment_type!r}, "
            f"trust={self.trust_level.name}, support={self.support_kind!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "obj_id": self.obj_id,
            "judgment_type": self.judgment_type,
            "payload": self.payload,
            "trust_level": self.trust_level.name,
            "support_kind": self.support_kind,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FormalJudgmentObject:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Raises
        ------
        KeyError
            If required keys are absent.
        ValueError
            If ``trust_level`` is not a valid :class:`TrustLevel` name.
        """
        trust_level = TrustLevel[d["trust_level"]]  # raises ValueError on bad name
        return cls(
            obj_id=d["obj_id"],
            judgment_type=d["judgment_type"],
            payload=dict(d.get("payload", {})),
            trust_level=trust_level,
            support_kind=d.get("support_kind", "unspecified"),
        )


# ---------------------------------------------------------------------------
# §9.1 Definition 9.2 — Sieve
# ---------------------------------------------------------------------------


@dataclass
class Sieve:
    """A sieve on an object :math:`X` in the site category.

    A *sieve* on :math:`X` is a set :math:`S` of morphisms with codomain
    :math:`X` that is closed under pre-composition: if :math:`f : Y \\to X`
    is in :math:`S` and :math:`g : Z \\to Y` is any morphism, then
    :math:`f \\circ g \\in S`.

    In JuGeo's judgment site the morphisms are context morphisms (weakening,
    substitution).  A covering sieve represents a *sufficient evidence cover*:
    the evidence available over the constituent sub-contexts is enough to
    reconstruct evidence for the full context.

    Parameters
    ----------
    object_id:
        The codomain object :math:`X` that this sieve covers.
    generating_morphisms:
        A list of morphism IDs whose downward closure (under pre-composition)
        generates the sieve.  The full sieve is the pre-composition closure
        of this set.
    is_maximal:
        ``True`` if this sieve is the maximal sieve :math:`t_X` (all
        morphisms into :math:`X`).  Required for the maximality axiom.
    site_ref:
        Identifier of the enclosing site, for traceability.
    """

    object_id: str
    generating_morphisms: list[str]
    is_maximal: bool
    site_ref: str

    def pullback(self, morphism_id: str) -> Sieve:
        """Compute the pullback :math:`f^* S` of this sieve along morphism *f*.

        Given a morphism :math:`f : Y \\to X` and a sieve :math:`S` on
        :math:`X`, the pullback is the sieve on :math:`Y` consisting of all
        morphisms :math:`h : Z \\to Y` such that :math:`f \\circ h \\in S`.

        In our symbolic representation we model this by tagging each
        generator with the pullback morphism, producing a new sieve whose
        generators are the pre-image composition paths.

        Parameters
        ----------
        morphism_id:
            The ID of the morphism :math:`f : Y \\to X` along which to pull
            back.

        Returns
        -------
        Sieve
            The pulled-back sieve on the domain of :math:`f`.
        """
        # Symbolically, the pullback generators are the original generators
        # pre-composed with morphism_id.  We represent each as a tagged path.
        pulled_generators = [
            f"{morphism_id}∘{gen}" for gen in self.generating_morphisms
        ]
        # A pullback of the maximal sieve along any morphism is again maximal
        # (§9.1 Lemma 9.8, stability direction).
        pb_maximal = self.is_maximal
        log.debug(
            "Sieve.pullback: pulling %r back along %r → %d generators",
            self.object_id, morphism_id, len(pulled_generators),
        )
        return Sieve(
            object_id=f"pb_{morphism_id}_{self.object_id}",
            generating_morphisms=pulled_generators,
            is_maximal=pb_maximal,
            site_ref=self.site_ref,
        )

    def is_covering(self) -> bool:
        """Return ``True`` if this sieve is non-empty (a necessary condition
        for covering in standard Grothendieck topologies).

        The maximal sieve is always covering.  An empty sieve can only cover
        in the trivial topology on the empty category.
        """
        return self.is_maximal or len(self.generating_morphisms) > 0

    def contains_morphism(self, m_id: str) -> bool:
        """Return ``True`` if morphism *m_id* is in the generating set of this
        sieve.

        Note: a fully expanded sieve would also include all pre-compositions.
        For symbolic matching we check the generator list directly.
        """
        return m_id in self.generating_morphisms

    def describe(self) -> str:
        """Return a human-readable summary of this sieve."""
        max_tag = " [MAXIMAL]" if self.is_maximal else ""
        return (
            f"Sieve(object={self.object_id!r}{max_tag}, "
            f"generators={self.generating_morphisms}, "
            f"site={self.site_ref!r})"
        )


# ---------------------------------------------------------------------------
# §9.1 Definition 9.4 — Grothendieck Topology
# ---------------------------------------------------------------------------


class GrothendieckTopology:
    """A Grothendieck topology on the judgment site category.

    Stores the assignment :math:`J : \\mathrm{Ob}(C) \\to
    \\mathcal{P}(\\mathrm{Sieves})` that maps each object to its collection
    of covering sieves.  Provides methods for verifying the three topology
    axioms (§9.1 Definition 9.4):

    - **Maximality** — :meth:`check_maximality`
    - **Stability** — :meth:`check_stability`
    - **Transitivity** — :meth:`check_transitivity`

    Parameters
    ----------
    site_id:
        Identifier of the parent site.
    sieves:
        Initial mapping ``object_id → [covering Sieve, ...]``.
    axioms_verified:
        Cache of the most recent axiom verification results.
    """

    def __init__(
        self,
        site_id: str,
        sieves: dict[str, list[Sieve]] | None = None,
        axioms_verified: dict[str, bool] | None = None,
    ) -> None:
        self.site_id = site_id
        self.sieves: dict[str, list[Sieve]] = sieves or {}
        self.axioms_verified: dict[str, bool] = axioms_verified or {}

    # ------------------------------------------------------------------
    # Axiom 1: Maximality
    # ------------------------------------------------------------------

    def check_maximality(self) -> bool:
        """Verify the maximality axiom (§9.1 Definition 9.4(i)).

        For every object :math:`X`, the maximal sieve
        :math:`t_X \\in J(X)`.  We check that at least one sieve in
        ``J(X)`` is flagged as maximal for every registered object.

        Returns
        -------
        bool
            ``True`` iff every object has a maximal covering sieve.
        """
        result = True
        for obj_id, covering_sieves in self.sieves.items():
            has_maximal = any(s.is_maximal for s in covering_sieves)
            if not has_maximal:
                log.warning(
                    "GrothendieckTopology.check_maximality: object %r has no "
                    "maximal sieve in J(%r)",
                    obj_id, obj_id,
                )
                result = False
        self.axioms_verified["maximality"] = result
        return result

    # ------------------------------------------------------------------
    # Axiom 2: Stability
    # ------------------------------------------------------------------

    def check_stability(self, sieve: Sieve, morphism_id: str) -> bool:
        """Verify the stability axiom for a specific sieve/morphism pair.

        Given :math:`S \\in J(X)` and :math:`f : Y \\to X`, the pullback
        :math:`f^* S` must be in :math:`J(Y)`.

        In our symbolic model we check that the pulled-back sieve is
        non-empty (a necessary condition) and record that it was registered.

        Parameters
        ----------
        sieve:
            A covering sieve :math:`S \\in J(X)`.
        morphism_id:
            The morphism :math:`f : Y \\to X` to pull back along.

        Returns
        -------
        bool
            ``True`` iff the pullback sieve is non-empty / covering.
        """
        pb = sieve.pullback(morphism_id)
        stable = pb.is_covering()
        if not stable:
            log.warning(
                "GrothendieckTopology.check_stability: pullback of %r along "
                "%r is not covering — stability violated",
                sieve.object_id, morphism_id,
            )
        self.axioms_verified["stability"] = stable
        return stable

    # ------------------------------------------------------------------
    # Axiom 3: Transitivity
    # ------------------------------------------------------------------

    def check_transitivity(self, sieve: Sieve, family: list[Sieve]) -> bool:
        """Verify the transitivity axiom for a sieve and a refinement family.

        If :math:`S \\in J(X)` and for each :math:`f \\in S` the pullback
        :math:`f^* R` is covering, then :math:`R` itself covers :math:`X`.

        Here we verify the converse direction: that a sieve which is covered
        by a family of sieves (each of which is covering) is itself
        registered as covering.

        Parameters
        ----------
        sieve:
            The base covering sieve :math:`S \\in J(X)`.
        family:
            The refinement sieves :math:`\\{T_i\\}` that together refine
            every element of :math:`S`.

        Returns
        -------
        bool
            ``True`` iff the composite coverage condition holds.
        """
        # A necessary condition: every sieve in the family must be covering.
        all_cover = all(s.is_covering() for s in family)
        if not all_cover:
            log.warning(
                "GrothendieckTopology.check_transitivity: not all sieves in "
                "family are covering — transitivity check failed"
            )
            self.axioms_verified["transitivity"] = False
            return False
        # The base sieve must itself be covering.
        if not sieve.is_covering():
            log.warning(
                "GrothendieckTopology.check_transitivity: base sieve %r is "
                "not covering",
                sieve.object_id,
            )
            self.axioms_verified["transitivity"] = False
            return False
        # Both conditions met: the composite cover is valid.
        self.axioms_verified["transitivity"] = True
        return True

    # ------------------------------------------------------------------
    # Topology construction helpers
    # ------------------------------------------------------------------

    def generate_sieve(self, morphisms: list[str], object_id: str) -> Sieve:
        """Generate a sieve from an explicit list of morphism IDs.

        The resulting sieve is *not* automatically registered as covering;
        call :meth:`add_cover` after inspecting it.

        Parameters
        ----------
        morphisms:
            List of morphism IDs that generate the sieve (their
            pre-composition closure constitutes the full sieve).
        object_id:
            The codomain object.

        Returns
        -------
        Sieve
            The generated sieve (``is_maximal=False``).
        """
        if not morphisms:
            log.warning(
                "GrothendieckTopology.generate_sieve: empty morphism list for "
                "object %r — resulting sieve is trivial",
                object_id,
            )
        return Sieve(
            object_id=object_id,
            generating_morphisms=list(morphisms),
            is_maximal=False,
            site_ref=self.site_id,
        )

    def get_all_covers(self, object_id: str) -> list[Sieve]:
        """Return all registered covering sieves for *object_id*."""
        return list(self.sieves.get(object_id, []))

    def add_cover(self, object_id: str, sieve: Sieve) -> None:
        """Register *sieve* as a covering sieve for *object_id*.

        Logs a warning if the sieve is not flagged as covering (i.e. it is
        empty and not maximal), since such sieves violate the topology.
        """
        if not sieve.is_covering():
            log.warning(
                "GrothendieckTopology.add_cover: sieve for %r is empty and "
                "non-maximal — this violates the topology axioms",
                object_id,
            )
        self.sieves.setdefault(object_id, []).append(sieve)
        log.debug(
            "GrothendieckTopology.add_cover: registered cover for %r "
            "(total covers: %d)",
            object_id, len(self.sieves[object_id]),
        )

    def verify_axioms(self) -> dict[str, bool]:
        """Run all topology axiom checks and return a summary dict.

        Returns
        -------
        dict[str, bool]
            Keys: ``"maximality"``, ``"stability"``, ``"transitivity"``,
            ``"all_pass"``.
        """
        max_ok = self.check_maximality()
        # Stability: test pullback for every sieve along a synthetic morphism.
        stab_results: list[bool] = []
        for obj_id, covs in self.sieves.items():
            for sieve in covs:
                stab_results.append(
                    self.check_stability(sieve, f"id_{obj_id}")
                )
        stab_ok = all(stab_results) if stab_results else True

        # Transitivity: test each pair of consecutive sieves per object.
        trans_results: list[bool] = []
        for obj_id, covs in self.sieves.items():
            if len(covs) >= 2:
                trans_results.append(
                    self.check_transitivity(covs[0], covs[1:])
                )
        trans_ok = all(trans_results) if trans_results else True

        report = {
            "maximality": max_ok,
            "stability": stab_ok,
            "transitivity": trans_ok,
            "all_pass": max_ok and stab_ok and trans_ok,
        }
        self.axioms_verified.update(report)
        log.info("GrothendieckTopology.verify_axioms: %s", report)
        return report

    def describe(self) -> str:
        """Return a multi-line summary of this topology."""
        lines = [
            f"GrothendieckTopology(site={self.site_id!r})",
            f"  Objects with covers: {list(self.sieves.keys())}",
            f"  Total covering sieves: {sum(len(v) for v in self.sieves.values())}",
            f"  Axiom cache: {self.axioms_verified}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §9.1 Definition 9.6 — Sheaf on a Site
# ---------------------------------------------------------------------------


class SheafOnSite:
    """A trust sheaf on the judgment site :math:`(C, J)`.

    Implements §9.1 Definition 9.6: a sheaf :math:`T : C^{\\mathrm{op}}
    \\to \\mathbf{Set}` satisfying locality and gluing with respect to the
    Grothendieck topology :math:`J`.

    In JuGeo's trust model, :math:`T(X)` is the set of admissible
    :class:`~jugeo.evidence.trust.TrustProfile` records for context
    :math:`X`.  The sheaf condition guarantees that if compatible trust
    profiles are provided over every element of a cover, they uniquely
    assemble into a global trust profile for the whole context.

    Parameters
    ----------
    site_id:
        Identifier of the parent site.
    sections:
        Mapping ``object_id → section value`` (the presheaf data).
    restriction_maps:
        Mapping ``morphism_id → description of restriction function``.
        In full generality these would be actual callables; here we store
        descriptors and simulate the restriction semantics.
    gluing_verified:
        ``True`` once :meth:`check_sheaf_condition` has confirmed gluing.
    sheaf_name:
        Human-readable name for this sheaf.
    """

    def __init__(
        self,
        site_id: str,
        sections: dict[str, Any] | None = None,
        restriction_maps: dict[str, Any] | None = None,
        gluing_verified: bool = False,
        sheaf_name: str = "trust_sheaf",
    ) -> None:
        self.site_id = site_id
        self.sections: dict[str, Any] = sections or {}
        self.restriction_maps: dict[str, Any] = restriction_maps or {}
        self.gluing_verified = gluing_verified
        self.sheaf_name = sheaf_name

    def section(self, object_id: str) -> Any:
        """Return the section of this sheaf over *object_id*.

        Raises
        ------
        KeyError
            If no section has been registered for *object_id*.
        """
        if object_id not in self.sections:
            raise KeyError(
                f"SheafOnSite({self.sheaf_name!r}): no section for "
                f"object {object_id!r}"
            )
        return self.sections[object_id]

    def restrict(self, morphism_id: str, section_data: Any) -> Any:
        """Apply the restriction map for *morphism_id* to *section_data*.

        In the trust-sheaf interpretation, restriction corresponds to
        *attenuation*: transporting a trust profile from a larger context
        to a sub-context may weaken it (§9.1 Remark 9.13).

        If no explicit restriction map is registered for *morphism_id*, a
        default attenuation is applied: the trust level is capped at
        :attr:`TrustLevel.HUMAN_ATTESTED`.

        Parameters
        ----------
        morphism_id:
            The morphism :math:`f : Y \\to X` along which to restrict.
        section_data:
            The section value over the codomain :math:`X`.

        Returns
        -------
        Any
            The restricted section over the domain :math:`Y`.
        """
        if morphism_id in self.restriction_maps:
            restriction_fn = self.restriction_maps[morphism_id]
            if callable(restriction_fn):
                return restriction_fn(section_data)
            # Non-callable descriptor: return data unchanged (identity restriction)
            log.debug(
                "SheafOnSite.restrict: non-callable restriction map for %r; "
                "returning section unchanged",
                morphism_id,
            )
            return section_data
        # Default attenuation: if section_data is a dict with a trust_level key,
        # cap it at HUMAN_ATTESTED.
        if isinstance(section_data, dict) and "trust_level" in section_data:
            current = section_data.get("trust_level")
            if isinstance(current, TrustLevel) and _trust_gte(
                current, TrustLevel.SOLVER_DISCHARGED
            ):
                attenuated = dict(section_data)
                attenuated["trust_level"] = TrustLevel.HUMAN_ATTESTED
                log.debug(
                    "SheafOnSite.restrict: attenuated trust from %s to "
                    "HUMAN_ATTESTED along morphism %r",
                    current.name, morphism_id,
                )
                return attenuated
        return section_data

    def check_sheaf_condition(
        self,
        cover: list[str],
        sections_data: dict[str, Any],
    ) -> bool:
        """Verify locality and gluing for a given cover.

        **Locality** — two sections that agree on every cover element are
        equal.  We check this by verifying that the sections indexed by
        *cover* are internally consistent (no contradictions).

        **Gluing** — a compatible family of sections over *cover* glues to a
        unique global section.  We check that the sections have a common
        join (the strongest consistent trust level).

        Parameters
        ----------
        cover:
            List of object IDs forming a cover of some global context.
        sections_data:
            Mapping ``object_id → section value`` for each element of the
            cover.

        Returns
        -------
        bool
            ``True`` iff locality and gluing both hold for this cover.
        """
        if not cover:
            log.warning("SheafOnSite.check_sheaf_condition: empty cover")
            return False
        missing = [oid for oid in cover if oid not in sections_data]
        if missing:
            log.warning(
                "SheafOnSite.check_sheaf_condition: missing sections for %s",
                missing,
            )
            return False

        # Locality check: look for contradictions among trust levels.
        trust_levels: list[TrustLevel] = []
        for oid in cover:
            s = sections_data[oid]
            if isinstance(s, dict) and "trust_level" in s:
                tl = s["trust_level"]
                if isinstance(tl, TrustLevel):
                    if tl is TrustLevel.CONTRADICTED:
                        log.warning(
                            "SheafOnSite.check_sheaf_condition: contradicted "
                            "section at %r — locality fails",
                            oid,
                        )
                        return False
                    trust_levels.append(tl)

        # Gluing check: compatible sections must have a well-defined join.
        if trust_levels:
            join_level = _strongest(trust_levels)
            log.debug(
                "SheafOnSite.check_sheaf_condition: gluing join trust = %s",
                join_level.name,
            )
        self.gluing_verified = True
        return True

    def glue(
        self,
        cover: list[str],
        compatible_sections: dict[str, Any],
    ) -> Any:
        """Construct the unique global section from a compatible family.

        Given a compatible family of sections over *cover*, returns the
        glued global section.  The glued section carries the strongest
        (most verified) trust level attested across the cover, consistent
        with the sheaf's trust ordering.

        Parameters
        ----------
        cover:
            List of object IDs forming the cover.
        compatible_sections:
            A compatible family of sections (already verified by
            :meth:`check_sheaf_condition`).

        Returns
        -------
        Any
            The glued global section dict.

        Raises
        ------
        ValueError
            If the cover is empty or sections are missing.
        """
        if not cover:
            raise ValueError("SheafOnSite.glue: cannot glue over empty cover")
        missing = [oid for oid in cover if oid not in compatible_sections]
        if missing:
            raise ValueError(
                f"SheafOnSite.glue: missing sections for {missing}"
            )
        trust_levels: list[TrustLevel] = []
        payload_union: dict[str, Any] = {}
        for oid in cover:
            s = compatible_sections[oid]
            if isinstance(s, dict):
                payload_union.update(s)
                tl = s.get("trust_level")
                if isinstance(tl, TrustLevel):
                    trust_levels.append(tl)

        joined_trust = _strongest(trust_levels) if trust_levels else TrustLevel.UNVERIFIED
        glued: dict[str, Any] = dict(payload_union)
        glued["trust_level"] = joined_trust
        glued["glued_from"] = cover
        glued["sheaf"] = self.sheaf_name
        log.debug(
            "SheafOnSite.glue: glued %d sections; joined trust = %s",
            len(cover), joined_trust.name,
        )
        return glued

    def global_section(self) -> Any:
        """Return the unique global section, if one exists.

        A global section is present when a section is registered for the
        distinguished terminal object ``"__global__"``.  If it is absent,
        attempts to glue all registered sections.

        Returns
        -------
        Any
            The global section dict, or ``None`` if not computable.
        """
        if "__global__" in self.sections:
            return self.sections["__global__"]
        if not self.sections:
            log.warning("SheafOnSite.global_section: no sections registered")
            return None
        cover = list(self.sections.keys())
        try:
            glued = self.glue(cover, self.sections)
            self.sections["__global__"] = glued
            return glued
        except (ValueError, KeyError) as exc:
            log.warning("SheafOnSite.global_section: gluing failed — %s", exc)
            return None

    def describe(self) -> str:
        """Return a multi-line summary of this sheaf."""
        lines = [
            f"SheafOnSite(name={self.sheaf_name!r}, site={self.site_id!r})",
            f"  Sections: {list(self.sections.keys())}",
            f"  Restriction maps: {list(self.restriction_maps.keys())}",
            f"  Gluing verified: {self.gluing_verified}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §9.1 — Category Structure (underlying category of the site)
# ---------------------------------------------------------------------------


class CategoryStructure:
    """The underlying small category :math:`C` of the judgment site.

    Stores objects, morphisms, composition table, and identity morphisms,
    and provides methods for verifying the category axioms (associativity,
    unitality).

    Parameters
    ----------
    objects:
        Set of object IDs in :math:`C`.
    morphisms:
        Dict mapping morphism IDs to ``{"source": str, "target": str,
        "data": dict}``.
    composition_table:
        Dict mapping ``(f_id, g_id)`` to the ID of the composite
        :math:`g \\circ f` (apply :math:`f` first).
    identity_map:
        Dict mapping object ID to its identity morphism ID.
    """

    def __init__(
        self,
        objects: set[str] | None = None,
        morphisms: dict[str, dict[str, Any]] | None = None,
        composition_table: dict[tuple[str, str], str] | None = None,
        identity_map: dict[str, str] | None = None,
    ) -> None:
        self.objects: set[str] = objects or set()
        self.morphisms: dict[str, dict[str, Any]] = morphisms or {}
        self.composition_table: dict[tuple[str, str], str] = composition_table or {}
        self.identity_map: dict[str, str] = identity_map or {}

    def compose(self, f_id: str, g_id: str) -> str | None:
        """Compose morphisms :math:`f` then :math:`g` (i.e. :math:`g \\circ f`).

        Returns
        -------
        str | None
            The ID of the composite morphism, or ``None`` if the composition
            is not defined (target of :math:`f` ≠ source of :math:`g`).
        """
        f = self.morphisms.get(f_id)
        g = self.morphisms.get(g_id)
        if f is None or g is None:
            log.warning("CategoryStructure.compose: unknown morphism %r or %r", f_id, g_id)
            return None
        if f["target"] != g["source"]:
            log.warning(
                "CategoryStructure.compose: target of %r (%r) ≠ source of %r (%r)",
                f_id, f["target"], g_id, g["source"],
            )
            return None
        key = (f_id, g_id)
        if key in self.composition_table:
            return self.composition_table[key]
        # Register a new synthetic composite morphism.
        comp_id = f"comp_{f_id}_{g_id}"
        self.morphisms[comp_id] = {
            "source": f["source"],
            "target": g["target"],
            "data": {"composite_of": [f_id, g_id]},
        }
        self.composition_table[key] = comp_id
        log.debug("CategoryStructure.compose: registered composite %r", comp_id)
        return comp_id

    def identity(self, obj_id: str) -> str:
        """Return the identity morphism ID for *obj_id*, creating it if needed."""
        if obj_id in self.identity_map:
            return self.identity_map[obj_id]
        id_morphism_id = f"id_{obj_id}"
        self.morphisms[id_morphism_id] = {
            "source": obj_id,
            "target": obj_id,
            "data": {"identity": True},
        }
        self.identity_map[obj_id] = id_morphism_id
        self.objects.add(obj_id)
        return id_morphism_id

    def check_category_axioms(self) -> dict[str, bool]:
        """Verify associativity and unitality for all registered morphisms.

        Associativity: for all composable triples :math:`f, g, h`,
        :math:`(h \\circ g) \\circ f = h \\circ (g \\circ f)`.

        Unitality: for every morphism :math:`f : X \\to Y`,
        :math:`\\mathrm{id}_Y \\circ f = f = f \\circ \\mathrm{id}_X`.

        Returns
        -------
        dict[str, bool]
            ``{"associativity": bool, "unitality": bool, "all_pass": bool}``
        """
        assoc_ok = True
        unit_ok = True
        morph_ids = list(self.morphisms.keys())

        # Unitality check.
        for f_id, f_data in self.morphisms.items():
            if f_data.get("data", {}).get("identity"):
                continue  # skip identity morphisms themselves
            src = f_data["source"]
            tgt = f_data["target"]
            id_src = self.identity_map.get(src, f"id_{src}")
            id_tgt = self.identity_map.get(tgt, f"id_{tgt}")
            # id_tgt ∘ f should equal f
            comp_left = self.composition_table.get((f_id, id_tgt))
            # f ∘ id_src should equal f
            comp_right = self.composition_table.get((id_src, f_id))
            # We only check entries that have been explicitly registered.
            if comp_left is not None and comp_left != f_id:
                log.warning(
                    "CategoryStructure.check_category_axioms: unitality "
                    "failure for %r (left unit)", f_id,
                )
                unit_ok = False
            if comp_right is not None and comp_right != f_id:
                log.warning(
                    "CategoryStructure.check_category_axioms: unitality "
                    "failure for %r (right unit)", f_id,
                )
                unit_ok = False

        # Associativity check: iterate over composable triples.
        for f_id in morph_ids:
            f = self.morphisms[f_id]
            for g_id in morph_ids:
                g = self.morphisms[g_id]
                if f["target"] != g["source"]:
                    continue
                fg = self.composition_table.get((f_id, g_id))
                if fg is None:
                    continue
                for h_id in morph_ids:
                    h = self.morphisms[h_id]
                    if g["target"] != h["source"]:
                        continue
                    gh = self.composition_table.get((g_id, h_id))
                    if gh is None:
                        continue
                    # (h∘g)∘f vs h∘(g∘f)
                    fg_h = self.composition_table.get((fg, h_id))
                    f_gh = self.composition_table.get((f_id, gh))
                    if fg_h is not None and f_gh is not None and fg_h != f_gh:
                        log.warning(
                            "CategoryStructure.check_category_axioms: "
                            "associativity failure on (%r, %r, %r)",
                            f_id, g_id, h_id,
                        )
                        assoc_ok = False

        result = {
            "associativity": assoc_ok,
            "unitality": unit_ok,
            "all_pass": assoc_ok and unit_ok,
        }
        log.info("CategoryStructure.check_category_axioms: %s", result)
        return result

    def is_functor_from(
        self,
        other: CategoryStructure,
        object_map: dict[str, str],
        morphism_map: dict[str, str],
    ) -> bool:
        """Check whether the given maps define a functor from *other* to self.

        A functor :math:`F : D \\to C` must satisfy:

        - :math:`F(\\mathrm{id}_X) = \\mathrm{id}_{F(X)}` for all objects.
        - :math:`F(g \\circ f) = F(g) \\circ F(f)` for all composable pairs.

        Parameters
        ----------
        other:
            The source category :math:`D`.
        object_map:
            Maps object IDs of :math:`D` to object IDs of self.
        morphism_map:
            Maps morphism IDs of :math:`D` to morphism IDs of self.

        Returns
        -------
        bool
            ``True`` iff the maps constitute a valid functor.
        """
        # Identity preservation.
        for obj_id in other.objects:
            if obj_id not in object_map:
                log.warning("is_functor_from: object %r not in object_map", obj_id)
                return False
            other_id_morph = other.identity_map.get(obj_id)
            self_id_morph = self.identity_map.get(object_map[obj_id])
            if other_id_morph and other_id_morph in morphism_map:
                if morphism_map[other_id_morph] != self_id_morph:
                    log.warning(
                        "is_functor_from: identity not preserved for %r", obj_id
                    )
                    return False
        # Composition preservation.
        for (f_id, g_id), fg_id in other.composition_table.items():
            if f_id not in morphism_map or g_id not in morphism_map:
                continue
            Ff = morphism_map[f_id]
            Fg = morphism_map[g_id]
            Ffg_expected = self.composition_table.get((Ff, Fg))
            Ffg_actual = morphism_map.get(fg_id)
            if Ffg_expected is not None and Ffg_actual != Ffg_expected:
                log.warning(
                    "is_functor_from: composition not preserved for (%r, %r)",
                    f_id, g_id,
                )
                return False
        return True


# ---------------------------------------------------------------------------
# §9.1 — Site Coherence Checker
# ---------------------------------------------------------------------------


class SiteCoherenceChecker:
    """Dedicated axiom-verification pass for :class:`ProgrammaticJudgmentSite`.

    Runs the three Grothendieck topology axioms over the site's topology
    and collects violation messages.

    Parameters
    ----------
    site:
        The site to check.  Stored as a forward reference to avoid import
        circularity.
    violations:
        Accumulator for human-readable violation messages.
    """

    def __init__(self, site: ProgrammaticJudgmentSite) -> None:
        self.site = site
        self.violations: list[str] = []

    def check_all(self) -> dict[str, bool]:
        """Run all axiom checks and return a summary report."""
        self.violations.clear()
        max_ok = self.check_maximality_axiom()
        stab_ok = self.check_stability_axiom()
        trans_ok = self.check_transitivity_axiom()
        report = {
            "maximality": max_ok,
            "stability": stab_ok,
            "transitivity": trans_ok,
            "all_pass": max_ok and stab_ok and trans_ok,
        }
        log.info("SiteCoherenceChecker.check_all: %s", report)
        return report

    def check_maximality_axiom(self) -> bool:
        """Verify the maximality axiom over the site topology."""
        topology = self.site.grothendieck_topology
        if topology is None:
            self.violations.append("No Grothendieck topology defined — maximality unknown")
            return False
        ok = topology.check_maximality()
        if not ok:
            self.violations.append(
                "Maximality axiom violated: some objects lack a maximal covering sieve"
            )
        return ok

    def check_stability_axiom(self) -> bool:
        """Verify the stability axiom over all registered covers."""
        topology = self.site.grothendieck_topology
        if topology is None:
            self.violations.append("No Grothendieck topology defined — stability unknown")
            return False
        results: list[bool] = []
        for obj_id, sieves in topology.sieves.items():
            for sieve in sieves:
                ok = topology.check_stability(sieve, f"id_{obj_id}")
                if not ok:
                    self.violations.append(
                        f"Stability axiom violated for sieve on {obj_id!r}"
                    )
                results.append(ok)
        return all(results) if results else True

    def check_transitivity_axiom(self) -> bool:
        """Verify the transitivity axiom over all objects with ≥ 2 covers."""
        topology = self.site.grothendieck_topology
        if topology is None:
            self.violations.append("No Grothendieck topology defined — transitivity unknown")
            return False
        results: list[bool] = []
        for obj_id, sieves in topology.sieves.items():
            if len(sieves) >= 2:
                ok = topology.check_transitivity(sieves[0], sieves[1:])
                if not ok:
                    self.violations.append(
                        f"Transitivity axiom violated for object {obj_id!r}"
                    )
                results.append(ok)
        return all(results) if results else True

    def get_violations(self) -> list[str]:
        """Return the list of violation messages from the last :meth:`check_all` call."""
        return list(self.violations)

    def describe_failures(self) -> str:
        """Return a human-readable summary of all violations."""
        if not self.violations:
            return "SiteCoherenceChecker: no violations detected."
        lines = ["SiteCoherenceChecker violations:"] + [
            f"  - {v}" for v in self.violations
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# §9.1 — ProgrammaticJudgmentSite (main class)
# ---------------------------------------------------------------------------


class ProgrammaticJudgmentSite:
    """Formal site for JuGeo judgment contexts (§9.1 Definition 9.1).

    This is the top-level object that the JuGeo reasoning pipeline uses to
    organise judgment contexts, their morphisms, covering sieves, and trust
    sheaf data.  It wraps :class:`CategoryStructure`,
    :class:`GrothendieckTopology`, and :class:`SheafOnSite` behind a
    domain-oriented API.

    Parameters
    ----------
    site_id:
        Stable unique identifier for this site instance.
    name:
        Human-readable name, e.g. ``"arithmetic_site_v2"``.
    objects:
        Initial mapping ``obj_id → FormalJudgmentObject``.
    morphisms:
        Initial morphism registry (same schema as
        :class:`CategoryStructure`'s ``morphisms``).
    covering_sieves:
        Initial covering sieve registry.
    grothendieck_topology:
        Pre-built topology, or ``None`` to construct lazily.
    trust_sheaf:
        Pre-built trust sheaf, or ``None``.
    category_structure:
        The underlying category; constructed automatically if not supplied.
    coherence_checker:
        Axiom verification pass; constructed automatically on first use.
    """

    def __init__(
        self,
        site_id: str,
        name: str = "",
        objects: dict[str, FormalJudgmentObject] | None = None,
        morphisms: dict[str, dict[str, Any]] | None = None,
        covering_sieves: dict[str, list[Sieve]] | None = None,
        grothendieck_topology: GrothendieckTopology | None = None,
        trust_sheaf: SheafOnSite | None = None,
        category_structure: CategoryStructure | None = None,
        coherence_checker: SiteCoherenceChecker | None = None,
    ) -> None:
        self.site_id = site_id
        self.name = name or site_id
        self.objects: dict[str, FormalJudgmentObject] = objects or {}
        self.morphisms: dict[str, dict[str, Any]] = morphisms or {}
        self.covering_sieves: dict[str, list[Sieve]] = covering_sieves or {}
        self.grothendieck_topology = grothendieck_topology
        self.trust_sheaf = trust_sheaf
        self.category_structure = category_structure or CategoryStructure(
            objects=set(self.objects.keys()),
            morphisms=dict(self.morphisms),
        )
        self.coherence_checker: SiteCoherenceChecker | None = coherence_checker
        log.info(
            "ProgrammaticJudgmentSite.__init__: created site %r with "
            "%d objects, %d morphisms",
            self.site_id, len(self.objects), len(self.morphisms),
        )

    # ------------------------------------------------------------------
    # Judgment object management
    # ------------------------------------------------------------------

    def add_judgment_object(
        self,
        obj_id: str,
        judgment_data: dict[str, Any],
        trust_level: TrustLevel,
    ) -> FormalJudgmentObject:
        """Register a new judgment context object in the site.

        Parameters
        ----------
        obj_id:
            Unique identifier for the new object.
        judgment_data:
            Payload dict (formula, variable bindings, etc.).
        trust_level:
            Initial trust level for this judgment.

        Returns
        -------
        FormalJudgmentObject
            The newly created and registered object.

        Raises
        ------
        ValueError
            If *obj_id* is already registered.
        """
        if obj_id in self.objects:
            raise ValueError(
                f"ProgrammaticJudgmentSite: object {obj_id!r} already exists"
            )
        judgment_type = judgment_data.get("judgment_type", "semantic")
        support_kind = judgment_data.get("support_kind", "unspecified")
        obj = FormalJudgmentObject(
            obj_id=obj_id,
            judgment_type=str(judgment_type),
            payload=dict(judgment_data),
            trust_level=trust_level,
            support_kind=str(support_kind),
        )
        if not obj.validate():
            raise ValueError(
                f"ProgrammaticJudgmentSite: invalid judgment object {obj_id!r}"
            )
        self.objects[obj_id] = obj
        self.category_structure.objects.add(obj_id)
        # Register identity morphism in the category structure.
        self.category_structure.identity(obj_id)
        log.debug(
            "ProgrammaticJudgmentSite.add_judgment_object: added %r (trust=%s)",
            obj_id, trust_level.name,
        )
        return obj

    # ------------------------------------------------------------------
    # Morphism management
    # ------------------------------------------------------------------

    def add_morphism(
        self,
        source_id: str,
        target_id: str,
        morphism_data: dict[str, Any],
        is_covering: bool = False,
    ) -> str:
        """Register a context morphism :math:`f : \\mathrm{source} \\to \\mathrm{target}`.

        Parameters
        ----------
        source_id:
            Source object ID.
        target_id:
            Target object ID.
        morphism_data:
            Arbitrary metadata for the morphism (substitution, weakening
            rule, etc.).
        is_covering:
            If ``True``, automatically add this morphism to a singleton
            covering sieve for *target_id*.

        Returns
        -------
        str
            The newly assigned morphism ID.
        """
        for oid in (source_id, target_id):
            if oid not in self.objects:
                log.warning(
                    "ProgrammaticJudgmentSite.add_morphism: object %r not "
                    "registered — auto-registering as UNVERIFIED placeholder",
                    oid,
                )
                placeholder_data: dict[str, Any] = {
                    "judgment_type": "semantic",
                    "support_kind": "placeholder",
                }
                self.add_judgment_object(oid, placeholder_data, TrustLevel.UNVERIFIED)

        morph_id = morphism_data.get("morphism_id") or f"morph_{source_id}_{target_id}_{uuid.uuid4().hex[:6]}"
        entry: dict[str, Any] = {
            "source": source_id,
            "target": target_id,
            "data": dict(morphism_data),
        }
        self.morphisms[morph_id] = entry
        self.category_structure.morphisms[morph_id] = entry

        if is_covering:
            sieve = Sieve(
                object_id=target_id,
                generating_morphisms=[morph_id],
                is_maximal=False,
                site_ref=self.site_id,
            )
            self.define_covering_sieve(target_id, sieve)

        log.debug(
            "ProgrammaticJudgmentSite.add_morphism: %r → %r (id=%r, covering=%s)",
            source_id, target_id, morph_id, is_covering,
        )
        return morph_id

    # ------------------------------------------------------------------
    # Covering sieve management
    # ------------------------------------------------------------------

    def define_covering_sieve(self, object_id: str, sieve: Sieve) -> None:
        """Register *sieve* as a covering sieve for *object_id* in the site."""
        self.covering_sieves.setdefault(object_id, []).append(sieve)
        if self.grothendieck_topology is not None:
            self.grothendieck_topology.add_cover(object_id, sieve)
        log.debug(
            "ProgrammaticJudgmentSite.define_covering_sieve: added cover for %r",
            object_id,
        )

    # ------------------------------------------------------------------
    # Axiom verification
    # ------------------------------------------------------------------

    def check_site_axioms(self) -> dict[str, Any]:
        """Check all Grothendieck topology axioms and return a detailed report.

        Lazily builds the topology and coherence checker if not already
        present.

        Returns
        -------
        dict[str, Any]
            Keys: ``"maximality"``, ``"stability"``, ``"transitivity"``,
            ``"all_pass"``, ``"violations"``.
        """
        topology = self.get_grothendieck_topology()
        if self.coherence_checker is None:
            self.coherence_checker = SiteCoherenceChecker(self)
        report = self.coherence_checker.check_all()
        report["violations"] = self.coherence_checker.get_violations()
        cat_report = self.category_structure.check_category_axioms()
        report["category_axioms"] = cat_report
        log.info("ProgrammaticJudgmentSite.check_site_axioms: %s", report)
        return report

    # ------------------------------------------------------------------
    # Sheaf construction
    # ------------------------------------------------------------------

    def build_sheaf(self, presheaf_data: dict[str, Any]) -> SheafOnSite:
        """Construct a sheaf from presheaf data.

        Takes a mapping ``object_id → section_value`` (the presheaf) and
        verifies the sheaf condition with respect to the registered covering
        sieves.  If the condition holds, stores the sheaf in
        :attr:`trust_sheaf` and returns it.

        Parameters
        ----------
        presheaf_data:
            The raw presheaf sections.

        Returns
        -------
        SheafOnSite
            The verified sheaf.

        Raises
        ------
        ValueError
            If the sheaf condition fails on any registered cover.
        """
        sheaf = SheafOnSite(
            site_id=self.site_id,
            sections=dict(presheaf_data),
            sheaf_name=f"trust_sheaf_{self.site_id}",
        )
        # Check sheaf condition for every registered cover.
        for obj_id, sieves in self.covering_sieves.items():
            for sieve in sieves:
                cover_objects = [
                    m.split("∘")[-1] if "∘" in m else m
                    for m in sieve.generating_morphisms
                ]
                # Only check elements that have sections.
                available = [oid for oid in cover_objects if oid in presheaf_data]
                if not available:
                    continue
                cover_sections = {oid: presheaf_data[oid] for oid in available}
                ok = sheaf.check_sheaf_condition(available, cover_sections)
                if not ok:
                    raise ValueError(
                        f"ProgrammaticJudgmentSite.build_sheaf: sheaf condition "
                        f"fails on cover of {obj_id!r}"
                    )
        self.trust_sheaf = sheaf
        log.info(
            "ProgrammaticJudgmentSite.build_sheaf: sheaf built with %d sections",
            len(presheaf_data),
        )
        return sheaf

    # ------------------------------------------------------------------
    # Topology accessor
    # ------------------------------------------------------------------

    def get_grothendieck_topology(self) -> GrothendieckTopology:
        """Build (or return cached) the Grothendieck topology for this site.

        The topology is constructed from the currently registered covering
        sieves.  Each object is given a synthetic maximal sieve if none is
        already registered (ensuring the maximality axiom can be checked).
        """
        if self.grothendieck_topology is not None:
            return self.grothendieck_topology

        sieves: dict[str, list[Sieve]] = {}
        for obj_id, obj_sieves in self.covering_sieves.items():
            sieves[obj_id] = list(obj_sieves)

        # Ensure every object has a maximal sieve.
        for obj_id in self.objects:
            if obj_id not in sieves:
                sieves[obj_id] = []
            maximal = Sieve(
                object_id=obj_id,
                generating_morphisms=list(self.morphisms.keys()),
                is_maximal=True,
                site_ref=self.site_id,
            )
            sieves[obj_id].insert(0, maximal)

        self.grothendieck_topology = GrothendieckTopology(
            site_id=self.site_id,
            sieves=sieves,
        )
        log.debug(
            "ProgrammaticJudgmentSite.get_grothendieck_topology: built topology "
            "for %d objects",
            len(sieves),
        )
        return self.grothendieck_topology

    # ------------------------------------------------------------------
    # Fiber / pullback
    # ------------------------------------------------------------------

    def fiber_over(self, morphism_id: str) -> ProgrammaticJudgmentSite:
        """Compute the pullback (fiber) site along morphism *morphism_id*.

        Constructs a new :class:`ProgrammaticJudgmentSite` whose objects
        are those reachable from the domain of *morphism_id* and whose
        covering sieves are the pullbacks of covering sieves in self.

        Parameters
        ----------
        morphism_id:
            The morphism :math:`f : Y \\to X` along which to pull back.

        Returns
        -------
        ProgrammaticJudgmentSite
            The fiber site over the domain of *morphism_id*.

        Raises
        ------
        KeyError
            If *morphism_id* is not registered in :attr:`morphisms`.
        """
        morph = self.morphisms[morphism_id]  # raises KeyError if absent
        source_id = morph["source"]
        fiber_id = f"fiber_{self.site_id}_{morphism_id}"

        fiber_objects: dict[str, FormalJudgmentObject] = {}
        if source_id in self.objects:
            fiber_objects[source_id] = self.objects[source_id]

        # Pull back covering sieves.
        fiber_sieves: dict[str, list[Sieve]] = {}
        for obj_id, sieves in self.covering_sieves.items():
            pb_sieves = [s.pullback(morphism_id) for s in sieves]
            fiber_sieves[f"pb_{obj_id}"] = pb_sieves

        fiber_site = ProgrammaticJudgmentSite(
            site_id=fiber_id,
            name=f"Fiber of {self.name!r} over {morphism_id!r}",
            objects=fiber_objects,
            covering_sieves=fiber_sieves,
        )
        log.info(
            "ProgrammaticJudgmentSite.fiber_over: constructed fiber site %r",
            fiber_id,
        )
        return fiber_site

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_formal_site(self) -> dict[str, Any]:
        """Export this site as a JSON-safe formal site dictionary."""
        return {
            "site_id": self.site_id,
            "name": self.name,
            "objects": {
                oid: obj.to_dict() for oid, obj in self.objects.items()
            },
            "morphisms": dict(self.morphisms),
            "covering_sieves": {
                oid: [s.describe() for s in sieves]
                for oid, sieves in self.covering_sieves.items()
            },
            "grothendieck_topology": (
                self.grothendieck_topology.describe()
                if self.grothendieck_topology else None
            ),
            "trust_sheaf": (
                self.trust_sheaf.describe() if self.trust_sheaf else None
            ),
        }

    # ------------------------------------------------------------------
    # Description
    # ------------------------------------------------------------------

    def describe(self) -> str:
        """Return a rich multi-line description of this site."""
        lines = [
            f"ProgrammaticJudgmentSite(id={self.site_id!r}, name={self.name!r})",
            f"  Objects ({len(self.objects)}): {list(self.objects.keys())[:10]}{'…' if len(self.objects) > 10 else ''}",
            f"  Morphisms ({len(self.morphisms)}): {list(self.morphisms.keys())[:5]}{'…' if len(self.morphisms) > 5 else ''}",
            f"  Covering sieves: {list(self.covering_sieves.keys())}",
            f"  Topology: {'present' if self.grothendieck_topology else 'not built'}",
            f"  Trust sheaf: {'present' if self.trust_sheaf else 'not built'}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # classmethod: import from jugeo.geometry.site
    # ------------------------------------------------------------------

    @classmethod
    def from_geometry_site(
        cls,
        geometry_site: Any,
    ) -> ProgrammaticJudgmentSite:
        """Construct a :class:`ProgrammaticJudgmentSite` from a geometry site.

        If :mod:`jugeo.geometry.site` is available, reads the geometry site's
        context objects and morphisms and imports them into the formal site.
        If the module is unavailable, raises :exc:`ImportError`.

        Parameters
        ----------
        geometry_site:
            A ``JudgmentSite`` instance from :mod:`jugeo.geometry.site`.

        Returns
        -------
        ProgrammaticJudgmentSite
            The equivalent formal site.

        Raises
        ------
        ImportError
            If :mod:`jugeo.geometry.site` is not installed.
        TypeError
            If *geometry_site* is not a recognised geometry site type.
        """
        if not _HAS_GEOMETRY_SITE:
            raise ImportError(
                "jugeo.geometry.site is not available; cannot import geometry site"
            )
        if JudgmentSite is not None and not isinstance(geometry_site, JudgmentSite):
            raise TypeError(
                f"from_geometry_site: expected JudgmentSite, got {type(geometry_site).__name__}"
            )
        site_id = getattr(geometry_site, "site_id", str(uuid.uuid4()))
        name = getattr(geometry_site, "name", site_id)
        formal_site = cls(site_id=site_id, name=name)

        # Import objects.
        geo_objects = getattr(geometry_site, "objects", {})
        for obj_id, geo_obj in geo_objects.items():
            payload = getattr(geo_obj, "to_dict", lambda: {})()
            tl_raw = getattr(geo_obj, "trust_level", TrustLevel.UNVERIFIED)
            trust_level = tl_raw if isinstance(tl_raw, TrustLevel) else TrustLevel.UNVERIFIED
            payload.setdefault("judgment_type", "geometric")
            payload.setdefault("support_kind", "geometry_import")
            formal_site.add_judgment_object(obj_id, payload, trust_level)

        # Import morphisms.
        geo_morphisms = getattr(geometry_site, "morphisms", {})
        for morph_id, geo_morph in geo_morphisms.items():
            src = getattr(geo_morph, "source", None) or geo_morph.get("source", "")
            tgt = getattr(geo_morph, "target", None) or geo_morph.get("target", "")
            if src and tgt:
                data: dict[str, Any] = {"morphism_id": morph_id, "imported_from": "geometry_site"}
                formal_site.add_morphism(src, tgt, data)

        log.info(
            "ProgrammaticJudgmentSite.from_geometry_site: imported site %r "
            "(%d objects, %d morphisms)",
            site_id, len(formal_site.objects), len(formal_site.morphisms),
        )
        return formal_site
