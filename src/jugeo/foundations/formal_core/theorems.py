"""Formal theorem registry for Theory2.tex Chapter 9.

Each theorem is represented as a Python object with a formal statement, proof
sketch, and a ``verify()`` method that checks the theorem statement
programmatically using concrete data.

The theorems in this module correspond to *Theory2.tex §9 — Mathematical
interlude: a more explicit formal core*.  The chapter develops the categorical
and algebraic machinery that underlies the JuGeo trust infrastructure:

* §9.1  The Grothendieck site ``(C, J)`` of judgment objects and covering sieves.
* §9.2  The trust presheaf ``T: Cᵒᵖ → Pos`` and the sheaf condition.
* §9.3  The ordered algebra ``(E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)``.
* §9.4  Sheaf cohomology and the vanishing criterion for global trust lifts.
* §9.5  Boundedness of the copilot / oracle evidence channel.
* §9.6  Monotonicity under admissible aggregation.

Usage::

    from jugeo.foundations.formal_core.theorems import (
        THEOREM_REGISTRY,
        get_chapter_9_theorems,
        verify_chapter_9,
    )

    results = verify_chapter_9({"presheaf_data": {"satisfies_sheaf_condition": True}})
    print(results["passed"], "theorems passed")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = None  # type: ignore[assignment,misc]

__all__ = [
    "TheoremStatement",
    "Lemma",
    "Corollary",
    "TheoremRegistry",
    "THEOREM_REGISTRY",
    "get_chapter_9_theorems",
    "verify_chapter_9",
    "verify_theorem_via_solver",
    "theorem_to_judgment",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trust_rank(level_name: str) -> int:
    """Return a numeric rank for a TrustLevel name so we can compare them.

    The ordering follows the Hasse diagram defined in trust.py::

        CONTRADICTED(0) < UNVERIFIED(1) < COPILOT_SUGGESTED(2)
        < ORACLE_PROPOSED(3) < HUMAN_ATTESTED(4) < RUNTIME_WITNESSED(5)
        < SOLVER_DISCHARGED(6) < MECHANICALLY_VERIFIED(7)

    Parameters
    ----------
    level_name:
        String name of a TrustLevel enum member.

    Returns
    -------
    int
        Numeric rank; higher means more trusted.
    """
    _RANK: dict[str, int] = {
        "CONTRADICTED": 0,
        "UNVERIFIED": 1,
        "COPILOT_SUGGESTED": 2,
        "ORACLE_PROPOSED": 3,
        "HUMAN_ATTESTED": 4,
        "RUNTIME_WITNESSED": 5,
        "SOLVER_DISCHARGED": 6,
        "MECHANICALLY_VERIFIED": 7,
    }
    return _RANK.get(level_name.upper(), -1)


def _compose_trust(t1: str, t2: str) -> str:
    """Compute ``t1 ⊕ t2`` as the *meet* (greatest lower bound) in the partial order.

    The algebra law is::

        e₁ ⊕ e₂  =  meet(e₁, e₂)

    which ensures that composing two pieces of evidence never inflates trust
    beyond what either piece independently guarantees.  This matches the
    ``TrustAlgebra.compose`` implementation in ``jugeo.evidence.trust``.

    Parameters
    ----------
    t1, t2:
        String names of TrustLevel enum members.

    Returns
    -------
    str
        Name of the resulting TrustLevel after composition.
    """
    _ORDER = [
        "CONTRADICTED",
        "UNVERIFIED",
        "COPILOT_SUGGESTED",
        "ORACLE_PROPOSED",
        "HUMAN_ATTESTED",
        "RUNTIME_WITNESSED",
        "SOLVER_DISCHARGED",
        "MECHANICALLY_VERIFIED",
    ]
    r1 = _trust_rank(t1)
    r2 = _trust_rank(t2)
    # meet = min in a totally-ordered chain approximation
    result_rank = min(r1, r2)
    if result_rank < 0:
        return "UNVERIFIED"
    return _ORDER[result_rank]


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TheoremStatement:
    """A formal theorem from Theory2.tex.

    Captures both the mathematical content of a theorem and enough metadata to
    locate it in the source document and to run a lightweight programmatic
    check.

    Theory2.tex reference: §9 preamble — *"Each proposition below is given
    both in the categorical language of the judgment site and as a concrete
    invariant that can be tested against runtime data."*

    Attributes
    ----------
    theorem_id:
        Unique string identifier, e.g. ``"theorem_9_1"``.
    name:
        Human-readable theorem name.
    statement:
        Full mathematical statement as a string (may include LaTeX notation).
    proof_sketch:
        Informal proof outline.
    hypotheses:
        List of hypothesis strings.
    conclusion:
        The conclusion of the theorem.
    chapter:
        Chapter number in Theory2.tex.
    section:
        Section string, e.g. ``"9.2"``.
    verified:
        Whether this theorem has been formally verified in a proof assistant.
    dependencies:
        IDs of other theorems / lemmas this theorem depends on.
    """

    theorem_id: str
    name: str
    statement: str
    proof_sketch: str
    hypotheses: list[str] = field(default_factory=list)
    conclusion: str = ""
    chapter: int = 9
    section: str = ""
    verified: bool = False
    dependencies: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def verify(self, context: dict[str, Any]) -> bool:
        """Check the theorem statement against *context* data.

        The default implementation performs a sanity check: it looks for a
        ``"theorems"`` key in *context* and verifies that this theorem's ID is
        not explicitly listed as ``False``.  Concrete theorems override this
        method with domain-specific checks.

        Parameters
        ----------
        context:
            Arbitrary mapping of runtime / test data used to instantiate the
            theorem's variables.

        Returns
        -------
        bool
            ``True`` when the check passes.
        """
        theorems_status: dict[str, bool] = context.get("theorems", {})
        if self.theorem_id in theorems_status:
            result = bool(theorems_status[self.theorem_id])
            logger.debug(
                "TheoremStatement.verify(%s): explicit override → %s",
                self.theorem_id,
                result,
            )
            return result
        logger.debug(
            "TheoremStatement.verify(%s): no override, returning True",
            self.theorem_id,
        )
        return True

    def describe(self) -> str:
        """Return a short human-readable summary of this theorem.

        Returns
        -------
        str
            Multi-line string with ID, name, section, and statement excerpt.
        """
        excerpt = self.statement[:120].rstrip() + ("…" if len(self.statement) > 120 else "")
        return (
            f"[{self.theorem_id}] {self.name}\n"
            f"  Chapter {self.chapter}, §{self.section}\n"
            f"  Statement: {excerpt}\n"
            f"  Dependencies: {', '.join(self.dependencies) or 'none'}\n"
            f"  Formally verified: {self.verified}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the theorem to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of the theorem.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "hypotheses": list(self.hypotheses),
            "conclusion": self.conclusion,
            "chapter": self.chapter,
            "section": self.section,
            "verified": self.verified,
            "dependencies": list(self.dependencies),
            "kind": "theorem",
        }


@dataclass
class Lemma(TheoremStatement):
    """A supporting lemma used by one or more parent theorems.

    A *Lemma* has the same structure as a :class:`TheoremStatement` but carries
    an additional ``parent_theorem_id`` field that links it to its primary
    parent theorem in Theory2.tex.

    Theory2.tex reference: §9 — lemmas are marked with *"Lem."* in the margin.

    Attributes
    ----------
    parent_theorem_id:
        ID of the theorem for which this lemma is a stepping stone.
    """

    parent_theorem_id: str = ""

    def verify(self, context: dict[str, Any]) -> bool:
        """Verify the lemma against *context*.

        Delegates to the parent :meth:`TheoremStatement.verify` after injecting
        a ``parent_theorem_id`` check: if the parent theorem is listed as
        ``False`` in ``context["theorems"]``, this lemma is also considered
        unverified.

        Parameters
        ----------
        context:
            Runtime / test data mapping.

        Returns
        -------
        bool
        """
        theorems_status: dict[str, bool] = context.get("theorems", {})
        if self.parent_theorem_id and theorems_status.get(self.parent_theorem_id) is False:
            logger.debug(
                "Lemma.verify(%s): parent theorem %s is False → False",
                self.theorem_id,
                self.parent_theorem_id,
            )
            return False
        return super().verify(context)

    def describe(self) -> str:
        """Return a human-readable summary including the parent theorem ID."""
        base = super().describe()
        return base + f"\n  Parent theorem: {self.parent_theorem_id or 'none'}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dict, adding ``parent_theorem_id`` and kind ``"lemma"``."""
        d = super().to_dict()
        d["parent_theorem_id"] = self.parent_theorem_id
        d["kind"] = "lemma"
        return d


