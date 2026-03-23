"""
Research Assistance Theorem Schema and Falsification Suite.

This module defines the theorem-level data structures and falsification
test harness for the JuGeo Research Assistance subsystem (theory2.tex Ch63).
Research assistance operates on the ``obstruction field`` — the set of
currently unsatisfied constraints in the federation's knowledge graph —
and proposes new theorems that reduce obstruction density.

Key abstractions:
  ResearchAssistanceTheoremSchema — A richly-typed data model for a theorem
      candidate, including statement, proof sketch, dependencies, and
      quality scores.
  FalsificationSuite             — A collection of named falsification tests
      that attempt to refute a proposed theorem before it enters the
      review pipeline.

copilot: research-assistance-theorems marker
theory2.tex Ch63 — Research Assistance Theorem Schema
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Optional cross-module imports — all guarded so the module is self-contained
# when used in isolation or in test environments.
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.research_assistance.models import PackMeta  # type: ignore
except ImportError:
    PackMeta = None  # type: ignore

try:
    from jugeo.ideation.research_assistance.algorithms import ObstructionField  # type: ignore
except ImportError:
    ObstructionField = None  # type: ignore

try:
    from jugeo.federation.authority import AuthorityLevel  # type: ignore
except ImportError:
    AuthorityLevel = None  # type: ignore

try:
    from jugeo.knowledge_graph.node import KGNode  # type: ignore
except ImportError:
    KGNode = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level constants — see theory2.tex §63.2 for rationale behind each.
# ---------------------------------------------------------------------------

# copilot: threshold constants for falsification gates
CORRECTNESS_FLOOR: float = 0.50
"""Minimum acceptable correctness estimate for a theorem to pass review.

Any theorem whose ``correctness_estimate`` falls below this value is
immediately rejected by ``CorrectnessFloorTest``.  The value 0.50 is
intentionally generous: the research-assistance layer is an ideation
tool, not a formal-verification oracle.  Human reviewers are expected
to apply stricter standards during the peer-review phase.
"""

NOVELTY_SANITY_LOW: float = 0.05
"""Lower sentinel for novelty score sanity checking.

A novelty score this low strongly suggests the proposed theorem is
either a verbatim copy of an existing result or a trivial paraphrase.
The ``NoveltySanityTest`` uses this as the lower gate.
"""

NOVELTY_SANITY_HIGH: float = 0.99
"""Upper sentinel for novelty score sanity checking.

A novelty score this high is suspicious because it implies the theorem
has no overlap whatsoever with the existing knowledge graph.  Truly
revolutionary results are rare; a score this high more often indicates
a mis-parameterised embedding or a scope error.
"""

OBSTRUCTION_REDUCTION_MIN: float = 0.0
"""Obstruction reduction must be strictly positive.

A theorem that does not reduce obstruction density by any measurable
amount provides no value to the ideation pipeline.  Even a negligibly
small positive value is accepted; the threshold is exclusive of zero.
"""

DEPENDENCY_STRENGTH_FLOOR: float = 1e-6
"""Minimum meaningful dependency strength.

Dependency edges with strength at or below this value are treated as
absent.  ``DependencyStrengthTest`` rejects schemas that declare such
near-zero dependencies because they inflate edge counts without
contributing semantic weight.
"""

MAX_SKETCH_COMPLEXITY: int = 10_000
"""Hard cap on estimated proof complexity (in logical steps).

Sketches claiming more than this many steps are considered
unverifiable within the current system and are flagged by downstream
schedulers.  This constant does not directly gate falsification but is
used as a normalisation denominator in quality ranking.
"""

REGISTRY_DEFAULT_CAPACITY: int = 65_536
"""Soft capacity hint for ``ResearchAssistanceTheoremRegistry``.

The registry does not enforce this limit, but callers may use it to
pre-allocate internal data structures.  It corresponds to the expected
maximum number of theorem candidates generated in a single ideation
session (theory2.tex §63.8).
"""

# copilot: formal-statement syntax markers
FORMAL_STMT_MIN_LENGTH: int = 10
"""Minimum character length for a well-formed formal statement string."""

FORMAL_STMT_QUANTIFIER_TOKENS: tuple[str, ...] = (
    "∀", "∃", "forall", "exists", "for all", "there exists",
    ":-", "⊢", "⊨", "→", "⟹", "iff", "↔",
)
"""Tokens whose presence in a formal statement suggests syntactic validity.

