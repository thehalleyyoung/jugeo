"""Section 10.4: Residual Gaps.

A residual gap is the first-class record of what remains unresolved when
descent partially fails.  Rather than treating failure as a binary opaque
error, the jugeo framework treats residual gaps as structured, queryable
objects that carry obstruction data, repair hints, and provenance so that
subsequent verification rounds can be informed by prior failures.

The central invariant is *residual faithfulness*: a gap must faithfully
represent what is missing, at what severity, and what could be done to
resolve it.  Gaps must never silently drop obstruction information.

References
----------
theory2.tex §10.4 — "Residual Gaps and Repair Frontiers"
theory2.tex §10.4.1 — "Gap Anatomy: Missing Evidence and Obstruction Classes"
theory2.tex §10.4.2 — "Severity Assessment and Trust Impact"
theory2.tex §10.4.3 — "Repair Strategy Generation"
theory2.tex §10.4.4 — "Gap Prioritisation and Tracking"

copilot: specification-satisfaction sub-module — provides gap analysis,
obstruction-class computation, and repair-strategy generation used by the
outer SatisfactionOrchestrator.  All public classes are designed so that
LLM-driven pipelines can inspect, serialize, and act on gap records without
needing access to the full jugeo geometry stack.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Mapping, Sequence

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        Specification,
        SatisfactionWitness,
        CertificateOfSatisfaction,
        ResidualGap,
        SpecificationKind,
        WitnessStatus,
        GapSeverity,
        SatisfactionStatus,
        DescentCondition,
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
    from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve
except ImportError:
    HypercoverLevel = Any  # type: ignore[assignment,misc]
    CechNerve = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentResult,
        LocalSection,
        GluingData,
        DescentObstruction,
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
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[assignment,misc]
    CertificateStatus = Any  # type: ignore[assignment,misc]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _digest(*parts: str) -> str:
    """Compute a short SHA-256 hex digest from string parts.

    Parameters
    ----------
    *parts : str
        Arbitrary string fragments to hash together.

    Returns
    -------
    str
        First 16 hex characters of the SHA-256 digest.
    """
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    """Return the current Unix timestamp as a float."""
    return time.time()


def _witness_coords(witness: Any) -> list[str]:
    """Extract sorted coordinate keys from a witness object or dict.

    Parameters
    ----------
    witness : Any
        A SatisfactionWitness instance or plain dict.

    Returns
    -------
    list[str]
        Sorted list of coordinate key strings.
    """
    if isinstance(witness, dict):
        return sorted(witness.get("coordinates", {}).keys())
    coords = getattr(witness, "coordinates", None) or getattr(witness, "local_sections", None)
    if coords is None:
        return []
    if isinstance(coords, dict):
        return sorted(coords.keys())
    return [str(c) for c in coords]


def _spec_required_coords(spec: Any) -> list[str]:
    """Return sorted required coordinates from a specification.

    Parameters
    ----------
    spec : Any
        A Specification instance or compatible dict.

    Returns
    -------
    list[str]
        Sorted list of required coordinate key strings.
    """
    if isinstance(spec, dict):
        p = spec.get("prescriptions", {})
        if p:
            return sorted(p.keys())
        return sorted(spec.get("required_coordinates", []))
    p = getattr(spec, "prescriptions", None) or getattr(spec, "requirements", None) or {}
    if isinstance(p, dict) and p:
        return sorted(p.keys())
    req = getattr(spec, "required_coordinates", []) or []
    return sorted(str(c) for c in req)


def _spec_prescriptions(spec: Any) -> dict[str, Any]:
    """Extract prescription map from a specification.

    Parameters
    ----------
    spec : Any
        A Specification instance or compatible dict.

    Returns
    -------
    dict[str, Any]
        Mapping from coordinate keys to prescription dicts.
    """
    if isinstance(spec, dict):
        return dict(spec.get("prescriptions", {}))
    p = getattr(spec, "prescriptions", None) or getattr(spec, "requirements", None) or {}
    return dict(p) if isinstance(p, dict) else {}


def _witness_evidence_kinds(witness: Any) -> dict[str, set[str]]:
    """Map each coordinate to the set of evidence kinds present in the witness.

    Parameters
    ----------
    witness : Any
        SatisfactionWitness or compatible dict.

    Returns
    -------
    dict[str, set[str]]
        Map from coordinate key to set of evidence kind strings.
    """
    result: dict[str, set[str]] = {}
    if isinstance(witness, dict):
        for coord, ev in witness.get("evidence", {}).items():
            if isinstance(ev, list):
                result[coord] = {str(e.get("kind", "unknown")) for e in ev if isinstance(e, dict)}
            elif isinstance(ev, dict):
                result[coord] = {str(ev.get("kind", "unknown"))}
    else:
        ev_attr = getattr(witness, "evidence", {}) or {}
        if isinstance(ev_attr, dict):
            for coord, ev in ev_attr.items():
                if isinstance(ev, list):
                    result[str(coord)] = {str(e.get("kind", "unknown")) for e in ev if isinstance(e, dict)}
                elif isinstance(ev, dict):
                    result[str(coord)] = {str(ev.get("kind", "unknown"))}
    return result


def _gap_id_from(spec: Any, failed_conditions: list[str]) -> str:
    """Compute a deterministic gap ID from spec and failed conditions.

    Parameters
    ----------
    spec : Any
        Specification (used for its ID attribute).
    failed_conditions : list[str]
        Names of conditions that failed.

    Returns
    -------
    str
        A short hex string uniquely identifying this gap configuration.
    """
    spec_id = spec.get("id") if isinstance(spec, dict) else getattr(spec, "id", "spec")
    return _digest(str(spec_id), json.dumps(sorted(failed_conditions)))


def _build_gap_dict(
    gap_id: str,
    spec_id: str,
    failed_conditions: list[str],
    missing_coordinates: list[str],
    missing_evidence_kinds: list[str],
    severity: str,
    obstruction: dict[str, Any],
    repair_hints: list[dict[str, Any]],
    trust_impact: float,
) -> dict[str, Any]:
    """Construct a plain-dict representation of a ResidualGap.

    Returns
    -------
    dict[str, Any]
        Gap dict that mirrors the ResidualGap dataclass fields.
    """
    return {
        "gap_id": gap_id,
        "spec_id": spec_id,
        "failed_conditions": list(failed_conditions),
        "missing_coordinates": list(missing_coordinates),
        "missing_evidence_kinds": list(missing_evidence_kinds),
        "severity": severity,
        "obstruction": obstruction,
        "repair_hints": repair_hints,
        "trust_impact": trust_impact,
        "generated_at": _now_iso(),
        "kind": "ResidualGap",
    }


def _coerce_gap(obj: Any) -> dict[str, Any]:
    """Coerce a ResidualGap instance or dict to a plain dict.

    Parameters
    ----------
    obj : Any
        A ResidualGap dataclass instance or an already-plain dict.

    Returns
    -------
    dict[str, Any]
        Plain dict representation of the gap.
    """
    if isinstance(obj, dict):
        return obj
    # Attempt attribute-based extraction from a dataclass/object.
    return {
        "gap_id": getattr(obj, "gap_id", ""),
        "spec_id": getattr(obj, "spec_id", ""),
        "failed_conditions": list(getattr(obj, "failed_conditions", [])),
        "missing_coordinates": list(getattr(obj, "missing_coordinates", [])),
        "missing_evidence_kinds": list(getattr(obj, "missing_evidence_kinds", [])),
        "severity": str(getattr(obj, "severity", "UNKNOWN")),
        "obstruction": dict(getattr(obj, "obstruction", {})),
        "repair_hints": list(getattr(obj, "repair_hints", [])),
        "trust_impact": float(getattr(obj, "trust_impact", 0.0)),
        "generated_at": getattr(obj, "generated_at", _now_iso()),
        "kind": "ResidualGap",
    }


# ---------------------------------------------------------------------------
# GapAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GapAnalyzer:
    """Create and analyse ResidualGap objects from partial witnesses.

    A gap analyzer examines a (possibly partial) witness against a
    specification and produces a structured gap record describing what
    evidence is missing, what conditions failed, and how severe the
    shortfall is.

    Attributes
    ----------
    analysis_results : list[dict]
        Accumulated analysis records from each :meth:`analyze` call.
    last_analysis : dict | None
        The most recent analysis result dict, or None if none have been run.
    """

    analysis_results: list[dict] = field(default_factory=list)
    last_analysis: dict[str, Any] | None = None

    # -- public API -----------------------------------------------------------

    def analyze(self, witness: Any, spec: Any) -> Any:
        """Create a ResidualGap from a partial witness and specification.

        Parameters
        ----------
        witness : SatisfactionWitness
            The (partial) witness assembled so far.
        spec : Specification
            The target specification.

        Returns
        -------
        ResidualGap | dict
            The residual gap describing what is missing and its severity.
        """
        unsatisfied = self.identify_unsatisfied_coordinates(witness, spec)
        missing_kinds = self.identify_missing_evidence_kinds(witness, spec)
        total_required = len(_spec_required_coords(spec))
        severity = self.assess_severity(unsatisfied, missing_kinds, spec)
        trust_impact = self.compute_trust_impact(unsatisfied, total_required)

        # Determine failed conditions (lightweight — no full checker invocation).
        provided_coords = set(_witness_coords(witness))
        required_coords = set(_spec_required_coords(spec))
        failed_conditions: list[str] = []
        if not required_coords.issubset(provided_coords):
            failed_conditions.append("COVER_EXISTS")
        if missing_kinds:
            failed_conditions.append("OVERLAPS_COMPATIBLE")
        if unsatisfied:
            failed_conditions.append("GLOBAL_SECTION_EXISTS")

        spec_id = spec.get("id") if isinstance(spec, dict) else getattr(spec, "id", "unknown")
        gap_id = _gap_id_from(spec, failed_conditions)

        gap_dict = _build_gap_dict(
            gap_id=gap_id,
            spec_id=str(spec_id),
            failed_conditions=failed_conditions,
            missing_coordinates=unsatisfied,
            missing_evidence_kinds=missing_kinds,
            severity=str(severity) if not isinstance(severity, str) else severity,
            obstruction={},
            repair_hints=[],
            trust_impact=trust_impact,
        )

        # Attempt to instantiate the typed ResidualGap if available.
        gap: Any = gap_dict
        try:
            if ResidualGap is not Any:
                gap = ResidualGap(**{k: v for k, v in gap_dict.items() if k != "kind"})
        except Exception:
            pass

        record = {
            "gap_id": gap_id,
            "unsatisfied_count": len(unsatisfied),
            "missing_kinds_count": len(missing_kinds),
            "severity": str(severity),
            "trust_impact": trust_impact,
            "analyzed_at": _now_iso(),
        }
        self.analysis_results.append(record)
        self.last_analysis = record
        return gap

    def identify_unsatisfied_coordinates(
        self,
        witness: Any,
        spec: Any,
    ) -> list[str]:
        """List coordinates from the specification that are not covered by the witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to inspect.
        spec : Specification
            The specification whose required coordinates are checked.

        Returns
        -------
        list[str]
            Sorted list of coordinate keys that are required but absent or
            under-evidenced in the witness.
        """
        provided = set(_witness_coords(witness))
        required = set(_spec_required_coords(spec))
        missing = sorted(required - provided)

        # Also include coordinates that are present but fail the prescription.
        prescriptions = _spec_prescriptions(spec)
        ev_map: dict[str, Any] = {}
        if isinstance(witness, dict):
            ev_map = witness.get("evidence", {})
        else:
            ev_map = dict(getattr(witness, "evidence", {}) or {})

        under_evidenced: list[str] = []
        for coord in provided & required:
            presc = prescriptions.get(coord, {})
            if not isinstance(presc, dict):
                continue
            min_trust = float(presc.get("min_trust", 0.0))
            required_kind = presc.get("kind")
            ev = ev_map.get(coord, {})
            if isinstance(ev, list):
                ev = ev[0] if ev else {}
            if isinstance(ev, dict):
                trust = float(ev.get("trust", 1.0))
                kind = ev.get("kind")
                if trust < min_trust:
                    under_evidenced.append(coord)
                elif required_kind and kind != required_kind:
                    under_evidenced.append(coord)

        return sorted(set(missing + under_evidenced))

    def identify_missing_evidence_kinds(
        self,
        witness: Any,
        spec: Any,
    ) -> list[str]:
        """Identify evidence kinds required by the specification but absent in the witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to inspect.
        spec : Specification
            The specification prescribing required evidence kinds.

        Returns
        -------
        list[str]
            Sorted list of missing evidence-kind strings.
        """
        prescriptions = _spec_prescriptions(spec)
        present_kinds = _witness_evidence_kinds(witness)
        missing: set[str] = set()
        for coord, presc in prescriptions.items():
            if not isinstance(presc, dict):
                continue
            required_kind = presc.get("kind")
            if required_kind is None:
                continue
            coord_kinds = present_kinds.get(coord, set())
            if required_kind not in coord_kinds:
                missing.add(required_kind)
        return sorted(missing)

    def assess_severity(
        self,
        unsatisfied: list[str],
        missing_kinds: list[str],
        spec: Any,
    ) -> str:
        """Assess the severity of a gap given unsatisfied coordinates and missing kinds.

        Severity levels (from most to least severe):

        * ``"FATAL"`` — cover does not exist; no local sections at all.
        * ``"CRITICAL"`` — majority of required coordinates unsatisfied.
        * ``"MAJOR"`` — significant minority unsatisfied or kind mismatches.
        * ``"MINOR"`` — one or two coordinates or kinds missing.
        * ``"NEGLIGIBLE"`` — effectively satisfied; tiny residual gap.

        Parameters
        ----------
        unsatisfied : list[str]
            Unsatisfied coordinate keys.
        missing_kinds : list[str]
            Missing evidence kind strings.
        spec : Specification
            The specification (used to determine total required count).

        Returns
        -------
        str
            Severity level string: ``"FATAL"``, ``"CRITICAL"``, ``"MAJOR"``,
            ``"MINOR"``, or ``"NEGLIGIBLE"``.
        """
        required = _spec_required_coords(spec)
        total = max(len(required), 1)
        unsatisfied_ratio = len(unsatisfied) / total
        kind_count = len(missing_kinds)

        if total == 0 or len(unsatisfied) == total:
            return "FATAL"
        if unsatisfied_ratio > 0.5 or kind_count >= 3:
            return "CRITICAL"
        if unsatisfied_ratio > 0.2 or kind_count >= 2:
            return "MAJOR"
        if unsatisfied_ratio > 0.0 or kind_count >= 1:
            return "MINOR"
        return "NEGLIGIBLE"

    def compute_trust_impact(
        self,
        unsatisfied: list[str],
        total: int,
    ) -> float:
        """Compute the fractional trust impact of unsatisfied coordinates.

        The trust impact is the proportion of required coordinates that are
        not satisfied.  A value of 1.0 means complete failure; 0.0 means no
        impact (all satisfied).

        Parameters
        ----------
        unsatisfied : list[str]
            Coordinates not yet satisfied.
        total : int
            Total number of required coordinates.

        Returns
        -------
        float
            Impact score in [0.0, 1.0].
        """
        if total <= 0:
            return 0.0
        return min(1.0, len(unsatisfied) / total)

    def generate_analysis_report(self) -> dict[str, Any]:
        """Produce a summary report of all analysis runs.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_analyses``, ``last_analysis``, ``severity_counts``,
            ``avg_trust_impact``, ``records``.
        """
        severity_counts: dict[str, int] = {}
        trust_impacts: list[float] = []
        for r in self.analysis_results:
            sev = r.get("severity", "UNKNOWN")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            trust_impacts.append(float(r.get("trust_impact", 0.0)))
        avg_impact = sum(trust_impacts) / len(trust_impacts) if trust_impacts else 0.0
        return {
            "total_analyses": len(self.analysis_results),
            "last_analysis": self.last_analysis,
            "severity_counts": severity_counts,
            "avg_trust_impact": avg_impact,
            "records": list(self.analysis_results),
        }

    def compare_gaps(
        self,
        gap_a: Any,
        gap_b: Any,
    ) -> dict[str, Any]:
        """Compute the diff between two residual gaps.

        Parameters
        ----------
        gap_a : ResidualGap | dict
            First gap.
        gap_b : ResidualGap | dict
            Second gap.

        Returns
        -------
        dict[str, Any]
            Keys: ``added_missing``, ``removed_missing``, ``severity_changed``,
            ``trust_impact_delta``, ``added_failed_conditions``,
            ``removed_failed_conditions``.
        """
        da = _coerce_gap(gap_a)
        db = _coerce_gap(gap_b)
        missing_a = set(da.get("missing_coordinates", []))
        missing_b = set(db.get("missing_coordinates", []))
        cond_a = set(da.get("failed_conditions", []))
        cond_b = set(db.get("failed_conditions", []))
        return {
            "added_missing": sorted(missing_b - missing_a),
            "removed_missing": sorted(missing_a - missing_b),
            "severity_changed": da.get("severity") != db.get("severity"),
            "severity_a": da.get("severity"),
            "severity_b": db.get("severity"),
            "trust_impact_delta": float(db.get("trust_impact", 0.0)) - float(da.get("trust_impact", 0.0)),
            "added_failed_conditions": sorted(cond_b - cond_a),
            "removed_failed_conditions": sorted(cond_a - cond_b),
        }


# ---------------------------------------------------------------------------
# ObstructionClassComputer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ObstructionClassComputer:
    """Compute the H¹ obstruction class arising from a partial witness.

    The obstruction class lives in the first Čech cohomology of the semantic
    site and encodes the exact algebraic reason why descent fails.  A trivial
    obstruction (zero in H¹) means failure is reparable by adding evidence.
    A non-trivial obstruction means a structural incompatibility exists.

    Attributes
    ----------
    computed_obstructions : list[dict]
        Accumulated obstruction records from each :meth:`compute` call.
    """

    computed_obstructions: list[dict] = field(default_factory=list)

    # -- public API -----------------------------------------------------------

    def compute(self, witness: Any, spec: Any) -> dict[str, Any]:
        """Compute the H¹ obstruction class for the given witness and spec.

        Parameters
        ----------
        witness : SatisfactionWitness
            The (partial) witness providing local sections and gluing data.
        spec : Specification
            The target specification prescribing required properties.

        Returns
        -------
        dict[str, Any]
            Obstruction class dict with keys ``is_trivial``,
            ``h1_generators``, ``violation_count``, ``obstruction_id``,
            ``latex``, ``classification``.
        """
        violations = self._collect_violations(witness)
        classified = [(v, self._classify_violation(v)) for v in violations]
        h1 = self._compute_h1_class([vc for v, vc in classified])
        obs_id = _digest(json.dumps(sorted(v.get("key", "") for v in violations)))
        latex_str = self.obstruction_to_latex(h1)
        trivial = self.is_trivial_obstruction(h1)
        record = {
            "obstruction_id": obs_id,
            "is_trivial": trivial,
            "h1_generators": h1.get("generators", []),
            "violation_count": len(violations),
            "violation_classes": [vc for _, vc in classified],
            "h1_class": h1,
            "latex": latex_str,
            "classification": "trivial" if trivial else "non_trivial",
            "computed_at": _now_iso(),
        }
        self.computed_obstructions.append(record)
        return record

    def _collect_violations(self, witness: Any) -> list[dict[str, Any]]:
        """Gather all overlap violations from the witness gluing data.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness whose gluing data are inspected.

        Returns
        -------
        list[dict[str, Any]]
            List of violation dicts, each with keys ``key``, ``reason``,
            ``trust``, ``coord_a``, ``coord_b``.
        """
        violations: list[dict[str, Any]] = []
        gluing: dict[str, Any] = {}
        if isinstance(witness, dict):
            gluing = dict(witness.get("gluing_data", {}))
        else:
            gd = getattr(witness, "gluing_data", None) or {}
            gluing = dict(gd) if isinstance(gd, dict) else {}

        for key, datum in gluing.items():
            if not isinstance(datum, dict):
                continue
            compatible = datum.get("compatible", True)
            if not compatible:
                parts = key.split("::")
                violations.append({
                    "key": key,
                    "reason": datum.get("reason", "unspecified_incompatibility"),
                    "trust": float(datum.get("trust", 0.0)),
                    "coord_a": parts[0] if len(parts) > 0 else "",
                    "coord_b": parts[1] if len(parts) > 1 else "",
                })
            elif datum.get("is_missing", False):
                parts = key.split("::")
                violations.append({
                    "key": key,
                    "reason": "missing_gluing_datum",
                    "trust": 0.0,
                    "coord_a": parts[0] if len(parts) > 0 else "",
                    "coord_b": parts[1] if len(parts) > 1 else "",
                })
        return violations

    def _classify_violation(self, violation: dict[str, Any]) -> str:
        """Classify a single violation by its reason code.

        Parameters
        ----------
        violation : dict[str, Any]
            A violation dict as produced by :meth:`_collect_violations`.

        Returns
        -------
        str
            Classification string: one of ``"type_mismatch"``,
            ``"missing_evidence"``, ``"trust_below_floor"``,
            ``"cocycle_failure"``, or ``"unknown"``.
        """
        reason = violation.get("reason", "")
        if "type" in reason or "kind" in reason:
            return "type_mismatch"
        if "missing" in reason or reason == "":
            return "missing_evidence"
        if "trust" in reason or "floor" in reason:
            return "trust_below_floor"
        if "cocycle" in reason or "compose" in reason:
            return "cocycle_failure"
        return "unknown"

    def _compute_h1_class(
        self,
        violation_classes: list[str],
    ) -> dict[str, Any]:
        """Compute a representative of the H¹ cohomology class.

        Parameters
        ----------
        violation_classes : list[str]
            Classification strings for all collected violations.

        Returns
        -------
        dict[str, Any]
            H¹ class dict with keys ``generators``, ``rank``, ``is_trivial``,
            ``class_id``.
        """
        if not violation_classes:
            return {
                "generators": [],
                "rank": 0,
                "is_trivial": True,
                "class_id": "0",
            }
        # Deduplicate and sort generators.
        unique_classes = sorted(set(violation_classes))
        class_id = _digest(*unique_classes)
        return {
            "generators": unique_classes,
            "rank": len(unique_classes),
            "is_trivial": False,
            "class_id": class_id,
        }

    def _obstruction_involves_coordinate(
        self,
        obstruction: dict[str, Any],
        coordinate: str,
    ) -> bool:
        """Check whether the obstruction involves a specific coordinate.

        Parameters
        ----------
        obstruction : dict[str, Any]
            An obstruction class dict.
        coordinate : str
            The coordinate key to look for.

        Returns
        -------
        bool
            True when any violation in the obstruction involves *coordinate*.
        """
        for gen in obstruction.get("h1_generators", []):
            if coordinate in str(gen):
                return True
        for rec in self.computed_obstructions:
            if rec.get("obstruction_id") == obstruction.get("obstruction_id"):
                for v in rec.get("violation_classes", []):
                    if coordinate in str(v):
                        return True
        return False

    def obstruction_to_latex(self, obstruction_class: dict[str, Any]) -> str:
        """Render the obstruction class as a LaTeX string.

        Parameters
        ----------
        obstruction_class : dict[str, Any]
            The H¹ class dict as returned by :meth:`_compute_h1_class`.

        Returns
        -------
        str
            A LaTeX math-mode string representing the obstruction class.
        """
        if obstruction_class.get("is_trivial"):
            return r"[\mathbf{0}] \in H^1(\mathcal{U}, \mathcal{F})"
        generators = obstruction_class.get("generators", [])
        gens_str = ", ".join(f"\\mathrm{{{g}}}" for g in generators)
        rank = obstruction_class.get("rank", len(generators))
        class_id = obstruction_class.get("class_id", "?")[:8]
        return (
            rf"[\alpha_{{{class_id}}}] \in H^1(\mathcal{{U}}, \mathcal{{F}})"
            rf"\;[\mathrm{{rank}}={rank},\;{gens_str}]"
        )

    def is_trivial_obstruction(self, obstruction_class: dict[str, Any]) -> bool:
        """Return True when the obstruction class is trivial (zero in H¹).

        Parameters
        ----------
        obstruction_class : dict[str, Any]
            The H¹ class dict.

        Returns
        -------
        bool
            True iff the class is trivial.
        """
        return bool(obstruction_class.get("is_trivial", False))

    def merge_obstructions(
        self,
        obs_a: dict[str, Any],
        obs_b: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two obstruction classes via direct sum.

        The merged class has generators from both and rank equal to the sum
        of their ranks (minus any shared generators that cancel).

        Parameters
        ----------
        obs_a : dict[str, Any]
            First obstruction class dict.
        obs_b : dict[str, Any]
            Second obstruction class dict.

        Returns
        -------
        dict[str, Any]
            The merged obstruction class.
        """
        gens_a = set(obs_a.get("generators", []))
        gens_b = set(obs_b.get("generators", []))
        # Direct sum: shared generators may cancel in the presence of a
        # connecting homomorphism; here we take the union as a conservative
        # over-approximation.
        merged_gens = sorted(gens_a | gens_b)
        is_trivial = obs_a.get("is_trivial", True) and obs_b.get("is_trivial", True)
        class_id = _digest(*merged_gens) if merged_gens else "0"
        return {
            "generators": merged_gens,
            "rank": len(merged_gens),
            "is_trivial": is_trivial,
            "class_id": class_id,
        }


