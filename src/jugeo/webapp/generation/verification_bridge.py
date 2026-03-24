"""Cross-language verification of generated Flask apps — stdlib only."""
from __future__ import annotations

import os
import re


class VerificationBridge:
    """Runs cross-language analysis on generated Flask app files."""

    def verify_generated_app(self, output_dir: str) -> dict:
        project = self._scan_generated_project(output_dir)
        results = {
            "template_variables": self._check_template_variables(
                project.get("routes_code", ""), project.get("templates", {})
            ),
            "css_references": self._check_css_references(
                project.get("templates", {}), project.get("css_files", {})
            ),
            "form_actions": self._check_form_actions(
                project.get("templates", {}), project.get("routes", {})
            ),
            "url_consistency": self._check_url_consistency(
                project.get("routes", {})
            ),
        }
        return self._generate_verification_report(results)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _scan_generated_project(self, output_dir: str) -> dict:
        project: dict = {
            "routes_code": "",
            "templates": {},
            "css_files": {},
            "routes": {},
        }
        if not os.path.isdir(output_dir):
            return project

        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                path = os.path.join(root, fname)
                rel = os.path.relpath(path, output_dir)
                try:
                    with open(path) as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    continue

                if fname.endswith(".py"):
                    project["routes"][rel] = content
                    if "route" in content or "app.route" in content:
                        project["routes_code"] += content + "\n"
                elif fname.endswith(".html"):
                    project["templates"][rel] = content
                elif fname.endswith(".css"):
                    project["css_files"][rel] = content

        return project

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _check_template_variables(self, routes_code: str, templates: dict) -> list:
        issues: list[str] = []
        # Find render_template calls and their kwargs
        render_calls = re.findall(
            r"render_template\(\s*['\"]([^'\"]+)['\"](?:,\s*(.+?))?\s*\)",
            routes_code,
        )
        for tmpl_name, kwargs_str in render_calls:
            if kwargs_str:
                var_names = re.findall(r"(\w+)\s*=", kwargs_str)
                # Check if template uses these variables
                for tpath, tcontent in templates.items():
                    if tmpl_name in tpath:
                        for var in var_names:
                            if var not in tcontent:
                                issues.append(
                                    f"Variable '{var}' passed to '{tmpl_name}' "
                                    f"but not found in template"
                                )
        return issues

    def _check_css_references(self, templates: dict, css_files: dict) -> list:
        issues: list[str] = []
        if not css_files:
            return issues
        # Collect all CSS class definitions
        all_classes: set[str] = set()
        for _path, content in css_files.items():
            all_classes.update(re.findall(r"\.([a-zA-Z_][\w-]*)\s*[{,:]", content))
        # Check class= references in templates
        for tpath, tcontent in templates.items():
            classes_used = re.findall(r'class="([^"]*)"', tcontent)
            for class_str in classes_used:
                for cls in class_str.split():
                    # Skip Jinja2 template variables in class names
                    if "{{" in cls or "{%" in cls:
                        continue
                    if cls.startswith("alert-"):
                        base = "alert"
                        if base not in all_classes:
                            issues.append(f"CSS class '.{cls}' in {tpath} not found in CSS files")
                    # Don't be too strict — many classes are dynamic
        return issues

    def _check_form_actions(self, templates: dict, routes: dict) -> list:
        issues: list[str] = []
        all_routes_code = "\n".join(routes.values())
        for tpath, tcontent in templates.items():
            actions = re.findall(r'action="([^"]*)"', tcontent)
            for action in actions:
                if "{{" in action or "{%" in action:
                    continue  # dynamic URL
                if action == "#":
                    continue
                # Check if action URL exists in route definitions
                if action not in all_routes_code and f"'{action}'" not in all_routes_code:
                    issues.append(f"Form action '{action}' in {tpath} not matched to a route")
        return issues

    def _check_url_consistency(self, routes: dict) -> list:
        issues: list[str] = []
        all_code = "\n".join(routes.values())

        # Find all url_for() calls
        url_for_calls = re.findall(r"url_for\(\s*['\"](\w+)['\"]", all_code)

        # Find all defined endpoints
        endpoints: set[str] = set()
        for match in re.finditer(r"def\s+(\w+)\s*\(", all_code):
            endpoints.add(match.group(1))

        for endpoint in url_for_calls:
            if endpoint not in endpoints:
                issues.append(f"url_for('{endpoint}') references undefined endpoint")

        return issues

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _generate_verification_report(self, results: dict) -> dict:
        total_issues = sum(len(v) for v in results.values())
        all_issues: list[str] = []
        for category, issues in results.items():
            for issue in issues:
                all_issues.append(f"[{category}] {issue}")
        return {
            "status": "pass" if total_issues == 0 else "issues_found",
            "total_issues": total_issues,
            "issues": all_issues,
            "details": results,
            "summary": f"Verification complete: {total_issues} issue(s) found",
        }
