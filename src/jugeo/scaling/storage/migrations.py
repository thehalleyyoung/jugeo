"""Schema migration manager for the JuGeo SQLite storage backend.

Migrations are versioned SQL scripts that bring the database schema from one
version to the next.  Each :class:`Migration` contains both an ``up_sql``
(apply) and a ``down_sql`` (rollback) script.

The :class:`MigrationManager` tracks applied migrations in a
``schema_migrations`` table and exposes :meth:`~MigrationManager.apply_pending`
/ :meth:`~MigrationManager.rollback_to` to manage schema state.

Usage example::

    from jugeo.scaling.storage.sqlite_backend import SQLiteBackend
    from jugeo.scaling.storage.migrations import MigrationManager, MIGRATIONS

    store = SQLiteBackend("state.db", auto_initialize=False)
    manager = MigrationManager(store, MIGRATIONS)
    manager.apply_pending()
    print("schema version:", manager.current_version())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from jugeo.scaling.storage.store import MigrationError


# ---------------------------------------------------------------------------
# Migration DDL constants
# ---------------------------------------------------------------------------

_V1_UP = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  REAL    NOT NULL,
    description TEXT    NOT NULL
);

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

CREATE TABLE IF NOT EXISTS morphisms (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_morph_source   ON morphisms(source_id);
CREATE INDEX IF NOT EXISTS idx_morph_target   ON morphisms(target_id);
CREATE INDEX IF NOT EXISTS idx_morph_kind     ON morphisms(kind);
CREATE INDEX IF NOT EXISTS idx_morph_src_kind ON morphisms(source_id, kind);

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
CREATE INDEX IF NOT EXISTS idx_judg_coord        ON judgments(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_judg_trust        ON judgments(trust_level);
CREATE INDEX IF NOT EXISTS idx_judg_status       ON judgments(status);
CREATE INDEX IF NOT EXISTS idx_judg_coord_status ON judgments(coordinate_id, status);

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
CREATE INDEX IF NOT EXISTS idx_ev_judgment  ON evidence(judgment_id);
CREATE INDEX IF NOT EXISTS idx_ev_coord     ON evidence(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_ev_channel   ON evidence(channel);
CREATE INDEX IF NOT EXISTS idx_ev_trust     ON evidence(trust_level);
CREATE INDEX IF NOT EXISTS idx_ev_timestamp ON evidence(timestamp);

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
CREATE INDEX IF NOT EXISTS idx_obl_judgment ON obligations(judgment_id);
CREATE INDEX IF NOT EXISTS idx_obl_coord    ON obligations(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_obl_status   ON obligations(status);
CREATE INDEX IF NOT EXISTS idx_obl_priority ON obligations(priority);
CREATE INDEX IF NOT EXISTS idx_obl_deadline ON obligations(deadline);

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
CREATE INDEX IF NOT EXISTS idx_obs_resolved ON obstructions(resolved_at);

CREATE TABLE IF NOT EXISTS treaties (
    id                       TEXT PRIMARY KEY,
    parties_json             TEXT NOT NULL DEFAULT '[]',
    overlap_coordinates_json TEXT NOT NULL DEFAULT '[]',
    propositions_json        TEXT NOT NULL DEFAULT '[]',
    status                   TEXT NOT NULL DEFAULT 'proposed',
    trust_floor              TEXT NOT NULL DEFAULT '',
    created_at               REAL NOT NULL,
    updated_at               REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_treaty_status ON treaties(status);

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
CREATE INDEX IF NOT EXISTS idx_cert_judgment ON certificates(judgment_id);
CREATE INDEX IF NOT EXISTS idx_cert_coord    ON certificates(coordinate_id);
CREATE INDEX IF NOT EXISTS idx_cert_trust    ON certificates(trust_level);
CREATE INDEX IF NOT EXISTS idx_cert_expires  ON certificates(expires_at);
"""

_V1_DOWN = """\
DROP TABLE IF EXISTS certificates;
DROP TABLE IF EXISTS treaties;
DROP TABLE IF EXISTS obstructions;
DROP TABLE IF EXISTS obligations;
DROP TABLE IF EXISTS evidence;
DROP TABLE IF EXISTS judgments;
DROP TABLE IF EXISTS morphisms;
DROP TABLE IF EXISTS coordinates;
DROP TABLE IF EXISTS schema_migrations;
"""

# ---------------------------------------------------------------------------
# v2: Add severity index and record_id index on evidence
# ---------------------------------------------------------------------------

_V2_UP = """\
CREATE INDEX IF NOT EXISTS idx_obs_severity  ON obstructions(severity);
CREATE INDEX IF NOT EXISTS idx_ev_record_id  ON evidence(record_id);
CREATE INDEX IF NOT EXISTS idx_cert_issued   ON certificates(issued_at);
"""

_V2_DOWN = """\
DROP INDEX IF EXISTS idx_obs_severity;
DROP INDEX IF EXISTS idx_ev_record_id;
DROP INDEX IF EXISTS idx_cert_issued;
"""

# ---------------------------------------------------------------------------
# v3: Add composite indices for hot query paths
# ---------------------------------------------------------------------------

_V3_UP = """\
CREATE INDEX IF NOT EXISTS idx_judg_trust_status ON judgments(trust_level, status);
CREATE INDEX IF NOT EXISTS idx_obl_stat_pri      ON obligations(status, priority);
CREATE INDEX IF NOT EXISTS idx_obs_coord_kind    ON obstructions(coordinate_id, kind);
CREATE INDEX IF NOT EXISTS idx_ev_coord_chan      ON evidence(coordinate_id, channel);
CREATE INDEX IF NOT EXISTS idx_cert_coord_trust  ON certificates(coordinate_id, trust_level);
"""

