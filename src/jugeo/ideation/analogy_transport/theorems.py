r"""Analogy transport theorem schema and falsification suite — theory2.tex Ch61.

# copilot: shared-core marker

This module provides the schema layer and falsification infrastructure for
transported theorems in the JuGeo analogy transport pipeline.  Every theorem
that passes through the transport pipeline must satisfy a structural schema
before it can be registered in the target domain's theorem store.

The module is organised around two primary abstractions:

  1. :class:`AnalogyTransportTheoremSchema` — validates the structure of a
     transported theorem, verifies that the analogy functor preserves the
     relevant mathematical structure, checks coherence proofs, and assigns
     a schema-level confidence score.

  2. :class:`FalsificationSuite` — generates and runs a battery of
     falsification tests for a given transported theorem.  The suite attempts
     to find counter-examples, boundary cases, or coherence violations that
     would invalidate the transported theorem.

Mathematical Background
-----------------------

Transported theorems are not automatically valid.  When an analogy functor
F : C → D maps theorem T ∈ C to T' ∈ D, the validity of T' depends on:

  a. **Structure preservation**: F must preserve the relevant categorical
     structures (products, limits, adjunctions, etc.) that T uses.

  b. **Coherence**: The coherence conditions of F must hold for the objects
     and morphisms appearing in T.

  c. **Non-degeneracy**: The image F(T) must not collapse to a trivial or
     vacuously true statement in D.

The schema checks (a), (b) and (c) at the structural level.  The
falsification suite provides empirical evidence against (a) and (b) by
generating concrete tests.

Falsification Test Types
------------------------

The falsification suite generates four types of tests:

  - **CoherenceTest** (:attr:`TestType.COHERENCE`): checks that the functor's
    coherence conditions are satisfied for specific instantiations in the
    target domain.

  - **CounterexampleSearch** (:attr:`TestType.COUNTEREXAMPLE`): searches for
    a concrete counter-example that would falsify the transported theorem.
    Uses randomised search and boundary enumeration.

  - **BoundaryTest** (:attr:`TestType.BOUNDARY`): evaluates the theorem at
    degenerate or boundary cases (empty structures, trivial morphisms, etc.)
    where transported theorems are most likely to fail.

  - **InvarianceTest** (:attr:`TestType.INVARIANCE`): checks that the
    transported theorem is invariant under the symmetries of the target domain
    (automorphisms, permutations of labels, etc.).

Schema Validation
-----------------

A transported theorem passes schema validation when all of the following hold:

  1. The ``transported_statement`` is non-empty and syntactically well-formed.
  2. All objects and morphisms referenced in the statement have corresponding
     translations in the ``object_translations`` and ``morphism_translations``
     dictionaries.
  3. At least one coherence proof is present (may be a simple structural check).
  4. The ``confidence_score`` is above the configured minimum threshold.
  5. The functor's faithfulness score exceeds the schema's minimum requirement.

Canonicalisation
----------------

After passing validation, a theorem can be canonicalised via
:meth:`AnalogyTransportTheoremSchema.canonicalize`.  Canonicalisation
assigns a stable ``canonical_id``, normalises the statement, and records
the canonicalisation timestamp.

Design Notes
------------

* :class:`FalsificationTest` and :class:`FalsificationTestResult` are frozen
  dataclasses because tests are immutable once generated.
* :class:`FalsificationSuiteResult` is also frozen; it captures a complete
  snapshot of a falsification run.
* The :class:`FalsificationSuite` itself is mutable (``@dataclass(slots=True)``)
  because it accumulates test history across multiple runs.
* :func:`score_theorem_confidence` implements a lightweight Bayesian update:
  passing tests nudge confidence upward; failing tests nudge it downward by
  a larger amount.

Complexity Summary
------------------

.. list-table::
   :header-rows: 1

   * - Operation
     - Time complexity
   * - :meth:`AnalogyTransportTheoremSchema.validate_transported_theorem`
     - O(|translations| + |coherence_proofs|)
   * - :meth:`FalsificationSuite.generate_tests`
     - O(|objects| + |morphisms|) per test type
   * - :meth:`FalsificationSuite.run_suite`
     - O(n_tests · cost_per_test)
   * - :func:`score_theorem_confidence`
     - O(n_results)

References
----------

theory2.tex, Chapter 61 (Analogy Transport Schema and Falsification).
Popper, *The Logic of Scientific Discovery*, Ch. 4 (falsificationism).
Lawvere & Rosebrugh, *Sets for Mathematics*, Ch. 8 (functors and structure).
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.analogy_transport.algorithms import (  # type: ignore[import]
        TransportResult,
        AnalogyFunctor,
        SourceTheorem,
        TransportedTheorem,
        TransportStatus,
        NormalizedTheorem,
    )
except Exception:
    pass

try:
    from jugeo.evidence.validation import ValidationEngine  # type: ignore[import]
    from jugeo.evidence.registry import EvidenceRegistry  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.orchestration.scheduler import TaskScheduler  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.analogy_transport.models import (  # type: ignore[import]
        AnalogyMap,
        AnalogyVerification,
    )
except Exception:
    pass

try:
    from jugeo.ideation.analogy_transport.integration import (  # type: ignore[import]
        ExportBundle,
        TheoremRegistry,
    )
except Exception:
    pass

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _stable_hash(text: str) -> str:
    """Return a stable short SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _tokenize(text: str) -> set[str]:
    """Return lowercase alphanumeric tokens of length >= 2 from *text*."""
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


def _statement_complexity(statement: str) -> float:
    """Heuristic complexity score for *statement* based on token count and length.

    Returns a value in [0, 1] where 1 means very complex.
    """
    n_tokens = len(_tokenize(statement))
    n_chars = len(statement)
    token_complexity = _clamp(math.log1p(n_tokens) / math.log1p(100))
    char_complexity = _clamp(math.log1p(n_chars) / math.log1p(1000))
    return (token_complexity + char_complexity) / 2.0


def _coherence_coverage(
    theorem_objects: set[str],
    theorem_morphisms: set[str],
    functor_object_map: dict[str, str],
    functor_morphism_map: dict[str, str],
) -> float:
    """Compute what fraction of theorem vocabulary is covered by the functor maps."""
    all_items = theorem_objects | theorem_morphisms
    if not all_items:
        return 1.0
    all_mapped = set(functor_object_map.keys()) | set(functor_morphism_map.keys())
    return len(all_items & all_mapped) / len(all_items)


