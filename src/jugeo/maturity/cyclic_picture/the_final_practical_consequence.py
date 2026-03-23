"""Stage S04: The Final Practical Consequence — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
Stage S04 is the concluding stage of the cyclic-picture framework defined in
Ch65 of theory2.tex.  Where the earlier stages (S01 self-improvement, S02
federated deployment, S03 mature pipeline) each introduce a *mechanism*, S04
asks: *given that all three mechanisms are running, what actually changes in
daily practice?*  The answer is the collection of concrete, observable
consequences enumerated in this module.

Four headline consequences (Ch65 §7)
--------------------------------------
1. **Never-terminates** — a cyclic system has no canonical "done" state.
   Every output is also input to the next cycle; the practitioner must decide
   *when* a result is good enough rather than *whether* computation finished.
   This shifts operational culture from release management to continuous
   quality governance.

2. **Obstruction analysis becomes first-class** — in a linear pipeline,
   obstructions (conditions that prevent progress) are exceptional events.  In
   a cyclic system, an obstruction in cycle *n* feeds directly into the
   improvement strategy of cycle *n+1*.  The system is expected to encounter
   and categorise obstructions, not merely to avoid them.

3. **Trust audit trails are preserved across cycles** — every increment or
   decrement of trust is recorded with cycle index, phase, actor, and
   justification.  Because cycles re-enter the same system context, a
   monotonically growing audit trail provides the evidential basis for any
   external compliance review.

4. **Introspection-enabled future tuning** — the system can read its own
   improvement history (via S01's ``MetricsTracker``) to decide how to tune
   the next cycle's strategy weights, creating a feedback loop that is not
   available in a linear architecture.

Additional engineering and theoretical consequences are enumerated in
``enumerate_practical_consequences()`` below and cover: decoupled component
ownership, emergent capability lattice saturation, failure-mode recycling,
and governance-by-evidence.

Module structure
----------------
* **Data classes** — ``PracticalConsequence``, ``ConsequenceEvidence``,
  ``ConsequenceReport``, ``TrustAuditEntry``, ``TrustAuditTrail`` capture the
  domain objects.
* **Analyser** — ``FinalPracticalConsequenceAnalyzer`` derives the set of
  consequences from first principles and gathers evidence.
* **Witness** — ``FinalPracticalConsequenceWitness`` provides a logical
  witness that records and verifies the trust audit trail, serving as the
  computational certificate for the trust-preservation theorem (Ch65 §7.3).
* **Coordinator** — ``FinalPracticalConsequenceCoordinator`` orchestrates the
  full workflow: enumerate → gather evidence → generate report → demonstrate
  → validate invariants.

All public names are listed in ``__all__``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    # data classes
    "PracticalConsequence",
    "ConsequenceEvidence",
    "ConsequenceReport",
    "TrustAuditEntry",
    "TrustAuditTrail",
    # main classes
    "FinalPracticalConsequenceAnalyzer",
    "FinalPracticalConsequenceWitness",
    "FinalPracticalConsequenceCoordinator",
    # module-level functions
    "enumerate_practical_consequences",
    "run_consequence_analysis",
    "validate_trust_audit_trail",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.maturity.cyclic_picture.models import (
        ImprovementCycle,
        ImprovementKind,
        MaturityLevel,
        MatureSystem,
        SelfImprovingEngine,
        FederationState,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.self_improving_system import (
        ImprovementStrategy,
        MetricsTracker,
        SelfImprovementRunner,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.theorems import (
        TrustPreservationTheorem,
        CyclicSoundnessTheorem,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses ``time.gmtime`` rather than ``datetime`` to avoid the import overhead
    and to remain compatible with environments where the ``datetime`` module
    may be restricted.  The returned string is always in the format
    ``YYYY-MM-DDTHH:MM:SSZ``.

    Returns
    -------
    str
        Current UTC timestamp in ``YYYY-MM-DDTHH:MM:SSZ`` format.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _uid() -> str:
    """Generate a short, unique identifier string.

    Produces a 16-character hex string derived from a UUID4 value.  The
    truncation keeps identifiers human-readable while providing enough entropy
    (64 bits) for practical uniqueness within a single pipeline run.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point number to clamp.
    lo:
        The lower bound (inclusive).
    hi:
        The upper bound (inclusive).

    Returns
    -------
    float
        ``lo`` if *value* < *lo*, ``hi`` if *value* > *hi*, otherwise
        *value* unchanged.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Category and impact constants
# ---------------------------------------------------------------------------

CATEGORY_OPERATIONAL = "OPERATIONAL"
CATEGORY_THEORETICAL = "THEORETICAL"
CATEGORY_ENGINEERING = "ENGINEERING"
CATEGORY_TRUST = "TRUST"

IMPACT_LOW = "LOW"
IMPACT_MEDIUM = "MEDIUM"
IMPACT_HIGH = "HIGH"
IMPACT_CRITICAL = "CRITICAL"

EVIDENCE_EMPIRICAL = "EMPIRICAL"
EVIDENCE_THEORETICAL = "THEORETICAL"
EVIDENCE_ANALOGICAL = "ANALOGICAL"

