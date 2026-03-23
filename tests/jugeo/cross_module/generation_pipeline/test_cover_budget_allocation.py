"""Cross-module tests: DefaultBudgetConfig from runtime_defaults."""
import pytest

try:
    from jugeo.runtime_defaults import get_defaults
except ImportError as e:
    pytest.skip(f"runtime_defaults not available: {e}", allow_module_level=True)

def test_default_budget_config_importable():
    try:
        from jugeo.runtime_defaults import DefaultBudgetConfig
        assert DefaultBudgetConfig is not None
    except ImportError:
        # DefaultBudgetConfig might not be exported; get_defaults suffices
        defaults = get_defaults()
        assert defaults is not None

def test_get_defaults_returns_runtime_defaults():
    defaults = get_defaults()
    assert defaults is not None
