from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.interfaces.serialization import deserialize, serialize


def test_serialize_is_canonical() -> None:
    payload = serialize({'b': 1, 'a': 2})
    assert payload == '{"a":2,"b":1}'
    assert deserialize(payload) == {'a': 2, 'b': 1}
