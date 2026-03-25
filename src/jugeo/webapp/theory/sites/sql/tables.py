"""
SQL Schema as a Grothendieck Site
==================================

This module models a relational SQL schema using the language of Grothendieck
sites and categorical geometry.  The core dictionary is:

* **Objects** → tables, views, and CTEs (``SQLTable``), each lifted to a
  ``Coordinate`` in a ``Site``.
* **Morphisms** → foreign-key relationships.  A FK from child column *c* to
  parent column *p* is a genuine categorical arrow: a *restriction* morphism
  that sends a child row to the unique parent row it depends on.
* **JOINs** → fiber products (pullbacks).  An INNER JOIN of tables *A* and *B*
  on a shared column is *exactly* the categorical fiber product of *A* and *B*
  over their common range — see ``SQLRelationalSite.join_fiber_product``.
* **Covering families** → a table's indexes provide a covering: together they
  expose every row for query access, covering the table object in the site's
  Grothendieck topology.

The design keeps all SQL logic in plain Python dataclasses and delegates the
categorical bookkeeping to the ``jugeo.geometry`` layer.

References
----------
* SGA 4 — Théorie des Topos et Cohomologie Étale des Schémas
* Johnstone, *Sketches of an Elephant*, Part C
"""

from __future__ import annotations

__all__ = [
    "SQLColumnKind",
    "SQLColumn",
    "SQLTable",
    "SQLRelationalSite",
    "QuerySection",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
)
from jugeo.geometry.descent import LocalSection, DescentResult


# ---------------------------------------------------------------------------
# 1. SQLColumnKind
# ---------------------------------------------------------------------------


class SQLColumnKind(str, Enum):
    """Type classification for a single SQL column.

    The ``FOREIGN_KEY`` and ``PRIMARY_KEY`` kinds overlap with the structural
    role captured by ``SQLColumn.references`` and ``SQLTable.primary_key``; they
    are kept here for convenience so that callers can tag a column by intent
    without inspecting the parent table.
    """

    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    TEXT = "TEXT"
    VARCHAR = "VARCHAR"
    BOOLEAN = "BOOLEAN"
    FLOAT = "FLOAT"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    DATETIME = "DATETIME"
    TIMESTAMP = "TIMESTAMP"
    JSON = "JSON"
    BLOB = "BLOB"
    UUID = "UUID"
    ENUM_TYPE = "ENUM_TYPE"
    FOREIGN_KEY = "FOREIGN_KEY"
    PRIMARY_KEY = "PRIMARY_KEY"
    COMPUTED = "COMPUTED"


# ---------------------------------------------------------------------------
# 2. SQLColumn
# ---------------------------------------------------------------------------


@dataclass
class SQLColumn:
    """A single column in an SQL table or view.

    Parameters
    ----------
    name:
        Column identifier as it appears in DDL.
    kind:
        Data-type classification from ``SQLColumnKind``.
    nullable:
        Whether the column accepts NULL.  Defaults to ``True`` (SQL standard).
    default:
        Optional default value expression.
    unique:
        Whether a UNIQUE constraint exists on this column alone.
    references:
        ``(table_name, column_name)`` pair naming the FK target, or ``None``
        for non-referencing columns.
    check_constraint:
        Raw SQL expression for a CHECK constraint, e.g. ``"age >= 0"``.
    """

    name: str
    kind: SQLColumnKind
    nullable: bool = True
    default: Any | None = None
    unique: bool = False
    references: tuple[str, str] | None = None
    check_constraint: str | None = None

    @property
    def is_foreign_key(self) -> bool:
        """True when this column carries a FK reference."""
        return self.references is not None

    def __repr__(self) -> str:  # pragma: no cover
        nullable_str = "NULL" if self.nullable else "NOT NULL"
        ref_str = f" -> {self.references[0]}.{self.references[1]}" if self.references else ""
        return f"SQLColumn({self.name!r}: {self.kind.value} {nullable_str}{ref_str})"


# ---------------------------------------------------------------------------
# 3. SQLTable
# ---------------------------------------------------------------------------


