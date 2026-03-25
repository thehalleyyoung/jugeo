"""Lazy on-demand AST loading and coordinate extraction.

``LazyASTLoader`` loads files progressively — from a cheap docstring header
up to a full AST parse with coordinate extraction — and caches results so
that repeated accesses do not re-parse unchanged files.

``CoordinateExtractor`` separates the AST-to-coordinate logic so it can be
used independently.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jugeo.scaling.incremental.models import LazyLoadStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_source(filepath: str) -> str:
    with open(filepath, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Module-level worker functions (must be at top level for pickling)
# ---------------------------------------------------------------------------


def _worker_load_level(args: tuple[str, str]) -> tuple[str, dict[str, Any]]:
    """Worker function for parallel preloading; avoids instance pickling."""
    filepath, level_value = args
    loader = LazyASTLoader.__new__(LazyASTLoader)
    loader._cache: dict[str, dict[str, Any]] = {}
    loader._status: dict[str, LazyLoadStatus] = {}
    loader.cache_dir = ""
    level = LazyLoadStatus(level_value)
    result = loader.load_level(filepath, level)
    return filepath, result


# ---------------------------------------------------------------------------
# LazyASTLoader
# ---------------------------------------------------------------------------


class LazyASTLoader:
    """Progressively loads Python source files up to a requested fidelity level.

    Level ordering (least → most expensive):
      UNLOADED → HEADER_ONLY → IMPORTS_ONLY → FULL_AST → COORDINATES_EXTRACTED
    """

    _LEVEL_ORDER: list[LazyLoadStatus] = [
        LazyLoadStatus.UNLOADED,
        LazyLoadStatus.HEADER_ONLY,
        LazyLoadStatus.IMPORTS_ONLY,
        LazyLoadStatus.FULL_AST,
        LazyLoadStatus.COORDINATES_EXTRACTED,
    ]

    def __init__(self, cache_dir: str = ".jugeo_cache") -> None:
        self.cache_dir = cache_dir
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        # filepath -> aggregated data dict
        self._cache: dict[str, dict[str, Any]] = {}
        # filepath -> current load status
        self._status: dict[str, LazyLoadStatus] = {}
        # stats
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_level(self, filepath: str, level: LazyLoadStatus) -> dict[str, Any]:
        """Ensure *filepath* is loaded to at least *level*, return data dict."""
        current_status = self._status.get(filepath, LazyLoadStatus.UNLOADED)
        current_idx = self._LEVEL_ORDER.index(current_status)
        target_idx = self._LEVEL_ORDER.index(level)

        if current_idx >= target_idx and filepath in self._cache:
            self._hits += 1
            return self._cache[filepath]

        self._misses += 1
        data = dict(self._cache.get(filepath, {}))

        # Walk up levels until we reach the requested one
        start = current_idx + 1 if filepath in self._cache else 1
        for idx in range(start, target_idx + 1):
            lvl = self._LEVEL_ORDER[idx]
            if lvl == LazyLoadStatus.HEADER_ONLY:
                data.update(self._load_header(filepath))
            elif lvl == LazyLoadStatus.IMPORTS_ONLY:
                data.update(self._load_imports(filepath))
            elif lvl == LazyLoadStatus.FULL_AST:
                ast_data = self._load_full_ast(filepath)
                data.update(ast_data)
            elif lvl == LazyLoadStatus.COORDINATES_EXTRACTED:
                ast_data = data  # already contains ast_tree if FULL_AST loaded
                coords = self._extract_coordinates(filepath, ast_data)
                data["coordinates"] = coords

        self._cache[filepath] = data
        self._status[filepath] = level
        return data

    def get_status(self, filepath: str) -> LazyLoadStatus:
        """Return the current load level for *filepath*."""
        return self._status.get(filepath, LazyLoadStatus.UNLOADED)

    def evict(self, filepath: str) -> None:
        """Remove *filepath* from the in-memory cache."""
        self._cache.pop(filepath, None)
        self._status.pop(filepath, None)

    def preload(
        self,
        filepaths: list[str],
        level: LazyLoadStatus,
        max_workers: int = 4,
    ) -> None:
        """Preload *filepaths* up to *level* in parallel."""
        results = self._parallel_load(filepaths, level, max_workers)
        for fp, data in results.items():
            self._cache[fp] = data
            self._status[fp] = level

    # ------------------------------------------------------------------
    # Level loaders
    # ------------------------------------------------------------------

    def _load_header(self, filepath: str) -> dict[str, Any]:
        """Extract module docstring and name only."""
        name = Path(filepath).stem
        docstring = ""
        try:
            source = _read_source(filepath)
            tree = ast.parse(source, filename=filepath)
            docstring = ast.get_docstring(tree) or ""
        except (OSError, SyntaxError):
            pass
        return {
            "filepath": filepath,
            "module_name": name,
            "docstring": docstring,
            "loaded_at": time.time(),
        }

    def _load_imports(self, filepath: str) -> dict[str, Any]:
        """Extract import edges (without full AST retention)."""
        import_edges: list[tuple[str, str]] = []
        try:
            source = _read_source(filepath)
            tree = ast.parse(source, filename=filepath)
            module_name = Path(filepath).stem
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        import_edges.append((module_name, alias.name))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    import_edges.append((module_name, node.module))
        except (OSError, SyntaxError):
            pass
        return {"import_edges": import_edges}

    def _load_full_ast(self, filepath: str) -> dict[str, Any]:
        """Parse and return the full AST (stored as the live tree object)."""
        try:
            source = _read_source(filepath)
            content_hash = _sha256(source.encode("utf-8", errors="replace"))
            tree = ast.parse(source, filename=filepath)
            return {
                "source": source,
                "content_hash": content_hash,
                "ast_tree": tree,
                "parsed_at": time.time(),
            }
        except OSError:
            return {"ast_tree": None, "parse_error": "file_not_found"}
        except SyntaxError as exc:
            return {"ast_tree": None, "parse_error": str(exc)}

    def _extract_coordinates(
        self, filepath: str, ast_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Run CoordinateExtractor on the already-loaded AST."""
        tree = ast_data.get("ast_tree")
        if tree is None:
            return []
        extractor = CoordinateExtractor()
        return extractor.extract_from_ast(filepath, tree)

    # ------------------------------------------------------------------
    # Parallel loading
    # ------------------------------------------------------------------

    def _parallel_load(
        self,
        filepaths: list[str],
        level: LazyLoadStatus,
        max_workers: int,
    ) -> dict[str, dict[str, Any]]:
        """Load files in parallel using ProcessPoolExecutor."""
        if not filepaths:
            return {}
        results: dict[str, dict[str, Any]] = {}
        args = [(fp, level.value) for fp in filepaths]
        try:
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_worker_load_level, a): a[0] for a in args}
                for future in as_completed(futures):
                    fp = futures[future]
                    try:
                        _, data = future.result()
                        results[fp] = data
                    except Exception:
                        results[fp] = {"filepath": fp, "error": "parallel_load_failed"}
        except Exception:
            # Fallback to sequential loading
            for fp in filepaths:
                results[fp] = self.load_level(fp, level)
        return results

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def cache_statistics(self) -> dict[str, Any]:
        total = len(self._cache)
        by_level: dict[str, int] = {}
        for lvl in LazyLoadStatus:
            by_level[lvl.value] = sum(
                1 for s in self._status.values() if s == lvl
            )
        requests = self._hits + self._misses
        hit_rate = self._hits / requests if requests else 0.0
        return {
            "total_cached": total,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "by_level": by_level,
        }