At least one of these must appear in ``formal_statement`` for
``FormalStatementSyntaxTest`` to pass.  This is a heuristic, not a
full parser: the research-assistance layer does not currently embed a
proof-assistant kernel.
"""


# ===========================================================================
# Enumerations
# ===========================================================================


class TheoremKind(str, Enum):
    """Taxonomy of theorem-like objects recognised by the research assistant.

    This enumeration mirrors the classification hierarchy used in formal
    mathematical writing and maps onto authority levels in the JuGeo
    federation (theory2.tex §63.3.1).  The string value of each member is
    its canonical label as it appears in rendered documents and API
    responses.

    Authority semantics
    -------------------
    Each kind carries an implicit authority weight used by the federation's
    review pipeline.  ``AXIOM_CANDIDATE`` has the highest weight because
    accepting an axiom modifies the logical foundations of the entire
    knowledge graph.  ``LEMMA`` has the lowest weight because lemmas are
    considered local, supporting results that do not independently justify
    heavyweight peer review.

    Lifecycle
    ---------
    A theorem candidate is born as either a ``CONJECTURE`` (if the submitter
    is uncertain about its truth) or a ``PROPOSITION`` / ``THEOREM``
    (if the submitter has a partial proof).  After surviving the
    ``FalsificationSuite`` it enters the review pipeline where its kind may
    be promoted or demoted based on reviewer consensus.

    Example usage::

        kind = TheoremKind.THEOREM
        if kind.is_review_mandatory:
            schedule_peer_review(schema)
    """

    LEMMA = "lemma"
    """A supporting result used internally within a larger proof.

    Lemmas are local to a single pack and do not appear in the public API
    of the federation.  The research assistant assigns them low priority
    in the presentation layer but high priority in the dependency resolver
    because they are load-bearing for parent theorems.

    Authority level: 1 (lowest).
    Typical peer-review threshold: none — a pack author may self-certify.
    Example: 'Every compact metric space is sequentially compact.'
    """

    PROPOSITION = "proposition"
    """A result of moderate significance, typically with a short direct proof.

    Propositions occupy a middle tier: they are exposed in pack public
    APIs but do not require the full review ceremony demanded of
    ``THEOREM``.  The research assistant targets propositions when the
    obstruction field has isolated, well-scoped gaps.

    Authority level: 2.
    Typical peer-review threshold: one reviewer sign-off.
    Example: 'The composition of two continuous maps is continuous.'
    """

    THEOREM = "theorem"
    """A significant, independently important result.

    Theorems are the primary deliverable of the research-assistance
    pipeline.  They carry full authority weight and undergo mandatory
    multi-reviewer consensus before entering the knowledge graph.  The
    research assistant generates theorem candidates only when its
    correctness estimate exceeds ``CORRECTNESS_FLOOR`` by a comfortable
    margin.

    Authority level: 3.
    Typical peer-review threshold: two independent reviewer sign-offs.
    Example: 'Every finite-dimensional normed space is locally compact.'
    """

    COROLLARY = "corollary"
    """A result that follows almost immediately from a parent theorem.

    Corollaries inherit much of their authority from their parent.  The
    research assistant tags a candidate as a corollary when its
    dependency graph has exactly one high-strength edge pointing to a
    ``THEOREM`` and the proof sketch has estimated_complexity < 3.

    Authority level: 2 (inherited from parent, discounted).
    Typical peer-review threshold: parent's reviewers are asked to ratify.
    Example: 'Every closed subspace of a compact space is compact.'
    """

    CONJECTURE = "conjecture"
    """An unproven claim submitted for community investigation.

    Conjectures enter the pipeline with no proof sketch required.
    They are valuable because they direct the obstruction-reduction
    engine toward promising regions of the proof space.  A conjecture
    is promoted to ``THEOREM`` only after a complete proof is accepted.

    Authority level: 0 (speculative).
    Typical peer-review threshold: informal discussion only.
    Example: 'Every even integer > 2 is the sum of two primes.'
    """

    AXIOM_CANDIDATE = "axiom_candidate"
    """A proposed foundational assumption for a pack or the entire federation.

    Axiom candidates are extraordinarily rare and require federation-wide
    consensus.  The research assistant generates them only when the
    obstruction field indicates a deep, irresolvable gap that cannot be
    closed by derivable results.  Accepting an axiom_candidate triggers
    a full re-validation of all dependent knowledge-graph nodes.

    Authority level: 5 (highest).
    Typical peer-review threshold: full federation vote.
    Example: 'The axiom of choice holds in the meta-category of packs.'
    """


class ProofStrategy(str, Enum):
    """Enumeration of proof strategies that the research assistant may deploy.

    Each strategy corresponds to a distinct structural pattern in the
    space of mathematical proofs (theory2.tex §63.4).  The research
    assistant selects a strategy based on the shape of the obstruction
    field, the kind of the proposed theorem, and the estimated complexity
    of the proof sketch.

    Complexity classes
    ------------------
    Each strategy has an associated worst-case complexity class for the
    *search* problem of finding a valid proof.  Note that this is the
    complexity of *discovering* the proof, not of *verifying* it (which
    is always polynomial in the proof length under standard assumptions).

    Preference heuristics
    ---------------------
    The research assistant maintains a learned preference matrix over
    (TheoremKind × ObstructionClass × ProofStrategy) triples, updated
    after each review cycle.  The values in this enumeration are the
    stable identifiers used as keys in that matrix.
    """

    DIRECT = "direct"
    """Prove the statement by a direct chain of logical implications.

    Direct proof is the most natural strategy and is attempted first
    for all theorem kinds.  It is preferred when the obstruction is a
    ``MISSING_LEMMA``: the assistant first fills in the lemma, then
    proceeds directly.

    Complexity class (search): polynomial in the depth of the
    dependency graph.
    Research assistant preference: highest, used as the default
    fallback when no other strategy is clearly superior.
    """

    CONTRADICTION = "contradiction"
    """Assume the negation and derive a contradiction.

    Proof by contradiction is preferred when the statement involves
    uniqueness, non-existence, or extremal properties that are difficult
    to construct directly.  The assistant selects this strategy when the
    obstruction class is ``UNRESOLVED_QUANTIFIER`` with an existential
    quantifier.

    Complexity class (search): exponential in the worst case, but often
    practical due to aggressive pruning of the negation tree.
    Research assistant preference: second highest for ``THEOREM`` and
    ``PROPOSITION`` kinds.
    """

    INDUCTION = "induction"
    """Prove a base case and an inductive step.

    Mathematical induction — in its simple, strong, transfinite, and
    structural variants — is the workhorse for results about inductively
    defined objects (natural numbers, trees, formal language derivations).
    The assistant selects this strategy when the theorem's formal
    statement contains a universally quantified natural-number variable.

    Complexity class (search): linear in the depth of the induction
    if the inductive step can be automated; exponential otherwise.
    Research assistant preference: high for discrete-mathematics packs.
    """

    CONTRAPOSITIVE = "contrapositive"
    """Prove ¬Q → ¬P instead of P → Q.

    Contrapositive proof is a specialisation of direct proof applied to
    the logically equivalent contrapositive.  It is preferred when ¬P
    is a stronger or more structured hypothesis than P, giving the prover
    more to work with.  The assistant detects this preference by checking
    whether ¬P appears as a known lemma in the knowledge graph.

    Complexity class (search): same as DIRECT applied to the
    contrapositive statement.
    Research assistant preference: moderate; used when DIRECT fails on
    the original statement within the first 100 search steps.
    """

    CONSTRUCTION = "construction"
    """Prove existence by exhibiting a concrete witness.

    Constructive proofs are required in packs that adopt constructive
    logic (no law of excluded middle).  Even in classical packs the
    assistant prefers construction when the obstruction class is
    ``MISSING_LEMMA`` of existential type: building an explicit object
    often yields a lemma reusable elsewhere.

    Complexity class (search): highly problem-dependent; can be
    polynomial (closed-form witness) or undecidable (Diophantine).
    Research assistant preference: mandatory in constructive-logic packs.
    """

    EXHAUSTION = "exhaustion"
    """Case-split the domain and verify each case separately.

    Proof by exhaustion is used when the domain is small and finite, or
    can be partitioned into a bounded number of structurally distinct
    cases.  The assistant applies exhaustion when the formal statement's
    quantifier range is bounded and the bound fits within
    ``MAX_SKETCH_COMPLEXITY``.

    Complexity class (search): linear in the number of cases, but the
    number of cases can be exponential in the input parameters.
    Research assistant preference: low for large domains; high for
    small finite structures like small groups or graph families.
    """

    DIAGONAL = "diagonal"
    """Exploit a diagonalisation or self-reference argument.

    Cantor's diagonal argument, Gödel's incompleteness technique, and
    Russell's paradox all fall under this strategy.  The assistant
    selects diagonal when the obstruction involves a self-referential
    constraint or when the theorem is about cardinality, definability,
    or provability.

    Complexity class (search): hard to classify; typically requires
    a non-constructive leap and is flagged for human-in-the-loop review.
    Research assistant preference: reserved for ``THEOREM`` and
    ``CONJECTURE`` kinds in logic/set-theory packs.
    """

    PROBABILISTIC = "probabilistic"
    """Use a probabilistic argument to show existence or prevalence.

    Probabilistic proofs (the probabilistic method, Lovász Local Lemma,
    random algebraic constructions) are powerful when deterministic
    constructions are unknown.  The assistant selects this strategy when
    the obstruction involves a combinatorial existence claim and the pack
    supports probabilistic reasoning.

    Complexity class (search): polynomial if the probability space is
    well-understood; depends on the mixing time of the underlying Markov
    chain.
    Research assistant preference: high for combinatorics and
    information-theory packs; disabled by default in constructive packs.
    """


class ObstructionClass(str, Enum):
    """Classification of obstruction types in the federation knowledge graph.

    An obstruction is an unsatisfied constraint detected by the
    obstruction-field analyser (theory2.tex §63.5).  Each class
    corresponds to a distinct structural defect that a new theorem might
    repair.  The research assistant uses the dominant obstruction class
    to select proof strategies and template the formal statement.

    Cross-class interactions
    ------------------------
    Obstructions of different classes can co-occur in a single pack.
    When multiple classes are present the assistant addresses them in
    priority order: ``DEPENDENCY_CYCLE`` first (because cycles prevent
    any downstream reasoning), then ``MISSING_LEMMA``, and so on.

    Reduction semantics
    -------------------
    ``obstruction_reduction`` in ``ResearchAssistanceTheoremSchema``
    measures the expected decrease in total obstruction count across all
    classes after the proposed theorem is accepted.  A single theorem
    may address multiple obstruction classes simultaneously.
    """

    DEPENDENCY_CYCLE = "dependency_cycle"
    """Two or more theorem nodes form a circular dependency chain.

    This is the most critical obstruction class.  A cycle prevents the
    topological sort of the knowledge graph and blocks proof-checking.
    The assistant resolves cycles by proposing a ``LEMMA`` that breaks
    the cycle: one edge in the cycle is replaced by two edges through
    the new lemma node.

    Typical resolution strategy: DIRECT or CONSTRUCTION.
    Priority: critical — must be resolved before other classes.
    Detection: tarjan's SCC algorithm applied to the dependency graph.
    """

    MISSING_LEMMA = "missing_lemma"
    """A proof step cites a result that does not exist in the knowledge graph.

    Missing lemmas are the most common obstruction class encountered in
    practice.  They arise when a theorem is entered with an incomplete
    proof sketch.  The assistant proposes the missing lemma as a new
    ``LEMMA`` or ``PROPOSITION`` candidate, attempting to fill the gap.

    Typical resolution strategy: DIRECT, INDUCTION, or CONTRADICTION.
    Priority: high.
    Detection: reference resolution during proof-sketch parsing.
    """

    SCOPE_OVERFLOW = "scope_overflow"
    """A theorem's statement or proof references concepts outside its pack.

    Each pack defines a scope boundary.  A theorem that borrows
    definitions from a foreign pack without a declared import creates a
    scope-overflow obstruction.  The assistant resolves this by either
    narrowing the statement to stay within scope or proposing an import
    declaration.

    Typical resolution strategy: CONSTRUCTION (restrict to local objects).
    Priority: medium.
    Detection: scope checker applied to formal_statement tokens.
    """

    PACK_BOUNDARY_VIOLATION = "pack_boundary_violation"
    """A theorem's proof crosses a pack boundary without a valid bridge.

    Similar to SCOPE_OVERFLOW but specifically about proof steps
    (not just statements).  If a proof step invokes a theorem from
    pack B while executing inside pack A, and no bridge theorem exists,
    the pack boundary is violated.

    Typical resolution strategy: DIRECT (add bridge lemma).
    Priority: medium.
    Detection: pack-boundary checker in the proof-step evaluator.
    """

    UNRESOLVED_QUANTIFIER = "unresolved_quantifier"
    """A quantifier in the formal statement has no witness or bounding argument.

    Existential quantifiers without witnesses and universal quantifiers
    without a bounding argument trigger this obstruction.  It is often
    a symptom of an incomplete formal statement rather than a deep
    mathematical gap.

    Typical resolution strategy: CONSTRUCTION (for ∃) or DIRECT (for ∀).
    Priority: medium-low.
    Detection: quantifier parser in the formal-statement analyser.
    """

    SEMANTIC_GAP = "semantic_gap"
    """Two neighbouring theorems address logically adjacent but disconnected claims.

    Semantic gaps arise when the knowledge graph has regions of high
    density separated by sparse corridors.  The corridor theorems are
    missing, leaving an implicit logical jump that human readers must
    bridge mentally.  The assistant proposes connector theorems that
    make the jump explicit and machine-checkable.

    Typical resolution strategy: DIRECT or CONTRAPOSITIVE.
    Priority: low (cosmetic / quality-of-life).
    Detection: semantic-distance metric on the embedding of theorem statements.
    """


# ===========================================================================
# Data classes
# ===========================================================================


@dataclass(frozen=True, slots=True)
class TheoremDependency:
    """An edge in the theorem dependency graph pointing to a prerequisite.

    A ``TheoremDependency`` instance represents a directed edge
    ``schema → dep``, where ``schema`` is the theorem that *depends on*
    the result identified by ``dep_id``.  The edge carries a real-valued
    ``strength`` that quantifies how load-bearing the prerequisite is: a
    strength of 1.0 means the dependent theorem is logically vacuous
    without the prerequisite; a strength near 0 means the dependency is
    merely motivational.

    The ``is_circular`` flag is set by the dependency-cycle detector
    (theory2.tex §63.5.1).  A circular dependency does not immediately
    invalidate the schema — it flags it for the ``CircularDependencyTest``
    in the ``FalsificationSuite``.

    Invariants
    ----------
    * ``dep_id`` must be a non-empty string.
    * ``strength`` must satisfy 0.0 ≤ strength ≤ 1.0.
    * ``dep_kind`` specifies the kind of the *prerequisite* node, not the
      dependent node.
    * ``is_circular`` is informational; the falsification test decides
      whether circularity is fatal.

    Example::

        dep = TheoremDependency(
            dep_id="thm:heine-borel",
            dep_kind=TheoremKind.THEOREM,
            strength=0.95,
            is_circular=False,
        )
        assert dep.strength <= 1.0
    """

    dep_id: str
    """Unique identifier of the prerequisite theorem/lemma/axiom."""

    dep_kind: TheoremKind
    """The ``TheoremKind`` of the prerequisite node."""

    strength: float
    """Dependency strength in [0, 1].

    1.0 = the dependent theorem is entirely vacuous without this prerequisite.
    0.0 = purely motivational; the proof does not formally cite this result.
    """

    is_circular: bool
    """True if this edge is part of a detected dependency cycle."""


@dataclass(frozen=True, slots=True)
class ProofSketch:
    """A structured summary of the intended proof approach for a theorem candidate.

    A ``ProofSketch`` is not a formal proof.  It is a human-readable (and
    machine-parseable) outline that the research assistant produces to
    justify its confidence estimate and to guide the human reviewer.

    The sketch consists of a natural-language description (``sketch_text``),
    the primary proof strategy, an estimate of proof length in logical
    steps, a subjective confidence value, and the list of obstruction IDs
    that the theorem is expected to resolve.

    Complexity semantics
    --------------------
    ``estimated_complexity`` is measured in *logical steps* — the number
    of distinct inference rules applied in a fully formal proof.  This is
    an estimate and may differ from the actual length of a machine-checked
    proof by an order of magnitude.  The assistant uses this value to
    prioritise which theorem candidates to present first: shorter proofs
    are presented before longer ones because they are more likely to be
    verified quickly.

    Confidence semantics
    --------------------
    ``confidence`` reflects the assistant's internal belief that the
    sketch can be completed to a valid proof.  It is a product of:
      * the strength of the analogy to known proofs of similar theorems,
      * the absence of detected counterexamples in the falsification suite,
      * the coverage of the obstruction IDs by known lemmas.

    Example::

        sketch = ProofSketch(
            strategy=ProofStrategy.DIRECT,
            sketch_text="Apply Heine-Borel to the closure of the ball.",
            estimated_complexity=12,
            confidence=0.82,
            obstruction_ids=("obs:missing-compactness-lemma",),
        )
    """

    strategy: ProofStrategy
    """The primary proof strategy employed in this sketch."""

    sketch_text: str
    """Free-text description of the proof approach (natural language)."""

    estimated_complexity: int
    """Estimated number of logical steps in the full formal proof."""

    confidence: float
    """Assistant's confidence that the sketch completes to a valid proof, in [0, 1]."""

    obstruction_ids: tuple[str, ...]
    """IDs of obstructions in the obstruction field that this theorem resolves."""