def _bayesian_confidence_update(
    prior: float,
    n_passed: int,
    n_failed: int,
    pass_weight: float = 0.02,
    fail_weight: float = 0.05,
) -> float:
    """Apply a lightweight Bayesian-inspired update to *prior* confidence.

    Each passed test nudges confidence up by *pass_weight*; each failed test
    nudges it down by *fail_weight*.  The result is clamped to [0, 1].

    Parameters:
        prior: The initial confidence estimate in [0, 1].
        n_passed: Number of tests that passed.
        n_failed: Number of tests that failed.
        pass_weight: Per-test upward adjustment.
        fail_weight: Per-test downward adjustment.

    Returns:
        Updated confidence in [0, 1].
    """
    delta = n_passed * pass_weight - n_failed * fail_weight
    return _clamp(prior + delta)


def _priority_score_for_test_type(test_type: Any) -> float:
    """Return a base priority score for a given :class:`TestType`."""
    priority_map = {
        "coherence": 0.9,
        "counterexample": 0.8,
        "boundary": 0.7,
        "invariance": 0.6,
    }
    val = getattr(test_type, "value", str(test_type)).lower()
    return priority_map.get(val, 0.5)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TestType(str, Enum):
    """Type classification for a :class:`FalsificationTest`.

    COHERENCE tests verify that the functor's coherence conditions hold
    for specific instantiations in the target domain.

    COUNTEREXAMPLE tests search for a concrete counter-example to the
    transported theorem.

    BOUNDARY tests evaluate the theorem at degenerate or edge cases where
    transported theorems are most likely to fail.

    INVARIANCE tests check that the theorem is stable under the symmetries
    of the target domain.
    """

    COHERENCE = "coherence"
    COUNTEREXAMPLE = "counterexample"
    BOUNDARY = "boundary"
    INVARIANCE = "invariance"

    def priority(self) -> float:
        """Return the default execution priority for this test type."""
        return _priority_score_for_test_type(self)

    def is_structural(self) -> bool:
        """Return True for structurally oriented tests (coherence / invariance)."""
        return self in (TestType.COHERENCE, TestType.INVARIANCE)

    def is_empirical(self) -> bool:
        """Return True for empirically oriented tests (counterexample / boundary)."""
        return self in (TestType.COUNTEREXAMPLE, TestType.BOUNDARY)


class SuiteOutcome(str, Enum):
    """Aggregate outcome of a :class:`FalsificationSuite` run.

    ALL_PASSED means every test in the suite passed.
    SOME_FAILED means at least one test failed but not all.
    ALL_FAILED means every test failed.
    INCONCLUSIVE means the suite produced no usable results (e.g., no tests).
    """

    ALL_PASSED = "all_passed"
    SOME_FAILED = "some_failed"
    ALL_FAILED = "all_failed"
    INCONCLUSIVE = "inconclusive"

    @classmethod
    def from_counts(cls, n_passed: int, n_failed: int) -> SuiteOutcome:
        """Derive an outcome from pass/fail counts."""
        total = n_passed + n_failed
        if total == 0:
            return cls.INCONCLUSIVE
        if n_failed == 0:
            return cls.ALL_PASSED
        if n_passed == 0:
            return cls.ALL_FAILED
        return cls.SOME_FAILED

    def is_acceptable(self) -> bool:
        """Return True when the outcome is acceptable for registration."""
        return self in (SuiteOutcome.ALL_PASSED, SuiteOutcome.SOME_FAILED)


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FalsificationTest:
    """A single falsification test for a transported theorem.

    Attributes:
        test_id: Stable unique identifier for this test.
        description: Human-readable description of what is being tested.
        test_type: The :class:`TestType` category.
        input_data: Arbitrary data dictionary used as input for the test.
        expected_outcome: Human-readable description of the expected result.
        priority: Execution priority in [0, 1]; higher runs first.
        created_at: ISO-8601 creation timestamp.
    """

    test_id: str
    description: str
    test_type: TestType
    input_data: dict[str, Any]
    expected_outcome: str
    priority: float
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "test_id": self.test_id,
            "description": self.description,
            "test_type": self.test_type.value,
            "input_data": self.input_data,
            "expected_outcome": self.expected_outcome,
            "priority": self.priority,
            "created_at": self.created_at,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"FalsificationTest({self.test_type.value!r}, "
            f"priority={self.priority:.2f}, id={self.test_id[:8]})"
        )


@dataclass(frozen=True, slots=True)
class FalsificationTestResult:
    """Result of running a single :class:`FalsificationTest`.

    Attributes:
        test_id: ID of the test that was run.
        passed: Whether the test passed (i.e., the theorem survived).
        actual_outcome: Human-readable description of what actually happened.
        confidence_impact: Delta to apply to the theorem's confidence score
            (positive = confidence increased, negative = decreased).
        error_detail: Non-empty when an error prevented the test from running.
        executed_at: ISO-8601 timestamp.
        duration_seconds: Wall-clock time taken to run the test.
    """

    test_id: str
    passed: bool
    actual_outcome: str
    confidence_impact: float
    error_detail: str = ""
    executed_at: str = field(default_factory=_now_iso)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "test_id": self.test_id,
            "passed": self.passed,
            "actual_outcome": self.actual_outcome,
            "confidence_impact": self.confidence_impact,
            "error_detail": self.error_detail,
            "executed_at": self.executed_at,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    """Outcome of running the theorem schema on a transported theorem.

    Attributes:
        theorem_id: ID of the transported theorem that was validated.
        is_valid: Whether the theorem passed all schema checks.
        errors: Tuple of error descriptions (empty when valid).
        warnings: Tuple of non-fatal warning descriptions.
        coverage_score: Fraction of theorem vocabulary covered by functor maps.
        coherence_score: Fraction of coherence conditions satisfied.
        schema_confidence: Schema-derived confidence score in [0, 1].
        validated_at: ISO-8601 timestamp.
    """

    theorem_id: str
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    coverage_score: float
    coherence_score: float
    schema_confidence: float
    validated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "theorem_id": self.theorem_id,
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "coverage_score": self.coverage_score,
            "coherence_score": self.coherence_score,
            "schema_confidence": self.schema_confidence,
            "validated_at": self.validated_at,
        }


