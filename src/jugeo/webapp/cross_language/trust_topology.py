"""
Trust topology for cross-language analysis.

Models the partial order of trust levels across web-application layers
and enforces that trust cannot promote across trust-boundary crossings
without re-evidence.  This is the Grothendieck transport condition (§3.4).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation


__all__ = [
    "WebTrustLevel",
    "TrustBoundary",
    "WebTrustChecker",
    "TrustTransportChecker",
]


def _vid(*parts: str) -> str:
    """Deterministic violation id."""
    raw = ":".join(parts)
    return "v-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Trust level enum
# ---------------------------------------------------------------------------

class WebTrustLevel(str, Enum):
    """Trust levels from highest (most reliable) to lowest."""
    DB_CONSTRAINT_ENFORCED = "db_constraint_enforced"
    SERVER_VALIDATED = "server_validated"
    MIDDLEWARE_ENFORCED = "middleware_enforced"
    ORM_TYPE_CHECKED = "orm_type_checked"
    API_CONTRACT_TESTED = "api_contract_tested"
    SCHEMA_VALIDATED = "schema_validated"
    TEMPLATE_TYPE_CHECKED = "template_type_checked"
    JS_TYPE_CHECKED = "js_type_checked"
    CLIENT_VALIDATED = "client_validated"
    CSS_LINTED = "css_linted"
    BROWSER_TESTED = "browser_tested"
    USER_INPUT = "user_input"


# Ordered from highest to lowest trust
_TRUST_ORDER: list[WebTrustLevel] = [
    WebTrustLevel.DB_CONSTRAINT_ENFORCED,
    WebTrustLevel.SERVER_VALIDATED,
    WebTrustLevel.MIDDLEWARE_ENFORCED,
    WebTrustLevel.ORM_TYPE_CHECKED,
    WebTrustLevel.API_CONTRACT_TESTED,
    WebTrustLevel.SCHEMA_VALIDATED,
    WebTrustLevel.TEMPLATE_TYPE_CHECKED,
    WebTrustLevel.JS_TYPE_CHECKED,
    WebTrustLevel.CLIENT_VALIDATED,
    WebTrustLevel.CSS_LINTED,
    WebTrustLevel.BROWSER_TESTED,
    WebTrustLevel.USER_INPUT,
]

_TRUST_RANK: dict[WebTrustLevel, int] = {
    level: idx for idx, level in enumerate(_TRUST_ORDER)
}


# ---------------------------------------------------------------------------
# Trust boundary
# ---------------------------------------------------------------------------

@dataclass
class TrustBoundary:
    """
    A boundary between two layers that may require re-validation.

    When ``requires_revalidation`` is ``True``, trust cannot promote
    across this boundary without new evidence.
    """
    source_layer: str
    target_layer: str
    requires_revalidation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_layer": self.source_layer,
            "target_layer": self.target_layer,
            "requires_revalidation": self.requires_revalidation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustBoundary:
        return cls(
            source_layer=data["source_layer"],
            target_layer=data["target_layer"],
            requires_revalidation=data["requires_revalidation"],
        )


# ---------------------------------------------------------------------------
# Layer → max trust mapping
# ---------------------------------------------------------------------------

_LAYER_MAX_TRUST: dict[str, WebTrustLevel] = {
    "sql": WebTrustLevel.DB_CONSTRAINT_ENFORCED,
    "db": WebTrustLevel.DB_CONSTRAINT_ENFORCED,
    "python": WebTrustLevel.SERVER_VALIDATED,
    "flask": WebTrustLevel.SERVER_VALIDATED,
    "middleware": WebTrustLevel.MIDDLEWARE_ENFORCED,
    "orm": WebTrustLevel.ORM_TYPE_CHECKED,
    "jinja2": WebTrustLevel.TEMPLATE_TYPE_CHECKED,
    "template": WebTrustLevel.TEMPLATE_TYPE_CHECKED,
    "js": WebTrustLevel.JS_TYPE_CHECKED,
    "javascript": WebTrustLevel.JS_TYPE_CHECKED,
    "html": WebTrustLevel.CLIENT_VALIDATED,
    "css": WebTrustLevel.CLIENT_VALIDATED,
    "browser": WebTrustLevel.BROWSER_TESTED,
}

# Layers considered client-side vs server-side
_CLIENT_LAYERS: set[str] = {"js", "javascript", "css", "html", "browser"}
_SERVER_LAYERS: set[str] = {"python", "flask", "jinja2", "sql", "orm", "middleware", "db"}


# ---------------------------------------------------------------------------
# Trust checker
# ---------------------------------------------------------------------------

class WebTrustChecker:
    """
    Enforce trust topology rules.

    The fundamental rule: trust cannot promote across a client→server
    boundary without re-evidence.
    """

    CLIENT_SERVER_BOUNDARY: set[str] = {
        "api_call", "form_submit", "fetch", "xhr", "websocket",
    }

    def check_trust_promotion(
        self,
        morphism: dict,
        source_trust: str,
        target_trust: str,
    ) -> bool:
        """
        Can trust promote across this morphism?

        Returns ``False`` if the morphism crosses a client→server
        boundary and the target trust is higher than the source trust
        (i.e. an invalid promotion).

        Parameters
        ----------
        morphism : dict
            Standard morphism dict with ``kind``, ``source``, ``target``.
        source_trust : str
            Trust level value string at the source.
        target_trust : str
            Trust level value string at the target.
        """
        # Parse trust levels
        try:
            src_level = WebTrustLevel(source_trust)
            tgt_level = WebTrustLevel(target_trust)
        except ValueError:
            return True  # Unknown levels — allow

        src_rank = _TRUST_RANK.get(src_level, len(_TRUST_ORDER))
        tgt_rank = _TRUST_RANK.get(tgt_level, len(_TRUST_ORDER))

        # Lower rank = higher trust.  Promotion = target rank < source rank.
        if tgt_rank >= src_rank:
            return True  # Not a promotion — allowed

        # It's a promotion.  Check if we cross a trust boundary.
        kind = morphism.get("kind", "")
        if kind in self.CLIENT_SERVER_BOUNDARY:
            return False  # Cannot promote across boundary

        # Check layer-level boundary
        src_layer = morphism.get("source", "").split(":")[0].lower()
        tgt_layer = morphism.get("target", "").split(":")[0].lower()
        if src_layer in _CLIENT_LAYERS and tgt_layer in _SERVER_LAYERS:
            return False

        return True

    def max_trust_at_layer(self, layer: str) -> WebTrustLevel:
        """Maximum achievable trust for each layer."""
        return _LAYER_MAX_TRUST.get(layer.lower(), WebTrustLevel.USER_INPUT)

    def check_never_trust_client(
        self,
        project_data: dict,
    ) -> list[OverlapViolation]:
        """
        Find violations where client validation is the sole validation.

        Specifically: routes that accept user input without any
        server-side validation (relying only on JS/HTML validation).

        Expected keys in *project_data*::

            routes: [{"pattern", "methods", "has_server_validation", ...}]
            client_validations: [{"route", "validation_type", "file", "line"}]
            server_validations: [{"route", "validation_type", "file", "line"}]
        """
        violations: list[OverlapViolation] = []

        server_validated_routes: set[str] = set()
        for sv in project_data.get("server_validations", []):
            server_validated_routes.add(sv["route"])

        client_validated_routes: set[str] = set()
        for cv in project_data.get("client_validations", []):
            client_validated_routes.add(cv["route"])

        # Routes with client validation but no server validation
        only_client = client_validated_routes - server_validated_routes
        for route_pattern in sorted(only_client):
            violations.append(OverlapViolation(
                id=_vid("never_trust_client", route_pattern),
                condition_id=f"oc:trust:client_only:{route_pattern}",
                kind=OverlapKind.AUTH_SESSION,
                message=(
                    f"Route '{route_pattern}' has client-side validation "
                    f"but no server-side validation"
                ),
                severity="error",
                left_detail=f"client validates: {route_pattern}",
                right_detail="no server validation found",
                repair_hint="Add server-side validation (never trust the client)",
            ))

        # Also check routes that accept mutation methods without any validation
        for route in project_data.get("routes", []):
            methods = {m.upper() for m in route.get("methods", ["GET"])}
            mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}
            if methods & mutation_methods:
                pattern = route["pattern"]
                if (
                    pattern not in server_validated_routes
                    and pattern not in client_validated_routes
                ):
                    violations.append(OverlapViolation(
                        id=_vid("no_validation", pattern),
                        condition_id=f"oc:trust:no_validation:{pattern}",
                        kind=OverlapKind.AUTH_SESSION,
                        message=(
                            f"Route '{pattern}' accepts {sorted(methods & mutation_methods)} "
                            f"but has no validation at all"
                        ),
                        severity="error",
                        left_detail=f"route methods: {sorted(methods)}",
                        right_detail="no validation found",
                        repair_hint="Add server-side validation for mutation routes",
                        file_path=route.get("file", ""),
                        line_number=route.get("line", 0),
                    ))

        return violations


# ---------------------------------------------------------------------------
# Trust transport checker
# ---------------------------------------------------------------------------

class TrustTransportChecker:
    """
    Verify that trust can transport along a chain of morphisms.

    The transport result records the minimum trust along the chain,
    boundary crossings, and any violations.
    """

    def __init__(self) -> None:
        self._checker = WebTrustChecker()

    def verify_trust_transport(
        self,
        morphism_chain: list[dict],
    ) -> dict:
        """
        Verify trust can transport along *morphism_chain*.

        Parameters
        ----------
        morphism_chain : list[dict]
            A list of morphism dicts in order of traversal.

        Returns
        -------
        dict
            ``{"valid": bool, "min_trust": str,
              "boundary_crossings": int, "violations": list}``
        """
        if not morphism_chain:
            return {
                "valid": True,
                "min_trust": WebTrustLevel.USER_INPUT.value,
                "boundary_crossings": 0,
                "violations": [],
            }

        violations: list[str] = []
        boundary_crossings = 0
        min_rank = 0  # Start with highest trust (rank 0)

        for i, morphism in enumerate(morphism_chain):
            src_layer = morphism.get("source", "").split(":")[0].lower()
            tgt_layer = morphism.get("target", "").split(":")[0].lower()

            # Determine trust at source and target
            src_trust = self._checker.max_trust_at_layer(src_layer)
            tgt_trust = self._checker.max_trust_at_layer(tgt_layer)
            src_rank = _TRUST_RANK.get(src_trust, len(_TRUST_ORDER) - 1)
            tgt_rank = _TRUST_RANK.get(tgt_trust, len(_TRUST_ORDER) - 1)

            # Track minimum trust (highest rank number = lowest trust)
            min_rank = max(min_rank, src_rank, tgt_rank)

            # Detect boundary crossings
            src_side = (
                "client" if src_layer in _CLIENT_LAYERS
                else "server" if src_layer in _SERVER_LAYERS
                else "unknown"
            )
            tgt_side = (
                "client" if tgt_layer in _CLIENT_LAYERS
                else "server" if tgt_layer in _SERVER_LAYERS
                else "unknown"
            )
            if src_side != tgt_side and src_side != "unknown" and tgt_side != "unknown":
                boundary_crossings += 1

            # Check for invalid promotion
            if not self._checker.check_trust_promotion(
                morphism, src_trust.value, tgt_trust.value
            ):
                violations.append(
                    f"Step {i}: invalid trust promotion from "
                    f"{src_layer}({src_trust.value}) to "
                    f"{tgt_layer}({tgt_trust.value}) "
                    f"via {morphism.get('kind', '?')}"
                )

        min_trust_level = _TRUST_ORDER[min(min_rank, len(_TRUST_ORDER) - 1)]

        return {
            "valid": len(violations) == 0,
            "min_trust": min_trust_level.value,
            "boundary_crossings": boundary_crossings,
            "violations": violations,
        }
