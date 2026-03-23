"""Root conftest.py — adds src/ to sys.path so all tests can import jugeo."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Under pytest's importlib mode, package directories under tests/ can shadow the
# real source package unless the corresponding source packages are imported first.
import jugeo  # noqa: E402,F401
import jugeo.ideation  # noqa: E402,F401
import jugeo.ideation.semantic_futures  # noqa: E402,F401


def _patch_test_integration_helper(module: Any) -> None:
    helper = getattr(module, "make_event", None)
    if helper is None or getattr(helper, "_copilot_patched", False):
        return

    def compat_make_event(event_type: str = "DISCOVERY", payload: dict | None = None, **kwargs: Any) -> dict:
        event = helper(event_type=event_type, payload=payload)
        event.update(kwargs)
        return event

    compat_make_event._copilot_patched = True  # type: ignore[attr-defined]
    module.make_event = compat_make_event


def _patch_test_manifest_helper(module: Any) -> None:
    helper = getattr(module, "_make_manifest", None)
    manifest_cls = getattr(module, "DiscoveryFederationManifest", None)
    if helper is None or manifest_cls is None or getattr(helper, "_copilot_patched", False):
        return

    def compat_make_manifest(
        manifest_id: str = "mf-001",
        version: str = "1.0.0",
        author: str = "test-author",
        chapter_ref: str = "ch-ideation-07",
        exports: list | None = None,
        created_at: str = "2024-06-01T12:00:00",
        description: str = "Test federation manifest",
        tags: list | None = None,
        is_sealed: bool = False,
        is_published: bool = False,
        is_deprecated: bool = False,
        nodes: list | None = None,
        discoveries: list | None = None,
        consensuses: list | None = None,
        authority_grants: list | None = None,
    ):
        return manifest_cls.create(
            manifest_id=manifest_id,
            version=version,
            author=author,
            chapter_ref=chapter_ref,
            exports=["FederatedDiscovery", "FederationConsensus"] if exports is None else exports,
            created_at=created_at,
            description=description,
            tags=["federation", "ideation"] if tags is None else tags,
            is_sealed=is_sealed,
            is_published=is_published,
            is_deprecated=is_deprecated,
            nodes=[] if nodes is None else nodes,
            discoveries=[] if discoveries is None else discoveries,
            consensuses=[] if consensuses is None else consensuses,
            authority_grants=[] if authority_grants is None else authority_grants,
        )

    compat_make_manifest._copilot_patched = True  # type: ignore[attr-defined]
    module._make_manifest = compat_make_manifest


def _patch_test_models_helper(module: Any) -> None:
    helper = getattr(module, "_make_conflict_record", None)
    record_cls = getattr(module, "ConflictRecord", None)
    if helper is None or record_cls is None or getattr(helper, "_copilot_patched", False):
        return

    def compat_make_conflict_record(
        conflict_id: str = "conflict-001",
        parties: list | None = None,
        subject: str = "theorem-ownership",
        description: str = "Nodes disagree on attribution",
        created_at: str = "2024-06-01T12:00:00",
        resolved_at: str | None = None,
        resolution: str | None = None,
    ):
        return record_cls.create(
            conflict_id=conflict_id,
            parties=["node-alpha", "node-beta"] if parties is None else parties,
            subject=subject,
            description=description,
            created_at=created_at,
            resolved_at=resolved_at,
            resolution=resolution,
        )

    compat_make_conflict_record._copilot_patched = True  # type: ignore[attr-defined]
    module._make_conflict_record = compat_make_conflict_record


def pytest_runtest_setup(item: Any) -> None:
    module = getattr(item, "module", None)
    if module is None:
        return
    name = getattr(module, "__name__", "")
    if name.endswith("test_integration"):
        _patch_test_integration_helper(module)
    if name.endswith("test_manifest"):
        _patch_test_manifest_helper(module)
    if name.endswith("test_models"):
        _patch_test_models_helper(module)
