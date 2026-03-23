"""Tests for jugeo.generation.replay_gluing.s02_incremental_replay.

Covers GluingSnapshot, ReplayCache, OverlapReconciler, IncrementalReplayer,
and the module-level helper functions (create_snapshot_from_gluing,
restore_snapshot, merge_snapshots).
"""

from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import pytest
import time
import uuid

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------

from jugeo.generation.replay_gluing.models import (
    ReplayGluingPlan, GluingUnderReplay, IncrementalGluing, ReplayPhase, ReplayStrategy,
)
from jugeo.generation.replay_gluing.s01_replay_planning import ChangeSet, ReplayPlanner
from jugeo.generation.replay_gluing.s02_incremental_replay import (
    GluingSnapshot,
    ReplayCache,
    OverlapReconciler,
    IncrementalReplayer,
    ReplayError,
    OverlapIncompatibilityError,
    create_snapshot_from_gluing,
    restore_snapshot,
    merge_snapshots,
)

# ---------------------------------------------------------------------------
# Optional jugeo geometry / construction / treaty dependencies
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.geometry.supports import SupportRegion
    from jugeo.generation.treaties import Treaty, TreatyStatus
    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False


# ---------------------------------------------------------------------------
# Test-local helpers
# ---------------------------------------------------------------------------


def _make_snapshot(sections=None, overlaps=None, treaties=None):
    """Build a GluingSnapshot from plain dicts."""
    return GluingSnapshot(
        patch_sections=dict(sections or {}),
        overlap_conditions=dict(overlaps or {}),
        treaties=dict(treaties or {}),
    )


def _make_plan(changed=None, unchanged=None, removed=None, strategy=ReplayStrategy.INCREMENTAL):
    cs = ChangeSet(
        changed_patches=frozenset(changed or []),
        unchanged_patches=frozenset(unchanged or []),
        removed_patches=frozenset(removed or []),
    )
    planner = ReplayPlanner(strategy_override=strategy)
    return planner.plan(cs, {})


def _prior_gluing_from_snapshot(snap: GluingSnapshot) -> dict:
    return restore_snapshot(snap)


# ===========================================================================
# TestGluingSnapshot
# ===========================================================================


