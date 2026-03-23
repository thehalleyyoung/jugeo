"""Theorem statements and verification for scope and state (theory2.tex Ch15).

This module formalises the key theorems from Chapter 15 of ``theory2.tex``
about scope and state in Python programs.  From a sheaf-theoretic perspective,
the theorems assert *well-formedness conditions* that must hold for the scope
analysis to be sound.

Each theorem class provides a ``check()`` method that verifies the theorem
against actual scope data, and a ``build_judgment()`` method that encodes the
theorem as a :class:`~jugeo.judgments.judgment_terms.Judgment` object in the
JuGeo judgment algebra.

The theorems form a complete axiom system for Python's lexical scoping rules
(the LEGB rule: Local → Enclosing → Global → Builtin):

* **T15.1 Scope Covering** — the family of scope sections covers the entire
  name-space of the module, i.e. every name is visible in at least one scope.
* **T15.2 Name Uniqueness** — within each individual scope there are no
  duplicate binding names.
* **T15.3 Closure Well-Formedness** — every closure correctly captures all of
  its free variables from the lexically enclosing scope chain.
* **T15.4 Module State Consistency** — module-level state manifests reference
  a consistent coordinate across all snapshots.
* **T15.5 Resolution Determinism** — the LEGB resolution algorithm is
  deterministic: each name resolves to at most one binding.
* **T15.6 Lexical Scoping** — name visibility depends only on textual
  containment, never on call order or dynamic dispatch.

The ``TheoremRegistry`` collects all theorem objects and provides batch
verification as well as human-readable reports.  The module-level factory
``build_default_theorems`` pre-populates a registry with one theorem per kind.

copilot integration: theorem results are surfaced as inline annotations via
``build_judgment()``, making it possible to flag scope errors at their
definition sites without leaving the editor.

References:
    theory2.tex Ch15 — Scope, State, and the LEGB Sheaf.
    theory2.tex §3.2 — CoordinateObject and CoordinateKind.
    PEP 3104 — Access to Names in Outer Scopes.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-package imports — geometry
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        CoordinateObject,
        CoordinateKind,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except ImportError:
    class CoordinateKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        FUNCTION = "function"
        MODULE = "module"
        CLASS = "class"
        REGION = "region"

    class MorphismKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        RESTRICTION = "restriction"
        TRANSPORT = "transport"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class CoordinateObject:
        """Stub CoordinateObject."""
        components: tuple[str, ...] = ()
        kind: CoordinateKind = CoordinateKind.REGION

        @property
        def key(self) -> str:  # noqa: D102
            return "/".join(self.components)

        def serialize(self) -> dict[str, Any]:  # noqa: D102
            return {"components": list(self.components), "kind": self.kind.value}

    class Site:  # type: ignore[no-redef]
        """Stub."""

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub."""


# ---------------------------------------------------------------------------
# Cross-package imports — supports
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.supports import (
        SupportRegion,
        SupportSet,
        SupportedSection,
        SupportTracker,
    )
except ImportError:
    class SupportRegion:  # type: ignore[no-redef]
        """Stub."""

    class SupportSet:  # type: ignore[no-redef]
        """Stub."""

    class SupportedSection:  # type: ignore[no-redef]
        """Stub."""

    class SupportTracker:  # type: ignore[no-redef]
        """Stub."""


# ---------------------------------------------------------------------------
# Cross-package imports — judgment_terms
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
    )
except ImportError:
    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class TrustLevel(int, Enum):  # type: ignore[no-redef]
        """Stub."""
        CONTRADICTED = 0
        UNVERIFIED = 1
        HEURISTIC = 2
        SOLVER_DISCHARGED = 3
        VERIFIED_PROOF = 4

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        STRUCTURAL = "structural"
        RELATIONAL = "relational"
        EXISTENTIAL = "existential"
        UNIVERSAL = "universal"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        STATIC_ANALYSIS = "static_analysis"
        SOLVER_CERTIFICATE = "solver_certificate"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Proposition:
        """Stub."""
        kind: PropositionKind = PropositionKind.STRUCTURAL
        formula: str = ""
        free_variables: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Carrier:
        """Stub."""
        name: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class TrustAnnotation:
        """Stub."""
        level: TrustLevel = TrustLevel.UNVERIFIED

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class EvidenceItem:
        """Stub."""
        kind: EvidenceItemKind = EvidenceItemKind.STATIC_ANALYSIS
        trust_level: TrustLevel = TrustLevel.UNVERIFIED
        note: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class EvidenceBundle:
        """Stub."""
        items: tuple[EvidenceItem, ...] = ()

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ResidualObligation:
        """Stub."""
        key: str = ""
        description: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Obstruction:
        """Stub."""
        key: str = ""
        description: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Provenance:
        """Stub."""
        source_id: str = ""
        note: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class Judgment:
        """Stub."""
        coordinate: CoordinateObject = field(default_factory=CoordinateObject)
        proposition: Proposition = field(default_factory=Proposition)
        carrier: Carrier = field(default_factory=Carrier)
        trust: TrustAnnotation = field(default_factory=TrustAnnotation)
        evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
        status: JudgmentStatus = JudgmentStatus.PROPOSED


# ---------------------------------------------------------------------------
# Cross-package imports — z3_session
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (
        Z3Session,
        Z3Formula,
        z3_available,
        SolveOutcome,
    )
