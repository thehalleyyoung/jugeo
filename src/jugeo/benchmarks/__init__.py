from __future__ import annotations

from typing import Any

from .loader import load_benchmark_bundle, load_bug_suite, load_equivalence_suite, load_spec_suite
from .validation import validate_suite_payloads


def run_all_benchmarks(*args: Any, **kwargs: Any):
    from .runner import run_all_benchmarks as _run_all_benchmarks

    return _run_all_benchmarks(*args, **kwargs)


def run_bug_benchmark(*args: Any, **kwargs: Any):
    from .runner import run_bug_benchmark as _run_bug_benchmark

    return _run_bug_benchmark(*args, **kwargs)


def run_equivalence_benchmark(*args: Any, **kwargs: Any):
    from .runner import run_equivalence_benchmark as _run_equivalence_benchmark

    return _run_equivalence_benchmark(*args, **kwargs)


def run_spec_benchmark(*args: Any, **kwargs: Any):
    from .runner import run_spec_benchmark as _run_spec_benchmark

    return _run_spec_benchmark(*args, **kwargs)

__all__ = [
    "load_benchmark_bundle",
    "load_bug_suite",
    "load_equivalence_suite",
    "load_spec_suite",
    "validate_suite_payloads",
    "run_all_benchmarks",
    "run_bug_benchmark",
    "run_equivalence_benchmark",
    "run_spec_benchmark",
]


# --- auto-registered submodules ---
try:
    from . import models
except Exception:
    pass
try:
    from . import semantics
except Exception:
    pass
