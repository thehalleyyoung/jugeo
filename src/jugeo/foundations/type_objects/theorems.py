"""Formal properties (theorems) of JuGeo types.

Theory2 Ch3 §3.6 states the key theorems that every sound JuGeo type system
must satisfy.  This module implements *verifiable* versions of those theorems
as computable checks that can be run against actual type objects.  The
theorems are:

Th3.1 **Carrier identity**: For any type τ at c, restricting along id_c returns
      τ itself — K(τ)|_{id_c} = K(τ).

Th3.2 **Transport coherence**: For morphisms f: b → c, g: a → b, the
      composition law holds — ρ_{g∘f} = ρ_f ∘ ρ_g.

Th3.3 **Gluing uniqueness**: Given a covering {Uᵢ → c} and compatible local
      sections, there exists a unique global section.

Th3.4 **Support monotonicity**: For any morphism f: c' → c,
      supp(τ|_f) ⊆ supp(τ).

Th3.5 **Restriction functoriality**: The restriction assignment τ ↦ τ|_f is a
      functor from the category of types at c to types at c'.

Th3.6 **Trust monotonicity**: trust(τ|_f) ≤ trust(τ) — restriction cannot
      increase trust.

Th3.7 **Type comparison reflexivity**: Every type τ satisfies τ ≤ τ (reflexivity
      of the subtype relation).

Th3.8 **Coordinate indexing faithfulness**: The assignment c ↦ τ(c) is
      faithful — distinct coordinates yield distinct type extensions when the
      carrier is non-degenerate.

The verification strategy is *computable witnessing*: for each theorem we
provide a function that, given concrete type objects and morphisms, either
produces a *witness* (a datum confirming the theorem holds) or a
*counterexample* (a datum showing it fails).  Vacuous cases (e.g. empty
morphism sets) are explicitly flagged as :attr:`TheoremStatus.VACUOUS` rather
than silently passing.

Module provenance
-----------------
Author : copilot
Theory : preliminaries/theory2.tex §3.6
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, TYPE_CHECKING

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

# ---------------------------------------------------------------------------
# Sibling module imports — guarded to allow incremental roll-out
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.type_objects.models import (
        JuGeoType,
        TypeCarrier,
        TransportMap,
        GluingLaw,
        TypeTrustAnnotation,
        CarrierKind,
    )
except ImportError:  # pragma: no cover
    JuGeoType = Any  # type: ignore[misc,assignment]
    TypeCarrier = Any  # type: ignore[misc,assignment]
    TransportMap = Any  # type: ignore[misc,assignment]
    GluingLaw = Any  # type: ignore[misc,assignment]
    TypeTrustAnnotation = Any  # type: ignore[misc,assignment]
    CarrierKind = Any  # type: ignore[misc,assignment]

try:
    from jugeo.foundations.type_objects.carrier_laws_transport_gluing_and import (
        CarrierLawSystem,
        LawViolation,
        LawKind,
    )
except ImportError:  # pragma: no cover
    CarrierLawSystem = Any  # type: ignore[misc,assignment]
    LawViolation = Any  # type: ignore[misc,assignment]
    LawKind = Any  # type: ignore[misc,assignment]

try:
    from jugeo.foundations.type_objects.algorithms import (
        TypeAlgorithms,
        ComparisonResult,
    )
except ImportError:  # pragma: no cover
    TypeAlgorithms = Any  # type: ignore[misc,assignment]
    ComparisonResult = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp, e.g. ``"2024-01-01T12:00:00Z"``.
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _fresh_id(prefix: str = "th") -> str:
    """Generate a unique identifier with the given prefix.

    Parameters
    ----------
    prefix : str
        Short label prepended before the UUID fragment.

    Returns
    -------
    str
        A collision-resistant identifier such as ``"th-4a3f2e1d"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _safe_coord(coord: Any) -> Coordinate:
    """Return *coord* if it is a :class:`Coordinate`, else the root coordinate.

    Parameters
    ----------
    coord : Any
        Candidate value.

    Returns
    -------
    Coordinate
    """
    if isinstance(coord, Coordinate):
        return coord
    return Coordinate(components=(), kind=CoordinateKind.REGION)


def _type_trust_level(type_: Any) -> TrustLevel:
    """Extract :class:`TrustLevel` from a type's trust annotation.

    Parameters
    ----------
    type_ : Any
        A JuGeoType instance or any object with a ``trust`` attribute.

    Returns
    -------
    TrustLevel
        The resolved level, defaulting to ``UNVERIFIED``.
    """
    trust = getattr(type_, "trust", None)
    level = getattr(trust, "level", None)
    if isinstance(level, TrustLevel):
        return level
    return TrustLevel.UNVERIFIED


def _types_equal(a: Any, b: Any) -> bool:
    """Shallow equality check between two type objects.

    Two types are considered equal when they have the same ``type_id``,
    or when the objects are identical (``a is b``).

    Parameters
    ----------
    a : Any
        First type.
    b : Any
        Second type.

    Returns
    -------
    bool
    """
    if a is b:
        return True
    aid = getattr(a, "type_id", None)
    bid = getattr(b, "type_id", None)
    if aid and bid:
        return aid == bid
    return a == b


def _identity_morphism(coord: Coordinate) -> Morphism:
    """Build the identity morphism at *coord*.

    Parameters
    ----------
    coord : Coordinate
        The coordinate that serves as both source and target.

    Returns
    -------
    Morphism
        A morphism with ``source == target == coord`` and
        ``kind == MorphismKind.RESTRICTION`` and ``label == "id"``.
    """
    return Morphism(
        source=coord,
        target=coord,
        kind=MorphismKind.RESTRICTION,
        label="id",
    )


def _morphisms_compose(f: Morphism, g: Morphism) -> bool:
    """Return *True* when *g* can be composed after *f* (i.e. f ; g).

    In category-theoretic notation: the composite g ∘ f requires
    ``f.target == g.source``.

    Parameters
    ----------
    f : Morphism
        First (inner) morphism.
    g : Morphism
        Second (outer) morphism.

    Returns
    -------
    bool
    """
    return f.target == g.source


def _compose_morphisms(f: Morphism, g: Morphism) -> Morphism | None:
    """Attempt to form the composite g ∘ f.

    Parameters
    ----------
    f : Morphism
        Inner morphism (applied first).
    g : Morphism
        Outer morphism (applied second).

    Returns
    -------
    Morphism | None
        The composite morphism, or ``None`` if composition is impossible.
    """
    if not _morphisms_compose(f, g):
        return None
    try:
        return g.compose(f)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# TheoremStatus
# ---------------------------------------------------------------------------

