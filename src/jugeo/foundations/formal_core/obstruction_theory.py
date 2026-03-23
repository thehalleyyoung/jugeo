"""Obstruction theory for trust lifting in JuGeo — Theory2.tex §9.3.

Cohomological obstruction theory answers a fundamental question in the JuGeo
trust framework: given compatible *local* trust data over a covering family,
when can we lift to a *global* trust assignment?  This is precisely the question
of H¹ vanishing.

Concretely, let T be a sheaf of trust levels on a Grothendieck site (S, J).
A 0-cochain in C⁰(cover, T) assigns a trust value to each element U of the
cover.  The *cocycle condition* (δ = 0) says that on every overlap U ∩ V the
two restricted values agree.  A compatible 0-cocycle therefore specifies a
candidate global section.  The *obstruction class* [σ] ∈ H¹(cover, T) is the
cohomology class of the 1-cocycle that records all pairwise disagreements.

When H¹ = 0 (the site is "cohomologically trivial" for the sheaf T), every
compatible local trust datum lifts uniquely to a global section.  When H¹ ≠ 0,
at least one obstruction class is non-zero; to perform the lift one must acquire
enough extra evidence to make every such class vanish — typically by supplying
solver-backed or mechanically-verified certificates that bridge the gap between
locally-held trust levels.

This module implements:
  - ``CohomologyClass``          — an abstract cohomology class [σ] ∈ Hⁿ
  - ``Cochain``                  — a concrete n-cochain σ ∈ Cⁿ(cover, T)
  - ``CoboundaryCondition``      — the coboundary operator δ : Cⁿ → Cⁿ⁺¹
  - ``ObstructionClass``         — an element of H¹ obstructing a lift
  - ``CohomologicalObstructionComputer`` — computes H⁰ and H¹ for a site/cover
  - ``TrustObstructionMap``      — maps trust data to cochains and obstructions
  - ``DescentObstructionChecker``— high-level descent check with explanation

References
----------
Theory2.tex §9.3 "Obstruction Theory" (cohomological obstructions, coboundary
conditions, long exact sequence in cohomology, trust-lifting criteria).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier
except ImportError:  # pragma: no cover
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.channels import (
        ChannelJurisdiction,
        EvidenceChannel,
        EvidenceRequest,
        EvidenceResponse,
    )
except ImportError:  # pragma: no cover
    EvidenceChannel = None  # type: ignore[assignment,misc]
    ChannelJurisdiction = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ZERO_SENTINEL = "__zero__"


def _simplex_key(simplex: str | tuple) -> str:
    """Normalise a simplex to a canonical string key."""
    if isinstance(simplex, tuple):
        return "|".join(str(s) for s in simplex)
    return str(simplex)


def _overlap_key(u: str, v: str) -> str:
    """Canonical key for the overlap U ∩ V (order-independent)."""
    return "|".join(sorted([u, v]))


# ---------------------------------------------------------------------------
# CohomologyClass
# ---------------------------------------------------------------------------


@dataclass
class CohomologyClass:
    """An element of the cohomology group Hⁿ(cover, T).

    Theory2.tex §9.3.1 — A cohomology class [σ] is an equivalence class of
    cocycles modulo coboundaries.  The class is *trivial* (zero) iff the
    cocycle σ is a coboundary, i.e. σ = δ(τ) for some (n-1)-cochain τ.

    Parameters
    ----------
    degree:
        The cohomological degree n.
    representative:
        A dict encoding a representative cocycle.
    is_zero:
        Whether this class is the zero class in Hⁿ.
    site_id:
        Identifier of the Grothendieck site on which the sheaf lives.
    sheaf_name:
        Name of the coefficient sheaf (e.g. ``"TrustLevel"``).
    """

    degree: int
    representative: dict
    is_zero: bool
    site_id: str
    sheaf_name: str

    # ------------------------------------------------------------------
    def is_trivial(self) -> bool:
        """Return ``True`` iff this cohomology class is zero.

        A non-trivial class in H¹ represents a genuine obstruction to lifting
        compatible local sections to a global section (Theory2.tex §9.3.4).
        """
        return self.is_zero

    # ------------------------------------------------------------------
    def add(self, other: CohomologyClass) -> CohomologyClass:
        """Add two cohomology classes (group operation in Hⁿ).

        Both classes must live in the same degree, site, and sheaf.  The
        result is zero iff both summands are zero (the group law is computed
        symbolically here; a concrete implementation would use the actual
        abelian-group structure of T).

        Raises
        ------
        ValueError
            If the two classes are incompatible (different degree/site/sheaf).
        """
        if self.degree != other.degree:
            raise ValueError(
                f"Cannot add cohomology classes of different degrees "
                f"({self.degree} vs {other.degree})"
            )
        if self.site_id != other.site_id or self.sheaf_name != other.sheaf_name:
            raise ValueError(
                "Cannot add cohomology classes from different sites/sheaves: "
                f"({self.site_id!r}, {self.sheaf_name!r}) vs "
                f"({other.site_id!r}, {other.sheaf_name!r})"
            )
        merged_rep = {**self.representative, **other.representative}
        combined_zero = self.is_zero and other.is_zero
        log.debug(
            "CohomologyClass.add: degree=%d site=%r zero=%s",
            self.degree,
            self.site_id,
            combined_zero,
        )
        return CohomologyClass(
            degree=self.degree,
            representative=merged_rep,
            is_zero=combined_zero,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
        )

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Human-readable description of this cohomology class."""
        status = "trivial (zero)" if self.is_zero else "non-trivial"
        lines = [
            f"H^{self.degree}({self.site_id!r}; {self.sheaf_name!r})",
            f"  Status         : {status}",
            f"  Representative : {self.representative}",
        ]
        if not self.is_zero:
            lines.append(
                "  Interpretation : This non-zero class is an obstruction to "
                "lifting local sections to a global section."
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {
            "degree": self.degree,
            "representative": self.representative,
            "is_zero": self.is_zero,
            "site_id": self.site_id,
            "sheaf_name": self.sheaf_name,
        }


# ---------------------------------------------------------------------------
# Cochain
# ---------------------------------------------------------------------------


@dataclass
class Cochain:
    """A concrete n-cochain σ ∈ Cⁿ(cover, T).

    Theory2.tex §9.3.2 — A cochain is a function that assigns a value in the
    coefficient sheaf T to each n-simplex of the nerve of the cover.  For n=0
    this means assigning a value to each cover element U; for n=1 a value to
    each ordered pair (U, V) with U ∩ V ≠ ∅.

    Parameters
    ----------
    degree:
        Degree of the cochain (0, 1, …).
    site_id:
        Identifier of the site.
    sheaf_name:
        Name of the coefficient sheaf.
    components:
        Dict mapping simplex keys to sheaf values.
    """

    degree: int
    site_id: str
    sheaf_name: str
    components: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def evaluate(self, simplex: str | tuple) -> Any:
        """Evaluate the cochain on a simplex.

        Returns ``None`` if the simplex is not in the support of the cochain.
        """
        key = _simplex_key(simplex)
        return self.components.get(key)

    # ------------------------------------------------------------------
    def coboundary(self) -> Cochain:
        """Compute the coboundary δ(σ) ∈ Cⁿ⁺¹(cover, T).

        Theory2.tex §9.3.2 — For a 0-cochain s: Cov → T the coboundary is

            (δs)(U, V) = s(V) - s(U)        for each overlap U ∩ V ≠ ∅

        where the "difference" is taken in the abelian group structure of T
        (here represented symbolically).  A 0-cochain is a cocycle iff all
        these differences vanish, i.e. s is globally constant on overlaps.

        For a 1-cochain t the coboundary is

            (δt)(U, V, W) = t(V, W) - t(U, W) + t(U, V)

        which is the standard alternating-sum formula on triples.
        """
        result: dict[str, Any] = {}

        if self.degree == 0:
            # δ : C⁰ → C¹  —  (δs)(U,V) = s(V) - s(U)
            keys = list(self.components.keys())
            for i, u in enumerate(keys):
                for v in keys[i + 1 :]:
                    su = self.components.get(u)
                    sv = self.components.get(v)
                    edge_key = _overlap_key(u, v)
                    if su is None or sv is None:
                        result[edge_key] = None
                    elif su == sv:
                        result[edge_key] = _ZERO_SENTINEL
                    else:
                        result[edge_key] = (sv, su, "difference")
        elif self.degree == 1:
            # δ : C¹ → C²  —  alternating sum on triples
            keys = list(self.components.keys())
            # keys are "U|V" strings; reconstruct triples
            vertices: set[str] = set()
            for k in keys:
                parts = k.split("|")
                vertices.update(parts)
            vlist = sorted(vertices)
            for i, u in enumerate(vlist):
                for j, v in enumerate(vlist[i + 1 :], i + 1):
                    for w in vlist[j + 1 :]:
                        t_uv = self.components.get(_overlap_key(u, v), _ZERO_SENTINEL)
                        t_vw = self.components.get(_overlap_key(v, w), _ZERO_SENTINEL)
                        t_uw = self.components.get(_overlap_key(u, w), _ZERO_SENTINEL)
                        triple_key = f"{u}|{v}|{w}"
                        if (
                            t_uv == _ZERO_SENTINEL
                            and t_vw == _ZERO_SENTINEL
                            and t_uw == _ZERO_SENTINEL
                        ):
                            result[triple_key] = _ZERO_SENTINEL
                        else:
                            result[triple_key] = (t_vw, t_uw, t_uv, "alternating_sum")
        else:
            log.warning(
                "Cochain.coboundary: degree %d not explicitly implemented; "
                "returning empty cochain",
                self.degree,
            )

        return Cochain(
            degree=self.degree + 1,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            components=result,
        )

    # ------------------------------------------------------------------
    def add(self, other: Cochain) -> Cochain:
        """Pointwise sum of two cochains of the same degree."""
        if self.degree != other.degree:
            raise ValueError(
                f"Cannot add cochains of different degrees "
                f"({self.degree} vs {other.degree})"
            )
        merged: dict[str, Any] = {}
        all_keys = set(self.components) | set(other.components)
        for k in all_keys:
            a = self.components.get(k, _ZERO_SENTINEL)
            b = other.components.get(k, _ZERO_SENTINEL)
            if a == _ZERO_SENTINEL:
                merged[k] = b
            elif b == _ZERO_SENTINEL:
                merged[k] = a
            elif a == b:
                merged[k] = a
            else:
                merged[k] = (a, b, "sum")
        return Cochain(
            degree=self.degree,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            components=merged,
        )

    # ------------------------------------------------------------------
    def subtract(self, other: Cochain) -> Cochain:
        """Pointwise difference of two cochains of the same degree."""
        if self.degree != other.degree:
            raise ValueError(
                f"Cannot subtract cochains of different degrees "
                f"({self.degree} vs {other.degree})"
            )
        merged: dict[str, Any] = {}
        all_keys = set(self.components) | set(other.components)
        for k in all_keys:
            a = self.components.get(k, _ZERO_SENTINEL)
            b = other.components.get(k, _ZERO_SENTINEL)
            if a == b:
                merged[k] = _ZERO_SENTINEL
            elif b == _ZERO_SENTINEL:
                merged[k] = a
            elif a == _ZERO_SENTINEL:
                merged[k] = (None, b, "negation")
            else:
                merged[k] = (a, b, "difference")
        return Cochain(
            degree=self.degree,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            components=merged,
        )

    # ------------------------------------------------------------------
    def is_cocycle(self) -> bool:
        """Return ``True`` iff δ(self) is the zero cochain.

        A cochain is a cocycle when all its coboundary components are zero
        (or the ``_ZERO_SENTINEL``).  This is the necessary condition for
        representing a cohomology class.
        """
        bd = self.coboundary()
        return all(v == _ZERO_SENTINEL for v in bd.components.values())

    # ------------------------------------------------------------------
    def is_coboundary(self) -> bool:
        """Return ``True`` iff this cochain is a coboundary.

        By definition a cochain σ is a coboundary if σ = δ(τ) for some
        (degree-1)-cochain τ.  For degree-0 cochains every cochain is
        trivially a coboundary (there are no (-1)-cochains), so we return
        ``True``.  For degree-1 cochains we check whether all components
        can be expressed as a consistent difference of 0-values, i.e.
        whether the cochain lies in the image of δ : C⁰ → C¹.
        """
        if self.degree == 0:
            return True
        if self.degree == 1:
            # Attempt to find a 0-cochain whose coboundary equals self.
            # Collect vertices from overlap keys.
            vertices: set[str] = set()
            for k in self.components:
                parts = k.split("|")
                vertices.update(parts)
            if not vertices:
                return True
            # Try to assign consistent values v(u) such that
            # self(u,v) = v(v) - v(u) for all edges.
            # Fix one vertex and propagate.
            vlist = sorted(vertices)
            assignment: dict[str, Any] = {vlist[0]: _ZERO_SENTINEL}
            for k, diff in self.components.items():
                parts = k.split("|")
                if len(parts) != 2:
                    continue
                u, v = parts[0], parts[1]
                if diff == _ZERO_SENTINEL:
                    # s(v) = s(u)
                    if u in assignment and v not in assignment:
                        assignment[v] = assignment[u]
                    elif v in assignment and u not in assignment:
                        assignment[u] = assignment[v]
                    elif u in assignment and v in assignment:
                        if assignment[u] != assignment[v]:
                            return False
            # Check all edges for consistency
            for k, diff in self.components.items():
                parts = k.split("|")
                if len(parts) != 2:
                    continue
                u, v = parts[0], parts[1]
                if u in assignment and v in assignment:
                    expected = (
                        _ZERO_SENTINEL
                        if assignment[u] == assignment[v]
                        else (assignment[v], assignment[u], "difference")
                    )
                    if diff == _ZERO_SENTINEL and expected != _ZERO_SENTINEL:
                        return False
            return True
        # Higher degrees: not implemented, conservatively return False.
        log.debug(
            "Cochain.is_coboundary: degree %d not implemented; returning False",
            self.degree,
        )
        return False

    # ------------------------------------------------------------------
    def cohomology_class(self) -> CohomologyClass:
        """Return the cohomology class [self] ∈ Hⁿ.

        Raises
        ------
        ValueError
            If this cochain is not a cocycle (δ(self) ≠ 0).
        """
        if not self.is_cocycle():
            raise ValueError(
                f"Cochain of degree {self.degree} is not a cocycle; "
                "cannot form a cohomology class."
            )
        is_zero = self.is_coboundary()
        return CohomologyClass(
            degree=self.degree,
            representative=dict(self.components),
            is_zero=is_zero,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
        )

    # ------------------------------------------------------------------
    def scale(self, factor: Any) -> Cochain:
        """Scale every component of the cochain by *factor* (symbolic)."""
        scaled = {
            k: (factor, v, "scaled") if v != _ZERO_SENTINEL else _ZERO_SENTINEL
            for k, v in self.components.items()
        }
        return Cochain(
            degree=self.degree,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
            components=scaled,
        )

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Human-readable description of this cochain."""
        lines = [
            f"C^{self.degree}({self.site_id!r}; {self.sheaf_name!r})",
            f"  Components ({len(self.components)}):",
        ]
        for k, v in sorted(self.components.items()):
            lines.append(f"    {k!r:40s} -> {v!r}")
        lines.append(f"  is_cocycle    = {self.is_cocycle()}")
        lines.append(f"  is_coboundary = {self.is_coboundary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CoboundaryCondition
# ---------------------------------------------------------------------------


class CoboundaryCondition:
    """The coboundary operator δ : Cⁿ(cover, T) → Cⁿ⁺¹(cover, T).

    Theory2.tex §9.3.3 — The coboundary operator encodes the compatibility
    conditions that local sections must satisfy in order to glue.  The kernel
    ker(δ) is the space of cocycles; the image im(δ) is the space of
    coboundaries; and the cohomology Hⁿ = ker(δ)/im(δ) measures the failure
    of local-to-global for the sheaf T.

    Parameters
    ----------
    degree:
        The source degree n (δ maps degree n to degree n+1).
    domain_description:
        Human-readable description of Cⁿ.
    range_description:
        Human-readable description of Cⁿ⁺¹.
    morphisms:
        Dict encoding the transition maps of the cover (overlap data).
    """

    def __init__(
        self,
        degree: int,
        domain_description: str,
        range_description: str,
        morphisms: dict | None = None,
    ) -> None:
        self.degree = degree
        self.domain_description = domain_description
        self.range_description = range_description
        self.morphisms: dict = morphisms or {}
        log.debug(
            "CoboundaryCondition created: degree=%d domain=%r range=%r",
            degree,
            domain_description,
            range_description,
        )

    # ------------------------------------------------------------------
    def apply(self, cochain: Cochain) -> Cochain:
        """Compute δ(cochain).

        Delegates to ``Cochain.coboundary()`` but also records the morphism
        data stored in *self.morphisms* so that non-trivial restriction maps
        are respected.
        """
        if cochain.degree != self.degree:
            raise ValueError(
                f"CoboundaryCondition of degree {self.degree} cannot be "
                f"applied to a cochain of degree {cochain.degree}"
            )
        bd = cochain.coboundary()
        # Apply restriction maps from self.morphisms if present.
        if self.morphisms and cochain.degree == 0:
            for (u, v), restriction in self.morphisms.items():
                edge_key = _overlap_key(u, v)
                su = cochain.components.get(u)
                sv = cochain.components.get(v)
                if su is None or sv is None:
                    continue
                # Apply restriction map: (δs)(u,v) = restriction(sv) - su
                restricted_sv = restriction(sv) if callable(restriction) else sv
                bd.components[edge_key] = (
                    _ZERO_SENTINEL if restricted_sv == su else (restricted_sv, su, "restricted_difference")
                )
        return bd

    # ------------------------------------------------------------------
    def check_cocycle(self, cochain: Cochain) -> bool:
        """Return ``True`` iff δ(cochain) = 0, i.e. cochain is a cocycle."""
        bd = self.apply(cochain)
        return all(v == _ZERO_SENTINEL for v in bd.components.values())

    # ------------------------------------------------------------------
    def check_coboundary(self, cochain: Cochain) -> bool:
        """Return ``True`` iff *cochain* lies in the image of δ.

        Delegates to ``Cochain.is_coboundary()`` on the assumption that the
        morphisms in *self* do not alter the coboundary structure.
        """
        return cochain.is_coboundary()

    # ------------------------------------------------------------------
    def cohomology_class(self, cocycle: Cochain) -> CohomologyClass:
        """Compute the cohomology class of a cocycle."""
        if not self.check_cocycle(cocycle):
            raise ValueError("Provided cochain is not a cocycle under this coboundary condition.")
        is_zero = self.check_coboundary(cocycle)
        return CohomologyClass(
            degree=self.degree,
            representative=dict(cocycle.components),
            is_zero=is_zero,
            site_id=cocycle.site_id,
            sheaf_name=cocycle.sheaf_name,
        )

    # ------------------------------------------------------------------
    def kernel(self) -> list[dict]:
        """Describe the kernel ker(δ) — the space of cocycles.

        Returns a list of dicts, each describing a generator of the kernel
        (symbolic, since the actual computation depends on the coefficient
        sheaf's group structure).
        """
        return [
            {
                "description": f"Kernel of δ : C^{self.degree} → C^{self.degree + 1}",
                "domain": self.domain_description,
                "characterisation": "All cochains σ such that δ(σ) = 0 on every simplex.",
            }
        ]

    # ------------------------------------------------------------------
    def image(self) -> list[dict]:
        """Describe the image im(δ) — the space of coboundaries."""
        return [
            {
                "description": f"Image of δ : C^{self.degree - 1} → C^{self.degree}",
                "range": self.range_description,
                "characterisation": (
                    f"All cochains of the form δ(τ) for some τ ∈ C^{self.degree - 1}."
                ),
            }
        ]

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Human-readable summary of this coboundary operator."""
        return (
            f"δ : C^{self.degree}  →  C^{self.degree + 1}\n"
            f"  Domain  : {self.domain_description}\n"
            f"  Range   : {self.range_description}\n"
            f"  Morphisms registered: {len(self.morphisms)}"
        )


# ---------------------------------------------------------------------------
# ObstructionClass
# ---------------------------------------------------------------------------


class ObstructionClass:
    """An element of H¹(cover, T) obstructing a trust lift.

    Theory2.tex §9.3.4 — An obstruction class [σ] ∈ H¹ is represented by a
    1-cocycle σ that records the pairwise incompatibilities between local trust
    sections.  The obstruction *vanishes* if and only if σ is a coboundary,
    i.e. [σ] = 0 in H¹.  When the obstruction does not vanish, one must supply
    additional evidence to "kill" the class before lifting.

    Parameters
    ----------
    class_id:
        Unique identifier for this obstruction class.
    degree:
        Cohomological degree (typically 1 for lifting obstructions).
    coefficient_sheaf:
        Name of the sheaf of coefficients.
    representative_cochain:
        A representative 1-cocycle, if available.
    vanishes:
        Whether the obstruction class is zero.
    vanishing_condition:
        Human-readable description of what extra evidence would make the
        obstruction vanish.
    site_id:
        Identifier of the Grothendieck site.
    """

    def __init__(
        self,
        class_id: str,
        degree: int,
        coefficient_sheaf: str,
        representative_cochain: Cochain | None,
        vanishes: bool,
        vanishing_condition: str,
        site_id: str,
    ) -> None:
        self.class_id = class_id
        self.degree = degree
        self.coefficient_sheaf = coefficient_sheaf
        self.representative_cochain = representative_cochain
        self.vanishes = vanishes
        self.vanishing_condition = vanishing_condition
        self.site_id = site_id
        log.debug(
            "ObstructionClass %r created: degree=%d vanishes=%s",
            class_id,
            degree,
            vanishes,
        )

    # ------------------------------------------------------------------
    @classmethod
    def compute_from_local_data(
        cls,
        local_sections: dict,
        cover: list[str],
        site_id: str = "default",
        sheaf_name: str = "TrustLevel",
        class_id: str | None = None,
    ) -> ObstructionClass:
        """Compute the H¹ obstruction from local sections on a cover.

        Theory2.tex §9.3.4 — Given local sections {s_U : U ∈ cover}, check
        compatibility on all overlaps U ∩ V.  If every pair agrees, the
        obstruction vanishes and the cochain is a cocycle.  Otherwise the
        1-cocycle recording all disagreements represents a non-zero class.

        Parameters
        ----------
        local_sections:
            Dict mapping cover element names to their local trust values.
        cover:
            List of cover elements.
        site_id:
            Identifier for the site.
        sheaf_name:
            Name of the coefficient sheaf.
        class_id:
            Optional explicit identifier; auto-generated if omitted.
        """
        cid = class_id or f"obs_{site_id}_{sheaf_name}"
        incompatibilities: dict[str, Any] = {}
        all_compatible = True

        for i, u in enumerate(cover):
            for v in cover[i + 1 :]:
                su = local_sections.get(u)
                sv = local_sections.get(v)
                edge_key = _overlap_key(u, v)
                if su is None or sv is None:
                    continue
                if su != sv:
                    all_compatible = False
                    incompatibilities[edge_key] = (sv, su, "difference")
                    log.debug(
                        "Incompatibility on (%s, %s): %r vs %r", u, v, su, sv
                    )
                else:
                    incompatibilities[edge_key] = _ZERO_SENTINEL

        if all_compatible:
            rep_cochain = None
            vanishing_cond = "Obstruction already vanishes; sections are compatible."
        else:
            rep_cochain = Cochain(
                degree=1,
                site_id=site_id,
                sheaf_name=sheaf_name,
                components=incompatibilities,
            )
            vanishing_cond = (
                "Supply additional evidence (e.g. solver certificates or "
                "mechanically-verified proofs) that bridges the gap between "
                "the incompatible local trust values on each overlap."
            )

        return cls(
            class_id=cid,
            degree=1,
            coefficient_sheaf=sheaf_name,
            representative_cochain=rep_cochain,
            vanishes=all_compatible,
            vanishing_condition=vanishing_cond,
            site_id=site_id,
        )

    # ------------------------------------------------------------------
    def check_vanishing(self) -> bool:
        """Return ``True`` iff this obstruction class is zero in H¹."""
        if self.representative_cochain is None:
            return True
        return self.representative_cochain.is_coboundary()

    # ------------------------------------------------------------------
    def lift(self, local_data: dict) -> dict | None:
        """If the obstruction vanishes, return the global section; else ``None``.

        The global section is computed as the common value agreed upon by all
        local sections.  If the sections are incompatible the lift does not
        exist and ``None`` is returned.
        """
        if not self.vanishes and not self.check_vanishing():
            log.info(
                "ObstructionClass %r: lift blocked by non-zero obstruction.",
                self.class_id,
            )
            return None
        # Build global section by taking the "best" (highest-trust) value.
        if not local_data:
            return {}
        values = list(local_data.values())
        if TrustLevel is not None:
            try:
                best = max(
                    (v for v in values if isinstance(v, TrustLevel)),
                    default=values[0],
                    key=lambda tl: list(TrustLevel).index(tl),
                )
                return {"global_section": best, "source": "lifted"}
            except (ValueError, TypeError):
                pass
        # Fallback: return the first value.
        return {"global_section": values[0], "source": "lifted"}

    # ------------------------------------------------------------------
    def coboundary_representative(self) -> Cochain | None:
        """Return the representative 1-cocycle, if any."""
        return self.representative_cochain

    # ------------------------------------------------------------------
    def add_evidence(self, extra_evidence: dict) -> ObstructionClass:
        """Update the obstruction with new evidence; may cause it to vanish.

        Theory2.tex §9.3.5 — Evidence that resolves a pairwise incompatibility
        on an overlap effectively sets that component of the obstruction cochain
        to zero.  If *all* components become zero the class vanishes.

        Parameters
        ----------
        extra_evidence:
            Dict mapping overlap keys (e.g. ``"U|V"``) to resolved trust
            values or the sentinel ``_ZERO_SENTINEL``.
        """
        if self.vanishes or self.representative_cochain is None:
            return self  # Already vanished; nothing to do.

        new_components = dict(self.representative_cochain.components)
        for key, resolved_value in extra_evidence.items():
            normalised = _overlap_key(*key.split("|")) if "|" in key else key
            if normalised in new_components:
                new_components[normalised] = _ZERO_SENTINEL if resolved_value is None else resolved_value
                log.debug(
                    "ObstructionClass.add_evidence: resolved overlap %r", normalised
                )

        now_vanishes = all(v == _ZERO_SENTINEL for v in new_components.values())
        new_cochain = Cochain(
            degree=self.representative_cochain.degree,
            site_id=self.representative_cochain.site_id,
            sheaf_name=self.representative_cochain.sheaf_name,
            components=new_components,
        )
        return ObstructionClass(
            class_id=self.class_id,
            degree=self.degree,
            coefficient_sheaf=self.coefficient_sheaf,
            representative_cochain=new_cochain,
            vanishes=now_vanishes,
            vanishing_condition=(
                "Obstruction has been resolved by supplied evidence."
                if now_vanishes
                else self.vanishing_condition
            ),
            site_id=self.site_id,
        )

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Rich description of the obstruction class and how to clear it."""
        lines = [
            f"ObstructionClass {self.class_id!r}",
            f"  Site            : {self.site_id!r}",
            f"  Sheaf           : {self.coefficient_sheaf!r}",
            f"  Degree          : H^{self.degree}",
            f"  Vanishes        : {self.vanishes}",
            f"  Vanishing cond. : {self.vanishing_condition}",
        ]
        if self.representative_cochain is not None:
            lines.append("  Representative cochain:")
            for k, v in sorted(self.representative_cochain.components.items()):
                lines.append(f"    {k!r:40s} -> {v!r}")
        else:
            lines.append("  Representative cochain: <none> (obstruction is trivial)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {
            "class_id": self.class_id,
            "degree": self.degree,
            "coefficient_sheaf": self.coefficient_sheaf,
            "vanishes": self.vanishes,
            "vanishing_condition": self.vanishing_condition,
            "site_id": self.site_id,
            "representative_cochain": (
                self.representative_cochain.components
                if self.representative_cochain is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# CohomologicalObstructionComputer
# ---------------------------------------------------------------------------


class CohomologicalObstructionComputer:
    """Compute cohomological invariants (H⁰, H¹) for a site/sheaf pair.

    Theory2.tex §9.3.6 — The computer implements the Čech cohomology
    construction for a fixed cover and coefficient sheaf.  H⁰ is the space of
    global sections; H¹ records obstructions to gluing.

    Parameters
    ----------
    site_id:
        Identifier of the Grothendieck site.
    sheaf_name:
        Name of the coefficient sheaf (e.g. ``"TrustLevel"``).
    cover:
        Default cover (list of cover element names).
    sections_cache:
        Cache of previously computed sections.
    """

    def __init__(
        self,
        site_id: str,
        sheaf_name: str,
        cover: list[str] | None = None,
        sections_cache: dict | None = None,
    ) -> None:
        self.site_id = site_id
        self.sheaf_name = sheaf_name
        self.cover: list[str] = cover or []
        self.sections_cache: dict = sections_cache or {}
        log.debug(
            "CohomologicalObstructionComputer: site=%r sheaf=%r cover_size=%d",
            site_id,
            sheaf_name,
            len(self.cover),
        )

    # ------------------------------------------------------------------
    def compute_h0(self, sections_data: dict) -> dict:
        """Compute H⁰ — the space of global sections.

        A global section exists iff all local sections are mutually compatible
        (every pairwise restriction agrees).  Returns a dict with:

        - ``"exists"`` (bool): whether a global section exists;
        - ``"global_section"`` (Any | None): the common value if it exists;
        - ``"incompatible_pairs"`` (list): pairs with conflicting values.
        """
        incompatible: list[tuple[str, str]] = []
        cover = list(sections_data.keys())
        for i, u in enumerate(cover):
            for v in cover[i + 1 :]:
                su = sections_data[u]
                sv = sections_data[v]
                if su != sv:
                    incompatible.append((u, v))

        if not incompatible:
            values = list(sections_data.values())
            global_val = values[0] if values else None
            log.info(
                "compute_h0: global section exists on site %r: %r",
                self.site_id,
                global_val,
            )
            return {"exists": True, "global_section": global_val, "incompatible_pairs": []}

        log.info(
            "compute_h0: no global section on site %r (%d incompatible pairs)",
            self.site_id,
            len(incompatible),
        )
        return {
            "exists": False,
            "global_section": None,
            "incompatible_pairs": incompatible,
        }

    # ------------------------------------------------------------------
    def compute_h1(
        self, cover: list[str], local_sections: dict
    ) -> list[ObstructionClass]:
        """Compute H¹ obstruction classes from local sections on a cover.

        For each pair of cover elements with incompatible local sections we
        record an obstruction class.  Returns a (possibly empty) list of
        ``ObstructionClass`` instances; an empty list means H¹ = 0 and the
        lift is unobstructed.
        """
        obs = ObstructionClass.compute_from_local_data(
            local_sections=local_sections,
            cover=cover,
            site_id=self.site_id,
            sheaf_name=self.sheaf_name,
        )
        if obs.vanishes:
            log.info("compute_h1: H^1 = 0 on site %r", self.site_id)
            return []
        log.info(
            "compute_h1: non-trivial H^1 on site %r (%d incompatible overlaps)",
            self.site_id,
            (
                sum(
                    1
                    for v in obs.representative_cochain.components.values()
                    if v != _ZERO_SENTINEL
                )
                if obs.representative_cochain
                else 0
            ),
        )
        return [obs]

    # ------------------------------------------------------------------
    def lift_section(
        self, cover: list[str], local_sections: dict
    ) -> dict | None:
        """Try to lift local sections to a global section.

        Returns the global section dict if the lift succeeds (H¹ obstruction
        vanishes), or ``None`` if it is blocked.
        """
        obstructions = self.compute_h1(cover, local_sections)
        if not obstructions:
            h0 = self.compute_h0(local_sections)
            return {"global_section": h0["global_section"]} if h0["exists"] else None
        # Try each obstruction's own lift logic.
        for obs in obstructions:
            result = obs.lift(local_sections)
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------
    def obstruction_to_lifting(self, partial_lift: dict) -> ObstructionClass | None:
        """Find the obstruction to extending a partial lift.

        Given a dict of already-lifted values over a subset of the cover,
        compute the obstruction to extending to the full cover.
        """
        remaining = {u: v for u, v in self.sections_cache.items() if u not in partial_lift}
        if not remaining:
            return None  # Nothing left to extend over.
        merged = {**partial_lift, **remaining}
        obstructions = self.compute_h1(list(merged.keys()), merged)
        return obstructions[0] if obstructions else None

    # ------------------------------------------------------------------
    def get_exact_sequence(self) -> dict:
        """Return the long exact sequence in Čech cohomology.

        Theory2.tex §9.3.7 — The short exact sequence 0 → T' → T → T'' → 0
        of sheaves induces a long exact sequence

            0 → H⁰(T') → H⁰(T) → H⁰(T'') → H¹(T') → H¹(T) → H¹(T'') → …

        We return a symbolic description of the relevant portion.
        """
        return {
            "site": self.site_id,
            "sheaf": self.sheaf_name,
            "sequence": [
                f"0 → H^0({self.sheaf_name}') → H^0({self.sheaf_name}) → H^0({self.sheaf_name}'')",
                f"  → H^1({self.sheaf_name}') → H^1({self.sheaf_name}) → H^1({self.sheaf_name}'')",
                f"  → H^2({self.sheaf_name}') → …",
            ],
            "connecting_homomorphism": (
                f"δ : H^0({self.sheaf_name}'') → H^1({self.sheaf_name}') "
                "is the connecting homomorphism whose image consists precisely "
                "of the obstruction classes."
            ),
        }

    # ------------------------------------------------------------------
    def describe_obstruction_theory(self) -> str:
        """Rich narrative description of the obstruction theory for this site."""
        return (
            f"Obstruction theory on site {self.site_id!r} "
            f"with coefficient sheaf {self.sheaf_name!r}\n"
            "─────────────────────────────────────────────────────────\n"
            "Central question: given compatible local sections over the cover,\n"
            "  can they be glued to a unique global section?\n\n"
            "H⁰  =  global sections  (the goal of lifting)\n"
            "H¹  =  obstruction group  (must vanish for lifting to succeed)\n\n"
            "Procedure:\n"
            "  1. Assign local trust levels to each cover element.\n"
            "  2. Form the 0-cochain s ∈ C⁰(cover, T).\n"
            "  3. Compute δ(s) ∈ C¹.  If δ(s) = 0, s is a cocycle and\n"
            "     the lift exists uniquely (H¹ = 0 here).\n"
            "  4. If δ(s) ≠ 0, the cohomology class [δ(s)] ∈ H¹ is the\n"
            "     obstruction.  One must supply extra evidence to kill it.\n"
            f"  Cover: {self.cover}\n"
        )


# ---------------------------------------------------------------------------
# TrustObstructionMap
# ---------------------------------------------------------------------------


class TrustObstructionMap:
    """Map between trust-level data and cohomological obstruction theory.

    Theory2.tex §9.3.8 — Trust levels form a partially ordered set, and a
    sheaf of trust levels on a site assigns a trust level to each geometric
    object.  This class bridges the algebraic trust machinery in
    ``jugeo.evidence.trust`` with the cohomological framework above.

    Parameters
    ----------
    obstruction_computer:
        The ``CohomologicalObstructionComputer`` for the relevant site.
    oracle_ceiling:
        The maximum trust level attainable without solver/mechanically-
        verified evidence (default: ``TrustLevel.ORACLE_PROPOSED``).
    """

    def __init__(
        self,
        obstruction_computer: CohomologicalObstructionComputer,
        oracle_ceiling: Any | None = None,
    ) -> None:
        self.obstruction_computer = obstruction_computer
        if TrustLevel is not None:
            self.oracle_ceiling = oracle_ceiling or TrustLevel.ORACLE_PROPOSED
        else:
            self.oracle_ceiling = oracle_ceiling
        log.debug(
            "TrustObstructionMap: site=%r oracle_ceiling=%r",
            obstruction_computer.site_id,
            self.oracle_ceiling,
        )

    # ------------------------------------------------------------------
    def trust_to_cochain(self, trust_levels: dict[str, Any]) -> Cochain:
        """Convert a dict of trust assignments over a cover to a 0-cochain.

        Each key is a cover element name; each value is a ``TrustLevel``.
        The resulting ``Cochain`` has degree 0 and can be passed directly to
        ``CoboundaryCondition.apply`` or ``Cochain.coboundary``.
        """
        components: dict[str, Any] = {}
        for cover_elem, trust_val in trust_levels.items():
            if TrustLevel is not None and isinstance(trust_val, TrustLevel):
                components[cover_elem] = trust_val.value
            else:
                components[cover_elem] = str(trust_val)
        return Cochain(
            degree=0,
            site_id=self.obstruction_computer.site_id,
            sheaf_name=self.obstruction_computer.sheaf_name,
            components=components,
        )

    # ------------------------------------------------------------------
    def compute_trust_obstruction(
        self,
        cover: list[str],
        local_trust_data: dict[str, Any],
    ) -> ObstructionClass | None:
        """Compute the H¹ obstruction to gluing local trust data.

        Theory2.tex §9.3.8 — If the local trust levels are mutually
        compatible, the obstruction is ``None`` (H¹ = 0).  Otherwise the
        returned ``ObstructionClass`` records which overlaps are inconsistent
        and what extra evidence would resolve them.
        """
        # Normalise TrustLevel values to their string names for comparison.
        normalised: dict[str, Any] = {}
        for k, v in local_trust_data.items():
            if TrustLevel is not None and isinstance(v, TrustLevel):
                normalised[k] = v.value
            else:
                normalised[k] = v

        obstructions = self.obstruction_computer.compute_h1(cover, normalised)
        if not obstructions:
            return None
        return obstructions[0]

    # ------------------------------------------------------------------
    def lift_trust(
        self,
        obstruction: ObstructionClass,
        extra_evidence: dict,
    ) -> Any | None:
        """Try to lift past an obstruction using extra evidence.

        If the obstruction involves oracle-tier trust, require solver-backed
        evidence to lift above the oracle ceiling.  Otherwise apply the
        evidence and check whether the obstruction vanishes.

        Returns
        -------
        TrustLevel | None
            The lifted global trust level, or ``None`` if lifting is still
            blocked.
        """
        updated_obs = obstruction.add_evidence(extra_evidence)
        if not updated_obs.check_vanishing() and not updated_obs.vanishes:
            log.info(
                "lift_trust: obstruction %r not resolved by supplied evidence.",
                obstruction.class_id,
            )
            return None

        # Determine the best trust level from the extra_evidence.
        if TrustLevel is not None:
            candidate_levels = [
                v for v in extra_evidence.values() if isinstance(v, TrustLevel)
            ]
            if candidate_levels:
                best = max(
                    candidate_levels,
                    key=lambda tl: list(TrustLevel).index(tl),
                )
                # Enforce oracle ceiling unless solver/mechanically verified.
                if best > self.oracle_ceiling:
                    solver_tiers = {
                        TrustLevel.SOLVER_DISCHARGED,
                        TrustLevel.MECHANICALLY_VERIFIED,
                    }
                    if not any(tl in solver_tiers for tl in candidate_levels):
                        log.warning(
                            "lift_trust: attempting to exceed oracle ceiling "
                            "(%r > %r) without solver evidence; clamping.",
                            best,
                            self.oracle_ceiling,
                        )
                        return self.oracle_ceiling
                return best

        # Fallback: obstruction vanished but no explicit TrustLevel evidence.
        return self.oracle_ceiling

    # ------------------------------------------------------------------
    def explain_obstruction(self, obstruction: ObstructionClass) -> str:
        """Explain what the obstruction means in trust-theoretic terms."""
        if obstruction.vanishes:
            return (
                f"Obstruction {obstruction.class_id!r} is trivial: all local "
                "trust assignments are compatible and the global section exists."
            )
        rep = obstruction.representative_cochain
        conflicts: list[str] = []
        if rep is not None:
            for k, v in rep.components.items():
                if v != _ZERO_SENTINEL:
                    parts = k.split("|")
                    if len(parts) == 2:
                        conflicts.append(
                            f"  • Overlap {parts[0]!r} ∩ {parts[1]!r}: "
                            f"trust levels differ ({v!r})"
                        )
        conflict_str = "\n".join(conflicts) if conflicts else "  (no explicit conflicts recorded)"
        return (
            f"Obstruction {obstruction.class_id!r} in "
            f"H^{obstruction.degree}({obstruction.site_id!r}; "
            f"{obstruction.coefficient_sheaf!r}) is non-trivial.\n\n"
            "Conflicting overlaps:\n"
            f"{conflict_str}\n\n"
            "To clear this obstruction:\n"
            f"  {obstruction.vanishing_condition}\n\n"
            "Note: trust levels above the oracle ceiling "
            f"({self.oracle_ceiling!r}) require solver-discharged or "
            "mechanically-verified evidence."
        )


# ---------------------------------------------------------------------------
# DescentObstructionChecker
# ---------------------------------------------------------------------------


class DescentObstructionChecker:
    """High-level descent check with rich explanation output.

    Theory2.tex §9.3.9 — *Descent* is the property that compatible local data
    can always be glued to global data.  A site/sheaf pair satisfies descent
    (for a given cover) iff H¹ = 0.  This class wraps the obstruction
    machinery into a single ``check_descent`` call and provides guidance on
    what evidence is needed to restore descent when it fails.

    Parameters
    ----------
    site_id:
        Identifier of the site.
    trust_obstruction_map:
        The ``TrustObstructionMap`` to use for trust-level computations.
    """

    def __init__(
        self,
        site_id: str,
        trust_obstruction_map: TrustObstructionMap,
    ) -> None:
        self.site_id = site_id
        self.trust_obstruction_map = trust_obstruction_map
        log.debug("DescentObstructionChecker: site=%r", site_id)

    # ------------------------------------------------------------------
    def check_descent(
        self,
        cover: list[str],
        local_data: dict,
    ) -> dict:
        """Full descent check for the given cover and local data.

        Returns a dict with:

        - ``"descends"`` (bool): whether the local data descends to a global
          section;
        - ``"obstruction"`` (ObstructionClass | None): the obstruction class,
          or ``None`` if the lift is unobstructed;
        - ``"global_section"`` (dict | None): the global section if it exists;
        - ``"explanation"`` (str): human-readable summary.
        """
        computer = self.trust_obstruction_map.obstruction_computer
        obstructions = computer.compute_h1(cover, local_data)

        if not obstructions:
            global_sec = computer.lift_section(cover, local_data)
            explanation = (
                f"Descent holds on site {self.site_id!r}: H¹ = 0.  "
                "All local sections are compatible and lift to a unique "
                f"global section: {global_sec!r}."
            )
            log.info("check_descent: descent holds on site %r", self.site_id)
            return {
                "descends": True,
                "obstruction": None,
                "global_section": global_sec,
                "explanation": explanation,
            }

        obs = obstructions[0]
        explanation = self.trust_obstruction_map.explain_obstruction(obs)
        log.info(
            "check_descent: descent fails on site %r; obstruction %r",
            self.site_id,
            obs.class_id,
        )
        return {
            "descends": False,
            "obstruction": obs,
            "global_section": None,
            "explanation": explanation,
        }

    # ------------------------------------------------------------------
    def requires_extra_evidence(self, obstruction: ObstructionClass) -> list[str]:
        """Return a list of evidence types needed to clear *obstruction*.

        Theory2.tex §9.3.9 — Each non-zero component of the representative
        1-cocycle corresponds to an overlap where trust values disagree.  To
        resolve each disagreement one typically needs one of:

        - A solver certificate (``SOLVER_DISCHARGED``);
        - A mechanically-verified proof (``MECHANICALLY_VERIFIED``);
        - Human attestation (``HUMAN_ATTESTED``) for lower-stakes conflicts;
        - A runtime witness (``RUNTIME_WITNESSED``) for empirical claims.
        """
        if obstruction.vanishes or obstruction.representative_cochain is None:
            return []

        evidence_types: list[str] = []
        for k, v in obstruction.representative_cochain.components.items():
            if v == _ZERO_SENTINEL:
                continue
            parts = k.split("|")
            label = f"Overlap ({' ∩ '.join(parts)})" if len(parts) == 2 else k

            # Determine required evidence tier from the nature of the conflict.
            if TrustLevel is not None and isinstance(v, tuple) and len(v) >= 2:
                high_str = str(v[0]) if v[0] != _ZERO_SENTINEL else ""
                if "mechanically" in high_str or "solver" in high_str:
                    evidence_types.append(
                        f"{label}: MECHANICALLY_VERIFIED or SOLVER_DISCHARGED certificate"
                    )
                elif "oracle" in high_str or "human" in high_str:
                    evidence_types.append(
                        f"{label}: HUMAN_ATTESTED confirmation or ORACLE_PROPOSED review"
                    )
                else:
                    evidence_types.append(
                        f"{label}: RUNTIME_WITNESSED observation or HUMAN_ATTESTED review"
                    )
            else:
                evidence_types.append(
                    f"{label}: evidence resolving trust conflict {v!r}"
                )

        if not evidence_types:
            evidence_types.append(
                "General: additional trust evidence to resolve all pairwise conflicts."
            )
        return evidence_types

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Human-readable description of this checker."""
        comp = self.trust_obstruction_map.obstruction_computer
        return (
            f"DescentObstructionChecker\n"
            f"  Site          : {self.site_id!r}\n"
            f"  Sheaf         : {comp.sheaf_name!r}\n"
            f"  Default cover : {comp.cover}\n"
            f"  Oracle ceiling: {self.trust_obstruction_map.oracle_ceiling!r}\n"
            "─────────────────────────────────────────────────────────────\n"
            "Use check_descent(cover, local_data) to determine whether\n"
            "local trust assignments glue to a global section.  If not,\n"
            "requires_extra_evidence(obstruction) lists the certificates\n"
            "needed to clear the H¹ obstruction and enable the lift.\n"
        )
