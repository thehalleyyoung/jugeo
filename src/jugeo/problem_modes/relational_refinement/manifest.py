"""Module manifest for the relational_refinement package.

Defines the provenance metadata, capability registry, and the singleton
``RELATIONAL_REFINEMENT_MANIFEST`` that describes this stage of the JuGeo
geometric judgment algebra pipeline.

Theory context
--------------
Chapter 12 of theory2.tex ("Equivalence and Refinement") introduces the
refinement partial order on judgments together with its companion structures:
equivalence classes, refinement witnesses, and the comparison algebra.  This
manifest records every capability provided by the package and cross-references
the formal theorems that each capability must satisfy.

Design notes
------------
All public dataclasses in this module are frozen + slotted so that manifest
singletons can safely be shared across threads and processes.  Serialisation
follows the project-wide ``to_dict`` / ``from_dict`` convention.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Project-wide type aliases (mirrored from jugeo.core.types)
# ---------------------------------------------------------------------------
JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch12-relational-refinement",
    "sequence": 12,
    "theory": "theory2.tex",
    "chapter": 12,
    "title": "Equivalence and Refinement",
    "version": "1.0.0",
    "created": "2024-01-01",
    "authors": ["JuGeo core team"],
    "description": (
        "Implements the refinement partial order on judgments (J ≤ J'), "
        "equivalence classes (J ≡ J' iff J ≤ J' and J' ≤ J), refinement "
        "witnesses, and the comparison algebra (compose, invert, tensor, diagonal)."
    ),
    "dependencies": [
        "jugeo.judgments.judgment_terms",
        "jugeo.judgments.comparisons",
        "jugeo.solver.countermodels",
        "jugeo.errors",
    ],
    "exports": [
        "RefinementRelation",
        "EquivalenceClass",
        "RefinementWitness",
        "RefinementOrder",
        "RefinementChecker",
        "EquivalenceVerifier",
        "WitnessConstructor",
        "ComparisonAlgebra",
        "RelationalRefinementIntegration",
    ],
}

# ---------------------------------------------------------------------------
# Theorem targets
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[tuple[str, str, str], ...] = (
    (
        "refinement_reflexivity",
        "For all judgments J: J ≤ J holds (the identity witness is valid).",
        "Ch12.Thm1",
    ),
    (
        "refinement_antisymmetry",
        "J ≤ J' and J' ≤ J implies J ≡ J' (bidirectional refinement = equivalence).",
        "Ch12.Thm2",
    ),
    (
        "refinement_transitivity",
        "J ≤ J' and J' ≤ J'' implies J ≤ J'' (witnesses compose).",
        "Ch12.Thm3",
    ),
    (
        "equivalence_congruence",
        "The equivalence relation ≡ is a congruence on the judgment algebra.",
        "Ch12.Thm4",
    ),
    (
        "witness_compositionality",
        "The composition of valid witnesses is a valid witness.",
        "Ch12.Thm5",
    ),
    (
        "trust_monotonicity",
        "J ≤ J' implies trust(J) ≤ trust(J') in the trust lattice.",
        "Ch12.Thm6",
    ),
    (
        "evidence_embedding_soundness",
        "The evidence embedding of a refinement witness preserves semantic content.",
        "Ch12.Thm7",
    ),
    (
        "obligation_discharge_completeness",
        "Every residual obligation of J is discharged or subsumed in J'.",
        "Ch12.Thm8",
    ),
    (
        "section_refinement_preservation",
        "Refinement of sections preserves the descent conditions over any cover.",
        "Ch12.Thm9",
    ),
    (
        "lub_existence",
        "Any two comparable judgments have a least upper bound in the order.",
        "Ch12.Thm10",
    ),
    (
        "glb_existence",
        "Any two comparable judgments have a greatest lower bound in the order.",
        "Ch12.Thm11",
    ),
    (
        "regression_detection",
        "A regressive refinement is detectable from the trust-delta and obligation delta.",
        "Ch12.Thm12",
    ),
)

# ---------------------------------------------------------------------------
# Capability enumeration
# ---------------------------------------------------------------------------


class RelationalRefinementCap(str, Enum):
    """Capability flags for the relational_refinement package.

    Each member names a feature that the package exposes.  The manifest
    singleton lists which capabilities are active in the current build.
    """

    REFINEMENT_CHECK = "refinement_check"
    """Check whether J ≤ J' holds between two judgments."""

    EQUIVALENCE_VERIFY = "equivalence_verify"
    """Verify bidirectional refinement (J ≡ J')."""

    WITNESS_CONSTRUCT = "witness_construct"
    """Construct and validate refinement witnesses."""

    COMPARISON_ALGEBRA = "comparison_algebra"
    """Algebraic operations: compose, invert, tensor, diagonal."""

    BATCH_PROCESSING = "batch_processing"
    """Check refinement for a whole sequence of judgments at once."""

    SECTION_REFINEMENT = "section_refinement"
    """Refinement of sections over a cover 𝔘."""

    TRANSITIVE_CLOSURE = "transitive_closure"
    """Compute the transitive closure of a set of refinement relations."""

    ORDER_ANALYSIS = "order_analysis"
    """LUB/GLB computation, chain/antichain extraction, cycle detection."""

    REGRESSION_DETECTION = "regression_detection"
    """Flag refinements that are actually regressions (trust-δ < 0)."""

    INTEGRATION = "integration"
    """Bridge between refinement structures and the JudgmentAlgebra."""

    THEOREM_OBLIGATIONS = "theorem_obligations"
    """Generate and track Ch12 proof obligations."""