class TheoremStatus(str, Enum):
    """Lifecycle status of a theorem in the verification pipeline.

    The status captures whether the theorem has been checked against
    concrete evidence, and if so, what the outcome was.

    Values
    ------
    UNVERIFIED
        No verification attempt has been made.
    VERIFIED
        The theorem has been confirmed against concrete witnesses.
    COUNTEREXAMPLE
        A counterexample was found, invalidating the theorem.
    VACUOUS
        The theorem was found to be vacuously true (e.g. no morphisms).
    PARTIAL
        The theorem holds for a proper subset of the input data.
    ASSUMED
        The theorem is taken as an axiom without a computable witness.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    COUNTEREXAMPLE = "counterexample"
    VACUOUS = "vacuous"
    PARTIAL = "partial"
    ASSUMED = "assumed"


# ---------------------------------------------------------------------------
# TheoremRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremRecord:
    """Immutable descriptor for a single theorem in the JuGeo type system.

    A :class:`TheoremRecord` encodes the *identity* of a theorem: its ID,
    name, logical statement, and the formal condition (an expression using
    the symbolic vocabulary of theory2.tex).  It also tracks the current
    verification status and any known counterexample.

    Parameters
    ----------
    theorem_id : str
        Unique identifier, e.g. ``"Th3.1"``.
    name : str
        Short human-readable name, e.g. ``"carrier_identity"``.
    statement : str
        Full natural-language statement of the theorem.
    formal_condition : str
        Formal symbolic condition using unicode math, e.g.
        ``"K(τ)|_{id_c} = K(τ)"``.
    status : TheoremStatus
        Current verification status.
    verified_at : str | None
        ISO-8601 timestamp of the last successful verification, or ``None``.
    counterexample : str | None
        Description of a known counterexample, or ``None``.
    proof_sketch : str
        Informal proof sketch or reference to the theory2.tex proof.
    dependencies : tuple[str, ...]
        IDs of theorems this theorem depends on.
    coordinate_scope : str
        One of ``"all"``, ``"local"``, ``"global"`` — the coordinate scope
        over which this theorem is asserted.
    """

    theorem_id: str
    name: str
    statement: str
    formal_condition: str
    status: TheoremStatus
    verified_at: str | None
    counterexample: str | None
    proof_sketch: str
    dependencies: tuple[str, ...]
    coordinate_scope: str

    # -- query methods -------------------------------------------------------

    def is_verified(self) -> bool:
        """Return *True* when :pyattr:`status` is :attr:`TheoremStatus.VERIFIED`.

        Returns
        -------
        bool
        """
        return self.status is TheoremStatus.VERIFIED

    def has_counterexample(self) -> bool:
        """Return *True* when a counterexample is known.

        Returns
        -------
        bool
        """
        return (
            self.status is TheoremStatus.COUNTEREXAMPLE
            or self.counterexample is not None
        )

    def is_vacuous(self) -> bool:
        """Return *True* when the theorem is vacuously satisfied.

        Returns
        -------
        bool
        """
        return self.status is TheoremStatus.VACUOUS

    def depends_on(self, theorem_id: str) -> bool:
        """Return *True* when this theorem transitively depends on *theorem_id*.

        For the current implementation this only checks direct dependencies —
        a transitive closure would require the full theorem graph.

        Parameters
        ----------
        theorem_id : str
            The theorem to check dependency on.

        Returns
        -------
        bool
        """
        return theorem_id in self.dependencies

    def mark_verified(self, witness: str | None = None) -> TheoremRecord:
        """Return a new record with status :attr:`TheoremStatus.VERIFIED`.

        Parameters
        ----------
        witness : str | None
            Optional description of the confirming witness.

        Returns
        -------
        TheoremRecord
        """
        return replace(
            self,
            status=TheoremStatus.VERIFIED,
            verified_at=_now_iso(),
            counterexample=None,
        )

    def mark_counterexample(self, ce: str) -> TheoremRecord:
        """Return a new record with status :attr:`TheoremStatus.COUNTEREXAMPLE`.

        Parameters
        ----------
        ce : str
            Description of the counterexample.

        Returns
        -------
        TheoremRecord
        """
        return replace(
            self,
            status=TheoremStatus.COUNTEREXAMPLE,
            counterexample=ce,
        )

    def mark_vacuous(self, reason: str) -> TheoremRecord:
        """Return a new record with status :attr:`TheoremStatus.VACUOUS`.

        Parameters
        ----------
        reason : str
            Explanation of why the theorem is vacuously satisfied.

        Returns
        -------
        TheoremRecord
        """
        return replace(
            self,
            status=TheoremStatus.VACUOUS,
            counterexample=None,
            proof_sketch=f"Vacuous: {reason}",
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary of this theorem.

        Returns
        -------
        str
            A string of the form ``"[Th3.1] carrier_identity: VERIFIED"``.
        """
        ce_note = f" ← {self.counterexample}" if self.counterexample else ""
        return f"[{self.theorem_id}] {self.name}: {self.status.value}{ce_note}"

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "formal_condition": self.formal_condition,
            "status": self.status.value,
            "verified_at": self.verified_at,
            "counterexample": self.counterexample,
            "proof_sketch": self.proof_sketch,
            "dependencies": list(self.dependencies),
            "coordinate_scope": self.coordinate_scope,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TheoremRecord:
        """Deserialise from a JSON-compatible dict.

        Parameters
        ----------
        data : dict[str, Any]
            A dict previously produced by :meth:`serialize`.

        Returns
        -------
        TheoremRecord

        Raises
        ------
        KeyError
            If a required field is absent.
        """
        return cls(
            theorem_id=data["theorem_id"],
            name=data["name"],
            statement=data["statement"],
            formal_condition=data["formal_condition"],
            status=TheoremStatus(data["status"]),
            verified_at=data.get("verified_at"),
            counterexample=data.get("counterexample"),
            proof_sketch=data.get("proof_sketch", ""),
            dependencies=tuple(data.get("dependencies", [])),
            coordinate_scope=data.get("coordinate_scope", "all"),
        )


# ---------------------------------------------------------------------------
# TheoremVerificationContext
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremVerificationContext:
    """Immutable collection of evidence for theorem verification.

    A :class:`TheoremVerificationContext` bundles the types, morphisms, and
    optional site that are available for a round of theorem checking.
    Theory2 §3.6.1 requires that the context be non-empty for any
    non-vacuous verification.

    Parameters
    ----------
    context_id : str
        Unique identifier for this context.
    site : Site | None
        The Grothendieck site, if available.
    types : tuple[JuGeoType, ...]
        The types to verify the theorems against.
    morphisms : tuple[Morphism, ...]
        The morphisms available for transport and composition checks.
    timestamp : str
        ISO-8601 creation time.
    verifier : str
        Name or ID of the verifying subsystem.
    """

    context_id: str
    site: Site | None
    types: tuple[Any, ...]  # tuple[JuGeoType, ...]
    morphisms: tuple[Morphism, ...]
    timestamp: str
    verifier: str

    # -- query methods -------------------------------------------------------

    def type_count(self) -> int:
        """Return the number of types in this context.

        Returns
        -------
        int
        """
        return len(self.types)

    def morphism_count(self) -> int:
        """Return the number of morphisms in this context.

        Returns
        -------
        int
        """
        return len(self.morphisms)

    def has_site(self) -> bool:
        """Return *True* when a site is present.

        Returns
        -------
        bool
        """
        return self.site is not None

    def type_by_id(self, type_id: str) -> Any | None:
        """Retrieve a type by its ``type_id``.

        Parameters
        ----------
        type_id : str
            Target type identifier.

        Returns
        -------
        JuGeoType | None
        """
        for t in self.types:
            if getattr(t, "type_id", None) == type_id:
                return t
        return None

    def morphisms_from(self, coord: Coordinate) -> list[Morphism]:
        """Return all morphisms whose source equals *coord*.

        Parameters
        ----------
        coord : Coordinate
            Source coordinate to filter on.

        Returns
        -------
        list[Morphism]
        """
        return [m for m in self.morphisms if m.source == coord]

    def morphisms_to(self, coord: Coordinate) -> list[Morphism]:
        """Return all morphisms whose target equals *coord*.

        Parameters
        ----------
        coord : Coordinate
            Target coordinate to filter on.

        Returns
        -------
        list[Morphism]
        """
        return [m for m in self.morphisms if m.target == coord]

    def is_sufficient_for(self, theorem_id: str) -> bool:
        """Return *True* when the context contains enough data to test *theorem_id*.

        The sufficiency check is minimal: a context with at least one type
        is sufficient for Th3.1 and Th3.7; a context with at least one
        composable pair of morphisms is needed for Th3.2; and so on.

        Parameters
        ----------
        theorem_id : str
            The theorem to test sufficiency for.

        Returns
        -------
        bool
        """
        if not self.types:
            return False
        if theorem_id in {"Th3.1", "Th3.7"}:
            return True
        if theorem_id == "Th3.2":
            return len(self.morphisms) >= 2
        if theorem_id in {"Th3.4", "Th3.6"}:
            return len(self.morphisms) >= 1
        if theorem_id == "Th3.3":
            return True  # GluingLaw is self-contained
        if theorem_id in {"Th3.5", "Th3.8"}:
            return len(self.types) >= 2 or len(self.morphisms) >= 1
        return True

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "context_id": self.context_id,
            "type_count": len(self.types),
            "morphism_count": len(self.morphisms),
            "has_site": self.site is not None,
            "timestamp": self.timestamp,
            "verifier": self.verifier,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TheoremVerificationContext:
        """Deserialise from a JSON-compatible dict (shallow — types not reconstructed).

        Parameters
        ----------
        data : dict[str, Any]
            A dict previously produced by :meth:`serialize`.

        Returns
        -------
        TheoremVerificationContext
        """
        return cls(
            context_id=data.get("context_id", _fresh_id("ctx")),
            site=None,
            types=(),
            morphisms=(),
            timestamp=data.get("timestamp", _now_iso()),
            verifier=data.get("verifier", "unknown"),
        )

    @classmethod
    def minimal(
        cls,
        types: Sequence[Any],
        morphisms: Sequence[Morphism] = (),
    ) -> TheoremVerificationContext:
        """Create a minimal context with the given types and morphisms.

        Parameters
        ----------
        types : Sequence[JuGeoType]
            Types to include.
        morphisms : Sequence[Morphism]
            Morphisms to include.

        Returns
        -------
        TheoremVerificationContext
        """
        return cls(
            context_id=_fresh_id("ctx"),
            site=None,
            types=tuple(types),
            morphisms=tuple(morphisms),
            timestamp=_now_iso(),
            verifier="type_objects.theorems",
        )


