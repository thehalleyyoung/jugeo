from __future__ import annotations

r"""Import Graph Package Manifest — theory2.tex Ch19 §19.0.

This module provides the canonical manifest for
``jugeo.python_runtime.import_graph``, the Python implementation companion to
Theory2.tex Chapter 19: *Python Runtime Import Graph as a Grothendieck Site*.

JuGeo (Judgment Geometry) treats every Python module as an object in a site
category and every import statement as a morphism.  The presheaf of module
namespaces over this site satisfies descent (gluing) conditions with respect to
the Grothendieck topology induced by package boundaries.

Trust is an ordered algebra :math:`\mathfrak{T} = (\mathcal{E}_\mathrm{adm},
\preceq, \oplus, \ominus, \uparrow_\pi, \downarrow_\chi)`.  Copilot-assisted
import analysis always enters at ``ORACLE_PROPOSED`` (tier 2); promotion to
``RUNTIME_WITNESSED`` or higher requires an explicit evidence step, typically
a successful runtime import or a solver proof of module availability.  No
silent promotion is permitted.

Manifest responsibilities
-------------------------

:data:`CHAPTER_COVERAGE`
    Maps each Theory2.tex §190x section to the Python module that implements
    its claims, together with coverage confidence and open TODOs.

:data:`EXPORTED_SYMBOLS`
    The complete public API surface of this sub-package, grouped by conceptual
    role.

:data:`THEORY_CLAIMS`
    Machine-readable summary of the four theory claims in Ch.19.

:class:`PackageManifest`
    Root manifest object: validates coverage, resolves cross-references, and
    emits a JSON report suitable for CI gating.

All copilot-assisted code generation within this sub-package is governed by
the same trust algebra: generated stubs enter at ``ORACLE_PROPOSED`` and
must be promoted explicitly through review before they carry
``SOLVER_DISCHARGED`` or higher trust.

Theory alignment
----------------

Section §1900 of Theory2.tex ("Import Graph Overview") is the primary
reference.  Section §1901 defines the site structure; §1902 proves the
fixed-point convergence theorem; §1903 establishes associativity and
trust-monotonicity of re-export composition; §1904 proves the trust ceiling
for dynamic loads.  This manifest encodes all four claims in machine-readable
form.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

# ---
# Enumerations
# ---


class CoverageStatus(Enum):
    """Degree to which a Theory2.tex section is covered by Python code.

    Levels are strictly ordered from weakest to strongest.  A CI gate may
    enforce a minimum level per section before a release is tagged.  The
    copilot CI adapter reads these values from the manifest to produce
    per-section pass/fail badges.

    Levels
    ------
    MISSING
        No Python module exists for this section.
    STUB
        A module exists but contains only stubs or ``...`` bodies.
    PARTIAL
        Core data structures are defined but algorithms are incomplete.
    SUBSTANTIAL
        All major constructs are implemented; minor details may be missing.
    COMPLETE
        Full fidelity to the theory section, including edge cases.
    """

    MISSING = "missing"
    STUB = "stub"
    PARTIAL = "partial"
    SUBSTANTIAL = "substantial"
    COMPLETE = "complete"

    @property
    def ordinal(self) -> int:
        """Integer rank for comparison (0 = weakest, 4 = strongest).

        Used by :meth:`__lt__` and :meth:`__le__` to implement the strict
        partial order over coverage levels.  The copilot CI gate uses this
        ordinal when computing the minimum coverage threshold across all
        sections.

        Returns
        -------
        int
            Ordinal rank of this coverage status.
        """
        _ranks: dict[str, int] = {
            "missing": 0,
            "stub": 1,
            "partial": 2,
            "substantial": 3,
            "complete": 4,
        }
        return _ranks[self.value]

    def __lt__(self, other: object) -> bool:
        """Return ``True`` when this status is strictly weaker than *other*.

        Enables sorting and threshold comparisons in the copilot CI gate.
        Only compares against other :class:`CoverageStatus` instances.

        Parameters
        ----------
        other:
            The status to compare against.

        Returns
        -------
        bool
            ``True`` when ``self.ordinal < other.ordinal``.
        """
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal < other.ordinal

    def __le__(self, other: object) -> bool:
        """Return ``True`` when this status is weaker than or equal to *other*.

        Used by the CI gate to test whether a section meets the minimum
        threshold for release.

        Parameters
        ----------
        other:
            The status to compare against.

        Returns
        -------
        bool
            ``True`` when ``self.ordinal <= other.ordinal``.
        """
        if not isinstance(other, CoverageStatus):
            return NotImplemented
        return self.ordinal <= other.ordinal


class SymbolRole(Enum):
    """Conceptual role of an exported symbol in the import-graph package.

    Roles are used to group symbols in :class:`SymbolGroup` entries and to
    generate categorised API documentation.  Copilot uses these roles when
    deciding which symbols to surface in quick-fix suggestions.

    Values
    ------
    DATA_MODEL
        Frozen dataclass representing a domain object.
    CLAIM
        A theorem claim or formal proposition.
    ALGORITHM
        A function or class implementing a graph algorithm.
    THEOREM
        A formally verified proposition with proof sketch.
    INTEGRATION
        Glue code connecting the import graph to external systems.
    UTILITY
        Helper function or internal infrastructure symbol.
    """

    DATA_MODEL = "data_model"
    CLAIM = "claim"
    ALGORITHM = "algorithm"
    THEOREM = "theorem"
    INTEGRATION = "integration"
    UTILITY = "utility"


class ClaimStatus(Enum):
    """Lifecycle status of a theory claim in Ch.19.

    Claims progress from ``PROPOSED`` through ``FORMALISED`` and
    ``PARTIALLY_VERIFIED`` to ``VERIFIED``.  Negative outcomes are encoded as
    ``FALSIFIED`` or ``RETRACTED``.  The copilot evidence pipeline tracks
    claim status across commits to detect regressions.

    Values
    ------
    PROPOSED
        Initial state; claim is stated but not yet formalised.
    FORMALISED
        Claim has been given a precise mathematical statement.
    PARTIALLY_VERIFIED
        Some but not all sub-conditions have been verified.
    VERIFIED
        All conditions verified; claim is settled.
    FALSIFIED
        A counter-example has been found; claim is discharged negatively.
    RETRACTED
        Claim withdrawn; no longer part of the theory.
    """

    PROPOSED = "proposed"
    FORMALISED = "formalised"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    RETRACTED = "retracted"


# ---
# Data structures
# ---


@dataclass(frozen=True)
class ManifestRecord:
    """Coverage record for a single Theory2.tex section in Ch.19.

    Each record maps a section identifier (e.g. ``"§1901"``) to the Python
    module that implements its content, together with a confidence score,
    a list of open TODOs, and a textual summary of what is covered.

    Copilot-assisted sections carry ``copilot_assisted=True`` to indicate
    that initial scaffolding used oracle-level proposals.  Promotion to
    higher trust requires an explicit review step.

    Parameters
    ----------
    section_id:
        The Theory2.tex section identifier, e.g. ``"§1901"``.
    section_title:
        Human-readable title of the section.
    module_path:
        Dotted Python module path, e.g.
        ``"jugeo.python_runtime.import_graph.models"``.
    status:
        Coverage status from :class:`CoverageStatus`.
    confidence:
        Float in ``[0.0, 1.0]`` estimating how faithfully the Python module
        captures the theory.  Derived from author review.
    open_todos:
        Unresolved implementation gaps as short strings.
    summary:
        One-paragraph prose summary of what the module covers.
    copilot_assisted:
        Whether any part of the module was scaffolded with copilot assistance.
        Copilot-assisted sections carry ``ORACLE_PROPOSED`` trust until
        reviewed and promoted via an explicit evidence step.
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
        """Validate field constraints on construction.

        Raises
        ------
        ValueError
            If :attr:`confidence` is not in ``[0.0, 1.0]``, or if
            :attr:`section_id` does not start with ``§``.
        """
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence!r}"
            )
        if not self.section_id.startswith("§"):
            raise ValueError(
                f"section_id must start with §, got {self.section_id!r}"
            )

    def is_complete(self) -> bool:
        """Return ``True`` if this section has ``COMPLETE`` coverage status.

        Used by the copilot CI gate to identify sections that require no
        further work before a chapter is tagged for release.

        Returns
        -------
        bool
            ``True`` when :attr:`status` is ``CoverageStatus.COMPLETE``.
        """
        return self.status == CoverageStatus.COMPLETE

    def coverage_gap(self) -> float:
        """Return the fractional gap to full coverage.

        A record with ``confidence=0.85`` and status ``SUBSTANTIAL`` returns
        the complement ``0.15``.  Copilot uses this value to rank sections
        by implementation priority in the release planning dashboard.

        Returns
        -------
        float
            ``1.0 - self.confidence``, always in ``[0.0, 1.0]``.
        """
        return 1.0 - self.confidence

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        All fields are rendered as primitive Python types.  The
        :attr:`status` enum is stored as its string value.  Copilot
        CI tooling reads these dicts from the manifest JSON export to
        generate per-section coverage badges.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`ManifestRecord`.
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
    def from_dict(cls, data: Mapping[str, Any]) -> ManifestRecord:
        """Deserialise from a dictionary produced by :meth:`to_dict`.

        Used by the copilot CI pipeline to reload a previously serialised
        manifest and compare it against the current source tree.

        Parameters
        ----------
        data:
            A mapping as produced by :meth:`to_dict`.

        Returns
        -------
        ManifestRecord
            A new instance constructed from *data*.
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


@dataclass(frozen=True)
class SymbolGroup:
    """Named cluster of related exported symbols.

    Symbol groups organise the public API by conceptual role, making it
    easier for copilot to surface relevant completions and for documentation
    generators to produce structured API references.

    Parameters
    ----------
    name:
        Short name for the group, e.g. ``"import_graph_models"``.
    role:
        Conceptual role shared by all symbols in this group.
    symbols:
        Tuple of fully-qualified symbol names exported from
        :attr:`source_module`.
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
        """Return ``True`` if *symbol* is in this group.

        Matches both the fully qualified name (``"jugeo.…ClassName"``) and
        the short name (``"ClassName"``).  Copilot completion filtering uses
        this to restrict suggestions to the symbols relevant to the current
        import context.

        Parameters
        ----------
        symbol:
            The symbol to look up, either short or fully qualified.

        Returns
        -------
        bool
            ``True`` when a matching entry exists in :attr:`symbols`.
        """
        return any(
            s == symbol or s.endswith(f".{symbol}") for s in self.symbols
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        The :attr:`role` enum is stored as its string value.  Copilot
        documentation tooling reads these dicts when generating the public
        API index page for the import-graph sub-package.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`SymbolGroup`.
        """
        return {
            "name": self.name,
            "role": self.role.value,
            "symbols": list(self.symbols),
            "description": self.description,
            "source_module": self.source_module,
        }


@dataclass(frozen=True)
class ClaimSummary:
    """Lightweight summary of a Ch.19 theory claim.

    This is the manifest-level view of a claim; the full structured object
    (with proof sketch and evidence plan) lives in the corresponding
    theorem module.

    Copilot tracks claim status across commits.  A claim that transitions
    from ``PARTIALLY_VERIFIED`` back to ``PROPOSED`` triggers a regression
    alert in the CI pipeline.

    Parameters
    ----------
    claim_id:
        Short identifier, e.g. ``"C19_1"``.
    title:
        One-line claim title.
    status:
        Current lifecycle status.
    theory_section:
        Theory2.tex section that formally states this claim.
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
        """Return ``True`` if the claim has not yet reached a terminal status.

        Open claims are ``PROPOSED``, ``FORMALISED``, and
        ``PARTIALLY_VERIFIED``.  Copilot surfaces open claims in the IDE
        progress sidebar to keep them visible during implementation sprints.

        Returns
        -------
        bool
            ``True`` when the claim is still in progress.
        """
        return self.status in (
            ClaimStatus.PROPOSED,
            ClaimStatus.FORMALISED,
            ClaimStatus.PARTIALLY_VERIFIED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        The :attr:`status` enum is stored as its string value.  Used by the
        copilot claim-tracking dashboard to display current verification
        progress across all Ch.19 theory claims.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of this :class:`ClaimSummary`.
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


# ---
# Chapter coverage table — §1901 through §1910
# ---


CHAPTER_COVERAGE: tuple[ManifestRecord, ...] = (
    ManifestRecord(
        section_id="§1901",
        section_title="Import Graph Site",
        module_path="jugeo.python_runtime.import_graph.import_graph",
        status=CoverageStatus.PARTIAL,
        confidence=0.60,
        open_todos=(
            "Implement full Grothendieck topology construction from package boundaries",
            "Add descent condition verification for namespace packages",
            "Wire CoordinateIndex to the site's module registry",
        ),
        summary=(
            "Section §1901 establishes that the collection of Python modules "
            "with import morphisms constitutes a valid site in the sense of "
            "Grothendieck (theory2.tex §19.1).  The module import_graph "
            "provides the site construction, coordinate assignment for each "
            "module, and the basic morphism factory for import edges.  Coverage "
            "is partial because the Grothendieck topology axioms (base change "
            "and local character) have not yet been fully verified by the solver."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1902",
        section_title="Package Fixed Points",
        module_path="jugeo.python_runtime.import_graph.package_fixpoints",
        status=CoverageStatus.PARTIAL,
        confidence=0.65,
        open_todos=(
            "Implement Algorithm 1: iterative fixed-point expansion",
            "Add convergence proof check via solver",
            "Handle circular imports in the fixed-point iteration",
        ),
        summary=(
            "Section §1902 proves that the iterative process of expanding a "
            "package's member set until it is closed under internal imports "
            "converges to a unique fixed point (theory2.tex §19.2, Theorem "
            "19.2.1).  The module package_fixpoints provides the "
            "PackageFixedPoint dataclass and the seed construction.  The "
            "iterative algorithm and convergence certificate are partially "
            "implemented; the copilot scaffolding will be promoted once the "
            "solver discharges the convergence condition."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1903",
        section_title="Re-exports and Name Transport",
        module_path="jugeo.python_runtime.import_graph.reexports",
        status=CoverageStatus.PARTIAL,
        confidence=0.62,
        open_todos=(
            "Implement compose_chain for multi-hop re-export chains",
            "Add star-export resolution against __all__ declarations",
            "Verify trust-monotonicity under composition by solver",
        ),
        summary=(
            "Section §1903 models re-export maps as transport morphisms in "
            "the site category and proves that composition of re-export chains "
            "is associative and trust-monotone (theory2.tex §19.3, Lemmas "
            "19.3.1–19.3.2).  The module reexports wraps the ReExportMap "
            "dataclass with chain-composition utilities.  Copilot scaffolded "
            "the initial compose_with method; the associativity proof is "
            "partially verified."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1904",
        section_title="Dynamic Loading Morphisms",
        module_path="jugeo.python_runtime.import_graph.dynamic_loading",
        status=CoverageStatus.STUB,
        confidence=0.40,
        open_todos=(
            "Implement runtime load hook integration via sys.meta_path",
            "Add DynamicLoadRecord factory from importlib events",
            "Prove trust ceiling theorem (C19_4) via solver",
            "Integrate load records into PackageFixedPoint expansion",
        ),
        summary=(
            "Section §1904 treats dynamic loads as non-static morphisms that "
            "are witnessed only at runtime.  The module dynamic_loading "
            "is currently a stub providing only the DynamicLoadRecord dataclass "
            "and its as_obstruction method.  The copilot CI gate marks this "
            "section as requiring substantial work before the trust ceiling "
            "theorem (C19_4) can be verified."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1905",
        section_title="Import Graph Data Models",
        module_path="jugeo.python_runtime.import_graph.models",
        status=CoverageStatus.SUBSTANTIAL,
        confidence=0.82,
        open_todos=(
            "Add round-trip deserialisation from_dict for all dataclasses",
            "Integrate node identity hash into PackageFixedPoint certificate",
        ),
        summary=(
            "Section §1905 specifies the five canonical data models used "
            "throughout Ch.19: ImportNode, ImportEdge, PackageFixedPoint, "
            "DynamicLoadRecord, and ReExportMap.  The models.py module "
            "provides full frozen dataclass implementations with complete "
            "docstrings, real method logic, trust-algebra integration, and "
            "jugeo geometry coordinates.  Copilot scaffolding was used for "
            "initial method stubs and has been reviewed and promoted."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1906",
        section_title="Algorithms",
        module_path="jugeo.python_runtime.import_graph.algorithms",
        status=CoverageStatus.STUB,
        confidence=0.35,
        open_todos=(
            "Implement topological sort respecting trust levels",
            "Add cycle detection algorithm with obstruction recording",
            "Implement reachability analysis for package closure",
            "Add trust propagation algorithm (theory2.tex §1910)",
            "Implement strongly connected component analysis",
        ),
        summary=(
            "Section §1906 specifies the graph algorithms operating on the "
            "import site: topological sort, cycle detection, reachability, "
            "and trust propagation.  The algorithms.py module is currently a "
            "stub.  Copilot has proposed algorithm skeletons but they require "
            "solver discharge before promotion.  This section is the critical "
            "path for the CI gate release."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1907",
        section_title="Integration",
        module_path="jugeo.python_runtime.import_graph.integration",
        status=CoverageStatus.STUB,
        confidence=0.30,
        open_todos=(
            "Implement sys.modules walker to build ImportNode set",
            "Add importlib.metadata integration for trust assignment",
            "Integrate with jugeo.geometry.site SiteBuilder",
            "Add snapshot serialisation / deserialisation",
            "Wire dynamic load hooks to DynamicLoadRecord factory",
        ),
        summary=(
            "Section §1907 covers the integration layer that bridges the live "
            "Python runtime (sys.modules, importlib.metadata) to the jugeo "
            "import-graph site.  The integration.py module is currently a stub "
            "with placeholder class definitions.  Copilot has proposed the "
            "sys.modules walker but it has not yet been reviewed or tested."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1908",
        section_title="Theorems",
        module_path="jugeo.python_runtime.import_graph.theorems",
        status=CoverageStatus.STUB,
        confidence=0.25,
        open_todos=(
            "Formalise Theorem 19.2.1 (fixed-point convergence) in Python",
            "Formalise Lemma 19.3.1 (trust monotonicity of re-export composition)",
            "Formalise Lemma 19.3.2 (associativity of re-export composition)",
            "Formalise Theorem 19.4.1 (trust ceiling for dynamic loads)",
            "Add solver-dischargeable propositions for each theorem",
        ),
        summary=(
            "Section §1908 contains the formal theorem statements for Ch.19 "
            "as Python dataclass propositions in the jugeo judgment algebra.  "
            "The theorems.py module is currently a stub.  Copilot has proposed "
            "proof sketches for all four theorems but none have been discharged "
            "by the solver.  All claims are in ``PROPOSED`` status."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1909",
        section_title="Circular Import Obstruction",
        module_path="jugeo.python_runtime.import_graph.import_graph",
        status=CoverageStatus.STUB,
        confidence=0.38,
        open_todos=(
            "Implement cycle detector that records Obstruction objects",
            "Assign cohomology class H^1_circ to circular import obstructions",
            "Add repair hint generator for circular import resolution",
            "Test with real-world circular import examples",
        ),
        summary=(
            "Section §1909 models circular imports as first-class obstructions "
            "in the cohomology of the import-graph site (theory2.tex §19.9, "
            "H^1_circ class).  These obstructions are never silently discarded; "
            "they accumulate until an explicit architectural refactoring provides "
            "repair evidence.  Copilot has proposed the obstruction recording "
            "logic but the cycle detector is not yet implemented."
        ),
        copilot_assisted=True,
    ),
    ManifestRecord(
        section_id="§1910",
        section_title="Trust Propagation",
        module_path="jugeo.python_runtime.import_graph.algorithms",
        status=CoverageStatus.STUB,
        confidence=0.32,
        open_todos=(
            "Implement trust propagation along import edges",
            "Enforce trust ceiling: dynamic edges cannot exceed ORACLE_PROPOSED",
            "Add trust gradient visualisation for import graph",
            "Wire trust propagation to judgment algebra discharge step",
        ),
        summary=(
            "Section §1910 specifies the trust propagation rules for the import "
            "graph: trust flows along import morphisms subject to monotonicity "
            "constraints.  Dynamic edges impose a hard ceiling at "
            "``ORACLE_PROPOSED``.  The algorithms.py module will host the "
            "propagation algorithm; currently only stub functions exist.  "
            "Copilot has proposed the propagation loop but it requires solver "
            "discharge before the trust ceiling claim (C19_4) can be settled."
        ),
        copilot_assisted=True,
    ),
)


# ---
# Exported symbols table
# ---


EXPORTED_SYMBOLS: tuple[SymbolGroup, ...] = (
    SymbolGroup(
        name="import_graph_models",
        role=SymbolRole.DATA_MODEL,
        symbols=(
            "jugeo.python_runtime.import_graph.models.ImportNode",
            "jugeo.python_runtime.import_graph.models.ImportEdge",
            "jugeo.python_runtime.import_graph.models.PackageFixedPoint",
            "jugeo.python_runtime.import_graph.models.DynamicLoadRecord",
            "jugeo.python_runtime.import_graph.models.ReExportMap",
        ),
        description=(
            "Frozen dataclass records for the five canonical objects of the "
            "import-graph theory (theory2.tex §19.1–§19.4).  All records are "
            "immutable and carry explicit TrustLevel fields.  Copilot-proposed "
            "instances enter at ORACLE_PROPOSED trust."
        ),
        source_module="jugeo.python_runtime.import_graph.models",
    ),
    SymbolGroup(
        name="import_graph_model_helpers",
        role=SymbolRole.UTILITY,
        symbols=(
            "jugeo.python_runtime.import_graph.models._make_stub_coordinate",
            "jugeo.python_runtime.import_graph.models._trust_value",
            "jugeo.python_runtime.import_graph.models._node_identity_hash",
            "jugeo.python_runtime.import_graph.models._edge_identity_hash",
            "jugeo.python_runtime.import_graph.models._now_iso_local",
            "jugeo.python_runtime.import_graph.models._make_empty_fixed_point",
            "jugeo.python_runtime.import_graph.models._certify_fixed_point",
        ),
        description=(
            "Private helper functions in the models module used for coordinate "
            "synthesis, trust coercion, content-addressed hashing, and fixed-point "
            "lifecycle management.  Prefixed with ``_`` per jugeo style conventions.  "
            "Copilot documentation tooling includes these in internal API docs."
        ),
        source_module="jugeo.python_runtime.import_graph.models",
    ),
    SymbolGroup(
        name="manifest_types",
        role=SymbolRole.INTEGRATION,
        symbols=(
            "jugeo.python_runtime.import_graph.manifest.CoverageStatus",
            "jugeo.python_runtime.import_graph.manifest.SymbolRole",
            "jugeo.python_runtime.import_graph.manifest.ClaimStatus",
            "jugeo.python_runtime.import_graph.manifest.ManifestRecord",
            "jugeo.python_runtime.import_graph.manifest.SymbolGroup",
            "jugeo.python_runtime.import_graph.manifest.ClaimSummary",
            "jugeo.python_runtime.import_graph.manifest.PackageManifest",
        ),
        description=(
            "Enums and dataclasses that together constitute the package manifest "
            "system for Ch.19.  The PackageManifest class is the root accessor "
            "object used by the copilot CI pipeline to validate coverage and "
            "generate gate reports before tagging releases."
        ),
        source_module="jugeo.python_runtime.import_graph.manifest",
    ),
    SymbolGroup(
        name="manifest_data",
        role=SymbolRole.INTEGRATION,
        symbols=(
            "jugeo.python_runtime.import_graph.manifest.CHAPTER_COVERAGE",
            "jugeo.python_runtime.import_graph.manifest.EXPORTED_SYMBOLS",
            "jugeo.python_runtime.import_graph.manifest.THEORY_CLAIMS",
            "jugeo.python_runtime.import_graph.manifest.MANIFEST",
            "jugeo.python_runtime.import_graph.manifest.get_manifest",
        ),
        description=(
            "Module-level data constants and the singleton manifest instance.  "
            "``CHAPTER_COVERAGE`` and ``THEORY_CLAIMS`` are the primary "
            "machine-readable tables consumed by the copilot CI adapter.  "
            "``get_manifest()`` provides lazy singleton access."
        ),
        source_module="jugeo.python_runtime.import_graph.manifest",
    ),
    SymbolGroup(
        name="import_graph_site_symbols",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.import_graph.import_graph.build_import_site",
            "jugeo.python_runtime.import_graph.import_graph.register_module",
            "jugeo.python_runtime.import_graph.import_graph.record_import_edge",
            "jugeo.python_runtime.import_graph.import_graph.detect_circular_imports",
        ),
        description=(
            "Functions from the import graph site module that construct the "
            "Grothendieck site from Python module metadata and record import "
            "edges as morphisms.  Copilot-proposed signatures; implementations "
            "are stubs pending solver discharge."
        ),
        source_module="jugeo.python_runtime.import_graph.import_graph",
    ),
    SymbolGroup(
        name="fixed_point_symbols",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.import_graph.package_fixpoints.compute_fixed_point",
            "jugeo.python_runtime.import_graph.package_fixpoints.expand_member_set",
            "jugeo.python_runtime.import_graph.package_fixpoints.check_closure",
        ),
        description=(
            "Functions from the package fixed-point module implementing "
            "Algorithm 1 of theory2.tex §19.2.  ``compute_fixed_point`` is "
            "the top-level entry point; ``expand_member_set`` handles a single "
            "iteration step; ``check_closure`` verifies the invariant.  All are "
            "copilot-proposed stubs awaiting implementation and solver discharge."
        ),
        source_module="jugeo.python_runtime.import_graph.package_fixpoints",
    ),
    SymbolGroup(
        name="reexport_symbols",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.import_graph.reexports.build_reexport_map",
            "jugeo.python_runtime.import_graph.reexports.compose_reexport_chain",
            "jugeo.python_runtime.import_graph.reexports.resolve_star_export",
        ),
        description=(
            "Functions from the re-export module providing map construction, "
            "chain composition, and star-export resolution.  Chain composition "
            "implements the associativity lemma from theory2.tex §19.3.  "
            "Copilot-proposed; implementation pending."
        ),
        source_module="jugeo.python_runtime.import_graph.reexports",
    ),
    SymbolGroup(
        name="algorithm_symbols",
        role=SymbolRole.ALGORITHM,
        symbols=(
            "jugeo.python_runtime.import_graph.algorithms.topological_sort",
            "jugeo.python_runtime.import_graph.algorithms.detect_cycles",
            "jugeo.python_runtime.import_graph.algorithms.reachable_from",
            "jugeo.python_runtime.import_graph.algorithms.propagate_trust",
            "jugeo.python_runtime.import_graph.algorithms.strongly_connected_components",
        ),
        description=(
            "Graph algorithms operating on the import site.  "
            "``propagate_trust`` implements the trust flow rules of theory2.tex "
            "§1910 subject to the dynamic-load ceiling constraint.  "
            "All are copilot-proposed stubs; ``algorithms.py`` is the critical "
            "path for the §1906 and §1910 CI gates."
        ),
        source_module="jugeo.python_runtime.import_graph.algorithms",
    ),
    SymbolGroup(
        name="theorem_symbols",
        role=SymbolRole.THEOREM,
        symbols=(
            "jugeo.python_runtime.import_graph.theorems.FIXED_POINT_CONVERGENCE",
            "jugeo.python_runtime.import_graph.theorems.TRUST_MONOTONICITY",
            "jugeo.python_runtime.import_graph.theorems.REEXPORT_ASSOCIATIVITY",
            "jugeo.python_runtime.import_graph.theorems.DYNAMIC_TRUST_CEILING",
        ),
        description=(
            "Formal theorem objects corresponding to the four major claims "
            "of Ch.19.  Each theorem is a Proposition instance in the jugeo "
            "judgment algebra with a proof sketch and evidence plan.  "
            "Copilot proposed the initial proof sketches; none are yet "
            "discharged by the solver."
        ),
        source_module="jugeo.python_runtime.import_graph.theorems",
    ),
)


# ---
# Theory claims table
# ---


THEORY_CLAIMS: tuple[ClaimSummary, ...] = (
    ClaimSummary(
        claim_id="C19_1",
        title="Import graph is a valid Grothendieck site",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§1901",
        implementing_module="jugeo.python_runtime.import_graph.import_graph",
        falsification_module="jugeo.python_runtime.import_graph.theorems",
        evidence_required=(
            "runtime_import_trace",
            "site_axiom_check",
            "covering_family_verification",
            "base_change_solver_proof",
        ),
    ),
    ClaimSummary(
        claim_id="C19_2",
        title="Package fixed points are closed under internal imports",
        status=ClaimStatus.PARTIALLY_VERIFIED,
        theory_section="§1902",
        implementing_module="jugeo.python_runtime.import_graph.package_fixpoints",
        falsification_module="jugeo.python_runtime.import_graph.theorems",
        evidence_required=(
            "fixed_point_convergence_proof",
            "closure_invariant_check",
            "circular_import_handling_evidence",
        ),
    ),
    ClaimSummary(
        claim_id="C19_3",
        title="Re-export composition is associative and trust-monotone",
        status=ClaimStatus.FORMALISED,
        theory_section="§1903",
        implementing_module="jugeo.python_runtime.import_graph.reexports",
        falsification_module="jugeo.python_runtime.import_graph.theorems",
        evidence_required=(
            "associativity_solver_proof",
            "trust_monotonicity_solver_proof",
            "compose_with_unit_test_suite",
        ),
    ),
    ClaimSummary(
        claim_id="C19_4",
        title="Dynamic loads cannot produce trust above ORACLE_PROPOSED",
        status=ClaimStatus.PROPOSED,
        theory_section="§1904",
        implementing_module="jugeo.python_runtime.import_graph.dynamic_loading",
        falsification_module="jugeo.python_runtime.import_graph.theorems",
        evidence_required=(
            "trust_ceiling_solver_proof",
            "dynamic_load_runtime_witness",
            "trust_promotion_policy_review",
        ),
    ),
)


# ---
# PackageManifest root object
# ---


@dataclass
class PackageManifest:
    """Root manifest object for ``jugeo.python_runtime.import_graph``.

    Aggregates coverage records, exported symbol groups, and theory claims
    for Ch.19 of Theory2.tex.  Provides validation, query, and CI-gate
    reporting methods used by the copilot CI pipeline.

    This class is **not** frozen because it may accumulate runtime state
    (e.g. computed confidence averages or CI gate results) after construction.
    The underlying data tuples are immutable.

    Parameters
    ----------
    chapter_coverage:
        Tuple of :class:`ManifestRecord` objects, one per §190x section.
    exported_symbols:
        Tuple of :class:`SymbolGroup` objects describing the public API.
    theory_claims:
        Tuple of :class:`ClaimSummary` objects for the four Ch.19 claims.
    created_at:
        ISO-8601 timestamp of manifest construction.
    """

    chapter_coverage: tuple[ManifestRecord, ...]
    exported_symbols: tuple[SymbolGroup, ...]
    theory_claims: tuple[ClaimSummary, ...]
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    # --- coverage methods ---

    def coverage_for_section(self, section_id: str) -> ManifestRecord | None:
        """Return the :class:`ManifestRecord` for a given section ID.

        Performs a linear scan over :attr:`chapter_coverage`.  Copilot uses
        this method when generating per-section CI badge links; it returns
        ``None`` rather than raising to allow graceful handling of unknown IDs.

        Parameters
        ----------
        section_id:
            The Theory2.tex section identifier, e.g. ``"§1901"``.

        Returns
        -------
        ManifestRecord | None
            The matching record, or ``None`` if *section_id* is not found.
        """
        for record in self.chapter_coverage:
            if record.section_id == section_id:
                return record
        return None

    def sections_below_threshold(
        self, threshold: CoverageStatus = CoverageStatus.SUBSTANTIAL
    ) -> tuple[ManifestRecord, ...]:
        """Return all sections whose coverage status is below *threshold*.

        Used by the copilot CI gate to identify sections that must be
        addressed before a release can be tagged.  A default threshold of
        ``SUBSTANTIAL`` means only ``MISSING``, ``STUB``, and ``PARTIAL``
        sections are flagged.

        Parameters
        ----------
        threshold:
            Minimum acceptable coverage status.

        Returns
        -------
        tuple[ManifestRecord, ...]
            Records whose status is strictly below *threshold*.
        """
        return tuple(r for r in self.chapter_coverage if r.status < threshold)

    def total_open_todos(self) -> int:
        """Return the total number of open TODO items across all sections.

        Sums the lengths of all :attr:`ManifestRecord.open_todos` tuples.
        Copilot sprint planning uses this count as a rough measure of
        implementation debt remaining for Ch.19.

        Returns
        -------
        int
            Total count of unresolved TODO items.
        """
        return sum(len(r.open_todos) for r in self.chapter_coverage)

    def mean_confidence(self) -> float:
        """Return the mean confidence score across all coverage records.

        An arithmetic mean of all :attr:`ManifestRecord.confidence` values.
        Returns ``0.0`` when :attr:`chapter_coverage` is empty.  Copilot
        uses this value as the chapter-level confidence metric in the
        research program dashboard.

        Returns
        -------
        float
            Mean confidence in ``[0.0, 1.0]``, or ``0.0`` if no records.
        """
        if not self.chapter_coverage:
            return 0.0
        return sum(r.confidence for r in self.chapter_coverage) / len(
            self.chapter_coverage
        )

    # --- symbol methods ---

    def find_symbol(self, symbol: str) -> SymbolGroup | None:
        """Find the :class:`SymbolGroup` that contains *symbol*.

        Searches all groups using :meth:`SymbolGroup.contains`.  Returns the
        first matching group, or ``None`` if the symbol is not exported.
        Copilot auto-import uses this to find the correct group and source
        module for a requested symbol.

        Parameters
        ----------
        symbol:
            The symbol to look up (short name or fully qualified).

        Returns
        -------
        SymbolGroup | None
            The first group containing *symbol*, or ``None``.
        """
        for group in self.exported_symbols:
            if group.contains(symbol):
                return group
        return None

    def symbols_by_role(self, role: SymbolRole) -> tuple[SymbolGroup, ...]:
        """Return all symbol groups whose role matches *role*.

        Copilot documentation generators use this to produce role-specific
        API sections (e.g. all ``DATA_MODEL`` symbols in one table, all
        ``ALGORITHM`` symbols in another).

        Parameters
        ----------
        role:
            The conceptual role to filter by.

        Returns
        -------
        tuple[SymbolGroup, ...]
            All groups with the given role.
        """
        return tuple(g for g in self.exported_symbols if g.role == role)

    def all_symbol_names(self) -> tuple[str, ...]:
        """Return a flat tuple of all exported symbol names.

        Concatenates the :attr:`SymbolGroup.symbols` tuples from all groups.
        Copilot completion engines use this to populate the auto-complete
        candidate list for imports from this package.

        Returns
        -------
        tuple[str, ...]
            All fully-qualified symbol names exported by this package.
        """
        names: list[str] = []
        for group in self.exported_symbols:
            names.extend(group.symbols)
        return tuple(names)

    # --- claim methods ---

    def claim(self, claim_id: str) -> ClaimSummary | None:
        """Return the :class:`ClaimSummary` with the given *claim_id*.

        Linear scan over :attr:`theory_claims`.  Returns ``None`` if not
        found.  Copilot's claim-tracking UI uses this to look up individual
        claims by their short IDs (e.g. ``"C19_1"``).

        Parameters
        ----------
        claim_id:
            The claim identifier, e.g. ``"C19_1"``.

        Returns
        -------
        ClaimSummary | None
            The matching summary, or ``None``.
        """
        for c in self.theory_claims:
            if c.claim_id == claim_id:
                return c
        return None

    def open_claims(self) -> tuple[ClaimSummary, ...]:
        """Return all claims that have not yet reached a terminal status.

        Filters :attr:`theory_claims` using :meth:`ClaimSummary.is_open`.
        Copilot's sprint board surfaces open claims as items requiring
        active implementation work.

        Returns
        -------
        tuple[ClaimSummary, ...]
            All open (non-terminal) claims.
        """
        return tuple(c for c in self.theory_claims if c.is_open())

    def claims_by_status(self, status: ClaimStatus) -> tuple[ClaimSummary, ...]:
        """Return all claims with the given *status*.

        Used by the copilot research dashboard to group claims into lifecycle
        columns (proposed, formalised, partially verified, verified, etc.).

        Parameters
        ----------
        status:
            The claim lifecycle status to filter by.

        Returns
        -------
        tuple[ClaimSummary, ...]
            All claims matching *status*.
        """
        return tuple(c for c in self.theory_claims if c.status == status)

    # --- serialisation ---

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full manifest to a JSON-safe nested dictionary.

        All nested objects are recursively serialised via their own
        ``to_dict`` methods.  The resulting dict is suitable for writing to
        ``.jugeo/import_graph_manifest.json``.  Copilot CI reads this file
        to perform manifest diffing across commits.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation of the full manifest.
        """
        return {
            "created_at": self.created_at,
            "chapter_coverage": [r.to_dict() for r in self.chapter_coverage],
            "exported_symbols": [g.to_dict() for g in self.exported_symbols],
            "theory_claims": [c.to_dict() for c in self.theory_claims],
            "summary": {
                "total_sections": len(self.chapter_coverage),
                "mean_confidence": self.mean_confidence(),
                "total_open_todos": self.total_open_todos(),
                "open_claims": len(self.open_claims()),
            },
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise the manifest to a formatted JSON string.

        Convenience wrapper around :meth:`to_dict` and :func:`json.dumps`.
        Copilot CI uses this to write the manifest snapshot file at the end
        of each analysis run.

        Parameters
        ----------
        indent:
            JSON indentation level (default 2).

        Returns
        -------
        str
            Formatted JSON string representing the full manifest.
        """
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def content_hash(self) -> str:
        """Compute a deterministic SHA-256 hash of the manifest content.

        Hashes the JSON representation with sorted keys so that the hash is
        stable across Python versions and dict orderings.  Copilot stores
        this hash in the CI artefact to detect unexpected manifest mutations
        between build steps.

        Returns
        -------
        str
            Hex SHA-256 digest of the manifest content.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def ci_gate_report(
        self, threshold: CoverageStatus = CoverageStatus.SUBSTANTIAL
    ) -> dict[str, Any]:
        """Generate a CI gate report for this manifest.

        Identifies failing sections (below *threshold*), open claims, and
        unresolved TODOs.  Returns a structured dict with a ``passed`` boolean
        that the copilot CI adapter uses to set the workflow exit code.

        Parameters
        ----------
        threshold:
            Minimum coverage status required for all sections.

        Returns
        -------
        dict[str, Any]
            Structured CI gate report with ``passed``, ``failing_sections``,
            ``open_claims``, ``total_open_todos``, ``mean_confidence``, and
            ``content_hash`` fields.
        """
        failing = self.sections_below_threshold(threshold)
        open_cls = self.open_claims()
        passed = len(failing) == 0 and len(open_cls) == 0
        return {
            "passed": passed,
            "threshold": threshold.value,
            "failing_sections": [r.section_id for r in failing],
            "failing_section_details": [r.to_dict() for r in failing],
            "open_claims": [c.claim_id for c in open_cls],
            "total_open_todos": self.total_open_todos(),
            "mean_confidence": round(self.mean_confidence(), 4),
            "content_hash": self.content_hash(),
            "created_at": self.created_at,
        }

    def iter_copilot_assisted_sections(self) -> Iterator[ManifestRecord]:
        """Yield all coverage records that were copilot-assisted.

        These sections carry ``ORACLE_PROPOSED`` trust and require review
        before they can be promoted.  Copilot uses this iterator when
        generating the trust-promotion checklist for a release review.

        Yields
        ------
        ManifestRecord
            Each record whose :attr:`copilot_assisted` flag is ``True``.
        """
        for record in self.chapter_coverage:
            if record.copilot_assisted:
                yield record


# ---
# Singleton manifest instance
# ---


MANIFEST: PackageManifest = PackageManifest(
    chapter_coverage=CHAPTER_COVERAGE,
    exported_symbols=EXPORTED_SYMBOLS,
    theory_claims=THEORY_CLAIMS,
)


def get_manifest() -> PackageManifest:
    """Return the singleton :class:`PackageManifest` for this sub-package.

    Provides lazy singleton access for callers who prefer a function
    interface.  The singleton is constructed at module import time and is
    shared across all callers in the same process.  Copilot CI tooling
    calls this function at the start of each analysis run to obtain the
    current manifest state.

    Returns
    -------
    PackageManifest
        The module-level :data:`MANIFEST` singleton.
    """
    return MANIFEST
