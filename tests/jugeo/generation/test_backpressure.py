from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.generation.backpressure import BackpressureLevel, compute_backpressure
from jugeo.generation.integration import IntegrationPlan


def test_backpressure_throttles_when_blocked() -> None:
    signal = compute_backpressure(IntegrationPlan((), (), ('blocked', 'still blocked')))
    assert signal.level is BackpressureLevel.THROTTLE
