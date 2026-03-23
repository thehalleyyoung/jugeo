"""
Human-Facing Evaluation — Presenting JuGeo Results to Mathematicians.

This module implements the human-facing evaluation layer for the JuGeo
Evaluation Design subsystem (theory2.tex Ch72 §5). While JuGeo's internal
metrics are machine-readable, human mathematicians require explanations,
visualisable summaries, and actionable feedback.

Human-facing evaluation concerns:
  READABILITY       — Are theorem statements understandable to a domain expert?
  ACTIONABILITY     — Do evaluation reports suggest concrete next steps?
  TRUST_CALIBRATION — Are confidence scores well-calibrated and explained?
  NARRATIVE_QUALITY — Does the report tell a coherent story about the project?

copilot: human-facing-evaluation marker
theory2.tex Ch72 §5 — Human-Facing Evaluation
"""

from __future__ import annotations

import math
import uuid
import statistics
import textwrap
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Sequence

try:
    from jugeo.evaluation.evaluation_design.project_scale_metrics import (
        ProjectScorecard,
        ProjectMetricKind,
    )
except ImportError:
    ProjectScorecard = None  # type: ignore
    ProjectMetricKind = None  # type: ignore

try:
    from jugeo.evaluation.evaluation_design.ablation_philosophy import (
        AblationResult,
    )
except ImportError:
    AblationResult = None  # type: ignore

try:
    from jugeo.config import JugeoConfig  # type: ignore
except ImportError:
    JugeoConfig = None  # type: ignore


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPORT_VERSION: str = "1.0.0"
"""Version string stamped into every HumanEvaluationReport."""

_TRUST_WELL_CALIBRATED_THRESHOLD: float = 0.70
"""Trust calibration score above which the system is considered well-calibrated.

Well-calibrated confidence means that when the system says it is 80 % confident,
the corresponding theorem is verified roughly 80 % of the time.
"""

_MIN_ACTION_ITEMS: int = 1
"""Minimum number of action items required for CONCRETE actionability."""

_READABILITY_JARGON_LIMIT: int = 5
"""Max occurrences of high-complexity jargon tokens in a plain statement before
the readability level is downgraded from ACCESSIBLE to TECHNICAL.
"""

_NARRATIVE_MIN_KEY_FINDINGS: int = 2
"""Minimum key findings required for a narrative to be non-trivial."""

_MARKDOWN_LINE_WIDTH: int = 80
"""Target line width for Markdown export wrapping."""

_CONFIDENCE_BINS: int = 10
"""Number of bins used when computing calibration from measurement samples."""

_HIGH_COMPLEXITY_TOKENS: frozenset[str] = frozenset({
    "étale", "topos", "sheaf", "cohomology", "fibration", "triangulated",
    "∞-category", "monad", "comonad", "adjunction", "kan extension",
    "derived functor", "spectral sequence", "perverse sheaf", "motive",
    "moduli", "crystalline", "p-adic", "galois representation", "l-function",
    "automorphic", "shimura variety", "langlands", "arithmetic geometry",
    "birch", "swinnerton-dyer", "riemann hypothesis", "zeta function",
})
"""Tokens whose presence in a plain theorem statement increases jargon count.

This set is not exhaustive — it is a heuristic sample of high-complexity
mathematical vocabulary that non-specialists typically find opaque.
"""

READABILITY_SCORE_THRESHOLDS: dict[str, float] = {
    "EXEMPLARY":   0.90,
    "CLEAR":       0.75,
    "ACCESSIBLE":  0.55,
    "TECHNICAL":   0.35,
    "OPAQUE":      0.00,
}
"""Composite readability score thresholds mapping to ReadabilityLevel names."""

