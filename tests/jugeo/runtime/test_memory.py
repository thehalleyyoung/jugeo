from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.runtime.memory import MemoryNote, SemanticMemory


def test_semantic_memory_searches_by_tag() -> None:
    memory = SemanticMemory()
    memory.remember(MemoryNote('k', 1, ('tag',)))
    assert memory.search_by_tag('tag')[0].key == 'k'
