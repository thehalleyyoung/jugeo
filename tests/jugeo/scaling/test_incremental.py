"""Tests for the jugeo.scaling.incremental module.

Covers:
- FileHasher: hash files, detect changes, detect renames
- ImportScanner: regex and AST import scanning
- LazyASTLoader: progressive loading and cache
- CoordinateExtractor: extraction from Python files
- DeltaEngine: delta computation, merge, apply, invert
- EnhancedInvalidationGraph: full, contract-bounded, tiered, probabilistic cascades
- Contract boundaries stopping invalidation
- Batch invalidation
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from jugeo.scaling.incremental.delta_engine import DeltaEngine
from jugeo.scaling.incremental.file_hasher import FileHasher, ImportScanner
from jugeo.scaling.incremental.invalidation_graph import EnhancedInvalidationGraph
from jugeo.scaling.incremental.lazy_loader import CoordinateExtractor, LazyASTLoader
from jugeo.scaling.incremental.models import (
    CacheEntry,
    CacheStatistics,
    ChangeKind,
    ChangeSet,
    DeltaRecord,
    FileChange,
    FileState,
    InvalidationEvent,
    InvalidationPolicy,
    InvalidationStrategy,
    LazyLoadStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temporary directory path as a string."""
    return str(tmp_path)


@pytest.fixture
def sample_py_file(tmp_path):
    """Create a simple Python file and return its path."""
    src = tmp_path / "sample.py"
    src.write_text(
        '"""Module docstring."""\n'
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "CONSTANT = 42\n"
        "\n"
        "def greet(name: str) -> str:\n"
        '    """Return greeting."""\n'
        "    return f'Hello, {name}'\n"
        "\n"
        "class Greeter:\n"
        '    """Greeter class."""\n'
        "\n"
        "    def __init__(self, prefix: str = 'Hi') -> None:\n"
        "        self.prefix = prefix\n"
        "\n"
        "    def say(self, name: str) -> str:\n"
        "        return f'{self.prefix}, {name}'\n",
        encoding="utf-8",
    )
    return str(src)


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_file_state_round_trip(self):
        fs = FileState(
            path="/a/b.py",
            content_hash="abc123",
            size_bytes=100,
            modified_at=1234567890.0,
            parsed=True,
            coordinate_ids=["foo", "bar"],
            import_edges=[("foo", "bar")],
            last_parsed_at=1234567891.0,
        )
        d = fs.to_dict()
        fs2 = FileState.from_dict(d)
        assert fs2.path == fs.path
        assert fs2.content_hash == fs.content_hash
        assert fs2.coordinate_ids == fs.coordinate_ids
        assert fs2.import_edges == fs.import_edges
        assert fs2.last_parsed_at == fs.last_parsed_at

    def test_file_change_round_trip(self):
        fc = FileChange(
            path="/a/b.py",
            kind=ChangeKind.RENAMED,
            old_hash="old",
            new_hash="new",
            old_path="/a/a.py",
        )
        d = fc.to_dict()
        fc2 = FileChange.from_dict(d)
        assert fc2.kind == ChangeKind.RENAMED
        assert fc2.old_path == "/a/a.py"

    def test_change_set_create(self):
        changes = [
            FileChange(path="/x.py", kind=ChangeKind.CREATED, new_hash="abc"),
        ]
        cs = ChangeSet.create(changes)
        assert len(cs.changes) == 1
        assert cs.id  # non-empty UUID
        assert cs.timestamp > 0

    def test_change_set_round_trip(self):
        cs = ChangeSet.create([
            FileChange(path="/x.py", kind=ChangeKind.MODIFIED, old_hash="a", new_hash="b"),
        ])
        cs.affected_coordinates = ["coord1"]
        d = cs.to_dict()
        cs2 = ChangeSet.from_dict(d)
        assert cs2.id == cs.id
        assert len(cs2.changes) == 1
        assert cs2.affected_coordinates == ["coord1"]

    def test_delta_record_round_trip(self):
        dr = DeltaRecord(
            change_set_id="cs1",
            added_coordinates=["a"],
            removed_coordinates=["b"],
            modified_coordinates=["c"],
        )
        d = dr.to_dict()
        dr2 = DeltaRecord.from_dict(d)
        assert dr2.added_coordinates == ["a"]
        assert dr2.removed_coordinates == ["b"]
        assert dr2.change_set_id == "cs1"
        assert not dr2.is_empty()

    def test_delta_record_empty(self):
        dr = DeltaRecord(change_set_id="x")
        assert dr.is_empty()

    def test_invalidation_event_round_trip(self):
        event = InvalidationEvent.create(
            source="A",
            invalidated=["B", "C"],
            depth=2,
            strategy=InvalidationStrategy.TIERED,
        )
        d = event.to_dict()
        e2 = InvalidationEvent.from_dict(d)
        assert e2.source_coordinate == "A"
        assert e2.strategy == "TIERED"
        assert "B" in e2.invalidated_coordinates

    def test_invalidation_policy_round_trip(self):
        policy = InvalidationPolicy(max_cascade_depth=5, probabilistic_threshold=500)
        d = policy.to_dict()
        p2 = InvalidationPolicy.from_dict(d)
        assert p2.max_cascade_depth == 5
        assert p2.probabilistic_threshold == 500

    def test_cache_entry_lifecycle(self):
        entry = CacheEntry.create("key1", "hash1", depends_on=["key0"])
        assert entry.is_valid
        assert entry.access_count == 1
        entry.touch()
        assert entry.access_count == 2
        entry.invalidate()
        assert not entry.is_valid

    def test_cache_entry_round_trip(self):
        entry = CacheEntry.create("k", "h")
        d = entry.to_dict()
        e2 = CacheEntry.from_dict(d)
        assert e2.key == "k"
        assert e2.is_valid

    def test_cache_statistics_empty(self):
        stats = CacheStatistics.empty()
        assert stats.hit_rate == 0.0
        d = stats.to_dict()
        s2 = CacheStatistics.from_dict(d)
        assert s2.total_entries == 0


