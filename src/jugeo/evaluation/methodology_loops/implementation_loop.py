"""
Implementation Loop — JuGeo Methodology Loops Package (s02)

This module implements the implementation loop of the JuGeo evaluation
methodology.  An *implementation loop* is an iterative procedure that
takes formal specifications (produced by the formalization loop) and
produces concrete implementations together with test suites.  Each
iteration applies an :class:`Implementer` to a list of spec texts,
constructs a test suite via :class:`TestSuiteBuilder`, and assesses
coverage with :class:`CoverageAnalyzer`.  The loop continues until every
implementation reaches the required coverage level or the maximum number
of iterations is exhausted.

Design principles
-----------------
* **Immutable results** – :class:`ImplementationResult` is a frozen
  dataclass so the loop history is always auditable and reproducible.
* **Pluggable target languages** – the ``target_language`` parameter
  selects the output programming language (Python by default; Rust, Haskell,
  OCaml, and TypeScript are also supported stubs).
* **Structured diagnostics** – the runner produces a rich summary dictionary
  capturing per-iteration coverage trends and build statuses.

copilot: shared-core marker
Theory reference: theory2.tex Ch62
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    "ImplementationResult",
    "Implementer",
    "TestSuiteBuilder",
    "CoverageAnalyzer",
    "ImplementationLoopRunner",
    "run_implementation_loop",
    "measure_coverage",
]

# ---------------------------------------------------------------------------
# Optional JuGeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase,
        LoopStatus,
        TransitionKind,
        LoopState,
        LoopTransition,
        MethodologyConfig,
        LoopDiagnostics,
        MethodologyLoop,
        FormalizationLoop,
        ImplementationLoop,
        FalsificationLoop,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level constants and helpers
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGES = frozenset(
    {"python", "rust", "haskell", "ocaml", "typescript", "java", "cpp"}
)

_BUILD_STATUS_OK = "ok"
_BUILD_STATUS_ERROR = "error"
_BUILD_STATUS_PENDING = "pending"

_SPEC_KEYWORDS: tuple[str, ...] = (
    "theorem",
    "lemma",
    "definition",
    "axiom",
    "require",
    "ensure",
    "invariant",
    "precondition",
    "postcondition",
    "forall",
    "exists",
    "constraint",
    "property",
    "type",
    "structure",
)

_COVERAGE_HINTS: tuple[str, ...] = (
    "if",
    "else",
    "while",
    "for",
    "try",
    "except",
    "assert",
    "raise",
    "return",
    "yield",
    "match",
    "case",
)


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _normalise_language(lang: str) -> str:
    """Return a canonical lower-cased language identifier.

    Raises :class:`ValueError` if the language is not in the supported set.
    """
    norm = lang.strip().lower()
    if norm not in _SUPPORTED_LANGUAGES:
        supported = ", ".join(sorted(_SUPPORTED_LANGUAGES))
        raise ValueError(
            f"Unsupported target language {lang!r}. "
            f"Supported: {supported}"
        )
    return norm


def _count_spec_clauses(spec_text: str) -> int:
    """Count the number of top-level specification clauses in *spec_text*.

    A clause is any line containing a recognised specification keyword.

    Parameters
    ----------
    spec_text:
        Formal specification text.

    Returns
    -------
    int
        Number of detected clauses (≥ 0).
    """
    count = 0
    for line in spec_text.splitlines():
        lower = line.lower()
        if any(kw in lower for kw in _SPEC_KEYWORDS):
            count += 1
    return count


# ---------------------------------------------------------------------------
# ImplementationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class ImplementationResult:
    """Immutable record produced by one implementation attempt.

    Attributes
    ----------
    result_id:
        Globally unique identifier for this result (UUID4 string).
    spec_id:
        The identifier of the formal specification that was implemented.
    implementation_text:
        The generated implementation source code.
    test_suite:
        Immutable tuple of test-case strings generated for this implementation.
    coverage:
        Estimated test coverage ratio in [0, 1].
    build_status:
        One of ``"ok"``, ``"error"``, or ``"pending"``.
    warnings:
        Immutable tuple of human-readable warning messages.
    created_at:
        Unix timestamp (UTC) at which this result was created.
    """

    result_id: str
    spec_id: str
    implementation_text: str
    test_suite: tuple[str, ...]
    coverage: float
    build_status: str
    warnings: tuple[str, ...]
    created_at: float
    _correctness_score: float

    def __init__(
        self,
        code: Optional[str] = None,
        coverage_score: float = 0.0,
        correctness_score: float = 0.0,
        *,
        result_id: Optional[str] = None,
        spec_id: str = "",
        implementation_text: Optional[str] = None,
        test_suite: Sequence[str] = (),
        coverage: Optional[float] = None,
        build_status: str = _BUILD_STATUS_PENDING,
        warnings: Sequence[str] = (),
        created_at: Optional[float] = None,
    ) -> None:
        source = implementation_text if implementation_text is not None else (code or "")
        effective_coverage = coverage_score if coverage is None else coverage
        valid_statuses = {_BUILD_STATUS_OK, _BUILD_STATUS_ERROR, _BUILD_STATUS_PENDING}
        if build_status not in valid_statuses:
            build_status = _BUILD_STATUS_PENDING
        object.__setattr__(self, "result_id", result_id or _uid())
        object.__setattr__(self, "spec_id", spec_id or _uid())
        object.__setattr__(self, "implementation_text", source)
        object.__setattr__(self, "test_suite", tuple(test_suite))
        object.__setattr__(self, "coverage", _clamp(effective_coverage, 0.0, 1.0))
        object.__setattr__(self, "build_status", build_status)
        object.__setattr__(self, "warnings", tuple(warnings))
        object.__setattr__(self, "created_at", _utcnow() if created_at is None else float(created_at))
        object.__setattr__(self, "_correctness_score", _clamp(correctness_score, 0.0, 1.0))

    @property
    def code(self) -> str:
        return self.implementation_text

    @property
    def coverage_score(self) -> float:
        return self.coverage

    @property
    def correctness_score(self) -> float:
        if self._correctness_score > 0.0:
            return self._correctness_score
        if not self.implementation_text.strip():
            return 0.0
        return 1.0 if self.build_status == _BUILD_STATUS_OK else 0.5 if self.build_status == _BUILD_STATUS_PENDING else 0.0

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        spec_id: str,
        implementation_text: str,
        test_suite: Sequence[str],
        coverage: float,
        build_status: str = _BUILD_STATUS_PENDING,
        warnings: Sequence[str] = (),
    ) -> "ImplementationResult":
        """Construct a new :class:`ImplementationResult` with a fresh UUID.

        Parameters
        ----------
        spec_id:
            Identifier of the source specification.
        implementation_text:
            Generated implementation source code.
        test_suite:
            Sequence of test-case strings.
        coverage:
            Estimated coverage ratio in [0, 1].
        build_status:
            Build status string (default ``"pending"``).
        warnings:
            Optional sequence of warning strings.

        Returns
        -------
        ImplementationResult
            A freshly constructed, immutable result object.
        """
        return cls(
            result_id=_uid(),
            spec_id=spec_id,
            implementation_text=implementation_text,
            test_suite=tuple(test_suite),
            coverage=coverage,
            build_status=build_status,
            warnings=tuple(warnings),
            created_at=_utcnow(),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this result to a JSON string.

        Returns
        -------
        str
            UTF-8 JSON representation of the result.
        """
        return json.dumps(
            {
                "result_id": self.result_id,
                "spec_id": self.spec_id,
                "code": self.code,
                "implementation_text": self.implementation_text,
                "coverage_score": self.coverage_score,
                "correctness_score": self.correctness_score,
                "test_suite": list(self.test_suite),
                "coverage": self.coverage,
                "build_status": self.build_status,
                "warnings": list(self.warnings),
                "created_at": self.created_at,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "ImplementationResult":
        """Deserialise an :class:`ImplementationResult` from a JSON string.

        Parameters
        ----------
        data:
            A JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        ImplementationResult
            The reconstructed result object.

        Raises
        ------
        ValueError
            If the JSON is missing required keys.
        """
        obj = json.loads(data)
        required = {
            "result_id", "spec_id", "implementation_text",
            "test_suite", "coverage", "build_status",
            "warnings", "created_at",
        }
        missing = required - obj.keys()
        if missing:
            raise ValueError(f"Missing keys in JSON: {missing!r}")
        return cls(
            result_id=obj["result_id"],
            spec_id=obj["spec_id"],
            implementation_text=obj["implementation_text"],
            test_suite=tuple(obj["test_suite"]),
            coverage=float(obj["coverage"]),
            correctness_score=float(obj.get("correctness_score", 0.0)),
            build_status=obj["build_status"],
            warnings=tuple(obj["warnings"]),
            created_at=float(obj["created_at"]),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a brief human-readable summary of this result.

        Returns
        -------
        str
            One-line summary string.
        """
        short_id = self.result_id[:8]
        return (
            f"ImplementationResult({short_id}) "
            f"spec={self.spec_id[:8]} "
            f"coverage={self.coverage:.3f} "
            f"build={self.build_status} "
            f"tests={len(self.test_suite)} "
            f"warnings={len(self.warnings)}"
        )

    def is_passing(self) -> bool:
        """Return ``True`` if the build status is ``"ok"`` and coverage ≥ 0.8.

        Returns
        -------
        bool
        """
        return self.build_status == _BUILD_STATUS_OK and self.coverage >= 0.8

    def is_acceptable(
        self,
        coverage_threshold: float = 0.8,
        correctness_threshold: float = 0.75,
    ) -> bool:
        return (
            self.coverage_score >= coverage_threshold
            and self.correctness_score >= correctness_threshold
        )

    def quality_score(self) -> float:
        """Compute an aggregate quality score for this result.

        The quality score is based on coverage and penalised by 2 % per
        warning (capped at 50 %) and by 20 % for a failed build.

        Returns
        -------
        float
            Quality score in [0, 1].
        """
        base = (self.coverage + self.correctness_score) / 2.0
        warn_penalty = _clamp(0.02 * len(self.warnings), 0.0, 0.5)
        build_penalty = 0.20 if self.build_status == _BUILD_STATUS_ERROR else 0.0
        return _clamp(base * (1.0 - warn_penalty) - build_penalty, 0.0, 1.0)

    def render_tex(self) -> str:
        """Render a LaTeX snippet describing this result.

        Returns
        -------
        str
            LaTeX source snippet.
        """
        short = self.result_id[:8]
        lines = [
            f"\\paragraph{{Implementation result \\texttt{{{short}}}}}",
            f"Spec: \\texttt{{{self.spec_id[:8]}}}.",
            f"Coverage: ${self.coverage:.3f}$.",
            f"Build status: \\texttt{{{self.build_status}}}.",
            f"Tests: ${len(self.test_suite)}$.",
        ]
        if self.warnings:
            warn_list = ", ".join(f"\\textit{{{w}}}" for w in self.warnings)
            lines.append(f"Warnings: {warn_list}.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Implementer
# ---------------------------------------------------------------------------


class Implementer:
    """Transform formal specifications into executable implementations.

    An :class:`Implementer` applies heuristic source-code synthesis to
    convert formal specification texts into stub implementations in a chosen
    target programming language.  Like :class:`~formalization_loop.Formalizer`,
    it does not invoke an external compiler at runtime; instead it applies
    lightweight text-analysis heuristics to produce scored
    :class:`ImplementationResult` objects.

    Attributes
    ----------
    target_language : str
        Target programming language (e.g. ``"python"``).
    implementation_registry : dict
        Maps *impl_id* strings to :class:`ImplementationResult` objects.
    build_log : list
        Ordered list of build-log dictionaries for all registered impls.
    """

    def __init__(self, target_language: str = "python") -> None:
        """Initialise the implementer.

        Parameters
        ----------
        target_language:
            Target programming language.  Defaults to ``"python"``.

        Raises
        ------
        ValueError
            If *target_language* is not in the supported set.
        """
        self.target_language: str = _normalise_language(target_language)
        self.implementation_registry: dict[str, ImplementationResult] = {}
        self.build_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def implement(
        self,
        spec_text: str,
        context: dict[str, Any] | None = None,
    ) -> ImplementationResult:
        """Synthesise an implementation from *spec_text*.

        The synthesis pipeline proceeds as follows:

        1. **Coverage scoring** – :meth:`_score_coverage` estimates the ratio
           of spec clauses that would be exercised by a naive implementation.
        2. **Test generation** – :meth:`_generate_tests` produces stub test
           cases that exercise the identified clauses.
        3. **Build checking** – :meth:`_check_build` performs a lightweight
           static validation of the generated implementation stub.
        4. **Warning collection** – structural deficiencies discovered during
           the above steps are collected as warning strings.
        5. **Result construction** – a new :class:`ImplementationResult` is
           created, appended to the registry, and returned.

        Parameters
        ----------
        spec_text:
            Formal specification text to implement.
        context:
            Optional context dictionary.  Recognised keys:

            ``"spec_id"`` (str)
                Explicit spec identifier; a UUID is generated if absent.
            ``"language_hints"`` (list of str)
                Language-specific hints influencing stub generation.

        Returns
        -------
        ImplementationResult
            An immutable record containing the produced implementation.
        """
        ctx = context or {}
        spec_id = ctx.get("spec_id", _uid())
        coverage = self._score_coverage("", spec_text)
        tests = self._generate_tests("", spec_text)
        impl_text = self._build_impl_stub(spec_text, ctx)
        build_status = self._check_build(impl_text)
        warnings: list[str] = []
        if coverage < 0.5:
            warnings.append("Low coverage estimate — many spec clauses may be untested.")
        if build_status == _BUILD_STATUS_ERROR:
            warnings.append("Build check reported syntax or structural errors.")
        if not tests:
            warnings.append("No test cases could be generated for this spec.")
        result = ImplementationResult.create(
            spec_id=spec_id,
            implementation_text=impl_text,
            test_suite=tests,
            coverage=coverage,
            build_status=build_status,
            warnings=warnings,
        )
        self.implementation_registry[result.result_id] = result
        return result

    def get_history(self) -> list[ImplementationResult]:
        """Return the implementation history in insertion order."""
        return list(self.implementation_registry.values())

    def clear_history(self) -> None:
        """Clear registered implementations and build log history."""
        self.implementation_registry.clear()
        self.build_log.clear()

    def batch_implement(
        self, specs: list[str]
    ) -> list[ImplementationResult]:
        """Implement a list of spec texts.

        Parameters
        ----------
        specs:
            List of formal specification texts.

        Returns
        -------
        list[ImplementationResult]
            One result per input spec, in the same order.
        """
        return [self.implement(s) for s in specs]

    def register_implementation(
        self, impl_id: str, result: ImplementationResult
    ) -> None:
        """Store *result* under *impl_id* in the implementation registry.

        Parameters
        ----------
        impl_id:
            Unique identifier key for the implementation.
        result:
            The :class:`ImplementationResult` to register.
        """
        self.implementation_registry[impl_id] = result

    def get_implementation(
        self, impl_id: str
    ) -> ImplementationResult | None:
        """Retrieve an implementation by its identifier.

        Parameters
        ----------
        impl_id:
            The implementation identifier.

        Returns
        -------
        ImplementationResult | None
        """
        return self.implementation_registry.get(impl_id)

    def list_implementations(self) -> list[str]:
        """Return all registered implementation identifiers.

        Returns
        -------
        list[str]
        """
        return list(self.implementation_registry.keys())

    def build(self, impl_id: str) -> ImplementationResult:
        """Build an implementation by ID or directly from a spec string.

        Parameters
        ----------
        impl_id:
            The implementation to build-check.

        Returns
        -------
        dict
            Build log entry with keys ``"impl_id"``, ``"status"``,
            ``"timestamp"``.

        Raises
        ------
        KeyError
            If *impl_id* is not registered.
        """
        result = self.implementation_registry.get(impl_id)
        if result is None:
            return self.implement(impl_id)
        status = self._check_build(result.implementation_text)
        entry: dict[str, Any] = {
            "impl_id": impl_id,
            "status": status,
            "timestamp": _utcnow(),
        }
        self.build_log.append(entry)
        return ImplementationResult(
            result_id=result.result_id,
            spec_id=result.spec_id,
            implementation_text=result.implementation_text,
            test_suite=result.test_suite,
            coverage=result.coverage,
            correctness_score=result.correctness_score,
            build_status=status,
            warnings=result.warnings,
            created_at=result.created_at,
        )

    def rebuild_all(self) -> list[ImplementationResult]:
        """Rebuild all registered implementations.

        Returns
        -------
        list[dict]
            List of build log entries, one per registered implementation.
        """
        return [self.build(impl_id) for impl_id in list(self.implementation_registry.keys())]

    def summarize(self) -> str:
        """Return a brief summary of the implementer's state.

        Returns
        -------
        str
        """
        return (
            f"Implementer(lang={self.target_language}, "
            f"registered={len(self.implementation_registry)}, "
            f"builds={len(self.build_log)})"
        )

    def export_all(self, fmt: str = "json") -> str:
        """Serialise all registered implementations.

        Parameters
        ----------
        fmt:
            Output format (only ``"json"`` supported).

        Returns
        -------
        str
            JSON array of implementation result objects.
        """
        if fmt != "json":
            raise ValueError(f"Unsupported export format: {fmt!r}")
        items = [json.loads(r.to_json()) for r in self.implementation_registry.values()]
        return json.dumps(items, indent=2)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_coverage(
        self, impl_text: str, spec_text: str
    ) -> float:
        """Estimate coverage of *spec_text* clauses by *impl_text*.

        The heuristic counts how many spec keywords also appear in the
        implementation text.  If the implementation is empty (stub generation
        mode), a conservative estimate based solely on spec richness is used.

        Parameters
        ----------
        impl_text:
            Generated implementation source code (may be empty).
        spec_text:
            Formal specification text.

        Returns
        -------
        float
            Coverage estimate in [0, 1].
        """
        clause_count = _count_spec_clauses(spec_text)
        if clause_count == 0:
            return 0.50
        if not impl_text:
            return _clamp(0.40 + 0.02 * min(clause_count, 10), 0.0, 1.0)
        lower_impl = impl_text.lower()
        hits = sum(1 for kw in _COVERAGE_HINTS if kw in lower_impl)
        return _clamp(0.40 + 0.04 * hits, 0.0, 1.0)

    def _generate_tests(
        self, impl_text: str, spec_text: str
    ) -> list[str]:
        """Generate stub test cases based on *spec_text* clauses.

        Each detected clause yields one ``def test_...`` stub function.

        Parameters
        ----------
        impl_text:
            Implementation source code (may be empty).
        spec_text:
            Formal specification text.

        Returns
        -------
        list[str]
            List of test-function stub strings.
        """
        tests: list[str] = []
        for i, line in enumerate(spec_text.splitlines(), 1):
            lower = line.lower()
            if any(kw in lower for kw in _SPEC_KEYWORDS):
                test_name = re.sub(r"[^a-z0-9_]", "_", lower[:40]).strip("_")
                tests.append(
                    f"def test_{test_name}_{i}():\n"
                    f"    # Stub for: {line.strip()!r}\n"
                    f"    pass"
                )
        return tests

    def _check_build(self, impl_text: str) -> str:
        """Perform a lightweight static build-check on *impl_text*.

        Returns ``"ok"`` if no obvious issues are found, ``"error"`` otherwise.

        Parameters
        ----------
        impl_text:
            Implementation source code text.

        Returns
        -------
        str
            ``"ok"`` or ``"error"``.
        """
        if not impl_text.strip():
            return _BUILD_STATUS_ERROR
        # Check balanced braces/brackets/parens
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[str] = []
        for ch in impl_text:
            if ch in pairs:
                stack.append(pairs[ch])
            elif ch in pairs.values():
                if not stack or stack[-1] != ch:
                    return _BUILD_STATUS_ERROR
                stack.pop()
        if stack:
            return _BUILD_STATUS_ERROR
        return _BUILD_STATUS_OK

    def _build_impl_stub(
        self, spec_text: str, context: dict[str, Any]
    ) -> str:
        """Synthesise a stub implementation from *spec_text*.

        Parameters
        ----------
        spec_text:
            Formal specification text.
        context:
            Context dictionary (may contain ``"language_hints"``).

        Returns
        -------
        str
            Stub implementation source code.
        """
        lang = self.target_language
        hints = context.get("language_hints", [])
        lines: list[str] = [f"# Generated stub ({lang})"]
        for i, kw in enumerate(_SPEC_KEYWORDS[:5], 1):
            if kw in spec_text.lower():
                if lang == "python":
                    lines.append(f"\ndef impl_{kw}_{i}():\n    \"\"\"Implements {kw!r} clause.\"\"\"\n    pass")
                elif lang == "rust":
                    lines.append(f"\nfn impl_{kw}_{i}() {{\n    // Implements {kw!r} clause.\n}}")
                else:
                    lines.append(f"\n-- {kw} clause {i}")
        if hints:
            lines.append(f"\n# Hints: {', '.join(hints)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TestSuiteBuilder
# ---------------------------------------------------------------------------


class TestSuiteBuilder:
    """Build and manage test suites for implementation results.

    :class:`TestSuiteBuilder` generates, stores, and exports test suites
    that correspond to :class:`ImplementationResult` objects.  It supports
    a template registry so that custom test scaffolding can be injected.

    Attributes
    ----------
    test_framework : str
        The target test framework (e.g. ``"pytest"``).
    suite_registry : dict
        Maps suite IDs to lists of test-case strings.
    template_registry : dict
        Maps template names to template strings.
    """

    def __init__(self, test_framework: str = "pytest") -> None:
        """Initialise the builder.

        Parameters
        ----------
        test_framework:
            Target test framework name.  Defaults to ``"pytest"``.
        """
        self.test_framework: str = test_framework
        self.suite_registry: dict[str, list[str]] = {}
        self.template_registry: dict[str, str] = {}

    def build_suite(
        self, result: ImplementationResult | str
    ) -> list[str]:
        """Build a test suite from an :class:`ImplementationResult`.

        The suite is derived from the ``test_suite`` field of *result* and
        optionally wrapped in a framework-specific header.  The produced
        suite is stored in :attr:`suite_registry` under the result's ID.

        Parameters
        ----------
        result:
            The implementation result to build a suite for.

        Returns
        -------
        list[str]
            List of test-case strings.
        """
        if isinstance(result, str):
            suite = self.build_from_spec(result, "")
            suite_id = _uid()
        else:
            suite = list(result.test_suite)
            suite_id = result.result_id
        if self.test_framework == "pytest":
            header = f"# pytest suite for result {suite_id[:8]}"
            suite = [header] + suite
        self.suite_registry[suite_id] = suite
        return suite

    def build_from_spec(
        self, spec_text: str, impl_text: str = ""
    ) -> list[str]:
        """Build test cases directly from a spec/impl pair.

        Parameters
        ----------
        spec_text:
            Formal specification text.
        impl_text:
            Implementation source code.

        Returns
        -------
        list[str]
            List of generated test-case strings.
        """
        tests: list[str] = []
        for i, line in enumerate(spec_text.splitlines(), 1):
            lower = line.lower()
            if any(kw in lower for kw in _SPEC_KEYWORDS):
                safe = re.sub(r"[^a-z0-9_]", "_", lower[:40]).strip("_")
                tests.append(
                    f"def test_{safe}_{i}():\n    # spec: {line.strip()!r}\n    pass"
                )
        return tests

    def register_template(self, name: str, template: str) -> None:
        """Register a named test template.

        Parameters
        ----------
        name:
            Template identifier.
        template:
            Template string (may contain ``{test_name}``, ``{spec_line}``
            placeholders).
        """
        self.template_registry[name] = template

    def get_template(self, name: str) -> str | None:
        """Return a registered template by name."""
        return self.template_registry.get(name)

    def register_suite(self, suite_id: str, suite: list[str]) -> None:
        """Register an externally-built suite."""
        self.suite_registry[suite_id] = list(suite)

    def add_test(self, suite_id: str | list[str], test_code: str) -> None:
        """Append a test case to an existing suite.

        Parameters
        ----------
        suite_id:
            The suite to append to.  If it does not exist, a new suite
            containing only *test_code* is created.
        test_code:
            Test-case source string to append.
        """
        if isinstance(suite_id, list):
            suite_id.append(test_code)
            return
        if suite_id not in self.suite_registry:
            self.suite_registry[suite_id] = []
        self.suite_registry[suite_id].append(test_code)

    def get_suite(self, suite_id: str) -> list[str]:
        """Retrieve a test suite by its ID.

        Parameters
        ----------
        suite_id:
            Suite identifier.

        Returns
        -------
        list[str]
            The test-case strings, or an empty list if not found.
        """
        return self.suite_registry.get(suite_id, [])

    def export_suite(
        self, suite_id: str | list[str], path: str | None = None
    ) -> str:
        """Export a test suite to a string (and optionally to a file).

        Parameters
        ----------
        suite_id:
            Suite identifier.
        path:
            If provided, the suite is also written to this file path.

        Returns
        -------
        str
            The test suite as a single joined string.
        """
        suite = suite_id if isinstance(suite_id, list) else self.get_suite(suite_id)
        text = "\n\n".join(suite)
        if path is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def validate_suite(self, suite_id: str | list[str]) -> bool:
        """Validate a test suite for structural correctness.

        Parameters
        ----------
        suite_id:
            Suite identifier.

        Returns
        -------
        dict
            Keys: ``"suite_id"``, ``"test_count"``, ``"issues"``,
            ``"valid"``.
        """
        suite = suite_id if isinstance(suite_id, list) else self.get_suite(suite_id)
        issues: list[str] = []
        for i, test in enumerate(suite):
            if "def test_" not in test and "def Test" not in test:
                if not test.startswith("#"):
                    issues.append(f"Test case {i} does not define a test function.")
        return len(issues) == 0

    def test_count(self, suite_id: str | list[str]) -> int:
        """Return the number of tests in a suite."""
        suite = suite_id if isinstance(suite_id, list) else self.get_suite(suite_id)
        return len(suite)

    def summarize(self) -> str:
        """Return a brief summary of the builder's state.

        Returns
        -------
        str
        """
        return (
            f"TestSuiteBuilder(framework={self.test_framework}, "
            f"suites={len(self.suite_registry)}, "
            f"templates={len(self.template_registry)})"
        )


# ---------------------------------------------------------------------------
# CoverageAnalyzer
# ---------------------------------------------------------------------------


class CoverageAnalyzer:
    """Analyse and report on test coverage for implementation results.

    :class:`CoverageAnalyzer` maintains a history of coverage measurements
    and provides trend analysis and gap reporting for the implementation loop.

    Attributes
    ----------
    thresholds : dict
        Maps ``"min_coverage"`` to its threshold value.
    coverage_history : list
        Ordered list of coverage analysis dictionaries.
    """

    def __init__(self, min_coverage: float = 0.8) -> None:
        """Initialise the analyzer.

        Parameters
        ----------
        min_coverage:
            Minimum acceptable coverage ratio, in [0, 1].
        """
        self.thresholds: dict[str, float] = {
            "min_coverage": _clamp(min_coverage, 0.0, 1.0)
        }
        self.coverage_history: list[dict[str, Any]] = []

    def analyze(self, result: ImplementationResult) -> dict[str, Any]:
        """Analyse a single :class:`ImplementationResult`.

        Parameters
        ----------
        result:
            The result to analyse.

        Returns
        -------
        dict
            Keys: ``"result_id"``, ``"coverage"``, ``"sufficient"``,
            ``"gap"``, ``"quality_score"``, ``"analyzed_at"``.
        """
        gap = max(0.0, self.thresholds["min_coverage"] - result.coverage)
        summary = {
            "result_id": result.result_id,
            "coverage": result.coverage,
            "sufficient": result.coverage >= self.thresholds["min_coverage"],
            "gap": round(gap, 4),
            "quality_score": round(result.quality_score(), 4),
            "analyzed_at": _utcnow(),
        }
        self.coverage_history.append(summary)
        return summary

    def analyze_batch(
        self, results: list[ImplementationResult]
    ) -> list[dict[str, Any]]:
        """Analyse a list of results.

        Parameters
        ----------
        results:
            List of implementation results.

        Returns
        -------
        list[dict]
            One analysis summary per input result.
        """
        return [self.analyze(r) for r in results]

    def compute_coverage(
        self, impl_text: str | ImplementationResult, spec_text: str = ""
    ) -> float:
        """Compute a coverage estimate for an impl/spec pair.

        Parameters
        ----------
        impl_text:
            Implementation source code.
        spec_text:
            Formal specification text.

        Returns
        -------
        float
            Coverage estimate in [0, 1].
        """
        if isinstance(impl_text, ImplementationResult):
            return impl_text.coverage
        clause_count = _count_spec_clauses(spec_text)
        if clause_count == 0:
            return 0.5
        lower_impl = impl_text.lower()
        hits = sum(1 for kw in _COVERAGE_HINTS if kw in lower_impl)
        return _clamp(0.35 + 0.05 * hits, 0.0, 1.0)

    def is_sufficient(self, result: ImplementationResult) -> bool:
        """Return ``True`` if *result*'s coverage meets the minimum threshold.

        Parameters
        ----------
        result:
            The result to test.

        Returns
        -------
        bool
        """
        return result.coverage >= self.thresholds["min_coverage"]

    def gap_report(self, result: ImplementationResult) -> dict[str, Any]:
        """Produce a gap report for *result*.

        Returns
        -------
        dict
            Keys: ``"result_id"``, ``"coverage"``, ``"min_coverage"``,
            ``"gap"``, ``"gap_pct"``.
        """
        gap = max(0.0, self.thresholds["min_coverage"] - result.coverage)
        gap_pct = gap / self.thresholds["min_coverage"] * 100 if self.thresholds["min_coverage"] > 0 else 0.0
        return {
            "result_id": result.result_id,
            "coverage": result.coverage,
            "min_coverage": self.thresholds["min_coverage"],
            "gap": round(gap, 4),
            "gap_pct": round(gap_pct, 2),
        }

    def trend(self) -> list[float]:
        """Return coverage values in chronological order.

        Returns
        -------
        list[float]
            Coverage history as a list of floats.
        """
        return [h["coverage"] for h in self.coverage_history]

    def summarize(self) -> str:
        """Return a brief summary of the analyzer's state.

        Returns
        -------
        str
        """
        n = len(self.coverage_history)
        mean_cov = sum(self.trend()) / n if n else 0.0
        return (
            f"CoverageAnalyzer(min={self.thresholds['min_coverage']:.2f}, "
            f"analyses={n}, mean_coverage={mean_cov:.3f})"
        )


# ---------------------------------------------------------------------------
# ImplementationLoopRunner
# ---------------------------------------------------------------------------


class ImplementationLoopRunner:
    """Orchestrate the complete implementation loop.

    :class:`ImplementationLoopRunner` iteratively applies the
    :class:`Implementer`, :class:`TestSuiteBuilder`, and
    :class:`CoverageAnalyzer` until all generated implementations achieve
    sufficient coverage or the maximum iteration count is reached.

    Attributes
    ----------
    config : dict
        Loop configuration dictionary.
    implementer : Implementer
        The implementer used during the loop.
    builder : TestSuiteBuilder
        The test-suite builder used to generate test suites.
    analyzer : CoverageAnalyzer
        The coverage analyzer used to evaluate each iteration.
    loop_state : dict
        Mutable state dictionary tracking loop progress.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        min_coverage: float = 0.8,
        target_language: str = "python",
    ) -> None:
        """Initialise the loop runner.

        Parameters
        ----------
        max_iterations:
            Maximum number of refinement iterations before giving up.
        min_coverage:
            Minimum required coverage ratio, in [0, 1].
        target_language:
            Target programming language for implementation synthesis.
        """
        self.config: dict[str, Any] = {
            "max_iterations": max_iterations,
            "min_coverage": min_coverage,
            "target_language": target_language,
        }
        self.implementer = Implementer(target_language=target_language)
        self.builder = TestSuiteBuilder()
        self.analyzer = CoverageAnalyzer(min_coverage=min_coverage)
        self.loop_state: dict[str, Any] = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "started_at": None,
            "finished_at": None,
        }

    @property
    def max_iterations(self) -> int:
        return int(self.config["max_iterations"])

    def run(
        self,
        specs: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the full implementation loop.

        Parameters
        ----------
        specs:
            List of formal specification texts to implement.
        context:
            Optional context dictionary forwarded to :meth:`Implementer.implement`.

        Returns
        -------
        dict
            Summary with keys ``"converged"``, ``"iterations_used"``,
            ``"final_results"``, ``"coverage_trend"``, ``"started_at"``,
            ``"finished_at"``.
        """
        self.loop_state["status"] = "running"
        self.loop_state["started_at"] = _utcnow()
        self.loop_state["iteration"] = 0
        self.loop_state["converged"] = False
        all_results: list[dict[str, Any]] = []
        max_it = self.config["max_iterations"]
        for it in range(1, max_it + 1):
            self.loop_state["iteration"] = it
            try:
                iteration_result = self.run_single_iteration(specs, it)
            except Exception as exc:
                iteration_result = self.handle_failure(exc, it)
            all_results.append(iteration_result)
            if self.check_convergence(all_results):
                self.loop_state["converged"] = True
                break
        self.loop_state["status"] = "done"
        self.loop_state["finished_at"] = _utcnow()
        final = all_results[-1] if all_results else {}
        self.loop_state["results"] = list(final.get("results", [])) if final else []
        return {
            "converged": self.loop_state["converged"],
            "iterations": self.loop_state["iteration"],
            "iterations_used": self.loop_state["iteration"],
            "results": list(final.get("results", [])) if final else [],
            "final_results": final,
            "coverage_trend": self.analyzer.trend(),
            "started_at": self.loop_state["started_at"],
            "finished_at": self.loop_state["finished_at"],
        }

    def run_single_iteration(
        self,
        specs: list[str],
        iteration: int,
    ) -> dict[str, Any]:
        """Execute one iteration of the implementation loop.

        Parameters
        ----------
        specs:
            Spec texts to implement in this iteration.
        iteration:
            Current iteration number (1-based).

        Returns
        -------
        dict
            Keys: ``"iteration"``, ``"results"``, ``"analysis"``,
            ``"all_sufficient"``.
        """
        results = self.implementer.batch_implement(specs)
        for r in results:
            self.builder.build_suite(r)
        analyses = self.analyzer.analyze_batch(results)
        all_ok = all(a["sufficient"] for a in analyses)
        return {
            "iteration": iteration,
            "results": list(results),
            "analysis": analyses,
            "all_sufficient": all_ok,
        }

    def check_convergence(
        self, iteration_results: list[Any]
    ) -> bool:
        """Return ``True`` if the latest iteration achieved full coverage.

        Parameters
        ----------
        iteration_results:
            All iteration result dictionaries produced so far.

        Returns
        -------
        bool
        """
        if not iteration_results:
            return False
        if all(isinstance(result, ImplementationResult) for result in iteration_results):
            return all(self.analyzer.is_sufficient(result) for result in iteration_results)
        return bool(iteration_results[-1].get("all_sufficient", False))

    def handle_failure(
        self, error: Exception, iteration: int
    ) -> dict[str, Any]:
        """Produce a failure record for a failed iteration.

        Parameters
        ----------
        error:
            The exception raised.
        iteration:
            The iteration number.

        Returns
        -------
        dict
        """
        return {
            "iteration": iteration,
            "error": str(error),
            "all_sufficient": False,
            "results": [],
            "analysis": [],
        }

    def get_state(self) -> dict[str, Any]:
        """Return a copy of the current loop state.

        Returns
        -------
        dict
        """
        state = dict(self.loop_state)
        state["iterations_completed"] = state.get("iteration", 0)
        return state

    def reset(self) -> None:
        """Reset the runner to its initial idle state."""
        self.implementer.implementation_registry.clear()
        self.implementer.build_log.clear()
        self.builder.suite_registry.clear()
        self.analyzer.coverage_history.clear()
        self.loop_state = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "results": [],
            "started_at": None,
            "finished_at": None,
        }

    def summarize(self) -> str:
        """Return a brief summary of the runner.

        Returns
        -------
        str
        """
        cfg = self.config
        st = self.loop_state
        return (
            f"ImplementationLoopRunner("
            f"max_iter={cfg['max_iterations']}, "
            f"lang={cfg['target_language']}, "
            f"status={st['status']}, "
            f"converged={st['converged']})"
        )

    def export_results(self) -> dict[str, Any]:
        """Export the current runner state in a serialisable structure."""
        return {
            "config": dict(self.config),
            "state": self.get_state(),
            "coverage_trend": self.analyzer.trend(),
            "results": list(self.loop_state.get("results", [])),
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def run_implementation_loop(
    specs: list[str],
    max_iterations: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a full implementation loop and return the result summary.

    This is the primary entry-point for the implementation loop.  It creates
    an :class:`ImplementationLoopRunner` configured with *max_iterations* and
    any additional keyword arguments, then invokes :meth:`~ImplementationLoopRunner.run`
    on the provided *specs*.

    Algorithm
    ---------
    1. Initialise :class:`Implementer`, :class:`TestSuiteBuilder`, and
       :class:`CoverageAnalyzer` with the supplied configuration.
    2. For each iteration ``i`` from 1 to *max_iterations*:

       a. Synthesise an implementation for each spec in *specs*.
       b. Build a test suite for each implementation.
       c. Analyse coverage for each implementation.
       d. If **all** implementations have sufficient coverage, declare
          convergence and halt.

    3. Return a comprehensive summary dictionary.

    Convergence criteria
    --------------------
    The loop converges when every :class:`ImplementationResult` produced in
    the current iteration has ``coverage >= min_coverage`` (default 0.8).

    Parameters
    ----------
    specs:
        List of formal specification texts to implement.
    max_iterations:
        Maximum number of refinement iterations.
    **kwargs:
        Additional keyword arguments forwarded to
        :class:`ImplementationLoopRunner`.  Recognised keys:
        ``min_coverage`` (float, default 0.8),
        ``target_language`` (str, default ``"python"``).

    Returns
    -------
    dict
        Keys: ``"converged"``, ``"iterations_used"``, ``"final_results"``,
        ``"coverage_trend"``, ``"started_at"``, ``"finished_at"``.

    Examples
    --------
    >>> result = run_implementation_loop(
    ...     ["-- theorem trivial : True := trivial"],
    ...     max_iterations=3,
    ...     min_coverage=0.6,
    ... )
    >>> "converged" in result
    True
    """
    if hasattr(specs, "loop_id") and hasattr(specs, "state"):
        loop = deepcopy(specs)
        if hasattr(loop, "updated_at"):
            loop.updated_at = time.time()
        return loop
    runner = ImplementationLoopRunner(
        max_iterations=max_iterations,
        **kwargs,
    )
    return runner.run(specs)


def measure_coverage(
    impl_text: str | ImplementationResult,
    spec_text: str = "",
    min_coverage: float = 0.8,
) -> float:
    """Measure coverage of *spec_text* by *impl_text*.

    This convenience function creates a :class:`CoverageAnalyzer` and
    computes the coverage ratio for the supplied implementation/spec pair,
    returning a concise summary dictionary.

    Parameters
    ----------
    impl_text:
        Implementation source code text.
    spec_text:
        Formal specification text.
    min_coverage:
        Minimum required coverage ratio used to determine sufficiency.

    Returns
    -------
    float
        Coverage ratio in ``[0, 1]``.

    Examples
    --------
    >>> result = measure_coverage(
    ...     "def foo(): pass",
    ...     "require foo is defined",
    ... )
    >>> "coverage" in result
    True
    """
    analyzer = CoverageAnalyzer(min_coverage=min_coverage)
    return round(analyzer.compute_coverage(impl_text, spec_text), 4)