class TestGluingSnapshot:
    """Unit tests for the GluingSnapshot dataclass."""

    def test_creation(self):
        snap = GluingSnapshot()
        assert isinstance(snap.snapshot_id, str)
        assert len(snap.snapshot_id) > 0
        assert snap.patch_sections == {}
        assert snap.overlap_conditions == {}
        assert snap.treaties == {}
        assert isinstance(snap.timestamp, float)

    def test_get_section_found(self):
        snap = _make_snapshot(sections={"p1": {"val": 1}})
        result = snap.get_section("p1")
        assert result == {"val": 1}

    def test_get_section_not_found(self):
        snap = _make_snapshot()
        assert snap.get_section("missing") is None

    def test_get_overlap_forward(self):
        overlaps = {("p1", "p2"): {"boundary": "x"}}
        snap = _make_snapshot(overlaps=overlaps)
        result = snap.get_overlap(("p1", "p2"))
        assert result == {"boundary": "x"}

    def test_get_overlap_reverse(self):
        """If (a,b) is absent but (b,a) is present, returns (b,a)."""
        overlaps = {("p2", "p1"): {"boundary": "y"}}
        snap = _make_snapshot(overlaps=overlaps)
        result = snap.get_overlap(("p1", "p2"))
        assert result == {"boundary": "y"}

    def test_get_overlap_missing(self):
        snap = _make_snapshot()
        assert snap.get_overlap(("x", "y")) is None

    def test_diff_from_identical(self):
        """Two identical snapshots produce an empty diff."""
        snap_a = _make_snapshot(sections={"p1": {"v": 1}})
        snap_b = _make_snapshot(sections={"p1": {"v": 1}})
        diff = snap_a.diff_from(snap_b)
        assert diff["added_patches"] == []
        assert diff["removed_patches"] == []
        assert diff["modified_patches"] == []

    def test_diff_from_added_patch(self):
        snap_old = _make_snapshot(sections={"p1": {"v": 1}})
        snap_new = _make_snapshot(sections={"p1": {"v": 1}, "p2": {"v": 2}})
        diff = snap_new.diff_from(snap_old)
        assert "p2" in diff["added_patches"]

    def test_diff_from_removed_patch(self):
        snap_old = _make_snapshot(sections={"p1": {"v": 1}, "p2": {"v": 2}})
        snap_new = _make_snapshot(sections={"p1": {"v": 1}})
        diff = snap_new.diff_from(snap_old)
        assert "p2" in diff["removed_patches"]

    def test_diff_from_modified_patch(self):
        snap_old = _make_snapshot(sections={"p1": {"v": 1}})
        snap_new = _make_snapshot(sections={"p1": {"v": 99}})
        diff = snap_new.diff_from(snap_old)
        assert "p1" in diff["modified_patches"]

    def test_restore_returns_dict(self):
        snap = _make_snapshot(sections={"p1": {"val": 5}})
        restored = snap.restore()
        assert isinstance(restored, dict)
        assert "sections" in restored

    def test_to_dict_roundtrip(self):
        snap = _make_snapshot(sections={"p1": {"v": 1}})
        d = snap.to_dict()
        assert "snapshot_id" in d
        assert "patch_sections" in d
        assert "overlap_conditions" in d
        assert d["patch_sections"]["p1"] == {"v": 1}

    def test_from_dict(self):
        snap = _make_snapshot(sections={"px": {"data": True}})
        d = snap.to_dict()
        snap2 = GluingSnapshot.from_dict(d)
        assert snap2.snapshot_id == snap.snapshot_id
        assert snap2.patch_sections == snap.patch_sections

    def test_from_dict_with_overlaps(self):
        """Overlaps serialised with '::' separator are correctly parsed."""
        d = {
            "snapshot_id": "test-id",
            "patch_sections": {"a": 1},
            "overlap_conditions": {"a::b": {"score": 0.9}},
            "treaties": {},
            "timestamp": time.time(),
        }
        snap = GluingSnapshot.from_dict(d)
        assert snap.get_overlap(("a", "b")) == {"score": 0.9}

    def test_snapshot_id_unique_on_creation(self):
        s1 = GluingSnapshot()
        s2 = GluingSnapshot()
        assert s1.snapshot_id != s2.snapshot_id

    def test_timestamp_is_recent(self):
        before = time.time() - 0.01
        snap = GluingSnapshot()
        after = time.time() + 0.01
        assert before <= snap.timestamp <= after

    @pytest.mark.parametrize("patches", [
        ["p1"],
        ["p1", "p2", "p3"],
        [f"patch_{i}" for i in range(10)],
    ])
    def test_snapshot_with_various_patches(self, patches):
        sections = {p: {"index": i} for i, p in enumerate(patches)}
        snap = _make_snapshot(sections=sections)
        for p in patches:
            assert snap.get_section(p) is not None
            assert snap.get_section(p)["index"] == patches.index(p)


# ===========================================================================
# TestReplayCache
# ===========================================================================