_V3_DOWN = """\
DROP INDEX IF EXISTS idx_judg_trust_status;
DROP INDEX IF EXISTS idx_obl_stat_pri;
DROP INDEX IF EXISTS idx_obs_coord_kind;
DROP INDEX IF EXISTS idx_ev_coord_chan;
DROP INDEX IF EXISTS idx_cert_coord_trust;
"""


# ---------------------------------------------------------------------------
# Migration dataclass
# ---------------------------------------------------------------------------

@dataclass
class Migration:
    """A single schema migration.

    Parameters
    ----------
    version:
        Monotonically increasing integer version number.
    description:
        Human-readable description of what this migration does.
    up_sql:
        SQL script to apply the migration (may contain multiple statements
        separated by semicolons).
    down_sql:
        SQL script to roll back the migration.
    """

    version: int
    description: str
    up_sql: str
    down_sql: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Built-in migrations
# ---------------------------------------------------------------------------

MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Initial schema: all core tables",
        up_sql=_V1_UP,
        down_sql=_V1_DOWN,
    ),
    Migration(
        version=2,
        description="Add severity / record_id / issued_at indices",
        up_sql=_V2_UP,
        down_sql=_V2_DOWN,
    ),
    Migration(
        version=3,
        description="Add composite indices for hot query paths",
        up_sql=_V3_UP,
        down_sql=_V3_DOWN,
    ),
]


# ---------------------------------------------------------------------------
# MigrationManager
# ---------------------------------------------------------------------------

class MigrationManager:
    """Manages schema migrations for a SQLite database.

    Parameters
    ----------
    backend:
        An initialised :class:`~jugeo.scaling.storage.sqlite_backend.SQLiteBackend`.
    migrations:
        Ordered list of :class:`Migration` objects (default: ``MIGRATIONS``).
    """

    def __init__(self, backend: Any, migrations: list[Migration] | None = None) -> None:
        self._backend = backend
        self._migrations: list[Migration] = sorted(
            migrations or MIGRATIONS, key=lambda m: m.version
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @property
    def _conn(self):  # type: ignore[return]
        return self._backend._pool.acquire()

    def _ensure_tracking_table(self) -> None:
        """Create the schema_migrations table if absent."""
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL, description TEXT NOT NULL)"
        )
        self._conn.commit()

    def _applied_versions(self) -> set[int]:
        self._ensure_tracking_table()
        rows = self._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        return {r[0] for r in rows}

    def _record_migration(self, version: int, description: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (version, time.time(), description),
        )
        self._conn.commit()

    def _remove_migration(self, version: int) -> None:
        self._conn.execute(
            "DELETE FROM schema_migrations WHERE version = ?", (version,)
        )
        self._conn.commit()

    def _run_sql_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script."""
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    self._conn.execute(stmt)
                except Exception as exc:
                    # Ignore "already exists" / "no such index" errors
                    msg = str(exc).lower()
                    if "already exists" in msg or "no such index" in msg:
                        continue
                    raise MigrationError(f"Migration SQL failed: {exc}\nSQL: {stmt}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_version(self) -> int:
        """Return the highest applied migration version, or 0 if none."""
        self._ensure_tracking_table()
        row = self._conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        result = row[0] if row else None
        return result if result is not None else 0

    def pending_migrations(self) -> list[Migration]:
        """Return migrations not yet applied, in version order."""
        applied = self._applied_versions()
        return [m for m in self._migrations if m.version not in applied]

    def apply_pending(self) -> list[int]:
        """Apply all pending migrations.  Returns list of applied version numbers."""
        pending = self.pending_migrations()
        applied: list[int] = []
        for migration in pending:
            try:
                self._run_sql_script(migration.up_sql)
                self._record_migration(migration.version, migration.description)
                applied.append(migration.version)
            except MigrationError:
                raise
            except Exception as exc:
                raise MigrationError(
                    f"Failed to apply migration v{migration.version}: {exc}"
                ) from exc
        return applied

    def rollback_to(self, target_version: int) -> list[int]:
        """Roll back all migrations with version > *target_version*.

        Returns the list of rolled-back version numbers in descending order.
        """
        applied = self._applied_versions()
        to_rollback = sorted(
            [v for v in applied if v > target_version], reverse=True
        )
        rolled_back: list[int] = []
        version_map = {m.version: m for m in self._migrations}
        for version in to_rollback:
            migration = version_map.get(version)
            if migration is None:
                raise MigrationError(
                    f"Cannot roll back version {version}: migration not found"
                )
            try:
                self._run_sql_script(migration.down_sql)
                self._remove_migration(version)
                rolled_back.append(version)
            except Exception as exc:
                raise MigrationError(
                    f"Failed to roll back migration v{version}: {exc}"
                ) from exc
        return rolled_back

    def migration_history(self) -> list[dict[str, Any]]:
        """Return a list of applied migration records, newest first."""
        self._ensure_tracking_table()
        rows = self._conn.execute(
            "SELECT version, applied_at, description FROM schema_migrations "
            "ORDER BY version DESC"
        ).fetchall()
        return [
            {"version": r[0], "applied_at": r[1], "description": r[2]}
            for r in rows
        ]

    def is_up_to_date(self) -> bool:
        """Return ``True`` when all known migrations are applied."""
        return len(self.pending_migrations()) == 0


__all__ = [
    "Migration",
    "MigrationManager",
    "MIGRATIONS",
]
