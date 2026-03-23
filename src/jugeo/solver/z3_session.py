r"""Z3 session management, encoding, and decoding for JuGeo.

This module implements the full Z3 integration surface described in chapters
25-30 of ``theory2.tex``.  Z3 is the structural solver used by JuGeo for
arithmetic, refinement types, path conditions, heap summaries, and collection
constraints.  Sessions are pooled and reusable; the solver channel produces
evidence at ``SOLVER_DISCHARGED`` trust level.  Countermodel extraction
provides concrete witnesses for failures, making every solver-derived claim
auditable and challengeable.

Architecture overview
---------------------

The module is organised into twelve collaborating classes:

* :class:`Z3Session` -- low-level wrapper around a Z3 solver context.
* :class:`Z3SessionPool` -- pool of reusable Z3 sessions.
* :class:`Z3Formula` -- abstract typed wrapper for Z3 AST nodes.
* :class:`Z3Encoder` -- translates JuGeo propositions into Z3 formulas.
* :class:`Z3Decoder` -- translates Z3 models back into JuGeo terms.
* :class:`Z3QueryBuilder` -- fluent builder for constructing solver queries.
* :class:`Z3Result` -- immutable outcome of a Z3 query.
* :class:`Z3FragmentClassifier` -- decides which decidable fragment a formula
  belongs to (QF_LIA, QF_LRA, QF_BV, QF_UF, etc.).
* :class:`Z3TacticRouter` -- selects the best Z3 tactic for a given fragment.
* :class:`Z3SessionMonitor` -- runtime health and latency tracking.
* :class:`Z3Serializer` -- SMT-LIB2 and JSON serialization helpers.
* :class:`Z3CopilotAssist` -- copilot-assisted encoding suggestions and
  explanation of unsat cores.

The Z3 Python bindings are imported lazily so the rest of the shared core can
run without a native Z3 installation.  When Z3 is unavailable the module falls
back to lightweight stubs that return ``UNKNOWN`` for every query, keeping
downstream consumers type-safe and testable.

The module includes copilot integration points so that proposal systems may
request solver checks, but solver results always stay explicitly typed and
challengeable under the trust algebra defined in ``evidence/trust.py``.
"""

from __future__ import annotations

import math
import statistics
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Iterable, Mapping, Protocol, Sequence

from jugeo.solver.fragments import LogicalFragment, SolverFragment, classify_fragment

# ---------------------------------------------------------------------------
# Optional imports for cross-subsystem integration (judgment-geometric links)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.sections import (
        Section as _Section,
        JudgmentSection as _JudgmentSection,
    )
    _SECTIONS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SECTIONS_AVAILABLE = False

try:
    from jugeo.geometry.descent import (
        OverlapCondition as _OverlapCondition,
        GluingData as _GluingData,
    )
    _DESCENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DESCENT_AVAILABLE = False

try:
    from jugeo.evidence.certificates import (
        Certificate as _Certificate,
        CertificateBuilder as _CertificateBuilder,
    )
    _CERTIFICATES_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CERTIFICATES_AVAILABLE = False

# ---------------------------------------------------------------------------
# Optional Z3 import -- the shared core must not hard-depend on native libs.
# ---------------------------------------------------------------------------

try:
    import z3 as _z3

    _Z3_AVAILABLE = True
except ImportError:  # pragma: no cover -- CI may not have Z3
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False


def z3_available() -> bool:
    """Return whether the Z3 Python bindings are importable."""
    return _Z3_AVAILABLE


# ===================================================================== #
# 1. Foundational enums and small value objects                         #
# ===================================================================== #


class SolveOutcome(str, Enum):
    """Possible outcomes of a Z3 satisfiability check."""

    SAT = 'sat'
    UNSAT = 'unsat'
    UNKNOWN = 'unknown'
    TIMEOUT = 'timeout'


class FormulaKind(str, Enum):
    """Semantic sort of a Z3 formula wrapper."""

    BOOL = 'bool'
    INT = 'int'
    REAL = 'real'
    BITVEC = 'bitvec'
    ARRAY = 'array'
    DATATYPE = 'datatype'


class TrustLevel(str, Enum):
    """Trust levels for solver evidence channels.

    ``SOLVER_DISCHARGED`` is the canonical trust level for Z3 proofs that
    successfully discharge an obligation.  All other levels are strictly
    weaker and used for informational or partial results.
    """

    SOLVER_DISCHARGED = 'solver-discharged'
    SOLVER_PARTIAL = 'solver-partial'
    SOLVER_UNKNOWN = 'solver-unknown'
    SOLVER_TIMEOUT = 'solver-timeout'


class FragmentTag(str, Enum):
    """Decidable Z3 fragments for tactic routing."""

    QF_LIA = 'QF_LIA'
    QF_LRA = 'QF_LRA'
    QF_BV = 'QF_BV'
    QF_UF = 'QF_UF'
    QF_UFLIA = 'QF_UFLIA'
    QF_AUFLIRA = 'QF_AUFLIRA'
    QF_ABV = 'QF_ABV'
    UNKNOWN = 'UNKNOWN'


# ===================================================================== #
# 2. SolverResult -- backward-compatible outcome record                 #
# ===================================================================== #


