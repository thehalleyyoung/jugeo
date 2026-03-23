from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.trust import TrustTier
from jugeo.packs.authority import PackAuthority
from jugeo.packs.catalog import PackCatalog, PackDescriptor
from jugeo.packs.loading import PackLoadRequest, load_pack


def test_load_pack_respects_catalog_and_authority() -> None:
    catalog = PackCatalog({'core@1.0': PackDescriptor('core', '1.0')})
    authority = PackAuthority('core', ('coord',), TrustTier.REVIEWED)
    result = load_pack(catalog, authority, PackLoadRequest('core@1.0', 'coord', TrustTier.PROPOSAL))
    assert result.loaded is True
