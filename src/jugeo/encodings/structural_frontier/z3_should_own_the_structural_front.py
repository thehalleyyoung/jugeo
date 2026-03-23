"""Z3 ownership of the structural frontier.

This module establishes that Z3 is the correct and canonical decision procedure
for the *structural* (type-theoretic) fragment of proof obligations produced by
the Jugeo encoding pipeline.  The central insight is that not all obligations are
equal: some are *structural* — they arise from type-checking, subtyping,
containment, or finite-domain enumeration — while others are *semantic* — they
involve external oracles, infinite arithmetic domains, higher-order properties,
or black-box predicates that Z3 fundamentally cannot discharge.

The Structural Frontier
-----------------------
The structural frontier is the boundary between what Z3 can decide and what it
cannot.  Obligations that lie inside the frontier (i.e., those that are purely
propositional, belong to decidable SMT fragments such as QF_UF, QF_LIA, QF_BV,
QF_DT, or QF_AUFLIA) should be routed *directly* to Z3.  Obligations that lie
outside the frontier — because they involve higher-order quantification,
semantic side-conditions from external specification systems, or properties of
infinite streams — must be escalated to oracle handlers or hybrid solvers.

Why Z3 Should Own the Structural Front
---------------------------------------
Z3 is a complete decision procedure for a large class of quantifier-free first-
order theories.  For structural obligations the completeness guarantee is
valuable: if Z3 returns ``unsat`` on a validity query we know for certain that
the structural property holds; if it returns ``sat`` we have a concrete
counter-model.  This precision is impossible to achieve with approximate or
semantic solvers.

Semantic obligations, by contrast, involve properties that are undecidable in
general, or that require domain-specific reasoning engines (e.g., a machine-
learning model, a symbolic execution engine, or a human-in-the-loop oracle).
Routing semantic obligations to Z3 is not merely wasteful — it is *wrong*,
because Z3 may return spurious ``sat`` or ``unknown`` results on obligations
it was never designed to handle.

The Decidability Score
-----------------------
Each obligation receives a *decidability score* in [0.0, 1.0].  A score of 1.0
means that every heuristic indicator points to a cleanly decidable structural
obligation.  A score of 0.0 means the obligation exhibits every marker of a
semantic or undecidable problem.  Scores in between indicate hybrid obligations
that should be partially handled by Z3 (for the structural sub-goals) and
partially escalated.

Structural Depth
-----------------
Structural depth is a proxy for the nesting complexity of the type-theoretic
term.  Shallow obligations (depth ≤ 3) are almost always decidable.  Deep
obligations (depth ≥ 10) may trigger quantifier-alternation or non-linear
arithmetic that pushes them out of Z3's decidable core.

Integration with the Jugeo Pipeline
-------------------------------------
This module is designed to be imported at encoding time.  The
``Z3OwnStructuralFrontierCoordinator`` is the main entry point: callers
register SMT-LIB2 obligation strings, receive ``Z3OwnStructuralFrontierWitness``
objects back, and route based on the ``ownership_kind`` field.  No external
dependencies are required — all imports from ``jugeo.solver`` and
``jugeo.encodings`` are guarded with try/except blocks so that the module can
be used in isolation for testing or analysis without a full Jugeo installation.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, SolveOutcome
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.fragments import classify_fragment, Fragment
    _FRAGMENTS_AVAILABLE = True
except ImportError:
    _FRAGMENTS_AVAILABLE = False
    classify_fragment = None  # type: ignore[assignment]
    Fragment = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.structural_frontier.models import (
        DecidabilityClass, StructuralFrontier, FrontierBoundary,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False
    DecidabilityClass = None  # type: ignore[assignment,misc]
    StructuralFrontier = None  # type: ignore[assignment,misc]
    FrontierBoundary = None  # type: ignore[assignment,misc]

try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants describing known fragments and patterns
# ---------------------------------------------------------------------------

# copilot: these keywords indicate obligations that belong to decidable SMT
# fragments and should therefore be owned by Z3.
_STRUCTURAL_Z3_KEYWORDS: dict[str, str] = {
    "declare-sort": "QF_UF — uninterpreted sorts are fine for Z3",
    "declare-fun": "uninterpreted function symbols — Z3 handles QF_UF",
    "declare-const": "constant declarations are always safe for Z3",
    "define-fun": "macro definitions stay within decidable fragments",
    "assert": "plain assertions without quantifiers are structural",
    "check-sat": "satisfiability queries are the core Z3 operation",
    "get-model": "model extraction is supported by Z3",
    "bvadd": "bitvector addition is in QF_BV — fully decidable",
    "bvmul": "bitvector multiplication is decidable in QF_BV",
    "store": "array store is in QF_AX / QF_AUFLIA — Z3 handles it",
    "select": "array select is in QF_AX — Z3 handles it",
    "ite": "if-then-else is supported in all Z3 fragments",
    "distinct": "disequality constraints are structural",
}

# copilot: these patterns suggest semantic obligations that CANNOT be reliably
# discharged by Z3 alone and must be escalated to an oracle or hybrid solver.
_SEMANTIC_ORACLE_PATTERNS: dict[str, str] = {
    "oracle-call": "explicit oracle invocation — not Z3 territory",
    "higher-order": "higher-order predicate — Z3 is first-order only",
    "stream-property": "property of an infinite stream — undecidable in general",
    "ml-predicate": "machine-learning predicate — semantic, not structural",
    "external-ref": "reference to an external specification system",
    "coinductive": "coinductive property — requires special coinduction rules",
    "divergence": "divergence/termination property — generally undecidable",
    "probabilistic": "probabilistic assertion — requires probabilistic logic",
}

# copilot: non-linear arithmetic patterns push obligations outside QF_LIA
# and into QF_NIA, which Z3 handles incompletely.
_NONLINEAR_PATTERNS: list[str] = [
    "(* ", "(/ ", "(mod ", "(rem ", "(div ",
    "nonlinear", "polynomial", "transcendental",
    "(sin ", "(cos ", "(tan ", "(exp ", "(log ",
    "(sqrt ", "(power ",
]

# copilot: quantifier patterns push obligations into the quantified fragment,
# which is generally undecidable (though Z3 uses heuristic instantiation).
_QUANTIFIER_PATTERNS: list[str] = [
    "(forall ", "(exists ", "(lambda ",
    "quantifier", "forall-elim", "exists-intro",
]

# copilot: these are the fragment names (in SMT-LIB2 logic string format)
# that Z3 fully and completely decides.
_FULLY_DECIDABLE_LOGICS: list[str] = [
    "QF_UF", "QF_LIA", "QF_LRA", "QF_BV", "QF_AX",
    "QF_AUFLIA", "QF_AUFLIRA", "QF_DT", "QF_IDL", "QF_RDL",
    "QF_UFLIA", "QF_UFLRA", "QF_UFBV", "QF_UFIDL",
    "PROP",  # purely propositional
]

# ============================== ownership kinds ==============================


class StructuralOwnershipKind(Enum):
    """Kinds of obligation ownership with respect to Z3 and the structural frontier.

    This enumeration captures the five possible ownership states for a proof
    obligation as it flows through the Jugeo structural-frontier analysis
    pipeline.  The ownership kind determines how the downstream routing logic
    treats the obligation: whether to send it directly to Z3, escalate it to
    an oracle, split it for hybrid treatment, or hold it pending further
    classification.

    Members
    -------
    STRUCTURAL_Z3 :
        The obligation belongs entirely within the structural (type-theoretic)
        fragment that Z3 can decide completely.  Z3 is the sole owner and its
        answer (sat/unsat/unknown) is authoritative.
    SEMANTIC_ORACLE :
        The obligation involves semantic properties that Z3 cannot reliably
        discharge.  An external oracle, SMT extension, or human-in-the-loop
        review process must own this obligation.
    HYBRID_PARTIAL :
        The obligation has both structural and semantic sub-goals.  Z3 can
        discharge the structural sub-goals, but an oracle must be consulted
        for the semantic remainder.  The obligation is split before solving.
    ESCALATED :
        The obligation was initially classified as structural but a previous
        Z3 solve attempt returned ``unknown`` or timed out, so it has been
        escalated to a higher-priority solver or reviewer.
    UNDETERMINED :
        Classification has not yet been performed, or the classifier was
        unable to reach a confident decision.  Obligations in this state
        must be re-analyzed before being routed.

    Notes
    -----
    The escalation priority ordering is::

        STRUCTURAL_Z3 (0) < HYBRID_PARTIAL (1) < SEMANTIC_ORACLE (2)
        < ESCALATED (3) < UNDETERMINED (5)

    Higher priority values indicate more expensive or uncertain handling.

    Examples
    --------
    >>> kind = StructuralOwnershipKind.STRUCTURAL_Z3
    >>> kind.is_z3_owned()
    True
    >>> kind.escalation_priority()
    0
    >>> kind.smt2_ownership_marker()
    '; ownership: STRUCTURAL_Z3'
    """

    STRUCTURAL_Z3 = auto()
    SEMANTIC_ORACLE = auto()
    HYBRID_PARTIAL = auto()
    ESCALATED = auto()
    UNDETERMINED = auto()

    def is_z3_owned(self) -> bool:
        """Return True iff this ownership kind is STRUCTURAL_Z3.

        Returns
        -------
        bool
            True only when ``self`` is ``STRUCTURAL_Z3``.

        Examples
        --------
        >>> StructuralOwnershipKind.STRUCTURAL_Z3.is_z3_owned()
        True
        >>> StructuralOwnershipKind.SEMANTIC_ORACLE.is_z3_owned()
        False
        """
        # copilot: only STRUCTURAL_Z3 passes directly to the Z3 solver
        return self is StructuralOwnershipKind.STRUCTURAL_Z3

    def is_oracle_owned(self) -> bool:
        """Return True iff this ownership kind is SEMANTIC_ORACLE.

        Returns
        -------
        bool
            True only when ``self`` is ``SEMANTIC_ORACLE``.

        Examples
        --------
        >>> StructuralOwnershipKind.SEMANTIC_ORACLE.is_oracle_owned()
        True
        >>> StructuralOwnershipKind.STRUCTURAL_Z3.is_oracle_owned()
        False
        """
        # copilot: oracle-owned obligations never go to Z3
        return self is StructuralOwnershipKind.SEMANTIC_ORACLE

    def is_hybrid(self) -> bool:
        """Return True iff this ownership kind is HYBRID_PARTIAL.

        Returns
        -------
        bool
            True only when ``self`` is ``HYBRID_PARTIAL``.

        Examples
        --------
        >>> StructuralOwnershipKind.HYBRID_PARTIAL.is_hybrid()
        True
        """
        # copilot: hybrid obligations are split: structural sub-goals to Z3,
        # semantic sub-goals to the oracle
        return self is StructuralOwnershipKind.HYBRID_PARTIAL

    def is_escalated(self) -> bool:
        """Return True iff this ownership kind is ESCALATED.

        Returns
        -------
        bool
            True only when ``self`` is ``ESCALATED``.
        """
        return self is StructuralOwnershipKind.ESCALATED

    def smt2_ownership_marker(self) -> str:
        """Return an SMT-LIB2 comment string annotating the ownership kind.

        The returned string is a valid SMT-LIB2 comment (starts with ``;``)
        and can be prepended to an obligation's SMT-LIB2 representation to
        record how it was classified.

        Returns
        -------
        str
            SMT-LIB2 comment of the form ``; ownership: <KIND_NAME>``.

        Examples
        --------
        >>> StructuralOwnershipKind.STRUCTURAL_Z3.smt2_ownership_marker()
        '; ownership: STRUCTURAL_Z3'
        >>> StructuralOwnershipKind.ESCALATED.smt2_ownership_marker()
        '; ownership: ESCALATED'
        """
        # copilot: embed ownership metadata directly in the SMT2 output so
        # downstream tools can parse it without re-running classification
        return f"; ownership: {self.name}"

    def escalation_priority(self) -> int:
        """Return an integer priority for escalation ordering.

        Lower values indicate cheaper, more confident handling.  Higher values
        indicate that the obligation is expensive or uncertain.

        Returns
        -------
        int
            0 for STRUCTURAL_Z3, 1 for HYBRID_PARTIAL, 2 for SEMANTIC_ORACLE,
            3 for ESCALATED, 5 for UNDETERMINED.

        Examples
        --------
        >>> StructuralOwnershipKind.STRUCTURAL_Z3.escalation_priority()
        0
        >>> StructuralOwnershipKind.UNDETERMINED.escalation_priority()
        5
        """
        # copilot: numeric priority makes it easy to sort obligations by
        # handling cost in the routing layer
        _priority_map = {
            StructuralOwnershipKind.STRUCTURAL_Z3: 0,
            StructuralOwnershipKind.HYBRID_PARTIAL: 1,
            StructuralOwnershipKind.SEMANTIC_ORACLE: 2,
            StructuralOwnershipKind.ESCALATED: 3,
            StructuralOwnershipKind.UNDETERMINED: 5,
        }
        return _priority_map[self]

    def severity_default(self) -> str:
        """Return a default severity label for this ownership kind.

        The severity label is used by reporting and alerting systems to
        communicate the urgency with which unresolved obligations of this kind
        should be investigated.

        Returns
        -------
        str
            One of ``"low"``, ``"medium"``, ``"high"``, or ``"critical"``.

        Examples
        --------
        >>> StructuralOwnershipKind.STRUCTURAL_Z3.severity_default()
        'low'
        >>> StructuralOwnershipKind.UNDETERMINED.severity_default()
        'critical'
        """
        # copilot: structural obligations handled by Z3 are low-severity since
        # Z3 gives a definitive answer; undetermined obligations are critical
        # because they are blocking
        _severity_map = {
            StructuralOwnershipKind.STRUCTURAL_Z3: "low",
            StructuralOwnershipKind.HYBRID_PARTIAL: "medium",
            StructuralOwnershipKind.SEMANTIC_ORACLE: "high",
            StructuralOwnershipKind.ESCALATED: "high",
            StructuralOwnershipKind.UNDETERMINED: "critical",
        }
        return _severity_map[self]


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class Z3OwnStructuralFrontierWitness:
    """Immutable witness that Z3 owns (or does not own) a given obligation.

    A ``Z3OwnStructuralFrontierWitness`` is the canonical record produced by
    the structural-frontier analysis pipeline for a single SMT-LIB2 obligation
    string.  It captures:

    * The original SMT-LIB2 obligation text (``obligation_smt``).
    * The inferred ownership kind (``ownership_kind``).
    * A heuristic *decidability score* in [0.0, 1.0] indicating how confident
      the classifier is that the obligation belongs to a decidable Z3 fragment.
    * The structural depth of the obligation term, which correlates with the
      complexity of the type-theoretic reasoning required.
    * A human-readable copilot label for display in IDE integrations.
    * A rationale string explaining *why* this ownership decision was made.

    The witness is *frozen* (immutable) so that it can be safely cached,
    hashed, and stored in sets or as dict keys.

    Parameters
    ----------
    witness_id : str
        A unique identifier for this witness record, typically a UUID4 string.
    obligation_smt : str
        The full SMT-LIB2 text of the obligation being classified.
    ownership_kind : StructuralOwnershipKind
        The inferred ownership kind (Z3, oracle, hybrid, escalated, or
        undetermined).
    decidability_score : float
        A score in [0.0, 1.0].  1.0 means fully decidable by Z3; 0.0 means
        the obligation exhibits every marker of a semantic/undecidable problem.
    structural_depth : int
        The nesting depth of the deepest sub-expression in the obligation,
        computed by counting parenthesis depth in the SMT-LIB2 text.
    copilot_label : str
        A short human-readable label suitable for display in IDE copilot hints.
    created_at : float
        Unix timestamp (from ``time.time()``) at which this witness was created.
    rationale : str
        A free-text explanation of why the ownership decision was made.

    Examples
    --------
    >>> w = Z3OwnStructuralFrontierWitness(
    ...     witness_id="abc123",
    ...     obligation_smt="(assert (= x 1))",
    ...     ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
    ...     decidability_score=0.95,
    ...     structural_depth=2,
    ...     copilot_label="equality constraint",
    ...     created_at=time.time(),
    ...     rationale="No quantifiers, no non-linear arithmetic.",
    ... )
    >>> w.is_z3_owned()
    True
    >>> w.fingerprint()  # doctest: +SKIP
    'a1b2c3...'
    """

    witness_id: str
    obligation_smt: str
    ownership_kind: StructuralOwnershipKind
    decidability_score: float
    structural_depth: int
    copilot_label: str
    created_at: float
    rationale: str

    def is_z3_owned(self) -> bool:
        """Return True iff the ownership kind is STRUCTURAL_Z3.

        Returns
        -------
        bool
            Delegates to ``self.ownership_kind.is_z3_owned()``.
        """
        # copilot: convenience accessor so callers don't need to inspect the
        # enum member directly
        return self.ownership_kind.is_z3_owned()

    def ownership_summary(self) -> str:
        """Return a multi-line human-readable summary of this witness.

        The summary includes the witness ID, ownership kind, decidability
        score, structural depth, severity, and the rationale string.

        Returns
        -------
        str
            A multi-line string suitable for printing to a terminal or
            embedding in a report.

        Examples
        --------
        >>> w = Z3OwnStructuralFrontierWitness(
        ...     witness_id="w1", obligation_smt="(assert true)",
        ...     ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
        ...     decidability_score=1.0, structural_depth=1,
        ...     copilot_label="trivial", created_at=0.0, rationale="trivial",
        ... )
        >>> "STRUCTURAL_Z3" in w.ownership_summary()
        True
        """
        # copilot: build a multi-line report card for this witness
        lines = [
            f"=== Z3OwnStructuralFrontierWitness ===",
            f"  witness_id        : {self.witness_id}",
            f"  copilot_label     : {self.copilot_label}",
            f"  ownership_kind    : {self.ownership_kind.name}",
            f"  decidability_score: {self.decidability_score:.4f}",
            f"  structural_depth  : {self.structural_depth}",
            f"  severity          : {self.ownership_kind.severity_default()}",
            f"  smt2_marker       : {self.ownership_kind.smt2_ownership_marker()}",
            f"  escalation_prio   : {self.ownership_kind.escalation_priority()}",
            f"  created_at        : {self.created_at:.3f}",
            f"  rationale         : {self.rationale}",
            f"  obligation_smt    : {self.obligation_smt[:120]}",
        ]
        return "\n".join(lines)

    def to_smt2_assertion(self) -> str:
        """Return the obligation wrapped in SMT-LIB2 with ownership comments.

        The returned string is a complete, self-contained SMT-LIB2 fragment
        that can be appended to any valid SMT-LIB2 script.  It prepends the
        ownership marker comment and the witness ID comment before the
        obligation text.

        Returns
        -------
        str
            A string of the form::

                ; ownership: STRUCTURAL_Z3
                ; witness_id: <id>
                ; decidability_score: 0.9500
                <obligation_smt>

        Examples
        --------
        >>> w = Z3OwnStructuralFrontierWitness(
        ...     witness_id="w1", obligation_smt="(assert (= x 1))",
        ...     ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
        ...     decidability_score=0.95, structural_depth=2,
        ...     copilot_label="eq", created_at=0.0, rationale="ok",
        ... )
        >>> "(assert (= x 1))" in w.to_smt2_assertion()
        True
        """
        # copilot: annotate the SMT2 output so that downstream tools can
        # quickly filter by ownership without re-running classification
        header = "\n".join([
            self.ownership_kind.smt2_ownership_marker(),
            f"; witness_id: {self.witness_id}",
            f"; decidability_score: {self.decidability_score:.4f}",
            f"; structural_depth: {self.structural_depth}",
            f"; label: {self.copilot_label}",
        ])
        return f"{header}\n{self.obligation_smt}"

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of this witness's key fields.

        The fingerprint is computed over the concatenation of ``witness_id``,
        ``obligation_smt``, and ``ownership_kind.name``, encoded as UTF-8.

        Returns
        -------
        str
            A 64-character lowercase hex string.

        Examples
        --------
        >>> w = Z3OwnStructuralFrontierWitness(
        ...     witness_id="w1", obligation_smt="(assert true)",
        ...     ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
        ...     decidability_score=1.0, structural_depth=1,
        ...     copilot_label="t", created_at=0.0, rationale="r",
        ... )
        >>> len(w.fingerprint())
        64
        """
        # copilot: SHA-256 over the three most identifying fields; this is
        # stable across runs as long as the inputs don't change
        raw = (
            self.witness_id
            + "\x00"
            + self.obligation_smt
            + "\x00"
            + self.ownership_kind.name
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def merge(
        self, other: Z3OwnStructuralFrontierWitness
    ) -> Z3OwnStructuralFrontierWitness:
        """Merge two witnesses into one, taking the stronger classification.

        The merge operation produces a new witness that:

        * Uses the higher ``decidability_score`` of the two inputs.
        * Uses the deeper ``structural_depth``.
        * Combines the ``rationale`` strings with a separator.
        * Chooses the ownership kind with the higher escalation priority (i.e.,
          the more conservative / expensive classification wins).
        * Generates a fresh ``witness_id`` for the merged result.
        * Uses the current time as ``created_at``.

        Parameters
        ----------
        other : Z3OwnStructuralFrontierWitness
            The witness to merge with ``self``.

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            A new, merged witness.

        Examples
        --------
        >>> import time
        >>> w1 = Z3OwnStructuralFrontierWitness(
        ...     witness_id="a", obligation_smt="(assert (= x 1))",
        ...     ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
        ...     decidability_score=0.8, structural_depth=2,
        ...     copilot_label="w1", created_at=time.time(), rationale="r1",
        ... )
        >>> w2 = Z3OwnStructuralFrontierWitness(
        ...     witness_id="b", obligation_smt="(assert (= x 1))",
        ...     ownership_kind=StructuralOwnershipKind.HYBRID_PARTIAL,
        ...     decidability_score=0.5, structural_depth=4,
        ...     copilot_label="w2", created_at=time.time(), rationale="r2",
        ... )
        >>> merged = w1.merge(w2)
        >>> merged.decidability_score
        0.8
        >>> merged.structural_depth
        4
        """
        # copilot: take the more pessimistic ownership kind (higher escalation
        # priority) so that we never under-escalate a problematic obligation
        if (
            self.ownership_kind.escalation_priority()
            >= other.ownership_kind.escalation_priority()
        ):
            merged_kind = self.ownership_kind
        else:
            merged_kind = other.ownership_kind

        # copilot: keep the higher decidability score — if one classifier was
        # more confident it belongs to Z3, that evidence should not be lost
        merged_score = max(self.decidability_score, other.decidability_score)
        merged_depth = max(self.structural_depth, other.structural_depth)
        merged_rationale = (
            f"[merge of {self.witness_id} and {other.witness_id}] "
            f"{self.rationale} | {other.rationale}"
        )
        merged_label = f"{self.copilot_label}+{other.copilot_label}"

        return Z3OwnStructuralFrontierWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=self.obligation_smt,
            ownership_kind=merged_kind,
            decidability_score=merged_score,
            structural_depth=merged_depth,
            copilot_label=merged_label,
            created_at=time.time(),
            rationale=merged_rationale,
        )

    def copilot_ownership_hint(self) -> str:
        """Return a multi-line copilot hint string for IDE display.

        The hint is formatted for display in a Copilot inline hint or a VS Code
        hover widget.  It summarises the ownership decision and provides
        actionable advice for the developer.

        Returns
        -------
        str
            A multi-line hint string.
        """
        # copilot: format the hint so that it renders nicely in the IDE hover
        # panel without requiring markdown rendering support
        action_map = {
            StructuralOwnershipKind.STRUCTURAL_Z3: (
                "✅ Route this obligation directly to Z3. "
                "No oracle consultation needed."
            ),
            StructuralOwnershipKind.SEMANTIC_ORACLE: (
                "⚠️  Do NOT send this to Z3. "
                "Route to the semantic oracle handler."
            ),
            StructuralOwnershipKind.HYBRID_PARTIAL: (
                "🔀 Split this obligation: structural sub-goals → Z3, "
                "semantic sub-goals → oracle."
            ),
            StructuralOwnershipKind.ESCALATED: (
                "🚨 Previously failed Z3 attempt. "
                "Escalate to human review or alternative solver."
            ),
            StructuralOwnershipKind.UNDETERMINED: (
                "❓ Re-run classification before routing. "
                "Do not send to Z3 or oracle yet."
            ),
        }
        action = action_map.get(self.ownership_kind, "No action hint available.")
        return (
            f"[Jugeo Structural Frontier Hint]\n"
            f"  Label     : {self.copilot_label}\n"
            f"  Ownership : {self.ownership_kind.name}\n"
            f"  Score     : {self.decidability_score:.2f}\n"
            f"  Depth     : {self.structural_depth}\n"
            f"  Action    : {action}\n"
            f"  Rationale : {self.rationale}"
        )

    def age_seconds(self) -> float:
        """Return the age of this witness in seconds.

        Returns
        -------
        float
            ``time.time() - self.created_at``
        """
        # copilot: fresh witnesses have higher cache validity; stale ones
        # should be re-analyzed if the obligation text changed
        return time.time() - self.created_at

    def is_fresh(self, max_age: float = 300.0) -> bool:
        """Return True if this witness was created within ``max_age`` seconds.

        Parameters
        ----------
        max_age : float, optional
            Maximum acceptable age in seconds.  Default is 300.0 (5 minutes).

        Returns
        -------
        bool
            True if ``self.age_seconds() < max_age``.
        """
        # copilot: used by the caching layer to decide whether to reuse a
        # previously computed witness or re-run analysis
        return self.age_seconds() < max_age

    def to_dict(self) -> dict[str, Any]:
        """Serialise this witness to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain Python dict with string keys and JSON-serialisable values.
        """
        # copilot: serialise to dict so that witnesses can be stored in
        # databases, sent over HTTP, or written to JSON files
        return {
            "witness_id": self.witness_id,
            "obligation_smt": self.obligation_smt,
            "ownership_kind": self.ownership_kind.name,
            "decidability_score": self.decidability_score,
            "structural_depth": self.structural_depth,
            "copilot_label": self.copilot_label,
            "created_at": self.created_at,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Z3OwnStructuralFrontierWitness:
        """Deserialise a witness from a JSON-compatible dictionary.

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary as produced by ``to_dict()``.

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            The reconstructed witness object.

        Raises
        ------
        KeyError
            If a required field is missing from ``d``.
        ValueError
            If ``ownership_kind`` is not a valid ``StructuralOwnershipKind``
            member name.
        """
        # copilot: reconstruct the enum from its name string
        kind = StructuralOwnershipKind[d["ownership_kind"]]
        return cls(
            witness_id=d["witness_id"],
            obligation_smt=d["obligation_smt"],
            ownership_kind=kind,
            decidability_score=float(d["decidability_score"]),
            structural_depth=int(d["structural_depth"]),
            copilot_label=d["copilot_label"],
            created_at=float(d["created_at"]),
            rationale=d["rationale"],
        )


# ============================== analyzer ==============================


class Z3OwnStructuralFrontierAnalyzer:
    """Analyze obligations to determine whether Z3 should own them.

    The analyzer applies a battery of heuristic checks to an SMT-LIB2
    obligation string and produces a ``Z3OwnStructuralFrontierWitness``
    recording the ownership decision and supporting evidence.

    The analysis pipeline proceeds as follows:

    1. **Fragment classification**: check for quantifiers, non-linear
       arithmetic, array theory, string theory, and other markers that push
       the obligation outside Z3's decidable core.
    2. **Decidability scoring**: aggregate the fragment indicators into a
       single score in [0.0, 1.0].  Each negative indicator reduces the
       score; positive indicators (decidable fragment keywords) increase it.
    3. **Ownership classification**: map the score and fragment indicators to
       a ``StructuralOwnershipKind`` value.
    4. **Structural depth extraction**: count the maximum parenthesis nesting
       depth in the SMT-LIB2 text as a proxy for term complexity.

    The analyzer maintains an internal cache keyed by the SHA-256 of the
    obligation text.  Identical obligations are classified only once per
    analyzer instance lifetime.

    Parameters
    ----------
    None
        The analyzer is stateless except for its cache; no constructor
        arguments are required.

    Examples
    --------
    >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
    >>> w = analyzer.analyze_obligation("(assert (= x 1))", label="eq-check")
    >>> w.is_z3_owned()
    True
    >>> analyzer.cache_size()
    1
    """

    def __init__(self) -> None:
        # copilot: cache maps obligation text hash → witness to avoid
        # redundant re-analysis of identical obligations
        self._analysis_cache: dict[str, Z3OwnStructuralFrontierWitness] = {}

    def analyze_obligation(
        self, smt: str, label: str = ""
    ) -> Z3OwnStructuralFrontierWitness:
        """Analyze a single SMT-LIB2 obligation and return a witness.

        This is the primary entry point for single-obligation analysis.  The
        method checks the cache first; if a fresh witness is available it is
        returned immediately.  Otherwise, the full analysis pipeline is run
        and the result is cached.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text to analyze.
        label : str, optional
            A human-readable label for this obligation, used in copilot hints
            and reports.  Defaults to an empty string.

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            The witness recording the ownership decision and evidence.

        Examples
        --------
        >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
        >>> w = analyzer.analyze_obligation(
        ...     "(assert (and (= x 0) (= y 1)))", label="two-eq"
        ... )
        >>> w.ownership_kind in (
        ...     StructuralOwnershipKind.STRUCTURAL_Z3,
        ...     StructuralOwnershipKind.HYBRID_PARTIAL,
        ... )
        True
        """
        # copilot: use a content hash as cache key so that re-labeled
        # obligations with identical SMT text still hit the cache
        cache_key = hashlib.sha256(smt.encode("utf-8")).hexdigest()
        if cache_key in self._analysis_cache:
            cached = self._analysis_cache[cache_key]
            if cached.is_fresh():
                logger.debug(
                    "Cache hit for obligation (label=%s, key=%s…)",
                    label,
                    cache_key[:8],
                )
                return cached

        # copilot: run the full classification pipeline
        kind = self.classify_fragment(smt)
        score = self.estimate_decidability_score(smt)
        depth = self.extract_structural_depth(smt)
        rationale = self._build_rationale(smt, kind, score, depth)

        witness = Z3OwnStructuralFrontierWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=smt,
            ownership_kind=kind,
            decidability_score=score,
            structural_depth=depth,
            copilot_label=label or f"obligation-{cache_key[:8]}",
            created_at=time.time(),
            rationale=rationale,
        )
        self._analysis_cache[cache_key] = witness
        logger.debug(
            "Analyzed obligation (label=%s, kind=%s, score=%.2f)",
            label,
            kind.name,
            score,
        )
        return witness

    def _build_rationale(
        self,
        smt: str,
        kind: StructuralOwnershipKind,
        score: float,
        depth: int,
    ) -> str:
        """Build a human-readable rationale string for an ownership decision.

        Parameters
        ----------
        smt : str
            The obligation text.
        kind : StructuralOwnershipKind
            The inferred ownership kind.
        score : float
            The decidability score.
        depth : int
            The structural depth.

        Returns
        -------
        str
            A rationale string explaining the decision.
        """
        # copilot: collect all the fragment indicators that fired and
        # summarise them in the rationale
        parts: list[str] = []
        if self._has_quantifiers(smt):
            parts.append("contains quantifiers (forall/exists)")
        if self._has_nonlinear(smt):
            parts.append("contains non-linear arithmetic operators")
        if self._has_array_theory(smt):
            parts.append("uses array theory (store/select)")
        if self._has_string_theory(smt):
            parts.append("uses string theory operations")
        if self._has_arithmetic(smt):
            parts.append("uses linear arithmetic")
        keywords = self._fragment_keywords_present(smt)
        if keywords:
            parts.append(f"structural keywords: {', '.join(keywords[:5])}")
        nv = self._count_distinct_variables(smt)
        parts.append(f"{nv} distinct variable(s)")
        parts.append(f"structural depth={depth}")
        parts.append(f"decidability score={score:.3f}")
        parts.append(f"→ ownership={kind.name}")
        return "; ".join(parts)

    def classify_fragment(self, smt: str) -> StructuralOwnershipKind:
        """Classify the SMT-LIB2 fragment of an obligation string.

        Applies a rule-based classification using fragment indicator functions.
        The classification is deterministic given the obligation text.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        StructuralOwnershipKind
            The inferred ownership kind.

        Examples
        --------
        >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
        >>> analyzer.classify_fragment("(assert (= x 1))")
        <StructuralOwnershipKind.STRUCTURAL_Z3: 1>
        """
        # copilot: apply a priority-ordered rule set; more expensive patterns
        # take precedence over cheaper ones
        has_oracle = any(p in smt for p in _SEMANTIC_ORACLE_PATTERNS)
        has_quantifiers = self._has_quantifiers(smt)
        has_nonlinear = self._has_nonlinear(smt)
        has_strings = self._has_string_theory(smt)

        if has_oracle:
            # copilot: explicit oracle references are never Z3 territory
            return StructuralOwnershipKind.SEMANTIC_ORACLE

        if has_quantifiers and has_nonlinear:
            # copilot: quantified non-linear arithmetic is undecidable
            return StructuralOwnershipKind.SEMANTIC_ORACLE

        if has_quantifiers and has_strings:
            return StructuralOwnershipKind.SEMANTIC_ORACLE

        if has_quantifiers:
            # copilot: quantifiers alone push us into hybrid territory —
            # Z3 can handle them heuristically but not completely
            return StructuralOwnershipKind.HYBRID_PARTIAL

        if has_nonlinear and has_strings:
            return StructuralOwnershipKind.HYBRID_PARTIAL

        if has_nonlinear:
            # copilot: pure non-linear arithmetic without quantifiers is
            # in QF_NIA; Z3 handles many cases but not all
            return StructuralOwnershipKind.HYBRID_PARTIAL

        # copilot: everything else is structural and Z3-owned
        return StructuralOwnershipKind.STRUCTURAL_Z3

    def estimate_decidability_score(self, smt: str) -> float:
        """Estimate a decidability score in [0.0, 1.0] for an obligation.

        The score is computed by starting at 1.0 and applying penalties for
        each negative fragment indicator, then clamping to [0.0, 1.0].

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        float
            A score in [0.0, 1.0].  Higher is better (more decidable).

        Examples
        --------
        >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
        >>> analyzer.estimate_decidability_score("(assert (= x 1))")  # doctest: +SKIP
        0.95
        """
        # copilot: start optimistic and subtract confidence for each
        # problematic pattern found in the obligation text
        score = 1.0

        if self._has_quantifiers(smt):
            score -= 0.35
        if self._has_nonlinear(smt):
            score -= 0.25
        if self._has_string_theory(smt):
            score -= 0.10
        if any(p in smt for p in _SEMANTIC_ORACLE_PATTERNS):
            score -= 0.60

        # copilot: penalise very deep obligations because they may trigger
        # Z3 resource limits (e.g., stack overflows or time-outs)
        depth = self.extract_structural_depth(smt)
        if depth > 15:
            score -= 0.15
        elif depth > 8:
            score -= 0.05

        # copilot: reward obligations that use well-known decidable keywords
        keyword_count = len(self._fragment_keywords_present(smt))
        score += min(keyword_count * 0.02, 0.10)

        return max(0.0, min(1.0, score))

    def extract_structural_depth(self, smt: str) -> int:
        """Extract the maximum parenthesis nesting depth from an SMT string.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        int
            The maximum parenthesis nesting depth encountered.

        Examples
        --------
        >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
        >>> analyzer.extract_structural_depth("(assert (= x 1))")
        2
        >>> analyzer.extract_structural_depth("(assert (and (= x (+ y 1)) (= z 0)))")
        4
        """
        # copilot: simple linear scan; parentheses are the depth markers in
        # SMT-LIB2 so we don't need a full parser
        depth = 0
        max_depth = 0
        for ch in smt:
            if ch == "(":
                depth += 1
                if depth > max_depth:
                    max_depth = depth
            elif ch == ")":
                depth -= 1
        return max_depth

    def batch_analyze(
        self, obligations: list[str]
    ) -> list[Z3OwnStructuralFrontierWitness]:
        """Analyze a list of obligations and return a list of witnesses.

        Parameters
        ----------
        obligations : list[str]
            A list of SMT-LIB2 obligation strings.

        Returns
        -------
        list[Z3OwnStructuralFrontierWitness]
            A list of witnesses in the same order as the input.

        Examples
        --------
        >>> analyzer = Z3OwnStructuralFrontierAnalyzer()
        >>> witnesses = analyzer.batch_analyze([
        ...     "(assert (= x 1))",
        ...     "(assert (forall ((x Int)) (= x x)))",
        ... ])
        >>> len(witnesses)
        2
        """
        # copilot: iterate over the obligations and call analyze_obligation on
        # each; the cache ensures that duplicates are free
        results: list[Z3OwnStructuralFrontierWitness] = []
        for idx, smt in enumerate(obligations):
            label = f"batch-{idx}"
            results.append(self.analyze_obligation(smt, label=label))
        return results

    def copilot_analysis_hint(
        self, witness: Z3OwnStructuralFrontierWitness
    ) -> str:
        """Return a copilot analysis hint for a witness.

        Parameters
        ----------
        witness : Z3OwnStructuralFrontierWitness
            The witness to generate a hint for.

        Returns
        -------
        str
            A multi-line hint string.
        """
        # copilot: delegate to the witness's own hint method but add
        # analyzer-level context about available backends
        backend_note = (
            "Z3 backend is available."
            if _Z3_AVAILABLE
            else "Z3 backend NOT available — install z3-solver."
        )
        return witness.copilot_ownership_hint() + f"\n  Backend   : {backend_note}"

    def _has_quantifiers(self, smt: str) -> bool:
        """Return True if the SMT text contains quantifier keywords.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        bool
        """
        # copilot: check for both SMT-LIB2 quantifier forms
        return any(pat in smt for pat in _QUANTIFIER_PATTERNS)

    def _has_arithmetic(self, smt: str) -> bool:
        """Return True if the SMT text contains linear arithmetic operators.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        bool
        """
        # copilot: linear arithmetic (+ - <=  >= < >) is decidable in QF_LIA
        return any(op in smt for op in ("(+ ", "(- ", "(<= ", "(>= ", "(< ", "(> "))

    def _has_array_theory(self, smt: str) -> bool:
        """Return True if the SMT text uses array theory operators.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        bool
        """
        # copilot: store/select are the canonical array-theory operators in
        # SMT-LIB2; Z3 handles them in QF_AX and QF_AUFLIA
        return "(store " in smt or "(select " in smt

    def _has_nonlinear(self, smt: str) -> bool:
        """Return True if the SMT text contains non-linear arithmetic patterns.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        bool
        """
        # copilot: non-linear patterns are the primary reason obligations
        # escape Z3's decidable core
        return any(pat in smt for pat in _NONLINEAR_PATTERNS)

    def _has_string_theory(self, smt: str) -> bool:
        """Return True if the SMT text uses string theory operations.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        bool
        """
        # copilot: string operations push the obligation into QF_S or beyond
        return any(
            op in smt
            for op in ("str.len", "str.contains", "str.replace", "str.++",
                       "str.substr", "str.at", "str.to_int", "re.range")
        )

    def _count_distinct_variables(self, smt: str) -> int:
        """Count the number of distinct variable-like tokens in the SMT text.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        int
            An approximation of the number of distinct variables (non-keyword
            lowercase identifiers).
        """
        # copilot: simple token-based heuristic; not a full parser but good
        # enough for scoring purposes
        import re as _re
        # copilot: extract lowercase identifiers that aren't SMT keywords
        _smt_keywords = {
            "assert", "check-sat", "declare", "define", "forall", "exists",
            "and", "or", "not", "ite", "let", "true", "false", "lambda",
        }
        tokens = _re.findall(r"\b[a-z][a-z0-9_-]*\b", smt)
        return len({t for t in tokens if t not in _smt_keywords})

    def _fragment_keywords_present(self, smt: str) -> list[str]:
        """Return the list of known structural Z3 keywords present in the SMT text.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        list[str]
            The subset of ``_STRUCTURAL_Z3_KEYWORDS`` keys found in ``smt``.
        """
        # copilot: used for both scoring and rationale generation
        return [kw for kw in _STRUCTURAL_Z3_KEYWORDS if kw in smt]

    def cache_size(self) -> int:
        """Return the number of entries in the analysis cache.

        Returns
        -------
        int
            The current cache size.
        """
        return len(self._analysis_cache)

    def clear_cache(self) -> None:
        """Clear the analysis cache.

        After calling this method ``cache_size()`` will return 0 and all
        subsequent calls to ``analyze_obligation`` will re-run the full
        analysis pipeline.
        """
        # copilot: clear the cache e.g. after an obligation set has been
        # updated and all cached results are stale
        self._analysis_cache.clear()
        logger.debug("Analysis cache cleared.")


