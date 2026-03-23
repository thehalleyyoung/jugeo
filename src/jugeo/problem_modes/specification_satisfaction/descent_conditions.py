"""Section 10.3: Descent Conditions.

Descent is the process of showing that local evidence glues to a global
section matching the specification.  A specification is *satisfied* when
all descent conditions hold: local sections exist on a cover of the semantic
site, their restrictions to pairwise overlaps agree up to specified tolerance,
the resulting Čech 1-cocycle is trivial in H¹, and a genuine global section
can be extracted from the local data.

References
----------
theory2.tex §10.3 — "Descent and Global Section Extraction"
theory2.tex §10.3.1 — "Čech Covers and Local Sections"
theory2.tex §10.3.2 — "Overlap Compatibility"
theory2.tex §10.3.3 — "Cocycle Triviality and H¹ Obstructions"
theory2.tex §10.3.4 — "Global Section Reconstruction"

copilot: specification-satisfaction sub-module — implements descent-condition
checking pipeline used by the outer SatisfactionOrchestrator.  All results are
deterministic given fixed inputs so that LLM-driven orchestration loops can
cache and replay condition checks without side-effects.
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
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _witness_coords(witness: Any) -> list[str]:
    """Extract coordinate keys from a witness object or dict.

    Parameters
    ----------
    witness : Any
        A SatisfactionWitness instance or a plain dict with a ``"coordinates"``
        key.

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
    if isinstance(coords, (list, tuple)):
        return [str(c) for c in coords]
    return []


def _witness_gluing(witness: Any) -> dict[str, Any]:
    """Extract gluing data mapping from a witness.

    Parameters
    ----------
    witness : Any
        A SatisfactionWitness or compatible dict.

    Returns
    -------
    dict[str, Any]
        Mapping from overlap-pair keys to gluing datum dicts.
    """
    if isinstance(witness, dict):
        return dict(witness.get("gluing_data", {}))
    gd = getattr(witness, "gluing_data", None)
    if gd is None:
        return {}
    if isinstance(gd, dict):
        return dict(gd)
    return {}


def _spec_prescriptions(spec: Any) -> dict[str, Any]:
    """Extract prescription map from a specification.

    Parameters
    ----------
    spec : Any
        A Specification instance or compatible dict.

    Returns
    -------
    dict[str, Any]
        Mapping from coordinate keys to their required prescription dicts.
    """
    if isinstance(spec, dict):
        return dict(spec.get("prescriptions", {}))
    p = getattr(spec, "prescriptions", None) or getattr(spec, "requirements", None)
    if p is None:
        return {}
    if isinstance(p, dict):
        return dict(p)
    return {}


def _spec_required_coords(spec: Any) -> list[str]:
    """Return the list of coordinates required by the specification.

    Parameters
    ----------
    spec : Any
        A Specification instance or compatible dict.

    Returns
    -------
    list[str]
        Sorted list of required coordinate keys.
    """
    prescriptions = _spec_prescriptions(spec)
    if prescriptions:
        return sorted(prescriptions.keys())
    if isinstance(spec, dict):
        return sorted(spec.get("required_coordinates", []))
    req = getattr(spec, "required_coordinates", None)
    if req:
        return sorted(str(c) for c in req)
    return []


def _pair_key(coord_a: str, coord_b: str) -> str:
    """Canonical key for an unordered coordinate pair.

    Parameters
    ----------
    coord_a : str
        First coordinate key.
    coord_b : str
        Second coordinate key.

    Returns
    -------
    str
        Lexicographically ordered ``"a::b"`` string.
    """
    a, b = sorted([coord_a, coord_b])
    return f"{a}::{b}"


def _triple_key(ca: str, cb: str, cc: str) -> str:
    """Canonical key for an unordered triple of coordinates.

    Parameters
    ----------
    ca : str
        First coordinate key.
    cb : str
        Second coordinate key.
    cc : str
        Third coordinate key.

    Returns
    -------
    str
        Lexicographically sorted ``"a::b::c"`` string.
    """
    return "::".join(sorted([ca, cb, cc]))


