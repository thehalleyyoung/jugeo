"""
SQL ORM as a Functor Between Relational and Object Sites
=========================================================

This module models an Object-Relational Mapper (ORM) as a functor

    F : Rel → Obj

where

* **Rel** is the *relational site*: objects are SQL tables, morphisms are
  JOIN conditions (foreign-key constraints), and covers are sets of joins
  that collectively expose a coherent row view.

* **Obj** is the *Python object-graph site*: objects are Python classes,
  morphisms are attribute traversals (``user.posts``), and covers are sets
  of eager-load paths that yield a coherent in-memory object tree.

FUNCTOR FIDELITY STATEMENT
--------------------------
The ORM functor is faithful on column data — scalar fields round-trip
faithfully between the two sites.  It is **not full**: not every Python
attribute traversal corresponds to a valid SQL JOIN (virtual/computed
properties have no relational preimage).

THE N+1 PROBLEM AS A FUNCTOR FAILURE
-------------------------------------
The N+1 query problem is precisely a failure of the ORM functor to compose
morphisms lazily vs. eagerly in a coherent way.

In categorical terms: given a composed morphism

    users ──fk──> posts ──fk──> comments

the ORM functor should map this to a **single** eagerly-loaded traversal

    user.posts[i].comments

But lazy loading breaks composition into N+1 individual DB round-trips —
one for the outer query (``SELECT * FROM users``) and N for each
``SELECT * FROM posts WHERE user_id = ?``.

This is a failure of naturality: the square

    Query(users ∘ posts)   ──F──>   user.posts
           |                              |
         eager                         access
           |                              |
    Query(users) × N·Query(posts)  ──F──>  user + N·posts

does not commute when the ORM defaults to lazy loading.

The fix — ``joinedload`` / ``selectinload`` / ``subqueryload`` — restores
naturality by re-composing the relational morphisms before applying the
functor.

DESCENT INTERPRETATION
-----------------------
Schema consistency is a descent problem: a set of local models (one per
table) must glue to a global object graph.  ``ORMFunctorChecker`` checks
the cocycle conditions (FK targets exist, PK present, etc.) and returns a
``DescentResult`` — either a ``GlobalSection`` (schema is coherent) or a
``DescentObstruction`` (inconsistency found).
"""

from __future__ import annotations