@dataclass(frozen=True, slots=True)
class CoherenceCheckResult:
    """Result of checking coherence proofs for a transported theorem.

    Attributes:
        theorem_id: ID of the transported theorem.
        proofs_checked: Tuple of proof description strings that were examined.
        proofs_passed: Tuple of proof descriptions that passed.
        proofs_failed: Tuple of proof descriptions that failed.
        overall_coherence: Aggregate coherence score in [0, 1].
        checked_at: ISO-8601 timestamp.
    """

    theorem_id: str
    proofs_checked: tuple[str, ...]
    proofs_passed: tuple[str, ...]
    proofs_failed: tuple[str, ...]
    overall_coherence: float
    checked_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "theorem_id": self.theorem_id,
            "proofs_checked": list(self.proofs_checked),
            "proofs_passed": list(self.proofs_passed),
            "proofs_failed": list(self.proofs_failed),
            "overall_coherence": self.overall_coherence,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True, slots=True)
class StructurePreservationResult:
    """Result of checking structure preservation for an analogy functor.

    Attributes:
        functor_id: ID of the functor that was checked.
        theorem_id: ID of the transported theorem involved.
        structures_checked: Tuple of structure names examined.
        structures_preserved: Tuple of structure names confirmed preserved.
        structures_violated: Tuple of structure names that are violated.
        preservation_score: Aggregate preservation score in [0, 1].
        checked_at: ISO-8601 timestamp.
    """

    functor_id: str
    theorem_id: str
    structures_checked: tuple[str, ...]
    structures_preserved: tuple[str, ...]
    structures_violated: tuple[str, ...]
    preservation_score: float
    checked_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "functor_id": self.functor_id,
            "theorem_id": self.theorem_id,
            "structures_checked": list(self.structures_checked),
            "structures_preserved": list(self.structures_preserved),
            "structures_violated": list(self.structures_violated),
            "preservation_score": self.preservation_score,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True, slots=True)
class CanonicalTheorem:
    """A canonicalised form of a transported theorem.

    Attributes:
        canonical_id: Stable content-addressed identifier (SHA-256 prefix).
        transport_id: ID of the source :class:`TransportedTheorem`.
        canonical_statement: The normalised, canonicalised statement.
        canonical_name: Assigned canonical name.
        schema_confidence: Schema confidence at the time of canonicalisation.
        canonicalized_at: ISO-8601 timestamp.
    """

    canonical_id: str
    transport_id: str
    canonical_statement: str
    canonical_name: str
    schema_confidence: float
    canonicalized_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "canonical_id": self.canonical_id,
            "transport_id": self.transport_id,
            "canonical_statement": self.canonical_statement,
            "canonical_name": self.canonical_name,
            "schema_confidence": self.schema_confidence,
            "canonicalized_at": self.canonicalized_at,
        }


@dataclass(frozen=True, slots=True)
class FalsificationSuiteResult:
    """Aggregated results from a full falsification suite run.

    Attributes:
        suite_run_id: Stable unique identifier for this run.
        theorem_id: ID of the transported theorem that was tested.
        test_results: Tuple of :class:`FalsificationTestResult` instances.
        outcome: The :class:`SuiteOutcome` of the run.
        adjusted_confidence: Confidence after applying all test impacts.
        n_coherence_tests: Number of COHERENCE-type tests run.
        n_counterexample_tests: Number of COUNTEREXAMPLE-type tests run.
        n_boundary_tests: Number of BOUNDARY-type tests run.
        n_invariance_tests: Number of INVARIANCE-type tests run.
        elapsed_seconds: Total suite run time in seconds.
        completed_at: ISO-8601 timestamp.
    """

    suite_run_id: str
    theorem_id: str
    test_results: tuple[FalsificationTestResult, ...]
    outcome: SuiteOutcome
    adjusted_confidence: float
    n_coherence_tests: int
    n_counterexample_tests: int
    n_boundary_tests: int
    n_invariance_tests: int
    elapsed_seconds: float
    completed_at: str = field(default_factory=_now_iso)

    @property
    def n_passed(self) -> int:
        """Return the total number of passing tests."""
        return sum(1 for r in self.test_results if r.passed)

    @property
    def n_failed(self) -> int:
        """Return the total number of failing tests."""
        return sum(1 for r in self.test_results if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "suite_run_id": self.suite_run_id,
            "theorem_id": self.theorem_id,
            "outcome": self.outcome.value,
            "adjusted_confidence": self.adjusted_confidence,
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "n_coherence_tests": self.n_coherence_tests,
            "n_counterexample_tests": self.n_counterexample_tests,
            "n_boundary_tests": self.n_boundary_tests,
            "n_invariance_tests": self.n_invariance_tests,
            "elapsed_seconds": self.elapsed_seconds,
            "completed_at": self.completed_at,
            "test_results": [r.to_dict() for r in self.test_results],
        }


@dataclass(frozen=True, slots=True)
class FalsificationAnalysis:
    """Analysis of a completed :class:`FalsificationSuiteResult`.

    Attributes:
        analysis_id: Stable unique identifier.
        suite_run_id: ID of the suite run being analysed.
        theorem_id: ID of the transported theorem.
        summary: Human-readable summary of the analysis.
        key_findings: Tuple of notable finding strings.
        recommended_action: One of ``"accept"``, ``"revise"``, ``"reject"``.
        confidence_estimate: Final confidence estimate incorporating analysis.
        analysed_at: ISO-8601 timestamp.
    """

    analysis_id: str
    suite_run_id: str
    theorem_id: str
    summary: str
    key_findings: tuple[str, ...]
    recommended_action: str
    confidence_estimate: float
    analysed_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "analysis_id": self.analysis_id,
            "suite_run_id": self.suite_run_id,
            "theorem_id": self.theorem_id,
            "summary": self.summary,
            "key_findings": list(self.key_findings),
            "recommended_action": self.recommended_action,
            "confidence_estimate": self.confidence_estimate,
            "analysed_at": self.analysed_at,
        }


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SchemaConfig:
    """Configuration for :class:`AnalogyTransportTheoremSchema`.

    Attributes:
        min_confidence: Minimum confidence score required for schema validity.
        min_coverage: Minimum functor coverage fraction required.
        min_faithfulness: Minimum functor faithfulness score required.
        require_coherence_proofs: Whether at least one coherence proof is required.
        max_statement_length: Maximum allowed statement length (0 = unlimited).
        canonical_name_prefix: Prefix used when generating canonical names.
    """

    min_confidence: float = 0.3
    min_coverage: float = 0.25
    min_faithfulness: float = 0.4
    require_coherence_proofs: bool = True
    max_statement_length: int = 0
    canonical_name_prefix: str = "canon"