# ============================== coordinator ==============================


class Z3OwnStructuralFrontierCoordinator:
    """Main coordinator for structural frontier ownership analysis.

    The coordinator is the primary façade for the structural-frontier analysis
    subsystem.  It wraps a ``Z3OwnStructuralFrontierAnalyzer``, maintains a
    registry of all witnesses produced, tracks usage statistics, and provides
    convenience query methods for downstream routing logic.

    A typical usage pattern is:

    1. Create a ``Z3OwnStructuralFrontierCoordinator`` instance (one per
       encoding session or per test suite).
    2. Call ``register_obligation`` for each SMT-LIB2 obligation produced by
       the encoding pipeline.
    3. Inspect the returned witnesses (or query ``all_z3_owned``,
       ``all_oracle_owned``, etc.) to route obligations to the appropriate
       solvers.
    4. Call ``structural_ownership_report()`` to generate a human-readable
       summary for logging or debugging.

    Parameters
    ----------
    None

    Attributes
    ----------
    _analyzer : Z3OwnStructuralFrontierAnalyzer
        The underlying analyzer instance.
    _stats : collections.defaultdict[str, int]
        Usage statistics keyed by event name.
    _witness_registry : dict[str, Z3OwnStructuralFrontierWitness]
        Maps obligation text (not hash) to the most recently computed witness.

    Examples
    --------
    >>> coord = Z3OwnStructuralFrontierCoordinator()
    >>> w = coord.register_obligation("(assert (= x 0))", label="zero-check")
    >>> w.is_z3_owned()
    True
    >>> len(coord)
    1
    >>> coord.stats["registered"]
    1
    """

    def __init__(self) -> None:
        # copilot: create a fresh analyzer and empty stats/registry
        self._analyzer = Z3OwnStructuralFrontierAnalyzer()
        self._stats: dict[str, int] = collections.defaultdict(int)
        self._witness_registry: dict[str, Z3OwnStructuralFrontierWitness] = {}

    def register_obligation(
        self, smt: str, label: str = ""
    ) -> Z3OwnStructuralFrontierWitness:
        """Register an SMT-LIB2 obligation and return an ownership witness.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.
        label : str, optional
            A human-readable label for the obligation.

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            The ownership witness for this obligation.

        Examples
        --------
        >>> coord = Z3OwnStructuralFrontierCoordinator()
        >>> w = coord.register_obligation("(assert (= a b))", label="eq-ab")
        >>> w.copilot_label
        'eq-ab'
        """
        # copilot: analyze and store; use smt text as registry key so that
        # duplicate obligations with different labels share a witness
        witness = self._analyzer.analyze_obligation(smt, label=label)
        self._witness_registry[smt] = witness
        self._stats["registered"] += 1
        self._stats[f"kind_{witness.ownership_kind.name}"] += 1
        logger.info(
            "Registered obligation (label=%s, kind=%s)",
            label,
            witness.ownership_kind.name,
        )
        return witness

    def promote_to_z3(self, smt: str) -> Z3OwnStructuralFrontierWitness:
        """Force-promote an obligation to STRUCTURAL_Z3 ownership.

        This method is intended for use when domain knowledge guarantees that
        an obligation is decidable by Z3 even though the heuristic classifier
        returned a more conservative result.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text (must have been previously registered).

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            A new witness with ``ownership_kind`` set to ``STRUCTURAL_Z3``.
        """
        # copilot: build a replacement witness with the new ownership kind
        old = self._witness_registry.get(smt)
        if old is None:
            old = self._analyzer.analyze_obligation(smt, label="auto-promoted")
        new_witness = Z3OwnStructuralFrontierWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=smt,
            ownership_kind=StructuralOwnershipKind.STRUCTURAL_Z3,
            decidability_score=max(old.decidability_score, 0.75),
            structural_depth=old.structural_depth,
            copilot_label=old.copilot_label + "[promoted]",
            created_at=time.time(),
            rationale=f"Force-promoted to Z3. Original rationale: {old.rationale}",
        )
        self._witness_registry[smt] = new_witness
        self._stats["promoted_to_z3"] += 1
        return new_witness

    def demote_to_oracle(self, smt: str) -> Z3OwnStructuralFrontierWitness:
        """Force-demote an obligation to SEMANTIC_ORACLE ownership.

        This method is intended for use after a Z3 solve attempt returns
        ``unknown`` or times out, indicating the obligation is outside Z3's
        practical decidable core.

        Parameters
        ----------
        smt : str
            The SMT-LIB2 obligation text.

        Returns
        -------
        Z3OwnStructuralFrontierWitness
            A new witness with ``ownership_kind`` set to ``ESCALATED``.
        """
        # copilot: after Z3 failure, escalate rather than immediately going
        # to oracle — give the escalation pipeline a chance to split the goal
        old = self._witness_registry.get(smt)
        if old is None:
            old = self._analyzer.analyze_obligation(smt, label="auto-demoted")
        new_witness = Z3OwnStructuralFrontierWitness(
            witness_id=str(uuid.uuid4()),
            obligation_smt=smt,
            ownership_kind=StructuralOwnershipKind.ESCALATED,
            decidability_score=min(old.decidability_score, 0.25),
            structural_depth=old.structural_depth,
            copilot_label=old.copilot_label + "[demoted]",
            created_at=time.time(),
            rationale=(
                f"Demoted after Z3 failure. Original rationale: {old.rationale}"
            ),
        )
        self._witness_registry[smt] = new_witness
        self._stats["demoted_to_oracle"] += 1
        return new_witness

    def structural_ownership_report(self) -> str:
        """Return a detailed multi-line ownership report for all registered obligations.

        Returns
        -------
        str
            A formatted report string.
        """
        # copilot: aggregate statistics across all registered witnesses
        total = len(self._witness_registry)
        z3_count = sum(1 for w in self._witness_registry.values() if w.is_z3_owned())
        oracle_count = sum(
            1
            for w in self._witness_registry.values()
            if w.ownership_kind.is_oracle_owned()
        )
        hybrid_count = sum(
            1
            for w in self._witness_registry.values()
            if w.ownership_kind.is_hybrid()
        )
        escalated_count = sum(
            1
            for w in self._witness_registry.values()
            if w.ownership_kind.is_escalated()
        )
        undet_count = (
            total - z3_count - oracle_count - hybrid_count - escalated_count
        )
        avg_score = (
            sum(w.decidability_score for w in self._witness_registry.values()) / total
            if total > 0
            else 0.0
        )
        avg_depth = (
            sum(w.structural_depth for w in self._witness_registry.values()) / total
            if total > 0
            else 0.0
        )
        lines = [
            "=" * 70,
            "  Z3 Structural Frontier Ownership Report",
            "=" * 70,
            f"  Total obligations    : {total}",
            f"  Z3-owned (structural): {z3_count}",
            f"  Oracle-owned         : {oracle_count}",
            f"  Hybrid               : {hybrid_count}",
            f"  Escalated            : {escalated_count}",
            f"  Undetermined         : {undet_count}",
            f"  Avg decidability     : {avg_score:.4f}",
            f"  Avg structural depth : {avg_depth:.2f}",
            f"  Analyzer cache size  : {self._analyzer.cache_size()}",
            f"  Z3 available         : {_Z3_AVAILABLE}",
            f"  Z3Session available  : {_Z3_SESSION_AVAILABLE}",
            "=" * 70,
            "  Stats:",
        ]
        for k, v in sorted(self._stats.items()):
            lines.append(f"    {k}: {v}")
        lines.append("=" * 70)
        return "\n".join(lines)

    def all_z3_owned(self) -> list[Z3OwnStructuralFrontierWitness]:
        """Return all witnesses with STRUCTURAL_Z3 ownership.

        Returns
        -------
        list[Z3OwnStructuralFrontierWitness]
        """
        # copilot: filter the registry to Z3-owned witnesses only
        return [w for w in self._witness_registry.values() if w.is_z3_owned()]

    def all_oracle_owned(self) -> list[Z3OwnStructuralFrontierWitness]:
        """Return all witnesses with SEMANTIC_ORACLE ownership.

        Returns
        -------
        list[Z3OwnStructuralFrontierWitness]
        """
        return [
            w
            for w in self._witness_registry.values()
            if w.ownership_kind.is_oracle_owned()
        ]

    def all_hybrid(self) -> list[Z3OwnStructuralFrontierWitness]:
        """Return all witnesses with HYBRID_PARTIAL ownership.

        Returns
        -------
        list[Z3OwnStructuralFrontierWitness]
        """
        return [
            w
            for w in self._witness_registry.values()
            if w.ownership_kind.is_hybrid()
        ]

    def find_by_label(self, label: str) -> list[Z3OwnStructuralFrontierWitness]:
        """Return all witnesses whose copilot_label contains ``label``.

        Parameters
        ----------
        label : str
            Substring to search for in ``copilot_label``.

        Returns
        -------
        list[Z3OwnStructuralFrontierWitness]
        """
        # copilot: substring match is intentional — labels are not necessarily
        # unique and users may search for prefixes or keywords
        return [
            w
            for w in self._witness_registry.values()
            if label in w.copilot_label
        ]

    def iter_witnesses(self) -> Iterator[Z3OwnStructuralFrontierWitness]:
        """Iterate over all registered witnesses.

        Yields
        ------
        Z3OwnStructuralFrontierWitness
            Each witness in insertion order (Python 3.7+ dict ordering).
        """
        # copilot: yields from the registry values in insertion order
        yield from self._witness_registry.values()

    @property
    def stats(self) -> dict[str, int]:
        """Return a copy of the usage statistics dictionary.

        Returns
        -------
        dict[str, int]
            A shallow copy of the internal stats counter.
        """
        # copilot: return a copy so that callers cannot mutate the internal
        # state of the coordinator
        return dict(self._stats)

    def __repr__(self) -> str:
        return (
            f"Z3OwnStructuralFrontierCoordinator("
            f"obligations={len(self._witness_registry)}, "
            f"z3_owned={len(self.all_z3_owned())}, "
            f"oracle_owned={len(self.all_oracle_owned())})"
        )

    def __len__(self) -> int:
        """Return the number of registered obligations."""
        return len(self._witness_registry)


