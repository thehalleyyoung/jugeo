from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.runtime_defaults import PolicyPreset, default_frontier_budget, default_runtime_options


def test_safe_preset_disables_silent_promotion() -> None:
    defaults = default_runtime_options(PolicyPreset.SAFE)
    assert defaults.trust_policy.silent_promotion_allowed is False
    assert defaults.copilot_channel_name.startswith('copilot-')
    assert default_frontier_budget(PolicyPreset.EXPLORATORY).max_parallel > defaults.frontier_budget.max_parallel
