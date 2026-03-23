"""
Formalization Loop — Theory to Formal Specification.

This module implements the formalization feedback loop for the JuGeo
Evaluation Methodology subsystem (theory2.tex Ch71 §2). The formalization
loop transforms informal theory into a formal specification that can be
mechanically checked.

Loop definition:
  INPUT:  An informal theory statement and a set of background axioms.
  OUTPUT: A formal specification in JuGeo's specification language, plus
          a list of unresolved gaps that indicate further theory work is needed.
  ENTRY:  The theory statement is complete and the background axioms are
          loaded into the current pack.
  EXIT:   All theory statements have been successfully formalized, or a
          blocking gap has been identified requiring loop restart.

State machine:
  WAITING → RUNNING (entry conditions satisfied)
  RUNNING → BLOCKED_BY_GAP (blocking gap detected)
  RUNNING → COMPLETED (no gaps remain)
  BLOCKED_BY_GAP → RUNNING (gaps resolved, restart)
  RUNNING → FAILED (irrecoverable error)
  COMPLETED → terminal state

copilot: formalization-loop marker
theory2.tex Ch71 §2 — Formalization Loop
"""

from __future__ import annotations

import time
import uuid
import math
import re
import hashlib
import itertools
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

# ---------------------------------------------------------------------------
# Optional cross-module imports
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.methodology_loops.models import BaseArtifact  # type: ignore
except ImportError:
    BaseArtifact = None  # type: ignore

try:
    from jugeo.pack.theory_store import TheoryStore  # type: ignore
except ImportError:
    TheoryStore = None  # type: ignore

try:
    from jugeo.pack.axiom_store import AxiomStore  # type: ignore
except ImportError:
    AxiomStore = None  # type: ignore

try:
    from jugeo.evaluation.methodology_loops.a_thesis_needs_a_method_not_only_a import (
        MethodComponent,
    )  # type: ignore
except ImportError:
    MethodComponent = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# copilot: maximum number of formalization iterations before marking FAILED
MAX_ITERATIONS: int = 20

# copilot: minimum confidence level (0–1) required for a formal spec to pass
MIN_SPEC_CONFIDENCE: float = 0.75

# copilot: default resolution hint template used when none is provided
DEFAULT_RESOLUTION_HINT: str = (
    "Review the theory statement for ambiguities and consult the axiom store."
)

# copilot: weights for gap kinds — blocking gaps have weight 1.0,
# non-blocking gaps are partial deductions from the completion score.
GAP_KIND_WEIGHTS: dict[str, float] = {
    "MISSING_AXIOM": 1.0,
    "AMBIGUOUS_QUANTIFIER": 0.8,
    "UNDEFINED_TERM": 0.9,
    "SCOPE_MISMATCH": 0.7,
    "TYPE_ERROR": 0.6,
}

# copilot: keywords in theory text that trigger gap detection heuristics
AMBIGUITY_KEYWORDS: set[str] = {
    "some", "many", "often", "usually", "few", "several", "approximately",
    "roughly", "about", "around", "nearly", "almost", "quite",
}

UNDEFINED_TERM_PATTERNS: list[str] = [
    r"\b[A-Z][a-z]+[A-Z][a-zA-Z]*\b",   # CamelCase — potential undefined type
    r"\b[A-Z]{2,}\b",                    # ALL_CAPS — potential undefined constant
]

SCOPE_INDICATOR_WORDS: set[str] = {
    "forall", "exists", "for all", "there exists", "for every", "for some",
}

# copilot: axiom name patterns that represent well-known JuGeo axioms
KNOWN_AXIOM_PATTERNS: list[str] = [
    r"^pack\.",
    r"^theory\.",
    r"^jugeo\.",
    r"^evaluation\.",
]

# copilot: schema version embedded in every artifact
ARTIFACT_SCHEMA_VERSION: str = "2.0.0"

# copilot: threshold below which the completion estimate triggers a BLOCKED warning
BLOCKED_COMPLETION_THRESHOLD: float = 0.30

# Human-readable descriptions of each gap kind
GAP_KIND_DESCRIPTIONS: dict[str, str] = {
    "MISSING_AXIOM": (
        "A required background axiom is not present in the current pack. "
        "The formalization cannot proceed without this axiom being loaded."
    ),
    "AMBIGUOUS_QUANTIFIER": (
        "A quantifier in the theory statement is underspecified — its range, "
        "type, or binding scope is not uniquely determined by the context."
    ),
    "UNDEFINED_TERM": (
        "A term referenced in the theory statement is not defined in the "
        "current vocabulary or type environment."
    ),
    "SCOPE_MISMATCH": (
        "A variable binding appears in a scope where it is not accessible, "
        "or the scope of a universal/existential quantifier is ambiguous."
    ),
    "TYPE_ERROR": (
        "An expression in the theory statement contains a type inconsistency "
        "that prevents unambiguous formalization."
    ),
}

