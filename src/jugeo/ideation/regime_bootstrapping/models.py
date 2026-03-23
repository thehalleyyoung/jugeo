"""
Regime-Bootstrapping Models
============================

Core data-model layer for the *regime_bootstrapping* sub-package
(JuGeo Theory — Regime Bootstrapping).

A *regime* is a coherent cluster of concepts, generators, and type
constructors that collectively form a self-consistent mathematical domain.
*Bootstrapping* refers to the process of discovering and validating such
regimes from raw ideation evidence.

This module defines the data structures used throughout that process:

* :class:`BootstrapStatus` — life-cycle states of a bootstrapping run
* :class:`ObstructionKind` — taxonomy of obstructions encountered
* :class:`DomainType` — classification of mathematical domains
* :class:`TypeConstructorKind` — classification of type-theoretic constructors
* :class:`BootstrapPriority` — priority levels for bootstrap steps
* :class:`ObstructionField` — a discovered obstruction with severity metadata
* :class:`DomainFormation` — mutable accumulator for domain generators/relations
* :class:`TypeConstructor` — immutable descriptor for a type-theoretic functor
* :class:`RegimeCandidate` — candidate regime under evaluation
* :class:`BootstrapStep` — a single step in a bootstrap plan
* :class:`BootstrapPlan` — ordered collection of bootstrap steps
* :class:`BootstrapResult` — outcome of executing a bootstrap plan
* :class:`RegimeBootstrapperConfig` — configuration for the bootstrapper
* :class:`RegimeBootstrapper` — the top-level orchestrator

Internal helpers
----------------
* :func:`_utcnow` — returns current UTC time as a float (POSIX timestamp)
* :func:`_uid` — returns a short unique identifier string
* :func:`_clamp` — clamps a float value to ``[lo, hi]``
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "BootstrapStatus",
    "ObstructionKind",
    "DomainType",
    "TypeConstructorKind",
    "BootstrapPriority",
    "ObstructionField",
    "DomainFormation",
    "TypeConstructor",
    "RegimeCandidate",
    "BootstrapStep",
    "BootstrapPlan",
    "BootstrapResult",
    "RegimeBootstrapperConfig",
    "RegimeBootstrapper",
    "_utcnow",
    "_uid",
    "_clamp",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Returns
    -------
    float
        Seconds since the UNIX epoch, always > 0.

    Examples
    --------
    >>> t = _utcnow()
    >>> isinstance(t, float)
    True
    >>> t > 0
    True
    """
    return time.time()