# ---------------------------------------------------------------------------
# Capability dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationalRefinementCapability:
    """A single named capability with metadata.

    Attributes
    ----------
    cap:
        The capability flag (``RelationalRefinementCap`` member).
    description:
        Human-readable description of what the capability provides.
    is_enabled:
        Whether the capability is currently active.
    version:
        Semantic version string for this capability.
    theorem_refs:
        Names of theorems that this capability must satisfy.
    stage_file:
        The module in this package that implements the capability.
    """

    cap: RelationalRefinementCap
    description: str
    is_enabled: bool = True
    version: str = "1.0.0"
    theorem_refs: tuple[str, ...] = field(default_factory=tuple)
    stage_file: str = ""

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields represented as JSON scalars / lists.
        """
        return {
            "cap": self.cap.value,
            "description": self.description,
            "is_enabled": self.is_enabled,
            "version": self.version,
            "theorem_refs": list(self.theorem_refs),
            "stage_file": self.stage_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> RelationalRefinementCapability:
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        RelationalRefinementCapability
            Reconstructed capability object.

        Raises
        ------
        KeyError
            If a required key is missing.
        ValueError
            If a field value is invalid.
        """
        return cls(
            cap=RelationalRefinementCap(str(data["cap"])),
            description=str(data.get("description", "")),
            is_enabled=bool(data.get("is_enabled", True)),
            version=str(data.get("version", "1.0.0")),
            theorem_refs=tuple(str(x) for x in data.get("theorem_refs", [])),  # type: ignore[union-attr]
            stage_file=str(data.get("stage_file", "")),
        )