@dataclass
class SQLTable:
    """A table, view, or CTE treated as an object in the relational site.

    Parameters
    ----------
    name:
        Identifier used throughout the site (must be unique).
    columns:
        Ordered list of column definitions.
    primary_key:
        List of column names that form the primary key.  May be empty for
        views without a natural key.
    indexes:
        List of ``(index_name, [col, ...])`` pairs.  Each index becomes a
        morphism in the covering family produced by ``index_covering``.
    is_view:
        ``True`` for SQL views and CTEs.
    view_query:
        Raw SQL defining the view body, populated only when ``is_view=True``.
    """

    name: str
    columns: list[SQLColumn]
    primary_key: list[str] = field(default_factory=list)
    indexes: list[tuple[str, list[str]]] = field(default_factory=list)
    is_view: bool = False
    view_query: str | None = None

    # ------------------------------------------------------------------
    # Conversion to site geometry
    # ------------------------------------------------------------------

    def to_coordinate(self) -> Coordinate:
        """Lift this table to a ``Coordinate`` in the relational site.

        Tables are modelled as ``CoordinateKind.MODULE`` coordinates: each
        table is a self-contained namespace of typed data, analogous to a
        module in a codebase.  Views receive the same kind — they present the
        same interface regardless of whether they are materialized.

        The ``metadata`` dictionary carries SQL-specific information so that
        site consumers can reconstruct the DDL context without keeping a
        separate registry.
        """
        meta: dict[str, Any] = {
            "is_view": self.is_view,
            "primary_key": list(self.primary_key),
            "column_count": len(self.columns),
            "index_count": len(self.indexes),
        }
        if self.is_view and self.view_query is not None:
            meta["view_query"] = self.view_query
        return Coordinate(
            components=self.name,
            kind=CoordinateKind.MODULE,
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # Structural helpers
    # ------------------------------------------------------------------

    def foreign_keys(self) -> list[tuple[str, str, str]]:
        """Return every FK in this table as ``(this_col, ref_table, ref_col)``."""
        result: list[tuple[str, str, str]] = []
        for col in self.columns:
            if col.references is not None:
                ref_table, ref_col = col.references
                result.append((col.name, ref_table, ref_col))
        return result

    def column(self, name: str) -> SQLColumn | None:
        """Look up a column by name, returning ``None`` if absent."""
        for col in self.columns:
            if col.name == name:
                return col
        return None

    def __repr__(self) -> str:  # pragma: no cover
        kind_str = "VIEW" if self.is_view else "TABLE"
        return f"SQLTable({kind_str} {self.name!r}, {len(self.columns)} columns)"


# ---------------------------------------------------------------------------
# 4. SQLRelationalSite
# ---------------------------------------------------------------------------


class SQLRelationalSite:
    """A Grothendieck site whose objects are SQL tables and morphisms are FKs.

    Category structure
    ------------------
    * **Objects** — every ``SQLTable`` is a coordinate in the underlying
      ``Site``.
    * **Morphisms** — each FK ``child.col → parent.col`` is a
      ``MorphismKind.RESTRICTION`` arrow: given any child row, restriction along
      the FK selects the unique parent row the child row "lives over".
    * **Covers** — a table's named indexes collectively cover it for query
      access; together they make every row reachable by at least one access
      path.

    The ``Site`` instance is available via ``_site`` for clients that need
    direct access to the categorical machinery (descent, sheaf conditions, …).
    """

    def __init__(self) -> None:
        self._site: Site = Site(label="sql_relational_site")
        self._tables: dict[str, SQLTable] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_table(self, table: SQLTable) -> None:
        """Register a table (or view / CTE) as an object in the site.

        This also registers any FK morphisms declared on the table's columns,
        provided the referenced table has already been added.  FKs pointing to
        tables that are added later can be registered explicitly via
        ``add_foreign_key_morphism``.
        """
        self._tables[table.name] = table
        self._site.add_coordinate(table.to_coordinate())
        # Auto-register FKs whose target is already present.
        for this_col, ref_table, ref_col in table.foreign_keys():
            if ref_table in self._tables:
                self.add_foreign_key_morphism(table.name, this_col, ref_table, ref_col)

    def add_foreign_key_morphism(
        self,
        from_table: str,
        from_col: str,
        to_table: str,
        to_col: str,
    ) -> None:
        """Record a FK relationship as a categorical restriction morphism.

        A FK ``child.col → parent.col`` is a *restriction* in the categorical
        sense: the child row is "restricted" to the parent row that witnesses
        the referential constraint.  Concretely:

        * ``source`` — the child table coordinate
        * ``target`` — the parent table coordinate
        * ``kind``   — ``MorphismKind.RESTRICTION``

        The label encodes the column pair for debugging and serialization.

        Raises
        ------
        KeyError
            If either table has not been registered with ``add_table``.
        """
        if from_table not in self._tables:
            raise KeyError(f"Table {from_table!r} not registered in site.")
        if to_table not in self._tables:
            raise KeyError(f"Table {to_table!r} not registered in site.")

        source_coord = self._tables[from_table].to_coordinate()
        target_coord = self._tables[to_table].to_coordinate()
        morphism = Morphism(
            source=source_coord,
            target=target_coord,
            kind=MorphismKind.RESTRICTION,
            label=f"{from_table}.{from_col} -> {to_table}.{to_col}",
        )
        self._site.add_morphism(morphism)

    # ------------------------------------------------------------------
    # Covering families
    # ------------------------------------------------------------------

    def index_covering(self, table_name: str) -> CoveringFamily:
        """Build the covering family generated by a table's named indexes.

        In the Grothendieck topology sense, a *covering* of object *U* is a
        family of morphisms whose "images" jointly cover *U*.  For SQL tables,
        the named indexes collectively cover the table for *query access*: any
        row is reachable by at least one of the access paths the indexes
        provide.

        Each index produces an ``INCLUSION`` morphism from the "index view" of
        the table (a sub-coordinate keyed on the index columns) into the table
        coordinate.  Together these morphisms form the covering family.

        If the table has no indexes the covering family is empty (the table is
        covered only by a full sequential scan, which is implicit).

        Raises
        ------
        KeyError
            If ``table_name`` is not registered.
        """
        if table_name not in self._tables:
            raise KeyError(f"Table {table_name!r} not registered in site.")

        table = self._tables[table_name]
        base_coord = table.to_coordinate()
        family = CoveringFamily(base=base_coord, label=f"index_cover({table_name})")

        for index_name, index_cols in table.indexes:
            # The index is modelled as a sub-coordinate: a slice of the table
            # keyed on the indexed columns.
            index_label = f"{table_name}::{index_name}"
            index_coord = Coordinate(
                components=(table_name, index_name),
                kind=CoordinateKind.REGION,
                metadata={"index_columns": index_cols, "parent_table": table_name},
            )
            morphism = Morphism(
                source=index_coord,
                target=base_coord,
                kind=MorphismKind.INCLUSION,
                label=f"index {index_label} -> {table_name}",
            )
            family.add_member(morphism)

        self._site.add_covering_family(family)
        return family

    # ------------------------------------------------------------------
    # Fiber products (JOINs)
    # ------------------------------------------------------------------

    def join_fiber_product(
        self,
        table_a: str,
        table_b: str,
        on_a_col: str,
        on_b_col: str,
    ) -> tuple[str, CoveringFamily]:
        """Model an INNER JOIN as the categorical fiber product of two tables.

        Why JOIN is a fiber product
        ---------------------------
        Let *A* and *B* be two table-objects and let *K* be the "key space"
        coordinate (the domain of the join columns).  Each join column defines
        a morphism::

            f : A → K       (projection of A onto on_a_col values)
            g : B → K       (projection of B onto on_b_col values)

        The fiber product (pullback) ``A ×_K B`` is the universal object *P*
        equipped with projections ``π_A : P → A`` and ``π_B : P → B`` such
        that::

            f ∘ π_A = g ∘ π_B

        This is *exactly* the INNER JOIN: the result set consists of every pair
        ``(a, b) ∈ A × B`` for which ``a.on_a_col = b.on_b_col``.  The
        universal property says any other object that maps into both *A* and
        *B* with equal projections to *K* factors uniquely through the join
        result — mirroring how query rewriting can always replace a compatible
        sub-expression with a JOIN.

        The join coordinate is registered in the site as a new
        ``CoordinateKind.REGION`` and receives a two-member covering family
        consisting of the projections back to *A* and *B*.

        Parameters
        ----------
        table_a, table_b:
            Names of the two tables to join (must already be registered).
        on_a_col:
            The column in *table_a* used in the equijoin predicate.
        on_b_col:
            The column in *table_b* used in the equijoin predicate.

        Returns
        -------
        join_coord_name:
            The string key under which the join coordinate is stored.
        covering:
            The ``CoveringFamily`` containing the two projection morphisms.

        Raises
        ------
        KeyError
            If either table is not registered.
        """
        for tname in (table_a, table_b):
            if tname not in self._tables:
                raise KeyError(f"Table {tname!r} not registered in site.")

        join_name = f"JOIN({table_a}.{on_a_col}={table_b}.{on_b_col})"
        join_coord = Coordinate(
            components=(table_a, table_b, on_a_col, on_b_col),
            kind=CoordinateKind.REGION,
            metadata={
                "join_type": "INNER",
                "table_a": table_a,
                "table_b": table_b,
                "on_a_col": on_a_col,
                "on_b_col": on_b_col,
            },
        )
        self._site.add_coordinate(join_coord)

        coord_a = self._tables[table_a].to_coordinate()
        coord_b = self._tables[table_b].to_coordinate()

        # π_A: join result projects back to A
        proj_a = Morphism(
            source=join_coord,
            target=coord_a,
            kind=MorphismKind.RESTRICTION,
            label=f"π_A: {join_name} -> {table_a}",
        )
        # π_B: join result projects back to B
        proj_b = Morphism(
            source=join_coord,
            target=coord_b,
            kind=MorphismKind.RESTRICTION,
            label=f"π_B: {join_name} -> {table_b}",
        )

        covering = CoveringFamily(
            base=join_coord,
            members=[proj_a, proj_b],
            label=f"fiber_product_cover({join_name})",
        )
        self._site.add_covering_family(covering)
        return join_name, covering

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def reachable_tables(self, from_table: str) -> list[str]:
        """Return all tables reachable from *from_table* via FK chains.

        Traversal follows FK arrows in the *forward* direction: from child
        tables toward parent tables.  The starting table itself is excluded
        from the result.

        Parameters
        ----------
        from_table:
            The starting table name (must be registered).

        Returns
        -------
        list[str]
            Alphabetically sorted list of reachable table names.
        """
        if from_table not in self._tables:
            raise KeyError(f"Table {from_table!r} not registered in site.")

        visited: set[str] = set()
        queue: list[str] = [from_table]

        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            table = self._tables.get(current)
            if table is None:
                continue
            for _col, ref_table, _ref_col in table.foreign_keys():
                if ref_table not in visited and ref_table in self._tables:
                    queue.append(ref_table)

        visited.discard(from_table)
        return sorted(visited)

    def detect_cycles(self) -> list[list[str]]:
        """Find all circular FK reference chains in the schema.

        A cycle occurs when a chain of FK relationships leads back to the
        starting table, e.g. ``A → B → C → A``.  Circular FKs are legal in
        some databases (with deferrable constraints) but complicate migration,
        truncation, and descent gluing.

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle, represented as an ordered sequence of
            table names that forms a closed loop.  The first and last elements
            are the same table.  Returns an empty list when the schema is
            acyclic.
        """
        # Build adjacency: table_name -> set of directly referenced tables
        adj: dict[str, set[str]] = {
            name: set() for name in self._tables
        }
        for name, table in self._tables.items():
            for _col, ref_table, _ref_col in table.foreign_keys():
                if ref_table in self._tables:
                    adj[name].add(ref_table)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)
            for neighbour in adj[node]:
                if neighbour not in visited:
                    dfs(neighbour)
                elif neighbour in rec_stack:
                    # Extract the cycle starting from neighbour
                    idx = rec_stack.index(neighbour)
                    cycle = rec_stack[idx:] + [neighbour]
                    if cycle not in cycles:
                        cycles.append(cycle)
            rec_stack.pop()

        for table_name in self._tables:
            if table_name not in visited:
                dfs(table_name)

        return cycles

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SQLRelationalSite("
            f"{len(self._tables)} tables, "
            f"{self._site.morphism_count()} morphisms)"
        )


