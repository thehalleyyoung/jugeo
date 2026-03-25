from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))
from jugeo.webapp.cli.generators.model_generator import ModelGenerator, GeneratedModels

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

SPEC = {
    "name": "recipe_app",
    "mode": "flask",
    "auth_required": True,
    "models": [
        {
            "name": "Recipe",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True, "nullable": False},
                {"name": "title", "type": "String(200)", "nullable": False},
                {"name": "user_id", "type": "Integer", "foreign_key": "users.id", "nullable": False},
            ],
        },
        {
            "name": "User",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True, "nullable": False},
                {"name": "username", "type": "String(80)", "nullable": False},
                {"name": "email", "type": "String(120)", "nullable": False},
            ],
        },
    ],
}


@pytest.fixture(scope="module")
def result() -> GeneratedModels:
    return ModelGenerator().generate(SPEC)


@pytest.fixture(scope="module")
def models_py(result: GeneratedModels) -> str:
    return result.models_py


# ---------------------------------------------------------------------------
# Basic output tests
# ---------------------------------------------------------------------------

def test_generates_models_py(models_py: str) -> None:
    assert isinstance(models_py, str) and len(models_py) > 0


def test_has_sqlalchemy_import(models_py: str) -> None:
    assert "SQLAlchemy" in models_py


def test_recipe_class_present(models_py: str) -> None:
    assert "class Recipe" in models_py


def test_user_class_present(models_py: str) -> None:
    assert "class User" in models_py


# ---------------------------------------------------------------------------
# Schema-constraint tests
# ---------------------------------------------------------------------------

def test_tablename_is_plural(models_py: str) -> None:
    assert "__tablename__ = 'recipes'" in models_py


def test_pk_not_nullable(models_py: str) -> None:
    assert "primary_key=True" in models_py
    # primary_key=True must never appear alongside nullable=True in the same Column().
    assert not re.search(
        r'primary_key=True[^)]*nullable=True|nullable=True[^)]*primary_key=True',
        models_py,
    )


def test_fk_column_has_index(models_py: str) -> None:
    # The user_id FK column must be accompanied by an Index.
    assert "ix_recipes_user_id" in models_py or (
        "user_id" in models_py and "db.Index" in models_py
    )


def test_has_timestamps(models_py: str) -> None:
    assert "created_at" in models_py
    assert "updated_at" in models_py


# ---------------------------------------------------------------------------
# ORM feature tests
# ---------------------------------------------------------------------------

def test_has_to_dict(models_py: str) -> None:
    assert "to_dict" in models_py


def test_has_repr(models_py: str) -> None:
    assert "__repr__" in models_py


def test_relationship_back_populates(models_py: str) -> None:
    assert "back_populates" in models_py


# ---------------------------------------------------------------------------
# Theory annotation / violation tests
# ---------------------------------------------------------------------------

def test_theory_annotations_nonempty(result: GeneratedModels) -> None:
    assert len(result.theory_annotations) > 0


def test_no_violations(result: GeneratedModels) -> None:
    assert result.violations == [], f"Unexpected violations: {result.violations}"


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

def test_user_tablename_is_plural(models_py: str) -> None:
    assert "__tablename__ = 'users'" in models_py


def test_fk_foreign_key_declaration(models_py: str) -> None:
    assert "db.ForeignKey('users.id')" in models_py


def test_index_attr_present(models_py: str) -> None:
    # The index attribute should be declared on the Recipe model.
    assert "ix_recipes_user_id" in models_py


def test_nullable_false_on_title(models_py: str) -> None:
    # Column declaration may contain nested parens (e.g. db.String(200)), so
    # search for the field name and nullable=False anywhere on the same line.
    assert re.search(r"title\s*=\s*db\.Column\(.*nullable=False", models_py)


def test_generated_models_dataclass_fields(result: GeneratedModels) -> None:
    assert hasattr(result, 'models_py')
    assert hasattr(result, 'theory_annotations')
    assert hasattr(result, 'violations')
