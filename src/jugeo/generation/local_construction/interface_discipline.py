r"""Interface discipline enforcement for local construction.

Theory (theory2.tex §39 — Interface discipline):
    Each local section s_u must expose a *specific interface* at its boundary ∂u.
    The interface treaty T_{∂u} specifies what the section must agree to provide
    at each point of the boundary:

        T_{∂u} = (E_{∂u}, I_{∂u}, L_{∂u}, σ)

    where:
    * E_{∂u}   — the set of *required exports* (values the section must produce
                 at ∂u so that neighbouring sections can consume them)
    * I_{∂u}   — the set of *required imports* (values the section must be able
                 to receive from its neighbours)
    * L_{∂u}   — the set of *overlap laws* (consistency conditions that must hold
                 whenever two adjacent sections share a boundary segment)
    * σ ∈ {strict, negotiable, lenient}  — the *strictness level* controlling
                 how compliance failures are handled

    A *discipline check* verifies two things:

        check_export(s_u, T_{∂u}) ≡ ∀ e ∈ E_{∂u} :  s_u.boundary[∂u] ⊇ {e}
        check_import(s_u, T_{∂u}) ≡ ∀ i ∈ I_{∂u} :  ∃ src : src provides i to s_u

    The *compliance score* for section s_u under treaty T_{∂u} is:

        score(s_u, T_{∂u}) = (|E_{∂u} ∩ exports(s_u)| + |I_{∂u} ∩ imports(s_u)|)
                              / (|E_{∂u}| + |I_{∂u}|)

    When two sections s_u, s_v share a boundary segment ∂u ∩ ∂v ≠ ∅, the
    *overlap law* L_{uv} ∈ L_{∂u} must hold:

        L_{uv}(s_u|_{∂u∩∂v}, s_v|_{∂u∩∂v}) = ✓

    Failure to satisfy an overlap law is an *interface breach*, escalated to an
    InterfaceBreachError when the strictness level is ``strict``.

    *Negotiation* allows two discipline objects to relax their mutual requirements
    until a satisfiable common interface is found.  The negotiation terminates
    either on agreement or when the maximum number of negotiation rounds is
    exceeded.

    References
    ----------
    theory2.tex  §39 (Local construction loops — Interface discipline)
    theory2.tex  §40 (Overlap laws and gluing conditions)

copilot: s02-interface-discipline
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.local_construction.models import (  # type: ignore[import]
    InterfaceDiscipline,
    InterfaceBreachError,
    LocalConstructionError,
)

__all__ = [
    "InterfaceBreach",
    "NegotiationRecord",
    "InterfaceDisciplineEnforcer",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STRICTNESS_LEVELS: tuple[str, ...] = ("strict", "negotiable", "lenient")
_DEFAULT_TREATY_VERSION: str = "1.0.0"
_EXPORT_STUB_TEMPLATE: dict[str, Any] = {"type": "stub", "value": None, "source": "auto-repair"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InterfaceBreach:
    """Record of a single interface discipline breach.

    Attributes
    ----------
    breach_id:
        Unique identifier for this breach record.
    section_id:
        The section that violated the discipline.
    discipline_id:
        The discipline object that was violated.
    violation_type:
        One of ``"missing_export"``, ``"unsatisfied_import"``,
        ``"overlap_law_violation"``, or ``"combined"``.
    missing_exports:
        Exports required by the discipline but absent from the section.
    severity:
        One of ``"critical"``, ``"major"``, ``"minor"``.
    detected_at:
        Unix timestamp when the breach was first detected.
    """

    breach_id: str
    section_id: str
    discipline_id: str
    violation_type: str
    missing_exports: list[str]
    severity: str
    detected_at: float


@dataclass
class NegotiationRecord:
    """Record of a negotiation session between two InterfaceDiscipline objects.

    Attributes
    ----------
    negotiation_id:
        Unique identifier.
    discipline_a_id:
        ID of the first discipline participant.
    discipline_b_id:
        ID of the second discipline participant.
    rounds:
        Number of negotiation rounds executed.
    outcome:
        One of ``"agreement"``, ``"stalemate"``, ``"aborted"``.
    final_discipline_id:
        The ID of the resulting (merged/relaxed) discipline, or ``None`` if
        no agreement was reached.
    started_at:
        Unix timestamp.
    completed_at:
        Unix timestamp.
    """

    negotiation_id: str
    discipline_a_id: str
    discipline_b_id: str
    rounds: int
    outcome: str
    final_discipline_id: str | None
    started_at: float
    completed_at: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_treaty(treaty_id: str, strictness: str = "strict") -> dict[str, Any]:
    """Fabricate a minimal treaty record for *treaty_id*."""
    return {
        "treaty_id": treaty_id,
        "required_exports": [],
        "required_imports": [],
        "overlap_laws": [],
        "strictness": strictness,
        "version": _DEFAULT_TREATY_VERSION,
        "created_at": time.time(),
    }


def _severity_from_score(score: float) -> str:
    """Map a compliance score to a severity label."""
    if score < 0.4:
        return "critical"
    if score < 0.7:
        return "major"
    return "minor"


def _overlap_law_key(law: str) -> str:
    """Normalise an overlap law identifier for comparison."""
    return law.strip().lower().replace(" ", "_")


def _safe_fraction(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


# ---------------------------------------------------------------------------
# Enforcer
# ---------------------------------------------------------------------------


class InterfaceDisciplineEnforcer:
    """Enforces interface discipline between local sections.

    This class is the operational counterpart to the ``InterfaceDiscipline``
    model.  It holds no mathematical content — all domain logic is delegated
    to the ``InterfaceDiscipline`` model.  The enforcer is responsible for:

    - Loading and caching treaty records
    - Running export-compliance and import-satisfaction checks
    - Detecting and reporting overlap-law violations
    - Negotiating relaxed disciplines when strictness allows
    - Proposing interface refinements
    - Generating compliance reports and repairing breaches
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the enforcer.

        Parameters
        ----------
        config:
            Optional configuration overrides.  Recognised keys:

            ``default_strictness``
                Default strictness level for newly loaded treaties.
                One of ``"strict"``, ``"negotiable"``, ``"lenient"``.
                Default: ``"strict"``.
            ``auto_negotiate``
                Whether to attempt automatic negotiation when a strict
                check fails.  Default: ``True``.
            ``max_negotiation_rounds``
                Maximum rounds per negotiation session.  Default: ``5``.
            ``compliance_threshold``
                Minimum compliance score for a section to be considered
                compliant.  Default: ``0.8``.
        """
        _defaults: dict[str, Any] = {
            "default_strictness": "strict",
            "auto_negotiate": True,
            "max_negotiation_rounds": 5,
            "compliance_threshold": 0.8,
        }
        merged: dict[str, Any] = dict(_defaults)
        if config:
            merged.update(config)
        self._config: dict[str, Any] = merged

        self._logger: logging.Logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._treaty_cache: dict[str, dict[str, Any]] = {}
        self._compliance_history: list[dict[str, Any]] = []
        self._active_negotiations: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Treaty loading
    # ------------------------------------------------------------------

    def load_treaty(self, treaty_id: str) -> dict[str, Any]:
        """Load (or fabricate) a treaty record for *treaty_id*.

        If the treaty is already cached it is returned from the cache.
        Otherwise a minimal record is fabricated, stored, and returned.

        The treaty structure is::

            {
                "treaty_id": str,
                "required_exports": list[str],
                "required_imports": list[str],
                "overlap_laws": list[str],
                "strictness": str,
                "version": str,
                "created_at": float,
            }

        Parameters
        ----------
        treaty_id:
            Identifier of the treaty to load.

        Returns
        -------
        dict
            The treaty record.
        """
        if treaty_id in self._treaty_cache:
            return self._treaty_cache[treaty_id]

        # In a full implementation this would fetch from a treaty store.
        # Here we fabricate a reasonable default.
        treaty = _make_treaty(treaty_id, self._config["default_strictness"])
        self._treaty_cache[treaty_id] = treaty
        self._logger.debug("Loaded (fabricated) treaty %s", treaty_id)
        return treaty

    # ------------------------------------------------------------------
    # Export compliance
    # ------------------------------------------------------------------

    def check_export_compliance(
        self,
        section: dict[str, Any],
        discipline: InterfaceDiscipline,
    ) -> dict[str, Any]:
        """Check whether *section* satisfies the export requirements of *discipline*.

        For each export in ``discipline.required_exports``, the method checks
        whether it appears in ``section.get("exports", {})``.

        Parameters
        ----------
        section:
            The section dict, expected to contain an ``"exports"`` mapping.
        discipline:
            The discipline whose ``required_exports`` are to be checked.

        Returns
        -------
        dict
            ``{
                "compliant": bool,
                "missing": list[str],
                "present": list[str],
                "score": float,
            }``
        """
        threshold = self._config["compliance_threshold"]
        section_exports: dict[str, Any] = section.get("exports", {})
        required: list[str] = list(discipline.required_exports)

        present: list[str] = []
        missing: list[str] = []

        for export_name in required:
            if export_name in section_exports:
                present.append(export_name)
            else:
                missing.append(export_name)

        score = _safe_fraction(len(present), len(required))
        compliant = score >= threshold

        result: dict[str, Any] = {
            "compliant": compliant,
            "missing": missing,
            "present": present,
            "score": score,
            "required_count": len(required),
        }

        self._logger.debug(
            "Export compliance for section '%s' under discipline '%s': "
            "score=%.3f, missing=%s",
            section.get("section_id", "?"),
            discipline.discipline_id,
            score,
            missing,
        )
        return result

    # ------------------------------------------------------------------
    # Import satisfaction
    # ------------------------------------------------------------------

    def check_import_satisfaction(
        self,
        section: dict[str, Any],
        discipline: InterfaceDiscipline,
    ) -> dict[str, Any]:
        """Check whether all required imports of *discipline* have a source.

        For each import in ``discipline.required_imports``, the method checks
        whether ``section.get("imports", {})`` contains a non-empty source
        entry for that import.

        Parameters
        ----------
        section:
            The section dict, expected to contain an ``"imports"`` mapping
            of the form ``{import_name: {"source": ..., ...}}``.
        discipline:
            The discipline specifying ``required_imports``.

        Returns
        -------
        dict
            ``{
                "satisfied": bool,
                "unsatisfied_imports": list[str],
                "satisfied_imports": list[str],
                "score": float,
            }``
        """
        threshold = self._config["compliance_threshold"]
        section_imports: dict[str, Any] = section.get("imports", {})
        required: list[str] = list(discipline.required_imports)

        satisfied_imports: list[str] = []
        unsatisfied_imports: list[str] = []

        for import_name in required:
            entry = section_imports.get(import_name, {})
            # An import is satisfied if it has a non-None, non-empty "source"
            source = entry.get("source") if isinstance(entry, dict) else entry
            if source:
                satisfied_imports.append(import_name)
            else:
                unsatisfied_imports.append(import_name)

        score = _safe_fraction(len(satisfied_imports), len(required))
        satisfied = score >= threshold

        self._logger.debug(
            "Import satisfaction for section '%s' under discipline '%s': "
            "score=%.3f, unsatisfied=%s",
            section.get("section_id", "?"),
            discipline.discipline_id,
            score,
            unsatisfied_imports,
        )
        return {
            "satisfied": satisfied,
            "unsatisfied_imports": unsatisfied_imports,
            "satisfied_imports": satisfied_imports,
            "score": score,
            "required_count": len(required),
        }

    # ------------------------------------------------------------------
    # Overlap law enforcement
    # ------------------------------------------------------------------

    def enforce_overlap_laws(
        self,
        section_a: dict[str, Any],
        section_b: dict[str, Any],
        discipline_a: InterfaceDiscipline,
        discipline_b: InterfaceDiscipline,
    ) -> dict[str, Any]:
        """Enforce overlap laws between two adjacent sections.

        Finds the *shared* overlap laws present in both ``discipline_a`` and
        ``discipline_b``, and for each shared law calls
        ``discipline_a.validate_overlap_law(law, section_a, section_b)``.

        Parameters
        ----------
        section_a:
            First section dict.
        section_b:
            Second section dict.
        discipline_a:
            Discipline for the first section.
        discipline_b:
            Discipline for the second section.

        Returns
        -------
        dict
            ``{
                "laws_checked": list[str],
                "violations": list[dict],
                "compliant": bool,
                "enforcement_record": dict,
            }``
        """
        laws_a = {_overlap_law_key(l): l for l in discipline_a.overlap_laws}
        laws_b = {_overlap_law_key(l): l for l in discipline_b.overlap_laws}
        shared_keys = set(laws_a.keys()) & set(laws_b.keys())
        shared_laws: list[str] = [laws_a[k] for k in sorted(shared_keys)]

        laws_checked: list[str] = []
        violations: list[dict[str, Any]] = []

        for law in shared_laws:
            laws_checked.append(law)
            try:
                ok, reason = discipline_a.validate_overlap_law(law, section_a, section_b)
            except Exception as exc:  # noqa: BLE001
                ok = False
                reason = str(exc)

            if not ok:
                violations.append(
                    {
                        "law": law,
                        "reason": reason,
                        "section_a_id": section_a.get("section_id", "?"),
                        "section_b_id": section_b.get("section_id", "?"),
                        "discipline_a_id": discipline_a.discipline_id,
                        "discipline_b_id": discipline_b.discipline_id,
                    }
                )

        compliant = len(violations) == 0
        enforcement_record: dict[str, Any] = {
            "enforcement_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "shared_laws": shared_laws,
            "violation_count": len(violations),
            "discipline_a_id": discipline_a.discipline_id,
            "discipline_b_id": discipline_b.discipline_id,
        }

        self._logger.debug(
            "Overlap law enforcement: %d laws checked, %d violations "
            "(disciplines %s × %s)",
            len(laws_checked),
            len(violations),
            discipline_a.discipline_id,
            discipline_b.discipline_id,
        )
        return {
            "laws_checked": laws_checked,
            "violations": violations,
            "compliant": compliant,
            "enforcement_record": enforcement_record,
        }

    # ------------------------------------------------------------------
    # Negotiation
    # ------------------------------------------------------------------

    def negotiate_missing_laws(
        self,
        discipline: InterfaceDiscipline,
    ) -> InterfaceDiscipline:
        """Negotiate a relaxed discipline when required laws are missing.

        Behaviour depends on ``discipline.strictness_level``:

        * ``"lenient"``     — return discipline unchanged.
        * ``"negotiable"``  — attempt to produce a relaxed variant with
                               fewer required exports (via
                               ``discipline.negotiate_with`` if available,
                               otherwise by fabricating a relaxed copy).
        * ``"strict"``      — raise :class:`InterfaceBreachError`.

        Parameters
        ----------
        discipline:
            The discipline to relax.

        Returns
        -------
        InterfaceDiscipline
            The (potentially relaxed) discipline.

        Raises
        ------
        InterfaceBreachError
            If ``strictness_level == "strict"`` and the discipline cannot
            be relaxed.
        """
        level = getattr(discipline, "strictness_level", "strict")

        if level == "lenient":
            self._logger.debug(
                "Discipline %s is lenient — no negotiation needed",
                discipline.discipline_id,
            )
            return discipline

        if level == "negotiable":
            self._logger.debug(
                "Attempting to negotiate a relaxed variant of discipline %s",
                discipline.discipline_id,
            )
            # Prefer the model's own negotiation method if it exists
            if hasattr(discipline, "negotiate_with"):
                try:
                    relaxed = discipline.negotiate_with(discipline)
                    if relaxed is not None:
                        return relaxed
                except Exception:  # noqa: BLE001
                    pass

            # Fabricate a relaxed discipline: drop half the required_exports
            current_exports = list(discipline.required_exports)
            retain_count = max(0, len(current_exports) // 2)
            relaxed_exports = current_exports[:retain_count]

            relaxed = InterfaceDiscipline(
                discipline_id=f"{discipline.discipline_id}-relaxed-{uuid.uuid4().hex[:6]}",
                coordinate_id=discipline.coordinate_id,
                boundary_coordinates=discipline.boundary_coordinates,
                required_exports=relaxed_exports,
                required_imports=list(discipline.required_imports),
                overlap_laws=list(discipline.overlap_laws),
                treaty_id=discipline.treaty_id,
                strictness_level="lenient",
            )
            self._logger.debug(
                "Fabricated relaxed discipline %s (exports: %d → %d)",
                relaxed.discipline_id,
                len(current_exports),
                len(relaxed_exports),
            )
            return relaxed

        # strict
        raise InterfaceBreachError(
            f"Discipline {discipline.discipline_id!r} is strict and cannot be "
            "relaxed via negotiation.  Missing laws must be resolved by the "
            "section author."
        )

    # ------------------------------------------------------------------
    # Refinement proposals
    # ------------------------------------------------------------------

    def propose_interface_refinement(
        self,
        section: dict[str, Any],
        discipline: InterfaceDiscipline,
    ) -> dict[str, Any]:
        """Propose additions and waivers to bring a section into compliance.

        The method analyses the gap between what ``section`` currently
        provides and what ``discipline`` requires, then produces a ranked
        list of refinement actions.

        Parameters
        ----------
        section:
            The section dict.
        discipline:
            The discipline the section should satisfy.

        Returns
        -------
        dict
            ``{
                "refinements": list[dict],
                "rationale": str,
                "estimated_effort": float,
                "priority": str,
            }``
        """
        export_check = self.check_export_compliance(section, discipline)
        import_check = self.check_import_satisfaction(section, discipline)

        refinements: list[dict[str, Any]] = []

        # Additions: exports that are missing
        for missing_export in export_check["missing"]:
            refinements.append(
                {
                    "action": "add_export",
                    "target": missing_export,
                    "reason": (
                        f"discipline {discipline.discipline_id!r} requires export "
                        f"{missing_export!r} but section does not provide it"
                    ),
                    "effort": 0.3,
                    "priority": "high",
                }
            )

        # Additions: import sources that are missing
        for missing_import in import_check["unsatisfied_imports"]:
            refinements.append(
                {
                    "action": "wire_import",
                    "target": missing_import,
                    "reason": (
                        f"discipline {discipline.discipline_id!r} requires import "
                        f"{missing_import!r} to have a source but none is registered"
                    ),
                    "effort": 0.2,
                    "priority": "medium",
                }
            )

        # Removals: exports present in section but not in discipline
        section_exports = set(section.get("exports", {}).keys())
        discipline_exports = set(discipline.required_exports)
        superfluous = section_exports - discipline_exports
        for sup in sorted(superfluous):
            refinements.append(
                {
                    "action": "waive_obligation",
                    "target": sup,
                    "reason": (
                        f"export {sup!r} is provided by the section but not required "
                        "by the discipline — can be removed to reduce coupling"
                    ),
                    "effort": 0.05,
                    "priority": "low",
                }
            )

        total_effort = sum(r["effort"] for r in refinements)
        gap = 1.0 - (export_check["score"] + import_check["score"]) / 2.0

        if gap > 0.5:
            priority = "critical"
        elif gap > 0.2:
            priority = "high"
        elif gap > 0.0:
            priority = "medium"
        else:
            priority = "none"

        rationale = (
            f"Section '{section.get('section_id', '?')}' has export compliance "
            f"score {export_check['score']:.2f} and import satisfaction score "
            f"{import_check['score']:.2f} against discipline "
            f"'{discipline.discipline_id}'.  "
            f"{len(refinements)} refinement action(s) proposed."
        )

        return {
            "refinements": refinements,
            "rationale": rationale,
            "estimated_effort": round(total_effort, 4),
            "priority": priority,
        }

    # ------------------------------------------------------------------
    # Full validation
    # ------------------------------------------------------------------

    def validate_full_discipline(
        self,
        section: dict[str, Any],
        discipline: InterfaceDiscipline,
    ) -> dict[str, Any]:
        """Run a complete discipline validation for *section*.

        Combines export compliance and import satisfaction into a single
        comprehensive report.

        Parameters
        ----------
        section:
            The section dict.
        discipline:
            The discipline to validate against.

        Returns
        -------
        dict
            Combined validation report with keys:

            ``valid`` (bool), ``discipline_id`` (str), ``section_id`` (str),
            ``export_compliance`` (dict), ``import_satisfaction`` (dict),
            ``overall_score`` (float), ``timestamp`` (float).
        """
        section_id: str = section.get("section_id", "unknown")
        export_result = self.check_export_compliance(section, discipline)
        import_result = self.check_import_satisfaction(section, discipline)

        overall_score = (export_result["score"] + import_result["score"]) / 2.0
        threshold = self._config["compliance_threshold"]
        valid = export_result["compliant"] and import_result["satisfied"]

        report: dict[str, Any] = {
            "valid": valid,
            "discipline_id": discipline.discipline_id,
            "section_id": section_id,
            "export_compliance": export_result,
            "import_satisfaction": import_result,
            "overall_score": overall_score,
            "timestamp": time.time(),
        }

        # Append to compliance history for generate_compliance_report
        history_entry: dict[str, Any] = {
            "section_id": section_id,
            "discipline_id": discipline.discipline_id,
            "coordinate_id": getattr(discipline, "coordinate_id", "unknown"),
            "valid": valid,
            "overall_score": overall_score,
            "timestamp": report["timestamp"],
        }
        self._compliance_history.append(history_entry)

        self._logger.debug(
            "Full discipline validation for section '%s' / discipline '%s': "
            "valid=%s, score=%.3f",
            section_id,
            discipline.discipline_id,
            valid,
            overall_score,
        )
        return report

    # ------------------------------------------------------------------
    # Compliance reporting
    # ------------------------------------------------------------------

    def generate_compliance_report(self) -> dict[str, Any]:
        """Summarise all compliance checks recorded so far.

        Groups results by ``coordinate_id`` and identifies recent
        violations.

        Returns
        -------
        dict
            ``{
                "summary": dict,
                "by_coordinate": dict[str, dict],
                "recent_violations": list[dict],
                "timestamp": float,
            }``
        """
        history = self._compliance_history

        total = len(history)
        passed = sum(1 for h in history if h["valid"])
        failed = total - passed
        avg_score = (
            sum(h["overall_score"] for h in history) / total if total else 0.0
        )

        by_coordinate: dict[str, dict[str, Any]] = {}
        for entry in history:
            coord = entry.get("coordinate_id", "unknown")
            if coord not in by_coordinate:
                by_coordinate[coord] = {
                    "coordinate_id": coord,
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "scores": [],
                }
            bucket = by_coordinate[coord]
            bucket["total"] += 1
            if entry["valid"]:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
            bucket["scores"].append(entry["overall_score"])

        # Compute per-coordinate averages
        for bucket in by_coordinate.values():
            scores = bucket.pop("scores")
            bucket["avg_score"] = sum(scores) / len(scores) if scores else 0.0

        # Recent violations: last 10 failed checks
        recent_violations = [
            h for h in reversed(history) if not h["valid"]
        ][:10]

        return {
            "summary": {
                "total_checks": total,
                "passed": passed,
                "failed": failed,
                "avg_score": avg_score,
                "pass_rate": _safe_fraction(passed, total),
            },
            "by_coordinate": by_coordinate,
            "recent_violations": recent_violations,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Batch violation detection
    # ------------------------------------------------------------------

    def detect_discipline_violations(
        self,
        sections: list[dict[str, Any]],
        disciplines: list[InterfaceDiscipline],
    ) -> list[dict[str, Any]]:
        """Detect discipline violations across a list of sections.

        Runs ``validate_full_discipline`` for each ``(section, discipline)``
        pair (zipped together) and returns violation records for every pair
        where ``valid == False``.

        Parameters
        ----------
        sections:
            List of section dicts to check.
        disciplines:
            List of disciplines, one per section (must be same length or
            shorter).

        Returns
        -------
        list[dict]
            Each violation dict has:
            ``section_id``, ``discipline_id``, ``violation_type``,
            ``details`` (the full validation report), ``severity``.
        """
        violations: list[dict[str, Any]] = []

        for section, discipline in zip(sections, disciplines):
            report = self.validate_full_discipline(section, discipline)

            if report["valid"]:
                continue

            # Determine violation type
            export_ok = report["export_compliance"]["compliant"]
            import_ok = report["import_satisfaction"]["satisfied"]

            if not export_ok and not import_ok:
                violation_type = "combined"
            elif not export_ok:
                violation_type = "missing_export"
            else:
                violation_type = "unsatisfied_import"

            severity = _severity_from_score(report["overall_score"])

            violations.append(
                {
                    "section_id": report["section_id"],
                    "discipline_id": report["discipline_id"],
                    "violation_type": violation_type,
                    "details": report,
                    "severity": severity,
                }
            )

        self._logger.debug(
            "detect_discipline_violations: checked %d pairs, found %d violations",
            min(len(sections), len(disciplines)),
            len(violations),
        )
        return violations

    # ------------------------------------------------------------------
    # Breach repair
    # ------------------------------------------------------------------

    def repair_interface_breach(
        self,
        breach: dict[str, Any],
    ) -> dict[str, Any]:
        """Attempt to repair an interface breach.

        Three repair strategies are tried in order:

        1. **Add stub exports** — insert placeholder values for each
           missing export into a copy of the section.
        2. **Weaken discipline** — if strictness allows, relax the
           discipline to remove the missing exports from its requirements.
        3. **Request human review** — flag the breach as requiring
           manual intervention.

        Parameters
        ----------
        breach:
            Breach description dict.  Expected keys:
            ``breach_id``, ``section_id``, ``discipline_id``,
            ``missing_exports``, ``violation_type``.

        Returns
        -------
        dict
            ``{
                "breach_id": str,
                "repair_action": str,
                "success": bool,
                "repaired_section": dict | None,
                "repaired_discipline": dict | None,
                "requires_human_review": bool,
            }``
        """
        breach_id: str = breach.get("breach_id", str(uuid.uuid4()))
        section_id: str = breach.get("section_id", "unknown")
        discipline_id: str = breach.get("discipline_id", "unknown")
        missing_exports: list[str] = breach.get("missing_exports", [])
        violation_type: str = breach.get("violation_type", "unknown")

        self._logger.warning(
            "Attempting repair of breach %s (section=%s, discipline=%s, "
            "type=%s, missing=%s)",
            breach_id,
            section_id,
            discipline_id,
            violation_type,
            missing_exports,
        )

        # ------ Strategy 1: Add stub exports ------
        if missing_exports:
            stub_section: dict[str, Any] = dict(breach.get("section", {}))
            stub_section["section_id"] = section_id
            exports: dict[str, Any] = dict(stub_section.get("exports", {}))
            for export_name in missing_exports:
                exports[export_name] = dict(_EXPORT_STUB_TEMPLATE)
            stub_section["exports"] = exports

            self._logger.warning(
                "Breach %s: added %d stub export(s) to section %s",
                breach_id,
                len(missing_exports),
                section_id,
            )
            return {
                "breach_id": breach_id,
                "repair_action": "add_stub_exports",
                "success": True,
                "repaired_section": stub_section,
                "repaired_discipline": None,
                "requires_human_review": False,
            }

        # ------ Strategy 2: Weaken discipline ------
        treaty_id = breach.get("treaty_id", f"treaty-{discipline_id}")
        treaty = self.load_treaty(treaty_id)
        strictness = treaty.get("strictness", "strict")

        if strictness in ("negotiable", "lenient"):
            # Build a discipline-like dict with relaxed requirements
            relaxed_discipline: dict[str, Any] = {
                "discipline_id": f"{discipline_id}-weakened",
                "required_exports": [],
                "required_imports": breach.get("required_imports", []),
                "overlap_laws": breach.get("overlap_laws", []),
                "strictness_level": "lenient",
            }
            self._logger.warning(
                "Breach %s: weakened discipline %s → %s",
                breach_id,
                discipline_id,
                relaxed_discipline["discipline_id"],
            )
            return {
                "breach_id": breach_id,
                "repair_action": "weaken_discipline",
                "success": True,
                "repaired_section": None,
                "repaired_discipline": relaxed_discipline,
                "requires_human_review": False,
            }

        # ------ Strategy 3: Human review ------
        self._logger.error(
            "Breach %s: all automated repair strategies exhausted — "
            "flagging for human review",
            breach_id,
        )
        return {
            "breach_id": breach_id,
            "repair_action": "request_human_review",
            "success": False,
            "repaired_section": None,
            "repaired_discipline": None,
            "requires_human_review": True,
        }

    # ------------------------------------------------------------------
    # Public read-only accessors
    # ------------------------------------------------------------------

    @property
    def treaty_cache(self) -> dict[str, dict[str, Any]]:
        """Read-only view of the treaty cache."""
        return dict(self._treaty_cache)

    @property
    def compliance_history(self) -> list[dict[str, Any]]:
        """Read-only copy of the compliance history."""
        return list(self._compliance_history)

    @property
    def active_negotiations(self) -> dict[str, dict[str, Any]]:
        """Read-only view of active negotiation sessions."""
        return dict(self._active_negotiations)

    def __repr__(self) -> str:
        return (
            f"InterfaceDisciplineEnforcer("
            f"treaties={len(self._treaty_cache)}, "
            f"checks={len(self._compliance_history)}, "
            f"strictness={self._config['default_strictness']!r})"
        )
