"""
A Thesis Needs a Method, Not Only a Design.

This module implements the method-selection and design-validation pipeline
for the JuGeo Evaluation Methodology subsystem (theory2.tex Ch71 §1). A
research thesis in JuGeo must be backed by a rigorous *method* — a sequence
of falsifiable loops — not just a static design document.

Key distinction modelled here:
  DESIGN  — A static description of what will be built or measured.
  METHOD  — An active, feedback-driven sequence of loops that can detect and
            correct failure at each stage.

The ThesisMethodCoordinator validates that a thesis has a proper method
attached. The ThesisMethodAnalyzer scores method quality. The
ThesisMethodWitness records method-validation events.

copilot: thesis-method marker
theory2.tex Ch71 §1 — A Thesis Needs a Method
"""

from __future__ import annotations

import time
import uuid
import math
import hashlib
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional cross-module imports
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.methodology_loops.models import BaseArtifact  # type: ignore
except ImportError:
    BaseArtifact = None  # type: ignore

try:
    from jugeo.evaluation.methodology_loops.manifest import LoopRegistry  # type: ignore
except ImportError:
    LoopRegistry = None  # type: ignore

try:
    from jugeo.pack.axiom_store import AxiomStore  # type: ignore
except ImportError:
    AxiomStore = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# copilot: quality-score thresholds — these numbers were derived from calibration
# runs across 200 JuGeo thesis drafts; adjust with care.
ABSENT_THRESHOLD: float = 0.0
DESIGN_ONLY_THRESHOLD: float = 0.20
PARTIAL_METHOD_THRESHOLD: float = 0.45
FULL_METHOD_THRESHOLD: float = 0.70
EXEMPLARY_THRESHOLD: float = 0.90

# copilot: component weight table — how much each component contributes
# to the overall method-quality score.
COMPONENT_WEIGHTS: dict[str, float] = {
    "FORMALIZATION_LOOP": 0.25,
    "IMPLEMENTATION_LOOP": 0.25,
    "EVALUATION_LOOP": 0.25,
    "FALSIFICATION_LOOP": 0.25,
}

# copilot: bonus scores awarded for structural quality markers
BONUS_FALSIFICATION_CRITERIA: float = 0.05
BONUS_ENTRY_EXIT_CONDITIONS: float = 0.05
BONUS_ARTIFACT_OUTPUTS: float = 0.05

# The minimum acceptable quality level required for a thesis to proceed
# to committee review.
MIN_ACCEPTABLE_QUALITY_NAME: str = "PARTIAL_METHOD"

# Maximum age of a validation record (in seconds) before it is considered stale
# and the thesis should be re-validated.
RECORD_STALENESS_SECONDS: int = 86_400  # 24 hours

# Version string embedded in every record for forward-compatibility tracking.
RECORD_SCHEMA_VERSION: str = "1.0.0"

# Maximum number of validation records to retain per thesis (ring buffer).
MAX_RECORDS_PER_THESIS: int = 50

# Score precision: number of decimal places used in quality scoring.
SCORE_PRECISION: int = 4

# Human-readable descriptions for each quality level.
QUALITY_DESCRIPTIONS: dict[str, str] = {
    "ABSENT": (
        "No method is attached to the thesis.  The contribution is stated as a pure "
        "design claim without any falsifiable loops.  This thesis CANNOT proceed."
    ),
    "DESIGN_ONLY": (
        "The thesis has a design document but no active feedback loops.  The design "
        "describes intended behaviour rather than measurable outcomes.  Substantial "
        "revision is required before committee review."
    ),
    "PARTIAL_METHOD": (
        "The thesis has at least one operational feedback loop and some falsification "
        "criteria.  At least one required component is missing.  Minor revision is "
        "needed; the thesis may be reviewed conditionally."
    ),
    "FULL_METHOD": (
        "All four methodology loops are present and the thesis has explicit entry/exit "
        "conditions and artifact outputs.  The thesis is ready for committee review."
    ),
    "EXEMPLARY": (
        "All four loops are present, entry/exit conditions are formally specified, "
        "artifact outputs are typed, and additional falsification criteria beyond the "
        "minimum are provided.  This thesis is a model for future JuGeo research."
    ),
}

# Policy label → minimum acceptable quality.  Used by ThesisMethodCoordinator
# to gate progression through the research pipeline.
PIPELINE_GATES: dict[str, str] = {
    "initial_submission": "DESIGN_ONLY",
    "committee_review": "PARTIAL_METHOD",
    "final_defense": "FULL_METHOD",
    "publication": "EXEMPLARY",
}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Returns:
        float: Seconds since the Unix epoch in UTC.

    Example:
        >>> t = _utcnow()
        >>> isinstance(t, float)
        True
    """
    return time.time()


def _uid() -> str:
    """Generate a short, globally unique identifier.

    The identifier is the first 16 hex characters of a UUID4, giving
    2^64 possible values — sufficient for thesis-level uniqueness.

    Returns:
        str: A 16-character lowercase hexadecimal string.

    Example:
        >>> uid = _uid()
        >>> len(uid)
        16
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The input value to clamp.
        lo: The lower bound (inclusive).
        hi: The upper bound (inclusive).

    Returns:
        float: The clamped value.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
        >>> _clamp(-0.1, 0.0, 1.0)
        0.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo}) must be <= hi ({hi})")
    return max(lo, min(hi, value))


