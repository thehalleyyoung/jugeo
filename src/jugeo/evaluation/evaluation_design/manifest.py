from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _utcnow() -> float:
    return time.time()


def _uid() -> str:
    return str(uuid.uuid4())


def _dedupe(items: list[str] | tuple[str, ...] | None) -> list[str]:
    if not items:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@dataclass(slots=True)
class EvaluationDesignManifest:
    manifest_id: str
    version: str
    author: str
    chapter_ref: str = 'Ch63'
    theory_section: str = ''
    design_name: str = ''
    exports: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)
    description: str = ''
    clause_count: int = 0
    ablation_count: int = 0
    calibration_methods: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, *, version: str, author: str, **kwargs: Any) -> 'EvaluationDesignManifest':
        return cls(
            manifest_id=str(kwargs.pop('manifest_id', _uid())),
            version=str(version),
            author=str(author),
            chapter_ref=str(kwargs.pop('chapter_ref', 'Ch63')),
            theory_section=str(kwargs.pop('theory_section', '')),
            design_name=str(kwargs.pop('design_name', '')),
            exports=_dedupe(kwargs.pop('exports', [])),
            created_at=float(kwargs.pop('created_at', _utcnow())),
            description=str(kwargs.pop('description', '')),
            clause_count=max(0, int(kwargs.pop('clause_count', 0))),
            ablation_count=max(0, int(kwargs.pop('ablation_count', 0))),
            calibration_methods=_dedupe(kwargs.pop('calibration_methods', [])),
            tags=_dedupe(kwargs.pop('tags', [])),
        )

    @classmethod
    def from_design(cls, design: Any) -> 'EvaluationDesignManifest':
        calibration_config = getattr(design, 'calibration_config', {}) or {}
        methods = []
        if isinstance(calibration_config, dict):
            if 'methods' in calibration_config:
                methods = calibration_config.get('methods', []) or []
            elif 'method' in calibration_config:
                methods = [calibration_config['method']]
        ablation_plan = getattr(design, 'ablation_plan', {}) or {}
        ablation_count = len(ablation_plan.get('components', [])) if isinstance(ablation_plan, dict) and 'components' in ablation_plan else len(ablation_plan) if hasattr(ablation_plan, '__len__') else 0
        return cls.create(
            version='1.0',
            author='system',
            design_name=getattr(design, 'name', ''),
            description=getattr(design, 'description', ''),
            clause_count=len(getattr(design, 'clauses', []) or []),
            ablation_count=ablation_count,
            calibration_methods=list(methods),
            exports=list(getattr(design, 'exports', []) or []),
            tags=list(getattr(design, 'tags', []) or []),
        )

    def add_export(self, name: str) -> None:
        text = str(name).strip()
        if text and text not in self.exports:
            self.exports.append(text)

    def add_tag(self, tag: str) -> None:
        text = str(tag).strip()
        if text and text not in self.tags:
            self.tags.append(text)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.manifest_id:
            errors.append('manifest_id must not be empty')
        if not self.version:
            errors.append('version must not be empty')
        elif '.' not in self.version:
            errors.append('version must contain at least one dot')
        if not self.author:
            errors.append('author must not be empty')
        if not self.design_name:
            errors.append('design_name must not be empty')
        if not self.description:
            errors.append('description must not be empty')
        if self.clause_count < 0:
            errors.append('clause_count must be non-negative')
        if self.ablation_count < 0:
            errors.append('ablation_count must be non-negative')
        if any(not exp for exp in self.exports):
            errors.append('exports must not contain empty strings')
        if any(not tag for tag in self.tags):
            errors.append('tags must not contain empty strings')
        return errors

    def is_complete(self) -> bool:
        return bool(self.design_name and self.description and self.exports and not self.validate())

    def summarize(self) -> str:
        return f"{self.design_name or 'Unnamed'} v{self.version} by {self.author} ({len(self.exports)} exports)"

    def render_tex(self) -> str:
        return (
            f"\\section*{{{self.design_name or 'Unnamed Evaluation Design'}}}\n"
            f"Version: {self.version}\\\\\n"
            f"Author: {self.author}\\\\\n"
            f"Description: {self.description}\\\\\n"
            f"Exports: {', '.join(self.exports)}"
        )

    def to_registry_entry(self) -> dict[str, Any]:
        return {
            'type': 'evaluation_design_manifest',
            'manifest_id': self.manifest_id,
            'version': self.version,
            'author': self.author,
            'chapter_ref': self.chapter_ref,
            'theory_section': self.theory_section,
            'design_name': self.design_name,
            'exports': list(self.exports),
            'created_at': self.created_at,
            'description': self.description,
            'clause_count': self.clause_count,
            'ablation_count': self.ablation_count,
            'calibration_methods': list(self.calibration_methods),
            'tags': list(self.tags),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_registry_entry() | {'type': 'evaluation_design_manifest'}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, data: str) -> 'EvaluationDesignManifest':
        payload = json.loads(data)
        payload.pop('type', None)
        return cls.create(**payload)


