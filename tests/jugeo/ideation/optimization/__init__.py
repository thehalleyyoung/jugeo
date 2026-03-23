"""Tests for the jugeo.ideation.optimization package (Ch50)."""

from __future__ import annotations

from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "tests" / "jugeo").exists() and (parent / "src").exists()
)
TESTS_ROOT = ROOT / "tests" / "jugeo"
RELATIVE = Path(__file__).resolve().parent.relative_to(TESTS_ROOT)
SOURCE_PATH = ROOT / "src" / "jugeo" / RELATIVE

if SOURCE_PATH.exists():
    __path__.append(str(SOURCE_PATH))
