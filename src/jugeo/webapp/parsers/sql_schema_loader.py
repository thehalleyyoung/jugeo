"""
SQL DDL (schema) parser.

Uses regular expressions to extract CREATE TABLE statements (with columns,
constraints), CREATE INDEX statements and foreign-key relationships.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from .models import (
    CoordinateKind,
    ErrorSeverity,
    Language,
    ParsedCoordinate,
    ParsedReference,
    ParseError,
    ParseResult,
    ReferenceType,
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?"
    r"\s+ON\s+[`\"]?(\w+)[`\"]?\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Column definition (simplified)
_COLUMN_RE = re.compile(
    r"^\s*[`\"]?(\w+)[`\"]?\s+([\w()]+(?:\(\d+(?:,\s*\d+)?\))?)"
    r"(.*?)$",
    re.MULTILINE,
)

# Inline REFERENCES
_INLINE_FK_RE = re.compile(
    r"REFERENCES\s+[`\"]?(\w+)[`\"]?\s*\(\s*[`\"]?(\w+)[`\"]?\s*\)",
    re.IGNORECASE,
)

# Table-level FOREIGN KEY constraint
_TABLE_FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*[`\"]?(\w+)[`\"]?\s*\)\s*REFERENCES\s+[`\"]?(\w+)[`\"]?"
    r"\s*\(\s*[`\"]?(\w+)[`\"]?\s*\)",
    re.IGNORECASE,
)

# PRIMARY KEY constraint (table-level)
_TABLE_PK_RE = re.compile(
    r"PRIMARY\s+KEY\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# UNIQUE constraint (table-level)
_TABLE_UNIQUE_RE = re.compile(
    r"UNIQUE\s*\(([^)]+)\)",
    re.IGNORECASE,
)

_NOT_NULL_RE = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
_DEFAULT_RE = re.compile(r"\bDEFAULT\s+(\S+)", re.IGNORECASE)
_PK_INLINE_RE = re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE)

# SQL keywords that start constraint lines (not columns)
_CONSTRAINT_START = re.compile(
    r"^\s*(?:PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CONSTRAINT|CHECK)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _line_number_at(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


def _split_column_defs(body: str) -> list[str]:
    """Split a table body by commas, respecting parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


# ---------------------------------------------------------------------------
# SQLSchemaParser
# ---------------------------------------------------------------------------

