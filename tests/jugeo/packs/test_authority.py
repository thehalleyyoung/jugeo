from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.trust import TrustTier
from jugeo.packs.authority import PackAuthority, authorize_pack
from jugeo.packs.catalog import PackDescriptor


def test_pack_authority_checks_coordinate_and_tier() -> None:
    descriptor = PackDescriptor('core', '1.0')
    authority = PackAuthority('core', ('coord',), TrustTier.REVIEWED)
    assert authorize_pack(descriptor, authority, coordinate='coord', tier=TrustTier.PROPOSAL) is True