# ---------------------------------------------------------------------------
# Stage info dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationalRefinementStageInfo:
    """Metadata about a single implementation stage within the package.

    Attributes
    ----------
    stage_id:
        Identifier of the form ``s01``, ``s02``, … or ``algorithms``.
    module_name:
        Importable module name (e.g. ``refinement_checking``).
    primary_class:
        Name of the primary class exposed by this stage.
    capabilities:
        Capabilities provided by this stage.
    description:
        One-line description.
    line_count_target:
        Approximate target line count for the stage module.
    """

    stage_id: str
    module_name: str
    primary_class: str
    capabilities: tuple[RelationalRefinementCap, ...]
    description: str
    line_count_target: int = 500

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to JSON-compatible dict."""
        return {
            "stage_id": self.stage_id,
            "module_name": self.module_name,
            "primary_class": self.primary_class,
            "capabilities": [c.value for c in self.capabilities],
            "description": self.description,
            "line_count_target": self.line_count_target,
        }


# ---------------------------------------------------------------------------
# Manifest dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationalRefinementManifest:
    """Full manifest for the relational_refinement package.

    This singleton captures everything the package offers: its provenance,
    capabilities, stage layout, and theorem targets.  It is the single source
    of truth for tooling that introspects the package.

    Attributes
    ----------
    stage:
        Pipeline stage identifier.
    sequence:
        Ordinal position in the JuGeo pipeline (12 for Ch12).
    title:
        Human-readable title.
    version:
        Semantic version string.
    theory_file:
        Source LaTeX file.
    chapter:
        Chapter number in the theory file.
    capabilities:
        All capability descriptors for this package.
    stages:
        Per-module stage info objects.
    theorem_targets:
        Theorem names, statements, and theory references.
    created_at:
        ISO-8601 creation timestamp.
    description:
        Multi-sentence description of the package.
    """

    stage: str
    sequence: int
    title: str
    version: str
    theory_file: str
    chapter: int
    capabilities: tuple[RelationalRefinementCapability, ...]
    stages: tuple[RelationalRefinementStageInfo, ...]
    theorem_targets: tuple[tuple[str, str, str], ...]
    created_at: str
    description: str

    # ------------------------------------------------------------------
    def get_capability(self, cap: RelationalRefinementCap) -> RelationalRefinementCapability | None:
        """Look up a capability by flag.

        Parameters
        ----------
        cap:
            The capability flag to look up.

        Returns
        -------
        RelationalRefinementCapability | None
            The matching capability, or ``None`` if not present.
        """
        for c in self.capabilities:
            if c.cap == cap:
                return c
        return None

    def is_enabled(self, cap: RelationalRefinementCap) -> bool:
        """Return whether a capability flag is currently enabled.

        Parameters
        ----------
        cap:
            The capability to query.

        Returns
        -------
        bool
            ``True`` if the capability exists and is enabled.
        """
        found = self.get_capability(cap)
        return found is not None and found.is_enabled

    def enabled_capabilities(self) -> tuple[RelationalRefinementCapability, ...]:
        """Return only the enabled capabilities.

        Returns
        -------
        tuple[RelationalRefinementCapability, ...]
            All capabilities where ``is_enabled`` is ``True``.
        """
        return tuple(c for c in self.capabilities if c.is_enabled)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise the manifest to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields serialised to JSON scalars, lists, and dicts.
        """
        return {
            "stage": self.stage,
            "sequence": self.sequence,
            "title": self.title,
            "version": self.version,
            "theory_file": self.theory_file,
            "chapter": self.chapter,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "stages": [s.to_dict() for s in self.stages],
            "theorem_targets": [
                {"name": t[0], "statement": t[1], "ref": t[2]}
                for t in self.theorem_targets
            ],
            "created_at": self.created_at,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Build the singleton
# ---------------------------------------------------------------------------

def _build_capabilities() -> tuple[RelationalRefinementCapability, ...]:
    """Construct the full capability tuple for the manifest singleton.

    Returns
    -------
    tuple[RelationalRefinementCapability, ...]
        One ``RelationalRefinementCapability`` per ``RelationalRefinementCap`` member.
    """
    return (
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.REFINEMENT_CHECK,
            description="Check whether J ≤ J' holds (trust, evidence, obligations, proposition).",
            theorem_refs=("refinement_reflexivity", "refinement_transitivity", "trust_monotonicity"),
            stage_file="refinement_checking",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.EQUIVALENCE_VERIFY,
            description="Verify bidirectional refinement and partition judgments into equivalence classes.",
            theorem_refs=("refinement_antisymmetry", "equivalence_congruence"),
            stage_file="equivalence_verification",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.WITNESS_CONSTRUCT,
            description="Construct, validate, and compose refinement witnesses w: J → J'.",
            theorem_refs=("witness_compositionality", "evidence_embedding_soundness"),
            stage_file="witness_construction",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.COMPARISON_ALGEBRA,
            description="Algebraic operations: compose, invert, tensor, diagonal on relations.",
            theorem_refs=("refinement_transitivity", "equivalence_congruence"),
            stage_file="comparison_algebra",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.BATCH_PROCESSING,
            description="Check refinement for a whole sequence of judgments, returning a RefinementOrder.",
            theorem_refs=("refinement_transitivity",),
            stage_file="refinement_checking",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.SECTION_REFINEMENT,
            description="Refinement of sections over a cover 𝔘 with descent-condition preservation.",
            theorem_refs=("section_refinement_preservation",),
            stage_file="refinement_checking",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.TRANSITIVE_CLOSURE,
            description="Compute the transitive closure of a set of refinement relations.",
            theorem_refs=("refinement_transitivity",),
            stage_file="algorithms",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.ORDER_ANALYSIS,
            description="LUB/GLB, chains, antichains, cycle detection on the refinement order.",
            theorem_refs=("lub_existence", "glb_existence"),
            stage_file="algorithms",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.REGRESSION_DETECTION,
            description="Detect regressive refinements where trust decreases or obligations grow.",
            theorem_refs=("regression_detection",),
            stage_file="algorithms",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.INTEGRATION,
            description="Bridge between RefinementOrder/RefinementWitness and JudgmentAlgebra.",
            theorem_refs=("evidence_embedding_soundness", "obligation_discharge_completeness"),
            stage_file="integration",
        ),
        RelationalRefinementCapability(
            cap=RelationalRefinementCap.THEOREM_OBLIGATIONS,
            description="Generate and track Ch12 proof obligations for a RefinementOrder.",
            theorem_refs=tuple(t[0] for t in THEOREM_TARGETS),
            stage_file="theorems",
        ),
    )