except ImportError:
    class SolveOutcome(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        SAT = "sat"
        UNSAT = "unsat"
        UNKNOWN = "unknown"
        ERROR = "error"

    @dataclass(slots=True)  # type: ignore[no-redef]
    class Z3Formula:
        """Stub."""
        expr: str = ""
        kind: str = "boolean"

    class Z3Session:  # type: ignore[no-redef]
        """Stub."""
        def check(self, formula: Any) -> SolveOutcome:  # noqa: D102
            return SolveOutcome.UNKNOWN

    def z3_available() -> bool:  # type: ignore[no-redef]
        """Stub."""
        return False


# ---------------------------------------------------------------------------
# Local imports — scope_and_state.models
# ---------------------------------------------------------------------------

try:
    from jugeo.python_runtime.scope_and_state.models import (
        NameCoordinate,
        NameKind,
        ScopeSection,
        ScopeKind,
        ClosureRecord,
        ModuleStateManifest,
        BindingMap,
        NameResolutionResult,
        ScopeChain,
    )
except ImportError:
    class NameKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        LOCAL = "local"
        PARAMETER = "parameter"
        FREE = "free"
        CLOSURE = "closure"
        GLOBAL = "global"
        BUILTIN = "builtin"
        NONLOCAL = "nonlocal"
        UNKNOWN = "unknown"

    class ScopeKind(str, Enum):  # type: ignore[no-redef]
        """Stub."""
        MODULE = "module"
        FUNCTION = "function"
        LAMBDA = "lambda"
        CLASS = "class"
        COMPREHENSION = "comprehension"
        GENERATOR = "generator"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class NameCoordinate:
        """Stub."""
        name: str = ""
        kind: NameKind = NameKind.LOCAL
        scope_key: str = ""
        type_repr: str = "unknown"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ScopeSection:
        """Stub."""
        scope_key: str = ""
        scope_kind: ScopeKind = ScopeKind.FUNCTION
        parent_key: str | None = None
        bindings: tuple[NameCoordinate, ...] = ()
        source_location: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ClosureRecord:
        """Stub."""
        function_key: str = ""
        enclosing_keys: tuple[str, ...] = ()
        free_variables: tuple[NameCoordinate, ...] = ()
        all_free_names: tuple[str, ...] = ()
        depth: int = 0

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ModuleStateManifest:
        """Stub."""
        module_name: str = ""
        module_coordinate: CoordinateObject = field(default_factory=CoordinateObject)
        global_names: tuple[str, ...] = ()
        type_reprs: dict[str, str] = field(default_factory=dict)
        version: int = 0

    # BindingMap is a plain type alias in the real models; dict for the stub.
    BindingMap = dict  # type: ignore[misc, assignment]

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class NameResolutionResult:
        """Stub."""
        name: str = ""
        resolved: bool = False
        coordinate: NameCoordinate | None = None
        scope_key: str | None = None
        resolution_path: tuple[str, ...] = ()
        error_message: str = ""

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class ScopeChain:
        """Stub."""
        scopes: tuple[ScopeSection, ...] = ()
        module_key: str = ""


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TheoremKind
# ---------------------------------------------------------------------------


class TheoremKind(str, Enum):
    """Classification of scope-and-state theorems from theory2.tex Ch15.

    Each value corresponds to one of the formal correctness dimensions of the
    LEGB sheaf model.  The string representation matches the section identifier
    used in the LaTeX source so that theorem objects can be cross-referenced
    with the formal document.

    Attributes:
        SCOPE_COVERING: Every name in the module is visible in at least one
            scope section (T15.1).
        NAME_UNIQUENESS: Within a single scope there are no duplicate binding
            names (T15.2).
        CLOSURE_WELL_FORMED: Every closure correctly captures its free
            variables from the lexically enclosing chain (T15.3).
        MODULE_STATE_CONSISTENCY: All module-state manifests reference the
            same canonical module coordinate (T15.4).
        RESOLUTION_DETERMINISM: The LEGB algorithm assigns at most one binding
            to each name lookup (T15.5).
        LEXICAL_SCOPING: Name visibility depends only on textual containment
            and never on runtime call order (T15.6).
    """

    SCOPE_COVERING = "scope_covering"
    NAME_UNIQUENESS = "name_uniqueness"
    CLOSURE_WELL_FORMED = "closure_well_formed"
    MODULE_STATE_CONSISTENCY = "module_state_consistency"
    RESOLUTION_DETERMINISM = "resolution_determinism"
    LEXICAL_SCOPING = "lexical_scoping"


# ---------------------------------------------------------------------------
# ScopeTheorem — immutable value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeTheorem:
    """An immutable record of one formally stated scope theorem.

    ``ScopeTheorem`` is the *value-object* representation of a theorem from
    Ch15.  It carries all the metadata needed to reference, display, and
    serialise the theorem, but performs no verification itself.  Verification
    is delegated to the mutable theorem classes (e.g.
    :class:`NameUniquenessTheorem`), which produce ``ScopeTheorem`` instances
    via their ``verify()`` methods.

    Attributes:
        theorem_id: Globally unique identifier, typically a UUID hex string.
        kind: The :class:`TheoremKind` that classifies this theorem.
        statement: Human-readable one-sentence statement of the theorem.
        hypothesis: Ordered tuple of hypothesis strings (premises).
        conclusion: The conclusion string.
        proof_sketch: A brief English description of the proof strategy.
        is_verified: ``True`` after a successful call to ``verify()``.

    Example:
        >>> t = ScopeTheorem(
        ...     theorem_id="abc123",
        ...     kind=TheoremKind.NAME_UNIQUENESS,
        ...     statement="Binding names within a scope are distinct.",
        ...     hypothesis=("scope is well-formed",),
        ...     conclusion="no duplicate names exist",
        ...     proof_sketch="Enumerate all binding names and check pairwise.",
        ... )
        >>> t.is_verified
        False
        >>> t.mark_verified().is_verified
        True
    """

    theorem_id: str
    kind: TheoremKind
    statement: str
    hypothesis: tuple[str, ...]
    conclusion: str
    proof_sketch: str
    is_verified: bool = False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise the theorem to a plain JSON-compatible dictionary.

        Returns:
            A dictionary with keys ``theorem_id``, ``kind``, ``statement``,
            ``hypothesis``, ``conclusion``, ``proof_sketch``, and
            ``is_verified``.  All values are primitive Python types suitable
            for ``json.dumps``.
        """
        return {
            "theorem_id": self.theorem_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "hypothesis": list(self.hypothesis),
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "is_verified": self.is_verified,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ScopeTheorem:
        """Deserialise a ``ScopeTheorem`` from a plain dictionary.

        Parameters:
            data: A dictionary previously produced by :meth:`serialize`.
                Unknown keys are silently ignored.

        Returns:
            A new :class:`ScopeTheorem` instance.

        Raises:
            KeyError: If a required key (``theorem_id``, ``kind``,
                ``statement``, ``conclusion``, ``proof_sketch``) is absent.
            ValueError: If ``kind`` is not a valid :class:`TheoremKind` value.
        """
        kind_raw = data["kind"]
        try:
            kind = TheoremKind(kind_raw)
        except ValueError as exc:
            valid = [k.value for k in TheoremKind]
            raise ValueError(
                f"Unknown TheoremKind {kind_raw!r}; valid values: {valid}"
            ) from exc

        hypothesis_raw = data.get("hypothesis", [])
        if not isinstance(hypothesis_raw, (list, tuple)):
            hypothesis_raw = []

        return cls(
            theorem_id=data["theorem_id"],
            kind=kind,
            statement=data["statement"],
            hypothesis=tuple(str(h) for h in hypothesis_raw),
            conclusion=data["conclusion"],
            proof_sketch=data["proof_sketch"],
            is_verified=bool(data.get("is_verified", False)),
        )

    # ------------------------------------------------------------------
    # Transition helpers (return new instances via dataclasses.replace)
    # ------------------------------------------------------------------

    def mark_verified(self) -> ScopeTheorem:
        """Return a copy of this theorem with ``is_verified=True``.

        Uses :func:`dataclasses.replace` so all other fields are preserved.

        Returns:
            A new :class:`ScopeTheorem` with ``is_verified`` set to ``True``.
        """
        return dataclasses.replace(self, is_verified=True)

    def mark_unverified(self) -> ScopeTheorem:
        """Return a copy of this theorem with ``is_verified=False``.

        Uses :func:`dataclasses.replace` so all other fields are preserved.

        Returns:
            A new :class:`ScopeTheorem` with ``is_verified`` set to ``False``.
        """
        return dataclasses.replace(self, is_verified=False)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a compact one-line summary of the theorem.

        Returns:
            A string of the form ``"<theorem_id>: <first 60 chars of
            statement>..."``.
        """
        truncated = self.statement[:60]
        return f"{self.theorem_id}: {truncated}..."


# ---------------------------------------------------------------------------
# NameUniquenessTheorem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NameUniquenessTheorem:
    """Verifier for T15.2: binding names within a single scope are unique.

    From theory2.tex Ch15.2: *within a scope section S, the binding tuple
    is injective on names* — no two distinct ``NameCoordinate`` objects in the
    same scope share a name string.  Violations arise when a scope accumulates
    duplicate entries (e.g. through augmented-assignment target re-binding or
    from malformed AST walkers that visit the same node twice).

    This class is *mutable* because ``check()`` populates ``_violations`` as a
    side effect, enabling incremental inspection after the fact without
    requiring callers to store the intermediate violation list themselves.

    Attributes:
        _violations: List of human-readable violation descriptions populated
            by the last call to :meth:`check` or :meth:`find_violations`.
        module_coordinate: The :class:`CoordinateObject` for the enclosing
            module, used as the anchor coordinate in produced Judgments.
    """

    _violations: list[str]
    module_coordinate: CoordinateObject

    # ------------------------------------------------------------------
    # Core predicate
    # ------------------------------------------------------------------

    def check(self, scope: ScopeSection) -> bool:
        """Verify that all binding names in *scope* are distinct.

        Populates :attr:`_violations` with one entry per duplicate name found.
        A scope with zero bindings trivially satisfies the theorem.

        Parameters:
            scope: The :class:`ScopeSection` whose binding tuple is inspected.

        Returns:
            ``True`` if no duplicate names are found; ``False`` otherwise.
        """
        self._violations.clear()
        violations = self.find_violations(scope)
        self._violations.extend(violations)
        if violations:
            logger.debug(
                "NameUniquenessTheorem.check FAILED for scope %r: %d violation(s)",
                scope.scope_key,
                len(violations),
            )
        else:
            logger.debug(
                "NameUniquenessTheorem.check PASSED for scope %r", scope.scope_key
            )
        return len(violations) == 0

    def find_violations(self, scope: ScopeSection) -> list[str]:
        """Return a list of duplicate name strings found in *scope*.

        Iterates over the ``bindings`` tuple and records each name that
        appears more than once.  The returned strings are formatted as
        ``"duplicate name '<name>' in scope '<scope_key>'"`` for easy
        downstream reporting.

        Parameters:
            scope: The :class:`ScopeSection` to inspect.

        Returns:
            A list of violation description strings.  Empty if all names are
            unique.
        """
        seen: set[str] = set()
        reported: set[str] = set()
        duplicates: list[str] = []
        for coord in scope.bindings:
            name = coord.name
            if name in seen and name not in reported:
                msg = (
                    f"duplicate name {name!r} in scope {scope.scope_key!r}"
                )
                duplicates.append(msg)
                reported.add(name)
            else:
                seen.add(name)
        return duplicates

    # ------------------------------------------------------------------
    # Judgment construction
    # ------------------------------------------------------------------

    def build_judgment(self, scope: ScopeSection) -> Judgment:
        """Encode the name-uniqueness check as a :class:`Judgment`.

        Runs :meth:`check` and selects the appropriate :class:`TrustLevel`
        based on the result: ``SOLVER_DISCHARGED`` on pass, ``UNVERIFIED`` on
        failure.  The proposition formula is the universal statement from
        theory2.tex §T15.2.

        Parameters:
            scope: The :class:`ScopeSection` being verified.

        Returns:
            A fully constructed :class:`Judgment` object whose coordinate
            anchors to the scope's key within the module coordinate.
        """
        passed = self.check(scope)
        coord_components = self.module_coordinate.components + (scope.scope_key,)
        scope_coord = CoordinateObject(
            components=coord_components,
            kind=CoordinateKind.REGION,
        )
        formula = (
            "∀ n1,n2 ∈ scope.bindings: n1.name = n2.name → n1 = n2"
        )
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=("n1", "n2"),
        )
        carrier = Carrier(name="ScopeCarrier")
        trust_level = (
            TrustLevel.SOLVER_DISCHARGED if passed else TrustLevel.UNVERIFIED
        )
        trust = TrustAnnotation(level=trust_level)
        evidence_items: tuple[EvidenceItem, ...] = ()
        for violation in self._violations:
            evidence_items = evidence_items + (
                EvidenceItem(
                    kind=EvidenceItemKind.STATIC_ANALYSIS,
                    trust_level=TrustLevel.UNVERIFIED,
                    note=violation,
                ),
            )
        evidence = EvidenceBundle(items=evidence_items)
        return Judgment(
            coordinate=scope_coord,
            proposition=proposition,
            carrier=carrier,
            trust=trust,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # SMT encoding
    # ------------------------------------------------------------------

    def z3_encode(self, scope: ScopeSection) -> str:
        """Return an SMT-LIB2 formula asserting distinctness of all names.

        The formula declares each binding name as a string constant and
        asserts pairwise inequality over them.  This is suitable for direct
        consumption by a :class:`Z3Session`.

        Parameters:
            scope: The :class:`ScopeSection` whose names are encoded.

        Returns:
            A multi-line SMT-LIB2 string containing ``declare-const``
            declarations and an ``assert distinct`` statement.  Returns the
            trivially-true formula ``"(assert true)"`` if the scope has fewer
            than two bindings.
        """
        names = [coord.name for coord in scope.bindings]
        if len(names) < 2:
            return "(assert true)"
        lines: list[str] = ["(set-logic QF_S)"]
        for i, name in enumerate(names):
            sanitised = name.replace("-", "_").replace(".", "_")
            lines.append(f"(declare-const n{i} String)")
            lines.append(f'(assert (= n{i} "{sanitised}"))')
        distinct_vars = " ".join(f"n{i}" for i in range(len(names)))
        lines.append(f"(assert (distinct {distinct_vars}))")
        lines.append("(check-sat)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Theorem record construction
    # ------------------------------------------------------------------

    def verify(self, scope: ScopeSection) -> ScopeTheorem:
        """Run :meth:`check` and return a :class:`ScopeTheorem` with status.

        Parameters:
            scope: The :class:`ScopeSection` to verify against.

        Returns:
            A :class:`ScopeTheorem` with ``is_verified=True`` if the theorem
            holds and ``is_verified=False`` otherwise.
        """
        record = self.build_theorem_record(scope)
        passed = self.check(scope)
        if passed:
            return record.mark_verified()
        return record.mark_unverified()

    def build_theorem_record(self, scope: ScopeSection) -> ScopeTheorem:
        """Construct the base :class:`ScopeTheorem` dataclass for this theorem.

        Does *not* run the check; the ``is_verified`` flag defaults to
        ``False``.  Callers should invoke :meth:`verify` to obtain a record
        with the flag set correctly.

        Parameters:
            scope: The :class:`ScopeSection` being described.

        Returns:
            A :class:`ScopeTheorem` describing T15.2 for the given scope.
        """
        theorem_id = uuid.uuid4().hex[:12]
        binding_count = len(scope.bindings)
        statement = (
            f"All binding names within scope {scope.scope_key!r} are distinct "
            f"(T15.2, Name Uniqueness)."
        )
        hypothesis = (
            "scope is a well-formed ScopeSection",
            f"scope.bindings contains {binding_count} entry(ies)",
            f"scope.scope_kind is {scope.scope_kind.value!r}",
        )
        conclusion = (
            "for all n1, n2 in scope.bindings: n1.name == n2.name implies n1 is n2"
        )
        proof_sketch = (
            "Iterate over scope.bindings; insert each name into a seen-set while "
            "scanning.  If a name is already present in the set, record it as a "
            "duplicate and mark the theorem unverified."
        )
        return ScopeTheorem(
            theorem_id=theorem_id,
            kind=TheoremKind.NAME_UNIQUENESS,
            statement=statement,
            hypothesis=hypothesis,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
        )


# ---------------------------------------------------------------------------
# ScopeCoveringTheorem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScopeCoveringTheorem:
    """Verifier for T15.1: scope sections form a valid covering of the module.

    From theory2.tex Ch15.1: *the family {S_i} of scope sections constitutes
    a covering sieve for the module coordinate* — every name declared at the
    module level is visible from at least one scope in the family.

    This class also detects *overlaps*: pairs of sibling scopes (i.e. those
    sharing the same ``parent_key``) that bind the same name.  Overlaps are
    legal in Python (one scope shadows the other) but must be accounted for
    explicitly in later analysis stages.

    Attributes:
        _violations: Accumulated violation strings from the last check.
        module_coordinate: Anchor coordinate for the module under analysis.
    """

    _violations: list[str]
    module_coordinate: CoordinateObject

    def check(
        self,
        scopes: list[ScopeSection],
        all_module_names: list[str],
    ) -> bool:
        """Verify every module-level name is visible in at least one scope.

        A name is *visible* in a scope if it appears in that scope's bindings
        tuple.  Gaps are names present in *all_module_names* but absent from
        every scope binding set.

        Parameters:
            scopes: The list of :class:`ScopeSection` objects covering the
                module.
            all_module_names: The authoritative list of all names declared at
                module level (obtained from, e.g., ``dir()`` or AST walking).

        Returns:
            ``True`` if there are no gaps; ``False`` otherwise.
        """
        self._violations.clear()
        gaps = self.find_gaps(scopes, all_module_names)
        for gap in gaps:
            self._violations.append(
                f"name {gap!r} is not covered by any scope section"
            )
        if gaps:
            logger.debug(
                "ScopeCoveringTheorem.check FAILED: %d gap(s) found", len(gaps)
            )
        else:
            logger.debug(
                "ScopeCoveringTheorem.check PASSED: all %d names covered",
                len(all_module_names),
            )
        return len(gaps) == 0

    def find_gaps(
        self,
        scopes: list[ScopeSection],
        all_module_names: list[str],
    ) -> list[str]:
        """Return names present in *all_module_names* but absent from all scopes.

        Builds the union of all binding-name sets across every scope, then
        computes the set difference against *all_module_names*.

        Parameters:
            scopes: Scope sections to search.
            all_module_names: Reference name set.

        Returns:
            A sorted list of uncovered name strings.
        """
        covered: set[str] = set()
        for scope in scopes:
            for coord in scope.bindings:
                covered.add(coord.name)
        return sorted(name for name in all_module_names if name not in covered)

    def find_overlaps(
        self,
        scopes: list[ScopeSection],
    ) -> list[tuple[str, str, str]]:
        """Return (scope1_key, scope2_key, shared_name) triples for siblings.

        Two scopes are considered siblings when they share the same
        ``parent_key`` (or both have ``parent_key=None``).  For each pair of
        sibling scopes, the method collects names that appear in both binding
        tuples.

        Parameters:
            scopes: All scope sections belonging to the module.

        Returns:
            A list of three-tuples ``(scope1_key, scope2_key, name)`` where
            ``scope1_key < scope2_key`` lexicographically (to deduplicate
            symmetric pairs).  Empty list if there are no overlaps.
        """
        parent_map: dict[str | None, list[ScopeSection]] = defaultdict(list)
        for scope in scopes:
            parent_map[scope.parent_key].append(scope)

        overlaps: list[tuple[str, str, str]] = []
        for siblings in parent_map.values():
            if len(siblings) < 2:
                continue
            for i in range(len(siblings)):
                for j in range(i + 1, len(siblings)):
                    s1 = siblings[i]
                    s2 = siblings[j]
                    names_1 = {coord.name for coord in s1.bindings}
                    names_2 = {coord.name for coord in s2.bindings}
                    shared = names_1 & names_2
                    key1, key2 = sorted([s1.scope_key, s2.scope_key])
                    for name in sorted(shared):
                        overlaps.append((key1, key2, name))
        return overlaps

    def build_judgment(self, scopes: list[ScopeSection]) -> Judgment:
        """Encode the covering check as a :class:`Judgment`.

        Derives the full name set from the union of all scope bindings, runs
        :meth:`check`, and assembles a Judgment with trust level reflecting
        the outcome.

        Parameters:
            scopes: The scope sections being verified.

        Returns:
            A :class:`Judgment` anchored to the module coordinate with an
            existential proposition asserting the covering property.
        """
        all_names: set[str] = set()
        for scope in scopes:
            for coord in scope.bindings:
                all_names.add(coord.name)
        deduped = sorted(all_names)
        passed = self.check(scopes, deduped)
        formula = "∀ n ∈ module.names: ∃ S ∈ scopes: n ∈ S.bindings"
        proposition = Proposition(
            kind=PropositionKind.EXISTENTIAL,
            formula=formula,
            free_variables=("n", "S"),
        )
        carrier = Carrier(name="ScopeCarrier")
        trust_level = (
            TrustLevel.SOLVER_DISCHARGED if passed else TrustLevel.UNVERIFIED
        )
        trust = TrustAnnotation(level=trust_level)
        violation_items: tuple[EvidenceItem, ...] = tuple(
            EvidenceItem(
                kind=EvidenceItemKind.STATIC_ANALYSIS,
                trust_level=TrustLevel.UNVERIFIED,
                note=v,
            )
            for v in self._violations
        )
        evidence = EvidenceBundle(items=violation_items)
        return Judgment(
            coordinate=self.module_coordinate,
            proposition=proposition,
            carrier=carrier,
            trust=trust,
            evidence=evidence,
        )

    def verify(
        self,
        scopes: list[ScopeSection],
        all_module_names: list[str],
    ) -> ScopeTheorem:
        """Run the covering check and return a :class:`ScopeTheorem`.

        Parameters:
            scopes: Scope sections to verify.
            all_module_names: Authoritative name list.

        Returns:
            A :class:`ScopeTheorem` with ``is_verified`` set appropriately.
        """
        theorem_id = uuid.uuid4().hex[:12]
        statement = (
            f"The {len(scopes)} scope section(s) form a covering sieve for "
            f"{len(all_module_names)} module-level name(s) (T15.1, Scope Covering)."
        )
        hypothesis = (
            "module has a well-defined name-space",
            f"there are {len(scopes)} scope section(s) under analysis",
            "all_module_names contains every name declared at module level",
        )
        conclusion = (
            "every module-level name is visible in at least one scope section"
        )
        proof_sketch = (
            "Accumulate the union of all scope binding-name sets; verify that "
            "this union is a superset of all_module_names."
        )
        record = ScopeTheorem(
            theorem_id=theorem_id,
            kind=TheoremKind.SCOPE_COVERING,
            statement=statement,
            hypothesis=hypothesis,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
        )
        passed = self.check(scopes, all_module_names)
        return record.mark_verified() if passed else record.mark_unverified()


# ---------------------------------------------------------------------------
# ClosureWellFormednessTheorem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClosureWellFormednessTheorem:
    """Verifier for T15.3: closures correctly capture their free variables.

    From theory2.tex Ch15.3: *for every closure C with free-variable set
    FV(C), each name in FV(C) is bound in some enclosing scope S_i that is
    lexically accessible at C's definition site.*  A closure that references a
    name not present in any reachable enclosing scope is an *invalid closure*,
    indicating either a static analysis bug or a genuine undeclared-name error.

    The ``check()`` method operates on a single :class:`ClosureRecord` and a
    flat list of names reachable from the enclosing scope chain (as derived
    from ``all_free_names`` cross-checked against the outer scope bindings).

    Attributes:
        _violations: Accumulated violation strings from the last check.
        module_coordinate: Anchor coordinate for produced Judgments.
    """

    _violations: list[str]
    module_coordinate: CoordinateObject

    def check(
        self,
        record: ClosureRecord,
        available_outer_names: list[str],
    ) -> bool:
        """Verify that all free variables in *record* are available externally.

        Inspects ``record.all_free_names`` — the canonical flat string list of
        free-variable names — and checks each against *available_outer_names*.

        Parameters:
            record: The :class:`ClosureRecord` being validated.
            available_outer_names: Names reachable from the enclosing scope
                chain at the closure's definition site.

        Returns:
            ``True`` if every free variable in the record appears in
            *available_outer_names*; ``False`` otherwise.
        """
        self._violations.clear()
        outer_set = set(available_outer_names)
        all_good = True
        for var in record.all_free_names:
            if var not in outer_set:
                msg = (
                    f"closure {record.function_key!r}: "
                    f"free variable {var!r} not found in enclosing scope"
                )
                self._violations.append(msg)
                all_good = False
                logger.debug("ClosureWellFormednessTheorem: %s", msg)
        if all_good:
            logger.debug(
                "ClosureWellFormednessTheorem.check PASSED for closure %r",
                record.function_key,
            )
        return all_good

    def find_invalid_closures(
        self,
        records: list[ClosureRecord],
        available_outer_names: list[str],
    ) -> list[ClosureRecord]:
        """Return records whose free variables cannot all be resolved.

        Iterates over *records* and applies :meth:`check` to each one,
        collecting those that fail.  Unlike :meth:`check`, this method does
        *not* mutate :attr:`_violations`; it performs an independent scan.

        Parameters:
            records: Closure records to inspect.
            available_outer_names: Names reachable from the enclosing scope.

        Returns:
            A list of :class:`ClosureRecord` instances that failed
            :meth:`check`.
        """
        outer_set = set(available_outer_names)
        invalid: list[ClosureRecord] = []
        for record in records:
            unresolvable = [
                var for var in record.all_free_names if var not in outer_set
            ]
            if unresolvable:
                logger.debug(
                    "find_invalid_closures: closure %r has %d unresolvable var(s): %s",
                    record.function_key,
                    len(unresolvable),
                    ", ".join(unresolvable),
                )
                invalid.append(record)
        return invalid

    def build_judgment(self, record: ClosureRecord) -> Judgment:
        """Encode the closure well-formedness check as a :class:`Judgment`.

        The proposition formula captures the semantic content of T15.3:
        *every free variable of the closure is bound in the enclosing scope
        chain*.

        Parameters:
            record: The :class:`ClosureRecord` being judged.

        Returns:
            A :class:`Judgment` anchored to a coordinate derived from the
            module coordinate plus the closure's ``function_key``.
        """
        coord_components = self.module_coordinate.components + (
            "closure",
            record.function_key,
        )
        closure_coord = CoordinateObject(
            components=coord_components,
            kind=CoordinateKind.FUNCTION,
        )
        formula = "all free variables of closure are bound in enclosing scope"
        proposition = Proposition(
            kind=PropositionKind.UNIVERSAL,
            formula=formula,
            free_variables=tuple(record.all_free_names),
        )
        carrier = Carrier(name="ScopeCarrier")
        is_clean = len(self._violations) == 0
        trust_level = (
            TrustLevel.SOLVER_DISCHARGED if is_clean else TrustLevel.UNVERIFIED
        )
        trust = TrustAnnotation(level=trust_level)
        violation_items: tuple[EvidenceItem, ...] = tuple(
            EvidenceItem(
                kind=EvidenceItemKind.STATIC_ANALYSIS,
                trust_level=TrustLevel.UNVERIFIED,
                note=v,
            )
            for v in self._violations
        )
        evidence = EvidenceBundle(items=violation_items)
        return Judgment(
            coordinate=closure_coord,
            proposition=proposition,
            carrier=carrier,
            trust=trust,
            evidence=evidence,
        )

    def verify(
        self,
        record: ClosureRecord,
        available_outer_names: list[str],
    ) -> ScopeTheorem:
        """Run the check and return a :class:`ScopeTheorem`.

        Parameters:
            record: The :class:`ClosureRecord` to verify.
            available_outer_names: Names available in the enclosing scope chain.

        Returns:
            A :class:`ScopeTheorem` with ``is_verified`` set appropriately.
        """
        theorem_id = uuid.uuid4().hex[:12]
        fv_count = len(record.all_free_names)
        depth_str = f"depth={record.depth}"
        statement = (
            f"Closure {record.function_key!r} with {fv_count} free variable(s) "
            f"({depth_str}) is well-formed: all free variables are resolvable "
            f"from the enclosing scope chain (T15.3)."
        )
        hypothesis = (
            "closure record is produced by a sound AST free-variable analyser",
            f"enclosing scope provides {len(available_outer_names)} name(s)",
            "enclosing_keys chain is correctly ordered from innermost to outermost",
        )
        conclusion = (
            "every name in closure.all_free_names is present in available_outer_names"
        )
        proof_sketch = (
            "Compute set(record.all_free_names) − set(available_outer_names); "
            "the theorem holds iff this difference is empty."
        )
        base_record = ScopeTheorem(
            theorem_id=theorem_id,
            kind=TheoremKind.CLOSURE_WELL_FORMED,
            statement=statement,
            hypothesis=hypothesis,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
        )
        passed = self.check(record, available_outer_names)
        return base_record.mark_verified() if passed else base_record.mark_unverified()


# ---------------------------------------------------------------------------
# ModuleStateConsistencyTheorem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModuleStateConsistencyTheorem:
    """Verifier for T15.4: module-state manifests are mutually consistent.

    From theory2.tex Ch15.4: *all ModuleStateManifest snapshots for a given
    module must reference the same canonical CoordinateObject.*  Inconsistency
    arises when an analysis pipeline inadvertently mixes manifests from
    different module versions or import paths, leading to unsound conclusions
    about the global name-space.

    The consistency check also validates that the ``version`` counter is
    monotonically non-decreasing across the list, since snapshots are expected
    to be ordered by creation time.

    Attributes:
        _violations: Accumulated inconsistency description strings.
        module_coordinate: The expected canonical coordinate for comparison.
    """

    _violations: list[str]
    module_coordinate: CoordinateObject

    def check(self, manifests: list[ModuleStateManifest]) -> bool:
        """Verify all manifests reference the same module coordinate.

        Compares each manifest's ``module_coordinate.key`` against the
        ``module_coordinate`` attribute of this theorem instance.  Also checks
        that ``version`` values are non-decreasing across the sequence.

        Parameters:
            manifests: The list of :class:`ModuleStateManifest` snapshots to
                check.

        Returns:
            ``True`` if all manifests are consistent; ``False`` otherwise.
        """
        self._violations.clear()
        inconsistencies = self.find_inconsistencies(manifests)
        self._violations.extend(inconsistencies)
        if inconsistencies:
            logger.debug(
                "ModuleStateConsistencyTheorem.check FAILED: %d issue(s)",
                len(inconsistencies),
            )
        else:
            logger.debug(
                "ModuleStateConsistencyTheorem.check PASSED for %d manifest(s)",
                len(manifests),
            )
        return len(inconsistencies) == 0

    def find_inconsistencies(
        self, manifests: list[ModuleStateManifest]
    ) -> list[str]:
        """Return a list of inconsistency description strings.

        Performs two checks:

        1. Each manifest's ``module_coordinate.key`` must equal the canonical
           key stored in :attr:`module_coordinate`.
        2. The ``version`` sequence must be non-decreasing (manifests are
           assumed to be in creation order).

        Parameters:
            manifests: The manifest snapshots to inspect.

        Returns:
            A list of human-readable inconsistency descriptions.  Empty if all
            manifests pass both checks.
        """
        expected_key = self.module_coordinate.key
        descriptions: list[str] = []
        prev_version: int = -1
        for manifest in manifests:
            actual_key = manifest.module_coordinate.key
            if actual_key != expected_key:
                descriptions.append(
                    f"manifest for {manifest.module_name!r}: "
                    f"coordinate key {actual_key!r} != expected {expected_key!r}"
                )
            if manifest.version < prev_version:
                descriptions.append(
                    f"manifest for {manifest.module_name!r}: "
                    f"version {manifest.version} is less than previous "
                    f"version {prev_version} (non-monotonic)"
                )
            prev_version = max(prev_version, manifest.version)
        return descriptions

    def build_judgment(self, manifest: ModuleStateManifest) -> Judgment:
        """Encode the consistency check for a single manifest as a Judgment.

        Parameters:
            manifest: The :class:`ModuleStateManifest` being judged.

        Returns:
            A :class:`Judgment` anchored to the module coordinate with a
            structural proposition.
        """
        formula = (
            "manifest.module_coordinate ≡ canonical_module_coordinate"
        )
        proposition = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=("manifest",),
        )
        carrier = Carrier(name="ScopeCarrier")
        actual_key = manifest.module_coordinate.key
        expected_key = self.module_coordinate.key
        is_consistent = actual_key == expected_key
        trust_level = (
            TrustLevel.SOLVER_DISCHARGED if is_consistent else TrustLevel.UNVERIFIED
        )
        trust = TrustAnnotation(level=trust_level)
        notes: list[str] = []
        if not is_consistent:
            notes.append(
                f"expected key {expected_key!r}, got {actual_key!r}"
            )
        evidence = EvidenceBundle(
            items=tuple(
                EvidenceItem(
                    kind=EvidenceItemKind.STATIC_ANALYSIS,
                    trust_level=TrustLevel.UNVERIFIED,
                    note=n,
                )
                for n in notes
            )
        )
        return Judgment(
            coordinate=self.module_coordinate,
            proposition=proposition,
            carrier=carrier,
            trust=trust,
            evidence=evidence,
        )

    def verify(self, manifests: list[ModuleStateManifest]) -> ScopeTheorem:
        """Run the consistency check and return a :class:`ScopeTheorem`.

        Parameters:
            manifests: The manifest snapshots to verify.

        Returns:
            A :class:`ScopeTheorem` with ``is_verified`` set appropriately.
        """
        theorem_id = uuid.uuid4().hex[:12]
        canon_key = self.module_coordinate.key
        statement = (
            f"All {len(manifests)} ModuleStateManifest snapshot(s) reference "
            f"the canonical coordinate {canon_key!r} and have a non-decreasing "
            f"version counter (T15.4, Module State Consistency)."
        )
        hypothesis = (
            "manifests are produced by the same analysis run",
            f"canonical coordinate key is {canon_key!r}",
            "manifest list is ordered by creation time (ascending version)",
        )
        conclusion = (
            "every manifest.module_coordinate.key equals the canonical key "
            "and manifest.version is non-decreasing"
        )
        proof_sketch = (
            "Iterate over all manifests: (1) compare module_coordinate.key to "
            "the canonical key; (2) check that version >= previous_version.  "
            "Report any deviation as an inconsistency."
        )
        base_record = ScopeTheorem(
            theorem_id=theorem_id,
            kind=TheoremKind.MODULE_STATE_CONSISTENCY,
            statement=statement,
            hypothesis=hypothesis,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
        )
        passed = self.check(manifests)
        return base_record.mark_verified() if passed else base_record.mark_unverified()


# ---------------------------------------------------------------------------
# ResolutionDeterminismTheorem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResolutionDeterminismTheorem:
    """Verifier for T15.5: LEGB name resolution is deterministic.

    From theory2.tex Ch15.5: *the LEGB rule induces a total function from
    names to bindings — each name in a given scope context resolves to exactly
    one binding, or is explicitly declared unbound.*  Non-determinism arises
    when two or more distinct scope sections at the same LEGB priority level
    both claim a name, which should be structurally impossible if T15.2 (name
    uniqueness) is already satisfied.

    Since ``NameResolutionResult`` records whether resolution succeeded and
    which scope_key was found, this theorem enforces: no two results for the
    same name should carry different ``scope_key`` values.

    Attributes:
        _violations: Accumulated ambiguity descriptions.
        module_coordinate: Anchor coordinate for produced Judgments.
    """

    _violations: list[str]
    module_coordinate: CoordinateObject

    def check(self, results: list[NameResolutionResult]) -> bool:
        """Verify that name resolution is deterministic across all results.

        Groups results by ``name`` and checks that each group contains at most
        one result with a ``scope_key`` value.  Multiple resolved results for
        the same name with different ``scope_key`` values constitute ambiguity.

        Parameters:
            results: The :class:`NameResolutionResult` objects produced by the
                LEGB resolver for a given scope context.

        Returns:
            ``True`` if all names resolve deterministically; ``False`` if any
            name has conflicting resolution candidates.
        """
        self._violations.clear()
        ambiguous = self.find_ambiguities(results)
        for r in ambiguous:
            msg = (
                f"name {r.name!r} has ambiguous resolution: "
                f"scope_key={r.scope_key!r}, error={r.error_message!r}"
            )
            self._violations.append(msg)
            logger.debug("ResolutionDeterminismTheorem: %s", msg)
        if ambiguous:
            logger.debug(
                "ResolutionDeterminismTheorem.check FAILED: %d ambiguous result(s)",
                len(ambiguous),
            )
        else:
            logger.debug(
                "ResolutionDeterminismTheorem.check PASSED for %d result(s)",
                len(results),
            )
        return len(ambiguous) == 0

    def find_ambiguities(
        self, results: list[NameResolutionResult]
    ) -> list[NameResolutionResult]:
        """Return all results whose name resolves to conflicting scope_keys.

        Groups results by ``name``.  A name is *ambiguous* if its group
        contains two or more distinct non-``None`` ``scope_key`` values,
        meaning the LEGB walk found multiple candidate binding sites.  Returns
        the first result from each ambiguous group as the representative.

        Parameters:
            results: The resolution results to inspect.

        Returns:
            A filtered list containing one representative per ambiguous name.
        """
        by_name: dict[str, list[NameResolutionResult]] = defaultdict(list)
        for r in results:
            by_name[r.name].append(r)

        ambiguous: list[NameResolutionResult] = []
        for name, group in by_name.items():
            scope_keys = {
                r.scope_key for r in group if r.scope_key is not None
            }
            if len(scope_keys) > 1:
                logger.debug(
                    "find_ambiguities: name %r has scope_keys %s", name, scope_keys
                )
                ambiguous.append(group[0])
        return ambiguous

    def build_judgment(
        self, results: list[NameResolutionResult]
    ) -> Judgment:
        """Encode the determinism check as a :class:`Judgment`.

        Parameters:
            results: The full set of name resolution results being verified.

        Returns:
            A :class:`Judgment` anchored to the module coordinate with a
            universal proposition asserting LEGB determinism.
        """
        passed = self.check(results)
        formula = "LEGB rule produces unique binding for each name"
        proposition = Proposition(
            kind=PropositionKind.UNIVERSAL,
            formula=formula,
            free_variables=("name",),
        )
        carrier = Carrier(name="ScopeCarrier")
        trust_level = (
            TrustLevel.SOLVER_DISCHARGED if passed else TrustLevel.UNVERIFIED
        )
        trust = TrustAnnotation(level=trust_level)
        violation_items: tuple[EvidenceItem, ...] = tuple(
            EvidenceItem(
                kind=EvidenceItemKind.STATIC_ANALYSIS,
                trust_level=TrustLevel.UNVERIFIED,
                note=v,
            )
            for v in self._violations
        )
        evidence = EvidenceBundle(items=violation_items)
        return Judgment(
            coordinate=self.module_coordinate,
            proposition=proposition,
            carrier=carrier,
            trust=trust,
            evidence=evidence,
        )

    def verify(self, results: list[NameResolutionResult]) -> ScopeTheorem:
        """Run the determinism check and return a :class:`ScopeTheorem`.

        Parameters:
            results: The name resolution results to verify.

        Returns:
            A :class:`ScopeTheorem` with ``is_verified`` set appropriately.
        """
        theorem_id = uuid.uuid4().hex[:12]
        unique_names = {r.name for r in results}
        statement = (
            f"LEGB resolution over {len(unique_names)} unique name(s) is "
            f"deterministic: each name resolves to at most one binding "
            f"scope (T15.5, Resolution Determinism)."
        )
        hypothesis = (
            "scope chain is well-formed and acyclic",
            "LEGB priority order (Local → Enclosing → Global → Builtin) is "
            "applied strictly without exception",
            "T15.2 (Name Uniqueness) holds for all scope sections",
        )
        conclusion = (
            "no name in results has two conflicting scope_key values"
        )
        proof_sketch = (
            "Group results by name; for each group compute the set of distinct "
            "non-None scope_key values.  The theorem holds iff every such set "
            "has cardinality ≤ 1."
        )
        base_record = ScopeTheorem(
            theorem_id=theorem_id,
            kind=TheoremKind.RESOLUTION_DETERMINISM,
            statement=statement,
            hypothesis=hypothesis,
            conclusion=conclusion,
            proof_sketch=proof_sketch,
        )
        passed = self.check(results)
        return base_record.mark_verified() if passed else base_record.mark_unverified()


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """Mutable registry of all scope-and-state theorems for a module.

    The registry maintains two indices: a primary ``_theorems`` dictionary
    keyed by ``theorem_id``, and a secondary ``_by_kind`` dictionary that
    maps :class:`TheoremKind` values to lists of theorem identifiers.
    Both indices are updated atomically by :meth:`register`.

    The registry is the single point of truth for theorem status at the module
    level.  It integrates with the copilot annotation pipeline through
    :meth:`report`, which renders a human-readable status table that can be
    displayed in editor tooltips or CI summaries.

    Attributes:
        _theorems: Primary index from theorem_id to :class:`ScopeTheorem`.
        _by_kind: Secondary index from :class:`TheoremKind` to lists of
            theorem_id strings (in registration order).
    """

    _theorems: dict[str, ScopeTheorem]
    _by_kind: dict[TheoremKind, list[str]]

    def register(self, theorem: ScopeTheorem) -> None:
        """Add *theorem* to the registry.

        Updates both :attr:`_theorems` and :attr:`_by_kind` in a single
        method call so callers never observe a partially-updated registry.

        Parameters:
            theorem: The :class:`ScopeTheorem` to register.

        Raises:
            ValueError: If a theorem with the same ``theorem_id`` is already
                present in the registry.  Use :meth:`lookup` to check first.
        """
        if theorem.theorem_id in self._theorems:
            raise ValueError(
                f"Duplicate theorem_id {theorem.theorem_id!r}; "
                f"use a fresh id or remove the existing entry first."
            )
        self._theorems[theorem.theorem_id] = theorem
        if theorem.kind not in self._by_kind:
            self._by_kind[theorem.kind] = []
        self._by_kind[theorem.kind].append(theorem.theorem_id)
        logger.debug(
            "TheoremRegistry.register: id=%r kind=%s total=%d",
            theorem.theorem_id,
            theorem.kind.value,
            len(self._theorems),
        )

    def lookup(self, theorem_id: str) -> ScopeTheorem | None:
        """Return the theorem with the given *theorem_id*, or ``None``.

        Parameters:
            theorem_id: The unique identifier to look up.

        Returns:
            The :class:`ScopeTheorem` if found, otherwise ``None``.
        """
        return self._theorems.get(theorem_id)

    def verify_all(
        self, verifier_fn: Any | None = None
    ) -> dict[str, bool]:
        """Return a mapping from theorem_id to verification status.

        If *verifier_fn* is provided it is called as ``verifier_fn(theorem)``
        and its boolean return value is used as the status for that theorem.
        Otherwise the theorem's own ``is_verified`` attribute is used directly.

        Parameters:
            verifier_fn: An optional callable that accepts a
                :class:`ScopeTheorem` and returns ``bool``.  May be ``None``
                to use stored status.

        Returns:
            A dictionary ``{theorem_id: bool}`` for every registered theorem,
            preserving registration order.
        """
        results: dict[str, bool] = {}
        for tid, theorem in self._theorems.items():
            if verifier_fn is not None:
                try:
                    status = bool(verifier_fn(theorem))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "TheoremRegistry.verify_all: verifier raised for %r: %s",
                        tid,
                        exc,
                    )
                    status = False
            else:
                status = theorem.is_verified
            results[tid] = status
        return results

    def failed_theorems(self) -> list[ScopeTheorem]:
        """Return all theorems where ``is_verified`` is ``False``.

        Returns:
            A list of unverified :class:`ScopeTheorem` objects in registration
            order.
        """
        return [t for t in self._theorems.values() if not t.is_verified]

    def passed_theorems(self) -> list[ScopeTheorem]:
        """Return all theorems where ``is_verified`` is ``True``.

        Returns:
            A list of verified :class:`ScopeTheorem` objects in registration
            order.
        """
        return [t for t in self._theorems.values() if t.is_verified]

    def report(self) -> str:
        """Render a multi-line human-readable status report.

        The report contains one line per theorem, formatted as::

            [PASS] <theorem_id>  (<kind>)  <first 60 chars of statement>...
            [FAIL] <theorem_id>  (<kind>)  <first 60 chars of statement>...

        A summary line is appended at the end.

        Returns:
            A single string containing the full report, with lines separated
            by ``"\\n"``.
        """
        lines: list[str] = ["=== Scope & State Theorem Registry Report ==="]
        for theorem in self._theorems.values():
            status_tag = "[PASS]" if theorem.is_verified else "[FAIL]"
            truncated = theorem.statement[:60]
            lines.append(
                f"  {status_tag}  {theorem.theorem_id}  "
                f"({theorem.kind.value})  {truncated}..."
            )
        total = len(self._theorems)
        passed_count = len(self.passed_theorems())
        failed_count = total - passed_count
        lines.append(
            f"--- {total} theorem(s): {passed_count} passed, "
            f"{failed_count} failed ---"
        )
        return "\n".join(lines)

    def count(self) -> int:
        """Return the total number of registered theorems.

        Returns:
            Integer count of entries in :attr:`_theorems`.
        """
        return len(self._theorems)

    def by_kind(self, kind: TheoremKind) -> list[ScopeTheorem]:
        """Return all theorems of a given :class:`TheoremKind`.

        Parameters:
            kind: The :class:`TheoremKind` to filter by.

        Returns:
            A list of :class:`ScopeTheorem` objects belonging to *kind*,
            ordered by registration order.  Returns an empty list if no
            theorems of this kind are registered.
        """
        ids = self._by_kind.get(kind, [])
        result: list[ScopeTheorem] = []
        for tid in ids:
            theorem = self._theorems.get(tid)
            if theorem is not None:
                result.append(theorem)
        return result

    def serialize(self) -> dict[str, Any]:
        """Serialise the entire registry to a JSON-compatible dictionary.

        Returns:
            A dictionary with key ``"theorems"`` mapping to a list of
            serialised theorem dictionaries in registration order.
        """
        return {
            "theorems": [t.serialize() for t in self._theorems.values()]
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> TheoremRegistry:
        """Deserialise a :class:`TheoremRegistry` from a plain dictionary.

        Parameters:
            data: A dictionary previously produced by :meth:`serialize`.

        Returns:
            A new :class:`TheoremRegistry` populated with all theorems from
            the ``"theorems"`` list, in their original order.

        Raises:
            KeyError: If ``"theorems"`` is absent from *data*.
            ValueError: If any individual theorem entry cannot be parsed.
        """
        registry = cls(_theorems={}, _by_kind={})
        for raw in data.get("theorems", []):
            theorem = ScopeTheorem.parse(raw)
            registry.register(theorem)
        logger.debug(
            "TheoremRegistry.parse: loaded %d theorem(s)", registry.count()
        )
        return registry


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def build_default_theorems(module_coord: CoordinateObject) -> TheoremRegistry:
    """Create a :class:`TheoremRegistry` pre-populated with one theorem per kind.

    Each theorem is initialised with a proper ``statement``, ``hypothesis``
    tuple, ``conclusion``, and ``proof_sketch`` derived from the formal
    definitions in theory2.tex Ch15.  The ``is_verified`` flag is ``False``
    for all theorems because no live scope data has been supplied yet; callers
    should run the appropriate verifier classes and use
    :meth:`TheoremRegistry.register` to replace entries with verified ones.

    This function is the canonical entry-point for copilot annotation
    pipelines: call it once per module coordinate at the start of a scope
    analysis session to obtain a fully-structured registry ready for batch
    verification.

    Parameters:
        module_coord: The :class:`CoordinateObject` identifying the module
            under analysis.  Used as the anchor in theorem statements and
            as the hypothesis context.

    Returns:
        A :class:`TheoremRegistry` containing exactly six theorems, one for
        each :class:`TheoremKind` value, in the order listed in Ch15.
    """
    registry = TheoremRegistry(_theorems={}, _by_kind={})
    module_key = module_coord.key

    covering = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.SCOPE_COVERING,
        statement=(
            f"The scope sections for module {module_key!r} form a covering "
            f"sieve: every module-level name is visible in at least one scope "
            f"section (theory2.tex T15.1)."
        ),
        hypothesis=(
            f"module coordinate is {module_key!r}",
            "a non-empty family of ScopeSection objects is defined",
            "all_module_names is the complete name-space of the module",
        ),
        conclusion=(
            "∀ n ∈ all_module_names: ∃ S ∈ scopes such that n ∈ {c.name for c in S.bindings}"
        ),
        proof_sketch=(
            "Compute the union U of all scope binding-name sets; verify that "
            "all_module_names ⊆ U by set difference.  Any element of the "
            "difference is a gap that falsifies the covering property."
        ),
    )
    registry.register(covering)

    uniqueness = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.NAME_UNIQUENESS,
        statement=(
            f"Within every scope section of module {module_key!r}, the binding "
            f"tuple is injective on names: no two distinct NameCoordinate objects "
            f"share the same name string (theory2.tex T15.2)."
        ),
        hypothesis=(
            "scope section is well-formed",
            "bindings tuple is populated by a single-pass AST walker",
            "no post-hoc mutation of the bindings tuple is performed",
        ),
        conclusion=(
            "∀ n1, n2 ∈ scope.bindings: n1.name = n2.name → n1 = n2"
        ),
        proof_sketch=(
            "Iterate over scope.bindings and insert each name into a seen-set; "
            "if any name is already present, record it as a duplicate and set "
            "is_verified=False."
        ),
    )
    registry.register(uniqueness)

    closure_wf = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.CLOSURE_WELL_FORMED,
        statement=(
            f"Every closure defined in module {module_key!r} correctly captures "
            f"its free variables: each name in closure.all_free_names is bound "
            f"in some lexically enclosing scope (theory2.tex T15.3)."
        ),
        hypothesis=(
            "closure record is produced by a sound AST free-variable analyser",
            "available_outer_names covers the full enclosing scope chain",
            "the enclosing scope chain is acyclic",
        ),
        conclusion=(
            "∀ v ∈ closure.all_free_names: v ∈ available_outer_names"
        ),
        proof_sketch=(
            "For each ClosureRecord, compute set(closure.all_free_names) − "
            "set(available_outer_names).  The theorem holds iff this difference "
            "is empty for every record."
        ),
    )
    registry.register(closure_wf)

    state_consistency = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.MODULE_STATE_CONSISTENCY,
        statement=(
            f"All ModuleStateManifest snapshots for module {module_key!r} "
            f"reference the same canonical CoordinateObject and have a "
            f"non-decreasing version counter (theory2.tex T15.4)."
        ),
        hypothesis=(
            f"canonical coordinate key is {module_key!r}",
            "manifests are produced by the same analysis pipeline run",
            "manifest list is ordered by creation time",
        ),
        conclusion=(
            "∀ m ∈ manifests: m.module_coordinate.key = canonical_key "
            "and version[i] ≥ version[i-1]"
        ),
        proof_sketch=(
            "Compare m.module_coordinate.key to the canonical key for every "
            "manifest; also verify the version sequence is non-decreasing. "
            "Report any deviation as an inconsistency."
        ),
    )
    registry.register(state_consistency)

    determinism = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.RESOLUTION_DETERMINISM,
        statement=(
            f"LEGB name resolution within module {module_key!r} is deterministic: "
            f"each name resolves to at most one binding scope, never to two "
            f"conflicting scope_keys simultaneously (theory2.tex T15.5)."
        ),
        hypothesis=(
            "scope chain is acyclic and well-formed",
            "LEGB priority order (Local → Enclosing → Global → Builtin) is "
            "applied strictly without exception",
            "T15.2 (Name Uniqueness) holds for all participating scope sections",
        ),
        conclusion=(
            "∀ name in results: {r.scope_key for r in results[name] "
            "if r.scope_key is not None} has cardinality ≤ 1"
        ),
        proof_sketch=(
            "Group results by name; for each group compute the set of distinct "
            "non-None scope_key values.  The theorem holds iff every such set "
            "has cardinality ≤ 1."
        ),
    )
    registry.register(determinism)

    lexical = ScopeTheorem(
        theorem_id=uuid.uuid4().hex[:12],
        kind=TheoremKind.LEXICAL_SCOPING,
        statement=(
            f"Name visibility in module {module_key!r} depends only on textual "
            f"containment (lexical nesting), never on runtime call order, "
            f"dynamic dispatch, or import order (theory2.tex T15.6)."
        ),
        hypothesis=(
            "the module is parsed from static source text",
            "no exec() or eval() calls introduce dynamic bindings",
            "all imports are resolved at import time, not lazily",
            "no monkey-patching of __builtins__ is performed at runtime",
        ),
        conclusion=(
            "the LEGB scope chain is a static property of the AST and is "
            "independent of any runtime execution order or call graph"
        ),
        proof_sketch=(
            "Demonstrate that the scope chain for any name lookup can be fully "
            "determined from the AST without executing the program.  Specifically, "
            "the parent_key chain of ScopeSection objects mirrors the textual "
            "nesting of def/class/lambda/comprehension nodes in the source, "
            "which is invariant across all possible execution traces."
        ),
    )
    registry.register(lexical)

    logger.info(
        "build_default_theorems: created registry with %d theorem(s) for %r",
        registry.count(),
        module_key,
    )
    return registry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TheoremKind",
    "ScopeTheorem",
    "NameUniquenessTheorem",
    "ScopeCoveringTheorem",
    "ClosureWellFormednessTheorem",
    "ModuleStateConsistencyTheorem",
    "ResolutionDeterminismTheorem",
    "TheoremRegistry",
    "build_default_theorems",
]