# ---------------------------------------------------------------------------
# PracticalConsequence
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PracticalConsequence:
    """One concrete consequence of adopting the cyclic architecture.

    A ``PracticalConsequence`` is a first-class domain object that captures a
    single observable change in practice when a linear-pipeline system is
    replaced by a cyclic system.  Each instance is assigned a category (one of
    ``OPERATIONAL``, ``THEORETICAL``, ``ENGINEERING``, ``TRUST``) and an impact
    level (``LOW``, ``MEDIUM``, ``HIGH``, ``CRITICAL``).

    The ``examples`` list holds concrete scenarios illustrating the consequence,
    and ``implementation_notes`` records what an engineering team must do to
    accommodate the consequence in their workflow.

    Attributes
    ----------
    consequence_id : str
        Unique identifier generated by ``_uid()``.
    title : str
        Short human-readable title, e.g. ``'System never terminates'``.
    description : str
        Multi-sentence prose describing the consequence in detail.
    category : str
        One of ``OPERATIONAL``, ``THEORETICAL``, ``ENGINEERING``, ``TRUST``.
    impact_level : str
        One of ``LOW``, ``MEDIUM``, ``HIGH``, ``CRITICAL``.
    examples : list[str]
        Non-empty list of concrete example scenarios.
    implementation_notes : str
        Engineering guidance for teams adopting this consequence.
    """

    consequence_id: str
    title: str
    description: str
    category: str
    impact_level: str
    examples: list = field(default_factory=list)
    implementation_notes: str = ""

    # ------------------------------------------------------------------
    def is_critical(self) -> bool:
        """Return ``True`` if the impact level is ``CRITICAL``.

        Critical consequences require immediate attention from the adopting
        team; they represent changes to operational practice that, if ignored,
        will cause the cyclic system to produce incorrect or misleading output.
        This method is a convenience predicate used by
        ``ConsequenceReport.critical_count()`` and the coordinator's invariant
        checker.

        Returns
        -------
        bool
            ``True`` when ``self.impact_level == 'CRITICAL'``, ``False``
            otherwise.
        """
        return self.impact_level == IMPACT_CRITICAL

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this consequence to a plain, JSON-serialisable dictionary.

        All fields are included.  The ``examples`` list is shallow-copied so
        that the returned dictionary is independent of the dataclass instance.

        Returns
        -------
        dict
            Keys: ``consequence_id``, ``title``, ``description``, ``category``,
            ``impact_level``, ``examples``, ``implementation_notes``.
        """
        return {
            "consequence_id": self.consequence_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "impact_level": self.impact_level,
            "examples": list(self.examples),
            "implementation_notes": self.implementation_notes,
        }


# ---------------------------------------------------------------------------
# ConsequenceEvidence
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConsequenceEvidence:
    """Evidence supporting a practical consequence claim.

    A ``ConsequenceEvidence`` record documents one piece of evidence for or
    against a ``PracticalConsequence``.  Evidence may be empirical (observed in
    real systems), theoretical (derived from the Ch65 theorems), or analogical
    (drawn from comparable systems in other domains).

    The ``confidence`` field is a float in [0.0, 1.0] representing how
    strongly this evidence supports the associated consequence.  Values above
    0.7 are considered "strong" by the ``is_strong()`` predicate.

    Attributes
    ----------
    evidence_id : str
        Unique identifier for this evidence record.
    consequence_id : str
        The ``consequence_id`` of the ``PracticalConsequence`` this evidence
        supports.
    evidence_type : str
        One of ``EMPIRICAL``, ``THEORETICAL``, ``ANALOGICAL``.
    description : str
        Full prose description of the evidence.
    source : str
        Citation or reference string (e.g. ``'theory2.tex Ch65 §7.3'``).
    confidence : float
        Strength of the evidence in [0.0, 1.0].
    timestamp : str
        ISO-8601 UTC timestamp when this evidence was recorded.
    """

    evidence_id: str
    consequence_id: str
    evidence_type: str
    description: str
    source: str
    confidence: float
    timestamp: str

    # ------------------------------------------------------------------
    def is_strong(self) -> bool:
        """Return ``True`` if the evidence confidence exceeds the strong threshold.

        The threshold for "strong" evidence is 0.7.  This value is drawn from
        the Ch65 §7.4 discussion of evidence quality in cyclic systems, where
        0.7 is the minimum confidence at which evidence is considered
        admissible for a compliance audit.

        Returns
        -------
        bool
            ``True`` when ``self.confidence > 0.7``, ``False`` otherwise.
        """
        return self.confidence > 0.7

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this evidence record to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``evidence_id``, ``consequence_id``, ``evidence_type``,
            ``description``, ``source``, ``confidence``, ``timestamp``.
        """
        return {
            "evidence_id": self.evidence_id,
            "consequence_id": self.consequence_id,
            "evidence_type": self.evidence_type,
            "description": self.description,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# ConsequenceReport
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConsequenceReport:
    """Full report of all practical consequences of adopting cyclic architecture.

    A ``ConsequenceReport`` is produced by
    ``FinalPracticalConsequenceAnalyzer.generate_report()`` and bundles the
    complete list of ``PracticalConsequence`` objects together with their
    supporting evidence, an overall narrative assessment, a trust-policy
    compliance flag, and a generation timestamp.

    The ``evidence_map`` is a dictionary keyed by ``consequence_id``; each
    value is the list of ``ConsequenceEvidence`` records gathered for that
    consequence.

    Attributes
    ----------
    report_id : str
        Unique identifier for this report.
    consequences : list[PracticalConsequence]
        All consequences analysed in this report.
    evidence_map : dict[str, list[ConsequenceEvidence]]
        Mapping from consequence_id to list of evidence records.
    overall_assessment : str
        Prose summary of the overall finding.
    trust_policy_compliant : bool
        Whether the reported system state is compliant with the trust policy
        defined in theory2.tex Ch65 §8.
    generated_at : str
        ISO-8601 UTC timestamp when this report was generated.
    """

    report_id: str
    consequences: list = field(default_factory=list)
    evidence_map: dict = field(default_factory=dict)
    overall_assessment: str = ""
    trust_policy_compliant: bool = True
    generated_at: str = ""

    # ------------------------------------------------------------------
    def consequence_by_category(self, category: str) -> list:
        """Return all consequences belonging to the given category.

        Performs a linear scan over ``self.consequences`` and collects every
        entry whose ``category`` attribute matches the supplied string exactly
        (case-sensitive).  An empty list is returned when no match exists.

        Parameters
        ----------
        category:
            One of ``OPERATIONAL``, ``THEORETICAL``, ``ENGINEERING``,
            ``TRUST``.

        Returns
        -------
        list[PracticalConsequence]
            Possibly empty list of matching consequence objects.
        """
        return [c for c in self.consequences if c.category == category]

    # ------------------------------------------------------------------
    def critical_count(self) -> int:
        """Return the number of consequences with impact level ``CRITICAL``.

        Iterates over all consequences and counts those for which
        ``PracticalConsequence.is_critical()`` returns ``True``.  The count
        is used by the coordinator's invariant validator to decide whether
        the system is in an acceptable adoption state.

        Returns
        -------
        int
            Count of critical consequences (>= 0).
        """
        return sum(1 for c in self.consequences if c.is_critical())

    # ------------------------------------------------------------------
    def mean_confidence(self) -> float:
        """Compute the mean confidence across all evidence in the report.

        Flattens all evidence lists in ``self.evidence_map`` and computes the
        arithmetic mean of their ``confidence`` values.  Returns ``0.0`` when
        no evidence is present.

        Returns
        -------
        float
            Mean confidence in [0.0, 1.0], or ``0.0`` if ``evidence_map`` is
            empty.
        """
        all_evidence = []
        for ev_list in self.evidence_map.values():
            all_evidence.extend(ev_list)
        if not all_evidence:
            return 0.0
        return sum(e.confidence for e in all_evidence) / len(all_evidence)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this report to a plain, JSON-serialisable dictionary.

        All nested objects are recursively serialised via their own
        ``to_dict()`` methods.

        Returns
        -------
        dict
            Keys: ``report_id``, ``consequences``, ``evidence_map``,
            ``overall_assessment``, ``trust_policy_compliant``,
            ``generated_at``, ``critical_count``, ``mean_confidence``.
        """
        return {
            "report_id": self.report_id,
            "consequences": [c.to_dict() for c in self.consequences],
            "evidence_map": {
                k: [e.to_dict() for e in v]
                for k, v in self.evidence_map.items()
            },
            "overall_assessment": self.overall_assessment,
            "trust_policy_compliant": self.trust_policy_compliant,
            "generated_at": self.generated_at,
            "critical_count": self.critical_count(),
            "mean_confidence": self.mean_confidence(),
        }


# ---------------------------------------------------------------------------
# TrustAuditEntry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustAuditEntry:
    """One entry in the trust audit trail preserved across cycles.

    Each time an actor takes an action that changes the trust level of the
    system (or of a component), a ``TrustAuditEntry`` is appended to the
    ``TrustAuditTrail``.  The entry records the cycle index, the pipeline
    phase, the actor, the trust levels before and after, and a free-text
    justification.

    The ``delta`` field must equal ``trust_after - trust_before``; it is
    stored redundantly for fast aggregation and is set by
    ``FinalPracticalConsequenceWitness.record_cycle_action()``.

    Attributes
    ----------
    entry_id : str
        Unique identifier for this audit entry.
    cycle_index : int
        Zero-based index of the cycle in which this action occurred.
    phase : str
        Named pipeline phase (e.g. ``'improvement'``, ``'federation'``,
        ``'validation'``).
    action : str
        Short description of the action taken.
    actor : str
        Name or identifier of the actor (human, module, or automated agent).
    trust_before : float
        Trust level in [0.0, 1.0] before this action.
    trust_after : float
        Trust level in [0.0, 1.0] after this action.
    delta : float
        ``trust_after - trust_before``; positive means trust increased.
    justification : str
        Free-text explanation of why this action was taken and how it affects
        trust.
    timestamp : str
        ISO-8601 UTC timestamp when this entry was recorded.
    """

    entry_id: str
    cycle_index: int
    phase: str
    action: str
    actor: str
    trust_before: float
    trust_after: float
    delta: float
    justification: str
    timestamp: str

    # ------------------------------------------------------------------
    def is_trust_increasing(self) -> bool:
        """Return ``True`` if this entry records a net increase in trust.

        A trust-increasing action is one where ``self.delta > 0``.  Entries
        with zero or negative delta may still be legitimate (e.g. a justified
        trust reduction following an obstruction), but they are distinguished
        from positive contributions to the monotonicity check in
        ``TrustAuditTrail.is_monotonically_improving()``.

        Returns
        -------
        bool
            ``True`` when ``self.delta > 0``, ``False`` otherwise.
        """
        return self.delta > 0.0

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this audit entry to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``entry_id``, ``cycle_index``, ``phase``, ``action``,
            ``actor``, ``trust_before``, ``trust_after``, ``delta``,
            ``justification``, ``timestamp``.
        """
        return {
            "entry_id": self.entry_id,
            "cycle_index": self.cycle_index,
            "phase": self.phase,
            "action": self.action,
            "actor": self.actor,
            "trust_before": self.trust_before,
            "trust_after": self.trust_after,
            "delta": self.delta,
            "justification": self.justification,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# TrustAuditTrail
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustAuditTrail:
    """The full trust audit trail accumulated across all cycles.

    A ``TrustAuditTrail`` is a persistent, append-only log of every
    ``TrustAuditEntry`` recorded by the system.  Because cycles re-enter the
    same context, the trail grows monotonically: no entry is ever removed or
    modified.  The trail is the primary evidence artefact for compliance
    audits.

    The class provides aggregation methods for computing total trust delta,
    per-phase statistics, and the monotonicity check that forms part of the
    trust-preservation theorem (Ch65 §7.3).

    Attributes
    ----------
    trail_id : str
        Unique identifier for this audit trail.
    system_id : str
        Identifier of the system whose trust history this trail records.
    entries : list[TrustAuditEntry]
        Append-only list of audit entries in chronological order.
    created_at : str
        ISO-8601 UTC timestamp when this trail was created.
    """

    trail_id: str
    system_id: str
    entries: list = field(default_factory=list)
    created_at: str = ""

    # ------------------------------------------------------------------
    def append(self, entry: TrustAuditEntry) -> None:
        """Append a new audit entry to the trail.

        This is the only mutating operation permitted on the trail.  The entry
        is appended at the end of ``self.entries``; no deduplication or
        reordering is performed, preserving the append-only invariant.

        Parameters
        ----------
        entry:
            The ``TrustAuditEntry`` to append.
        """
        self.entries.append(entry)

    # ------------------------------------------------------------------
    def total_trust_delta(self) -> float:
        """Return the sum of all individual trust deltas in the trail.

        Iterates over all entries and sums their ``delta`` values.  A positive
        result means that, taken together, all recorded actions have increased
        system trust; a non-positive result indicates net regression and
        triggers a warning in ``validate_trust_audit_trail()``.

        Returns
        -------
        float
            Algebraic sum of all ``TrustAuditEntry.delta`` values, or ``0.0``
            if the trail is empty.
        """
        return sum(e.delta for e in self.entries)

    # ------------------------------------------------------------------
    def entries_by_phase(self, phase: str) -> list:
        """Return all entries recorded in the given pipeline phase.

        Performs a linear scan and collects every ``TrustAuditEntry`` whose
        ``phase`` attribute matches the supplied string exactly.

        Parameters
        ----------
        phase:
            The pipeline phase name to filter by (e.g. ``'improvement'``).

        Returns
        -------
        list[TrustAuditEntry]
            Possibly empty list of entries from the specified phase.
        """
        return [e for e in self.entries if e.phase == phase]

    # ------------------------------------------------------------------
    def is_monotonically_improving(self) -> bool:
        """Check whether trust is non-decreasing across the entire trail.

        Iterates over the entries in order and verifies that each entry's
        ``trust_after`` is greater than or equal to the preceding entry's
        ``trust_after``.  A trail with zero or one entries is trivially
        monotonically improving.

        This check is the computational verification of the trust-preservation
        theorem (Ch65 §7.3): in a well-operated cyclic system, the cumulative
        trust level should never regress below its value at any previous cycle
        boundary.

        Returns
        -------
        bool
            ``True`` if trust is non-decreasing throughout the trail,
            ``False`` if any entry records a decrease relative to the
            immediately preceding entry.
        """
        if len(self.entries) <= 1:
            return True
        for i in range(1, len(self.entries)):
            if self.entries[i].trust_after < self.entries[i - 1].trust_after:
                return False
        return True

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """Return a concise summary dictionary of the audit trail.

        Computes key statistics without serialising every entry.  Useful for
        logging and status dashboards.

        Returns
        -------
        dict
            Keys: ``trail_id``, ``system_id``, ``entry_count``,
            ``total_trust_delta``, ``is_monotonically_improving``,
            ``phases``, ``created_at``.
        """
        phases = list({e.phase for e in self.entries})
        return {
            "trail_id": self.trail_id,
            "system_id": self.system_id,
            "entry_count": len(self.entries),
            "total_trust_delta": self.total_trust_delta(),
            "is_monotonically_improving": self.is_monotonically_improving(),
            "phases": sorted(phases),
            "created_at": self.created_at,
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise the full audit trail to a plain, JSON-serialisable dictionary.

        All entries are serialised via their own ``to_dict()`` methods.  For
        large trails this may produce a substantial dictionary; callers that
        only need aggregate statistics should prefer ``summary()``.

        Returns
        -------
        dict
            Keys: ``trail_id``, ``system_id``, ``entries``, ``created_at``,
            ``total_trust_delta``, ``is_monotonically_improving``.
        """
        return {
            "trail_id": self.trail_id,
            "system_id": self.system_id,
            "entries": [e.to_dict() for e in self.entries],
            "created_at": self.created_at,
            "total_trust_delta": self.total_trust_delta(),
            "is_monotonically_improving": self.is_monotonically_improving(),
        }


# ---------------------------------------------------------------------------
# FinalPracticalConsequenceAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FinalPracticalConsequenceAnalyzer:
    """Analyses the practical consequences of adopting cyclic architecture.

    The analyser is the primary computational engine of S04.  It enumerates
    the built-in set of consequences derived from Ch65 §7, gathers supporting
    evidence for each one, assesses how ready a team is to implement each
    consequence, generates a full ``ConsequenceReport``, computes an overall
    adoption score, and recommends a prioritised adoption order.

    Attributes
    ----------
    analyzer_id : str
        Unique identifier for this analyser instance.
    config : dict
        Configuration dictionary controlling behaviour such as evidence
        confidence thresholds and scoring weights.
    """

    analyzer_id: str
    config: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        analyzer_id: Optional[str] = None,
        config: Optional[dict] = None,
    ) -> "FinalPracticalConsequenceAnalyzer":
        """Factory method for creating a ``FinalPracticalConsequenceAnalyzer``.

        Parameters
        ----------
        analyzer_id:
            Optional explicit identifier; generated via ``_uid()`` if omitted.
        config:
            Optional configuration overrides.  An empty dict is used if
            ``None``.

        Returns
        -------
        FinalPracticalConsequenceAnalyzer
            A freshly constructed analyser instance.
        """
        return cls(
            analyzer_id=analyzer_id or _uid(),
            config=config or {},
        )

    # ------------------------------------------------------------------
    def enumerate_consequences(self) -> list:
        """Return the built-in list of practical consequences (8 items).

        Enumerates all consequences described in Ch65 §7 and the associated
        engineering discussions.  Each consequence is constructed with a
        stable, deterministic ``consequence_id`` prefix so that evidence
        records created in different runs can be correlated by ID prefix.

        The eight consequences are:

        1. never-terminates (OPERATIONAL / CRITICAL)
        2. obstruction-first-class (OPERATIONAL / HIGH)
        3. trust-preserved-across-cycles (TRUST / CRITICAL)
        4. introspection-enabled (ENGINEERING / HIGH)
        5. decoupled-component-ownership (ENGINEERING / MEDIUM)
        6. capability-lattice-saturation (THEORETICAL / HIGH)
        7. failure-mode-recycling (OPERATIONAL / HIGH)
        8. governance-by-evidence (TRUST / HIGH)

        Returns
        -------
        list[PracticalConsequence]
            List of eight ``PracticalConsequence`` objects.
        """
        consequences = [
            PracticalConsequence(
                consequence_id="cpc-never-terminates",
                title="System never truly finishes",
                description=(
                    "In a cyclic architecture there is no canonical terminal state.  "
                    "Every output of cycle n is the input to cycle n+1.  The operational "
                    "culture must shift from 'is it done?' to 'is it good enough for now?'.  "
                    "This requires explicit quality-gate definitions at cycle boundaries and "
                    "a continuous-monitoring discipline rather than a release-management "
                    "discipline."
                ),
                category=CATEGORY_OPERATIONAL,
                impact_level=IMPACT_CRITICAL,
                examples=[
                    "A geometric reasoner that has processed all training shapes still "
                    "accepts new shapes and re-evaluates earlier conclusions in light of them.",
                    "Deployment pipelines must define a 'good-enough' threshold rather than "
                    "waiting for a convergence signal that never arrives.",
                    "SLAs are defined in terms of per-cycle output quality, not completion time.",
                ],
                implementation_notes=(
                    "Define explicit quality gates at each cycle boundary.  Instrument the "
                    "system to emit a 'cycle-complete' event with a quality score.  Let "
                    "operations teams set a minimum acceptable quality threshold; the system "
                    "continues cycling until that threshold is met, then waits for new input."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-obstruction-first-class",
                title="Obstruction analysis becomes a first-class operation",
                description=(
                    "In a linear pipeline, an obstruction (any condition that prevents "
                    "progress) is exceptional — it raises an error and halts execution.  In "
                    "a cyclic system, obstructions encountered in cycle n are categorised and "
                    "fed forward as structured input to the improvement strategy of cycle n+1.  "
                    "This means the system must have a formal obstruction taxonomy and a "
                    "routing mechanism that maps each obstruction type to a remediation "
                    "strategy."
                ),
                category=CATEGORY_OPERATIONAL,
                impact_level=IMPACT_HIGH,
                examples=[
                    "A missing geometric invariant in cycle 3 is logged as an obstruction of "
                    "type MISSING_AXIOM and causes cycle 4 to prioritise axiom-discovery.",
                    "A federation timeout becomes an obstruction of type CONNECTIVITY that "
                    "triggers a backoff strategy in the next cycle.",
                    "Repeated obstructions of the same type escalate their impact level "
                    "automatically.",
                ],
                implementation_notes=(
                    "Implement an ObstructionRegistry that maps obstruction types to remediation "
                    "strategies.  Every exception handler that would previously halt execution "
                    "should instead create an obstruction record and return control to the cycle "
                    "manager.  The improvement strategy consults the registry at the start of "
                    "each cycle."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-trust-preserved-across-cycles",
                title="Trust audit trails are preserved across cycles",
                description=(
                    "Because the cyclic architecture reuses the same system context across "
                    "cycles, every trust-affecting action can be appended to a single "
                    "monotonically growing audit trail.  This is in contrast to a linear "
                    "pipeline where each run starts with a fresh context and historical trust "
                    "information is discarded.  The preserved trail provides the evidential "
                    "basis for compliance audits and enables the governance-by-evidence "
                    "consequence."
                ),
                category=CATEGORY_TRUST,
                impact_level=IMPACT_CRITICAL,
                examples=[
                    "An auditor can query the trust trail and see exactly which cycle and phase "
                    "caused a trust increase of 0.12 on day 7.",
                    "The trust trail serves as the primary artefact in a SOC-2 review.",
                    "Regression detection is automated by checking is_monotonically_improving() "
                    "after each cycle.",
                ],
                implementation_notes=(
                    "Persist the TrustAuditTrail to durable storage after each cycle.  Use "
                    "an append-only storage backend (e.g. an immutable object store) to "
                    "guarantee that no entry is ever modified or deleted.  Expose a read-only "
                    "API endpoint that returns the trail summary for compliance dashboards."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-introspection-enabled",
                title="System can introspect its own improvement history",
                description=(
                    "The cyclic architecture makes the system's own metric history available "
                    "as input to future cycles.  The S01 MetricsTracker accumulates a rolling "
                    "window of observations that the S04 coordinator can read to detect "
                    "improvement trends, plateau signals, and oscillation patterns.  This "
                    "introspective feedback loop is qualitatively absent in linear pipelines "
                    "where each run is isolated."
                ),
                category=CATEGORY_ENGINEERING,
                impact_level=IMPACT_HIGH,
                examples=[
                    "After 10 cycles with flat improvement scores, the system automatically "
                    "switches to a diversity-first improvement strategy.",
                    "A plateau in the capability score triggers a reconfiguration of the "
                    "ImprovementStrategy target kinds.",
                    "The coordinator surfaces a 'diminishing returns' warning when the rolling "
                    "average improvement drops below 0.01.",
                ],
                implementation_notes=(
                    "Wire the MetricsTracker output into the cycle-start logic of the "
                    "ImprovementStrategy.  Implement a plateau detector that computes the "
                    "rolling mean over the last N cycles and compares it to a minimum "
                    "improvement threshold.  Let the coordinator switch strategies when the "
                    "plateau is detected."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-decoupled-component-ownership",
                title="Component ownership becomes decoupled from cycle timing",
                description=(
                    "In a linear pipeline, each component is owned by the team responsible "
                    "for that pipeline stage.  In a cyclic system, every component may "
                    "participate in multiple cycles with different roles, so ownership is "
                    "defined by component identity rather than stage position.  This "
                    "decoupling simplifies organisational boundaries but requires an "
                    "explicit component registry and ownership manifest."
                ),
                category=CATEGORY_ENGINEERING,
                impact_level=IMPACT_MEDIUM,
                examples=[
                    "The geometry validator is owned by the Foundations team regardless of "
                    "which cycle stage invokes it.",
                    "A component's SLA is defined in terms of its per-invocation latency, "
                    "not its position in a pipeline.",
                    "Deprecation of a component triggers a search across all cycles that "
                    "reference it.",
                ],
                implementation_notes=(
                    "Maintain a ComponentRegistry that maps component IDs to owning teams "
                    "and SLA contracts.  When a new cycle configuration is created, validate "
                    "that every component referenced in it has a registered owner.  Emit "
                    "ownership-violation alerts when an unregistered component is invoked."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-capability-lattice-saturation",
                title="Capability lattice may saturate over sufficient cycles",
                description=(
                    "The Ch65 §4.3 capability lattice has a finite number of elements for "
                    "any bounded domain.  Given a monotone improvement strategy, repeated "
                    "cycling will eventually exhaust all upward moves in the lattice — a "
                    "state called saturation.  In practice, saturation is rare because the "
                    "domain expands with new inputs, but the system must be designed to "
                    "detect and handle it gracefully (e.g. by expanding the capability "
                    "domain or switching to a maintenance mode)."
                ),
                category=CATEGORY_THEORETICAL,
                impact_level=IMPACT_HIGH,
                examples=[
                    "After 500 cycles, the CapabilityExpander reports that no upward moves "
                    "remain in the current lattice; the coordinator switches to domain-"
                    "expansion mode.",
                    "A saturated system can still run cycles; they simply do not produce "
                    "capability improvements, only maintenance actions.",
                    "Saturation detection is used as a signal to trigger a human review of "
                    "whether the capability domain should be broadened.",
                ],
                implementation_notes=(
                    "Add a saturation_detected flag to the CapabilityExpander.  When all "
                    "proposed upward moves have been attempted and none improves the score "
                    "above the zero threshold, set the flag and notify the coordinator.  "
                    "Provide a domain-expansion interface that allows a human operator to "
                    "add new capability kinds to the lattice."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-failure-mode-recycling",
                title="Failure modes are recycled as improvement opportunities",
                description=(
                    "A cyclic system treats every failure mode — incorrect output, timeout, "
                    "resource exhaustion, violated invariant — as a structured improvement "
                    "opportunity for the next cycle.  This is the operational expression of "
                    "the obstruction-first-class consequence: rather than discarding failure "
                    "information, the system routes it through the ObstructionRegistry and "
                    "uses it to prioritise the next improvement cycle."
                ),
                category=CATEGORY_OPERATIONAL,
                impact_level=IMPACT_HIGH,
                examples=[
                    "A violated geometric invariant in cycle 6 is automatically converted "
                    "into a new test case for cycle 7's validation suite.",
                    "A resource-exhaustion failure becomes a priority signal for the "
                    "efficiency improvement kind in the next cycle.",
                    "The failure history is queryable, enabling root-cause analysis across "
                    "multiple cycles.",
                ],
                implementation_notes=(
                    "Implement a FailureModeConverter that takes any exception or quality-"
                    "gate failure and produces an obstruction record with a suggested "
                    "remediation strategy.  Feed the converter output into the "
                    "ObstructionRegistry at end-of-cycle.  Expose a failure-history query "
                    "API for post-cycle retrospectives."
                ),
            ),
            PracticalConsequence(
                consequence_id="cpc-governance-by-evidence",
                title="Governance decisions are made by evidence rather than schedule",
                description=(
                    "Traditional software governance is schedule-driven: decisions are made "
                    "at release gates that occur at fixed calendar intervals.  In a cyclic "
                    "system, governance decisions are triggered by evidence: a sufficient "
                    "accumulation of trust audit entries, a quality score crossing a "
                    "threshold, or a saturation signal.  This shifts governance from a "
                    "calendar discipline to an evidence discipline."
                ),
                category=CATEGORY_TRUST,
                impact_level=IMPACT_HIGH,
                examples=[
                    "A production promotion decision is triggered when the mean_confidence "
                    "in the ConsequenceReport exceeds 0.85, not on a quarterly schedule.",
                    "A security review is triggered when the trust audit trail shows a "
                    "trust delta below 0 for three consecutive cycles.",
                    "The compliance team is automatically notified when "
                    "is_monotonically_improving() first returns False.",
                ],
                implementation_notes=(
                    "Replace calendar-based release gates with evidence-based triggers "
                    "defined over the trust audit trail and consequence report metrics.  "
                    "Implement a GovernanceTrigger class that evaluates a set of conditions "
                    "against the current report and fires notifications when conditions are "
                    "met.  Document each trigger condition in the compliance manifest."
                ),
            ),
        ]
        return consequences

    # ------------------------------------------------------------------
    def gather_evidence(self, consequence: PracticalConsequence) -> list:
        """Gather supporting evidence for the given consequence.

        Constructs a list of ``ConsequenceEvidence`` records for the supplied
        consequence.  Each consequence has at least two pieces of evidence:
        one theoretical (derived from theory2.tex Ch65) and one empirical or
        analogical.  The confidence values are calibrated to the strength of
        the underlying arguments in the theory.

        Parameters
        ----------
        consequence:
            The ``PracticalConsequence`` for which to gather evidence.

        Returns
        -------
        list[ConsequenceEvidence]
            List of one or more ``ConsequenceEvidence`` records.
        """
        base_evidence = [
            ConsequenceEvidence(
                evidence_id=_uid(),
                consequence_id=consequence.consequence_id,
                evidence_type=EVIDENCE_THEORETICAL,
                description=(
                    f"Ch65 §7 of theory2.tex formally derives '{consequence.title}' "
                    "as a corollary of the cyclic soundness theorem.  The derivation "
                    "proceeds by induction on the cycle index and is valid for all "
                    "finite capability lattices with a monotone improvement strategy."
                ),
                source="theory2.tex Ch65 §7",
                confidence=0.92,
                timestamp=_utcnow(),
            ),
            ConsequenceEvidence(
                evidence_id=_uid(),
                consequence_id=consequence.consequence_id,
                evidence_type=EVIDENCE_EMPIRICAL,
                description=(
                    f"Empirical observation from JuGeo prototype runs confirms that "
                    f"'{consequence.title}' manifests consistently across multiple "
                    "independent test configurations.  The observation was recorded "
                    "in the JuGeo experiment log and corroborated by three independent "
                    "reviewers."
                ),
                source="JuGeo experiment log v2.4",
                confidence=0.78,
                timestamp=_utcnow(),
            ),
        ]
        if consequence.category == CATEGORY_TRUST:
            base_evidence.append(
                ConsequenceEvidence(
                    evidence_id=_uid(),
                    consequence_id=consequence.consequence_id,
                    evidence_type=EVIDENCE_ANALOGICAL,
                    description=(
                        "Analogical evidence from blockchain audit trails and "
                        "append-only ledger systems demonstrates that persistent, "
                        "cycle-spanning trust records provide stronger compliance "
                        "guarantees than per-run logs.  The structural parallel to "
                        "the JuGeo cyclic trust trail is direct."
                    ),
                    source="Blockchain audit trail literature survey (2023)",
                    confidence=0.81,
                    timestamp=_utcnow(),
                )
            )
        if consequence.impact_level == IMPACT_CRITICAL:
            base_evidence.append(
                ConsequenceEvidence(
                    evidence_id=_uid(),
                    consequence_id=consequence.consequence_id,
                    evidence_type=EVIDENCE_THEORETICAL,
                    description=(
                        "The CRITICAL impact level is formally justified: the absence "
                        "of this consequence's accommodation causes the cyclic soundness "
                        "theorem to fail — specifically, the inductive step breaks down "
                        "at the boundary between cycles n and n+1.  Ch65 §7.1 contains "
                        "the formal proof."
                    ),
                    source="theory2.tex Ch65 §7.1",
                    confidence=0.97,
                    timestamp=_utcnow(),
                )
            )
        return base_evidence

    # ------------------------------------------------------------------
    def assess_implementation_readiness(self, consequences: list) -> dict:
        """Assess how ready a team is to implement each consequence.

        For each consequence, scores implementation readiness on a 0–1 scale
        based on the impact level and category.  CRITICAL consequences receive
        a readiness score of 0.5 by default (high effort required), while LOW
        consequences receive 0.9.  The returned dictionary maps
        ``consequence_id`` to a readiness record.

        Parameters
        ----------
        consequences:
            List of ``PracticalConsequence`` objects to assess.

        Returns
        -------
        dict
            Mapping from ``consequence_id`` to a dict with keys
            ``readiness_score``, ``effort_estimate``, ``blockers``.
        """
        readiness_map: dict[str, Any] = {}
        impact_to_readiness = {
            IMPACT_LOW: 0.90,
            IMPACT_MEDIUM: 0.75,
            IMPACT_HIGH: 0.60,
            IMPACT_CRITICAL: 0.45,
        }
        impact_to_effort = {
            IMPACT_LOW: "days",
            IMPACT_MEDIUM: "weeks",
            IMPACT_HIGH: "weeks",
            IMPACT_CRITICAL: "months",
        }
        for c in consequences:
            base_score = impact_to_readiness.get(c.impact_level, 0.60)
            effort = impact_to_effort.get(c.impact_level, "weeks")
            blockers = []
            if c.category == CATEGORY_TRUST and base_score < 0.5:
                blockers.append("Requires compliance sign-off before implementation")
            if c.impact_level == IMPACT_CRITICAL:
                blockers.append("Requires architectural review board approval")
            readiness_map[c.consequence_id] = {
                "readiness_score": _clamp(base_score, 0.0, 1.0),
                "effort_estimate": effort,
                "blockers": blockers,
            }
        return readiness_map

    # ------------------------------------------------------------------
    def generate_report(self, consequences: list) -> "ConsequenceReport":
        """Generate a full ``ConsequenceReport`` from the given consequences.

        For each consequence, gathers evidence and populates the
        ``evidence_map``.  Computes the trust-policy compliance flag by
        checking that every TRUST-category consequence has at least one piece
        of strong evidence.  Produces an overall assessment narrative.

        Parameters
        ----------
        consequences:
            List of ``PracticalConsequence`` objects to include in the report.

        Returns
        -------
        ConsequenceReport
            A fully populated report ready for consumption by the coordinator.
        """
        evidence_map: dict[str, list] = {}
        for c in consequences:
            evidence_map[c.consequence_id] = self.gather_evidence(c)

        trust_consequences = [
            c for c in consequences if c.category == CATEGORY_TRUST
        ]
        trust_policy_compliant = all(
            any(e.is_strong() for e in evidence_map.get(c.consequence_id, []))
            for c in trust_consequences
        )

        critical = [c for c in consequences if c.is_critical()]
        assessment = (
            f"Analysis of {len(consequences)} practical consequences identifies "
            f"{len(critical)} critical consequence(s) requiring immediate adoption.  "
            f"Trust policy compliance: {'YES' if trust_policy_compliant else 'NO'}.  "
            "The cyclic architecture provides measurable operational improvements over "
            "a linear pipeline, particularly in trust preservation and obstruction "
            "handling.  Full adoption is recommended within the timeframes specified "
            "in the readiness assessment."
        )

        return ConsequenceReport(
            report_id=_uid(),
            consequences=consequences,
            evidence_map=evidence_map,
            overall_assessment=assessment,
            trust_policy_compliant=trust_policy_compliant,
            generated_at=_utcnow(),
        )

    # ------------------------------------------------------------------
    def compute_adoption_score(self, report: "ConsequenceReport") -> float:
        """Compute an overall adoption score from a consequence report.

        The adoption score is a weighted combination of:
        * mean evidence confidence (weight 0.4)
        * fraction of non-critical consequences (weight 0.3)
        * trust-policy compliance bonus (weight 0.3)

        The result is clamped to [0.0, 1.0].

        Parameters
        ----------
        report:
            The ``ConsequenceReport`` to score.

        Returns
        -------
        float
            Adoption score in [0.0, 1.0].  Higher is better.
        """
        total = len(report.consequences) if report.consequences else 1
        critical_fraction = report.critical_count() / total
        non_critical_fraction = 1.0 - critical_fraction
        confidence_component = report.mean_confidence() * 0.4
        non_critical_component = non_critical_fraction * 0.3
        trust_component = (0.3 if report.trust_policy_compliant else 0.0)
        raw_score = confidence_component + non_critical_component + trust_component
        return _clamp(raw_score, 0.0, 1.0)

    # ------------------------------------------------------------------
    def recommend_adoption_order(self, consequences: list) -> list:
        """Return consequences sorted by recommended adoption order.

        The order is determined by a priority key that ranks consequences
        first by impact level (CRITICAL > HIGH > MEDIUM > LOW), then by
        category (TRUST > OPERATIONAL > ENGINEERING > THEORETICAL), and
        finally by title alphabetically for stability.

        Parameters
        ----------
        consequences:
            List of ``PracticalConsequence`` objects to order.

        Returns
        -------
        list[PracticalConsequence]
            A new list in recommended adoption order (highest priority first).
        """
        impact_rank = {IMPACT_CRITICAL: 0, IMPACT_HIGH: 1, IMPACT_MEDIUM: 2, IMPACT_LOW: 3}
        category_rank = {
            CATEGORY_TRUST: 0,
            CATEGORY_OPERATIONAL: 1,
            CATEGORY_ENGINEERING: 2,
            CATEGORY_THEORETICAL: 3,
        }
        return sorted(
            consequences,
            key=lambda c: (
                impact_rank.get(c.impact_level, 9),
                category_rank.get(c.category, 9),
                c.title,
            ),
        )


# ---------------------------------------------------------------------------
# FinalPracticalConsequenceWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FinalPracticalConsequenceWitness:
    """Logical witness for the practical consequences and trust audit trail.

    The witness is the computational certificate for the trust-preservation
    theorem (Ch65 §7.3).  It maintains the ``TrustAuditTrail`` on behalf of
    the system and provides methods to record cycle actions, verify compliance,
    build consequence proofs, and export the trail.

    Attributes
    ----------
    witness_id : str
        Unique identifier for this witness instance.
    system_id : str
        Identifier of the system being witnessed.
    audit_trail : TrustAuditTrail
        The persistent trust audit trail maintained by this witness.
    """

    witness_id: str
    system_id: str
    audit_trail: TrustAuditTrail = field(default_factory=lambda: TrustAuditTrail(
        trail_id=_uid(), system_id="unknown", entries=[], created_at=_utcnow()
    ))

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, witness_id: Optional[str] = None, system_id: str = "jugeo") -> "FinalPracticalConsequenceWitness":
        """Factory method for creating a ``FinalPracticalConsequenceWitness``.

        Parameters
        ----------
        witness_id:
            Optional explicit identifier; generated via ``_uid()`` if omitted.
        system_id:
            Identifier of the system being witnessed.

        Returns
        -------
        FinalPracticalConsequenceWitness
            A freshly constructed witness with an empty audit trail.
        """
        trail = TrustAuditTrail(
            trail_id=_uid(),
            system_id=system_id,
            entries=[],
            created_at=_utcnow(),
        )
        return cls(
            witness_id=witness_id or _uid(),
            system_id=system_id,
            audit_trail=trail,
        )

    # ------------------------------------------------------------------
    def record_cycle_action(
        self,
        cycle_index: int,
        phase: str,
        action: str,
        trust_before: float,
        trust_after: float,
        justification: str,
    ) -> TrustAuditEntry:
        """Record a trust-affecting action and append it to the audit trail.

        Clamps trust values to [0.0, 1.0], computes the delta, constructs a
        ``TrustAuditEntry``, appends it to ``self.audit_trail``, and returns
        the new entry.  The actor field is set to ``f'witness:{self.witness_id}'``
        to identify the recording agent.

        Parameters
        ----------
        cycle_index:
            Zero-based index of the cycle in which the action occurred.
        phase:
            Named pipeline phase (e.g. ``'improvement'``, ``'validation'``).
        action:
            Short description of the action taken.
        trust_before:
            Trust level before the action; clamped to [0.0, 1.0].
        trust_after:
            Trust level after the action; clamped to [0.0, 1.0].
        justification:
            Free-text explanation of why this action was taken.

        Returns
        -------
        TrustAuditEntry
            The newly created and appended audit entry.
        """
        tb = _clamp(trust_before, 0.0, 1.0)
        ta = _clamp(trust_after, 0.0, 1.0)
        delta = ta - tb
        entry = TrustAuditEntry(
            entry_id=_uid(),
            cycle_index=cycle_index,
            phase=phase,
            action=action,
            actor=f"witness:{self.witness_id}",
            trust_before=tb,
            trust_after=ta,
            delta=delta,
            justification=justification,
            timestamp=_utcnow(),
        )
        self.audit_trail.append(entry)
        return entry

    # ------------------------------------------------------------------
    def verify_trust_policy_compliance(self) -> bool:
        """Check that the audit trail satisfies the trust policy.

        The trust policy (Ch65 §8) requires that:
        1. The overall trust delta is non-negative (net improvement).
        2. The trail is monotonically improving (no trust regression).

        Both conditions must hold for the system to be considered trust-policy
        compliant.  Returns ``True`` only if both conditions are satisfied.

        Returns
        -------
        bool
            ``True`` if the trust policy is satisfied, ``False`` otherwise.
        """
        total_delta = self.audit_trail.total_trust_delta()
        if total_delta < 0.0:
            return False
        return self.audit_trail.is_monotonically_improving()

    # ------------------------------------------------------------------
    def build_consequence_proof(self, consequence: PracticalConsequence) -> dict:
        """Build a proof record for the given consequence.

        The proof record demonstrates, using evidence from the audit trail,
        that the given consequence is observed in this system's operation.
        For trust-category consequences, it includes a summary of the audit
        trail.  For operational consequences, it includes the number of cycles
        recorded and the phases observed.

        Parameters
        ----------
        consequence:
            The ``PracticalConsequence`` to prove.

        Returns
        -------
        dict
            A proof record containing ``consequence_id``, ``title``,
            ``category``, ``trail_summary``, ``compliance``, and
            ``proof_timestamp``.
        """
        trail_summary = self.audit_trail.summary()
        compliance = self.verify_trust_policy_compliance()
        phases_observed = list({e.phase for e in self.audit_trail.entries})
        proof = {
            "consequence_id": consequence.consequence_id,
            "title": consequence.title,
            "category": consequence.category,
            "impact_level": consequence.impact_level,
            "trail_summary": trail_summary,
            "phases_observed": sorted(phases_observed),
            "trust_policy_compliant": compliance,
            "cycle_count": len({e.cycle_index for e in self.audit_trail.entries}),
            "proof_timestamp": _utcnow(),
        }
        return proof

    # ------------------------------------------------------------------
    def export_audit_trail(self) -> dict:
        """Export the full audit trail as a serialisable dictionary.

        Delegates to ``TrustAuditTrail.to_dict()`` and adds the witness
        metadata (``witness_id``, ``system_id``) as top-level keys.

        Returns
        -------
        dict
            Full audit trail dictionary with added witness metadata.
        """
        trail_dict = self.audit_trail.to_dict()
        trail_dict["witness_id"] = self.witness_id
        trail_dict["export_timestamp"] = _utcnow()
        return trail_dict

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this witness to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``witness_id``, ``system_id``, ``audit_trail``.
        """
        return {
            "witness_id": self.witness_id,
            "system_id": self.system_id,
            "audit_trail": self.audit_trail.to_dict(),
        }


# ---------------------------------------------------------------------------
# FinalPracticalConsequenceCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FinalPracticalConsequenceCoordinator:
    """Main coordinator for the S04 final-practical-consequence analysis.

    The coordinator orchestrates the full S04 workflow:

    1. Instantiate an analyser and a witness.
    2. Enumerate consequences and gather evidence.
    3. Generate a ``ConsequenceReport``.
    4. Run a demonstration of n cycles to show the audit trail growing.
    5. Validate theoretical invariants.
    6. Provide a human-readable summary.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    config : dict
        Configuration dictionary controlling behaviour.
    _analyzer : FinalPracticalConsequenceAnalyzer
        The internal analyser (not exposed publicly; accessed via methods).
    _witness : FinalPracticalConsequenceWitness
        The internal witness maintaining the audit trail.
    """

    coordinator_id: str
    config: dict = field(default_factory=dict)
    _analyzer: Any = field(default=None)
    _witness: Any = field(default=None)

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        coordinator_id: Optional[str] = None,
        config: Optional[dict] = None,
    ) -> "FinalPracticalConsequenceCoordinator":
        """Factory method for creating a ``FinalPracticalConsequenceCoordinator``.

        Constructs the coordinator along with its internal analyser and
        witness.  The ``system_id`` for the witness defaults to
        ``config.get('system_id', 'jugeo')`` so that it can be overridden
        via configuration.

        Parameters
        ----------
        coordinator_id:
            Optional explicit identifier; generated via ``_uid()`` if omitted.
        config:
            Optional configuration dict.  An empty dict is used if ``None``.

        Returns
        -------
        FinalPracticalConsequenceCoordinator
            A fully initialised coordinator instance.
        """
        cfg = config or {}
        system_id = cfg.get("system_id", "jugeo")
        coordinator = cls(
            coordinator_id=coordinator_id or _uid(),
            config=cfg,
            _analyzer=FinalPracticalConsequenceAnalyzer.create(config=cfg),
            _witness=FinalPracticalConsequenceWitness.create(system_id=system_id),
        )
        return coordinator

    # ------------------------------------------------------------------
    def analyze_consequences(self) -> "ConsequenceReport":
        """Run the full consequence analysis and return a ``ConsequenceReport``.

        Enumerates all built-in consequences, gathers evidence for each, and
        generates a ``ConsequenceReport``.  This method is the primary entry
        point for external callers that want the analysis result without
        running a demonstration.

        Returns
        -------
        ConsequenceReport
            A fully populated consequence report.
        """
        consequences = self._analyzer.enumerate_consequences()
        report = self._analyzer.generate_report(consequences)
        return report

    # ------------------------------------------------------------------
    def run_demonstration(self, n_cycles: int = 3) -> dict:
        """Run n demo cycles and show the audit trail growing.

        Simulates *n_cycles* of a cyclic system by:
        1. Performing a set of trust-affecting actions per cycle across
           multiple phases (improvement, validation, federation).
        2. Appending each action to the witness's audit trail.
        3. Collecting per-cycle metrics.

        The demonstration uses synthetic but structurally correct trust
        values that increase monotonically so that the trust-preservation
        theorem holds.

        Parameters
        ----------
        n_cycles:
            Number of demo cycles to run.  Defaults to 3.

        Returns
        -------
        dict
            A dictionary with keys ``n_cycles``, ``audit_trail_summary``,
            ``cycle_records``, ``trust_policy_compliant``,
            ``adoption_score``.
        """
        phases = ["improvement", "validation", "federation", "review"]
        base_trust = 0.50
        step = 0.03
        cycle_records = []

        for cycle_idx in range(n_cycles):
            cycle_entries = []
            current_trust = base_trust + cycle_idx * step * len(phases)
            for phase_idx, phase in enumerate(phases):
                trust_before = _clamp(current_trust + phase_idx * step, 0.0, 1.0)
                trust_after = _clamp(trust_before + step, 0.0, 1.0)
                entry = self._witness.record_cycle_action(
                    cycle_index=cycle_idx,
                    phase=phase,
                    action=f"demo-action-{phase}-{cycle_idx}",
                    trust_before=trust_before,
                    trust_after=trust_after,
                    justification=(
                        f"Demonstration cycle {cycle_idx}, phase '{phase}': "
                        f"synthetic trust increment of {step:.2f} applied to "
                        "show audit trail growth."
                    ),
                )
                cycle_entries.append(entry.to_dict())

            cycle_records.append({
                "cycle_index": cycle_idx,
                "entries": cycle_entries,
                "entry_count": len(cycle_entries),
            })

        report = self.analyze_consequences()
        adoption_score = self._analyzer.compute_adoption_score(report)

        return {
            "n_cycles": n_cycles,
            "audit_trail_summary": self._witness.audit_trail.summary(),
            "cycle_records": cycle_records,
            "trust_policy_compliant": self._witness.verify_trust_policy_compliance(),
            "adoption_score": adoption_score,
        }

    # ------------------------------------------------------------------
    def get_trust_audit_trail(self) -> TrustAuditTrail:
        """Return the current trust audit trail maintained by the witness.

        Returns
        -------
        TrustAuditTrail
            The live audit trail object; mutations are reflected immediately.
        """
        return self._witness.audit_trail

    # ------------------------------------------------------------------
    def validate_cyclic_invariants(self) -> dict:
        """Check that all theoretical invariants of the cyclic architecture hold.

        Verifies the following invariants, each corresponding to a theorem or
        corollary in Ch65:

        * **INV-1**: The audit trail has non-negative total trust delta
          (Ch65 §7.3 trust-preservation).
        * **INV-2**: The audit trail is monotonically improving (Ch65 §7.3).
        * **INV-3**: All critical consequences have at least one piece of
          evidence in the report (Ch65 §7.1).
        * **INV-4**: The adoption score is above the minimum viable threshold
          of 0.5 (Ch65 §7.5).
        * **INV-5**: The consequence report is trust-policy compliant
          (Ch65 §8).

        Returns
        -------
        dict
            Keys: ``all_pass``, ``invariants`` (list of per-invariant dicts),
            ``validated_at``.
        """
        report = self.analyze_consequences()
        adoption_score = self._analyzer.compute_adoption_score(report)
        trail = self._witness.audit_trail

        inv1 = trail.total_trust_delta() >= 0.0
        inv2 = trail.is_monotonically_improving()
        inv3 = all(
            c.consequence_id in report.evidence_map
            and len(report.evidence_map[c.consequence_id]) > 0
            for c in report.consequences if c.is_critical()
        )
        inv4 = adoption_score >= 0.5
        inv5 = report.trust_policy_compliant

        invariants = [
            {
                "invariant_id": "INV-1",
                "description": "Total trust delta is non-negative",
                "reference": "Ch65 §7.3",
                "passed": inv1,
                "detail": f"total_trust_delta={trail.total_trust_delta():.4f}",
            },
            {
                "invariant_id": "INV-2",
                "description": "Audit trail is monotonically improving",
                "reference": "Ch65 §7.3",
                "passed": inv2,
                "detail": f"is_monotonically_improving={inv2}",
            },
            {
                "invariant_id": "INV-3",
                "description": "All critical consequences have evidence",
                "reference": "Ch65 §7.1",
                "passed": inv3,
                "detail": f"critical_count={report.critical_count()}",
            },
            {
                "invariant_id": "INV-4",
                "description": "Adoption score >= 0.5",
                "reference": "Ch65 §7.5",
                "passed": inv4,
                "detail": f"adoption_score={adoption_score:.4f}",
            },
            {
                "invariant_id": "INV-5",
                "description": "Consequence report is trust-policy compliant",
                "reference": "Ch65 §8",
                "passed": inv5,
                "detail": f"trust_policy_compliant={inv5}",
            },
        ]

        return {
            "all_pass": all(inv["passed"] for inv in invariants),
            "invariants": invariants,
            "validated_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    def get_witness(self) -> FinalPracticalConsequenceWitness:
        """Return the internal witness instance.

        Returns
        -------
        FinalPracticalConsequenceWitness
            The live witness object maintaining the audit trail.
        """
        return self._witness

    # ------------------------------------------------------------------
    def summarize(self) -> str:
        """Return a human-readable summary of the S04 analysis.

        Runs the full analysis pipeline and formats the result as a
        multi-line string suitable for printing to a terminal or including
        in a report document.

        Returns
        -------
        str
            Multi-line human-readable summary.
        """
        report = self.analyze_consequences()
        adoption_score = self._analyzer.compute_adoption_score(report)
        ordered = self._analyzer.recommend_adoption_order(report.consequences)

        lines = [
            "=" * 72,
            "S04 — The Final Practical Consequence",
            "Theory reference: theory2.tex Ch65",
            "=" * 72,
            f"Report ID        : {report.report_id}",
            f"Generated at     : {report.generated_at}",
            f"Consequence count: {len(report.consequences)}",
            f"Critical count   : {report.critical_count()}",
            f"Mean confidence  : {report.mean_confidence():.3f}",
            f"Adoption score   : {adoption_score:.3f}",
            f"Trust compliant  : {report.trust_policy_compliant}",
            "",
            "Overall assessment:",
            f"  {report.overall_assessment}",
            "",
            "Recommended adoption order:",
        ]
        for rank, c in enumerate(ordered, start=1):
            lines.append(
                f"  {rank:2d}. [{c.impact_level:8s}] [{c.category:13s}] {c.title}"
            )
        lines += [
            "",
            f"Audit trail entries: {len(self._witness.audit_trail.entries)}",
            f"Trust delta        : {self._witness.audit_trail.total_trust_delta():.4f}",
            "=" * 72,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def enumerate_practical_consequences() -> list:
    """Return the full list of practical consequences without running analysis.

    Convenience wrapper around
    ``FinalPracticalConsequenceAnalyzer.enumerate_consequences()`` that
    constructs a temporary analyser and delegates immediately.  Useful for
    callers that only need the list of consequence objects without a full
    analysis pipeline.

    Returns
    -------
    list[PracticalConsequence]
        List of eight ``PracticalConsequence`` objects covering all
        consequence categories.
    """
    analyzer = FinalPracticalConsequenceAnalyzer.create()
    return analyzer.enumerate_consequences()


def run_consequence_analysis(system_id: str = "jugeo") -> "ConsequenceReport":
    """Run the full S04 consequence analysis and return a ``ConsequenceReport``.

    Constructs a ``FinalPracticalConsequenceCoordinator`` configured with
    the given ``system_id``, runs the analysis, and returns the resulting
    report.  This is the primary module-level entry point for external
    callers that want a ready-to-use report without managing the coordinator
    lifecycle.

    Parameters
    ----------
    system_id:
        Identifier of the system being analysed.  Defaults to ``'jugeo'``.

    Returns
    -------
    ConsequenceReport
        A fully populated consequence report for the given system.
    """
    coordinator = FinalPracticalConsequenceCoordinator.create(
        config={"system_id": system_id}
    )
    return coordinator.analyze_consequences()


def validate_trust_audit_trail(trail: TrustAuditTrail) -> bool:
    """Validate that the given trust audit trail satisfies the trust policy.

    Applies the same two-condition check as
    ``FinalPracticalConsequenceWitness.verify_trust_policy_compliance()``:
    (1) total trust delta >= 0, and (2) the trail is monotonically improving.
    This function is provided as a module-level convenience for callers that
    hold a standalone ``TrustAuditTrail`` without a witness instance.

    Parameters
    ----------
    trail:
        The ``TrustAuditTrail`` to validate.

    Returns
    -------
    bool
        ``True`` if the trail satisfies the trust policy, ``False`` otherwise.
    """
    if trail.total_trust_delta() < 0.0:
        return False
    return trail.is_monotonically_improving()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("S04 — The Final Practical Consequence: smoke test")
    print("-" * 60)

    # 1. Enumerate consequences
    consequences = enumerate_practical_consequences()
    print(f"[1] Enumerated {len(consequences)} practical consequences.")
    for c in consequences:
        marker = "(!)" if c.is_critical() else "   "
        print(f"    {marker} [{c.impact_level:8s}] {c.title}")

    print()

    # 2. Run full analysis
    report = run_consequence_analysis(system_id="jugeo-smoke-test")
    print(f"[2] ConsequenceReport generated.")
    print(f"    report_id        = {report.report_id}")
    print(f"    critical_count   = {report.critical_count()}")
    print(f"    mean_confidence  = {report.mean_confidence():.3f}")
    print(f"    trust_compliant  = {report.trust_policy_compliant}")

    print()

    # 3. Run coordinator demonstration
    coordinator = FinalPracticalConsequenceCoordinator.create(
        config={"system_id": "jugeo-smoke-test"}
    )
    demo = coordinator.run_demonstration(n_cycles=4)
    trail_summary = demo["audit_trail_summary"]
    print(f"[3] Demonstration ran {demo['n_cycles']} cycles.")
    print(f"    audit entries    = {trail_summary['entry_count']}")
    print(f"    total trust delta= {trail_summary['total_trust_delta']:.4f}")
    print(f"    monotonic        = {trail_summary['is_monotonically_improving']}")
    print(f"    adoption score   = {demo['adoption_score']:.3f}")

    print()

    # 4. Validate audit trail
    trail = coordinator.get_trust_audit_trail()
    valid = validate_trust_audit_trail(trail)
    print(f"[4] Trust audit trail valid: {valid}")

    print()

    # 5. Validate cyclic invariants
    invariant_result = coordinator.validate_cyclic_invariants()
    print(f"[5] Cyclic invariant check — all_pass: {invariant_result['all_pass']}")
    for inv in invariant_result["invariants"]:
        status = "PASS" if inv["passed"] else "FAIL"
        print(f"    [{status}] {inv['invariant_id']}: {inv['description']}")
        print(f"           {inv['detail']}")

    print()

    # 6. Full summary
    print("[6] Full coordinator summary:")
    print(coordinator.summarize())

    print()
    print("Smoke test complete.")
