from __future__ import annotations

r"""Package manifest for ``jugeo.python_runtime.metaobject_surfaces``.

theory2.tex Ch20 §20.1–§20.10 — Class Creation, Metaclasses, Descriptors,
and Behavioral Surfaces as a Grothendieck Site.

This module provides the canonical manifest for the
``jugeo.python_runtime.metaobject_surfaces`` package, the Python
implementation companion to Theory2.tex Chapter 20: *Metaobject Protocol
Geometry*.

JuGeo models Python's metaobject protocol (MOP) as a typed judgment system
over a Grothendieck site.  Every class creation event, metaclass resolution
step, descriptor lookup, and protocol implementation is a ``Judgment`` tuple

    J = (c, φ, A, E, O, B, T, Π)

where:

* ``c``  — coordinate in the site (module + class + phase)
* ``φ``  — semantic formula (metaclass_valid, behavioral_surface_complete, …)
* ``A``  — carrier type (MetaclassCarrier, DescriptorChain, …)
* ``E``  — evidence bundle (multi-channel: RUNTIME, SOLVER, COPILOT)
* ``O``  — obligation set (residual verification duties)
* ``B``  — obstruction bundle (cohomology classes for metaclass conflicts)
* ``T``  — trust level from the ordered algebra
* ``Π``  — provenance chain (creation timestamp + transformation history)

Trust algebra
-------------

Trust is an ordered algebra ``𝔗 = (ℰ_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)``.  No
silent promotion is permitted.  CopilotChannel proposals—flagged by the
``COPILOT_SUGGESTED`` policy—enter at ``ORACLE_PROPOSED`` trust (level 2)
and require explicit corroboration before they can be promoted to
``SOLVER_DISCHARGED`` (level 4) or higher.  The copilot-assisted code
generation trust algebra is enforced at the channel boundary: records with
``channel=COPILOT`` carry a ``trust_ceiling`` annotation and a
``requires_corroboration`` flag that downstream consumers must honour.

Manifest responsibilities
-------------------------

:data:`CHAPTER_COVERAGE`
    Maps each Theory2.tex Ch20 section number to the Python module that
    implements its claims, together with coverage confidence and open TODOs.

:data:`EXPORTED_SYMBOLS`
    The complete public API surface of this sub-package, grouped by
    conceptual role.

:data:`THEORY_CLAIMS`
    Machine-readable description of every theorem in Ch20 (T20.1–T20.5).

:class:`ManifestRecord`
    Structured record for a single chapter-coverage entry.

:class:`SymbolGroup`
    Named cluster of exported symbols with descriptions.

:class:`ClaimSummary`
    Lightweight summary of a theorem claim linking to the full theorem
    objects in ``jugeo.python_runtime.metaobject_surfaces.theorems``.

:class:`PackageManifest`
    Root manifest object: validates coverage, resolves cross-references,
    and can emit a JSON report suitable for CI gating.

All copilot-assisted code generation within this sub-package is governed by
the same trust algebra: generated stubs enter at ``COPILOT_SUGGESTED`` and
must be promoted explicitly through review before they carry
``SOLVER_DISCHARGED`` or higher trust.

Theory alignment
----------------

§20.1  Package overview and site construction.
§20.2  Metaclass resolution as a judgment.
§20.3  Behavioral surfaces and protocol functoriality.
§20.4  Descriptor protocol and MRO morphisms.
§20.5  Class creation trace (three-phase protocol).
§20.6  MRO algorithms (C3 linearisation, monotonicity).
§20.7  Theorem catalog (T20.1–T20.5).
§20.8  Integration layer (judgment emitters, site builders).
§20.9  Core data models (MetaclassRecord, BehavioralSurface, …).
§20.10 Package public API (__init__.py exports).
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
    """Degree to which a Theory2.tex Ch20 section is covered by Python code.

    Levels are ordered from weakest (MISSING) to strongest (COMPLETE).  A CI
    gate may enforce a minimum level per section before a release is tagged.

    theory2.tex Ch20 §20.1
    """

    MISSING = "missing"
    STUB = "stub"
    PARTIAL = "partial"
    SUBSTANTIAL = "substantial"
    COMPLETE = "complete"

    @property
    def ordinal(self) -> int:
        """Integer rank for ordered comparison.

        Returns
        -------
        int
            0 (MISSING) through 4 (COMPLETE).
        """
        _ranks: dict[str, int] = {
            "missing": 0,
            "stub": 1,
            "partial": 2,
            "substantial": 3,
            "complete": 4,
        }
        return _ranks[self.value]

    def meets(self, minimum: "CoverageStatus") -> bool:
        """Return ``True`` if this status meets or exceeds *minimum*.

        Parameters
        ----------
        minimum:
            The minimum acceptable coverage level.

        Returns
        -------
        bool
        """
        return self.ordinal >= minimum.ordinal

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal <= other.ordinal

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal > other.ordinal

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal >= other.ordinal


# ---


class SymbolRole(Enum):
    """Conceptual role of an exported symbol in this package.

    Roles are used to group symbols in :data:`EXPORTED_SYMBOLS` and to
    answer queries such as "give me all algorithm symbols" or "give me all
    data-model symbols".

    theory2.tex Ch20 §20.1
    """

    DATA_MODEL = "data_model"
    ALGORITHM = "algorithm"
    INTEGRATION = "integration"
    CLAIM = "claim"
    THEOREM = "theorem"
    UTILITY = "utility"

    @property
    def description(self) -> str:
        """Return a one-line description of this role.

        Returns
        -------
        str
        """
        _descriptions: dict[str, str] = {
            "data_model": "Core data structures and record types.",
            "algorithm": "Procedural algorithms (MRO, linearisation, verification).",
            "integration": "Bridge classes connecting models to judgments and sites.",
            "claim": "Claim and theorem summary types.",
            "theorem": "Theorem catalog entries and proof-status types.",
            "utility": "Helper functions and internal utilities.",
        }
        return _descriptions.get(self.value, "")


# ---


class ClaimStatus(Enum):
    """Lifecycle status of a theory claim or theorem.

    PROPOSED → FORMALISED → PARTIALLY_VERIFIED → VERIFIED is the normal
    forward progression.  REFUTED is a terminal state indicating that the
    claim has been disproved.

    theory2.tex Ch20 §20.7
    """

    PROPOSED = "proposed"
    FORMALISED = "formalised"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    REFUTED = "refuted"

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if this status admits no further forward transitions.

        ``VERIFIED`` and ``REFUTED`` are both terminal.

        Returns
        -------
        bool
        """
        return self in (ClaimStatus.VERIFIED, ClaimStatus.REFUTED)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRecord:
    """Coverage record for a single Theory2.tex Ch20 section.

    Each record maps a section identifier (e.g. ``"§2001"``) to the Python
    module that implements its content, together with a confidence score,
    a list of open TODOs, and a textual summary of what is covered.

    Copilot-assisted sections carry ``COPILOT_SUGGESTED`` trust until reviewed
    and promoted through the trust algebra.  This mirrors the evidence channel
    discipline enforced throughout the jugeo judgment system.

    Parameters
    ----------
    section_id:
        The Theory2.tex section identifier, e.g. ``"§2001"``.  Must begin
        with the ``§`` character.
    section_title:
        Human-readable title of the section.
    module_path:
        Dotted Python module path, e.g.
        ``"jugeo.python_runtime.metaobject_surfaces.manifest"``.
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
        Whether any part of the module was scaffolded with CopilotChannel
        assistance.  Copilot-assisted sections carry ``COPILOT_SUGGESTED``
        trust until reviewed and promoted.

    theory2.tex Ch20 §20.1
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

    def coverage_gap(self) -> float:
        """Return the fractional gap to full coverage.

        A record with ``confidence=0.85`` returns the complement ``0.15``.
        A fully-confident record returns ``0.0``.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        return round(1.0 - self.confidence, 6)

    def is_complete(self) -> bool:
        """Return True if this section has COMPLETE coverage status.

        Returns
        -------
        bool
        """
        return self.status == CoverageStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
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
        """Deserialise from a dictionary produced by :meth:`to_dict`.

        Parameters
        ----------
        data:
            A mapping produced by :meth:`to_dict`.

        Returns
        -------
        ManifestRecord
        """
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