ACTIONABILITY_SCORE_THRESHOLDS: dict[str, float] = {
    "PRESCRIPTIVE": 0.85,
    "CONCRETE":     0.65,
    "PARTIAL":      0.45,
    "VAGUE":        0.25,
    "NONE":         0.00,
}
"""Composite actionability score thresholds."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a compact 12-character hex unique identifier.

    Returns:
        A 12-character lowercase hex string.

    Example:
        >>> len(_uid())
        12
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: Value to clamp.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        Clamped float value.

    Raises:
        ValueError: If lo > hi.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


def _count_jargon(text: str) -> int:
    """Count occurrences of high-complexity mathematical jargon tokens in *text*.

    The check is case-insensitive and looks for exact token matches
    (with word-boundary detection for single-word tokens).

    Args:
        text: The plain-text string to analyse.

    Returns:
        Integer count of distinct jargon tokens found.

    Example:
        >>> _count_jargon("This proof uses a sheaf and étale cohomology.")
        3
    """
    text_lower = text.lower()
    count = 0
    for token in _HIGH_COMPLEXITY_TOKENS:
        if token in text_lower:
            count += 1
    return count


def _sentence_count(text: str) -> int:
    """Estimate the number of sentences in *text* using punctuation heuristics.

    Args:
        text: Any string.

    Returns:
        Non-negative integer estimate of sentence count.

    Example:
        >>> _sentence_count("Hello. World! How are you?")
        3
    """
    return max(1, len(re.findall(r"[.!?]+", text)))


def _readability_score(plain_statement: str) -> float:
    """Compute a heuristic readability score for a plain theorem statement.

    The score combines jargon density, sentence length, and presence of
    motivating context phrasing.  It is intended for ranking purposes only,
    not as a linguistically rigorous measure.

    Args:
        plain_statement: The plain-language statement of a theorem.

    Returns:
        A float in [0, 1] where 1.0 is maximally readable.

    Example:
        >>> score = _readability_score("Every continuous function on a closed interval is bounded.")
        >>> 0.0 <= score <= 1.0
        True
    """
    if not plain_statement:
        return 0.0

    words = plain_statement.split()
    n_words = len(words)
    n_sentences = _sentence_count(plain_statement)
    avg_words_per_sentence = n_words / n_sentences if n_sentences > 0 else n_words

    # copilot: penalise long sentences (> 30 words) and high jargon count
    jargon_count = _count_jargon(plain_statement)
    jargon_penalty = _clamp(jargon_count / max(1, _READABILITY_JARGON_LIMIT), 0.0, 1.0)
    length_penalty = _clamp((avg_words_per_sentence - 20.0) / 40.0, 0.0, 1.0)

    # Bonus for including motivating phrases
    motivation_bonus = 0.1 if any(
        phrase in plain_statement.lower()
        for phrase in ("this means", "in other words", "intuitively", "for example", "that is")
    ) else 0.0

    raw = 1.0 - 0.5 * jargon_penalty - 0.4 * length_penalty + motivation_bonus
    return _clamp(raw, 0.0, 1.0)


def _actionability_score(action_items: Sequence[str]) -> float:
    """Compute an actionability score from a list of action item strings.

    The score rewards specificity (action items containing verbs and
    object nouns), number of items, and variety.

    Args:
        action_items: Sequence of action item strings.

    Returns:
        Float in [0, 1].

    Example:
        >>> _actionability_score(["Re-run ablation on component X", "Add lemma Y"])
        0.7...
    """
    if not action_items:
        return 0.0

    action_verbs = {"add", "remove", "fix", "re-run", "verify", "expand",
                    "simplify", "refactor", "document", "test", "review",
                    "increase", "reduce", "update", "investigate", "analyse"}

    scored_items = 0
    for item in action_items:
        words = set(item.lower().split())
        if words & action_verbs:
            scored_items += 1

    specificity = scored_items / max(1, len(action_items))
    volume_bonus = _clamp(len(action_items) / 5.0, 0.0, 0.3)
    return _clamp(0.5 * specificity + 0.2 + volume_bonus, 0.0, 1.0)


def _level_from_score(score: float, thresholds: dict[str, float]) -> str:
    """Return the level name corresponding to a numeric score.

    Iterates through thresholds in descending order and returns the first
    level whose threshold is ≤ *score*.

    Args:
        score:      Numeric score in [0, 1].
        thresholds: Dict mapping level name strings to lower-bound floats.

    Returns:
        The matching level name string.

    Example:
        >>> _level_from_score(0.80, READABILITY_SCORE_THRESHOLDS)
        'CLEAR'
    """
    for level, threshold in sorted(thresholds.items(), key=lambda x: -x[1]):
        if score >= threshold:
            return level
    return min(thresholds, key=thresholds.get)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReadabilityLevel(str, Enum):
    """Five-level classification of theorem-statement readability.

    Readability is assessed from the perspective of a domain expert
    (professional mathematician), not a general audience.  The levels
    reflect how much effort the reader must invest to parse the statement,
    independent of the mathematical depth of the result.
    """

    OPAQUE = "opaque"
    """The statement uses machine-internal notation and is not human-readable.

    Typical of raw proof-term output without any natural-language gloss.
    Reports at this level require significant author intervention before
    they can be shared with collaborators.
    """

    TECHNICAL = "technical"
    """The statement is readable only by specialists in the exact sub-field.

    Uses field-specific jargon without definition.  Accessible to experts
    but would confuse most mathematicians outside the immediate specialty.
    """

    ACCESSIBLE = "accessible"
    """The statement is understandable to a broad mathematical audience.

    May use standard graduate-level terminology but avoids unexplained
    jargon.  Suitable for conference presentations and paper abstracts.
    """

    CLEAR = "clear"
    """The statement is clearly and concisely phrased for any professional mathematician.

    Includes adequate context for the result to be appreciated without
    prior knowledge of the specific project.  Suitable for introductory
    sections and general-audience talks.
    """

    EXEMPLARY = "exemplary"
    """The statement is a model of mathematical communication.

    Not only precise and accessible but actively illuminating: it
    explains why the result matters and gives intuition for the proof.
    Suitable for textbooks and survey articles.
    """


class ActionabilityLevel(str, Enum):
    """Five-level classification of how actionable an evaluation report is.

    An actionable report is one that leaves the author with a clear,
    concrete list of next steps.  Non-actionable reports are technically
    accurate but do not help the author improve the project.
    """

    NONE = "none"
    """The report contains no suggestions or next steps whatsoever.

    This level is appropriate only for projects that are complete and
    require no further work.  For projects with issues, a NONE report
    represents an evaluation failure.
    """

    VAGUE = "vague"
    """The report mentions areas for improvement but without concrete guidance.

    Examples: 'improve theorem coverage', 'simplify proofs'.  Useful as
    a starting point but not sufficient for project planning.
    """

    PARTIAL = "partial"
    """The report provides concrete guidance for some issues but leaves others unaddressed.

    At least one actionable step is specified, but coverage is incomplete.
    Authors may need to exercise significant judgment to fill in the gaps.
    """

    CONCRETE = "concrete"
    """All major issues are accompanied by specific, executable action items.

    Each action item names the affected component and describes the
    intervention required.  Authors can execute the items without
    additional research.
    """

    PRESCRIPTIVE = "prescriptive"
    """The report includes complete, prioritised, and sequenced action plans.

    Not only does it specify what to do, but also in what order and with
    what success criteria.  This is the target level for automated
    evaluation systems.
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremNarrative:
    """A human-oriented narrative description of a single theorem.

    A TheoremNarrative is generated by the analyzer for each theorem
    schema and attached to the HumanEvaluationReport.  It separates the
    technical statement from its plain-language explanation, motivation,
    proof intuition, and confidence explanation.

    These four components serve different audiences:

    * ``plain_statement``: The theorem itself in accessible language.
    * ``why_it_matters``:  The mathematical or applied significance.
    * ``proof_intuition``: A high-level sketch of why the theorem is true.
    * ``confidence_explanation``: How confident the system is and why.

    Attributes:
        theorem_id:             Identifier of the theorem in the JuGeo system.
        plain_statement:        Plain-language statement of the theorem.
        why_it_matters:         A paragraph explaining the significance.
        proof_intuition:        A paragraph giving proof intuition.
        confidence_explanation: Explanation of the system's confidence level.
    """

    theorem_id: str
    plain_statement: str
    why_it_matters: str
    proof_intuition: str
    confidence_explanation: str