# ============================== module convenience ==============================


def register_structural_obligation(
    smt: str, label: str = ""
) -> Z3OwnStructuralFrontierWitness:
    """Create a coordinator, register a single obligation, and return the witness.

    This is a module-level convenience function for callers that only need to
    classify a single obligation without managing a coordinator instance.

    Parameters
    ----------
    smt : str
        The SMT-LIB2 obligation text.
    label : str, optional
        A human-readable label for the obligation.

    Returns
    -------
    Z3OwnStructuralFrontierWitness
        The ownership witness for the obligation.

    Examples
    --------
    >>> w = register_structural_obligation("(assert (= x y))", label="eq-xy")
    >>> isinstance(w, Z3OwnStructuralFrontierWitness)
    True
    """
    # copilot: one-shot helper — creates a throwaway coordinator, registers
    # the obligation, and returns the witness
    coord = Z3OwnStructuralFrontierCoordinator()
    return coord.register_obligation(smt, label=label)


# ============================== smoke test ==============================

if __name__ == "__main__":
    # copilot: exercise the main functionality end-to-end to verify that
    # the module works correctly in isolation

    logging.basicConfig(level=logging.WARNING)

    print("=== Z3OwnStructuralFrontierWitness smoke test ===\n")

    coord = Z3OwnStructuralFrontierCoordinator()

    # copilot: a set of representative obligations spanning multiple fragments
    test_cases: list[tuple[str, str]] = [
        ("(assert (= x 1))", "simple-equality"),
        ("(assert (and (= a 0) (= b 1)))", "conjunction"),
        ("(assert (forall ((n Int)) (>= n 0)))", "quantified-nonneg"),
        ("(assert (* x x))", "nonlinear-square"),
        ("(assert (store a 0 1))", "array-store"),
        ("(assert (str.len s))", "string-len"),
        ("(assert (and (forall ((x Int)) (= x x)) (* y y)))", "quant-nonlinear"),
        ("(assert (oracle-call pred x y))", "oracle-obligation"),
        ("(assert (ite (= x 0) true false))", "ite-expr"),
        ("(assert (distinct a b c d))", "distinct-4-vars"),
        ("(assert (bvadd x y))", "bv-add"),
        ("(assert (let ((z (+ x 1))) (= z 2)))", "let-binding"),
    ]

    print("Registering obligations...")
    for smt, label in test_cases:
        w = coord.register_obligation(smt, label=label)
        print(f"  [{w.ownership_kind.name:20s}] score={w.decidability_score:.2f}  {label}")

    print()
    print(coord.structural_ownership_report())

    print()
    print("=== Z3-owned witnesses ===")
    for w in coord.all_z3_owned():
        print(f"  {w.copilot_label}: score={w.decidability_score:.2f}, depth={w.structural_depth}")

    print()
    print("=== Oracle-owned witnesses ===")
    for w in coord.all_oracle_owned():
        print(f"  {w.copilot_label}: {w.rationale[:80]}")

    print()
    print("=== Hybrid witnesses ===")
    for w in coord.all_hybrid():
        print(f"  {w.copilot_label}: depth={w.structural_depth}")

    print()
    print("=== Copilot hint for first witness ===")
    first = next(coord.iter_witnesses())
    print(first.copilot_ownership_hint())

    print()
    print("=== SMT2 assertion with ownership marker ===")
    print(first.to_smt2_assertion())

    print()
    print("=== Merge test ===")
    witnesses = list(coord.iter_witnesses())
    if len(witnesses) >= 2:
        merged = witnesses[0].merge(witnesses[1])
        print(f"  Merged: kind={merged.ownership_kind.name}, score={merged.decidability_score:.2f}")
        print(f"  Fingerprint: {merged.fingerprint()[:16]}...")

    print()
    print("=== Serialisation round-trip ===")
    d = first.to_dict()
    restored = Z3OwnStructuralFrontierWitness.from_dict(d)
    assert restored.witness_id == first.witness_id
    assert restored.ownership_kind == first.ownership_kind
    assert restored.decidability_score == first.decidability_score
    print(f"  Round-trip OK for witness_id={first.witness_id[:8]}...")

    print()
    print("=== Promote / demote test ===")
    smt_to_promote = test_cases[2][0]  # quantified obligation
    promoted = coord.promote_to_z3(smt_to_promote)
    print(f"  After promote: {promoted.ownership_kind.name}")
    demoted = coord.demote_to_oracle(smt_to_promote)
    print(f"  After demote : {demoted.ownership_kind.name}")

    print()
    print("=== Module-level convenience function ===")
    w_conv = register_structural_obligation("(assert (= p q))", label="module-level")
    print(f"  witness_id={w_conv.witness_id[:8]}..., kind={w_conv.ownership_kind.name}")

    print()
    print("=== Batch analyze ===")
    analyzer = Z3OwnStructuralFrontierAnalyzer()
    batch_smts = [smt for smt, _ in test_cases[:6]]
    batch_results = analyzer.batch_analyze(batch_smts)
    for w in batch_results:
        print(f"  {w.copilot_label}: {w.ownership_kind.name}")

    print()
    print("=== Freshness / age test ===")
    w_time = first
    print(f"  age_seconds: {w_time.age_seconds():.3f}s")
    print(f"  is_fresh(300): {w_time.is_fresh(300.0)}")
    print(f"  is_fresh(0): {w_time.is_fresh(0.0)}")

    print()
    print("=== StructuralOwnershipKind enum smoke test ===")
    for kind in StructuralOwnershipKind:
        print(
            f"  {kind.name:20s} prio={kind.escalation_priority()}"
            f" sev={kind.severity_default():8s}"
            f" marker={kind.smt2_ownership_marker()}"
        )

    print()
    print("=== find_by_label ===")
    found = coord.find_by_label("array")
    print(f"  Obligations matching 'array': {[w.copilot_label for w in found]}")

    print()
    print("=== repr / len ===")
    print(f"  repr: {coord!r}")
    print(f"  len : {len(coord)}")

    print()
    print("=== Analyzer cache ===")
    print(f"  Cache size before clear: {analyzer.cache_size()}")
    analyzer.clear_cache()
    print(f"  Cache size after clear : {analyzer.cache_size()}")

    print()
    print("All smoke tests passed ✅")
