from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jugeo.benchmarks.validation import validate_suite_payloads

ROOT = Path(__file__).resolve().parent
SUITE_SCHEMA_VERSION = 1
DECLARED_COVER_POINTS = 10


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip() + "\n"


def _with_bug_scaffold(program: str, *, family: str, index: int) -> str:
    scaffold = _dedent(
        f"""
        def _bug_{family}_coordinate_{index}():
            return "bug.{family}.{index:02d}"

        def _bug_{family}_coerce_{index}(value):
            if isinstance(value, bool):
                return int(value)
            return value

        def _bug_{family}_snapshot_{index}(values):
            copied = []
            for value in values:
                copied.append(_bug_{family}_coerce_{index}(value))
            return tuple(copied)

        def _bug_{family}_support_{index}(values):
            support = []
            for offset, value in enumerate(_bug_{family}_snapshot_{index}(values)):
                support.append((_bug_{family}_coordinate_{index}(), offset, value))
            return tuple(support)

        def _bug_{family}_descent_profile_{index}(values):
            profile = []
            for coordinate, offset, value in _bug_{family}_support_{index}(values):
                profile.append((coordinate, offset % 3, value))
            return tuple(profile)
        """
    )
    return scaffold + "\n" + _dedent(program)


def _with_semantic_scaffold(program: str, *, suite: str, family: str, index: int, role: str) -> str:
    scaffold = _dedent(
        f"""
        def _{suite}_{family}_{role}_{index}_coordinate():
            return "{suite}.{family}.{role}.{index:02d}"

        def _{suite}_{family}_{role}_{index}_coerce(value):
            if isinstance(value, bool):
                return int(value)
            return value

        def _{suite}_{family}_{role}_{index}_snapshot(values):
            copied = []
            for value in values:
                copied.append(_{suite}_{family}_{role}_{index}_coerce(value))
            return tuple(copied)

        def _{suite}_{family}_{role}_{index}_support(values):
            support = []
            for offset, value in enumerate(_{suite}_{family}_{role}_{index}_snapshot(values)):
                support.append((_{suite}_{family}_{role}_{index}_coordinate(), offset, value))
            return tuple(support)

        def _{suite}_{family}_{role}_{index}_descent_profile(values):
            profile = []
            for coordinate, offset, value in _{suite}_{family}_{role}_{index}_support(values):
                profile.append((coordinate, offset % 2, value))
            return tuple(profile)

        def _{suite}_{family}_{role}_{index}_marker(*values):
            return ({index}, len(values), len(_{suite}_{family}_{role}_{index}_descent_profile(values)))
        """
    )
    return scaffold + "\n" + _dedent(program)


def _point(*args, **kwargs):
    return {"args": list(args), "kwargs": kwargs}


def _mutate_cover_value(value: object, *, offset: int) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value + offset
    if isinstance(value, float):
        return value + (offset / 10.0)
    if isinstance(value, str):
        return f"{value} extra{offset}"
    if isinstance(value, list):
        return [_mutate_cover_value(item, offset=offset + index + 1) for index, item in enumerate(value)]
    if isinstance(value, dict):
        return {
            key: _mutate_cover_value(item, offset=offset + index + 1)
            for index, (key, item) in enumerate(value.items())
        }
    return value


def _mutate_cover_point(point: dict[str, object], *, offset: int) -> dict[str, object]:
    return {
        "args": [_mutate_cover_value(arg, offset=offset + index + 1) for index, arg in enumerate(point.get("args", []))],
        "kwargs": {
            key: _mutate_cover_value(value, offset=offset + index + 1)
            for index, (key, value) in enumerate(point.get("kwargs", {}).items())
        },
    }


def _vary_cover(index: int, *points: dict[str, object]) -> list[dict[str, object]]:
    cover: list[dict[str, object]] = []
    seen: set[str] = set()
    base = [json.loads(json.dumps(point)) for point in points]

    def add(candidate: dict[str, object]) -> bool:
        signature = json.dumps(candidate, sort_keys=True)
        if signature in seen:
            return False
        seen.add(signature)
        cover.append(candidate)
        return True

    for point in base:
        add(point)

    variant_index = 0
    while len(cover) < DECLARED_COVER_POINTS:
        seed = base[(index + variant_index) % len(base)]
        offset = index + variant_index + 1
        candidate = _mutate_cover_point(seed, offset=offset)
        if not add(candidate):
            fallback = _mutate_cover_point(seed, offset=offset + DECLARED_COVER_POINTS)
            add(fallback)
        variant_index += 1

    return cover


def _iter_family_indices(families: list, total_variants: int = 50):
    base, remainder = divmod(total_variants, len(families))
    next_index = 0
    for family_offset, family in enumerate(families):
        count = base + (1 if family_offset < remainder else 0)
        for _ in range(count):
            yield family, next_index
            next_index += 1


def _case_family(case_id: str) -> str:
    return case_id.split("-")[1]


def _program_lines(source: str) -> int:
    return source.count("\n")


def _suite_metadata(
    *,
    suite: str,
    cases: list[dict[str, object]],
    benchmark_semantics: str,
    composition: dict[str, object],
    longish_programs: dict[str, int],
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    metadata = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "theory_source": "preliminaries/theory2.tex",
        "benchmark_semantics": benchmark_semantics,
        "family_count": len({_case_family(str(case["case_id"])) for case in cases}),
        "families": sorted({_case_family(str(case["case_id"])) for case in cases}),
        "composition": composition,
        "longish_programs": longish_programs,
    }
    if extra:
        metadata.update(extra)
    return {"suite": suite, "metadata": metadata, "cases": cases}


def _decorate_equivalence_case(case: dict[str, object]) -> dict[str, object]:
    family = _case_family(str(case["case_id"]))
    index = str(case["case_id"]).rsplit("-", 1)[-1]
    case["left_program"] = _with_semantic_scaffold(
        str(case["left_program"]),
        suite="equivalence",
        family=family,
        index=int(index),
        role="left",
    )
    case["right_program"] = _with_semantic_scaffold(
        str(case["right_program"]),
        suite="equivalence",
        family=family,
        index=int(index),
        role="right",
    )
    return case


def _decorate_spec_case(case: dict[str, object]) -> dict[str, object]:
    family = _case_family(str(case["case_id"]))
    index = str(case["case_id"]).rsplit("-", 1)[-1]
    case["program"] = _with_semantic_scaffold(
        str(case["program"]),
        suite="spec",
        family=family,
        index=int(index),
        role="program",
    )
    case["spec_program"] = _with_semantic_scaffold(
        str(case["spec_program"]),
        suite="spec",
        family=family,
        index=int(index),
        role="spec",
    )
    return case