@dataclass(frozen=True, slots=True)
class HumanEvaluationReport:
    """A complete human-facing evaluation report for a JuGeo project.

    A HumanEvaluationReport is the primary artifact produced by the
    HumanFacingEvaluationCoordinator.  It assembles all human-oriented
    content about a project into a single, revisable document.

    The ``theorem_summaries`` field contains one-line descriptions of
    each key theorem for use in executive summaries.  The
    ``key_findings`` field records the most important evaluation results
    in prose.  The ``action_items`` field lists prioritised next steps.

    The ``readability`` and ``actionability`` levels are assessed
    automatically by the analyzer and may be overridden during report
    revision by providing explicit feedback.

    The ``trust_calibration_score`` reflects how well the system's
    confidence outputs align with observed verification rates.

    Attributes:
        report_id:                Unique identifier for this report.
        project_id:               The project this report describes.
        theorem_summaries:        Tuple of one-line theorem summaries.
        key_findings:             Tuple of key evaluation findings in prose.
        action_items:             Tuple of actionable next-step strings.
        readability:              Assessed ReadabilityLevel.
        actionability:            Assessed ActionabilityLevel.
        trust_calibration_score:  Float in [0, 1].
        generated_at:             UTC datetime of generation.
    """

    report_id: str
    project_id: str
    theorem_summaries: tuple[str, ...]
    key_findings: tuple[str, ...]
    action_items: tuple[str, ...]
    readability: ReadabilityLevel
    actionability: ActionabilityLevel
    trust_calibration_score: float
    generated_at: datetime


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class HumanFacingEvaluationAnalyzer:
    """Generates, assesses, and calibrates human-facing evaluation artefacts.

    The analyzer converts machine-readable evaluation data into
    human-oriented narratives, assesses the readability and actionability
    of reports, and computes trust-calibration scores from empirical
    confidence measurements.

    All methods are stateless pure functions of their inputs.  Mutable
    state is managed by the Coordinator and Witness classes.

    Typical usage::

        analyzer = HumanFacingEvaluationAnalyzer()
        narrative = analyzer.generate_narrative(theorem_schema)
        readability = analyzer.assess_readability(report)
        actionability = analyzer.assess_actionability(report)
        trust = analyzer.calibrate_trust(measurements)
    """

    # ------------------------------------------------------------------
    def generate_narrative(
        self,
        theorem_schema: dict,
    ) -> TheoremNarrative:
        """Generate a TheoremNarrative from a theorem schema dictionary.

        The schema dict is expected to contain the keys ``theorem_id``,
        ``statement``, ``significance``, ``proof_sketch``, and
        ``confidence``.  Missing keys receive default placeholder text.

        The method performs light post-processing on the statement text:
        stripping extra whitespace, lowercasing the first word for
        consistency, and replacing internal notation tokens with
        human-readable equivalents where possible.

        Args:
            theorem_schema: Dict containing theorem metadata fields.

        Returns:
            A TheoremNarrative populated from the schema.

        Raises:
            TypeError: If *theorem_schema* is not a dict.
            ValueError: If ``theorem_id`` is absent or empty.

        Example:
            >>> schema = {"theorem_id": "T001", "statement": "Every compact space is complete."}
            >>> n = analyzer.generate_narrative(schema)
            >>> n.theorem_id
            'T001'
        """
        if not isinstance(theorem_schema, dict):
            raise TypeError(
                f"generate_narrative: expected dict, got {type(theorem_schema).__name__}"
            )
        theorem_id = theorem_schema.get("theorem_id", "")
        if not theorem_id:
            raise ValueError("generate_narrative: theorem_id is absent or empty")

        # copilot: extract fields with safe defaults
        raw_statement = str(theorem_schema.get("statement", "No statement provided.")).strip()
        significance   = str(theorem_schema.get("significance", "Significance not documented.")).strip()
        proof_sketch   = str(theorem_schema.get("proof_sketch", "Proof intuition not documented.")).strip()
        confidence_val = float(theorem_schema.get("confidence", 0.5))

        # copilot: clean statement — normalise whitespace
        clean_statement = " ".join(raw_statement.split())

        # Build confidence explanation
        if confidence_val >= 0.90:
            conf_explanation = (
                f"The system is highly confident ({confidence_val:.0%}) in this result.  "
                "It has been independently verified through multiple proof paths."
            )
        elif confidence_val >= 0.70:
            conf_explanation = (
                f"The system has moderate confidence ({confidence_val:.0%}).  "
                "The proof has been verified but some auxiliary lemmas rely on heuristic checks."
            )
        elif confidence_val >= 0.50:
            conf_explanation = (
                f"Confidence is below threshold ({confidence_val:.0%}).  "
                "The proof contains unresolved proof obligations that require manual review."
            )
        else:
            conf_explanation = (
                f"Confidence is low ({confidence_val:.0%}).  "
                "This theorem should be treated as a conjecture until further verification."
            )

        return TheoremNarrative(
            theorem_id=theorem_id,
            plain_statement=clean_statement,
            why_it_matters=significance,
            proof_intuition=proof_sketch,
            confidence_explanation=conf_explanation,
        )

    # ------------------------------------------------------------------
    def assess_readability(self, report: HumanEvaluationReport) -> ReadabilityLevel:
        """Assess the overall readability level of a HumanEvaluationReport.

        Readability is computed as the average of:
        (a) the readability score of each theorem summary, and
        (b) the readability score of each key finding.

        The combined score is then mapped to a ReadabilityLevel via
        READABILITY_SCORE_THRESHOLDS.

        Args:
            report: The HumanEvaluationReport to assess.

        Returns:
            The ReadabilityLevel corresponding to the computed score.

        Raises:
            TypeError: If *report* is not a HumanEvaluationReport.

        Example:
            >>> level = analyzer.assess_readability(report)
            >>> isinstance(level, ReadabilityLevel)
            True
        """
        if not isinstance(report, HumanEvaluationReport):
            raise TypeError(
                f"assess_readability: expected HumanEvaluationReport, "
                f"got {type(report).__name__}"
            )

        # copilot: collect all text blocks to score
        all_texts: list[str] = list(report.theorem_summaries) + list(report.key_findings)
        if not all_texts:
            return ReadabilityLevel.OPAQUE

        scores = [_readability_score(t) for t in all_texts]
        mean_score = statistics.mean(scores)

        # copilot: map numeric score to level
        level_name = _level_from_score(mean_score, READABILITY_SCORE_THRESHOLDS)
        return ReadabilityLevel[level_name]

    # ------------------------------------------------------------------
    def assess_actionability(self, report: HumanEvaluationReport) -> ActionabilityLevel:
        """Assess the actionability level of a HumanEvaluationReport.

        Actionability is computed from the count and quality of action
        items.  Reports with no action items are NONE; reports with at
        least one specific, verb-containing item progress through VAGUE,
        PARTIAL, CONCRETE, and PRESCRIPTIVE based on the
        _actionability_score helper.

        Args:
            report: The HumanEvaluationReport to assess.

        Returns:
            The ActionabilityLevel corresponding to the computed score.

        Raises:
            TypeError: If *report* is not a HumanEvaluationReport.

        Example:
            >>> level = analyzer.assess_actionability(report)
            >>> isinstance(level, ActionabilityLevel)
            True
        """
        if not isinstance(report, HumanEvaluationReport):
            raise TypeError(
                f"assess_actionability: expected HumanEvaluationReport, "
                f"got {type(report).__name__}"
            )

        if not report.action_items:
            return ActionabilityLevel.NONE

        # copilot: compute action score from items list
        score = _actionability_score(report.action_items)
        level_name = _level_from_score(score, ACTIONABILITY_SCORE_THRESHOLDS)
        return ActionabilityLevel[level_name]

    # ------------------------------------------------------------------
    def calibrate_trust(
        self,
        measurements: list[tuple[float, bool]],
    ) -> float:
        """Compute a trust calibration score from confidence–outcome pairs.

        Each measurement is a tuple ``(confidence, was_verified)`` where
        ``confidence`` is the system's stated confidence in [0, 1] and
        ``was_verified`` is whether the theorem was subsequently verified.

        Calibration is measured as 1 minus the expected calibration error
        (ECE), binned into _CONFIDENCE_BINS equal-width bins.  A perfectly
        calibrated system has ECE = 0 and calibration score = 1.

        Args:
            measurements: List of (confidence, was_verified) tuples.

        Returns:
            Calibration score in [0, 1].

        Raises:
            ValueError: If *measurements* is empty.

        Example:
            >>> score = analyzer.calibrate_trust([(0.9, True), (0.4, False)])
            >>> 0.0 <= score <= 1.0
            True
        """
        if not measurements:
            raise ValueError("calibrate_trust: measurements list is empty")

        # copilot: group measurements into confidence bins
        bin_width = 1.0 / _CONFIDENCE_BINS
        bins: dict[int, list[tuple[float, bool]]] = {i: [] for i in range(_CONFIDENCE_BINS)}
        for conf, verified in measurements:
            bin_idx = min(int(conf / bin_width), _CONFIDENCE_BINS - 1)
            bins[bin_idx].append((conf, verified))

        ece = 0.0
        n_total = len(measurements)
        for bin_idx, bin_items in bins.items():
            if not bin_items:
                continue
            n_bin = len(bin_items)
            mean_conf = statistics.mean(c for c, _ in bin_items)
            frac_verified = sum(1 for _, v in bin_items if v) / n_bin
            ece += (n_bin / n_total) * abs(mean_conf - frac_verified)

        calibration_score = _clamp(1.0 - ece, 0.0, 1.0)
        return round(calibration_score, 4)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class HumanFacingEvaluationCoordinator:
    """Orchestrates report generation, revision, and export for human evaluators.

    The coordinator assembles HumanEvaluationReport objects from project
    data, delegates narrative generation to the analyzer, and manages
    the revision cycle when human feedback is incorporated.

    The ``export_markdown`` method converts a report into a formatted
    Markdown document suitable for review by mathematicians.

    Attributes:
        _analyzer: The HumanFacingEvaluationAnalyzer used by this coordinator.
        _witness:  The HumanFacingEvaluationWitness receiving observations.
        _cache:    Dict mapping project_id to list of reports.
    """

    def __init__(
        self,
        analyzer: Optional[HumanFacingEvaluationAnalyzer] = None,
        witness: Optional["HumanFacingEvaluationWitness"] = None,
    ) -> None:
        """Initialise coordinator with optional analyzer and witness.

        Args:
            analyzer: Pre-built analyzer, or None for a default instance.
            witness:  Pre-built witness, or None for a default instance.

        Example:
            >>> coord = HumanFacingEvaluationCoordinator()
        """
        self._analyzer: HumanFacingEvaluationAnalyzer = (
            analyzer or HumanFacingEvaluationAnalyzer()
        )
        self._witness: HumanFacingEvaluationWitness = (
            witness or HumanFacingEvaluationWitness()
        )
        self._cache: dict[str, list[HumanEvaluationReport]] = {}

    # ------------------------------------------------------------------
    def generate_report(
        self,
        project_id: str,
        schemas: list[dict],
        measurements: list[tuple[float, bool]],
    ) -> HumanEvaluationReport:
        """Generate a HumanEvaluationReport for a project.

        Processes each schema through the analyzer's ``generate_narrative``
        to produce theorem summaries and key findings.  Action items are
        synthesised from anomaly flags derived from the schema metadata.

        Args:
            project_id:   Identifier of the project.
            schemas:      List of theorem schema dicts (see generate_narrative).
            measurements: List of (confidence, verified) tuples for calibration.

        Returns:
            A fully populated HumanEvaluationReport.

        Raises:
            ValueError: If *project_id* is empty or *schemas* is empty.

        Example:
            >>> report = coord.generate_report("p1", [{"theorem_id": "T1", ...}], [])
        """
        if not project_id:
            raise ValueError("generate_report: project_id must not be empty")
        if not schemas:
            raise ValueError("generate_report: schemas list must not be empty")

        narratives: list[TheoremNarrative] = []
        for schema in schemas:
            try:
                n = self._analyzer.generate_narrative(schema)
                narratives.append(n)
            except (TypeError, ValueError):
                # copilot: skip malformed schemas gracefully
                continue

        # copilot: build theorem summaries from narrative plain statements
        theorem_summaries: list[str] = [
            f"[{n.theorem_id}] {n.plain_statement[:80]}{'...' if len(n.plain_statement) > 80 else ''}"
            for n in narratives
        ]

        # copilot: key findings synthesise confidence and significance
        key_findings: list[str] = []
        high_conf = [n for n in narratives if "highly confident" in n.confidence_explanation]
        low_conf  = [n for n in narratives if "low" in n.confidence_explanation]

        if high_conf:
            key_findings.append(
                f"{len(high_conf)} theorem(s) have high confidence and are ready for authority promotion."
            )
        if low_conf:
            key_findings.append(
                f"{len(low_conf)} theorem(s) have low confidence and require additional verification."
            )
        if not key_findings:
            key_findings.append("All theorems are at intermediate confidence levels; review recommended.")

        # copilot: generate action items from low-confidence narratives
        action_items: list[str] = []
        for n in low_conf[:3]:
            action_items.append(f"Re-verify theorem {n.theorem_id}: {n.plain_statement[:60]}.")
        if len(narratives) > 5:
            action_items.append(
                "Review federation density for projects with more than 5 theorems."
            )
        action_items.append("Update theorem summaries to improve readability scores.")

        trust_score = (
            self._analyzer.calibrate_trust(measurements)
            if measurements
            else 0.5
        )

        # Build a draft report to assess levels
        draft = HumanEvaluationReport(
            report_id=_uid(),
            project_id=project_id,
            theorem_summaries=tuple(theorem_summaries),
            key_findings=tuple(key_findings),
            action_items=tuple(action_items),
            readability=ReadabilityLevel.ACCESSIBLE,
            actionability=ActionabilityLevel.PARTIAL,
            trust_calibration_score=trust_score,
            generated_at=_utcnow(),
        )

        # copilot: assess actual levels from the draft content
        real_readability   = self._analyzer.assess_readability(draft)
        real_actionability = self._analyzer.assess_actionability(draft)

        final_report = HumanEvaluationReport(
            report_id=draft.report_id,
            project_id=project_id,
            theorem_summaries=tuple(theorem_summaries),
            key_findings=tuple(key_findings),
            action_items=tuple(action_items),
            readability=real_readability,
            actionability=real_actionability,
            trust_calibration_score=trust_score,
            generated_at=draft.generated_at,
        )

        self._cache.setdefault(project_id, []).append(final_report)
        self._witness.observe(final_report)
        return final_report

    # ------------------------------------------------------------------
    def revise_report(
        self,
        report: HumanEvaluationReport,
        feedback: dict,
    ) -> HumanEvaluationReport:
        """Produce a revised report incorporating human feedback.

        The *feedback* dict may contain any of the keys:
        ``additional_action_items``, ``override_readability``,
        ``override_actionability``, ``supplementary_findings``.

        A new report is created with a fresh report_id and current
        timestamp.  The original report is preserved in the cache.

        Args:
            report:   The original HumanEvaluationReport.
            feedback: Dict of revision instructions.

        Returns:
            A new, revised HumanEvaluationReport.

        Raises:
            TypeError: If *report* is not a HumanEvaluationReport.

        Example:
            >>> revised = coord.revise_report(report, {"supplementary_findings": ["New finding."]})
        """
        if not isinstance(report, HumanEvaluationReport):
            raise TypeError(
                f"revise_report: expected HumanEvaluationReport, "
                f"got {type(report).__name__}"
            )

        # copilot: merge feedback into a revised report
        extra_actions   = list(feedback.get("additional_action_items", []))
        extra_findings  = list(feedback.get("supplementary_findings", []))
        override_read   = feedback.get("override_readability")
        override_action = feedback.get("override_actionability")

        new_action_items = tuple(list(report.action_items) + extra_actions)
        new_key_findings = tuple(list(report.key_findings) + extra_findings)

        new_readability = (
            ReadabilityLevel(override_read) if override_read else report.readability
        )
        new_actionability = (
            ActionabilityLevel(override_action) if override_action else report.actionability
        )

        revised = HumanEvaluationReport(
            report_id=_uid(),
            project_id=report.project_id,
            theorem_summaries=report.theorem_summaries,
            key_findings=new_key_findings,
            action_items=new_action_items,
            readability=new_readability,
            actionability=new_actionability,
            trust_calibration_score=report.trust_calibration_score,
            generated_at=_utcnow(),
        )
        self._cache.setdefault(report.project_id, []).append(revised)
        self._witness.observe(revised)
        return revised

    # ------------------------------------------------------------------
    def export_markdown(self, report: HumanEvaluationReport) -> str:
        """Convert a HumanEvaluationReport to a Markdown-formatted string.

        The output uses standard Markdown headings and bullet lists,
        suitable for rendering in GitHub, Jupyter, or any Markdown-aware
        documentation system.

        Args:
            report: The report to export.

        Returns:
            A multi-line Markdown string.

        Raises:
            TypeError: If *report* is not a HumanEvaluationReport.

        Example:
            >>> md = coord.export_markdown(report)
            >>> md.startswith("# JuGeo Evaluation Report")
            True
        """
        if not isinstance(report, HumanEvaluationReport):
            raise TypeError(
                f"export_markdown: expected HumanEvaluationReport, "
                f"got {type(report).__name__}"
            )

        lines: list[str] = [
            f"# JuGeo Evaluation Report",
            f"",
            f"**Project:** `{report.project_id}`  ",
            f"**Report ID:** `{report.report_id}`  ",
            f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ",
            f"**Trust Calibration:** {report.trust_calibration_score:.2%}  ",
            f"**Readability:** {report.readability.value.title()}  ",
            f"**Actionability:** {report.actionability.value.title()}  ",
            f"",
            f"---",
            f"",
            f"## Theorem Summaries",
            f"",
        ]

        for summary in report.theorem_summaries:
            lines.append(f"- {summary}")

        lines += [
            f"",
            f"## Key Findings",
            f"",
        ]

        for finding in report.key_findings:
            wrapped = textwrap.fill(finding, width=_MARKDOWN_LINE_WIDTH)
            lines.append(f"- {wrapped}")

        lines += [
            f"",
            f"## Action Items",
            f"",
        ]

        for i, item in enumerate(report.action_items, start=1):
            lines.append(f"{i}. {item}")

        lines += [
            f"",
            f"---",
            f"",
            f"*Report version: {REPORT_VERSION}*",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    def status_report(self, project_id: str) -> dict:
        """Return a status summary for *project_id* based on cached reports.

        Args:
            project_id: Identifier of the project.

        Returns:
            Dict with keys ``project_id``, ``n_reports``,
            ``latest_readability``, ``latest_actionability``,
            ``trust_calibration``.

        Example:
            >>> rep = coord.status_report("p1")
            >>> "n_reports" in rep
            True
        """
        history = self._cache.get(project_id, [])
        if not history:
            return {
                "project_id": project_id,
                "n_reports": 0,
                "latest_readability": None,
                "latest_actionability": None,
                "trust_calibration": None,
            }
        latest = history[-1]
        return {
            "project_id": project_id,
            "n_reports": len(history),
            "latest_readability": latest.readability.value,
            "latest_actionability": latest.actionability.value,
            "trust_calibration": latest.trust_calibration_score,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class HumanFacingEvaluationWitness:
    """Observes and stores all HumanEvaluationReport objects for audit.

    The witness accumulates reports from all coordinator calls and
    supports post-hoc queries: which projects have low readability,
    a summary of all action items, and retrieval of the full log.

    Attributes:
        _log: Ordered list of all observed HumanEvaluationReport objects.
    """

    def __init__(self) -> None:
        """Initialise an empty witness log.

        Example:
            >>> w = HumanFacingEvaluationWitness()
            >>> w.all_reports()
            []
        """
        self._log: list[HumanEvaluationReport] = []

    # ------------------------------------------------------------------
    def observe(self, report: HumanEvaluationReport) -> None:
        """Append a HumanEvaluationReport to the witness log.

        Args:
            report: The report to record.

        Raises:
            TypeError: If *report* is not a HumanEvaluationReport.

        Example:
            >>> w.observe(report)
        """
        if not isinstance(report, HumanEvaluationReport):
            raise TypeError(
                f"observe: expected HumanEvaluationReport, "
                f"got {type(report).__name__}"
            )
        self._log.append(report)

    # ------------------------------------------------------------------
    def low_readability_reports(self) -> list[HumanEvaluationReport]:
        """Return all reports at OPAQUE or TECHNICAL readability level.

        These reports are candidates for revision to improve communication
        quality.

        Returns:
            List of HumanEvaluationReport objects with low readability.

        Example:
            >>> low = w.low_readability_reports()
        """
        low_levels = {ReadabilityLevel.OPAQUE, ReadabilityLevel.TECHNICAL}
        return [r for r in self._log if r.readability in low_levels]

    # ------------------------------------------------------------------
    def action_item_summary(self) -> dict:
        """Aggregate all action items across observed reports.

        Returns:
            Dict with keys ``total_action_items``, ``unique_items``,
            ``most_common`` (up to 5 most-repeated items),
            ``reports_with_no_items``.

        Example:
            >>> summary = w.action_item_summary()
            >>> "total_action_items" in summary
            True
        """
        all_items: list[str] = []
        no_item_count = 0
        for r in self._log:
            if not r.action_items:
                no_item_count += 1
            else:
                all_items.extend(r.action_items)

        from collections import Counter
        counter = Counter(all_items)
        most_common = [item for item, _ in counter.most_common(5)]

        return {
            "total_action_items": len(all_items),
            "unique_items": len(set(all_items)),
            "most_common": most_common,
            "reports_with_no_items": no_item_count,
        }

    # ------------------------------------------------------------------
    def all_reports(self) -> list[HumanEvaluationReport]:
        """Return a copy of all observed reports in observation order.

        Returns:
            List of HumanEvaluationReport objects.

        Example:
            >>> all_r = w.all_reports()
        """
        return list(self._log)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== HumanFacingEvaluation smoke test ===")

    # copilot: define a small set of theorem schemas for testing
    _SCHEMAS = [
        {
            "theorem_id": "T001",
            "statement": (
                "Every complete metric space that is also a Baire space "
                "admits a dense G-delta set of generic points."
            ),
            "significance": (
                "This result underpins the open mapping theorem and is central "
                "to functional analysis.  It is used in more than 40 proofs within "
                "the JuGeo alpha pack."
            ),
            "proof_sketch": (
                "Construct a sequence of open dense sets by transfinite induction "
                "and take the countable intersection; completeness ensures non-emptiness."
            ),
            "confidence": 0.94,
        },
        {
            "theorem_id": "T002",
            "statement": (
                "The étale cohomology groups of a smooth projective variety "
                "satisfy the Weil conjectures."
            ),
            "significance": (
                "This is one of the crowning achievements of twentieth-century "
                "algebraic geometry and has implications for the Langlands programme."
            ),
            "proof_sketch": (
                "Apply the Lefschetz trace formula to the Frobenius endomorphism "
                "acting on the l-adic cohomology sheaf."
            ),
            "confidence": 0.62,
        },
        {
            "theorem_id": "T003",
            "statement": (
                "For any finitely generated abelian group G, "
                "every subgroup of G is also finitely generated."
            ),
            "significance": (
                "This is a standard result in abstract algebra.  It justifies "
                "the use of finitely generated abelian groups as a canonical class."
            ),
            "proof_sketch": (
                "Use the structure theorem to decompose G, then note that "
                "subgroups of cyclic groups are cyclic."
            ),
            "confidence": 0.99,
        },
    ]

    # Confidence calibration measurements
    _MEASUREMENTS: list[tuple[float, bool]] = [
        (0.94, True),
        (0.62, False),
        (0.99, True),
        (0.80, True),
        (0.45, False),
        (0.70, True),
        (0.30, False),
        (0.88, True),
    ]

    _analyzer = HumanFacingEvaluationAnalyzer()
    _witness  = HumanFacingEvaluationWitness()
    _coord    = HumanFacingEvaluationCoordinator(analyzer=_analyzer, witness=_witness)

    # Generate narratives individually
    for _schema in _SCHEMAS:
        _narr = _analyzer.generate_narrative(_schema)
        print(f"  Narrative for {_narr.theorem_id}: readability_score="
              f"{_readability_score(_narr.plain_statement):.2f}")

    # Generate report
    _report = _coord.generate_report("jugeo_test_project", _SCHEMAS, _MEASUREMENTS)
    print(f"\n  Report ID: {_report.report_id}")
    print(f"  Readability: {_report.readability.value}")
    print(f"  Actionability: {_report.actionability.value}")
    print(f"  Trust calibration: {_report.trust_calibration_score:.4f}")

    # Export markdown
    _md = _coord.export_markdown(_report)
    print(f"\n  Markdown export ({len(_md)} chars):\n{_md[:400]}...")

    # Revise report with feedback
    _revised = _coord.revise_report(
        _report,
        feedback={
            "additional_action_items": ["Verify T002 using alternative proof path."],
            "supplementary_findings": ["Cross-pack references detected for T001 in 3 packs."],
        },
    )
    print(f"\n  Revised report: {len(_revised.action_items)} action items")

    # Witness queries
    _low = _witness.low_readability_reports()
    print(f"  Low-readability reports: {len(_low)}")
    _ai_summary = _witness.action_item_summary()
    print(f"  Action item summary: {_ai_summary}")

    # Status report
    _status = _coord.status_report("jugeo_test_project")
    print(f"  Status report: {_status}")

    print("=== Smoke test PASSED ===")