class SQLSchemaParser:
    """Parse SQL DDL and extract table/column/constraint/index coordinates."""

    def parse(self, source: str, file_path: str) -> ParseResult:
        t0 = time.monotonic()
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        errors: list[ParseError] = []

        try:
            for m in _CREATE_TABLE_RE.finditer(source):
                table_name = m.group(1)
                body = m.group(2)
                line = _line_number_at(source, m.start())
                end_line = _line_number_at(source, m.end())
                cid = _make_coord_id(file_path, CoordinateKind.DB_TABLE.value, table_name, line)
                table_coord = ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.DB_TABLE,
                    name=table_name,
                    file_path=file_path,
                    line_number=line,
                    end_line=end_line,
                    language=Language.SQL,
                    metadata={"table_name": table_name},
                )
                coords.append(table_coord)
                col_coords, col_refs = self._extract_columns(body, table_coord, file_path)
                coords.extend(col_coords)
                refs.extend(col_refs)
                con_coords, con_refs = self._extract_constraints(body, table_coord, file_path)
                coords.extend(con_coords)
                refs.extend(con_refs)

            idx_coords = self._extract_indexes(source, file_path)
            coords.extend(idx_coords)
        except Exception as exc:  # noqa: BLE001
            errors.append(ParseError(
                file_path=file_path,
                line_number=0,
                message=f"SQL parse error: {exc}",
                severity=ErrorSeverity.ERROR,
            ))

        elapsed = (time.monotonic() - t0) * 1000
        return ParseResult(
            file_path=file_path,
            language=Language.SQL,
            coordinates=coords,
            references=refs,
            errors=errors,
            parse_time_ms=elapsed,
        )

    # -- columns -------------------------------------------------------------

    def _extract_columns(
        self, body: str, table_coord: ParsedCoordinate, file_path: str,
    ) -> tuple[list[ParsedCoordinate], list[ParsedReference]]:
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        parts = _split_column_defs(body)
        for part in parts:
            part_stripped = part.strip()
            if _CONSTRAINT_START.match(part_stripped):
                continue
            col_m = re.match(
                r"[`\"]?(\w+)[`\"]?\s+([\w]+(?:\(\d+(?:,\s*\d+)?\))?)(.*)",
                part_stripped,
                re.IGNORECASE | re.DOTALL,
            )
            if not col_m:
                continue
            col_name = col_m.group(1)
            col_type = col_m.group(2).upper()
            rest = col_m.group(3)

            nullable = not bool(_NOT_NULL_RE.search(rest))
            default_m = _DEFAULT_RE.search(rest)
            default_val = default_m.group(1) if default_m else None
            is_pk = bool(_PK_INLINE_RE.search(rest))

            full_name = f"{table_coord.name}.{col_name}"
            cid = _make_coord_id(
                file_path, CoordinateKind.DB_COLUMN.value,
                full_name, table_coord.line_number,
            )
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.DB_COLUMN,
                name=full_name,
                file_path=file_path,
                line_number=table_coord.line_number,
                end_line=table_coord.end_line,
                language=Language.SQL,
                metadata={
                    "column_name": col_name,
                    "column_type": col_type,
                    "nullable": nullable,
                    "default": default_val,
                    "primary_key": is_pk,
                    "table": table_coord.name,
                },
            ))

            # Inline FK
            fk_m = _INLINE_FK_RE.search(rest)
            if fk_m:
                ref_table = fk_m.group(1)
                ref_col = fk_m.group(2)
                sid = _make_coord_id(file_path, "fk", full_name, table_coord.line_number)
                refs.append(ParsedReference(
                    source_id=sid,
                    target_name=f"{ref_table}.{ref_col}",
                    reference_type=ReferenceType.FK_REFERENCE,
                    file_path=file_path,
                    line_number=table_coord.line_number,
                    metadata={
                        "from_table": table_coord.name,
                        "from_column": col_name,
                        "to_table": ref_table,
                        "to_column": ref_col,
                    },
                ))

        return coords, refs

    # -- constraints ---------------------------------------------------------

    def _extract_constraints(
        self, body: str, table_coord: ParsedCoordinate, file_path: str,
    ) -> tuple[list[ParsedCoordinate], list[ParsedReference]]:
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []

        # Table-level PRIMARY KEY
        for m in _TABLE_PK_RE.finditer(body):
            cols_str = m.group(1)
            cols = [c.strip().strip("`\"") for c in cols_str.split(",")]
            name = f"{table_coord.name}::pk"
            cid = _make_coord_id(file_path, CoordinateKind.DB_CONSTRAINT.value, name, table_coord.line_number)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.DB_CONSTRAINT,
                name=name,
                file_path=file_path,
                line_number=table_coord.line_number,
                end_line=table_coord.end_line,
                language=Language.SQL,
                metadata={"constraint_type": "PRIMARY KEY", "columns": cols, "table": table_coord.name},
            ))

        # Table-level UNIQUE
        for m in _TABLE_UNIQUE_RE.finditer(body):
            cols_str = m.group(1)
            cols = [c.strip().strip("`\"") for c in cols_str.split(",")]
            name = f"{table_coord.name}::unique({','.join(cols)})"
            cid = _make_coord_id(file_path, CoordinateKind.DB_CONSTRAINT.value, name, table_coord.line_number)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.DB_CONSTRAINT,
                name=name,
                file_path=file_path,
                line_number=table_coord.line_number,
                end_line=table_coord.end_line,
                language=Language.SQL,
                metadata={"constraint_type": "UNIQUE", "columns": cols, "table": table_coord.name},
            ))

        # Table-level FOREIGN KEY
        for m in _TABLE_FK_RE.finditer(body):
            from_col = m.group(1)
            to_table = m.group(2)
            to_col = m.group(3)
            name = f"{table_coord.name}::fk({from_col})"
            cid = _make_coord_id(file_path, CoordinateKind.DB_CONSTRAINT.value, name, table_coord.line_number)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.DB_CONSTRAINT,
                name=name,
                file_path=file_path,
                line_number=table_coord.line_number,
                end_line=table_coord.end_line,
                language=Language.SQL,
                metadata={
                    "constraint_type": "FOREIGN KEY",
                    "from_column": from_col,
                    "to_table": to_table,
                    "to_column": to_col,
                    "table": table_coord.name,
                },
            ))
            sid = _make_coord_id(file_path, "fk_tbl", f"{table_coord.name}.{from_col}", table_coord.line_number)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=f"{to_table}.{to_col}",
                reference_type=ReferenceType.FK_REFERENCE,
                file_path=file_path,
                line_number=table_coord.line_number,
                metadata={
                    "from_table": table_coord.name,
                    "from_column": from_col,
                    "to_table": to_table,
                    "to_column": to_col,
                },
            ))

        return coords, refs

    # -- indexes -------------------------------------------------------------

    def _extract_indexes(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _CREATE_INDEX_RE.finditer(source):
            idx_name = m.group(1)
            table = m.group(2)
            cols_str = m.group(3)
            cols = [c.strip().strip("`\"") for c in cols_str.split(",")]
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.DB_INDEX.value, idx_name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.DB_INDEX,
                name=idx_name,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.SQL,
                metadata={"table": table, "columns": cols},
            ))
        return coords


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_sql_coordinates(source: str, file_path: str) -> ParseResult:
    """Convenience wrapper around ``SQLSchemaParser.parse``."""
    return SQLSchemaParser().parse(source, file_path)


