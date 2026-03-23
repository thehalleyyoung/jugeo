"""Cross-module tests: PEP 484 annotations → parsed obligations."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        spec_from_annotations, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

ANNOTATED_SOURCE = """
def add(x: int, y: int) -> int:
    return x + y

def greet(name: str) -> str:
    return f"Hello, {name}"

def process(items: list[int]) -> list[int]:
    return [i * 2 for i in items]
"""

def test_spec_from_annotations_fn():
    spec = spec_from_annotations(ANNOTATED_SOURCE)
    assert isinstance(spec, ParsedSpecification)

def test_annotations_spec_format():
    spec = spec_from_annotations(ANNOTATED_SOURCE)
    assert spec.format == SpecFormat.PYTHON_ANNOTATIONS

def test_annotations_spec_has_obligations():
    spec = spec_from_annotations(ANNOTATED_SOURCE)
    assert isinstance(spec.obligations, tuple)
