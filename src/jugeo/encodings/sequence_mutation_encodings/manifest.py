"""manifest.py — Package manifest for jugeo.encodings.sequence_mutation_encodings.

Theory2.tex Chapter 29: "Exact Z3 encodings IV: sequences, finite maps,
heap slices, support-aware mutation".

This module is the authoritative self-description of the
``sequence_mutation_encodings`` package.  It records:

*   Which chapter and sections of theory2.tex this package implements.
*   The full capability surface claimed by the package.
*   The explicit dependency list on adjacent jugeo sub-systems.
*   The stable export set guaranteed not to be removed without a major version bump.
*   Theory provenance: which formal claim in theory2.tex each capability traces to.
*   Authority boundaries: what this package may assert vs. what requires external proof.

Chapter 29 overview
-------------------
Chapter 29 develops the four-layer encoding pipeline for mutating data structures:

§29.1  **Structured-data encoder** — every Python sequence (list, tuple, ordered
       dict) is embedded in a Z3 array ``Array(IntSort, ElemSort)`` with an
       explicit length variable.  Index-bound invariants prevent out-of-range
       accesses from polluting the satisfiability query.

§29.2  **Sequence-window encoder** — "window" predicates of the form
       ``∀ i ∈ [lo, hi): P(arr[i])`` are encoded as bounded quantifier blocks.
       Splitting, shifting, and conjunction of windows are first-class operations.

§29.3  **Finite-map encoder** — Python ``dict`` objects are represented as
       Z3 uninterpreted functions ``f: KeySort → ValSort`` together with an
       explicit domain predicate ``dom(x) ≡ x = k₀ ∨ … ∨ x = kₙ``.  The
       encoder maintains the invariant that ``¬dom(x) → f(x) = default``.

§29.4  **Heap-slice encoder** — a heap is modelled as ``Array(Addr, Cell)``.
       A *heap slice* is a summary restricted to a finite support set ``S``;
       the *frame axiom* ``∀ addr ∉ S: post[addr] = pre[addr]`` is generated
       automatically and discharged by the solver.

§29.5  **Mutation countermodel encoder** — when a mutation violates an
       invariant the solver returns UNSAT; this encoder extracts the UNSAT core,
       localises the violation to a minimal ``MutationSlice``, and produces a
       structured ``RepairSuggestion``.

Design principles
-----------------
*  *No silent promotion* — a copilot proposal never escalates beyond
   ``ORACLE_PROPOSED`` trust without an explicit solver discharge.
*  *Support honesty* — every mutation declares its support set up-front;
   the encoder refuses to generate frame axioms for undeclared cells.
*  *Fragment discipline* — the output of every encoder call is annotated with
   the Z3 fragment it belongs to (typically ``QF_AUFLIA`` or ``SEQUENCES``).

# copilot: manifest for sequence_mutation_encodings — Theory2.tex Ch29.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version and identity constants
# ---------------------------------------------------------------------------

PACKAGE_NAME: Final[str] = "jugeo.encodings.sequence_mutation_encodings"
PACKAGE_VERSION: Final[str] = "0.29.0"
THEORY_CHAPTER: Final[int] = 29
THEORY_FILE: Final[str] = "preliminaries/theory2.tex"
THEORY_CHAPTER_TITLE: Final[str] = (
    "Exact Z3 encodings IV: sequences, finite maps, heap slices, "
    "support-aware mutation"
)

# ---------------------------------------------------------------------------
# Capability declarations
# ---------------------------------------------------------------------------

CAPABILITIES: Final[Tuple[str, ...]] = (
    "sequence-encoding",           # §29.1 — list/tuple → Z3 Array
    "window-predicate-encoding",   # §29.2 — ∀ i∈[lo,hi): P(arr[i])
    "finite-map-encoding",         # §29.3 — dict → Z3 partial function
    "heap-slice-encoding",         # §29.4 — heap summary with frame axiom
    "mutation-countermodel",       # §29.5 — UNSAT core → repair suggestion
    "support-aware-mutation",      # support-bounded mutation correctness
    "frame-axiom-generation",      # automatic frame axiom synthesis
    "mutation-composition",        # compose support-bounded mutations
    "invariant-repair-search",     # minimal repair from violation
    "copilot-encoding-assist",     # copilot-guided encoding suggestions
)

# ---------------------------------------------------------------------------
# Dependency list
# ---------------------------------------------------------------------------

DEPENDENCIES: Final[Tuple[str, ...]] = (
    "jugeo.solver.z3_session",
    "jugeo.solver.fragments",
    "jugeo.solver.reconstruction",
)

# ---------------------------------------------------------------------------
# Stable export list
# ---------------------------------------------------------------------------

STABLE_EXPORTS: Final[Tuple[str, ...]] = (
    # models
    "SequenceEncoding",
    "MutationSlice",
    "HeapSlice",
    "SupportAwareMutation",
    "SequenceInvariant",
    "MutationKind",
    "SequenceInvariantKind",
    # encoders
    "StructuredDataEncoder",
    "SequenceWindowEncoder",
    "FiniteMapEncoder",
    "HeapSliceEncoder",
    "MutationCountermodelEncoder",
    # algorithms
    "sequence_induction_schema",
    "build_support_closure",
    "decompose_mutation_by_support",
    "unify_heap_slices",
    "check_frame_preservation",
    "compute_mutation_footprint",
    "repair_invariant_violation",
    # integration
    "SequenceMutationSolverIntegration",
    # theorems
    "FramePreservationTheorem",
    "SupportClosureTheorem",
    "MutationCompositionTheorem",
    "HeapSliceConsistencyTheorem",
    "InvariantRepairTheorem",
)

# ---------------------------------------------------------------------------
# Theory provenance mapping
# ---------------------------------------------------------------------------

THEORY_PROVENANCE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "sequence-encoding": "Theory2.tex §29.1 — Structured-data encoder",
        "window-predicate-encoding": "Theory2.tex §29.2 — Sequence-window encoder",
        "finite-map-encoding": "Theory2.tex §29.3 — Finite-map encoder",
        "heap-slice-encoding": "Theory2.tex §29.4 — Heap-slice encoder",
        "mutation-countermodel": "Theory2.tex §29.5 — Mutation countermodel encoder",
        "support-aware-mutation": "Theory2.tex §29.4 — support-bounded mutation",
        "frame-axiom-generation": "Theory2.tex §29.4 — frame axiom ∀addr∉S: post[addr]=pre[addr]",
        "mutation-composition": "Theory2.tex §29.4 Prop 29.3 — support union under composition",
        "invariant-repair-search": "Theory2.tex §29.5 Thm 29.1 — minimal repair existence",
        "copilot-encoding-assist": "Theory2.tex §29.1 Remark 29.1 — copilot suggestion interface",
    }
)

# ---------------------------------------------------------------------------
# Chapter coverage
# ---------------------------------------------------------------------------

CHAPTER_COVERAGE: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "chapter": THEORY_CHAPTER,
        "title": THEORY_CHAPTER_TITLE,
        "theory_file": THEORY_FILE,
        "sections": {
            "29.1": {
                "title": "Structured-data encoder",
                "implemented_by": "structured_data_encoder.py",
                "status": "complete",
            },
            "29.2": {
                "title": "Sequence-window encoder",
                "implemented_by": "sequence_window_encoder.py",
                "status": "complete",
            },
            "29.3": {
                "title": "Finite-map encoder",
                "implemented_by": "finite_map_encoder.py",
                "status": "complete",
            },
            "29.4": {
                "title": "Heap-slice encoder",
                "implemented_by": "heap_slice_encoder.py",
                "status": "complete",
            },
            "29.5": {
                "title": "Mutation countermodel encoder",
                "implemented_by": "mutation_countermodel_encoder.py",
                "status": "complete",
            },
        },
    }
)

# ---------------------------------------------------------------------------
# SubsystemManifest dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubsystemManifest:
    """Compact, frozen declaration of a JuGeo sub-package as a semantic unit.

    Fields
    ------
    name : str
        Canonical dotted Python package name.
    package : str
        Filesystem-relative package path (dot-separated).
    capabilities : tuple[str, ...]
        Short capability identifiers claimed by the sub-package.
    dependencies : tuple[str, ...]
        Other JuGeo packages this sub-package directly depends on.
    stage : str
        Build/generation stage ('root-foundation', 'encoding', etc.).
    authority_boundary : str
        Human-readable description of what assertions this package may make.
    scope_honesty : str
        Statement of what this package explicitly does *not* claim.
    stable_exports : tuple[str, ...]
        Names guaranteed stable across minor version increments.
    theory_chapter : int
        Chapter of theory2.tex this manifest corresponds to.
    theory_provenance : Mapping[str, str]
        Per-capability mapping to formal theory claims.

    # copilot: SubsystemManifest dataclass for sequence_mutation_encodings.
    """

    name: str
    package: str
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    stage: str = "encoding"
    authority_boundary: str = ""
    scope_honesty: str = ""
    stable_exports: tuple[str, ...] = ()
    theory_chapter: int = 0
    theory_provenance: Mapping[str, str] = field(default_factory=dict)

    def has_capability(self, cap: str) -> bool:
        """Return True if *cap* is in this manifest's capability set.

        Parameters
        ----------
        cap:
            A short capability identifier string.

        Returns
        -------
        bool
        """
        return cap in self.capabilities

    def provenance_for(self, cap: str) -> str | None:
        """Return the theory provenance string for a capability, or ``None``.

        Parameters
        ----------
        cap:
            A short capability identifier string.

        Returns
        -------
        str or None
            The formal theory reference, or None if not found.
        """
        return self.theory_provenance.get(cap)

    def dependency_check(self, available: frozenset[str]) -> list[str]:
        """Return list of declared dependencies that are *not* in *available*.

        Parameters
        ----------
        available:
            The set of package names available in the current environment.

        Returns
        -------
        list[str]
            Missing dependency names.  Empty list means all satisfied.
        """
        return [d for d in self.dependencies if d not in available]

    def chapter_summary(self) -> str:
        """Return a one-paragraph summary of what this manifest covers.

        Returns
        -------
        str
        """
        caps = ", ".join(self.capabilities[:4])
        return (
            f"Package '{self.name}' implements Theory2.tex Chapter "
            f"{self.theory_chapter}: '{THEORY_CHAPTER_TITLE}'. "
            f"Stage: '{self.stage}'. "
            f"Key capabilities: {caps}, and {len(self.capabilities) - 4} more. "
            f"Authority boundary: {self.authority_boundary or '(none declared)'}."
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation of this manifest.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "package": self.package,
            "capabilities": list(self.capabilities),
            "dependencies": list(self.dependencies),
            "stage": self.stage,
            "authority_boundary": self.authority_boundary,
            "scope_honesty": self.scope_honesty,
            "stable_exports": list(self.stable_exports),
            "theory_chapter": self.theory_chapter,
            "theory_chapter_title": THEORY_CHAPTER_TITLE,
            "theory_file": THEORY_FILE,
        }


# ---------------------------------------------------------------------------
# Module-level manifest instance
# ---------------------------------------------------------------------------

SEQUENCE_MUTATION_MANIFEST: Final[SubsystemManifest] = SubsystemManifest(
    name=PACKAGE_NAME,
    package="jugeo.encodings.sequence_mutation_encodings",
    capabilities=CAPABILITIES,
    dependencies=DEPENDENCIES,
    stage="encoding",
    authority_boundary=(
        "This package may assert: (a) Z3 formula correctness for the five "
        "encoding schemes listed in its capabilities, subject to the solver "
        "discharging all generated axioms; (b) that generated frame axioms are "
        "logically equivalent to their informal descriptions in theory2.tex §29.4; "
        "(c) that copilot-assisted encoding suggestions carry ORACLE_PROPOSED trust "
        "and must be independently verified."
    ),
    scope_honesty=(
        "This package does NOT assert: (a) completeness of the Z3 solver for the "
        "generated formulas (that is delegated to jugeo.solver.*); (b) soundness of "
        "the overall verification pipeline (that is the responsibility of "
        "jugeo.foundations.*); (c) that repair suggestions produced by "
        "MutationCountermodelEncoder are the unique or optimal repairs."
    ),
    stable_exports=STABLE_EXPORTS,
    theory_chapter=THEORY_CHAPTER,
    theory_provenance=THEORY_PROVENANCE,
)


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates a SubsystemManifest against a set of consistency rules.

    Checks performed
    ----------------
    1. All declared capabilities have theory provenance entries.
    2. Stable exports list is non-empty.
    3. Dependencies list references only known jugeo sub-systems.
    4. Authority boundary is non-empty.
    5. Scope honesty statement is non-empty.

    # copilot: manifest validator for sequence_mutation_encodings.
    """

    KNOWN_JUGEO_PREFIXES: Final[tuple[str, ...]] = (
        "jugeo.solver.",
        "jugeo.foundations.",
        "jugeo.encodings.",
        "jugeo.evidence.",
        "jugeo.runtime.",
    )

    def __init__(self, manifest: SubsystemManifest) -> None:
        """Initialise the validator with the manifest to check.

        Parameters
        ----------
        manifest:
            The SubsystemManifest instance to validate.
        """
        self._manifest = manifest
        self._errors: list[str] = []

    def validate(self) -> bool:
        """Run all validation rules and return True if all pass.

        Returns
        -------
        bool
            True if the manifest is consistent; False with errors logged.
        """
        self._errors.clear()
        self._check_provenance_coverage()
        self._check_stable_exports_nonempty()
        self._check_dependency_prefixes()
        self._check_authority_boundary()
        self._check_scope_honesty()
        if self._errors:
            for err in self._errors:
                logger.warning("ManifestValidator: %s", err)
            return False
        return True

    def errors(self) -> list[str]:
        """Return accumulated validation errors from the last ``validate()`` call.

        Returns
        -------
        list[str]
        """
        return list(self._errors)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_provenance_coverage(self) -> None:
        for cap in self._manifest.capabilities:
            if cap not in self._manifest.theory_provenance:
                self._errors.append(
                    f"Capability '{cap}' has no theory provenance entry."
                )

    def _check_stable_exports_nonempty(self) -> None:
        if not self._manifest.stable_exports:
            self._errors.append("stable_exports list must be non-empty.")

    def _check_dependency_prefixes(self) -> None:
        for dep in self._manifest.dependencies:
            if not any(dep.startswith(p) for p in self.KNOWN_JUGEO_PREFIXES):
                self._errors.append(
                    f"Dependency '{dep}' does not match any known jugeo prefix."
                )

    def _check_authority_boundary(self) -> None:
        if not self._manifest.authority_boundary.strip():
            self._errors.append("authority_boundary must be non-empty.")

    def _check_scope_honesty(self) -> None:
        if not self._manifest.scope_honesty.strip():
            self._errors.append("scope_honesty must be non-empty.")


