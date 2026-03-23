"""Cross-module tests: cli_detect() exit code interface using a temp file."""
import pytest
import tempfile
import os

try:
    from jugeo.problem_modes.bug_detection import cli_detect
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

VALID_PYTHON = """
def foo(x: int) -> int:
    return x * 2

result = foo(5)
"""

def test_cli_detect_on_valid_file_returns_int():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(VALID_PYTHON)
        tmp_path = f.name
    try:
        exit_code = cli_detect(tmp_path)
        assert isinstance(exit_code, int)
    finally:
        os.unlink(tmp_path)

def test_cli_detect_exit_code_is_valid():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(VALID_PYTHON)
        tmp_path = f.name
    try:
        exit_code = cli_detect(tmp_path)
        assert exit_code in (0, 1, 2)
    finally:
        os.unlink(tmp_path)
