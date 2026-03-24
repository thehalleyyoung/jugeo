"""
Formal descent theorems for web applications.

Each theorem states a sufficient condition under which a particular
class of sheaf-condition violations cannot exist.  The ``check`` method
verifies the condition against concrete ``site_data`` and returns
structured evidence.
"""
from __future__ import annotations

from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation
from jugeo.webapp.cross_language.overlap_checker import OverlapChecker
from jugeo.webapp.cross_language.trust_topology import WebTrustChecker
from jugeo.webapp.descent.cohomology import CechCohomology


__all__ = [
    "ContextCompletenessTheorem",
    "ContractConsistencyTheorem",
    "DOMIntegrityTheorem",
    "TrustMonotonicityTheorem",
    "CohomologicalCompletenessTheorem",
]


def _check_result(
    holds: bool,
    evidence: list[str],
    counterexample: dict | None = None,
) -> dict[str, Any]:
    """Build a standard theorem-check result dict."""
    return {
        "holds": holds,
        "evidence": evidence,
        "counterexample": counterexample,
    }


# ---------------------------------------------------------------------------
# Theorem 1 — Context Completeness
# ---------------------------------------------------------------------------

class ContextCompletenessTheorem:
    """
    **Context Completeness Theorem.**

    *If ``render_template`` provides all variables used in the template,
    the context-provision morphism is an isomorphism on the overlap.*

    Concretely: for every route that renders a template, the set of
    ``context_vars`` passed to ``render_template`` must be a superset of
    the template's ``{{ variables }}``.

    When the theorem holds, H¹ for the ROUTE_TEMPLATE overlap is zero.
    """

    name = "ContextCompletenessTheorem"
    statement = (
        "If render_template provides all variables used in the template, "
        "the context provision morphism is an isomorphism on the overlap"
    )

    def check(self, site_data: dict) -> dict[str, Any]:
        """
        Check the theorem against *site_data*.

        Returns ``{"holds": bool, "evidence": list[str],
        "counterexample": dict | None}``.
        """
        checker = OverlapChecker()
        violations = checker.check_route_template(
            site_data.get("routes", []),
            site_data.get("templates", []),
        )

        # Only consider errors (missing vars), not warnings (unused vars)
        errors = [v for v in violations if v.severity == "error"]

        evidence: list[str] = []
        routes = site_data.get("routes", [])
        templates = site_data.get("templates", [])
        evidence.append(
            f"Checked {len(routes)} route(s) against "
            f"{len(templates)} template(s)"
        )

        if not errors:
            evidence.append(
                "All template variables are provided by their "
                "render_template calls"
            )
            return _check_result(True, evidence)

        # Theorem fails — build counterexample from first violation
        first = errors[0]
        evidence.append(
            f"Found {len(errors)} missing variable(s): "
            f"{first.message}"
        )
        counterexample = {
            "violation_id": first.id,
            "message": first.message,
            "left_detail": first.left_detail,
            "right_detail": first.right_detail,
            "file": first.file_path,
            "line": first.line_number,
        }
        return _check_result(False, evidence, counterexample)


# ---------------------------------------------------------------------------
# Theorem 2 — Contract Consistency
# ---------------------------------------------------------------------------

class ContractConsistencyTheorem:
    """
    **Contract Consistency Theorem.**

    *If the server response schema subsumes the client expected schema,
    the API-contract morphism preserves sections.*

    Concretely: for every JS ``fetch`` call, every expected field must
    appear in the server route's response.

    When the theorem holds, H¹ for the ROUTE_JS_FETCH overlap is zero.
    """

    name = "ContractConsistencyTheorem"
    statement = (
        "If the server response schema subsumes the client expected schema, "
        "the API contract morphism preserves sections"
    )

    def check(self, site_data: dict) -> dict[str, Any]:
        """
        Check the theorem against *site_data*.
        """
        checker = OverlapChecker()
        violations = checker.check_route_js_fetch(
            site_data.get("routes", []),
            site_data.get("fetch_calls", []),
        )

        evidence: list[str] = []
        fetch_calls = site_data.get("fetch_calls", [])
        routes = site_data.get("routes", [])
        evidence.append(
            f"Checked {len(fetch_calls)} fetch call(s) against "
            f"{len(routes)} route(s)"
        )

        if not violations:
            evidence.append(
                "All client-expected fields are provided by server routes"
            )
            return _check_result(True, evidence)

        first = violations[0]
        evidence.append(
            f"Found {len(violations)} contract violation(s): "
            f"{first.message}"
        )
        counterexample = {
            "violation_id": first.id,
            "message": first.message,
            "left_detail": first.left_detail,
            "right_detail": first.right_detail,
            "file": first.file_path,
            "line": first.line_number,
        }
        return _check_result(False, evidence, counterexample)


# ---------------------------------------------------------------------------
# Theorem 3 — DOM Integrity
# ---------------------------------------------------------------------------

