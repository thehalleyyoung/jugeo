"""Web trust topology, the "never-trust-client" checker, and policy engine.

The :class:`WebTrustTopology` encodes the fundamental invariant of web
security: **evidence gathered on the client side cannot be promoted past
the client-server boundary without server-side revalidation**.

:class:`NeverTrustClientChecker` audits project metadata (forms, routes,
JS files) for violations of this invariant.

:class:`TrustPolicyEngine` applies a :class:`TrustPolicy` to a list of
evidence items, filtering or demoting them according to the rules.
"""
from __future__ import annotations

from .models import (
    TrustBoundary,
    TrustTransport,
    TrustPolicy,
    TrustRule,
    TrustReport,
    TRUST_ORDER,
    trust_index,
)

# ── layer classification helpers ───────────────────────────────────────

_CLIENT_LAYERS = frozenset({
    "javascript", "browser", "css", "html", "client",
})
_SERVER_LAYERS = frozenset({
    "python", "flask", "django", "server",
})
_DATABASE_LAYERS = frozenset({
    "database", "db", "sql", "postgres", "mysql", "sqlite",
})
_FORMAL_LAYERS = frozenset({
    "formal", "verification", "proof", "solver",
})

_CLIENT_VALIDATED_IDX = trust_index("CLIENT_VALIDATED")


# ═══════════════════════════════════════════════════════════════════════
#  WebTrustTopology
# ═══════════════════════════════════════════════════════════════════════

class WebTrustTopology:
    """Encodes the web trust lattice and transport rules.

    All methods are class-level; no instance state is needed.
    """

    CLIENT_SERVER_BOUNDARY: TrustBoundary = TrustBoundary(
        name="client_server",
        source_layers=["browser", "javascript", "css", "html"],
        target_layers=["python", "database", "server"],
        boundary_type="client_server",
        requires_revalidation=True,
        description="The fundamental trust boundary between client and server",
    )

    TRUST_ORDER: list[str] = TRUST_ORDER

    # ── layer → max trust ──────────────────────────────────────────────

    @staticmethod
    def max_trust_for_layer(layer: str) -> str:
        """Return the maximum trust level achievable inside *layer*."""
        layer_lower = layer.lower()
        if layer_lower in _CLIENT_LAYERS:
            return "CLIENT_VALIDATED"
        if layer_lower in _SERVER_LAYERS:
            return "SERVER_VALIDATED"
        if layer_lower in _DATABASE_LAYERS:
            return "DB_CONSTRAINT_ENFORCED"
        if layer_lower in _FORMAL_LAYERS:
            return "MECHANICALLY_VERIFIED"
        return "SERVER_VALIDATED"

    # ── promotion predicate ────────────────────────────────────────────

    @staticmethod
    def can_promote(
        source_trust: str,
        target_trust: str,
        crosses_boundary: bool,
    ) -> bool:
        """Return whether promoting *source_trust* → *target_trust* is legal.

        When *crosses_boundary* is ``True`` (client → server), client-side
        evidence may not push trust above ``CLIENT_VALIDATED``.
        """
        if crosses_boundary:
            return trust_index(target_trust) <= _CLIENT_VALIDATED_IDX
        return trust_index(target_trust) >= trust_index(source_trust)

    # ── morphism-driven trust change ───────────────────────────────────

    @staticmethod
    def trust_after_transport(morphism_kind: str, source_trust: str) -> str:
        """Compute trust level after a single morphism transport."""
        kind = morphism_kind.upper()
        if kind == "API_CONTRACT":
            return "API_CONTRACT_TESTED"
        if kind == "ORM_MAPPING":
            return "ORM_TYPE_CHECKED"
        if kind == "TEMPLATE_RENDERING":
            return "TEMPLATE_TYPE_CHECKED"
        if kind == "DB_CONSTRAINT":
            return "DB_CONSTRAINT_ENFORCED"
        if kind == "MIDDLEWARE":
            return "MIDDLEWARE_ENFORCED"
        return source_trust

    # ── chain validation ───────────────────────────────────────────────

    @classmethod
    def validate_trust_chain(cls, chain: list[dict]) -> TrustReport:
        """Validate a sequence of trust-transport steps.

        Each element of *chain* is a dict::

            {
                "from": <source_trust>,
                "to":   <target_trust>,
                "morphism": <morphism_kind>,
                "crosses_boundary": <bool>,
            }

        Returns a :class:`TrustReport` summarising boundaries crossed,
        transports applied, and any violations found.
        """
        violations: list[dict] = []
        transports: list[TrustTransport] = []
        boundaries: list[TrustBoundary] = []
        current_trust = chain[0]["from"] if chain else "USER_INPUT"

        for i, step in enumerate(chain):
            src = step["from"]
            tgt = step["to"]
            morphism = step["morphism"]
            crosses = step.get("crosses_boundary", False)

            src_idx = trust_index(src)
            tgt_idx = trust_index(tgt)
            change = tgt_idx - src_idx

            # Record boundary when crossed
            if crosses:
                boundaries.append(cls.CLIENT_SERVER_BOUNDARY)

            # Check: client evidence promoted past CLIENT_VALIDATED?
            if crosses and tgt_idx > _CLIENT_VALIDATED_IDX:
                violations.append({
                    "location": f"step[{i}]",
                    "violation": (
                        f"Client-side trust {src!r} promoted to {tgt!r} "
                        f"across client-server boundary"
                    ),
                    "severity": "error",
                })

            # Check: illegal promotion (target lower than source without
            # boundary crossing)
            if not crosses and not cls.can_promote(src, tgt, crosses):
                violations.append({
                    "location": f"step[{i}]",
                    "violation": (
                        f"Illegal trust promotion from {src!r} to {tgt!r}"
                    ),
                    "severity": "error",
                })

            valid = not any(
                v["location"] == f"step[{i}]" for v in violations
            )

            transports.append(TrustTransport(
                morphism_kind=morphism,
                source_trust=src,
                target_trust=tgt,
                trust_change=change,
                valid=valid,
                reason="" if valid else "trust policy violation",
            ))

            current_trust = tgt

        return TrustReport(
            boundaries=boundaries,
            violations=violations,
            transports=transports,
            policy_applied="web_trust_topology",
            overall_trust=current_trust,
            passed=len(violations) == 0,
        )


