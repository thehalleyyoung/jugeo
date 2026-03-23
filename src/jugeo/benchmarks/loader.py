from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from .models import BenchmarkBundle, BugCase, EquivalenceCase, SpecCase
from .validation import validate_suite_payloads

ROOT = Path(__file__).resolve().parents[3]
TEST_EXAMPLES = ROOT / "test_examples"
EQUIVALENCE_SUITE = TEST_EXAMPLES / "equivalence_suite.json"
SPEC_SUITE = TEST_EXAMPLES / "spec_suite.json"
BUG_SUITE = TEST_EXAMPLES / "bug_suite.json"

T = TypeVar("T")


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    validate_suite_payloads({path: payload})
    return payload


def _load_suite(path: Path, factory: Callable[[dict[str, Any]], T]) -> tuple[T, ...]:
    payload = _load_payload(path)
    return tuple(factory(item) for item in payload["cases"])


def load_equivalence_suite() -> tuple[EquivalenceCase, ...]:
    return _load_suite(EQUIVALENCE_SUITE, EquivalenceCase.from_dict)


def load_spec_suite() -> tuple[SpecCase, ...]:
    return _load_suite(SPEC_SUITE, SpecCase.from_dict)


def load_bug_suite() -> tuple[BugCase, ...]:
    return _load_suite(BUG_SUITE, BugCase.from_dict)


def load_benchmark_bundle() -> BenchmarkBundle:
    return BenchmarkBundle(
        equivalence_cases=load_equivalence_suite(),
        spec_cases=load_spec_suite(),
        bug_cases=load_bug_suite(),
    )