@dataclass(frozen=True, slots=True)
class ResearchAssistanceTheoremSchema:
    """Canonical data model for a theorem candidate in the research-assistance pipeline.

    A ``ResearchAssistanceTheoremSchema`` instance is the primary unit of
    work produced by the JuGeo research-assistance subsystem.  It bundles
    together all the information needed for the ``FalsificationSuite`` to
    evaluate the theorem's plausibility, for the review pipeline to
    schedule human review, and for the knowledge-graph ingestion layer to
    import the theorem after acceptance.

    Field descriptions
    ------------------
    ``theorem_id``
        A globally unique identifier, typically a UUID4 string with an
        optional human-readable prefix (e.g. ``"thm:abc123"``).  Must be
        non-empty.

    ``kind``
        The ``TheoremKind`` of this candidate.  Determines the authority
        weight and the review ceremony.

    ``statement``
        Natural-language statement of the theorem, written for a
        mathematically literate human reader.  Must be non-empty.

    ``formal_statement``
        A formal rendering of the statement in a pseudo-formal language
        (e.g. first-order logic with set-theoretic sugar).  Used by
        ``FormalStatementSyntaxTest`` and by the knowledge-graph ingestion
        layer.  Must contain at least one token from
        ``FORMAL_STMT_QUANTIFIER_TOKENS``.

    ``pack_id``
        The ID of the pack (knowledge-graph namespace) to which this
        theorem belongs.  Used by ``ScopeOverflowTest`` and by
        ``ResearchAssistanceTheoremRegistry.by_pack``.

    ``dependencies``
        A tuple of ``TheoremDependency`` edges.  The empty tuple is
        allowed for axiom candidates that have no prerequisites.

    ``proof_sketch``
        The ``ProofSketch`` justifying the theorem's plausibility.

    ``novelty_score``
        A float in [0, 1] produced by the embedding-based novelty
        estimator.  High values indicate the theorem is unlike anything
        in the current knowledge graph.

    ``correctness_estimate``
        A float in [0, 1] produced by the consistency checker.  Below
        ``CORRECTNESS_FLOOR`` the theorem is rejected by
        ``CorrectnessFloorTest``.

    ``obstruction_reduction``
        Expected fractional reduction in the total obstruction count
        after this theorem is accepted.  Must be strictly positive.

    ``submitted_at``
        Unix timestamp (seconds since epoch) of submission.

    ``submitter_id``
        Identifier of the agent (human or automated) that submitted
        this theorem candidate.

    Invariants
    ----------
    * ``novelty_score``, ``correctness_estimate``, and
      ``obstruction_reduction`` must all lie in [0, 1].
    * ``theorem_id``, ``statement``, ``formal_statement``, ``pack_id``,
      and ``submitter_id`` must all be non-empty strings.
    * ``submitted_at`` must be a positive float.
    * The ``dependencies`` tuple may be empty but must not contain
      duplicate ``dep_id`` values (validated externally).

    Example::

        schema = ResearchAssistanceTheoremSchema(
            theorem_id="thm:seq-compact-metric",
            kind=TheoremKind.THEOREM,
            statement="Every compact metric space is sequentially compact.",
            formal_statement="∀ X. compact(X) ∧ metric(X) → seq_compact(X)",
            pack_id="pack:topology-basics",
            dependencies=(
                TheoremDependency("thm:heine-borel", TheoremKind.THEOREM, 0.9, False),
            ),
            proof_sketch=ProofSketch(
                ProofStrategy.DIRECT, "Extract subsequence using compactness.", 8, 0.9,
                ("obs:seq-compact-gap",),
            ),
            novelty_score=0.72,
            correctness_estimate=0.91,
            obstruction_reduction=0.15,
            submitted_at=time.time(),
            submitter_id="agent:ideation-v3",
        )
    """

    theorem_id: str
    """Globally unique identifier for this theorem candidate."""

    kind: TheoremKind
    """Taxonomy classification of this candidate."""

    statement: str
    """Natural-language statement of the theorem."""

    formal_statement: str
    """Formal (pseudo-logic) rendering of the statement."""

    pack_id: str
    """Knowledge-graph pack namespace for this theorem."""

    dependencies: tuple[TheoremDependency, ...]
    """Prerequisite theorems/lemmas required by the proof sketch."""

    proof_sketch: ProofSketch
    """Structured summary of the intended proof approach."""

    novelty_score: float
    """Novelty estimate in [0, 1] from the embedding-based novelty estimator."""

    correctness_estimate: float
    """Consistency estimate in [0, 1] from the consistency checker."""

    obstruction_reduction: float
    """Expected fractional reduction in total obstruction count after acceptance."""

    submitted_at: float
    """Unix timestamp of submission (seconds since epoch)."""

    submitter_id: str
    """Identifier of the submitting agent or user."""