def _build_stages() -> tuple[RelationalRefinementStageInfo, ...]:
    """Construct the per-module stage info tuple.

    Returns
    -------
    tuple[RelationalRefinementStageInfo, ...]
        One entry per implementation module in the package.
    """
    return (
        RelationalRefinementStageInfo(
            stage_id="models",
            module_name="models",
            primary_class="RefinementRelation",
            capabilities=(RelationalRefinementCap.REFINEMENT_CHECK,),
            description="Core frozen dataclasses for the refinement package.",
            line_count_target=600,
        ),
        RelationalRefinementStageInfo(
            stage_id="s01",
            module_name="refinement_checking",
            primary_class="RefinementChecker",
            capabilities=(
                RelationalRefinementCap.REFINEMENT_CHECK,
                RelationalRefinementCap.BATCH_PROCESSING,
                RelationalRefinementCap.SECTION_REFINEMENT,
            ),
            description="RefinementChecker: checks J ≤ J' across all four dimensions.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="s02",
            module_name="equivalence_verification",
            primary_class="EquivalenceVerifier",
            capabilities=(RelationalRefinementCap.EQUIVALENCE_VERIFY,),
            description="EquivalenceVerifier: verifies J ≡ J' and computes equivalence classes.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="s03",
            module_name="witness_construction",
            primary_class="WitnessConstructor",
            capabilities=(RelationalRefinementCap.WITNESS_CONSTRUCT,),
            description="WitnessConstructor: builds and validates RefinementWitness objects.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="s04",
            module_name="comparison_algebra",
            primary_class="ComparisonAlgebra",
            capabilities=(RelationalRefinementCap.COMPARISON_ALGEBRA,),
            description="ComparisonAlgebra: compose, invert, tensor, diagonal on relations.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="algorithms",
            module_name="algorithms",
            primary_class="",
            capabilities=(
                RelationalRefinementCap.TRANSITIVE_CLOSURE,
                RelationalRefinementCap.ORDER_ANALYSIS,
                RelationalRefinementCap.REGRESSION_DETECTION,
            ),
            description="Stand-alone graph/order algorithms for refinement structures.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="integration",
            module_name="integration",
            primary_class="RelationalRefinementIntegration",
            capabilities=(RelationalRefinementCap.INTEGRATION,),
            description="Bridge between refinement structures and JudgmentAlgebra.",
            line_count_target=500,
        ),
        RelationalRefinementStageInfo(
            stage_id="theorems",
            module_name="theorems",
            primary_class="TheoremObligation",
            capabilities=(RelationalRefinementCap.THEOREM_OBLIGATIONS,),
            description="Ch12 theorem obligations and proof strategy registry.",
            line_count_target=500,
        ),
    )


