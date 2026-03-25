"""SQLite implementation of the JuGeo :class:`~jugeo.scaling.storage.store.Store`.

Architecture
------------
* One SQLite database file per Store instance.
* Thread-local connections so each OS thread gets its own ``sqlite3.Connection``.
  This avoids the ``check_same_thread=False`` foot-gun while still supporting
  multi-threaded usage.
* WAL journal mode for concurrent read/write throughput.
* All queries are parameterised — no string interpolation on user data.
* A lightweight query-builder helper constructs ``WHERE`` clauses from
  keyword-filter dicts without resorting to an ORM.
* Schema versioning is tracked in a ``schema_migrations`` table; the
  :class:`~jugeo.scaling.storage.migrations.MigrationManager` handles
  applying and rolling back migrations.

Performance knobs
-----------------
* ``cache_size=-65536`` → 64 MiB page cache per connection.
* ``mmap_size=268435456`` → 256 MiB memory-mapped I/O.
* ``synchronous=NORMAL`` → safe crash semantics without full fsync.
* Batch inserts via ``executemany`` for bulk operations.

Usage example::

    from jugeo.scaling.storage.sqlite_backend import SQLiteBackend
    from jugeo.scaling.storage.models import StoredCoordinate

    store = SQLiteBackend("state.db")
    store.initialize()
    coord = StoredCoordinate.create("jugeo.geometry", "MODULE", 1, "jugeo", "jugeo.geometry")
    store.put_coordinate(coord)
    result = store.get_coordinate(coord.id)
    store.close()
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable, Iterator, Optional

from jugeo.scaling.storage.models import (
    JudgmentStatus,
    ObligationStatus,
    StoredCertificate,
    StoredCoordinate,
    StoredEvidence,
    StoredJudgment,
    StoredMorphism,
    StoredObligation,
    StoredObstruction,
    StoredTreaty,
)
from jugeo.scaling.storage.store import (
    DuplicateError,
    MigrationError,
    NotFoundError,
    Store,
    StoreError,
    TransactionError,
)


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_PRAGMA_SETUP = """\
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA cache_size=-65536;
PRAGMA mmap_size=268435456;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
"""

_SCHEMA_MIGRATIONS_DDL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  REAL    NOT NULL,
    description TEXT    NOT NULL
);
"""

_COORDINATES_DDL = """\
CREATE TABLE IF NOT EXISTS coordinates (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    depth            INTEGER NOT NULL DEFAULT 0,
    package          TEXT NOT NULL DEFAULT '',
    module           TEXT NOT NULL DEFAULT '',
    components_json  TEXT NOT NULL DEFAULT '[]',
    metadata_json    TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coord_kind    ON coordinates(kind);
CREATE INDEX IF NOT EXISTS idx_coord_package ON coordinates(package);
CREATE INDEX IF NOT EXISTS idx_coord_depth   ON coordinates(depth);
CREATE INDEX IF NOT EXISTS idx_coord_name    ON coordinates(name);
CREATE INDEX IF NOT EXISTS idx_coord_pkg_kind ON coordinates(package, kind);
"""

_MORPHISMS_DDL = """\
CREATE TABLE IF NOT EXISTS morphisms (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES coordinates(id) ON DELETE CASCADE,
    target_id   TEXT NOT NULL REFERENCES coordinates(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_morph_source ON morphisms(source_id);
CREATE INDEX IF NOT EXISTS idx_morph_target ON morphisms(target_id);
CREATE INDEX IF NOT EXISTS idx_morph_kind   ON morphisms(kind);
CREATE INDEX IF NOT EXISTS idx_morph_src_kind ON morphisms(source_id, kind);
"""

_JUDGMENTS_DDL = """\
CREATE TABLE IF NOT EXISTS judgments (
    id                 TEXT PRIMARY KEY,
    coordinate_id      TEXT NOT NULL,
    proposition        TEXT NOT NULL,
    trust_level        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open',
    carrier_json       TEXT NOT NULL DEFAULT '{}',
    evidence_json      TEXT NOT NULL DEFAULT '[]',
    obligations_json   TEXT NOT NULL DEFAULT '[]',
    obstructions_json  TEXT NOT NULL DEFAULT '[]',
    provenance_json    TEXT NOT NULL DEFAULT '{}',
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_judg_coord   ON judgments(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_judg_trust   ON judgments(trust_level);
CREATE INDEX IF NOT EXISTS idx_judg_status  ON judgments(status);
CREATE INDEX IF NOT EXISTS idx_judg_prop    ON judgments(proposition);
CREATE INDEX IF NOT EXISTS idx_judg_coord_status ON judgments(coordinate_id, status);
CREATE INDEX IF NOT EXISTS idx_judg_trust_status ON judgments(trust_level, status);
"""

