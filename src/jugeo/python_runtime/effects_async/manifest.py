from __future__ import annotations

r"""Package manifest for JuGeo Chapter 18 effects_async.

This module provides the canonical manifest for
``jugeo.python_runtime.effects_async``, the Python implementation companion to
theory2.tex Chapter 18: *Python Runtime Effects — Exceptions, Context Managers,
Async Coroutines, and Generators*.

JuGeo (Judgment Geometry) treats every Python runtime effect as a typed section
of a structured sheaf over the semantic site.  Chapter 18 establishes five
effect kinds:

* **Exception sections** (§18.2) — failures as sections of the failure sheaf.
* **Context scopes** (§18.6) — ``with``-block lifetimes as covering families.
* **Async sections** (§18.3) — asyncio tasks as suspended section morphisms.
* **Generator sections** (§18.5) — lazy generators as fiber restriction sequences.
* **Cancellation records** (§18.4) — cancellation as obstruction morphisms.

Manifest responsibilities
-------------------------

:data:`CHAPTER_COVERAGE`
    Maps each theory2.tex §18xx section to the Python module that implements
    its claims, together with coverage confidence and open TODOs.

:data:`EXPORTED_SYMBOLS`
    The complete public API surface of the ``effects_async`` sub-package,
    grouped by conceptual role.

:data:`THEORY_CLAIMS`
    Machine-readable description of the five principal claims in Ch.18.

:class:`ManifestRecord`
    Structured record for a single chapter-coverage entry.

:class:`SymbolGroup`
    Named cluster of exported symbols with descriptions.

:class:`ClaimSummary`
    Lightweight summary of a theory claim linking to full theorem objects.

:class:`PackageManifest`
    Root manifest object: validates coverage, resolves cross-references,
    and emits a JSON report suitable for CI gating.

All copilot-assisted code generation within this sub-package is governed by
the trust algebra defined in theory2.tex Ch.2: generated stubs enter at
``COPILOT_SUGGESTED`` and must be promoted explicitly through review before
they carry ``SOLVER_DISCHARGED`` or higher trust.  No silent promotion is
permitted; every trust advance requires a policy-justified evidence step.

Theory alignment
----------------

Section §18.1 of theory2.tex ("Ch18 Package Overview") is the primary
reference.  §18.2–§18.6 enumerate the five typed effect constructions;
§18.7–§18.10 cover algorithms, integration, theorems, and the package API
surface.  This manifest encodes all ten sections in machine-readable form.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CoverageStatus(Enum):
    """Degree to which a theory2.tex section is covered by Python code.

    Levels are ordered from weakest to strongest.  A CI gate may enforce a
    minimum level per section before a release is tagged.
    """

    MISSING = "missing"
    STUB = "stub"
    PARTIAL = "partial"
    SUBSTANTIAL = "substantial"
    COMPLETE = "complete"

    @property
    def ordinal(self) -> int:
        """Integer rank for comparison."""
        _ranks = {
            "missing": 0,
            "stub": 1,
            "partial": 2,
            "substantial": 3,
            "complete": 4,
        }
        return _ranks[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal <= other.ordinal


class SymbolRole(Enum):
    """Conceptual role of an exported symbol."""

    DATA_MODEL = "data_model"
    CLAIM = "claim"
    ALGORITHM = "algorithm"
    THEOREM = "theorem"
    INTEGRATION = "integration"
    UTILITY = "utility"


class ClaimStatus(Enum):
    """Lifecycle status of a theory claim."""

    PROPOSED = "proposed"
    FORMALISED = "formalised"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    RETRACTED = "retracted"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRecord:
    """Coverage record for a single theory2.tex §18xx section.

    Each record maps a section identifier (e.g. ``"§1802"``) to the Python
    module that implements its content, together with a confidence score,
    a list of open TODOs, and a textual summary of what is covered.

    Parameters
    ----------
    section_id:
        The theory2.tex section identifier, e.g. ``"§1801"``.
    section_title:
        Human-readable title of the section.
    module_path:
        Dotted Python module path, e.g.
        ``"jugeo.python_runtime.effects_async.models"``.
    status:
        Coverage status from :class:`CoverageStatus`.
    confidence:
        Float in ``[0.0, 1.0]`` estimating how faithfully the Python module
        captures the theory.  Derived from author review; does not imply
        mechanical verification.
    open_todos:
        Unresolved implementation gaps as short strings.
    summary:
        One-paragraph prose summary of what the module covers.
    copilot_assisted:
        Whether any part of the module was scaffolded with copilot assistance.
        Copilot-assisted sections carry ``COPILOT_SUGGESTED`` trust until
        reviewed and promoted.
    """

    section_id: str
    section_title: str
    module_path: str
    status: CoverageStatus
    confidence: float
    open_todos: tuple[str, ...]
    summary: str
    copilot_assisted: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence!r}"
            )
        if not self.section_id.startswith("§"):
            raise ValueError(
                f"section_id must start with §, got {self.section_id!r}"
            )

    def is_complete(self) -> bool:
        """Return True if this section is fully covered."""
        return self.status == CoverageStatus.COMPLETE

    def coverage_gap(self) -> float:
        """Return the fractional gap to full coverage.

        A record with ``confidence=0.85`` and status ``SUBSTANTIAL`` returns
        the complement ``0.15``.
        """
        return 1.0 - self.confidence

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "section_id": self.section_id,
            "section_title": self.section_title,
            "module_path": self.module_path,
            "status": self.status.value,
            "confidence": self.confidence,
            "open_todos": list(self.open_todos),
            "summary": self.summary,
            "copilot_assisted": self.copilot_assisted,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ManifestRecord":
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            section_id=data["section_id"],
            section_title=data["section_title"],
            module_path=data["module_path"],
            status=CoverageStatus(data["status"]),
            confidence=float(data["confidence"]),
            open_todos=tuple(data.get("open_todos", [])),
            summary=data["summary"],
            copilot_assisted=bool(data.get("copilot_assisted", False)),
        )


@dataclass(frozen=True)
class SymbolGroup:
    """Named cluster of related exported symbols.

    Parameters
    ----------
    name:
        Short name for the group, e.g. ``"effect_models"``.
    role:
        Conceptual role shared by all symbols in this group.
    symbols:
        Tuple of fully-qualified symbol names.
    description:
        Prose description of what the group provides.
    source_module:
        The module that defines all symbols in this group.
    """

    name: str
    role: SymbolRole
    symbols: tuple[str, ...]
    description: str
    source_module: str

    def contains(self, symbol: str) -> bool:
        """Return True if *symbol* is in this group (short or fully qualified)."""
        return any(
            s == symbol or s.endswith(f".{symbol}") for s in self.symbols
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {
            "name": self.name,
            "role": self.role.value,
            "symbols": list(self.symbols),
            "description": self.description,
            "source_module": self.source_module,
        }


@dataclass(frozen=True)
class ClaimSummary:
    """Lightweight summary of a theory claim from Ch.18.

    This is the manifest-level view; the full structured object lives in
    :mod:`jugeo.python_runtime.effects_async.theorems`.

    Parameters
    ----------
    claim_id:
        Short identifier, e.g. ``"C18_1"``.
    title:
        One-line claim title.
    status:
        Current lifecycle status.
    theory_section:
        theory2.tex section that states this claim.
    implementing_module:
        Python module that provides the claim's verification logic.
    falsification_module:
        Python module that provides falsification criteria.
    evidence_required:
        Short names of evidence types needed to verify the claim.
    """

    claim_id: str
    title: str
    status: ClaimStatus
    theory_section: str
    implementing_module: str
    falsification_module: str
    evidence_required: tuple[str, ...]

    def is_open(self) -> bool:
        """Return True if the claim has not yet been resolved."""
        return self.status in (
            ClaimStatus.PROPOSED,
            ClaimStatus.FORMALISED,
            ClaimStatus.PARTIALLY_VERIFIED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "claim_id": self.claim_id,
            "title": self.title,
            "status": self.status.value,
            "theory_section": self.theory_section,
            "implementing_module": self.implementing_module,
            "falsification_module": self.falsification_module,
            "evidence_required": list(self.evidence_required),
        }


# ---------------------------------------------------------------------------
# Chapter coverage table
# ---------------------------------------------------------------------------


CHAPTER_COVERAGE: tuple[ManifestRecord, ...] = (
    ManifestRecord(
        section_id="§1801",
        section_title="Ch18 Package Overview",
        module_path="jugeo.python_runtime.effects_async.manifest",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.90,
        open_todos=(
            "Add cross-ref validation against Ch18 theorem catalog",
            "Wire confidence scores to CI gate",
        ),
        summary=(
            "The manifest module encodes the Chapter 18 package overview: five typed "
            "effect constructions (exception sections, context scopes, async sections, "
            "generator sections, cancellation records), coverage confidence, and "
            "theory-to-code mappings.  CHAPTER_COVERAGE, EXPORTED_SYMBOLS, and "
            "THEORY_CLAIMS provide machine-readable access to the package scope."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1802",
        section_title="Exception Sections",
        module_path="jugeo.python_runtime.effects_async.exceptions",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Implement ExceptionSheaf gluing law verification",
            "Add cross-site exception propagation integration test",
        ),
        summary=(
            "The exceptions module implements the failure sheaf construction "
            "from §18.2: ExceptionSheaf, ExceptionChain, FailurePropagator, and "
            "StructuredFailureEncoder.  Each Python exception is modelled as a typed "
            "section of the failure sheaf at its originating coordinate, with trust "
            "levels that decay as exceptions propagate through the call stack."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1803",
        section_title="Context Manager Scopes",
        module_path="jugeo.python_runtime.effects_async.context_managers",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=(
            "Prove covering family axioms for nested with-blocks",
            "Add residual obligation discharge test suite",
        ),
        summary=(
            "The context_managers module implements the scope covering "
            "construction from §18.6: ContextScopeManager, SectionScopeStack, "
            "AsyncContextScope, and ContextCoveringBuilder.  Each with-block "
            "contributes a CoveringFamily to the site topology, and residuals "
            "track cleanup obligations that survive scope exit."
        ),
    ),
    ManifestRecord(
        section_id="§1804",
        section_title="Async Coroutines and Tasks",
        module_path="jugeo.python_runtime.effects_async.async",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.86,
        open_todos=(
            "Implement EventLoopTopology covering axiom check",
            "Add cancellation cascade integration test",
        ),
        summary=(
            "The async module implements the async sub-site construction "
            "from §18.3: AsyncSiteBuilder, CoroutineSection, EventLoopTopology, "
            "and TaskRegistry.  Each asyncio Task is a suspended section morphism "
            "in the event-loop topology, with await-dependency edges as restriction "
            "morphisms and cancellation as obstruction morphisms."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1805",
        section_title="Generators and Iterators",
        module_path="jugeo.python_runtime.effects_async.generators",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.85,
        open_todos=(
            "Verify restriction sequence axioms for infinite generators",
            "Add send() history replay test",
        ),
        summary=(
            "The generators module implements the lazy fiber construction "
            "from §18.5: GeneratorSheaf, LazyFiberBuilder, IteratorSection, and "
            "GeneratorCombinator.  Each yield point emits a partial section over "
            "a derived fiber coordinate; the sequence of sections forms a valid "
            "restriction sequence in the semantic site."
        ),
    ),
    ManifestRecord(
        section_id="§1806",
        section_title="Data Models",
        module_path="jugeo.python_runtime.effects_async.models",
        status=CoverageStatus.COMPLETE,
        confidence=0.92,
        open_todos=(
            "Add round-trip serialization property tests",
            "Verify _decay_trust floor invariant with QuickCheck",
        ),
        summary=(
            "The models module provides frozen dataclass representations of all five "
            "typed effect constructions: ExceptionSection, ContextScope, AsyncSection, "
            "GeneratorSection, and CancellationRecord.  All copilot-assisted generation "
            "enters at COPILOT_SUGGESTED trust and requires explicit promotion.  "
            "Helper functions _make_coord_id and _decay_trust are provided for "
            "coordinate hashing and trust decay."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1807",
        section_title="Algorithms",
        module_path="jugeo.python_runtime.effects_async.algorithms",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.84,
        open_todos=(
            "Implement topological sort for async dependency graph",
            "Add benchmark for collect_generator_fibers on large sequences",
        ),
        summary=(
            "The algorithms module implements the procedural complement to the "
            "declarative models: propagate_exception_through_site, "
            "resolve_context_stack, schedule_async_sections, "
            "collect_generator_fibers, build_async_sub_site, and "
            "detect_cancellation_cascade.  Each algorithm operates over the "
            "frozen model objects and returns new model state."
        ),
    ),
    ManifestRecord(
        section_id="§1808",
        section_title="Integration",
        module_path="jugeo.python_runtime.effects_async.integration",
        status=CoverageStatus.PARTIAL,
        confidence=0.78,
        open_todos=(
            "Complete ExceptionJudgmentIntegrator channel wiring",
            "Add AsyncSiteIntegrator end-to-end test",
            "Wire ContextScopeIntegrator to Z3 solver session",
        ),
        summary=(
            "The integration module bridges the effects_async models to the "
            "wider JuGeo infrastructure: ExceptionJudgmentIntegrator routes "
            "exception sections into the judgment algebra, AsyncSiteIntegrator "
            "registers async tasks in the site topology, ContextScopeIntegrator "
            "contributes covering families to the Grothendieck topology, and "
            "GeneratorChannelBridge forwards fiber evidence to the evidence channels."
        ),
    ),
    ManifestRecord(
        section_id="§1809",
        section_title="Theorems",
        module_path="jugeo.python_runtime.effects_async.theorems",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.83,
        open_todos=(
            "Add proof sketch for Theorem_GeneratorFiberSequence",
            "Formalise Theorem_CancellationPropagation monotonicity condition",
        ),
        summary=(
            "The theorems module enumerates the five principal theorems of Ch.18: "
            "Theorem_ExceptionSectionality (exceptions form valid sheaf sections), "
            "Theorem_ContextScopeCovers (context scopes produce valid covering families), "
            "Theorem_AsyncTopologicalOrder (await-dependency graphs are DAGs), "
            "Theorem_GeneratorFiberSequence (generator yields form restriction sequences), "
            "and Theorem_CancellationPropagation (cancellation is monotone and cascade-complete)."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1810",
        section_title="Package API Surface",
        module_path="jugeo.python_runtime.effects_async",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=(
            "Add __version__ and package-level __all__",
            "Wire package-level init to manifest singleton",
        ),
        summary=(
            "The package __init__ re-exports the public API surface for "
            "jugeo.python_runtime.effects_async: all five model classes, the "
            "algorithm functions, integration bridges, and theorem objects.  "
            "The MANIFEST singleton provides a single entry point for package "
            "introspection and CI gate reporting."
        ),
    ),
)

# ---------------------------------------------------------------------------
# Exported symbols
# ---------------------------------------------------------------------------


EXPORTED_SYMBOLS: tuple[SymbolGroup, ...] = (
    SymbolGroup(
        name="effect_models",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.effects_async.models.ExceptionSection",
            "jugeo.python_runtime.effects_async.models.ContextScope",
            "jugeo.python_runtime.effects_async.models.AsyncSection",
            "jugeo.python_runtime.effects_async.models.GeneratorSection",
            "jugeo.python_runtime.effects_async.models.CancellationRecord",
        ),
        description=(
            "Core frozen dataclass models for the five typed Python runtime effects: "
            "exception sections, context scopes, async task sections, generator fiber "
            "sections, and cancellation records.  All copilot-assisted instances enter "
            "at ORACLE_PROPOSED trust."
        ),
        source_module="jugeo.python_runtime.effects_async.models",
    ),
    SymbolGroup(
        name="exception_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.effects_async.exceptions.ExceptionSheaf",
            "jugeo.python_runtime.effects_async.exceptions.ExceptionChain",
            "jugeo.python_runtime.effects_async.exceptions.FailurePropagator",
            "jugeo.python_runtime.effects_async.exceptions.StructuredFailureEncoder",
        ),
        description=(
            "Types implementing the failure sheaf construction from §18.2: the sheaf "
            "itself, exception chains, propagation logic, and structured encoding of "
            "Python exceptions as typed sheaf sections."
        ),
        source_module="jugeo.python_runtime.effects_async.exceptions",
    ),
    SymbolGroup(
        name="context_manager_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.effects_async.context_managers.ContextScopeManager",
            "jugeo.python_runtime.effects_async.context_managers.SectionScopeStack",
            "jugeo.python_runtime.effects_async.context_managers.AsyncContextScope",
            "jugeo.python_runtime.effects_async.context_managers.ContextCoveringBuilder",
        ),
        description=(
            "Types implementing the scope covering construction from §18.6: scope "
            "management, nested scope stacks, async context manager support, and "
            "CoveringFamily builder for Grothendieck topology contribution."
        ),
        source_module="jugeo.python_runtime.effects_async.context_managers",
    ),
    SymbolGroup(
        name="async_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.effects_async.async.AsyncSiteBuilder",
            "jugeo.python_runtime.effects_async.async.CoroutineSection",
            "jugeo.python_runtime.effects_async.async.EventLoopTopology",
            "jugeo.python_runtime.effects_async.async.TaskRegistry",
        ),
        description=(
            "Types implementing the async sub-site construction from §18.3: the site "
            "builder for async coordinates, coroutine sections as suspended morphisms, "
            "the event-loop Grothendieck topology, and the task registry for lifecycle "
            "tracking."
        ),
        source_module="jugeo.python_runtime.effects_async.async",
    ),
    SymbolGroup(
        name="generator_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.effects_async.generators.GeneratorSheaf",
            "jugeo.python_runtime.effects_async.generators.LazyFiberBuilder",
            "jugeo.python_runtime.effects_async.generators.IteratorSection",
            "jugeo.python_runtime.effects_async.generators.GeneratorCombinator",
        ),
        description=(
            "Types implementing the lazy fiber construction from §18.5: the generator "
            "sheaf, fiber builder for per-yield coordinates, iterator sections, and "
            "a combinator for composing generator sequences into restriction sequences."
        ),
        source_module="jugeo.python_runtime.effects_async.generators",
    ),
    SymbolGroup(
        name="algorithms",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.effects_async.algorithms.propagate_exception_through_site",
            "jugeo.python_runtime.effects_async.algorithms.resolve_context_stack",
            "jugeo.python_runtime.effects_async.algorithms.schedule_async_sections",
            "jugeo.python_runtime.effects_async.algorithms.collect_generator_fibers",
            "jugeo.python_runtime.effects_async.algorithms.build_async_sub_site",
            "jugeo.python_runtime.effects_async.algorithms.detect_cancellation_cascade",
        ),
        description=(
            "Procedural algorithms for the five effect kinds: propagating exceptions "
            "through the site, resolving context scope stacks, scheduling async sections "
            "in topological order, collecting generator fiber sequences, building the "
            "async sub-site, and detecting cascade cancellation in task graphs."
        ),
        source_module="jugeo.python_runtime.effects_async.algorithms",
    ),
    SymbolGroup(
        name="integration_types",
        role=SymbolRole.INTEGRATION,
        symbols=(
            "jugeo.python_runtime.effects_async.integration.ExceptionJudgmentIntegrator",
            "jugeo.python_runtime.effects_async.integration.AsyncSiteIntegrator",
            "jugeo.python_runtime.effects_async.integration.ContextScopeIntegrator",
            "jugeo.python_runtime.effects_async.integration.GeneratorChannelBridge",
        ),
        description=(
            "Integration bridges connecting the effects_async models to the wider "
            "JuGeo infrastructure: exception sections into the judgment algebra, "
            "async tasks into the site topology, context scopes into the Grothendieck "
            "topology, and generator fibers into the evidence channel pipeline."
        ),
        source_module="jugeo.python_runtime.effects_async.integration",
    ),
    SymbolGroup(
        name="theorem_types",
        role=SymbolRole.THEOREM,
        symbols=(
            "jugeo.python_runtime.effects_async.theorems.Theorem_ExceptionSectionality",
            "jugeo.python_runtime.effects_async.theorems.Theorem_ContextScopeCovers",
            "jugeo.python_runtime.effects_async.theorems.Theorem_AsyncTopologicalOrder",
            "jugeo.python_runtime.effects_async.theorems.Theorem_GeneratorFiberSequence",
            "jugeo.python_runtime.effects_async.theorems.Theorem_CancellationPropagation",
        ),
        description=(
            "The five principal theorems of Ch.18: exception sectionality, context "
            "scope covering families, async DAG ordering, generator fiber restriction "
            "sequences, and monotone cascade-complete cancellation propagation."
        ),
        source_module="jugeo.python_runtime.effects_async.theorems",
    ),
    SymbolGroup(
        name="manifest_types",
        role=SymbolRole.UTILITY,
        symbols=(
            "jugeo.python_runtime.effects_async.manifest.CoverageStatus",
            "jugeo.python_runtime.effects_async.manifest.SymbolRole",
            "jugeo.python_runtime.effects_async.manifest.ClaimStatus",
            "jugeo.python_runtime.effects_async.manifest.ManifestRecord",
            "jugeo.python_runtime.effects_async.manifest.SymbolGroup",
            "jugeo.python_runtime.effects_async.manifest.ClaimSummary",
            "jugeo.python_runtime.effects_async.manifest.PackageManifest",
        ),
        description=(
            "Root manifest utility types for package-level introspection: coverage "
            "status levels, symbol roles, claim lifecycle statuses, and the "
            "PackageManifest root object that aggregates all tables."
        ),
        source_module="jugeo.python_runtime.effects_async.manifest",
    ),
)

# ---------------------------------------------------------------------------
# Theory claims summary table
# ---------------------------------------------------------------------------


THEORY_CLAIMS: tuple[ClaimSummary, ...] = (
    ClaimSummary(
        claim_id="C18_1",
        title="Exception sections are sheaf sections over the failure sheaf",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§1802",
        implementing_module="jugeo.python_runtime.effects_async.exceptions",
        falsification_module="jugeo.python_runtime.effects_async.theorems",
        evidence_required=(
            "failure_sheaf_gluing_law",
            "exception_section_locality",
            "traceback_coord_restriction_morphism",
        ),
    ),
    ClaimSummary(
        claim_id="C18_2",
        title="Context scopes contribute valid Grothendieck covering families",
        status=ClaimStatus.FORMALISED,
        theory_section="§1803",
        implementing_module="jugeo.python_runtime.effects_async.context_managers",
        falsification_module="jugeo.python_runtime.effects_async.theorems",
        evidence_required=(
            "covering_family_axioms",
            "scope_nesting_transitivity",
            "residual_obligation_discharge",
        ),
    ),
    ClaimSummary(
        claim_id="C18_3",
        title="Async await-dependency graphs are DAGs (no circular awaits)",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§1804",
        implementing_module="jugeo.python_runtime.effects_async.async",
        falsification_module="jugeo.python_runtime.effects_async.theorems",
        evidence_required=(
            "await_dag_acyclicity",
            "event_loop_topology_soundness",
            "task_lifecycle_monotonicity",
        ),
    ),
    ClaimSummary(
        claim_id="C18_4",
        title="Generator fiber sequences are valid restriction sequences in the site",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§1805",
        implementing_module="jugeo.python_runtime.effects_async.generators",
        falsification_module="jugeo.python_runtime.effects_async.theorems",
        evidence_required=(
            "fiber_restriction_compatibility",
            "yield_index_monotonicity",
            "send_history_commutativity",
        ),
    ),
    ClaimSummary(
        claim_id="C18_5",
        title="Cancellation propagation is monotone and cascade-complete",
        status=ClaimStatus.PROPOSED,
        theory_section="§1804",
        implementing_module="jugeo.python_runtime.effects_async.async",
        falsification_module="jugeo.python_runtime.effects_async.theorems",
        evidence_required=(
            "cancellation_monotonicity",
            "cascade_completeness",
            "obstruction_morphism_naturality",
        ),
    ),
)


# ---------------------------------------------------------------------------
# PackageManifest — root manifest object
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Root manifest for the JuGeo Chapter 18 effects_async package.

    Aggregates :data:`CHAPTER_COVERAGE`, :data:`EXPORTED_SYMBOLS`, and
    :data:`THEORY_CLAIMS` and provides validation, querying, and reporting
    methods.

    Parameters
    ----------
    chapter_coverage:
        Tuple of :class:`ManifestRecord` objects.
    exported_symbols:
        Tuple of :class:`SymbolGroup` objects.
    theory_claims:
        Tuple of :class:`ClaimSummary` objects.
    created_at:
        Unix timestamp when this manifest was instantiated.

    Notes
    -----
    The manifest is deliberately read-only after construction (all mutation
    produces new instances).  This mirrors the append-only audit-log constraint
    in the trust algebra: once a claim is registered, it can only be updated
    by creating a new manifest version.

    Copilot-assisted sections are flagged in :attr:`ManifestRecord.copilot_assisted`
    and can be iterated with :meth:`iter_copilot_assisted_sections`.
    """

    chapter_coverage: tuple[ManifestRecord, ...]
    exported_symbols: tuple[SymbolGroup, ...]
    theory_claims: tuple[ClaimSummary, ...]
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Coverage queries
    # ------------------------------------------------------------------

    def coverage_for_section(self, section_id: str) -> ManifestRecord | None:
        """Return the coverage record for the given section, or ``None``.

        Parameters
        ----------
        section_id:
            Section identifier to look up, e.g. ``"§1802"``.

        Returns
        -------
        ManifestRecord | None
        """
        for rec in self.chapter_coverage:
            if rec.section_id == section_id:
                return rec
        return None

    def sections_below_threshold(
        self,
        min_status: CoverageStatus = CoverageStatus.SUBSTANTIAL,
        min_confidence: float = 0.80,
    ) -> list[ManifestRecord]:
        """Return sections that fall below the given coverage threshold.

        Parameters
        ----------
        min_status:
            Minimum :class:`CoverageStatus` required.
        min_confidence:
            Minimum confidence score required (``[0.0, 1.0]``).

        Returns
        -------
        list[ManifestRecord]
            All records whose status is below *min_status* **or** whose
            confidence is below *min_confidence*.
        """
        return [
            r
            for r in self.chapter_coverage
            if r.status < min_status or r.confidence < min_confidence
        ]

    def total_open_todos(self) -> int:
        """Return the total number of open TODO items across all sections."""
        return sum(len(r.open_todos) for r in self.chapter_coverage)

    def mean_confidence(self) -> float:
        """Return the arithmetic mean confidence across all coverage records."""
        if not self.chapter_coverage:
            return 0.0
        return sum(r.confidence for r in self.chapter_coverage) / len(
            self.chapter_coverage
        )

    # ------------------------------------------------------------------
    # Symbol queries
    # ------------------------------------------------------------------

    def find_symbol(self, name: str) -> SymbolGroup | None:
        """Return the first :class:`SymbolGroup` that contains *name*."""
        for group in self.exported_symbols:
            if group.contains(name):
                return group
        return None

    def symbols_by_role(self, role: SymbolRole) -> list[SymbolGroup]:
        """Return all symbol groups with the given role."""
        return [g for g in self.exported_symbols if g.role == role]

    def all_symbol_names(self) -> list[str]:
        """Return a flat sorted list of all exported symbol names."""
        names: list[str] = []
        for group in self.exported_symbols:
            names.extend(group.symbols)
        return sorted(set(names))

    # ------------------------------------------------------------------
    # Claim queries
    # ------------------------------------------------------------------

    def claim(self, claim_id: str) -> ClaimSummary | None:
        """Return the claim summary with the given identifier."""
        for c in self.theory_claims:
            if c.claim_id == claim_id:
                return c
        return None

    def open_claims(self) -> list[ClaimSummary]:
        """Return all claims that have not yet been resolved."""
        return [c for c in self.theory_claims if c.is_open()]

    def claims_by_status(self, status: ClaimStatus) -> list[ClaimSummary]:
        """Return all claims with the given status."""
        return [c for c in self.theory_claims if c.status == status]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full manifest to a JSON-safe dictionary."""
        return {
            "created_at": self.created_at,
            "mean_confidence": self.mean_confidence(),
            "total_open_todos": self.total_open_todos(),
            "chapter_coverage": [r.to_dict() for r in self.chapter_coverage],
            "exported_symbols": [g.to_dict() for g in self.exported_symbols],
            "theory_claims": [c.to_dict() for c in self.theory_claims],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def content_hash(self) -> str:
        """Return a SHA-256 digest of the canonical JSON representation.

        Useful for CI gating: if the hash changes, the manifest has been
        modified and downstream consumers should be re-validated.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def ci_gate_report(
        self,
        min_status: CoverageStatus = CoverageStatus.SUBSTANTIAL,
        min_confidence: float = 0.80,
    ) -> dict[str, Any]:
        """Produce a CI gate report for the effects_async package.

        Returns a dictionary with ``passed`` (bool), ``failing_sections``
        (list), ``open_claims`` (list), and summary statistics.  A gate
        passes if no sections fall below the thresholds and all claims are
        at least FORMALISED.

        Parameters
        ----------
        min_status:
            Minimum :class:`CoverageStatus` required per section.
        min_confidence:
            Minimum confidence score per section.

        Returns
        -------
        dict[str, Any]
        """
        failing = self.sections_below_threshold(min_status, min_confidence)
        under_formalised = [
            c
            for c in self.theory_claims
            if c.status == ClaimStatus.PROPOSED
        ]
        passed = not failing and not under_formalised
        return {
            "passed": passed,
            "mean_confidence": self.mean_confidence(),
            "total_open_todos": self.total_open_todos(),
            "failing_sections": [r.section_id for r in failing],
            "under_formalised_claims": [c.claim_id for c in under_formalised],
            "content_hash": self.content_hash(),
        }

    # ------------------------------------------------------------------
    # Iterator helpers
    # ------------------------------------------------------------------

    def iter_copilot_assisted_sections(self) -> Iterator[ManifestRecord]:
        """Yield all coverage records that were copilot-assisted.

        Copilot-assisted sections require additional human review before
        their trust level can be promoted above ``COPILOT_SUGGESTED``.
        """
        for rec in self.chapter_coverage:
            if rec.copilot_assisted:
                yield rec


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


MANIFEST = PackageManifest(
    chapter_coverage=CHAPTER_COVERAGE,
    exported_symbols=EXPORTED_SYMBOLS,
    theory_claims=THEORY_CLAIMS,
)
"""The canonical manifest instance for jugeo.python_runtime.effects_async."""


def get_manifest() -> PackageManifest:
    """Return the module-level :data:`MANIFEST` singleton.

    Provided as a function so that downstream code can be updated to accept
    a dependency-injected manifest without changing call sites.
    """
    return MANIFEST
