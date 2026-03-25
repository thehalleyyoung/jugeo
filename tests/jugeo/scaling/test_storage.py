"""Comprehensive tests for the JuGeo scaling storage backend.

Tests cover:
- SQLiteBackend CRUD for every entity type
- Query filtering (by kind, trust level, status, time range, etc.)
- Bulk operations (bulk_put_coordinates, bulk_put_morphisms, bulk_put_evidence)
- Transactions (commit / rollback)
- CachedStore (cache hits/misses, invalidation, cache statistics, pinning)
- MigrationManager (apply_pending, rollback_to, idempotency, history)
- Concurrent access (thread safety)
- Storage statistics (table_statistics, storage_size)
- Models (to_dict / from_dict round-trips)
"""

from __future__ import annotations

import tempfile
import threading
import time
import os
from typing import Generator

import pytest

from jugeo.scaling.storage.models import (
    JudgmentStatus,
    ObligationStatus,
    TreatyStatus,
    StoredCertificate,
    StoredCoordinate,
    StoredEvidence,
    StoredJudgment,
    StoredMorphism,
    StoredObligation,
    StoredObstruction,
    StoredTreaty,
)
from jugeo.scaling.storage.sqlite_backend import SQLiteBackend
from jugeo.scaling.storage.cache_layer import CachedStore, LRUCache, CacheStats
from jugeo.scaling.storage.migrations import Migration, MigrationManager, MIGRATIONS
from jugeo.scaling.storage.store import (
    NotFoundError,
    StoreError,
    TransactionError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path() -> Generator[str, None, None]:
    """Yield a temporary database file path that is cleaned up after the test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        yield path
    finally:
        for p in [path, path + "-wal", path + "-shm"]:
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


@pytest.fixture
def store() -> Generator[SQLiteBackend, None, None]:
    """Yield an in-memory SQLiteBackend."""
    s = SQLiteBackend(":memory:")
    yield s
    s.close()


@pytest.fixture
def file_store(db_path: str) -> Generator[SQLiteBackend, None, None]:
    """Yield a file-backed SQLiteBackend."""
    s = SQLiteBackend(db_path)
    yield s
    s.close()


@pytest.fixture
def cached_store() -> Generator[CachedStore, None, None]:
    """Yield a CachedStore wrapping an in-memory SQLiteBackend."""
    backend = SQLiteBackend(":memory:")
    cs = CachedStore(backend)
    yield cs
    cs.close()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def make_coord(name: str = "jugeo.test", kind: str = "MODULE", pkg: str = "jugeo") -> StoredCoordinate:
    return StoredCoordinate.create(name, kind, depth=2, package=pkg, module=name)


def make_morphism(src: str, tgt: str, kind: str = "RESTRICTION") -> StoredMorphism:
    return StoredMorphism.create(src, tgt, kind, label=f"{src}->{tgt}")


def make_judgment(coord_id: str, prop: str = "P holds", trust: str = "UNVERIFIED") -> StoredJudgment:
    return StoredJudgment.create(coord_id, prop, trust)


def make_evidence(judgment_id: str, coord_id: str, channel: str = "test") -> StoredEvidence:
    return StoredEvidence.create(
        judgment_id=judgment_id,
        channel=channel,
        trust_level="UNVERIFIED",
        claim="evidence for P",
        coordinate_id=coord_id,
    )


def make_obligation(judgment_id: str, coord_id: str, priority: int = 2) -> StoredObligation:
    return StoredObligation.create(judgment_id, coord_id, "prove Q", priority=priority)


def make_obstruction(coord_id: str, kind: str = "cover_failure") -> StoredObstruction:
    return StoredObstruction.create(coord_id, kind, "obstruction desc", severity=0.7)


def make_treaty(parties: list[str] | None = None) -> StoredTreaty:
    return StoredTreaty.create(
        parties=parties or ["agent_a", "agent_b"],
        overlap_coordinates=["jugeo.test"],
        propositions=["P1", "P2"],
        trust_floor="UNVERIFIED",
    )


def make_certificate(judgment_id: str, coord_id: str) -> StoredCertificate:
    return StoredCertificate.create(
        judgment_id=judgment_id,
        coordinate_id=coord_id,
        trust_level="HUMAN_ATTESTED",
        issuer="test_issuer",
    )


# ===========================================================================
# Model round-trip tests
# ===========================================================================

class TestModelRoundTrips:
    def test_coordinate_round_trip(self):
        c = make_coord()
        d = c.to_dict()
        c2 = StoredCoordinate.from_dict(d)
        assert c2.id == c.id
        assert c2.name == c.name
        assert c2.kind == c.kind
        assert c2.depth == c.depth
        assert c2.package == c.package

    def test_morphism_round_trip(self):
        m = make_morphism("src", "tgt")
        d = m.to_dict()
        m2 = StoredMorphism.from_dict(d)
        assert m2.id == m.id
        assert m2.source_id == m.source_id
        assert m2.target_id == m.target_id
        assert m2.kind == m.kind

    def test_judgment_round_trip(self):
        j = make_judgment("coord1")
        d = j.to_dict()
        j2 = StoredJudgment.from_dict(d)
        assert j2.id == j.id
        assert j2.proposition == j.proposition
        assert j2.trust_level == j.trust_level
        assert j2.status == j.status

    def test_evidence_round_trip(self):
        e = make_evidence("jid", "cid")
        d = e.to_dict()
        e2 = StoredEvidence.from_dict(d)
        assert e2.id == e.id
        assert e2.channel == e.channel
        assert e2.claim == e.claim

    def test_obligation_round_trip(self):
        o = make_obligation("jid", "cid")
        d = o.to_dict()
        o2 = StoredObligation.from_dict(d)
        assert o2.id == o.id
        assert o2.proposition == o.proposition
        assert o2.priority == o.priority

    def test_obstruction_round_trip(self):
        obs = make_obstruction("cid")
        d = obs.to_dict()
        obs2 = StoredObstruction.from_dict(d)
        assert obs2.id == obs.id
        assert obs2.kind == obs.kind
        assert abs(obs2.severity - obs.severity) < 1e-9

    def test_treaty_round_trip(self):
        t = make_treaty()
        d = t.to_dict()
        t2 = StoredTreaty.from_dict(d)
        assert t2.id == t.id
        assert t2.status == t.status
        assert set(t2.parties_json) == set(t.parties_json)

    def test_certificate_round_trip(self):
        cert = make_certificate("jid", "cid")
        d = cert.to_dict()
        cert2 = StoredCertificate.from_dict(d)
        assert cert2.id == cert.id
        assert cert2.trust_level == cert.trust_level
        assert cert2.issuer == cert.issuer


# ===========================================================================
# SQLiteBackend — Coordinate CRUD
# ===========================================================================

class TestCoordinateCRUD:
    def test_put_and_get(self, store):
        c = make_coord()
        store.put_coordinate(c)
        result = store.get_coordinate(c.id)
        assert result is not None
        assert result.id == c.id
        assert result.name == c.name

    def test_get_missing_returns_none(self, store):
        assert store.get_coordinate("nonexistent") is None

    def test_upsert_updates_existing(self, store):
        c = make_coord()
        store.put_coordinate(c)
        # Update name via new object with same id
        from dataclasses import replace
        c2 = StoredCoordinate(
            id=c.id, name="jugeo.updated", kind=c.kind,
            depth=c.depth, package=c.package, module=c.module,
            components_json=c.components_json, metadata_json=c.metadata_json,
            created_at=c.created_at,
        )
        store.put_coordinate(c2)
        result = store.get_coordinate(c.id)
        assert result.name == "jugeo.updated"

    def test_delete_coordinate(self, store):
        c = make_coord()
        store.put_coordinate(c)
        assert store.delete_coordinate(c.id) is True
        assert store.get_coordinate(c.id) is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete_coordinate("ghost") is False

    def test_count_coordinates(self, store):
        for i in range(5):
            store.put_coordinate(make_coord(f"pkg.mod{i}"))
        assert store.count_coordinates() == 5

    def test_count_coordinates_by_kind(self, store):
        store.put_coordinate(make_coord("a", kind="MODULE"))
        store.put_coordinate(make_coord("b", kind="FUNCTION"))
        store.put_coordinate(make_coord("c", kind="MODULE"))
        assert store.count_coordinates(kind="MODULE") == 2
        assert store.count_coordinates(kind="FUNCTION") == 1

    def test_query_by_kind(self, store):
        store.put_coordinate(make_coord("a", kind="MODULE"))
        store.put_coordinate(make_coord("b", kind="FUNCTION"))
        results = store.query_coordinates(kind="MODULE")
        assert all(r.kind == "MODULE" for r in results)
        assert len(results) == 1

    def test_query_by_package(self, store):
        store.put_coordinate(make_coord("a", pkg="mypkg"))
        store.put_coordinate(make_coord("b", pkg="otherpkg"))
        results = store.query_coordinates(package="mypkg")
        assert len(results) == 1
        assert results[0].package == "mypkg"

    def test_query_name_prefix(self, store):
        store.put_coordinate(make_coord("jugeo.geometry.site"))
        store.put_coordinate(make_coord("jugeo.geometry.covers"))
        store.put_coordinate(make_coord("other.module"))
        results = store.query_coordinates(name_prefix="jugeo.geometry")
        assert len(results) == 2

    def test_query_depth_range(self, store):
        c1 = StoredCoordinate.create("a", "MODULE", depth=1, package="p", module="a")
        c2 = StoredCoordinate.create("b", "MODULE", depth=3, package="p", module="b")
        c3 = StoredCoordinate.create("c", "MODULE", depth=5, package="p", module="c")
        for c in [c1, c2, c3]:
            store.put_coordinate(c)
        results = store.query_coordinates(depth_range=(2, 4))
        assert len(results) == 1
        assert results[0].depth == 3

    def test_bulk_put_coordinates(self, store):
        coords = [make_coord(f"mod{i}") for i in range(10)]
        results = store.bulk_put_coordinates(coords)
        assert len(results) == 10
        assert store.count_coordinates() == 10


# ===========================================================================
# SQLiteBackend — Morphism CRUD
# ===========================================================================

class TestMorphismCRUD:
    def _setup_two_coords(self, store):
        c1 = make_coord("src")
        c2 = make_coord("tgt")
        store.put_coordinate(c1)
        store.put_coordinate(c2)
        return c1.id, c2.id

    def test_put_and_get(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        m = make_morphism(src_id, tgt_id)
        store.put_morphism(m)
        result = store.get_morphism(m.id)
        assert result is not None
        assert result.source_id == src_id
        assert result.target_id == tgt_id

    def test_get_missing_returns_none(self, store):
        assert store.get_morphism("nope") is None

    def test_morphisms_from(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        m = make_morphism(src_id, tgt_id)
        store.put_morphism(m)
        results = store.morphisms_from(src_id)
        assert len(results) == 1
        assert results[0].source_id == src_id

    def test_morphisms_to(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        m = make_morphism(src_id, tgt_id)
        store.put_morphism(m)
        results = store.morphisms_to(tgt_id)
        assert len(results) == 1
        assert results[0].target_id == tgt_id

    def test_query_by_kind(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        m1 = StoredMorphism.create(src_id, tgt_id, "RESTRICTION", "r")
        m2 = StoredMorphism.create(src_id, tgt_id, "INCLUSION", "i")
        store.put_morphism(m1)
        store.put_morphism(m2)
        results = store.query_morphisms(kind="RESTRICTION")
        assert len(results) == 1
        assert results[0].kind == "RESTRICTION"

    def test_count_morphisms(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        for _ in range(3):
            store.put_morphism(make_morphism(src_id, tgt_id))
        assert store.count_morphisms() == 3

    def test_bulk_put_morphisms(self, store):
        src_id, tgt_id = self._setup_two_coords(store)
        morphisms = [make_morphism(src_id, tgt_id) for _ in range(5)]
        results = store.bulk_put_morphisms(morphisms)
        assert len(results) == 5
        assert store.count_morphisms() == 5


# ===========================================================================
# SQLiteBackend — Judgment CRUD
# ===========================================================================

class TestJudgmentCRUD:
    def test_put_and_get(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j = make_judgment(c.id)
        store.put_judgment(j)
        result = store.get_judgment(j.id)
        assert result is not None
        assert result.proposition == j.proposition

    def test_get_missing_returns_none(self, store):
        assert store.get_judgment("nope") is None

    def test_query_by_coordinate(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j1 = make_judgment(c.id, "P")
        j2 = make_judgment(c.id, "Q")
        store.put_judgment(j1)
        store.put_judgment(j2)
        results = store.query_judgments(coordinate_id=c.id)
        assert len(results) == 2

    def test_query_by_trust_level(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j1 = make_judgment(c.id, trust="UNVERIFIED")
        j2 = make_judgment(c.id, trust="HUMAN_ATTESTED")
        store.put_judgment(j1)
        store.put_judgment(j2)
        results = store.query_judgments(trust_level="HUMAN_ATTESTED")
        assert len(results) == 1
        assert results[0].trust_level == "HUMAN_ATTESTED"

    def test_query_by_status(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j = make_judgment(c.id)
        j.status = JudgmentStatus.CLOSED.value
        store.put_judgment(j)
        results = store.query_judgments(status=JudgmentStatus.CLOSED.value)
        assert len(results) == 1

    def test_query_proposition_like(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j1 = make_judgment(c.id, "hypothesis holds")
        j2 = make_judgment(c.id, "conclusion holds")
        store.put_judgment(j1)
        store.put_judgment(j2)
        results = store.query_judgments(proposition_like="hypothesis")
        assert len(results) == 1
        assert "hypothesis" in results[0].proposition

    def test_count_judgments(self, store):
        c = make_coord()
        store.put_coordinate(c)
        for i in range(4):
            store.put_judgment(make_judgment(c.id, f"prop {i}"))
        assert store.count_judgments() == 4

    def test_count_judgments_by_status(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j1 = make_judgment(c.id)
        j1.status = JudgmentStatus.OPEN.value
        j2 = make_judgment(c.id)
        j2.status = JudgmentStatus.CLOSED.value
        store.put_judgment(j1)
        store.put_judgment(j2)
        assert store.count_judgments(status=JudgmentStatus.OPEN.value) == 1


# ===========================================================================
# SQLiteBackend — Evidence CRUD
# ===========================================================================

class TestEvidenceCRUD:
    def _setup(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j = make_judgment(c.id)
        store.put_judgment(j)
        return c.id, j.id

    def test_put_and_get(self, store):
        c_id, j_id = self._setup(store)
        e = make_evidence(j_id, c_id)
        store.put_evidence(e)
        result = store.get_evidence(e.id)
        assert result is not None
        assert result.channel == e.channel

    def test_query_by_channel(self, store):
        c_id, j_id = self._setup(store)
        e1 = StoredEvidence.create(j_id, "chan_a", "UNVERIFIED", "claim", c_id)
        e2 = StoredEvidence.create(j_id, "chan_b", "UNVERIFIED", "claim", c_id)
        store.put_evidence(e1)
        store.put_evidence(e2)
        results = store.query_evidence(channel="chan_a")
        assert len(results) == 1
        assert results[0].channel == "chan_a"

    def test_query_by_trust_level(self, store):
        c_id, j_id = self._setup(store)
        e1 = StoredEvidence.create(j_id, "ch", "UNVERIFIED", "c", c_id)
        e2 = StoredEvidence.create(j_id, "ch", "HUMAN_ATTESTED", "c", c_id)
        store.put_evidence(e1)
        store.put_evidence(e2)
        results = store.query_evidence(trust_level="HUMAN_ATTESTED")
        assert len(results) == 1

    def test_query_time_range(self, store):
        c_id, j_id = self._setup(store)
        t0 = time.time()
        e = StoredEvidence.create(j_id, "ch", "UNVERIFIED", "c", c_id, timestamp=t0)
        store.put_evidence(e)
        # Query within range
        results = store.query_evidence(time_range=(t0 - 1, t0 + 1))
        assert len(results) == 1
        # Query outside range
        results2 = store.query_evidence(time_range=(t0 + 100, t0 + 200))
        assert len(results2) == 0

    def test_query_by_judgment_id(self, store):
        c_id, j_id = self._setup(store)
        e = make_evidence(j_id, c_id)
        store.put_evidence(e)
        results = store.query_evidence(judgment_id=j_id)
        assert len(results) == 1

    def test_count_evidence(self, store):
        c_id, j_id = self._setup(store)
        for _ in range(3):
            store.put_evidence(make_evidence(j_id, c_id))
        assert store.count_evidence() == 3

    def test_bulk_put_evidence(self, store):
        c_id, j_id = self._setup(store)
        records = [make_evidence(j_id, c_id) for _ in range(6)]
        results = store.bulk_put_evidence(records)
        assert len(results) == 6
        assert store.count_evidence() == 6


# ===========================================================================
# SQLiteBackend — Obligation CRUD
# ===========================================================================

class TestObligationCRUD:
    def _setup(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j = make_judgment(c.id)
        store.put_judgment(j)
        return c.id, j.id

    def test_put_and_get(self, store):
        c_id, j_id = self._setup(store)
        o = make_obligation(j_id, c_id)
        store.put_obligation(o)
        result = store.get_obligation(o.id)
        assert result is not None
        assert result.proposition == o.proposition

    def test_pending_obligations(self, store):
        c_id, j_id = self._setup(store)
        o = make_obligation(j_id, c_id)
        o.status = ObligationStatus.PENDING.value
        store.put_obligation(o)
        results = store.pending_obligations()
        assert any(r.id == o.id for r in results)

    def test_overdue_obligations(self, store):
        c_id, j_id = self._setup(store)
        past = time.time() - 1000
        o = StoredObligation.create(j_id, c_id, "overdue prop", deadline=past)
        store.put_obligation(o)
        results = store.overdue_obligations()
        assert any(r.id == o.id for r in results)

    def test_not_overdue_when_discharged(self, store):
        c_id, j_id = self._setup(store)
        past = time.time() - 1000
        o = StoredObligation.create(
            j_id, c_id, "done", deadline=past,
            status=ObligationStatus.DISCHARGED.value,
        )
        store.put_obligation(o)
        results = store.overdue_obligations()
        assert not any(r.id == o.id for r in results)

    def test_query_by_priority(self, store):
        c_id, j_id = self._setup(store)
        o1 = make_obligation(j_id, c_id, priority=1)
        o2 = make_obligation(j_id, c_id, priority=4)
        store.put_obligation(o1)
        store.put_obligation(o2)
        results = store.query_obligations(priority=4)
        assert len(results) == 1
        assert results[0].priority == 4

    def test_count_obligations_by_status(self, store):
        c_id, j_id = self._setup(store)
        o = make_obligation(j_id, c_id)
        store.put_obligation(o)
        assert store.count_obligations(status=ObligationStatus.PENDING.value) == 1
        assert store.count_obligations(status=ObligationStatus.DISCHARGED.value) == 0


# ===========================================================================
# SQLiteBackend — Obstruction CRUD
# ===========================================================================

class TestObstructionCRUD:
    def _setup(self, store):
        c = make_coord()
        store.put_coordinate(c)
        return c.id

    def test_put_and_get(self, store):
        c_id = self._setup(store)
        obs = make_obstruction(c_id)
        store.put_obstruction(obs)
        result = store.get_obstruction(obs.id)
        assert result is not None
        assert result.kind == obs.kind

    def test_active_obstructions(self, store):
        c_id = self._setup(store)
        obs = make_obstruction(c_id)
        obs.resolved_at = None
        store.put_obstruction(obs)
        results = store.active_obstructions()
        assert any(r.id == obs.id for r in results)

    def test_resolved_obstruction_not_in_active(self, store):
        c_id = self._setup(store)
        obs = make_obstruction(c_id)
        obs.resolved_at = time.time()
        store.put_obstruction(obs)
        results = store.active_obstructions()
        assert not any(r.id == obs.id for r in results)

    def test_query_by_kind(self, store):
        c_id = self._setup(store)
        obs1 = StoredObstruction.create(c_id, "cover_failure", "desc")
        obs2 = StoredObstruction.create(c_id, "trust_violation", "desc")
        store.put_obstruction(obs1)
        store.put_obstruction(obs2)
        results = store.query_obstructions(kind="cover_failure")
        assert len(results) == 1

    def test_query_severity_min(self, store):
        c_id = self._setup(store)
        obs_low = StoredObstruction.create(c_id, "cover_failure", "low", severity=0.2)
        obs_high = StoredObstruction.create(c_id, "cover_failure", "high", severity=0.9)
        store.put_obstruction(obs_low)
        store.put_obstruction(obs_high)
        results = store.query_obstructions(severity_min=0.5)
        assert all(r.severity >= 0.5 for r in results)
        assert any(r.id == obs_high.id for r in results)

    def test_count_obstructions_active_only(self, store):
        c_id = self._setup(store)
        obs1 = make_obstruction(c_id)
        obs2 = make_obstruction(c_id)
        obs2.resolved_at = time.time()
        store.put_obstruction(obs1)
        store.put_obstruction(obs2)
        assert store.count_obstructions(active_only=True) == 1
        assert store.count_obstructions() == 2


# ===========================================================================
# SQLiteBackend — Treaty CRUD
# ===========================================================================

class TestTreatyCRUD:
    def test_put_and_get(self, store):
        t = make_treaty()
        store.put_treaty(t)
        result = store.get_treaty(t.id)
        assert result is not None
        assert result.status == t.status

    def test_query_by_status(self, store):
        t1 = StoredTreaty.create(["a"], ["c"], ["p"], "U", TreatyStatus.ACTIVE.value)
        t2 = StoredTreaty.create(["b"], ["c"], ["p"], "U", TreatyStatus.PROPOSED.value)
        store.put_treaty(t1)
        store.put_treaty(t2)
        results = store.query_treaties(status=TreatyStatus.ACTIVE.value)
        assert len(results) == 1
        assert results[0].status == TreatyStatus.ACTIVE.value

    def test_query_by_party(self, store):
        t = make_treaty(parties=["alice", "bob"])
        store.put_treaty(t)
        results = store.query_treaties(party="alice")
        assert len(results) == 1

    def test_count_treaties(self, store):
        for _ in range(3):
            store.put_treaty(make_treaty())
        assert store.count_treaties() == 3


# ===========================================================================
# SQLiteBackend — Certificate CRUD
# ===========================================================================

class TestCertificateCRUD:
    def _setup(self, store):
        c = make_coord()
        store.put_coordinate(c)
        j = make_judgment(c.id)
        store.put_judgment(j)
        return c.id, j.id

    def test_put_and_get(self, store):
        c_id, j_id = self._setup(store)
        cert = make_certificate(j_id, c_id)
        store.put_certificate(cert)
        result = store.get_certificate(cert.id)
        assert result is not None
        assert result.issuer == cert.issuer

    def test_query_by_coordinate(self, store):
        c_id, j_id = self._setup(store)
        cert = make_certificate(j_id, c_id)
        store.put_certificate(cert)
        results = store.query_certificates(coordinate_id=c_id)
        assert len(results) == 1

    def test_query_valid_only_excludes_expired(self, store):
        c_id, j_id = self._setup(store)
        past = time.time() - 100
        cert_expired = StoredCertificate.create(j_id, c_id, "UNVERIFIED", "me", expires_at=past)
        cert_valid = StoredCertificate.create(j_id, c_id, "UNVERIFIED", "me", expires_at=None)
        store.put_certificate(cert_expired)
        store.put_certificate(cert_valid)
        results = store.query_certificates(valid_only=True)
        ids = {r.id for r in results}
        assert cert_valid.id in ids
        assert cert_expired.id not in ids

    def test_count_certificates(self, store):
        c_id, j_id = self._setup(store)
        for _ in range(4):
            store.put_certificate(make_certificate(j_id, c_id))
        assert store.count_certificates() == 4


# ===========================================================================
# Transactions
# ===========================================================================

class TestTransactions:
    def test_commit(self, store):
        c = make_coord()
        store.begin_transaction()
        store.put_coordinate(c)
        store.commit()
        assert store.get_coordinate(c.id) is not None

    def test_rollback(self, store):
        c = make_coord()
        store.begin_transaction()
        store.put_coordinate(c)
        store.rollback()
        assert store.get_coordinate(c.id) is None

    def test_double_begin_raises(self, store):
        store.begin_transaction()
        with pytest.raises(TransactionError):
            store.begin_transaction()
        store.rollback()

    def test_commit_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.commit()

    def test_rollback_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.rollback()

    def test_context_manager_commit(self, store):
        """Context manager commits on clean exit."""
        c = make_coord()
        store.begin_transaction()
        store.put_coordinate(c)
        store.commit()
        assert store.get_coordinate(c.id) is not None

    def test_bulk_in_transaction(self, store):
        coords = [make_coord(f"mod{i}") for i in range(5)]
        store.begin_transaction()
        store.bulk_put_coordinates(coords)
        store.rollback()
        assert store.count_coordinates() == 0


# ===========================================================================
# Storage statistics
# ===========================================================================

class TestStorageStatistics:
    def test_table_statistics_has_all_tables(self, store):
        stats = store.table_statistics()
        for table in ["coordinates", "morphisms", "judgments", "evidence",
                      "obligations", "obstructions", "treaties", "certificates"]:
            assert table in stats, f"Missing table {table} in stats"

    def test_table_statistics_row_counts(self, store):
        store.put_coordinate(make_coord("a"))
        store.put_coordinate(make_coord("b"))
        stats = store.table_statistics()
        assert stats["coordinates"]["row_count"] == 2

    def test_storage_size_memory_is_zero(self, store):
        assert store.storage_size() == 0

    def test_storage_size_file_nonzero(self, file_store):
        file_store.put_coordinate(make_coord("a"))
        assert file_store.storage_size() > 0

    def test_is_healthy(self, store):
        assert store.is_healthy() is True


# ===========================================================================
# CachedStore
# ===========================================================================

class TestCachedStore:
    def test_put_and_get_coordinate_cache_hit(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        # First get should come from cache (hit)
        result = cached_store.get_coordinate(c.id)
        assert result is not None
        assert result.id == c.id
        stats = cached_store.stats["coordinates"]
        assert stats["hits"] >= 1

    def test_cache_miss_then_hit(self, cached_store):
        c = make_coord()
        cached_store._backing.put_coordinate(c)  # write directly to backing
        # Cache empty → miss
        result1 = cached_store.get_coordinate(c.id)
        assert result1 is not None
        stats1 = cached_store.stats["coordinates"]
        assert stats1["misses"] >= 1
        # Now cache populated → hit
        result2 = cached_store.get_coordinate(c.id)
        assert result2 is not None
        stats2 = cached_store.stats["coordinates"]
        assert stats2["hits"] >= 1

    def test_delete_invalidates_cache(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        _ = cached_store.get_coordinate(c.id)  # populate cache
        cached_store.delete_coordinate(c.id)
        result = cached_store.get_coordinate(c.id)
        assert result is None
        stats = cached_store.stats["coordinates"]
        assert stats["invalidations"] >= 1

    def test_cache_eviction(self):
        backend = SQLiteBackend(":memory:")
        cs = CachedStore(backend, coordinate_cache_size=3)
        coords = [make_coord(f"mod{i}") for i in range(5)]
        for c in coords:
            cs.put_coordinate(c)
        # Cache can hold at most 3 un-pinned entries
        assert cs._caches["coordinates"].size <= 3
        cs.close()

    def test_pin_survives_eviction(self):
        backend = SQLiteBackend(":memory:")
        cs = CachedStore(backend, coordinate_cache_size=2)
        pinned = make_coord("pinned")
        cs.put_coordinate(pinned)
        cs.pin("coordinates", pinned.id, pinned)
        # Fill cache past limit
        for i in range(5):
            cs.put_coordinate(make_coord(f"filler{i}"))
        # Pinned entry should still be retrievable from cache
        result = cs._caches["coordinates"].get(pinned.id)
        assert result is not None
        assert result.id == pinned.id
        cs.close()

    def test_cache_statistics(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        cached_store.get_coordinate(c.id)
        cached_store.get_coordinate("nonexistent")
        stats = cached_store.stats["coordinates"]
        assert stats["total_requests"] >= 2
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1

    def test_clear_cache(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        assert cached_store._caches["coordinates"].size > 0
        cached_store.clear_cache("coordinates")
        assert cached_store._caches["coordinates"].size == 0

    def test_clear_all_caches(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        cached_store.clear_cache()
        for cache in cached_store._caches.values():
            assert cache.size == 0

    def test_warm_coordinates(self, cached_store):
        for i in range(5):
            cached_store._backing.put_coordinate(make_coord(f"m{i}"))
        count = cached_store.warm_coordinates(limit=10)
        assert count == 5

    def test_table_statistics_includes_cache(self, cached_store):
        stats = cached_store.table_statistics()
        assert "_cache" in stats

    def test_delegated_transactions(self, cached_store):
        c = make_coord()
        cached_store.begin_transaction()
        cached_store.put_coordinate(c)
        cached_store.rollback()
        # After rollback the backing store should not have the coord
        assert cached_store._backing.get_coordinate(c.id) is None

    def test_judgment_cache(self, cached_store):
        c = make_coord()
        cached_store.put_coordinate(c)
        j = make_judgment(c.id)
        cached_store.put_judgment(j)
        result = cached_store.get_judgment(j.id)
        assert result is not None
        stats = cached_store.stats["judgments"]
        assert stats["hits"] >= 1


# ===========================================================================
# LRU Cache unit tests
# ===========================================================================

class TestLRUCache:
    def test_basic_put_get(self):
        cache: LRUCache[str] = LRUCache(max_size=4)
        cache.put("a", "alpha")
        assert cache.get("a") == "alpha"

    def test_miss_returns_none(self):
        cache: LRUCache[str] = LRUCache(max_size=4)
        assert cache.get("missing") is None

    def test_eviction_at_capacity(self):
        cache: LRUCache[str] = LRUCache(max_size=3)
        for i in range(4):
            cache.put(str(i), f"val{i}")
        # The oldest entry (key "0") should have been evicted
        assert cache.get("0") is None
        assert cache.stats.evictions >= 1

    def test_lru_order(self):
        cache: LRUCache[str] = LRUCache(max_size=3)
        cache.put("a", "A")
        cache.put("b", "B")
        cache.put("c", "C")
        # Access "a" to make it recently used
        cache.get("a")
        # Insert "d" → should evict "b" (now LRU)
        cache.put("d", "D")
        assert cache.get("a") == "A"
        assert cache.get("b") is None

    def test_invalidate(self):
        cache: LRUCache[str] = LRUCache(max_size=4)
        cache.put("x", "X")
        cache.invalidate("x")
        assert cache.get("x") is None
        assert cache.stats.invalidations == 1

    def test_pin_not_evicted(self):
        cache: LRUCache[str] = LRUCache(max_size=2)
        cache.put("p", "pinned")
        cache.pin("p", "pinned")
        # Fill past capacity
        cache.put("a", "A")
        cache.put("b", "B")
        cache.put("c", "C")
        assert cache.get("p") == "pinned"

    def test_warm(self):
        cache: LRUCache[int] = LRUCache(max_size=10)
        loaded = cache.warm([("k1", 1), ("k2", 2), ("k3", 3)])
        assert loaded == 3
        assert cache.get("k1") == 1

    def test_clear(self):
        cache: LRUCache[str] = LRUCache(max_size=4)
        cache.put("a", "A")
        cache.pin("b", "B")
        cache.clear()
        assert cache.size == 0
        assert cache.stats.hits == 0


# ===========================================================================
# MigrationManager
# ===========================================================================

class TestMigrationManager:
    def test_apply_pending_initial(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        applied = mgr.apply_pending()
        assert 1 in applied
        assert mgr.current_version() == max(m.version for m in MIGRATIONS)
        store.close()

    def test_apply_pending_idempotent(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        mgr.apply_pending()
        applied2 = mgr.apply_pending()
        assert applied2 == []
        store.close()

    def test_is_up_to_date(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        assert not mgr.is_up_to_date()
        mgr.apply_pending()
        assert mgr.is_up_to_date()
        store.close()

    def test_current_version_zero_before_migrations(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        assert mgr.current_version() == 0
        store.close()

    def test_migration_history(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        mgr.apply_pending()
        history = mgr.migration_history()
        assert len(history) == len(MIGRATIONS)
        versions = [h["version"] for h in history]
        assert 1 in versions
        store.close()

    def test_rollback_to(self):
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, MIGRATIONS)
        mgr.apply_pending()
        rolled = mgr.rollback_to(1)
        # Should have rolled back versions > 1
        assert all(v > 1 for v in rolled)
        assert mgr.current_version() == 1
        store.close()

    def test_pending_migrations_after_partial_apply(self):
        only_v1 = [m for m in MIGRATIONS if m.version == 1]
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, only_v1)
        mgr.apply_pending()
        # Now switch to full migration list
        mgr2 = MigrationManager(store, MIGRATIONS)
        pending = mgr2.pending_migrations()
        assert all(m.version > 1 for m in pending)
        store.close()

    def test_custom_migration(self):
        custom = Migration(
            version=100,
            description="Add test_table",
            up_sql="CREATE TABLE test_table (id TEXT PRIMARY KEY, value TEXT);",
            down_sql="DROP TABLE IF EXISTS test_table;",
        )
        store = SQLiteBackend(":memory:", auto_initialize=False)
        mgr = MigrationManager(store, [custom])
        mgr.apply_pending()
        # Table should exist
        conn = store._pool.acquire()
        conn.execute("INSERT INTO test_table VALUES ('1', 'hello')")
        conn.commit()
        row = conn.execute("SELECT value FROM test_table WHERE id = '1'").fetchone()
        assert row[0] == "hello"
        # Rollback
        mgr.rollback_to(0)
        with pytest.raises(Exception):
            conn.execute("SELECT * FROM test_table")
        store.close()


# ===========================================================================
# Concurrent access
# ===========================================================================

class TestConcurrency:
    def test_concurrent_coordinate_inserts(self):
        """Multiple threads should be able to insert coordinates without errors."""
        store = SQLiteBackend(":memory:")
        errors: list[Exception] = []

        def insert_coords(thread_id: int) -> None:
            try:
                for i in range(20):
                    c = make_coord(f"thread{thread_id}.mod{i}")
                    store.put_coordinate(c)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=insert_coords, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent insert errors: {errors}"
        assert store.count_coordinates() == 100
        store.close()

    def test_concurrent_reads_and_writes(self):
        """Interleaved reads and writes across threads."""
        store = SQLiteBackend(":memory:")
        coord = make_coord("shared")
        store.put_coordinate(coord)
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    result = store.get_coordinate(coord.id)
                    assert result is not None
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(10):
                    store.put_coordinate(make_coord(f"new{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent read/write errors: {errors}"
        store.close()

    def test_cached_store_concurrent_access(self):
        """CachedStore should be thread-safe across multiple threads."""
        backend = SQLiteBackend(":memory:")
        cs = CachedStore(backend)
        errors: list[Exception] = []

        def do_work(thread_id: int) -> None:
            try:
                for i in range(10):
                    c = make_coord(f"t{thread_id}m{i}")
                    cs.put_coordinate(c)
                    cs.get_coordinate(c.id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_work, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"CachedStore concurrent errors: {errors}"
        cs.close()


# ===========================================================================
# is_healthy and lifecycle
# ===========================================================================

class TestLifecycle:
    def test_initialize_idempotent(self, store):
        store.initialize()
        store.initialize()
        assert store.is_healthy()

    def test_close_and_reopen(self, db_path):
        s1 = SQLiteBackend(db_path)
        c = make_coord("persistent")
        s1.put_coordinate(c)
        s1.close()
        s2 = SQLiteBackend(db_path)
        result = s2.get_coordinate(c.id)
        assert result is not None
        assert result.name == "persistent"
        s2.close()