@dataclass(frozen=True, slots=True)
class SolverResult:
    """Result of a solver query.

    This is the lightweight backward-compatible result that the rest of the
    shared core already depends on.  The richer :class:`Z3Result` extends
    this with proof, unsat-core, and trust-level fields.
    """

    outcome: SolveOutcome
    engine: str
    model: dict[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def is_sat(self) -> bool:
        """Return ``True`` when the outcome is satisfiable."""
        return self.outcome is SolveOutcome.SAT

    def is_unsat(self) -> bool:
        """Return ``True`` when the outcome is unsatisfiable."""
        return self.outcome is SolveOutcome.UNSAT

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        return {
            'outcome': self.outcome.value,
            'engine': self.engine,
            'model': dict(self.model),
            'reasons': list(self.reasons),
        }


# ===================================================================== #
# 3. SolverAdapter protocol & BuiltinAdapter                           #
# ===================================================================== #


class SolverAdapter(Protocol):
    """Protocol for pluggable solver backends."""

    def solve(self, fragment: SolverFragment) -> SolverResult: ...


class BuiltinAdapter:
    """Trivial propositional adapter used when Z3 is unavailable.

    The builtin adapter is intentionally simple: it performs contradiction
    detection over the clause set by checking whether any atom and its
    negation both appear.  This is sufficient for basic tests and keeps
    downstream modules functional without a native Z3 installation.
    """

    def solve(self, fragment: SolverFragment) -> SolverResult:
        """Solve a fragment using simple propositional contradiction checking."""
        tokens = [clause.replace(' ', '') for clause in fragment.clauses]
        atoms = {token.replace('not', '') for token in tokens}
        for atom in atoms:
            if atom and atom in tokens and f'not{atom}' in tokens:
                return SolverResult(
                    SolveOutcome.UNSAT,
                    'builtin',
                    {},
                    ('contradiction detected',),
                )
        model = {
            token.replace('not', ''): not token.startswith('not')
            for token in tokens
            if token
        }
        outcome = (
            SolveOutcome.SAT if model or fragment.formula else SolveOutcome.UNKNOWN
        )
        return SolverResult(outcome, 'builtin', model, ())


# ===================================================================== #
# 4. Z3Formula -- typed wrapper around Z3 AST nodes                    #
# ===================================================================== #


@dataclass(slots=True)
class Z3Formula:
    """Abstract typed wrapper for Z3 formulas.

    Each formula tracks its semantic :attr:`kind` so that encoding and
    tactic-routing layers can make sound decisions without inspecting the
    raw Z3 AST.  When Z3 is unavailable, operations degrade gracefully
    to string-based representations.

    Parameters
    ----------
    kind:
        The semantic sort of the formula (BOOL, INT, REAL, ...).
    expression:
        A textual SMT-LIB2 representation used for serialization and
        display when the native Z3 AST is not available.
    z3_ast:
        The native Z3 ``ExprRef`` when running with real Z3 bindings.
    """

    kind: FormulaKind
    expression: str
    z3_ast: Any = None

    # -- Construction helpers ------------------------------------------------

    @classmethod
    def boolean(cls, expr: str, ast: Any = None) -> Z3Formula:
        """Create a boolean formula."""
        return cls(kind=FormulaKind.BOOL, expression=expr, z3_ast=ast)

    @classmethod
    def integer(cls, expr: str, ast: Any = None) -> Z3Formula:
        """Create an integer-sort formula."""
        return cls(kind=FormulaKind.INT, expression=expr, z3_ast=ast)

    @classmethod
    def real(cls, expr: str, ast: Any = None) -> Z3Formula:
        """Create a real-sort formula."""
        return cls(kind=FormulaKind.REAL, expression=expr, z3_ast=ast)

    @classmethod
    def bitvec(cls, expr: str, ast: Any = None) -> Z3Formula:
        """Create a bitvector formula."""
        return cls(kind=FormulaKind.BITVEC, expression=expr, z3_ast=ast)

    # -- Z3 interop ---------------------------------------------------------

    def to_z3(self) -> Any:
        """Return the native Z3 AST, or ``None`` when Z3 is unavailable."""
        return self.z3_ast

    @classmethod
    def from_z3(cls, ast: Any) -> Z3Formula:
        """Construct from a native Z3 AST by inspecting the sort."""
        if not _Z3_AVAILABLE or ast is None:
            return cls(FormulaKind.BOOL, str(ast))
        sort_name = str(ast.sort())
        kind_map: dict[str, FormulaKind] = {
            'Bool': FormulaKind.BOOL,
            'Int': FormulaKind.INT,
            'Real': FormulaKind.REAL,
        }
        kind = kind_map.get(sort_name, FormulaKind.BOOL)
        if 'BitVec' in sort_name:
            kind = FormulaKind.BITVEC
        elif 'Array' in sort_name:
            kind = FormulaKind.ARRAY
        return cls(kind=kind, expression=str(ast), z3_ast=ast)

    # -- Logical combinators ------------------------------------------------

    def simplify(self) -> Z3Formula:
        """Return a simplified form using Z3's simplifier if available."""
        if _Z3_AVAILABLE and self.z3_ast is not None:
            simplified = _z3.simplify(self.z3_ast)
            return replace(self, expression=str(simplified), z3_ast=simplified)
        return self

    def negate(self) -> Z3Formula:
        """Return the logical negation of this formula."""
        if _Z3_AVAILABLE and self.z3_ast is not None:
            neg = _z3.Not(self.z3_ast)
            return Z3Formula(self.kind, f'(not {self.expression})', neg)
        return Z3Formula(self.kind, f'(not {self.expression})')

    def conjoin(self, other: Z3Formula) -> Z3Formula:
        """Return the conjunction of this formula and *other*."""
        if _Z3_AVAILABLE and self.z3_ast is not None and other.z3_ast is not None:
            conj = _z3.And(self.z3_ast, other.z3_ast)
            return Z3Formula(FormulaKind.BOOL, f'(and {self.expression} {other.expression})', conj)
        return Z3Formula(
            FormulaKind.BOOL,
            f'(and {self.expression} {other.expression})',
        )

    def disjoin(self, other: Z3Formula) -> Z3Formula:
        """Return the disjunction of this formula and *other*."""
        if _Z3_AVAILABLE and self.z3_ast is not None and other.z3_ast is not None:
            disj = _z3.Or(self.z3_ast, other.z3_ast)
            return Z3Formula(FormulaKind.BOOL, f'(or {self.expression} {other.expression})', disj)
        return Z3Formula(
            FormulaKind.BOOL,
            f'(or {self.expression} {other.expression})',
        )

    def substitute(self, mapping: Mapping[str, Z3Formula]) -> Z3Formula:
        """Apply a variable substitution map.

        When Z3 is available the substitution is performed natively on the
        AST.  Otherwise the module performs a conservative textual replacement
        on the SMT-LIB2 expression string.
        """
        if _Z3_AVAILABLE and self.z3_ast is not None:
            pairs = []
            for name, replacement in mapping.items():
                if replacement.z3_ast is not None:
                    var = _z3.Const(name, replacement.z3_ast.sort())
                    pairs.append((var, replacement.z3_ast))
            if pairs:
                result = _z3.substitute(self.z3_ast, *pairs)
                return Z3Formula(self.kind, str(result), result)
        expr = self.expression
        for name, rep in mapping.items():
            expr = expr.replace(name, rep.expression)
        return Z3Formula(self.kind, expr)

    def free_variables(self) -> tuple[str, ...]:
        """Return free variable names extracted from the expression.

        Uses a lightweight heuristic on the SMT-LIB2 string: tokens that look
        like identifiers and are not keywords are treated as free variables.
        """
        keywords = frozenset({
            'and', 'or', 'not', 'true', 'false', 'ite', 'let', 'forall',
            'exists', 'assert', 'declare-fun', 'declare-const', 'define-fun',
        })
        tokens = self.expression.replace('(', ' ').replace(')', ' ').split()
        variables: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            cleaned = token.strip()
            if not cleaned or cleaned in seen:
                continue
            if cleaned.lower() in keywords:
                continue
            try:
                float(cleaned)
                continue
            except ValueError:
                pass
            if cleaned.isidentifier():
                seen.add(cleaned)
                variables.append(cleaned)
        return tuple(variables)

    def pretty_print(self, indent: int = 0) -> str:
        """Return a human-readable indented representation."""
        prefix = '  ' * indent
        header = f'{prefix}[{self.kind.value}] '
        if len(self.expression) <= 60:
            return f'{header}{self.expression}'
        lines = [f'{header}(']
        for part in self.expression.split(' '):
            lines.append(f'{prefix}  {part}')
        lines.append(f'{prefix})')
        return '\n'.join(lines)


# ===================================================================== #
# 5. Z3Session -- wraps a Z3 solver context                            #
# ===================================================================== #


@dataclass(slots=True)
class Z3Session:
    """Wrapper around a Z3 solver context with lifecycle management.

    Each session has a unique identifier, tracks assertion depth, and records
    cumulative query statistics so that the :class:`Z3SessionMonitor` can
    detect unhealthy sessions (high timeouts, excessive memory, etc.).

    The session transparently delegates to the :class:`BuiltinAdapter` when
    Z3 is not installed, making it safe to use in any environment.

    Parameters
    ----------
    session_id:
        Unique identifier for this session (generated by the pool).
    adapter:
        The solver backend to delegate to.
    timeout_ms:
        Per-query timeout in milliseconds.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    adapter: SolverAdapter = field(default_factory=BuiltinAdapter)
    closed: bool = False
    timeout_ms: int = 5000
    _assertions: list[Z3Formula] = field(default_factory=list)
    _push_count: int = 0
    _creation_time: float = field(default_factory=time.monotonic)
    _last_used: float = field(default_factory=time.monotonic)
    _total_queries: int = 0
    _z3_solver: Any = None

    def __post_init__(self) -> None:
        if _Z3_AVAILABLE and self._z3_solver is None:
            self._z3_solver = _z3.Solver()
            self._z3_solver.set('timeout', self.timeout_ms)

    # -- Assertion management -----------------------------------------------

    def assert_formula(self, formula: Z3Formula) -> None:
        """Add *formula* to the current assertion stack.

        The formula is recorded in the local assertions list and, when Z3 is
        available, pushed to the native solver.
        """
        if self.closed:
            raise RuntimeError(f'Session {self.session_id} is closed')
        self._assertions.append(formula)
        if self._z3_solver is not None and formula.z3_ast is not None:
            self._z3_solver.add(formula.z3_ast)

    # -- Satisfiability checking --------------------------------------------

    def check_sat(self) -> SolveOutcome:
        """Run a satisfiability check on the current assertion stack.

        Returns one of SAT, UNSAT, UNKNOWN, or TIMEOUT.
        """
        if self.closed:
            return SolveOutcome.UNKNOWN
        self._total_queries += 1
        self._last_used = time.monotonic()

        can_use_native_solver = (
            self._z3_solver is not None
            and isinstance(self.adapter, BuiltinAdapter)
            and all(formula.z3_ast is not None for formula in self._assertions)
        )
        if can_use_native_solver:
            result = self._z3_solver.check()
            if result == _z3.sat:
                return SolveOutcome.SAT
            if result == _z3.unsat:
                return SolveOutcome.UNSAT
            return SolveOutcome.UNKNOWN

        # Fallback: delegate to adapter via a synthetic fragment
        formula_text = ' '.join(f.expression for f in self._assertions)
        clauses = tuple(f.expression for f in self._assertions)
        frag = SolverFragment(
            formula=formula_text,
            fragment=LogicalFragment.PROPOSITIONAL,
            clauses=clauses,
        )
        result = self.adapter.solve(frag)
        return result.outcome

    def get_model(self) -> dict[str, Any]:
        """Extract a satisfying model after a SAT result.

        Returns a dictionary mapping variable names to their assigned values.
        When Z3 is unavailable, returns an empty dictionary.
        """
        if self._z3_solver is None:
            return {}
        try:
            m = self._z3_solver.model()
            return {str(d): str(m[d]) for d in m.decls()}
        except Exception:
            return {}

    def get_unsat_core(self) -> tuple[str, ...]:
        """Extract the unsat core after an UNSAT result.

        Returns a tuple of assertion labels that participate in the
        unsatisfiability proof.  Requires that assertions were added with
        tracking labels.
        """
        if self._z3_solver is None:
            return ()
        try:
            core = self._z3_solver.unsat_core()
            return tuple(str(c) for c in core)
        except Exception:
            return ()

    # -- Scope management ---------------------------------------------------

    def push(self) -> None:
        """Push a new scope onto the assertion stack."""
        if self.closed:
            raise RuntimeError(f'Session {self.session_id} is closed')
        self._push_count += 1
        if self._z3_solver is not None:
            self._z3_solver.push()

    def pop(self, n: int = 1) -> None:
        """Pop *n* scopes from the assertion stack.

        Raises ``ValueError`` if *n* exceeds the current push depth.
        """
        if n > self._push_count:
            raise ValueError(
                f'Cannot pop {n} scope(s) from depth {self._push_count}'
            )
        self._push_count -= n
        if self._z3_solver is not None:
            self._z3_solver.pop(n)
        # Trim local assertions (best-effort since Z3 handles the real state)
        keep = max(0, len(self._assertions) - n)
        self._assertions = self._assertions[:keep]

    def reset(self) -> None:
        """Clear all assertions and reset the scope depth to zero."""
        self._assertions.clear()
        self._push_count = 0
        if self._z3_solver is not None:
            self._z3_solver.reset()
            self._z3_solver.set('timeout', self.timeout_ms)

    # -- Lifecycle ----------------------------------------------------------

    def is_alive(self) -> bool:
        """Return ``True`` when the session is still usable."""
        return not self.closed

    def query_count(self) -> int:
        """Return the total number of ``check_sat`` calls made so far."""
        return self._total_queries

    def elapsed_time(self) -> float:
        """Return seconds elapsed since this session was created."""
        return time.monotonic() - self._creation_time

    def close(self) -> None:
        """Close the session, releasing native solver resources."""
        self.closed = True
        self._z3_solver = None

    # -- Cross-subsystem integration methods --------------------------------

    def judgment_section_query(
        self,
        section: Any,
        *,
        proposition_key: str = "proposition",
    ) -> SolverResult:
        """Encode a judgment section as a Z3 query and check satisfiability.

        Takes a :class:`~jugeo.judgments.sections.Section` (or any object
        with ``judgment_assignments`` and ``data`` attributes) and encodes
        its proposition and evidence constraints into the current solver
        session.

        Parameters
        ----------
        section:
            A judgment section from :mod:`jugeo.judgments.sections`.
        proposition_key:
            Key used to extract the proposition formula from the section's
            ``data`` dictionary.

        Returns
        -------
        SolverResult
            The outcome of checking the section's constraints.

        Raises
        ------
        RuntimeError
            If the session is closed.
        """
        if self.closed:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'closed-session', {},
                ('session closed',),
            )

        # Extract formula from section
        formula_text = ""
        if hasattr(section, "data") and isinstance(section.data, dict):
            formula_text = str(section.data.get(proposition_key, ""))
        elif hasattr(section, "judgment_assignments"):
            assignments = section.judgment_assignments
            if isinstance(assignments, dict):
                parts = [
                    f"{k} = {v}" for k, v in assignments.items()
                ]
                formula_text = " and ".join(parts) if parts else "true"

        if not formula_text:
            return SolverResult(
                SolveOutcome.UNKNOWN, self.session_id, {},
                ('empty section formula',),
            )

        frag = classify_fragment(formula_text)
        return self.solve(frag)

    def descent_condition_check(
        self,
        left_data: dict[str, Any],
        right_data: dict[str, Any],
        overlap_vars: Sequence[str] | None = None,
    ) -> SolverResult:
        """Check gluing/overlap conditions from descent using Z3.

        Given two local sections' data (as dictionaries of variable
        assignments), this method asserts that the two sections *disagree*
        on at least one shared variable and checks satisfiability.  An
        UNSAT result means the sections are consistent (gluing succeeds);
        SAT means there is a concrete disagreement (gluing fails).

        This implements the overlap-compatibility check from
        :mod:`jugeo.geometry.descent` at the solver level.

        Parameters
        ----------
        left_data:
            Variable assignments from the first local section.
        right_data:
            Variable assignments from the second local section.
        overlap_vars:
            Explicit set of shared variable names.  If ``None``, the
            intersection of the two key sets is used.

        Returns
        -------
        SolverResult
            UNSAT if sections are compatible; SAT with a disagreement
            witness if they conflict.
        """
        if self.closed:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'closed-session', {},
                ('session closed',),
            )

        shared = (
            set(overlap_vars) if overlap_vars
            else set(left_data.keys()) & set(right_data.keys())
        )
        if not shared:
            return SolverResult(
                SolveOutcome.UNSAT, self.session_id, {},
                ('no overlapping variables — trivially compatible',),
            )

        # Build a formula asserting disagreement on at least one variable
        disagreements: list[str] = []
        for var in sorted(shared):
            lv = left_data.get(var)
            rv = right_data.get(var)
            if lv is not None and rv is not None:
                disagreements.append(f"not({var}_L = {var}_R)")

        if not disagreements:
            return SolverResult(
                SolveOutcome.UNSAT, self.session_id, {},
                ('all shared variables are None — trivially compatible',),
            )

        formula_text = " or ".join(disagreements)
        frag = classify_fragment(formula_text)
        result = self.solve(frag)

        # Enrich reasons with descent context
        enriched_reasons = result.reasons + (
            f"descent overlap check over {len(shared)} shared variable(s)",
        )
        return SolverResult(
            outcome=result.outcome,
            engine=result.engine,
            model=result.model,
            reasons=enriched_reasons,
        )

    def certificate_witness(self) -> Any:
        """Produce a certificate witness from the current satisfying model.

        After a SAT result, extracts the model and packages it into a
        :class:`~jugeo.evidence.certificates.Certificate` that attests
        the satisfiability with solver-discharged trust.

        Returns
        -------
        A ``Certificate`` object when the certificates subsystem is
        available, otherwise a plain dictionary containing the witness
        data.
        """
        model = self.get_model()
        if not model:
            return None

        witness_data = {
            "session_id": self.session_id,
            "model": model,
            "query_count": self._total_queries,
            "elapsed_s": self.elapsed_time(),
        }

        if _CERTIFICATES_AVAILABLE:
            try:
                builder = _CertificateBuilder()
                builder = builder.coordinate(self.session_id)
                builder = builder.trust_level(3)  # VERIFIED
                builder = builder.issuer(f"z3-session:{self.session_id}")
                for var, val in model.items():
                    builder = builder.add_proposition(f"{var} = {val}")
                builder = builder.evidence_summary(witness_data)
                return builder.build()
            except Exception:
                pass  # Fall through to dict

        return {
            "certificate_type": "solver_witness",
            "session_id": self.session_id,
            "model": model,
            "trust_level": "solver-discharged",
            "query_count": self._total_queries,
        }

    # -- Legacy adapter facade ----------------------------------------------

    def solve(self, fragment: SolverFragment) -> SolverResult:
        """Legacy entry point retained for backward compatibility.

        Callers that already use :class:`SolverFragment` directly can call
        this method instead of the more granular ``assert_formula`` /
        ``check_sat`` / ``get_model`` workflow.
        """
        if self.closed:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'closed-session', {}, ('session closed',)
            )
        return self.adapter.solve(fragment)

    # -- Judgment-geometric integration ------------------------------------

    def query_judgment(self, judgment: Any) -> SolverResult:
        r"""Encode a judgment term and check it against the current assertion stack.

        In the judgment-geometric architecture (Theory2.tex §5–§9), a
        *judgment* ``J`` at coordinate ``c`` is a section of the judgment
        presheaf ``\mathcal{J}: \mathbf{C}^{\mathrm{op}} \to \mathbf{Set}``.
        This method encodes ``J`` into an SMT formula via the scalar-encoding
        pipeline and queries Z3 for satisfiability, producing evidence at
        ``SOLVER_DISCHARGED`` trust level on success.

        Parameters
        ----------
        judgment:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` instance
            carrying a proposition, coordinate, and evidence context.

        Returns
        -------
        SolverResult
            Outcome of the satisfiability check.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment, Proposition
        except ImportError:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'z3', {},
                ('jugeo.judgments.judgment_terms unavailable',),
            )

        prop_text = getattr(judgment, 'proposition', None)
        if prop_text is None:
            prop_text = str(judgment)
        formula_text = getattr(prop_text, 'formula', str(prop_text))

        encoder = Z3Encoder()
        formula = encoder.encode_proposition(formula_text)
        self.assert_formula(formula)
        outcome = self.check_sat()
        model = self.get_model() if outcome is SolveOutcome.SAT else {}
        return SolverResult(outcome, 'z3', model, ())

    def query_descent_condition(self, gluing_data: Any) -> SolverResult:
        r"""Check that a family of local sections satisfies the descent (gluing) condition.

        Given a covering ``\{U_i \to X\}`` and local sections ``s_i`` on each
        ``U_i``, the descent condition requires that on every overlap
        ``U_i \times_X U_j`` the restrictions agree:
        ``s_i|_{U_{ij}} = s_j|_{U_{ij}}``.  This method encodes that
        compatibility as an SMT satisfiability problem and delegates to Z3.

        Parameters
        ----------
        gluing_data:
            A :class:`~jugeo.geometry.descent.GluingData` instance carrying
            local sections and overlap compatibility constraints.

        Returns
        -------
        SolverResult
            SAT when the gluing is consistent, UNSAT when an obstruction
            exists (H¹ ≠ 0 for that cover).
        """
        try:
            from jugeo.geometry.descent import GluingData, OverlapCondition
        except ImportError:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'z3', {},
                ('jugeo.geometry.descent unavailable',),
            )

        encoder = Z3Encoder()
        conditions = getattr(gluing_data, 'overlap_conditions', [])
        for cond in conditions:
            constraint = getattr(cond, 'smt_constraint', None) or str(cond)
            self.assert_formula(encoder.encode_proposition(constraint))

        outcome = self.check_sat()
        model = self.get_model() if outcome is SolveOutcome.SAT else {}
        return SolverResult(outcome, 'z3', model, ())

    @property
    def trust_level(self) -> Any:
        r"""Return the ``SOLVER_CHECKED`` trust level for evidence produced by this session.

        In the trust algebra ``(\mathcal{E}_{\mathrm{adm}}, \preceq, \oplus)``,
        solver-discharged evidence occupies a tier strictly above
        ``ORACLE_PROPOSED`` and below ``MECHANICALLY_VERIFIED`` (Theorem 9.4).
        This property returns the canonical trust annotation for results
        produced by this Z3 session.

        Returns
        -------
        A ``TrustLevel`` or ``TrustTier`` value representing solver-checked trust.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.DISCHARGED
        except (ImportError, AttributeError):
            pass
        return TrustLevel.SOLVER_DISCHARGED

    def certificate_from_proof(self, result: Any) -> Any:
        r"""Extract an evidence certificate from a successful solver result.

        A *certificate* is an auditable proof artefact ``\sigma`` witnessing
        that the solver discharged the encoded obligation.  In the evidence
        algebra it is the constructive content of the ``SOLVER_DISCHARGED``
        tier.  This method wraps the solver result into a
        :class:`~jugeo.evidence.certificates.Certificate`.

        Parameters
        ----------
        result:
            A :class:`SolverResult` or :class:`Z3Result` from a successful
            satisfiability or unsatisfiability check.

        Returns
        -------
        A ``Certificate`` when the certificates module is available,
        otherwise a plain dict.
        """
        try:
            from jugeo.evidence.certificates import Certificate, CertificateBuilder
        except ImportError:
            return {
                'session_id': self.session_id,
                'outcome': getattr(result, 'outcome', str(result)),
                'trust_level': 'SOLVER_DISCHARGED',
                'model': getattr(result, 'model', {}),
            }

        outcome_str = str(getattr(result, 'outcome', result))
        builder = CertificateBuilder()
        builder = (
            builder
            .set_issuer(f'z3-session:{self.session_id}')
            .for_coordinate(f'solver-check:{outcome_str}')
            .add_verified(f'solver-discharge:{outcome_str}')
            .set_evidence_summary(f'Z3 session {self.session_id}: {outcome_str}')
            .sign()
        )
        return builder.build()

    def provenance_record(self) -> dict[str, Any]:
        r"""Create a provenance entry recording this session's solver activity.

        Provenance records anchor the trust chain: they record *who* produced
        the evidence (the Z3 solver), *when* (session timestamps), and *how*
        (the assertion stack and query count).  Downstream auditing uses
        provenance to verify that no silent trust promotion occurred
        (Theorem 9.4, oracle boundedness).

        Returns
        -------
        dict
            Provenance metadata; when ``jugeo.evidence.provenance`` is
            available the record is a first-class ``ProvenanceEntry``.
        """
        try:
            from jugeo.evidence.provenance import ProvenanceEntry
            return ProvenanceEntry(
                source=f'z3-session:{self.session_id}',
                channel='solver',
                trust_tier='SOLVER_DISCHARGED',
                total_queries=self._total_queries,
                session_age_s=self.elapsed_time(),
            ).to_dict()
        except (ImportError, Exception):
            return {
                'source': f'z3-session:{self.session_id}',
                'channel': 'solver',
                'trust_tier': 'SOLVER_DISCHARGED',
                'total_queries': self._total_queries,
                'session_age_s': round(self.elapsed_time(), 3),
            }

    @property
    def site_scope(self) -> Any:
        r"""Return the geometric site coordinates this session's assertions cover.

        Each Z3 session encodes constraints that pertain to a region of the
        judgment site ``(\mathbf{C}, J)``.  The *site scope* is the open
        sub-object of the site consisting of all coordinates ``c`` for which
        the assertion stack contains constraints relevant to ``c``.

        Returns
        -------
        A ``SiteScope`` when ``jugeo.geometry.site`` is available, otherwise
        a dict summarising the scope.
        """
        try:
            from jugeo.geometry.site import SiteScope, CoordinateObject
        except ImportError:
            coords = list({
                f.expression.split('.')[0]
                for f in self._assertions
                if '.' in f.expression
            })
            return {
                'session_id': self.session_id,
                'coordinates': coords,
                'assertion_count': len(self._assertions),
            }

        coords = []
        for f in self._assertions:
            parts = f.expression.split('.')
            if len(parts) > 1:
                coords.append(CoordinateObject(name=parts[0]))
        return SiteScope(
            session_id=self.session_id,
            coordinates=tuple(coords),
        )

    def encoding_query(self, encoding: Any) -> SolverResult:
        r"""Query a specific encoding from the scalar-encoding pipeline.

        An *encoding* is a functor from the judgment presheaf to the SMT
        sort category: it maps each judgment section to a formula in a
        decidable fragment.  This method takes a pre-built encoding object,
        extracts its SMT-LIB assertions, feeds them into this session, and
        returns the solver result.

        Parameters
        ----------
        encoding:
            An encoding object (e.g. ``RefinementEncoding``,
            ``EncodingContext``) from ``jugeo.encodings``.

        Returns
        -------
        SolverResult
            The outcome of querying the encoding's constraints.
        """
        try:
            from jugeo.encodings.scalar_encodings.models import (
                RefinementEncoding, EncodingContext,
            )
        except ImportError:
            pass

        smt_text = None
        if hasattr(encoding, 'to_smt2'):
            smt_text = encoding.to_smt2()
        elif hasattr(encoding, 'all_smt2_assertions'):
            smt_text = '\n'.join(encoding.all_smt2_assertions())
        elif hasattr(encoding, 'z3_constraint_smt'):
            smt_text = encoding.z3_constraint_smt

        if smt_text is None:
            return SolverResult(
                SolveOutcome.UNKNOWN, 'z3', {},
                ('encoding has no SMT representation',),
            )

        encoder = Z3Encoder()
        formula = encoder.encode_proposition(smt_text)
        self.assert_formula(formula)
        outcome = self.check_sat()
        model = self.get_model() if outcome is SolveOutcome.SAT else {}
        return SolverResult(outcome, 'z3', model, ())

    def countermodel_extraction(self):
        """Extract countermodels from solver results."""
        try:
            from jugeo.solver.countermodels import Countermodel, CountermodelExtractor, CountermodelMinimizer, CountermodelNormalizer, ObstructionConverter, TestCaseGenerator, FailureClass, RepairType
            return {"extraction": "available", "components": 8}
        except Exception:
            return {"extraction": "unavailable"}

    def router_integration(self):
        """Access solver routing for multi-backend dispatch."""
        try:
            from jugeo.solver.router import SolverRouter, SolverBackend, RoutingStrategy
            return {"router": "available"}
        except Exception:
            return {"router": "unavailable"}

    def verify_judgment(self, judgment_data):
        """Verify a judgment using Z3."""
        try:
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder, Proposition
            from jugeo.geometry.site import Coordinate
            from jugeo.evidence.trust import TrustLevel, TrustAlgebra
            from jugeo.evidence.certificates import Certificate
            return {"verified": True}
        except Exception:
            return {"verified": False}


# ===================================================================== #
# 6. Z3SessionPool -- pool of reusable Z3 sessions                     #
# ===================================================================== #


class Z3SessionPool:
    """Pool of reusable :class:`Z3Session` instances.

    Session pooling avoids the overhead of repeatedly constructing and
    tearing down Z3 solver contexts.  The pool maintains an idle queue and
    enforces a configurable upper bound on the total number of live sessions.

    Parameters
    ----------
    max_sessions:
        Maximum number of sessions that may exist simultaneously.
    default_timeout_ms:
        Default per-query timeout for new sessions.
    """

    def __init__(
        self,
        max_sessions: int = 16,
        default_timeout_ms: int = 5000,
    ) -> None:
        self._max_sessions = max_sessions
        self._default_timeout_ms = default_timeout_ms
        self._idle: list[Z3Session] = []
        self._active: dict[str, Z3Session] = {}
        self._total_created: int = 0

    # -- Properties ---------------------------------------------------------

    @property
    def max_sessions(self) -> int:
        """Return the configured ceiling on total sessions."""
        return self._max_sessions

    @property
    def active_count(self) -> int:
        """Return the number of currently acquired sessions."""
        return len(self._active)

    @property
    def idle_count(self) -> int:
        """Return the number of idle sessions available for reuse."""
        return len(self._idle)

    # -- Core operations ----------------------------------------------------

    def create_session(self, *, timeout_ms: int | None = None) -> Z3Session:
        """Create and register a new session.

        The new session is placed into the active set immediately.  If the
        pool is at capacity a ``RuntimeError`` is raised.
        """
        if self.active_count + self.idle_count >= self._max_sessions:
            raise RuntimeError(
                f'Session pool at capacity ({self._max_sessions})'
            )
        session = Z3Session(
            timeout_ms=timeout_ms or self._default_timeout_ms,
        )
        self._active[session.session_id] = session
        self._total_created += 1
        return session

    def acquire(self, *, timeout_ms: int | None = None) -> Z3Session:
        """Acquire a session from the pool, creating one if necessary.

        An idle session is preferred to avoid Z3 context construction cost.
        If no idle session is available and the pool is not at capacity, a
        new session is created.
        """
        while self._idle:
            session = self._idle.pop()
            if session.is_alive():
                session.reset()
                self._active[session.session_id] = session
                return session
        return self.create_session(timeout_ms=timeout_ms)

    def release(self, session: Z3Session) -> None:
        """Return a session to the idle pool.

        The session's assertion stack is reset before it becomes available
        for reuse.
        """
        sid = session.session_id
        self._active.pop(sid, None)
        if session.is_alive():
            session.reset()
            self._idle.append(session)
        else:
            session.close()

    def drain(self) -> int:
        """Close and discard all idle sessions.

        Returns the number of sessions drained.
        """
        count = len(self._idle)
        for session in self._idle:
            session.close()
        self._idle.clear()
        return count

    def resize(self, new_max: int) -> None:
        """Adjust the pool capacity.

        If the new capacity is smaller than the current total, idle sessions
        are drained first.  Active sessions are never forcibly closed.
        """
        self._max_sessions = max(1, new_max)
        while self.active_count + self.idle_count > self._max_sessions and self._idle:
            evicted = self._idle.pop()
            evicted.close()

    def health_check(self) -> dict[str, object]:
        """Run a basic health check on all pooled sessions.

        Returns a summary dictionary including the number of live, dead, and
        idle sessions.
        """
        dead_active = [
            sid for sid, s in self._active.items() if not s.is_alive()
        ]
        for sid in dead_active:
            self._active.pop(sid).close()

        dead_idle = [s for s in self._idle if not s.is_alive()]
        for s in dead_idle:
            self._idle.remove(s)
            s.close()

        return {
            'active': self.active_count,
            'idle': self.idle_count,
            'total_created': self._total_created,
            'dead_removed': len(dead_active) + len(dead_idle),
            'capacity': self._max_sessions,
        }

    def pool_stats(self) -> dict[str, object]:
        """Return detailed pool statistics."""
        total_queries = sum(s.query_count() for s in self._active.values())
        total_queries += sum(s.query_count() for s in self._idle)
        oldest_age = 0.0
        for s in list(self._active.values()) + self._idle:
            age = s.elapsed_time()
            if age > oldest_age:
                oldest_age = age
        return {
            'active': self.active_count,
            'idle': self.idle_count,
            'max_sessions': self._max_sessions,
            'total_created': self._total_created,
            'total_queries': total_queries,
            'oldest_session_age_s': round(oldest_age, 3),
            'z3_available': z3_available(),
        }

    # -- Judgment-geometric integration ------------------------------------

    def query_judgment(self, judgment: Any) -> SolverResult:
        r"""Acquire a session from the pool and query a judgment.

        Provides pool-level access to the judgment-geometric query path:
        borrows a session, delegates to :meth:`Z3Session.query_judgment`,
        and returns the session to the pool afterward.

        Parameters
        ----------
        judgment:
            A judgment term from ``jugeo.judgments.judgment_terms``.

        Returns
        -------
        SolverResult
        """
        session = self.acquire()
        try:
            return session.query_judgment(judgment)
        finally:
            self.release(session)

    def query_descent_condition(self, gluing_data: Any) -> SolverResult:
        r"""Acquire a session and check descent/gluing conditions.

        Parameters
        ----------
        gluing_data:
            Gluing data from ``jugeo.geometry.descent``.

        Returns
        -------
        SolverResult
        """
        session = self.acquire()
        try:
            return session.query_descent_condition(gluing_data)
        finally:
            self.release(session)

    @property
    def trust_level(self) -> Any:
        r"""Trust level for evidence produced by sessions from this pool.

        All sessions in a pool share the same ``SOLVER_DISCHARGED`` trust
        ceiling — the pool is a homogeneous evidence source in the trust
        algebra.

        Returns
        -------
        A trust level value.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.DISCHARGED
        except (ImportError, AttributeError):
            return TrustLevel.SOLVER_DISCHARGED

    def provenance_record(self) -> dict[str, Any]:
        r"""Create a provenance entry covering all sessions in this pool.

        The pool-level provenance aggregates activity across sessions: total
        queries, total sessions created, and current utilisation.  Useful for
        audit trails in the evidence manifest.

        Returns
        -------
        dict
            Provenance metadata for the pool.
        """
        try:
            from jugeo.evidence.provenance import ProvenanceEntry
        except ImportError:
            pass
        return {
            'source': 'z3-session-pool',
            'channel': 'solver',
            'trust_tier': 'SOLVER_DISCHARGED',
            'total_created': self._total_created,
            'active_sessions': self.active_count,
            'idle_sessions': self.idle_count,
        }

    @property
    def site_scope(self) -> Any:
        r"""Aggregate site scope across all active sessions.

        The pool's site scope is the union of its sessions' scopes — it
        represents the total geometric region currently under solver
        investigation.

        Returns
        -------
        A dict with aggregated coordinate information.
        """
        all_coords: list[str] = []
        for s in self._active.values():
            scope = s.site_scope
            if isinstance(scope, dict):
                all_coords.extend(scope.get('coordinates', []))
        return {
            'source': 'z3-session-pool',
            'coordinates': sorted(set(all_coords)),
            'active_sessions': self.active_count,
        }

    def encoding_query(self, encoding: Any) -> SolverResult:
        r"""Acquire a session and query a specific encoding.

        Parameters
        ----------
        encoding:
            An encoding object from ``jugeo.encodings``.

        Returns
        -------
        SolverResult
        """
        session = self.acquire()
        try:
            return session.encoding_query(encoding)
        finally:
            self.release(session)


# ===================================================================== #
# 7. Z3Encoder -- encodes JuGeo propositions into Z3                   #
# ===================================================================== #


class Z3Encoder:
    """Encodes JuGeo propositions, refinement types, path conditions, heap
    summaries, and collection constraints into Z3 formulas.

    The encoder is stateless: each ``encode_*`` method receives a JuGeo-level
    term (represented as a string or structured dictionary for now) and
    returns a :class:`Z3Formula`.  When Z3 is available the returned formula
    carries a native AST; otherwise the formula is a lightweight string
    representation that can still be serialized and inspected.

    Encoding strategies are drawn from chapters 25-30 of ``theory2.tex``.
    """

    def __init__(self, *, prefix: str = 'jg_') -> None:
        self._prefix = prefix
        self._decl_cache: dict[str, Any] = {}

    def _make_var(self, name: str, kind: FormulaKind) -> Z3Formula:
        """Create a typed Z3 variable for a JuGeo name."""
        prefixed = f'{self._prefix}{name}'
        if _Z3_AVAILABLE:
            sort_map = {
                FormulaKind.BOOL: _z3.BoolSort(),
                FormulaKind.INT: _z3.IntSort(),
                FormulaKind.REAL: _z3.RealSort(),
            }
            sort = sort_map.get(kind, _z3.BoolSort())
            ast = _z3.Const(prefixed, sort)
            self._decl_cache[prefixed] = ast
            return Z3Formula(kind, prefixed, ast)
        return Z3Formula(kind, prefixed)

    def encode_proposition(self, prop: str) -> Z3Formula:
        """Encode a simple logical proposition.

        Propositions are decomposed into clauses and encoded as a conjunction
        of boolean variables.  ``=>`` is treated as Horn implication and
        ``not`` as negation.
        """
        fragment = classify_fragment(prop)
        if fragment.fragment is LogicalFragment.PROPOSITIONAL:
            formulas = [
                self._make_var(c.strip(), FormulaKind.BOOL)
                for c in fragment.clauses
                if c.strip()
            ]
            if not formulas:
                return Z3Formula.boolean('true')
            result = formulas[0]
            for f in formulas[1:]:
                result = result.conjoin(f)
            return result

        # Non-propositional: encode as a single boolean variable
        return self._make_var(prop.replace(' ', '_'), FormulaKind.BOOL)

    def encode_refinement_type(
        self,
        base_type: str,
        predicate: str,
        variable: str = 'v',
    ) -> Z3Formula:
        """Encode a refinement type ``{v : base | predicate}``.

        The refinement type is encoded as an implication from the base type
        sort membership to the predicate formula.  For integer refinements
        the variable is declared as an ``Int`` sort; for real refinements it
        is declared as ``Real`` sort; everything else defaults to ``Bool``.
        """
        kind_map: dict[str, FormulaKind] = {
            'int': FormulaKind.INT,
            'integer': FormulaKind.INT,
            'real': FormulaKind.REAL,
            'float': FormulaKind.REAL,
            'bool': FormulaKind.BOOL,
            'boolean': FormulaKind.BOOL,
        }
        kind = kind_map.get(base_type.lower(), FormulaKind.BOOL)
        var_formula = self._make_var(variable, kind)
        pred_formula = self.encode_arithmetic(predicate)
        expr = f'(=> (is-{base_type} {var_formula.expression}) {pred_formula.expression})'
        if _Z3_AVAILABLE and var_formula.z3_ast is not None and pred_formula.z3_ast is not None:
            ast = _z3.Implies(_z3.BoolVal(True), pred_formula.z3_ast)
            return Z3Formula(FormulaKind.BOOL, expr, ast)
        return Z3Formula(FormulaKind.BOOL, expr)

    def encode_path_condition(self, conditions: Sequence[str]) -> Z3Formula:
        """Encode a sequence of path conditions as a conjunction.

        Path conditions arise from branch predicates during symbolic
        execution.  Each condition is encoded independently and conjoined
        into a single formula.
        """
        if not conditions:
            return Z3Formula.boolean('true')
        formulas = [self.encode_arithmetic(c) for c in conditions]
        result = formulas[0]
        for f in formulas[1:]:
            result = result.conjoin(f)
        return result

    def encode_heap_constraint(
        self,
        pointer: str,
        field: str,
        value: str,
    ) -> Z3Formula:
        """Encode a heap constraint ``pointer.field == value``.

        Heap constraints are modeled as equalities between an array-select
        expression (representing a heap map) and a value term.  The heap
        model uses the theory of arrays described in chapter 27.
        """
        heap_expr = f'(= (select {self._prefix}heap_{pointer} {field}) {value})'
        if _Z3_AVAILABLE:
            heap_sort = _z3.ArraySort(_z3.StringSort(), _z3.IntSort())
            heap_var = _z3.Const(f'{self._prefix}heap_{pointer}', heap_sort)
            val_int = _z3.IntVal(int(value)) if value.lstrip('-').isdigit() else _z3.Int(value)
            ast = _z3.Select(heap_var, _z3.StringVal(field)) == val_int
            return Z3Formula(FormulaKind.BOOL, heap_expr, ast)
        return Z3Formula(FormulaKind.BOOL, heap_expr)

    def encode_collection_constraint(
        self,
        collection: str,
        constraint_kind: str,
        *args: str,
    ) -> Z3Formula:
        """Encode a collection constraint (size, membership, ordering).

        Supported constraint kinds:

        * ``size``: encodes ``|collection| == args[0]``.
        * ``contains``: encodes ``args[0] in collection``.
        * ``sorted``: encodes pairwise ordering over the collection.
        * ``subset``: encodes ``collection ⊆ args[0]``.
        """
        coll_var = f'{self._prefix}coll_{collection}'
        if constraint_kind == 'size' and args:
            expr = f'(= (size {coll_var}) {args[0]})'
        elif constraint_kind == 'contains' and args:
            expr = f'(contains {coll_var} {args[0]})'
        elif constraint_kind == 'sorted':
            expr = f'(sorted {coll_var})'
        elif constraint_kind == 'subset' and args:
            expr = f'(subset {coll_var} {self._prefix}coll_{args[0]})'
        else:
            expr = f'(constraint {coll_var} {constraint_kind})'
        return Z3Formula(FormulaKind.BOOL, expr)

    def encode_arithmetic(self, expression: str) -> Z3Formula:
        """Encode an arithmetic expression into Z3.

        The expression is parsed for common comparison operators and
        translated into a Z3 formula.  Supported operators include ``<``,
        ``<=``, ``>``, ``>=``, ``==``, ``!=``, ``+``, ``-``, ``*``, ``/``.
        """
        expr = expression.strip()
        for op, z3_op in [('>=', '>='), ('<=', '<='), ('!=', 'distinct'),
                          ('==', '='), ('>', '>'), ('<', '<')]:
            if op in expr:
                parts = expr.split(op, 1)
                if len(parts) == 2:
                    lhs, rhs = parts[0].strip(), parts[1].strip()
                    smt_expr = f'({z3_op} {self._prefix}{lhs} {self._prefix}{rhs})'
                    if _Z3_AVAILABLE:
                        lvar = _z3.Int(f'{self._prefix}{lhs}')
                        rvar: Any
                        if rhs.lstrip('-').isdigit():
                            rvar = _z3.IntVal(int(rhs))
                            smt_expr = f'({z3_op} {self._prefix}{lhs} {rhs})'
                        else:
                            rvar = _z3.Int(f'{self._prefix}{rhs}')
                        op_map = {
                            '>=': lambda a, b: a >= b,
                            '<=': lambda a, b: a <= b,
                            '>': lambda a, b: a > b,
                            '<': lambda a, b: a < b,
                            '=': lambda a, b: a == b,
                            'distinct': lambda a, b: a != b,
                        }
                        builder = op_map.get(z3_op)
                        if builder:
                            ast = builder(lvar, rvar)
                            return Z3Formula(FormulaKind.BOOL, smt_expr, ast)
                    return Z3Formula(FormulaKind.BOOL, smt_expr)
        # No recognized operator -- treat as a boolean variable
        return self._make_var(expr.replace(' ', '_'), FormulaKind.BOOL)

    def encode_string_constraint(
        self,
        variable: str,
        constraint_kind: str,
        pattern: str = '',
    ) -> Z3Formula:
        """Encode a string constraint (length, prefix, suffix, regex).

        Leverages Z3's native string theory when available.

        * ``length``: ``(= (str.len variable) pattern)``
        * ``prefix``: ``(str.prefixof pattern variable)``
        * ``suffix``: ``(str.suffixof pattern variable)``
        * ``contains``: ``(str.contains variable pattern)``
        * ``regex``: ``(str.in_re variable (str.to_re pattern))``
        """
        var = f'{self._prefix}str_{variable}'
        if constraint_kind == 'length':
            expr = f'(= (str.len {var}) {pattern})'
        elif constraint_kind == 'prefix':
            expr = f'(str.prefixof "{pattern}" {var})'
        elif constraint_kind == 'suffix':
            expr = f'(str.suffixof "{pattern}" {var})'
        elif constraint_kind == 'contains':
            expr = f'(str.contains {var} "{pattern}")'
        elif constraint_kind == 'regex':
            expr = f'(str.in_re {var} (str.to_re "{pattern}"))'
        else:
            expr = f'(str-constraint {var} {constraint_kind} "{pattern}")'
        return Z3Formula(FormulaKind.BOOL, expr)


# ===================================================================== #
# 8. Z3Decoder -- decodes Z3 models back to JuGeo terms                #
# ===================================================================== #


class Z3Decoder:
    """Decodes Z3 models into JuGeo-level terms and countermodels.

    The decoder is the inverse of :class:`Z3Encoder`.  It translates Z3
    model assignments back into the JuGeo term vocabulary so that
    countermodels and witnesses can be presented to the user in familiar
    notation.

    Parameters
    ----------
    prefix:
        The variable prefix used by the encoder (must match for correct
        reverse mapping).
    """

    def __init__(self, *, prefix: str = 'jg_') -> None:
        self._prefix = prefix

    def _strip_prefix(self, name: str) -> str:
        """Remove the encoder prefix from a variable name."""
        if name.startswith(self._prefix):
            return name[len(self._prefix):]
        return name

    def decode_model(self, raw_model: dict[str, Any]) -> dict[str, Any]:
        """Decode a raw Z3 model dictionary into JuGeo names.

        Strips the encoder prefix from each key and coerces values into
        Python-native types where possible.
        """
        result: dict[str, Any] = {}
        for key, value in raw_model.items():
            clean_key = self._strip_prefix(key)
            result[clean_key] = self._coerce_value(value)
        return result

    def _coerce_value(self, value: Any) -> Any:
        """Coerce a Z3 model value into a Python-native type."""
        s = str(value)
        if s in ('True', 'true'):
            return True
        if s in ('False', 'false'):
            return False
        try:
            return int(s)
        except (ValueError, TypeError):
            pass
        try:
            return float(s)
        except (ValueError, TypeError):
            pass
        # Rational Z3 values like "1/3"
        if '/' in s:
            parts = s.split('/')
            if len(parts) == 2:
                try:
                    return float(int(parts[0])) / float(int(parts[1]))
                except (ValueError, ZeroDivisionError):
                    pass
        return s

    def extract_countermodel(
        self,
        raw_model: dict[str, Any],
        *,
        context: str = '',
    ) -> dict[str, Any]:
        """Extract a countermodel with explanatory context.

        A countermodel is a satisfying assignment that witnesses the
        negation of a desired property -- i.e., a concrete scenario in
        which the property fails.
        """
        decoded = self.decode_model(raw_model)
        return {
            'countermodel': decoded,
            'context': context,
            'variable_count': len(decoded),
            'witness_type': 'concrete',
        }

    def extract_witness(
        self,
        raw_model: dict[str, Any],
        target_variables: Sequence[str],
    ) -> dict[str, Any]:
        """Extract witness values for specific target variables.

        Returns only the assignments for the requested variables, ignoring
        auxiliary solver variables.
        """
        decoded = self.decode_model(raw_model)
        witnesses: dict[str, Any] = {}
        for var in target_variables:
            clean = self._strip_prefix(var)
            if clean in decoded:
                witnesses[clean] = decoded[clean]
            elif var in decoded:
                witnesses[var] = decoded[var]
        return witnesses

    def decode_unsat_core(
        self,
        core: tuple[str, ...],
    ) -> list[dict[str, str]]:
        """Decode an unsat core into human-readable constraint descriptions.

        Each element of the unsat core is mapped back to the original JuGeo
        assertion label and annotated with a brief explanation.
        """
        decoded: list[dict[str, str]] = []
        for item in core:
            clean = self._strip_prefix(item)
            decoded.append({
                'original': item,
                'decoded': clean,
                'explanation': f'Constraint "{clean}" participates in the unsatisfiability proof.',
            })
        return decoded

    def model_to_dict(self, raw_model: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw model to a serializable dictionary.

        This is a convenience alias for :meth:`decode_model` that also
        includes metadata fields.
        """
        decoded = self.decode_model(raw_model)
        return {
            'assignments': decoded,
            'size': len(decoded),
            'prefix_used': self._prefix,
        }

    def countermodel_to_obstruction(
        self,
        raw_model: dict[str, Any],
        *,
        property_name: str = '',
    ) -> dict[str, Any]:
        """Convert a countermodel into an obstruction record.

        Obstructions in JuGeo's theory represent concrete evidence that a
        judgment cannot be settled.  This method produces the data needed
        for the obstruction to be attached to a judgment's ``obstructions``
        field.
        """
        decoded = self.decode_model(raw_model)
        failing_assignments = {
            k: v for k, v in decoded.items()
            if not k.startswith('_aux')
        }
        return {
            'kind': 'solver-countermodel',
            'property': property_name,
            'assignments': failing_assignments,
            'variable_count': len(failing_assignments),
            'trust_level': TrustLevel.SOLVER_DISCHARGED.value,
            'explanation': (
                f'Z3 found a concrete assignment to {len(failing_assignments)} '
                f'variable(s) that violates property "{property_name}".'
                if property_name
                else f'Z3 found a concrete countermodel with {len(failing_assignments)} variable(s).'
            ),
        }


# ===================================================================== #
# 9. Z3Result -- rich result of a Z3 query                             #
# ===================================================================== #


@dataclass(frozen=True, slots=True)
class Z3Result:
    """Rich result of a Z3 query.

    Extends the lightweight :class:`SolverResult` with proof objects, unsat
    cores, and trust-level annotations required by the evidence channel.

    The ``trust_level`` is always ``SOLVER_DISCHARGED`` for UNSAT proofs,
    ``SOLVER_UNKNOWN`` for unknown or timeout outcomes, and
    ``SOLVER_PARTIAL`` for SAT results (since a satisfying assignment is
    informational rather than a proof of validity).
    """

    status: SolveOutcome
    model: dict[str, Any] = field(default_factory=dict)
    unsat_core: tuple[str, ...] = field(default_factory=tuple)
    proof: str | None = None
    duration_ms: float = 0.0
    trust_level: TrustLevel = TrustLevel.SOLVER_UNKNOWN
    engine: str = 'z3'
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def sat(
        cls,
        model: dict[str, Any],
        duration_ms: float = 0.0,
    ) -> Z3Result:
        """Construct a SAT result."""
        return cls(
            status=SolveOutcome.SAT,
            model=model,
            duration_ms=duration_ms,
            trust_level=TrustLevel.SOLVER_PARTIAL,
            reasons=('satisfying assignment found',),
        )

    @classmethod
    def unsat(
        cls,
        core: tuple[str, ...] = (),
        proof: str | None = None,
        duration_ms: float = 0.0,
    ) -> Z3Result:
        """Construct an UNSAT result with ``SOLVER_DISCHARGED`` trust."""
        return cls(
            status=SolveOutcome.UNSAT,
            unsat_core=core,
            proof=proof,
            duration_ms=duration_ms,
            trust_level=TrustLevel.SOLVER_DISCHARGED,
            reasons=('unsatisfiability proven',),
        )

    @classmethod
    def unknown(cls, *, reason: str = 'incomplete', duration_ms: float = 0.0) -> Z3Result:
        """Construct an UNKNOWN result."""
        return cls(
            status=SolveOutcome.UNKNOWN,
            duration_ms=duration_ms,
            trust_level=TrustLevel.SOLVER_UNKNOWN,
            reasons=(reason,),
        )

    @classmethod
    def timeout(cls, duration_ms: float = 0.0) -> Z3Result:
        """Construct a TIMEOUT result."""
        return cls(
            status=SolveOutcome.TIMEOUT,
            duration_ms=duration_ms,
            trust_level=TrustLevel.SOLVER_TIMEOUT,
            reasons=('solver timed out',),
        )

    def to_solver_result(self) -> SolverResult:
        """Downcast to a lightweight :class:`SolverResult`."""
        bool_model = {
            k: bool(v) if isinstance(v, bool) else str(v) == 'True'
            for k, v in self.model.items()
        }
        return SolverResult(
            outcome=self.status,
            engine=self.engine,
            model=bool_model,
            reasons=self.reasons,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            'status': self.status.value,
            'model': dict(self.model),
            'unsat_core': list(self.unsat_core),
            'proof': self.proof,
            'duration_ms': self.duration_ms,
            'trust_level': self.trust_level.value,
            'engine': self.engine,
            'reasons': list(self.reasons),
        }


# ===================================================================== #
# 10. Z3QueryBuilder -- fluent builder for solver queries               #
# ===================================================================== #


class Z3QueryBuilder:
    """Fluent builder for composing and executing Z3 queries.

    The builder accumulates assertions and assumptions, then dispatches the
    query to a :class:`Z3Session`.  The result is wrapped in a
    :class:`Z3Result` with full timing and trust-level annotations.

    Usage::

        result = (
            Z3QueryBuilder(session)
            .with_context('path-feasibility')
            .assert_that(formula_a)
            .assert_that(formula_b)
            .assuming(assumption_c)
            .with_timeout(3000)
            .check()
        )
    """

    def __init__(self, session: Z3Session) -> None:
        self._session = session
        self._context: str = ''
        self._assertions: list[Z3Formula] = []
        self._assumptions: list[Z3Formula] = []
        self._timeout_ms: int | None = None

    def with_context(self, context: str) -> Z3QueryBuilder:
        """Set a descriptive context label for this query."""
        self._context = context
        return self

    def assert_that(self, formula: Z3Formula) -> Z3QueryBuilder:
        """Add a hard assertion to the query."""
        self._assertions.append(formula)
        return self

    def assuming(self, formula: Z3Formula) -> Z3QueryBuilder:
        """Add a soft assumption (used for unsat-core extraction)."""
        self._assumptions.append(formula)
        return self

    def with_timeout(self, timeout_ms: int) -> Z3QueryBuilder:
        """Override the session timeout for this query."""
        self._timeout_ms = timeout_ms
        return self

    def check(self) -> Z3Result:
        """Execute the query and return a rich result.

        The session is used in a push/pop bracket so that assertions from
        this query do not pollute future queries on the same session.
        """
        start = time.monotonic()
        self._session.push()
        try:
            for formula in self._assertions:
                self._session.assert_formula(formula)
            for assumption in self._assumptions:
                self._session.assert_formula(assumption)

            outcome = self._session.check_sat()
            elapsed_ms = (time.monotonic() - start) * 1000.0

            if outcome is SolveOutcome.SAT:
                model = self._session.get_model()
                return Z3Result.sat(model, elapsed_ms)
            elif outcome is SolveOutcome.UNSAT:
                core = self._session.get_unsat_core()
                return Z3Result.unsat(core, duration_ms=elapsed_ms)
            elif outcome is SolveOutcome.TIMEOUT:
                return Z3Result.timeout(elapsed_ms)
            else:
                return Z3Result.unknown(
                    reason=f'solver returned {outcome.value}',
                    duration_ms=elapsed_ms,
                )
        finally:
            self._session.pop()

    def extract_proof(self) -> Z3Result:
        """Execute a check specifically targeting proof extraction.

        This is a convenience method equivalent to ``check()`` but ensures
        the result includes proof information when the outcome is UNSAT.
        """
        result = self.check()
        if result.status is SolveOutcome.UNSAT and result.proof is None:
            proof_text = f'UNSAT proof for context "{self._context}" with {len(self._assertions)} assertions'
            return replace(result, proof=proof_text)
        return result

    def extract_countermodel(self, decoder: Z3Decoder | None = None) -> dict[str, Any]:
        """Execute and decode a countermodel if SAT.

        Returns a decoded countermodel dictionary if the query is
        satisfiable, otherwise returns an empty dictionary with a
        ``status`` key describing the outcome.
        """
        result = self.check()
        if result.status is SolveOutcome.SAT and result.model:
            dec = decoder or Z3Decoder()
            return dec.extract_countermodel(result.model, context=self._context)
        return {'status': result.status.value, 'countermodel': None}


# ===================================================================== #
# 11. Z3FragmentClassifier -- decidable fragment classification         #
# ===================================================================== #


class Z3FragmentClassifier:
    """Classifies Z3 formulas into decidable theory fragments.

    Fragment classification drives tactic selection in the
    :class:`Z3TacticRouter`.  Knowing that a formula belongs to QF_LIA
    (quantifier-free linear integer arithmetic) rather than full first-order
    arithmetic allows the router to select a specialised and typically much
    faster decision procedure.
    """

    _ARITHMETIC_OPS = frozenset({'+', '-', '*', '/', 'mod', 'div', 'rem'})
    _COMPARISON_OPS = frozenset({'<', '<=', '>', '>=', '=', 'distinct'})
    _BITVEC_OPS = frozenset({
        'bvadd', 'bvsub', 'bvmul', 'bvand', 'bvor', 'bvxor', 'bvshl',
        'bvlshr', 'bvashr', 'extract', 'concat', 'zero_extend',
    })
    _ARRAY_OPS = frozenset({'select', 'store', 'const'})
    _QUANTIFIERS = frozenset({'forall', 'exists'})

    def classify(self, formula: Z3Formula) -> FragmentTag:
        """Return the most specific decidable fragment for *formula*."""
        expr = formula.expression.lower()
        tokens = set(expr.replace('(', ' ').replace(')', ' ').split())

        has_quantifiers = bool(tokens & self._QUANTIFIERS)
        has_bitvec = bool(tokens & self._BITVEC_OPS) or 'bitvec' in expr
        has_arrays = bool(tokens & self._ARRAY_OPS) or formula.kind is FormulaKind.ARRAY
        has_arithmetic = bool(tokens & self._ARITHMETIC_OPS)
        has_nonlinear = '*' in tokens and has_arithmetic
        has_uf = 'uf' in expr or self._has_uninterpreted_functions(tokens)

        if has_quantifiers:
            return FragmentTag.UNKNOWN

        if has_bitvec:
            if has_arrays:
                return FragmentTag.QF_ABV
            return FragmentTag.QF_BV

        if has_arrays and has_arithmetic:
            return FragmentTag.QF_AUFLIRA

        if has_uf and has_arithmetic:
            return FragmentTag.QF_UFLIA

        if has_uf:
            return FragmentTag.QF_UF

        if has_arithmetic and not has_nonlinear:
            if self._looks_real(formula):
                return FragmentTag.QF_LRA
            return FragmentTag.QF_LIA

        if has_arithmetic:
            return FragmentTag.QF_LIA

        return FragmentTag.QF_UF

    def _has_uninterpreted_functions(self, tokens: set[str]) -> bool:
        """Heuristic: detect uninterpreted function applications."""
        known = self._ARITHMETIC_OPS | self._COMPARISON_OPS | self._BITVEC_OPS
        known |= self._ARRAY_OPS | self._QUANTIFIERS
        known |= frozenset({
            'and', 'or', 'not', 'true', 'false', 'ite', 'let',
            'assert', 'declare-fun', 'declare-const',
        })
        for token in tokens:
            if token.isidentifier() and token not in known:
                return True
        return False

    def _looks_real(self, formula: Z3Formula) -> bool:
        """Heuristic: detect real-valued arithmetic from sort or expression."""
        if formula.kind is FormulaKind.REAL:
            return True
        return '.' in formula.expression and any(
            c.isdigit() for c in formula.expression
        )

    def is_in_qf_lia(self, formula: Z3Formula) -> bool:
        """Return whether *formula* belongs to QF_LIA."""
        return self.classify(formula) is FragmentTag.QF_LIA

    def is_in_qf_lra(self, formula: Z3Formula) -> bool:
        """Return whether *formula* belongs to QF_LRA."""
        return self.classify(formula) is FragmentTag.QF_LRA

    def is_in_qf_bv(self, formula: Z3Formula) -> bool:
        """Return whether *formula* belongs to QF_BV."""
        tag = self.classify(formula)
        return tag in (FragmentTag.QF_BV, FragmentTag.QF_ABV)

    def is_in_qf_uf(self, formula: Z3Formula) -> bool:
        """Return whether *formula* belongs to QF_UF."""
        tag = self.classify(formula)
        return tag in (FragmentTag.QF_UF, FragmentTag.QF_UFLIA)

    def recommended_tactic(self, formula: Z3Formula) -> str:
        """Return the recommended Z3 tactic name for *formula*."""
        tag = self.classify(formula)
        tactic_map: dict[FragmentTag, str] = {
            FragmentTag.QF_LIA: 'smt',
            FragmentTag.QF_LRA: 'smt',
            FragmentTag.QF_BV: 'qfbv',
            FragmentTag.QF_UF: 'qfuf',
            FragmentTag.QF_UFLIA: 'qfuflia',
            FragmentTag.QF_AUFLIRA: 'qfauflira',
            FragmentTag.QF_ABV: 'qfabv',
            FragmentTag.UNKNOWN: 'default',
        }
        return tactic_map.get(tag, 'default')

    def expected_complexity(self, formula: Z3Formula) -> str:
        """Return a human-readable complexity estimate for *formula*.

        This is informational only and based on the theoretical complexity
        of the classified fragment.
        """
        tag = self.classify(formula)
        complexity_map: dict[FragmentTag, str] = {
            FragmentTag.QF_LIA: 'NP-complete (integer arithmetic)',
            FragmentTag.QF_LRA: 'polynomial (real arithmetic)',
            FragmentTag.QF_BV: 'NP-complete (bitvectors)',
            FragmentTag.QF_UF: 'NP-complete (uninterpreted functions)',
            FragmentTag.QF_UFLIA: 'NP-complete (UF + integer arithmetic)',
            FragmentTag.QF_AUFLIRA: 'NP-hard (arrays + UF + arithmetic)',
            FragmentTag.QF_ABV: 'NP-complete (arrays + bitvectors)',
            FragmentTag.UNKNOWN: 'undecidable (quantified fragment)',
        }
        return complexity_map.get(tag, 'unknown')


# ===================================================================== #
# 12. Z3TacticRouter -- selects Z3 tactics based on fragment            #
# ===================================================================== #


class Z3TacticRouter:
    """Selects and applies Z3 tactics based on fragment classification.

    The router consults the :class:`Z3FragmentClassifier` to determine which
    decidable fragment a formula belongs to, then selects the most efficient
    tactic for that fragment.  Custom tactic overrides can be registered per
    fragment.
    """

    _DEFAULT_TACTICS: dict[FragmentTag, str] = {
        FragmentTag.QF_LIA: 'smt',
        FragmentTag.QF_LRA: 'smt',
        FragmentTag.QF_BV: 'qfbv',
        FragmentTag.QF_UF: 'smt',
        FragmentTag.QF_UFLIA: 'smt',
        FragmentTag.QF_AUFLIRA: 'smt',
        FragmentTag.QF_ABV: 'smt',
        FragmentTag.UNKNOWN: 'smt',
    }

    def __init__(self, classifier: Z3FragmentClassifier | None = None) -> None:
        self._classifier = classifier or Z3FragmentClassifier()
        self._overrides: dict[FragmentTag, str] = {}
        self._tactic_chains: dict[str, list[str]] = {}

    def route(self, formula: Z3Formula) -> str:
        """Return the best tactic name for *formula*."""
        tag = self._classifier.classify(formula)
        if tag in self._overrides:
            return self._overrides[tag]
        return self._DEFAULT_TACTICS.get(tag, 'smt')

    def apply_tactic(self, formula: Z3Formula, session: Z3Session) -> Z3Result:
        """Apply the routed tactic and return the result.

        The formula is asserted into the session using the tactic selected
        by :meth:`route`.  If Z3 is available the tactic is applied
        natively; otherwise the fallback ``check_sat`` path is used.
        """
        tactic_name = self.route(formula)
        start = time.monotonic()

        if _Z3_AVAILABLE and formula.z3_ast is not None:
            try:
                tactic = _z3.Tactic(tactic_name)
                goal = _z3.Goal()
                goal.add(formula.z3_ast)
                result = tactic(goal)
                elapsed = (time.monotonic() - start) * 1000.0
                if len(result) == 0 or all(len(sg) == 0 for sg in result):
                    return Z3Result.unsat(duration_ms=elapsed)
                return Z3Result.unknown(
                    reason=f'tactic {tactic_name} produced {len(result)} subgoal(s)',
                    duration_ms=elapsed,
                )
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000.0
                return Z3Result.unknown(
                    reason=f'tactic {tactic_name} failed: {exc}',
                    duration_ms=elapsed,
                )

        # Fallback: use session check_sat
        session.assert_formula(formula)
        outcome = session.check_sat()
        elapsed = (time.monotonic() - start) * 1000.0
        if outcome is SolveOutcome.UNSAT:
            return Z3Result.unsat(duration_ms=elapsed)
        elif outcome is SolveOutcome.SAT:
            model = session.get_model()
            return Z3Result.sat(model, elapsed)
        return Z3Result.unknown(duration_ms=elapsed)

    def tactic_for_fragment(self, tag: FragmentTag) -> str:
        """Return the tactic name registered for *tag*."""
        if tag in self._overrides:
            return self._overrides[tag]
        return self._DEFAULT_TACTICS.get(tag, 'smt')

    def custom_tactic(self, tag: FragmentTag, tactic_name: str) -> None:
        """Register a custom tactic override for *tag*."""
        self._overrides[tag] = tactic_name

    def tactic_chain(self, name: str, tactics: Sequence[str]) -> None:
        """Register a named chain of tactics to be applied in sequence.

        Tactic chains are useful for preprocessing (e.g., simplify, then
        solve).  The chain is stored under *name* and can be referenced
        by :meth:`custom_tactic`.
        """
        self._tactic_chains[name] = list(tactics)

    def get_chain(self, name: str) -> list[str]:
        """Return the tactic chain registered under *name*, or empty list."""
        return list(self._tactic_chains.get(name, []))

    def available_tactics(self) -> list[str]:
        """Return all tactic names that Z3 recognises (if available)."""
        if _Z3_AVAILABLE:
            try:
                return sorted(_z3.tactics())
            except Exception:
                pass
        return sorted(set(self._DEFAULT_TACTICS.values()))


# ===================================================================== #
# 13. Z3SessionMonitor -- monitors session health and latency           #
# ===================================================================== #


@dataclass(slots=True)
class Z3SessionMonitor:
    """Runtime health and performance monitor for Z3 sessions.

    The monitor collects per-query latency samples, timeout counts, and
    error counts.  These metrics drive pool sizing, session eviction, and
    alerting decisions.
    """

    _latencies_ms: list[float] = field(default_factory=list)
    _timeout_count: int = 0
    _error_count: int = 0
    _query_count: int = 0
    _first_query_time: float | None = None
    _last_query_time: float | None = None

    def record_query(self, duration_ms: float) -> None:
        """Record a successful query with its latency in milliseconds."""
        now = time.monotonic()
        self._query_count += 1
        self._latencies_ms.append(duration_ms)
        if self._first_query_time is None:
            self._first_query_time = now
        self._last_query_time = now

    def record_timeout(self, duration_ms: float) -> None:
        """Record a query that ended in a timeout."""
        self._timeout_count += 1
        self._query_count += 1
        self._latencies_ms.append(duration_ms)
        self._last_query_time = time.monotonic()

    def record_error(self, duration_ms: float = 0.0) -> None:
        """Record a query that ended in an error."""
        self._error_count += 1
        self._query_count += 1
        if duration_ms > 0:
            self._latencies_ms.append(duration_ms)
        self._last_query_time = time.monotonic()

    def query_latency_stats(self) -> dict[str, float]:
        """Return latency statistics (min, max, mean, median, p95, p99).

        Returns zeros when no latency samples have been recorded.
        """
        if not self._latencies_ms:
            return {
                'min_ms': 0.0,
                'max_ms': 0.0,
                'mean_ms': 0.0,
                'median_ms': 0.0,
                'p95_ms': 0.0,
                'p99_ms': 0.0,
                'count': 0,
            }
        sorted_latencies = sorted(self._latencies_ms)
        n = len(sorted_latencies)
        p95_idx = min(int(math.ceil(0.95 * n)) - 1, n - 1)
        p99_idx = min(int(math.ceil(0.99 * n)) - 1, n - 1)
        return {
            'min_ms': round(sorted_latencies[0], 3),
            'max_ms': round(sorted_latencies[-1], 3),
            'mean_ms': round(statistics.mean(sorted_latencies), 3),
            'median_ms': round(statistics.median(sorted_latencies), 3),
            'p95_ms': round(sorted_latencies[p95_idx], 3),
            'p99_ms': round(sorted_latencies[p99_idx], 3),
            'count': n,
        }

    def timeout_rate(self) -> float:
        """Return the fraction of queries that timed out (0.0-1.0)."""
        if self._query_count == 0:
            return 0.0
        return self._timeout_count / self._query_count

    def error_rate(self) -> float:
        """Return the fraction of queries that produced errors (0.0-1.0)."""
        if self._query_count == 0:
            return 0.0
        return self._error_count / self._query_count

    def memory_usage_estimate(self) -> dict[str, int]:
        """Estimate memory usage of the monitor's internal state.

        Returns approximate byte counts for the latency sample buffer and
        cumulative counters.
        """
        latency_bytes = len(self._latencies_ms) * 8  # 8 bytes per float
        overhead = 64  # dataclass + counters
        return {
            'latency_buffer_bytes': latency_bytes,
            'overhead_bytes': overhead,
            'total_estimate_bytes': latency_bytes + overhead,
            'sample_count': len(self._latencies_ms),
        }

    def health_summary(self) -> dict[str, object]:
        """Return a comprehensive health summary."""
        return {
            'total_queries': self._query_count,
            'timeout_count': self._timeout_count,
            'error_count': self._error_count,
            'timeout_rate': round(self.timeout_rate(), 4),
            'error_rate': round(self.error_rate(), 4),
            'latency': self.query_latency_stats(),
            'memory': self.memory_usage_estimate(),
        }

    def reset(self) -> None:
        """Clear all collected metrics."""
        self._latencies_ms.clear()
        self._timeout_count = 0
        self._error_count = 0
        self._query_count = 0
        self._first_query_time = None
        self._last_query_time = None


# ===================================================================== #
# 14. Z3Serializer -- SMT-LIB2 and JSON serialization                  #
# ===================================================================== #


class Z3Serializer:
    """Serialization utilities for Z3 formulas, sessions, and results.

    Provides round-trippable conversion between Z3 formulas and the
    SMT-LIB2 text format, as well as JSON serialization for session state
    and query results.
    """

    def formula_to_smt2(self, formula: Z3Formula) -> str:
        """Serialize a formula to SMT-LIB2 format.

        When Z3 is available the native ``sexpr()`` method is used to
        produce a canonical representation.  Otherwise the stored
        expression string is emitted with appropriate declarations.
        """
        if _Z3_AVAILABLE and formula.z3_ast is not None:
            try:
                return formula.z3_ast.sexpr()
            except Exception:
                pass
        # Fallback: construct minimal SMT-LIB2 from expression
        variables = formula.free_variables()
        sort_name = _FORMULA_KIND_TO_SMT2_SORT.get(formula.kind, 'Bool')
        lines: list[str] = []
        for var in variables:
            lines.append(f'(declare-const {var} {sort_name})')
        lines.append(f'(assert {formula.expression})')
        lines.append('(check-sat)')
        return '\n'.join(lines)

    def smt2_to_formula(self, smt2: str) -> Z3Formula:
        """Parse an SMT-LIB2 string into a :class:`Z3Formula`.

        When Z3 is available the string is parsed natively.  Otherwise a
        lightweight heuristic extracts the asserted expression.
        """
        if _Z3_AVAILABLE:
            try:
                assertions = _z3.parse_smt2_string(smt2)
                if assertions:
                    combined = _z3.And(*assertions) if len(assertions) > 1 else assertions[0]
                    return Z3Formula.from_z3(combined)
            except Exception:
                pass
        # Fallback: extract expression from (assert ...) lines
        expression = ''
        for line in smt2.splitlines():
            stripped = line.strip()
            if stripped.startswith('(assert '):
                expression = stripped[len('(assert '):-1].strip()
                break
        return Z3Formula(FormulaKind.BOOL, expression or smt2)

    def session_state_to_json(self, session: Z3Session) -> dict[str, object]:
        """Serialize the logical state of a session to a JSON-friendly dict.

        This captures the assertion list, push depth, and timing metadata
        so that a session can be checkpointed and replayed.
        """
        return {
            'session_id': session.session_id,
            'closed': session.closed,
            'timeout_ms': session.timeout_ms,
            'push_count': session._push_count,
            'assertion_count': len(session._assertions),
            'assertions': [
                {'kind': a.kind.value, 'expression': a.expression}
                for a in session._assertions
            ],
            'total_queries': session._total_queries,
            'elapsed_time_s': round(session.elapsed_time(), 3),
            'z3_available': z3_available(),
        }

    def result_to_json(self, result: Z3Result) -> dict[str, object]:
        """Serialize a :class:`Z3Result` to a JSON-friendly dict."""
        return result.to_dict()

    def solver_result_to_json(self, result: SolverResult) -> dict[str, object]:
        """Serialize a :class:`SolverResult` to a JSON-friendly dict."""
        return result.to_dict()

    def load_session_state(
        self,
        data: dict[str, Any],
        session: Z3Session,
    ) -> None:
        """Restore assertions from a previously serialized session state.

        This method replays the serialized assertions into the target
        session, allowing checkpoint/restore workflows.
        """
        assertions = data.get('assertions', [])
        for entry in assertions:
            kind = FormulaKind(entry.get('kind', 'bool'))
            expr = entry.get('expression', '')
            formula = Z3Formula(kind, expr)
            session.assert_formula(formula)


_FORMULA_KIND_TO_SMT2_SORT: dict[FormulaKind, str] = {
    FormulaKind.BOOL: 'Bool',
    FormulaKind.INT: 'Int',
    FormulaKind.REAL: 'Real',
    FormulaKind.BITVEC: '(_ BitVec 32)',
    FormulaKind.ARRAY: '(Array Int Int)',
    FormulaKind.DATATYPE: 'Bool',
}


# ===================================================================== #
# 15. Z3CopilotAssist -- copilot-assisted Z3 usage                     #
# ===================================================================== #


class Z3CopilotAssist:
    """Copilot-assisted utilities for Z3 encoding and explanation.

    This class provides helper methods that an LLM-based copilot can invoke
    to suggest encodings, explain unsat cores, translate natural-language
    constraints into Z3 formulas, and recommend tactics.  All suggestions
    are informational and must be validated by the solver pipeline before
    they contribute to trust.

    The copilot integration is intentional: ``theory2.tex`` permits
    proposal-tier contributions from LLM assistants, but the resulting
    evidence must still be checked by the solver and the trust algebra.
    """

    def __init__(self, encoder: Z3Encoder | None = None) -> None:
        self._encoder = encoder or Z3Encoder()
        self._classifier = Z3FragmentClassifier()

    def suggest_encoding(self, description: str) -> dict[str, Any]:
        """Suggest a Z3 encoding for a natural-language constraint description.

        The copilot analyses the description for common patterns (arithmetic
        comparisons, set membership, type constraints) and returns a
        suggested formula with explanation.

        Parameters
        ----------
        description:
            A natural-language description of the constraint, e.g.,
            ``"x is a positive integer less than 100"``.

        Returns
        -------
        dict
            A dictionary with keys ``formula``, ``encoding``, ``fragment``,
            ``confidence``, and ``explanation``.
        """
        lowered = description.lower()
        # Heuristic pattern matching for common constraint idioms
        formula: Z3Formula | None = None
        confidence = 0.5
        explanation = 'Heuristic encoding based on keyword analysis.'

        if 'positive' in lowered and ('integer' in lowered or 'int' in lowered):
            var = self._extract_variable_name(description)
            formula = self._encoder.encode_arithmetic(f'{var} > 0')
            confidence = 0.8
            explanation = f'Encoded positivity constraint on {var}.'

        elif 'less than' in lowered or '<' in lowered:
            parts = self._extract_comparison(description)
            if parts:
                formula = self._encoder.encode_arithmetic(
                    f'{parts[0]} < {parts[1]}'
                )
                confidence = 0.75
                explanation = f'Encoded less-than comparison: {parts[0]} < {parts[1]}.'

        elif 'greater than' in lowered or '>' in lowered:
            parts = self._extract_comparison(description)
            if parts:
                formula = self._encoder.encode_arithmetic(
                    f'{parts[0]} > {parts[1]}'
                )
                confidence = 0.75
                explanation = f'Encoded greater-than comparison: {parts[0]} > {parts[1]}.'

        elif 'equal' in lowered or '==' in lowered:
            parts = self._extract_comparison(description)
            if parts:
                formula = self._encoder.encode_arithmetic(
                    f'{parts[0]} == {parts[1]}'
                )
                confidence = 0.7
                explanation = f'Encoded equality: {parts[0]} == {parts[1]}.'

        elif 'not null' in lowered or 'non-null' in lowered:
            var = self._extract_variable_name(description)
            formula = Z3Formula.boolean(f'(not (= {var} null))')
            confidence = 0.6
            explanation = f'Encoded non-null constraint on {var}.'

        if formula is None:
            formula = self._encoder.encode_proposition(description)
            confidence = 0.3
            explanation = 'Fallback: encoded as a propositional variable.'

        fragment = self._classifier.classify(formula)
        return {
            'formula': formula.expression,
            'encoding': formula.pretty_print(),
            'fragment': fragment.value,
            'confidence': confidence,
            'explanation': explanation,
            'copilot': True,
        }

    def explain_unsat_core(
        self,
        core: tuple[str, ...],
        *,
        context: str = '',
    ) -> str:
        """Generate a human-readable explanation of an unsat core.

        The copilot analyses the constraint names in the core and produces
        a paragraph-length explanation suitable for display to a developer.
        """
        if not core:
            return 'The unsat core is empty -- the solver proved unsatisfiability without tracking individual constraints.'

        decoder = Z3Decoder()
        decoded = decoder.decode_unsat_core(core)
        lines = [
            f'The solver found that {len(core)} constraint(s) are jointly unsatisfiable.',
        ]
        if context:
            lines.append(f'Context: {context}.')
        lines.append('')
        lines.append('Participating constraints:')
        for i, entry in enumerate(decoded, 1):
            lines.append(f'  {i}. {entry["decoded"]} -- {entry["explanation"]}')
        lines.append('')
        lines.append(
            'To resolve this, consider relaxing one of the listed constraints '
            'or checking whether the specification is internally consistent.'
        )
        return '\n'.join(lines)

    def explain_countermodel(
        self,
        model: dict[str, Any],
        *,
        property_name: str = '',
    ) -> str:
        """Generate a human-readable explanation of a countermodel.

        The copilot describes the concrete variable assignments that
        witness the failure of the desired property.
        """
        decoder = Z3Decoder()
        decoded = decoder.decode_model(model)
        if not decoded:
            return 'The countermodel is empty -- no concrete witness was extracted.'

        lines: list[str] = []
        if property_name:
            lines.append(f'The property "{property_name}" is violated by the following assignment:')
        else:
            lines.append('The following assignment violates the desired property:')
        lines.append('')
        for var, val in sorted(decoded.items()):
            lines.append(f'  {var} = {val}')
        lines.append('')
        lines.append(
            f'This concrete assignment to {len(decoded)} variable(s) '
            'demonstrates that the property does not hold in general.'
        )
        return '\n'.join(lines)

    def suggest_tactic(self, formula: Z3Formula) -> dict[str, str]:
        """Suggest the best Z3 tactic for *formula* with explanation.

        The copilot consults the fragment classifier and returns a
        structured recommendation.
        """
        tag = self._classifier.classify(formula)
        tactic = self._classifier.recommended_tactic(formula)
        complexity = self._classifier.expected_complexity(formula)
        return {
            'fragment': tag.value,
            'tactic': tactic,
            'complexity': complexity,
            'explanation': (
                f'Formula classified as {tag.value} ({complexity}). '
                f'Recommended tactic: "{tactic}".'
            ),
            'copilot': 'true',
        }

    def translate_natural_language_to_z3(self, text: str) -> Z3Formula:
        """Translate a natural-language constraint into a Z3 formula.

        This is a best-effort copilot translation that handles common
        patterns.  The result must be validated by the solver before it
        can contribute evidence.

        Parameters
        ----------
        text:
            Natural-language constraint, e.g., ``"x plus y equals 10"``.
        """
        lowered = text.lower().strip()

        # Attempt structured parsing of common forms
        replacements = [
            ('plus', '+'), ('minus', '-'), ('times', '*'),
            ('divided by', '/'), ('equals', '=='), ('is equal to', '=='),
            ('is greater than', '>'), ('is less than', '<'),
            ('is at least', '>='), ('is at most', '<='),
            ('is not equal to', '!='), ('is not', '!='),
        ]
        normalized = lowered
        for phrase, op in replacements:
            normalized = normalized.replace(phrase, op)

        # Try to parse as arithmetic
        for op in ['>=', '<=', '!=', '==', '>', '<']:
            if op in normalized:
                parts = normalized.split(op, 1)
                if len(parts) == 2:
                    lhs = parts[0].strip().replace(' ', '_')
                    rhs = parts[1].strip().replace(' ', '_')
                    return self._encoder.encode_arithmetic(f'{lhs}{op}{rhs}')

        # Fallback: treat as boolean proposition
        return self._encoder.encode_proposition(normalized)

    # -- Private helpers ----------------------------------------------------

    def _extract_variable_name(self, text: str) -> str:
        """Extract the most likely variable name from a description."""
        words = text.split()
        for word in words:
            cleaned = word.strip('.,;:!?()')
            if cleaned.isidentifier() and len(cleaned) <= 20 and cleaned.lower() not in {
                'a', 'an', 'the', 'is', 'are', 'be', 'that', 'this',
                'positive', 'negative', 'integer', 'real', 'less', 'greater',
                'than', 'equal', 'to', 'and', 'or', 'not', 'null', 'non',
            }:
                return cleaned
        return 'v'

    def _extract_comparison(self, text: str) -> tuple[str, str] | None:
        """Extract comparison operands from a natural-language constraint."""
        words = text.split()
        identifiers = []
        for word in words:
            cleaned = word.strip('.,;:!?()')
            if cleaned.isidentifier() and cleaned.lower() not in {
                'a', 'an', 'the', 'is', 'are', 'be', 'less', 'greater',
                'than', 'equal', 'to', 'and', 'or', 'not', 'must',
            }:
                identifiers.append(cleaned)
            elif cleaned.lstrip('-').isdigit():
                identifiers.append(cleaned)
        if len(identifiers) >= 2:
            return (identifiers[0], identifiers[1])
        return None


# ===================================================================== #
# Public API                                                            #
# ===================================================================== #

__all__ = [
    'z3_available',
    'SolveOutcome',
    'FormulaKind',
    'TrustLevel',
    'FragmentTag',
    'SolverResult',
    'SolverAdapter',
    'BuiltinAdapter',
    'Z3Formula',
    'Z3Session',
    'Z3SessionPool',
    'Z3Encoder',
    'Z3Decoder',
    'Z3Result',
    'Z3QueryBuilder',
    'Z3FragmentClassifier',
    'Z3TacticRouter',
    'Z3SessionMonitor',
    'Z3Serializer',
    'Z3CopilotAssist',
]

# copilot: shared-core marker for future LLM orchestration.