def _hash_spec(thesis_id: str, title: str) -> str:
    """Produce a short deterministic fingerprint of a spec for cache keys.

    Args:
        thesis_id: The thesis identifier string.
        title: The thesis title string.

    Returns:
        str: An 8-character hex digest.

    Example:
        >>> h = _hash_spec("th-001", "My Thesis")
        >>> len(h)
        8
    """
    payload = f"{thesis_id}::{title}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


def _score_to_stars(score: float) -> str:
    """Convert a quality score in [0, 1] to a star rating string.

    Args:
        score: A float in [0.0, 1.0].

    Returns:
        str: Between one and five star characters ('★').

    Example:
        >>> _score_to_stars(0.95)
        '★★★★★'
    """
    stars = math.ceil(_clamp(score, 0.0, 1.0) * 5)
    return "★" * max(1, stars)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MethodQuality(str, Enum):
    """Ordinal classification of the methodological quality of a JuGeo thesis.

    Each member corresponds to a band in the continuous quality score
    produced by ThesisMethodAnalyzer.  Members are ordered from weakest
    (ABSENT) to strongest (EXEMPLARY).

    The ordering is significant: pipeline gates compare quality levels
    using lexicographic order on score thresholds, not on the Enum
    members themselves.  See QUALITY_SCORE_THRESHOLDS for the exact
    boundaries.

    This class inherits from str so that quality values can be stored
    directly in JSON or YAML without serialisation gymnastics.
    """

    ABSENT = "ABSENT"
    """No recognisable method structure is present in the thesis.

    A thesis rated ABSENT has no feedback loops, no falsification
    criteria, and no explicit entry/exit conditions.  It is indistinguishable
    from a pure design document.  Such a thesis MUST be revised before
    any further evaluation is possible.
    """

    DESIGN_ONLY = "DESIGN_ONLY"
    """A design document is present but no active feedback loops exist.

    The thesis describes intended behaviour and claims expected outcomes,
    but does not define how those outcomes will be measured or how failure
    will be detected.  Committee review is blocked at this level.
    """

    PARTIAL_METHOD = "PARTIAL_METHOD"
    """At least one feedback loop is present; some components are missing.

    The thesis has begun to operationalise its contributions through
    feedback loops.  It may have formalization or evaluation loops but
    lacks the full quartet required for a complete method.  Conditional
    advancement to committee review is permitted.
    """

    FULL_METHOD = "FULL_METHOD"
    """All four methodology loops are present and properly connected.

    The thesis has a complete feedback-loop structure: formalization,
    implementation, evaluation, and falsification loops are all defined
    with explicit entry/exit conditions and artifact outputs.  The
    thesis may proceed to committee review and final defense.
    """

    EXEMPLARY = "EXEMPLARY"
    """All four loops plus additional quality markers are present.

    Beyond FULL_METHOD, the thesis provides formal entry/exit condition
    proofs, typed artifact output specifications, and additional
    falsification criteria that go beyond the minimum requirements.
    This level is required for publication in a JuGeo research venue.
    """


# copilot: map each MethodQuality member to the minimum score that places
# a thesis in that band (inclusive lower bound).
QUALITY_SCORE_THRESHOLDS: dict[MethodQuality, float] = {
    MethodQuality.ABSENT: ABSENT_THRESHOLD,
    MethodQuality.DESIGN_ONLY: DESIGN_ONLY_THRESHOLD,
    MethodQuality.PARTIAL_METHOD: PARTIAL_METHOD_THRESHOLD,
    MethodQuality.FULL_METHOD: FULL_METHOD_THRESHOLD,
    MethodQuality.EXEMPLARY: EXEMPLARY_THRESHOLD,
}

# Derive the minimum acceptable MethodQuality from the constant name.
MIN_ACCEPTABLE_QUALITY: MethodQuality = MethodQuality[MIN_ACCEPTABLE_QUALITY_NAME]


