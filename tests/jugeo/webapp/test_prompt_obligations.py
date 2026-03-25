"""Tests for jugeo.webapp.cli.prompt_obligations (Phase 0 pipeline)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest

from jugeo.webapp.cli.prompt_obligations import (
    AppMode,
    ObligationKind,
    AppObligation,
    AppObligationPresheaf,
    PromptObligationExtractor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def extractor() -> PromptObligationExtractor:
    return PromptObligationExtractor()


@pytest.fixture
def recipe_presheaf(extractor: PromptObligationExtractor) -> AppObligationPresheaf:
    return extractor.extract("recipe sharing app")


@pytest.fixture
def landing_presheaf(extractor: PromptObligationExtractor) -> AppObligationPresheaf:
    return extractor.extract("landing page for a coffee shop")


@pytest.fixture
def todo_presheaf(extractor: PromptObligationExtractor) -> AppObligationPresheaf:
    return extractor.extract("todo list app to create and view tasks")


# ---------------------------------------------------------------------------
# TestPromptObligationExtractor
# ---------------------------------------------------------------------------


class TestPromptObligationExtractor:
    def test_recipe_app_is_flask(self, recipe_presheaf: AppObligationPresheaf) -> None:
        assert recipe_presheaf.mode == AppMode.FLASK

    def test_recipe_app_auth_required(self, recipe_presheaf: AppObligationPresheaf) -> None:
        assert recipe_presheaf.auth_required is True

    def test_recipe_app_has_recipe_model(self, recipe_presheaf: AppObligationPresheaf) -> None:
        models = recipe_presheaf.by_kind(ObligationKind.MODEL)
        model_names = [m.name.lower() for m in models]
        assert any("recipe" in name for name in model_names), (
            f"Expected a 'recipe' model; got {model_names}"
        )

    def test_recipe_app_has_crud_routes(self, recipe_presheaf: AppObligationPresheaf) -> None:
        routes = recipe_presheaf.by_kind(ObligationKind.ROUTE)
        methods = {r.data["method"] for r in routes}
        assert "GET" in methods, "Expected at least one GET route"
        assert "POST" in methods, "Expected at least one POST route"

    def test_landing_page_is_static(self, landing_presheaf: AppObligationPresheaf) -> None:
        assert landing_presheaf.mode == AppMode.STATIC

    def test_landing_page_no_auth(self, landing_presheaf: AppObligationPresheaf) -> None:
        assert landing_presheaf.auth_required is False

    def test_todo_app_is_flask(self, todo_presheaf: AppObligationPresheaf) -> None:
        assert todo_presheaf.mode == AppMode.FLASK

    def test_auth_signals_detected(self, extractor: PromptObligationExtractor) -> None:
        presheaf = extractor.extract("A site where users can login and manage their content")
        assert presheaf.auth_required is True

    def test_always_has_accessibility_obligation(
        self, extractor: PromptObligationExtractor
    ) -> None:
        for prompt in ["simple blog", "shop", "landing page for a dentist"]:
            p = extractor.extract(prompt)
            a11y = p.by_kind(ObligationKind.ACCESSIBILITY)
            assert len(a11y) >= 1, f"No ACCESSIBILITY obligation for prompt: {prompt!r}"

    def test_always_has_responsive_obligation(
        self, extractor: PromptObligationExtractor
    ) -> None:
        for prompt in ["task manager", "portfolio site", "chat app"]:
            p = extractor.extract(prompt)
            responsive = p.by_kind(ObligationKind.RESPONSIVE)
            assert len(responsive) >= 1, f"No RESPONSIVE obligation for prompt: {prompt!r}"

    def test_always_has_performance_obligation(
        self, extractor: PromptObligationExtractor
    ) -> None:
        for prompt in ["recipe app", "todo list", "gallery"]:
            p = extractor.extract(prompt)
            perf = p.by_kind(ObligationKind.PERFORMANCE)
            assert len(perf) >= 1, f"No PERFORMANCE obligation for prompt: {prompt!r}"


# ---------------------------------------------------------------------------
# TestAppObligationPresheaf
# ---------------------------------------------------------------------------


class TestAppObligationPresheaf:
    def test_by_kind_filters_correctly(
        self, recipe_presheaf: AppObligationPresheaf
    ) -> None:
        models = recipe_presheaf.by_kind(ObligationKind.MODEL)
        assert all(m.kind == ObligationKind.MODEL for m in models)

        routes = recipe_presheaf.by_kind(ObligationKind.ROUTE)
        assert all(r.kind == ObligationKind.ROUTE for r in routes)

        # Cross-check: model list and route list are disjoint
        model_coords = {m.coordinate_name for m in models}
        route_coords = {r.coordinate_name for r in routes}
        assert model_coords.isdisjoint(route_coords)

    def test_to_local_sections_nonempty(
        self, recipe_presheaf: AppObligationPresheaf
    ) -> None:
        sections = recipe_presheaf.to_local_sections()
        assert len(sections) > 0

    def test_satisfiability_with_auth_and_routes(self) -> None:
        """A presheaf with AUTH + matching auth-required routes should succeed."""
        auth_ob = AppObligation(
            kind=ObligationKind.AUTH,
            name="auth",
            data={"strategy": "session"},
            coordinate_name="auth.session",
        )
        route_ob = AppObligation(
            kind=ObligationKind.ROUTE,
            name="recipe_create",
            data={"path": "/recipes", "method": "POST", "auth_required": True},
            coordinate_name="route.recipe.create",
        )
        model_ob = AppObligation(
            kind=ObligationKind.MODEL,
            name="Recipe",
            data={"fields": ["id", "created_at"]},
            coordinate_name="model.recipe",
        )
        presheaf = AppObligationPresheaf(
            prompt="recipe app",
            mode=AppMode.FLASK,
            obligations=[auth_ob, route_ob, model_ob],
            domain_nouns=["recipe"],
            domain_verbs=["create"],
            ui_metaphors=[],
            auth_required=True,
        )
        result = presheaf.check_satisfiability()
        assert result.is_success, f"Expected success; got: {result.evidence_summary()}"

    def test_satisfiability_fails_auth_no_routes(self) -> None:
        """AUTH obligation with no routes should produce a DescentObstruction."""
        auth_ob = AppObligation(
            kind=ObligationKind.AUTH,
            name="auth",
            data={"strategy": "session"},
            coordinate_name="auth.session",
        )
        presheaf = AppObligationPresheaf(
            prompt="secure app",
            mode=AppMode.FLASK,
            obligations=[auth_ob],
            domain_nouns=[],
            domain_verbs=[],
            ui_metaphors=[],
            auth_required=True,
        )
        result = presheaf.check_satisfiability()
        assert not result.is_success, "Expected failure for AUTH with no routes"
        obstruction = result.unwrap_obstruction()
        assert obstruction is not None

    def test_to_app_spec_seed_has_mode(
        self, recipe_presheaf: AppObligationPresheaf
    ) -> None:
        seed = recipe_presheaf.to_app_spec_seed()
        assert "mode" in seed
        assert seed["mode"] == AppMode.FLASK.value

    def test_to_app_spec_seed_has_routes(
        self, recipe_presheaf: AppObligationPresheaf
    ) -> None:
        seed = recipe_presheaf.to_app_spec_seed()
        assert "routes" in seed
        assert isinstance(seed["routes"], list)
        assert len(seed["routes"]) > 0, "Expected at least one route in the seed"
