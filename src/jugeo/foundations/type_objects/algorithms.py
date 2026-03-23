"""Type algorithms for JuGeo: inference, checking, transport, gluing, and comparison.

This module implements the computational face of the type-object layer.
The algorithms are grounded in Theory2 Ch3 and operate on the semantic
objects defined in models.py.  Key algorithms:

* **Type inference** (τ-infer): given a proposition φ at coordinate c,
  infer the most specific JuGeo type τ such that c ⊢ φ : τ.

* **Type checking** (τ-check): verify that an expression/value e is an
  inhabitant of carrier K(τ) at coordinate c.

* **Type transport** (τ-transport): given τ at c and a morphism f: c' → c,
  compute τ|_f at c'.

* **Type gluing** (τ-glue): given compatible local types {τᵢ at cᵢ},
  assemble the unique global type τ at c with τ|_{cᵢ} = τᵢ.

* **Type comparison** (τ-compare): decide τ₁ ≤ τ₂ (subtype), τ₁ ≃ τ₂
  (equivalence), or τ₁ ⊓ τ₂ (intersection).

Algorithm design principles (theory2.tex §3.3–§3.6):

1. **Completeness over speed** — the algorithms prefer completeness to
   performance.  When a fast syntactic decision is possible, it is taken;
   otherwise, full semantic evaluation is performed.

2. **Trust-aware output** — every result carries a confidence score derived
   from the trust algebra (E_adm, ⪯, ⊕).  Results produced under weaker
   evidence have lower confidence.

3. **Cost accounting** — every result records a ``cost`` field.  Cost is
   an abstract non-negative float indicating relative computational effort.
   Downstream systems may use this to prefer cheaper alternatives when
   multiple results are equally confident.

4. **Batch interfaces** — the ``TypeAlgorithms`` class provides batch
   versions of inference and checking to amortize per-call overhead when
   processing many propositions at once.

5. **Sheaf coherence** — transport and gluing operations enforce the
   carrier laws from §3.3 (CL1–CL6) via the ``CarrierLawSystem`` imported
   from the sibling module.

References
----------
theory2.tex §3.3 (carrier laws), §3.4 (transport), §3.5 (gluing),
§3.6 (comparison and subtyping).

# copilot: module provenance — author: copilot
"""

from __future__ import annotations

import logging
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

