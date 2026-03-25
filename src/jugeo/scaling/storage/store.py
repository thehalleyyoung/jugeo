"""Abstract Store interface for JuGeo persistent storage.

All concrete backends (SQLite, in-memory, distributed) must implement
:class:`Store`.  The interface is intentionally explicit so that IDEs and
type-checkers can verify call-site correctness.

Design notes
------------
* Every mutating method returns the stored object so callers can inspect the
  assigned ``id`` after an insert.
* Query methods accept keyword-only filter arguments; omitting a filter means
  "no constraint on that field".
* Bulk operations accept iterables and return a list of stored objects.
* Transactions follow the context-manager protocol (``begin_transaction`` /
  ``commit`` / ``rollback``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Optional

from jugeo.scaling.storage.models import (
    StoredCertificate,
    StoredCoordinate,
    StoredEvidence,
    StoredJudgment,
    StoredMorphism,
    StoredObligation,
    StoredObstruction,
    StoredTreaty,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class StoreError(Exception):
    """Base class for all store-level errors."""


class NotFoundError(StoreError):
    """Raised when a requested entity does not exist in the store."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(f"{entity_type} not found: {entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id


class DuplicateError(StoreError):
    """Raised when inserting an entity whose id already exists."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(f"{entity_type} already exists: {entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id


class TransactionError(StoreError):
    """Raised for transaction protocol violations."""


class MigrationError(StoreError):
    """Raised when a schema migration fails."""


# ---------------------------------------------------------------------------
# Abstract Store
# ---------------------------------------------------------------------------

class Store(ABC):
    """Abstract persistent store for JuGeo verification state.

    Subclasses must implement every ``@abstractmethod``.  Optional bulk and
    transaction methods have default implementations that wrap the atomic ones
    so that minimal backends can skip them.
    """

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    @abstractmethod
    def initialize(self) -> None:
        """Create schema and run pending migrations.  Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Flush buffers and release resources."""

    @abstractmethod
    def is_healthy(self) -> bool:
        """Return ``True`` when the backend is ready to serve requests."""

    # -----------------------------------------------------------------------
    # Coordinates
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_coordinate(self, coord: StoredCoordinate) -> StoredCoordinate:
        """Upsert *coord* and return the stored object."""

    @abstractmethod
    def get_coordinate(self, coord_id: str) -> Optional[StoredCoordinate]:
        """Return the coordinate with *coord_id*, or ``None``."""

    @abstractmethod
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
        """Return coordinates matching all supplied filters."""

    @abstractmethod
    def count_coordinates(self, *, kind: str | None = None, package: str | None = None) -> int:
        """Return the number of coordinates matching the filters."""

    @abstractmethod
    def delete_coordinate(self, coord_id: str) -> bool:
        """Delete the coordinate; return ``True`` if it existed."""

    # -----------------------------------------------------------------------
    # Morphisms
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_morphism(self, morphism: StoredMorphism) -> StoredMorphism:
        """Upsert *morphism* and return the stored object."""

    @abstractmethod
    def get_morphism(self, morphism_id: str) -> Optional[StoredMorphism]:
        """Return the morphism with *morphism_id*, or ``None``."""

    @abstractmethod
    def query_morphisms(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        kind: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredMorphism]:
        """Return morphisms matching all supplied filters."""

    @abstractmethod
    def morphisms_from(self, coord_id: str) -> list[StoredMorphism]:
        """Return all morphisms with ``source_id == coord_id``."""

    @abstractmethod
    def morphisms_to(self, coord_id: str) -> list[StoredMorphism]:
        """Return all morphisms with ``target_id == coord_id``."""

    @abstractmethod
    def count_morphisms(self, *, source_id: str | None = None, kind: str | None = None) -> int:
        """Return the number of morphisms matching the filters."""

    # -----------------------------------------------------------------------
    # Judgments
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_judgment(self, judgment: StoredJudgment) -> StoredJudgment:
        """Upsert *judgment* and return the stored object."""

    @abstractmethod
    def get_judgment(self, judgment_id: str) -> Optional[StoredJudgment]:
        """Return the judgment with *judgment_id*, or ``None``."""

    @abstractmethod
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
        """Return judgments matching all supplied filters."""

    @abstractmethod
    def count_judgments(
        self,
        *,
        coordinate_id: str | None = None,
        status: str | None = None,
    ) -> int:
        """Return the number of judgments matching the filters."""

    # -----------------------------------------------------------------------
    # Evidence
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_evidence(self, evidence: StoredEvidence) -> StoredEvidence:
        """Upsert *evidence* and return the stored object."""

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> Optional[StoredEvidence]:
        """Return the evidence record with *evidence_id*, or ``None``."""

    @abstractmethod
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
        """Return evidence records matching all supplied filters."""

    @abstractmethod
    def count_evidence(
        self,
        *,
        coordinate_id: str | None = None,
        channel: str | None = None,
    ) -> int:
        """Return the number of evidence records matching the filters."""

    # -----------------------------------------------------------------------
    # Obligations
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_obligation(self, obligation: StoredObligation) -> StoredObligation:
        """Upsert *obligation* and return the stored object."""

    @abstractmethod
    def get_obligation(self, obligation_id: str) -> Optional[StoredObligation]:
        """Return the obligation with *obligation_id*, or ``None``."""

    @abstractmethod
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
        """Return obligations matching all supplied filters."""

    @abstractmethod
    def pending_obligations(self) -> list[StoredObligation]:
        """Return all obligations with status ``PENDING``."""

    @abstractmethod
    def overdue_obligations(self, now: float | None = None) -> list[StoredObligation]:
        """Return obligations whose deadline has passed and are not discharged."""

    @abstractmethod
    def count_obligations(self, *, status: str | None = None) -> int:
        """Return the number of obligations matching the filters."""

    # -----------------------------------------------------------------------
    # Obstructions
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_obstruction(self, obstruction: StoredObstruction) -> StoredObstruction:
        """Upsert *obstruction* and return the stored object."""

    @abstractmethod
    def get_obstruction(self, obstruction_id: str) -> Optional[StoredObstruction]:
        """Return the obstruction with *obstruction_id*, or ``None``."""

    @abstractmethod
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
        """Return obstructions matching all supplied filters."""

    @abstractmethod
    def active_obstructions(self) -> list[StoredObstruction]:
        """Return all obstructions with ``resolved_at IS NULL``."""

    @abstractmethod
    def count_obstructions(self, *, active_only: bool = False) -> int:
        """Return the number of obstructions matching the filters."""

    # -----------------------------------------------------------------------
    # Treaties
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_treaty(self, treaty: StoredTreaty) -> StoredTreaty:
        """Upsert *treaty* and return the stored object."""

    @abstractmethod
    def get_treaty(self, treaty_id: str) -> Optional[StoredTreaty]:
        """Return the treaty with *treaty_id*, or ``None``."""

    @abstractmethod
    def query_treaties(
        self,
        *,
        status: str | None = None,
        party: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredTreaty]:
        """Return treaties matching all supplied filters."""

    @abstractmethod
    def count_treaties(self, *, status: str | None = None) -> int:
        """Return the number of treaties matching the filters."""

    # -----------------------------------------------------------------------
    # Certificates
    # -----------------------------------------------------------------------

    @abstractmethod
    def put_certificate(self, certificate: StoredCertificate) -> StoredCertificate:
        """Upsert *certificate* and return the stored object."""

    @abstractmethod
    def get_certificate(self, cert_id: str) -> Optional[StoredCertificate]:
        """Return the certificate with *cert_id*, or ``None``."""

    @abstractmethod
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
        """Return certificates matching all supplied filters."""

    @abstractmethod
    def count_certificates(
        self,
        *,
        coordinate_id: str | None = None,
        valid_only: bool = False,
    ) -> int:
        """Return the number of certificates matching the filters."""

    # -----------------------------------------------------------------------
    # Bulk operations  (default: loop over atomic puts)
    # -----------------------------------------------------------------------

    def bulk_put_coordinates(
        self, coords: Iterable[StoredCoordinate]
    ) -> list[StoredCoordinate]:
        """Insert/update many coordinates atomically."""
        return [self.put_coordinate(c) for c in coords]

    def bulk_put_morphisms(
        self, morphisms: Iterable[StoredMorphism]
    ) -> list[StoredMorphism]:
        """Insert/update many morphisms atomically."""
        return [self.put_morphism(m) for m in morphisms]

    def bulk_put_evidence(
        self, records: Iterable[StoredEvidence]
    ) -> list[StoredEvidence]:
        """Insert/update many evidence records atomically."""
        return [self.put_evidence(r) for r in records]

    # -----------------------------------------------------------------------
    # Transactions
    # -----------------------------------------------------------------------

    @abstractmethod
    def begin_transaction(self) -> None:
        """Begin an explicit transaction."""

    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the current transaction."""

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    @abstractmethod
    def table_statistics(self) -> dict[str, Any]:
        """Return row counts and size estimates keyed by table name."""

    @abstractmethod
    def storage_size(self) -> int:
        """Return the total on-disk storage used in bytes."""


__all__ = [
    "Store",
    "StoreError",
    "NotFoundError",
    "DuplicateError",
    "TransactionError",
    "MigrationError",
]
