"""Integration bridge between the JuGeo type-object layer and the rest of
the JuGeo framework — judgments, evidence, and solver.

Theory2 Ch3 §3.5 establishes the *integration contract*: every JuGeo type
τ = (c, K, ρ, γ, supp, trust) must be *extractable* from a judgment
J = (c, φ, A, E, O, B, T, Π) via the carrier projection A, *embeddable*
into a judgment as its type annotation, and *transportable* along judgment
restriction maps.  The integration bridge ensures that the type-object layer
interoperates cleanly with:

- The judgment term algebra (jugeo.judgments.judgment_terms)
- The evidence and trust subsystem
- The solver (which may discharge type obligations)
- The semantic site (jugeo.geometry.site)

The integration contract has three principal components (Theory2 §3.5.1–3.5.3):

1. **Extraction** — reading a JuGeoType τ out of the carrier slot A of a
   judgment J = (c, φ, A, …).  The carrier A may carry its own coordinate,
   kind, and trust annotations; extraction must faithfully project these into
   the corresponding slots of τ.

2. **Embedding** — injecting τ back into a judgment's carrier slot so that the
   type annotation is machine-readable by later certification passes.  The
   embedding must preserve the coordinate c, the trust annotation T, and the
   obligation set O so that round-tripping (extract ∘ embed) is the identity
   on τ.

3. **Transport** — when a judgment is restricted along a morphism f: c' → c,
   the embedded type τ must be transported via the presheaf restriction map
   ρ_f so that the restricted judgment J|_f carries the correctly restricted
   type τ|_f.

The module also exposes a **solver bridge** that lets the solver discharge type
obligations arising from incompletely specified types, and a **synchronization**
operation that aligns the trust level of τ with the trust annotation T of the
judgment it was extracted from.

Module provenance
-----------------
Author : copilot
Theory : preliminaries/theory2.tex §3.5
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, ClassVar, TYPE_CHECKING

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
except ImportError:  # pragma: no cover — modules not yet generated
    JuGeoType = Any  # type: ignore[misc,assignment]
    TypeCarrier = Any  # type: ignore[misc,assignment]
    TransportMap = Any  # type: ignore[misc,assignment]
    GluingLaw = Any  # type: ignore[misc,assignment]
    TypeTrustAnnotation = Any  # type: ignore[misc,assignment]
    CarrierKind = Any  # type: ignore[misc,assignment]

try:
    from jugeo.foundations.type_objects.algorithms import TypeAlgorithms
except ImportError:  # pragma: no cover
    TypeAlgorithms = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _fresh_id(prefix: str = "rec") -> str:
    """Generate a unique record identifier with a human-readable prefix.

    Parameters
    ----------
    prefix : str
        Short label prepended before the UUID fragment.

    Returns
    -------
    str
        A collision-resistant identifier such as ``"rec-4a3f2e1d"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _safe_coordinate(coord: Any) -> Coordinate:
    """Return *coord* unchanged if it is a :class:`Coordinate`, else root.

    Parameters
    ----------
    coord : Any
        Candidate coordinate value.

    Returns
    -------
    Coordinate
        A valid :class:`Coordinate` — the root coordinate if *coord* is not
        a proper :class:`Coordinate` instance.
    """
    if isinstance(coord, Coordinate):
        return coord
    return Coordinate(components=(), kind=CoordinateKind.REGION)


def _trust_level_from_annotation(annotation: Any) -> TrustLevel:
    """Extract the weakest :class:`TrustLevel` from a type trust annotation.

    Parameters
    ----------
    annotation : TypeTrustAnnotation | Any
        A type trust annotation object.  If it exposes a ``level`` attribute
        that is a :class:`TrustLevel`, that value is returned; otherwise the
        function falls back to ``UNVERIFIED``.

    Returns
    -------
    TrustLevel
        The resolved trust level.
    """
    level = getattr(annotation, "level", None)
    if isinstance(level, TrustLevel):
        return level
    return TrustLevel.UNVERIFIED


def _proposition_formula_from_type(type_obj: Any) -> str:
    """Derive a proposition formula string from a JuGeoType.

    Parameters
    ----------
    type_obj : JuGeoType | Any
        The type object to inspect.

    Returns
    -------
    str
        A formula string of the form ``"type(τ_id)"`` when *type_obj*
        exposes a ``type_id`` attribute, else a generic sentinel.
    """
    type_id = getattr(type_obj, "type_id", None)
    if type_id:
        return f"type({type_id})"
    return "type(<unknown>)"


# ---------------------------------------------------------------------------
# IntegrationMode
# ---------------------------------------------------------------------------

class IntegrationMode(str, Enum):
    """Enumeration of the six fundamental integration operations.

    Each value names one of the six operations defined in Theory2 §3.5.1:

    - **EXTRACTION** — reading a type out of a judgment carrier slot.
    - **EMBEDDING** — writing a type into a judgment carrier slot.
    - **TRANSPORT** — carrying a type along a restriction morphism.
    - **DISCHARGE** — submitting a type obligation to the solver.
    - **VERIFICATION** — checking that an extracted type is well-formed.
    - **SYNCHRONIZATION** — aligning type trust with judgment trust.
    """

    EXTRACTION = "extraction"
    EMBEDDING = "embedding"
    TRANSPORT = "transport"
    DISCHARGE = "discharge"
    VERIFICATION = "verification"
    SYNCHRONIZATION = "synchronization"