RELATIONAL_REFINEMENT_MANIFEST: RelationalRefinementManifest = RelationalRefinementManifest(
    stage="ch12-relational-refinement",
    sequence=12,
    title="Equivalence and Refinement",
    version="1.0.0",
    theory_file="theory2.tex",
    chapter=12,
    capabilities=_build_capabilities(),
    stages=_build_stages(),
    theorem_targets=THEOREM_TARGETS,
    created_at="2024-01-01T00:00:00Z",
    description=(
        "The relational_refinement package implements Chapter 12 of theory2.tex.  "
        "It provides a refinement partial order on judgments (J ≤ J'), equivalence "
        "classes (the largest congruence on the judgment algebra), refinement witnesses "
        "(morphisms that certify J ≤ J'), a comparison algebra over refinement relations, "
        "and integration utilities that bridge these structures with the core JudgmentAlgebra."
    ),
)

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def get_manifest() -> RelationalRefinementManifest:
    """Return the package manifest singleton.

    Returns
    -------
    RelationalRefinementManifest
        The singleton ``RELATIONAL_REFINEMENT_MANIFEST``.
    """
    return RELATIONAL_REFINEMENT_MANIFEST


def validate_manifest() -> bool:
    """Validate the internal consistency of the manifest singleton.

    Checks that:
    * All capabilities have non-empty descriptions.
    * Every theorem reference in a capability matches a ``THEOREM_TARGETS`` entry.
    * The stage sequence is positive.
    * All stage module names are non-empty.

    Returns
    -------
    bool
        ``True`` if the manifest is internally consistent.

    Raises
    ------
    ValueError
        If the manifest fails any consistency check.
    """
    m = RELATIONAL_REFINEMENT_MANIFEST
    known_theorems = {t[0] for t in m.theorem_targets}

    if m.sequence <= 0:
        raise ValueError(f"Manifest sequence must be positive, got {m.sequence}.")

    for cap in m.capabilities:
        if not cap.description:
            raise ValueError(f"Capability {cap.cap!r} has no description.")
        for ref in cap.theorem_refs:
            if ref not in known_theorems:
                raise ValueError(
                    f"Capability {cap.cap!r} references unknown theorem {ref!r}."
                )

    for stage in m.stages:
        if not stage.module_name:
            raise ValueError(f"Stage {stage.stage_id!r} has an empty module_name.")

    return True


def manifest_to_dict() -> dict[str, JsonValue]:
    """Serialise the manifest singleton to a JSON-compatible dictionary.

    Returns
    -------
    dict[str, JsonValue]
        The manifest as a plain-Python nested dictionary suitable for
        ``json.dumps``.
    """
    return RELATIONAL_REFINEMENT_MANIFEST.to_dict()


def list_theorem_names() -> tuple[str, ...]:
    """Return the names of all Ch12 theorem targets.

    Returns
    -------
    tuple[str, ...]
        One entry per theorem in ``THEOREM_TARGETS``.
    """
    return tuple(t[0] for t in THEOREM_TARGETS)


def capabilities_for_stage(stage_id: str) -> tuple[RelationalRefinementCapability, ...]:
    """Return all capabilities implemented by a given stage module.

    Parameters
    ----------
    stage_id:
        A stage identifier such as ``"s01"`` or ``"algorithms"``.

    Returns
    -------
    tuple[RelationalRefinementCapability, ...]
        All capabilities whose ``stage_file`` matches *stage_id*.
    """
    return tuple(
        c
        for c in RELATIONAL_REFINEMENT_MANIFEST.capabilities
        if c.stage_file == stage_id
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "MANIFEST_SPEC_PROVENANCE",
    "THEOREM_TARGETS",
    # Enums
    "RelationalRefinementCap",
    # Dataclasses
    "RelationalRefinementCapability",
    "RelationalRefinementStageInfo",
    "RelationalRefinementManifest",
    # Singleton
    "RELATIONAL_REFINEMENT_MANIFEST",
    # Functions
    "get_manifest",
    "validate_manifest",
    "manifest_to_dict",
    "list_theorem_names",
    "capabilities_for_stage",
]

# copilot: manifest for Ch12 relational_refinement — provenance, capabilities, theorems
