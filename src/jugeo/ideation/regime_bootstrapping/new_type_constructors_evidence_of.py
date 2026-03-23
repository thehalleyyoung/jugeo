"""
New type constructors: evidence of structural need — theory2.tex Ch59.

# copilot: shared-core marker

Theory reference: theory2.tex Ch59 — Regime Bootstrapping via New Type Constructor
Discovery.  This module implements the second stage of the regime bootstrapping
pipeline: given a corpus of theorem sketches produced by (or harvested for) a
developing mathematical regime, we detect ad hoc structural patterns that recur
across multiple theorems and therefore warrant promotion to first-class type
constructors.

Background
----------
A *new type constructor* is introduced when existing type-level abstractions
cannot represent a new mathematical pattern without repeated ad hoc constructions.
In other words, if every theorem in a family requires a structurally identical
sub-expression that does not yet have a name in the type theory, that shared
structure is evidence of a missing constructor.

Evidence of structural need
---------------------------
The central criterion is *frequency*:

    If k ≥ min_pattern_occurrences theorems each independently use the same
    structural template T, and the proportion k/N ≥ min_frequency_tier (as a
    fraction of the full theorem set of size N), then T is promoted to a
    TypeConstructorProposal.

The frequency is bucketed into PatternFrequency tiers so that downstream
consumers can apply coarse priority without re-computing ratios.

What "ad hoc" means mathematically
-----------------------------------
A construction is *ad hoc* if it appears inline — written out in full each time
it is needed — rather than being named and axiomatised.  For example, suppose
every theorem about dependent products must write out the full Church-encoding of
Σ-types inline; then Σ is an ad hoc pattern and should be promoted to a type
constructor.  The detector in this module uses a template abstraction: every free
term sub-expression is replaced by a placeholder variable, and the resulting
skeleton is the template.  Two occurrences that share a skeleton are considered
instances of the same ad hoc pattern.

TypeConstructorProposal
-----------------------
Each proposal carries:
  * ``constructor_name``  — a human-readable identifier derived from the template
  * ``arity``             — how many type-level arguments the constructor takes
  * ``kind_signature``    — the kind-theoretic type, e.g. ``* -> * -> *``
  * ``motivating_theorem_ids``  — the theorem IDs whose patterns motivated this
  * ``expected_axioms``   — sketch axioms that the constructor should satisfy
  * ``rationale``         — prose explanation for human review

Pattern detection pipeline
--------------------------
1. ``detect_ad_hoc_patterns`` tokenises each theorem statement and computes its
   structural template via ``_structural_template``.
2. Templates are counted across the full theorem set.
3. Templates meeting the frequency threshold are wrapped in ``AdHocPattern``.
4. ``propose_type_constructor`` maps each ``AdHocPattern`` to a
   ``TypeConstructorProposal``, inferring arity and kind signature.
5. ``validate_type_constructor`` checks kind consistency and coverage.
6. ``register_type_constructor`` creates a ``TypeConstructorRecord``.

Frequency scoring formula
--------------------------
Given a pattern p appearing in k theorems out of N total:

    raw_score(p) = k / N

    promoted_score(p) = raw_score(p) * log(1 + k)

The logarithmic factor rewards patterns that appear in many theorems
absolutely, even if the relative frequency is modest.

Typical usage
-------------
::

    from jugeo.ideation.regime_bootstrapping.new_type_constructors_evidence_of import (
        run_constructor_mining_cycle, TheoremSketch, TypeConstructorConfig,
    )

    theorems = [
        TheoremSketch(
            theorem_id="thm_001",
            statement="forall A B, Prod(A, B) -> Sum(A, B)",
            hypotheses=("A : Type", "B : Type"),
            conclusion="Sum(A, B)",
            tags=("algebra", "product"),
        ),
    ]
    result = run_constructor_mining_cycle(theorems)
    print(result)

Design notes
------------
* All cross-module imports are guarded in ``try/except Exception: pass`` blocks
  so the module can be exercised in isolation during testing.
* The module does *not* mutate any shared global state; all functions and methods
  are side-effect-free with respect to external systems (logging excepted).
* ``TypeConstructorConfig`` is a frozen dataclass: create a new instance to
  change configuration rather than mutating an existing one.
* Witness objects are intentionally lightweight — they record what happened in a
  mining cycle so that audit trails can be constructed without loading the full
  evidence manifest infrastructure.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
MIN_OCCURRENCES_FOR_PROMOTION: int = 3
"""Minimum number of theorems that must contain a pattern before it is a
promotion candidate.  Matches the default in TypeConstructorConfig."""

TEMPLATE_VAR_PLACEHOLDER: str = "_VAR_"
"""String used to replace free term variables when computing structural
templates from theorem statements."""

DEFAULT_MAX_CONSTRUCTORS_PER_CYCLE: int = 8
"""Default cap on how many new type constructors may be proposed in one
mining cycle, to avoid blowing up the downstream axiom-checking pipeline."""

DEDUP_SIMILARITY_THRESHOLD: float = 0.85
"""Jaccard-similarity threshold above which two templates are considered
duplicates and merged into a single pattern."""

COVERAGE_SUFFICIENCY_THRESHOLD: float = 0.60
"""Fraction of motivating theorems that a proposed constructor must cover
for its ``CoverageAnalysis.is_sufficient`` flag to be set to True."""

KIND_STAR: str = "*"
"""Base kind for concrete types."""

KIND_ARROW: str = "->"
"""Kind-level function arrow."""

MAX_AXIOM_SKETCHES: int = 6
"""Maximum number of axiom sketches generated per constructor proposal."""

FREQUENCY_OCCASIONAL_LOWER: float = 0.05
"""Lower bound of the OCCASIONAL frequency tier (inclusive)."""

FREQUENCY_FREQUENT_LOWER: float = 0.20
"""Lower bound of the FREQUENT frequency tier (inclusive)."""

FREQUENCY_UBIQUITOUS_LOWER: float = 0.50
"""Lower bound of the UBIQUITOUS frequency tier (inclusive)."""

# ---------------------------------------------------------------------------
# Public API list
# ---------------------------------------------------------------------------
__all__ = [
    "PatternFrequency",
    "ConstructorArity",
    "TypeConstructorConfig",
    "TheoremSketch",
    "AdHocPattern",
    "TypeConstructorProposal",
    "ConstructorValidationResult",
    "TypeConstructorRecord",
    "ConstructorMiningResult",
    "PatternFrequencyReport",
    "CoverageAnalysis",
    "KindConsistencyReport",
    "PatternWitnessReport",
    "ConstructorWitnessReport",
    "RegistrationWitnessReport",
    "NewTypeConstructorsCoordinator",
    "NewTypeConstructorsAnalyzer",
    "NewTypeConstructorsWitness",
    "run_constructor_mining_cycle",
    "score_ad_hoc_pattern",
    "select_patterns_for_promotion",
]

# ---------------------------------------------------------------------------
# Cross-module imports — always guarded
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
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField,
        ObstructionKind,
        DomainFormation,
        DomainType,
        TypeConstructor,
        TypeConstructorKind,
        BootstrapStatus,
        BootstrapPriority,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PatternFrequency(Enum):
    """Frequency tier describing how often an ad hoc pattern appears in the
    theorem corpus."""

    RARE = auto()        # appears in < 5% of theorem set
    OCCASIONAL = auto()  # 5–20%
    FREQUENT = auto()    # 20–50%
    UBIQUITOUS = auto()  # > 50%


class ConstructorArity(Enum):
    """The arity (number of type-level arguments) of a proposed type
    constructor."""

    NULLARY = auto()   # 0-ary: type constant
    UNARY = auto()     # 1-ary: type-level function of one argument
    BINARY = auto()    # 2-ary: type-level function of two arguments
    TERNARY = auto()   # 3-ary
    VARIADIC = auto()  # variable arity


# ---------------------------------------------------------------------------
# Configuration & value dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeConstructorConfig:
    """Immutable configuration for a constructor-mining run.

    All thresholds are intentionally conservative so that the pipeline
    produces a small number of high-quality proposals rather than a large
    number of speculative ones.
    """

    min_pattern_occurrences: int = MIN_OCCURRENCES_FOR_PROMOTION
    min_frequency_tier: PatternFrequency = PatternFrequency.OCCASIONAL
    max_constructors_per_cycle: int = DEFAULT_MAX_CONSTRUCTORS_PER_CYCLE
    kind_check_enabled: bool = True
    dedup_threshold: float = DEDUP_SIMILARITY_THRESHOLD
    coverage_threshold: float = COVERAGE_SUFFICIENCY_THRESHOLD


@dataclass(frozen=True, slots=True)
class TheoremSketch:
    """A lightweight representation of a theorem sufficient for pattern
    mining.

    The ``statement`` field is the full mathematical statement as a string.
    ``hypotheses`` and the ``conclusion`` are kept separate so that the
    pattern detector can weight them differently if needed.
    """

    theorem_id: str
    statement: str
    hypotheses: tuple[str, ...]
    conclusion: str
    tags: tuple[str, ...]
    domain_hint: str = ""


@dataclass(frozen=True, slots=True)
class AdHocPattern:
    """A structural pattern detected across multiple theorem statements.

    ``template`` is the normalised skeleton of the pattern (free variables
    replaced by ``TEMPLATE_VAR_PLACEHOLDER``).  ``occurrence_count`` is the
    number of distinct theorems in which the pattern was observed.
    ``theorem_ids`` lists those theorems.
    """

    pattern_id: str
    template: str
    occurrence_count: int
    theorem_ids: tuple[str, ...]
    frequency: PatternFrequency
    suggested_arity: ConstructorArity
    kind_signature: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TypeConstructorProposal:
    """A concrete proposal to introduce a new type constructor.

    This object is the primary output of the pattern-to-proposal mapping.
    It carries enough information for a human or downstream automated checker
    to decide whether to accept or reject the proposed constructor.
    """

    proposal_id: str
    pattern_id: str
    constructor_name: str
    arity: ConstructorArity
    kind_signature: str
    motivating_theorem_ids: tuple[str, ...]
    expected_axioms: tuple[str, ...]
    rationale: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ConstructorValidationResult:
    """Result of validating a TypeConstructorProposal.

    ``coverage_score`` is the fraction of motivating theorems that the
    proposed constructor actually covers (as determined by the analyzer).
    """

    proposal_id: str
    is_valid: bool
    kind_consistent: bool
    coverage_score: float
    issues: tuple[str, ...]
    suggestions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TypeConstructorRecord:
    """A registered type constructor that has passed validation.

    Once a proposal is registered, it is represented by a
    ``TypeConstructorRecord`` which is the canonical reference for the new
    constructor throughout the rest of the bootstrapping pipeline.
    """

    record_id: str
    constructor_name: str
    arity: ConstructorArity
    kind_signature: str
    registered_at: str
    source_proposal_id: str


@dataclass(frozen=True, slots=True)
class ConstructorMiningResult:
    """Summary of a complete constructor-mining cycle.

    Returned by ``NewTypeConstructorsCoordinator.run_constructor_mining_cycle``
    and the module-level ``run_constructor_mining_cycle`` wrapper.
    """

    cycle_id: str
    theorems_analyzed: int
    patterns_found: int
    constructors_proposed: int
    constructors_registered: int
    record_ids: tuple[str, ...]
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class PatternFrequencyReport:
    """Aggregated frequency statistics over a collection of AdHocPatterns.

    ``frequency_distribution`` maps PatternFrequency.name to the count of
    patterns in that tier.  ``top_patterns`` lists the pattern_ids of the
    most frequent patterns (by occurrence_count).
    ``promotion_candidates`` lists pattern_ids that exceed the promotion
    threshold.
    """

    total_patterns: int
    frequency_distribution: Dict[str, int] = field(default_factory=dict)
    top_patterns: tuple[str, ...] = ()
    promotion_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageAnalysis:
    """Coverage analysis for a single TypeConstructorProposal.

    Measures what fraction of the motivating theorems are actually
    "covered" by the proposed constructor — i.e., would benefit from it.
    """

    proposal_id: str
    covered_theorem_ids: tuple[str, ...]
    uncovered_theorem_ids: tuple[str, ...]
    coverage_fraction: float
    is_sufficient: bool


@dataclass(frozen=True, slots=True)
class KindConsistencyReport:
    """Report on whether a proposal's kind signature is internally consistent.

    ``inferred_kind`` is what the detector computed from the arity;
    ``expected_kind`` is what the proposal claims.  If they differ,
    ``is_consistent`` is False and ``inconsistencies`` lists the
    discrepancies.
    """

    proposal_id: str
    is_consistent: bool
    inferred_kind: str
    expected_kind: str
    inconsistencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatternWitnessReport:
    """Lightweight audit record for a pattern-detection step."""

    witness_id: str
    theorem_count: int
    pattern_count: int
    frequency_summary: Dict[str, int] = field(default_factory=dict)
    timestamp: str = ""


@dataclass(frozen=True, slots=True)
class ConstructorWitnessReport:
    """Lightweight audit record for a constructor-proposal step."""

    witness_id: str
    proposal_id: str
    constructor_name: str
    arity_name: str
    is_valid: bool
    timestamp: str = ""


@dataclass(frozen=True, slots=True)
class RegistrationWitnessReport:
    """Lightweight audit record for a constructor-registration step."""

    witness_id: str
    record_id: str
    constructor_name: str
    registered_at: str
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _tokenize_statement(stmt: str) -> list[str]:
    """Tokenise a theorem statement into a list of lowercase tokens.

    Splits on whitespace and common punctuation.  Filters out empty tokens.

    Parameters
    ----------
    stmt:
        Raw theorem statement string.

    Returns
    -------
    list[str]
        List of normalised tokens.
    """
    import re
    # Replace common mathematical punctuation with spaces
    cleaned = re.sub(r"[(),.:;→⊢⊗⊕∀∃λΠΣ]", " ", stmt)
    tokens = [t.strip().lower() for t in cleaned.split()]
    return [t for t in tokens if t]


def _structural_template(stmt: str) -> str:
    """Compute the structural template of a theorem statement.

    Replaces all tokens that look like variable names or numerals with
    ``TEMPLATE_VAR_PLACEHOLDER``, preserving recognised keywords (logical
    connectives, type-former names, etc.) as-is.

    Parameters
    ----------
    stmt:
        Raw theorem statement string.

    Returns
    -------
    str
        Normalised template string suitable for equality comparison.
    """
    import re

    # Keywords that are structural, not variable
    STRUCTURAL_KEYWORDS = frozenset({
        "forall", "exists", "lambda", "fun", "let", "in", "type",
        "prop", "sort", "set", "match", "with", "end", "if", "then",
        "else", "return", "fix", "cofix", "def", "theorem", "lemma",
        "axiom", "hypothesis", "proof", "qed", "prod", "sum", "sigma",
        "pi", "nat", "bool", "list", "option", "unit", "empty", "eq",
        "and", "or", "not", "true", "false", "intro", "apply", "exact",
        "rewrite", "induction", "destruct", "case", "assert",
    })

    tokens = _tokenize_statement(stmt)
    template_tokens: list[str] = []
    for tok in tokens:
        if tok in STRUCTURAL_KEYWORDS:
            template_tokens.append(tok)
        elif re.fullmatch(r"[0-9]+", tok):
            template_tokens.append(TEMPLATE_VAR_PLACEHOLDER)
        elif re.fullmatch(r"[a-z][a-z0-9_']*", tok) and len(tok) <= 3:
            # Short identifiers are likely variable names
            template_tokens.append(TEMPLATE_VAR_PLACEHOLDER)
        else:
            template_tokens.append(tok)
    return " ".join(template_tokens)


def _frequency_tier(occurrence_count: int, total_theorems: int) -> PatternFrequency:
    """Map a raw occurrence count to a PatternFrequency tier.

    Parameters
    ----------
    occurrence_count:
        Number of theorems in which the pattern was observed.
    total_theorems:
        Size of the full theorem set.

    Returns
    -------
    PatternFrequency
        The appropriate frequency tier.
    """
    if total_theorems <= 0:
        return PatternFrequency.RARE
    ratio = occurrence_count / total_theorems
    if ratio >= FREQUENCY_UBIQUITOUS_LOWER:
        return PatternFrequency.UBIQUITOUS
    if ratio >= FREQUENCY_FREQUENT_LOWER:
        return PatternFrequency.FREQUENT
    if ratio >= FREQUENCY_OCCASIONAL_LOWER:
        return PatternFrequency.OCCASIONAL
    return PatternFrequency.RARE


def _arity_from_template(template: str) -> ConstructorArity:
    """Estimate the arity of a type constructor from a pattern template.

    Heuristic: count how many times ``TEMPLATE_VAR_PLACEHOLDER`` appears in
    the template as a proxy for the number of type-level arguments.

    Parameters
    ----------
    template:
        Structural template string.

    Returns
    -------
    ConstructorArity
        Estimated arity.
    """
    count = template.count(TEMPLATE_VAR_PLACEHOLDER)
    if count == 0:
        return ConstructorArity.NULLARY
    if count == 1:
        return ConstructorArity.UNARY
    if count == 2:
        return ConstructorArity.BINARY
    if count == 3:
        return ConstructorArity.TERNARY
    return ConstructorArity.VARIADIC


def _kind_signature_for_arity(arity: ConstructorArity) -> str:
    """Return the kind signature string for a given arity.

    Uses the convention that ``*`` is the base kind for concrete types and
    ``->`` is the kind-level function arrow.

    Parameters
    ----------
    arity:
        The constructor arity.

    Returns
    -------
    str
        Kind signature, e.g. ``"* -> * -> *"`` for BINARY.
    """
    if arity == ConstructorArity.NULLARY:
        return KIND_STAR
    if arity == ConstructorArity.UNARY:
        return f"{KIND_STAR} {KIND_ARROW} {KIND_STAR}"
    if arity == ConstructorArity.BINARY:
        return f"{KIND_STAR} {KIND_ARROW} {KIND_STAR} {KIND_ARROW} {KIND_STAR}"
    if arity == ConstructorArity.TERNARY:
        return (
            f"{KIND_STAR} {KIND_ARROW} {KIND_STAR} {KIND_ARROW} "
            f"{KIND_STAR} {KIND_ARROW} {KIND_STAR}"
        )
    # VARIADIC: use a schematic notation
    return f"{KIND_STAR}... {KIND_ARROW} {KIND_STAR}"


def _constructor_name_from_template(template: str) -> str:
    """Derive a candidate constructor name from a structural template.

    Extracts the first structural keyword present in the template and
    capitalises it.  Falls back to a hash-based name if no keyword is
    found.

    Parameters
    ----------
    template:
        Structural template string.

    Returns
    -------
    str
        A human-readable candidate name, e.g. ``"Prod"`` or ``"Tc3a9f"``.
    """
    NAME_WORTHY_KEYWORDS = [
        "prod", "sum", "sigma", "pi", "list", "option", "eq",
        "nat", "bool", "fix", "cofix",
    ]
    tokens = template.split()
    for tok in tokens:
        if tok in NAME_WORTHY_KEYWORDS:
            return tok.capitalize()
    # Fall back: short hash
    digest = hashlib.sha1(template.encode()).hexdigest()[:5]
    return f"Tc{digest}"


def _generate_axiom_sketches(name: str, arity: ConstructorArity) -> list[str]:
    """Generate a list of axiom sketch strings for a new type constructor.

    The sketches are not full formal axioms; they are prose/template
    statements that a proof engineer can formalise.

    Parameters
    ----------
    name:
        Constructor name, e.g. ``"Prod"``.
    arity:
        The constructor's arity.

    Returns
    -------
    list[str]
        Up to ``MAX_AXIOM_SKETCHES`` axiom sketch strings.
    """
    axioms: list[str] = []
    axioms.append(f"{name}_intro: introduction rule for {name}.")
    axioms.append(f"{name}_elim: elimination rule for {name}.")
    axioms.append(f"{name}_beta: computation rule (β-reduction) for {name}.")
    axioms.append(f"{name}_eta: uniqueness principle (η-expansion) for {name}.")
    if arity in (ConstructorArity.BINARY, ConstructorArity.TERNARY, ConstructorArity.VARIADIC):
        axioms.append(f"{name}_assoc: associativity of {name} (if applicable).")
    if arity != ConstructorArity.NULLARY:
        axioms.append(f"{name}_functor: functoriality of {name} over its arguments.")
    return axioms[:MAX_AXIOM_SKETCHES]


def _build_pattern_id(template: str) -> str:
    """Build a deterministic pattern ID from a template string.

    Parameters
    ----------
    template:
        Structural template string.

    Returns
    -------
    str
        A short hex digest prefixed with ``"pat_"``.
    """
    digest = hashlib.sha256(template.encode()).hexdigest()[:12]
    return f"pat_{digest}"


def _build_proposal_id(pattern_id: str) -> str:
    """Build a deterministic proposal ID from a pattern ID.

    Parameters
    ----------
    pattern_id:
        The source pattern's ID.

    Returns
    -------
    str
        A short hex digest prefixed with ``"prop_"``.
    """
    seed = f"proposal::{pattern_id}"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"prop_{digest}"


def _build_record_id(proposal_id: str) -> str:
    """Build a deterministic record ID from a proposal ID.

    Parameters
    ----------
    proposal_id:
        The accepted proposal's ID.

    Returns
    -------
    str
        A short hex digest prefixed with ``"rec_"``.
    """
    seed = f"record::{proposal_id}"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"rec_{digest}"


# ---------------------------------------------------------------------------
# Core coordinator class
# ---------------------------------------------------------------------------


class NewTypeConstructorsCoordinator:
    """Coordinates the full pipeline for detecting and registering new type
    constructors from a corpus of theorem sketches.

    The coordinator is the primary entry point for the mining cycle.  All
    individual steps are available as public methods so that callers can
    invoke them piecemeal if needed.

    Parameters
    ----------
    config:
        Configuration object controlling thresholds and limits.

    Attributes
    ----------
    config:
        The configuration in use.
    _registered_records:
        Internal accumulator for TypeConstructorRecord objects produced
        during ``run_constructor_mining_cycle``.
    """

    def __init__(self, config: TypeConstructorConfig) -> None:
        self.config: TypeConstructorConfig = config
        self._registered_records: list[TypeConstructorRecord] = []

    # ------------------------------------------------------------------
    # Step 1: detect ad hoc patterns
    # ------------------------------------------------------------------

    def detect_ad_hoc_patterns(
        self, theorem_set: list[TheoremSketch]
    ) -> list[AdHocPattern]:
        """Detect ad hoc structural patterns across a theorem corpus.

        Tokenises each theorem statement, computes its structural template,
        then counts occurrences of each template.  Templates that appear in
        at least ``config.min_pattern_occurrences`` distinct theorems are
        returned as ``AdHocPattern`` objects.

        Duplicate patterns (templates with Jaccard similarity above
        ``config.dedup_threshold``) are merged: the higher-count template
        wins.

        Parameters
        ----------
        theorem_set:
            The full corpus of theorem sketches to mine.

        Returns
        -------
        list[AdHocPattern]
            Detected patterns sorted by occurrence count descending.
        """
        if not theorem_set:
            log.debug("detect_ad_hoc_patterns: empty theorem set, returning []")
            return []

        total = len(theorem_set)
        # Map template -> list of theorem_ids
        template_map: dict[str, list[str]] = defaultdict(list)

        for thm in theorem_set:
            combined = " ".join([thm.statement] + list(thm.hypotheses) + [thm.conclusion])
            tmpl = _structural_template(combined)
            if tmpl and tmpl not in ("", TEMPLATE_VAR_PLACEHOLDER):
                template_map[tmpl].append(thm.theorem_id)

        # Deduplicate similar templates using token-level Jaccard similarity
        templates = list(template_map.keys())
        merged: dict[str, str] = {}  # template -> canonical template
        for i, t1 in enumerate(templates):
            if t1 in merged:
                continue
            merged[t1] = t1
            set1 = set(t1.split())
            for t2 in templates[i + 1 :]:
                if t2 in merged:
                    continue
                set2 = set(t2.split())
                union = set1 | set2
                if not union:
                    continue
                jaccard = len(set1 & set2) / len(union)
                if jaccard >= self.config.dedup_threshold:
                    # Merge t2 into t1: accumulate theorem IDs under t1
                    template_map[t1].extend(template_map.pop(t2, []))
                    merged[t2] = t1

        now = datetime.now(timezone.utc).isoformat()
        patterns: list[AdHocPattern] = []

        for tmpl, thm_ids in template_map.items():
            # Deduplicate theorem IDs
            unique_ids = list(dict.fromkeys(thm_ids))
            count = len(unique_ids)
            if count < self.config.min_pattern_occurrences:
                continue

            tier = _frequency_tier(count, total)

            # Only promote if the tier meets the configured minimum
            tier_order = [
                PatternFrequency.RARE,
                PatternFrequency.OCCASIONAL,
                PatternFrequency.FREQUENT,
                PatternFrequency.UBIQUITOUS,
            ]
            if tier_order.index(tier) < tier_order.index(self.config.min_frequency_tier):
                continue

            arity = _arity_from_template(tmpl)
            kind_sig = _kind_signature_for_arity(arity)
            pat_id = _build_pattern_id(tmpl)

            patterns.append(
                AdHocPattern(
                    pattern_id=pat_id,
                    template=tmpl,
                    occurrence_count=count,
                    theorem_ids=tuple(unique_ids),
                    frequency=tier,
                    suggested_arity=arity,
                    kind_signature=kind_sig,
                    created_at=now,
                )
            )

        patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
        log.info("detect_ad_hoc_patterns: found %d qualifying patterns", len(patterns))
        return patterns

    # ------------------------------------------------------------------
    # Step 2: propose type constructor
    # ------------------------------------------------------------------

    def propose_type_constructor(self, pattern: AdHocPattern) -> TypeConstructorProposal:
        """Create a TypeConstructorProposal from an AdHocPattern.

        Derives the constructor name, kind signature, expected axioms, and
        a prose rationale from the pattern data.

        Parameters
        ----------
        pattern:
            The source ad hoc pattern.

        Returns
        -------
        TypeConstructorProposal
            A fully populated proposal object.
        """
        name = _constructor_name_from_template(pattern.template)
        kind_sig = _kind_signature_for_arity(pattern.suggested_arity)
        axioms = _generate_axiom_sketches(name, pattern.suggested_arity)
        proposal_id = _build_proposal_id(pattern.pattern_id)
        now = datetime.now(timezone.utc).isoformat()

        rationale = (
            f"Pattern '{pattern.pattern_id}' appears in {pattern.occurrence_count} theorems "
            f"({pattern.frequency.name} tier).  Template: «{pattern.template[:80]}».  "
            f"Promoting to constructor '{name}' with kind '{kind_sig}' reduces ad hoc "
            f"repetition and enables generic reasoning over this structural pattern."
        )

        log.debug("propose_type_constructor: proposal_id=%s name=%s", proposal_id, name)
        return TypeConstructorProposal(
            proposal_id=proposal_id,
            pattern_id=pattern.pattern_id,
            constructor_name=name,
            arity=pattern.suggested_arity,
            kind_signature=kind_sig,
            motivating_theorem_ids=pattern.theorem_ids,
            expected_axioms=tuple(axioms),
            rationale=rationale,
            created_at=now,
        )

    # ------------------------------------------------------------------
    # Step 3: validate type constructor
    # ------------------------------------------------------------------

    def validate_type_constructor(
        self, proposal: TypeConstructorProposal
    ) -> ConstructorValidationResult:
        """Validate a TypeConstructorProposal for kind consistency and coverage.

        Performs the following checks:
        1. The kind signature is syntactically well-formed (non-empty, contains
           the base kind ``*``).
        2. The arity is consistent with the kind signature (count of ``->``
           equals expected depth).
        3. At least one motivating theorem is listed.
        4. At least one expected axiom is listed.

        The ``coverage_score`` is set to 1.0 here because full theorem
        coverage analysis requires the original theorem set; use
        ``NewTypeConstructorsAnalyzer.analyze_constructor_coverage`` for an
        accurate score.

        Parameters
        ----------
        proposal:
            The proposal to validate.

        Returns
        -------
        ConstructorValidationResult
            Validation outcome.
        """
        issues: list[str] = []
        suggestions: list[str] = []

        # Check 1: kind signature well-formedness
        kind_consistent = True
        if KIND_STAR not in proposal.kind_signature:
            issues.append(f"Kind signature '{proposal.kind_signature}' does not contain '*'.")
            kind_consistent = False

        # Check 2: arity vs kind signature depth
        arrow_count = proposal.kind_signature.count(KIND_ARROW)
        expected_arrows = {
            ConstructorArity.NULLARY: 0,
            ConstructorArity.UNARY: 1,
            ConstructorArity.BINARY: 2,
            ConstructorArity.TERNARY: 3,
        }.get(proposal.arity, None)
        if expected_arrows is not None and arrow_count != expected_arrows:
            issues.append(
                f"Arity {proposal.arity.name} expects {expected_arrows} '->' in kind "
                f"signature but found {arrow_count}."
            )
            kind_consistent = False
        elif expected_arrows is None and proposal.arity == ConstructorArity.VARIADIC:
            # Variadic: just check at least one arrow
            if arrow_count == 0:
                suggestions.append(
                    "Variadic constructor kind should include at least one '->'."
                )

        # Check 3: motivating theorems
        if not proposal.motivating_theorem_ids:
            issues.append("No motivating theorem IDs listed.")

        # Check 4: axioms
        if not proposal.expected_axioms:
            suggestions.append("No axiom sketches generated; consider adding manually.")

        # Check 5: constructor name sanity
        if not proposal.constructor_name or len(proposal.constructor_name) < 2:
            issues.append("Constructor name is too short or empty.")

        is_valid = len(issues) == 0
        # Placeholder coverage score; real analysis requires the theorem corpus
        coverage_score = 1.0 if is_valid else 0.0

        log.debug(
            "validate_type_constructor: proposal_id=%s is_valid=%s",
            proposal.proposal_id,
            is_valid,
        )
        return ConstructorValidationResult(
            proposal_id=proposal.proposal_id,
            is_valid=is_valid,
            kind_consistent=kind_consistent,
            coverage_score=coverage_score,
            issues=tuple(issues),
            suggestions=tuple(suggestions),
        )

    # ------------------------------------------------------------------
    # Step 4: register type constructor
    # ------------------------------------------------------------------

    def register_type_constructor(
        self, proposal: TypeConstructorProposal
    ) -> TypeConstructorRecord:
        """Create and store a TypeConstructorRecord for an accepted proposal.

        Parameters
        ----------
        proposal:
            A validated proposal to register.

        Returns
        -------
        TypeConstructorRecord
            The new registration record.
        """
        now = datetime.now(timezone.utc).isoformat()
        record_id = _build_record_id(proposal.proposal_id)
        record = TypeConstructorRecord(
            record_id=record_id,
            constructor_name=proposal.constructor_name,
            arity=proposal.arity,
            kind_signature=proposal.kind_signature,
            registered_at=now,
            source_proposal_id=proposal.proposal_id,
        )
        self._registered_records.append(record)
        log.info(
            "register_type_constructor: registered '%s' as %s",
            record.constructor_name,
            record.record_id,
        )
        return record

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_constructor_mining_cycle(
        self, theorems: list[TheoremSketch]
    ) -> ConstructorMiningResult:
        """Execute the full constructor-mining pipeline.

        Steps:
        1. Detect ad hoc patterns.
        2. For each pattern (up to ``config.max_constructors_per_cycle``),
           propose a type constructor.
        3. Validate each proposal.
        4. Register proposals that pass validation.

        Parameters
        ----------
        theorems:
            Theorem corpus to mine.

        Returns
        -------
        ConstructorMiningResult
            Summary of the cycle.
        """
        import time

        cycle_id = f"cycle_{uuid.uuid4().hex[:8]}"
        start = time.monotonic()

        log.info("run_constructor_mining_cycle: start cycle_id=%s theorems=%d", cycle_id, len(theorems))

        patterns = self.detect_ad_hoc_patterns(theorems)
        capped_patterns = patterns[: self.config.max_constructors_per_cycle]

        record_ids: list[str] = []
        proposals_count = 0
        registered_count = 0

        for pat in capped_patterns:
            proposal = self.propose_type_constructor(pat)
            proposals_count += 1
            validation = self.validate_type_constructor(proposal)
            if validation.is_valid:
                record = self.register_type_constructor(proposal)
                record_ids.append(record.record_id)
                registered_count += 1

        duration = time.monotonic() - start
        result = ConstructorMiningResult(
            cycle_id=cycle_id,
            theorems_analyzed=len(theorems),
            patterns_found=len(patterns),
            constructors_proposed=proposals_count,
            constructors_registered=registered_count,
            record_ids=tuple(record_ids),
            duration_seconds=round(duration, 4),
        )
        log.info(
            "run_constructor_mining_cycle: done cycle_id=%s registered=%d duration=%.4fs",
            cycle_id,
            registered_count,
            duration,
        )
        return result


# ---------------------------------------------------------------------------
# Analyzer class
# ---------------------------------------------------------------------------


class NewTypeConstructorsAnalyzer:
    """Provides analytical queries over ad hoc patterns and constructor
    proposals.

    This class is stateless: all methods are pure functions of their
    arguments.  It exists as a class rather than a collection of module-level
    functions so that it can be injected as a dependency and mocked in tests.
    """

    def analyze_pattern_frequency(
        self, patterns: list[AdHocPattern]
    ) -> PatternFrequencyReport:
        """Compute aggregated frequency statistics over a list of patterns.

        Builds a distribution over ``PatternFrequency`` tiers and identifies
        the top-5 patterns by occurrence count and the promotion candidates
        (patterns whose tier is FREQUENT or UBIQUITOUS).

        Parameters
        ----------
        patterns:
            List of ad hoc patterns to analyse.

        Returns
        -------
        PatternFrequencyReport
            Aggregated statistics.
        """
        if not patterns:
            return PatternFrequencyReport(
                total_patterns=0,
                frequency_distribution={tier.name: 0 for tier in PatternFrequency},
                top_patterns=(),
                promotion_candidates=(),
            )

        distribution: dict[str, int] = {tier.name: 0 for tier in PatternFrequency}
        for pat in patterns:
            distribution[pat.frequency.name] += 1

        sorted_pats = sorted(patterns, key=lambda p: p.occurrence_count, reverse=True)
        top_5 = tuple(p.pattern_id for p in sorted_pats[:5])

        promotion_tiers = {PatternFrequency.FREQUENT, PatternFrequency.UBIQUITOUS}
        candidates = tuple(
            p.pattern_id for p in sorted_pats if p.frequency in promotion_tiers
        )

        return PatternFrequencyReport(
            total_patterns=len(patterns),
            frequency_distribution=distribution,
            top_patterns=top_5,
            promotion_candidates=candidates,
        )

    def analyze_constructor_coverage(
        self,
        proposal: TypeConstructorProposal,
        theorems: list[TheoremSketch],
    ) -> CoverageAnalysis:
        """Determine which theorems in the corpus are covered by a proposal.

        A theorem is *covered* if its ID appears in the proposal's
        ``motivating_theorem_ids``.  This is the recall-based notion of
        coverage; a more precise analysis would re-run pattern detection on
        each theorem individually, but that is deferred to a later pipeline
        stage.

        Parameters
        ----------
        proposal:
            The proposal whose coverage to assess.
        theorems:
            The full theorem corpus.

        Returns
        -------
        CoverageAnalysis
            Coverage metrics.
        """
        if not theorems:
            return CoverageAnalysis(
                proposal_id=proposal.proposal_id,
                covered_theorem_ids=(),
                uncovered_theorem_ids=(),
                coverage_fraction=0.0,
                is_sufficient=False,
            )

        motivating_set = set(proposal.motivating_theorem_ids)
        all_ids = [t.theorem_id for t in theorems]
        covered = tuple(tid for tid in all_ids if tid in motivating_set)
        uncovered = tuple(tid for tid in all_ids if tid not in motivating_set)
        fraction = len(covered) / len(all_ids)

        return CoverageAnalysis(
            proposal_id=proposal.proposal_id,
            covered_theorem_ids=covered,
            uncovered_theorem_ids=uncovered,
            coverage_fraction=round(fraction, 4),
            is_sufficient=fraction >= COVERAGE_SUFFICIENCY_THRESHOLD,
        )

    def analyze_kind_consistency(
        self, proposal: TypeConstructorProposal
    ) -> KindConsistencyReport:
        """Verify that a proposal's kind signature is consistent with its arity.

        Infers what the kind signature *should* be from the declared arity
        and compares it to what the proposal actually claims.

        Parameters
        ----------
        proposal:
            The proposal to check.

        Returns
        -------
        KindConsistencyReport
            Consistency report.
        """
        inferred = _kind_signature_for_arity(proposal.arity)
        expected = proposal.kind_signature
        inconsistencies: list[str] = []

        if inferred != expected:
            inconsistencies.append(
                f"Inferred kind '{inferred}' differs from declared kind '{expected}'."
            )

        return KindConsistencyReport(
            proposal_id=proposal.proposal_id,
            is_consistent=len(inconsistencies) == 0,
            inferred_kind=inferred,
            expected_kind=expected,
            inconsistencies=tuple(inconsistencies),
        )

    def rank_patterns_by_frequency(
        self, patterns: list[AdHocPattern]
    ) -> list[AdHocPattern]:
        """Sort patterns by occurrence count descending.

        Ties are broken by ``pattern_id`` lexicographically so that the
        ordering is deterministic.

        Parameters
        ----------
        patterns:
            Patterns to rank.

        Returns
        -------
        list[AdHocPattern]
            Sorted copy of the input.
        """
        return sorted(
            patterns,
            key=lambda p: (-p.occurrence_count, p.pattern_id),
        )


# ---------------------------------------------------------------------------
# Witness class
# ---------------------------------------------------------------------------


class NewTypeConstructorsWitness:
    """Produces lightweight audit / witness records for mining cycle events.

    Witness objects are useful for building an audit trail without loading
    the full evidence manifest infrastructure.  They are intentionally
    simple: each ``witness_*`` method creates and returns a report dataclass
    without side effects.
    """

    def witness_pattern_detection(
        self,
        theorems: list[TheoremSketch],
        patterns: list[AdHocPattern],
    ) -> PatternWitnessReport:
        """Create a witness report for a pattern-detection step.

        Parameters
        ----------
        theorems:
            The theorem corpus that was mined.
        patterns:
            The patterns detected.

        Returns
        -------
        PatternWitnessReport
            Audit record.
        """
        freq_summary: dict[str, int] = {tier.name: 0 for tier in PatternFrequency}
        for pat in patterns:
            freq_summary[pat.frequency.name] += 1

        return PatternWitnessReport(
            witness_id=f"wit_pat_{uuid.uuid4().hex[:8]}",
            theorem_count=len(theorems),
            pattern_count=len(patterns),
            frequency_summary=freq_summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def witness_constructor_proposal(
        self, proposal: TypeConstructorProposal
    ) -> ConstructorWitnessReport:
        """Create a witness report for a constructor-proposal step.

        Parameters
        ----------
        proposal:
            The proposal that was created.

        Returns
        -------
        ConstructorWitnessReport
            Audit record.
        """
        # Quick inline validation to populate is_valid
        coord = NewTypeConstructorsCoordinator(TypeConstructorConfig())
        validation = coord.validate_type_constructor(proposal)

        return ConstructorWitnessReport(
            witness_id=f"wit_prop_{uuid.uuid4().hex[:8]}",
            proposal_id=proposal.proposal_id,
            constructor_name=proposal.constructor_name,
            arity_name=proposal.arity.name,
            is_valid=validation.is_valid,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def witness_constructor_registration(
        self, record: TypeConstructorRecord
    ) -> RegistrationWitnessReport:
        """Create a witness report for a constructor-registration step.

        Parameters
        ----------
        record:
            The registration record that was created.

        Returns
        -------
        RegistrationWitnessReport
            Audit record.
        """
        return RegistrationWitnessReport(
            witness_id=f"wit_reg_{uuid.uuid4().hex[:8]}",
            record_id=record.record_id,
            constructor_name=record.constructor_name,
            registered_at=record.registered_at,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Module-level free functions
# ---------------------------------------------------------------------------


def run_constructor_mining_cycle(
    theorems: list[TheoremSketch],
    config: Optional[TypeConstructorConfig] = None,
) -> ConstructorMiningResult:
    """Module-level wrapper around ``NewTypeConstructorsCoordinator.run_constructor_mining_cycle``.

    Creates a fresh coordinator with the supplied (or default) config and
    runs a single mining cycle over the provided theorem corpus.

    Parameters
    ----------
    theorems:
        Theorem corpus to mine.
    config:
        Optional configuration; defaults to ``TypeConstructorConfig()`` if
        not provided.

    Returns
    -------
    ConstructorMiningResult
        Summary of the mining cycle.

    Examples
    --------
    ::

        result = run_constructor_mining_cycle(my_theorems)
        print(result.constructors_registered)
    """
    cfg = config if config is not None else TypeConstructorConfig()
    coordinator = NewTypeConstructorsCoordinator(cfg)
    return coordinator.run_constructor_mining_cycle(theorems)


def score_ad_hoc_pattern(
    pattern: AdHocPattern,
    config: Optional[TypeConstructorConfig] = None,
) -> float:
    """Compute a promotion score for an ad hoc pattern.

    The score combines relative frequency (occurrence_count / max plausible
    count, capped at 1) with a logarithmic bonus for absolute occurrence
    count, as described in the module docstring::

        promoted_score(p) = raw_score(p) * log(1 + k)

    where ``k = occurrence_count`` and ``raw_score`` is approximated by the
    frequency tier ordinal normalised to [0, 1].

    Parameters
    ----------
    pattern:
        The pattern to score.
    config:
        Optional config (currently unused, reserved for threshold overrides).

    Returns
    -------
    float
        Promotion score in [0, ∞).  Higher is better.
    """
    tier_weights: dict[PatternFrequency, float] = {
        PatternFrequency.RARE: 0.02,
        PatternFrequency.OCCASIONAL: 0.12,
        PatternFrequency.FREQUENT: 0.35,
        PatternFrequency.UBIQUITOUS: 0.75,
    }
    raw_score = tier_weights.get(pattern.frequency, 0.02)
    log_bonus = math.log1p(pattern.occurrence_count)
    return round(raw_score * log_bonus, 6)


def select_patterns_for_promotion(
    patterns: list[AdHocPattern],
    config: Optional[TypeConstructorConfig] = None,
) -> list[AdHocPattern]:
    """Select patterns that meet the threshold for type constructor promotion.

    Applies both the occurrence-count threshold from ``config`` and the
    frequency-tier threshold, then ranks survivors by promotion score
    (descending) and caps the result at
    ``config.max_constructors_per_cycle``.

    Parameters
    ----------
    patterns:
        All detected ad hoc patterns.
    config:
        Optional configuration; defaults to ``TypeConstructorConfig()`` if
        not provided.

    Returns
    -------
    list[AdHocPattern]
        Patterns selected for promotion, ordered by score descending.
    """
    cfg = config if config is not None else TypeConstructorConfig()

    tier_order = [
        PatternFrequency.RARE,
        PatternFrequency.OCCASIONAL,
        PatternFrequency.FREQUENT,
        PatternFrequency.UBIQUITOUS,
    ]
    min_tier_idx = tier_order.index(cfg.min_frequency_tier)

    qualified = [
        p
        for p in patterns
        if p.occurrence_count >= cfg.min_pattern_occurrences
        and tier_order.index(p.frequency) >= min_tier_idx
    ]

    scored = sorted(qualified, key=lambda p: score_ad_hoc_pattern(p, cfg), reverse=True)
    return scored[: cfg.max_constructors_per_cycle]


# ---------------------------------------------------------------------------
# Smoke test / CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    # -- Build a small synthetic theorem corpus ---------------------------------
    sample_theorems: list[TheoremSketch] = [
        TheoremSketch(
            theorem_id="thm_001",
            statement="forall A B, Prod(A, B) -> Sum(A, B)",
            hypotheses=("A : Type", "B : Type"),
            conclusion="Sum(A, B)",
            tags=("algebra", "product"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_002",
            statement="forall A B, Prod(A, B) -> A",
            hypotheses=("A : Type", "B : Type"),
            conclusion="A",
            tags=("algebra", "projection"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_003",
            statement="forall A B, Prod(A, B) -> B",
            hypotheses=("A : Type", "B : Type"),
            conclusion="B",
            tags=("algebra", "projection"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_004",
            statement="forall A B C, Prod(A, Prod(B, C)) -> Prod(Prod(A, B), C)",
            hypotheses=("A : Type", "B : Type", "C : Type"),
            conclusion="Prod(Prod(A, B), C)",
            tags=("algebra", "associativity"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_005",
            statement="forall A, Sum(A, Empty) -> A",
            hypotheses=("A : Type",),
            conclusion="A",
            tags=("algebra", "unit"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_006",
            statement="forall A B, Sum(A, B) -> Sum(B, A)",
            hypotheses=("A : Type", "B : Type"),
            conclusion="Sum(B, A)",
            tags=("algebra", "commutativity"),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_007",
            statement="forall A B C, Prod(A, B) -> (B -> C) -> Prod(A, C)",
            hypotheses=("A : Type", "B : Type", "C : Type"),
            conclusion="Prod(A, C)",
            tags=("functor",),
            domain_hint="algebra",
        ),
        TheoremSketch(
            theorem_id="thm_008",
            statement="forall A B, Prod(A, B) -> Prod(B, A)",
            hypotheses=("A : Type", "B : Type"),
            conclusion="Prod(B, A)",
            tags=("algebra", "swap"),
            domain_hint="algebra",
        ),
    ]

    print("=" * 70)
    print("JuGeo — New Type Constructors Evidence Of (Ch59 smoke test)")
    print("=" * 70)
    print(f"Theorem corpus size: {len(sample_theorems)}")
    print()

    # -- Run mining cycle -------------------------------------------------------
    cfg = TypeConstructorConfig(min_pattern_occurrences=2)
    result = run_constructor_mining_cycle(sample_theorems, config=cfg)

    print("Mining cycle result:")
    print(f"  cycle_id              : {result.cycle_id}")
    print(f"  theorems_analyzed     : {result.theorems_analyzed}")
    print(f"  patterns_found        : {result.patterns_found}")
    print(f"  constructors_proposed : {result.constructors_proposed}")
    print(f"  constructors_registered: {result.constructors_registered}")
    print(f"  record_ids            : {result.record_ids}")
    print(f"  duration_seconds      : {result.duration_seconds}")
    print()

    # -- Pattern frequency report -----------------------------------------------
    coordinator = NewTypeConstructorsCoordinator(cfg)
    patterns = coordinator.detect_ad_hoc_patterns(sample_theorems)
    analyzer = NewTypeConstructorsAnalyzer()
    freq_report = analyzer.analyze_pattern_frequency(patterns)
    print("Pattern frequency report:")
    print(f"  total_patterns        : {freq_report.total_patterns}")
    print(f"  distribution          : {freq_report.frequency_distribution}")
    print(f"  top_patterns          : {freq_report.top_patterns}")
    print(f"  promotion_candidates  : {freq_report.promotion_candidates}")
    print()

    # -- Witness reports --------------------------------------------------------
    witness = NewTypeConstructorsWitness()
    if patterns:
        proposal = coordinator.propose_type_constructor(patterns[0])
        pw = witness.witness_pattern_detection(sample_theorems, patterns)
        cw = witness.witness_constructor_proposal(proposal)
        record = coordinator.register_type_constructor(proposal)
        rw = witness.witness_constructor_registration(record)
        print("Witness reports:")
        print(f"  PatternWitness  : {pw.witness_id}  theorems={pw.theorem_count} patterns={pw.pattern_count}")
        print(f"  ConstructorWitness: {cw.witness_id}  name={cw.constructor_name}  valid={cw.is_valid}")
        print(f"  RegistrationWitness: {rw.witness_id}  record={rw.record_id}  name={rw.constructor_name}")
        print()

    print("Smoke test complete.")