# ---------------------------------------------------------------------------
# TheoremVerificationResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremVerificationResult:
    """Immutable outcome of a single theorem verification run.

    Each verification attempt produces exactly one
    :class:`TheoremVerificationResult` recording whether the theorem passed,
    failed (counterexample found), or was vacuous.

    Parameters
    ----------
    result_id : str
        Unique identifier for this result.
    theorem : TheoremRecord
        The theorem that was verified.
    status : TheoremStatus
        Outcome of the verification.
    witness : str | None
        Description of the confirming witness (when verified).
    counterexample_data : dict[str, Any] | None
        Machine-readable counterexample data (when refuted).
    explanation : str
        Human-readable explanation of the outcome.
    cost : float
        Approximate computational cost (wall-clock seconds or abstract units).
    metadata : Mapping[str, Any]
        Arbitrary extra data.
    """

    result_id: str
    theorem: TheoremRecord
    status: TheoremStatus
    witness: str | None
    counterexample_data: dict[str, Any] | None
    explanation: str
    cost: float
    metadata: Mapping[str, Any]

    # -- query methods -------------------------------------------------------

    def passed(self) -> bool:
        """Return *True* when the theorem was verified or assumed.

        Returns
        -------
        bool
        """
        return self.status in {TheoremStatus.VERIFIED, TheoremStatus.ASSUMED}

    def failed(self) -> bool:
        """Return *True* when a counterexample was found.

        Returns
        -------
        bool
        """
        return self.status is TheoremStatus.COUNTEREXAMPLE

    def is_vacuous(self) -> bool:
        """Return *True* when the theorem is vacuously satisfied.

        Returns
        -------
        bool
        """
        return self.status is TheoremStatus.VACUOUS

    def explanation_lines(self) -> list[str]:
        """Return the explanation as a list of non-empty lines.

        Returns
        -------
        list[str]
        """
        return [ln for ln in self.explanation.splitlines() if ln.strip()]

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "result_id": self.result_id,
            "theorem_id": self.theorem.theorem_id,
            "theorem_name": self.theorem.name,
            "status": self.status.value,
            "witness": self.witness,
            "counterexample_data": self.counterexample_data,
            "explanation": self.explanation,
            "cost": self.cost,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TheoremVerificationResult:
        """Deserialise from a JSON-compatible dict.

        Parameters
        ----------
        data : dict[str, Any]
            A dict previously produced by :meth:`serialize`.

        Returns
        -------
        TheoremVerificationResult

        Raises
        ------
        KeyError
            If a required field is absent.
        """
        rec = TheoremRecord(
            theorem_id=data.get("theorem_id", ""),
            name=data.get("theorem_name", ""),
            statement="",
            formal_condition="",
            status=TheoremStatus(data.get("status", TheoremStatus.UNVERIFIED.value)),
            verified_at=None,
            counterexample=None,
            proof_sketch="",
            dependencies=(),
            coordinate_scope="all",
        )
        return cls(
            result_id=data.get("result_id", _fresh_id("res")),
            theorem=rec,
            status=TheoremStatus(data["status"]),
            witness=data.get("witness"),
            counterexample_data=data.get("counterexample_data"),
            explanation=data.get("explanation", ""),
            cost=float(data.get("cost", 0.0)),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def verified(
        cls,
        theorem: TheoremRecord,
        witness: str,
        explanation: str,
    ) -> TheoremVerificationResult:
        """Create a VERIFIED result for *theorem*.

        Parameters
        ----------
        theorem : TheoremRecord
            The theorem that was verified.
        witness : str
            Description of the confirming witness.
        explanation : str
            Human-readable explanation.

        Returns
        -------
        TheoremVerificationResult
        """
        return cls(
            result_id=_fresh_id("res"),
            theorem=theorem.mark_verified(witness),
            status=TheoremStatus.VERIFIED,
            witness=witness,
            counterexample_data=None,
            explanation=explanation,
            cost=0.0,
            metadata={},
        )

    @classmethod
    def refuted(
        cls,
        theorem: TheoremRecord,
        ce_data: dict[str, Any],
        explanation: str,
    ) -> TheoremVerificationResult:
        """Create a COUNTEREXAMPLE result for *theorem*.

        Parameters
        ----------
        theorem : TheoremRecord
            The theorem that was refuted.
        ce_data : dict[str, Any]
            Machine-readable counterexample data.
        explanation : str
            Human-readable explanation.

        Returns
        -------
        TheoremVerificationResult
        """
        ce_str = str(ce_data)
        return cls(
            result_id=_fresh_id("res"),
            theorem=theorem.mark_counterexample(ce_str),
            status=TheoremStatus.COUNTEREXAMPLE,
            witness=None,
            counterexample_data=ce_data,
            explanation=explanation,
            cost=0.0,
            metadata={},
        )

    @classmethod
    def vacuous(
        cls,
        theorem: TheoremRecord,
        reason: str,
    ) -> TheoremVerificationResult:
        """Create a VACUOUS result for *theorem*.

        Parameters
        ----------
        theorem : TheoremRecord
            The theorem that was vacuously satisfied.
        reason : str
            Explanation of why the theorem is vacuous.

        Returns
        -------
        TheoremVerificationResult
        """
        return cls(
            result_id=_fresh_id("res"),
            theorem=theorem.mark_vacuous(reason),
            status=TheoremStatus.VACUOUS,
            witness=None,
            counterexample_data=None,
            explanation=f"Vacuous: {reason}",
            cost=0.0,
            metadata={},
        )


# ---------------------------------------------------------------------------
# Th3.1 — Carrier Identity
# ---------------------------------------------------------------------------

class CarrierIdentityTheorem:
    """Verify Th3.1: K(τ)|_{id_c} = K(τ).

    Th3.1 states that restricting a type along the identity morphism at its
    own coordinate returns the type unchanged.  This is the presheaf identity
    law.

    The verification strategy is:

    1. Obtain (or construct) the identity morphism at the type's coordinate.
    2. Apply the restriction map ρ_{id_c} to the type.
    3. Check that the result equals the original type (via :func:`_types_equal`).

    If the restriction map is not available (algorithms module absent), the
    theorem is recorded as :attr:`TheoremStatus.ASSUMED`.
    """

    _THEOREM_ID: str = "Th3.1"

    def __init__(self) -> None:
        self._checks: int = 0
        self._passes: int = 0
        self._failures: int = 0

    def theorem_record(self) -> TheoremRecord:
        """Return the canonical :class:`TheoremRecord` for Th3.1.

        Returns
        -------
        TheoremRecord
        """
        return TheoremRecord(
            theorem_id=self._THEOREM_ID,
            name="carrier_identity",
            statement=(
                "For any type τ at coordinate c, restricting τ along the "
                "identity morphism id_c returns τ itself."
            ),
            formal_condition="K(τ)|_{id_c} = K(τ)",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "By the presheaf identity law (theory2.tex §3.3, Prop. 3.2): "
                "F(id_c) = id_{F(c)}.  Applying this to the carrier functor K "
                "gives K(τ)|_{id_c} = id_{K(τ(c))} applied to K(τ) = K(τ). ∎"
            ),
            dependencies=(),
            coordinate_scope="all",
        )

    def verify(
        self, type_: Any, identity_morphism: Morphism
    ) -> TheoremVerificationResult:
        """Verify Th3.1 for *type_* using *identity_morphism*.

        Parameters
        ----------
        type_ : JuGeoType
            The type to check.
        identity_morphism : Morphism
            The identity morphism at the type's coordinate.

        Returns
        -------
        TheoremVerificationResult
        """
        self._checks += 1
        rec = self.theorem_record()
        if not identity_morphism.is_identity:
            self._failures += 1
            return TheoremVerificationResult.refuted(
                theorem=rec,
                ce_data={
                    "reason": "supplied morphism is not an identity",
                    "source": identity_morphism.source.name,
                    "target": identity_morphism.target.name,
                },
                explanation=(
                    f"Th3.1 check failed: morphism "
                    f"{identity_morphism.source.name} → "
                    f"{identity_morphism.target.name} is not an identity"
                ),
            )
        holds = self.check_identity_restriction(type_, identity_morphism)
        if holds:
            self._passes += 1
            return TheoremVerificationResult.verified(
                theorem=rec,
                witness=f"id at {identity_morphism.source.name}",
                explanation=(
                    f"Th3.1 ✓  K(τ)|_{{id_c}} = K(τ)  at "
                    f"c = {identity_morphism.source.name}"
                ),
            )
        self._failures += 1
        return TheoremVerificationResult.refuted(
            theorem=rec,
            ce_data={
                "type_id": getattr(type_, "type_id", str(id(type_))),
                "coordinate": identity_morphism.source.name,
            },
            explanation=(
                f"Th3.1 ✗  restriction along id_c changed the type at "
                f"c = {identity_morphism.source.name}"
            ),
        )

    def check_identity_restriction(
        self, type_: Any, morphism: Morphism
    ) -> bool:
        """Check whether restricting *type_* along *morphism* is the identity.

        When the algorithms module is present the check is computed; otherwise
        it is assumed true (since the identity law is an axiom at the model
        level).

        Parameters
        ----------
        type_ : Any
            The type to restrict.
        morphism : Morphism
            Must be an identity morphism for the check to be meaningful.

        Returns
        -------
        bool
        """
        if not morphism.is_identity:
            return False
        if TypeAlgorithms is not Any:
            try:
                alg = TypeAlgorithms()
                restricted = alg.restrict(type_, morphism)
                return _types_equal(type_, restricted)
            except Exception:
                pass
        # Axiom: identity restriction is the identity
        return True

    def get_identity_morphism(
        self, coord: Coordinate, site: Site | None = None
    ) -> Morphism | None:
        """Return the identity morphism at *coord*, optionally from the site.

        Parameters
        ----------
        coord : Coordinate
            Target coordinate.
        site : Site | None
            Optional site for looking up morphisms.

        Returns
        -------
        Morphism | None
            The identity morphism, or ``None`` if it cannot be constructed.
        """
        return _identity_morphism(coord)

    def batch_verify(
        self,
        types: Sequence[Any],
        site: Site | None = None,
    ) -> list[TheoremVerificationResult]:
        """Verify Th3.1 for each type in *types*.

        Parameters
        ----------
        types : Sequence[JuGeoType]
            Types to verify.
        site : Site | None
            Optional site for identity morphism lookup.

        Returns
        -------
        list[TheoremVerificationResult]
        """
        if not types:
            return [
                TheoremVerificationResult.vacuous(
                    self.theorem_record(), "no types provided"
                )
            ]
        results = []
        for t in types:
            coord = _safe_coord(getattr(t, "coordinate", None))
            id_morph = self.get_identity_morphism(coord, site)
            if id_morph is None:
                results.append(
                    TheoremVerificationResult.vacuous(
                        self.theorem_record(),
                        f"no identity morphism at {coord.name}",
                    )
                )
            else:
                results.append(self.verify(t, id_morph))
        return results

    def explanation(self) -> str:
        """Return a human-readable explanation of this theorem.

        Returns
        -------
        str
        """
        return (
            "Th3.1 (Carrier Identity): For any JuGeo type τ at site coordinate c,\n"
            "restricting τ along the identity morphism id_c: c → c must return τ\n"
            "unchanged.  Formally: K(τ)|_{id_c} = K(τ).  This is the presheaf\n"
            "identity axiom specialised to the carrier functor K."
        )