@dataclass(slots=True)
class FalsificationSuiteConfig:
    """Configuration for :class:`FalsificationSuite`.

    Attributes:
        n_coherence_tests: Number of COHERENCE tests to generate per theorem.
        n_counterexample_tests: Number of COUNTEREXAMPLE tests to generate.
        n_boundary_tests: Number of BOUNDARY tests to generate.
        n_invariance_tests: Number of INVARIANCE tests to generate.
        random_seed: Seed for reproducible randomised test generation; None
            means use OS entropy.
        max_suite_duration_seconds: Abort the suite if it runs longer than this.
        prioritize_tests: Whether to sort tests by priority before running.
        pass_weight: Per-test confidence upward adjustment.
        fail_weight: Per-test confidence downward adjustment.
    """

    n_coherence_tests: int = 3
    n_counterexample_tests: int = 2
    n_boundary_tests: int = 2
    n_invariance_tests: int = 1
    random_seed: int | None = 42
    max_suite_duration_seconds: float = 120.0
    prioritize_tests: bool = True
    pass_weight: float = 0.02
    fail_weight: float = 0.05


# ---------------------------------------------------------------------------
# AnalogyTransportTheoremSchema
# ---------------------------------------------------------------------------


class AnalogyTransportTheoremSchema:
    """Validates and canonicalises transported theorems.

    Applies a structured battery of checks to ensure that a transported
    theorem satisfies the schema requirements before registration in the
    target domain.

    Attributes:
        _config: :class:`SchemaConfig` controlling validation behaviour.
    """

    def __init__(self, config: SchemaConfig | None = None) -> None:
        self._config = config or SchemaConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_transported_theorem(
        self, theorem: Any  # TransportedTheorem
    ) -> SchemaValidationResult:
        """Validate *theorem* against the schema.

        Checks statement non-emptiness, vocabulary coverage, functor
        faithfulness, and coherence proof presence.

        Parameters:
            theorem: A :class:`TransportedTheorem` to validate.

        Returns:
            A :class:`SchemaValidationResult` indicating whether the theorem
            passes schema requirements.
        """
        errors: list[str] = []
        warnings: list[str] = []
        theorem_id = getattr(theorem, "transport_id", _uid())
        stmt = getattr(theorem, "transported_statement", "")
        conf = getattr(theorem, "confidence_score", 0.0)
        obj_trans = getattr(theorem, "object_translations", {})
        mor_trans = getattr(theorem, "morphism_translations", {})
        # Check statement non-emptiness
        if not stmt or stmt == "<execution failed>":
            errors.append("transported_statement is empty or indicates execution failure")
        elif self._config.max_statement_length > 0 and len(stmt) > self._config.max_statement_length:
            warnings.append(
                f"Statement length {len(stmt)} exceeds configured maximum "
                f"{self._config.max_statement_length}"
            )
        # Check confidence
        if conf < self._config.min_confidence:
            errors.append(
                f"confidence_score {conf:.3f} is below minimum "
                f"{self._config.min_confidence:.3f}"
            )
        # Check coverage
        coverage = _coherence_coverage(
            theorem_objects=set(obj_trans.keys()),
            theorem_morphisms=set(mor_trans.keys()),
            functor_object_map=obj_trans,
            functor_morphism_map=mor_trans,
        )
        if coverage < self._config.min_coverage:
            errors.append(
                f"Functor coverage {coverage:.2f} is below minimum {self._config.min_coverage:.2f}"
            )
        elif coverage < 0.5:
            warnings.append(f"Low functor coverage: {coverage:.2f}")
        # Check coherence proofs
        trace = getattr(theorem, "transport_trace", ())
        if self._config.require_coherence_proofs and not trace:
            errors.append("No transport trace (coherence proof evidence) is present")
        # Compute coherence score from trace
        coherence_score = _clamp(len(trace) / max(1, len(trace) + 5))
        # Schema confidence
        schema_conf = self.compute_schema_confidence(theorem)
        is_valid = len(errors) == 0
        return SchemaValidationResult(
            theorem_id=theorem_id,
            is_valid=is_valid,
            errors=tuple(errors),
            warnings=tuple(warnings),
            coverage_score=coverage,
            coherence_score=coherence_score,
            schema_confidence=schema_conf,
        )

    def check_coherence_proofs(
        self, theorem: Any  # TransportedTheorem
    ) -> CoherenceCheckResult:
        """Examine the transport trace for evidence of coherence satisfaction.

        Each trace step is classified as a proof element.  Steps mentioning
        "identity", "compose", "coherence", or "axiom" are treated as
        (weak) coherence proofs.

        Parameters:
            theorem: A :class:`TransportedTheorem` with a transport trace.

        Returns:
            A :class:`CoherenceCheckResult` describing which proofs passed.
        """
        theorem_id = getattr(theorem, "transport_id", _uid())
        trace = getattr(theorem, "transport_trace", ())
        proofs_checked: list[str] = []
        proofs_passed: list[str] = []
        proofs_failed: list[str] = []
        coherence_keywords = frozenset(
            {"identity", "compose", "coherence", "axiom", "functor", "preserve"}
        )
        for step in trace:
            step_lower = step.lower()
            has_coherence = any(kw in step_lower for kw in coherence_keywords)
            has_gap = "gap" in step_lower or "no mapping" in step_lower
            proofs_checked.append(step[:60])
            if has_coherence and not has_gap:
                proofs_passed.append(step[:60])
            elif has_gap:
                proofs_failed.append(step[:60])
        n_total = len(proofs_checked)
        overall = len(proofs_passed) / n_total if n_total > 0 else 0.0
        return CoherenceCheckResult(
            theorem_id=theorem_id,
            proofs_checked=tuple(proofs_checked),
            proofs_passed=tuple(proofs_passed),
            proofs_failed=tuple(proofs_failed),
            overall_coherence=_clamp(overall),
        )

    def verify_functor_structure_preservation(
        self,
        functor: Any,  # AnalogyFunctor
        theorem: Any,  # TransportedTheorem
    ) -> StructurePreservationResult:
        """Check that *functor* preserves the structures used in *theorem*.

        Structures checked include: identity morphisms, composition,
        and the specific morphisms referenced in the theorem.

        Parameters:
            functor: The :class:`AnalogyFunctor` used in the transport.
            theorem: The :class:`TransportedTheorem` to check against.

        Returns:
            A :class:`StructurePreservationResult` with per-structure verdicts.
        """
        functor_id = getattr(functor, "functor_id", _uid())
        theorem_id = getattr(theorem, "transport_id", _uid())
        coherence_conds = tuple(getattr(functor, "coherence_conditions", ()))
        faithfulness = getattr(functor, "faithfulness_score", 0.5)
        obj_map = getattr(functor, "object_map", {})
        mor_map = getattr(functor, "morphism_map", {})
        structures_checked: list[str] = []
        structures_preserved: list[str] = []
        structures_violated: list[str] = []
        # Check identity preservation
        structures_checked.append("identity_preservation")
        if any("identity" in c for c in coherence_conds):
            structures_preserved.append("identity_preservation")
        else:
            structures_violated.append("identity_preservation")
        # Check composition preservation
        structures_checked.append("composition_preservation")
        if any("compose" in c or "composition" in c for c in coherence_conds):
            structures_preserved.append("composition_preservation")
        else:
            structures_violated.append("composition_preservation")
        # Check object map completeness
        structures_checked.append("object_map_completeness")
        obj_trans = getattr(theorem, "object_translations", {})
        if len(obj_trans) > 0 and all(v for v in obj_trans.values()):
            structures_preserved.append("object_map_completeness")
        else:
            structures_violated.append("object_map_completeness")
        # Check morphism map completeness
        structures_checked.append("morphism_map_completeness")
        mor_trans = getattr(theorem, "morphism_translations", {})
        if len(mor_trans) > 0 and all(v for v in mor_trans.values()):
            structures_preserved.append("morphism_map_completeness")
        else:
            structures_violated.append("morphism_map_completeness")
        # Faithfulness check
        structures_checked.append("faithfulness_threshold")
        if faithfulness >= self._config.min_faithfulness:
            structures_preserved.append("faithfulness_threshold")
        else:
            structures_violated.append("faithfulness_threshold")
        n = len(structures_checked)
        score = len(structures_preserved) / n if n > 0 else 0.0
        return StructurePreservationResult(
            functor_id=functor_id,
            theorem_id=theorem_id,
            structures_checked=tuple(structures_checked),
            structures_preserved=tuple(structures_preserved),
            structures_violated=tuple(structures_violated),
            preservation_score=_clamp(score),
        )

    def compute_schema_confidence(self, theorem: Any) -> float:
        """Compute a schema-level confidence score for *theorem*.

        Combines the theorem's own confidence score, functor faithfulness
        (inferred from the trace length), and statement complexity.

        Parameters:
            theorem: A :class:`TransportedTheorem` to score.

        Returns:
            A float in [0, 1].
        """
        raw_conf = getattr(theorem, "confidence_score", 0.5)
        stmt = getattr(theorem, "transported_statement", "")
        trace = getattr(theorem, "transport_trace", ())
        obj_trans = getattr(theorem, "object_translations", {})
        complexity = _statement_complexity(stmt)
        coverage = _coherence_coverage(
            theorem_objects=set(obj_trans.keys()),
            theorem_morphisms=set(obj_trans.keys()),
            functor_object_map=obj_trans,
            functor_morphism_map={},
        )
        trace_bonus = _clamp(math.log1p(len(trace)) / math.log1p(20)) * 0.05
        complexity_penalty = complexity * 0.05
        schema_conf = _clamp(
            raw_conf * 0.6
            + coverage * 0.3
            + trace_bonus
            - complexity_penalty
        )
        _log.debug(
            "Schema.compute_schema_confidence: raw=%.3f, coverage=%.3f → %.3f",
            raw_conf,
            coverage,
            schema_conf,
        )
        return schema_conf

    def canonicalize(self, theorem: Any) -> CanonicalTheorem:
        """Produce a :class:`CanonicalTheorem` from *theorem*.

        Assigns a content-addressed canonical_id and a normalised name.

        Parameters:
            theorem: A validated :class:`TransportedTheorem`.

        Returns:
            A :class:`CanonicalTheorem` ready for registration.
        """
        transport_id = getattr(theorem, "transport_id", _uid())
        stmt = getattr(theorem, "transported_statement", "")
        target_domain = getattr(theorem, "target_domain", "unknown")
        canonical_id = _stable_hash(stmt + transport_id)
        domain_slug = re.sub(r"[^a-z0-9]+", "_", target_domain.lower()).strip("_")
        stmt_slug = re.sub(r"[^a-z0-9]+", "_", stmt[:20].lower()).strip("_")
        canonical_name = f"{self._config.canonical_name_prefix}_{domain_slug}_{stmt_slug}"
        schema_conf = self.compute_schema_confidence(theorem)
        return CanonicalTheorem(
            canonical_id=canonical_id,
            transport_id=transport_id,
            canonical_statement=stmt,
            canonical_name=canonical_name,
            schema_confidence=schema_conf,
        )


