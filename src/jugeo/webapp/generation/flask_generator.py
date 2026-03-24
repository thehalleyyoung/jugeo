"""Main Flask app generator — produces a complete project directory. Stdlib only."""
from __future__ import annotations

import os
import textwrap

from .models import AppSpec, GenerationResult, ResponseType, ColumnSpec, ColumnType
from .route_generator import RouteCodeGenerator
from .model_generator import ModelCodeGenerator, SchemaGenerator
from .template_generator import TemplateCodeGenerator
from .static_generator import CSSGenerator, JSGenerator
from .config_generator import ConfigCodeGenerator
from .obligations import (
    Obligation,
    ObligationReport,
    resolve_obligations,
    enforce_obligations,
    GenerationTarget,
)
from .flask_obligations import FlaskObligationChecker, FlaskSpecEnricher


class FlaskAppGenerator:
    """Generate a complete Flask project at a given output directory.

    Like the HTML-only generator, enforces visual/structural obligations
    via the obligation presheaf.  A sparse spec is enriched until all
    obligations are met (or max rounds exhausted).

    Parameters
    ----------
    obligations : str or list[Obligation]
        Preset name ("minimal", "standard", "stunning") or explicit list.
        Default is "stunning".
    max_enrichment_rounds : int
        Maximum enrichment passes before giving up.
    """

    def __init__(
        self,
        obligations: "str | list[Obligation]" = "stunning",
        max_enrichment_rounds: int = 5,
    ) -> None:
        self._route_gen = RouteCodeGenerator()
        self._model_gen = ModelCodeGenerator()
        self._schema_gen = SchemaGenerator()
        self._template_gen = TemplateCodeGenerator()
        self._css_gen = CSSGenerator()
        self._js_gen = JSGenerator()
        self._config_gen = ConfigCodeGenerator()
        self._checker = FlaskObligationChecker()
        self._enricher = FlaskSpecEnricher()
        self._max_rounds = max_enrichment_rounds
        self._obligations = resolve_obligations(obligations, GenerationTarget.FLASK)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, spec: AppSpec, output_dir: str,
                 obligations: "str | list[Obligation] | None" = None) -> GenerationResult:
        """Generate a Flask project, enforcing obligations on the spec first."""
        os.makedirs(output_dir, exist_ok=True)

        # Resolve per-call obligations override
        obs = resolve_obligations(obligations, GenerationTarget.FLASK) if obligations is not None else self._obligations

        # ── Obligation enforcement loop ───────────────────────────────
        spec, report = enforce_obligations(spec, obs, self._checker, self._enricher, self._max_rounds)

        # ── Generate files ────────────────────────────────────────────
        warnings = self._validate_spec(spec)
        if not report.all_met:
            for r in report.unmet:
                warnings.append(
                    f"Flask obligation not met after {report.enrichment_rounds} rounds: "
                    f"{r.obligation.kind.value} (need {r.obligation.minimum}, have {r.actual})"
                )

        self._create_directory_structure(output_dir, spec)

        files_created: list[str] = []
        files_created.extend(self._generate_main_py(spec, output_dir))
        files_created.extend(self._generate_requirements_txt(spec, output_dir))
        files_created.extend(self._generate_config_py(spec, output_dir))
        files_created.extend(self._generate_models_py(spec, output_dir))
        files_created.extend(self._generate_routes_py(spec, output_dir))
        files_created.extend(self._generate_templates(spec, output_dir))
        files_created.extend(self._generate_static(spec, output_dir))
        files_created.extend(self._generate_init_db_py(spec, output_dir))

        post_warnings = self._post_generation_verify(output_dir, spec)
        result = GenerationResult(
            output_dir=output_dir,
            files_created=files_created,
            app_spec=spec,
            verification_results={"obligation_report": report.to_dict()},
            warnings=warnings + post_warnings,
        )
        return result

    # ------------------------------------------------------------------
    # Directory structure
    # ------------------------------------------------------------------

    def _create_directory_structure(self, output_dir: str, spec: AppSpec) -> None:
        for d in ["templates", "static/css", "static/js", "static/img"]:
            os.makedirs(os.path.join(output_dir, d), exist_ok=True)

    # ------------------------------------------------------------------
    # main.py
    # ------------------------------------------------------------------

    def _generate_main_py(self, spec: AppSpec, output_dir: str) -> list:
        secret = spec.config.secret_key or "dev-secret-key-change-in-production"
        debug = "True" if spec.config.debug else "False"

        route_blocks: list[str] = []
        for route in spec.routes:
            handler = route.handler_name or self._handler_name(route.url)
            methods_str = repr(route.methods)

            if route.response_type == ResponseType.JSON:
                if "POST" in route.methods or "PUT" in route.methods:
                    body = (
                        f"@app.route('{route.url}', methods={methods_str})\n"
                        f"def {handler}():\n"
                        f"    data = request.get_json(silent=True) or {{}}\n"
                        f"    return jsonify({{'status': 'ok', 'data': data}})\n"
                    )
                else:
                    body = (
                        f"@app.route('{route.url}', methods={methods_str})\n"
                        f"def {handler}():\n"
                        f"    return jsonify({{'status': 'ok', 'data': []}})\n"
                    )
            elif route.response_type == ResponseType.FORM:
                tmpl = route.template or f"{handler}.html"
                body = (
                    f"@app.route('{route.url}', methods={methods_str})\n"
                    f"def {handler}():\n"
                    f"    if request.method == 'POST':\n"
                    f"        flash('Success!', 'success')\n"
                    f"        return redirect(url_for('index'))\n"
                    f"    return render_template('{tmpl}')\n"
                )
            elif route.response_type == ResponseType.REDIRECT:
                body = (
                    f"@app.route('{route.url}', methods={methods_str})\n"
                    f"def {handler}():\n"
                    f"    return redirect(url_for('index'))\n"
                )
            else:
                tmpl = route.template or f"{handler}.html"
                body = (
                    f"@app.route('{route.url}', methods={methods_str})\n"
                    f"def {handler}():\n"
                    f"    return render_template('{tmpl}')\n"
                )
            route_blocks.append(body)

        routes_code = "\n\n".join(route_blocks)

        content = textwrap.dedent(f"""\
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
import sqlite3

app = Flask(__name__)
app.config['SECRET_KEY'] = '{secret}'
app.config['DATABASE'] = os.path.join(app.root_path, 'app.db')
app.config['DEBUG'] = {debug}


def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db


{routes_code}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port={spec.port}, debug=app.config['DEBUG'])
""")
        path = os.path.join(output_dir, "main.py")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # requirements.txt
    # ------------------------------------------------------------------

    def _generate_requirements_txt(self, spec: AppSpec, output_dir: str) -> list:
        deps = list(spec.dependencies) if spec.dependencies else []
        if "flask" not in [d.lower() for d in deps]:
            deps.insert(0, "Flask")
        content = "\n".join(deps) + "\n"
        path = os.path.join(output_dir, "requirements.txt")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # config.py
    # ------------------------------------------------------------------

    def _generate_config_py(self, spec: AppSpec, output_dir: str) -> list:
        content = self._config_gen.generate_config_module(spec.config)
        path = os.path.join(output_dir, "config.py")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # models.py  (Flask-SQLAlchemy style — for reference, not imported at runtime)
    # ------------------------------------------------------------------

    def _generate_models_py(self, spec: AppSpec, output_dir: str) -> list:
        if not spec.models:
            return []
        content = self._model_gen.generate_models_module(spec.models)
        path = os.path.join(output_dir, "models.py")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # routes.py
    # ------------------------------------------------------------------

    def _generate_routes_py(self, spec: AppSpec, output_dir: str) -> list:
        if not spec.routes:
            return []
        content = self._route_gen.generate_routes_module(spec.routes)
        path = os.path.join(output_dir, "routes.py")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------

    def _generate_templates(self, spec: AppSpec, output_dir: str) -> list:
        tmpl_dir = os.path.join(output_dir, "templates")
        files: list[str] = []

        # base.html
        nav_items = [{"url": r.url, "label": (r.handler_name or r.url).replace("_", " ").title()}
                     for r in spec.routes if r.response_type == ResponseType.TEMPLATE][:5]
        base_path = os.path.join(tmpl_dir, "base.html")
        self._write(base_path, self._template_gen.generate_base_template(spec.name, nav_items))
        files.append(base_path)

        # error templates
        for code in [404, 500]:
            err_path = os.path.join(tmpl_dir, f"{code}.html")
            self._write(err_path, self._template_gen.generate_error_template(code))
            files.append(err_path)

        # route templates
        for route in spec.routes:
            if route.template and route.response_type in (ResponseType.TEMPLATE, ResponseType.FORM):
                tp = os.path.join(tmpl_dir, route.template)
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                if route.response_type == ResponseType.FORM:
                    content = textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>{route.handler_name or route.url}</h1>