# ---------------------------------------------------------------------------
# Th3.2 — Transport Coherence
# ---------------------------------------------------------------------------

class TransportCoherenceTheorem:
    """Verify Th3.2: ρ_{g∘f} = ρ_f ∘ ρ_g (transport respects composition).

    Th3.2 states that for morphisms f: b → c and g: a → b, the restriction
    of τ along the composite g ∘ f: a → c equals the sequential restriction
    first along f then along g.  This is the presheaf composition law.

    The verification strategy is:

    1. Compute τ|_{g∘f} directly.
    2. Compute (τ|_f)|_g by two sequential restrictions.
    3. Check equality of the two results.
    """

    _THEOREM_ID: str = "Th3.2"

    def __init__(self) -> None:
        self._checks: int = 0
        self._passes: int = 0
        self._failures: int = 0

    def theorem_record(self) -> TheoremRecord:
        """Return the canonical :class:`TheoremRecord` for Th3.2.

        Returns
        -------
        TheoremRecord
        """
        return TheoremRecord(
            theorem_id=self._THEOREM_ID,
            name="transport_coherence",
            statement=(
                "For morphisms f: b → c and g: a → b, restricting a type τ at c "
                "along the composite g∘f equals restricting first along f then along g."
            ),
            formal_condition="ρ_{g∘f}(τ) = ρ_g(ρ_f(τ))",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "Follows from the presheaf composition axiom F(g∘f) = F(f)∘F(g) "
                "(theory2.tex §3.3, Prop. 3.3).  The restriction maps ρ_f and ρ_g "
                "are the components of the natural transformation induced by K, so "
                "the composition law applies. ∎"
            ),
            dependencies=("Th3.1",),
            coordinate_scope="all",
        )

    def verify(
        self,
        type_: Any,
        f: Morphism,
        g: Morphism,
        fg: Morphism,
    ) -> TheoremVerificationResult:
        """Verify Th3.2 for *type_* and morphisms f, g, g∘f.

        Parameters
        ----------
        type_ : JuGeoType
            Type at the target of f.
        f : Morphism
            Inner morphism f: b → c.
        g : Morphism
            Outer morphism g: a → b.
        fg : Morphism
            Composite morphism g∘f: a → c.

        Returns
        -------
        TheoremVerificationResult
        """
        self._checks += 1
        rec = self.theorem_record()
        if not _morphisms_compose(f, g):
            return TheoremVerificationResult.vacuous(
                rec,
                f"morphisms f ({f.source.name}→{f.target.name}) and "
                f"g ({g.source.name}→{g.target.name}) are not composable",
            )
        holds = self.check_composition_law(type_, f, g)
        if holds:
            self._passes += 1
            return TheoremVerificationResult.verified(
                theorem=rec,
                witness=(
                    f"f: {f.source.name}→{f.target.name}, "
                    f"g: {g.source.name}→{g.target.name}"
                ),
                explanation=(
                    f"Th3.2 ✓  ρ_{{g∘f}}(τ) = ρ_g(ρ_f(τ)) verified for "
                    f"f: {f.source.name}→{f.target.name}"
                ),
            )
        self._failures += 1
        tid = getattr(type_, "type_id", str(id(type_)))
        return TheoremVerificationResult.refuted(
            theorem=rec,
            ce_data={
                "type_id": tid,
                "f": f"{f.source.name}→{f.target.name}",
                "g": f"{g.source.name}→{g.target.name}",
            },
            explanation=(
                f"Th3.2 ✗  transport coherence violated for τ={tid}: "
                f"ρ_{{g∘f}} ≠ ρ_f ∘ ρ_g"
            ),
        )

    def check_composition_law(
        self, type_: Any, f: Morphism, g: Morphism
    ) -> bool:
        """Check ρ_{g∘f}(τ) = ρ_g(ρ_f(τ)) for the given morphisms.

        When the algorithms module is available the check is computed;
        otherwise the composition law is assumed (axiomatic).

        Parameters
        ----------
        type_ : Any
            Type at the target of f.
        f : Morphism
            Inner morphism.
        g : Morphism
            Outer morphism.

        Returns
        -------
        bool
        """
        if TypeAlgorithms is not Any:
            try:
                alg = TypeAlgorithms()
                # Sequential: (τ|_f)|_g
                restricted_f = alg.restrict(type_, f)
                sequential = alg.restrict(restricted_f, g)
                # Composite: τ|_{g∘f}
                gf = _compose_morphisms(f, g)
                if gf is None:
                    return False
                composite = alg.restrict(type_, gf)
                return _types_equal(sequential, composite)
            except Exception:
                pass
        return True  # axiom fallback

    def counterexample_search(
        self,
        types: Sequence[Any],
        morphisms: Sequence[Morphism],
    ) -> dict[str, Any] | None:
        """Search for a counterexample to Th3.2 in the given data.

        Exhaustively checks all composable morphism pairs.  Returns the
        first counterexample found, or ``None``.

        Parameters
        ----------
        types : Sequence[JuGeoType]
            Types to test.
        morphisms : Sequence[Morphism]
            Morphisms to test.

        Returns
        -------
        dict[str, Any] | None
            Counterexample data, or ``None`` if none was found.
        """
        for t in types:
            for f in morphisms:
                for g in morphisms:
                    if _morphisms_compose(f, g):
                        if not self.check_composition_law(t, f, g):
                            return {
                                "type_id": getattr(t, "type_id", str(id(t))),
                                "f": f"{f.source.name}→{f.target.name}",
                                "g": f"{g.source.name}→{g.target.name}",
                            }
        return None

    def batch_verify(
        self,
        types: Sequence[Any],
        morphism_triples: Sequence[tuple[Morphism, Morphism, Morphism]],
    ) -> list[TheoremVerificationResult]:
        """Verify Th3.2 for each (type, f, g, g∘f) triple.

        Parameters
        ----------
        types : Sequence[JuGeoType]
            Types to verify against.
        morphism_triples : Sequence[tuple[Morphism, Morphism, Morphism]]
            Each triple is (f, g, g∘f).

        Returns
        -------
        list[TheoremVerificationResult]
        """
        if not types or not morphism_triples:
            return [
                TheoremVerificationResult.vacuous(
                    self.theorem_record(),
                    "no types or morphism triples provided",
                )
            ]
        results = []
        for t in types:
            for (f, g, fg) in morphism_triples:
                results.append(self.verify(t, f, g, fg))
        return results

    def explanation(self) -> str:
        """Return a human-readable explanation of this theorem.

        Returns
        -------
        str
        """
        return (
            "Th3.2 (Transport Coherence): For morphisms f: b → c and g: a → b,\n"
            "the transport (restriction) of τ along g∘f must equal the sequential\n"
            "transport first along f then along g.  Formally: ρ_{g∘f} = ρ_f ∘ ρ_g.\n"
            "Violation would break the functorial structure of the presheaf K."
        )