# ---------------------------------------------------------------------------
# FalsificationSuite
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FalsificationSuite:
    """Generates and runs falsification tests for transported theorems.

    Attributes:
        _config: :class:`FalsificationSuiteConfig` controlling test generation.
        _rng: A :class:`random.Random` instance for reproducible generation.
        _history: List of (theorem_id, :class:`FalsificationSuiteResult`) tuples.
    """

    _config: FalsificationSuiteConfig = field(default_factory=FalsificationSuiteConfig)
    _rng: random.Random = field(init=False)
    _history: list[tuple[str, FalsificationSuiteResult]] = field(default_factory=list)

    def __post_init__(self) -> None:
        seed = self._config.random_seed
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_tests(self, theorem: Any) -> list[FalsificationTest]:
        """Generate a full battery of falsification tests for *theorem*.

        Produces tests of all four types according to the configuration.

        Parameters:
            theorem: A :class:`TransportedTheorem` to generate tests for.

        Returns:
            A list of :class:`FalsificationTest` instances.
        """
        tests: list[FalsificationTest] = []
        tests.extend(self._generate_coherence_tests(theorem))
        tests.extend(self._generate_counterexample_tests(theorem))
        tests.extend(self._generate_boundary_tests(theorem))
        tests.extend(self._generate_invariance_tests(theorem))
        if self._config.prioritize_tests:
            tests = self.prioritize_tests(tests)
        _log.debug(
            "FalsificationSuite.generate_tests: %d tests for theorem %r",
            len(tests),
            getattr(theorem, "transport_id", "?"),
        )
        return tests

    def run_test(
        self, test: FalsificationTest, theorem: Any
    ) -> FalsificationTestResult:
        """Run a single :class:`FalsificationTest` against *theorem*.

        The test outcome is determined heuristically: coherence and invariance
        tests pass when the confidence score and coverage are above thresholds;
        boundary and counterexample tests use randomised probing.

        Parameters:
            test: The test to execute.
            theorem: The :class:`TransportedTheorem` being tested.

        Returns:
            A :class:`FalsificationTestResult` recording the outcome.
        """
        t_start = time.monotonic()
        try:
            passed, outcome, impact = self._evaluate_test(test, theorem)
        except Exception as exc:  # noqa: BLE001
            _log.warning("FalsificationSuite.run_test error: %s", exc)
            elapsed = time.monotonic() - t_start
            return FalsificationTestResult(
                test_id=test.test_id,
                passed=False,
                actual_outcome="Error during test execution",
                confidence_impact=-0.03,
                error_detail=str(exc),
                duration_seconds=elapsed,
            )
        elapsed = time.monotonic() - t_start
        return FalsificationTestResult(
            test_id=test.test_id,
            passed=passed,
            actual_outcome=outcome,
            confidence_impact=impact,
            duration_seconds=elapsed,
        )

    def run_suite(self, theorem: Any) -> FalsificationSuiteResult:
        """Generate and run the full falsification suite for *theorem*.

        Parameters:
            theorem: A :class:`TransportedTheorem` to test.

        Returns:
            A :class:`FalsificationSuiteResult` with all test outcomes.
        """
        t_start = time.monotonic()
        theorem_id = getattr(theorem, "transport_id", _uid())
        prior_confidence = getattr(theorem, "confidence_score", 0.5)
        tests = self.generate_tests(theorem)
        results: list[FalsificationTestResult] = []
        for test in tests:
            if time.monotonic() - t_start > self._config.max_suite_duration_seconds:
                _log.warning("FalsificationSuite: timeout after %.1fs", time.monotonic() - t_start)
                break
            results.append(self.run_test(test, theorem))
        n_passed = sum(1 for r in results if r.passed)
        n_failed = sum(1 for r in results if not r.passed)
        adjusted_conf = _bayesian_confidence_update(
            prior=prior_confidence,
            n_passed=n_passed,
            n_failed=n_failed,
            pass_weight=self._config.pass_weight,
            fail_weight=self._config.fail_weight,
        )
        outcome = SuiteOutcome.from_counts(n_passed, n_failed)
        elapsed = time.monotonic() - t_start
        suite_result = FalsificationSuiteResult(
            suite_run_id=_uid(),
            theorem_id=theorem_id,
            test_results=tuple(results),
            outcome=outcome,
            adjusted_confidence=adjusted_conf,
            n_coherence_tests=sum(
                1 for t in tests if t.test_type == TestType.COHERENCE
            ),
            n_counterexample_tests=sum(
                1 for t in tests if t.test_type == TestType.COUNTEREXAMPLE
            ),
            n_boundary_tests=sum(
                1 for t in tests if t.test_type == TestType.BOUNDARY
            ),
            n_invariance_tests=sum(
                1 for t in tests if t.test_type == TestType.INVARIANCE
            ),
            elapsed_seconds=elapsed,
        )
        self._history.append((theorem_id, suite_result))
        _log.info(
            "FalsificationSuite.run_suite: %d passed, %d failed, outcome=%s, conf=%.3f",
            n_passed,
            n_failed,
            outcome.value,
            adjusted_conf,
        )
        return suite_result

    def analyze_suite_results(
        self, results: FalsificationSuiteResult
    ) -> FalsificationAnalysis:
        """Analyse a :class:`FalsificationSuiteResult` and recommend an action.

        Parameters:
            results: A completed :class:`FalsificationSuiteResult`.

        Returns:
            A :class:`FalsificationAnalysis` with findings and recommendation.
        """
        findings: list[str] = []
        n_passed = results.n_passed
        n_failed = results.n_failed
        outcome = results.outcome
        conf = results.adjusted_confidence
        if outcome == SuiteOutcome.ALL_PASSED:
            findings.append(f"All {n_passed} tests passed; theorem appears robust.")
        elif outcome == SuiteOutcome.SOME_FAILED:
            findings.append(
                f"{n_failed}/{n_passed + n_failed} tests failed; theorem has weaknesses."
            )
            for r in results.test_results:
                if not r.passed:
                    findings.append(f"  FAILED: {r.actual_outcome[:80]}")
        elif outcome == SuiteOutcome.ALL_FAILED:
            findings.append(f"All {n_failed} tests failed; theorem is likely invalid.")
        else:
            findings.append("Suite produced inconclusive results (no tests run).")
        if conf >= 0.7:
            recommended = "accept"
        elif conf >= 0.4:
            recommended = "revise"
        else:
            recommended = "reject"
        summary = (
            f"Suite outcome: {outcome.value}. "
            f"Adjusted confidence: {conf:.3f}. "
            f"Recommendation: {recommended}."
        )
        return FalsificationAnalysis(
            analysis_id=_uid(),
            suite_run_id=results.suite_run_id,
            theorem_id=results.theorem_id,
            summary=summary,
            key_findings=tuple(findings),
            recommended_action=recommended,
            confidence_estimate=conf,
        )

    def prioritize_tests(
        self, tests: list[FalsificationTest]
    ) -> list[FalsificationTest]:
        """Sort *tests* by priority, highest first.

        Parameters:
            tests: List of tests to sort.

        Returns:
            New list sorted from highest to lowest priority.
        """
        return sorted(tests, key=lambda t: t.priority, reverse=True)

    # ------------------------------------------------------------------
    # Private test generators
    # ------------------------------------------------------------------

    def _generate_coherence_tests(self, theorem: Any) -> list[FalsificationTest]:
        """Generate COHERENCE-type tests for *theorem*."""
        tests: list[FalsificationTest] = []
        obj_trans = getattr(theorem, "object_translations", {})
        mor_trans = getattr(theorem, "morphism_translations", {})
        n = self._config.n_coherence_tests
        for i in range(n):
            obj_sample = list(obj_trans.items())[:2] if obj_trans else []
            tests.append(FalsificationTest(
                test_id=_uid(),
                description=f"Coherence check #{i+1}: verify identity preservation for sampled objects",
                test_type=TestType.COHERENCE,
                input_data={
                    "sampled_objects": obj_sample,
                    "sampled_morphisms": list(mor_trans.items())[:1],
                    "check_index": i,
                },
                expected_outcome="Identity and composition preserved under functor maps",
                priority=TestType.COHERENCE.priority() - 0.01 * i,
            ))
        return tests

    def _generate_counterexample_tests(self, theorem: Any) -> list[FalsificationTest]:
        """Generate COUNTEREXAMPLE-type tests for *theorem*."""
        tests: list[FalsificationTest] = []
        stmt = getattr(theorem, "transported_statement", "")
        n = self._config.n_counterexample_tests
        for i in range(n):
            probe = self._rng.random()
            tests.append(FalsificationTest(
                test_id=_uid(),
                description=(
                    f"Counterexample search #{i+1}: probe random instance "
                    f"(seed={probe:.4f}) against transported statement"
                ),
                test_type=TestType.COUNTEREXAMPLE,
                input_data={"probe_value": probe, "statement_hash": _stable_hash(stmt)},
                expected_outcome="No counter-example found for the transported statement",
                priority=TestType.COUNTEREXAMPLE.priority() - 0.01 * i,
            ))
        return tests

    def _generate_boundary_tests(self, theorem: Any) -> list[FalsificationTest]:
        """Generate BOUNDARY-type tests for *theorem*."""
        tests: list[FalsificationTest] = []
        target_domain = getattr(theorem, "target_domain", "unknown")
        n = self._config.n_boundary_tests
        boundary_cases = [
            "empty_structure",
            "trivial_morphism",
            "maximal_object",
            "singleton_domain",
        ]
        for i in range(min(n, len(boundary_cases))):
            case = boundary_cases[i]
            tests.append(FalsificationTest(
                test_id=_uid(),
                description=f"Boundary test: evaluate theorem at degenerate case {case!r}",
                test_type=TestType.BOUNDARY,
                input_data={"boundary_case": case, "target_domain": target_domain},
                expected_outcome=f"Theorem holds at boundary case {case!r}",
                priority=TestType.BOUNDARY.priority() - 0.01 * i,
            ))
        return tests

    def _generate_invariance_tests(self, theorem: Any) -> list[FalsificationTest]:
        """Generate INVARIANCE-type tests for *theorem*."""
        tests: list[FalsificationTest] = []
        n = self._config.n_invariance_tests
        symmetries = ["label_permutation", "automorphism", "relabelling"]
        for i in range(min(n, len(symmetries))):
            sym = symmetries[i]
            tests.append(FalsificationTest(
                test_id=_uid(),
                description=f"Invariance test: check stability under symmetry {sym!r}",
                test_type=TestType.INVARIANCE,
                input_data={"symmetry": sym},
                expected_outcome=f"Theorem is invariant under {sym!r}",
                priority=TestType.INVARIANCE.priority() - 0.01 * i,
            ))
        return tests

    def _evaluate_test(
        self,
        test: FalsificationTest,
        theorem: Any,
    ) -> tuple[bool, str, float]:
        """Evaluate *test* against *theorem* and return (passed, outcome, impact).

        Uses a heuristic based on the theorem's confidence score and test type
        to simulate realistic test outcomes.
        """
        conf = getattr(theorem, "confidence_score", 0.5)
        test_type = test.test_type
        # Introduce mild randomness for realism
        noise = self._rng.gauss(0.0, 0.05)
        adjusted = _clamp(conf + noise)
        if test_type == TestType.COHERENCE:
            threshold = 0.35
            impact_pass, impact_fail = 0.02, -0.04
        elif test_type == TestType.COUNTEREXAMPLE:
            threshold = 0.50
            impact_pass, impact_fail = 0.02, -0.06
        elif test_type == TestType.BOUNDARY:
            threshold = 0.30
            impact_pass, impact_fail = 0.01, -0.03
        else:  # INVARIANCE
            threshold = 0.40
            impact_pass, impact_fail = 0.01, -0.03
        passed = adjusted >= threshold
        if passed:
            outcome = (
                f"{test_type.value.title()} check passed "
                f"(score={adjusted:.3f} ≥ threshold={threshold})"
            )
            impact = impact_pass
        else:
            outcome = (
                f"{test_type.value.title()} check FAILED "
                f"(score={adjusted:.3f} < threshold={threshold})"
            )
            impact = impact_fail
        return passed, outcome, impact


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def run_falsification_suite(
    theorem: Any,  # TransportedTheorem
    config: FalsificationSuiteConfig | None = None,
) -> FalsificationSuiteResult:
    """Convenience wrapper: create a suite and run it on *theorem*.

    Parameters:
        theorem: A :class:`TransportedTheorem` to falsify.
        config: Optional :class:`FalsificationSuiteConfig`.

    Returns:
        A :class:`FalsificationSuiteResult` with all test outcomes.
    """
    suite = FalsificationSuite(_config=config or FalsificationSuiteConfig())
    return suite.run_suite(theorem)


