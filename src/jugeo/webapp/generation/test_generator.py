"""Generate pytest test suites for Flask apps — stdlib only."""
from __future__ import annotations

import textwrap
from .models import AppSpec, RouteSpec, FormSpec, ModelSpec, ResponseType


class TestCodeGenerator:
    """Produces a test_app.py source string for a generated Flask app."""

    def generate_test_suite(self, spec: AppSpec) -> str:
        sections: list[str] = [
            "import pytest",
            "",
            self._generate_test_client_fixture(),
            "",
        ]
        route_tests = self._generate_route_tests(spec.routes)
        if route_tests:
            sections.append(route_tests)
        api_routes = [r for r in spec.routes if r.response_type == ResponseType.JSON]
        if api_routes:
            sections.append(self._generate_api_tests(api_routes))
        form_routes = [r for r in spec.routes if r.response_type == ResponseType.FORM]
        if form_routes:
            sections.append(self._generate_form_tests(form_routes))
        if spec.models:
            sections.append(self._generate_model_tests(spec.models))
        auth_routes = [r for r in spec.routes if r.auth_required]
        if auth_routes:
            sections.append(self._generate_auth_tests(auth_routes))
        return "\n\n".join(sections)

    # ------------------------------------------------------------------

    def _generate_test_client_fixture(self) -> str:
        return textwrap.dedent("""\
@pytest.fixture
def client():
    from main import app
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client""")

    def _generate_route_tests(self, routes: list) -> str:
        lines: list[str] = []
        for route in routes:
            handler = route.handler_name or route.url.strip("/").replace("/", "_") or "index"
            lines.append(f"def test_{handler}_returns_ok(client):")
            lines.append(f"    response = client.get('{route.url}')")
            lines.append(f"    assert response.status_code in (200, 302)")
            lines.append("")
        return "\n".join(lines)

    def _generate_form_tests(self, forms: list) -> str:
        lines: list[str] = []
        for route in forms:
            handler = route.handler_name or route.url.strip("/").replace("/", "_") or "form"
            lines.append(f"def test_{handler}_get(client):")
            lines.append(f"    response = client.get('{route.url}')")
            lines.append(f"    assert response.status_code == 200")
            lines.append("")
            lines.append(f"def test_{handler}_post(client):")
            lines.append(f"    response = client.post('{route.url}', data={{}})")
            lines.append(f"    assert response.status_code in (200, 302)")
            lines.append("")
        return "\n".join(lines)

    def _generate_api_tests(self, api_routes: list) -> str:
        lines: list[str] = []
        for route in api_routes:
            handler = route.handler_name or route.url.strip("/").replace("/", "_")
            lines.append(f"def test_{handler}_json(client):")
            lines.append(f"    response = client.get('{route.url}')")
            lines.append(f"    assert response.status_code == 200")
            lines.append(f"    assert response.is_json")
            lines.append("")
        return "\n".join(lines)

    def _generate_model_tests(self, models: list) -> str:
        lines: list[str] = []
        for model in models:
            name = model.name if hasattr(model, "name") else str(model)
            lines.append(f"def test_{name.lower()}_model_exists():")
            lines.append(f"    from models import {name}")
            lines.append(f"    assert {name} is not None")
            lines.append("")
        return "\n".join(lines)

    def _generate_auth_tests(self, auth_routes: list) -> str:
        lines: list[str] = []
        for route in auth_routes:
            handler = route.handler_name or route.url.strip("/").replace("/", "_")
            lines.append(f"def test_{handler}_requires_auth(client):")
            lines.append(f"    response = client.get('{route.url}')")
            lines.append(f"    assert response.status_code in (302, 401, 403)")
            lines.append("")
        return "\n".join(lines)
