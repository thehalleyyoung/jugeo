from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

JsonValue = Any


def _require_mapping(payload: object, *, context: str) -> Mapping[str, JsonValue]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{context} must be a mapping")
    return payload


def _require_str(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    return value


def _require_bool(value: object, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{context} must be a boolean")
    return value


def _require_args(value: object, *, context: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{context} must be a list or tuple")
    return tuple(value)


def _require_kwargs(value: object, *, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{context} must use string keys")
    return dict(value)


def _require_string_sequence(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{context} must be a sequence of strings")
    items = tuple(_require_str(item, context=f"{context} entry") for item in value)
    if len(items) != len(set(items)):
        raise ValueError(f"{context} must not contain duplicate labels")
    return items


@dataclass(frozen=True, slots=True)
class InputPoint:
    args: tuple[JsonValue, ...]
    kwargs: dict[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> "InputPoint":
        payload = _require_mapping(payload, context="input point")
        return cls(
            args=_require_args(payload.get("args", ()), context="input point args"),
            kwargs=_require_kwargs(payload.get("kwargs", {}), context="input point kwargs"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"args": list(self.args), "kwargs": dict(self.kwargs)}


@dataclass(frozen=True, slots=True)
class EquivalenceCase:
    case_id: str
    description: str
    relation_family: str
    left_program: str
    right_program: str
    input_cover: tuple[InputPoint, ...]
    expected_equivalent: bool

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> "EquivalenceCase":
        payload = _require_mapping(payload, context="equivalence case")
        return cls(
            case_id=_require_str(payload["case_id"], context="equivalence case id"),
            description=_require_str(payload["description"], context="equivalence description"),
            relation_family=_require_str(payload["relation_family"], context="equivalence relation family"),
            left_program=_require_str(payload["left_program"], context="equivalence left program"),
            right_program=_require_str(payload["right_program"], context="equivalence right program"),
            input_cover=tuple(InputPoint.from_dict(item) for item in payload["input_cover"]),
            expected_equivalent=_require_bool(payload["expected_equivalent"], context="expected_equivalent"),
        )


@dataclass(frozen=True, slots=True)
class SpecCase:
    case_id: str
    description: str
    program: str
    spec_program: str
    input_cover: tuple[InputPoint, ...]
    expected_satisfies: bool

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> "SpecCase":
        payload = _require_mapping(payload, context="spec case")
        return cls(
            case_id=_require_str(payload["case_id"], context="spec case id"),
            description=_require_str(payload["description"], context="spec description"),
            program=_require_str(payload["program"], context="spec program"),
            spec_program=_require_str(payload["spec_program"], context="spec program predicate"),
            input_cover=tuple(InputPoint.from_dict(item) for item in payload["input_cover"]),
            expected_satisfies=_require_bool(payload["expected_satisfies"], context="expected_satisfies"),
        )


@dataclass(frozen=True, slots=True)
class BugCase:
    case_id: str
    description: str
    program: str
    expected_bugs: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> "BugCase":
        payload = _require_mapping(payload, context="bug case")
        return cls(
            case_id=_require_str(payload["case_id"], context="bug case id"),
            description=_require_str(payload["description"], context="bug description"),
            program=_require_str(payload["program"], context="bug program"),
            expected_bugs=_require_string_sequence(payload["expected_bugs"], context="expected_bugs"),
        )


@dataclass(frozen=True, slots=True)
class Witness:
    message: str
    input_point: InputPoint | None = None
    coordinate: str | None = None
    cover_index: int | None = None


@dataclass(frozen=True, slots=True)
class ResidualObligation:
    obligation: str
    support_indices: tuple[int, ...] = ()
    reopen_condition: str | None = None


@dataclass(frozen=True, slots=True)
class MetricSummary:
    true_positives: int
    false_positives: int
    false_negatives: int
    total_cases: int
    correct_cases: int

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    @property
    def f1(self) -> float:
        precision = self.precision
        recall = self.recall
        denominator = precision + recall
        return 0.0 if denominator == 0 else 2.0 * precision * recall / denominator

    @property
    def accuracy(self) -> float:
        return 1.0 if self.total_cases == 0 else self.correct_cases / self.total_cases

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.true_positives,
            "fp": self.false_positives,
            "fn": self.false_negatives,
            "total_cases": self.total_cases,
            "correct_cases": self.correct_cases,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkJudgment:
    category: str
    case_id: str
    expected: Any
    predicted: Any
    passed: bool
    trust_tier: str
    witness: Witness | None = None
    residuals: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = ()
    residual_obligations: tuple[ResidualObligation, ...] = ()
    obstruction_class: str | None = None
    support_indices: tuple[int, ...] = ()
    repair_feasibility: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    category: str
    metrics: MetricSummary
    judgments: tuple[BenchmarkJudgment, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkBundle:
    equivalence_cases: tuple[EquivalenceCase, ...]
    spec_cases: tuple[SpecCase, ...]
    bug_cases: tuple[BugCase, ...]

    def category_sizes(self) -> dict[str, int]:
        return {
            "equivalence": len(self.equivalence_cases),
            "spec": len(self.spec_cases),
            "bug": len(self.bug_cases),
        }


# ---------------------------------------------------------------------------
# Unified judgment-geometric benchmark case models
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    judgment_terms = None

try:
    from jugeo.geometry import descent as _descent_mod  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _descent_mod = None

try:
    from jugeo.encodings import EncodingFamily  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    EncodingFamily = None


@dataclass(frozen=True, slots=True)
class JudgmentBenchmarkCase:
    """A benchmark case centred on judgment construction.

    Fields reference ``jugeo.judgments.judgment_terms`` for the canonical
    term algebra that underlies judgment formation.
    """

    case_id: str
    description: str
    judgment_descriptor: Any
    expected_term_kind: str = ""
    judgment_terms_module: Any = field(default_factory=lambda: judgment_terms)


@dataclass(frozen=True, slots=True)
class DescentBenchmarkCase:
    """A benchmark case centred on geometric descent.

    References ``jugeo.geometry.descent`` for the descent algorithm
    exercised by the benchmark runner.
    """

    case_id: str
    description: str
    site: Any
    expected_depth: int = 0
    descent_module: Any = field(default_factory=lambda: _descent_mod)


@dataclass(frozen=True, slots=True)
class EncodingBenchmarkCase:
    """A benchmark case centred on program encoding.

    References ``jugeo.encodings`` for the encoding families used.
    """

    case_id: str
    description: str
    program_source: str
    encoding_family: str = "default"
    encoding_family_type: Any = field(default_factory=lambda: EncodingFamily)