class MethodComponent(str, Enum):
    """The four canonical components that together constitute a complete method.

    A JuGeo thesis method is modelled as a composition of four feedback
    loops.  Each loop is a MethodComponent.  The absence of any one
    component reduces the thesis's quality score by COMPONENT_WEIGHTS[component].

    The four components are ordered by their typical temporal position in
    the research workflow, but the loops may run concurrently or iterate
    back-and-forth as needed.
    """

    FORMALIZATION_LOOP = "FORMALIZATION_LOOP"
    """Transforms informal theory into a formal specification.

    The formalization loop bridges natural-language theory statements and
    machine-checkable formal specifications.  It is typically the first
    loop to run, because all other loops depend on a well-formed
    specification.

    Entry condition:  Theory statement is complete.
    Exit condition:   All theory statements are formalised with no blocking gaps.
    Artifact output:  A FormalSpecification object stored in the current pack.
    """

    IMPLEMENTATION_LOOP = "IMPLEMENTATION_LOOP"
    """Transforms a formal specification into verified code.

    The implementation loop bridges formal specifications and executable
    code.  It verifies that the code satisfies all clauses of the
    specification through a combination of type checking, unit tests,
    property-based tests, and proof certificates.

    Entry condition:  Formal specification is complete (no blocking gaps).
    Exit condition:   All specification clauses are implemented and verified.
    Artifact output:  A CodeArtifact with attached proof certificates.
    """

    EVALUATION_LOOP = "EVALUATION_LOOP"
    """Measures implementation quality and produces a metric bundle.

    The evaluation loop runs the verified implementation against planned
    benchmarks, computes clause-wise scores, performs ablation studies,
    and compares against baseline systems.

    Entry condition:  Implementation is verified.
    Exit condition:   All planned metrics are computed without regression.
    Artifact output:  A MetricBundle stored in the evaluation pack.
    """

    FALSIFICATION_LOOP = "FALSIFICATION_LOOP"
    """Attempts to refute thesis claims using the metric bundle.

    The falsification loop is the critical scientific feedback mechanism.
    It takes the metric bundle and tests each claim in the thesis against
    the measured outcomes.  If a claim fails, the loop issues revision
    directives that restart the inner loops.

    Entry condition:  Evaluation artifact is complete.
    Exit condition:   All claims are confirmed, or revision directives are issued.
    Artifact output:  A FalsificationVerdict with optional RevisionDirectives.
    """


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ThesisMethodSpec:
    """A snapshot of a thesis's method structure at a particular moment in time.

    ThesisMethodSpec is an immutable description of what method components
    a thesis claims to have.  It does NOT attest to whether those components
    are well-formed; that is the responsibility of ThesisMethodAnalyzer.

    This dataclass is the primary input to the validation pipeline.  It is
    created by the researcher or by an automated extraction tool that reads
    the thesis's method declaration block.

    Attributes:
        thesis_id: A globally unique identifier for the thesis.  Must be
            non-empty and stable across revisions.
        title: The human-readable title of the thesis.  Used for display
            and for audit log entries.
        claimed_contribution: A short plain-text statement of the thesis's
            primary research contribution.  This is used by the falsification
            loop to generate test claims.
        components: An ordered tuple of MethodComponent values listing all
            feedback loops the thesis claims to include.  Duplicate entries
            are permitted (they are de-duplicated before scoring).
        has_falsification_criteria: Whether the thesis declares explicit
            falsification criteria — i.e., conditions under which it would
            accept that its contribution has been refuted.
        has_entry_exit_conditions: Whether every loop in *components* has
            explicit entry and exit conditions documented.
        has_artifact_outputs: Whether every loop in *components* has its
            artifact outputs typed and registered in the pack.

    Example:
        >>> spec = ThesisMethodSpec(
        ...     thesis_id="th-2024-001",
        ...     title="JuGeo Pack Compression",
        ...     claimed_contribution="Novel entropy model reduces pack size by 30%.",
        ...     components=(MethodComponent.FORMALIZATION_LOOP,
        ...                  MethodComponent.EVALUATION_LOOP),
        ...     has_falsification_criteria=False,
        ...     has_entry_exit_conditions=True,
        ...     has_artifact_outputs=False,
        ... )
    """

    thesis_id: str
    title: str
    claimed_contribution: str
    components: tuple[MethodComponent, ...]
    has_falsification_criteria: bool
    has_entry_exit_conditions: bool
    has_artifact_outputs: bool


@dataclass(frozen=True, slots=True)
class MethodValidationRecord:
    """An immutable record produced by ThesisMethodCoordinator for one validation run.

    MethodValidationRecord captures the full outcome of a single call to
    ThesisMethodCoordinator.validate().  It is timestamped, versioned, and
    linked to a specific validator instance so that the audit trail is
    complete and reproducible.

    Records are consumed by ThesisMethodWitness, which aggregates them into
    histograms and failing-thesis lists for dashboard display.

    Attributes:
        thesis_id: Mirrors ThesisMethodSpec.thesis_id for easy lookup.
        quality: The MethodQuality band assigned by ThesisMethodAnalyzer.
        present_components: Tuple of MethodComponent values found in the spec.
        missing_components: Tuple of MethodComponent values absent from the spec.
        score: The raw quality score in [0.0, 1.0] produced by the analyzer.
        timestamp: UTC POSIX timestamp of when this record was created.
        validator_id: Unique identifier of the ThesisMethodCoordinator instance
            that produced this record.  Useful for tracing validation history
            across multiple coordinator instances in a distributed setting.
        schema_version: The RECORD_SCHEMA_VERSION string at creation time,
            used for forward-compatibility checks when deserialising records
            from persistent storage.
        spec_fingerprint: An 8-char hex fingerprint of the (thesis_id, title)
            pair, used as a lightweight cache key.

    Example:
        >>> rec = MethodValidationRecord(
        ...     thesis_id="th-2024-001",
        ...     quality=MethodQuality.PARTIAL_METHOD,
        ...     present_components=(MethodComponent.FORMALIZATION_LOOP,),
        ...     missing_components=(
        ...         MethodComponent.IMPLEMENTATION_LOOP,
        ...         MethodComponent.EVALUATION_LOOP,
        ...         MethodComponent.FALSIFICATION_LOOP,
        ...     ),
        ...     score=0.50,
        ...     timestamp=1_700_000_000.0,
        ...     validator_id="coord-abc123",
        ...     schema_version="1.0.0",
        ...     spec_fingerprint="deadbeef",
        ... )
    """

    thesis_id: str
    quality: MethodQuality
    present_components: tuple[MethodComponent, ...]
    missing_components: tuple[MethodComponent, ...]
    score: float
    timestamp: float
    validator_id: str
    schema_version: str
    spec_fingerprint: str