_EVIDENCE_DDL = """\
CREATE TABLE IF NOT EXISTS evidence (
    id              TEXT PRIMARY KEY,
    judgment_id     TEXT NOT NULL,
    channel         TEXT NOT NULL,
    trust_level     TEXT NOT NULL,
    claim           TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    coordinate_id   TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    record_id       TEXT NOT NULL,
    support_json    TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ev_judgment   ON evidence(judgment_id);
CREATE INDEX IF NOT EXISTS idx_ev_coord      ON evidence(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_ev_channel    ON evidence(channel);
CREATE INDEX IF NOT EXISTS idx_ev_trust      ON evidence(trust_level);
CREATE INDEX IF NOT EXISTS idx_ev_timestamp  ON evidence(timestamp);
CREATE INDEX IF NOT EXISTS idx_ev_record_id  ON evidence(record_id);
CREATE INDEX IF NOT EXISTS idx_ev_coord_chan ON evidence(coordinate_id, channel);
CREATE INDEX IF NOT EXISTS idx_ev_jud_trust  ON evidence(judgment_id, trust_level);
"""

_OBLIGATIONS_DDL = """\
CREATE TABLE IF NOT EXISTS obligations (
    id            TEXT PRIMARY KEY,
    judgment_id   TEXT NOT NULL,
    coordinate_id TEXT NOT NULL,
    proposition   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 2,
    created_at    REAL NOT NULL,
    deadline      REAL,
    assigned_to   TEXT,
    support_json  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_obl_judgment   ON obligations(judgment_id);
CREATE INDEX IF NOT EXISTS idx_obl_coord      ON obligations(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_obl_status     ON obligations(status);
CREATE INDEX IF NOT EXISTS idx_obl_priority   ON obligations(priority);
CREATE INDEX IF NOT EXISTS idx_obl_deadline   ON obligations(deadline);
CREATE INDEX IF NOT EXISTS idx_obl_stat_pri   ON obligations(status, priority);
CREATE INDEX IF NOT EXISTS idx_obl_coord_stat ON obligations(coordinate_id, status);
"""

_OBSTRUCTIONS_DDL = """\
CREATE TABLE IF NOT EXISTS obstructions (
    id                   TEXT PRIMARY KEY,
    coordinate_id        TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    proposition          TEXT NOT NULL,
    cohomology_class     TEXT NOT NULL DEFAULT '',
    repair_frontier_json TEXT NOT NULL DEFAULT '[]',
    blast_radius         INTEGER NOT NULL DEFAULT 0,
    countermodel_json    TEXT NOT NULL DEFAULT '{}',
    severity             REAL NOT NULL DEFAULT 0.5,
    created_at           REAL NOT NULL,
    resolved_at          REAL
);
CREATE INDEX IF NOT EXISTS idx_obs_coord    ON obstructions(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_obs_kind     ON obstructions(kind);
CREATE INDEX IF NOT EXISTS idx_obs_severity ON obstructions(severity);
CREATE INDEX IF NOT EXISTS idx_obs_resolved ON obstructions(resolved_at);
CREATE INDEX IF NOT EXISTS idx_obs_coord_kind ON obstructions(coordinate_id, kind);
"""

_TREATIES_DDL = """\
CREATE TABLE IF NOT EXISTS treaties (
    id                      TEXT PRIMARY KEY,
    parties_json            TEXT NOT NULL DEFAULT '[]',
    overlap_coordinates_json TEXT NOT NULL DEFAULT '[]',
    propositions_json       TEXT NOT NULL DEFAULT '[]',
    status                  TEXT NOT NULL DEFAULT 'proposed',
    trust_floor             TEXT NOT NULL DEFAULT '',
    created_at              REAL NOT NULL,
    updated_at              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_treaty_status ON treaties(status);
"""

_CERTIFICATES_DDL = """\
CREATE TABLE IF NOT EXISTS certificates (
    id                  TEXT PRIMARY KEY,
    judgment_id         TEXT NOT NULL,
    coordinate_id       TEXT NOT NULL,
    trust_level         TEXT NOT NULL,
    evidence_chain_json TEXT NOT NULL DEFAULT '[]',
    version             INTEGER NOT NULL DEFAULT 1,
    issued_at           REAL NOT NULL,
    expires_at          REAL,
    issuer              TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cert_judgment  ON certificates(judgment_id);
CREATE INDEX IF NOT EXISTS idx_cert_coord     ON certificates(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_cert_trust     ON certificates(trust_level);
CREATE INDEX IF NOT EXISTS idx_cert_issued    ON certificates(issued_at);
CREATE INDEX IF NOT EXISTS idx_cert_expires   ON certificates(expires_at);
CREATE INDEX IF NOT EXISTS idx_cert_coord_trust ON certificates(coordinate_id, trust_level);
"""