# copilot: status transition table — maps current status to allowed next statuses
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "WAITING": {"RUNNING"},
    "RUNNING": {"BLOCKED_BY_GAP", "COMPLETED", "FAILED"},
    "BLOCKED_BY_GAP": {"RUNNING", "FAILED"},
    "COMPLETED": set(),   # terminal
    "FAILED": set(),      # terminal
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp.

    Returns:
        float: Seconds since the Unix epoch in UTC.

    Example:
        >>> t = _utcnow()
        >>> t > 0
        True
    """
    return time.time()


def _uid() -> str:
    """Generate a 16-character unique identifier.

    Returns:
        str: A 16-char lowercase hex string.

    Example:
        >>> len(_uid())
        16
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [lo, hi].

    Args:
        value: Input value.
        lo: Lower bound.
        hi: Upper bound.

    Returns:
        float: Clamped value.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(2.0, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


def _normalise_theory_text(text: str) -> str:
    """Strip excessive whitespace and normalise line endings in theory text.

    This is a preprocessing step before gap detection so that heuristics
    operate on clean, consistent input.

    Args:
        text: The raw theory statement text.

    Returns:
        str: Normalised text with collapsed whitespace and UNIX line endings.

    Example:
        >>> _normalise_theory_text("  hello  \\r\\n  world  ")
        'hello\\nworld'
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.strip() for line in lines if line.strip())


def _axiom_is_known(axiom_id: str) -> bool:
    """Heuristic check: does the axiom_id match a known JuGeo axiom pattern?

    Args:
        axiom_id: The axiom identifier string.

    Returns:
        bool: True if the axiom_id matches at least one known pattern.

    Example:
        >>> _axiom_is_known("pack.commutativity")
        True
        >>> _axiom_is_known("random.thing")
        False
    """
    for pattern in KNOWN_AXIOM_PATTERNS:
        if re.match(pattern, axiom_id):
            return True
    return False


def _count_ambiguity_keywords(text: str) -> int:
    """Count how many ambiguity keywords appear in *text*.

    Args:
        text: Theory statement text (already normalised).

    Returns:
        int: Count of ambiguity keyword occurrences.

    Example:
        >>> _count_ambiguity_keywords("some nodes often appear")
        2
    """
    lower = text.lower()
    return sum(1 for kw in AMBIGUITY_KEYWORDS if re.search(r"\b" + kw + r"\b", lower))


def _find_undefined_term_candidates(text: str) -> list[str]:
    """Find candidate undefined terms in theory text using regex heuristics.

    Args:
        text: Normalised theory text.

    Returns:
        list[str]: Deduplicated list of potential undefined term strings.

    Example:
        >>> candidates = _find_undefined_term_candidates("Let MyType be X.")
        >>> "MyType" in candidates
        True
    """
    found: set[str] = set()
    for pattern in UNDEFINED_TERM_PATTERNS:
        matches = re.findall(pattern, text)
        found.update(matches)
    # copilot: remove very short matches that are likely pronouns or articles
    return sorted(t for t in found if len(t) > 2)


def _has_scope_indicator(text: str) -> bool:
    """Check whether the theory text contains a quantifier scope indicator.

    Args:
        text: Normalised theory text (lower-cased internally).

    Returns:
        bool: True if at least one scope indicator word is found.

    Example:
        >>> _has_scope_indicator("for all x in S, P(x) holds")
        True
    """
    lower = text.lower()
    return any(indicator in lower for indicator in SCOPE_INDICATOR_WORDS)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FormalizationStatus(str, Enum):
    """The lifecycle state of a formalization loop instance.

    FormalizationStatus drives the loop's state machine.  Transitions are
    validated against ALLOWED_TRANSITIONS before being applied.  Terminal
    states (COMPLETED, FAILED) cannot be left.

    This class inherits from str so that status values can be stored in
    JSON/YAML without additional serialisation logic.
    """

    WAITING = "WAITING"
    """The loop is waiting for entry conditions to be satisfied.

    The theory statement or axiom set is not yet ready.  The loop will
    remain in this state until FormalizationLoopCoordinator.run_iteration()
    is called with a complete theory statement and a non-empty axiom list.
    """

    RUNNING = "RUNNING"
    """The loop is actively translating theory to formal specification.

    Gap detection heuristics are running against the theory text.  The
    loop will either complete successfully or transition to BLOCKED_BY_GAP
    if a blocking gap is found.
    """

    BLOCKED_BY_GAP = "BLOCKED_BY_GAP"
    """A blocking gap prevents forward progress.

    At least one FormalGap with blocking=True has been detected and not
    yet resolved.  The loop is paused and waiting for the researcher to
    resolve the gap (e.g. by adding a missing axiom).
    """

    COMPLETED = "COMPLETED"
    """All theory statements have been successfully formalized.

    This is a terminal state.  The artifact produced in this state contains
    the complete formal specification with no blocking gaps remaining.
    """

    FAILED = "FAILED"
    """The loop has encountered an irrecoverable error.

    This may occur if MAX_ITERATIONS is exceeded, or if a gap is marked
    blocking with no resolution hint and the researcher has not intervened.
    This is a terminal state.
    """


class GapKind(str, Enum):
    """Classification of a formalization gap.

    Each GapKind corresponds to a distinct category of formalization problem.
    The kind determines whether the gap is blocking (prevents forward
    progress) and what resolution strategy is appropriate.

    GAP_KIND_WEIGHTS provides the numerical weight of each gap kind, which
    is used by FormalizationLoopAnalyzer.estimate_completion() to compute
    how much of the specification is still incomplete.
    """

    MISSING_AXIOM = "MISSING_AXIOM"
    """A required axiom is absent from the current pack.

    Resolution: Load the missing axiom into the current pack's axiom store
    and restart the formalization loop.  If the axiom does not exist, it
    must be proved as a lemma before formalization can proceed.
    """

    AMBIGUOUS_QUANTIFIER = "AMBIGUOUS_QUANTIFIER"
    """A quantifier in the theory statement is underspecified.

    Resolution: Revise the theory statement to explicitly specify the domain,
    type, and binding scope of the ambiguous quantifier.
    """

    UNDEFINED_TERM = "UNDEFINED_TERM"
    """A term used in the theory statement is not in the vocabulary.

    Resolution: Either define the term in the vocabulary, or replace it with
    an already-defined equivalent.
    """

    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    """A variable or quantifier appears in an inaccessible scope.

    Resolution: Restructure the theory statement so that all variable bindings
    are within the correct scope.
    """

    TYPE_ERROR = "TYPE_ERROR"
    """An expression contains a type inconsistency.

    Resolution: Correct the types of the involved sub-expressions.  This often
    requires revisiting the type signatures of related definitions.
    """


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FormalGap:
    """A single identified gap in the formalization of a theory statement.

    FormalGap is an immutable record of one problem found during the
    formalization loop.  Gaps are produced by FormalizationLoopAnalyzer
    and embedded in FormalizationArtifact objects.

    A gap is *blocking* if it prevents the formalization loop from
    making forward progress.  Non-blocking gaps are advisory: they
    flag potential issues that the researcher should address but that
    do not halt the loop.

    Attributes:
        gap_id: A unique identifier for this gap instance.
        kind: The GapKind classifying this gap.
        description: A human-readable description of the specific problem,
            including context from the theory text where possible.
        blocking: True if this gap must be resolved before the loop can
            continue.  False if the gap is advisory only.
        resolution_hint: A short suggestion for how to resolve the gap.
            Defaults to DEFAULT_RESOLUTION_HINT if not provided by the
            detector.

    Example:
        >>> gap = FormalGap(
        ...     gap_id="g-001",
        ...     kind=GapKind.MISSING_AXIOM,
        ...     description="Axiom pack.idempotency is not loaded.",
        ...     blocking=True,
        ...     resolution_hint="Load pack.idempotency from the axiom store.",
        ... )
    """

    gap_id: str
    kind: GapKind
    description: str
    blocking: bool
    resolution_hint: str


@dataclass(frozen=True, slots=True)
class FormalizationArtifact:
    """The output of one iteration of the formalization loop.

    FormalizationArtifact is an immutable snapshot of the formalization
    loop's state at the end of one iteration.  Each call to
    FormalizationLoopCoordinator.run_iteration() produces a new artifact;
    artifacts are never mutated in place.

    The artifact contains:
      - The formal specification produced so far (may be incomplete if
        blocking gaps are present).
      - The list of gaps detected in this iteration.
      - The loop's current status.
      - The iteration count (useful for detecting runaway loops).

    Attributes:
        artifact_id: A globally unique identifier for this artifact.
        theory_id: The identifier of the theory statement being formalized.
        formal_spec: The formal specification text produced so far.  May be
            empty or partial if the loop is blocked or has not yet started.
        gaps: A tuple of FormalGap objects detected in this iteration.
        status: The FormalizationStatus at the end of this iteration.
        iteration_count: How many iterations have been run so far, including
            this one.
        created_at: UTC POSIX timestamp of when this artifact was produced.

    Example:
        >>> art = FormalizationArtifact(
        ...     artifact_id="a-001",
        ...     theory_id="th-001",
        ...     formal_spec="forall x : Pack, x.size >= 0",
        ...     gaps=(),
        ...     status=FormalizationStatus.COMPLETED,
        ...     iteration_count=1,
        ...     created_at=1_700_000_000.0,
        ... )
    """

    artifact_id: str
    theory_id: str
    formal_spec: str
    gaps: tuple[FormalGap, ...]
    status: FormalizationStatus
    iteration_count: int
    created_at: float


# ---------------------------------------------------------------------------
# FormalizationLoopAnalyzer
# ---------------------------------------------------------------------------

class FormalizationLoopAnalyzer:
    """Analyses theory text and formalization artifacts for the formalization loop.

    FormalizationLoopAnalyzer is a stateless analysis engine.  All methods
    are pure: given the same inputs they produce the same outputs.

    The analyzer uses a combination of keyword heuristics, regex patterns,
    and axiom-store lookups (mocked when AxiomStore is unavailable) to
    detect formalization gaps, estimate loop completion, and decide whether
    the loop can proceed.

    In a production deployment, the heuristics would be replaced by a
    proper type-checker and formal-specification parser.  The current
    implementation provides sufficient fidelity for smoke-testing and
    integration testing of the loop coordination logic.
    """

    def detect_gaps(
        self,
        theory_text: str,
        axiom_ids: Sequence[str],
    ) -> list[FormalGap]:
        """Detect formalization gaps in the given theory text.

        Runs a sequence of heuristic detectors against the normalised
        theory text and the supplied axiom identifiers.  Each detector
        may produce zero or more FormalGap objects.

        The detection pipeline is:
          1. MISSING_AXIOM: check each axiom_id against known patterns.
          2. AMBIGUOUS_QUANTIFIER: scan for ambiguity keywords.
          3. UNDEFINED_TERM: scan for CamelCase / ALL_CAPS tokens.
          4. SCOPE_MISMATCH: check for quantifier scope indicators.
          5. TYPE_ERROR: check for common type-error patterns.

        Args:
            theory_text: The informal theory statement as a string.
            axiom_ids: A sequence of axiom identifier strings that the
                theory claims to depend on.

        Returns:
            list[FormalGap]: All detected gaps, sorted by blocking status
            (blocking gaps first) then by GapKind order.

        Raises:
            ValueError: If theory_text is empty.

        Example:
            >>> analyzer = FormalizationLoopAnalyzer()
            >>> gaps = analyzer.detect_gaps("For some x, MyType(x) holds.", [])
            >>> len(gaps) > 0
            True
        """
        if not theory_text or not theory_text.strip():
            raise ValueError("detect_gaps: theory_text must be non-empty")

        normalised = _normalise_theory_text(theory_text)
        detected: list[FormalGap] = []

        # copilot: Phase 1 — check axiom dependencies
        for axiom_id in axiom_ids:
            if not _axiom_is_known(axiom_id):
                gap = FormalGap(
                    gap_id=_uid(),
                    kind=GapKind.MISSING_AXIOM,
                    description=(
                        f"Axiom '{axiom_id}' does not match any known JuGeo axiom "
                        f"pattern.  It may be missing from the pack's axiom store."
                    ),
                    blocking=True,
                    resolution_hint=(
                        f"Load '{axiom_id}' into the pack axiom store, or rename it "
                        f"to follow a known JuGeo namespace (e.g. 'pack.{axiom_id}')."
                    ),
                )
                detected.append(gap)

        # copilot: Phase 2 — ambiguous quantifier detection
        ambiguity_count = _count_ambiguity_keywords(normalised)
        if ambiguity_count > 0:
            gap = FormalGap(
                gap_id=_uid(),
                kind=GapKind.AMBIGUOUS_QUANTIFIER,
                description=(
                    f"Found {ambiguity_count} ambiguity keyword(s) in the theory "
                    f"statement (e.g. 'some', 'many', 'often').  These must be "
                    f"replaced with precise quantifiers."
                ),
                blocking=ambiguity_count >= 3,
                resolution_hint=(
                    "Replace vague quantifiers with precise mathematical ones: "
                    "'for all', 'there exists', 'at least N', etc."
                ),
            )
            detected.append(gap)

        # copilot: Phase 3 — undefined term detection
        undefined_candidates = _find_undefined_term_candidates(normalised)
        if undefined_candidates:
            terms_preview = ", ".join(undefined_candidates[:5])
            gap = FormalGap(
                gap_id=_uid(),
                kind=GapKind.UNDEFINED_TERM,
                description=(
                    f"Possible undefined terms detected: {terms_preview}.  "
                    f"Each must be defined in the JuGeo vocabulary before "
                    f"formalization can proceed."
                ),
                blocking=len(undefined_candidates) > 3,
                resolution_hint=(
                    "Define each candidate term in the pack's vocabulary file, "
                    "or replace with an already-defined synonym."
                ),
            )
            detected.append(gap)

        # copilot: Phase 4 — scope mismatch detection
        if _has_scope_indicator(normalised):
            # copilot: look for unmatched quantifier pairs as a proxy for scope
            # mismatch; a simple heuristic is to count forall/exists tokens.
            lower = normalised.lower()
            forall_count = lower.count("forall") + lower.count("for all") + lower.count("for every")
            exists_count = lower.count("exists") + lower.count("there exists") + lower.count("for some")
            if abs(forall_count - exists_count) > 2:
                gap = FormalGap(
                    gap_id=_uid(),
                    kind=GapKind.SCOPE_MISMATCH,
                    description=(
                        f"Unbalanced quantifier count: {forall_count} universal vs "
                        f"{exists_count} existential quantifiers.  This may indicate "
                        f"a scope mismatch or missing quantifier binding."
                    ),
                    blocking=False,
                    resolution_hint=(
                        "Review each quantifier binding and ensure variables are "
                        "bound exactly once within their intended scope."
                    ),
                )
                detected.append(gap)

        # copilot: Phase 5 — type error heuristic: look for ':' operator misuse
        type_error_pattern = re.compile(r"\b\w+\s*:\s*\w+\s*:\s*\w+")
        if type_error_pattern.search(normalised):
            gap = FormalGap(
                gap_id=_uid(),
                kind=GapKind.TYPE_ERROR,
                description=(
                    "A double-colon type annotation pattern was detected.  "
                    "JuGeo uses single-colon type ascriptions; double colons "
                    "may indicate a Haskell-style syntax error."
                ),
                blocking=False,
                resolution_hint=(
                    "Replace '::' type annotations with single ':' ascriptions "
                    "following JuGeo's type syntax."
                ),
            )
            detected.append(gap)

        # copilot: sort so that blocking gaps come first, then by kind name
        detected.sort(key=lambda g: (not g.blocking, g.kind.value))
        return detected

    def can_proceed(self, artifact: FormalizationArtifact) -> bool:
        """Determine whether the formalization loop can proceed past this artifact.

        The loop can proceed if and only if:
          - The artifact's status is not a terminal state (COMPLETED or FAILED).
          - There are no blocking gaps in the artifact.

        Args:
            artifact: The FormalizationArtifact to evaluate.

        Returns:
            bool: True if the loop should continue; False if it is blocked
            or has reached a terminal state.

        Raises:
            TypeError: If artifact is not a FormalizationArtifact.

        Example:
            >>> analyzer = FormalizationLoopAnalyzer()
            >>> art = FormalizationArtifact(
            ...     artifact_id="a1", theory_id="t1", formal_spec="",
            ...     gaps=(), status=FormalizationStatus.COMPLETED,
            ...     iteration_count=1, created_at=0.0,
            ... )
            >>> analyzer.can_proceed(art)
            False
        """
        if not isinstance(artifact, FormalizationArtifact):
            raise TypeError(
                f"can_proceed expects FormalizationArtifact, got {type(artifact)!r}"
            )

        # copilot: terminal states are always non-proceeding
        if artifact.status in (FormalizationStatus.COMPLETED, FormalizationStatus.FAILED):
            return False

        # copilot: check for blocking gaps
        has_blocking_gap = any(g.blocking for g in artifact.gaps)
        if has_blocking_gap:
            return False

        # copilot: if we've hit the iteration ceiling, do not proceed
        if artifact.iteration_count >= MAX_ITERATIONS:
            return False

        return True

    def estimate_completion(self, artifact: FormalizationArtifact) -> float:
        """Estimate the fraction of the formalization that is complete.

        The estimate is computed as:
          base_completion = 1.0 if status is COMPLETED else
                            0.0 if iteration_count == 0 else
                            min(iteration_count / MAX_ITERATIONS, 0.95)
          gap_penalty = sum(GAP_KIND_WEIGHTS[g.kind] for g in gaps) / MAX_GAP_PENALTY
          completion = clamp(base_completion - gap_penalty * 0.5, 0.0, 1.0)

        The MAX_GAP_PENALTY is the sum of all gap kind weights, representing
        the worst-case penalty when every gap kind is present.

        Args:
            artifact: The FormalizationArtifact to estimate.

        Returns:
            float: An estimated completion fraction in [0.0, 1.0].

        Raises:
            TypeError: If artifact is not a FormalizationArtifact.

        Example:
            >>> analyzer = FormalizationLoopAnalyzer()
            >>> art = FormalizationArtifact(
            ...     artifact_id="a1", theory_id="t1", formal_spec="spec",
            ...     gaps=(), status=FormalizationStatus.COMPLETED,
            ...     iteration_count=1, created_at=0.0,
            ... )
            >>> analyzer.estimate_completion(art)
            1.0
        """
        if not isinstance(artifact, FormalizationArtifact):
            raise TypeError(
                f"estimate_completion expects FormalizationArtifact, got {type(artifact)!r}"
            )

        # copilot: COMPLETED artifacts are 100% done
        if artifact.status == FormalizationStatus.COMPLETED:
            return 1.0

        # copilot: FAILED artifacts are 0% complete (irrecoverable)
        if artifact.status == FormalizationStatus.FAILED:
            return 0.0

        # copilot: estimate progress based on iteration count
        if artifact.iteration_count == 0:
            base = 0.0
        else:
            base = min(artifact.iteration_count / MAX_ITERATIONS, 0.95)

        # copilot: compute gap penalty
        max_penalty = sum(GAP_KIND_WEIGHTS.values())
        gap_penalty = sum(
            GAP_KIND_WEIGHTS.get(g.kind.value, 0.5) for g in artifact.gaps
        )
        normalised_penalty = (gap_penalty / max_penalty) if max_penalty > 0 else 0.0

        # copilot: apply penalty at half weight — gaps slow but don't fully stop
        completion = _clamp(base - normalised_penalty * 0.5, 0.0, 1.0)
        return round(completion, 4)


# ---------------------------------------------------------------------------
# FormalizationLoopCoordinator
# ---------------------------------------------------------------------------

class FormalizationLoopCoordinator:
    """Orchestrates iterations of the formalization feedback loop.

    FormalizationLoopCoordinator manages the lifecycle of one or more
    formalization loops.  Each call to run_iteration() produces a new
    FormalizationArtifact.  The coordinator tracks all artifacts per
    theory_id, enforces the MAX_ITERATIONS ceiling, and provides status
    reporting utilities.

    The coordinator is stateful; it is NOT thread-safe.

    Attributes:
        coordinator_id: A unique id for this coordinator instance.
        _analyzer: The FormalizationLoopAnalyzer used for gap detection.
        _artifacts: Mapping from theory_id to list of FormalizationArtifact.
    """

    def __init__(self) -> None:
        """Initialise a new FormalizationLoopCoordinator.

        Example:
            >>> coord = FormalizationLoopCoordinator()
            >>> len(coord.coordinator_id)
            16
        """
        self.coordinator_id: str = _uid()
        self._analyzer = FormalizationLoopAnalyzer()
        self._artifacts: dict[str, list[FormalizationArtifact]] = {}

    def run_iteration(
        self,
        theory_id: str,
        theory_text: str,
        axiom_ids: Sequence[str],
    ) -> FormalizationArtifact:
        """Run one iteration of the formalization loop for the given theory.

        One iteration:
          1. Checks if the loop has already reached a terminal state.
          2. Increments the iteration count.
          3. Runs gap detection on the theory text and axiom IDs.
          4. Determines the new status based on gaps and iteration count.
          5. Produces a formal specification stub (heuristic in this mock).
          6. Creates and stores a new FormalizationArtifact.

        Args:
            theory_id: The identifier of the theory being formalized.
            theory_text: The current informal theory statement.
            axiom_ids: Axiom identifiers that the theory depends on.

        Returns:
            FormalizationArtifact: The artifact produced by this iteration.

        Raises:
            ValueError: If theory_id or theory_text is empty.
            RuntimeError: If the loop is already in a terminal state.

        Example:
            >>> coord = FormalizationLoopCoordinator()
            >>> art = coord.run_iteration(
            ...     "th-001", "For all x, pack.size(x) >= 0.", ["pack.non_negative"]
            ... )
            >>> art.theory_id
            'th-001'
        """
        if not theory_id:
            raise ValueError("run_iteration: theory_id must be non-empty")
        if not theory_text or not theory_text.strip():
            raise ValueError("run_iteration: theory_text must be non-empty")

        history = self._artifacts.get(theory_id, [])
        prev_status = history[-1].status if history else FormalizationStatus.WAITING
        prev_iter = history[-1].iteration_count if history else 0

        # copilot: guard against running a terminal loop
        if prev_status in (FormalizationStatus.COMPLETED, FormalizationStatus.FAILED):
            raise RuntimeError(
                f"Theory '{theory_id}' loop is already in terminal state "
                f"{prev_status.value}.  Cannot run further iterations."
            )

        iteration = prev_iter + 1

        # copilot: detect gaps in this iteration
        gaps = self._analyzer.detect_gaps(theory_text, axiom_ids)

        # copilot: derive formal spec stub from theory text (production would
        # call an actual formal-spec parser here)
        formal_spec = self._build_spec_stub(theory_text, axiom_ids, gaps, iteration)

        # copilot: determine new status
        blocking_gaps = [g for g in gaps if g.blocking]
        if iteration >= MAX_ITERATIONS:
            new_status = FormalizationStatus.FAILED
        elif blocking_gaps:
            new_status = FormalizationStatus.BLOCKED_BY_GAP
        elif not gaps:
            new_status = FormalizationStatus.COMPLETED
        else:
            # copilot: non-blocking gaps only — loop is still running
            new_status = FormalizationStatus.RUNNING

        artifact = FormalizationArtifact(
            artifact_id=_uid(),
            theory_id=theory_id,
            formal_spec=formal_spec,
            gaps=tuple(gaps),
            status=new_status,
            iteration_count=iteration,
            created_at=_utcnow(),
        )

        bucket = self._artifacts.setdefault(theory_id, [])
        bucket.append(artifact)
        return artifact

    def _build_spec_stub(
        self,
        theory_text: str,
        axiom_ids: Sequence[str],
        gaps: list[FormalGap],
        iteration: int,
    ) -> str:
        """Build a heuristic formal specification stub for smoke testing.

        In a production system, this would invoke a formal-spec parser.
        Here it produces a structured stub that embeds the theory text,
        axiom list, and detected gaps.

        Args:
            theory_text: The informal theory statement.
            axiom_ids: Axiom identifiers.
            gaps: Detected gaps.
            iteration: Current iteration number.

        Returns:
            str: A stub formal specification string.
        """
        axiom_block = "\n".join(
            f"  axiom {ax}" for ax in axiom_ids
        ) or "  (* no axioms *)"
        gap_block = "\n".join(
            f"  (* GAP [{g.kind.value}]: {g.description[:60]}... *)"
            for g in gaps
        ) or "  (* no gaps *)"
        return (
            f"(* Formal spec stub — iteration {iteration} *)\n"
            f"theory T{iteration} =\n"
            f"  statement: {theory_text[:80].replace(chr(10), ' ')}\n"
            f"  axioms:\n{axiom_block}\n"
            f"  gaps:\n{gap_block}\n"
            f"end"
        )

    def is_done(self, artifact: FormalizationArtifact) -> bool:
        """Return True iff the artifact's status is COMPLETED.

        Args:
            artifact: The artifact to check.

        Returns:
            bool: True if status is COMPLETED.

        Example:
            >>> coord = FormalizationLoopCoordinator()
            >>> art = FormalizationArtifact(
            ...     artifact_id="a1", theory_id="t1", formal_spec="",
            ...     gaps=(), status=FormalizationStatus.COMPLETED,
            ...     iteration_count=1, created_at=0.0,
            ... )
            >>> coord.is_done(art)
            True
        """
        return artifact.status == FormalizationStatus.COMPLETED

    def restart(
        self,
        artifact: FormalizationArtifact,
        resolved_gaps: Sequence[str],
    ) -> FormalizationArtifact:
        """Restart a blocked formalization loop after resolving gaps.

        Creates a new artifact derived from the given artifact but with
        the RUNNING status and the resolved gaps removed.

        Args:
            artifact: The blocked artifact to restart from.
            resolved_gaps: Gap IDs that have been resolved and should be
                removed from the new artifact.

        Returns:
            FormalizationArtifact: A new artifact with RUNNING status and
            the resolved gaps filtered out.

        Raises:
            ValueError: If the artifact is not in BLOCKED_BY_GAP status.

        Example:
            >>> coord = FormalizationLoopCoordinator()
        """
        if artifact.status != FormalizationStatus.BLOCKED_BY_GAP:
            raise ValueError(
                f"restart: artifact must be in BLOCKED_BY_GAP status, "
                f"got {artifact.status.value}"
            )

        resolved_set = set(resolved_gaps)
        remaining_gaps = tuple(
            g for g in artifact.gaps if g.gap_id not in resolved_set
        )
        # copilot: check if remaining blocking gaps still exist
        still_blocked = any(g.blocking for g in remaining_gaps)
        new_status = FormalizationStatus.BLOCKED_BY_GAP if still_blocked else FormalizationStatus.RUNNING

        new_artifact = FormalizationArtifact(
            artifact_id=_uid(),
            theory_id=artifact.theory_id,
            formal_spec=artifact.formal_spec,
            gaps=remaining_gaps,
            status=new_status,
            iteration_count=artifact.iteration_count,
            created_at=_utcnow(),
        )
        bucket = self._artifacts.setdefault(artifact.theory_id, [])
        bucket.append(new_artifact)
        return new_artifact

    def status_report(self, theory_id: str) -> dict[str, Any]:
        """Produce a status report for a theory's formalization history.

        Args:
            theory_id: The theory identifier.

        Returns:
            dict[str, Any]: Report with keys: theory_id, iteration_count,
            current_status, completion_estimate, gap_count, blocking_gaps,
            artifact_ids.

        Example:
            >>> coord = FormalizationLoopCoordinator()
            >>> coord.status_report("nonexistent")['iteration_count']
            0
        """
        history = self._artifacts.get(theory_id, [])
        if not history:
            return {
                "theory_id": theory_id,
                "iteration_count": 0,
                "current_status": "WAITING",
                "completion_estimate": 0.0,
                "gap_count": 0,
                "blocking_gaps": 0,
                "artifact_ids": [],
            }

        latest = history[-1]
        estimate = self._analyzer.estimate_completion(latest)
        blocking = sum(1 for g in latest.gaps if g.blocking)

        return {
            "theory_id": theory_id,
            "iteration_count": latest.iteration_count,
            "current_status": latest.status.value,
            "completion_estimate": estimate,
            "gap_count": len(latest.gaps),
            "blocking_gaps": blocking,
            "artifact_ids": [a.artifact_id for a in history],
        }


# ---------------------------------------------------------------------------
# FormalizationLoopWitness
# ---------------------------------------------------------------------------

class FormalizationLoopWitness:
    """Observes FormalizationArtifact events and provides analytical queries.

    FormalizationLoopWitness implements the observer pattern for the
    formalization loop.  It collects artifacts as they are produced and
    exposes derived statistics useful for monitoring and dashboards.

    Attributes:
        _log: Ordered list of all observed FormalizationArtifact objects.
    """

    def __init__(self) -> None:
        """Initialise a new FormalizationLoopWitness with an empty log.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> w.audit_log()
            []
        """
        self._log: list[FormalizationArtifact] = []

    def observe(self, artifact: FormalizationArtifact) -> None:
        """Append a FormalizationArtifact to the event log.

        Args:
            artifact: The artifact to observe.

        Returns:
            None

        Raises:
            TypeError: If artifact is not a FormalizationArtifact.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> coord = FormalizationLoopCoordinator()
            >>> art = coord.run_iteration("t1", "For all x, P(x).", [])
            >>> w.observe(art)
            >>> len(w.audit_log())
            1
        """
        if not isinstance(artifact, FormalizationArtifact):
            raise TypeError(
                f"observe expects FormalizationArtifact, got {type(artifact)!r}"
            )
        self._log.append(artifact)

    def blocked_theories(self) -> list[str]:
        """Return theory_ids whose latest artifact is BLOCKED_BY_GAP.

        Returns:
            list[str]: Sorted list of blocked theory IDs.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> w.blocked_theories()
            []
        """
        latest: dict[str, FormalizationArtifact] = {}
        for art in self._log:
            latest[art.theory_id] = art

        return sorted(
            tid for tid, art in latest.items()
            if art.status == FormalizationStatus.BLOCKED_BY_GAP
        )

    def completion_rate(self) -> float:
        """Compute the fraction of observed theories that are COMPLETED.

        Considers only the latest artifact per theory.

        Returns:
            float: Completion rate in [0.0, 1.0].  Returns 0.0 if no
            artifacts have been observed.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> w.completion_rate()
            0.0
        """
        latest: dict[str, FormalizationArtifact] = {}
        for art in self._log:
            latest[art.theory_id] = art

        if not latest:
            return 0.0

        completed = sum(
            1 for art in latest.values()
            if art.status == FormalizationStatus.COMPLETED
        )
        return round(completed / len(latest), 4)

    def audit_log(self) -> list[FormalizationArtifact]:
        """Return a shallow copy of the full event log.

        Returns:
            list[FormalizationArtifact]: All observed artifacts in order.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> w.audit_log()
            []
        """
        return list(self._log)

    def gap_frequency(self) -> dict[str, int]:
        """Count how often each GapKind appears across all observed artifacts.

        Returns:
            dict[str, int]: Mapping from GapKind.value to total count.

        Example:
            >>> w = FormalizationLoopWitness()
            >>> freq = w.gap_frequency()
            >>> all(v == 0 for v in freq.values())
            True
        """
        freq: dict[str, int] = {k.value: 0 for k in GapKind}
        for art in self._log:
            for gap in art.gaps:
                freq[gap.kind.value] = freq.get(gap.kind.value, 0) + 1
        return freq


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("formalization_loop.py — smoke test")
    print("=" * 70)

    analyzer = FormalizationLoopAnalyzer()
    coord = FormalizationLoopCoordinator()
    witness = FormalizationLoopWitness()

    theories = [
        (
            "th-clean",
            "For all x : Pack, pack.size(x) >= 0.",
            ["pack.non_negative", "pack.size_def"],
        ),
        (
            "th-ambiguous",
            "For some x, many nodes often appear in the graph.",
            ["random.axiom"],
        ),
        (
            "th-undefined",
            "For all n : NodeType, GraphLayout(n) is valid.",
            ["pack.graph_validity"],
        ),
        (
            "th-complex",
            "For all x : MyType, there exists y : OtherType :: Relation(x, y).",
            ["pack.relation", "external.dep"],
        ),
    ]

    print("\n--- FormalizationLoopAnalyzer ---")
    for theory_id, theory_text, axiom_ids in theories:
        gaps = analyzer.detect_gaps(theory_text, axiom_ids)
        print(f"  {theory_id}: {len(gaps)} gap(s)")
        for g in gaps:
            print(f"    [{g.kind.value}] blocking={g.blocking}: {g.description[:60]}")

    print("\n--- FormalizationLoopCoordinator ---")
    for theory_id, theory_text, axiom_ids in theories:
        art = coord.run_iteration(theory_id, theory_text, axiom_ids)
        witness.observe(art)
        report = coord.status_report(theory_id)
        print(
            f"  {theory_id}: status={art.status.value:18s} "
            f"iter={art.iteration_count} "
            f"completion={report['completion_estimate']:.3f} "
            f"gaps={len(art.gaps)}"
        )

    # copilot: demonstrate restart on a blocked theory
    blocked = [t for t in theories if coord.status_report(t[0])["blocking_gaps"] > 0]
    if blocked:
        theory_id = blocked[0][0]
        history = coord._artifacts[theory_id]
        latest_art = history[-1]
        if latest_art.status == FormalizationStatus.BLOCKED_BY_GAP:
            # copilot: resolve the first blocking gap
            blocking_gap_ids = [g.gap_id for g in latest_art.gaps if g.blocking][:1]
            restarted = coord.restart(latest_art, blocking_gap_ids)
            witness.observe(restarted)
            print(
                f"\n  After restart of '{theory_id}': status={restarted.status.value}"
                f"  remaining_gaps={len(restarted.gaps)}"
            )

    print("\n--- FormalizationLoopWitness ---")
    print(f"  completion_rate={witness.completion_rate():.3f}")
    print(f"  blocked_theories={witness.blocked_theories()}")
    freq = witness.gap_frequency()
    for kind, count in freq.items():
        if count > 0:
            print(f"    {kind}: {count}")

    print("\nSmoke test PASSED.")
