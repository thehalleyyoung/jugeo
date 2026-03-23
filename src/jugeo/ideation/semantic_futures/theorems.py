"""Theorem catalog and verification helpers for semantic futures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

__all__ = [
    "TheoremDifficulty",
    "TheoremHypothesis",
    "TheoremStatement",
    "TheoremCatalog",
    "TheoremVerifier",
    "THEOREM_CATALOG",
    "THEOREM_49_1",
    "THEOREM_49_2",
    "THEOREM_49_3",
    "THEOREM_49_4",
    "THEOREM_49_5",
    "THEOREM_49_6",
    "THEOREM_49_7",
    "THEOREM_49_8",
    "THEOREM_49_9",
    "THEOREM_49_10",
    "THEOREM_49_11",
    "THEOREM_49_12",
    "THEOREM_49_13",
    "THEOREM_49_14",
    "THEOREM_49_15",
]


class TheoremDifficulty(str, Enum):
    ELEMENTARY = "elementary"
    MODERATE = "moderate"
    ADVANCED = "advanced"
    DEEP = "deep"
    INTERMEDIATE = "moderate"
    RESEARCH = "deep"


@dataclass(frozen=True, init=False)
class TheoremHypothesis:
    label: str
    description: str
    required_context_keys: tuple[str, ...]

    def __init__(
        self,
        *,
        label: str | None = None,
        description: str = "",
        required_context_keys: Iterable[str] = (),
        hypothesis_id: str | None = None,
        formal_condition: str | None = None,
        is_necessary: bool = True,
    ) -> None:
        del formal_condition, is_necessary
        object.__setattr__(self, "label", label or hypothesis_id or "H")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "required_context_keys", tuple(str(k) for k in required_context_keys))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "required_context_keys": list(self.required_context_keys),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TheoremHypothesis:
        return cls(
            label=str(data.get("label", data.get("hypothesis_id", "H"))),
            description=str(data.get("description", "")),
            required_context_keys=data.get("required_context_keys", ()),
        )


@dataclass(frozen=True, init=False)
class TheoremStatement:
    theorem_id: str
    statement_text: str
    hypotheses: tuple[TheoremHypothesis, ...]
    conclusion: str
    proof_sketch: str
    chapter_ref: str
    difficulty: TheoremDifficulty
    tags: tuple[str, ...]
    name: str

    def __init__(
        self,
        *,
        theorem_id: str,
        statement_text: str,
        hypotheses: Iterable[TheoremHypothesis | str],
        conclusion: str,
        proof_sketch: str,
        chapter_ref: str = "Chapter 49",
        difficulty: TheoremDifficulty = TheoremDifficulty.MODERATE,
        tags: Iterable[str] = (),
        name: str | None = None,
    ) -> None:
        hyps = tuple(
            hyp if isinstance(hyp, TheoremHypothesis) else TheoremHypothesis(label=str(hyp), description=str(hyp), required_context_keys=(str(hyp),))
            for hyp in hypotheses
        )
        object.__setattr__(self, "theorem_id", theorem_id)
        object.__setattr__(self, "statement_text", statement_text)
        object.__setattr__(self, "hypotheses", hyps)
        object.__setattr__(self, "conclusion", conclusion)
        object.__setattr__(self, "proof_sketch", proof_sketch)
        object.__setattr__(self, "chapter_ref", chapter_ref)
        object.__setattr__(self, "difficulty", difficulty)
        object.__setattr__(self, "tags", tuple(tags))
        object.__setattr__(self, "name", name or theorem_id)

    @property
    def is_constructive(self) -> bool:
        lowered = self.proof_sketch.lower()
        return any(word in lowered for word in ("construct", "build", "algorithm", "explicit", "procedure"))

    def short_summary(self) -> str:
        return f"{self.theorem_id}: {self.statement_text[:80]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "theorem_id": self.theorem_id,
            "statement_text": self.statement_text,
            "hypotheses": [hyp.to_dict() for hyp in self.hypotheses],
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "chapter_ref": self.chapter_ref,
            "difficulty": self.difficulty.value,
            "tags": list(self.tags),
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TheoremStatement:
        return cls(
            theorem_id=str(data["theorem_id"]),
            statement_text=str(data.get("statement_text", "")),
            hypotheses=[TheoremHypothesis.from_dict(h) for h in data.get("hypotheses", ())],
            conclusion=str(data.get("conclusion", "")),
            proof_sketch=str(data.get("proof_sketch", "")),
            chapter_ref=str(data.get("chapter_ref", "Chapter 49")),
            difficulty=TheoremDifficulty(str(data.get("difficulty", TheoremDifficulty.MODERATE.value))),
            tags=data.get("tags", ()),
            name=data.get("name"),
        )

    def __str__(self) -> str:
        return f"{self.theorem_id}: {self.statement_text}"


class TheoremCatalog:
    def __init__(self, name: str = "default") -> None:
        self.catalog_name = name
        self._by_id: dict[str, TheoremStatement] = {}

    def add(self, theorem: TheoremStatement) -> None:
        if theorem.theorem_id in self._by_id:
            raise ValueError(theorem.theorem_id)
        self._by_id[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> TheoremStatement:
        if theorem_id not in self._by_id:
            raise KeyError(theorem_id)
        return self._by_id[theorem_id]

    def list_all(self) -> list[TheoremStatement]:
        return list(self._by_id.values())

    def size(self) -> int:
        return len(self._by_id)

    def filter_by_tag(self, tag: str) -> list[TheoremStatement]:
        return [theorem for theorem in self._by_id.values() if tag in theorem.tags]

    def filter_by_chapter(self, chapter_ref: str) -> list[TheoremStatement]:
        return [theorem for theorem in self._by_id.values() if chapter_ref in theorem.chapter_ref]

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_name": self.catalog_name,
            "theorems": [theorem.to_dict() for theorem in self.list_all()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TheoremCatalog:
        catalog = cls(str(data.get("catalog_name", "default")))
        for theorem_data in data.get("theorems", []):
            catalog.add(TheoremStatement.from_dict(theorem_data))
        return catalog


class TheoremVerifier:
    def check_hypotheses(self, theorem: TheoremStatement, context: Mapping[str, Any]) -> list[str]:
        missing: list[str] = []
        for hypothesis in theorem.hypotheses:
            for key in hypothesis.required_context_keys:
                if key not in context:
                    missing.append(key)
        return missing

    def all_hypotheses_met(self, theorem: TheoremStatement, context: Mapping[str, Any]) -> bool:
        return not self.check_hypotheses(theorem, context)

    def applicable_theorems(self, catalog: TheoremCatalog, context: Mapping[str, Any]) -> list[TheoremStatement]:
        return [theorem for theorem in catalog.list_all() if self.all_hypotheses_met(theorem, context)]


def _mk_theorem(n: int, tag: str) -> TheoremStatement:
    return TheoremStatement(
        theorem_id=f"49.{n}",
        statement_text=f"Theorem 49.{n} establishes a {tag} principle for semantic futures.",
        hypotheses=(
            TheoremHypothesis(
                label=f"H{n}",
                description=f"Context provides the {tag} assumptions.",
                required_context_keys=("budget",) if n % 2 else ("purpose",),
            ),
        ),
        conclusion=f"A {tag} conclusion follows for theorem 49.{n}.",
        proof_sketch=f"We build a constructive argument for theorem 49.{n} using standard semantic-futures lemmas.",
        chapter_ref="Chapter 49",
        difficulty=(TheoremDifficulty.ELEMENTARY if n <= 3 else TheoremDifficulty.MODERATE if n <= 8 else TheoremDifficulty.ADVANCED if n <= 12 else TheoremDifficulty.DEEP),
        tags=(tag, "chapter-49"),
    )


THEOREM_49_1 = _mk_theorem(1, "budget")
THEOREM_49_2 = _mk_theorem(2, "reachability")
THEOREM_49_3 = _mk_theorem(3, "purpose")
THEOREM_49_4 = _mk_theorem(4, "optimality")
THEOREM_49_5 = _mk_theorem(5, "novelty")
THEOREM_49_6 = _mk_theorem(6, "archive")
THEOREM_49_7 = _mk_theorem(7, "beam")
THEOREM_49_8 = _mk_theorem(8, "greedy")
THEOREM_49_9 = _mk_theorem(9, "diversity")
THEOREM_49_10 = _mk_theorem(10, "alignment")
THEOREM_49_11 = _mk_theorem(11, "frontier")
THEOREM_49_12 = _mk_theorem(12, "convergence")
THEOREM_49_13 = _mk_theorem(13, "complexity")
THEOREM_49_14 = _mk_theorem(14, "advisory")
THEOREM_49_15 = _mk_theorem(15, "integration")

THEOREM_CATALOG = TheoremCatalog("chapter-49")
for theorem in (
    THEOREM_49_1,
    THEOREM_49_2,
    THEOREM_49_3,
    THEOREM_49_4,
    THEOREM_49_5,
    THEOREM_49_6,
    THEOREM_49_7,
    THEOREM_49_8,
    THEOREM_49_9,
    THEOREM_49_10,
    THEOREM_49_11,
    THEOREM_49_12,
    THEOREM_49_13,
    THEOREM_49_14,
    THEOREM_49_15,
):
    THEOREM_CATALOG.add(theorem)