@dataclass(frozen=True, slots=True)
class FalsificationResult:
    """The outcome of a single falsification test applied to a theorem candidate.

    A ``FalsificationResult`` is an immutable record produced by calling
    ``FalsificationTest.run(schema)``.  It carries a boolean verdict
    (``passed``), the test's name (for traceability), a confidence value
    that modulates the aggregate verdict in ``FalsificationSuite.verdict``,
    an optional counterexample string, and a free-text details field for
    human reviewers.

    The ``passed`` flag means "this test did **not** falsify the theorem".
    A passing result is desirable; a failing result indicates the test
    found evidence against the theorem's validity or quality.

    Confidence semantics
    --------------------
    ``confidence`` is the test's own estimate of how reliable its verdict
    is.  A test with confidence 1.0 is certain about its conclusion;
    confidence 0.5 means the test is essentially uninformative.
    ``FalsificationSuite.verdict`` aggregates these values using a
    geometric mean, so a single very-low-confidence failing test does not
    doom an otherwise healthy schema.

    Example::

        result = FalsificationResult(
            test_name="CorrectnessFloorTest",
            passed=True,
            confidence=1.0,
            counterexample=None,
            details="correctness_estimate=0.82 ≥ floor=0.50",
        )
    """

    test_name: str
    """Name of the falsification test that produced this result."""

    passed: bool
    """True if the test did not falsify the theorem; False if it found a defect."""

    confidence: float
    """Reliability of this verdict, in [0, 1]."""

    counterexample: Optional[str]
    """A counterexample string if the test found one, else None."""

    details: str
    """Human-readable explanation of the verdict."""


# ===========================================================================
# Falsification tests
# ===========================================================================


class FalsificationTest:
    """Abstract base class for falsification tests in the research-assistance pipeline.

    A ``FalsificationTest`` encapsulates a single check that attempts to
    *refute* a proposed theorem before it enters the human review pipeline.
    The philosophy is inspired by Popper's falsificationism: we cannot
    confirm a theorem is correct, but we can efficiently detect many
    common defects.

    Subclasses must override the ``run`` method.  The ``name`` and
    ``description`` properties are set in ``__init__`` and stored as
    private attributes.

    Lifecycle
    ---------
    1. The ``FalsificationSuite`` collects a list of tests via ``add_test``.
    2. When ``run_all`` is called, each test's ``run`` method receives the
       schema and returns a ``FalsificationResult``.
    3. ``verdict`` aggregates results into a single (passed, confidence) pair.
    4. ``report`` renders a human-readable summary.

    Subclassing conventions
    -----------------------
    * Override ``run``; do not override ``name`` or ``description``.
    * The ``run`` method must never raise an exception — catch all errors
      internally and return a failing result with details describing the
      exception.
    * Keep each test focused on a single property; compose tests in the
      suite rather than building omnibus tests.

    Example::

        class MyTest(FalsificationTest):
            def __init__(self):
                super().__init__("MyTest", "Checks that X holds.")

            def run(self, schema):
                if schema.novelty_score > 0:
                    return FalsificationResult("MyTest", True, 1.0, None, "OK")
                return FalsificationResult("MyTest", False, 0.9, None, "novelty=0")
    """

    def __init__(self, name: str, description: str) -> None:
        """Initialise the test with a name and description.

        Args:
            name: Short identifier for this test, used as the key in reports.
            description: One-sentence description of what property this test
                checks.

        Returns:
            None.

        Raises:
            ValueError: If ``name`` or ``description`` is empty.

        Example::

            test = FalsificationTest("MyTest", "Checks that X holds.")
            assert test.name == "MyTest"
        """
        if not name:
            raise ValueError("FalsificationTest name must be non-empty.")
        if not description:
            raise ValueError("FalsificationTest description must be non-empty.")
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        """Return the short identifier of this test.

        Returns:
            The name string provided at construction.

        Example::

            assert test.name == "CorrectnessFloorTest"
        """
        return self._name

    @property
    def description(self) -> str:
        """Return the one-sentence description of what this test checks.

        Returns:
            The description string provided at construction.

        Example::

            print(test.description)
        """
        return self._description

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Execute the falsification test against ``schema``.

        Subclasses must override this method.  The base implementation
        always returns a passing result with confidence 0 (i.e., it is
        completely uninformative).

        Args:
            schema: The ``ResearchAssistanceTheoremSchema`` to test.

        Returns:
            A ``FalsificationResult`` recording the verdict.

        Raises:
            Nothing — all exceptions must be caught internally.

        Example::

            result = test.run(schema)
            assert isinstance(result, FalsificationResult)
        """
        return FalsificationResult(
            test_name=self._name,
            passed=True,
            confidence=0.0,
            counterexample=None,
            details="Base FalsificationTest — no checks performed.",
        )


# ---------------------------------------------------------------------------
# Concrete falsification tests
# ---------------------------------------------------------------------------


class TautologyTest(FalsificationTest):
    """Detect trivially true or vacuous theorem statements.

    A tautological theorem provides zero informational value: it asserts
    something that is definitionally true, a direct consequence of notation,
    or so weak as to be uninteresting.  Common symptoms include statements
    of the form "if P then P", statements whose formal_statement reduces to
    a propositional tautology, or statements with novelty_score below
    ``NOVELTY_SANITY_LOW``.

    This test is heuristic: it cannot detect all tautologies (that would
    require a full theorem prover), but it catches the most obvious cases
    by inspecting keyword patterns and the novelty score in combination.

    Failure mode
    ------------
    The test returns ``passed=False`` when the statement contains a
    trivially self-referential pattern OR when the novelty_score is below
    ``NOVELTY_SANITY_LOW`` AND the statement is suspiciously short.  The
    confidence of a failing result is capped at 0.80 because the heuristics
    can produce false positives on legitimate low-novelty results (e.g.,
    classical theorems being re-stated with slightly different notation).
    """

    # copilot: tautology-detection heuristics
    _TRIVIAL_PATTERNS: tuple[str, ...] = (
        "trivially", "by definition", "if and only if itself",
        "is equal to itself", "follows immediately", "is trivial",
        "tautologically", "vacuously true",
    )

    def __init__(self) -> None:
        """Initialise TautologyTest with a fixed name and description.

        Returns:
            None.

        Example::

            test = TautologyTest()
            assert test.name == "TautologyTest"
        """
        super().__init__(
            "TautologyTest",
            "Detects trivially true or vacuous theorem statements.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Check whether the theorem statement appears to be tautological.

        The check proceeds in three stages:
          1. Keyword scan of ``statement`` for trivial-pattern tokens.
          2. Self-reference check: does ``formal_statement`` contain
             a simple 'X → X' or 'X ↔ X' pattern?
          3. Combined novelty+length heuristic: very low novelty and very
             short statement together suggest a trivial restatement.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult`` with ``passed=False`` if evidence of
            tautology is found, ``passed=True`` otherwise.

        Raises:
            Nothing — all errors are caught and surfaced in ``details``.

        Example::

            schema = make_test_schema(statement="Every X is X by definition.")
            result = TautologyTest().run(schema)
            assert result.passed is False
        """
        try:
            stmt_lower = schema.statement.lower()
            formal_lower = schema.formal_statement.lower()

            # Stage 1: keyword scan
            found_keywords = [p for p in self._TRIVIAL_PATTERNS if p in stmt_lower]
            if found_keywords:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.75,
                    counterexample=None,
                    details=(
                        f"Statement contains trivial-pattern keywords: {found_keywords}. "
                        "This suggests the theorem may be vacuous or definitionally true."
                    ),
                )

            # Stage 2: self-reference check in formal statement
            self_ref_patterns = ("→ x", "→ p", "↔ x", "↔ p", "iff itself")
            for pat in self_ref_patterns:
                if pat in formal_lower:
                    return FalsificationResult(
                        test_name=self.name,
                        passed=False,
                        confidence=0.70,
                        counterexample=f"Pattern '{pat}' found in formal_statement.",
                        details=(
                            "The formal statement appears to assert 'P → P' or similar "
                            "self-referential tautology."
                        ),
                    )

            # Stage 3: novelty + length heuristic
            if schema.novelty_score < NOVELTY_SANITY_LOW and len(schema.statement) < 60:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.60,
                    counterexample=None,
                    details=(
                        f"novelty_score={schema.novelty_score:.3f} < {NOVELTY_SANITY_LOW} "
                        f"and statement length={len(schema.statement)} < 60 chars. "
                        "Likely a trivial restatement of an existing result."
                    ),
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.85,
                counterexample=None,
                details="No tautology indicators detected.",
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"TautologyTest raised an unexpected error: {exc}",
            )


