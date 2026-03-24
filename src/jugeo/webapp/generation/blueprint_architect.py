"""Blueprint architecture design — stdlib only."""
from __future__ import annotations

import textwrap
from .models import AppSpec, BlueprintSpec, RouteSpec, ResponseType


class BlueprintArchitect:
    """Partitions routes into logical Flask Blueprints."""

    def design_blueprints(self, spec: AppSpec) -> list:
        blueprints: list[BlueprintSpec] = []
        remaining = list(spec.routes)

        auth_bp = self._detect_auth_blueprint(remaining)
        if auth_bp:
            blueprints.append(auth_bp)
            auth_urls = {r.url for r in auth_bp.routes}
            remaining = [r for r in remaining if r.url not in auth_urls]

        api_bp = self._detect_api_blueprint(remaining)
        if api_bp:
            blueprints.append(api_bp)
            api_urls = {r.url for r in api_bp.routes}
            remaining = [r for r in remaining if r.url not in api_urls]

        admin_bp = self._detect_admin_blueprint(remaining)
        if admin_bp:
            blueprints.append(admin_bp)
            admin_urls = {r.url for r in admin_bp.routes}
            remaining = [r for r in remaining if r.url not in admin_urls]

        if remaining:
            blueprints.append(BlueprintSpec(
                name="main",
                url_prefix="",
                routes=remaining,
            ))
        return blueprints

    # ------------------------------------------------------------------

    def _group_by_resource(self, routes: list) -> dict:
        groups: dict[str, list] = {}
        for route in routes:
            parts = route.url.strip("/").split("/")
            key = parts[0] if parts and parts[0] else "root"
            groups.setdefault(key, []).append(route)
        return groups

    def _detect_auth_blueprint(self, routes: list) -> BlueprintSpec | None:
        auth_keywords = {"login", "logout", "register", "signup", "signin", "signout"}
        auth_routes = [
            r for r in routes
            if any(kw in r.url.lower() for kw in auth_keywords)
            or any(kw in (r.handler_name or "").lower() for kw in auth_keywords)
        ]
        if auth_routes:
            return BlueprintSpec(name="auth", url_prefix="/auth", routes=auth_routes)
        return None

    def _detect_api_blueprint(self, routes: list) -> BlueprintSpec | None:
        api_routes = [
            r for r in routes
            if r.url.startswith("/api") or r.response_type == ResponseType.JSON
        ]
        if api_routes:
            return BlueprintSpec(name="api", url_prefix="/api", routes=api_routes)
        return None

    def _detect_admin_blueprint(self, routes: list) -> BlueprintSpec | None:
        admin_routes = [r for r in routes if "/admin" in r.url.lower()]
        if admin_routes:
            return BlueprintSpec(name="admin", url_prefix="/admin", routes=admin_routes)
        return None

    def generate_blueprint_module(self, bp: BlueprintSpec) -> str:
        lines = [
            "from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session",
            "",
            f"{bp.name}_bp = Blueprint('{bp.name}', __name__, url_prefix='{bp.url_prefix}')",
            "",
        ]
        for route in bp.routes:
            handler = route.handler_name or route.url.strip("/").replace("/", "_") or "index"
            methods_str = repr(route.methods)
            rel_url = route.url
            if bp.url_prefix and rel_url.startswith(bp.url_prefix):
                rel_url = rel_url[len(bp.url_prefix):] or "/"
            lines.append(f"@{bp.name}_bp.route('{rel_url}', methods={methods_str})")
            lines.append(f"def {handler}():")
            if route.response_type == ResponseType.JSON:
                lines.append("    return jsonify({'status': 'ok'})")
            elif route.template:
                lines.append(f"    return render_template('{route.template}')")
            else:
                lines.append(f"    return render_template('{handler}.html')")
            lines.append("")
        return "\n".join(lines)


class ArchitectureValidator:
    """Validates blueprint architecture for conflicts."""

    def validate_blueprint_isolation(self, blueprints: list) -> list:
        errors: list[str] = []
        handler_names: dict[str, str] = {}
        for bp in blueprints:
            for route in bp.routes:
                handler = route.handler_name or route.url
                if handler in handler_names:
                    errors.append(
                        f"Handler '{handler}' duplicated in blueprints "
                        f"'{handler_names[handler]}' and '{bp.name}'"
                    )
                handler_names[handler] = bp.name
        return errors

    def validate_url_uniqueness(self, blueprints: list) -> list:
        errors: list[str] = []
        seen: dict[str, str] = {}
        for bp in blueprints:
            prefix = bp.url_prefix or ""
            for route in bp.routes:
                full_url = prefix + route.url
                for method in route.methods:
                    key = f"{method} {full_url}"
                    if key in seen:
                        errors.append(
                            f"URL conflict: {key} in both '{seen[key]}' and '{bp.name}'"
                        )
                    seen[key] = bp.name
        return errors
