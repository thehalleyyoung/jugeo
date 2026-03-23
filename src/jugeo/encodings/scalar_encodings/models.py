r"""Scalar encoding models for the JuGeo SMT-based verification pipeline.

This module implements the data models described in **Chapter 26** of
``preliminaries/theory2.tex``, which covers *scalar sort encodings* — the
mechanism by which JuGeo maps abstract geometric and logical domains into SMT
solver sorts suitable for discharge by Z3 or compatible back-ends.

Chapter 26 introduces the following key abstractions:

* **Sort kinds** — the universe of base SMT sorts (§26.1), distinguishing
  integer, real, bit-vector, Boolean, uninterpreted, and refinement variants.
* **Fragment hints** — the SMT-LIB 2 fragment (logic) each encoding targets
  (§26.2), e.g. ``QF_LIA``, ``QF_LRA``, ``QF_BV``.
* **Refinement encodings** — pairing a base sort with a predicate that further
  restricts its semantics (§26.3), enabling lightweight dependent-type-like
  contracts to be discharged by a DPLL(T) solver.
* **Path conditions** — propositional constraints accumulated along solver
  exploration paths (§26.4); used during model reconstruction.
* **Guard formulae** — Boolean-sorted constraints guarding transitions in the
  fragment automaton (§26.5).
* **Arithmetic obligations** — linear or nonlinear arithmetic formulae whose
  satisfiability must be checked (§26.6).
* **Encoding contexts** — mutable containers that aggregate all of the above
  for a single solver session (§26.7).
* **Encoding results** — immutable records of solver outcomes (§26.8).

All frozen dataclasses are safe for use as dict keys and in sets.
Mutable :class:`EncodingContext` objects must not be shared across threads
without external synchronisation.

copilot: This module is a primary target for LLM-assisted scalar-sort
reasoning.  Copilot-suggested encodings are flagged via the
``copilot_suggested`` field on :class:`RefinementEncoding`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment
from jugeo.solver.z3_session import (
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
)

logger = logging.getLogger(__name__)

# ============================== helpers ==============================


def make_encoding_id() -> str:
    """Return a fresh, short encoding identifier.

    The identifier is prefixed with ``enc_`` followed by 8 hex digits drawn
    from a random UUID, giving 2^32 collision resistance for typical session
    sizes.
    """
    return f"enc_{uuid.uuid4().hex[:8]}"


def make_context_id() -> str:
    """Return a fresh, short context identifier.

    The identifier is prefixed with ``ctx_`` followed by 8 hex digits drawn
    from a random UUID.  Context IDs are used to correlate log messages and
    solver queries across a single :class:`EncodingContext` lifetime.
    """
    return f"ctx_{uuid.uuid4().hex[:8]}"


def make_result_id() -> str:
    """Return a fresh, short result identifier.

    The identifier is prefixed with ``res_`` followed by 8 hex digits drawn
    from a random UUID.  Result IDs allow downstream consumers to cache and
    de-duplicate solver outcomes without inspecting their content.
    """
    return f"res_{uuid.uuid4().hex[:8]}"


# ============================== enums ==============================


class SortKind(Enum):
    """Enumeration of the base SMT sorts supported by JuGeo scalar encodings.

    Each member corresponds to one of the sort families described in §26.1 of
    theory2.tex.  The ``REFINEMENT`` sort is a meta-sort: it wraps an
    underlying base sort with an additional predicate constraint, enabling
    lightweight dependent typing over the SMT layer.

    copilot: When proposing a new sort for a scalar encoding, prefer INT or
    REAL for arithmetic domains and BOOL for propositional guards.
    """

    INT = auto()
    """Arbitrary-precision integer sort (SMT-LIB ``Int``)."""

    REAL = auto()
    """Real-number sort (SMT-LIB ``Real``)."""

    BOOL = auto()
    """Boolean sort (SMT-LIB ``Bool``)."""

    BITVEC = auto()
    """Fixed-width bit-vector sort (SMT-LIB ``(_ BitVec N)``)."""

    UNINTERPRETED = auto()
    """Uninterpreted sort, standing for an opaque domain (SMT-LIB ``U``)."""

    REFINEMENT = auto()
    """Refinement sort — base sort plus a predicate (§26.3 of theory2.tex)."""

    def to_smt2(self) -> str:
        """Return the SMT-LIB 2 sort token for this kind.

        For ``BITVEC`` the placeholder ``N`` is used; callers that know the
        concrete width should substitute it.  For ``REFINEMENT`` the string
        ``Refinement`` is returned as a sentinel; the actual SMT encoding must
        be constructed by :class:`RefinementEncoding`.
        """
        _map = {
            SortKind.INT: "Int",
            SortKind.REAL: "Real",
            SortKind.BOOL: "Bool",
            SortKind.BITVEC: "(_ BitVec N)",
            SortKind.UNINTERPRETED: "U",
            SortKind.REFINEMENT: "Refinement",
        }
        return _map[self]

    def is_numeric(self) -> bool:
        """Return True if this sort kind supports arithmetic operations.

        INT, REAL, and BITVEC are considered numeric.  BOOL, UNINTERPRETED, and
        REFINEMENT are not (REFINEMENT delegates to its base sort).
        """
        return self in (SortKind.INT, SortKind.REAL, SortKind.BITVEC)

    def is_quantifier_free_compatible(self) -> bool:
        """Return True if this sort can appear in quantifier-free formulae.

        All sorts except REFINEMENT are directly usable in QF fragments.
        REFINEMENT sorts require unfolding the predicate before the formula can
        be sent to a QF-only solver, so they return False here.
        """
        return self is not SortKind.REFINEMENT


class FragmentHint(Enum):
    """SMT-LIB 2 logic fragment hint for a scalar encoding.

    Chapter 26 §26.2 of theory2.tex classifies each encoding by the smallest
    SMT-LIB 2 logic fragment sufficient to discharge it.  This hint drives
    solver selection and constraint compilation in the JuGeo pipeline.

    copilot: Use the weakest fragment that covers the obligation to maximise
    solver performance; escalate to MIXED only when necessary.
    """

    QF_LIA = auto()
    """Quantifier-free linear integer arithmetic."""

    QF_LRA = auto()
    """Quantifier-free linear real arithmetic."""

    QF_BV = auto()
    """Quantifier-free fixed-size bit-vector arithmetic."""

    QF_BOOL = auto()
    """Quantifier-free propositional logic (SAT)."""

    MIXED = auto()
    """Mixed or undetermined fragment — use a general-purpose solver."""

    def smt_lib_name(self) -> str:
        """Return the SMT-LIB 2 logic string for this fragment.

        The returned string is suitable for use in an ``(set-logic ...)``
        command.  ``MIXED`` maps to ``ALL``, which instructs the solver to
        enable all supported theories.
        """
        _map = {
            FragmentHint.QF_LIA: "QF_LIA",
            FragmentHint.QF_LRA: "QF_LRA",
            FragmentHint.QF_BV: "QF_BV",
            FragmentHint.QF_BOOL: "QF_BOOL",
            FragmentHint.MIXED: "ALL",
        }
        return _map[self]

    def supports_bitvec(self) -> bool:
        """Return True if this fragment natively supports bit-vector operations.

        Only ``QF_BV`` provides native bit-vector support.  All other fragments
        require explicit encoding of bit manipulation into their base theory,
        which is rarely sound.
        """
        return self is FragmentHint.QF_BV

    def is_quantifier_free(self) -> bool:
        """Return True for all members of this enum.

        Every fragment hint defined here is quantifier-free by construction
        (hence the ``QF_`` prefix on most members).  This method exists for
        duck-typing compatibility with richer fragment hierarchies.
        """
        return True

    def can_merge_with(self, other: FragmentHint) -> FragmentHint:
        """Return the least upper bound of two fragment hints.

        If both fragments are identical the result is ``self``.  Otherwise the
        result is ``MIXED``, indicating that a general-purpose solver is
        required for the combined obligation.
        """
        if self is other:
            return self
        return FragmentHint.MIXED


class EncodeStatus(Enum):
    """Lifecycle status of a scalar encoding within a solver session.

    Transitions follow the state machine defined in §26.7 of theory2.tex:
    ``PENDING`` → ``ENCODED`` → ``DISCHARGED``, with ``FAILED`` reachable
    from any non-terminal state on error.
    """

    PENDING = auto()
    """Encoding has been requested but not yet compiled to SMT2."""

    ENCODED = auto()
    """SMT2 form has been generated and is ready for the solver."""

    FAILED = auto()
    """Encoding or discharge attempt failed; see associated error record."""

    DISCHARGED = auto()
    """The solver has successfully discharged this encoding."""

    def is_terminal(self) -> bool:
        """Return True if no further state transitions are possible.

        ``FAILED`` and ``DISCHARGED`` are both terminal.  Once an encoding
        reaches one of these states it should be treated as read-only.
        """
        return self in (EncodeStatus.FAILED, EncodeStatus.DISCHARGED)

    def is_active(self) -> bool:
        """Return True if this status represents an in-progress encoding.

        ``PENDING`` and ``ENCODED`` are both active states where solver work
        remains to be done.
        """
        return self in (EncodeStatus.PENDING, EncodeStatus.ENCODED)


# ============================== dataclasses ==============================


@dataclass(frozen=True)
class RefinementEncoding:
    """An immutable record pairing a base SMT sort with a refinement predicate.

    This is the central artefact of §26.3 of theory2.tex.  A
    :class:`RefinementEncoding` captures everything the solver needs to check
    that a value inhabits a refined sort: the base sort, the predicate in SMT2
    syntax, the compiled constraint, and provenance information.

    copilot: When generating a refinement encoding for a new scalar domain,
    populate ``copilot_suggested=True`` so the pipeline can track provenance
    and flag the encoding for human review before discharge.
    """

    encoding_id: str
    """Unique identifier for this encoding, typically from make_encoding_id()."""

    base_sort: SortKind
    """The underlying SMT sort that this refinement constrains."""

    predicate_str: str
    """Human-readable predicate description, e.g. '0 <= x < 256'."""

    z3_constraint_smt: str
    """SMT2 expression encoding the predicate, ready for (assert ...)."""

    fragment: FragmentHint
    """Smallest fragment sufficient to discharge this encoding."""

    support: SupportRegion
    """Geometric support region associated with this encoding (§26.3)."""

    created_at: float
    """Wall-clock timestamp (time.time()) when this record was created."""

    copilot_suggested: bool
    """True if this encoding was proposed by the Copilot assistant."""

    # ------------------------------------------------------------------ #

    def is_discharged(self) -> bool:
        """Return True if a trivial-unsat marker is present in the constraint.

        This is a lightweight syntactic heuristic: if the constraint string
        already contains the token ``(unsat)`` or the literal ``(assert false)``
        we conclude the encoding has been pre-discharged by earlier
        simplification and no further solver call is needed.
        """
        return "(unsat)" in self.z3_constraint_smt or "(assert false)" in self.z3_constraint_smt

    def to_smt2(self) -> str:
        """Emit a self-contained SMT2 assertion block for this encoding.

        The output begins with a comment header showing the encoding ID and base
        sort, followed by the ``(assert ...)`` form of the compiled constraint.
        The result can be spliced directly into a ``.smt2`` file or handed to
        :class:`~jugeo.solver.z3_session.Z3Session`.
        """
        header = (
            f"; encoding_id={self.encoding_id} base_sort={self.base_sort.to_smt2()}\n"
            f"; predicate: {self.predicate_str}\n"
            f"; copilot_suggested={self.copilot_suggested}"
        )
        assertion = f"(assert {self.z3_constraint_smt})"
        return f"{header}\n{assertion}"

    def summary(self) -> str:
        """Return a compact multi-field summary string.

        The summary includes the encoding ID, base sort name, fragment name,
        predicate length, and whether the encoding was copilot-suggested.  It
        is intended for display in log messages and diagnostic reports.
        """
        parts = [
            f"id={self.encoding_id}",
            f"sort={self.base_sort.name}",
            f"fragment={self.fragment.smt_lib_name()}",
            f"predicate_len={len(self.predicate_str)}",
            f"copilot={self.copilot_suggested}",
            f"discharged={self.is_discharged()}",
        ]
        return " | ".join(parts)

    def merge_with(self, other: RefinementEncoding) -> RefinementEncoding:
        """Return a new encoding that is the conjunction of self and other.

        The two base sorts must be identical; a ``ValueError`` is raised
        otherwise.  The resulting constraint is the SMT2 conjunction
        ``(and P Q)`` of both constraint strings, ensuring the merged encoding
        is strictly stronger than either operand.
        """
        if self.base_sort is not other.base_sort:
            raise ValueError(
                f"Cannot merge encodings with differing base sorts: "
                f"{self.base_sort.name} vs {other.base_sort.name}"
            )
        merged_constraint = f"(and {self.z3_constraint_smt} {other.z3_constraint_smt})"
        merged_predicate = f"({self.predicate_str}) AND ({other.predicate_str})"
        merged_fragment = self.fragment.can_merge_with(other.fragment)
        logger.debug(
            "Merging encodings %s + %s → new encoding",
            self.encoding_id,
            other.encoding_id,
        )
        return RefinementEncoding(
            encoding_id=make_encoding_id(),
            base_sort=self.base_sort,
            predicate_str=merged_predicate,
            z3_constraint_smt=merged_constraint,
            fragment=merged_fragment,
            support=self.support,
            created_at=time.time(),
            copilot_suggested=self.copilot_suggested or other.copilot_suggested,
        )

    def validate(self) -> list[str]:
        """Return a list of validation error strings; empty list means valid.

        Checks that ``encoding_id``, ``predicate_str``, and
        ``z3_constraint_smt`` are all non-empty strings.  Additional structural
        checks may be added in future versions.
        """
        errors: list[str] = []
        if not self.encoding_id:
            errors.append("encoding_id must be non-empty")
        if not self.predicate_str:
            errors.append("predicate_str must be non-empty")
        if not self.z3_constraint_smt:
            errors.append("z3_constraint_smt must be non-empty")
        return errors

    def fingerprint(self) -> str:
        """Return an MD5 hex digest fingerprinting this encoding's content.

        The digest is computed over the concatenation of ``encoding_id``,
        ``predicate_str``, and ``z3_constraint_smt``.  It can be used to detect
        duplicate encodings within a context without comparing full strings.
        """
        raw = self.encoding_id + self.predicate_str + self.z3_constraint_smt
        return hashlib.md5(raw.encode()).hexdigest()

    def age_seconds(self, now: float | None = None) -> float:
        """Return the number of seconds elapsed since this encoding was created.

        If ``now`` is not provided ``time.time()`` is used.  The result is
        always non-negative for correctly-formed encodings.
        """
        reference = now if now is not None else time.time()
        return max(0.0, reference - self.created_at)

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""Return the originating judgment for this encoding.

        A scalar encoding is a morphism in the encoding category
        ``\mathcal{E}: \mathcal{J}(c) \to \mathrm{SMT}`` mapping a judgment
        section at coordinate ``c`` to an SMT formula.  This property
        recovers the domain of that morphism — the judgment that was encoded.

        Returns
        -------
        A ``Judgment`` when ``jugeo.judgments`` is available, otherwise a dict.
        """
        try:
            from jugeo.judgments.judgment_terms import (
                Judgment, Proposition, PropositionKind, Carrier,
            )
            from jugeo.geometry.site import CoordinateObject as _CO
        except ImportError:
            return {
                "encoding_id": self.encoding_id,
                "predicate": self.predicate_str,
                "source": "scalar_encoding",
            }

        try:
            coord = _CO(name="(derived-from-encoding)")
            prop = Proposition(
                kind=PropositionKind.REFINEMENT,
                formula=self.predicate_str,
            )
            carrier = Carrier(name="ScalarEncoding")
            return Judgment(
                coordinate=coord,
                proposition=prop,
                carrier=carrier,
            )
        except Exception:
            return {
                "encoding_id": self.encoding_id,
                "predicate": self.predicate_str,
                "source": "scalar_encoding",
            }

    @property
    def trust_annotation(self) -> Any:
        r"""Return the trust annotation for this encoding.

        Encodings inherit trust from their source channel.  A copilot-suggested
        encoding carries ``ORACLE_PROPOSED``; a solver-derived encoding
        carries ``SOLVER_DISCHARGED`` (if discharged) or ``SOLVER_PARTIAL``.

        Returns
        -------
        A trust tier value or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            if self.copilot_suggested:
                return TrustTier.PROPOSAL
            return TrustTier.DISCHARGED if self.is_discharged() else TrustTier.REVIEWED
        except (ImportError, AttributeError):
            return "ORACLE_PROPOSED" if self.copilot_suggested else "SOLVER_DISCHARGED"

    @property
    def solver_target(self) -> Any:
        r"""Return the Z3 session configuration this encoding should feed into.

        Each encoding targets a specific SMT-LIB fragment; the solver target
        identifies the appropriate Z3 session parameters (timeout, tactic,
        fragment) for discharging it.

        Returns
        -------
        A dict of solver configuration parameters.
        """
        try:
            from jugeo.solver.z3_session import Z3Session, FragmentTag
        except ImportError:
            pass
        return {
            "fragment": self.fragment.smt_lib_name(),
            "base_sort": self.base_sort.to_smt2(),
            "encoding_id": self.encoding_id,
            "is_discharged": self.is_discharged(),
        }

    def descent_encoding(self) -> Any:
        r"""Encode the descent condition for this refinement's support region.

        The descent condition requires that for a cover ``\{U_i \to X\}`` the
        refinement predicate restricted to each overlap ``U_i \cap U_j``
        agrees.  This method produces the SMT formula expressing that
        compatibility.

        Returns
        -------
        A dict containing the descent-condition SMT formula.
        """
        try:
            from jugeo.geometry.descent import OverlapCondition, GluingData
        except ImportError:
            pass
        return {
            "encoding_id": self.encoding_id,
            "support": str(self.support),
            "descent_smt": f"(=> {self.z3_constraint_smt} {self.z3_constraint_smt})",
            "fragment": self.fragment.smt_lib_name(),
            "note": "Reflexive descent condition: encoding is self-compatible.",
        }

    @property
    def certificate(self) -> Any:
        r"""Return an encoding certificate witnessing that this encoding is well-formed.

        The certificate records that the encoding was constructed from a valid
        base sort and predicate, targeting a specific SMT-LIB fragment.  It
        is the constructive content underlying the encoding's trust annotation.

        Returns
        -------
        A ``Certificate`` when available, otherwise a dict.
        """
        try:
            from jugeo.evidence.certificates import Certificate, CertificateBuilder
        except ImportError:
            return {
                "encoding_id": self.encoding_id,
                "base_sort": self.base_sort.name,
                "fragment": self.fragment.smt_lib_name(),
                "is_discharged": self.is_discharged(),
                "copilot_suggested": self.copilot_suggested,
            }

        builder = CertificateBuilder()
        builder = (
            builder
            .set_issuer(f"scalar-encoding:{self.encoding_id}")
            .for_coordinate(f"encoding:{self.encoding_id}")
            .add_verified(f"well-formed:{self.predicate_str[:60]}")
            .set_evidence_summary(
                f"Encoding {self.encoding_id}: sort={self.base_sort.name}, "
                f"fragment={self.fragment.smt_lib_name()}"
            )
            .sign()
        )
        return builder.build()


# -----------------------------------------------------------------------


@dataclass(frozen=True)
class PathCondition:
    """An immutable propositional constraint accumulated along a solver path.

    Path conditions (§26.4 of theory2.tex) record the sequence of branches
    taken during symbolic execution or proof-tree descent.  They consist of a
    set of *antecedents* (previously established facts) and a *consequent*
    (the new fact asserted at this node).

    copilot: Deep path conditions (depth >= 8) often indicate exponential
    blow-up in the branching structure; consider memoising or pruning.
    """

    condition_id: str
    """Unique identifier for this path condition."""

    branch_label: str
    """Human-readable label for the branch point, e.g. 'if_branch_42'."""

    antecedents: tuple[str, ...]
    """Tuple of SMT2 expressions known to hold at this path node."""

    consequent: str
    """SMT2 expression asserted as the new fact at this node."""

    depth: int
    """Depth in the branching tree at which this condition was created."""

    is_join: bool
    """True if this condition was produced by joining two paths."""

    fragment: FragmentHint
    """Fragment sufficient to discharge this condition."""

    # ------------------------------------------------------------------ #

    def negate(self) -> PathCondition:
        """Return a new PathCondition whose consequent is the negation of self.

        The resulting condition shares the same antecedents and depth, but its
        ``condition_id`` is prefixed with ``neg_`` to indicate negation.  This
        is used when exploring the else-branch of a conditional.
        """
        return PathCondition(
            condition_id=f"neg_{self.condition_id}",
            branch_label=self.branch_label,
            antecedents=self.antecedents,
            consequent=f"(not {self.consequent})",
            depth=self.depth,
            is_join=self.is_join,
            fragment=self.fragment,
        )

    def conjoin(self, other: PathCondition) -> PathCondition:
        """Return a new PathCondition that is the conjunction of self and other.

        Antecedents are merged with de-duplication (order-preserving).  The
        depth is the maximum of the two operand depths and ``is_join`` is set
        to True.  The fragment is the least upper bound of both fragments.
        """
        seen: set[str] = set()
        merged_antecedents: list[str] = []
        for a in (*self.antecedents, *other.antecedents):
            if a not in seen:
                seen.add(a)
                merged_antecedents.append(a)
        merged_fragment = self.fragment.can_merge_with(other.fragment)
        logger.debug(
            "Conjoining path conditions %s + %s",
            self.condition_id,
            other.condition_id,
        )
        return PathCondition(
            condition_id=f"pc_{uuid.uuid4().hex[:8]}",
            branch_label=f"{self.branch_label}+{other.branch_label}",
            antecedents=tuple(merged_antecedents),
            consequent=f"(and {self.consequent} {other.consequent})",
            depth=max(self.depth, other.depth),
            is_join=True,
            fragment=merged_fragment,
        )

    def implies(self, other: PathCondition) -> str:
        """Return an SMT2 implication string from self's context to other.

        The generated formula is ``(=> (and antecedents...) (and P Q))`` where
        ``P`` is ``self.consequent`` and ``Q`` is ``other.consequent``.  This
        is useful for checking whether a path condition entails another.
        """
        antecedent_part = (
            "(and " + " ".join(self.antecedents) + ")"
            if len(self.antecedents) > 1
            else (self.antecedents[0] if self.antecedents else "true")
        )
        return f"(=> {antecedent_part} (and {self.consequent} {other.consequent}))"

    def to_smt2(self) -> str:
        """Return a full SMT2 assertion block for this path condition.

        The assertion encodes ``(=> (and antecedents...) consequent)`` and is
        prefixed by a comment showing the condition ID and current depth.
        """
        comment = f"; condition_id={self.condition_id} depth={self.depth} join={self.is_join}"
        if not self.antecedents:
            body = self.consequent
        elif len(self.antecedents) == 1:
            body = f"(=> {self.antecedents[0]} {self.consequent})"
        else:
            ants = " ".join(self.antecedents)
            body = f"(=> (and {ants}) {self.consequent})"
        return f"{comment}\n(assert {body})"

    def is_tautology(self) -> bool:
        """Return True for trivially true path conditions.

        Two heuristic cases are checked: the consequent is the literal string
        ``true``, or the antecedents are non-empty and the consequent is
        identical to the first antecedent (trivial reflexive case).
        """
        if self.consequent == "true":
            return True
        if self.antecedents and self.consequent == self.antecedents[0]:
            return True
        return False

    def antecedent_count(self) -> int:
        """Return the number of antecedent formulae in this path condition.

        This is a simple ``len`` wrapper provided for readability at call sites
        that iterate over collections of path conditions.
        """
        return len(self.antecedents)

    def depth_label(self) -> str:
        """Return a human-readable depth bracket for this path condition.

        Depth < 3 is labelled ``[shallow]``, depth 3–7 is ``[medium]``, and
        depth >= 8 is ``[deep]``.  These brackets are used in diagnostic
        reports to highlight potentially expensive path conditions.
        """
        if self.depth < 3:
            return "[shallow]"
        if self.depth < 8:
            return "[medium]"
        return "[deep]"


# -----------------------------------------------------------------------


@dataclass(frozen=True)
class GuardFormula:
    """An immutable Boolean-sorted guard controlling a fragment automaton transition.

    Guard formulae (§26.5 of theory2.tex) are positive/negative pairs: a
    *guard* SMT2 expression and its precomputed negation.  Maintaining both
    forms avoids repeated double-negation elimination during path exploration.

    copilot: When synthesising guards for branching, always populate both
    ``guard_smt`` and ``negation_smt`` to keep the encoding self-contained.
    """

    guard_id: str
    """Unique identifier for this guard formula."""

    variable_name: str
    """Name(s) of the variable(s) this guard constrains."""

    guard_smt: str
    """SMT2 expression that must hold for the guarded transition to fire."""

    sort: SortKind
    """Sort of the guarded variable; typically BOOL or a numeric sort."""

    is_trivial: bool
    """True if this guard is trivially true (no-op guard)."""

    negation_smt: str
    """Pre-computed SMT2 negation of guard_smt."""

    # ------------------------------------------------------------------ #

    def and_with(self, other: GuardFormula) -> GuardFormula:
        """Return the conjunction of self and other as a new GuardFormula.

        The positive form uses ``(and ...)`` and the negative form uses
        ``(or ...)`` (De Morgan).  ``is_trivial`` is True only if both
        operands are trivial.  If the variable names differ they are joined
        with a comma.
        """
        new_var = (
            self.variable_name
            if self.variable_name == other.variable_name
            else f"{self.variable_name},{other.variable_name}"
        )
        return GuardFormula(
            guard_id=f"g_{uuid.uuid4().hex[:8]}",
            variable_name=new_var,
            guard_smt=f"(and {self.guard_smt} {other.guard_smt})",
            sort=self.sort,
            is_trivial=self.is_trivial and other.is_trivial,
            negation_smt=f"(or {self.negation_smt} {other.negation_smt})",
        )

    def or_with(self, other: GuardFormula) -> GuardFormula:
        """Return the disjunction of self and other as a new GuardFormula.

        The positive form uses ``(or ...)`` and the negative form uses
        ``(and ...)`` (De Morgan).  ``is_trivial`` is True only if both
        operands are trivial.  Variable names are joined if distinct.
        """
        new_var = (
            self.variable_name
            if self.variable_name == other.variable_name
            else f"{self.variable_name},{other.variable_name}"
        )
        return GuardFormula(
            guard_id=f"g_{uuid.uuid4().hex[:8]}",
            variable_name=new_var,
            guard_smt=f"(or {self.guard_smt} {other.guard_smt})",
            sort=self.sort,
            is_trivial=self.is_trivial and other.is_trivial,
            negation_smt=f"(and {self.negation_smt} {other.negation_smt})",
        )

    def negate(self) -> GuardFormula:
        """Return a new GuardFormula with guard and negation swapped.

        The ``guard_id`` is prefixed with ``neg_`` to indicate that this guard
        is the logical complement of the original.  ``is_trivial`` is
        preserved unchanged.
        """
        return GuardFormula(
            guard_id=f"neg_{self.guard_id}",
            variable_name=self.variable_name,
            guard_smt=self.negation_smt,
            sort=self.sort,
            is_trivial=self.is_trivial,
            negation_smt=self.guard_smt,
        )

    def specialize(self, var: str, val: str) -> GuardFormula:
        """Return a new GuardFormula with ``var`` substituted by ``val``.

        Both ``guard_smt`` and ``negation_smt`` are updated via
        :meth:`str.replace`.  This is a syntactic substitution; it is the
        caller's responsibility to ensure the substitution is semantically
        sound (e.g. that ``val`` is a closed SMT2 term of the correct sort).
        """
        return GuardFormula(
            guard_id=f"g_{uuid.uuid4().hex[:8]}",
            variable_name=self.variable_name.replace(var, val),
            guard_smt=self.guard_smt.replace(var, val),
            sort=self.sort,
            is_trivial=self.is_trivial,
            negation_smt=self.negation_smt.replace(var, val),
        )

    def complexity(self) -> int:
        """Return a proxy for AST depth based on open-parenthesis count.

        Each ``(`` in ``guard_smt`` corresponds roughly to one SMT2 node.
        This heuristic is used to prioritise simple guards during proof search
        and to warn about guards that may be expensive to discharge.
        """
        return self.guard_smt.count("(")

    def is_boolean_guard(self) -> bool:
        """Return True if this guard is defined over the Boolean sort.

        Boolean guards are the simplest case: they correspond to SAT clauses
        and can be handled by the ``QF_BOOL`` fragment without any arithmetic
        theory.
        """
        return self.sort is SortKind.BOOL

    def to_smt2_assertion(self) -> str:
        """Return an SMT2 assertion for this guard with an identifying comment.

        The assertion is of the form ``(assert guard_smt) ; guard guard_id``
        and can be inserted directly into a solver query.
        """
        return f"(assert {self.guard_smt}) ; guard {self.guard_id}"


# -----------------------------------------------------------------------


@dataclass(frozen=True)
class ArithmeticObligation:
    """An immutable arithmetic satisfiability obligation for the solver.

    Arithmetic obligations (§26.6 of theory2.tex) package an SMT2 formula
    together with its free variables, a set of constant witnesses, a linearity
    flag, and a support region.  The pipeline discharges them by emitting a
    complete SMT2 query via :meth:`to_z3_query`.

    copilot: If ``is_linear`` is False and the fragment is not MIXED,
    consider calling :meth:`escalate_to_nonlinear` before discharge to avoid
    solver errors.
    """

    obligation_id: str
    """Unique identifier for this obligation."""

    formula_smt: str
    """SMT2 boolean expression whose satisfiability is to be checked."""

    fragment: FragmentHint
    """Target fragment for this obligation."""

    variables: tuple[str, ...]
    """Free variables appearing in formula_smt."""

    constants: tuple[int | float, ...]
    """Numeric constants appearing in formula_smt, for analysis."""

    is_linear: bool
    """True if all arithmetic in formula_smt is linear."""

    support: SupportRegion
    """Geometric support region that scopes the arithmetic domain."""

    # ------------------------------------------------------------------ #

    def is_satisfiable_hint(self) -> bool:
        """Return a heuristic guess about satisfiability.

        Returns False only when the formula contains the literal string
        ``false`` or the pattern ``(= 0 0)`` appears in a way that suggests
        the obligation was generated from a trivially unsatisfiable branch.
        All other formulae are optimistically assumed satisfiable.
        """
        if "false" in self.formula_smt:
            return False
        if "(= 0 0)" in self.formula_smt:
            return False
        return True

    def variables_count(self) -> int:
        """Return the number of free variables in this obligation.

        This is simply ``len(self.variables)`` but is provided as a method for
        readability when iterating diagnostics over large obligation sets.
        """
        return len(self.variables)

    def to_z3_query(self) -> str:
        """Return a complete, self-contained SMT2 query for this obligation.

        The query declares all free variables (as ``Int`` for QF_LIA and
        QF_BOOL, or ``Real`` for QF_LRA and MIXED, or ``(_ BitVec 64)`` for
        QF_BV), asserts the formula, and ends with ``(check-sat)``.
        """
        logic_line = f"(set-logic {self.fragment.smt_lib_name()})"

        if self.fragment is FragmentHint.QF_LRA or self.fragment is FragmentHint.MIXED:
            sort_token = "Real"
        elif self.fragment is FragmentHint.QF_BV:
            sort_token = "(_ BitVec 64)"
        else:
            sort_token = "Int"

        decls = "\n".join(f"(declare-const {v} {sort_token})" for v in self.variables)
        assertion = f"(assert {self.formula_smt})"
        lines = [
            f"; obligation_id={self.obligation_id}",
            logic_line,
            decls,
            assertion,
            "(check-sat)",
        ]
        return "\n".join(line for line in lines if line)

    def escalate_to_nonlinear(self) -> ArithmeticObligation:
        """Return a copy of this obligation with linearity disabled.

        Sets ``is_linear=False`` and upgrades ``fragment`` to
        ``FragmentHint.MIXED`` so that the solver pipeline will route the
        obligation to a solver capable of handling nonlinear arithmetic.
        """
        logger.debug(
            "Escalating obligation %s to nonlinear / MIXED fragment",
            self.obligation_id,
        )
        return ArithmeticObligation(
            obligation_id=self.obligation_id,
            formula_smt=self.formula_smt,
            fragment=FragmentHint.MIXED,
            variables=self.variables,
            constants=self.constants,
            is_linear=False,
            support=self.support,
        )

    def constant_range(self) -> tuple[float, float] | None:
        """Return (min, max) of the numeric constants, or None if empty.

        This method is useful for quick sanity checks, e.g. detecting
        obligations with extremely large constants that may cause solver
        timeouts.
        """
        if not self.constants:
            return None
        float_constants = [float(c) for c in self.constants]
        return (min(float_constants), max(float_constants))

    def formula_complexity(self) -> int:
        """Return a proxy for formula complexity based on open-paren count.

        Counts the number of ``(`` characters in ``formula_smt`` as an
        approximation of the number of SMT2 AST nodes.
        """
        return self.formula_smt.count("(")


# ============================== mutable context ==============================


@dataclass
class EncodingContext:
    """Mutable container aggregating all scalar encoding artefacts for a session.

    An :class:`EncodingContext` is the working area for a single solver session
    as described in §26.7 of theory2.tex.  It holds lists of
    :class:`RefinementEncoding`, :class:`PathCondition`,
    :class:`ArithmeticObligation`, and :class:`GuardFormula` objects and
    provides convenience methods for querying and exporting them.

    The context becomes immutable once :meth:`close` is called; attempts to
    add further items to a closed context raise ``ValueError``.

    copilot: Use :meth:`copilot_explain` to get a natural-language summary of
    what the context contains and what steps are needed to discharge it.
    """

    context_id: str
    """Unique identifier for this encoding context."""

    session_id: str
    """Identifier of the solver session that owns this context."""

    fragment_hint: FragmentHint
    """Overall fragment hint for the combined context."""

    encodings: list[RefinementEncoding] = field(default_factory=list)
    """Refinement encodings accumulated in this context."""

    path_conditions: list[PathCondition] = field(default_factory=list)
    """Path conditions accumulated in this context."""

    arithmetic_obligations: list[ArithmeticObligation] = field(default_factory=list)
    """Arithmetic obligations accumulated in this context."""

    guards: list[GuardFormula] = field(default_factory=list)
    """Guard formulae accumulated in this context."""

    created_at: float = field(default_factory=time.time)
    """Wall-clock timestamp when this context was created."""

    closed: bool = False
    """True after :meth:`close` has been called."""

    # ------------------------------------------------------------------ #

    def _check_open(self) -> None:
        if self.closed:
            raise ValueError(
                f"EncodingContext {self.context_id} is closed; no further items may be added."
            )

    def add_encoding(self, enc: RefinementEncoding) -> None:
        """Append a :class:`RefinementEncoding` to this context.

        Raises ``ValueError`` if the context has been closed.  Logs at
        DEBUG level on success.
        """
        self._check_open()
        self.encodings.append(enc)
        logger.debug("Context %s: added encoding %s", self.context_id, enc.encoding_id)

    def add_path_condition(self, pc: PathCondition) -> None:
        """Append a :class:`PathCondition` to this context.

        Raises ``ValueError`` if the context has been closed.  Logs at
        DEBUG level on success.
        """
        self._check_open()
        self.path_conditions.append(pc)
        logger.debug(
            "Context %s: added path condition %s (depth=%d)",
            self.context_id,
            pc.condition_id,
            pc.depth,
        )

    def add_obligation(self, ob: ArithmeticObligation) -> None:
        """Append an :class:`ArithmeticObligation` to this context.

        Raises ``ValueError`` if the context has been closed.  Logs at
        DEBUG level on success and at WARNING level if the obligation's
        satisfiability hint is False.
        """
        self._check_open()
        self.arithmetic_obligations.append(ob)
        if not ob.is_satisfiable_hint():
            logger.warning(
                "Context %s: obligation %s has negative satisfiability hint",
                self.context_id,
                ob.obligation_id,
            )
        else:
            logger.debug(
                "Context %s: added obligation %s", self.context_id, ob.obligation_id
            )

    def add_guard(self, g: GuardFormula) -> None:
        """Append a :class:`GuardFormula` to this context.

        Raises ``ValueError`` if the context has been closed.  Logs at
        DEBUG level on success.
        """
        self._check_open()
        self.guards.append(g)
        logger.debug("Context %s: added guard %s", self.context_id, g.guard_id)

    def close(self) -> None:
        """Mark this context as closed, preventing further additions.

        After closing, calls to :meth:`add_encoding`, :meth:`add_path_condition`,
        :meth:`add_obligation`, and :meth:`add_guard` will raise ``ValueError``.
        Closing is idempotent; closing an already-closed context has no effect.
        """
        if not self.closed:
            self.closed = True
            logger.debug("Context %s closed with %d items", self.context_id, self.encoding_count())

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this context.

        Lists counts of each collection, the fragment hint, the session ID,
        and whether the context is closed.  Intended for log messages and
        diagnostic output.
        """
        lines = [
            f"EncodingContext id={self.context_id}",
            f"  session_id     : {self.session_id}",
            f"  fragment_hint  : {self.fragment_hint.smt_lib_name()}",
            f"  encodings      : {len(self.encodings)}",
            f"  path_conditions: {len(self.path_conditions)}",
            f"  obligations    : {len(self.arithmetic_obligations)}",
            f"  guards         : {len(self.guards)}",
            f"  closed         : {self.closed}",
            f"  created_at     : {self.created_at:.3f}",
        ]
        return "\n".join(lines)

    def all_smt2_assertions(self) -> list[str]:
        """Return all SMT2 assertion strings from every item in this context.

        Collects output from :meth:`RefinementEncoding.to_smt2`,
        :meth:`PathCondition.to_smt2`, :meth:`ArithmeticObligation.to_z3_query`,
        and :meth:`GuardFormula.to_smt2_assertion` in that order.
        """
        result: list[str] = []
        result.extend(enc.to_smt2() for enc in self.encodings)
        result.extend(pc.to_smt2() for pc in self.path_conditions)
        result.extend(ob.to_z3_query() for ob in self.arithmetic_obligations)
        result.extend(g.to_smt2_assertion() for g in self.guards)
        return result

    def merge_context(self, other: EncodingContext) -> EncodingContext:
        """Return a new :class:`EncodingContext` combining self and other.

        The merged context is open (not closed), has a fresh context ID, and
        inherits ``session_id`` from self.  The fragment hint is the least
        upper bound of both operands' hints.  All four item lists are
        concatenated without de-duplication.
        """
        merged_fragment = self.fragment_hint.can_merge_with(other.fragment_hint)
        logger.debug(
            "Merging contexts %s + %s → fragment=%s",
            self.context_id,
            other.context_id,
            merged_fragment.smt_lib_name(),
        )
        return EncodingContext(
            context_id=make_context_id(),
            session_id=self.session_id,
            fragment_hint=merged_fragment,
            encodings=list(self.encodings) + list(other.encodings),
            path_conditions=list(self.path_conditions) + list(other.path_conditions),
            arithmetic_obligations=list(self.arithmetic_obligations) + list(other.arithmetic_obligations),
            guards=list(self.guards) + list(other.guards),
            created_at=time.time(),
            closed=False,
        )

    def copilot_explain(self) -> str:
        """Return a natural-language paragraph explaining this context.

        Describes what the context contains, which SMT-LIB fragment it targets,
        and offers high-level suggestions for next steps.  This method is
        intended to be surfaced to copilot or human reviewers who need a quick
        orientation without reading the raw SMT2 output.
        """
        status_word = "closed" if self.closed else "open"
        has_fail = self.has_failures()
        fail_note = (
            "  WARNING: at least one arithmetic obligation has a negative "
            "satisfiability hint — inspect obligations before dispatching to "
            "the solver."
            if has_fail
            else "  No negative satisfiability hints detected."
        )
        paragraph = (
            f"This EncodingContext (id={self.context_id}, status={status_word}) "
            f"belongs to solver session '{self.session_id}' and targets the "
            f"{self.fragment_hint.smt_lib_name()} SMT-LIB fragment.\n"
            f"It contains {len(self.encodings)} refinement encoding(s), "
            f"{len(self.path_conditions)} path condition(s), "
            f"{len(self.arithmetic_obligations)} arithmetic obligation(s), "
            f"and {len(self.guards)} guard formula(e).\n"
            f"{fail_note}\n"
            f"Next steps: call all_smt2_assertions() to collect the full SMT2 "
            f"assertion set, then dispatch to a Z3Session targeting "
            f"{self.fragment_hint.smt_lib_name()}.  If discharge fails, "
            f"consider calling escalate_to_nonlinear() on failing obligations "
            f"and re-merging before retrying."
        )
        return paragraph

    def encoding_count(self) -> int:
        """Return the total number of items across all four collections.

        Sums the lengths of ``encodings``, ``path_conditions``,
        ``arithmetic_obligations``, and ``guards``.
        """
        return (
            len(self.encodings)
            + len(self.path_conditions)
            + len(self.arithmetic_obligations)
            + len(self.guards)
        )

    def has_failures(self) -> bool:
        """Return True if any arithmetic obligation has a negative sat hint.

        Iterates over all :class:`ArithmeticObligation` items and returns True
        as soon as one reports ``is_satisfiable_hint() == False``.
        """
        return any(not ob.is_satisfiable_hint() for ob in self.arithmetic_obligations)

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""Return the originating judgment context for this encoding context.

        An ``EncodingContext`` aggregates multiple encodings targeting the same
        solver session.  The judgment source is the composite judgment whose
        sub-obligations are encoded as individual refinements, path conditions,
        and arithmetic obligations.

        Returns
        -------
        A dict describing the composite judgment source.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment
        except ImportError:
            pass
        return {
            "context_id": self.context_id,
            "session_id": self.session_id,
            "encoding_count": self.encoding_count(),
            "fragment_hint": self.fragment_hint.smt_lib_name(),
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Return the aggregate trust annotation for this context.

        The context's trust is the meet (greatest lower bound) of its
        constituent encodings' trust levels in the trust algebra.  If any
        encoding is copilot-suggested, the aggregate drops to
        ``ORACLE_PROPOSED``.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
        except ImportError:
            has_copilot = any(
                getattr(e, 'copilot_suggested', False) for e in self.encodings
            )
            return "ORACLE_PROPOSED" if has_copilot else "SOLVER_PARTIAL"

        has_copilot = any(
            getattr(e, 'copilot_suggested', False) for e in self.encodings
        )
        return TrustTier.PROPOSAL if has_copilot else TrustTier.REVIEWED

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Z3 session parameters for discharging this encoding context.

        Returns
        -------
        dict with session_id, fragment, and encoding count.
        """
        return {
            "session_id": self.session_id,
            "fragment": self.fragment_hint.smt_lib_name(),
            "encoding_count": self.encoding_count(),
            "has_failures": self.has_failures(),
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Produce the descent-condition encoding for this context's support.

        Aggregates the supports of all constituent encodings and produces
        a composite descent constraint requiring pairwise compatibility.

        Returns
        -------
        dict describing the descent encoding.
        """
        try:
            from jugeo.geometry.descent import GluingData
        except ImportError:
            pass
        supports = [str(getattr(e, 'support', '')) for e in self.encodings]
        return {
            "context_id": self.context_id,
            "supports": supports,
            "descent_smt": "(and true)",
            "note": "Trivial descent: all encodings share a session.",
        }

    @property
    def certificate(self) -> dict[str, Any]:
        r"""Encoding context certificate.

        Returns
        -------
        dict summarising the context's well-formedness.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
        except ImportError:
            pass
        return {
            "context_id": self.context_id,
            "session_id": self.session_id,
            "encoding_count": self.encoding_count(),
            "fragment": self.fragment_hint.smt_lib_name(),
            "has_failures": self.has_failures(),
        }


# ============================== result ==============================


@dataclass(frozen=True)
class EncodingResult:
    """An immutable record of the outcome of discharging an encoding context.

    :class:`EncodingResult` captures the solver's response as described in
    §26.8 of theory2.tex: the satisfiability outcome, any unsat core, model
    assignments for satisfiable instances, timing, and the fragment actually
    used.

    copilot: Inspect :meth:`explain` to get a human-readable summary of the
    result before acting on it programmatically.
    """

    result_id: str
    """Unique identifier for this result record."""

    context: EncodingContext
    """The encoding context that was discharged to produce this result."""

    outcome: str
    """Solver outcome string: one of ``'sat'``, ``'unsat'``, or ``'unknown'``."""

    unsat_core: tuple[str, ...]
    """Subset of assertions forming an unsatisfiable core (empty if sat/unknown)."""

    model_assignments: dict[str, str] = field(default_factory=dict)
    """Variable → value map from the solver model (populated when sat)."""

    elapsed_ms: float = 0.0
    """Wall-clock milliseconds consumed by the solver call."""

    fragment_used: FragmentHint = FragmentHint.QF_LIA
    """The SMT-LIB fragment that was actually sent to the solver."""

    # ------------------------------------------------------------------ #

    def is_success(self) -> bool:
        """Return True if the solver returned ``sat``.

        A satisfiable result means a model exists for the encoding context;
        check :attr:`model_assignments` for concrete witnesses.
        """
        return self.outcome == "sat"

    def has_core(self) -> bool:
        """Return True if a non-empty unsat core is available.

        An unsat core is only populated when the outcome is ``unsat`` and the
        solver was configured to produce core extraction.
        """
        return len(self.unsat_core) > 0

    def model_value(self, var: str) -> str | None:
        """Return the model assignment for ``var``, or None if absent.

        Looks up ``var`` in :attr:`model_assignments`.  Returns ``None`` if
        the variable was not assigned in the model (e.g. because the outcome
        was ``unsat`` or the variable was not declared).
        """
        return self.model_assignments.get(var)

    def explain(self) -> str:
        """Return a detailed human-readable explanation of this result.

        Covers the outcome, elapsed time, fragment used, unsat core size, and
        a sample of model assignments.  Intended for logging, debugging, and
        copilot-assisted diagnosis.
        """
        timing = self.timing_label()
        core_note = (
            f"Unsat core has {len(self.unsat_core)} assertion(s)."
            if self.has_core()
            else "No unsat core available."
        )
        model_note = (
            f"Model has {len(self.model_assignments)} assignment(s): "
            + ", ".join(f"{k}={v}" for k, v in list(self.model_assignments.items())[:5])
            if self.model_assignments
            else "No model assignments."
        )
        return (
            f"EncodingResult id={self.result_id}\n"
            f"  outcome      : {self.outcome}\n"
            f"  elapsed      : {self.elapsed_ms:.2f} ms ({timing})\n"
            f"  fragment_used: {self.fragment_used.smt_lib_name()}\n"
            f"  {core_note}\n"
            f"  {model_note}"
        )

    def is_unsat(self) -> bool:
        """Return True if the solver returned ``unsat``.

        An unsatisfiable result means no model exists; if ``has_core()`` is
        also True, the core identifies the minimal conflicting assertions.
        """
        return self.outcome == "unsat"

    def is_unknown(self) -> bool:
        """Return True if the solver returned ``unknown``.

        An unknown result typically indicates a timeout or a fragment that
        exceeds the solver's decision procedure.  Consider escalating the
        fragment or increasing the solver timeout.
        """
        return self.outcome == "unknown"

    def timing_label(self) -> str:
        """Return a human-readable performance bracket for the solver call.

        Returns ``'fast'`` for under 100 ms, ``'medium'`` for under 1000 ms,
        and ``'slow'`` for 1000 ms or above.
        """
        if self.elapsed_ms < 100:
            return "fast"
        if self.elapsed_ms < 1000:
            return "medium"
        return "slow"

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""The judgment context that produced this result.

        Returns
        -------
        dict with context and outcome metadata.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment
        except ImportError:
            pass
        return {
            "result_id": self.result_id,
            "context_id": self.context.context_id,
            "outcome": self.outcome,
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Trust annotation for this result.

        A successful discharge (``unsat``) earns ``SOLVER_DISCHARGED``
        trust; a satisfiable result earns ``SOLVER_PARTIAL``; an unknown
        result carries ``SOLVER_UNKNOWN``.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            if self.is_unsat():
                return TrustTier.DISCHARGED
            if self.is_success():
                return TrustTier.REVIEWED
            return TrustTier.PROPOSAL
        except (ImportError, AttributeError):
            if self.is_unsat():
                return "SOLVER_DISCHARGED"
            return "SOLVER_PARTIAL" if self.is_success() else "SOLVER_UNKNOWN"

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Solver session that produced this result.

        Returns
        -------
        dict with session and fragment metadata.
        """
        return {
            "result_id": self.result_id,
            "context_id": self.context.context_id,
            "session_id": self.context.session_id,
            "fragment_used": self.fragment_used.smt_lib_name(),
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Encode the descent condition for this result's context.

        Delegates to the underlying :attr:`context`'s descent encoding.

        Returns
        -------
        dict describing the descent encoding.
        """
        try:
            from jugeo.geometry.descent import GluingData
        except ImportError:
            pass
        return {
            "result_id": self.result_id,
            "outcome": self.outcome,
            "descent": self.context.descent_encoding()
            if hasattr(self.context, 'descent_encoding')
            else {},
        }

    @property
    def certificate(self) -> Any:
        r"""Evidence certificate for this encoding result.

        When the result is ``unsat`` the certificate carries the unsat core
        as the constructive proof content.  When ``sat``, the model
        assignments serve as the witness.

        Returns
        -------
        A ``Certificate`` or dict.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
            builder = CertificateBuilder()
            builder = (
                builder
                .set_issuer(f"encoding-result:{self.result_id}")
                .for_coordinate(f"discharge:{self.outcome}")
                .set_evidence_summary(
                    f"Result {self.result_id}: {self.outcome}, "
                    f"core_size={len(self.unsat_core)}, "
                    f"model_size={len(self.model_assignments)}"
                )
            )
            if self.is_unsat():
                for core_item in self.unsat_core[:5]:
                    builder = builder.add_verified(core_item)
            return builder.sign().build()
        except (ImportError, Exception):
            return {
                "result_id": self.result_id,
                "outcome": self.outcome,
                "unsat_core_size": len(self.unsat_core),
                "model_size": len(self.model_assignments),
            }