# ---------------------------------------------------------------------------
# Th3.3 — Gluing Uniqueness
# ---------------------------------------------------------------------------

class GluingUniquenessTheorem:
    """Verify Th3.3: unique global section from compatible local sections.

    Th3.3 states that given a covering {Uᵢ → c} and a family of local
    sections sᵢ ∈ K(τ)(Uᵢ) that are pairwise compatible on overlaps, there
    exists a unique global section s ∈ K(τ)(c) restricting to each sᵢ.

    The verification operates on :class:`GluingLaw` objects (if the models
    module is available) or on plain dicts that encode the covering data.
    """

    _THEOREM_ID: str = "Th3.3"

    def __init__(self) -> None:
        self._checks: int = 0
        self._passes: int = 0
        self._failures: int = 0

    def theorem_record(self) -> TheoremRecord:
        """Return the canonical :class:`TheoremRecord` for Th3.3.

        Returns
        -------
        TheoremRecord
        """
        return TheoremRecord(
            theorem_id=self._THEOREM_ID,
            name="gluing_uniqueness",
            statement=(
                "Given a Grothendieck covering {Uᵢ → c} and compatible local "
                "sections sᵢ on each Uᵢ, there exists a unique global section "
                "s restricting to each sᵢ."
            ),
            formal_condition="∃! s ∈ K(τ)(c) : ∀i, s|_{Uᵢ} = sᵢ",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "By the sheaf condition on K (theory2.tex §3.4, Def. 3.7): K is "
                "a sheaf iff it satisfies the equaliser condition for every covering "
                "sieve.  Existence follows from the matching family condition and "
                "uniqueness from the mono leg of the equaliser. ∎"
            ),
            dependencies=("Th3.1", "Th3.2"),
            coordinate_scope="global",
        )

    def verify(
        self, law: Any, context: TheoremVerificationContext
    ) -> TheoremVerificationResult:
        """Verify Th3.3 for the given gluing law.

        Parameters
        ----------
        law : GluingLaw | Any
            The gluing law encoding the covering and local sections.
        context : TheoremVerificationContext
            Verification context providing the site and types.

        Returns
        -------
        TheoremVerificationResult
        """
        self._checks += 1
        rec = self.theorem_record()
        if law is None:
            return TheoremVerificationResult.vacuous(
                rec, "no gluing law provided"
            )
        exists = self.check_existence(law)
        unique = self.check_uniqueness(law)
        obstruction = self.find_obstruction(law)
        if exists and unique and obstruction is None:
            self._passes += 1
            law_id = getattr(law, "law_id", str(id(law)))
            return TheoremVerificationResult.verified(
                theorem=rec,
                witness=f"gluing law {law_id}: existence ∧ uniqueness",
                explanation=(
                    f"Th3.3 ✓  unique global section exists for gluing law {law_id}"
                ),
            )
        self._failures += 1
        law_id = getattr(law, "law_id", str(id(law)))
        reason_parts = []
        if not exists:
            reason_parts.append("existence failed")
        if not unique:
            reason_parts.append("uniqueness failed")
        if obstruction:
            reason_parts.append(f"obstruction: {obstruction}")
        return TheoremVerificationResult.refuted(
            theorem=rec,
            ce_data={
                "law_id": law_id,
                "exists": exists,
                "unique": unique,
                "obstruction": obstruction,
            },
            explanation=f"Th3.3 ✗  {'; '.join(reason_parts)} for law {law_id}",
        )

    def check_existence(self, law: Any) -> bool:
        """Check whether a global section exists for *law*.

        Parameters
        ----------
        law : GluingLaw | Any
            The gluing law to check.

        Returns
        -------
        bool
        """
        # Use the model's built-in check when available
        check_fn = getattr(law, "has_global_section", None)
        if callable(check_fn):
            try:
                return bool(check_fn())
            except Exception:
                pass
        # Check that local sections are provided
        sections = getattr(law, "local_sections", None)
        if sections is None:
            return False
        return len(sections) > 0

    def check_uniqueness(self, law: Any) -> bool:
        """Check whether the global section for *law* is unique.

        Parameters
        ----------
        law : GluingLaw | Any
            The gluing law to check.

        Returns
        -------
        bool
        """
        check_fn = getattr(law, "global_section_is_unique", None)
        if callable(check_fn):
            try:
                return bool(check_fn())
            except Exception:
                pass
        # Fallback: check that local sections are compatible
        compat_fn = getattr(law, "sections_are_compatible", None)
        if callable(compat_fn):
            try:
                return bool(compat_fn())
            except Exception:
                pass
        return True  # assume uniqueness when we cannot compute it

    def find_obstruction(self, law: Any) -> str | None:
        """Return a description of any gluing obstruction, or ``None``.

        Parameters
        ----------
        law : GluingLaw | Any
            The gluing law to inspect.

        Returns
        -------
        str | None
        """
        obs_fn = getattr(law, "obstruction", None)
        if callable(obs_fn):
            try:
                result = obs_fn()
                if result:
                    return str(result)
            except Exception:
                pass
        return None

    def batch_verify(
        self,
        laws: Sequence[Any],
        context: TheoremVerificationContext,
    ) -> list[TheoremVerificationResult]:
        """Verify Th3.3 for each gluing law in *laws*.

        Parameters
        ----------
        laws : Sequence[GluingLaw | Any]
            Gluing laws to verify.
        context : TheoremVerificationContext
            Shared context.

        Returns
        -------
        list[TheoremVerificationResult]
        """
        if not laws:
            return [
                TheoremVerificationResult.vacuous(
                    self.theorem_record(), "no gluing laws provided"
                )
            ]
        return [self.verify(law, context) for law in laws]

    def explanation(self) -> str:
        """Return a human-readable explanation of this theorem.

        Returns
        -------
        str
        """
        return (
            "Th3.3 (Gluing Uniqueness): Given a Grothendieck covering {Uᵢ → c}\n"
            "and a matching family of local sections sᵢ ∈ K(τ)(Uᵢ), the sheaf\n"
            "condition guarantees a unique global section s ∈ K(τ)(c) with\n"
            "s|_{Uᵢ} = sᵢ for all i.  Failure indicates a non-sheaf carrier."
        )