# ---


@dataclass(frozen=True)
class SymbolGroup:
    """Named cluster of related exported symbols.

    Groups symbols by conceptual role so that manifest consumers can quickly
    find all data-model symbols, all algorithm symbols, etc.

    Parameters
    ----------
    name:
        Short name for the group, e.g. ``"core_models"``.
    role:
        Conceptual role shared by all symbols in this group.
    symbols:
        Tuple of fully-qualified symbol names.
    description:
        Prose description of what the group provides.
    source_module:
        The module that defines all symbols in this group.

    theory2.tex Ch20 §20.1
    """

    name: str
    role: SymbolRole
    symbols: tuple[str, ...]
    description: str
    source_module: str

    def contains(self, symbol: str) -> bool:
        """Return True if *symbol* is a member of this group.

        Matches both fully-qualified names and short names (the part after
        the last ``"."``) so that callers can pass either form.

        Parameters
        ----------
        symbol:
            The symbol name to look up.

        Returns
        -------
        bool
        """
        return any(
            s == symbol or s.endswith(f".{symbol}") for s in self.symbols
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "role": self.role.value,
            "symbols": list(self.symbols),
            "description": self.description,
            "source_module": self.source_module,
        }


# ---


@dataclass(frozen=True)
class ClaimSummary:
    """Lightweight summary of a theory claim or theorem.

    This is the manifest-level view.  Full structured theorem objects live in
    :mod:`jugeo.python_runtime.metaobject_surfaces.theorems`.

    Parameters
    ----------
    claim_id:
        Short identifier, e.g. ``"C20.1"``.
    title:
        One-line claim title.
    status:
        Current lifecycle status from :class:`ClaimStatus`.
    theory_section:
        Theory2.tex section that states this claim.
    implementing_module:
        Python module that provides the claim's verification logic.
    falsification_module:
        Python module that provides falsification criteria.
    evidence_required:
        Short names of evidence types needed to verify the claim.

    theory2.tex Ch20 §20.7
    """

    claim_id: str
    title: str
    status: ClaimStatus
    theory_section: str
    implementing_module: str
    falsification_module: str
    evidence_required: tuple[str, ...]

    def is_open(self) -> bool:
        """Return True if the claim has not yet been resolved.

        A claim is open if it is PROPOSED, FORMALISED, or PARTIALLY_VERIFIED.
        VERIFIED and REFUTED are terminal and therefore not open.

        Returns
        -------
        bool
        """
        return self.status in (
            ClaimStatus.PROPOSED,
            ClaimStatus.FORMALISED,
            ClaimStatus.PARTIALLY_VERIFIED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
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

_BASE = "jugeo.python_runtime.metaobject_surfaces"

CHAPTER_COVERAGE: tuple[ManifestRecord, ...] = (
    ManifestRecord(
        section_id="§2001",
        section_title="Ch20 Overview — Metaobject Protocol in the Site",
        module_path=f"{_BASE}.manifest",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.90,
        open_todos=(
            "Add cross-ref validation against theorem catalog",
            "Wire confidence scores to CI gate",
            "Verify copilot-assisted section promotion audit trail",
        ),
        summary=(
            "The manifest module encodes the Ch20 overview: the full package "
            "API surface, theory-claim coverage records, and exported symbol "
            "groups.  CopilotChannel-assisted sections are flagged for explicit "
            "trust promotion.  The CHAPTER_COVERAGE, EXPORTED_SYMBOLS, and "
            "THEORY_CLAIMS tables provide machine-readable access to the "
            "package scope."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2002",
        section_title="Metaclass Resolution",
        module_path=f"{_BASE}.metaclasses",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Implement metaclass conflict cohomology class computation",
            "Add solver-backed metaclass compatibility check",
        ),
        summary=(
            "The metaclass module provides MetaclassRecord, metaclass resolution "
            "logic, and conflict detection.  Every metaclass resolution event is "
            "modelled as a STRUCTURAL judgment with trust derived from the "
            "resolution channel (RUNTIME_WITNESSED or ORACLE_PROPOSED for "
            "CopilotChannel-suggested resolutions).  Metaclass conflicts are "
            "represented as Obstruction objects with cohomology class labels."
        ),
    ),
    ManifestRecord(
        section_id="§2003",
        section_title="Behavioral Surfaces",
        module_path=f"{_BASE}.behavioral_surfaces",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.86,
        open_todos=(
            "Implement protocol functoriality composition check",
            "Add abstract method obligation tracking",
        ),
        summary=(
            "The behavioral surfaces module models each class's protocol "
            "implementation surface as a judgment-indexed covering family in the "
            "site.  BehavioralSurface objects record dunder methods, abstract "
            "methods, and protocol memberships.  The covering family construction "
            "in §20.3 is implemented via as_covering_family() with overlap data "
            "encoding protocol intersection conditions."
        ),
    ),
    ManifestRecord(
        section_id="§2004",
        section_title="Descriptor Protocol",
        module_path=f"{_BASE}.descriptors",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.87,
        open_todos=(
            "Implement __set_name__ trace integration",
            "Add data-descriptor precedence proof obligation",
        ),
        summary=(
            "The descriptor module captures MRO-ordered attribute resolution as "
            "a sequence of RESTRICTION morphisms in the site.  DescriptorChain "
            "objects record the full resolution chain with descriptor kind "
            "(DATA vs NON_DATA), override maps, and trust annotations.  "
            "CopilotChannel-proposed chains carry ORACLE_PROPOSED trust and are "
            "marked with the COPILOT_SUGGESTED policy for explicit promotion."
        ),
    ),
    ManifestRecord(
        section_id="§2005",
        section_title="Class Creation Trace",
        module_path=f"{_BASE}.class_creation",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.85,
        open_todos=(
            "Implement __init_subclass__ obligation chain",
            "Add __set_name__ morphism to creation trace",
        ),
        summary=(
            "The class creation module models the three-phase class creation "
            "protocol (__prepare__ / body / __new__) as a sequence of three "
            "Judgment objects, each with its own coordinate, proposition, and "
            "trust level.  ClassCreationTrace records the full context including "
            "metaclass, body names, and init_subclass_called flag.  CopilotChannel "
            "annotations for creation traces are supported via copilot_annotation()."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2006",
        section_title="MRO Algorithms",
        module_path=f"{_BASE}.algorithms",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.89,
        open_todos=(
            "Prove MRO monotonicity invariant via solver",
            "Add linearisation conflict detection test",
        ),
        summary=(
            "The algorithms module implements C3 linearisation, MRO "
            "well-foundedness checks, and descriptor precedence verification.  "
            "Each algorithm produces a Judgment with appropriate trust: "
            "computationally verified MRO orderings carry RUNTIME_WITNESSED "
            "trust while solver-discharged monotonicity proofs carry "
            "SOLVER_DISCHARGED trust.  The module directly implements T20.1 "
            "(MRO Well-Foundedness) and T20.2 (Descriptor Data Precedence)."
        ),
    ),
    ManifestRecord(
        section_id="§2007",
        section_title="Theorem Catalog",
        module_path=f"{_BASE}.theorems",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.88,
        open_todos=(
            "Add proof sketch validation for T20.3",
            "Link theorem entries to implementing functions",
        ),
        summary=(
            "The theorem catalog enumerates all five formal theorems from Ch20 "
            "(T20.1–T20.5) with their proof status, dependencies, and links to "
            "implementing modules.  Each theorem entry records the theory section, "
            "the falsification criterion, and the evidence types required for "
            "verification.  CopilotChannel-assisted theorem scaffolding carries "
            "ORACLE_PROPOSED trust until reviewer-promoted."
        ),
    ),
    ManifestRecord(
        section_id="§2008",
        section_title="Integration Layer",
        module_path=f"{_BASE}.integration",
        status=CoverageStatus.PARTIAL,
        confidence=0.80,
        open_todos=(
            "Complete artifact cross-reference table",
            "Add automated import resolution for integration bridges",
            "Wire DescriptorChainChannelBridge to ChannelRouter",
        ),
        summary=(
            "The integration module connects the abstract metaobject records to "
            "concrete judgment tuples, site coordinates, and evidence channel "
            "pipelines.  Four bridge classes are provided: "
            "MetaclassJudgmentIntegrator, BehavioralSurfaceSiteBuilder, "
            "DescriptorChainChannelBridge, and ClassCreationJudgmentEmitter.  "
            "CopilotChannel evidence is admitted at ORACLE_PROPOSED trust via "
            "dedicated emit_copilot_evidence() and copilot_annotation() methods."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§2009",
        section_title="Core Models",
        module_path=f"{_BASE}.models",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.91,
        open_todos=(
            "Add JSON round-trip tests for all models",
            "Implement MetaclassRecord.as_carrier() delegation",
        ),
        summary=(
            "The models module provides the four core frozen dataclasses: "
            "MetaclassRecord, BehavioralSurface, DescriptorChain, and "
            "ClassCreationTrace.  Each record type includes as_judgment(), "
            "as_covering_family(), and as_morphism_sequence() delegation methods "
            "that bridge to the judgment algebra.  All trust annotations follow "
            "the jugeo trust algebra with no silent promotion."
        ),
    ),
    ManifestRecord(
        section_id="§2010",
        section_title="Package API",
        module_path=f"{_BASE}.__init__",
        status=CoverageStatus.COMPLETE,
        confidence=0.95,
        open_todos=(),
        summary=(
            "The package __init__.py exports the four core model types "
            "(MetaclassRecord, BehavioralSurface, DescriptorChain, "
            "ClassCreationTrace) and the MANIFEST singleton.  The public API "
            "surface is deliberately minimal and stable: integration, algorithm, "
            "and theorem symbols are accessed via explicit sub-module imports "
            "to keep the top-level namespace uncluttered."
        ),
    ),
)

# ---------------------------------------------------------------------------
# Exported symbols
# ---------------------------------------------------------------------------

EXPORTED_SYMBOLS: tuple[SymbolGroup, ...] = (
    SymbolGroup(
        name="manifest_types",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            f"{_BASE}.manifest.ManifestRecord",
            f"{_BASE}.manifest.SymbolGroup",
            f"{_BASE}.manifest.ClaimSummary",
            f"{_BASE}.manifest.PackageManifest",
            f"{_BASE}.manifest.CoverageStatus",
            f"{_BASE}.manifest.SymbolRole",
            f"{_BASE}.manifest.ClaimStatus",
        ),
        description=(
            "Root manifest types for package-level introspection.  These types "
            "mirror the structure from jugeo.thesis.research_program.manifest and "
            "are adapted for Ch20 metaobject protocol coverage."
        ),
        source_module=f"{_BASE}.manifest",
    ),
    SymbolGroup(
        name="core_models",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            f"{_BASE}.models.MetaclassRecord",
            f"{_BASE}.models.BehavioralSurface",
            f"{_BASE}.models.DescriptorChain",
            f"{_BASE}.models.ClassCreationTrace",
        ),
        description=(
            "The four core frozen-dataclass record types that model Python MOP "
            "events: metaclass resolution, behavioral surface protocol membership, "
            "descriptor chain resolution, and three-phase class creation."
        ),
        source_module=f"{_BASE}.models",
    ),
    SymbolGroup(
        name="metaclass_analysis",
        role=SymbolRole.ALGORITHM,
        symbols=(
            f"{_BASE}.metaclasses.MetaclassResolver",
            f"{_BASE}.metaclasses.MetaclassConflictDetector",
            f"{_BASE}.metaclasses.mro_metaclass_check",
        ),
        description=(
            "Metaclass resolution algorithms and conflict detection logic.  "
            "Implements §20.2 and T20.5 (Metaclass Conflict Obstruction).  "
            "Conflict obstructions are represented as H1 cohomology classes."
        ),
        source_module=f"{_BASE}.metaclasses",
    ),
    SymbolGroup(
        name="descriptor_analysis",
        role=SymbolRole.ALGORITHM,
        symbols=(
            f"{_BASE}.descriptors.DescriptorResolver",
            f"{_BASE}.descriptors.DataDescriptorPrecedence",
            f"{_BASE}.descriptors.descriptor_chain_for",
        ),
        description=(
            "Descriptor protocol resolution algorithms implementing §20.4 and "
            "T20.2 (Descriptor Data Precedence).  Data descriptors take precedence "
            "over instance __dict__ entries; this invariant is checked and "
            "evidence-recorded at RUNTIME_WITNESSED trust."
        ),
        source_module=f"{_BASE}.descriptors",
    ),
    SymbolGroup(
        name="behavioral_surfaces",
        role=SymbolRole.ALGORITHM,
        symbols=(
            f"{_BASE}.behavioral_surfaces.BehavioralSurfaceAnalyser",
            f"{_BASE}.behavioral_surfaces.ProtocolMembershipChecker",
            f"{_BASE}.behavioral_surfaces.surface_for_class",
        ),
        description=(
            "Behavioral surface construction and protocol membership analysis "
            "implementing §20.3 and T20.3 (Behavioral Surface Functoriality).  "
            "Each surface is a judgment-indexed covering family over protocol "
            "coordinates."
        ),
        source_module=f"{_BASE}.behavioral_surfaces",
    ),
    SymbolGroup(
        name="class_creation",
        role=SymbolRole.ALGORITHM,
        symbols=(
            f"{_BASE}.class_creation.ClassCreationMonitor",
            f"{_BASE}.class_creation.PhaseTracer",
            f"{_BASE}.class_creation.trace_class_creation",
        ),
        description=(
            "Class creation tracing implementing §20.5 and T20.4 (Class "
            "Creation Monotonicity).  The monitor instruments the three-phase "
            "protocol (__prepare__ / body / __new__) and emits one judgment per "
            "phase."
        ),
        source_module=f"{_BASE}.class_creation",
    ),
    SymbolGroup(
        name="algorithms",
        role=SymbolRole.ALGORITHM,
        symbols=(
            f"{_BASE}.algorithms.C3Lineariser",
            f"{_BASE}.algorithms.MROWellFoundednessChecker",
            f"{_BASE}.algorithms.linearise_mro",
            f"{_BASE}.algorithms.check_mro_monotonicity",
        ),
        description=(
            "Core MRO and linearisation algorithms implementing §20.6 and T20.1 "
            "(MRO Well-Foundedness).  The C3 lineariser produces the standard "
            "Python MRO with a well-foundedness proof obligation attached as a "
            "residual obligation on the resulting judgment."
        ),
        source_module=f"{_BASE}.algorithms",
    ),
    SymbolGroup(
        name="integration",
        role=SymbolRole.INTEGRATION,
        symbols=(
            f"{_BASE}.integration.MetaclassJudgmentIntegrator",
            f"{_BASE}.integration.BehavioralSurfaceSiteBuilder",
            f"{_BASE}.integration.DescriptorChainChannelBridge",
            f"{_BASE}.integration.ClassCreationJudgmentEmitter",
        ),
        description=(
            "Integration bridge classes connecting metaobject records to the "
            "judgment algebra, Grothendieck site, and evidence channel pipeline "
            "(§20.8).  CopilotChannel evidence is admitted at ORACLE_PROPOSED "
            "trust via dedicated emit_copilot_evidence() methods."
        ),
        source_module=f"{_BASE}.integration",
    ),
    SymbolGroup(
        name="theorems",
        role=SymbolRole.THEOREM,
        symbols=(
            f"{_BASE}.theorems.TheoremCatalog",
            f"{_BASE}.theorems.TheoremEntry",
            f"{_BASE}.theorems.ProofStatus",
            f"{_BASE}.theorems.THEOREM_CATALOG",
        ),
        description=(
            "Theorem catalog for Ch20 (T20.1–T20.5) with proof status, "
            "dependencies, and links to implementing modules.  Implements §20.7."
        ),
        source_module=f"{_BASE}.theorems",
    ),
)

# ---------------------------------------------------------------------------
# Theory claims summary table
# ---------------------------------------------------------------------------

THEORY_CLAIMS: tuple[ClaimSummary, ...] = (
    ClaimSummary(
        claim_id="C20.1",
        title="MRO Well-Foundedness",
        status=ClaimStatus.FORMALISED,
        theory_section="§2006",
        implementing_module=f"{_BASE}.algorithms",
        falsification_module=f"{_BASE}.theorems",
        evidence_required=(
            "c3_linearisation_terminates",
            "mro_consistent_with_bases",
            "mro_monotonicity_invariant",
        ),
    ),
    ClaimSummary(
        claim_id="C20.2",
        title="Descriptor Data Precedence",
        status=ClaimStatus.VERIFIED,
        theory_section="§2004",
        implementing_module=f"{_BASE}.descriptors",
        falsification_module=f"{_BASE}.theorems",
        evidence_required=(
            "data_descriptor_beats_instance_dict",
            "non_data_descriptor_yields_to_instance_dict",
        ),
    ),
    ClaimSummary(
        claim_id="C20.3",
        title="Behavioral Surface Functoriality",
        status=ClaimStatus.FORMALISED,
        theory_section="§2003",
        implementing_module=f"{_BASE}.behavioral_surfaces",
        falsification_module=f"{_BASE}.theorems",
        evidence_required=(
            "protocol_morphism_composition_law",
            "covering_family_gluing_condition",
            "behavioral_surface_functor_naturality",
        ),
    ),
    ClaimSummary(
        claim_id="C20.4",
        title="Class Creation Monotonicity",
        status=ClaimStatus.FORMALISED,
        theory_section="§2005",
        implementing_module=f"{_BASE}.class_creation",
        falsification_module=f"{_BASE}.theorems",
        evidence_required=(
            "three_phase_judgment_sequence_settled",
            "namespace_monotone_growth",
            "metaclass_coordinate_stable",
        ),
    ),
    ClaimSummary(
        claim_id="C20.5",
        title="Metaclass Conflict Obstruction",
        status=ClaimStatus.FORMALISED,
        theory_section="§2002",
        implementing_module=f"{_BASE}.metaclasses",
        falsification_module=f"{_BASE}.theorems",
        evidence_required=(
            "metaclass_conflict_generates_obstruction",
            "obstruction_cohomology_class_nontrivial",
            "conflict_repair_hints_exhaustive",
        ),
    ),
)


# ---------------------------------------------------------------------------
# PackageManifest — root manifest object
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """Root manifest for the ``jugeo.python_runtime.metaobject_surfaces`` package.

    Aggregates :data:`CHAPTER_COVERAGE`, :data:`EXPORTED_SYMBOLS`, and
    :data:`THEORY_CLAIMS` and provides validation, querying, and reporting
    methods.

    All copilot-assisted sections are tracked via the ``copilot_assisted``
    flag on their :class:`ManifestRecord`.  The :meth:`copilot_assisted_sections`
    method returns all such records so that review tooling can enforce the
    COPILOT_SUGGESTED → ORACLE_PROPOSED → SOLVER_DISCHARGED promotion pipeline.

    Parameters
    ----------
    chapter_coverage:
        Tuple of :class:`ManifestRecord` objects, one per §2001–§2010.
    exported_symbols:
        Tuple of :class:`SymbolGroup` objects.
    theory_claims:
        Tuple of :class:`ClaimSummary` objects for T20.1–T20.5.
    package_name:
        Dotted name of the package this manifest describes.
    created_at:
        Unix timestamp when this manifest was instantiated.

    theory2.tex Ch20 §20.1
    """

    chapter_coverage: tuple[ManifestRecord, ...]
    exported_symbols: tuple[SymbolGroup, ...]
    theory_claims: tuple[ClaimSummary, ...]
    package_name: str = "jugeo.python_runtime.metaobject_surfaces"
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Coverage queries
    # ------------------------------------------------------------------

    def coverage_for(self, section_id: str) -> ManifestRecord | None:
        """Return the coverage record for the given section identifier.

        Parameters
        ----------
        section_id:
            The section identifier to look up, e.g. ``"§2001"``.

        Returns
        -------
        ManifestRecord | None
            The matching record, or ``None`` if not found.
        """
        for rec in self.chapter_coverage:
            if rec.section_id == section_id:
                return rec
        return None

    def symbols_for_role(self, role: SymbolRole) -> list[SymbolGroup]:
        """Return all symbol groups with the given conceptual role.

        Parameters
        ----------
        role:
            The :class:`SymbolRole` to filter by.

        Returns
        -------
        list[SymbolGroup]
        """
        return [g for g in self.exported_symbols if g.role == role]

    def open_claims(self) -> list[ClaimSummary]:
        """Return all theory claims that have not yet been resolved.

        A claim is open if its status is PROPOSED, FORMALISED, or
        PARTIALLY_VERIFIED.

        Returns
        -------
        list[ClaimSummary]
        """
        return [c for c in self.theory_claims if c.is_open()]

    def coverage_report(self) -> dict[str, Any]:
        """Build a summary dictionary of section coverage statistics.

        Returns a dictionary with keys ``total_sections``,
        ``complete_sections``, ``substantial_sections``, ``partial_sections``,
        ``stub_sections``, ``missing_sections``, ``mean_confidence``,
        ``total_open_todos``, ``open_claims_count``, and
        ``copilot_assisted_count``.

        Returns
        -------
        dict[str, Any]
        """
        status_counts: dict[str, int] = {s.value: 0 for s in CoverageStatus}
        for rec in self.chapter_coverage:
            status_counts[rec.status.value] += 1
        confidences = [r.confidence for r in self.chapter_coverage]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return {
            "package_name": self.package_name,
            "total_sections": len(self.chapter_coverage),
            "complete_sections": status_counts.get("complete", 0),
            "substantial_sections": status_counts.get("substantial", 0),
            "partial_sections": status_counts.get("partial", 0),
            "stub_sections": status_counts.get("stub", 0),
            "missing_sections": status_counts.get("missing", 0),
            "mean_confidence": round(mean_conf, 4),
            "total_open_todos": sum(len(r.open_todos) for r in self.chapter_coverage),
            "open_claims_count": len(self.open_claims()),
            "copilot_assisted_count": len(self.copilot_assisted_sections()),
        }

    def validate(self) -> list[str]:
        """Return a list of validation errors for this manifest.

        Checks performed:

        * No two records share the same ``section_id``.
        * All ``confidence`` values are in ``[0.0, 1.0]``.
        * COMPLETE sections must have ``confidence >= 0.95``.
        * All claim ``theory_section`` values appear in ``chapter_coverage``.

        Returns
        -------
        list[str]
            An empty list if the manifest is valid.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for rec in self.chapter_coverage:
            if rec.section_id in seen_ids:
                errors.append(f"Duplicate section_id: {rec.section_id!r}")
            seen_ids.add(rec.section_id)
            if not 0.0 <= rec.confidence <= 1.0:
                errors.append(
                    f"{rec.section_id}: confidence {rec.confidence!r} out of [0,1]"
                )
            if rec.status == CoverageStatus.COMPLETE and rec.confidence < 0.95:
                errors.append(
                    f"{rec.section_id}: COMPLETE status requires confidence >= 0.95, "
                    f"got {rec.confidence!r}"
                )
        for claim in self.theory_claims:
            section_ids = {r.section_id for r in self.chapter_coverage}
            if claim.theory_section not in section_ids:
                errors.append(
                    f"Claim {claim.claim_id!r} references unknown section "
                    f"{claim.theory_section!r}"
                )
        return errors

    def to_json(self, indent: int = 2) -> str:
        """Serialise the full manifest to a JSON string.

        Parameters
        ----------
        indent:
            JSON indentation level (default 2).

        Returns
        -------
        str
        """
        data: dict[str, Any] = {
            "package_name": self.package_name,
            "created_at": self.created_at,
            "coverage_report": self.coverage_report(),
            "chapter_coverage": [r.to_dict() for r in self.chapter_coverage],
            "exported_symbols": [g.to_dict() for g in self.exported_symbols],
            "theory_claims": [c.to_dict() for c in self.theory_claims],
        }
        return json.dumps(data, indent=indent)

    def symbol_count(self) -> int:
        """Return the total number of exported symbols across all groups.

        Returns
        -------
        int
        """
        return sum(len(g.symbols) for g in self.exported_symbols)

    def minimum_coverage(self) -> CoverageStatus:
        """Return the minimum coverage status across all records.

        Useful for CI gating: the gate fails if this value is below a
        configured threshold.

        Returns
        -------
        CoverageStatus
            The weakest status present in the chapter coverage table.
        """
        if not self.chapter_coverage:
            return CoverageStatus.MISSING
        return min(self.chapter_coverage, key=lambda r: r.status.ordinal).status

    def copilot_assisted_sections(self) -> list[ManifestRecord]:
        """Return all records where CopilotChannel assistance was used.

        These sections carry COPILOT_SUGGESTED trust and require explicit
        promotion through the trust algebra before any downstream CI gate
        can treat them as SOLVER_DISCHARGED.

        Returns
        -------
        list[ManifestRecord]
        """
        return [r for r in self.chapter_coverage if r.copilot_assisted]

    def content_hash(self) -> str:
        """Return a SHA-256 digest of the canonical JSON representation.

        Returns
        -------
        str
            64-character hex digest.
        """
        data: dict[str, Any] = {
            "chapter_coverage": [r.to_dict() for r in self.chapter_coverage],
            "exported_symbols": [g.to_dict() for g in self.exported_symbols],
            "theory_claims": [c.to_dict() for c in self.theory_claims],
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def mean_confidence(self) -> float:
        """Return the arithmetic mean confidence across all coverage records.

        Returns
        -------
        float
        """
        if not self.chapter_coverage:
            return 0.0
        return sum(r.confidence for r in self.chapter_coverage) / len(
            self.chapter_coverage
        )


# ---------------------------------------------------------------------------
# MANIFEST singleton
# ---------------------------------------------------------------------------

MANIFEST: PackageManifest = PackageManifest(
    chapter_coverage=CHAPTER_COVERAGE,
    exported_symbols=EXPORTED_SYMBOLS,
    theory_claims=THEORY_CLAIMS,
)