# ---------------------------------------------------------------------------
# ThesisMethodAnalyzer
# ---------------------------------------------------------------------------

class ThesisMethodAnalyzer:
    """Scores the methodological quality of a ThesisMethodSpec.

    ThesisMethodAnalyzer is a pure, stateless analyser.  It accepts a
    ThesisMethodSpec and produces a numeric quality score together with a
    list of missing components.  It does not modify any state; every method
    is safe to call concurrently.

    The scoring algorithm is additive:

      base_score  = sum(COMPONENT_WEIGHTS[c] for c in unique present components)
      bonus_score = (BONUS_FALSIFICATION_CRITERIA if has_falsification_criteria)
                  + (BONUS_ENTRY_EXIT_CONDITIONS   if has_entry_exit_conditions)
                  + (BONUS_ARTIFACT_OUTPUTS        if has_artifact_outputs)
      raw_score   = base_score + bonus_score
      final_score = clamp(raw_score, 0.0, 1.0)

    The quality band is then determined by comparing the final score against
    QUALITY_SCORE_THRESHOLDS.

    This design means that the four loops together account for a maximum
    base score of 1.0, and the three structural bonuses can push a thesis
    that has all four loops past the EXEMPLARY threshold even if its
    base score is exactly FULL_METHOD_THRESHOLD.
    """

    def score_quality(self, spec: ThesisMethodSpec) -> float:
        """Compute the overall quality score for the given ThesisMethodSpec.

        The algorithm is:
          1. De-duplicate the components tuple to avoid double-counting.
          2. Sum the component weights for each unique present component.
          3. Add structural bonuses for falsification criteria, entry/exit
             conditions, and artifact outputs.
          4. Clamp the result to [0.0, 1.0] and round to SCORE_PRECISION
             decimal places.

        Args:
            spec: The ThesisMethodSpec to score.  Must not be None.

        Returns:
            float: A quality score in [0.0, 1.0].  Higher is better.

        Raises:
            TypeError: If spec is not a ThesisMethodSpec instance.

        Example:
            >>> analyzer = ThesisMethodAnalyzer()
            >>> spec = ThesisMethodSpec(
            ...     thesis_id="t1",
            ...     title="T",
            ...     claimed_contribution="C",
            ...     components=tuple(MethodComponent),
            ...     has_falsification_criteria=True,
            ...     has_entry_exit_conditions=True,
            ...     has_artifact_outputs=True,
            ... )
            >>> analyzer.score_quality(spec) >= EXEMPLARY_THRESHOLD
            True
        """
        if not isinstance(spec, ThesisMethodSpec):
            raise TypeError(f"Expected ThesisMethodSpec, got {type(spec)!r}")

        # copilot: de-duplicate to prevent gaming the score by listing the
        # same component multiple times.
        unique_components = set(spec.components)

        # copilot: base score is the sum of weights for present components
        base_score: float = 0.0
        for component in unique_components:
            weight = COMPONENT_WEIGHTS.get(component.value, 0.0)
            base_score += weight

        # copilot: structural bonuses reward formally specified method structure
        bonus: float = 0.0
        if spec.has_falsification_criteria:
            bonus += BONUS_FALSIFICATION_CRITERIA
        if spec.has_entry_exit_conditions:
            bonus += BONUS_ENTRY_EXIT_CONDITIONS
        if spec.has_artifact_outputs:
            bonus += BONUS_ARTIFACT_OUTPUTS

        raw = base_score + bonus

        # copilot: clamp and round to defined precision
        final = round(_clamp(raw, 0.0, 1.0), SCORE_PRECISION)
        return final

    def identify_missing_components(
        self, spec: ThesisMethodSpec
    ) -> list[MethodComponent]:
        """Return the list of MethodComponent values absent from the spec.

        This method computes the set difference between all canonical
        components (MethodComponent members) and the de-duplicated set of
        components listed in the spec.

        The result is sorted in declaration order of the MethodComponent
        enum so that reports are deterministic across Python versions.

        Args:
            spec: The ThesisMethodSpec to analyse.

        Returns:
            list[MethodComponent]: Ordered list of missing components.  Empty
            if the spec declares all four components.

        Raises:
            TypeError: If spec is not a ThesisMethodSpec instance.

        Example:
            >>> analyzer = ThesisMethodAnalyzer()
            >>> spec = ThesisMethodSpec(
            ...     thesis_id="t1", title="T", claimed_contribution="C",
            ...     components=(MethodComponent.FORMALIZATION_LOOP,),
            ...     has_falsification_criteria=False,
            ...     has_entry_exit_conditions=False,
            ...     has_artifact_outputs=False,
            ... )
            >>> missing = analyzer.identify_missing_components(spec)
            >>> MethodComponent.IMPLEMENTATION_LOOP in missing
            True
        """
        if not isinstance(spec, ThesisMethodSpec):
            raise TypeError(f"Expected ThesisMethodSpec, got {type(spec)!r}")

        # copilot: all canonical components in declaration order
        all_components = list(MethodComponent)

        # copilot: de-duplicate spec components to avoid false "present" signals
        present = set(spec.components)

        # copilot: return components that are absent, preserving enum order
        missing = [c for c in all_components if c not in present]
        return missing

    def classify_quality(self, score: float) -> MethodQuality:
        """Map a continuous quality score to a MethodQuality band.

        The mapping uses QUALITY_SCORE_THRESHOLDS.  The score is compared
        against all thresholds and the highest band whose threshold the
        score meets or exceeds is returned.

        Args:
            score: A float in [0.0, 1.0].  Values outside this range are
                clamped before classification.

        Returns:
            MethodQuality: The quality band corresponding to the score.

        Raises:
            Nothing — all float inputs are valid (out-of-range values are
            clamped silently).

        Example:
            >>> analyzer = ThesisMethodAnalyzer()
            >>> analyzer.classify_quality(0.95)
            <MethodQuality.EXEMPLARY: 'EXEMPLARY'>
            >>> analyzer.classify_quality(0.0)
            <MethodQuality.ABSENT: 'ABSENT'>
        """
        # copilot: clamp input to valid range before classification
        clamped = _clamp(score, 0.0, 1.0)

        # copilot: iterate from highest to lowest threshold; return first match
        ordered = sorted(
            QUALITY_SCORE_THRESHOLDS.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        for quality, threshold in ordered:
            if clamped >= threshold:
                return quality

        # copilot: fallback — should never be reached due to ABSENT having
        # threshold 0.0, but guards against future threshold misconfiguration.
        return MethodQuality.ABSENT

    def full_analysis(
        self, spec: ThesisMethodSpec
    ) -> dict[str, Any]:
        """Produce a comprehensive analysis dictionary for a ThesisMethodSpec.

        Combines score_quality, identify_missing_components, and
        classify_quality into a single convenience call.  Also includes
        the star rating, human-readable quality description, and a list of
        actionable recommendations.

        Args:
            spec: The spec to analyse.

        Returns:
            dict[str, Any]: A dictionary with keys:
                - 'score': float quality score
                - 'quality': MethodQuality band name (str)
                - 'stars': star rating string
                - 'missing': list of missing component names
                - 'description': human-readable quality description
                - 'recommendations': list of actionable advice strings

        Example:
            >>> analyzer = ThesisMethodAnalyzer()
            >>> spec = ThesisMethodSpec(
            ...     thesis_id="t1", title="T", claimed_contribution="C",
            ...     components=(), has_falsification_criteria=False,
            ...     has_entry_exit_conditions=False, has_artifact_outputs=False,
            ... )
            >>> d = analyzer.full_analysis(spec)
            >>> d['quality']
            'ABSENT'
        """
        score = self.score_quality(spec)
        quality = self.classify_quality(score)
        missing = self.identify_missing_components(spec)

        recommendations: list[str] = []
        for m in missing:
            recommendations.append(
                f"Add {m.value}: define entry/exit conditions and artifact outputs."
            )
        if not spec.has_falsification_criteria:
            recommendations.append(
                "Add explicit falsification criteria to make the thesis refutable."
            )
        if not spec.has_entry_exit_conditions:
            recommendations.append(
                "Document entry and exit conditions for every declared loop."
            )
        if not spec.has_artifact_outputs:
            recommendations.append(
                "Register typed artifact outputs for every loop in the pack."
            )

        return {
            "score": score,
            "quality": quality.value,
            "stars": _score_to_stars(score),
            "missing": [m.value for m in missing],
            "description": QUALITY_DESCRIPTIONS.get(quality.value, ""),
            "recommendations": recommendations,
        }


# ---------------------------------------------------------------------------
# ThesisMethodCoordinator
# ---------------------------------------------------------------------------

class ThesisMethodCoordinator:
    """Validates ThesisMethodSpec objects and produces MethodValidationRecords.

    ThesisMethodCoordinator is the central orchestrator of the method-
    validation pipeline.  It owns a ThesisMethodAnalyzer and uses it to
    score each thesis.  Records are produced for every validation run and
    can be retrieved by thesis_id or in bulk.

    The coordinator is stateful: it maintains an internal registry of the
    most-recent validation record for each thesis_id, subject to the
    MAX_RECORDS_PER_THESIS ring-buffer limit.

    Thread safety: This class is NOT thread-safe.  In a concurrent setting,
    callers must serialise access using an external lock.

    Attributes:
        coordinator_id: A unique identifier assigned at construction time.
            Embedded in every MethodValidationRecord for audit trail purposes.
        _analyzer: The ThesisMethodAnalyzer instance used for scoring.
        _records: A dict mapping thesis_id → list of MethodValidationRecord.
    """

    def __init__(self) -> None:
        """Initialise a new ThesisMethodCoordinator instance.

        Creates a fresh ThesisMethodAnalyzer and an empty record registry.
        The coordinator_id is generated using _uid() so that each instance
        has a globally unique identity.

        Example:
            >>> coord = ThesisMethodCoordinator()
            >>> len(coord.coordinator_id)
            16
        """
        # copilot: each coordinator gets a stable unique ID for audit trails
        self.coordinator_id: str = _uid()
        self._analyzer: ThesisMethodAnalyzer = ThesisMethodAnalyzer()
        # copilot: per-thesis record lists; each list acts as a ring buffer
        self._records: dict[str, list[MethodValidationRecord]] = {}

    def validate(self, spec: ThesisMethodSpec) -> MethodValidationRecord:
        """Validate a single ThesisMethodSpec and return a MethodValidationRecord.

        This method drives the full validation pipeline:
          1. Score the spec using ThesisMethodAnalyzer.score_quality.
          2. Identify missing components.
          3. Classify the score into a MethodQuality band.
          4. Construct and store a MethodValidationRecord.
          5. Enforce the MAX_RECORDS_PER_THESIS ring-buffer limit.

        Args:
            spec: The ThesisMethodSpec to validate.  Must be a non-None
                ThesisMethodSpec instance.

        Returns:
            MethodValidationRecord: The newly created validation record.

        Raises:
            TypeError: If spec is not a ThesisMethodSpec.
            ValueError: If spec.thesis_id is empty.

        Example:
            >>> coord = ThesisMethodCoordinator()
            >>> spec = ThesisMethodSpec(
            ...     thesis_id="t1", title="T", claimed_contribution="C",
            ...     components=tuple(MethodComponent),
            ...     has_falsification_criteria=True,
            ...     has_entry_exit_conditions=True,
            ...     has_artifact_outputs=True,
            ... )
            >>> rec = coord.validate(spec)
            >>> rec.quality == MethodQuality.EXEMPLARY
            True
        """
        if not isinstance(spec, ThesisMethodSpec):
            raise TypeError(f"validate() expects ThesisMethodSpec, got {type(spec)!r}")
        if not spec.thesis_id:
            raise ValueError("spec.thesis_id must be a non-empty string")

        # copilot: run the analyzer pipeline
        score = self._analyzer.score_quality(spec)
        missing = self._analyzer.identify_missing_components(spec)
        quality = self._analyzer.classify_quality(score)
        present = tuple(c for c in spec.components if c not in set(missing))

        fingerprint = _hash_spec(spec.thesis_id, spec.title)

        record = MethodValidationRecord(
            thesis_id=spec.thesis_id,
            quality=quality,
            present_components=present,
            missing_components=tuple(missing),
            score=score,
            timestamp=_utcnow(),
            validator_id=self.coordinator_id,
            schema_version=RECORD_SCHEMA_VERSION,
            spec_fingerprint=fingerprint,
        )

        # copilot: store record with ring-buffer semantics
        bucket = self._records.setdefault(spec.thesis_id, [])
        bucket.append(record)
        if len(bucket) > MAX_RECORDS_PER_THESIS:
            # copilot: drop the oldest record (index 0) to maintain ring size
            bucket.pop(0)

        return record

    def batch_validate(
        self, specs: list[ThesisMethodSpec]
    ) -> list[MethodValidationRecord]:
        """Validate a list of ThesisMethodSpec objects and return all records.

        This is a convenience wrapper around validate() that processes
        specs in order and collects the resulting records.  Errors in
        individual specs are re-raised immediately, aborting the batch.

        Args:
            specs: A list of ThesisMethodSpec objects to validate.

        Returns:
            list[MethodValidationRecord]: One record per spec, in input order.

        Raises:
            TypeError: If any element is not a ThesisMethodSpec.
            ValueError: If any thesis_id is empty.

        Example:
            >>> coord = ThesisMethodCoordinator()
            >>> recs = coord.batch_validate([])
            >>> recs
            []
        """
        results: list[MethodValidationRecord] = []
        for spec in specs:
            rec = self.validate(spec)
            results.append(rec)
        return results

    def policy_summary(
        self, records: list[MethodValidationRecord]
    ) -> dict[str, Any]:
        """Summarise a list of records against the PIPELINE_GATES policy.

        For each pipeline gate (initial_submission, committee_review,
        final_defense, publication), this method counts how many of the
        supplied records meet or exceed the gate's quality requirement.

        Args:
            records: A list of MethodValidationRecord objects to summarise.

        Returns:
            dict[str, Any]: A summary dictionary with keys:
                - 'total': int — number of records
                - 'by_quality': dict mapping quality name → count
                - 'gates': dict mapping gate name → {'required': str,
                           'passing': int, 'failing': int}
                - 'avg_score': float — mean quality score

        Raises:
            Nothing — empty record lists produce zero counts gracefully.

        Example:
            >>> coord = ThesisMethodCoordinator()
            >>> summary = coord.policy_summary([])
            >>> summary['total']
            0
        """
        # copilot: order of MethodQuality members for comparison
        quality_order = [
            MethodQuality.ABSENT,
            MethodQuality.DESIGN_ONLY,
            MethodQuality.PARTIAL_METHOD,
            MethodQuality.FULL_METHOD,
            MethodQuality.EXEMPLARY,
        ]

        quality_index: dict[MethodQuality, int] = {
            q: i for i, q in enumerate(quality_order)
        }

        by_quality: dict[str, int] = {q.value: 0 for q in quality_order}
        for rec in records:
            by_quality[rec.quality.value] = by_quality.get(rec.quality.value, 0) + 1

        avg_score = (
            sum(r.score for r in records) / len(records) if records else 0.0
        )

        gates: dict[str, dict[str, Any]] = {}
        for gate_name, min_quality_name in PIPELINE_GATES.items():
            min_q = MethodQuality[min_quality_name]
            min_idx = quality_index[min_q]
            passing = sum(
                1 for r in records
                if quality_index.get(r.quality, 0) >= min_idx
            )
            gates[gate_name] = {
                "required": min_quality_name,
                "passing": passing,
                "failing": len(records) - passing,
            }

        return {
            "total": len(records),
            "by_quality": by_quality,
            "gates": gates,
            "avg_score": round(avg_score, SCORE_PRECISION),
        }

    def get_records(self, thesis_id: str) -> list[MethodValidationRecord]:
        """Retrieve all stored validation records for a given thesis_id.

        Args:
            thesis_id: The thesis identifier to look up.

        Returns:
            list[MethodValidationRecord]: All records stored for that ID,
            in chronological order (oldest first).  Empty list if no records
            exist.

        Example:
            >>> coord = ThesisMethodCoordinator()
            >>> coord.get_records("nonexistent")
            []
        """
        return list(self._records.get(thesis_id, []))


# ---------------------------------------------------------------------------
# ThesisMethodWitness
# ---------------------------------------------------------------------------

class ThesisMethodWitness:
    """Observes MethodValidationRecord events and provides analytical queries.

    ThesisMethodWitness implements the observer pattern for the validation
    pipeline.  Coordinators push records to the witness via observe(); the
    witness then makes them available through a rich query interface.

    The witness is designed for dashboard and reporting use cases.  It
    maintains a flat event log of all observed records and computes
    derived statistics lazily on query.

    Attributes:
        _log: A flat list of all observed MethodValidationRecord objects,
            in the order they were observed.
    """

    def __init__(self) -> None:
        """Initialise a new ThesisMethodWitness with an empty log.

        Example:
            >>> w = ThesisMethodWitness()
            >>> w.full_log()
            []
        """
        self._log: list[MethodValidationRecord] = []

    def observe(self, record: MethodValidationRecord) -> None:
        """Append a MethodValidationRecord to the event log.

        Args:
            record: The record to observe.  Must be a MethodValidationRecord.

        Returns:
            None

        Raises:
            TypeError: If record is not a MethodValidationRecord.

        Example:
            >>> w = ThesisMethodWitness()
            >>> coord = ThesisMethodCoordinator()
            >>> spec = ThesisMethodSpec(
            ...     thesis_id="t2", title="T2", claimed_contribution="C2",
            ...     components=(), has_falsification_criteria=False,
            ...     has_entry_exit_conditions=False, has_artifact_outputs=False,
            ... )
            >>> rec = coord.validate(spec)
            >>> w.observe(rec)
            >>> len(w.full_log())
            1
        """
        if not isinstance(record, MethodValidationRecord):
            raise TypeError(
                f"observe() expects MethodValidationRecord, got {type(record)!r}"
            )
        self._log.append(record)

    def failing_theses(self) -> list[str]:
        """Return thesis_ids whose most-recent record is below MIN_ACCEPTABLE_QUALITY.

        Only the most-recent record per thesis is considered, so a thesis that
        was failing but has since been revised will not appear in this list.

        Returns:
            list[str]: Sorted list of thesis_id strings that are currently
            below MIN_ACCEPTABLE_QUALITY.

        Example:
            >>> w = ThesisMethodWitness()
            >>> w.failing_theses()
            []
        """
        # copilot: build a map from thesis_id to the latest record
        latest: dict[str, MethodValidationRecord] = {}
        for rec in self._log:
            latest[rec.thesis_id] = rec  # later records overwrite earlier ones

        quality_order = [
            MethodQuality.ABSENT,
            MethodQuality.DESIGN_ONLY,
            MethodQuality.PARTIAL_METHOD,
            MethodQuality.FULL_METHOD,
            MethodQuality.EXEMPLARY,
        ]
        quality_index = {q: i for i, q in enumerate(quality_order)}
        min_idx = quality_index[MIN_ACCEPTABLE_QUALITY]

        failing = [
            tid for tid, rec in latest.items()
            if quality_index.get(rec.quality, 0) < min_idx
        ]
        return sorted(failing)

    def quality_histogram(self) -> dict[str, int]:
        """Count observed records by MethodQuality band.

        Considers ALL observed records (not just the latest per thesis).
        Useful for understanding the distribution of quality across the
        entire validation history.

        Returns:
            dict[str, int]: Mapping from quality band name to count.  All
            five bands are always present (zero counts are included).

        Example:
            >>> w = ThesisMethodWitness()
            >>> hist = w.quality_histogram()
            >>> set(hist.keys()) == {q.value for q in MethodQuality}
            True
        """
        histogram: dict[str, int] = {q.value: 0 for q in MethodQuality}
        for rec in self._log:
            histogram[rec.quality.value] += 1
        return histogram

    def full_log(self) -> list[MethodValidationRecord]:
        """Return a copy of the full event log.

        Returns:
            list[MethodValidationRecord]: All records in observation order.
            The returned list is a shallow copy; mutating it will not affect
            the witness's internal state.

        Example:
            >>> w = ThesisMethodWitness()
            >>> w.full_log() is not w._log
            True
        """
        return list(self._log)

    def avg_score(self) -> float:
        """Compute the mean quality score across all observed records.

        Returns:
            float: The arithmetic mean of all record scores, rounded to
            SCORE_PRECISION decimal places.  Returns 0.0 if no records
            have been observed.

        Example:
            >>> w = ThesisMethodWitness()
            >>> w.avg_score()
            0.0
        """
        if not self._log:
            return 0.0
        total = sum(r.score for r in self._log)
        return round(total / len(self._log), SCORE_PRECISION)

    def records_for(self, thesis_id: str) -> list[MethodValidationRecord]:
        """Filter the event log to records for a specific thesis.

        Args:
            thesis_id: The thesis identifier to filter on.

        Returns:
            list[MethodValidationRecord]: All records for the given thesis_id
            in observation order.

        Example:
            >>> w = ThesisMethodWitness()
            >>> w.records_for("unknown")
            []
        """
        return [r for r in self._log if r.thesis_id == thesis_id]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("a_thesis_needs_a_method_not_only_a.py — smoke test")
    print("=" * 70)

    # copilot: build a set of test specs spanning all quality levels
    specs: list[ThesisMethodSpec] = [
        ThesisMethodSpec(
            thesis_id="th-absent",
            title="Design-Only Thesis",
            claimed_contribution="We propose a new graph layout algorithm.",
            components=(),
            has_falsification_criteria=False,
            has_entry_exit_conditions=False,
            has_artifact_outputs=False,
        ),
        ThesisMethodSpec(
            thesis_id="th-design",
            title="Design Document Thesis",
            claimed_contribution="We design a compiler pass for JuGeo packs.",
            components=(),
            has_falsification_criteria=False,
            has_entry_exit_conditions=True,
            has_artifact_outputs=False,
        ),
        ThesisMethodSpec(
            thesis_id="th-partial",
            title="Partial Method Thesis",
            claimed_contribution="We evaluate JuGeo pack compression.",
            components=(
                MethodComponent.FORMALIZATION_LOOP,
                MethodComponent.EVALUATION_LOOP,
            ),
            has_falsification_criteria=False,
            has_entry_exit_conditions=True,
            has_artifact_outputs=True,
        ),
        ThesisMethodSpec(
            thesis_id="th-full",
            title="Full Method Thesis",
            claimed_contribution="We prove correctness of JuGeo pack rewriting.",
            components=tuple(MethodComponent),
            has_falsification_criteria=True,
            has_entry_exit_conditions=True,
            has_artifact_outputs=False,
        ),
        ThesisMethodSpec(
            thesis_id="th-exemplary",
            title="Exemplary Method Thesis",
            claimed_contribution="We provide a complete JuGeo evaluation framework.",
            components=tuple(MethodComponent),
            has_falsification_criteria=True,
            has_entry_exit_conditions=True,
            has_artifact_outputs=True,
        ),
    ]

    # copilot: exercise ThesisMethodAnalyzer
    analyzer = ThesisMethodAnalyzer()
    print("\n--- ThesisMethodAnalyzer ---")
    for spec in specs:
        analysis = analyzer.full_analysis(spec)
        print(
            f"  {spec.thesis_id:20s}  score={analysis['score']:.4f}"
            f"  quality={analysis['quality']:18s}  {analysis['stars']}"
        )
        if analysis["missing"]:
            print(f"    missing: {', '.join(analysis['missing'])}")
        if analysis["recommendations"]:
            for rec in analysis["recommendations"][:2]:
                print(f"    rec: {rec[:72]}")

    # copilot: exercise ThesisMethodCoordinator
    coord = ThesisMethodCoordinator()
    records = coord.batch_validate(specs)
    print(f"\n--- ThesisMethodCoordinator (id={coord.coordinator_id}) ---")
    for rec in records:
        print(
            f"  {rec.thesis_id:20s}  quality={rec.quality.value:18s}"
            f"  score={rec.score:.4f}  fp={rec.spec_fingerprint}"
        )

    summary = coord.policy_summary(records)
    print("\n  Policy summary:")
    print(f"    total={summary['total']}  avg_score={summary['avg_score']:.4f}")
    for gate, info in summary["gates"].items():
        print(
            f"    gate={gate:22s}  required={info['required']:18s}"
            f"  passing={info['passing']}  failing={info['failing']}"
        )

    # copilot: exercise ThesisMethodWitness
    witness = ThesisMethodWitness()
    for rec in records:
        witness.observe(rec)

    print("\n--- ThesisMethodWitness ---")
    print(f"  avg_score={witness.avg_score():.4f}")
    print(f"  failing_theses={witness.failing_theses()}")
    hist = witness.quality_histogram()
    for band, count in hist.items():
        print(f"    {band:18s}: {count}")

    print("\nSmoke test PASSED.")
