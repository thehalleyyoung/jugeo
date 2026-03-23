"""Satisfaction witnesses for the specification-satisfaction problem mode.

Section 10.2: Satisfaction Witnesses.  A witness records local evidence at
every coordinate and the gluing data across overlaps, providing the raw
material from which a certificate of satisfaction is assembled.  This module
implements witness construction, evidence collection, gluing computation,
merging, and validation.

References theory2.tex §10.2.

copilot: generated scaffold for jugeo satisfaction witnesses; evidence
collection and gluing semantics follow the descent-theoretic account in §10.2.
Extend GluingDataComputer.check_* methods as domain-specific compatibility
rules are formalised.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

# ---------------------------------------------------------------------------
# Optional internal imports
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        CertificateOfSatisfaction,
        DescentCondition,
        GapSeverity,
        ResidualGap,
        SatisfactionStatus,
        SatisfactionWitness,
        Specification,
        SpecificationKind,
        WitnessStatus,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    CertificateOfSatisfaction = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    DescentCondition = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.hypercovers import CechNerve, HypercoverLevel
except ImportError:
    HypercoverLevel = Any  # type: ignore[assignment,misc]
    CechNerve = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentObstruction,
        DescentResult,
        GluingData,
        LocalSection,
    )
except ImportError:
    DescentEngine = Any  # type: ignore[assignment,misc]
    DescentResult = Any  # type: ignore[assignment,misc]
    LocalSection = Any  # type: ignore[assignment,misc]
    GluingData = Any  # type: ignore[assignment,misc]
    DescentObstruction = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]
    SemanticSite = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentKind, JudgmentTerm, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[assignment,misc]
    CertificateStatus = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns
    -------
    str
        UTC timestamp in ISO 8601 format.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _evidence_fingerprint(evidence_item: dict[str, Any]) -> str:
    """Compute a stable fingerprint for an evidence dict for deduplication.

    Parameters
    ----------
    evidence_item : dict[str, Any]
        Evidence entry to fingerprint.

    Returns
    -------
    str
        A hex digest string derived from the item's canonical JSON form.
    """
    canonical = json.dumps(evidence_item, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode()).hexdigest()  # noqa: S324 — not crypto


def _overlap_key_canonical(coord_a: str, coord_b: str) -> str:
    """Return a canonical overlap key for a pair of coordinates.

    The key is independent of the order of the arguments.

    Parameters
    ----------
    coord_a : str
        First coordinate identifier.
    coord_b : str
        Second coordinate identifier.

    Returns
    -------
    str
        A canonical string key of the form ``"<lesser>||<greater>"``.
    """
    a, b = sorted([coord_a, coord_b])
    return f"{a}||{b}"


def _spec_coords(spec: Any) -> tuple[str, ...]:
    """Extract the target coordinate tuple from a specification-like object.

    Parameters
    ----------
    spec : Specification | dict
        A specification dataclass or plain dict.

    Returns
    -------
    tuple[str, ...]
        The target coordinate identifiers.
    """
    if isinstance(spec, dict):
        return tuple(spec.get("target_coordinates", ()))
    return tuple(getattr(spec, "target_coordinates", ()))


def _spec_id(spec: Any) -> str:
    """Extract the spec_id from a specification-like object.

    Parameters
    ----------
    spec : Specification | dict
        A specification dataclass or plain dict.

    Returns
    -------
    str
        The specification's id string.
    """
    if isinstance(spec, dict):
        return str(spec.get("spec_id", ""))
    return str(getattr(spec, "spec_id", ""))


def _make_witness_dict(
    *,
    witness_id: str,
    spec_id: str,
    target_coordinates: tuple[str, ...],
    evidence_map: dict[str, list[dict[str, Any]]],
    gluing_data: dict[str, dict[str, Any]],
    trust_levels: dict[str, float],
    status: str,
    created_at: str,
) -> dict[str, Any]:
    """Assemble a witness dict or dataclass instance.

    Tries to instantiate the real :class:`SatisfactionWitness` dataclass;
    falls back to a plain dict if the models module is unavailable.

    Parameters
    ----------
    witness_id : str
        Unique identifier for the witness.
    spec_id : str
        The specification this witness pertains to.
    target_coordinates : tuple[str, ...]
        Coordinates covered by the witness.
    evidence_map : dict[str, list[dict[str, Any]]]
        Evidence collected at each coordinate.
    gluing_data : dict[str, dict[str, Any]]
        Gluing data across coordinate overlaps.
    trust_levels : dict[str, float]
        Trust level assigned to evidence at each coordinate.
    status : str
        Witness status string.
    created_at : str
        ISO 8601 creation timestamp.

    Returns
    -------
    SatisfactionWitness | dict[str, Any]
        The assembled witness.
    """
    base: dict[str, Any] = {
        "witness_id": witness_id,
        "spec_id": spec_id,
        "target_coordinates": target_coordinates,
        "evidence_map": evidence_map,
        "gluing_data": gluing_data,
        "trust_levels": trust_levels,
        "status": status,
        "created_at": created_at,
    }
    try:
        from jugeo.problem_modes.specification_satisfaction.models import (
            SatisfactionWitness as _SW,
        )
        fields = {f.name for f in _SW.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return _SW(**{k: v for k, v in base.items() if k in fields})
    except (ImportError, AttributeError, TypeError):
        return base


def _get_witness_field(witness: Any, field_name: str, default: Any = None) -> Any:
    """Retrieve a field from a witness dataclass or dict.

    Parameters
    ----------
    witness : SatisfactionWitness | dict
        The witness object.
    field_name : str
        The attribute/key to look up.
    default : Any, optional
        Value returned if the field is absent.

    Returns
    -------
    Any
        The field value or *default*.
    """
    if isinstance(witness, dict):
        return witness.get(field_name, default)
    return getattr(witness, field_name, default)


# ---------------------------------------------------------------------------
# WitnessBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WitnessBuilder:
    """Fluent builder for satisfaction witness objects.

    Collects evidence, gluing data, and trust assignments for a
    :class:`Specification` and emits a fully populated witness via
    :meth:`build`.

    Attributes
    ----------
    spec : Specification | None
        The specification this witness is being built for.
    target_coordinates : list[str]
        Coordinate identifiers the witness must cover.
    collected_evidence : dict[str, list[dict[str, Any]]]
        Evidence items gathered at each coordinate.
    computed_gluing : dict[str, dict[str, Any]]
        Gluing data computed for each overlap key.
    trust_assignments : dict[str, float]
        Trust level (0.0–1.0) assigned to evidence at each coordinate.
    """

    spec: Any = None
    target_coordinates: list[str] = field(default_factory=list)
    collected_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    computed_gluing: dict[str, dict[str, Any]] = field(default_factory=dict)
    trust_assignments: dict[str, float] = field(default_factory=dict)

    # -- fluent setters -------------------------------------------------------

    def set_spec(self, spec: Any) -> WitnessBuilder:
        """Bind a specification to this builder and copy its coordinates.

        Parameters
        ----------
        spec : Specification
            The specification to satisfy.

        Returns
        -------
        WitnessBuilder
            ``self`` for method chaining.
        """
        self.spec = spec
        for coord in _spec_coords(spec):
            if coord not in self.target_coordinates:
                self.target_coordinates.append(coord)
        return self

    def add_evidence(
        self, coordinate: str, evidence_item: dict[str, Any]
    ) -> WitnessBuilder:
        """Add a single evidence item for the given coordinate.

        Parameters
        ----------
        coordinate : str
            The coordinate to which the evidence pertains.
        evidence_item : dict[str, Any]
            Evidence record.  Should contain at minimum a ``"source"`` key.

        Returns
        -------
        WitnessBuilder
            ``self`` for method chaining.
        """
        if coordinate not in self.collected_evidence:
            self.collected_evidence[coordinate] = []
        stamped = {**evidence_item, "_collected_at": _utc_now_iso()}
        self.collected_evidence[coordinate].append(stamped)
        if coordinate not in self.target_coordinates:
            self.target_coordinates.append(coordinate)
        return self

    def add_gluing(
        self, overlap_key: str, gluing_datum: dict[str, Any]
    ) -> WitnessBuilder:
        """Record a gluing datum for the given overlap key.

        Parameters
        ----------
        overlap_key : str
            Canonical overlap key of the form ``"<coord_a>||<coord_b>"``.
        gluing_datum : dict[str, Any]
            The gluing data record.

        Returns
        -------
        WitnessBuilder
            ``self`` for method chaining.
        """
        existing = self.computed_gluing.get(overlap_key, {})
        self.computed_gluing[overlap_key] = {**existing, **gluing_datum, "_recorded_at": _utc_now_iso()}
        return self

    def assign_trust(self, coordinate: str, trust_level: float) -> WitnessBuilder:
        """Assign a trust level to the evidence at the given coordinate.

        Parameters
        ----------
        coordinate : str
            The coordinate identifier.
        trust_level : float
            A value in [0.0, 1.0] where 1.0 is fully trusted.

        Returns
        -------
        WitnessBuilder
            ``self`` for method chaining.

        Raises
        ------
        ValueError
            If *trust_level* is outside [0.0, 1.0].
        """
        if not 0.0 <= trust_level <= 1.0:
            raise ValueError(
                f"trust_level must be in [0.0, 1.0]; got {trust_level}."
            )
        self.trust_assignments[coordinate] = trust_level
        return self

    # -- classmethod constructors ---------------------------------------------

    @classmethod
    def from_spec(cls, spec: Any) -> WitnessBuilder:
        """Create a builder pre-initialised from a :class:`Specification`.

        Parameters
        ----------
        spec : Specification
            The specification to satisfy.

        Returns
        -------
        WitnessBuilder
            A new builder bound to *spec*.
        """
        builder = cls()
        builder.set_spec(spec)
        return builder

    # -- introspection --------------------------------------------------------

    def estimate_completeness(self) -> float:
        """Estimate the fraction of target coordinates with collected evidence.

        Returns
        -------
        float
            A value in [0.0, 1.0].  Returns 0.0 if there are no target
            coordinates.
        """
        if not self.target_coordinates:
            return 0.0
        covered = sum(
            1
            for c in self.target_coordinates
            if self.collected_evidence.get(c)
        )
        return covered / len(self.target_coordinates)

    def validate_before_build(self) -> list[str]:
        """Collect all validation errors without raising.

        Returns
        -------
        list[str]
            A list of human-readable error messages.  Empty if validation
            passes.
        """
        errors: list[str] = []
        if not self.target_coordinates:
            errors.append("No target coordinates registered.")
        for coord in self.target_coordinates:
            if not self.collected_evidence.get(coord):
                errors.append(f"No evidence collected for coordinate '{coord}'.")
        for coord, trust in self.trust_assignments.items():
            if not 0.0 <= trust <= 1.0:
                errors.append(
                    f"Trust level for '{coord}' out of range: {trust}."
                )
        return errors

    # -- build ----------------------------------------------------------------

    def build(self) -> Any:
        """Construct and return a satisfaction witness.

        Returns
        -------
        SatisfactionWitness
            The assembled witness.

        Raises
        ------
        ValueError
            If validation errors are present.
        """
        errors = self.validate_before_build()
        if errors:
            raise ValueError(
                f"Cannot build witness — {len(errors)} error(s): "
                + "; ".join(errors)
            )
        completeness = self.estimate_completeness()
        status = "complete" if completeness >= 1.0 else "partial"
        return _make_witness_dict(
            witness_id=str(uuid.uuid4()),
            spec_id=_spec_id(self.spec) if self.spec is not None else "",
            target_coordinates=tuple(self.target_coordinates),
            evidence_map=dict(self.collected_evidence),
            gluing_data=dict(self.computed_gluing),
            trust_levels=dict(self.trust_assignments),
            status=status,
            created_at=_utc_now_iso(),
        )


# ---------------------------------------------------------------------------
# EvidenceCollector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvidenceCollector:
    """Gathers and organises evidence items from a variety of sources.

    Evidence items are plain dicts stamped with provenance metadata and stored
    in ``evidence_store``, keyed by coordinate identifier.

    Attributes
    ----------
    evidence_store : dict[str, list[dict[str, Any]]]
        All collected evidence, keyed by coordinate.
    collection_log : list[dict[str, Any]]
        Audit log of every collection operation.
    trust_threshold : float
        Default trust threshold below which evidence is considered unreliable.
    """

    evidence_store: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    collection_log: list[dict[str, Any]] = field(default_factory=dict.__new__(dict).__class__)
    trust_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.collection_log, list):
            self.collection_log = []

    # -- collection methods ---------------------------------------------------

    def collect_for_coordinate(
        self, coordinate: str, evidence_source: Any
    ) -> list[dict[str, Any]]:
        """Collect evidence items from *evidence_source* for a coordinate.

        The source may be a list of dicts, a single dict, a string (treated as
        a free-text observation), or any object with an ``evidence`` attribute.

        Parameters
        ----------
        coordinate : str
            Coordinate to attach evidence to.
        evidence_source : Any
            Source of evidence.

        Returns
        -------
        list[dict[str, Any]]
            The collected evidence items.
        """
        items: list[dict[str, Any]] = []
        if isinstance(evidence_source, list):
            for item in evidence_source:
                if isinstance(item, dict):
                    items.append(item)
                else:
                    items.append({"raw": str(item), "source": "list"})
        elif isinstance(evidence_source, dict):
            items.append(evidence_source)
        elif isinstance(evidence_source, str):
            items.append({"observation": evidence_source, "source": "string"})
        elif hasattr(evidence_source, "evidence"):
            raw = evidence_source.evidence
            if isinstance(raw, list):
                items.extend(raw)
            else:
                items.append({"value": raw, "source": type(evidence_source).__name__})
        else:
            items.append({
                "value": str(evidence_source),
                "source": type(evidence_source).__name__,
            })
        stamped: list[dict[str, Any]] = []
        for item in items:
            stamped_item = {
                **item,
                "_coordinate": coordinate,
                "_collected_at": _utc_now_iso(),
                "_fingerprint": _evidence_fingerprint(item),
            }
            stamped.append(stamped_item)
        if coordinate not in self.evidence_store:
            self.evidence_store[coordinate] = []
        self.evidence_store[coordinate].extend(stamped)
        self.collection_log.append({
            "coordinate": coordinate,
            "count": len(stamped),
            "timestamp": _utc_now_iso(),
        })
        return stamped

    def collect_from_judgment(self, judgment_term: Any) -> dict[str, Any]:
        """Extract an evidence record from a :class:`JudgmentTerm`.

        Parameters
        ----------
        judgment_term : JudgmentTerm
            The judgment term to extract evidence from.

        Returns
        -------
        dict[str, Any]
            An evidence record.
        """
        if isinstance(judgment_term, dict):
            return {
                "source": "judgment_term",
                "kind": judgment_term.get("kind", "unknown"),
                "polarity": judgment_term.get("polarity", "unknown"),
                "confidence": judgment_term.get("confidence", 1.0),
                "payload": judgment_term.get("payload", {}),
                "_collected_at": _utc_now_iso(),
            }
        return {
            "source": "judgment_term",
            "kind": str(getattr(judgment_term, "kind", "unknown")),
            "polarity": str(getattr(judgment_term, "polarity", "unknown")),
            "confidence": float(getattr(judgment_term, "confidence", 1.0)),
            "payload": getattr(judgment_term, "payload", {}),
            "_collected_at": _utc_now_iso(),
        }

    def collect_from_certificate(self, certificate: Any) -> dict[str, Any]:
        """Extract an evidence record from a :class:`Certificate`.

        Parameters
        ----------
        certificate : Certificate
            The certificate to extract evidence from.

        Returns
        -------
        dict[str, Any]
            An evidence record.
        """
        if isinstance(certificate, dict):
            return {
                "source": "certificate",
                "certificate_id": certificate.get("certificate_id", str(uuid.uuid4())),
                "status": certificate.get("status", "unknown"),
                "claims": certificate.get("claims", []),
                "issued_at": certificate.get("issued_at", _utc_now_iso()),
                "_collected_at": _utc_now_iso(),
            }
        return {
            "source": "certificate",
            "certificate_id": str(getattr(certificate, "certificate_id", uuid.uuid4())),
            "status": str(getattr(certificate, "status", "unknown")),
            "claims": list(getattr(certificate, "claims", [])),
            "issued_at": str(getattr(certificate, "issued_at", _utc_now_iso())),
            "_collected_at": _utc_now_iso(),
        }

    def collect_from_test_result(
        self,
        coordinate: str,
        test_name: str,
        passed: bool,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a test result as evidence at a coordinate.

        Parameters
        ----------
        coordinate : str
            Coordinate to attach the evidence to.
        test_name : str
            Name of the test.
        passed : bool
            Whether the test passed.
        details : dict[str, Any] or None, optional
            Additional test result details.

        Returns
        -------
        dict[str, Any]
            The evidence record, also stored in ``evidence_store``.
        """
        item: dict[str, Any] = {
            "source": "test_result",
            "test_name": test_name,
            "passed": passed,
            "confidence": 1.0 if passed else 0.0,
            "details": details or {},
            "_coordinate": coordinate,
            "_collected_at": _utc_now_iso(),
        }
        item["_fingerprint"] = _evidence_fingerprint(item)
        if coordinate not in self.evidence_store:
            self.evidence_store[coordinate] = []
        self.evidence_store[coordinate].append(item)
        self.collection_log.append({
            "coordinate": coordinate,
            "source": "test_result",
            "test_name": test_name,
            "passed": passed,
            "timestamp": _utc_now_iso(),
        })
        return item

    def collect_from_static_analysis(
        self,
        coordinate: str,
        tool_name: str,
        findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Record static analysis findings as evidence at a coordinate.

        Parameters
        ----------
        coordinate : str
            Coordinate to attach evidence to.
        tool_name : str
            Name of the static analysis tool (e.g. ``"mypy"``, ``"ruff"``).
        findings : list[dict[str, Any]]
            List of finding dicts; each should have ``"severity"`` and
            ``"message"`` keys.

        Returns
        -------
        dict[str, Any]
            An aggregated evidence record.
        """
        error_count = sum(1 for f in findings if f.get("severity") in ("error", "critical"))
        warning_count = sum(1 for f in findings if f.get("severity") == "warning")
        passed = error_count == 0
        confidence = 1.0 if passed else max(0.0, 1.0 - 0.1 * error_count)
        item: dict[str, Any] = {
            "source": "static_analysis",
            "tool_name": tool_name,
            "findings": findings,
            "error_count": error_count,
            "warning_count": warning_count,
            "passed": passed,
            "confidence": confidence,
            "_coordinate": coordinate,
            "_collected_at": _utc_now_iso(),
        }
        item["_fingerprint"] = _evidence_fingerprint(
            {k: v for k, v in item.items() if k not in ("findings",)}
        )
        if coordinate not in self.evidence_store:
            self.evidence_store[coordinate] = []
        self.evidence_store[coordinate].append(item)
        self.collection_log.append({
            "coordinate": coordinate,
            "source": "static_analysis",
            "tool_name": tool_name,
            "error_count": error_count,
            "timestamp": _utc_now_iso(),
        })
        return item

    # -- filtering and summary ------------------------------------------------

    def filter_by_trust(self, min_trust: float) -> dict[str, list[dict[str, Any]]]:
        """Return only evidence items with confidence >= *min_trust*.

        Parameters
        ----------
        min_trust : float
            Minimum confidence value (inclusive).

        Returns
        -------
        dict[str, list[dict[str, Any]]]
            Filtered evidence store.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for coord, items in self.evidence_store.items():
            filtered = [
                item for item in items
                if float(item.get("confidence", 1.0)) >= min_trust
            ]
            if filtered:
                result[coord] = filtered
        return result

    def summarize_collected(self) -> dict[str, Any]:
        """Return a summary of all collected evidence.

        Returns
        -------
        dict[str, Any]
            Dict with ``"total_coordinates"``, ``"total_items"``,
            ``"per_coordinate"``, and ``"sources_used"`` keys.
        """
        per_coord: dict[str, int] = {
            coord: len(items) for coord, items in self.evidence_store.items()
        }
        all_items = [item for items in self.evidence_store.values() for item in items]
        sources: set[str] = {str(item.get("source", "unknown")) for item in all_items}
        return {
            "total_coordinates": len(self.evidence_store),
            "total_items": sum(per_coord.values()),
            "per_coordinate": per_coord,
            "sources_used": sorted(sources),
        }

    def get_evidence_for_coord(self, coordinate: str) -> list[dict[str, Any]]:
        """Return the evidence list for a single coordinate.

        Parameters
        ----------
        coordinate : str
            The coordinate identifier to look up.

        Returns
        -------
        list[dict[str, Any]]
            Evidence items; empty list if none collected.
        """
        return list(self.evidence_store.get(coordinate, []))

    def total_evidence_count(self) -> int:
        """Return the total number of evidence items across all coordinates.

        Returns
        -------
        int
            Total evidence item count.
        """
        return sum(len(items) for items in self.evidence_store.values())


# ---------------------------------------------------------------------------
# GluingDataComputer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GluingDataComputer:
    """Computes compatibility and gluing data across coordinate overlaps.

    For each pair of coordinates (A, B) the computer checks whether the
    evidence collected at A and B is mutually compatible and, if so, records a
    gluing datum describing *how* A and B agree on their shared judgments.

    Attributes
    ----------
    compatibility_rules : dict[str, Callable]
        Named compatibility checks; each ``fn(a, b) -> bool``.
    computed_gluings : dict[str, dict[str, Any]]
        Computed gluing data keyed by canonical overlap key.
    incompatible_overlaps : list[str]
        Overlap keys for which compatibility failed.
    """

    compatibility_rules: dict[str, Callable[..., bool]] = field(default_factory=dict)
    computed_gluings: dict[str, dict[str, Any]] = field(default_factory=dict)
    incompatible_overlaps: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._register_builtin_rules()

    # -- built-in rules -------------------------------------------------------

    def _register_builtin_rules(self) -> None:
        """Register default compatibility rules."""
        self.compatibility_rules["type_compatibility"] = self.check_type_compatibility
        self.compatibility_rules["behavioral_compatibility"] = (
            self.check_behavioral_compatibility
        )
        self.compatibility_rules["confidence_compatibility"] = (
            self._check_confidence_compatibility
        )

    def _check_confidence_compatibility(self, ev_a: Any, ev_b: Any) -> bool:
        """Return True iff both evidence dicts have confidence >= 0.5."""
        conf_a = float(ev_a.get("confidence", 1.0)) if isinstance(ev_a, dict) else 1.0
        conf_b = float(ev_b.get("confidence", 1.0)) if isinstance(ev_b, dict) else 1.0
        return conf_a >= 0.5 and conf_b >= 0.5

    def check_type_compatibility(self, type_a: Any, type_b: Any) -> bool:
        """Determine whether two type evidence records are compatible.

        Two type records are compatible if:
        * Both lack a ``"type_name"`` key (unknown types do not conflict), or
        * Their ``"type_name"`` values are equal, or
        * One is a sub-type of the other (heuristic: name prefix).

        Parameters
        ----------
        type_a : Any
            First type evidence record (dict or string).
        type_b : Any
            Second type evidence record (dict or string).

        Returns
        -------
        bool
            ``True`` if the types are compatible.
        """
        def _extract_type(t: Any) -> str | None:
            if isinstance(t, dict):
                return t.get("type_name") or t.get("value")
            if isinstance(t, str):
                return t
            return None

        name_a = _extract_type(type_a)
        name_b = _extract_type(type_b)
        if name_a is None or name_b is None:
            return True
        if name_a == name_b:
            return True
        # Heuristic sub-type: "Optional[X]" is compatible with "X"
        if name_a.startswith("Optional[") and name_a[9:-1] == name_b:
            return True
        if name_b.startswith("Optional[") and name_b[9:-1] == name_a:
            return True
        return False

    def check_behavioral_compatibility(self, behav_a: Any, behav_b: Any) -> bool:
        """Determine whether two behavioural evidence records are compatible.

        Two behavioural records are compatible if their postcondition sets are
        non-conflicting (no direct negation) and their confidence is both
        above 0.5.

        Parameters
        ----------
        behav_a : Any
            First behavioural evidence record.
        behav_b : Any
            Second behavioural evidence record.

        Returns
        -------
        bool
            ``True`` if the behaviours are compatible.
        """
        if isinstance(behav_a, dict) and isinstance(behav_b, dict):
            posts_a = set(str(p) for p in behav_a.get("postconditions", []))
            posts_b = set(str(p) for p in behav_b.get("postconditions", []))
            # Direct negation: "NOT X" in one, "X" in the other
            for post in posts_a:
                negation = post[4:] if post.startswith("NOT ") else f"NOT {post}"
                if negation in posts_b:
                    return False
            conf_a = float(behav_a.get("confidence", 1.0))
            conf_b = float(behav_b.get("confidence", 1.0))
            return conf_a >= 0.5 and conf_b >= 0.5
        return True

    # -- gluing computation ---------------------------------------------------

    def compute_overlap_key(self, coord_a: str, coord_b: str) -> str:
        """Compute the canonical overlap key for a pair of coordinates.

        Parameters
        ----------
        coord_a : str
            First coordinate identifier.
        coord_b : str
            Second coordinate identifier.

        Returns
        -------
        str
            Canonical key string ``"<lesser>||<greater>"``.
        """
        return _overlap_key_canonical(coord_a, coord_b)

    def compute_gluing(
        self,
        coord_a: str,
        coord_b: str,
        evidence_a: list[dict[str, Any]],
        evidence_b: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute the gluing datum for a single coordinate pair.

        The gluing datum records whether the two evidence lists are compatible,
        which rules passed, and a combined confidence estimate.

        Parameters
        ----------
        coord_a : str
            First coordinate.
        coord_b : str
            Second coordinate.
        evidence_a : list[dict[str, Any]]
            Evidence collected at *coord_a*.
        evidence_b : list[dict[str, Any]]
            Evidence collected at *coord_b*.

        Returns
        -------
        dict[str, Any]
            The gluing datum.
        """
        overlap_key = self.compute_overlap_key(coord_a, coord_b)
        rule_results: dict[str, bool] = {}
        for rule_name, rule_fn in self.compatibility_rules.items():
            try:
                # Apply rule to each pair of items and AND results
                compatible = True
                for ea in evidence_a:
                    for eb in evidence_b:
                        if not rule_fn(ea, eb):
                            compatible = False
                            break
                    if not compatible:
                        break
                rule_results[rule_name] = compatible
            except Exception as exc:  # noqa: BLE001
                rule_results[rule_name] = False
                rule_results[f"{rule_name}_error"] = str(exc)

        all_compatible = all(v for k, v in rule_results.items() if not k.endswith("_error"))
        combined_conf = self._combined_confidence(evidence_a + evidence_b)
        datum: dict[str, Any] = {
            "overlap_key": overlap_key,
            "coord_a": coord_a,
            "coord_b": coord_b,
            "compatible": all_compatible,
            "rule_results": rule_results,
            "combined_confidence": combined_conf,
            "computed_at": _utc_now_iso(),
        }
        self.computed_gluings[overlap_key] = datum
        if not all_compatible and overlap_key not in self.incompatible_overlaps:
            self.incompatible_overlaps.append(overlap_key)
        return datum

    def _combined_confidence(self, items: list[dict[str, Any]]) -> float:
        """Compute a harmonic-mean confidence from a list of evidence items.

        Parameters
        ----------
        items : list[dict[str, Any]]
            Evidence items, each optionally carrying a ``"confidence"`` key.

        Returns
        -------
        float
            Combined confidence in [0.0, 1.0].
        """
        if not items:
            return 0.0
        confs = [float(item.get("confidence", 1.0)) for item in items]
        nonzero = [c for c in confs if c > 0.0]
        if not nonzero:
            return 0.0
        return len(nonzero) / sum(1.0 / c for c in nonzero)

    def compute_all_gluings(
        self,
        evidence_map: dict[str, list[dict[str, Any]]],
        coordinate_pairs: list[tuple[str, str]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Compute gluing data for all coordinate pairs.

        Parameters
        ----------
        evidence_map : dict[str, list[dict[str, Any]]]
            Evidence indexed by coordinate.
        coordinate_pairs : list[tuple[str, str]] or None, optional
            Pairs to compute gluings for; defaults to all pairs from the
            evidence map.

        Returns
        -------
        dict[str, dict[str, Any]]
            Gluing data keyed by overlap key.
        """
        if coordinate_pairs is None:
            coords = list(evidence_map.keys())
            coordinate_pairs = [
                (coords[i], coords[j])
                for i in range(len(coords))
                for j in range(i + 1, len(coords))
            ]
        results: dict[str, dict[str, Any]] = {}
        for ca, cb in coordinate_pairs:
            ev_a = evidence_map.get(ca, [])
            ev_b = evidence_map.get(cb, [])
            datum = self.compute_gluing(ca, cb, ev_a, ev_b)
            results[datum["overlap_key"]] = datum
        return results

    def get_incompatible_pairs(self) -> list[tuple[str, str]]:
        """Return all pairs of coordinates with incompatible gluing data.

        Returns
        -------
        list[tuple[str, str]]
            List of ``(coord_a, coord_b)`` pairs.
        """
        pairs: list[tuple[str, str]] = []
        for key in self.incompatible_overlaps:
            parts = key.split("||", 1)
            if len(parts) == 2:
                pairs.append((parts[0], parts[1]))
        return pairs

    def register_compatibility_rule(
        self, rule_name: str, fn: Callable[..., bool]
    ) -> None:
        """Register a custom compatibility rule.

        Parameters
        ----------
        rule_name : str
            Unique name for the rule.
        fn : Callable[..., bool]
            A callable ``(evidence_a, evidence_b) -> bool``.
        """
        self.compatibility_rules[rule_name] = fn

    def summarize_gluings(self) -> dict[str, Any]:
        """Return a summary of all computed gluings.

        Returns
        -------
        dict[str, Any]
            Dict with ``"total_pairs"``, ``"compatible_count"``,
            ``"incompatible_count"``, and ``"avg_confidence"`` keys.
        """
        total = len(self.computed_gluings)
        compatible_count = sum(
            1 for d in self.computed_gluings.values() if d.get("compatible", False)
        )
        confs = [d.get("combined_confidence", 0.0) for d in self.computed_gluings.values()]
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        return {
            "total_pairs": total,
            "compatible_count": compatible_count,
            "incompatible_count": total - compatible_count,
            "avg_confidence": round(avg_conf, 4),
            "incompatible_overlap_keys": list(self.incompatible_overlaps),
        }


# ---------------------------------------------------------------------------
# WitnessMerger
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WitnessMerger:
    """Merges two or more satisfaction witnesses into a single witness.

    Merging combines evidence stores (taking the union), gluing data (taking
    the union with newer data overriding older on key conflicts), and trust
    levels (taking the maximum per coordinate).

    Attributes
    ----------
    merge_log : list[dict[str, Any]]
        Audit log of every merge operation performed.
    """

    merge_log: list[dict[str, Any]] = field(default_factory=list)

    # -- merge helpers --------------------------------------------------------

    def _merge_evidence(
        self,
        ev_a: dict[str, list[dict[str, Any]]],
        ev_b: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Merge two evidence stores by taking the union per coordinate.

        Duplicate items (same ``_fingerprint``) are dropped.

        Parameters
        ----------
        ev_a : dict[str, list[dict[str, Any]]]
            First evidence store.
        ev_b : dict[str, list[dict[str, Any]]]
            Second evidence store.

        Returns
        -------
        dict[str, list[dict[str, Any]]]
            Merged evidence store.
        """
        merged: dict[str, list[dict[str, Any]]] = {}
        all_coords = set(ev_a.keys()) | set(ev_b.keys())
        for coord in all_coords:
            seen_fps: set[str] = set()
            items: list[dict[str, Any]] = []
            for item in ev_a.get(coord, []) + ev_b.get(coord, []):
                fp = item.get("_fingerprint", _evidence_fingerprint(item))
                if fp not in seen_fps:
                    seen_fps.add(fp)
                    items.append(item)
            merged[coord] = items
        return merged

    def _merge_gluing_data(
        self,
        gd_a: dict[str, dict[str, Any]],
        gd_b: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Merge two gluing data dicts; *gd_b* overrides *gd_a* on key conflicts.

        Parameters
        ----------
        gd_a : dict[str, dict[str, Any]]
            First gluing data.
        gd_b : dict[str, dict[str, Any]]
            Second gluing data (takes precedence).

        Returns
        -------
        dict[str, dict[str, Any]]
            Merged gluing data.
        """
        merged: dict[str, dict[str, Any]] = dict(gd_a)
        for key, datum in gd_b.items():
            if key in merged:
                merged[key] = {**merged[key], **datum, "_merged": True}
            else:
                merged[key] = datum
        return merged

    def _merge_trust_levels(
        self,
        tl_a: dict[str, float],
        tl_b: dict[str, float],
    ) -> dict[str, float]:
        """Merge trust-level dicts by taking the maximum per coordinate.

        Parameters
        ----------
        tl_a : dict[str, float]
            First trust-level assignment.
        tl_b : dict[str, float]
            Second trust-level assignment.

        Returns
        -------
        dict[str, float]
            Merged trust levels.
        """
        merged: dict[str, float] = {}
        all_coords = set(tl_a.keys()) | set(tl_b.keys())
        for coord in all_coords:
            merged[coord] = max(tl_a.get(coord, 0.0), tl_b.get(coord, 0.0))
        return merged

    def _resolve_conflicts(
        self, witness_a: Any, witness_b: Any
    ) -> dict[str, Any]:
        """Detect and record conflicts between two witnesses.

        A conflict exists at a coordinate if both witnesses provide gluing data
        with opposite ``compatible`` flags for the same overlap key.

        Parameters
        ----------
        witness_a : SatisfactionWitness
            First witness.
        witness_b : SatisfactionWitness
            Second witness.

        Returns
        -------
        dict[str, Any]
            Dict mapping overlap key to ``{"conflict": True, "detail": ...}``.
        """
        gd_a = _get_witness_field(witness_a, "gluing_data", {})
        gd_b = _get_witness_field(witness_b, "gluing_data", {})
        conflicts: dict[str, Any] = {}
        for key in set(gd_a.keys()) & set(gd_b.keys()):
            compat_a = gd_a[key].get("compatible", True)
            compat_b = gd_b[key].get("compatible", True)
            if compat_a != compat_b:
                conflicts[key] = {
                    "conflict": True,
                    "compat_a": compat_a,
                    "compat_b": compat_b,
                    "resolution": "kept_b",
                }
        return conflicts

    # -- public interface -----------------------------------------------------

    def is_compatible_merge(self, witness_a: Any, witness_b: Any) -> bool:
        """Return ``True`` iff merging the two witnesses yields no conflicts.

        Parameters
        ----------
        witness_a : SatisfactionWitness
            First witness.
        witness_b : SatisfactionWitness
            Second witness.

        Returns
        -------
        bool
            ``True`` if no conflicts would arise from merging.
        """
        return len(self._resolve_conflicts(witness_a, witness_b)) == 0

    def merge(self, witness_a: Any, witness_b: Any) -> Any:
        """Merge two witnesses into a single new witness.

        Parameters
        ----------
        witness_a : SatisfactionWitness
            First witness.
        witness_b : SatisfactionWitness
            Second witness.

        Returns
        -------
        SatisfactionWitness
            The merged witness.
        """
        ev_a = _get_witness_field(witness_a, "evidence_map", {})
        ev_b = _get_witness_field(witness_b, "evidence_map", {})
        gd_a = _get_witness_field(witness_a, "gluing_data", {})
        gd_b = _get_witness_field(witness_b, "gluing_data", {})
        tl_a = _get_witness_field(witness_a, "trust_levels", {})
        tl_b = _get_witness_field(witness_b, "trust_levels", {})

        merged_ev = self._merge_evidence(ev_a, ev_b)
        merged_gd = self._merge_gluing_data(gd_a, gd_b)
        merged_tl = self._merge_trust_levels(tl_a, tl_b)
        conflicts = self._resolve_conflicts(witness_a, witness_b)

        coords_a = set(_get_witness_field(witness_a, "target_coordinates", ()))
        coords_b = set(_get_witness_field(witness_b, "target_coordinates", ()))
        merged_coords = tuple(sorted(coords_a | coords_b))

        spec_id_a = _get_witness_field(witness_a, "spec_id", "")
        spec_id_b = _get_witness_field(witness_b, "spec_id", "")
        merged_spec_id = spec_id_a if spec_id_a == spec_id_b else f"{spec_id_a}+{spec_id_b}"

        status = "complete" if all(c in merged_ev and merged_ev[c] for c in merged_coords) else "partial"

        self.merge_log.append({
            "witness_a_spec": spec_id_a,
            "witness_b_spec": spec_id_b,
            "conflict_count": len(conflicts),
            "merged_coord_count": len(merged_coords),
            "timestamp": _utc_now_iso(),
        })

        return _make_witness_dict(
            witness_id=str(uuid.uuid4()),
            spec_id=merged_spec_id,
            target_coordinates=merged_coords,
            evidence_map=merged_ev,
            gluing_data=merged_gd,
            trust_levels=merged_tl,
            status=status,
            created_at=_utc_now_iso(),
        )

    def merge_many(self, witnesses: list[Any]) -> Any:
        """Fold a list of witnesses using :meth:`merge`.

        Parameters
        ----------
        witnesses : list[SatisfactionWitness]
            Witnesses to merge; must have at least one element.

        Returns
        -------
        SatisfactionWitness
            The merged witness.

        Raises
        ------
        ValueError
            If *witnesses* is empty.
        """
        if not witnesses:
            raise ValueError("merge_many requires at least one witness.")
        result = witnesses[0]
        for w in witnesses[1:]:
            result = self.merge(result, w)
        return result


# ---------------------------------------------------------------------------
# WitnessValidator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WitnessValidator:
    """Validates satisfaction witnesses against their specifications.

    Checks that:
    * Every target coordinate in the spec has evidence in the witness.
    * Gluing data is present for all adjacent coordinate pairs.
    * Trust levels meet the minimum threshold.
    * Overlap keys in gluing data are consistently formatted.

    Attributes
    ----------
    validation_log : list[dict[str, Any]]
        Audit log of validation operations.
    strict_mode : bool
        If ``True``, warnings are treated as errors.
    """

    validation_log: list[dict[str, Any]] = field(default_factory=list)
    strict_mode: bool = False

    # -- individual checks ----------------------------------------------------

    def check_coverage(self, witness: Any, spec: Any) -> list[str]:
        """Return a list of coordinates missing from the witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to check.
        spec : Specification
            The specification defining required coordinates.

        Returns
        -------
        list[str]
            Error messages for each missing coordinate.
        """
        errors: list[str] = []
        required_coords = set(_spec_coords(spec))
        witness_coords = set(_get_witness_field(witness, "target_coordinates", ()))
        ev_map = _get_witness_field(witness, "evidence_map", {})
        for coord in required_coords:
            if coord not in witness_coords:
                errors.append(f"Coordinate '{coord}' required by spec but absent from witness.")
            elif not ev_map.get(coord):
                errors.append(f"Coordinate '{coord}' present but has no evidence.")
        return errors

    def check_gluing_consistency(self, witness: Any) -> list[str]:
        """Return a list of gluing consistency errors.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to check.

        Returns
        -------
        list[str]
            Error messages for inconsistent gluing data.
        """
        errors: list[str] = []
        gd = _get_witness_field(witness, "gluing_data", {})
        for key, datum in gd.items():
            if not isinstance(datum, dict):
                errors.append(f"Gluing datum for '{key}' is not a dict.")
                continue
            if "||" not in key:
                errors.append(
                    f"Overlap key '{key}' is not in canonical '<a>||<b>' format."
                )
            if datum.get("compatible") is False:
                errors.append(
                    f"Overlap '{key}' has incompatible gluing: "
                    f"{datum.get('rule_results', {})}."
                )
        return errors

    def check_trust_thresholds(
        self, witness: Any, min_trust: float = 0.5
    ) -> list[str]:
        """Return a list of coordinates with trust below *min_trust*.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to check.
        min_trust : float, optional
            Minimum acceptable trust level.

        Returns
        -------
        list[str]
            Error/warning messages for low-trust coordinates.
        """
        errors: list[str] = []
        trust_levels = _get_witness_field(witness, "trust_levels", {})
        ev_map = _get_witness_field(witness, "evidence_map", {})
        for coord in ev_map:
            trust = trust_levels.get(coord)
            if trust is not None and float(trust) < min_trust:
                label = "Error" if self.strict_mode else "Warning"
                errors.append(
                    f"{label}: trust at '{coord}' is {trust:.3f} < minimum {min_trust}."
                )
        return errors

    def check_overlap_keys_present(self, witness: Any) -> list[str]:
        """Verify that all expected overlap keys are present in gluing data.

        For N coordinates, at minimum we expect each adjacent pair's overlap key
        to be present if both sides have evidence.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to check.

        Returns
        -------
        list[str]
            Messages for missing overlap keys.
        """
        errors: list[str] = []
        ev_map = _get_witness_field(witness, "evidence_map", {})
        gd = _get_witness_field(witness, "gluing_data", {})
        coords_with_evidence = [c for c, items in ev_map.items() if items]
        for i, ca in enumerate(coords_with_evidence):
            for cb in coords_with_evidence[i + 1:]:
                key = _overlap_key_canonical(ca, cb)
                if key not in gd and self.strict_mode:
                    errors.append(
                        f"Expected overlap key '{key}' not present in gluing data."
                    )
        return errors

    # -- aggregate validation -------------------------------------------------

    def validate(
        self, witness: Any, spec: Any
    ) -> tuple[bool, list[str]]:
        """Fully validate a witness against its specification.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to validate.
        spec : Specification
            The specification to validate against.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if valid; ``(False, [error, ...])`` otherwise.
        """
        errors: list[str] = []
        errors.extend(self.check_coverage(witness, spec))
        errors.extend(self.check_gluing_consistency(witness))
        errors.extend(self.check_trust_thresholds(witness))
        errors.extend(self.check_overlap_keys_present(witness))
        valid = len(errors) == 0
        self.validation_log.append({
            "spec_id": _spec_id(spec),
            "witness_id": _get_witness_field(witness, "witness_id", "unknown"),
            "valid": valid,
            "error_count": len(errors),
            "timestamp": _utc_now_iso(),
        })
        return (valid, errors)

    def generate_validation_report(
        self, witness: Any, spec: Any
    ) -> dict[str, Any]:
        """Generate a detailed validation report dict.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to validate.
        spec : Specification
            The specification to validate against.

        Returns
        -------
        dict[str, Any]
            Structured report with sections for each check category.
        """
        coverage_errors = self.check_coverage(witness, spec)
        gluing_errors = self.check_gluing_consistency(witness)
        trust_errors = self.check_trust_thresholds(witness)
        overlap_errors = self.check_overlap_keys_present(witness)
        all_errors = coverage_errors + gluing_errors + trust_errors + overlap_errors
        ev_map = _get_witness_field(witness, "evidence_map", {})
        gd = _get_witness_field(witness, "gluing_data", {})
        return {
            "spec_id": _spec_id(spec),
            "witness_id": _get_witness_field(witness, "witness_id", "unknown"),
            "valid": len(all_errors) == 0,
            "strict_mode": self.strict_mode,
            "error_count": len(all_errors),
            "summary": {
                "coverage_errors": len(coverage_errors),
                "gluing_errors": len(gluing_errors),
                "trust_errors": len(trust_errors),
                "overlap_errors": len(overlap_errors),
            },
            "details": {
                "coverage_errors": coverage_errors,
                "gluing_errors": gluing_errors,
                "trust_errors": trust_errors,
                "overlap_errors": overlap_errors,
            },
            "statistics": {
                "total_coordinates": len(ev_map),
                "total_evidence_items": sum(len(v) for v in ev_map.values()),
                "total_gluing_pairs": len(gd),
                "compatible_pairs": sum(1 for d in gd.values() if d.get("compatible", True)),
            },
            "generated_at": _utc_now_iso(),
        }

    def quick_check(self, witness: Any) -> bool:
        """Perform a fast, non-exhaustive sanity check on a witness.

        Returns ``False`` immediately on the first sign of corruption.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to check.

        Returns
        -------
        bool
            ``True`` if the witness looks superficially valid.
        """
        ev_map = _get_witness_field(witness, "evidence_map", {})
        if not ev_map:
            return False
        gd = _get_witness_field(witness, "gluing_data", {})
        for key, datum in gd.items():
            if not isinstance(datum, dict):
                return False
            if datum.get("compatible") is False:
                return False
        coords = _get_witness_field(witness, "target_coordinates", ())
        if not coords:
            return False
        return True


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_witness(
    spec: Any,
    evidence_map: dict[str, list[dict[str, Any]]],
    gluing_data: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build a :class:`SatisfactionWitness` from pre-collected evidence.

    Parameters
    ----------
    spec : Specification
        The specification to satisfy.
    evidence_map : dict[str, list[dict[str, Any]]]
        Evidence keyed by coordinate.
    gluing_data : dict[str, dict[str, Any]] or None, optional
        Pre-computed gluing data; if ``None`` it is computed automatically.

    Returns
    -------
    SatisfactionWitness
        The assembled witness.
    """
    builder = WitnessBuilder.from_spec(spec)
    for coord, items in evidence_map.items():
        for item in items:
            builder.add_evidence(coord, item)
    if gluing_data is not None:
        for key, datum in gluing_data.items():
            builder.add_gluing(key, datum)
    else:
        computer = GluingDataComputer()
        auto_gluings = computer.compute_all_gluings(evidence_map)
        for key, datum in auto_gluings.items():
            builder.add_gluing(key, datum)
    # Assign default trust levels based on average confidence
    for coord, items in evidence_map.items():
        if items:
            avg_conf = sum(float(i.get("confidence", 1.0)) for i in items) / len(items)
            builder.assign_trust(coord, min(1.0, avg_conf))
    return builder.build()


def collect_evidence_for_spec(
    spec: Any,
    evidence_sources: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Collect evidence for every coordinate in a specification.

    Parameters
    ----------
    spec : Specification
        The specification whose coordinates to cover.
    evidence_sources : dict[str, Any]
        Mapping from coordinate id to its evidence source (see
        :meth:`EvidenceCollector.collect_for_coordinate`).

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Evidence indexed by coordinate.
    """
    collector = EvidenceCollector()
    for coord in _spec_coords(spec):
        source = evidence_sources.get(coord)
        if source is not None:
            collector.collect_for_coordinate(coord, source)
        else:
            collector.collect_for_coordinate(
                coord, [{"observation": "no_evidence_provided", "confidence": 0.0}]
            )
    return dict(collector.evidence_store)


def compute_gluing_data(
    evidence_map: dict[str, list[dict[str, Any]]],
    coordinate_pairs: list[tuple[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute gluing data across all (or specified) coordinate pairs.

    Parameters
    ----------
    evidence_map : dict[str, list[dict[str, Any]]]
        Evidence indexed by coordinate.
    coordinate_pairs : list[tuple[str, str]] or None, optional
        Pairs to compute; defaults to all pairs.

    Returns
    -------
    dict[str, dict[str, Any]]
        Gluing data keyed by canonical overlap key.
    """
    computer = GluingDataComputer()
    return computer.compute_all_gluings(evidence_map, coordinate_pairs)


def merge_witnesses(witnesses: list[Any]) -> Any:
    """Merge a list of satisfaction witnesses into one.

    Parameters
    ----------
    witnesses : list[SatisfactionWitness]
        Witnesses to merge; must be non-empty.

    Returns
    -------
    SatisfactionWitness
        The merged witness.

    Raises
    ------
    ValueError
        If *witnesses* is empty.
    """
    if not witnesses:
        raise ValueError("merge_witnesses requires at least one witness.")
    merger = WitnessMerger()
    return merger.merge_many(witnesses)


def validate_witness(
    witness: Any, spec: Any
) -> tuple[bool, list[str]]:
    """Validate a witness against its specification.

    Parameters
    ----------
    witness : SatisfactionWitness
        The witness to validate.
    spec : Specification
        The specification to validate against.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if valid; ``(False, [error, ...])`` otherwise.
    """
    validator = WitnessValidator()
    return validator.validate(witness, spec)


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Classes
    "WitnessBuilder",
    "EvidenceCollector",
    "GluingDataComputer",
    "WitnessMerger",
    "WitnessValidator",
    # Module-level functions
    "build_witness",
    "collect_evidence_for_spec",
    "compute_gluing_data",
    "merge_witnesses",
    "validate_witness",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
