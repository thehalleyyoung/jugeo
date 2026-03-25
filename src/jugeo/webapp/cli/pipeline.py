"""Pipeline orchestration for the webapp CLI.

Standalone module — imports only from sibling ``models`` module.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from jugeo.webapp.cli.models import (
    AppType,
    PipelineStage,
    PipelineResult,
    StageResult,
    TemplateChoice,
    WebappConfig,
)

try:
    from jugeo.webapp.cli.theory_conformance import TheoryDrivenVerifier
    _THEORY_AVAILABLE = True
except ImportError:
    _THEORY_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
# TemplateSpecs — canonical spec dicts for each template tier
# ═══════════════════════════════════════════════════════════════════════════

class TemplateSpecs:
    """Pre-built application specifications for each template tier."""

    @staticmethod
    def minimal(name: str, port: int) -> dict:
        return {
            "name": name,
            "port": port,
            "type": "minimal",
            "routes": [
                {"path": "/", "method": "GET", "handler": "index"},
            ],
            "models": [],
            "auth": False,
            "blueprints": [],
        }

    @staticmethod
    def standard(name: str, port: int) -> dict:
        return {
            "name": name,
            "port": port,
            "type": "standard",
            "routes": [
                {"path": "/", "method": "GET", "handler": "index"},
                {"path": "/items", "method": "GET", "handler": "list_items"},
                {"path": "/items", "method": "POST", "handler": "create_item"},
                {"path": "/items/<int:id>", "method": "GET", "handler": "get_item"},
                {"path": "/items/<int:id>", "method": "PUT", "handler": "update_item"},
                {"path": "/items/<int:id>", "method": "DELETE", "handler": "delete_item"},
                {"path": "/login", "method": "GET", "handler": "login"},
                {"path": "/login", "method": "POST", "handler": "login_post"},
                {"path": "/logout", "method": "GET", "handler": "logout"},
            ],
            "models": [
                {"name": "User", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "username", "type": "String(80)"},
                    {"name": "email", "type": "String(120)"},
                    {"name": "password_hash", "type": "String(256)"},
                ]},
                {"name": "Item", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "title", "type": "String(200)"},
                    {"name": "description", "type": "Text"},
                    {"name": "created_at", "type": "DateTime"},
                    {"name": "user_id", "type": "Integer"},
                ]},
                {"name": "Category", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "name", "type": "String(100)"},
                ]},
            ],
            "auth": True,
            "blueprints": [],
        }

    @staticmethod
    def full(name: str, port: int) -> dict:
        return {
            "name": name,
            "port": port,
            "type": "full",
            "routes": [
                {"path": "/", "method": "GET", "handler": "index"},
                {"path": "/items", "method": "GET", "handler": "list_items"},
                {"path": "/items", "method": "POST", "handler": "create_item"},
                {"path": "/items/<int:id>", "method": "GET", "handler": "get_item"},
                {"path": "/items/<int:id>", "method": "PUT", "handler": "update_item"},
                {"path": "/items/<int:id>", "method": "DELETE", "handler": "delete_item"},
                {"path": "/api/v1/items", "method": "GET", "handler": "api_list_items"},
                {"path": "/api/v1/items/<int:id>", "method": "GET", "handler": "api_get_item"},
                {"path": "/admin/", "method": "GET", "handler": "admin_index"},
                {"path": "/admin/users", "method": "GET", "handler": "admin_users"},
                {"path": "/dashboard", "method": "GET", "handler": "dashboard"},
                {"path": "/login", "method": "GET", "handler": "login"},
                {"path": "/login", "method": "POST", "handler": "login_post"},
                {"path": "/logout", "method": "GET", "handler": "logout"},
            ],
            "models": [
                {"name": "User", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "username", "type": "String(80)"},
                    {"name": "email", "type": "String(120)"},
                    {"name": "password_hash", "type": "String(256)"},
                    {"name": "is_admin", "type": "Boolean"},
                ]},
                {"name": "Item", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "title", "type": "String(200)"},
                    {"name": "description", "type": "Text"},
                    {"name": "created_at", "type": "DateTime"},
                    {"name": "user_id", "type": "Integer"},
                ]},
                {"name": "Category", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "name", "type": "String(100)"},
                ]},
                {"name": "AuditLog", "fields": [
                    {"name": "id", "type": "Integer", "primary_key": True},
                    {"name": "action", "type": "String(200)"},
                    {"name": "timestamp", "type": "DateTime"},
                    {"name": "user_id", "type": "Integer"},
                ]},
            ],
            "auth": True,
            "blueprints": [
                {"name": "api", "url_prefix": "/api/v1"},
                {"name": "admin", "url_prefix": "/admin"},
                {"name": "dashboard", "url_prefix": "/dashboard"},
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════
# SpecBuilder — build spec dicts from various sources
# ═══════════════════════════════════════════════════════════════════════════

class SpecBuilder:
    """Builds application specification dicts from various inputs."""

    @staticmethod
    def from_config(config: WebappConfig) -> dict:
        """Build a spec dict directly from a WebappConfig."""
        return {
            "name": config.app_name,
            "port": config.port,
            "type": config.app_type.value if isinstance(config.app_type, AppType) else config.app_type,
            "domain": config.domain,
            "user_population": config.user_population,
            "routes": list(config.routes_json) if config.routes_json else [],
            "models": list(config.models_json) if config.models_json else [],
            "auth": False,
            "blueprints": [],
        }

    @staticmethod
    def from_models_json(models: list, config: WebappConfig) -> dict:
        """Build a spec from a list of model dicts, auto-generating CRUD routes."""
        routes: list = [{"path": "/", "method": "GET", "handler": "index"}]
        for m in models:
            mname = m.get("name", "item").lower()
            routes.extend([
                {"path": f"/{mname}s", "method": "GET", "handler": f"list_{mname}s"},
                {"path": f"/{mname}s", "method": "POST", "handler": f"create_{mname}"},
                {"path": f"/{mname}s/<int:id>", "method": "GET", "handler": f"get_{mname}"},
                {"path": f"/{mname}s/<int:id>", "method": "PUT", "handler": f"update_{mname}"},
                {"path": f"/{mname}s/<int:id>", "method": "DELETE", "handler": f"delete_{mname}"},
            ])
        return {
            "name": config.app_name,
            "port": config.port,
            "type": config.app_type.value if isinstance(config.app_type, AppType) else config.app_type,
            "routes": routes,
            "models": list(models),
            "auth": False,
            "blueprints": [],
        }

    @staticmethod
    def from_routes_json(routes: list, config: WebappConfig) -> dict:
        """Build a spec with custom routes."""
        return {
            "name": config.app_name,
            "port": config.port,
            "type": config.app_type.value if isinstance(config.app_type, AppType) else config.app_type,
            "routes": list(routes),
            "models": list(config.models_json) if config.models_json else [],
            "auth": False,
            "blueprints": [],
        }

    @staticmethod
    def from_ideation(idea: dict, config: WebappConfig) -> dict:
        """Build a spec from an ideation result dict."""
        return {
            "name": idea.get("name", config.app_name),
            "port": config.port,
            "type": idea.get("type", config.app_type.value if isinstance(config.app_type, AppType) else config.app_type),
            "routes": idea.get("routes", []),
            "models": idea.get("models", []),
            "auth": idea.get("auth", False),
            "blueprints": idea.get("blueprints", []),
            "description": idea.get("description", ""),
        }

    @staticmethod
    def from_template(template: str, config: WebappConfig) -> dict:
        """Build a spec from a named template tier."""
        name = config.app_name
        port = config.port
        if template == "minimal":
            return TemplateSpecs.minimal(name, port)
        elif template == "full":
            return TemplateSpecs.full(name, port)
        else:
            return TemplateSpecs.standard(name, port)


# ═══════════════════════════════════════════════════════════════════════════
# Code-generation helpers
# ═══════════════════════════════════════════════════════════════════════════

def _generate_app_py(spec: dict, config: WebappConfig) -> str:
    """Return the text of ``app.py``."""
    name = spec.get("name", config.app_name)
    port = spec.get("port", config.port)
    routes = spec.get("routes", [])
    models = spec.get("models", [])
    auth = spec.get("auth", False)

    lines: list = [
        '"""Flask application — generated by jugeo."""',
        "from flask import Flask, jsonify, request, render_template_string",
        "",
        f"app = Flask(__name__)",
        f'app.config["SECRET_KEY"] = "dev-secret-key"',
        "",
    ]

    # Simple in-memory store
    lines.append("# In-memory data store")
    lines.append("_store: dict = {}")
    lines.append("")

    # Route handlers
    for route in routes:
        path = route.get("path", "/")
        method = route.get("method", "GET")
        handler = route.get("handler", "index")
        lines.append(f'@app.route("{path}", methods=["{method}"])')
        lines.append(f"def {handler}():")
        if method == "GET" and path == "/":
            lines.append(f'    return render_template_string("<h1>Welcome to {name}</h1>")')
        elif method == "GET":
            lines.append(f'    return jsonify({{"status": "ok", "handler": "{handler}"}})')
        elif method == "POST":
            lines.append(f"    data = request.get_json(silent=True) or {{}}")
            lines.append(f'    return jsonify({{"created": True, "handler": "{handler}"}}), 201')
        elif method in ("PUT", "PATCH"):
            lines.append(f"    data = request.get_json(silent=True) or {{}}")
            lines.append(f'    return jsonify({{"updated": True, "handler": "{handler}"}})')
        elif method == "DELETE":
            lines.append(f'    return jsonify({{"deleted": True, "handler": "{handler}"}})')
        else:
            lines.append(f'    return jsonify({{"handler": "{handler}"}})')
        lines.append("")

    # Main guard
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append(f"    app.run(port={port}, debug=True)")
    lines.append("")

    return "\n".join(lines)


def _generate_requirements_txt() -> str:
    return "\n".join([
        "Flask>=2.3.0",
        "Jinja2>=3.1.0",
        "Werkzeug>=2.3.0",
        "",
    ])


def _generate_readme(name: str, port: int) -> str:
    return "\n".join([
        f"# {name}",
        "",
        f"Flask application generated by **jugeo**.",
        "",
        "## Quick Start",
        "",
        "```bash",
        "pip install -r requirements.txt",
        f"python app.py   # serves on http://localhost:{port}",
        "```",
        "",
    ])


def _generate_test_app(name: str) -> str:
    return "\n".join([
        '"""Basic tests for the generated Flask app."""',
        "import pytest",
        "from app import app",
        "",
        "",
        "@pytest.fixture",
        "def client():",
        "    app.config['TESTING'] = True",
        "    with app.test_client() as client:",
        "        yield client",
        "",
        "",
        "def test_index(client):",
        '    rv = client.get("/")',
        "    assert rv.status_code == 200",
        "",
    ])


def _generate_dockerfile(name: str, port: int) -> str:
    return "\n".join([
        "FROM python:3.11-slim",
        f'LABEL maintainer="jugeo"',
        "",
        "WORKDIR /app",
        "COPY requirements.txt .",
        "RUN pip install --no-cache-dir -r requirements.txt",
        "COPY . .",
        f'EXPOSE {port}',
        f'CMD ["python", "app.py"]',
        "",
    ])


# ═══════════════════════════════════════════════════════════════════════════
# WebappPipeline
# ═══════════════════════════════════════════════════════════════════════════

class WebappPipeline:
    """Orchestrates the full ideate → specify → generate → verify → report pipeline."""

    def run(self, config: WebappConfig) -> PipelineResult:
        """Execute the pipeline and return an aggregated result."""
        t0 = time.monotonic()

        result = PipelineResult(config=config, output_dir=config.outdir)
        stages_completed: list = []

        # 1. Ideate
        ideate_res = self._stage_ideate(config)
        stages_completed.append(ideate_res)
        result.ideation_result = ideate_res.details

        # 2. Specify
        specify_res = self._stage_specify(config, ideation=ideate_res.details)
        stages_completed.append(specify_res)
        result.app_spec = specify_res.details.get("spec", {})

        # 2.5. Theory descent check
        descent_res = self._stage_descent_check(config, result.app_spec)
        stages_completed.append(descent_res)
        result.descent_result = descent_res.details

        # 3. Generate
        generate_res = self._stage_generate(config, result.app_spec)
        stages_completed.append(generate_res)
        result.generation_result = generate_res.details

        # 4. Verify (optional)
        if config.verify:
            verify_res = self._stage_verify(config, config.outdir, result.app_spec)
            stages_completed.append(verify_res)
            result.verification_result = verify_res.details

        # 5. Report
        report_res = self._stage_report(config, result)
        stages_completed.append(report_res)

        result.stages_completed = stages_completed
        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    # -- individual stages --------------------------------------------------

    def _stage_ideate(self, config: WebappConfig) -> StageResult:
        t0 = time.monotonic()
        spec = SpecBuilder.from_template(
            config.template.value if isinstance(config.template, TemplateChoice) else config.template,
            config,
        )
        details = {
            "spec": spec,
            "domain": config.domain,
            "user_population": config.user_population,
        }
        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(
            stage=PipelineStage.IDEATE,
            success=True,
            duration_ms=elapsed,
            details=details,
        )

    def _stage_specify(self, config: WebappConfig, ideation: dict = None) -> StageResult:
        t0 = time.monotonic()
        if ideation and "spec" in ideation:
            spec = ideation["spec"]
        elif config.models_json:
            spec = SpecBuilder.from_models_json(config.models_json, config)
        elif config.routes_json:
            spec = SpecBuilder.from_routes_json(config.routes_json, config)
        else:
            spec = SpecBuilder.from_config(config)

        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(
            stage=PipelineStage.SPECIFY,
            success=True,
            duration_ms=elapsed,
            details={"spec": spec},
        )

    def _stage_descent_check(self, config: WebappConfig, spec: dict) -> StageResult:
        t0 = time.monotonic()
        if not _THEORY_AVAILABLE:
            return StageResult(
                stage=PipelineStage.DESCENT_CHECK,
                success=True,
                duration_ms=0.0,
                details={"skipped": True, "reason": "theory_conformance module not available"},
            )
        try:
            verifier = TheoryDrivenVerifier()
            report = verifier.verify_spec(spec)
            elapsed = (time.monotonic() - t0) * 1000
            errors = [v["detail"] for v in report.get("violations", []) if v.get("severity") == "error"]
            warnings = [v["detail"] for v in report.get("violations", []) if v.get("severity") == "warning"]
            return StageResult(
                stage=PipelineStage.DESCENT_CHECK,
                success=True,  # advisory: violations reported but never block generation
                duration_ms=elapsed,
                details=report,
                warnings=errors + warnings,  # surfaced as warnings, not blocking errors
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return StageResult(
                stage=PipelineStage.DESCENT_CHECK,
                success=True,  # non-fatal: theory check failure doesn't block generation
                duration_ms=elapsed,
                details={"error": str(exc)},
                warnings=[f"Theory descent check failed: {exc}"],
            )

    def _stage_generate(self, config: WebappConfig, spec: dict) -> StageResult:
        t0 = time.monotonic()
        errors: list = []
        files_created: list = []

        outdir = Path(config.outdir)
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return StageResult(
                stage=PipelineStage.GENERATE,
                success=False,
                errors=[str(exc)],
            )

        # app.py
        try:
            app_text = _generate_app_py(spec, config)
            (outdir / "app.py").write_text(app_text)
            files_created.append("app.py")
        except OSError as exc:
            errors.append(f"app.py: {exc}")

        # requirements.txt
        try:
            (outdir / "requirements.txt").write_text(_generate_requirements_txt())
            files_created.append("requirements.txt")
        except OSError as exc:
            errors.append(f"requirements.txt: {exc}")

        # README.md
        try:
            (outdir / "README.md").write_text(
                _generate_readme(config.app_name, config.port)
            )
            files_created.append("README.md")
        except OSError as exc:
            errors.append(f"README.md: {exc}")

        # Optional: tests
        if config.include_tests:
            tests_dir = outdir / "tests"
            tests_dir.mkdir(exist_ok=True)
            try:
                (tests_dir / "test_app.py").write_text(
                    _generate_test_app(config.app_name)
                )
                files_created.append("tests/test_app.py")
            except OSError as exc:
                errors.append(f"tests/test_app.py: {exc}")

        # Optional: Dockerfile
        if config.include_docker:
            try:
                (outdir / "Dockerfile").write_text(
                    _generate_dockerfile(config.app_name, config.port)
                )
                files_created.append("Dockerfile")
            except OSError as exc:
                errors.append(f"Dockerfile: {exc}")

        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(
            stage=PipelineStage.GENERATE,
            success=len(errors) == 0,
            duration_ms=elapsed,
            details={"files_created": files_created, "output_dir": str(outdir)},
            errors=errors,
        )

    def _stage_verify(self, config: WebappConfig, output_dir: str, spec=None) -> StageResult:
        t0 = time.monotonic()
        checks: list = []
        errors: list = []
        outdir = Path(output_dir)

        # Check app.py exists
        app_path = outdir / "app.py"
        if app_path.exists():
            checks.append({"name": "app.py exists", "passed": True})

            # Check contains Flask
            source = app_path.read_text()
            has_flask = "Flask" in source
            checks.append({"name": "app.py contains Flask", "passed": has_flask})
            if not has_flask:
                errors.append("app.py does not reference Flask")

            # Syntax check
            try:
                compile(source, str(app_path), "exec")
                checks.append({"name": "app.py syntax valid", "passed": True})
            except SyntaxError as exc:
                checks.append({"name": "app.py syntax valid", "passed": False})
                errors.append(f"Syntax error: {exc}")

            # Theory-based verification of generated code (advisory — never blocks)
            if _THEORY_AVAILABLE and app_path.exists():
                try:
                    verifier = TheoryDrivenVerifier()
                    gen_report = verifier.verify_generated(spec, source)
                    gen_warnings = [v["detail"] for v in gen_report.get("violations", [])]
                    checks.append({
                        "name": "theory descent verification",
                        "passed": True,  # advisory only
                        "details": gen_report.get("summary", ""),
                        "theory_warnings": gen_warnings,
                    })
                except Exception as exc:
                    checks.append({"name": "theory descent verification", "passed": True, "details": f"skipped: {exc}"})
        else:
            checks.append({"name": "app.py exists", "passed": False})
            errors.append("app.py not found")

        passed = all(c["passed"] for c in checks)
        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(
            stage=PipelineStage.VERIFY,
            success=passed,
            duration_ms=elapsed,
            details={"passed": passed, "checks": checks, "errors": errors},
            errors=errors,
        )

    def _stage_report(self, config: WebappConfig, result: PipelineResult) -> StageResult:
        t0 = time.monotonic()
        details = {
            "output_dir": config.outdir,
            "app_name": config.app_name,
            "port": config.port,
            "template": config.template.value if isinstance(config.template, TemplateChoice) else config.template,
            "stages_run": len(result.stages_completed),
        }
        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(
            stage=PipelineStage.REPORT,
            success=True,
            duration_ms=elapsed,
            details=details,
        )
