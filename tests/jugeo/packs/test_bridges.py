from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.packs.bridges import PackBridge


def test_pack_bridge_connects_expected_packs() -> None:
    bridge = PackBridge('a', 'b', 'transport')
    assert bridge.connects('a', 'b') is True