# ---------------------------------------------------------------------------
# CoordinateExtractor
# ---------------------------------------------------------------------------


class CoordinateExtractor:
    """Extracts coordinate-like definitions from a Python AST.

    A "coordinate" here is any named top-level or class-level definition:
    functions, classes, and module-level constants.
    """

    def extract_from_ast(
        self, filepath: str, ast_tree: ast.AST
    ) -> list[dict[str, Any]]:
        """Return a flat list of coordinate descriptors found in *ast_tree*."""
        coords: list[dict[str, Any]] = []
        coords.append(self._extract_module_level(filepath))
        coords.extend(self._extract_functions(ast_tree))
        coords.extend(self._extract_classes(ast_tree))
        return coords

    # ------------------------------------------------------------------

    def _extract_functions(self, ast_tree: ast.AST) -> list[dict[str, Any]]:
        """Extract top-level and nested function definitions."""
        results: list[dict[str, Any]] = []
        for node in ast.walk(ast_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [
                    a.arg for a in node.args.args
                ]
                results.append(
                    {
                        "kind": "function",
                        "name": node.name,
                        "lineno": node.lineno,
                        "col_offset": node.col_offset,
                        "args": args,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "docstring": ast.get_docstring(node) or "",
                        "decorators": [
                            ast.unparse(d) if hasattr(ast, "unparse") else ""
                            for d in node.decorator_list
                        ],
                    }
                )
        return results

    def _extract_classes(self, ast_tree: ast.AST) -> list[dict[str, Any]]:
        """Extract class definitions with their method names."""
        results: list[dict[str, Any]] = []
        for node in ast.walk(ast_tree):
            if isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    try:
                        bases.append(ast.unparse(base) if hasattr(ast, "unparse") else "")
                    except Exception:
                        bases.append("")
                methods = [
                    n.name
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                results.append(
                    {
                        "kind": "class",
                        "name": node.name,
                        "lineno": node.lineno,
                        "bases": bases,
                        "methods": methods,
                        "docstring": ast.get_docstring(node) or "",
                    }
                )
        return results

    def _extract_module_level(self, filepath: str) -> dict[str, Any]:
        """Return a synthetic coordinate representing the module itself."""
        return {
            "kind": "module",
            "name": Path(filepath).stem,
            "filepath": filepath,
            "lineno": 0,
        }

    # ------------------------------------------------------------------
    # Incremental extraction helpers
    # ------------------------------------------------------------------

    def incremental_extract(
        self, filepath: str, old_hash: str, new_hash: str
    ) -> dict[str, Any]:
        """Extract coordinates and classify which changed relative to *old_hash*.

        The old coordinates are looked up from the cache file if available;
        otherwise the full extraction is treated as all-added.
        """
        try:
            source = _read_source(filepath)
            tree = ast.parse(source, filename=filepath)
        except (OSError, SyntaxError):
            return {"added": [], "removed": [], "modified": [], "error": True}

        new_coords = self.extract_from_ast(filepath, tree)

        # Try to load old coords from a stable cache key based on old_hash
        old_coords: list[dict[str, Any]] = []
        cache_path = _coord_cache_path(filepath, old_hash)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    old_coords = json.load(fh)
            except (OSError, json.JSONDecodeError):
                pass

        # Persist new coords
        new_cache_path = _coord_cache_path(filepath, new_hash)
        try:
            os.makedirs(os.path.dirname(new_cache_path), exist_ok=True)
            with open(new_cache_path, "w", encoding="utf-8") as fh:
                json.dump(new_coords, fh)
        except OSError:
            pass

        diff = self._diff_coordinates(old_coords, new_coords)
        return diff

    def _diff_coordinates(
        self,
        old_coords: list[dict[str, Any]],
        new_coords: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Classify coordinates into added, removed, modified."""
        old_by_name = {f"{c.get('kind','?')}::{c.get('name','?')}": c for c in old_coords}
        new_by_name = {f"{c.get('kind','?')}::{c.get('name','?')}": c for c in new_coords}

        added = [c for key, c in new_by_name.items() if key not in old_by_name]
        removed = [c for key, c in old_by_name.items() if key not in new_by_name]
        modified: list[dict[str, Any]] = []

        for key in set(old_by_name) & set(new_by_name):
            if old_by_name[key] != new_by_name[key]:
                modified.append(new_by_name[key])

        return {"added": added, "removed": removed, "modified": modified}


# ---------------------------------------------------------------------------
# Coordinate cache path helper
# ---------------------------------------------------------------------------


def _coord_cache_path(filepath: str, content_hash: str) -> str:
    safe_name = hashlib.md5(filepath.encode()).hexdigest()
    return os.path.join(".jugeo_cache", "coords", f"{safe_name}_{content_hash[:16]}.json")
