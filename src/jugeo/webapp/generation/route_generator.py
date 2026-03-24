"""Generate Flask route handler code — stdlib only, no flask imports."""
from __future__ import annotations

import textwrap
from .models import RouteSpec, ResponseType, FormSpec


class RouteCodeGenerator:
    """Produces Python source strings for Flask route handlers."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_route(self, route: RouteSpec) -> str:
        handler = route.handler_name or self._handler_name_from_url(route.url)
        if route.response_type == ResponseType.JSON:
            return self._generate_api_route(route, handler)
        elif route.response_type == ResponseType.FORM:
            return self._generate_form_route(route, handler)
        elif route.response_type == ResponseType.REDIRECT:
            return self._generate_redirect_route(route, handler)
        else:
            return self._generate_template_route(route, handler)

    def generate_routes_module(self, routes: list) -> str:
        lines = [
            "from flask import render_template, request, redirect, url_for, flash, jsonify, session",
            "from functools import wraps",
            "",
            "",
            "def login_required(f):",
            "    @wraps(f)",
            "    def decorated(*args, **kwargs):",
            "        if 'user_id' not in session:",
            "            return redirect(url_for('login'))",
            "        return f(*args, **kwargs)",
            "    return decorated",
            "",
            "",
            "def register_routes(app):",
        ]
        for route in routes:
            body = self.generate_route(route)
            # indent the route code inside register_routes
            indented = textwrap.indent(body, "    ")
            lines.append(indented)
            lines.append("")
        error_block = textwrap.indent(self._generate_error_handlers(), "    ")
        lines.append(error_block)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Route type generators
    # ------------------------------------------------------------------

    def _generate_template_route(self, route: RouteSpec, handler: str) -> str:
        methods_str = repr(route.methods)
        tmpl = route.template or f"{handler}.html"
        auth_dec = self._generate_auth_decorator(route)
        body = self._generate_handler_body(route, handler, tmpl)
        return (
            f"@app.route('{route.url}', methods={methods_str})\n"
            f"{auth_dec}"
            f"def {handler}():\n"
            f"{body}"
        )

    def _generate_api_route(self, route: RouteSpec, handler: str) -> str:
        methods_str = repr(route.methods)
        auth_dec = self._generate_auth_decorator(route)
        lines = []
        lines.append(f"@app.route('{route.url}', methods={methods_str})")
        if auth_dec:
            lines.append(auth_dec.rstrip("\n"))
            lines.append(f"def {handler}():")
        else:
            lines.append(f"def {handler}():")
        if "POST" in route.methods or "PUT" in route.methods:
            lines.append("    data = request.get_json(silent=True) or {}")
            lines.append("    return jsonify({'status': 'ok', 'data': data})")
        else:
            lines.append("    return jsonify({'status': 'ok', 'data': []})")
        return "\n".join(lines)

    def _generate_form_route(self, route: RouteSpec, handler: str) -> str:
        methods_str = repr(route.methods) if route.methods else "['GET', 'POST']"
        tmpl = route.template or f"{handler}.html"
        auth_dec = self._generate_auth_decorator(route)
        lines = []
        lines.append(f"@app.route('{route.url}', methods={methods_str})")
        if auth_dec:
            lines.append(auth_dec.rstrip("\n"))
        lines.append(f"def {handler}():")
        lines.append("    if request.method == 'POST':")
        lines.append("        flash('Success!', 'success')")
        lines.append("        return redirect(url_for('index'))")
        lines.append(f"    return render_template('{tmpl}')")
        return "\n".join(lines)

    def _generate_redirect_route(self, route: RouteSpec, handler: str) -> str:
        methods_str = repr(route.methods)
        auth_dec = self._generate_auth_decorator(route)
        lines = []
        lines.append(f"@app.route('{route.url}', methods={methods_str})")
        if auth_dec:
            lines.append(auth_dec.rstrip("\n"))
        lines.append(f"def {handler}():")
        lines.append("    return redirect(url_for('index'))")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _generate_handler_body(self, route: RouteSpec, handler: str, tmpl: str) -> str:
        return f"    return render_template('{tmpl}')\n"

    def _generate_auth_decorator(self, route: RouteSpec) -> str:
        if route.auth_required:
            return "@login_required\n"
        return ""

    def _generate_auth_check(self, route: RouteSpec) -> str:
        if route.auth_required:
            return "    if 'user_id' not in session:\n        return redirect(url_for('login'))\n"
        return ""

    def _generate_error_handlers(self) -> str:
        return textwrap.dedent("""\
            @app.errorhandler(404)
            def page_not_found(e):
                return render_template('404.html'), 404

            @app.errorhandler(500)
            def internal_error(e):
                return render_template('500.html'), 500
        """)

    def _build_url_pattern(self, route: RouteSpec) -> str:
        url = route.url
        for p in route.params:
            pname = p.get("name", p) if isinstance(p, dict) else p
            ptype = p.get("type", "string") if isinstance(p, dict) else "string"
            flask_type = {"int": "int", "integer": "int", "float": "float"}.get(ptype, "")
            if flask_type:
                url += f"/<{flask_type}:{pname}>"
            else:
                url += f"/<{pname}>"
        return url

    @staticmethod
    def _handler_name_from_url(url: str) -> str:
        name = url.strip("/").replace("/", "_").replace("<", "").replace(">", "").replace(":", "_")
        return name or "index"


class URLPatternGenerator:
    """Utility for Flask URL patterns."""

    def flask_url_pattern(self, params: list) -> str:
        parts = []
        for p in params:
            if isinstance(p, dict):
                pname = p.get("name", "id")
                ptype = p.get("type", "string")
            else:
                pname = str(p)
                ptype = "string"
            flask_type = {"int": "int", "integer": "int", "float": "float"}.get(ptype, "")
            if flask_type:
                parts.append(f"<{flask_type}:{pname}>")
            else:
                parts.append(f"<{pname}>")
        return "/".join(parts)

    def url_for_call(self, endpoint: str, params: dict) -> str:
        kw = ", ".join(f"{k}={v!r}" for k, v in params.items())
        if kw:
            return f"url_for('{endpoint}', {kw})"
        return f"url_for('{endpoint}')"