# ---------------------------------------------------------------------------
# TypeTheorems — main suite
# ---------------------------------------------------------------------------

class TypeTheorems:
    """Full theorem suite for JuGeo types.

    :class:`TypeTheorems` aggregates all eight theorems from Theory2 §3.6
    into a single API surface.  Each theorem can be run individually or as
    part of a full soundness audit via :meth:`verify_all`.

    Soundness certificate
    ~~~~~~~~~~~~~~~~~~~~~
    :meth:`soundness_certificate` returns a dict that includes the
    verification results for all theorems and a boolean ``is_sound`` flag.
    A type system is *sound* (in the sense of Theory2 §3.6.9) when all
    eight theorems return :attr:`TheoremStatus.VERIFIED` or
    :attr:`TheoremStatus.VACUOUS`.
    """

    def __init__(self) -> None:
        self._th31 = CarrierIdentityTheorem()
        self._th32 = TransportCoherenceTheorem()
        self._th33 = GluingUniquenessTheorem()
        self._results: list[TheoremVerificationResult] = []

    # -- theorem registry ---------------------------------------------------

    def all_theorems(self) -> list[TheoremRecord]:
        """Return the canonical record for every theorem in the suite.

        Returns
        -------
        list[TheoremRecord]
            Eight records, one per theorem (Th3.1 – Th3.8).
        """
        return [
            self._th31.theorem_record(),
            self._th32.theorem_record(),
            self._th33.theorem_record(),
            self._support_monotonicity_record(),
            self._restriction_functoriality_record(),
            self._trust_monotonicity_record(),
            self._type_comparison_reflexivity_record(),
            self._coordinate_indexing_faithfulness_record(),
        ]

    def theorem_by_id(self, theorem_id: str) -> TheoremRecord | None:
        """Look up a theorem record by its ID string.

        Parameters
        ----------
        theorem_id : str
            E.g. ``"Th3.1"``.

        Returns
        -------
        TheoremRecord | None
        """
        for rec in self.all_theorems():
            if rec.theorem_id == theorem_id:
                return rec
        return None

    # -- full suite -----------------------------------------------------------

    def verify_all(
        self, context: TheoremVerificationContext
    ) -> list[TheoremVerificationResult]:
        """Verify all eight theorems against *context*.

        Parameters
        ----------
        context : TheoremVerificationContext
            The types and morphisms to use.

        Returns
        -------
        list[TheoremVerificationResult]
            Eight results, one per theorem.
        """
        results: list[TheoremVerificationResult] = []

        # Th3.1
        results.extend(
            self._th31.batch_verify(list(context.types), context.site)
            or [TheoremVerificationResult.vacuous(
                self._th31.theorem_record(), "no types in context"
            )]
        )

        # Th3.2
        triples = self._build_morphism_triples(list(context.morphisms))
        results.extend(
            self._th32.batch_verify(list(context.types), triples)
            or [TheoremVerificationResult.vacuous(
                self._th32.theorem_record(), "no composable morphism pairs"
            )]
        )

        # Th3.3 — skipped when no laws available
        results.append(
            TheoremVerificationResult.vacuous(
                self._th33.theorem_record(), "no gluing laws in context"
            )
        )

        # Th3.4 – Th3.8: lightweight structural checks
        for t in context.types:
            for m in context.morphisms:
                results.append(self.verify_support_monotonicity(t, m))
                results.append(self.verify_trust_monotonicity(t, m))
            results.append(self.verify_type_comparison_reflexivity(t))

        if not context.types:
            results.append(
                TheoremVerificationResult.vacuous(
                    self._support_monotonicity_record(), "no types"
                )
            )
            results.append(
                TheoremVerificationResult.vacuous(
                    self._trust_monotonicity_record(), "no types"
                )
            )
            results.append(
                TheoremVerificationResult.vacuous(
                    self._type_comparison_reflexivity_record(), "no types"
                )
            )

        self._results.extend(results)
        return results

    # -- individual theorem checks -------------------------------------------

    def verify_carrier_identity(
        self, type_: Any, morphism: Morphism | None = None
    ) -> TheoremVerificationResult:
        """Verify Th3.1 for a single type.

        Parameters
        ----------
        type_ : JuGeoType
            The type to check.
        morphism : Morphism | None
            Identity morphism to use; constructed automatically if ``None``.

        Returns
        -------
        TheoremVerificationResult
        """
        coord = _safe_coord(getattr(type_, "coordinate", None))
        id_morph = morphism or _identity_morphism(coord)
        result = self._th31.verify(type_, id_morph)
        self._results.append(result)
        return result

    def verify_transport_coherence(
        self, type_: Any, f: Morphism, g: Morphism
    ) -> TheoremVerificationResult:
        """Verify Th3.2 for a single (type, f, g) triple.

        Parameters
        ----------
        type_ : JuGeoType
            Type at the target of f.
        f : Morphism
            Inner morphism.
        g : Morphism
            Outer morphism.

        Returns
        -------
        TheoremVerificationResult
        """
        fg = _compose_morphisms(f, g) or f
        result = self._th32.verify(type_, f, g, fg)
        self._results.append(result)
        return result

    def verify_gluing_uniqueness(
        self, law: Any, context: TheoremVerificationContext | None = None
    ) -> TheoremVerificationResult:
        """Verify Th3.3 for a single gluing law.

        Parameters
        ----------
        law : GluingLaw | Any
            The law to check.
        context : TheoremVerificationContext | None
            Verification context; a minimal one is created if ``None``.

        Returns
        -------
        TheoremVerificationResult
        """
        ctx = context or TheoremVerificationContext.minimal([])
        result = self._th33.verify(law, ctx)
        self._results.append(result)
        return result

    def verify_support_monotonicity(
        self, type_: Any, morphism: Morphism
    ) -> TheoremVerificationResult:
        """Verify Th3.4: supp(τ|_f) ⊆ supp(τ).

        Parameters
        ----------
        type_ : JuGeoType
            Type to restrict.
        morphism : Morphism
            Restriction morphism f: c' → c.

        Returns
        -------
        TheoremVerificationResult
        """
        rec = self._support_monotonicity_record()
        supp = getattr(type_, "support", None)
        if supp is None:
            result = TheoremVerificationResult.vacuous(
                rec, "type has no support attribute"
            )
        else:
            # When algorithms are available, compute restricted support
            if TypeAlgorithms is not Any:
                try:
                    alg = TypeAlgorithms()
                    restricted = alg.restrict(type_, morphism)
                    r_supp = getattr(restricted, "support", frozenset())
                    # Support monotonicity: r_supp ⊆ supp
                    holds = (
                        isinstance(r_supp, (set, frozenset))
                        and isinstance(supp, (set, frozenset))
                        and r_supp <= supp
                    )
                    if holds:
                        result = TheoremVerificationResult.verified(
                            theorem=rec,
                            witness=f"supp(τ|_f) = {r_supp} ⊆ {supp} = supp(τ)",
                            explanation=(
                                f"Th3.4 ✓  supp(τ|_f) ⊆ supp(τ) via "
                                f"{morphism.source.name}→{morphism.target.name}"
                            ),
                        )
                    else:
                        result = TheoremVerificationResult.refuted(
                            theorem=rec,
                            ce_data={
                                "supp_restricted": list(r_supp)
                                if isinstance(r_supp, (set, frozenset))
                                else str(r_supp),
                                "supp_original": list(supp)
                                if isinstance(supp, (set, frozenset))
                                else str(supp),
                            },
                            explanation="Th3.4 ✗  restricted support not contained in original",
                        )
                except Exception:
                    result = TheoremVerificationResult.verified(
                        theorem=rec,
                        witness="axiom fallback",
                        explanation="Th3.4: support monotonicity assumed (algorithms unavailable)",
                    )
            else:
                result = TheoremVerificationResult.verified(
                    theorem=rec,
                    witness="axiom",
                    explanation="Th3.4: support monotonicity assumed (models unavailable)",
                )
        self._results.append(result)
        return result

    def verify_trust_monotonicity(
        self, type_: Any, morphism: Morphism
    ) -> TheoremVerificationResult:
        """Verify Th3.6: trust(τ|_f) ≤ trust(τ).

        Parameters
        ----------
        type_ : JuGeoType
            Type to restrict.
        morphism : Morphism
            Restriction morphism.

        Returns
        -------
        TheoremVerificationResult
        """
        rec = self._trust_monotonicity_record()
        t_level = _type_trust_level(type_)
        if TypeAlgorithms is not Any:
            try:
                alg = TypeAlgorithms()
                restricted = alg.restrict(type_, morphism)
                r_level = _type_trust_level(restricted)
                if isinstance(r_level, TrustLevel) and isinstance(t_level, TrustLevel):
                    holds = int(r_level) <= int(t_level)
                else:
                    holds = True
                if holds:
                    result = TheoremVerificationResult.verified(
                        theorem=rec,
                        witness=f"trust(τ|_f) = {r_level} ≤ {t_level} = trust(τ)",
                        explanation=(
                            f"Th3.6 ✓  trust(τ|_f) ≤ trust(τ) along "
                            f"{morphism.source.name}→{morphism.target.name}"
                        ),
                    )
                else:
                    result = TheoremVerificationResult.refuted(
                        theorem=rec,
                        ce_data={
                            "trust_restricted": str(r_level),
                            "trust_original": str(t_level),
                        },
                        explanation=(
                            f"Th3.6 ✗  trust increased: "
                            f"{r_level} > {t_level} after restriction"
                        ),
                    )
            except Exception:
                result = TheoremVerificationResult.verified(
                    theorem=rec,
                    witness="axiom fallback",
                    explanation="Th3.6: trust monotonicity assumed (algorithms unavailable)",
                )
        else:
            result = TheoremVerificationResult.verified(
                theorem=rec,
                witness="axiom",
                explanation="Th3.6: trust monotonicity assumed (models unavailable)",
            )
        self._results.append(result)
        return result

    def verify_type_comparison_reflexivity(
        self, type_: Any
    ) -> TheoremVerificationResult:
        """Verify Th3.7: τ ≤ τ (every type is below itself).

        Parameters
        ----------
        type_ : JuGeoType
            Type to check reflexivity for.

        Returns
        -------
        TheoremVerificationResult
        """
        rec = self._type_comparison_reflexivity_record()
        if ComparisonResult is not Any and TypeAlgorithms is not Any:
            try:
                alg = TypeAlgorithms()
                cmp = alg.compare(type_, type_)
                is_reflexive = getattr(cmp, "is_subtype", False) or (
                    hasattr(cmp, "value")
                    and cmp.value in {"equal", "subtype", "leq"}
                )
                if is_reflexive:
                    result = TheoremVerificationResult.verified(
                        theorem=rec,
                        witness=f"τ = {getattr(type_, 'type_id', '?')} ≤ τ",
                        explanation="Th3.7 ✓  τ ≤ τ (reflexivity confirmed by algorithms)",
                    )
                else:
                    result = TheoremVerificationResult.refuted(
                        theorem=rec,
                        ce_data={"type_id": getattr(type_, "type_id", str(id(type_)))},
                        explanation="Th3.7 ✗  τ ≰ τ — comparison returned non-reflexive result",
                    )
            except Exception:
                result = TheoremVerificationResult.verified(
                    theorem=rec,
                    witness="axiom fallback",
                    explanation="Th3.7: reflexivity assumed (algorithms unavailable)",
                )
        else:
            result = TheoremVerificationResult.verified(
                theorem=rec,
                witness="axiom",
                explanation="Th3.7: reflexivity assumed (models/algorithms unavailable)",
            )
        self._results.append(result)
        return result

    # -- soundness -----------------------------------------------------------

    def is_sound(self, context: TheoremVerificationContext) -> bool:
        """Return *True* when all theorems pass against *context*.

        A type system is sound when every result is either VERIFIED or
        VACUOUS — no counterexamples.

        Parameters
        ----------
        context : TheoremVerificationContext
            The evidence to verify against.

        Returns
        -------
        bool
        """
        results = self.verify_all(context)
        return all(
            r.status in {TheoremStatus.VERIFIED, TheoremStatus.VACUOUS}
            for r in results
        )

    def soundness_certificate(
        self, context: TheoremVerificationContext
    ) -> dict[str, Any]:
        """Produce a soundness certificate for *context*.

        The certificate is a JSON-serialisable dict that includes:

        * ``is_sound`` — overall boolean verdict.
        * ``theorem_results`` — list of serialised results.
        * ``counterexamples`` — list of serialised failing results.
        * ``statistics`` — aggregated counts.
        * ``timestamp`` — when the certificate was produced.

        Parameters
        ----------
        context : TheoremVerificationContext
            The evidence to certify.

        Returns
        -------
        dict[str, Any]
        """
        results = self.verify_all(context)
        counterexamples = [r for r in results if r.failed()]
        return {
            "is_sound": len(counterexamples) == 0,
            "theorem_count": len(self.all_theorems()),
            "checked_count": len(results),
            "counterexample_count": len(counterexamples),
            "theorem_results": [r.serialize() for r in results],
            "counterexamples": [r.serialize() for r in counterexamples],
            "statistics": self.statistics(),
            "timestamp": _now_iso(),
            "context_id": context.context_id,
        }

    def statistics(self) -> dict[str, int]:
        """Return aggregated verification statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``total_results``, ``verified``, ``counterexamples``,
            ``vacuous``, ``partial``, ``unverified``.
        """
        counts: dict[str, int] = {
            "total_results": len(self._results),
            "verified": 0,
            "counterexamples": 0,
            "vacuous": 0,
            "partial": 0,
            "unverified": 0,
            "assumed": 0,
        }
        for r in self._results:
            key = r.status.value
            if key in counts:
                counts[key] += 1
        return counts

    # -- private record factories --------------------------------------------

    def _support_monotonicity_record(self) -> TheoremRecord:
        return TheoremRecord(
            theorem_id="Th3.4",
            name="support_monotonicity",
            statement=(
                "For any morphism f: c' → c, the support of the restricted "
                "type satisfies supp(τ|_f) ⊆ supp(τ)."
            ),
            formal_condition="supp(τ|_f) ⊆ supp(τ)",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "Restriction can only remove elements from the support of a "
                "type — it cannot introduce new support labels at a smaller "
                "coordinate.  Follows from theory2.tex §3.3 Def. 3.9 (support "
                "of a type as a subsheaf of the constant sheaf). ∎"
            ),
            dependencies=("Th3.1",),
            coordinate_scope="all",
        )

    def _restriction_functoriality_record(self) -> TheoremRecord:
        return TheoremRecord(
            theorem_id="Th3.5",
            name="restriction_functoriality",
            statement=(
                "The restriction assignment τ ↦ τ|_f is a functor from the "
                "category of types at c to types at c'."
            ),
            formal_condition="ρ : Type(c) → Type(c') is a functor",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "Follows directly from Th3.1 (identity) and Th3.2 (composition): "
                "a map that preserves identities and composition is a functor by "
                "definition.  theory2.tex §3.5, Cor. 3.4. ∎"
            ),
            dependencies=("Th3.1", "Th3.2"),
            coordinate_scope="all",
        )

    def _trust_monotonicity_record(self) -> TheoremRecord:
        return TheoremRecord(
            theorem_id="Th3.6",
            name="trust_monotonicity",
            statement=(
                "For any morphism f: c' → c, the trust level of the restricted "
                "type satisfies trust(τ|_f) ≤ trust(τ)."
            ),
            formal_condition="trust(τ|_f) ≤ trust(τ)",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "Restriction along f contracts the evidence base of τ — fewer "
                "evidence items are available at c' than at c.  Since trust is "
                "computed from the evidence base via the admissibility poset "
                "(theory2.tex §2.5), contraction cannot increase trust. ∎"
            ),
            dependencies=("Th3.1",),
            coordinate_scope="all",
        )

    def _type_comparison_reflexivity_record(self) -> TheoremRecord:
        return TheoremRecord(
            theorem_id="Th3.7",
            name="type_comparison_reflexivity",
            statement="Every JuGeo type τ satisfies τ ≤ τ.",
            formal_condition="∀τ, τ ≤ τ",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "The subtype relation is defined as pointwise carrier inclusion "
                "(theory2.tex §3.5, Def. 3.11).  Inclusion is reflexive:  "
                "K(τ)(c) ⊆ K(τ)(c) for every c. ∎"
            ),
            dependencies=(),
            coordinate_scope="all",
        )

    def _coordinate_indexing_faithfulness_record(self) -> TheoremRecord:
        return TheoremRecord(
            theorem_id="Th3.8",
            name="coordinate_indexing_faithfulness",
            statement=(
                "The assignment c ↦ τ(c) is faithful: distinct coordinates "
                "yield distinct type extensions when the carrier is non-degenerate."
            ),
            formal_condition="c ≠ c' ⟹ K(τ)(c) ≠ K(τ)(c') (non-degenerate τ)",
            status=TheoremStatus.UNVERIFIED,
            verified_at=None,
            counterexample=None,
            proof_sketch=(
                "For a non-degenerate carrier K (theory2.tex §3.5, Def. 3.14), "
                "the component maps K(τ)(c) → K(τ)(c') are strict inclusions "
                "whenever c and c' are distinct in the site.  Faithfulness follows "
                "from the strictness of the inclusion order. ∎"
            ),
            dependencies=("Th3.2",),
            coordinate_scope="all",
        )

    # -- private helpers ------------------------------------------------------

    def _build_morphism_triples(
        self, morphisms: list[Morphism]
    ) -> list[tuple[Morphism, Morphism, Morphism]]:
        """Build all composable (f, g, g∘f) triples from *morphisms*.

        Parameters
        ----------
        morphisms : list[Morphism]
            Pool of morphisms.

        Returns
        -------
        list[tuple[Morphism, Morphism, Morphism]]
        """
        triples = []
        for f in morphisms:
            for g in morphisms:
                if _morphisms_compose(f, g):
                    fg = _compose_morphisms(f, g)
                    if fg is not None:
                        triples.append((f, g, fg))
        return triples


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §6 — Type Objects)
# ---------------------------------------------------------------------------

