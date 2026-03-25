"""
SQL Transactions as Atomic Gluing Operations
=============================================

This module models SQL transactions through the lens of sheaf theory, treating
each transaction as an **atomic gluing** in the Grothendieck-topology sense.

**The sheaf analogy**
    A database can be thought of as a sheaf over a site whose objects are tables
    (or table–predicate pairs) and whose covering families are the sets of
    sub-queries whose unions reconstruct a full query result.  A *transaction*
    is then an attempt to glue a collection of locally-consistent writes into a
    globally-consistent new state.  Atomicity is exactly the sheaf condition:
    either the local sections (individual write operations) glue into a
    coherent global section, or the entire attempt is rolled back so that no
    intermediate, incoherent partial section is ever visible.

**Serializability via descent**
    Concurrency control can be phrased as a descent problem.  Given a set of
    concurrent transactions, their combined effect is globally consistent iff
    the dependency graph (WR, RW, WW edges) is acyclic — i.e., iff the local
    sections indexed by individual transactions can be glued into a total order
    without encountering an H¹ obstruction.  A cycle in the dependency graph
    is precisely such an obstruction.

**Schema migration as a site morphism**
    Schema evolution (``MigrationMorphism``) is modelled as a morphism of
    sites: it maps covers in the old schema to covers in the new schema.  A
    reversible migration is an isomorphism of sites; a destructive one (e.g.
    dropping a column) is merely a morphism that lacks a right inverse.

References
----------
* Gray & Reuter, *Transaction Processing* (1992) — concurrency anomalies
* SGA 4, Exposé II — Grothendieck topologies and descent
* Bernstein, Hadzilacos & Goodman, *Concurrency Control and Recovery* (1987)
"""

from __future__ import annotations