# ═══════════════════════════════════════════════════════════════════════
#  NeverTrustClientChecker
# ═══════════════════════════════════════════════════════════════════════

class NeverTrustClientChecker:
    """Audit project metadata for "never trust the client" violations.

    Expects *project_data* shaped as::

        {
            "forms": [
                {"action": "/submit", "method": "POST",
                 "has_client_validation": True},
                ...
            ],
            "routes": [
                {"path": "/submit", "methods": ["POST"],
                 "has_server_validation": True, "requires_auth": True},
                ...
            ],
            "js_files": {
                "app.js": {"has_auth_check": True},
                ...
            },
        }
    """

    def check(self, project_data: dict) -> list[dict]:
        """Run all sub-checks and return a flat list of issues."""
        forms = project_data.get("forms", [])
        routes = project_data.get("routes", [])
        js_files = project_data.get("js_files", {})

        issues: list[dict] = []
        issues.extend(self._find_client_only_validation(forms, routes))
        issues.extend(self._find_js_auth_without_server(js_files, routes))
        return issues

    # ── sub-checks ─────────────────────────────────────────────────────

    @staticmethod
    def _find_client_only_validation(
        forms: list[dict],
        routes: list[dict],
    ) -> list[dict]:
        """Find forms relying on client-side validation only."""
        route_lookup: dict[str, dict] = {}
        for route in routes:
            route_lookup[route["path"]] = route

        issues: list[dict] = []
        for form in forms:
            if form.get("method", "").upper() != "POST":
                continue
            if not form.get("has_client_validation", False):
                continue

            action = form.get("action", "")
            route = route_lookup.get(action)

            if route is None:
                issues.append({
                    "issue": "Form POSTs to unknown route",
                    "location": f"form action={action!r}",
                    "severity": "warning",
                    "repair": (
                        f"Add a server-side route for {action!r} with "
                        f"validation"
                    ),
                })
                continue

            if not route.get("has_server_validation", False):
                issues.append({
                    "issue": "Client-only validation on POST form",
                    "location": f"form action={action!r}",
                    "severity": "error",
                    "repair": (
                        f"Add server-side validation to route {action!r}"
                    ),
                })

        return issues

    @staticmethod
    def _find_js_auth_without_server(
        js_files: dict[str, dict],
        routes: list[dict],
    ) -> list[dict]:
        """Find routes that appear to rely only on JS-based auth checks."""
        routes_needing_auth = {
            r["path"]: r
            for r in routes
            if r.get("requires_auth", False)
        }

        js_auth_paths: set[str] = set()
        for filename, info in js_files.items():
            if info.get("has_auth_check", False):
                js_auth_paths.add(filename)

        if not js_auth_paths:
            return []

        issues: list[dict] = []
        for path, route in routes_needing_auth.items():
            if not route.get("has_server_validation", False):
                issues.append({
                    "issue": "Auth may rely on client-side JS only",
                    "location": f"route {path!r}",
                    "severity": "error",
                    "repair": (
                        f"Add server-side authentication middleware to "
                        f"route {path!r}"
                    ),
                })

        return issues