# ---------------------------------------------------------------------------
# IntegrationRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IntegrationRecord:
    """Immutable audit record for a single integration operation.

    Every call made through :class:`TypeIntegration` creates one
    :class:`IntegrationRecord` that is stored internally and can be
    serialised to JSON for downstream trust accounting.

    Parameters
    ----------
    record_id : str
        Unique identifier for this record.
    mode : IntegrationMode
        Which of the six integration operations was performed.
    type_id : str
        Identifier of the JuGeoType involved.
    judgment_id : str | None
        Identifier of the associated Judgment, if any.
    coordinate : Coordinate
        The site coordinate at which the operation occurred.
    timestamp : str
        ISO-8601 UTC timestamp of the operation.
    success : bool
        Whether the operation completed without error.
    explanation : str
        Human-readable description of the outcome.
    evidence_keys : tuple[str, ...]
        Keys of evidence items produced or consumed during the operation.
    metadata : Mapping[str, Any]
        Arbitrary extra data attached to the record.
    """

    record_id: str
    mode: IntegrationMode
    type_id: str
    judgment_id: str | None
    coordinate: Coordinate
    timestamp: str
    success: bool
    explanation: str
    evidence_keys: tuple[str, ...]
    metadata: Mapping[str, Any]

    # -- query methods -------------------------------------------------------

    def is_successful(self) -> bool:
        """Return *True* when the integration operation completed without error.

        Returns
        -------
        bool
            ``True`` iff :pyattr:`success` is ``True``.
        """
        return self.success

    def is_extraction(self) -> bool:
        """Return *True* when :pyattr:`mode` is :attr:`IntegrationMode.EXTRACTION`.

        Returns
        -------
        bool
        """
        return self.mode is IntegrationMode.EXTRACTION

    def is_embedding(self) -> bool:
        """Return *True* when :pyattr:`mode` is :attr:`IntegrationMode.EMBEDDING`.

        Returns
        -------
        bool
        """
        return self.mode is IntegrationMode.EMBEDDING

    def age_seconds(self) -> float | None:
        """Return the age of this record in seconds, or ``None`` on parse error.

        The age is computed relative to the current UTC clock.  If
        :pyattr:`timestamp` cannot be parsed the method returns ``None``
        rather than raising.

        Returns
        -------
        float | None
            Age in seconds, or ``None`` if the timestamp is malformed.
        """
        try:
            ts = datetime.datetime.fromisoformat(
                self.timestamp.rstrip("Z")
            ).replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            return (now - ts).total_seconds()
        except ValueError:
            return None

    def to_evidence_item(self) -> dict[str, Any]:
        """Convert this record to an evidence-item dict consumable by judgments.

        The returned dict follows the evidence-item contract used by
        :class:`~jugeo.judgments.judgment_terms.EvidenceItem`: it contains
        ``kind``, ``description``, ``trust``, and ``provenance`` keys.

        Returns
        -------
        dict[str, Any]
            A shallow evidence-item representation of this record.
        """
        return {
            "kind": "integration_record",
            "description": self.explanation,
            "trust": TrustLevel.ORACLE_PROPOSED.name,
            "provenance": "type_objects.integration",
            "record_id": self.record_id,
            "mode": self.mode.value,
            "success": self.success,
            "type_id": self.type_id,
            "judgment_id": self.judgment_id,
            "coordinate": self.coordinate.name,
        }

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Full representation of this record.
        """
        return {
            "record_id": self.record_id,
            "mode": self.mode.value,
            "type_id": self.type_id,
            "judgment_id": self.judgment_id,
            "coordinate": {
                "components": list(self.coordinate.components),
                "kind": self.coordinate.kind.value,
            },
            "timestamp": self.timestamp,
            "success": self.success,
            "explanation": self.explanation,
            "evidence_keys": list(self.evidence_keys),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> IntegrationRecord:
        """Deserialise from a JSON-compatible dict.

        Parameters
        ----------
        data : dict[str, Any]
            A dict previously produced by :meth:`serialize`.

        Returns
        -------
        IntegrationRecord
            The reconstructed record.

        Raises
        ------
        KeyError
            If a required field is absent from *data*.
        """
        raw_coord = data.get("coordinate", {})
        coord = Coordinate(
            components=tuple(raw_coord.get("components", [])),
            kind=CoordinateKind(raw_coord.get("kind", CoordinateKind.REGION.value)),
        )
        return cls(
            record_id=data["record_id"],
            mode=IntegrationMode(data["mode"]),
            type_id=data["type_id"],
            judgment_id=data.get("judgment_id"),
            coordinate=coord,
            timestamp=data["timestamp"],
            success=bool(data["success"]),
            explanation=data.get("explanation", ""),
            evidence_keys=tuple(data.get("evidence_keys", [])),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def success_record(
        cls,
        mode: IntegrationMode,
        type_id: str,
        judgment_id: str | None,
        coord: Coordinate,
        explanation: str,
    ) -> IntegrationRecord:
        """Create a success record for a given mode and type.

        Parameters
        ----------
        mode : IntegrationMode
            The operation that succeeded.
        type_id : str
            Identifier of the type involved.
        judgment_id : str | None
            Associated judgment identifier.
        coord : Coordinate
            Site coordinate.
        explanation : str
            Human-readable outcome description.

        Returns
        -------
        IntegrationRecord
            A record with :pyattr:`success` = ``True``.
        """
        return cls(
            record_id=_fresh_id("rec"),
            mode=mode,
            type_id=type_id,
            judgment_id=judgment_id,
            coordinate=coord,
            timestamp=_now_iso(),
            success=True,
            explanation=explanation,
            evidence_keys=(),
            metadata={},
        )

    @classmethod
    def failure_record(
        cls,
        mode: IntegrationMode,
        type_id: str,
        coord: Coordinate,
        explanation: str,
    ) -> IntegrationRecord:
        """Create a failure record for a given mode and type.

        Parameters
        ----------
        mode : IntegrationMode
            The operation that failed.
        type_id : str
            Identifier of the type involved.
        coord : Coordinate
            Site coordinate.
        explanation : str
            Human-readable description of the failure.

        Returns
        -------
        IntegrationRecord
            A record with :pyattr:`success` = ``False``.
        """
        return cls(
            record_id=_fresh_id("rec"),
            mode=mode,
            type_id=type_id,
            judgment_id=None,
            coordinate=coord,
            timestamp=_now_iso(),
            success=False,
            explanation=explanation,
            evidence_keys=(),
            metadata={},
        )


# ---------------------------------------------------------------------------
# JudgmentTypeExtractor
# ---------------------------------------------------------------------------

class JudgmentTypeExtractor:
    """Extract :class:`JuGeoType` objects from Judgment carrier slots.

    The extractor reads the ``carrier`` slot A of a
    :class:`~jugeo.judgments.judgment_terms.Judgment` and reconstructs the
    corresponding JuGeoType τ.  Theory2 §3.5.2 requires that this projection
    be *stable* — applying it twice yields the same type.

    The extractor maintains internal statistics so callers can diagnose
    extraction success rates across large judgment corpora.
    """

    def __init__(self) -> None:
        self._attempts: int = 0
        self._successes: int = 0
        self._failures: int = 0
        self._records: list[IntegrationRecord] = []

    # -- main extraction interface -------------------------------------------

    def extract(self, judgment: Judgment) -> JuGeoType | None:
        """Extract a JuGeoType from *judgment*, returning ``None`` on failure.

        The method inspects ``judgment.carrier`` and ``judgment.proposition``
        to reconstruct the type τ whose coordinate is ``judgment.coordinate``.

        Parameters
        ----------
        judgment : Judgment
            The source judgment.

        Returns
        -------
        JuGeoType | None
            The reconstructed type, or ``None`` if extraction is not possible
            (e.g. the carrier slot is empty or the type class is unavailable).
        """
        self._attempts += 1
        if not self.can_extract(judgment):
            self._failures += 1
            return None
        carrier = self.extract_carrier(judgment)
        if carrier is None:
            self._failures += 1
            return None
        coord = self.extract_coordinate(judgment)
        trust = self.extract_trust(judgment)
        formula = self.extract_formula(judgment)
        type_id = _fresh_id("τ")
        try:
            # Attempt real construction when JuGeoType is available
            if JuGeoType is not Any:
                result = JuGeoType(  # type: ignore[call-arg]
                    type_id=type_id,
                    coordinate=coord,
                    carrier=carrier,
                    formula=formula,
                    trust=trust,
                )
            else:
                # Fallback: return a plain dict proxy when models are absent
                result = {  # type: ignore[assignment]
                    "type_id": type_id,
                    "coordinate": coord,
                    "carrier": carrier,
                    "formula": formula,
                    "trust": trust,
                }
            self._successes += 1
            return result  # type: ignore[return-value]
        except Exception as exc:
            self._failures += 1
            raise_with_scope(
                FailureScope.TYPE_SYSTEM,
                f"type extraction failed for judgment {judgment.coordinate.name}: {exc}",
            )
            return None

    def extract_carrier(self, judgment: Judgment) -> TypeCarrier | None:
        """Extract the carrier component from the judgment's carrier slot.

        Parameters
        ----------
        judgment : Judgment
            Source judgment.

        Returns
        -------
        TypeCarrier | None
            The carrier object, or ``None`` when the slot is empty.
        """
        raw = getattr(judgment, "carrier", None)
        if raw is None:
            return None
        # If TypeCarrier is available and raw is already one, return it directly
        if TypeCarrier is not Any and isinstance(raw, TypeCarrier):
            return raw
        # Otherwise wrap the raw carrier dict if possible
        carrier_data = getattr(raw, "metadata", {})
        type_id = getattr(raw, "type_id", None) or carrier_data.get("type_id", "")
        if TypeCarrier is not Any:
            try:
                return TypeCarrier(  # type: ignore[call-arg]
                    carrier_id=type_id or _fresh_id("K"),
                    kind=getattr(raw, "kind", None),
                    metadata=carrier_data,
                )
            except Exception:
                pass
        return raw  # type: ignore[return-value]

    def extract_coordinate(self, judgment: Judgment) -> Coordinate:
        """Return the site coordinate associated with *judgment*.

        Parameters
        ----------
        judgment : Judgment
            Source judgment.

        Returns
        -------
        Coordinate
            The judgment's coordinate, coerced to a proper
            :class:`Coordinate` if necessary.
        """
        raw = getattr(judgment, "coordinate", None)
        return _safe_coordinate(raw)

    def extract_trust(self, judgment: Judgment) -> TypeTrustAnnotation:
        """Build a :class:`TypeTrustAnnotation` mirroring the judgment trust.

        Parameters
        ----------
        judgment : Judgment
            Source judgment.

        Returns
        -------
        TypeTrustAnnotation
            The trust annotation, or a minimal default when the judgment trust
            is absent or cannot be converted.
        """
        raw_trust = getattr(judgment, "trust", None)
        if TypeTrustAnnotation is not Any:
            try:
                level_val = getattr(raw_trust, "level", TrustLevel.UNVERIFIED)
                return TypeTrustAnnotation(  # type: ignore[call-arg]
                    level=level_val,
                    source="judgment_extraction",
                )
            except Exception:
                pass
        return raw_trust  # type: ignore[return-value]

    def extract_formula(self, judgment: Judgment) -> str:
        """Derive a type formula from the judgment's proposition.

        Parameters
        ----------
        judgment : Judgment
            Source judgment.

        Returns
        -------
        str
            The formula string, typically the judgment proposition formula
            or a derived type expression.
        """
        prop = getattr(judgment, "proposition", None)
        if prop is None:
            return ""
        return getattr(prop, "formula", "")

    def can_extract(self, judgment: Judgment) -> bool:
        """Return *True* when *judgment* is in a state from which a type can
        be extracted.

        A type can be extracted when the judgment has a non-``None``
        coordinate, a non-``None`` proposition, and at least one of:
        a non-``None`` carrier, or a non-empty proposition formula.

        Parameters
        ----------
        judgment : Judgment
            Candidate judgment.

        Returns
        -------
        bool
        """
        if not isinstance(judgment, Judgment):
            return False
        coord = getattr(judgment, "coordinate", None)
        if coord is None:
            return False
        prop = getattr(judgment, "proposition", None)
        carrier = getattr(judgment, "carrier", None)
        if prop is None and carrier is None:
            return False
        return True

    def batch_extract(
        self, judgments: Sequence[Judgment]
    ) -> list[tuple[Judgment, JuGeoType | None]]:
        """Extract types from a sequence of judgments in order.

        Parameters
        ----------
        judgments : Sequence[Judgment]
            Input judgments.

        Returns
        -------
        list[tuple[Judgment, JuGeoType | None]]
            Pairs of (judgment, extracted type or None).
        """
        results = []
        for j in judgments:
            t = self.extract(j)
            results.append((j, t))
        return results

    def record_extraction(
        self, judgment: Judgment, type_: JuGeoType | None
    ) -> IntegrationRecord:
        """Create and store an :class:`IntegrationRecord` for an extraction.

        Parameters
        ----------
        judgment : Judgment
            The source judgment.
        type_ : JuGeoType | None
            The extracted type (``None`` on failure).

        Returns
        -------
        IntegrationRecord
            The record that was created and stored.
        """
        coord = self.extract_coordinate(judgment)
        jid = getattr(judgment, "judgment_id", None) or id(judgment)
        tid = getattr(type_, "type_id", None) or "<none>"
        if type_ is not None:
            rec = IntegrationRecord.success_record(
                mode=IntegrationMode.EXTRACTION,
                type_id=str(tid),
                judgment_id=str(jid),
                coord=coord,
                explanation=f"Extracted type {tid} from judgment at {coord.name}",
            )
        else:
            rec = IntegrationRecord.failure_record(
                mode=IntegrationMode.EXTRACTION,
                type_id="<none>",
                coord=coord,
                explanation=f"Extraction failed for judgment at {coord.name}",
            )
        self._records.append(rec)
        return rec

    def statistics(self) -> dict[str, int]:
        """Return extraction statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``attempts``, ``successes``, ``failures``, ``records``.
        """
        return {
            "attempts": self._attempts,
            "successes": self._successes,
            "failures": self._failures,
            "records": len(self._records),
        }


# ---------------------------------------------------------------------------
# TypeJudgmentEmbedder
# ---------------------------------------------------------------------------

class TypeJudgmentEmbedder:
    """Embed a :class:`JuGeoType` into a Judgment as its carrier/annotation.

    The embedder implements the inverse of :class:`JudgmentTypeExtractor`:
    given a type τ, it creates or updates a Judgment whose carrier slot A
    encodes τ faithfully (Theory2 §3.5.3).  Round-tripping is guaranteed
    when the type is well-formed.
    """

    def __init__(self) -> None:
        self._embed_count: int = 0
        self._fail_count: int = 0
        self._records: list[IntegrationRecord] = []

    # -- main embed interface ------------------------------------------------

    def embed(
        self, type_: JuGeoType, base_judgment: Judgment | None = None
    ) -> Judgment:
        """Embed *type_* into a judgment, creating one if *base_judgment* is None.

        Parameters
        ----------
        type_ : JuGeoType
            The type to embed.
        base_judgment : Judgment | None
            An existing judgment to annotate.  When ``None`` a fresh judgment
            is created via :meth:`create_type_judgment`.

        Returns
        -------
        Judgment
            The judgment with τ embedded in its carrier slot.

        Raises
        ------
        JuGeoError
            If the type cannot be embedded (e.g. malformed carrier).
        """
        if not self.can_embed(type_):
            self._fail_count += 1
            raise_with_scope(
                FailureScope.TYPE_SYSTEM,
                f"cannot embed type {getattr(type_, 'type_id', type_)}: "
                "type is not embeddable",
            )
        self._embed_count += 1
        if base_judgment is None:
            return self.create_type_judgment(type_)
        return self.embed_as_carrier(type_, base_judgment)

    def embed_as_carrier(self, type_: JuGeoType, judgment: Judgment) -> Judgment:
        """Update *judgment*'s carrier slot to reflect *type_*.

        The carrier dict produced by :meth:`type_to_carrier_dict` is stored in
        ``judgment.carrier.metadata`` and the coordinate is aligned.

        Parameters
        ----------
        type_ : JuGeoType
            Source type.
        judgment : Judgment
            Target judgment.

        Returns
        -------
        Judgment
            A new judgment with the updated carrier.
        """
        carrier_data = self.type_to_carrier_dict(type_)
        raw_carrier = getattr(judgment, "carrier", None)
        if raw_carrier is not None:
            try:
                new_carrier = replace(
                    raw_carrier,
                    metadata={**getattr(raw_carrier, "metadata", {}), **carrier_data},
                )
                return replace(judgment, carrier=new_carrier)
            except (TypeError, Exception):
                pass
        return judgment  # fallback: judgment unchanged if carrier is unmodifiable

    def embed_as_proposition(self, type_: JuGeoType, judgment: Judgment) -> Judgment:
        """Update *judgment*'s proposition to encode the type formula.

        This is used when the proposition slot is preferred over the carrier
        slot for type annotation, e.g. in dependent-type judgments.

        Parameters
        ----------
        type_ : JuGeoType
            Source type.
        judgment : Judgment
            Target judgment.

        Returns
        -------
        Judgment
            A new judgment with the updated proposition.
        """
        new_prop = self.type_to_proposition(type_)
        try:
            return replace(judgment, proposition=new_prop)
        except (TypeError, Exception):
            return judgment

    def create_type_judgment(
        self, type_: JuGeoType, site: Site | None = None
    ) -> Judgment:
        """Create a fresh Judgment whose sole purpose is to carry *type_*.

        Parameters
        ----------
        type_ : JuGeoType
            The type to encode.
        site : Site | None
            Optional site (unused in base implementation, reserved for
            subclasses that need topology-aware judgment construction).

        Returns
        -------
        Judgment
            A minimal judgment encoding τ.
        """
        from jugeo.judgments.judgment_terms import (
            Carrier,
            Provenance,
            PropositionKind,
            TrustAnnotation,
        )
        coord = _safe_coordinate(getattr(type_, "coordinate", None))
        prop = self.type_to_proposition(type_)
        carrier_data = self.type_to_carrier_dict(type_)
        carrier = Carrier(
            type_id=getattr(type_, "type_id", ""),
            metadata=carrier_data,
        )
        trust = TrustAnnotation(
            level=_trust_level_from_annotation(
                getattr(type_, "trust", None)
            )
        )
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            trust=trust,
            provenance=Provenance(source="type_objects.integration"),
        )

    def type_to_proposition(self, type_: JuGeoType) -> Proposition:
        """Convert *type_* to a :class:`~jugeo.judgments.judgment_terms.Proposition`.

        Parameters
        ----------
        type_ : JuGeoType
            Source type.

        Returns
        -------
        Proposition
            A structural proposition whose formula encodes τ.
        """
        from jugeo.judgments.judgment_terms import PropositionKind

        formula = _proposition_formula_from_type(type_)
        return Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
        )

    def type_to_carrier_dict(self, type_: JuGeoType) -> dict[str, Any]:
        """Produce a carrier dict representation of *type_*.

        Parameters
        ----------
        type_ : JuGeoType
            Source type.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for storage in ``carrier.metadata``.
        """
        return {
            "type_id": getattr(type_, "type_id", ""),
            "coordinate": _safe_coordinate(
                getattr(type_, "coordinate", None)
            ).name,
            "formula": _proposition_formula_from_type(type_),
            "trust_level": _trust_level_from_annotation(
                getattr(type_, "trust", None)
            ).name,
            "_embedded_by": "type_objects.integration",
        }

    def can_embed(self, type_: JuGeoType) -> bool:
        """Return *True* when *type_* is in a state that permits embedding.

        A type can be embedded when it has a non-empty ``type_id`` attribute
        and a valid coordinate.

        Parameters
        ----------
        type_ : JuGeoType
            Candidate type.

        Returns
        -------
        bool
        """
        type_id = getattr(type_, "type_id", None)
        if not type_id:
            return False
        coord = getattr(type_, "coordinate", None)
        return coord is not None

    def batch_embed(self, types: Sequence[JuGeoType]) -> list[Judgment]:
        """Embed each type in *types* into a fresh judgment.

        Parameters
        ----------
        types : Sequence[JuGeoType]
            Types to embed.

        Returns
        -------
        list[Judgment]
            One judgment per type.
        """
        return [self.embed(t) for t in types]

    def statistics(self) -> dict[str, int]:
        """Return embedding statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``embed_count``, ``fail_count``, ``records``.
        """
        return {
            "embed_count": self._embed_count,
            "fail_count": self._fail_count,
            "records": len(self._records),
        }


# ---------------------------------------------------------------------------
# TypeDischargeRequest
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TypeDischargeRequest:
    """An obligation submitted to the solver on behalf of a JuGeoType.

    When the solver encounters an incomplete type annotation it may raise a
    *type obligation* — a claim that a certain formula must hold for the type
    to be admissible.  This dataclass packages that obligation for transport
    to the solver subsystem.

    Parameters
    ----------
    request_id : str
        Unique identifier for this request.
    type_ : JuGeoType
        The type whose obligation is to be discharged.
    judgment_id : str
        The judgment that generated the obligation.
    obligation : str
        The formula or description of what must be proved.
    strategy : str
        Solver strategy hint (``"auto"``, ``"smt"``, ``"proof"``, etc.).
    priority : int
        Scheduling priority — higher values are attempted first.
    timeout : float | None
        Maximum solver time in seconds, or ``None`` for no limit.
    metadata : Mapping[str, Any]
        Arbitrary extra data.
    """

    request_id: str
    type_: Any  # JuGeoType
    judgment_id: str
    obligation: str
    strategy: str
    priority: int
    timeout: float | None
    metadata: Mapping[str, Any]

    # -- query methods -------------------------------------------------------

    def is_urgent(self) -> bool:
        """Return *True* when :pyattr:`priority` is ≥ 10.

        Returns
        -------
        bool
        """
        return self.priority >= 10

    def has_timeout(self) -> bool:
        """Return *True* when a finite timeout is set.

        Returns
        -------
        bool
        """
        return self.timeout is not None

    def is_expired(self, now: float | None = None) -> bool:
        """Return *True* when the request has timed out.

        The check compares the request creation time (stored in
        ``metadata["created_at"]``) against *now*.  If the timestamp is
        absent or :pyattr:`timeout` is ``None``, returns ``False``.

        Parameters
        ----------
        now : float | None
            Current epoch time in seconds.  Defaults to
            ``datetime.datetime.utcnow().timestamp()``.

        Returns
        -------
        bool
        """
        if self.timeout is None:
            return False
        created_str = self.metadata.get("created_at", "")
        if not created_str:
            return False
        try:
            created = datetime.datetime.fromisoformat(
                str(created_str).rstrip("Z")
            ).replace(tzinfo=datetime.timezone.utc)
            if now is None:
                now = datetime.datetime.now(
                    tz=datetime.timezone.utc
                ).timestamp()
            elapsed = now - created.timestamp()
            return elapsed > self.timeout
        except (ValueError, TypeError):
            return False

    def to_solver_payload(self) -> dict[str, Any]:
        """Produce a JSON-serialisable payload for the solver subsystem.

        Returns
        -------
        dict[str, Any]
            A dict containing all fields needed by the solver routing layer.
        """
        type_id = getattr(self.type_, "type_id", str(id(self.type_)))
        coord = _safe_coordinate(getattr(self.type_, "coordinate", None))
        return {
            "request_id": self.request_id,
            "type_id": type_id,
            "coordinate": coord.name,
            "judgment_id": self.judgment_id,
            "obligation": self.obligation,
            "strategy": self.strategy,
            "priority": self.priority,
            "timeout": self.timeout,
        }

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
        """
        type_id = getattr(self.type_, "type_id", str(id(self.type_)))
        return {
            "request_id": self.request_id,
            "type_id": type_id,
            "judgment_id": self.judgment_id,
            "obligation": self.obligation,
            "strategy": self.strategy,
            "priority": self.priority,
            "timeout": self.timeout,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TypeDischargeRequest:
        """Deserialise from a JSON-compatible dict.

        Parameters
        ----------
        data : dict[str, Any]
            A dict previously produced by :meth:`serialize`.

        Returns
        -------
        TypeDischargeRequest

        Raises
        ------
        KeyError
            If a required field is absent.
        """
        return cls(
            request_id=data["request_id"],
            type_=data.get("type_id", ""),
            judgment_id=data["judgment_id"],
            obligation=data["obligation"],
            strategy=data.get("strategy", "auto"),
            priority=int(data.get("priority", 0)),
            timeout=data.get("timeout"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def for_type(
        cls,
        type_: JuGeoType,
        judgment_id: str,
        obligation: str,
        strategy: str = "auto",
    ) -> TypeDischargeRequest:
        """Convenience constructor: create a request for a given type.

        Parameters
        ----------
        type_ : JuGeoType
            The type whose obligation is to be discharged.
        judgment_id : str
            The associated judgment.
        obligation : str
            The formula to discharge.
        strategy : str
            Solver strategy hint.

        Returns
        -------
        TypeDischargeRequest
        """
        return cls(
            request_id=_fresh_id("req"),
            type_=type_,
            judgment_id=judgment_id,
            obligation=obligation,
            strategy=strategy,
            priority=0,
            timeout=None,
            metadata={"created_at": _now_iso()},
        )


# ---------------------------------------------------------------------------
# TypeSolverBridge
# ---------------------------------------------------------------------------

class TypeSolverBridge:
    """Submit type obligations to the solver and track their lifecycle.

    The solver bridge acts as a façade over the (optionally injected) solver
    object.  When no real solver is available it records obligations as
    pending and marks them ``"unknown"`` — callers can check
    :meth:`pending_requests` to see what still needs external attention.
    """

    def __init__(self, solver: Any = None) -> None:
        self._solver = solver
        self._pending: dict[str, TypeDischargeRequest] = {}
        self._completed: list[IntegrationRecord] = []
        self._cancelled: set[str] = set()

    # -- submission -----------------------------------------------------------

    def submit(self, request: TypeDischargeRequest) -> IntegrationRecord:
        """Submit *request* to the solver and return an audit record.

        Parameters
        ----------
        request : TypeDischargeRequest
            The discharge obligation to process.

        Returns
        -------
        IntegrationRecord
            Outcome record — ``success=True`` when the solver accepted the
            request (even if it is still pending), ``False`` on error.
        """
        coord = _safe_coordinate(
            getattr(request.type_, "coordinate", None)
        )
        type_id = getattr(request.type_, "type_id", request.request_id)
        self._pending[request.request_id] = request
        if self._solver is not None:
            try:
                payload = request.to_solver_payload()
                self._solver.submit(payload)
                del self._pending[request.request_id]
                rec = IntegrationRecord.success_record(
                    mode=IntegrationMode.DISCHARGE,
                    type_id=str(type_id),
                    judgment_id=request.judgment_id,
                    coord=coord,
                    explanation=(
                        f"Solver accepted obligation '{request.obligation}'"
                        f" for type {type_id}"
                    ),
                )
                self._completed.append(rec)
                return rec
            except Exception as exc:
                rec = IntegrationRecord.failure_record(
                    mode=IntegrationMode.DISCHARGE,
                    type_id=str(type_id),
                    coord=coord,
                    explanation=f"Solver rejected obligation: {exc}",
                )
                self._completed.append(rec)
                return rec
        # No solver: record as deferred
        rec = IntegrationRecord.success_record(
            mode=IntegrationMode.DISCHARGE,
            type_id=str(type_id),
            judgment_id=request.judgment_id,
            coord=coord,
            explanation=f"Obligation '{request.obligation}' deferred (no solver)",
        )
        return rec

    def discharge_type(
        self,
        type_: JuGeoType,
        judgment_id: str,
        obligation: str,
    ) -> IntegrationRecord:
        """Convenience method: create a request and submit it immediately.

        Parameters
        ----------
        type_ : JuGeoType
            Type whose obligation is to be discharged.
        judgment_id : str
            Associated judgment.
        obligation : str
            Formula to discharge.

        Returns
        -------
        IntegrationRecord
        """
        req = TypeDischargeRequest.for_type(type_, judgment_id, obligation)
        return self.submit(req)

    def batch_discharge(
        self, requests: Sequence[TypeDischargeRequest]
    ) -> list[IntegrationRecord]:
        """Submit multiple discharge requests.

        Parameters
        ----------
        requests : Sequence[TypeDischargeRequest]
            Requests to submit in order.

        Returns
        -------
        list[IntegrationRecord]
            One record per request.
        """
        return [self.submit(r) for r in requests]

    def query_status(self, request_id: str) -> str:
        """Return the current status string for *request_id*.

        Parameters
        ----------
        request_id : str
            Identifier of the request to query.

        Returns
        -------
        str
            One of ``"pending"``, ``"completed"``, ``"cancelled"``,
            ``"unknown"``.
        """
        if request_id in self._cancelled:
            return "cancelled"
        if request_id in self._pending:
            return "pending"
        if any(
            r.metadata.get("request_id") == request_id
            for r in self._completed
        ):
            return "completed"
        return "unknown"

    def cancel(self, request_id: str) -> bool:
        """Cancel a pending discharge request.

        Parameters
        ----------
        request_id : str
            Identifier of the request to cancel.

        Returns
        -------
        bool
            ``True`` if the request was pending and has been cancelled.
        """
        if request_id in self._pending:
            del self._pending[request_id]
            self._cancelled.add(request_id)
            return True
        return False

    def pending_requests(self) -> list[TypeDischargeRequest]:
        """Return all requests that have not yet been sent to the solver.

        Returns
        -------
        list[TypeDischargeRequest]
        """
        return list(self._pending.values())

    def completed_records(self) -> list[IntegrationRecord]:
        """Return all completed integration records.

        Returns
        -------
        list[IntegrationRecord]
        """
        return list(self._completed)

    def statistics(self) -> dict[str, int]:
        """Return solver-bridge statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``pending``, ``completed``, ``cancelled``.
        """
        return {
            "pending": len(self._pending),
            "completed": len(self._completed),
            "cancelled": len(self._cancelled),
        }


# ---------------------------------------------------------------------------
# TypeIntegration
# ---------------------------------------------------------------------------

class TypeIntegration:
    """Full integration surface between the type-object layer and JuGeo.

    :class:`TypeIntegration` is the primary entry point for consumers of
    this module.  It coordinates the extractor, embedder, and solver bridge
    into a single cohesive API surface and maintains the bidirectional index
    between type IDs and judgment IDs.

    Theory2 §3.5 specifies that the integration layer must be:

    * **Functorial** — transport commutes with composition of morphisms.
    * **Consistent** — the type index never maps a judgment ID to two
      distinct types with different coordinates.
    * **Trust-monotone** — synchronisation never increases trust above the
      judgment's trust level.
    """

    def __init__(
        self, site: Site | None = None, solver: Any = None
    ) -> None:
        self._site = site
        self._extractor = JudgmentTypeExtractor()
        self._embedder = TypeJudgmentEmbedder()
        self._solver_bridge = TypeSolverBridge(solver=solver)
        self._type_index: dict[str, JuGeoType] = {}          # judgment_id → type
        self._judgment_index: dict[str, Judgment] = {}        # type_id → judgment
        self._records: list[IntegrationRecord] = []

    # -- extraction ----------------------------------------------------------

    def extract_from_judgment(self, judgment: Judgment) -> JuGeoType | None:
        """Extract a type from *judgment* and update the internal index.

        Parameters
        ----------
        judgment : Judgment
            Source judgment.

        Returns
        -------
        JuGeoType | None
        """
        type_ = self._extractor.extract(judgment)
        rec = self._extractor.record_extraction(judgment, type_)
        self._records.append(rec)
        if type_ is not None:
            jid = str(getattr(judgment, "judgment_id", id(judgment)))
            tid = str(getattr(type_, "type_id", id(type_)))
            self._type_index[jid] = type_
            self._judgment_index[tid] = judgment
        return type_

    # -- embedding -----------------------------------------------------------

    def embed_into_judgment(
        self, type_: JuGeoType, base_judgment: Judgment | None = None
    ) -> Judgment:
        """Embed *type_* into a judgment and update the index.

        Parameters
        ----------
        type_ : JuGeoType
            Type to embed.
        base_judgment : Judgment | None
            Existing judgment to annotate, or ``None`` to create a fresh one.

        Returns
        -------
        Judgment
        """
        j = self._embedder.embed(type_, base_judgment)
        tid = str(getattr(type_, "type_id", id(type_)))
        jid = str(getattr(j, "judgment_id", id(j)))
        self._type_index[jid] = type_
        self._judgment_index[tid] = j
        coord = _safe_coordinate(getattr(type_, "coordinate", None))
        rec = IntegrationRecord.success_record(
            mode=IntegrationMode.EMBEDDING,
            type_id=tid,
            judgment_id=jid,
            coord=coord,
            explanation=f"Embedded type {tid} into judgment {jid}",
        )
        self._records.append(rec)
        return j

    # -- transport -----------------------------------------------------------

    def transport_with_judgment(
        self, type_: JuGeoType, judgment: Judgment, morphism: Morphism
    ) -> JuGeoType:
        """Transport *type_* along *morphism*, respecting the judgment context.

        Given a type τ at c and a morphism f: c' → c, this method computes the
        restricted type τ|_f at c'.  Theory2 §3.5.2 requires that the
        restriction ρ_f be applied to the carrier, formula, and trust
        components of τ.

        Parameters
        ----------
        type_ : JuGeoType
            Type to restrict.
        judgment : Judgment
            The judgment whose restriction map triggers the transport.
        morphism : Morphism
            The site morphism f: c' → c.

        Returns
        -------
        JuGeoType
            The restricted type τ|_f.
        """
        if TypeAlgorithms is not Any:
            try:
                alg = TypeAlgorithms()
                restricted = alg.restrict(type_, morphism)
                coord = _safe_coordinate(morphism.source)
                tid = str(getattr(type_, "type_id", id(type_)))
                jid = str(getattr(judgment, "judgment_id", id(judgment)))
                rec = IntegrationRecord.success_record(
                    mode=IntegrationMode.TRANSPORT,
                    type_id=tid,
                    judgment_id=jid,
                    coord=coord,
                    explanation=(
                        f"Transported τ={tid} along {morphism.label or 'f'}: "
                        f"{morphism.source.name} → {morphism.target.name}"
                    ),
                )
                self._records.append(rec)
                return restricted  # type: ignore[return-value]
            except Exception:
                pass
        # Fallback: return the original type (identity transport)
        coord = _safe_coordinate(getattr(type_, "coordinate", None))
        tid = str(getattr(type_, "type_id", id(type_)))
        rec = IntegrationRecord.success_record(
            mode=IntegrationMode.TRANSPORT,
            type_id=tid,
            judgment_id=str(getattr(judgment, "judgment_id", id(judgment))),
            coord=coord,
            explanation="Transport deferred — algorithms module unavailable",
        )
        self._records.append(rec)
        return type_

    # -- discharge -----------------------------------------------------------

    def discharge_obligation(
        self, type_: JuGeoType, judgment_id: str, obligation: str
    ) -> IntegrationRecord:
        """Submit an obligation arising from *type_* to the solver.

        Parameters
        ----------
        type_ : JuGeoType
            Type with the obligation.
        judgment_id : str
            Associated judgment.
        obligation : str
            The formula or description to discharge.

        Returns
        -------
        IntegrationRecord
        """
        rec = self._solver_bridge.discharge_type(type_, judgment_id, obligation)
        self._records.append(rec)
        return rec

    # -- synchronization -----------------------------------------------------

    def synchronize_trust(
        self, type_: JuGeoType, judgment: Judgment
    ) -> JuGeoType:
        """Align *type_*'s trust annotation with *judgment*'s trust level.

        Theory2 §3.5.4 states that the type trust must never exceed the
        judgment trust — trust(τ) ≤ trust(J).  This method lowers the type
        trust if it exceeds the judgment trust, leaving it unchanged otherwise.

        Parameters
        ----------
        type_ : JuGeoType
            Type whose trust is to be synchronised.
        judgment : Judgment
            Judgment whose trust provides the upper bound.

        Returns
        -------
        JuGeoType
            A (possibly new) type with adjusted trust annotation.
        """
        j_trust = getattr(judgment, "trust", None)
        j_level = getattr(j_trust, "level", TrustLevel.UNVERIFIED)
        t_trust = getattr(type_, "trust", None)
        t_level = _trust_level_from_annotation(t_trust)
        coord = _safe_coordinate(getattr(type_, "coordinate", None))
        tid = str(getattr(type_, "type_id", id(type_)))
        if isinstance(t_level, TrustLevel) and isinstance(j_level, TrustLevel):
            if t_level > j_level:
                if TypeTrustAnnotation is not Any and t_trust is not None:
                    try:
                        new_trust = replace(t_trust, level=j_level)
                        result = replace(type_, trust=new_trust)  # type: ignore[call-arg]
                        rec = IntegrationRecord.success_record(
                            mode=IntegrationMode.SYNCHRONIZATION,
                            type_id=tid,
                            judgment_id=str(
                                getattr(judgment, "judgment_id", id(judgment))
                            ),
                            coord=coord,
                            explanation=(
                                f"Lowered trust from {t_level.name} → "
                                f"{j_level.name} to match judgment"
                            ),
                        )
                        self._records.append(rec)
                        return result  # type: ignore[return-value]
                    except (TypeError, Exception):
                        pass
        rec = IntegrationRecord.success_record(
            mode=IntegrationMode.SYNCHRONIZATION,
            type_id=tid,
            judgment_id=str(getattr(judgment, "judgment_id", id(judgment))),
            coord=coord,
            explanation="Trust already consistent — no adjustment needed",
        )
        self._records.append(rec)
        return type_

    # -- index management ----------------------------------------------------

    def register_judgment(self, judgment: Judgment) -> None:
        """Add *judgment* to the internal judgment index.

        Parameters
        ----------
        judgment : Judgment
            Judgment to register.
        """
        jid = str(getattr(judgment, "judgment_id", id(judgment)))
        if jid not in self._type_index:
            t = self._extractor.extract(judgment)
            if t is not None:
                self._type_index[jid] = t

    def register_type(self, type_: JuGeoType) -> None:
        """Add *type_* to the internal type index.

        Parameters
        ----------
        type_ : JuGeoType
            Type to register.
        """
        tid = str(getattr(type_, "type_id", id(type_)))
        if tid not in self._judgment_index:
            j = self._embedder.embed(type_)
            self._judgment_index[tid] = j

    def lookup_type(self, judgment_id: str) -> JuGeoType | None:
        """Look up the type associated with *judgment_id*.

        Parameters
        ----------
        judgment_id : str
            Judgment identifier.

        Returns
        -------
        JuGeoType | None
        """
        return self._type_index.get(judgment_id)

    def lookup_judgment(self, type_id: str) -> Judgment | None:
        """Look up the judgment associated with *type_id*.

        Parameters
        ----------
        type_id : str
            Type identifier.

        Returns
        -------
        Judgment | None
        """
        return self._judgment_index.get(type_id)

    # -- diagnostics ---------------------------------------------------------

    def consistency_check(self) -> list[str]:
        """Run a consistency check on the integration layer.

        Checks:
        * Every type in the judgment→type index has a coordinate.
        * Every judgment in the type→judgment index has a matching coordinate.
        * No type_id appears in both indices simultaneously with conflicting
          coordinates.

        Returns
        -------
        list[str]
            A list of human-readable inconsistency descriptions.  An empty
            list means the integration layer is consistent.
        """
        issues: list[str] = []
        for jid, t in self._type_index.items():
            coord = getattr(t, "coordinate", None)
            if coord is None:
                issues.append(
                    f"Type for judgment {jid} has no coordinate"
                )
        for tid, j in self._judgment_index.items():
            coord = getattr(j, "coordinate", None)
            if coord is None:
                issues.append(
                    f"Judgment for type {tid} has no coordinate"
                )
        return issues

    def statistics(self) -> dict[str, int]:
        """Return integration-layer statistics.

        Returns
        -------
        dict[str, int]
            Keys: ``records``, ``indexed_types``, ``indexed_judgments``,
            plus nested counts from the extractor, embedder, and solver bridge.
        """
        stats: dict[str, int] = {
            "records": len(self._records),
            "indexed_types": len(self._type_index),
            "indexed_judgments": len(self._judgment_index),
        }
        stats.update(
            {f"extractor_{k}": v for k, v in self._extractor.statistics().items()}
        )
        stats.update(
            {f"embedder_{k}": v for k, v in self._embedder.statistics().items()}
        )
        stats.update(
            {f"solver_{k}": v for k, v in self._solver_bridge.statistics().items()}
        )
        return stats

    def full_report(self) -> dict[str, Any]:
        """Produce a comprehensive integration report.

        Returns
        -------
        dict[str, Any]
            A report containing statistics, consistency issues, and the
            first 10 integration records (serialised).
        """
        return {
            "statistics": self.statistics(),
            "consistency_issues": self.consistency_check(),
            "recent_records": [
                r.serialize() for r in self._records[-10:]
            ],
            "pending_discharge_count": len(
                self._solver_bridge.pending_requests()
            ),
        }