def extract_tables(source: str) -> list[dict]:
    """Return a list of table dicts with *name* and *columns*.

    Each column dict has keys: name, type, nullable, default.
    """
    tables: list[dict] = []
    for m in _CREATE_TABLE_RE.finditer(source):
        table_name = m.group(1)
        body = m.group(2)
        columns: list[dict] = []
        parts = _split_column_defs(body)
        for part in parts:
            part_stripped = part.strip()
            if _CONSTRAINT_START.match(part_stripped):
                continue
            col_m = re.match(
                r"[`\"]?(\w+)[`\"]?\s+([\w]+(?:\(\d+(?:,\s*\d+)?\))?)(.*)",
                part_stripped,
                re.IGNORECASE | re.DOTALL,
            )
            if not col_m:
                continue
            col_name = col_m.group(1)
            col_type = col_m.group(2).upper()
            rest = col_m.group(3)
            nullable = not bool(_NOT_NULL_RE.search(rest))
            default_m = _DEFAULT_RE.search(rest)
            default_val = default_m.group(1) if default_m else None
            columns.append({
                "name": col_name,
                "type": col_type,
                "nullable": nullable,
                "default": default_val,
            })
        tables.append({"name": table_name, "columns": columns})
    return tables


def extract_foreign_keys(source: str) -> list[dict]:
    """Return a list of FK dicts with from_table, from_column, to_table, to_column."""
    fks: list[dict] = []
    for m in _CREATE_TABLE_RE.finditer(source):
        table_name = m.group(1)
        body = m.group(2)

        # Table-level FK
        for fk in _TABLE_FK_RE.finditer(body):
            fks.append({
                "from_table": table_name,
                "from_column": fk.group(1),
                "to_table": fk.group(2),
                "to_column": fk.group(3),
            })

        # Inline FK in column defs
        parts = _split_column_defs(body)
        for part in parts:
            part_stripped = part.strip()
            if _CONSTRAINT_START.match(part_stripped):
                continue
            col_m = re.match(r"[`\"]?(\w+)[`\"]?\s+", part_stripped)
            if not col_m:
                continue
            col_name = col_m.group(1)
            fk_m = _INLINE_FK_RE.search(part_stripped)
            if fk_m:
                fks.append({
                    "from_table": table_name,
                    "from_column": col_name,
                    "to_table": fk_m.group(1),
                    "to_column": fk_m.group(2),
                })

    return fks