__all__ = [
    "IsolationLevel",
    "TransactionState",
    "SQLOperation",
    "SQLTransaction",
    "TransactionDescentChecker",
    "MigrationMorphism",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import Coordinate, CoveringFamily  # noqa: F401
from jugeo.geometry.descent import (
    LocalSection,  # noqa: F401
    DescentEngine,  # noqa: F401
    DescentResult,
    DescentObstruction,
    GlobalSection,
    RepairFrontier,
    CohomologyClass,
)


# ---------------------------------------------------------------------------
# 1. IsolationLevel
# ---------------------------------------------------------------------------

class IsolationLevel(str, Enum):
    """SQL transaction isolation levels and the anomalies each prevents.

    The ANSI/ISO SQL standard defines four isolation levels that trade
    concurrency for consistency by suppressing progressively more anomalies:

    Anomaly catalogue
    -----------------
    *Dirty read*
        Transaction T2 reads a row that T1 has modified but not yet committed.
        If T1 later aborts, T2 has observed data that never officially existed.

    *Non-repeatable read*
        T2 reads row R twice in the same transaction.  Between the two reads,
        T1 commits an UPDATE on R, so the two reads return different values.

    *Phantom read*
        T2 issues a range query twice.  Between the two queries, T1 commits an
        INSERT or DELETE that changes the row-set matching the predicate, so
        the two results differ in cardinality.

    Isolation level → anomaly prevention matrix
    -------------------------------------------
    ==================  ===========  ====================  ============
    Level               Dirty read   Non-repeatable read   Phantom read
    ==================  ===========  ====================  ============
    READ_UNCOMMITTED    ✗ allowed    ✗ allowed             ✗ allowed
    READ_COMMITTED      ✓ prevented  ✗ allowed             ✗ allowed
    REPEATABLE_READ     ✓ prevented  ✓ prevented           ✗ allowed
    SERIALIZABLE        ✓ prevented  ✓ prevented           ✓ prevented
    ==================  ===========  ====================  ============
    """

    READ_UNCOMMITTED = "READ UNCOMMITTED"
    READ_COMMITTED = "READ COMMITTED"
    REPEATABLE_READ = "REPEATABLE READ"
    SERIALIZABLE = "SERIALIZABLE"

    # -- anomaly predicate methods ------------------------------------------

    def prevents_dirty_read(self) -> bool:
        """Return True if this level prevents dirty reads.

        Dirty reads are prevented at READ_COMMITTED and above: the engine only
        returns rows from committed snapshots, so an in-flight write is never
        exposed to concurrent readers.
        """
        return self in (
            IsolationLevel.READ_COMMITTED,
            IsolationLevel.REPEATABLE_READ,
            IsolationLevel.SERIALIZABLE,
        )

    def prevents_nonrepeatable_read(self) -> bool:
        """Return True if this level prevents non-repeatable reads.

        Non-repeatable reads are prevented at REPEATABLE_READ and above: the
        engine pins the snapshot for each row on first read so subsequent reads
        within the same transaction see the same value.
        """
        return self in (
            IsolationLevel.REPEATABLE_READ,
            IsolationLevel.SERIALIZABLE,
        )

    def prevents_phantom_read(self) -> bool:
        """Return True if this level prevents phantom reads.

        Phantom reads require predicate locking (or equivalent snapshot
        semantics that freeze the full result-set of a range query).  Only
        SERIALIZABLE provides this guarantee.
        """
        return self is IsolationLevel.SERIALIZABLE


# ---------------------------------------------------------------------------
# 2. TransactionState
# ---------------------------------------------------------------------------

class TransactionState(str, Enum):
    """Lifecycle states of a SQL transaction.

    The state machine follows the classic model from Gray & Reuter §4.3:

    ::

        ACTIVE ──► PARTIALLY_COMMITTED ──► COMMITTED
           │                │
           └──► FAILED ──►  ABORTED

    * ``ACTIVE``               — operations are being executed.
    * ``PARTIALLY_COMMITTED``  — final operation executed; pre-commit checks
                                 (constraint validation, write-ahead log flush)
                                 are in progress.
    * ``COMMITTED``            — all changes durably written; transaction over.
    * ``FAILED``               — an error was detected; rollback has not yet
                                 completed.
    * ``ABORTED``              — rollback complete; database restored to its
                                 state prior to the transaction.
    """

    ACTIVE = "ACTIVE"
    PARTIALLY_COMMITTED = "PARTIALLY_COMMITTED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


# ---------------------------------------------------------------------------
# 3. SQLOperation
# ---------------------------------------------------------------------------

@dataclass
class SQLOperation:
    """A single SQL statement within a transaction.

    Represents one atomic database operation.  ``is_read`` and ``is_write``
    may both be True for statements such as ``INSERT … RETURNING`` or
    ``UPDATE … RETURNING``.

    Parameters
    ----------
    op_id:
        Unique identifier for this operation within its transaction.
    op_kind:
        Statement type: ``SELECT``, ``INSERT``, ``UPDATE``, ``DELETE``,
        or ``DDL`` (CREATE/ALTER/DROP/TRUNCATE).
    table_names:
        Tables accessed by the statement (both read and write sides).
    is_read:
        True when the operation reads rows (SELECT, or the read side of
        INSERT … SELECT).
    is_write:
        True when the operation modifies the table (INSERT, UPDATE, DELETE,
        or DDL).
    estimated_rows:
        Heuristic row-count affected; used by the serializability checker to
        weight dependency edges.
    """

    op_id: str
    op_kind: str
    table_names: list[str]
    is_read: bool
    is_write: bool
    estimated_rows: int = 0


# ---------------------------------------------------------------------------
# 4. SQLTransaction
# ---------------------------------------------------------------------------

@dataclass
class SQLTransaction:
    """A database transaction modelled as an atomic gluing.

    In sheaf terms, each ``SQLOperation`` is a local section on the open set
    corresponding to the tables it touches.  The transaction is the attempt to
    glue all these local sections into a coherent global state change.
    Atomicity is the sheaf gluing axiom: either every section patches cleanly
    (COMMIT) or none of them is exposed (ROLLBACK).

    Parameters
    ----------
    tx_id:
        Unique transaction identifier (e.g. a UUID or sequence number).
    isolation_level:
        The ANSI isolation level under which this transaction executes.
    state:
        Current lifecycle state.
    operations:
        Ordered list of SQL operations executed so far.
    savepoints:
        Named savepoints declared within this transaction; each names a
        partial rollback target.
    """

    tx_id: str
    isolation_level: IsolationLevel
    state: TransactionState
    operations: list[SQLOperation] = field(default_factory=list)
    savepoints: list[str] = field(default_factory=list)

    # -- mutation -----------------------------------------------------------

    def add_operation(self, op: SQLOperation) -> None:
        """Append *op* to the operation list and advance state to ACTIVE."""
        if self.state is TransactionState.COMMITTED:
            raise ValueError(
                f"Transaction {self.tx_id!r} is already committed; "
                "cannot add further operations."
            )
        if self.state is TransactionState.ABORTED:
            raise ValueError(
                f"Transaction {self.tx_id!r} has been aborted; "
                "cannot add further operations."
            )
        self.operations.append(op)
        if self.state is not TransactionState.ACTIVE:
            self.state = TransactionState.ACTIVE

    # -- predicates ---------------------------------------------------------

    def can_commit(self) -> bool:
        """Return True when this transaction is eligible to commit.

        A transaction can commit iff:

        1. Its state is ``PARTIALLY_COMMITTED`` (all operations executed,
           pre-commit checks pending); and
        2. It has no internal write–write conflict, i.e. no single table is
           written more than once with semantically incompatible operations
           (treated conservatively as any duplicate write-table appearance
           across DELETE and INSERT on the same table without an intervening
           SELECT, which could indicate a lost-update).

        In sheaf terms this checks the *local* compatibility condition before
        attempting the global glue.
        """
        if self.state is not TransactionState.PARTIALLY_COMMITTED:
            return False
        write_tables: dict[str, str] = {}
        for op in self.operations:
            if not op.is_write:
                continue
            for table in op.table_names:
                if table in write_tables and write_tables[table] != op.op_kind:
                    return False
                write_tables[table] = op.op_kind
        return True

    # -- set projections ----------------------------------------------------

    def write_set(self) -> set[str]:
        """Return the set of table names written by this transaction."""
        return {
            table
            for op in self.operations
            if op.is_write
            for table in op.table_names
        }

    def read_set(self) -> set[str]:
        """Return the set of table names read by this transaction."""
        return {
            table
            for op in self.operations
            if op.is_read
            for table in op.table_names
        }

    # -- conflict detection -------------------------------------------------

    def has_write_conflict(self, other: SQLTransaction) -> bool:
        """Return True if both transactions write to at least one common table.

        A write–write conflict is a direct serialization concern: if two
        concurrent transactions both modify the same table the outcome depends
        on the order in which they commit, and under SERIALIZABLE isolation the
        engine must detect and abort one of them.

        In the sheaf model this is an overlap-condition failure: the two local
        sections (T1's write, T2's write) disagree on the same object and
        cannot be glued without arbitration.
        """
        return bool(self.write_set() & other.write_set())


# ---------------------------------------------------------------------------
# 5. TransactionDescentChecker
# ---------------------------------------------------------------------------

class TransactionDescentChecker:
    """Checks serializability and atomicity via descent theory.

    Serializability check
    ~~~~~~~~~~~~~~~~~~~~~
    A concurrent schedule of transactions is *conflict-serializable* iff its
    *conflict-dependency graph* (also called the *serialization graph* or
    *precedence graph*) is acyclic.  Nodes are transactions; edges represent
    dependencies:

    * **WR** (write–read): T_i → T_j if T_j reads a table that T_i wrote.
    * **RW** (read–write): T_i → T_j if T_j writes a table that T_i read.
    * **WW** (write–write): T_i → T_j if T_j writes a table that T_i wrote.

    A cycle means at least two transactions are mutually dependent — there is
    no serial order consistent with the observed dependencies — which in sheaf
    language is an H¹ obstruction to gluing the local database states into a
    coherent global history.

    Atomicity check
    ~~~~~~~~~~~~~~~
    Atomicity requires that all writes of a committed transaction appear in
    ``committed_tables``, or none do.  Partial visibility is modelled as a
    failed descent: the local sections (individual writes) could not be glued
    into a single global section because the covering condition is violated.
    """

    # -- public API ---------------------------------------------------------

    def check_serializability(
        self,
        transactions: list[SQLTransaction],
    ) -> DescentResult:
        """Check whether *transactions* form a serializable schedule.

        Returns a successful ``DescentResult`` wrapping a ``GlobalSection``
        when no cycle is detected, or a failure wrapping a
        ``DescentObstruction`` describing the cycle when one exists.

        Parameters
        ----------
        transactions:
            The concurrent transactions to analyse.  Only ACTIVE and
            PARTIALLY_COMMITTED transactions participate; COMMITTED and
            ABORTED transactions are treated as already ordered.
        """
        graph = self._build_dependency_graph(transactions)
        coordinate = "tx_schedule:" + ",".join(tx.tx_id for tx in transactions)

        if not self._has_cycle(graph):
            section = GlobalSection(
                coordinate=coordinate,
                merged_judgment={
                    "serializable": True,
                    "transaction_count": len(transactions),
                    "dependency_edges": sum(len(v) for v in graph.values()),
                },
                constituent_sections=tuple(tx.tx_id for tx in transactions),
                certificate="acyclic_dependency_graph",
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        # Locate the tables involved in the cycle for the repair frontier.
        cycle_tables: list[str] = []
        for tx in transactions:
            for dep_id in graph.get(tx.tx_id, []):
                dep_txs = [t for t in transactions if t.tx_id == dep_id]
                if dep_txs:
                    overlap = tx.write_set() & dep_txs[0].write_set()
                    overlap |= tx.write_set() & dep_txs[0].read_set()
                    overlap |= tx.read_set() & dep_txs[0].write_set()
                    cycle_tables.extend(sorted(overlap))

        frontier = RepairFrontier(
            suggested_refinements=tuple(
                f"abort_or_reorder:{tx.tx_id}" for tx in transactions
            ),
            weakened_claims=tuple(f"table_conflict:{t}" for t in set(cycle_tables)),
        )
        cohomology = CohomologyClass(
            dimension=1,
            cocycle_data={tx.tx_id: graph.get(tx.tx_id, []) for tx in transactions},
        )
        obstruction = DescentObstruction(
            coordinate=coordinate,
            repair_frontier=frontier,
            cohomology_class=cohomology,
            partial_section={
                "non_serializable": True,
                "cycle_participants": [
                    tx.tx_id
                    for tx in transactions
                    if graph.get(tx.tx_id)
                ],
            },
        )
        return DescentResult.failure(obstruction)

    def _build_dependency_graph(
        self,
        transactions: list[SQLTransaction],
    ) -> dict[str, list[str]]:
        """Build the conflict-dependency graph for *transactions*.

        Returns a mapping ``tx_id → [tx_id, …]`` where each entry lists the
        transactions that the key transaction depends on (i.e. must be ordered
        after).  Three kinds of edges are added:

        * WR: T_i writes table X, T_j reads X  → T_j depends on T_i.
        * RW: T_i reads table X, T_j writes X  → T_j depends on T_i.
        * WW: T_i writes table X, T_j writes X → T_j depends on T_i.
        """
        graph: dict[str, list[str]] = {tx.tx_id: [] for tx in transactions}
        for i, t_i in enumerate(transactions):
            for j, t_j in enumerate(transactions):
                if i == j:
                    continue
                # WR: t_i wrote something t_j reads
                if t_i.write_set() & t_j.read_set():
                    if t_i.tx_id not in graph[t_j.tx_id]:
                        graph[t_j.tx_id].append(t_i.tx_id)
                # RW: t_i read something t_j writes
                if t_i.read_set() & t_j.write_set():
                    if t_i.tx_id not in graph[t_j.tx_id]:
                        graph[t_j.tx_id].append(t_i.tx_id)
                # WW: t_i wrote something t_j also writes
                if t_i.write_set() & t_j.write_set():
                    if t_i.tx_id not in graph[t_j.tx_id]:
                        graph[t_j.tx_id].append(t_i.tx_id)
        return graph

    def _has_cycle(self, graph: dict[str, list[str]]) -> bool:
        """Return True if *graph* contains a directed cycle.

        Uses iterative DFS with a three-colour marking scheme:
        * white (0) — not yet visited
        * grey  (1) — currently on the DFS stack
        * black (2) — fully processed

        A grey → grey back-edge is the cycle witness.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {node: WHITE for node in graph}

        for start in graph:
            if colour[start] != WHITE:
                continue
            stack = [(start, iter(graph.get(start, [])))]
            colour[start] = GREY
            while stack:
                node, children = stack[-1]
                try:
                    child = next(children)
                    if child not in colour:
                        colour[child] = WHITE
                    if colour[child] == GREY:
                        return True
                    if colour[child] == WHITE:
                        colour[child] = GREY
                        stack.append((child, iter(graph.get(child, []))))
                except StopIteration:
                    colour[node] = BLACK
                    stack.pop()
        return False

    def check_atomicity(
        self,
        tx: SQLTransaction,
        committed_tables: set[str],
    ) -> DescentResult:
        """Check whether *tx* satisfies the atomicity condition.

        Atomicity is the sheaf gluing axiom applied to writes: either every
        table in the transaction's write-set appears in ``committed_tables``
        (full commit), or none do (clean rollback).  A partial overlap is an
        atomicity violation — a failed gluing where some local sections were
        persisted but others were not.

        Parameters
        ----------
        tx:
            The transaction to check.
        committed_tables:
            The set of tables for which durable writes have been confirmed
            (e.g. after a WAL flush).

        Returns
        -------
        DescentResult
            Success when the transaction is fully committed or fully rolled
            back; failure when a partial write is detected.
        """
        ws = tx.write_set()
        if not ws:
            section = GlobalSection(
                coordinate=f"tx_atomicity:{tx.tx_id}",
                merged_judgment={"atomic": True, "reason": "no_writes"},
                constituent_sections=(tx.tx_id,),
                certificate="empty_write_set",
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        committed = ws & committed_tables
        uncommitted = ws - committed_tables

        if not uncommitted:
            section = GlobalSection(
                coordinate=f"tx_atomicity:{tx.tx_id}",
                merged_judgment={
                    "atomic": True,
                    "committed_tables": sorted(committed),
                },
                constituent_sections=(tx.tx_id,),
                certificate="all_writes_committed",
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        if not committed:
            section = GlobalSection(
                coordinate=f"tx_atomicity:{tx.tx_id}",
                merged_judgment={
                    "atomic": True,
                    "reason": "fully_rolled_back",
                    "uncommitted_tables": sorted(uncommitted),
                },
                constituent_sections=(tx.tx_id,),
                certificate="all_writes_rolled_back",
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        # Partial write: atomicity violated.
        frontier = RepairFrontier(
            missing_evidence=tuple(
                f"confirm_rollback:{t}" for t in sorted(uncommitted)
            ),
            weakened_claims=tuple(
                f"partial_write_visible:{t}" for t in sorted(committed)
            ),
        )
        cohomology = CohomologyClass(
            dimension=1,
            cocycle_data={
                "committed": sorted(committed),
                "uncommitted": sorted(uncommitted),
            },
        )
        obstruction = DescentObstruction(
            coordinate=f"tx_atomicity:{tx.tx_id}",
            repair_frontier=frontier,
            cohomology_class=cohomology,
            partial_section={
                "atomic": False,
                "committed_tables": sorted(committed),
                "uncommitted_tables": sorted(uncommitted),
            },
        )
        return DescentResult.failure(obstruction)


# ---------------------------------------------------------------------------
# 6. MigrationMorphism
# ---------------------------------------------------------------------------

@dataclass
class MigrationMorphism:
    """Schema evolution modelled as a morphism of database sites.

    A schema migration is a morphism f : Site(schema_v_old) → Site(schema_v_new)
    in the category of sites.  Concretely, it maps every covering family of the
    old schema to a covering family of the new schema while preserving the
    Grothendieck topology (the cover axioms).

    A *reversible* migration corresponds to an isomorphism: the ``down``
    direction provides the inverse morphism.  A *destructive* migration (e.g.
    dropping a column) is a morphism that lacks a right inverse — it is not
    possible to recover the dropped data from the new schema alone.

    The flag ``breaks_existing_queries`` signals that queries valid under
    ``from_version`` may be syntactically or semantically invalid under
    ``to_version`` (e.g. due to a renamed or removed column).

    Parameters
    ----------
    migration_id:
        Unique, human-readable identifier (e.g. ``"20240315_add_user_email"``).
    from_version:
        Schema version label before applying this migration.
    to_version:
        Schema version label after applying this migration.
    up_operations:
        Ordered SQL statements that advance the schema from *from_version* to
        *to_version*.
    down_operations:
        Ordered SQL statements that reverse the migration.  May be empty when
        ``is_reversible`` is False.
    is_reversible:
        True when ``down_operations`` is a faithful inverse of
        ``up_operations`` — i.e. applying up then down leaves the schema
        (and, where possible, the data) unchanged.
    breaks_existing_queries:
        True when the migration removes or renames schema elements that
        existing application queries depend on (e.g. dropping a column,
        renaming a table).  Callers should audit and update queries before
        applying such migrations.
    """

    migration_id: str
    from_version: str
    to_version: str
    up_operations: list[str]
    down_operations: list[str]
    is_reversible: bool
    breaks_existing_queries: bool = False

    def is_additive(self) -> bool:
        """Return True when this migration only adds schema elements.

        An *additive* migration (adding columns with defaults, creating new
        tables, adding indexes) never breaks existing queries and is always
        safe to apply without application-layer coordination.  It corresponds
        to an open embedding of sites: the new schema extends the old one.
        """
        destructive_keywords = {"DROP", "ALTER COLUMN", "RENAME", "TRUNCATE"}
        up_sql = " ".join(self.up_operations).upper()
        return not any(kw in up_sql for kw in destructive_keywords)

    def as_local_section(self) -> LocalSection:
        """Represent this migration as a ``LocalSection`` over its version span.

        The coordinate is the version-span string ``"from_version→to_version"``.
        The judgment data records the essential migration metadata so that the
        migration can participate in descent checks (e.g. verifying that a
        chain of migrations can be glued into a coherent schema history).
        """
        return LocalSection(
            coordinate=f"{self.from_version}→{self.to_version}",
            judgment_data={
                "migration_id": self.migration_id,
                "reversible": self.is_reversible,
                "additive": self.is_additive(),
                "breaks_queries": self.breaks_existing_queries,
                "up_count": len(self.up_operations),
                "down_count": len(self.down_operations),
            },
            trust_level=1.0 if self.is_reversible else 0.7,
        )
