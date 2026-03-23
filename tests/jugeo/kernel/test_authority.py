from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.errors import JuGeoError
from jugeo.kernel.authority import AuthorityTier, DelegationRule, build_authority_center, validate_delegation_graph


def test_validate_delegation_graph_blocks_silent_promotion() -> None:
    source = build_authority_center('source', capabilities={'solve'}, trust_ceiling=AuthorityTier.PROPOSAL, delegations=[DelegationRule('target', frozenset({'solve'}), AuthorityTier.VERIFIED)])
    target = build_authority_center('target', capabilities={'solve'}, trust_ceiling=AuthorityTier.VERIFIED)
    with pytest.raises(JuGeoError):
        validate_delegation_graph([source, target])