# ---------------------------------------------------------------------------
# 5. QuerySection
# ---------------------------------------------------------------------------


@dataclass
class QuerySection:
    """A SELECT query modelled as a local section of the relational sheaf.

    A *local section* over a coordinate *U* assigns data locally — over the
    "open" *U* — consistently with the gluing conditions imposed by the site's
    topology.  A SELECT query is precisely a local section: it selects data
    from a sub-region of the schema (the FROM clause) consistent with the
    join conditions (gluing data across table boundaries) and subject to WHERE
    predicates (restricting to a sub-cover).

    Parameters
    ----------
    query_id:
        Unique string identifier for this query (e.g. a function name or
        migration label).
    from_tables:
        Ordered list of table names in the FROM clause.
    join_conditions:
        List of equijoin predicates, each encoded as
        ``(table_a, col_a, table_b, col_b)``.
    where_predicates:
        Raw SQL predicate strings from the WHERE clause.
    selected_columns:
        List of ``(table, column)`` pairs describing the SELECT list.
    """

    query_id: str
    from_tables: list[str] = field(default_factory=list)
    join_conditions: list[tuple[str, str, str, str]] = field(default_factory=list)
    where_predicates: list[str] = field(default_factory=list)
    selected_columns: list[tuple[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Conversion to descent layer
    # ------------------------------------------------------------------

    def to_local_section(self, site: SQLRelationalSite) -> LocalSection:
        """Lift this query to a ``LocalSection`` over its FROM-clause coordinate.

        The *coordinate* of the section is the primary FROM table (or a join
        coordinate if multiple tables appear).  The ``judgment_data`` encodes
        the full query structure so that descent machinery can check gluing
        conditions across table boundaries.

        The ``evidence_bundle`` lists all participating table names as
        "witnesses" — the tables whose data the query depends on.

        The section is marked ``is_partial=True`` when WHERE predicates are
        present, because the query only covers a sub-region of the table, not
        the full object.

        Parameters
        ----------
        site:
            The ``SQLRelationalSite`` that contextualises this query.

        Returns
        -------
        LocalSection
        """
        if not self.from_tables:
            coordinate = self.query_id
        elif len(self.from_tables) == 1:
            coordinate = self.from_tables[0]
        else:
            # Multiple tables: use the join coordinate label convention.
            a, b = self.from_tables[0], self.from_tables[1]
            first_join = self.join_conditions[0] if self.join_conditions else None
            if first_join:
                coordinate = f"JOIN({first_join[0]}.{first_join[1]}={first_join[2]}.{first_join[3]})"
            else:
                coordinate = f"CROSS({a},{b})"

        evidence: tuple[str, ...] = tuple(self.from_tables)
        judgment: dict[str, Any] = {
            "query_id": self.query_id,
            "from_tables": list(self.from_tables),
            "join_conditions": [list(jc) for jc in self.join_conditions],
            "where_predicates": list(self.where_predicates),
            "selected_columns": [list(sc) for sc in self.selected_columns],
            "has_n_plus_1_risk": self.has_n_plus_1_risk(),
        }

        return LocalSection(
            coordinate=coordinate,
            judgment_data=judgment,
            evidence_bundle=evidence,
            trust_level=0.8 if self.has_n_plus_1_risk() else 1.0,
            provenance=(self.query_id,),
            is_partial=bool(self.where_predicates),
            residual_obligations=(
                ["resolve_n_plus_1"] if self.has_n_plus_1_risk() else []
            ),
        )

    # ------------------------------------------------------------------
    # Static analysis
    # ------------------------------------------------------------------

    def has_n_plus_1_risk(self) -> bool:
        """Return True if this query carries an N+1 risk pattern.

        The N+1 problem arises when code executes one query to retrieve *N*
        rows from table *A*, then issues *N* additional queries — one per row —
        to fetch related data from table *B*, instead of using a single JOIN.

        This heuristic detects the risk structurally: if the query references
        more than one table in its FROM clause but declares *no* join condition
        linking those tables, the caller likely relies on iterating over the
        first query's result set and issuing per-row follow-up queries.  A JOIN
        would resolve all such cases in a single round-trip.

        Note: this is a conservative heuristic.  A query with multiple FROM
        tables and no JOIN conditions might also be an intentional cross join,
        but that is itself usually a performance hazard.

        Returns
        -------
        bool
        """
        if len(self.from_tables) <= 1:
            return False

        # Collect pairs of tables that are joined
        joined_pairs: set[frozenset[str]] = set()
        for ta, _ca, tb, _cb in self.join_conditions:
            joined_pairs.add(frozenset({ta, tb}))

        # Check every pair of from_tables for a missing join condition
        tables = self.from_tables
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                if frozenset({tables[i], tables[j]}) not in joined_pairs:
                    return True
        return False

    def __repr__(self) -> str:  # pragma: no cover
        n1 = " [N+1 risk]" if self.has_n_plus_1_risk() else ""
        return (
            f"QuerySection({self.query_id!r}, "
            f"from={self.from_tables}{n1})"
        )