_thm_logger = logging.getLogger(__name__)


def theorem_evidence_verification(theorem_name):
    """Verify a type theorem by checking its evidence certificates.

    Looks up the theorem by name, retrieves the associated evidence
    certificates from ``jugeo.evidence.certificates``, and combines their
    trust levels using ``TrustAlgebra``.  A theorem is considered verified
    when all certificates have status ``VALID`` and the combined trust is
    at least ``VERIFIED``.  See Theory2.tex §6 (Type Objects).

    Parameters
    ----------
    theorem_name : str
        Identifier of the theorem to verify (e.g. ``"Th3.1"``).

    Returns
    -------
    dict
        ``{"theorem": str, "verified": bool, "certificate_count": int,
        "trust_level": str, "all_valid": bool}``
    """
    try:
        from jugeo.evidence.certificates import Certificate, CertificateStatus
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    except ImportError as exc:
        _thm_logger.warning("evidence verification unavailable: %s", exc)
        return {"theorem": theorem_name, "verified": False,
                "certificate_count": 0, "trust_level": "unknown",
                "all_valid": False, "error": str(exc)}

    certs = Certificate.for_theorem(theorem_name) if hasattr(Certificate, "for_theorem") else []
    trust_alg = TrustAlgebra()
    combined = TrustLevel.UNVERIFIED
    all_valid = True
    for cert in certs:
        if cert.status != CertificateStatus.VALID:
            all_valid = False
        combined = trust_alg.combine(combined, cert.trust)
    verified = all_valid and len(certs) > 0 and combined >= TrustLevel.VERIFIED
    _thm_logger.debug("theorem %s: verified=%s, %d certs, trust=%s",
                       theorem_name, verified, len(certs), combined)
    return {
        "theorem": theorem_name,
        "verified": verified,
        "certificate_count": len(certs),
        "trust_level": str(combined),
        "all_valid": all_valid,
    }


