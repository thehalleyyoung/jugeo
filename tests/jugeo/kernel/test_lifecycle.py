from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.kernel.lifecycle import LifecycleController, LifecycleState, advance_lifecycle, recover_from_failure


def test_lifecycle_recovery_requires_failure() -> None:
    controller = LifecycleController()
    advance_lifecycle(controller, LifecycleState.CONFIGURED, 'configure')
    advance_lifecycle(controller, LifecycleState.FAILED, 'fail')
    assert recover_from_failure(controller) is LifecycleState.REOPENED