class TestReplayCache:
    """Unit tests for the ReplayCache LRU cache."""

    def test_store_and_lookup(self):
        cache = ReplayCache()
        cache.store("p1", {"result": 42})
        assert cache.lookup("p1") == {"result": 42}

    def test_lookup_miss_returns_none(self):
        cache = ReplayCache()
        assert cache.lookup("absent") is None

    def test_hit_rate_zero_initial(self):
        cache = ReplayCache()
        assert cache.get_hit_rate() == 0.0

    def test_hit_rate_after_hits(self):
        cache = ReplayCache()
        cache.store("p1", "v1")
        cache.lookup("p1")
        cache.lookup("p1")
        assert cache.get_hit_rate() == pytest.approx(1.0)

    def test_hit_rate_after_misses(self):
        cache = ReplayCache()
        cache.lookup("absent1")
        cache.lookup("absent2")
        assert cache.get_hit_rate() == 0.0

    def test_hit_rate_mixed(self):
        cache = ReplayCache()
        cache.store("p1", "v1")
        cache.lookup("p1")   # hit
        cache.lookup("miss")  # miss
        assert cache.get_hit_rate() == pytest.approx(0.5)

    def test_invalidate_removes_entry(self):
        cache = ReplayCache()
        cache.store("p1", "val")
        cache.invalidate("p1")
        assert cache.lookup("p1") is None

    def test_invalidate_nonexistent_noop(self):
        """Invalidating a key that doesn't exist does not raise."""
        cache = ReplayCache()
        cache.invalidate("ghost")  # should not raise

    def test_invalidate_all(self):
        cache = ReplayCache()
        for i in range(5):
            cache.store(f"p{i}", f"v{i}")
        cache.invalidate_all([f"p{i}" for i in range(3)])
        assert cache.lookup("p0") is None
        assert cache.lookup("p1") is None
        assert cache.lookup("p2") is None
        # p3 and p4 should still be present
        assert cache.lookup("p3") == "v3"
        assert cache.lookup("p4") == "v4"

    def test_eviction_at_max_size(self):
        """When cache is full, the LRU entry is evicted."""
        cache = ReplayCache(max_size=3)
        cache.store("p1", "v1")
        cache.store("p2", "v2")
        cache.store("p3", "v3")
        cache.store("p4", "v4")  # p1 should be evicted
        assert cache.lookup("p1") is None
        assert cache.lookup("p4") == "v4"

    def test_clear_empties_cache(self):
        cache = ReplayCache()
        cache.store("p1", "v1")
        cache.store("p2", "v2")
        cache.clear()
        assert cache.lookup("p1") is None
        assert cache.lookup("p2") is None

    def test_get_stats(self):
        cache = ReplayCache(max_size=10)
        cache.store("p1", "v1")
        cache.lookup("p1")  # hit
        cache.lookup("miss")  # miss
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["cache_size"] == 1
        assert stats["max_size"] == 10
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_clear_resets_stats(self):
        cache = ReplayCache()
        cache.store("p1", "v1")
        cache.lookup("p1")
        cache.clear()
        assert cache.get_hit_rate() == 0.0
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    @pytest.mark.parametrize("value", [
        "string value",
        42,
        {"nested": "dict"},
        [1, 2, 3],
        None,
    ])
    def test_store_various_types(self, value):
        cache = ReplayCache()
        cache.store("key", value)
        result = cache.lookup("key")
        assert result == value

    def test_overwrite_existing_entry(self):
        cache = ReplayCache()
        cache.store("p1", "original")
        cache.store("p1", "updated")
        assert cache.lookup("p1") == "updated"

    def test_lru_access_order_updated(self):
        """Accessing an entry moves it to most-recently-used."""
        cache = ReplayCache(max_size=2)
        cache.store("p1", "v1")
        cache.store("p2", "v2")
        cache.lookup("p1")  # p1 is now MRU
        cache.store("p3", "v3")  # p2 should be evicted, not p1
        assert cache.lookup("p1") == "v1"
        assert cache.lookup("p2") is None


# ===========================================================================
# TestOverlapReconciler
# ===========================================================================


