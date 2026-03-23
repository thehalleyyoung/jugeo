"""Cross-module tests: runtime replay module."""
import pytest

try:
    from jugeo.runtime.cache import SemanticCache
    HAS_CACHE = True
except ImportError:
    HAS_CACHE = False

try:
    import jugeo.runtime.replay as replay_module
    HAS_REPLAY = True
except ImportError:
    HAS_REPLAY = False

@pytest.mark.skipif(not HAS_REPLAY, reason="runtime.replay not available")
def test_replay_module_importable():
    assert replay_module is not None

@pytest.mark.skipif(not HAS_CACHE, reason="runtime.cache not available")
def test_runtime_cache_importable():
    assert SemanticCache is not None

def test_runtime_defaults_importable():
    """Fallback: runtime_defaults always available."""
    try:
        from jugeo.runtime_defaults import get_defaults
        defaults = get_defaults()
        assert defaults is not None
    except ImportError as e:
        pytest.skip(f"runtime_defaults not available: {e}")
