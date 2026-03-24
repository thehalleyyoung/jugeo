"""
API contract checker for cross-language analysis.

Verifies that the implicit contract between Flask routes and JS fetch
calls is honoured: request schemas, response schemas, error codes,
HTTP methods, and content types must all agree.
"""
from __future__ import annotations

import hashlib
from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation


__all__ = [
    "APIContractChecker",
    "SchemaComparer",
]


def _vid(*parts: str) -> str:
    """Deterministic violation id."""
    raw = ":".join(parts)
    return "v-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Schema comparison
# ---------------------------------------------------------------------------

class SchemaComparer:
    """
    Compare server-side and client-side schemas field by field.

    Returns lists of diff dicts::

        {"field": str, "issue": str, "severity": str}
    """

    # Mapping of server types to compatible client / JS types
    _TYPE_COMPAT: dict[str, set[str]] = {
        "int": {"number", "integer", "int"},
        "integer": {"number", "integer", "int"},
        "float": {"number", "float", "double"},
        "double": {"number", "float", "double"},
        "str": {"string", "str", "text"},
        "string": {"string", "str", "text"},
        "text": {"string", "str", "text"},
        "bool": {"boolean", "bool"},
        "boolean": {"boolean", "bool"},
        "list": {"array", "list"},
        "array": {"array", "list"},
        "dict": {"object", "dict", "map"},
        "object": {"object", "dict", "map"},
        "datetime": {"string", "date", "datetime"},
        "date": {"string", "date", "datetime"},
        "null": {"null", "none", "undefined"},
        "none": {"null", "none", "undefined"},
    }

    def compare_schemas(
        self,
        server_schema: dict,
        client_schema: dict,
    ) -> list[dict]:
        """
        Field-by-field schema comparison.

        Parameters
        ----------
        server_schema : dict
            ``{"fields": [{"name": str, "type": str, "nullable": bool}]}``
        client_schema : dict
            ``{"fields": [{"name": str, "type": str, "handles_null": bool}]}``

        Returns
        -------
        list[dict]
            Each dict has ``field``, ``issue``, ``severity``.
        """
        diffs: list[dict] = []

        server_fields = {f["name"]: f for f in server_schema.get("fields", [])}
        client_fields = {f["name"]: f for f in client_schema.get("fields", [])}

        # Presence checks
        diffs.extend(self._check_field_presence(
            list(server_fields.keys()),
            list(client_fields.keys()),
        ))

        # Type and nullability checks for shared fields
        shared = set(server_fields) & set(client_fields)
        for name in sorted(shared):
            sf = server_fields[name]
            cf = client_fields[name]

            if not self._check_type_compatibility(
                sf.get("type", ""), cf.get("type", "")
            ):
                diffs.append({
                    "field": name,
                    "issue": (
                        f"type mismatch: server={sf.get('type', '?')} "
                        f"client={cf.get('type', '?')}"
                    ),
                    "severity": "error",
                })

            if not self._check_nullability(
                sf.get("nullable", False),
                cf.get("handles_null", False),
            ):
                diffs.append({
                    "field": name,
                    "issue": (
                        f"nullability mismatch: server nullable={sf.get('nullable', False)} "
                        f"but client handles_null={cf.get('handles_null', False)}"
                    ),
                    "severity": "warning",
                })

        return diffs

    def _check_field_presence(
        self,
        server_fields: list[str],
        client_fields: list[str],
    ) -> list[dict]:
        """Check fields present in one side but missing in the other."""
        diffs: list[dict] = []
        server_set = set(server_fields)
        client_set = set(client_fields)

        # Server has fields client doesn't access (informational)
        for f in sorted(server_set - client_set):
            diffs.append({
                "field": f,
                "issue": "server provides field but client does not access it",
                "severity": "info",
            })

        # Client accesses fields server doesn't provide (error)
        for f in sorted(client_set - server_set):
            diffs.append({
                "field": f,
                "issue": "client accesses field but server does not provide it",
                "severity": "error",
            })

        return diffs

    def _check_type_compatibility(
        self,
        server_type: str,
        client_type: str,
    ) -> bool:
        """
        Check if server and client types are compatible.

        Returns ``True`` if compatible (e.g. ``int`` and ``number``).
        """
        if not server_type or not client_type:
            return True  # Unknown types are assumed compatible
        s = server_type.lower().strip()
        c = client_type.lower().strip()
        if s == c:
            return True
        compatible = self._TYPE_COMPAT.get(s, set())
        return c in compatible

    def _check_nullability(
        self,
        server_nullable: bool,
        client_handles_null: bool,
    ) -> bool:
        """
        Check if client handles null when server can return null.

        Returns ``True`` if the combination is safe.
        """
        if not server_nullable:
            return True  # Server never returns null, no issue
        return client_handles_null


