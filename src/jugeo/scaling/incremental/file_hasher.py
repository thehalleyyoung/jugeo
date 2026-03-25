"""Content-addressed file tracking and import scanning.

``FileHasher`` provides SHA-256 based file-state snapshots and change
detection with rename heuristics.  ``ImportScanner`` extracts import
edges cheaply via regex or via the stdlib AST.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jugeo.scaling.incremental.models import ChangeKind, FileChange, FileState


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# FileHasher
# ---------------------------------------------------------------------------


class FileHasher:
    """Maintains a content-addressed map of source files.

    Usage::

        hasher = FileHasher(cache_dir=".jugeo_cache")
        scan = hasher.scan_directory("src/", "*.py")
        # … edit some files …
        new_scan, changes = hasher.incremental_scan("src/", scan)
    """

    def __init__(self, cache_dir: str = ".jugeo_cache") -> None:
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Hashing helpers
    # ------------------------------------------------------------------

    def hash_file(self, filepath: str) -> str:
        """Return the SHA-256 hex digest of *filepath*'s content."""
        return _file_sha256(filepath)

    def hash_content(self, content: str) -> str:
        """Return the SHA-256 hex digest of an arbitrary string."""
        return _sha256(content.encode("utf-8", errors="replace"))

    # ------------------------------------------------------------------
    # Directory scanning
    # ------------------------------------------------------------------

    def scan_directory(
        self, root: str, pattern: str = "*.py"
    ) -> dict[str, FileState]:
        """Hash every file matching *pattern* under *root*.

        Returns a mapping ``filepath -> FileState``.
        """
        result: dict[str, FileState] = {}
        root_path = Path(root).resolve()

        for dirpath, _dirnames, filenames in os.walk(root_path):
            for fname in filenames:
                if not fnmatch.fnmatch(fname, pattern):
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    stat = os.stat(full)
                    content_hash = _file_sha256(full)
                    state = FileState(
                        path=full,
                        content_hash=content_hash,
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                    )
                    result[full] = state
                except OSError:
                    continue
        return result

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def detect_changes(
        self,
        current_scan: dict[str, FileState],
        previous_scan: dict[str, FileState],
    ) -> list[FileChange]:
        """Return the list of FileChange objects between two scans."""
        changes: list[FileChange] = []
        current_paths = set(current_scan.keys())
        previous_paths = set(previous_scan.keys())

        # Newly created
        created_paths = current_paths - previous_paths
        # Deleted
        deleted_paths = previous_paths - current_paths

        # Modified
        for path in current_paths & previous_paths:
            cur = current_scan[path]
            prev = previous_scan[path]
            if cur.content_hash != prev.content_hash:
                changes.append(
                    FileChange(
                        path=path,
                        kind=ChangeKind.MODIFIED,
                        old_hash=prev.content_hash,
                        new_hash=cur.content_hash,
                    )
                )

        # Detect renames before labelling remainder as created/deleted
        rename_changes = self._detect_renames(
            list(created_paths), list(deleted_paths), current_scan, previous_scan
        )
        renamed_new_paths = {rc.path for rc in rename_changes}
        renamed_old_paths = {rc.old_path for rc in rename_changes if rc.old_path}

        changes.extend(rename_changes)

        for path in created_paths:
            if path not in renamed_new_paths:
                changes.append(
                    FileChange(
                        path=path,
                        kind=ChangeKind.CREATED,
                        new_hash=current_scan[path].content_hash,
                    )
                )

        for path in deleted_paths:
            if path not in renamed_old_paths:
                changes.append(
                    FileChange(
                        path=path,
                        kind=ChangeKind.DELETED,
                        old_hash=previous_scan[path].content_hash,
                    )
                )

        return changes

    def _detect_renames(
        self,
        created: list[str],
        deleted: list[str],
        current_scan: dict[str, FileState],
        previous_scan: dict[str, FileState],
    ) -> list[FileChange]:
        """Match created/deleted files with the same content hash as renames."""
        deleted_by_hash: dict[str, str] = {
            previous_scan[p].content_hash: p for p in deleted
        }
        renames: list[FileChange] = []
        for new_path in created:
            new_hash = current_scan[new_path].content_hash
            if new_hash in deleted_by_hash:
                old_path = deleted_by_hash.pop(new_hash)
                renames.append(
                    FileChange(
                        path=new_path,
                        kind=ChangeKind.RENAMED,
                        old_hash=new_hash,
                        new_hash=new_hash,
                        old_path=old_path,
                    )
                )
        return renames

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_scan(self, scan: dict[str, FileState], filepath: str) -> None:
        """Persist *scan* to *filepath* as JSON."""
        data = {path: state.to_dict() for path, state in scan.items()}
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def load_scan(self, filepath: str) -> dict[str, FileState]:
        """Load a previously saved scan from *filepath*."""
        with open(filepath, encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return {path: FileState.from_dict(d) for path, d in data.items()}

    # ------------------------------------------------------------------
    # Incremental scanning
    # ------------------------------------------------------------------

    def incremental_scan(
        self, root: str, previous: dict[str, FileState], pattern: str = "*.py"
    ) -> tuple[dict[str, FileState], list[FileChange]]:
        """Re-hash only files whose mtime has changed since *previous*.

        Returns ``(new_full_scan, changes)``.
        """
        current: dict[str, FileState] = {}
        root_path = Path(root).resolve()

        for dirpath, _dirnames, filenames in os.walk(root_path):
            for fname in filenames:
                if not fnmatch.fnmatch(fname, pattern):
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue

                prev_state = previous.get(full)
                if prev_state and abs(stat.st_mtime - prev_state.modified_at) < 0.01:
                    # mtime unchanged — reuse previous hash
                    current[full] = prev_state
                else:
                    try:
                        content_hash = _file_sha256(full)
                    except OSError:
                        continue
                    current[full] = FileState(
                        path=full,
                        content_hash=content_hash,
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                    )

        changes = self.detect_changes(current, previous)
        return current, changes


# ---------------------------------------------------------------------------
# ImportScanner
# ---------------------------------------------------------------------------


class ImportScanner:
    """Extracts import edges from Python source files.

    Two strategies are provided:

    * ``scan_imports_fast`` — regex-based, no AST overhead, best for bulk passes.
    * ``scan_imports_ast``  — full AST parse, more accurate for edge cases.
    """

    _import_regex_patterns: list[re.Pattern[str]] = [
        # import foo, import foo.bar.baz
        re.compile(r"^\s*import\s+([\w\.]+)", re.MULTILINE),
        # from foo import bar  /  from foo.bar import *
        re.compile(r"^\s*from\s+([\w\.]+)\s+import\s+", re.MULTILINE),
    ]

    # ------------------------------------------------------------------
    # Fast (regex) scanning
    # ------------------------------------------------------------------

    def scan_imports_fast(self, filepath: str) -> list[tuple[str, str]]:
        """Return ``(importing_module, imported_module)`` edges via regex.

        Relative imports are normalised relative to *filepath*.
        """
        try:
            with open(filepath, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError:
            return []

        module_name = self._filepath_to_module(filepath)
        edges: list[tuple[str, str]] = []
        seen: set[str] = set()

        for pattern in self._import_regex_patterns:
            for match in pattern.finditer(source):
                target = match.group(1).strip()
                if target not in seen:
                    seen.add(target)
                    edges.append((module_name, target))

        return edges

    # ------------------------------------------------------------------
    # AST-based scanning
    # ------------------------------------------------------------------

    def scan_imports_ast(self, filepath: str) -> list[tuple[str, str]]:
        """Return ``(importing_module, imported_module)`` edges via AST parse."""
        try:
            with open(filepath, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except OSError:
            return []

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return self.scan_imports_fast(filepath)

        module_name = self._filepath_to_module(filepath)
        edges: list[tuple[str, str]] = []
        seen: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name not in seen:
                        seen.add(name)
                        edges.append((module_name, name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    if node.level and node.level > 0:
                        resolved = self.resolve_relative_import(
                            filepath, "." * node.level + node.module
                        )
                    else:
                        resolved = node.module
                    if resolved not in seen:
                        seen.add(resolved)
                        edges.append((module_name, resolved))

        return edges

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def batch_scan_imports(
        self, filepaths: list[str], use_ast: bool = False
    ) -> dict[str, list[tuple[str, str]]]:
        """Scan imports for many files, returning a dict keyed by filepath."""
        scanner = self.scan_imports_ast if use_ast else self.scan_imports_fast
        return {fp: scanner(fp) for fp in filepaths}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def resolve_relative_import(
        self, importing_file: str, import_name: str
    ) -> str:
        """Convert a relative import string to an absolute module name.

        ``import_name`` is expected to start with one or more dots, e.g.
        ``..sibling.module``.
        """
        parts = import_name.lstrip(".")
        level = len(import_name) - len(parts)
        base_module = self._filepath_to_module(importing_file)
        base_parts = base_module.split(".")
        # Go up *level* package levels
        up = max(0, len(base_parts) - level)
        prefix = ".".join(base_parts[:up])
        if parts and prefix:
            return f"{prefix}.{parts}"
        return parts or prefix

    @staticmethod
    def _filepath_to_module(filepath: str) -> str:
        """Best-effort conversion of a filesystem path to a dotted module name."""
        p = Path(filepath)
        # Strip common src/ prefix if present
        parts = list(p.with_suffix("").parts)
        # Find the first component that looks like a package root (has __init__)
        for i, part in enumerate(parts):
            candidate = Path(*parts[: i + 1]) / "__init__.py"
            if candidate.exists():
                return ".".join(parts[i:])
        # Fallback: just use the stem
        return p.stem
