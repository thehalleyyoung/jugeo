"""Test package — extends __path__ so ``import gofai_chat`` resolves from repo root."""
from __future__ import annotations

from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'gofai_chat').exists())
SOURCE_PATH = ROOT / 'gofai_chat'
__path__.append(str(SOURCE_PATH))