# ---------------------------------------------------------------------------
# Public accessor
# ---------------------------------------------------------------------------


def get_manifest() -> SubsystemManifest:
    """Return the canonical SubsystemManifest for this package.

    This is the preferred accessor; it also runs a lightweight validation
    check and emits a warning if the manifest is inconsistent.

    Returns
    -------
    SubsystemManifest
        The module-level ``SEQUENCE_MUTATION_MANIFEST`` instance.

    # copilot: manifest accessor — returns validated manifest.
    """
    validator = ManifestValidator(SEQUENCE_MUTATION_MANIFEST)
    if not validator.validate():
        logger.warning(
            "sequence_mutation_encodings manifest has validation errors: %s",
            validator.errors(),
        )
    return SEQUENCE_MUTATION_MANIFEST


# ---------------------------------------------------------------------------
# Module-level description helper
# ---------------------------------------------------------------------------


def describe_package() -> str:
    """Return a multi-line human-readable description of this package.

    Suitable for logging, README generation, and copilot context injection.

    Returns
    -------
    str
    """
    m = SEQUENCE_MUTATION_MANIFEST
    lines = [
        f"Package : {m.name}",
        f"Version : {PACKAGE_VERSION}",
        f"Stage   : {m.stage}",
        f"Theory  : {THEORY_FILE} Chapter {THEORY_CHAPTER} — {THEORY_CHAPTER_TITLE}",
        "",
        "Capabilities:",
    ]
    for cap in m.capabilities:
        prov = m.provenance_for(cap) or "(no provenance)"
        lines.append(f"  [{cap}]  →  {prov}")
    lines += [
        "",
        "Dependencies:",
        *[f"  {d}" for d in m.dependencies],
        "",
        f"Authority boundary:\n  {m.authority_boundary}",
        "",
        f"Scope honesty:\n  {m.scope_honesty}",
    ]
    return "\n".join(lines)


__all__: list[str] = [
    "SubsystemManifest",
    "ManifestValidator",
    "SEQUENCE_MUTATION_MANIFEST",
    "CAPABILITIES",
    "DEPENDENCIES",
    "STABLE_EXPORTS",
    "THEORY_PROVENANCE",
    "CHAPTER_COVERAGE",
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "THEORY_CHAPTER",
    "get_manifest",
    "describe_package",
]
