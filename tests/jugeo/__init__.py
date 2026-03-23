"""Test package — extends __path__ so ``import jugeo`` resolves via src/."""
from __future__ import annotations

from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'src').exists())
SOURCE_PATH = ROOT / 'src' / 'jugeo'
__path__.append(str(SOURCE_PATH))