__all__ = [
    "RelationshipKind",
    "ORMColumn",
    "ORMModel",
    "NPlusOnePattern",
    "ORMQueryAnalyzer",
    "MigrationDiff",
    "ORMFunctorChecker",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.descent import LocalSection, DescentResult, DescentObstruction, GlobalSection


# ---------------------------------------------------------------------------
# 1. RelationshipKind
# ---------------------------------------------------------------------------

class RelationshipKind(str, Enum):
    """Cardinality of an ORM relationship — i.e. the type of relational morphism."""

    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"

    def requires_join(self) -> bool:
        """All relationship kinds require a SQL JOIN to traverse."""
        return True

    def is_collection(self) -> bool:
        """True when the Python-side value is a list / collection."""
        return self in (RelationshipKind.ONE_TO_MANY, RelationshipKind.MANY_TO_MANY)


# ---------------------------------------------------------------------------
# 2. ORMColumn
# ---------------------------------------------------------------------------

_VALID_COLUMN_TYPES = frozenset({"TEXT", "INTEGER", "REAL", "BLOB", "BOOLEAN", "JSON"})


@dataclass
class ORMColumn:
    """A single column in a SQL table, along with its ORM metadata."""

    name: str
    column_type: str  # TEXT | INTEGER | REAL | BLOB | BOOLEAN | JSON
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str | None = None  # "other_table.column_name"
    indexed: bool = False

    def __post_init__(self) -> None:
        if self.column_type not in _VALID_COLUMN_TYPES:
            raise ValueError(
                f"Unknown column_type {self.column_type!r}. "
                f"Expected one of: {sorted(_VALID_COLUMN_TYPES)}"
            )
        if self.primary_key:
            # Primary keys are implicitly NOT NULL in SQL
            object.__setattr__(self, "nullable", False)  # safe even without frozen
            self.nullable = False

    def _sql_type_fragment(self) -> str:
        """Return the SQL type declaration fragment for this column."""
        parts: list[str] = [self.column_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable and not self.primary_key:
            parts.append("NOT NULL")
        if self.foreign_key:
            table, col = self.foreign_key.rsplit(".", 1)
            parts.append(f"REFERENCES {table}({col})")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# 3. ORMModel
# ---------------------------------------------------------------------------

@dataclass
class ORMModel:
    """A Python class mapped to a SQL table — one object in the ORM functor's domain."""

    class_name: str
    table_name: str
    columns: list[ORMColumn]
    relationships: dict[str, tuple[RelationshipKind, str]]
    # attr_name → (kind, target_model_class_name)

    # ------------------------------------------------------------------
    # Column accessors
    # ------------------------------------------------------------------

    def primary_key_column(self) -> ORMColumn | None:
        """Return the first primary-key column, or None if absent."""
        for col in self.columns:
            if col.primary_key:
                return col
        return None

    def foreign_key_columns(self) -> list[ORMColumn]:
        """Return all columns that carry a foreign-key constraint."""
        return [col for col in self.columns if col.foreign_key is not None]

    def required_columns(self) -> list[ORMColumn]:
        """Columns that are NOT NULL and are not the primary key.

        These must be supplied explicitly on INSERT.
        """
        return [
            col for col in self.columns
            if not col.nullable and not col.primary_key
        ]

    # ------------------------------------------------------------------
    # SQL generation
    # ------------------------------------------------------------------

    def to_create_table_sql(self) -> str:
        """Generate a ``CREATE TABLE IF NOT EXISTS`` statement for this model."""
        col_defs: list[str] = []
        fk_clauses: list[str] = []

        for col in self.columns:
            parts: list[str] = [f"    {col.name} {col.column_type}"]
            if col.primary_key:
                parts.append("PRIMARY KEY")
            if not col.nullable and not col.primary_key:
                parts.append("NOT NULL")
            col_defs.append(" ".join(parts))

            if col.foreign_key:
                ref_table, ref_col = col.foreign_key.rsplit(".", 1)
                fk_clauses.append(
                    f"    FOREIGN KEY ({col.name}) REFERENCES {ref_table}({ref_col})"
                )

        all_defs = col_defs + fk_clauses
        body = ",\n".join(all_defs)
        return f"CREATE TABLE IF NOT EXISTS {self.table_name} (\n{body}\n);"

    # ------------------------------------------------------------------
    # Descent / sheaf integration
    # ------------------------------------------------------------------

    def to_local_section(self, db_coord: str) -> LocalSection:
        """Represent this model as a local section over the given DB coordinate.

        The ``judgment_data`` encodes the table schema in a form that can
        be glued with other local sections by ``ORMFunctorChecker``.
        """
        col_info = {
            col.name: {
                "type": col.column_type,
                "nullable": col.nullable,
                "primary_key": col.primary_key,
                "foreign_key": col.foreign_key,
                "indexed": col.indexed,
            }
            for col in self.columns
        }
        rel_info = {
            attr: {"kind": kind.value, "target": target}
            for attr, (kind, target) in self.relationships.items()
        }
        return LocalSection(
            coordinate=f"{db_coord}/{self.table_name}",
            judgment_data={
                "class_name": self.class_name,
                "table_name": self.table_name,
                "columns": col_info,
                "relationships": rel_info,
            },
            evidence_bundle=(f"orm_model:{self.class_name}",),
            trust_level=1.0,
            provenance=(db_coord,),
            is_partial=False,
        )


# ---------------------------------------------------------------------------
# 4. NPlusOnePattern
# ---------------------------------------------------------------------------

@dataclass
class NPlusOnePattern:
    """A detected N+1 query risk in Python ORM code.

    The N+1 problem is a failure of the ORM functor to compose eager-loading
    morphisms: instead of a single JOIN, the functor emits N+1 SQL queries.
    """

    query_location: str          # file:line or function name
    model_name: str
    accessed_relationship: str
    severity: str = "warning"    # "warning" | "error"
    recommendation: str = ""

    def __post_init__(self) -> None:
        if self.severity not in ("warning", "error"):
            raise ValueError(f"severity must be 'warning' or 'error', got {self.severity!r}")
        if not self.recommendation:
            self.recommendation = (
                f"Use joinedload({self.model_name}.{self.accessed_relationship}) "
                f"or selectinload({self.model_name}.{self.accessed_relationship}) "
                "to eliminate the N+1 query."
            )


# ---------------------------------------------------------------------------
# 5. ORMQueryAnalyzer
# ---------------------------------------------------------------------------

# Regex patterns for N+1 detection
# ─────────────────────────────────
# Pattern A: list/generator comprehension that calls .query.all() and accesses
#            a second attribute on each element:
#            [x.something for x in Model.query.all()]
_RE_COMPREHENSION_N1 = re.compile(
    r"\[\s*(\w+)\s*\.\s*(\w+)"           # [x.attr
    r".*?"                                # (anything)
    r"for\s+\w+\s+in\s+"                 # for x in
    r"\w+\s*\.\s*query\s*\.\s*all\(\)",  # Model.query.all()
    re.DOTALL,
)

# Pattern B: for-loop over .query.all() / .all() / session.query(…).all()
#            followed on the next non-blank line by variable.attribute access
_RE_FOR_LOOP_QUERY = re.compile(
    r"for\s+(\w+)\s+in\s+.*?\.all\(\)\s*:",  # for x in …query….all():
    re.DOTALL,
)

# Pattern C: explicit for-loop body that accesses x.<relationship>
_RE_LOOP_BODY_ATTR = re.compile(
    r"\.(\w+)",  # any attribute access — we filter by context
)

# Pattern D: session.query(…).all() followed by an attribute access chain
_RE_SESSION_QUERY = re.compile(
    r"session\s*\.\s*query\s*\(\s*(\w+)\s*\)\s*(?:\.\s*\w+\s*(?:\([^)]*\)\s*)?)*.all\(\)",
)


@dataclass
class ORMQueryAnalyzer:
    """Static analyser that detects N+1 risks and schema issues in ORM usage."""

    # ------------------------------------------------------------------
    # N+1 detection
    # ------------------------------------------------------------------

    def detect_n_plus_one(self, python_code: str) -> list[NPlusOnePattern]:
        """Scan *python_code* for common N+1 anti-patterns.

        Detected patterns
        -----------------
        1. List comprehension ``[x.attr for x in Model.query.all()]`` where
           ``attr`` is a second-level attribute (potential relationship).
        2. ``for x in query.all():`` loop where the loop body accesses
           ``x.<something>`` that looks like a relationship (lower_case, not
           a dunder or built-in).
        3. ``session.query(Model).all()`` followed by per-item attribute
           access in a loop.
        """
        patterns: list[NPlusOnePattern] = []

        # ── Pattern 1: comprehension N+1 ──────────────────────────────
        for match in _RE_COMPREHENSION_N1.finditer(python_code):
            var_name = match.group(1)
            attr_name = match.group(2)
            line_no = python_code[: match.start()].count("\n") + 1
            patterns.append(
                NPlusOnePattern(
                    query_location=f"line:{line_no}",
                    model_name=var_name,
                    accessed_relationship=attr_name,
                    severity="warning",
                    recommendation=(
                        f"Use eager loading (joinedload / selectinload) for "
                        f"'{attr_name}' to avoid N+1 queries in comprehension."
                    ),
                )
            )

        # ── Pattern 2: for-loop over .all() with body attribute access ─
        lines = python_code.splitlines()
        for i, line in enumerate(lines):
            m = _RE_FOR_LOOP_QUERY.search(line)
            if not m:
                continue
            loop_var = m.group(1)
            line_no = i + 1
            # Scan the next 20 lines for loop_var.<attr> accesses
            body_lines = lines[i + 1 : i + 21]
            for j, body_line in enumerate(body_lines):
                # Stop if we hit a dedented line (end of loop body)
                stripped = body_line.lstrip()
                if stripped and not body_line.startswith((" ", "\t")):
                    break
                # Look for loop_var.attr or loop_var.attr.sub_attr
                body_re = re.compile(
                    r"\b" + re.escape(loop_var) + r"\s*\.\s*([a-z_]\w*)"
                )
                for bm in body_re.finditer(body_line):
                    attr = bm.group(1)
                    # Exclude common non-relationship attributes
                    if attr in ("id", "pk", "created_at", "updated_at",
                                "append", "extend", "pop", "items", "keys",
                                "values", "get", "update", "copy"):
                        continue
                    patterns.append(
                        NPlusOnePattern(
                            query_location=f"line:{line_no + j + 1}",
                            model_name=loop_var,
                            accessed_relationship=attr,
                            severity="warning",
                            recommendation=(
                                f"Use joinedload({loop_var}.{attr}) or "
                                f"selectinload({loop_var}.{attr}) on the outer "
                                "query to collapse N+1 into a single JOIN."
                            ),
                        )
                    )

        # Deduplicate by (location, relationship) — keep first occurrence
        seen: set[tuple[str, str]] = set()
        unique: list[NPlusOnePattern] = []
        for p in patterns:
            key = (p.query_location, p.accessed_relationship)
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    # ------------------------------------------------------------------
    # Schema checks
    # ------------------------------------------------------------------

    def check_missing_indexes(self, models: list[ORMModel]) -> list[str]:
        """Warn about foreign-key columns that lack an index.

        In the relational site, every FK morphism should have an indexed
        domain to make the JOIN morphism efficient.  A missing index is a
        performance defect — the functor still *works*, but at O(N) cost
        instead of O(log N).
        """
        warnings: list[str] = []
        for model in models:
            for col in model.foreign_key_columns():
                if not col.indexed:
                    warnings.append(
                        f"{model.table_name}.{col.name}: foreign key column "
                        f"references '{col.foreign_key}' but has no index. "
                        f"Add indexed=True or CREATE INDEX on this column."
                    )
        return warnings

    def check_nullable_pks(self, models: list[ORMModel]) -> list[str]:
        """Warn about primary-key columns that are marked nullable.

        A nullable PK violates the entity-integrity constraint — the identity
        morphism of every row in Rel requires a non-null, unique key.
        """
        errors: list[str] = []
        for model in models:
            pk = model.primary_key_column()
            if pk is not None and pk.nullable:
                errors.append(
                    f"{model.table_name}.{pk.name}: primary key column "
                    "must not be nullable."
                )
        return errors


# ---------------------------------------------------------------------------
# 6. MigrationDiff
# ---------------------------------------------------------------------------

@dataclass
class MigrationDiff:
    """A schema evolution step — a morphism in the category of DB schemas.

    In the functor picture, a migration is a natural transformation between
    two versions of the ORM functor: F_v1 → F_v2.  Destructive migrations
    (DROP TABLE / DROP COLUMN) are not invertible — they break the
    adjunction between schema versions.
    """

    migration_id: str
    adds_tables: list[str] = field(default_factory=list)
    drops_tables: list[str] = field(default_factory=list)
    adds_columns: list[tuple[str, str]] = field(default_factory=list)
    # (table_name, column_definition)
    drops_columns: list[tuple[str, str]] = field(default_factory=list)
    # (table_name, column_name)

    def is_destructive(self) -> bool:
        """True when this migration drops tables or columns (non-invertible)."""
        return bool(self.drops_tables or self.drops_columns)

    def sql_statements(self) -> list[str]:
        """Generate the SQL DDL statements for this migration step."""
        stmts: list[str] = []

        for table in self.adds_tables:
            # Caller is expected to pass a complete CREATE TABLE statement here;
            # if they pass just a name we emit a minimal placeholder.
            if table.strip().upper().startswith("CREATE"):
                stmts.append(table if table.rstrip().endswith(";") else table + ";")
            else:
                stmts.append(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY);")

        for table, col_def in self.adds_columns:
            stmts.append(f"ALTER TABLE {table} ADD COLUMN {col_def};")

        for table in self.drops_tables:
            stmts.append(f"DROP TABLE IF EXISTS {table};")

        for table, col_name in self.drops_columns:
            stmts.append(f"ALTER TABLE {table} DROP COLUMN {col_name};")

        return stmts


# ---------------------------------------------------------------------------
# 7. ORMFunctorChecker
# ---------------------------------------------------------------------------

@dataclass
class ORMFunctorChecker:
    """Verify that the ORM functor is well-defined on the given set of models.

    In descent terms, a collection of ``ORMModel`` instances forms a *cover*
    of the database schema.  The functor is well-defined iff the cover
    satisfies the descent (cocycle) conditions:

    * Every FK column's target ``table.column`` exists in the cover.
    * Every model has a primary key (identity morphism).
    * Columns named ``id`` are primary keys (by convention).
    * Every relationship target is present in the cover.

    ``check_model_integrity`` returns a ``DescentResult``:
    - ``DescentResult.success(GlobalSection(...))`` when all conditions hold.
    - ``DescentResult.failure(DescentObstruction(...))`` listing violations.
    """

    # ------------------------------------------------------------------
    # Integrity check → DescentResult
    # ------------------------------------------------------------------

    def check_model_integrity(self, models: list[ORMModel]) -> DescentResult:
        """Check FK targets, PK presence, and 'id' column convention.

        Returns a ``DescentResult`` encoding success or failure as a
        sheaf-theoretic descent computation.
        """
        table_map: dict[str, ORMModel] = {m.table_name: m for m in models}
        violations: list[str] = []

        for model in models:
            # ── PK check ──────────────────────────────────────────────
            pk = model.primary_key_column()
            if pk is None:
                violations.append(
                    f"{model.class_name} ({model.table_name}): no primary key defined."
                )

            # ── FK target existence ───────────────────────────────────
            for col in model.foreign_key_columns():
                if col.foreign_key is None:
                    continue
                parts = col.foreign_key.rsplit(".", 1)
                if len(parts) != 2:
                    violations.append(
                        f"{model.table_name}.{col.name}: malformed foreign_key "
                        f"'{col.foreign_key}' (expected 'table.column')."
                    )
                    continue
                ref_table, ref_col = parts
                if ref_table not in table_map:
                    violations.append(
                        f"{model.table_name}.{col.name}: FK references unknown "
                        f"table '{ref_table}'."
                    )
                    continue
                ref_model = table_map[ref_table]
                ref_col_names = {c.name for c in ref_model.columns}
                if ref_col not in ref_col_names:
                    violations.append(
                        f"{model.table_name}.{col.name}: FK references unknown "
                        f"column '{ref_col}' in table '{ref_table}'."
                    )

            # ── 'id' convention ───────────────────────────────────────
            for col in model.columns:
                if col.name == "id" and not col.primary_key:
                    violations.append(
                        f"{model.table_name}.id: column named 'id' must be "
                        "primary_key=True by convention."
                    )

        if violations:
            obstruction = DescentObstruction(
                coordinate="orm_schema",
                partial_section={
                    "models_checked": [m.class_name for m in models],
                    "violations": violations,
                },
            )
            return DescentResult.failure(obstruction)

        # Build a GlobalSection summarising the validated schema
        merged: dict[str, Any] = {
            m.table_name: {
                "class_name": m.class_name,
                "column_count": len(m.columns),
                "relationship_count": len(m.relationships),
            }
            for m in models
        }
        global_sec = GlobalSection(
            coordinate="orm_schema",
            merged_judgment=merged,
            constituent_sections=tuple(m.class_name for m in models),
            certificate="orm_functor_integrity_ok",
        )
        return DescentResult.success(global_sec)

    # ------------------------------------------------------------------
    # Relationship consistency (soft check — returns warnings, not DescentResult)
    # ------------------------------------------------------------------

    def check_relationship_consistency(self, models: list[ORMModel]) -> list[str]:
        """Check that every relationship target is a known model class name.

        Returns a list of human-readable warning strings.
        """
        known_classes: set[str] = {m.class_name for m in models}
        warnings: list[str] = []

        for model in models:
            for attr_name, (kind, target_class) in model.relationships.items():
                if target_class not in known_classes:
                    warnings.append(
                        f"{model.class_name}.{attr_name}: relationship target "
                        f"'{target_class}' is not present in the provided model list. "
                        f"Functor morphism '{model.table_name} --{kind.value}--> {target_class}' "
                        "has no codomain object."
                    )
        return warnings
