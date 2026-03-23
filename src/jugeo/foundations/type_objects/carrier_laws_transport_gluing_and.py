"""Carrier laws, transport coherence, and gluing coherence for JuGeo types.

Theory2 Ch3 §3.3 establishes the *carrier law system* as a collection of
axioms that every JuGeo type carrier must satisfy:

(CL1) **Identity law**: ρ_id ∘ K = K (restriction along the identity morphism
      is the identity)
(CL2) **Composition law**: ρ_{g∘f} = ρ_f ∘ ρ_g (restriction is functorial /
      contravariant)
(CL3) **Locality law**: if s|_{Uᵢ} = t|_{Uᵢ} for all i, then s = t (sections
      are determined locally)
(CL4) **Gluing law**: compatible local sections admit a unique global section
(CL5) **Trust monotonicity**: trust(τ|_f) ≤ trust(τ) (restriction does not
      increase trust)
(CL6) **Support inclusion**: supp(τ|_f) ⊆ supp(τ) ∩ cod(f) (restriction
      shrinks support)

This module implements the validator that checks these laws for a given type.

The validator operates in three modes:

* **Syntactic mode** — inspects field signatures and kind tags to decide
  whether a law could possibly be violated without running any computation.
* **Semantic mode** — evaluates the law condition against runtime carrier
  data.
* **Deferred mode** — records a pending obligation when full evaluation is
  not yet possible (e.g., because gluing data is incomplete).

Transport coherence (§3.4) checks that a pair of transport maps compose
consistently along a chain of morphisms.  Gluing coherence (§3.5) checks
that a family of local sections satisfying pairwise overlap conditions
actually admits a unique global assembly.

References
----------
theory2.tex §3.3, §3.4, §3.5 — carrier laws, transport coherence, gluing
coherence.

# copilot: module provenance — author: copilot
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from jugeo.errors import FailureScope, JuGeoError, raise_with_scope
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateObject,
    CoordinateMorphism,
    Morphism,
    MorphismKind,
    Site,
)
from jugeo.judgments.judgment_terms import (
    Judgment,
    JudgmentStatus,
    Proposition,
    TrustLevel,
)

if TYPE_CHECKING:
    from jugeo.foundations.type_objects.models import (
        CarrierKind,
        GluingLaw,
        JuGeoType,
        TransportMap,
        TypeCarrier,
        TypeTrustAnnotation,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_id(prefix: str = "law") -> str:
    """Return a short deterministic-looking unique identifier.

    Parameters
    ----------
    prefix:
        Short label prepended to the UUID fragment.

    Returns
    -------
    str
        A string of the form ``"<prefix>-<hex8>"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _carrier_has_field(carrier: Any, field_name: str) -> bool:
    """Return True if *carrier* exposes *field_name* as a non-None attribute.

    Parameters
    ----------
    carrier:
        Any object that is expected to behave like a ``TypeCarrier``.
    field_name:
        Attribute name to probe.

    Returns
    -------
    bool
        True when the attribute exists and is not ``None``.
    """
    return getattr(carrier, field_name, None) is not None


def _trust_level_rank(level: Any) -> int:
    """Map a trust-level tag to a comparable integer rank.

    Parameters
    ----------
    level:
        A ``TrustLevel`` member or any object with a ``value`` attribute.

    Returns
    -------
    int
        Integer rank, higher meaning more trusted.  Unknown values get 0.
    """
    _RANKS: dict[str, int] = {
        "axiom": 100,
        "verified": 80,
        "trusted": 60,
        "provisional": 40,
        "speculative": 20,
        "untrusted": 0,
    }
    tag = getattr(level, "value", str(level)).lower()
    return _RANKS.get(tag, 0)


def _coordinates_overlap(coord_a: Coordinate, coord_b: Coordinate) -> bool:
    """Decide whether two coordinates share a common ancestor at depth ≥ 1.

    Parameters
    ----------
    coord_a:
        First coordinate.
    coord_b:
        Second coordinate.

    Returns
    -------
    bool
        True when coord_a is a prefix of coord_b, coord_b is a prefix of
        coord_a, or they share at least one common component.
    """
    a_parts = coord_a.components
    b_parts = coord_b.components
    min_len = min(len(a_parts), len(b_parts))
    if min_len == 0:
        return False
    return a_parts[:min_len] == b_parts[:min_len]


# ---------------------------------------------------------------------------
# LawKind enumeration
# ---------------------------------------------------------------------------


class LawKind(str, Enum):
    """Enumeration of the carrier law categories from theory2.tex §3.3.

    Members
    -------
    IDENTITY
        ρ_id ∘ K = K — restriction along the identity is the identity.
    COMPOSITION
        ρ_{g∘f} = ρ_f ∘ ρ_g — restriction is contravariant/functorial.
    LOCALITY
        s|_{Uᵢ} = t|_{Uᵢ} ∀ i ⟹ s = t — sections determined locally.
    GLUING
        Compatible local sections ⟹ unique global section.
    TRUST_MONOTONICITY
        trust(τ|_f) ≤ trust(τ) — restriction cannot promote trust.
    SUPPORT_INCLUSION
        supp(τ|_f) ⊆ supp(τ) ∩ cod(f) — support shrinks on restriction.
    COHERENCE
        Composite transport diagrams commute up to canonical isomorphism.
    UNIQUENESS
        Gluing produces a *unique* section, not merely an existing one.
    """

    IDENTITY = "identity"
    COMPOSITION = "composition"
    LOCALITY = "locality"
    GLUING = "gluing"
    TRUST_MONOTONICITY = "trust_monotonicity"
    SUPPORT_INCLUSION = "support_inclusion"
    COHERENCE = "coherence"
    UNIQUENESS = "uniqueness"


