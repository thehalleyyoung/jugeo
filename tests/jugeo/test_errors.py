from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.errors import FailureScope, JuGeoError, as_failure_payload, raise_with_scope


def test_raise_with_scope_preserves_payload() -> None:
    with pytest.raises(JuGeoError) as excinfo:
        raise_with_scope('bad overlap', scope=FailureScope.GEOMETRY, code='overlap-failure', details={'patch': 'u1'})
    payload = as_failure_payload(excinfo.value)
    assert payload['code'] == 'overlap-failure'
    assert payload['scope'] == 'geometry'
    assert payload['details']['patch'] == 'u1'