class EvaluationManifestBuilder:
    def __init__(self, *, version: str, author: str) -> None:
        self.version = version
        self.author = author
        self.reset()

    def reset(self) -> 'EvaluationManifestBuilder':
        self.chapter_ref = 'Ch63'
        self.theory_section = ''
        self.design_name = ''
        self.exports: list[str] = []
        self.description = ''
        self.clause_count = 0
        self.ablation_count = 0
        self.calibration_methods: list[str] = []
        self.tags: list[str] = []
        return self

    def clone(self) -> 'EvaluationManifestBuilder':
        other = EvaluationManifestBuilder(version=self.version, author=self.author)
        other.chapter_ref = self.chapter_ref
        other.theory_section = self.theory_section
        other.design_name = self.design_name
        other.exports = list(self.exports)
        other.description = self.description
        other.clause_count = self.clause_count
        other.ablation_count = self.ablation_count
        other.calibration_methods = list(self.calibration_methods)
        other.tags = list(self.tags)
        return other

    def set_design_name(self, design_name: str) -> 'EvaluationManifestBuilder':
        self.design_name = str(design_name)
        return self

    def add_export(self, export_name: str) -> 'EvaluationManifestBuilder':
        text = str(export_name).strip()
        if text and text not in self.exports:
            self.exports.append(text)
        return self

    def add_tag(self, tag: str) -> 'EvaluationManifestBuilder':
        text = str(tag).strip()
        if text and text not in self.tags:
            self.tags.append(text)
        return self

    def set_clause_count(self, clause_count: int) -> 'EvaluationManifestBuilder':
        self.clause_count = max(0, int(clause_count))
        return self

    def set_ablation_count(self, ablation_count: int) -> 'EvaluationManifestBuilder':
        self.ablation_count = max(0, int(ablation_count))
        return self

    def set_calibration_methods(self, calibration_methods: list[str]) -> 'EvaluationManifestBuilder':
        self.calibration_methods = _dedupe(list(calibration_methods))
        return self

    def build(self) -> EvaluationDesignManifest:
        return EvaluationDesignManifest.create(
            version=self.version,
            author=self.author,
            chapter_ref=self.chapter_ref,
            theory_section=self.theory_section,
            design_name=self.design_name,
            exports=list(self.exports),
            description=self.description,
            clause_count=self.clause_count,
            ablation_count=self.ablation_count,
            calibration_methods=list(self.calibration_methods),
            tags=list(self.tags),
        )


class EvaluationManifestRegistry:
    def __init__(self) -> None:
        self._items: dict[str, EvaluationDesignManifest] = {}

    def is_empty(self) -> bool:
        return not self._items

    def count(self) -> int:
        return len(self._items)

    def register(self, manifest: EvaluationDesignManifest) -> None:
        self._items[manifest.manifest_id] = manifest

    def has(self, manifest_id: str) -> bool:
        return manifest_id in self._items

    def get(self, manifest_id: str) -> EvaluationDesignManifest | None:
        return self._items.get(manifest_id)

    def remove(self, manifest_id: str) -> bool:
        return self._items.pop(manifest_id, None) is not None

    def list_all(self) -> list[EvaluationDesignManifest]:
        return sorted(self._items.values(), key=lambda m: m.created_at)

    def find_by_tag(self, tag: str) -> list[EvaluationDesignManifest]:
        return [m for m in self.list_all() if tag in m.tags]

    def find_by_author(self, author: str) -> list[EvaluationDesignManifest]:
        return [m for m in self.list_all() if m.author == author]

    def find_by_design_name(self, design_name: str) -> list[EvaluationDesignManifest]:
        return [m for m in self.list_all() if m.design_name == design_name]

    def find_by_chapter_ref(self, chapter_ref: str) -> list[EvaluationDesignManifest]:
        return [m for m in self.list_all() if m.chapter_ref == chapter_ref]

    def latest(self) -> EvaluationDesignManifest | None:
        if not self._items:
            return None
        return max(self._items.values(), key=lambda m: m.created_at)

    def to_json(self) -> str:
        return json.dumps([m.to_dict() for m in self.list_all()], indent=2)

    @classmethod
    def from_json(cls, data: str) -> 'EvaluationManifestRegistry':
        payload = json.loads(data)
        registry = cls()
        items = payload if isinstance(payload, list) else payload.get('items', [])
        for item in items:
            item.pop('type', None)
            registry.register(EvaluationDesignManifest.create(**item))
        return registry


def build_evaluation_manifest(*args: Any, **kwargs: Any) -> EvaluationDesignManifest:
    if args and not isinstance(args[0], str):
        return EvaluationDesignManifest.from_design(args[0])
    if len(args) < 4:
        raise TypeError('build_evaluation_manifest requires design_name, version, author, exports')
    design_name, version, author, exports = args[:4]
    return EvaluationDesignManifest.create(
        version=version,
        author=author,
        design_name=design_name,
        exports=list(exports),
        **kwargs,
    )


def validate_manifest(manifest: EvaluationDesignManifest) -> list[str]:
    return manifest.validate()


def merge_manifests(a: EvaluationDesignManifest, b: EvaluationDesignManifest) -> EvaluationDesignManifest:
    author = a.author if a.author == b.author else f'{a.author}, {b.author}'
    return EvaluationDesignManifest.create(
        version=a.version,
        author=author,
        chapter_ref=a.chapter_ref,
        theory_section=a.theory_section or b.theory_section,
        design_name=a.design_name or b.design_name,
        exports=_dedupe(a.exports + b.exports),
        description=a.description or b.description,
        clause_count=a.clause_count + b.clause_count,
        ablation_count=a.ablation_count + b.ablation_count,
        calibration_methods=_dedupe(a.calibration_methods + b.calibration_methods),
        tags=_dedupe(a.tags + b.tags),
    )


__all__ = [
    'EvaluationDesignManifest',
    'EvaluationManifestBuilder',
    'EvaluationManifestRegistry',
    'build_evaluation_manifest',
    'validate_manifest',
    'merge_manifests',
]