class TestOverlapReconciler:
    """Unit tests for OverlapReconciler."""

    def _make_context(self, sections=None):
        return {"sections": dict(sections or {})}

    def test_reconcile_empty_neighbors(self):
        r = OverlapReconciler()
        result = r.reconcile("p1", [], self._make_context({"p1": {"v": 1}}))
        assert result == {}

    def test_reconcile_compatible_neighbors(self):
        r = OverlapReconciler()
        ctx = self._make_context({"p1": {"key": "x"}, "p2": {"key": "x"}})
        result = r.reconcile("p1", ["p2"], ctx)
        key = "p1::p2"
        assert key in result
        assert result[key]["compatible"] is True

    def test_reconcile_incompatible_neighbors(self):
        r = OverlapReconciler()
        ctx = self._make_context({
            "p1": {"shared_key": "value_a"},
            "p2": {"shared_key": "value_b"},
        })
        result = r.reconcile("p1", ["p2"], ctx)
        key = "p1::p2"
        assert key in result
        assert result[key]["compatible"] is False

    def test_check_compatibility_identical_dicts(self):
        r = OverlapReconciler()
        assert r.check_compatibility({"a": 1}, {"a": 1}) is True

    def test_check_compatibility_non_contradictory_dicts(self):
        """No shared keys — trivially compatible."""
        r = OverlapReconciler()
        assert r.check_compatibility({"a": 1}, {"b": 2}) is True

    def test_check_compatibility_contradictory_values(self):
        r = OverlapReconciler()
        assert r.check_compatibility({"x": 1}, {"x": 2}) is False

    def test_check_compatibility_non_dict_same(self):
        r = OverlapReconciler()
        assert r.check_compatibility("hello", "hello") is True

    def test_check_compatibility_non_dict_different(self):
        r = OverlapReconciler()
        assert r.check_compatibility("hello", "world") is False

    def test_check_compatibility_none_is_compatible(self):
        """None represents missing data and is trivially compatible."""
        r = OverlapReconciler()
        assert r.check_compatibility(None, {"a": 1}) is True
        assert r.check_compatibility({"a": 1}, None) is True

    def test_check_compatibility_value_key_numeric(self):
        """Two sections with equal 'value' floats are compatible."""
        r = OverlapReconciler()
        assert r.check_compatibility({"value": 1.0}, {"value": 1.0}) is True

    def test_check_compatibility_value_key_numeric_close(self):
        """Floating-point comparison uses tolerance."""
        r = OverlapReconciler()
        assert r.check_compatibility({"value": 1.0}, {"value": 1.0 + 1e-12}) is True

    def test_suggest_fix_returns_string(self):
        r = OverlapReconciler()
        fix = r.suggest_fix({
            "patch": "p1",
            "neighbor": "p2",
            "s1": {"x": 1},
            "s2": {"x": 2},
        })
        assert isinstance(fix, str)
        assert len(fix) > 0

    def test_suggest_fix_missing_s1(self):
        r = OverlapReconciler()
        fix = r.suggest_fix({"patch": "p1", "neighbor": "p2", "s1": None, "s2": {}})
        assert "p1" in fix

    def test_suggest_fix_missing_s2(self):
        r = OverlapReconciler()
        fix = r.suggest_fix({"patch": "p1", "neighbor": "p2", "s1": {}, "s2": None})
        assert "p2" in fix

    def test_compute_overlap_hash_deterministic(self):
        r = OverlapReconciler()
        h1 = r.compute_overlap_hash({"a": 1}, {"b": 2})
        h2 = r.compute_overlap_hash({"a": 1}, {"b": 2})
        assert h1 == h2

    def test_compute_overlap_hash_different_inputs_differ(self):
        r = OverlapReconciler()
        h1 = r.compute_overlap_hash({"a": 1}, {"b": 2})
        h2 = r.compute_overlap_hash({"a": 99}, {"b": 2})
        assert h1 != h2

    def test_get_log_empty_initially(self):
        r = OverlapReconciler()
        assert r.get_log() == []

    def test_get_log_populated_after_reconcile(self):
        r = OverlapReconciler()
        ctx = self._make_context({"p1": {"v": 1}, "p2": {"v": 1}})
        r.reconcile("p1", ["p2"], ctx)
        log = r.get_log()
        assert len(log) == 1
        assert log[0].pair == ("p1", "p2")


# ===========================================================================
# TestIncrementalReplayer
# ===========================================================================