class CircularDependencyTest(FalsificationTest):
    """Detect circular dependency chains in the theorem's dependency graph.

    A circular dependency means at least one ``TheoremDependency`` in the
    schema's ``dependencies`` tuple has ``is_circular=True``, or a cycle
    can be inferred from the dep_id references.  Circular dependencies
    prevent the topological sort of the knowledge graph and are classified
    as ``ObstructionClass.DEPENDENCY_CYCLE``.

    Severity
    --------
    Unlike most other tests, a circular dependency is almost always a
    hard failure.  The confidence of a failing result is therefore set to
    0.95 — very high certainty.  The only exception is if the cycle
    involves an ``AXIOM_CANDIDATE`` dep, which may legitimately be
    mutually referential in bootstrap packs.

    Detection algorithm
    -------------------
    This test inspects the ``is_circular`` flag on each dependency edge
    (set externally by the dependency-cycle detector) and also performs
    a simple self-loop check (dep_id == schema.theorem_id).
    """

    def __init__(self) -> None:
        """Initialise CircularDependencyTest.

        Returns:
            None.

        Example::

            test = CircularDependencyTest()
        """
        super().__init__(
            "CircularDependencyTest",
            "Detects circular dependency chains that would prevent graph topological sort.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Scan dependency edges for circularity.

        Checks every ``TheoremDependency`` in ``schema.dependencies`` for
        the ``is_circular`` flag and for self-loop edges (dep_id ==
        theorem_id).  If any such edge is found, returns a failing result
        with the offending edge IDs listed in ``details``.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult`` indicating whether circular
            dependencies were detected.

        Raises:
            Nothing.

        Example::

            dep = TheoremDependency("thm:self", TheoremKind.LEMMA, 0.5, True)
            schema = make_test_schema(dependencies=(dep,))
            result = CircularDependencyTest().run(schema)
            assert result.passed is False
        """
        try:
            circular_ids = []
            self_loop_ids = []

            for dep in schema.dependencies:
                if dep.is_circular:
                    circular_ids.append(dep.dep_id)
                if dep.dep_id == schema.theorem_id:
                    self_loop_ids.append(dep.dep_id)

            if self_loop_ids:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(self_loop_ids),
                    details=(
                        f"Self-loop detected: theorem depends on itself. "
                        f"Self-loop dep_ids: {self_loop_ids}."
                    ),
                )

            if circular_ids:
                # Axiom candidates get a softer treatment
                confidence = 0.95
                if schema.kind == TheoremKind.AXIOM_CANDIDATE:
                    confidence = 0.60
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=confidence,
                    counterexample=str(circular_ids),
                    details=(
                        f"Circular dependency edges detected for dep_ids: {circular_ids}. "
                        "These edges are part of a dependency cycle in the knowledge graph."
                    ),
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.95,
                counterexample=None,
                details=f"No circular dependencies detected among {len(schema.dependencies)} edges.",
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"CircularDependencyTest raised an unexpected error: {exc}",
            )


class ScopeOverflowTest(FalsificationTest):
    """Check that the theorem does not reference concepts outside its pack.

    A scope overflow occurs when a theorem's ``formal_statement`` or
    ``statement`` references a pack identifier other than the theorem's own
    ``pack_id``.  This is detected heuristically by scanning for pack-ID
    prefixes in the statement strings.

    The test scans for tokens of the form ``pack:<name>`` and compares them
    against the schema's own ``pack_id``.  Cross-pack references that are
    not declared in the dependency list are flagged.

    Limitations
    -----------
    This is a lexical scan only.  It cannot detect implicit cross-pack
    dependencies arising from shared notation or overloaded symbols.  A
    full semantic scope check requires the pack-boundary checker which is
    not available inside this module.
    """

    def __init__(self) -> None:
        """Initialise ScopeOverflowTest.

        Returns:
            None.

        Example::

            test = ScopeOverflowTest()
        """
        super().__init__(
            "ScopeOverflowTest",
            "Checks that formal_statement does not overflow the theorem's pack scope.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Scan for cross-pack references in the formal statement.

        Tokenises ``formal_statement`` on whitespace and bracket delimiters,
        then identifies tokens that look like pack IDs (i.e., match
        ``pack:<name>`` pattern) that differ from ``schema.pack_id``.
        Also checks if ``schema.pack_id`` is empty.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult``.  Fails if foreign pack references are
            found or if pack_id is empty.

        Raises:
            Nothing.

        Example::

            schema = make_test_schema(pack_id="pack:A",
                formal_statement="∀x∈pack:B. P(x)")
            result = ScopeOverflowTest().run(schema)
            assert result.passed is False
        """
        try:
            if not schema.pack_id:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=None,
                    details="pack_id is empty — theorem has no pack scope.",
                )

            import re  # local import to keep module-level namespace clean
            tokens = re.findall(r"pack:[A-Za-z0-9_\-]+", schema.formal_statement)
            tokens += re.findall(r"pack:[A-Za-z0-9_\-]+", schema.statement)

            foreign = [t for t in tokens if t != schema.pack_id]
            if foreign:
                unique_foreign = sorted(set(foreign))
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.80,
                    counterexample=str(unique_foreign),
                    details=(
                        f"Foreign pack references found: {unique_foreign}. "
                        f"Expected only references to '{schema.pack_id}'."
                    ),
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.75,
                counterexample=None,
                details="No scope overflow detected in statement strings.",
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"ScopeOverflowTest raised an unexpected error: {exc}",
            )


class NoveltySanityTest(FalsificationTest):
    """Check that the novelty score is within a plausible range.

    A novelty score outside [``NOVELTY_SANITY_LOW``, ``NOVELTY_SANITY_HIGH``]
    suggests a mis-calibrated embedding or a degenerate input.  Suspiciously
    low novelty implies the theorem is already in the knowledge graph;
    suspiciously high novelty implies the theorem is disconnected from all
    existing knowledge, which is usually a sign of scope error or garbled
    input.

    This test does not judge the *content* of the theorem — it validates
    the *metadata* quality.  A passing result here means the novelty score
    is in a range where it carries information; it does not mean the theorem
    is novel.
    """

    def __init__(self) -> None:
        """Initialise NoveltySanityTest.

        Returns:
            None.

        Example::

            test = NoveltySanityTest()
        """
        super().__init__(
            "NoveltySanityTest",
            "Validates that novelty_score is within the plausible range "
            f"[{NOVELTY_SANITY_LOW}, {NOVELTY_SANITY_HIGH}].",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Evaluate the novelty score for range sanity.

        Checks that ``schema.novelty_score`` is a finite float in the range
        [0, 1] and specifically within [``NOVELTY_SANITY_LOW``,
        ``NOVELTY_SANITY_HIGH``].  The confidence of the result is higher
        for clearly out-of-range values and lower for borderline values.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult``.

        Raises:
            Nothing.

        Example::

            schema = make_test_schema(novelty_score=0.001)
            result = NoveltySanityTest().run(schema)
            assert result.passed is False
        """
        try:
            score = schema.novelty_score

            if not math.isfinite(score):
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(score),
                    details=f"novelty_score={score} is not finite.",
                )

            if score < 0.0 or score > 1.0:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(score),
                    details=f"novelty_score={score} is outside the valid range [0, 1].",
                )

            if score < NOVELTY_SANITY_LOW:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.85,
                    counterexample=str(score),
                    details=(
                        f"novelty_score={score:.4f} < NOVELTY_SANITY_LOW={NOVELTY_SANITY_LOW}. "
                        "The theorem may already exist in the knowledge graph."
                    ),
                )

            if score > NOVELTY_SANITY_HIGH:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.80,
                    counterexample=str(score),
                    details=(
                        f"novelty_score={score:.4f} > NOVELTY_SANITY_HIGH={NOVELTY_SANITY_HIGH}. "
                        "The theorem appears completely disconnected from existing knowledge."
                    ),
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.90,
                counterexample=None,
                details=f"novelty_score={score:.4f} is within [{NOVELTY_SANITY_LOW}, {NOVELTY_SANITY_HIGH}].",
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"NoveltySanityTest raised an unexpected error: {exc}",
            )


class CorrectnessFloorTest(FalsificationTest):
    """Reject theorems whose correctness estimate falls below the acceptable floor.

    ``CORRECTNESS_FLOOR`` is the minimum acceptable value for
    ``correctness_estimate``.  Any theorem below this threshold is
    too speculative to enter the review pipeline: the expected cost
    of human reviewer time spent on a likely-incorrect theorem exceeds
    the expected benefit.

    This test has confidence 1.0 for all outcomes because the check is
    deterministic given the schema's data.  A theorem with
    ``correctness_estimate < CORRECTNESS_FLOOR`` always fails; one with
    ``correctness_estimate ≥ CORRECTNESS_FLOOR`` always passes this test
    (though it may fail others).
    """

    def __init__(self) -> None:
        """Initialise CorrectnessFloorTest.

        Returns:
            None.

        Example::

            test = CorrectnessFloorTest()
        """
        super().__init__(
            "CorrectnessFloorTest",
            f"Rejects theorems with correctness_estimate < {CORRECTNESS_FLOOR}.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Compare correctness_estimate against CORRECTNESS_FLOOR.

        Validates that ``schema.correctness_estimate`` is finite, in [0,1],
        and at or above ``CORRECTNESS_FLOOR``.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult`` with confidence 1.0 in all cases.

        Raises:
            Nothing.

        Example::

            schema = make_test_schema(correctness_estimate=0.3)
            result = CorrectnessFloorTest().run(schema)
            assert result.passed is False
        """
        try:
            est = schema.correctness_estimate

            if not math.isfinite(est):
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(est),
                    details=f"correctness_estimate={est} is not finite.",
                )

            if est < 0.0 or est > 1.0:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(est),
                    details=f"correctness_estimate={est} is outside valid range [0,1].",
                )

            if est < CORRECTNESS_FLOOR:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(est),
                    details=(
                        f"correctness_estimate={est:.4f} < floor={CORRECTNESS_FLOOR}. "
                        "Theorem is too speculative for the review pipeline."
                    ),
                )

            margin = est - CORRECTNESS_FLOOR
            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=1.0,
                counterexample=None,
                details=(
                    f"correctness_estimate={est:.4f} ≥ floor={CORRECTNESS_FLOOR} "
                    f"(margin={margin:.4f})."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"CorrectnessFloorTest raised an unexpected error: {exc}",
            )


