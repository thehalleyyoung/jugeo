from __future__ import annotations

from jugeo.easy import spec


def _runtime_payload(spec_program: str, input_cover: list[dict]) -> dict:
    return {
        "description": "runtime declared-cover check",
        "entrypoint": "solve",
        "spec_function": "spec",
        "spec_program": spec_program,
        "input_cover": input_cover,
    }


def test_easy_spec_accepts_runtime_witnesses() -> None:
    source = """
def solve(n):
    return n * 2
"""
    payload = _runtime_payload(
        """
def spec(result, n):
    return result == n * 2
""",
        [{"args": [n], "kwargs": {}} for n in range(10)],
    )

    result = spec(source, payload)

    assert result.satisfied is True
    assert result.mode == "runtime-declared-cover"
    assert result.witness_count == 0
    assert len(result.clauses) == 10


def test_easy_spec_reports_cover_witness_for_semantic_bug() -> None:
    source = """
def solve(n):
    return n + 1
"""
    payload = _runtime_payload(
        """
def spec(result, n):
    return result == n + 2
""",
        [{"args": [n], "kwargs": {}} for n in range(10)],
    )

    result = spec(source, payload)

    assert result.satisfied is False
    assert result.mode == "runtime-declared-cover"
    assert result.witness_count > 0
    assert any("returned False" in obstruction for obstruction in result.obstructions)
