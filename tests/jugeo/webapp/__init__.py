"""Test package scaffold that also exposes the shared JuGeo webapp sources."""
from __future__ import annotations

from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'src').exists())
SOURCE_PATH = ROOT / 'src' / 'jugeo' / 'webapp'
__path__.append(str(SOURCE_PATH))  # type: ignore[name-defined]