# All DDL blocks in application order
_ALL_DDL = [
    _PRAGMA_SETUP,
    _SCHEMA_MIGRATIONS_DDL,
    _COORDINATES_DDL,
    _MORPHISMS_DDL,
    _JUDGMENTS_DDL,
    _EVIDENCE_DDL,
    _OBLIGATIONS_DDL,
    _OBSTRUCTIONS_DDL,
    _TREATIES_DDL,
    _CERTIFICATES_DDL,
]


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

class _QueryBuilder:
    """Lightweight dynamic WHERE-clause builder.

    Usage::

        qb = _QueryBuilder("SELECT * FROM coordinates")
        qb.eq("kind", kind)
        qb.eq("package", package)
        qb.like("name", name_prefix, suffix="%")
        sql, params = qb.build(limit=100, offset=0)
    """

    def __init__(self, base_sql: str) -> None:
        self._base = base_sql
        self._clauses: list[str] = []
        self._params: list[Any] = []

    def eq(self, column: str, value: Any) -> _QueryBuilder:
        """Add ``column = ?`` when *value* is not ``None``."""
        if value is not None:
            self._clauses.append(f"{column} = ?")
            self._params.append(value)
        return self

    def gt(self, column: str, value: Any, inclusive: bool = False) -> _QueryBuilder:
        """Add ``column > ?`` (or ``>=``) when *value* is not ``None``."""
        if value is not None:
            op = ">=" if inclusive else ">"
            self._clauses.append(f"{column} {op} ?")
            self._params.append(value)
        return self

    def lt(self, column: str, value: Any, inclusive: bool = False) -> _QueryBuilder:
        """Add ``column < ?`` (or ``<=``) when *value* is not ``None``."""
        if value is not None:
            op = "<=" if inclusive else "<"
            self._clauses.append(f"{column} {op} ?")
            self._params.append(value)
        return self

    def between(self, column: str, lo: Any, hi: Any) -> _QueryBuilder:
        """Add ``column BETWEEN ? AND ?`` when both bounds are not ``None``."""
        if lo is not None and hi is not None:
            self._clauses.append(f"{column} BETWEEN ? AND ?")
            self._params.extend([lo, hi])
        elif lo is not None:
            self.gt(column, lo, inclusive=True)
        elif hi is not None:
            self.lt(column, hi, inclusive=True)
        return self

    def like(self, column: str, value: Any, suffix: str = "%") -> _QueryBuilder:
        """Add ``column LIKE ?`` (with *suffix* appended to *value*)."""
        if value is not None:
            self._clauses.append(f"{column} LIKE ?")
            self._params.append(f"{value}{suffix}")
        return self

    def is_null(self, column: str, null: bool | None) -> _QueryBuilder:
        """Add ``column IS NULL`` or ``column IS NOT NULL`` when *null* is not ``None``."""
        if null is True:
            self._clauses.append(f"{column} IS NULL")
        elif null is False:
            self._clauses.append(f"{column} IS NOT NULL")
        return self

    def json_contains(self, column: str, value: Any) -> _QueryBuilder:
        """Add ``column LIKE '%"value"%'`` for simple JSON array membership."""
        if value is not None:
            self._clauses.append(f"{column} LIKE ?")
            self._params.append(f'%"{value}"%')
        return self

    def build(self, limit: int = 1000, offset: int = 0) -> tuple[str, list[Any]]:
        """Return ``(sql, params)`` ready for ``cursor.execute``."""
        sql = self._base
        if self._clauses:
            sql += " WHERE " + " AND ".join(self._clauses)
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return sql, list(self._params)

    def build_count(self) -> tuple[str, list[Any]]:
        """Return a ``COUNT(*)`` variant of the query."""
        # Replace SELECT ... FROM with SELECT COUNT(*) FROM
        base_lower = self._base.lower()
        from_idx = base_lower.index(" from ")
        count_sql = "SELECT COUNT(*)" + self._base[from_idx:]
        if self._clauses:
            count_sql += " WHERE " + " AND ".join(self._clauses)
        return count_sql, list(self._params)


# ---------------------------------------------------------------------------
# Row-to-model converters
# ---------------------------------------------------------------------------

def _row_to_coordinate(row: sqlite3.Row) -> StoredCoordinate:
    return StoredCoordinate(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        depth=row["depth"],
        package=row["package"],
        module=row["module"],
        components_json=row["components_json"],
        metadata_json=row["metadata_json"],
        created_at=row["created_at"],
    )


def _row_to_morphism(row: sqlite3.Row) -> StoredMorphism:
    return StoredMorphism(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        kind=row["kind"],
        label=row["label"],
        created_at=row["created_at"],
    )


