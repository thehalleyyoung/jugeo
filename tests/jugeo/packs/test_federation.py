from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.packs.bridges import PackBridge
from jugeo.packs.catalog import PackCatalog, PackDescriptor
from jugeo.packs.federation import PackFederation


def test_pack_federation_merges_catalogs() -> None:
    left = PackCatalog({'a@1': PackDescriptor('a', '1')})
    right = PackCatalog({'b@1': PackDescriptor('b', '1')})
    federation = PackFederation((left, right), (PackBridge('a', 'b', 'thm'),))
    assert federation.merged_catalog().get('b@1') is not None
    assert federation.reachable_packs('a') == ('b',)