# ---------------------------------------------------------------------------
# FileHasher
# ---------------------------------------------------------------------------


class TestFileHasher:
    def test_hash_file_deterministic(self, sample_py_file, cache_dir):
        hasher = FileHasher(cache_dir=cache_dir)
        h1 = hasher.hash_file(sample_py_file)
        h2 = hasher.hash_file(sample_py_file)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_content(self, cache_dir):
        hasher = FileHasher(cache_dir=cache_dir)
        h = hasher.hash_content("hello world")
        assert len(h) == 64
        assert h == hasher.hash_content("hello world")

    def test_hash_file_differs_after_content_change(self, tmp_path, cache_dir):
        f = tmp_path / "f.py"
        f.write_text("x = 1\n")
        hasher = FileHasher(cache_dir=cache_dir)
        h1 = hasher.hash_file(str(f))
        f.write_text("x = 2\n")
        h2 = hasher.hash_file(str(f))
        assert h1 != h2

    def test_scan_directory(self, tmp_path, cache_dir):
        (tmp_path / "a.py").write_text("pass\n")
        (tmp_path / "b.py").write_text("x = 1\n")
        (tmp_path / "c.txt").write_text("not python\n")
        hasher = FileHasher(cache_dir=cache_dir)
        scan = hasher.scan_directory(str(tmp_path), "*.py")
        assert len(scan) == 2
        py_files = {Path(k).name for k in scan}
        assert "a.py" in py_files
        assert "b.py" in py_files
        assert "c.txt" not in py_files

    def test_detect_changes_created(self, tmp_path, cache_dir):
        hasher = FileHasher(cache_dir=cache_dir)
        prev: dict = {}
        (tmp_path / "new.py").write_text("pass\n")
        current = hasher.scan_directory(str(tmp_path), "*.py")
        changes = hasher.detect_changes(current, prev)
        assert any(c.kind == ChangeKind.CREATED for c in changes)

    def test_detect_changes_deleted(self, tmp_path, cache_dir):
        f = tmp_path / "old.py"
        f.write_text("pass\n")
        hasher = FileHasher(cache_dir=cache_dir)
        prev = hasher.scan_directory(str(tmp_path), "*.py")
        f.unlink()
        current = hasher.scan_directory(str(tmp_path), "*.py")
        changes = hasher.detect_changes(current, prev)
        assert any(c.kind == ChangeKind.DELETED for c in changes)

    def test_detect_changes_modified(self, tmp_path, cache_dir):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        hasher = FileHasher(cache_dir=cache_dir)
        prev = hasher.scan_directory(str(tmp_path), "*.py")
        time.sleep(0.01)
        f.write_text("x = 2\n")
        current = hasher.scan_directory(str(tmp_path), "*.py")
        changes = hasher.detect_changes(current, prev)
        assert any(c.kind == ChangeKind.MODIFIED for c in changes)

    def test_detect_rename(self, tmp_path, cache_dir):
        old = tmp_path / "old.py"
        old.write_text("renamed_content = True\n")
        hasher = FileHasher(cache_dir=cache_dir)
        prev = hasher.scan_directory(str(tmp_path), "*.py")
        new = tmp_path / "new.py"
        old.rename(new)
        current = hasher.scan_directory(str(tmp_path), "*.py")
        changes = hasher.detect_changes(current, prev)
        rename_changes = [c for c in changes if c.kind == ChangeKind.RENAMED]
        assert len(rename_changes) == 1
        assert Path(rename_changes[0].path).name == "new.py"
        assert Path(rename_changes[0].old_path).name == "old.py"

    def test_save_and_load_scan(self, tmp_path, cache_dir):
        f = tmp_path / "a.py"
        f.write_text("pass\n")
        hasher = FileHasher(cache_dir=cache_dir)
        scan = hasher.scan_directory(str(tmp_path), "*.py")
        save_path = str(tmp_path / "scan.json")
        hasher.save_scan(scan, save_path)
        loaded = hasher.load_scan(save_path)
        assert set(loaded.keys()) == set(scan.keys())
        for path in scan:
            assert loaded[path].content_hash == scan[path].content_hash

    def test_incremental_scan_skips_unchanged(self, tmp_path, cache_dir):
        f = tmp_path / "stable.py"
        f.write_text("pass\n")
        hasher = FileHasher(cache_dir=cache_dir)
        prev = hasher.scan_directory(str(tmp_path), "*.py")
        # incremental_scan should detect no changes
        current, changes = hasher.incremental_scan(str(tmp_path), prev, "*.py")
        assert changes == []

    def test_incremental_scan_detects_modification(self, tmp_path, cache_dir):
        f = tmp_path / "changing.py"
        f.write_text("x = 1\n")
        hasher = FileHasher(cache_dir=cache_dir)
        prev = hasher.scan_directory(str(tmp_path), "*.py")
        # Modify mtime via touch with new content
        time.sleep(0.05)
        f.write_text("x = 9999\n")
        current, changes = hasher.incremental_scan(str(tmp_path), prev, "*.py")
        assert any(c.kind == ChangeKind.MODIFIED for c in changes)


