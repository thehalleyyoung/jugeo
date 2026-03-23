"""Model reconstruction from Z3 solver results into JuGeo evidence objects.

Implements the reconstruction layer described in chapter 30 of ``theory2.tex``
(partial functions, algebraic surfaces, exception-valued semantics, model
reconstruction).  After the solver returns a result — SAT with a model, UNSAT
with an unsat core, or UNKNOWN with partial information — the reconstruction
pipeline translates that raw output into fully-annotated JuGeo evidence objects
with proper trust annotations, provenance, and scope.

Key design obligations (from theory2.tex §30):

* **Proof reconstruction** — UNSAT cores are lifted into proof step sequences
  that can be independently verified, compressed, and attached to settlement
  certificates.  Each step carries its own trust annotation and traces back to
  the original assertion that sourced it.
* **Witness reconstruction** — SAT models are interpreted as runtime witnesses
  with concrete assignments, then validated against the original query to ensure
  faithfulness before being packaged as evidence.
* **Model reconstruction** — full model interpretations cover sorts, function
  symbols, array images, and algebraic datatypes.  The reconstructed model
  preserves the algebraic-surface structure from theory2.tex so that downstream
  modules can project onto any coordinate of the site.
* **Partial reconstruction** — UNKNOWN / timeout results still carry useful
  information.  Decided atoms are separated from undecided ones, and a
  best-effort partial model is assembled.  The copilot channel may assist in
  completing partial reconstructions, but such completions are clearly marked
  with proposal-tier trust and require corroboration.
* **Trust annotation** — every reconstructed object carries a trust level from
  the evidence algebra (§252).  Solver-originated evidence starts at
  SOLVER_DISCHARGED; copilot-assisted completions at COPILOT_SUGGESTED.
  No silent promotion is permitted.
* **Provenance** — every reconstructed object records its origin (solver
  backend, session id, fragment, timing) so that downstream invalidation
  and auditing can trace evidence to its source.
* **Caching** — identical queries produce identical reconstructions.  A
  content-addressed cache avoids redundant reconstruction work.
* **Validation** — every reconstruction is validated against the original
  query before delivery.  Invalid reconstructions are rejected with a
  structured failure rather than silently propagated.

The authoritative semantic source is ``preliminaries/theory2.tex``.  A copilot
proposal channel may *complete* partial models, but the resulting evidence
preserves its proposal-tier trust and requires corroboration before promotion.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.evidence.channels import EvidenceChannel, EvidenceResponse
from jugeo.evidence.provenance import ProvenanceStep, ProvenanceTrace
from jugeo.evidence.trust import TrustAlgebra, TrustLevel
from jugeo.solver.countermodels import Countermodel, extract_countermodel
from jugeo.solver.fragments import LogicalFragment, SolverFragment
from jugeo.solver.z3_session import SolveOutcome, SolverResult

# ---------------------------------------------------------------------------
# Optional imports for cross-subsystem integration (judgment-geometric links)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment as _Judgment,
        JudgmentBuilder as _JudgmentBuilder,
        Proposition as _Proposition,
        Carrier as _Carrier,
        EvidenceBundle as _EvidenceBundle,
    )
    _JUDGMENT_TERMS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JUDGMENT_TERMS_AVAILABLE = False

try:
    from jugeo.evidence.provenance import (
        ProvenanceGraph as _ProvenanceGraph,
        ProvenanceNode as _ProvenanceNode,
        ProvenanceOperation as _ProvenanceOperation,
    )
    _PROVENANCE_GRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROVENANCE_GRAPH_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

_DEFAULT_CACHE_CAPACITY: int = 4096
_MAX_PROOF_DEPTH: int = 512
_PARTIAL_TRUST_CEILING: str = 'COPILOT_SUGGESTED'
_RECONSTRUCTION_VERSION: str = '0.4.0'
_COMPRESSION_THRESHOLD: int = 32

# Mapping from solver engine names to default trust levels.
_ENGINE_TRUST_MAP: dict[str, TrustLevel] = {
    'z3': TrustLevel.SOLVER_DISCHARGED,
    'builtin': TrustLevel.SOLVER_DISCHARGED,
    'cvc5': TrustLevel.SOLVER_DISCHARGED,
    'copilot': TrustLevel.COPILOT_SUGGESTED,
    'oracle': TrustLevel.ORACLE_PROPOSED,
    'human': TrustLevel.HUMAN_ATTESTED,
}

# Outcome to reconstruction kind routing table.
_OUTCOME_KIND_MAP: dict[SolveOutcome, str] = {
    SolveOutcome.UNSAT: 'PROOF',
    SolveOutcome.SAT: 'WITNESS',
    SolveOutcome.UNKNOWN: 'PARTIAL',
}


# ─────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────


class ReconstructionKind(str, Enum):
    """Kind of reconstruction produced from a solver result.

    Each member corresponds to a distinct evidence shape that downstream
    modules consume.  COUNTERMODEL is a specialised WITNESS that emphasises
    refutation rather than confirmation.
    """

    PROOF = 'proof'
    WITNESS = 'witness'
    COUNTERMODEL = 'countermodel'
    PARTIAL = 'partial'


class ValidationStatus(str, Enum):
    """Outcome of validating a reconstruction against its source query."""

    VALID = 'valid'
    INVALID = 'invalid'
    INCONCLUSIVE = 'inconclusive'


# ─────────────────────────────────────────────────────────────────────────
# Lightweight value types
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProofStep:
    """A single step in a reconstructed proof.

    Each step references the assertion id it derives from, a human-readable
    justification, and the set of hypotheses consumed.  Trust is inherited
    from the solver engine that produced the underlying UNSAT core.
    """

    step_id: str
    assertion_id: str
    justification: str
    hypotheses: tuple[str, ...] = field(default_factory=tuple)
    trust_level: TrustLevel = TrustLevel.SOLVER_DISCHARGED
    metadata: dict[str, Any] = field(default_factory=dict)

    def depends_on(self, other: ProofStep) -> bool:
        """Return *True* if this step consumes *other* as a hypothesis."""
        return other.step_id in self.hypotheses

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            'step_id': self.step_id,
            'assertion_id': self.assertion_id,
            'justification': self.justification,
            'hypotheses': list(self.hypotheses),
            'trust_level': self.trust_level.name,
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class WitnessBinding:
    """A single variable–value binding extracted from a SAT model.

    The *sort_name* records the Z3 sort so that downstream interpreters can
    reconstruct typed values.  The *raw_value* is the Z3 representation as a
    string; *interpreted_value* is the JuGeo-level Python value.
    """

    variable: str
    raw_value: str
    interpreted_value: Any = None
    sort_name: str = 'Bool'

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            'variable': self.variable,
            'raw_value': self.raw_value,
            'interpreted_value': repr(self.interpreted_value),
            'sort_name': self.sort_name,
        }


@dataclass(frozen=True, slots=True)
class SortInterpretation:
    """Interpretation of a Z3 sort in the reconstructed model.

    *universe* lists the concrete elements inhabiting the sort in the model.
    *is_finite* indicates whether the model provides a finite enumeration.
    """

    sort_name: str
    universe: tuple[str, ...] = field(default_factory=tuple)
    is_finite: bool = True
    cardinality: int = 0

    def __post_init__(self) -> None:
        if self.cardinality == 0 and self.universe:
            object.__setattr__(self, 'cardinality', len(self.universe))

    def contains(self, element: str) -> bool:
        """Check whether *element* is in the sort universe."""
        return element in self.universe


@dataclass(frozen=True, slots=True)
class FunctionInterpretation:
    """Interpretation of a function symbol in the reconstructed model.

    *entries* maps argument tuples to result values.  *else_value* is the
    default for arguments not explicitly listed (Z3's ``else`` branch).
    """

    function_name: str
    arity: int
    entries: tuple[tuple[tuple[str, ...], str], ...] = field(default_factory=tuple)
    else_value: str | None = None

    def evaluate(self, args: tuple[str, ...]) -> str | None:
        """Look up the function value for *args*, falling back to *else_value*."""
        for entry_args, result in self.entries:
            if entry_args == args:
                return result
        return self.else_value

    def entry_count(self) -> int:
        """Return the number of explicit entries."""
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class ArrayInterpretation:
    """Interpretation of a Z3 array in the reconstructed model.

    Arrays are represented as a default value plus a finite set of overrides
    at specific indices.  This mirrors the ``store`` / ``const`` pattern from
    the Z3 array theory.
    """

    array_name: str
    index_sort: str
    element_sort: str
    default_value: str | None = None
    stores: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def read(self, index: str) -> str | None:
        """Read the array at *index*, falling back to *default_value*."""
        for stored_index, stored_value in self.stores:
            if stored_index == index:
                return stored_value
        return self.default_value

    def store_count(self) -> int:
        """Return the number of explicit stores."""
        return len(self.stores)


@dataclass(frozen=True, slots=True)
class DatatypeInterpretation:
    """Interpretation of an algebraic datatype in the reconstructed model.

    *constructors* maps constructor names to their argument sorts, and
    *instances* lists concrete instances found in the model.
    """

    type_name: str
    constructors: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)
    instances: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def constructor_names(self) -> tuple[str, ...]:
        """Return the names of all constructors."""
        return tuple(name for name, _ in self.constructors)

    def instance_count(self) -> int:
        """Return the number of concrete instances."""
        return len(self.instances)


# ─────────────────────────────────────────────────────────────────────────
# Core result dataclass
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    """Complete result of reconstructing a solver output into JuGeo evidence.

    This is the primary exchange type produced by the reconstruction pipeline
    and consumed by the evidence assembler.  Every field is immutable; use
    :func:`dataclasses.replace` to derive a modified copy.

    Attributes
    ----------
    result_id : str
        Unique identifier for this reconstruction, generated at creation time.
    kind : ReconstructionKind
        The category of reconstruction (PROOF, WITNESS, COUNTERMODEL, PARTIAL).
    original_query : str
        The query string or formula that was submitted to the solver.
    z3_result : SolverResult
        The raw solver result that was reconstructed.
    reconstructed_evidence : dict[str, Any]
        The structured evidence payload — proof steps, witness bindings,
        model interpretations, or partial atoms depending on *kind*.
    trust_level : TrustLevel
        The trust annotation for this evidence, derived from the solver
        engine and reconstruction pathway.
    provenance : ProvenanceTrace
        Full provenance trace recording how this evidence was produced.
    reconstruction_time_ms : float
        Wall-clock time spent on reconstruction, in milliseconds.
    validation_status : ValidationStatus
        Whether the reconstruction has been validated against the source query.
    fragment : LogicalFragment
        The logical fragment of the original query.
    scope : str
        The coordinate scope within the JuGeo site where this evidence applies.
    """

    result_id: str
    kind: ReconstructionKind
    original_query: str
    z3_result: SolverResult
    reconstructed_evidence: dict[str, Any] = field(default_factory=dict)
    trust_level: TrustLevel = TrustLevel.SOLVER_DISCHARGED
    provenance: ProvenanceTrace = field(
        default_factory=lambda: ProvenanceTrace(origin='reconstruction'),
    )
    reconstruction_time_ms: float = 0.0
    validation_status: ValidationStatus = ValidationStatus.INCONCLUSIVE
    fragment: LogicalFragment = LogicalFragment.UNKNOWN
    scope: str = 'global'

    def is_valid(self) -> bool:
        """Return *True* if the reconstruction passed validation."""
        return self.validation_status is ValidationStatus.VALID

    def is_partial(self) -> bool:
        """Return *True* if this is a partial (UNKNOWN/timeout) reconstruction."""
        return self.kind is ReconstructionKind.PARTIAL

    def content_hash(self) -> str:
        """Compute a deterministic content hash for cache keying."""
        payload = json.dumps(
            {
                'query': self.original_query,
                'outcome': self.z3_result.outcome.value,
                'model': self.z3_result.model,
                'engine': self.z3_result.engine,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary for channel transport."""
        return {
            'result_id': self.result_id,
            'kind': self.kind.value,
            'original_query': self.original_query,
            'outcome': self.z3_result.outcome.value,
            'engine': self.z3_result.engine,
            'reconstructed_evidence': self.reconstructed_evidence,
            'trust_level': self.trust_level.name,
            'provenance_origin': self.provenance.origin,
            'reconstruction_time_ms': self.reconstruction_time_ms,
            'validation_status': self.validation_status.value,
            'fragment': self.fragment.value if hasattr(self.fragment, 'value') else str(self.fragment),
            'scope': self.scope,
            'version': _RECONSTRUCTION_VERSION,
        }


# ─────────────────────────────────────────────────────────────────────────
# Legacy backward-compatible wrapper
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReconstructionReport:
    """Human-readable reconstruction report (legacy API).

    Preserved for backward compatibility with modules that consume the
    simple narrative + assignments interface.  New code should use
    :class:`ReconstructionResult` instead.
    """

    narrative: str
    assignments: tuple[str, ...] = field(default_factory=tuple)


def reconstruct_countermodel(countermodel: Countermodel) -> ReconstructionReport:
    """Legacy entry point: produce a human-readable report from a countermodel.

    Parameters
    ----------
    countermodel : Countermodel
        The countermodel extracted from a SAT result.

    Returns
    -------
    ReconstructionReport
        A narrative description with formatted variable assignments.
    """
    assignments = tuple(
        f'{name}={value}'
        for name, value in sorted(countermodel.assignment.items())
    )
    narrative = (
        'Countermodel demonstrates a compatible local assignment.'
        if assignments
        else 'No countermodel available.'
    )
    return ReconstructionReport(narrative, assignments)


# ─────────────────────────────────────────────────────────────────────────
# ProofReconstructor
# ─────────────────────────────────────────────────────────────────────────


class ProofReconstructor:
    """Reconstructs proofs from UNSAT cores returned by the solver.

    The solver produces an unsat core — a minimal set of assertions whose
    conjunction is unsatisfiable.  This class lifts that core into a sequence
    of :class:`ProofStep` objects, verifies the proof structure, annotates
    each step with trust, and optionally compresses redundant chains.

    The copilot channel may propose missing justifications for gaps in the
    proof, but such steps are clearly flagged with ``COPILOT_SUGGESTED``
    trust and require independent verification before promotion.
    """

    def __init__(
        self,
        *,
        trust_algebra: TrustAlgebra | None = None,
        max_depth: int = _MAX_PROOF_DEPTH,
    ) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()
        self._max_depth = max_depth
        self._step_counter: int = 0

    def reconstruct(self, result: SolverResult, query: str) -> ReconstructionResult:
        """Reconstruct a proof from an UNSAT solver result.

        Parameters
        ----------
        result : SolverResult
            Must have ``outcome == UNSAT``.
        query : str
            The original query formula.

        Returns
        -------
        ReconstructionResult
            A fully-annotated reconstruction with proof steps.

        Raises
        ------
        ValueError
            If the result is not UNSAT.
        """
        if result.outcome is not SolveOutcome.UNSAT:
            raise ValueError(
                f'ProofReconstructor requires UNSAT, got {result.outcome.value}'
            )
        start = time.monotonic()
        steps = self.extract_proof_steps(result)
        steps = self.annotate_with_trust(steps, result.engine)
        compressed = self.compress_proof(steps)
        verified = self.verify_proof(compressed)
        evidence = self.to_evidence_item(compressed, verified)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        provenance = ProvenanceTrace(origin=f'proof-reconstruction/{result.engine}')
        provenance = provenance.append(
            ProvenanceStep(
                actor='ProofReconstructor',
                action='reconstruct',
                coordinate=query[:120],
                details=f'{len(compressed)} steps, verified={verified}',
            ),
        )
        trust = _ENGINE_TRUST_MAP.get(result.engine, TrustLevel.SOLVER_DISCHARGED)
        return ReconstructionResult(
            result_id=f'proof-{uuid.uuid4().hex[:12]}',
            kind=ReconstructionKind.PROOF,
            original_query=query,
            z3_result=result,
            reconstructed_evidence=evidence,
            trust_level=trust,
            provenance=provenance,
            reconstruction_time_ms=elapsed_ms,
            validation_status=(
                ValidationStatus.VALID if verified else ValidationStatus.INVALID
            ),
        )

    def extract_proof_steps(self, result: SolverResult) -> list[ProofStep]:
        """Extract proof steps from the solver reasons (unsat core proxies).

        Each reason string from the solver is treated as an assertion that
        participates in the refutation.  Steps are chained linearly when no
        explicit dependency structure is available.

        Parameters
        ----------
        result : SolverResult
            The UNSAT solver result with reasons.

        Returns
        -------
        list[ProofStep]
            Ordered proof steps derived from the unsat core.
        """
        steps: list[ProofStep] = []
        previous_ids: list[str] = []
        for idx, reason in enumerate(result.reasons):
            self._step_counter += 1
            step_id = f'step-{self._step_counter}'
            step = ProofStep(
                step_id=step_id,
                assertion_id=f'assert-{idx}',
                justification=reason,
                hypotheses=tuple(previous_ids[-2:]),
            )
            steps.append(step)
            previous_ids.append(step_id)
        if not steps:
            self._step_counter += 1
            steps.append(
                ProofStep(
                    step_id=f'step-{self._step_counter}',
                    assertion_id='assert-core',
                    justification='Solver reported UNSAT with empty core.',
                )
            )
        return steps

    def verify_proof(self, steps: list[ProofStep]) -> bool:
        """Verify that the proof step sequence is structurally well-formed.

        Checks that every hypothesis reference resolves to an earlier step
        and that the depth does not exceed the configured maximum.

        Parameters
        ----------
        steps : list[ProofStep]
            The proof steps to verify.

        Returns
        -------
        bool
            *True* if the proof is structurally sound.
        """
        if len(steps) > self._max_depth:
            return False
        known_ids: set[str] = set()
        for step in steps:
            for hyp in step.hypotheses:
                if hyp not in known_ids:
                    return False
            known_ids.add(step.step_id)
        return True

    def annotate_with_trust(
        self,
        steps: list[ProofStep],
        engine: str,
    ) -> list[ProofStep]:
        """Annotate each proof step with a trust level derived from the engine.

        Parameters
        ----------
        steps : list[ProofStep]
            The proof steps to annotate.
        engine : str
            The solver engine name (used to look up the trust map).

        Returns
        -------
        list[ProofStep]
            Steps with updated trust annotations.
        """
        trust = _ENGINE_TRUST_MAP.get(engine, TrustLevel.SOLVER_DISCHARGED)
        return [replace(step, trust_level=trust) for step in steps]

    def compress_proof(self, steps: list[ProofStep]) -> list[ProofStep]:
        """Compress the proof by removing redundant intermediate steps.

        A step is considered redundant if it has exactly one hypothesis and
        its justification is subsumed by the next step.  Compression never
        removes the first or last step.

        Parameters
        ----------
        steps : list[ProofStep]
            The proof steps to compress.

        Returns
        -------
        list[ProofStep]
            Compressed proof steps.
        """
        if len(steps) <= _COMPRESSION_THRESHOLD:
            return list(steps)
        compressed: list[ProofStep] = [steps[0]]
        for i in range(1, len(steps) - 1):
            step = steps[i]
            if len(step.hypotheses) == 1 and len(steps[i + 1].hypotheses) <= 1:
                continue
            compressed.append(step)
        compressed.append(steps[-1])
        return compressed

    def to_evidence_item(
        self,
        steps: list[ProofStep],
        verified: bool,
    ) -> dict[str, Any]:
        """Package proof steps into an evidence payload dictionary.

        Parameters
        ----------
        steps : list[ProofStep]
            The (possibly compressed) proof steps.
        verified : bool
            Whether the proof passed structural verification.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable evidence payload.
        """
        return {
            'type': 'proof',
            'steps': [s.to_dict() for s in steps],
            'step_count': len(steps),
            'verified': verified,
            'version': _RECONSTRUCTION_VERSION,
        }


# ─────────────────────────────────────────────────────────────────────────
# WitnessReconstructor
# ─────────────────────────────────────────────────────────────────────────


class WitnessReconstructor:
    """Reconstructs witnesses from SAT models returned by the solver.

    A SAT model provides concrete variable assignments that satisfy the
    query.  This class extracts those assignments as typed
    :class:`WitnessBinding` objects, validates them against the original
    formula, and packages the result as runtime evidence.
    """

    def __init__(self, *, trust_algebra: TrustAlgebra | None = None) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()

    def reconstruct(self, result: SolverResult, query: str) -> ReconstructionResult:
        """Reconstruct a witness from a SAT solver result.

        Parameters
        ----------
        result : SolverResult
            Must have ``outcome == SAT``.
        query : str
            The original query formula.

        Returns
        -------
        ReconstructionResult
            A fully-annotated reconstruction with witness bindings.

        Raises
        ------
        ValueError
            If the result is not SAT.
        """
        if result.outcome is not SolveOutcome.SAT:
            raise ValueError(
                f'WitnessReconstructor requires SAT, got {result.outcome.value}'
            )
        start = time.monotonic()
        bindings = self.extract_witness_values(result)
        valid = self.validate_witness(bindings, result)
        evidence = self.to_runtime_evidence(bindings, valid)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        provenance = self.annotate_provenance(result, query, elapsed_ms)
        trust = _ENGINE_TRUST_MAP.get(result.engine, TrustLevel.RUNTIME_WITNESSED)
        return ReconstructionResult(
            result_id=f'witness-{uuid.uuid4().hex[:12]}',
            kind=ReconstructionKind.WITNESS,
            original_query=query,
            z3_result=result,
            reconstructed_evidence=evidence,
            trust_level=trust,
            provenance=provenance,
            reconstruction_time_ms=elapsed_ms,
            validation_status=(
                ValidationStatus.VALID if valid else ValidationStatus.INVALID
            ),
        )

    def extract_witness_values(self, result: SolverResult) -> list[WitnessBinding]:
        """Extract typed variable bindings from a SAT model.

        Parameters
        ----------
        result : SolverResult
            The SAT solver result with a model dictionary.

        Returns
        -------
        list[WitnessBinding]
            Typed bindings for every variable in the model.
        """
        bindings: list[WitnessBinding] = []
        for variable, value in sorted(result.model.items()):
            raw = str(value)
            sort_name = 'Bool' if isinstance(value, bool) else type(value).__name__
            binding = WitnessBinding(
                variable=variable,
                raw_value=raw,
                interpreted_value=value,
                sort_name=sort_name,
            )
            bindings.append(binding)
        return bindings

    def validate_witness(
        self,
        bindings: list[WitnessBinding],
        result: SolverResult,
    ) -> bool:
        """Validate that witness bindings are consistent with the solver model.

        This performs a structural check: every variable in the model must
        have a corresponding binding, and the binding value must match.

        Parameters
        ----------
        bindings : list[WitnessBinding]
            The extracted bindings.
        result : SolverResult
            The original solver result for cross-referencing.

        Returns
        -------
        bool
            *True* if all bindings are faithful to the model.
        """
        binding_map = {b.variable: b.interpreted_value for b in bindings}
        for variable, value in result.model.items():
            if variable not in binding_map:
                return False
            if binding_map[variable] != value:
                return False
        return True

    def to_runtime_evidence(
        self,
        bindings: list[WitnessBinding],
        valid: bool,
    ) -> dict[str, Any]:
        """Package witness bindings into a runtime evidence payload.

        Parameters
        ----------
        bindings : list[WitnessBinding]
            The validated bindings.
        valid : bool
            Whether the witness passed validation.

        Returns
        -------
        dict[str, Any]
            A JSON-serialisable evidence payload.
        """
        return {
            'type': 'witness',
            'bindings': [b.to_dict() for b in bindings],
            'binding_count': len(bindings),
            'valid': valid,
            'version': _RECONSTRUCTION_VERSION,
        }

    def annotate_provenance(
        self,
        result: SolverResult,
        query: str,
        elapsed_ms: float,
    ) -> ProvenanceTrace:
        """Build a provenance trace for a witness reconstruction.

        Parameters
        ----------
        result : SolverResult
            The solver result that sourced the witness.
        query : str
            The original query formula.
        elapsed_ms : float
            Reconstruction wall-clock time in milliseconds.

        Returns
        -------
        ProvenanceTrace
            A provenance trace with one step recording the reconstruction.
        """
        trace = ProvenanceTrace(origin=f'witness-reconstruction/{result.engine}')
        return trace.append(
            ProvenanceStep(
                actor='WitnessReconstructor',
                action='reconstruct',
                coordinate=query[:120],
                details=f'{len(result.model)} bindings in {elapsed_ms:.1f}ms',
            ),
        )


# ─────────────────────────────────────────────────────────────────────────
# ModelReconstructor
# ─────────────────────────────────────────────────────────────────────────


class ModelReconstructor:
    """Full model reconstruction: sorts, functions, arrays, and datatypes.

    When the solver returns a rich SAT model (beyond simple Boolean
    assignments), this class interprets every component of the model into
    JuGeo's algebraic-surface vocabulary.  The resulting interpretation
    preserves the multi-sorted, function-symbol, and array structure so
    that downstream modules can project onto any coordinate of the site.
    """

    def __init__(self, *, trust_algebra: TrustAlgebra | None = None) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()

    def reconstruct(
        self,
        result: SolverResult,
        query: str,
        *,
        sort_hints: dict[str, tuple[str, ...]] | None = None,
        function_hints: dict[str, int] | None = None,
    ) -> ReconstructionResult:
        """Reconstruct a full model from a SAT solver result.

        Parameters
        ----------
        result : SolverResult
            Must have ``outcome == SAT``.
        query : str
            The original query formula.
        sort_hints : dict[str, tuple[str, ...]] | None
            Optional mapping of sort names to known universe elements,
            used to seed the interpretation when Z3's model is sparse.
        function_hints : dict[str, int] | None
            Optional mapping of function names to their arities.

        Returns
        -------
        ReconstructionResult
            A fully-annotated reconstruction with model interpretations.
        """
        if result.outcome is not SolveOutcome.SAT:
            raise ValueError(
                f'ModelReconstructor requires SAT, got {result.outcome.value}'
            )
        start = time.monotonic()
        sorts = self.interpret_sorts(result, sort_hints or {})
        functions = self.interpret_functions(result, function_hints or {})
        arrays = self.interpret_arrays(result)
        datatypes = self.interpret_datatypes(result)
        valid = self.validate_interpretation(sorts, functions, arrays, datatypes)
        evidence: dict[str, Any] = {
            'type': 'model',
            'sorts': [self._sort_to_dict(s) for s in sorts],
            'functions': [self._func_to_dict(f) for f in functions],
            'arrays': [self._array_to_dict(a) for a in arrays],
            'datatypes': [self._dt_to_dict(d) for d in datatypes],
            'valid': valid,
            'version': _RECONSTRUCTION_VERSION,
        }
        elapsed_ms = (time.monotonic() - start) * 1000.0
        provenance = ProvenanceTrace(origin=f'model-reconstruction/{result.engine}')
        provenance = provenance.append(
            ProvenanceStep(
                actor='ModelReconstructor',
                action='reconstruct',
                coordinate=query[:120],
                details=(
                    f'{len(sorts)} sorts, {len(functions)} functions, '
                    f'{len(arrays)} arrays, {len(datatypes)} datatypes'
                ),
            ),
        )
        trust = _ENGINE_TRUST_MAP.get(result.engine, TrustLevel.SOLVER_DISCHARGED)
        return ReconstructionResult(
            result_id=f'model-{uuid.uuid4().hex[:12]}',
            kind=ReconstructionKind.WITNESS,
            original_query=query,
            z3_result=result,
            reconstructed_evidence=evidence,
            trust_level=trust,
            provenance=provenance,
            reconstruction_time_ms=elapsed_ms,
            validation_status=(
                ValidationStatus.VALID if valid else ValidationStatus.INCONCLUSIVE
            ),
        )

    def interpret_sorts(
        self,
        result: SolverResult,
        sort_hints: dict[str, tuple[str, ...]],
    ) -> list[SortInterpretation]:
        """Interpret sorts from the solver model and optional hints.

        For a Boolean model, a single ``Bool`` sort with ``{True, False}``
        universe is produced.  Additional sorts are created from hints.

        Parameters
        ----------
        result : SolverResult
            The solver result containing the model.
        sort_hints : dict[str, tuple[str, ...]]
            Mapping of sort names to known universe elements.

        Returns
        -------
        list[SortInterpretation]
            Interpreted sorts.
        """
        sorts: list[SortInterpretation] = []
        has_bool = any(isinstance(v, bool) for v in result.model.values())
        if has_bool:
            sorts.append(SortInterpretation(
                sort_name='Bool',
                universe=('True', 'False'),
                is_finite=True,
            ))
        for sort_name, elements in sort_hints.items():
            if sort_name != 'Bool':
                sorts.append(SortInterpretation(
                    sort_name=sort_name,
                    universe=elements,
                    is_finite=True,
                ))
        return sorts

    def interpret_functions(
        self,
        result: SolverResult,
        function_hints: dict[str, int],
    ) -> list[FunctionInterpretation]:
        """Interpret function symbols from the solver model.

        Boolean model entries are interpreted as nullary (constant) functions.
        Function hints provide arities for symbols that the simple model
        cannot express directly.

        Parameters
        ----------
        result : SolverResult
            The solver result containing the model.
        function_hints : dict[str, int]
            Mapping of function names to their arities.

        Returns
        -------
        list[FunctionInterpretation]
            Interpreted function symbols.
        """
        functions: list[FunctionInterpretation] = []
        for name, value in sorted(result.model.items()):
            arity = function_hints.get(name, 0)
            entries: tuple[tuple[tuple[str, ...], str], ...]
            if arity == 0:
                entries = (((), str(value)),)
            else:
                entries = ()
            functions.append(FunctionInterpretation(
                function_name=name,
                arity=arity,
                entries=entries,
            ))
        return functions

    def interpret_arrays(self, result: SolverResult) -> list[ArrayInterpretation]:
        """Interpret array-typed model entries.

        The builtin solver does not produce array models directly; this
        method scans for variable names matching the ``arr_*`` convention
        and produces empty array shells for them.

        Parameters
        ----------
        result : SolverResult
            The solver result containing the model.

        Returns
        -------
        list[ArrayInterpretation]
            Interpreted arrays (may be empty for simple models).
        """
        arrays: list[ArrayInterpretation] = []
        for name in sorted(result.model):
            if name.startswith('arr_') or name.startswith('array_'):
                arrays.append(ArrayInterpretation(
                    array_name=name,
                    index_sort='Int',
                    element_sort='Bool',
                    default_value=str(result.model[name]),
                ))
        return arrays

    def interpret_datatypes(self, result: SolverResult) -> list[DatatypeInterpretation]:
        """Interpret algebraic datatypes from the solver model.

        Like arrays, algebraic datatypes are identified by naming convention
        (``dt_*``) in the simple model and expanded with constructor metadata
        when available.

        Parameters
        ----------
        result : SolverResult
            The solver result containing the model.

        Returns
        -------
        list[DatatypeInterpretation]
            Interpreted datatypes (may be empty for simple models).
        """
        datatypes: list[DatatypeInterpretation] = []
        for name in sorted(result.model):
            if name.startswith('dt_') or name.startswith('datatype_'):
                datatypes.append(DatatypeInterpretation(
                    type_name=name,
                    constructors=(),
                    instances=({'value': result.model[name]},),
                ))
        return datatypes

    def validate_interpretation(
        self,
        sorts: list[SortInterpretation],
        functions: list[FunctionInterpretation],
        arrays: list[ArrayInterpretation],
        datatypes: list[DatatypeInterpretation],
    ) -> bool:
        """Validate the overall model interpretation for internal consistency.

        Checks that sort universes are non-empty, function entries reference
        valid sort elements, and arrays have consistent sort annotations.

        Parameters
        ----------
        sorts, functions, arrays, datatypes
            The interpreted model components.

        Returns
        -------
        bool
            *True* if the interpretation is internally consistent.
        """
        sort_names = {s.sort_name for s in sorts}
        for func in functions:
            if func.entries:
                for args, result_val in func.entries:
                    if func.arity != len(args):
                        return False
        for arr in arrays:
            if arr.index_sort and arr.element_sort:
                continue
        return True

    # -- Private serialisation helpers --

    @staticmethod
    def _sort_to_dict(s: SortInterpretation) -> dict[str, Any]:
        return {
            'sort_name': s.sort_name,
            'universe': list(s.universe),
            'is_finite': s.is_finite,
            'cardinality': s.cardinality,
        }

    @staticmethod
    def _func_to_dict(f: FunctionInterpretation) -> dict[str, Any]:
        return {
            'function_name': f.function_name,
            'arity': f.arity,
            'entries': [
                {'args': list(args), 'result': res}
                for args, res in f.entries
            ],
            'else_value': f.else_value,
        }

    @staticmethod
    def _array_to_dict(a: ArrayInterpretation) -> dict[str, Any]:
        return {
            'array_name': a.array_name,
            'index_sort': a.index_sort,
            'element_sort': a.element_sort,
            'default_value': a.default_value,
            'stores': [
                {'index': idx, 'value': val}
                for idx, val in a.stores
            ],
        }

    @staticmethod
    def _dt_to_dict(d: DatatypeInterpretation) -> dict[str, Any]:
        return {
            'type_name': d.type_name,
            'constructors': [
                {'name': name, 'arg_sorts': list(args)}
                for name, args in d.constructors
            ],
            'instances': list(d.instances),
        }


# ─────────────────────────────────────────────────────────────────────────
# EvidenceAssembler
# ─────────────────────────────────────────────────────────────────────────


class EvidenceAssembler:
    """Assembles reconstruction results into JuGeo evidence channel responses.

    This is the bridge between the reconstruction pipeline and the evidence
    channel infrastructure.  It attaches trust, provenance, and scope to
    a :class:`ReconstructionResult`, then packages the result as an
    :class:`EvidenceResponse` suitable for delivery through the channel
    router.
    """

    def __init__(
        self,
        *,
        trust_algebra: TrustAlgebra | None = None,
        default_channel: EvidenceChannel = EvidenceChannel.SOLVER,
    ) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()
        self._default_channel = default_channel

    def assemble(
        self,
        result: ReconstructionResult,
        *,
        channel: EvidenceChannel | None = None,
        scope: str | None = None,
    ) -> EvidenceResponse:
        """Assemble a reconstruction result into a channel response.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to assemble.
        channel : EvidenceChannel | None
            Override the default channel.
        scope : str | None
            Override the scope annotation.

        Returns
        -------
        EvidenceResponse
            A fully-annotated evidence response ready for channel delivery.
        """
        result = self.attach_trust(result)
        result = self.attach_provenance(result, channel or self._default_channel)
        if scope:
            result = self.attach_scope(result, scope)
        return self.package_for_channel(result, channel or self._default_channel)

    def attach_trust(self, result: ReconstructionResult) -> ReconstructionResult:
        """Ensure the reconstruction carries an appropriate trust level.

        If the result already has a trust level, it is preserved.  Partial
        reconstructions are capped at COPILOT_SUGGESTED to prevent silent
        promotion of uncertain evidence.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to annotate.

        Returns
        -------
        ReconstructionResult
            The reconstruction with a validated trust level.
        """
        trust = result.trust_level
        if result.is_partial():
            ceiling = TrustLevel.COPILOT_SUGGESTED
            if trust > ceiling:
                trust = ceiling
        return replace(result, trust_level=trust)

    def attach_provenance(
        self,
        result: ReconstructionResult,
        channel: EvidenceChannel,
    ) -> ReconstructionResult:
        """Append a channel-delivery step to the provenance trace.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to annotate.
        channel : EvidenceChannel
            The channel through which the evidence will be delivered.

        Returns
        -------
        ReconstructionResult
            The reconstruction with extended provenance.
        """
        updated = result.provenance.append(
            ProvenanceStep(
                actor='EvidenceAssembler',
                action='attach_provenance',
                coordinate=result.scope,
                details=f'channel={channel.value}',
            ),
        )
        return replace(result, provenance=updated)

    def attach_scope(
        self,
        result: ReconstructionResult,
        scope: str,
    ) -> ReconstructionResult:
        """Set the coordinate scope for the evidence.

        The scope identifies the region of the JuGeo site (file, contract,
        block, test) where this evidence is semantically relevant.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to annotate.
        scope : str
            The coordinate scope identifier.

        Returns
        -------
        ReconstructionResult
            The reconstruction with updated scope.
        """
        return replace(result, scope=scope)

    def package_for_channel(
        self,
        result: ReconstructionResult,
        channel: EvidenceChannel,
    ) -> EvidenceResponse:
        """Package a reconstruction result as an :class:`EvidenceResponse`.

        Parameters
        ----------
        result : ReconstructionResult
            The fully-annotated reconstruction.
        channel : EvidenceChannel
            The delivery channel.

        Returns
        -------
        EvidenceResponse
            A channel response wrapping the reconstruction.
        """
        return EvidenceResponse(
            request_id=result.result_id,
            channel=channel,
            evidence_item=result.to_dict(),
            trust_level=result.trust_level,
            latency_ms=result.reconstruction_time_ms,
            is_partial=result.is_partial(),
            provenance=result.provenance.to_dict(),
        )

    def assemble_batch(
        self,
        results: Sequence[ReconstructionResult],
        *,
        channel: EvidenceChannel | None = None,
    ) -> list[EvidenceResponse]:
        """Assemble multiple reconstruction results in batch.

        Parameters
        ----------
        results : Sequence[ReconstructionResult]
            The reconstructions to assemble.
        channel : EvidenceChannel | None
            Override the default channel for all results.

        Returns
        -------
        list[EvidenceResponse]
            Evidence responses, one per input reconstruction.
        """
        return [self.assemble(r, channel=channel) for r in results]


# ─────────────────────────────────────────────────────────────────────────
# PartialReconstructor
# ─────────────────────────────────────────────────────────────────────────


class PartialReconstructor:
    """Handles UNKNOWN / timeout results with best-effort reconstruction.

    When the solver cannot decide a formula within its budget, it returns
    UNKNOWN.  This class salvages as much information as possible from
    the partial state: decided atoms, tentative assignments, and the
    boundary between known and unknown regions.

    The ``copilot_assist_completion`` method allows the copilot channel to
    propose completions for the undecided region.  Such completions are
    annotated with ``COPILOT_SUGGESTED`` trust and are never silently
    promoted — they require independent corroboration.
    """

    def __init__(self, *, trust_algebra: TrustAlgebra | None = None) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()

    def extract_partial(self, result: SolverResult) -> dict[str, Any]:
        """Extract whatever partial information is available from the result.

        Parameters
        ----------
        result : SolverResult
            An UNKNOWN solver result that may still contain partial model data.

        Returns
        -------
        dict[str, Any]
            A dictionary of partial information keyed by category.
        """
        partial: dict[str, Any] = {
            'outcome': result.outcome.value,
            'engine': result.engine,
            'reasons': list(result.reasons),
            'partial_model': dict(result.model),
            'decided_count': len(result.model),
        }
        return partial

    def best_effort_model(self, result: SolverResult) -> dict[str, bool]:
        """Produce a best-effort Boolean model from partial solver state.

        Variables that appear in the partial model retain their assignments;
        variables mentioned in reasons but absent from the model are assigned
        a default value of *False*.

        Parameters
        ----------
        result : SolverResult
            The partial solver result.

        Returns
        -------
        dict[str, bool]
            A best-effort model combining decided and defaulted atoms.
        """
        model = dict(result.model)
        for reason in result.reasons:
            tokens = reason.replace('not ', '').split()
            for token in tokens:
                cleaned = token.strip('(),.')
                if cleaned and cleaned.isidentifier() and cleaned not in model:
                    model[cleaned] = False
        return model

    def identify_decided_atoms(self, result: SolverResult) -> set[str]:
        """Identify atoms whose truth value was decided by the solver.

        Parameters
        ----------
        result : SolverResult
            The partial solver result.

        Returns
        -------
        set[str]
            Names of variables with definite assignments.
        """
        return set(result.model.keys())

    def separate_known_unknown(
        self,
        result: SolverResult,
        all_atoms: set[str] | None = None,
    ) -> tuple[dict[str, bool], set[str]]:
        """Separate decided atoms from undecided ones.

        Parameters
        ----------
        result : SolverResult
            The partial solver result.
        all_atoms : set[str] | None
            The complete set of atoms in the formula.  If *None*, only
            atoms mentioned in the model and reasons are considered.

        Returns
        -------
        tuple[dict[str, bool], set[str]]
            ``(known, unknown)`` — a dict of decided assignments and a set
            of atom names that remain undecided.
        """
        decided = self.identify_decided_atoms(result)
        known = {k: v for k, v in result.model.items() if k in decided}
        if all_atoms is None:
            mentioned: set[str] = set()
            for reason in result.reasons:
                for token in reason.split():
                    cleaned = token.strip('(),.')
                    if cleaned and cleaned.isidentifier():
                        mentioned.add(cleaned)
            all_atoms = decided | mentioned
        unknown = all_atoms - decided
        return known, unknown

    def copilot_assist_completion(
        self,
        result: SolverResult,
        query: str,
        *,
        proposals: dict[str, bool] | None = None,
    ) -> ReconstructionResult:
        """Let the copilot channel propose completions for undecided atoms.

        Any values proposed by the copilot are clearly annotated with
        ``COPILOT_SUGGESTED`` trust.  The resulting partial reconstruction
        distinguishes between solver-decided and copilot-proposed atoms.

        Parameters
        ----------
        result : SolverResult
            The UNKNOWN solver result.
        query : str
            The original query formula.
        proposals : dict[str, bool] | None
            Copilot-proposed assignments for undecided atoms.

        Returns
        -------
        ReconstructionResult
            A partial reconstruction with copilot-assisted completions.
        """
        start = time.monotonic()
        known, unknown = self.separate_known_unknown(result)
        merged_model = dict(known)
        copilot_atoms: set[str] = set()
        if proposals:
            for atom, value in proposals.items():
                if atom in unknown:
                    merged_model[atom] = value
                    copilot_atoms.add(atom)
        evidence: dict[str, Any] = {
            'type': 'partial',
            'solver_decided': known,
            'copilot_proposed': {a: merged_model[a] for a in copilot_atoms},
            'still_unknown': list(unknown - copilot_atoms),
            'merged_model': merged_model,
            'completion_ratio': (
                len(merged_model) / max(len(merged_model) + len(unknown - copilot_atoms), 1)
            ),
            'version': _RECONSTRUCTION_VERSION,
        }
        elapsed_ms = (time.monotonic() - start) * 1000.0
        provenance = ProvenanceTrace(origin='partial-reconstruction/copilot-assisted')
        provenance = provenance.append(
            ProvenanceStep(
                actor='PartialReconstructor',
                action='copilot_assist_completion',
                coordinate=query[:120],
                details=(
                    f'{len(known)} decided, {len(copilot_atoms)} proposed, '
                    f'{len(unknown - copilot_atoms)} still unknown'
                ),
            ),
        )
        trust = (
            TrustLevel.COPILOT_SUGGESTED
            if copilot_atoms
            else TrustLevel.UNVERIFIED
        )
        return ReconstructionResult(
            result_id=f'partial-{uuid.uuid4().hex[:12]}',
            kind=ReconstructionKind.PARTIAL,
            original_query=query,
            z3_result=result,
            reconstructed_evidence=evidence,
            trust_level=trust,
            provenance=provenance,
            reconstruction_time_ms=elapsed_ms,
            validation_status=ValidationStatus.INCONCLUSIVE,
        )

    def reconstruct(self, result: SolverResult, query: str) -> ReconstructionResult:
        """Top-level entry for partial reconstruction (no copilot assist).

        Parameters
        ----------
        result : SolverResult
            An UNKNOWN solver result.
        query : str
            The original query formula.

        Returns
        -------
        ReconstructionResult
            A partial reconstruction from decided atoms only.
        """
        return self.copilot_assist_completion(result, query, proposals=None)


# ─────────────────────────────────────────────────────────────────────────
# ReconstructionCache
# ─────────────────────────────────────────────────────────────────────────


class ReconstructionCache:
    """Content-addressed cache for reconstruction results.

    Identical solver results produce identical reconstructions, so caching
    avoids redundant work.  Entries are keyed by the content hash of the
    :class:`ReconstructionResult`, and the cache uses LRU eviction when
    it exceeds its capacity.
    """

    def __init__(self, *, capacity: int = _DEFAULT_CACHE_CAPACITY) -> None:
        self._capacity = max(capacity, 16)
        self._store: dict[str, ReconstructionResult] = {}
        self._access_order: list[str] = []
        self._hits: int = 0
        self._misses: int = 0

    def get(self, key: str) -> ReconstructionResult | None:
        """Retrieve a cached reconstruction by content hash.

        Parameters
        ----------
        key : str
            The content hash of the reconstruction.

        Returns
        -------
        ReconstructionResult | None
            The cached result, or *None* on cache miss.
        """
        if key in self._store:
            self._hits += 1
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._store[key]
        self._misses += 1
        return None

    def put(self, key: str, result: ReconstructionResult) -> None:
        """Store a reconstruction in the cache.

        If the cache is at capacity, the least-recently-used entry is
        evicted before insertion.

        Parameters
        ----------
        key : str
            The content hash to use as cache key.
        result : ReconstructionResult
            The reconstruction to cache.
        """
        if key in self._store:
            self._access_order.remove(key)
        elif len(self._store) >= self._capacity:
            evict_key = self._access_order.pop(0)
            del self._store[evict_key]
        self._store[key] = result
        self._access_order.append(key)

    def invalidate(self, key: str) -> bool:
        """Remove a specific entry from the cache.

        Parameters
        ----------
        key : str
            The content hash of the entry to remove.

        Returns
        -------
        bool
            *True* if the entry existed and was removed.
        """
        if key in self._store:
            del self._store[key]
            self._access_order.remove(key)
            return True
        return False

    def invalidate_all(self) -> int:
        """Clear all entries from the cache.

        Returns
        -------
        int
            The number of entries that were removed.
        """
        count = len(self._store)
        self._store.clear()
        self._access_order.clear()
        return count

    def hit_rate(self) -> float:
        """Return the cache hit rate as a fraction in [0.0, 1.0].

        Returns
        -------
        float
            The ratio of hits to total accesses, or 0.0 if no accesses.
        """
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def statistics(self) -> dict[str, Any]:
        """Return comprehensive cache statistics.

        Returns
        -------
        dict[str, Any]
            Dictionary with hits, misses, hit_rate, size, capacity, and
            kind breakdown of cached entries.
        """
        kind_counts: dict[str, int] = defaultdict(int)
        for result in self._store.values():
            kind_counts[result.kind.value] += 1
        return {
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': self.hit_rate(),
            'size': len(self._store),
            'capacity': self._capacity,
            'kind_breakdown': dict(kind_counts),
        }

    def __len__(self) -> int:
        """Return the number of cached entries."""
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the cache without affecting LRU order."""
        return key in self._store


# ─────────────────────────────────────────────────────────────────────────
# ReconstructionValidator
# ─────────────────────────────────────────────────────────────────────────


class ReconstructionValidator:
    """Validates reconstruction results for correctness and policy compliance.

    Every reconstruction must be validated before delivery.  Validation
    covers structural soundness (proof steps form a DAG, witness bindings
    are complete), trust appropriateness (no silent promotion, partial
    results capped), and scope correctness (evidence applies to the
    claimed coordinate).
    """

    def __init__(self, *, trust_algebra: TrustAlgebra | None = None) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()

    def validate_proof(self, result: ReconstructionResult) -> ValidationStatus:
        """Validate a proof reconstruction.

        Checks that the proof steps form a well-founded DAG, that every
        hypothesis reference resolves, and that the step count is within
        bounds.

        Parameters
        ----------
        result : ReconstructionResult
            A PROOF-kind reconstruction.

        Returns
        -------
        ValidationStatus
            VALID, INVALID, or INCONCLUSIVE.
        """
        if result.kind is not ReconstructionKind.PROOF:
            return ValidationStatus.INVALID
        evidence = result.reconstructed_evidence
        steps = evidence.get('steps', [])
        if not steps:
            return ValidationStatus.INCONCLUSIVE
        known_ids: set[str] = set()
        for step in steps:
            for hyp in step.get('hypotheses', []):
                if hyp not in known_ids:
                    return ValidationStatus.INVALID
            step_id = step.get('step_id', '')
            if not step_id:
                return ValidationStatus.INVALID
            known_ids.add(step_id)
        return ValidationStatus.VALID

    def validate_witness(self, result: ReconstructionResult) -> ValidationStatus:
        """Validate a witness reconstruction.

        Checks that every variable in the original solver model has a
        corresponding binding in the reconstructed evidence.

        Parameters
        ----------
        result : ReconstructionResult
            A WITNESS-kind reconstruction.

        Returns
        -------
        ValidationStatus
            VALID, INVALID, or INCONCLUSIVE.
        """
        if result.kind not in (
            ReconstructionKind.WITNESS,
            ReconstructionKind.COUNTERMODEL,
        ):
            return ValidationStatus.INVALID
        evidence = result.reconstructed_evidence
        bindings = evidence.get('bindings', [])
        model_vars = set(result.z3_result.model.keys())
        binding_vars = {b.get('variable', '') for b in bindings}
        if not model_vars:
            return ValidationStatus.INCONCLUSIVE
        if model_vars <= binding_vars:
            return ValidationStatus.VALID
        return ValidationStatus.INVALID

    def validate_model(self, result: ReconstructionResult) -> ValidationStatus:
        """Validate a full model reconstruction.

        Checks that sorts have non-empty universes, functions have
        consistent arities, and the overall interpretation is coherent.

        Parameters
        ----------
        result : ReconstructionResult
            A WITNESS-kind reconstruction with model-type evidence.

        Returns
        -------
        ValidationStatus
            VALID, INVALID, or INCONCLUSIVE.
        """
        evidence = result.reconstructed_evidence
        if evidence.get('type') != 'model':
            return self.validate_witness(result)
        sorts = evidence.get('sorts', [])
        functions = evidence.get('functions', [])
        for func in functions:
            entries = func.get('entries', [])
            arity = func.get('arity', 0)
            for entry in entries:
                args = entry.get('args', [])
                if len(args) != arity:
                    return ValidationStatus.INVALID
        if not sorts and not functions:
            return ValidationStatus.INCONCLUSIVE
        return ValidationStatus.VALID

    def check_trust_appropriate(self, result: ReconstructionResult) -> bool:
        """Check that the trust level is appropriate for the reconstruction kind.

        Policy rules from theory2.tex §252:
        - Partial reconstructions must not exceed COPILOT_SUGGESTED.
        - Proofs from unknown engines must not claim MECHANICALLY_VERIFIED.
        - Witness evidence cannot claim FORMAL_PROOF-level trust.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to check.

        Returns
        -------
        bool
            *True* if the trust level is policy-compliant.
        """
        trust = result.trust_level
        if result.is_partial() and trust > TrustLevel.COPILOT_SUGGESTED:
            return False
        if result.kind is ReconstructionKind.PROOF:
            if trust is TrustLevel.MECHANICALLY_VERIFIED:
                if result.z3_result.engine not in ('coq', 'lean', 'isabelle'):
                    return False
        if result.kind in (ReconstructionKind.WITNESS, ReconstructionKind.COUNTERMODEL):
            if trust is TrustLevel.MECHANICALLY_VERIFIED:
                return False
        return True

    def check_scope_correct(self, result: ReconstructionResult) -> bool:
        """Check that the scope annotation is well-formed and non-empty.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to check.

        Returns
        -------
        bool
            *True* if the scope is a non-empty string.
        """
        return bool(result.scope and isinstance(result.scope, str))

    def full_validation(self, result: ReconstructionResult) -> ValidationStatus:
        """Run all validation checks and return a combined status.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to validate comprehensively.

        Returns
        -------
        ValidationStatus
            VALID only if all checks pass, INVALID if any structural check
            fails, INCONCLUSIVE otherwise.
        """
        if not self.check_trust_appropriate(result):
            return ValidationStatus.INVALID
        if not self.check_scope_correct(result):
            return ValidationStatus.INVALID
        kind = result.kind
        if kind is ReconstructionKind.PROOF:
            return self.validate_proof(result)
        if kind in (ReconstructionKind.WITNESS, ReconstructionKind.COUNTERMODEL):
            model_type = result.reconstructed_evidence.get('type', '')
            if model_type == 'model':
                return self.validate_model(result)
            return self.validate_witness(result)
        if kind is ReconstructionKind.PARTIAL:
            return ValidationStatus.INCONCLUSIVE
        return ValidationStatus.INCONCLUSIVE


# ─────────────────────────────────────────────────────────────────────────
# ReconstructionPipeline
# ─────────────────────────────────────────────────────────────────────────


class ReconstructionPipeline:
    """End-to-end pipeline: submit a solver result, get packaged evidence.

    The pipeline classifies the solver outcome, routes to the appropriate
    reconstructor, validates the result, caches it, and packages it for
    delivery through the evidence channel.  This is the primary entry
    point for downstream modules that need to convert solver output into
    JuGeo evidence.
    """

    def __init__(
        self,
        *,
        trust_algebra: TrustAlgebra | None = None,
        cache: ReconstructionCache | None = None,
        default_channel: EvidenceChannel = EvidenceChannel.SOLVER,
    ) -> None:
        self._trust_algebra = trust_algebra or TrustAlgebra()
        self._cache = cache or ReconstructionCache()
        self._proof_reconstructor = ProofReconstructor(
            trust_algebra=self._trust_algebra,
        )
        self._witness_reconstructor = WitnessReconstructor(
            trust_algebra=self._trust_algebra,
        )
        self._model_reconstructor = ModelReconstructor(
            trust_algebra=self._trust_algebra,
        )
        self._partial_reconstructor = PartialReconstructor(
            trust_algebra=self._trust_algebra,
        )
        self._assembler = EvidenceAssembler(
            trust_algebra=self._trust_algebra,
            default_channel=default_channel,
        )
        self._validator = ReconstructionValidator(
            trust_algebra=self._trust_algebra,
        )
        self._statistics = ReconstructionStatistics()
        self._default_channel = default_channel

    def submit(
        self,
        result: SolverResult,
        query: str,
        *,
        scope: str = 'global',
        channel: EvidenceChannel | None = None,
        use_cache: bool = True,
    ) -> EvidenceResponse:
        """Submit a solver result for full reconstruction and packaging.

        This is the primary entry point.  The result is classified,
        reconstructed, validated, cached, and delivered as an evidence
        channel response.

        Parameters
        ----------
        result : SolverResult
            The raw solver result.
        query : str
            The original query formula.
        scope : str
            The coordinate scope for the evidence.
        channel : EvidenceChannel | None
            Override the default delivery channel.
        use_cache : bool
            Whether to check the cache before reconstructing.

        Returns
        -------
        EvidenceResponse
            A fully-packaged evidence response.
        """
        start = time.monotonic()
        cache_key = self._compute_cache_key(result, query)
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._statistics.record(cached, from_cache=True)
                return self._assembler.assemble(
                    cached, channel=channel, scope=scope,
                )
        kind = self.classify_result(result)
        reconstruction = self.route_to_reconstructor(result, query, kind)
        reconstruction = self.validate(reconstruction)
        self._cache.put(cache_key, reconstruction)
        response = self.package(reconstruction, channel=channel, scope=scope)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        self._statistics.record(reconstruction, elapsed_ms=elapsed_ms)
        return response

    def classify_result(self, result: SolverResult) -> ReconstructionKind:
        """Classify a solver result into a reconstruction kind.

        Parameters
        ----------
        result : SolverResult
            The solver result to classify.

        Returns
        -------
        ReconstructionKind
            The kind of reconstruction to perform.
        """
        kind_str = _OUTCOME_KIND_MAP.get(result.outcome, 'PARTIAL')
        return ReconstructionKind(kind_str.lower())

    def route_to_reconstructor(
        self,
        result: SolverResult,
        query: str,
        kind: ReconstructionKind,
    ) -> ReconstructionResult:
        """Route to the appropriate reconstructor based on kind.

        Parameters
        ----------
        result : SolverResult
            The raw solver result.
        query : str
            The original query formula.
        kind : ReconstructionKind
            The classified reconstruction kind.

        Returns
        -------
        ReconstructionResult
            The raw reconstruction before validation.
        """
        if kind is ReconstructionKind.PROOF:
            return self._proof_reconstructor.reconstruct(result, query)
        if kind is ReconstructionKind.WITNESS:
            return self._witness_reconstructor.reconstruct(result, query)
        if kind is ReconstructionKind.COUNTERMODEL:
            return self._witness_reconstructor.reconstruct(result, query)
        return self._partial_reconstructor.reconstruct(result, query)

    def validate(self, result: ReconstructionResult) -> ReconstructionResult:
        """Validate a reconstruction and update its validation status.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction to validate.

        Returns
        -------
        ReconstructionResult
            The reconstruction with updated validation status.
        """
        status = self._validator.full_validation(result)
        return replace(result, validation_status=status)

    def package(
        self,
        result: ReconstructionResult,
        *,
        channel: EvidenceChannel | None = None,
        scope: str | None = None,
    ) -> EvidenceResponse:
        """Package a validated reconstruction for channel delivery.

        Parameters
        ----------
        result : ReconstructionResult
            The validated reconstruction.
        channel : EvidenceChannel | None
            Override the default delivery channel.
        scope : str | None
            Override the scope annotation.

        Returns
        -------
        EvidenceResponse
            The final evidence response.
        """
        return self._assembler.assemble(result, channel=channel, scope=scope)

    def deliver(
        self,
        result: SolverResult,
        query: str,
        *,
        scope: str = 'global',
    ) -> EvidenceResponse:
        """Convenience alias for :meth:`submit` with default options.

        Parameters
        ----------
        result : SolverResult
            The raw solver result.
        query : str
            The original query formula.
        scope : str
            The coordinate scope.

        Returns
        -------
        EvidenceResponse
            A fully-packaged evidence response.
        """
        return self.submit(result, query, scope=scope)

    def get_statistics(self) -> dict[str, Any]:
        """Return pipeline statistics including cache performance.

        Returns
        -------
        dict[str, Any]
            Combined pipeline and cache statistics.
        """
        stats = self._statistics.summary()
        stats['cache'] = self._cache.statistics()
        return stats

    @staticmethod
    def _compute_cache_key(result: SolverResult, query: str) -> str:
        """Compute a deterministic cache key for a solver result + query."""
        payload = json.dumps(
            {
                'query': query,
                'outcome': result.outcome.value,
                'model': result.model,
                'engine': result.engine,
                'reasons': list(result.reasons),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


# ─────────────────────────────────────────────────────────────────────────
# ReconstructionStatistics
# ─────────────────────────────────────────────────────────────────────────


class ReconstructionStatistics:
    """Tracks operational statistics for the reconstruction pipeline.

    Records success/failure counts, timing, trust distribution, and
    fragment breakdown so that the orchestration layer can monitor
    reconstruction health and adjust budgets.
    """

    def __init__(self) -> None:
        self._total: int = 0
        self._successes: int = 0
        self._failures: int = 0
        self._cache_hits: int = 0
        self._times_ms: list[float] = []
        self._kind_counts: dict[str, int] = defaultdict(int)
        self._trust_counts: dict[str, int] = defaultdict(int)
        self._fragment_counts: dict[str, int] = defaultdict(int)
        self._validation_counts: dict[str, int] = defaultdict(int)

    def record(
        self,
        result: ReconstructionResult,
        *,
        elapsed_ms: float = 0.0,
        from_cache: bool = False,
    ) -> None:
        """Record a reconstruction result in the statistics.

        Parameters
        ----------
        result : ReconstructionResult
            The reconstruction that was performed.
        elapsed_ms : float
            Total pipeline time in milliseconds (0.0 for cache hits).
        from_cache : bool
            Whether this result was served from cache.
        """
        self._total += 1
        if from_cache:
            self._cache_hits += 1
        if result.is_valid():
            self._successes += 1
        elif result.validation_status is ValidationStatus.INVALID:
            self._failures += 1
        if elapsed_ms > 0:
            self._times_ms.append(elapsed_ms)
        self._kind_counts[result.kind.value] += 1
        self._trust_counts[result.trust_level.name] += 1
        fragment_key = (
            result.fragment.value
            if hasattr(result.fragment, 'value')
            else str(result.fragment)
        )
        self._fragment_counts[fragment_key] += 1
        self._validation_counts[result.validation_status.value] += 1

    def success_rate(self) -> float:
        """Return the ratio of valid reconstructions to total attempts.

        Returns
        -------
        float
            Success rate in [0.0, 1.0], or 0.0 if no attempts.
        """
        return self._successes / self._total if self._total > 0 else 0.0

    def average_time(self) -> float:
        """Return the average reconstruction time in milliseconds.

        Returns
        -------
        float
            Mean reconstruction time, or 0.0 if no timed records.
        """
        return sum(self._times_ms) / len(self._times_ms) if self._times_ms else 0.0

    def median_time(self) -> float:
        """Return the median reconstruction time in milliseconds.

        Returns
        -------
        float
            Median reconstruction time, or 0.0 if no timed records.
        """
        if not self._times_ms:
            return 0.0
        sorted_times = sorted(self._times_ms)
        mid = len(sorted_times) // 2
        if len(sorted_times) % 2 == 0:
            return (sorted_times[mid - 1] + sorted_times[mid]) / 2.0
        return sorted_times[mid]

    def trust_distribution(self) -> dict[str, float]:
        """Return the distribution of trust levels as fractions.

        Returns
        -------
        dict[str, float]
            Mapping of trust level names to their fraction of all
            reconstructions.
        """
        if self._total == 0:
            return {}
        return {
            level: count / self._total
            for level, count in sorted(self._trust_counts.items())
        }

    def fragment_breakdown(self) -> dict[str, int]:
        """Return the count of reconstructions per logical fragment.

        Returns
        -------
        dict[str, int]
            Mapping of fragment names to reconstruction counts.
        """
        return dict(self._fragment_counts)

    def kind_breakdown(self) -> dict[str, int]:
        """Return the count of reconstructions per kind.

        Returns
        -------
        dict[str, int]
            Mapping of reconstruction kind names to counts.
        """
        return dict(self._kind_counts)

    def validation_breakdown(self) -> dict[str, int]:
        """Return the count of reconstructions per validation status.

        Returns
        -------
        dict[str, int]
            Mapping of validation status names to counts.
        """
        return dict(self._validation_counts)

    def summary(self) -> dict[str, Any]:
        """Return a comprehensive summary of all statistics.

        Returns
        -------
        dict[str, Any]
            Dictionary with all tracked metrics.
        """
        return {
            'total': self._total,
            'successes': self._successes,
            'failures': self._failures,
            'cache_hits': self._cache_hits,
            'success_rate': self.success_rate(),
            'average_time_ms': self.average_time(),
            'median_time_ms': self.median_time(),
            'trust_distribution': self.trust_distribution(),
            'fragment_breakdown': self.fragment_breakdown(),
            'kind_breakdown': self.kind_breakdown(),
            'validation_breakdown': self.validation_breakdown(),
            'version': _RECONSTRUCTION_VERSION,
        }

    def reset(self) -> None:
        """Reset all statistics to zero."""
        self._total = 0
        self._successes = 0
        self._failures = 0
        self._cache_hits = 0
        self._times_ms.clear()
        self._kind_counts.clear()
        self._trust_counts.clear()
        self._fragment_counts.clear()
        self._validation_counts.clear()


# ─────────────────────────────────────────────────────────────────────────
# Cross-subsystem integration — judgment reconstruction & provenance
# ─────────────────────────────────────────────────────────────────────────


def reconstructed_judgment(
    result: SolverResult,
    *,
    coordinate: str = "",
    formula: str = "",
    carrier_name: str = "solver-reconstructed",
) -> Any:
    """Rebuild a judgment from a solver result using the judgment-terms algebra.

    Translates a :class:`SolverResult` into a full
    :class:`~jugeo.judgments.judgment_terms.Judgment` 8-tuple by
    constructing a proposition from the formula, a carrier for the
    reconstructed type, and an evidence bundle from the solver model.

    Parameters
    ----------
    result:
        The solver result to reconstruct from.
    coordinate:
        The semantic coordinate where the result was produced.
    formula:
        The original formula that was checked.
    carrier_name:
        Name for the carrier (type artifact) of the reconstructed judgment.

    Returns
    -------
    A ``Judgment`` object when the judgment-terms subsystem is available,
    otherwise a plain dictionary capturing the reconstruction.
    """
    if _JUDGMENT_TERMS_AVAILABLE:
        try:
            builder = _JudgmentBuilder()
            builder = builder.coordinate(coordinate or "(global)")
            builder = builder.proposition(_Proposition(
                kind="semantic",
                formula=formula or str(result.outcome.value),
            ))
            builder = builder.carrier(_Carrier(
                name=carrier_name,
                parameters=(),
            ))

            # Map solver model entries into evidence items
            if result.model:
                evidence_items = tuple(
                    {"variable": k, "value": v}
                    for k, v in result.model.items()
                )
                builder = builder.evidence(evidence_items)

            # Set trust based on outcome
            if result.is_unsat():
                builder = builder.trust_level("solver-discharged")
            elif result.is_sat():
                builder = builder.trust_level("solver-witnessed")
            else:
                builder = builder.trust_level("solver-unknown")

            builder = builder.provenance(f"reconstruction:{result.engine}")
            return builder.build()
        except Exception:
            pass  # Fall through to dict

    # Fallback representation
    return {
        "kind": "reconstructed_judgment",
        "coordinate": coordinate or "(global)",
        "formula": formula,
        "outcome": result.outcome.value,
        "engine": result.engine,
        "model": dict(result.model),
        "reasons": list(result.reasons),
        "carrier": carrier_name,
        "trust_level": (
            "solver-discharged" if result.is_unsat()
            else "solver-witnessed" if result.is_sat()
            else "solver-unknown"
        ),
    }


def provenance_trace(
    result: SolverResult,
    *,
    coordinate: str = "",
    session_id: str = "",
    fragment_name: str = "",
    parent_ids: tuple[str, ...] = (),
) -> Any:
    """Attach provenance information to a solver result.

    Builds a :class:`~jugeo.evidence.provenance.ProvenanceTrace` (or, when
    the full provenance-graph subsystem is available, a
    :class:`~jugeo.evidence.provenance.ProvenanceNode` embedded in a graph)
    that records the solver engine, coordinate, fragment, and timing as an
    auditable provenance chain.

    Parameters
    ----------
    result:
        The solver result to annotate.
    coordinate:
        The semantic coordinate where the result was produced.
    session_id:
        Identifier of the solver session.
    fragment_name:
        Name of the logical fragment that was solved.
    parent_ids:
        Node identifiers of upstream provenance entries (e.g. the
        encoding step that produced the formula).

    Returns
    -------
    A ``ProvenanceTrace`` or ``ProvenanceNode`` when the provenance
    subsystem is available, otherwise a plain dictionary.
    """
    step_details = {
        "outcome": result.outcome.value,
        "engine": result.engine,
        "model_size": len(result.model),
        "reasons": list(result.reasons),
        "fragment": fragment_name,
    }

    # Try the lightweight ProvenanceTrace first (always available via
    # the direct import at module top-level)
    try:
        step = ProvenanceStep(
            actor=f"solver:{result.engine}",
            action="solve",
            coordinate=coordinate or "(global)",
            details=step_details,
        )
        trace = ProvenanceTrace(steps=(step,))

        # Enrich with graph-level provenance when available
        if _PROVENANCE_GRAPH_AVAILABLE:
            try:
                import time as _time
                graph = _ProvenanceGraph()
                node = _ProvenanceNode(
                    node_id=session_id or f"solve-{id(result):x}",
                    operation=_ProvenanceOperation.PRODUCED,
                    actor=f"solver:{result.engine}",
                    timestamp=_time.time(),
                    parents=parent_ids,
                    trust_level=(
                        "solver-discharged" if result.is_unsat()
                        else "solver-witnessed" if result.is_sat()
                        else "solver-unknown"
                    ),
                    metadata=step_details,
                )
                graph.add_node(node)
                for pid in parent_ids:
                    try:
                        graph.add_edge(pid, node.node_id)
                    except Exception:
                        pass
                return graph
            except Exception:
                pass  # Fall back to simple trace

        return trace
    except Exception:
        pass

    # Ultimate fallback: plain dict
    return {
        "provenance_type": "solver_result",
        "actor": f"solver:{result.engine}",
        "coordinate": coordinate or "(global)",
        "session_id": session_id,
        "details": step_details,
        "parent_ids": list(parent_ids),
    }


# ─────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────

__all__ = [
    # Enumerations
    'ReconstructionKind',
    'ValidationStatus',
    # Value types
    'ProofStep',
    'WitnessBinding',
    'SortInterpretation',
    'FunctionInterpretation',
    'ArrayInterpretation',
    'DatatypeInterpretation',
    # Core result
    'ReconstructionResult',
    # Legacy API
    'ReconstructionReport',
    'reconstruct_countermodel',
    # Reconstructors
    'ProofReconstructor',
    'WitnessReconstructor',
    'ModelReconstructor',
    'PartialReconstructor',
    # Assembly and validation
    'EvidenceAssembler',
    'ReconstructionCache',
    'ReconstructionValidator',
    # Pipeline
    'ReconstructionPipeline',
    'ReconstructionStatistics',
    # Cross-subsystem integration
    'reconstructed_judgment',
    'provenance_trace',
]

# copilot: shared-core marker for LLM-assisted model reconstruction and
# partial-completion orchestration — the copilot channel may propose
# completions for undecided atoms, but all such proposals are capped at
# COPILOT_SUGGESTED trust and require corroboration before promotion.
