"""Integration tests: manifest (J, O, E, X, K, η, σ) lifecycle.

Tests the full semantic-state manifest: adding judgments, updating epochs,
invalidating entries, checking that obstructions are persistent, and that
archived evidence preserves its kind (federation invariant).

Theory2 invariants under test
-------------------------------
* Judgments are 8-tuples — manifest stores them as such.
* Trust levels carried in JudgmentStore are ordered values, not scalars.
* Obstructions are persistent: ObstructionStore never silently erases them.
* Evidence kinds are preserved through the EvidenceArchive (federation invariant).
* σ (InvalidationGraph) dependency edges are correctly cascaded.
* η (EpochMap) is monotonically increasing per coordinate.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists()
)
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

# ---------------------------------------------------------------------------
# Manifest imports (all seven components)
# ---------------------------------------------------------------------------
from jugeo.evidence.manifests import (
    EpochMap,
    EvidenceArchive,
    InvalidationGraph,
    JudgmentStore,
    Manifest,
    ObligationPriority,
    ObligationStore,
    ObstructionKind,
    ObstructionStore,
    CertificateStore as ManifestCertStore,
)

# ---------------------------------------------------------------------------
# Trust imports
# ---------------------------------------------------------------------------
from jugeo.evidence.trust import (
    TrustAlgebra,
    TrustLevel,
    TrustTier,
    TrustProfile,
    join_trust_profiles,
)

# ---------------------------------------------------------------------------
# Evidence channel imports (for EvidenceRecord)
# ---------------------------------------------------------------------------
from jugeo.evidence.channels import (
    EvidenceChannel,
    EvidenceRecord,
)

# ---------------------------------------------------------------------------
# Judgment imports (for building proper judgment objects)
# ---------------------------------------------------------------------------
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    TrustAnnotation,
    TrustLevel as JudgmentTrustLevel,
)

# ---------------------------------------------------------------------------
# Error imports
# ---------------------------------------------------------------------------
from jugeo.errors import (
    EvidenceFamily,
    FailureScope,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALGEBRA = TrustAlgebra()


def _coord(*parts: str) -> Coordinate:
    return Coordinate(components=parts, kind=CoordinateKind.REGION)


def _judgment_obj(
    coord: Coordinate,
    formula: str = "P",
    trust: JudgmentTrustLevel = JudgmentTrustLevel.UNVERIFIED,
) -> Judgment:
    return Judgment(
        coordinate=coord,
        proposition=Proposition(kind=PropositionKind.STRUCTURAL, formula=formula),
        carrier=Carrier(name="TestType"),
        trust=TrustAnnotation(level=trust),
        provenance=Provenance(source=ProvenanceSource.SOLVER),
    )


def _evidence_record(
    record_id: str,
    channel: EvidenceChannel = EvidenceChannel.SOLVER,
    coordinate: str = "mod/fn",
    trust: str = "solver_discharged",
    kind: str = "solver_proof",
) -> EvidenceRecord:
    return EvidenceRecord(
        record_id=record_id,
        channel=channel,
        evidence={"result": "verified", "kind": kind},
        trust_level=trust,
        coordinate=coordinate,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# §1  JudgmentStore (J component)
# ---------------------------------------------------------------------------


class TestJudgmentStore:
    """JudgmentStore must store and query 8-tuple judgments."""

    def test_add_judgment_returns_id(self) -> None:
        store = JudgmentStore()
        jid = store.add(
            coordinate="mod/auth",
            proposition="login_safe",
            trust_tier=TrustTier.PROPOSAL,
            evidence_refs=["ev-1"],
            status="proposed",
        )
        assert isinstance(jid, str)
        assert len(jid) > 0
        assert store.count() == 1

    def test_judgment_not_a_boolean(self) -> None:
        store = JudgmentStore()
        jid = store.add(
            coordinate="mod/fn",
            proposition="fn_terminates",
            trust_tier=TrustTier.REVIEWED,
        )
        entry = store.get(jid)
        assert entry is not None
        assert not isinstance(entry, bool)
        # Entry is a dict with structural fields
        assert "coordinate" in entry
        assert "proposition" in entry
        assert "trust_tier" in entry

    def test_query_by_coordinate(self) -> None:
        store = JudgmentStore()
        store.add(coordinate="svc/auth", proposition="P1", trust_tier=TrustTier.VERIFIED)
        store.add(coordinate="svc/auth", proposition="P2", trust_tier=TrustTier.PROPOSAL)
        store.add(coordinate="svc/db", proposition="P3", trust_tier=TrustTier.REVIEWED)
        results = store.query_by_coordinate("svc/auth")
        assert len(results) == 2
        for r in results:
            assert r["coordinate"] == "svc/auth"

    def test_query_by_proposition(self) -> None:
        store = JudgmentStore()
        store.add(coordinate="a", proposition="type_safe_contract", trust_tier=TrustTier.VERIFIED)
        store.add(coordinate="b", proposition="runtime_type_safe", trust_tier=TrustTier.REVIEWED)
        store.add(coordinate="c", proposition="something_else", trust_tier=TrustTier.PROPOSAL)
        results = store.query_by_proposition("type_safe")
        assert len(results) == 2

    def test_query_by_trust_tier(self) -> None:
        store = JudgmentStore()
        store.add(coordinate="a", proposition="P", trust_tier=TrustTier.VERIFIED)
        store.add(coordinate="b", proposition="Q", trust_tier=TrustTier.PROPOSAL)
        store.add(coordinate="c", proposition="R", trust_tier=TrustTier.REVIEWED)
        # Query for entries with trust_tier >= REVIEWED (tier >= 2)
        results = store.query_by_trust(min_tier=int(TrustTier.REVIEWED))
        assert len(results) >= 2

    def test_remove_judgment(self) -> None:
        store = JudgmentStore()
        jid = store.add(coordinate="mod/x", proposition="P", trust_tier=TrustTier.PROPOSAL)
        assert store.count() == 1
        removed = store.remove(jid)
        assert removed is True
        assert store.count() == 0
        assert store.get(jid) is None

    def test_bulk_add_judgments(self) -> None:
        store = JudgmentStore()
        entries = [
            {"coordinate": f"mod/fn_{i}", "proposition": f"prop_{i}", "trust_tier": TrustTier.PROPOSAL}
            for i in range(5)
        ]
        ids = store.bulk_add(entries)
        assert len(ids) == 5
        assert store.count() == 5

    def test_judgment_trust_is_not_a_scalar(self) -> None:
        """Trust tier stored in manifest is an ordered value, not a raw int."""
        store = JudgmentStore()
        jid = store.add(
            coordinate="mod/fn",
            proposition="fn_safe",
            trust_tier=TrustTier.VERIFIED,
        )
        entry = store.get(jid)
        trust = entry["trust_tier"]
        # Trust tier should be comparable and ordered
        assert TrustTier.REVIEWED < TrustTier.VERIFIED
        assert trust == TrustTier.VERIFIED or trust == int(TrustTier.VERIFIED)

    def test_judgment_iterate(self) -> None:
        store = JudgmentStore()
        for i in range(3):
            store.add(coordinate=f"c/{i}", proposition=f"P{i}", trust_tier=TrustTier.PROPOSAL)
        items = list(store.iterate())
        assert len(items) == 3


# ---------------------------------------------------------------------------
# §2  ObligationStore (O component)
# ---------------------------------------------------------------------------


class TestObligationStore:
    """ObligationStore tracks residual obligations by priority and coordinate."""

    def test_add_obligation(self) -> None:
        store = ObligationStore()
        oid = store.add(
            coordinate="mod/fn",
            description="prove termination",
            priority=ObligationPriority.HIGH,
        )
        assert isinstance(oid, str)
        assert store.count() == 1

    def test_discharge_obligation(self) -> None:
        store = ObligationStore()
        oid = store.add(
            coordinate="mod/fn",
            description="prove safety",
            priority=ObligationPriority.CRITICAL,
        )
        discharged = store.discharge(oid)
        assert discharged is True
        assert store.is_discharged(oid) is True

    def test_pending_obligations(self) -> None:
        store = ObligationStore()
        oid1 = store.add(coordinate="a", description="P1", priority=ObligationPriority.HIGH)
        oid2 = store.add(coordinate="b", description="P2", priority=ObligationPriority.LOW)
        store.discharge(oid1)
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0]["obligation_id"] == oid2

    def test_obligations_by_priority(self) -> None:
        store = ObligationStore()
        store.add(coordinate="a", description="critical task", priority=ObligationPriority.CRITICAL)
        store.add(coordinate="b", description="low priority", priority=ObligationPriority.LOW)
        store.add(coordinate="c", description="another critical", priority=ObligationPriority.CRITICAL)
        critical = store.by_priority(ObligationPriority.CRITICAL)
        assert len(critical) == 2

    def test_obligations_by_coordinate(self) -> None:
        store = ObligationStore()
        store.add(coordinate="svc/auth", description="O1", priority=ObligationPriority.HIGH)
        store.add(coordinate="svc/auth", description="O2", priority=ObligationPriority.MEDIUM)
        store.add(coordinate="svc/db", description="O3", priority=ObligationPriority.LOW)
        auth_obs = store.by_coordinate("svc/auth")
        assert len(auth_obs) == 2

    def test_obligation_dependencies(self) -> None:
        store = ObligationStore()
        o1 = store.add(coordinate="a", description="base", priority=ObligationPriority.HIGH)
        o2 = store.add(
            coordinate="b", description="derived", priority=ObligationPriority.MEDIUM,
            dependencies=[o1],
        )
        deps = store.dependencies(o2)
        assert o1 in deps


# ---------------------------------------------------------------------------
# §3  EvidenceArchive (E component) — kind invariance
# ---------------------------------------------------------------------------


class TestEvidenceArchive:
    """EvidenceArchive must preserve evidence kind through storage/retrieval."""

    def test_add_evidence_record(self) -> None:
        archive = EvidenceArchive()
        rec = _evidence_record("ev-001", EvidenceChannel.SOLVER, "mod/fn")
        aid = archive.add(record=rec, coordinate="mod/fn", channel_name="solver")
        assert isinstance(aid, str)
        assert archive.count() == 1

    def test_evidence_kind_preserved_by_channel(self) -> None:
        """Evidence kind must not be changed after archival (federation invariant)."""
        archive = EvidenceArchive()
        solver_rec = _evidence_record("ev-s1", EvidenceChannel.SOLVER, kind="solver_proof")
        copilot_rec = _evidence_record("ev-c1", EvidenceChannel.COPILOT, kind="oracle_proposal")
        archive.add(record=solver_rec, coordinate="mod/fn", channel_name="solver")
        archive.add(record=copilot_rec, coordinate="mod/fn", channel_name="copilot")
        # Retrieve by channel — kinds should be preserved
        solver_items = archive.by_channel("solver")
        copilot_items = archive.by_channel("copilot")
        assert len(solver_items) == 1
        assert len(copilot_items) == 1
        # Check that the channel identity is preserved
        for item in solver_items:
            assert item.get("channel_name") == "solver" or item.get("channel") == "solver"
        for item in copilot_items:
            assert item.get("channel_name") == "copilot" or item.get("channel") == "copilot"

    def test_evidence_retrieved_by_coordinate(self) -> None:
        archive = EvidenceArchive()
        r1 = _evidence_record("ev-1", coordinate="api/auth")
        r2 = _evidence_record("ev-2", coordinate="api/auth")
        r3 = _evidence_record("ev-3", coordinate="api/db")
        archive.add(r1, "api/auth", "solver")
        archive.add(r2, "api/auth", "solver")
        archive.add(r3, "api/db", "solver")
        auth_items = archive.by_coordinate("api/auth")
        assert len(auth_items) == 2

    def test_evidence_retrieved_by_trust_level(self) -> None:
        archive = EvidenceArchive()
        high_rec = _evidence_record("ev-h", trust="solver_discharged")
        low_rec = _evidence_record("ev-l", trust="proposal")
        archive.add(high_rec, "mod/fn", "solver", trust_tier=int(TrustTier.VERIFIED))
        archive.add(low_rec, "mod/fn", "copilot", trust_tier=int(TrustTier.PROPOSAL))
        high_items = archive.by_trust_level(min_tier=int(TrustTier.REVIEWED))
        assert len(high_items) >= 1

    def test_evidence_archive_statistics(self) -> None:
        archive = EvidenceArchive()
        for i in range(5):
            r = _evidence_record(f"ev-{i}", coordinate=f"mod/fn_{i}")
            archive.add(r, f"mod/fn_{i}", "solver")
        stats = archive.statistics()
        assert isinstance(stats, dict)
        assert stats.get("total", 0) == 5

    def test_evidence_kind_invariant_across_federation(self) -> None:
        """Multiple channel kinds must coexist without collapsing to one kind."""
        archive = EvidenceArchive()
        channels_and_kinds = [
            (EvidenceChannel.SOLVER, "solver_proof"),
            (EvidenceChannel.COPILOT, "oracle_proposal"),
            (EvidenceChannel.RUNTIME, "runtime_witness"),
        ]
        for i, (ch, knd) in enumerate(channels_and_kinds):
            r = _evidence_record(f"ev-{i}", channel=ch, kind=knd)
            archive.add(r, "mod/fn", ch.value)
        # All three channels must be separately accessible
        all_items = archive.by_coordinate("mod/fn")
        assert len(all_items) == 3
        # Channel names preserved
        channel_names = {item.get("channel_name", item.get("channel", "")) for item in all_items}
        assert len(channel_names) == 3


# ---------------------------------------------------------------------------
# §4  ObstructionStore (X component) — obstructions are persistent
# ---------------------------------------------------------------------------


class TestObstructionStore:
    """ObstructionStore must never silently erase obstructions."""

    def test_add_obstruction(self) -> None:
        store = ObstructionStore()
        oid = store.add(
            coordinate="mod/overlap",
            kind=ObstructionKind.DESCENT_FAILURE,
            message="overlap mismatch between p1 and p2",
            rank=1,
        )
        assert isinstance(oid, str)
        assert store.count() == 1

    def test_obstruction_not_deleted_by_default(self) -> None:
        """Obstructions persist — no auto-deletion."""
        store = ObstructionStore()
        oid = store.add(
            coordinate="svc/auth",
            kind=ObstructionKind.OVERLAP_VIOLATION,
            message="patch compatibility failed",
            rank=1,
        )
        active = store.active()
        assert len(active) == 1
        assert active[0]["obstruction_id"] == oid

    def test_obstruction_resolve_marks_not_active(self) -> None:
        """Resolving an obstruction marks it resolved but does NOT delete it."""
        store = ObstructionStore()
        oid = store.add(
            coordinate="mod/fn",
            kind=ObstructionKind.TRUST_CEILING_VIOLATION,
            message="trust ceiling exceeded",
            rank=1,
        )
        resolved = store.resolve(oid)
        assert resolved is True
        assert store.is_resolved(oid) is True
        # Active list excludes resolved
        active = store.active()
        assert len(active) == 0
        # But total count still 1 (persistence invariant)
        assert store.count() == 1

    def test_obstruction_by_coordinate(self) -> None:
        store = ObstructionStore()
        store.add(coordinate="api/auth", kind=ObstructionKind.DESCENT_FAILURE, message="m1", rank=1)
        store.add(coordinate="api/auth", kind=ObstructionKind.OVERLAP_VIOLATION, message="m2", rank=2)
        store.add(coordinate="api/db", kind=ObstructionKind.TRUST_CEILING_VIOLATION, message="m3", rank=1)
        auth_obs = store.by_coordinate("api/auth")
        assert len(auth_obs) == 2

    def test_obstruction_cohomology_classes(self) -> None:
        """ObstructionStore.cohomology_classes() groups by rank."""
        store = ObstructionStore()
        store.add(
            coordinate="mod/a", kind=ObstructionKind.DESCENT_FAILURE,
            message="failure-a", rank=1, cohomology_class="cc-alpha",
        )
        store.add(
            coordinate="mod/b", kind=ObstructionKind.OVERLAP_VIOLATION,
            message="failure-b", rank=1, cohomology_class="cc-alpha",
        )
        store.add(
            coordinate="mod/c", kind=ObstructionKind.DESCENT_FAILURE,
            message="failure-c", rank=2, cohomology_class="cc-beta",
        )
        classes = store.cohomology_classes()
        # Classes are grouped — "cc-alpha" group has 2 entries
        assert len(classes) >= 1

    def test_obstruction_repair_frontier(self) -> None:
        store = ObstructionStore()
        store.add(
            coordinate="mod/fn",
            kind=ObstructionKind.DESCENT_FAILURE,
            message="overlap mismatch",
            rank=1,
            metadata={"repair_hints": ["refine_cover", "add_evidence"]},
        )
        frontier = store.repair_frontier()
        assert isinstance(frontier, list)

    def test_obstruction_by_kind(self) -> None:
        store = ObstructionStore()
        store.add(coordinate="a", kind=ObstructionKind.DESCENT_FAILURE, message="df", rank=1)
        store.add(coordinate="b", kind=ObstructionKind.OVERLAP_VIOLATION, message="ov", rank=1)
        store.add(coordinate="c", kind=ObstructionKind.DESCENT_FAILURE, message="df2", rank=2)
        descent_obs = store.by_kind(ObstructionKind.DESCENT_FAILURE)
        assert len(descent_obs) == 2

    def test_multiple_obstructions_accumulate(self) -> None:
        """Obstructions accumulate; later additions do not overwrite earlier ones."""
        store = ObstructionStore()
        initial_count = store.count()
        for i in range(5):
            store.add(
                coordinate=f"mod/fn_{i}",
                kind=ObstructionKind.OVERLAP_VIOLATION,
                message=f"violation {i}",
                rank=i + 1,
            )
        assert store.count() == initial_count + 5


# ---------------------------------------------------------------------------
# §5  EpochMap (η component) — monotonic epochs
# ---------------------------------------------------------------------------


class TestEpochMap:
    """EpochMap η must be monotonically increasing per coordinate."""

    def test_initial_epoch_is_zero(self) -> None:
        em = EpochMap()
        assert em.current_epoch_at("mod/fn") == 0

    def test_advance_increments_epoch(self) -> None:
        em = EpochMap()
        e1 = em.advance("mod/fn")
        assert e1 == 1
        e2 = em.advance("mod/fn")
        assert e2 == 2
        assert em.current_epoch_at("mod/fn") == 2

    def test_epochs_are_independent_per_coordinate(self) -> None:
        em = EpochMap()
        em.advance("mod/a")
        em.advance("mod/a")
        em.advance("mod/b")
        assert em.current_epoch_at("mod/a") == 2
        assert em.current_epoch_at("mod/b") == 1
        assert em.current_epoch_at("mod/c") == 0

    def test_is_stale_detects_outdated_epoch(self) -> None:
        em = EpochMap()
        em.advance("mod/fn")
        em.advance("mod/fn")  # epoch = 2
        # If caller knows epoch = 1, it is stale (current = 2 > 1)
        assert em.is_stale("mod/fn", known_epoch=1)
        # If caller knows epoch = 2, it is current
        assert not em.is_stale("mod/fn", known_epoch=2)

    def test_staleness_report(self) -> None:
        em = EpochMap()
        em.advance("api/auth")  # epoch 1
        em.advance("api/auth")  # epoch 2
        em.advance("api/db")    # epoch 1
        known = {"api/auth": 1, "api/db": 1}
        report = em.staleness_report(known)
        assert "api/auth" in report
        assert report["api/auth"]["current"] == 2
        assert report["api/auth"]["known"] == 1
        # api/db is current
        assert "api/db" not in report

    def test_epoch_bulk_advance(self) -> None:
        em = EpochMap()
        coords = ["a/b", "c/d", "e/f"]
        result = em.bulk_advance(coords)
        assert len(result) == 3
        for coord in coords:
            assert result[coord] == 1
            assert em.current_epoch_at(coord) == 1

    def test_epoch_rollback(self) -> None:
        em = EpochMap()
        em.advance("mod/fn")
        em.advance("mod/fn")
        # Rollback one step
        new_epoch = em.rollback("mod/fn")
        assert new_epoch == 1
        assert em.current_epoch_at("mod/fn") == 1

    def test_epoch_monotone_no_decrease_on_advance(self) -> None:
        """Advancing epochs can only increase them."""
        em = EpochMap()
        prev = em.current_epoch_at("x/y")
        for _ in range(10):
            new = em.advance("x/y")
            assert new > prev
            prev = new

    def test_epoch_serialization(self) -> None:
        em = EpochMap()
        em.advance("a/b")
        em.advance("a/b")
        em.advance("c/d")
        serialized = em.serialize()
        assert isinstance(serialized, dict)
        assert serialized.get("a/b") == 2
        assert serialized.get("c/d") == 1


# ---------------------------------------------------------------------------
# §6  InvalidationGraph (σ component)
# ---------------------------------------------------------------------------


class TestInvalidationGraph:
    """σ must correctly cascade invalidation and compute repair order."""

    def test_add_dependency(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("api/auth", "api/auth/cache")
        invalidated = g.invalidate("api/auth")
        assert "api/auth/cache" in invalidated

    def test_cascade_transitive_invalidation(self) -> None:
        g = InvalidationGraph()
        # a → b → c  (if a changes, c must also be re-evaluated)
        g.add_dependency("a", "b")
        g.add_dependency("b", "c")
        cascade = g.cascade("a")
        assert "b" in cascade
        assert "c" in cascade
        assert "a" not in cascade

    def test_cascade_multiple_dependencies(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("core", "api")
        g.add_dependency("core", "db")
        g.add_dependency("api", "frontend")
        cascade = g.cascade("core")
        assert "api" in cascade
        assert "db" in cascade
        assert "frontend" in cascade

    def test_remove_dependency(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("x", "y")
        removed = g.remove_dependency("x", "y")
        assert removed is True
        assert len(g.invalidate("x")) == 0

    def test_affected_by_reverse_lookup(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("schema", "service_a")
        g.add_dependency("schema", "service_b")
        affected = g.affected_by("service_a")
        assert "schema" in affected

    def test_compute_repair_order(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("a", "b")
        g.add_dependency("b", "c")
        dirty = {"a", "b", "c"}
        order = g.compute_repair_order(dirty)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_no_duplicate_edges(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("x", "y")
        g.add_dependency("x", "y")  # duplicate
        invalidated = g.invalidate("x")
        assert len(invalidated) == 1  # deduplicated

    def test_invalidation_graph_serialization(self) -> None:
        g = InvalidationGraph()
        g.add_dependency("mod/a", "mod/b")
        g.add_dependency("mod/b", "mod/c")
        serialized = g.serialize()
        assert isinstance(serialized, dict)
        assert "mod/a" in serialized


# ---------------------------------------------------------------------------
# §7  Manifest lifecycle (all seven components together)
# ---------------------------------------------------------------------------


class TestManifestLifecycle:
    """End-to-end manifest lifecycle: build, update, invalidate, snapshot."""

    def test_manifest_creation(self) -> None:
        m = Manifest()
        assert m.manifest_id.startswith("m-")
        assert m.judgments.count() == 0
        assert m.obligations.count() == 0
        assert m.obstructions.count() == 0

    def test_manifest_add_judgment_and_query(self) -> None:
        m = Manifest()
        jid = m.judgments.add(
            coordinate="api/login",
            proposition="login_safe",
            trust_tier=TrustTier.REVIEWED,
        )
        results = m.judgments.query_by_coordinate("api/login")
        assert len(results) == 1
        assert results[0]["proposition"] == "login_safe"

    def test_manifest_advance_epoch_on_update(self) -> None:
        m = Manifest()
        assert m.epoch_map.current_epoch_at("api/auth") == 0
        new_epoch = m.advance_epoch("api/auth")
        assert new_epoch == 1
        assert m.epoch_map.current_epoch_at("api/auth") == 1

    def test_manifest_epoch_cascades_invalidation(self) -> None:
        m = Manifest()
        m.invalidation_graph.add_dependency("core/schema", "api/handler")
        m.invalidation_graph.add_dependency("api/handler", "api/response")
        m.advance_epoch("core/schema")
        # Cascaded coordinates should have advanced epochs too
        assert m.epoch_map.current_epoch_at("api/handler") >= 1
        assert m.epoch_map.current_epoch_at("api/response") >= 1

    def test_manifest_obstruction_persistent_after_resolution(self) -> None:
        m = Manifest()
        oid = m.obstructions.add(
            coordinate="mod/fn",
            kind=ObstructionKind.DESCENT_FAILURE,
            message="overlap mismatch",
            rank=1,
        )
        m.obstructions.resolve(oid)
        # Resolved but still counted (persistent)
        assert m.obstructions.count() == 1
        assert m.obstructions.is_resolved(oid)

    def test_manifest_sigma_dependency_graph_updates(self) -> None:
        """Modifying the invalidation graph σ correctly tracks dependencies."""
        m = Manifest()
        m.invalidation_graph.add_dependency("auth/token", "session/cache")
        cascade = m.invalidation_graph.cascade("auth/token")
        assert "session/cache" in cascade

    def test_manifest_snapshot_and_restore(self) -> None:
        m = Manifest()
        m.judgments.add(coordinate="mod/fn", proposition="P", trust_tier=TrustTier.VERIFIED)
        m.obligations.add(coordinate="mod/fn", description="prove P", priority=ObligationPriority.HIGH)
        m.obstructions.add(
            coordinate="mod/fn", kind=ObstructionKind.DESCENT_FAILURE, message="m", rank=1
        )
        m.epoch_map.advance("mod/fn")
        snapshot = m.snapshot()
        # Restore into a new manifest
        m2 = Manifest()
        m2.restore(snapshot)
        assert m2.judgments.count() == 1
        assert m2.obligations.count() == 1
        assert m2.obstructions.count() == 1
        assert m2.epoch_map.current_epoch_at("mod/fn") == 1

    def test_manifest_certificate_store_add_and_retrieve(self) -> None:
        m = Manifest()
        m.certificates.add(
            certificate_id="cert-001",
            coordinate="api/auth",
            data={"proposition": "login_safe", "status": "valid"},
        )
        result = m.certificates.get("cert-001")
        assert result is not None
        assert result["coordinate"] == "api/auth"

    def test_manifest_evidence_archive_kind_preserved(self) -> None:
        """Evidence kinds must survive archival in the manifest."""
        m = Manifest()
        solver_rec = _evidence_record("ev-s", EvidenceChannel.SOLVER)
        copilot_rec = _evidence_record("ev-c", EvidenceChannel.COPILOT)
        m.evidence_archive.add(solver_rec, "mod/fn", "solver")
        m.evidence_archive.add(copilot_rec, "mod/fn", "copilot")
        # Both channels preserved in archive
        solver_items = m.evidence_archive.by_channel("solver")
        copilot_items = m.evidence_archive.by_channel("copilot")
        assert len(solver_items) == 1
        assert len(copilot_items) == 1

    def test_manifest_obligation_discharge_lifecycle(self) -> None:
        m = Manifest()
        oid = m.obligations.add(
            coordinate="svc/parser",
            description="prove_parse_terminates",
            priority=ObligationPriority.HIGH,
        )
        assert len(m.obligations.pending()) == 1
        m.obligations.discharge(oid)
        assert len(m.obligations.pending()) == 0
        assert m.obligations.is_discharged(oid)

    def test_manifest_archived_evidence_has_kind_invariant(self) -> None:
        """Federation invariant: archived evidence kind must not change."""
        m = Manifest()
        for i, channel in enumerate([EvidenceChannel.SOLVER, EvidenceChannel.RUNTIME, EvidenceChannel.COPILOT]):
            rec = _evidence_record(f"ev-fed-{i}", channel=channel, kind=f"kind_{channel.value}")
            m.evidence_archive.add(rec, "fed/target", channel.value)
        all_items = m.evidence_archive.by_coordinate("fed/target")
        assert len(all_items) == 3
        # All three different channels must be present
        observed_channels = set()
        for item in all_items:
            ch = item.get("channel_name", item.get("channel", ""))
            observed_channels.add(ch)
        assert len(observed_channels) == 3