def _row_to_judgment(row: sqlite3.Row) -> StoredJudgment:
    return StoredJudgment(
        id=row["id"],
        coordinate_id=row["coordinate_id"],
        proposition=row["proposition"],
        trust_level=row["trust_level"],
        status=row["status"],
        carrier_json=row["carrier_json"],
        evidence_json=row["evidence_json"],
        obligations_json=row["obligations_json"],
        obstructions_json=row["obstructions_json"],
        provenance_json=row["provenance_json"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_evidence(row: sqlite3.Row) -> StoredEvidence:
    return StoredEvidence(
        id=row["id"],
        judgment_id=row["judgment_id"],
        channel=row["channel"],
        trust_level=row["trust_level"],
        claim=row["claim"],
        payload_json=row["payload_json"],
        coordinate_id=row["coordinate_id"],
        timestamp=row["timestamp"],
        record_id=row["record_id"],
        support_json=row["support_json"],
        provenance_json=row["provenance_json"],
    )


def _row_to_obligation(row: sqlite3.Row) -> StoredObligation:
    return StoredObligation(
        id=row["id"],
        judgment_id=row["judgment_id"],
        coordinate_id=row["coordinate_id"],
        proposition=row["proposition"],
        status=row["status"],
        priority=row["priority"],
        created_at=row["created_at"],
        deadline=row["deadline"],
        assigned_to=row["assigned_to"],
        support_json=row["support_json"],
    )


def _row_to_obstruction(row: sqlite3.Row) -> StoredObstruction:
    return StoredObstruction(
        id=row["id"],
        coordinate_id=row["coordinate_id"],
        kind=row["kind"],
        proposition=row["proposition"],
        cohomology_class=row["cohomology_class"],
        repair_frontier_json=row["repair_frontier_json"],
        blast_radius=row["blast_radius"],
        countermodel_json=row["countermodel_json"],
        severity=row["severity"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _row_to_treaty(row: sqlite3.Row) -> StoredTreaty:
    return StoredTreaty(
        id=row["id"],
        parties_json=row["parties_json"],
        overlap_coordinates_json=row["overlap_coordinates_json"],
        propositions_json=row["propositions_json"],
        status=row["status"],
        trust_floor=row["trust_floor"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_certificate(row: sqlite3.Row) -> StoredCertificate:
    return StoredCertificate(
        id=row["id"],
        judgment_id=row["judgment_id"],
        coordinate_id=row["coordinate_id"],
        trust_level=row["trust_level"],
        evidence_chain_json=row["evidence_chain_json"],
        version=row["version"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        issuer=row["issuer"],
    )


# ---------------------------------------------------------------------------
# Thread-local connection pool
# ---------------------------------------------------------------------------

class _ConnectionPool:
    """Per-thread SQLite connection pool.

    For file-based databases, each OS thread gets its own
    ``sqlite3.Connection`` (created lazily, cached for the thread's lifetime).
    This avoids ``check_same_thread`` issues while keeping per-thread state.

    For in-memory databases (``":memory:"``), a single shared connection is
    used with a threading lock to serialise access, because SQLite in-memory
    databases are connection-scoped — separate connections see empty databases.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._is_memory = db_path == ":memory:"
        # Thread-local storage for file-backed connections
        self._local = threading.local()
        # Shared state for in-memory databases
        self._shared_conn: Optional[sqlite3.Connection] = None
        self._shared_lock = threading.Lock()
        # DDL to replay on new thread connections (file-backed)
        self._ddl_lock = threading.Lock()
        self._schema_ddl: list[str] = []

    def set_schema_ddl(self, ddl_blocks: list[str]) -> None:
        """Store the DDL to replay on every new thread connection (file-backed only)."""
        with self._ddl_lock:
            self._schema_ddl = list(ddl_blocks)

    def acquire(self) -> sqlite3.Connection:
        if self._is_memory:
            return self._acquire_shared()
        return self._acquire_thread_local()

    def _acquire_shared(self) -> sqlite3.Connection:
        """Return the single shared in-memory connection (thread-safe via lock)."""
        with self._shared_lock:
            if self._shared_conn is None:
                self._shared_conn = sqlite3.connect(
                    self._db_path,
                    timeout=30.0,
                    check_same_thread=False,
                )
                self._shared_conn.row_factory = sqlite3.Row
                self._shared_conn.executescript(_PRAGMA_SETUP)
                self._shared_conn.commit()
            return self._shared_conn

    def _acquire_thread_local(self) -> sqlite3.Connection:
        """Return (or lazily create) the per-thread file-backed connection."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.executescript(_PRAGMA_SETUP)
            # Replay schema DDL so new threads see the correct schema
            with self._ddl_lock:
                ddl_blocks = list(self._schema_ddl)
            for ddl_block in ddl_blocks:
                for stmt in ddl_block.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        try:
                            conn.execute(stmt)
                        except sqlite3.Error:
                            pass  # IF NOT EXISTS guards handle duplicates
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        if self._is_memory:
            with self._shared_lock:
                if self._shared_conn is not None:
                    try:
                        self._shared_conn.close()
                    except Exception:
                        pass
                    self._shared_conn = None
        else:
            conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    @property
    def db_path(self) -> str:
        return self._db_path


# ---------------------------------------------------------------------------
# SQLiteBackend
# ---------------------------------------------------------------------------

class SQLiteBackend(Store):
    """Fully-featured SQLite implementation of :class:`Store`.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Use ``":memory:"`` for tests.
    auto_initialize:
        When ``True`` (default), call :meth:`initialize` in ``__init__``.
    """

    def __init__(self, db_path: str = ":memory:", *, auto_initialize: bool = True) -> None:
        self._db_path = db_path
        self._pool = _ConnectionPool(db_path)
        self._in_transaction: bool = False
        # Dedicated lock to serialise SQL operations (separate from pool's connection lock)
        self._op_lock: threading.Lock = threading.Lock()
        if auto_initialize:
            self.initialize()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._pool.acquire()

    def _execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
        conn = self._conn  # acquire connection BEFORE holding lock
        try:
            with self._op_lock:
                return conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error: {exc}") from exc

    def _execute_commit(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
        """Execute *sql* and commit atomically under the operation lock."""
        conn = self._conn  # acquire connection BEFORE holding lock
        try:
            with self._op_lock:
                cur = conn.execute(sql, params)
                if not self._in_transaction:
                    conn.commit()
                return cur
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error: {exc}") from exc

    def _executemany(self, sql: str, params_iter: Iterable[Any]) -> sqlite3.Cursor:
        conn = self._conn
        try:
            with self._op_lock:
                return conn.executemany(sql, params_iter)
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error during executemany: {exc}") from exc

    def _executemany_commit(self, sql: str, params_iter: Iterable[Any]) -> sqlite3.Cursor:
        """executemany + commit atomically under the operation lock."""
        conn = self._conn
        try:
            with self._op_lock:
                cur = conn.executemany(sql, params_iter)
                if not self._in_transaction:
                    conn.commit()
                return cur
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error during executemany: {exc}") from exc

    def _fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Optional[sqlite3.Row]:
        conn = self._conn
        try:
            with self._op_lock:
                cur = conn.execute(sql, params)
                return cur.fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error: {exc}") from exc

    def _fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
        conn = self._conn
        try:
            with self._op_lock:
                cur = conn.execute(sql, params)
                return cur.fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error: {exc}") from exc

    def _scalar(self, sql: str, params: tuple[Any, ...] | list[Any] = (), default: Any = 0) -> Any:
        conn = self._conn
        try:
            with self._op_lock:
                cur = conn.execute(sql, params)
                row = cur.fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"SQLite error: {exc}") from exc
        if row is None:
            return default
        return row[0]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create all tables and indices.  Idempotent."""
        conn = self._conn  # acquire BEFORE holding lock
        with self._op_lock:
            for ddl_block in _ALL_DDL:
                for stmt in ddl_block.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        try:
                            conn.execute(stmt)
                        except sqlite3.Error as exc:
                            if "already exists" not in str(exc).lower():
                                raise StoreError(f"Schema init error: {exc}") from exc
            conn.commit()
        # Register DDL blocks so that new thread connections replay the schema
        self._pool.set_schema_ddl(_ALL_DDL)

    def close(self) -> None:
        self._pool.close()

    def is_healthy(self) -> bool:
        try:
            self._scalar("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Coordinates
    # ------------------------------------------------------------------

    _COORD_INSERT = """\
INSERT OR REPLACE INTO coordinates
    (id, name, kind, depth, package, module, components_json, metadata_json, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_coordinate(self, coord: StoredCoordinate) -> StoredCoordinate:
        self._execute_commit(
            self._COORD_INSERT,
            (coord.id, coord.name, coord.kind, coord.depth, coord.package,
             coord.module, coord.components_json, coord.metadata_json, coord.created_at),
        )
        return coord

    def get_coordinate(self, coord_id: str) -> Optional[StoredCoordinate]:
        row = self._fetchone("SELECT * FROM coordinates WHERE id = ?", (coord_id,))
        return _row_to_coordinate(row) if row else None

    def query_coordinates(
        self,
        *,
        kind: str | None = None,
        package: str | None = None,
        depth_range: tuple[int, int] | None = None,
        name_prefix: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredCoordinate]:
        qb = _QueryBuilder("SELECT * FROM coordinates")
        qb.eq("kind", kind)
        qb.eq("package", package)
        if depth_range is not None:
            qb.between("depth", depth_range[0], depth_range[1])
        qb.like("name", name_prefix)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_coordinate(r) for r in rows]

    def count_coordinates(self, *, kind: str | None = None, package: str | None = None) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM coordinates")
        qb.eq("kind", kind)
        qb.eq("package", package)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    def delete_coordinate(self, coord_id: str) -> bool:
        cur = self._execute_commit("DELETE FROM coordinates WHERE id = ?", (coord_id,))
        return cur.rowcount > 0

    def bulk_put_coordinates(self, coords: Iterable[StoredCoordinate]) -> list[StoredCoordinate]:
        coord_list = list(coords)
        rows = [
            (c.id, c.name, c.kind, c.depth, c.package, c.module,
             c.components_json, c.metadata_json, c.created_at)
            for c in coord_list
        ]
        self._executemany_commit(self._COORD_INSERT, rows)
        return coord_list

    # ------------------------------------------------------------------
    # Morphisms
    # ------------------------------------------------------------------

    _MORPH_INSERT = """\
INSERT OR REPLACE INTO morphisms (id, source_id, target_id, kind, label, created_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

    def put_morphism(self, morphism: StoredMorphism) -> StoredMorphism:
        self._execute_commit(
            self._MORPH_INSERT,
            (morphism.id, morphism.source_id, morphism.target_id,
             morphism.kind, morphism.label, morphism.created_at),
        )
        return morphism

    def get_morphism(self, morphism_id: str) -> Optional[StoredMorphism]:
        row = self._fetchone("SELECT * FROM morphisms WHERE id = ?", (morphism_id,))
        return _row_to_morphism(row) if row else None

    def query_morphisms(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        kind: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredMorphism]:
        qb = _QueryBuilder("SELECT * FROM morphisms")
        qb.eq("source_id", source_id)
        qb.eq("target_id", target_id)
        qb.eq("kind", kind)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_morphism(r) for r in rows]

    def morphisms_from(self, coord_id: str) -> list[StoredMorphism]:
        rows = self._fetchall("SELECT * FROM morphisms WHERE source_id = ?", (coord_id,))
        return [_row_to_morphism(r) for r in rows]

    def morphisms_to(self, coord_id: str) -> list[StoredMorphism]:
        rows = self._fetchall("SELECT * FROM morphisms WHERE target_id = ?", (coord_id,))
        return [_row_to_morphism(r) for r in rows]

    def count_morphisms(self, *, source_id: str | None = None, kind: str | None = None) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM morphisms")
        qb.eq("source_id", source_id)
        qb.eq("kind", kind)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    def bulk_put_morphisms(self, morphisms: Iterable[StoredMorphism]) -> list[StoredMorphism]:
        m_list = list(morphisms)
        rows = [
            (m.id, m.source_id, m.target_id, m.kind, m.label, m.created_at)
            for m in m_list
        ]
        self._executemany_commit(self._MORPH_INSERT, rows)
        return m_list

    # ------------------------------------------------------------------
    # Judgments
    # ------------------------------------------------------------------

    _JUDG_INSERT = """\
INSERT OR REPLACE INTO judgments
    (id, coordinate_id, proposition, trust_level, status,
     carrier_json, evidence_json, obligations_json,
     obstructions_json, provenance_json, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_judgment(self, judgment: StoredJudgment) -> StoredJudgment:
        self._execute_commit(
            self._JUDG_INSERT,
            (judgment.id, judgment.coordinate_id, judgment.proposition,
             judgment.trust_level, judgment.status, judgment.carrier_json,
             judgment.evidence_json, judgment.obligations_json,
             judgment.obstructions_json, judgment.provenance_json,
             judgment.created_at, judgment.updated_at),
        )
        return judgment

    def get_judgment(self, judgment_id: str) -> Optional[StoredJudgment]:
        row = self._fetchone("SELECT * FROM judgments WHERE id = ?", (judgment_id,))
        return _row_to_judgment(row) if row else None

    def query_judgments(
        self,
        *,
        coordinate_id: str | None = None,
        trust_level: str | None = None,
        status: str | None = None,
        proposition_like: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredJudgment]:
        qb = _QueryBuilder("SELECT * FROM judgments")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("trust_level", trust_level)
        qb.eq("status", status)
        if proposition_like is not None:
            qb.like("proposition", proposition_like)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_judgment(r) for r in rows]

    def count_judgments(
        self,
        *,
        coordinate_id: str | None = None,
        status: str | None = None,
    ) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM judgments")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("status", status)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    _EV_INSERT = """\
INSERT OR REPLACE INTO evidence
    (id, judgment_id, channel, trust_level, claim, payload_json,
     coordinate_id, timestamp, record_id, support_json, provenance_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_evidence(self, evidence: StoredEvidence) -> StoredEvidence:
        self._execute_commit(
            self._EV_INSERT,
            (evidence.id, evidence.judgment_id, evidence.channel,
             evidence.trust_level, evidence.claim, evidence.payload_json,
             evidence.coordinate_id, evidence.timestamp, evidence.record_id,
             evidence.support_json, evidence.provenance_json),
        )
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[StoredEvidence]:
        row = self._fetchone("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        return _row_to_evidence(row) if row else None

    def query_evidence(
        self,
        *,
        coordinate_id: str | None = None,
        channel: str | None = None,
        trust_level: str | None = None,
        time_range: tuple[float, float] | None = None,
        judgment_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredEvidence]:
        qb = _QueryBuilder("SELECT * FROM evidence")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("channel", channel)
        qb.eq("trust_level", trust_level)
        qb.eq("judgment_id", judgment_id)
        if time_range is not None:
            qb.between("timestamp", time_range[0], time_range[1])
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_evidence(r) for r in rows]

    def count_evidence(self, *, coordinate_id: str | None = None, channel: str | None = None) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM evidence")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("channel", channel)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    def bulk_put_evidence(self, records: Iterable[StoredEvidence]) -> list[StoredEvidence]:
        rec_list = list(records)
        rows = [
            (r.id, r.judgment_id, r.channel, r.trust_level, r.claim,
             r.payload_json, r.coordinate_id, r.timestamp, r.record_id,
             r.support_json, r.provenance_json)
            for r in rec_list
        ]
        self._executemany_commit(self._EV_INSERT, rows)
        return rec_list

    # ------------------------------------------------------------------
    # Obligations
    # ------------------------------------------------------------------

    _OBL_INSERT = """\
INSERT OR REPLACE INTO obligations
    (id, judgment_id, coordinate_id, proposition, status, priority,
     created_at, deadline, assigned_to, support_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_obligation(self, obligation: StoredObligation) -> StoredObligation:
        self._execute_commit(
            self._OBL_INSERT,
            (obligation.id, obligation.judgment_id, obligation.coordinate_id,
             obligation.proposition, obligation.status, obligation.priority,
             obligation.created_at, obligation.deadline, obligation.assigned_to,
             obligation.support_json),
        )
        return obligation

    def get_obligation(self, obligation_id: str) -> Optional[StoredObligation]:
        row = self._fetchone("SELECT * FROM obligations WHERE id = ?", (obligation_id,))
        return _row_to_obligation(row) if row else None

    def query_obligations(
        self,
        *,
        status: str | None = None,
        priority: int | None = None,
        coordinate_id: str | None = None,
        deadline_before: float | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredObligation]:
        qb = _QueryBuilder("SELECT * FROM obligations")
        qb.eq("status", status)
        qb.eq("priority", priority)
        qb.eq("coordinate_id", coordinate_id)
        if deadline_before is not None:
            qb.lt("deadline", deadline_before, inclusive=True)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_obligation(r) for r in rows]

    def pending_obligations(self) -> list[StoredObligation]:
        rows = self._fetchall(
            "SELECT * FROM obligations WHERE status = ?",
            (ObligationStatus.PENDING.value,),
        )
        return [_row_to_obligation(r) for r in rows]

    def overdue_obligations(self, now: float | None = None) -> list[StoredObligation]:
        t = now if now is not None else time.time()
        rows = self._fetchall(
            "SELECT * FROM obligations WHERE deadline IS NOT NULL AND deadline < ?"
            " AND status NOT IN (?, ?)",
            (t, ObligationStatus.DISCHARGED.value, ObligationStatus.FAILED.value),
        )
        return [_row_to_obligation(r) for r in rows]

    def count_obligations(self, *, status: str | None = None) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM obligations")
        qb.eq("status", status)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    # ------------------------------------------------------------------
    # Obstructions
    # ------------------------------------------------------------------

    _OBS_INSERT = """\
INSERT OR REPLACE INTO obstructions
    (id, coordinate_id, kind, proposition, cohomology_class,
     repair_frontier_json, blast_radius, countermodel_json, severity,
     created_at, resolved_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_obstruction(self, obstruction: StoredObstruction) -> StoredObstruction:
        self._execute_commit(
            self._OBS_INSERT,
            (obstruction.id, obstruction.coordinate_id, obstruction.kind,
             obstruction.proposition, obstruction.cohomology_class,
             obstruction.repair_frontier_json, obstruction.blast_radius,
             obstruction.countermodel_json, obstruction.severity,
             obstruction.created_at, obstruction.resolved_at),
        )
        return obstruction

    def get_obstruction(self, obstruction_id: str) -> Optional[StoredObstruction]:
        row = self._fetchone("SELECT * FROM obstructions WHERE id = ?", (obstruction_id,))
        return _row_to_obstruction(row) if row else None

    def query_obstructions(
        self,
        *,
        coordinate_id: str | None = None,
        kind: str | None = None,
        severity_min: float | None = None,
        active_only: bool = False,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredObstruction]:
        qb = _QueryBuilder("SELECT * FROM obstructions")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("kind", kind)
        if severity_min is not None:
            qb.gt("severity", severity_min, inclusive=True)
        if active_only:
            qb.is_null("resolved_at", True)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_obstruction(r) for r in rows]

    def active_obstructions(self) -> list[StoredObstruction]:
        rows = self._fetchall("SELECT * FROM obstructions WHERE resolved_at IS NULL")
        return [_row_to_obstruction(r) for r in rows]

    def count_obstructions(self, *, active_only: bool = False) -> int:
        if active_only:
            return self._scalar("SELECT COUNT(*) FROM obstructions WHERE resolved_at IS NULL")
        return self._scalar("SELECT COUNT(*) FROM obstructions")

    # ------------------------------------------------------------------
    # Treaties
    # ------------------------------------------------------------------

    _TREATY_INSERT = """\
INSERT OR REPLACE INTO treaties
    (id, parties_json, overlap_coordinates_json, propositions_json,
     status, trust_floor, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_treaty(self, treaty: StoredTreaty) -> StoredTreaty:
        self._execute_commit(
            self._TREATY_INSERT,
            (treaty.id, treaty.parties_json, treaty.overlap_coordinates_json,
             treaty.propositions_json, treaty.status, treaty.trust_floor,
             treaty.created_at, treaty.updated_at),
        )
        return treaty

    def get_treaty(self, treaty_id: str) -> Optional[StoredTreaty]:
        row = self._fetchone("SELECT * FROM treaties WHERE id = ?", (treaty_id,))
        return _row_to_treaty(row) if row else None

    def query_treaties(
        self,
        *,
        status: str | None = None,
        party: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredTreaty]:
        qb = _QueryBuilder("SELECT * FROM treaties")
        qb.eq("status", status)
        if party is not None:
            qb.json_contains("parties_json", party)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_treaty(r) for r in rows]

    def count_treaties(self, *, status: str | None = None) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM treaties")
        qb.eq("status", status)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    _CERT_INSERT = """\
INSERT OR REPLACE INTO certificates
    (id, judgment_id, coordinate_id, trust_level, evidence_chain_json,
     version, issued_at, expires_at, issuer)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

    def put_certificate(self, certificate: StoredCertificate) -> StoredCertificate:
        self._execute_commit(
            self._CERT_INSERT,
            (certificate.id, certificate.judgment_id, certificate.coordinate_id,
             certificate.trust_level, certificate.evidence_chain_json,
             certificate.version, certificate.issued_at, certificate.expires_at,
             certificate.issuer),
        )
        return certificate

    def get_certificate(self, cert_id: str) -> Optional[StoredCertificate]:
        row = self._fetchone("SELECT * FROM certificates WHERE id = ?", (cert_id,))
        return _row_to_certificate(row) if row else None

    def query_certificates(
        self,
        *,
        coordinate_id: str | None = None,
        trust_level: str | None = None,
        valid_only: bool = False,
        judgment_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredCertificate]:
        qb = _QueryBuilder("SELECT * FROM certificates")
        qb.eq("coordinate_id", coordinate_id)
        qb.eq("trust_level", trust_level)
        qb.eq("judgment_id", judgment_id)
        if valid_only:
            now = time.time()
            # valid = (expires_at IS NULL OR expires_at > now)
            qb._clauses.append("(expires_at IS NULL OR expires_at > ?)")
            qb._params.append(now)
        sql, params = qb.build(limit=limit, offset=offset)
        rows = self._fetchall(sql, params)
        return [_row_to_certificate(r) for r in rows]

    def count_certificates(
        self,
        *,
        coordinate_id: str | None = None,
        valid_only: bool = False,
    ) -> int:
        qb = _QueryBuilder("SELECT COUNT(*) FROM certificates")
        qb.eq("coordinate_id", coordinate_id)
        if valid_only:
            now = time.time()
            qb._clauses.append("(expires_at IS NULL OR expires_at > ?)")
            qb._params.append(now)
        sql, params = qb.build_count()
        return self._scalar(sql, params)

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        if self._in_transaction:
            raise TransactionError("Transaction already in progress")
        conn = self._conn
        with self._op_lock:
            conn.execute("BEGIN")
        self._in_transaction = True

    def commit(self) -> None:
        if not self._in_transaction:
            raise TransactionError("No transaction in progress")
        conn = self._conn
        with self._op_lock:
            conn.commit()
        self._in_transaction = False

    def rollback(self) -> None:
        if not self._in_transaction:
            raise TransactionError("No transaction in progress")
        conn = self._conn
        with self._op_lock:
            conn.rollback()
        self._in_transaction = False

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    _TABLE_NAMES = [
        "coordinates", "morphisms", "judgments", "evidence",
        "obligations", "obstructions", "treaties", "certificates",
    ]

    def table_statistics(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for table in self._TABLE_NAMES:
            count = self._scalar(f"SELECT COUNT(*) FROM {table}")
            stats[table] = {"row_count": count}
        stats["schema_migrations"] = {
            "row_count": self._scalar("SELECT COUNT(*) FROM schema_migrations"),
        }
        return stats

    def storage_size(self) -> int:
        if self._db_path == ":memory:":
            return 0
        try:
            return os.path.getsize(self._db_path)
        except OSError:
            return 0

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> SQLiteBackend:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._in_transaction:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        self.close()


__all__ = ["SQLiteBackend"]
