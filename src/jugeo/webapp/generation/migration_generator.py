"""Database migration generation — stdlib only."""
from __future__ import annotations

from .models import ModelSpec, ColumnSpec, ColumnType


class MigrationCodeGenerator:
    """Generates SQL migration strings."""

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_init_migration(self, models: list) -> str:
        stmts: list[str] = []
        for model in models:
            stmts.append(self._create_table_sql(model))
        return "\n\n".join(stmts)

    def generate_seed_data(self, models: list) -> str:
        stmts: list[str] = []
        for model in models:
            cols = [c for c in model.columns if isinstance(c, ColumnSpec) and not c.primary_key]
            if not cols:
                continue
            col_names = ", ".join(c.name for c in cols)
            placeholders = ", ".join(self._sample_value(c) for c in cols)
            stmts.append(
                f"INSERT INTO {model.table_name} ({col_names}) VALUES ({placeholders});"
            )
        if not stmts:
            return "-- No seed data generated"
        return "-- Seed data\n" + "\n".join(stmts)

    def generate_migration(self, old_models: list, new_models: list) -> str:
        old_map = {m.name: m for m in old_models}
        new_map = {m.name: m for m in new_models}
        stmts: list[str] = []

        # New tables
        for name, model in new_map.items():
            if name not in old_map:
                stmts.append(self._create_table_sql(model))

        # Altered tables
        for name in old_map:
            if name in new_map:
                diffs = self._diff_models(old_map[name], new_map[name])
                for diff in diffs:
                    if diff["action"] == "add":
                        col = diff["column"]
                        col_type = self._sql_type(col.type.value if isinstance(col.type, ColumnType) else str(col.type))
                        stmts.append(
                            f"ALTER TABLE {new_map[name].table_name} ADD COLUMN {col.name} {col_type};"
                        )
                    elif diff["action"] == "remove":
                        stmts.append(
                            f"-- SQLite does not support DROP COLUMN; "
                            f"column '{diff['column_name']}' removed from {old_map[name].table_name}"
                        )

        # Dropped tables
        for name in old_map:
            if name not in new_map:
                stmts.append(f"DROP TABLE IF EXISTS {old_map[name].table_name};")

        return "\n".join(stmts) if stmts else "-- No migration needed"

    def _diff_models(self, old: ModelSpec, new: ModelSpec) -> list:
        old_cols = {c.name: c for c in old.columns if isinstance(c, ColumnSpec)}
        new_cols = {c.name: c for c in new.columns if isinstance(c, ColumnSpec)}
        diffs: list[dict] = []

        for name, col in new_cols.items():
            if name not in old_cols:
                diffs.append({"action": "add", "column": col, "column_name": name})

        for name in old_cols:
            if name not in new_cols:
                diffs.append({"action": "remove", "column_name": name})

        return diffs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_table_sql(self, model: ModelSpec) -> str:
        cols: list[str] = []
        has_pk = any(
            (c.primary_key if isinstance(c, ColumnSpec) else False)
            for c in model.columns
        )
        if not has_pk:
            cols.append("id INTEGER PRIMARY KEY AUTOINCREMENT")
        for col in model.columns:
            if isinstance(col, ColumnSpec):
                col_type = self._sql_type(col.type.value if isinstance(col.type, ColumnType) else str(col.type))
                parts = [col.name, col_type]
                if col.primary_key:
                    parts.append("PRIMARY KEY")
                    if col.type == ColumnType.INTEGER:
                        parts.append("AUTOINCREMENT")
                if not col.nullable and not col.primary_key:
                    parts.append("NOT NULL")
                if col.unique:
                    parts.append("UNIQUE")
                cols.append(" ".join(parts))
        cols_str = ",\n    ".join(cols)
        return f"CREATE TABLE IF NOT EXISTS {model.table_name} (\n    {cols_str}\n);"

    def _sql_type(self, type_name: str) -> str:
        return self.SQL_TYPES.get(type_name, "TEXT")

    def _sample_value(self, col: ColumnSpec) -> str:
        ct = col.type.value if isinstance(col.type, ColumnType) else str(col.type)
        if ct in ("integer", "float"):
            return "0"
        if ct == "boolean":
            return "0"
        return f"'sample_{col.name}'"
