"""Conftest for gofai_chat tests — ensures repo root is on sys.path."""
from __future__ import annotations

import sys
from pathlib import Path

# tests/gofai_chat/ → tests/ → repo root
ROOT = Path(__file__).resolve().parent.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