class DOMIntegrityTheorem:
    """
    **DOM Integrity Theorem.**

    *If every JS DOM reference resolves to an HTML id, the DOM-selection
    morphisms have no kernel.*

    Concretely: every ``getElementById`` / ``querySelector`` call in JS
    targets an ``id`` that exists in the HTML.

    When the theorem holds, H¹ for the JS_DOM_HTML overlap is zero.
    """

    name = "DOMIntegrityTheorem"
    statement = (
        "If every JS DOM reference resolves to an HTML id, "
        "the DOM selection morphisms have no kernel"
    )

    def check(self, site_data: dict) -> dict[str, Any]:
        """
        Check the theorem against *site_data*.
        """
        checker = OverlapChecker()
        js_refs = site_data.get("js_dom_refs", [])
        html_ids = site_data.get("html_ids", set())

        violations = checker.check_js_dom_html(js_refs, html_ids)

        evidence: list[str] = []
        evidence.append(
            f"Checked {len(js_refs)} JS DOM reference(s) against "
            f"{len(html_ids)} HTML id(s)"
        )

        if not violations:
            evidence.append(
                "All JS DOM references resolve to existing HTML ids"
            )
            return _check_result(True, evidence)

        first = violations[0]
        evidence.append(
            f"Found {len(violations)} unresolved DOM reference(s): "
            f"{first.message}"
        )
        counterexample = {
            "violation_id": first.id,
            "message": first.message,
            "element_id": first.left_detail,
            "html_ids_sample": first.right_detail,
            "file": first.file_path,
            "line": first.line_number,
        }
        return _check_result(False, evidence, counterexample)


# ---------------------------------------------------------------------------
# Theorem 4 — Trust Monotonicity
# ---------------------------------------------------------------------------

class TrustMonotonicityTheorem:
    """
    **Trust Monotonicity Theorem.**

    *Trust cannot increase when transporting across the client-server
    boundary without server-side re-evidence.*

    Concretely: no route that accepts user input via a mutation HTTP
    method should rely solely on client-side validation.

    When the theorem holds, the trust topology is consistent.
    """

    name = "TrustMonotonicityTheorem"
    statement = (
        "Trust cannot increase when transporting across the "
        "client-server boundary without server-side re-evidence"
    )

    def check(self, site_data: dict) -> dict[str, Any]:
        """
        Check the theorem against *site_data*.
        """
        trust_checker = WebTrustChecker()
        violations = trust_checker.check_never_trust_client(site_data)

        evidence: list[str] = []
        routes = site_data.get("routes", [])
        evidence.append(
            f"Checked trust boundaries across {len(routes)} route(s)"
        )

        client_validations = site_data.get("client_validations", [])
        server_validations = site_data.get("server_validations", [])
        evidence.append(
            f"Found {len(client_validations)} client validation(s), "
            f"{len(server_validations)} server validation(s)"
        )

        if not violations:
            evidence.append(
                "Trust monotonicity holds: no invalid trust promotion "
                "across the client-server boundary"
            )
            return _check_result(True, evidence)

        first = violations[0]
        evidence.append(
            f"Found {len(violations)} trust violation(s): "
            f"{first.message}"
        )
        counterexample = {
            "violation_id": first.id,
            "message": first.message,
            "left_detail": first.left_detail,
            "right_detail": first.right_detail,
            "file": first.file_path,
            "line": first.line_number,
        }
        return _check_result(False, evidence, counterexample)


# ---------------------------------------------------------------------------
# Theorem 5 — Cohomological Completeness
# ---------------------------------------------------------------------------

class CohomologicalCompletenessTheorem:
    """
    **Cohomological Completeness Theorem.**

    *If H¹ = 0 for all pairwise overlaps, the web application has a
    global section (all language layers are consistent).*

    This is the main descent theorem: vanishing of all H¹ obstructions
    implies the existence of a global section — a fully consistent
    cross-language state.
    """

    name = "CohomologicalCompletenessTheorem"
    statement = (
        "If H¹ = 0 for all pairwise overlaps, the web application has "
        "a global section (all language layers are consistent)"
    )

    def check(self, site_data: dict) -> dict[str, Any]:
        """
        Check the theorem against *site_data*.

        Computes H¹ via :class:`CechCohomology` and checks whether
        it vanishes.  If it does, also reports the global sections (H⁰).
        """
        cech = CechCohomology()
        h1 = cech.compute_h1(site_data)

        evidence: list[str] = []
        evidence.append(f"Computed H¹: found {len(h1)} obstruction(s)")

        if not h1:
            # H¹ = 0 → theorem holds → report global sections
            h0 = cech.compute_h0(site_data)
            evidence.append(
                f"H¹ = 0: the application has {len(h0)} global section(s)"
            )
            evidence.append(
                "All pairwise overlaps are consistent — descent succeeds"
            )
            return _check_result(True, evidence)

        # H¹ ≠ 0 — gather some detail
        by_kind: dict[str, int] = {}
        for obs in h1:
            by_kind[obs.overlap_kind] = by_kind.get(obs.overlap_kind, 0) + 1

        for kind, count in sorted(by_kind.items()):
            evidence.append(
                f"  {kind}: {count} obstruction(s)"
            )

        first = h1[0]
        evidence.append(
            f"First obstruction: {first.description}"
        )
        counterexample = {
            "obstruction_id": first.id,
            "cohomology_class": first.cohomology_class.value,
            "overlap_kind": first.overlap_kind,
            "description": first.description,
            "coordinates": first.coordinates,
            "severity": first.severity,
            "repair_hint": first.repair_hint,
        }
        return _check_result(False, evidence, counterexample)