# ---------------------------------------------------------------------------
# API contract checker
# ---------------------------------------------------------------------------

class APIContractChecker:
    """
    Check the implicit API contract between Flask routes and JS fetch.

    Each method returns a list of ``OverlapViolation`` instances.
    """

    def __init__(self) -> None:
        self._comparer = SchemaComparer()

    # -- request schema ------------------------------------------------------

    def check_request_schema(
        self,
        route_definition: dict,
        js_fetch_call: dict,
    ) -> list[OverlapViolation]:
        """
        Check that JS fetch sends the expected request fields.

        Parameters
        ----------
        route_definition : dict
            ``{"pattern": str, "required_fields": [str],
              "optional_fields": [str]}``
        js_fetch_call : dict
            ``{"url": str, "method": str, "body_fields": [str],
              "expected_response_fields": [str]}``
        """
        violations: list[OverlapViolation] = []
        pattern = route_definition.get("pattern", "")
        required = set(route_definition.get("required_fields", []))
        sent = set(js_fetch_call.get("body_fields", []))

        missing = required - sent
        for fld in sorted(missing):
            violations.append(OverlapViolation(
                id=_vid("req_schema_missing", pattern, fld),
                condition_id=f"oc:api_request:{pattern}",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"Route '{pattern}' requires field '{fld}' but "
                    f"JS fetch to '{js_fetch_call.get('url', '?')}' does not send it"
                ),
                severity="error",
                left_detail=f"required: {sorted(required)}",
                right_detail=f"sent: {sorted(sent)}",
                repair_hint=f"Add '{fld}' to fetch body",
            ))

        # Extra fields not recognised by route (warning)
        optional = set(route_definition.get("optional_fields", []))
        known = required | optional
        extra = sent - known
        for fld in sorted(extra):
            violations.append(OverlapViolation(
                id=_vid("req_schema_extra", pattern, fld),
                condition_id=f"oc:api_request:{pattern}",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"JS fetch sends field '{fld}' but route '{pattern}' "
                    f"does not expect it"
                ),
                severity="warning",
                left_detail=f"route knows: {sorted(known)}",
                right_detail=f"fetch sends: {sorted(sent)}",
                repair_hint=f"Remove '{fld}' from fetch body or handle it in route",
            ))

        return violations

    # -- response schema -----------------------------------------------------

    def check_response_schema(
        self,
        route_return: dict,
        js_response_handling: dict,
    ) -> list[OverlapViolation]:
        """
        Check that server response fields match what JS expects.

        Parameters
        ----------
        route_return : dict
            ``{"fields": [str], "nullable_fields": [str],
              "status_codes": [int]}``
        js_response_handling : dict
            ``{"accessed_fields": [str], "null_checked_fields": [str],
              "handled_status_codes": [int]}``
        """
        violations: list[OverlapViolation] = []
        server_fields = set(route_return.get("fields", []))
        accessed = set(js_response_handling.get("accessed_fields", []))

        # Client accesses fields the server doesn't provide
        missing = accessed - server_fields
        for fld in sorted(missing):
            violations.append(OverlapViolation(
                id=_vid("resp_schema_missing", fld),
                condition_id="oc:api_response:fields",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"JS accesses response field '{fld}' but server does "
                    f"not provide it"
                ),
                severity="error",
                left_detail=f"server provides: {sorted(server_fields)}",
                right_detail=f"JS accesses: {sorted(accessed)}",
                repair_hint=f"Add '{fld}' to server response or remove JS access",
            ))

        # Nullable fields not null-checked by client
        nullable = set(route_return.get("nullable_fields", []))
        null_checked = set(js_response_handling.get("null_checked_fields", []))
        unchecked_nullable = (nullable & accessed) - null_checked
        for fld in sorted(unchecked_nullable):
            violations.append(OverlapViolation(
                id=_vid("resp_schema_null", fld),
                condition_id="oc:api_response:nullable",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"Server field '{fld}' is nullable but JS does not "
                    f"null-check it"
                ),
                severity="warning",
                left_detail=f"nullable: {sorted(nullable)}",
                right_detail=f"null-checked: {sorted(null_checked)}",
                repair_hint=f"Add null check for '{fld}' in JS",
            ))

        return violations

    # -- error codes ---------------------------------------------------------

    def check_error_codes(
        self,
        route_error_handlers: list[dict],
        js_error_handlers: list[dict],
    ) -> list[OverlapViolation]:
        """
        Check server error codes have client-side handling.

        Parameters
        ----------
        route_error_handlers : list[dict]
            ``[{"status_code": int, "file": str}, ...]``
        js_error_handlers : list[dict]
            ``[{"handles_status": [int], "file": str}, ...]``
        """
        violations: list[OverlapViolation] = []

        js_handled: set[int] = set()
        for jh in js_error_handlers:
            js_handled.update(jh.get("handles_status", []))

        for eh in route_error_handlers:
            code = eh["status_code"]
            if code not in js_handled:
                violations.append(OverlapViolation(
                    id=_vid("error_code", str(code)),
                    condition_id=f"oc:api_error:{code}",
                    kind=OverlapKind.ERROR_HANDLER_JS,
                    message=(
                        f"Server error handler for status {code} has no "
                        f"client-side handling"
                    ),
                    severity="warning",
                    left_detail=f"server handles {code}",
                    right_detail=f"JS handles: {sorted(js_handled)}",
                    repair_hint=f"Add handling for HTTP {code} in JS",
                    file_path=eh.get("file", ""),
                ))

        return violations

    # -- HTTP methods --------------------------------------------------------

    def check_http_methods(
        self,
        route_methods: set[str],
        fetch_method: str,
    ) -> list[OverlapViolation]:
        """
        Check HTTP method compatibility.

        Parameters
        ----------
        route_methods : set[str]
            e.g. ``{"GET", "POST"}``
        fetch_method : str
            e.g. ``"POST"``
        """
        violations: list[OverlapViolation] = []
        normalised = {m.upper() for m in route_methods}
        fm = fetch_method.upper()
        if fm not in normalised:
            violations.append(OverlapViolation(
                id=_vid("http_method", fm, ",".join(sorted(normalised))),
                condition_id="oc:api_method",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"JS uses HTTP {fm} but route only accepts "
                    f"{sorted(normalised)}"
                ),
                severity="error",
                left_detail=f"route methods: {sorted(normalised)}",
                right_detail=f"fetch method: {fm}",
                repair_hint=f"Add '{fm}' to route methods or change fetch method",
            ))
        return violations

    # -- content type --------------------------------------------------------

    def check_content_type(
        self,
        route_content_type: str,
        fetch_accept: str,
    ) -> list[OverlapViolation]:
        """
        Check content type compatibility.

        Parameters
        ----------
        route_content_type : str
            e.g. ``"application/json"``
        fetch_accept : str
            e.g. ``"application/json"``
        """
        violations: list[OverlapViolation] = []
        rct = route_content_type.lower().strip()
        fa = fetch_accept.lower().strip()

        if not rct or not fa:
            return violations  # Unknown — nothing to check

        # Accept wildcard
        if fa == "*/*" or rct == "*/*":
            return violations

        # Simple match: strip parameters
        rct_base = rct.split(";")[0].strip()
        fa_base = fa.split(";")[0].strip()

        if rct_base != fa_base:
            violations.append(OverlapViolation(
                id=_vid("content_type", rct, fa),
                condition_id="oc:api_content_type",
                kind=OverlapKind.ROUTE_JS_FETCH,
                message=(
                    f"Content type mismatch: server returns '{rct}' but "
                    f"JS expects '{fa}'"
                ),
                severity="error",
                left_detail=f"route content-type: {rct}",
                right_detail=f"fetch accept: {fa}",
                repair_hint=f"Align content types between server and client",
            ))

        return violations