class ObstructionReductionTest(FalsificationTest):
    """Check that the theorem actually reduces obstruction density.

    A theorem that does not reduce obstruction density — i.e., whose
    ``obstruction_reduction`` is zero or negative — contributes nothing
    to the ideation pipeline's primary goal.  This test enforces
    ``obstruction_reduction > OBSTRUCTION_REDUCTION_MIN``.

    Additionally, the test checks that ``proof_sketch.obstruction_ids`` is
    non-empty: a theorem that claims to reduce obstructions but does not
    name any specific obstruction IDs is internally inconsistent.

    Special cases
    -------------
    ``AXIOM_CANDIDATE`` theorems are exempt from the non-empty
    ``obstruction_ids`` requirement because axioms may address structural
    gaps that do not correspond to named obstructions.
    """

    def __init__(self) -> None:
        """Initialise ObstructionReductionTest.

        Returns:
            None.

        Example::

            test = ObstructionReductionTest()
        """
        super().__init__(
            "ObstructionReductionTest",
            "Checks that obstruction_reduction > 0 and obstruction_ids is non-empty.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Validate obstruction reduction metrics.

        Checks (1) that ``obstruction_reduction`` is finite and positive,
        and (2) that ``proof_sketch.obstruction_ids`` is non-empty unless
        the schema kind is ``AXIOM_CANDIDATE``.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult``.

        Raises:
            Nothing.

        Example::

            schema = make_test_schema(obstruction_reduction=0.0)
            result = ObstructionReductionTest().run(schema)
            assert result.passed is False
        """
        try:
            red = schema.obstruction_reduction

            if not math.isfinite(red):
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(red),
                    details=f"obstruction_reduction={red} is not finite.",
                )

            if red <= OBSTRUCTION_REDUCTION_MIN:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(red),
                    details=(
                        f"obstruction_reduction={red:.6f} ≤ 0. "
                        "This theorem does not reduce the obstruction field."
                    ),
                )

            sketch_obs = schema.proof_sketch.obstruction_ids
            if (
                not sketch_obs
                and schema.kind != TheoremKind.AXIOM_CANDIDATE
            ):
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.85,
                    counterexample=None,
                    details=(
                        "proof_sketch.obstruction_ids is empty but "
                        "obstruction_reduction > 0.  The theorem claims to "
                        "reduce obstructions but names none."
                    ),
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.95,
                counterexample=None,
                details=(
                    f"obstruction_reduction={red:.4f} > 0; "
                    f"obstruction_ids count={len(sketch_obs)}."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"ObstructionReductionTest raised an unexpected error: {exc}",
            )


class FormalStatementSyntaxTest(FalsificationTest):
    """Validate that the formal statement has minimal syntactic well-formedness.

    A formal statement that is empty, too short, or devoid of logical
    connectives is unlikely to be machine-parseable by the knowledge-graph
    ingestion layer.  This test applies lightweight syntactic heuristics:

    1. Length check: ``len(formal_statement) >= FORMAL_STMT_MIN_LENGTH``.
    2. Quantifier/connective check: at least one token from
       ``FORMAL_STMT_QUANTIFIER_TOKENS`` appears in the statement.
    3. Balanced parentheses check: the number of open parentheses equals
       the number of close parentheses.

    These checks are necessary but not sufficient for syntactic validity.
    Full parsing is delegated to the proof-assistant kernel.
    """

    def __init__(self) -> None:
        """Initialise FormalStatementSyntaxTest.

        Returns:
            None.

        Example::

            test = FormalStatementSyntaxTest()
        """
        super().__init__(
            "FormalStatementSyntaxTest",
            "Validates formal_statement for minimal syntactic well-formedness.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Check the formal statement for length, quantifiers, and balanced parens.

        Applies three sequential checks; returns on the first failure found.
        If all checks pass, returns a passing result.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult``.

        Raises:
            Nothing.

        Example::

            schema = make_test_schema(formal_statement="x")
            result = FormalStatementSyntaxTest().run(schema)
            assert result.passed is False
        """
        try:
            fs = schema.formal_statement

            # Check 1: length
            if len(fs) < FORMAL_STMT_MIN_LENGTH:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=repr(fs),
                    details=(
                        f"formal_statement length={len(fs)} < "
                        f"FORMAL_STMT_MIN_LENGTH={FORMAL_STMT_MIN_LENGTH}."
                    ),
                )

            # Check 2: quantifier/connective presence
            fs_lower = fs.lower()
            has_quantifier = any(tok in fs_lower for tok in FORMAL_STMT_QUANTIFIER_TOKENS)
            if not has_quantifier:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.80,
                    counterexample=None,
                    details=(
                        "formal_statement contains no recognised quantifier or logical "
                        f"connective. Expected one of: {FORMAL_STMT_QUANTIFIER_TOKENS}"
                    ),
                )

            # Check 3: balanced parentheses
            depth = 0
            for ch in fs:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if depth < 0:
                    return FalsificationResult(
                        test_name=self.name,
                        passed=False,
                        confidence=0.90,
                        counterexample="unmatched ')'",
                        details="formal_statement has unbalanced parentheses (extra ')').",
                    )
            if depth != 0:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.90,
                    counterexample=f"unclosed depth={depth}",
                    details=f"formal_statement has {depth} unclosed parenthes{'is' if depth == 1 else 'es'}.",
                )

            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.80,
                counterexample=None,
                details="formal_statement passed length, quantifier, and paren-balance checks.",
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"FormalStatementSyntaxTest raised an unexpected error: {exc}",
            )


