from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.interfaces.cli import main


def test_cli_returns_schema_payloads() -> None:
    code, payload = main(['judgment-schema'])
    assert code == 0
    assert payload['title'] == 'JuGeo Judgment Export'