# ---------------------------------------------------------------------------
# ImportScanner
# ---------------------------------------------------------------------------


class TestImportScanner:
    def _make_import_file(self, tmp_path, name: str, content: str) -> str:
        f = tmp_path / name
        f.write_text(content, encoding="utf-8")
        return str(f)

    def test_fast_scan_basic_imports(self, tmp_path):
        fp = self._make_import_file(
            tmp_path,
            "imports1.py",
            "import os\nimport sys\nfrom pathlib import Path\n",
        )
        scanner = ImportScanner()
        edges = scanner.scan_imports_fast(fp)
        targets = [e[1] for e in edges]
        assert "os" in targets
        assert "sys" in targets
        assert "pathlib" in targets

    def test_ast_scan_basic_imports(self, tmp_path):
        fp = self._make_import_file(
            tmp_path,
            "imports2.py",
            "import os\nimport sys\nfrom pathlib import Path\n",
        )
        scanner = ImportScanner()
        edges = scanner.scan_imports_ast(fp)
        targets = [e[1] for e in edges]
        assert "os" in targets
        assert "sys" in targets
        assert "pathlib" in targets

    def test_fast_and_ast_agree_on_simple_file(self, tmp_path):
        content = "import os\nfrom pathlib import Path\nimport collections\n"
        fp = self._make_import_file(tmp_path, "agree.py", content)
        scanner = ImportScanner()
        fast_targets = {e[1] for e in scanner.scan_imports_fast(fp)}
        ast_targets = {e[1] for e in scanner.scan_imports_ast(fp)}
        assert fast_targets == ast_targets

    def test_no_imports(self, tmp_path):
        fp = self._make_import_file(tmp_path, "empty.py", "x = 1\n")
        scanner = ImportScanner()
        assert scanner.scan_imports_fast(fp) == []
        assert scanner.scan_imports_ast(fp) == []

    def test_from_import_captured(self, tmp_path):
        fp = self._make_import_file(
            tmp_path, "from_import.py", "from os.path import join, exists\n"
        )
        scanner = ImportScanner()
        targets_fast = {e[1] for e in scanner.scan_imports_fast(fp)}
        targets_ast = {e[1] for e in scanner.scan_imports_ast(fp)}
        assert "os.path" in targets_fast
        assert "os.path" in targets_ast

    def test_batch_scan_imports(self, tmp_path):
        fp1 = self._make_import_file(tmp_path, "b1.py", "import os\n")
        fp2 = self._make_import_file(tmp_path, "b2.py", "import sys\n")
        scanner = ImportScanner()
        result = scanner.batch_scan_imports([fp1, fp2], use_ast=False)
        assert fp1 in result
        assert fp2 in result
        assert any(e[1] == "os" for e in result[fp1])
        assert any(e[1] == "sys" for e in result[fp2])

    def test_batch_scan_imports_ast(self, tmp_path):
        fp1 = self._make_import_file(tmp_path, "b3.py", "import os\n")
        fp2 = self._make_import_file(tmp_path, "b4.py", "import sys\n")
        scanner = ImportScanner()
        result = scanner.batch_scan_imports([fp1, fp2], use_ast=True)
        assert any(e[1] == "os" for e in result[fp1])
        assert any(e[1] == "sys" for e in result[fp2])

    def test_missing_file_returns_empty(self):
        scanner = ImportScanner()
        assert scanner.scan_imports_fast("/nonexistent/file.py") == []
        assert scanner.scan_imports_ast("/nonexistent/file.py") == []

    def test_relative_import_resolution(self, tmp_path):
        # Build a stub package for module resolution
        scanner = ImportScanner()
        # Just verify the method doesn't crash and returns a string
        result = scanner.resolve_relative_import("/pkg/sub/module.py", "..sibling")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# LazyASTLoader