<form method="POST">
    <button type="submit" class="btn btn-primary">Submit</button>
</form>
{{% endblock %}}""")
                else:
                    content = textwrap.dedent(f"""\
{{% extends 'base.html' %}}
{{% block content %}}
<h1>{route.handler_name or route.url}</h1>
<p>Welcome to {spec.name}.</p>
{{% endblock %}}""")
                self._write(tp, content)
                files.append(tp)

        # named templates from spec
        for tmpl_spec in spec.templates:
            tp = os.path.join(tmpl_dir, tmpl_spec.name)
            if not os.path.exists(tp):
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                self._write(tp, self._template_gen.generate_template(tmpl_spec))
                files.append(tp)

        return files

    # ------------------------------------------------------------------
    # static files
    # ------------------------------------------------------------------

    def _generate_static(self, spec: AppSpec, output_dir: str) -> list:
        files: list[str] = []

        css_path = os.path.join(output_dir, "static", "css", "base.css")
        self._write(css_path, self._css_gen.generate_base_css())
        files.append(css_path)

        js_path = os.path.join(output_dir, "static", "js", "base.js")
        self._write(js_path, self._js_gen.generate_base_js())
        files.append(js_path)

        for sf in spec.static_files:
            sp = os.path.join(output_dir, "static", sf.path)
            os.makedirs(os.path.dirname(sp), exist_ok=True)
            self._write(sp, sf.content)
            files.append(sp)

        return files

    # ------------------------------------------------------------------
    # init_db.py  (raw sqlite3, no flask-sqlalchemy)
    # ------------------------------------------------------------------

    def _generate_init_db_py(self, spec: AppSpec, output_dir: str) -> list:
        if not spec.models:
            return []
        content = self._schema_gen.generate_init_db(spec.models)
        path = os.path.join(output_dir, "init_db.py")
        self._write(path, content)
        return [path]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_spec(self, spec: AppSpec) -> list:
        warnings: list[str] = []
        if not spec.name:
            warnings.append("App name is empty")
        if not spec.routes:
            warnings.append("No routes defined")
        handlers = [r.handler_name for r in spec.routes if r.handler_name]
        seen: set[str] = set()
        for h in handlers:
            if h in seen:
                warnings.append(f"Duplicate handler name: {h}")
            seen.add(h)
        return warnings

    def _post_generation_verify(self, output_dir: str, spec: AppSpec) -> list:
        warnings: list[str] = []
        main_py = os.path.join(output_dir, "main.py")
        if not os.path.exists(main_py):
            warnings.append("main.py was not created")
        return warnings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write(path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    @staticmethod
    def _handler_name(url: str) -> str:
        name = url.strip("/").replace("/", "_").replace("<", "").replace(">", "").replace(":", "_")
        return name or "index"
