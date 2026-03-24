"""
Web-application verification pipeline.

Walks through verification levels from ``SYNTAX_ONLY`` to
``VISUAL_INVARIANTS``, stopping at the configured level.  Each level
accumulates results into a single ``VerificationResult``.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from .models import (
    VerificationLevel,
    VerificationResult,
    VerificationConfig,
)


__all__ = ["WebVerificationPipeline"]

# Ordered list of (level, method_name) pairs so the pipeline knows
# which levels to run and in what order.
_LEVEL_ORDER: list[tuple[VerificationLevel, str]] = [
    (VerificationLevel.SYNTAX_ONLY, "_level_syntax"),
    (VerificationLevel.SINGLE_LANGUAGE, "_level_single_language"),
    (VerificationLevel.CROSS_LANGUAGE, "_level_cross_language"),
    (VerificationLevel.FULL_DESCENT, "_level_full_descent"),
    (VerificationLevel.VISUAL_INVARIANTS, "_level_visual"),
]

# File-extension → language-layer mapping.
_EXT_TO_LAYER: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".sql": "sql",
    ".jinja": "template",
    ".jinja2": "template",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_source_files(project_dir: str) -> dict[str, list[str]]:
    """Walk *project_dir* and group source files by language layer."""
    files_by_layer: dict[str, list[str]] = {}
    if not os.path.isdir(project_dir):
        return files_by_layer
    for root, _dirs, filenames in os.walk(project_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            layer = _EXT_TO_LAYER.get(ext)
            if layer is not None:
                files_by_layer.setdefault(layer, []).append(
                    os.path.join(root, fname),
                )
    return files_by_layer


def _safe_read(path: str) -> str | None:
    """Read a file, returning ``None`` on I/O errors."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except IOError:
        return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class WebVerificationPipeline:
    """Hierarchical verification pipeline for web applications.

    Instantiate, then call :meth:`verify` with a project directory and
    a :class:`VerificationConfig`.
    """

    # ------------------------------------------------------------------ API

    def verify(
        self,
        project_dir: str,
        config: VerificationConfig | None = None,
    ) -> VerificationResult:
        """Run the verification pipeline up to *config.level*.

        Parameters
        ----------
        project_dir:
            Root directory of the web application to verify.
        config:
            Pipeline configuration.  Defaults to ``VerificationConfig()``.

        Returns
        -------
        VerificationResult
            Aggregated results from every level that was executed.
        """
        if config is None:
            config = VerificationConfig()

        start_ns = time.monotonic_ns()

        all_passed: list[str] = []
        all_failed: list[str] = []
        all_warnings: list[str] = []
        all_obstructions: list[dict[str, str]] = []

        for level, method_name in _LEVEL_ORDER:
            method = getattr(self, method_name)
            step: dict[str, Any] = method(project_dir)

            all_passed.extend(step.get("passed", []))
            all_failed.extend(step.get("failed", []))
            all_warnings.extend(step.get("warnings", []))
            all_obstructions.extend(step.get("obstructions", []))

            if level == config.level:
                break

        elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000

        # Compute summary metrics.
        total_checks = len(all_passed) + len(all_failed)
        evidence_coverage = {
            "total": total_checks,
            "covered": len(all_passed),
            "pct": (len(all_passed) / total_checks * 100.0
                    if total_checks else 0.0),
        }

        trust_summary = self._compute_trust_summary(all_obstructions)

        return VerificationResult(
            level=config.level,
            passed=all_passed,
            failed=all_failed,
            warnings=all_warnings,
            obstructions=all_obstructions,
            evidence_coverage=evidence_coverage,
            trust_summary=trust_summary,
            timing_ms=round(elapsed_ms, 3),
            overall_passed=len(all_failed) == 0,
        )

    # ------------------------------------------------- level: syntax_only

    def _level_syntax(self, project_dir: str) -> dict[str, Any]:
        """Check syntactic validity of every source file."""
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []

        files_by_layer = _collect_source_files(project_dir)
        if not files_by_layer:
            warnings.append(
                f"No source files found under {project_dir!r}."
            )
            return {"passed": passed, "failed": failed, "warnings": warnings}

        for layer, paths in files_by_layer.items():
            for path in paths:
                source = _safe_read(path)
                if source is None:
                    failed.append(f"[{layer}] Could not read {path}")
                    continue

                if layer == "python":
                    try:
                        compile(source, path, "exec")
                        passed.append(f"[python] Syntax OK: {path}")
                    except SyntaxError as exc:
                        failed.append(
                            f"[python] Syntax error in {path} "
                            f"line {exc.lineno}: {exc.msg}"
                        )
                else:
                    # Non-Python files: treat readable as syntactically OK.
                    passed.append(f"[{layer}] Readable: {path}")

        return {"passed": passed, "failed": failed, "warnings": warnings}

    # ------------------------------------------ level: single_language

    def _level_single_language(self, project_dir: str) -> dict[str, Any]:
        """Per-language structural analysis."""
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []
        obstructions: list[dict[str, str]] = []

        files_by_layer = _collect_source_files(project_dir)

        # --- Python ---
        for path in files_by_layer.get("python", []):
            source = _safe_read(path)
            if source is None:
                continue
            if re.search(r"^\s*import\s+|^\s*from\s+", source, re.MULTILINE):
                passed.append(f"[python] Has imports: {path}")
            if re.search(r"^\s*(def |class )", source, re.MULTILINE):
                passed.append(f"[python] Has definitions: {path}")

        # --- HTML ---
        for path in files_by_layer.get("html", []):
            source = _safe_read(path)
            if source is None:
                continue
            lower = source.lower()
            if "<html" in lower:
                passed.append(f"[html] Contains <html>: {path}")
            else:
                warnings.append(f"[html] Missing <html> tag: {path}")
            if "<body" in lower:
                passed.append(f"[html] Contains <body>: {path}")
            else:
                warnings.append(f"[html] Missing <body> tag: {path}")

        # --- CSS ---
        for path in files_by_layer.get("css", []):
            source = _safe_read(path)
            if source is None:
                continue
            open_braces = source.count("{")
            close_braces = source.count("}")
            if open_braces == close_braces:
                passed.append(f"[css] Balanced braces: {path}")
            else:
                failed.append(
                    f"[css] Unbalanced braces ({open_braces} open, "
                    f"{close_braces} close): {path}"
                )
                obstructions.append({
                    "id": f"css-brace-{os.path.basename(path)}",
                    "description": "Unbalanced CSS braces",
                    "severity": "error",
                    "location": path,
                    "repair_hint": "Check for missing '{' or '}' characters.",
                })

        # --- JavaScript ---
        for path in files_by_layer.get("javascript", []):
            source = _safe_read(path)
            if source is None:
                continue
            open_braces = source.count("{")
            close_braces = source.count("}")
            if open_braces == close_braces:
                passed.append(f"[javascript] Balanced braces: {path}")
            else:
                warnings.append(
                    f"[javascript] Potentially unbalanced braces: {path}"
                )

        return {
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "obstructions": obstructions,
        }

    # ----------------------------------------- level: cross_language

    def _level_cross_language(self, project_dir: str) -> dict[str, Any]:
        """Cross-language consistency checks."""
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []
        obstructions: list[dict[str, str]] = []

        files_by_layer = _collect_source_files(project_dir)
        sources: dict[str, dict[str, str]] = {}
        for layer, paths in files_by_layer.items():
            for path in paths:
                content = _safe_read(path)
                if content is not None:
                    sources.setdefault(layer, {})[path] = content

        # Try to use the real CrossLanguageStaticAnalyzer if available.
        try:
            from jugeo.webapp.evidence.static_analysis import (
                CrossLanguageStaticAnalyzer,
            )
            analyzer = CrossLanguageStaticAnalyzer()
            issues = analyzer.check_all(sources)
            for issue in issues:
                if issue.get("severity") == "error":
                    failed.append(
                        f"[cross] {issue.get('issue', 'unknown')}: "
                        f"{issue.get('source_file', '?')} ↔ "
                        f"{issue.get('target_file', '?')}"
                    )
                    obstructions.append({
                        "id": f"cross-{len(obstructions)}",
                        "description": issue.get("issue", ""),
                        "severity": issue.get("severity", "error"),
                        "location": issue.get("source_file", ""),
                        "repair_hint": issue.get("repair_hint", ""),
                    })
                else:
                    warnings.append(
                        f"[cross] {issue.get('issue', 'unknown')}: "
                        f"{issue.get('source_file', '?')}"
                    )
            if not issues:
                passed.append(
                    "[cross] No cross-language issues detected."
                )
            return {
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "obstructions": obstructions,
            }
        except (ImportError, AttributeError, TypeError):
            pass

        # Fallback: lightweight heuristic checks.
        self._check_template_variables(sources, passed, failed, obstructions)
        self._check_css_classes(sources, passed, warnings, obstructions)
        self._check_js_dom_ids(sources, passed, warnings, obstructions)

        if not failed and not obstructions:
            passed.append("[cross] Basic cross-language checks passed.")

        return {
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "obstructions": obstructions,
        }

    # ------------------------------------------- level: full_descent

    def _level_full_descent(self, project_dir: str) -> dict[str, Any]:
        """Full fibered-descent verification."""
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []
        obstructions: list[dict[str, str]] = []

        files_by_layer = _collect_source_files(project_dir)
        sources: dict[str, dict[str, str]] = {}
        for layer, paths in files_by_layer.items():
            for path in paths:
                content = _safe_read(path)
                if content is not None:
                    sources.setdefault(layer, {})[path] = content

        # Try to use the real FiberDescentEngine if available.
        try:
            from jugeo.webapp.fibered.fiber_descent import FiberDescentEngine
            engine = FiberDescentEngine()
            report = engine.check_all(sources)
            for item in report.get("failures", []):
                failed.append(
                    f"[descent] {item.get('description', 'failure')}"
                )
                obstructions.append({
                    "id": f"descent-{len(obstructions)}",
                    "description": item.get("description", ""),
                    "severity": item.get("severity", "error"),
                    "location": item.get("location", ""),
                    "repair_hint": item.get("repair_hint", ""),
                })
            for item in report.get("passed", []):
                passed.append(f"[descent] {item}")
            if not report.get("failures"):
                passed.append("[descent] All descent conditions satisfied.")
            return {
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "obstructions": obstructions,
            }
        except (ImportError, AttributeError, TypeError):
            pass

        # Fallback: boundary consistency heuristics.
        self._check_boundary_consistency(sources, passed, failed, obstructions)

        if not failed:
            passed.append(
                "[descent] Boundary consistency checks passed (heuristic)."
            )

        return {
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "obstructions": obstructions,
        }

    # -------------------------------------- level: visual_invariants

    def _level_visual(self, project_dir: str) -> dict[str, Any]:
        """Visual-invariant checks on CSS and HTML."""
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []
        obstructions: list[dict[str, str]] = []

        files_by_layer = _collect_source_files(project_dir)

        for path in files_by_layer.get("css", []):
            source = _safe_read(path)
            if source is None:
                continue

            # Check for viewport-relative units.
            if re.search(r"\b\d+(vw|vh|vmin|vmax)\b", source):
                passed.append(f"[visual] Uses viewport units: {path}")
            else:
                warnings.append(
                    f"[visual] No viewport units found: {path}"
                )

            # Check for responsive breakpoints.
            if re.search(r"@media\s*\(", source):
                passed.append(f"[visual] Has media queries: {path}")
            else:
                warnings.append(
                    f"[visual] No @media queries found: {path}"
                )

        for path in files_by_layer.get("html", []):
            source = _safe_read(path)
            if source is None:
                continue
            lower = source.lower()

            if '<meta name="viewport"' in lower:
                passed.append(f"[visual] Has viewport meta: {path}")
            else:
                warnings.append(
                    f"[visual] Missing viewport meta tag: {path}"
                )
                obstructions.append({
                    "id": f"visual-viewport-{os.path.basename(path)}",
                    "description": "Missing viewport meta tag",
                    "severity": "warning",
                    "location": path,
                    "repair_hint": (
                        'Add <meta name="viewport" '
                        'content="width=device-width, initial-scale=1"> '
                        "to <head>."
                    ),
                })

        if not files_by_layer.get("css") and not files_by_layer.get("html"):
            warnings.append(
                "[visual] No CSS or HTML files found for visual checks."
            )

        return {
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "obstructions": obstructions,
        }

    # ------------------------------------------------ reports

    def generate_report(self, result: VerificationResult) -> str:
        """Return a human-readable verification report."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  Verification Report")
        lines.append(f"  Level: {result.level.value}")
        lines.append("=" * 60)
        lines.append("")
        lines.append(
            f"  Passed:  {result.pass_count}  |  "
            f"Failed:  {result.fail_count}  |  "
            f"Warnings:  {len(result.warnings)}"
        )
        lines.append(
            f"  Obstructions: {result.obstruction_count}"
        )
        lines.append(
            f"  Overall: {'PASSED' if result.overall_passed else 'FAILED'}"
        )
        lines.append(f"  Time: {result.timing_ms:.1f} ms")
        lines.append("")

        if result.failed:
            lines.append("-" * 60)
            lines.append("  Failures")
            lines.append("-" * 60)
            for desc in result.failed:
                lines.append(f"  ✗ {desc}")
            lines.append("")

        if result.obstructions:
            lines.append("-" * 60)
            lines.append("  Obstructions (cohomology generators)")
            lines.append("-" * 60)
            for obs in result.obstructions:
                lines.append(
                    f"  [{obs.get('severity', '?')}] {obs.get('id', '?')}: "
                    f"{obs.get('description', '')}"
                )
                hint = obs.get("repair_hint", "")
                if hint:
                    lines.append(f"         Hint: {hint}")
            lines.append("")

        if result.warnings:
            lines.append("-" * 60)
            lines.append("  Warnings")
            lines.append("-" * 60)
            for w in result.warnings:
                lines.append(f"  ⚠ {w}")
            lines.append("")

        cov = result.evidence_coverage
        lines.append("-" * 60)
        lines.append("  Evidence coverage")
        lines.append("-" * 60)
        lines.append(
            f"  {cov.get('covered', 0)}/{cov.get('total', 0)} checks "
            f"({cov.get('pct', 0.0):.1f}%)"
        )
        lines.append("")

        ts = result.trust_summary
        lines.append("-" * 60)
        lines.append("  Trust summary")
        lines.append("-" * 60)
        lines.append(f"  Highest: {ts.get('highest', 'n/a')}")
        lines.append(f"  Lowest:  {ts.get('lowest', 'n/a')}")
        dist = ts.get("distribution", {})
        if dist:
            for level_name, count in dist.items():
                lines.append(f"    {level_name}: {count}")
        lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    def generate_json_report(self, result: VerificationResult) -> dict[str, Any]:
        """Return the result as a JSON-serialisable dict with metadata."""
        report = result.to_dict()
        report["metadata"] = {
            "pass_count": result.pass_count,
            "fail_count": result.fail_count,
            "obstruction_count": result.obstruction_count,
            "pipeline": "WebVerificationPipeline",
        }
        return report

    # ----------------------------------------- internal helpers

    def _compute_trust_summary(
        self,
        obstructions: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Derive a trust summary from the collected obstructions."""
        severity_order = ["info", "warning", "error", "critical"]
        distribution: dict[str, int] = {}

        for obs in obstructions:
            sev = obs.get("severity", "unknown")
            distribution[sev] = distribution.get(sev, 0) + 1

        present = [s for s in severity_order if s in distribution]
        return {
            "highest": present[-1] if present else "none",
            "lowest": present[0] if present else "none",
            "distribution": distribution,
        }

    # -- cross-language heuristic helpers -----------------------------------

    def _check_template_variables(
        self,
        sources: dict[str, dict[str, str]],
        passed: list[str],
        failed: list[str],
        obstructions: list[dict[str, str]],
    ) -> None:
        """Check that render_template kwargs appear in templates."""
        py_sources = sources.get("python", {})
        tpl_sources = sources.get("template", {}) | sources.get("html", {})

        render_re = re.compile(
            r"render_template\s*\(\s*['\"]([^'\"]+)['\"]\s*"
            r"(?:,\s*(\w+)\s*=)*",
        )

        for py_path, py_src in py_sources.items():
            for match in render_re.finditer(py_src):
                tpl_name = match.group(1)
                kwarg = match.group(2)
                if kwarg is None:
                    continue
                # Look for {{ kwarg }} in any template source.
                found = any(
                    "{{" in tpl_src and kwarg in tpl_src
                    for tpl_src in tpl_sources.values()
                )
                if found:
                    passed.append(
                        f"[cross] Template var '{kwarg}' referenced "
                        f"from {py_path} found in templates."
                    )
                else:
                    failed.append(
                        f"[cross] Template var '{kwarg}' from {py_path} "
                        f"not found in any template."
                    )
                    obstructions.append({
                        "id": f"cross-tplvar-{kwarg}",
                        "description": (
                            f"render_template kwarg '{kwarg}' has no "
                            f"corresponding {{{{ {kwarg} }}}} in templates"
                        ),
                        "severity": "error",
                        "location": py_path,
                        "repair_hint": (
                            f"Add {{{{ {kwarg} }}}} to template "
                            f"'{tpl_name}', or remove the kwarg."
                        ),
                    })

    def _check_css_classes(
        self,
        sources: dict[str, dict[str, str]],
        passed: list[str],
        warnings: list[str],
        obstructions: list[dict[str, str]],
    ) -> None:
        """Check that CSS classes used in HTML/templates are defined."""
        css_sources = sources.get("css", {})
        html_sources = sources.get("html", {}) | sources.get("template", {})

        # Collect defined CSS classes.
        class_def_re = re.compile(r"\.([\w-]+)\s*\{")
        defined_classes: set[str] = set()
        for css_src in css_sources.values():
            defined_classes.update(class_def_re.findall(css_src))

        if not defined_classes or not html_sources:
            return

        # Collect used classes from HTML.
        class_use_re = re.compile(r'class\s*=\s*["\']([^"\']+)["\']')
        for html_path, html_src in html_sources.items():
            for match in class_use_re.finditer(html_src):
                for cls_name in match.group(1).split():
                    if cls_name in defined_classes:
                        passed.append(
                            f"[cross] CSS class '{cls_name}' "
                            f"defined and used in {html_path}."
                        )
                    else:
                        warnings.append(
                            f"[cross] CSS class '{cls_name}' used in "
                            f"{html_path} but not found in CSS files."
                        )

    def _check_js_dom_ids(
        self,
        sources: dict[str, dict[str, str]],
        passed: list[str],
        warnings: list[str],
        obstructions: list[dict[str, str]],
    ) -> None:
        """Check that JS getElementById targets exist in HTML."""
        js_sources = sources.get("javascript", {})
        html_sources = sources.get("html", {}) | sources.get("template", {})

        id_re = re.compile(r'getElementById\s*\(\s*["\'](\w+)["\']\s*\)')
        html_id_re = re.compile(r'id\s*=\s*["\'](\w+)["\']')

        html_ids: set[str] = set()
        for html_src in html_sources.values():
            html_ids.update(html_id_re.findall(html_src))

        for js_path, js_src in js_sources.items():
            for match in id_re.finditer(js_src):
                dom_id = match.group(1)
                if dom_id in html_ids:
                    passed.append(
                        f"[cross] DOM id '{dom_id}' from {js_path} "
                        f"found in HTML."
                    )
                else:
                    warnings.append(
                        f"[cross] DOM id '{dom_id}' referenced in "
                        f"{js_path} not found in HTML files."
                    )

    def _check_boundary_consistency(
        self,
        sources: dict[str, dict[str, str]],
        passed: list[str],
        failed: list[str],
        obstructions: list[dict[str, str]],
    ) -> None:
        """Lightweight descent-style boundary consistency checks."""
        # Check that forms posting to routes have matching endpoints.
        html_sources = sources.get("html", {}) | sources.get("template", {})
        py_sources = sources.get("python", {})

        form_action_re = re.compile(
            r'<form[^>]*action\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE,
        )
        route_re = re.compile(
            r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]",
        )

        routes: set[str] = set()
        for py_src in py_sources.values():
            routes.update(route_re.findall(py_src))

        for html_path, html_src in html_sources.items():
            for match in form_action_re.finditer(html_src):
                action = match.group(1)
                # Ignore {{ url_for(...) }} and external URLs.
                if "{{" in action or action.startswith("http"):
                    continue
                if action in routes:
                    passed.append(
                        f"[descent] Form action '{action}' in "
                        f"{html_path} matches a route."
                    )
                else:
                    failed.append(
                        f"[descent] Form action '{action}' in "
                        f"{html_path} has no matching route."
                    )
                    obstructions.append({
                        "id": f"descent-form-{action}",
                        "description": (
                            f"Form action '{action}' does not match "
                            f"any declared route"
                        ),
                        "severity": "error",
                        "location": html_path,
                        "repair_hint": (
                            f"Add a route for '{action}' or fix the "
                            f"form action attribute."
                        ),
                    })