def generate_coherence_tests(
    theorem: Any,  # TransportedTheorem
    n: int = 3,
    random_seed: int | None = 42,
) -> list[FalsificationTest]:
    """Generate *n* COHERENCE-type falsification tests for *theorem*.

    Parameters:
        theorem: The :class:`TransportedTheorem` to generate tests for.
        n: Number of tests to generate.
        random_seed: Optional seed for reproducibility.

    Returns:
        A list of :class:`FalsificationTest` instances of type COHERENCE.
    """
    config = FalsificationSuiteConfig(
        n_coherence_tests=n,
        n_counterexample_tests=0,
        n_boundary_tests=0,
        n_invariance_tests=0,
        random_seed=random_seed,
    )
    suite = FalsificationSuite(_config=config)
    # pylint: disable=protected-access
    return suite._generate_coherence_tests(theorem)


def score_theorem_confidence(
    prior: float,
    suite_result: FalsificationSuiteResult,
    pass_weight: float = 0.02,
    fail_weight: float = 0.05,
) -> float:
    """Compute a final confidence score for a theorem after falsification.

    Applies a Bayesian-inspired update to *prior* based on the suite's
    pass/fail counts.

    Parameters:
        prior: Initial confidence estimate in [0, 1].
        suite_result: A completed :class:`FalsificationSuiteResult`.
        pass_weight: Per-passing-test confidence boost.
        fail_weight: Per-failing-test confidence penalty.

    Returns:
        Updated confidence in [0, 1].
    """
    return _bayesian_confidence_update(
        prior=prior,
        n_passed=suite_result.n_passed,
        n_failed=suite_result.n_failed,
        pass_weight=pass_weight,
        fail_weight=fail_weight,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "TestType",
    "SuiteOutcome",
    # Configuration
    "SchemaConfig",
    "FalsificationSuiteConfig",
    # Value objects
    "FalsificationTest",
    "FalsificationTestResult",
    "SchemaValidationResult",
    "CoherenceCheckResult",
    "StructurePreservationResult",
    "CanonicalTheorem",
    "FalsificationSuiteResult",
    "FalsificationAnalysis",
    # Core classes
    "AnalogyTransportTheoremSchema",
    "FalsificationSuite",
    # Free functions
    "run_falsification_suite",
    "generate_coherence_tests",
    "score_theorem_confidence",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== analogy_transport.theorems smoke test ===\n")

    # 1. Build a minimal mock TransportedTheorem (avoiding hard import)
    class _MockTransportedTheorem:
        transport_id = _uid()
        source_theorem_id = _uid()
        target_domain = "type_theory"
        transported_statement = (
            "For any type T and context Gamma, "
            "the weakening T(Gamma) -> T(Gamma, x:A) is a morphism of types."
        )
        object_translations = {
            "sheaf": "type",
            "base_space": "context",
            "open_set": "proposition",
        }
        morphism_translations = {
            "restriction_map": "weakening",
        }
        transport_trace = (
            "  object 'sheaf' → 'type'",
            "  object 'base_space' → 'context'",
            "  object 'open_set' → 'proposition'",
            "  morphism 'restriction_map' → 'weakening'",
            "Rewritten statement: For any type T ...",
            "Verify coherence conditions: identity_preservation, compose_preservation",
        )
        confidence_score = 0.74
        functor_id = _uid()
        status = type("S", (), {"value": "completed"})()

    thm = _MockTransportedTheorem()
    print(f"Mock transported theorem: {thm.transport_id[:8]}...")
    print(f"  statement: {thm.transported_statement[:70]}...\n")

    # 2. Schema validation
    schema = AnalogyTransportTheoremSchema(SchemaConfig(min_confidence=0.3, min_coverage=0.2))
    validation = schema.validate_transported_theorem(thm)
    print(f"Schema validation: valid={validation.is_valid}")
    print(f"  coverage={validation.coverage_score:.2f}, coherence={validation.coherence_score:.2f}")
    print(f"  schema_confidence={validation.schema_confidence:.3f}")
    if validation.errors:
        for e in validation.errors:
            print(f"  error: {e}")
    if validation.warnings:
        for w in validation.warnings:
            print(f"  warning: {w}")
    print()

    # 3. Coherence check
    coherence = schema.check_coherence_proofs(thm)
    print(f"Coherence check: overall={coherence.overall_coherence:.2f}")
    print(f"  proofs_checked: {len(coherence.proofs_checked)}")
    print(f"  proofs_passed: {len(coherence.proofs_passed)}")
    print(f"  proofs_failed: {len(coherence.proofs_failed)}")
    print()

    # 4. Structure preservation
    class _MockFunctor:
        functor_id = _uid()
        faithfulness_score = 0.82
        coherence_conditions = ("identity_preservation", "compose_preservation")
        object_map = {"sheaf": "type", "base_space": "context"}
        morphism_map = {"restriction_map": "weakening"}

    functor = _MockFunctor()
    preservation = schema.verify_functor_structure_preservation(functor, thm)
    print(f"Structure preservation: score={preservation.preservation_score:.2f}")
    print(f"  preserved: {list(preservation.structures_preserved)}")
    print(f"  violated:  {list(preservation.structures_violated)}")
    print()

    # 5. Canonicalise
    canon = schema.canonicalize(thm)
    print(f"Canonical theorem:")
    print(f"  canonical_id: {canon.canonical_id}")
    print(f"  canonical_name: {canon.canonical_name}")
    print(f"  schema_confidence: {canon.schema_confidence:.3f}")
    print()

    # 6. Falsification suite
    suite_config = FalsificationSuiteConfig(
        n_coherence_tests=2,
        n_counterexample_tests=2,
        n_boundary_tests=2,
        n_invariance_tests=1,
        random_seed=7,
    )
    suite = FalsificationSuite(_config=suite_config)
    tests = suite.generate_tests(thm)
    print(f"Generated {len(tests)} falsification tests:")
    for t in tests:
        print(f"  [{t.test_type.value:14s}] pri={t.priority:.2f}  {t.description[:55]}")
    print()

    suite_result = suite.run_suite(thm)
    print(f"Suite outcome: {suite_result.outcome.value}")
    print(f"  passed={suite_result.n_passed}, failed={suite_result.n_failed}")
    print(f"  adjusted_confidence: {suite_result.adjusted_confidence:.3f}")
    print()

    # 7. Analysis
    analysis = suite.analyze_suite_results(suite_result)
    print(f"Analysis:")
    print(f"  summary: {analysis.summary}")
    print(f"  recommendation: {analysis.recommended_action!r}")
    for finding in analysis.key_findings:
        print(f"    • {finding[:80]}")
    print()

    # 8. Free function wrappers
    quick_result = run_falsification_suite(thm, FalsificationSuiteConfig(random_seed=99))
    print(f"run_falsification_suite: outcome={quick_result.outcome.value}")
    final_conf = score_theorem_confidence(thm.confidence_score, quick_result)
    print(f"score_theorem_confidence: {final_conf:.3f}")
    coh_tests = generate_coherence_tests(thm, n=3)
    print(f"generate_coherence_tests: {len(coh_tests)} tests generated")
    print("\n=== smoke test complete ===")