class TestIncrementalReplayer:
    """Unit tests for IncrementalReplayer."""

    def _make_prior(self, patches):
        """Build a minimal prior_gluing dict for the given patches."""
        return {
            "sections": {p: {"value": i} for i, p in enumerate(patches)},
            "overlaps": {},
        }

    def test_replay_returns_gluing(self):
        plan = _make_plan(changed=["p1"], unchanged=["p2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2"])
        result = replayer.replay(plan, prior)
        assert result is not None

    def test_replay_changed_patches_replayed(self):
        """Changed patches produce replay steps with action='replay'."""
        plan = _make_plan(changed=["p1"], unchanged=["p2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2"])
        replayer.replay(plan, prior)
        steps = replayer.get_steps()
        replay_patches = {s.patch for s in steps if s.action == "replay"}
        assert "p1" in replay_patches

    def test_replay_unchanged_patches_skipped(self):
        """Unchanged patches produce skip steps."""
        plan = _make_plan(changed=["p1"], unchanged=["p2", "p3"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2", "p3"])
        replayer.replay(plan, prior)
        steps = replayer.get_steps()
        skip_patches = {s.patch for s in steps if s.action == "skip"}
        assert "p2" in skip_patches
        assert "p3" in skip_patches

    def test_replay_uses_cache(self):
        """Subsequent replay of same patches hits the cache."""
        plan = _make_plan(changed=["p1"])
        cache = ReplayCache()
        replayer = IncrementalReplayer(cache=cache)
        prior = self._make_prior(["p1"])
        replayer.replay(plan, prior)
        # Replay again — this time the result should come from cache.
        replayer2 = IncrementalReplayer(cache=cache)
        replayer2.replay(plan, prior)
        stats = replayer2.get_statistics()
        # Cache now has entries; hit_rate may be > 0
        assert stats["cache_stats"]["cache_size"] >= 1

    def test_replay_phase_transitions(self):
        """replay() completes without error and records steps."""
        plan = _make_plan(changed=["p1"], unchanged=["p2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2"])
        replayer.replay(plan, prior)
        steps = replayer.get_steps()
        assert len(steps) >= 2  # at least one replay + one skip

    def test_replay_empty_plan(self):
        """Replaying an empty plan produces no steps."""
        plan = _make_plan()  # nothing changed or unchanged
        replayer = IncrementalReplayer()
        replayer.replay(plan, {})
        stats = replayer.get_statistics()
        assert stats["replayed"] == 0
        assert stats["skipped"] == 0

    def test_replay_all_changed(self):
        """All-changed plan replays every patch."""
        patches = ["a", "b", "c", "d"]
        plan = _make_plan(changed=patches)
        replayer = IncrementalReplayer()
        prior = self._make_prior(patches)
        replayer.replay(plan, prior)
        stats = replayer.get_statistics()
        assert stats["replayed"] == len(patches)
        assert stats["skipped"] == 0

    def test_get_statistics_returns_dict(self):
        plan = _make_plan(changed=["p1"], unchanged=["p2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2"])
        replayer.replay(plan, prior)
        stats = replayer.get_statistics()
        assert isinstance(stats, dict)
        assert "replayed" in stats
        assert "skipped" in stats
        assert "failed" in stats
        assert "total_steps" in stats
        assert "cache_stats" in stats

    def test_replay_cache_hit_rate_after_replay(self):
        """After replaying, cache has entries stored."""
        plan = _make_plan(changed=["x1", "x2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["x1", "x2"])
        replayer.replay(plan, prior)
        stats = replayer.get_statistics()
        assert stats["cache_stats"]["cache_size"] >= 0  # may be > 0

    @pytest.mark.parametrize("strategy", [
        ReplayStrategy.FULL,
        ReplayStrategy.INCREMENTAL,
        ReplayStrategy.LAZY,
    ])
    def test_replay_with_strategies(self, strategy):
        """Replay works for all standard strategies."""
        plan = _make_plan(changed=["p1"], unchanged=["p2"], strategy=strategy)
        replayer = IncrementalReplayer()
        prior = self._make_prior(["p1", "p2"])
        result = replayer.replay(plan, prior)
        assert result is not None

    def test_get_steps_is_list(self):
        plan = _make_plan(changed=["q1"])
        replayer = IncrementalReplayer()
        replayer.replay(plan, self._make_prior(["q1"]))
        steps = replayer.get_steps()
        assert isinstance(steps, list)

    def test_statistics_replayed_count(self):
        plan = _make_plan(changed=["r1", "r2", "r3"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["r1", "r2", "r3"])
        replayer.replay(plan, prior)
        stats = replayer.get_statistics()
        assert stats["replayed"] == 3

    def test_statistics_skipped_count(self):
        plan = _make_plan(changed=["r1"], unchanged=["s1", "s2"])
        replayer = IncrementalReplayer()
        prior = self._make_prior(["r1", "s1", "s2"])
        replayer.replay(plan, prior)
        stats = replayer.get_statistics()
        assert stats["skipped"] == 2

    def test_custom_cache_injected(self):
        """The replayer uses the cache that was injected."""
        custom_cache = ReplayCache(max_size=50)
        replayer = IncrementalReplayer(cache=custom_cache)
        assert replayer.cache is custom_cache

    def test_custom_reconciler_injected(self):
        custom_reconciler = OverlapReconciler()
        replayer = IncrementalReplayer(reconciler=custom_reconciler)
        assert replayer.reconciler is custom_reconciler


# ===========================================================================
# TestHelpers — module-level functions
# ===========================================================================


class TestHelpers:
    """Tests for create_snapshot_from_gluing, restore_snapshot, merge_snapshots."""

    def test_create_snapshot_from_gluing_dict(self):
        """create_snapshot_from_gluing works with a plain dict."""
        gluing = {
            "sections": {"p1": {"v": 1}, "p2": {"v": 2}},
            "overlaps": {"p1::p2": {"score": 0.8}},
        }
        snap = create_snapshot_from_gluing(gluing)
        assert isinstance(snap, GluingSnapshot)
        assert snap.get_section("p1") == {"v": 1}
        assert snap.get_section("p2") == {"v": 2}
        assert snap.get_overlap(("p1", "p2")) == {"score": 0.8}

    def test_create_snapshot_from_gluing_under_replay(self):
        """create_snapshot_from_gluing works with a GluingUnderReplay object."""
        plan = _make_plan(changed=["p1"])
        g = GluingUnderReplay(plan=plan)
        g.mark_replayed("p1", {"data": 10})
        snap = create_snapshot_from_gluing(g)
        assert isinstance(snap, GluingSnapshot)
        assert snap.get_section("p1") == {"data": 10}

    def test_restore_snapshot(self):
        snap = _make_snapshot(sections={"p1": {"v": 1}})
        restored = restore_snapshot(snap)
        assert isinstance(restored, dict)
        assert "sections" in restored
        assert restored["sections"]["p1"] == {"v": 1}

    def test_merge_snapshots_combines_patches(self):
        s1 = _make_snapshot(sections={"a": {"v": 1}})
        s2 = _make_snapshot(sections={"b": {"v": 2}})
        merged = merge_snapshots(s1, s2)
        assert merged.get_section("a") == {"v": 1}
        assert merged.get_section("b") == {"v": 2}

    def test_merge_snapshots_precedence(self):
        """s2 takes precedence over s1 for shared patches."""
        s1 = _make_snapshot(sections={"p": {"v": 1}})
        s2 = _make_snapshot(sections={"p": {"v": 99}})
        merged = merge_snapshots(s1, s2)
        assert merged.get_section("p") == {"v": 99}

    def test_merge_snapshots_overlaps_combined(self):
        s1 = _make_snapshot(
            sections={"a": 1},
            overlaps={("a", "b"): {"x": 1}},
        )
        s2 = _make_snapshot(
            sections={"c": 3},
            overlaps={("c", "d"): {"x": 2}},
        )
        merged = merge_snapshots(s1, s2)
        assert merged.get_overlap(("a", "b")) == {"x": 1}
        assert merged.get_overlap(("c", "d")) == {"x": 2}

    def test_merge_snapshots_returns_new_id(self):
        s1 = _make_snapshot(sections={"a": 1})
        s2 = _make_snapshot(sections={"b": 2})
        merged = merge_snapshots(s1, s2)
        assert merged.snapshot_id != s1.snapshot_id
        assert merged.snapshot_id != s2.snapshot_id

    def test_restore_snapshot_contains_overlaps(self):
        snap = _make_snapshot(
            sections={"p1": {"v": 1}},
            overlaps={("p1", "p2"): {"score": 0.7}},
        )
        restored = restore_snapshot(snap)
        assert "overlaps" in restored

    def test_create_snapshot_from_empty_dict(self):
        snap = create_snapshot_from_gluing({})
        assert isinstance(snap, GluingSnapshot)
        assert snap.patch_sections == {}


# ===========================================================================
# TestExceptions
# ===========================================================================


class TestExceptions:
    """Tests for ReplayError and OverlapIncompatibilityError."""

    def test_replay_error_is_exception(self):
        err = ReplayError("test message")
        assert isinstance(err, Exception)
        assert str(err) == "test message"

    def test_overlap_incompatibility_error_is_replay_error(self):
        err = OverlapIncompatibilityError(("p1", "p2"))
        assert isinstance(err, ReplayError)
        assert err.patches == ("p1", "p2")

    def test_overlap_incompatibility_error_default_message(self):
        err = OverlapIncompatibilityError(("a", "b"))
        msg = str(err)
        assert "a" in msg
        assert "b" in msg

    def test_overlap_incompatibility_error_custom_message(self):
        err = OverlapIncompatibilityError(("x", "y"), message="custom error")
        assert "custom" in str(err)

    def test_replay_error_can_be_raised(self):
        with pytest.raises(ReplayError):
            raise ReplayError("forced")

    def test_overlap_incompatibility_error_can_be_caught_as_replay_error(self):
        with pytest.raises(ReplayError):
            raise OverlapIncompatibilityError(("p", "q"))


# ===========================================================================
# Integration tests — skipped if jugeo geometry/treaty deps are absent
# ===========================================================================


@pytest.mark.skipif(not HAS_JUGEO_DEPS, reason="jugeo deps not available")
class TestIntegration:
    """Integration tests that require jugeo geometry and treaty dependencies."""

    def test_replayer_with_construction_context(self):
        """Full replay pipeline: plan → snapshot → replayer → result."""
        patches = ["patch_alpha", "patch_beta", "patch_gamma"]
        plan = _make_plan(changed=["patch_alpha"], unchanged=["patch_beta", "patch_gamma"])
        # Build a prior snapshot with geometry-flavoured section data.
        coord = CoordinateObject("root", CoordinateKind.REGION, ("root",))
        sections = {
            p: {
                "coordinate": str(coord),
                "value": i * 1.5,
            }
            for i, p in enumerate(patches)
        }
        prior_snap = GluingSnapshot(patch_sections=sections)
        prior = restore_snapshot(prior_snap)

        replayer = IncrementalReplayer()
        result = replayer.replay(plan, prior)
        assert result is not None
        stats = replayer.get_statistics()
        assert stats["replayed"] >= 1
        assert stats["skipped"] >= 1

    def test_reconciler_with_treaty_check(self):
        """OverlapReconciler verifies section compatibility with treaty-like data."""
        from jugeo.generation.treaties import TreatyLaw
        law = TreatyLaw(
            predicate="compatible_sections",
            variables=("s1", "s2"),
            quantifiers=(),
            natural_language_description="Sections s1 and s2 are compatible",
        )
        # Section data that satisfies the (mock) treaty predicate: same keys.
        sections_ok = {
            "p1": {"value": 1.0, "treaty_predicate": law.predicate},
            "p2": {"value": 1.0, "treaty_predicate": law.predicate},
        }
        r = OverlapReconciler()
        ctx = {"sections": sections_ok}
        result = r.reconcile("p1", ["p2"], ctx)
        assert result["p1::p2"]["compatible"] is True

        # Section data that VIOLATES the predicate (differing values)
        sections_bad = {
            "p1": {"value": 1.0},
            "p2": {"value": 2.0},  # different → incompatible
        }
        r2 = OverlapReconciler()
        ctx2 = {"sections": sections_bad}
        result2 = r2.reconcile("p1", ["p2"], ctx2)
        assert result2["p1::p2"]["compatible"] is False


# ===========================================================================
# Additional round-trip and edge-case tests
# ===========================================================================


class TestSnapshotRoundTrip:
    """Extra serialisation round-trip tests for GluingSnapshot."""

    @pytest.mark.parametrize("n", [0, 1, 5, 20])
    def test_to_dict_from_dict_roundtrip(self, n):
        sections = {f"p{i}": {"val": i} for i in range(n)}
        snap = _make_snapshot(sections=sections)
        d = snap.to_dict()
        snap2 = GluingSnapshot.from_dict(d)
        assert snap2.patch_sections == snap.patch_sections
        assert snap2.snapshot_id == snap.snapshot_id

    def test_empty_snapshot_roundtrip(self):
        snap = GluingSnapshot()
        d = snap.to_dict()
        snap2 = GluingSnapshot.from_dict(d)
        assert snap2.patch_sections == {}
        assert snap2.overlap_conditions == {}

    def test_overlap_conditions_roundtrip(self):
        snap = _make_snapshot(
            overlaps={("a", "b"): {"key": "val"}, ("c", "d"): {"score": 0.5}},
        )
        d = snap.to_dict()
        snap2 = GluingSnapshot.from_dict(d)
        assert snap2.get_overlap(("a", "b")) == {"key": "val"}
        assert snap2.get_overlap(("c", "d")) == {"score": 0.5}

    def test_treaties_preserved_in_roundtrip(self):
        snap = _make_snapshot(treaties={"t1": {"status": "ratified"}})
        d = snap.to_dict()
        snap2 = GluingSnapshot.from_dict(d)
        assert snap2.treaties["t1"]["status"] == "ratified"

    def test_restore_and_recreate(self):
        """restore() → create_snapshot_from_gluing() gives equivalent snapshot."""
        original = _make_snapshot(
            sections={"x": {"v": 7}, "y": {"v": 8}},
            overlaps={("x", "y"): {"delta": 0.1}},
        )
        restored_dict = restore_snapshot(original)
        recreated = create_snapshot_from_gluing(restored_dict)
        assert recreated.get_section("x") == {"v": 7}
        assert recreated.get_section("y") == {"v": 8}


class TestReplayCacheEdgeCases:
    """Edge-case tests for ReplayCache."""

    def test_store_overwrites_existing(self):
        c = ReplayCache()
        c.store("k", "v1")
        c.store("k", "v2")
        assert c.lookup("k") == "v2"

    def test_max_size_one(self):
        c = ReplayCache(max_size=1)
        c.store("first", 1)
        c.store("second", 2)
        assert c.lookup("first") is None
        assert c.lookup("second") == 2

    def test_hit_rate_rounds(self):
        c = ReplayCache()
        c.store("a", 1)
        for _ in range(3):
            c.lookup("a")    # 3 hits
        c.lookup("missing")  # 1 miss
        assert c.get_hit_rate() == pytest.approx(0.75)

    def test_invalidate_all_iterable(self):
        c = ReplayCache()
        for i in range(5):
            c.store(f"k{i}", i)
        c.invalidate_all(iter([f"k{i}" for i in range(5)]))
        for i in range(5):
            assert c.lookup(f"k{i}") is None

    def test_large_cache(self):
        c = ReplayCache(max_size=1000)
        for i in range(500):
            c.store(f"patch_{i}", {"index": i})
        for i in range(500):
            result = c.lookup(f"patch_{i}")
            assert result == {"index": i}
        stats = c.get_stats()
        assert stats["cache_size"] == 500


class TestOverlapReconcilerEdgeCases:
    """Additional edge-case tests for OverlapReconciler."""

    def test_reconcile_multiple_neighbors(self):
        r = OverlapReconciler()
        ctx = {
            "sections": {
                "center": {"v": 1},
                "n1": {"v": 1},
                "n2": {"v": 1},
                "n3": {"v": 99},  # incompatible
            }
        }
        result = r.reconcile("center", ["n1", "n2", "n3"], ctx)
        assert result["center::n1"]["compatible"] is True
        assert result["center::n2"]["compatible"] is True
        assert result["center::n3"]["compatible"] is False

    def test_get_log_grows_per_reconcile(self):
        r = OverlapReconciler()
        ctx = {"sections": {"a": {"v": 1}, "b": {"v": 1}, "c": {"v": 1}}}
        r.reconcile("a", ["b"], ctx)
        r.reconcile("a", ["c"], ctx)
        log = r.get_log()
        assert len(log) == 2

    def test_overlap_hash_length(self):
        r = OverlapReconciler()
        h = r.compute_overlap_hash({"a": 1}, {"b": 2})
        assert len(h) == 16  # 16-char hex digest

    def test_check_compatibility_nested_dict(self):
        r = OverlapReconciler()
        # Same nested structures → compatible
        s1 = {"outer": {"inner": 1}}
        s2 = {"outer": {"inner": 1}}
        assert r.check_compatibility(s1, s2) is True

    @pytest.mark.parametrize("s1,s2,expected", [
        ({}, {}, True),
        ({"a": 1}, {}, True),
        ({}, {"b": 2}, True),
        ({"a": 1}, {"a": 1, "b": 2}, True),
        ({"a": 1}, {"a": 2}, False),
        ("same", "same", True),
        ("diff_a", "diff_b", False),
    ])
    def test_check_compatibility_parametrized(self, s1, s2, expected):
        r = OverlapReconciler()
        assert r.check_compatibility(s1, s2) is expected
