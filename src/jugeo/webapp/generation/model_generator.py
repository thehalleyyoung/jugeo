"""Generate SQLAlchemy model code and raw SQL DDL — stdlib only."""
from __future__ import annotations

import textwrap
from .models import ModelSpec, ColumnSpec, ColumnType


class ModelCodeGenerator:
    """Produces Flask-SQLAlchemy model class source strings."""

    SQLALCHEMY_TYPES: dict[str, str] = {
        "string": "db.String(255)",
        "integer": "db.Integer",
        "float": "db.Float",
        "boolean": "db.Boolean",
        "text": "db.Text",
        "date": "db.Date",
        "datetime": "db.DateTime",
        "json": "db.JSON",
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_model(self, model: ModelSpec) -> str:
        lines: list[str] = []
        lines.append(f"class {model.name}(db.Model):")
        lines.append(f"    __tablename__ = '{model.table_name}'")
        lines.append("")

        has_pk = any(
            (c.primary_key if isinstance(c, ColumnSpec) else False)
            for c in model.columns
        )
        if not has_pk:
            lines.append("    id = db.Column(db.Integer, primary_key=True)")

        for col in model.columns:
            if isinstance(col, ColumnSpec):
                lines.append(f"    {self._column_definition(col)}")
            else:
                lines.append(f"    # unknown column: {col}")

        lines.append("")
        for rel in model.relationships:
            lines.append(f"    {self._relationship_definition(rel)}")

        lines.append("")
        lines.append(self._generate_repr(model))
        lines.append("")
        lines.append(self._generate_to_dict(model))
        return "\n".join(lines)

    def generate_models_module(self, models: list) -> str:
        lines = [
            "from flask_sqlalchemy import SQLAlchemy",
            "",
            "db = SQLAlchemy()",
            "",
        ]
        for model in models:
            lines.append(self.generate_model(model))
            lines.append("")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _column_definition(self, col: ColumnSpec) -> str:
        type_str = self._type_mapping(col.type.value if isinstance(col.type, ColumnType) else str(col.type))
        parts = [f"db.Column({type_str}"]
        if col.primary_key:
            parts.append("primary_key=True")
        if col.foreign_key:
            parts.append(f"db.ForeignKey('{col.foreign_key}')")
        if not col.nullable and not col.primary_key:
            parts.append("nullable=False")
        if col.unique:
            parts.append("unique=True")
        if col.default is not None:
            parts.append(f"default={col.default!r}")
        return f"{col.name} = {', '.join(parts)})"

    def _relationship_definition(self, rel: dict) -> str:
        if isinstance(rel, dict):
            target = rel.get("target", "Unknown")
            back = rel.get("backref", "")
            lazy = rel.get("lazy", "select")
            if back:
                return f"{target.lower()}s = db.relationship('{target}', backref='{back}', lazy='{lazy}')"
            return f"{target.lower()}s = db.relationship('{target}', lazy='{lazy}')"
        return f"# relationship: {rel}"

    def _type_mapping(self, type_name: str) -> str:
        return self.SQLALCHEMY_TYPES.get(type_name, "db.String(255)")

    def _generate_repr(self, model: ModelSpec) -> str:
        return textwrap.dedent(f"""\
    def __repr__(self):
        return f'<{model.name} {{self.id}}>'""")

    def _generate_to_dict(self, model: ModelSpec) -> str:
        cols = []
        has_pk = any(
            (c.primary_key if isinstance(c, ColumnSpec) else False)
            for c in model.columns
        )
        if not has_pk:
            cols.append("'id': self.id")
        for col in model.columns:
            name = col.name if isinstance(col, ColumnSpec) else str(col)
            cols.append(f"'{name}': self.{name}")
        cols_str = ", ".join(cols)
        return f"    def to_dict(self):\n        return {{{cols_str}}}"


class SchemaGenerator:
    """Generates raw SQL DDL and SQLite init code (no SQLAlchemy)."""

    SQL_TYPES: dict[str, str] = {
        "string": "TEXT",
        "integer": "INTEGER",
        "float": "REAL",
        "boolean": "INTEGER",
        "text": "TEXT",
        "date": "TEXT",
        "datetime": "TEXT",
        "json": "TEXT",
    }

    def generate_schema_sql(self, models: list) -> str:
        stmts: list[str] = []
        for model in models:
            stmts.append(self._create_table(model))
        return "\n\n".join(stmts)

    def generate_init_db(self, models: list) -> str:
        schema = self.generate_schema_sql(models)
        escaped = schema.replace("'", "\\'")
        lines = [
            "import sqlite3",
            "import os",
            "",
            "",
            "def init_db(db_path):",
            "    conn = sqlite3.connect(db_path)",
            "    cursor = conn.cursor()",
        ]
        for model in models:
            ddl = self._create_table(model).replace("'", "\\'")
            lines.append(f"    cursor.execute('''{self._create_table(model)}''')")
        lines.extend([
            "    conn.commit()",
            "    conn.close()",
            "",
            "",
            "if __name__ == '__main__':",
            "    init_db('app.db')",
        ])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_table(self, model: ModelSpec) -> str:
        cols: list[str] = []
        has_pk = any(
            (c.primary_key if isinstance(c, ColumnSpec) else False)
            for c in model.columns
        )
        if not has_pk:
            cols.append("id INTEGER PRIMARY KEY AUTOINCREMENT")
        for col in model.columns:
            if isinstance(col, ColumnSpec):
                col_type = self.SQL_TYPES.get(
                    col.type.value if isinstance(col.type, ColumnType) else str(col.type),
                    "TEXT",
                )
                parts = [col.name, col_type]
                if col.primary_key:
                    parts.append("PRIMARY KEY")
                    if col.type == ColumnType.INTEGER:
                        parts.append("AUTOINCREMENT")
                if not col.nullable and not col.primary_key:
                    parts.append("NOT NULL")
                if col.unique:
                    parts.append("UNIQUE")
                if col.default is not None:
                    parts.append(f"DEFAULT {col.default!r}")
                cols.append(" ".join(parts))
        cols_str = ",\n    ".join(cols)
        return f"CREATE TABLE IF NOT EXISTS {model.table_name} (\n    {cols_str}\n);"

    def _sql_type(self, col_type: str) -> str:
        return self.SQL_TYPES.get(col_type, "TEXT")
