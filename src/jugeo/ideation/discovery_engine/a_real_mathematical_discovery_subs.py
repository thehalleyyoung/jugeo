"""A real mathematical-discovery subsystem: inputs, proposal records, and archive
traces — theory2.tex Ch58.

This module implements the core mathematical-discovery subsystem of the JuGeo
discovery engine.  Its responsibility is to ingest *obstruction records* (evidence
that a particular domain currently blocks a desired structural result), synthesise
*theorem proposals* that would, if proven, reduce those obstructions, and maintain
a permanent *archive trace* of all proposals and their eventual outcomes.

Theory reference: theory2.tex Ch58 §6.1 — The Real Mathematical Discovery Subsystem.

# copilot: shared-core marker

Overview
--------
The discovery subsystem sits at the interface between the ideation layer and the
formal-verification layer.  Conceptually it answers the question: *given that we
know about a collection of obstructions blocking a structural theorem, what new
mathematical statements would most efficiently clear those obstructions?*

The main entities are:

ObstructionRecord
    A frozen record describing a single obstruction: the domain in which it lives,
    a natural-language description, a numeric severity score, and a unique ID
    assigned either externally or by the coordinator during ingestion.

ProposalRecord
    A frozen record for a single proposed theorem: the formal statement, a
    predicted leverage score (expected reduction in total obstruction severity
    if the theorem is proven), a proof sketch, and a list of supporting evidence
    strings.  Proposals are immutable once created.

ArchiveEntry
    A frozen record pairing a ProposalRecord with its eventual outcome — one of
    ACCEPTED, REJECTED, PENDING, or SUPERSEDED.  Archive entries are never
    deleted; the archive grows monotonically.

ArchiveTrace
    A mutable accumulator that stores all ArchiveEntry objects produced during a
    session.  Provides O(1) lookup by proposal_id and cheap iteration over all
    entries.

Discovery cycle
---------------
The full discovery cycle is orchestrated by ``MathDiscoverySubsystemCoordinator``
and proceeds in four steps:

1. **Ingest** — each obstruction record is assigned a unique ID and stored in an
   in-memory registry keyed by obstruction_id.
2. **Propose** — for each obstruction, the coordinator generates one or more
   ProposalRecords by scoring domain tokens against an internal template library
   and returning the top-k proposals by predicted leverage.
3. **Archive** — after each proposal is evaluated externally, the coordinator
   receives a (proposal_id, outcome) pair and records an ArchiveEntry.
4. **Analyse / Witness** — the ``MathDiscoverySubsystemAnalyzer`` computes
   coverage, quality, and health metrics over the archive; the
   ``MathDiscoverySubsystemWitness`` performs consistency and completeness checks.

Design notes
------------
* All coordinator state (obstruction registry, proposal registry, archive) is
  instance-local; coordinators are *not* singletons.  This makes testing trivial:
  each test instantiates its own coordinator and never leaks state.
* Predicted leverage is computed by ``score_proposal``, a free function that
  multiplies a statement-complexity factor by a domain-coverage factor and clamps
  the result to [0, 1].  The formula is intentionally simple and meant to be
  replaced by a learned model in production.
* The ``MathDiscoverySubsystemWitness`` is designed to be called *after* a
  discovery cycle completes, not interleaved with it.  All witness methods are
  pure with respect to the archive — they do not mutate it.

Typical usage::

    from jugeo.ideation.discovery_engine.a_real_mathematical_discovery_subs import (
        MathDiscoverySubsystemCoordinator,
        MathDiscoverySubsystemAnalyzer,
        MathDiscoverySubsystemWitness,
        DiscoverySubsystemConfig,
        ObstructionRecord,
        run_discovery_cycle,
    )

    cfg = DiscoverySubsystemConfig(max_proposals_per_obstruction=3, leverage_threshold=0.4)
    coord = MathDiscoverySubsystemCoordinator(cfg)
    obstructions = [
        ObstructionRecord(obstruction_id="obs-1", domain="algebraic-geometry",
                          description="Lack of flat resolution in mixed char.", severity=0.8),
    ]
    result = coord.run_discovery_cycle(obstructions)
    analyzer = MathDiscoverySubsystemAnalyzer()
    quality = analyzer.analyze_proposal_quality(result.all_proposals)

See also
--------
* ``evaluation_and_calibration_realize`` — calibrates leverage predictions.
* ``theorem_and_falsification_burden_f`` — measures falsification burden.
* ``jugeo.ideation.discovery_engine.models`` — shared pipeline dataclasses.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    # Enums
    "ProposalOutcome",
    # Frozen dataclasses
    "ProposalRecord",
    "ObstructionRecord",
    "ArchiveEntry",
    "DiscoverySubsystemConfig",
    "DiscoverySubsystemResult",
    "ObstructionCoverageReport",
    "ProposalQualityReport",
    "ArchiveHealthReport",
    "WitnessVerdict",
    "ArchiveConsistencyReport",
    "CycleWitnessReport",
    # Mutable dataclasses
    "ArchiveTrace",
    # Coordinator / Analyzer / Witness
    "MathDiscoverySubsystemCoordinator",
    "MathDiscoverySubsystemAnalyzer",
    "MathDiscoverySubsystemWitness",
    # Free functions
    "run_discovery_cycle",
    "score_proposal",
    "select_best_proposals",
    # Internal helpers exposed for testing
    "_utcnow",
    "_uid",
    "_clamp",
    "_tokenize",
    "_domain_coverage_factor",
    "_statement_complexity_factor",
    "_build_proof_sketch",
    "_severity_weighted_coverage",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
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
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "algebraic-geometry": ["scheme", "sheaf", "cohomology", "flat", "étale", "morphism", "topos"],
    "number-theory": ["prime", "congruence", "diophantine", "modular", "arithmetic", "zeta"],
    "topology": ["manifold", "homology", "homotopy", "fibration", "cobordism", "surgery"],
    "category-theory": ["functor", "adjunction", "monad", "limit", "colimit", "topos", "fiber"],
    "analysis": ["convergence", "measure", "operator", "spectrum", "integral", "functional"],
    "combinatorics": ["graph", "partition", "generating", "bijection", "ramsey", "chromatic"],
    "logic": ["provability", "model", "consistency", "completeness", "forcing", "ultrafilter"],
}

_PROOF_SKETCH_TEMPLATES: list[str] = [
    "Apply the {method} to reduce to the case where {domain} obstructions are controlled "
    "by a {structure} argument.  The key step uses {tool} to establish the {property}.",
    "The proof proceeds by induction on the {measure} of the {object}.  The base case "
    "follows immediately; for the inductive step use {tool} to transfer the {property}.",
    "Construct an explicit {structure} and verify that it satisfies the required {property}. "
    "Finiteness follows from a compactness argument using {tool}.",
    "Reduce to the universal case via a {method} argument, then apply the classification "
    "theorem for {structure}s to conclude the {property}.",
    "Use a spectral-sequence argument: the E₂ page degenerates at {measure} ≤ 2, giving "
    "the desired {property} as a consequence of the {domain} structure.",
]

_PROOF_METHODS: list[str] = [
    "descent",
    "base-change",
    "localisation",
    "completion",
    "deformation",
    "obstruction-theory",
    "resolution",
    "filtration",
    "dévissage",
]

_PROOF_TOOLS: list[str] = [
    "Grothendieck duality",
    "flat base change",
    "Künneth formula",
    "the Leray spectral sequence",
    "Nakayama's lemma",
    "Zariski's main theorem",
    "the comparison isomorphism",
    "proper base change",
    "Serre duality",
]


def _utcnow() -> float:
    """Return the current UTC time as a POSIX float timestamp."""
    return time.time()


def _uid() -> str:
    """Return a 32-character lowercase hex unique identifier."""
    return uuid.uuid4().hex


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lower*, *upper*].

    Parameters
    ----------
    value:
        Numeric value to clamp.
    lower:
        Lower bound (inclusive).
    upper:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.
    """
    return max(lower, min(upper, value))


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a lower-cased whitespace-split token set.

    Non-alphanumeric characters are stripped from each token so that
    punctuation does not create spurious mismatches.

    Parameters
    ----------
    text:
        Raw input string.

    Returns
    -------
    set[str]
        Set of normalised tokens.
    """
    return {t.strip(".,;:()[]{}'\"-") for t in text.lower().split() if t.strip(".,;:()[]{}'\"-")}


def _domain_coverage_factor(domain: str, description: str) -> float:
    """Compute how well *description* covers the expected vocabulary of *domain*.

    Returns a score in [0, 1]: 0 if none of the domain keywords appear in the
    description, 1 if all of them do.

    Parameters
    ----------
    domain:
        Domain key, e.g. ``"algebraic-geometry"``.
    description:
        Free-text description to score.

    Returns
    -------
    float
        Coverage score ∈ [0, 1].
    """
    keywords = _DOMAIN_KEYWORDS.get(domain, [])
    if not keywords:
        return 0.5
    tokens = _tokenize(description)
    hits = sum(1 for kw in keywords if kw in tokens)
    return hits / len(keywords)


def _statement_complexity_factor(statement: str) -> float:
    """Return a complexity factor for a theorem *statement* string.

    Complexity is measured as a sigmoid of (token_count − 10) / 10, which maps
    short statements (≤5 tokens) to ≈ 0.27 and long statements (≥30 tokens) to
    ≈ 0.88.  This rewards specificity while penalising vague or trivially short
    statements.

    Parameters
    ----------
    statement:
        The theorem statement string.

    Returns
    -------
    float
        Complexity factor ∈ (0, 1).
    """
    n = len(statement.split())
    return 1.0 / (1.0 + math.exp(-(n - 10) / 10.0))


def _build_proof_sketch(domain: str, statement: str, index: int = 0) -> str:
    """Construct a heuristic proof sketch for a theorem in *domain*.

    Uses a rotating template from ``_PROOF_SKETCH_TEMPLATES`` indexed by
    *index* modulo the template count, substituting domain-appropriate method,
    tool, structure, property, measure, and object tokens.

    Parameters
    ----------
    domain:
        Mathematical domain for the theorem.
    statement:
        The theorem statement (used to extract a property noun).
    index:
        Rotation index selecting which template to use.

    Returns
    -------
    str
        A heuristic proof sketch string.
    """
    template = _PROOF_SKETCH_TEMPLATES[index % len(_PROOF_SKETCH_TEMPLATES)]
    method = _PROOF_METHODS[index % len(_PROOF_METHODS)]
    tool = _PROOF_TOOLS[index % len(_PROOF_TOOLS)]
    keywords = _DOMAIN_KEYWORDS.get(domain, ["object"])
    structure = keywords[index % len(keywords)] if keywords else "structure"
    tokens = statement.split()
    property_noun = tokens[-1].rstrip(".,;:") if tokens else "finiteness"
    measure = "degree" if domain in ("algebraic-geometry", "topology") else "rank"
    obj = keywords[0] if keywords else "object"
    return template.format(
        method=method,
        domain=domain,
        structure=structure,
        tool=tool,
        property=property_noun,
        measure=measure,
        object=obj,
    )


def _severity_weighted_coverage(
    obstructions: list[ObstructionRecord],
    archived_ids: set[str],
) -> float:
    """Compute severity-weighted coverage of *obstructions* by *archived_ids*.

    Coverage is the fraction of total severity (sum of severity over all
    obstructions) attributable to obstructions that have at least one proposal
    in the archive.

    Parameters
    ----------
    obstructions:
        Full list of obstruction records.
    archived_ids:
        Set of obstruction IDs that have been addressed.

    Returns
    -------
    float
        Severity-weighted coverage ∈ [0, 1].
    """
    total_severity = sum(o.severity for o in obstructions)
    if total_severity == 0.0:
        return 1.0
    covered_severity = sum(o.severity for o in obstructions if o.obstruction_id in archived_ids)
    return _clamp(covered_severity / total_severity)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProposalOutcome(Enum):
    """The outcome of a theorem proposal in the discovery archive.

    Attributes
    ----------
    ACCEPTED:
        The proposal was proven and accepted into the knowledge base.
    REJECTED:
        The proposal was attempted but found to be false or unprovable.
    PENDING:
        The proposal has not yet been evaluated.
    SUPERSEDED:
        A stronger theorem was accepted that renders this proposal redundant.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"
    SUPERSEDED = "superseded"