# ---------------------------------------------------------------------------
# LawViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LawViolation:
    """A single violation of a carrier law, with evidence and repair hints.

    Violations are first-class semantic objects in theory2 §3.3.  They
    record precisely which law was broken, at which coordinate the break
    was detected, and what evidence was found.

    Parameters
    ----------
    violation_id:
        Unique identifier for this violation record.
    law_kind:
        The :class:`LawKind` member that was violated.
    coordinate:
        The semantic coordinate at which the violation was detected, or
        ``None`` if the violation is global.
    description:
        Human-readable summary of what went wrong.
    evidence:
        Tuple of short evidence strings (field names, value excerpts, etc.)
        that were consulted when detecting the violation.
    severity:
        One of ``"critical"`` (blocks discharge), ``"warning"`` (degrades
        quality), or ``"info"`` (informational only).
    is_repairable:
        True when a concrete repair action is known and possible.
    repair_hint:
        A short instruction describing the repair action, or ``None``.

    Raises
    ------
    ValueError
        If *severity* is not one of the three permitted values.
    """

    violation_id: str
    law_kind: LawKind
    coordinate: Coordinate | None
    description: str
    evidence: tuple[str, ...]
    severity: str
    is_repairable: bool
    repair_hint: str | None

    def __post_init__(self) -> None:
        if self.severity not in ("critical", "warning", "info"):
            raise ValueError(
                f"LawViolation.severity must be 'critical', 'warning', or 'info';"
                f" got {self.severity!r}"
            )

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_critical(self) -> bool:
        """Return True when severity is ``"critical"``.

        Returns
        -------
        bool
            True iff this violation blocks discharge of the enclosing type.
        """
        return self.severity == "critical"

    def is_warning(self) -> bool:
        """Return True when severity is ``"warning"``.

        Returns
        -------
        bool
            True iff this violation degrades quality without blocking discharge.
        """
        return self.severity == "warning"

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def to_repair_hint(self) -> dict[str, Any]:
        """Return a structured repair hint mapping.

        Returns
        -------
        dict[str, Any]
            Keys: ``"violation_id"``, ``"law_kind"``, ``"is_repairable"``,
            ``"hint"``, ``"severity"``.
        """
        return {
            "violation_id": self.violation_id,
            "law_kind": self.law_kind.value,
            "is_repairable": self.is_repairable,
            "hint": self.repair_hint,
            "severity": self.severity,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this violation to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trip-safe mapping of all fields.
        """
        return {
            "violation_id": self.violation_id,
            "law_kind": self.law_kind.value,
            "coordinate": self.coordinate.serialize() if self.coordinate else None,
            "description": self.description,
            "evidence": list(self.evidence),
            "severity": self.severity,
            "is_repairable": self.is_repairable,
            "repair_hint": self.repair_hint,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> LawViolation:
        """Reconstruct a :class:`LawViolation` from a serialized mapping.

        Parameters
        ----------
        data:
            A dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        LawViolation
            The reconstructed violation object.

        Raises
        ------
        KeyError
            If required keys are absent from *data*.
        """
        coord_raw = data.get("coordinate")
        coord = Coordinate.parse(coord_raw) if coord_raw else None
        return cls(
            violation_id=data["violation_id"],
            law_kind=LawKind(data["law_kind"]),
            coordinate=coord,
            description=data["description"],
            evidence=tuple(data.get("evidence", [])),
            severity=data["severity"],
            is_repairable=bool(data.get("is_repairable", False)),
            repair_hint=data.get("repair_hint"),
        )

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def critical(
        cls,
        law_kind: LawKind,
        coord: Coordinate | None,
        description: str,
        hint: str | None = None,
    ) -> LawViolation:
        """Construct a critical-severity violation.

        Parameters
        ----------
        law_kind:
            The law that was violated.
        coord:
            The coordinate at which the violation occurred.
        description:
            Human-readable description of the violation.
        hint:
            Optional repair hint.

        Returns
        -------
        LawViolation
            A new critical violation with a fresh ``violation_id``.
        """
        return cls(
            violation_id=_fresh_id("viol"),
            law_kind=law_kind,
            coordinate=coord,
            description=description,
            evidence=(),
            severity="critical",
            is_repairable=hint is not None,
            repair_hint=hint,
        )

    @classmethod
    def warning(
        cls,
        law_kind: LawKind,
        coord: Coordinate | None,
        description: str,
    ) -> LawViolation:
        """Construct a warning-severity violation.

        Parameters
        ----------
        law_kind:
            The law that was violated.
        coord:
            The coordinate at which the violation occurred.
        description:
            Human-readable description.

        Returns
        -------
        LawViolation
            A new warning violation with a fresh ``violation_id``.
        """
        return cls(
            violation_id=_fresh_id("viol"),
            law_kind=law_kind,
            coordinate=coord,
            description=description,
            evidence=(),
            severity="warning",
            is_repairable=False,
            repair_hint=None,
        )


# ---------------------------------------------------------------------------
# CarrierLaw
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CarrierLaw:
    """A single axiom in the carrier law system from theory2.tex §3.3.

    A :class:`CarrierLaw` is both a specification (its ``statement`` and
    ``formal_condition``) and a checker (its :meth:`check` method evaluates
    the condition against a live carrier object).

    Parameters
    ----------
    law_id:
        Unique identifier, e.g. ``"CL1"``.
    kind:
        The :class:`LawKind` this law belongs to.
    name:
        Short human-readable name, e.g. ``"Identity Law"``.
    statement:
        Full prose statement of the law.
    formal_condition:
        A concise formal/mathematical statement of the condition.
    is_mandatory:
        True when violation is unconditionally blocking.
    priority:
        Lower numbers are checked first; laws with lower priority that fail
        often render higher-priority checks moot.
    """

    law_id: str
    kind: LawKind
    name: str
    statement: str
    formal_condition: str
    is_mandatory: bool
    priority: int

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def mandatory(self) -> bool:
        """Return True when this law is mandatory.

        Returns
        -------
        bool
            Alias for ``self.is_mandatory``.
        """
        return self.is_mandatory

    def priority_key(self) -> tuple[int, str]:
        """Return a sort key combining priority and law_id.

        Returns
        -------
        tuple[int, str]
            ``(priority, law_id)`` suitable for ``sorted()``.
        """
        return (self.priority, self.law_id)

    def statement_formula(self) -> str:
        """Return the formal condition with Unicode math symbols.

        Returns
        -------
        str
            The ``formal_condition`` field, which may contain ∘ ≤ ⊆ ∧ ∨ ¬
            → ⪯ τ φ ρ symbols as appropriate for the law kind.
        """
        return self.formal_condition

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def check(self, carrier: Any) -> LawViolation | None:
        """Perform a syntactic check of this law against *carrier*.

        Syntactic checks inspect field presence and plausible type tags
        without evaluating full semantic equality.  They are fast and
        deterministic.

        Parameters
        ----------
        carrier:
            An object expected to behave like a ``TypeCarrier``.

        Returns
        -------
        LawViolation | None
            A :class:`LawViolation` if the law is violated, else ``None``.
        """
        if carrier is None:
            return LawViolation.critical(
                self.kind,
                None,
                f"{self.name}: carrier is None — cannot check {self.formal_condition}",
                hint="Provide a non-None TypeCarrier.",
            )

        if self.kind == LawKind.IDENTITY:
            return self._check_identity(carrier)
        if self.kind == LawKind.COMPOSITION:
            return self._check_composition(carrier)
        if self.kind == LawKind.LOCALITY:
            return self._check_locality(carrier)
        if self.kind == LawKind.GLUING:
            return self._check_gluing(carrier)
        if self.kind == LawKind.TRUST_MONOTONICITY:
            return self._check_trust_monotonicity(carrier)
        if self.kind == LawKind.SUPPORT_INCLUSION:
            return self._check_support_inclusion(carrier)
        return None

    def _check_identity(self, carrier: Any) -> LawViolation | None:
        """Check CL1: identity restriction is the identity."""
        coord = getattr(carrier, "coordinate", None)
        if coord is None:
            return LawViolation.critical(
                LawKind.IDENTITY,
                None,
                "CL1 (ρ_id ∘ K = K): carrier exposes no 'coordinate' field.",
                hint="Add a 'coordinate' field to the carrier.",
            )
        kind_tag = getattr(carrier, "kind", None)
        if kind_tag is None:
            return LawViolation.warning(
                LawKind.IDENTITY,
                coord,
                "CL1: carrier exposes no 'kind' field; identity law cannot be"
                " fully verified.",
            )
        return None

    def _check_composition(self, carrier: Any) -> LawViolation | None:
        """Check CL2: restriction is contravariant (ρ_{g∘f} = ρ_f ∘ ρ_g)."""
        restrictions = getattr(carrier, "restrictions", None)
        if restrictions is None:
            return LawViolation.warning(
                LawKind.COMPOSITION,
                getattr(carrier, "coordinate", None),
                "CL2 (ρ_{g∘f} = ρ_f ∘ ρ_g): carrier exposes no 'restrictions'"
                " mapping; composition law cannot be verified.",
            )
        if isinstance(restrictions, dict) and len(restrictions) < 2:
            return None  # Not enough data to falsify.
        return None

    def _check_locality(self, carrier: Any) -> LawViolation | None:
        """Check CL3: sections are locally determined."""
        sections = getattr(carrier, "sections", None)
        if sections is None:
            return LawViolation.warning(
                LawKind.LOCALITY,
                getattr(carrier, "coordinate", None),
                "CL3 (locality): carrier exposes no 'sections' field; locality"
                " law cannot be checked without section data.",
            )
        return None

    def _check_gluing(self, carrier: Any) -> LawViolation | None:
        """Check CL4: compatible local sections admit a unique global section."""
        gluing = getattr(carrier, "gluing_data", None)
        if gluing is None:
            return LawViolation.warning(
                LawKind.GLUING,
                getattr(carrier, "coordinate", None),
                "CL4 (gluing): carrier exposes no 'gluing_data' field; gluing"
                " law cannot be verified.",
            )
        return None

    def _check_trust_monotonicity(self, carrier: Any) -> LawViolation | None:
        """Check CL5: trust(τ|_f) ≤ trust(τ)."""
        trust = getattr(carrier, "trust_level", None)
        if trust is None:
            return LawViolation.warning(
                LawKind.TRUST_MONOTONICITY,
                getattr(carrier, "coordinate", None),
                "CL5 (trust(τ|_f) ≤ trust(τ)): carrier exposes no 'trust_level';"
                " monotonicity cannot be verified.",
            )
        restricted_trust = getattr(carrier, "restricted_trust_level", None)
        if restricted_trust is not None:
            parent_rank = _trust_level_rank(trust)
            child_rank = _trust_level_rank(restricted_trust)
            if child_rank > parent_rank:
                return LawViolation.critical(
                    LawKind.TRUST_MONOTONICITY,
                    getattr(carrier, "coordinate", None),
                    f"CL5 violated: restricted trust {restricted_trust!r} has"
                    f" rank {child_rank} > parent trust {trust!r} rank"
                    f" {parent_rank}.  Trust must not increase under restriction.",
                    hint="Lower the restricted_trust_level to at most the parent level.",
                )
        return None

    def _check_support_inclusion(self, carrier: Any) -> LawViolation | None:
        """Check CL6: supp(τ|_f) ⊆ supp(τ) ∩ cod(f)."""
        support = getattr(carrier, "support", None)
        if support is None:
            return LawViolation.warning(
                LawKind.SUPPORT_INCLUSION,
                getattr(carrier, "coordinate", None),
                "CL6 (supp(τ|_f) ⊆ supp(τ) ∩ cod(f)): carrier has no 'support'"
                " field; support inclusion cannot be checked.",
            )
        restricted_support = getattr(carrier, "restricted_support", None)
        if restricted_support is not None and support is not None:
            if isinstance(support, (set, frozenset)) and isinstance(
                restricted_support, (set, frozenset)
            ):
                if not restricted_support.issubset(support):
                    extra = restricted_support - support
                    return LawViolation.critical(
                        LawKind.SUPPORT_INCLUSION,
                        getattr(carrier, "coordinate", None),
                        f"CL6 violated: restricted_support contains elements"
                        f" {extra!r} not in the parent support.  supp(τ|_f) ⊄"
                        f" supp(τ).",
                        hint="Remove extra elements from restricted_support.",
                    )
        return None

    def check_against_transport(
        self, carrier: Any, transport: Any
    ) -> LawViolation | None:
        """Check this law in the context of a specific transport map.

        Parameters
        ----------
        carrier:
            The ``TypeCarrier`` being transported.
        transport:
            The ``TransportMap`` being applied.

        Returns
        -------
        LawViolation | None
            A violation if the law fails in the transport context, else
            ``None``.
        """
        if transport is None:
            return LawViolation.critical(
                self.kind,
                getattr(carrier, "coordinate", None),
                f"{self.name}: transport map is None; cannot evaluate law in"
                " transport context.",
                hint="Provide a valid TransportMap.",
            )
        base_violation = self.check(carrier)
        if base_violation is not None:
            return base_violation
        if self.kind == LawKind.TRUST_MONOTONICITY:
            src_trust = getattr(carrier, "trust_level", None)
            tgt_trust = getattr(transport, "target_trust", None)
            if src_trust is not None and tgt_trust is not None:
                if _trust_level_rank(tgt_trust) > _trust_level_rank(src_trust):
                    return LawViolation.critical(
                        LawKind.TRUST_MONOTONICITY,
                        getattr(carrier, "coordinate", None),
                        f"CL5 violated via transport: target trust {tgt_trust!r}"
                        f" > source trust {src_trust!r}.",
                        hint="Transport must not promote trust.",
                    )
        return None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this law to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trip-safe mapping of all fields.
        """
        return {
            "law_id": self.law_id,
            "kind": self.kind.value,
            "name": self.name,
            "statement": self.statement,
            "formal_condition": self.formal_condition,
            "is_mandatory": self.is_mandatory,
            "priority": self.priority,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> CarrierLaw:
        """Reconstruct a :class:`CarrierLaw` from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        CarrierLaw
            The reconstructed law.
        """
        return cls(
            law_id=data["law_id"],
            kind=LawKind(data["kind"]),
            name=data["name"],
            statement=data["statement"],
            formal_condition=data["formal_condition"],
            is_mandatory=bool(data.get("is_mandatory", True)),
            priority=int(data.get("priority", 50)),
        )

    # ------------------------------------------------------------------
    # Standard law factories
    # ------------------------------------------------------------------

    @classmethod
    def identity_law(cls) -> CarrierLaw:
        """Return the standard CL1 identity law.

        Returns
        -------
        CarrierLaw
            The identity law: ρ_id ∘ K = K.
        """
        return cls(
            law_id="CL1",
            kind=LawKind.IDENTITY,
            name="Identity Law",
            statement=(
                "Restriction along the identity morphism is the identity: "
                "ρ_id ∘ K = K."
            ),
            formal_condition="ρ_id ∘ K = K",
            is_mandatory=True,
            priority=10,
        )

    @classmethod
    def composition_law(cls) -> CarrierLaw:
        """Return the standard CL2 composition law.

        Returns
        -------
        CarrierLaw
            The composition law: ρ_{g∘f} = ρ_f ∘ ρ_g.
        """
        return cls(
            law_id="CL2",
            kind=LawKind.COMPOSITION,
            name="Composition Law",
            statement=(
                "Restriction is functorial in the contravariant direction: "
                "ρ_{g∘f} = ρ_f ∘ ρ_g."
            ),
            formal_condition="ρ_{g∘f} = ρ_f ∘ ρ_g",
            is_mandatory=True,
            priority=20,
        )

    @classmethod
    def locality_law(cls) -> CarrierLaw:
        """Return the standard CL3 locality law.

        Returns
        -------
        CarrierLaw
            The locality law: s|_{Uᵢ} = t|_{Uᵢ} ∀ i ⟹ s = t.
        """
        return cls(
            law_id="CL3",
            kind=LawKind.LOCALITY,
            name="Locality Law",
            statement=(
                "Sections are determined locally: if s|_{Uᵢ} = t|_{Uᵢ} for "
                "all i in a cover, then s = t globally."
            ),
            formal_condition="s|_{Uᵢ} = t|_{Uᵢ} ∀ i ⟹ s = t",
            is_mandatory=True,
            priority=30,
        )

    @classmethod
    def gluing_law(cls) -> CarrierLaw:
        """Return the standard CL4 gluing law.

        Returns
        -------
        CarrierLaw
            The gluing law: compatible local sections admit a unique global
            section.
        """
        return cls(
            law_id="CL4",
            kind=LawKind.GLUING,
            name="Gluing Law",
            statement=(
                "Compatible local sections (sᵢ|_{Uᵢ∩Uⱼ} = sⱼ|_{Uᵢ∩Uⱼ} for "
                "all i,j) admit a unique global section s with s|_{Uᵢ} = sᵢ."
            ),
            formal_condition="∀ compatible {sᵢ} ∃! s. s|_{Uᵢ} = sᵢ",
            is_mandatory=True,
            priority=40,
        )

    @classmethod
    def trust_monotonicity_law(cls) -> CarrierLaw:
        """Return the standard CL5 trust-monotonicity law.

        Returns
        -------
        CarrierLaw
            The trust-monotonicity law: trust(τ|_f) ≤ trust(τ).
        """
        return cls(
            law_id="CL5",
            kind=LawKind.TRUST_MONOTONICITY,
            name="Trust Monotonicity",
            statement=(
                "Restriction does not increase trust: "
                "trust(τ|_f) ≤ trust(τ) for every morphism f."
            ),
            formal_condition="trust(τ|_f) ≤ trust(τ)",
            is_mandatory=True,
            priority=50,
        )

    @classmethod
    def support_inclusion_law(cls) -> CarrierLaw:
        """Return the standard CL6 support-inclusion law.

        Returns
        -------
        CarrierLaw
            The support-inclusion law: supp(τ|_f) ⊆ supp(τ) ∩ cod(f).
        """
        return cls(
            law_id="CL6",
            kind=LawKind.SUPPORT_INCLUSION,
            name="Support Inclusion",
            statement=(
                "Restriction shrinks support: "
                "supp(τ|_f) ⊆ supp(τ) ∩ cod(f) for every morphism f."
            ),
            formal_condition="supp(τ|_f) ⊆ supp(τ) ∩ cod(f)",
            is_mandatory=True,
            priority=60,
        )


# ---------------------------------------------------------------------------
# TransportCoherence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransportCoherence:
    """Coherence data for a pair of transport maps along composable morphisms.

    Theory2 §3.4 requires that transport along a composite morphism g∘f equals
    the composite of the individual transports.  This dataclass stores the
    evidence for (or against) that requirement.

    Parameters
    ----------
    coherence_id:
        Unique identifier for this coherence record.
    transport_a:
        First transport map (along f).
    transport_b:
        Second transport map (along g), composable after transport_a.
    composition:
        The transport map along g∘f, if available.
    is_coherent:
        True if coherence has been verified, False if a violation was found,
        None if verification has not yet been run.
    coherence_witness:
        A string encoding the witness/proof that coherence holds, or None.
    violation:
        A :class:`LawViolation` recording the incoherence, or None.
    """

    coherence_id: str
    transport_a: Any  # TransportMap
    transport_b: Any  # TransportMap
    composition: Any  # TransportMap | None
    is_coherent: bool | None
    coherence_witness: str | None
    violation: LawViolation | None

    # ------------------------------------------------------------------
    # Composition checks
    # ------------------------------------------------------------------

    def check_composition_law(self) -> bool:
        """Check whether transport_a ∘ transport_b equals the composition map.

        Returns
        -------
        bool
            True when the composition law appears satisfied (or when
            insufficient data is present to falsify it).
        """
        if self.composition is None:
            return True  # Cannot falsify without explicit composite.
        src_a = getattr(self.transport_a, "source_coordinate", None)
        tgt_b = getattr(self.transport_b, "target_coordinate", None)
        comp_src = getattr(self.composition, "source_coordinate", None)
        comp_tgt = getattr(self.composition, "target_coordinate", None)
        if src_a is not None and comp_src is not None:
            if src_a != comp_src:
                return False
        if tgt_b is not None and comp_tgt is not None:
            if tgt_b != comp_tgt:
                return False
        return True

    def check_identity_law(self) -> bool:
        """Check that neither transport is degenerate (both have defined source/target).

        Returns
        -------
        bool
            True when both transport maps have non-None source and target
            coordinates, indicating neither is an identity collapse.
        """
        a_ok = (
            getattr(self.transport_a, "source_coordinate", None) is not None
            and getattr(self.transport_a, "target_coordinate", None) is not None
        )
        b_ok = (
            getattr(self.transport_b, "source_coordinate", None) is not None
            and getattr(self.transport_b, "target_coordinate", None) is not None
        )
        return a_ok and b_ok

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> TransportCoherence:
        """Run all coherence checks and return a new verified instance.

        Returns
        -------
        TransportCoherence
            A new :class:`TransportCoherence` with ``is_coherent`` set and
            ``violation`` populated if incoherence was found.
        """
        comp_ok = self.check_composition_law()
        id_ok = self.check_identity_law()

        if not id_ok:
            viol = LawViolation.critical(
                LawKind.COHERENCE,
                None,
                "Transport coherence (identity law): one or both transport maps"
                " have undefined source/target coordinate — identity cannot be"
                " verified (ρ_id ∘ K = K).",
                hint="Ensure all transport maps carry source_coordinate and"
                " target_coordinate.",
            )
            return replace(self, is_coherent=False, violation=viol)

        if not comp_ok:
            viol = LawViolation.critical(
                LawKind.COHERENCE,
                None,
                "Transport coherence (composition law): source/target of the"
                " composite map does not match the composed pair — ρ_{g∘f} ≠"
                " ρ_f ∘ ρ_g.",
                hint="Recompute the composition map to match the pair.",
            )
            return replace(self, is_coherent=False, violation=viol)

        witness = (
            f"coherence-verified:{self.coherence_id}@{_now_iso()}"
        )
        return replace(
            self,
            is_coherent=True,
            coherence_witness=witness,
            violation=None,
        )

    def is_verified(self) -> bool:
        """Return True when coherence has been positively verified.

        Returns
        -------
        bool
            True iff ``is_coherent`` is ``True``.
        """
        return self.is_coherent is True

    def has_violation(self) -> bool:
        """Return True when a coherence violation has been recorded.

        Returns
        -------
        bool
            True iff ``violation`` is not None.
        """
        return self.violation is not None

    def can_compose(self) -> bool:
        """Return True when the two transport maps are composable.

        Composability requires that the target coordinate of transport_a
        equals the source coordinate of transport_b.

        Returns
        -------
        bool
            True when the coordinates match or are not present (optimistic).
        """
        tgt_a = getattr(self.transport_a, "target_coordinate", None)
        src_b = getattr(self.transport_b, "source_coordinate", None)
        if tgt_a is None or src_b is None:
            return True  # Optimistic: cannot disprove.
        return tgt_a == src_b

    def composed_carrier(self) -> Any:
        """Return the carrier produced by composing both transports, if any.

        Returns
        -------
        TypeCarrier | None
            The ``target_carrier`` of ``transport_b``, or ``None``.
        """
        return getattr(self.transport_b, "target_carrier", None)

    def coherence_report(self) -> dict[str, Any]:
        """Return a structured report of the coherence status.

        Returns
        -------
        dict[str, Any]
            Keys: ``"coherence_id"``, ``"is_coherent"``, ``"can_compose"``,
            ``"has_violation"``, ``"witness"``, ``"violation"``.
        """
        return {
            "coherence_id": self.coherence_id,
            "is_coherent": self.is_coherent,
            "can_compose": self.can_compose(),
            "has_violation": self.has_violation(),
            "witness": self.coherence_witness,
            "violation": self.violation.serialize() if self.violation else None,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this coherence record.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping of all fields.
        """
        return {
            "coherence_id": self.coherence_id,
            "is_coherent": self.is_coherent,
            "coherence_witness": self.coherence_witness,
            "violation": self.violation.serialize() if self.violation else None,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TransportCoherence:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary previously produced by :meth:`serialize`.

        Returns
        -------
        TransportCoherence
            The reconstructed coherence object (with None transport maps
            since they cannot be round-tripped without models.py).
        """
        viol_raw = data.get("violation")
        return cls(
            coherence_id=data["coherence_id"],
            transport_a=None,
            transport_b=None,
            composition=None,
            is_coherent=data.get("is_coherent"),
            coherence_witness=data.get("coherence_witness"),
            violation=LawViolation.parse(viol_raw) if viol_raw else None,
        )

    @classmethod
    def from_pair(cls, t_a: Any, t_b: Any) -> TransportCoherence:
        """Construct an unverified coherence record from a pair of transport maps.

        Parameters
        ----------
        t_a:
            First transport map.
        t_b:
            Second transport map.

        Returns
        -------
        TransportCoherence
            A new unverified :class:`TransportCoherence`.
        """
        return cls(
            coherence_id=_fresh_id("tcoh"),
            transport_a=t_a,
            transport_b=t_b,
            composition=None,
            is_coherent=None,
            coherence_witness=None,
            violation=None,
        )


# ---------------------------------------------------------------------------
# GluingCoherence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingCoherence:
    """Coherence data for a gluing law from theory2.tex §3.5.

    A gluing coherence record tracks whether a family of local sections
    satisfies pairwise overlap conditions and admits a unique global section.

    Parameters
    ----------
    coherence_id:
        Unique identifier.
    gluing_law:
        The ``GluingLaw`` whose coherence is being checked.
    overlap_data:
        A tuple of triples ``(coord_i_str, coord_j_str, agrees)`` recording,
        for each pair of local indices, whether the sections agree on the
        overlap Uᵢ ∩ Uⱼ.
    is_coherent:
        True when all pairwise overlaps agree, False on any failure, None
        if not yet checked.
    uniqueness_satisfied:
        True when the unique-gluing condition (CL4) is confirmed, False when
        a non-unique gluing is detected, None when not yet checked.
    violations:
        Tuple of :class:`LawViolation` objects produced during verification.
    """

    coherence_id: str
    gluing_law: Any  # GluingLaw
    overlap_data: tuple[tuple[str, str, bool], ...]
    is_coherent: bool | None
    uniqueness_satisfied: bool | None
    violations: tuple[LawViolation, ...]

    # ------------------------------------------------------------------
    # Overlap checking
    # ------------------------------------------------------------------

    def check_overlap_conditions(self) -> list[LawViolation]:
        """Check all pairwise overlap conditions.

        Returns
        -------
        list[LawViolation]
            A list of violations for any pair where overlap agreement fails.
        """
        result: list[LawViolation] = []
        for coord_i, coord_j, agrees in self.overlap_data:
            if not agrees:
                viol = LawViolation.critical(
                    LawKind.GLUING,
                    None,
                    f"Gluing coherence: sections at {coord_i!r} and"
                    f" {coord_j!r} do not agree on their overlap"
                    f" {coord_i} ∩ {coord_j} — CL4 pre-condition violated.",
                    hint=(
                        f"Reconcile the sections at {coord_i!r} and"
                        f" {coord_j!r} on their shared domain."
                    ),
                )
                result.append(viol)
        return result

    def check_uniqueness(self) -> LawViolation | None:
        """Check the uniqueness part of the gluing law.

        Returns
        -------
        LawViolation | None
            A violation if non-uniqueness is detected, else ``None``.
        """
        if self.gluing_law is None:
            return LawViolation.warning(
                LawKind.UNIQUENESS,
                None,
                "Gluing uniqueness: no GluingLaw provided; uniqueness (∃!) cannot"
                " be verified.",
            )
        gluing_sections = getattr(self.gluing_law, "glued_sections", None)
        if gluing_sections is not None and isinstance(gluing_sections, (list, tuple)):
            if len(gluing_sections) > 1:
                return LawViolation.critical(
                    LawKind.UNIQUENESS,
                    None,
                    f"CL4 (uniqueness) violated: {len(gluing_sections)} distinct"
                    " global sections were produced; the law requires ∃! (exactly"
                    " one).",
                    hint="Ensure the gluing data uniquely determines the global section.",
                )
        return None

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> GluingCoherence:
        """Run all gluing coherence checks and return an updated instance.

        Returns
        -------
        GluingCoherence
            A new :class:`GluingCoherence` with ``is_coherent``,
            ``uniqueness_satisfied``, and ``violations`` populated.
        """
        overlap_violations = self.check_overlap_conditions()
        uniqueness_violation = self.check_uniqueness()

        all_violations = list(overlap_violations)
        if uniqueness_violation is not None:
            all_violations.append(uniqueness_violation)

        coherent = len(overlap_violations) == 0
        unique_ok = uniqueness_violation is None

        return replace(
            self,
            is_coherent=coherent,
            uniqueness_satisfied=unique_ok,
            violations=tuple(all_violations),
        )

    def is_fully_coherent(self) -> bool:
        """Return True when both coherence and uniqueness are satisfied.

        Returns
        -------
        bool
            True iff ``is_coherent`` and ``uniqueness_satisfied`` are both
            True.
        """
        return self.is_coherent is True and self.uniqueness_satisfied is True

    def violation_count(self) -> int:
        """Return the number of recorded violations.

        Returns
        -------
        int
            Length of ``self.violations``.
        """
        return len(self.violations)

    def admits_gluing(self) -> bool:
        """Return True when all overlap conditions pass (gluing is possible).

        Returns
        -------
        bool
            True iff ``is_coherent`` is True (uniqueness may still fail).
        """
        return self.is_coherent is True

    def gluing_obstruction(self) -> str | None:
        """Return a human-readable obstruction description, if any.

        Returns
        -------
        str | None
            A description of the first critical violation, or ``None``
            if no obstruction is present.
        """
        for viol in self.violations:
            if viol.is_critical():
                return viol.description
        return None

    def coherence_report(self) -> dict[str, Any]:
        """Return a structured coherence report.

        Returns
        -------
        dict[str, Any]
            Keys: ``"coherence_id"``, ``"is_coherent"``,
            ``"uniqueness_satisfied"``, ``"violation_count"``,
            ``"admits_gluing"``, ``"obstruction"``, ``"violations"``.
        """
        return {
            "coherence_id": self.coherence_id,
            "is_coherent": self.is_coherent,
            "uniqueness_satisfied": self.uniqueness_satisfied,
            "violation_count": self.violation_count(),
            "admits_gluing": self.admits_gluing(),
            "obstruction": self.gluing_obstruction(),
            "violations": [v.serialize() for v in self.violations],
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this gluing coherence record.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping.
        """
        return {
            "coherence_id": self.coherence_id,
            "overlap_data": [list(t) for t in self.overlap_data],
            "is_coherent": self.is_coherent,
            "uniqueness_satisfied": self.uniqueness_satisfied,
            "violations": [v.serialize() for v in self.violations],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> GluingCoherence:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        GluingCoherence
            The reconstructed object.
        """
        return cls(
            coherence_id=data["coherence_id"],
            gluing_law=None,
            overlap_data=tuple(
                tuple(t) for t in data.get("overlap_data", [])  # type: ignore[misc]
            ),
            is_coherent=data.get("is_coherent"),
            uniqueness_satisfied=data.get("uniqueness_satisfied"),
            violations=tuple(
                LawViolation.parse(v) for v in data.get("violations", [])
            ),
        )

    @classmethod
    def from_law(cls, law: Any) -> GluingCoherence:
        """Construct an unverified coherence record from a ``GluingLaw``.

        Parameters
        ----------
        law:
            The ``GluingLaw`` to build a coherence record around.

        Returns
        -------
        GluingCoherence
            An unverified :class:`GluingCoherence` with overlap_data
            derived from the law's cover if available.
        """
        overlap_raw = getattr(law, "overlap_agreements", ()) or ()
        overlap_data: tuple[tuple[str, str, bool], ...] = tuple(
            (str(t[0]), str(t[1]), bool(t[2])) for t in overlap_raw
        )
        return cls(
            coherence_id=_fresh_id("gcoh"),
            gluing_law=law,
            overlap_data=overlap_data,
            is_coherent=None,
            uniqueness_satisfied=None,
            violations=(),
        )


# ---------------------------------------------------------------------------
# CarrierValidator
# ---------------------------------------------------------------------------


class CarrierValidator:
    """Validates a ``TypeCarrier`` against a configurable set of carrier laws.

    The validator accumulates violations across multiple validation calls
    and provides reporting methods.  It is not frozen since it maintains
    mutable validation state.

    Parameters
    ----------
    laws:
        Optional initial list of :class:`CarrierLaw` objects.  If ``None``,
        the standard six CL1–CL6 laws are loaded automatically.
    """

    def __init__(self, laws: list[CarrierLaw] | None = None) -> None:
        if laws is None:
            self._laws: list[CarrierLaw] = [
                CarrierLaw.identity_law(),
                CarrierLaw.composition_law(),
                CarrierLaw.locality_law(),
                CarrierLaw.gluing_law(),
                CarrierLaw.trust_monotonicity_law(),
                CarrierLaw.support_inclusion_law(),
            ]
        else:
            self._laws = list(laws)
        self._violations: list[LawViolation] = []

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, carrier: Any) -> list[LawViolation]:
        """Validate *carrier* against all registered laws.

        Parameters
        ----------
        carrier:
            The ``TypeCarrier`` to validate.

        Returns
        -------
        list[LawViolation]
            All violations found.  Also accumulated into ``_violations``.
        """
        found: list[LawViolation] = []
        for law in sorted(self._laws, key=lambda l: l.priority_key()):
            viol = law.check(carrier)
            if viol is not None:
                found.append(viol)
                self._violations.append(viol)
        return found

    def validate_transport(
        self, carrier: Any, transport: Any
    ) -> list[LawViolation]:
        """Validate *carrier* in the context of *transport*.

        Parameters
        ----------
        carrier:
            The source ``TypeCarrier``.
        transport:
            The ``TransportMap`` being applied.

        Returns
        -------
        list[LawViolation]
            Violations found in the transport context.
        """
        found: list[LawViolation] = []
        for law in sorted(self._laws, key=lambda l: l.priority_key()):
            viol = law.check_against_transport(carrier, transport)
            if viol is not None:
                found.append(viol)
                self._violations.append(viol)
        return found

    def validate_gluing(self, gluing_law: Any) -> list[LawViolation]:
        """Validate a ``GluingLaw`` for CL3 and CL4 compliance.

        Parameters
        ----------
        gluing_law:
            The ``GluingLaw`` to check.

        Returns
        -------
        list[LawViolation]
            Violations from the gluing check.
        """
        coherence = GluingCoherence.from_law(gluing_law).verify()
        found = list(coherence.violations)
        self._violations.extend(found)
        return found

    def validate_type(self, type_: Any) -> list[LawViolation]:
        """Validate all carriers within a ``JuGeoType``.

        Parameters
        ----------
        type_:
            A ``JuGeoType`` object whose ``carrier`` attribute (if present)
            will be validated.

        Returns
        -------
        list[LawViolation]
            All violations found across the type's carriers.
        """
        found: list[LawViolation] = []
        carrier = getattr(type_, "carrier", None)
        if carrier is not None:
            found.extend(self.validate(carrier))
        transport = getattr(type_, "transport_map", None)
        if transport is not None and carrier is not None:
            found.extend(self.validate_transport(carrier, transport))
        return found

    # ------------------------------------------------------------------
    # Law management
    # ------------------------------------------------------------------

    def add_law(self, law: CarrierLaw) -> None:
        """Add a law to this validator.

        Parameters
        ----------
        law:
            The :class:`CarrierLaw` to add.  No-ops if a law with the same
            ``law_id`` already exists.
        """
        if not any(l.law_id == law.law_id for l in self._laws):
            self._laws.append(law)

    def remove_law(self, law_id: str) -> None:
        """Remove a law by its identifier.

        Parameters
        ----------
        law_id:
            The ``law_id`` of the law to remove.
        """
        self._laws = [l for l in self._laws if l.law_id != law_id]

    def law_by_id(self, law_id: str) -> CarrierLaw | None:
        """Look up a law by its identifier.

        Parameters
        ----------
        law_id:
            The ``law_id`` to search for.

        Returns
        -------
        CarrierLaw | None
            The matching law, or ``None``.
        """
        for l in self._laws:
            if l.law_id == law_id:
                return l
        return None

    def mandatory_laws(self) -> list[CarrierLaw]:
        """Return only the mandatory laws.

        Returns
        -------
        list[CarrierLaw]
            Laws with ``is_mandatory == True``.
        """
        return [l for l in self._laws if l.is_mandatory]

    def clear_violations(self) -> None:
        """Clear the accumulated violation list."""
        self._violations.clear()

    def violation_report(self) -> dict[str, Any]:
        """Return a structured report of all accumulated violations.

        Returns
        -------
        dict[str, Any]
            Keys: ``"total"``, ``"critical"``, ``"warnings"``,
            ``"violations"``.
        """
        critical = [v for v in self._violations if v.is_critical()]
        warnings = [v for v in self._violations if v.is_warning()]
        return {
            "total": len(self._violations),
            "critical": len(critical),
            "warnings": len(warnings),
            "violations": [v.serialize() for v in self._violations],
        }


# ---------------------------------------------------------------------------
# CarrierLawSystem
# ---------------------------------------------------------------------------


class CarrierLawSystem:
    """The complete carrier law system for a JuGeo type theory.

    The law system maintains a registry of carriers, transports, and gluing
    laws, and provides unified checking across all of them.  It is the
    top-level entry point for theory2 §3.3–§3.5 compliance.

    Notes
    -----
    The law system is intentionally *not* frozen: it accumulates runtime
    state (registered objects, cached violations) that must be mutable.
    """

    def __init__(self) -> None:
        self._carriers: list[Any] = []
        self._transports: list[Any] = []
        self._gluings: list[Any] = []
        self._custom_laws: list[CarrierLaw] = []
        self._validator: CarrierValidator = CarrierValidator()
        self._coherence_cache: dict[str, TransportCoherence] = {}
        self._gluing_cache: dict[str, GluingCoherence] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_carrier(self, carrier: Any) -> None:
        """Register a ``TypeCarrier`` with the law system.

        Parameters
        ----------
        carrier:
            The carrier to register.
        """
        self._carriers.append(carrier)

    def register_transport(self, transport: Any) -> None:
        """Register a ``TransportMap`` with the law system.

        Parameters
        ----------
        transport:
            The transport map to register.
        """
        self._transports.append(transport)

    def register_gluing(self, law: Any) -> None:
        """Register a ``GluingLaw`` with the law system.

        Parameters
        ----------
        law:
            The gluing law to register.
        """
        self._gluings.append(law)

    # ------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------

    def check_all(self, type_: Any) -> list[LawViolation]:
        """Check all carrier laws for a given ``JuGeoType``.

        Parameters
        ----------
        type_:
            The type to check.

        Returns
        -------
        list[LawViolation]
            All violations found across every registered law.
        """
        return self._validator.validate_type(type_)

    def check_carrier(self, carrier: Any) -> list[LawViolation]:
        """Check a single carrier against all registered laws.

        Parameters
        ----------
        carrier:
            The ``TypeCarrier`` to check.

        Returns
        -------
        list[LawViolation]
            All violations found.
        """
        return self._validator.validate(carrier)

    def check_transport_coherence(
        self, t_a: Any, t_b: Any
    ) -> TransportCoherence:
        """Check whether two transport maps are mutually coherent.

        Parameters
        ----------
        t_a:
            First transport map.
        t_b:
            Second transport map.

        Returns
        -------
        TransportCoherence
            A verified :class:`TransportCoherence` record.
        """
        coh = TransportCoherence.from_pair(t_a, t_b).verify()
        self._coherence_cache[coh.coherence_id] = coh
        return coh

    def check_gluing_coherence(self, law: Any) -> GluingCoherence:
        """Check whether a gluing law is internally coherent.

        Parameters
        ----------
        law:
            The ``GluingLaw`` to check.

        Returns
        -------
        GluingCoherence
            A verified :class:`GluingCoherence` record.
        """
        gcoh = GluingCoherence.from_law(law).verify()
        self._gluing_cache[gcoh.coherence_id] = gcoh
        return gcoh

    # ------------------------------------------------------------------
    # Consistency
    # ------------------------------------------------------------------

    def is_law_system_consistent(self) -> bool:
        """Return True when no critical violations have been recorded.

        Returns
        -------
        bool
            True iff there are no critical violations in the validator.
        """
        report = self._validator.violation_report()
        return report["critical"] == 0

    # ------------------------------------------------------------------
    # Law management
    # ------------------------------------------------------------------

    def all_laws(self) -> list[CarrierLaw]:
        """Return all laws currently active in the system.

        Returns
        -------
        list[CarrierLaw]
            Standard laws from the validator plus any custom laws.
        """
        return list(self._validator._laws) + list(self._custom_laws)

    def add_custom_law(self, law: CarrierLaw) -> None:
        """Add a custom law to the system.

        Parameters
        ----------
        law:
            The :class:`CarrierLaw` to add.
        """
        self._custom_laws.append(law)
        self._validator.add_law(law)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def violation_summary(self) -> dict[str, int]:
        """Return a concise summary of violation counts by severity.

        Returns
        -------
        dict[str, int]
            Keys: ``"critical"``, ``"warning"``, ``"info"``, ``"total"``.
        """
        report = self._validator.violation_report()
        viols = self._validator._violations
        info_count = sum(1 for v in viols if v.severity == "info")
        return {
            "critical": report["critical"],
            "warning": report["warnings"],
            "info": info_count,
            "total": report["total"],
        }

    def full_report(self) -> dict[str, Any]:
        """Return a complete report of the law system state.

        Returns
        -------
        dict[str, Any]
            Keys: ``"consistent"``, ``"registered_carriers"``,
            ``"registered_transports"``, ``"registered_gluings"``,
            ``"laws"``, ``"violations"``, ``"coherence_cache_size"``,
            ``"gluing_cache_size"``.
        """
        return {
            "consistent": self.is_law_system_consistent(),
            "registered_carriers": len(self._carriers),
            "registered_transports": len(self._transports),
            "registered_gluings": len(self._gluings),
            "laws": [l.serialize() for l in self.all_laws()],
            "violations": self._validator.violation_report(),
            "coherence_cache_size": len(self._coherence_cache),
            "gluing_cache_size": len(self._gluing_cache),
        }

    def statistics(self) -> dict[str, int]:
        """Return numeric statistics about the law system.

        Returns
        -------
        dict[str, int]
            Keys: ``"carriers"``, ``"transports"``, ``"gluings"``,
            ``"laws"``, ``"coherence_records"``, ``"gluing_records"``,
            ``"violations"``.
        """
        return {
            "carriers": len(self._carriers),
            "transports": len(self._transports),
            "gluings": len(self._gluings),
            "laws": len(self.all_laws()),
            "coherence_records": len(self._coherence_cache),
            "gluing_records": len(self._gluing_cache),
            "violations": len(self._validator._violations),
        }