def _equivalence_family_affine(index: int, equivalent: bool) -> dict[str, object]:
    bias = (index % 5) - 2
    mod = 3 + (index % 2)
    keep = index % mod
    cover = _vary_cover(
        index,
        _point([index - 3, -1, 0, 1, 4 + index], bias, mod, keep),
        _point([keep, keep + mod, keep - mod, keep + 2 * mod], bias, mod, keep),
        _point([7, 8, 9, 10 + index], bias, mod, keep),
        _point([keep - 2 * mod, keep - mod, keep, keep + 3 * mod], bias, mod, keep),
        _point([index + 11, index + 12, index + 13, keep], bias=bias, mod=mod, keep=keep),
    )
    left = _dedent(
        f"""
        def _normalize(values):
            normalized = []
            for value in values:
                normalized.append(int(value))
            return normalized

        def _eligible(value, mod, keep):
            return value % mod == keep

        def solve(values, bias, mod, keep):
            total = 0
            cleaned = _normalize(values)
            for value in cleaned:
                if _eligible(value, mod, keep):
                    total += value + bias
            return total
        """
    )
    right = _dedent(
        f"""
        def _candidate_terms(values, bias, mod, keep):
            items = []
            for value in values:
                value = int(value)
                if value % mod == keep:
                    items.append(value + bias)
            return items

        def solve(values, bias, mod, keep):
            total = 0
            for term in _candidate_terms(values, bias, mod, keep):
                total += term
            return total
        """
        if equivalent
        else f"""
        def _candidate_terms(values, bias, mod, keep):
            items = []
            for value in values:
                value = int(value)
                if value % mod == keep:
                    items.append(value + bias + 1)
            return items

        def solve(values, bias, mod, keep):
            total = 0
            for term in _candidate_terms(values, bias, mod, keep):
                total += term
            return total
        """
    )
    return {
        "case_id": f"eq-affine-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Affine filtered sum under an explicit finite cover.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_gaps(index: int, equivalent: bool) -> dict[str, object]:
    limit = 2 + (index % 4)
    cover = _vary_cover(
        index,
        _point([0, limit, limit + 3, limit + 6]),
        _point([1, 1 + limit, 1 + 2 * limit, 10]),
        _point([2, 2 + limit - 1, 2 + limit, 30], limit=limit),
        _point([limit - 1, limit, limit + 1, limit + 4], limit=limit),
        _point([5, 5 + limit, 5 + 2 * limit, 5 + 3], limit=limit),
    )
    left = _dedent(
        """
        def _ordered_unique(values):
            return sorted({int(value) for value in values})

        def solve(values, limit=3):
            ordered = _ordered_unique(values)
            total = 0
            previous = None
            for value in ordered:
                if previous is not None:
                    gap = value - previous
                    if gap <= limit:
                        total += gap
                previous = value
            return total
        """
    )
    right = _dedent(
        """
        def _gap_values(values):
            ordered = sorted({int(value) for value in values})
            return tuple(zip(ordered, ordered[1:]))

        def solve(values, limit=3):
            gaps = []
            for left, right in _gap_values(values):
                gap = right - left
                if gap <= limit:
                    gaps.append(gap)
            return sum(gaps)
        """
        if equivalent
        else """
        def _gap_values(values):
            ordered = sorted({int(value) for value in values})
            return tuple(zip(ordered, ordered[1:]))

        def solve(values, limit=3):
            gaps = []
            for left, right in _gap_values(values):
                gap = right - left
                if gap < limit:
                    gaps.append(gap)
            return sum(gaps)
        """
    )
    return {
        "case_id": f"eq-gaps-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Adjacent-gap score with a cover that exercises the limit boundary.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_streak(index: int, equivalent: bool) -> dict[str, object]:
    threshold = 1 + (index % 4)
    cover = _vary_cover(
        index,
        _point([threshold, threshold, threshold - 1, threshold, threshold]),
        _point([0, threshold + 1, threshold + 2, 0, threshold], threshold),
        _point([threshold - 1, threshold, threshold + 1, threshold + 2], threshold=threshold),
        _point([threshold + 2, threshold + 2, threshold - 2, threshold + 3], threshold=threshold),
        _point([threshold - 3, threshold, threshold, threshold, threshold - 1], threshold),
    )
    left = _dedent(
        """
        def _passes(value, threshold):
            return value >= threshold

        def solve(values, threshold=1):
            best = 0
            current = 0
            for value in values:
                if _passes(value, threshold):
                    current += 1
                    if current > best:
                        best = current
                else:
                    current = 0
            return best
        """
    )
    right = _dedent(
        """
        def _passes(value, threshold):
            return value >= threshold

        def solve(values, threshold=1):
            best = 0
            current = 0
            for value in values:
                passes = _passes(value, threshold)
                if passes:
                    current = current + 1
                    best = best if best > current else current
                else:
                    current = 0
            return best
        """
        if equivalent
        else """
        def _passes(value, threshold):
            return value > threshold

        def solve(values, threshold=1):
            best = 0
            current = 0
            for value in values:
                passes = _passes(value, threshold)
                if passes:
                    current = current + 1
                    best = best if best > current else current
                else:
                    current = 0
            return best
        """
    )
    return {
        "case_id": f"eq-streak-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Longest streak above a threshold on a finite cover.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_matrix(index: int, equivalent: bool) -> dict[str, object]:
    shift = (index % 3) + 1
    cover = _vary_cover(
        index,
        _point([[1, 2, 3], [4, 5], [6]], shift),
        _point([[index, index + 1], [index + 2, index + 3], [index + 4]], shift),
        _point([[9], [8, 7, 6], [5, 4]], shift=shift),
        _point([[shift, shift + 1], [shift + 2], [shift + 3, shift + 4, shift + 5]], shift=shift),
        _point([[index + 5, index + 6, index + 7], [index + 8], [index + 9, index + 10]], shift),
    )
    left = _dedent(
        """
        def _diagonal_value(row, row_index):
            return row[row_index] if row_index < len(row) else 0

        def _row_contribution(row, row_index, shift):
            return _diagonal_value(row, row_index) + shift

        def solve(matrix, shift=1):
            total = 0
            for row_index, row in enumerate(matrix):
                total += _row_contribution(row, row_index, shift)
            return total
        """
    )
    right = _dedent(
        """
        def _diagonal_or_shift(matrix, shift):
            values = []
            for row_index, row in enumerate(matrix):
                values.append(row[row_index] if row_index < len(row) else 0)
            return values

        def solve(matrix, shift=1):
            total = 0
            for value in _diagonal_or_shift(matrix, shift):
                total += value + shift
            return total
        """
        if equivalent
        else """
        def _diagonal_or_shift(matrix, shift):
            values = []
            for row_index, row in enumerate(matrix):
                if row_index + 1 < len(row):
                    values.append(row[row_index + 1])
                else:
                    values.append(0)
            return values

        def solve(matrix, shift=1):
            total = 0
            for value in _diagonal_or_shift(matrix, shift):
                total += value + shift
            return total
        """
    )
    return {
        "case_id": f"eq-matrix-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Diagonal accumulation relative to the explicit input cover.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_words(index: int, equivalent: bool) -> dict[str, object]:
    minimum = 3 + (index % 2)
    cover = _vary_cover(
        index,
        _point("Alpha beta ALPHA gamma! delta??", minimum),
        _point("red blue blue green", minimum),
        _point("odd even EVEN tally", minimum=minimum),
        _point("zeta zeta eta! theta theta", minimum=minimum),
        _point("mix-and-match words, words, words", minimum),
    )
    left = _dedent(
        """
        def _normalized_words(text):
            cleaned = []
            for raw in text.split():
                letters = ''.join(ch for ch in raw.lower() if ch.isalpha())
                if letters:
                    cleaned.append(letters)
            return cleaned

        def solve(text, minimum=3):
            words = []
            for word in _normalized_words(text):
                if len(word) >= minimum and word not in words:
                    words.append(word)
            words.sort()
            return '|'.join(words)
        """
    )
    right = _dedent(
        """
        def _normalized_set(text):
            return {
                ''.join(ch for ch in raw.lower() if ch.isalpha())
                for raw in text.split()
            }

        def solve(text, minimum=3):
            normalized = _normalized_set(text)
            words = [word for word in normalized if len(word) >= minimum and word]
            return '|'.join(sorted(words))
        """
        if equivalent
        else """
        def _normalized_set(text):
            return {
                ''.join(ch for ch in raw.lower() if ch.isalpha())
                for raw in text.split()
            }

        def solve(text, minimum=3):
            normalized = _normalized_set(text)
            words = [word for word in normalized if len(word) > minimum and word]
            return '|'.join(sorted(words))
        """
    )
    return {
        "case_id": f"eq-words-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Word-signature normalization with an explicit cover and relation family.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_guard(index: int, equivalent: bool) -> dict[str, object]:
    guard = (index % 5) - 2
    cover = _vary_cover(
        index,
        _point([guard, guard + 1, guard + 3], guard),
        _point([guard - 2, guard, guard + 4], guard=guard),
        _point([10, guard, -10], guard),
        _point([guard + 5, guard - 5, guard + 2], guard=guard),
        _point([guard, guard + 6, guard + 7], guard),
    )
    left = _dedent(
        """
        def _step(value, guard):
            if int(value) == guard:
                raise ValueError(f'guard {guard} triggered')
            return abs(int(value) - guard)

        def solve(values, guard=0):
            total = 0
            for value in values:
                total += _step(value, guard)
            return total
        """
    )
    right = _dedent(
        """
        def _terms(values, guard):
            pieces = []
            for value in values:
                value = int(value)
                if value == guard:
                    raise ValueError(f'guard {guard} triggered')
                pieces.append(abs(value - guard))
            return pieces

        def solve(values, guard=0):
            total = 0
            for piece in _terms(values, guard):
                total += piece
            return total
        """
        if equivalent
        else """
        def _terms(values, guard):
            pieces = []
            for value in values:
                value = int(value)
                if value == guard:
                    raise RuntimeError(f'guard {guard} triggered')
                pieces.append(abs(value - guard))
            return pieces

        def solve(values, guard=0):
            total = 0
            for piece in _terms(values, guard):
                total += piece
            return total
        """
    )
    return {
        "case_id": f"eq-guard-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Guarded extensional equality with matching or mismatching exception behavior on the declared cover.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_records(index: int, equivalent: bool) -> dict[str, object]:
    minimum = 5 + (index % 3)
    cover = _vary_cover(
        index,
        _point(
            [{"name": "ada", "score": minimum}, {"name": "bea", "score": minimum + 1}, {"name": "cy", "score": minimum - 1}],
            minimum,
        ),
        _point(
            [{"name": "dio", "score": minimum + 3}, {"name": "eve", "score": minimum}, {"name": "flo", "score": minimum - 2}],
            minimum=minimum,
        ),
        _point(
            [{"name": "gia", "score": minimum - 1}, {"name": "hal", "score": minimum + 4}, {"name": "ivy", "score": minimum + 1}],
            minimum,
        ),
        _point(
            [{"name": "jo", "score": minimum + 2}, {"name": "kai", "score": minimum - 3}, {"name": "lou", "score": minimum}],
            minimum=minimum,
        ),
        _point(
            [{"name": "mia", "score": minimum + 5}, {"name": "ned", "score": minimum - 1}, {"name": "ola", "score": minimum + 2}],
            minimum,
        ),
    )
    left = _dedent(
        """
        def _selected_names(records, minimum):
            names = []
            for record in records:
                if int(record['score']) >= minimum:
                    names.append(record['name'].upper())
            names.sort()
            return tuple(names)

        def solve(records, minimum=5):
            return _selected_names(records, minimum)
        """
    )
    right = _dedent(
        """
        def _selected_names(records, minimum):
            names = []
            for record in records:
                score = int(record['score'])
                if score >= minimum:
                    names.append(record['name'].upper())
            names.sort()
            return tuple(names)

        def solve(records, minimum=5):
            return _selected_names(records, minimum)
        """
        if equivalent
        else """
        def _selected_names(records, minimum):
            names = []
            for record in records:
                score = int(record['score'])
                if score > minimum:
                    names.append(record['name'].upper())
            names.sort()
            return tuple(names)

        def solve(records, minimum=5):
            return _selected_names(records, minimum)
        """
    )
    return {
        "case_id": f"eq-records-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Finite-support extensional equality over structured record summaries.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _equivalence_family_mutation(index: int, equivalent: bool) -> dict[str, object]:
    bias = (index % 4) - 1
    cover = _vary_cover(
        index,
        _point(
            [{"values": [index, index + 1]}, {"values": [2, 3, 4]}],
            bias=bias,
        ),
        _point(
            [{"values": [-2, 5]}, {"values": [index + 2, index + 3, 1]}],
            bias=bias + 1,
        ),
        _point(
            [{"values": [7, 8, 9]}, {"values": [bias, bias + 2]}],
            bias=bias,
        ),
        _point(
            [{"values": [3, 3, 3]}, {"values": [index - 1, index, index + 1]}],
            bias=bias - 1,
        ),
        _point(
            [{"values": [10, -4]}, {"values": [6, 1, 0, index]}],
            bias=bias + 2,
        ),
    )
    left = _dedent(
        """
        def _drain(entry):
            total = 0
            values = entry['values']
            while values:
                total += int(values.pop(0))
            return total

        def solve(rows, bias=0):
            total = 0
            for entry in rows:
                total += _drain(entry) + bias
            return total
        """
    )
    right = _dedent(
        """
        def _drain(entry):
            total = 0
            values = entry['values']
            while values:
                total += int(values.pop())
            return total

        def solve(rows, bias=0):
            pieces = []
            for entry in rows:
                pieces.append(_drain(entry) + bias)
            return sum(pieces)
        """
        if equivalent
        else """
        def _drain(entry):
            total = 0
            values = entry['values']
            while values:
                total += int(values.pop())
            return total

        def solve(rows, bias=0):
            pieces = []
            for entry in rows:
                pieces.append(_drain(entry))
            return sum(pieces) + bias
        """
    )
    return {
        "case_id": f"eq-mutation-{'eq' if equivalent else 'neq'}-{index:02d}",
        "description": "Declared-cover extensional equality over nested mutable inputs that must be evaluated in isolation.",
        "relation_family": "extensional-equality-on-declared-cover",
        "left_program": left,
        "right_program": right,
        "input_cover": cover,
        "expected_equivalent": equivalent,
    }


def _spec_family_affine(index: int, satisfies: bool) -> dict[str, object]:
    bias = (index % 5) - 2
    mod = 3 + (index % 2)
    keep = index % mod
    program = _dedent(
        f"""
        def _eligible(value, mod, keep):
            return int(value) % mod == keep

        def solve(values, bias, mod, keep):
            total = 0
            for value in values:
                if _eligible(value, mod, keep):
                    total += int(value) + bias
            return total
        """
        if satisfies
        else f"""
        def _eligible(value, mod, keep):
            return int(value) % mod == keep

        def solve(values, bias, mod, keep):
            total = 0
            for value in values:
                if _eligible(value, mod, keep):
                    total += int(value) + bias + 1
            return total
        """
    )
    spec = _dedent(
        """
        def _expected_total(values, bias, mod, keep):
            total = 0
            for value in values:
                value = int(value)
                if value % mod == keep:
                    total += value + bias
            return total

        def spec(result, values, bias, mod, keep):
            return isinstance(result, int) and result == _expected_total(values, bias, mod, keep)
        """
    )
    cover = _vary_cover(
        index,
        _point([index - 2, keep, keep + mod, keep + 2 * mod], bias, mod, keep),
        _point([0, 1, 2, 3, 4, 5], bias, mod, keep),
        _point([-3, -2, -1, 0, 1], bias, mod, keep),
        _point([keep - mod, keep, keep + mod, keep + 3 * mod], bias=bias, mod=mod, keep=keep),
        _point([index + 6, index + 7, keep, keep + 4 * mod], bias, mod, keep),
    )
    return {
        "case_id": f"spec-affine-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks an affine filtered sum over a finite cover.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_gaps(index: int, satisfies: bool) -> dict[str, object]:
    program = _dedent(
        """
        def _gap_pairs(values):
            ordered = sorted({int(value) for value in values})
            return tuple(zip(ordered, ordered[1:]))

        def solve(values, limit=3):
            total = 0
            for left, right in _gap_pairs(values):
                gap = right - left
                if gap <= limit:
                    total += gap
            return total
        """
        if satisfies
        else """
        def _gap_pairs(values):
            ordered = sorted({int(value) for value in values})
            return tuple(zip(ordered, ordered[1:]))

        def solve(values, limit=3):
            total = 0
            for left, right in _gap_pairs(values):
                gap = right - left
                if gap < limit:
                    total += gap
            return total
        """
    )
    spec = _dedent(
        """
        def spec(result, values, limit=3):
            ordered = sorted({int(value) for value in values})
            expected = 0
            for left, right in zip(ordered, ordered[1:]):
                gap = right - left
                if gap <= limit:
                    expected += gap
            return result == expected and result >= 0
        """
    )
    limit = 2 + (index % 4)
    cover = _vary_cover(
        index,
        _point([0, limit, limit + 3], limit=limit),
        _point([2, 2 + limit, 8], limit=limit),
        _point([4, 4 + limit - 1, 4 + limit], limit=limit),
        _point([6, 6 + limit, 6 + limit + 2], limit=limit),
        _point([1, 1 + limit - 1, 1 + limit, 1 + 2 * limit], limit=limit),
    )
    return {
        "case_id": f"spec-gaps-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks a bounded adjacent-gap sum.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_streak(index: int, satisfies: bool) -> dict[str, object]:
    program = _dedent(
        """
        def _passes(value, threshold):
            return value >= threshold

        def solve(values, threshold=1):
            best = 0
            current = 0
            for value in values:
                if _passes(value, threshold):
                    current += 1
                    best = best if best > current else current
                else:
                    current = 0
            return best
        """
        if satisfies
        else """
        def _passes(value, threshold):
            return value > threshold

        def solve(values, threshold=1):
            best = 0
            current = 0
            for value in values:
                if _passes(value, threshold):
                    current += 1
                    best = best if best > current else current
                else:
                    current = 0
            return best
        """
    )
    spec = _dedent(
        """
        def spec(result, values, threshold=1):
            best = 0
            current = 0
            for value in values:
                if value >= threshold:
                    current += 1
                    best = best if best > current else current
                else:
                    current = 0
            return result == best
        """
    )
    threshold = 1 + (index % 4)
    cover = _vary_cover(
        index,
        _point([threshold, threshold, threshold - 1, threshold], threshold),
        _point([0, threshold + 1, threshold + 2, 0], threshold),
        _point([threshold - 1, threshold, threshold + 1], threshold),
        _point([threshold + 2, threshold + 2, threshold, threshold - 2], threshold=threshold),
        _point([threshold - 3, threshold - 1, threshold, threshold], threshold),
    )
    return {
        "case_id": f"spec-streak-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks the longest threshold streak exactly.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_matrix(index: int, satisfies: bool) -> dict[str, object]:
    program = _dedent(
        """
        def _diagonal_value(row, row_index):
            return row[row_index] if row_index < len(row) else 0

        def _row_contribution(row, row_index, shift):
            return _diagonal_value(row, row_index) + shift

        def solve(matrix, shift=1):
            total = 0
            for row_index, row in enumerate(matrix):
                total += _row_contribution(row, row_index, shift)
            return total
        """
        if satisfies
        else """
        def _diagonal_value(row, row_index):
            return row[row_index + 1] if row_index + 1 < len(row) else 0

        def _row_contribution(row, row_index, shift):
            return _diagonal_value(row, row_index) + shift

        def solve(matrix, shift=1):
            total = 0
            for row_index, row in enumerate(matrix):
                total += _row_contribution(row, row_index, shift)
            return total
        """
    )
    spec = _dedent(
        """
        def spec(result, matrix, shift=1):
            total = 0
            for row_index, row in enumerate(matrix):
                if row_index < len(row):
                    total += row[row_index] + shift
                else:
                    total += shift
            return result == total
        """
    )
    shift = (index % 3) + 1
    cover = _vary_cover(
        index,
        _point([[1, 2, 3], [4, 5], [6]], shift),
        _point([[index, index + 1], [index + 2, index + 3], [index + 4]], shift),
        _point([[9], [8, 7, 6], [5, 4]], shift),
        _point([[shift, shift + 1], [shift + 2], [shift + 3, shift + 4]], shift=shift),
        _point([[index + 5, index + 6], [index + 7, index + 8], [index + 9]], shift),
    )
    return {
        "case_id": f"spec-matrix-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks diagonal accumulation with shift.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_words(index: int, satisfies: bool) -> dict[str, object]:
    program = _dedent(
        """
        def solve(text, minimum=3):
            normalized = []
            for raw in text.split():
                word = ''.join(ch for ch in raw.lower() if ch.isalpha())
                if word and len(word) >= minimum and word not in normalized:
                    normalized.append(word)
            normalized.sort()
            return '|'.join(normalized)
        """
        if satisfies
        else """
        def solve(text, minimum=3):
            normalized = []
            for raw in text.split():
                word = ''.join(ch for ch in raw.lower() if ch.isalpha())
                if word and len(word) > minimum and word not in normalized:
                    normalized.append(word)
            normalized.sort()
            return '|'.join(normalized)
        """
    )
    spec = _dedent(
        """
        def spec(result, text, minimum=3):
            normalized = sorted({
                ''.join(ch for ch in raw.lower() if ch.isalpha())
                for raw in text.split()
                if ''.join(ch for ch in raw.lower() if ch.isalpha())
            })
            normalized = [word for word in normalized if len(word) >= minimum]
            return result == '|'.join(normalized)
        """
    )
    minimum = 3 + (index % 2)
    cover = _vary_cover(
        index,
        _point("Alpha beta ALPHA gamma! delta??", minimum),
        _point("red blue blue green", minimum),
        _point("odd even EVEN tally", minimum),
        _point("zeta eta eta theta", minimum=minimum),
        _point("tiny BIG bigger biggest", minimum),
    )
    return {
        "case_id": f"spec-words-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks normalized unique word signatures.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_records(index: int, satisfies: bool) -> dict[str, object]:
    minimum = 5 + (index % 3)
    program = _dedent(
        """
        def solve(records, minimum=5):
            passed = 0
            total = 0
            names = []
            for record in records:
                score = int(record['score'])
                total += score
                if score >= minimum:
                    passed += 1
                    names.append(record['name'].upper())
            names.sort()
            return {'passed': passed, 'total': total, 'names': tuple(names)}
        """
        if satisfies
        else """
        def solve(records, minimum=5):
            passed = 0
            total = 0
            names = []
            for record in records:
                score = int(record['score'])
                total += score
                if score > minimum:
                    passed += 1
                    names.append(record['name'].upper())
            names.sort()
            return {'passed': passed, 'total': total, 'names': tuple(names)}
        """
    )
    spec = _dedent(
        """
        def _expected(records, minimum):
            passed = 0
            total = 0
            names = []
            for record in records:
                score = int(record['score'])
                total += score
                if score >= minimum:
                    passed += 1
                    names.append(record['name'].upper())
            names.sort()
            return {'passed': passed, 'total': total, 'names': tuple(names)}

        def spec(result, records, minimum=5):
            return isinstance(result, dict) and result == _expected(records, minimum)
        """
    )
    cover = _vary_cover(
        index,
        _point(
            [{"name": "ada", "score": minimum}, {"name": "bea", "score": minimum + 2}, {"name": "cy", "score": minimum - 1}],
            minimum,
        ),
        _point(
            [{"name": "dio", "score": minimum + 3}, {"name": "eve", "score": minimum}, {"name": "flo", "score": minimum - 2}],
            minimum=minimum,
        ),
        _point(
            [{"name": "gia", "score": minimum - 1}, {"name": "hal", "score": minimum + 4}, {"name": "ivy", "score": minimum + 1}],
            minimum,
        ),
        _point(
            [{"name": "jo", "score": minimum + 1}, {"name": "kai", "score": minimum - 3}, {"name": "lou", "score": minimum + 2}],
            minimum=minimum,
        ),
        _point(
            [{"name": "mia", "score": minimum + 5}, {"name": "ned", "score": minimum}, {"name": "ola", "score": minimum - 2}],
            minimum,
        ),
    )
    return {
        "case_id": f"spec-records-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks structured record summaries over the declared finite cover.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_mutation(index: int, satisfies: bool) -> dict[str, object]:
    bias = (index % 4) - 1
    program = _dedent(
        """
        def _drain(entry):
            total = 0
            values = entry['values']
            while values:
                total += int(values.pop(0))
            return total

        def solve(rows, bias=0):
            total = 0
            for entry in rows:
                total += _drain(entry) + bias
            return total
        """
        if satisfies
        else """
        def _drain(entry):
            total = 0
            values = entry['values']
            while values:
                total += int(values.pop(0))
            return total

        def solve(rows, bias=0):
            total = 0
            for entry in rows:
                total += _drain(entry)
            return total + bias
        """
    )
    spec = _dedent(
        """
        def _expected_total(rows, bias):
            total = 0
            for entry in rows:
                row_total = 0
                for value in entry['values']:
                    row_total += int(value)
                total += row_total + bias
            return total

        def spec(result, rows, bias=0):
            return isinstance(result, int) and result == _expected_total(rows, bias)
        """
    )
    cover = _vary_cover(
        index,
        _point(
            [{"values": [index, index + 1]}, {"values": [2, 3, 4]}],
            bias=bias,
        ),
        _point(
            [{"values": [-2, 5]}, {"values": [index + 2, index + 3, 1]}],
            bias=bias + 1,
        ),
        _point(
            [{"values": [7, 8, 9]}, {"values": [bias, bias + 2]}],
            bias=bias,
        ),
        _point(
            [{"values": [3, 3, 3]}, {"values": [index - 1, index, index + 1]}],
            bias=bias - 1,
        ),
        _point(
            [{"values": [10, -4]}, {"values": [6, 1, 0, index]}],
            bias=bias + 2,
        ),
    )
    return {
        "case_id": f"spec-mutation-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks nested mutable inputs against the original declared-cover point.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _spec_family_guard(index: int, satisfies: bool) -> dict[str, object]:
    guard = (index % 5) - 2
    program = _dedent(
        """
        def _distance(value, guard):
            return abs(int(value) - guard)

        def solve(values, guard=0):
            total = 0
            for value in values:
                total += _distance(value, guard)
            return total
        """
        if satisfies
        else """
        def _distance(value, guard):
            value = int(value)
            if value == guard:
                raise ValueError(f'guard {guard} triggered')
            return abs(value - guard)

        def solve(values, guard=0):
            total = 0
            for value in values:
                total += _distance(value, guard)
            return total
        """
    )
    spec = _dedent(
        """
        def _expected_total(values, guard):
            total = 0
            for value in values:
                total += abs(int(value) - guard)
            return total

        def spec(result, values, guard=0):
            return isinstance(result, int) and result == _expected_total(values, guard)
        """
    )
    cover = _vary_cover(
        index,
        _point([guard - 2, guard - 1, guard + 1], guard),
        _point([guard + 2, guard + 4, guard + 6], guard=guard),
        _point([guard, guard + 3, guard - 3], guard),
        _point([guard + 1, guard + 5, guard - 5], guard=guard),
        _point([guard, guard + 7, guard - 7], guard),
    )
    return {
        "case_id": f"spec-guard-{'sat' if satisfies else 'unsat'}-{index:02d}",
        "description": "Specification checks exact guarded-distance totals with explicit exception-witness pressure points.",
        "program": program,
        "spec_program": spec,
        "input_cover": cover,
        "expected_satisfies": satisfies,
    }


def _bug_mutable_default(index: int, bugged: bool) -> dict[str, object]:
    variant = index % 5
    if bugged and variant == 0:
        program = _dedent(
            f"""
            def collect_{index}(value, bucket=[]):
                marker = int(value)
                bucket.append(marker)
                snapshot = []
                for item in bucket:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif bugged and variant == 1:
        program = _dedent(
            f"""
            def collect_{index}(value, ledger={{'items': []}}):
                marker = int(value)
                ledger['items'].append(marker)
                snapshot = []
                for item in ledger['items']:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif bugged and variant == 2:
        program = _dedent(
            f"""
            def collect_{index}(value, state=({{'items': []}},)):
                marker = int(value)
                state[0]['items'].append(marker)
                snapshot = []
                for item in state[0]['items']:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif bugged:
        program = _dedent(
            f"""
            def collect_{index}(value, bucket=list()):
                marker = int(value)
                bucket.append(marker)
                snapshot = []
                for item in bucket:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif variant == 0:
        program = _dedent(
            f"""
            def collect_{index}(value, bucket=None):
                if bucket is None:
                    bucket = []
                marker = int(value)
                bucket.append(marker)
                snapshot = []
                for item in bucket:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif variant == 1:
        program = _dedent(
            f"""
            def collect_{index}(value, ledger=None):
                if ledger is None:
                    ledger = {{'items': []}}
                marker = int(value)
                ledger['items'].append(marker)
                snapshot = []
                for item in ledger['items']:
                    snapshot.append(item)
                return snapshot
            """
        )
    elif variant == 2:
        program = _dedent(
            f"""
            def collect_{index}(value, state=()):
                holder = [{{'items': []}}]
                marker = int(value)
                holder[0]['items'].append(marker)
                snapshot = []
                for item in holder[0]['items']:
                    snapshot.append(item)
                return snapshot
            """
        )
    else:
        program = _dedent(
            f"""
            def collect_{index}(value, seed=()):
                bucket = list(seed)
                marker = int(value)
                bucket.append(marker)
                snapshot = []
                for item in bucket:
                    snapshot.append(item)
                return snapshot
            """
        )
    return {
        "case_id": f"bug-mutable-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Mutable default argument benchmark example.",
        "program": _with_bug_scaffold(program, family="mutable", index=index),
        "expected_bugs": ["mutable-default"] if bugged else [],
    }


def _bug_bare_except(index: int, bugged: bool) -> dict[str, object]:
    if bugged and index % 2 == 0:
        program = _dedent(
            f"""
            def parse_{index}(raw):
                try:
                    number = int(raw)
                    return number + {index}
                except:
                    return {index}
            """
        )
    elif bugged:
        program = _dedent(
            f"""
            def parse_{index}(raw):
                total = 0
                try:
                    for piece in raw.split(','):
                        total += int(piece)
                    return total
                except:
                    return {index}
            """
        )
    elif index % 2 == 0:
        program = _dedent(
            f"""
            def parse_{index}(raw):
                try:
                    number = int(raw)
                    return number + {index}
                except ValueError:
                    return {index}
            """
        )
    else:
        program = _dedent(
            f"""
            def parse_{index}(raw):
                total = 0
                try:
                    for piece in raw.split(','):
                        total += int(piece)
                    return total
                except (TypeError, ValueError):
                    return {index}
            """
        )
    return {
        "case_id": f"bug-except-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Bare except benchmark example.",
        "program": _with_bug_scaffold(program, family="except_case", index=index),
        "expected_bugs": ["bare-except"] if bugged else [],
    }


def _bug_late_binding(index: int, bugged: bool) -> dict[str, object]:
    variant = index % 3
    if bugged and variant == 0:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                return [lambda value: value + factor for factor in factors]
            """
        )
    elif bugged and variant == 1:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                callbacks = []
                for factor in factors:
                    callbacks.append(lambda value: value + factor)
                return callbacks
            """
        )
    elif bugged:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                callbacks = []
                for factor in factors:
                    def apply(value):
                        return value + factor
                    callbacks.append(apply)
                return callbacks
            """
        )
    elif variant == 0:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                return [lambda value, factor=factor: value + factor for factor in factors]
            """
        )
    elif variant == 1:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                callbacks = []
                for factor in factors:
                    callbacks.append(lambda value, factor=factor: value + factor)
                return callbacks
            """
        )
    else:
        program = _dedent(
            f"""
            def builders_{index}(factors):
                callbacks = []
                for factor in factors:
                    def apply(value, factor=factor):
                        return value + factor
                    callbacks.append(apply)
                return callbacks
            """
        )
    return {
        "case_id": f"bug-late-binding-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Late-binding closure benchmark example.",
        "program": _with_bug_scaffold(program, family="late_binding", index=index),
        "expected_bugs": ["late-binding-closure"] if bugged else [],
    }


def _bug_open_without_close(index: int, bugged: bool) -> dict[str, object]:
    variant = index % 4
    if bugged and variant == 0:
        program = _dedent(
            f"""
            def read_{index}(path):
                left, right = open(path, 'r', encoding='utf-8'), open(path, 'r', encoding='utf-8')
                content = left.read() + right.read()
                return content.splitlines()
            """
        )
    elif bugged and variant == 1:
        program = _dedent(
            f"""
            def read_{index}(path):
                handle = open(path, 'r', encoding='utf-8')
                content = handle.read()
                pieces = []
                for line in content.splitlines():
                    pieces.append(line.strip())
                return pieces
            """
        )
    elif bugged and variant == 2:
        program = _dedent(
            f"""
            def read_{index}(path):
                use_file = True
                if use_file:
                    handle = open(path, 'r', encoding='utf-8')
                    first = handle.readline().strip()
                    remainder = handle.read().splitlines()
                    return [first, *remainder]
                return []
            """
        )
    elif bugged:
        program = _dedent(
            f"""
            def read_{index}(path, should_close=False):
                handle = open(path, 'r', encoding='utf-8')
                if should_close:
                    handle.close()
                return should_close
            """
        )
    elif variant == 0:
        program = _dedent(
            f"""
            def read_{index}(path):
                with open(path, 'r', encoding='utf-8') as left, open(path, 'r', encoding='utf-8') as right:
                    content = left.read() + right.read()
                return content.splitlines()
            """
        )
    elif variant == 1:
        program = _dedent(
            f"""
            def read_{index}(path):
                with open(path, 'r', encoding='utf-8') as handle:
                    content = handle.read()
                pieces = []
                for line in content.splitlines():
                    pieces.append(line.strip())
                return pieces
            """
        )
    elif variant == 2:
        program = _dedent(
            f"""
            def read_{index}(path):
                use_file = True
                if use_file:
                    handle = open(path, 'r', encoding='utf-8')
                    try:
                        first = handle.readline().strip()
                        remainder = handle.read().splitlines()
                    finally:
                        handle.close()
                    return [first, *remainder]
                return []
            """
        )
    else:
        program = _dedent(
            f"""
            def read_{index}(path, should_close=True):
                if should_close:
                    handle = open(path, 'r', encoding='utf-8')
                    try:
                        return handle.readline().strip()
                    finally:
                        handle.close()
                return ""
            """
        )
    return {
        "case_id": f"bug-open-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Open-without-close benchmark example.",
        "program": _with_bug_scaffold(program, family="resource", index=index),
        "expected_bugs": ["open-without-close"] if bugged else [],
    }


def _bug_shadow_builtin(index: int, bugged: bool) -> dict[str, object]:
    if bugged and index % 2 == 0:
        program = _dedent(
            f"""
            def summarize_{index}(values, cache=[]):
                list = []
                for value in values:
                    marker = int(value) * 2
                    cache.append(marker)
                    list.append(marker)
                stitched = []
                for item in list:
                    stitched.append(item)
                return stitched, tuple(cache)
            """
        )
        expected_bugs = ["mutable-default", "shadow-builtin"]
        description = "Mutable default state plus builtin shadowing benchmark example."
    elif bugged and index % 3 == 0:
        program = _dedent(
            f"""
            def summarize_{index}(sum, values):
                running_total = int(sum)
                scaled_values = []
                for value in values:
                    marker = int(value) * 2
                    running_total += marker
                    scaled_values.append(marker)
                return running_total, tuple(scaled_values)
            """
        )
        expected_bugs = ["shadow-builtin"]
        description = "Builtin parameter shadowing benchmark example."
    else:
        program = _dedent(
            f"""
            def summarize_{index}(values):
                list = []
                for value in values:
                    marker = int(value) * 2
                    list.append(marker)
                stitched = []
                for item in list:
                    stitched.append(item)
                return stitched
            """
            if bugged
            else f"""
            def summarize_{index}(values):
                doubled_values = []
                for value in values:
                    marker = int(value) * 2
                    doubled_values.append(marker)
                stitched = []
                for item in doubled_values:
                    stitched.append(item)
                return stitched
            """
        )
        expected_bugs = ["shadow-builtin"] if bugged else []
        description = "Builtin shadowing benchmark example."
    return {
        "case_id": f"bug-shadow-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": description,
        "program": _with_bug_scaffold(program, family="shadow", index=index),
        "expected_bugs": expected_bugs,
    }


def _bug_identity_literal(index: int, bugged: bool) -> dict[str, object]:
    token = f"token-{index}"
    pair = (token, f"alt-{index}")
    if bugged and index % 2 == 0:
        program = _dedent(
            f"""
            def matches_{index}(raw):
                cleaned = raw.strip()
                pieces = []
                for char in cleaned:
                    pieces.append(char)
                candidate = ''.join(pieces)
                if candidate is {token!r}:
                    return True
                return candidate.startswith('token-')
            """
        )
    elif bugged:
        program = _dedent(
            f"""
            def matches_{index}(raw):
                first, second = raw.strip().split(':')
                pair = (first, second)
                if pair is not {pair!r}:
                    return True
                return False
            """
        )
    elif index % 2 == 0:
        program = _dedent(
            f"""
            def matches_{index}(raw):
                cleaned = raw.strip()
                pieces = []
                for char in cleaned:
                    pieces.append(char)
                candidate = ''.join(pieces)
                if candidate == {token!r}:
                    return True
                return candidate.startswith('token-')
            """
        )
    else:
        program = _dedent(
            f"""
            def matches_{index}(raw):
                first, second = raw.strip().split(':')
                pair = (first, second)
                if pair != {pair!r}:
                    return True
                return False
            """
        )
    return {
        "case_id": f"bug-identity-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Identity comparison against a non-singleton literal benchmark example.",
        "program": _with_bug_scaffold(program, family="identity", index=index),
        "expected_bugs": ["identity-literal"] if bugged else [],
    }


def _bug_hybrid_obstruction(index: int, bugged: bool) -> dict[str, object]:
    if bugged and index % 2 == 0:
        program = _dedent(
            f"""
            def diagnose_{index}(path, cache=[]):
                handle = open(path, 'r', encoding='utf-8')
                try:
                    for line in handle:
                        cache.append(line.strip())
                    return tuple(cache)
                except:
                    return tuple(cache)
            """
        )
        expected_bugs = ["mutable-default", "open-without-close", "bare-except"]
    elif bugged:
        program = _dedent(
            f"""
            def diagnose_{index}(factors, total=0):
                callbacks = []
                for sum in factors:
                    callbacks.append(lambda value: value + sum + total)
                return callbacks
            """
        )
        expected_bugs = ["shadow-builtin", "late-binding-closure"]
    elif index % 2 == 0:
        program = _dedent(
            f"""
            def diagnose_{index}(path, cache=None):
                if cache is None:
                    cache = []
                try:
                    with open(path, 'r', encoding='utf-8') as handle:
                        for line in handle:
                            cache.append(line.strip())
                    return tuple(cache)
                except OSError:
                    return tuple(cache)
            """
        )
        expected_bugs = []
    else:
        program = _dedent(
            f"""
            def diagnose_{index}(factors, offset=0):
                callbacks = []
                for factor in factors:
                    callbacks.append(lambda value, factor=factor: value + factor + offset)
                return callbacks
            """
        )
        expected_bugs = []
    return {
        "case_id": f"bug-hybrid-{'bug' if bugged else 'clean'}-{index:02d}",
        "description": "Hybrid benchmark example mixing multiple common Python bug modes in one support region.",
        "program": _with_bug_scaffold(program, family="hybrid", index=index),
        "expected_bugs": expected_bugs,
    }


def build_equivalence_suite() -> dict[str, object]:
    families = [
        _equivalence_family_affine,
        _equivalence_family_gaps,
        _equivalence_family_streak,
        _equivalence_family_matrix,
        _equivalence_family_words,
        _equivalence_family_guard,
        _equivalence_family_records,
        _equivalence_family_mutation,
    ]
    cases = []
    for family, index in _iter_family_indices(families):
        cases.append(_decorate_equivalence_case(family(index, True)))
        cases.append(_decorate_equivalence_case(family(index, False)))
    return _suite_metadata(
        suite="equivalence",
        cases=cases,
        benchmark_semantics="extensional-equality-on-declared-cover",
        composition={
            "total_cases": len(cases),
            "equivalent_cases": sum(bool(case["expected_equivalent"]) for case in cases),
            "non_equivalent_cases": sum(not bool(case["expected_equivalent"]) for case in cases),
            "declared_cover_min_points": min(len(case["input_cover"]) for case in cases),
            "declared_cover_max_points": max(len(case["input_cover"]) for case in cases),
        },
        longish_programs={
            "left_program_min_lines": min(_program_lines(str(case["left_program"])) for case in cases),
            "right_program_min_lines": min(_program_lines(str(case["right_program"])) for case in cases),
        },
        extra={
            "relation_families": ["extensional-equality-on-declared-cover"],
            "certificate_projection": "declared-cover-observables-only",
        },
    )


def build_spec_suite() -> dict[str, object]:
    families = [
        _spec_family_affine,
        _spec_family_gaps,
        _spec_family_streak,
        _spec_family_matrix,
        _spec_family_words,
        _spec_family_records,
        _spec_family_mutation,
        _spec_family_guard,
    ]
    cases = []
    for family, index in _iter_family_indices(families):
        cases.append(_decorate_spec_case(family(index, True)))
        cases.append(_decorate_spec_case(family(index, False)))
    return _suite_metadata(
        suite="spec",
        cases=cases,
        benchmark_semantics="boolean-returning-specification-on-declared-cover",
        composition={
            "total_cases": len(cases),
            "satisfying_cases": sum(bool(case["expected_satisfies"]) for case in cases),
            "unsatisfying_cases": sum(not bool(case["expected_satisfies"]) for case in cases),
            "declared_cover_min_points": min(len(case["input_cover"]) for case in cases),
            "declared_cover_max_points": max(len(case["input_cover"]) for case in cases),
        },
        longish_programs={
            "program_min_lines": min(_program_lines(str(case["program"])) for case in cases),
            "spec_program_min_lines": min(_program_lines(str(case["spec_program"])) for case in cases),
        },
        extra={
            "spec_contract": "spec(result, *args, **kwargs) -> bool",
            "cover_truth_requirement": "spec must hold on every declared cover point",
        },
    )


def build_bug_suite() -> dict[str, object]:
    families = [
        _bug_mutable_default,
        _bug_bare_except,
        _bug_late_binding,
        _bug_open_without_close,
        _bug_shadow_builtin,
        _bug_identity_literal,
        _bug_hybrid_obstruction,
    ]
    cases = []
    for family, index in _iter_family_indices(families):
        cases.append(family(index, True))
        cases.append(family(index, False))
    return _suite_metadata(
        suite="bug",
        cases=cases,
        benchmark_semantics="common-python-bug-checking",
        composition={
            "total_cases": len(cases),
            "bug_positive_cases": sum(bool(case["expected_bugs"]) for case in cases),
            "bug_negative_cases": sum(not bool(case["expected_bugs"]) for case in cases),
            "multi_bug_cases": sum(len(set(case["expected_bugs"])) >= 2 for case in cases),
        },
        longish_programs={
            "program_min_lines": min(_program_lines(str(case["program"])) for case in cases),
        },
        extra={
            "bug_labels": sorted(
                {
                    label
                    for case in cases
                    for label in case["expected_bugs"]
                }
            ),
        },
    )


def main() -> None:
    payloads = {
        ROOT / "equivalence_suite.json": build_equivalence_suite(),
        ROOT / "spec_suite.json": build_spec_suite(),
        ROOT / "bug_suite.json": build_bug_suite(),
    }
    validate_suite_payloads(payloads)
    for path, payload in payloads.items():
        path.write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
