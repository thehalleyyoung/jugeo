"""Security scanner for web application sources.

Performs regex-based detection of common vulnerability patterns
including XSS, CSRF, SQL injection, open redirects, secret exposure,
and authentication bypass.
"""
from __future__ import annotations

import re
from enum import Enum


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------

class SecuritySeverity(str, Enum):
    """Severity levels for security findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class WebSecurityScanner:
    """Scan web application sources for common security vulnerabilities.

    Every ``scan_*`` method returns ``list[dict]`` where each dict has
    keys: *issue*, *severity*, *file*, *line*, *code_snippet*,
    *repair_hint*, *cwe*.
    """

    # ------------------------------------------------------------------
    # XSS
    # ------------------------------------------------------------------

    def scan_xss(self, template_sources: dict[str, str]) -> list[dict]:
        """Detect potential XSS via Jinja2 ``|safe`` filter usage."""
        findings: list[dict] = []

        for filename, source in template_sources.items():
            for line_no, line in enumerate(source.splitlines(), start=1):
                # |safe without upstream sanitization.
                if re.search(r"\|\s*safe\b", line):
                    # Check whether the variable has been previously escaped.
                    has_escape = re.search(r"\|\s*e(?:scape)?\s*\|", line)
                    if not has_escape:
                        findings.append({
                            "issue": (
                                f"Potential XSS: '|safe' filter used without "
                                f"prior escaping in {filename} at line {line_no}"
                            ),
                            "severity": SecuritySeverity.HIGH.value,
                            "file": filename,
                            "line": line_no,
                            "code_snippet": line.strip(),
                            "repair_hint": (
                                "Remove the |safe filter, or ensure the "
                                "variable is sanitised before rendering."
                            ),
                            "cwe": "CWE-79",
                        })

                # Explicit {{ var | safe }} pattern.
                for m in re.finditer(
                    r"\{\{\s*\w+(?:\.\w+)*\s*\|\s*safe\s*\}\}", line
                ):
                    # Already captured above, but tag it specifically.
                    pass

        return findings

    # ------------------------------------------------------------------
    # CSRF
    # ------------------------------------------------------------------

    def scan_csrf(
        self,
        route_sources: dict[str, str],
        template_sources: dict[str, str],
    ) -> list[dict]:
        """Detect POST routes and forms lacking CSRF protection."""
        findings: list[dict] = []

        # Routes accepting POST without CSRF import / decorator.
        for filename, source in route_sources.items():
            has_csrf = bool(re.search(r"csrf|CSRFProtect|csrf_token", source))
            for m in re.finditer(
                r"@\w+\.route\([^)]*methods\s*=\s*\[[^\]]*['\"]POST['\"][^\]]*\]",
                source,
            ):
                if not has_csrf:
                    line_no = source[: m.start()].count("\n") + 1
                    findings.append({
                        "issue": (
                            f"POST route without CSRF protection in "
                            f"{filename} at line {line_no}"
                        ),
                        "severity": SecuritySeverity.HIGH.value,
                        "file": filename,
                        "line": line_no,
                        "code_snippet": m.group(0),
                        "repair_hint": (
                            "Add Flask-WTF CSRFProtect or include a "
                            "csrf_token() hidden field in the form."
                        ),
                        "cwe": "CWE-352",
                    })

        # Forms without csrf_token field.
        for filename, source in template_sources.items():
            form_regions = list(re.finditer(
                r"<form\b[^>]*>(.+?)</form>", source, re.DOTALL | re.IGNORECASE
            ))
            for fm in form_regions:
                form_body = fm.group(1)
                if "csrf_token" not in form_body and "csrf" not in form_body.lower():
                    line_no = source[: fm.start()].count("\n") + 1
                    findings.append({
                        "issue": (
                            f"Form without csrf_token in {filename} "
                            f"at line {line_no}"
                        ),
                        "severity": SecuritySeverity.HIGH.value,
                        "file": filename,
                        "line": line_no,
                        "code_snippet": source.splitlines()[line_no - 1].strip()
                        if line_no <= len(source.splitlines()) else "",
                        "repair_hint": (
                            "Add {{ csrf_token() }} or "
                            "<input type='hidden' name='csrf_token' "
                            "value='{{ csrf_token() }}'> inside the form."
                        ),
                        "cwe": "CWE-352",
                    })

        return findings

    # ------------------------------------------------------------------
    # SQL injection
    # ------------------------------------------------------------------

    def scan_sql_injection(self, py_sources: dict[str, str]) -> list[dict]:
        """Detect potential SQL injection via string formatting."""
        findings: list[dict] = []

        patterns = [
            (r'f["\'](?:SELECT|INSERT|UPDATE|DELETE)', "f-string SQL query"),
            (r'["\']SELECT\s.+%s', "%-format SQL query"),
            (r'["\']SELECT\s.+\.format\(', ".format() SQL query"),
            (r'["\']INSERT\s.+%s', "%-format SQL INSERT"),
            (r'["\']INSERT\s.+\.format\(', ".format() SQL INSERT"),
            (r'["\']UPDATE\s.+%s', "%-format SQL UPDATE"),
            (r'["\']UPDATE\s.+\.format\(', ".format() SQL UPDATE"),
            (r'["\']DELETE\s.+%s', "%-format SQL DELETE"),
            (r'["\']DELETE\s.+\.format\(', ".format() SQL DELETE"),
        ]

        for filename, source in py_sources.items():
            for line_no, line in enumerate(source.splitlines(), start=1):
                for pat, desc in patterns:
                    if re.search(pat, line, re.IGNORECASE):
                        findings.append({
                            "issue": (
                                f"Potential SQL injection ({desc}) in "
                                f"{filename} at line {line_no}"
                            ),
                            "severity": SecuritySeverity.CRITICAL.value,
                            "file": filename,
                            "line": line_no,
                            "code_snippet": line.strip(),
                            "repair_hint": (
                                "Use parameterised queries (e.g. "
                                "cursor.execute('SELECT ... WHERE id = ?', (id,))) "
                                "instead of string interpolation."
                            ),
                            "cwe": "CWE-89",
                        })
                        break  # one finding per line

        return findings

    # ------------------------------------------------------------------
    # Open redirect
    # ------------------------------------------------------------------

    def scan_open_redirect(self, route_sources: dict[str, str]) -> list[dict]:
        """Detect redirects using unvalidated user input."""
        findings: list[dict] = []

        patterns = [
            r"redirect\(\s*request\.args",
            r"redirect\(\s*request\.form",
            r"redirect\(\s*request\.values",
        ]

        for filename, source in route_sources.items():
            for line_no, line in enumerate(source.splitlines(), start=1):
                for pat in patterns:
                    if re.search(pat, line):
                        findings.append({
                            "issue": (
                                f"Potential open redirect using unvalidated "
                                f"user input in {filename} at line {line_no}"
                            ),
                            "severity": SecuritySeverity.MEDIUM.value,
                            "file": filename,
                            "line": line_no,
                            "code_snippet": line.strip(),
                            "repair_hint": (
                                "Validate the redirect target against an "
                                "allow-list of internal URLs, or use "
                                "url_for() with a known endpoint name."
                            ),
                            "cwe": "CWE-601",
                        })
                        break

        return findings

    # ------------------------------------------------------------------
    # Secret / credential exposure
    # ------------------------------------------------------------------

    def scan_secret_exposure(self, all_sources: dict[str, str]) -> list[dict]:
        """Detect hard-coded secrets and credentials."""
        findings: list[dict] = []

        patterns = [
            (
                r"SECRET_KEY\s*=\s*['\"][^'\"]{8,}['\"]",
                "hard-coded SECRET_KEY",
            ),
            (
                r"(?i)password\s*=\s*['\"][^'\"]+['\"]",
                "hard-coded password",
            ),
            (
                r"(?i)api_key\s*=\s*['\"][^'\"]{8,}['\"]",
                "hard-coded API key",
            ),
            (
                r"(?i)secret\s*=\s*['\"][^'\"]{8,}['\"]",
                "hard-coded secret",
            ),
            (
                r"(?i)token\s*=\s*['\"][A-Za-z0-9+/=]{20,}['\"]",
                "hard-coded token",
            ),
            (
                r"(?i)aws_access_key_id\s*=\s*['\"]AKIA[A-Z0-9]{16}['\"]",
                "hard-coded AWS access key",
            ),
        ]

        for filename, source in all_sources.items():
            for line_no, line in enumerate(source.splitlines(), start=1):
                # Skip comments and obvious non-production lines.
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                # Skip lines that load from environment.
                if "os.environ" in line or "os.getenv" in line:
                    continue

                for pat, desc in patterns:
                    if re.search(pat, line):
                        findings.append({
                            "issue": (
                                f"Potential {desc} in "
                                f"{filename} at line {line_no}"
                            ),
                            "severity": SecuritySeverity.CRITICAL.value,
                            "file": filename,
                            "line": line_no,
                            "code_snippet": _redact_secrets(line.strip()),
                            "repair_hint": (
                                "Move secrets to environment variables or a "
                                "secrets manager.  Never hard-code credentials."
                            ),
                            "cwe": "CWE-798",
                        })
                        break

        return findings

    # ------------------------------------------------------------------
    # Authentication bypass
    # ------------------------------------------------------------------

    def scan_auth_bypass(self, route_sources: dict[str, str]) -> list[dict]:
        """Detect admin/dashboard routes without authentication checks."""
        findings: list[dict] = []

        sensitive_patterns = [
            r"/admin",
            r"/dashboard",
            r"/settings",
            r"/manage",
            r"/users",
            r"/config",
        ]

        auth_indicators = {
            "login_required",
            "auth_required",
            "requires_auth",
            "permission_required",
            "roles_required",
            "roles_accepted",
            "jwt_required",
            "token_required",
            "current_user",
        }

        for filename, source in route_sources.items():
            lines = source.splitlines()
            for i, line in enumerate(lines):
                route_match = re.search(
                    r"@\w+\.route\(\s*['\"]([^'\"]+)['\"]", line
                )
                if not route_match:
                    continue

                route_path = route_match.group(1)
                is_sensitive = any(
                    re.search(pat, route_path) for pat in sensitive_patterns
                )
                if not is_sensitive:
                    continue

                # Look in the 5 lines preceding the route for auth decorators.
                context_start = max(0, i - 5)
                context_block = "\n".join(lines[context_start: i + 1])

                has_auth = any(
                    indicator in context_block for indicator in auth_indicators
                )

                # Also check the function body (next 10 lines).
                body_end = min(len(lines), i + 12)
                body_block = "\n".join(lines[i: body_end])
                has_auth = has_auth or any(
                    indicator in body_block for indicator in auth_indicators
                )

                if not has_auth:
                    findings.append({
                        "issue": (
                            f"Sensitive route '{route_path}' in {filename} "
                            f"at line {i + 1} lacks authentication check"
                        ),
                        "severity": SecuritySeverity.HIGH.value,
                        "file": filename,
                        "line": i + 1,
                        "code_snippet": line.strip(),
                        "repair_hint": (
                            "Add @login_required or an equivalent auth "
                            "decorator to protect this route."
                        ),
                        "cwe": "CWE-306",
                    })

        return findings

    # ------------------------------------------------------------------
    # Aggregate scan
    # ------------------------------------------------------------------

    def scan_all(self, project_sources: dict[str, dict]) -> list[dict]:
        """Run all security scans and return combined findings.

        *project_sources* is expected to contain:

        * ``templates`` – ``dict[str, str]``
        * ``routes``    – ``dict[str, str]``
        * ``python``    – ``dict[str, str]``
        * ``all``       – ``dict[str, str]``
        """
        templates = project_sources.get("templates", {})
        routes = project_sources.get("routes", {})
        python_files = project_sources.get("python", {})
        all_files = project_sources.get("all", {})

        results: list[dict] = []
        results.extend(self.scan_xss(templates))
        results.extend(self.scan_csrf(routes, templates))
        results.extend(self.scan_sql_injection(python_files))
        results.extend(self.scan_open_redirect(routes))
        results.extend(self.scan_secret_exposure(all_files))
        results.extend(self.scan_auth_bypass(routes))
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _redact_secrets(snippet: str) -> str:
    """Replace the middle of quoted strings with asterisks for safe logging."""
    def _mask(m: re.Match) -> str:
        quote = m.group(0)[0]
        inner = m.group(0)[1:-1]
        if len(inner) <= 4:
            return m.group(0)
        return quote + inner[:2] + "*" * (len(inner) - 4) + inner[-2:] + quote

    return re.sub(r"['\"][^'\"]{5,}['\"]", _mask, snippet)