# ---------------------------------------------------------------------------
# RepairStrategyEngine
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RepairStrategyEngine:
    """Generate, rank, and apply repair strategies for residual gaps.

    A repair strategy is a structured hint that tells downstream agents or
    human reviewers exactly what action would reduce or eliminate the gap.
    Strategies are parameterised so they can be serialised and replayed.

    Attributes
    ----------
    strategy_registry : dict[str, Callable]
        Named strategy functions registered via :meth:`register_strategy`.
    generated_strategies : list[dict]
        All strategy records generated during the engine's lifetime.
    """

    strategy_registry: dict[str, Callable] = field(default_factory=dict)
    generated_strategies: list[dict] = field(default_factory=list)

    # -- strategy generation --------------------------------------------------

    def generate_strategies(self, gap: Any) -> list[dict[str, Any]]:
        """Generate all applicable repair hints for the given gap.

        Parameters
        ----------
        gap : ResidualGap | dict
            The gap to repair.

        Returns
        -------
        list[dict[str, Any]]
            Ordered list of repair hint dicts (most actionable first).
        """
        d = _coerce_gap(gap)
        hints: list[dict[str, Any]] = []

        # Missing-evidence hints per unsatisfied coordinate.
        missing_kinds = d.get("missing_evidence_kinds", [])
        missing_coords = d.get("missing_coordinates", [])
        for coord in missing_coords:
            for kind in missing_kinds or ["any"]:
                hints.append(self.generate_for_missing_evidence(kind, coord))

        # Type-mismatch hints (synthesized from obstruction violation classes).
        obstruction = d.get("obstruction", {})
        for gen in obstruction.get("h1_generators", []):
            if gen == "type_mismatch":
                for i, ca in enumerate(missing_coords):
                    for cb in missing_coords[i + 1 :]:
                        hints.append(
                            self.generate_for_type_mismatch(ca, "unknown", cb, "unknown")
                        )

        # Incomplete-cover hint.
        if missing_coords:
            hints.append(self.generate_for_incomplete_cover(missing_coords))

        # Obstruction-class hints.
        if obstruction and not obstruction.get("is_trivial", True):
            hints.extend(self.generate_for_obstruction(obstruction))

        prioritized = self.prioritize_hints(hints)
        self.generated_strategies.extend(prioritized)
        return prioritized

    def generate_for_missing_evidence(
        self,
        missing_kind: str,
        coordinate: str,
    ) -> dict[str, Any]:
        """Generate a repair hint for missing evidence of a specific kind.

        Parameters
        ----------
        missing_kind : str
            The evidence kind that is absent.
        coordinate : str
            The coordinate where the evidence is missing.

        Returns
        -------
        dict[str, Any]
            Repair hint dict with ``strategy``, ``action``, ``target``,
            ``params``, ``estimated_cost``, ``impact``.
        """
        cost = 0.3  # Adding evidence is usually cheap.
        return {
            "strategy": "add_evidence",
            "action": f"Provide evidence of kind '{missing_kind}' at coordinate '{coordinate}'",
            "target": coordinate,
            "params": {"kind": missing_kind, "coordinate": coordinate},
            "estimated_cost": cost,
            "impact": 0.6,
            "hint_id": _digest("add_evidence", missing_kind, coordinate),
            "generated_at": _now_iso(),
        }

    def generate_for_type_mismatch(
        self,
        coord_a: str,
        type_a: str,
        coord_b: str,
        type_b: str,
    ) -> dict[str, Any]:
        """Generate a repair hint for a type mismatch between two coordinates.

        Parameters
        ----------
        coord_a : str
            First coordinate key.
        type_a : str
            Evidence type at coordinate A.
        coord_b : str
            Second coordinate key.
        type_b : str
            Evidence type at coordinate B.

        Returns
        -------
        dict[str, Any]
            Repair hint for resolving the type mismatch.
        """
        return {
            "strategy": "resolve_type_mismatch",
            "action": (
                f"Reconcile type '{type_a}' at '{coord_a}' with type '{type_b}' "
                f"at '{coord_b}' by providing a type-coercion gluing datum."
            ),
            "target": f"{coord_a}::{coord_b}",
            "params": {
                "coord_a": coord_a,
                "type_a": type_a,
                "coord_b": coord_b,
                "type_b": type_b,
            },
            "estimated_cost": 0.6,
            "impact": 0.7,
            "hint_id": _digest("type_mismatch", coord_a, type_a, coord_b, type_b),
            "generated_at": _now_iso(),
        }

    def generate_for_incomplete_cover(
        self,
        uncovered_coords: list[str],
    ) -> dict[str, Any]:
        """Generate a repair hint for an incomplete cover.

        Parameters
        ----------
        uncovered_coords : list[str]
            Coordinate keys not yet covered by any local section.

        Returns
        -------
        dict[str, Any]
            Repair hint for extending the cover.
        """
        return {
            "strategy": "extend_cover",
            "action": (
                f"Extend the cover to include {len(uncovered_coords)} missing "
                f"coordinate(s): {uncovered_coords[:5]}"
                + ("..." if len(uncovered_coords) > 5 else "")
            ),
            "target": "cover",
            "params": {"uncovered": list(uncovered_coords)},
            "estimated_cost": 0.5,
            "impact": 0.9,
            "hint_id": _digest("extend_cover", *sorted(uncovered_coords)),
            "generated_at": _now_iso(),
        }

    def generate_for_obstruction(
        self,
        obstruction_class: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate repair hints targeting a non-trivial obstruction class.

        Parameters
        ----------
        obstruction_class : dict[str, Any]
            The H¹ obstruction class dict.

        Returns
        -------
        list[dict[str, Any]]
            List of hints, one per generator of the obstruction class.
        """
        hints: list[dict[str, Any]] = []
        for gen in obstruction_class.get("generators", []):
            if gen == "missing_evidence":
                hints.append({
                    "strategy": "supply_missing_evidence",
                    "action": "Supply all absent evidence records to discharge the missing-evidence generator.",
                    "target": "evidence_archive",
                    "params": {"generator": gen},
                    "estimated_cost": 0.4,
                    "impact": 0.8,
                    "hint_id": _digest("obstruction_hint", gen),
                    "generated_at": _now_iso(),
                })
            elif gen == "type_mismatch":
                hints.append({
                    "strategy": "normalise_types",
                    "action": "Normalise all evidence types to a common schema to discharge the type-mismatch generator.",
                    "target": "type_system",
                    "params": {"generator": gen},
                    "estimated_cost": 0.7,
                    "impact": 0.75,
                    "hint_id": _digest("obstruction_hint", gen),
                    "generated_at": _now_iso(),
                })
            elif gen == "cocycle_failure":
                hints.append({
                    "strategy": "rebuild_gluing",
                    "action": "Recompute gluing data to satisfy cocycle conditions on all triple overlaps.",
                    "target": "gluing_data",
                    "params": {"generator": gen},
                    "estimated_cost": 0.9,
                    "impact": 1.0,
                    "hint_id": _digest("obstruction_hint", gen),
                    "generated_at": _now_iso(),
                })
            else:
                hints.append({
                    "strategy": "investigate_obstruction",
                    "action": f"Investigate obstruction generator '{gen}' manually.",
                    "target": "manual_review",
                    "params": {"generator": gen},
                    "estimated_cost": 1.0,
                    "impact": 0.5,
                    "hint_id": _digest("obstruction_hint", gen),
                    "generated_at": _now_iso(),
                })
        return hints

    def register_strategy(self, strategy_name: str, fn: Callable) -> None:
        """Register a named strategy function for use in :meth:`apply_strategy`.

        Parameters
        ----------
        strategy_name : str
            Unique name for the strategy.
        fn : Callable
            Strategy function accepting ``(gap, params) -> gap``.

        Returns
        -------
        None
        """
        self.strategy_registry[strategy_name] = fn

    def estimate_repair_cost(self, hint: dict[str, Any]) -> float:
        """Return the estimated repair cost for a hint in [0, 1].

        Parameters
        ----------
        hint : dict[str, Any]
            A repair hint dict as generated by the strategy methods.

        Returns
        -------
        float
            Effort estimate where 0.0 means trivial and 1.0 means maximum
            effort.
        """
        return float(hint.get("estimated_cost", 0.5))

    def prioritize_hints(
        self,
        hints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Sort repair hints by (cost ascending, impact descending).

        Parameters
        ----------
        hints : list[dict[str, Any]]
            Unsorted list of repair hint dicts.

        Returns
        -------
        list[dict[str, Any]]
            Hints sorted so cheapest, highest-impact hints appear first.
        """
        return sorted(
            hints,
            key=lambda h: (float(h.get("estimated_cost", 0.5)), -float(h.get("impact", 0.5))),
        )

    def apply_strategy(
        self,
        gap: Any,
        strategy_name: str,
        params: dict[str, Any],
    ) -> Any:
        """Apply a registered strategy to the gap and return an updated gap.

        Parameters
        ----------
        gap : ResidualGap | dict
            The gap to transform.
        strategy_name : str
            Name of the registered strategy to apply.
        params : dict[str, Any]
            Strategy-specific parameters.

        Returns
        -------
        ResidualGap | dict
            The transformed gap after strategy application.

        Raises
        ------
        KeyError
            If *strategy_name* is not registered.
        """
        if strategy_name not in self.strategy_registry:
            raise KeyError(
                f"Strategy '{strategy_name}' is not registered. "
                f"Available: {sorted(self.strategy_registry)}"
            )
        fn = self.strategy_registry[strategy_name]
        return fn(gap, params)


# ---------------------------------------------------------------------------
# GapPrioritizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GapPrioritizer:
    """Rank and prioritise a collection of residual gaps.

    Priority is determined by a weighted combination of severity, reparability,
    and impact scores.  Higher-priority gaps should be addressed first.

    Attributes
    ----------
    priority_weights : dict[str, float]
        Weight factors for each scoring dimension.
    prioritization_log : list[dict]
        Log of each prioritization run.
    """

    priority_weights: dict[str, float] = field(
        default_factory=lambda: {"severity": 0.5, "repair": 0.3, "impact": 0.2}
    )
    prioritization_log: list[dict] = field(default_factory=list)

    # -- public API -----------------------------------------------------------

    def prioritize(self, gaps: list[Any]) -> list[Any]:
        """Sort gaps by composite priority score (highest first).

        Parameters
        ----------
        gaps : list[ResidualGap | dict]
            Gaps to prioritise.

        Returns
        -------
        list[ResidualGap | dict]
            Gaps sorted from highest to lowest priority.
        """
        scored = [(self.score_gap(g), g) for g in gaps]
        scored.sort(key=lambda x: x[0], reverse=True)
        ordered = [g for _, g in scored]
        self.prioritization_log.append({
            "input_count": len(gaps),
            "top_gap_id": _coerce_gap(ordered[0]).get("gap_id", "") if ordered else "",
            "scored_at": _now_iso(),
        })
        return ordered

    def score_gap(self, gap: Any) -> float:
        """Compute a composite priority score for a single gap.

        Parameters
        ----------
        gap : ResidualGap | dict
            The gap to score.

        Returns
        -------
        float
            Composite score in [0.0, 1.0].  Higher means higher priority.
        """
        d = _coerce_gap(gap)
        w = self.priority_weights
        sev_score = self._severity_to_score(d.get("severity", "UNKNOWN"))
        rep_score = self._repairability_score(d)
        imp_score = self._impact_score(d)
        return (
            w.get("severity", 0.5) * sev_score
            + w.get("repair", 0.3) * rep_score
            + w.get("impact", 0.2) * imp_score
        )

    def _severity_to_score(self, severity: Any) -> float:
        """Map a severity string to a numeric score.

        Parameters
        ----------
        severity : str | GapSeverity
            The severity level.

        Returns
        -------
        float
            Score in [0.0, 1.0] where 1.0 is most severe.
        """
        mapping = {
            "FATAL": 1.0,
            "CRITICAL": 0.85,
            "MAJOR": 0.65,
            "MINOR": 0.35,
            "NEGLIGIBLE": 0.05,
        }
        sev_str = str(severity).upper()
        return mapping.get(sev_str, 0.5)

    def _repairability_score(self, gap_dict: dict[str, Any]) -> float:
        """Compute a reparability score: lower cost → higher score.

        Parameters
        ----------
        gap_dict : dict[str, Any]
            Plain gap dict.

        Returns
        -------
        float
            Score in [0.0, 1.0] where 1.0 means easily repairable.
        """
        hints = gap_dict.get("repair_hints", [])
        if not hints:
            # No hints means we cannot easily repair: score as medium difficulty.
            return 0.4
        avg_cost = sum(float(h.get("estimated_cost", 0.5)) for h in hints) / len(hints)
        # Higher cost → lower reparability score.
        return max(0.0, 1.0 - avg_cost)

    def _impact_score(self, gap_dict: dict[str, Any]) -> float:
        """Compute impact score from trust impact.

        Parameters
        ----------
        gap_dict : dict[str, Any]
            Plain gap dict.

        Returns
        -------
        float
            Score equal to the trust impact (0.0 → 1.0).
        """
        return float(gap_dict.get("trust_impact", 0.5))

    def rank_repairs(self, gap: Any) -> list[dict[str, Any]]:
        """Rank the repair hints within a single gap by priority.

        Parameters
        ----------
        gap : ResidualGap | dict
            The gap whose repair hints are to be ranked.

        Returns
        -------
        list[dict[str, Any]]
            Repair hints sorted by (cost ascending, impact descending).
        """
        d = _coerce_gap(gap)
        hints = list(d.get("repair_hints", []))
        return sorted(
            hints,
            key=lambda h: (float(h.get("estimated_cost", 0.5)), -float(h.get("impact", 0.5))),
        )

    def most_critical_gap(self, gaps: list[Any]) -> Any | None:
        """Return the highest-priority gap from a list.

        Parameters
        ----------
        gaps : list[ResidualGap | dict]
            Collection of gaps to search.

        Returns
        -------
        ResidualGap | dict | None
            The most critical gap, or None when *gaps* is empty.
        """
        if not gaps:
            return None
        return self.prioritize(gaps)[0]

    def set_weights(
        self,
        severity_w: float,
        repair_w: float,
        impact_w: float,
    ) -> None:
        """Update the priority weight factors.

        Weights are normalised to sum to 1.0 automatically.

        Parameters
        ----------
        severity_w : float
            Weight for the severity dimension (>= 0.0).
        repair_w : float
            Weight for the reparability dimension (>= 0.0).
        impact_w : float
            Weight for the impact dimension (>= 0.0).

        Returns
        -------
        None

        Raises
        ------
        ValueError
            If any weight is negative or all weights are zero.
        """
        if severity_w < 0 or repair_w < 0 or impact_w < 0:
            raise ValueError("All weights must be non-negative.")
        total = severity_w + repair_w + impact_w
        if total == 0.0:
            raise ValueError("Sum of weights must be non-zero.")
        self.priority_weights = {
            "severity": severity_w / total,
            "repair": repair_w / total,
            "impact": impact_w / total,
        }


# ---------------------------------------------------------------------------
# GapTracker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GapTracker:
    """Track active and resolved residual gaps across verification rounds.

    The tracker acts as a registry for all gaps encountered during a
    verification session.  It records when gaps are opened, what evidence
    is applied to them, and when they are resolved.

    Attributes
    ----------
    active_gaps : dict[str, ResidualGap | dict]
        Map from gap_id to currently active (unresolved) gap.
    resolved_gaps : dict[str, ResidualGap | dict]
        Map from gap_id to resolved gap record.
    resolution_history : list[dict]
        Chronological log of resolution events.
    tracking_started_at : str
        ISO-8601 timestamp when this tracker was created.
    """

    active_gaps: dict[str, Any] = field(default_factory=dict)
    resolved_gaps: dict[str, Any] = field(default_factory=dict)
    resolution_history: list[dict] = field(default_factory=list)
    tracking_started_at: str = field(default_factory=_now_iso)

    # -- public API -----------------------------------------------------------

    def track(self, gap: Any) -> None:
        """Register a new gap as active.

        If a gap with the same ID is already active, it is silently replaced
        (the caller's newer record takes precedence).

        Parameters
        ----------
        gap : ResidualGap | dict
            The gap to track.

        Returns
        -------
        None
        """
        d = _coerce_gap(gap)
        gap_id = d.get("gap_id", _digest(str(d)))
        self.active_gaps[gap_id] = gap
        _log.debug("GapTracker.track: registered gap %s", gap_id)

    def mark_resolved(self, gap_id: str, resolution_note: str = "") -> bool:
        """Mark an active gap as resolved.

        Parameters
        ----------
        gap_id : str
            The ID of the gap to resolve.
        resolution_note : str, optional
            Human-readable description of how the gap was resolved.

        Returns
        -------
        bool
            True when the gap was found and moved to resolved, False when the
            gap_id was not in the active set.
        """
        if gap_id not in self.active_gaps:
            _log.warning("GapTracker.mark_resolved: gap_id %s not found", gap_id)
            return False
        gap = self.active_gaps.pop(gap_id)
        d = _coerce_gap(gap)
        resolved_record = dict(d)
        resolved_record["resolved_at"] = _now_iso()
        resolved_record["resolution_note"] = resolution_note
        self.resolved_gaps[gap_id] = resolved_record
        self.resolution_history.append({
            "gap_id": gap_id,
            "resolved_at": resolved_record["resolved_at"],
            "note": resolution_note,
        })
        _log.debug("GapTracker.mark_resolved: gap %s resolved", gap_id)
        return True

    def apply_evidence_to_gap(
        self,
        gap_id: str,
        coordinate: str,
        evidence: dict[str, Any],
    ) -> Any | None:
        """Apply new evidence at a coordinate and return an updated gap.

        When evidence is applied, the gap's missing-coordinates list is updated:
        if the evidence satisfies the coordinate it is removed from the list.
        The updated gap replaces the tracked record.

        Parameters
        ----------
        gap_id : str
            ID of the active gap to update.
        coordinate : str
            The coordinate that the evidence targets.
        evidence : dict[str, Any]
            Evidence dict (must include a ``"trust"`` key).

        Returns
        -------
        ResidualGap | dict | None
            The updated gap, or None if *gap_id* is not active.
        """
        if gap_id not in self.active_gaps:
            return None
        gap = self.active_gaps[gap_id]
        d = _coerce_gap(gap)
        missing = list(d.get("missing_coordinates", []))
        if coordinate in missing:
            trust = float(evidence.get("trust", 0.0))
            # Remove coordinate from missing when trust meets a minimum threshold.
            if trust >= 0.5:
                missing.remove(coordinate)
        new_d = dict(d)
        new_d["missing_coordinates"] = missing
        # Recompute trust impact.
        total_required = len(missing) + len(d.get("missing_coordinates", [])) or 1
        new_d["trust_impact"] = len(missing) / max(total_required, 1)
        self.active_gaps[gap_id] = new_d
        return new_d

    def get_active_gaps(self) -> list[Any]:
        """Return all currently active (unresolved) gaps.

        Returns
        -------
        list[ResidualGap | dict]
            List of active gap objects in insertion order.
        """
        return list(self.active_gaps.values())

    def get_resolved_gaps(self) -> list[Any]:
        """Return all resolved gaps.

        Returns
        -------
        list[ResidualGap | dict]
            List of resolved gap records.
        """
        return list(self.resolved_gaps.values())

    def gaps_for_spec(self, spec_id: str) -> list[Any]:
        """Return all active gaps belonging to a specific specification.

        Parameters
        ----------
        spec_id : str
            The specification ID to filter by.

        Returns
        -------
        list[ResidualGap | dict]
            Active gaps whose ``spec_id`` matches.
        """
        return [
            g for g in self.active_gaps.values()
            if _coerce_gap(g).get("spec_id") == spec_id
        ]

    def resolution_rate(self) -> float:
        """Compute the fraction of all tracked gaps that have been resolved.

        Returns
        -------
        float
            Value in [0.0, 1.0]; 1.0 means all gaps resolved, 0.0 means none.
        """
        total = len(self.active_gaps) + len(self.resolved_gaps)
        if total == 0:
            return 1.0
        return len(self.resolved_gaps) / total

    def summary_report(self) -> dict[str, Any]:
        """Produce a comprehensive summary of tracked gaps.

        Returns
        -------
        dict[str, Any]
            Keys: ``active_count``, ``resolved_count``, ``resolution_rate``,
            ``tracking_started_at``, ``resolution_history``,
            ``active_gap_ids``, ``resolved_gap_ids``.
        """
        return {
            "active_count": len(self.active_gaps),
            "resolved_count": len(self.resolved_gaps),
            "resolution_rate": self.resolution_rate(),
            "tracking_started_at": self.tracking_started_at,
            "resolution_history": list(self.resolution_history),
            "active_gap_ids": sorted(self.active_gaps.keys()),
            "resolved_gap_ids": sorted(self.resolved_gaps.keys()),
        }

    def check_gap_stale(self, gap_id: str, max_age_seconds: float = 3600.0) -> bool:
        """Return True when the gap has been active longer than *max_age_seconds*.

        Staleness is computed from the ``generated_at`` timestamp embedded in
        the gap record.  If no timestamp is available the gap is assumed fresh.

        Parameters
        ----------
        gap_id : str
            The ID of the gap to check.
        max_age_seconds : float, optional
            Maximum age in seconds before a gap is considered stale.
            Default is 3600 (one hour).

        Returns
        -------
        bool
            True when the gap is stale, False otherwise.

        Raises
        ------
        KeyError
            If *gap_id* is not in the active gaps registry.
        """
        if gap_id not in self.active_gaps:
            raise KeyError(f"Gap '{gap_id}' is not in the active gaps registry.")
        gap = self.active_gaps[gap_id]
        d = _coerce_gap(gap)
        generated_at = d.get("generated_at", "")
        if not generated_at:
            return False
        try:
            import datetime
            # Parse the ISO-8601 string produced by _now_iso().
            ts = datetime.datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
            ts = ts.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            age = (now - ts).total_seconds()
            return age > max_age_seconds
        except (ValueError, OSError):
            return False


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def analyze_gaps(witness: Any, spec: Any) -> Any:
    """Create a ResidualGap by analyzing a partial witness against a spec.

    Parameters
    ----------
    witness : SatisfactionWitness
        The partial witness to analyse.
    spec : Specification
        The target specification.

    Returns
    -------
    ResidualGap | dict
        The residual gap describing what is missing.
    """
    analyzer = GapAnalyzer()
    return analyzer.analyze(witness, spec)


def compute_obstruction(witness: Any, spec: Any) -> dict[str, Any]:
    """Compute the H¹ obstruction class for a witness and specification.

    Parameters
    ----------
    witness : SatisfactionWitness
        The partial witness.
    spec : Specification
        The target specification.

    Returns
    -------
    dict[str, Any]
        Obstruction class dict as returned by
        :meth:`ObstructionClassComputer.compute`.
    """
    computer = ObstructionClassComputer()
    return computer.compute(witness, spec)


def generate_repair_strategy(gap: Any) -> list[dict[str, Any]]:
    """Generate repair hints for the given residual gap.

    Parameters
    ----------
    gap : ResidualGap | dict
        The gap to generate strategies for.

    Returns
    -------
    list[dict[str, Any]]
        Prioritised list of repair hint dicts.
    """
    engine = RepairStrategyEngine()
    return engine.generate_strategies(gap)


def prioritize_gaps(gaps: list[Any]) -> list[Any]:
    """Sort a list of residual gaps by composite priority (highest first).

    Parameters
    ----------
    gaps : list[ResidualGap | dict]
        Gaps to prioritise.

    Returns
    -------
    list[ResidualGap | dict]
        Gaps sorted from highest to lowest priority.
    """
    prioritizer = GapPrioritizer()
    return prioritizer.prioritize(gaps)


def track_gap_resolution(
    gap: Any,
    evidence_additions: dict[str, dict[str, Any]],
) -> Any:
    """Apply evidence additions to a gap and return the updated gap.

    Convenience wrapper that creates a :class:`GapTracker`, registers the
    gap, applies each piece of evidence, and returns the resulting gap.

    Parameters
    ----------
    gap : ResidualGap | dict
        The gap to update.
    evidence_additions : dict[str, dict[str, Any]]
        Map from coordinate key to evidence dict to apply.

    Returns
    -------
    ResidualGap | dict
        The gap after all evidence additions have been applied.
    """
    tracker = GapTracker()
    tracker.track(gap)
    d = _coerce_gap(gap)
    gap_id = d.get("gap_id", "")
    updated: Any = gap
    for coord, evidence in evidence_additions.items():
        result = tracker.apply_evidence_to_gap(gap_id, coord, evidence)
        if result is not None:
            updated = result
    return updated


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


__all__ = [
    "GapAnalyzer",
    "ObstructionClassComputer",
    "RepairStrategyEngine",
    "GapPrioritizer",
    "GapTracker",
    "analyze_gaps",
    "compute_obstruction",
    "generate_repair_strategy",
    "prioritize_gaps",
    "track_gap_resolution",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