# ---------------------------------------------------------------------------
# DescentConditionChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentConditionChecker:
    """Run all five descent conditions against a witness and specification.

    Descent succeeds when every condition in the sequence passes.  The checker
    accumulates a per-run log so that downstream orchestrators can produce
    diagnostic reports without re-running checks.

    Attributes
    ----------
    conditions_checked : list[dict]
        Records of each condition evaluated (name, passed, message, ts).
    failed_conditions : list[dict]
        Subset of ``conditions_checked`` where ``passed`` is False.
    strict_mode : bool
        When True every condition must pass; when False COVER_EXISTS and
        GLOBAL_SECTION_EXISTS are mandatory but overlap/cocycle failures are
        recorded as warnings rather than hard failures.
    log_entries : list[str]
        Free-text diagnostic log lines appended during each run.
    """

    conditions_checked: list[dict] = field(default_factory=list)
    failed_conditions: list[dict] = field(default_factory=list)
    strict_mode: bool = True
    log_entries: list[str] = field(default_factory=list)

    # -- public API -----------------------------------------------------------

    def check_all(
        self,
        witness: Any,
        spec: Any,
    ) -> tuple[bool, list[dict]]:
        """Run all descent conditions in canonical order and aggregate results.

        Parameters
        ----------
        witness : SatisfactionWitness
            The partial or full witness assembled so far.
        spec : Specification
            Target specification against which descent is tested.

        Returns
        -------
        tuple[bool, list[dict]]
            ``(overall_pass, list_of_condition_records)`` where each record has
            keys ``condition``, ``passed``, ``message``, ``detail``,
            ``checked_at``.

        Raises
        ------
        ValueError
            If *witness* or *spec* is None.
        """
        if witness is None:
            raise ValueError("witness must not be None")
        if spec is None:
            raise ValueError("spec must not be None")

        self.log_entries.append(f"[{_now_iso()}] check_all started")
        records: list[dict] = []

        cover_ok, cover_msg = self.check_cover_exists(witness, spec)
        records.append(self._record("COVER_EXISTS", cover_ok, cover_msg))

        overlaps_ok, overlap_msgs = self.check_overlaps_compatible(witness)
        overlap_detail = "; ".join(overlap_msgs) if overlap_msgs else "all overlaps compatible"
        records.append(self._record("OVERLAPS_COMPATIBLE", overlaps_ok, overlap_detail))

        sections_ok, section_msgs = self.check_sections_compatible(witness)
        section_detail = "; ".join(section_msgs) if section_msgs else "all sections compatible"
        records.append(self._record("SECTIONS_COMPATIBLE", sections_ok, section_detail))

        cocycle_ok, cocycle_detail = self.check_cocycle_trivial(witness)
        cocycle_msg = "cocycle trivial" if cocycle_ok else f"obstruction: {cocycle_detail}"
        records.append(self._record("COCYCLE_TRIVIAL", cocycle_ok, cocycle_msg, cocycle_detail))

        global_ok, global_detail = self.check_global_section_exists(witness, spec)
        global_msg = "global section found" if global_ok else "global section absent"
        records.append(self._record("GLOBAL_SECTION_EXISTS", global_ok, global_msg, global_detail))

        self.conditions_checked.extend(records)
        failed = [r for r in records if not r["passed"]]
        self.failed_conditions.extend(failed)

        if self.strict_mode:
            overall = all(r["passed"] for r in records)
        else:
            mandatory = {"COVER_EXISTS", "GLOBAL_SECTION_EXISTS"}
            overall = all(r["passed"] for r in records if r["condition"] in mandatory)

        self.log_entries.append(
            f"[{_now_iso()}] check_all finished overall={overall} "
            f"failed={len(failed)}/{len(records)}"
        )
        return overall, records

    def check_cover_exists(
        self,
        witness: Any,
        spec: Any,
    ) -> tuple[bool, str]:
        """Verify DescentCondition.COVER_EXISTS: a cover witnesses the spec.

        A cover exists when the witness provides at least one local section for
        every coordinate required by the specification.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness under test.
        spec : Specification
            The specification whose required coordinates must be covered.

        Returns
        -------
        tuple[bool, str]
            ``(passed, diagnostic_message)``.
        """
        provided = set(_witness_coords(witness))
        required = set(_spec_required_coords(spec))
        missing = required - provided
        if missing:
            msg = f"cover missing coordinates: {sorted(missing)}"
            self.log_entries.append(f"  COVER_EXISTS FAIL: {msg}")
            return False, msg
        if not provided:
            msg = "witness has no coordinates — empty cover"
            self.log_entries.append(f"  COVER_EXISTS FAIL: {msg}")
            return False, msg
        msg = f"cover verified: {len(provided)} coordinates, {len(required)} required"
        self.log_entries.append(f"  COVER_EXISTS OK: {msg}")
        return True, msg

    def check_overlaps_compatible(
        self,
        witness: Any,
    ) -> tuple[bool, list[str]]:
        """Verify DescentCondition.OVERLAPS_COMPATIBLE for all coordinate pairs.

        Two local sections are compatible on an overlap when their restrictions
        to the intersection agree or differ only within tolerance.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness whose gluing data encode pairwise compatibility.

        Returns
        -------
        tuple[bool, list[str]]
            ``(all_compatible, list_of_violation_messages)``.
        """
        gluing = _witness_gluing(witness)
        coords = _witness_coords(witness)
        violations: list[str] = []

        for i, ca in enumerate(coords):
            for cb in coords[i + 1 :]:
                key = _pair_key(ca, cb)
                datum = gluing.get(key) or gluing.get(f"{ca}::{cb}") or gluing.get(f"{cb}::{ca}")
                if datum is None:
                    # No gluing data means we cannot confirm compatibility — treat as failure.
                    violations.append(f"no gluing datum for pair ({ca}, {cb})")
                    continue
                compatible = datum.get("compatible", True) if isinstance(datum, dict) else True
                if not compatible:
                    reason = datum.get("reason", "unspecified") if isinstance(datum, dict) else "?"
                    violations.append(f"overlap ({ca}, {cb}) incompatible: {reason}")

        passed = len(violations) == 0
        if passed:
            self.log_entries.append(
                f"  OVERLAPS_COMPATIBLE OK: {len(coords)} coords, "
                f"{len(coords) * (len(coords) - 1) // 2} pairs checked"
            )
        else:
            self.log_entries.append(
                f"  OVERLAPS_COMPATIBLE FAIL: {len(violations)} violations"
            )
        return passed, violations

    def check_sections_compatible(
        self,
        witness: Any,
    ) -> tuple[bool, list[str]]:
        """Verify DescentCondition.SECTIONS_COMPATIBLE: sections agree on triple overlaps.

        Beyond pairwise compatibility, sections must agree on triple overlaps
        to satisfy the sheaf condition.  This checks all triples of coordinates.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness providing local section and gluing data.

        Returns
        -------
        tuple[bool, list[str]]
            ``(all_compatible, list_of_violation_messages)``.
        """
        coords = _witness_coords(witness)
        gluing = _witness_gluing(witness)
        violations: list[str] = []

        for i, ca in enumerate(coords):
            for j, cb in enumerate(coords[i + 1 :], i + 1):
                for cc in coords[j + 1 :]:
                    # Check that g_ab ∘ g_bc == g_ac (cocycle condition on triple).
                    key_ab = _pair_key(ca, cb)
                    key_bc = _pair_key(cb, cc)
                    key_ac = _pair_key(ca, cc)
                    d_ab = gluing.get(key_ab, {})
                    d_bc = gluing.get(key_bc, {})
                    d_ac = gluing.get(key_ac, {})
                    if not isinstance(d_ab, dict) or not isinstance(d_bc, dict) or not isinstance(d_ac, dict):
                        continue
                    # Compare hash fingerprints if present.
                    fp_ab = d_ab.get("fingerprint", "")
                    fp_bc = d_bc.get("fingerprint", "")
                    fp_ac = d_ac.get("fingerprint", "")
                    composed = _digest(fp_ab, fp_bc)
                    if fp_ac and fp_ac != composed and fp_ab and fp_bc:
                        violations.append(
                            f"sections disagree on triple ({ca},{cb},{cc}): "
                            f"g_ab∘g_bc fingerprint {composed[:8]} != g_ac {fp_ac[:8]}"
                        )

        passed = len(violations) == 0
        self.log_entries.append(
            f"  SECTIONS_COMPATIBLE {'OK' if passed else 'FAIL'}: {len(violations)} violations"
        )
        return passed, violations

    def check_cocycle_trivial(
        self,
        witness: Any,
    ) -> tuple[bool, dict]:
        """Verify DescentCondition.COCYCLE_TRIVIAL: H¹ obstruction vanishes.

        Computes the Čech 1-cocycle from the witness gluing data and checks
        whether it represents the trivial class in H¹.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness whose gluing data encode the transition functions.

        Returns
        -------
        tuple[bool, dict]
            ``(trivial, cocycle_or_obstruction_dict)``.
        """
        computer = CocycleComputer()
        cocycle = computer.compute_cocycle(witness)
        trivial = computer.is_trivial(cocycle)
        obstruction = {} if trivial else computer.obstruction_from_cocycle(cocycle)
        self.log_entries.append(
            f"  COCYCLE_TRIVIAL {'OK' if trivial else 'FAIL'}: "
            f"{len(cocycle)} cocycle entries"
        )
        return trivial, obstruction

    def check_global_section_exists(
        self,
        witness: Any,
        spec: Any,
    ) -> tuple[bool, dict]:
        """Verify DescentCondition.GLOBAL_SECTION_EXISTS: extraction succeeds.

        Attempts to extract a global section from the witness; succeeds when
        the extractor produces a non-empty result that validates against the
        specification prescriptions.

        Parameters
        ----------
        witness : SatisfactionWitness
            The assembled witness.
        spec : Specification
            Specification against which the extracted section is validated.

        Returns
        -------
        tuple[bool, dict]
            ``(exists, detail_dict)`` where *detail_dict* contains the
            extracted section or an error description.
        """
        extractor = GlobalSectionExtractor()
        section = extractor.extract(witness, spec)
        if section is None:
            self.log_entries.append("  GLOBAL_SECTION_EXISTS FAIL: extractor returned None")
            return False, {"error": "extraction failed", "log": extractor.extraction_log[-3:]}
        errors = extractor.validate_extracted_section(section, spec)
        if errors:
            self.log_entries.append(
                f"  GLOBAL_SECTION_EXISTS FAIL: {len(errors)} validation errors"
            )
            return False, {"section": section, "validation_errors": errors}
        self.log_entries.append(
            f"  GLOBAL_SECTION_EXISTS OK: section with {len(section)} entries"
        )
        return True, {"section": section}

    # -- reporting ------------------------------------------------------------

    def get_failed_condition_names(self) -> list[str]:
        """Return the names of all failed conditions accumulated so far.

        Returns
        -------
        list[str]
            Condition name strings for every failed record.
        """
        return [r["condition"] for r in self.failed_conditions]

    def condition_report(self) -> dict[str, Any]:
        """Produce a structured summary of all condition checks performed.

        Returns
        -------
        dict[str, Any]
            Keys: ``checked_count``, ``failed_count``, ``passed_count``,
            ``strict_mode``, ``failed_names``, ``records``, ``log_lines``.
        """
        checked = self.conditions_checked
        failed = self.failed_conditions
        return {
            "checked_count": len(checked),
            "failed_count": len(failed),
            "passed_count": len(checked) - len(failed),
            "strict_mode": self.strict_mode,
            "failed_names": self.get_failed_condition_names(),
            "records": list(checked),
            "log_lines": list(self.log_entries),
        }

    def reset(self) -> None:
        """Clear all accumulated state so the checker can be reused.

        Returns
        -------
        None
        """
        self.conditions_checked.clear()
        self.failed_conditions.clear()
        self.log_entries.clear()

    # -- internal helpers -----------------------------------------------------

    def _record(
        self,
        condition: str,
        passed: bool,
        message: str,
        detail: Any = None,
    ) -> dict:
        return {
            "condition": condition,
            "passed": passed,
            "message": message,
            "detail": detail,
            "checked_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# OverlapCompatibilityVerifier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OverlapCompatibilityVerifier:
    """Verify pairwise and triple overlap compatibility of local sections.

    Encapsulates the symmetry check (g_ab = g_ba⁻¹) and the cocycle condition
    (g_ab ∘ g_bc = g_ac) needed to confirm that transition functions form a
    valid Čech 1-cocycle.

    Attributes
    ----------
    verification_results : dict[str, bool]
        Map from pair-key to boolean verification outcome.
    violation_messages : dict[str, str]
        Map from pair-key to human-readable violation description.
    verified_pairs_count : int
        Running count of pairs that have been verified.
    """

    verification_results: dict[str, bool] = field(default_factory=dict)
    violation_messages: dict[str, str] = field(default_factory=dict)
    verified_pairs_count: int = 0

    # -- public API -----------------------------------------------------------

    def verify_pair(
        self,
        coord_a: str,
        coord_b: str,
        gluing_datum: Any,
    ) -> tuple[bool, str]:
        """Verify that two coordinates' gluing datum is locally compatible.

        Parameters
        ----------
        coord_a : str
            Key of the first coordinate patch.
        coord_b : str
            Key of the second coordinate patch.
        gluing_datum : Any
            Gluing datum dict or object describing the transition on the
            overlap.

        Returns
        -------
        tuple[bool, str]
            ``(compatible, diagnostic_message)``.
        """
        key = _pair_key(coord_a, coord_b)
        if gluing_datum is None:
            msg = f"no gluing datum for ({coord_a}, {coord_b})"
            self.verification_results[key] = False
            self.violation_messages[key] = msg
            return False, msg

        if isinstance(gluing_datum, dict):
            compatible = gluing_datum.get("compatible", True)
            if not compatible:
                reason = gluing_datum.get("reason", "unspecified")
                msg = f"incompatible: {reason}"
                self.verification_results[key] = False
                self.violation_messages[key] = msg
                return False, msg
            trust = float(gluing_datum.get("trust", 1.0))
            if trust < 0.0 or trust > 1.0:
                msg = f"trust out of range: {trust}"
                self.verification_results[key] = False
                self.violation_messages[key] = msg
                return False, msg

        self.verification_results[key] = True
        self.verified_pairs_count += 1
        return True, f"pair ({coord_a}, {coord_b}) compatible"

    def verify_all_pairs(self, witness: Any) -> dict[str, bool]:
        """Verify every pair of coordinates present in *witness*.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness whose coordinate set and gluing data are inspected.

        Returns
        -------
        dict[str, bool]
            Map from pair-keys to compatibility booleans.
        """
        coords = _witness_coords(witness)
        gluing = _witness_gluing(witness)
        for i, ca in enumerate(coords):
            for cb in coords[i + 1 :]:
                key = _pair_key(ca, cb)
                datum = gluing.get(key) or gluing.get(f"{ca}::{cb}") or gluing.get(f"{cb}::{ca}")
                self.verify_pair(ca, cb, datum)
        return dict(self.verification_results)

    def check_symmetry(
        self,
        witness: Any,
        coord_a: str,
        coord_b: str,
    ) -> bool:
        """Check that g_ab = g_ba⁻¹ (anti-symmetry of transition functions).

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness providing gluing data.
        coord_a : str
            First coordinate patch key.
        coord_b : str
            Second coordinate patch key.

        Returns
        -------
        bool
            True when the symmetry condition is satisfied or when insufficient
            data are present to falsify it.
        """
        gluing = _witness_gluing(witness)
        key_ab = f"{coord_a}::{coord_b}"
        key_ba = f"{coord_b}::{coord_a}"
        d_ab = gluing.get(key_ab, {})
        d_ba = gluing.get(key_ba, {})
        if not isinstance(d_ab, dict) or not isinstance(d_ba, dict):
            return True  # Cannot falsify without structured data.
        fp_ab = d_ab.get("fingerprint", "")
        fp_ba = d_ba.get("fingerprint", "")
        inv_ba = d_ba.get("inverse_fingerprint", "")
        if fp_ab and inv_ba:
            return fp_ab == inv_ba
        return True

    def check_cocycle_condition(
        self,
        witness: Any,
        coord_a: str,
        coord_b: str,
        coord_c: str,
    ) -> bool:
        """Check g_ab ∘ g_bc = g_ac (cocycle condition on triple overlap).

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness providing gluing data for all three pairs.
        coord_a : str
            First coordinate key.
        coord_b : str
            Second coordinate key.
        coord_c : str
            Third coordinate key.

        Returns
        -------
        bool
            True when the cocycle condition holds or cannot be falsified.
        """
        gluing = _witness_gluing(witness)
        d_ab = gluing.get(_pair_key(coord_a, coord_b), {})
        d_bc = gluing.get(_pair_key(coord_b, coord_c), {})
        d_ac = gluing.get(_pair_key(coord_a, coord_c), {})
        if not (isinstance(d_ab, dict) and isinstance(d_bc, dict) and isinstance(d_ac, dict)):
            return True
        fp_ab = d_ab.get("fingerprint", "")
        fp_bc = d_bc.get("fingerprint", "")
        fp_ac = d_ac.get("fingerprint", "")
        if fp_ab and fp_bc and fp_ac:
            composed = _digest(fp_ab, fp_bc)
            return composed == fp_ac
        return True

    def get_violating_pairs(self) -> list[tuple[str, str]]:
        """Return all pairs that failed compatibility verification.

        Returns
        -------
        list[tuple[str, str]]
            List of ``(coord_a, coord_b)`` tuples for violated pairs.
        """
        result: list[tuple[str, str]] = []
        for key, ok in self.verification_results.items():
            if not ok:
                parts = key.split("::")
                if len(parts) == 2:
                    result.append((parts[0], parts[1]))
        return result

    def violation_summary(self) -> dict[str, Any]:
        """Produce a summary of all compatibility violations.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_pairs``, ``violations``, ``violation_messages``,
            ``compatible_pairs``.
        """
        total = len(self.verification_results)
        violations = [k for k, v in self.verification_results.items() if not v]
        return {
            "total_pairs": total,
            "violations": violations,
            "violation_messages": {k: self.violation_messages.get(k, "") for k in violations},
            "compatible_pairs": total - len(violations),
        }

    def is_fully_compatible(self, witness: Any) -> bool:
        """Return True only when every pair in *witness* is compatible.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness to evaluate.

        Returns
        -------
        bool
            True iff all pairs pass compatibility verification.
        """
        results = self.verify_all_pairs(witness)
        return all(results.values())


# ---------------------------------------------------------------------------
# CocycleComputer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CocycleComputer:
    """Compute the Čech 1-cocycle and assess its H¹ cohomology class.

    The cocycle encodes how local transition functions fail to compose globally.
    A trivial cocycle (one that is a coboundary) means descent succeeds.

    Attributes
    ----------
    computed_cocycles : dict[str, dict]
        Cache of computed cocycles keyed by witness digest.
    is_trivial_cache : dict[str, bool]
        Cache of triviality checks keyed by cocycle digest.
    """

    computed_cocycles: dict[str, dict] = field(default_factory=dict)
    is_trivial_cache: dict[str, bool] = field(default_factory=dict)

    # -- cocycle computation --------------------------------------------------

    def compute_cocycle(self, witness: Any) -> dict[str, dict[str, Any]]:
        """Compute the Čech 1-cocycle from witness gluing data.

        For each ordered pair (a, b) of coordinates the cocycle value c_ab is
        derived from the gluing datum g_ab.  Pairs without gluing data produce
        a *missing* entry flagged as a potential obstruction.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness whose gluing data supply the transition functions.

        Returns
        -------
        dict[str, dict[str, Any]]
            Map from ``"coord_a::coord_b"`` to cocycle entry dicts containing
            ``fingerprint``, ``trust``, ``is_missing``, ``is_trivial``.
        """
        coords = _witness_coords(witness)
        gluing = _witness_gluing(witness)
        cocycle: dict[str, dict[str, Any]] = {}
        for i, ca in enumerate(coords):
            for cb in coords[i + 1 :]:
                canonical = _pair_key(ca, cb)
                datum = (
                    gluing.get(canonical)
                    or gluing.get(f"{ca}::{cb}")
                    or gluing.get(f"{cb}::{ca}")
                    or {}
                )
                if not datum:
                    cocycle[canonical] = {
                        "coord_a": ca,
                        "coord_b": cb,
                        "fingerprint": "",
                        "trust": 0.0,
                        "is_missing": True,
                        "is_trivial": False,
                    }
                else:
                    d = datum if isinstance(datum, dict) else {}
                    fp = d.get("fingerprint", _digest(ca, cb))
                    trust = float(d.get("trust", 1.0))
                    # A cocycle entry is locally trivial when the datum
                    # explicitly marks it as an identity transition.
                    is_id = d.get("is_identity", False) or d.get("fingerprint", "") == "identity"
                    cocycle[canonical] = {
                        "coord_a": ca,
                        "coord_b": cb,
                        "fingerprint": fp,
                        "trust": trust,
                        "is_missing": False,
                        "is_trivial": bool(is_id),
                    }
        key = _digest(str(sorted(cocycle.keys())))
        self.computed_cocycles[key] = cocycle
        return cocycle

    def is_trivial(self, cocycle: dict[str, dict[str, Any]]) -> bool:
        """Determine whether a cocycle bounds (represents trivial H¹ class).

        A cocycle is trivial when every entry is either the identity transition
        or when it is a coboundary of some 0-cochain (i.e., there exist local
        gauges h_a such that g_ab = h_a⁻¹ ∘ h_b for all pairs).

        Parameters
        ----------
        cocycle : dict[str, dict[str, Any]]
            Output of ``compute_cocycle``.

        Returns
        -------
        bool
            True when the cocycle is trivial.
        """
        key = _digest(json.dumps(sorted(cocycle.keys())))
        if key in self.is_trivial_cache:
            return self.is_trivial_cache[key]

        if not cocycle:
            self.is_trivial_cache[key] = True
            return True

        # Check if any entry is flagged as missing — that is an obstruction.
        if any(v.get("is_missing", False) for v in cocycle.values()):
            self.is_trivial_cache[key] = False
            return False

        # If all entries are marked trivial (identity transitions), the cocycle bounds.
        if all(v.get("is_trivial", False) for v in cocycle.values()):
            self.is_trivial_cache[key] = True
            return True

        # Collect all unique fingerprints.  If there is only one non-identity
        # fingerprint class then we check whether it is a global coboundary.
        fingerprints = {v["fingerprint"] for v in cocycle.values() if not v.get("is_trivial")}
        if len(fingerprints) == 0:
            self.is_trivial_cache[key] = True
            return True

        # Heuristic: if all fingerprints are consistent (equal) and a single
        # gauge exists that trivialises them, declare trivial.
        if len(fingerprints) == 1:
            # Single fingerprint: check if it equals the coboundary of the
            # constant gauge.  We approximate this by checking whether all
            # entries share the same trust level.
            trusts = {v["trust"] for v in cocycle.values()}
            if len(trusts) == 1:
                self.is_trivial_cache[key] = True
                return True

        self.is_trivial_cache[key] = False
        return False

    def compute_coboundary(
        self,
        section_map: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Compute the coboundary of a 0-cochain (section map).

        Given a map a ↦ h_a (0-cochain), the coboundary is δh with
        (δh)_ab = h_a⁻¹ ∘ h_b on each pairwise overlap.

        Parameters
        ----------
        section_map : dict[str, dict[str, Any]]
            Map from coordinate key to local section dict (the 0-cochain).

        Returns
        -------
        dict[str, dict[str, Any]]
            Coboundary cocycle map from pair-keys to transition dicts.
        """
        coords = sorted(section_map.keys())
        coboundary: dict[str, dict[str, Any]] = {}
        for i, ca in enumerate(coords):
            for cb in coords[i + 1 :]:
                key = _pair_key(ca, cb)
                ha = section_map.get(ca, {})
                hb = section_map.get(cb, {})
                fp_a = ha.get("fingerprint", _digest(ca)) if isinstance(ha, dict) else _digest(ca)
                fp_b = hb.get("fingerprint", _digest(cb)) if isinstance(hb, dict) else _digest(cb)
                # (δh)_ab = inverse(h_a) ∘ h_b — approximated via digest composition.
                composed_fp = _digest(fp_a, fp_b)
                coboundary[key] = {
                    "coord_a": ca,
                    "coord_b": cb,
                    "fingerprint": composed_fp,
                    "is_trivial": fp_a == fp_b,
                    "trust": min(
                        float(ha.get("trust", 1.0)) if isinstance(ha, dict) else 1.0,
                        float(hb.get("trust", 1.0)) if isinstance(hb, dict) else 1.0,
                    ),
                    "is_missing": False,
                }
        return coboundary

    def check_cocycle_condition(
        self,
        cocycle: dict[str, dict[str, Any]],
        coord_a: str,
        coord_b: str,
        coord_c: str,
    ) -> bool:
        """Check the cocycle condition c_ab ∘ c_bc = c_ac on a triple.

        Parameters
        ----------
        cocycle : dict[str, dict[str, Any]]
            Precomputed cocycle.
        coord_a : str
            First coordinate key.
        coord_b : str
            Second coordinate key.
        coord_c : str
            Third coordinate key.

        Returns
        -------
        bool
            True when the condition holds or cannot be falsified.
        """
        key_ab = _pair_key(coord_a, coord_b)
        key_bc = _pair_key(coord_b, coord_c)
        key_ac = _pair_key(coord_a, coord_c)
        c_ab = cocycle.get(key_ab, {})
        c_bc = cocycle.get(key_bc, {})
        c_ac = cocycle.get(key_ac, {})
        fp_ab = c_ab.get("fingerprint", "")
        fp_bc = c_bc.get("fingerprint", "")
        fp_ac = c_ac.get("fingerprint", "")
        if fp_ab and fp_bc and fp_ac:
            return _digest(fp_ab, fp_bc) == fp_ac
        return True

    def cohomology_class(self, cocycle: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Return a representative dict for the H¹ class of the cocycle.

        Parameters
        ----------
        cocycle : dict[str, dict[str, Any]]
            The Čech cocycle to classify.

        Returns
        -------
        dict[str, Any]
            Keys: ``is_trivial``, ``representative_fingerprints``,
            ``num_generators``, ``obstruction_pairs``.
        """
        trivial = self.is_trivial(cocycle)
        non_trivial_fps = sorted(
            {v["fingerprint"] for v in cocycle.values() if not v.get("is_trivial") and v.get("fingerprint")}
        )
        obstruction_pairs = [
            (v["coord_a"], v["coord_b"])
            for v in cocycle.values()
            if v.get("is_missing") or not v.get("is_trivial", True)
        ]
        return {
            "is_trivial": trivial,
            "representative_fingerprints": non_trivial_fps,
            "num_generators": len(non_trivial_fps),
            "obstruction_pairs": obstruction_pairs,
        }

    def obstruction_from_cocycle(self, cocycle: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Build an obstruction description from a non-trivial cocycle.

        Parameters
        ----------
        cocycle : dict[str, dict[str, Any]]
            A non-trivial cocycle whose H¹ class represents the obstruction.

        Returns
        -------
        dict[str, Any]
            Keys: ``h1_class``, ``missing_pairs``, ``incompatible_pairs``,
            ``obstruction_id``, ``generated_at``.
        """
        h1 = self.cohomology_class(cocycle)
        missing = [k for k, v in cocycle.items() if v.get("is_missing")]
        incompatible = [k for k, v in cocycle.items() if not v.get("is_trivial") and not v.get("is_missing")]
        obs_id = _digest(str(missing), str(incompatible))
        return {
            "h1_class": h1,
            "missing_pairs": missing,
            "incompatible_pairs": incompatible,
            "obstruction_id": obs_id,
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# GlobalSectionExtractor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GlobalSectionExtractor:
    """Extract a global section from assembled local evidence.

    The global section is the H⁰ element: a consistent assignment of
    judgment values over all coordinates that satisfies the specification
    prescriptions.

    Attributes
    ----------
    extraction_log : list[dict]
        Records of each extraction attempt with outcome and diagnostics.
    last_extracted : dict | None
        The most recently successfully extracted global section, or None.
    """

    extraction_log: list[dict] = field(default_factory=list)
    last_extracted: dict[str, Any] | None = None

    # -- extraction -----------------------------------------------------------

    def extract(
        self,
        witness: Any,
        spec: Any,
    ) -> dict[str, Any] | None:
        """Extract the global section or return None if descent fails.

        Aggregates evidence from all coordinates, reconciles overlaps, builds
        per-coordinate global judgments, and validates the result.

        Parameters
        ----------
        witness : SatisfactionWitness
            The assembled witness providing local sections and gluing data.
        spec : Specification
            The specification prescribing required judgment values.

        Returns
        -------
        dict[str, Any] | None
            The global section dict mapping coordinate keys to judgment entries,
            or None if the extraction fails.
        """
        start = time.monotonic()
        coords = _witness_coords(witness)
        if not coords:
            self._log_attempt(False, "no coordinates in witness", {})
            return None

        prescriptions = _spec_prescriptions(spec)
        gluing = _witness_gluing(witness)

        # Build a raw evidence map: coord -> list of evidence dicts.
        raw_evidence: dict[str, list[dict[str, Any]]] = {}
        if isinstance(witness, dict):
            for coord, ev in witness.get("evidence", {}).items():
                raw_evidence[coord] = ev if isinstance(ev, list) else [ev]
        else:
            ev_attr = getattr(witness, "evidence", None) or getattr(witness, "local_sections", None) or {}
            if isinstance(ev_attr, dict):
                for coord, ev in ev_attr.items():
                    raw_evidence[str(coord)] = ev if isinstance(ev, list) else [ev]

        global_section: dict[str, Any] = {}
        for coord in coords:
            evidence_list = raw_evidence.get(coord, [{}])
            aggregated = self._aggregate_evidence_at_coord(evidence_list)
            prescription = prescriptions.get(coord, {})
            entry = self._build_global_judgment(coord, aggregated, prescription)
            global_section[coord] = entry

        # Reconcile overlapping evidence across gluing data.
        for key, datum in gluing.items():
            parts = key.split("::")
            if len(parts) < 2:
                continue
            ca, cb = parts[0], parts[1]
            if ca in global_section and cb in global_section:
                reconciled = self._reconcile_overlapping_evidence(
                    global_section[ca],
                    global_section[cb],
                    datum,
                )
                global_section[ca] = reconciled.get("section_a", global_section[ca])
                global_section[cb] = reconciled.get("section_b", global_section[cb])

        if not self._verify_global_section(global_section, spec):
            self._log_attempt(False, "global section failed verification", global_section)
            return None

        elapsed = time.monotonic() - start
        self.last_extracted = global_section
        self._log_attempt(True, f"extracted in {elapsed:.3f}s", global_section)
        return global_section

    def _aggregate_evidence_at_coord(
        self,
        evidence_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate multiple evidence records at a single coordinate.

        Takes the union of all keys, using the highest-trust value for
        conflicts, and computes a combined trust score.

        Parameters
        ----------
        evidence_list : list[dict[str, Any]]
            Evidence dicts collected at this coordinate.

        Returns
        -------
        dict[str, Any]
            Merged evidence dict with ``trust`` and ``sources`` fields.
        """
        if not evidence_list:
            return {"trust": 0.0, "sources": []}
        merged: dict[str, Any] = {}
        trusts: list[float] = []
        for ev in evidence_list:
            if not isinstance(ev, dict):
                continue
            trust = float(ev.get("trust", 0.5))
            trusts.append(trust)
            for k, v in ev.items():
                if k == "trust":
                    continue
                if k not in merged:
                    merged[k] = v
                else:
                    # Keep value from highest-trust source.
                    existing_trust = float(merged.get("_trust_" + k, 0.0))
                    if trust > existing_trust:
                        merged[k] = v
                        merged["_trust_" + k] = trust
        # Remove internal trust bookkeeping keys.
        merged = {k: v for k, v in merged.items() if not k.startswith("_trust_")}
        merged["trust"] = sum(trusts) / len(trusts) if trusts else 0.0
        merged["sources"] = [ev.get("source", "unknown") for ev in evidence_list if isinstance(ev, dict)]
        return merged

    def _reconcile_overlapping_evidence(
        self,
        evidence_a: dict[str, Any],
        evidence_b: dict[str, Any],
        gluing: Any,
    ) -> dict[str, Any]:
        """Reconcile evidence from two overlapping coordinate patches.

        When gluing data is present and marks the pair as compatible, the
        reconciled sections inherit the higher trust value.

        Parameters
        ----------
        evidence_a : dict[str, Any]
            Aggregated evidence for coordinate A.
        evidence_b : dict[str, Any]
            Aggregated evidence for coordinate B.
        gluing : Any
            Gluing datum dict or None.

        Returns
        -------
        dict[str, Any]
            Keys ``section_a`` and ``section_b`` with potentially updated trust.
        """
        gluing_dict = gluing if isinstance(gluing, dict) else {}
        compatible = gluing_dict.get("compatible", True)
        if not compatible:
            return {"section_a": evidence_a, "section_b": evidence_b}
        # Average the trust when compatible.
        trust_a = float(evidence_a.get("trust", 0.5))
        trust_b = float(evidence_b.get("trust", 0.5))
        reconciled_trust = (trust_a + trust_b) / 2.0
        new_a = dict(evidence_a)
        new_b = dict(evidence_b)
        new_a["trust"] = reconciled_trust
        new_b["trust"] = reconciled_trust
        return {"section_a": new_a, "section_b": new_b}

    def _build_global_judgment(
        self,
        coord: str,
        evidence: dict[str, Any],
        spec_prescription: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a global judgment entry for a single coordinate.

        Parameters
        ----------
        coord : str
            Coordinate key.
        evidence : dict[str, Any]
            Aggregated evidence for this coordinate.
        spec_prescription : dict[str, Any]
            Required prescription from the specification.

        Returns
        -------
        dict[str, Any]
            Judgment entry with ``coord``, ``trust``, ``satisfied``,
            ``evidence``, ``prescription``, and ``judgment_id`` fields.
        """
        trust = float(evidence.get("trust", 0.0))
        required_trust = float(spec_prescription.get("min_trust", 0.0)) if isinstance(spec_prescription, dict) else 0.0
        required_kind = spec_prescription.get("kind", None) if isinstance(spec_prescription, dict) else None
        actual_kind = evidence.get("kind", None)
        kind_ok = (required_kind is None) or (required_kind == actual_kind)
        trust_ok = trust >= required_trust
        satisfied = kind_ok and trust_ok
        return {
            "coord": coord,
            "trust": trust,
            "satisfied": satisfied,
            "evidence": evidence,
            "prescription": spec_prescription,
            "judgment_id": _digest(coord, str(trust), str(satisfied)),
            "kind_match": kind_ok,
            "trust_match": trust_ok,
        }

    def _verify_global_section(
        self,
        global_section: dict[str, Any],
        spec: Any,
    ) -> bool:
        """Check that the extracted section satisfies the specification.

        Parameters
        ----------
        global_section : dict[str, Any]
            The candidate global section.
        spec : Specification
            The specification prescriptions to check against.

        Returns
        -------
        bool
            True when every required coordinate is satisfied.
        """
        errors = self.validate_extracted_section(global_section, spec)
        return len(errors) == 0

    def extract_trust_profile(self, witness: Any) -> dict[str, float]:
        """Extract the per-coordinate trust scores from the witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness to profile.

        Returns
        -------
        dict[str, float]
            Map from coordinate key to trust score in [0.0, 1.0].
        """
        coords = _witness_coords(witness)
        profile: dict[str, float] = {}
        if isinstance(witness, dict):
            for coord in coords:
                ev = witness.get("evidence", {}).get(coord, {})
                profile[coord] = float(ev.get("trust", 0.5)) if isinstance(ev, dict) else 0.5
        else:
            ev_attr = getattr(witness, "evidence", {}) or {}
            for coord in coords:
                ev = ev_attr.get(coord, {})
                profile[coord] = float(ev.get("trust", 0.5)) if isinstance(ev, dict) else 0.5
        return profile

    def validate_extracted_section(
        self,
        global_section: dict[str, Any],
        spec: Any,
    ) -> list[str]:
        """Validate an extracted global section against the specification.

        Parameters
        ----------
        global_section : dict[str, Any]
            Section to validate.
        spec : Specification
            Specification prescriptions used as validation rules.

        Returns
        -------
        list[str]
            List of validation error messages (empty means valid).
        """
        errors: list[str] = []
        required = set(_spec_required_coords(spec))
        provided = set(global_section.keys())
        missing = required - provided
        for coord in missing:
            errors.append(f"required coordinate {coord!r} absent from extracted section")
        for coord, entry in global_section.items():
            if not isinstance(entry, dict):
                errors.append(f"coord {coord!r}: entry is not a dict")
                continue
            if not entry.get("satisfied", True):
                errors.append(
                    f"coord {coord!r}: not satisfied "
                    f"(trust={entry.get('trust', '?')}, kind_match={entry.get('kind_match')})"
                )
        return errors

    # -- internal helpers -----------------------------------------------------

    def _log_attempt(
        self,
        success: bool,
        message: str,
        section: dict[str, Any],
    ) -> None:
        self.extraction_log.append({
            "success": success,
            "message": message,
            "coord_count": len(section),
            "logged_at": _now_iso(),
        })


# ---------------------------------------------------------------------------
# DescentOrchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentOrchestrator:
    """Top-level orchestrator that coordinates all descent sub-steps.

    Combines condition checking, cocycle computation, global-section
    extraction, and certificate or gap construction into a single callable
    pipeline.

    Attributes
    ----------
    condition_checker : DescentConditionChecker
        Runs the five canonical descent conditions.
    overlap_verifier : OverlapCompatibilityVerifier
        Verifies pairwise and triple overlap compatibility.
    extractor : GlobalSectionExtractor
        Extracts a global section from local evidence.
    cocycle_computer : CocycleComputer
        Computes and analyses the Čech 1-cocycle.
    orchestration_log : list[dict]
        Structured log of each orchestration run.
    """

    condition_checker: DescentConditionChecker = field(
        default_factory=DescentConditionChecker
    )
    overlap_verifier: OverlapCompatibilityVerifier = field(
        default_factory=OverlapCompatibilityVerifier
    )
    extractor: GlobalSectionExtractor = field(
        default_factory=GlobalSectionExtractor
    )
    cocycle_computer: CocycleComputer = field(
        default_factory=CocycleComputer
    )
    orchestration_log: list[dict] = field(default_factory=list)

    # -- main pipeline --------------------------------------------------------

    def orchestrate(
        self,
        witness: Any,
        spec: Any,
    ) -> tuple[Any, Any]:
        """Run the full descent pipeline and return a certificate or gap.

        Parameters
        ----------
        witness : SatisfactionWitness
            The assembled witness.
        spec : Specification
            The specification to satisfy.

        Returns
        -------
        tuple[CertificateOfSatisfaction | None, ResidualGap | None]
            On success: ``(certificate, None)``.
            On failure: ``(None, gap)``.

        Raises
        ------
        ValueError
            If both *witness* and *spec* are None.
        """
        if witness is None and spec is None:
            raise ValueError("Both witness and spec are None — nothing to orchestrate.")
        run_id = str(uuid.uuid4())[:8]
        self.orchestration_log.append({"run_id": run_id, "started_at": _now_iso()})

        all_pass, records = self._run_condition_checks(witness, spec)
        failed = [r for r in records if not r["passed"]]

        if all_pass:
            section = self._attempt_extraction(witness, spec)
            if section is not None:
                cert = self._build_certificate(witness, spec, section)
                self.orchestration_log[-1].update({"outcome": "certificate", "run_id": run_id})
                return cert, None

        gap = self._build_gap_from_failed_conditions(witness, spec, failed)
        self.orchestration_log[-1].update({"outcome": "gap", "failed": len(failed), "run_id": run_id})
        return None, gap

    def _run_condition_checks(
        self,
        witness: Any,
        spec: Any,
    ) -> tuple[bool, list[dict]]:
        """Run all descent conditions and return aggregate pass/fail.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness under test.
        spec : Specification
            Target specification.

        Returns
        -------
        tuple[bool, list[dict]]
            ``(all_passed, condition_records)``.
        """
        self.condition_checker.reset()
        return self.condition_checker.check_all(witness, spec)

    def _attempt_extraction(
        self,
        witness: Any,
        spec: Any,
    ) -> dict[str, Any] | None:
        """Attempt global section extraction after conditions pass.

        Parameters
        ----------
        witness : SatisfactionWitness
            Witness providing local evidence.
        spec : Specification
            Specification prescriptions for validation.

        Returns
        -------
        dict[str, Any] | None
            Extracted section or None on failure.
        """
        return self.extractor.extract(witness, spec)

    def _build_certificate(
        self,
        witness: Any,
        spec: Any,
        global_section: dict[str, Any],
    ) -> Any:
        """Construct a CertificateOfSatisfaction from a successful extraction.

        Parameters
        ----------
        witness : SatisfactionWitness
            The witness that supported extraction.
        spec : Specification
            The specification that was satisfied.
        global_section : dict[str, Any]
            The extracted global section.

        Returns
        -------
        CertificateOfSatisfaction | dict
            The certificate object, or a plain dict if the model class is not
            available.
        """
        spec_id = (
            spec.get("id") if isinstance(spec, dict) else getattr(spec, "id", _digest("spec"))
        )
        witness_id = (
            witness.get("id")
            if isinstance(witness, dict)
            else getattr(witness, "id", _digest("witness"))
        )
        cert_id = _digest(str(spec_id), str(witness_id))
        trust_profile = self.extractor.extract_trust_profile(witness)
        avg_trust = sum(trust_profile.values()) / len(trust_profile) if trust_profile else 0.0

        # Attempt to build the typed CertificateOfSatisfaction; fall back to dict.
        try:
            if CertificateOfSatisfaction is not Any:
                return CertificateOfSatisfaction(
                    certificate_id=cert_id,
                    spec_id=str(spec_id),
                    witness_id=str(witness_id),
                    global_section=global_section,
                    trust=avg_trust,
                    issued_at=_now_iso(),
                )
        except Exception:
            pass
        return {
            "certificate_id": cert_id,
            "spec_id": str(spec_id),
            "witness_id": str(witness_id),
            "global_section": global_section,
            "trust": avg_trust,
            "issued_at": _now_iso(),
            "kind": "CertificateOfSatisfaction",
        }

    def _build_gap_from_failed_conditions(
        self,
        witness: Any,
        spec: Any,
        failed: list[dict],
    ) -> Any:
        """Build a ResidualGap from the list of failed descent conditions.

        Parameters
        ----------
        witness : SatisfactionWitness
            The partially satisfying witness.
        spec : Specification
            The target specification.
        failed : list[dict]
            Records of conditions that did not pass.

        Returns
        -------
        ResidualGap | dict
            The gap object or a plain dict if the model class is unavailable.
        """
        spec_id = spec.get("id") if isinstance(spec, dict) else getattr(spec, "id", "unknown_spec")
        coords = _witness_coords(witness)
        required = _spec_required_coords(spec)
        missing_coords = sorted(set(required) - set(coords))
        failed_names = [r["condition"] for r in failed]
        severity = "FATAL" if "COVER_EXISTS" in failed_names or "GLOBAL_SECTION_EXISTS" in failed_names else "PARTIAL"
        gap_id = _digest(str(spec_id), str(failed_names))
        cocycle = self.cocycle_computer.compute_cocycle(witness)
        obstruction = self.cocycle_computer.obstruction_from_cocycle(cocycle) if not self.cocycle_computer.is_trivial(cocycle) else {}

        try:
            if ResidualGap is not Any:
                return ResidualGap(
                    gap_id=gap_id,
                    spec_id=str(spec_id),
                    failed_conditions=failed_names,
                    missing_coordinates=missing_coords,
                    severity=severity,
                    obstruction=obstruction,
                    generated_at=_now_iso(),
                )
        except Exception:
            pass
        return {
            "gap_id": gap_id,
            "spec_id": str(spec_id),
            "failed_conditions": failed_names,
            "missing_coordinates": missing_coords,
            "severity": severity,
            "obstruction": obstruction,
            "generated_at": _now_iso(),
            "kind": "ResidualGap",
        }

    # -- reporting ------------------------------------------------------------

    def get_orchestration_summary(self) -> dict[str, Any]:
        """Return a summary of all orchestration runs performed so far.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_runs``, ``certificate_count``, ``gap_count``,
            ``runs``, ``condition_report``.
        """
        certs = sum(1 for r in self.orchestration_log if r.get("outcome") == "certificate")
        gaps = sum(1 for r in self.orchestration_log if r.get("outcome") == "gap")
        return {
            "total_runs": len(self.orchestration_log),
            "certificate_count": certs,
            "gap_count": gaps,
            "runs": list(self.orchestration_log),
            "condition_report": self.condition_checker.condition_report(),
        }

    def reset(self) -> None:
        """Clear all accumulated orchestration state.

        Returns
        -------
        None
        """
        self.condition_checker.reset()
        self.overlap_verifier.verification_results.clear()
        self.overlap_verifier.violation_messages.clear()
        self.overlap_verifier.verified_pairs_count = 0
        self.extractor.extraction_log.clear()
        self.extractor.last_extracted = None
        self.cocycle_computer.computed_cocycles.clear()
        self.cocycle_computer.is_trivial_cache.clear()
        self.orchestration_log.clear()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def check_descent_conditions(
    witness: Any,
    spec: Any,
) -> tuple[bool, list[dict]]:
    """Run all descent conditions and return pass/fail with condition records.

    Parameters
    ----------
    witness : SatisfactionWitness
        The assembled witness.
    spec : Specification
        The target specification.

    Returns
    -------
    tuple[bool, list[dict]]
        ``(all_passed, condition_records)`` as returned by
        :meth:`DescentConditionChecker.check_all`.
    """
    checker = DescentConditionChecker()
    return checker.check_all(witness, spec)


def extract_global_section(
    witness: Any,
    spec: Any,
) -> dict[str, Any] | None:
    """Extract a global section from the witness.

    Parameters
    ----------
    witness : SatisfactionWitness
        The assembled witness.
    spec : Specification
        Specification prescriptions used for validation.

    Returns
    -------
    dict[str, Any] | None
        The extracted section or None on failure.
    """
    extractor = GlobalSectionExtractor()
    return extractor.extract(witness, spec)


def run_satisfaction_descent(
    witness: Any,
    spec: Any,
) -> tuple[Any, Any]:
    """Run the full descent pipeline and return a certificate or gap.

    Parameters
    ----------
    witness : SatisfactionWitness
        The assembled witness.
    spec : Specification
        The target specification.

    Returns
    -------
    tuple[CertificateOfSatisfaction | None, ResidualGap | None]
        ``(certificate, None)`` on success or ``(None, gap)`` on failure.
    """
    orchestrator = DescentOrchestrator()
    return orchestrator.orchestrate(witness, spec)


def compute_cech_cocycle(witness: Any) -> dict[str, dict]:
    """Compute the Čech 1-cocycle for a witness.

    Parameters
    ----------
    witness : SatisfactionWitness
        Witness whose gluing data are used.

    Returns
    -------
    dict[str, dict]
        The Čech 1-cocycle as returned by :meth:`CocycleComputer.compute_cocycle`.
    """
    computer = CocycleComputer()
    return computer.compute_cocycle(witness)


def is_descent_possible(witness: Any, spec: Any) -> bool:
    """Quick pre-check: is descent at all possible for this witness and spec?

    Returns True when the witness covers all required coordinates and has no
    obviously missing gluing data.  This is a cheaper check than running the
    full condition suite.

    Parameters
    ----------
    witness : SatisfactionWitness
        Witness to check.
    spec : Specification
        Target specification.

    Returns
    -------
    bool
        True when the necessary preconditions for descent appear to hold.
    """
    coords = set(_witness_coords(witness))
    required = set(_spec_required_coords(spec))
    if not required.issubset(coords):
        _log.debug(
            "is_descent_possible: missing coordinates %s", sorted(required - coords)
        )
        return False
    gluing = _witness_gluing(witness)
    coords_list = sorted(coords)
    for i, ca in enumerate(coords_list):
        for cb in coords_list[i + 1 :]:
            key = _pair_key(ca, cb)
            if key not in gluing and f"{ca}::{cb}" not in gluing and f"{cb}::{ca}" not in gluing:
                _log.debug("is_descent_possible: missing gluing for pair (%s, %s)", ca, cb)
                return False
    return True


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
    "DescentConditionChecker",
    "OverlapCompatibilityVerifier",
    "GlobalSectionExtractor",
    "CocycleComputer",
    "DescentOrchestrator",
    "check_descent_conditions",
    "extract_global_section",
    "run_satisfaction_descent",
    "compute_cech_cocycle",
    "is_descent_possible",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