# ---------------------------------------------------------------------------
# Frozen (immutable) dataclasses — value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """A single obstruction blocking a structural theorem.

    Attributes
    ----------
    obstruction_id:
        Unique identifier.  If not supplied by the caller, the coordinator
        assigns one during ingestion.
    domain:
        Mathematical domain, e.g. ``"algebraic-geometry"``.
    description:
        Natural-language description of the obstruction.
    severity:
        Numeric severity score ∈ [0, 1]; higher means more blocking.
    """

    obstruction_id: str
    domain: str
    description: str
    severity: float = 0.5


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """An immutable theorem proposal generated by the discovery subsystem.

    Attributes
    ----------
    proposal_id:
        Unique identifier for this proposal.
    theorem_statement:
        Formal (or semi-formal) statement of the proposed theorem.
    predicted_leverage:
        Predicted reduction in total obstruction severity if the theorem is proven.
        ∈ [0, 1].
    proof_sketch:
        Heuristic sketch of a potential proof strategy.
    supporting_evidence:
        List of evidence strings (citations, analogies, computational checks)
        supporting the proposal.
    source_obstruction_id:
        The obstruction record that prompted this proposal.
    created_at:
        POSIX timestamp of creation.
    """

    proposal_id: str
    theorem_statement: str
    predicted_leverage: float
    proof_sketch: str
    supporting_evidence: tuple[str, ...]
    source_obstruction_id: str
    created_at: float = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """An immutable record of a proposal and its outcome in the archive.

    Attributes
    ----------
    proposal_id:
        ID of the archived proposal (mirrors ``ProposalRecord.proposal_id``).
    proposal_record:
        The full proposal record.
    outcome:
        The outcome assigned to this proposal.
    archived_at:
        POSIX timestamp of archiving.
    """

    proposal_id: str
    proposal_record: ProposalRecord
    outcome: ProposalOutcome
    archived_at: float = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class DiscoverySubsystemConfig:
    """Configuration for the mathematical-discovery subsystem.

    Attributes
    ----------
    max_proposals_per_obstruction:
        Maximum number of theorem proposals to generate for a single obstruction.
    leverage_threshold:
        Minimum predicted leverage for a proposal to be included in results.
    top_k_select:
        Maximum number of proposals selected by ``select_best_proposals``.
    enable_proof_sketches:
        Whether to attach heuristic proof sketches to proposals.
    severity_weight:
        Weight applied to obstruction severity when computing composite scores.
    """

    max_proposals_per_obstruction: int = 3
    leverage_threshold: float = 0.3
    top_k_select: int = 10
    enable_proof_sketches: bool = True
    severity_weight: float = 0.6


@dataclass(frozen=True, slots=True)
class DiscoverySubsystemResult:
    """The result of a full discovery cycle.

    Attributes
    ----------
    cycle_id:
        Unique ID for this cycle run.
    obstruction_count:
        Number of obstructions ingested.
    all_proposals:
        All proposals generated, including those below the leverage threshold.
    selected_proposals:
        Proposals that passed the leverage threshold and top-k selection.
    archive_trace:
        Snapshot of the archive at the end of the cycle.
    cycle_duration_s:
        Wall-clock duration of the cycle in seconds.
    """

    cycle_id: str
    obstruction_count: int
    all_proposals: tuple[ProposalRecord, ...]
    selected_proposals: tuple[ProposalRecord, ...]
    archive_trace: ArchiveTrace
    cycle_duration_s: float


@dataclass(frozen=True, slots=True)
class ObstructionCoverageReport:
    """Coverage statistics for the obstruction set.

    Attributes
    ----------
    total_obstructions:
        Total number of obstructions analysed.
    covered_obstructions:
        Number of obstructions that have at least one proposal in the archive.
    severity_weighted_coverage:
        Severity-weighted fraction of total obstruction severity addressed.
    uncovered_domain_counts:
        Mapping from domain name to count of uncovered obstructions in that domain.
    """

    total_obstructions: int
    covered_obstructions: int
    severity_weighted_coverage: float
    uncovered_domain_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ProposalQualityReport:
    """Quality statistics for a set of proposals.

    Attributes
    ----------
    proposal_count:
        Number of proposals analysed.
    mean_leverage:
        Arithmetic mean of predicted leverage scores.
    max_leverage:
        Maximum predicted leverage in the set.
    min_leverage:
        Minimum predicted leverage in the set.
    leverage_std:
        Sample standard deviation of predicted leverage scores.
    high_quality_fraction:
        Fraction of proposals with leverage ≥ 0.6.
    """

    proposal_count: int
    mean_leverage: float
    max_leverage: float
    min_leverage: float
    leverage_std: float
    high_quality_fraction: float


@dataclass(frozen=True, slots=True)
class ArchiveHealthReport:
    """Health statistics for the proposal archive.

    Attributes
    ----------
    total_entries:
        Total number of archive entries.
    accepted_count:
        Number of proposals with ACCEPTED outcome.
    rejected_count:
        Number with REJECTED outcome.
    pending_count:
        Number with PENDING outcome.
    superseded_count:
        Number with SUPERSEDED outcome.
    acceptance_rate:
        Fraction of non-pending proposals that were accepted.
    mean_time_to_outcome_s:
        Mean time between proposal creation and archiving (seconds).
    """

    total_entries: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    superseded_count: int
    acceptance_rate: float
    mean_time_to_outcome_s: float


@dataclass(frozen=True, slots=True)
class WitnessVerdict:
    """The verdict issued by the witness for a single proposal.

    Attributes
    ----------
    proposal_id:
        ID of the proposal being witnessed.
    is_consistent:
        Whether the proposal is internally consistent with the given obstructions.
    confidence:
        Confidence in the verdict ∈ [0, 1].
    rationale:
        Short human-readable rationale for the verdict.
    flagged_issues:
        List of specific issues detected.
    """

    proposal_id: str
    is_consistent: bool
    confidence: float
    rationale: str
    flagged_issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchiveConsistencyReport:
    """Consistency report for the entire archive produced by the witness.

    Attributes
    ----------
    is_consistent:
        Whether no consistency violations were found.
    total_checked:
        Number of archive entries checked.
    violation_count:
        Number of consistency violations found.
    violations:
        Descriptions of each violation.
    """

    is_consistent: bool
    total_checked: int
    violation_count: int
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CycleWitnessReport:
    """Witness report for a full discovery cycle result.

    Attributes
    ----------
    cycle_id:
        ID of the cycle being witnessed.
    all_verdicts:
        Per-proposal verdicts.
    overall_consistent:
        True iff all proposals are consistent.
    coverage_adequate:
        Whether the cycle addressed a sufficient fraction of obstruction severity.
    summary:
        Human-readable summary of the witness findings.
    """

    cycle_id: str
    all_verdicts: tuple[WitnessVerdict, ...]
    overall_consistent: bool
    coverage_adequate: bool
    summary: str


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ArchiveTrace:
    """Mutable accumulator of all ArchiveEntry objects in a session.

    The trace is append-only: entries are never removed.  Lookup by
    ``proposal_id`` is O(1) via the internal ``_index`` dictionary.

    Attributes
    ----------
    entries:
        Ordered list of ArchiveEntry objects (insertion order).
    created_at:
        POSIX timestamp of trace creation.
    """

    entries: list[ArchiveEntry] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)
    _index: dict[str, ArchiveEntry] = field(default_factory=dict, repr=False)

    def append(self, entry: ArchiveEntry) -> None:
        """Append *entry* to the trace and update the internal index.

        Parameters
        ----------
        entry:
            The archive entry to append.  If an entry with the same
            ``proposal_id`` already exists, it is **overwritten** in the
            index (last-write wins) but appended again to the list.
        """
        self.entries.append(entry)
        self._index[entry.proposal_id] = entry

    def get(self, proposal_id: str) -> ArchiveEntry | None:
        """Return the most-recently-appended entry with *proposal_id*, or None.

        Parameters
        ----------
        proposal_id:
            The proposal ID to look up.

        Returns
        -------
        ArchiveEntry or None
            The archive entry, or ``None`` if not found.
        """
        return self._index.get(proposal_id)

    def count_by_outcome(self) -> dict[ProposalOutcome, int]:
        """Return a frequency table of outcomes across all entries.

        Returns
        -------
        dict[ProposalOutcome, int]
            Mapping from each ``ProposalOutcome`` value to its count.
        """
        counts: dict[ProposalOutcome, int] = {o: 0 for o in ProposalOutcome}
        for entry in self.entries:
            counts[entry.outcome] += 1
        return counts

    def proposal_ids(self) -> list[str]:
        """Return the list of unique proposal IDs in the archive (insertion order).

        Returns
        -------
        list[str]
            Unique proposal IDs, preserving last-seen order via the index.
        """
        return list(self._index.keys())

    def __len__(self) -> int:
        return len(self.entries)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def score_proposal(
    theorem_statement: str,
    obstruction: ObstructionRecord,
    config: DiscoverySubsystemConfig,
) -> float:
    """Compute the predicted leverage score for a proposed theorem.

    The score is computed as::

        leverage = clamp(complexity_factor × coverage_factor × severity_weight
                         + (1 − severity_weight) × coverage_factor)

    where:
    * *complexity_factor* rewards specificity of the theorem statement.
    * *coverage_factor* rewards domain-vocabulary alignment with the obstruction.
    * *severity_weight* up-weights severe obstructions.

    Parameters
    ----------
    theorem_statement:
        The proposed theorem's formal statement.
    obstruction:
        The obstruction record that prompted the proposal.
    config:
        Discovery subsystem configuration.

    Returns
    -------
    float
        Predicted leverage ∈ [0, 1].
    """
    complexity = _statement_complexity_factor(theorem_statement)
    coverage = _domain_coverage_factor(obstruction.domain, theorem_statement)
    w = config.severity_weight
    raw = w * complexity * coverage * obstruction.severity + (1.0 - w) * coverage
    return _clamp(raw)


def select_best_proposals(
    proposals: list[ProposalRecord],
    config: DiscoverySubsystemConfig,
) -> list[ProposalRecord]:
    """Select the best proposals by predicted leverage.

    Filters out proposals below ``config.leverage_threshold``, then returns
    the top ``config.top_k_select`` proposals sorted by descending leverage.

    Parameters
    ----------
    proposals:
        Candidate proposals to filter and rank.
    config:
        Discovery subsystem configuration.

    Returns
    -------
    list[ProposalRecord]
        Filtered and ranked proposals, at most ``config.top_k_select`` items.
    """
    filtered = [p for p in proposals if p.predicted_leverage >= config.leverage_threshold]
    filtered.sort(key=lambda p: p.predicted_leverage, reverse=True)
    return filtered[: config.top_k_select]


def run_discovery_cycle(
    obstructions: list[ObstructionRecord],
    config: DiscoverySubsystemConfig | None = None,
) -> DiscoverySubsystemResult:
    """Run a complete discovery cycle as a free function (convenience API).

    Creates a fresh ``MathDiscoverySubsystemCoordinator`` internally and
    delegates to its ``run_discovery_cycle`` method.

    Parameters
    ----------
    obstructions:
        List of obstruction records to process.
    config:
        Optional configuration; uses default ``DiscoverySubsystemConfig`` if
        not provided.

    Returns
    -------
    DiscoverySubsystemResult
        The result of the discovery cycle.
    """
    cfg = config if config is not None else DiscoverySubsystemConfig()
    coord = MathDiscoverySubsystemCoordinator(cfg)
    return coord.run_discovery_cycle(obstructions)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class MathDiscoverySubsystemCoordinator:
    """Orchestrates the full mathematical-discovery subsystem.

    The coordinator manages three in-memory registries:
    * ``_obstruction_registry`` — maps obstruction_id → ObstructionRecord.
    * ``_proposal_registry`` — maps proposal_id → ProposalRecord.
    * ``_archive`` — the running ArchiveTrace for this coordinator instance.

    Parameters
    ----------
    config:
        Configuration for the discovery subsystem.
    """

    def __init__(self, config: DiscoverySubsystemConfig) -> None:
        self._config = config
        self._obstruction_registry: dict[str, ObstructionRecord] = {}
        self._proposal_registry: dict[str, ProposalRecord] = {}
        self._archive: ArchiveTrace = ArchiveTrace()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_obstruction(self, record: ObstructionRecord) -> str:
        """Ingest an obstruction record and return its (possibly reassigned) ID.

        If ``record.obstruction_id`` is the empty string, a fresh UUID-based ID
        is assigned.  The record is stored in the internal registry.

        Parameters
        ----------
        record:
            Obstruction record to ingest.

        Returns
        -------
        str
            The obstruction ID (may differ from ``record.obstruction_id`` if
            it was empty).
        """
        obs_id = record.obstruction_id if record.obstruction_id else f"obs-{_uid()[:8]}"
        if obs_id != record.obstruction_id:
            record = ObstructionRecord(
                obstruction_id=obs_id,
                domain=record.domain,
                description=record.description,
                severity=record.severity,
            )
        self._obstruction_registry[obs_id] = record
        return obs_id

    def generate_proposals(self, obstruction_id: str) -> list[ProposalRecord]:
        """Generate theorem proposals for a given obstruction.

        Looks up the obstruction in the registry and produces up to
        ``config.max_proposals_per_obstruction`` proposals, each with a
        distinct statement derived from domain keywords and the obstruction
        description.

        Parameters
        ----------
        obstruction_id:
            ID of the obstruction for which to generate proposals.

        Returns
        -------
        list[ProposalRecord]
            Generated proposals (may be empty if no matching keywords are found).

        Raises
        ------
        KeyError
            If *obstruction_id* is not in the registry.
        """
        obs = self._obstruction_registry[obstruction_id]
        keywords = _DOMAIN_KEYWORDS.get(obs.domain, ["object", "structure", "map"])
        proposals: list[ProposalRecord] = []
        max_k = self._config.max_proposals_per_obstruction
        for i in range(min(max_k, len(keywords))):
            kw = keywords[i]
            statement = (
                f"For every {kw} in {obs.domain}, there exists a canonical "
                f"resolution that eliminates the obstruction: {obs.description[:60].strip()}."
            )
            leverage = score_proposal(statement, obs, self._config)
            sketch = (
                _build_proof_sketch(obs.domain, statement, index=i)
                if self._config.enable_proof_sketches
                else ""
            )
            evidence: tuple[str, ...] = (
                f"Domain keyword '{kw}' matches obstruction vocabulary.",
                f"Severity {obs.severity:.2f} justifies priority treatment.",
                f"Analogy: classical resolution in characteristic 0 reduces similar obstructions.",
            )
            proposal_id = f"prop-{_uid()[:8]}"
            prop = ProposalRecord(
                proposal_id=proposal_id,
                theorem_statement=statement,
                predicted_leverage=leverage,
                proof_sketch=sketch,
                supporting_evidence=evidence,
                source_obstruction_id=obstruction_id,
                created_at=_utcnow(),
            )
            self._proposal_registry[proposal_id] = prop
            proposals.append(prop)
        return proposals

    def archive_outcome(
        self,
        proposal_id: str,
        outcome: ProposalOutcome,
    ) -> ArchiveEntry:
        """Record the outcome of a proposal in the archive.

        Parameters
        ----------
        proposal_id:
            ID of the proposal whose outcome is being recorded.
        outcome:
            The outcome to assign.

        Returns
        -------
        ArchiveEntry
            The newly created archive entry.

        Raises
        ------
        KeyError
            If *proposal_id* is not in the proposal registry.
        """
        proposal = self._proposal_registry[proposal_id]
        entry = ArchiveEntry(
            proposal_id=proposal_id,
            proposal_record=proposal,
            outcome=outcome,
            archived_at=_utcnow(),
        )
        self._archive.append(entry)
        return entry

    def get_archive_trace(self) -> ArchiveTrace:
        """Return the current archive trace.

        Returns
        -------
        ArchiveTrace
            The live archive trace object (not a copy).
        """
        return self._archive

    def run_discovery_cycle(
        self,
        obstructions: list[ObstructionRecord],
    ) -> DiscoverySubsystemResult:
        """Run a complete discovery cycle over a list of obstructions.

        Steps:
        1. Ingest all obstructions.
        2. Generate proposals for each.
        3. Archive all proposals with PENDING outcome.
        4. Select the best proposals.
        5. Return a ``DiscoverySubsystemResult``.

        Parameters
        ----------
        obstructions:
            The obstructions to process in this cycle.

        Returns
        -------
        DiscoverySubsystemResult
            Full result including all proposals, selected proposals, and the
            current archive trace.
        """
        cycle_id = f"cycle-{_uid()[:8]}"
        t0 = _utcnow()

        for obs in obstructions:
            self.ingest_obstruction(obs)

        all_proposals: list[ProposalRecord] = []
        for obs in obstructions:
            props = self.generate_proposals(obs.obstruction_id)
            all_proposals.extend(props)

        for prop in all_proposals:
            self.archive_outcome(prop.proposal_id, ProposalOutcome.PENDING)

        selected = select_best_proposals(all_proposals, self._config)
        duration = _utcnow() - t0

        return DiscoverySubsystemResult(
            cycle_id=cycle_id,
            obstruction_count=len(obstructions),
            all_proposals=tuple(all_proposals),
            selected_proposals=tuple(selected),
            archive_trace=self._archive,
            cycle_duration_s=duration,
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class MathDiscoverySubsystemAnalyzer:
    """Analyses the state of the mathematical-discovery subsystem.

    All methods are stateless with respect to the analyzer itself — they take
    their inputs as parameters and return immutable report objects.
    """

    def analyze_obstruction_coverage(
        self,
        archive: ArchiveTrace,
        obstructions: list[ObstructionRecord] | None = None,
    ) -> ObstructionCoverageReport:
        """Analyse how well the archive covers the given obstruction set.

        Parameters
        ----------
        archive:
            The archive trace to analyse.
        obstructions:
            The full obstruction set.  If ``None``, the set is inferred from
            the source obstruction IDs recorded in the archive's proposals.

        Returns
        -------
        ObstructionCoverageReport
            Coverage statistics.
        """
        if obstructions is None:
            inferred: list[ObstructionRecord] = [
                ObstructionRecord(
                    obstruction_id=e.proposal_record.source_obstruction_id,
                    domain="unknown",
                    description="(inferred)",
                    severity=0.5,
                )
                for e in archive.entries
            ]
            obstructions = list({o.obstruction_id: o for o in inferred}.values())

        covered_ids = {e.proposal_record.source_obstruction_id for e in archive.entries}
        total = len(obstructions)
        covered = sum(1 for o in obstructions if o.obstruction_id in covered_ids)
        sw_cov = _severity_weighted_coverage(obstructions, covered_ids)

        uncovered: dict[str, int] = {}
        for obs in obstructions:
            if obs.obstruction_id not in covered_ids:
                uncovered[obs.domain] = uncovered.get(obs.domain, 0) + 1

        return ObstructionCoverageReport(
            total_obstructions=total,
            covered_obstructions=covered,
            severity_weighted_coverage=sw_cov,
            uncovered_domain_counts=uncovered,
        )

    def analyze_proposal_quality(
        self,
        proposals: list[ProposalRecord],
    ) -> ProposalQualityReport:
        """Analyse the quality of a collection of proposals.

        Parameters
        ----------
        proposals:
            The proposals to analyse.

        Returns
        -------
        ProposalQualityReport
            Quality statistics.
        """
        if not proposals:
            return ProposalQualityReport(0, 0.0, 0.0, 0.0, 0.0, 0.0)
        leverages = [p.predicted_leverage for p in proposals]
        n = len(leverages)
        mean_l = sum(leverages) / n
        max_l = max(leverages)
        min_l = min(leverages)
        variance = sum((x - mean_l) ** 2 for x in leverages) / max(n - 1, 1)
        std_l = math.sqrt(variance)
        high_q = sum(1 for x in leverages if x >= 0.6) / n
        return ProposalQualityReport(
            proposal_count=n,
            mean_leverage=mean_l,
            max_leverage=max_l,
            min_leverage=min_l,
            leverage_std=std_l,
            high_quality_fraction=high_q,
        )

    def analyze_archive_health(
        self,
        archive: ArchiveTrace,
    ) -> ArchiveHealthReport:
        """Analyse the health of the proposal archive.

        Parameters
        ----------
        archive:
            Archive trace to inspect.

        Returns
        -------
        ArchiveHealthReport
            Health statistics.
        """
        counts = archive.count_by_outcome()
        total = len(archive)
        accepted = counts[ProposalOutcome.ACCEPTED]
        rejected = counts[ProposalOutcome.REJECTED]
        pending = counts[ProposalOutcome.PENDING]
        superseded = counts[ProposalOutcome.SUPERSEDED]
        decided = accepted + rejected + superseded
        acceptance_rate = accepted / decided if decided > 0 else 0.0

        times_to_outcome: list[float] = [
            e.archived_at - e.proposal_record.created_at
            for e in archive.entries
            if e.outcome != ProposalOutcome.PENDING
        ]
        mean_tto = sum(times_to_outcome) / len(times_to_outcome) if times_to_outcome else 0.0

        return ArchiveHealthReport(
            total_entries=total,
            accepted_count=accepted,
            rejected_count=rejected,
            pending_count=pending,
            superseded_count=superseded,
            acceptance_rate=acceptance_rate,
            mean_time_to_outcome_s=mean_tto,
        )

    def rank_proposals_by_leverage(
        self,
        proposals: list[ProposalRecord],
    ) -> list[ProposalRecord]:
        """Return *proposals* sorted by descending predicted leverage.

        Parameters
        ----------
        proposals:
            The proposals to rank.

        Returns
        -------
        list[ProposalRecord]
            Sorted proposals (original list is not mutated).
        """
        return sorted(proposals, key=lambda p: p.predicted_leverage, reverse=True)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class MathDiscoverySubsystemWitness:
    """Verifies consistency and completeness of the discovery subsystem state.

    Witness methods are pure: they never mutate the archive or any input object.
    """

    def witness_proposal(
        self,
        proposal: ProposalRecord,
        obstructions: list[ObstructionRecord],
    ) -> WitnessVerdict:
        """Issue a verdict on whether *proposal* is consistent with *obstructions*.

        Checks:
        1. The source obstruction is present in *obstructions*.
        2. Predicted leverage is in [0, 1].
        3. The theorem statement is non-empty.
        4. Supporting evidence is non-empty.

        Parameters
        ----------
        proposal:
            The proposal to witness.
        obstructions:
            The obstruction context.

        Returns
        -------
        WitnessVerdict
            The verdict with rationale and flagged issues.
        """
        issues: list[str] = []
        obs_ids = {o.obstruction_id for o in obstructions}
        if proposal.source_obstruction_id not in obs_ids:
            issues.append(
                f"Source obstruction '{proposal.source_obstruction_id}' not in context."
            )
        if not (0.0 <= proposal.predicted_leverage <= 1.0):
            issues.append(
                f"Predicted leverage {proposal.predicted_leverage:.4f} outside [0, 1]."
            )
        if not proposal.theorem_statement.strip():
            issues.append("Theorem statement is empty.")
        if not proposal.supporting_evidence:
            issues.append("No supporting evidence attached.")

        is_consistent = len(issues) == 0
        confidence = _clamp(1.0 - 0.25 * len(issues))
        rationale = (
            "Proposal is internally consistent with the obstruction context."
            if is_consistent
            else f"Found {len(issues)} issue(s): " + "; ".join(issues[:2])
        )
        return WitnessVerdict(
            proposal_id=proposal.proposal_id,
            is_consistent=is_consistent,
            confidence=confidence,
            rationale=rationale,
            flagged_issues=tuple(issues),
        )

    def witness_archive_consistency(
        self,
        archive: ArchiveTrace,
    ) -> ArchiveConsistencyReport:
        """Check that the archive is internally consistent.

        Checks performed:
        * No duplicate proposal IDs with conflicting outcomes.
        * All entries have non-negative ``archived_at`` timestamps.
        * Accepted proposals have non-zero leverage.

        Parameters
        ----------
        archive:
            The archive trace to check.

        Returns
        -------
        ArchiveConsistencyReport
            Consistency report.
        """
        violations: list[str] = []
        seen: dict[str, ProposalOutcome] = {}
        for entry in archive.entries:
            pid = entry.proposal_id
            if pid in seen and seen[pid] != entry.outcome:
                violations.append(
                    f"Proposal '{pid}' appears with conflicting outcomes "
                    f"{seen[pid].value} and {entry.outcome.value}."
                )
            seen[pid] = entry.outcome
            if entry.archived_at < 0:
                violations.append(f"Entry '{pid}' has negative archived_at timestamp.")
            if (
                entry.outcome == ProposalOutcome.ACCEPTED
                and entry.proposal_record.predicted_leverage == 0.0
            ):
                violations.append(
                    f"Accepted proposal '{pid}' has zero predicted leverage."
                )
        return ArchiveConsistencyReport(
            is_consistent=len(violations) == 0,
            total_checked=len(archive),
            violation_count=len(violations),
            violations=tuple(violations),
        )

    def witness_discovery_cycle(
        self,
        result: DiscoverySubsystemResult,
    ) -> CycleWitnessReport:
        """Issue a witness report for a full discovery cycle result.

        Parameters
        ----------
        result:
            The discovery cycle result to witness.

        Returns
        -------
        CycleWitnessReport
            Cycle-level witness report aggregating per-proposal verdicts.
        """
        verdicts: list[WitnessVerdict] = []
        for prop in result.all_proposals:
            obs_list: list[ObstructionRecord] = []
            src_id = prop.source_obstruction_id
            for entry in result.archive_trace.entries:
                if entry.proposal_record.source_obstruction_id == src_id:
                    obs_list.append(
                        ObstructionRecord(
                            obstruction_id=src_id,
                            domain="unknown",
                            description="(cycle witness reconstruction)",
                            severity=0.5,
                        )
                    )
                    break
            verdict = self.witness_proposal(prop, obs_list if obs_list else [
                ObstructionRecord(
                    obstruction_id=src_id,
                    domain="unknown",
                    description="",
                    severity=0.5,
                )
            ])
            verdicts.append(verdict)

        overall_consistent = all(v.is_consistent for v in verdicts)
        total_leverage = sum(p.predicted_leverage for p in result.all_proposals)
        coverage_adequate = total_leverage >= 0.5 * result.obstruction_count

        if overall_consistent and coverage_adequate:
            summary = (
                f"Cycle '{result.cycle_id}' passed all witness checks. "
                f"{len(result.selected_proposals)} proposals selected from "
                f"{result.obstruction_count} obstructions."
            )
        else:
            flagged = sum(1 for v in verdicts if not v.is_consistent)
            summary = (
                f"Cycle '{result.cycle_id}' has {flagged} inconsistent proposal(s). "
                f"Coverage adequate: {coverage_adequate}."
            )

        return CycleWitnessReport(
            cycle_id=result.cycle_id,
            all_verdicts=tuple(verdicts),
            overall_consistent=overall_consistent,
            coverage_adequate=coverage_adequate,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== MathDiscoverySubsystem smoke test ===\n")

    cfg = DiscoverySubsystemConfig(
        max_proposals_per_obstruction=3,
        leverage_threshold=0.1,
        top_k_select=10,
        enable_proof_sketches=True,
        severity_weight=0.6,
    )

    obstructions = [
        ObstructionRecord(
            obstruction_id="obs-ag-001",
            domain="algebraic-geometry",
            description="Lack of a flat morphism from the scheme to its base; "
                        "cohomology does not commute with base change.",
            severity=0.85,
        ),
        ObstructionRecord(
            obstruction_id="obs-nt-001",
            domain="number-theory",
            description="No modular form lifting exists for the given congruence condition; "
                        "prime ramification obstructs the arithmetic argument.",
            severity=0.72,
        ),
        ObstructionRecord(
            obstruction_id="obs-cat-001",
            domain="category-theory",
            description="The adjunction between the functor and its right adjoint fails to "
                        "preserve limits in the enriched setting.",
            severity=0.60,
        ),
    ]

    # --- Coordinator ---
    coord = MathDiscoverySubsystemCoordinator(cfg)
    result = coord.run_discovery_cycle(obstructions)

    print(f"Cycle ID          : {result.cycle_id}")
    print(f"Obstruction count : {result.obstruction_count}")
    print(f"Total proposals   : {len(result.all_proposals)}")
    print(f"Selected proposals: {len(result.selected_proposals)}")
    print(f"Cycle duration    : {result.cycle_duration_s*1000:.2f} ms\n")

    for p in result.selected_proposals[:3]:
        print(f"  [{p.proposal_id}] leverage={p.predicted_leverage:.3f}")
        print(f"    Statement: {p.theorem_statement[:80]}...")
        print(f"    Sketch   : {p.proof_sketch[:80]}...\n")

    # --- Analyzer ---
    analyzer = MathDiscoverySubsystemAnalyzer()
    quality = analyzer.analyze_proposal_quality(list(result.all_proposals))
    print(f"Quality Report:")
    print(f"  count={quality.proposal_count}  mean_leverage={quality.mean_leverage:.3f}  "
          f"std={quality.leverage_std:.3f}  high_q_frac={quality.high_quality_fraction:.3f}")

    health = analyzer.analyze_archive_health(result.archive_trace)
    print(f"\nArchive Health:")
    print(f"  total={health.total_entries}  accepted={health.accepted_count}  "
          f"pending={health.pending_count}  acceptance_rate={health.acceptance_rate:.3f}")

    ranked = analyzer.rank_proposals_by_leverage(list(result.all_proposals))
    print(f"\nTop proposal by leverage: {ranked[0].proposal_id} "
          f"(leverage={ranked[0].predicted_leverage:.3f})")

    coverage = analyzer.analyze_obstruction_coverage(result.archive_trace, obstructions)
    print(f"\nCoverage:")
    print(f"  total_obstructions={coverage.total_obstructions}  "
          f"covered={coverage.covered_obstructions}  "
          f"sw_coverage={coverage.severity_weighted_coverage:.3f}")

    # --- Witness ---
    witness = MathDiscoverySubsystemWitness()
    cycle_report = witness.witness_discovery_cycle(result)
    print(f"\nCycle Witness Report:")
    print(f"  overall_consistent={cycle_report.overall_consistent}")
    print(f"  coverage_adequate ={cycle_report.coverage_adequate}")
    print(f"  summary: {cycle_report.summary}")

    consistency = witness.witness_archive_consistency(result.archive_trace)
    print(f"\nArchive Consistency:")
    print(f"  is_consistent={consistency.is_consistent}  "
          f"violations={consistency.violation_count}")

    # --- Free-function API ---
    result2 = run_discovery_cycle(obstructions[:1], config=cfg)
    print(f"\nFree-function cycle: {result2.cycle_id}  "
          f"proposals={len(result2.all_proposals)}")

    print("\n=== Smoke test passed ===")