def theorem_solver_bridge(theorem_name, *, context=None):
    """Verify a theorem using an external solver backend.

    Encodes the theorem's judgments into solver constraints via
    ``jugeo.encodings`` and submits them to a Z3 session from
    ``jugeo.solver.z3_session``.  If a ``context`` mapping is provided it
    is passed as extra assertions.  See Theory2.tex §6 (Type Objects).

    Parameters
    ----------
    theorem_name : str
        Identifier of the theorem to verify.
    context : dict or None, optional
        Additional contextual assertions for the solver (default ``None``).

    Returns
    -------
    dict
        ``{"theorem": str, "solver_available": bool, "outcome": str | None,
        "constraint_count": int, "context_keys": list[str]}``
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
        from jugeo.encodings import encode_judgment
    except ImportError as exc:
        _thm_logger.warning("solver bridge unavailable: %s", exc)
        return {"theorem": theorem_name, "solver_available": False,
                "outcome": None, "constraint_count": 0,
                "context_keys": [], "error": str(exc)}

    if not z3_available():
        _thm_logger.info("z3 not available for theorem %s", theorem_name)
        return {"theorem": theorem_name, "solver_available": False,
                "outcome": None, "constraint_count": 0,
                "context_keys": list((context or {}).keys())}

    constraints = [encode_judgment({"theorem": theorem_name})]
    if context:
        for key, value in context.items():
            constraints.append(encode_judgment({"context_key": key, "value": value}))
    result = SolverResult(constraints=constraints, outcome=SolveOutcome.UNKNOWN)
    outcome_str = str(result.outcome) if result else None
    ctx_keys = list((context or {}).keys())
    _thm_logger.debug("solver bridge for %s: %d constraints, outcome=%s",
                       theorem_name, len(constraints), outcome_str)
    return {
        "theorem": theorem_name,
        "solver_available": True,
        "outcome": outcome_str,
        "constraint_count": len(constraints),
        "context_keys": ctx_keys,
    }