# ═══════════════════════════════════════════════════════════════════════
#  TrustPolicyEngine
# ═══════════════════════════════════════════════════════════════════════

class TrustPolicyEngine:
    """Evaluate a :class:`TrustPolicy` against a list of evidence items.

    Each evidence item is expected to carry at least a ``trust_level``
    key whose value is one of the strings in :data:`TRUST_ORDER`.
    """

    DEFAULT_WEB_POLICY: TrustPolicy = TrustPolicy(
        name="default_web_policy",
        rules=[
            TrustRule(
                condition="crosses_client_server_boundary",
                action="demote",
                trust_floor="USER_INPUT",
                trust_ceiling="CLIENT_VALIDATED",
                description=(
                    "Demote any evidence that crosses the client-server "
                    "boundary to at most CLIENT_VALIDATED"
                ),
            ),
            TrustRule(
                condition="requires_server_validation",
                action="deny",
                trust_floor="SERVER_VALIDATED",
                trust_ceiling="MECHANICALLY_VERIFIED",
                description=(
                    "Deny evidence below SERVER_VALIDATED for routes that "
                    "require server validation"
                ),
            ),
            TrustRule(
                condition="database_write",
                action="require_revalidation",
                trust_floor="DB_CONSTRAINT_ENFORCED",
                trust_ceiling="MECHANICALLY_VERIFIED",
                description=(
                    "Require revalidation for evidence below "
                    "DB_CONSTRAINT_ENFORCED on database writes"
                ),
            ),
        ],
        default_action="allow",
        description="Default web policy enforcing never-trust-client",
    )

    @staticmethod
    def apply_policy(
        policy: TrustPolicy,
        evidence: list[dict],
    ) -> list[dict]:
        """Filter / demote *evidence* according to *policy*.

        Returns a new list of evidence items with ``action_taken`` and
        (for demotions) adjusted ``trust_level`` values.
        """
        results: list[dict] = []

        for item in evidence:
            trust = item.get("trust_level", "USER_INPUT")
            trust_idx = trust_index(trust)
            action_taken = policy.default_action
            matched_rule: TrustRule | None = None

            for rule in policy.rules:
                condition = item.get("condition", "")
                if condition != rule.condition:
                    continue

                floor_idx = trust_index(rule.trust_floor)
                ceiling_idx = trust_index(rule.trust_ceiling)

                if rule.action == "deny" and trust_idx < floor_idx:
                    action_taken = "deny"
                    matched_rule = rule
                    break

                if rule.action == "demote" and trust_idx > ceiling_idx:
                    action_taken = "demote"
                    matched_rule = rule
                    break

                if (
                    rule.action == "require_revalidation"
                    and trust_idx < floor_idx
                ):
                    action_taken = "require_revalidation"
                    matched_rule = rule
                    break

                if rule.action == "allow":
                    action_taken = "allow"
                    matched_rule = rule
                    break

            out = dict(item)
            out["action_taken"] = action_taken

            if action_taken == "demote" and matched_rule is not None:
                out["trust_level"] = matched_rule.trust_ceiling

            if action_taken != "deny":
                results.append(out)

        return results