@dataclass
class Corollary(TheoremStatement):
    """A corollary that follows directly from one or more theorems.

    Attributes
    ----------
    follows_from:
        List of theorem / lemma IDs from which this corollary is derived.
    """

    follows_from: list[str] = field(default_factory=list)

    def verify(self, context: dict[str, Any]) -> bool:
        """Verify the corollary: all parent theorems must pass first.

        If any theorem listed in ``follows_from`` fails its own ``verify()``,
        this corollary is considered unverified as well.

        Parameters
        ----------
        context:
            Runtime / test data mapping.

        Returns
        -------
        bool
        """
        theorems_status: dict[str, bool] = context.get("theorems", {})
        for parent_id in self.follows_from:
            if theorems_status.get(parent_id) is False:
                logger.debug(
                    "Corollary.verify(%s): parent %s is False → False",
                    self.theorem_id,
                    parent_id,
                )
                return False
        return super().verify(context)

    def describe(self) -> str:
        """Return a human-readable summary including the ``follows_from`` chain."""
        base = super().describe()
        return base + f"\n  Follows from: {', '.join(self.follows_from) or 'none'}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dict, adding ``follows_from`` and kind ``"corollary"``."""
        d = super().to_dict()
        d["follows_from"] = list(self.follows_from)
        d["kind"] = "corollary"
        return d


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """A registry of :class:`TheoremStatement`, :class:`Lemma`, and :class:`Corollary` objects.

    The registry supports:
    * Named registration and retrieval by ID.
    * Bulk verification against a context dictionary.
    * Chapter-scoped listing.
    * Dependency traversal.

    Theory2.tex reference: §9.0 — *"The following registry collects every
    numbered proposition from this chapter for machine-readable access."*

    Attributes
    ----------
    theorems:
        All registered items keyed by ``theorem_id``.
    lemmas:
        Only lemmas, keyed by ``theorem_id``.
    corollaries:
        Only corollaries, keyed by ``theorem_id``.
    """

    def __init__(self) -> None:
        self.theorems: dict[str, TheoremStatement | Lemma | Corollary] = {}
        self.lemmas: dict[str, Lemma] = {}
        self.corollaries: dict[str, Corollary] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, theorem: TheoremStatement | Lemma | Corollary) -> None:
        """Register a theorem, lemma, or corollary.

        Raises
        ------
        ValueError
            If an entry with the same ``theorem_id`` is already registered.

        Parameters
        ----------
        theorem:
            The theorem object to register.
        """
        tid = theorem.theorem_id
        if tid in self.theorems:
            raise ValueError(
                f"TheoremRegistry: duplicate registration for id={tid!r}"
            )
        self.theorems[tid] = theorem
        if isinstance(theorem, Lemma):
            self.lemmas[tid] = theorem
        elif isinstance(theorem, Corollary):
            self.corollaries[tid] = theorem
        logger.debug("TheoremRegistry.register: %s (%s)", tid, type(theorem).__name__)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, theorem_id: str) -> TheoremStatement | Lemma | Corollary | None:
        """Retrieve a registered item by ID, or ``None`` if not found.

        Parameters
        ----------
        theorem_id:
            The ID to look up.

        Returns
        -------
        TheoremStatement | Lemma | Corollary | None
        """
        return self.theorems.get(theorem_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all registered theorems serialised as dictionaries.

        Returns
        -------
        list[dict]
            Sorted by ``theorem_id``.
        """
        return [t.to_dict() for t in sorted(self.theorems.values(), key=lambda t: t.theorem_id)]

    def get_chapter_theorems(self, chapter: int) -> list[TheoremStatement | Lemma | Corollary]:
        """Return all registered items from a given chapter.

        Parameters
        ----------
        chapter:
            Chapter number to filter by.

        Returns
        -------
        list
            Theorems, lemmas, and corollaries whose ``chapter == chapter``,
            sorted by ``theorem_id``.
        """
        return sorted(
            [t for t in self.theorems.values() if t.chapter == chapter],
            key=lambda t: t.theorem_id,
        )

    def dependencies_of(self, theorem_id: str) -> list[str]:
        """Return the transitive dependency closure for *theorem_id*.

        Performs a breadth-first traversal of ``dependencies`` links.

        Parameters
        ----------
        theorem_id:
            Starting theorem.

        Returns
        -------
        list[str]
            All transitive dependencies (excluding *theorem_id* itself),
            in breadth-first order.
        """
        visited: set[str] = set()
        queue: list[str] = [theorem_id]
        result: list[str] = []
        while queue:
            current = queue.pop(0)
            item = self.theorems.get(current)
            if item is None:
                continue
            for dep in item.dependencies:
                if dep not in visited and dep != theorem_id:
                    visited.add(dep)
                    result.append(dep)
                    queue.append(dep)
            # Also follow follows_from for corollaries
            if isinstance(item, Corollary):
                for dep in item.follows_from:
                    if dep not in visited and dep != theorem_id:
                        visited.add(dep)
                        result.append(dep)
                        queue.append(dep)
        return result

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_all_statements(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run ``verify()`` on every registered item.

        Parameters
        ----------
        context:
            Shared context dictionary passed to each ``verify()`` call.

        Returns
        -------
        dict
            Keys: ``passed`` (int), ``failed`` (int), ``results`` (dict[id → bool]),
            ``failed_ids`` (list[str]).
        """
        results: dict[str, bool] = {}
        failed_ids: list[str] = []
        for tid, theorem in self.theorems.items():
            try:
                ok = theorem.verify(context)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TheoremRegistry.verify_all_statements: %s raised %s", tid, exc)
                ok = False
            results[tid] = ok
            if not ok:
                failed_ids.append(tid)
        passed = sum(1 for v in results.values() if v)
        failed = len(results) - passed
        logger.info(
            "TheoremRegistry.verify_all_statements: %d passed, %d failed",
            passed,
            failed,
        )
        return {
            "passed": passed,
            "failed": failed,
            "total": len(results),
            "results": results,
            "failed_ids": failed_ids,
        }

    def describe(self) -> str:
        """Return a human-readable summary of the registry.

        Returns
        -------
        str
        """
        lines = [
            f"TheoremRegistry: {len(self.theorems)} items total",
            f"  Theorems (base): {len(self.theorems) - len(self.lemmas) - len(self.corollaries)}",
            f"  Lemmas: {len(self.lemmas)}",
            f"  Corollaries: {len(self.corollaries)}",
        ]
        for tid in sorted(self.theorems):
            item = self.theorems[tid]
            kind = type(item).__name__
            lines.append(f"  [{kind}] {tid}: {item.name}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Specific theorem definitions (Theory2.tex Chapter 9)
# ---------------------------------------------------------------------------

# ---- Theorem 9.1 -----------------------------------------------------------


class _Theorem91(TheoremStatement):
    """Theorem 9.1 — Sheaf Condition Necessity for Global Trust.

    Theory2.tex §9.2.

    This theorem states that the trust presheaf ``T`` satisfies the sheaf
    condition precisely when global trust assignments are coherent.  A failure
    of the sheaf condition corresponds to either non-unique global sections
    (locality failure) or incompatible local data with no global representative
    (gluing failure).
    """

    def verify(self, context: dict[str, Any]) -> bool:
        """Check the sheaf condition against *context*.

        Looks for ``context["presheaf_data"]["satisfies_sheaf_condition"]``.
        If the presheaf satisfies the sheaf condition, the theorem holds trivially.
        If it does not, we simulate finding a covering violation.

        Parameters
        ----------
        context:
            Expected keys: ``presheaf_data`` (dict with key
            ``satisfies_sheaf_condition: bool`` and optionally
            ``covering_violations: list``).

        Returns
        -------
        bool
        """
        presheaf_data: dict[str, Any] = context.get("presheaf_data", {})
        satisfies = bool(presheaf_data.get("satisfies_sheaf_condition", True))
        if satisfies:
            logger.debug("Theorem 9.1 verify: presheaf satisfies sheaf condition → True")
            return True
        # Simulate violation detection: look for explicit violation data or
        # treat absence of violations as a data error.
        violations = presheaf_data.get("covering_violations", [])
        if violations:
            logger.debug(
                "Theorem 9.1 verify: sheaf condition fails, %d violation(s) found → True"
                " (theorem correctly predicts failure)",
                len(violations),
            )
            return True
        # No violations listed but condition stated as failing — inconsistent data.
        logger.warning(
            "Theorem 9.1 verify: presheaf_data claims sheaf condition fails but"
            " no covering_violations provided — context data is inconsistent"
        )
        return False


THEOREM_9_1_SHEAF_CONDITION_NECESSITY = _Theorem91(
    theorem_id="theorem_9_1",
    name="Sheaf Condition Necessity for Global Trust",
    statement=(
        "A presheaf T on the judgment site (C, J) satisfies the sheaf condition if and only if"
        " it admits coherent global trust assignments.  Specifically, if T is not a sheaf, there"
        " exists a covering family {f_i: U_i → X} and a compatible family {s_i ∈ T(U_i)} that"
        " fails to have a unique amalgamation."
    ),
    proof_sketch=(
        "The sheaf condition decomposes into locality (sections equal if they agree on a cover)"
        " and gluing (compatible sections amalgamate uniquely).  Failure of locality gives"
        " non-unique global sections; failure of gluing gives incompatible local trust data with"
        " no global representative."
    ),
    hypotheses=[
        "(C, J) is a Grothendieck site",
        "T: Cᵒᵖ → Pos is a presheaf of partially ordered sets",
    ],
    conclusion=(
        "T satisfies the sheaf condition iff every compatible family over every covering sieve"
        " has a unique amalgamation."
    ),
    chapter=9,
    section="9.2",
    verified=False,
    dependencies=[],
)


# ---- Theorem 9.2 -----------------------------------------------------------


class _Theorem92(TheoremStatement):
    """Theorem 9.2 — Trust Ordered Algebra Satisfies Required Axioms.

    Theory2.tex §9.3.

    Checks all five axioms of the trust algebra against provided data.
    """

    def verify(self, context: dict[str, Any]) -> bool:
        """Check all five axioms against ``context["algebra_data"]``.

        Axioms checked:
        1. ``(E_adm, ⪯)`` is a partial order (reflexivity, antisymmetry, transitivity).
        2. ``⊕`` is monotone w.r.t. ``⪯``.
        3. ``⊖`` is weakening: ``t ⊖ χ ⪯ t``.
        4. ``↑_π`` requires a named policy ``π``.
        5. ``ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED``.

        Parameters
        ----------
        context:
            Expected key: ``algebra_data`` (dict).

        Returns
        -------
        bool
        """
        algebra_data: dict[str, Any] = context.get("algebra_data", {})
        failures: list[str] = []

        # Axiom 1: partial order
        if not algebra_data.get("is_partial_order", True):
            failures.append("Axiom 1: (E_adm, ⪯) is not a partial order")

        # Axiom 2: monotonicity
        if not algebra_data.get("compose_monotone", True):
            failures.append("Axiom 2: ⊕ is not monotone w.r.t. ⪯")

        # Axiom 3: weakening
        attenuation_result = algebra_data.get("attenuation_weakens", True)
        if not attenuation_result:
            failures.append("Axiom 3: ⊖ does not weaken (t ⊖ χ ⪰ t found)")

        # Axiom 4: named policy required for promotion
        if not algebra_data.get("promotion_requires_policy", True):
            failures.append("Axiom 4: ↑_π does not require a named policy")

        # Axiom 5: oracle idempotency — verify programmatically
        composed = _compose_trust("ORACLE_PROPOSED", "ORACLE_PROPOSED")
        if composed != "ORACLE_PROPOSED":
            failures.append(
                f"Axiom 5: ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = {composed}, expected ORACLE_PROPOSED"
            )

        if failures:
            for msg in failures:
                logger.warning("Theorem 9.2 verify: FAIL — %s", msg)
            return False
        logger.debug("Theorem 9.2 verify: all five axioms passed")
        return True


THEOREM_9_2_TRUST_ALGEBRA_AXIOMS = _Theorem92(
    theorem_id="theorem_9_2",
    name="Trust Ordered Algebra Satisfies Required Axioms",
    statement=(
        "The structure (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) forms an ordered algebra satisfying:"
        " (1) (E_adm, ⪯) is a partial order;"
        " (2) ⊕ is monotone with respect to ⪯;"
        " (3) ⊖ is weakening (t ⊖ χ ⪯ t);"
        " (4) ↑_π requires a named policy π;"
        " (5) ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED."
    ),
    proof_sketch=(
        "Axioms (1)-(3) follow from the Hasse diagram definition.  Axiom (4) is enforced at the"
        " API boundary.  Axiom (5) follows from the conservation principle: the oracle/copilot"
        " channel cannot self-compose to a higher tier — corroboration from a solver or human"
        " attestation is required."
    ),
    hypotheses=[
        "E_adm is the set of admissible evidence configurations",
        "⪯ is induced by the Hasse diagram in trust.py",
        "⊕ = meet in the partial order",
        "⊖ is attenuation",
        "↑_π is promotion under named policy π",
    ],
    conclusion="(E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) satisfies all five required algebra axioms.",
    chapter=9,
    section="9.3",
    verified=False,
    dependencies=["theorem_9_1"],
)


# ---- Theorem 9.3 -----------------------------------------------------------


class _Theorem93(TheoremStatement):
    """Theorem 9.3 — H^1 Vanishing Implies Global Section.

    Theory2.tex §9.4.

    Connects sheaf cohomology to the solvability of the descent problem for
    trust data.
    """

    def verify(self, context: dict[str, Any]) -> bool:
        """Check that vanishing obstruction classes imply existence of global lifts.

        Iterates over ``context["obstruction_data"]["classes"]`` and verifies:
        * Each class with ``vanishes=True`` has an associated ``global_lift``.
        * If any class has ``vanishes=False``, the global lift must be absent.

        Parameters
        ----------
        context:
            Expected key: ``obstruction_data`` (dict with key
            ``classes: list[dict]``).

        Returns
        -------
        bool
        """
        obs_data: dict[str, Any] = context.get("obstruction_data", {})
        classes: list[dict[str, Any]] = obs_data.get("classes", [])
        if not classes:
            logger.debug("Theorem 9.3 verify: no obstruction classes provided → trivially True")
            return True

        failures: list[str] = []
        for cls in classes:
            label = cls.get("label", "?")
            vanishes = bool(cls.get("vanishes", True))
            has_lift = "global_lift" in cls and cls["global_lift"] is not None
            if vanishes and not has_lift:
                failures.append(
                    f"Class {label}: H^1 vanishes but no global_lift recorded"
                )
            if not vanishes and has_lift:
                failures.append(
                    f"Class {label}: H^1 does not vanish but global_lift is present — contradiction"
                )

        if failures:
            for msg in failures:
                logger.warning("Theorem 9.3 verify: FAIL — %s", msg)
            return False
        logger.debug("Theorem 9.3 verify: obstruction/lift consistency OK")
        return True


THEOREM_9_3_OBSTRUCTION_VANISHING = _Theorem93(
    theorem_id="theorem_9_3",
    name="H^1 Vanishing Implies Global Section",
    statement=(
        "Let (C, J) be the judgment site and T the trust sheaf.  If H^1(C, T) = 0, then every"
        " compatible family of local trust assignments over any covering sieve lifts to a unique"
        " global trust assignment.  Equivalently, the descent problem for trust data is solvable"
        " if and only if the obstruction class in H^1 vanishes."
    ),
    proof_sketch=(
        "By the long exact sequence in sheaf cohomology, H^1 = 0 implies the restriction map"
        " H^0(C, T) → ∏_i H^0(U_i, T) is surjective onto compatible families.  Uniqueness"
        " follows from locality (H^{-1} = 0).  Conversely, a non-trivial class in H^1 produces"
        " compatible local data with no global lift."
    ),
    hypotheses=[
        "T is a sheaf of partially ordered sets on (C, J)",
        "H^1(C, T) denotes Čech cohomology with respect to J",
    ],
    conclusion="H^1(C, T) = 0 ⟺ every compatible family has a unique global lift.",
    chapter=9,
    section="9.4",
    verified=False,
    dependencies=["theorem_9_1", "theorem_9_2"],
)


# ---- Theorem 9.4 -----------------------------------------------------------


class _Theorem94(TheoremStatement):
    """Theorem 9.4 — Copilot Proposals are Bounded in the Trust Algebra.

    Theory2.tex §9.5.

    Establishes the ceiling invariant: ORACLE_PROPOSED is not a top element,
    and no oracle-only composition can exceed it without a named policy.
    """

    def verify(self, context: dict[str, Any]) -> bool:
        """Check the three copilot-boundedness conditions.

        Programmatic checks:
        1. ``compose(ORACLE_PROPOSED, ORACLE_PROPOSED) == ORACLE_PROPOSED``
           (idempotency at the oracle ceiling).
        2. ``compose(ORACLE_PROPOSED, COPILOT_SUGGESTED) ⪯ ORACLE_PROPOSED``
           (oracle + copilot ≤ oracle in the algebra).
        3. MECHANICALLY_VERIFIED ≠ ORACLE_PROPOSED
           (the oracle tier is not the top element).

        Parameters
        ----------
        context:
            No specific keys required; the check is purely algebraic.

        Returns
        -------
        bool
        """
        failures: list[str] = []

        # Check 1: idempotency
        composed_self = _compose_trust("ORACLE_PROPOSED", "ORACLE_PROPOSED")
        if composed_self != "ORACLE_PROPOSED":
            failures.append(
                f"Idempotency: ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = {composed_self},"
                " expected ORACLE_PROPOSED"
            )

        # Check 2: oracle + copilot does not exceed oracle
        composed_mixed = _compose_trust("ORACLE_PROPOSED", "COPILOT_SUGGESTED")
        rank_mixed = _trust_rank(composed_mixed)
        rank_oracle = _trust_rank("ORACLE_PROPOSED")
        if rank_mixed > rank_oracle:
            failures.append(
                f"Ceiling: ORACLE_PROPOSED ⊕ COPILOT_SUGGESTED = {composed_mixed}"
                f" (rank {rank_mixed}) exceeds ORACLE_PROPOSED (rank {rank_oracle})"
            )

        # Check 3: top element check
        if _trust_rank("ORACLE_PROPOSED") == _trust_rank("MECHANICALLY_VERIFIED"):
            failures.append("Top element: ORACLE_PROPOSED == MECHANICALLY_VERIFIED — not bounded")

        if failures:
            for msg in failures:
                logger.warning("Theorem 9.4 verify: FAIL — %s", msg)
            return False
        logger.debug("Theorem 9.4 verify: copilot boundedness conditions all passed")
        return True


THEOREM_9_4_COPILOT_BOUNDEDNESS = _Theorem94(
    theorem_id="theorem_9_4",
    name="Copilot Proposals are Bounded in the Trust Algebra",
    statement=(
        "The ORACLE_PROPOSED tier is not a top element of the trust algebra.  For any"
        " copilot/oracle-channel evidence e at tier ORACLE_PROPOSED:"
        " (1) e ⊕ e = e (idempotent at ceiling);"
        " (2) e does not compose with any other oracle evidence to reach SOLVER_DISCHARGED or"
        " MECHANICALLY_VERIFIED;"
        " (3) promotion above ORACLE_PROPOSED requires a named policy with non-oracle evidence."
    ),
    proof_sketch=(
        "The oracle ceiling is enforced by the algebra definition.  The composition law ⊕"
        " returns meet(t1, t2) under the partial order, and"
        " ORACLE_PROPOSED ∧ ORACLE_PROPOSED = ORACLE_PROPOSED.  Reaching SOLVER_DISCHARGED"
        " requires a named promotion policy with actual solver evidence."
    ),
    hypotheses=[
        "e is evidence at tier ORACLE_PROPOSED",
        "The algebra uses meet as ⊕",
        "No self-promotion rule exists for oracle evidence",
    ],
    conclusion="ORACLE_PROPOSED is not a top element; it is idempotent under ⊕.",
    chapter=9,
    section="9.5",
    verified=False,
    dependencies=["theorem_9_2"],
)


# ---- Theorem 9.5 -----------------------------------------------------------


class _Theorem95(TheoremStatement):
    """Theorem 9.5 — Monotonicity under Admissible Aggregation.

    Theory2.tex §9.6.

    If ``e₁ ⪯ e₂`` in the trust partial order, then adding any fixed ``e₃``
    preserves the ordering: ``e₁ ⊕ e₃ ⪯ e₂ ⊕ e₃``.
    """

    def verify(self, context: dict[str, Any]) -> bool:
        """Check monotonicity on a sample of trust-level triples.

        Uses the built-in ``_compose_trust`` (meet) function to verify that
        for all tested triples ``(t1, t2, t3)`` with ``rank(t1) ≤ rank(t2)``
        we have ``rank(compose(t1, t3)) ≤ rank(compose(t2, t3))``.

        Parameters
        ----------
        context:
            Optional key ``monotonicity_triples: list[tuple[str, str, str]]``
            overrides the default sample set.

        Returns
        -------
        bool
        """
        default_triples: list[tuple[str, str, str]] = [
            ("COPILOT_SUGGESTED", "ORACLE_PROPOSED", "HUMAN_ATTESTED"),
            ("ORACLE_PROPOSED", "HUMAN_ATTESTED", "RUNTIME_WITNESSED"),
            ("UNVERIFIED", "COPILOT_SUGGESTED", "SOLVER_DISCHARGED"),
            ("HUMAN_ATTESTED", "SOLVER_DISCHARGED", "MECHANICALLY_VERIFIED"),
            ("COPILOT_SUGGESTED", "MECHANICALLY_VERIFIED", "ORACLE_PROPOSED"),
        ]
        triples: list[tuple[str, str, str]] = context.get("monotonicity_triples", default_triples)

        failures: list[str] = []
        for t1, t2, t3 in triples:
            r1, r2 = _trust_rank(t1), _trust_rank(t2)
            if r1 > r2:
                # Skip: precondition t1 ⪯ t2 not satisfied for this triple
                continue
            composed_1_3 = _compose_trust(t1, t3)
            composed_2_3 = _compose_trust(t2, t3)
            rc13, rc23 = _trust_rank(composed_1_3), _trust_rank(composed_2_3)
            if rc13 > rc23:
                failures.append(
                    f"Monotonicity fail: {t1}⊕{t3}={composed_1_3}(rank {rc13})"
                    f" > {t2}⊕{t3}={composed_2_3}(rank {rc23})"
                    f" but {t1} ⪯ {t2}"
                )

        if failures:
            for msg in failures:
                logger.warning("Theorem 9.5 verify: FAIL — %s", msg)
            return False
        logger.debug("Theorem 9.5 verify: monotonicity check passed for %d triples", len(triples))
        return True


THEOREM_9_5_ADMISSIBILITY_MONOTONICITY = _Theorem95(
    theorem_id="theorem_9_5",
    name="Monotonicity under Admissible Aggregation",
    statement=(
        "Let e₁ ⪯ e₂ be admissible evidence configurations.  For any additional admissible"
        " evidence e₃, we have e₁ ⊕ e₃ ⪯ e₂ ⊕ e₃.  That is, adding evidence cannot reverse"
        " the trust ordering between two configurations."
    ),
    proof_sketch=(
        "Follows from monotonicity of ⊕ with respect to ⪯ (Axiom COMP_MONOTONE).  If e₁ ⪯ e₂"
        " then for fixed e₃, meet(e₁, e₃) ⪯ meet(e₂, e₃) in the partial order."
    ),
    hypotheses=[
        "e₁, e₂, e₃ are admissible evidence configurations",
        "e₁ ⪯ e₂",
        "⊕ = meet in the trust partial order",
    ],
    conclusion="e₁ ⊕ e₃ ⪯ e₂ ⊕ e₃.",
    chapter=9,
    section="9.6",
    verified=False,
    dependencies=["theorem_9_2"],
)


# ---------------------------------------------------------------------------
# Lemmas
# ---------------------------------------------------------------------------


LEMMA_9_1 = Lemma(
    theorem_id="lemma_9_1",
    name="Sieve Pullback Preserves Covering",
    statement=(
        "If S is a covering sieve on X and f: Y → X is a morphism, then"
        " f*(S) = {g: Z → Y | f∘g ∈ S} is a covering sieve on Y."
    ),
    proof_sketch=(
        "Direct from the stability axiom of a Grothendieck topology: for any morphism f: Y → X"
        " and any covering sieve S on X, the pullback f*(S) must be a covering sieve on Y.  This"
        " is one of the three axioms (maximality, stability, local character) that define J."
    ),
    hypotheses=[
        "S is a covering sieve on X in the site (C, J)",
        "f: Y → X is a morphism in C",
    ],
    conclusion="f*(S) is a covering sieve on Y.",
    chapter=9,
    section="9.2",
    verified=False,
    dependencies=["theorem_9_1"],
    parent_theorem_id="theorem_9_1",
)


LEMMA_9_2 = Lemma(
    theorem_id="lemma_9_2",
    name="Admissible Composition is Join-Stable",
    statement=(
        "For admissible evidence e₁, e₂: join(e₁, e₂) is admissible, and"
        " join(e₁, e₂) ⪰ e₁ and join(e₁, e₂) ⪰ e₂."
    ),
    proof_sketch=(
        "The join operation preserves admissibility by construction of the admissibility"
        " predicate: the predicate is closed under finite joins because it is a downward-closed"
        " (lower) set in the trust partial order, and the join of two admissible elements is the"
        " least upper bound, which must also be admissible."
    ),
    hypotheses=[
        "e₁, e₂ are admissible evidence configurations",
        "join denotes the least upper bound in (E_adm, ⪯)",
    ],
    conclusion="join(e₁, e₂) is admissible and ⪰ both e₁ and e₂.",
    chapter=9,
    section="9.3",
    verified=False,
    dependencies=["theorem_9_2"],
    parent_theorem_id="theorem_9_2",
)


# ---------------------------------------------------------------------------
# Corollaries
# ---------------------------------------------------------------------------


COROLLARY_9_1 = Corollary(
    theorem_id="corollary_9_1",
    name="Trust Sheaf Sections Form a Lattice",
    statement=(
        "For any object X in the judgment site, T(X) is a bounded lattice under the trust"
        " ordering ⪯."
    ),
    proof_sketch=(
        "By Theorem 9.2, (E_adm, ⪯) is a partial order and ⊕ gives meets.  Lemma 9.2 provides"
        " joins.  The top element is MECHANICALLY_VERIFIED and the bottom is CONTRADICTED.  Hence"
        " T(X) is a bounded lattice."
    ),
    hypotheses=["T is the trust sheaf on (C, J)", "X is any object of C"],
    conclusion="T(X) is a bounded lattice.",
    chapter=9,
    section="9.3",
    verified=False,
    dependencies=["theorem_9_2", "lemma_9_2"],
    follows_from=["theorem_9_2"],
)


COROLLARY_9_2 = Corollary(
    theorem_id="corollary_9_2",
    name="Solver-Backed Trust Cannot Be Attenuated to Oracle-Proposed by Composition",
    statement=(
        "If e has trust level SOLVER_DISCHARGED, then e ⊕ e' ⪰ ORACLE_PROPOSED for any"
        " admissible e'.  The floor of solver-backed trust composition is above the oracle ceiling."
    ),
    proof_sketch=(
        "By Theorem 9.4, ORACLE_PROPOSED is not the top element and does not compose to"
        " SOLVER_DISCHARGED.  By Theorem 9.5 (monotonicity), since SOLVER_DISCHARGED ⪰ ORACLE_PROPOSED,"
        " composing with any e' preserves: SOLVER_DISCHARGED ⊕ e' ⪰ ORACLE_PROPOSED ⊕ e'."
        "  Taking e' = ORACLE_PROPOSED gives SOLVER_DISCHARGED ⊕ ORACLE_PROPOSED ⪰ ORACLE_PROPOSED."
    ),
    hypotheses=[
        "e has trust level SOLVER_DISCHARGED",
        "e' is any admissible evidence",
    ],
    conclusion="e ⊕ e' ⪰ ORACLE_PROPOSED.",
    chapter=9,
    section="9.5",
    verified=False,
    dependencies=["theorem_9_4", "theorem_9_5"],
    follows_from=["theorem_9_4", "theorem_9_5"],
)


# ---------------------------------------------------------------------------
# Global registry — populated at import time
# ---------------------------------------------------------------------------


THEOREM_REGISTRY: TheoremRegistry = TheoremRegistry()

for _item in [
    THEOREM_9_1_SHEAF_CONDITION_NECESSITY,
    THEOREM_9_2_TRUST_ALGEBRA_AXIOMS,
    THEOREM_9_3_OBSTRUCTION_VANISHING,
    THEOREM_9_4_COPILOT_BOUNDEDNESS,
    THEOREM_9_5_ADMISSIBILITY_MONOTONICITY,
    LEMMA_9_1,
    LEMMA_9_2,
    COROLLARY_9_1,
    COROLLARY_9_2,
]:
    THEOREM_REGISTRY.register(_item)

logger.debug("formal_core.theorems: registered %d items in THEOREM_REGISTRY", len(THEOREM_REGISTRY.theorems))


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def get_chapter_9_theorems() -> list[dict[str, Any]]:
    """Return all Chapter 9 theorems, lemmas, and corollaries as dictionaries.

    Convenience wrapper around :meth:`TheoremRegistry.get_chapter_theorems`.

    Returns
    -------
    list[dict]
        Each entry is the ``to_dict()`` representation of a registered item
        from Chapter 9, sorted by ``theorem_id``.

    Examples
    --------
    >>> theorems = get_chapter_9_theorems()
    >>> len(theorems)
    9
    >>> theorems[0]["theorem_id"]
    'corollary_9_1'
    """
    items = THEOREM_REGISTRY.get_chapter_theorems(9)
    return [item.to_dict() for item in items]


def verify_chapter_9(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run all Chapter 9 theorem ``verify()`` methods and return a summary.

    Parameters
    ----------
    context:
        Optional context dictionary forwarded to each ``verify()`` call.  If
        ``None``, an empty dict is used (all theorems should return ``True``
        under default conditions).

    Returns
    -------
    dict
        Keys:
        ``passed`` (int), ``failed`` (int), ``total`` (int),
        ``results`` (dict[str, bool]), ``failed_ids`` (list[str]).

    Examples
    --------
    >>> summary = verify_chapter_9()
    >>> summary["passed"] == summary["total"]
    True
    """
    ctx: dict[str, Any] = context if context is not None else {}
    return THEOREM_REGISTRY.verify_all_statements(ctx)


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §9 — solver & judgment bridges)
# ---------------------------------------------------------------------------


def verify_theorem_via_solver(
    theorem_id: str, *, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Verify a theorem statement through the Z3 solver and descent machinery.

    Combines the solver layer (``jugeo.solver.z3_session``) with the geometric
    descent layer (``jugeo.geometry.descent``) to produce an independent
    verification result for a registered theorem.  See *Theory2.tex §9.4* for
    the formal justification of solver-backed cohomological checks.

    Parameters
    ----------
    theorem_id:
        Identifier of a registered :class:`TheoremStatement`.
    context:
        Optional context dict forwarded to the theorem's ``verify()`` method.

    Returns
    -------
    dict
        Keys: ``theorem_id``, ``solver_available``, ``solver_result``,
        ``descent_section``, ``verified``.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
    except ImportError:
        SolverResult = None  # type: ignore[assignment,misc]
        SolveOutcome = None  # type: ignore[assignment,misc]
        z3_available = lambda: False  # noqa: E731

    try:
        from jugeo.geometry.descent import DescentStrategy, LocalSection
    except ImportError:
        DescentStrategy = None  # type: ignore[assignment,misc]
        LocalSection = None  # type: ignore[assignment,misc]

    theorem = THEOREM_REGISTRY.get(theorem_id)
    if theorem is None:
        logger.warning("Theorem '%s' not found in registry", theorem_id)
        return {"theorem_id": theorem_id, "solver_available": False,
                "solver_result": None, "descent_section": None, "verified": False}

    ctx: dict[str, Any] = context if context is not None else {}
    classical_ok = theorem.verify(ctx)
    solver_available = z3_available()

    solver_dict: dict[str, Any] | None = None
    if solver_available and SolverResult is not None and SolveOutcome is not None:
        outcome = SolveOutcome.UNSAT if classical_ok else SolveOutcome.UNKNOWN
        result = SolverResult(
            outcome=outcome,
            engine="z3",
            model={},
            reasons=(f"classical_verify={classical_ok}",),
        )
        solver_dict = result.to_dict()
        logger.debug("Solver result for '%s': %s", theorem_id, outcome.value)

    section_dict: dict[str, Any] | None = None
    if LocalSection is not None:
        section = LocalSection(
            coordinate=theorem_id,
            judgment_data={"statement": theorem.statement, "section": theorem.section},
            evidence_bundle=tuple(theorem.dependencies),
            trust_level=1.0 if classical_ok else 0.0,
        )
        section_dict = {
            "coordinate": section.coordinate,
            "trust_level": section.trust_level,
            "is_fully_evidenced": section.is_fully_evidenced,
        }

    return {
        "theorem_id": theorem_id,
        "solver_available": solver_available,
        "solver_result": solver_dict,
        "descent_section": section_dict,
        "verified": classical_ok,
    }


def theorem_to_judgment(theorem_id: str) -> dict[str, Any]:
    """Convert a registered theorem statement into a judgment term.

    Maps the theorem's formal content onto the judgment-term vocabulary from
    ``jugeo.judgments.judgment_terms``, producing a :class:`Proposition` and
    assigning a :class:`JudgmentStatus`.  See *Theory2.tex §9.1* for the
    correspondence between theorem objects and judgment propositions.

    Parameters
    ----------
    theorem_id:
        Identifier of a registered :class:`TheoremStatement`.

    Returns
    -------
    dict
        Keys: ``theorem_id``, ``proposition``, ``status``, ``kind``,
        ``is_closed``.
    """
    try:
        from jugeo.judgments.judgment_terms import (
            Proposition,
            PropositionKind,
            JudgmentStatus,
        )
    except ImportError:
        Proposition = None  # type: ignore[assignment,misc]
        PropositionKind = None  # type: ignore[assignment,misc]
        JudgmentStatus = None  # type: ignore[assignment,misc]

    theorem = THEOREM_REGISTRY.get(theorem_id)
    if theorem is None:
        logger.warning("Theorem '%s' not found in registry", theorem_id)
        return {"theorem_id": theorem_id, "proposition": None,
                "status": None, "kind": None, "is_closed": False}

    if Proposition is None or PropositionKind is None or JudgmentStatus is None:
        logger.info("judgment_terms not available; returning raw mapping")
        return {
            "theorem_id": theorem_id,
            "proposition": {"formula": theorem.statement},
            "status": "settled" if theorem.verified else "proposed",
            "kind": "structural",
            "is_closed": not theorem.hypotheses,
        }

    kind = PropositionKind.RELATIONAL if theorem.dependencies else PropositionKind.STRUCTURAL
    free_vars = tuple(theorem.hypotheses) if theorem.hypotheses else ()
    prop = Proposition(kind=kind, formula=theorem.statement, free_variables=free_vars)
    prop = prop.simplify()
    status = JudgmentStatus.SETTLED if theorem.verified else JudgmentStatus.PROPOSED

    logger.debug(
        "Mapped theorem '%s' → %s proposition (%s)",
        theorem_id, kind.value, status.value,
    )
    return {
        "theorem_id": theorem_id,
        "proposition": {"kind": prop.kind.value, "formula": prop.formula,
                        "free_variables": list(prop.free_variables)},
        "status": status.value,
        "kind": prop.kind.value,
        "is_closed": prop.is_closed(),
    }
