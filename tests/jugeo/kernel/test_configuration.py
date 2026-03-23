from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.errors import JuGeoError
from jugeo.kernel.configuration import ConfigSource, ConfigurationLayer, load_configuration


def test_configuration_merges_layers() -> None:
    layer = ConfigurationLayer('file', ConfigSource.FILE, {'kernel': {'threads': 3}})
    config = load_configuration(layer)
    assert config.get('kernel.threads') == 3


def test_configuration_rejects_silent_promotion() -> None:
    layer = ConfigurationLayer('override', ConfigSource.OVERRIDE, {'trust_policy': {'silent_promotion_allowed': True}})
    with pytest.raises(JuGeoError):
        load_configuration(layer)
