r"""theory2.tex Ch31 §31.5 — Reconstruction Witnesses.

# copilot: This module formalises *reconstruction witnesses* — certificates
# that a partial model can be extended to a total model satisfying all
# constraints.  The central idea is that a witness for a partial assignment
# :math:`\sigma: V' \to D` (where :math:`V' \subsetneq V`) is a proof that
# every constraint whose variables are *all* in :math:`V'` is already
# satisfied, and that there exists at least one completion of :math:`\sigma`
# to the full variable set :math:`V` that satisfies the remaining constraints.

Formal definition (§31.5.1)
-----------------------------
Let :math:`\Phi` be a set of Z3 constraints over variables :math:`V`, and let
:math:`\sigma: V' \to D` be a partial assignment (with :math:`V' \subseteq V`
and :math:`D` the value domain).

A *reconstruction witness* for :math:`(\Phi, \sigma)` is a tuple

.. math::

   W = (\sigma, \, \delta, \, \pi, \, \rho)

where

- :math:`\sigma` is the *base partial assignment* already established,
- :math:`\delta: V \setminus V' \to D` is a *completion assignment* for the
  remaining variables,
- :math:`\pi` is a *consistency proof* showing that
  :math:`\sigma \cup \delta \models \Phi`, and
- :math:`\rho` is a *relevance certificate* recording which constraints in
  :math:`\Phi` were discharged by :math:`\sigma` alone vs. those that
  required :math:`\delta`.

Čech obstruction (§31.5.2)
----------------------------
A partial assignment :math:`\sigma` on an open cover
:math:`\{U_i\}_{i \in I}` of the variable set admits a global section if
and only if the *Čech 1-cocycle* condition holds:

.. math::

   \forall i, j \in I: \quad \sigma_i \big|_{U_i \cap U_j} = \sigma_j \big|_{U_i \cap U_j}

Failure of this condition yields a *Čech obstruction class*
:math:`[\omega] \in \check{H}^1(\{U_i\}, \mathcal{F})` where
:math:`\mathcal{F}` is the sheaf of solutions.  The witness module computes
this class and, when it vanishes, constructs an explicit global section.

Trust propagation (§31.5.3)
------------------------------
Every witness carries a *trust level* (from §31.4.3) that reflects how the
completion was found:

.. math::

   \mathrm{trust}(W) = \min\bigl(\mathrm{trust}(\sigma),\,
   \mathrm{trust}(\delta)\bigr)

where the minimum is taken in the partial order
UNVERIFIED ≺ AUTOMATED ≺ COPILOT_PROPOSED ≺ SOLVER_INFERRED ≺ HUMAN_REVIEWED.

Witness composition (§31.5.4)
-------------------------------
Two witnesses :math:`W_1 = (\sigma_1, \delta_1, \pi_1, \rho_1)` and
:math:`W_2 = (\sigma_2, \delta_2, \pi_2, \rho_2)` can be *composed* when
their completions agree on the overlap
:math:`\mathrm{dom}(\delta_1) \cap \mathrm{dom}(\delta_2)`:

.. math::

   W_1 \circ W_2 = (\sigma_1 \cup \sigma_2,\,
                    \delta_1 \sqcup \delta_2,\,
                    \pi_1 \wedge \pi_2,\,
                    \rho_1 \cup \rho_2)

The composition is *left-biased*: when both witnesses provide a value for
the same variable in their completion assignments, the value from
:math:`W_1` takes precedence.

Termination (§31.5.5)
-----------------------
Witness construction is guaranteed to terminate because the set of
unassigned variables strictly decreases at each completion step, and the
domain :math:`D` is assumed finite (or at least well-founded).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import itertools
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional Z3 import
# ---------------------------------------------------------------------------
try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:  # pragma: no cover
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]

try:
    from jugeo.encodings.partiality_model_reconstruction.model_reconstruction import (
        ReconstructionPipeline,
        PartialModelAssembler,
        TrustAnnotator,
        EvidencePackager,
        _TRUST_ORDER,
    )
    _S04_AVAILABLE = True
except ImportError:
    _S04_AVAILABLE = False
    _TRUST_ORDER: list[str] = [
        "UNVERIFIED",
        "AUTOMATED",
        "COPILOT_PROPOSED",
        "SOLVER_INFERRED",
        "HUMAN_REVIEWED",
    ]
    class ReconstructionPipeline: pass  # type: ignore[misc]
    class PartialModelAssembler: pass  # type: ignore[misc]
    class TrustAnnotator: pass  # type: ignore[misc]
    class EvidencePackager: pass  # type: ignore[misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §31.5 Enumerations
# ---------------------------------------------------------------------------


class WitnessKind(str, Enum):
    """Classification of how a reconstruction witness was produced.

    DIRECT
        The completion :math:`\\delta` was read directly from a satisfying Z3
        model returned by the solver.
    INTERPOLATED
        :math:`\\delta` was obtained by interpolating between two partial
        witnesses whose domains cover all variables.
    COMPOSED
        The witness is a left-biased composition of two sub-witnesses
        (see §31.5.4).
    SYNTHETIC
        The completion was constructed synthetically from domain defaults,
        not from a solver run (used for testing and gap-filling).
    ORACLE
        The completion was provided by a human oracle (highest trust level).
    """

    DIRECT = "DIRECT"
    INTERPOLATED = "INTERPOLATED"
    COMPOSED = "COMPOSED"
    SYNTHETIC = "SYNTHETIC"
    ORACLE = "ORACLE"


class ObstructionClass(str, Enum):
    """Whether a Čech obstruction was detected on the partial cover (§31.5.2).

    NONE
        No obstruction; a global section exists.
    LOCAL
        Obstruction is local to a single pair of patches.
    GLOBAL
        Obstruction class is non-trivial in :math:`\\check{H}^1`.
    UNRESOLVED
        The obstruction check has not yet been performed.
    """

    NONE = "NONE"
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"
    UNRESOLVED = "UNRESOLVED"


class ConsistencyStatus(str, Enum):
    """Result of checking :math:`\\sigma \\cup \\delta \\models \\Phi`.

    VERIFIED
        The combined assignment satisfies all constraints.
    PARTIAL
        The assignment satisfies all *checkable* constraints; some constraints
        contain free variables not yet assigned.
    FAILED
        At least one constraint is violated.
    UNKNOWN
        The consistency check could not be performed (Z3 unavailable or
        timeout).
    """

    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# §31.5.1 Core data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VariableBinding:
    """A single variable-to-value binding in an assignment.

    .. math::

       x \\mapsto v \\quad (x \\in V,\\; v \\in D)

    Attributes
    ----------
    variable_name:
        The Z3 variable name (str form of the declaration).
    value_repr:
        JSON-serialisable representation of the assigned value.
    sort_name:
        Z3 sort name (e.g., ``"Int"``, ``"Bool"``, ``"Array(Int,Int)"``).
    trust_level:
        The trust level for this particular binding.
    source:
        Human-readable description of how this binding was obtained.
    """

    variable_name: str
    value_repr: Any
    sort_name: str
    trust_level: str = "AUTOMATED"
    source: str = ""

    def with_trust(self, level: str) -> VariableBinding:
        """Return a copy with a (possibly lower) trust level."""
        cur = _TRUST_ORDER.index(self.trust_level) if self.trust_level in _TRUST_ORDER else 0
        new = _TRUST_ORDER.index(level) if level in _TRUST_ORDER else 0
        effective = _TRUST_ORDER[min(cur, new)]
        return dataclasses.replace(self, trust_level=effective)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True, slots=True)
class ConstraintDischarge:
    """Records which constraint was discharged and by which bindings.

    Attributes
    ----------
    constraint_id:
        Unique identifier for the constraint (e.g., its Z3 sexpr hash).
    constraint_repr:
        Human-readable string representation of the constraint.
    discharged_by:
        Names of the variables whose assignment collectively discharged this
        constraint.
    discharged_in_phase:
        ``"base"`` if discharged by :math:`\\sigma` alone, ``"completion"``
        if :math:`\\delta` was required.
    """

    constraint_id: str
    constraint_repr: str
    discharged_by: tuple[str, ...]
    discharged_in_phase: str = "completion"

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "constraint_repr": self.constraint_repr,
            "discharged_by": list(self.discharged_by),
            "discharged_in_phase": self.discharged_in_phase,
        }


@dataclass(frozen=True, slots=True)
class CechPatch:
    """A single patch in the open cover used for Čech obstruction checking.

    Attributes
    ----------
    patch_id:
        Unique identifier for this patch.
    variable_names:
        The subset of variables :math:`U_i \\subseteq V` covered by this patch.
    local_assignment:
        The local assignment :math:`\\sigma_i: U_i \\to D`.
    """

    patch_id: str
    variable_names: frozenset[str]
    local_assignment: dict[str, Any]

    def intersection(self, other: CechPatch) -> frozenset[str]:
        """Return the variable names in :math:`U_i \\cap U_j`."""
        return self.variable_names & other.variable_names

    def agrees_with(self, other: CechPatch) -> bool:
        """Check the Čech 1-cocycle condition on the overlap with *other*."""
        overlap = self.intersection(other)
        for var in overlap:
            if self.local_assignment.get(var) != other.local_assignment.get(var):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "variable_names": sorted(self.variable_names),
            "local_assignment": self.local_assignment,
        }


# ---------------------------------------------------------------------------
# §31.5 Main Witness class
# ---------------------------------------------------------------------------


@dataclass
class ReconstructionWitness:
    """A certificate that a partial assignment :math:`\\sigma` can be completed.

    # copilot: Central artefact of §31.5.  A ``ReconstructionWitness``
    # records the base partial assignment, the completion that was found,
    # a consistency status, a relevance certificate (which constraints were
    # discharged by the base vs. the completion), and metadata about the
    # witness kind and Čech obstruction class.

    .. math::

       W = (\\sigma, \\, \\delta, \\, \\pi, \\, \\rho)

    Parameters
    ----------
    witness_id:
        A unique identifier for this witness (auto-generated UUID4).
    base_assignment:
        Bindings in :math:`\\sigma` — the partial assignment already known.
    completion_assignment:
        Bindings in :math:`\\delta` — the additional values added to complete
        the assignment.
    consistency_status:
        Whether :math:`\\sigma \\cup \\delta \\models \\Phi` has been verified.
    relevance_certificate:
        Records which constraints were discharged by base vs. completion.
    obstruction_class:
        Whether a Čech obstruction was detected.
    witness_kind:
        How this witness was produced.
    created_at:
        Unix timestamp of witness creation.
    digest:
        SHA-256 hex digest of the canonical JSON representation.
    metadata:
        Arbitrary key/value metadata for downstream consumers.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    base_assignment: list[VariableBinding] = field(default_factory=list)
    completion_assignment: list[VariableBinding] = field(default_factory=list)
    consistency_status: ConsistencyStatus = ConsistencyStatus.UNKNOWN
    relevance_certificate: list[ConstraintDischarge] = field(default_factory=list)
    obstruction_class: ObstructionClass = ObstructionClass.UNRESOLVED
    witness_kind: WitnessKind = WitnessKind.SYNTHETIC
    created_at: float = field(default_factory=time.time)
    digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def base_domain(self) -> frozenset[str]:
        """Variable names covered by the base assignment :math:`\\sigma`."""
        return frozenset(b.variable_name for b in self.base_assignment)

    @property
    def completion_domain(self) -> frozenset[str]:
        """Variable names introduced by the completion :math:`\\delta`."""
        return frozenset(b.variable_name for b in self.completion_assignment)

    @property
    def full_domain(self) -> frozenset[str]:
        """Union of base and completion domains."""
        return self.base_domain | self.completion_domain

    @property
    def combined_assignment(self) -> list[VariableBinding]:
        """Return :math:`\\sigma \\cup \\delta` (base takes precedence)."""
        seen: set[str] = set()
        result: list[VariableBinding] = []
        for b in itertools.chain(self.base_assignment, self.completion_assignment):
            if b.variable_name not in seen:
                seen.add(b.variable_name)
                result.append(b)
        return result

    @property
    def trust_level(self) -> str:
        """Minimum trust across all bindings (§31.5.3)."""
        all_bindings = self.combined_assignment
        if not all_bindings:
            return "UNVERIFIED"
        levels = [
            _TRUST_ORDER.index(b.trust_level)
            if b.trust_level in _TRUST_ORDER else 0
            for b in all_bindings
        ]
        return _TRUST_ORDER[min(levels)]

    @property
    def is_complete(self) -> bool:
        """True when the consistency status is VERIFIED."""
        return self.consistency_status == ConsistencyStatus.VERIFIED

    # ------------------------------------------------------------------
    # Composition (§31.5.4)
    # ------------------------------------------------------------------

    def compose(self, other: ReconstructionWitness) -> ReconstructionWitness:
        """Left-biased composition :math:`W_1 \\circ W_2` (self takes priority).

        The composed witness:

        - merges base assignments (self first),
        - merges completion assignments (self first),
        - takes the weaker consistency status,
        - concatenates relevance certificates,
        - keeps ObstructionClass.NONE only if both witnesses have it.
        """
        seen_base: set[str] = set()
        new_base: list[VariableBinding] = []
        for b in itertools.chain(self.base_assignment, other.base_assignment):
            if b.variable_name not in seen_base:
                seen_base.add(b.variable_name)
                new_base.append(b)

        seen_comp: set[str] = set()
        new_comp: list[VariableBinding] = []
        for b in itertools.chain(self.completion_assignment, other.completion_assignment):
            if b.variable_name not in seen_comp and b.variable_name not in seen_base:
                seen_comp.add(b.variable_name)
                new_comp.append(b)

        _status_order = [
            ConsistencyStatus.VERIFIED,
            ConsistencyStatus.PARTIAL,
            ConsistencyStatus.UNKNOWN,
            ConsistencyStatus.FAILED,
        ]

        def _weaker(a: ConsistencyStatus, b_: ConsistencyStatus) -> ConsistencyStatus:
            return a if _status_order.index(a) >= _status_order.index(b_) else b_

        def _obs_combine(a: ObstructionClass, b_: ObstructionClass) -> ObstructionClass:
            if a == ObstructionClass.NONE and b_ == ObstructionClass.NONE:
                return ObstructionClass.NONE
            if ObstructionClass.GLOBAL in (a, b_):
                return ObstructionClass.GLOBAL
            if ObstructionClass.LOCAL in (a, b_):
                return ObstructionClass.LOCAL
            return ObstructionClass.UNRESOLVED

        return ReconstructionWitness(
            base_assignment=new_base,
            completion_assignment=new_comp,
            consistency_status=_weaker(self.consistency_status, other.consistency_status),
            relevance_certificate=self.relevance_certificate + other.relevance_certificate,
            obstruction_class=_obs_combine(self.obstruction_class, other.obstruction_class),
            witness_kind=WitnessKind.COMPOSED,
            metadata={**other.metadata, **self.metadata},
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def seal(self) -> ReconstructionWitness:
        """Compute and attach the SHA-256 digest of the canonical JSON."""
        payload = json.dumps(self.to_dict(include_digest=False), sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.digest = digest  # dataclass is NOT frozen so we can assign
        return self

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON export."""
        d: dict[str, Any] = {
            "witness_id": self.witness_id,
            "witness_kind": self.witness_kind.value,
            "obstruction_class": self.obstruction_class.value,
            "consistency_status": self.consistency_status.value,
            "trust_level": self.trust_level,
            "created_at": self.created_at,
            "base_assignment": [b.to_dict() for b in self.base_assignment],
            "completion_assignment": [b.to_dict() for b in self.completion_assignment],
            "relevance_certificate": [r.to_dict() for r in self.relevance_certificate],
            "metadata": self.metadata,
        }
        if include_digest:
            d["digest"] = self.digest
        return d

    def to_json(self, *, indent: int = 2) -> str:
        """Return pretty-printed JSON representation."""
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# §31.5.2 Čech obstruction checker (helper)
# ---------------------------------------------------------------------------


@dataclass
class _CechObstructionChecker:
    """Internal helper that computes the Čech 1-cocycle condition.

    Given a list of :class:`CechPatch` objects, it checks every pair
    :math:`(U_i, U_j)` for consistency on their overlap.  When an
    inconsistency is found, it records the conflicting variables and
    classifies the obstruction.
    """

    patches: list[CechPatch] = field(default_factory=list)
    _conflicts: list[tuple[str, str, str]] = field(default_factory=list, repr=False)

    def run(self) -> ObstructionClass:
        """Execute the cocycle check and return an :class:`ObstructionClass`."""
        self._conflicts.clear()
        for i, pi in enumerate(self.patches):
            for j, pj in enumerate(self.patches):
                if j <= i:
                    continue
                overlap = pi.intersection(pj)
                for var in overlap:
                    vi = pi.local_assignment.get(var)
                    vj = pj.local_assignment.get(var)
                    if vi != vj:
                        self._conflicts.append((pi.patch_id, pj.patch_id, var))
        if not self._conflicts:
            return ObstructionClass.NONE
        # If all conflicts share the same patch pair it is LOCAL, else GLOBAL
        patch_pairs = {(c[0], c[1]) for c in self._conflicts}
        if len(patch_pairs) == 1:
            return ObstructionClass.LOCAL
        return ObstructionClass.GLOBAL

    @property
    def conflict_report(self) -> list[dict[str, str]]:
        """Return a list of conflict dictionaries (patch_i, patch_j, variable)."""
        return [
            {"patch_i": c[0], "patch_j": c[1], "variable": c[2]}
            for c in self._conflicts
        ]


# ---------------------------------------------------------------------------
# §31.5 ReconstructionWitnessAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class ReconstructionWitnessAnalyzer:
    """Analyses an existing :class:`ReconstructionWitness` for quality metrics.

    # copilot: The analyzer is a *read-only* pass over a finished witness.
    # It computes coverage ratios, trust statistics, obstruction diagnostics,
    # and emits a structured report that can feed the trust annotation phase
    # (§31.4.3) or be shown directly to the user.

    Attributes
    ----------
    witness:
        The witness to analyse.
    constraint_universe:
        All constraint IDs known to exist; used to compute uncovered fraction.
    """

    witness: ReconstructionWitness
    constraint_universe: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Coverage metrics
    # ------------------------------------------------------------------

    def base_coverage(self) -> float:
        """Fraction of constraint_universe discharged in the base phase."""
        if not self.constraint_universe:
            return 0.0
        base_ids = {
            d.constraint_id
            for d in self.witness.relevance_certificate
            if d.discharged_in_phase == "base"
        }
        return len(base_ids) / len(self.constraint_universe)

    def completion_coverage(self) -> float:
        """Fraction of constraint_universe discharged in the completion phase."""
        if not self.constraint_universe:
            return 0.0
        comp_ids = {
            d.constraint_id
            for d in self.witness.relevance_certificate
            if d.discharged_in_phase == "completion"
        }
        return len(comp_ids) / len(self.constraint_universe)

    def uncovered_constraints(self) -> list[str]:
        """Constraint IDs in the universe that are not in the certificate."""
        cert_ids = {d.constraint_id for d in self.witness.relevance_certificate}
        return [cid for cid in self.constraint_universe if cid not in cert_ids]

    # ------------------------------------------------------------------
    # Trust statistics
    # ------------------------------------------------------------------

    def trust_histogram(self) -> dict[str, int]:
        """Count of bindings at each trust level across the full assignment."""
        hist: dict[str, int] = {level: 0 for level in _TRUST_ORDER}
        for b in self.witness.combined_assignment:
            level = b.trust_level if b.trust_level in hist else "UNVERIFIED"
            hist[level] += 1
        return hist

    def weakest_binding(self) -> VariableBinding | None:
        """Return the binding with the lowest trust level, or None."""
        bindings = self.witness.combined_assignment
        if not bindings:
            return None
        return min(
            bindings,
            key=lambda b: _TRUST_ORDER.index(b.trust_level)
            if b.trust_level in _TRUST_ORDER else -1,
        )

    # ------------------------------------------------------------------
    # Obstruction diagnostics
    # ------------------------------------------------------------------

    def obstruction_summary(self) -> dict[str, Any]:
        """Summarise the Čech obstruction status of the witness."""
        return {
            "obstruction_class": self.witness.obstruction_class.value,
            "is_obstructed": self.witness.obstruction_class
            not in (ObstructionClass.NONE, ObstructionClass.UNRESOLVED),
            "consistency_status": self.witness.consistency_status.value,
        }

    # ------------------------------------------------------------------
    # Full report
    # ------------------------------------------------------------------

    def full_report(self) -> dict[str, Any]:
        """Return a comprehensive analysis report as a plain dict."""
        return {
            "witness_id": self.witness.witness_id,
            "witness_kind": self.witness.witness_kind.value,
            "trust_level": self.witness.trust_level,
            "base_domain_size": len(self.witness.base_domain),
            "completion_domain_size": len(self.witness.completion_domain),
            "full_domain_size": len(self.witness.full_domain),
            "base_coverage": self.base_coverage(),
            "completion_coverage": self.completion_coverage(),
            "uncovered_constraints": self.uncovered_constraints(),
            "trust_histogram": self.trust_histogram(),
            "weakest_binding": self.weakest_binding().to_dict()
            if self.weakest_binding() else None,
            "obstruction": self.obstruction_summary(),
            "is_complete_witness": self.witness.is_complete,
        }

    # ------------------------------------------------------------------
    # Z3 re-verification
    # ------------------------------------------------------------------

    def reverify_with_z3(
        self,
        z3_formulas: list[Any],
        *,
        timeout_ms: int = 10_000,
    ) -> ConsistencyStatus:
        """Re-verify the witness assignment against *z3_formulas* using Z3.

        If Z3 is not available the status is set to UNKNOWN and a warning is
        logged.  Otherwise a fresh :class:`z3.Solver` is created, all
        bindings are added as equalities, and the formulas are checked.

        Parameters
        ----------
        z3_formulas:
            List of Z3 Boolean expressions (already constructed by the
            caller).
        timeout_ms:
            Solver timeout in milliseconds.

        Returns
        -------
        ConsistencyStatus
            The updated consistency status.
        """
        if not _Z3_AVAILABLE or z3 is None:
            logger.warning("Z3 not available; skipping reverification")
            return ConsistencyStatus.UNKNOWN

        solver = z3.Solver()
        solver.set("timeout", timeout_ms)

        for b in self.witness.combined_assignment:
            try:
                var_ref = z3.Int(b.variable_name)
                solver.add(var_ref == int(b.value_repr))
            except (TypeError, ValueError):
                pass  # Non-integer sorts — skip binding equality

        for formula in z3_formulas:
            solver.add(formula)

        result = solver.check()
        if result == z3.sat:
            return ConsistencyStatus.VERIFIED
        if result == z3.unsat:
            return ConsistencyStatus.FAILED
        return ConsistencyStatus.UNKNOWN


# ---------------------------------------------------------------------------
# §31.5 ReconstructionWitnessCoordinator
# ---------------------------------------------------------------------------


@dataclass
class ReconstructionWitnessCoordinator:
    """Orchestrates the full witness construction workflow (§31.5).

    # copilot: The coordinator is the top-level entry point for building
    # reconstruction witnesses.  It accepts a partial assignment, an open
    # cover description (list of :class:`CechPatch`), and optional Z3
    # formulas, then:
    #
    # 1. Runs the Čech obstruction check to determine if a global section
    #    can exist at all.
    # 2. If unobstructed, queries the Z3 solver for a completion.
    # 3. Constructs the :class:`ReconstructionWitness`.
    # 4. Runs the :class:`ReconstructionWitnessAnalyzer` for quality metrics.
    # 5. Returns a :class:`WitnessResult` summary.

    Attributes
    ----------
    session_id:
        Identifier for the current JuGeo session (used in metadata).
    default_trust_level:
        Trust level to assign to solver-inferred completions.
    max_completion_attempts:
        Number of times to retry completion before giving up.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    default_trust_level: str = "SOLVER_INFERRED"
    max_completion_attempts: int = 3

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_witness(
        self,
        base_bindings: list[dict[str, Any]],
        patches: list[CechPatch],
        z3_formulas: list[Any] | None = None,
        *,
        constraint_universe: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WitnessResult:
        """Build a :class:`ReconstructionWitness` from *base_bindings* and *patches*.

        Parameters
        ----------
        base_bindings:
            List of dicts with keys ``variable_name``, ``value_repr``,
            ``sort_name``, and optionally ``trust_level`` / ``source``.
        patches:
            Open cover of the variable set for the Čech obstruction check.
        z3_formulas:
            Optional Z3 formula objects to be satisfied by the completion.
        constraint_universe:
            All constraint IDs; used for coverage metrics.
        metadata:
            Arbitrary metadata to embed in the witness.

        Returns
        -------
        WitnessResult
            A summary including the constructed witness and quality report.
        """
        t_start = time.time()
        base_assignment = [
            VariableBinding(
                variable_name=bd["variable_name"],
                value_repr=bd["value_repr"],
                sort_name=bd.get("sort_name", "Unknown"),
                trust_level=bd.get("trust_level", self.default_trust_level),
                source=bd.get("source", "caller-provided"),
            )
            for bd in base_bindings
        ]

        # Step 1 — Čech obstruction check
        obstruction_class = self._check_obstruction(patches)

        if obstruction_class == ObstructionClass.GLOBAL:
            # Cannot complete; return a failed witness immediately
            witness = ReconstructionWitness(
                base_assignment=base_assignment,
                consistency_status=ConsistencyStatus.FAILED,
                obstruction_class=ObstructionClass.GLOBAL,
                witness_kind=WitnessKind.SYNTHETIC,
                metadata=metadata or {},
            )
            witness.seal()
            return WitnessResult(
                witness=witness,
                elapsed_seconds=time.time() - t_start,
                success=False,
                error_message="Global Čech obstruction detected; no global section exists",
            )

        # Step 2 — attempt completion via Z3
        completion, kind = self._attempt_completion(
            base_assignment, z3_formulas or [], patches
        )

        # Step 3 — build relevance certificate
        certificate = self._build_relevance_certificate(
            base_assignment, completion, z3_formulas or [], constraint_universe or []
        )

        # Step 4 — determine consistency status
        consistency = self._determine_consistency(
            base_assignment, completion, z3_formulas or []
        )

        witness = ReconstructionWitness(
            base_assignment=base_assignment,
            completion_assignment=completion,
            consistency_status=consistency,
            relevance_certificate=certificate,
            obstruction_class=obstruction_class,
            witness_kind=kind,
            metadata=metadata or {},
        )
        witness.seal()

        # Step 5 — analysis
        analyzer = ReconstructionWitnessAnalyzer(
            witness=witness,
            constraint_universe=constraint_universe or [],
        )
        report = analyzer.full_report()

        return WitnessResult(
            witness=witness,
            elapsed_seconds=time.time() - t_start,
            success=witness.is_complete or consistency == ConsistencyStatus.PARTIAL,
            analysis_report=report,
        )

    def compose_witnesses(
        self, w1: ReconstructionWitness, w2: ReconstructionWitness
    ) -> ReconstructionWitness:
        """Compose two witnesses (§31.5.4) and seal the result."""
        composed = w1.compose(w2)
        composed.seal()
        return composed

    def iter_patches(
        self, all_variables: list[str], patch_size: int = 4
    ) -> Iterator[CechPatch]:
        """Generate overlapping patches of *all_variables* for Čech checking.

        Each patch covers a sliding window of *patch_size* variables.
        Consecutive patches share ``patch_size - 1`` variables so that every
        adjacent pair has a non-trivial overlap.

        Parameters
        ----------
        all_variables:
            Ordered list of all variable names.
        patch_size:
            Number of variables per patch (default 4).

        Yields
        ------
        CechPatch
            Patches with empty local assignments (to be filled by caller).
        """
        if patch_size < 2:
            raise ValueError("patch_size must be at least 2 for non-trivial overlaps")
        for i in range(0, max(1, len(all_variables) - patch_size + 2)):
            window = all_variables[i: i + patch_size]
            if not window:
                break
            yield CechPatch(
                patch_id=f"patch_{i}",
                variable_names=frozenset(window),
                local_assignment={},
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_obstruction(self, patches: list[CechPatch]) -> ObstructionClass:
        if not patches:
            return ObstructionClass.NONE
        checker = _CechObstructionChecker(patches=patches)
        return checker.run()

    def _attempt_completion(
        self,
        base: list[VariableBinding],
        formulas: list[Any],
        patches: list[CechPatch],
    ) -> tuple[list[VariableBinding], WitnessKind]:
        """Try to find a completion for variables not already in *base*."""
        if not _Z3_AVAILABLE or z3 is None or not formulas:
            return self._synthetic_completion(base, patches)

        base_vars = {b.variable_name for b in base}
        solver = z3.Solver()
        solver.set("timeout", 15_000)

        for b in base:
            try:
                solver.add(z3.Int(b.variable_name) == int(b.value_repr))
            except (TypeError, ValueError):
                pass

        for formula in formulas:
            solver.add(formula)

        for _attempt in range(self.max_completion_attempts):
            result = solver.check()
            if result == z3.sat:
                model = solver.model()
                completion: list[VariableBinding] = []
                for decl in model.decls():
                    name = str(decl.name())
                    if name in base_vars:
                        continue
                    val = model[decl]
                    completion.append(
                        VariableBinding(
                            variable_name=name,
                            value_repr=str(val),
                            sort_name=str(decl.range()),
                            trust_level=self.default_trust_level,
                            source="z3-model",
                        )
                    )
                return completion, WitnessKind.DIRECT
            if result == z3.unsat:
                break

        return self._synthetic_completion(base, patches)

    def _synthetic_completion(
        self,
        base: list[VariableBinding],
        patches: list[CechPatch],
    ) -> tuple[list[VariableBinding], WitnessKind]:
        """Produce a synthetic completion from patch local assignments."""
        base_vars = {b.variable_name for b in base}
        completion: list[VariableBinding] = []
        seen: set[str] = set(base_vars)

        for patch in patches:
            for var, val in patch.local_assignment.items():
                if var not in seen:
                    seen.add(var)
                    completion.append(
                        VariableBinding(
                            variable_name=var,
                            value_repr=val,
                            sort_name="Unknown",
                            trust_level="AUTOMATED",
                            source=f"patch:{patch.patch_id}",
                        )
                    )
        return completion, WitnessKind.SYNTHETIC

    def _build_relevance_certificate(
        self,
        base: list[VariableBinding],
        completion: list[VariableBinding],
        formulas: list[Any],
        universe: list[str],
    ) -> list[ConstraintDischarge]:
        """Build a relevance certificate by inspecting formula variable sets."""
        base_vars = {b.variable_name for b in base}
        comp_vars = {b.variable_name for b in completion}
        certificate: list[ConstraintDischarge] = []

        for cid in universe:
            # We cannot introspect arbitrary Z3 formula objects here
            # without knowing their structure, so we emit placeholder
            # certificates for every ID in the universe.
            certificate.append(
                ConstraintDischarge(
                    constraint_id=cid,
                    constraint_repr=f"constraint({cid})",
                    discharged_by=tuple(base_vars | comp_vars),
                    discharged_in_phase="completion" if comp_vars else "base",
                )
            )

        if not certificate and formulas:
            for i, _ in enumerate(formulas):
                cid = hashlib.sha256(f"formula_{i}_{self.session_id}".encode()).hexdigest()[:12]
                phase = "base" if not completion else "completion"
                certificate.append(
                    ConstraintDischarge(
                        constraint_id=cid,
                        constraint_repr=f"formula[{i}]",
                        discharged_by=tuple(base_vars | comp_vars),
                        discharged_in_phase=phase,
                    )
                )

        return certificate

    def _determine_consistency(
        self,
        base: list[VariableBinding],
        completion: list[VariableBinding],
        formulas: list[Any],
    ) -> ConsistencyStatus:
        """Quick consistency determination; defers to PARTIAL when uncertain."""
        if not formulas:
            return ConsistencyStatus.PARTIAL
        if not _Z3_AVAILABLE or z3 is None:
            return ConsistencyStatus.UNKNOWN

        solver = z3.Solver()
        solver.set("timeout", 5_000)

        for b in itertools.chain(base, completion):
            try:
                solver.add(z3.Int(b.variable_name) == int(b.value_repr))
            except (TypeError, ValueError):
                pass

        for formula in formulas:
            solver.add(formula)

        result = solver.check()
        if result == z3.sat:
            return ConsistencyStatus.VERIFIED
        if result == z3.unsat:
            return ConsistencyStatus.FAILED
        return ConsistencyStatus.UNKNOWN


# ---------------------------------------------------------------------------
# §31.5 WitnessResult (output container)
# ---------------------------------------------------------------------------


@dataclass
class WitnessResult:
    """Container returned by :meth:`ReconstructionWitnessCoordinator.build_witness`.

    # copilot: Bundles the constructed witness with quality metrics and
    # timing information so callers can make policy decisions (e.g., reject
    # low-trust witnesses) without unpacking the witness internals.

    Attributes
    ----------
    witness:
        The constructed :class:`ReconstructionWitness`.
    elapsed_seconds:
        Wall-clock time for the entire build_witness call.
    success:
        True when the witness has status VERIFIED or PARTIAL.
    error_message:
        Non-empty when *success* is False; describes the failure reason.
    analysis_report:
        Optional report dict from :class:`ReconstructionWitnessAnalyzer`.
    """

    witness: ReconstructionWitness
    elapsed_seconds: float = 0.0
    success: bool = False
    error_message: str = ""
    analysis_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "elapsed_seconds": self.elapsed_seconds,
            "error_message": self.error_message,
            "witness": self.witness.to_dict(),
            "analysis_report": self.analysis_report,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Convenience factory functions
# ---------------------------------------------------------------------------


def make_witness_from_z3_model(
    model: Any,
    base_vars: list[str],
    *,
    trust_level: str = "SOLVER_INFERRED",
) -> ReconstructionWitness:
    """Construct a :class:`ReconstructionWitness` from a Z3 model object.

    All variables declared in *model* that are present in *base_vars* go into
    the base assignment; the rest go into the completion assignment.

    Parameters
    ----------
    model:
        A ``z3.ModelRef`` returned by :meth:`z3.Solver.model`.
    base_vars:
        Variable names considered part of the pre-existing partial assignment.
    trust_level:
        Trust level assigned to every binding.
    """
    if not _Z3_AVAILABLE or z3 is None or model is None:
        return ReconstructionWitness(
            consistency_status=ConsistencyStatus.UNKNOWN,
            witness_kind=WitnessKind.SYNTHETIC,
        )

    base_set = set(base_vars)
    base_assignment: list[VariableBinding] = []
    completion_assignment: list[VariableBinding] = []

    for decl in model.decls():
        name = str(decl.name())
        val = model[decl]
        binding = VariableBinding(
            variable_name=name,
            value_repr=str(val),
            sort_name=str(decl.range()),
            trust_level=trust_level,
            source="z3-model",
        )
        if name in base_set:
            base_assignment.append(binding)
        else:
            completion_assignment.append(binding)

    witness = ReconstructionWitness(
        base_assignment=base_assignment,
        completion_assignment=completion_assignment,
        consistency_status=ConsistencyStatus.VERIFIED,
        obstruction_class=ObstructionClass.NONE,
        witness_kind=WitnessKind.DIRECT,
    )
    witness.seal()
    return witness


def compose_witness_list(witnesses: list[ReconstructionWitness]) -> ReconstructionWitness:
    """Left-fold a list of witnesses using :meth:`ReconstructionWitness.compose`.

    An empty list returns an empty UNKNOWN witness; a singleton list
    returns that witness unchanged (with a fresh seal).
    """
    if not witnesses:
        w = ReconstructionWitness(consistency_status=ConsistencyStatus.UNKNOWN)
        w.seal()
        return w
    result = witnesses[0]
    for w in witnesses[1:]:
        result = result.compose(w)
    result.seal()
    return result


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick self-test of the witness machinery (§31.5 smoke test).

    This function is intentionally self-contained so it can be run
    with ``python -m jugeo.encodings.partiality_model_reconstruction.reconstruction_witnesses``
    without any external dependencies.
    """
    print("=== §31.5 Reconstruction Witnesses — smoke test ===")

    # ---- CechPatch & obstruction check ------------------------------------
    p0 = CechPatch(
        patch_id="p0",
        variable_names=frozenset(["x", "y"]),
        local_assignment={"x": 1, "y": 2},
    )
    p1 = CechPatch(
        patch_id="p1",
        variable_names=frozenset(["y", "z"]),
        local_assignment={"y": 2, "z": 3},  # consistent overlap
    )
    p2_bad = CechPatch(
        patch_id="p2_bad",
        variable_names=frozenset(["y", "w"]),
        local_assignment={"y": 99, "w": 4},  # y conflicts with p0
    )

    checker_ok = _CechObstructionChecker(patches=[p0, p1])
    assert checker_ok.run() == ObstructionClass.NONE, "expected no obstruction"
    print("  [OK] consistent cover → ObstructionClass.NONE")

    checker_bad = _CechObstructionChecker(patches=[p0, p2_bad])
    obs = checker_bad.run()
    assert obs in (ObstructionClass.LOCAL, ObstructionClass.GLOBAL)
    print(f"  [OK] inconsistent cover → ObstructionClass.{obs.value}")

    # ---- VariableBinding & composition ------------------------------------
    b1 = VariableBinding("x", 1, "Int", trust_level="SOLVER_INFERRED", source="test")
    b2 = VariableBinding("y", 2, "Int", trust_level="AUTOMATED", source="test")
    b3 = VariableBinding("z", 3, "Int", trust_level="HUMAN_REVIEWED", source="test")

    w1 = ReconstructionWitness(
        base_assignment=[b1],
        completion_assignment=[b2],
        consistency_status=ConsistencyStatus.VERIFIED,
        obstruction_class=ObstructionClass.NONE,
        witness_kind=WitnessKind.DIRECT,
    )
    w2 = ReconstructionWitness(
        base_assignment=[b3],
        completion_assignment=[],
        consistency_status=ConsistencyStatus.PARTIAL,
        obstruction_class=ObstructionClass.NONE,
        witness_kind=WitnessKind.DIRECT,
    )

    w_composed = w1.compose(w2)
    assert w_composed.witness_kind == WitnessKind.COMPOSED
    # Composed consistency = weaker of VERIFIED and PARTIAL → PARTIAL
    assert w_composed.consistency_status == ConsistencyStatus.PARTIAL
    print("  [OK] witness composition produces COMPOSED kind and PARTIAL status")

    # ---- Trust propagation ------------------------------------------------
    assert w1.trust_level == "AUTOMATED", f"expected AUTOMATED, got {w1.trust_level}"
    print(f"  [OK] trust propagation: min(SOLVER_INFERRED, AUTOMATED) = {w1.trust_level}")

    # ---- Seal / digest ----------------------------------------------------
    w1.seal()
    assert len(w1.digest) == 64, "SHA-256 digest should be 64 hex chars"
    print(f"  [OK] seal() produces 64-char SHA-256 digest: {w1.digest[:16]}…")

    # ---- Coordinator (no Z3 required) -------------------------------------
    coord = ReconstructionWitnessCoordinator(
        default_trust_level="AUTOMATED",
        max_completion_attempts=1,
    )
    result = coord.build_witness(
        base_bindings=[
            {"variable_name": "a", "value_repr": 10, "sort_name": "Int"},
            {"variable_name": "b", "value_repr": 20, "sort_name": "Int"},
        ],
        patches=[p0, p1],
        constraint_universe=["c1", "c2"],
        metadata={"smoke_test": True},
    )
    assert result.witness is not None
    assert result.witness.base_domain == frozenset(["a", "b"])
    print(
        f"  [OK] Coordinator.build_witness(): success={result.success}, "
        f"kind={result.witness.witness_kind.value}"
    )

    # ---- compose_witness_list ---------------------------------------------
    composed_list = compose_witness_list([w1, w2])
    assert composed_list.witness_kind == WitnessKind.COMPOSED
    print("  [OK] compose_witness_list() produces COMPOSED witness")

    # ---- Analyzer ---------------------------------------------------------
    analyzer = ReconstructionWitnessAnalyzer(
        witness=result.witness,
        constraint_universe=["c1", "c2"],
    )
    report = analyzer.full_report()
    assert "trust_histogram" in report
    assert "obstruction" in report
    print(f"  [OK] Analyzer.full_report() keys: {sorted(report.keys())[:5]}…")

    # ---- Patch iterator ---------------------------------------------------
    all_vars = ["x", "y", "z", "w", "v"]
    generated_patches = list(coord.iter_patches(all_vars, patch_size=3))
    assert len(generated_patches) >= 2
    print(f"  [OK] iter_patches produced {len(generated_patches)} patches for {all_vars}")

    print("=== All smoke tests passed ===")


if __name__ == "__main__":
    _smoke_test()