class DependencyStrengthTest(FalsificationTest):
    """Reject schemas that declare near-zero dependency strengths.

    A ``TheoremDependency`` with strength at or below
    ``DEPENDENCY_STRENGTH_FLOOR`` is semantically absent: it inflates the
    edge count of the dependency graph without contributing logical weight.
    This test rejects such schemas to keep the knowledge graph sparse and
    meaningful.

    The test also checks that all strength values are in [0, 1] and finite,
    which are invariants of ``TheoremDependency`` that the constructor does
    not enforce (being a frozen dataclass with no ``__post_init__``).
    """

    def __init__(self) -> None:
        """Initialise DependencyStrengthTest.

        Returns:
            None.

        Example::

            test = DependencyStrengthTest()
        """
        super().__init__(
            "DependencyStrengthTest",
            f"Checks that no dependency has strength ≤ {DEPENDENCY_STRENGTH_FLOOR}.",
        )

    def run(self, schema: ResearchAssistanceTheoremSchema) -> FalsificationResult:
        """Inspect each TheoremDependency for strength validity.

        Iterates over all dependencies, checking:
          1. ``strength`` is finite.
          2. ``strength`` is in [0, 1].
          3. ``strength`` > ``DEPENDENCY_STRENGTH_FLOOR``.

        Returns the first failure found; if no failures, returns passing.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A ``FalsificationResult``.

        Raises:
            Nothing.

        Example::

            dep = TheoremDependency("t1", TheoremKind.LEMMA, 0.0, False)
            schema = make_test_schema(dependencies=(dep,))
            result = DependencyStrengthTest().run(schema)
            assert result.passed is False
        """
        try:
            if not schema.dependencies:
                return FalsificationResult(
                    test_name=self.name,
                    passed=True,
                    confidence=1.0,
                    counterexample=None,
                    details="No dependencies declared; strength check vacuously passes.",
                )

            weak_deps = []
            invalid_deps = []

            for dep in schema.dependencies:
                s = dep.strength
                if not math.isfinite(s) or s < 0.0 or s > 1.0:
                    invalid_deps.append((dep.dep_id, s))
                elif s <= DEPENDENCY_STRENGTH_FLOOR:
                    weak_deps.append((dep.dep_id, s))

            if invalid_deps:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=1.0,
                    counterexample=str(invalid_deps),
                    details=f"Dependencies with invalid strength values: {invalid_deps}",
                )

            if weak_deps:
                return FalsificationResult(
                    test_name=self.name,
                    passed=False,
                    confidence=0.90,
                    counterexample=str(weak_deps),
                    details=(
                        f"Dependencies with strength ≤ {DEPENDENCY_STRENGTH_FLOOR}: "
                        f"{weak_deps}.  These edges carry no semantic weight."
                    ),
                )

            min_strength = min(d.strength for d in schema.dependencies)
            return FalsificationResult(
                test_name=self.name,
                passed=True,
                confidence=0.95,
                counterexample=None,
                details=(
                    f"All {len(schema.dependencies)} dependencies have "
                    f"strength > {DEPENDENCY_STRENGTH_FLOOR} "
                    f"(min observed: {min_strength:.4f})."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return FalsificationResult(
                test_name=self.name,
                passed=False,
                confidence=0.10,
                counterexample=None,
                details=f"DependencyStrengthTest raised an unexpected error: {exc}",
            )


# ===========================================================================
# FalsificationSuite
# ===========================================================================


class FalsificationSuite:
    """A composable collection of falsification tests for theorem candidates.

    The ``FalsificationSuite`` is the top-level orchestrator for the
    falsification phase of the research-assistance pipeline.  It holds an
    ordered list of ``FalsificationTest`` instances and can run them all
    against a single ``ResearchAssistanceTheoremSchema``, aggregating
    results into a verdict and a human-readable report.

    Design philosophy
    -----------------
    Tests are run in the order they were added.  The suite does **not**
    short-circuit on the first failure (unless short_circuit mode is added
    in a future version) because reviewers benefit from seeing all detected
    defects at once.  The aggregate confidence is the geometric mean of all
    passing-test confidences; failing tests contribute their (1 - confidence)
    factor to a "defect weight" that reduces the aggregate.

    Verdict semantics
    -----------------
    ``verdict`` returns ``(overall_passed, aggregate_confidence)``.
    ``overall_passed`` is True iff **all** tests passed.  A suite with no
    tests vacuously passes with confidence 0.0.

    Thread safety
    -------------
    The suite is not thread-safe.  If running tests in parallel is desired,
    create one suite per thread.

    Example::

        suite = build_default_suite()
        results = suite.run_all(schema)
        passed, confidence = suite.verdict(schema)
        print(suite.report(schema))
    """

    def __init__(self) -> None:
        """Initialise an empty FalsificationSuite.

        Returns:
            None.

        Example::

            suite = FalsificationSuite()
            assert len(suite._tests) == 0
        """
        # copilot: internal test list; preserve insertion order
        self._tests: list[FalsificationTest] = []

    def add_test(self, test: FalsificationTest) -> None:
        """Append a falsification test to the suite.

        Args:
            test: A ``FalsificationTest`` instance to add.  Duplicate names
                are allowed (both tests will run) but not recommended.

        Returns:
            None.

        Raises:
            TypeError: If ``test`` is not a ``FalsificationTest`` instance.

        Example::

            suite = FalsificationSuite()
            suite.add_test(TautologyTest())
            assert len(suite._tests) == 1
        """
        if not isinstance(test, FalsificationTest):
            raise TypeError(
                f"Expected FalsificationTest, got {type(test).__name__}."
            )
        self._tests.append(test)

    def run_all(
        self, schema: ResearchAssistanceTheoremSchema
    ) -> list[FalsificationResult]:
        """Run every test in the suite against ``schema``.

        Executes each test in insertion order.  All tests are run regardless
        of prior failures.  Each test's ``run`` method is expected to catch
        its own exceptions and return a valid ``FalsificationResult``.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A list of ``FalsificationResult`` instances, one per test, in
            the same order as the tests were added.

        Raises:
            Nothing (individual test exceptions are caught by the tests).

        Example::

            results = suite.run_all(schema)
            failures = [r for r in results if not r.passed]
        """
        return [test.run(schema) for test in self._tests]

    def verdict(
        self, schema: ResearchAssistanceTheoremSchema
    ) -> tuple[bool, float]:
        """Compute the aggregate verdict for ``schema``.

        Runs all tests (via ``run_all``) and computes:
        * ``overall_passed``: True iff every test passed.
        * ``aggregate_confidence``: Geometric mean of all test confidences,
          weighted by pass/fail status.  Failing tests contribute their
          confidence as a negative signal.

        Args:
            schema: The theorem candidate to evaluate.

        Returns:
            A tuple ``(overall_passed, aggregate_confidence)`` where
            ``overall_passed`` is a bool and ``aggregate_confidence`` is
            a float in [0, 1].

        Raises:
            Nothing.

        Example::

            passed, conf = suite.verdict(schema)
            if passed:
                submit_for_review(schema)
        """
        results = self.run_all(schema)
        if not results:
            return True, 0.0

        overall_passed = all(r.passed for r in results)

        # Geometric mean of confidences, penalising failures
        log_sum = 0.0
        for r in results:
            c = _clamp(r.confidence, 1e-9, 1.0 - 1e-9)
            if r.passed:
                log_sum += math.log(c)
            else:
                log_sum += math.log(1.0 - c)

        raw = math.exp(log_sum / len(results))
        aggregate_confidence = _clamp(raw, 0.0, 1.0)
        return overall_passed, aggregate_confidence

    def report(self, schema: ResearchAssistanceTheoremSchema) -> str:
        """Produce a human-readable falsification report for ``schema``.

        Runs all tests and formats their results into a multi-section
        plain-text report.  The report includes a header with the theorem
        ID and kind, a per-test table, and a summary section with the
        overall verdict and aggregate confidence.

        Args:
            schema: The theorem candidate to report on.

        Returns:
            A formatted multi-line string suitable for printing or logging.

        Raises:
            Nothing.

        Example::

            print(suite.report(schema))
        """
        results = self.run_all(schema)
        overall_passed, agg_conf = self.verdict(schema)

        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("FALSIFICATION REPORT")
        lines.append(f"  theorem_id : {schema.theorem_id}")
        lines.append(f"  kind       : {schema.kind.value}")
        lines.append(f"  pack_id    : {schema.pack_id}")
        lines.append(f"  submitter  : {schema.submitter_id}")
        lines.append("-" * 72)
        lines.append(f"{'TEST':<36} {'PASS':>6} {'CONF':>6}  DETAILS")
        lines.append("-" * 72)

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            conf_str = f"{r.confidence:.2f}"
            # Truncate details to 40 chars for the table
            detail_short = r.details[:40] + "…" if len(r.details) > 40 else r.details
            lines.append(
                f"{r.test_name:<36} {status:>6} {conf_str:>6}  {detail_short}"
            )
            if r.counterexample:
                lines.append(f"  {'':36} counterexample: {r.counterexample[:60]}")

        lines.append("=" * 72)
        verdict_str = "PASSED" if overall_passed else "FAILED"
        lines.append(
            f"OVERALL VERDICT: {verdict_str}  |  aggregate confidence: {agg_conf:.4f}"
        )
        lines.append("=" * 72)
        return "\n".join(lines)


# ===========================================================================
# Registry
# ===========================================================================


class ResearchAssistanceTheoremRegistry:
    """In-memory registry for managing collections of theorem candidates.

    The ``ResearchAssistanceTheoremRegistry`` provides a lightweight
    key-value store for ``ResearchAssistanceTheoremSchema`` instances,
    with convenience query methods for filtering by pack, kind, and
    quality metrics.  It is intended for use within a single ideation
    session; persistence is handled by external storage adapters.

    Concurrency
    -----------
    The registry is not thread-safe.  Use external locking if accessed
    from multiple threads.

    Capacity
    --------
    The registry has no hard capacity limit.  ``REGISTRY_DEFAULT_CAPACITY``
    is a soft hint for pre-allocation; exceeding it is allowed.

    Example::

        registry = ResearchAssistanceTheoremRegistry()
        registry.register(schema)
        found = registry.lookup("thm:abc123")
        ranked = registry.quality_ranking()
    """

    def __init__(self) -> None:
        """Initialise an empty registry.

        Returns:
            None.

        Example::

            reg = ResearchAssistanceTheoremRegistry()
        """
        # copilot: primary index by theorem_id
        self._store: dict[str, ResearchAssistanceTheoremSchema] = {}

    def register(self, schema: ResearchAssistanceTheoremSchema) -> None:
        """Add or replace a theorem schema in the registry.

        If a schema with the same ``theorem_id`` already exists, it is
        silently replaced.  Callers that need idempotency checking should
        call ``lookup`` before ``register``.

        Args:
            schema: The ``ResearchAssistanceTheoremSchema`` to register.

        Returns:
            None.

        Raises:
            TypeError: If ``schema`` is not a ``ResearchAssistanceTheoremSchema``.

        Example::

            registry.register(schema)
            assert registry.lookup(schema.theorem_id) == schema
        """
        if not isinstance(schema, ResearchAssistanceTheoremSchema):
            raise TypeError(
                f"Expected ResearchAssistanceTheoremSchema, got {type(schema).__name__}."
            )
        self._store[schema.theorem_id] = schema

    def lookup(self, theorem_id: str) -> Optional[ResearchAssistanceTheoremSchema]:
        """Retrieve a schema by its theorem_id.

        Args:
            theorem_id: The unique identifier to look up.

        Returns:
            The matching ``ResearchAssistanceTheoremSchema``, or ``None`` if
            no schema with this ID is registered.

        Raises:
            Nothing.

        Example::

            schema = registry.lookup("thm:abc123")
            if schema is None:
                print("Not found.")
        """
        return self._store.get(theorem_id)

    def by_pack(self, pack_id: str) -> list[ResearchAssistanceTheoremSchema]:
        """Return all schemas belonging to a given pack.

        Args:
            pack_id: The pack identifier to filter on.

        Returns:
            A list of ``ResearchAssistanceTheoremSchema`` instances with
            ``schema.pack_id == pack_id``, in insertion order.

        Raises:
            Nothing.

        Example::

            schemas = registry.by_pack("pack:topology-basics")
        """
        return [s for s in self._store.values() if s.pack_id == pack_id]

    def by_kind(self, kind: TheoremKind) -> list[ResearchAssistanceTheoremSchema]:
        """Return all schemas of a given theorem kind.

        Args:
            kind: The ``TheoremKind`` to filter on.

        Returns:
            A list of ``ResearchAssistanceTheoremSchema`` instances with
            ``schema.kind == kind``, in insertion order.

        Raises:
            Nothing.

        Example::

            conjectures = registry.by_kind(TheoremKind.CONJECTURE)
        """
        return [s for s in self._store.values() if s.kind == kind]

    def obstruction_coverage(self) -> dict[str, list[str]]:
        """Map each obstruction ID to the theorem IDs that address it.

        Iterates over all registered schemas and collects the
        ``proof_sketch.obstruction_ids`` sets, building an inverted index
        from obstruction ID → list of theorem IDs.

        Returns:
            A ``dict[str, list[str]]`` mapping obstruction IDs to lists of
            theorem IDs that claim to resolve them.

        Raises:
            Nothing.

        Example::

            coverage = registry.obstruction_coverage()
            for obs_id, thm_ids in coverage.items():
                print(f"{obs_id}: {thm_ids}")
        """
        coverage: dict[str, list[str]] = {}
        for schema in self._store.values():
            for obs_id in schema.proof_sketch.obstruction_ids:
                coverage.setdefault(obs_id, []).append(schema.theorem_id)
        return coverage

    def quality_ranking(self) -> list[ResearchAssistanceTheoremSchema]:
        """Return all schemas sorted by a composite quality score (descending).

        The composite quality score is defined as:

            quality = (correctness_estimate * 0.4
                       + novelty_score * 0.3
                       + obstruction_reduction * 0.3)

        This weighting reflects the pipeline's priorities: correctness is
        most important, followed by novelty (we want new results), and then
        obstruction coverage.  Future versions may learn these weights from
        reviewer feedback.

        Returns:
            A list of all registered schemas, sorted by composite quality
            score in descending order.

        Raises:
            Nothing.

        Example::

            top5 = registry.quality_ranking()[:5]
        """
        def _quality(s: ResearchAssistanceTheoremSchema) -> float:
            return (
                s.correctness_estimate * 0.4
                + s.novelty_score * 0.3
                + s.obstruction_reduction * 0.3
            )

        return sorted(self._store.values(), key=_quality, reverse=True)


# ===========================================================================
# Helper functions
# ===========================================================================


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (seconds since epoch).

    This thin wrapper exists so that tests can monkeypatch ``_utcnow``
    without needing to patch the standard library directly.

    Returns:
        A positive float representing the current UTC time.

    Raises:
        Nothing.

    Example::

        ts = _utcnow()
        assert ts > 0
    """
    return time.time()


def _uid(prefix: str = "thm") -> str:
    """Generate a unique identifier with an optional human-readable prefix.

    Produces a string of the form ``"<prefix>:<uuid4>"`` using
    ``uuid.uuid4()`` for the unique suffix.

    Args:
        prefix: A short string prepended to the UUID, default ``"thm"``.

    Returns:
        A unique string identifier.

    Raises:
        Nothing.

    Example::

        uid = _uid("lemma")
        assert uid.startswith("lemma:")
    """
    return f"{prefix}:{uuid.uuid4()}"


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a float value to the closed interval [lo, hi].

    Args:
        value: The value to clamp.
        lo: The lower bound (inclusive).
        hi: The upper bound (inclusive).

    Returns:
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.

    Raises:
        Nothing.

    Example::

        assert _clamp(-0.5, 0.0, 1.0) == 0.0
        assert _clamp(1.5, 0.0, 1.0) == 1.0
        assert _clamp(0.7, 0.0, 1.0) == 0.7
    """
    return max(lo, min(hi, value))


def build_default_suite() -> FalsificationSuite:
    """Construct a ``FalsificationSuite`` pre-loaded with all 8 standard tests.

    The standard suite is the recommended starting point for any caller
    that does not need a customised set of tests.  Tests are added in
    severity order: the most critical checks (correctness, circular
    dependencies) come first so that the report is easy to scan.

    Returns:
        A ``FalsificationSuite`` with the following tests registered in order:
          1. CorrectnessFloorTest
          2. CircularDependencyTest
          3. ObstructionReductionTest
          4. DependencyStrengthTest
          5. FormalStatementSyntaxTest
          6. NoveltySanityTest
          7. ScopeOverflowTest
          8. TautologyTest

    Raises:
        Nothing.

    Example::

        suite = build_default_suite()
        passed, conf = suite.verdict(schema)
    """
    suite = FalsificationSuite()
    suite.add_test(CorrectnessFloorTest())
    suite.add_test(CircularDependencyTest())
    suite.add_test(ObstructionReductionTest())
    suite.add_test(DependencyStrengthTest())
    suite.add_test(FormalStatementSyntaxTest())
    suite.add_test(NoveltySanityTest())
    suite.add_test(ScopeOverflowTest())
    suite.add_test(TautologyTest())
    return suite


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    # copilot: smoke-test — exercises the full pipeline end-to-end

    print("=== JuGeo Research Assistance — Theorem Schema Smoke Test ===\n")

    # 1. Create a TheoremDependency
    dep_heine_borel = TheoremDependency(
        dep_id="thm:heine-borel",
        dep_kind=TheoremKind.THEOREM,
        strength=0.90,
        is_circular=False,
    )
    dep_metric_space = TheoremDependency(
        dep_id="lem:metric-space-basics",
        dep_kind=TheoremKind.LEMMA,
        strength=0.55,
        is_circular=False,
    )
    print(f"[1] TheoremDependency created: {dep_heine_borel.dep_id!r} "
          f"(strength={dep_heine_borel.strength})")

    # 2. Create a ProofSketch
    sketch = ProofSketch(
        strategy=ProofStrategy.DIRECT,
        sketch_text=(
            "Let (X, d) be a compact metric space and let (x_n) be a sequence in X. "
            "By compactness, every open cover of X has a finite subcover.  "
            "Apply Heine-Borel to show that the sequence has a convergent subsequence."
        ),
        estimated_complexity=14,
        confidence=0.88,
        obstruction_ids=("obs:seq-compact-gap", "obs:missing-bolzano-weierstrass"),
    )
    print(f"[2] ProofSketch created: strategy={sketch.strategy.value!r}, "
          f"complexity={sketch.estimated_complexity}")

    # 3. Create a ResearchAssistanceTheoremSchema
    schema = ResearchAssistanceTheoremSchema(
        theorem_id=_uid("thm"),
        kind=TheoremKind.THEOREM,
        statement=(
            "Every compact metric space is sequentially compact: "
            "every sequence has a convergent subsequence."
        ),
        formal_statement=(
            "∀ X. metric_space(X) ∧ compact(X) → "
            "(∀ (x : ℕ → X). ∃ (φ : ℕ → ℕ). strictly_increasing(φ) ∧ "
            "converges(x ∘ φ))"
        ),
        pack_id="pack:topology-basics",
        dependencies=(dep_heine_borel, dep_metric_space),
        proof_sketch=sketch,
        novelty_score=0.68,
        correctness_estimate=0.91,
        obstruction_reduction=0.22,
        submitted_at=_utcnow(),
        submitter_id="agent:ideation-smoke-test",
    )
    print(f"[3] ResearchAssistanceTheoremSchema created: {schema.theorem_id!r}")
    print(f"    kind={schema.kind.value}, pack={schema.pack_id}")
    print(f"    correctness={schema.correctness_estimate}, novelty={schema.novelty_score}")

    # 4. Build a FalsificationSuite and run it
    print("\n[4] Building default FalsificationSuite and running all tests …\n")
    suite = build_default_suite()
    results = suite.run_all(schema)

    # 5. Print results
    print(suite.report(schema))
    passed, agg_conf = suite.verdict(schema)
    print(f"\nSmoke-test verdict: passed={passed}, aggregate_confidence={agg_conf:.4f}")

    # 6. Use ResearchAssistanceTheoremRegistry
    print("\n[6] Registering schema in ResearchAssistanceTheoremRegistry …")
    registry = ResearchAssistanceTheoremRegistry()
    registry.register(schema)

    # Register a second schema (conjecture) to populate the registry
    dep2 = TheoremDependency(
        dep_id="thm:continuous-image-compact",
        dep_kind=TheoremKind.THEOREM,
        strength=0.75,
        is_circular=False,
    )
    sketch2 = ProofSketch(
        strategy=ProofStrategy.CONTRADICTION,
        sketch_text="Assume the image is not compact; derive a contradiction via Heine-Borel.",
        estimated_complexity=7,
        confidence=0.77,
        obstruction_ids=("obs:image-compact-missing",),
    )
    schema2 = ResearchAssistanceTheoremSchema(
        theorem_id=_uid("conj"),
        kind=TheoremKind.CONJECTURE,
        statement="The continuous image of a compact space under a Hausdorff map is compact.",
        formal_statement="∀ f X Y. continuous(f) ∧ compact(X) ∧ hausdorff(Y) → compact(f(X))",
        pack_id="pack:topology-basics",
        dependencies=(dep2,),
        proof_sketch=sketch2,
        novelty_score=0.55,
        correctness_estimate=0.80,
        obstruction_reduction=0.10,
        submitted_at=_utcnow(),
        submitter_id="agent:ideation-smoke-test",
    )
    registry.register(schema2)

    print(f"    Registered {schema.theorem_id!r} and {schema2.theorem_id!r}")
    print(f"    by_pack('pack:topology-basics'): {len(registry.by_pack('pack:topology-basics'))} schemas")
    print(f"    by_kind(CONJECTURE): {len(registry.by_kind(TheoremKind.CONJECTURE))} schemas")

    coverage = registry.obstruction_coverage()
    print(f"    obstruction_coverage keys: {list(coverage.keys())}")

    ranked = registry.quality_ranking()
    print(f"    quality_ranking top theorem_id: {ranked[0].theorem_id!r}")

    print("\n=== Smoke test completed successfully. ===")