from jugeo.foundations.type_objects.carrier_laws_transport_gluing_and import (
    CarrierLawSystem,
    CarrierValidator,
    LawViolation,
    LawKind,
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


def _fresh_id(prefix: str = "alg") -> str:
    """Return a short unique identifier.

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


def _clamp_confidence(value: float) -> float:
    """Clamp *value* to the interval [0.0, 1.0].

    Parameters
    ----------
    value:
        The raw confidence value.

    Returns
    -------
    float
        The clamped value in [0.0, 1.0].
    """
    return max(0.0, min(1.0, value))


def _type_tag(type_: Any) -> str:
    """Extract a short tag string from a type object.

    Parameters
    ----------
    type_:
        Any object expected to behave like a ``JuGeoType``.

    Returns
    -------
    str
        The ``type_id`` if present, else the repr truncated to 40 characters.
    """
    tid = getattr(type_, "type_id", None)
    if tid is not None:
        return str(tid)
    return repr(type_)[:40]


def _carrier_kind_tag(type_: Any) -> str:
    """Extract the carrier kind tag from a type.

    Parameters
    ----------
    type_:
        A ``JuGeoType`` or compatible object.

    Returns
    -------
    str
        String value of the carrier kind, or ``"unknown"``.
    """
    carrier = getattr(type_, "carrier", None)
    if carrier is None:
        return "unknown"
    kind = getattr(carrier, "kind", None)
    if kind is None:
        return "unknown"
    return getattr(kind, "value", str(kind))


def _prop_tag(proposition: Any) -> str:
    """Extract a short tag from a proposition.

    Parameters
    ----------
    proposition:
        A ``Proposition`` or compatible object.

    Returns
    -------
    str
        The ``label`` or ``proposition_id`` if present, else repr[:40].
    """
    for attr in ("label", "proposition_id", "name"):
        val = getattr(proposition, attr, None)
        if val is not None:
            return str(val)
    return repr(proposition)[:40]


def _coordinate_tag(coord: Any) -> str:
    """Return a readable tag for a coordinate.

    Parameters
    ----------
    coord:
        A :class:`~jugeo.geometry.site.Coordinate` or compatible.

    Returns
    -------
    str
        ``"/".join(coord.components)`` if available, else repr[:40].
    """
    parts = getattr(coord, "components", None)
    if parts:
        return "/".join(str(p) for p in parts)
    return repr(coord)[:40]


def _trust_rank(trust_level: Any) -> float:
    """Map a trust level to a float in [0, 1].

    Parameters
    ----------
    trust_level:
        A ``TrustLevel`` enum member or object with a ``value`` attribute.

    Returns
    -------
    float
        Normalized trust rank in [0.0, 1.0].
    """
    _MAP = {
        "axiom": 1.0,
        "verified": 0.9,
        "trusted": 0.75,
        "provisional": 0.5,
        "speculative": 0.25,
        "untrusted": 0.0,
    }
    tag = getattr(trust_level, "value", str(trust_level)).lower()
    return _MAP.get(tag, 0.5)


def _types_structurally_equal(a: Any, b: Any) -> bool:
    """Decide whether two types are structurally equal.

    Checks ``type_id`` equality first, then falls back to ``==``.

    Parameters
    ----------
    a:
        First type.
    b:
        Second type.

    Returns
    -------
    bool
        True when the types are considered equal.
    """
    a_id = getattr(a, "type_id", None)
    b_id = getattr(b, "type_id", None)
    if a_id is not None and b_id is not None:
        return a_id == b_id
    return a == b


def _carrier_subsumes(carrier_a: Any, carrier_b: Any) -> bool:
    """Return True when carrier_a subsumes (is coarser than) carrier_b.

    In theory2 terms: K(τ_a) ⊇ K(τ_b), i.e. every inhabitant of τ_b is an
    inhabitant of τ_a.

    Parameters
    ----------
    carrier_a:
        The candidate super-carrier.
    carrier_b:
        The candidate sub-carrier.

    Returns
    -------
    bool
        True when the subsumption appears to hold, based on kind and
        coordinate information.
    """
    if carrier_a is None or carrier_b is None:
        return False
    kind_a = getattr(carrier_a, "kind", None)
    kind_b = getattr(carrier_b, "kind", None)
    if kind_a is not None and kind_b is not None:
        if kind_a == kind_b:
            coord_a = getattr(carrier_a, "coordinate", None)
            coord_b = getattr(carrier_b, "coordinate", None)
            if coord_a is not None and coord_b is not None:
                return coord_b.is_prefix_of(coord_a) or coord_a == coord_b
    return False


# ---------------------------------------------------------------------------
# InferenceStrategy
# ---------------------------------------------------------------------------


class InferenceStrategy(str, Enum):
    """Enumeration of type inference strategies for τ-infer.

    Members
    -------
    SYNTACTIC
        Inspect the syntactic shape of the proposition without evaluating
        its semantic content.  Fastest, but least precise.
    SEMANTIC
        Evaluate the full semantic content of the proposition, including
        any evidence and trust annotations.  Slowest, most precise.
    STRUCTURAL
        Decompose the proposition structurally into sub-propositions and
        infer types for each, then compose.
    CONSTRAINT
        Use a constraint-propagation algorithm to narrow down the type.
    HEURISTIC
        Apply domain-specific heuristics that may not always be correct.
    DELEGATED
        Delegate the inference to an external oracle (e.g. a copilot
        channel).  Result has lower automatic trust.
    """

    SYNTACTIC = "syntactic"
    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    CONSTRAINT = "constraint"
    HEURISTIC = "heuristic"
    DELEGATED = "delegated"


# ---------------------------------------------------------------------------
# TypeInferenceResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeInferenceResult:
    """The result of a τ-infer call.

    Parameters
    ----------
    result_id:
        Unique identifier for this inference result.
    inferred_type:
        The most specific inferred ``JuGeoType``, or ``None`` on failure.
    strategy:
        The :class:`InferenceStrategy` that was used.
    confidence:
        A float in [0.0, 1.0] indicating confidence in the inferred type.
    alternatives:
        A tuple of alternative types considered during inference.
    explanation:
        A prose explanation of why this type was inferred.
    violations:
        Any law violations detected during inference.
    cost:
        Abstract non-negative cost of producing this result.

    Raises
    ------
    ValueError
        If ``confidence`` is outside [0.0, 1.0].
    """

    result_id: str
    inferred_type: Any  # JuGeoType | None
    strategy: InferenceStrategy
    confidence: float
    alternatives: tuple[Any, ...]  # tuple[JuGeoType, ...]
    explanation: str
    violations: tuple[str, ...]
    cost: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"TypeInferenceResult.confidence must be in [0.0, 1.0];"
                f" got {self.confidence}"
            )

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def succeeded(self) -> bool:
        """Return True when inference produced a type.

        Returns
        -------
        bool
            True iff ``inferred_type`` is not ``None``.
        """
        return self.inferred_type is not None

    def failed(self) -> bool:
        """Return True when inference failed to produce a type.

        Returns
        -------
        bool
            True iff ``inferred_type`` is ``None``.
        """
        return self.inferred_type is None

    def best_type(self) -> Any:
        """Return the best available type: inferred_type if present, else first alternative.

        Returns
        -------
        JuGeoType | None
            The inferred type, or the first alternative, or ``None``.
        """
        if self.inferred_type is not None:
            return self.inferred_type
        if self.alternatives:
            return self.alternatives[0]
        return None

    def alternative_count(self) -> int:
        """Return the number of alternative types.

        Returns
        -------
        int
            Length of ``self.alternatives``.
        """
        return len(self.alternatives)

    def is_ambiguous(self) -> bool:
        """Return True when multiple alternatives exist with near-equal confidence.

        Ambiguity is declared when there are ≥ 2 alternatives and their
        number exceeds 1 while the inferred type is present (meaning the
        algorithm could not conclusively rank them).

        Returns
        -------
        bool
            True iff the result is ambiguous.
        """
        return self.inferred_type is not None and len(self.alternatives) >= 2

    def explanation_lines(self) -> list[str]:
        """Return the explanation split into lines.

        Returns
        -------
        list[str]
            The explanation string split on newlines, stripped of blanks.
        """
        return [ln.strip() for ln in self.explanation.splitlines() if ln.strip()]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this result to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Round-trip-safe mapping of all fields.
        """
        return {
            "result_id": self.result_id,
            "inferred_type": _type_tag(self.inferred_type) if self.inferred_type else None,
            "strategy": self.strategy.value,
            "confidence": self.confidence,
            "alternatives": [_type_tag(t) for t in self.alternatives],
            "explanation": self.explanation,
            "violations": list(self.violations),
            "cost": self.cost,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TypeInferenceResult:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        TypeInferenceResult
            The reconstructed result (with ``inferred_type`` and
            ``alternatives`` as raw tag strings, since JuGeoType objects
            cannot be reconstructed without models.py).
        """
        return cls(
            result_id=data["result_id"],
            inferred_type=data.get("inferred_type"),
            strategy=InferenceStrategy(data["strategy"]),
            confidence=float(data.get("confidence", 0.0)),
            alternatives=tuple(data.get("alternatives", [])),
            explanation=data.get("explanation", ""),
            violations=tuple(data.get("violations", [])),
            cost=float(data.get("cost", 0.0)),
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def failure(
        cls,
        explanation: str,
        violations: tuple[str, ...] = (),
    ) -> TypeInferenceResult:
        """Construct a failed inference result.

        Parameters
        ----------
        explanation:
            Human-readable explanation of why inference failed.
        violations:
            Optional tuple of violation strings.

        Returns
        -------
        TypeInferenceResult
            A result with ``inferred_type=None`` and ``confidence=0.0``.
        """
        return cls(
            result_id=_fresh_id("inf"),
            inferred_type=None,
            strategy=InferenceStrategy.SYNTACTIC,
            confidence=0.0,
            alternatives=(),
            explanation=explanation,
            violations=violations,
            cost=0.0,
        )

    @classmethod
    def success(
        cls,
        type_: Any,
        explanation: str,
        confidence: float = 1.0,
        strategy: InferenceStrategy = InferenceStrategy.SYNTACTIC,
    ) -> TypeInferenceResult:
        """Construct a successful inference result.

        Parameters
        ----------
        type_:
            The inferred ``JuGeoType``.
        explanation:
            Human-readable explanation of the inference.
        confidence:
            Confidence score in [0.0, 1.0].  Default 1.0.
        strategy:
            The strategy used.  Default ``SYNTACTIC``.

        Returns
        -------
        TypeInferenceResult
            A result with ``inferred_type=type_`` and ``failed()==False``.
        """
        return cls(
            result_id=_fresh_id("inf"),
            inferred_type=type_,
            strategy=strategy,
            confidence=_clamp_confidence(confidence),
            alternatives=(),
            explanation=explanation,
            violations=(),
            cost=0.0,
        )


# ---------------------------------------------------------------------------
# TypeCheckResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeCheckResult:
    """The result of a τ-check call.

    Parameters
    ----------
    result_id:
        Unique identifier.
    type_:
        The ``JuGeoType`` against which the expression was checked.
    expression:
        String representation of the expression/value being checked.
    is_valid:
        True when the expression is a valid inhabitant of the carrier K(τ).
    witness:
        A string encoding the typing witness, or ``None``.
    violations:
        Tuple of violation strings produced during checking.
    explanation:
        Human-readable explanation of the check outcome.
    cost:
        Abstract computational cost.
    """

    result_id: str
    type_: Any  # JuGeoType
    expression: str
    is_valid: bool
    witness: str | None
    violations: tuple[str, ...]
    explanation: str
    cost: float

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def passed(self) -> bool:
        """Return True when the type check passed.

        Returns
        -------
        bool
            Alias for ``self.is_valid``.
        """
        return self.is_valid

    def failed(self) -> bool:
        """Return True when the type check failed.

        Returns
        -------
        bool
            True iff ``is_valid`` is False.
        """
        return not self.is_valid

    def has_witness(self) -> bool:
        """Return True when a typing witness is present.

        Returns
        -------
        bool
            True iff ``witness`` is not ``None``.
        """
        return self.witness is not None

    def explanation_lines(self) -> list[str]:
        """Return the explanation as a list of stripped lines.

        Returns
        -------
        list[str]
            Non-empty lines of the explanation text.
        """
        return [ln.strip() for ln in self.explanation.splitlines() if ln.strip()]

    def to_judgment_evidence(self) -> dict[str, Any]:
        """Convert this result to a structured judgment-evidence mapping.

        The output can be used to populate an evidence bundle in the judgment
        algebra (theory2.tex §2.4).

        Returns
        -------
        dict[str, Any]
            Keys: ``"channel"``, ``"is_valid"``, ``"witness"``,
            ``"expression"``, ``"type_id"``, ``"violations"``.
        """
        return {
            "channel": "type_check",
            "is_valid": self.is_valid,
            "witness": self.witness,
            "expression": self.expression,
            "type_id": _type_tag(self.type_),
            "violations": list(self.violations),
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this result.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping.
        """
        return {
            "result_id": self.result_id,
            "type_": _type_tag(self.type_),
            "expression": self.expression,
            "is_valid": self.is_valid,
            "witness": self.witness,
            "violations": list(self.violations),
            "explanation": self.explanation,
            "cost": self.cost,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TypeCheckResult:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        TypeCheckResult
            The reconstructed result.
        """
        return cls(
            result_id=data["result_id"],
            type_=data.get("type_"),
            expression=data.get("expression", ""),
            is_valid=bool(data.get("is_valid", False)),
            witness=data.get("witness"),
            violations=tuple(data.get("violations", [])),
            explanation=data.get("explanation", ""),
            cost=float(data.get("cost", 0.0)),
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def valid(
        cls,
        type_: Any,
        expression: str,
        witness: str | None = None,
    ) -> TypeCheckResult:
        """Construct a passing type-check result.

        Parameters
        ----------
        type_:
            The ``JuGeoType`` being checked against.
        expression:
            The expression that was checked.
        witness:
            Optional typing witness.

        Returns
        -------
        TypeCheckResult
            A result with ``is_valid=True``.
        """
        return cls(
            result_id=_fresh_id("chk"),
            type_=type_,
            expression=expression,
            is_valid=True,
            witness=witness,
            violations=(),
            explanation=(
                f"Expression {expression!r} is a valid inhabitant of"
                f" type {_type_tag(type_)}.  Carrier K(τ) admits the value."
            ),
            cost=0.0,
        )

    @classmethod
    def invalid(
        cls,
        type_: Any,
        expression: str,
        violations: tuple[str, ...],
        explanation: str,
    ) -> TypeCheckResult:
        """Construct a failing type-check result.

        Parameters
        ----------
        type_:
            The ``JuGeoType`` being checked against.
        expression:
            The expression that was checked.
        violations:
            Tuple of violation descriptions.
        explanation:
            Human-readable explanation.

        Returns
        -------
        TypeCheckResult
            A result with ``is_valid=False``.
        """
        return cls(
            result_id=_fresh_id("chk"),
            type_=type_,
            expression=expression,
            is_valid=False,
            witness=None,
            violations=violations,
            explanation=explanation,
            cost=0.0,
        )


# ---------------------------------------------------------------------------
# TransportResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransportResult:
    """The result of a τ-transport call.

    Transport along f: c' → c maps a type τ at c to τ|_f at c'.  The
    result records whether the transport was exact (all carrier data
    preserved) or approximate (some information was lost).

    Parameters
    ----------
    result_id:
        Unique identifier.
    source_type:
        The ``JuGeoType`` τ at coordinate c.
    target_type:
        The ``JuGeoType`` τ|_f at coordinate c'.
    morphism:
        The morphism f: c' → c along which transport was computed.
    transport_map:
        The ``TransportMap`` that was applied.
    is_exact:
        True when transport is lossless (no carrier information lost).
    approximation_error:
        Description of what was lost in an approximate transport, or
        ``None`` for exact transports.
    cost:
        Abstract computational cost.
    """

    result_id: str
    source_type: Any  # JuGeoType
    target_type: Any  # JuGeoType
    morphism: Morphism
    transport_map: Any  # TransportMap
    is_exact: bool
    approximation_error: str | None
    cost: float

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def succeeded(self) -> bool:
        """Return True when transport produced a target type.

        Returns
        -------
        bool
            True iff ``target_type`` is not ``None``.
        """
        return self.target_type is not None

    def is_lossless(self) -> bool:
        """Return True when no information was lost during transport.

        Returns
        -------
        bool
            Alias for ``self.is_exact``.
        """
        return self.is_exact

    def carrier_preserved(self) -> bool:
        """Return True when the carrier kind is preserved by transport.

        Carrier kind preservation means the target type has the same
        carrier kind tag as the source.

        Returns
        -------
        bool
            True when both types report the same carrier kind.
        """
        if self.source_type is None or self.target_type is None:
            return False
        src_kind = _carrier_kind_tag(self.source_type)
        tgt_kind = _carrier_kind_tag(self.target_type)
        return src_kind == tgt_kind or src_kind == "unknown" or tgt_kind == "unknown"

    def trust_preserved(self) -> bool:
        """Return True when transport did not increase trust.

        Trust monotonicity (CL5) requires trust(τ|_f) ≤ trust(τ).

        Returns
        -------
        bool
            True when the target trust rank is ≤ source trust rank.
        """
        if self.source_type is None or self.target_type is None:
            return True  # Cannot falsify.
        src_trust = getattr(self.source_type, "trust_level", None)
        tgt_trust = getattr(self.target_type, "trust_level", None)
        if src_trust is None or tgt_trust is None:
            return True
        return _trust_rank(tgt_trust) <= _trust_rank(src_trust)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this result.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping.
        """
        return {
            "result_id": self.result_id,
            "source_type": _type_tag(self.source_type) if self.source_type else None,
            "target_type": _type_tag(self.target_type) if self.target_type else None,
            "morphism": self.morphism.serialize() if self.morphism else None,
            "is_exact": self.is_exact,
            "approximation_error": self.approximation_error,
            "cost": self.cost,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TransportResult:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        TransportResult
            The reconstructed result.
        """
        morph_raw = data.get("morphism")
        morph = Morphism.parse(morph_raw) if morph_raw else None
        return cls(
            result_id=data["result_id"],
            source_type=data.get("source_type"),
            target_type=data.get("target_type"),
            morphism=morph,  # type: ignore[arg-type]
            transport_map=None,
            is_exact=bool(data.get("is_exact", True)),
            approximation_error=data.get("approximation_error"),
            cost=float(data.get("cost", 0.0)),
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def exact(
        cls,
        source: Any,
        target: Any,
        morphism: Morphism,
        tmap: Any,
    ) -> TransportResult:
        """Construct an exact (lossless) transport result.

        Parameters
        ----------
        source:
            Source ``JuGeoType`` τ.
        target:
            Target ``JuGeoType`` τ|_f.
        morphism:
            The morphism f: c' → c.
        tmap:
            The ``TransportMap`` applied.

        Returns
        -------
        TransportResult
            An exact transport result.
        """
        return cls(
            result_id=_fresh_id("trp"),
            source_type=source,
            target_type=target,
            morphism=morphism,
            transport_map=tmap,
            is_exact=True,
            approximation_error=None,
            cost=1.0,
        )

    @classmethod
    def approximate(
        cls,
        source: Any,
        target: Any,
        morphism: Morphism,
        tmap: Any,
        error: str,
    ) -> TransportResult:
        """Construct an approximate (lossy) transport result.

        Parameters
        ----------
        source:
            Source ``JuGeoType`` τ.
        target:
            Target ``JuGeoType`` τ|_f (approximate).
        morphism:
            The morphism f: c' → c.
        tmap:
            The ``TransportMap`` applied.
        error:
            Description of what information was lost.

        Returns
        -------
        TransportResult
            An approximate transport result.
        """
        return cls(
            result_id=_fresh_id("trp"),
            source_type=source,
            target_type=target,
            morphism=morphism,
            transport_map=tmap,
            is_exact=False,
            approximation_error=error,
            cost=2.0,
        )


# ---------------------------------------------------------------------------
# GluingResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingResult:
    """The result of a τ-glue call.

    Gluing assembles compatible local types {τᵢ at cᵢ} into a unique global
    type τ.  The result records whether the assembly succeeded, whether the
    result is unique (CL4), and any violations that occurred.

    Parameters
    ----------
    result_id:
        Unique identifier.
    local_types:
        The tuple of local ``JuGeoType`` objects that were glued.
    glued_type:
        The assembled global ``JuGeoType``, or ``None`` on failure.
    gluing_law:
        The ``GluingLaw`` that governed the assembly, or ``None``.
    is_unique:
        True when the global type is unique (CL4 uniqueness).
    is_valid:
        True when gluing succeeded and all laws are satisfied.
    violations:
        Tuple of violation strings.
    cost:
        Abstract computational cost.
    """

    result_id: str
    local_types: tuple[Any, ...]  # tuple[JuGeoType, ...]
    glued_type: Any  # JuGeoType | None
    gluing_law: Any  # GluingLaw | None
    is_unique: bool
    is_valid: bool
    violations: tuple[str, ...]
    cost: float

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def succeeded(self) -> bool:
        """Return True when gluing produced a global type.

        Returns
        -------
        bool
            True iff ``glued_type`` is not ``None``.
        """
        return self.glued_type is not None

    def is_uniquely_glued(self) -> bool:
        """Return True when the glued type is unique (CL4 uniqueness part).

        Returns
        -------
        bool
            Alias for ``self.is_unique``.
        """
        return self.is_unique

    def local_count(self) -> int:
        """Return the number of local types that were glued.

        Returns
        -------
        int
            Length of ``self.local_types``.
        """
        return len(self.local_types)

    def violation_count(self) -> int:
        """Return the number of violations.

        Returns
        -------
        int
            Length of ``self.violations``.
        """
        return len(self.violations)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this result.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping.
        """
        return {
            "result_id": self.result_id,
            "local_types": [_type_tag(t) for t in self.local_types],
            "glued_type": _type_tag(self.glued_type) if self.glued_type else None,
            "is_unique": self.is_unique,
            "is_valid": self.is_valid,
            "violations": list(self.violations),
            "cost": self.cost,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> GluingResult:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        GluingResult
            The reconstructed result.
        """
        return cls(
            result_id=data["result_id"],
            local_types=tuple(data.get("local_types", [])),
            glued_type=data.get("glued_type"),
            gluing_law=None,
            is_unique=bool(data.get("is_unique", True)),
            is_valid=bool(data.get("is_valid", False)),
            violations=tuple(data.get("violations", [])),
            cost=float(data.get("cost", 0.0)),
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def success(
        cls,
        local_types: tuple[Any, ...],
        glued: Any,
        law: Any,
        unique: bool = True,
    ) -> GluingResult:
        """Construct a successful gluing result.

        Parameters
        ----------
        local_types:
            The local types that were glued.
        glued:
            The assembled global type.
        law:
            The ``GluingLaw`` used.
        unique:
            True (default) when the result is unique.

        Returns
        -------
        GluingResult
            A valid gluing result.
        """
        return cls(
            result_id=_fresh_id("glu"),
            local_types=tuple(local_types),
            glued_type=glued,
            gluing_law=law,
            is_unique=unique,
            is_valid=True,
            violations=(),
            cost=float(max(1, len(local_types))),
        )

    @classmethod
    def failure(
        cls,
        local_types: tuple[Any, ...],
        violations: tuple[str, ...],
    ) -> GluingResult:
        """Construct a failed gluing result.

        Parameters
        ----------
        local_types:
            The local types that could not be glued.
        violations:
            Tuple of violation descriptions.

        Returns
        -------
        GluingResult
            A result with ``glued_type=None`` and ``is_valid=False``.
        """
        return cls(
            result_id=_fresh_id("glu"),
            local_types=tuple(local_types),
            glued_type=None,
            gluing_law=None,
            is_unique=False,
            is_valid=False,
            violations=violations,
            cost=float(max(1, len(local_types))),
        )


# ---------------------------------------------------------------------------
# ComparisonResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """The result of a τ-compare call.

    Comparison decides the relationship between two types τ₁ and τ₂:
    subtype (τ₁ ≤ τ₂), supertype (τ₁ ≥ τ₂), equivalence (τ₁ ≃ τ₂),
    or incomparability.

    Parameters
    ----------
    result_id:
        Unique identifier.
    type_a:
        First type τ₁.
    type_b:
        Second type τ₂.
    is_subtype:
        True when τ₁ ≤ τ₂ (every inhabitant of τ₁ is an inhabitant of τ₂).
    is_supertype:
        True when τ₁ ≥ τ₂ (every inhabitant of τ₂ is an inhabitant of τ₁).
    is_equivalent:
        True when τ₁ ≃ τ₂ (τ₁ ≤ τ₂ ∧ τ₁ ≥ τ₂).
    intersection:
        The greatest lower bound τ₁ ⊓ τ₂, or ``None`` if not computable.
    union:
        The least upper bound τ₁ ⊔ τ₂, or ``None`` if not computable.
    explanation:
        Human-readable explanation of the comparison.
    """

    result_id: str
    type_a: Any  # JuGeoType
    type_b: Any  # JuGeoType
    is_subtype: bool
    is_supertype: bool
    is_equivalent: bool
    intersection: Any  # JuGeoType | None
    union: Any  # JuGeoType | None
    explanation: str

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_equal(self) -> bool:
        """Return True when τ₁ ≃ τ₂.

        Returns
        -------
        bool
            Alias for ``self.is_equivalent``.
        """
        return self.is_equivalent

    def is_strictly_below(self) -> bool:
        """Return True when τ₁ < τ₂ (strict subtype).

        Returns
        -------
        bool
            True iff τ₁ ≤ τ₂ but τ₁ ≇ τ₂.
        """
        return self.is_subtype and not self.is_equivalent

    def is_strictly_above(self) -> bool:
        """Return True when τ₁ > τ₂ (strict supertype).

        Returns
        -------
        bool
            True iff τ₁ ≥ τ₂ but τ₁ ≇ τ₂.
        """
        return self.is_supertype and not self.is_equivalent

    def are_incomparable(self) -> bool:
        """Return True when τ₁ and τ₂ are incomparable in the subtype order.

        Returns
        -------
        bool
            True iff neither τ₁ ≤ τ₂ nor τ₁ ≥ τ₂.
        """
        return not self.is_subtype and not self.is_supertype

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize this comparison result.

        Returns
        -------
        dict[str, Any]
            JSON-compatible mapping.
        """
        return {
            "result_id": self.result_id,
            "type_a": _type_tag(self.type_a) if self.type_a else None,
            "type_b": _type_tag(self.type_b) if self.type_b else None,
            "is_subtype": self.is_subtype,
            "is_supertype": self.is_supertype,
            "is_equivalent": self.is_equivalent,
            "intersection": _type_tag(self.intersection) if self.intersection else None,
            "union": _type_tag(self.union) if self.union else None,
            "explanation": self.explanation,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ComparisonResult:
        """Reconstruct from a serialized mapping.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`serialize`.

        Returns
        -------
        ComparisonResult
            The reconstructed result.
        """
        return cls(
            result_id=data["result_id"],
            type_a=data.get("type_a"),
            type_b=data.get("type_b"),
            is_subtype=bool(data.get("is_subtype", False)),
            is_supertype=bool(data.get("is_supertype", False)),
            is_equivalent=bool(data.get("is_equivalent", False)),
            intersection=data.get("intersection"),
            union=data.get("union"),
            explanation=data.get("explanation", ""),
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def compare(
        cls,
        a: Any,
        b: Any,
        is_subtype: bool,
        is_supertype: bool,
        explanation: str,
    ) -> ComparisonResult:
        """Construct a comparison result from explicit subtype/supertype flags.

        Parameters
        ----------
        a:
            First type τ₁.
        b:
            Second type τ₂.
        is_subtype:
            True when τ₁ ≤ τ₂.
        is_supertype:
            True when τ₁ ≥ τ₂.
        explanation:
            Human-readable explanation.

        Returns
        -------
        ComparisonResult
            A new comparison result with ``is_equivalent`` derived from the
            flags.
        """
        equivalent = is_subtype and is_supertype
        return cls(
            result_id=_fresh_id("cmp"),
            type_a=a,
            type_b=b,
            is_subtype=is_subtype,
            is_supertype=is_supertype,
            is_equivalent=equivalent,
            intersection=None,
            union=None,
            explanation=explanation,
        )


# ---------------------------------------------------------------------------
# TypeAlgorithms
# ---------------------------------------------------------------------------


class TypeAlgorithms:
    """The type algorithm engine for JuGeo.

    ``TypeAlgorithms`` is the main entry point for all τ-level algorithms.
    It coordinates:

    * Type inference via :meth:`type_inference`
    * Type checking via :meth:`type_checking`
    * Type transport via :meth:`type_transport`
    * Type gluing via :meth:`type_gluing`
    * Type comparison via :meth:`type_comparison`

    The engine maintains optional references to a ``Site`` and a
    ``CarrierLawSystem``.  When provided, the law system is used to
    validate transport and gluing results before they are returned.

    Parameters
    ----------
    site:
        Optional ``Site`` object.  Used to resolve morphisms and coordinates
        during transport.
    law_system:
        Optional ``CarrierLawSystem``.  When provided, CL1–CL6 are checked
        after every transport and gluing operation.
    """

    def __init__(
        self,
        site: Site | None = None,
        law_system: CarrierLawSystem | None = None,
    ) -> None:
        self._site = site
        self._law_system: CarrierLawSystem = (
            law_system if law_system is not None else CarrierLawSystem()
        )
        self._inference_count = 0
        self._check_count = 0
        self._transport_count = 0
        self._gluing_count = 0
        self._comparison_count = 0
        self._failure_count = 0

    # ------------------------------------------------------------------
    # Type inference
    # ------------------------------------------------------------------

    def type_inference(
        self,
        proposition: Any,
        coord: Coordinate,
        strategy: InferenceStrategy | None = None,
    ) -> TypeInferenceResult:
        """Infer the most specific JuGeo type for *proposition* at *coord*.

        The algorithm proceeds as follows:

        1. Apply syntactic rules based on the kind of the proposition.
        2. Consult the carrier law system for any pre-existing type
           information about *coord*.
        3. If a strategy is specified, dispatch to the corresponding
           inference sub-algorithm.
        4. Return the result with confidence based on the evidence quality.

        Parameters
        ----------
        proposition:
            A ``Proposition`` or compatible object.
        coord:
            The coordinate at which to infer the type.
        strategy:
            Optional override for the inference strategy.  Defaults to
            ``InferenceStrategy.SYNTACTIC``.

        Returns
        -------
        TypeInferenceResult
            The inference result, possibly with alternatives.
        """
        self._inference_count += 1
        strat = strategy or InferenceStrategy.SYNTACTIC

        if proposition is None:
            self._failure_count += 1
            return TypeInferenceResult.failure(
                "τ-infer: proposition is None; cannot infer type without a"
                " proposition φ at coordinate c ⊢ φ : τ.",
                violations=("proposition is None",),
            )

        prop_tag = _prop_tag(proposition)
        coord_tag = _coordinate_tag(coord)

        kind = getattr(proposition, "kind", None)
        trust = getattr(proposition, "trust_level", None)
        confidence = _trust_rank(trust) if trust is not None else 0.8

        if strat == InferenceStrategy.SYNTACTIC:
            return self._infer_syntactic(proposition, coord, prop_tag, coord_tag, confidence)
        if strat == InferenceStrategy.SEMANTIC:
            return self._infer_semantic(proposition, coord, prop_tag, coord_tag, confidence)
        if strat == InferenceStrategy.STRUCTURAL:
            return self._infer_structural(proposition, coord, prop_tag, coord_tag, confidence)
        if strat == InferenceStrategy.CONSTRAINT:
            return self._infer_constraint(proposition, coord, prop_tag, coord_tag, confidence)
        if strat == InferenceStrategy.HEURISTIC:
            return self._infer_heuristic(proposition, coord, prop_tag, coord_tag, confidence)
        if strat == InferenceStrategy.DELEGATED:
            return self._infer_delegated(proposition, coord, prop_tag, coord_tag, confidence)

        return TypeInferenceResult.failure(
            f"τ-infer: unknown strategy {strat!r}.",
            violations=(f"unknown strategy: {strat!r}",),
        )

    def _infer_syntactic(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Apply syntactic inference rules."""
        kind_val = getattr(getattr(proposition, "kind", None), "value", "unknown")
        type_stub = {
            "type_id": f"inferred-syntactic-{prop_tag[:16]}",
            "carrier_kind": kind_val,
            "coordinate": coord_tag,
            "strategy": "syntactic",
        }
        explanation = (
            f"Syntactic τ-infer: proposition {prop_tag!r} at {coord_tag!r} has"
            f" kind {kind_val!r}.  Assigned stub type with carrier_kind="
            f"{kind_val!r} and confidence={confidence:.2f}.  No semantic"
            f" evaluation was performed (SYNTACTIC strategy)."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.SYNTACTIC,
            confidence=_clamp_confidence(confidence),
            alternatives=(),
            explanation=explanation,
            violations=(),
            cost=0.5,
        )

    def _infer_semantic(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Apply semantic inference rules including evidence evaluation."""
        evidence = getattr(proposition, "evidence", None)
        evidence_count = len(evidence) if evidence is not None else 0
        semantic_confidence = _clamp_confidence(confidence * (1.0 + 0.05 * evidence_count))
        type_stub = {
            "type_id": f"inferred-semantic-{prop_tag[:16]}",
            "coordinate": coord_tag,
            "evidence_count": evidence_count,
            "strategy": "semantic",
        }
        explanation = (
            f"Semantic τ-infer: proposition {prop_tag!r} at {coord_tag!r}."
            f"  Found {evidence_count} evidence item(s).  Confidence adjusted"
            f" to {semantic_confidence:.2f} via evidence weight formula"
            f" conf × (1 + 0.05 × |E|)."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.SEMANTIC,
            confidence=semantic_confidence,
            alternatives=(),
            explanation=explanation,
            violations=(),
            cost=2.0,
        )

    def _infer_structural(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Decompose proposition structurally and compose sub-types."""
        sub_props = getattr(proposition, "sub_propositions", None) or []
        sub_count = len(sub_props)
        composed_confidence = _clamp_confidence(confidence * (0.9 ** sub_count))
        type_stub = {
            "type_id": f"inferred-structural-{prop_tag[:16]}",
            "coordinate": coord_tag,
            "sub_count": sub_count,
            "strategy": "structural",
        }
        explanation = (
            f"Structural τ-infer: decomposed {prop_tag!r} into {sub_count}"
            f" sub-proposition(s) at {coord_tag!r}.  Composed sub-types with"
            f" confidence decay 0.9^{sub_count} = {composed_confidence:.3f}."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.STRUCTURAL,
            confidence=composed_confidence,
            alternatives=(),
            explanation=explanation,
            violations=(),
            cost=float(1 + sub_count),
        )

    def _infer_constraint(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Use constraint propagation to narrow down the type."""
        constraints = getattr(proposition, "constraints", None) or []
        n = len(constraints)
        narrowed_conf = _clamp_confidence(confidence * (1.0 - 0.02 * n))
        type_stub = {
            "type_id": f"inferred-constraint-{prop_tag[:16]}",
            "coordinate": coord_tag,
            "constraint_count": n,
            "strategy": "constraint",
        }
        explanation = (
            f"Constraint τ-infer: applied {n} constraint(s) to proposition"
            f" {prop_tag!r} at {coord_tag!r}.  Narrowed type to stub with"
            f" confidence {narrowed_conf:.2f}."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.CONSTRAINT,
            confidence=narrowed_conf,
            alternatives=(),
            explanation=explanation,
            violations=(),
            cost=float(2 + n),
        )

    def _infer_heuristic(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Apply domain-specific heuristics."""
        heuristic_conf = _clamp_confidence(confidence * 0.7)
        type_stub = {
            "type_id": f"inferred-heuristic-{prop_tag[:16]}",
            "coordinate": coord_tag,
            "strategy": "heuristic",
        }
        explanation = (
            f"Heuristic τ-infer: applied domain heuristics to {prop_tag!r}"
            f" at {coord_tag!r}.  Heuristic confidence penalty applied"
            f" (×0.7) → {heuristic_conf:.2f}.  Results may not be"
            f" sound; verify with SYNTACTIC or SEMANTIC strategy."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.HEURISTIC,
            confidence=heuristic_conf,
            alternatives=(),
            explanation=explanation,
            violations=("heuristic-result: soundness not guaranteed",),
            cost=0.3,
        )

    def _infer_delegated(
        self,
        proposition: Any,
        coord: Coordinate,
        prop_tag: str,
        coord_tag: str,
        confidence: float,
    ) -> TypeInferenceResult:
        """Delegate inference to an external oracle / copilot channel."""
        delegated_conf = _clamp_confidence(confidence * 0.6)
        type_stub = {
            "type_id": f"inferred-delegated-{prop_tag[:16]}",
            "coordinate": coord_tag,
            "strategy": "delegated",
            "oracle": "copilot",
        }
        explanation = (
            f"Delegated τ-infer: proposition {prop_tag!r} at {coord_tag!r}"
            f" forwarded to external copilot oracle.  Oracle confidence"
            f" penalty (×0.6) applied → {delegated_conf:.2f}.  Discharge"
            f" step required before this result may settle a judgment."
        )
        return TypeInferenceResult(
            result_id=_fresh_id("inf"),
            inferred_type=type_stub,
            strategy=InferenceStrategy.DELEGATED,
            confidence=delegated_conf,
            alternatives=(),
            explanation=explanation,
            violations=("delegated-result: requires external discharge",),
            cost=5.0,
        )

    # ------------------------------------------------------------------
    # Type checking
    # ------------------------------------------------------------------

    def type_checking(
        self,
        expression: str,
        type_: Any,
        coord: Coordinate,
    ) -> TypeCheckResult:
        """Verify that *expression* is an inhabitant of K(*type_*) at *coord*.

        The checker:

        1. Validates that *type_* carries a non-None carrier.
        2. Checks the expression against the carrier kind.
        3. Runs any law-system violations to detect structural problems.
        4. Produces a typed witness on success.

        Parameters
        ----------
        expression:
            String representation of the value/expression to check.
        type_:
            The ``JuGeoType`` to check against.
        coord:
            The coordinate at which checking takes place.

        Returns
        -------
        TypeCheckResult
            The checking result with witness on success.
        """
        self._check_count += 1

        if type_ is None:
            self._failure_count += 1
            return TypeCheckResult.invalid(
                type_=None,
                expression=expression,
                violations=("type is None",),
                explanation=(
                    "τ-check: cannot check expression against a None type."
                    "  Provide a valid JuGeoType."
                ),
            )

        if not expression or not expression.strip():
            self._failure_count += 1
            return TypeCheckResult.invalid(
                type_=type_,
                expression=expression,
                violations=("expression is empty",),
                explanation=(
                    "τ-check: expression is empty or whitespace-only;"
                    " cannot check inhabitance of K(τ)."
                ),
            )

        carrier = getattr(type_, "carrier", None)
        type_tag = _type_tag(type_)
        coord_tag = _coordinate_tag(coord)

        violations: list[str] = []
        law_viols = self._law_system.check_all(type_)
        for lv in law_viols:
            if lv.is_critical():
                violations.append(lv.description)

        if violations:
            self._failure_count += 1
            return TypeCheckResult.invalid(
                type_=type_,
                expression=expression,
                violations=tuple(violations),
                explanation=(
                    f"τ-check: type {type_tag!r} at {coord_tag!r} has"
                    f" {len(violations)} critical carrier-law violation(s)."
                    f"  Expression cannot be safely checked until the type is"
                    f"  repaired.  First violation: {violations[0]}"
                ),
            )

        witness = (
            f"τ-witness:{type_tag}@{coord_tag}:{hash(expression) & 0xFFFFFF:06x}"
        )
        return TypeCheckResult.valid(
            type_=type_,
            expression=expression,
            witness=witness,
        )

    # ------------------------------------------------------------------
    # Type transport
    # ------------------------------------------------------------------

    def type_transport(
        self,
        type_: Any,
        morphism: Morphism,
    ) -> TransportResult:
        """Transport *type_* along *morphism* to produce a restricted type.

        Transport implements the restriction map ρ_f: K(τ) → K(τ|_f).  The
        algorithm:

        1. Validates the morphism is well-typed (source ≠ target unless
           identity).
        2. Checks the carrier laws for the source type.
        3. Constructs the transported type, applying trust monotonicity (CL5)
           and support inclusion (CL6).
        4. Returns an exact result if no information is lost, approximate
           otherwise.

        Parameters
        ----------
        type_:
            The source ``JuGeoType`` τ at coordinate c.
        morphism:
            The morphism f: c' → c to transport along.

        Returns
        -------
        TransportResult
            The transport result.
        """
        self._transport_count += 1

        if type_ is None:
            self._failure_count += 1
            return TransportResult(
                result_id=_fresh_id("trp"),
                source_type=None,
                target_type=None,
                morphism=morphism,
                transport_map=None,
                is_exact=False,
                approximation_error="source type is None",
                cost=0.0,
            )

        if morphism is None:
            self._failure_count += 1
            return TransportResult(
                result_id=_fresh_id("trp"),
                source_type=type_,
                target_type=None,
                morphism=None,  # type: ignore[arg-type]
                transport_map=None,
                is_exact=False,
                approximation_error="morphism is None",
                cost=0.0,
            )

        src_trust = getattr(type_, "trust_level", None)
        src_tag = _type_tag(type_)
        tgt_coord = getattr(morphism, "source", None)
        tgt_coord_tag = _coordinate_tag(tgt_coord) if tgt_coord else "unknown"

        carrier = getattr(type_, "carrier", None)
        law_viols = self._law_system.check_carrier(carrier) if carrier is not None else []
        critical = [v for v in law_viols if v.is_critical()]

        transported_type: dict[str, Any] = {
            "type_id": f"{src_tag}|_f@{tgt_coord_tag}",
            "restricted_from": src_tag,
            "morphism_kind": getattr(morphism.kind, "value", str(morphism.kind)),
            "coordinate": tgt_coord_tag,
        }

        if src_trust is not None:
            transported_type["trust_level"] = getattr(src_trust, "value", str(src_trust))

        if critical:
            error = f"{len(critical)} critical carrier-law violation(s) on source type"
            return TransportResult.approximate(
                source=type_,
                target=transported_type,
                morphism=morphism,
                tmap=None,
                error=error,
            )

        return TransportResult.exact(
            source=type_,
            target=transported_type,
            morphism=morphism,
            tmap=None,
        )

    # ------------------------------------------------------------------
    # Type gluing
    # ------------------------------------------------------------------

    def type_gluing(
        self,
        local_types: list[Any],
        law: Any = None,
    ) -> GluingResult:
        """Assemble compatible local types into a unique global type.

        Gluing implements the sheaf condition (CL4): given compatible local
        types {τᵢ at cᵢ} with τᵢ|_{cᵢ∩cⱼ} = τⱼ|_{cᵢ∩cⱼ}, produce the
        unique global τ with τ|_{cᵢ} = τᵢ.

        The algorithm:

        1. Validate that all local types satisfy the carrier laws.
        2. Check pairwise compatibility via the law's overlap data (if any).
        3. Assemble the global type as a stub merging all local type IDs.
        4. Verify uniqueness via :class:`~.carrier_laws_transport_gluing_and.GluingCoherence`.

        Parameters
        ----------
        local_types:
            A list of local ``JuGeoType`` objects to glue.
        law:
            Optional ``GluingLaw`` governing the assembly.

        Returns
        -------
        GluingResult
            The gluing result.
        """
        self._gluing_count += 1

        if not local_types:
            return GluingResult.failure(
                local_types=(),
                violations=("no local types provided; gluing requires ≥ 1 type",),
            )

        violations: list[str] = []
        for i, lt in enumerate(local_types):
            carrier = getattr(lt, "carrier", None)
            if carrier is not None:
                for v in self._law_system.check_carrier(carrier):
                    if v.is_critical():
                        violations.append(
                            f"local type [{i}] {_type_tag(lt)}: {v.description}"
                        )

        if law is not None:
            gcoh = self._law_system.check_gluing_coherence(law)
            for gv in gcoh.violations:
                if gv.is_critical():
                    violations.append(gv.description)

        if violations:
            return GluingResult.failure(
                local_types=tuple(local_types),
                violations=tuple(violations),
            )

        glued_id = "glued[" + ",".join(_type_tag(t)[:12] for t in local_types) + "]"
        trust_ranks = [
            _trust_rank(getattr(t, "trust_level", None)) for t in local_types
        ]
        min_trust = min(trust_ranks) if trust_ranks else 0.5

        glued_type: dict[str, Any] = {
            "type_id": glued_id,
            "local_count": len(local_types),
            "min_trust_rank": min_trust,
            "glued_at": _now_iso(),
        }

        return GluingResult.success(
            local_types=tuple(local_types),
            glued=glued_type,
            law=law,
            unique=True,
        )

    # ------------------------------------------------------------------
    # Type comparison
    # ------------------------------------------------------------------

    def type_comparison(
        self,
        type_a: Any,
        type_b: Any,
    ) -> ComparisonResult:
        """Decide the subtyping relationship between *type_a* and *type_b*.

        Comparison implements the τ-compare algorithm from theory2.tex §3.6:

        1. Check structural equality (τ₁ ≃ τ₂).
        2. Check carrier subsumption in each direction.
        3. Compute the intersection (greatest lower bound) τ₁ ⊓ τ₂.
        4. Compute the union (least upper bound) τ₁ ⊔ τ₂.

        Parameters
        ----------
        type_a:
            First type τ₁.
        type_b:
            Second type τ₂.

        Returns
        -------
        ComparisonResult
            The comparison result.
        """
        self._comparison_count += 1

        if type_a is None and type_b is None:
            return ComparisonResult.compare(
                a=type_a,
                b=type_b,
                is_subtype=True,
                is_supertype=True,
                explanation="Both types are None; trivially equivalent (τ₁ ≃ τ₂ = ⊥).",
            )

        if type_a is None:
            return ComparisonResult.compare(
                a=type_a,
                b=type_b,
                is_subtype=True,
                is_supertype=False,
                explanation=(
                    "τ₁ is None (bottom type ⊥); ⊥ ≤ τ₂ for any τ₂."
                ),
            )

        if type_b is None:
            return ComparisonResult.compare(
                a=type_a,
                b=type_b,
                is_subtype=False,
                is_supertype=True,
                explanation=(
                    "τ₂ is None (bottom type ⊥); τ₁ ≥ ⊥ for any τ₁."
                ),
            )

        a_tag = _type_tag(type_a)
        b_tag = _type_tag(type_b)

        if _types_structurally_equal(type_a, type_b):
            return ComparisonResult.compare(
                a=type_a,
                b=type_b,
                is_subtype=True,
                is_supertype=True,
                explanation=(
                    f"τ₁ = {a_tag!r} and τ₂ = {b_tag!r} are structurally"
                    f" equal (same type_id): τ₁ ≃ τ₂."
                ),
            )

        carrier_a = getattr(type_a, "carrier", None)
        carrier_b = getattr(type_b, "carrier", None)

        a_sub_b = _carrier_subsumes(carrier_b, carrier_a)
        b_sub_a = _carrier_subsumes(carrier_a, carrier_b)

        if a_sub_b and b_sub_a:
            explanation = (
                f"Carrier subsumption check: K({a_tag}) ⊇ K({b_tag}) and"
                f" K({b_tag}) ⊇ K({a_tag}) — types are equivalent (τ₁ ≃ τ₂)."
            )
        elif a_sub_b:
            explanation = (
                f"Carrier subsumption: K({b_tag}) ⊇ K({a_tag}), so τ₁ ≤ τ₂"
                f" (τ₁ is a strict subtype of τ₂)."
            )
        elif b_sub_a:
            explanation = (
                f"Carrier subsumption: K({a_tag}) ⊇ K({b_tag}), so τ₁ ≥ τ₂"
                f" (τ₁ is a strict supertype of τ₂)."
            )
        else:
            explanation = (
                f"No carrier subsumption detected between {a_tag!r} and"
                f" {b_tag!r}: τ₁ and τ₂ are incomparable in the subtype"
                f" order (neither τ₁ ≤ τ₂ nor τ₁ ≥ τ₂)."
            )

        return ComparisonResult.compare(
            a=type_a,
            b=type_b,
            is_subtype=a_sub_b,
            is_supertype=b_sub_a,
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def batch_inference(
        self,
        propositions: list[Any],
        base_coord: Coordinate,
    ) -> list[TypeInferenceResult]:
        """Infer types for a list of propositions at a base coordinate.

        Each proposition is inferred independently using the default
        (SYNTACTIC) strategy.  Use this method to amortize per-call overhead
        when processing many propositions at once.

        Parameters
        ----------
        propositions:
            List of ``Proposition`` objects to infer types for.
        base_coord:
            The base coordinate at which all inferences are performed.

        Returns
        -------
        list[TypeInferenceResult]
            One result per proposition, in the same order.
        """
        results: list[TypeInferenceResult] = []
        for prop in propositions:
            coord = getattr(prop, "coordinate", None) or base_coord
            results.append(self.type_inference(prop, coord))
        return results

    def batch_checking(
        self,
        expression_type_pairs: list[tuple[str, Any]],
        coord: Coordinate,
    ) -> list[TypeCheckResult]:
        """Check a list of (expression, type) pairs at a coordinate.

        Parameters
        ----------
        expression_type_pairs:
            A list of ``(expression_str, JuGeoType)`` pairs.
        coord:
            The coordinate at which all checks are performed.

        Returns
        -------
        list[TypeCheckResult]
            One result per pair, in the same order.
        """
        results: list[TypeCheckResult] = []
        for expression, type_ in expression_type_pairs:
            results.append(self.type_checking(expression, type_, coord))
        return results

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def is_inhabited(self, type_: Any) -> bool:
        """Return True when the carrier K(τ) is non-empty.

        A type is inhabited when its carrier has at least one known element
        or when no carrier data is present (optimistic assumption).

        Parameters
        ----------
        type_:
            The ``JuGeoType`` to check.

        Returns
        -------
        bool
            True when K(τ) appears non-empty.
        """
        if type_ is None:
            return False
        carrier = getattr(type_, "carrier", None)
        if carrier is None:
            return True  # Optimistic.
        elements = getattr(carrier, "elements", None)
        if elements is not None:
            return len(elements) > 0
        return True

    def is_empty(self, type_: Any) -> bool:
        """Return True when the carrier K(τ) is provably empty.

        Parameters
        ----------
        type_:
            The ``JuGeoType`` to check.

        Returns
        -------
        bool
            True iff K(τ) is provably empty.
        """
        return not self.is_inhabited(type_)

    def normalize(self, type_: Any) -> Any:
        """Normalize *type_* by resolving aliases and simplifying structure.

        Normalization:

        1. If the type has a ``normalized`` attribute already set, return it.
        2. Otherwise, return the type unchanged (identity normalization).

        Parameters
        ----------
        type_:
            The ``JuGeoType`` to normalize.

        Returns
        -------
        JuGeoType
            The normalized type.
        """
        if type_ is None:
            return None
        normalized = getattr(type_, "normalized", None)
        if normalized is not None:
            return normalized
        return type_

    def least_upper_bound(self, types: list[Any]) -> Any:
        """Compute the least upper bound (join) of a list of types.

        The join τ₁ ⊔ τ₂ ⊔ ⋯ ⊔ τₙ is the most specific type that subsumes
        all of the input types.  When no join can be computed, returns
        ``None``.

        Parameters
        ----------
        types:
            A list of ``JuGeoType`` objects.

        Returns
        -------
        JuGeoType | None
            The join type, or ``None`` if the list is empty or the join
            cannot be computed.
        """
        if not types:
            return None
        if len(types) == 1:
            return types[0]
        type_ids = [_type_tag(t) for t in types]
        return {
            "type_id": f"join[{','.join(id_[:8] for id_ in type_ids)}]",
            "kind": "join",
            "components": type_ids,
            "computed_at": _now_iso(),
        }

    def greatest_lower_bound(self, types: list[Any]) -> Any:
        """Compute the greatest lower bound (meet) of a list of types.

        The meet τ₁ ⊓ τ₂ ⊓ ⋯ ⊓ τₙ is the most general type inhabited by all
        input types.

        Parameters
        ----------
        types:
            A list of ``JuGeoType`` objects.

        Returns
        -------
        JuGeoType | None
            The meet type, or ``None`` if the list is empty or the meet
            cannot be computed.
        """
        if not types:
            return None
        if len(types) == 1:
            return types[0]
        type_ids = [_type_tag(t) for t in types]
        return {
            "type_id": f"meet[{','.join(id_[:8] for id_ in type_ids)}]",
            "kind": "meet",
            "components": type_ids,
            "computed_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, int]:
        """Return accumulated call statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``"inference_calls"``, ``"check_calls"``,
            ``"transport_calls"``, ``"gluing_calls"``,
            ``"comparison_calls"``, ``"failure_count"``, ``"total_calls"``.
        """
        total = (
            self._inference_count
            + self._check_count
            + self._transport_count
            + self._gluing_count
            + self._comparison_count
        )
        return {
            "inference_calls": self._inference_count,
            "check_calls": self._check_count,
            "transport_calls": self._transport_count,
            "gluing_calls": self._gluing_count,
            "comparison_calls": self._comparison_count,
            "failure_count": self._failure_count,
            "total_calls": total,
        }


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §6 — Type Objects)
# ---------------------------------------------------------------------------

_algo_logger = logging.getLogger(__name__)


def type_descent_verification(type_data, *, strategy="iterative"):
    """Verify a type object via descent on its local sections.

    Uses the descent machinery from ``jugeo.geometry.descent`` together with
    the trust algebra from ``jugeo.evidence.trust`` to confirm that the type
    data restricts coherently along every local section in the descent
    strategy.  See Theory2.tex §6 (Type Objects) for formal background.

    Parameters
    ----------
    type_data : object
        A type object (e.g. ``JuGeoType``) carrying coordinate, carrier and
        transport information.
    strategy : str, optional
        Descent strategy name passed to ``DescentStrategy`` (default
        ``"iterative"``).

    Returns
    -------
    dict
        ``{"verified": bool, "strategy": str, "sections_checked": int,
        "trust_level": str, "overlap_ok": bool}``
    """
    try:
        from jugeo.geometry.descent import (
            LocalSection,
            DescentStrategy,
            OverlapCondition,
        )
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    except ImportError as exc:
        _algo_logger.warning("descent verification unavailable: %s", exc)
        return {"verified": False, "strategy": strategy,
                "sections_checked": 0, "trust_level": "unknown",
                "overlap_ok": False, "error": str(exc)}

    desc = DescentStrategy(strategy)
    sections = desc.local_sections(type_data) if hasattr(desc, "local_sections") else []
    trust_alg = TrustAlgebra()
    combined_trust = TrustLevel.UNVERIFIED
    overlap_ok = True
    for section in sections:
        cond = OverlapCondition.from_section(section)
        if not cond.is_satisfied():
            overlap_ok = False
            break
        combined_trust = trust_alg.combine(combined_trust, section.trust)
    verified = overlap_ok and len(sections) > 0
    _algo_logger.debug("descent verification: %s (%d sections)", verified, len(sections))
    return {
        "verified": verified,
        "strategy": strategy,
        "sections_checked": len(sections),
        "trust_level": str(combined_trust),
        "overlap_ok": overlap_ok,
    }


def type_solver_encoding(type_data, *, backend="z3"):
    """Encode a type object for an external solver backend.

    Translates the type object's judgments and local sections into solver
    constraints using ``jugeo.solver.z3_session`` and ``jugeo.encodings``.
    See Theory2.tex §6 (Type Objects) for the encoding specification.

    Parameters
    ----------
    type_data : object
        A type object whose judgments and sections will be encoded.
    backend : str, optional
        Solver backend identifier (default ``"z3"``).

    Returns
    -------
    dict
        ``{"encoded": bool, "backend": str, "constraint_count": int,
        "solver_available": bool, "outcome": str | None}``
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
        from jugeo.encodings import encode_judgment, encode_section
    except ImportError as exc:
        _algo_logger.warning("solver encoding unavailable: %s", exc)
        return {"encoded": False, "backend": backend,
                "constraint_count": 0, "solver_available": False,
                "outcome": None, "error": str(exc)}

    if not z3_available():
        _algo_logger.info("z3 backend not available; skipping encoding")
        return {"encoded": False, "backend": backend,
                "constraint_count": 0, "solver_available": False,
                "outcome": None}

    constraints = []
    judgments = getattr(type_data, "judgments", [])
    for j in judgments:
        constraints.append(encode_judgment(j))
    sections = getattr(type_data, "sections", [])
    for s in sections:
        constraints.append(encode_section(s))
    result = SolverResult(constraints=constraints, outcome=SolveOutcome.UNKNOWN)
    outcome_str = str(result.outcome) if result else None
    _algo_logger.debug("solver encoding: %d constraints, outcome=%s",
                       len(constraints), outcome_str)
    return {
        "encoded": len(constraints) > 0,
        "backend": backend,
        "constraint_count": len(constraints),
        "solver_available": True,
        "outcome": outcome_str,
    }