# ---------------------------------------------------------------------------


class TestLazyASTLoader:
    def test_unloaded_status(self, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        assert loader.get_status("/nonexistent.py") == LazyLoadStatus.UNLOADED

    def test_load_header(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        data = loader.load_level(sample_py_file, LazyLoadStatus.HEADER_ONLY)
        assert "module_name" in data
        assert "docstring" in data
        assert data["module_name"] == "sample"
        assert loader.get_status(sample_py_file) == LazyLoadStatus.HEADER_ONLY

    def test_load_imports(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        data = loader.load_level(sample_py_file, LazyLoadStatus.IMPORTS_ONLY)
        assert "import_edges" in data
        targets = [e[1] for e in data["import_edges"]]
        assert "os" in targets
        assert loader.get_status(sample_py_file) == LazyLoadStatus.IMPORTS_ONLY

    def test_load_full_ast(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        data = loader.load_level(sample_py_file, LazyLoadStatus.FULL_AST)
        assert "ast_tree" in data
        assert data["ast_tree"] is not None
        assert "content_hash" in data
        assert loader.get_status(sample_py_file) == LazyLoadStatus.FULL_AST

    def test_load_coordinates_extracted(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        data = loader.load_level(sample_py_file, LazyLoadStatus.COORDINATES_EXTRACTED)
        assert "coordinates" in data
        kinds = {c["kind"] for c in data["coordinates"]}
        assert "function" in kinds
        assert "class" in kinds
        assert loader.get_status(sample_py_file) == LazyLoadStatus.COORDINATES_EXTRACTED

    def test_cache_hit_on_second_load(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        loader.load_level(sample_py_file, LazyLoadStatus.FULL_AST)
        stats_before = loader.cache_statistics()
        loader.load_level(sample_py_file, LazyLoadStatus.FULL_AST)
        stats_after = loader.cache_statistics()
        assert stats_after["hits"] > stats_before["hits"]

    def test_evict(self, sample_py_file, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        loader.load_level(sample_py_file, LazyLoadStatus.HEADER_ONLY)
        loader.evict(sample_py_file)
        assert loader.get_status(sample_py_file) == LazyLoadStatus.UNLOADED

    def test_cache_statistics_keys(self, cache_dir):
        loader = LazyASTLoader(cache_dir=cache_dir)
        stats = loader.cache_statistics()
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
        assert "by_level" in stats

    def test_level_progression_is_monotone(self, sample_py_file, cache_dir):
        """Loading to a higher level should not downgrade the cached level."""
        loader = LazyASTLoader(cache_dir=cache_dir)
        loader.load_level(sample_py_file, LazyLoadStatus.FULL_AST)
        loader.load_level(sample_py_file, LazyLoadStatus.HEADER_ONLY)  # should not downgrade
        assert loader.get_status(sample_py_file) == LazyLoadStatus.FULL_AST

    def test_preload_multiple_files(self, tmp_path, cache_dir):
        files = []
        for i in range(3):
            f = tmp_path / f"m{i}.py"
            f.write_text(f"# module {i}\nimport os\n")
            files.append(str(f))
        loader = LazyASTLoader(cache_dir=cache_dir)
        loader.preload(files, LazyLoadStatus.IMPORTS_ONLY, max_workers=2)
        for fp in files:
            # After preload, status should be at least IMPORTS_ONLY
            status = loader.get_status(fp)
            assert status != LazyLoadStatus.UNLOADED


# ---------------------------------------------------------------------------
# CoordinateExtractor
# ---------------------------------------------------------------------------


class TestCoordinateExtractor:
    def test_extract_module_coord(self, sample_py_file):
        import ast as ast_mod

        with open(sample_py_file) as fh:
            tree = ast_mod.parse(fh.read())
        extractor = CoordinateExtractor()
        coords = extractor.extract_from_ast(sample_py_file, tree)
        kinds = {c["kind"] for c in coords}
        assert "module" in kinds

    def test_extract_functions(self, sample_py_file):
        import ast as ast_mod

        with open(sample_py_file) as fh:
            tree = ast_mod.parse(fh.read())
        extractor = CoordinateExtractor()
        coords = extractor.extract_from_ast(sample_py_file, tree)
        funcs = [c for c in coords if c["kind"] == "function"]
        names = {f["name"] for f in funcs}
        assert "greet" in names

    def test_extract_classes(self, sample_py_file):
        import ast as ast_mod

        with open(sample_py_file) as fh:
            tree = ast_mod.parse(fh.read())
        extractor = CoordinateExtractor()
        coords = extractor.extract_from_ast(sample_py_file, tree)
        classes = [c for c in coords if c["kind"] == "class"]
        names = {c["name"] for c in classes}
        assert "Greeter" in names

    def test_class_has_methods(self, sample_py_file):
        import ast as ast_mod

        with open(sample_py_file) as fh:
            tree = ast_mod.parse(fh.read())
        extractor = CoordinateExtractor()
        coords = extractor.extract_from_ast(sample_py_file, tree)
        greeter = next(c for c in coords if c.get("name") == "Greeter")
        assert "say" in greeter["methods"]

    def test_diff_coordinates(self):
        extractor = CoordinateExtractor()
        old = [
            {"kind": "function", "name": "foo", "lineno": 1},
            {"kind": "function", "name": "bar", "lineno": 5},
        ]
        new = [
            {"kind": "function", "name": "foo", "lineno": 1, "extra": True},
            {"kind": "function", "name": "baz", "lineno": 10},
        ]
        diff = extractor._diff_coordinates(old, new)
        added_names = {c["name"] for c in diff["added"]}
        removed_names = {c["name"] for c in diff["removed"]}
        modified_names = {c["name"] for c in diff["modified"]}
        assert "baz" in added_names
        assert "bar" in removed_names
        assert "foo" in modified_names

    def test_incremental_extract(self, sample_py_file):
        extractor = CoordinateExtractor()
        import hashlib

        with open(sample_py_file, "rb") as fh:
            content = fh.read()
        old_hash = hashlib.sha256(content).hexdigest()
        new_hash = hashlib.sha256(content + b"\n# comment\n").hexdigest()
        result = extractor.incremental_extract(sample_py_file, old_hash, new_hash)
        assert "added" in result
        assert "removed" in result
        assert "modified" in result


# ---------------------------------------------------------------------------
# DeltaEngine
# ---------------------------------------------------------------------------


class TestDeltaEngine:
    def _make_state(self, path: str, coord_ids: list[str]) -> FileState:
        return FileState(
            path=path,
            content_hash="hash_" + path,
            size_bytes=100,
            modified_at=time.time(),
            coordinate_ids=coord_ids,
        )

    def test_compute_delta_created(self):
        engine = DeltaEngine()
        change = FileChange(path="/a.py", kind=ChangeKind.CREATED, new_hash="h1")
        cs = ChangeSet(id="cs1", changes=[change], timestamp=time.time())
        fs = {"/a.py": self._make_state("/a.py", ["a::foo", "a::Bar"])}
        delta = engine.compute_delta(cs, fs, {})
        assert "a::foo" in delta.added_coordinates
        assert "a::Bar" in delta.added_coordinates
        assert delta.removed_coordinates == []

    def test_compute_delta_deleted(self):
        engine = DeltaEngine()
        change = FileChange(path="/b.py", kind=ChangeKind.DELETED, old_hash="h1")
        cs = ChangeSet(
            id="cs2",
            changes=[change],
            timestamp=time.time(),
            affected_coordinates=[],
        )
        fs = {"/b.py": self._make_state("/b.py", ["b::stuff"])}
        delta = engine.compute_delta(cs, fs, {})
        assert delta.removed_coordinates  # should have at least one

    def test_compute_delta_modified(self):
        engine = DeltaEngine()
        change = FileChange(
            path="/c.py", kind=ChangeKind.MODIFIED, old_hash="h1", new_hash="h2"
        )
        cs = ChangeSet(id="cs3", changes=[change], timestamp=time.time())
        fs = {"/c.py": self._make_state("/c.py", ["c::greet"])}
        delta = engine.compute_delta(cs, fs, {})
        assert "c::greet" in delta.modified_coordinates

    def test_compute_delta_with_import_edges(self):
        engine = DeltaEngine()
        change = FileChange(path="/d.py", kind=ChangeKind.CREATED, new_hash="h1")
        cs = ChangeSet(id="cs4", changes=[change], timestamp=time.time())
        fs = {"/d.py": self._make_state("/d.py", ["d::func"])}
        edges = {"/d.py": [("d", "os"), ("d", "sys")]}
        delta = engine.compute_delta(cs, fs, edges)
        assert "d->os" in delta.added_morphisms
        assert "d->sys" in delta.added_morphisms

    def test_merge_deltas(self):
        engine = DeltaEngine()
        d1 = DeltaRecord(
            change_set_id="a",
            added_coordinates=["x"],
            removed_coordinates=["y"],
        )
        d2 = DeltaRecord(
            change_set_id="b",
            added_coordinates=["z"],
            modified_coordinates=["w"],
        )
        merged = engine.merge_deltas([d1, d2])
        assert "x" in merged.added_coordinates
        assert "z" in merged.added_coordinates
        assert "y" in merged.removed_coordinates
        assert "w" in merged.modified_coordinates

    def test_merge_conflict_resolution(self):
        """An ID that appears in both added and removed becomes modified."""
        engine = DeltaEngine()
        d1 = DeltaRecord(change_set_id="a", added_coordinates=["conflict"])
        d2 = DeltaRecord(change_set_id="b", removed_coordinates=["conflict"])
        merged = engine.merge_deltas([d1, d2])
        assert "conflict" not in merged.added_coordinates
        assert "conflict" not in merged.removed_coordinates
        assert "conflict" in merged.modified_coordinates

    def test_apply_delta(self):
        engine = DeltaEngine()
        state = {"coordinates": ["x", "y"], "morphisms": ["x->y"]}
        delta = DeltaRecord(
            change_set_id="cs",
            added_coordinates=["z"],
            removed_coordinates=["y"],
            modified_coordinates=["x"],
            added_morphisms=["x->z"],
            removed_morphisms=["x->y"],
        )
        new_state = engine.apply_delta(state, delta)
        assert "z" in new_state["coordinates"]
        assert "y" not in new_state["coordinates"]
        assert "x" in new_state["coordinates"]
        assert "x->z" in new_state["morphisms"]
        assert "x->y" not in new_state["morphisms"]

    def test_invert_delta(self):
        engine = DeltaEngine()
        delta = DeltaRecord(
            change_set_id="cs",
            added_coordinates=["a"],
            removed_coordinates=["b"],
            modified_coordinates=["c"],
        )
        inv = engine.invert_delta(delta)
        assert "a" in inv.removed_coordinates
        assert "b" in inv.added_coordinates
        assert "c" in inv.modified_coordinates

    def test_apply_then_invert_is_identity(self):
        engine = DeltaEngine()
        initial_state = {"coordinates": ["x", "y"], "morphisms": []}
        delta = DeltaRecord(
            change_set_id="cs",
            added_coordinates=["z"],
            removed_coordinates=["x"],
        )
        new_state = engine.apply_delta(initial_state, delta)
        assert "z" in new_state["coordinates"]
        assert "x" not in new_state["coordinates"]
        inv = engine.invert_delta(delta)
        restored = engine.apply_delta(new_state, inv)
        assert "x" in restored["coordinates"]
        assert "z" not in restored["coordinates"]

    def test_affected_overlaps(self):
        engine = DeltaEngine()
        delta = DeltaRecord(
            change_set_id="cs",
            modified_coordinates=["A", "B"],
        )
        overlaps = [
            {"id": "o1", "coordinates": ["A", "C"]},
            {"id": "o2", "coordinates": ["D", "E"]},
        ]
        affected = engine.affected_overlaps(delta, overlaps)
        assert len(affected) == 1
        assert affected[0]["id"] == "o1"

    def test_affected_covers(self):
        engine = DeltaEngine()
        delta = DeltaRecord(change_set_id="cs", added_coordinates=["X"])
        covers = [
            {"id": "c1", "coordinates": ["X", "Y"]},
            {"id": "c2", "coordinates": ["A", "B"]},
        ]
        affected = engine.affected_covers(delta, covers)
        assert len(affected) == 1
        assert affected[0]["id"] == "c1"


# ---------------------------------------------------------------------------
# EnhancedInvalidationGraph
# ---------------------------------------------------------------------------


class TestEnhancedInvalidationGraph:
    def _build_chain(self, length: int) -> tuple[EnhancedInvalidationGraph, list[str]]:
        """Build A -> B -> C -> ... chain of *length* nodes."""
        graph = EnhancedInvalidationGraph()
        nodes = [f"node_{i}" for i in range(length)]
        for i in range(1, length):
            graph.add_dependency(nodes[i], nodes[i - 1])
        return graph, nodes

    def test_add_and_remove_dependency(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        assert "B" in graph._dependents["A"]
        graph.remove_dependency("B", "A")
        assert "B" not in graph._dependents["A"]

    def test_full_cascade(self):
        graph, nodes = self._build_chain(5)
        event = graph.invalidate(nodes[0])
        # All subsequent nodes should be invalidated
        assert set(event.invalidated_coordinates) == set(nodes[1:])

    def test_cascade_depth_limit(self):
        policy = InvalidationPolicy(max_cascade_depth=2)
        graph = EnhancedInvalidationGraph(policy=policy)
        # Build A -> B -> C -> D
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "B")
        graph.add_dependency("D", "C")
        event = graph.invalidate("A")
        # With max depth 2, D should NOT appear (it's at depth 3 from A)
        assert "D" not in event.invalidated_coordinates

    def test_contract_boundary_stops_cascade(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "B")
        graph.add_contract_boundary("B", contract_hash="stable_hash")
        # Implementation-only change to A should NOT propagate past B (boundary absorbs)
        event = graph.invalidate("A", change_kind="implementation")
        assert "C" not in event.invalidated_coordinates

    def test_contract_boundary_does_not_stop_contract_change(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "B")
        graph.add_contract_boundary("B", contract_hash="stable_hash")
        # Signature change DOES break the contract
        event = graph.invalidate("A", change_kind="signature")
        assert "B" in event.invalidated_coordinates or "C" in event.invalidated_coordinates

    def test_tiered_strategy(self):
        graph = EnhancedInvalidationGraph(
            policy=InvalidationPolicy(use_contract_boundaries=False, max_cascade_depth=5)
        )
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "B")
        graph.add_dependency("D", "C")
        event = graph.invalidate("A")
        assert "B" in event.invalidated_coordinates
        assert "C" in event.invalidated_coordinates

    def test_probabilistic_cascade(self):
        """Build a large graph and trigger probabilistic strategy."""
        policy = InvalidationPolicy(
            probabilistic_threshold=5,
            use_contract_boundaries=False,
        )
        graph = EnhancedInvalidationGraph(policy=policy)
        # Fan-out: A -> B0..B9, each Bi -> Ci
        for i in range(10):
            graph.add_dependency(f"B{i}", "A")
            graph.add_dependency(f"C{i}", f"B{i}")
        event = graph.invalidate("A")
        # At least some nodes should be invalidated
        assert len(event.invalidated_coordinates) > 0

    def test_batch_invalidate_deduplicates(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("B", "X")
        # Both A and X affect B; batch should not list B twice
        events = graph.batch_invalidate(["A", "X"])
        all_invalidated: list[str] = []
        for e in events:
            all_invalidated.extend(e.invalidated_coordinates)
        assert all_invalidated.count("B") <= 1

    def test_all_dependents(self):
        graph, nodes = self._build_chain(4)
        deps = graph.all_dependents(nodes[0])
        assert set(nodes[1:]) == deps

    def test_all_dependencies(self):
        graph, nodes = self._build_chain(4)
        # Last node depends on all predecessors
        deps = graph.all_dependencies(nodes[-1])
        assert set(nodes[:-1]) == deps

    def test_dependency_count(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "A")
        dependents, dependencies = graph.dependency_count("A")
        assert dependents == 2
        assert dependencies == 0

    def test_topological_sort_linear(self):
        graph, nodes = self._build_chain(4)
        order = graph.topological_sort()
        # Each node should appear before its dependents
        for i in range(1, len(nodes)):
            assert order.index(nodes[i - 1]) < order.index(nodes[i])

    def test_is_acyclic_true(self):
        graph, _ = self._build_chain(3)
        assert graph.is_acyclic()

    def test_is_acyclic_false(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("A", "B")  # cycle
        assert not graph.is_acyclic()

    def test_statistics_keys(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.invalidate("A")
        stats = graph.statistics()
        assert "total_nodes" in stats
        assert "total_edges" in stats
        assert "total_invalidations" in stats
        assert stats["total_invalidations"] == 1

    def test_change_impact_analysis(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "B")
        analysis = graph.change_impact_analysis("A", "implementation change")
        assert analysis["direct_dependents"] == 1
        assert analysis["total_transitive_dependents"] == 2
        assert "recommended_strategy" in analysis

    def test_no_self_loop_in_cascade(self):
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        event = graph.invalidate("A")
        assert "A" not in event.invalidated_coordinates

    def test_diamond_dependency_no_duplicates(self):
        """A -> B, A -> C, B -> D, C -> D should not list D twice."""
        graph = EnhancedInvalidationGraph()
        graph.add_dependency("B", "A")
        graph.add_dependency("C", "A")
        graph.add_dependency("D", "B")
        graph.add_dependency("D", "C")
        event = graph.invalidate("A")
        assert event.invalidated_coordinates.count("D") <= 1