def _uid() -> str:
    """Return a short, unique identifier string based on UUID4.

    The identifier is the hex form of a UUID4 without hyphens, giving 32
    lowercase hexadecimal characters.

    Returns
    -------
    str
        A 32-character hex string, e.g. ``"4b3a2c1d..."``.

    Examples
    --------
    >>> uid = _uid()
    >>> isinstance(uid, str)
    True
    >>> len(uid) > 0
    True
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval ``[lo, hi]``.

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.

    Raises
    ------
    ValueError
        If ``lo > hi``.

    Examples
    --------
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    >>> _clamp(-1.0, 0.0, 1.0)
    0.0
    >>> _clamp(2.0, 0.0, 1.0)
    1.0
    """
    if lo > hi:
        raise ValueError(
            f"_clamp: lower bound {lo!r} must be <= upper bound {hi!r}."
        )
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class BootstrapStatus(str, Enum):
    """Life-cycle states of a regime-bootstrapping run.

    Values
    ------
    PENDING
        The bootstrap run has been created but not yet started.
    RUNNING
        The bootstrapper is actively executing steps.
    PAUSED
        Execution has been temporarily suspended.
    SUCCEEDED
        All steps completed successfully.
    FAILED
        One or more steps failed and the run was aborted.
    CANCELLED
        The run was cancelled before completion.
    PARTIAL
        Some steps succeeded but the run did not reach full completion.
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class ObstructionKind(str, Enum):
    """Taxonomy of obstructions that can block regime bootstrapping.

    Values
    ------
    TYPE_MISMATCH
        A type-checking failure between two components.
    MISSING_GENERATOR
        A required generator is absent from the domain.
    CYCLIC_DEPENDENCY
        A circular dependency was detected among regime elements.
    AMBIGUOUS_CONSTRUCTOR
        Multiple incompatible type constructors apply to the same term.
    TRUST_DEFICIT
        Insufficient trust score to proceed with the candidate.
    SCHEMA_VIOLATION
        The proposed domain structure violates a schema invariant.
    EVIDENCE_GAP
        Insufficient evidence to support the regime candidate.
    INTERNAL_ERROR
        An unexpected internal error occurred during bootstrapping.
    """

    TYPE_MISMATCH = "type_mismatch"
    MISSING_GENERATOR = "missing_generator"
    CYCLIC_DEPENDENCY = "cyclic_dependency"
    AMBIGUOUS_CONSTRUCTOR = "ambiguous_constructor"
    TRUST_DEFICIT = "trust_deficit"
    SCHEMA_VIOLATION = "schema_violation"
    EVIDENCE_GAP = "evidence_gap"
    INTERNAL_ERROR = "internal_error"


class DomainType(str, Enum):
    """Classification of mathematical domains encountered during bootstrapping.

    Values
    ------
    ALGEBRAIC
        Domains defined by algebraic operations and axioms (groups, rings…).
    TOPOLOGICAL
        Domains defined by continuity and open-set structure.
    CATEGORICAL
        Domains expressed as categories with objects and morphisms.
    COMBINATORIAL
        Finite or discrete domains without algebraic or topological structure.
    ANALYTIC
        Domains involving real/complex analysis and convergence.
    GEOMETRIC
        Domains with geometric structure (manifolds, metric spaces…).
    LOGICAL
        Domains defined by formal logical systems.
    HYBRID
        Domains combining two or more of the above types.
    """

    ALGEBRAIC = "algebraic"
    TOPOLOGICAL = "topological"
    CATEGORICAL = "categorical"
    COMBINATORIAL = "combinatorial"
    ANALYTIC = "analytic"
    GEOMETRIC = "geometric"
    LOGICAL = "logical"
    HYBRID = "hybrid"


class TypeConstructorKind(str, Enum):
    """Classification of type-theoretic constructors used in regime building.

    Values
    ------
    PRODUCT
        Cartesian product type constructor (e.g. ``A × B``).
    COPRODUCT
        Disjoint union / sum type (e.g. ``A + B``).
    EXPONENTIAL
        Function type / hom-set constructor (e.g. ``B^A``).
    RECURSIVE
        Recursively defined type (e.g. ``μX. F(X)``).
    CORECURSIVE
        Corecursively defined type (e.g. ``νX. F(X)``).
    DEPENDENT
        Dependent type constructor (e.g. ``Π(x:A). B(x)``).
    QUOTIENT
        Quotient type by an equivalence relation.
    SUBTYPE
        Refinement / subtype constrained by a predicate.
    """

    PRODUCT = "product"
    COPRODUCT = "coproduct"
    EXPONENTIAL = "exponential"
    RECURSIVE = "recursive"
    CORECURSIVE = "corecursive"
    DEPENDENT = "dependent"
    QUOTIENT = "quotient"
    SUBTYPE = "subtype"


class BootstrapPriority(str, Enum):
    """Priority levels for individual bootstrap steps.

    Values
    ------
    CRITICAL
        Must execute; blocking failure if skipped.
    HIGH
        Should execute; strongly affects outcome quality.
    MEDIUM
        Normal execution priority.
    LOW
        Optional; can be deferred or skipped without severe impact.
    BACKGROUND
        Execute only when no higher-priority work remains.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    BACKGROUND = "background"


# ---------------------------------------------------------------------------
# ObstructionField
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObstructionField:
    """An immutable descriptor for an obstruction encountered during bootstrapping.

    An *obstruction field* records the kind, human-readable description,
    numeric severity (0–1), and optional location context of a problem
    discovered while evaluating a :class:`RegimeCandidate`.

    Attributes
    ----------
    kind:
        The :class:`ObstructionKind` that categorises this obstruction.
    description:
        A human-readable explanation of what went wrong.
    severity:
        A float in ``[0.0, 1.0]`` where ``0.0`` is negligible and ``1.0``
        is maximally severe.
    location:
        Optional string identifying where in the domain the obstruction
        was detected (e.g. a generator name or sub-module path).
    obstruction_id:
        A unique identifier for this obstruction instance.

    Examples
    --------
    ::

        obs = ObstructionField(
            kind=ObstructionKind.TYPE_MISMATCH,
            description="Type A cannot unify with Type B.",
            severity=0.9,
            location="generator:alpha",
        )
        assert obs.is_blocking()
    """

    kind: ObstructionKind
    description: str
    severity: float
    location: str = ""
    obstruction_id: str = field(default_factory=_uid)

    def is_blocking(self) -> bool:
        """Return ``True`` if this obstruction is severe enough to block progress.

        An obstruction is considered *blocking* when its :attr:`severity` is
        greater than or equal to ``0.7``.

        Returns
        -------
        bool
        """
        return self.severity >= 0.7

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``kind``, ``description``, ``severity``, ``location``,
            ``obstruction_id``.
        """
        return {
            "kind": self.kind.value,
            "description": self.description,
            "severity": self.severity,
            "location": self.location,
            "obstruction_id": self.obstruction_id,
        }

    def render_summary(self) -> str:
        """Return a one-line human-readable summary of this obstruction.

        Returns
        -------
        str
            Non-empty string of the form
            ``"[KIND] (severity=X.XX) description"``.
        """
        loc_part = f" @ {self.location}" if self.location else ""
        return (
            f"[{self.kind.value.upper()}]"
            f" (severity={self.severity:.2f})"
            f"{loc_part}: {self.description}"
        )


# ---------------------------------------------------------------------------
# DomainFormation
# ---------------------------------------------------------------------------


@dataclass
class DomainFormation:
    """Mutable accumulator for the generators and relations of a domain.

    A :class:`DomainFormation` is built up incrementally as the bootstrapper
    discovers generators (atomic building-blocks of the domain) and relations
    (axioms or constraints that hold between generators).

    Attributes
    ----------
    domain_id:
        Unique identifier for this domain.
    name:
        Human-readable name.
    domain_type:
        The :class:`DomainType` category of this domain.
    generators:
        Ordered list of generator names.
    relations:
        Ordered list of relation / axiom descriptions.
    notes:
        Optional free-text notes.

    Examples
    --------
    ::

        df = DomainFormation(name="Group", domain_type=DomainType.ALGEBRAIC)
        df.add_generator("identity")
        df.add_generator("inverse")
        df.add_relation("associativity: (a*b)*c = a*(b*c)")
        assert df.is_valid()
    """

    name: str
    domain_type: DomainType
    domain_id: str = field(default_factory=_uid)
    generators: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    notes: str = ""

    def add_generator(self, generator: str) -> None:
        """Append *generator* to :attr:`generators` if not already present.

        Parameters
        ----------
        generator:
            Name of the generator to add.
        """
        if generator not in self.generators:
            self.generators.append(generator)

    def add_relation(self, relation: str) -> None:
        """Append *relation* to :attr:`relations`.

        Parameters
        ----------
        relation:
            Description of the relation or axiom.
        """
        self.relations.append(relation)

    def is_valid(self) -> bool:
        """Return ``True`` if the domain has at least one generator and a name.

        A :class:`DomainFormation` is considered *valid* when:

        * :attr:`name` is non-empty.
        * :attr:`generators` contains at least one entry.

        Returns
        -------
        bool
        """
        return bool(self.name.strip()) and len(self.generators) >= 1

    def complexity_score(self) -> float:
        """Compute a simple complexity score for this domain.

        The score is defined as:

        .. math::

           C = \\frac{|\\text{generators}| + 2 \\cdot |\\text{relations}|}{10}

        and is clamped to ``[0.0, 1.0]``.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        raw = (len(self.generators) + 2 * len(self.relations)) / 10.0
        return _clamp(raw, 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "domain_id": self.domain_id,
            "name": self.name,
            "domain_type": self.domain_type.value,
            "generators": list(self.generators),
            "relations": list(self.relations),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# TypeConstructor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeConstructor:
    """Immutable descriptor for a type-theoretic constructor in the regime.

    Attributes
    ----------
    constructor_id:
        Unique identifier for this constructor.
    name:
        Human-readable name (e.g. ``"List"``, ``"Maybe"``).
    kind:
        The :class:`TypeConstructorKind` classification.
    source_type:
        String description of the input type.
    target_type:
        String description of the output type.
    arity:
        Number of type parameters accepted by this constructor (>= 0).
    is_contravariant:
        Whether this constructor is contravariant in its argument.
    notes:
        Optional free-text notes.

    Examples
    --------
    ::

        tc = TypeConstructor(
            name="List",
            kind=TypeConstructorKind.RECURSIVE,
            source_type="A",
            target_type="List[A]",
            arity=1,
        )
        assert not tc.is_endomorphic()
    """

    name: str
    kind: TypeConstructorKind
    source_type: str
    target_type: str
    arity: int = 1
    is_contravariant: bool = False
    notes: str = ""
    constructor_id: str = field(default_factory=_uid)

    def is_endomorphic(self) -> bool:
        """Return ``True`` if :attr:`source_type` equals :attr:`target_type`.

        An *endomorphic* constructor maps a type to itself, e.g. a list
        monad whose functor acts as ``List[A] → List[A]``.

        Returns
        -------
        bool
        """
        return self.source_type == self.target_type

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "constructor_id": self.constructor_id,
            "name": self.name,
            "kind": self.kind.value,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "arity": self.arity,
            "is_contravariant": self.is_contravariant,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# RegimeCandidate
# ---------------------------------------------------------------------------


@dataclass
class RegimeCandidate:
    """A candidate regime under active evaluation by the bootstrapper.

    Attributes
    ----------
    candidate_id:
        Unique identifier for this candidate.
    label:
        Short human-readable label.
    domain_formation:
        The :class:`DomainFormation` associated with this candidate.
    trust_score:
        A float in ``[0.0, 1.0]`` representing the degree of confidence
        placed in this candidate.  Starts at ``0.5``.
    constructors:
        List of :class:`TypeConstructor` objects identified for this regime.
    obstructions:
        List of :class:`ObstructionField` objects blocking or warning.
    created_at:
        POSIX timestamp of candidate creation.

    Examples
    --------
    ::

        candidate = RegimeCandidate(
            label="TopologicalGroup",
            domain_formation=df,
            trust_score=0.6,
        )
        candidate.update_trust(0.2)
        assert candidate.is_viable()
    """

    label: str
    domain_formation: DomainFormation
    candidate_id: str = field(default_factory=_uid)
    trust_score: float = 0.5
    constructors: list[TypeConstructor] = field(default_factory=list)
    obstructions: list[ObstructionField] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)

    def add_constructor(self, constructor: TypeConstructor) -> None:
        """Append *constructor* to :attr:`constructors`.

        Parameters
        ----------
        constructor:
            The :class:`TypeConstructor` to attach.
        """
        self.constructors.append(constructor)

    def add_obstruction(self, obstruction: ObstructionField) -> None:
        """Append *obstruction* to :attr:`obstructions`.

        Parameters
        ----------
        obstruction:
            The :class:`ObstructionField` to record.
        """
        self.obstructions.append(obstruction)

    def update_trust(self, delta: float) -> None:
        """Adjust :attr:`trust_score` by *delta*, clamping to ``[0.0, 1.0]``.

        Parameters
        ----------
        delta:
            Signed increment to apply to the trust score.
        """
        self.trust_score = _clamp(self.trust_score + delta, 0.0, 1.0)

    def is_viable(self) -> bool:
        """Return ``True`` if the candidate is viable for promotion.

        A candidate is viable when:

        * :attr:`trust_score` >= ``0.5``
        * :attr:`domain_formation` is valid (has at least one generator)
        * No *blocking* obstructions are present

        Returns
        -------
        bool
        """
        if self.trust_score < 0.5:
            return False
        if not self.domain_formation.is_valid():
            return False
        if any(o.is_blocking() for o in self.obstructions):
            return False
        return True

    def rank_score(self) -> float:
        """Compute a ranking score for this candidate.

        The rank score combines trust level, domain complexity, and the
        absence of obstructions:

        .. math::

           R = \\text{trust} \\cdot C_{\\text{domain}}
               - 0.1 \\cdot |\\text{blocking obstructions}|

        and is clamped to ``[0.0, 1.0]``.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        blocking_count = sum(1 for o in self.obstructions if o.is_blocking())
        raw = (
            self.trust_score * self.domain_formation.complexity_score()
            - 0.1 * blocking_count
        )
        return _clamp(raw, 0.0, 1.0)


# ---------------------------------------------------------------------------
# BootstrapStep
# ---------------------------------------------------------------------------


@dataclass
class BootstrapStep:
    """A single step within a :class:`BootstrapPlan`.

    Attributes
    ----------
    step_id:
        Unique identifier for this step.
    name:
        Human-readable step name.
    description:
        Longer description of what this step does.
    priority:
        The :class:`BootstrapPriority` of this step.
    estimated_cost:
        Abstract cost estimate (arbitrary positive float).
    status:
        Current :class:`BootstrapStatus` of this step.
    output:
        Free-form output produced when the step runs.

    Examples
    --------
    ::

        step = BootstrapStep(
            name="Discover generators",
            description="Scan domain for atomic generators.",
            priority=BootstrapPriority.HIGH,
            estimated_cost=2.5,
        )
    """

    name: str
    description: str
    priority: BootstrapPriority
    estimated_cost: float = 1.0
    status: BootstrapStatus = BootstrapStatus.PENDING
    output: str = ""
    step_id: str = field(default_factory=_uid)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "estimated_cost": self.estimated_cost,
            "status": self.status.value,
            "output": self.output,
        }


# ---------------------------------------------------------------------------
# BootstrapPlan
# ---------------------------------------------------------------------------


@dataclass
class BootstrapPlan:
    """An ordered collection of :class:`BootstrapStep` objects.

    Attributes
    ----------
    plan_id:
        Unique identifier for this plan.
    label:
        Human-readable plan name.
    steps:
        Ordered list of :class:`BootstrapStep` objects.
    created_at:
        POSIX timestamp of plan creation.
    notes:
        Optional free-text notes.

    Examples
    --------
    ::

        plan = BootstrapPlan(label="Default plan")
        plan.steps.append(step_a)
        plan.steps.append(step_b)
        assert plan.total_cost() == step_a.estimated_cost + step_b.estimated_cost
    """

    label: str
    plan_id: str = field(default_factory=_uid)
    steps: list[BootstrapStep] = field(default_factory=list)
    created_at: float = field(default_factory=_utcnow)
    notes: str = ""

    def total_cost(self) -> float:
        """Return the sum of :attr:`BootstrapStep.estimated_cost` for all steps.

        Returns
        -------
        float
            Total estimated cost; ``0.0`` if the plan is empty.
        """
        return sum(s.estimated_cost for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "plan_id": self.plan_id,
            "label": self.label,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# BootstrapResult
# ---------------------------------------------------------------------------


@dataclass
class BootstrapResult:
    """Outcome of executing a :class:`BootstrapPlan`.

    Attributes
    ----------
    result_id:
        Unique identifier for this result.
    plan_id:
        The :attr:`BootstrapPlan.plan_id` this result corresponds to.
    status:
        Final :class:`BootstrapStatus`.
    candidates:
        List of :class:`RegimeCandidate` objects produced or updated.
    obstructions:
        Aggregate list of all :class:`ObstructionField` encountered.
    elapsed_seconds:
        Wall-clock time taken to execute the plan.
    message:
        Optional human-readable summary message.
    finished_at:
        POSIX timestamp of completion.

    Examples
    --------
    ::

        result = BootstrapResult(
            plan_id=plan.plan_id,
            status=BootstrapStatus.SUCCEEDED,
            candidates=[candidate],
            obstructions=[],
            elapsed_seconds=3.14,
        )
        assert result.is_success()
    """

    plan_id: str
    status: BootstrapStatus
    elapsed_seconds: float
    result_id: str = field(default_factory=_uid)
    candidates: list[RegimeCandidate] = field(default_factory=list)
    obstructions: list[ObstructionField] = field(default_factory=list)
    message: str = ""
    finished_at: float = field(default_factory=_utcnow)

    def is_success(self) -> bool:
        """Return ``True`` if :attr:`status` is :attr:`BootstrapStatus.SUCCEEDED`.

        Returns
        -------
        bool
        """
        return self.status == BootstrapStatus.SUCCEEDED

    def summary(self) -> str:
        """Return a one-line human-readable summary of this result.

        Returns
        -------
        str
            Non-empty string describing the result status, candidate count,
            obstruction count, and elapsed time.
        """
        return (
            f"BootstrapResult[{self.status.value}]"
            f" candidates={len(self.candidates)}"
            f" obstructions={len(self.obstructions)}"
            f" elapsed={self.elapsed_seconds:.3f}s"
            + (f" — {self.message}" if self.message else "")
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "result_id": self.result_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "elapsed_seconds": self.elapsed_seconds,
            "message": self.message,
            "finished_at": self.finished_at,
            "candidate_count": len(self.candidates),
            "obstruction_count": len(self.obstructions),
        }


# ---------------------------------------------------------------------------
# RegimeBootstrapperConfig
# ---------------------------------------------------------------------------


@dataclass
class RegimeBootstrapperConfig:
    """Configuration for :class:`RegimeBootstrapper`.

    Attributes
    ----------
    max_candidates:
        Maximum number of :class:`RegimeCandidate` objects to maintain.
    min_trust_threshold:
        Minimum trust score for a candidate to be considered viable.
    allow_partial_results:
        If ``True``, partial success is tolerated.
    max_obstructions:
        Maximum number of blocking obstructions before aborting.
    auto_prune:
        If ``True``, non-viable candidates are pruned automatically.
    timeout_seconds:
        Maximum allowed wall-clock time for a bootstrap run.
    verbose:
        Enable verbose logging output.

    Examples
    --------
    ::

        cfg = RegimeBootstrapperConfig.default()
        assert cfg.is_permissive()
    """

    max_candidates: int = 100
    min_trust_threshold: float = 0.5
    allow_partial_results: bool = True
    max_obstructions: int = 50
    auto_prune: bool = True
    timeout_seconds: float = 300.0
    verbose: bool = False

    @classmethod
    def default(cls) -> RegimeBootstrapperConfig:
        """Return a sensible default configuration.

        Returns
        -------
        RegimeBootstrapperConfig
        """
        return cls()

    @classmethod
    def strict(cls) -> RegimeBootstrapperConfig:
        """Return a strict configuration that rejects partial results.

        In strict mode:

        * :attr:`allow_partial_results` is ``False``
        * :attr:`min_trust_threshold` is raised to ``0.8``
        * :attr:`max_obstructions` is reduced to ``5``
        * :attr:`auto_prune` is ``True``

        Returns
        -------
        RegimeBootstrapperConfig
        """
        return cls(
            allow_partial_results=False,
            min_trust_threshold=0.8,
            max_obstructions=5,
            auto_prune=True,
        )

    def is_permissive(self) -> bool:
        """Return ``True`` if this config allows partial results.

        Returns
        -------
        bool
        """
        return self.allow_partial_results

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "max_candidates": self.max_candidates,
            "min_trust_threshold": self.min_trust_threshold,
            "allow_partial_results": self.allow_partial_results,
            "max_obstructions": self.max_obstructions,
            "auto_prune": self.auto_prune,
            "timeout_seconds": self.timeout_seconds,
            "verbose": self.verbose,
        }


# ---------------------------------------------------------------------------
# RegimeBootstrapper
# ---------------------------------------------------------------------------


class RegimeBootstrapper:
    """Top-level orchestrator for the regime-bootstrapping process.

    Manages a queue of :class:`BootstrapPlan` objects, tracks submitted
    plans and their results, and provides aggregate statistics.

    Attributes
    ----------
    config:
        The active :class:`RegimeBootstrapperConfig`.

    Examples
    --------
    ::

        bootstrapper = RegimeBootstrapper()
        plan = BootstrapPlan(label="test plan")
        bootstrapper.submit_plan(plan)
        assert bootstrapper.success_rate() == 0.0  # no results yet
    """

    def __init__(self, config: RegimeBootstrapperConfig | None = None) -> None:
        self.config: RegimeBootstrapperConfig = config or RegimeBootstrapperConfig.default()
        self._plans: list[BootstrapPlan] = []
        self._results: list[BootstrapResult] = []

    def submit_plan(self, plan: BootstrapPlan) -> None:
        """Submit *plan* to the bootstrapper's queue.

        Parameters
        ----------
        plan:
            The :class:`BootstrapPlan` to enqueue.
        """
        self._plans.append(plan)

    def record_result(self, result: BootstrapResult) -> None:
        """Record *result* from a completed bootstrap run.

        Parameters
        ----------
        result:
            The :class:`BootstrapResult` to store.
        """
        self._results.append(result)

    def success_rate(self) -> float:
        """Return the fraction of recorded results that are successful.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.  Returns ``0.0`` when no results have
            been recorded.
        """
        if not self._results:
            return 0.0
        successes = sum(1 for r in self._results if r.is_success())
        return successes / len(self._results)

    def plan_count(self) -> int:
        """Return the number of submitted plans."""
        return len(self._plans)

    def result_count(self) -> int:
        """Return the number of recorded results."""
        return len(self._results)
